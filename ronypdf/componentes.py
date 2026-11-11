"""Componentes del sistema de diseño 2.0 (los ``rp-*`` de ``bundle.base.css``, pasados a Qt).

Todos leen los colores de ``tema.py`` al pintarse.  Los que dependen de la categoría (interruptor, fichas,
tarjetas de opción, pasos, chip ``cat``) toman la de su contenedor: la propiedad ``cat`` del primer
antecesor que la tenga (``ToolPage`` la pone con la categoría de su herramienta), igual que ``--c`` en CSS.
También se les puede pasar ``cat=`` a mano.

Se importan también desde ``widgets.py``:
    GlyphTile, Chip, Count, Kbd, PdfThumb, Switch, SwitchRow, Segmented, ChoiceChip, Choices, OptionCard,
    OptionGroup, Note, FileChip, Step, StepList, FlowLayout
"""

from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import (QEasingCurve, QPoint, QPointF, QRect, QRectF, QSize, Qt, QVariantAnimation,
                            Signal)
from PySide6.QtGui import QColor, QFontMetrics, QIcon, QPainter, QPen
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QFrame, QHBoxLayout, QLabel, QLayout,
                               QPushButton, QSizePolicy, QToolButton, QVBoxLayout, QWidget, QWidgetItem)

from . import art, tema
from .icons import pixmap as line_pixmap


# ---------------------------------------------------------------- utilidades

def cat_de(widget: QWidget | None) -> str | None:
    """La categoría heredada: la propiedad ``cat`` del widget o del primer antecesor que la tenga."""
    w = widget
    while w is not None:
        v = w.property("cat")
        if isinstance(v, str) and v:
            return v
        w = w.parentWidget()
    return None


def _tonos(widget: QWidget, cat: str | None) -> dict[str, str]:
    return tema.tonos(cat or cat_de(widget))


def _foco_teclado(e) -> bool:
    return e.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason,
                          Qt.FocusReason.ShortcutFocusReason)


def _anillo(p: QPainter, rect: QRectF, radio: float) -> None:
    """Anillo de foco del teclado: sólido, 2 px, color ``focus``."""
    p.save()
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor(tema.color("focus")), 2))
    p.drawRoundedRect(rect, radio, radio)
    p.restore()


def label(text: str, name: str | None = None, wrap: bool = False) -> QLabel:
    lb = QLabel(text)
    if name:
        lb.setObjectName(name)
    lb.setWordWrap(wrap)
    return lb


def repolish(w: QWidget) -> None:
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()


# ---------------------------------------------------------------- disposición en flujo

class FlowLayout(QLayout):
    """Distribuye los elementos en filas que se parten solas (como ``flex-wrap``)."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 8, align: Qt.AlignmentFlag | None = None):
        super().__init__(parent)
        self._items: list = []
        self._sp = spacing
        self._align = align or Qt.AlignmentFlag.AlignLeft
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):  # noqa: N802 — API de Qt
        self._items.append(item)

    def addWidget(self, w):  # noqa: N802
        self.addChildWidget(w)
        self.addItem(QWidgetItem(w))

    def count(self):
        return len(self._items)

    def itemAt(self, i):  # noqa: N802
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):  # noqa: N802
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self):  # noqa: N802
        return True

    def heightForWidth(self, w):  # noqa: N802
        return self._hacer(QRect(0, 0, w, 0), True)

    def setGeometry(self, r):  # noqa: N802
        super().setGeometry(r)
        self._hacer(r, False)

    def sizeHint(self):  # noqa: N802
        return self.minimumSize()

    def minimumSize(self):  # noqa: N802
        s = QSize()
        for it in self._items:
            if not it.isEmpty():
                s = s.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return s + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _hacer(self, rect: QRect, prueba: bool) -> int:
        m = self.contentsMargins()
        r = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        filas: list[list] = [[]]
        x = 0
        for it in self._items:
            if it.isEmpty():
                continue
            w = it.sizeHint().width()
            if filas[-1] and x + w > r.width():
                filas.append([])
                x = 0
            filas[-1].append(it)
            x += w + self._sp
        y = r.y()
        for fila in filas:
            if not fila:
                continue
            alto = max(it.sizeHint().height() for it in fila)
            ancho = sum(it.sizeHint().width() for it in fila) + self._sp * (len(fila) - 1)
            if self._align & Qt.AlignmentFlag.AlignRight:
                x = r.x() + max(0, r.width() - ancho)
            elif self._align & Qt.AlignmentFlag.AlignHCenter:
                x = r.x() + max(0, (r.width() - ancho) // 2)
            else:
                x = r.x()
            for it in fila:
                sh = it.sizeHint()
                if not prueba:
                    it.setGeometry(QRect(QPoint(x, y + (alto - sh.height()) // 2), sh))
                x += sh.width() + self._sp
            y += alto + self._sp
        return y - self._sp - r.y() + m.top() + m.bottom() if any(filas) else m.top() + m.bottom()


# ---------------------------------------------------------------- azulejo

class GlyphTile(QWidget):
    """Azulejo de categoría con el glifo blanco (``rp-gtile``).  Tamaños: 22, 28, 40, 52 y 64.

    ``cat`` = categoría; si se omite, la de la herramienta ``key`` y, si no es una herramienta, la heredada."""

    def __init__(self, key: str, size: int = tema.TILE_MD, cat: str | None = None, soft: bool = False,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.key, self.cat, self.soft, self._size = key, cat, soft, size
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def set_key(self, key: str, cat: str | None = None) -> None:
        self.key = key
        if cat is not None:
            self.cat = cat
        self.update()

    def categoria(self) -> str | None:
        if self.cat:
            return self.cat
        c = tema.categoria_de(self.key)
        return c if c != "marca" or self.key == "home" else cat_de(self)

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.drawPixmap(0, 0, art.glyph_tile(self.key, self._size, self.categoria(), self.soft,
                                          self.devicePixelRatioF()))
        p.end()


# ---------------------------------------------------------------- chips, contador, tecla, miniatura

class Chip(QWidget):
    """Pastilla de estado (``rp-chip``): tonos ok, warn, danger, info, neutral y cat.

    ``glyph`` = nombre de un ícono de icons.py (14 px), o ``dot=True`` para el punto de color."""

    def __init__(self, text: str = "", tone: str = "neutral", glyph: str | None = None, dot: bool = False,
                 cat: str | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self._text, self.tone, self.glyph, self.dot, self.cat = text, tone, glyph, dot, cat
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(text)

    def text(self) -> str:
        return self._text

    def setText(self, text: str) -> None:  # noqa: N802
        self._text = text
        self.setAccessibleName(text)
        self.updateGeometry()
        self.update()

    def set_tone(self, tone: str) -> None:
        self.tone = tone
        self.update()

    def _colores(self) -> tuple[str, str]:
        t = tema.tokens()
        if self.tone in ("ok", "warn", "danger", "info"):
            return t[f"{self.tone}-tint"], t[self.tone]
        if self.tone == "cat":
            ton = _tonos(self, self.cat)
            return ton["band"], ton["ink"]
        return t["surface-sunken"], t["ink-2"]

    def sizeHint(self):  # noqa: N802
        fm = QFontMetrics(tema.fuente("chip"))
        w = 20 + fm.horizontalAdvance(self._text)
        if self.glyph:
            w += 20
        elif self.dot:
            w += 13
        return QSize(w, 26)

    minimumSizeHint = sizeHint

    def paintEvent(self, _e):  # noqa: N802
        bg, fg = self._colores()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0, (self.height() - 26) / 2, 0, -(self.height() - 26) / 2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(bg))
        p.drawRoundedRect(r, 13, 13)
        x = r.left() + 10
        if self.glyph:
            p.drawPixmap(QPointF(x, r.center().y() - 7), line_pixmap(self.glyph, fg, 14, 2.0))
            x += 20
        elif self.dot:
            p.setBrush(QColor(fg))
            p.drawEllipse(QPointF(x + 3.5, r.center().y()), 3.5, 3.5)
            x += 13
        p.setFont(tema.fuente("chip", self.font()))
        p.setPen(QColor(fg))
        p.drawText(QRectF(x, r.top(), r.right() - x, r.height()), Qt.AlignmentFlag.AlignVCenter, self._text)
        p.end()


class Count(QWidget):
    """Contador rojo para pendientes (``rp-count``); ``quiet`` para totales que no piden atención."""

    def __init__(self, n: int | str = 0, quiet: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.n, self.quiet = n, quiet
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def set_count(self, n) -> None:
        self.n = n
        self.updateGeometry()
        self.update()

    @staticmethod
    def pintar(p: QPainter, centro_derecha: QPointF, n, quiet: bool = False) -> QRectF:
        f = tema.fuente("chip")
        f.setPixelSize(11)
        f.setWeight(f.Weight.Bold)
        fm = QFontMetrics(f)
        w = max(20, fm.horizontalAdvance(str(n)) + 12)
        r = QRectF(centro_derecha.x() - w, centro_derecha.y() - 10, w, 20)
        t = tema.tokens()
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(t["surface-sunken"] if quiet else t["brand"]))
        p.drawRoundedRect(r, 10, 10)
        p.setFont(f)
        p.setPen(QColor(t["ink-2"] if quiet else t["on-brand"]))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, str(n))
        p.restore()
        return r

    def sizeHint(self):  # noqa: N802
        f = tema.fuente("chip")
        f.setPixelSize(11)
        return QSize(max(20, QFontMetrics(f).horizontalAdvance(str(self.n)) + 14), 20)

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        Count.pintar(p, QPointF(self.width(), self.height() / 2), self.n, self.quiet)
        p.end()


class Kbd(QLabel):
    """Tecla de atajo (``rp-kbd``)."""

    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setObjectName("Kbd")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)


def pintar_miniatura(p: QPainter, r: QRectF, pdf: bool = True, etiqueta: str = "PDF") -> None:
    """Hoja blanca con tres renglones y el distintivo rojo PDF (``rp-file .thumb`` + ``rp-pdf``)."""
    t = tema.tokens()
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QColor(t["line"]), 1))
    p.setBrush(QColor("#ffffff"))
    p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
    k = r.width() / 34
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#d9dce3"))
    for dy, w in ((8, 20), (13, 16), (18, 20)):
        p.drawRect(QRectF(r.left() + 6 * k, r.top() + dy * k, w * k, 2 * k))
    if pdf:
        f = tema.fuente("chip")
        f.setPixelSize(max(6, round(8 * k)))
        f.setWeight(f.Weight.Bold)
        f.setLetterSpacing(f.SpacingType.PercentageSpacing, 104)
        fm = QFontMetrics(f)
        bw, bh = fm.horizontalAdvance(etiqueta) + 8 * k, 14 * k
        b = QRectF(r.left() + 4 * k, r.bottom() - 5 * k - bh, bw, bh)
        p.setBrush(QColor(t["brand"]))
        p.drawRoundedRect(b, 4 * k, 4 * k)
        p.setFont(f)
        p.setPen(QColor(t["on-brand"]))
        p.drawText(b, Qt.AlignmentFlag.AlignCenter, etiqueta)
    p.restore()


class PdfThumb(QWidget):
    def __init__(self, w: int = 34, h: int = 44, pdf: bool = True, parent: QWidget | None = None):
        super().__init__(parent)
        self.pdf = pdf
        self.etiqueta = "PDF"
        self.setFixedSize(w, h)

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        pintar_miniatura(p, QRectF(self.rect()), self.pdf, self.etiqueta)
        p.end()


# ---------------------------------------------------------------- interruptor

class Switch(QAbstractButton):
    """Interruptor sí/no (``rp-switch``): 38 × 22, encendido en el color de la categoría."""

    def __init__(self, checked: bool = False, cat: str | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.cat = cat
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedSize(44, 28)
        self._kbd = False
        self._pos = 1.0 if checked else 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._mover)
        self.toggled.connect(self._al_cambiar)

    def _mover(self, v):
        self._pos = float(v)
        self.update()

    def _al_cambiar(self, on: bool):
        fin = 1.0 if on else 0.0
        if not self.isVisible():
            self._anim.stop()
            self._pos = fin
            self.update()
            return
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(fin)
        self._anim.start()

    def focusInEvent(self, e):  # noqa: N802
        self._kbd = _foco_teclado(e)
        super().focusInEvent(e)

    def focusOutEvent(self, e):  # noqa: N802
        self._kbd = False
        super().focusOutEvent(e)

    def sizeHint(self):  # noqa: N802
        return QSize(44, 28)

    def paintEvent(self, _e):  # noqa: N802
        t = tema.tokens()
        ton = _tonos(self, self.cat)
        on_col = QColor(ton["c"] if (self.cat or cat_de(self)) else t["ink"])
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRectF(3, 3, 38, 22)
        k = self._pos
        if not self.isEnabled():
            p.setOpacity(0.45)
        off_bg, off_bd = QColor(t["field"]), QColor(t["line-control"])

        def mix(a: QColor, b: QColor) -> QColor:
            return QColor(round(a.red() + (b.red() - a.red()) * k), round(a.green() + (b.green() - a.green()) * k),
                          round(a.blue() + (b.blue() - a.blue()) * k))

        p.setPen(QPen(mix(off_bd, on_col), 1))
        p.setBrush(mix(off_bg, on_col))
        p.drawRoundedRect(track.adjusted(0.5, 0.5, -0.5, -0.5), 10.5, 10.5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(mix(QColor(t["line-control"]), QColor(t["on-cat"])))
        p.drawEllipse(QRectF(track.left() + 3 + 16 * k, track.top() + 3, 16, 16))
        if self._kbd and self.hasFocus():
            p.setOpacity(1)
            _anillo(p, track.adjusted(-2, -2, 2, 2), 13)
        p.end()


class SwitchRow(QWidget):
    """Fila con el texto a la izquierda (y una línea de ayuda opcional) y el interruptor a la derecha."""

    toggled = Signal(bool)

    def __init__(self, text: str, help: str | None = None, checked: bool = False,  # noqa: A002
                 cat: str | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(12)
        col = QVBoxLayout()
        col.setSpacing(0)
        self.text_lb = label(text, "SwitchText", wrap=True)
        col.addWidget(self.text_lb)
        self.help_lb = label(help or "", "Help", wrap=True)
        col.addWidget(self.help_lb)
        h.addLayout(col, 1)
        self.help_lb.setVisible(bool(help))      # ya con padre: sin padre, mostrarla crearía una ventana nativa
        self.switch = Switch(checked, cat)
        self.switch.setAccessibleName(text)
        self.switch.toggled.connect(self.toggled)
        h.addWidget(self.switch, 0, Qt.AlignmentFlag.AlignVCenter)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def isChecked(self) -> bool:  # noqa: N802
        return self.switch.isChecked()

    def setChecked(self, on: bool) -> None:  # noqa: N802
        self.switch.setChecked(on)

    def set_help(self, text: str | None) -> None:
        self.help_lb.setText(text or "")
        self.help_lb.setVisible(bool(text))

    def mouseReleaseEvent(self, e):  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self.isEnabled() and self.rect().contains(e.position().toPoint()):
            self.switch.toggle()
        super().mouseReleaseEvent(e)


# ---------------------------------------------------------------- segmentos

class Segmented(QFrame):
    """De 2 a 4 opciones excluyentes y cortas (``rp-seg``).  ``full=True`` ocupa todo el ancho.

    ``options``: textos, o tuplas ``(texto, valor)`` / ``(texto, valor, glifo)``.  Señal ``changed(índice, valor)``."""

    changed = Signal(int, object)

    def __init__(self, options: Iterable, current: int = 0, full: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Segmented")
        self.full = full
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self._values: list = []
        self.buttons: list[QToolButton] = []
        for k, opt in enumerate(options):
            if isinstance(opt, (tuple, list)):
                text, value = opt[0], opt[1]
                glyph = opt[2] if len(opt) > 2 else None
            else:
                text, value, glyph = opt, opt, None
            b = QToolButton()
            b.setObjectName("SegItem")
            b.setText(text)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setAccessibleName(text)
            if glyph:
                b.setIcon(art.glyph_icon(glyph, tema.color("ink-2"), 16))
                b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            if full:
                b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.group.addButton(b, k)
            lay.addWidget(b)
            self._values.append(value)
            self.buttons.append(b)
        if self.buttons:
            self.buttons[max(0, min(current, len(self.buttons) - 1))].setChecked(True)
        self.group.idToggled.connect(self._toggled)
        self.setSizePolicy(QSizePolicy.Policy.Expanding if full else QSizePolicy.Policy.Fixed,
                           QSizePolicy.Policy.Fixed)

    def _toggled(self, k: int, on: bool):
        if on:
            self.changed.emit(k, self._values[k])

    def index(self) -> int:
        return self.group.checkedId()

    def value(self):
        k = self.index()
        return self._values[k] if k >= 0 else None

    def set_index(self, k: int) -> None:
        if 0 <= k < len(self.buttons):
            self.buttons[k].setChecked(True)

    def set_value(self, value) -> None:
        if value in self._values:
            self.set_index(self._values.index(value))

    def button(self, k: int) -> QToolButton:
        return self.buttons[k]


# ---------------------------------------------------------------- fichas de elección

class ChoiceChip(QAbstractButton):
    """Ficha para elegir varias cosas (``rp-choice``): rectángulo de radio 8, con palomita al elegirla."""

    def __init__(self, text: str, checked: bool = False, count: int | str | None = None, cat: str | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.cat = cat
        self.count = count
        self.setText(text)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(text)
        self._kbd = False
        self.toggled.connect(lambda *_: self.updateGeometry())

    def set_count(self, count) -> None:
        self.count = count
        self.updateGeometry()
        self.update()

    def _fuentes(self):
        f = tema.fuente("body-sm", self.font())
        f.setWeight(f.Weight.DemiBold if self.isChecked() else f.Weight.Medium)
        fc = tema.fuente("body-sm", self.font())
        return f, fc

    def sizeHint(self):  # noqa: N802
        f, fc = self._fuentes()
        w = 24 + QFontMetrics(f).horizontalAdvance(self.text())
        if self.isChecked():
            w += 20
        if self.count not in (None, ""):
            w += 6 + QFontMetrics(fc).horizontalAdvance(str(self.count))
        return QSize(w, 32)

    minimumSizeHint = sizeHint

    def focusInEvent(self, e):  # noqa: N802
        self._kbd = _foco_teclado(e)
        super().focusInEvent(e)

    def focusOutEvent(self, e):  # noqa: N802
        self._kbd = False
        super().focusOutEvent(e)

    def paintEvent(self, _e):  # noqa: N802
        t = tema.tokens()
        hay_cat = bool(self.cat or cat_de(self))
        ton = _tonos(self, self.cat)
        on = self.isChecked()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            p.setOpacity(0.45)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if on:
            p.setPen(QPen(QColor(ton["c"] if hay_cat else t["ink"]), 1))
            p.setBrush(QColor(ton["band"] if hay_cat else t["surface-sunken"]))
            fg = ton["ink"] if hay_cat else t["ink"]
        else:
            p.setPen(QPen(QColor(t["line-control"]), 1))
            p.setBrush(QColor(t["surface-hover"] if self.underMouse() else t["surface"]))
            fg = t["ink"]
        p.drawRoundedRect(r, 8, 8)
        x = 12.0
        if on:
            p.drawPixmap(QPointF(x, self.height() / 2 - 7), line_pixmap("check", fg, 14, 2.2))
            x += 20
        f, fc = self._fuentes()
        p.setFont(f)
        p.setPen(QColor(fg))
        tw = QFontMetrics(f).horizontalAdvance(self.text())
        p.drawText(QRectF(x, 0, tw + 2, self.height()), Qt.AlignmentFlag.AlignVCenter, self.text())
        if self.count not in (None, ""):
            p.setFont(fc)
            p.setPen(QColor(t["ink-3"]))
            p.drawText(QRectF(x + tw + 6, 0, self.width() - x - tw - 6, self.height()),
                       Qt.AlignmentFlag.AlignVCenter, str(self.count))
        if self._kbd and self.hasFocus():
            p.setOpacity(1)
            _anillo(p, QRectF(self.rect()).adjusted(1, 1, -1, -1), 8)
        p.end()


class Choices(QWidget):
    """Grupo de fichas que se parte en filas (``rp-choices``).  Señal ``changed()``."""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.flow = FlowLayout(self, 8)
        self.chips: list[ChoiceChip] = []
        self._valores: list = []
        sp = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)

    def add(self, text: str, checked: bool = False, value=None, count=None) -> ChoiceChip:
        c = ChoiceChip(text, checked, count)
        c.toggled.connect(lambda *_: (self.flow.invalidate(), self.updateGeometry(), self.changed.emit()))
        self.flow.addWidget(c)
        self.chips.append(c)
        self._valores.append(text if value is None else value)
        self.updateGeometry()
        return c

    def values(self) -> list:
        return [v for c, v in zip(self.chips, self._valores) if c.isChecked()]

    def set_values(self, values: Iterable) -> None:
        values = list(values)
        for c, v in zip(self.chips, self._valores):
            c.setChecked(v in values)

    def hasHeightForWidth(self):  # noqa: N802
        return True

    def heightForWidth(self, w):  # noqa: N802
        return self.flow.heightForWidth(w)

    def sizeHint(self):  # noqa: N802
        w = max(self.width(), 200)
        return QSize(w, self.flow.heightForWidth(w))


# ---------------------------------------------------------------- tarjeta de opción

class OptionCard(QAbstractButton):
    """Opción excluyente con una línea de explicación (``rp-option``, ``role="radio"``)."""

    def __init__(self, title: str, help: str | None = None, value=None, cat: str | None = None,  # noqa: A002
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.cat = cat
        self.value = title if value is None else value
        self.setCheckable(True)
        self.setAutoExclusive(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setAccessibleName(title)
        if help:
            self.setAccessibleDescription(help)
        self._kbd = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(44, 12, 14, 12)
        lay.setSpacing(2)
        self.title_lb = label(title, "OptTitle", wrap=True)
        self.help_lb = label(help or "", "Help", wrap=True)
        for lb in (self.title_lb, self.help_lb):
            lb.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            lay.addWidget(lb)
        self.help_lb.setVisible(bool(help))      # ya con padre (ver SwitchRow)
        sp = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)

    def focusInEvent(self, e):  # noqa: N802
        self._kbd = _foco_teclado(e)
        super().focusInEvent(e)

    def focusOutEvent(self, e):  # noqa: N802
        self._kbd = False
        super().focusOutEvent(e)

    def paintEvent(self, _e):  # noqa: N802
        t = tema.tokens()
        hay_cat = bool(self.cat or cat_de(self))
        ton = _tonos(self, self.cat)
        c = QColor(ton["c"] if hay_cat else t["ink"])
        on = self.isChecked()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            p.setOpacity(0.45)
        if on:
            r = QRectF(self.rect()).adjusted(0.75, 0.75, -0.75, -0.75)
            p.setPen(QPen(c, 1.5))
            p.setBrush(QColor(ton["band"] if hay_cat else t["surface-sunken"]))
        else:
            r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            p.setPen(QPen(QColor(t["line-strong"] if self.underMouse() else t["line"]), 1))
            p.setBrush(QColor(t["surface"]))
        p.drawRoundedRect(r, 12, 12)
        # botón de opción
        rr = QRectF(14, 13, 18, 18)
        if on:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawEllipse(rr)
            p.setBrush(QColor(t["surface"]))
            p.drawEllipse(rr.adjusted(5, 5, -5, -5))
        else:
            p.setPen(QPen(QColor(t["line-control"]), 1))
            p.setBrush(QColor(t["field"]))
            p.drawEllipse(rr.adjusted(0.5, 0.5, -0.5, -0.5))
        if self._kbd and self.hasFocus():
            p.setOpacity(1)
            _anillo(p, QRectF(self.rect()).adjusted(1, 1, -1, -1), 12)
        p.end()


class OptionGroup(QWidget):
    """Lista vertical de ``OptionCard`` excluyentes.  Señal ``changed(valor)``."""

    changed = Signal(object)

    def __init__(self, parent: QWidget | None = None, spacing: int = 8):
        super().__init__(parent)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(spacing)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.cards: list[OptionCard] = []
        self.group.buttonToggled.connect(self._toggled)

    def add(self, title: str, help: str | None = None, value=None, checked: bool = False) -> OptionCard:  # noqa: A002
        card = OptionCard(title, help, value)
        self.group.addButton(card, len(self.cards))
        self.cards.append(card)
        self.lay.addWidget(card)
        if checked or len(self.cards) == 1:
            card.setChecked(True)
        return card

    def _toggled(self, b, on):
        if on:
            self.changed.emit(b.value)

    def value(self):
        b = self.group.checkedButton()
        return b.value if b is not None else None

    def set_value(self, value) -> None:
        for c in self.cards:
            if c.value == value:
                c.setChecked(True)
                return

    def card(self, value) -> OptionCard | None:
        return next((c for c in self.cards if c.value == value), None)


# ---------------------------------------------------------------- aviso

class Note(QFrame):
    """Aviso corto (``rp-note``): tonos info, ok y warn, con su ícono."""

    GLIFOS = {"info": "info", "ok": "check", "warn": "info", "danger": "info"}

    def __init__(self, text: str, tone: str = "info", glyph: str | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Note")
        self.setProperty("tone", tone)
        h = QHBoxLayout(self)
        h.setContentsMargins(12, 10, 12, 10)
        h.setSpacing(10)
        self.icon_lb = QLabel()
        self.icon_lb.setFixedSize(16, 18)
        h.addWidget(self.icon_lb, 0, Qt.AlignmentFlag.AlignTop)
        self.text_lb = label(text, "NoteText", wrap=True)
        self.text_lb.setTextFormat(Qt.TextFormat.AutoText)
        h.addWidget(self.text_lb, 1)
        self._glyph = glyph
        self.set_tone(tone)

    def set_tone(self, tone: str) -> None:
        self.setProperty("tone", tone)
        color = tema.color(tone if tone in ("ok", "warn", "danger", "info") else "info")
        self.icon_lb.setPixmap(line_pixmap(self._glyph or self.GLIFOS.get(tone, "info"), color, 16, 1.9))
        repolish(self)
        repolish(self.text_lb)

    def setText(self, text: str) -> None:  # noqa: N802
        self.text_lb.setText(text)

    def text(self) -> str:
        return self.text_lb.text()


# ---------------------------------------------------------------- ficha de archivo

class FileChip(QFrame):
    """El archivo abierto como ficha (``rp-filechip``): miniatura, nombre, datos y «Cambiar»."""

    change_clicked = Signal()
    remove_clicked = Signal()

    def __init__(self, name: str = "", meta: str = "", removable: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("FileChip")
        self.setFixedHeight(48)
        h = QHBoxLayout(self)
        h.setContentsMargins(10, 0, 8, 0)
        h.setSpacing(10)
        self.thumb = PdfThumb(28, 36)
        h.addWidget(self.thumb)
        col = QVBoxLayout()
        col.setSpacing(0)
        col.setContentsMargins(0, 0, 0, 0)
        self.name_lb = label("", "FileChipName")
        self.meta_lb = label("", "Caption")
        col.addStretch(1)
        col.addWidget(self.name_lb)
        col.addWidget(self.meta_lb)
        col.addStretch(1)
        h.addLayout(col, 1)
        self.b_change = QPushButton("Cambiar")
        self.b_change.setObjectName("Ghost")
        self.b_change.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_change.clicked.connect(self.change_clicked)
        h.addWidget(self.b_change)
        self.b_remove = QToolButton()
        self.b_remove.setObjectName("Ghost")
        self.b_remove.setIcon(QIcon(line_pixmap("close", tema.color("ink-2"), 16, 1.8)))
        self.b_remove.setIconSize(QSize(16, 16))
        self.b_remove.setToolTip("Quitar archivo")
        self.b_remove.setAccessibleName("Quitar archivo")
        self.b_remove.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_remove.clicked.connect(self.remove_clicked)
        h.addWidget(self.b_remove)
        self.b_remove.setVisible(removable)      # ya con padre (ver SwitchRow)
        self._name = ""
        self.max_nombre = 240        # ancho máximo del nombre antes de recortarlo por el medio
        self.set_file(name, meta)

    def set_file(self, name: str, meta: str = "", tooltip: str | None = None) -> None:
        self._name = name
        self.name_lb.setToolTip(tooltip or name)
        self.meta_lb.setText(meta)
        self.thumb.pdf = name.lower().endswith(".pdf") or not name
        self._elide()

    def _elide(self):
        fm = QFontMetrics(tema.fuente("label", self.name_lb.font()))
        self.name_lb.setText(fm.elidedText(self._name, Qt.TextElideMode.ElideMiddle, self.max_nombre))

    def name(self) -> str:
        return self._name


# ---------------------------------------------------------------- pasos

class Step(QWidget):
    """Un paso del panel de ajustes (``rp-step``): número en la banda de la categoría, título ``title-3``,
    una línea de ayuda opcional y sus controles en ``body`` (un QVBoxLayout).

    ``numbered=False`` = bloque sin número ni título (contenido suelto dentro del panel)."""

    def __init__(self, title: str | None, help: str | None = None, numbered: bool = True,  # noqa: A002
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.numbered = numbered and bool(title)
        self.number = 1
        self.done = False
        self.last = False
        self.main = QVBoxLayout(self)
        self.main.setContentsMargins(42 if self.numbered else 0, 0, 0, 24)
        self.main.setSpacing(12)
        self.head = QHBoxLayout()
        self.head.setSpacing(8)
        self.head.setContentsMargins(0, 4, 0, 0)
        self.title_lb = label(title or "", "StepTitle", wrap=True)
        self.head.addWidget(self.title_lb, 1)
        self.help_lb = label(help or "", "Help", wrap=True)
        self.summary_lb = label("", "BodySm", wrap=True)
        self.summary_lb.hide()
        if title:
            head_box = QVBoxLayout()
            head_box.setSpacing(2)
            head_box.addLayout(self.head)
            head_box.addWidget(self.help_lb)
            self.main.addLayout(head_box)
            self.help_lb.setVisible(bool(help))  # ya con padre (ver SwitchRow)
        else:
            self.title_lb.hide()
            self.help_lb.hide()
        self.main.addWidget(self.summary_lb)
        self.body = QVBoxLayout()
        self.body.setSpacing(12)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.main.addLayout(self.body)
        if title:
            self.setAccessibleName(title)

    def add_head_widget(self, w: QWidget) -> QWidget:
        """Algo a la derecha del título (p. ej. un ``Chip`` con «12 datos»)."""
        self.head.addWidget(w, 0, Qt.AlignmentFlag.AlignVCenter)
        return w

    def set_title(self, title: str) -> None:
        self.title_lb.setText(title)

    def set_help(self, text: str | None) -> None:
        self.help_lb.setText(text or "")
        self.help_lb.setVisible(bool(text))

    def set_done(self, done: bool, summary: str | None = None) -> None:
        """Paso terminado: número relleno con palomita y, si se da, un resumen de una línea en lugar de los
        controles (los controles se ocultan mientras está hecho)."""
        self.done = done
        self.summary_lb.setText(summary or "")
        self.summary_lb.setVisible(done and bool(summary))
        for i in range(self.body.count()):
            w = self.body.itemAt(i).widget()
            if w is not None and summary:
                w.setVisible(not done)
        self.update()

    def _set_last(self, last: bool) -> None:
        self.last = last
        m = self.main.contentsMargins()
        self.main.setContentsMargins(m.left(), 0, 0, 4 if last else 24)
        self.update()

    def paintEvent(self, _e):  # noqa: N802
        if not self.numbered:
            return
        t = tema.tokens()
        ton = _tonos(self, None)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        circ = QRectF(0, 0, 28, 28)
        p.setPen(Qt.PenStyle.NoPen)
        if self.done:
            p.setBrush(QColor(ton["c"]))
            p.drawEllipse(circ)
            p.drawPixmap(QPointF(6, 6), line_pixmap("check", t["on-cat"], 16, 2.4))
        else:
            p.setBrush(QColor(ton["band"]))
            p.drawEllipse(circ)
            p.setFont(tema.fuente("step-n", self.font()))
            p.setPen(QColor(ton["ink"]))
            p.drawText(circ, Qt.AlignmentFlag.AlignCenter, str(self.number))
        if not self.last:
            pen = QPen(QColor(t["line"]), 2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawLine(QPointF(14, 34), QPointF(14, max(34.0, self.height() - 6)))
        p.end()


class StepList(QWidget):
    """Los pasos numerados de un panel (``rp-steps``).  Los pasos ocultos no cuentan para la numeración."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(0)
        self.steps: list[Step] = []

    def add_step(self, title: str | None, help: str | None = None) -> Step:  # noqa: A002
        st = Step(title, help, numbered=bool(title))
        st.installEventFilter(self)
        self.lay.addWidget(st)
        self.steps.append(st)
        self.renumerar()
        return st

    def eventFilter(self, obj, e):  # noqa: N802
        if e.type() in (e.Type.ShowToParent, e.Type.HideToParent, e.Type.Show, e.Type.Hide):
            self.renumerar()
        return False

    def renumerar(self) -> None:
        visibles = [s for s in self.steps if not s.isHidden()]
        n = 0
        for s in visibles:
            if s.numbered:
                n += 1
                s.number = n
        for s in self.steps:
            s._set_last(bool(visibles) and s is visibles[-1])
