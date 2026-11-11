"""Texto y enlaces de una página para el visor: seleccionar, copiar y seguir enlaces.

Sin interfaz: recibe páginas de PyMuPDF y devuelve datos en coordenadas visibles (las de la página
tal como se ve, con su giro aplicado), que son las mismas que usa el lienzo del visor.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

import pymupdf

from . import core

# Las ligaduras («ﬁ», «ﬂ») se separan en letras: así el texto copiado se puede buscar y corregir en Word.
WORD_FLAGS = pymupdf.TEXTFLAGS_WORDS & ~pymupdf.TEXT_PRESERVE_LIGATURES
WEB_SCHEMES = ("http://", "https://", "mailto:")
NAMED_ACTIONS = {"nextpage": 1, "prevpage": -1}


@dataclass
class Palabra:
    indice: int
    rect: tuple[float, float, float, float]   # coordenadas visibles de la página
    texto: str
    bloque: int
    linea: int

    @property
    def renglon(self) -> tuple[int, int]:
        return (self.bloque, self.linea)


@dataclass
class Enlace:
    rect: tuple[float, float, float, float]   # zona que se puede pulsar (coordenadas visibles)
    tipo: str                                 # 'pagina' | 'web' | 'otro'
    pagina: int | None = None                 # destino, base 0 (solo 'pagina')
    punto: tuple[float, float] | None = None  # altura de destino en esa página, si la trae
    url: str = ""

    @property
    def ayuda(self) -> str:
        if self.tipo == "pagina":
            return f"Ir a la página {self.pagina + 1}"
        if self.tipo == "web":
            return self.url[7:] if self.url.lower().startswith("mailto:") else self.url
        return "Por seguridad, RonyPDF no abre este tipo de enlace."


# --------------------------------------------------------------------------
# Palabras
# --------------------------------------------------------------------------

def palabras_pagina(page: pymupdf.Page) -> list[Palabra]:
    """Palabras de la página en su orden natural (bloque, renglón, palabra), que es el orden de lectura
    de MuPDF; los rectángulos ya están en coordenadas visibles (páginas giradas incluidas)."""
    out: list[Palabra] = []
    for x0, y0, x1, y1, texto, bloque, linea, _n in page.get_text("words", flags=WORD_FLAGS):
        if not texto.strip():
            continue
        r = core._visual(page, (x0, y0, x1, y1))
        out.append(Palabra(len(out), (r.x0, r.y0, r.x1, r.y1), texto, bloque, linea))
    return out


def _dist(rect, x: float, y: float) -> tuple[float, float]:
    x0, y0, x1, y1 = rect
    return max(x0 - x, 0.0, x - x1), max(y0 - y, 0.0, y - y1)


_LINEAS_CACHE: dict[int, tuple[list, list]] = {}


def _lineas(palabras: list[Palabra]) -> list[tuple[tuple[float, float, float, float], int, int]]:
    """(rectángulo, primera, última) de cada renglón. Se recuerda por lista: el ratón pregunta por la
    misma página muchas veces seguidas."""
    hit = _LINEAS_CACHE.get(id(palabras))
    if hit is not None and hit[0] is palabras:
        return hit[1]
    lineas = _calc_lineas(palabras)
    if len(_LINEAS_CACHE) > 64:
        _LINEAS_CACHE.clear()
    _LINEAS_CACHE[id(palabras)] = (palabras, lineas)
    return lineas


def _calc_lineas(palabras: list[Palabra]) -> list[tuple[tuple[float, float, float, float], int, int]]:
    out: list[list] = []
    prev = None
    for w in palabras:
        if w.renglon != prev:
            out.append([list(w.rect), w.indice, w.indice])
            prev = w.renglon
        else:
            r = out[-1][0]
            r[0], r[1], r[2], r[3] = min(r[0], w.rect[0]), min(r[1], w.rect[1]), max(r[2], w.rect[2]), \
                max(r[3], w.rect[3])
            out[-1][2] = w.indice
    return [(tuple(r), a, b) for r, a, b in out]


def palabra_en(palabras: list[Palabra], x: float, y: float, tolerancia: float = 2.0) -> int | None:
    """La palabra bajo el punto o, si cae entre dos palabras de un mismo renglón, la más cercana."""
    best, best_key = None, None
    for w in palabras:
        x0, y0, x1, y1 = w.rect
        if x0 - tolerancia <= x <= x1 + tolerancia and y0 - tolerancia <= y <= y1 + tolerancia:
            dx, dy = _dist(w.rect, x, y)
            key = (dx + dy, (x1 - x0) * (y1 - y0))
            if best_key is None or key < best_key:
                best, best_key = w.indice, key
    if best is not None:
        return best
    for (x0, y0, x1, y1), a, b in _lineas(palabras):
        if x0 - tolerancia <= x <= x1 + tolerancia and y0 - tolerancia <= y <= y1 + tolerancia:
            return min(range(a, b + 1), key=lambda i: sum(_dist(palabras[i].rect, x, y)))
    return None


def palabra_cercana(palabras: list[Palabra], x: float, y: float, vertical: bool = False) -> int | None:
    """La palabra más cercana al punto, para arrastrar una selección.

    Pesa más la distancia entre renglones que a lo largo del renglón: arrastrar a la derecha del final
    de una línea toma esa línea y no la siguiente; por encima o por debajo de todo el texto toma la
    primera o la última palabra. ``vertical`` es para páginas giradas (renglones de arriba abajo)."""
    if not palabras:
        return None
    if not vertical:
        if y < min(w.rect[1] for w in palabras):
            return 0
        if y > max(w.rect[3] for w in palabras):
            return len(palabras) - 1

    def score(w: Palabra) -> float:
        dx, dy = _dist(w.rect, x, y)
        return (dy + 3 * dx) if vertical else (dx + 3 * dy)

    return min(palabras, key=score).indice


def renglon_de(palabras: list[Palabra], i: int) -> tuple[int, int]:
    """Primera y última palabra del renglón de la palabra ``i``."""
    key = palabras[i].renglon
    a = b = i
    while a > 0 and palabras[a - 1].renglon == key:
        a -= 1
    while b < len(palabras) - 1 and palabras[b + 1].renglon == key:
        b += 1
    return a, b


def rango(palabras: list[Palabra], a: int, b: int) -> tuple[str, list[tuple[float, float, float, float]]]:
    """Texto de las palabras ``a``..``b`` (en cualquier orden) y un rectángulo por renglón.

    Espacio entre palabras, salto de línea entre renglones y una línea en blanco entre bloques."""
    if not palabras:
        return "", []
    if b < a:
        a, b = b, a
    a, b = max(0, a), min(len(palabras) - 1, b)
    partes: list[str] = []
    rects: list[list[float]] = []
    prev = None
    for w in palabras[a:b + 1]:
        key = w.renglon
        if prev is not None:
            partes.append(" " if key == prev else "\n" if key[0] == prev[0] else "\n\n")
        if key == prev:
            r = rects[-1]
            r[0], r[1], r[2], r[3] = min(r[0], w.rect[0]), min(r[1], w.rect[1]), max(r[2], w.rect[2]), \
                max(r[3], w.rect[3])
        else:
            rects.append(list(w.rect))
        partes.append(w.texto)
        prev = key
    return "".join(partes), [tuple(r) for r in rects]


def texto_pagina(page: pymupdf.Page) -> str:
    palabras = palabras_pagina(page)
    return rango(palabras, 0, len(palabras) - 1)[0]


# --------------------------------------------------------------------------
# Enlaces
# --------------------------------------------------------------------------

def enlaces_pagina(page: pymupdf.Page) -> list[Enlace]:
    """Enlaces de la página. Los de destino inválido (página fuera del documento) se ignoran."""
    n = page.parent.page_count
    out: list[Enlace] = []
    try:
        links = page.get_links()
    except Exception:  # noqa: BLE001 — un enlace dañado no debe impedir leer el documento
        return out
    for link in links:
        try:
            r = pymupdf.Rect(link.get("from"))
        except Exception:  # noqa: BLE001
            continue
        if r.is_empty or r.is_infinite:
            continue
        rect = (r.x0, r.y0, r.x1, r.y1)
        kind = link.get("kind")
        if kind in (pymupdf.LINK_GOTO, pymupdf.LINK_NAMED):
            dest = link.get("page")
            if not isinstance(dest, int) or dest < 0:
                name = str(link.get("nameddest") or link.get("name") or "").lower()
                dest = {"firstpage": 0, "lastpage": n - 1}.get(name)
                if name in NAMED_ACTIONS:
                    dest = page.number + NAMED_ACTIONS[name]
            if not isinstance(dest, int) or not 0 <= dest < n:
                continue
            to = link.get("to")
            punto = None
            if to is not None:
                try:
                    punto = (float(to.x), float(to.y))
                except (AttributeError, TypeError, ValueError):
                    punto = None
            out.append(Enlace(rect, "pagina", dest, punto))
        elif kind == pymupdf.LINK_URI:
            uri = str(link.get("uri") or "").strip()
            if uri.lower().startswith("www."):
                uri = "http://" + uri
            tipo = "web" if uri.lower().startswith(WEB_SCHEMES) and len(uri) > 7 else "otro"
            out.append(Enlace(rect, tipo, url=uri))
        else:   # abrir archivos, ejecutar programas, otros documentos…
            out.append(Enlace(rect, "otro", url=str(link.get("file") or link.get("uri") or "")))
    return out


def enlace_en(enlaces: list[Enlace], x: float, y: float) -> Enlace | None:
    for e in enlaces:
        x0, y0, x1, y1 = e.rect
        if x0 <= x <= x1 and y0 <= y <= y1:
            return e
    return None


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------

def normalizar(texto: str) -> str:
    """Minúsculas y sin tildes, para buscar sin distinguir «Anexos» de «anexos» ni «Introducción» de
    «introduccion»."""
    s = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")
