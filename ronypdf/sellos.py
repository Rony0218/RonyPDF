"""«Mis sellos»: los sellos que cada persona diseña y reutiliza (texto, su nombre y cargo, fecha y hora
automáticas, color, forma y, si quiere, la imagen de su firma o de su propio logo).

Nada institucional: los cuatro diseños de «Para empezar» son genéricos y sirven tal cual o como base.
Los sellos viven solo en este equipo, en la carpeta «RonyPDF_sellos» junto al archivo de ajustes (es decir,
junto al programa portable), un .json por sello con la imagen en base64.

Sin diálogos: aquí se dibuja el sello (``dibujar`` devuelve un PNG transparente por fuera del contorno y,
con «Fondo blanco», blanco por dentro para que se lea sobre el texto de la página) y se guardan, listan,
copian, exportan e importan los diseños.
"""

from __future__ import annotations

import base64
import copy
import datetime as _dt
import json
import math
import re
import secrets
from dataclasses import asdict, dataclass, field
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen, QTransform

from . import core
from .core import UserError

CARPETA = "RonyPDF_sellos"
EXTENSION = ".ronysello"
MAX_SELLOS = 30
MAX_LADO_IMAGEN = 1600
MAX_LINEAS = 6
MAX_LARGO_LINEA = 90
AVISO_PRIVACIDAD = ("Tus sellos se guardan solo en este equipo, en la carpeta RonyPDF_sellos junto al "
                    "programa. Puedes borrarlos cuando quieras.")

FORMAS = {"rectangulo": "Rectángulo", "redondeado": "Redondeado", "ovalo": "Óvalo", "circulo": "Círculo",
          "sin_borde": "Sin borde"}
BORDES = {"simple": "Simple", "doble": "Doble"}
LETRAS = {"sans": "Sin remates", "serif": "Con remates", "mono": "Máquina de escribir"}
POSICIONES_IMAGEN = {"izquierda": "A la izquierda", "arriba": "Arriba", "fondo": "De fondo"}
FAMILIAS = {"sans": ("Arial", "Segoe UI"), "serif": ("Times New Roman", "Georgia"),
            "mono": ("Courier New", "Consolas")}
COLORES = (("Azul", "#1E4FA0"), ("Verde", "#1F7A4D"), ("Rojo", "#B42323"), ("Morado", "#6D28D9"),
           ("Gris", "#374151"), ("Naranja", "#C2410C"))
CAMPOS = {"nombre": "Mi nombre", "cargo": "Mi cargo", "fecha": "Fecha", "hora": "Hora",
          "fecha_hora": "Fecha y hora"}
_RE_CAMPO = re.compile(r"\{(nombre|cargo|fecha_hora|fecha|hora)\}")
_RE_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


# ---------------------------------------------------------------- el diseño

@dataclass
class Sello:
    id: str
    nombre: str
    lineas: list[str]                       # la primera es el texto principal
    forma: str = "redondeado"
    color: str = "#1E4FA0"
    borde: str = "simple"
    relleno: bool = False                   # fondo suave del mismo color
    fondo_blanco: bool = True               # blanco casi opaco dentro del borde: se lee encima del texto
    letra: str = "sans"
    negrita: bool = True
    inclinacion: int = 0                    # grados, de −15 a 15
    imagen: bytes | None = field(default=None, repr=False)     # PNG de su firma o de su logo
    posicion_imagen: str = "izquierda"
    ancho_pt: float = 170.0
    creado: str = ""

    def copia(self, **cambios) -> "Sello":
        s = copy.deepcopy(self)
        for k, v in cambios.items():
            setattr(s, k, v)
        return s


def nuevo_id() -> str:
    return "s-" + secrets.token_hex(6)


def _hora(f: _dt.datetime) -> str:
    h = f.hour % 12 or 12
    return f"{h}:{f.minute:02d} {'a. m.' if f.hour < 12 else 'p. m.'}"


def valores(datos: dict | None) -> dict[str, str]:
    """Textos de los campos automáticos: fecha 12/03/2025, hora 3:42 p. m."""
    datos = datos or {}
    f = datos.get("fecha") or _dt.datetime.now()
    if isinstance(f, _dt.date) and not isinstance(f, _dt.datetime):
        f = _dt.datetime(f.year, f.month, f.day)
    fecha = f"{f.day:02d}/{f.month:02d}/{f.year}"
    return {"nombre": (datos.get("nombre") or "").strip(), "cargo": (datos.get("cargo") or "").strip(),
            "fecha": fecha, "hora": _hora(f), "fecha_hora": f"{fecha} {_hora(f)}"}


def resolver(texto: str, datos: dict | None) -> str:
    """Cambia {nombre}, {cargo}, {fecha}, {hora} y {fecha_hora} por sus valores de este momento."""
    texto = texto or ""
    if not _RE_CAMPO.search(texto):
        return texto.strip()
    v = valores(datos)
    out = _RE_CAMPO.sub(lambda m: v[m.group(1)], texto)
    # si un campo quedó vacío (sin cargo, por ejemplo) no sobran separadores
    out = re.sub(r"(\s*[·|,–—-]\s*){2,}", " · ", out)
    return re.sub(r"\s{2,}", " ", out).strip(" ·-–—|,")


def lineas_resueltas(sello: Sello, datos: dict | None) -> list[str]:
    out = []
    for k, ln in enumerate(sello.lineas[:MAX_LINEAS]):
        t = resolver(ln, datos)
        if t or k == 0:
            out.append(t)
    return out


def texto_plano(sello: Sello, datos: dict | None) -> str:
    """El texto del sello en una línea: va en el contenido de la anotación (se puede buscar y sale en el
    resumen de comentarios)."""
    return " · ".join(t for t in lineas_resueltas(sello, datos) if t) or sello.nombre


# ---------------------------------------------------------------- dibujo

def _png(img: QImage) -> bytes:
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


def _fuente(sello: Sello, px: float, principal: bool) -> QFont:
    f = QFont(FAMILIAS.get(sello.letra, FAMILIAS["sans"])[0])
    f.setPixelSize(max(1, int(round(px))))
    f.setBold(sello.negrita if principal else False)
    if principal:
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 104)
    return f


def _ajustar(sello: Sello, texto: str, ancho: float, px: float, minimo: float, principal: bool) -> QFont:
    """La letra más grande (hasta ``px``) con la que ``texto`` cabe en ``ancho``."""
    while px > minimo:
        f = _fuente(sello, px, principal)
        if QFontMetricsF(f).horizontalAdvance(texto) <= ancho:
            return f
        px *= 0.93
    return _fuente(sello, minimo, principal)


def _contorno(forma: str, r: QRectF) -> QPainterPath:
    path = QPainterPath()
    if forma in ("ovalo", "circulo"):
        path.addEllipse(r)
    elif forma == "redondeado":
        rad = min(r.width(), r.height()) * 0.18
        path.addRoundedRect(r, rad, rad)
    else:
        path.addRect(r)
    return path


FONDO_BLANCO_ALFA = 230                     # 90 %: tapa los renglones de la página sin parecer un parche


def dibujar(sello: Sello, datos: dict | None = None, escala: float = 4.0) -> bytes:
    """PNG del sello, transparente por fuera del contorno (y por dentro, si no lleva «Fondo blanco»).
    ``escala`` = píxeles por punto PDF (4 ≈ 288 ppp): el ancho de la imagen dividido por la escala es el
    ancho del sello en la página."""
    e = float(escala)
    lineas = lineas_resueltas(sello, datos)
    color = QColor(sello.color if _RE_COLOR.match(sello.color or "") else "#1E4FA0")
    redondo = sello.forma in ("ovalo", "circulo")
    W = max(60.0, min(600.0, float(sello.ancho_pt))) * e
    grosor = 2.0 * e if sello.forma != "sin_borde" else 0.0
    if sello.forma == "circulo":
        W *= 0.82
    pad_x = (W * (0.16 if sello.forma == "circulo" else 0.15) if redondo else 9 * e) + grosor
    pad_y = (7 * e if not redondo else 10 * e) + grosor
    img_src = QImage.fromData(sello.imagen) if sello.imagen else QImage()
    tiene_img = not img_src.isNull()

    # Texto: el principal grande; los demás renglones, pequeños.
    ancho_texto = W - 2 * pad_x
    lado_img = 0.0
    if tiene_img and sello.posicion_imagen == "izquierda":
        lado_img = min(W * 0.32, 60 * e)
        ancho_texto -= lado_img + 5 * e
    fuentes = []
    for k, t in enumerate(lineas):
        if k == 0:
            fuentes.append(_ajustar(sello, t or " ", ancho_texto, 21 * e, 7 * e, True))
        else:
            fuentes.append(_ajustar(sello, t or " ", ancho_texto, 9.5 * e, 5 * e, False))
    alturas = [QFontMetricsF(f).height() * (0.98 if k == 0 else 1.0) for k, f in enumerate(fuentes)]
    alto_texto = sum(alturas) + (2.2 * e if len(lineas) > 1 else 0)
    alto_img_arriba = 0.0
    if tiene_img and sello.posicion_imagen == "arriba":
        alto_img_arriba = min(42 * e, ancho_texto * img_src.height() / max(1, img_src.width()))
        alto_texto += alto_img_arriba + 3 * e
    if tiene_img and sello.posicion_imagen == "izquierda":
        alto_texto = max(alto_texto, min(lado_img * img_src.height() / max(1, img_src.width()), 60 * e))
    H = alto_texto + 2 * pad_y
    if sello.forma == "circulo":
        H = W = max(W, H * 1.2)
    elif sello.forma == "ovalo":
        H = max(H * 1.18, W * 0.42)
    ancho_contenido = ancho_texto + (lado_img + 5 * e if lado_img else 0.0)

    margen = grosor + 2 * e
    img = QImage(int(math.ceil(W + 2 * margen)), int(math.ceil(H + 2 * margen)),
                 QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    caja = QRectF(margen, margen, W, H)
    if sello.fondo_blanco:
        p.fillPath(_contorno(sello.forma, caja), QColor(255, 255, 255, FONDO_BLANCO_ALFA))
    if sello.relleno:
        fondo = QColor(color)
        fondo.setAlpha(28)
        p.fillPath(_contorno(sello.forma, caja), fondo)
    if tiene_img and sello.posicion_imagen == "fondo":
        p.save()
        p.setClipPath(_contorno(sello.forma, caja))
        p.setOpacity(0.22)
        area = caja.adjusted(grosor * 2, grosor * 2, -grosor * 2, -grosor * 2)
        tam = img_src.size().scaled(area.size().toSize(), Qt.AspectRatioMode.KeepAspectRatio)
        p.drawImage(QRectF(area.center().x() - tam.width() / 2, area.center().y() - tam.height() / 2,
                           tam.width(), tam.height()), img_src)
        p.restore()
    if sello.forma != "sin_borde":
        pen = QPen(color, grosor)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(_contorno(sello.forma, caja.adjusted(grosor / 2, grosor / 2, -grosor / 2, -grosor / 2)))
        if sello.borde == "doble":
            p.setPen(QPen(color, max(1.0, grosor * 0.42)))
            d = grosor * 2.1
            p.drawPath(_contorno(sello.forma, caja.adjusted(d, d, -d, -d)))

    # contenido centrado verticalmente
    y = caja.top() + (H - alto_texto) / 2
    x0 = caja.left() + (W - ancho_contenido) / 2
    if tiene_img and sello.posicion_imagen == "izquierda":
        alto_i = min(lado_img * img_src.height() / max(1, img_src.width()), alto_texto)
        ancho_i = alto_i * img_src.width() / max(1, img_src.height())
        p.drawImage(QRectF(x0 + (lado_img - ancho_i) / 2, caja.top() + (H - alto_i) / 2, ancho_i, alto_i), img_src)
        x0 += lado_img + 5 * e
    if tiene_img and sello.posicion_imagen == "arriba":
        ancho_i = alto_img_arriba * img_src.width() / max(1, img_src.height())
        p.drawImage(QRectF(x0 + (ancho_texto - ancho_i) / 2, y, ancho_i, alto_img_arriba), img_src)
        y += alto_img_arriba + 3 * e
    p.setPen(color)
    for k, (t, f, h) in enumerate(zip(lineas, fuentes, alturas)):
        p.setFont(f)
        p.drawText(QRectF(x0, y, ancho_texto, h), int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter), t)
        y += h + (2.2 * e if k == 0 and len(lineas) > 1 else 0)
    p.end()
    ang = max(-15, min(15, int(sello.inclinacion or 0)))
    if ang:
        img = img.transformed(QTransform().rotate(-ang), Qt.TransformationMode.SmoothTransformation)
    return _png(img)


def tamano_pt(png: bytes, escala: float = 4.0) -> tuple[float, float]:
    """Ancho y alto del sello en la página, en puntos."""
    img = QImage.fromData(png)
    return img.width() / escala, img.height() / escala


# ---------------------------------------------------------------- guardar y leer diseños

def _limpiar_imagen(png: bytes | None) -> bytes | None:
    if not png:
        return None
    img = QImage.fromData(png)
    if img.isNull():
        raise UserError("La imagen del sello no se pudo leer.")
    if max(img.width(), img.height()) > MAX_LADO_IMAGEN:
        img = img.scaled(MAX_LADO_IMAGEN, MAX_LADO_IMAGEN, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
        return _png(img)
    return png if png.startswith(b"\x89PNG") else _png(img)


def imagen_desde_archivo(ruta) -> bytes:
    """PNG (de hasta 1600 px de lado) a partir de una imagen: la firma escaneada o el logo propio."""
    img = QImage(str(ruta))
    if img.isNull():
        raise UserError(f"No se pudo leer la imagen «{Path(ruta).name}».")
    return _limpiar_imagen(_png(img))


def a_dict(sello: Sello) -> dict:
    d = asdict(sello)
    d["imagen"] = base64.b64encode(sello.imagen).decode("ascii") if sello.imagen else None
    return {"formato": "RonyPDF-sello", "version": 1, **d}


def desde_dict(d: dict, nuevo: bool = False) -> Sello:
    """Lee un diseño (de la carpeta o de un .ronysello) y corrige lo que venga fuera de rango."""
    if not isinstance(d, dict) or not isinstance(d.get("lineas"), list):
        raise UserError("El archivo no es un sello de RonyPDF.")
    lineas = [str(x)[:MAX_LARGO_LINEA] for x in d.get("lineas") or [] if isinstance(x, (str, int, float))]
    lineas = lineas[:MAX_LINEAS] or ["SELLO"]
    imagen = None
    if d.get("imagen"):
        try:
            imagen = _limpiar_imagen(base64.b64decode(d["imagen"]))
        except (ValueError, TypeError) as exc:
            raise UserError("La imagen del sello está dañada.") from exc
    color = str(d.get("color") or "")
    try:
        inclinacion = max(-15, min(15, int(d.get("inclinacion") or 0)))
        ancho = max(60.0, min(600.0, float(d.get("ancho_pt") or 170.0)))
    except (TypeError, ValueError):
        inclinacion, ancho = 0, 170.0
    sid = str(d.get("id") or "")
    if nuevo or not re.fullmatch(r"[A-Za-z0-9_\-]{3,40}", sid):
        sid = nuevo_id()
    return Sello(
        id=sid, nombre=(str(d.get("nombre") or lineas[0]).strip()[:60] or "Sello"), lineas=lineas,
        forma=d.get("forma") if d.get("forma") in FORMAS else "rectangulo",
        color=color if _RE_COLOR.match(color) else COLORES[0][1],
        borde=d.get("borde") if d.get("borde") in BORDES else "simple",
        relleno=bool(d.get("relleno")), fondo_blanco=bool(d.get("fondo_blanco", True)),
        letra=d.get("letra") if d.get("letra") in LETRAS else "sans",
        negrita=bool(d.get("negrita", True)), inclinacion=inclinacion, imagen=imagen,
        posicion_imagen=d.get("posicion_imagen") if d.get("posicion_imagen") in POSICIONES_IMAGEN else "izquierda",
        ancho_pt=ancho, creado=str(d.get("creado") or ""))


class AlmacenSellos:
    """Los sellos de la persona, en la carpeta «RonyPDF_sellos» junto a los ajustes (máximo 30)."""

    def __init__(self, carpeta=None):
        if carpeta is None:
            from .widgets import ctx
            if ctx.settings is not None:
                base = Path(ctx.settings.path).parent
            else:
                from .settings import Settings
                base = Path(Settings().path).parent
            carpeta = base / CARPETA
        self.carpeta = Path(carpeta)

    def _ruta(self, sid: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_\-]{3,40}", sid or ""):
            raise UserError("Ese sello no existe.")
        return self.carpeta / f"{sid}.json"

    def listar(self) -> list[Sello]:
        if not self.carpeta.is_dir():
            return []
        out = []
        try:
            archivos = sorted(self.carpeta.glob("*.json"))
        except OSError:
            return []
        for f in archivos:
            try:
                s = desde_dict(json.loads(f.read_text(encoding="utf-8")))
            except (OSError, ValueError, UserError):
                continue                       # un archivo dañado no impide ver los demás
            if s.id != f.stem:
                s.id = f.stem
            out.append(s)
        return sorted(out, key=lambda s: (s.creado or "", s.nombre.lower()))

    def obtener(self, sid: str) -> Sello | None:
        try:
            f = self._ruta(sid)
            return desde_dict(json.loads(f.read_text(encoding="utf-8"))) if f.is_file() else None
        except (OSError, ValueError, UserError):
            return None

    def lleno(self) -> bool:
        return len(self.listar()) >= MAX_SELLOS

    def guardar(self, sello: Sello) -> Sello:
        """Guarda (o reemplaza) un diseño.  Uno nuevo no entra si ya hay 30."""
        if not sello.lineas or not (sello.lineas[0] or "").strip():
            raise UserError("Escribe el texto principal del sello.")
        if not (sello.nombre or "").strip():
            sello.nombre = sello.lineas[0].strip()[:60]
        if not sello.id:
            sello.id = nuevo_id()
        destino = self._ruta(sello.id)
        if not destino.exists() and self.lleno():
            raise UserError(f"Ya tienes {MAX_SELLOS} sellos guardados. Borra uno para guardar otro.")
        if not sello.creado:
            sello.creado = _dt.datetime.now().isoformat(timespec="seconds")
        sello.imagen = _limpiar_imagen(sello.imagen)
        datos = json.dumps(a_dict(sello), ensure_ascii=False, indent=1)
        try:
            with core._output(destino) as tmp:
                Path(tmp).write_text(datos, encoding="utf-8")
        except OSError as exc:
            raise UserError(f"No se pudo guardar el sello en «{self.carpeta}».") from exc
        return sello

    def borrar(self, sid: str) -> None:
        f = self._ruta(sid)
        try:
            f.unlink(missing_ok=True)
        except OSError as exc:
            raise UserError(f"No se pudo borrar el sello «{sid}».") from exc

    def _nombre_libre(self, nombre: str) -> str:
        usados = {s.nombre.lower() for s in self.listar()}
        if nombre.lower() not in usados:
            return nombre
        k = 2
        while f"{nombre} ({k})".lower() in usados:
            k += 1
        return f"{nombre} ({k})"

    def duplicar(self, sid: str) -> Sello:
        s = self.obtener(sid)
        if s is None:
            raise UserError("Ese sello ya no existe.")
        copia = s.copia(id=nuevo_id(), nombre=self._nombre_libre(f"Copia de {s.nombre}")[:60], creado="")
        return self.guardar(copia)

    def agregar_copia(self, sello: Sello, nombre: str | None = None) -> Sello:
        """Guarda una copia editable de un diseño (p. ej. uno de «Para empezar»)."""
        copia = sello.copia(id=nuevo_id(), nombre=self._nombre_libre((nombre or sello.nombre)[:60]), creado="")
        return self.guardar(copia)

    def exportar(self, sid: str, ruta) -> Path:
        s = self.obtener(sid)
        if s is None:
            raise UserError("Ese sello ya no existe.")
        ruta = Path(ruta)
        if ruta.suffix.lower() != EXTENSION:
            ruta = ruta.with_name(ruta.name + EXTENSION)
        d = a_dict(s)
        d.pop("creado", None)
        with core._output(ruta) as tmp:
            Path(tmp).write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        return ruta

    def importar(self, ruta) -> Sello:
        ruta = Path(ruta)
        try:
            if ruta.stat().st_size > 12 * 1024 * 1024:
                raise UserError("El archivo es demasiado grande para ser un sello.")
            d = json.loads(ruta.read_text(encoding="utf-8-sig"))
        except UserError:
            raise
        except (OSError, ValueError) as exc:
            raise UserError(f"«{ruta.name}» no es un sello de RonyPDF.") from exc
        s = desde_dict(d, nuevo=True)
        s.nombre = self._nombre_libre(s.nombre)
        s.creado = ""
        return self.guardar(s)


# ---------------------------------------------------------------- para empezar (genéricos)

PARA_EMPEZAR: list[Sello] = [
    Sello(id="inicio-recibido", nombre="Recibido", lineas=["RECIBIDO", "{fecha_hora}", "{nombre}"],
          forma="redondeado", color="#1E4FA0", borde="doble"),
    Sello(id="inicio-aprobado", nombre="Aprobado", lineas=["APROBADO", "{nombre}", "{fecha}"],
          forma="rectangulo", color="#1F7A4D", borde="simple"),
    Sello(id="inicio-revisado", nombre="Revisado", lineas=["REVISADO"], forma="ovalo", color="#6D28D9",
          borde="simple", ancho_pt=150),
    Sello(id="inicio-copia", nombre="Copia", lineas=["COPIA"], forma="rectangulo", color="#B42323", borde="doble",
          inclinacion=6, ancho_pt=130),
]


def de_fabrica(sid: str) -> Sello | None:
    return next((s.copia() for s in PARA_EMPEZAR if s.id == sid), None)
