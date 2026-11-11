"""Íconos premium en azulejo y pequeñas ilustraciones dibujadas con QPainter.

Los azulejos salen de `assets/tiles/<clave>.png` (generados con IA y recortados por
`scripts/slice_icons.py`). Si falta alguno, se dibuja uno equivalente: degradado del
color de su sección con el símbolo vectorial en blanco.
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap

from .icons import pixmap as line_pixmap


def assets_dir() -> Path:
    override = os.environ.get("RONYPDF_TILES")  # para previsualizar otros juegos de íconos
    if override:
        return Path(override)
    base = getattr(sys, "_MEIPASS", None)
    root = Path(base) / "ronypdf" if base else Path(__file__).resolve().parent
    return root / "assets" / "tiles"


EXTRA: dict = {}   # azulejos de los módulos *_ui.py: clave → función que devuelve el QPixmap

# clave → (color claro, color oscuro, símbolo vectorial de respaldo)
CORAL = ("#F26B6B", "#D63A3A")
BLUE = ("#5B9BFF", "#2563EB")
VIOLET = ("#A78BFA", "#7C3AED")
TEAL = ("#2DD4BF", "#0F9488")
AMBER = ("#FBBF24", "#D97706")
GRAPHITE = ("#64748B", "#1E293B")
TILES: dict[str, tuple[tuple[str, str], str]] = {
    "logo": (CORAL, "pdf"), "home": (CORAL, "home"), "viewer": (BLUE, "pen"),
    "merge": (VIOLET, "merge"), "split": (VIOLET, "split"), "organize": (VIOLET, "organize"),
    "compress": (CORAL, "compress"), "ocr": (TEAL, "ocr"), "toimage": (TEAL, "toimage"),
    "fromimage": (TEAL, "fromimage"), "extract": (TEAL, "text"), "watermark": (AMBER, "watermark"),
    "numbers": (AMBER, "numbers"), "metadata": (AMBER, "info"), "protect": (GRAPHITE, "lock"),
    "unlock": (GRAPHITE, "unlock"), "sec_convert": (TEAL, "reverse"), "sec_edit": (AMBER, "pen"),
    "sec_security": (GRAPHITE, "lock"), "sec_organize": (VIOLET, "organize"),
    "folder": (("#7DD3FC", "#0284C7"), "folder"), "recent": (("#818CF8", "#4F46E5"), "clock"),
    "search": (("#94A3B8", "#475569"), "search"), "signature": (BLUE, "pen"),
    "offline": (("#4ADE80", "#15803D"), "lock"),
}
# Orden de la hoja generada (5 × 5, de izquierda a derecha y de arriba abajo).
SHEET_ORDER = ["logo", "home", "viewer", "merge", "split",
               "organize", "compress", "ocr", "toimage", "fromimage",
               "extract", "watermark", "numbers", "metadata", "protect",
               "unlock", "sec_convert", "sec_edit", "sec_security", "sec_organize",
               "folder", "recent", "search", "signature", "offline"]


def _fallback(key: str, px: int) -> QPixmap:
    (light, dark), glyph = TILES.get(key, (CORAL, "pdf"))
    pm = QPixmap(px, px)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r = QRectF(0.5, 0.5, px - 1, px - 1)
    grad = QLinearGradient(0, 0, 0, px)
    grad.setColorAt(0, QColor(light))
    grad.setColorAt(1, QColor(dark))
    path = QPainterPath()
    path.addRoundedRect(r, px * 0.235, px * 0.235)
    p.fillPath(path, grad)
    # brillo superior sutil
    p.setPen(QPen(QColor(255, 255, 255, 70), max(1.0, px / 64)))
    p.drawPath(path)
    g = line_pixmap(glyph, "#FFFFFF", int(px * 0.58 / 2) or 1, 2.2)  # 2x dpr ya incluido
    gw = g.width() / g.devicePixelRatio()
    gh = g.height() / g.devicePixelRatio()
    target = QRectF((px - gw * 2) / 2, (px - gh * 2) / 2, gw * 2, gh * 2)
    p.drawPixmap(target, g, QRectF(0, 0, g.width(), g.height()))
    p.end()
    return pm


@lru_cache(maxsize=None)
def _source(key: str) -> QPixmap:
    if key in EXTRA:
        return EXTRA[key]()
    f = assets_dir() / f"{key}.png"
    if f.exists():
        pm = QPixmap(str(f))
        if not pm.isNull():
            return pm
    return _fallback(key, 256)


@lru_cache(maxsize=None)
def tile(key: str, size: int, dpr: float = 2.0) -> QPixmap:
    """Azulejo listo para pintar a ``size`` px lógicos (nítido en pantallas de alta densidad)."""
    src = _source(key)
    px = max(1, int(round(size * dpr)))
    pm = src.scaled(px, px, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    pm.setDevicePixelRatio(dpr)
    return pm


@lru_cache(maxsize=None)
def tile_icon(key: str, size: int = 22) -> QIcon:
    ic = QIcon()
    for dpr in (1.0, 2.0, 3.0):
        ic.addPixmap(tile(key, size, dpr))
    return ic


def app_icon() -> QIcon:
    ic = QIcon()
    for s in (16, 20, 24, 32, 40, 48, 64, 128, 256):
        ic.addPixmap(tile("logo", s, 1.0))
    return ic


def has_ai_tiles() -> bool:
    return all((assets_dir() / f"{k}.png").exists() for k in SHEET_ORDER)


# --------------------------------------------------------------------------
# Azulejo de categoría (GlyphTile) y glifos teñidos — sistema de diseño 2.0
# --------------------------------------------------------------------------

def _line_glyph(name: str) -> QPixmap:
    """Glifo de línea de icons.py a 256 px, con el mismo aire que los PNG de ``assets/tiles``."""
    from .icons import _draw
    pm = QPixmap(256, 256)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.translate(22, 22)
    p.scale(212 / 24, 212 / 24)
    pen = QPen(QColor("#1F2937"), 1.45)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    _draw(name, p, QColor("#1F2937"))
    p.end()
    return pm


@lru_cache(maxsize=None)
def glyph_source(key: str) -> QPixmap:
    """El dibujo de línea de una herramienta (sin fondo), a 256 px: PNG de ``assets/tiles``, el de una
    extensión (``EXTRA``) o, si no hay ninguno, el ícono vectorial equivalente de icons.py."""
    if key in EXTRA:
        return EXTRA[key]()
    f = assets_dir() / f"{key}.png"
    if f.exists() and key != "logo":
        pm = QPixmap(str(f))
        if not pm.isNull():
            return pm
    from .icons import EXTRA as ICON_EXTRA
    name = key if key in ICON_EXTRA else TILES.get(key, (None, key))[1]
    return _line_glyph(name)


@lru_cache(maxsize=None)
def _glyph_grueso(key: str, r: int) -> QPixmap:
    """El glifo de 256 px con la línea engrosada ``r`` px (para tamaños muy chicos, donde la línea original
    quedaría de menos de un píxel y se vería desvaída)."""
    import math
    from PySide6.QtGui import QImage
    src = glyph_source(key).toImage().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    out = src.copy()
    p = QPainter(out)
    for radio in (r, r / 2):
        for k in range(8):
            a = k * math.pi / 4
            p.drawImage(QPointF(radio * math.cos(a), radio * math.sin(a)), src)
    p.end()
    return QPixmap.fromImage(out)


@lru_cache(maxsize=None)
def glyph(key: str, size: int, color: str, dpr: float = 2.0) -> QPixmap:
    """El glifo de ``key`` pintado de un solo color (``CompositionMode_SourceIn``) a ``size`` px lógicos.

    Sirve para botones, barras y menús: blanco sobre un azulejo, ``ink-2`` en una barra, etc."""
    px = max(1, int(round(size * dpr)))
    src = glyph_source(key) if px >= 24 else _glyph_grueso(key, max(2, round(256 / px * 0.3)))
    from PySide6.QtGui import QImage
    img = src.scaled(px, px, Qt.AspectRatioMode.KeepAspectRatio,
                     Qt.TransformationMode.SmoothTransformation).toImage()
    img = img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    p = QPainter(img)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    p.fillRect(img.rect(), QColor(color))
    p.end()
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(dpr)
    return pm


def glyph_icon(key: str, color: str, size: int = 18) -> QIcon:
    """``QIcon`` con el glifo teñido (nítido a 1×, 2× y 3×)."""
    ic = QIcon()
    for dpr in (1.0, 2.0, 3.0):
        ic.addPixmap(glyph(key, size, color, dpr))
    return ic


def white_glyph(key: str, size: int, dpr: float = 2.0) -> QPixmap:
    from . import tema
    return glyph(key, size, tema.color("on-cat"), dpr)


def ink_glyph(key: str, size: int, cat: str | None = None, dpr: float = 2.0) -> QPixmap:
    """Glifo en la tinta de la categoría (o en ``ink-2`` si no hay categoría)."""
    from . import tema
    return glyph(key, size, tema.tonos(cat)["ink"] if cat else tema.color("ink-2"), dpr)


def _glyph_tile(key: str, size: int, cat: str | None, soft: bool, dpr: float, tema_id: str) -> QPixmap:
    from . import tema
    tonos = tema.tonos(cat)
    px = max(1, int(round(size * dpr)))
    pm = QPixmap(px, px)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    r = QRectF(0, 0, px, px)
    path = QPainterPath()
    path.addRoundedRect(r, px * 0.3, px * 0.3)
    if soft:
        p.fillPath(path, QColor(tonos["band"]))
        color = tonos["ink"]
    else:
        grad = QLinearGradient(0, 0, 0, px)
        grad.setColorAt(0, QColor(tonos["hi"]))
        grad.setColorAt(1, QColor(tonos["c"]))
        p.fillPath(path, grad)
        if size > tema.TILE_XS:        # brillo interior de 1 px arriba (inset 0 1px 0 rgba(255,255,255,.28))
            p.save()
            p.setClipPath(path)
            hl = QPainterPath()
            hl.addRoundedRect(r.adjusted(0, dpr, 0, dpr), px * 0.3, px * 0.3)
            p.fillPath(path.subtracted(hl), QColor(255, 255, 255, 72))
            p.restore()
        color = tema.color("on-cat")
    g = glyph(key, size * 0.58, color, dpr)
    gw, gh = g.width(), g.height()
    p.drawPixmap(QRectF((px - gw) / 2, (px - gh) / 2, gw, gh), g, QRectF(0, 0, gw, gh))
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


@lru_cache(maxsize=None)
def _glyph_tile_cached(key, size, cat, soft, dpr, tema_id):
    return _glyph_tile(key, size, cat, soft, dpr, tema_id)


def glyph_tile(key: str, size: int, cat: str | None, soft: bool = False, dpr: float = 2.0) -> QPixmap:
    """Azulejo de categoría: cuadrado redondeado (radio 30 %) con degradado ``cat-x-hi`` → ``cat-x`` y el
    glifo en blanco al 58 %.  ``soft`` = banda pastel con el glifo en ``cat-x-ink``.

    ``cat`` es una categoría de ``tema.CATEGORIAS``, "marca" o None (neutro).  ``dpr`` es la densidad de la
    pantalla donde se va a pintar (``widget.devicePixelRatioF()``)."""
    from . import tema
    return _glyph_tile_cached(key, int(size), cat, bool(soft), float(dpr), tema.tema_actual())


def glyph_tile_icon(key: str, size: int, cat: str | None, soft: bool = False) -> QIcon:
    ic = QIcon()
    for dpr in (1.0, 2.0, 3.0):
        ic.addPixmap(glyph_tile(key, size, cat, soft, dpr))
    return ic


def brand_logo(size: int, dpr: float = 2.0) -> QPixmap:
    """El logotipo (cuadrado rojo con el documento y la cruz) tal cual, nunca recoloreado."""
    return tile("logo", size, dpr)


# --------------------------------------------------------------------------
# Ilustraciones
# --------------------------------------------------------------------------

def mini_pdf(size_w: int = 32, size_h: int = 40, dpr: float = 2.0) -> QPixmap:
    """Miniatura genérica de un PDF: hoja blanca con etiqueta roja «PDF»."""
    pm = QPixmap(int(size_w * dpr), int(size_h * dpr))
    pm.fill(Qt.GlobalColor.transparent)
    pm.setDevicePixelRatio(dpr)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QColor("#E1E4E8"), 1))
    p.setBrush(QColor("white"))
    p.drawRoundedRect(QRectF(0.5, 0.5, size_w - 1, size_h - 1), 4, 4)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#E6E8EC"))
    for i, w in enumerate((0.6, 0.75, 0.5)):
        p.drawRoundedRect(QRectF(5, 6 + i * 5, (size_w - 10) * w, 2.5), 1, 1)
    p.setBrush(QColor("#D63A3A"))
    lab = QRectF(4, size_h - 12, 18, 8)
    p.drawRoundedRect(lab, 2, 2)
    f = p.font()
    f.setPixelSize(6)
    f.setBold(True)
    p.setFont(f)
    p.setPen(QColor("white"))
    p.drawText(lab, Qt.AlignmentFlag.AlignCenter, "PDF")
    p.end()
    return pm


def stacked_docs(w: int = 170, h: int = 112, dpr: float = 2.0) -> QPixmap:
    """Ilustración de tres hojas apiladas para la zona de soltar archivos."""
    pm = QPixmap(int(w * dpr), int(h * dpr))
    pm.fill(Qt.GlobalColor.transparent)
    pm.setDevicePixelRatio(dpr)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    def sheet(cx, cy, angle, kind):
        p.save()
        p.translate(cx, cy)
        p.rotate(angle)
        r = QRectF(-33, -43, 66, 86)
        p.setPen(Qt.PenStyle.NoPen)
        for k, a in ((6, 10), (3, 16)):
            p.setBrush(QColor(16, 24, 40, a))
            p.drawRoundedRect(r.translated(0, k), 7, 7)
        p.setBrush(QColor("white"))
        p.setPen(QPen(QColor("#E6E8EC"), 1))
        p.drawRoundedRect(r, 7, 7)
        p.setPen(Qt.PenStyle.NoPen)
        if kind == "chart":
            p.setBrush(QColor("#E8F1FB"))
            p.drawRoundedRect(QRectF(-24, -32, 48, 30), 3, 3)
            p.setBrush(QColor("#ECEEF1"))
            p.drawRoundedRect(QRectF(-24, 4, 48, 5), 2, 2)
        else:
            p.setBrush(QColor("#C9CED6" if kind == "pdf" else "#D9DDE3"))
            p.drawRoundedRect(QRectF(-24, -32, 38, 5), 2, 2)
            p.setBrush(QColor("#ECEEF1"))
            for i in range(2):
                p.drawRoundedRect(QRectF(-24, -22 + i * 9, 48, 5), 2, 2)
            if kind == "pdf":
                p.setBrush(QColor("#D63A3A"))
                lab = QRectF(-24, 24, 24, 11)
                p.drawRoundedRect(lab, 2.5, 2.5)
                f = p.font()
                f.setPixelSize(8)
                f.setBold(True)
                p.setFont(f)
                p.setPen(QColor("white"))
                p.drawText(lab, Qt.AlignmentFlag.AlignCenter, "PDF")
        p.restore()

    sheet(w / 2 - 34, h / 2 + 6, -9, "text")
    sheet(w / 2 + 34, h / 2 + 6, 9, "chart")
    sheet(w / 2, h / 2, 0, "pdf")
    p.end()
    return pm


def arrow_out(color: str = "#9AA1AC", size: int = 16) -> QPixmap:
    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.GlobalColor.transparent)
    pm.setDevicePixelRatio(2)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 1.8)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    s = size / 24
    p.scale(s, s)
    p.drawLine(QPointF(7, 17), QPointF(17, 7))
    p.drawPolyline([QPointF(8, 7), QPointF(17, 7), QPointF(17, 16)])
    p.end()
    return pm
