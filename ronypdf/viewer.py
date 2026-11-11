"""Visor y editor de PDF.

La escena trabaja en puntos PDF (1 unidad = 1 pt) y el zoom es la transformación de la vista.
Cada página es un rectángulo blanco con su imagen renderizada a la resolución del zoom actual.
Las ediciones son objetos de la escena que solo se escriben en el PDF al guardar.
"""

from __future__ import annotations

import bisect
import math
import os
import secrets
import time
from pathlib import Path

import pymupdf
from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (QBrush, QColor, QDesktopServices, QFont, QFontMetricsF, QIcon, QImage,
                           QIntValidator, QKeySequence, QPainter, QPainterPath, QPainterPathStroker, QPen, QPixmap,
                           QPolygonF, QShortcut, QTransform)
from PySide6.QtWidgets import (QAbstractButton, QApplication, QButtonGroup, QComboBox, QFrame, QGraphicsItem,
                               QGraphicsPathItem, QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene,
                               QGraphicsTextItem, QGraphicsView, QHBoxLayout, QLabel, QLineEdit, QMenu,
                               QMessageBox, QPushButton, QScrollArea, QSpinBox, QStackedLayout, QStackedWidget,
                               QToolButton, QToolTip, QVBoxLayout, QWidget)

from . import art, core, extensiones, tema
from .componentes import cat_de, repolish
from . import guardado, guardado_ui, visor_paneles, visor_texto
from .widgets import (IMAGE_FILTER, PDF_LOCK, Chip, ColorButton, DropArea, GlyphTile, Kbd, Note, PdfThumb,
                      ask_open, ask_save, ctx, label, qimage_from_render, show_done, suggest_output, tool_button)

GAP = 18
MARGIN = 28
MAX_PIXELS = 16_000_000
Z_PAGE, Z_COVER, Z_EDIT, Z_SELECT, Z_SEARCH = 0, 1, 2, 4, 5
QT_FAMILY = {core.SANS: "Arial", core.SERIF: "Times New Roman", core.MONO: "Courier New"}
# El pozo donde se ven las páginas (``surface-sunken``) y su versión para el modo noche (la del tema oscuro).
CANVAS_BG, CANVAS_BG_NIGHT = tema.qcolor("surface-sunken"), QColor(tema.OSCURO["surface-sunken"])
# Colores de lo que se pinta SOBRE el documento (no son de la interfaz): búsqueda y tintas iniciales.
HIT_COLOR, HIT_ACTUAL_COLOR = QColor(255, 214, 0, 90), QColor(255, 120, 0, 130)
TINTAS_INICIALES = {"text": "#1F2328", "draw": "#D63A3A", "highlight": "#FFE14D"}
CAT = "ver"                      # categoría de «Ver y editar» (violeta): herramienta activa, selección, anillos
SCANNED_MSG = "Este PDF es escaneado: usa Reconocer texto (OCR) para poder seleccionar y copiar."
ROTATED_MSG = ("La vista está girada. Para editar, vuelve a la orientación original; para girar las páginas "
               "de verdad usa Organizar páginas.")
HISTORY_MAX = 50

TOOLS = [
    ("select", "cursor", "Seleccionar (V)",
     "Arrastra sobre el texto para seleccionarlo y copiarlo (Ctrl+C); doble clic toma una palabra. "
     "Arrastra una zona vacía para desplazarte. Haz clic en algo que agregaste para moverlo; Supr lo elimina."),
    ("text", "type", "Agregar texto (T)", "Haz clic donde quieras escribir."),
    ("edittext", "edittext", "Editar texto del PDF (E)", "Haz clic sobre una línea de texto del PDF para cambiarla."),
    ("highlight", "highlighter", "Resaltar (H)", "Arrastra sobre el texto que quieres resaltar."),
    ("ink", "pen", "Dibujar a mano alzada (P)", "Dibuja a mano alzada sobre la página."),
    ("rect", "square", "Rectángulo (R)", "Arrastra para dibujar un rectángulo."),
    ("ellipse", "circle", "Elipse (O)", "Arrastra para dibujar una elipse."),
    ("line", "line", "Línea (L)", "Arrastra desde el inicio hasta el final de la línea."),
    ("arrow", "arrow", "Flecha (A)", "Arrastra desde el inicio hasta la punta de la flecha."),
    ("redact", "eraser", "Borrar zona (B)",
     "Arrastra sobre la zona a borrar: al guardar se elimina de verdad el texto y las imágenes que cubra."),
]
TOOL_KEYS = {"V": "select", "T": "text", "E": "edittext", "H": "highlight", "P": "ink", "R": "rect",
             "O": "ellipse", "L": "line", "A": "arrow", "B": "redact"}
DRAW_TOOLS = {"ink", "rect", "ellipse", "line", "arrow"}


def color_seleccion() -> QColor:
    """Contorno y tiradores de lo que se selecciona en la página: el color de la categoría «ver»."""
    return QColor(tema.tonos(CAT)["c"])


def color_borrar() -> QColor:
    """La zona que se va a borrar al guardar («danger»)."""
    return tema.qcolor("danger")


GLIFO = 17                       # glifos de las barras del visor (``rp-toolgroup``: 17 px)


def _tinta(nombre: str) -> str:
    return nombre if nombre.startswith("#") else tema.color(nombre)


def glifo(nombre: str, color: str = "ink-2", activo: str | None = None, size: int = GLIFO,
          hover: str | None = "ink") -> QIcon:
    """Ícono con el glifo de ``nombre`` teñido (``art.glyph_icon``): ``color`` en reposo, ``hover`` bajo el
    puntero, ``activo`` cuando el botón está encendido y un tono apagado cuando está desactivado.  Los colores
    son tokens de ``tema`` («ink-2», «cat-ver-ink»…) o #rrggbb."""
    ic = art.glyph_icon(nombre, _tinta(color), size)
    apagado = tema.mezclar("ink-2", "surface", 0.55)
    for dpr in (1.0, 2.0, 3.0):
        ic.addPixmap(art.glyph(nombre, size, apagado, dpr), QIcon.Mode.Disabled, QIcon.State.Off)
        if hover:
            ic.addPixmap(art.glyph(nombre, size, _tinta(hover), dpr), QIcon.Mode.Active, QIcon.State.Off)
        if activo:
            ic.addPixmap(art.glyph(nombre, size, _tinta(activo), dpr), QIcon.Mode.Normal, QIcon.State.On)
            ic.addPixmap(art.glyph(nombre, size, _tinta(activo), dpr), QIcon.Mode.Active, QIcon.State.On)
    return ic


def glifo_barra(nombre: str) -> QIcon:
    """Glifo de una herramienta de la barra: ``ink-2`` y, encendida, la tinta de la categoría «ver»."""
    return glifo(nombre, "ink-2", tema.tonos(CAT)["ink"])


def glifo_menu(nombre: str) -> QIcon:
    """Glifo para una opción de menú (16 px, ``ink-2``)."""
    return glifo(nombre, "ink-2", size=16, hover=None)


def reteñir(icono: QIcon, size: int = GLIFO) -> QIcon:
    """Vuelve a teñir un ícono que trae otro módulo (p. ej. el botón de una extensión) con los colores de la
    barra: se toma su silueta y se pinta en ``ink-2`` (``ink`` bajo el puntero, la tinta de «ver» encendido)."""
    base = icono.pixmap(QSize(size, size), 2.0)
    if base.isNull():
        return icono
    out = QIcon()
    tonos = ((QIcon.Mode.Normal, QIcon.State.Off, tema.color("ink-2")),
             (QIcon.Mode.Active, QIcon.State.Off, tema.color("ink")),
             (QIcon.Mode.Normal, QIcon.State.On, tema.tonos(CAT)["ink"]),
             (QIcon.Mode.Active, QIcon.State.On, tema.tonos(CAT)["ink"]),
             (QIcon.Mode.Disabled, QIcon.State.Off, tema.mezclar("ink-2", "surface", 0.55)))
    for modo, estado, color in tonos:
        img = base.toImage().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        p = QPainter(img)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        p.fillRect(img.rect(), QColor(color))
        p.end()
        pm = QPixmap.fromImage(img)
        pm.setDevicePixelRatio(base.devicePixelRatio())
        out.addPixmap(pm, modo, estado)
    return out


def rgb(c: QColor) -> tuple[float, float, float]:
    return (c.redF(), c.greenF(), c.blueF())


def qcolor(t) -> QColor:
    return QColor.fromRgbF(*t)


def view_zoom(item: QGraphicsItem) -> float:
    sc = item.scene()
    if sc and sc.views():
        view = sc.views()[0]
        z = getattr(view, "zoom", None)
        if isinstance(z, (int, float)) and z > 0:
            return float(z)
        t = view.transform()
        return math.hypot(t.m11(), t.m12()) or 1.0   # también con la vista girada
    return 1.0


def view_night(item: QGraphicsItem) -> bool:
    sc = item.scene()
    return bool(sc and sc.views() and getattr(sc.views()[0], "night", False))


def rel(r: QRectF, origin: QPointF) -> tuple[float, float, float, float]:
    r = r.translated(-origin)
    return (r.left(), r.top(), r.right(), r.bottom())


# --------------------------------------------------------------------------
# Elementos de la escena
# --------------------------------------------------------------------------

class PageItem(QGraphicsRectItem):
    def __init__(self, index: int, w: float, h: float):
        super().__init__(0, 0, w, h)
        self.index = index
        self.w, self.h = w, h
        self.setBrush(QColor("white"))
        pen = QPen(QColor(0, 0, 0, 45), 0)
        self.setPen(pen)
        self.setZValue(Z_PAGE)
        self.pix = QGraphicsPixmapItem(self)
        self.pix.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.rendered = 0.0

    def set_image(self, img: QImage, scale: float):
        self.pix.setPixmap(QPixmap.fromImage(img))
        self.pix.setScale(1 / scale)
        self.rendered = scale

    def clear_image(self):
        self.pix.setPixmap(QPixmap())
        self.rendered = 0.0


class _Editable:
    kind = ""

    def to_edit(self, page: int, origin: QPointF) -> core.Edit | None:
        raise NotImplementedError

    def apply_style(self, color: QColor | None = None, width: float | None = None):
        pass


class ShapeItem(_Editable, QGraphicsItem):
    """Rectángulo, elipse, imagen o zona a borrar; se mueve y se redimensiona por la esquina."""
    HANDLE = 10

    def __init__(self, kind: str, rect: QRectF, color: QColor, width: float, image: QImage | None = None,
                 image_bytes: bytes | None = None):
        super().__init__()
        self.kind = kind
        rect = rect.normalized()
        self.setPos(rect.topLeft())
        self.r = QRectF(0, 0, rect.width(), rect.height())
        self.color = QColor(color)
        self.width = width
        self.image = image
        self.image_bytes = image_bytes
        self._resize = None
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setAcceptHoverEvents(True)
        self.setZValue(Z_EDIT)

    def set_rect(self, rect: QRectF):
        rect = rect.normalized()
        self.prepareGeometryChange()
        self.setPos(rect.topLeft())
        self.r = QRectF(0, 0, rect.width(), rect.height())

    def _hs(self) -> float:
        return self.HANDLE / view_zoom(self)

    def _handle(self) -> QRectF:
        s = self._hs()
        return QRectF(self.r.right() - s / 2, self.r.bottom() - s / 2, s, s)

    def boundingRect(self) -> QRectF:
        m = self.width / 2 + self._hs()
        return self.r.adjusted(-m, -m, m, m)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addRect(self.r.adjusted(-3, -3, 3, 3))
        return path

    def paint(self, p: QPainter, opt, widget=None):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.kind == "image" and self.image is not None:
            p.drawImage(self.r, self.image)
        elif self.kind == "redact":
            p.fillRect(self.r, QColor("white"))
            pen = QPen(color_borrar(), 0, Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawRect(self.r)
            p.setPen(color_borrar())
            f = p.font()
            f.setPixelSize(max(6, int(min(self.r.height() * 0.3, 11))))
            p.setFont(f)
            if self.r.width() > 40 and self.r.height() > 10:
                p.drawText(self.r, Qt.AlignmentFlag.AlignCenter, "Se borrará")
        else:
            pen = QPen(self.color, self.width)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            if self.kind == "ellipse":
                p.drawEllipse(self.r)
            else:
                p.drawRect(self.r)
        if self.isSelected():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(color_seleccion(), 0, Qt.PenStyle.DashLine))
            p.drawRect(self.r)
            p.setPen(QPen(QColor("white"), 0))
            p.setBrush(color_seleccion())
            p.drawRect(self._handle())

    def hoverMoveEvent(self, e):
        if self.isSelected() and self._handle().contains(e.pos()):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        else:
            self.unsetCursor()
        super().hoverMoveEvent(e)

    def mousePressEvent(self, e):
        if self.isSelected() and self._handle().contains(e.pos()) and e.button() == Qt.MouseButton.LeftButton:
            self._resize = (e.pos(), QRectF(self.r))
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._resize:
            start, r0 = self._resize
            d = e.pos() - start
            w = max(4.0, r0.width() + d.x())
            h = max(4.0, r0.height() + d.y())
            if self.kind == "image" and r0.width() > 0:
                h = w * r0.height() / r0.width()
            self.prepareGeometryChange()
            self.r = QRectF(0, 0, w, h)
            self.update()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._resize = None
        super().mouseReleaseEvent(e)

    def apply_style(self, color=None, width=None):
        if self.kind in ("rect", "ellipse"):
            if color is not None:
                self.color = QColor(color)
            if width is not None:
                self.prepareGeometryChange()
                self.width = width
            self.update()

    def to_edit(self, page, origin):
        r = rel(self.mapRectToScene(self.r), origin)
        if self.kind == "image":
            return core.Edit("image", page, rect=r, image=self.image_bytes)
        if self.kind == "redact":
            return core.Edit("redact", page, rect=r)
        return core.Edit(self.kind, page, rect=r, color=rgb(self.color), width=self.width)


class LineItem(_Editable, QGraphicsItem):
    def __init__(self, kind: str, p1: QPointF, p2: QPointF, color: QColor, width: float):
        super().__init__()
        self.kind = kind
        self.p1, self.p2 = QPointF(p1), QPointF(p2)
        self.color = QColor(color)
        self.width = width
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setZValue(Z_EDIT)

    def set_end(self, p2: QPointF):
        self.prepareGeometryChange()
        self.p2 = QPointF(p2)

    def _head(self) -> QPolygonF:
        size = max(6.0, self.width * 4)
        ang = math.atan2(self.p2.y() - self.p1.y(), self.p2.x() - self.p1.x())
        return QPolygonF([QPointF(self.p2.x() - size * math.cos(ang - 0.45), self.p2.y() - size * math.sin(ang - 0.45)),
                          self.p2,
                          QPointF(self.p2.x() - size * math.cos(ang + 0.45), self.p2.y() - size * math.sin(ang + 0.45))])

    def _path(self) -> QPainterPath:
        path = QPainterPath(self.p1)
        path.lineTo(self.p2)
        return path

    def boundingRect(self):
        m = self.width * 4 + 12
        return QRectF(self.p1, self.p2).normalized().adjusted(-m, -m, m, m)

    def shape(self):
        st = QPainterPathStroker()
        st.setWidth(max(self.width, 10 / view_zoom(self)))
        return st.createStroke(self._path())

    def paint(self, p, opt, widget=None):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(self.color, self.width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.drawLine(self.p1, self.p2)
        if self.kind == "arrow":
            p.setBrush(self.color)
            p.drawPolygon(self._head())
        if self.isSelected():
            p.setPen(QPen(color_seleccion(), 0))
            p.setBrush(QColor("white"))
            s = 4 / view_zoom(self)
            for pt in (self.p1, self.p2):
                p.drawEllipse(pt, s, s)

    def apply_style(self, color=None, width=None):
        if color is not None:
            self.color = QColor(color)
        if width is not None:
            self.prepareGeometryChange()
            self.width = width
        self.update()

    def to_edit(self, page, origin):
        a, b = self.mapToScene(self.p1) - origin, self.mapToScene(self.p2) - origin
        return core.Edit(self.kind, page, points=[(a.x(), a.y()), (b.x(), b.y())], color=rgb(self.color),
                         width=self.width)


class InkItem(_Editable, QGraphicsPathItem):
    kind = "ink"

    def __init__(self, start: QPointF, color: QColor, width: float):
        super().__init__()
        self.points = [QPointF(start)]
        self.color = QColor(color)
        self.width = width
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setZValue(Z_EDIT)
        self._refresh()

    def add_point(self, pt: QPointF):
        if (pt - self.points[-1]).manhattanLength() > 0.6:
            self.points.append(QPointF(pt))
            self._refresh()

    def _refresh(self):
        path = QPainterPath(self.points[0])
        for pt in self.points[1:]:
            path.lineTo(pt)
        self.setPath(path)
        pen = QPen(self.color, self.width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        self.setPen(pen)

    def shape(self):
        st = QPainterPathStroker()
        st.setWidth(max(self.width, 10 / view_zoom(self)))
        return st.createStroke(self.path())

    def paint(self, p, opt, widget=None):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(self.pen())
        p.drawPath(self.path())
        if self.isSelected():
            p.setPen(QPen(color_seleccion(), 0, Qt.PenStyle.DashLine))
            p.drawRect(self.path().boundingRect())

    def apply_style(self, color=None, width=None):
        if color is not None:
            self.color = QColor(color)
        if width is not None:
            self.width = width
        self._refresh()

    def to_edit(self, page, origin):
        pts = [self.mapToScene(pt) - origin for pt in self.points]
        return core.Edit("ink", page, points=[(q.x(), q.y()) for q in pts], color=rgb(self.color), width=self.width)


def autor_comentarios() -> str:
    """El nombre con el que la persona firma sus comentarios (el mismo del panel «Comentarios»)."""
    s = ctx.settings
    try:
        return (s.get("comentarios_autor", "") or "").strip() if s is not None else ""
    except Exception:  # noqa: BLE001 — los ajustes nunca interrumpen el trabajo
        return ""


class HighlightItem(_Editable, QGraphicsItem):
    """Resaltado del visor.  Al guardar es un comentario más (anotación Highlight) con el nombre de quien lo
    hizo, su fecha y un /NM propio, como los del panel «Comentarios»."""
    kind = "highlight"

    def __init__(self, rects: list[QRectF], color: QColor):
        super().__init__()
        self.rects = rects
        self.color = QColor(color)
        self.autor = autor_comentarios()
        self.fecha = core.fecha_pdf_ahora()
        self.nm = "rpdf-" + secrets.token_hex(6)
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setZValue(Z_EDIT)

    def boundingRect(self):
        r = QRectF()
        for x in self.rects:
            r = r.united(x)
        return r.adjusted(-2, -2, 2, 2)

    def shape(self):
        path = QPainterPath()
        for x in self.rects:
            path.addRect(x)
        return path

    def paint(self, p, opt, widget=None):
        # En modo noche la página está invertida (fondo negro): «multiplicar» lo dejaría invisible.
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Screen if view_night(self)
                             else QPainter.CompositionMode.CompositionMode_Multiply)
        c = QColor(self.color)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        for r in self.rects:
            p.drawRect(r)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        if self.isSelected():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(color_seleccion(), 0, Qt.PenStyle.DashLine))
            for r in self.rects:
                p.drawRect(r)

    def apply_style(self, color=None, width=None):
        if color is not None:
            self.color = QColor(color)
            self.update()

    def to_edit(self, page, origin):
        return core.Edit("highlight", page, rects=[rel(self.mapRectToScene(r), origin) for r in self.rects],
                         color=rgb(self.color), autor=self.autor, fecha=self.fecha, nm=self.nm)


class TextItem(_Editable, QGraphicsTextItem):
    """Texto nuevo. Se maqueta con Arial/Times/Courier, que miden igual que las fuentes estándar del PDF,
    y al guardar se escribe línea por línea en las mismas posiciones."""
    kind = "text"

    def __init__(self, text: str, size: float, family: str, bold: bool, italic: bool, color: QColor):
        super().__init__(text)
        self.size = max(1, round(size))
        self.family, self.bold, self.italic = family, bold, italic
        self.on_empty = None
        self.document().setDocumentMargin(0)
        self.setDefaultTextColor(color)
        self._apply_font()
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
                      | QGraphicsItem.GraphicsItemFlag.ItemIsFocusable)
        self.setZValue(Z_EDIT)

    def _apply_font(self):
        f = QFont(QT_FAMILY.get(self.family, "Arial"))
        f.setPixelSize(self.size)
        f.setBold(self.bold)
        f.setItalic(self.italic)
        f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
        f.setKerning(False)
        self.setFont(f)

    def set_style(self, size=None, family=None, bold=None, italic=None, color=None):
        if size is not None:
            self.size = max(1, round(size))
        if family is not None:
            self.family = family
        if bold is not None:
            self.bold = bold
        if italic is not None:
            self.italic = italic
        if color is not None:
            self.setDefaultTextColor(QColor(color))
        self._apply_font()

    def apply_style(self, color=None, width=None):
        self.set_style(color=color)

    def start_editing(self, select_all: bool = False):
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        cur = self.textCursor()
        if select_all:
            cur.select(cur.SelectionType.Document)
        else:
            cur.movePosition(cur.MoveOperation.End)
        self.setTextCursor(cur)

    def mouseDoubleClickEvent(self, e):
        if not self.textInteractionFlags() & Qt.TextInteractionFlag.TextEditorInteraction:
            self.start_editing()
        super().mouseDoubleClickEvent(e)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        cur = self.textCursor()
        cur.clearSelection()
        self.setTextCursor(cur)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        if not self.toPlainText().strip() and self.on_empty:
            self.on_empty(self)

    def baselines(self) -> list[tuple[QPointF, str]]:
        out = []
        self.document().documentLayout()  # asegura la maquetación
        block = self.document().begin()
        while block.isValid():
            layout = block.layout()
            pos = layout.position()
            text = block.text()
            for i in range(layout.lineCount()):
                line = layout.lineAt(i)
                part = text[line.textStart(): line.textStart() + line.textLength()]
                local = QPointF(pos.x() + line.x(), pos.y() + line.y() + line.ascent())
                out.append((self.mapToScene(local), part))
            block = block.next()
        return out

    def _edit_kwargs(self, origin: QPointF) -> dict:
        lines = [(pt.x() - origin.x(), pt.y() - origin.y(), t) for pt, t in self.baselines()]
        return dict(lines=lines, fontsize=self.size, font=core.base14_font(self.family, self.bold, self.italic),
                    color=rgb(self.defaultTextColor()))

    def to_edit(self, page, origin):
        if not self.toPlainText().strip():
            return None
        return core.Edit("text", page, **self._edit_kwargs(origin))


class ReplaceTextItem(TextItem):
    """Reemplaza una línea de texto existente: tapa la original y escribe la nueva."""
    kind = "replace"

    def __init__(self, info: dict, page: "PageItem"):
        super().__init__(info["text"], info["size"], info["family"], info["bold"], info["italic"],
                         qcolor(info["color"]))
        self.info = info
        self.page_index = page.index
        r = QRectF(QPointF(*info["rect"][:2]), QPointF(*info["rect"][2:])).translated(page.pos())
        self.cover = QGraphicsRectItem(r.adjusted(-0.5, -0.5, 0.5, 0.5))
        self.cover.setBrush(QColor("white"))
        self.cover.setPen(QPen(Qt.PenStyle.NoPen))
        self.cover.setZValue(Z_COVER)
        self.cover.setData(0, "cover")
        ascent = QFontMetricsF(self.font()).ascent()
        self.start_pos = QPointF(page.pos().x() + info["origin"][0], page.pos().y() + info["origin"][1] - ascent)
        self.setPos(self.start_pos)

    def unchanged(self) -> bool:
        return self.toPlainText() == self.info["text"] and (self.pos() - self.start_pos).manhattanLength() < 0.01

    def to_edit(self, page, origin):
        # Siempre pertenece a la página del texto original (``origin`` es el de esa página).
        return core.Edit("replace", self.page_index, cover=self.info["rect"], **self._edit_kwargs(origin))


class SelectionOverlay(QGraphicsItem):
    """Rectángulos azules de la selección de texto. No es un elemento de edición: no cuenta como cambio,
    no se guarda y no recibe clics."""
    COLOR = QColor(tema.color("focus"))
    COLOR.setAlpha(70)

    def __init__(self):
        super().__init__()
        self.rects: list[QRectF] = []
        self._bounds = QRectF()
        self.setZValue(Z_SELECT)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setAcceptHoverEvents(False)

    def set_rects(self, rects: list[QRectF]):
        self.prepareGeometryChange()
        self.rects = rects
        bounds = QRectF()
        for r in rects:
            bounds = bounds.united(r)
        self._bounds = bounds.adjusted(-1, -1, 1, 1) if rects else QRectF()
        self.update()

    def boundingRect(self) -> QRectF:
        return self._bounds

    def shape(self) -> QPainterPath:
        return QPainterPath()   # nunca tapa a los elementos de debajo al buscar qué hay bajo el ratón

    def paint(self, p: QPainter, opt, widget=None):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self.COLOR)
        for r in self.rects:
            p.drawRect(r)


# --------------------------------------------------------------------------
# Lienzo
# --------------------------------------------------------------------------

class PdfCanvas(QGraphicsView):
    zoom_changed = Signal(float)
    page_changed = Signal(int)
    status = Signal(str)
    edits_changed = Signal()
    tool_finished = Signal()
    files_dropped = Signal(list)
    selection_styles = Signal(object)  # elemento seleccionado (para sincronizar la barra)
    tool_requested = Signal(str)
    notice = Signal(str)               # aviso informativo (p. ej. «Texto copiado»)
    history_changed = Signal()         # cambió el historial de «Volver»
    search_requested = Signal(str)     # «Buscar en el documento» desde la selección
    tool_changed = Signal(str)         # cambió la herramienta (también las de extensiones: «ext:…»)
    repintado = Signal(object)         # páginas que una extensión mandó a redibujar (lista, o None = todas)

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(CANVAS_BG)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
                            | QPainter.RenderHint.TextAntialiasing)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAcceptDrops(True)
        self.doc: pymupdf.Document | None = None
        self.path: str | None = None
        self.password: str | None = None
        self.pages: list[PageItem] = []
        self.zoom = 1.0
        self.fit: str | None = "width"
        self.tool = "select"
        self.colors = {k: QColor(v) for k, v in TINTAS_INICIALES.items()}
        self.line_width = 2.0
        self.font_size = 12
        self.family = core.SANS
        self.bold = self.italic = False
        self.items_order: list[QGraphicsItem] = []
        self.undo_stack: list[tuple[str, list[QGraphicsItem]]] = []
        self.redo_stack: list[tuple[str, list[QGraphicsItem]]] = []    # lo deshecho, para «Rehacer»
        self.disco: tuple[int, int] | None = None     # tamaño y fecha del archivo al abrirlo (¿cambió afuera?)
        self.hits: list[tuple[int, QRectF]] = []
        self.hit_items: list[QGraphicsRectItem] = []
        self.hit_index = -1
        self.current_page = 0
        self._drag = None
        # Enganches de las extensiones (ver extensiones.py): la página del visor dueña del lienzo y si
        # el arrastre en curso se lo quedó una extensión.
        self.visor = None
        self._ext_captura = False
        # Vista: una o dos páginas por fila, portada sola, modo noche y giro de la vista (no del archivo).
        self.view_mode = "una"
        self.cover_alone = False
        self.night = False
        self.view_rot = 0
        self._rot = QTransform()          # escena → «espacio de vista» (girado, sin zoom)
        self._lay: list[QRectF] = []      # rectángulo de cada página en el espacio de vista
        self._rows: list[list[int]] = []
        self._row_of: list[int] = []
        self._row_limits: list[float] = []
        self._lay_w = 0.0
        # Texto y enlaces: cachés por página (se vacían al abrir otro archivo).
        self._words: dict[int, list[visor_texto.Palabra]] = {}
        self._vertical: dict[int, bool] = {}
        self._links: dict[int, list[visor_texto.Enlace]] = {}
        self._scanned: dict[int, bool] = {}
        self._full_rects: dict[int, list] = {}
        self.sel_anchor: tuple[int, int] | None = None
        self.sel_focus: tuple[int, int] | None = None
        self.overlay: SelectionOverlay | None = None
        # Ratón con la herramienta Seleccionar.
        self._press: dict | None = None
        self._pan: tuple[QPointF, int, int] | None = None
        self._space_down = False
        self._space_panned = False
        self._space_shift = False
        self._last_dbl = (0.0, QPointF())
        self._last_mouse: QPointF | None = None
        self._hover_link = None
        self._last_scan_msg = 0.0
        self._back: list[tuple[int, float, float]] = []
        self._fwd: list[tuple[int, float, float]] = []
        self.autoscroll_timer = QTimer(self)
        self.autoscroll_timer.setInterval(30)
        self.autoscroll_timer.timeout.connect(self._autoscroll_tick)
        self.viewport().setMouseTracking(True)
        self.render_timer = QTimer(self)
        self.render_timer.setSingleShot(True)
        self.render_timer.setInterval(30)
        self.render_timer.timeout.connect(self.render_visible)
        self.verticalScrollBar().valueChanged.connect(self._scrolled)
        self.horizontalScrollBar().valueChanged.connect(self._scrolled)
        self.scene().selectionChanged.connect(self._selection_changed)
        self.set_tool("select")

    # ---------------------------------------------------------------- documento
    def load(self, path: str, password: str | None) -> bool:
        with PDF_LOCK:
            doc = core.open_pdf(path, password)
            sizes = [(p.rect.width, p.rect.height) for p in doc]
        self.close_doc()
        self._press = self._pan = self._drag = None
        self._ext_captura = False
        self.autoscroll_timer.stop()
        # Primero se sueltan las listas: al vaciar la escena, Qt avisa que cambió la selección y quien
        # responda (los botones) no debe recorrer elementos que ya se borraron.
        self.pages, self.items_order, self.undo_stack, self.redo_stack = [], [], [], []
        self.scene().clear()
        self.disco = guardado.firma_archivo(path)
        self.hits, self.hit_items, self.hit_index = [], [], -1
        self._words, self._vertical, self._links, self._scanned, self._full_rects = {}, {}, {}, {}, {}
        self.sel_anchor = self.sel_focus = None
        self._hover_link = None
        self._back, self._fwd = [], []
        self.view_rot = 0
        self.doc, self.path, self.password = doc, path, password
        for i, (w, h) in enumerate(sizes):
            item = PageItem(i, w, h)
            self.scene().addItem(item)
            self.pages.append(item)
        self.overlay = SelectionOverlay()
        self.scene().addItem(self.overlay)
        self._layout()
        self._apply_page_brush()
        self.current_page = 0
        self.fit = "width"
        self.set_zoom(self.zoom, keep_fit=True)
        self.apply_fit()
        self.verticalScrollBar().setValue(self.verticalScrollBar().minimum())
        self.page_changed.emit(0)
        self.edits_changed.emit()
        self.history_changed.emit()
        self.schedule_render()
        return True

    # ---------------------------------------------------------------- colocación de páginas
    def _layout(self):
        """Coloca las páginas en filas de una o dos (con la portada sola si se pide).

        Se calcula en el «espacio de vista», con el ancho y el alto intercambiados si la vista está
        girada, y luego se pasa a la escena con el giro inverso: la escena sigue en puntos PDF sin girar,
        así los elementos de edición, la búsqueda, la selección y los enlaces no cambian de coordenadas."""
        rot = self.view_rot % 360
        self._rot = QTransform()
        self._rot.rotate(rot)
        inv = self._rot.inverted()[0]
        swap = rot in (90, 270)
        sizes = [(p.h, p.w) if swap else (p.w, p.h) for p in self.pages]
        n = len(sizes)
        lay: list[QRectF] = [QRectF() for _ in range(n)]
        rows: list[list[int]] = []
        y = 0.0
        if self.view_mode == "dos" and n:
            idx = list(range(n))
            pairs: list[tuple[int | None, int | None]] = []
            if self.cover_alone:
                pairs.append((None, 0))
                idx = idx[1:]
            for k in range(0, len(idx), 2):
                pair = idx[k:k + 2]
                pairs.append((pair[0], pair[1] if len(pair) > 1 else None))
            left_w = max((sizes[a][0] for a, _ in pairs if a is not None), default=0.0)
            right_w = max((sizes[b][0] for _, b in pairs if b is not None), default=0.0)
            right_x = left_w + GAP if left_w else 0.0
            for a, b in pairs:
                h = max(sizes[i][1] for i in (a, b) if i is not None)
                if a is not None:
                    lay[a] = QRectF(left_w - sizes[a][0], y, *sizes[a])
                if b is not None:
                    lay[b] = QRectF(right_x, y, *sizes[b])
                rows.append([i for i in (a, b) if i is not None])
                y += h + GAP
            total_w = (left_w + GAP + right_w) if (left_w and right_w) else (left_w or right_w)
        else:
            total_w = max((w for w, _ in sizes), default=595)
            for i, (w, h) in enumerate(sizes):
                lay[i] = QRectF((total_w - w) / 2, y, w, h)
                rows.append([i])
                y += h + GAP
        self._lay, self._rows, self._lay_w = lay, rows, total_w
        self._row_of = [0] * n
        self._row_limits = []
        for k, row in enumerate(rows):
            for i in row:
                self._row_of[i] = k
            self._row_limits.append(max(lay[i].bottom() for i in row) + GAP / 2)
        for i, page in enumerate(self.pages):
            page.setPos(inv.mapRect(lay[i]).topLeft())
        bounds = QRectF(-MARGIN, -MARGIN, total_w + 2 * MARGIN, max(y - GAP, 1) + 2 * MARGIN)
        self.scene().setSceneRect(inv.mapRect(bounds))

    def _to_layout(self, sp: QPointF) -> QPointF:
        return self._rot.map(sp)

    def _from_layout(self, lp: QPointF) -> QPointF:
        return self._rot.inverted()[0].map(lp)

    def _row_rect(self, i: int) -> QRectF:
        r = QRectF()
        for j in self._rows[self._row_of[i]]:
            r = r.united(self._lay[j])
        return r

    def _edit_owners(self) -> list[tuple[QGraphicsItem, int]]:
        """Página a la que pertenece cada elemento de edición (también los quitados que «Deshacer»
        puede volver a poner)."""
        seen, items = set(), []
        for it in list(self.items_order) + [i for a, its in self.undo_stack + self.redo_stack if a != "ext"
                                            for i in its]:
            if id(it) not in seen:
                seen.add(id(it))
                items.append(it)
        owners = []
        for it in items:
            if isinstance(it, ReplaceTextItem):
                owners.append((it, it.page_index))
            elif isinstance(it, _Editable):
                c = it.sceneBoundingRect().center()
                page = self.page_at(c) or self._nearest_page(c)
                if page is not None:
                    owners.append((it, page.index))
        return owners

    def relayout(self):
        """Vuelve a colocar las páginas (cambio de vista) sin que nada se salga de su página."""
        if not self.pages:
            self.set_zoom(self.zoom, keep_fit=True)
            return
        editing = self.editing_text()
        if editing is not None:
            editing.clearFocus()
        cur = self.current_page
        old = [p.pos() for p in self.pages]
        owners = self._edit_owners()
        self._layout()
        for it, idx in owners:
            d = self.pages[idx].pos() - old[idx]
            if d.isNull():
                continue
            it.moveBy(d.x(), d.y())
            if isinstance(it, ReplaceTextItem):
                it.cover.moveBy(d.x(), d.y())
                it.start_pos = it.start_pos + d
        for k, (pno, rect) in enumerate(self.hits):
            d = self.pages[pno].pos() - old[pno]
            self.hits[k] = (pno, rect.translated(d))
            self.hit_items[k].setRect(self.hit_items[k].rect().translated(d))
        self._update_selection()
        self.set_zoom(self.zoom, keep_fit=True)
        if self.fit:
            self.apply_fit()
        self.go_to_page(cur)
        self.schedule_render()

    def set_view(self, mode: str, cover_alone: bool | None = None):
        mode = "dos" if mode == "dos" else "una"
        cover = self.cover_alone if cover_alone is None else bool(cover_alone)
        if mode == self.view_mode and cover == self.cover_alone:
            return
        self.view_mode, self.cover_alone = mode, cover
        self.relayout()

    def rotate_view(self, delta: int = 90) -> bool:
        """Gira cómo se ven las páginas (no el archivo). Solo sin cambios pendientes."""
        if self.doc is None:
            return False
        if self.has_changes():
            self.status.emit("Guarda o descarta tus cambios antes de girar la vista.")
            return False
        self.view_rot = (self.view_rot + delta) % 360
        if self.view_rot and self.tool != "select":
            self.set_tool("select")
        self.relayout()
        return True

    def set_night(self, on: bool):
        on = bool(on)
        if on == self.night:
            return
        self.night = on
        self.setBackgroundBrush(CANVAS_BG_NIGHT if on else CANVAS_BG)
        self._apply_page_brush()
        for p in self.pages:
            p.rendered = 0.0     # se vuelve a dibujar (sin borrar la imagen actual: no parpadea)
        self.scene().update()
        self.schedule_render()

    def _apply_page_brush(self):
        brush = QColor("black") if self.night else QColor("white")
        for p in self.pages:
            p.setBrush(brush)
        for it in self.scene().items():
            if isinstance(it, QGraphicsRectItem) and it.data(0) == "cover":
                it.setBrush(brush)

    def close_doc(self):
        if self.doc is not None:
            with PDF_LOCK:
                if not self.doc.is_closed:
                    self.doc.close()
            self.doc = None

    def reopen(self):
        """Reabre el archivo actual tras cerrarlo (p. ej. si falló un guardado sobre sí mismo)."""
        if self.doc is None and self.path:
            with PDF_LOCK:
                self.doc = core.open_pdf(self.path, self.password)
            for p in self.pages:
                p.rendered = 0
            self.schedule_render()

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def has_changes(self) -> bool:
        if self.visor is not None and extensiones.cambios_visor(self.visor):
            return True
        for it in self.items_order:
            if it.scene() is None:
                continue
            if isinstance(it, ReplaceTextItem) and it.unchanged():
                continue
            if type(it) is TextItem and not it.toPlainText().strip():
                continue
            if not isinstance(it, _Editable):
                continue
            return True
        return False

    def editing_text(self) -> TextItem | None:
        focus = self.scene().focusItem()
        if isinstance(focus, TextItem) and focus.textInteractionFlags() & Qt.TextInteractionFlag.TextEditorInteraction:
            return focus
        return None

    # ---------------------------------------------------------------- zoom y render
    def set_zoom(self, z: float, keep_fit: bool = False):
        z = max(0.1, min(6.0, z))
        if not keep_fit:
            self.fit = None
        self.zoom = z
        t = QTransform()
        t.rotate(self.view_rot)
        t.scale(z, z)
        self.setTransform(t)
        self.zoom_changed.emit(z)
        self.schedule_render()

    def zoom_by(self, factor: float):
        self.set_zoom(self.zoom * factor)

    def apply_fit(self):
        if not self.pages or not self.fit:
            return
        # El margen de la escena también se escala con el zoom, así que va dentro del cociente.
        vbar = self.verticalScrollBar()
        vw = self.viewport().width() - (0 if vbar.isVisible() else vbar.sizeHint().width()) - 6
        vh = self.viewport().height() - 6
        if self.fit == "width":
            if self.view_mode == "dos":
                ref_w = self._lay_w            # el ancho de la fila de dos páginas
            else:
                # Ancho típico (mediana): una página apaisada suelta no achica todas las demás.
                widths = sorted(r.width() for r in self._lay)
                ref_w = widths[len(widths) // 2]
            z = vw / (ref_w + 2 * MARGIN)
        else:
            row = self._row_rect(self.current_page)
            z = min(vw / (row.width() + 2 * MARGIN), vh / (row.height() + 2 * MARGIN))
        z = max(z, 0.1)
        anchor = self.transformationAnchor()
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.set_zoom(z, keep_fit=True)
        self.setTransformationAnchor(anchor)
        hbar = self.horizontalScrollBar()
        hbar.setValue((hbar.minimum() + hbar.maximum()) // 2)
        if self.fit == "page":
            self.go_to_page(self.current_page)

    def set_fit(self, mode: str):
        self.fit = mode
        self.apply_fit()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.fit:
            self.apply_fit()
        self.schedule_render()

    def schedule_render(self):
        if not self.render_timer.isActive():
            self.render_timer.start()

    def _visible_scene_rect(self) -> QRectF:
        return self.mapToScene(self.viewport().rect()).boundingRect()

    def render_visible(self):
        if self.doc is None or not self.pages:
            return
        if ctx.busy or not PDF_LOCK.acquire(blocking=False):
            self.render_timer.start(150)
            return
        pending = False
        try:
            vis = self._visible_scene_rect()
            vis = vis.adjusted(0, -vis.height() * 0.5, 0, vis.height() * 0.5)
            scale = self.zoom * self.devicePixelRatioF()
            visible = [p for p in self.pages if p.sceneBoundingRect().intersects(vis)]
            start = time.perf_counter()
            for p in visible:
                s = min(scale, math.sqrt(MAX_PIXELS / max(p.w * p.h, 1)))
                if abs(p.rendered - s) / s > 0.02:
                    if time.perf_counter() - start > 0.06:
                        pending = True
                        break
                    img = qimage_from_render(core.render_scaled(self.doc, p.index, s))
                    if self.night:
                        img.invertPixels()   # solo la imagen en pantalla: el archivo no cambia
                    p.set_image(img, s)
            if visible:
                lo, hi = visible[0].index, visible[-1].index
                for p in self.pages:
                    if p.rendered and (p.index < lo - 3 or p.index > hi + 3):
                        p.clear_image()
        finally:
            PDF_LOCK.release()
        if pending:
            self.render_timer.start(0)

    def _scrolled(self, *_):
        self.schedule_render()
        if not self.pages:
            return
        c = self.mapToScene(self.viewport().rect().center())
        idx = self.page_index_at_y(self._to_layout(c).y())
        if len(self._row_of) != len(self.pages):
            return
        cur = min(self.current_page, len(self.pages) - 1)
        # En la vista de dos páginas, la página actual no cambia mientras siga en la misma fila.
        if idx != self.current_page and self._row_of[idx] != self._row_of[cur]:
            self.current_page = idx
            self.page_changed.emit(idx)

    def page_index_at_y(self, y: float) -> int:
        """Primera página de la fila que ocupa la altura ``y`` (en la escena, o en el espacio de vista si
        la vista está girada)."""
        if not self._rows:
            return 0
        k = bisect.bisect_right(self._row_limits, y)
        return self._rows[k][0] if k < len(self._rows) else len(self.pages) - 1

    def page_at(self, sp: QPointF) -> PageItem | None:
        for p in self.pages:
            if p.sceneBoundingRect().contains(sp):
                return p
        return None

    def _nearest_page(self, sp: QPointF) -> PageItem | None:
        if not self.pages:
            return None
        lp = self._to_layout(sp)
        row = self._rows[self._row_of[self.page_index_at_y(lp.y())]]
        return min((self.pages[i] for i in row), key=lambda p: abs(self._lay[p.index].center().x() - lp.x()))

    def go_to_page(self, i: int):
        if not self.pages:
            return
        i = max(0, min(i, len(self.pages) - 1))
        lay = self._lay[i]
        vw = self.viewport().width() / self.zoom
        vh = self.viewport().height() / self.zoom
        row = self._row_rect(i)
        cx = row.center().x() if row.width() <= vw else lay.center().x()
        self.centerOn(self._from_layout(QPointF(cx, lay.top() - 10 / self.zoom + vh / 2)))
        self.current_page = i
        self.page_changed.emit(i)

    def go_to_dest(self, i: int, point: tuple[float, float] | None = None, remember: bool = True):
        """Va a la página ``i`` y, si hay punto de destino, a esa altura (enlaces y marcadores)."""
        if not self.pages:
            return
        i = max(0, min(int(i), len(self.pages) - 1))
        if remember:
            self.push_history()
        if point is None:
            self.go_to_page(i)
            return
        page = self.pages[i]
        x = min(max(float(point[0]), 0.0), page.w)
        y = min(max(float(point[1]), 0.0), page.h)
        target = self._to_layout(page.pos() + QPointF(x, y))
        vw = self.viewport().width() / self.zoom
        vh = self.viewport().height() / self.zoom
        row = self._row_rect(i)
        cx = row.center().x() if row.width() <= vw else target.x() - 20 / self.zoom + vw / 2
        cy = max(target.y() - 24 / self.zoom, self._lay[i].top() - 10 / self.zoom) + vh / 2
        self.centerOn(self._from_layout(QPointF(cx, cy)))
        self.current_page = i
        self.page_changed.emit(i)

    def scroll_screen(self, direction: int):
        """Avanza o retrocede una pantalla (Av Pág, Re Pág, barra espaciadora)."""
        bar = self.verticalScrollBar()
        bar.setValue(bar.value() + direction * max(20, int(self.viewport().height() * 0.9)))

    # ---------------------------------------------------------------- historial («Volver»)
    def _position(self) -> tuple[int, float, float] | None:
        if not self.pages:
            return None
        c = self.mapToScene(self.viewport().rect().center())
        page = self.page_at(c) or self.pages[min(self.current_page, len(self.pages) - 1)]
        return (page.index, c.x() - page.x(), c.y() - page.y())

    def _restore_position(self, pos: tuple[int, float, float]):
        i, dx, dy = pos
        page = self.pages[max(0, min(i, len(self.pages) - 1))]
        self.centerOn(page.pos() + QPointF(dx, dy))
        if self.current_page != page.index:
            self.current_page = page.index
            self.page_changed.emit(page.index)

    def push_history(self):
        pos = self._position()
        if pos is None:
            return
        self._back.append(pos)
        del self._back[:-HISTORY_MAX]
        self._fwd.clear()
        self.history_changed.emit()

    def can_go_back(self) -> bool:
        return bool(self._back)

    def can_go_forward(self) -> bool:
        return bool(self._fwd)

    def history_back(self) -> bool:
        if not self._back:
            return False
        cur = self._position()
        if cur is not None:
            self._fwd.append(cur)
        self._restore_position(self._back.pop())
        self.history_changed.emit()
        return True

    def history_forward(self) -> bool:
        if not self._fwd:
            return False
        cur = self._position()
        if cur is not None:
            self._back.append(cur)
        self._restore_position(self._fwd.pop())
        self.history_changed.emit()
        return True

    def wheelEvent(self, e):
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom_by(1.15 ** (e.angleDelta().y() / 120))
            e.accept()
            return
        super().wheelEvent(e)

    # ---------------------------------------------------------------- búsqueda
    def clear_search(self):
        for it in self.hit_items:
            if it.scene():
                self.scene().removeItem(it)
        self.hits, self.hit_items, self.hit_index = [], [], -1

    def search(self, term: str) -> int:
        self.clear_search()
        if self.doc is None or not term.strip():
            return 0
        with PDF_LOCK:
            found = core.search_text(self.doc, term)
        for pno, r in found:
            page = self.pages[pno]
            rect = QRectF(r.x0, r.y0, r.width, r.height).translated(page.pos())
            item = QGraphicsRectItem(rect.adjusted(-1, -1, 1, 1))
            item.setPen(QPen(Qt.PenStyle.NoPen))
            item.setBrush(HIT_COLOR)
            item.setZValue(Z_SEARCH)
            self.scene().addItem(item)
            self.hits.append((pno, rect))
            self.hit_items.append(item)
        if self.hits:
            self.goto_hit(0)
        return len(self.hits)

    def goto_hit(self, k: int):
        if not self.hits:
            return
        k %= len(self.hits)
        if 0 <= self.hit_index < len(self.hit_items):
            self.hit_items[self.hit_index].setBrush(HIT_COLOR)
        self.hit_index = k
        self.hit_items[k].setBrush(HIT_ACTUAL_COLOR)
        self.centerOn(self.hits[k][1].center())

    # ---------------------------------------------------------------- herramientas
    def set_tool(self, tool: str):
        self.tool = tool
        # El desplazamiento con la mano lo hace el propio lienzo (ver mousePressEvent): así la
        # herramienta Seleccionar puede, además, seleccionar texto y seguir enlaces.
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        if tool == "select":
            self._set_cursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.clear_selection()
            cursor = Qt.CursorShape.IBeamCursor if tool in ("text", "edittext") else Qt.CursorShape.CrossCursor
            self.viewport().setCursor(cursor)
        hint = next((t[3] for t in TOOLS if t[0] == tool), "")
        self.status.emit(hint)
        self.tool_changed.emit(tool)

    def _set_cursor(self, shape: Qt.CursorShape):
        if self.viewport().cursor().shape() != shape:
            self.viewport().setCursor(shape)

    # ---------------------------------------------------------------- texto: palabras y enlaces por página
    def _doc_ready(self, i: int) -> bool:
        # Mientras se guarda sobre el mismo archivo el documento está cerrado: no se guarda nada en caché.
        return self.doc is not None and not self.doc.is_closed and 0 <= i < len(self.pages)

    def words_for(self, i: int) -> list[visor_texto.Palabra]:
        if i not in self._words:
            if not self._doc_ready(i):
                return []
            with PDF_LOCK:
                try:
                    page = self.doc[i]
                    words = visor_texto.palabras_pagina(page)
                    vertical = page.rotation in (90, 270)
                except Exception:  # noqa: BLE001 — una página dañada no debe romper el visor
                    words, vertical = [], False
            self._words[i], self._vertical[i] = words, vertical
        return self._words[i]

    def links_for(self, i: int) -> list[visor_texto.Enlace]:
        if i not in self._links:
            if not self._doc_ready(i):
                return []
            with PDF_LOCK:
                try:
                    links = visor_texto.enlaces_pagina(self.doc[i])
                except Exception:  # noqa: BLE001
                    links = []
            self._links[i] = links
        return self._links[i]

    def _is_scanned(self, i: int) -> bool:
        if i not in self._scanned:
            if not self._doc_ready(i):
                return False
            scanned = False
            if not self.words_for(i):
                with PDF_LOCK:
                    try:
                        scanned = core.image_coverage(self.doc[i]) > 0.5
                    except Exception:  # noqa: BLE001
                        scanned = False
            self._scanned[i] = scanned
        return self._scanned[i]

    def _no_text_message(self, i: int, force: bool = False):
        now = time.monotonic()
        if not force and now - self._last_scan_msg < 4:
            return
        self._last_scan_msg = now
        if self._is_scanned(i):
            self.status.emit(SCANNED_MSG)
        elif force:
            self.status.emit("Esta página no tiene texto para seleccionar.")

    def _word_at(self, sp: QPointF, tolerance: float = 2.0) -> tuple[PageItem, int] | None:
        page = self.page_at(sp)
        if page is None:
            return None
        words = self.words_for(page.index)
        local = sp - page.pos()
        w = visor_texto.palabra_en(words, local.x(), local.y(), tolerance) if words else None
        return (page, w) if w is not None else None

    def link_at(self, sp: QPointF) -> visor_texto.Enlace | None:
        page = self.page_at(sp)
        if page is None or self.doc is None:
            return None
        local = sp - page.pos()
        return visor_texto.enlace_en(self.links_for(page.index), local.x(), local.y())

    def follow_link(self, link: visor_texto.Enlace):
        if link.tipo == "pagina":
            self.go_to_dest(link.pagina, link.punto)
        elif link.tipo == "web":
            if visor_paneles.confirmar_web(self, link.url):
                QDesktopServices.openUrl(QUrl(link.url))
        else:
            self.status.emit("Por seguridad, RonyPDF no abre este tipo de enlace.")

    # ---------------------------------------------------------------- selección de texto
    def has_selection(self) -> bool:
        return self.sel_anchor is not None and self.sel_focus is not None

    def _sel_pages(self) -> list[tuple[int, int, int]]:
        """[(página, primera, última)] de la selección, en orden de lectura."""
        if not self.has_selection():
            return []
        (pa, wa), (pb, wb) = sorted([self.sel_anchor, self.sel_focus])
        out = []
        for p in range(pa, pb + 1):
            words = self.words_for(p)
            if not words:
                continue
            lo = wa if p == pa else 0
            hi = wb if p == pb else len(words) - 1
            out.append((p, max(0, lo), min(hi, len(words) - 1)))
        return out

    def _line_rects(self, p: int, lo: int, hi: int) -> list:
        words = self.words_for(p)
        if lo == 0 and hi == len(words) - 1:
            if p not in self._full_rects:
                self._full_rects[p] = visor_texto.rango(words, lo, hi)[1]
            return self._full_rects[p]
        return visor_texto.rango(words, lo, hi)[1]

    def _scene_rects(self, p: int, rects) -> list[QRectF]:
        origin = self.pages[p].pos()
        return [QRectF(QPointF(r[0], r[1]), QPointF(r[2], r[3])).translated(origin) for r in rects]

    def _update_selection(self):
        if self.overlay is None:
            return
        rects: list[QRectF] = []
        for p, lo, hi in self._sel_pages():
            rects += self._scene_rects(p, self._line_rects(p, lo, hi))
        self.overlay.set_rects(rects)

    def selected_text(self) -> str:
        parts = [visor_texto.rango(self.words_for(p), lo, hi)[0] for p, lo, hi in self._sel_pages()]
        return "\n\n".join(t for t in parts if t)

    def select_words(self, pagina: int, a: int, b: int):
        words = self.words_for(pagina)
        if not words:
            self.clear_selection()
            return
        a = max(0, min(a, len(words) - 1))
        b = max(0, min(b, len(words) - 1))
        self.sel_anchor, self.sel_focus = (pagina, a), (pagina, b)
        self._update_selection()

    def select_all_page(self):
        if self.doc is None or not self.pages:
            return
        p = min(self.current_page, len(self.pages) - 1)
        words = self.words_for(p)
        if not words:
            self.clear_selection()
            self._no_text_message(p, force=True)
            return
        self.select_words(p, 0, len(words) - 1)

    def clear_selection(self):
        self.sel_anchor = self.sel_focus = None
        if self.overlay is not None:
            self.overlay.set_rects([])

    def copy_selection(self) -> str:
        text = self.selected_text()
        if not text:
            return ""
        QApplication.clipboard().setText(text)
        self.notice.emit(f"Texto copiado ({len(text)} caracteres)")
        return text

    def highlight_selection(self) -> list[HighlightItem]:
        """Convierte la selección en resaltados (uno por página) con los rectángulos exactos."""
        if self.view_rot:
            self.status.emit(ROTATED_MSG)
            return []
        items = [HighlightItem(self._scene_rects(p, visor_texto.rango(self.words_for(p), lo, hi)[1]),
                               self.colors["highlight"]) for p, lo, hi in self._sel_pages()]
        if items:
            self._register(items)
            self.clear_selection()
        return items

    def search_selection(self) -> str:
        term = " ".join(self.selected_text().split())[:120]
        if term:
            self.search_requested.emit(term)
        return term

    def selection_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.addAction(glifo_menu("duplicate"), "Copiar\tCtrl+C", self.copy_selection)
        hl = menu.addAction(glifo_menu("highlighter"), "Resaltar", self.highlight_selection)
        hl.setEnabled(not self.view_rot)
        menu.addAction(glifo_menu("search"), "Buscar en el documento", self.search_selection)
        if self.visor is not None:
            extensiones.menu_seleccion_visor(self.visor, menu)
        return menu

    def _over_selection(self, sp: QPointF) -> bool:
        return self.overlay is not None and any(r.adjusted(-2, -2, 2, 2).contains(sp) for r in self.overlay.rects)

    def _select_line(self, page: int, w: int):
        a, b = visor_texto.renglon_de(self.words_for(page), w)
        self.select_words(page, a, b)

    def _extend_selection(self, pos: QPointF):
        """Mueve el extremo de la selección a la palabra más cercana al ratón (puede ser otra página)."""
        if self.sel_anchor is None:
            return
        sp = self.mapToScene(pos.toPoint())
        page = self.page_at(sp) or self._nearest_page(sp)
        if page is None:
            return
        words = self.words_for(page.index)
        anchor_page = self.sel_anchor[0]
        if not words:
            # página sin texto: el extremo queda al final (o al principio) de la página con texto más cercana
            step = -1 if page.index > anchor_page else 1
            q = page.index
            while q != anchor_page and not self.words_for(q):
                q += step
            focus = (q, len(self.words_for(q)) - 1 if step < 0 else 0)
        else:
            local = sp - page.pos()
            w = visor_texto.palabra_cercana(words, local.x(), local.y(), self._vertical.get(page.index, False))
            focus = (page.index, w)
        if focus != self.sel_focus:
            self.sel_focus = focus
            self._update_selection()

    def _autoscroll_tick(self):
        d = self._press
        if not d or d.get("mode") != "text" or self._last_mouse is None:
            self.autoscroll_timer.stop()
            return
        pos, vp, edge = self._last_mouse, self.viewport().rect(), 28
        dy = (pos.y() - edge) if pos.y() < edge else (pos.y() - (vp.height() - edge)) if pos.y() > vp.height() - edge else 0
        dx = (pos.x() - edge) if pos.x() < edge else (pos.x() - (vp.width() - edge)) if pos.x() > vp.width() - edge else 0
        if not dx and not dy:
            return
        for bar, delta in ((self.verticalScrollBar(), dy), (self.horizontalScrollBar(), dx)):
            if delta:
                bar.setValue(bar.value() + int(max(-60, min(60, delta * 0.7))) or (1 if delta > 0 else -1))
        self._extend_selection(pos)

    def _editable_at(self, pos) -> QGraphicsItem | None:
        for it in self.items(pos):
            if isinstance(it, _Editable):
                return it
        return None

    def _start_pan(self, pos: QPointF):
        self._pan = (QPointF(pos), self.horizontalScrollBar().value(), self.verticalScrollBar().value())
        self._set_cursor(Qt.CursorShape.ClosedHandCursor)

    def _do_pan(self, pos: QPointF):
        if self._pan is None:
            return
        start, h0, v0 = self._pan
        d = pos - start
        self.horizontalScrollBar().setValue(h0 - round(d.x()))
        self.verticalScrollBar().setValue(v0 - round(d.y()))

    def _hover(self, pos: QPointF):
        """Cursor y ayuda según lo que haya bajo el ratón (herramienta Seleccionar)."""
        if self._space_down:
            self._show_link_tip(None, pos)
            self._set_cursor(Qt.CursorShape.OpenHandCursor)
            return
        item = self._editable_at(pos.toPoint())
        if item is not None:
            self._show_link_tip(None, pos)
            if not item.hasCursor():
                self._set_cursor(Qt.CursorShape.SizeAllCursor)
            return
        sp = self.mapToScene(pos.toPoint())
        link = self.link_at(sp)
        self._show_link_tip(link, pos)
        if link is not None:
            self._set_cursor(Qt.CursorShape.PointingHandCursor)
        elif self._word_at(sp, 1.0) is not None:
            self._set_cursor(Qt.CursorShape.IBeamCursor)
        else:
            self._set_cursor(Qt.CursorShape.OpenHandCursor)

    def _show_link_tip(self, link, pos: QPointF):
        if link is self._hover_link:
            return
        self._hover_link = link
        if link is None:
            QToolTip.hideText()
        else:
            QToolTip.showText(self.viewport().mapToGlobal(pos.toPoint()), link.ayuda, self.viewport())

    def color_key(self, tool: str | None = None) -> str:
        tool = tool or self.tool
        if tool in ("text", "edittext"):
            return "text"
        if tool == "highlight":
            return "highlight"
        return "draw"

    def current_color(self) -> QColor:
        return self.colors[self.color_key()]

    def _register(self, items: list[QGraphicsItem]):
        for it in items:
            if it.scene() is None:
                self.scene().addItem(it)
        self.items_order.extend(i for i in items if i not in self.items_order)
        self.undo_stack.append(("add", items))
        self.redo_stack.clear()           # una acción nueva invalida lo que se podía rehacer
        self.edits_changed.emit()

    def _remove_empty_text(self, item: TextItem):
        for it in (item, getattr(item, "cover", None)):
            if it is not None and it.scene():
                self.scene().removeItem(it)
        self.edits_changed.emit()

    def add_text(self, sp: QPointF, text: str = "") -> TextItem:
        item = TextItem(text, self.font_size, self.family, self.bold, self.italic, self.colors["text"])
        item.on_empty = self._remove_empty_text
        ascent = QFontMetricsF(item.font()).ascent()
        item.setPos(sp.x(), sp.y() - ascent * 0.7)
        self._register([item])
        return item

    def edit_text_at(self, page: PageItem, sp: QPointF) -> ReplaceTextItem | None:
        local = sp - page.pos()
        with PDF_LOCK:
            info = core.text_line_at(self.doc[page.index], local.x(), local.y())
        if not info:
            self.status.emit("Aquí no hay texto editable. Si el PDF es escaneado, primero aplica OCR.")
            return None
        if not info["horizontal"]:
            self.status.emit("Solo se puede editar texto horizontal.")
            return None
        for it in self.items_order:
            if isinstance(it, ReplaceTextItem) and it.scene() and it.page_index == page.index \
                    and it.info["rect"] == info["rect"]:
                it.start_editing()
                return it
        item = ReplaceTextItem(info, page)
        if self.night:
            item.cover.setBrush(QColor("black"))
        item.on_empty = None  # vaciarlo es válido: borra la línea
        self._register([item.cover, item])
        item.start_editing(select_all=False)
        return item

    def add_image(self, path: str) -> ShapeItem | None:
        data = Path(path).read_bytes()
        img = QImage.fromData(data)
        if img.isNull():
            QMessageBox.warning(self, "Imagen", "No se pudo leer la imagen.")
            return None
        if Path(path).suffix.lower() not in (".png", ".jpg", ".jpeg"):
            from PySide6.QtCore import QBuffer, QIODevice
            buf = QBuffer()
            buf.open(QIODevice.OpenModeFlag.WriteOnly)
            img.save(buf, "PNG")
            data = bytes(buf.data())
        page = self.pages[self.current_page]
        vis = self._visible_scene_rect().intersected(page.sceneBoundingRect())
        center = vis.center() if not vis.isEmpty() else page.sceneBoundingRect().center()
        w = min(200.0, page.w * 0.5)
        h = w * img.height() / max(img.width(), 1)
        if h > page.h * 0.5:
            h = page.h * 0.5
            w = h * img.width() / max(img.height(), 1)
        item = ShapeItem("image", QRectF(center.x() - w / 2, center.y() - h / 2, w, h), QColor(), 0, img, data)
        self._register([item])
        self.scene().clearSelection()
        item.setSelected(True)
        return item

    def add_shape(self, kind: str, rect: QRectF) -> ShapeItem:
        item = ShapeItem(kind, rect, self.colors["draw"], self.line_width)
        self._register([item])
        return item

    def add_line(self, kind: str, p1: QPointF, p2: QPointF) -> LineItem:
        item = LineItem(kind, p1, p2, self.colors["draw"], self.line_width)
        self._register([item])
        return item

    def add_highlight(self, page: PageItem, rect: QRectF) -> HighlightItem:
        local = rect.translated(-page.pos())
        with PDF_LOCK:
            words = core.words_in_rect(self.doc[page.index], (local.left(), local.top(), local.right(), local.bottom()))
        rects = [QRectF(QPointF(r[0], r[1]), QPointF(r[2], r[3])).translated(page.pos()) for r in words] or [rect]
        item = HighlightItem(rects, self.colors["highlight"])
        self._register([item])
        return item

    def delete_selected(self):
        items = [i for i in self.scene().selectedItems() if isinstance(i, _Editable)]
        if not items:
            return
        extra = [i.cover for i in items if isinstance(i, ReplaceTextItem)]
        removed = items + extra
        for it in removed:
            if it.scene():
                self.scene().removeItem(it)
        self.undo_stack.append(("del", removed))
        self.redo_stack.clear()
        self.edits_changed.emit()

    def registrar_ext(self, accion):
        """Apila en «Deshacer» un cambio hecho por una extensión: un objeto con ``deshacer()`` y
        ``rehacer()`` (ver extensiones.py)."""
        self.undo_stack.append(("ext", accion))
        self.redo_stack.clear()
        self.edits_changed.emit()

    def refrescar(self, paginas=None):
        """Vuelve a dibujar esas páginas (None = todas) y avisa para que se rehagan sus miniaturas.
        Lo usan las extensiones después de cambiar el documento en memoria."""
        n = len(self.pages)
        idx = list(range(n)) if paginas is None else [i for i in paginas if 0 <= i < n]
        for i in idx:
            self.pages[i].rendered = 0.0
        self.repintado.emit(None if paginas is None else idx)
        self.schedule_render()

    def punto_en_pagina(self, pos) -> tuple[int, float, float] | None:
        """(página, x, y) bajo una posición del lienzo (la de un evento del ratón), en puntos PDF de la
        página tal como se ve; None si ahí no hay página."""
        sp = self.mapToScene(pos.toPoint() if hasattr(pos, "toPoint") else pos)
        page = self.page_at(sp)
        if page is None:
            return None
        local = sp - page.pos()
        return page.index, local.x(), local.y()

    def seleccion_por_pagina(self) -> list[tuple[int, str, list[tuple[float, float, float, float]]]]:
        """El texto seleccionado, por página: (página, texto, un rectángulo por renglón), con los
        rectángulos en puntos PDF de la página tal como se ve."""
        out = []
        for p, lo, hi in self._sel_pages():
            texto, rects = visor_texto.rango(self.words_for(p), lo, hi)
            out.append((p, texto, rects))
        return out

    def _ext(self, tipo: str, e) -> bool:
        """Ofrece un evento del ratón o del teclado a las extensiones. True si alguna lo consumió."""
        return (self.visor is not None and self.doc is not None
                and extensiones.evento_visor(self.visor, tipo, e))

    def undo(self):
        while self.undo_stack:
            action, items = self.undo_stack.pop()
            if action == "ext":
                items.deshacer()
                self.redo_stack.append(("ext", items))
                self.edits_changed.emit()
                return
            if action == "add":
                live = [i for i in items if i.scene()]
                for it in live:
                    self.scene().removeItem(it)
                if not live:
                    continue
                self.redo_stack.append(("add", live))
            else:
                for it in items:
                    if it.scene() is None:
                        self.scene().addItem(it)
                self.redo_stack.append(("del", items))
            self.edits_changed.emit()
            return

    def redo(self):
        """Vuelve a hacer lo último que se deshizo: los elementos agregados vuelven a la página, los
        eliminados se quitan otra vez y las acciones de las extensiones llaman a su ``rehacer()``."""
        if not self.redo_stack:
            return
        action, items = self.redo_stack.pop()
        if action == "ext":
            items.rehacer()
        elif action == "add":
            for it in items:
                if it.scene() is None:
                    self.scene().addItem(it)
                if it not in self.items_order:
                    self.items_order.append(it)
        else:
            for it in items:
                if it.scene() is not None:
                    self.scene().removeItem(it)
        self.undo_stack.append((action, items))
        self.edits_changed.emit()

    def apply_style_to_selection(self, color: QColor | None = None, width: float | None = None, **text_style):
        for it in self.scene().selectedItems():
            if isinstance(it, TextItem):
                it.set_style(color=color, **text_style)
            elif isinstance(it, _Editable) and (color is not None or width is not None):
                it.apply_style(color=color, width=width)

    def _selection_changed(self):
        sel = [i for i in self.scene().selectedItems() if isinstance(i, _Editable)]
        self.selection_styles.emit(sel[0] if len(sel) == 1 else None)

    def collect_edits(self) -> tuple[list[core.Edit], int]:
        edits, skipped = [], 0
        for it in self.items_order:
            if it.scene() is None or not isinstance(it, _Editable):
                continue
            if isinstance(it, ReplaceTextItem):
                if it.unchanged():
                    continue
                origin = self.pages[it.page_index].pos()
                edits.append(it.to_edit(it.page_index, origin))
                continue
            center = it.sceneBoundingRect().center()
            page = self.page_at(center)
            if page is None:
                skipped += 1
                continue
            e = it.to_edit(page.index, page.pos())
            if e is not None:
                edits.append(e)
        return edits, skipped

    # ---------------------------------------------------------------- ratón y teclado
    def mousePressEvent(self, e):
        btn = e.button()
        self._last_mouse = e.position()
        if self._press is None and self._drag is None and self._ext("press", e):
            self._ext_captura = True
            e.accept()
            return
        # Botón central, o barra espaciadora presionada: siempre desplaza, con cualquier herramienta.
        if self.doc is not None and self._press is None and self._drag is None and (
                btn == Qt.MouseButton.MiddleButton or (btn == Qt.MouseButton.LeftButton and self._space_down)):
            self._space_panned = self._space_down
            self._press = {"mode": "pan", "button": btn, "pos": e.position(), "link": None, "word": None}
            self._start_pan(e.position())
            e.accept()
            return
        if self.tool == "select" and btn == Qt.MouseButton.LeftButton and self.doc is not None:
            if self._select_press(e):
                e.accept()
                return
            super().mousePressEvent(e)
            return
        if self.tool == "select" or btn != Qt.MouseButton.LeftButton or self.doc is None:
            super().mousePressEvent(e)
            return
        sp = self.mapToScene(e.position().toPoint())
        # un clic sobre un texto agregado lo edita en lugar de crear otro
        hit = self._editable_at(e.position().toPoint())
        if self.tool in ("text", "edittext") and isinstance(hit, TextItem):
            super().mousePressEvent(e)
            return
        page = self.page_at(sp)
        if page is None:
            return
        self.scene().clearSelection()
        if self.tool == "text":
            item = self.add_text(sp)
            item.start_editing()
            self.tool_finished.emit()
            return
        if self.tool == "edittext":
            self.edit_text_at(page, sp)
            self.tool_finished.emit()
            return
        self._drag = {"start": sp, "page": page, "item": None}

    def _select_press(self, e) -> bool:
        """Herramienta Seleccionar: sobre un elemento agregado, lo mueve (lo hace la escena: devuelve
        False); sobre una palabra, prepara la selección de texto; en una zona vacía, desplaza. Un clic
        sin arrastrar sobre un enlace lo sigue."""
        pos = e.position()
        if self._editable_at(pos.toPoint()) is not None:
            self.clear_selection()
            return False
        # Como hacía la escena con un clic en una zona vacía: termina la escritura y quita la selección
        # de elementos agregados.
        if self.scene().focusItem() is not None:
            self.scene().setFocusItem(None, Qt.FocusReason.MouseFocusReason)
        self.scene().clearSelection()
        sp = self.mapToScene(pos.toPoint())
        page = self.page_at(sp)
        hit = self._word_at(sp) if page is not None else None
        word = hit[1] if hit else None
        if page is not None and not self.words_for(page.index):
            self._no_text_message(page.index)
        # triple clic: el renglón completo
        t, p0 = self._last_dbl
        if word is not None and time.monotonic() - t < QApplication.doubleClickInterval() / 1000 \
                and (pos - p0).manhattanLength() < 8:
            self._select_line(page.index, word)
            self._last_dbl = (0.0, QPointF())
            self._press = {"mode": "done", "button": e.button()}
            return True
        # Mayús + clic: extiende la selección hasta aquí
        if word is not None and e.modifiers() & Qt.KeyboardModifier.ShiftModifier and self.sel_anchor is not None:
            self.sel_focus = (page.index, word)
            self._update_selection()
            self._press = {"mode": "text", "button": e.button(), "pos": pos, "link": None, "word": word}
            self.autoscroll_timer.start()
            return True
        self._press = {"mode": None, "button": e.button(), "pos": pos, "link": self.link_at(sp),
                       "page": page.index if page is not None else None, "word": word}
        if word is None:
            self.clear_selection()
            self._start_pan(pos)
        return True

    def mouseMoveEvent(self, e):
        self._last_mouse = e.position()
        if self._ext_captura:
            self._ext("move", e)
            return
        if self._press is None and not self._drag and self._ext("move", e):
            return
        d = self._press
        if d is not None:
            if d["mode"] is None and (e.position() - d["pos"]).manhattanLength() >= QApplication.startDragDistance():
                if d["word"] is not None:
                    d["mode"] = "text"
                    self._show_link_tip(None, e.position())
                    self.sel_anchor = self.sel_focus = (d["page"], d["word"])
                    self._set_cursor(Qt.CursorShape.IBeamCursor)
                    self.autoscroll_timer.start()
                else:
                    d["mode"] = "pan"
            if d["mode"] == "pan":
                self._do_pan(e.position())
            elif d["mode"] == "text":
                self._extend_selection(e.position())
            return
        if not self._drag:
            super().mouseMoveEvent(e)
            if self.tool == "select" and self.doc is not None and not e.buttons():
                self._hover(e.position())
            return
        sp = self.mapToScene(e.position().toPoint())
        d = self._drag
        rect = QRectF(d["start"], sp).normalized()
        if d["item"] is None:
            if (sp - d["start"]).manhattanLength() * self.zoom < 3:
                return
            if self.tool in ("rect", "ellipse", "redact"):
                d["item"] = ShapeItem(self.tool, rect, self.colors["draw"], self.line_width)
            elif self.tool in ("line", "arrow"):
                d["item"] = LineItem(self.tool, d["start"], sp, self.colors["draw"], self.line_width)
            elif self.tool == "ink":
                d["item"] = InkItem(d["start"], self.colors["draw"], self.line_width)
            elif self.tool == "highlight":
                c = QColor(self.colors["highlight"])
                c.setAlpha(110)
                d["item"] = QGraphicsRectItem(rect)
                d["item"].setBrush(c)
                d["item"].setPen(QPen(Qt.PenStyle.NoPen))
            self.scene().addItem(d["item"])
        item = d["item"]
        if isinstance(item, ShapeItem):
            item.set_rect(rect)
        elif isinstance(item, LineItem):
            item.set_end(sp)
        elif isinstance(item, InkItem):
            item.add_point(sp)
        elif isinstance(item, QGraphicsRectItem):
            item.setRect(rect)

    def mouseReleaseEvent(self, e):
        if self._ext_captura:
            if not e.buttons():
                self._ext_captura = False
            self._ext("release", e)
            return
        d = self._press
        if d is not None:
            if e.button() != d.get("button", Qt.MouseButton.LeftButton):
                return
            self._press = self._pan = None
            self.autoscroll_timer.stop()
            if d["mode"] is None:              # clic sin arrastrar
                if d.get("link") is not None:
                    self.follow_link(d["link"])
                elif d.get("word") is not None:
                    self.clear_selection()
            self._restore_cursor(e.position())
            return
        if not self._drag:
            super().mouseReleaseEvent(e)
            return
        d, self._drag = self._drag, None
        item = d["item"]
        if item is None:
            return
        if self.tool == "highlight":
            rect = item.rect()
            self.scene().removeItem(item)
            self.add_highlight(d["page"], rect)
        else:
            self.scene().removeItem(item)
            self._register([item])

    def _restore_cursor(self, pos: QPointF | None = None):
        if self.tool == "select":
            if pos is not None and self.doc is not None:
                self._hover(pos)
            else:
                self._set_cursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.viewport().setCursor(Qt.CursorShape.IBeamCursor if self.tool in ("text", "edittext")
                                      else Qt.CursorShape.CrossCursor)

    def mouseDoubleClickEvent(self, e):
        if self._ext("doble", e):
            self._ext_captura = True
            e.accept()
            return
        if self.tool == "select" and e.button() == Qt.MouseButton.LeftButton and self.doc is not None \
                and not self._space_down and self._editable_at(e.position().toPoint()) is None:
            sp = self.mapToScene(e.position().toPoint())
            page = self.page_at(sp)
            if page is not None:
                hit = self._word_at(sp)
                if hit is not None:
                    self.select_words(page.index, hit[1], hit[1])
                    self._last_dbl = (time.monotonic(), QPointF(e.position()))
                    self._press = {"mode": "done", "button": e.button()}
                elif not self.words_for(page.index):
                    self._no_text_message(page.index, force=True)
            e.accept()
            return
        super().mouseDoubleClickEvent(e)

    def contextMenuEvent(self, e):
        if self._ext("menu", e):
            e.accept()
            return
        if self.tool == "select" and self.has_selection() and self._over_selection(self.mapToScene(e.pos())):
            self.selection_menu().exec(e.globalPos())
            e.accept()
            return
        super().contextMenuEvent(e)

    def keyReleaseEvent(self, e):
        if e.key() == Qt.Key.Key_Space and not e.isAutoRepeat() and self._space_down:
            self._space_down = False
            if not self._space_panned and self._press is None:
                self.scroll_screen(-1 if self._space_shift else 1)   # barra espaciadora: una pantalla
            self._restore_cursor(self._last_mouse)
            e.accept()
            return
        super().keyReleaseEvent(e)

    def focusOutEvent(self, e):
        self._space_down = False
        super().focusOutEvent(e)

    def leaveEvent(self, e):
        self._show_link_tip(None, QPointF())   # al volver sobre el mismo enlace, la ayuda aparece otra vez
        super().leaveEvent(e)

    def keyPressEvent(self, e):
        editing = self.editing_text()
        if editing is None:
            if self._ext("tecla", e):
                return
            key, mods = e.key(), e.modifiers()
            ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
            alt = bool(mods & Qt.KeyboardModifier.AltModifier)
            if key == Qt.Key.Key_Space and not ctrl and not alt:
                if not e.isAutoRepeat() and not self._space_down:
                    self._space_down, self._space_panned = True, False
                    self._space_shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
                    self._show_link_tip(None, QPointF())
                    self._set_cursor(Qt.CursorShape.OpenHandCursor)
                return
            if ctrl and key == Qt.Key.Key_C and self.has_selection():
                self.copy_selection()
                return
            if ctrl and key == Qt.Key.Key_A and self.doc is not None:
                self.select_all_page()
                return
            if key in (Qt.Key.Key_PageDown, Qt.Key.Key_PageUp) and not alt:
                self.scroll_screen(1 if key == Qt.Key.Key_PageDown else -1)
                return
            if key in (Qt.Key.Key_Home, Qt.Key.Key_End) and not alt and self.pages:
                self.go_to_page(0 if key == Qt.Key.Key_Home else len(self.pages) - 1)
                return
            if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                self.delete_selected()
                return
            if key == Qt.Key.Key_Escape:
                self.clear_selection()
                self.scene().clearSelection()
                self.tool_finished.emit()
                return
            # atajos de una letra para las herramientas (solo si no se está escribiendo)
            plain = not (e.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier))
            letter = e.text().upper()
            if plain and letter in TOOL_KEYS and self.doc is not None:
                self.tool_requested.emit(TOOL_KEYS[letter])
                return
            if plain and letter == "I" and self.doc is not None:
                self.tool_requested.emit("image")
                return
        elif e.key() == Qt.Key.Key_Escape:
            editing.clearFocus()
            return
        super().keyPressEvent(e)

    # ---------------------------------------------------------------- soltar archivos
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            e.acceptProposedAction()
            self.files_dropped.emit(paths)


# --------------------------------------------------------------------------
# Página «Ver y editar»
# --------------------------------------------------------------------------

from PySide6.QtWidgets import (QAbstractItemView, QColorDialog, QListView, QListWidget,  # noqa: E402
                               QListWidgetItem, QPlainTextEdit, QSizePolicy, QStyle, QStyledItemDelegate)

ZOOM_PRESETS = [("Ajustar al ancho", "width"), ("Página completa", "page"), ("50 %", 0.5), ("75 %", 0.75),
                ("100 %", 1.0), ("125 %", 1.25), ("150 %", 1.5), ("200 %", 2.0), ("300 %", 3.0), ("400 %", 4.0)]
TOOL_LABELS = {"select": "Seleccionar", "text": "Texto", "edittext": "Editar texto", "highlight": "Resaltar",
               "ink": "Dibujar", "rect": "Rectángulo", "ellipse": "Elipse", "line": "Línea", "arrow": "Flecha",
               "redact": "Borrar zona"}
TOOL_ICONS = {"select": "cursor", "text": "type", "edittext": "edittext", "highlight": "highlighter", "ink": "pen",
              "rect": "square", "ellipse": "circle", "line": "line", "arrow": "arrow", "redact": "eraser"}
SHAPES = ["rect", "ellipse", "line", "arrow"]
PALETTES = {
    "text": ["#1E3A5F", "#111827", "#D63A3A", "#1F7A4D", "#2563EB"],
    "draw": ["#D63A3A", "#111827", "#2563EB", "#1F7A4D", "#D97706"],
    "highlight": ["#FFE14D", "#A7F3D0", "#FBCFE8", "#BFDBFE", "#FED7AA"],
}
KIND_LABEL = {"text": "Texto agregado", "replace": "Texto editado", "rect": "Rectángulo", "ellipse": "Elipse",
              "line": "Línea", "arrow": "Flecha", "ink": "Dibujo", "highlight": "Resaltado",
              "image": "Imagen o firma", "redact": "Zona a borrar"}
KIND_TIP = {
    "text": "Haz doble clic sobre el texto para escribir directamente en la página.",
    "replace": "Al guardar se borra la línea original y se escribe esta en su lugar.",
    "image": "Arrastra la esquina inferior derecha para cambiar el tamaño.",
    "redact": "Al guardar se elimina de verdad el texto y las imágenes que cubre esta zona.",
    "highlight": "Elige otro color o elimínalo si no lo necesitas.",
}
THUMB_W, THUMB_H = 104, 134
LEFT_W = 200                     # panel izquierdo (pestañas Páginas / Marcadores)
RIGHT_W = 280                    # panel derecho (Propiedades y las pestañas de las extensiones)
NOMBRES_COLOR = {"#1E3A5F": "Azul oscuro", "#111827": "Negro", "#D63A3A": "Rojo", "#1F7A4D": "Verde",
                 "#2563EB": "Azul", "#D97706": "Naranja", "#FFE14D": "Amarillo", "#A7F3D0": "Verde menta",
                 "#FBCFE8": "Rosa", "#BFDBFE": "Celeste", "#FED7AA": "Durazno"}
R_ASPECT = Qt.ItemDataRole.UserRole + 10
MAX_POSITIONS = 50               # documentos cuya última página leída se recuerda


class ThumbDelegate(QStyledItemDelegate):
    def __init__(self, strip: "ThumbStrip"):
        super().__init__()
        self.strip = strip

    def sizeHint(self, option, index):
        return QSize(140, THUMB_H + 34)

    def paint(self, p: QPainter, option, index):
        """Hoja blanca con su sombra; la página actual lleva el anillo de 2 px de la categoría y su número en
        la tinta de la categoría (como en la mesa «Visor»)."""
        t, ton = tema.tokens(), tema.tonos(CAT)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = option.rect
        aspect = index.data(R_ASPECT) or 0.75
        w, h = THUMB_W, THUMB_W / aspect
        if h > THUMB_H:
            h, w = THUMB_H, THUMB_H * aspect
        page = QRectF(r.center().x() - w / 2, r.top() + 8 + (THUMB_H - h) / 2, w, h)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(22, 26, 37, 18))          # sombra de la hoja (la de tema.sombra, muy suave)
        p.drawRoundedRect(page.translated(0, 2), 4, 4)
        p.setBrush(QColor("white"))                 # la hoja es blanca en los dos temas (rp-page)
        p.setPen(QPen(QColor(t["line-strong"] if hover and not selected else t["line"]), 1))
        p.drawRoundedRect(page, 3, 3)
        img = self.strip.images.get(index.row())
        if img is not None:
            p.drawImage(page.adjusted(1, 1, -1, -1), img)
        if selected:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(ton["c"]), 2))
            p.drawRoundedRect(page.adjusted(-2, -2, 2, 2), 5, 5)
        f = tema.fuente("caption", p.font())
        if selected:
            f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(QColor(ton["ink"] if selected else t["ink-3"]))
        p.drawText(QRectF(r.left(), r.top() + THUMB_H + 14, r.width(), 18), Qt.AlignmentFlag.AlignCenter,
                   str(index.row() + 1))
        p.restore()


class ThumbStrip(QListWidget):
    """Columna de miniaturas de páginas del visor (se renderizan a medida que se necesitan)."""

    def __init__(self, canvas: "PdfCanvas"):
        super().__init__()
        self.setObjectName("ThumbList")
        self.canvas = canvas
        self.images: dict[int, QImage] = {}
        self.pending: dict[int, None] = {}
        self.setViewMode(QListView.ViewMode.ListMode)
        self.setFlow(QListView.Flow.TopToBottom)
        self.setUniformItemSizes(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setItemDelegate(ThumbDelegate(self))
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.timer = QTimer(self)
        self.timer.setInterval(5)
        self.timer.timeout.connect(self._render_some)
        self.itemClicked.connect(lambda it: self.canvas.go_to_page(self.row(it)))
        self.verticalScrollBar().valueChanged.connect(lambda *_: self._kick())

    def reset_pages(self):
        self.clear()
        self.images.clear()
        self.pending.clear()
        for page in self.canvas.pages:
            it = QListWidgetItem()
            it.setData(R_ASPECT, page.w / max(page.h, 1))
            it.setToolTip(f"Página {page.index + 1}")
            self.addItem(it)
            self.pending[page.index] = None
        if self.count():
            self.setCurrentRow(0)
        self._kick()

    def select_page(self, i: int):
        if 0 <= i < self.count() and self.currentRow() != i:
            self.blockSignals(True)
            self.setCurrentRow(i)
            self.blockSignals(False)
            self.scrollToItem(self.item(i))

    def invalidar(self, paginas=None):
        """Vuelve a dibujar las miniaturas de esas páginas (None = todas)."""
        for i in (range(self.count()) if paginas is None else paginas):
            if 0 <= i < self.count():
                self.pending[i] = None
        self._kick()

    def _kick(self):
        if self.pending and not self.timer.isActive():
            self.timer.start()

    def _render_some(self):
        doc = self.canvas.doc
        if not self.pending or doc is None:
            self.timer.stop()
            return
        if ctx.busy or not PDF_LOCK.acquire(blocking=False):
            return
        try:
            first = max(0, self.indexAt(self.viewport().rect().topLeft()).row())
            order = [i for i in range(first, min(first + 8, self.count())) if i in self.pending]
            i = order[0] if order else next(iter(self.pending))
            self.pending.pop(i, None)
            try:
                self.images[i] = qimage_from_render(core.render_page(doc, i, THUMB_H * 2))
            except Exception:  # noqa: BLE001
                pass
        finally:
            PDF_LOCK.release()
        self.viewport().update()


def _seg_group(*buttons: QWidget) -> QFrame:
    """Grupo de herramientas (``rp-toolgroup``): pozo gris redondeado con los botones juntos."""
    box = QFrame()
    box.setObjectName("SegGroup")
    box.setFixedHeight(38)
    lay = QHBoxLayout(box)
    lay.setContentsMargins(3, 3, 3, 3)
    lay.setSpacing(2)
    for b in buttons:
        lay.addWidget(b)
    return box


def _seg_button(text: str, icon_name: str | None = None, tip: str = "", checkable: bool = True) -> QToolButton:
    """Botón de un grupo de herramientas: glifo de 17 px y texto de 13 px.  Encendido se ve en ``surface`` con
    la tinta de la categoría (regla ``QWidget[cat="ver"] QToolButton#SegButton:checked`` de style.py)."""
    b = QToolButton()
    b.setObjectName("SegButton")
    b.setText(text)
    if icon_name:
        b.setIcon(glifo_barra(icon_name))
        b.setIconSize(QSize(GLIFO, GLIFO))
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon if text
                             else Qt.ToolButtonStyle.ToolButtonIconOnly)
    b.setCheckable(checkable)
    b.setFixedHeight(32)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if tip:
        b.setToolTip(tip)
        b.setAccessibleName(tip)
    return b


def _seg_item(text: str, tip: str) -> QToolButton:
    """Opción de unos segmentos (``rp-seg``) del panel «Propiedades»."""
    b = QToolButton()
    b.setObjectName("SegItem")
    b.setText(text)
    b.setCheckable(True)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setToolTip(tip)
    b.setAccessibleName(tip)
    return b


def _segmentos(*buttons: QWidget, full: bool = False) -> QFrame:
    box = QFrame()
    box.setObjectName("Segmented")
    lay = QHBoxLayout(box)
    lay.setContentsMargins(3, 3, 3, 3)
    lay.setSpacing(2)
    for b in buttons:
        if full:
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lay.addWidget(b)
    return box


def _icon_button(icon_name: str, tip: str, cb=None) -> QToolButton:
    """Botón de ícono fantasma (``rp-btn ghost icon sm``): 32 × 32, glifo en ``ink-2``."""
    b = QToolButton()
    b.setObjectName("IconButton")
    b.setIcon(glifo(icon_name))
    b.setIconSize(QSize(GLIFO, GLIFO))
    b.setFixedSize(32, 32)
    b.setToolTip(tip)
    b.setAccessibleName(tip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if cb:
        b.clicked.connect(cb)
    return b


def _a_herramienta(b: QToolButton) -> None:
    """Un botón que una extensión agrega a la barra queda con el aspecto de los del visor: su glifo (la
    propiedad «glifo», si la trae) o su propio ícono vuelto a teñir."""
    nombre = b.property("glifo")
    b.setIcon(glifo_barra(nombre) if isinstance(nombre, str) and nombre else reteñir(b.icon()))
    b.setIconSize(QSize(GLIFO, GLIFO))
    b.setFixedHeight(32)


def _qss_visor() -> str:
    """Hoja propia del visor (se pone sobre la página): superficies, buscador, pie y avisos, con los tokens."""
    t = tema.tokens()
    aviso = tema.mezclar(t["warn-tint"], t["warn"], 0.22)
    info = tema.mezclar(t["info-tint"], t["info"], 0.18)
    return f"""
#VisorVacio, #VisorVacioCuerpo {{ background: {t['canvas']}; }}
#EditorHeader, #EditorToolbar {{ background: {t['surface']}; border: none; border-bottom: 1px solid {t['line']}; }}
#EditorFooter {{ background: {t['surface']}; border: none; border-top: 1px solid {t['line']}; }}
#VisorPaginas {{ background: {t['surface']}; border: none; border-right: 1px solid {t['line']}; }}
#VisorDerecho {{ background: {t['surface']}; border: none; border-left: 1px solid {t['line']}; }}
#PanelInterior {{ background: {t['surface']}; border: none; }}
QFrame#SegGroup[pestanas="true"] QToolButton#SegButton {{ padding: 5px 8px; font-size: 12px; }}
#EditorToolbar QToolButton#SegButton {{ padding: 6px 8px; }}
#VisorBuscador {{ background: {t['field']}; border: 1px solid {t['line']}; border-radius: 12px; }}
#VisorBuscador:hover {{ border-color: {t['line-strong']}; }}
#VisorBuscador[foco="true"] {{ background: {t['surface']}; border: 2px solid {t['focus']}; }}
QLineEdit#VisorBuscarCampo, QLineEdit#VisorBuscarCampo:focus {{
    background: transparent; border: none; padding: 0; min-height: 22px; font-size: 14px; }}
#VisorBuscador QToolButton#IconButton {{ padding: 2px; border-radius: 6px; }}
#SegActual {{ background: {t['surface']}; border: 1px solid {t['line']}; border-radius: 8px; }}
QLineEdit#PaginaActual {{ background: transparent; border: none; border-radius: 6px; padding: 0 2px;
    min-height: 0; font-size: 13px; font-weight: 600; }}
QLineEdit#PaginaActual:focus {{ background: {t['field']}; border: none; padding: 0 2px; }}
#PaginaTotal {{ color: {t['ink-2']}; font-size: 13px; }}
QToolButton#SegButton[actual="true"] {{
    background: {t['surface']}; border: 1px solid {t['line']}; color: {t['ink']}; font-weight: 600; }}
#VisorAviso {{ background: {t['warn-tint']}; border: none; border-bottom: 1px solid {aviso}; }}
#VisorAviso QLabel {{ color: {t['warn']}; font-size: 13px; }}
#AvisoExtension {{ background: {t['info-tint']}; border: none; border-bottom: 1px solid {info}; }}
#AvisoExtension QLabel {{ color: {t['info']}; font-size: 13px; }}
"""


class _Muestra(QAbstractButton):
    """Muestra de color redonda de 28 px (``sw`` de la mesa): elegida, la rodea un anillo de 2 px del color de
    la categoría (la heredada, como los componentes del sistema) con 2 px de aire.  ``otro=True`` es «Otro color»: círculo punteado con un +."""

    def __init__(self, otro: bool = False):
        super().__init__()
        self.otro = otro
        self.setCheckable(not otro)
        self.setFixedSize(36, 36)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        if otro:
            self.setToolTip("Otro color")
            self.setAccessibleName("Otro color")

    def set_color(self, hexc: str) -> None:
        self.setProperty("color", hexc)
        nombre = NOMBRES_COLOR.get(hexc.upper(), hexc)
        self.setToolTip(nombre)
        self.setAccessibleName(f"Color {nombre.lower()}")
        self.update()

    def sizeHint(self):  # noqa: N802
        return QSize(36, 36)

    def paintEvent(self, _e):  # noqa: N802
        t = tema.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QRectF(4, 4, 28, 28)
        if self.otro:
            pen = QPen(QColor(t["line-control"]), 1.2, Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.setBrush(QColor(t["surface-hover"] if self.underMouse() else t["surface"]))
            p.drawEllipse(c.adjusted(0.5, 0.5, -0.5, -0.5))
            g = art.glyph("add", 14, t["ink-2"], self.devicePixelRatioF())
            p.drawPixmap(QPointF(11, 11), g)
        else:
            if self.isChecked():
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(QColor(tema.tonos(cat_de(self) or CAT)["c"]), 2))
                p.drawEllipse(QRectF(1, 1, 34, 34))
            p.setPen(QPen(QColor(22, 26, 37, 30), 1))      # borde sutil para los colores claros
            p.setBrush(QColor(self.property("color") or t["ink"]))
            p.drawEllipse(c.adjusted(0.5, 0.5, -0.5, -0.5))
        if self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(t["focus"]), 2))
            p.drawEllipse(QRectF(1, 1, 34, 34))
        p.end()


class _CampoPagina(QLineEdit):
    """El número de página del pie («3» de «3 de 12»), editable: Enter lleva a esa página y ↑ ↓ cambian el
    número.  Tiene la parte de la interfaz de QSpinBox que usa el visor (``setRange``, ``setValue``,
    ``value``); es un campo de texto porque con la hoja de estilos el campo de un QSpinBox sin flechas queda
    de 1 px de ancho y el número no se ve."""

    def __init__(self):
        super().__init__()
        self._min, self._max = 1, 1
        self.setValidator(QIntValidator(1, 1, self))
        self.setText("1")
        self.editingFinished.connect(self._normalizar)

    def setRange(self, minimo: int, maximo: int) -> None:  # noqa: N802 — como QSpinBox
        self._min, self._max = int(minimo), max(int(minimo), int(maximo))
        self.validator().setRange(self._min, self._max)
        self.setValue(self.value())

    def setValue(self, valor: int) -> None:  # noqa: N802
        valor = max(self._min, min(self._max, int(valor)))
        if self.text() != str(valor):
            self.setText(str(valor))

    def value(self) -> int:
        try:
            return max(self._min, min(self._max, int(self.text())))
        except ValueError:
            return self._min

    def _normalizar(self) -> None:
        self.setValue(self.value())

    def keyPressEvent(self, e):  # noqa: N802
        if e.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self.setValue(self.value() + (1 if e.key() == Qt.Key.Key_Up else -1))
            self.selectAll()
            return
        super().keyPressEvent(e)


class _Buscador(QFrame):
    """Buscador del visor (``rp-input-wrap``): lupa, campo, «2 de 7» con sus flechas mientras hay resultados y
    la tecla «Ctrl F» mientras está vacío."""

    def __init__(self):
        super().__init__()
        self.setObjectName("VisorBuscador")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFixedHeight(38)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._compacto = False
        self._ancho = 300
        h = QHBoxLayout(self)
        h.setContentsMargins(12, 0, 6, 0)
        h.setSpacing(6)
        self.lupa = QLabel()
        self.lupa.setPixmap(art.glyph("search", 16, tema.color("ink-3")))
        h.addWidget(self.lupa)
        self.campo = QLineEdit()
        self.campo.setObjectName("VisorBuscarCampo")
        self.campo.setPlaceholderText("Buscar en el documento")
        self.campo.setClearButtonEnabled(True)
        self.campo.setAccessibleName("Buscar en el documento")
        h.addWidget(self.campo, 1)
        self.cuenta = label("", "Caption")
        h.addWidget(self.cuenta)
        self.b_ant = _icon_button("up_chevron", "Resultado anterior (Shift+Enter)")
        self.b_sig = _icon_button("down_chevron", "Resultado siguiente (Enter)")
        for b in (self.b_ant, self.b_sig):
            b.setFixedSize(26, 26)
            b.setIconSize(QSize(15, 15))
            h.addWidget(b)
        self.tecla = Kbd("Ctrl F")
        h.addWidget(self.tecla)
        h.addSpacing(4)
        self.campo.installEventFilter(self)
        self.campo.textChanged.connect(self._tecla)
        self.mostrar_flechas(False)
        self.set_compacto(False)

    def sizeHint(self):  # noqa: N802
        return QSize(300, 38)

    def eventFilter(self, obj, e):  # noqa: N802
        if obj is self.campo and e.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            self.setProperty("foco", "true" if e.type() == QEvent.Type.FocusIn else "false")
            repolish(self)
            self._tecla()
        return False

    def _tecla(self, *_):
        self.tecla.setVisible(not self._compacto and not self.campo.text() and not self.campo.hasFocus())

    def mostrar_flechas(self, on: bool) -> None:
        self.b_ant.setVisible(on)
        self.b_sig.setVisible(on)

    def set_ancho(self, ancho: int) -> None:
        """Ancho del buscador cuando el encabezado no está compacto (300 px, o 240 en una ventana angosta)."""
        self._ancho = ancho
        if not self._compacto:
            self.set_compacto(False)

    def set_compacto(self, on: bool) -> None:
        self._compacto = on
        self.setFixedWidth(160 if on else self._ancho)
        self.campo.setPlaceholderText("Buscar" if on else "Buscar en el documento" if self._ancho >= 300
                                      else "Buscar en el PDF")
        self._tecla()


class _BotonGuardar(guardado_ui.BotonGuardar):
    """«Guardar ▾» con el aspecto de ``rp-split``: el único botón principal del visor."""
    ANCHO_FLECHA = 26

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        t = tema.tokens()
        apagado = tema.mezclar("brand", "surface", 0.55)
        claro = tema.qcolor("on-brand")
        corte = f"rgba({claro.red()}, {claro.green()}, {claro.blue()}, 0.35)"
        self.principal.setStyleSheet("QPushButton#Primary { border-top-right-radius: 0px; "
                                     "border-bottom-right-radius: 0px; padding: 9px 8px 9px 12px; }")
        ic = art.glyph_icon("chevron_down", t["on-brand"], 14)
        for dpr in (1.0, 2.0, 3.0):
            ic.addPixmap(art.glyph("chevron_down", 14, t["on-brand"], dpr), QIcon.Mode.Disabled, QIcon.State.Off)
        self.flecha.setIcon(ic)
        self.flecha.setStyleSheet(
            f"QToolButton#GuardarFlecha {{ background: {t['brand']}; border: 1px solid {t['brand']}; "
            f"border-left: 1px solid {corte}; border-top-right-radius: 12px; border-bottom-right-radius: 12px; "
            "padding: 0px; }"
            f"QToolButton#GuardarFlecha:hover {{ background: {t['brand-hover']}; border-color: {t['brand-hover']}; "
            f"border-left: 1px solid {corte}; }}"
            f"QToolButton#GuardarFlecha:disabled {{ background: {apagado}; border-color: {apagado}; }}"
            "QToolButton#GuardarFlecha::menu-indicator { image: none; width: 0px; }")
        self.a_guardar.setIcon(glifo_menu("save"))
        self._alto()

    def _alto(self):
        self.principal.ensurePolished()
        self.flecha.setFixedSize(self.ANCHO_FLECHA, max(28, self.principal.sizeHint().height()))


class _Avisos(extensiones.AvisosVisor):
    """Franjas de aviso de las extensiones, con los colores del sistema (``info-tint``; ver ``_qss_visor``)."""

    def actualizar(self) -> None:
        super().actualizar()
        for k in range(self.lay.count()):
            fila = self.lay.itemAt(k).widget()
            if fila is None:
                continue
            fila.setStyleSheet("")              # la regla #AvisoExtension de la hoja del visor
            fila.layout().setContentsMargins(16, 8, 16, 8)
            for b in fila.findChildren(QPushButton):
                b.setProperty("tam", "sm")
                repolish(b)


class _Elidida(QLabel):
    """Etiqueta de una línea que, si no cabe, se acorta con «…» en vez de empujar a sus vecinos."""

    ALINEACION = Qt.AlignmentFlag.AlignLeft

    def minimumSizeHint(self) -> QSize:
        return QSize(48, super().minimumSizeHint().height())

    def paintEvent(self, e):
        p = QPainter(self)
        texto = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideMiddle, self.width())
        p.drawText(self.rect(), self.ALINEACION | Qt.AlignmentFlag.AlignVCenter, texto)
        p.end()


class _ElididaDerecha(_Elidida):
    ALINEACION = Qt.AlignmentFlag.AlignRight


class ViewerPage(QWidget):
    ANCHO_AMPLIO = 1100       # desde aquí el buscador mide 300 px y «Sin conexión · privado» lleva su texto
    key = "viewer"
    title = "Ver y editar PDF"
    subtitle = "Lee, busca, agrega texto, edita líneas, resalta, dibuja, inserta imágenes o firmas y borra zonas."
    icon_name = "viewer"

    def __init__(self):
        super().__init__()
        self.setObjectName("Page")
        self.cat = CAT
        self.setProperty("cat", CAT)              # color de la categoría para las piezas del sistema
        self.canvas = PdfCanvas()
        self.canvas.visor = self
        self.ext: dict = {}                       # estado de las extensiones (ver extensiones.py)
        self._paneles: dict[str, tuple] = {}      # pestañas del panel derecho: clave → (botón, widget)
        self._compacto: dict[str, bool] = {}      # barras que están en su forma compacta
        self._lleno: dict[str, int] = {}          # ancho que necesita cada barra en su forma completa
        self._reading = False
        self._full = False
        self._full_prev: dict | None = None
        self._reading_prev: dict | None = None
        self.outer = QStackedLayout(self)
        self.outer.addWidget(self._build_empty())
        self.outer.addWidget(self._build_editor())
        self.setStyleSheet(_qss_visor())
        self._last_term = None
        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(lambda: self._refresh_props())
        # «Continuar donde quedaste»: la página se recuerda poco después de dejar de pasar páginas.
        self._pos_timer = QTimer(self)
        self._pos_timer.setSingleShot(True)
        self._pos_timer.setInterval(1500)
        self._pos_timer.timeout.connect(self._remember_position)
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._remember_position)

        c = self.canvas
        c.zoom_changed.connect(lambda z: self.zoom_btn.setText(f"{z * 100:.0f} %"))
        c.page_changed.connect(self._page_changed)
        c.status.connect(self._show_status)
        c.notice.connect(self._show_notice)
        c.edits_changed.connect(self._update_state)
        c.tool_finished.connect(lambda: self.set_tool("select"))
        c.files_dropped.connect(lambda ps: self.load(ps[0]))
        c.selection_styles.connect(lambda _item: (self._refresh_props(), self._update_state()))
        c.tool_requested.connect(lambda t: self.insert_image() if t == "image" else self.set_tool(t))
        c.history_changed.connect(self._update_history)
        c.search_requested.connect(self._search_for)
        c.repintado.connect(self.thumbs.invalidar)

        for seq, fn in (("Ctrl+O", self.open_dialog), ("Ctrl+F", self._focus_search),
                        ("Ctrl+S", self._guardar_principal), ("Ctrl+Shift+S", lambda: self.save()),
                        ("Ctrl+Z", self._undo), ("Ctrl+Y", self._redo), ("Ctrl+Shift+Z", self._redo),
                        ("Ctrl++", lambda: self.canvas.zoom_by(1.2)),
                        ("Ctrl+=", lambda: self.canvas.zoom_by(1.2)), ("Ctrl+-", lambda: self.canvas.zoom_by(1 / 1.2)),
                        ("Ctrl+0", lambda: self.canvas.set_fit("width")),
                        ("F11", self.toggle_fullscreen), ("Ctrl+H", lambda: self.set_reading_mode(not self._reading)),
                        ("Ctrl+R", self.rotate_view), ("Ctrl+G", self._focus_page),
                        ("Alt+Left", self.go_back), ("Alt+Right", self.go_forward)):
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)
        # Esc sale de la pantalla completa (solo existe mientras dura, para no quitarle Esc al lienzo).
        self._esc_full = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self._esc_full.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._esc_full.activated.connect(self._escape_fullscreen)
        self._esc_full.setEnabled(False)
        sc = QShortcut(QKeySequence("Shift+Return"), self.search)
        sc.setContext(Qt.ShortcutContext.WidgetShortcut)
        sc.activated.connect(lambda: self._step_hit(-1))
        self._load_view_settings()
        self.set_tool("select")
        self._update_state()

    # ------------------------------------------------------------------ construcción
    def _build_empty(self) -> QWidget:
        """Estado vacío: el encabezado de toda herramienta y el pozo con la zona para soltar el PDF."""
        w = QWidget()
        w.setObjectName("VisorVacio")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        head = QWidget()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(32, 22, 32, 18)
        hl.setSpacing(16)
        hl.addWidget(GlyphTile("viewer", tema.TILE_LG, CAT), 0, Qt.AlignmentFlag.AlignVCenter)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addStretch(1)
        titles.addWidget(label(self.title, "ToolTitle"))
        titles.addWidget(label(self.subtitle, "ToolSubtitle", wrap=True))
        titles.addStretch(1)
        hl.addLayout(titles, 1)
        lay.addWidget(head)
        cuerpo = QWidget()
        cuerpo.setObjectName("VisorVacioCuerpo")
        cl = QVBoxLayout(cuerpo)
        cl.setContentsMargins(32, 0, 32, 28)
        pozo = QFrame()
        pozo.setObjectName("ToolMain")
        pl = QVBoxLayout(pozo)
        pl.setContentsMargins(0, 0, 0, 0)
        self.drop = DropArea("Abre o arrastra un PDF",
                             "Podrás leerlo, buscar, agregar texto, resaltar, dibujar, firmar y comentar.",
                             core.PDF_EXTS, tile_key="viewer", zone=True, button_text="Abrir un PDF…")
        self.drop.clicked.connect(self.open_dialog)
        self.drop.files_dropped.connect(lambda ps: self.load(ps[0]))
        self.drop.button.setToolTip("Abrir un PDF (Ctrl+O)")
        pl.addWidget(self.drop)
        cl.addWidget(pozo, 1)
        lay.addWidget(cuerpo, 1)
        return w

    def _build_editor(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # 1. Encabezado: el documento, sus acciones, la búsqueda y «Sin conexión · privado»
        header = QFrame()
        self.header = header
        header.setObjectName("EditorHeader")
        header.setFixedHeight(56)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(16, 0, 16, 0)
        hl.setSpacing(10)
        self.doc_thumb = PdfThumb(28, 36)
        hl.addWidget(self.doc_thumb)
        names = QVBoxLayout()
        names.setSpacing(0)
        fila_nombre = QHBoxLayout()
        fila_nombre.setSpacing(8)
        self.doc_name = _Elidida("")
        self.doc_name.setObjectName("DocName")
        fila_nombre.addWidget(self.doc_name)
        self.unsaved = Chip("Sin guardar", "warn", dot=True)
        self.unsaved.setToolTip("Hay cambios sin guardar")
        fila_nombre.addWidget(self.unsaved)
        fila_nombre.addStretch(1)
        self.doc_meta = label("", "Caption")
        names.addStretch(1)
        names.addLayout(fila_nombre)
        names.addWidget(self.doc_meta)
        names.addStretch(1)
        hl.addLayout(names, 1)
        self.b_open = _icon_button("folder_open", "Abrir otro PDF (Ctrl+O)", self.open_dialog)
        hl.addWidget(self.b_open)
        acciones = [a for m in extensiones.modulos() for a in getattr(m, "ACCIONES_VISOR", ())]
        self.ext_botones = extensiones.botones_visor(self)
        for b, a in zip(self.ext_botones, [a for a in acciones if not a.get("solo_menu")]):
            b.setIcon(glifo(a["icono"]))         # mismo aspecto que «Abrir»: glifo en ink-2, 32 × 32
            b.setIconSize(QSize(GLIFO, GLIFO))
            b.setFixedSize(32, 32)
            hl.addWidget(b)
        self.b_mas = extensiones.menu_visor(self)     # «Más acciones»: las mismas acciones, con su texto
        if self.b_mas is not None:
            self.b_mas.setIcon(glifo("mas"))
            self.b_mas.setIconSize(QSize(GLIFO, GLIFO))
            self.b_mas.setFixedSize(32, 32)
            for act, a in zip(self.b_mas.menu().actions(), acciones):
                act.setIcon(glifo_menu(a["icono"]))
            hl.addWidget(self.b_mas)
            self.b_mas.setVisible(extensiones.solo_menu_visor())
        hl.addSpacing(4)
        self.buscador = _Buscador()
        self.search = self.buscador.campo
        self.search.returnPressed.connect(self._search_enter)
        self.search.textChanged.connect(lambda t: (not t) and self._clear_search())
        self.b_hit_prev, self.b_hit_next = self.buscador.b_ant, self.buscador.b_sig
        self.b_hit_prev.clicked.connect(lambda: self._step_hit(-1))
        self.b_hit_next.clicked.connect(lambda: self._step_hit(1))
        self.hits_label = self.buscador.cuenta
        hl.addWidget(self.buscador)
        self.offline = Chip("Sin conexión · privado", "ok", glyph="shield")
        self.offline.setToolTip("RonyPDF funciona sin internet: tus archivos no salen de este equipo.")
        hl.addWidget(self.offline)
        v.addWidget(header)

        # 2. Barra de herramientas agrupada (ViewerChrome): grupos · deshacer y rehacer · «Guardar ▾»
        bar = QFrame()
        self.toolbar = bar
        bar.setObjectName("EditorToolbar")
        bar.setFixedHeight(56)
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(12, 0, 12, 0)
        bl.setSpacing(8)
        self.tool_buttons: dict[str, QToolButton] = {}
        for key in ("select", "text", "edittext", "highlight", "ink", "redact"):
            b = _seg_button(TOOL_LABELS[key], TOOL_ICONS[key], f"{TOOL_LABELS[key]} ({_key_for(key)})")
            b.clicked.connect(lambda _=False, k=key: self.set_tool(k))
            self.tool_buttons[key] = b
        self.shape_btn = _seg_button("Formas", "square", "Formas: rectángulo, elipse, línea o flecha")
        self.shape_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.shape_btn)
        for key in SHAPES:
            menu.addAction(glifo_menu(TOOL_ICONS[key]), f"{TOOL_LABELS[key]}\t{_key_for(key)}",
                           lambda k=key: self.set_tool(k))
        self.shape_btn.setMenu(menu)
        for key in SHAPES:
            self.tool_buttons[key] = self.shape_btn
        self.b_image = _seg_button("Firma", "signature", "Insertar una firma o una imagen (I)", checkable=False)
        self.b_image.clicked.connect(self.insert_image)
        bl.addWidget(_seg_group(self.tool_buttons["select"], self.tool_buttons["text"],
                                self.tool_buttons["edittext"]))
        bl.addWidget(_seg_group(self.tool_buttons["highlight"], self.tool_buttons["ink"], self.shape_btn))
        bl.addWidget(_seg_group(self.b_image, self.tool_buttons["redact"]))
        for grupo in extensiones.grupos_visor(self):  # herramientas de las extensiones («Comentar», «Sello»)
            for b in grupo:
                _a_herramienta(b)
            bl.addWidget(_seg_group(*grupo))
        for k in range(bl.count()):
            bl.setAlignment(bl.itemAt(k).widget(), Qt.AlignmentFlag.AlignVCenter)
        bl.addStretch(1)
        self.b_undo = _icon_button("undo", "Deshacer (Ctrl+Z)", self._undo)
        self.b_redo = _icon_button("redo", "Rehacer (Ctrl+Y)", self._redo)
        self.b_delete = _icon_button("delete", "Eliminar lo seleccionado (Supr)", lambda: self.canvas.delete_selected())
        for b in (self.b_undo, self.b_redo, self.b_delete):
            bl.addWidget(b)
        bl.addSpacing(4)
        # «Guardar» (en el mismo archivo, con copia de respaldo) y, en la flecha, «Guardar como…»
        self.save_group = _BotonGuardar(self._guardar_principal, self.guardar, lambda: self.save())
        self.b_save = self.save_group.principal
        bl.addWidget(self.save_group)
        v.addWidget(bar)

        # Aviso de documento escaneado
        self.banner = QFrame()
        self.banner.setObjectName("VisorAviso")
        bnl = QHBoxLayout(self.banner)
        bnl.setContentsMargins(16, 8, 16, 8)
        bnl.setSpacing(10)
        ic = QLabel()
        ic.setPixmap(art.glyph("info", 16, tema.color("warn")))
        bnl.addWidget(ic)
        bnl.addWidget(label("Este PDF parece escaneado: su texto no se puede buscar, copiar ni editar todavía.",
                            None, wrap=True), 1)
        b_ocr = QPushButton("Reconocer texto (OCR)")
        b_ocr.setObjectName("Secondary")
        b_ocr.setProperty("tam", "sm")
        b_ocr.setCursor(Qt.CursorShape.PointingHandCursor)
        b_ocr.clicked.connect(self._go_ocr)
        bnl.addWidget(b_ocr)
        self.banner.hide()
        v.addWidget(self.banner)
        self.ext_avisos = _Avisos(self)
        v.addWidget(self.ext_avisos)

        # Cuerpo: páginas o marcadores | documento | propiedades
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        left = QFrame()
        self.left_panel = left
        left.setObjectName("VisorPaginas")
        left.setFixedWidth(LEFT_W)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(10, 12, 10, 12)
        ll.setSpacing(10)
        self.tab_pages = _seg_button("Páginas", None, "Miniaturas de las páginas")
        self.tab_marks = _seg_button("Marcadores", None, "Marcadores (índice) del documento")
        tabs = QButtonGroup(self)
        tabs.setExclusive(True)
        for k, b in enumerate((self.tab_pages, self.tab_marks)):
            b.setSizePolicy(QSizePolicy.Policy.Expanding if k else QSizePolicy.Policy.Preferred,
                            QSizePolicy.Policy.Fixed)
            tabs.addButton(b, k)
            b.clicked.connect(lambda _=False, k_=k: self.show_left_tab(k_))
        self.tab_pages.setChecked(True)
        tab_row = _seg_group(self.tab_pages, self.tab_marks)
        tab_row.setProperty("pestanas", "true")
        ll.addWidget(tab_row)
        self.left_stack = QStackedWidget()
        pages_box = QWidget()
        pl = QVBoxLayout(pages_box)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(6)
        self.pages_count = label("", "Caption")
        self.pages_count.setContentsMargins(4, 0, 0, 0)
        pl.addWidget(self.pages_count)
        self.thumbs = ThumbStrip(self.canvas)
        pl.addWidget(self.thumbs, 1)
        b_org = QPushButton("Organizar páginas")
        self.b_org = b_org
        b_org.setObjectName("Secondary")
        b_org.setProperty("tam", "sm")
        b_org.setIcon(glifo("organize", "ink-2", hover=None, size=16))
        b_org.setCursor(Qt.CursorShape.PointingHandCursor)
        b_org.setToolTip("Abrir este PDF en «Organizar páginas» para mover, girar o quitar páginas")
        b_org.clicked.connect(self._go_organize)
        pl.addWidget(b_org)
        self.left_stack.addWidget(pages_box)
        self.marcadores = visor_paneles.PanelMarcadores()
        self.marcadores.ir_a.connect(lambda p, y: self.canvas.go_to_dest(p, None if y is None else (0.0, y)))
        self.left_stack.addWidget(self.marcadores)
        ll.addWidget(self.left_stack, 1)
        body.addWidget(left)
        body.addWidget(self.canvas, 1)
        self.props_panel = self._build_right(self._build_props())
        body.addWidget(self.props_panel)
        v.addLayout(body, 1)

        # Pie: página, zoom, vista y estado
        footer = QFrame()
        self.footer = footer
        footer.setObjectName("EditorFooter")
        footer.setFixedHeight(48)
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(12, 0, 16, 0)
        fl.setSpacing(8)
        self.b_prev = _seg_button("", "prev", "Página anterior", checkable=False)
        self.b_prev.clicked.connect(lambda: self.canvas.go_to_page(self.canvas.current_page - 1))
        caja = QFrame()
        caja.setObjectName("SegActual")
        caja.setFixedHeight(32)
        cl = QHBoxLayout(caja)
        cl.setContentsMargins(6, 0, 10, 0)
        cl.setSpacing(4)
        self.page_spin = _CampoPagina()
        self.page_spin.setObjectName("PaginaActual")
        self.page_spin.setRange(1, 1)
        self.page_spin.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.page_spin.setFixedSize(36, 26)
        self.page_spin.setToolTip("Página actual: escribe un número y pulsa Enter (Ctrl+G)")
        self.page_spin.setAccessibleName("Página actual")
        self.page_spin.editingFinished.connect(lambda: self.canvas.go_to_page(self.page_spin.value() - 1))
        self.page_total = QLabel("de 0")
        self.page_total.setObjectName("PaginaTotal")
        cl.addWidget(self.page_spin)
        cl.addWidget(self.page_total)
        self.b_next = _seg_button("", "next", "Página siguiente", checkable=False)
        self.b_next.clicked.connect(lambda: self.canvas.go_to_page(self.canvas.current_page + 1))
        fl.addWidget(_seg_group(self.b_prev, caja, self.b_next))
        self.b_zoom_out = _seg_button("", "minus", "Alejar (Ctrl+−)", checkable=False)
        self.b_zoom_out.clicked.connect(lambda: self.canvas.zoom_by(1 / 1.2))
        self.zoom_btn = QToolButton()
        self.zoom_btn.setObjectName("SegButton")
        self.zoom_btn.setProperty("actual", "true")
        self.zoom_btn.setText("100 %")
        self.zoom_btn.setFixedHeight(32)
        self.zoom_btn.setMinimumWidth(64)
        self.zoom_btn.setToolTip("Zoom: elige un tamaño")
        self.zoom_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.zoom_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        zmenu = QMenu(self.zoom_btn)
        for text, val in ZOOM_PRESETS:
            zmenu.addAction(text, lambda v_=val: self.canvas.set_fit(v_) if isinstance(v_, str) else self.canvas.set_zoom(v_))
        self.zoom_btn.setMenu(zmenu)
        self.b_zoom_in = _seg_button("", "add", "Acercar (Ctrl++)", checkable=False)
        self.b_zoom_in.clicked.connect(lambda: self.canvas.zoom_by(1.2))
        fl.addWidget(_seg_group(self.b_zoom_out, self.zoom_btn, self.b_zoom_in))
        b_fit = QPushButton("Ajustar al ancho")
        self.b_fit = b_fit
        b_fit.setObjectName("Ghost")
        b_fit.setCursor(Qt.CursorShape.PointingHandCursor)
        b_fit.setToolTip("Ajustar al ancho (Ctrl+0)")
        b_fit.clicked.connect(lambda: self.canvas.set_fit("width"))
        fl.addWidget(b_fit)
        self.view_btn = QToolButton()
        self.view_btn.setObjectName("Ghost")
        self.view_btn.setText("Vista")
        self.view_btn.setIcon(glifo("eye", size=16))
        self.view_btn.setIconSize(QSize(16, 16))
        self.view_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.view_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.view_btn.setToolTip("Una o dos páginas, modo lectura, pantalla completa, modo noche y girar la vista")
        self.view_btn.setAccessibleName("Vista")
        self.view_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.view_menu = visor_paneles.MenuVista(self.view_btn)
        vm = self.view_menu
        vm.act_una.triggered.connect(lambda: self.set_view_mode("una"))
        vm.act_dos.triggered.connect(lambda: self.set_view_mode("dos"))
        vm.act_portada.toggled.connect(lambda on: self.set_view_mode(self.canvas.view_mode, on))
        vm.act_lectura.triggered.connect(lambda on: self.set_reading_mode(on))
        vm.act_completa.triggered.connect(lambda on: self.set_fullscreen(on))
        vm.act_noche.toggled.connect(self.set_night)
        vm.act_girar.triggered.connect(self.rotate_view)
        self.view_btn.setMenu(vm)
        fl.addWidget(self.view_btn)
        self.b_back = QPushButton("Volver")
        self.b_back.setObjectName("Ghost")
        self.b_back.setIcon(glifo("prev", size=15, hover=None))
        self.b_back.setToolTip("Volver a donde estabas antes de seguir el enlace (Alt+Izquierda)")
        self.b_back.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_back.clicked.connect(self.go_back)
        self.b_back.hide()
        fl.addWidget(self.b_back)
        self.b_exit_full = QPushButton("Salir de pantalla completa")
        self.b_exit_full.setObjectName("Secondary")
        self.b_exit_full.setProperty("tam", "sm")
        self.b_exit_full.setToolTip("También con Esc o F11")
        self.b_exit_full.clicked.connect(lambda: self.set_fullscreen(False))
        self.b_exit_full.hide()
        fl.addWidget(self.b_exit_full)
        self.footer_note = _ElididaDerecha("")
        self.footer_note.setObjectName("Caption")
        fl.addWidget(self.footer_note, 1)
        self._nota_pie()
        v.addWidget(footer)
        return w

    def _nota_pie(self) -> None:
        """La frase del pie dice qué pasa al guardar (con copia de respaldo o sin ella)."""
        try:
            respaldo = guardado_ui.respaldo_activo()
        except Exception:  # noqa: BLE001 — sin ajustes todavía: se asume el valor por defecto
            respaldo = True
        self.footer_note.setText("Al guardar se deja una copia de respaldo del original." if respaldo
                                 else "«Guardar» reemplaza el archivo; «Guardar como…» crea una copia.")
        self.footer_note.setToolTip("«Guardar» reemplaza el archivo y antes deja una copia de respaldo; "
                                    "«Guardar como…» crea una copia con otro nombre.")

    def _build_right(self, props: QFrame) -> QFrame:
        """Panel derecho: «Propiedades» y, en pestañas, los paneles de las extensiones (p. ej. «Comentarios»)."""
        self._paneles = {"props": (None, props)}
        paneles = extensiones.paneles_visor(self)
        if not paneles:
            props.setFixedWidth(RIGHT_W)
            return props
        props.setObjectName("PanelInterior")
        outer = QFrame()
        outer.setObjectName("VisorDerecho")
        outer.setFixedWidth(RIGHT_W)
        lay = QVBoxLayout(outer)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.right_stack = QStackedWidget()
        self.right_stack.addWidget(props)
        grupo = QButtonGroup(self)
        grupo.setExclusive(True)
        b_props = _seg_button("Propiedades", None, "Propiedades de lo que seleccionas o de la herramienta activa")
        botones = [b_props]
        self._paneles["props"] = (b_props, props)
        for d in paneles:
            b = _seg_button(d["titulo"], None, d.get("ayuda", d["titulo"]))
            self._paneles[d["clave"]] = (b, d["widget"])
            self.right_stack.addWidget(d["widget"])
            botones.append(b)
        for clave, (b, _w) in self._paneles.items():
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            grupo.addButton(b)
            b.clicked.connect(lambda _=False, k=clave: self.mostrar_panel(k))
        b_props.setChecked(True)
        fila = _seg_group(*botones)
        fila.setProperty("pestanas", "true")
        caja = QWidget()
        cl = QVBoxLayout(caja)
        cl.setContentsMargins(12, 12, 12, 0)
        cl.addWidget(fila)
        lay.addWidget(caja)
        lay.addWidget(self.right_stack, 1)
        return outer

    def mostrar_panel(self, clave: str = "props") -> None:
        """Muestra una pestaña del panel derecho: «props» o la clave del panel de una extensión."""
        boton, widget = self._paneles.get(clave, (None, None))
        if boton is None:
            return
        boton.setChecked(True)
        self.right_stack.setCurrentWidget(widget)

    def panel_actual(self) -> str:
        """Clave de la pestaña del panel derecho que está a la vista."""
        actual = self.right_stack.currentWidget() if len(self._paneles) > 1 else None
        return next((k for k, (_b, w) in self._paneles.items() if w is actual), "props")

    def titulo_panel(self, clave: str, texto: str) -> None:
        """Cambia el texto de una pestaña del panel derecho (por ejemplo, «Comentarios (3)»)."""
        boton = self._paneles.get(clave, (None, None))[0]
        if boton is not None:
            boton.setText(texto)

    # ------------------------------------------------------------------ barras que caben en poco ancho
    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._ajustar_barras()

    def _ajustar_barras(self):
        """Con poco ancho las barras se compactan en vez de recortar sus textos: la de herramientas deja
        solo los íconos, el encabezado encoge la búsqueda y pasa las acciones de las extensiones al menú
        «Más acciones», y el pie quita lo accesorio."""
        if not hasattr(self, "footer"):
            return
        # Primero, que los grupos y el botón «Guardar» midan lo que miden ahora (si un texto acaba de cambiar,
        # su ajuste está pendiente y el ancho mínimo de la barra saldría viejo).
        QApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)
        ancho = self.width()
        amplio = ancho >= self.ANCHO_AMPLIO
        self.buscador.set_ancho(300 if amplio else 240)
        self.offline.setText("Sin conexión · privado" if amplio and not self._compacto.get("encabezado") else "")
        self._ajustar_herramientas(ancho)
        for clave, barra, aplicar in (("encabezado", self.header, self._compactar_encabezado),
                                      ("pie", self.footer, self._compactar_pie)):
            if not self._compacto.get(clave, False):
                self._lleno[clave] = barra.minimumSizeHint().width()
                if ancho < self._lleno[clave]:
                    aplicar(True)
                    self._compacto[clave] = True
            elif ancho >= self._lleno[clave] + 8:
                aplicar(False)
                self._compacto[clave] = False

    def _botones_con_texto(self) -> list[QToolButton]:
        """Botones de la barra de herramientas que tienen ícono y texto, en el orden en que lo conservan: los
        que pierden el texto primero van al final.  Primero lo pierden los del visor, de derecha a izquierda,
        y de últimos los de las extensiones («Comentar», «Sello»), que son nuevos y se reconocen menos por el
        ícono solo."""
        propios, ajenos = [], []
        mios = set(self.tool_buttons.values()) | {self.b_image}
        lay = self.toolbar.layout()
        for k in range(lay.count()):
            grupo = lay.itemAt(k).widget()
            if grupo is None or grupo.objectName() != "SegGroup":
                continue
            for j in range(grupo.layout().count()):
                b = grupo.layout().itemAt(j).widget()
                if isinstance(b, QToolButton) and b.text() and not b.icon().isNull():
                    (propios if b in mios else ajenos).append(b)
        return ajenos + propios

    def _ajustar_herramientas(self, ancho: int):
        """Si la barra de herramientas no cabe, sus botones pierden el texto uno por uno, en el orden de
        ``_botones_con_texto`` (queda el ícono, con su ayuda); cuando vuelve a haber espacio, lo recuperan."""
        botones = self._botones_con_texto()
        solo, texto = Qt.ToolButtonStyle.ToolButtonIconOnly, Qt.ToolButtonStyle.ToolButtonTextBesideIcon

        def poner(b: QToolButton, estilo) -> None:
            if b.toolButtonStyle() != estilo:
                b.setToolButtonStyle(estilo)
                if estilo == solo:                    # solo el glifo: un botón cuadrado, sin relleno de más
                    b.setFixedWidth(34)
                else:
                    b.setMinimumWidth(0)
                    b.setMaximumWidth(16777215)
                b.parentWidget().updateGeometry()     # para que la barra recalcule ya su ancho mínimo

        k = sum(1 for b in botones if b.toolButtonStyle() == solo)      # los k de la derecha van sin texto
        for i, b in enumerate(botones):
            poner(b, texto if i < len(botones) - k else solo)
        while k < len(botones) and self.toolbar.minimumSizeHint().width() > ancho:
            k += 1
            poner(botones[-k], solo)
        while k > 0:
            poner(botones[-k], texto)
            if self.toolbar.minimumSizeHint().width() > ancho:
                poner(botones[-k], solo)
                break
            k -= 1

    def _compactar_encabezado(self, on: bool):
        self.buscador.set_compacto(on)
        self.unsaved.setText("" if on else "Sin guardar")
        self.offline.setText("" if on or self.width() < self.ANCHO_AMPLIO else "Sin conexión · privado")
        for b in self.ext_botones:
            b.setVisible(not on)
        if self.b_mas is not None:
            self.b_mas.setVisible(on or extensiones.solo_menu_visor())

    def _compactar_pie(self, on: bool):
        self.b_fit.setVisible(not on)
        self.footer_note.setVisible(not on)

    def _build_props(self) -> QFrame:
        """Panel «Propiedades» (mesa «Visor»): título y chip de lo seleccionado, campos con la etiqueta encima,
        muestras de color, «Eliminar elemento» y una nota con el consejo de la herramienta."""
        panel = QFrame()
        panel.setObjectName("VisorDerecho")
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()                    # con la ventana baja, el panel se desplaza en vez de apretarse
        scroll.setObjectName("PanelScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.viewport().setAutoFillBackground(False)
        interior = QWidget()
        interior.setObjectName("PanelInterior")
        scroll.setWidget(interior)
        pl.addWidget(scroll)
        lay = QVBoxLayout(interior)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(16)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.props_title = label("Propiedades", "PanelTitle")
        head.addWidget(self.props_title)
        head.addStretch(1)
        self.kind_chip = Chip("", "cat")
        head.addWidget(self.kind_chip)
        lay.addLayout(head)

        def field(title: str, widget: QWidget) -> QWidget:
            box = QWidget()
            bl = QVBoxLayout(box)
            bl.setContentsMargins(0, 0, 0, 0)
            bl.setSpacing(6)
            bl.addWidget(label(title, "Label"))
            bl.addWidget(widget)
            return box

        self.content_edit = QPlainTextEdit()
        self.content_edit.setFixedHeight(72)
        self.content_edit.textChanged.connect(self._content_changed)
        self.f_content = field("Contenido", self.content_edit)
        lay.addWidget(self.f_content)

        self.font_buttons: dict[str, QToolButton] = {}
        fonts = []
        for fam, text, letra in ((core.SANS, "Sans", ""), (core.SERIF, "Serif", "Georgia"),
                                 (core.MONO, "Mono", "Consolas")):
            b = _seg_item(text, f"Letra {text}")
            if letra:
                b.setStyleSheet(f"font-family: {letra};")
            b.clicked.connect(lambda _=False, f=fam: self._set_family(f))
            self.font_buttons[fam] = b
            fonts.append(b)
        grupo_letras = QButtonGroup(self)
        for b in fonts:
            grupo_letras.addButton(b)
        self.f_font = field("Letra", _segmentos(*fonts, full=True))
        lay.addWidget(self.f_font)

        row = QHBoxLayout()
        row.setSpacing(12)
        self.size_spin = QSpinBox()
        self.size_spin.setRange(4, 144)
        self.size_spin.setValue(12)
        self.size_spin.setSuffix(" pt")
        self.size_spin.setMinimumWidth(96)
        self.size_spin.valueChanged.connect(self._text_style_changed)
        self.f_size = field("Tamaño", self.size_spin)
        row.addWidget(self.f_size, 1)
        self.b_bold = _seg_item("N", "Negrita")
        self.b_bold.setStyleSheet("font-weight: 700;")
        self.b_bold.toggled.connect(self._text_style_changed)
        self.b_italic = _seg_item("C", "Cursiva")
        self.b_italic.setStyleSheet("font-style: italic; font-family: Georgia;")
        self.b_italic.toggled.connect(self._text_style_changed)
        self.f_style = field("Estilo", _segmentos(self.b_bold, self.b_italic))
        row.addWidget(self.f_style)
        self.f_textrow = QWidget()
        self.f_textrow.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.f_textrow)

        self.width_spin = QSpinBox()
        self.width_spin.setRange(1, 30)
        self.width_spin.setValue(2)
        self.width_spin.setSuffix(" pt")
        self.width_spin.valueChanged.connect(self._width_changed)
        self.f_width = field("Grosor", self.width_spin)
        lay.addWidget(self.f_width)

        sw = QWidget()
        swl = QHBoxLayout(sw)
        swl.setContentsMargins(0, 0, 0, 0)
        swl.setSpacing(2)
        self.swatches: list[_Muestra] = []
        for _ in range(5):
            b = _Muestra()
            b.clicked.connect(lambda _=False, btn=b: self._pick_color(QColor(btn.property("color"))))
            self.swatches.append(b)
            swl.addWidget(b)
        custom = _Muestra(otro=True)
        custom.clicked.connect(self._custom_color)
        swl.addWidget(custom)
        swl.addStretch(1)
        self.f_color = field("Color", sw)
        lay.addWidget(self.f_color)

        self.b_remove = QPushButton("Eliminar elemento")
        self.b_remove.setObjectName("Danger")
        self.b_remove.setIcon(glifo("trash", "danger", size=16, hover=None))
        self.b_remove.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_remove.clicked.connect(lambda: self.canvas.delete_selected())
        lay.addWidget(self.b_remove)

        self.tip = Note("", "info")
        self.tip_text = self.tip.text_lb
        lay.addWidget(self.tip)
        lay.addStretch(1)
        return panel

    # ------------------------------------------------------------------ propiedades
    def _selected_item(self):
        sel = [i for i in self.canvas.scene().selectedItems() if isinstance(i, _Editable)]
        return sel[0] if len(sel) == 1 else None

    def _context(self):
        """(elemento seleccionado o None, categoría de color, es_texto, es_trazo, es_resaltado)."""
        item = self._selected_item()
        if item is not None:
            kind = item.kind
            is_text = isinstance(item, TextItem)
            is_draw = kind in ("rect", "ellipse", "line", "arrow", "ink")
            is_hl = kind == "highlight"
        else:
            tool = self.canvas.tool
            is_text = tool in ("text", "edittext")
            is_draw = tool in DRAW_TOOLS
            is_hl = tool == "highlight"
        key = "text" if is_text else "highlight" if is_hl else "draw"
        return item, key, is_text, is_draw, is_hl

    def _refresh_props(self):
        item, key, is_text, is_draw, is_hl = self._context()
        if item is not None:
            self.kind_chip.setText(KIND_LABEL.get(item.kind, "Elemento"))
        else:
            self.kind_chip.setText(TOOL_LABELS.get(self.canvas.tool, ""))
            self.kind_chip.setToolTip("Herramienta activa")
        self.f_content.setVisible(isinstance(item, TextItem))
        self.f_font.setVisible(is_text)
        self.f_textrow.setVisible(is_text)
        self.f_width.setVisible(is_draw)
        self.f_color.setVisible(is_text or is_draw or is_hl)
        self.b_remove.setVisible(item is not None)
        # valores
        widgets = [self.content_edit, self.size_spin, self.b_bold, self.b_italic, self.width_spin]
        for w in widgets:
            w.blockSignals(True)
        if isinstance(item, TextItem):
            if self.content_edit.toPlainText() != item.toPlainText():
                self.content_edit.setPlainText(item.toPlainText())
            fam, size, bold, italic = item.family, item.size, item.bold, item.italic
            color = item.defaultTextColor()
        else:
            c = self.canvas
            fam, size, bold, italic = c.family, c.font_size, c.bold, c.italic
            color = item.color if item is not None and hasattr(item, "color") else c.colors[key]
        self.size_spin.setValue(int(size))
        self.b_bold.setChecked(bool(bold))
        self.b_italic.setChecked(bool(italic))
        for f, b in self.font_buttons.items():
            b.setChecked(f == fam)
        width = getattr(item, "width", None) if item is not None else self.canvas.line_width
        self.width_spin.setValue(int(round(width or self.canvas.line_width)))
        for w in widgets:
            w.blockSignals(False)
        palette = PALETTES[key]
        current = QColor(color).name().upper()
        for hexc, b in zip(palette, self.swatches):
            b.set_color(hexc)
            b.setChecked(hexc.upper() == current)
        # consejo
        if item is not None:
            tip = KIND_TIP.get(item.kind, "Arrástralo para moverlo; Supr lo elimina.")
        elif self.canvas.view_rot:
            self._set_tip(ROTATED_MSG, warn=True)
            return
        else:
            tip = next((t[3] for t in TOOLS if t[0] == self.canvas.tool), "")
        self._set_tip(tip, warn=False)

    def _set_tip(self, text: str, warn: bool):
        self.tip.set_tone("warn" if warn else "info")
        self.tip.setText(text)
        self.tip.setVisible(bool(text))

    def _show_status(self, msg: str):
        hint = next((t[3] for t in TOOLS if t[0] == self.canvas.tool), "")
        if msg and msg != hint:
            self._set_tip(msg, warn=True)
            self._status_timer.start(5000)
        else:
            self._refresh_props()

    def _show_notice(self, msg: str):
        """Aviso informativo (azul) en el panel de consejos, que se va solo."""
        self._set_tip(msg, warn=False)
        self._status_timer.start(5000)

    def _content_changed(self):
        item = self._selected_item()
        if isinstance(item, TextItem):
            text = self.content_edit.toPlainText()
            if text != item.toPlainText():
                item.setPlainText(text)
                self._update_state()

    def _set_family(self, fam: str):
        for f, b in self.font_buttons.items():
            b.setChecked(f == fam)
        self.canvas.family = fam
        self.canvas.apply_style_to_selection(family=fam)

    def _text_style_changed(self, *_):
        c = self.canvas
        c.font_size = self.size_spin.value()
        c.bold = self.b_bold.isChecked()
        c.italic = self.b_italic.isChecked()
        c.apply_style_to_selection(size=c.font_size, bold=c.bold, italic=c.italic)

    def _width_changed(self, v: int):
        self.canvas.line_width = float(v)
        self.canvas.apply_style_to_selection(width=float(v))

    def _pick_color(self, color: QColor):
        item, key, *_ = self._context()
        self.canvas.colors[key] = QColor(color)
        if item is not None:
            self.canvas.apply_style_to_selection(color=QColor(color))
        self._refresh_props()

    def _custom_color(self):
        _item, key, *_ = self._context()
        c = QColorDialog.getColor(self.canvas.colors[key], self, "Elige un color")
        if c.isValid():
            self._pick_color(c)

    # ------------------------------------------------------------------ herramientas
    def set_tool(self, tool: str):
        if self.canvas.view_rot and tool != "select":
            tool = "select"
            self.canvas.status.emit(ROTATED_MSG)
        self.canvas.set_tool(tool)
        for key, b in self.tool_buttons.items():
            if b is not self.shape_btn:
                b.setChecked(key == tool)
        in_shape = tool in SHAPES
        self.shape_btn.setChecked(in_shape)
        if in_shape:
            self.shape_btn.setText(TOOL_LABELS[tool])
            self.shape_btn.setIcon(glifo_barra(TOOL_ICONS[tool]))
        self._refresh_props()

    def _undo(self):
        editing = self.canvas.editing_text()
        if editing is not None:
            editing.document().undo()   # mientras se escribe, deshace la escritura
        else:
            self.canvas.undo()

    def _redo(self):
        editing = self.canvas.editing_text()
        if editing is not None:
            editing.document().redo()   # mientras se escribe, rehace la escritura
        else:
            self.canvas.redo()

    def _focus_search(self):
        self.search.setFocus()
        self.search.selectAll()

    def _focus_page(self):
        self.page_spin.setFocus()
        self.page_spin.selectAll()

    def _page_changed(self, i: int):
        self.page_spin.blockSignals(True)
        self.page_spin.setValue(i + 1)
        self.page_spin.blockSignals(False)
        self.b_prev.setEnabled(i > 0)
        self.b_next.setEnabled(i < self.canvas.page_count - 1)
        self.thumbs.select_page(i)
        self.marcadores.marcar_actual(i)
        if self.canvas.doc is not None:
            self._pos_timer.start()

    def _update_state(self):
        loaded = self.canvas.doc is not None or bool(self.canvas.pages)
        self.outer.setCurrentIndex(1 if loaded else 0)
        try:
            changes = self.canvas.has_changes()
        except RuntimeError:              # Qt está destruyendo la escena (al cerrar la aplicación)
            return
        self.unsaved.setVisible(changes)
        self.save_group.actualizar(loaded, changes, directo=changes and bool(self.canvas.path)
                                   and not core.is_image(self.canvas.path))
        self.b_undo.setEnabled(bool(self.canvas.undo_stack))
        self.b_redo.setEnabled(bool(self.canvas.redo_stack))
        self.b_delete.setEnabled(bool([i for i in self.canvas.scene().selectedItems() if isinstance(i, _Editable)]))
        self.buscador.mostrar_flechas(bool(self.canvas.hits))
        editable = not self.canvas.view_rot      # con la vista girada no se edita
        for key, b in self.tool_buttons.items():
            b.setEnabled(editable or key == "select")
        self.b_image.setEnabled(editable)
        self._update_history()
        if loaded:
            self._page_changed(self.canvas.current_page)
            self._ajustar_barras()        # el aviso «Cambios sin guardar» ocupa lugar: que todo siga cabiendo

    def _update_history(self):
        self.b_back.setVisible(self.canvas.can_go_back())

    # ------------------------------------------------------------------ búsqueda
    def _search_enter(self):
        term = self.search.text().strip()
        if not term:
            return
        if self._last_term == term and self.canvas.hits:
            self._step_hit(1)
            return
        self._last_term = term
        n = self.canvas.search(term)
        self._update_hits()
        if not n:
            self.hits_label.setText("Sin resultados")

    def _step_hit(self, d: int):
        if self.canvas.hits:
            self.canvas.goto_hit(self.canvas.hit_index + d)
            self._update_hits()

    def _update_hits(self):
        c = self.canvas
        self.hits_label.setText(f"{c.hit_index + 1} de {len(c.hits)}" if c.hits else "")
        self._update_state()

    def _clear_search(self):
        self.canvas.clear_search()
        self._last_term = None
        self.hits_label.setText("")
        self._update_state()

    def _search_for(self, term: str):
        """«Buscar en el documento» desde el menú de la selección."""
        self.search.setText(term)
        self._last_term = None
        self._search_enter()

    # ------------------------------------------------------------------ vista
    def _load_view_settings(self):
        s = ctx.settings
        vista = s.get("visor_vista", "una") if s is not None else "una"
        c = self.canvas
        c.view_mode = "dos" if vista in ("dos", "dos_portada") else "una"
        c.cover_alone = vista == "dos_portada"
        c.set_night(bool(s.get("visor_noche", False)) if s is not None else False)
        self._sync_view_menu()

    def _sync_view_menu(self):
        vm, c = self.view_menu, self.canvas
        for act, on in ((vm.act_una, c.view_mode == "una"), (vm.act_dos, c.view_mode == "dos"),
                        (vm.act_portada, c.cover_alone), (vm.act_lectura, self._reading),
                        (vm.act_completa, self._full), (vm.act_noche, c.night)):
            act.blockSignals(True)
            act.setChecked(on)
            act.blockSignals(False)
        vm.act_portada.setEnabled(c.view_mode == "dos")
        vm.act_girar.setText("Girar la vista\tCtrl+R" + (f"   (ahora {c.view_rot}°)" if c.view_rot else ""))

    def _save_setting(self, key: str, value):
        if ctx.settings is not None:
            try:
                ctx.settings.set(key, value)
            except Exception:  # noqa: BLE001 — los ajustes nunca deben interrumpir la lectura
                pass

    def set_view_mode(self, mode: str, cover_alone: bool | None = None):
        """«Una página» (continua) o «Dos páginas», con la portada sola si se pide."""
        c = self.canvas
        c.set_view(mode, cover_alone)
        self._save_setting("visor_vista", "una" if c.view_mode == "una"
                           else "dos_portada" if c.cover_alone else "dos")
        self._sync_view_menu()

    def set_night(self, on: bool):
        self.canvas.set_night(on)
        self._save_setting("visor_noche", bool(on))
        self._sync_view_menu()

    def rotate_view(self):
        """Gira 90° cómo se ven las páginas, sin tocar el archivo."""
        if self.canvas.rotate_view(90):
            self.set_tool("select")
            self._update_state()
            if self.canvas.view_rot:
                self._set_tip(ROTATED_MSG, warn=True)
        self._sync_view_menu()

    def go_back(self):
        self.canvas.history_back()

    def go_forward(self):
        self.canvas.history_forward()

    def show_left_tab(self, k: int):
        self.left_stack.setCurrentIndex(k)
        (self.tab_pages if k == 0 else self.tab_marks).setChecked(True)
        if k == 1:
            self.marcadores.marcar_actual(self.canvas.current_page)

    def _end_editing(self):
        editing = self.canvas.editing_text()
        if editing is not None:
            editing.clearFocus()

    def set_reading_mode(self, on: bool):
        """Modo lectura: solo el documento, con el encabezado y el pie."""
        on = bool(on)
        if on != self._reading:
            self._end_editing()
            panels = (self.toolbar, self.left_panel, self.props_panel)
            if on:
                self._reading_prev = {w: w.isVisibleTo(self) for w in panels}
                for w in panels:
                    w.hide()
                self.set_tool("select")
            else:
                prev = self._reading_prev or {}
                for w in panels:
                    w.setVisible(prev.get(w, True))
                self._reading_prev = None
            self._reading = on
        self._sync_view_menu()
        self.canvas.setFocus()

    def toggle_fullscreen(self):
        self.set_fullscreen(not self._full)

    def set_fullscreen(self, on: bool):
        """Pantalla completa: además del modo lectura, oculta el menú lateral y el encabezado. Al salir
        todo vuelve exactamente a como estaba (también si el menú estaba contraído)."""
        on = bool(on)
        if on == self._full:
            self._sync_view_menu()
            return
        win = self.window()
        sidebar = getattr(win, "sidebar", None)
        self._end_editing()
        if on:
            self._full_prev = {"state": win.windowState(), "reading": self._reading,
                               "header": self.header.isVisibleTo(self),
                               "sidebar": sidebar.isVisibleTo(win) if sidebar is not None else None}
            self._full = True
            self.set_reading_mode(True)
            self.header.hide()
            if sidebar is not None:
                sidebar.hide()
            self.b_exit_full.show()
            self._esc_full.setEnabled(True)
            if win is not self:
                win.showFullScreen()
        else:
            prev = self._full_prev or {}
            self._full = False
            self._esc_full.setEnabled(False)
            self.b_exit_full.hide()
            if win is not self:
                state = prev.get("state", Qt.WindowState.WindowNoState)
                if state & Qt.WindowState.WindowFullScreen:
                    pass
                elif state & Qt.WindowState.WindowMaximized:
                    win.showMaximized()
                else:
                    win.showNormal()
            if sidebar is not None and prev.get("sidebar") is not None:
                sidebar.setVisible(prev["sidebar"])
            self.header.setVisible(prev.get("header", True))
            self.set_reading_mode(prev.get("reading", False))
            self._full_prev = None
        self._sync_view_menu()
        self.canvas.setFocus()

    def _escape_fullscreen(self):
        if self.canvas.editing_text() is not None:
            self._end_editing()
            return
        self.canvas.clear_selection()
        self.set_fullscreen(False)

    def hideEvent(self, e):
        # Si se sale del visor (otra herramienta), la pantalla completa no debe quedar puesta.
        if self._full and not e.spontaneous():
            self.set_fullscreen(False)
        super().hideEvent(e)

    # ------------------------------------------------------------------ continuar donde quedaste
    @staticmethod
    def _position_key(path: str) -> str:
        return os.path.normcase(os.path.abspath(path))

    def _remember_position(self):
        """Guarda la página en que va el documento (solo la ruta y el número, nunca el contenido)."""
        s, c = ctx.settings, self.canvas
        if s is None or not c.path or not c.pages or not s.get("recent_enabled", True):
            return
        key = self._position_key(c.path)
        positions = dict(s.get("visor_posiciones") or {})
        page = c.current_page + 1
        old = positions.get(key)
        if (page == 1 and old is None) or old == page:
            return
        positions.pop(key, None)
        if page > 1:
            positions[key] = page
        while len(positions) > MAX_POSITIONS:
            positions.pop(next(iter(positions)))
        self._save_setting("visor_posiciones", positions)

    def _restore_reading_position(self, path: str):
        s, c = ctx.settings, self.canvas
        if s is None or not s.get("recent_enabled", True):
            return
        page = (s.get("visor_posiciones") or {}).get(self._position_key(path))
        if isinstance(page, int) and 1 < page <= c.page_count:
            c.go_to_page(page - 1)
            self._show_notice(f"Continuaste en la página {page}.")

    # ------------------------------------------------------------------ archivos
    def accept_files(self, paths: list[str]):
        pdfs = [p for p in paths if p.lower().endswith(".pdf")]
        if pdfs:
            self.load(pdfs[0])

    def open_dialog(self):
        paths = ask_open(self, False, title="Abrir PDF")
        if paths:
            self.load(paths[0])

    def _confirm_discard(self) -> bool:
        """Antes de abrir otro documento: «Guardar», «No guardar» o «Cancelar».  True si se puede seguir."""
        if not self.canvas.has_changes():
            return True
        nombre = Path(self.canvas.path).name if self.canvas.path else "el documento"
        r = guardado_ui.preguntar_cambios(self, "Cambios sin guardar",
                                          f"¿Quieres guardar los cambios de «{nombre}» antes de abrir otro "
                                          "documento?")
        if r == "guardar":
            return guardado_ui.guardar_y_esperar(self)
        if r == "descartar":
            guardado_ui.trabajo_descartado(self, "viewer")
            return True
        return False

    def load(self, path: str, confirm: bool = True) -> bool:
        if not path.lower().endswith(".pdf"):
            QMessageBox.warning(self, "Abrir", "Solo se pueden abrir archivos PDF en el visor.")
            return False
        if confirm and not self._confirm_discard():
            return False
        if not ctx.passwords.ensure(path, self):
            return False
        self._pos_timer.stop()
        self._remember_position()            # la página en que iba el documento anterior
        self._end_editing()
        try:
            self.canvas.load(path, ctx.passwords.get(path))
        except core.UserError as exc:
            QMessageBox.warning(self, "No se pudo abrir", str(exc))
            return False
        extensiones.visor_abierto(self)      # las extensiones sueltan lo del documento anterior
        ctx.remember_dir(path)
        ctx.add_recent(path, self.canvas.page_count)
        with PDF_LOCK:
            scanned = core.looks_scanned(self.canvas.doc)
        self.banner.setVisible(scanned)
        self.ext_avisos.actualizar()
        self.search.clear()
        n = self.canvas.page_count
        self.doc_name.setText(Path(path).name)
        self.doc_name.setToolTip(path)
        try:
            size = core.human_size(os.path.getsize(path))
        except OSError:
            size = ""
        self.doc_meta.setText(f"{n} página{'s' if n != 1 else ''}" + (f" · {size.replace('.', ',')}" if size else ""))
        self.pages_count.setText(f"{n} página{'s' if n != 1 else ''}")
        self.page_total.setText(f"de {n}")
        self.page_spin.setRange(1, max(1, n))
        self.thumbs.reset_pages()
        self._load_bookmarks()
        win = self.window()
        if win and hasattr(win, "setWindowTitle"):
            win.setWindowTitle(f"{Path(path).name} — RonyPDF")
        self.set_tool("select")
        self._update_state()
        self._sync_view_menu()
        self._restore_reading_position(path)
        self._nota_pie()
        self._ajustar_barras()
        self.canvas.setFocus()
        return True

    def _load_bookmarks(self):
        c = self.canvas
        toc, pagemode = [], ""
        with PDF_LOCK:
            try:
                toc = c.doc.get_toc(simple=False)
            except Exception:  # noqa: BLE001 — un índice dañado no impide leer el documento
                toc = []
            try:
                pagemode = c.doc.pagemode or ""
            except Exception:  # noqa: BLE001
                pagemode = ""
        self.marcadores.cargar(toc, c.page_count)
        n = self.marcadores.total
        self.tab_marks.setText(f"Marcadores ({n})")
        self.tab_marks.setToolTip("Marcadores (índice) del documento" if n else "Este PDF no tiene marcadores.")
        if n and pagemode == "UseOutlines":
            self.show_left_tab(1)        # el documento pide abrirse con el índice a la vista
        self.marcadores.marcar_actual(c.current_page)

    def _go_ocr(self):
        win = ctx.window
        if win and self.canvas.path:
            win.navigate("ocr")
            win.pages["ocr"].accept_files([self.canvas.path])

    def _go_organize(self):
        win = ctx.window
        if win and self.canvas.path:
            win.navigate("organize")
            win.pages["organize"].accept_files([self.canvas.path])

    def insert_image(self):
        if self.canvas.doc is None:
            return
        if self.canvas.view_rot:
            self.canvas.status.emit(ROTATED_MSG)
            return
        paths = ask_open(self, False, IMAGE_FILTER, "Elige una imagen o firma (PNG con fondo transparente "
                                                     "queda mejor)")
        if paths:
            self.canvas.add_image(paths[0])
            self.set_tool("select")

    # ------------------------------------------------------------------ guardar
    def _guardar_principal(self):
        """Botón principal y Ctrl+S: «Guardar» si hay cambios; si no, «Guardar como…» (como antes)."""
        c = self.canvas
        if c.has_changes() and c.path and not core.is_image(c.path):
            self.guardar()
        else:
            self.save()

    def _recoger(self):
        """Lo que hay que escribir: (cambios del visor, elementos fuera de las páginas, cambios de las
        extensiones).  Termina antes la edición de un texto, para que entre completo."""
        c = self.canvas
        focus = c.scene().focusItem()
        if focus:
            focus.clearFocus()
        sin_autor = [i for i in c.items_order if isinstance(i, HighlightItem) and not i.autor and i.scene() is not None]
        if sin_autor:                                # los resaltados llevan el nombre de quien los hizo
            nombre = autor_comentarios() or self._preguntar_autor()
            for i in sin_autor:
                i.autor = nombre or ""
        edits, skipped = c.collect_edits()
        extra = extensiones.guardar_visor(self)      # cambios de las extensiones (None si no hay)
        return edits, skipped, extra

    def _preguntar_autor(self) -> str:
        """La primera vez, el nombre con el que se firman los comentarios (el diálogo del panel «Comentarios»).
        Si la persona cancela, se guarda igual, sin nombre."""
        try:
            from . import comentarios_ui
        except ImportError:
            return ""
        return comentarios_ui.asegurar_autor(self) or ""

    def save(self, al_terminar=None):
        """«Guardar como…»: escribe los cambios en el archivo que se elija (reescritura completa)."""
        c = self.canvas
        fin = guardado_ui.callback(al_terminar)
        if c.doc is None or ctx.busy:
            return fin(False)
        edits, skipped, extra = self._recoger()
        src = c.path
        if not edits and extra is None:
            out = ask_save(self, suggest_output(src, "_copia"))
            if not out:
                return fin(False)
            if os.path.normcase(os.path.abspath(out)) != os.path.normcase(os.path.abspath(src)):
                try:
                    guardado.copiar(src, out)
                except (OSError, core.UserError) as exc:
                    QMessageBox.warning(self, "No se pudo guardar", str(exc))
                    return fin(False)
                show_done(self, "Copia guardada", file=out)
            return fin(True)
        if skipped:
            QMessageBox.information(self, "Guardar", f"{skipped} elemento(s) quedaron fuera de las páginas y no "
                                    "se guardarán.")
        out = ask_save(self, suggest_output(src, "_editado"))
        if not out:
            return fin(False)
        self._guardar_en(out, edits, extra, directo=False, incremental=False, al_terminar=fin)

    def guardar(self, al_terminar=None):
        """«Guardar» en el mismo archivo: la primera vez lo confirma, antes deja una copia de respaldo y, si
        ningún cambio borra contenido, solo agrega los cambios al final del archivo (guardado incremental)."""
        c = self.canvas
        fin = guardado_ui.callback(al_terminar)
        if c.doc is None or ctx.busy:
            return fin(False)
        if not c.has_changes() or not c.path or core.is_image(c.path):
            return self.save(al_terminar=fin)
        edits, skipped, extra = self._recoger()
        if not edits and extra is None:
            return self.save(al_terminar=fin)
        with PDF_LOCK:
            # si un comentario se borra o cambia de texto, lo anterior no puede quedar dentro del archivo
            incremental = guardado.modo_incremental(c.doc, edits) and not getattr(extra, "borra", False)
            firmado = guardado.tiene_firma_digital(c.doc)
        r = guardado_ui.preparar_directo(self, c.path, c.disco, incremental, firmado)
        if r == "guardar_como":
            return self.save(al_terminar=fin)
        if r != "guardar":
            return fin(False)
        if skipped:
            QMessageBox.information(self, "Guardar", f"{skipped} elemento(s) quedaron fuera de las páginas y no "
                                    "se guardarán.")
        self._guardar_en(c.path, edits, extra, directo=True, incremental=incremental, al_terminar=fin)

    def _guardar_en(self, out: str, edits, extra, directo: bool, incremental: bool, al_terminar):
        """Rutina común de «Guardar» y «Guardar como…»: si el destino es el mismo archivo, antes se respalda."""
        c = self.canvas
        src = c.path
        same = os.path.normcase(os.path.abspath(out)) == os.path.normcase(os.path.abspath(src))
        password = c.password
        pagina = c.current_page
        hecho: dict = {}
        volver: list = []
        if same:
            # si otra pantalla tiene abierto este archivo, se suelta allí y se vuelve a abrir al terminar
            volver = guardado_ui.preparar_reemplazo(self, out)
            if volver is None:
                return al_terminar(False)
            c.close_doc()                     # Windows no deja reemplazar un archivo abierto

        def done(res):
            hecho["ok"] = True
            if res.error_respaldo:
                if guardado_ui.respaldo_fallido(self, res.error_respaldo) == "guardar_como":
                    self.save(al_terminar=al_terminar)
                else:
                    al_terminar(False)
                return
            if password:
                ctx.passwords.set(out, password)
            self.load(out, confirm=False)
            if pagina and c.page_count:
                c.go_to_page(min(pagina, c.page_count - 1))
            guardado_ui.trabajo_guardado(self, "viewer")
            if directo:
                self._show_notice(guardado_ui.aviso_guardado(res.respaldo is not None))
            else:
                n = res.valor
                detalle = (f"Se aplicaron {n} cambio(s) en «{Path(out).name}»." if n
                           else f"Los cambios quedaron guardados en «{Path(out).name}».")
                show_done(self, "Cambios guardados", detalle, file=out)
            al_terminar(True)

        def finally_():
            if c.doc is None and same:
                try:
                    c.reopen()
                except core.UserError:
                    pass
            guardado_ui.reabrir(volver)
            guardado_ui.revisar_fin(hecho, al_terminar)

        guardado_ui.lanzar_guardado("Guardando…" if directo else "Guardando cambios…", guardado.guardar_con_respaldo,
                                    done, on_finally=finally_, tarea=core.apply_edits,
                                    respaldo=same and guardado_ui.respaldo_activo(),
                                    ajustes=guardado_ui.ajustes_path(), retencion=guardado_ui.retencion(),
                                    path=src, output=out, edits=edits, password=password, extra=extra,
                                    incremental=incremental)
    def has_unsaved(self) -> bool:
        return self.canvas.has_changes()


def _key_for(tool: str) -> str:
    return next((k for k, t in TOOL_KEYS.items() if t == tool), "")
