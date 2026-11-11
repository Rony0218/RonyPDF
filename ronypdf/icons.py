"""Íconos vectoriales dibujados con QPainter (sin archivos externos)."""

from __future__ import annotations

import math
from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen,
                           QPixmap)

ACCENT = "#D93636"
EXTRA: dict = {}   # íconos de los módulos *_ui.py: nombre → dibujo(p, base, acento); ver extensiones.py


def _doc(p: QPainter, x, y, w, h, fold=4.0, fill: str | None = None):
    path = QPainterPath()
    path.moveTo(x, y)
    path.lineTo(x + w - fold, y)
    path.lineTo(x + w, y + fold)
    path.lineTo(x + w, y + h)
    path.lineTo(x, y + h)
    path.closeSubpath()
    if fill:
        p.save()
        p.setBrush(QColor(fill))
        p.drawPath(path)
        p.restore()
    else:
        p.drawPath(path)
    p.drawPolyline([QPointF(x + w - fold, y), QPointF(x + w - fold, y + fold), QPointF(x + w, y + fold)])


def _arrowhead(p: QPainter, tip: QPointF, dx: float, dy: float, size=3.2):
    n = math.hypot(dx, dy) or 1
    dx, dy = dx / n, dy / n
    back = QPointF(tip.x() - dx * size, tip.y() - dy * size)
    nx, ny = -dy * size * 0.8, dx * size * 0.8
    p.drawPolyline([QPointF(back.x() + nx, back.y() + ny), tip, QPointF(back.x() - nx, back.y() - ny)])


def _line(p, x1, y1, x2, y2):
    p.drawLine(QPointF(x1, y1), QPointF(x2, y2))


def _rotate_arrow(p: QPainter):
    rect = QRectF(4, 5, 15, 15)
    path = QPainterPath()
    path.arcMoveTo(rect, 200)
    path.arcTo(rect, 200, -215)
    p.drawPath(path)
    end = path.currentPosition()
    th = math.radians(-15)
    _arrowhead(p, end, math.sin(th), math.cos(th))


def _draw(name: str, p: QPainter, color: QColor):
    if name in EXTRA:
        EXTRA[name](p, color, color)
        return
    if name == "home":
        p.drawPolyline([QPointF(3, 11.5), QPointF(12, 3.5), QPointF(21, 11.5)])
        p.drawPolyline([QPointF(5.5, 9.5), QPointF(5.5, 20.5), QPointF(18.5, 20.5), QPointF(18.5, 9.5)])
        p.drawRect(QRectF(10, 14, 4, 6.5))
    elif name == "merge":
        _doc(p, 3, 2.5, 11, 14)
        _doc(p, 10, 7.5, 11, 14, fill="#FFFFFF")
        _line(p, 15.5, 11.5, 15.5, 17.5)
        _line(p, 12.5, 14.5, 18.5, 14.5)
    elif name == "split":
        _doc(p, 2, 4, 8, 16, 3)
        _doc(p, 14, 4, 8, 16, 3)
        pen = p.pen()
        pen.setStyle(Qt.PenStyle.DashLine)
        p.save()
        p.setPen(pen)
        _line(p, 12, 1.5, 12, 22.5)
        p.restore()
    elif name == "organize":
        for x, y in ((3, 3), (13, 3), (3, 13), (13, 13)):
            p.drawRoundedRect(QRectF(x, y, 8, 8), 1.6, 1.6)
    elif name == "compress":
        _doc(p, 5, 2, 14, 20)
        _line(p, 12, 4.5, 12, 9.5)
        _arrowhead(p, QPointF(12, 9.5), 0, 1, 2.6)
        _line(p, 12, 19.5, 12, 14.5)
        _arrowhead(p, QPointF(12, 14.5), 0, -1, 2.6)
        _line(p, 8.5, 12, 15.5, 12)
    elif name == "toimage":
        p.drawRoundedRect(QRectF(3, 4, 18, 16), 2.5, 2.5)
        p.drawEllipse(QPointF(8.5, 9.5), 1.8, 1.8)
        p.drawPolyline([QPointF(3.5, 18), QPointF(9, 12.5), QPointF(13, 16.5), QPointF(16, 13.5),
                        QPointF(20.5, 18)])
    elif name == "fromimage":
        _doc(p, 4, 2, 16, 20)
        p.drawEllipse(QPointF(9.5, 9), 1.6, 1.6)
        p.drawPolyline([QPointF(7, 18), QPointF(10.5, 13.5), QPointF(13, 16), QPointF(15, 14),
                        QPointF(17, 18)])
    elif name == "watermark":
        path = QPainterPath()
        path.moveTo(12, 2.5)
        path.cubicTo(12, 2.5, 19, 10, 19, 14.5)
        path.arcTo(QRectF(5, 7.5, 14, 14), 0, -180)
        path.cubicTo(5, 10, 12, 2.5, 12, 2.5)
        p.drawPath(path)
        p.drawArc(QRectF(8.5, 11, 7, 7), 200 * 16, 70 * 16)
    elif name == "numbers":
        _line(p, 9.5, 3.5, 7.5, 20.5)
        _line(p, 16.5, 3.5, 14.5, 20.5)
        _line(p, 4.5, 9, 20, 9)
        _line(p, 4, 15, 19.5, 15)
    elif name in ("lock", "unlock"):
        p.drawRoundedRect(QRectF(5, 11, 14, 10), 2, 2)
        path = QPainterPath()
        path.moveTo(8, 11)
        path.lineTo(8, 7.5)
        path.arcTo(QRectF(8, 3.5, 8, 8), 180, -180)
        path.lineTo(16, 11 if name == "lock" else 8)
        if name == "unlock":
            path = QPainterPath()
            path.moveTo(8, 11)
            path.lineTo(8, 7.5)
            path.arcTo(QRectF(8, 3.5, 8, 8), 180, -150)
        p.drawPath(path)
        _line(p, 12, 15, 12, 17.5)
    elif name == "text":
        _doc(p, 4, 2, 16, 20)
        _line(p, 8, 8, 16, 8)
        _line(p, 8, 12, 16, 12)
        _line(p, 8, 16, 13, 16)
    elif name == "info":
        p.drawEllipse(QPointF(12, 12), 9, 9)
        _line(p, 12, 11, 12, 16.5)
        p.save()
        p.setBrush(color)
        p.drawEllipse(QPointF(12, 7.8), 0.6, 0.6)
        p.restore()
    elif name == "rotate_right":
        _rotate_arrow(p)
    elif name == "rotate_left":
        p.translate(24, 0)
        p.scale(-1, 1)
        _rotate_arrow(p)
    elif name == "delete":
        _line(p, 4, 6.5, 20, 6.5)
        p.drawPolyline([QPointF(9, 6.5), QPointF(9, 3.5), QPointF(15, 3.5), QPointF(15, 6.5)])
        p.drawPolyline([QPointF(6, 6.5), QPointF(7, 20.5), QPointF(17, 20.5), QPointF(18, 6.5)])
        _line(p, 10, 10, 10, 17)
        _line(p, 14, 10, 14, 17)
    elif name == "duplicate":
        p.drawRoundedRect(QRectF(8.5, 8.5, 12, 12), 2, 2)
        p.drawPolyline([QPointF(5.5, 15.5), QPointF(3.5, 15.5), QPointF(3.5, 3.5), QPointF(15.5, 3.5),
                        QPointF(15.5, 5.5)])
    elif name == "blank":
        _doc(p, 4, 2, 16, 20)
        _line(p, 12, 9, 12, 17)
        _line(p, 8, 13, 16, 13)
    elif name == "add":
        _line(p, 12, 4.5, 12, 19.5)
        _line(p, 4.5, 12, 19.5, 12)
    elif name in ("remove", "close"):          # «close»: la equis de «Quitar» en fichas y tarjetas
        _line(p, 5.5, 5.5, 18.5, 18.5)
        _line(p, 18.5, 5.5, 5.5, 18.5)
    elif name == "up":
        _line(p, 12, 19.5, 12, 4.5)
        _arrowhead(p, QPointF(12, 4.5), 0, -1, 5)
    elif name == "down":
        _line(p, 12, 4.5, 12, 19.5)
        _arrowhead(p, QPointF(12, 19.5), 0, 1, 5)
    elif name == "sort":
        _line(p, 4, 6, 20, 6)
        _line(p, 4, 12, 15, 12)
        _line(p, 4, 18, 10, 18)
    elif name == "reverse":
        _line(p, 8, 19.5, 8, 4.5)
        _arrowhead(p, QPointF(8, 4.5), 0, -1, 3.5)
        _line(p, 16, 4.5, 16, 19.5)
        _arrowhead(p, QPointF(16, 19.5), 0, 1, 3.5)
    elif name == "save":
        _line(p, 12, 3.5, 12, 15)
        _arrowhead(p, QPointF(12, 15), 0, 1, 4.5)
        p.drawPolyline([QPointF(4, 15), QPointF(4, 20.5), QPointF(20, 20.5), QPointF(20, 15)])
    elif name == "folder":
        path = QPainterPath()
        path.moveTo(3, 6.5)
        path.lineTo(3, 19.5)
        path.lineTo(21, 19.5)
        path.lineTo(21, 8.5)
        path.lineTo(11.5, 8.5)
        path.lineTo(9.5, 5)
        path.lineTo(3.5, 5)
        path.closeSubpath()
        p.drawPath(path)
    elif name == "open":
        _doc(p, 4, 2, 16, 20)
        _line(p, 12, 17, 12, 8)
        _arrowhead(p, QPointF(12, 8), 0, -1, 3.5)
    elif name == "undo" or name == "redo":
        if name == "redo":
            p.translate(24, 0)
            p.scale(-1, 1)
        path = QPainterPath()
        path.moveTo(5, 9.5)
        path.lineTo(15, 9.5)
        path.cubicTo(22, 9.5, 22, 19.5, 15, 19.5)
        path.lineTo(9, 19.5)
        p.drawPath(path)
        _arrowhead(p, QPointF(5, 9.5), -1, 0, 4)
    elif name == "select":
        pen = p.pen()
        pen.setStyle(Qt.PenStyle.DashLine)
        p.save()
        p.setPen(pen)
        p.drawRoundedRect(QRectF(3.5, 3.5, 17, 17), 2, 2)
        p.restore()
        p.drawPolyline([QPointF(8, 12.5), QPointF(11, 15.5), QPointF(16.5, 9)])
    elif name == "zoom":
        p.drawEllipse(QPointF(10.5, 10.5), 6.5, 6.5)
        _line(p, 15.5, 15.5, 20.5, 20.5)
    elif name == "eye":
        path = QPainterPath()
        path.moveTo(2.5, 12)
        path.cubicTo(7, 4.5, 17, 4.5, 21.5, 12)
        path.cubicTo(17, 19.5, 7, 19.5, 2.5, 12)
        p.drawPath(path)
        p.drawEllipse(QPointF(12, 12), 3, 3)
    elif name == "upload":
        _line(p, 12, 16, 12, 4)
        _arrowhead(p, QPointF(12, 4), 0, -1, 5)
        p.drawPolyline([QPointF(4, 14), QPointF(4, 20.5), QPointF(20, 20.5), QPointF(20, 14)])
    elif name == "pdf":
        _doc(p, 4, 2, 16, 20, 5)
    elif name == "image":
        p.drawRoundedRect(QRectF(3, 4, 18, 16), 2.5, 2.5)
        p.drawPolyline([QPointF(3.5, 18), QPointF(9, 12.5), QPointF(13, 16.5), QPointF(16, 13.5),
                        QPointF(20.5, 18)])
    elif name == "check":
        p.drawPolyline([QPointF(5, 12.5), QPointF(10, 17.5), QPointF(19, 7)])
    elif name == "cursor":
        path = QPainterPath()
        path.moveTo(6, 3.5)
        path.lineTo(6, 18.5)
        path.lineTo(10, 14.5)
        path.lineTo(13, 20.5)
        path.lineTo(15.5, 19.3)
        path.lineTo(12.5, 13.5)
        path.lineTo(18, 13.5)
        path.closeSubpath()
        p.drawPath(path)
    elif name == "type":
        _line(p, 5, 5, 19, 5)
        _line(p, 12, 5, 12, 20)
        _line(p, 9, 20, 15, 20)
        _line(p, 5, 5, 5, 7.5)
        _line(p, 19, 5, 19, 7.5)
    elif name == "edittext":
        _line(p, 3.5, 5, 13.5, 5)
        _line(p, 8.5, 5, 8.5, 17)
        path = QPainterPath()
        path.moveTo(13, 20.5)
        path.lineTo(13.5, 17.5)
        path.lineTo(19.5, 11.5)
        path.lineTo(22, 14)
        path.lineTo(16, 20)
        path.closeSubpath()
        p.drawPath(path)
    elif name == "highlighter":
        path = QPainterPath()
        path.moveTo(9, 15)
        path.lineTo(16.5, 4.5)
        path.lineTo(20.5, 8)
        path.lineTo(12, 17.5)
        path.closeSubpath()
        p.drawPath(path)
        p.drawPolyline([QPointF(9, 15), QPointF(7, 18.5), QPointF(10.5, 19.5), QPointF(12, 17.5)])
        _line(p, 3.5, 21, 20.5, 21)
    elif name == "pen":
        path = QPainterPath()
        path.moveTo(4, 20)
        path.lineTo(5, 15.5)
        path.lineTo(15.5, 5)
        path.lineTo(19, 8.5)
        path.lineTo(8.5, 19)
        path.closeSubpath()
        p.drawPath(path)
        _line(p, 13, 7.5, 16.5, 11)
    elif name == "square":
        p.drawRoundedRect(QRectF(4, 5, 16, 14), 1.5, 1.5)
    elif name == "circle":
        p.drawEllipse(QRectF(3.5, 5, 17, 14))
    elif name == "line":
        _line(p, 4.5, 19.5, 19.5, 4.5)
    elif name == "arrow":
        _line(p, 4.5, 19.5, 19, 5)
        _arrowhead(p, QPointF(19, 5), 1, -1, 5)
    elif name == "eraser":
        path = QPainterPath()
        path.moveTo(9, 20)
        path.lineTo(3.5, 14.5)
        path.lineTo(13.5, 4.5)
        path.lineTo(20.5, 11.5)
        path.lineTo(12, 20)
        path.closeSubpath()
        p.drawPath(path)
        _line(p, 8.5, 9.5, 15.5, 16.5)
        _line(p, 12, 20, 20.5, 20)
    elif name == "search":
        p.drawEllipse(QPointF(10.5, 10.5), 6, 6)
        _line(p, 15, 15, 20, 20)
    elif name == "prev":
        p.drawPolyline([QPointF(14.5, 5.5), QPointF(8, 12), QPointF(14.5, 18.5)])
    elif name == "next":
        p.drawPolyline([QPointF(9.5, 5.5), QPointF(16, 12), QPointF(9.5, 18.5)])
    elif name == "up_chevron":
        p.drawPolyline([QPointF(5.5, 14.5), QPointF(12, 8), QPointF(18.5, 14.5)])
    elif name == "down_chevron":
        p.drawPolyline([QPointF(5.5, 9.5), QPointF(12, 16), QPointF(18.5, 9.5)])
    elif name == "minus":
        _line(p, 5, 12, 19, 12)
    elif name == "ocr":
        for x, y, dx, dy in ((3, 8, 0, -4.5), (3, 16, 0, 4.5), (21, 8, 0, -4.5), (21, 16, 0, 4.5)):
            p.drawPolyline([QPointF(x, y), QPointF(x, y + dy), QPointF(x + (4.5 if x < 12 else -4.5), y + dy)])
        _line(p, 8, 9, 16, 9)
        _line(p, 12, 9, 12, 16)
    elif name == "viewer":
        _doc(p, 3, 2, 13, 17)
        p.drawEllipse(QPointF(15.5, 15.5), 4, 4)
        _line(p, 18.5, 18.5, 21.5, 21.5)
    elif name == "clock":
        p.drawEllipse(QPointF(12, 12), 8.5, 8.5)
        p.drawPolyline([QPointF(12, 7), QPointF(12, 12), QPointF(15.5, 14)])
    elif name in ("panel_close", "panel_open"):
        p.drawRoundedRect(QRectF(3.5, 4, 17, 16), 2.5, 2.5)
        _line(p, 9.5, 4, 9.5, 20)
        if name == "panel_close":
            p.drawPolyline([QPointF(16, 10), QPointF(14, 12), QPointF(16, 14)])
        else:
            p.drawPolyline([QPointF(14, 10), QPointF(16, 12), QPointF(14, 14)])
    elif name == "chevron_down":
        p.drawPolyline([QPointF(6, 9), QPointF(12, 15), QPointF(18, 9)])
    elif name == "shield":
        path = QPainterPath()
        path.moveTo(12, 3)
        path.lineTo(20, 6)
        path.lineTo(20, 12)
        path.cubicTo(20, 16.5, 16.6, 20, 12, 21)
        path.cubicTo(7.4, 20, 4, 16.5, 4, 12)
        path.lineTo(4, 6)
        path.closeSubpath()
        p.drawPath(path)
        p.drawPolyline([QPointF(9, 12), QPointF(11, 14), QPointF(15, 10)])
    elif name == "trash":
        _line(p, 4, 6.5, 20, 6.5)
        p.drawPolyline([QPointF(9, 6.5), QPointF(9, 3.5), QPointF(15, 3.5), QPointF(15, 6.5)])
        p.drawPolyline([QPointF(6, 6.5), QPointF(7, 20.5), QPointF(17, 20.5), QPointF(18, 6.5)])
    elif name == "folder_open":
        p.drawPolyline([QPointF(3, 18.5), QPointF(3, 5), QPointF(9, 5), QPointF(11, 7.5), QPointF(18, 7.5),
                        QPointF(18, 10)])
        p.drawPolyline([QPointF(3, 18.5), QPointF(6, 10), QPointF(21, 10), QPointF(18, 18.5), QPointF(3, 18.5)])


@lru_cache(maxsize=None)
def pixmap(name: str, color: str = "#4B5563", size: int = 20, width: float = 1.7) -> QPixmap:
    dpr = 2.0
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.fill(Qt.GlobalColor.transparent)
    pm.setDevicePixelRatio(dpr)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 24, size / 24)
    c = QColor(color)
    pen = QPen(c, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    _draw(name, p, c)
    p.end()
    return pm


@lru_cache(maxsize=None)
def icon(name: str, color: str = "#4B5563", checked_color: str | None = None, size: int = 20) -> QIcon:
    ic = QIcon()
    ic.addPixmap(pixmap(name, color, size), QIcon.Mode.Normal, QIcon.State.Off)
    ic.addPixmap(pixmap(name, "#B4B9C0", size), QIcon.Mode.Disabled, QIcon.State.Off)
    if checked_color:
        ic.addPixmap(pixmap(name, checked_color, size), QIcon.Mode.Normal, QIcon.State.On)
    return ic


def logo_pixmap(size: int = 256) -> QPixmap:
    """Logotipo de la aplicación: documento blanco sobre un cuadrado rojo."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size / 256
    grad = QLinearGradient(0, 0, 0, size)
    grad.setColorAt(0, QColor("#EF5350"))
    grad.setColorAt(1, QColor("#C62828"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(grad))
    p.drawRoundedRect(QRectF(8 * s, 8 * s, 240 * s, 240 * s), 56 * s, 56 * s)
    # documento
    x, y, w, h, f = 70 * s, 46 * s, 116 * s, 152 * s, 36 * s
    path = QPainterPath()
    path.moveTo(x, y)
    path.lineTo(x + w - f, y)
    path.lineTo(x + w, y + f)
    path.lineTo(x + w, y + h)
    path.lineTo(x, y + h)
    path.closeSubpath()
    p.setBrush(QColor("#FFFFFF"))
    p.drawPath(path)
    fold = QPainterPath()
    fold.moveTo(x + w - f, y)
    fold.lineTo(x + w - f, y + f)
    fold.lineTo(x + w, y + f)
    fold.closeSubpath()
    p.setBrush(QColor("#F6C9C9"))
    p.drawPath(fold)
    # líneas de texto y una «herramienta»
    p.setBrush(QColor("#E57373"))
    for i, lw in enumerate((76, 60, 76, 44)):
        p.drawRoundedRect(QRectF(x + 20 * s, y + (58 + i * 22) * s, lw * s, 9 * s), 4.5 * s, 4.5 * s)
    p.setBrush(QColor("#C62828"))
    p.drawEllipse(QPointF(x + w - 4 * s, y + h - 6 * s), 30 * s, 30 * s)
    pen = QPen(QColor("#FFFFFF"), 8 * s)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    cx, cy = x + w - 4 * s, y + h - 6 * s
    p.drawLine(QPointF(cx - 13 * s, cy), QPointF(cx + 13 * s, cy))
    p.drawLine(QPointF(cx, cy - 13 * s), QPointF(cx, cy + 13 * s))
    p.end()
    return pm


def app_icon() -> QIcon:
    ic = QIcon()
    for sz in (16, 24, 32, 48, 64, 128, 256):
        ic.addPixmap(logo_pixmap(sz))
    return ic
