"""Impresión por imagen: planificación de hojas, vista previa e impresión.

La impresión no le entrega el PDF al controlador de la impresora: cada página se renderiza con
PyMuPDF a la resolución elegida y se pinta con QPainter sobre un QPrinter.  El mismo código pinta la
vista previa, así que lo que se ve en pantalla es exactamente lo que sale en el papel.

Toda la distribución (qué página va en qué hoja, dónde y de qué tamaño) la decide ``planificar``,
una función pura que no usa Qt ni PyMuPDF.  Las coordenadas de una hoja son fracciones (de 0 a 1)
de su área imprimible, vista en la orientación de la hoja.

Giros: «girar = 90» significa un cuarto de vuelta en sentido contrario a las agujas del reloj (el
borde superior de la página queda a la izquierda de la hoja), igual que el modo horizontal de las
impresoras.  Una hoja apaisada se imprime en el papel vertical con ese mismo giro.
"""

from __future__ import annotations

import math
import secrets
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Sequence

import pymupdf
from PySide6.QtCore import QMarginsF, QPointF, QRectF, QSizeF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPageLayout, QPageSize, QPainter, QPdfWriter, QPen
from PySide6.QtPrintSupport import QPrinter, QPrinterInfo

from . import core

MM = 72 / 25.4
MAX_PX = 16_000_000          # más píxeles que esto en una pieza se renderiza por franjas
SEPARACION = 6.0             # puntos entre páginas en «varias páginas por hoja»
POR_HOJA = (2, 4, 6, 9, 16)
CUADRICULAS = {2: [(2, 1), (1, 2)], 4: [(2, 2)], 6: [(3, 2), (2, 3)], 9: [(3, 3)], 16: [(4, 4)]}
MODOS = ("ajustar", "real", "reducir", "escala", "varias", "folleto", "poster")
CALIDADES = {150: "Borrador", 300: "Normal", 600: "Alta"}
TOLERANCIA_POSTER = 0.95     # se acepta achicar el póster hasta un 5 % si así se ahorra una fila de hojas
GANANCIA_GIRO = 1.02         # en varias por hoja, una página se gira solo si así queda 2 % más grande


def _error_impresora(nombre: str) -> str:
    return (f"No se pudo imprimir en «{nombre or 'la impresora'}». Revisa que la impresora esté "
            f"encendida y conectada.")


# --------------------------------------------------------------------------
# Modelo
# --------------------------------------------------------------------------

@dataclass
class OpcionesImpresion:
    paginas: str = ""                 # rango, p. ej. «1-3, 5, 8-»; vacío = todas
    subconjunto: str = "todas"        # todas | impares | pares
    invertir: bool = False
    copias: int = 1
    intercalar: bool = True
    modo: str = "ajustar"             # ajustar | real | reducir | escala | varias | folleto | poster
    escala: float = 100.0             # % (escala personalizada y póster)
    por_hoja: int = 2                 # 2, 4, 6, 9 o 16
    orden: str = "horizontal"         # horizontal | vertical
    borde: bool = False
    encuadernacion: str = "izquierda"  # izquierda | derecha
    solape_mm: float = 5.0
    marcas_corte: bool = True
    orientacion: str = "auto"         # auto | vertical | horizontal
    centrar: bool = True
    grises: bool = False
    anotaciones: bool = True
    ppp: int = 300                    # 150, 300 o 600
    doble_cara: str = "no"            # no | largo | corto | manual


@dataclass
class Celda:
    pagina: int | None                              # índice base 0; None = hueco en blanco
    destino: tuple[float, float, float, float]      # x, y, ancho, alto (fracción del área imprimible)
    recorte: tuple[float, float, float, float] | None = None   # x, y, ancho, alto (fracción de la página)
    girar: int = 0                                  # 0 o 90 (antihorario)


@dataclass
class Hoja:
    celdas: list[Celda]
    horizontal: bool = False
    cara: str = "frente"                            # frente | dorso
    marcas: list[tuple[float, float, float, float]] = field(default_factory=list)  # líneas de corte
    etiqueta: str = ""                              # póster: «A1», «A2»…
    caja_etiqueta: tuple[float, float, float, float] | None = None
    copia: int = 1

    @property
    def paginas(self) -> list[int]:
        return [c.pagina for c in self.celdas if c.pagina is not None]


@dataclass
class Papel:
    """Tamaño de papel en puntos (siempre vertical) y márgenes que la impresora no alcanza."""
    nombre: str
    ancho: float
    alto: float
    margenes: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)   # izquierda, arriba, derecha, abajo
    id: object = None                                                   # QPageSize.PageSizeId

    @property
    def area(self) -> tuple[float, float]:
        iz, ar, de, ab = self.margenes
        return (max(1.0, self.ancho - iz - de), max(1.0, self.alto - ar - ab))


@dataclass
class InfoImpresora:
    nombre: str
    predeterminada: bool = False
    duplex: bool = False
    color: bool = True
    papeles: list[str] = field(default_factory=list)
    papel_defecto: str = "Carta"


# --------------------------------------------------------------------------
# Planificación (pura: sin Qt ni PyMuPDF)
# --------------------------------------------------------------------------

def paginas_elegidas(n: int, op: OpcionesImpresion) -> list[int]:
    """Rango → impares o pares → orden inverso.  Índices base 0."""
    if n <= 0:
        raise core.UserError("El documento no tiene páginas para imprimir.")
    seq = core.parse_pages(op.paginas, n)
    if op.subconjunto == "impares":
        seq = [i for i in seq if (i + 1) % 2 == 1]
    elif op.subconjunto == "pares":
        seq = [i for i in seq if (i + 1) % 2 == 0]
    if not seq:
        cual = "impares" if op.subconjunto == "impares" else "pares"
        raise core.UserError(f"En las páginas elegidas no hay páginas {cual}.")
    if op.invertir:
        seq = list(reversed(seq))
    return seq


def orden_folleto(n: int) -> list[tuple[int | None, int | None]]:
    """Pares (izquierda, derecha) de cada cara de un folleto, en base 0.

    El total se completa con blancos (None) hasta un múltiplo de 4.  Para 8 páginas (en base 1):
    (8, 1), (2, 7), (6, 3), (4, 5): frente y dorso de la hoja de afuera, luego los de la siguiente.
    """
    if n <= 0:
        return []
    m = (n + 3) // 4 * 4

    def pag(i: int) -> int | None:
        return i if i < n else None

    out: list[tuple[int | None, int | None]] = []
    for k in range(m // 4):
        out.append((pag(m - 1 - 2 * k), pag(2 * k)))
        out.append((pag(2 * k + 1), pag(m - 2 - 2 * k)))
    return out


def _area(papel: tuple[float, float], horizontal: bool) -> tuple[float, float]:
    corto, largo = sorted((max(1.0, float(papel[0])), max(1.0, float(papel[1]))))
    return (largo, corto) if horizontal else (corto, largo)


def _rot_punto(x: float, y: float, r: int) -> tuple[float, float]:
    """Gira un punto (en fracciones) ``r`` grados en sentido antihorario."""
    r %= 360
    if r == 90:
        return y, 1 - x
    if r == 180:
        return 1 - x, 1 - y
    if r == 270:
        return 1 - y, x
    return x, y


def _rot_rect(rect: tuple[float, float, float, float], r: int) -> tuple[float, float, float, float]:
    x, y, w, h = rect
    xa, ya = _rot_punto(x, y, r)
    xb, yb = _rot_punto(x + w, y + h, r)
    return (min(xa, xb), min(ya, yb), abs(xb - xa), abs(yb - ya))


def _girar_conviene(w: float, h: float, cw: float, ch: float) -> bool:
    """¿La página aprovecha mejor la celda si se gira 90°?"""
    normal = min(cw / w, ch / h)
    girada = min(cw / h, ch / w)
    return girada > normal * GANANCIA_GIRO


def _colocar(w: float, h: float, x0: float, y0: float, cw: float, ch: float, escala: float | None,
             girar: int, centrar: bool, sw: float, sh: float, pagina: int) -> Celda | None:
    """Pone una página en la celda (x0, y0, cw, ch) de una hoja de sw × sh puntos.

    ``escala`` None = ajustar a la celda.  Lo que se sale de la hoja se recorta.
    """
    ew, eh = (h, w) if girar else (w, h)
    s = min(cw / ew, ch / eh) if escala is None else escala
    dw, dh = ew * s, eh * s
    x = x0 + ((cw - dw) / 2 if centrar or escala is None else 0)
    y = y0 + ((ch - dh) / 2 if centrar or escala is None else 0)
    vx0, vy0 = max(x, 0.0), max(y, 0.0)
    vx1, vy1 = min(x + dw, sw), min(y + dh, sh)
    if vx1 - vx0 <= 1e-6 or vy1 - vy0 <= 1e-6:
        return None
    recorte = None
    if vx0 > x + 1e-6 or vy0 > y + 1e-6 or vx1 < x + dw - 1e-6 or vy1 < y + dh - 1e-6:
        visible = ((vx0 - x) / dw, (vy0 - y) / dh, (vx1 - vx0) / dw, (vy1 - vy0) / dh)
        recorte = _rot_rect(visible, (360 - girar) % 360)   # de la vista girada a la página
    return Celda(pagina, (vx0 / sw, vy0 / sh, (vx1 - vx0) / sw, (vy1 - vy0) / sh), recorte, girar)


def _tamano_tipico(tamanos: Sequence[tuple[float, float]], seq: Sequence[int]) -> list[tuple[tuple[float, float], int]]:
    cuenta = Counter((round(max(tamanos[i][0], 1.0)), round(max(tamanos[i][1], 1.0))) for i in seq)
    return cuenta.most_common(6)


def _hojas_sueltas(tamanos, papel, op, seq) -> list[Hoja]:
    out = []
    escala = {"ajustar": None, "real": 1.0, "escala": max(1.0, min(1000.0, op.escala)) / 100}.get(op.modo)
    for i in seq:
        w, h = max(tamanos[i][0], 1.0), max(tamanos[i][1], 1.0)
        horizontal = op.orientacion == "horizontal"
        sw, sh = _area(papel, horizontal)
        girar = 90 if op.orientacion == "auto" and abs(w - h) > 0.5 and (w > h) != (sw > sh) else 0
        ew, eh = (h, w) if girar else (w, h)
        s = escala
        if op.modo == "reducir":
            s = min(1.0, sw / ew, sh / eh)
        celda = _colocar(w, h, 0, 0, sw, sh, s, girar, op.centrar, sw, sh, i)
        out.append(Hoja([celda] if celda else [], horizontal))
    return out


def _hojas_varias(tamanos, papel, op, seq) -> list[Hoja]:
    n = op.por_hoja
    if n not in CUADRICULAS:
        raise core.UserError("Elige 2, 4, 6, 9 o 16 páginas por hoja.")
    tipicos = _tamano_tipico(tamanos, seq)
    opciones_orient = {"auto": (False, True), "vertical": (False,), "horizontal": (True,)}[op.orientacion]
    mejor = None
    for horizontal in opciones_orient:
        sw, sh = _area(papel, horizontal)
        for cols, filas in CUADRICULAS[n]:
            cw = (sw - SEPARACION * (cols - 1)) / cols
            ch = (sh - SEPARACION * (filas - 1)) / filas
            total = peso = 0.0
            for (w, h), k in tipicos:
                s = min(cw / w, ch / h)
                if op.orientacion == "auto":      # girar páginas sueltas solo si se gana bastante
                    s = max(s, min(cw / h, ch / w) / GANANCIA_GIRO)
                total += s * k
                peso += k
            puntaje = total / peso
            if mejor is None or puntaje > mejor[0] * 1.001:
                mejor = (puntaje, horizontal, cols, filas)
    _, horizontal, cols, filas = mejor
    sw, sh = _area(papel, horizontal)
    cw = (sw - SEPARACION * (cols - 1)) / cols
    ch = (sh - SEPARACION * (filas - 1)) / filas
    out = []
    for inicio in range(0, len(seq), n):
        celdas = []
        for k, i in enumerate(seq[inicio:inicio + n]):
            if op.orden == "vertical":
                col, fila = k // filas, k % filas
            else:
                col, fila = k % cols, k // cols
            w, h = max(tamanos[i][0], 1.0), max(tamanos[i][1], 1.0)
            girar = 90 if op.orientacion == "auto" and _girar_conviene(w, h, cw, ch) else 0
            c = _colocar(w, h, col * (cw + SEPARACION), fila * (ch + SEPARACION), cw, ch, None, girar,
                         True, sw, sh, i)
            if c:
                celdas.append(c)
        out.append(Hoja(celdas, horizontal))
    return out


def _hojas_folleto(tamanos, papel, op, seq) -> list[Hoja]:
    tipicos = _tamano_tipico(tamanos, seq)
    (w0, h0), _ = tipicos[0]
    lado_a_lado = w0 <= h0                   # páginas verticales: lado a lado en hoja apaisada
    horizontal = lado_a_lado
    sw, sh = _area(papel, horizontal)
    if lado_a_lado:
        mitades = [(0.0, 0.0, sw / 2, sh), (sw / 2, 0.0, sw / 2, sh)]
    else:                                    # páginas apaisadas: arriba y abajo en hoja vertical
        mitades = [(0.0, 0.0, sw, sh / 2), (0.0, sh / 2, sw, sh / 2)]
    out = []
    for izq, der in orden_folleto(len(seq)):
        par = (der, izq) if op.encuadernacion == "derecha" else (izq, der)
        celdas = []
        for pos, (x0, y0, cw, ch) in zip(par, mitades):
            if pos is None:
                celdas.append(Celda(None, (x0 / sw, y0 / sh, cw / sw, ch / sh)))
                continue
            i = seq[pos]
            w, h = max(tamanos[i][0], 1.0), max(tamanos[i][1], 1.0)
            c = _colocar(w, h, x0, y0, cw, ch, None, 0, True, sw, sh, i)
            if c:
                celdas.append(c)
        out.append(Hoja(celdas, horizontal))
    return out


def _piezas(largo: float, hoja: float, solape: float) -> int:
    c = 1
    while (largo + (c - 1) * solape) / c > hoja + 1e-6 and c < 200:
        c += 1
    return c


def cuadricula_poster(tamano: tuple[float, float], papel: tuple[float, float], op: OpcionesImpresion,
                      horizontal: bool | None = None) -> tuple[int, int, float, bool]:
    """Columnas, filas, escala real (%) y orientación de las hojas de un póster.

    Si achicando el póster un poco (hasta un 5 %) se ahorra una fila o una columna de hojas, se
    achica: imprimir otra fila completa por unos milímetros sería desperdiciar papel.
    """
    w, h = max(tamano[0], 1.0), max(tamano[1], 1.0)
    esc = max(10.0, min(2000.0, op.escala)) / 100
    W, H = w * esc, h * esc
    candidatos = (horizontal,) if horizontal is not None else {
        "auto": (False, True), "vertical": (False,), "horizontal": (True,)}[op.orientacion]
    mejor = None
    for horiz in candidatos:
        sw, sh = _area(papel, horiz)
        solape = min(max(0.0, op.solape_mm) * MM, min(sw, sh) / 3)
        cols, filas = _piezas(W, sw, solape), _piezas(H, sh, solape)
        opcion = (cols * filas, 1.0, cols, filas)
        for dc in (0, 1):
            for df in (0, 1):
                c2, f2 = cols - dc, filas - df
                if c2 < 1 or f2 < 1 or (dc == df == 0):
                    continue
                f = min((c2 * sw - (c2 - 1) * solape) / W, (f2 * sh - (f2 - 1) * solape) / H, 1.0)
                if f >= TOLERANCIA_POSTER and c2 * f2 < opcion[0]:
                    opcion = (c2 * f2, f, c2, f2)
        if mejor is None or opcion[0] < mejor[0][0]:
            mejor = (opcion, horiz)
    (_, f, cols, filas), horiz = mejor
    return cols, filas, esc * f * 100, horiz


def _letra(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _hojas_poster(tamanos, papel, op, seq) -> list[Hoja]:
    out = []
    for i in seq:
        w, h = max(tamanos[i][0], 1.0), max(tamanos[i][1], 1.0)
        cols, filas, esc_real, horizontal = cuadricula_poster((w, h), papel, op)
        sw, sh = _area(papel, horizontal)
        solape = min(max(0.0, op.solape_mm) * MM, min(sw, sh) / 3)
        W, H = w * esc_real / 100, h * esc_real / 100
        cw = min(sw, (W + (cols - 1) * solape) / cols)
        ch = min(sh, (H + (filas - 1) * solape) / filas)
        x0, y0 = (sw - cw) / 2, (sh - ch) / 2
        destino = (x0 / sw, y0 / sh, cw / sw, ch / sh)
        varias = cols * filas > 1
        for f in range(filas):
            for c in range(cols):
                rx, ry = c * (cw - solape) / W, f * (ch - solape) / H
                recorte = (rx, ry, min(cw / W, 1 - rx), min(ch / H, 1 - ry))
                hoja = Hoja([Celda(i, destino, recorte if varias else None)], horizontal)
                if op.marcas_corte and varias:
                    largo = min(6 * MM, cw / 8, ch / 8)
                    fuera_x, fuera_y = min(x0, largo), min(y0, largo)
                    lineas = []
                    xs = ([x0 + solape] if c > 0 else []) + ([x0 + cw - solape] if c < cols - 1 else [])
                    ys = ([y0 + solape] if f > 0 else []) + ([y0 + ch - solape] if f < filas - 1 else [])
                    for x in xs:
                        lineas += [(x, y0 - fuera_y, x, y0 + largo), (x, y0 + ch - largo, x, y0 + ch + fuera_y)]
                    for y in ys:
                        lineas += [(x0 - fuera_x, y, x0 + largo, y), (x0 + cw - largo, y, x0 + cw + fuera_x, y)]
                    hoja.marcas = [(a / sw, b / sh, cc / sw, d / sh) for a, b, cc, d in lineas]
                    if solape >= 4:
                        hoja.etiqueta = f"{_letra(f)}{c + 1}"
                        # en una franja que se recorta o queda tapada al armar el póster
                        if c or f:
                            bx, by = x0, y0
                        else:
                            bx, by = x0 + cw - solape, y0 + ch - solape
                        hoja.caja_etiqueta = (bx / sw, by / sh, solape / sw, solape / sh)
                out.append(hoja)
    return out


def planificar(tamanos: Sequence[tuple[float, float]], papel: tuple[float, float],
               op: OpcionesImpresion) -> list[Hoja]:
    """Distribuye las páginas en hojas.

    ``tamanos``: ancho y alto (puntos) de cada página tal como se ve (con /Rotate aplicado).
    ``papel``: ancho y alto (puntos) del área imprimible del papel.
    Devuelve una hoja por cara impresa, ya con las copias y, en doble cara, con un blanco al final
    de cada copia si el número de caras es impar.
    """
    seq = paginas_elegidas(len(tamanos), op)
    if op.modo in ("ajustar", "real", "reducir", "escala"):
        caras = _hojas_sueltas(tamanos, papel, op, seq)
    elif op.modo == "varias":
        caras = _hojas_varias(tamanos, papel, op, seq)
    elif op.modo == "folleto":
        caras = _hojas_folleto(tamanos, papel, op, seq)
    elif op.modo == "poster":
        caras = _hojas_poster(tamanos, papel, op, seq)
    else:
        raise core.UserError(f"Modo de impresión desconocido: «{op.modo}».")

    dos_caras = op.doble_cara in ("largo", "corto", "manual")
    if dos_caras and len(caras) % 2:
        caras.append(Hoja([], caras[-1].horizontal))
    paso = 2 if dos_caras else 1
    papeles = [caras[k:k + paso] for k in range(0, len(caras), paso)]   # hojas de papel de una copia
    copias = max(1, int(op.copias))
    if op.intercalar or copias == 1:
        orden = [(c, p) for c in range(copias) for p in papeles]
    else:
        orden = [(c, p) for p in papeles for c in range(copias)]
    out: list[Hoja] = []
    for c, hojas in orden:
        for hoja in hojas:
            out.append(replace(hoja, celdas=list(hoja.celdas), marcas=list(hoja.marcas), copia=c + 1))
    alterna = dos_caras or op.modo == "folleto"
    for k, hoja in enumerate(out):
        hoja.cara = "dorso" if alterna and k % 2 else "frente"
    return out


def contar(hojas: Sequence[Hoja], op: OpcionesImpresion) -> tuple[int, int]:
    """(caras impresas, hojas de papel)."""
    caras = len(hojas)
    return caras, (math.ceil(caras / 2) if op.doble_cara in ("largo", "corto", "manual") else caras)


def seleccionar_caras(hojas: Sequence[Hoja], caras: str = "todas", invertir_dorso: bool = False) -> list[Hoja]:
    """Para la doble cara manual: solo los frentes o solo los dorsos (estos, si se pide, al revés)."""
    if caras == "todas":
        return list(hojas)
    out = [h for h in hojas if h.cara == caras]
    if caras == "dorso" and invertir_dorso:
        out.reverse()
    return out


# --------------------------------------------------------------------------
# Papel e impresoras
# --------------------------------------------------------------------------

_P = QPageSize.PageSizeId
PAPELES: dict[str, object] = {
    "Carta": _P.Letter, "Oficio": _P.Legal, "A4": _P.A4, "A3": _P.A3, "A5": _P.A5,
    "Tabloide": _P.Tabloid, "Media carta": _P.Statement, "Ejecutivo": _P.ExecutiveStandard, "B5": _P.B5,
}
_NOMBRE_POR_ID = {v: k for k, v in PAPELES.items()}


def _nombre_papel(ps: QPageSize) -> str:
    return _NOMBRE_POR_ID.get(ps.id(), ps.name())


def _qpage(p: Papel) -> QPageSize:
    if p.id is not None and p.id != _P.Custom:
        return QPageSize(p.id)
    return QPageSize(QSizeF(p.ancho, p.alto), QPageSize.Unit.Point, p.nombre)


def margenes_impresora(nombre: str, ps: QPageSize) -> tuple[float, float, float, float]:
    """Márgenes (puntos) que la impresora no puede imprimir con ese papel.  No envía nada."""
    pi = QPrinterInfo.printerInfo(nombre) if nombre else QPrinterInfo()
    if pi.isNull():
        return (0.0, 0.0, 0.0, 0.0)
    pr = QPrinter(pi, QPrinter.PrinterMode.HighResolution)
    pr.setPageSize(ps)
    pr.setPageOrientation(QPageLayout.Orientation.Portrait)
    lay = pr.pageLayout()
    pr.setPageMargins(lay.minimumMargins(), lay.units())     # igual que en preparar_impresora
    pr.setFullPage(False)
    papel_r, pagina = pr.paperRect(QPrinter.Unit.Point), pr.pageRect(QPrinter.Unit.Point)
    return (max(0.0, pagina.left() - papel_r.left()), max(0.0, pagina.top() - papel_r.top()),
            max(0.0, papel_r.right() - pagina.right()), max(0.0, papel_r.bottom() - pagina.bottom()))


def papel(valor, impresora: str | None = None) -> Papel:
    """Convierte «Carta», (ancho, alto) en puntos o un Papel en un Papel (con los márgenes de la impresora)."""
    if isinstance(valor, Papel):
        return valor
    if isinstance(valor, (tuple, list)):
        w, h = sorted((float(valor[0]), float(valor[1])))
        return Papel("Personalizado", w, h)
    nombre = str(valor)
    ps = None
    if nombre in PAPELES:
        ps = QPageSize(PAPELES[nombre])
    elif impresora:
        pi = QPrinterInfo.printerInfo(impresora)
        for s in ([] if pi.isNull() else pi.supportedPageSizes()):
            if s.name() == nombre or _nombre_papel(s) == nombre:
                ps = s
                break
    if ps is None or not ps.isValid():
        if nombre in core.PAGE_SIZES:
            w, h = core.PAGE_SIZES[nombre]
            return Papel(nombre, w, h)
        raise core.UserError(f"No se reconoce el tamaño de papel «{nombre}».")
    size = ps.size(QPageSize.Unit.Point)
    w, h = sorted((size.width(), size.height()))
    marg = margenes_impresora(impresora, ps) if impresora else (0.0, 0.0, 0.0, 0.0)
    return Papel(nombre, w, h, marg, ps.id())


def impresoras() -> list[InfoImpresora]:
    """Impresoras instaladas (la predeterminada primero).  Solo consulta, no envía nada."""
    out = []
    for pi in QPrinterInfo.availablePrinters():
        try:
            modos = pi.supportedDuplexModes()
            duplex = any(m in (QPrinter.DuplexMode.DuplexLongSide, QPrinter.DuplexMode.DuplexShortSide)
                         for m in modos)
            colores = pi.supportedColorModes()
            color = (QPrinter.ColorMode.Color in colores) if colores else True
            papeles = []
            for s in pi.supportedPageSizes():
                n = _nombre_papel(s)
                if n and n not in papeles:
                    papeles.append(n)
            dps = pi.defaultPageSize()
            defecto = _nombre_papel(dps) if dps.isValid() else ""
        except Exception:  # noqa: BLE001 — un controlador que falla no debe tumbar la lista
            duplex, color, papeles, defecto = False, True, [], ""
        papeles = papeles or list(PAPELES)
        if defecto not in papeles:
            defecto = "Carta" if "Carta" in papeles else papeles[0]
        out.append(InfoImpresora(pi.printerName(), pi.isDefault(), duplex, color, papeles, defecto))
    out.sort(key=lambda i: (not i.predeterminada, i.nombre.lower()))
    return out


def preparar_impresora(printer: QPrinter, op: OpcionesImpresion, p: Papel, titulo: str = "") -> None:
    """Papel, márgenes, color, doble cara y nombre del trabajo.  No imprime nada.

    También acepta un QPdfWriter (para guardar el PDF listo para imprimir).
    """
    printer.setPageSize(_qpage(p))
    printer.setPageOrientation(QPageLayout.Orientation.Portrait)
    if not isinstance(printer, QPrinter):
        printer.setResolution(int(op.ppp))
        printer.setPageMargins(QMarginsF(*p.margenes), QPageLayout.Unit.Point)
        printer.setCreator("RonyPDF")
        if titulo:
            printer.setTitle(titulo)
        return
    if printer.outputFormat() == QPrinter.OutputFormat.PdfFormat:
        printer.setResolution(int(op.ppp))
        printer.setPageMargins(QMarginsF(*p.margenes), QPageLayout.Unit.Point)
    else:
        lay = printer.pageLayout()           # el área imprimible más grande que admite la impresora
        printer.setPageMargins(lay.minimumMargins(), lay.units())
    printer.setFullPage(False)
    printer.setColorMode(QPrinter.ColorMode.GrayScale if op.grises else QPrinter.ColorMode.Color)
    printer.setDuplex({"largo": QPrinter.DuplexMode.DuplexLongSide,
                       "corto": QPrinter.DuplexMode.DuplexShortSide}.get(op.doble_cara,
                                                                       QPrinter.DuplexMode.DuplexNone))
    printer.setCreator("RonyPDF")
    if titulo:
        printer.setDocName(titulo)


# --------------------------------------------------------------------------
# Pintado (vista previa e impresora)
# --------------------------------------------------------------------------

def _tamanos(doc: pymupdf.Document) -> list[tuple[float, float]]:
    return [(p.rect.width, p.rect.height) for p in doc]


def _qimage(pix: pymupdf.Pixmap) -> QImage:
    fmt = QImage.Format.Format_Grayscale8 if pix.n == 1 else QImage.Format.Format_RGB888
    return QImage(pix.samples, pix.width, pix.height, pix.stride, fmt).copy()


def _pintar_celda(painter: QPainter, page: pymupdf.Page, celda: Celda, target: QRectF, rot: int,
                  op: OpcionesImpresion, k: float) -> None:
    """Renderiza la página (o su recorte) girada ``rot`` grados y la pinta en ``target``."""
    pr = page.rect
    rx, ry, rw, rh = celda.recorte or (0.0, 0.0, 1.0, 1.0)
    tw_px = max(1, round(target.width() * k))
    th_px = max(1, round(target.height() * k))
    franjas = max(1, math.ceil(tw_px * th_px / MAX_PX))
    cs = pymupdf.csGRAY if op.grises else pymupdf.csRGB
    for b in range(franjas):
        t0, t1 = b / franjas, (b + 1) / franjas
        fx, fy, fw, fh = _rot_rect((0.0, t0, 1.0, t1 - t0), (360 - rot) % 360)
        clip = pymupdf.Rect(pr.x0 + (rx + fx * rw) * pr.width, pr.y0 + (ry + fy * rh) * pr.height,
                            pr.x0 + (rx + (fx + fw) * rw) * pr.width, pr.y0 + (ry + (fy + fh) * rh) * pr.height)
        ancho_visto = clip.width if rot in (0, 180) else clip.height
        if ancho_visto <= 0:
            continue
        z = tw_px / ancho_visto
        pix = page.get_pixmap(matrix=pymupdf.Matrix(z, z).prerotate(-rot), clip=clip, alpha=False,
                              annots=op.anotaciones, colorspace=cs)
        img = _qimage(pix)
        del pix
        destino = QRectF(target.x(), target.y() + target.height() * t0, target.width(),
                         target.height() * (t1 - t0))
        painter.drawImage(destino, img, QRectF(0, 0, img.width(), img.height()))


def pintar_hoja(painter: QPainter, area: QRectF, doc: pymupdf.Document, hoja: Hoja, op: OpcionesImpresion,
                ppp: int, voltear: bool = False) -> None:
    """Pinta una hoja en ``area`` (el área imprimible, en coordenadas del pintor).

    Si la orientación del área no es la de la hoja, la hoja se gira 90° (antihorario) para
    caber.  ``voltear`` la gira 180° más (dorsos de la doble cara manual en hojas apaisadas).
    ``ppp`` es la resolución máxima a la que se renderizan las páginas.
    """
    if area.width() <= 0 or area.height() <= 0:
        return
    area_horizontal = area.width() > area.height()
    giro = 90 if hoja.horizontal != area_horizontal and abs(area.width() - area.height()) > 0.5 else 0
    if voltear:
        giro = (giro + 180) % 360
    dpi = painter.device().logicalDpiX() or 96
    k = min(float(ppp), float(dpi)) / dpi          # píxeles renderizados por unidad del dispositivo

    def rect(frac):
        x, y, w, h = _rot_rect(frac, giro)
        return QRectF(area.x() + x * area.width(), area.y() + y * area.height(), w * area.width(),
                      h * area.height())

    for celda in hoja.celdas:
        if celda.pagina is None or not (0 <= celda.pagina < doc.page_count):
            continue
        target = rect(celda.destino)
        rot = (celda.girar + giro) % 360
        _pintar_celda(painter, doc[celda.pagina], celda, target, rot, op, k)
        if op.borde and op.modo == "varias":
            painter.save()
            pen = QPen(QColor("#4B5563"), max(1.0, dpi * 0.6 / 72))
            pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(target)
            painter.restore()

    if hoja.marcas:
        painter.save()
        for color, ancho in (("#FFFFFF", 1.8), ("#111111", 0.6)):
            pen = QPen(QColor(color), max(1.0, dpi * ancho / 72))
            pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            painter.setPen(pen)
            for x1, y1, x2, y2 in hoja.marcas:
                ax, ay = _rot_punto(x1, y1, giro)
                bx, by = _rot_punto(x2, y2, giro)
                painter.drawLine(QPointF(area.x() + ax * area.width(), area.y() + ay * area.height()),
                                 QPointF(area.x() + bx * area.width(), area.y() + by * area.height()))
        painter.restore()

    if hoja.etiqueta and hoja.caja_etiqueta:
        caja = rect(hoja.caja_etiqueta)
        lado = min(caja.width(), caja.height())
        if lado >= 3:
            painter.save()
            painter.translate(caja.center())
            painter.rotate(-giro)
            font = QFont("Segoe UI")
            font.setPixelSize(max(3, int(lado * 0.42)))
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor("#6B7280"))
            painter.drawText(QRectF(-lado / 2, -lado / 2, lado, lado), int(Qt.AlignmentFlag.AlignCenter),
                             hoja.etiqueta)
            painter.restore()


def imagen_hoja(doc: pymupdf.Document, hoja: Hoja, op: OpcionesImpresion, papel_: Papel,
                max_px: int = 900) -> QImage:
    """La hoja de papel completa (blanca, con su área imprimible) tal como saldrá de la impresora."""
    W, H = papel_.ancho, papel_.alto
    iz, ar, de, ab = papel_.margenes
    if hoja.horizontal != (W > H) and abs(W - H) > 0.5:
        W, H = H, W
        iz, ar, de, ab = ab, iz, ar, de     # márgenes del papel vertical vistos en la hoja apaisada
    esc = max(1, int(max_px)) / max(W, H)
    img = QImage(max(1, round(W * esc)), max(1, round(H * esc)), QImage.Format.Format_RGB32)
    dpm = round(esc * 72 / 0.0254)
    img.setDotsPerMeterX(dpm)
    img.setDotsPerMeterY(dpm)
    img.fill(QColor("#FFFFFF"))
    pintor = QPainter(img)
    pintor.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
                          | QPainter.RenderHint.TextAntialiasing)
    area = QRectF(iz * esc, ar * esc, max(1.0, (W - iz - de) * esc), max(1.0, (H - ar - ab) * esc))
    if max(iz, ar, de, ab) > 0.5:
        pen = QPen(QColor("#D5D9DF"), 1, Qt.PenStyle.DotLine)
        pintor.setPen(pen)
        pintor.drawRect(area.adjusted(-0.5, -0.5, 0.5, 0.5))
    try:
        pintar_hoja(pintor, area, doc, hoja, op, 10_000)
    finally:
        pintor.end()
    return img


def vista_previa(path, indice_hoja: int, op: OpcionesImpresion, papel_, max_px: int = 900,
                 password: str | None = None) -> QImage:
    """Imagen de la hoja número ``indice_hoja`` (base 0) tal como saldrá impresa."""
    p = papel(papel_)
    with core.open_pdf(path, password) as doc:
        hojas = planificar(_tamanos(doc), p.area, op)
        i = max(0, min(int(indice_hoja), len(hojas) - 1))
        return imagen_hoja(doc, hojas[i], op, p, max_px)


# --------------------------------------------------------------------------
# Imprimir
# --------------------------------------------------------------------------

def _orientar(dispositivo, horizontal: bool) -> None:
    dispositivo.setPageOrientation(QPageLayout.Orientation.Landscape if horizontal
                                   else QPageLayout.Orientation.Portrait)


def _area_px(dispositivo) -> QRectF:
    """Área imprimible en píxeles del dispositivo (el pintor empieza en su esquina superior izquierda)."""
    if isinstance(dispositivo, QPrinter):
        r = dispositivo.pageRect(QPrinter.Unit.DevicePixel)
    else:
        r = dispositivo.pageLayout().paintRectPixels(dispositivo.resolution())
    return QRectF(0, 0, r.width(), r.height())


def _enviar(doc: pymupdf.Document, dispositivo, hojas: Sequence[Hoja], op: OpcionesImpresion,
            progress: Callable | None, voltear_dorsos: bool = False) -> int:
    """Pinta las hojas en un QPrinter o en un QPdfWriter, una por página."""
    n = len(hojas)
    if n == 0:
        raise core.UserError("No hay hojas para imprimir.")
    impresora = isinstance(dispositivo, QPrinter)
    nombre = dispositivo.printerName() if impresora else ""
    # En un PDF cada hoja apaisada sale como página apaisada; en una impresora el papel queda
    # vertical y la hoja se pinta girada (así no depende de cómo maneje el controlador un cambio
    # de orientación a mitad del trabajo, y la doble cara sale igual en todas las impresoras).
    pdf = not impresora or dispositivo.outputFormat() == QPrinter.OutputFormat.PdfFormat
    error = (_error_impresora(nombre) if impresora and not pdf
             else "No se pudo escribir el PDF. Revisa que la carpeta permita guardar archivos.")
    if pdf:
        _orientar(dispositivo, hojas[0].horizontal)
    pintor = QPainter()
    if not pintor.begin(dispositivo):
        raise core.UserError(error)
    try:
        for k, hoja in enumerate(hojas):
            core._report(progress, k, n, f"Hoja {k + 1} de {n}")
            if k:
                if pdf:
                    _orientar(dispositivo, hoja.horizontal)
                if not dispositivo.newPage():
                    raise core.UserError(error)
            pintar_hoja(pintor, _area_px(dispositivo), doc, hoja, op, op.ppp,
                        voltear=voltear_dorsos and hoja.cara == "dorso" and hoja.horizontal)
        core._report(progress, n, n, "Guardando…" if pdf else "Enviando a la impresora…")
    except BaseException:
        if impresora:
            dispositivo.abort()             # cancela el trabajo que ya estaba en la cola
        raise
    finally:
        if pintor.isActive():
            pintor.end()
    if impresora and dispositivo.printerState() == QPrinter.PrinterState.Error:
        raise core.UserError(error)
    return n


def imprimir(path, printer: QPrinter, op: OpcionesImpresion, password: str | None = None,
             progress: Callable | None = None, caras: str = "todas", invertir_dorso: bool = False) -> int:
    """Imprime en ``printer`` (ya preparado con ``preparar_impresora``).  Devuelve las hojas enviadas.

    ``caras``: «todas», o «frente» / «dorso» para la doble cara manual.

    Se llama desde ``ctx.run_task``, es decir, en un hilo que no es el de la interfaz: Qt permite
    pintar sobre un QPrinter desde otro hilo (igual que sobre un QImage) y así la ventana no se
    congela con documentos largos y se puede cancelar entre hojas.  El QPrinter se crea y se
    prepara en el hilo principal; aquí solo se pinta y nunca se muestra un diálogo.
    """
    plan_op = op
    if isinstance(printer, QPrinter):                 # (también acepta un QPdfWriter)
        nombre = printer.printerName()
        nativa = printer.outputFormat() == QPrinter.OutputFormat.NativeFormat
        if nativa and nombre and QPrinterInfo.printerInfo(nombre).isNull():
            raise core.UserError(_error_impresora(nombre))
        if nativa and op.copias > 1 and op.doble_cara != "manual" and printer.supportsMultipleCopies():
            printer.setCopyCount(int(op.copias))      # la impresora hace las copias
            printer.setCollateCopies(bool(op.intercalar))
            plan_op = replace(op, copias=1)
        else:
            printer.setCopyCount(1)                   # las copias van repetidas en el trabajo
    with core.open_pdf(path, password) as doc:
        r = _area_px(printer)
        res = printer.resolution() or 300
        hojas = planificar(_tamanos(doc), (r.width() * 72 / res, r.height() * 72 / res), plan_op)
        hojas = seleccionar_caras(hojas, caras, invertir_dorso)
        return _enviar(doc, printer, hojas, op, progress,
                       voltear_dorsos=op.doble_cara == "manual" and caras == "dorso")


def imprimir_a_pdf(path, output, op: OpcionesImpresion, papel_="Carta", password: str | None = None,
                   progress: Callable | None = None) -> int:
    """Guarda un PDF listo para imprimir: una página por cada cara, igual a la vista previa.

    Usa QPdfWriter (el mismo motor PDF de QPrinter) para no depender del sistema de impresión de
    Windows: funciona aunque no haya impresoras o el servicio de impresión esté detenido.
    """
    p = papel(papel_)
    with core._output(output) as tmp:
        w = QPdfWriter(tmp)
        preparar_impresora(w, op, p, Path(str(path)).stem)
        try:
            with core.open_pdf(path, password) as doc:
                r = _area_px(w)
                res = w.resolution()
                hojas = planificar(_tamanos(doc), (r.width() * 72 / res, r.height() * 72 / res), op)
                n = _enviar(doc, w, hojas, op, progress)
                permisos = doc.permissions
        finally:
            del w                               # cierra el archivo antes de moverlo a su destino
        if password and core.needs_password(path):
            _cifrar_como_el_original(tmp, password, permisos)
    return n


def _cifrar_como_el_original(pdf: str, password: str, permisos: int) -> None:
    """La copia lista para imprimir pide la misma contraseña que el original (regla del proyecto)."""
    with pymupdf.open(pdf) as d:
        datos = d.tobytes(garbage=3, deflate=True, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=password,
                          owner_pw=secrets.token_urlsafe(24), permissions=permisos)
    Path(pdf).write_bytes(datos)
