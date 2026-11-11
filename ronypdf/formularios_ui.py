"""Pantalla «Formularios y firma»: rellenar campos de verdad y firmar sin imprimir.

Una sola pantalla sirve para los dos casos:
- PDF con campos (AcroForm): cada campo se edita ahí mismo, sobre la página, con Tab para avanzar.
- PDF planos o escaneados: se escribe encima con Texto, Equis, Chulo, Fecha y Firma.

La escena trabaja en puntos PDF (como el visor).  Los editores de los campos son widgets de Qt
dentro de la escena (QGraphicsProxyWidget) y solo existen para las páginas visibles y sus vecinas,
así un formulario de cientos de campos se mueve con fluidez.  Los valores viven en el modelo de
la página; los sellos se escriben en el PDF solo al guardar, con ``formularios.rellenar``.
"""

from __future__ import annotations

import copy
import datetime
import math
import os
import time
from collections import defaultdict
from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QCursor, QFont, QFontMetricsF, QIcon, QImage, QIntValidator, QKeySequence,
                           QPainter, QPainterPath, QPen, QShortcut, QTransform)
from PySide6.QtWidgets import (QAbstractButton, QAbstractItemView, QAbstractScrollArea, QComboBox, QDialog,
                               QFileDialog, QFrame, QGraphicsItem, QGraphicsProxyWidget, QGraphicsRectItem,
                               QGraphicsScene, QGraphicsView, QGridLayout, QHBoxLayout, QLineEdit, QListWidget,
                               QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QSizePolicy,
                               QStackedWidget, QStyle, QStyledItemDelegate, QToolButton, QVBoxLayout, QWidget)

from . import core, extensiones, firmas, formularios, guardado, guardado_ui, icons, tema
from .formularios import RELLENABLES, Campo
from .icons import icon
from .viewer import PageItem, ShapeItem, TextItem
from .widgets import (PDF_AND_IMAGES_FILTER, PDF_LOCK, Chip, DropArea, FileChip, Note, SwitchRow, ToolPage,
                      ask_open, ask_save, cat_de, ctx, label, qimage_from_render, show_done, suggest_output)

GAP = 18
MARGIN = 28
MAX_PIXELS = 16_000_000
Z_PAGINA, Z_ZONA, Z_EDITOR, Z_SELLO, Z_DESTACADO = 0, 2, 3, 4, 6



def _pag(nombre: str) -> str:
    """Color de lo que se dibuja sobre la página del PDF.  La página es blanca en los dos temas (es el documento,
    no la interfaz), así que usa siempre los tonos del tema claro."""
    return tema.CLARO[nombre]


CAMPO_FONDO = _pag("info-tint")
CAMPO_BORDE = tema.mezclar(_pag("info-tint"), _pag("focus"), 0.4)
CAMPO_FOCO = _pag("focus")
CAMPO_OBLIGATORIO = _pag("danger")
CAMPO_SOLO_LECTURA = _pag("surface-sunken")
CAMPO_TEXTO_APAGADO = _pag("ink-3")
TINTA_RGB = (0.067, 0.094, 0.153)       # la tinta de lo que se escribe en el PDF (textos, marcas)
TINTA = QColor.fromRgbF(*TINTA_RGB)

TAMANOS = {   # texto (pt), lado de las marcas (pt), ancho de la firma (pt)
    "pequeno": dict(texto=9, marca=9, firma=110),
    "mediano": dict(texto=11, marca=12, firma=160),
    "grande": dict(texto=14, marca=16, firma=220),
}
NOMBRE_TAMANO = {"pequeno": "Pequeño", "mediano": "Mediano", "grande": "Grande"}
HERRAMIENTAS = [
    ("rellenar", "Rellenar", "cursor"),
    ("texto", "Texto", "type"),
    ("equis", "Equis", "remove"),
    ("chulo", "Chulo", "check"),
    ("fecha", "Fecha", "forms_fecha"),
]
AYUDA = {
    "rellenar": "Haz clic en un campo para escribir. Tab pasa al siguiente y Shift+Tab al anterior.",
    "rellenar_plano": "Elige Texto, Equis, Chulo, Fecha o Firma y haz clic sobre la página.",
    "texto": "Haz clic donde quieras escribir. Después puedes arrastrar el texto para moverlo.",
    "equis": "Haz clic en cada casilla que quieras marcar con una equis. Esc para terminar.",
    "chulo": "Haz clic en cada casilla que quieras marcar con un chulo. Esc para terminar.",
    "fecha": "Haz clic donde va la fecha de hoy. Doble clic sobre ella para cambiarla.",
    "firma": "Haz clic donde va tu firma. Luego puedes moverla y agrandarla desde la esquina.",
}
AVISOS = {
    "xfa": "Este formulario usa una tecnología antigua de Adobe (XFA). Puede que algunos campos no aparezcan.",
    "firmado": "Este PDF tiene una firma digital. Si lo modificas, esa firma dejará de ser válida.",
    "calculos": ("Este formulario hace cálculos automáticos (sumas, totales). RonyPDF no los recalcula: "
                 "revisa los totales antes de enviar."),
    "sin_campos": ("Este documento no tiene campos para rellenar. Usa Texto, Equis, Chulo, Fecha y Firma "
                   "para escribir encima."),
}
ROL_ID = Qt.ItemDataRole.UserRole
ROL_TITULO = Qt.ItemDataRole.UserRole + 1
ROL_SUB = Qt.ItemDataRole.UserRole + 2
ROL_ESTADO = Qt.ItemDataRole.UserRole + 3


# --------------------------------------------------------------------------
# Íconos (cuadrícula de 24 × 24, de línea, como los de icons.py)
# --------------------------------------------------------------------------

def _hoja(p: QPainter, x=4.0, y=2.0, w=16.0, h=20.0, fold=4.5):
    path = QPainterPath()
    path.moveTo(x, y)
    path.lineTo(x + w - fold, y)
    path.lineTo(x + w, y + fold)
    path.lineTo(x + w, y + h)
    path.lineTo(x, y + h)
    path.closeSubpath()
    p.drawPath(path)
    p.drawPolyline([QPointF(x + w - fold, y), QPointF(x + w - fold, y + fold), QPointF(x + w, y + fold)])


def dibujo(p: QPainter, base: QColor, acento: QColor):
    """Hoja con dos renglones y un chulo rojo."""
    _hoja(p)
    p.drawLine(QPointF(8, 8.5), QPointF(14.5, 8.5))
    p.drawLine(QPointF(8, 12.5), QPointF(16, 12.5))
    extensiones.lapiz(p, acento)
    p.drawPolyline([QPointF(8.5, 17), QPointF(10.8, 19.2), QPointF(16, 14.5)])


def _dibujo_fecha(p: QPainter, base: QColor, _acento: QColor):
    p.drawRoundedRect(QRectF(3.5, 5, 17, 15.5), 2.2, 2.2)
    p.drawLine(QPointF(3.5, 9.5), QPointF(20.5, 9.5))
    p.drawLine(QPointF(8, 3), QPointF(8, 6.5))
    p.drawLine(QPointF(16, 3), QPointF(16, 6.5))
    for x, y in ((8, 13.2), (12, 13.2), (16, 13.2), (8, 17), (12, 17)):
        p.drawPoint(QPointF(x, y))


icons.EXTRA.setdefault("forms_fecha", _dibujo_fecha)


def _seg_button(text: str, icon_name: str | None = None, tip: str = "", checkable: bool = True) -> QToolButton:
    b = QToolButton()
    b.setObjectName("SegButton")
    b.setText(text)
    if icon_name:
        b.setIcon(icon(icon_name, tema.color("ink-2"), tema.color("ink"), 17))
        b.setIconSize(QSize(17, 17))
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon if text
                             else Qt.ToolButtonStyle.ToolButtonIconOnly)
    b.setCheckable(checkable)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if tip:
        b.setToolTip(tip)
        b.setAccessibleName(tip)
    return b


def _seg_group(*buttons: QWidget) -> QFrame:
    box = QFrame()
    box.setObjectName("SegGroup")
    lay = QHBoxLayout(box)
    lay.setContentsMargins(3, 3, 3, 3)
    lay.setSpacing(2)
    for b in buttons:
        lay.addWidget(b)
    return box


def _icon_button(icon_name: str, tip: str, cb=None) -> QToolButton:
    b = QToolButton()
    b.setObjectName("IconButton")
    b.setIcon(icon(icon_name, tema.color("ink-2"), size=17))
    b.setIconSize(QSize(17, 17))
    b.setToolTip(tip)
    b.setAccessibleName(tip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if cb:
        b.clicked.connect(cb)
    return b


def _plural(n: int, uno: str, varios: str) -> str:
    return f"{n} {uno if n == 1 else varios}"


def _detalle_guardado(campos: int, elementos: int, aplanado: bool) -> str:
    partes = []
    if campos:
        partes.append(f"se {'rellenó' if campos == 1 else 'rellenaron'} {_plural(campos, 'campo', 'campos')}")
    if elementos:
        partes.append(f"se {'agregó' if elementos == 1 else 'agregaron'} "
                      f"{_plural(elementos, 'elemento', 'elementos')}")
    texto = " y ".join(partes) + "."
    texto = texto[0].upper() + texto[1:] if partes else "Se guardó una copia del documento."
    if aplanado:
        texto += " El formulario quedó aplanado: ya no se puede cambiar lo escrito."
    return texto


def _lleno(c: Campo, v) -> bool:
    if c.tipo in ("casilla", "opcion"):
        return bool(v)
    if isinstance(v, (list, tuple)):
        return bool(v)
    return bool(str(v or "").strip())


# --------------------------------------------------------------------------
# Editores de campos
# --------------------------------------------------------------------------

def _qss(clase: str, c: Campo, vacio: bool) -> str:
    fondo = CAMPO_SOLO_LECTURA if c.solo_lectura else CAMPO_FONDO
    borde = CAMPO_OBLIGATORIO if (c.obligatorio and vacio and not c.solo_lectura) else CAMPO_BORDE
    color = CAMPO_TEXTO_APAGADO if c.solo_lectura else _pag("ink")
    letra = max(1, int(round(_tamano_letra(c))))     # la hoja de estilo global fija 10 pt: se reemplaza aquí
    css = (f"{clase}#CampoEditor {{ background: {fondo}; color: {color}; border: 1px solid {borde}; "
           f"font-family: Arial; font-size: {letra}px; "
           f"border-radius: 0px; padding: 0px 1px; margin: 0px; min-height: 1px; min-width: 1px; "
           f"selection-background-color: {CAMPO_FOCO}; selection-color: white; }}"
           f"{clase}#CampoEditor:focus {{ border: 1px solid {CAMPO_FOCO}; background: {_pag('surface')}; }}"
           f"{clase}#CampoEditor:disabled {{ background: {CAMPO_SOLO_LECTURA}; color: {CAMPO_TEXTO_APAGADO}; }}")
    if clase == "QComboBox":
        css += ("QComboBox#CampoEditor::drop-down { border: none; width: 11px; }"
                f"QComboBox#CampoEditor QAbstractItemView {{ selection-background-color: {CAMPO_FONDO}; "
                f"selection-color: {_pag('ink')}; }}")
    if clase == "QListWidget":
        css += ("QListWidget#CampoEditor::item { padding: 0px 1px; border: none; }"
                f"QListWidget#CampoEditor::item:selected {{ background: {CAMPO_BORDE}; color: {_pag('ink')}; }}")
    return css


def _tamano_letra(c: Campo) -> float:
    if c.tamano_fuente:
        return max(4.0, min(36.0, c.tamano_fuente))
    h = c.rect[3] - c.rect[1]
    if c.tipo == "texto" and not c.multilinea:
        return max(5.0, min(12.0, h * 0.62))
    if c.tipo == "desplegable":
        return max(5.0, min(12.0, h * 0.6))
    if c.tipo == "lista":
        return max(5.0, min(10.0, h - 3))
    return 10.0


class CasillaEditor(QAbstractButton):
    """Casilla o botón de opción dibujado a la medida exacta del campo."""
    siguiente = Signal()

    def __init__(self, campo: Campo):
        super().__init__()
        self.campo = campo
        self.redonda = campo.tipo == "opcion"
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(campo.titulo)

    def nextCheckState(self):
        if self.redonda and self.isChecked():
            return                    # una opción marcada no se desmarca con otro clic (como en los PDF)
        super().nextCheckState()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.siguiente.emit()
            return
        super().keyPressEvent(e)

    def sizeHint(self) -> QSize:
        return QSize(14, 14)

    def minimumSizeHint(self) -> QSize:
        return QSize(1, 1)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        c = self.campo
        fondo = QColor(CAMPO_SOLO_LECTURA if not self.isEnabled() else
                       (_pag("surface") if self.hasFocus() else CAMPO_FONDO))
        pendiente = c.obligatorio and not self.isChecked() and self.isEnabled()
        borde = QColor(CAMPO_FOCO if self.hasFocus() else (CAMPO_OBLIGATORIO if pendiente else CAMPO_BORDE))
        p.setPen(QPen(borde, 1.2 if self.hasFocus() else 1))
        p.setBrush(fondo)
        lado = min(r.width(), r.height())
        if self.redonda:
            cuadro = QRectF(r.center().x() - lado / 2, r.center().y() - lado / 2, lado, lado)
            p.drawEllipse(cuadro)
            if self.isChecked():
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(TINTA if self.isEnabled() else QColor(CAMPO_TEXTO_APAGADO))
                p.drawEllipse(cuadro.center(), lado * 0.26, lado * 0.26)
        else:
            p.drawRect(r)
            if self.isChecked():
                pen = QPen(TINTA if self.isEnabled() else QColor(CAMPO_TEXTO_APAGADO), max(1.0, lado * 0.13))
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                p.setPen(pen)
                x0, y0 = r.center().x() - lado * 0.32, r.center().y() - lado * 0.3
                s = lado * 0.64
                p.drawPolyline([QPointF(x0, y0 + s * 0.55), QPointF(x0 + s * 0.38, y0 + s * 0.92),
                                QPointF(x0 + s, y0 + s * 0.05)])
        p.end()


class FirmaZona(QGraphicsRectItem):
    """Campo de firma del PDF: un clic pone ahí la firma en imagen."""

    def __init__(self, campo: Campo, rect: QRectF, al_clic):
        super().__init__(rect)
        self.campo = campo
        self.al_clic = al_clic
        firmado = campo.firmado
        color = QColor(_pag("ok") if firmado else CAMPO_FOCO)
        self.setPen(QPen(color, 0, Qt.PenStyle.DashLine))
        if firmado:                       # se deja ver la firma digital que ya tiene
            self.setBrush(Qt.BrushStyle.NoBrush)
        else:                             # tapa la apariencia vacía (algunos PDF dibujan un rótulo)
            self.setBrush(QColor(CAMPO_FONDO))
        self.setZValue(Z_ZONA)
        self.setData(0, campo.id)
        self.con_firma = False            # ya tiene encima una firma en imagen
        if firmado:
            self.setToolTip("Este campo ya tiene una firma digital.")
        else:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setToolTip("Haz clic para poner tu firma aquí")

    def paint(self, p, opt, widget=None):
        super().paint(p, opt, widget)
        r = self.rect()
        f = QFont("Segoe UI")
        p.setFont(f)
        if self.campo.firmado:
            f.setPixelSize(max(4, int(min(8, r.height() * 0.3))))
            p.setFont(f)
            p.setPen(QColor(_pag("ok")))
            p.drawText(r.adjusted(2, 1, -2, -1), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                       "Firmado digitalmente")
            return
        if self.con_firma:
            return
        f.setPixelSize(max(4, int(min(11, r.height() * 0.38))))
        p.setFont(f)
        p.setPen(QColor(CAMPO_FOCO))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, "Firmar aquí")

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and not self.campo.firmado:
            e.accept()
            self.al_clic(self.campo)
            return
        e.ignore()


# --------------------------------------------------------------------------
# Sellos: textos, marcas y firmas que se escriben encima de la página
# --------------------------------------------------------------------------

class SelloTexto(TextItem):
    """Texto o fecha: se escribe con el teclado, se mueve arrastrando y se edita con doble clic."""

    def __init__(self, texto: str, tamano: float, tipo: str = "texto"):
        super().__init__(texto, tamano, core.SANS, False, False, TINTA)
        self.sello = tipo
        self.setZValue(Z_SELLO)

    def set_tamano(self, tamano: float):
        self.set_style(size=tamano)


class MarcaSello(QGraphicsItem):
    """Equis o chulo para marcar casillas impresas."""

    def __init__(self, tipo: str, lado: float):
        super().__init__()
        self.sello = tipo
        self.lado = lado
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setZValue(Z_SELLO)
        self.setCursor(Qt.CursorShape.SizeAllCursor)

    def _grosor(self) -> float:
        return max(1.0, self.lado / 7)

    def boundingRect(self) -> QRectF:
        m = self._grosor() + 2
        return QRectF(-m, -m, self.lado + 2 * m, self.lado + 2 * m)

    def set_tamano(self, lado: float):
        self.prepareGeometryChange()
        c = self.mapToScene(QPointF(self.lado / 2, self.lado / 2))
        self.lado = lado
        self.setPos(c.x() - lado / 2, c.y() - lado / 2)
        self.update()

    def _puntos(self, r: QRectF) -> list[list[QPointF]]:
        if self.sello == "equis":
            return [[r.topLeft(), r.bottomRight()], [r.bottomLeft(), r.topRight()]]
        return [[QPointF(r.left(), r.top() + r.height() * 0.55), QPointF(r.left() + r.width() * 0.38, r.bottom()),
                 r.topRight()]]

    def paint(self, p, opt, widget=None):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(TINTA, self._grosor())
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        for linea in self._puntos(QRectF(0, 0, self.lado, self.lado)):
            p.drawPolyline(linea)
        if self.isSelected():
            p.setPen(QPen(QColor(_pag("cat-ver")), 0, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(self.boundingRect().adjusted(1, 1, -1, -1))

    def to_edit(self, page: int, origin: QPointF) -> list[core.Edit]:
        r = self.mapRectToScene(QRectF(0, 0, self.lado, self.lado)).translated(-origin)
        out = []
        for linea in self._puntos(r):
            kind = "line" if len(linea) == 2 else "ink"
            out.append(core.Edit(kind, page, points=[(q.x(), q.y()) for q in linea], color=TINTA_RGB,
                                 width=self._grosor()))
        return out


def _es_sello(item) -> bool:
    return item is not None and hasattr(item, "sello")


# --------------------------------------------------------------------------
# Lienzo
# --------------------------------------------------------------------------

class FormCanvas(QGraphicsView):
    zoom_changed = Signal(float)
    page_changed = Signal(int)
    files_dropped = Signal(list)

    def __init__(self, owner: "FormsPage"):
        super().__init__()
        self.owner = owner
        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(QColor(tema.color("surface-sunken")))
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
                            | QPainter.RenderHint.TextAntialiasing)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAcceptDrops(True)
        self.doc = None
        self.pages: list[PageItem] = []
        self.zoom = 1.0
        self.fit: str | None = "width"
        self.current_page = 0
        self.tool = "rellenar"
        self.por_pagina: dict[int, list[Campo]] = {}
        self.proxies: dict[str, QGraphicsProxyWidget] = {}
        self.editores: dict[str, QWidget] = {}
        self.zonas: dict[str, FirmaZona] = {}
        self.con_editores: set[int] = set()
        self._bloqueo = False
        self.render_timer = QTimer(self)
        self.render_timer.setSingleShot(True)
        self.render_timer.setInterval(30)
        self.render_timer.timeout.connect(self.render_visible)
        self.verticalScrollBar().valueChanged.connect(self._scrolled)
        self.horizontalScrollBar().valueChanged.connect(self._scrolled)
        self.scene().focusItemChanged.connect(self._foco_cambio)
        self.set_tool("rellenar")

    # ---------------------------------------------------------------- documento
    def cargar(self, doc, campos: list[Campo]):
        self.limpiar()
        self.doc = doc
        sizes = [(p.rect.width, p.rect.height) for p in doc]
        maxw = max((w for w, _ in sizes), default=595)
        y = 0.0
        for i, (w, h) in enumerate(sizes):
            item = PageItem(i, w, h)
            item.setPos((maxw - w) / 2, y)
            self.scene().addItem(item)
            self.pages.append(item)
            y += h + GAP
        self.scene().setSceneRect(-MARGIN, -MARGIN, maxw + 2 * MARGIN, max(y - GAP, 1) + 2 * MARGIN)
        self.por_pagina = defaultdict(list)
        for c in campos:
            if c.tipo == "boton" or not 0 <= c.pagina < len(self.pages):
                continue
            self.por_pagina[c.pagina].append(c)
            if c.tipo == "firma":
                zona = FirmaZona(c, self.rect_escena(c), self.owner.firmar_campo)
                self.scene().addItem(zona)
                self.zonas[c.id] = zona
        self.current_page = 0
        self.fit = "width"
        self.apply_fit()
        self.verticalScrollBar().setValue(self.verticalScrollBar().minimum())
        self.page_changed.emit(0)
        self.schedule_render()

    def limpiar(self):
        self.scene().clear()
        self.pages, self.por_pagina = [], {}
        self.proxies, self.editores, self.zonas = {}, {}, {}
        self.con_editores = set()
        self.doc = None

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def rect_escena(self, c: Campo) -> QRectF:
        x0, y0, x1, y1 = c.rect
        return QRectF(x0, y0, x1 - x0, y1 - y0).translated(self.pages[c.pagina].pos())

    # ---------------------------------------------------------------- zoom y render
    def set_zoom(self, z: float, keep_fit: bool = False):
        z = max(0.25, min(6.0, z))
        if not keep_fit:
            self.fit = None
        self.zoom = z
        self.setTransform(QTransform.fromScale(z, z))
        self.zoom_changed.emit(z)
        self.schedule_render()

    def zoom_by(self, factor: float):
        self.set_zoom(self.zoom * factor)

    def apply_fit(self):
        if not self.pages or not self.fit:
            return
        vbar = self.verticalScrollBar()
        vw = self.viewport().width() - (0 if vbar.isVisible() else vbar.sizeHint().width()) - 6
        widths = sorted(p.w for p in self.pages)
        z = max(vw / (widths[len(widths) // 2] + 2 * MARGIN), 0.25)
        anchor = self.transformationAnchor()
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.set_zoom(z, keep_fit=True)
        self.setTransformationAnchor(anchor)
        hbar = self.horizontalScrollBar()
        hbar.setValue((hbar.minimum() + hbar.maximum()) // 2)

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

    def visibles(self) -> list[int]:
        vis = self._visible_scene_rect()
        return [p.index for p in self.pages if p.sceneBoundingRect().intersects(vis)]

    def render_visible(self):
        if self.doc is None or not self.pages:
            return
        if getattr(self.doc, "is_closed", False):
            return
        if ctx.busy or not PDF_LOCK.acquire(blocking=False):
            self.render_timer.start(150)
            return
        pending = False
        visible: list[PageItem] = []
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
                    p.set_image(qimage_from_render(core.render_scaled(self.doc, p.index, s)), s)
            if visible:
                lo, hi = visible[0].index, visible[-1].index
                for p in self.pages:
                    if p.rendered and (p.index < lo - 3 or p.index > hi + 3):
                        p.clear_image()
        finally:
            PDF_LOCK.release()
        if visible:
            self.sync_editores(visible[0].index, visible[-1].index)
        if pending:
            self.render_timer.start(0)

    def _scrolled(self, *_):
        self.schedule_render()
        if not self.pages:
            return
        c = self.mapToScene(self.viewport().rect().center())
        idx = self.page_index_at_y(c.y())
        if idx != self.current_page:
            self.current_page = idx
            self.page_changed.emit(idx)

    def page_index_at_y(self, y: float) -> int:
        for p in self.pages:
            if y < p.y() + p.h + GAP / 2:
                return p.index
        return len(self.pages) - 1

    def page_at(self, sp: QPointF) -> PageItem | None:
        for p in self.pages:
            if p.sceneBoundingRect().contains(sp):
                return p
        return None

    def go_to_page(self, i: int):
        if not self.pages:
            return
        i = max(0, min(i, len(self.pages) - 1))
        p = self.pages[i]
        vh = self.viewport().height() / self.zoom
        self.centerOn(p.x() + p.w / 2, p.y() - 10 / self.zoom + vh / 2)
        self.current_page = i
        self.page_changed.emit(i)

    def wheelEvent(self, e):
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom_by(1.15 ** (e.angleDelta().y() / 120))
            e.accept()
            return
        it = self.itemAt(e.position().toPoint())
        cid = it.data(0) if isinstance(it, QGraphicsProxyWidget) else None
        if cid and (cid != self.campo_con_foco() or isinstance(it.widget(), (QComboBox, QLineEdit, CasillaEditor))):
            # La rueda sobre un campo desplaza la página: nunca cambia el valor de un desplegable sin querer.
            QAbstractScrollArea.wheelEvent(self, e)
            return
        super().wheelEvent(e)

    # ---------------------------------------------------------------- editores (perezosos)
    def sync_editores(self, lo: int, hi: int):
        n = len(self.pages)
        for pno in range(max(0, lo - 1), min(n, hi + 2)):
            if pno not in self.con_editores:
                self.crear_editores(pno)
        for pno in list(self.con_editores):
            if pno < lo - 3 or pno > hi + 3:
                self.quitar_editores(pno)

    def crear_editores(self, pno: int):
        if pno in self.con_editores or not 0 <= pno < len(self.pages):
            return
        self.con_editores.add(pno)
        for c in self.por_pagina.get(pno, []):
            if c.tipo not in RELLENABLES or c.id in self.proxies:
                continue
            w = self._editor(c)
            self.editores[c.id] = w
            self.estilo(c.id)          # antes de medir: la hoja de estilo cambia el tamaño mínimo
            proxy = self.scene().addWidget(w)
            proxy.setZValue(Z_EDITOR)
            proxy.setMinimumSize(0, 0)
            proxy.setGeometry(self.rect_escena(c))
            proxy.setData(0, c.id)
            self.proxies[c.id] = proxy

    def quitar_editores(self, pno: int):
        foco = self.campo_con_foco()
        for c in self.por_pagina.get(pno, []):
            if c.id == foco:
                return                          # nunca se quita el editor en el que se escribe
        for c in self.por_pagina.get(pno, []):
            proxy = self.proxies.pop(c.id, None)
            self.editores.pop(c.id, None)
            if proxy is not None:
                self.scene().removeItem(proxy)
                proxy.deleteLater()
        self.con_editores.discard(pno)

    def _editor(self, c: Campo) -> QWidget:
        own = self.owner
        v = own.actuales.get(c.id, c.valor)
        f = QFont("Arial")
        f.setPixelSize(max(1, int(round(_tamano_letra(c)))))
        f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
        if c.tipo == "texto" and not c.multilinea:
            w = QLineEdit()
            w.setFrame(False)
            if c.max_largo:
                w.setMaxLength(c.max_largo)
            if c.clave:
                w.setEchoMode(QLineEdit.EchoMode.Password)
            w.setText(str(v or ""))
            w.setCursorPosition(0)
            w.textChanged.connect(lambda t, cid=c.id: self._bloqueo or own.editado(cid, t))
            w.returnPressed.connect(lambda cid=c.id: own.saltar(cid, 1))
        elif c.tipo == "texto":
            w = QPlainTextEdit()
            w.setFrameShape(QFrame.Shape.NoFrame)
            w.setTabChangesFocus(True)
            w.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            w.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            w.document().setDocumentMargin(1)
            w.setPlainText(str(v or ""))
            w.textChanged.connect(lambda cid=c.id, ed=w: self._texto_multilinea(cid, ed))
        elif c.tipo in ("casilla", "opcion"):
            w = CasillaEditor(c)
            w.setChecked(bool(v))
            w.toggled.connect(lambda on, cid=c.id: self._bloqueo or own.editado(cid, on))
            w.siguiente.connect(lambda cid=c.id: own.saltar(cid, 1))
        elif c.tipo == "lista":
            w = QListWidget()
            w.setFrameShape(QFrame.Shape.NoFrame)
            w.setUniformItemSizes(True)
            w.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            w.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            w.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection if c.multiple
                               else QAbstractItemView.SelectionMode.SingleSelection)
            w.addItems(c.opciones)
            self._mostrar_lista(w, c, v)
            w.itemSelectionChanged.connect(lambda cid=c.id, ed=w: self._lista_cambio(cid, ed))
        else:
            w = QComboBox()
            w.setEditable(c.editable)
            w.addItems(c.opciones)
            w.setPlaceholderText("Elige…")
            self._mostrar_desplegable(w, c, v)
            w.activated.connect(lambda i, cid=c.id, ed=w: self._desplegable_cambio(cid, ed))
            if c.editable:
                w.editTextChanged.connect(lambda _t, cid=c.id, ed=w: self._desplegable_cambio(cid, ed))
        w.setObjectName("CampoEditor")
        w.setFont(f)
        w.setMinimumSize(0, 0)
        w.setToolTip(c.titulo + (" (obligatorio)" if c.obligatorio else ""))
        w.setAccessibleName(c.titulo)
        if c.solo_lectura:
            w.setEnabled(False)
        w.setProperty("campo_id", c.id)
        return w

    def _texto_multilinea(self, cid: str, ed: QPlainTextEdit):
        if self._bloqueo:
            return
        texto = ed.toPlainText()
        c = self.owner.por_id[cid]
        if c.max_largo and len(texto) > c.max_largo:
            self._bloqueo = True
            ed.setPlainText(texto[:c.max_largo])
            cur = ed.textCursor()
            cur.movePosition(cur.MoveOperation.End)
            ed.setTextCursor(cur)
            self._bloqueo = False
            texto = texto[:c.max_largo]
        self.owner.editado(cid, texto)

    def _mostrar_lista(self, w: QListWidget, c: Campo, v):
        elegidos = set(v) if isinstance(v, (list, tuple)) else ({v} if v else set())
        w.blockSignals(True)
        w.clearSelection()
        primero = None
        for i, val in enumerate(c.valores_opciones):
            if val in elegidos:
                w.item(i).setSelected(True)
                primero = primero if primero is not None else i
        if primero is not None:
            w.scrollToItem(w.item(primero))
        w.blockSignals(False)

    def _lista_cambio(self, cid: str, w: QListWidget):
        if self._bloqueo:
            return
        c = self.owner.por_id[cid]
        filas = sorted(w.row(it) for it in w.selectedItems())
        vals = [c.valores_opciones[i] for i in filas if i < len(c.valores_opciones)]
        self.owner.editado(cid, vals if c.multiple else (vals[0] if vals else ""))

    def _mostrar_desplegable(self, w: QComboBox, c: Campo, v):
        w.blockSignals(True)
        v = "" if v is None else str(v)
        if v in c.valores_opciones:
            w.setCurrentIndex(c.valores_opciones.index(v))
        elif c.editable and v:
            w.setCurrentIndex(-1)
            w.setEditText(v)
        else:
            w.setCurrentIndex(-1)
            if c.editable:
                w.setEditText("")
        w.blockSignals(False)

    def _desplegable_cambio(self, cid: str, w: QComboBox):
        if self._bloqueo:
            return
        c = self.owner.por_id[cid]
        if c.editable:
            texto = w.currentText()
            val = c.valores_opciones[c.opciones.index(texto)] if texto in c.opciones else texto
        else:
            i = w.currentIndex()
            val = c.valores_opciones[i] if 0 <= i < len(c.valores_opciones) else ""
        self.owner.editado(cid, val)

    def mostrar_valor(self, cid: str):
        """Pone en el editor (si existe) el valor actual del modelo, sin disparar ediciones."""
        w = self.editores.get(cid)
        if w is None:
            return
        c = self.owner.por_id[cid]
        v = self.owner.actuales.get(cid, c.valor)
        self._bloqueo = True
        try:
            if isinstance(w, QLineEdit):
                if w.text() != str(v or ""):
                    w.setText(str(v or ""))
            elif isinstance(w, QPlainTextEdit):
                if w.toPlainText() != str(v or ""):
                    w.setPlainText(str(v or ""))
            elif isinstance(w, CasillaEditor):
                w.setChecked(bool(v))
                w.update()
            elif isinstance(w, QListWidget):
                self._mostrar_lista(w, c, v)
            elif isinstance(w, QComboBox):
                self._mostrar_desplegable(w, c, v)
        finally:
            self._bloqueo = False
        self.estilo(cid)

    def estilo(self, cid: str):
        w = self.editores.get(cid)
        if w is None or isinstance(w, CasillaEditor):
            if w is not None:
                w.update()
            return
        c = self.owner.por_id[cid]
        clase = type(w).__name__
        css = _qss(clase, c, not _lleno(c, self.owner.actuales.get(cid, c.valor)))
        if w.styleSheet() != css:
            w.setStyleSheet(css)

    # ---------------------------------------------------------------- foco y Tab
    def campo_con_foco(self) -> str | None:
        it = self.scene().focusItem()
        if isinstance(it, QGraphicsProxyWidget):
            return it.data(0)
        return None

    def _foco_cambio(self, nuevo, _viejo, _razon):
        cid = nuevo.data(0) if isinstance(nuevo, QGraphicsProxyWidget) else None
        self.owner.foco_en(cid)

    def enfocar(self, cid: str, seleccionar: bool = True) -> bool:
        c = self.owner.por_id.get(cid)
        if c is None or not 0 <= c.pagina < len(self.pages):
            return False
        self.crear_editores(c.pagina)
        rect = self.rect_escena(c)
        self.ensureVisible(rect, 60, 90)
        proxy = self.proxies.get(cid)
        if proxy is None:
            self.destacar(rect)
            return False
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.scene().setFocusItem(proxy, Qt.FocusReason.TabFocusReason)
        w = self.editores[cid]
        w.setFocus(Qt.FocusReason.TabFocusReason)
        if seleccionar and isinstance(w, QLineEdit):
            w.selectAll()
        return True

    def destacar(self, rect: QRectF):
        it = QGraphicsRectItem(rect.adjusted(-3, -3, 3, 3))
        pen = QPen(QColor(_pag("cat-ver")), 2)
        pen.setCosmetic(True)
        it.setPen(pen)
        it.setBrush(Qt.BrushStyle.NoBrush)
        it.setZValue(Z_DESTACADO)
        self.scene().addItem(it)
        QTimer.singleShot(1400, lambda: it.scene() is not None and it.scene().removeItem(it))

    def event(self, e):
        if e.type() == QEvent.Type.KeyPress and e.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab) \
                and not (e.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)) \
                and self.doc is not None and not self._editando_texto():
            atras = e.key() == Qt.Key.Key_Backtab or bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            if self.owner.saltar(self.campo_con_foco(), -1 if atras else 1):
                return True
        return super().event(e)

    def _editando_texto(self) -> bool:
        it = self.scene().focusItem()
        return isinstance(it, TextItem) and bool(it.textInteractionFlags() & Qt.TextInteractionFlag.TextEditorInteraction)

    # ---------------------------------------------------------------- herramientas y ratón
    def set_tool(self, tool: str):
        self.tool = tool
        if tool == "rellenar":
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self.viewport().unsetCursor()
        else:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            self.viewport().setCursor(Qt.CursorShape.IBeamCursor if tool in ("texto", "fecha")
                                      else Qt.CursorShape.CrossCursor)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.doc is not None and self.tool != "rellenar":
            pos = e.position().toPoint()
            hit = self.itemAt(pos)
            if _es_sello(hit):                 # un sello que ya está: se selecciona o se mueve
                super().mousePressEvent(e)
                return
            cid = hit.data(0) if isinstance(hit, QGraphicsProxyWidget) else None
            if cid and self.owner.usar_en_campo(self.tool, cid):
                return                         # la herramienta se aplicó al campo (marcar, fecha, escribir)
            sp = self.mapToScene(pos)
            page = self.page_at(sp)
            if page is None:
                return
            self.scene().clearSelection()
            local = sp - page.pos()
            self.owner.colocar(self.tool, page.index, local.x(), local.y())
            return
        super().mousePressEvent(e)

    def keyPressEvent(self, e):
        if self.campo_con_foco() is None and not self._editando_texto():
            if e.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                self.owner.borrar_seleccion()
                return
            if e.key() == Qt.Key.Key_Escape:
                self.scene().clearSelection()
                self.owner.set_tool("rellenar")
                return
        elif e.key() == Qt.Key.Key_Escape and self._editando_texto():
            self.scene().focusItem().clearFocus()
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
# Panel: lista de campos y firmas guardadas
# --------------------------------------------------------------------------

class _DelegadoCampos(QStyledItemDelegate):
    """Fila de la lista de campos: estado (lleno, obligatorio, pendiente, firma), título y una línea de datos."""

    ALTO = 46

    @staticmethod
    def _color(estado: str) -> str:
        t = tema.tokens()
        return {"lleno": t["ok"], "obligatorio": t["danger"], "pendiente": t["line-control"],
                "solo": t["line-control"], "firma": t["info"], "firmada": t["ok"],
                "firmado": t["ok"]}.get(estado, t["line-control"])

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), self.ALTO)

    def paint(self, p: QPainter, option, index):
        t = tema.tokens()
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(option.rect).adjusted(0, 1, 0, -1)
        if option.state & QStyle.StateFlag.State_Selected:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(tema.tonos(cat_de(self.parent()))["band"]))
            p.drawRoundedRect(r, 8, 8)
        elif option.state & QStyle.StateFlag.State_MouseOver:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t["surface-hover"]))
            p.drawRoundedRect(r, 8, 8)
        estado = index.data(ROL_ESTADO) or "pendiente"
        color = QColor(self._color(estado))
        c = QPointF(r.left() + 14, r.center().y())
        if estado in ("lleno", "firmada", "firmado"):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawEllipse(c, 7, 7)
            pen = QPen(QColor(t["on-cat"]), 1.6)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawPolyline([c + QPointF(-3.2, 0.2), c + QPointF(-0.8, 2.6), c + QPointF(3.4, -2.4)])
        elif estado == "obligatorio":
            p.setPen(QPen(color, 1.6))
            p.setBrush(QColor(t["danger-tint"]))
            p.drawEllipse(c, 6.5, 6.5)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawEllipse(c, 2.4, 2.4)
        elif estado == "firma":
            p.setPen(QPen(color, 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(c, 6.5, 6.5)
            p.drawLine(c + QPointF(-3, 2.5), c + QPointF(3, -2.5))
        else:
            pen = QPen(color, 1.4)
            if estado == "solo":
                pen.setStyle(Qt.PenStyle.DotLine)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(c, 6.5, 6.5)
        texto = QRectF(r.left() + 30, r.top() + 5, r.width() - 36, r.height() - 10)
        f = tema.fuente("body-sm", option.font)
        f.setWeight(f.Weight.DemiBold if estado == "obligatorio" else f.Weight.Medium)
        p.setFont(f)
        p.setPen(QColor(t["ink"] if estado != "solo" else t["ink-3"]))
        titulo = p.fontMetrics().elidedText(index.data(ROL_TITULO) or "", Qt.TextElideMode.ElideRight, int(texto.width()))
        p.drawText(texto, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, titulo)
        p.setFont(tema.fuente("caption", option.font))
        p.setPen(QColor(t["danger"] if estado == "obligatorio" else t["ink-3"]))
        sub = p.fontMetrics().elidedText(index.data(ROL_SUB) or "", Qt.TextElideMode.ElideRight, int(texto.width()))
        p.drawText(texto, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, sub)
        p.restore()


class _TarjetaFirma(QAbstractButton):
    """Una firma guardada: su miniatura sobre blanco, en una ficha que se elige con un clic."""

    def __init__(self, ruta, numero: int):
        super().__init__()
        self.ruta = str(ruta)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setToolTip(f"Firma {numero}: elígela y haz clic en la página donde va")
        self.setAccessibleName(f"Firma {numero}")
        self._pm = firmas.miniatura(ruta, 112, 40)
        self.setFixedHeight(52)
        self.setMinimumWidth(80)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        return QSize(120, 52)

    def paintEvent(self, _e):
        t = tema.tokens()
        ton = tema.tonos(cat_de(self))
        on = self.isChecked()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(0.75, 0.75, -0.75, -0.75)
        p.setPen(QPen(QColor(ton["c"] if on else t["line-strong"] if self.underMouse() else t["line"]),
                      1.5 if on else 1))
        p.setBrush(QColor(_pag("surface")))          # la firma va sobre papel blanco, como en la página
        p.drawRoundedRect(r, 8, 8)
        dpr = self._pm.devicePixelRatio() or 1
        w, h = self._pm.width() / dpr, self._pm.height() / dpr
        s = min(1.0, (r.width() - 12) / w, (r.height() - 8) / h)
        dest = QRectF(r.center().x() - w * s / 2, r.center().y() - h * s / 2, w * s, h * s)
        p.drawPixmap(dest, self._pm, QRectF(self._pm.rect()))
        if self.hasFocus() and self.focusPolicy() != Qt.FocusPolicy.NoFocus:
            p.setPen(QPen(QColor(t["focus"]), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 7, 7)
        p.end()


class _NumeroPagina(QLineEdit):
    """Número de página de la barra del lienzo: un campo corto y sin borde (con la API de QSpinBox que se usa)."""

    def __init__(self):
        super().__init__()
        self._max = 1
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setValidator(QIntValidator(1, 1, self))
        self.setStyleSheet("QLineEdit, QLineEdit:focus { padding: 2px 2px; border: none; min-height: 0px; "
                           "background: transparent; }")
        self.setText("1")

    def setRange(self, lo: int, hi: int):  # noqa: N802 — como QSpinBox
        self._max = max(lo, hi)
        self.validator().setRange(lo, self._max)

    def setValue(self, v: int):  # noqa: N802
        self.setText(str(max(1, min(int(v), self._max))))

    def value(self) -> int:
        try:
            return max(1, min(int(self.text()), self._max))
        except ValueError:
            return 1


def _grupo(*widgets: QWidget) -> QFrame:
    """Grupo de botones de la barra del lienzo (ficha blanca redondeada)."""
    box = QFrame()
    box.setObjectName("Card")
    lay = QHBoxLayout(box)
    lay.setContentsMargins(4, 2, 4, 2)
    lay.setSpacing(2)
    for w in widgets:
        lay.addWidget(w)
    return box


def _caja(*widgets: QWidget, espacio: int = 12, izquierda: bool = False) -> QWidget:
    """Columna de controles; con ``izquierda`` cada uno conserva su ancho y queda a la izquierda."""
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(espacio)
    for x in widgets:
        if izquierda:
            v.addWidget(x, 0, Qt.AlignmentFlag.AlignLeft)
        else:
            v.addWidget(x)
    return w


# --------------------------------------------------------------------------
# Página
# --------------------------------------------------------------------------

INTRO = {   # la ayuda de cada paso mientras no hay documento: cuenta qué se puede hacer
    "campos": "Haz clic en cada campo de la página y escribe. Tab pasa al siguiente.",
    "sellos": "En formatos planos o escaneados: texto, equis, chulo y fecha.",
    "firma": "Dibújala, escríbela o tómala de una foto. Queda guardada en tu equipo.",
    "guardar": "Nada sale a internet. Antes de reemplazar el archivo se guarda una copia de respaldo.",
}


class FormsPage(ToolPage):
    """Plantilla 2.0 con forma de editor: la página con sus campos en el área principal; la lista de campos, las
    herramientas para escribir encima, la firma y las opciones de guardado en los pasos del panel; «Guardar
    formulario» como única acción principal."""

    key = "forms"
    title = "Formularios y firma"
    subtitle = "Rellena campo por campo y firma sin imprimir."
    icon_name = "forms"
    action_text = "Guardar formulario"

    def build(self):
        self.path: str | None = None
        self.password: str | None = None
        self.doc = None
        self.campos: list[Campo] = []
        self.por_id: dict[str, Campo] = {}
        self.por_nombre: dict[str, list[str]] = defaultdict(list)
        self.navegables: list[str] = []
        self.actuales: dict[str, object] = {}
        self.originales: dict[str, object] = {}
        self.sellos: list[QGraphicsItem] = []
        self.undo_stack: list[tuple] = []
        self.redo_stack: list[tuple] = []            # lo deshecho, para «Rehacer»
        self._disco: tuple[int, int] | None = None   # tamaño y fecha del archivo al abrirlo
        self.info = formularios.InfoFormulario(0, False, False, False)
        self.escaneado = False
        self.tamano = "mediano"
        self.firma_png: bytes | None = None
        self._firma_ruta: str | None = None          # la firma guardada que se está usando (si es una)
        self._almacen: firmas.AlmacenFirmas | None = None
        self._sesion = 0
        self._ultimo_undo = None
        self.campo_actual: str | None = None
        self.textos_avisos: list[str] = []
        self._mensaje = ""                           # aviso pasajero en la barra de acción
        self._resumen_actual = None

        self.canvas = FormCanvas(self)
        self._panel()
        self.set_main(self._area_principal())

        # Encabezado: el documento abierto, con «Cambiar»
        self.ficha = FileChip()
        self.ficha.change_clicked.connect(self.abrir_dialogo)
        self.ficha.b_change.setToolTip("Abrir otro formulario o documento (Ctrl+O)")
        self.ficha.hide()
        self.set_header_widget(self.ficha)

        # Barra de acción: «Cambios sin guardar», «Guardar como…» y el principal
        self.unsaved = Chip("Cambios sin guardar", "warn", dot=True)
        self.unsaved.setToolTip("Hay cambios sin guardar")
        self.summary.layout().insertWidget(0, self.unsaved)
        self.unsaved.hide()
        self.b_como = self.add_secondary("Guardar como…", lambda: self.save())
        self.b_como.setToolTip("Guardar una copia con otro nombre (Ctrl+Mayús+S)")
        self.footer_info.setWordWrap(True)

        self.canvas.zoom_changed.connect(lambda z: self.zoom_btn.setText(f"{z * 100:.0f} %"))
        self.canvas.page_changed.connect(self._pagina_cambio)
        self.canvas.files_dropped.connect(self.accept_files)
        self.canvas.scene().selectionChanged.connect(self._actualizar_botones)

        self._refresco = QTimer(self)
        self._refresco.setSingleShot(True)
        self._refresco.setInterval(120)
        self._refresco.timeout.connect(self.refrescar)

        for seq, fn in (("Ctrl+S", self._guardar_principal), ("Ctrl+Shift+S", lambda: self.save()),
                        ("Ctrl+O", self.abrir_dialogo), ("Ctrl+Z", self.deshacer), ("Ctrl+Y", self.rehacer),
                        ("Ctrl+Shift+Z", self.rehacer),
                        ("Ctrl++", lambda: self.canvas.zoom_by(1.2)), ("Ctrl+=", lambda: self.canvas.zoom_by(1.2)),
                        ("Ctrl+-", lambda: self.canvas.zoom_by(1 / 1.2)),
                        ("Ctrl+0", lambda: self.canvas.set_fit("width"))):
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)
        self._mostrar_tamano()
        self.set_tool("rellenar")

    # ------------------------------------------------------------------ construcción
    def _panel(self):
        # Avisos del documento (formulario antiguo, firma digital, cálculos, sin campos), arriba de los pasos
        self.avisos = QWidget()
        self.avisos_lay = QVBoxLayout(self.avisos)
        self.avisos_lay.setContentsMargins(0, 0, 0, 0)
        self.avisos_lay.setSpacing(8)
        lay = self.step(None)
        lay.addWidget(self.avisos)
        self.bloque_avisos = lay.parentWidget()
        self.bloque_avisos.hide()

        # 1. Campos
        lay = self.step("Rellena los campos", INTRO["campos"])
        self.paso_campos = lay.parentWidget()
        self.resumen = self.paso_campos.help_lb
        self.chip = Chip("", "cat")
        self.chip.hide()
        self.paso_campos.add_head_widget(self.chip)
        self.chk_pendientes = SwitchRow("Ver solo los que faltan")
        self.chk_pendientes.setToolTip("Muestra solo los campos que todavía están vacíos")
        self.chk_pendientes.toggled.connect(lambda _on: self.refrescar())
        self.vacio_lb = label("", "Help", wrap=True)
        self.lista = QListWidget()
        self.lista.setObjectName("ListaCampos")
        self.lista.setItemDelegate(_DelegadoCampos(self.lista))
        self.lista.setMouseTracking(True)
        self.lista.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.lista.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.lista.setToolTip("Haz clic en un campo para ir a él. En la página, Tab pasa al siguiente.")
        self.lista.itemClicked.connect(self._campo_elegido)
        self.lista.itemActivated.connect(self._campo_elegido)
        self.ctl_campos = _caja(self.chk_pendientes, self.vacio_lb, self.lista, espacio=8)
        lay.addWidget(self.ctl_campos)

        # 2. Escribir o marcar encima: cuatro herramientas en una cuadrícula de segmentos y el tamaño
        lay = self.step("Escribe o marca encima", INTRO["sellos"])
        self.paso_sellos = lay.parentWidget()
        rejilla = QFrame()
        rejilla.setObjectName("Segmented")
        g = QGridLayout(rejilla)
        g.setContentsMargins(3, 3, 3, 3)
        g.setSpacing(2)
        self.tool_buttons: dict[str, QToolButton] = {}
        for k, (clave, texto, ic) in enumerate(HERRAMIENTAS[1:]):
            b = QToolButton()
            b.setObjectName("SegItem")
            b.setText(texto)
            b.setIcon(icon(ic, tema.color("ink-2"), tema.color("ink"), 16))
            b.setIconSize(QSize(16, 16))
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            b.setToolTip(AYUDA[clave])
            b.setAccessibleName(texto)
            b.clicked.connect(lambda on, k=clave: self.set_tool(k if on else "rellenar"))
            self.tool_buttons[clave] = b
            g.addWidget(b, k // 2, k % 2)
        self.c_tamano = QComboBox()
        for clave in TAMANOS:
            self.c_tamano.addItem(NOMBRE_TAMANO[clave], clave)
        self.c_tamano.setToolTip("Tamaño de los textos, marcas y firmas nuevos (y del que esté seleccionado)")
        self.c_tamano.currentIndexChanged.connect(lambda _i: self.set_tamano(self.c_tamano.currentData()))
        self.ctl_sellos = _caja(rejilla, _caja(label("Tamaño", "Label"), self.c_tamano, espacio=6))
        lay.addWidget(self.ctl_sellos)

        # 3. Firma: las guardadas, como fichas; crear una nueva o administrarlas
        lay = self.step("Pon tu firma", INTRO["firma"])
        self.paso_firma = lay.parentWidget()
        self.rejilla_firmas = QWidget()
        self.rejilla_firmas_lay = QGridLayout(self.rejilla_firmas)
        self.rejilla_firmas_lay.setContentsMargins(0, 0, 0, 0)
        self.rejilla_firmas_lay.setSpacing(8)
        self.tarjetas_firma: list[_TarjetaFirma] = []
        self.b_nueva_firma = QPushButton("Nueva firma…")
        self.b_nueva_firma.setObjectName("Secondary")
        self.b_nueva_firma.setProperty("tam", "sm")
        self.b_nueva_firma.setIcon(icon("add", tema.color("ink-2"), size=16))
        self.b_nueva_firma.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_nueva_firma.clicked.connect(self.nueva_firma)
        self.b_firmas = QPushButton("Administrar")
        self.b_firmas.setObjectName("Ghost")
        self.b_firmas.setToolTip("Ver y borrar las firmas guardadas en este equipo")
        self.b_firmas.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_firmas.clicked.connect(self.administrar_firmas)
        fila = QHBoxLayout()
        fila.setSpacing(8)
        fila.addWidget(self.b_nueva_firma)
        fila.addWidget(self.b_firmas)
        fila.addStretch(1)
        botones = QWidget()
        botones.setLayout(fila)
        self.ctl_firma = _caja(self.rejilla_firmas, botones, espacio=10)
        lay.addWidget(self.ctl_firma)

        # 4. Guardar: aplanar y, plegados, los datos del formulario
        lay = self.step("Al guardar", INTRO["guardar"])
        self.paso_guardar = lay.parentWidget()
        self.chk_aplanar = SwitchRow("Aplanar el formulario", "Nadie podrá cambiar lo escrito. Se guarda como copia.")
        self.chk_aplanar.setToolTip("Convierte el formulario en un PDF normal: nadie podrá cambiar lo que escribiste")
        self.chk_aplanar.toggled.connect(self._aplanar_cambio)
        self.b_datos = QPushButton("Datos del formulario")
        self.b_datos.setObjectName("Ghost")
        self.b_datos.setCheckable(True)
        self.b_datos.setIcon(icon("chevron_down", tema.color("ink-2"), size=14))
        self.b_datos.setToolTip("Vaciar el formulario, o pasar sus datos a Excel (CSV) o JSON y traerlos de vuelta")
        self.b_datos.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_datos.toggled.connect(self._ver_datos)
        self.b_vaciar = self._boton_datos("Vaciar formulario", "eraser", self.vaciar_formulario)
        self.b_exportar = self._boton_datos("Exportar datos…", "save", self.exportar_datos)
        self.b_importar = self._boton_datos("Importar datos…", "open", self.importar_datos)
        self.datos = _caja(self.b_vaciar, self.b_exportar, self.b_importar, espacio=2, izquierda=True)
        self.datos.hide()
        self.caja_datos = _caja(self.b_datos, self.datos, espacio=4, izquierda=True)
        self.ctl_guardar = _caja(self.chk_aplanar, self.caja_datos)
        lay.addWidget(self.ctl_guardar)
        self._controles = [self.ctl_campos, self.ctl_sellos, self.ctl_firma, self.ctl_guardar]

    def _boton_datos(self, texto: str, ic: str, fn) -> QPushButton:
        b = QPushButton(texto)
        b.setObjectName("Ghost")
        b.setIcon(icon(ic, tema.color("ink-2"), size=16))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(fn)
        return b

    def _ver_datos(self, on: bool):
        self.datos.setVisible(on)
        self.b_datos.setIcon(icon("up_chevron" if on else "chevron_down", tema.color("ink-2"), size=14))

    def _area_principal(self) -> QWidget:
        self.zona = DropArea("Arrastra aquí el formulario o el documento que quieres firmar",
                             "PDF con campos, formatos planos o escaneados, y también fotos de documentos.",
                             core.PDF_EXTS | core.IMAGE_EXTS, tile_key=self.key, zone=True,
                             button_text="Elegir archivo…")
        self.zona.clicked.connect(self.abrir_dialogo)
        self.zona.files_dropped.connect(self.accept_files)

        editor = QWidget()
        v = QVBoxLayout(editor)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        barra = QWidget()
        bl = QHBoxLayout(barra)
        bl.setContentsMargins(12, 10, 12, 8)
        bl.setSpacing(8)
        self.b_undo = _icon_button("undo", "Deshacer (Ctrl+Z)", self.deshacer)
        self.b_redo = _icon_button("redo", "Rehacer (Ctrl+Y)", self.rehacer)
        self.b_delete = _icon_button("delete", "Borrar lo seleccionado (Supr)", self.borrar_seleccion)
        bl.addWidget(_grupo(self.b_undo, self.b_redo, self.b_delete))
        bl.addStretch(1)
        self.b_prev = _seg_button("", "prev", "Página anterior", checkable=False)
        self.b_prev.clicked.connect(lambda: self.canvas.go_to_page(self.canvas.current_page - 1))
        self.page_spin = _NumeroPagina()
        self.page_spin.setRange(1, 1)
        self.page_spin.setFixedWidth(40)
        self.page_spin.setToolTip("Escribe un número de página y pulsa Enter")
        self.page_spin.setAccessibleName("Página")
        self.page_spin.editingFinished.connect(lambda: self.canvas.go_to_page(self.page_spin.value() - 1))
        self.page_total = label("de 0", "BodySm")
        self.b_next = _seg_button("", "next", "Página siguiente", checkable=False)
        self.b_next.clicked.connect(lambda: self.canvas.go_to_page(self.canvas.current_page + 1))
        bl.addWidget(_grupo(self.b_prev, self.page_spin, self.page_total, self.b_next))
        b_out = _seg_button("", "minus", "Alejar (Ctrl+−)", checkable=False)
        b_out.clicked.connect(lambda: self.canvas.zoom_by(1 / 1.2))
        self.zoom_btn = QToolButton()
        self.zoom_btn.setObjectName("SegButton")
        self.zoom_btn.setText("100 %")
        self.zoom_btn.setMinimumWidth(58)
        self.zoom_btn.setToolTip("Zoom (Ctrl+0: ajustar al ancho)")
        self.zoom_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.zoom_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        zmenu = QMenu(self.zoom_btn)
        for text, val in (("Ajustar al ancho", "width"), ("75 %", 0.75), ("100 %", 1.0), ("125 %", 1.25),
                          ("150 %", 1.5), ("200 %", 2.0), ("300 %", 3.0)):
            zmenu.addAction(text, lambda v_=val: self.canvas.set_fit(v_) if isinstance(v_, str)
                            else self.canvas.set_zoom(v_))
        self.zoom_btn.setMenu(zmenu)
        b_in = _seg_button("", "add", "Acercar (Ctrl++)", checkable=False)
        b_in.clicked.connect(lambda: self.canvas.zoom_by(1.2))
        bl.addWidget(_grupo(b_out, self.zoom_btn, b_in))
        v.addWidget(barra)
        lienzo = QWidget()
        ll = QVBoxLayout(lienzo)
        ll.setContentsMargins(6, 0, 6, 8)          # las esquinas redondeadas del pozo quedan a la vista
        ll.addWidget(self.canvas)
        v.addWidget(lienzo, 1)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.zona)
        self.stack.addWidget(editor)
        return self.stack

    def _mostrar_tamano(self):
        self.c_tamano.blockSignals(True)
        self.c_tamano.setCurrentIndex(max(0, self.c_tamano.findData(self.tamano)))
        self.c_tamano.blockSignals(False)

    # ------------------------------------------------------------------ contrato de la plantilla
    def ready(self) -> bool:
        return getattr(self, "doc", None) is not None

    def run(self):
        self._guardar_principal()

    def refresh(self):
        super().refresh()
        if hasattr(self, "_controles"):
            self.refrescar()

    # ------------------------------------------------------------------ abrir
    def accept_files(self, paths: list[str]):
        for p in paths:
            if Path(p).suffix.lower() in core.PDF_EXTS or core.is_image(p):
                self.load(p)
                return

    def abrir_dialogo(self):
        paths = ask_open(self, False, PDF_AND_IMAGES_FILTER, "Abrir formulario o documento")
        if paths:
            self.load(paths[0])

    def _confirmar_descartar(self) -> bool:
        """Antes de abrir otro documento: «Guardar», «No guardar» o «Cancelar».  True si se puede seguir."""
        if not self.has_unsaved():
            return True
        nombre = Path(self.path).name if self.path else "el formulario"
        r = guardado_ui.preguntar_cambios(self, "Cambios sin guardar",
                                          f"¿Quieres guardar los cambios de «{nombre}» antes de abrir otro "
                                          "documento?")
        if r == "guardar":
            return guardado_ui.guardar_y_esperar(self)
        if r == "descartar":
            guardado_ui.trabajo_descartado(self, "forms")
            return True
        return False

    def _cerrar_doc(self):
        if self.doc is not None:
            with PDF_LOCK:
                if not self.doc.is_closed:
                    self.doc.close()
            self.doc = None
            self.canvas.doc = None

    def load(self, path: str, confirm: bool = True) -> bool:
        path = str(path)
        if Path(path).suffix.lower() not in core.PDF_EXTS and not core.is_image(path):
            QMessageBox.warning(self, "Abrir", "Elige un PDF o una foto (JPG o PNG) del documento.")
            return False
        if confirm and not self._confirmar_descartar():
            return False
        if not ctx.passwords.ensure(path, self):
            return False
        pw = ctx.passwords.get(path)
        aviso_lectura = False
        try:
            with PDF_LOCK:
                doc = core.open_pdf(path, pw)
                try:
                    campos = formularios._leer(doc)
                    info = formularios._info(doc)
                except Exception:  # noqa: BLE001 — un formulario raro no debe impedir escribir encima
                    campos, info = [], formularios.InfoFormulario(0, False, False, False)
                    aviso_lectura = True
                escaneado = core.looks_scanned(doc)
        except core.UserError as exc:
            QMessageBox.warning(self, "No se pudo abrir", str(exc))
            return False
        self._cerrar_doc()
        self.doc, self.path, self.password = doc, path, pw
        self.campos = campos
        self.por_id = {c.id: c for c in campos}
        self.por_nombre = defaultdict(list)
        for c in campos:
            if c.tipo in RELLENABLES:
                self.por_nombre[c.nombre].append(c.id)
        self.navegables = [c.id for c in campos if c.tipo in RELLENABLES and not c.solo_lectura]
        self.actuales = {c.id: copy.deepcopy(c.valor) for c in campos if c.tipo in RELLENABLES}
        self.originales = copy.deepcopy(self.actuales)
        self.sellos, self.undo_stack, self.redo_stack = [], [], []
        self._disco = guardado.firma_archivo(path)
        self._ultimo_undo = None
        self._mensaje = ""
        self.campo_actual = None
        self.info, self.escaneado = info, escaneado
        self.canvas.cargar(doc, campos)
        n = self.canvas.page_count
        try:
            peso = core.human_size(os.path.getsize(path))
        except OSError:
            peso = ""
        tipo = "Foto" if core.is_image(path) else _plural(n, "página", "páginas")
        self.ficha.set_file(Path(path).name, " · ".join(x for x in (tipo, peso) if x), path)
        self.page_total.setText(f"de {n}")
        self.page_spin.setRange(1, max(1, n))
        if not campos and self.chk_aplanar.isChecked():   # sin campos no hay nada que aplanar
            self.chk_aplanar.blockSignals(True)
            self.chk_aplanar.setChecked(False)
            self.chk_aplanar.blockSignals(False)
        self._llenar_panel()
        self._llenar_firmas()
        self._mostrar_avisos(aviso_lectura)
        self.set_tool("rellenar")
        self.stack.setCurrentIndex(1)
        ctx.remember_dir(path)
        ctx.add_recent(path, n)
        self.refrescar()
        self.canvas.setFocus()
        return True

    def _mostrar_avisos(self, aviso_lectura: bool = False):
        while self.avisos_lay.count():
            it = self.avisos_lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        textos = []
        if self.info.xfa:
            textos.append(AVISOS["xfa"])
        if self.info.firmado:
            textos.append(AVISOS["firmado"])
        if self.info.calculos:
            textos.append(AVISOS["calculos"])
        if aviso_lectura:
            textos.append("No se pudieron leer los campos de este formulario. Puedes escribir encima con las "
                          "herramientas.")
        elif not [c for c in self.campos if c.tipo in RELLENABLES]:
            textos.append(AVISOS["sin_campos"])
        self.textos_avisos = textos
        for t in textos:
            self.avisos_lay.addWidget(Note(t, "info" if t == AVISOS["sin_campos"] else "warn"))
        self.bloque_avisos.setVisible(bool(textos))

    # ------------------------------------------------------------------ modelo de valores
    def valores(self) -> dict[str, object]:
        return copy.deepcopy(self.actuales)

    def _guardar_deshacer(self, cid: str, agrupar: bool):
        c = self.por_id.get(cid)
        if c is None:
            return
        self.redo_stack.clear()                # un cambio nuevo invalida lo que se podía rehacer
        clave = (c.nombre, self._sesion)
        if agrupar and self._ultimo_undo == clave:
            return
        foto = {x: copy.deepcopy(self.actuales.get(x)) for x in self.por_nombre.get(c.nombre, [cid])}
        self.undo_stack.append(("valores", foto))
        self._ultimo_undo = clave if agrupar else None

    def _asignar(self, cid: str, valor, desde: str | None = None):
        c = self.por_id[cid]
        grupo = self.por_nombre.get(c.nombre, [cid])
        if c.tipo in ("casilla", "opcion"):
            if valor:
                estado = c.estado
            elif self.actuales.get(cid):
                estado = "Off"
            else:
                return
            for oid in grupo:
                o = self.por_id[oid]
                self.actuales[oid] = estado != "Off" and o.estado == estado
            for oid in grupo:
                self.canvas.mostrar_valor(oid)
            return
        if c.tipo == "texto" and c.max_largo and isinstance(valor, str):
            valor = valor[:c.max_largo]
        for oid in grupo:
            self.actuales[oid] = copy.deepcopy(valor)
            if oid != desde:
                self.canvas.mostrar_valor(oid)
            else:
                self.canvas.estilo(oid)

    def editado(self, cid: str, valor):
        """Un editor cambió (lo llama el lienzo)."""
        if cid not in self.por_id or self.por_id[cid].solo_lectura:
            return
        if self.actuales.get(cid) == valor:
            return
        self._guardar_deshacer(cid, agrupar=True)
        self._asignar(cid, valor, desde=cid)
        self._cambio()

    def set_valor(self, campo_id: str, valor):
        """Cambia el valor de un campo (y de los que comparten su nombre)."""
        c = self.por_id.get(campo_id)
        if c is None or c.tipo not in RELLENABLES or c.solo_lectura:
            return
        self._guardar_deshacer(campo_id, agrupar=False)
        self._asignar(campo_id, valor)
        self._cambio()

    def _cambio(self):
        self._mensaje = ""                     # un cambio nuevo deja atrás el aviso anterior
        self._actualizar_botones()
        self._refresco.start()

    def _avisar(self, texto: str):
        """Aviso pasajero en la frase de la barra de acción (hasta el próximo cambio)."""
        self._mensaje = texto
        self._actualizar_botones()

    def foco_en(self, cid: str | None):
        if cid != self.campo_actual:
            self._sesion += 1
        self.campo_actual = cid
        if cid is None:
            return
        c = self.por_id.get(cid)
        nombre = c.nombre if c else None
        self.lista.blockSignals(True)
        for i in range(self.lista.count()):
            it = self.lista.item(i)
            iid = it.data(ROL_ID)
            if iid == cid or (nombre and self.por_id.get(iid) and self.por_id[iid].nombre == nombre
                              and self.por_id[iid].tipo in RELLENABLES):
                self.lista.setCurrentItem(it)
                self.lista.scrollToItem(it)
                break
        self.lista.blockSignals(False)

    def saltar(self, cid: str | None, paso: int) -> bool:
        """Lleva el foco al campo siguiente (paso 1) o anterior (−1) en el orden del documento."""
        if not self.navegables:
            return False
        if cid in self.navegables:
            k = (self.navegables.index(cid) + paso) % len(self.navegables)
        else:
            k = 0 if paso > 0 else len(self.navegables) - 1
        return self.canvas.enfocar(self.navegables[k])

    def deshacer(self):
        foco = self.canvas.scene().focusItem()
        if isinstance(foco, TextItem) and foco.textInteractionFlags() & Qt.TextInteractionFlag.TextEditorInteraction:
            foco.document().undo()
            return
        while self.undo_stack:
            accion = self.undo_stack.pop()
            efecto = False
            if accion[0] == "valores":
                antes = {cid: copy.deepcopy(self.actuales.get(cid)) for cid in accion[1]}
                for cid, v in accion[1].items():
                    efecto = efecto or self.actuales.get(cid) != v
                    self.actuales[cid] = v
                    self.canvas.mostrar_valor(cid)
                if efecto:
                    self.redo_stack.append(("valores", antes))
            elif accion[0] == "sello+":
                for it in accion[1]:
                    if it.scene() is not None:
                        self.canvas.scene().removeItem(it)
                        efecto = True
                if efecto:
                    self.redo_stack.append(accion)
            elif accion[0] == "sello-":
                for it in accion[1]:
                    if it.scene() is None:
                        self.canvas.scene().addItem(it)
                        efecto = True
                if efecto:
                    self.redo_stack.append(accion)
            if efecto:                         # las acciones que ya no cambian nada se saltan
                break
        self._ultimo_undo = None
        self._cambio()

    def rehacer(self):
        """Vuelve a hacer lo último que se deshizo: valores, sellos agregados o sellos quitados."""
        foco = self.canvas.scene().focusItem()
        if isinstance(foco, TextItem) and foco.textInteractionFlags() & Qt.TextInteractionFlag.TextEditorInteraction:
            foco.document().redo()
            return
        while self.redo_stack:
            accion = self.redo_stack.pop()
            efecto = False
            if accion[0] == "valores":
                antes = {cid: copy.deepcopy(self.actuales.get(cid)) for cid in accion[1]}
                for cid, v in accion[1].items():
                    efecto = efecto or self.actuales.get(cid) != v
                    self.actuales[cid] = copy.deepcopy(v)
                    self.canvas.mostrar_valor(cid)
                if efecto:
                    self.undo_stack.append(("valores", antes))
            elif accion[0] == "sello+":
                for it in accion[1]:
                    if it.scene() is None:
                        self.canvas.scene().addItem(it)
                        efecto = True
                if efecto:
                    self.undo_stack.append(accion)
            elif accion[0] == "sello-":
                for it in accion[1]:
                    if it.scene() is not None:
                        self.canvas.scene().removeItem(it)
                        efecto = True
                if efecto:
                    self.undo_stack.append(accion)
            if efecto:
                break
        self._ultimo_undo = None
        self._cambio()

    # ------------------------------------------------------------------ sellos
    def set_tool(self, tool: str):
        if tool == "firma" and not self.firma_png:
            tool = "rellenar"
        self.canvas.set_tool(tool)
        for k, b in self.tool_buttons.items():
            b.setChecked(k == tool)
        for t in self.tarjetas_firma:
            t.setChecked(tool == "firma" and t.ruta == self._firma_ruta)
        self._ayudas()

    def _ayudas(self):
        """La línea de ayuda de los pasos 2 y 3: qué hacer con la herramienta elegida."""
        if self.doc is None and not self.canvas.pages:
            self.paso_sellos.set_help(INTRO["sellos"])
            self.paso_firma.set_help(INTRO["firma"])
            return
        tool = self.canvas.tool
        self.paso_sellos.set_help(AYUDA[tool] if tool in ("texto", "equis", "chulo", "fecha") else
                                  "Elige una herramienta y haz clic sobre la página.")
        if tool == "firma":
            self.paso_firma.set_help(AYUDA["firma"])
        elif self.tarjetas_firma:
            self.paso_firma.set_help("Elige una firma y haz clic en la página donde va.")
        else:
            self.paso_firma.set_help("Créala una vez y queda guardada para el próximo documento.")

    def set_tamano(self, tamano: str):
        self.tamano = tamano
        self._mostrar_tamano()
        t = TAMANOS[tamano]
        for it in self.canvas.scene().selectedItems():
            if isinstance(it, SelloTexto):
                it.set_tamano(t["texto"])
            elif isinstance(it, MarcaSello):
                it.set_tamano(t["marca"])
            elif isinstance(it, ShapeItem) and getattr(it, "sello", "") == "firma" and it.r.width() > 0:
                c = it.mapToScene(it.r.center())
                h = it.r.height() * t["firma"] / it.r.width()
                it.set_rect(QRectF(c.x() - t["firma"] / 2, c.y() - h / 2, t["firma"], h))
        self._cambio()

    def _zona_en(self, pagina: int, x: float, y: float) -> Campo | None:
        for c in self.campos:
            if c.tipo == "firma" and c.pagina == pagina and not c.firmado \
                    and c.rect[0] <= x <= c.rect[2] and c.rect[1] <= y <= c.rect[3]:
                return c
        return None

    def agregar_sello(self, tipo: str, pagina: int, x: float, y: float, texto: str | None = None,
                      imagen: bytes | None = None):
        """Pone un sello (texto, fecha, equis, chulo o firma) en la página, en coordenadas visibles."""
        if not 0 <= pagina < self.canvas.page_count:
            return None
        page = self.canvas.pages[pagina]
        sp = page.pos() + QPointF(x, y)
        t = TAMANOS[self.tamano]
        if tipo in ("texto", "fecha"):
            if tipo == "fecha" and texto is None:
                texto = datetime.date.today().strftime("%d/%m/%Y")
            item = SelloTexto(texto or "", t["texto"], tipo)
            item.on_empty = self._texto_vacio
            ascent = QFontMetricsF(item.font()).ascent()
            item.setPos(sp.x(), sp.y() - ascent * 0.7)
        elif tipo in ("equis", "chulo"):
            item = MarcaSello(tipo, t["marca"])
            item.setPos(sp.x() - t["marca"] / 2, sp.y() - t["marca"] / 2)
        elif tipo == "firma":
            png = imagen or self.firma_png
            img = QImage.fromData(png) if png else QImage()
            if img.isNull():
                return None
            ratio = img.height() / max(1, img.width())
            zona = self._zona_en(pagina, x, y)
            if zona is not None:
                area = self.canvas.rect_escena(zona).adjusted(2, 2, -2, -2)
                w = min(area.width(), area.height() / max(ratio, 1e-6))
                h = w * ratio
                rect = QRectF(area.center().x() - w / 2, area.center().y() - h / 2, w, h)
            else:
                w = min(t["firma"], page.w * 0.6)
                h = w * ratio
                rect = QRectF(sp.x() - w / 2, sp.y() - h / 2, w, h)
            item = ShapeItem("image", rect, QColor(), 0, img, png)
            item.sello = "firma"
        else:
            raise ValueError(tipo)
        item.setZValue(Z_SELLO)
        self.canvas.scene().addItem(item)
        self.sellos.append(item)
        self.undo_stack.append(("sello+", [item]))
        self.redo_stack.clear()
        self._ultimo_undo = None
        self.canvas.scene().clearSelection()
        if tipo == "firma":
            item.setSelected(True)
        self._cambio()
        return item

    def colocar(self, tool: str, pagina: int, x: float, y: float):
        """Clic sobre la página con una herramienta de sello."""
        item = self.agregar_sello(tool, pagina, x, y)
        if item is None:
            return
        if tool == "texto":
            item.start_editing()
        elif tool == "fecha":                        # lista para moverla; doble clic para cambiarla
            item.setSelected(True)
        if tool not in ("equis", "chulo"):          # las marcas siguen activas para marcar varias casillas
            self.set_tool("rellenar")

    def usar_en_campo(self, tool: str, cid: str) -> bool:
        """Una herramienta de sello usada sobre un campo del formulario actúa sobre el campo:
        Equis o Chulo marcan la casilla, Fecha escribe la de hoy y Texto lleva a escribir en él."""
        c = self.por_id.get(cid)
        if c is None or c.solo_lectura:
            return False
        if tool in ("equis", "chulo") and c.tipo in ("casilla", "opcion"):
            self.set_valor(cid, True)
            return True
        if tool == "fecha" and c.tipo == "texto":
            self.set_valor(cid, datetime.date.today().strftime("%d/%m/%Y"))
            self.set_tool("rellenar")
            return True
        if tool == "texto" and c.tipo == "texto":
            self.set_tool("rellenar")
            self.canvas.enfocar(cid)
            return True
        return False

    def _texto_vacio(self, item):
        if item.scene() is not None:
            self.canvas.scene().removeItem(item)
        self._cambio()

    def borrar_seleccion(self):
        items = [i for i in self.canvas.scene().selectedItems() if _es_sello(i)]
        if not items:
            return
        for it in items:
            if it.scene() is not None:
                self.canvas.scene().removeItem(it)
        self.undo_stack.append(("sello-", items))
        self.redo_stack.clear()
        self._ultimo_undo = None
        self._cambio()

    def _sellos_vivos(self) -> list:
        out = []
        for it in self.sellos:
            try:
                if it.scene() is None:
                    continue
            except RuntimeError:               # la escena ya se destruyó (al cerrar la ventana)
                continue
            if isinstance(it, TextItem) and not it.toPlainText().strip():
                continue
            out.append(it)
        return out

    def _recoger_sellos(self) -> tuple[list, int]:
        out, fuera = [], 0
        for it in self._sellos_vivos():
            page = self.canvas.page_at(it.sceneBoundingRect().center())
            if page is None:
                fuera += 1
                continue
            e = it.to_edit(page.index, page.pos())
            if e:
                out.append(e)
        return out, fuera

    # ------------------------------------------------------------------ firmas
    def almacen(self) -> firmas.AlmacenFirmas:
        if self._almacen is None:
            self._almacen = firmas.AlmacenFirmas()
        return self._almacen

    def _llenar_firmas(self):
        """Las firmas guardadas en el equipo, como fichas en el paso «Pon tu firma» (dos por fila)."""
        lay = self.rejilla_firmas_lay
        while lay.count():
            it = lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self.tarjetas_firma = []
        try:
            guardadas = self.almacen().listar()
        except Exception:  # noqa: BLE001 — una carpeta de firmas ilegible no debe impedir trabajar
            guardadas = []
        for k, path in enumerate(guardadas):
            t = _TarjetaFirma(path, k + 1)
            t.setChecked(self.canvas.tool == "firma" and t.ruta == self._firma_ruta)
            t.clicked.connect(lambda on, ruta=t.ruta: self.usar_firma_guardada(ruta) if on else
                              self.set_tool("rellenar"))
            lay.addWidget(t, k // 2, k % 2)
            self.tarjetas_firma.append(t)
        self.rejilla_firmas.setVisible(bool(guardadas))
        self.b_firmas.setVisible(bool(guardadas))
        self._ayudas()

    def usar_firma(self, png: bytes):
        self.firma_png = png
        self.set_tool("firma")

    def usar_firma_guardada(self, path):
        try:
            png = Path(path).read_bytes()
        except OSError:
            QMessageBox.warning(self, "Firma", "No se pudo leer esa firma guardada.")
            return
        self._firma_ruta = str(path)
        self.usar_firma(png)

    def pedir_firma(self) -> bytes | None:
        """Abre el diálogo para crear una firma. Devuelve el PNG o None si se cancela."""
        dlg = firmas.DialogoFirma(self)
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.png:
            return None
        self._firma_ruta = None
        if dlg.guardar:
            try:
                self._firma_ruta = str(self.almacen().guardar(dlg.png))
            except core.UserError as exc:
                QMessageBox.information(self, "Firma", f"{exc}\nLa firma se usará solo esta vez.")
        return dlg.png

    def nueva_firma(self):
        png = self.pedir_firma()
        if png:
            self._llenar_firmas()
            self.usar_firma(png)

    def administrar_firmas(self):
        firmas.DialogoAdministrar(self.almacen(), self).exec()
        if self._firma_ruta and not Path(self._firma_ruta).exists():
            self._firma_ruta = None
        self._llenar_firmas()

    def firmar_campo(self, campo: Campo):
        """Clic en un campo de firma del PDF: pone la firma en imagen dentro del campo."""
        png = self.firma_png
        if png is None:
            guardadas = self.almacen().listar()
            if guardadas:
                menu = QMenu(self)
                for k, path in enumerate(guardadas, 1):
                    menu.addAction(QIcon(firmas.miniatura(path, 132, 46)), f"Firma {k}").setData(str(path))
                menu.addSeparator()
                nueva = menu.addAction(icon("add"), "Nueva firma…")
                elegida = menu.exec(QCursor.pos())
                if elegida is None:
                    return
                if elegida is nueva:
                    png = self.pedir_firma()
                else:
                    try:
                        png = Path(elegida.data()).read_bytes()
                    except OSError:
                        png = None
            else:
                png = self.pedir_firma()
            if not png:
                return
            self.firma_png = png
        x = (campo.rect[0] + campo.rect[2]) / 2
        y = (campo.rect[1] + campo.rect[3]) / 2
        self.agregar_sello("firma", campo.pagina, x, y, imagen=png)

    # ------------------------------------------------------------------ datos
    def vaciar_formulario(self):
        ids = [cid for cid in self.navegables if _lleno(self.por_id[cid], self.actuales.get(cid))]
        if not ids:
            self._avisar("El formulario ya está vacío.")
            return
        foto = {cid: copy.deepcopy(v) for cid, v in self.actuales.items()}
        for cid in self.navegables:
            c = self.por_id[cid]
            vacio = False if c.tipo in ("casilla", "opcion") else ([] if c.multiple else "")
            self.actuales[cid] = vacio
            self.canvas.mostrar_valor(cid)
        self.undo_stack.append(("valores", foto))
        self.redo_stack.clear()
        self._ultimo_undo = None
        self._cambio()
        self._avisar("Se vació el formulario. Usa Deshacer si te equivocaste.")

    def exportar_datos(self):
        if not self.campos:
            return
        sugerido = suggest_output(self.path, "_datos", ".csv")
        out, flt = QFileDialog.getSaveFileName(self, "Exportar datos del formulario", sugerido,
                                               "Excel (CSV) (*.csv);;JSON (*.json)")
        if not out:
            return
        if Path(out).suffix.lower() not in (".csv", ".json"):
            out += ".json" if "json" in (flt or "").lower() else ".csv"
        ctx.remember_dir(out)
        try:
            n = formularios.escribir_datos(formularios.datos_de(self.campos, self.actuales), out)
        except core.UserError as exc:
            QMessageBox.warning(self, "No se pudo exportar", str(exc))
            return
        show_done(self, "Datos exportados", f"Se guardaron {_plural(n, 'campo', 'campos')} en «{Path(out).name}».",
                  file=out)

    def importar_datos(self):
        if not self.campos:
            return
        paths = ask_open(self, False, "Datos de formulario (*.json *.csv)", "Importar datos")
        if not paths:
            return
        try:
            datos = formularios.importar_datos(paths[0])
        except core.UserError as exc:
            QMessageBox.warning(self, "No se pudo importar", str(exc))
            return
        valores, aplicados, ignorados = formularios.valores_desde_datos(self.campos, datos)
        if valores:
            foto = {cid: copy.deepcopy(v) for cid, v in self.actuales.items()}
            for cid, v in valores.items():
                self.actuales[cid] = copy.deepcopy(v)
                self.canvas.mostrar_valor(cid)
            self.undo_stack.append(("valores", foto))
            self.redo_stack.clear()
            self._ultimo_undo = None
            self._cambio()
        msg = f"Se aplicaron {_plural(aplicados, 'campo', 'campos')}."
        if ignorados:
            msg += (f" {_plural(ignorados, 'dato no corresponde', 'datos no corresponden')} a ningún campo "
                    "que se pueda cambiar y se ignoraron.")
        QMessageBox.information(self, "Datos importados", msg)

    # ------------------------------------------------------------------ panel y estado
    def _llenar_panel(self):
        self.lista.clear()
        vistos = set()
        for c in self.campos:
            if c.tipo == "boton":
                continue
            clave = ("n", c.nombre) if c.tipo in RELLENABLES else ("i", c.id)
            if clave in vistos:
                continue
            vistos.add(clave)
            it = QListWidgetItem()
            it.setData(ROL_ID, c.id)
            self.lista.addItem(it)

    def _resumen_valor(self, c: Campo) -> str:
        grupo = self.por_nombre.get(c.nombre, [c.id])
        if c.tipo == "opcion":
            elegido = next((self.por_id[x].estado for x in grupo if self.actuales.get(x)), None)
            return f"«{elegido}»" if elegido else "sin elegir"
        v = self.actuales.get(c.id, c.valor)
        if c.tipo == "casilla":
            return "marcada" if any(self.actuales.get(x) for x in grupo) else "sin marcar"
        if isinstance(v, (list, tuple)):
            return ", ".join(c.texto_de(x) for x in v) if v else "vacío"
        texto = c.texto_de(v) if c.tipo in ("lista", "desplegable") else str(v or "")
        if c.clave and texto:
            return "••••••"
        texto = " ".join(texto.split())
        return f"«{texto[:40]}{'…' if len(texto) > 40 else ''}»" if texto else "vacío"

    def _firmada(self, c: Campo) -> bool:
        r = self.canvas.rect_escena(c) if 0 <= c.pagina < self.canvas.page_count else None
        if r is None:
            return False
        return any(getattr(it, "sello", "") == "firma" and it.sceneBoundingRect().intersects(r)
                   for it in self._sellos_vivos())

    def _estado(self, c: Campo) -> str:
        if c.tipo == "firma":
            return "firmado" if c.firmado else ("firmada" if self._firmada(c) else "firma")
        if c.solo_lectura:
            return "solo"
        grupo = self.por_nombre.get(c.nombre, [c.id])
        lleno = any(_lleno(self.por_id[x], self.actuales.get(x)) for x in grupo)
        if lleno:
            return "lleno"
        return "obligatorio" if c.obligatorio else "pendiente"

    def conteo(self) -> tuple[int, int, int]:
        """(campos, rellenados, obligatorios que faltan), contando una vez cada nombre."""
        total = llenos = faltan = 0
        for nombre, ids in self.por_nombre.items():
            c = self.por_id[ids[0]]
            if c.solo_lectura:
                continue
            total += 1
            if any(_lleno(self.por_id[x], self.actuales.get(x)) for x in ids):
                llenos += 1
            elif any(self.por_id[x].obligatorio for x in ids):
                faltan += 1
        return total, llenos, faltan

    def faltantes(self) -> list[Campo]:
        out = []
        for nombre, ids in self.por_nombre.items():
            c = self.por_id[ids[0]]
            if c.solo_lectura or not any(self.por_id[x].obligatorio for x in ids):
                continue
            if not any(_lleno(self.por_id[x], self.actuales.get(x)) for x in ids):
                out.append(c)
        return out

    def refrescar(self):
        cargado = self.doc is not None or bool(self.canvas.pages)
        self.stack.setCurrentIndex(1 if cargado else 0)
        self.ficha.setVisible(cargado)
        for w in self._controles:
            w.setVisible(cargado)
        if not cargado:
            self.paso_campos.setVisible(True)
            self.paso_campos.set_help(INTRO["campos"])
            self.paso_guardar.set_help(INTRO["guardar"])
            self.chip.hide()
            self._ayudas()
            self._actualizar_botones()
            return
        total, llenos, faltan = self.conteo()
        if total:
            partes = [_plural(total, "campo", "campos"), _plural(llenos, "rellenado", "rellenados")]
            if faltan:
                partes.append(f"falta{'n' if faltan != 1 else ''} {_plural(faltan, 'obligatorio', 'obligatorios')}")
            elif any(self.por_id[x].obligatorio for ids in self.por_nombre.values() for x in ids):
                partes.append("obligatorios completos")
            texto = " · ".join(partes)
        else:
            texto = "Sin campos: escribe encima con las herramientas"
        self.paso_campos.set_help(texto)
        self.chip.setText(f"{llenos} de {total}" if total else "")
        self.chip.setVisible(bool(total))
        solo = self.chk_pendientes.isChecked()
        visibles = 0
        for i in range(self.lista.count()):
            it = self.lista.item(i)
            c = self.por_id.get(it.data(ROL_ID))
            if c is None:
                continue
            estado = self._estado(c)
            sub = formularios.NOMBRE_TIPO.get(c.tipo, "")
            if c.tipo == "firma":
                sub += " · " + {"firmado": "firmado digitalmente", "firmada": "con tu firma",
                                "firma": "haz clic para firmar"}[estado]
            else:
                sub += " · " + self._resumen_valor(c)
                if estado == "obligatorio":
                    sub = "Obligatorio · " + sub
                elif estado == "solo":
                    sub += " · solo lectura"
            if self.canvas.page_count > 1:
                sub += f" · pág. {c.pagina + 1}"
            it.setData(ROL_TITULO, c.titulo)
            it.setData(ROL_SUB, sub)
            it.setData(ROL_ESTADO, estado)
            it.setToolTip(f"{c.titulo}\n{sub}")
            oculto = solo and estado not in ("pendiente", "obligatorio", "firma")
            it.setHidden(oculto)
            visibles += not oculto
        self.vacio_lb.setText("No quedan campos pendientes." if self.lista.count() and not visibles else "")
        self.vacio_lb.setVisible(bool(self.vacio_lb.text()))
        hay_lista = self.lista.count() > 0
        self.paso_campos.setVisible(hay_lista)       # sin campos, el aviso de arriba explica qué hacer
        self.lista.setVisible(visibles > 0)
        self._ajustar_lista(visibles)
        self.lista.viewport().update()
        self.caja_datos.setVisible(bool(self.navegables))
        self.chk_aplanar.setVisible(bool(self.campos))      # aplanar solo cambia algo si hay campos
        directo, motivo = self._directo()
        self.paso_guardar.set_help(INTRO["guardar"] if directo or not motivo else motivo[0].upper() + motivo[1:] + ".")
        for cid, zona in self.canvas.zonas.items():
            con = self._firmada(zona.campo)
            if con != zona.con_firma:
                zona.con_firma = con
                zona.update()
        self._ayudas()
        self._actualizar_botones()

    def _ajustar_lista(self, visibles: int | None = None):
        """La lista de campos muestra hasta tantas filas como quepan en el panel; más allá, se desplaza."""
        if visibles is None:
            visibles = sum(not self.lista.item(i).isHidden() for i in range(self.lista.count()))
        alto_panel = self.panel_scroll.viewport().height() if self.panel_scroll is not None else 480
        filas = max(3, min(8, (alto_panel - 170) // _DelegadoCampos.ALTO))
        self.lista.setFixedHeight(min(max(visibles, 1), filas) * _DelegadoCampos.ALTO + 10)

    def _actualizar_botones(self):
        try:
            self._actualizar_botones_ya()
        except RuntimeError:                   # señales que llegan mientras Qt destruye la ventana
            pass

    def _actualizar_botones_ya(self):
        cargado = self.doc is not None
        cambios = self.has_unsaved()
        self.unsaved.setVisible(cambios)
        directo, motivo = self._directo()
        guardar = cambios and directo
        hay_campos = bool(self.navegables)
        self.action.setEnabled(cargado)
        if guardar or not cargado:
            self.action.setText("Guardar formulario" if hay_campos else "Guardar documento")
            self.action.setToolTip("Guardar en el mismo archivo, con una copia de respaldo (Ctrl+S)")
        else:
            self.action.setText("Guardar copia…")
            self.action.setToolTip(motivo[0].upper() + motivo[1:] if motivo else
                                   "Guardar una copia con otro nombre (Ctrl+Mayús+S)")
        self.b_como.setVisible(cargado and guardar)
        self.b_undo.setEnabled(bool(self.undo_stack))
        self.b_redo.setEnabled(bool(self.redo_stack))
        self.b_delete.setEnabled(any(_es_sello(i) for i in self.canvas.scene().selectedItems()))
        self._resumen_barra(cargado, cambios, directo, motivo, hay_campos)

    def _resumen_barra(self, cargado: bool, cambios: bool, directo: bool, motivo: str, hay_campos: bool):
        """Chips (obligatorios, elementos agregados) y la frase de la barra de acción."""
        chips = []
        if not cargado:
            texto = "Abre un formulario o un documento para empezar."
        else:
            _total, _llenos, faltan = self.conteo()
            if faltan:
                chips.append((f"Falta{'n' if faltan != 1 else ''} {_plural(faltan, 'obligatorio', 'obligatorios')}",
                              "warn"))
            elif any(self.por_id[x].obligatorio for ids in self.por_nombre.values() for x in ids):
                chips.append(("Obligatorios completos", "ok"))
            n_sellos = len(self._sellos_vivos())
            if n_sellos:
                chips.append((_plural(n_sellos, "elemento agregado", "elementos agregados"), "cat"))
            if self._mensaje:
                texto = self._mensaje
            elif not cambios:
                texto = ("Haz clic en un campo para escribir. Tab pasa al siguiente." if hay_campos else
                         "Agrega texto, marcas o tu firma sobre la página.")
            elif directo:
                texto = "Se guarda en el mismo archivo, con una copia de respaldo."
            else:
                texto = (motivo[0].upper() + motivo[1:] + ".") if motivo else ""
        clave = (tuple(chips), texto)
        if clave != self._resumen_actual:
            self._resumen_actual = clave
            self.set_summary(chips, texto)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "lista") and self.lista.count():
            self._ajustar_lista()

    def _pagina_cambio(self, i: int):
        self.page_spin.blockSignals(True)
        self.page_spin.setValue(i + 1)
        self.page_spin.blockSignals(False)
        self.b_prev.setEnabled(i > 0)
        self.b_next.setEnabled(i < self.canvas.page_count - 1)

    def _campo_elegido(self, it: QListWidgetItem):
        cid = it.data(ROL_ID)
        c = self.por_id.get(cid)
        if c is None:
            return
        if c.tipo == "firma" or c.solo_lectura:
            self.canvas.crear_editores(c.pagina)
            r = self.canvas.rect_escena(c)
            self.canvas.ensureVisible(r, 60, 90)
            self.canvas.destacar(r)
            return
        grupo = self.por_nombre.get(c.nombre, [cid])
        destino = next((x for x in grupo if self.actuales.get(x)), cid) if c.tipo == "opcion" else cid
        self.canvas.enfocar(destino)

    # ------------------------------------------------------------------ guardar
    def has_unsaved(self) -> bool:
        if self.doc is None and not self.canvas.pages:
            return False
        return self.actuales != self.originales or bool(self._sellos_vivos())

    def _directo(self) -> tuple[bool, str]:
        """¿Se puede «Guardar» en el mismo archivo?  (sí/no, por qué no)."""
        if self.doc is None or not self.path:
            return False, ""
        if core.is_image(self.path):
            return False, "una foto se guarda como un PDF nuevo"
        if self.chk_aplanar.isChecked():
            return False, "al aplanar se guarda siempre una copia"
        return True, ""

    def _aplanar_cambio(self, on: bool):
        self._mensaje = ("Al aplanar se guarda una copia con otro nombre: ya no se podrá cambiar lo escrito."
                         if on else "")
        self.refrescar()

    def _guardar_principal(self):
        if self.has_unsaved() and self._directo()[0]:
            self.guardar()
        else:
            self.save()

    def _preparar(self):
        """Revisa lo que falta y recoge los sellos.  (sellos, aplanar) o None si no hay que seguir."""
        foco = self.canvas.scene().focusItem()
        if foco is not None:
            foco.clearFocus()
        faltan = self.faltantes()
        if faltan:
            nombres = ", ".join(f"«{c.titulo}»" for c in faltan[:4]) + (" y otros" if len(faltan) > 4 else "")
            r = QMessageBox.question(self, "Faltan campos obligatorios",
                                     f"Faltan {_plural(len(faltan), 'campo obligatorio', 'campos obligatorios')}: "
                                     f"{nombres}.\n¿Guardar de todas formas?")
            if r != QMessageBox.StandardButton.Yes:
                self.canvas.enfocar(faltan[0].id)
                return None
        sellos, fuera = self._recoger_sellos()
        aplanar = self.chk_aplanar.isChecked()
        if not self.campos and not sellos and not aplanar:
            QMessageBox.information(self, "Guardar", "Todavía no agregaste nada. Usa Texto, Equis, Chulo, Fecha o "
                                    "Firma y haz clic sobre la página.")
            return None
        if fuera:
            QMessageBox.information(self, "Guardar", f"{_plural(fuera, 'elemento quedó', 'elementos quedaron')} "
                                    "fuera de las páginas y no se guardará.")
        return sellos, aplanar

    def save(self, al_terminar=None):
        """«Guardar como…»: una copia rellenada con otro nombre (el original no cambia)."""
        fin = guardado_ui.callback(al_terminar)
        if self.doc is None or ctx.busy or not self.path:
            return fin(False)
        prep = self._preparar()
        if prep is None:
            return fin(False)
        sellos, aplanar = prep
        out = ask_save(self, suggest_output(self.path, "_rellenado"), title="Guardar formulario")
        if not out:
            return fin(False)
        self._guardar_en(out, sellos, aplanar, directo=False, incremental=False, al_terminar=fin)

    def guardar(self, al_terminar=None):
        """«Guardar» en el mismo archivo: la primera vez lo confirma, antes deja una copia de respaldo y agrega
        los cambios al final del archivo (incremental).  Con «Aplanar» marcado, o con una foto, «Guardar como…»."""
        fin = guardado_ui.callback(al_terminar)
        if self.doc is None or ctx.busy or not self.path:
            return fin(False)
        if not self._directo()[0] or not self.has_unsaved():
            return self.save(al_terminar=fin)
        prep = self._preparar()
        if prep is None:
            return fin(False)
        sellos, aplanar = prep
        with PDF_LOCK:
            incremental = guardado.modo_incremental(self.doc, aplanar=aplanar)
            firmado = guardado.tiene_firma_digital(self.doc)
        r = guardado_ui.preparar_directo(self, self.path, self._disco, incremental, firmado)
        if r == "guardar_como":
            return self.save(al_terminar=fin)
        if r != "guardar":
            return fin(False)
        self._guardar_en(self.path, sellos, aplanar, directo=True, incremental=incremental, al_terminar=fin)

    def _guardar_en(self, out: str, sellos, aplanar: bool, directo: bool, incremental: bool, al_terminar):
        src = self.path
        same = os.path.normcase(os.path.abspath(out)) == os.path.normcase(os.path.abspath(src))
        volver: list = []
        if same:
            # si otra pantalla tiene abierto este archivo, se suelta allí y se vuelve a abrir al terminar
            volver = guardado_ui.preparar_reemplazo(self, out)
            if volver is None:
                return al_terminar(False)
            self._cerrar_doc()
        password = self.password
        n_sellos = len(sellos)
        pagina = self.canvas.current_page
        hecho: dict = {}

        def done(res):
            hecho["ok"] = True
            if res.error_respaldo:
                if guardado_ui.respaldo_fallido(self, res.error_respaldo) == "guardar_como":
                    self.save(al_terminar=al_terminar)
                else:
                    al_terminar(False)
                return
            n, _m = res.valor
            if password:
                ctx.passwords.set(out, password)
            if self.load(out, confirm=False) and pagina:
                self.canvas.go_to_page(pagina)
            guardado_ui.trabajo_guardado(self, "forms")
            if directo:
                self._avisar(guardado_ui.aviso_guardado(res.respaldo is not None))
            else:
                show_done(self, "Formulario guardado", _detalle_guardado(n, n_sellos, aplanar), file=out)
            al_terminar(True)

        def finally_():
            if same and self.doc is None and os.path.exists(src):
                try:
                    with PDF_LOCK:
                        self.doc = core.open_pdf(src, password)
                    self.canvas.doc = self.doc
                    for p in self.canvas.pages:
                        p.rendered = 0
                    self.canvas.schedule_render()
                except core.UserError:
                    pass
            guardado_ui.reabrir(volver)
            guardado_ui.revisar_fin(hecho, al_terminar)

        guardado_ui.lanzar_guardado("Guardando el formulario…", guardado.guardar_con_respaldo, done,
                                    on_finally=finally_, tarea=formularios.rellenar,
                                    respaldo=same and guardado_ui.respaldo_activo(),
                                    ajustes=guardado_ui.ajustes_path(), retencion=guardado_ui.retencion(),
                                    path=src, output=out, valores=self.valores(), sellos=sellos,
                                    aplanar=aplanar, password=password, incremental=incremental)

# --------------------------------------------------------------------------
# Registro
# --------------------------------------------------------------------------

def aviso_visor(visor):
    """Franja en «Ver y editar» cuando el PDF abierto es un formulario."""
    canvas = getattr(visor, "canvas", None)
    doc = getattr(canvas, "doc", None)
    if doc is None:
        return None
    with PDF_LOCK:
        try:
            n = int(doc.is_form_pdf or 0) if not doc.is_closed and doc.is_pdf else 0
        except Exception:  # noqa: BLE001
            n = 0
    if not n:
        return None
    path = canvas.path
    return ("Este PDF es un formulario: puedes rellenarlo campo por campo.", "Rellenar formulario",
            lambda: extensiones.ir_a("forms", [path]))


REGISTRO = dict(clave="forms", titulo="Formularios y firma", seccion="ver", orden=10, pagina=FormsPage,
                dibujo=dibujo, inicio="Rellena y firma sin imprimir")
