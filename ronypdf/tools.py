"""Pantallas de cada herramienta."""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QImageReader, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFrame, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QListView, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
                               QSlider, QSpinBox, QStackedWidget, QStyle, QStyledItemDelegate,
                               QTreeWidget, QVBoxLayout, QWidget)

from . import core, tema
from .icons import icon, pixmap
from .widgets import (IMAGE_FILTER, PDF_AND_IMAGES_FILTER, PDF_LOCK, Chip, Choices, ColorButton, Count, DropArea,
                      FileCardDelegate, FileList, Note, OptionGroup, PreviewPane, Segmented, SingleFilePicker,
                      SwitchRow, ToolPage, ask_dir, ask_open, ask_save, cat_de, collect_paths, ctx, label,
                      new_subfolder, pintar_miniatura, qimage_from_render, show_done, suggest_output)

ALL = {"", "todas", "todo", "—"}


def combo(options: list[tuple[str, object]], default: int = 0) -> QComboBox:
    c = QComboBox()
    for text, data in options:
        c.addItem(text, data)
    c.setCurrentIndex(default)
    return c


def spin(lo: int, hi: int, value: int, suffix: str = "") -> QSpinBox:
    s = QSpinBox()
    s.setMinimumHeight(32)
    s.setRange(lo, hi)
    s.setValue(value)
    if suffix:
        s.setSuffix(suffix)
    s.setMinimumWidth(90)
    return s


def pages_edit(placeholder: str = "Todas (o por ejemplo: 1-3, 5, 8-)") -> QLineEdit:
    e = QLineEdit()
    e.setPlaceholderText(placeholder)
    e.setToolTip("Escribe páginas o rangos separados por comas.\n"
                 "Ejemplos: 1-3, 5, 8-   ·   «8-» significa de la 8 al final   ·   vacío = todas")
    return e


def check_spec(parent, spec: str, pages: int | None, groups: bool = False) -> bool:
    if pages is None:
        return True
    try:
        (core.parse_groups if groups else core.parse_pages)(spec, pages)
        return True
    except core.UserError as exc:
        QMessageBox.warning(parent, "Páginas", str(exc))
        return False


def pdf_row(page: QWidget, path: str) -> core.PdfInfo | None:
    if not ctx.passwords.ensure(path, page):
        return None
    try:
        with PDF_LOCK:
            return core.pdf_info(path, ctx.passwords.get(path))
    except core.UserError as exc:
        QMessageBox.warning(page, "Archivo no válido", str(exc))
        return None


def plural(n: int, uno: str, varios: str) -> str:
    return f"{n} {uno if n == 1 else varios}"


def paginas_de(spec: str, total: int | None) -> tuple[list[int] | None, str | None]:
    """Índices de un rango escrito por la persona y, si no se entiende, el mensaje que lo explica."""
    if total is None:
        return None, None
    try:
        return core.parse_pages(spec, total), None
    except core.UserError as exc:
        return None, str(exc)


# ==========================================================================
# Piezas de la plantilla 2.0 propias de estas pantallas
# ==========================================================================

def campo(lay, texto: str, widget: QWidget, ayuda: str | None = None, estrecho: bool = False) -> QWidget:
    """Un dato con su etiqueta encima (``rp-field``).  ``estrecho``: el control no se estira (casillas de
    números, botón de color)."""
    box = QWidget()
    v = QVBoxLayout(box)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(6)
    lb = label(texto, "Label")
    lb.setBuddy(widget)
    v.addWidget(lb)
    if estrecho:
        fila = QHBoxLayout()
        fila.setContentsMargins(0, 0, 0, 0)
        fila.addWidget(widget)
        fila.addStretch(1)
        v.addLayout(fila)
    else:
        v.addWidget(widget)
    if ayuda:
        v.addWidget(label(ayuda, "Help", wrap=True))
    if not widget.accessibleName():
        widget.setAccessibleName(texto)
    box.etiqueta = lb
    if lay is not None:
        lay.addWidget(box)
    return box


def campos(lay, *pares) -> list[QWidget]:
    """Dos campos cortos lado a lado, cada uno con su etiqueta encima."""
    fila = QHBoxLayout()
    fila.setSpacing(12)
    cajas = []
    for texto, w in pares:
        caja = campo(None, texto, w, estrecho=isinstance(w, ColorButton))
        fila.addWidget(caja, 1, Qt.AlignmentFlag.AlignTop)
        cajas.append(caja)
    lay.addLayout(fila)
    return cajas


def boton_color(color: str) -> ColorButton:
    """``ColorButton`` con el ancho que pide su estilo (con el fijo de 64 px quedaba 2 px recortado)."""
    b = ColorButton(color)
    b.setAccessibleName("Color")
    b.ensurePolished()
    b.setFixedWidth(max(64, b.minimumSizeHint().width()))
    return b


def panel_estable(page: ToolPage) -> None:
    """Sin barra de desplazamiento de más en el panel de ajustes.  Para la altura mínima, QScrollArea suma la de
    cada etiqueta partida en renglones a un ancho de referencia (no al real) y pedía más alto del necesario;
    con una altura mínima explícita usa solo la altura para el ancho real (heightForWidth)."""
    page.body.parentWidget().setMinimumHeight(1)


class Avanzadas(QWidget):
    """«Opciones avanzadas»: un botón ``Ghost`` que pliega lo que casi nadie cambia (al final del último paso)."""

    def __init__(self, texto: str = "Opciones avanzadas"):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)
        self.boton = QPushButton(texto)
        self.boton.setObjectName("Ghost")
        self.boton.setCheckable(True)
        self.boton.setCursor(Qt.CursorShape.PointingHandCursor)
        self.boton.toggled.connect(self._plegar)
        v.addWidget(self.boton, 0, Qt.AlignmentFlag.AlignLeft)
        self.cuerpo = QWidget()
        self.lay = QVBoxLayout(self.cuerpo)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(12)
        v.addWidget(self.cuerpo)
        self._plegar(False)

    def _plegar(self, abierto: bool):
        self.cuerpo.setVisible(abierto)
        self.boton.setIcon(icon("up_chevron" if abierto else "chevron_down", tema.color("ink-2"), size=16))


class _SegCombo(Segmented):
    """Segmentos con la parte de la API de ``QComboBox`` que usan ``run()`` y las pruebas: ``currentData``,
    ``currentIndex``, ``setCurrentIndex``, ``findData`` y la señal ``currentIndexChanged``."""

    currentIndexChanged = Signal(int)

    def __init__(self, options, current: int = 0, full: bool = True):
        super().__init__(options, current, full)
        self.changed.connect(lambda k, _v: self.currentIndexChanged.emit(k))

    def currentData(self):  # noqa: N802
        return self.value()

    def currentIndex(self) -> int:  # noqa: N802
        return self.index()

    def setCurrentIndex(self, k: int) -> None:  # noqa: N802
        self.set_index(k)

    def findData(self, data) -> int:  # noqa: N802
        return self._values.index(data) if data in self._values else -1

    def count(self) -> int:
        return len(self.buttons)


class _Lienzo(QWidget):
    """Contenido del área principal con una barra arriba: título, chips de resultado y, debajo, avisos."""

    def __init__(self, titulo: str, contenido: QWidget):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 10)
        v.setSpacing(10)
        self.barra = QHBoxLayout()
        self.barra.setSpacing(8)
        self.titulo = label(titulo, "StepTitle")
        self.barra.addWidget(self.titulo)
        self._chips = QHBoxLayout()
        self._chips.setSpacing(6)
        self.barra.addLayout(self._chips)
        self.barra.addStretch(1)
        v.addLayout(self.barra)
        self.avisos = QVBoxLayout()
        self.avisos.setSpacing(8)
        v.addLayout(self.avisos)
        v.addWidget(contenido, 1)

    def set_chips(self, chips) -> None:
        while self._chips.count():
            it = self._chips.takeAt(0)
            if it.widget():
                it.widget().hide()
                it.widget().deleteLater()
        for c in chips:
            texto, tono = (c, "neutral") if isinstance(c, str) else c
            self._chips.addWidget(Chip(texto, tono))


# ---------------------------------------------------------------- miniaturas de las páginas

class _CeldaPagina(QStyledItemDelegate):
    def __init__(self, grid: "_PageGrid"):
        super().__init__(grid)
        self.grid = grid

    def sizeHint(self, option, index):  # noqa: N802
        return self.grid.celda

    def paint(self, p, option, index):
        g = self.grid
        i = index.data(Qt.ItemDataRole.UserRole)
        t = tema.tokens()
        ton = tema.tonos(cat_de(g))
        r = QRectF(option.rect)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if i is None:                                           # «y 120 páginas más»
            p.setFont(tema.fuente("caption"))
            p.setPen(QColor(t["ink-3"]))
            p.drawText(r.adjusted(8, 0, -8, 0), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                       index.data(Qt.ItemDataRole.DisplayRole))
            p.restore()
            return
        caja = QRectF(r.left() + 14, r.top() + 12, r.width() - 28, r.height() - 42)
        pm = g.thumbs.get(i)
        if pm is not None and not pm.isNull():
            pw, ph = pm.width() / pm.devicePixelRatio(), pm.height() / pm.devicePixelRatio()
        else:
            pw, ph = 1.0, 1.414
        k = min(caja.width() / pw, caja.height() / ph)
        hoja = QRectF(caja.center().x() - pw * k / 2, caja.bottom() - ph * k, pw * k, ph * k)
        marca = g.marca(i)
        if marca is False:
            p.setOpacity(0.35)
        p.setPen(QPen(QColor(ton["c"] if marca else t["line-strong"]), 2 if marca else 1))
        p.setBrush(QColor(Qt.GlobalColor.white))                # la página es blanca en los dos temas
        p.drawRoundedRect(hoja.adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
        if pm is not None and not pm.isNull():
            p.drawPixmap(hoja.adjusted(1.5, 1.5, -1.5, -1.5), pm, QRectF(pm.rect()))
        p.setOpacity(1.0)
        if marca:
            b = QRectF(hoja.left() - 9, hoja.top() - 9, 24, 24)
            par = marca == "✓" or int(marca) % 2 == 1
            p.setPen(QPen(QColor(ton["c"]), 1.5))
            p.setBrush(QColor(ton["c"] if par else ton["band"]))
            p.drawEllipse(b.adjusted(0.75, 0.75, -0.75, -0.75))
            fg = t["on-cat"] if par else ton["ink"]
            if marca == "✓":
                p.drawPixmap(QPointF(b.center().x() - 7, b.center().y() - 7), pixmap("check", fg, 14, 2.4))
            else:
                f = tema.fuente("chip")
                f.setPixelSize(11)
                f.setWeight(f.Weight.Bold)
                p.setFont(f)
                p.setPen(QColor(fg))
                p.drawText(b, Qt.AlignmentFlag.AlignCenter, marca)
        p.setFont(tema.fuente("caption"))
        p.setPen(QColor(t["ink-3"] if marca is False else t["ink-2"]))
        p.drawText(QRectF(r.left(), hoja.bottom() + 6, r.width(), 16), Qt.AlignmentFlag.AlignHCenter, str(i + 1))
        p.restore()


class _PageGrid(QListWidget):
    """Miniaturas de las páginas del PDF para el área principal.  Muestra qué páginas entran en el resultado
    (las demás quedan tenues) y, al dividir, en qué archivo queda cada una (el número de la esquina).

    ``set_marcas(None)``: todas, sin número; ``{índice: "2"}``: número del archivo; ``"✓"``: palomita.
    Con pocas páginas, las miniaturas crecen hasta llenar el espacio."""

    CELDAS = (QSize(232, 320), QSize(172, 240), QSize(132, 188), QSize(104, 156))   # de la más grande a la menor
    MAX_PAGINAS = 300

    def __init__(self):
        super().__init__()
        self.setObjectName("PaginasGrid")
        self.setStyleSheet("QListWidget#PaginasGrid { background: transparent; border: none; }")
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setUniformItemSizes(True)
        self.celda = self.CELDAS[-1]
        self.setGridSize(self.celda)
        self.setSpacing(0)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setItemDelegate(_CeldaPagina(self))
        self.path: str | None = None
        self.password: str | None = None
        self.total = 0
        self.thumbs: dict[int, QPixmap] = {}
        self.marcas: dict[int, str] | None = None
        self._siguiente = 0
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._render)

    def set_document(self, path: str | None, password: str | None, total: int) -> None:
        self.timer.stop()
        self.clear()
        self.thumbs = {}
        self.path, self.password, self.total = path, password, total if path else 0
        self._siguiente = 0
        if not path:
            return
        n = min(total, self.MAX_PAGINAS)
        for i in range(n):
            it = QListWidgetItem()
            it.setData(Qt.ItemDataRole.UserRole, i)
            self.addItem(it)
        if total > n:
            it = QListWidgetItem(f"y {plural(total - n, 'página', 'páginas')} más")
            it.setData(Qt.ItemDataRole.UserRole, None)
            self.addItem(it)
        self._ajustar_celda()
        self.timer.start(0)

    def _ajustar_celda(self):
        """La celda más grande con la que caben todas las páginas sin desplazarse (si no, la más chica)."""
        n = max(1, self.count())
        w, h = self.viewport().width(), self.viewport().height()
        celda = self.CELDAS[-1]
        for c in self.CELDAS:
            cols = max(1, w // c.width())
            if -(-n // cols) * c.height() <= h:
                celda = c
                break
        if celda != self.celda:
            self.celda = celda
            self.setGridSize(celda)
        for k in range(self.count()):
            self.item(k).setSizeHint(celda)

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        if self.count():
            self._ajustar_celda()

    def set_marcas(self, marcas: dict[int, str] | None) -> None:
        self.marcas = marcas
        self.viewport().update()

    def marca(self, i: int):
        return None if self.marcas is None else self.marcas.get(i, False)

    def _render(self):
        fin_total = min(self.total, self.MAX_PAGINAS)
        if not self.path or self._siguiente >= fin_total:
            return
        if ctx.busy or not PDF_LOCK.acquire(blocking=False):
            self.timer.start(150)
            return
        try:
            with core.open_pdf(self.path, self.password) as d:
                fin = min(self._siguiente + 6, fin_total, d.page_count)
                lado = 640 if self.total <= 4 else 400 if self.total <= 12 else 260
                for k in range(self._siguiente, fin):
                    pm = QPixmap.fromImage(qimage_from_render(core.render_page(d, k, lado)))
                    pm.setDevicePixelRatio(2.0)
                    self.thumbs[k] = pm
                self._siguiente = fin if fin > self._siguiente else fin_total
        except Exception:  # noqa: BLE001 — sin miniaturas la herramienta sigue funcionando
            self._siguiente = fin_total
        finally:
            PDF_LOCK.release()
        self.viewport().update()
        if self._siguiente < fin_total:
            self.timer.start(0)


# ---------------------------------------------------------------- listas de varios archivos

@lru_cache(maxsize=512)
def _miniatura_imagen(path: str) -> QPixmap | None:
    """La imagen reducida para la tarjeta (34 × 44, el doble de píxeles para pantallas nítidas)."""
    r = QImageReader(path)
    r.setAutoTransform(True)
    s = r.size()
    if not s.isValid():
        return None
    r.setScaledSize(s.scaled(QSize(68, 88), Qt.AspectRatioMode.KeepAspectRatio))
    img = r.read()
    if img.isNull():
        return None
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(2.0)
    return pm


class _Tarjetas(FileCardDelegate):
    """Tarjetas de archivo (``rp-file``) con la miniatura real de las imágenes y, si se pide, el asa para
    arrastrar y el número de orden (``numerada``).  Con ``editar``, el chip de la derecha se puede tocar."""

    def __init__(self, fl: FileList, meta_fn, chip_fn=None, numerada: bool = False, editar=None):
        super().__init__(fl, meta_fn, chip_fn)
        self.numerada, self.editar = numerada, editar
        self._chips: dict[int, QRectF] = {}

    def paint(self, p, option, index):
        item = self.fl.itemFromIndex(index)
        if item is None:
            return
        t = tema.tokens()
        ton = tema.tonos(cat_de(self.fl))
        r = QRectF(option.rect).adjusted(0.5, 3.5, -0.5, -3.5)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        sel = bool(option.state & QStyle.StateFlag.State_Selected)
        cy = r.center().y()
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(ton["c"] if sel else t["line-strong"] if hover else t["line"]), 1.5 if sel else 1))
        p.setBrush(QColor(ton["band"] if sel else t["surface"]))
        p.drawRoundedRect(r, 12, 12)
        x = r.left() + 10
        if self.numerada:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t["ink-3"]))
            for dx in (0.0, 5.0):
                for dy in (-5.0, 0.0, 5.0):
                    p.drawEllipse(QPointF(x + 2 + dx, cy + dy), 1.3, 1.3)
            x += 16
            caja = Count.pintar(p, QPointF(x + 24, cy), index.row() + 1, quiet=True)
            x = caja.right() + 10
        path = item.data(0, Qt.ItemDataRole.UserRole) or ""
        thumb = QRectF(x, cy - 22, 34, 44)
        img = _miniatura_imagen(path) if core.is_image(path) else None
        if img is not None:
            w, h = img.width() / img.devicePixelRatio(), img.height() / img.devicePixelRatio()
            dest = QRectF(thumb.center().x() - w / 2, thumb.center().y() - h / 2, w, h)
            p.drawPixmap(dest, img, QRectF(img.rect()))
            p.setPen(QPen(QColor(t["line"]), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(dest.adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
        else:
            pintar_miniatura(p, thumb, not core.is_image(path), "PDF")
        x = thumb.right() + 12
        derecha = r.right() - (48 if hover else 12)
        chip = self.chip_fn(item) if self.chip_fn else None
        self._chips.pop(index.row(), None)
        if chip:
            lapiz = 18 if self.editar else 0
            fc = tema.fuente("chip")
            cw = QFontMetrics(fc).horizontalAdvance(chip) + 20 + lapiz
            cr = QRectF(derecha - cw, cy - 13, cw, 26)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(ton["band"] if not sel else t["surface"]))
            p.drawRoundedRect(cr, 13, 13)
            p.setFont(fc)
            p.setPen(QColor(ton["ink"]))
            p.drawText(cr.adjusted(10, 0, -(10 + lapiz), 0), Qt.AlignmentFlag.AlignVCenter, chip)
            if lapiz:
                p.drawPixmap(QPointF(cr.right() - 24, cy - 6), pixmap("pen", ton["ink"], 12, 1.8))
            self._chips[index.row()] = cr
            derecha = cr.left() - 12
        ancho = max(40.0, derecha - x)
        fn = tema.fuente("body-strong")
        p.setFont(fn)
        p.setPen(QColor(t["ink"]))
        nombre = QFontMetrics(fn).elidedText(item.text(0), Qt.TextElideMode.ElideMiddle, int(ancho))
        p.drawText(QRectF(x, r.top() + 10, ancho, 20), Qt.AlignmentFlag.AlignVCenter, nombre)
        fm = tema.fuente("caption")
        p.setFont(fm)
        p.setPen(QColor(t["ink-3"]))
        meta = QFontMetrics(fm).elidedText(self.meta_fn(item) or "", Qt.TextElideMode.ElideRight, int(ancho))
        p.drawText(QRectF(x, r.top() + 30, ancho, 16), Qt.AlignmentFlag.AlignVCenter, meta)
        if hover:
            q = self._quitar(r)
            p.drawPixmap(QPointF(q.center().x() - 8, q.center().y() - 8), pixmap("remove", t["ink-2"], 16, 1.8))
        p.restore()

    def editorEvent(self, event, model, option, index):  # noqa: N802
        if self.editar and event.type() == QEvent.Type.MouseButtonRelease \
                and event.button() == Qt.MouseButton.LeftButton:
            cr = self._chips.get(index.row())
            if cr is not None and cr.contains(event.position()):
                item = self.fl.itemFromIndex(index)
                if item is not None:
                    QTimer.singleShot(0, lambda: self.editar(item))
                return True
        return super().editorEvent(event, model, option, index)


class _FranjaSoltar(QFrame):
    """Franja punteada al pie de una lista: «Arrastra aquí más archivos» (también se puede tocar)."""

    files_dropped = Signal(list)
    clicked = Signal()

    def __init__(self, texto: str, exts: set[str]):
        super().__init__()
        self.exts = exts
        self.setObjectName("DropZone")
        self.setStyleSheet("QFrame#DropZone { margin: 0; border-radius: 12px; }")
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(52)
        h = QHBoxLayout(self)
        h.setContentsMargins(12, 0, 12, 0)
        h.setSpacing(8)
        h.addStretch(1)
        ic = QLabel()
        ic.setPixmap(pixmap("add", tema.color("ink-3"), 18))
        h.addWidget(ic)
        self.texto = label(texto, "Hint")
        h.addWidget(self.texto)
        h.addStretch(1)

    def _hover(self, on: bool):
        self.setProperty("hover", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, e):  # noqa: N802
        if e.mimeData().hasUrls() and collect_paths(e.mimeData().urls(), self.exts):
            e.acceptProposedAction()
            self._hover(True)

    def dragLeaveEvent(self, e):  # noqa: N802
        self._hover(False)

    def dropEvent(self, e):  # noqa: N802
        self._hover(False)
        paths = collect_paths(e.mimeData().urls(), self.exts)
        if paths:
            e.acceptProposedAction()
            self.files_dropped.emit(paths)

    def mouseReleaseEvent(self, e):  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()


def _natural(texto: str):
    """Clave para ordenar «archivo 2» antes que «archivo 10»."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", texto)]


class _ListaVarios(QStackedWidget):
    """Área principal de una herramienta de varios archivos: mientras la lista está vacía, la zona para soltar;
    después, la lista como tarjetas, con su barra (título, orden y «Vaciar lista») y una franja para soltar más.

    El orden se cambia arrastrando (y entonces pasa a «A mano») o con «Nombre» y «Fecha»."""

    def __init__(self, fl: FileList, titulo: str, zona_titulo: str, zona_ayuda: str, tile_key: str, add_cb,
                 ordenar: bool = True, mas: str = "Arrastra aquí más archivos"):
        super().__init__()
        self.fl = fl
        self._ordenando = False
        self.zone = DropArea(zona_titulo, zona_ayuda, fl.exts, multi=True, tile_key=tile_key, zone=True,
                             button_text="Elegir archivos…")
        self.zone.clicked.connect(add_cb)
        self.zone.files_dropped.connect(fl.add_paths)
        lista = QWidget()
        self.lista_lay = QVBoxLayout(lista)
        self.lista_lay.setContentsMargins(14, 12, 14, 12)
        self.lista_lay.setSpacing(8)
        self.barra = QHBoxLayout()
        self.barra.setSpacing(8)
        self.barra.addWidget(label(titulo, "StepTitle"))
        self.barra.addStretch(1)
        self.orden: Segmented | None = None
        if ordenar:
            self.orden = Segmented([("A mano", "mano"), ("Nombre", "nombre"), ("Fecha", "fecha")])
            self.orden.setToolTip("Ordenar por")
            self.orden.setAccessibleName("Ordenar por")
            self.orden.changed.connect(lambda *_: self.ordenar())
            self.barra.addWidget(self.orden)
            fl.viewport().installEventFilter(self)
        self.b_vaciar = QPushButton("Vaciar lista")
        self.b_vaciar.setObjectName("Ghost")
        self.b_vaciar.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_vaciar.clicked.connect(fl.clear_all)
        self.barra.addWidget(self.b_vaciar)
        self.lista_lay.addLayout(self.barra)
        fl.setMinimumHeight(0)
        self.lista_lay.addWidget(fl, 1)
        self.mas = _FranjaSoltar(mas, fl.exts)
        self.mas.files_dropped.connect(fl.add_paths)
        self.mas.clicked.connect(add_cb)
        self.lista_lay.addWidget(self.mas)
        self.addWidget(self.zone)
        self.addWidget(lista)
        fl.changed.connect(self._cambio)

    def eventFilter(self, obj, e):  # noqa: N802
        if e.type() == QEvent.Type.Drop and e.source() is self.fl and self.orden is not None:
            self.orden.set_value("mano")            # arrastrar una fila: el orden ya es a mano
        return False

    def _cambio(self):
        self.setCurrentIndex(1 if self.fl.topLevelItemCount() else 0)
        if not self._ordenando and self.orden is not None and self.orden.value() != "mano":
            self.ordenar()

    def ordenar(self):
        modo = self.orden.value() if self.orden is not None else "mano"
        if modo == "mano" or self._ordenando:
            return
        fl = self.fl
        items = [fl.takeTopLevelItem(0) for _ in range(fl.topLevelItemCount())]

        def fecha(it):
            try:
                return os.path.getmtime(it.data(0, Qt.ItemDataRole.UserRole))
            except OSError:
                return 0.0

        items.sort(key=(lambda it: _natural(it.text(0))) if modo == "nombre" else fecha)
        self._ordenando = True
        try:
            for it in items:
                fl.addTopLevelItem(it)
            fl.changed.emit()
        finally:
            self._ordenando = False


def boton_agregar(texto: str, cb) -> QPushButton:
    b = QPushButton(texto)
    b.setObjectName("Secondary")
    b.setIcon(icon("add", tema.color("ink-2"), size=18))
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.clicked.connect(cb)
    return b


def render_original(picker: SingleFilePicker):
    """La primera página tal como está (para las herramientas que no cambian cómo se ve el documento)."""
    if not picker.path:
        return None
    return core.preview_first_page(picker.path, picker.password, lambda _d: None, 700)


# ==========================================================================
# Unir
# ==========================================================================

class MergePage(ToolPage):
    key = "merge"
    title = "Unir PDF"
    subtitle = "Combina varios PDF, y también fotos, en un solo documento y en el orden que quieras."
    icon_name = "merge"
    action_text = "Unir PDF"

    AYUDA_TODAS = "Se une cada archivo completo."
    AYUDA_ELEGIR = "Toca el chip de cada archivo para elegir sus páginas."

    def build(self):
        self._editor: QLineEdit | None = None
        g = self.step("¿Qué páginas de cada uno?", self.AYUDA_TODAS)
        self.paso_paginas = g.parentWidget()
        self.paginas = Segmented([("Todas", "todas"), ("Elegir por archivo", "elegir")], full=True)
        self.paginas.setAccessibleName("Páginas de cada archivo")
        self.paginas.changed.connect(lambda *_: self._modo_paginas())
        g.addWidget(self.paginas)

        o = self.step("Opciones")
        self.bookmarks = SwitchRow("Un marcador por archivo", "El índice del PDF lleva el nombre de cada uno.",
                                   checked=True)
        o.addWidget(self.bookmarks)

        self.list = FileList(["Archivo", "Páginas", "Rango a incluir", "Tamaño"], core.PDF_EXTS | core.IMAGE_EXTS,
                             self._row, placeholder="Arrastra aquí los PDF o fotos que quieres unir")
        self.list.set_cards(self._meta, self._chip)
        self.list.setItemDelegate(_Tarjetas(self.list, self._meta, self._chip, numerada=True,
                                            editar=self._editar_rango))
        self.list.itemDoubleClicked.connect(lambda it, _c: self._editar_rango(it))
        self.list.itemChanged.connect(self._rango_puesto)
        self.list.verticalScrollBar().valueChanged.connect(lambda *_: self._cerrar_editor())
        self.varios = _ListaVarios(self.list, "Orden de unión", "Arrastra aquí los PDF o fotos que quieres unir",
                                   "o elígelos desde tu equipo. También puedes soltar una carpeta entera.",
                                   tema.azulejo_de(self.key), self.add, mas="Arrastra aquí más PDF o fotos")
        self.set_main(self.varios)
        self.b_add = boton_agregar("Agregar archivos", self.add)
        self.set_header_widget(self.b_add)
        self.list.changed.connect(self.refresh)
        panel_estable(self)

    def _row(self, path: str):
        size = core.human_size(os.path.getsize(path))
        if core.is_image(path):
            return [Path(path).name, "1", "—", size]
        info = pdf_row(self, path)
        if not info:
            return None
        return [Path(path).name, info.pages, "todas", size]

    @staticmethod
    def _meta(it) -> str:
        path = it.data(0, Qt.ItemDataRole.UserRole) or ""
        n = it.text(1)
        tipo = "Imagen" if core.is_image(path) else f"{n} página{'s' if n != '1' else ''}"
        return f"{tipo} · {it.text(3)}"

    def _elegir(self) -> bool:
        return self.paginas.value() == "elegir"

    def _chip(self, it) -> str | None:
        if not self._elegir() or core.is_image(it.data(0, Qt.ItemDataRole.UserRole) or ""):
            return None
        spec = it.text(2).strip()
        return "Todas" if spec.lower() in ALL else f"Págs. {spec}"

    def _modo_paginas(self):
        self.paso_paginas.set_help(self.AYUDA_ELEGIR if self._elegir() else self.AYUDA_TODAS)
        self.list.viewport().update()
        self.refresh()

    def _rango_puesto(self, it, col):
        """Un rango escrito en la columna 2 (con el editor o desde fuera) pasa a «Elegir por archivo»."""
        if col == 2 and it.text(2).strip().lower() not in ALL and not self._elegir():
            self.paginas.set_value("elegir")

    # --- editar el rango de un archivo en la propia tarjeta
    def _editar_rango(self, it):
        if self.list.indexOfTopLevelItem(it) < 0 or core.is_image(it.data(0, Qt.ItemDataRole.UserRole) or ""):
            return
        if not self._elegir():
            self.paginas.set_value("elegir")
        self._cerrar_editor()
        ed = QLineEdit(self.list.viewport())
        ed.setPlaceholderText("Todas (por ejemplo: 1-3, 5, 8-)")
        ed.setToolTip(pages_edit().toolTip())
        ed.setAccessibleName(f"Páginas de «{it.text(0)}»")
        spec = it.text(2).strip()
        ed.setText("" if spec.lower() in ALL else spec)
        r = self.list.visualItemRect(it)
        w = max(150, min(230, r.width() // 2))
        ed.setGeometry(r.right() - 52 - w, r.center().y() - 19, w, 38)
        ed.installEventFilter(self)
        ed.editingFinished.connect(lambda: self._fin_rango(it, ed))
        self._editor = ed
        ed.show()
        ed.setFocus()
        ed.selectAll()

    def eventFilter(self, obj, e):  # noqa: N802
        if obj is self._editor and e.type() == QEvent.Type.KeyPress and e.key() == Qt.Key.Key_Escape:
            self._cerrar_editor(guardar=False)
            return True
        return super().eventFilter(obj, e)

    def _fin_rango(self, it, ed):
        if self._editor is not ed:
            return
        self._editor = None
        texto = ed.text().strip()
        ed.hide()
        ed.deleteLater()
        if self.list.indexOfTopLevelItem(it) >= 0:
            it.setText(2, texto or "todas")
        self.list.setFocus()

    def _cerrar_editor(self, guardar: bool = True):
        ed = self._editor
        if ed is None:
            return
        if guardar:
            ed.editingFinished.emit()
        else:
            self._editor = None
            ed.hide()
            ed.deleteLater()

    # --- datos
    def add(self):
        self.list.add_paths(ask_open(self, True, PDF_AND_IMAGES_FILTER))

    def accept_files(self, paths):
        self.list.add_paths(paths)

    def _error_rango(self) -> tuple[str, str] | None:
        """El primer archivo con un rango que no se entiende: (nombre, motivo)."""
        if not self._elegir():
            return None
        for it in self.list.items():
            spec = it.text(2).strip()
            if spec.lower() in ALL or not it.text(1).isdigit():
                continue
            _idx, error = paginas_de(spec, int(it.text(1)))
            if error:
                return it.text(0), error
        return None

    def ready(self):
        return hasattr(self, "list") and self.list.topLevelItemCount() >= 1 and self._error_rango() is None

    def refresh(self):
        super().refresh()
        if not hasattr(self, "varios"):
            return
        n = self.list.topLevelItemCount()
        self.b_add.setVisible(n > 0)
        if not n:
            self.set_summary([], "Agrega los PDF o las fotos que quieres unir.")
            return
        pages = 0
        for it in self.list.items():
            total = int(it.text(1)) if it.text(1).isdigit() else 0
            if self._elegir() and total and not core.is_image(it.data(0, Qt.ItemDataRole.UserRole) or ""):
                idx, _err = paginas_de(it.text(2), total)
                pages += len(idx) if idx else total
            else:
                pages += total
        chips = [plural(n, "archivo", "archivos"), plural(pages, "página", "páginas")]
        error = self._error_rango()
        if error:
            chips.append(("Rango no válido", "danger"))
            self.set_summary(chips, f"Revisa las páginas de «{error[0]}»: {error[1]}")
        else:
            self.set_summary(chips, "Eliges dónde guardar el PDF nuevo. El original de cada archivo no cambia.")

    def run(self):
        self._cerrar_editor()
        items = []
        elegir = self._elegir()
        for it in self.list.items():
            path = it.data(0, Qt.ItemDataRole.UserRole)
            spec = it.text(2).strip() if elegir else ""
            spec = "" if spec.lower() in ALL else spec
            if spec and not check_spec(self, spec, int(it.text(1))):
                return
            items.append(core.MergeItem(path, spec, ctx.passwords.get(path)))
        first = Path(items[0].path)
        out = ask_save(self, str(core.unique_path(first.with_name("unido.pdf"))))
        if not out:
            return
        ctx.run_task("Uniendo archivos…", core.merge,
                     lambda n: show_done(self, "PDF unido", f"{n} páginas en «{Path(out).name}».", file=out),
                     items=items, output=out, bookmarks=self.bookmarks.isChecked())


# ==========================================================================
# Dividir
# ==========================================================================

class SplitPage(ToolPage):
    key = "split"
    title = "Dividir PDF"
    subtitle = "Separa un PDF en varios archivos o extrae solo las páginas que necesitas."
    icon_name = "split"
    action_text = "Dividir PDF"

    DETALLE = {  # modo → (título del paso 2, ayuda)
        "every": ("¿Cada cuántas páginas?", "Cada archivo nuevo lleva ese número de páginas."),
        "ranges": ("Escribe los rangos", "Separa con comas: cada rango será un archivo."),
        "extract": ("¿Qué páginas quieres?", "Quedan juntas, en este orden, en un solo PDF."),
    }

    def build(self):
        self._capitulos: list[list[int]] = []
        self.picker = SingleFilePicker()
        self.picker.changed.connect(self._file_changed)
        self.use_picker(self.picker, "Arrastra aquí el PDF que quieres dividir",
                        "o elígelo desde tu equipo.")
        self.grid = _PageGrid()
        self.lienzo = _Lienzo("Así quedará", self.grid)
        self.set_main(self.lienzo)

        g = self.step("¿Cómo quieres dividirlo?")
        self.modes = OptionGroup()
        self.r_each = self.modes.add("Cada página por separado", "Un archivo por cada página.", "each")
        self.r_every = self.modes.add("Cada cierto número de páginas", "Por ejemplo, de 2 en 2.", "every")
        self.r_ranges = self.modes.add("Por rangos", "Un archivo por cada rango que escribas.", "ranges")
        self.r_bm = self.modes.add("Por capítulos", "Según los marcadores del índice.", "bm")
        self.r_extract = self.modes.add("Extraer algunas páginas", "Las que elijas, en un solo PDF.", "extract")
        self.r_bm.setEnabled(False)
        self.r_bm.hide()
        self.modes.changed.connect(lambda *_: self._modo())
        g.addWidget(self.modes)
        self.bm_hint = label("", "Help", wrap=True)
        self.bm_hint.hide()
        g.addWidget(self.bm_hint)

        d = self.step(*self.DETALLE["every"])
        self.paso_detalle = d.parentWidget()
        self.n_every = spin(1, 9999, 2, " páginas")
        self.n_every.setAccessibleName("Páginas por archivo")
        self.caja_every = QWidget()
        fila = QHBoxLayout(self.caja_every)
        fila.setContentsMargins(0, 0, 0, 0)
        fila.addWidget(self.n_every)
        fila.addStretch(1)
        self.e_ranges = pages_edit("Por ejemplo: 1-3, 4-10, 11-")
        self.e_ranges.setAccessibleName("Rangos")
        self.e_extract = pages_edit("Por ejemplo: 1, 3, 5-7")
        self.e_extract.setAccessibleName("Páginas que se extraen")
        for w in (self.caja_every, self.e_ranges, self.e_extract):
            d.addWidget(w)
        self.n_every.valueChanged.connect(lambda *_: self.refresh())
        self.e_ranges.textChanged.connect(lambda *_: self.refresh())
        self.e_extract.textChanged.connect(lambda *_: self.refresh())
        self._modo()
        panel_estable(self)

    def accept_files(self, paths):
        self.picker.set_path(paths[0])

    def _file_changed(self, path):
        info = self.picker.info
        has_toc = bool(info and info.has_toc)
        self._capitulos = self._leer_capitulos(path) if has_toc else []
        has_toc = has_toc and bool(self._capitulos)
        self.r_bm.setEnabled(has_toc)
        self.r_bm.setVisible(has_toc)
        if not has_toc and self.r_bm.isChecked():
            self.r_each.setChecked(True)
        self.bm_hint.setText("Este PDF no tiene marcadores, así que no se puede dividir por capítulos.")
        self.bm_hint.setVisible(bool(path) and not has_toc)
        self.grid.set_document(path, self.picker.password, info.pages if info else 0)
        self.refresh()

    def _leer_capitulos(self, path) -> list[list[int]]:
        """Los grupos de páginas que daría «Por capítulos» (los mismos que arma ``core.split_bookmarks``)."""
        try:
            with PDF_LOCK, core.open_pdf(path, self.picker.password) as doc:
                count = doc.page_count
                toc = sorted((p, t) for lvl, t, p in doc.get_toc(simple=True) if lvl == 1 and p >= 1)
        except Exception:  # noqa: BLE001
            return []
        grupos = []
        if toc and toc[0][0] > 1:
            grupos.append(list(range(0, toc[0][0] - 1)))
        for k, (page, _t) in enumerate(toc):
            end = toc[k + 1][0] - 1 if k + 1 < len(toc) else count
            if end >= page:
                grupos.append(list(range(page - 1, end)))
        return grupos

    def _modo(self):
        modo = self.modes.value()
        detalle = self.DETALLE.get(modo)
        self.paso_detalle.setVisible(detalle is not None)
        if detalle:
            paso = self.paso_detalle
            paso.set_title(detalle[0])
            paso.set_help(detalle[1])
        self.caja_every.setVisible(modo == "every")
        self.e_ranges.setVisible(modo == "ranges")
        self.e_extract.setVisible(modo == "extract")
        if hasattr(self, "action"):
            self.action.setText("Extraer páginas" if modo == "extract" else self.action_text)
        self.refresh()

    def _grupos(self) -> tuple[list[list[int]] | None, str | None]:
        """Cómo quedarían las páginas (una lista por archivo) y, si algo no se entiende, el motivo."""
        info = self.picker.info if getattr(self, "picker", None) else None
        if not info:
            return None, None
        n, modo = info.pages, self.modes.value()
        try:
            if modo == "each":
                return [[i] for i in range(n)], None
            if modo == "every":
                k = self.n_every.value()
                return [list(range(s, min(s + k, n))) for s in range(0, n, k)], None
            if modo == "ranges":
                spec = self.e_ranges.text().strip()
                return (core.parse_groups(spec, n), None) if spec else (None, None)
            if modo == "bm":
                return self._capitulos or None, None
            spec = self.e_extract.text().strip()
            return ([core.parse_pages(spec, n)], None) if spec else (None, None)
        except core.UserError as exc:
            return None, str(exc)

    def ready(self):
        if not getattr(self, "picker", None) or not self.picker.path:
            return False
        if self.r_ranges.isChecked() and not self.e_ranges.text().strip():
            return False
        if self.r_extract.isChecked() and not self.e_extract.text().strip():
            return False
        return self._grupos()[1] is None

    def refresh(self):
        super().refresh()
        if not hasattr(self, "paso_detalle"):
            return
        if not self.picker.path:
            self.set_summary([], "Elige el PDF que quieres dividir.")
            return
        grupos, error = self._grupos()
        extraer = self.modes.value() == "extract"
        if grupos is None:
            self.grid.set_marcas({} if error else None)
            self.lienzo.set_chips([])
        elif extraer:
            self.grid.set_marcas({i: "✓" for i in grupos[0]})
            self.lienzo.set_chips([(plural(len(grupos[0]), "página", "páginas"), "cat")])
        else:
            marcas: dict[int, str] = {}
            for k, g in enumerate(grupos, 1):
                for i in g:
                    marcas.setdefault(i, str(k))
            self.grid.set_marcas(marcas)
            self.lienzo.set_chips([(plural(len(grupos), "archivo", "archivos"), "cat")])
        stem = Path(self.picker.path).stem
        if error:
            self.set_summary([("Rango no válido", "danger")], error)
        elif grupos is None:
            self.set_summary([], "Escribe los rangos para ver cómo queda." if not extraer else
                             "Escribe las páginas que quieres extraer.")
        elif extraer:
            self.set_summary([plural(len(grupos[0]), "página", "páginas")],
                             "Eliges dónde guardar el PDF nuevo. El original no cambia.")
        else:
            self.set_summary([plural(len(grupos), "archivo", "archivos")],
                             f"Eliges la carpeta; los archivos van en una subcarpeta «{stem}_dividido».")

    def run(self):
        path, pw = self.picker.path, self.picker.password
        pages = self.picker.info.pages if self.picker.info else None
        stem = Path(path).stem
        if self.r_extract.isChecked():
            spec = self.e_extract.text()
            if not check_spec(self, spec, pages):
                return
            out = ask_save(self, suggest_output(path, "_extracto"))
            if not out:
                return
            ctx.run_task("Extrayendo páginas…", core.extract_pages,
                         lambda n: show_done(self, "Páginas extraídas", f"{n} páginas en «{Path(out).name}».",
                                             file=out),
                         path=path, spec=spec, output=out, password=pw)
            return
        if self.r_ranges.isChecked() and not check_spec(self, self.e_ranges.text(), pages, groups=True):
            return
        parent = ask_dir(self, str(Path(path).parent))
        if not parent:
            return
        folder = new_subfolder(parent, f"{stem}_dividido")
        common = dict(path=path, out_dir=folder, password=pw)
        if self.r_each.isChecked():
            fn, kw = core.split_every, dict(n=1)
        elif self.r_every.isChecked():
            fn, kw = core.split_every, dict(n=self.n_every.value())
        elif self.r_ranges.isChecked():
            fn, kw = core.split_ranges, dict(spec=self.e_ranges.text())
        else:
            fn, kw = core.split_bookmarks, {}

        def done(files):
            show_done(self, "PDF dividido", f"Se crearon {len(files)} archivos en la carpeta "
                                            f"«{Path(folder).name}».", folder=folder)

        ctx.run_task("Dividiendo PDF…", fn, done, **common, **kw)


# ==========================================================================
# Comprimir
# ==========================================================================

LEVELS = [
    # (clave del motor, cómo se llama en pantalla, una línea que explica el resultado) — en el orden en que se ve
    ("media", "Para correo", "Recomendada: se ve bien en pantalla."),
    ("baja", "Para imprimir", "Sin perder calidad. Ahorra menos espacio."),
    ("alta", "Para un portal con límite", "Más liviano: fotos a 110 ppp."),
    ("maxima", "Lo más pequeño posible", "Las fotos pierden nitidez (72 ppp)."),
]


class CompressPage(ToolPage):
    """Ejemplo de referencia de la plantilla 2.0 (ver docs/diseno/PLANTILLA.md)."""

    key = "compress"
    title = "Comprimir PDF"
    subtitle = "Baja el peso de tus PDF para enviarlos por correo o subirlos a un portal."
    icon_name = "compress"
    action_text = "Comprimir PDF"

    def build(self):
        self._resultado: str | None = None

        # Paso 1: tarjetas de opción, porque cada nivel necesita una línea de explicación
        g = self.step("¿Para qué lo necesitas?")
        self.levels = OptionGroup()
        for key, name, desc in LEVELS:
            self.levels.add(name, desc, key)
        g.addWidget(self.levels)

        # Paso 2: un ajuste de sí o no → interruptor
        o = self.step("Ajustes")
        self.gray = SwitchRow("Pasar a escala de grises", "Baja aún más el peso. Útil para escaneos de texto.")
        o.addWidget(self.gray)

        # Área principal: zona para soltar mientras no hay archivos; después, la lista como tarjetas
        self.list = FileList(["Archivo", "Páginas", "Tamaño", "Resultado"], core.PDF_EXTS, self._row,
                             placeholder="Arrastra aquí los PDF que quieres comprimir",
                             allow_duplicates=False)
        self.list.set_cards(self._meta, lambda it: it.text(3) or None)
        self.list.setMinimumHeight(0)
        self.zone = DropArea("Arrastra aquí los PDF que quieres comprimir",
                             "o elígelos desde tu equipo. También puedes soltar una carpeta entera.",
                             core.PDF_EXTS, multi=True, tile_key="compress", zone=True,
                             button_text="Elegir archivos…")
        self.zone.clicked.connect(self.add)
        self.zone.files_dropped.connect(self.list.add_paths)

        lista = QWidget()
        ll = QVBoxLayout(lista)
        ll.setContentsMargins(14, 12, 14, 8)
        ll.setSpacing(6)
        barra = QHBoxLayout()
        barra.addWidget(label("Archivos para comprimir", "StepTitle"))
        barra.addStretch(1)
        b_vaciar = QPushButton("Vaciar lista")
        b_vaciar.setObjectName("Ghost")
        b_vaciar.setCursor(Qt.CursorShape.PointingHandCursor)
        b_vaciar.clicked.connect(self.list.clear_all)
        barra.addWidget(b_vaciar)
        ll.addLayout(barra)
        ll.addWidget(self.list, 1)

        self.main_stack = QStackedWidget()
        self.main_stack.addWidget(self.zone)
        self.main_stack.addWidget(lista)
        self.set_main(self.main_stack)

        # Encabezado: «Agregar archivos» (solo cuando ya hay una lista a la que agregar)
        self.b_add = QPushButton("Agregar archivos")
        self.b_add.setObjectName("Secondary")
        self.b_add.setIcon(icon("add", tema.color("ink-2"), size=18))
        self.b_add.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_add.clicked.connect(self.add)
        self.set_header_widget(self.b_add)

        self.list.changed.connect(self._lista_cambiada)

    def _row(self, path):
        info = pdf_row(self, path)
        if not info:
            return None
        return [Path(path).name, info.pages, core.human_size(info.size), ""]

    @staticmethod
    def _meta(it) -> str:
        n = it.text(1)
        return f"{n} página{'s' if n != '1' else ''} · {it.text(2)}"

    def add(self):
        self.list.add_paths(ask_open(self, True))

    def accept_files(self, paths):
        self.list.add_paths(paths)

    def ready(self):
        return self.list.topLevelItemCount() > 0

    def _lista_cambiada(self):
        self._resultado = None
        self.refresh()

    def refresh(self):
        super().refresh()
        if not hasattr(self, "list"):
            return
        n = self.list.topLevelItemCount()
        self.main_stack.setCurrentIndex(1 if n else 0)
        self.b_add.setVisible(n > 0)
        if not n:
            self.set_summary([], "Agrega uno o varios PDF para empezar.")
            return
        total = sum(os.path.getsize(p) for p in self.list.paths() if os.path.isfile(p))
        chips = [f"{n} archivo{'s' if n != 1 else ''}", core.human_size(total)]
        if self._resultado:
            chips.append((self._resultado, "cat"))
        texto = ("Eliges dónde guardar la copia. El original no cambia." if n == 1 else
                 "Cada copia se guarda junto a su original, con «_comprimido» en el nombre.")
        self.set_summary(chips, texto)

    def run(self):
        items = self.list.items()
        paths = [it.data(0, Qt.ItemDataRole.UserRole) for it in items]
        if len(paths) == 1:
            out = ask_save(self, suggest_output(paths[0], "_comprimido"))
            if not out:
                return
            jobs = [(paths[0], out)]
        else:
            jobs = [(p, suggest_output(p, "_comprimido")) for p in paths]
        level = self.levels.value()
        gray = self.gray.isChecked()
        pws = {p: ctx.passwords.get(p) for p in paths}

        def work(progress):
            results = []
            for k, (src, dst) in enumerate(jobs):
                name = Path(src).name
                sub = (lambda i, n, m, k=k, name=name: progress(k, len(jobs), f"{name} — {m}"))
                before, after = core.compress(src, dst, level, gray, pws[src], progress=sub)
                results.append((src, dst, before, after))
            progress(len(jobs), len(jobs), "Listo")
            return results

        def done(results):
            tb = ta = 0
            for it, (src, dst, before, after) in zip(items, results):
                tb, ta = tb + before, ta + after
                if after < before:
                    it.setText(3, f"{core.human_size(after)}  (−{(1 - after / before) * 100:.0f} %)")
                else:
                    it.setText(3, "Ya estaba optimizado")
                it.setToolTip(3, dst)
            saved = (1 - ta / tb) * 100 if tb else 0
            detail = (f"Antes: {core.human_size(tb)}   →   Después: {core.human_size(ta)}\n"
                      f"Ahorro: {saved:.0f} %")
            if ta >= tb:
                detail = "No fue posible reducir más el tamaño: el archivo ya estaba optimizado."
                self._resultado = "Ya estaba optimizado"
            else:
                self._resultado = f"Después {core.human_size(ta)} · {saved:.0f} % menos"
            self.list.viewport().update()
            self.refresh()
            if len(results) == 1:
                show_done(self, "Compresión terminada", detail, file=results[0][1])
            else:
                show_done(self, f"{len(results)} archivos comprimidos", detail, folder=results[0][1])

        ctx.run_task("Comprimiendo…", lambda progress: work(progress), done)


# ==========================================================================
# PDF → imágenes
# ==========================================================================

class PdfToImagesPage(ToolPage):
    key = "toimage"
    title = "PDF a imágenes"
    subtitle = "Convierte cada página en una imagen PNG o JPG."
    icon_name = "toimage"
    action_text = "Convertir a imágenes"

    AYUDA_FMT = {"png": "Máxima nitidez, ideal para texto.", "jpg": "Más liviano, ideal para fotos."}
    AYUDA_PPP = {72: "72 ppp: para ver en pantalla.", 150: "150 ppp: sirve para casi todo.",
                 200: "200 ppp: para ampliar detalles.", 300: "300 ppp: para imprimir."}

    def build(self):
        self.picker = SingleFilePicker()
        self.picker.changed.connect(self._archivo)
        self.use_picker(self.picker, "Arrastra aquí el PDF que quieres pasar a imágenes",
                        "o elígelo desde tu equipo.")
        self.grid = _PageGrid()
        self.lienzo = _Lienzo("Páginas que se convierten", self.grid)
        self.set_main(self.lienzo)

        f = self.step("¿En qué formato?", self.AYUDA_FMT["png"])
        self.fmt = _SegCombo([("PNG", "png"), ("JPG", "jpg")])
        self.fmt.setAccessibleName("Formato")
        f.addWidget(self.fmt)
        d = self.step("¿Con qué nitidez?", self.AYUDA_PPP[150])
        self.dpi = _SegCombo([("72", 72), ("150", 150), ("200", 200), ("300", 300)], current=1)
        self.dpi.setAccessibleName("Resolución en puntos por pulgada")
        d.addWidget(self.dpi)
        p = self.step("¿Qué páginas?")
        self.pages = pages_edit()
        self.pages.setAccessibleName("Páginas")
        p.addWidget(self.pages)
        self.fmt.changed.connect(lambda _k, v: (f.parentWidget().set_help(self.AYUDA_FMT[v]), self.refresh()))
        self.dpi.changed.connect(lambda _k, v: (d.parentWidget().set_help(self.AYUDA_PPP[v]), self.refresh()))
        self.pages.textChanged.connect(lambda *_: self.refresh())
        panel_estable(self)

    def _archivo(self, path):
        info = self.picker.info
        self.grid.set_document(path, self.picker.password, info.pages if info else 0)
        self.refresh()

    def accept_files(self, paths):
        self.picker.set_path(paths[0])

    def _seleccion(self):
        info = self.picker.info if getattr(self, "picker", None) else None
        return paginas_de(self.pages.text(), info.pages if info else None)

    def ready(self):
        return bool(getattr(self, "picker", None) and self.picker.path) and self._seleccion()[1] is None

    def refresh(self):
        super().refresh()
        if not hasattr(self, "pages"):
            return
        if not self.picker.path:
            self.set_summary([], "Elige el PDF que quieres pasar a imágenes.")
            return
        idx, error = self._seleccion()
        if error:
            self.grid.set_marcas({})
            self.lienzo.set_chips([])
            self.set_summary([("Páginas no válidas", "danger")], error)
            return
        idx = idx or []
        self.grid.set_marcas(None if not self.pages.text().strip() else {i: "✓" for i in idx})
        self.lienzo.set_chips([(plural(len(idx), "imagen", "imágenes"), "cat")])
        self.set_summary([plural(len(idx), "imagen", "imágenes"),
                          f"{self.fmt.currentData().upper()} · {self.dpi.currentData()} ppp"],
                         "Eliges la carpeta; las imágenes van en una subcarpeta nueva.")

    def run(self):
        path = self.picker.path
        spec = self.pages.text()
        if not check_spec(self, spec, self.picker.info.pages if self.picker.info else None):
            return
        parent = ask_dir(self, str(Path(path).parent))
        if not parent:
            return
        folder = new_subfolder(parent, f"{Path(path).stem}_imagenes")
        ctx.run_task("Convirtiendo a imágenes…", core.pdf_to_images,
                     lambda files: show_done(self, "Conversión terminada",
                                             f"Se crearon {len(files)} imágenes.", folder=folder),
                     path=path, out_dir=folder, fmt=self.fmt.currentData(), dpi=self.dpi.currentData(),
                     spec=spec, password=self.picker.password)


# ==========================================================================
# Imágenes → PDF
# ==========================================================================

class ImagesToPdfPage(ToolPage):
    key = "fromimage"
    title = "Imágenes a PDF"
    subtitle = "Convierte fotos y escaneos (JPG, PNG, TIFF…) en un solo PDF, una imagen por página."
    icon_name = "fromimage"
    action_text = "Crear PDF"

    def build(self):
        s = self.step("¿De qué tamaño son las páginas?")
        self.papel = _SegCombo([("A4", "A4"), ("Carta", "Carta"), ("Oficio", "Oficio")])
        self.papel.setAccessibleName("Tamaño de página")
        s.addWidget(self.papel)
        self.ajustar = SwitchRow("Del tamaño de cada imagen", "Cada página mide lo mismo que su foto.")
        s.addWidget(self.ajustar)

        o = self.step("Orientación y margen", "Auto gira la hoja según la forma de cada imagen.")
        self.orient = _SegCombo([("Auto", "auto"), ("Vertical", "vertical"), ("Horizontal", "horizontal")])
        self.campo_orient = campo(o, "Orientación", self.orient)
        self.margin = _SegCombo([("Sin", 0), ("Pequeño", 18), ("Grande", 42)], current=1)
        campo(o, "Margen", self.margin)
        self.paso_orient = o.parentWidget()
        self.ajustar.toggled.connect(self._ajustar)

        self.list = FileList(["Imagen", "Dimensiones", "Tamaño"], core.IMAGE_EXTS, self._row,
                             placeholder="Arrastra aquí tus imágenes")
        self.list.set_cards(lambda it: f"{it.text(1)} · {it.text(2)}")
        self.list.setItemDelegate(_Tarjetas(self.list, lambda it: f"{it.text(1)} · {it.text(2)}", numerada=True))
        self.varios = _ListaVarios(self.list, "En este orden", "Arrastra aquí las fotos o escaneos",
                                   "o elígelos desde tu equipo. También puedes soltar una carpeta entera.",
                                   tema.azulejo_de(self.key), self.add, mas="Arrastra aquí más imágenes")
        self.set_main(self.varios)
        self.b_add = boton_agregar("Agregar imágenes", self.add)
        self.set_header_widget(self.b_add)
        self.list.changed.connect(self.refresh)
        panel_estable(self)

    def _ajustar(self, on: bool):
        self.papel.setVisible(not on)
        self.campo_orient.setVisible(not on)            # la orientación solo aplica a un tamaño de papel
        self.paso_orient.set_title("Margen" if on else "Orientación y margen")
        self.paso_orient.set_help(None if on else "Auto gira la hoja según la forma de cada imagen.")

    def _tamano(self) -> str:
        return "imagen" if self.ajustar.isChecked() else self.papel.currentData()

    def _row(self, path):
        reader = QImageReader(path)
        sz = reader.size()
        if not sz.isValid():
            with PDF_LOCK:
                try:
                    with core.open_pdf(path) as d:
                        r = d[0].rect
                    dims = f"{r.width:.0f} × {r.height:.0f} pt"
                except core.UserError:
                    QMessageBox.warning(self, "Imagen no válida", f"No se pudo leer «{Path(path).name}».")
                    return None
        else:
            dims = f"{sz.width()} × {sz.height()} px"
        return [Path(path).name, dims, core.human_size(os.path.getsize(path))]

    def add(self):
        self.list.add_paths(ask_open(self, True, IMAGE_FILTER, "Elige las imágenes"))

    def accept_files(self, paths):
        self.list.add_paths(paths)

    def ready(self):
        return hasattr(self, "list") and self.list.topLevelItemCount() > 0

    def refresh(self):
        super().refresh()
        if not hasattr(self, "varios"):
            return
        n = self.list.topLevelItemCount()
        self.b_add.setVisible(n > 0)
        if not n:
            self.set_summary([], "Agrega las imágenes que quieres juntar en un PDF.")
            return
        tam = "Tamaño de cada imagen" if self._tamano() == "imagen" else self._tamano()
        self.set_summary([plural(n, "página", "páginas"), tam],
                         "Eliges dónde guardar el PDF. La orientación de las fotos del celular se corrige sola.")

    def run(self):
        paths = self.list.paths()
        out = ask_save(self, str(core.unique_path(Path(paths[0]).with_name("imagenes.pdf"))))
        if not out:
            return
        ctx.run_task("Creando PDF…", core.images_to_pdf,
                     lambda n: show_done(self, "PDF creado", f"{n} páginas en «{Path(out).name}».", file=out),
                     images=paths, output=out, page_size=self._tamano(),
                     orientation=self.orient.currentData(), margin=self.margin.currentData())


# ==========================================================================
# Marca de agua
# ==========================================================================

class _PreviewToolPage(ToolPage):
    """Herramienta de un PDF con la vista previa del resultado en el área principal."""

    drop_title = "Arrastra aquí el PDF"

    def build_layout(self) -> None:
        self.picker = SingleFilePicker()
        self.picker.changed.connect(lambda *_: (self.refresh(), self.preview.schedule()))
        self.use_picker(self.picker, self.drop_title, "o elígelo desde tu equipo.")
        self.preview = PreviewPane(self.render_preview)
        self.set_main(self.preview)

    def watch(self, *widgets):
        for w in widgets:
            for sig in ("changed", "textChanged", "valueChanged", "currentIndexChanged", "toggled"):
                s = getattr(w, sig, None)
                if s is not None and hasattr(s, "connect"):
                    try:
                        s.connect(lambda *_: (self.preview.schedule(), self.refresh()))
                        break
                    except (TypeError, RuntimeError):
                        continue

    def accept_files(self, paths):
        self.picker.set_path(paths[0])

    def render_preview(self):
        raise NotImplementedError

    def _paginas_marcadas(self, spec: str) -> tuple[int | None, str | None]:
        info = self.picker.info
        idx, error = paginas_de(spec, info.pages if info else None)
        return (len(idx) if idx is not None else None), error


GRIS_MARCA = QColor(140, 145, 153).name()      # color con el que sale la marca de agua (del documento)
NEGRO = QColor(Qt.GlobalColor.black).name()
RENGLON = QColor(217, 220, 227)                # renglones grises dibujados sobre una hoja (blanca en los dos temas)


class WatermarkPage(_PreviewToolPage):
    key = "watermark"
    title = "Marca de agua"
    subtitle = "Estampa un texto como «CONFIDENCIAL» o «BORRADOR» sobre las páginas."
    icon_name = "watermark"
    action_text = "Aplicar marca de agua"
    drop_title = "Arrastra aquí el PDF que quieres marcar"

    def build(self):
        self.build_layout()
        t = self.step("¿Qué texto quieres estampar?")
        self.text = QLineEdit("CONFIDENCIAL")
        self.text.setPlaceholderText("Por ejemplo: BORRADOR")
        self.text.setAccessibleName("Texto de la marca")
        t.addWidget(self.text)
        self.bold = SwitchRow("Negrita", checked=True)
        t.addWidget(self.bold)

        d = self.step("¿Dónde y cómo se ve?")
        self.pos = combo([("Centro, en diagonal", "centro"), ("Mosaico (repetida)", "mosaico"),
                          ("Arriba", "superior"), ("Abajo", "inferior")])
        campo(d, "Posición", self.pos)
        self.size = spin(8, 300, 60, " pt")
        self.angle = spin(-90, 90, 45, "°")
        _c_size, self.campo_angle = campos(d, ("Tamaño", self.size), ("Ángulo", self.angle))
        self.color = boton_color(GRIS_MARCA)
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(5, 100)
        self.opacity.setValue(30)
        self.opacity.setAccessibleName("Opacidad")
        self.op_label = label("30 %", "BodySm")
        self.op_label.setMinimumWidth(40)
        self.opacity.valueChanged.connect(lambda v: self.op_label.setText(f"{v} %"))
        op = QWidget()
        op_row = QHBoxLayout(op)
        op_row.setContentsMargins(0, 0, 0, 0)
        op_row.addWidget(self.opacity, 1)
        op_row.addWidget(self.op_label)
        campos(d, ("Color", self.color), ("Opacidad", op))

        p = self.step("¿En qué páginas?")
        self.pages = pages_edit()
        self.pages.setAccessibleName("Páginas")
        p.addWidget(self.pages)
        self.pos.currentIndexChanged.connect(
            lambda: self.campo_angle.setVisible(self.pos.currentData() in ("centro", "mosaico")))
        self.pages.textChanged.connect(lambda *_: self.refresh())
        self.watch(self.text, self.pos, self.size, self.angle, self.color, self.opacity, self.bold)
        panel_estable(self)

    def opts(self):
        return dict(fontsize=self.size.value(), color=self.color.rgb(), opacity=self.opacity.value() / 100,
                    angle=self.angle.value(), position=self.pos.currentData(), bold=self.bold.isChecked())

    def render_preview(self):
        if not self.picker.path or not self.text.text().strip():
            return None
        return core.preview_first_page(self.picker.path, self.picker.password,
                                       lambda d: core.apply_watermark(d, self.text.text(), **self.opts()), 700)

    def ready(self):
        return bool(getattr(self, "picker", None) and self.picker.path and self.text.text().strip()) \
            and self._paginas_marcadas(self.pages.text())[1] is None

    def refresh(self):
        super().refresh()
        if not hasattr(self, "pages"):
            return
        if not self.picker.path:
            self.set_summary([], "Elige el PDF que quieres marcar.")
            return
        n, error = self._paginas_marcadas(self.pages.text())
        if error:
            self.set_summary([("Páginas no válidas", "danger")], error)
        elif not self.text.text().strip():
            self.set_summary([], "Escribe el texto de la marca para continuar.")
        else:
            self.set_summary([plural(n or 0, "página", "páginas")],
                             "Eliges dónde guardar la copia marcada. El original no cambia.")

    def run(self):
        path = self.picker.path
        if not check_spec(self, self.pages.text(), self.picker.info.pages if self.picker.info else None):
            return
        out = ask_save(self, suggest_output(path, "_marca"))
        if not out:
            return
        ctx.run_task("Aplicando marca de agua…", core.watermark,
                     lambda n: show_done(self, "Marca de agua aplicada", f"Se marcaron {n} páginas.", file=out),
                     path=path, output=out, text=self.text.text(), spec=self.pages.text(),
                     password=self.picker.password, **self.opts())


# ==========================================================================
# Numerar páginas
# ==========================================================================

FORMATS = [("1, 2, 3…", "{n}"), ("Página 1", "Página {n}"), ("Página 1 de 10", "Página {n} de {total}"),
           ("1 / 10", "{n} / {total}"), ("- 1 -", "- {n} -"), ("Personalizado…", None)]
POSITIONS = [("Abajo, al centro", "abajo-centro"), ("Abajo, a la derecha", "abajo-derecha"),
             ("Abajo, a la izquierda", "abajo-izquierda"), ("Arriba, al centro", "arriba-centro"),
             ("Arriba, a la derecha", "arriba-derecha"), ("Arriba, a la izquierda", "arriba-izquierda")]
MM = 72 / 25.4


class _PosicionPagina(QWidget):
    """Una hoja con seis lugares (arriba o abajo; a la izquierda, al centro o a la derecha): se toca uno para
    poner ahí el número.  Flechas del teclado para moverlo.  Tiene la API de ``QComboBox`` que usan ``run()``
    y las pruebas (``currentData``, ``currentIndex``, ``setCurrentIndex``, ``findData``,
    ``currentIndexChanged``)."""

    currentIndexChanged = Signal(int)
    HOJA = QRectF(2, 2, 92, 130)

    def __init__(self, opciones: list[tuple[str, str]]):
        super().__init__()
        self.opciones = opciones
        self._k = 0
        self._hover = -1
        self._kbd = False
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(int(self.HOJA.bottom()) + 4)
        self.setMinimumWidth(200)
        self.setAccessibleName("Posición del número")
        self.setToolTip("Toca un lugar de la hoja para poner ahí el número")

    # --- API de QComboBox
    def currentData(self):  # noqa: N802
        return self.opciones[self._k][1]

    def currentText(self) -> str:  # noqa: N802
        return self.opciones[self._k][0]

    def currentIndex(self) -> int:  # noqa: N802
        return self._k

    def setCurrentIndex(self, k: int) -> None:  # noqa: N802
        if 0 <= k < len(self.opciones) and k != self._k:
            self._k = k
            self.setAccessibleDescription(self.currentText())
            self.update()
            self.currentIndexChanged.emit(k)

    def findData(self, data) -> int:  # noqa: N802
        return next((k for k, (_t, v) in enumerate(self.opciones) if v == data), -1)

    def count(self) -> int:
        return len(self.opciones)

    # --- dibujo
    def _lugar(self, k: int) -> QRectF:
        fila, col = self.opciones[k][1].split("-")
        h = self.HOJA
        x = {"izquierda": h.left() + 7, "centro": h.center().x() - 11, "derecha": h.right() - 29}[col]
        y = h.top() + 7 if fila == "arriba" else h.bottom() - 23
        return QRectF(x, y, 22, 16)

    def _en(self, pos) -> int:
        for k in range(len(self.opciones)):
            if self._lugar(k).adjusted(-4, -4, 4, 4).contains(pos):
                return k
        return -1

    def paintEvent(self, _e):  # noqa: N802
        t = tema.tokens()
        ton = tema.tonos(cat_de(self))
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        h = self.HOJA
        p.setPen(QPen(QColor(t["line-strong"]), 1))
        p.setBrush(QColor(Qt.GlobalColor.white))
        p.drawRoundedRect(h.adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(RENGLON)                                         # la hoja es blanca en los dos temas
        for k, w in enumerate((56, 68, 50, 68, 62, 40, 60)):        # renglones de texto de mentiras
            p.drawRoundedRect(QRectF(h.left() + 12, h.top() + 34 + k * 10, w, 4), 2, 2)
        for k in range(len(self.opciones)):
            r = self._lugar(k)
            if k == self._k:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(ton["c"]))
                p.drawRoundedRect(r, 5, 5)
                f = tema.fuente("chip")
                f.setPixelSize(11)
                p.setFont(f)
                p.setPen(QColor(t["on-cat"]))
                p.drawText(r, Qt.AlignmentFlag.AlignCenter, "1")
            else:
                pen = QPen(QColor(ton["c"] if k == self._hover else t["line-control"]), 1.2)
                pen.setStyle(Qt.PenStyle.DashLine)
                p.setPen(pen)
                p.setBrush(QColor(ton["band"]) if k == self._hover else Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(r.adjusted(0.6, 0.6, -0.6, -0.6), 5, 5)
        if self._kbd and self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(t["focus"]), 2))
            p.drawRoundedRect(self._lugar(self._k).adjusted(-3, -3, 3, 3), 7, 7)
        x = h.right() + 16
        p.setFont(tema.fuente("body-strong"))
        p.setPen(QColor(t["ink"]))
        ancho = self.width() - x
        p.drawText(QRectF(x, h.center().y() - 22, ancho, 22), Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(tema.fuente("body-strong")).elidedText(self.currentText(),
                                                                      Qt.TextElideMode.ElideRight, int(ancho)))
        p.setFont(tema.fuente("caption"))
        p.setPen(QColor(t["ink-3"]))
        p.drawText(QRectF(x, h.center().y() + 2, ancho, 34), Qt.TextFlag.TextWordWrap,
                   "Toca otro lugar de la hoja para moverlo.")
        p.end()

    def mousePressEvent(self, e):  # noqa: N802
        k = self._en(e.position())
        if k >= 0:
            self._kbd = False
            self.setCurrentIndex(k)
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):  # noqa: N802
        k = self._en(e.position())
        if k != self._hover:
            self._hover = k
            self.update()

    def leaveEvent(self, e):  # noqa: N802
        self._hover = -1
        self.update()
        super().leaveEvent(e)

    def focusInEvent(self, e):  # noqa: N802
        self._kbd = e.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason)
        super().focusInEvent(e)
        self.update()

    def focusOutEvent(self, e):  # noqa: N802
        self._kbd = False
        super().focusOutEvent(e)
        self.update()

    def keyPressEvent(self, e):  # noqa: N802
        fila, col = self.currentData().split("-")
        cols = ["izquierda", "centro", "derecha"]
        k = e.key()
        if k in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            col = cols[max(0, min(2, cols.index(col) + (1 if k == Qt.Key.Key_Right else -1)))]
        elif k in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            fila = "arriba" if k == Qt.Key.Key_Up else "abajo"
        else:
            super().keyPressEvent(e)
            return
        self._kbd = True
        self.setCurrentIndex(self.findData(f"{fila}-{col}"))
        self.update()


class PageNumbersPage(_PreviewToolPage):
    key = "numbers"
    title = "Numerar páginas"
    subtitle = "Agrega números de página con el formato y la posición que prefieras."
    icon_name = "numbers"
    action_text = "Numerar páginas"
    drop_title = "Arrastra aquí el PDF que quieres numerar"

    def build(self):
        self.build_layout()
        f = self.step("¿Qué formato?")
        self.paso_fmt = f.parentWidget()
        self.fmt = combo(FORMATS)
        self.fmt.setAccessibleName("Formato del número")
        self.custom = QLineEdit("Pág. {n} de {total}")
        self.custom.setToolTip("Usa {n} para el número de página y {total} para el total.")
        self.custom.setAccessibleName("Formato personalizado")
        self.custom.setVisible(False)
        self.fmt.currentIndexChanged.connect(self._formato)
        f.addWidget(self.fmt)
        f.addWidget(self.custom)

        p = self.step("¿Dónde va el número?")
        self.pos = _PosicionPagina(POSITIONS)
        p.addWidget(self.pos)

        a = self.step("Ajustes")
        self.size = spin(6, 72, 11, " pt")
        self.start = spin(1, 99999, 1)
        campos(a, ("Tamaño", self.size), ("Empezar en", self.start))
        self.margin = spin(3, 60, 10, " mm")
        self.color = boton_color(NEGRO)
        campos(a, ("Margen", self.margin), ("Color", self.color))
        self.bold = SwitchRow("Negrita")
        self.skip_first = SwitchRow("Sin número en la portada", "La primera página queda sin número.")
        a.addWidget(self.bold)
        a.addWidget(self.skip_first)
        self.watch(self.fmt, self.custom, self.pos, self.size, self.margin, self.start, self.color,
                   self.bold, self.skip_first)
        panel_estable(self)

    def _formato(self):
        propio = self.fmt.currentData() is None
        self.custom.setVisible(propio)
        self.paso_fmt.set_help("Usa {n} para el número de página y {total} para el total." if propio else None)

    def fmt_text(self) -> str:
        return self.fmt.currentData() or self.custom.text()

    def opts(self):
        return dict(position=self.pos.currentData(), fontsize=self.size.value(),
                    margin=self.margin.value() * MM, color=self.color.rgb(), bold=self.bold.isChecked())

    def render_preview(self):
        info = self.picker.info
        if not self.picker.path or not info:
            return None
        first = 1 if self.skip_first.isChecked() and info.pages > 1 else 0
        total = info.pages - (1 if self.skip_first.isChecked() else 0) + self.start.value() - 1
        text = core.page_number_text(self.fmt_text(), self.start.value(), total)
        return core.preview_first_page(self.picker.path, self.picker.password,
                                       lambda d: core.stamp_page_number(d[0], text, **self.opts()),
                                       700, index=first)

    def ready(self):
        return bool(getattr(self, "picker", None) and self.picker.path and self.fmt_text().strip())

    def refresh(self):
        super().refresh()
        if not hasattr(self, "skip_first"):
            return
        info = self.picker.info
        if not self.picker.path or not info:
            self.set_summary([], "Elige el PDF que quieres numerar.")
            return
        n = info.pages - (1 if self.skip_first.isChecked() and info.pages > 1 else 0)
        self.set_summary([plural(n, "página numerada", "páginas numeradas")],
                         "Eliges dónde guardar la copia numerada. El original no cambia.")

    def run(self):
        try:
            core.page_number_text(self.fmt_text(), 1, 1)
        except core.UserError as exc:
            QMessageBox.warning(self, "Formato", str(exc))
            return
        path = self.picker.path
        out = ask_save(self, suggest_output(path, "_numerado"))
        if not out:
            return
        ctx.run_task("Numerando páginas…", core.page_numbers,
                     lambda n: show_done(self, "Páginas numeradas", f"Documento de {n} páginas.", file=out),
                     path=path, output=out, password=self.picker.password, fmt=self.fmt_text(),
                     start=self.start.value(), skip_first=self.skip_first.isChecked(), **self.opts())


# ==========================================================================
# Proteger / desbloquear
# ==========================================================================

class ProtectPage(ToolPage):
    key = "protect"
    title = "Proteger con contraseña"
    subtitle = "Cifra el PDF (AES de 256 bits) para que solo se abra con contraseña."
    icon_name = "lock"
    action_text = "Proteger PDF"

    def build(self):
        self.picker = SingleFilePicker()
        self.picker.changed.connect(lambda *_: (self.refresh(), self.preview.schedule()))
        self.use_picker(self.picker, "Arrastra aquí el PDF que quieres proteger", "o elígelo desde tu equipo.")
        self.preview = PreviewPane(lambda: render_original(self.picker))
        self.set_main(self.preview)

        a = self.step("Elige la contraseña", "Sin ella no se podrá abrir: guárdala bien.")
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.pw.setPlaceholderText("Contraseña para abrir el PDF")
        campo(a, "Contraseña", self.pw)
        self.pw2 = QLineEdit()
        self.pw2.setEchoMode(QLineEdit.EchoMode.Password)
        self.pw2.setPlaceholderText("Repite la contraseña")
        campo(a, "Confirma la contraseña", self.pw2)
        self.show_pw = QCheckBox("Mostrar las contraseñas")
        self.show_pw.toggled.connect(self._toggle_echo)
        a.addWidget(self.show_pw)

        b = self.step("¿Qué puede hacer quien lo abra?", "Toca cada acción para permitirla o no.")
        self.permisos = Choices()
        self.p_print = self.permisos.add("Imprimir", True, "imprimir")
        self.p_copy = self.permisos.add("Copiar contenido", True, "copiar")
        self.p_copy.setToolTip("Copiar el texto y las imágenes")
        self.p_annot = self.permisos.add("Comentar y rellenar", True, "comentar")
        self.p_modify = self.permisos.add("Modificar", False, "modificar")
        self.permisos.setAccessibleName("Permisos")
        b.addWidget(self.permisos)
        adv = Avanzadas()
        self.owner = QLineEdit()
        self.owner.setEchoMode(QLineEdit.EchoMode.Password)
        self.owner.setPlaceholderText("Opcional")
        campo(adv.lay, "Contraseña de propietario", self.owner,
              "Permite cambiar los permisos más adelante. Si la dejas vacía, se crea una al azar y los permisos "
              "quedan fijos.")
        b.addWidget(adv)
        self.avanzadas = adv
        for e in (self.pw, self.pw2):
            e.textChanged.connect(self.refresh)
        panel_estable(self)

    def _toggle_echo(self, on):
        mode = QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
        for e in (self.pw, self.pw2, self.owner):
            e.setEchoMode(mode)

    def accept_files(self, paths):
        self.picker.set_path(paths[0])

    def ready(self):
        if not getattr(self, "picker", None) or not self.picker.path:
            return False
        return bool(self.pw.text()) and self.pw.text() == self.pw2.text()

    def refresh(self):
        super().refresh()
        if not hasattr(self, "permisos"):
            return
        mismatch = bool(self.pw2.text()) and self.pw.text() != self.pw2.text()
        if bool(self.pw2.property("error")) != mismatch:
            self.pw2.setProperty("error", mismatch)
            self.pw2.style().unpolish(self.pw2)
            self.pw2.style().polish(self.pw2)
        if not self.picker.path:
            self.set_summary([], "Elige el PDF que quieres proteger.")
        elif mismatch:
            self.set_summary([("No coinciden", "danger")], "Las contraseñas no coinciden")
        elif not self.ready():
            self.set_summary([], "Escribe la contraseña dos veces para continuar.")
        else:
            self.set_summary([("Se abre con contraseña", "cat")],
                             "Eliges dónde guardar la copia protegida. El original no cambia.")

    def run(self):
        path = self.picker.path
        out = ask_save(self, suggest_output(path, "_protegido"))
        if not out:
            return
        ctx.run_task("Protegiendo PDF…", core.protect,
                     lambda _: show_done(self, "PDF protegido",
                                         "Guarda la contraseña en un lugar seguro: sin ella no se podrá abrir.",
                                         file=out),
                     path=path, output=out, user_pw=self.pw.text(), owner_pw=self.owner.text(),
                     allow_print=self.p_print.isChecked(), allow_copy=self.p_copy.isChecked(),
                     allow_modify=self.p_modify.isChecked(), allow_annotate=self.p_annot.isChecked(),
                     password=self.picker.password)


class UnlockPage(ToolPage):
    key = "unlock"
    title = "Quitar contraseña"
    subtitle = "Guarda una copia del PDF sin contraseña ni restricciones."
    icon_name = "unlock"
    action_text = "Quitar protección"

    INICIO = "Elige el PDF protegido. Se te pedirá la contraseña."

    def build(self):
        self.protected = False
        self.picker = SingleFilePicker(title="Arrastra aquí el PDF protegido")
        self.picker.changed.connect(self._changed)
        self.use_picker(self.picker, "Arrastra aquí el PDF protegido", "o elígelo desde tu equipo. "
                                                                       "Se te pedirá la contraseña.")
        self.preview = PreviewPane(lambda: render_original(self.picker))
        self.set_main(self.preview)
        s = self.step("Revisa el archivo", "La copia se abre sin contraseña. El original no cambia.")
        self.status = Note(self.INICIO, "info")
        s.addWidget(self.status)
        panel_estable(self)

    def _changed(self, path):
        self.protected = False
        if not path:
            self.status.set_tone("info")
            self.status.setText(self.INICIO)
        else:
            with PDF_LOCK:
                needs = core.needs_password(path)
            restricted = bool(self.picker.info and self.picker.info.encrypted)
            if needs:
                self.protected = True
                self.status.set_tone("ok")
                self.status.setText("Contraseña verificada.")
            elif restricted:
                self.protected = True
                self.status.set_tone("info")
                self.status.setText("Este PDF se abre sin contraseña pero tiene restricciones (imprimir, "
                                    "copiar…). Se guardará una copia sin restricciones.")
            else:
                self.status.set_tone("warn")
                self.status.setText("Este PDF no está protegido: no hace falta quitarle nada.")
        self.preview.schedule()
        self.refresh()

    def accept_files(self, paths):
        self.picker.set_path(paths[0])

    def ready(self):
        return bool(getattr(self, "picker", None) and self.picker.path and self.protected)

    def refresh(self):
        super().refresh()
        if not hasattr(self, "status"):
            return
        if not self.picker.path:
            self.set_summary([], "Elige el PDF al que quieres quitarle la contraseña.")
        elif not self.protected:
            self.set_summary([("Sin protección", "warn")], "Este PDF ya se abre sin contraseña.")
        else:
            self.set_summary([("Lista para quitar", "ok")], "Eliges dónde guardar la copia. El original no cambia.")

    def run(self):
        path = self.picker.path
        out = ask_save(self, suggest_output(path, "_desbloqueado"))
        if not out:
            return
        ctx.run_task("Quitando la protección…", core.unlock,
                     lambda _: show_done(self, "Protección eliminada", "La copia se abre sin contraseña.",
                                         file=out),
                     path=path, output=out, password=self.picker.password)


# ==========================================================================
# Extraer texto / imágenes
# ==========================================================================

class ExtractPage(ToolPage):
    key = "extract"
    title = "Extraer texto e imágenes"
    subtitle = "Saca todo el texto a un archivo .txt o guarda las imágenes que contiene el PDF."
    icon_name = "text"
    action_text = "Extraer texto"

    def build(self):
        self.picker = SingleFilePicker()
        self.picker.changed.connect(self._archivo)
        self.use_picker(self.picker, "Arrastra aquí el PDF del que quieres sacar texto o imágenes",
                        "o elígelo desde tu equipo.")
        self.grid = _PageGrid()
        self.lienzo = _Lienzo("Páginas elegidas", self.grid)
        self.set_main(self.lienzo)
        self.nota_ocr = Note("Este PDF no tiene texto seleccionable: parece escaneado. Para sacar su texto, "
                             "usa primero «Reconocer texto (OCR)».", "warn")
        self.nota_ocr.hide()
        self.lienzo.avisos.addWidget(self.nota_ocr)

        q = self.step("¿Qué quieres extraer?")
        self.que = OptionGroup()
        self.r_text = self.que.add("El texto", "En un archivo .txt, página por página.", "texto")
        self.r_imgs = self.que.add("Las imágenes", "En su formato original, en una carpeta nueva.", "imagenes")
        self.que.changed.connect(lambda *_: self._modo())
        q.addWidget(self.que)

        p = self.step("¿De qué páginas?")
        self.pages = pages_edit()
        self.pages.setAccessibleName("Páginas")
        p.addWidget(self.pages)
        self.min_px = spin(0, 2000, 64, " px")
        self.campo_min = campo(p, "Ignorar imágenes menores a", self.min_px, estrecho=True)
        self.pages.textChanged.connect(lambda *_: self.refresh())
        self._modo()
        panel_estable(self)

    def _modo(self):
        imgs = self.r_imgs.isChecked()
        self.min_px.setEnabled(imgs)
        self.campo_min.setVisible(imgs)
        if hasattr(self, "action"):
            self.action.setText("Extraer imágenes" if imgs else "Extraer texto")
        self.refresh()

    def _archivo(self, path):
        info = self.picker.info
        self.grid.set_document(path, self.picker.password, info.pages if info else 0)
        sin_texto = False
        if path:
            try:
                with PDF_LOCK, core.open_pdf(path, self.picker.password) as d:
                    sin_texto = not any(d[i].get_text("text").strip() for i in range(min(d.page_count, 12)))
            except Exception:  # noqa: BLE001
                sin_texto = False
        self.nota_ocr.setVisible(sin_texto)
        self.refresh()

    def accept_files(self, paths):
        self.picker.set_path(paths[0])

    def _seleccion(self):
        info = self.picker.info if getattr(self, "picker", None) else None
        return paginas_de(self.pages.text(), info.pages if info else None)

    def ready(self):
        return bool(getattr(self, "picker", None) and self.picker.path) and self._seleccion()[1] is None

    def refresh(self):
        super().refresh()
        if not hasattr(self, "campo_min"):
            return
        if not self.picker.path:
            self.set_summary([], "Elige el PDF del que quieres sacar texto o imágenes.")
            return
        idx, error = self._seleccion()
        if error:
            self.grid.set_marcas({})
            self.lienzo.set_chips([])
            self.set_summary([("Páginas no válidas", "danger")], error)
            return
        idx = idx or []
        self.grid.set_marcas(None if not self.pages.text().strip() else {i: "✓" for i in idx})
        self.lienzo.set_chips([(plural(len(idx), "página", "páginas"), "cat")])
        texto = ("Eliges la carpeta; las imágenes van en una subcarpeta nueva." if self.r_imgs.isChecked()
                 else "Eliges dónde guardar el archivo .txt. El original no cambia.")
        self.set_summary([plural(len(idx), "página", "páginas")], texto)

    def run(self):
        path, pw, spec = self.picker.path, self.picker.password, self.pages.text()
        if not check_spec(self, spec, self.picker.info.pages if self.picker.info else None):
            return
        if self.r_text.isChecked():
            out = ask_save(self, suggest_output(path, "_texto", ".txt"), "Texto (*.txt)")
            if not out:
                return

            def done(chars):
                with open(out, encoding="utf-8-sig") as fh:
                    body = "".join(line for line in fh if not line.startswith("===== Página")).strip()
                if not body:
                    QMessageBox.information(self, "Sin texto", "No se encontró texto seleccionable en esas "
                                            "páginas. Puede que sea un documento escaneado.")
                else:
                    show_done(self, "Texto extraído", f"Guardado en «{Path(out).name}».", file=out)

            ctx.run_task("Extrayendo texto…", core.extract_text, done, path=path, output=out, spec=spec,
                         password=pw)
        else:
            parent = ask_dir(self, str(Path(path).parent))
            if not parent:
                return
            folder = new_subfolder(parent, f"{Path(path).stem}_imagenes")

            def done(files):
                if not files:
                    try:
                        os.rmdir(folder)
                    except OSError:
                        pass
                    QMessageBox.information(self, "Sin imágenes", "No se encontraron imágenes con ese tamaño "
                                            "mínimo en las páginas elegidas.")
                else:
                    show_done(self, "Imágenes extraídas", f"Se guardaron {len(files)} imágenes.", folder=folder)

            ctx.run_task("Extrayendo imágenes…", core.extract_images, done, path=path, out_dir=folder,
                         min_px=self.min_px.value(), spec=spec, password=pw)


# ==========================================================================
# Propiedades (metadatos)
# ==========================================================================

META_LABELS = [("title", "Título"), ("author", "Autor"), ("subject", "Asunto"),
               ("keywords", "Palabras clave"), ("creator", "Creado con"), ("producer", "Productor PDF")]
META_EJEMPLOS = {"title": "Por ejemplo: Informe anual 2026", "author": "Quién lo escribió",
                 "subject": "Una frase sobre el contenido", "keywords": "Separadas por comas",
                 "creator": "El programa con que se hizo", "producer": "El programa que creó el PDF"}


class MetadataPage(ToolPage):
    key = "metadata"
    title = "Propiedades del documento"
    subtitle = "Consulta y edita el título, el autor y otros datos internos del PDF."
    icon_name = "info"
    action_text = "Guardar propiedades"

    def build(self):
        self._originales: dict[str, str] = {}
        self.picker = SingleFilePicker()
        self.picker.changed.connect(self._load)
        self.use_picker(self.picker, "Arrastra aquí el PDF cuyas propiedades quieres ver",
                        "o elígelo desde tu equipo.")
        self.preview = PreviewPane(lambda: render_original(self.picker))
        self.set_main(self.preview)
        self.fields: dict[str, QLineEdit] = {}
        a = self.step("¿Cómo se llama y quién lo hizo?", "Es lo que muestran los lectores de PDF y los buscadores.")
        b = self.step("¿De qué trata?")
        adv = Avanzadas("Datos del programa")
        destino = {"title": a, "author": a, "subject": b, "keywords": b, "creator": adv.lay, "producer": adv.lay}
        for key, text in META_LABELS:
            e = QLineEdit()
            e.setEnabled(False)
            e.setPlaceholderText(META_EJEMPLOS[key])
            e.textChanged.connect(lambda *_: self.refresh())
            self.fields[key] = e
            campo(destino[key], text, e)
        b.addWidget(adv)
        self.avanzadas = adv
        panel_estable(self)

    def _load(self, path):
        meta = {}
        if path:
            with PDF_LOCK:
                meta = core.get_metadata(path, self.picker.password)
        self._originales = {k: meta.get(k, "") for k in self.fields}
        for key, e in self.fields.items():
            e.setText(meta.get(key, ""))
            e.setEnabled(bool(path))
        self.preview.schedule()
        self.refresh()

    def accept_files(self, paths):
        self.picker.set_path(paths[0])

    def ready(self):
        return bool(getattr(self, "picker", None) and self.picker.path)

    def refresh(self):
        super().refresh()
        if not hasattr(self, "avanzadas"):
            return
        if not self.picker.path:
            self.set_summary([], "Elige el PDF cuyas propiedades quieres ver o cambiar.")
            return
        n = sum(1 for k, e in self.fields.items() if e.text().strip() != self._originales.get(k, "").strip())
        chips = [(plural(n, "cambio", "cambios"), "cat")] if n else []
        self.set_summary(chips, "Eliges dónde guardar la copia. El original no cambia.")

    def run(self):
        path = self.picker.path
        out = ask_save(self, suggest_output(path, "_editado"))
        if not out:
            return
        meta = {k: e.text().strip() for k, e in self.fields.items()}
        ctx.run_task("Guardando propiedades…", core.set_metadata,
                     lambda _: show_done(self, "Propiedades guardadas", file=out),
                     path=path, output=out, meta=meta, password=self.picker.password)


# ==========================================================================
# OCR
# ==========================================================================

def _language_options() -> list[tuple[str, str]]:
    langs = core.ocr_languages()
    opts = []
    if "spa" in langs and "eng" in langs:
        opts.append(("Español e inglés", "spa+eng"))
    for code in sorted(langs, key=lambda c: (c != "spa", c != "eng", c)):
        opts.append((core.OCR_LANGUAGE_NAMES.get(code, code), code))
    return opts


class OcrPage(ToolPage):
    key = "ocr"
    title = "Reconocer texto (OCR)"
    subtitle = "Convierte un PDF escaneado en uno donde se puede buscar, seleccionar y copiar el texto."
    icon_name = "ocr"
    action_text = "Reconocer texto"

    def showEvent(self, e):  # noqa: N802
        super().showEvent(e)
        from . import ocr as motor
        motor.precargar()                      # el diccionario se carga mientras la persona elige el archivo

    def build(self):
        self.resultados_carpeta = []
        self._ultimas_opciones = None
        self._destino: str | None = None
        self._resumen_resultados = None
        self._por_reconocer: list[int] = []
        self._actualizando = False

        # un documento: la ficha en el encabezado y la zona para soltar mientras no hay archivo
        self.picker = SingleFilePicker(title="Arrastra aquí el PDF escaneado")
        self.picker.setParent(self)
        self.picker.hide()
        self.picker.changed.connect(self._file_changed)
        self.zona_uno = self.picker.drop_zone("Arrastra aquí el PDF escaneado",
                                              "o elígelo desde tu equipo. Para varios PDF a la vez, elige «Varios PDF».",
                                              tema.azulejo_de(self.key))
        self.grid = _PageGrid()
        self.lienzo = _Lienzo("Páginas que se van a reconocer", self.grid)
        self.status = Note("", "info")
        self.lienzo.avisos.addWidget(self.status)

        # varios archivos o una carpeta
        self.lista = FileList(["Archivo", "Páginas", "Estado"], core.PDF_EXTS, self._fila_carpeta,
                              placeholder="Arrastra aquí los PDF escaneados o una carpeta completa",
                              allow_duplicates=False)
        self.lista.set_cards(self._meta_fila, self._chip_fila)
        self.lista.setItemDelegate(_Tarjetas(self.lista, self._meta_fila, self._chip_fila))
        self.lista_varios = _ListaVarios(self.lista, "Archivos para reconocer", "Arrastra aquí los PDF escaneados",
                                         "o una carpeta entera. También puedes elegirlos desde tu equipo.",
                                         tema.azulejo_de(self.key), self._agregar_archivos, ordenar=False,
                                         mas="Arrastra aquí más PDF o una carpeta")
        self.lista.changed.connect(self._lista_cambiada)
        self._resultados_ui()

        self.main_stack = QStackedWidget()
        for w in (self.zona_uno, self.lienzo, self.lista_varios):
            self.main_stack.addWidget(w)
        self.set_main(self.main_stack)

        # encabezado: la ficha del archivo (un PDF) o «Agregar archivos» (varios, con la lista ya empezada)
        cab = QWidget()
        hl = QHBoxLayout(cab)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(8)
        self.chip_archivo = self.picker.file_chip()
        hl.addWidget(self.chip_archivo)
        self.b_add = boton_agregar("Agregar archivos", self._agregar_archivos)
        hl.addWidget(self.b_add)
        self.set_header_widget(cab)

        # paso 1: un PDF o varios (y, para varios, agregar una carpeta entera)
        m = self.step("¿Qué quieres reconocer?")
        self.modo_seg = Segmented([("Un PDF", "uno"), ("Varios PDF", "varios")], full=True)
        self.modo_seg.setAccessibleName("Un PDF o varios")
        self.modo = self.modo_seg.group
        self.b_uno, self.b_varios = self.modo_seg.button(0), self.modo_seg.button(1)
        m.addWidget(self.modo_seg)
        self.caja_carpeta = QWidget()
        cl = QVBoxLayout(self.caja_carpeta)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(8)
        self.b_carpeta = QPushButton("Agregar carpeta…")
        self.b_carpeta.setObjectName("Secondary")
        self.b_carpeta.setIcon(icon("folder", tema.color("ink-2"), size=18))
        self.b_carpeta.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_carpeta.clicked.connect(self._agregar_carpeta)
        cl.addWidget(self.b_carpeta, 0, Qt.AlignmentFlag.AlignLeft)
        self.subcarpetas = QCheckBox("Incluir subcarpetas")
        cl.addWidget(self.subcarpetas)
        m.addWidget(self.caja_carpeta)

        # paso 2: idioma y resultado
        r = self.step("Idioma y resultado")
        self.lang = combo(_language_options())
        campo(r, "Idioma del documento", self.lang)
        self.output = _SegCombo([("PDF buscable", "pdf"), ("Texto (.txt)", "txt")])
        self.campo_output = campo(r, "Resultado", self.output)
        self.revisar = SwitchRow("Revisar las palabras dudosas", "Antes de guardar, junto al recorte de la imagen.",
                                 checked=True)
        self.revisar.setToolTip("Al terminar se muestran las palabras que el reconocimiento no leyó con "
                                "seguridad, junto al recorte de la imagen, para corregirlas antes de guardar.")
        r.addWidget(self.revisar)

        # paso 3 (varios): dónde guardar
        d = self.step("¿Dónde guardo los resultados?")
        self.paso_destino = d.parentWidget()
        self.destino_grupo = OptionGroup()
        self.r_junto = self.destino_grupo.add("Junto a cada original", "Con «_ocr» al final del nombre.", "junto")
        self.r_carpeta = self.destino_grupo.add("En otra carpeta", "Todavía no has elegido la carpeta.", "carpeta")
        self.destino_lb = self.r_carpeta.help_lb
        d.addWidget(self.destino_grupo)
        self.b_dest = QPushButton("Elegir carpeta…")
        self.b_dest.setObjectName("Secondary")
        self.b_dest.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_dest.clicked.connect(self._elegir_destino)
        d.addWidget(self.b_dest, 0, Qt.AlignmentFlag.AlignLeft)
        self.destino_grupo.changed.connect(lambda *_: (self.b_dest.setVisible(self.r_carpeta.isChecked()),
                                                       self.refresh()))
        self.b_dest.setVisible(False)

        # paso 4: mejorar la lectura (y lo avanzado)
        e = self.step("Mejorar la lectura", "Las páginas quedan derechas en el PDF nuevo. El original no cambia.")
        self.enderezar = SwitchRow("Enderezar las páginas torcidas", checked=True)
        self.girar = SwitchRow("Girar las páginas de lado o al revés", checked=True)
        self.limpiar = SwitchRow("Limpiar el fondo gris o manchado", checked=True)
        for w in (self.enderezar, self.girar, self.limpiar):
            e.addWidget(w)
        adv = Avanzadas()
        self.scope = _SegCombo([("Solo escaneadas", True), ("Todas", False)])
        self.campo_scope = campo(adv.lay, "¿Qué páginas?", self.scope, "Las que ya tienen texto se dejan como están.")
        self.dpi = _SegCombo([("200", 200), ("300", 300), ("400", 400)], current=1)
        campo(adv.lay, "Calidad (ppp)", self.dpi, "300 es lo recomendado; 200 es más rápido.")
        self.pages = pages_edit()
        self.campo_rango = campo(adv.lay, "Rango", self.pages)
        e.addWidget(adv)
        self.avanzadas = adv

        self.output.currentIndexChanged.connect(self._mostrar_modo)
        self.scope.currentIndexChanged.connect(lambda *_: self.refresh())
        self.pages.textChanged.connect(lambda *_: self.refresh())
        self.modo_seg.changed.connect(lambda *_: self._mostrar_modo())
        self._mostrar_modo()
        panel_estable(self)

    def _resultados_ui(self):
        self.caja_resultados = QWidget()
        rl = QVBoxLayout(self.caja_resultados)
        rl.setContentsMargins(0, 6, 0, 0)
        rl.setSpacing(6)
        fila = QHBoxLayout()
        fila.setSpacing(8)
        fila.addWidget(label("Resultados", "StepTitle"))
        fila.addStretch(1)
        self.b_abrir_carpeta = QPushButton("Abrir carpeta")
        self.b_informe = QPushButton("Guardar informe…")
        for b, cb in ((self.b_abrir_carpeta, self._abrir_carpeta), (self.b_informe, self._guardar_informe)):
            b.setObjectName("Ghost")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(cb)
            fila.addWidget(b)
        rl.addLayout(fila)
        self.tabla = QTreeWidget()
        self.tabla.setColumnCount(5)
        self.tabla.setHeaderLabels(["Archivo", "Resultado", "Páginas", "Dudosas", ""])
        self.tabla.setRootIsDecorated(False)
        self.tabla.setMinimumHeight(120)
        self.tabla.setTextElideMode(Qt.TextElideMode.ElideRight)
        hdr = self.tabla.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)       # el error completo va en la ayuda
        for c in (2, 3):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.tabla.setColumnWidth(4, QPushButton("Revisar").sizeHint().width() + 12)
        rl.addWidget(self.tabla, 1)
        self.lista_varios.lista_lay.addWidget(self.caja_resultados, 1)
        self.caja_resultados.hide()

    # --- modo
    def varios(self) -> bool:
        return bool(getattr(self, "b_varios", None) and self.b_varios.isChecked())

    def _mostrar_modo(self):
        if not hasattr(self, "avanzadas"):
            return
        varios = self.varios()
        pdf = self.output.currentData() == "pdf"
        self.paso_destino.setVisible(varios)
        self.caja_carpeta.setVisible(varios)
        self.campo_output.setVisible(not varios)            # en carpeta siempre es PDF buscable
        self.campo_rango.setVisible(not varios)             # cada archivo completo
        self.revisar.setVisible(not varios and pdf)         # revisar antes de guardar
        self.campo_scope.setVisible(pdf or varios)          # el texto solo sale de las páginas escaneadas
        self.caja_resultados.setVisible(varios and bool(self.resultados_carpeta))
        self.chip_archivo.setVisible(not varios and bool(self.picker.path))
        if hasattr(self, "action"):                         # (el botón se crea antes de build)
            self.action.setText("Reconocer los archivos" if varios else self.action_text)
        self._mostrar_principal()
        self.refresh()

    def _mostrar_principal(self):
        if self.varios():
            self.main_stack.setCurrentWidget(self.lista_varios)
        else:
            self.main_stack.setCurrentWidget(self.lienzo if self.picker.path else self.zona_uno)

    # --- un documento
    def _file_changed(self, path):
        self.status.setVisible(bool(path))
        self._por_reconocer = []
        info = self.picker.info
        self.grid.set_document(path, self.picker.password, info.pages if info else 0)
        if path:
            with PDF_LOCK:
                with core.open_pdf(path, self.picker.password) as d:
                    total = d.page_count
                    necesitan = [core.page_needs_ocr(p) for p in d]
                    escaneadas = sum(necesitan)
                    vacias = [not p.get_text("text").strip() for p in d] if not escaneadas else [False] * total
                    sin_texto = sum(vacias)
            self._por_reconocer = [i for i in range(total) if necesitan[i] or vacias[i]]
            if escaneadas == total:
                self.status.set_tone("info")
                self.status.setText("La página es escaneada y su texto todavía no se puede buscar: parece un "
                                    "documento escaneado." if total == 1 else
                                    f"Las {total} páginas son escaneadas y su texto todavía no se puede buscar: "
                                    "parece un documento escaneado.")
            elif escaneadas:
                self.status.set_tone("info")
                self.status.setText(f"{escaneadas} de {total} páginas son escaneadas y se van a reconocer; las "
                                    "demás ya tienen texto.")
            elif sin_texto:
                self.status.set_tone("info")
                self.status.setText(f"{sin_texto} de {total} páginas no tienen texto: se van a reconocer.")
            else:
                self.status.set_tone("ok")
                self.status.setText("Todas las páginas ya tienen texto. Si aun así quieres reconocerlas, elige "
                                    "«Todas» en «Opciones avanzadas».")
        self.chip_archivo.setVisible(not self.varios() and bool(path))
        self._mostrar_principal()
        self.refresh()

    def accept_files(self, paths):
        paths = [str(p) for p in paths]
        carpetas = [p for p in paths if os.path.isdir(p)]
        pdfs = [p for p in paths if p.lower().endswith(".pdf") and os.path.isfile(p)]
        if carpetas or len(pdfs) > 1:
            self.b_varios.setChecked(True)
            for c in carpetas:
                pdfs += self._pdfs_de(c)
            self.lista.add_paths(pdfs)
        elif pdfs:
            self.b_uno.setChecked(True)
            self.picker.set_path(pdfs[0])

    def ready(self):
        if not core.ocr_languages() or not hasattr(self, "avanzadas"):
            return False
        if self.varios():
            return self.lista.topLevelItemCount() > 0 and (self.r_junto.isChecked() or bool(self._destino))
        return bool(self.picker.path) and self._paginas()[1] is None

    def _paginas(self):
        info = self.picker.info
        return paginas_de(self.pages.text(), info.pages if info else None)

    def refresh(self):
        super().refresh()
        if not hasattr(self, "avanzadas"):
            return
        self.b_add.setVisible(self.varios() and self.lista.topLevelItemCount() > 0)
        if not core.ocr_languages():
            self.set_summary([("Falta el OCR", "danger")],
                             "No se encontraron los modelos de idioma del OCR (carpeta «tessdata»).")
            return
        if self.varios():
            self._refresh_varios()
            return
        if not self.picker.path:
            self.set_summary([], "Elige el PDF escaneado que quieres volver texto.")
            return
        idx, error = self._paginas()
        if error:
            self.grid.set_marcas({})
            self.lienzo.set_chips([])
            self.set_summary([("Rango no válido", "danger")], error)
            return
        elegidas = set(idx or [])
        todas = not self.scope.currentData() and self.output.currentData() == "pdf"
        marcadas = [i for i in sorted(elegidas) if todas or i in self._por_reconocer]
        self.grid.set_marcas({i: "✓" for i in marcadas})
        self.lienzo.set_chips([(plural(len(marcadas), "página", "páginas"), "cat")])
        texto = ("Eliges dónde guardar el archivo de texto." if self.output.currentData() == "txt" else
                 "Antes de guardar revisas las palabras dudosas. El original no cambia." if self.revisar.isChecked()
                 else "Eliges dónde guardar la copia buscable. El original no cambia.")
        self.set_summary([plural(len(marcadas), "página por reconocer", "páginas por reconocer")], texto)

    def _refresh_varios(self):
        if self._resumen_resultados is not None:
            self.set_summary(*self._resumen_resultados)
            return
        n = self.lista.topLevelItemCount()
        if not n:
            self.set_summary([], "Agrega los PDF escaneados o una carpeta.")
        elif self.r_carpeta.isChecked() and not self._destino:
            self.set_summary([plural(n, "archivo", "archivos")], "Elige la carpeta donde se guardan los resultados.")
        else:
            donde = ("Cada resultado se guarda junto a su original, con «_ocr» en el nombre." if self.r_junto.isChecked()
                     else f"Los resultados se guardan en «{Path(self._destino).name}».")
            self.set_summary([plural(n, "archivo", "archivos")], donde)

    def _opciones(self, spec: str = ""):
        from . import ocr as motor
        return motor.OpcionesOcr(idioma=self.lang.currentData() or "spa+eng", ppp=int(self.dpi.currentData()),
                                 solo_faltantes=bool(self.scope.currentData()), paginas=spec,
                                 enderezar=self.enderezar.isChecked(), girar=self.girar.isChecked(),
                                 limpiar="auto" if self.limpiar.isChecked() else "no")

    def run(self):
        from . import ocr as motor
        if self.varios():
            self._run_carpeta()
            return
        path, pw, spec = self.picker.path, self.picker.password, self.pages.text()
        if not check_spec(self, spec, self.picker.info.pages if self.picker.info else None):
            return
        op = self._opciones(spec)
        self._ultimas_opciones = op
        if self.output.currentData() == "txt":
            out = ask_save(self, suggest_output(path, "_ocr", ".txt"), "Texto (*.txt)")
            if not out:
                return
            op.solo_faltantes = True
            ctx.run_task("Reconociendo texto…", motor.texto,
                         lambda n: show_done(self, "Texto reconocido", f"Guardado en «{Path(out).name}».", file=out),
                         path=path, output=out, opciones=op, password=pw)
            return
        if self.revisar.isChecked():
            ctx.run_task("Reconociendo texto…", motor.analizar, lambda res: self._analizado(res, path, pw, op),
                         path=path, opciones=op, password=pw)
            return
        out = ask_save(self, suggest_output(path, "_ocr"))
        if not out:
            return
        ctx.run_task("Reconociendo texto…", motor.reconocer, lambda res: self._hecho(res.paginas, res.palabras, out),
                     path=path, output=out, opciones=op, password=pw)

    def _sin_paginas(self):
        QMessageBox.information(self, "OCR", "No había páginas escaneadas sin texto. Para reconocerlas igual, elige "
                                "«Todas» en «Opciones avanzadas».")

    def _hecho(self, pages: int, words: int, out: str):
        if pages == 0:
            self._sin_paginas()
            return
        show_done(self, "Texto reconocido",
                  f"Se reconocieron {words} palabras en {pages} página(s). Ahora puedes buscar y copiar el texto.",
                  file=out)

    def _analizado(self, res, path: str, pw: str | None, op):
        from . import extensiones, ocr as motor
        if not res.reconocidas():
            self._sin_paginas()
            return
        if res.dudosas():
            pagina = extensiones.ir_a("ocrcheck")
            if pagina is not None:
                pagina.cargar(path, pw, res, op)
                return
        out = ask_save(self, suggest_output(path, "_ocr"))
        if not out:
            return
        ctx.run_task("Guardando el PDF buscable…", motor.escribir, lambda r: self._hecho(r[0], r[1], out),
                     path=path, output=out, resultado=res, opciones=op, password=pw)

    # --- varios archivos
    @staticmethod
    def _pdfs_de(carpeta: str, subcarpetas: bool = False) -> list[str]:
        base = Path(carpeta)
        try:
            archivos = base.rglob("*") if subcarpetas else base.iterdir()
            return sorted((str(p) for p in archivos if p.is_file() and p.suffix.lower() == ".pdf"
                           and not p.name.startswith(".~")), key=str.lower)
        except OSError:
            return []

    def _agregar_archivos(self):
        self.lista.add_paths(ask_open(self, True))

    def _agregar_carpeta(self):
        carpeta = ask_dir(self, ctx.last_dir, "Elige la carpeta con los PDF")
        if carpeta:
            self.lista.add_paths(self._pdfs_de(carpeta, self.subcarpetas.isChecked()))

    def _elegir_destino(self):
        carpeta = ask_dir(self, self._destino or ctx.last_dir)
        if carpeta:
            self._destino = carpeta
            self.destino_lb.setText(carpeta)
            self.destino_lb.setToolTip(carpeta)
            self.r_carpeta.setChecked(True)
        self.refresh()

    @staticmethod
    def _meta_fila(it) -> str:
        n = it.text(1)
        if n == "protegido":
            return "Con contraseña: se pide al empezar"
        if not n.isdigit():
            return "No se pudo leer el archivo"
        return f"{n} página{'s' if n != '1' else ''}"

    @staticmethod
    def _chip_fila(it) -> str | None:
        return it.text(2) or ("Con contraseña" if it.text(1) == "protegido" else None)

    def _fila_carpeta(self, path: str):
        """Fila de la lista (sin pedir contraseñas: se piden todas juntas al empezar)."""
        try:
            with PDF_LOCK:
                if core.needs_password(path):
                    paginas = "protegido"
                else:
                    import pymupdf
                    with pymupdf.open(path) as d:
                        paginas = d.page_count
        except Exception:  # noqa: BLE001 — un archivo dañado se agrega igual y el informe lo dice
            paginas = "—"
        return [Path(path).name, paginas, ""]

    def _lista_cambiada(self):
        if not self._actualizando:               # la lista cambió: el resumen de la tanda anterior ya no aplica
            self._resumen_resultados = None
        self.refresh()

    def _run_carpeta(self):
        from . import ocr as motor
        paths = self.lista.paths()
        for p in paths:                         # todas las contraseñas antes de empezar
            if ctx.passwords.get(p) is None:
                ctx.passwords.ensure(p, self)   # si se cancela, ese archivo queda con error en el informe
        op = self._opciones("")
        op.dos_pasadas = False                  # una lectura por página: lo normal con muchos archivos
        self._ultimas_opciones = op
        destino = self._destino if self.r_carpeta.isChecked() else None
        self._actualizando = True
        try:
            for it in self.lista.items():
                it.setText(2, "En espera")
        finally:
            self._actualizando = False
        ctx.run_task("Reconociendo texto en varios archivos…", motor.reconocer_carpeta, self._carpeta_lista,
                     archivos=paths, destino=destino, opciones=op,
                     contrasenas={p: ctx.passwords.get(p) for p in paths})

    def _carpeta_lista(self, resultados):
        from PySide6.QtWidgets import QTreeWidgetItem
        self.resultados_carpeta = list(resultados)
        por_ruta = {os.path.normcase(r.ruta): r for r in resultados}
        self._actualizando = True
        try:
            for it in self.lista.items():
                r = por_ruta.get(os.path.normcase(it.data(0, Qt.ItemDataRole.UserRole)))
                if r is not None:
                    it.setText(2, {"hecho": "Listo", "ya tenía texto": "Ya tenía texto"}.get(r.estado, "Error"))
                    it.setToolTip(2, r.mensaje or (r.salida or ""))
        finally:
            self._actualizando = False
        self.tabla.clear()
        tinta = tema.tonos(self.cat)["ink"]
        for r in resultados:
            estado = {"hecho": "Reconocido", "ya tenía texto": "Ya tenía texto"}.get(r.estado, "Error")
            item = QTreeWidgetItem([Path(r.ruta).name, estado if r.estado != "error" else f"Error: {r.mensaje}",
                                    str(r.paginas) if r.estado == "hecho" else "—",
                                    str(r.dudosas) if r.estado == "hecho" else "—", ""])
            item.setToolTip(0, r.ruta)
            item.setToolTip(1, r.mensaje or (f"Guardado en {r.salida}" if r.salida else ""))
            self.tabla.addTopLevelItem(item)
            if r.estado == "hecho" and r.resultado is not None and r.resultado.todas():
                b = QPushButton("Revisar")
                b.setObjectName("Ghost")
                b.setStyleSheet(f"QPushButton {{ padding: 1px 8px; color: {tinta}; font-weight: 600; }}")
                b.setFixedHeight(24)
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.setToolTip("Abre «Revisar texto reconocido» con este archivo")
                b.clicked.connect(lambda _=False, r=r: self._revisar(r))
                self.tabla.setItemWidget(item, 4, b)
        hechos = sum(1 for r in resultados if r.estado == "hecho")
        ya = sum(1 for r in resultados if r.estado == "ya tenía texto")
        errores = sum(1 for r in resultados if r.estado == "error")
        partes = [f"{hechos} reconocido(s)"]
        chips = [(plural(hechos, "reconocido", "reconocidos"), "ok")]
        if ya:
            partes.append(f"{ya} ya tenía(n) texto")
        if errores:
            partes.append(f"{errores} con error")
            chips.append((plural(errores, "con error", "con error"), "danger"))
        self._resumen_resultados = (chips, " · ".join(partes))
        self.set_summary(*self._resumen_resultados)
        self.caja_resultados.setVisible(True)
        self.lista_varios.mas.hide()             # el informe ocupa ese lugar; se puede seguir soltando en la lista
        self.b_abrir_carpeta.setEnabled(hechos > 0)

    def _revisar(self, r):
        from . import extensiones
        pagina = extensiones.ir_a("ocrcheck")
        if pagina is not None:
            pagina.cargar(r.ruta, ctx.passwords.get(r.ruta), r.resultado, self._ultimas_opciones, salida=r.salida)

    def _abrir_carpeta(self):
        from .widgets import reveal
        salidas = [r.salida for r in self.resultados_carpeta if r.salida]
        if salidas:
            reveal(self._destino if self.r_carpeta.isChecked() and self._destino else salidas[0])

    def _guardar_informe(self):
        from . import ocr as motor
        if not self.resultados_carpeta:
            return
        base = self._destino if self.r_carpeta.isChecked() and self._destino else \
            str(Path(self.resultados_carpeta[0].ruta).parent)
        out = ask_save(self, str(core.unique_path(Path(base) / "informe_ocr.txt")), "Texto (*.txt)",
                       "Guardar informe")
        if not out:
            return
        try:
            Path(out).write_text(motor.informe_carpeta(self.resultados_carpeta), encoding="utf-8-sig")
        except OSError as exc:
            QMessageBox.warning(self, "Guardar informe", f"No se pudo guardar el informe: {exc}")
            return
        show_done(self, "Informe guardado", file=out)
