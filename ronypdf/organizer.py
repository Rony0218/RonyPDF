"""Organizador visual de páginas: miniaturas, reordenar, rotar, eliminar, duplicar, insertar."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QKeySequence, QPainter, QPen, QPixmap, QShortcut, QTransform
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout, QLabel, QListView,
                               QListWidget, QListWidgetItem, QMenu, QMessageBox, QScrollArea,
                               QSlider, QStackedWidget, QStyle, QStyledItemDelegate, QToolButton, QVBoxLayout,
                               QWidget)

from . import core, guardado, guardado_ui, tema
from .icons import icon, pixmap
from .widgets import (PDF_AND_IMAGES_FILTER, PDF_LOCK, Chip, DropArea, FileChip, ToolPage, ask_open, ask_save,
                      cat_de, collect_paths, ctx, qimage_from_render, show_done, suggest_output)

R_SRC = Qt.ItemDataRole.UserRole + 1     # índice de fuente (-1 = página en blanco)
R_PAGE = Qt.ItemDataRole.UserRole + 2
R_ROT = Qt.ItemDataRole.UserRole + 3
R_W = Qt.ItemDataRole.UserRole + 4
R_H = Qt.ItemDataRole.UserRole + 5

THUMB_PX = 260
# Una franja de color por archivo de origen (cuando hay varios): los colores de las categorías del tema.
SOURCE_CATS = ("organizar", "optimizar", "convertir", "ver", "editar", "seguridad")


def source_color(k: int) -> str:
    return tema.color(f"cat-{SOURCE_CATS[k % len(SOURCE_CATS)]}")


@dataclass
class Source:
    path: str
    password: str | None
    doc: pymupdf.Document | None
    color: str

    @property
    def name(self) -> str:
        return Path(self.path).name


class PageDelegate(QStyledItemDelegate):
    def __init__(self, page: "OrganizePage"):
        super().__init__()
        self.page = page

    def sizeHint(self, option, index):
        return self.page.cell_size()

    def paint(self, p: QPainter, option, index):
        t = tema.tokens()
        ton = tema.tonos(cat_de(self.page))
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(option.rect).adjusted(5, 5, -5, -5)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected:
            p.setPen(QPen(QColor(ton["c"]), 2))
            p.setBrush(QColor(ton["band"]))
        elif hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t["line"]))
        else:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r, 12, 12)

        src = index.data(R_SRC)
        page_no = index.data(R_PAGE)
        rot = index.data(R_ROT) or 0
        w, h = index.data(R_W) or 595, index.data(R_H) or 842
        if rot % 180:
            w, h = h, w
        box = r.adjusted(10, 10, -10, -30)
        scale = min(box.width() / w, box.height() / h)
        tw, th = w * scale, h * scale
        target = QRectF(box.center().x() - tw / 2, box.bottom() - th, tw, th)
        # sombra y hoja: la página es blanca en los dos temas (rp-page)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(22, 26, 37, 26))
        p.drawRect(target.translated(0, 2).adjusted(-1, -1, 1, 1))
        p.setBrush(QColor("white"))
        p.drawRect(target)
        img = self.page.thumb(src, page_no, rot) if src is not None and src >= 0 else None
        if img is not None:
            p.drawImage(target, img)
        elif src is not None and src >= 0:
            p.setPen(QColor(t["line-control"]))
            p.drawText(target, Qt.AlignmentFlag.AlignCenter, "…")
        p.setPen(QPen(QColor(t["line"]), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(target)

        # pie: número de página y fuente
        foot = QRectF(r.left(), r.bottom() - 26, r.width(), 22)
        text = str(index.row() + 1)
        if src is None or src < 0:
            text += "  ·  en blanco"
        if rot:
            text += f"  ·  {rot}°"
        p.setFont(tema.fuente("chip" if selected else "caption", p.font()))
        p.setPen(QColor(ton["ink"] if selected else t["ink-2"]))
        p.drawText(foot, Qt.AlignmentFlag.AlignCenter, text)
        if len(self.page.sources) > 1 and src is not None and src >= 0:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(self.page.sources[src].color))
            p.drawRoundedRect(QRectF(target.left(), target.bottom() - 5, target.width(), 5), 1, 1)
        p.restore()


class PageGrid(QListWidget):
    files_dropped = Signal(list)
    before_move = Signal()
    moved = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("PageGrid")
        self.setViewMode(QListView.ViewMode.ListMode)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setUniformItemSizes(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setMouseTracking(True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(24)

    def _external(self, e) -> bool:
        return e.source() is not self and e.mimeData().hasUrls()

    def dragEnterEvent(self, e):
        if self._external(e):
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if self._external(e):
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e):
        if self._external(e):
            paths = collect_paths(e.mimeData().urls(), core.PDF_EXTS | core.IMAGE_EXTS)
            e.acceptProposedAction()
            if paths:
                self.files_dropped.emit(paths)
            return
        self.before_move.emit()
        super().dropEvent(e)
        self.moved.emit()


class PagePreview(QDialog):
    def __init__(self, parent, img: QImage, title: str):
        super().__init__(parent)
        self.setWindowTitle(title)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        sc = QScrollArea()
        sc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sc.setStyleSheet(f"background: {tema.color('surface-sunken')};")
        lb = QLabel()
        dpr = self.devicePixelRatioF()
        pm = QPixmap.fromImage(img)
        pm.setDevicePixelRatio(dpr)
        lb.setPixmap(pm)
        sc.setWidget(lb)
        lay.addWidget(sc)
        screen = parent.screen().availableGeometry() if parent and parent.screen() else QRect(0, 0, 1200, 900)
        self.resize(min(int(img.width() / dpr) + 40, int(screen.width() * 0.8)),
                    min(int(img.height() / dpr) + 40, int(screen.height() * 0.88)))


def _herramienta(texto: str, glifo: str, ayuda: str, cb) -> QToolButton:
    """Botón de la barra de páginas (``rp-toolgroup``): glifo y, si cabe, texto; ayuda emergente siempre."""
    b = QToolButton()
    b.setObjectName("Tool")
    b.setIcon(icon(glifo, tema.color("ink-2"), size=18))
    b.setIconSize(QSize(18, 18))
    if texto:
        b.setText(texto)
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    else:
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    b.setToolTip(ayuda)
    b.setAccessibleName(ayuda.split(" (")[0])
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.clicked.connect(cb)
    return b


class OrganizePage(ToolPage):
    """Organizar páginas: un editor a todo el ancho con la forma de las demás herramientas (encabezado con la
    ficha del archivo, el pozo con las miniaturas y la barra de acción con un solo principal, «Guardar»).
    Las acciones sobre las páginas elegidas aparecen arriba de la cuadrícula solo cuando hay selección."""

    key = "organize"
    title = "Organizar páginas"
    subtitle = "Reordena arrastrando, gira, elimina, duplica o inserta páginas, incluso de varios PDF."
    icon_name = "organize"
    action_text = "Guardar"

    def __init__(self):
        super().__init__()
        if self.panel is not None:
            self.panel.hide()          # sin panel de pasos: la cuadrícula ocupa todo el ancho
        self.update_state()

    def build(self):
        self.sources: list[Source] = []
        self.thumbs: dict[tuple[int, int], QImage | None] = {}
        self.rotated: dict[tuple[int, int, int], QImage] = {}
        self.pending: dict[tuple[int, int], None] = {}
        self.undo_stack: list[list[tuple]] = []
        self.redo_stack: list[list[tuple]] = []
        self._limpio: list[tuple] | None = []      # la cuadrícula tal como se abrió o se guardó
        self._disco: tuple[int, int] | None = None  # tamaño y fecha del archivo de origen al abrirlo
        self._aviso_hasta = 0.0
        self.thumb_size = 150
        self.info = self.footer_info
        self.footer_info.setWordWrap(True)

        # Encabezado: el documento como ficha; «Cambiar» abre otro (reemplaza el actual)
        self.ficha = FileChip()
        self.ficha.b_change.setToolTip("Abrir otro PDF (reemplaza el actual)")
        self.ficha.change_clicked.connect(self.open_dialog)
        self.ficha.hide()
        self.set_header_widget(self.ficha)

        # Área principal: zona para soltar o la cuadrícula con su barra
        self.drop = DropArea("Arrastra aquí un PDF para ver sus páginas",
                             "Podrás reordenarlas, girarlas, eliminarlas o combinarlas con otros PDF e imágenes.",
                             core.PDF_EXTS | core.IMAGE_EXTS, multi=True, tile_key="organize", zone=True,
                             button_text="Elegir PDF…")
        self.drop.clicked.connect(self.open_dialog)
        self.drop.files_dropped.connect(self.load_files)

        editor = QWidget()
        ev = QVBoxLayout(editor)
        ev.setContentsMargins(12, 10, 6, 0)
        ev.setSpacing(4)
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 6, 0)
        bar.setSpacing(2)
        # Sin selección: lo que se hace con el documento
        self.grupo_doc = QWidget()
        gd = QHBoxLayout(self.grupo_doc)
        gd.setContentsMargins(0, 0, 0, 0)
        gd.setSpacing(2)
        self.b_add = _herramienta("Insertar PDF", "add", "Insertar las páginas de otro PDF o imagen después de la "
                                  "selección (o al final)", self.add_dialog)
        self.b_blank = _herramienta("Página en blanco", "blank", "Insertar una página en blanco después de la "
                                    "selección (o al final)", self.insert_blank)
        self.b_rev = _herramienta("Invertir orden", "reverse", "Invertir el orden de todas las páginas", self.reverse)
        self.b_all = _herramienta("Seleccionar todas", "select", "Seleccionar todas (Ctrl+A)",
                                  lambda: self.grid.selectAll())
        for b in (self.b_add, self.b_blank, self.b_rev, self.b_all):
            gd.addWidget(b)
        bar.addWidget(self.grupo_doc)
        # Con selección: lo que se hace con las páginas elegidas
        self.grupo_sel = QWidget()
        gs = QHBoxLayout(self.grupo_sel)
        gs.setContentsMargins(0, 0, 0, 0)
        gs.setSpacing(2)
        self.sel_chip = Chip("", "cat")
        gs.addWidget(self.sel_chip)
        gs.addSpacing(6)
        self.b_rl = _herramienta("", "rotate_left", "Girar a la izquierda (Ctrl+Mayús+R)", lambda: self.rotate(-90))
        self.b_rr = _herramienta("", "rotate_right", "Girar a la derecha (Ctrl+R)", lambda: self.rotate(90))
        self.b_dup = _herramienta("Duplicar", "duplicate", "Duplicar las páginas elegidas (Ctrl+D)", self.duplicate)
        self.b_blank_sel = _herramienta("", "blank", "Insertar una página en blanco después de la selección",
                                        self.insert_blank)
        self.b_add_sel = _herramienta("", "add", "Insertar las páginas de otro PDF o imagen después de la selección",
                                      self.add_dialog)
        self.b_del = _herramienta("Eliminar", "delete", "Eliminar las páginas elegidas (Supr)", self.delete)
        self.b_extract = _herramienta("Guardar selección…", "save", "Guardar las páginas elegidas como un PDF nuevo",
                                      lambda: self.save(selected_only=True))
        self.b_none = _herramienta("", "remove", "Quitar la selección (Esc)", lambda: self.grid.clearSelection())
        for b in (self.b_rl, self.b_rr, self.b_dup, self.b_add_sel, self.b_blank_sel, self.b_del):
            gs.addWidget(b)
        gs.addSpacing(10)
        gs.addWidget(self.b_extract)
        gs.addWidget(self.b_none)
        bar.addWidget(self.grupo_sel)
        bar.addStretch(1)
        self.b_undo = _herramienta("", "undo", "Deshacer (Ctrl+Z)", self.undo)
        self.b_redo = _herramienta("", "redo", "Rehacer (Ctrl+Y)", self.redo)
        bar.addWidget(self.b_undo)
        bar.addWidget(self.b_redo)
        bar.addSpacing(10)
        zl = QLabel()
        zl.setPixmap(pixmap("zoom", tema.color("ink-3"), 18))
        zl.setToolTip("Tamaño de las miniaturas")
        bar.addWidget(zl)
        self.zoom = QSlider(Qt.Orientation.Horizontal)
        self.zoom.setRange(90, 300)
        self.zoom.setValue(self.thumb_size)
        self.zoom.setMinimumWidth(50)        # cede espacio cuando la ventana es angosta
        self.zoom.setMaximumWidth(120)
        self.zoom.setToolTip("Tamaño de las miniaturas")
        self.zoom.setAccessibleName("Tamaño de las miniaturas")
        self.zoom.valueChanged.connect(self.set_zoom)
        bar.addWidget(self.zoom)
        ev.addLayout(bar)
        self.grid = PageGrid()
        self.grid.setItemDelegate(PageDelegate(self))
        self.grid.files_dropped.connect(self.insert_files)
        self.grid.before_move.connect(self.push_undo)
        self.grid.moved.connect(self.update_state)
        self.grid.itemSelectionChanged.connect(self.update_state)
        self.grid.itemDoubleClicked.connect(self.show_preview)
        self.grid.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self.context_menu)
        self.grid.verticalScrollBar().valueChanged.connect(lambda *_: self._kick())
        ev.addWidget(self.grid, 1)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.drop)
        self.stack.addWidget(editor)
        self.set_main(self.stack)

        # Barra de acción: el principal es «Guardar» (en el mismo archivo, con un solo PDF de origen) con la
        # flecha de «Guardar como…»; reemplaza al botón principal de la plantilla.
        self.action.hide()
        self.save_group = guardado_ui.BotonGuardar(self._guardar_principal, lambda: self.guardar(),
                                                   lambda: self.save(False))
        self.b_save = self.save_group.principal
        self.b_save.setProperty("tam", "lg")
        self.b_save.setStyleSheet("QPushButton#Primary { border-top-right-radius: 0px; "
                                  "border-bottom-right-radius: 0px; padding: 13px 14px 13px 22px; font-size: 15px; }")
        self.actionbar.layout().addWidget(self.save_group)

        self.timer = QTimer(self)
        self.timer.setInterval(1)
        self.timer.timeout.connect(self._render_some)

        for seq, fn in ((QKeySequence.StandardKey.Delete, self.delete), ("Ctrl+Z", self.undo),
                        ("Ctrl+Y", self.redo), ("Ctrl+Shift+Z", self.redo), ("Ctrl+D", self.duplicate),
                        ("Ctrl+R", lambda: self.rotate(90)), ("Ctrl+Shift+R", lambda: self.rotate(-90)),
                        ("Esc", lambda: self.grid.clearSelection())):
            sc = QShortcut(QKeySequence(seq), self.grid)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)
        for seq, fn in (("Ctrl+S", self._guardar_principal), ("Ctrl+Shift+S", lambda: self.save(False))):
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)
        self.set_zoom(self.thumb_size)

    # ------------------------------------------------------------------ contrato de la plantilla
    def ready(self) -> bool:
        return hasattr(self, "grid") and self.grid.count() > 0

    def run(self):
        self._guardar_principal()

    def refresh(self):
        super().refresh()
        if hasattr(self, "save_group"):
            self.update_state()

    # ------------------------------------------------------------------ fuentes
    def cell_size(self) -> QSize:
        s = self.thumb_size
        return QSize(s + 30, int(s * 1.3) + 52)

    def set_zoom(self, v: int):
        self.thumb_size = v
        self.grid.setGridSize(self.cell_size())
        self.grid.doItemsLayout()

    def _open_source(self, path: str) -> int | None:
        if not ctx.passwords.ensure(path, self):
            return None
        try:
            with PDF_LOCK:
                doc = core.open_pdf(path, ctx.passwords.get(path))
        except core.UserError as exc:
            QMessageBox.warning(self, "No se pudo abrir", str(exc))
            return None
        color = source_color(len(self.sources))
        self.sources.append(Source(path, ctx.passwords.get(path), doc, color))
        ctx.remember_dir(path)
        ctx.add_recent(path, doc.page_count)
        return len(self.sources) - 1

    def _doc(self, src: int) -> pymupdf.Document:
        s = self.sources[src]
        if s.doc is None or s.doc.is_closed:
            s.doc = core.open_pdf(s.path, s.password)
        return s.doc

    def _close_docs(self):
        with PDF_LOCK:
            for s in self.sources:
                if s.doc is not None and not s.doc.is_closed:
                    s.doc.close()
                s.doc = None

    def reset(self):
        self.timer.stop()
        self._close_docs()
        self.sources.clear()
        self.thumbs.clear()
        self.rotated.clear()
        self.pending.clear()
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.grid.clear()

    def _source_items(self, src: int) -> list[tuple]:
        with PDF_LOCK:
            doc = self._doc(src)
            rows = []
            for i in range(doc.page_count):
                r = doc[i].rect
                rows.append((src, i, 0, r.width, r.height))
        return rows

    # ------------------------------------------------------------------ cargar
    def accept_files(self, paths: list[str]):
        self.load_files(paths)

    def open_dialog(self):
        paths = ask_open(self, True, PDF_AND_IMAGES_FILTER, "Abrir PDF para organizar")
        if paths:
            self.load_files(paths)

    def add_dialog(self):
        paths = ask_open(self, True, PDF_AND_IMAGES_FILTER, "Insertar páginas de…")
        if paths:
            self.insert_files(paths)

    def load_files(self, paths: list[str]):
        if self.grid.count() and not self._confirm_discard():
            return
        self.reset()
        self.insert_files(paths, record=False, select=False)
        self._marcar_limpio()
        QTimer.singleShot(0, self.grid.scrollToTop)

    def _marcar_limpio(self):
        """La cuadrícula actual es la que está guardada: sin cambios pendientes."""
        self._limpio = self.snapshot()
        self._disco = guardado.firma_archivo(self.sources[0].path) if len(self.sources) == 1 else None
        self.update_state()

    def has_unsaved(self) -> bool:
        return self.grid.count() > 0 and self.snapshot() != self._limpio

    def _confirm_discard(self) -> bool:
        """Antes de abrir otro PDF: «Guardar», «No guardar» o «Cancelar».  True si se puede seguir."""
        if not self.has_unsaved():
            return True
        r = guardado_ui.preguntar_cambios(self, "Abrir otro PDF", "¿Quieres guardar los cambios de las páginas "
                                          "antes de abrir otro PDF?")
        if r == "guardar":
            return guardado_ui.guardar_y_esperar(self)
        if r == "descartar":
            guardado_ui.trabajo_descartado(self, "organize")
            return True
        return False

    def restaurar(self, fuentes: list[str], paginas: list) -> list[str] | None:
        """Recupera un trabajo autoguardado: reabre los archivos de origen (pide las contraseñas que hagan
        falta) y vuelve a armar la cuadrícula, con los cambios pendientes.  Devuelve los archivos que ya no
        están (sus páginas se omiten), o None si la persona canceló."""
        if self.grid.count() and not self._confirm_discard():
            return None
        self.reset()
        mapa, faltan = {}, []
        for k, ruta in enumerate(fuentes):
            if not os.path.isfile(ruta):
                faltan.append(ruta)
                continue
            src = self._open_source(ruta)
            if src is None:
                faltan.append(ruta)
                continue
            mapa[k] = src
        filas = []
        for fila in paginas:
            try:
                s, p, rot, w, h = fila
            except (TypeError, ValueError):
                continue
            if s is None or s < 0:
                filas.append((-1, 0, int(rot or 0), float(w), float(h)))
            elif s in mapa:
                with PDF_LOCK:
                    n = self._doc(mapa[s]).page_count
                if 0 <= int(p) < n:
                    filas.append((mapa[s], int(p), int(rot or 0), float(w), float(h)))
        self._insert_rows(0, filas, select=False)
        self._limpio = None                   # queda con cambios pendientes
        self._disco = guardado.firma_archivo(self.sources[0].path) if len(self.sources) == 1 else None
        self.update_state()
        QTimer.singleShot(0, self.grid.scrollToTop)
        return faltan

    def insert_files(self, paths: list[str], record: bool = True, select: bool = True):
        rows = []
        for p in paths:
            src = self._open_source(p)
            if src is not None:
                rows.extend(self._source_items(src))
        if not rows:
            self.update_state()
            return
        if record and self.grid.count():
            self.push_undo()
        at = self._insert_pos()
        self._insert_rows(at, rows, select)
        self.update_state()

    # ------------------------------------------------------------------ items
    def _make_item(self, row: tuple) -> QListWidgetItem:
        src, page, rot, w, h = row
        it = QListWidgetItem()
        it.setData(R_SRC, src)
        it.setData(R_PAGE, page)
        it.setData(R_ROT, rot)
        it.setData(R_W, float(w))
        it.setData(R_H, float(h))
        it.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsDragEnabled)
        if src >= 0:
            it.setToolTip(f"{self.sources[src].name} · página {page + 1}")
            if (src, page) not in self.thumbs:
                self.pending[(src, page)] = None
        else:
            it.setToolTip("Página en blanco")
        return it

    def _row(self, it: QListWidgetItem) -> tuple:
        return (it.data(R_SRC), it.data(R_PAGE), it.data(R_ROT) or 0, it.data(R_W), it.data(R_H))

    def _insert_rows(self, at: int, rows: list[tuple], select: bool = True) -> list[QListWidgetItem]:
        items = []
        self.grid.setUpdatesEnabled(False)
        for k, row in enumerate(rows):
            it = self._make_item(row)
            self.grid.insertItem(at + k, it)
            items.append(it)
        self.grid.setUpdatesEnabled(True)
        self.grid.clearSelection()
        if select:
            for it in items:
                it.setSelected(True)
            if items:
                self.grid.scrollToItem(items[0])
        self._kick()
        return items

    def _insert_pos(self) -> int:
        rows = [self.grid.row(i) for i in self.grid.selectedItems()]
        return max(rows) + 1 if rows else self.grid.count()

    def selected(self) -> list[QListWidgetItem]:
        return sorted(self.grid.selectedItems(), key=self.grid.row)

    # ------------------------------------------------------------------ deshacer
    def snapshot(self) -> list[tuple]:
        return [self._row(self.grid.item(i)) for i in range(self.grid.count())]

    def push_undo(self):
        self.undo_stack.append(self.snapshot())
        del self.undo_stack[:-60]
        self.redo_stack.clear()

    def _restore(self, snap: list[tuple]):
        self.grid.setUpdatesEnabled(False)
        self.grid.clear()
        for row in snap:
            self.grid.addItem(self._make_item(row))
        self.grid.setUpdatesEnabled(True)
        self._kick()
        self.update_state()

    def undo(self):
        if self.undo_stack:
            self.redo_stack.append(self.snapshot())
            self._restore(self.undo_stack.pop())

    def redo(self):
        if self.redo_stack:
            self.undo_stack.append(self.snapshot())
            self._restore(self.redo_stack.pop())

    # ------------------------------------------------------------------ acciones
    def rotate(self, angle: int):
        items = self.selected()
        if not items:
            return
        self.push_undo()
        for it in items:
            it.setData(R_ROT, ((it.data(R_ROT) or 0) + angle) % 360)
        self.grid.viewport().update()
        self.update_state()

    def delete(self):
        items = self.selected()
        if not items:
            return
        self.push_undo()
        first = self.grid.row(items[0])
        for it in reversed(items):
            self.grid.takeItem(self.grid.row(it))
        if self.grid.count():
            self.grid.setCurrentRow(min(first, self.grid.count() - 1))
        self.update_state()

    def duplicate(self):
        items = self.selected()
        if not items:
            return
        self.push_undo()
        rows = [self._row(it) for it in items]
        self._insert_rows(self.grid.row(items[-1]) + 1, rows)
        self.update_state()

    def insert_blank(self):
        if not self.grid.count():
            return
        self.push_undo()
        items = self.selected()
        ref = items[-1] if items else self.grid.item(self.grid.count() - 1)
        src, page, rot, w, h = self._row(ref)
        if rot % 180:
            w, h = h, w
        self._insert_rows(self.grid.row(ref) + 1, [(-1, 0, 0, w, h)])
        self.update_state()

    def reverse(self):
        if self.grid.count() < 2:
            return
        self.push_undo()
        self._restore(list(reversed(self.snapshot())))

    def context_menu(self, pos: QPoint):
        if not self.grid.itemAt(pos):
            return
        m = QMenu(self)
        m.addAction(icon("rotate_left"), "Rotar a la izquierda", lambda: self.rotate(-90))
        m.addAction(icon("rotate_right"), "Rotar a la derecha", lambda: self.rotate(90))
        m.addAction(icon("duplicate"), "Duplicar", self.duplicate)
        m.addAction(icon("add"), "Insertar otro PDF o imagen después…", self.add_dialog)
        m.addAction(icon("blank"), "Insertar página en blanco después", self.insert_blank)
        m.addSeparator()
        m.addAction(icon("eye"), "Ver en grande", lambda: self.show_preview(self.grid.itemAt(pos)))
        m.addAction(icon("save"), "Guardar selección como PDF…", lambda: self.save(selected_only=True))
        m.addSeparator()
        m.addAction(icon("delete"), "Eliminar", self.delete)
        m.exec(self.grid.viewport().mapToGlobal(pos))

    def show_preview(self, it: QListWidgetItem | None):
        if it is None:
            return
        src, page, rot, w, h = self._row(it)
        title = f"Página {self.grid.row(it) + 1}"
        if src < 0:
            img = QImage(int(w), int(h), QImage.Format.Format_RGB888)
            img.fill(QColor("white"))
        else:
            if not PDF_LOCK.acquire(blocking=False):
                return
            try:
                screen_h = self.screen().availableGeometry().height() if self.screen() else 900
                px = int(screen_h * 0.85 * self.devicePixelRatioF())
                img = qimage_from_render(core.render_page(self._doc(src), page, px, rot))
            finally:
                PDF_LOCK.release()
            title += f" — {self.sources[src].name}, pág. {page + 1}"
        PagePreview(self, img, title).exec()

    # ------------------------------------------------------------------ miniaturas
    def thumb(self, src: int, page: int, rot: int) -> QImage | None:
        base = self.thumbs.get((src, page))
        if base is None or not rot:
            return base
        key = (src, page, rot)
        if key not in self.rotated:
            self.rotated[key] = base.transformed(QTransform().rotate(rot), Qt.TransformationMode.SmoothTransformation)
        return self.rotated[key]

    def _kick(self):
        if self.pending and not self.timer.isActive():
            self.timer.start()

    def _next_pending(self) -> tuple[int, int]:
        first = self.grid.indexAt(QPoint(20, 20)).row()
        if first >= 0:
            for r in range(first, min(first + 80, self.grid.count())):
                it = self.grid.item(r)
                key = (it.data(R_SRC), it.data(R_PAGE))
                if key in self.pending:
                    return key
        return next(iter(self.pending))

    def _render_some(self):
        if not self.pending:
            self.timer.stop()
            return
        if ctx.busy or not PDF_LOCK.acquire(blocking=False):
            return
        try:
            deadline = time.perf_counter() + 0.03
            while self.pending and time.perf_counter() < deadline:
                key = self._next_pending()
                self.pending.pop(key, None)
                try:
                    self.thumbs[key] = qimage_from_render(core.render_page(self._doc(key[0]), key[1], THUMB_PX))
                except Exception:  # noqa: BLE001 — página dañada: se queda sin miniatura
                    self.thumbs[key] = None
        finally:
            PDF_LOCK.release()
        self.grid.viewport().update()

    # ------------------------------------------------------------------ estado
    def update_state(self):
        n = self.grid.count()
        sel = len(self.grid.selectedItems())
        self.stack.setCurrentIndex(1 if (n or self.sources) else 0)
        has = n > 0
        # Las acciones de las páginas elegidas solo aparecen con selección (no una fila de botones apagados).
        self.grupo_sel.setVisible(sel > 0)
        self.grupo_doc.setVisible(sel == 0)
        self.sel_chip.setText(f"{sel} elegida{'s' if sel != 1 else ''}")
        for b in (self.b_rl, self.b_rr, self.b_dup, self.b_del, self.b_blank_sel, self.b_extract):
            b.setEnabled(sel > 0)
        self.b_blank.setEnabled(has)
        self.b_add.setEnabled(bool(self.sources))
        self.b_add_sel.setEnabled(bool(self.sources))
        self.b_rev.setEnabled(n > 1)
        self.b_rev.setVisible(n > 1)
        self.b_all.setEnabled(has)
        self.b_undo.setEnabled(bool(self.undo_stack))
        self.b_redo.setEnabled(bool(self.redo_stack))
        directo, motivo = self._directo()
        self.save_group.actualizar(has, has and self.has_unsaved(), directo, motivo)
        # Encabezado: el documento abierto como ficha
        if self.sources:
            extra = f" · {len(self.sources)} archivos de origen" if len(self.sources) > 1 else ""
            self.ficha.set_file(self.sources[0].name, f"{n} página{'s' if n != 1 else ''}{extra}",
                                "\n".join(s.path for s in self.sources))
        self.ficha.setVisible(bool(self.sources))
        chips = []
        if has:
            chips.append(f"{n} página{'s' if n != 1 else ''}")
        if len(self.sources) > 1:
            chips.append(f"{len(self.sources)} archivos de origen")
        if self.has_unsaved():
            chips.append(("Cambios sin guardar", "warn"))
        if time.monotonic() < self._aviso_hasta:
            self.set_summary(chips)            # se deja a la vista el aviso de «Guardado»
            return
        if not has:
            texto = ("Abre un PDF o una imagen para ver sus páginas." if not self.sources else
                     "No quedan páginas: deshaz con Ctrl+Z o inserta otro PDF.")
        elif sel:
            texto = "Ctrl+R gira, Ctrl+D duplica y Supr elimina las páginas elegidas."
        else:
            texto = "Arrastra las miniaturas para cambiar el orden; doble clic para ver una en grande."
        self.set_summary(chips, texto)

    # ------------------------------------------------------------------ guardar
    def _directo(self) -> tuple[bool, str]:
        """¿Se puede «Guardar» en el mismo archivo?  (sí/no, por qué no)."""
        if len(self.sources) != 1:
            return False, "con varios archivos de origen se guarda como un PDF nuevo" if self.sources else ""
        if core.is_image(self.sources[0].path):
            return False, "las imágenes se guardan como un PDF nuevo"
        return True, ""

    def _guardar_principal(self):
        if self.has_unsaved() and self._directo()[0]:
            self.guardar()
        else:
            self.save(False)

    def _aviso(self, texto: str):
        self._aviso_hasta = time.monotonic() + 5
        self.info.setText(texto)
        QTimer.singleShot(5200, self.update_state)

    def _refs(self, items) -> list[core.PageRef]:
        refs = []
        for it in items:
            src, page, rot, w, h = self._row(it)
            refs.append(core.PageRef(None if src < 0 else src, page, rot, w, h))
        return refs

    def save(self, selected_only: bool = False, al_terminar=None):
        """«Guardar como…» (o «Guardar selección como PDF…»): un PDF nuevo con las páginas."""
        fin = guardado_ui.callback(al_terminar)
        items = self.selected() if selected_only else [self.grid.item(i) for i in range(self.grid.count())]
        if not items or ctx.busy:
            return fin(False)
        refs = self._refs(items)
        base = next((s.path for s in self.sources), str(Path(ctx.last_dir) / "documento.pdf"))
        out = ask_save(self, suggest_output(base, "_seleccion" if selected_only else "_organizado"))
        if not out:
            return fin(False)
        self._guardar_en(out, refs, completo=not selected_only, directo=False, al_terminar=fin)

    def guardar(self, al_terminar=None):
        """«Guardar» en el mismo archivo (un único PDF de origen y el documento completo): la primera vez lo
        confirma y antes deja una copia de respaldo.  En los demás casos, «Guardar como…»."""
        fin = guardado_ui.callback(al_terminar)
        if not self.grid.count() or ctx.busy:
            return fin(False)
        if not self._directo()[0] or not self.has_unsaved():
            return self.save(False, al_terminar=fin)
        ruta = self.sources[0].path
        with PDF_LOCK:
            firmado = guardado.tiene_firma_digital(self._doc(0))
        r = guardado_ui.preparar_directo(self, ruta, self._disco, False, firmado)
        if r == "guardar_como":
            return self.save(False, al_terminar=fin)
        if r != "guardar":
            return fin(False)
        self._guardar_en(ruta, self._refs([self.grid.item(i) for i in range(self.grid.count())]), completo=True,
                         directo=True, al_terminar=fin)

    def _guardar_en(self, out: str, refs: list[core.PageRef], completo: bool, directo: bool, al_terminar):
        norm = lambda p: os.path.normcase(os.path.abspath(p))  # noqa: E731
        overwrites = any(norm(s.path) == norm(out) for s in self.sources)
        volver: list = []
        if overwrites:
            # si otra pantalla tiene abierto este archivo, se suelta allí y se vuelve a abrir al terminar
            volver = guardado_ui.preparar_reemplazo(self, out)
            if volver is None:
                return al_terminar(False)
            self._close_docs()
        sources = [(s.path, s.password) for s in self.sources]
        hecho: dict = {}

        def done(res):
            hecho["ok"] = True
            if res.error_respaldo:
                if guardado_ui.respaldo_fallido(self, res.error_respaldo) == "guardar_como":
                    self.save(False, al_terminar=al_terminar)
                else:
                    al_terminar(False)
                return
            n = res.valor
            if overwrites:
                # El archivo de origen cambió: recargamos lo que quedó guardado.
                self.reset()
                self.insert_files([out], record=False)
            if completo:
                self._marcar_limpio()
                guardado_ui.trabajo_guardado(self, "organize")
            if directo:
                self._aviso(guardado_ui.aviso_guardado(res.respaldo is not None))
            else:
                show_done(self, "PDF guardado", f"{n} páginas en «{Path(out).name}».", file=out)
            al_terminar(True)

        guardado_ui.lanzar_guardado("Guardando PDF…", guardado.guardar_con_respaldo, done,
                                    on_finally=lambda: (guardado_ui.reabrir(volver),
                                                        guardado_ui.revisar_fin(hecho, al_terminar)),
                                    tarea=guardado.armar_pdf, respaldo=overwrites and guardado_ui.respaldo_activo(),
                                    ajustes=guardado_ui.ajustes_path(), retencion=guardado_ui.retencion(),
                                    origen=out, sources=sources, pages=refs, output=out)