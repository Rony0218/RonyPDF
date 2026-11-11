"""Infraestructura de la interfaz: tareas en segundo plano, selectores de archivo, etc."""

from __future__ import annotations

import os
import re
import subprocess
import threading
import traceback
from pathlib import Path
from typing import Callable, Iterable

from PySide6.QtCore import (QCoreApplication, QEvent, QObject, QPointF, QRectF, QRunnable, QSize, Qt,
                            QThreadPool, QTimer, QTranslator, QUrl, Signal)
from PySide6.QtGui import QAction, QColor, QDesktopServices, QFontMetrics, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QColorDialog, QFileDialog, QFrame, QHBoxLayout,
                               QHeaderView, QInputDialog, QLabel, QLineEdit, QMessageBox,
                               QProgressDialog, QPushButton, QScrollArea, QSizePolicy,
                               QStackedLayout, QStackedWidget, QStyle, QStyledItemDelegate, QToolButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from . import core, tema
from .art import tile
from .componentes import (Chip, ChoiceChip, Choices, Count, FileChip, FlowLayout, GlyphTile, Kbd,  # noqa: F401
                          Note, OptionCard, OptionGroup, PdfThumb, Segmented, Step, StepList, Switch, SwitchRow,
                          cat_de, pintar_miniatura, repolish)
from .icons import icon, pixmap
from .style import ACCENT, MUTED

# Un único candado: MuPDF no admite uso concurrente desde varios hilos.
PDF_LOCK = threading.RLock()

PDF_FILTER = "Documentos PDF (*.pdf)"
IMAGE_FILTER = "Imágenes (*.jpg *.jpeg *.png *.bmp *.gif *.tif *.tiff)"
PDF_AND_IMAGES_FILTER = "PDF e imágenes (*.pdf *.jpg *.jpeg *.png *.bmp *.gif *.tif *.tiff)"


# --------------------------------------------------------------------------
# Contexto global de la aplicación
# --------------------------------------------------------------------------

class Cancelled(Exception):
    pass


class PasswordStore:
    def __init__(self):
        self._pw: dict[str, str] = {}

    @staticmethod
    def _key(path: str) -> str:
        return os.path.normcase(os.path.abspath(path))

    def get(self, path: str) -> str | None:
        return self._pw.get(self._key(path))

    def set(self, path: str, password: str) -> None:
        self._pw[self._key(path)] = password

    def ensure(self, path: str, parent: QWidget | None) -> bool:
        """Pide la contraseña si hace falta. Devuelve False si el usuario cancela."""
        if core.is_image(path):
            return True
        with PDF_LOCK:
            needs = core.needs_password(path)
        if not needs:
            return True
        key = self._key(path)
        if key in self._pw:
            return True
        msg = f"«{Path(path).name}» está protegido con contraseña.\nEscríbela para continuar:"
        while True:
            pw, ok = QInputDialog.getText(parent, "Contraseña requerida", msg, QLineEdit.EchoMode.Password)
            if not ok:
                return False
            with PDF_LOCK:
                valid = core.check_password(path, pw)
            if valid:
                self._pw[key] = pw
                return True
            msg = f"Contraseña incorrecta para «{Path(path).name}».\nInténtalo de nuevo:"


class _Signals(QObject):
    progress = Signal(int, int, str)
    done = Signal(object)
    failed = Signal(str)
    cancelled = Signal()


class Task(QRunnable):
    def __init__(self, fn: Callable, kwargs: dict):
        super().__init__()
        self.setAutoDelete(False)
        self.fn = fn
        self.kwargs = kwargs
        self.signals = _Signals()
        self.cancel_requested = False

    def _progress(self, i: int, n: int, msg: str = "") -> None:
        if self.cancel_requested:
            raise Cancelled()
        self.signals.progress.emit(i, n, msg)

    def run(self):
        try:
            with PDF_LOCK:
                result = self.fn(progress=self._progress, **self.kwargs)
        except Cancelled:
            self.signals.cancelled.emit()
        except core.UserError as exc:
            self.signals.failed.emit(str(exc))
        except MemoryError:
            self.signals.failed.emit("No hay suficiente memoria para procesar este archivo.")
        except PermissionError as exc:
            self.signals.failed.emit(f"No hay permiso para escribir el archivo:\n{exc.filename}")
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self.signals.failed.emit(f"Ocurrió un error inesperado:\n{type(exc).__name__}: {exc}")
        else:
            self.signals.done.emit(result)


class Context:
    """Servicios compartidos (ventana, contraseñas, carpeta reciente, tareas)."""

    def __init__(self):
        self.window: QWidget | None = None
        self.settings = None  # ronypdf.settings.Settings, lo crea la ventana principal
        self.passwords = PasswordStore()
        self.last_dir = str(Path.home() / "Documents") if (Path.home() / "Documents").exists() else str(Path.home())
        self.busy = False
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(1)
        self._task: Task | None = None

    def remember_dir(self, path: str) -> None:
        p = Path(path)
        self.last_dir = str(p if p.is_dir() else p.parent)

    def add_recent(self, path: str, pages: int | None = None) -> None:
        if self.settings is not None and str(path).lower().endswith(".pdf"):
            try:
                self.settings.add_recent(path, pages)
            except Exception:  # noqa: BLE001 — los recientes nunca deben interrumpir el trabajo
                pass

    def run_task(self, label: str, fn: Callable, on_done: Callable[[object], None],
                 on_finally: Callable[[], None] | None = None, **kwargs) -> None:
        """Ejecuta ``fn(progress=..., **kwargs)`` en segundo plano con un diálogo de progreso."""
        if self.busy:
            return
        self.busy = True
        win = self.window
        central = win.centralWidget() if win else None
        if central:
            central.setEnabled(False)
        dlg = QProgressDialog(label, "Cancelar", 0, 0, win)
        dlg.setWindowTitle("Procesando")
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(400)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.setMinimumWidth(380)
        task = Task(fn, kwargs)
        self._task = task

        finished = {"yes": False}

        def finish():
            finished["yes"] = True
            self.busy = False
            self._task = None
            try:
                dlg.canceled.disconnect()  # cerrar el diálogo emite «canceled»
            except (RuntimeError, TypeError):
                pass
            dlg.reset()
            dlg.close()
            dlg.deleteLater()
            if central:
                central.setEnabled(True)
            if win and hasattr(win, "set_status"):
                win.set_status("")
            if on_finally:
                on_finally()

        def on_progress(i, n, msg):
            if n > 0:
                dlg.setMaximum(n)
                dlg.setValue(min(i, n))
            if msg and not finished["yes"]:
                dlg.setLabelText(f"{label}\n{msg}")

        def on_ok(result):
            finish()
            on_done(result)

        def on_fail(msg):
            finish()
            QMessageBox.warning(win, "No se pudo completar", msg)

        def on_cancel():
            finish()
            if win and hasattr(win, "set_status"):
                win.set_status("Operación cancelada", 4000)

        def request_cancel():
            task.cancel_requested = True
            dlg.setLabelText("Cancelando…")

        task.signals.progress.connect(on_progress)
        task.signals.done.connect(on_ok)
        task.signals.failed.connect(on_fail)
        task.signals.cancelled.connect(on_cancel)
        dlg.canceled.connect(request_cancel)
        self._pool.start(task)

    def run_silent(self, fn: Callable, al_terminar: Callable[[object], None] | None = None,
                   al_fallar: Callable[[str], None] | None = None, **kwargs) -> Task:
        """Ejecuta ``fn(progress=..., **kwargs)`` en el mismo hilo de trabajo que ``run_task`` (y con el mismo
        candado de PyMuPDF), pero sin cuadro de progreso, sin deshabilitar la ventana y sin marcar ``busy``.

        Sirve para el autoguardado: si la persona lanza una tarea normal mientras tanto, esa tarea espera su
        turno.  Un fallo no muestra nada: llega como texto a ``al_fallar``.  Devuelve la tarea (poner
        ``task.cancel_requested = True`` la detiene en su siguiente aviso de progreso)."""
        if not hasattr(self, "_silenciosas"):
            self._silenciosas: set[Task] = set()

        def envuelta(progress=None, **kw):
            try:
                return fn(progress=progress, **kw)
            except (core.UserError, Cancelled):
                raise
            except MemoryError as exc:
                raise core.UserError("No hay suficiente memoria para procesar este archivo.") from exc
            except Exception as exc:  # noqa: BLE001 — en silencio: nada a la consola ni en pantalla
                raise core.UserError(f"{type(exc).__name__}: {exc}") from exc

        task = Task(envuelta, kwargs)
        self._silenciosas.add(task)       # la tarea no se borra sola: hay que conservarla hasta que termine

        def terminar(resultado):
            self._silenciosas.discard(task)
            if al_terminar is not None:
                al_terminar(resultado)

        def fallar(mensaje):
            self._silenciosas.discard(task)
            if al_fallar is not None:
                al_fallar(mensaje)

        task.signals.done.connect(terminar)
        task.signals.failed.connect(fallar)
        task.signals.cancelled.connect(lambda: fallar("Se canceló."))
        self._pool.start(task)
        return task


ctx = Context()


# --------------------------------------------------------------------------
# Textos propios de Qt en español
# --------------------------------------------------------------------------

class TraductorQt(QTranslator):
    """Pone en español los textos que trae Qt: los botones de los diálogos estándar (Sí / No / Cancelar
    de ``QMessageBox.question``, Aceptar / Cancelar de ``QInputDialog``…) y el menú contextual de los
    campos de texto.  Sin archivos .qm: así funciona igual en el ejecutable portable."""

    TEXTOS = {
        "QPlatformTheme": {
            "OK": "Aceptar", "&OK": "&Aceptar", "Save": "Guardar", "&Save": "&Guardar",
            "Save All": "Guardar todo", "Open": "Abrir", "&Yes": "&Sí", "Yes to &All": "Sí a &todo",
            "&No": "&No", "N&o to All": "N&o a todo", "Abort": "Anular", "Retry": "Reintentar",
            "Ignore": "Omitir", "Close": "Cerrar", "&Close": "&Cerrar", "Cancel": "Cancelar",
            "&Cancel": "&Cancelar", "Discard": "Descartar", "Don't Save": "No guardar", "Help": "Ayuda",
            "Apply": "Aplicar", "Reset": "Restablecer", "Restore Defaults": "Restaurar valores predeterminados",
        },
        "QMessageBox": {"Show Details...": "Mostrar detalles…", "Hide Details...": "Ocultar detalles…"},
        "QLineEdit": {
            "&Undo": "&Deshacer", "&Redo": "&Rehacer", "Cu&t": "Cor&tar", "&Copy": "&Copiar",
            "&Paste": "&Pegar", "Delete": "Eliminar", "Select All": "Seleccionar todo",
        },
        "QWidgetTextControl": {
            "&Undo": "&Deshacer", "&Redo": "&Rehacer", "Cu&t": "Cor&tar", "&Copy": "&Copiar",
            "Copy &Link Location": "Copiar la &dirección del enlace", "&Paste": "&Pegar",
            "Delete": "Eliminar", "Select All": "Seleccionar todo",
        },
    }

    def translate(self, context, source, disambiguation=None, n=-1):  # noqa: N802 — método de Qt
        return self.TEXTOS.get(context, {}).get(source)   # None = sin traducción: Qt usa el original


def instalar_traductor(app: QCoreApplication) -> TraductorQt:
    """Instala ``TraductorQt`` en la aplicación (hay que guardar la referencia que devuelve)."""
    traductor = TraductorQt(app)
    app.installTranslator(traductor)
    return traductor


# --------------------------------------------------------------------------
# Utilidades de archivos y diálogos
# --------------------------------------------------------------------------

def open_path(path: str) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def reveal(path: str) -> None:
    path = os.path.normpath(path)
    if os.name == "nt":
        if os.path.isdir(path):
            subprocess.Popen(["explorer", path])
        else:
            subprocess.Popen(["explorer", "/select,", path])
    else:
        open_path(os.path.dirname(path) if os.path.isfile(path) else path)


def suggest_output(source: str, suffix: str, ext: str = ".pdf") -> str:
    p = Path(source)
    return str(core.unique_path(p.with_name(f"{p.stem}{suffix}{ext}")))


def ask_save(parent: QWidget, default: str, flt: str = PDF_FILTER, title: str = "Guardar como") -> str | None:
    path, _ = QFileDialog.getSaveFileName(parent, title, default, flt)
    if not path:
        return None
    ext = ".txt" if "txt" in flt else ".pdf"
    if not path.lower().endswith(ext):
        path += ext
    ctx.remember_dir(path)
    return path


def ask_dir(parent: QWidget, default: str, title: str = "Elige la carpeta de destino") -> str | None:
    path = QFileDialog.getExistingDirectory(parent, title, default)
    if path:
        ctx.remember_dir(path)
    return path or None


def ask_open(parent: QWidget, multi: bool, flt: str = PDF_FILTER, title: str | None = None) -> list[str]:
    title = title or ("Elige los archivos" if multi else "Elige un archivo")
    if multi:
        paths, _ = QFileDialog.getOpenFileNames(parent, title, ctx.last_dir, flt)
    else:
        path, _ = QFileDialog.getOpenFileName(parent, title, ctx.last_dir, flt)
        paths = [path] if path else []
    if paths:
        ctx.remember_dir(paths[0])
    return paths


def new_subfolder(parent_dir: str, name: str) -> str:
    return str(core.unique_path(Path(parent_dir) / core.safe_filename(name)))


def view_in_app(path: str) -> None:
    """Abre un PDF en el visor de la aplicación."""
    win = ctx.window
    if win is not None and "viewer" in getattr(win, "pages", {}):
        win.navigate("viewer")
        win.pages["viewer"].load(path)


def show_done(parent: QWidget, title: str, detail: str = "", file: str | None = None,
              folder: str | None = None) -> None:
    box = QMessageBox(parent)
    box.setWindowTitle("Listo")
    box.setIconPixmap(pixmap("check", "#1F9D55", 40, 2.2))
    box.setText(f"<b style='font-size:11pt'>{title}</b>")
    if detail:
        box.setInformativeText(detail)
    b_view = None
    if file and file.lower().endswith(".pdf") and ctx.window is not None \
            and getattr(ctx.window, "stack", None) is not None \
            and ctx.window.stack.currentWidget() is not ctx.window.pages.get("viewer"):
        b_view = box.addButton("Ver aquí", QMessageBox.ButtonRole.ActionRole)
    b_open = box.addButton("Abrir", QMessageBox.ButtonRole.ActionRole) if file else None
    b_folder = box.addButton("Mostrar en carpeta" if file else "Abrir carpeta",
                             QMessageBox.ButtonRole.ActionRole) if (file or folder) else None
    box.addButton("Cerrar", QMessageBox.ButtonRole.RejectRole)
    if b_view or b_open:
        box.setDefaultButton(b_view or b_open)
    box.exec()
    clicked = box.clickedButton()
    if b_view is not None and clicked is b_view:
        view_in_app(file)
    elif b_open is not None and clicked is b_open:
        open_path(file)
    elif b_folder is not None and clicked is b_folder:
        reveal(file or folder)


def collect_paths(urls: Iterable[QUrl], exts: set[str], allow_dirs: bool = True) -> list[str]:
    out: list[str] = []
    for url in urls:
        if not url.isLocalFile():
            continue
        p = Path(url.toLocalFile())
        if p.is_dir() and allow_dirs:
            out.extend(str(f) for f in sorted(p.iterdir(), key=lambda f: f.name.lower())
                       if f.is_file() and f.suffix.lower() in exts)
        elif p.is_file() and p.suffix.lower() in exts:
            out.append(str(p))
    return out


def qimage_from_render(data: tuple[int, int, int, bytes]) -> QImage:
    w, h, stride, buf = data
    return QImage(buf, w, h, stride, QImage.Format.Format_RGB888).copy()


def hline() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setStyleSheet(f"color: {tema.color('line')};")
    return f


def label(text: str, name: str | None = None, wrap: bool = False) -> QLabel:
    lb = QLabel(text)
    if name:
        lb.setObjectName(name)
    lb.setWordWrap(wrap)
    return lb


def tool_button(text: str, icon_name: str, tip: str = "", cb: Callable | None = None,
                style: str = "Tool") -> QToolButton:
    b = QToolButton()
    b.setObjectName(style)
    b.setText(text)
    b.setIcon(icon(icon_name))
    b.setIconSize(QSize(18, 18))
    b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon if text
                         else Qt.ToolButtonStyle.ToolButtonIconOnly)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if tip:
        b.setToolTip(tip)
    if cb:
        b.clicked.connect(cb)
    return b


# --------------------------------------------------------------------------
# Zona para soltar archivos
# --------------------------------------------------------------------------

class DropArea(QFrame):
    files_dropped = Signal(list)
    clicked = Signal()

    def __init__(self, title: str, hint: str, exts: set[str], multi: bool = False, tile_key: str | None = None,
                 zone: bool = False, button_text: str | None = None):
        """Zona para soltar archivos.  ``zone=True`` es la variante grande del área principal de la plantilla
        (borde punteado sobre el pozo gris); ``tile_key`` pone el azulejo suave de la herramienta encima."""
        super().__init__()
        self.setObjectName("DropZone" if zone else "DropArea")
        self.exts = exts
        self.multi = multi
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(190)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(8)
        lay.addStretch(1)
        if tile_key:
            ic = GlyphTile(tile_key, tema.TILE_XL, soft=True)
            lay.addWidget(ic, 0, Qt.AlignmentFlag.AlignHCenter)
            lay.addSpacing(6)
        else:
            ic = QLabel()
            ic.setPixmap(pixmap("upload", tema.color("ink-3"), 36, 1.7))
            lay.addWidget(ic, 0, Qt.AlignmentFlag.AlignHCenter)
        t = label(title, "DropTitle", wrap=True)
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        h = label(hint, "Hint", wrap=True)
        h.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_lb, self.hint_lb = t, h
        btn = QPushButton(button_text or ("Elegir archivo…" if not multi else "Elegir archivos…"))
        btn.setObjectName("Secondary")     # el único botón principal de la pantalla es el de la barra de acción
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(self.clicked)
        self.button = btn
        for w in (t, h):
            lay.addWidget(w)
        lay.addSpacing(8)
        lay.addWidget(btn, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addStretch(1)

    def _set_hover(self, on: bool):
        self.setProperty("hover", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and collect_paths(e.mimeData().urls(), self.exts, self.multi):
            e.acceptProposedAction()
            self._set_hover(True)

    def dragLeaveEvent(self, e):
        self._set_hover(False)

    def dropEvent(self, e):
        self._set_hover(False)
        paths = collect_paths(e.mimeData().urls(), self.exts, self.multi)
        if paths:
            e.acceptProposedAction()
            self.files_dropped.emit(paths if self.multi else paths[:1])

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()


# --------------------------------------------------------------------------
# Selector de un único PDF
# --------------------------------------------------------------------------

class _ForwardDrops(QFrame):
    """Marco que reenvía lo que se suelta sobre él a una DropArea."""

    def __init__(self, target: DropArea):
        super().__init__()
        self.target = target
        self.setAcceptDrops(True)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and collect_paths(e.mimeData().urls(), self.target.exts, False):
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.target.dropEvent(e)


class _ReenviarSoltar(QObject):
    """Filtro de eventos: lo que se suelta sobre los widgets vigilados va a una ``DropArea``."""

    def __init__(self, target: DropArea):
        super().__init__(target)
        self.target = target
        self._vigilados: set[int] = set()

    def vigilar(self, w: QWidget) -> None:
        if id(w) in self._vigilados:
            return
        self._vigilados.add(id(w))
        w.setAcceptDrops(True)
        w.installEventFilter(self)

    def eventFilter(self, obj, e):  # noqa: N802
        t = e.type()
        if t in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
            if e.mimeData().hasUrls() and collect_paths(e.mimeData().urls(), self.target.exts, False):
                e.acceptProposedAction()
                return True
            return False
        if t == QEvent.Type.Drop:
            self.target.dropEvent(e)
            return True
        return False


class SingleFilePicker(QWidget):
    """Elige un PDF.  Dos formas de usarlo:

    * Clásica (páginas sin migrar): el propio widget, con la zona para soltar y, ya elegido, la tarjeta.
    * Compacta (plantilla 2.0): ``ToolPage.use_picker(picker)``.  El widget queda oculto; el archivo se ve
      como ``file_chip()`` en el encabezado y, mientras no hay archivo, ``drop_zone()`` ocupa el área
      principal.  Las dos piezas se crean una sola vez y se mantienen al día solas.
    """

    changed = Signal(object)  # str | None

    def __init__(self, title: str = "Arrastra un PDF aquí",
                 hint: str = "o haz clic para buscarlo en tu equipo",
                 ask_password: bool = True):
        super().__init__()
        self.path: str | None = None
        self.info: core.PdfInfo | None = None
        self.ask_password = ask_password
        self.drop_title, self.drop_hint = title, hint
        self.detail = ""
        self._chip: FileChip | None = None
        self._zone: DropArea | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.drop = DropArea(title, hint, core.PDF_EXTS)
        self.drop.clicked.connect(self.browse)
        self.drop.files_dropped.connect(lambda ps: self.set_path(ps[0]))
        lay.addWidget(self.drop)

        card = _ForwardDrops(self.drop)
        self.card = card
        card.setObjectName("FileCard")
        row = QHBoxLayout(card)
        row.setContentsMargins(14, 12, 10, 12)
        row.setSpacing(12)
        row.addWidget(PdfThumb(28, 36))
        col = QVBoxLayout()
        col.setSpacing(2)
        self.name_lb = label("", "FileName")
        self.detail_lb = label("", "Hint")
        col.addWidget(self.name_lb)
        col.addWidget(self.detail_lb)
        row.addLayout(col, 1)
        b_view = tool_button("", "eye", "Abrir con el visor predeterminado",
                             lambda: self.path and open_path(self.path))
        b_change = QPushButton("Cambiar…")
        b_change.clicked.connect(self.browse)
        b_clear = tool_button("", "remove", "Quitar archivo", self.clear)
        for w in (b_view, b_change, b_clear):
            row.addWidget(w)
        lay.addWidget(card)
        card.hide()
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

    def _show_card(self, on: bool):
        self.card.setVisible(on)
        self.drop.setVisible(not on)
        # Alto explícito: algunos diseños con etiquetas de varias líneas le daban de más.
        if on:
            self.setFixedHeight(self.card.sizeHint().height())
        else:
            self.setMinimumHeight(0)
            self.setMaximumHeight(16777215)
        self.updateGeometry()

    # --- forma compacta (plantilla 2.0)
    def file_chip(self) -> FileChip:
        """La ficha del archivo para el encabezado de la herramienta (oculta mientras no hay archivo)."""
        if self._chip is None:
            self._chip = FileChip(removable=True)
            self._chip.change_clicked.connect(self.browse)
            self._chip.remove_clicked.connect(self.clear)
            self._chip.max_nombre = 190       # deja sitio a «×» sin ensanchar el encabezado a 1000 px
            # «Abrir con el visor predeterminado» y «Quitar» también con clic derecho sobre la ficha
            self._chip.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)
            a_ver = QAction(icon("eye"), "Abrir con el visor predeterminado", self._chip)
            a_ver.triggered.connect(lambda: self.path and open_path(self.path))
            a_quitar = QAction(icon("remove"), "Quitar archivo", self._chip)
            a_quitar.triggered.connect(self.clear)
            self._chip.addActions([a_ver, a_quitar])
            self._chip.thumb.setToolTip("Clic derecho: abrir con el visor predeterminado o quitar")
            self.aceptar_soltar(self._chip)
            self._sync_chip()
        return self._chip

    def aceptar_soltar(self, w: QWidget) -> None:
        """Soltar un PDF sobre ``w`` (la ficha, la vista previa) lo pone en lugar del actual."""
        if not hasattr(self, "_soltar"):
            self._soltar = _ReenviarSoltar(self.drop)
        self._soltar.vigilar(w)

    def drop_zone(self, title: str | None = None, hint: str | None = None,
                  tile_key: str | None = None) -> "DropArea":
        """La zona grande para soltar el archivo en el área principal (se usa mientras no hay archivo)."""
        if self._zone is None:
            self._zone = DropArea(title or self.drop_title, hint or self.drop_hint, core.PDF_EXTS,
                                  tile_key=tile_key, zone=True)
            self._zone.clicked.connect(self.browse)
            self._zone.files_dropped.connect(lambda ps: self.set_path(ps[0]))
        return self._zone

    def _sync_chip(self):
        if self._chip is None:
            return
        if self.path:
            self._chip.set_file(Path(self.path).name, self.detail, self.path)
        self._chip.setVisible(bool(self.path))

    def browse(self):
        paths = ask_open(self, False)
        if paths:
            self.set_path(paths[0])

    def clear(self):
        self.path = None
        self.info = None
        self.detail = ""
        self._show_card(False)
        self._sync_chip()
        self.changed.emit(None)

    @property
    def password(self) -> str | None:
        return ctx.passwords.get(self.path) if self.path else None

    def set_path(self, path: str):
        ctx.remember_dir(path)
        if self.ask_password and not ctx.passwords.ensure(path, self):
            return
        try:
            with PDF_LOCK:
                info = core.pdf_info(path, ctx.passwords.get(path))
        except core.PasswordError:
            info = None
            if self.ask_password:
                QMessageBox.warning(self, "Contraseña", "No se pudo abrir el archivo con esa contraseña.")
                return
        except core.UserError as exc:
            QMessageBox.warning(self, "Archivo no válido", str(exc))
            return
        self.path = path
        self.info = info
        self.name_lb.setText(Path(path).name)
        self.name_lb.setToolTip(path)
        if info:
            pages = f"{info.pages} página{'s' if info.pages != 1 else ''}"
            extra = " · protegido" if info.encrypted else ""
            self.detail = f"{pages} · {core.human_size(info.size)}{extra}"
        else:
            self.detail = f"Protegido · {core.human_size(os.path.getsize(path))}"
        self.detail_lb.setText(self.detail)
        self._show_card(True)
        self._sync_chip()
        ctx.add_recent(path, info.pages if info else None)
        self.changed.emit(path)


# --------------------------------------------------------------------------
# Lista de archivos reordenable
# --------------------------------------------------------------------------

class _NoEdit(QStyledItemDelegate):
    def createEditor(self, *args):
        return None


class FileList(QTreeWidget):
    changed = Signal()

    def __init__(self, columns: list[str], exts: set[str],
                 row_factory: Callable[[str], list[str] | None], editable_col: int | None = None,
                 placeholder: str = "Arrastra archivos aquí", allow_duplicates: bool = True):
        super().__init__()
        self.exts = exts
        self.row_factory = row_factory
        self.placeholder = placeholder
        self.allow_duplicates = allow_duplicates
        self.setColumnCount(len(columns))
        self.setHeaderLabels(columns)
        self.setRootIsDecorated(False)
        self.setUniformRowHeights(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setIconSize(QSize(18, 18))
        self.setMinimumHeight(220)
        hdr = self.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in range(1, len(columns)):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.editable_col = editable_col
        if editable_col is not None:
            for c in range(len(columns)):
                if c != editable_col:
                    self.setItemDelegateForColumn(c, _NoEdit(self))
            self.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                                 | QAbstractItemView.EditTrigger.EditKeyPressed
                                 | QAbstractItemView.EditTrigger.SelectedClicked)
        else:
            self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.itemChanged.connect(lambda *_: self.changed.emit())

    # --- datos
    def paths(self) -> list[str]:
        return [self.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) for i in range(self.topLevelItemCount())]

    def items(self) -> list[QTreeWidgetItem]:
        return [self.topLevelItem(i) for i in range(self.topLevelItemCount())]

    def add_paths(self, paths: Iterable[str]) -> None:
        existing = {os.path.normcase(p) for p in self.paths()}
        added = False
        for p in paths:
            if not self.allow_duplicates and os.path.normcase(p) in existing:
                continue
            cols = self.row_factory(p)
            if cols is None:
                continue
            item = QTreeWidgetItem([str(c) for c in cols])
            item.setData(0, Qt.ItemDataRole.UserRole, p)
            item.setToolTip(0, p)
            item.setIcon(0, icon("image" if core.is_image(p) else "pdf", ACCENT))
            flags = (Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsDragEnabled)
            if self.editable_col is not None and not core.is_image(p):
                flags |= Qt.ItemFlag.ItemIsEditable
            item.setFlags(flags)
            self.addTopLevelItem(item)
            existing.add(os.path.normcase(p))
            ctx.add_recent(p)
            added = True
        if added:
            ctx.remember_dir(self.paths()[-1])
            self.changed.emit()

    def remove_selected(self):
        for it in self.selectedItems():
            self.takeTopLevelItem(self.indexOfTopLevelItem(it))
        self.changed.emit()

    def clear_all(self):
        self.clear()
        self.changed.emit()

    def move_selected(self, delta: int):
        rows = sorted(self.indexOfTopLevelItem(i) for i in self.selectedItems())
        if not rows:
            return
        n = self.topLevelItemCount()
        if (delta < 0 and rows[0] == 0) or (delta > 0 and rows[-1] == n - 1):
            return
        moved = []
        for r in (rows if delta < 0 else reversed(rows)):
            it = self.takeTopLevelItem(r)
            self.insertTopLevelItem(r + delta, it)
            moved.append(it)
        self.clearSelection()
        for it in moved:
            it.setSelected(True)
        self.changed.emit()

    def sort_by_name(self):
        self.sortItems(0, Qt.SortOrder.AscendingOrder)
        self.changed.emit()

    # --- arrastrar y soltar desde el explorador
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
            paths = collect_paths(e.mimeData().urls(), self.exts)
            e.acceptProposedAction()
            if paths:
                self.add_paths(paths)
            return
        super().dropEvent(e)
        self.changed.emit()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Delete and self.state() != QAbstractItemView.State.EditingState:
            self.remove_selected()
            return
        super().keyPressEvent(e)

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.topLevelItemCount() == 0:
            p = QPainter(self.viewport())
            r = self.viewport().rect()
            pm = pixmap("upload", tema.color("line-control"), 36, 1.6)
            p.drawPixmap(r.center().x() - 18, r.center().y() - 46, pm)
            p.setPen(QColor(tema.color("ink-3")))
            p.drawText(r.adjusted(20, 10, -20, 10), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                       self.placeholder)
            p.end()

    # --- filas como tarjetas (rp-file), para la plantilla 2.0
    def set_cards(self, meta_fn: Callable[[QTreeWidgetItem], str],
                  chip_fn: Callable[[QTreeWidgetItem], str | None] | None = None) -> None:
        """Muestra cada archivo como una tarjeta (``rp-file``): miniatura con el distintivo PDF, nombre (columna
        0), una línea de datos ``meta_fn(item)`` y, a la derecha, un chip opcional ``chip_fn(item)`` en el color
        de la categoría.  Las demás columnas siguen guardando los datos (``item.text(c)``), solo dejan de verse.
        Al pasar el puntero aparece «Quitar» (×).  Arrastrar sigue reordenando; Supr quita los elegidos."""
        self.setObjectName("FileCards")
        self.setHeaderHidden(True)
        for c in range(1, self.columnCount()):
            self.setColumnHidden(c, True)
        self.setIndentation(0)
        self.setMouseTracking(True)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setItemDelegate(FileCardDelegate(self, meta_fn, chip_fn))
        repolish(self)


class FileCardDelegate(QStyledItemDelegate):
    """Pinta una fila de ``FileList`` como tarjeta de archivo (``rp-file``)."""

    ALTO = 62

    def __init__(self, fl: "FileList", meta_fn, chip_fn=None):
        super().__init__(fl)
        self.fl, self.meta_fn, self.chip_fn = fl, meta_fn, chip_fn

    def sizeHint(self, option, index):  # noqa: N802
        return QSize(max(200, option.rect.width()), self.ALTO)

    def createEditor(self, *args):  # noqa: N802
        return None

    @staticmethod
    def _quitar(r: QRectF) -> QRectF:
        return QRectF(r.right() - 40, r.center().y() - 16, 32, 32)

    def paint(self, p, option, index):
        item = self.fl.itemFromIndex(index)
        if item is None:
            return
        t = tema.tokens()
        ton = tema.tonos(cat_de(self.fl))
        r = QRectF(option.rect).adjusted(0.5, 3.5, -0.5, -3.5)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        sel = bool(option.state & QStyle.StateFlag.State_Selected)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(ton["c"] if sel else t["line-strong"] if hover else t["line"]), 1.5 if sel else 1))
        p.setBrush(QColor(ton["band"] if sel else t["surface"]))
        p.drawRoundedRect(r, 12, 12)
        path = item.data(0, Qt.ItemDataRole.UserRole) or ""
        pintar_miniatura(p, QRectF(r.left() + 9, r.center().y() - 22, 34, 44), not core.is_image(path),
                         "PDF")
        x = r.left() + 9 + 34 + 12
        derecha = r.right() - (48 if hover else 12)
        chip = self.chip_fn(item) if self.chip_fn else None
        if chip:
            fc = tema.fuente("chip")
            cw = QFontMetrics(fc).horizontalAdvance(chip) + 20
            cr = QRectF(derecha - cw, r.center().y() - 13, cw, 26)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(ton["band"] if not sel else t["surface"]))
            p.drawRoundedRect(cr, 13, 13)
            p.setFont(fc)
            p.setPen(QColor(ton["ink"]))
            p.drawText(cr, Qt.AlignmentFlag.AlignCenter, chip)
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
            p.drawPixmap(QPointF(q.center().x() - 8, q.center().y() - 8), pixmap("close", t["ink-2"], 16, 1.8))
        p.restore()

    def editorEvent(self, event, model, option, index):  # noqa: N802
        if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            r = QRectF(option.rect).adjusted(0.5, 3.5, -0.5, -3.5)
            if self._quitar(r).contains(event.position()):
                item = self.fl.itemFromIndex(index)
                if item is not None:
                    self.fl.takeTopLevelItem(self.fl.indexOfTopLevelItem(item))
                    self.fl.changed.emit()
                return True
        return super().editorEvent(event, model, option, index)


def file_list_buttons(fl: FileList, add_cb: Callable[[], None]) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(4)
    b_add = QPushButton(" Agregar archivos…")
    b_add.setIcon(icon("add"))
    b_add.clicked.connect(add_cb)
    row.addWidget(b_add)
    row.addSpacing(8)
    buttons = [
        tool_button("", "up", "Subir", lambda: fl.move_selected(-1)),
        tool_button("", "down", "Bajar", lambda: fl.move_selected(1)),
        tool_button("", "sort", "Ordenar por nombre (A→Z)", fl.sort_by_name),
        tool_button("", "delete", "Quitar seleccionados (Supr)", fl.remove_selected),
    ]
    for b in buttons:
        row.addWidget(b)
    row.addStretch(1)
    b_clear = QPushButton("Vaciar lista")
    b_clear.setObjectName("Flat")
    b_clear.clicked.connect(fl.clear_all)
    row.addWidget(b_clear)

    def refresh():
        has_sel = bool(fl.selectedItems())
        for b in buttons[:2] + buttons[3:]:
            b.setEnabled(has_sel)
        buttons[2].setEnabled(fl.topLevelItemCount() > 1)
        b_clear.setEnabled(fl.topLevelItemCount() > 0)

    fl.itemSelectionChanged.connect(refresh)
    fl.changed.connect(refresh)
    refresh()
    return row


# --------------------------------------------------------------------------
# Vista previa de la primera página con cambios aplicados
# --------------------------------------------------------------------------

class PreviewPane(QFrame):
    def __init__(self, render: Callable[[], tuple[int, int, int, bytes] | None]):
        super().__init__()
        self.setObjectName("Preview")
        self.render_fn = render
        self.setMinimumSize(280, 360)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 12)
        cap = label("Vista previa", "Hint")
        lay.addWidget(cap)
        self.img = QLabel()
        self.img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.img.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        lay.addWidget(self.img, 1)
        self._qimg: QImage | None = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._refresh)

    def schedule(self):
        self.timer.start()

    def _refresh(self):
        if ctx.busy or not PDF_LOCK.acquire(blocking=False):
            self.timer.start()
            return
        try:
            data = self.render_fn()
        except core.UserError:
            data = None
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            data = None
        finally:
            PDF_LOCK.release()
        self._qimg = qimage_from_render(data) if data else None
        self._update_pixmap()

    def _update_pixmap(self):
        if not self._qimg:
            self.img.setText("Elige un PDF para ver la vista previa")
            self.img.setStyleSheet(f"color: {MUTED};")
            return
        self.img.setStyleSheet("")
        dpr = self.devicePixelRatioF()
        size = self.img.size() * dpr
        pm = QPixmap.fromImage(self._qimg).scaled(size, Qt.AspectRatioMode.KeepAspectRatio,
                                                  Qt.TransformationMode.SmoothTransformation)
        pm.setDevicePixelRatio(dpr)
        self.img.setPixmap(pm)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._update_pixmap()


class ColorButton(QPushButton):
    changed = Signal()

    def __init__(self, color: str = "#808080"):
        super().__init__()
        self.color = QColor(color)
        self.setFixedWidth(64)
        self.clicked.connect(self.pick)
        self._apply()

    def _apply(self):
        self.setStyleSheet(f"QPushButton {{ background: {self.color.name()}; border: 1px solid #C5C9CF;"
                           f" border-radius: 6px; min-height: 22px; }}")

    def pick(self):
        c = QColorDialog.getColor(self.color, self, "Elige un color")
        if c.isValid():
            self.color = c
            self._apply()
            self.changed.emit()

    def rgb(self) -> tuple[float, float, float]:
        return (self.color.redF(), self.color.greenF(), self.color.blueF())


# --------------------------------------------------------------------------
# Página base de herramienta
# --------------------------------------------------------------------------

class ToolPage(QWidget):
    """Plantilla de toda herramienta (``rp-toolpage``), de arriba abajo:

    1. Encabezado: azulejo ``lg`` de la categoría, título ``title-1``, una línea y, a la derecha, un espacio
       para la ficha del archivo o botones (``set_header_widget``).
    2. Cuerpo de dos columnas: panel de ajustes (380 px; 340 px con la ventana de menos de 1100 px) con pasos
       numerados (``step``) y, a la derecha, el área principal (``set_main``): vista previa, lista de archivos
       o zona para soltar.
    3. Barra de acción: resumen en chips y una frase (``set_summary`` / ``footer_info``), botones secundarios
       (``add_secondary``) y el único botón principal (``action``), que nombra el resultado.

    Contrato de las subclases (igual que antes): ``key``, ``title``, ``subtitle``, ``icon_name``,
    ``action_text``; ``build()``, ``ready()``, ``run()``, ``accept_files(rutas)`` y ``refresh()``.

    Compatibilidad: ``group(título)`` es ``step(título)`` y ``self.body`` sigue aceptando ``addWidget`` /
    ``addLayout``.  Una página que nunca llama a ``set_main`` se muestra en una sola columna centrada (los
    pasos en un panel blanco, hasta 860 px), para que las pantallas sin migrar se vean ordenadas.
    Guía completa: ``docs/diseno/PLANTILLA.md``.
    """

    key = ""
    title = ""
    subtitle = ""
    icon_name = ""
    action_text = "Procesar"

    def __init__(self):
        super().__init__()
        self.setObjectName("Page")
        self.cat = tema.categoria_de(self.key)
        self.setProperty("cat", self.cat)
        self._main: QWidget | None = None
        self._picker: SingleFilePicker | None = None
        self._steps_panel: QFrame | None = None
        self._cards: list[QFrame] = []
        self._main_stack: QStackedWidget | None = None
        self.steps: StepList | None = None
        self.panel: QFrame | None = None
        self.panel_scroll: QScrollArea | None = None
        self.main_frame: QFrame | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._outer = outer

        # 1. encabezado
        self.header = QWidget()
        self.header.setObjectName("ToolHead")
        hl = QHBoxLayout(self.header)
        hl.setContentsMargins(32, 22, 32, 18)
        hl.setSpacing(16)
        self.head_tile = GlyphTile(tema.azulejo_de(self.key) if self.key else (self.icon_name or "pdf"),
                                   tema.TILE_LG, self.cat if self.cat != "marca" else None)
        hl.addWidget(self.head_tile, 0, Qt.AlignmentFlag.AlignVCenter)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.title_lb = label(self.title, "ToolTitle", wrap=True)   # en ventanas angostas baja de renglón
        self.subtitle_lb = label(self.subtitle, "ToolSubtitle", wrap=True)
        self.subtitle_lb.setVisible(bool(self.subtitle))
        titles.addStretch(1)
        titles.addWidget(self.title_lb)
        titles.addWidget(self.subtitle_lb)
        titles.addStretch(1)
        hl.addLayout(titles, 1)
        self.header_slot = QHBoxLayout()
        self.header_slot.setSpacing(8)
        hl.addLayout(self.header_slot)
        outer.addWidget(self.header)

        # 2. cuerpo: se arma después de build(), según la página use set_main o no
        self._body_w = QWidget()
        self._body_w.setObjectName("PanelBody")
        self.body = QVBoxLayout(self._body_w)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(16)

        # 3. barra de acción
        self.actionbar = QFrame()
        self.actionbar.setObjectName("ActionBar")
        fl = QHBoxLayout(self.actionbar)
        fl.setContentsMargins(32, 12, 32, 12)
        fl.setSpacing(12)
        self.summary = QWidget()
        sl = QHBoxLayout(self.summary)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(8)
        self._chips = QHBoxLayout()
        self._chips.setSpacing(8)
        sl.addLayout(self._chips)
        self.footer_info = label("", "Summary")
        sl.addWidget(self.footer_info, 1)
        fl.addWidget(self.summary, 1)
        self.action = QPushButton(self.action_text)
        self.action.setObjectName("Primary")
        self.action.setProperty("tam", "lg")
        self.action.setCursor(Qt.CursorShape.PointingHandCursor)
        self.action.clicked.connect(self._run)
        fl.addWidget(self.action)

        self.build()
        self._montar()
        outer.addWidget(self.actionbar)
        self.refresh()

    # ------------------------------------------------------------------ para sobrescribir
    def build(self):
        raise NotImplementedError

    def ready(self) -> bool:
        return True

    def run(self):
        raise NotImplementedError

    def accept_files(self, paths: list[str]) -> None:
        """Archivos recibidos desde la línea de comandos o la pantalla de inicio."""

    # ------------------------------------------------------------------ comunes
    def refresh(self):
        if hasattr(self, "action"):
            self.action.setEnabled(self.ready())

    def _run(self):
        if not self.ready() or ctx.busy:
            return
        self.run()

    # ------------------------------------------------------------------ API de la plantilla
    def step(self, title: str | None, help: str | None = None) -> QVBoxLayout:  # noqa: A002
        """Agrega un paso numerado al panel de ajustes y devuelve el QVBoxLayout de sus controles.

        El número es automático (los pasos ocultos no cuentan).  ``layout.parentWidget()`` es el ``Step``:
        ocultarlo oculta el paso entero.  Un «1. » al comienzo del título se quita (lo pone la plantilla).
        ``step(None)`` agrega un bloque sin número ni título dentro del panel."""
        if title:
            title = re.sub(r"^\s*\d+\s*[.)]\s*", "", title)
        if self._steps_panel is None:
            self._steps_panel = QFrame()
            self._steps_panel.setObjectName("StepsPanel")
            vl = QVBoxLayout(self._steps_panel)
            vl.setContentsMargins(16, 18, 16, 16)
            self.steps = StepList()
            vl.addWidget(self.steps)
            self.body.addWidget(self._steps_panel)
        return self.steps.add_step(title, help).body

    def group(self, title: str | None = None) -> QVBoxLayout:
        """Compatibilidad: con título es ``step(título)``; sin título, una tarjeta blanca suelta."""
        if title:
            return self.step(title)
        card = QFrame()
        card.setObjectName("Card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)
        self.body.addWidget(card)
        self._cards.append(card)
        return lay

    def set_main(self, widget: QWidget) -> QWidget:
        """Pone ``widget`` en el área principal (columna derecha, pozo gris redondeado) y activa las dos
        columnas.  Llámalo dentro de ``build()``."""
        self._main = widget
        if self._main_stack is not None:          # después de construida: se reemplaza en caliente
            self._poner_main()
        return widget

    def set_header_widget(self, widget: QWidget | None) -> None:
        """Lo que va a la derecha del encabezado: la ficha del archivo (``picker.file_chip()``) o botones."""
        while self.header_slot.count():
            it = self.header_slot.takeAt(0)
            if it.widget():
                it.widget().setParent(None)
        if widget is not None:
            self.header_slot.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)

    def add_secondary(self, text: str, cb: Callable | None = None) -> QPushButton:
        """Botón ``secondary`` en la barra de acción, a la izquierda del principal."""
        b = QPushButton(text)
        b.setObjectName("Secondary")
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        if cb:
            b.clicked.connect(cb)
        lay = self.actionbar.layout()
        lay.insertWidget(lay.indexOf(self.action), b)
        return b

    def set_summary(self, chips: Iterable = (), text: str | None = None) -> None:
        """Resumen de la barra de acción: chips («4 archivos», o ``(texto, tono)`` con tono ok, warn, danger,
        info o cat) y una frase (``None`` deja la que haya)."""
        while self._chips.count():
            it = self._chips.takeAt(0)
            if it.widget():
                it.widget().hide()  # deleteLater tarda un ciclo: sin esto se ven encima de la frase
                it.widget().deleteLater()
        for c in chips:
            texto, tono = (c, "neutral") if isinstance(c, str) else (c[0], c[1])
            self._chips.addWidget(Chip(texto, tono))
        if text is not None:
            self.footer_info.setText(text)

    def use_picker(self, picker: "SingleFilePicker", empty_title: str | None = None,
                   empty_hint: str | None = None) -> None:
        """Forma compacta del selector de un PDF: la ficha del archivo va al encabezado y, mientras no hay
        archivo, una zona grande para soltarlo ocupa el área principal (con el azulejo de la herramienta).
        Cuando hay archivo, el área principal muestra lo que se pasó a ``set_main``.  Llámalo en ``build()``."""
        self._picker = picker
        picker.setParent(self)
        picker.hide()
        self.set_header_widget(picker.file_chip())
        picker.drop_zone(empty_title, empty_hint, tema.azulejo_de(self.key) if self.key else None)
        picker.changed.connect(lambda *_: self._sync_main())
        if self._main_stack is not None:
            self._poner_main()

    # ------------------------------------------------------------------ montaje
    def _dos_columnas(self) -> bool:
        return self._main is not None or self._picker is not None

    def _montar(self):
        if self._dos_columnas():
            area = QWidget()
            area.setObjectName("ToolBody")
            h = QHBoxLayout(area)
            h.setContentsMargins(32, 0, 32, 16)
            h.setSpacing(16)
            self.panel = QFrame()
            self.panel.setObjectName("ToolPanel")
            pl = QVBoxLayout(self.panel)
            pl.setContentsMargins(1, 1, 1, 1)
            scroll = QScrollArea()
            scroll.setObjectName("PanelScroll")
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.viewport().setAutoFillBackground(False)
            self._body_w.setAutoFillBackground(False)
            self.body.setContentsMargins(19, 19, 19, 19)
            if self._steps_panel is not None:
                self._steps_panel.setProperty("plano", "true")
                self._steps_panel.layout().setContentsMargins(0, 0, 0, 0)
            for card in self._cards:
                card.setProperty("plano", "true")
                card.layout().setContentsMargins(0, 0, 0, 0)
            self.body.addStretch(1)
            scroll.setWidget(self._body_w)
            # Con una altura mínima explícita, QScrollArea usa la altura para el ancho real (heightForWidth) y no
            # la suma de las etiquetas partidas a un ancho de referencia: sin esto salía una barra de desplazamiento
            # de más, y al angostarse el panel por ella las tarjetas de opción cortaban su tercer renglón.
            self._body_w.setMinimumHeight(1)
            pl.addWidget(scroll)
            self.panel_scroll = scroll
            self.main_frame = QFrame()
            self.main_frame.setObjectName("ToolMain")
            ml = QVBoxLayout(self.main_frame)
            ml.setContentsMargins(0, 0, 0, 0)
            ml.setSpacing(0)
            self._main_stack = QStackedWidget()
            self._main_stack.setObjectName("MainStack")
            ml.addWidget(self._main_stack)
            h.addWidget(self.panel)
            h.addWidget(self.main_frame, 1)
            self._ajustar_panel()
            self._poner_main()
            self._outer.addWidget(area, 1)
        else:
            scroll = QScrollArea()
            scroll.setObjectName("PageScroll")
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            holder = QWidget()
            holder.setObjectName("ScrollBody")
            hl = QHBoxLayout(holder)
            hl.setContentsMargins(24, 0, 24, 24)       # algo menos de aire: las pantallas sin migrar son densas
            hl.setSpacing(0)
            hl.addStretch(1)
            self._body_w.setMaximumWidth(tema.COLUMNA_UNICA_MAX)
            self._body_w.setProperty("denso", "true")      # controles a 13 px (ver style.py)
            repolish(self._body_w)
            self._body_w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            hl.addWidget(self._body_w, 1000)
            hl.addStretch(1)
            # El encabezado va dentro de la columna, alineado con ella (y se desplaza con el contenido).
            self._outer.removeWidget(self.header)
            self.header.layout().setContentsMargins(0, 22, 0, 2)
            self.body.insertWidget(0, self.header)
            self.body.addStretch(1)
            scroll.setWidget(holder)
            self._outer.addWidget(scroll, 1)

    def _poner_main(self):
        st = self._main_stack
        while st.count():
            st.removeWidget(st.widget(0))
        if self._picker is not None:
            st.addWidget(self._picker.drop_zone())
        if self._main is not None:
            st.addWidget(self._main)
            if self._picker is not None:
                self._picker.aceptar_soltar(self._main)
        self._sync_main()

    def _sync_main(self):
        st = self._main_stack
        if st is None:
            return
        if self._picker is not None and not self._picker.path:
            st.setCurrentWidget(self._picker.drop_zone())
        elif self._main is not None:
            st.setCurrentWidget(self._main)

    def _ajustar_panel(self):
        if self.panel is None:
            return
        win = self.window()
        ancho = win.width() if win is not None and win is not self else self.width()
        self.panel.setFixedWidth(tema.PANEL_ANCHO_ESTRECHO if ancho < tema.ANCHO_VENTANA_ESTRECHA
                                 else tema.PANEL_ANCHO)

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._ajustar_panel()

    def showEvent(self, e):  # noqa: N802
        super().showEvent(e)
        self._ajustar_panel()
