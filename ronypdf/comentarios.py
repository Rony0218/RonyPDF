"""Comentarios personales en un PDF: notas adhesivas, resaltados, subrayados, tachados, cuadros de texto,
sellos, respuestas en hilo y estados de revisión.

Todo se escribe como anotaciones PDF estándar, así que Acrobat, Edge, PdfCraft y cualquier lector que
siga la norma los ven con su autor, su hilo y su estado.  Los comentarios llevan el nombre de quien los
escribe; aquí no hay nada de ninguna organización.

Sin interfaz.  El visor guarda los cambios como una lista de **operaciones** (diccionarios simples) y
``aplicar(doc, ops)`` los escribe en un ``pymupdf.Document``.  La misma función sirve para verlos enseguida
en el documento en memoria y para escribirlos al guardar sobre una copia recién abierta del archivo, de modo
que lo que se ve es lo que queda.  Las coordenadas de las operaciones son puntos PDF de la página tal como
se ve (con su giro aplicado), las mismas que usa el lienzo del visor.

Operaciones (todas llevan ``"op"``):
    crear      tipo (nota, resaltado, subrayado, tachado, ondulado, cuadro, sello), pagina, nm, autor,
               texto, fecha, color y, según el tipo: punto (nota), rects (un rectángulo por renglón, para
               las marcas de texto), rect (cuadro y sello), png en base64 (sello), letra y fondo (cuadro).
    editar     ref, texto, fecha
    mover      ref, rect (también cambia el tamaño de sellos y cuadros)
    color      ref, color
    borrar     ref (con todas sus respuestas)
    responder  ref, nm, autor, texto, fecha
    estado     ref, nm, autor, estado (None, "Accepted", "Rejected", "Cancelled", "Completed"), fecha
``ref`` es ``{"pagina": p, "nm": "rpdf-…"}`` para lo creado en la sesión o ``{"pagina": p, "xref": n}`` para
lo que ya venía en el archivo (el mismo archivo recién abierto tiene los mismos xref).
"""

from __future__ import annotations

import base64
import datetime as _dt
import os
import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

import pymupdf

from . import core
from .core import UserError

Progress = Callable[[int, int, str], None] | None

# ---------------------------------------------------------------- tipos, estados y colores

TIPOS = {
    "nota": "Nota", "resaltado": "Resaltado", "subrayado": "Subrayado", "tachado": "Tachado",
    "ondulado": "Subrayado ondulado", "cuadro": "Cuadro de texto", "sello": "Sello", "otro": "Anotación",
}
MARCAS = ("resaltado", "subrayado", "tachado", "ondulado")
MOVIBLES = ("nota", "cuadro", "sello")
SUBTIPO_A_TIPO = {"Text": "nota", "Highlight": "resaltado", "Underline": "subrayado", "StrikeOut": "tachado",
                  "Squiggly": "ondulado", "FreeText": "cuadro", "Stamp": "sello"}
NOMBRES_OTROS = {
    "Ink": "Dibujo a mano alzada", "Square": "Rectángulo", "Circle": "Elipse", "Line": "Línea",
    "Polygon": "Polígono", "PolyLine": "Trazo", "FileAttachment": "Archivo adjunto", "Sound": "Sonido",
    "Caret": "Marca de inserción", "Redact": "Zona marcada para censurar", "Movie": "Video",
    "Screen": "Contenido multimedia", "RichMedia": "Contenido multimedia", "3D": "Modelo 3D",
    "Projection": "Proyección",
}
# Lo que no es un comentario: campos de formulario, enlaces, ventanas emergentes y marcas de impresión.
IGNORADOS = {"Popup", "Link", "Widget", "PrinterMark", "TrapNet", "Watermark"}

ESTADOS = {None: "Sin estado", "Accepted": "Aceptado", "Rejected": "Rechazado", "Cancelled": "Cancelado",
           "Completed": "Completado"}
ESTADOS_LISTA = [None, "Accepted", "Rejected", "Cancelled", "Completed"]

COLORES = {"nota": "#F5B700", "resaltado": "#FFE14D", "subrayado": "#1F6FD1", "tachado": "#D63A3A",
           "ondulado": "#D97706", "cuadro": "#1F2937", "sello": "#1E4FA0"}
FONDO_CUADRO = "#FFF9DB"
LETRA_CUADRO = 11
PREFIJO_NM = "rpdf-"
_ICONO_NOTA = "Comment"
# Banderas PDF: 4 imprimir, 8 sin zoom, 16 sin giro; 2 oculto (los cambios de estado no se dibujan).
BANDERAS_RESPUESTA = 28
BANDERAS_ESTADO = 30
_OCULTO = 2 | 32


def nuevo_nm() -> str:
    """Identificador propio para una anotación nueva (va en su /NM)."""
    return PREFIJO_NM + secrets.token_hex(6)


def ahora_pdf() -> str:
    """Fecha y hora actuales en el formato de fechas de PDF («D:20250312154200-05'00'»).

    No usa ``pymupdf.get_pdf_now()``: arma el desfase con el del horario de verano aunque no esté en vigor, y
    en Colombia (UTC−5, sin horario de verano) escribe «-04'00'», una hora corrida."""
    return a_fecha_pdf(_dt.datetime.now())


# ---------------------------------------------------------------- fechas

_RE_FECHA = re.compile(r"D?:?(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?\s*([Zz+\-])?(\d{2})?'?(\d{2})?'?")
_MESES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")


def fecha_pdf(texto: str | None) -> _dt.datetime | None:
    """«D:20250312154200-05'00'» → datetime (con zona si la trae); None si no se entiende."""
    if not texto:
        return None
    m = _RE_FECHA.match(str(texto).strip())
    if not m:
        return None
    try:
        y = int(m.group(1))
        mes, dia = int(m.group(2) or 1), int(m.group(3) or 1)
        h, mi, s = int(m.group(4) or 0), int(m.group(5) or 0), int(m.group(6) or 0)
        tz = None
        signo = m.group(7)
        if signo in ("Z", "z"):
            tz = _dt.timezone.utc
        elif signo in ("+", "-"):
            delta = _dt.timedelta(hours=int(m.group(8) or 0), minutes=int(m.group(9) or 0))
            tz = _dt.timezone(delta if signo == "+" else -delta)
        return _dt.datetime(y, mes, dia, h, mi, min(s, 59), tzinfo=tz)
    except (ValueError, OverflowError):
        return None


def a_fecha_pdf(fecha: _dt.datetime) -> str:
    """datetime → fecha de PDF."""
    if fecha.tzinfo is None:
        fecha = fecha.astimezone()
    off = fecha.utcoffset() or _dt.timedelta(0)
    minutos = int(off.total_seconds() // 60)
    signo = "+" if minutos >= 0 else "-"
    minutos = abs(minutos)
    return fecha.strftime("D:%Y%m%d%H%M%S") + f"{signo}{minutos // 60:02d}'{minutos % 60:02d}'"


def _local(fecha: _dt.datetime) -> _dt.datetime:
    return fecha.astimezone().replace(tzinfo=None) if fecha.tzinfo else fecha


def hora_texto(fecha: _dt.datetime) -> str:
    """«3:42 p. m.» (como se escribe en Colombia)."""
    fecha = _local(fecha)
    h = fecha.hour % 12 or 12
    return f"{h}:{fecha.minute:02d} {'a. m.' if fecha.hour < 12 else 'p. m.'}"


def fecha_corta(fecha: _dt.datetime | None, hoy: _dt.date | None = None) -> str:
    """«hoy 3:42 p. m.», «ayer 9:05 a. m.» o «12 mar 2025»."""
    if fecha is None:
        return ""
    f = _local(fecha)
    hoy = hoy or _dt.date.today()
    if f.date() == hoy:
        return f"hoy {hora_texto(f)}"
    if f.date() == hoy - _dt.timedelta(days=1):
        return f"ayer {hora_texto(f)}"
    return f"{f.day} {_MESES[f.month - 1]} {f.year}"


def fecha_larga(fecha: _dt.datetime | None) -> str:
    """«12/03/2025 3:42 p. m.»."""
    if fecha is None:
        return ""
    f = _local(fecha)
    return f"{f.day:02d}/{f.month:02d}/{f.year} {hora_texto(f)}"


# ---------------------------------------------------------------- modelo

@dataclass
class Respuesta:
    id: str
    xref: int
    autor: str
    texto: str
    fecha: _dt.datetime | None
    nm: str = ""
    padre: int = 0                     # xref de la anotación a la que responde
    estado: str | None = None          # si es un cambio de estado: «Accepted»…
    es_estado: bool = False


@dataclass
class Comentario:
    id: str
    xref: int
    pagina: int
    tipo: str
    rect: tuple[float, float, float, float]          # coordenadas visibles de la página
    autor: str
    texto: str
    texto_marcado: str | None                       # None: todavía no se ha calculado
    creado: _dt.datetime | None
    modificado: _dt.datetime | None
    color: str
    estado: str | None
    respuestas: list[Respuesta]
    movible: bool
    nm: str = ""
    subtipo: str = ""
    nombre_tipo: str = ""
    zonas: list[tuple[float, float, float, float]] = field(default_factory=list)
    oculto: bool = False
    quads: list[tuple[float, float, float, float]] = field(default_factory=list, repr=False)   # sin girar

    @property
    def respondido(self) -> bool:
        return any(not r.es_estado for r in self.respuestas)

    @property
    def fecha(self) -> _dt.datetime | None:
        return self.modificado or self.creado


def referencia(c: Comentario | Respuesta, pagina: int, creados: Iterable[str] = ()) -> dict:
    """Cómo ubicar esta anotación en una operación: por /NM si se creó en la sesión, si no por xref."""
    if c.nm and c.nm in set(creados):
        return {"pagina": pagina, "nm": c.nm}
    return {"pagina": pagina, "xref": c.xref}


def _bound(annot) -> pymupdf.Rect:
    """Rectángulo de la anotación tal como se ve en la página (con el giro de la página)."""
    return pymupdf.Rect(pymupdf.mupdf.pdf_bound_annot(annot.this))


def _hex(rgb) -> str:
    if not rgb or len(rgb) < 3:
        return ""
    r, g, b = (max(0, min(255, round(float(v) * 255))) for v in rgb[:3])
    return f"#{r:02X}{g:02X}{b:02X}"


def _rgb(hexa: str | None, defecto: str = "#000000") -> tuple[float, float, float]:
    h = (hexa or defecto).lstrip("#")
    if len(h) != 6:
        h = defecto.lstrip("#")
    try:
        return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return (0.0, 0.0, 0.0)


_RE_DA = re.compile(r"([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+rg")


def _color_cuadro(doc: pymupdf.Document, xref: int) -> str:
    """Color del texto de un cuadro de texto (va en /DA, no en /C)."""
    try:
        da = doc.xref_get_key(xref, "DA")
        m = _RE_DA.search(da[1]) if da[0] == "string" else None
        if m:
            return _hex([float(m.group(i)) for i in (1, 2, 3)])
    except Exception:  # noqa: BLE001
        pass
    return ""


def _clave_pdf(doc: pymupdf.Document, xref: int, clave: str) -> str | None:
    try:
        t, v = doc.xref_get_key(xref, clave)
    except Exception:  # noqa: BLE001
        return None
    if t in ("null", None):
        return None
    if t == "name":
        return v.lstrip("/")
    return v


def _quads_de(annot) -> list[pymupdf.Rect]:
    v = annot.vertices or []
    out = []
    for k in range(0, len(v) - 3, 4):
        out.append(pymupdf.Quad(v[k], v[k + 1], v[k + 2], v[k + 3]).rect)
    return out


@dataclass
class _Cruda:
    """Una anotación leída, antes de armar los hilos."""
    xref: int
    subtipo: str
    nm: str
    autor: str
    texto: str
    asunto: str
    creado: _dt.datetime | None
    modificado: _dt.datetime | None
    irt: int
    rt: str | None
    flags: int
    rect: pymupdf.Rect
    color: str
    estado: str | None
    modelo: str | None
    zonas: list
    quads: list


def _leer_una(doc: pymupdf.Document, page: pymupdf.Page, a) -> _Cruda | None:
    subtipo = a.type[1]
    if subtipo in IGNORADOS:
        return None
    info = a.info
    xref = a.xref
    estado = modelo = None
    rt = None
    irt = a.irt_xref or 0
    if irt:
        rt = _clave_pdf(doc, xref, "RT")
    if subtipo == "Text":
        estado = _clave_pdf(doc, xref, "State")
        modelo = _clave_pdf(doc, xref, "StateModel")
    rect = _bound(a)
    color = _color_cuadro(doc, xref) if subtipo == "FreeText" else _hex(
        (a.colors or {}).get("stroke") or (a.colors or {}).get("fill"))
    zonas, quads = [], []
    if subtipo in ("Highlight", "Underline", "StrikeOut", "Squiggly"):
        rm = page.rotation_matrix
        for q in _quads_de(a):
            quads.append(tuple(q))
            zonas.append(tuple(q * rm if page.rotation else q))
    if not zonas and not rect.is_empty:
        zonas = [tuple(rect)]
    return _Cruda(xref=xref, subtipo=subtipo, nm=info.get("id") or "", autor=info.get("title") or "",
                  texto=info.get("content") or "", asunto=info.get("subject") or "",
                  creado=fecha_pdf(info.get("creationDate")), modificado=fecha_pdf(info.get("modDate")),
                  irt=irt, rt=rt, flags=a.flags or 0, rect=rect, color=color, estado=estado, modelo=modelo,
                  zonas=zonas, quads=quads)


def _es_estado(c: _Cruda) -> bool:
    return c.subtipo == "Text" and c.irt and c.estado is not None and (c.modelo in (None, "Review"))


def _orden_fecha(c) -> tuple:
    f = c.modificado or c.creado
    return (0, _local(f).timestamp()) if f else (1, 0.0)


def leer(doc: pymupdf.Document, paginas: Iterable[int] | None = None, marcado: bool = False) -> list[Comentario]:
    """Los comentarios del documento (o de esas páginas), con sus respuestas y su estado vigente.

    Una anotación dañada se omite sin tumbar la lectura.  Con ``marcado`` también se calcula el texto que
    cubren los resaltados, subrayados y tachados (cuesta extraer el texto de la página: el visor lo pide
    después, poco a poco, con ``calcular_marcado``)."""
    out: list[Comentario] = []
    idx = range(doc.page_count) if paginas is None else sorted({p for p in paginas if 0 <= p < doc.page_count})
    vistos_nm: set[str] = set()
    for pno in idx:
        try:
            page = doc[pno]
            anots = list(page.annots())
        except Exception:  # noqa: BLE001 — una página dañada no impide leer las demás
            continue
        crudas: list[_Cruda] = []
        for a in anots:
            try:
                c = _leer_una(doc, page, a)
            except Exception:  # noqa: BLE001 — anotación dañada
                c = None
            if c is not None:
                crudas.append(c)
        por_xref = {c.xref: c for c in crudas}
        raices: dict[int, Comentario] = {}
        hijos: list[_Cruda] = []
        for c in crudas:
            es_respuesta = c.irt and c.irt in por_xref and c.rt not in ("Group",) and c.irt != c.xref
            if es_respuesta:
                hijos.append(c)
                continue
            if c.subtipo == "Text" and c.modelo == "Marked":
                continue          # marcas personales de Acrobat (casilla), no son comentarios
            tipo = SUBTIPO_A_TIPO.get(c.subtipo, "otro")
            nm_ok = c.nm.startswith(PREFIJO_NM) and c.nm not in vistos_nm
            if nm_ok:
                vistos_nm.add(c.nm)
            raices[c.xref] = Comentario(
                id=c.nm if nm_ok else f"x{c.xref}", xref=c.xref, pagina=pno, tipo=tipo, rect=tuple(c.rect),
                autor=c.autor, texto=c.texto, texto_marcado=None if tipo in MARCAS else "",
                creado=c.creado, modificado=c.modificado, color=c.color, estado=None, respuestas=[],
                movible=tipo in MOVIBLES, nm=c.nm, subtipo=c.subtipo,
                nombre_tipo=TIPOS[tipo] if tipo != "otro" else NOMBRES_OTROS.get(c.subtipo, "Anotación"),
                zonas=list(c.zonas), oculto=bool(c.flags & _OCULTO), quads=list(c.quads))
        for c in hijos:
            # sube por el hilo hasta la anotación principal (con tope, por si el archivo trae un ciclo)
            raiz, paso, vistos = c.irt, 0, {c.xref}
            while raiz in por_xref and raiz not in raices and paso < 64:
                if raiz in vistos:
                    break
                vistos.add(raiz)
                raiz = por_xref[raiz].irt
                paso += 1
            principal = raices.get(raiz)
            if principal is None:
                continue
            principal.respuestas.append(Respuesta(
                id=c.nm if c.nm.startswith(PREFIJO_NM) else f"x{c.xref}", xref=c.xref, autor=c.autor,
                texto=c.texto, fecha=c.modificado or c.creado, nm=c.nm, padre=c.irt,
                estado=(None if c.estado in (None, "None") else c.estado) if _es_estado(c) else None,
                es_estado=bool(_es_estado(c))))
        for principal in raices.values():
            principal.respuestas.sort(key=lambda r: ((0, _local(r.fecha).timestamp()) if r.fecha else (1, 0.0), r.xref))
            for r in principal.respuestas:
                if r.es_estado:
                    principal.estado = r.estado if r.estado in ESTADOS else None
            out.append(principal)
        if marcado:
            try:
                calcular_marcado(page, [c for c in raices.values() if c.tipo in MARCAS])
            except Exception:  # noqa: BLE001
                pass
    return out


def calcular_marcado(page: pymupdf.Page, comentarios: Sequence[Comentario], palabras=None) -> None:
    """Llena ``texto_marcado`` de los resaltados, subrayados y tachados de esta página."""
    pendientes = [c for c in comentarios if c.tipo in MARCAS]
    if not pendientes:
        return
    if palabras is None:
        palabras = page.get_text("words")
    for c in pendientes:
        rects = [pymupdf.Rect(q) for q in c.quads]
        trozos = []
        for x0, y0, x1, y1, w, *_ in palabras:
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            if any(r.x0 - 1 <= cx <= r.x1 + 1 and r.y0 - 1 <= cy <= r.y1 + 1 for r in rects):
                trozos.append(w)
        c.texto_marcado = " ".join(trozos)


# ---------------------------------------------------------------- escribir: operaciones

def _pagina(doc: pymupdf.Document, n) -> pymupdf.Page:
    try:
        n = int(n)
    except (TypeError, ValueError) as exc:
        raise UserError("La operación no indica una página válida.") from exc
    if not 0 <= n < doc.page_count:
        raise UserError(f"La página {n + 1} no existe en este documento.")
    return doc[n]


def _buscar(doc: pymupdf.Document, ref: dict | None):
    """(página, anotación) que señala ``ref``; (página, None) si ya no está."""
    ref = ref or {}
    page = _pagina(doc, ref.get("pagina"))
    try:
        if ref.get("nm"):
            for a in page.annots():
                if (a.info.get("id") or "") == ref["nm"]:
                    return page, a
            return page, None
        xref = int(ref.get("xref") or 0)
        if xref <= 0:
            return page, None
        for a in page.annots():
            if a.xref == xref:
                return page, a
    except Exception:  # noqa: BLE001
        pass
    return page, None


def _poner_nm(doc: pymupdf.Document, xref: int, nm: str | None) -> None:
    if nm:
        doc.xref_set_key(xref, "NM", pymupdf.get_pdf_str(str(nm)))


def _poner_texto_crudo(doc: pymupdf.Document, xref: int, clave: str, texto: str) -> None:
    doc.xref_set_key(xref, clave, pymupdf.get_pdf_str(texto or ""))


def _vaciar_apariencia(doc: pymupdf.Document, xref: int) -> None:
    """Las respuestas y los cambios de estado no se dibujan en la página (se ven en el hilo): su apariencia
    es un formulario vacío, así ningún lector pinta un ícono encima del comentario original."""
    f = doc.get_new_xref()
    doc.update_object(f, "<< /Type /XObject /Subtype /Form /BBox [ 0 0 0 0 ] /Resources << >> >>")
    doc.update_stream(f, b"")
    doc.xref_set_key(xref, "AP", f"<< /N {f} 0 R >>")


def _pixmap_sello(png_b64: str, giro: int) -> pymupdf.Pixmap:
    """Imagen del sello; en una página con /Rotate se gira para que se vea derecha."""
    try:
        png = base64.b64decode(png_b64)
        pix = pymupdf.Pixmap(png)
    except Exception as exc:  # noqa: BLE001
        raise UserError("La imagen del sello está dañada.") from exc
    giro %= 360
    if not giro:
        return pix
    w, h = (pix.height, pix.width) if giro % 180 else (pix.width, pix.height)
    with pymupdf.open() as tmp:
        pg = tmp.new_page(width=w, height=h)
        pg.insert_image(pg.rect, stream=png, rotate=giro)
        return pg.get_pixmap(dpi=72, alpha=True)


def _info(a, op: dict, asunto: str) -> None:
    fecha = op.get("fecha") or ahora_pdf()
    a.set_info(title=op.get("autor") or "", content=op.get("texto") or "", subject=asunto,
               creationDate=fecha, modDate=fecha)


def _crear(doc: pymupdf.Document, op: dict) -> None:
    page = _pagina(doc, op.get("pagina"))
    tipo = op.get("tipo")
    if tipo not in COLORES:
        raise UserError(f"No se puede crear un comentario de tipo «{tipo}».")
    color = _rgb(op.get("color"), COLORES[tipo])
    derot = page.derotation_matrix
    texto = op.get("texto") or ""
    if tipo == "nota":
        x, y = op["punto"]
        a = page.add_text_annot(pymupdf.Point(x, y) * derot, texto, icon=op.get("icono") or _ICONO_NOTA)
    elif tipo in MARCAS:
        rects = [pymupdf.Rect(r) for r in op.get("rects") or []]
        rects = [r for r in rects if not r.is_empty]
        if not rects:
            raise UserError("No hay texto que marcar.")
        quads = [(r * derot).quad for r in rects]
        fn = {"resaltado": page.add_highlight_annot, "subrayado": page.add_underline_annot,
              "tachado": page.add_strikeout_annot, "ondulado": page.add_squiggly_annot}[tipo]
        a = fn(quads)
    elif tipo == "cuadro":
        r = pymupdf.Rect(op["rect"]) * derot
        a = page.add_freetext_annot(r, texto, fontsize=float(op.get("letra") or LETRA_CUADRO), fontname="helv",
                                    text_color=color, fill_color=_rgb(op.get("fondo"), FONDO_CUADRO),
                                    border_width=1, rotate=page.rotation)
    else:  # sello
        pix = _pixmap_sello(op.get("png") or "", page.rotation)
        a = page.add_stamp_annot(pymupdf.Rect(op["rect"]) * derot, stamp=pix)
    _info(a, op, op.get("asunto") or TIPOS[tipo])
    if tipo != "cuadro":
        a.set_colors(stroke=color)
    a.update()
    _poner_nm(doc, a.xref, op.get("nm"))


def _editar(doc: pymupdf.Document, op: dict) -> None:
    _page, a = _buscar(doc, op.get("ref"))
    if a is None:
        return
    texto = op.get("texto") or ""
    fecha = op.get("fecha") or ahora_pdf()
    if doc.xref_get_key(a.xref, "RC")[0] != "null":
        # el texto con formato que deja Acrobat se mostraría en vez del nuevo: se quita
        doc.xref_set_key(a.xref, "RC", "null")
        _page, a = _buscar(doc, op.get("ref"))
        if a is None:
            return
    if a.type[1] == "FreeText":
        # el texto de un cuadro sí cambia su apariencia: se vuelve a dibujar
        a.set_info(content=texto, modDate=fecha)
        a.update()
    else:
        # el resto se cambia sin tocar su apariencia (así un sello hecho en otro programa se ve igual)
        _poner_texto_crudo(doc, a.xref, "Contents", texto)
        _poner_texto_crudo(doc, a.xref, "M", fecha)


def _rect_crudo(doc: pymupdf.Document, xref: int) -> pymupdf.Rect:
    t, v = doc.xref_get_key(xref, "Rect")
    nums = [float(x) for x in v.strip("[] ").split()] if t == "array" else []
    if len(nums) != 4:
        raise UserError("La anotación no tiene un rectángulo válido.")
    return pymupdf.Rect(nums).normalize()


def _poner_rect_crudo(doc: pymupdf.Document, xref: int, r: pymupdf.Rect) -> None:
    r = pymupdf.Rect(r).normalize()
    doc.xref_set_key(xref, "Rect", f"[ {r.x0:.4f} {r.y0:.4f} {r.x1:.4f} {r.y1:.4f} ]")


def _vis_a_pdf(page: pymupdf.Page) -> pymupdf.Matrix:
    """Coordenadas visibles de la página → espacio del PDF (el de /Rect)."""
    return page.derotation_matrix * ~page.transformation_matrix


def _mover(doc: pymupdf.Document, op: dict) -> None:
    page, a = _buscar(doc, op.get("ref"))
    if a is None:
        return
    destino = pymupdf.Rect(op["rect"]).normalize()
    if destino.is_empty:
        return
    actual = _bound(a)
    m = _vis_a_pdf(page)
    xref, subtipo = a.xref, a.type[1]
    mismo_tamano = abs(actual.width - destino.width) < 0.05 and abs(actual.height - destino.height) < 0.05
    if mismo_tamano or subtipo == "Text":
        # traslado: se corre el /Rect sin tocar la apariencia (también si la hizo otro programa)
        d = pymupdf.Point(destino.x0 - actual.x0, destino.y0 - actual.y0)
        dp = (d * m) - (pymupdf.Point(0, 0) * m)
        _poner_rect_crudo(doc, xref, _rect_crudo(doc, xref) + (dp.x, dp.y, dp.x, dp.y))
    elif subtipo == "FreeText":
        a.set_rect(destino * page.derotation_matrix)
        a.update()                               # el texto se vuelve a acomodar en el tamaño nuevo
    else:
        _poner_rect_crudo(doc, xref, destino * m)    # un sello se agranda con su misma apariencia
    _poner_texto_crudo(doc, xref, "M", op.get("fecha") or ahora_pdf())


def _color(doc: pymupdf.Document, op: dict) -> None:
    _page, a = _buscar(doc, op.get("ref"))
    if a is None or a.type[1] in ("Stamp", "FileAttachment", "Sound"):
        return
    color = _rgb(op.get("color"))
    if a.type[1] == "FreeText":
        a.update(text_color=color)
    else:
        a.set_colors(stroke=color)
        a.update()
    if op.get("fecha"):
        _poner_texto_crudo(doc, a.xref, "M", op["fecha"])


def _descendientes(page: pymupdf.Page, xref: int) -> list[int]:
    """xref de las respuestas (y respuestas a respuestas) de una anotación, de la más profunda a la más cercana."""
    hijos: dict[int, list[int]] = {}
    for a in page.annots():
        if a.irt_xref:
            hijos.setdefault(a.irt_xref, []).append(a.xref)
    orden: list[int] = []
    pila, vistos = [xref], {xref}
    while pila:
        x = pila.pop()
        for h in hijos.get(x, []):
            if h not in vistos:
                vistos.add(h)
                orden.append(h)
                pila.append(h)
    return list(reversed(orden))


def _borrar(doc: pymupdf.Document, op: dict) -> None:
    page, a = _buscar(doc, op.get("ref"))
    if a is None:
        return
    for x in _descendientes(page, a.xref):
        for r in page.annots():
            if r.xref == x:
                page.delete_annot(r)
                break
    for r in page.annots():
        if r.xref == a.xref:
            page.delete_annot(r)
            break


def _responder(doc: pymupdf.Document, op: dict, estado: bool = False) -> None:
    page, padre = _buscar(doc, op.get("ref"))
    if padre is None:
        return
    rect_padre = _rect_crudo(doc, padre.xref)
    color = (padre.colors or {}).get("stroke") or _rgb(COLORES["nota"])
    if estado:
        clave = op.get("estado")
        clave = clave if clave in ESTADOS else None
        texto = f"{ESTADOS[clave]} por {op.get('autor') or 'alguien'}"
    else:
        texto = op.get("texto") or ""
    r = page.add_text_annot(_bound(padre).tl * page.derotation_matrix, texto, icon=_ICONO_NOTA)
    fecha = op.get("fecha") or ahora_pdf()
    r.set_info(title=op.get("autor") or "", content=texto, subject="Estado" if estado else "Respuesta",
               creationDate=fecha, modDate=fecha)
    r.set_irt_xref(padre.xref)
    r.set_flags(BANDERAS_ESTADO if estado else BANDERAS_RESPUESTA)
    r.set_colors(stroke=color)
    r.update()
    xref = r.xref
    if estado:
        doc.xref_set_key(xref, "State", pymupdf.get_pdf_str(op.get("estado") or "None"))
        doc.xref_set_key(xref, "StateModel", pymupdf.get_pdf_str("Review"))
    _vaciar_apariencia(doc, xref)
    _poner_rect_crudo(doc, xref, rect_padre)      # en el mismo rectángulo que el original, como Acrobat
    _poner_nm(doc, xref, op.get("nm"))


_OPS = {
    "crear": _crear, "editar": _editar, "mover": _mover, "color": _color, "borrar": _borrar,
    "responder": _responder, "estado": lambda doc, op: _responder(doc, op, estado=True),
}


def aplicar(doc: pymupdf.Document, ops: Sequence[dict]) -> None:
    """Escribe las operaciones en el documento, en orden.  Determinista: sobre el mismo archivo recién
    abierto da siempre el mismo resultado.  Una operación sobre una anotación que ya no existe no hace nada."""
    for op in ops:
        fn = _OPS.get(op.get("op"))
        if fn is None:
            raise UserError(f"Operación de comentarios desconocida: «{op.get('op')}».")
        fn(doc, op)


def paginas_de(ops: Iterable[dict]) -> set[int]:
    """Páginas que tocan estas operaciones."""
    out = set()
    for op in ops:
        p = op.get("pagina", (op.get("ref") or {}).get("pagina"))
        if isinstance(p, int):
            out.add(p)
    return out


def permite_comentar(doc: pymupdf.Document) -> bool:
    """¿El PDF permite agregar comentarios?  (Si no, se avisa una vez y se permite igual.)"""
    try:
        return bool(doc.permissions & pymupdf.PDF_PERM_ANNOTATE)
    except Exception:  # noqa: BLE001
        return True


# ---------------------------------------------------------------- filtros y orden

def _normal(texto: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(ch for ch in t if unicodedata.category(ch) != "Mn")


def texto_busqueda(c: Comentario) -> str:
    partes = [c.autor, c.texto, c.texto_marcado or "", c.nombre_tipo, ESTADOS.get(c.estado, "")]
    for r in c.respuestas:
        if not r.es_estado:
            partes += [r.autor, r.texto]
    return _normal(" ".join(partes))


@dataclass
class Filtro:
    """Qué comentarios mostrar (en el panel y en el resumen)."""
    texto: str = ""
    tipos: set[str] | None = None          # None = todos
    autores: set[str] | None = None
    estados: set[str | None] | None = None
    solo_de: str | None = None             # «Solo los míos»: el nombre de quien usa la aplicación
    sin_responder: bool = False

    def activo(self) -> bool:
        return bool(self.texto.strip() or self.tipos is not None or self.autores is not None
                    or self.estados is not None or self.solo_de or self.sin_responder)

    def cuantos(self) -> int:
        return sum(1 for x in (self.tipos, self.autores, self.estados) if x is not None) \
            + int(bool(self.solo_de)) + int(self.sin_responder)

    def acepta(self, c: Comentario, clave: str | None = None) -> bool:
        if self.tipos is not None and c.tipo not in self.tipos:
            return False
        if self.autores is not None and (c.autor or "") not in self.autores:
            return False
        if self.estados is not None and c.estado not in self.estados:
            return False
        if self.solo_de and _normal(c.autor).strip() != _normal(self.solo_de).strip():
            return False
        if self.sin_responder and c.respondido:
            return False
        q = _normal(self.texto).split()
        if q:
            hay = clave if clave is not None else texto_busqueda(c)
            if not all(t in hay for t in q):
                return False
        return True


ORDENES = {"pagina": "Página", "fecha": "Fecha", "autor": "Autor"}


def ordenar(comentarios: Iterable[Comentario], orden: str = "pagina") -> list[Comentario]:
    lista = list(comentarios)
    if orden == "fecha":
        return sorted(lista, key=lambda c: (_orden_fecha(c)[0], -_orden_fecha(c)[1], c.pagina, c.rect[1]))
    if orden == "autor":
        return sorted(lista, key=lambda c: (_normal(c.autor) or "\uffff", c.pagina, c.rect[1], c.rect[0]))
    return sorted(lista, key=lambda c: (c.pagina, round(c.rect[1], 1), c.rect[0]))


# ---------------------------------------------------------------- resumen

def _filas(comentarios: Sequence[Comentario]) -> list[dict]:
    filas = []
    for n, c in enumerate(comentarios, 1):
        respuestas = [r for r in c.respuestas if not r.es_estado]
        filas.append({
            "n": n, "pagina": c.pagina + 1, "tipo": c.nombre_tipo, "autor": c.autor or "Sin autor",
            "fecha": fecha_larga(c.creado or c.modificado), "estado": ESTADOS.get(c.estado, "Sin estado"),
            "marcado": c.texto_marcado or "", "texto": c.texto or "",
            "respuestas": [f"{r.autor or 'Sin autor'} ({fecha_larga(r.fecha)}): {r.texto}".replace(" ()", "")
                           for r in respuestas],
            "comentario": c,
        })
    return filas


def resumen(doc: pymupdf.Document, filtro: Filtro | None = None) -> list[dict]:
    """Filas del resumen de comentarios, en orden de página: número, página, tipo, autor, fecha, estado,
    texto marcado, texto y respuestas."""
    lista = [c for c in leer(doc, marcado=True) if filtro is None or filtro.acepta(c)]
    return _filas(ordenar(lista, "pagina"))


FORMATOS_RESUMEN = {"pdf": "PDF con miniaturas", "xlsx": "Excel", "docx": "Word", "txt": "Texto"}


class _Hoja:
    """Escritura de un PDF tamaño carta con renglones que se ajustan al ancho y pasan de página.

    El texto va en Helvetica (no se incrusta: el archivo pesa poco y se puede buscar y copiar tal cual).  Un
    renglón con letras fuera del alfabeto latino básico se escribe con el motor HTML de MuPDF, que incrusta
    solo las letras usadas con sus mismas medidas."""
    ANCHO, ALTO, MARGEN = 612.0, 792.0, 48.0

    def __init__(self, doc: pymupdf.Document, pie: str):
        self.doc = doc
        self.pie = pie
        self.fuentes = {"n": pymupdf.Font("helv"), "b": pymupdf.Font("hebo")}
        self.page: pymupdf.Page | None = None
        self.y = 0.0
        self.incrustadas = False

    def nueva(self) -> pymupdf.Page:
        self.page = self.doc.new_page(width=self.ANCHO, height=self.ALTO)
        self.y = self.MARGEN
        return self.page

    @staticmethod
    def nombre(estilo: str) -> str:
        return "hebo" if estilo == "b" else "helv"

    def partir(self, texto: str, ancho: float, tam: float, estilo: str = "n") -> list[str]:
        f = self.fuentes[estilo]
        lineas: list[str] = []
        for parrafo in (texto or "").replace("\r", "").split("\n"):
            palabras = parrafo.split(" ")
            actual = ""
            for w in palabras:
                prueba = f"{actual} {w}" if actual else w
                if f.text_length(prueba, fontsize=tam) <= ancho:
                    actual = prueba
                    continue
                if actual:
                    lineas.append(actual)
                # palabra más larga que el renglón: se corta
                while f.text_length(w, fontsize=tam) > ancho and len(w) > 1:
                    k = len(w)
                    while k > 1 and f.text_length(w[:k], fontsize=tam) > ancho:
                        k -= 1
                    lineas.append(w[:k])
                    w = w[k:]
                actual = w
            lineas.append(actual)
        return lineas

    def escribir(self, x: float, y: float, texto: str, tam: float, estilo: str = "n", color=(0.12, 0.14, 0.16),
                 page: pymupdf.Page | None = None):
        page = page or self.page
        if all(ord(ch) < 256 for ch in texto):
            page.insert_text((x, y), texto, fontsize=tam, fontname=self.nombre(estilo), color=color)
            return
        from html import escape
        rgb = "#%02x%02x%02x" % tuple(round(v * 255) for v in color)
        peso = "bold" if estilo == "b" else "normal"
        html = (f'<div style="font-family: sans-serif; font-size: {tam:.2f}px; font-weight: {peso}; color: {rgb}; '
                f'white-space: pre; margin: 0; line-height: 1.15">{escape(texto)}</div>')
        rect = pymupdf.Rect(x, y - tam * 0.95, self.ANCHO - 4, y + tam * 0.55)
        try:
            page.insert_htmlbox(rect, html, scale_low=0)
            self.incrustadas = True
        except Exception:  # noqa: BLE001 — en el peor caso, con las letras que tenga la Helvetica
            page.insert_text((x, y), texto, fontsize=tam, fontname=self.nombre(estilo), color=color)

    def numerar(self) -> None:
        total = self.doc.page_count
        for i, page in enumerate(self.doc):
            txt = f"{self.pie}  ·  Página {i + 1} de {total}"
            w = self.fuentes["n"].text_length(txt, fontsize=8)
            self.escribir((self.ANCHO - w) / 2, self.ALTO - 24, txt, 8, "n", (0.45, 0.47, 0.5), page=page)


def _marcador(page: pymupdf.Page, centro: pymupdf.Point, numero: int, hoja: _Hoja) -> None:
    r = 7.5 if numero < 100 else 9.5
    page.draw_circle(centro, r, color=(1, 1, 1), fill=(0.84, 0.23, 0.23), width=1.2)
    txt = str(numero)
    f = hoja.fuentes["b"]
    w = f.text_length(txt, fontsize=8)
    page.insert_text((centro.x - w / 2, centro.y + 2.9), txt, fontsize=8, fontname=hoja.nombre("b"), color=(1, 1, 1))


def _resumen_pdf(doc: pymupdf.Document, filas: list[dict], salida: str, nombre: str, filtro: Filtro | None,
                 progress: Progress) -> None:
    out = pymupdf.open()
    hoja = _Hoja(out, f"Resumen de comentarios de «{nombre}»")
    M, W = hoja.MARGEN, hoja.ANCHO
    gris, oscuro, rojo = (0.42, 0.45, 0.5), (0.12, 0.14, 0.16), (0.66, 0.15, 0.15)
    # ---- portada
    page = hoja.nueva()
    hoja.escribir(M, M + 20, "Resumen de comentarios", 22, "b", oscuro)
    y = M + 50
    for ln in hoja.partir(nombre, W - 2 * M, 12, "n"):
        hoja.escribir(M, y, ln, 12, "n", oscuro)
        y += 16
    ahora = _dt.datetime.now()
    hoja.escribir(M, y + 4, f"Generado el {ahora.day:02d}/{ahora.month:02d}/{ahora.year} a las {hora_texto(ahora)}",
                  10, "n", gris)
    y += 24
    paginas = sorted({f["pagina"] for f in filas})
    total = len(filas)
    hoja.escribir(M, y + 18, f"{total} comentario{'s' if total != 1 else ''} en {len(paginas)} "
                  f"página{'s' if len(paginas) != 1 else ''} del documento", 13, "b", oscuro)
    y += 30
    if filtro is not None and filtro.activo():
        hoja.escribir(M, y + 12, "Solo se incluyen los comentarios que coinciden con la búsqueda y los filtros "
                      "elegidos.",
                      9.5, "n", gris)
        y += 18
    y += 14

    def tabla(titulo: str, conteo: dict[str, int], y0: float) -> float:
        hoja.escribir(M, y0, titulo, 11.5, "b", oscuro)
        y0 += 8
        for etiqueta, n in conteo.items():
            y0 += 17
            page.draw_line((M, y0 + 5), (W - M, y0 + 5), color=(0.9, 0.91, 0.93), width=0.6)
            hoja.escribir(M + 4, y0, etiqueta, 10.5, "n", oscuro)
            t = str(n)
            hoja.escribir(W - M - 4 - hoja.fuentes["b"].text_length(t, fontsize=10.5), y0, t, 10.5, "b", oscuro)
        return y0 + 26

    por_tipo: dict[str, int] = {}
    por_estado: dict[str, int] = {}
    por_autor: dict[str, int] = {}
    for f in filas:
        por_tipo[f["tipo"]] = por_tipo.get(f["tipo"], 0) + 1
        por_estado[f["estado"]] = por_estado.get(f["estado"], 0) + 1
        por_autor[f["autor"]] = por_autor.get(f["autor"], 0) + 1
    y = tabla("Por tipo", dict(sorted(por_tipo.items(), key=lambda kv: -kv[1])), y + 10)
    y = tabla("Por estado", {ESTADOS[k]: por_estado[ESTADOS[k]] for k in ESTADOS_LISTA if ESTADOS[k] in por_estado}, y)
    autores = dict(sorted(por_autor.items(), key=lambda kv: -kv[1]))
    if y + 30 + 17 * min(len(autores), 12) < hoja.ALTO - 60:
        tabla("Por autor", dict(list(autores.items())[:12]), y)

    # ---- una sección por página del documento
    TW, TH = 200.0, 270.0                          # caja de la miniatura
    xl = M + TW + 18                               # columna de la lista
    for k, pno1 in enumerate(paginas):
        core._report(progress, k, len(paginas), f"Resumen: página {pno1} del documento")
        delas = [f for f in filas if f["pagina"] == pno1]
        src = doc[pno1 - 1]
        esc = min(TW / src.rect.width, TH / src.rect.height)
        tw, th = src.rect.width * esc, src.rect.height * esc
        if hoja.page is None or hoja.page.number == 0 or hoja.y + th + 40 > hoja.ALTO - 50:
            page = hoja.nueva()
        else:
            page = hoja.page
            hoja.y += 14
        y0 = hoja.y
        hoja.escribir(M, y0 + 14, f"Página {pno1}", 14, "b", oscuro)
        cnt = len(delas)
        hoja.escribir(M + 80, y0 + 14, f"{cnt} comentario{'s' if cnt != 1 else ''}", 10, "n", gris)
        top = y0 + 26
        caja = pymupdf.Rect(M, top, M + tw, top + th)
        pagina_mini = page.number
        # A 1,5× (unos 110 ppp en la hoja) y en el formato que pese menos: PNG para las páginas de texto, JPEG
        # para los escaneos y las fotos.  A 2,2× y siempre sin pérdida, el resumen de un documento largo pesaba
        # más de 17 MB y no cabía en un correo.
        pix = src.get_pixmap(matrix=pymupdf.Matrix(esc * 1.5, esc * 1.5), annots=True, alpha=False)
        page.insert_image(caja, stream=min(pix.tobytes("png"), pix.tobytes("jpg", jpg_quality=75), key=len))
        page.draw_rect(caja, color=(0.8, 0.82, 0.85), width=0.8)
        for f in delas:
            r = pymupdf.Rect(f["comentario"].rect)
            centro = pymupdf.Point(caja.x0 + r.x0 * esc, caja.y0 + r.y0 * esc)
            centro.x = min(max(centro.x, caja.x0 + 8), caja.x1 - 8)
            centro.y = min(max(centro.y, caja.y0 + 8), caja.y1 - 8)
            _marcador(page, centro, f["n"], hoja)
        # lista numerada a la derecha de la miniatura (y debajo, a todo el ancho, si no cabe)
        y = top + 4
        x, ancho = xl, W - M - xl
        fondo_mini = caja.y1

        def renglon(texto: str, tam: float, estilo: str, color, sangria: float = 0.0, despues: float = 0.0):
            nonlocal y, x, ancho, page
            for ln in hoja.partir(texto, ancho - sangria, tam, estilo):
                if y + tam > hoja.ALTO - 50:
                    page = hoja.nueva()
                    hoja.escribir(M, hoja.y + 12, f"Página {pno1} (continuación)", 11, "b", gris)
                    y, x, ancho = hoja.y + 32, M, W - 2 * M
                elif x == xl and y > fondo_mini + 4:
                    x, ancho = M, W - 2 * M         # ya pasó la miniatura: se usa todo el ancho
                hoja.escribir(x + sangria, y + tam, ln, tam, estilo, color)
                y += tam + 3.2
            y += despues

        for f in delas:
            c = f["comentario"]
            cab = f"{f['n']}.  {f['tipo']}  ·  {f['autor']}"
            renglon(cab, 10.5, "b", oscuro)
            meta = "  ·  ".join(t for t in (f["fecha"], f["estado"] if c.estado else "") if t)
            if meta:
                renglon(meta, 8.8, "n", gris if not c.estado else rojo)
            if f["marcado"]:
                renglon(f"«{f['marcado']}»", 9.5, "n", (0.3, 0.33, 0.38), 6)
            if f["texto"] and f["texto"] != f["marcado"]:
                renglon(f["texto"], 10, "n", oscuro, 6)
            for resp in f["respuestas"]:
                renglon(f"» {resp}", 9.3, "n", (0.25, 0.28, 0.33), 14)
            y += 7
        hoja.y = (max(y, fondo_mini) if page.number == pagina_mini else y) + 6
    hoja.numerar()
    core._report(progress, len(paginas), len(paginas), "Guardando el resumen…")
    if hoja.incrustadas:
        try:
            out.subset_fonts()           # solo las letras usadas de las fuentes incrustadas
        except Exception:  # noqa: BLE001 — sin el subconjunto el archivo solo pesa más
            pass
    with core._output(salida) as tmp:
        out.save(tmp, garbage=3, deflate=True)
    out.close()


def _resumen_docx(filas: list[dict], salida: str, nombre: str) -> None:
    from . import oficina
    P = oficina.Parrafo
    ahora = _dt.datetime.now()
    pars = [P("Resumen de comentarios", "titulo1"), P(nombre),
            P(f"Generado el {ahora.day:02d}/{ahora.month:02d}/{ahora.year} a las {hora_texto(ahora)} · "
              f"{len(filas)} comentario{'s' if len(filas) != 1 else ''}", cursiva=True)]
    actual = None
    for f in filas:
        if f["pagina"] != actual:
            actual = f["pagina"]
            pars.append(P(f"Página {actual}", "titulo2"))
        meta = " · ".join(t for t in (f["fecha"], f["estado"]) if t)
        pars.append(P(tramos=[(f"{f['n']}. {f['tipo']} · {f['autor']}", True, False), (f"   {meta}", False, False)]))
        if f["marcado"]:
            pars.append(P(f"«{f['marcado']}»", cursiva=True))
        if f["texto"] and f["texto"] != f["marcado"]:
            pars.append(P(f["texto"]))
        for r in f["respuestas"]:
            pars.append(P(f"↳ {r}"))
    oficina.escribir_docx(pars, salida)


def _resumen_xlsx(filas: list[dict], salida: str) -> None:
    from . import oficina
    tabla: list[list[object]] = [["N.º", "Página", "Tipo", "Autor", "Fecha", "Estado", "Texto marcado",
                                  "Comentario", "Respuestas"]]
    for f in filas:
        tabla.append([f["n"], f["pagina"], f["tipo"], f["autor"], f["fecha"], f["estado"], f["marcado"],
                      f["texto"], " | ".join(f["respuestas"])])
    oficina.escribir_xlsx([("Comentarios", tabla)], salida)


def _resumen_txt(filas: list[dict], salida: str, nombre: str) -> None:
    ahora = _dt.datetime.now()
    out = [f"RESUMEN DE COMENTARIOS — {nombre}",
           f"Generado el {ahora.day:02d}/{ahora.month:02d}/{ahora.year} a las {hora_texto(ahora)}",
           f"{len(filas)} comentario{'s' if len(filas) != 1 else ''}", ""]
    actual = None
    for f in filas:
        if f["pagina"] != actual:
            actual = f["pagina"]
            out += ["", f"== Página {actual} =="]
        meta = " · ".join(t for t in (f["fecha"], f["estado"]) if t)
        out.append(f"{f['n']}. {f['tipo']} · {f['autor']} · {meta}")
        if f["marcado"]:
            out.append(f"   «{f['marcado']}»")
        if f["texto"] and f["texto"] != f["marcado"]:
            out += [f"   {ln}" for ln in f["texto"].splitlines()]
        out += [f"      ↳ {r}" for r in f["respuestas"]]
    with core._output(salida) as tmp:
        Path(tmp).write_text("\n".join(out) + "\n", encoding="utf-8-sig")


def exportar_resumen(path: str | Path, salida: str | Path, formato: str = "pdf", filtro: Filtro | None = None,
                     password: str | None = None, progress: Progress = None, ops: Sequence[dict] | None = None) -> int:
    """Guarda el resumen de comentarios en ``salida`` («pdf», «xlsx», «docx» o «txt»).  Con ``ops`` incluye
    también los comentarios que aún no se han guardado en el archivo.  Devuelve cuántos comentarios entraron."""
    formato = (formato or "pdf").lower().lstrip(".")
    if formato not in FORMATOS_RESUMEN:
        raise UserError(f"No se puede hacer un resumen en formato «{formato}».")
    if os.path.normcase(os.path.abspath(str(salida))) == os.path.normcase(os.path.abspath(str(path))):
        raise UserError("Elige otro nombre para el resumen: no puede reemplazar el PDF original.")
    nombre = Path(path).name
    core._report(progress, 0, 1, "Leyendo los comentarios…")
    with core.open_pdf(path, password) as doc:
        if ops:
            aplicar(doc, ops)
        filas = resumen(doc, filtro)
        if not filas:
            raise UserError("No hay comentarios para el resumen." if filtro is None or not filtro.activo()
                            else "Ningún comentario coincide con la búsqueda o los filtros elegidos.")
        if formato == "pdf":
            _resumen_pdf(doc, filas, str(salida), nombre, filtro, progress)
    if formato == "docx":
        _resumen_docx(filas, str(salida), nombre)
    elif formato == "xlsx":
        _resumen_xlsx(filas, str(salida))
    elif formato == "txt":
        _resumen_txt(filas, str(salida), nombre)
    return len(filas)


# ---------------------------------------------------------------- aplanar

def aplanar(path: str | Path, output: str | Path, password: str | None = None, progress: Progress = None,
            ops: Sequence[dict] | None = None) -> int:
    """Deja los comentarios como parte fija de las páginas (ya no se pueden mover ni borrar) en un archivo
    nuevo.  Conserva la contraseña.  Devuelve cuántos comentarios se aplanaron."""
    if os.path.normcase(os.path.abspath(str(output))) == os.path.normcase(os.path.abspath(str(path))):
        raise UserError("Aplanar crea siempre una copia: elige otro nombre para no reemplazar el original.")
    core._report(progress, 0, 2, "Abriendo el documento…")
    with core._output(output) as tmp, core.open_pdf(path, password) as doc:
        if ops:
            aplicar(doc, ops)
        n = len(leer(doc))
        if not n:
            raise UserError("Este PDF no tiene comentarios para aplanar.")
        core._report(progress, 1, 2, "Aplanando los comentarios…")
        doc.bake(annots=True, widgets=False)
        core._report(progress, 2, 2, "Guardando…")
        core._save_keeping_password(doc, tmp, password)
    return n


# ---------------------------------------------------------------- sellar varias páginas («Acciones por lotes»)

POSICIONES_SELLO = {"arriba-derecha": "Arriba a la derecha", "arriba-izquierda": "Arriba a la izquierda",
                    "abajo-derecha": "Abajo a la derecha", "abajo-izquierda": "Abajo a la izquierda",
                    "centro": "En el centro"}
PAGINAS_SELLO = {"primera": "Solo en la primera página", "todas": "En todas las páginas",
                 "ultima": "Solo en la última página"}
MARGEN_SELLO = 28.0     # puntos (1 cm) desde el borde de la página


def rect_sello(ancho_pag: float, alto_pag: float, w: float, h: float, posicion: str,
               margen: float = MARGEN_SELLO) -> tuple[float, float, float, float]:
    """Dónde va un sello de ``w`` × ``h`` en una página de ese tamaño (tal como se ve).  Si no cabe, se achica."""
    f = min(1.0, (ancho_pag * 0.9) / max(w, 1e-6), (alto_pag * 0.45) / max(h, 1e-6))
    w, h = w * f, h * f
    m = min(margen, max(0.0, (ancho_pag - w) / 2), max(0.0, (alto_pag - h) / 2))
    x0 = m if posicion.endswith("izquierda") else (ancho_pag - w) / 2 if posicion == "centro" else ancho_pag - m - w
    y0 = m if posicion.startswith("arriba") else (alto_pag - h) / 2 if posicion == "centro" else alto_pag - m - h
    return round(x0, 2), round(y0, 2), round(x0 + w, 2), round(y0 + h, 2)


def sellar(path: str | Path, output: str | Path, png: bytes, ancho: float, alto: float, autor: str = "",
           texto: str = "", color: str = "#1E4FA0", asunto: str = "Sello", paginas: str = "primera",
           posicion: str = "arriba-derecha", password: str | None = None, progress: Progress = None) -> int:
    """Pone el mismo sello, como anotación estándar (se ve, se mueve y se borra en cualquier lector), en las
    páginas elegidas de una copia.  Conserva la contraseña.  Devuelve en cuántas páginas quedó."""
    if os.path.normcase(os.path.abspath(str(output))) == os.path.normcase(os.path.abspath(str(path))):
        raise UserError("Sellar crea siempre una copia: elige otro nombre para no reemplazar el original.")
    b64 = base64.b64encode(png).decode("ascii")
    fecha = ahora_pdf()
    with core._output(output) as tmp, core.open_pdf(path, password) as doc:
        n = doc.page_count
        indices = list(range(n)) if paginas == "todas" else [n - 1] if paginas == "ultima" else [0]
        for k, i in enumerate(indices):
            core._report(progress, k, len(indices), f"Sellando la página {i + 1} de {n}")
            r = doc[i].rect
            aplicar(doc, [{"op": "crear", "tipo": "sello", "pagina": i, "nm": nuevo_nm(), "autor": autor,
                           "texto": texto, "fecha": fecha, "color": color, "asunto": asunto, "png": b64,
                           "rect": rect_sello(r.width, r.height, ancho, alto, posicion)}])
        core._report(progress, len(indices), len(indices), "Guardando…")
        core._save_keeping_password(doc, tmp, password)
    return len(indices)
