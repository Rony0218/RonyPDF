"""Renombrar archivos por su contenido (sin interfaz gráfica).

Lee las primeras páginas de cada PDF (con OCR en los escaneados, también si traen encima un sello
digital como «Radicado 2025-00123»), encuentra la fecha, el NIT con su dígito de verificación, la
cédula, el número de factura o el texto que sigue a una palabra clave, y propone un nombre nuevo a
partir de una plantilla con fichas: ``{fecha}_{nit}_{factura}``.

Nunca se pisa un archivo: si dos archivos quedarían con el mismo nombre (o ya existe uno así en la
carpeta) se agrega « (2)», « (3)»…  Cada renombrado deja un registro en la carpeta «RonyPDF_lotes»
(junto a los ajustes) para poder deshacerlo.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import secrets
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from . import censura, core
from .core import Progress, UserError, _report

CARPETA_REGISTROS = "RonyPDF_lotes"
MAX_REGISTROS = 20

# Fichas de la plantilla (en el orden en que se muestran los botones)
FICHAS = ("fecha", "año", "mes", "dia", "nit", "nit_2", "cedula", "factura", "texto", "titulo", "nombre",
          "paginas", "n")
FORMATOS_FECHA = {"AAAA-MM-DD": "%Y-%m-%d", "AAAAMMDD": "%Y%m%d", "DD-MM-AAAA": "%d-%m-%Y"}
PLANTILLA_DEFECTO = "{fecha}_{nit}_{factura}"
# Lo que se escribe en el nombre cuando falta un dato (con «Si falta un dato: marcarlo»)
MARCA_FALTA = {"fecha": "sin-fecha", "nit": "sin-nit", "nit_2": "sin-nit2", "cedula": "sin-cedula",
               "factura": "sin-factura", "texto": "sin-texto", "titulo": "sin-titulo"}
# Nombres para mostrar a la persona
NOMBRES_DATO = {"fecha": "fecha", "nit": "NIT", "nit_2": "segundo NIT", "cedula": "cédula",
                "factura": "factura", "texto": "texto clave", "titulo": "título"}
ESTADOS = ("ok", "falta", "duplicado", "igual", "error", "protegido")


# --------------------------------------------------------------------------
# Lectura del texto
# --------------------------------------------------------------------------

@dataclass
class Lectura:
    """Texto de las primeras páginas de un PDF (digital más OCR) y lo necesario para el título."""
    texto: str
    titulo: str = ""
    paginas: int = 0
    ocr: bool = False               # se reconoció alguna página escaneada
    aviso: str = ""                 # p. ej. faltan los modelos de idioma del OCR


def _renglones_ocr(palabras: Sequence[tuple]) -> list[tuple[str, float]]:
    """Agrupa las palabras del OCR en renglones: (texto, alto típico de la letra)."""
    renglones: dict[tuple[int, int], list[tuple]] = {}
    for w in palabras:
        if len(w) < 7 or not str(w[4]).strip():
            continue
        renglones.setdefault((w[5], w[6]), []).append(w)
    out = []
    for ws in renglones.values():
        ws.sort(key=lambda w: w[7] if len(w) > 7 else w[0])
        altos = sorted(w[3] - w[1] for w in ws)
        out.append((" ".join(str(w[4]) for w in ws), altos[len(altos) // 2]))
    return out


def _renglones_digitales(page) -> list[tuple[str, float]]:
    """Renglones visibles de la página con el tamaño de su letra más grande."""
    out = []
    try:
        bloques = page.get_text("dict")["blocks"]
    except Exception:  # noqa: BLE001
        return out
    for b in bloques:
        if b.get("type") != 0:
            continue
        for ln in b.get("lines", ()):
            spans = [s for s in ln.get("spans", ()) if s.get("text", "").strip() and not core.span_is_invisible(s)]
            if spans:
                out.append(("".join(s["text"] for s in ln["spans"]).strip(), max(s.get("size", 0) for s in spans)))
    return out


def _renglon_mayor(renglones: list[tuple[str, float]]) -> str:
    mejor, tam = "", 0.0
    for texto, alto in renglones:
        texto = " ".join(texto.split())
        if sum(c.isalpha() for c in texto) < 3:
            continue
        if alto > tam + 0.5:
            mejor, tam = texto, alto
    return mejor[:60].strip()


_TITULO_GENERICO = re.compile(
    r"^(?:sin\s+t[ií]tulo|untitled|documento?\s*\d*|document\s*\d*|libro\s*\d*|book\s*\d*|hoja\s*\d*|"
    r"presentaci[oó]n(?:\s+de\s+powerpoint)?\s*\d*|microsoft\s+(?:word|excel|powerpoint)\b.*|"
    r".*\.(?:docx?|xlsx?|pptx?|pdf|txt|rtf|odt))$", re.IGNORECASE)


def titulo_util(titulo: str) -> str:
    """El título de los metadatos, si dice algo (no «Documento1» ni «Microsoft Word - carta.docx»)."""
    t = " ".join((titulo or "").split())
    if len(t) < 3 or _TITULO_GENERICO.match(t) or not any(c.isalpha() for c in t):
        return ""
    return t[:60].strip()


def leer(path, password: str | None = None, *, paginas: int = 2, usar_ocr: bool = True,
         idioma: str = "spa+eng") -> Lectura:
    """Lee el texto de las primeras ``paginas`` páginas.  Nunca escribe el PDF."""
    tess: str | None = None
    aviso = ""
    partes: list[str] = []
    usado_ocr = False
    with core.open_pdf(path, password) as doc:
        n = doc.page_count
        titulo = titulo_util((doc.metadata or {}).get("title", ""))
        renglones_p1: list[tuple[str, float]] = []
        for i in range(min(max(1, int(paginas)), n)):
            page = doc[i]
            texto = page.get_text("text", sort=True)
            digitales = _renglones_digitales(page) if i == 0 else []
            ocr: list[tuple[str, float]] = []
            if usar_ocr and core.page_needs_ocr(page):
                if tess is None and not aviso:
                    try:
                        tess = core._check_ocr(idioma)
                    except UserError as exc:
                        aviso = str(exc)
                if tess:
                    ocr = _renglones_ocr(core._ocr_words(page, idioma, 200, tess))
                    usado_ocr = True
                    # El texto digital que ya hubiera (un sello «Radicado…») también cuenta.
                    texto = texto + "\n" + "\n".join(t for t, _ in ocr)
            partes.append(texto)
            if i == 0:
                renglones_p1 = ocr or digitales
        if not titulo:
            titulo = _renglon_mayor(renglones_p1)
    return Lectura("\n".join(partes), titulo, n, usado_ocr, aviso)


# --------------------------------------------------------------------------
# Análisis del texto
# --------------------------------------------------------------------------

_GUIONES = str.maketrans({c: "-" for c in "‐‑‒–—−"})

MESES = {
    "enero": 1, "ene": 1, "january": 1, "jan": 1, "febrero": 2, "feb": 2, "february": 2,
    "marzo": 3, "mar": 3, "march": 3, "abril": 4, "abr": 4, "april": 4, "apr": 4, "mayo": 5, "may": 5,
    "junio": 6, "jun": 6, "june": 6, "julio": 7, "jul": 7, "july": 7, "agosto": 8, "ago": 8, "august": 8,
    "aug": 8, "septiembre": 9, "setiembre": 9, "september": 9, "sept": 9, "sep": 9, "set": 9,
    "octubre": 10, "oct": 10, "october": 10, "noviembre": 11, "nov": 11, "november": 11,
    "diciembre": 12, "dic": 12, "december": 12, "dec": 12,
}
_MES = r"(?P<mes>" + "|".join(sorted(MESES, key=len, reverse=True)) + r")(?![a-z])\.?"
_FECHAS = [
    ("dma", re.compile(r"(?<![\d/.\-])(?P<d>\d{1,2})\s?(?P<s>[/.\-])\s?(?P<m>\d{1,2})\s?(?P=s)\s?(?P<y>\d{4})(?!\d)")),
    ("amd", re.compile(r"(?<![\d/.\-])(?P<y>(?:19|20)\d{2})\s?(?P<s>[/.\-])\s?(?P<m>\d{1,2})\s?(?P=s)\s?"
                       r"(?P<d>\d{1,2})(?![\d])")),
    ("d_mes", re.compile(r"(?<!\d)(?P<d>\d{1,2})(?:\s*(?:de\s+)?|\s*[/.\-]\s*)" + _MES
                         + r"(?:\s*(?:de|del)\s+|\s*[/.,\-]\s*|\s+)(?P<y>\d{4})(?!\d)")),
    ("mes_d", re.compile(r"(?<![a-z])" + _MES + r"\s*(?P<d>\d{1,2})(?:\s*(?:de|del)\s+|\s*,\s*|\s+)(?P<y>\d{4})(?!\d)")),
]
_ETIQUETA_BUENA = re.compile(r"expedic|emision|emitid|elaborac|factura|generac|radicac|documento|elaborad")
_ETIQUETA_MALA = re.compile(r"vencim|vence|limite|pago|corte|nacim|inicio|hasta|desde|entrega|validez")


def _plegar(texto: str) -> str:
    return censura.plegar(texto.translate(_GUIONES))


def _fecha_valida(y: int, m: int, d: int) -> _dt.date | None:
    if not 1990 <= y <= _dt.date.today().year + 1:
        return None
    try:
        return _dt.date(y, m, d)
    except ValueError:
        return None


def _puntaje_fecha(pleg: str, inicio: int) -> float:
    """Qué tan probable es que la fecha que empieza en ``inicio`` sea la del documento."""
    contexto = pleg[max(0, inicio - 60):inicio]
    if contexto.count("\n") > 1:                     # solo este renglón y el anterior
        contexto = contexto[contexto[:contexto.rfind("\n")].rfind("\n") + 1:]
    k = contexto.rfind("fecha")
    if k < 0:
        if _ETIQUETA_BUENA.search(contexto[-25:]) and not _ETIQUETA_MALA.search(contexto[-25:]):
            return 2.5
        return 1.0
    etiqueta = contexto[k:]
    if _ETIQUETA_MALA.search(etiqueta):
        return 0.5
    if _ETIQUETA_BUENA.search(etiqueta):
        return 3.0
    return 2.0


def buscar_fecha(texto: str) -> _dt.date | None:
    """La fecha del documento: la que sigue a «Fecha», «Fecha de expedición»… o, si no, la primera
    (nunca una fecha de vencimiento, de pago o de nacimiento)."""
    pleg = _plegar(texto)
    candidatas: list[tuple[float, int, _dt.date]] = []
    for tipo, rx in _FECHAS:
        for m in rx.finditer(pleg):
            y, d = int(m.group("y")), int(m.group("d"))
            mes = MESES[m.group("mes")] if "mes" in rx.groupindex else int(m.group("m"))
            f = _fecha_valida(y, mes, d)
            if f is None and tipo == "dma":            # 03/25/2025: formato de Estados Unidos
                f = _fecha_valida(y, d, mes) if d <= 12 < mes else None
            if f is not None:
                candidatas.append((_puntaje_fecha(pleg, m.start()), m.start(), f))
    # Una fecha de vencimiento, de pago o de nacimiento no es la del documento.
    candidatas = [c for c in candidatas if c[0] > 0.5]
    if not candidatas:
        return None
    candidatas.sort(key=lambda c: (-c[0], c[1]))
    return candidatas[0][2]


def buscar_nits(texto: str) -> list[str]:
    """NIT con dígito de verificación correcto, normalizados como 900123456-7 (sin repetir)."""
    out: list[str] = []
    for dato in censura.coincidencias("nit", texto.translate(_GUIONES)):
        if "-" not in dato or not censura.validar_nit(dato):
            continue
        num, dv = dato.rsplit("-", 1)
        nit = re.sub(r"\D", "", num) + "-" + re.sub(r"\D", "", dv)
        if nit not in out:
            out.append(nit)
    return out


def buscar_cedula(texto: str, nits: Sequence[str] = ()) -> str:
    bases = {n.split("-")[0] for n in nits}
    for dato in censura.coincidencias("cedula", texto.translate(_GUIONES)):
        d = re.sub(r"\D", "", dato)
        if 6 <= len(d) <= 10 and d not in bases:
            return d
    return ""


_CON = r"(?:n\.?\s?[°º]|no\b\.?|nro\b\.?|num(?:ero)?\b\.?|#)"
_NUM = r"(?P<num>(?:[a-z]{1,5}\s?-?\s?)?\d(?:[\d\-]*\d)?)(?![\w])"
_FACTURA = [
    re.compile(r"(?<![a-z])(?:factura(?:\s+electronica)?(?:\s+de\s+venta)?|cuenta\s+de\s+cobro|fact\.)"
               r"(?:\s*" + _CON + r")?\s*[:.]?\s*" + _NUM),
    re.compile(r"(?<![a-z])" + _CON + r"\s*(?:de\s+)?(?:la\s+)?(?:factura|cuenta\s+de\s+cobro)\s*[:.]?\s*" + _NUM),
]


def buscar_factura(texto: str) -> str:
    """Número de factura o de cuenta de cobro: FE-1045, FV 00123, 1045…"""
    original = texto.translate(_GUIONES)
    pleg = censura.plegar(original)
    mejor: tuple[int, str] | None = None
    for rx in _FACTURA:
        for m in rx.finditer(pleg):
            num = original[m.start("num"):m.end("num")]
            if mejor is None or m.start() < mejor[0]:
                mejor = (m.start(), num)
            break
    if mejor is None:
        return ""
    num = re.sub(r"\s*-\s*", "-", " ".join(mejor[1].split())).strip("- ").upper()
    return num[:24]


_CONECTOR_INICIO = re.compile(r"^(?:\s|[:.#°º\-]|n[°º]\.?|no\b\.?|nro\b\.?)+", re.IGNORECASE)


def texto_tras(texto: str, palabra: str, maximo: int = 40) -> str:
    """Lo que sigue a ``palabra`` hasta el fin del renglón (o el renglón siguiente, si está vacío)."""
    palabra = (palabra or "").strip()
    if not palabra:
        return ""
    clave = censura.plegar(" ".join(palabra.split()))
    pleg = censura.plegar(texto)
    patron = r"(?<!\w)" + r"\s+".join(re.escape(p) for p in clave.split()) + r"(?!\w)"
    m = re.search(patron, pleg)
    if not m:
        return ""
    resto = texto[m.end():]
    renglones = resto.split("\n")
    valor = _CONECTOR_INICIO.sub("", renglones[0]).strip()
    if not valor:
        valor = next((_CONECTOR_INICIO.sub("", r).strip() for r in renglones[1:3] if r.strip()), "")
    return " ".join(valor.split())[:maximo].strip()


def analizar(texto: str, palabra_clave: str = "") -> dict[str, str]:
    """Los datos que se encuentran en el texto: fecha (AAAA-MM-DD), nit, nit_2, cedula, factura, texto."""
    f = buscar_fecha(texto)
    nits = buscar_nits(texto)
    return {
        "fecha": f.isoformat() if f else "",
        "nit": nits[0] if nits else "",
        "nit_2": nits[1] if len(nits) > 1 else "",
        "cedula": buscar_cedula(texto, nits),
        "factura": buscar_factura(texto),
        "texto": texto_tras(texto, palabra_clave),
    }


def datos_de(lectura: Lectura, path, palabra_clave: str = "") -> dict[str, str]:
    """Todos los campos de un archivo a partir de su lectura."""
    d = analizar(lectura.texto, palabra_clave)
    d["titulo"] = lectura.titulo
    d["nombre"] = Path(path).stem
    d["paginas"] = str(lectura.paginas)
    return d


def extraer(path, password: str | None = None, *, paginas: int = 2, usar_ocr: bool = True,
            idioma: str = "spa+eng", palabra_clave: str = "") -> dict[str, str]:
    """Lee el PDF y devuelve sus datos: fecha, nit, nit_2, cedula, factura, texto, titulo, nombre, paginas."""
    lectura = leer(path, password, paginas=paginas, usar_ocr=usar_ocr, idioma=idioma)
    return datos_de(lectura, path, palabra_clave)


def leer_lote(rutas: Sequence[str], contrasenas: dict[str, str] | None = None, *, paginas: int = 2,
              usar_ocr: bool = True, idioma: str = "spa+eng", progress: Progress = None) -> dict:
    """Lee varios archivos.  Devuelve ruta → Lectura, o ruta → {"_error": mensaje, "_protegido": bool}."""
    contrasenas = contrasenas or {}
    out: dict[str, object] = {}
    n = len(rutas)
    for k, ruta in enumerate(rutas):
        _report(progress, k, n, f"«{Path(ruta).name}» ({k + 1} de {n})")
        try:
            out[ruta] = leer(ruta, contrasenas.get(ruta), paginas=paginas, usar_ocr=usar_ocr, idioma=idioma)
        except core.PasswordError:
            out[ruta] = {"_error": "Protegido: no se pudo abrir", "_protegido": True}
        except UserError as exc:
            out[ruta] = {"_error": str(exc), "_protegido": False}
        except MemoryError:
            out[ruta] = {"_error": "No hay suficiente memoria para leer este archivo.", "_protegido": False}
    _report(progress, n, n, "Listo")
    return out


# --------------------------------------------------------------------------
# Plantillas y propuestas
# --------------------------------------------------------------------------

_FICHA = re.compile(r"\{([^{}:]+)(?::([^{}]*))?\}")
_ALIAS = {"ano": "año", "anio": "año", "día": "dia", "cédula": "cedula", "título": "titulo", "páginas": "paginas",
          "nit2": "nit_2"}


def _ficha(nombre: str) -> str:
    n = nombre.strip().lower()
    return _ALIAS.get(n, n)


def fichas_desconocidas(plantilla: str) -> list[str]:
    return [m.group(1) for m in _FICHA.finditer(plantilla or "") if _ficha(m.group(1)) not in FICHAS]


def validar_plantilla(plantilla: str) -> None:
    if not (plantilla or "").strip():
        raise UserError("Escribe una plantilla para el nombre, por ejemplo {fecha}_{nit}_{factura}.")
    malas = fichas_desconocidas(plantilla)
    if malas:
        raise UserError(f"La ficha {{{malas[0]}}} no existe. Usa los botones para insertar fichas válidas.")


def _formatear_fecha(iso: str, formato: str) -> str:
    try:
        f = _dt.date.fromisoformat(iso)
    except (TypeError, ValueError):
        return ""
    return f.strftime(FORMATOS_FECHA.get(formato, FORMATOS_FECHA["AAAA-MM-DD"]))




def limpiar_nombre(nombre: str, fallback: str = "documento") -> str:
    """Nombre de archivo válido en Windows, sin espacios dobles ni separadores al borde."""
    n = " ".join(str(nombre).split())
    n = core.safe_filename(n, fallback)
    n = re.sub(r"_{2,}", "_", n)
    n = re.sub(r"-{2,}", "-", n)
    n = n.strip(" ._-") or fallback
    return core.safe_filename(n, fallback)


def componer(datos: dict, plantilla: str, *, formato_fecha: str = "AAAA-MM-DD", n: int = 1,
             si_falta: str = "marcar") -> tuple[str, list[str]]:
    """Aplica la plantilla.  Devuelve (nombre sin extensión, datos que faltan)."""
    validar_plantilla(plantilla)
    faltan: list[str] = []
    fecha = _formatear_fecha(datos.get("fecha", ""), formato_fecha)
    iso = datos.get("fecha", "") or ""

    def valor(m: re.Match) -> str:
        f, ancho = _ficha(m.group(1)), (m.group(2) or "").strip()
        if f == "n":
            try:
                return f"{n:0{int(ancho)}d}" if ancho else str(n)
            except ValueError:
                return str(n)
        if f in ("fecha", "año", "mes", "dia"):
            if not fecha:
                if "fecha" not in faltan:
                    faltan.append("fecha")
                return MARCA_FALTA["fecha"] if si_falta == "marcar" else ""
            return {"fecha": fecha, "año": iso[0:4], "mes": iso[5:7], "dia": iso[8:10]}[f]
        v = str(datos.get(f, "") or "").strip()
        if not v and f in MARCA_FALTA:
            if f not in faltan:
                faltan.append(f)
            return MARCA_FALTA[f] if si_falta == "marcar" else ""
        return v

    texto = _FICHA.sub(valor, plantilla)
    return limpiar_nombre(texto, datos.get("nombre") or "documento"), faltan


@dataclass
class Propuesta:
    origen: str
    datos: dict
    nuevo: str                      # nombre del archivo (con extensión), sin carpeta
    estado: str                     # ok | falta | duplicado | igual | error | protegido
    detalle: str = ""
    incluir: bool = True            # se renombra al aplicar
    faltan: list[str] = field(default_factory=list)
    manual: bool = False            # el nombre lo escribió la persona


def _con_extension(nombre: str, ext: str) -> str:
    nombre = nombre.strip()
    if ext and nombre.lower().endswith(ext.lower()):
        nombre = nombre[: -len(ext)]
    return limpiar_nombre(nombre) + ext


def proponer(archivos: Sequence[str], plantilla: str, *, formato_fecha: str = "AAAA-MM-DD",
             si_falta: str = "marcar", datos: dict | None = None, manuales: dict[str, str] | None = None,
             carpeta: str | None = None, contrasenas: dict[str, str] | None = None,
             progress: Progress = None, **opciones) -> list[Propuesta]:
    """Propone el nombre nuevo de cada archivo.

    ``datos``: ruta → datos ya leídos (o {"_error": …}); si no se da, se lee cada archivo (``opciones``
    van a ``extraer``).  ``manuales``: ruta → nombre escrito a mano.  ``carpeta``: carpeta de destino de
    las copias (por defecto, la carpeta de cada archivo).  Nunca propone un nombre que ya exista.
    """
    validar_plantilla(plantilla)
    manuales = {os.path.normcase(os.path.abspath(k)): v for k, v in (manuales or {}).items()}
    contrasenas = contrasenas or {}
    out: list[Propuesta] = []
    ocupados: set[str] = set()
    n = len(archivos)
    for k, ruta in enumerate(archivos):
        origen = Path(ruta)
        clave = os.path.normcase(os.path.abspath(ruta))
        ext = origen.suffix
        if datos is not None:
            d = datos.get(ruta)
        else:
            _report(progress, k, n, f"Leyendo «{origen.name}»")
            try:
                d = extraer(ruta, contrasenas.get(ruta), **opciones)
            except core.PasswordError:
                d = {"_error": "Protegido: no se pudo abrir", "_protegido": True}
            except UserError as exc:
                d = {"_error": str(exc)}
        if d is None or "_error" in d:
            estado = "protegido" if d and d.get("_protegido") else "error"
            msg = (d or {}).get("_error") or "Todavía no se ha leído"
            out.append(Propuesta(str(ruta), dict(d or {}), origen.name, estado, msg, incluir=False))
            ocupados.add(clave)
            continue
        d = dict(d)
        d.setdefault("nombre", origen.stem)
        manual = manuales.get(clave)
        if manual:
            nuevo, faltan = _con_extension(manual, ext), []
        else:
            stem, faltan = componer(d, plantilla, formato_fecha=formato_fecha, n=k + 1, si_falta=si_falta)
            nuevo = stem + ext
        p = Propuesta(str(ruta), d, nuevo, "ok", "", faltan=faltan, manual=bool(manual))
        if manual:
            p.detalle = "Nombre escrito a mano"
        if faltan:
            p.estado = "falta"
            lista = ", ".join(NOMBRES_DATO.get(f, f) for f in faltan)
            if si_falta == "omitir":
                p.nuevo, p.incluir = origen.name, False
                p.detalle = f"Falta: {lista} (se deja con su nombre)"
                ocupados.add(clave)
                out.append(p)
                continue
            p.detalle = f"Falta: {lista}"
        destino_dir = Path(carpeta) if carpeta else origen.parent
        cand = destino_dir / p.nuevo
        stem_nuevo, k_dup = Path(p.nuevo).stem, 1
        while True:
            ck = os.path.normcase(os.path.abspath(cand))
            if ck not in ocupados and ((ck == clave and not carpeta) or not os.path.exists(cand)):
                break
            k_dup += 1
            cand = destino_dir / f"{stem_nuevo} ({k_dup}){ext}"
        if not carpeta and os.path.normcase(os.path.abspath(cand)) == clave:
            # Ya se llama así (también «X (2).pdf» cuando «X.pdf» es de otro archivo).
            p.nuevo = cand.name
            p.estado, p.incluir, p.detalle = "igual", False, p.detalle or "Ya tiene ese nombre"
            ocupados.add(clave)
            out.append(p)
            continue
        if k_dup > 1:
            p.nuevo = cand.name
            if p.estado == "ok":
                p.estado = "duplicado"
            p.detalle = (p.detalle + " · " if p.detalle else "") + f"Duplicado: se agregó ({k_dup})"
        ocupados.add(os.path.normcase(os.path.abspath(cand)))
        out.append(p)
    return out


# --------------------------------------------------------------------------
# Aplicar y deshacer
# --------------------------------------------------------------------------

def carpeta_datos(nombre: str = CARPETA_REGISTROS) -> Path:
    """Carpeta junto al archivo de ajustes (como «RonyPDF_firmas»)."""
    from .widgets import ctx
    if ctx.settings is not None:
        base = Path(ctx.settings.path).parent
    else:
        from .settings import Settings
        base = Path(Settings().path).parent
    return base / nombre


@dataclass
class Registro:
    modo: str                                   # renombrar | copiar
    fecha: str
    pares: list[tuple[str, str]] = field(default_factory=list)   # (origen, destino)
    tamanos: list[tuple[int, int]] = field(default_factory=list)  # copias: (bytes, mtime_ns) de cada destino
    archivo: str | None = None
    deshecho: bool = False

    def a_json(self) -> dict:
        return {"version": 1, "modo": self.modo, "fecha": self.fecha, "pares": [list(p) for p in self.pares],
                "tamanos": [list(t) for t in self.tamanos], "deshecho": self.deshecho}

    @classmethod
    def de_json(cls, data: dict, archivo: str | None = None) -> "Registro":
        return cls(modo=str(data.get("modo", "renombrar")), fecha=str(data.get("fecha", "")),
                   pares=[(str(a), str(b)) for a, b in data.get("pares", [])],
                   tamanos=[(int(a), int(b)) for a, b in data.get("tamanos", [])],
                   archivo=archivo, deshecho=bool(data.get("deshecho")))


def guardar_registro(reg: Registro, carpeta: str | Path | None = None) -> Path:
    base = Path(carpeta) if carpeta else carpeta_datos()
    base.mkdir(parents=True, exist_ok=True)
    if reg.archivo:
        ruta = Path(reg.archivo)
    else:
        sello = time.strftime("%Y%m%d-%H%M%S")
        ruta = base / f"renombrado-{sello}-{secrets.token_hex(2)}.json"
        reg.archivo = str(ruta)
    with core._output(ruta) as tmp:
        Path(tmp).write_text(json.dumps(reg.a_json(), ensure_ascii=False, indent=2), encoding="utf-8")
    viejos = sorted(base.glob("renombrado-*.json"), key=lambda p: p.name, reverse=True)[MAX_REGISTROS:]
    for p in viejos:
        try:
            p.unlink()
        except OSError:
            pass
    return ruta


def ultimo_registro(carpeta: str | Path | None = None) -> Registro | None:
    """El último renombrado que todavía se puede deshacer."""
    base = Path(carpeta) if carpeta else carpeta_datos()
    if not base.is_dir():
        return None
    for p in sorted(base.glob("renombrado-*.json"), key=lambda p: p.name, reverse=True):
        try:
            reg = Registro.de_json(json.loads(p.read_text(encoding="utf-8")), str(p))
        except (OSError, ValueError, TypeError):
            continue
        if reg.deshecho or not reg.pares:
            continue
        return reg
    return None


@dataclass
class Resultado:
    hechos: int = 0
    omitidos: int = 0
    errores: list[tuple[str, str]] = field(default_factory=list)
    registro: Registro | None = None
    nuevos: dict[str, str] = field(default_factory=dict)     # origen → ruta nueva


def _mensaje_error(exc: OSError) -> str:
    if isinstance(exc, PermissionError):
        return "Está abierto en otro programa (o no hay permiso para cambiarlo)"
    if isinstance(exc, FileNotFoundError):
        return "Ya no está en esa carpeta"
    return f"No se pudo: {exc.strerror or exc}"


def aplicar(propuestas: Sequence[Propuesta], modo: str = "renombrar", carpeta: str | None = None,
            progress: Progress = None, carpeta_registro: str | Path | None = None) -> Resultado:
    """Cambia los nombres (``renombrar``, en la misma carpeta) o deja copias en ``carpeta`` (``copiar``).

    Un archivo abierto en otro programa queda como error y el resto sigue.  Nunca se pisa un archivo."""
    if modo not in ("renombrar", "copiar"):
        raise ValueError(modo)
    if modo == "copiar":
        if not carpeta:
            raise UserError("Elige la carpeta donde se dejarán las copias.")
        try:
            Path(carpeta).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise UserError(f"No se pudo usar la carpeta «{carpeta}»: {_mensaje_error(exc)}.") from exc
    res = Resultado(registro=Registro(modo, _dt.datetime.now().isoformat(timespec="seconds")))
    activas = [p for p in propuestas if p.incluir and p.estado not in ("igual", "error", "protegido")]
    res.omitidos = len(propuestas) - len(activas)
    n = len(activas)
    for k, p in enumerate(activas):
        _report(progress, k, n, f"«{Path(p.origen).name}»")
        origen = Path(p.origen)
        base = Path(carpeta) if modo == "copiar" else origen.parent
        destino = base / p.nuevo
        try:
            for _intento in range(50):
                mismo = os.path.normcase(os.path.abspath(destino)) == os.path.normcase(os.path.abspath(origen))
                if destino.exists() and not mismo:
                    destino = core.unique_path(destino)
                try:
                    if modo == "renombrar":
                        os.rename(origen, destino)
                    else:
                        tmp = destino.with_name(f".~{destino.stem}.{secrets.token_hex(3)}.tmp")
                        try:
                            shutil.copy2(origen, tmp)
                            os.rename(tmp, destino)       # falla si alguien creó ese nombre mientras tanto
                        finally:
                            if tmp.exists():
                                tmp.unlink()
                    break
                except FileExistsError:
                    destino = core.unique_path(destino)
            else:
                raise FileExistsError(str(destino))
        except OSError as exc:
            res.errores.append((p.origen, _mensaje_error(exc)))
            continue
        res.hechos += 1
        res.nuevos[p.origen] = str(destino)
        res.registro.pares.append((str(origen), str(destino)))
        if modo == "copiar":
            st = destino.stat()
            res.registro.tamanos.append((st.st_size, st.st_mtime_ns))
    _report(progress, n, n, "Listo")
    if res.registro.pares:
        try:
            guardar_registro(res.registro, carpeta_registro)
        except OSError:
            res.registro.archivo = None       # sin registro no se podrá deshacer, pero el trabajo ya está hecho
    return res


def deshacer(registro: Registro, carpeta_registro: str | Path | None = None) -> int:
    """Devuelve los nombres originales (solo de los archivos que sigan donde quedaron).

    Si eran copias, borra las que no se hayan modificado después.  Devuelve cuántos archivos se deshicieron."""
    hechos = 0
    if registro.modo == "copiar":
        tamanos = registro.tamanos + [(None, None)] * (len(registro.pares) - len(registro.tamanos))
        for (_origen, destino), (tam, mtime) in zip(registro.pares, tamanos):
            try:
                st = os.stat(destino)
                if tam is not None and (st.st_size != tam or st.st_mtime_ns != mtime):
                    continue                          # la copia cambió después: no se toca
                os.remove(destino)
                hechos += 1
            except OSError:
                continue
    else:
        for origen, destino in reversed(registro.pares):
            mismo = os.path.normcase(os.path.abspath(origen)) == os.path.normcase(os.path.abspath(destino))
            if not os.path.exists(destino) or (os.path.exists(origen) and not mismo):
                continue
            try:
                os.rename(destino, origen)
                hechos += 1
            except OSError:
                continue
    registro.deshecho = True
    if registro.archivo:
        try:
            guardar_registro(registro, carpeta_registro or Path(registro.archivo).parent)
        except OSError:
            pass
    return hechos


__all__ = ["Lectura", "Propuesta", "Registro", "Resultado", "leer", "analizar", "extraer", "leer_lote", "proponer",
           "componer", "aplicar", "deshacer", "ultimo_registro", "validar_plantilla", "FICHAS", "FORMATOS_FECHA"]

