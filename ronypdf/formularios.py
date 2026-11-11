"""Formularios PDF (AcroForm) sin interfaz: leer, rellenar, vaciar, aplanar y pasar datos.

Las funciones públicas siguen el patrón de ``core``: trabajan con rutas, aceptan ``password`` y
``progress``, escriben de forma atómica y conservan la contraseña del original.

Un campo se identifica con ``Campo.id`` = «página:xref» (el xref del widget). Los widgets que
comparten nombre son el mismo campo para el PDF: al rellenar se sincronizan.
"""

from __future__ import annotations

import csv
import io
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import pymupdf

from . import core

TIPOS = {
    pymupdf.PDF_WIDGET_TYPE_TEXT: "texto",
    pymupdf.PDF_WIDGET_TYPE_CHECKBOX: "casilla",
    pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON: "opcion",
    pymupdf.PDF_WIDGET_TYPE_LISTBOX: "lista",
    pymupdf.PDF_WIDGET_TYPE_COMBOBOX: "desplegable",
    pymupdf.PDF_WIDGET_TYPE_SIGNATURE: "firma",
    pymupdf.PDF_WIDGET_TYPE_BUTTON: "boton",
}
NOMBRE_TIPO = {"texto": "Texto", "casilla": "Casilla", "opcion": "Opción", "lista": "Lista",
               "desplegable": "Desplegable", "firma": "Firma", "boton": "Botón"}
RELLENABLES = ("texto", "casilla", "opcion", "lista", "desplegable")

# Banderas de campo (PDF 1.7, tabla 221 y siguientes)
_FF_SOLO_LECTURA = 1
_FF_OBLIGATORIO = 2
_FF_MULTILINEA = 4096
_FF_CLAVE = 8192
_FF_EDITABLE = 262144
_FF_MULTIPLE = 2097152

SI = {"si", "sí", "s", "x", "true", "1", "yes", "y", "on", "verdadero", "marcado", "marcada", "✓"}


@dataclass
class Campo:
    id: str                                        # «página:xref»
    nombre: str
    etiqueta: str                                  # texto de ayuda del campo (/TU) o ''
    tipo: str                                      # texto | casilla | opcion | lista | desplegable | firma | boton
    pagina: int                                    # base 0
    rect: tuple[float, float, float, float]        # coordenadas visibles (con la rotación aplicada)
    valor: object = ""                             # str | bool | list[str]
    opciones: list[str] = field(default_factory=list)           # lo que se ve
    multilinea: bool = False
    solo_lectura: bool = False
    obligatorio: bool = False
    max_largo: int = 0                             # 0 = sin límite
    clave: bool = False                            # campo de contraseña (se ve con puntos)
    editable: bool = False                         # desplegable que admite escribir
    tamano_fuente: float = 0                       # 0 = automático
    valores_opciones: list[str] = field(default_factory=list)   # lo que se guarda (mismo orden)
    estado: str = ""                               # casilla y opción: nombre del estado «marcado»
    multiple: bool = False                         # lista de selección múltiple
    firmado: bool = False                          # campo de firma digital ya firmado

    @property
    def titulo(self) -> str:
        return self.etiqueta or self.nombre

    def texto_de(self, valor: object) -> str:
        """Texto que se ve para un valor guardado (desplegables y listas con pares valor/texto)."""
        if self.valores_opciones and valor in self.valores_opciones:
            return self.opciones[self.valores_opciones.index(valor)]
        return "" if valor is None else str(valor)


@dataclass
class InfoFormulario:
    campos: int            # número de campos (como lo cuenta el PDF)
    xfa: bool              # formulario con tecnología XFA de Adobe
    firmado: bool          # tiene una firma digital previa
    calculos: bool         # tiene cálculos automáticos (/CO)


# --------------------------------------------------------------------------
# Utilidades de bajo nivel
# --------------------------------------------------------------------------

def _clave(doc: pymupdf.Document, xref: int, key: str) -> tuple[str, str]:
    try:
        return doc.xref_get_key(xref, key)
    except Exception:  # noqa: BLE001
        return ("null", "null")


def _padre(doc: pymupdf.Document, xref: int) -> int | None:
    t, v = _clave(doc, xref, "Parent")
    if t == "xref":
        try:
            return int(v.split()[0])
        except (ValueError, IndexError):
            return None
    return None


def _cadena_campo(doc: pymupdf.Document, xref: int) -> list[int]:
    """El widget y sus padres, del más cercano al más lejano."""
    out, x = [], xref
    while x is not None and x not in out and len(out) < 32:
        out.append(x)
        x = _padre(doc, x)
    return out


def _heredado(doc: pymupdf.Document, xref: int, key: str) -> tuple[str, str]:
    for x in _cadena_campo(doc, xref):
        t, v = _clave(doc, x, key)
        if t != "null":
            return t, v
    return ("null", "null")


def _terminal(doc: pymupdf.Document, xref: int) -> int:
    """Objeto que guarda el valor del campo: el primero de la cadena que tiene nombre (/T)."""
    for x in _cadena_campo(doc, xref):
        if _clave(doc, x, "T")[0] != "null":
            return x
    return xref


_ESCAPES = {"n": b"\n", "r": b"\r", "t": b"\t", "b": b"\b", "f": b"\f", "(": b"(", ")": b")", "\\": b"\\"}


def _decodificar(raw: bytes) -> str:
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be", errors="replace")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", errors="replace")
    return raw.decode("cp1252", errors="replace")


def _cadenas_pdf(texto: str) -> list[str]:
    """Cadenas literales «(…)» y hexadecimales «<…>» de un objeto PDF en texto (p. ej. un arreglo)."""
    out: list[str] = []
    i, n = 0, len(texto)
    while i < n:
        c = texto[i]
        if c == "(":
            depth, buf, i = 1, bytearray(), i + 1
            while i < n and depth:
                ch = texto[i]
                if ch == "\\" and i + 1 < n:
                    nxt = texto[i + 1]
                    if nxt in _ESCAPES:
                        buf += _ESCAPES[nxt]
                        i += 2
                    elif nxt.isdigit():
                        j = i + 1
                        while j < n and j < i + 4 and texto[j] in "01234567":
                            j += 1
                        buf.append(int(texto[i + 1:j], 8) & 255)
                        i = j
                    else:
                        i += 2
                    continue
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if not depth:
                        i += 1
                        break
                buf += ch.encode("latin-1", errors="replace")
                i += 1
            out.append(_decodificar(bytes(buf)))
        elif c == "<" and not texto.startswith("<<", i):
            j = texto.find(">", i)
            if j < 0:
                break
            hexa = "".join(texto[i + 1:j].split())
            if len(hexa) % 2:
                hexa += "0"
            try:
                out.append(_decodificar(bytes.fromhex(hexa)))
            except ValueError:
                pass
            i = j + 1
        else:
            i += 1
    return out


def _texto_ap(s: str) -> bytes:
    """Cadena literal para un flujo de contenido con la fuente Helvetica en WinAnsi."""
    raw = s.encode("cp1252", errors="replace")
    out = bytearray(b"(")
    for b in raw:
        if b in (0x28, 0x29, 0x5C):
            out += b"\\" + bytes([b])
        elif b < 32 or b > 126:
            out += b"\\%03o" % b
        else:
            out.append(b)
    out += b")"
    return bytes(out)


def _num(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".") or "0"


def _rotacion_mk(doc: pymupdf.Document, xref: int) -> int:
    t, v = _clave(doc, xref, "MK/R")
    try:
        return int(float(v)) % 360 if t != "null" else 0
    except ValueError:
        return 0


# --------------------------------------------------------------------------
# Lectura
# --------------------------------------------------------------------------

def _valor_multiple(doc: pymupdf.Document, xref: int) -> list[str]:
    t, v = _heredado(doc, xref, "V")
    if t == "array":
        return _cadenas_pdf(v)
    if t == "string":
        return _cadenas_pdf(v) or [v]
    return []


def _orden(doc: pymupdf.Document, page: pymupdf.Page, campos: list[Campo]) -> list[Campo]:
    """Orden de tabulación: el de la página (/Tabs) o, si no dice nada, el de las anotaciones."""
    tabs = _clave(doc, page.xref, "Tabs")[1]
    if tabs == "/R":
        return sorted(campos, key=lambda c: (round(c.rect[1] / 4), c.rect[0]))
    if tabs == "/C":
        return sorted(campos, key=lambda c: (round(c.rect[0] / 4), c.rect[1]))
    return campos


def _campo(doc: pymupdf.Document, page: pymupdf.Page, w: pymupdf.Widget, k: int) -> Campo | None:
    tipo = TIPOS.get(w.field_type)
    if tipo is None or w.field_display in (1, 3):      # tipo desconocido u oculto
        return None
    ff = int(w.field_flags or 0)
    nombre = (w.field_name or "").strip() or f"Campo {k}"
    c = Campo(id=f"{page.number}:{w.xref}", nombre=nombre, etiqueta=(w.field_label or "").strip(),
              tipo=tipo, pagina=page.number, rect=tuple(core._visual(page, w.rect)),
              solo_lectura=bool(ff & _FF_SOLO_LECTURA), obligatorio=bool(ff & _FF_OBLIGATORIO),
              tamano_fuente=float(w.text_fontsize or 0))
    if tipo == "texto":
        c.valor = "" if w.field_value in (None, False) else str(w.field_value)
        c.multilinea = bool(ff & _FF_MULTILINEA)
        c.clave = bool(ff & _FF_CLAVE)
        c.max_largo = max(0, int(w.text_maxlen or 0))
    elif tipo in ("casilla", "opcion"):
        try:
            c.estado = w.on_state() or "Yes"
        except Exception:  # noqa: BLE001
            c.estado = "Yes"
        if c.estado is True:
            c.estado = "Yes"
        v = w.field_value
        c.valor = bool(v) if isinstance(v, bool) else (v not in (None, "", "Off") and str(v) == str(c.estado))
    elif tipo in ("lista", "desplegable"):
        for op in w.choice_values or []:
            if isinstance(op, (list, tuple)) and len(op) >= 2:
                c.valores_opciones.append(str(op[0]))
                c.opciones.append(str(op[1]))
            else:
                c.valores_opciones.append(str(op))
                c.opciones.append(str(op))
        if c.valores_opciones == c.opciones:
            c.valores_opciones = list(c.opciones)
        if tipo == "lista" and ff & _FF_MULTIPLE:
            c.multiple = True
            c.valor = _valor_multiple(doc, w.xref)
        else:
            c.valor = "" if w.field_value in (None, False) else str(w.field_value)
        c.editable = tipo == "desplegable" and bool(ff & _FF_EDITABLE)
    elif tipo == "firma":
        try:
            c.firmado = bool(w.is_signed)
        except Exception:  # noqa: BLE001
            c.firmado = False
        c.solo_lectura = c.solo_lectura or c.firmado
    return c


def _leer(doc: pymupdf.Document) -> list[Campo]:
    out: list[Campo] = []
    if not doc.is_pdf or not doc.is_form_pdf:
        return out
    k = 0
    for page in doc:
        campos = []
        for w in page.widgets():
            k += 1
            c = _campo(doc, page, w, k)
            if c is not None:
                campos.append(c)
        out.extend(_orden(doc, page, campos))
    return out


def _firma_en_campos(doc: pymupdf.Document, campos: tuple[str, str], visto: set | None = None) -> bool:
    """¿Algún campo de firma (aunque no esté en ninguna página) tiene una firma (/V)?"""
    visto = set() if visto is None else visto
    t, v = campos
    if t != "array":
        return False
    for ref in v.strip("[] \n").split(" R"):
        try:
            x = int(ref.split()[0])
        except (ValueError, IndexError):
            continue
        if x in visto or len(visto) > 5000:
            continue
        visto.add(x)
        if _clave(doc, x, "FT")[1] == "/Sig" and _clave(doc, x, "V")[0] != "null":
            return True
        if _firma_en_campos(doc, _clave(doc, x, "Kids"), visto):
            return True
    return False


def _info(doc: pymupdf.Document) -> InfoFormulario:
    n = int(doc.is_form_pdf or 0) if doc.is_pdf else 0
    if not doc.is_pdf:
        return InfoFormulario(0, False, False, False)
    cat = doc.pdf_catalog()
    xfa = _clave(doc, cat, "AcroForm/XFA")[0] != "null"
    t, co = _clave(doc, cat, "AcroForm/CO")
    calculos = t == "array" and co.strip("[] \n") != ""
    firmado = False
    try:
        flags = doc.get_sigflags()
    except Exception:  # noqa: BLE001
        flags = -1
    if flags == 3:
        # PyMuPDF marca SigFlags 3 al crear un campo de firma vacío: se mira si alguno está firmado.
        for page in doc:
            for w in page.widgets(types=[pymupdf.PDF_WIDGET_TYPE_SIGNATURE]):
                firmado = firmado or bool(w.is_signed)
        firmado = firmado or _firma_en_campos(doc, _clave(doc, cat, "AcroForm/Fields"))
    return InfoFormulario(campos=n, xfa=xfa, firmado=firmado, calculos=calculos)


def info_formulario(path, password=None) -> InfoFormulario:
    with core.open_pdf(path, password) as doc:
        return _info(doc)


def leer_campos(path, password=None) -> list[Campo]:
    """Campos visibles en orden de documento (página por página, en orden de tabulación)."""
    with core.open_pdf(path, password) as doc:
        return _leer(doc)


# --------------------------------------------------------------------------
# Escritura de valores
# --------------------------------------------------------------------------

def _valor_grupo(campos: list[Campo]) -> object:
    """Valor de un grupo de widgets con el mismo nombre (en casillas/opciones: el estado marcado)."""
    c = campos[0]
    if c.tipo in ("casilla", "opcion"):
        return next((x.estado for x in campos if x.valor), "Off")
    return c.valor


def _deseados(campos: list[Campo], valores: dict[str, object]) -> dict[str, object]:
    """Valor pedido por nombre de campo a partir de los valores por id."""
    por_id = {c.id: c for c in campos}
    marcado: dict[str, str] = {}
    apagado: set[str] = set()
    out: dict[str, object] = {}
    for cid, v in valores.items():
        c = por_id.get(cid)
        if c is None or c.solo_lectura or c.tipo not in RELLENABLES:
            continue
        if c.tipo in ("casilla", "opcion"):
            if bool(v):
                marcado.setdefault(c.nombre, c.estado)
            else:
                apagado.add(c.nombre)
        elif c.tipo == "lista" and c.multiple:
            vals = [str(x) for x in v] if isinstance(v, (list, tuple)) else ([str(v)] if v not in (None, "") else [])
            out[c.nombre] = vals
        else:
            texto = "" if v is None else str(v)
            if c.tipo == "texto" and c.max_largo:
                texto = texto[:c.max_largo]
            out[c.nombre] = texto
    for nombre in apagado:
        out[nombre] = "Off"
    out.update(marcado)
    return out


def _fuente_ap(doc: pymupdf.Document, cache: dict) -> int:
    if "helv" not in cache:
        x = doc.get_new_xref()
        doc.update_object(x, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
        cache["helv"] = x
    return cache["helv"]


def _apariencia_lista(doc: pymupdf.Document, w: pymupdf.Widget, c: Campo, elegidos: list[str], cache: dict) -> None:
    """MuPDF deja vacía la apariencia de las listas: se dibuja una con las opciones y la elegida resaltada."""
    r = w.rect
    rot = _rotacion_mk(doc, w.xref)
    bw, bh = (r.height, r.width) if rot in (90, 270) else (r.width, r.height)
    if bw <= 2 or bh <= 2:
        return
    fs = c.tamano_fuente or max(5.0, min(10.0, bh - 3))
    lh = fs * 1.16
    seleccion = [i for i, v in enumerate(c.valores_opciones) if v in elegidos]
    filas = max(1, int((bh - 2) // lh))
    top = 0
    if seleccion and seleccion[0] >= filas:
        top = min(seleccion[0], max(0, len(c.opciones) - filas))
    color = w.text_color or (0, 0, 0)
    if isinstance(color, (int, float)):
        color = (color, color, color)
    if len(color) == 1:
        color = (color[0],) * 3
    elif len(color) == 4:            # CMYK aproximado
        cc, m, y, k = color
        color = ((1 - cc) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
    rgb = " ".join(_num(float(x)) for x in color[:3])
    parts = [b"/Tx BMC q"]
    fill = w.fill_color
    if fill and len(fill) == 3:
        parts.append(f"{' '.join(_num(x) for x in fill)} rg 0 0 {_num(bw)} {_num(bh)} re f".encode())
    parts.append(f"1 1 {_num(bw - 2)} {_num(bh - 2)} re W n".encode())
    for k in seleccion:
        if top <= k < top + filas + 1:
            y = bh - 1 - (k - top + 1) * lh
            parts.append(f"0.6 0.756 0.855 rg 1 {_num(y)} {_num(bw - 2)} {_num(lh)} re f".encode())
    parts.append(f"BT /Helv {_num(fs)} Tf {rgb} rg".encode())
    for k in range(top, min(len(c.opciones), top + filas + 1)):
        y = bh - 1 - (k - top + 1) * lh + (lh - fs) / 2 + fs * 0.22
        parts.append(f"1 0 0 1 2 {_num(y)} Tm ".encode() + _texto_ap(c.opciones[k]) + b" Tj")
    parts.append(b"ET Q EMC")
    matrices = {0: "1 0 0 1 0 0", 90: "0 1 -1 0 0 0", 180: "-1 0 0 -1 0 0", 270: "0 -1 1 0 0 0"}
    matriz = matrices.get(rot, matrices[0])
    ap = doc.get_new_xref()
    doc.update_object(ap, f"<< /Type /XObject /Subtype /Form /BBox [0 0 {_num(bw)} {_num(bh)}] /Matrix [{matriz}] "
                          f"/Resources << /Font << /Helv {_fuente_ap(doc, cache)} 0 R >> >> >>")
    doc.update_stream(ap, b"\n".join(parts))
    doc.xref_set_key(w.xref, "AP", f"<< /N {ap} 0 R >>")
    if top:
        doc.xref_set_key(_terminal(doc, w.xref), "TI", str(top))


def _aplicar_valores(doc: pymupdf.Document, deseado: dict[str, object], progress=None) -> int:
    """Escribe los valores pedidos (por nombre) en todos los widgets. Devuelve los campos cambiados."""
    campos = _leer(doc)
    grupos: dict[str, list[Campo]] = defaultdict(list)
    for c in campos:
        grupos[c.nombre].append(c)
    cambios: dict[str, object] = {}
    for nombre, v in deseado.items():
        grupo = [c for c in grupos.get(nombre, []) if c.tipo in RELLENABLES and not c.solo_lectura]
        if not grupo:
            continue
        actual = _valor_grupo(grupo)
        if grupo[0].tipo == "lista" and grupo[0].multiple:
            if sorted(actual or []) != sorted(v or []):
                cambios[nombre] = v
        elif actual != v:
            cambios[nombre] = v
    if not cambios:
        return 0
    por_id = {c.id: c for c in campos}
    pendientes_ap: list[tuple[int, int, str]] = []      # (página, xref, nombre) que necesitan apariencia propia
    paginas = sorted({c.pagina for c in campos if c.nombre in cambios})
    for k, pno in enumerate(paginas):
        core._report(progress, k, len(paginas), f"Rellenando campos de la página {pno + 1}")
        page = doc[pno]
        widgets = [w for w in page.widgets() if f"{pno}:{w.xref}" in por_id
                   and por_id[f"{pno}:{w.xref}"].nombre in cambios]
        # casillas y opciones: primero se apagan y luego se marca la elegida (comparten /V con su padre)
        for paso in (0, 1):
            for w in widgets:
                c = por_id[f"{pno}:{w.xref}"]
                v = cambios[c.nombre]
                if c.solo_lectura:
                    continue
                if c.tipo in ("casilla", "opcion"):
                    marcar = v != "Off" and c.estado == v
                    if paso == 0 and not marcar and c.valor:
                        w.field_value = False
                        w.update()
                        if c.tipo == "casilla":     # PyMuPDF solo apaga el widget, no el campo padre
                            doc.xref_set_key(_terminal(doc, w.xref), "V", "/Off")
                    elif paso == 1 and marcar:
                        w.field_value = c.estado if c.tipo == "opcion" else True
                        w.update()
                elif paso == 0:
                    if c.tipo in ("texto", "desplegable"):
                        if v == "":
                            # PyMuPDF no escribe valores vacíos: se borra el valor y se redibuja.
                            doc.xref_set_key(_terminal(doc, w.xref), "V", "()")
                        w.field_value = str(v)
                        w.update()
                        if c.tipo == "desplegable" and c.valores_opciones != c.opciones \
                                and v in c.valores_opciones:
                            pendientes_ap.append((pno, w.xref, c.nombre))
                    elif c.tipo == "lista":
                        elegidos = list(v) if isinstance(v, list) else ([v] if v else [])
                        term = _terminal(doc, w.xref)
                        if c.multiple:
                            if elegidos:
                                arreglo = " ".join(pymupdf.get_pdf_str(x) for x in elegidos)
                                doc.xref_set_key(term, "V", f"[{arreglo}]")
                            else:
                                doc.xref_set_key(term, "V", "null")
                        elif elegidos:
                            w.field_value = elegidos[0]
                            w.update()
                        else:
                            doc.xref_set_key(term, "V", "null")
                        idx = [str(i) for i, x in enumerate(c.valores_opciones) if x in elegidos]
                        doc.xref_set_key(term, "I", f"[{' '.join(idx)}]" if idx else "null")
                        pendientes_ap.append((pno, w.xref, c.nombre))
    # Apariencias propias al final: así ninguna actualización de MuPDF las vuelve a reemplazar.
    cache: dict = {}
    valores_export: dict[int, str] = {}
    for pno, xref, nombre in pendientes_ap:
        page = doc[pno]
        w = next((x for x in page.widgets() if x.xref == xref), None)
        if w is None:
            continue
        c = por_id[f"{pno}:{xref}"]
        v = cambios[nombre]
        if c.tipo == "desplegable":
            # MuPDF dibuja el valor guardado; el PDF debe mostrar el texto: se dibuja y se restaura el valor.
            w.field_value = c.texto_de(v)
            w.update()
            valores_export[_terminal(doc, xref)] = v
        else:
            elegidos = list(v) if isinstance(v, list) else ([v] if v else [])
            _apariencia_lista(doc, w, c, elegidos, cache)
    for term, v in valores_export.items():
        doc.xref_set_key(term, "V", pymupdf.get_pdf_str(v))
    return len(cambios)


def _cubre(a: pymupdf.Rect, b: pymupdf.Rect) -> bool:
    """La firma en imagen ``b`` ocupa buena parte del campo ``a`` (o el campo, de la imagen)."""
    i = a & b
    if i.is_empty:
        return False
    area = i.width * i.height
    return area >= 0.25 * max(1e-6, a.width * a.height) or area >= 0.5 * max(1e-6, b.width * b.height)


def _firmar_campos_con_imagen(page: pymupdf.Page, imagenes: list[pymupdf.Rect]) -> None:
    """Un campo de firma vacío sobre el que se puso una firma en imagen ya cumplió su función: se
    quita.  Si se dejara, los campos se pintan encima del contenido y algunos lectores dibujan ahí
    un rótulo («SIGN», «Firme aquí») que taparía la firma."""
    if not imagenes:
        return
    for w in list(page.widgets(types=[pymupdf.PDF_WIDGET_TYPE_SIGNATURE])):
        if not w.is_signed and any(_cubre(core._visual(page, w.rect), r) for r in imagenes):
            page.delete_widget(w)


def _aplicar_sellos(doc: pymupdf.Document, sellos: Sequence) -> int:
    """Aplica los sellos (cada uno, un core.Edit o una lista de ellos). Devuelve cuántos se aplicaron."""
    por_pagina: dict[int, list[core.Edit]] = defaultdict(list)
    n = 0
    for s in sellos:
        grupo = [s] if isinstance(s, core.Edit) else [e for e in s if isinstance(e, core.Edit)]
        if not grupo or not 0 <= grupo[0].page < doc.page_count:
            continue
        por_pagina[grupo[0].page].extend(grupo)
        n += 1
    for pno in sorted(por_pagina):
        page = doc[pno]
        _firmar_campos_con_imagen(page, [pymupdf.Rect(e.rect) for e in por_pagina[pno] if e.kind == "image"])
        if page.rotation:
            # Quitar la rotación movería los campos del formulario: se dibuja en una hoja aparte
            # (en coordenadas visibles) y se superpone con la misma orientación.
            with pymupdf.open() as tmp:
                hoja = tmp.new_page(width=page.rect.width, height=page.rect.height)
                core._apply_page_edits(hoja, por_pagina[pno])
                page.show_pdf_page(page.rect * page.derotation_matrix, tmp, 0, rotate=page.rotation)
        else:
            core._apply_page_edits(page, por_pagina[pno])
    return n


def _aplanar_doc(doc: pymupdf.Document) -> int:
    n = sum(1 for page in doc for _w in page.widgets())
    if not n:
        return 0
    # Los campos de firma vacíos no aportan nada al aplanar (algunos dibujan un rótulo «SIGN»).
    for page in doc:
        for w in list(page.widgets()):
            if w.field_type == pymupdf.PDF_WIDGET_TYPE_SIGNATURE and not w.is_signed:
                page.delete_widget(w)
    doc.bake(annots=False, widgets=True)
    return n


def rellenar(path, output, valores: dict[str, object], sellos: Sequence = (), aplanar: bool = False,
             password=None, progress=None, incremental: bool = False) -> tuple[int, int]:
    """Rellena campos (``valores`` por ``Campo.id``), agrega sellos y, si se pide, aplana.

    Devuelve (campos cambiados, sellos aplicados).  Texto: str; casilla y opción: bool (True marca
    esa opción y desmarca las demás del mismo nombre); lista y desplegable: str (o list[str] en
    listas de selección múltiple).

    Con ``incremental=True`` los cambios se agregan al final de una copia exacta del original (sus bytes
    quedan intactos y una firma digital previa no se rompe).  Aplanar siempre reescribe el archivo completo,
    y también un PDF que tuvo que repararse al abrirlo.
    """
    if incremental and aplanar:
        incremental = False
    if incremental:
        with core.open_pdf(path, password) as doc:
            incremental = bool(doc.name == str(path) and doc.can_save_incrementally() and not doc.is_repaired)
    with core._output(output) as tmp:
        if incremental:
            import shutil
            shutil.copyfile(path, tmp)
        with core.open_pdf(tmp if incremental else path, password) as doc:
            core._report(progress, 0, 3, "Leyendo el formulario…")
            campos = _leer(doc)
            n = _aplicar_valores(doc, _deseados(campos, valores or {}), progress) if campos else 0
            core._report(progress, 1, 3, "Agregando textos, marcas y firmas…")
            m = _aplicar_sellos(doc, sellos or ())
            if aplanar:
                core._report(progress, 2, 3, "Aplanando el formulario…")
                _aplanar_doc(doc)
            if not n and not m and not aplanar and not campos:
                raise core.UserError("No hay nada para guardar: escribe en los campos o agrega algo sobre la página.")
            core._report(progress, 3, 3, "Guardando…")
            core._save_keeping_password(doc, tmp, password, incremental=incremental)
    return n, m


def vaciar(path, output, password=None, progress=None) -> int:
    """Deja en blanco todos los campos que se pueden editar. Devuelve cuántos se vaciaron."""
    with core._output(output) as tmp, core.open_pdf(path, password) as doc:
        campos = _leer(doc)
        if not campos:
            raise core.UserError("Este PDF no tiene campos de formulario.")
        deseado: dict[str, object] = {}
        for c in campos:
            if c.solo_lectura or c.tipo not in RELLENABLES:
                continue
            if c.tipo in ("casilla", "opcion"):
                deseado[c.nombre] = "Off"
            elif c.tipo == "lista" and c.multiple:
                deseado[c.nombre] = []
            else:
                deseado[c.nombre] = ""
        n = _aplicar_valores(doc, deseado, progress)
        core._save_keeping_password(doc, tmp, password)
    return n


def aplanar(path, output, password=None, progress=None) -> int:
    """Convierte los campos en contenido normal de la página. Devuelve cuántos se aplanaron."""
    with core._output(output) as tmp, core.open_pdf(path, password) as doc:
        core._report(progress, 0, 1, "Aplanando el formulario…")
        n = _aplanar_doc(doc)
        if not n:
            raise core.UserError("Este PDF no tiene campos de formulario: ya es un PDF normal.")
        core._save_keeping_password(doc, tmp, password)
    return n


# --------------------------------------------------------------------------
# Datos: exportar e importar
# --------------------------------------------------------------------------

def datos_de(campos: list[Campo], valores: dict[str, object] | None = None) -> dict[str, object]:
    """{nombre: valor} de los campos rellenables (opciones: el estado elegido o '')."""
    valores = valores or {}
    out: dict[str, object] = {}
    grupos: dict[str, list[Campo]] = defaultdict(list)
    for c in campos:
        if c.tipo in RELLENABLES:
            grupos[c.nombre].append(c)
    for nombre, grupo in grupos.items():
        c = grupo[0]
        if c.tipo == "opcion":
            elegido = next((x.estado for x in grupo if valores.get(x.id, x.valor)), "")
            out[nombre] = elegido
        elif c.tipo == "casilla":
            out[nombre] = any(bool(valores.get(x.id, x.valor)) for x in grupo)
        else:
            v = valores.get(c.id, c.valor)
            out[nombre] = list(v) if isinstance(v, (list, tuple)) else ("" if v is None else str(v))
    return out


def escribir_datos(datos: dict[str, object], output) -> int:
    """Guarda {nombre: valor} en .json o en .csv (columnas campo;valor, para Excel)."""
    ext = Path(output).suffix.lower()
    if ext not in (".json", ".csv"):
        raise core.UserError("Elige un archivo .json o .csv para los datos.")
    with core._output(output) as tmp:
        if ext == ".json":
            Path(tmp).write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            buf = io.StringIO()
            wr = csv.writer(buf, delimiter=";", lineterminator="\r\n")
            wr.writerow(["campo", "valor"])
            for k, v in datos.items():
                if isinstance(v, bool):
                    v = "Sí" if v else "No"
                elif isinstance(v, list):
                    v = " | ".join(v)
                wr.writerow([k, v])
            Path(tmp).write_text(buf.getvalue(), encoding="utf-8-sig", newline="")
    return len(datos)


def exportar_datos(path, output, password=None) -> int:
    """Exporta los valores del formulario a .json ({nombre: valor}) o .csv. Devuelve cuántos."""
    campos = leer_campos(path, password)
    datos = datos_de(campos)
    if not datos:
        raise core.UserError("Este PDF no tiene campos para exportar.")
    return escribir_datos(datos, output)


def importar_datos(path_datos) -> dict[str, object]:
    """Lee un .json o .csv de datos y devuelve {nombre de campo: valor}."""
    p = Path(path_datos)
    ext = p.suffix.lower()
    try:
        raw = p.read_bytes()
    except OSError as exc:
        raise core.UserError(f"No se pudo leer «{p.name}».") from exc
    texto = None
    for enc in ("utf-8-sig", "cp1252"):
        try:
            texto = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if texto is None:
        raise core.UserError(f"«{p.name}» no es un archivo de texto válido.")
    if ext == ".json":
        try:
            datos = json.loads(texto)
        except ValueError as exc:
            raise core.UserError(f"«{p.name}» no es un JSON válido.") from exc
        if not isinstance(datos, dict):
            raise core.UserError(f"«{p.name}» debe tener la forma {{\"campo\": \"valor\"}}.")
        return {str(k): v for k, v in datos.items()}
    if ext == ".csv":
        try:
            dialecto = csv.Sniffer().sniff(texto[:2048], delimiters=";,\t")
            delim = dialecto.delimiter
        except csv.Error:
            delim = ";"
        filas = [f for f in csv.reader(io.StringIO(texto), delimiter=delim) if f and any(x.strip() for x in f)]
        if filas and [x.strip().lower() for x in filas[0][:2]] == ["campo", "valor"]:
            filas = filas[1:]
        datos = {}
        for f in filas:
            if len(f) >= 2 and f[0].strip():
                datos[f[0].strip()] = f[1].replace("\r\n", "\n").replace("\r", "\n")
        if not datos:
            raise core.UserError(f"«{p.name}» no tiene filas con las columnas campo y valor.")
        return datos
    raise core.UserError("Los datos deben estar en un archivo .json o .csv.")


def _a_bool(v: object, estado: str = "") -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    s = str(v).strip().lower()
    return s in SI or (bool(estado) and s == estado.lower())


def _opcion_de(c: Campo, v: object) -> str | None:
    """Valor guardado de una lista o desplegable a partir de un valor o de un texto visible."""
    s = "" if v is None else str(v).strip()
    if s in c.valores_opciones:
        return s
    bajo = s.lower()
    for val, txt in zip(c.valores_opciones, c.opciones):
        if bajo in (val.lower(), txt.lower()):
            return val
    if c.editable or (not c.valores_opciones and c.tipo == "desplegable"):
        return s
    return None


def valores_desde_datos(campos: list[Campo], datos: dict[str, object]) -> tuple[dict[str, object], int, int]:
    """Convierte {nombre: valor} en valores por id. Devuelve (valores, aplicados, ignorados)."""
    grupos: dict[str, list[Campo]] = defaultdict(list)
    for c in campos:
        if c.tipo in RELLENABLES and not c.solo_lectura:
            grupos[c.nombre].append(c)
    valores: dict[str, object] = {}
    aplicados = ignorados = 0
    for nombre, v in datos.items():
        grupo = grupos.get(str(nombre))
        if not grupo:
            ignorados += 1
            continue
        c = grupo[0]
        ok = True
        if c.tipo == "casilla":
            on = _a_bool(v, c.estado)
            for x in grupo:
                valores[x.id] = on
        elif c.tipo == "opcion":
            s = "" if v is None else str(v).strip().lower()
            elegido = next((x for x in grupo if x.estado.lower() == s), None)
            if elegido is None and s not in ("", "off"):
                ok = False
            else:
                for x in grupo:
                    valores[x.id] = elegido is not None and x.estado == elegido.estado
        elif c.tipo == "lista" and c.multiple:
            items = v if isinstance(v, (list, tuple)) else [p for p in str(v or "").split("|")]
            elegidos = [o for o in (_opcion_de(c, i) for i in items if str(i).strip()) if o is not None]
            for x in grupo:
                valores[x.id] = elegidos
        elif c.tipo in ("lista", "desplegable"):
            o = _opcion_de(c, v) if str(v or "").strip() else ""
            if o is None:
                ok = False
            else:
                for x in grupo:
                    valores[x.id] = o
        else:
            s = "" if v is None else ("Sí" if v is True else "No" if v is False else str(v))
            if c.max_largo:
                s = s[:c.max_largo]
            for x in grupo:
                valores[x.id] = s
        if ok:
            aplicados += 1
        else:
            ignorados += 1
    return valores, aplicados, ignorados
