"""Censura de datos personales y limpieza de datos ocultos (sin interfaz gráfica).

Encuentra datos personales con patrones colombianos (cédulas, NIT con dígito de verificación,
celulares, cuentas, direcciones…), también en páginas escaneadas gracias al OCR; los borra de verdad
(el texto, los píxeles de las imágenes y los dibujos que quedan debajo) y verifica después que ninguno
de esos textos siga en el archivo.  Además audita las «falsas censuras» (recuadros que tapan texto sin
borrarlo) y quita los datos que viajan ocultos en el PDF (metadatos, adjuntos, JavaScript…).

Todas las coordenadas de los hallazgos son «visibles»: las de la página tal como se ve, con su giro.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

import pymupdf

from . import core
from .core import Progress, UserError, _report

Rect4 = tuple[float, float, float, float]


# --------------------------------------------------------------------------
# Texto normalizado (misma longitud que el original, un carácter por carácter)
# --------------------------------------------------------------------------

_BASE: dict[str, str] = {}

# Al extraer el texto de un PDF, el guion de «901.234.567-8» o «310-456-7890» suele salir como otro carácter:
# con fuentes como Segoe UI incrustadas, MuPDF devuelve U+2010 (guion tipográfico); Word usa a veces el guion
# de no separación, la raya corta o el guion suave.  Para buscar datos cuentan todos como «-»: si no, un
# celular con guiones no se encuentra y a un NIT se le escapa el dígito de verificación.  (La raya larga
# «—» no entra: separa frases, no números.)
_GUIONES = dict.fromkeys("\u00ad\u2010\u2011\u2012\u2013\u2212\ufe63\uff0d", "-")


def _base(c: str) -> str:
    r = _BASE.get(c)
    if r is None:
        g = _GUIONES.get(c)
        if g is not None:
            r = g
        else:
            d = unicodedata.normalize("NFD", c)
            r = d[0] if d and len(c) == 1 else c
        _BASE[c] = r
    return r


def normalizar(texto: str) -> str:
    """Quita las tildes (á → a, ñ → n) sin cambiar la longitud del texto."""
    return "".join(_base(c) for c in texto)


def plegar(texto: str) -> str:
    """Sin tildes y en minúsculas, sin cambiar la longitud del texto."""
    out = []
    for c in texto:
        b = _base(c)
        low = b.lower()
        out.append(low if len(low) == 1 else b)
    return "".join(out)


def _digitos(texto: str) -> str:
    return re.sub(r"\D", "", texto)


# --------------------------------------------------------------------------
# Validaciones
# --------------------------------------------------------------------------

_PESOS_NIT = (3, 7, 13, 17, 19, 23, 29, 37, 41, 43, 47, 53, 59, 67, 71)


def digito_verificacion(numero: str) -> int:
    """Dígito de verificación de un NIT (algoritmo de la DIAN)."""
    num = _digitos(numero)
    s = sum(int(d) * w for d, w in zip(reversed(num), _PESOS_NIT))
    r = s % 11
    return r if r < 2 else 11 - r


def validar_nit(texto: str) -> bool:
    """¿El NIT trae un dígito de verificación correcto?  Acepta «899.999.068-1» o «8999990681»."""
    texto = texto.strip()
    if "-" in texto:
        num, dv = texto.rsplit("-", 1)
        num, dv = _digitos(num), _digitos(dv)
    else:
        todo = _digitos(texto)
        num, dv = todo[:-1], todo[-1:]
    if len(dv) != 1 or not 5 <= len(num) <= len(_PESOS_NIT):
        return False
    return digito_verificacion(num) == int(dv)


def luhn(texto: str) -> bool:
    """Algoritmo de Luhn (números de tarjeta)."""
    d = _digitos(texto)
    if len(d) < 2:
        return False
    total = 0
    for k, c in enumerate(reversed(d)):
        n = int(c)
        if k % 2:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _validar_cedula(t: str) -> bool:
    return 6 <= len(_digitos(t)) <= 10


def _validar_nit_coincidencia(t: str) -> bool:
    # Con la palabra NIT delante basta el formato; sin ella exige un dígito de verificación válido.
    if re.match(r"\s*nit\b", t, re.IGNORECASE):
        return True
    return "-" in t and len(_digitos(t)) == 10 and validar_nit(t)


def _validar_tarjeta(t: str) -> bool:
    d = _digitos(t)
    return 13 <= len(d) <= 19 and d[0] in "23456" and luhn(d)


# --------------------------------------------------------------------------
# Patrones colombianos
# --------------------------------------------------------------------------

@dataclass
class Patron:
    clave: str
    nombre: str
    ejemplo: str
    regex: re.Pattern
    grupo: int | str = 0            # grupo que se censura (0 = toda la coincidencia)
    validar: Callable[[str], bool] | None = None   # recibe toda la coincidencia
    activo: bool = True             # marcado por defecto
    regex_ocr: re.Pattern | None = None   # variante más tolerante para el texto leído con OCR


_X = re.IGNORECASE | re.VERBOSE
_CONECTOR = r"(?:\s*(?:numero|num|nro|no|n[°º]|[#])\.?)?\s*:?\s*"

_CEDULA = r"""
(?:
    (?<![a-z0-9])(?:c[ .]{0,2}c|t[ .]{0,2}i|c[ .]{0,2}e)(?![a-z0-9])\.?
  | \bcedula(?:\s+de\s+(?:ciudadania|extranjeria))?
  | \btarjeta\s+de\s+identidad
  | \bdocumento(?:\s+de\s+identi(?:dad|ficacion))?
  | \bidentificacion
  | \bnuip
  | \bregistro\s+civil
)
""" + _CONECTOR + r"""
(?P<dato>\d{1,3}(?:[.,]\d{3}){1,3}|\d{6,10})(?!\d)(?![.,]\d)
"""

_NIT = r"""
(?:(?P<pre>\bnit\b\.?""" + _CONECTOR + r"""))?
(?<![\d.,])(?P<dato>\d{2,3}\.?\d{3}\.?\d{3}(?:\s?-\s?\d)?)(?!\d)(?![.,]\d)
"""

_INDICATIVO = r"(?:(?:\+|00)\s?57[\s.-]?|\(\s?\+?57\s?\)\s?|\b57[\s.-])?"

_CELULAR = r"(?<![\d+])" + _INDICATIVO + r"""
(?:\(\s?)?3[0-5]\d(?:\s?\))?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)
"""

_TELEFONO = r"(?<![\d+])" + _INDICATIVO + r"""
(?:\(\s?60[1-8]\s?\)|60[1-8])[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)
"""

_CORREO = r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}(?![\w-])"

_CUENTA = r"""
(?:
    \bcuenta(?:\s+(?:de\s+)?(?:ahorros?|corriente|bancaria|nomina))?
  | \bcta\b\.?(?:\s+(?:de\s+)?(?:ahorros?|corriente|cte\b\.?))?
  | \b(?:ahorros|corriente)\b
)
""" + _CONECTOR + r"""
(?P<dato>\d(?:[ -]?\d){7,19})(?!\d)
"""

_TARJETA = r"(?<![\d.,])(?:\d[ -]?){12,18}\d(?!\d)(?![.,]\d)"

_LETRA = r"(?:\s?[a-z](?![a-z]))"
_TIPOS_VIA = (r"avenida\s+(?:calle|carrera|cra|kr)|av\.?\s*(?:calle|carrera|cra|kra|kr|cl|cll)"
              r"|calle|cll|cl|carrera|cra|kra|kr|cr|ak|ac|avenida|av|diagonal|diag|dg"
              r"|transversal|transv|trans|tv|tr|circular|circ|autopista")
_CARDINAL = r"(?:\s+(?:sur|norte|este|oeste)\b)"


def _plantilla_direccion(separador: str) -> str:
    return rf"""
\b(?:
    (?:{_TIPOS_VIA})\.?(?![a-z])\s*\d{{1,3}}{_LETRA}?(?:\s?bis\b{_LETRA}?)?{_CARDINAL}?
  | (?:avenida|av|autopista)\.?\s+(?:(?:el|la|los|las)\s+)?[a-z]{{3,}}(?:\s+(?:de\s+|del\s+)?[a-z]{{3,}}){{0,2}}
)
\s*{separador}\s*
\d{{1,3}}{_LETRA}?(?:\s?bis\b)?{_LETRA}?\s*[-–]\s*\d{{1,3}}(?:\s?[a-z](?![a-z\d]))?(?!\d)
{_CARDINAL}?
(?:\s*,?\s*(?:apto|apartamento|apt|int|interior|casa|torre|bloque|of|oficina|local|piso)\.?\s*\d{{1,4}}{_LETRA}?)*
"""


_DIRECCION = _plantilla_direccion(r"(?:[#]|no\b\.?|n[°º]\.?|numero|nro\.?)")
# En páginas escaneadas, Tesseract suele leer el signo «#» como «++», «+», «*» o «H».
_DIRECCION_OCR = _plantilla_direccion(r"(?:[#+*]{1,2}|h\b|no\b\.?|n[°º]\.?|numero|nro\.?)")

_PLACA = r"(?<![A-Za-z0-9])[A-Z]{3}[ -]?\d{2}[0-9A-Z](?![A-Za-z0-9])(?![.,]\d)"
# Siglas que van seguidas de números y no son placas: «NIT 899.999…», «COP 500», «LEY 158»…
_NO_PLACA = {"NIT", "COP", "USD", "EUR", "LEY", "IVA", "RUT", "PBX", "TEL", "CEL", "ART", "DEC", "REF", "NUM",
             "NRO", "FAX", "EXT", "COD", "RES", "CAP", "PAG", "TAB", "FIG", "ISO", "NTC", "DIR", "APT", "INT"}


def _validar_placa(t: str) -> bool:
    return t[:3].upper() not in _NO_PLACA

_FECHA = r"(?<!\d)(?:0?[1-9]|[12]\d|3[01])[/.-](?:0?[1-9]|1[0-2])[/.-](?:19|20)\d{2}(?!\d)"

PATRONES: dict[str, Patron] = {p.clave: p for p in (
    Patron("cedula", "Cédulas y documentos de identidad", "C.C. 1.032.456.789",
           re.compile(_CEDULA, _X), "dato", _validar_cedula),
    Patron("nit", "NIT", "NIT 899.999.068-1", re.compile(_NIT, _X), "dato", _validar_nit_coincidencia),
    Patron("celular", "Celulares", "+57 310 456 7890", re.compile(_CELULAR, _X)),
    Patron("telefono", "Teléfonos fijos", "(601) 345 6789", re.compile(_TELEFONO, _X)),
    Patron("correo", "Correos electrónicos", "nombre@correo.com", re.compile(_CORREO, re.IGNORECASE)),
    Patron("cuenta", "Cuentas bancarias", "Cuenta de ahorros 0123-4567-8901",
           re.compile(_CUENTA, _X), "dato"),
    Patron("tarjeta", "Tarjetas de crédito o débito", "4111 1111 1111 1111",
           re.compile(_TARJETA, _X), 0, _validar_tarjeta),
    Patron("direccion", "Direcciones", "Calle 45 # 12-30 Sur", re.compile(_DIRECCION, _X),
           regex_ocr=re.compile(_DIRECCION_OCR, _X)),
    Patron("placa", "Placas de vehículos", "ABC123 o ABC12D", re.compile(_PLACA), 0, _validar_placa,
           activo=False),
    Patron("fecha", "Fechas", "15/03/1985", re.compile(_FECHA), activo=False),
)}

NOMBRES_CATEGORIA = {**{k: p.nombre for k, p in PATRONES.items()},
                     "palabra": "Palabras y nombres", "regex": "Expresión personalizada",
                     "falsa": "Recuadros que no borraban el texto", "manual": "Zonas marcadas a mano"}


def coincidencias(clave: str, texto: str, ocr: bool = False) -> list[str]:
    """Lo que el patrón ``clave`` censuraría en ``texto`` (útil para revisar y para las pruebas).

    ``ocr=True``: como si el texto viniera del reconocimiento de una página escaneada."""
    pat = PATRONES[clave]
    norm = normalizar(texto)
    return [texto[s:e] for s, e in _spans_patron(pat, norm, ocr)]


def _spans_patron(pat: Patron, norm: str, ocr: bool = False) -> Iterable[tuple[int, int]]:
    regex = pat.regex_ocr if ocr and pat.regex_ocr is not None else pat.regex
    for m in regex.finditer(norm):
        if pat.validar and not pat.validar(m.group(0)):
            continue
        s, e = m.span(pat.grupo)
        if 0 <= s < e:
            yield s, e


_SEP_NUM = r"[\s.,\-/()+]{0,2}"


def _es_numero(texto: str) -> bool:
    return len(_digitos(texto)) >= 5 and bool(re.fullmatch(r"[\d\s.,\-/()+]+", texto.strip()))


def _regex_numero(texto: str) -> str | None:
    """Un número (cédula, cuenta, celular…) escrito con o sin puntos, espacios, guiones o paréntesis."""
    if _es_numero(texto):
        return r"(?<!\d)" + _SEP_NUM.join(_digitos(texto)) + r"(?!\d)"
    return None


def _regex_palabra(termino: str, completa: bool, exacta: bool) -> re.Pattern | None:
    """Expresión para buscar una palabra o un nombre (los espacios valen también como salto de renglón)."""
    termino = termino.strip()
    if not termino:
        return None
    num = _regex_numero(termino)
    if num:
        return re.compile(num)
    base = termino if exacta else plegar(termino)
    patron = r"\s+".join(re.escape(p) for p in base.split())
    if completa:
        patron = r"(?<!\w)" + patron + r"(?!\w)"
    return re.compile(patron)


def compilar_regex(texto: str, mayusculas: bool = False) -> re.Pattern:
    try:
        return re.compile(texto, 0 if mayusculas else re.IGNORECASE)
    except re.error as exc:
        raise UserError(f"La expresión no es válida: {exc}") from exc


# --------------------------------------------------------------------------
# Hallazgos
# --------------------------------------------------------------------------

@dataclass
class Hallazgo:
    pagina: int                     # base 0
    rect: Rect4                     # coordenadas visibles; es lo que se cubre
    texto: str
    categoria: str                  # clave de patrón, 'palabra', 'regex', 'manual' o 'falsa'
    origen: str = "texto"           # 'texto' | 'ocr' | 'manual'
    contexto: str = ""
    incluir: bool = True
    rect_texto: Rect4 | None = None   # zona más ajustada para quitar el texto (si difiere de rect)


class ListaHallazgos(list):
    """Lista de hallazgos con datos de la búsqueda."""

    def __init__(self, *args):
        super().__init__(*args)
        self.escaneadas: list[int] = []     # páginas escaneadas (una imagen grande sin capa de OCR)
        self.sin_revisar: list[int] = []    # escaneadas cuya imagen no se pudo revisar
        self.avisos: list[str] = []
        self.paginas: int = 0               # páginas revisadas


@dataclass
class _Pagina:
    """Texto de una página con la caja de cada carácter (o de cada palabra, si viene del OCR)."""
    texto: str
    mapa: list[int]                 # posición del texto → índice de caja (-1 = espacio o salto agregado)
    cajas: list[Rect4]              # sin rotar
    base: list[float]               # línea base (y) de cada caja
    tam: list[float]                # tamaño de letra
    renglon: list[int]
    invisible: list[bool]
    renglones: list[list[float]]    # caja de cada renglón
    horizontal: list[bool]
    pos: list[int] = field(default_factory=list)   # índice de caja → posición en el texto
    ocr: bool = False


def _es_invisible(span: dict) -> bool:
    return core.span_is_invisible(span)


_FLAGS_TEXTO = pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES


def _datos_texto(page: pymupdf.Page) -> _Pagina:
    texto: list[str] = []
    mapa: list[int] = []
    pag = _Pagina("", mapa, [], [], [], [], [], [], [])
    for block in page.get_text("rawdict", flags=_FLAGS_TEXTO)["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            lid = len(pag.renglones)
            pag.renglones.append(list(line["bbox"]))
            dx, dy = line.get("dir", (1, 0))
            pag.horizontal.append(abs(dy) < 0.1)
            prev = None
            for span in line["spans"]:
                size = span.get("size", 10) or 10
                inv = _es_invisible(span)
                for ch in span["chars"]:
                    c = ch["c"]
                    if not c or c.isspace():
                        if texto and texto[-1] not in " \n":
                            texto.append(" ")
                            mapa.append(-1)
                        continue
                    b = ch["bbox"]
                    if prev is not None and texto and texto[-1] != " ":
                        pb = pag.cajas[prev]
                        if dx >= 0.7:
                            gap = b[0] - pb[2]
                        elif dx <= -0.7:
                            gap = pb[0] - b[2]
                        elif dy >= 0.7:
                            gap = b[1] - pb[3]
                        else:
                            gap = pb[1] - b[3]
                        if gap > 0.25 * size:
                            texto.append(" ")
                            mapa.append(-1)
                    k = len(pag.cajas)
                    pag.cajas.append(tuple(b))
                    pag.base.append(ch["origin"][1])
                    pag.tam.append(size)
                    pag.renglon.append(lid)
                    pag.invisible.append(inv)
                    pag.pos.append(len(texto))
                    texto.append(c[0])
                    mapa.append(k)
                    prev = k
            if texto and texto[-1] == " ":
                texto[-1] = "\n"
            elif texto and texto[-1] != "\n":
                texto.append("\n")
                mapa.append(-1)
    pag.texto = "".join(texto)
    return pag


def _datos_ocr(words: list[tuple]) -> _Pagina:
    """Texto armado con las palabras del OCR: cada carácter apunta a la caja de su palabra."""
    texto: list[str] = []
    mapa: list[int] = []
    pag = _Pagina("", mapa, [], [], [], [], [], [], [], ocr=True)
    lineas: dict[tuple, int] = {}
    ultimo = None
    for w in sorted(words, key=lambda w: (w[5], w[6], w[7])):
        x0, y0, x1, y1, palabra = w[:5]
        palabra = palabra.strip()
        if not palabra:
            continue
        key = (w[5], w[6])
        if key not in lineas:
            lineas[key] = len(pag.renglones)
            pag.renglones.append([x0, y0, x1, y1])
            pag.horizontal.append(True)
            if texto:
                texto.append("\n")
                mapa.append(-1)
        else:
            r = pag.renglones[lineas[key]]
            r[:] = [min(r[0], x0), min(r[1], y0), max(r[2], x1), max(r[3], y1)]
            if key == ultimo:
                texto.append(" ")
                mapa.append(-1)
        ultimo = key
        k = len(pag.cajas)
        pag.cajas.append((x0, y0, x1, y1))
        pag.base.append(y1)
        pag.tam.append(max(y1 - y0, 1))
        pag.renglon.append(lineas[key])
        pag.invisible.append(False)
        pag.pos.append(len(texto))
        for c in palabra:
            texto.append(c)
            mapa.append(k)
    if texto:
        texto.append("\n")
        mapa.append(-1)
    pag.texto = "".join(texto)
    return pag


def _cruza(a, b, tol: float = 0.0) -> bool:
    return a[0] < b[2] + tol and b[0] < a[2] + tol and a[1] < b[3] and b[1] < a[3]


def _union(cajas: Iterable[Rect4]) -> list[float]:
    it = iter(cajas)
    x0, y0, x1, y1 = next(it)
    for b in it:
        x0, y0, x1, y1 = min(x0, b[0]), min(y0, b[1]), max(x1, b[2]), max(y1, b[3])
    return [x0, y0, x1, y1]


def _ajustada(pag: _Pagina, k: int) -> Rect4:
    """Caja estrecha de un carácter (aprox. de la parte alta de las mayúsculas a la de los descendentes)."""
    b, y, s = pag.cajas[k], pag.base[k], pag.tam[k]
    return (b[0], max(b[1], y - 0.8 * s), b[2], min(b[3], y + 0.22 * s))


def _ajustar(pag: _Pagina, objetivo: set[int], rect: list[float], estrecha: bool = False) -> list[float]:
    """Recorta ``rect`` para no tocar caracteres vecinos (renglones de arriba y abajo y palabras del lado).

    MuPDF borra todo carácter cuya caja toca la zona de censura; las cajas de letra suelen ser más altas
    que el interlineado, así que sin este ajuste se irían también los renglones vecinos.
    """
    r = list(rect)
    centro_x = (r[0] + r[2]) / 2
    centro_y = (r[1] + r[3]) / 2
    propios = {pag.renglon[k] for k in objetivo}
    for lid, lb in enumerate(pag.renglones):
        if not _cruza(lb, r, 0.5):
            continue
        for k in _cajas_de_renglon(pag, lid):
            if k in objetivo:
                continue
            b = _ajustada(pag, k) if estrecha else pag.cajas[k]
            if not _cruza(b, r, 0.3):
                continue
            mismo = lid in propios or (min(b[3], r[3]) - max(b[1], r[1])) > 0.6 * min(b[3] - b[1], r[3] - r[1])
            if mismo:
                if (b[0] + b[2]) / 2 < centro_x:
                    r[0] = max(r[0], b[2] + 0.3)
                else:
                    r[2] = min(r[2], b[0] - 0.3)
            elif (b[1] + b[3]) / 2 < centro_y:
                r[1] = max(r[1], b[3] + 0.2)
            else:
                r[3] = min(r[3], b[1] - 0.2)
    return r


def _cajas_de_renglon(pag: _Pagina, lid: int) -> list[int]:
    cache = getattr(pag, "_por_renglon", None)
    if cache is None:
        cache = defaultdict(list)
        for k, ren in enumerate(pag.renglon):
            cache[ren].append(k)
        pag._por_renglon = cache        # type: ignore[attr-defined]
    return cache.get(lid, [])


def _zonas_de_tramo(pag: _Pagina, s: int, e: int):
    """Divide la coincidencia [s, e) en renglones → (rect, rect_texto, desde, hasta) sin rotar."""
    grupos: dict[int, list[int]] = defaultdict(list)
    for i in range(s, e):
        k = pag.mapa[i]
        if k >= 0 and (not grupos[pag.renglon[k]] or grupos[pag.renglon[k]][-1] != k):
            grupos[pag.renglon[k]].append(k)
    out = []
    for lid in sorted(grupos, key=lambda g: pag.pos[grupos[g][0]]):
        ks = grupos[lid]
        desde = pag.pos[ks[0]]
        hasta = pag.pos[ks[-1]] + 1
        if pag.ocr:
            # palabras completas: se extiende el texto hasta los bordes de cada palabra
            while desde > 0 and pag.mapa[desde - 1] == ks[0]:
                desde -= 1
            while hasta < len(pag.mapa) and pag.mapa[hasta] == ks[-1]:
                hasta += 1
            r = _union(pag.cajas[k] for k in ks)
            pad = max(1.0, 0.08 * (r[3] - r[1]))
            out.append(([r[0] - pad, r[1] - pad, r[2] + pad, r[3] + pad], None, desde, hasta))
            continue
        completa = _union(pag.cajas[k] for k in ks)
        objetivo = set(ks)
        if pag.horizontal[lid]:
            r = _ajustar(pag, objetivo, completa)
            alto = min(pag.tam[k] for k in ks)
            if r[3] - r[1] < 0.25 * alto or r[2] <= r[0]:
                r = _ajustar(pag, objetivo, _union(_ajustada(pag, k) for k in ks), estrecha=True)
            if r[3] - r[1] <= 0.3 or r[2] <= r[0]:
                r = _union(_ajustada(pag, k) for k in ks)
        else:
            r = completa
        if all(pag.invisible[k] for k in ks):
            # texto invisible (capa de OCR sobre un escaneo): se quita el texto con la zona ajustada y
            # se cubren los píxeles de la palabra con la zona completa
            out.append((completa, r, desde, hasta))
        else:
            out.append((r, None, desde, hasta))
    return out


def _contexto(texto: str, desde: int, hasta: int, ancho: int = 30) -> str:
    antes = texto[max(0, desde - ancho):desde].replace("\n", " ")
    despues = texto[hasta:hasta + ancho].replace("\n", " ")
    return (("…" if desde > ancho else "") + antes + texto[desde:hasta].replace("\n", " ") + despues
            + ("…" if hasta + ancho < len(texto) else ""))


def _visual_t(page: pymupdf.Page, r) -> Rect4:
    return tuple(core._visual(page, r))


def _tramos(pag: _Pagina, patrones: Sequence[Patron], palabras: Sequence[re.Pattern],
            regexes: Sequence[re.Pattern], exacta: bool) -> list[tuple[int, int, str]]:
    norm = normalizar(pag.texto)
    cand: list[tuple[int, int, int, str]] = []
    for prio, pat in enumerate(patrones):
        for s, e in _spans_patron(pat, norm, pag.ocr):
            cand.append((s, e, prio, pat.clave))
    if palabras:
        donde = pag.texto if exacta else plegar(pag.texto)
        for w in palabras:
            for m in w.finditer(donde):
                if m.end() > m.start():
                    cand.append((m.start(), m.end(), 100, "palabra"))
    for rx in regexes:
        for m in rx.finditer(pag.texto):
            if m.end() > m.start():
                cand.append((m.start(), m.end(), 200, "regex"))
    cand.sort(key=lambda c: (c[0], -(c[1] - c[0]), c[2]))
    out, fin = [], -1
    for s, e, _p, cat in cand:
        if e <= fin:              # ya cubierto por una coincidencia anterior
            continue
        # recorta espacios y saltos de los bordes
        while s < e and pag.texto[s].isspace():
            s += 1
        while e > s and pag.texto[e - 1].isspace():
            e -= 1
        if e > s:
            out.append((s, e, cat))
            fin = max(fin, e)
    return out


def buscar(path, *, patrones: Sequence[str] = (), palabras: Sequence[str] = (), regex: Sequence[str] | str = (),
           paginas: str = "", mayusculas: bool = False, palabra_completa: bool = False, usar_ocr: bool = True,
           idioma: str = "spa+eng", dpi: int = 300, password: str | None = None,
           progress: Progress = None) -> ListaHallazgos:
    """Busca datos personales.  Devuelve una lista de ``Hallazgo`` (con avisos en ``.avisos``)."""
    pats = []
    for k in patrones:
        if k not in PATRONES:
            raise ValueError(f"patrón desconocido: {k}")
        pats.append(PATRONES[k])
    if isinstance(regex, str):
        regex = [regex]
    regexes = [compilar_regex(r, mayusculas) for r in regex if r and r.strip()]
    words = [w for w in (_regex_palabra(p, palabra_completa, mayusculas) for p in palabras or ()) if w]
    if not pats and not words and not regexes:
        raise UserError("Elige al menos un tipo de dato o escribe una palabra para buscar.")
    out = ListaHallazgos()
    tessdata: str | None = None
    error_ocr: str | None = None
    with core.open_pdf(path, password) as doc:
        idx = core.parse_pages(paginas, doc.page_count)
        out.paginas = len(idx)
        for k, i in enumerate(idx):
            _report(progress, k, len(idx), f"Revisando la página {i + 1} de {doc.page_count}")
            page = doc[i]
            pag = _datos_texto(page)
            fuentes = [(pag, "texto")]
            # Se decide por la imagen y no por el texto: un escaneo con un sello de radicado, una marca de
            # agua o un encabezado digital encima también se revisa con OCR.  Si la página ya trae su capa
            # de texto invisible (un PDF escaneado y buscable), esa capa basta.
            if core.image_coverage(page) > 0.5 and sum(pag.invisible) < 20:
                out.escaneadas.append(i)
                if usar_ocr and tessdata is None and error_ocr is None:
                    try:
                        tessdata = core._check_ocr(idioma)
                    except UserError as exc:
                        error_ocr = str(exc)
                if not usar_ocr or error_ocr:
                    out.sin_revisar.append(i)
                else:
                    _report(progress, k, len(idx), f"Reconociendo el texto de la imagen de la página {i + 1}")
                    palabras_ocr = _fuera_del_texto(core._ocr_words(page, idioma, dpi, tessdata), pag)
                    fuentes.append((_datos_ocr(palabras_ocr), "ocr"))
            for pag, origen in fuentes:
                for s, e, cat in _tramos(pag, pats, words, regexes, mayusculas):
                    for rect, rect_texto, desde, hasta in _zonas_de_tramo(pag, s, e):
                        out.append(Hallazgo(
                            pagina=i, rect=_visual_t(page, rect), texto=pag.texto[desde:hasta].strip(),
                            categoria=cat, origen=origen, contexto=_contexto(pag.texto, desde, hasta),
                            rect_texto=_visual_t(page, rect_texto) if rect_texto else None))
        _report(progress, len(idx), len(idx), "Listo")
    if error_ocr and out.sin_revisar:
        out.avisos.append(f"{error_ocr} Las imágenes escaneadas ({_lista_paginas(out.sin_revisar)}) no se "
                          f"pudieron revisar: solo se buscó en el texto que el PDF ya tiene.")
    elif out.sin_revisar:
        out.avisos.append(f"Las imágenes escaneadas ({_lista_paginas(out.sin_revisar)}) no se revisaron porque "
                          "el reconocimiento de texto (OCR) está desactivado.")
    return out


def _lista_paginas(paginas: Sequence[int]) -> str:
    nums = [str(p + 1) for p in paginas]
    if len(nums) == 1:
        return f"página {nums[0]}"
    if len(nums) > 8:
        return f"{len(nums)} páginas"
    return "páginas " + ", ".join(nums[:-1]) + " y " + nums[-1]


def _fuera_del_texto(palabras: list[tuple], pag: _Pagina) -> list[tuple]:
    """Quita del OCR las palabras que caen sobre texto digital visible (ese texto ya se buscó
    directamente): así un sello o un encabezado no sale dos veces."""
    renglones: dict[int, list[int]] = defaultdict(list)
    for k, inv in enumerate(pag.invisible):
        if not inv:
            renglones[pag.renglon[k]].append(k)
    if not renglones:
        return palabras
    cajas_renglon = [(_union(pag.cajas[k] for k in ks), ks) for ks in renglones.values()]
    out = []
    for w in palabras:
        x0, y0, x1, y1 = w[:4]
        area = max((x1 - x0) * (y1 - y0), 1e-6)
        tapada = 0.0
        for rb, ks in cajas_renglon:
            if not _cruza(rb, (x0, y0, x1, y1)):
                continue
            for k in ks:
                b = pag.cajas[k]
                ix = min(b[2], x1) - max(b[0], x0)
                iy = min(b[3], y1) - max(b[1], y0)
                if ix > 0 and iy > 0:
                    tapada += ix * iy
        if tapada < 0.5 * area:
            out.append(w)
    return out


# --------------------------------------------------------------------------
# Censurar y verificar
# --------------------------------------------------------------------------

@dataclass
class Resto:
    donde: str        # 'página 3', 'metadatos', 'comentario de la página 2', 'campo de formulario'…
    texto: str
    tipo: str = "texto"    # 'texto' (el texto sigue ahí) | 'zona' (la zona no quedó de un solo color)


@dataclass
class ResultadoCensura:
    zonas: int
    paginas: int
    restos: list[Resto] = field(default_factory=list)


def _rect_valido(page: pymupdf.Page, r) -> pymupdf.Rect | None:
    rr = pymupdf.Rect(r) & page.rect
    return None if rr.is_empty or rr.width < 0.2 or rr.height < 0.2 else rr


def _rgb(color) -> tuple[float, float, float]:
    if not color:
        return (0.0, 0.0, 0.0)
    if len(color) == 1:
        return (color[0],) * 3
    if len(color) == 4:
        c, m, y, k = color
        return ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
    return tuple(color[:3])


def _aplanar_campo(page: pymupdf.Page, w) -> None:
    """Convierte un campo de formulario en contenido fijo de la página (su valor queda como texto)."""
    r = pymupdf.Rect(w.rect)
    tipo = w.field_type
    valor = w.field_value
    if isinstance(valor, (list, tuple)):
        valor = ", ".join(map(str, valor))
    texto = "" if valor in (None, False, "Off") else str(valor)
    if tipo in (pymupdf.PDF_WIDGET_TYPE_CHECKBOX, pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON):
        texto = "X" if texto else ""
    elif tipo in (pymupdf.PDF_WIDGET_TYPE_BUTTON, pymupdf.PDF_WIDGET_TYPE_SIGNATURE):
        texto = ""
    try:
        borde = w.border_color if (w.border_width or 0) > 0 else None
        if w.fill_color or borde:
            page.draw_rect(r, color=_rgb(borde) if borde else None, fill=_rgb(w.fill_color) if w.fill_color else None,
                           width=w.border_width or 0)
    except Exception:  # noqa: BLE001 — un borde raro no debe impedir la censura
        pass
    if texto.strip():
        fs = w.text_fontsize or min(11.0, max(6.0, r.height * 0.6))
        color = _rgb(w.text_color)
        if w.field_flags & pymupdf.PDF_TX_FIELD_IS_MULTILINE:
            caja = r + (2, 2, -2, -2)
            while fs >= 4 and page.insert_textbox(caja, texto, fontsize=fs, fontname="helv", color=color) < 0:
                fs -= 1
        else:
            page.insert_text((r.x0 + 2, r.y0 + (r.height + fs * 0.7) / 2), texto, fontsize=fs, fontname="helv",
                             color=color)
    page.delete_widget(w)


def _limpiar_encima(page: pymupdf.Page, zonas: Sequence[Hallazgo]) -> list[pymupdf.Rect]:
    """Enlaces, campos y anotaciones que tocan una zona: su contenido no está en la página, así que la censura
    no lo alcanzaría.  Los enlaces y anotaciones se quitan; los campos se aplanan para borrar solo el dato.
    Devuelve zonas adicionales: dónde quedó el dato dentro de los campos aplanados."""
    rects = [pymupdf.Rect(z.rect) for z in zonas]

    def toca(r) -> bool:
        r = pymupdf.Rect(r)
        return any(r.intersects(z) for z in rects)

    for ln in page.get_links():
        if toca(ln["from"]):
            page.delete_link(ln)
    extra: list[pymupdf.Rect] = []
    campos = [w.xref for w in page.widgets() if toca(w.rect)]
    if campos:
        page.wrap_contents()
    for xref in campos:
        w = page.load_widget(xref)
        if w is None:
            continue
        wr = pymupdf.Rect(w.rect)
        textos = [z.texto for z, zr in zip(zonas, rects) if z.texto and zr.intersects(wr)]
        _aplanar_campo(page, w)
        for t in textos:
            hits = page.search_for(t, clip=wr)
            if not hits:                     # no se pudo ubicar: se borra todo el campo
                extra.append(wr)
                break
            extra.extend(h & wr for h in hits)
    a = page.first_annot
    while a:
        if a.type[0] not in (pymupdf.PDF_ANNOT_POPUP, pymupdf.PDF_ANNOT_REDACT, pymupdf.PDF_ANNOT_WIDGET,
                             pymupdf.PDF_ANNOT_LINK) and toca(a.rect):
            a = page.delete_annot(a)
        else:
            a = a.next
    return extra


def _censurar_pagina(page: pymupdf.Page, zonas: Sequence[Hallazgo], color) -> None:
    if page.rotation:
        page.remove_rotation()       # así las coordenadas visibles son las de la página
    hay = False
    for r in _limpiar_encima(page, zonas):
        r = _rect_valido(page, r)
        if r is not None:
            page.add_redact_annot(r, fill=color, cross_out=False)
            hay = True
    for z in zonas:
        r = _rect_valido(page, z.rect_texto or z.rect)
        if r is not None:
            page.add_redact_annot(r, fill=color, cross_out=False)
            hay = True
    if hay:
        # core.apply_redactions corrige el corrimiento del texto vecino que deja MuPDF 1.28
        core.apply_redactions(page, images=pymupdf.PDF_REDACT_IMAGE_PIXELS,
                              graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                              text=pymupdf.PDF_REDACT_TEXT_REMOVE)
    # Segunda pasada: zona completa (píxeles de la imagen) sin tocar el texto de los vecinos.
    dobles = [z for z in zonas if z.rect_texto and tuple(z.rect_texto) != tuple(z.rect)]
    hay = False
    for z in dobles:
        r = _rect_valido(page, z.rect)
        if r is not None:
            page.add_redact_annot(r, fill=color, cross_out=False)
            hay = True
    if hay:
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS,
                              graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                              text=pymupdf.PDF_REDACT_TEXT_NONE)


def censurar(path, output, zonas: Sequence[Hallazgo], *, color=(0, 0, 0), password: str | None = None,
             progress: Progress = None) -> ResultadoCensura:
    """Borra de verdad las zonas incluidas y verifica el resultado.  El original no se modifica."""
    activas = [z for z in zonas if z.incluir]
    if not activas:
        raise UserError("No hay datos marcados para censurar.")
    por_pagina: dict[int, list[Hallazgo]] = defaultdict(list)
    for z in activas:
        por_pagina[z.pagina].append(z)
    paginas = sorted(por_pagina)
    total = len(paginas) + 2
    with core._output(output) as tmp:
        with core.open_pdf(path, password) as doc:
            for k, pno in enumerate(paginas):
                if not 0 <= pno < doc.page_count:
                    raise UserError(f"La página {pno + 1} no existe en el documento.")
                _report(progress, k, total, f"Censurando la página {pno + 1}")
                _censurar_pagina(doc[pno], por_pagina[pno], color)
            _report(progress, len(paginas), total, "Guardando…")
            # Siempre se reescribe el archivo completo: un guardado incremental dejaría el dato dentro.
            core._save_keeping_password(doc, tmp, password, garbage=4, clean=True)
        _report(progress, len(paginas) + 1, total, "Verificando que no quede nada…")
        textos = sorted({z.texto for z in activas if z.texto and z.categoria != "manual"})
        restos = verificar(tmp, textos, activas, password=password,
                           progress=lambda i, n, m: _report(progress, len(paginas) + 1, total, m))
    excluidos = {(plegar(z.texto).strip(), z.pagina) for z in zonas if not z.incluir and z.texto}
    restos = [r for r in restos if not (r.tipo == "texto" and r.donde.startswith("página ")
                                        and (plegar(r.texto).strip(), int(r.donde.split()[1]) - 1) in excluidos)]
    return ResultadoCensura(zonas=len(activas), paginas=len(paginas), restos=restos)


def _regex_verificacion(texto: str) -> re.Pattern | None:
    t = " ".join(texto.split())
    if sum(ch.isalnum() for ch in t) < 3:
        return None
    num = _regex_numero(t)
    if num:
        return re.compile(num)
    partes = plegar(t).split()
    patron = r"\s+".join(re.escape(p) for p in partes) if len(partes) > 1 else re.escape(partes[0])
    pre = r"(?<!\w)" if re.match(r"\w", partes[0]) else ""
    post = r"(?!\w)" if re.search(r"\w$", partes[-1]) else ""
    return re.compile(pre + patron + post)


_RE_TOKEN = re.compile(r"\w+")
_RE_GRUPO_NUM = re.compile(r"\d(?:[\s.,\-/()+]{0,2}\d)*")


class _Buscador:
    """Busca muchos textos a la vez sin recorrer cada fuente una vez por texto.

    Cada texto se indexa por sus primeras palabras (o, si es un número, por sus dígitos); en cada fuente
    solo se confirman con la expresión completa los textos cuyas palabras o dígitos aparecen."""

    def __init__(self, textos: Iterable[str]):
        self.palabras: dict[tuple, list] = defaultdict(list)
        self.numeros: dict[str, list] = defaultdict(list)
        for t in dict.fromkeys(t.strip() for t in textos if t and t.strip()):
            rx = _regex_verificacion(t)
            if rx is None:
                continue
            if _es_numero(t):
                self.numeros[_digitos(t)].append((t, rx))
            else:
                claves = tuple(_RE_TOKEN.findall(plegar(t)))[:3]
                if claves:
                    self.palabras[claves].append((t, rx))
        self.ngramas = sorted({len(k) for k in self.palabras})
        self.largos = sorted({len(k) for k in self.numeros})

    def __bool__(self) -> bool:
        return bool(self.palabras or self.numeros)

    def buscar(self, fuente: str) -> list[str]:
        f = plegar(fuente)
        cand: dict[str, re.Pattern] = {}
        if self.palabras:
            toks = _RE_TOKEN.findall(f)
            for i in range(len(toks)):
                for n in self.ngramas:
                    for t, rx in self.palabras.get(tuple(toks[i:i + n]), ()):
                        cand[t] = rx
        if self.numeros:
            for m in _RE_GRUPO_NUM.finditer(f):
                ds = _digitos(m.group(0))
                for n in self.largos:
                    for i in range(len(ds) - n + 1):
                        for t, rx in self.numeros.get(ds[i:i + n], ()):
                            cand[t] = rx
        return [t for t, rx in cand.items() if rx.search(f)]


def _zona_uniforme(page: pymupdf.Page, rect) -> bool:
    """¿La zona (coordenadas visibles) renderizada es de un solo color?"""
    r = (pymupdf.Rect(rect) + (0.8, 0.8, -0.8, -0.8)) & page.rect
    if r.is_empty or r.width < 1 or r.height < 1:
        return True
    zoom = 2
    if page.rotation:
        # la página se renderiza tal como se ve y se mira solo la zona
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        clip = pymupdf.IRect(int(r.x0 * zoom) + 1, int(r.y0 * zoom) + 1, int(r.x1 * zoom) - 1,
                             int(r.y1 * zoom) - 1) & pix.irect
        if clip.is_empty:
            return True
        ratio, _color = pix.color_topusage(clip=clip)
        return ratio >= 0.995
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=r, alpha=False)
    if pix.width == 0 or pix.height == 0 or pix.is_unicolor:
        return True
    ratio, _color = pix.color_topusage()
    return ratio >= 0.995


def _textos_adjunto(doc: pymupdf.Document, i: int) -> list[str]:
    info = doc.embfile_info(i)
    out = [info.get("name", ""), info.get("filename", ""), info.get("ufilename", ""), info.get("description", "")]
    try:
        if info.get("size", 0) <= 5_000_000:
            data = doc.embfile_get(i)
            out.append(data.decode("utf-8", errors="ignore"))
    except Exception:  # noqa: BLE001
        pass
    return out


def verificar(path, textos: Sequence[str], zonas: Sequence[Hallazgo] = (), password: str | None = None,
              progress: Progress = None) -> list[Resto]:
    """Busca ``textos`` en todo el archivo (páginas, comentarios, campos, enlaces, metadatos, marcadores y
    adjuntos) y comprueba que las zonas de origen OCR o manual quedaron de un solo color."""
    buscados = _Buscador(textos)
    restos: list[Resto] = []
    vistos: set[tuple[str, str]] = set()

    def revisar(fuente: str, donde: str):
        if not fuente:
            return
        for t in buscados.buscar(fuente):
            if (t, donde) not in vistos:
                vistos.add((t, donde))
                restos.append(Resto(donde, t))

    with core.open_pdf(path, password) as doc:
        n = doc.page_count
        if buscados:
            for i in range(n):
                if i % 10 == 0:
                    _report(progress, i, n, f"Verificando la página {i + 1}")
                page = doc[i]
                revisar(page.get_text("text"), f"página {i + 1}")
                for annot in page.annots():
                    info = annot.info or {}
                    partes = [info.get("content", ""), info.get("title", ""), info.get("subject", "")]
                    if annot.type[0] == pymupdf.PDF_ANNOT_FILE_ATTACHMENT:
                        try:
                            fi = annot.file_info
                            partes += [fi.get("filename", ""), fi.get("description", "")]
                        except Exception:  # noqa: BLE001
                            pass
                    revisar(" ".join(p for p in partes if p), f"comentario de la página {i + 1}")
                for w in page.widgets():
                    valor = w.field_value
                    if isinstance(valor, (list, tuple)):
                        valor = " ".join(map(str, valor))
                    revisar(str(valor or ""), "campo de formulario")
                for ln in page.get_links():
                    revisar(ln.get("uri") or "", f"enlace de la página {i + 1}")
            meta = doc.metadata or {}
            revisar(" ".join(str(v) for k, v in meta.items() if v and k not in ("format", "encryption")),
                    "metadatos")
            try:
                revisar(doc.get_xml_metadata(), "metadatos")
            except Exception:  # noqa: BLE001
                pass
            try:
                revisar(" ".join(t[1] for t in doc.get_toc(simple=True)), "marcador")
            except Exception:  # noqa: BLE001
                pass
            for k in range(doc.embfile_count()):
                revisar(" ".join(_textos_adjunto(doc, k)), "adjunto")
        for z in zonas:
            if z.origen in ("ocr", "manual") or z.rect_texto or z.categoria == "falsa":
                if 0 <= z.pagina < n and not _zona_uniforme(doc[z.pagina], z.rect):
                    nombre = "zona marcada a mano" if z.origen == "manual" else (z.texto or "zona del recuadro")
                    restos.append(Resto(f"página {z.pagina + 1}", nombre, "zona"))
    return restos


# --------------------------------------------------------------------------
# Auditoría de falsas censuras
# --------------------------------------------------------------------------

def _oscuro(color) -> bool:
    if not color:
        return False
    if len(color) == 1:
        return color[0] < 0.3
    if len(color) == 4:      # CMYK
        c, m, y, k = color
        color = ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
    r, g, b = color[:3]
    return 0.299 * r + 0.587 * g + 0.114 * b < 0.3


def _tapas_de_anotaciones(page: pymupdf.Page) -> list[tuple[pymupdf.Rect, float, bool]]:
    """(rect, orden de pintado, ¿es oscuro?) de las anotaciones opacas: siempre se pintan encima de todo."""
    out = []
    encima = float("inf")
    for annot in page.annots():
        t = annot.type[0]
        cols = annot.colors or {}
        op = annot.opacity
        opaca = op is None or op < 0 or op >= 0.9
        if t == pymupdf.PDF_ANNOT_REDACT:
            out.append((annot.rect, encima, False))
        elif t in (pymupdf.PDF_ANNOT_SQUARE, pymupdf.PDF_ANNOT_CIRCLE, pymupdf.PDF_ANNOT_POLYGON,
                   pymupdf.PDF_ANNOT_FREE_TEXT):
            if cols.get("fill") and opaca:
                out.append((annot.rect, encima, _oscuro(cols.get("fill"))))
        elif t == pymupdf.PDF_ANNOT_HIGHLIGHT:
            if _oscuro(cols.get("stroke")) and opaca:
                out.append((annot.rect, encima, True))
        elif t == pymupdf.PDF_ANNOT_INK:
            ancho = (annot.border or {}).get("width", 1) or 1
            if _oscuro(cols.get("stroke")) and opaca and ancho >= 4:
                out.append((annot.rect, encima, True))
    return out


def _pixeles(doc: pymupdf.Document, xref: int, cache: dict) -> pymupdf.Pixmap | None:
    """Píxeles de una imagen del PDF (en gris o RGB, sin transparencia), guardados en ``cache``."""
    if xref not in cache:
        try:
            pix = pymupdf.Pixmap(doc, xref)
            if pix.alpha:
                pix = pymupdf.Pixmap(pix, 0)
            if pix.colorspace is None or pix.colorspace.n not in (1, 3):
                pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        except Exception:  # noqa: BLE001
            pix = None
        cache[xref] = pix
    return cache[xref]


def _imagen_ya_borrada(doc: pymupdf.Document, imagenes: list[dict], r: pymupdf.Rect, cache: dict) -> bool:
    """¿La zona ``r`` de las imágenes que hay debajo es de un solo color?

    Así queda una imagen censurada de verdad (MuPDF rellena los píxeles); si la zona tiene detalle, lo que el
    recuadro tapa todavía se puede recuperar quitándolo.  Se mira la imagen guardada en el archivo, no la página
    pintada (que siempre se vería negra por el recuadro)."""
    revisadas = 0
    for info in imagenes:
        bbox = pymupdf.Rect(info.get("bbox") or (0, 0, 0, 0))
        zona = bbox & r
        if zona.is_empty or zona.get_area() < 0.5 * r.get_area():
            continue
        xref = info.get("xref") or 0
        pix = _pixeles(doc, xref, cache) if xref > 0 else None
        m = pymupdf.Matrix(info.get("transform") or (0, 0, 0, 0, 0, 0))
        if pix is None or abs(m.a * m.d - m.b * m.c) < 1e-9:
            return False                         # no se puede comprobar: se avisa por si acaso
        u = (zona.quad * ~m).rect                # zona en el cuadrado unidad de la imagen
        clip = pymupdf.IRect(math.floor(u.x0 * pix.width) + 1, math.floor(u.y0 * pix.height) + 1,
                             math.ceil(u.x1 * pix.width) - 1, math.ceil(u.y1 * pix.height) - 1) & pix.irect
        if clip.is_empty:
            continue
        if pix.color_topusage(clip=clip)[0] < 0.98:
            return False
        revisadas += 1
    return revisadas > 0


def _tapa_algo(pag: _Pagina, objetivo: set[int], texto: str) -> bool:
    """¿El recuadro tapa un dato y no solo el borde de una letra vecina?

    Cuenta si tapa una palabra completa (con letras o números) o al menos tres letras o números."""
    if sum(c.isalnum() for c in texto) >= 3:
        return True
    t = pag.texto
    for k in objetivo:
        a = b = pag.pos[k]
        while a > 0 and not t[a - 1].isspace():
            a -= 1
        while b < len(t) and not t[b].isspace():
            b += 1
        if any(t[i].isalnum() for i in range(a, b)) and all(pag.mapa[i] in objetivo for i in range(a, b)
                                                            if pag.mapa[i] >= 0):
            return True
    return False


def auditar(path, password: str | None = None, progress: Progress = None) -> list[Hallazgo]:
    """Recuadros (dibujos o anotaciones opacas, o censuras sin aplicar) que tapan texto sin borrarlo.

    Un recuadro es falsa censura si debajo hay texto extraíble pintado ANTES que él.  Un título en letra
    blanca sobre un recuadro negro (texto pintado después) no lo es, ni un recuadro que solo roza el borde
    de una letra.  En una página escaneada sin texto, un recuadro oscuro pintado encima de la imagen también
    lo es, salvo que los píxeles de debajo ya estén borrados (así quedan las copias censuradas de verdad).
    """
    out: list[Hallazgo] = []
    pixeles: dict = {}
    with core.open_pdf(path, password) as doc:
        n = doc.page_count
        for i in range(n):
            _report(progress, i, n, f"Revisando la página {i + 1} de {n}")
            page = doc[i]
            area_pagina = abs(page.rect.width * page.rect.height) or 1
            tapas: list[tuple[pymupdf.Rect, float, bool]] = []
            try:
                dibujos = page.get_drawings()
            except Exception:  # noqa: BLE001
                dibujos = []
            for d in dibujos:
                if d.get("fill") is None:
                    continue
                op = d.get("fill_opacity")
                if op is not None and op < 0.9:
                    continue
                r = pymupdf.Rect(d["rect"])
                area = r.width * r.height
                if r.width < 3 or r.height < 3 or area < 20 or area >= 0.6 * area_pagina:
                    continue
                tapas.append((r, d.get("seqno", 0), _oscuro(d.get("fill"))))
            tapas += _tapas_de_anotaciones(page)
            if not tapas:
                continue
            try:
                traza = page.get_texttrace()
            except Exception:  # noqa: BLE001
                traza = []
            letras = []        # (seqno, centro x, centro y)
            for span in traza:
                for ch in span.get("chars", ()):
                    if chr(ch[0]).isspace():
                        continue
                    b = ch[3]
                    letras.append((span.get("seqno", 0), (b[0] + b[2]) / 2, (b[1] + b[3]) / 2))
            imagenes = []      # (orden de pintado, caja) de las imágenes de una página escaneada sin texto
            if not letras and core.image_coverage(page) > 0.5:
                try:
                    imagenes = [(k, pymupdf.Rect(b)) for k, (t, b) in enumerate(page.get_bboxlog())
                                if t == "fill-image"]
                except Exception:  # noqa: BLE001
                    imagenes = []
            if not letras and not imagenes:
                continue
            pag = None
            info_imagenes = None
            for r, seq, oscuro in tapas:
                zona = r + (-1, -1, 1, 1)
                if imagenes:
                    tapa_imagen = oscuro and any(k < seq and (img & r).get_area() >= 0.8 * r.get_area()
                                                 for k, img in imagenes)
                    if tapa_imagen:
                        if info_imagenes is None:
                            try:
                                info_imagenes = page.get_image_info(xrefs=True)
                            except Exception:  # noqa: BLE001
                                info_imagenes = []
                        tapa_imagen = not _imagen_ya_borrada(doc, info_imagenes, r, pixeles)
                    if tapa_imagen:
                        out.append(Hallazgo(pagina=i, rect=_visual_t(page, r), texto="", categoria="falsa",
                                            origen="texto", contexto="Recuadro que tapa una parte de la imagen "
                                            "escaneada sin borrarla"))
                    continue
                if not any(s < seq and zona.contains(pymupdf.Point(x, y)) for s, x, y in letras):
                    continue
                if pag is None:
                    pag = _datos_texto(page)
                objetivo = {k for k, b in enumerate(pag.cajas)
                            if zona.contains(pymupdf.Point((b[0] + b[2]) / 2, (b[1] + b[3]) / 2))}
                if not objetivo:
                    continue
                orden = sorted(objetivo, key=lambda k: pag.pos[k])
                texto = " ".join(pag.texto[pag.pos[orden[0]]:pag.pos[orden[-1]] + 1].split())
                if not _tapa_algo(pag, objetivo, texto):
                    continue
                rt = _ajustar(pag, objetivo, _union(pag.cajas[k] for k in objetivo))
                if rt[3] - rt[1] <= 0.3 or rt[2] <= rt[0]:
                    rt = _union(_ajustada(pag, k) for k in objetivo)
                corto = texto if len(texto) <= 60 else texto[:57] + "…"
                out.append(Hallazgo(pagina=i, rect=_visual_t(page, r), texto=texto[:500], categoria="falsa",
                                    origen="texto", contexto=f"Recuadro que tapa: «{corto}»",
                                    rect_texto=_visual_t(page, rt)))
        _report(progress, n, n, "Listo")
    return out


# --------------------------------------------------------------------------
# Datos ocultos
# --------------------------------------------------------------------------

@dataclass
class DatoOculto:
    clave: str
    nombre: str
    cantidad: int
    detalle: str
    recomendado: bool


ORDEN_OCULTOS = ("metadatos", "adjuntos", "javascript", "comentarios", "censuras", "miniaturas",
                 "formularios", "enlaces", "texto_invisible", "marcadores")

NOTA_TEXTO_INVISIBLE = ("Incluye el texto reconocido por OCR: si lo quitas, el documento dejará de ser "
                        "buscable.")

_META_NOMBRES = (("author", "Autor"), ("title", "Título"), ("subject", "Asunto"), ("keywords", "Palabras clave"),
                 ("creator", "Programa"), ("producer", "Productor"), ("creationDate", "Creado"),
                 ("modDate", "Modificado"))

_TIPOS_COMENTARIO = {
    "Text": ("nota", "notas"), "FreeText": ("cuadro de texto", "cuadros de texto"),
    "Highlight": ("resaltado", "resaltados"), "Underline": ("subrayado", "subrayados"),
    "StrikeOut": ("tachado", "tachados"), "Squiggly": ("subrayado ondulado", "subrayados ondulados"),
    "Ink": ("dibujo a mano", "dibujos a mano"), "Square": ("rectángulo", "rectángulos"),
    "Circle": ("círculo", "círculos"), "Line": ("línea", "líneas"), "Polygon": ("figura", "figuras"),
    "PolyLine": ("figura", "figuras"), "Stamp": ("sello", "sellos"), "Caret": ("marca de inserción",
                                                                               "marcas de inserción"),
}
_NO_COMENTARIOS = {pymupdf.PDF_ANNOT_LINK, pymupdf.PDF_ANNOT_WIDGET, pymupdf.PDF_ANNOT_REDACT,
                   pymupdf.PDF_ANNOT_FILE_ATTACHMENT, pymupdf.PDF_ANNOT_POPUP}

_RE_JS = re.compile(r"/S\s*/JavaScript\b")


def _fecha_pdf(v: str) -> str:
    m = re.match(r"D?:?(\d{4})(\d{2})?(\d{2})?", v or "")
    if not m:
        return v
    a, mes, dia = m.group(1), m.group(2), m.group(3)
    return f"{dia}/{mes}/{a}" if dia and mes else a


def _metadatos(doc: pymupdf.Document) -> list[str]:
    meta = doc.metadata or {}
    partes = []
    for k, nombre in _META_NOMBRES:
        v = (meta.get(k) or "").strip()
        if v:
            partes.append(f"{nombre}: {_fecha_pdf(v) if k.endswith('Date') else v}")
    try:
        if doc.get_xml_metadata().strip():
            partes.append("Metadatos XMP")
    except Exception:  # noqa: BLE001
        pass
    return partes


def _js_vacio(doc: pymupdf.Document, x: int) -> bool:
    """¿La acción JavaScript ``x`` ya no tiene código (así la deja «Quitar datos ocultos»)?"""
    try:
        tipo, valor = doc.xref_get_key(x, "JS")
        if tipo == "null":
            return True
        if tipo == "xref":
            return not (doc.xref_stream(int(valor.split()[0])) or b"").strip()
        if tipo == "string":
            return not valor.strip()
    except Exception:  # noqa: BLE001
        pass
    return False


_RE_JS_CON_CODIGO = re.compile(r"/JS\s*(?!\(\s*\)|<\s*>)\S")


def _xrefs_js(doc: pymupdf.Document) -> list[int]:
    """Objetos con JavaScript que todavía tiene código (una acción vaciada ya no cuenta)."""
    out = []
    for x in range(1, doc.xref_length()):
        try:
            fuente = doc.xref_object(x, compressed=True) or ""
            if not _RE_JS.search(fuente):
                continue
            if doc.xref_get_key(x, "S")[1] == "/JavaScript":
                if not _js_vacio(doc, x):
                    out.append(x)
            elif _RE_JS_CON_CODIGO.search(fuente):       # acción escrita dentro de otro objeto
                out.append(x)
        except Exception:  # noqa: BLE001
            continue
    return out


def _palabras_invisibles(page: pymupdf.Page) -> int:
    n = 0
    flags = pymupdf.TEXT_PRESERVE_SPANS | pymupdf.TEXT_COLLECT_STYLES
    for block in page.get_text("dict", flags=flags)["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                if span.get("text", "").strip() and _es_invisible(span):
                    n += len(span["text"].split())
    return n


def _plural(n: int, uno: str, varios: str) -> str:
    return f"{n} {uno if n == 1 else varios}"


def datos_ocultos(path, password: str | None = None, progress: Progress = None) -> list[DatoOculto]:
    """Lo que viaja dentro del PDF sin verse: metadatos, adjuntos, JavaScript, comentarios…"""
    with core.open_pdf(path, password) as doc:
        n = doc.page_count
        comentarios: dict[str, int] = defaultdict(int)
        autores: set[str] = set()
        adjuntos = list(doc.embfile_names())
        censuras = formularios = enlaces = invisibles = paginas_invisibles = miniaturas = 0
        ejemplo_enlace = ""
        for i in range(n):
            if i % 10 == 0:
                _report(progress, i, n, f"Revisando la página {i + 1} de {n}")
            page = doc[i]
            for annot in page.annots():
                t = annot.type[0]
                if t == pymupdf.PDF_ANNOT_REDACT:
                    censuras += 1
                elif t == pymupdf.PDF_ANNOT_FILE_ATTACHMENT:
                    try:
                        adjuntos.append(annot.file_info.get("filename") or "archivo adjunto")
                    except Exception:  # noqa: BLE001
                        adjuntos.append("archivo adjunto")
                elif t not in _NO_COMENTARIOS:
                    comentarios[annot.type[1]] += 1
                    autor = (annot.info or {}).get("title", "").strip()
                    if autor:
                        autores.add(autor)
            formularios += sum(1 for _ in page.widgets())
            links = page.get_links()
            enlaces += len(links)
            if not ejemplo_enlace:
                ejemplo_enlace = next((ln.get("uri") for ln in links if ln.get("uri")), "")
            w = _palabras_invisibles(page)
            if w:
                invisibles += w
                paginas_invisibles += 1
            if doc.xref_get_key(page.xref, "Thumb")[0] != "null":
                miniaturas += 1
        out: list[DatoOculto] = []
        meta = _metadatos(doc)
        if meta:
            out.append(DatoOculto("metadatos", "Metadatos (autor, título y otros)", len(meta), " · ".join(meta),
                                  True))
        if adjuntos:
            out.append(DatoOculto("adjuntos", "Archivos adjuntos", len(adjuntos), ", ".join(adjuntos[:6])
                                  + ("…" if len(adjuntos) > 6 else ""), True))
        js = _xrefs_js(doc)
        if js:
            out.append(DatoOculto("javascript", "JavaScript", len(js),
                                  "Código que se ejecuta al abrir o al usar el documento", True))
        if comentarios:
            partes = [_plural(c, *_TIPOS_COMENTARIO.get(t, ("anotación", "anotaciones")))
                      for t, c in sorted(comentarios.items(), key=lambda x: -x[1])]
            detalle = " · ".join(partes)
            if autores:
                detalle += " · de " + ", ".join(sorted(autores)[:3])
            out.append(DatoOculto("comentarios", "Comentarios y anotaciones", sum(comentarios.values()),
                                  detalle, True))
        if censuras:
            out.append(DatoOculto("censuras", "Censuras marcadas sin aplicar", censuras,
                                  "Lo que hay debajo todavía se puede copiar; al limpiar se borra de verdad",
                                  True))
        if miniaturas:
            out.append(DatoOculto("miniaturas", "Miniaturas de página", miniaturas,
                                  "Imágenes pequeñas de las páginas guardadas dentro del archivo", True))
        if formularios:
            out.append(DatoOculto("formularios", "Campos de formulario", formularios,
                                  "Se aplanan: lo escrito se sigue viendo, pero ya no se puede editar", False))
        if enlaces:
            out.append(DatoOculto("enlaces", "Enlaces", enlaces,
                                  f"Por ejemplo: {ejemplo_enlace}" if ejemplo_enlace else "Enlaces a páginas o "
                                  "sitios", False))
        if invisibles:
            out.append(DatoOculto("texto_invisible", "Texto invisible", invisibles,
                                  f"{_plural(invisibles, 'palabra', 'palabras')} en "
                                  f"{_plural(paginas_invisibles, 'página', 'páginas')}. {NOTA_TEXTO_INVISIBLE}",
                                  False))
        try:
            toc = doc.get_toc(simple=True)
        except Exception:  # noqa: BLE001
            toc = []
        if toc:
            out.append(DatoOculto("marcadores", "Marcadores (índice)", len(toc),
                                  ", ".join(t[1] for t in toc[:4]) + ("…" if len(toc) > 4 else ""), False))
        _report(progress, n, n, "Listo")
    return sorted(out, key=lambda d: ORDEN_OCULTOS.index(d.clave))


_RE_CLAVES_ACCION = re.compile(r"/(?:OpenAction|AA|A|Next|JavaScript)(?![\w#])")
# Disparadores de /AA (acciones adicionales) de páginas, anotaciones, campos y del documento.
_DISPARADORES = ("E", "X", "D", "U", "Fo", "Bl", "PO", "PC", "PV", "PI", "O", "C", "K", "F", "V",
                 "WC", "WS", "DS", "WP", "DP")


def _es_js(doc: pymupdf.Document, tipo: str, valor: str) -> bool:
    """¿El valor de una clave (en línea o por referencia) es una acción JavaScript?"""
    if tipo == "xref":
        try:
            valor = doc.xref_object(int(valor.split()[0]), compressed=True) or ""
        except Exception:  # noqa: BLE001
            return False
    elif tipo != "dict":
        return False
    return bool(_RE_JS.search(valor))


def _quitar_js(doc: pymupdf.Document) -> int:
    """Neutraliza el JavaScript: vacía el código de cada acción y corta las referencias que la disparan
    (/OpenAction del documento, /AA y /A de páginas, campos y enlaces, /Next de otras acciones y el
    árbol de nombres).  Sin referencias, el guardado con garbage=4 elimina los objetos que quedan."""
    acciones = _xrefs_js(doc)
    for x in range(1, doc.xref_length()):
        try:
            fuente = doc.xref_object(x, compressed=True) or ""
        except Exception:  # noqa: BLE001
            continue
        if _RE_JS.search(fuente) and doc.xref_get_key(x, "S")[1] == "/JavaScript":
            doc.update_object(x, "<</S/JavaScript/JS()>>")
            continue
        if not _RE_CLAVES_ACCION.search(fuente):
            continue
        try:
            # «JavaScript» como clave solo existe en el árbol de nombres de los scripts del documento
            for key in ("JavaScript", "Names/JavaScript"):
                if doc.xref_get_key(x, key)[0] != "null":
                    doc.xref_set_key(x, key, "null")
            for key in ("OpenAction", "A", "Next"):
                tipo, valor = doc.xref_get_key(x, key)
                if tipo == "array":                      # /Next puede ser una lista de acciones
                    if _RE_JS.search(valor) or any(_es_js(doc, "xref", f"{r} 0 R")
                                                   for r in re.findall(r"(\d+)\s+\d+\s+R", valor)):
                        doc.xref_set_key(x, key, "null")
                elif tipo != "null" and _es_js(doc, tipo, valor):
                    doc.xref_set_key(x, key, "null")
            tipo, valor = doc.xref_get_key(x, "AA")
            if tipo == "dict":
                for d in _DISPARADORES:
                    t, v = doc.xref_get_key(x, f"AA/{d}")
                    if t != "null" and _es_js(doc, t, v):
                        doc.xref_set_key(x, f"AA/{d}", "null")
            elif tipo == "xref":
                aa = int(valor.split()[0])
                for d in _DISPARADORES:
                    t, v = doc.xref_get_key(aa, d)
                    if t != "null" and _es_js(doc, t, v):
                        doc.xref_set_key(aa, d, "null")
        except Exception:  # noqa: BLE001 — un objeto raro no impide limpiar los demás
            continue
    return len(acciones)


def _sin_censuras_pendientes(page: pymupdf.Page):
    """Quita (y devuelve) las censuras marcadas sin aplicar, para volver a ponerlas después."""
    guardadas = []
    for annot in list(page.annots(types=[pymupdf.PDF_ANNOT_REDACT])):
        guardadas.append((pymupdf.Rect(annot.rect), (annot.colors or {}).get("fill")))
    if guardadas:
        a = page.first_annot
        while a:
            a = page.delete_annot(a) if a.type[0] == pymupdf.PDF_ANNOT_REDACT else a.next
    return guardadas


def limpiar(path, output, categorias: Sequence[str], password: str | None = None,
            progress: Progress = None) -> dict[str, int]:
    """Quita las categorías de datos ocultos elegidas.  Devuelve cuántos elementos se quitaron de cada una."""
    cats = set(categorias)
    desconocidas = cats - set(ORDEN_OCULTOS)
    if desconocidas:
        raise ValueError(f"categorías desconocidas: {', '.join(sorted(desconocidas))}")
    if not cats:
        raise UserError("Elige al menos un tipo de dato oculto para quitar.")
    hecho: dict[str, int] = defaultdict(int)
    with core._output(output) as tmp, core.open_pdf(path, password) as doc:
        n = doc.page_count
        if "metadatos" in cats:
            hecho["metadatos"] = len(_metadatos(doc))
        if "javascript" in cats:
            hecho["javascript"] = _quitar_js(doc)
        if "adjuntos" in cats:
            hecho["adjuntos"] = len(doc.embfile_names())
        # doc.scrub hace lo que corresponde a metadatos, XMP, miniaturas y adjuntos del documento.
        try:
            doc.scrub(attached_files=False, clean_pages=False, embedded_files="adjuntos" in cats,
                      hidden_text=False, javascript="javascript" in cats, metadata="metadatos" in cats,
                      redactions=False, remove_links=False, reset_fields=False, reset_responses=False,
                      thumbnails=False, xml_metadata="metadatos" in cats)
        except ValueError:
            # algunos PDF con referencias rotas no se dejan recorrer: se hace a mano
            if "metadatos" in cats:
                doc.set_metadata({})
                doc.del_xml_metadata()
            if "adjuntos" in cats:
                for name in doc.embfile_names():
                    doc.embfile_del(name)
        if "adjuntos" in cats:
            for name in doc.embfile_names():
                doc.embfile_del(name)
        if "formularios" in cats:
            hecho["formularios"] = sum(sum(1 for _ in p.widgets()) for p in doc)
            if hecho["formularios"]:
                doc.bake(annots=False, widgets=True)
        if "marcadores" in cats:
            hecho["marcadores"] = len(doc.get_toc(simple=True))
            doc.set_toc([])
        for i in range(n):
            if i % 5 == 0:
                _report(progress, i, n, f"Limpiando la página {i + 1} de {n}")
            page = doc[i]
            if "censuras" in cats:
                k = sum(1 for _ in page.annots(types=[pymupdf.PDF_ANNOT_REDACT]))
                if k:
                    core.apply_redactions(page, images=pymupdf.PDF_REDACT_IMAGE_PIXELS,
                                          graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED)
                    hecho["censuras"] += k
            if cats & {"comentarios", "adjuntos"}:
                a = page.first_annot
                while a:
                    t = a.type[0]
                    if t == pymupdf.PDF_ANNOT_FILE_ATTACHMENT and "adjuntos" in cats:
                        hecho["adjuntos"] += 1
                        a = page.delete_annot(a)
                    elif t not in _NO_COMENTARIOS and "comentarios" in cats:
                        hecho["comentarios"] += 1
                        a = page.delete_annot(a)
                    else:
                        a = a.next
            if "enlaces" in cats:
                for ln in page.get_links():
                    page.delete_link(ln)
                    hecho["enlaces"] += 1
            if "texto_invisible" in cats:
                k = _palabras_invisibles(page)
                if k:
                    pendientes = _sin_censuras_pendientes(page) if "censuras" not in cats else []
                    toda = page.rect * page.derotation_matrix if page.rotation else page.rect
                    page.add_redact_annot(toda, cross_out=False)
                    core.apply_redactions(page, images=pymupdf.PDF_REDACT_IMAGE_NONE,
                                          graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                                          text=pymupdf.PDF_REDACT_TEXT_REMOVE_INVISIBLE)
                    for r, fill in pendientes:
                        page.add_redact_annot(r, fill=fill)
                    hecho["texto_invisible"] += k
            if "miniaturas" in cats and doc.xref_get_key(page.xref, "Thumb")[0] != "null":
                doc.xref_set_key(page.xref, "Thumb", "null")
                hecho["miniaturas"] += 1
        _report(progress, n, n, "Guardando…")
        core._save_keeping_password(doc, tmp, password, garbage=4, clean=True)
    return {k: v for k, v in hecho.items() if v}
