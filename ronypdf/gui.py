"""Ventana principal."""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QPalette, QShortcut
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QMainWindow, QMessageBox, QStackedWidget,
                               QVBoxLayout, QWidget)

from . import APP_NAME, core, diagnostico, extensiones, tema
from .art import app_icon
from .home import HomePage
from .organizer import OrganizePage
from .pantallas import Pantallas
from .settings import Settings
from .paleta import PaletaComandos
from .sidebar import Sidebar, TopBar
from .style import QSS, instalar_ajustes
from .tools import (CompressPage, ExtractPage, ImagesToPdfPage, MergePage, MetadataPage, OcrPage,
                    PageNumbersPage, PdfToImagesPage, ProtectPage, SplitPage, UnlockPage,
                    WatermarkPage)
from .viewer import ViewerPage
from .widgets import ctx, instalar_traductor

PAGE_CLASSES = {cls.key: cls for cls in (
    ViewerPage, MergePage, SplitPage, OrganizePage, CompressPage, OcrPage, PdfToImagesPage, ImagesToPdfPage,
    ExtractPage, WatermarkPage, PageNumbersPage, MetadataPage, ProtectPage, UnlockPage)}

# (clave, título, azulejo, [(página, título en el menú, azulejo)])
SECTIONS = [
    ("ver", "Ver y editar", "viewer", [("viewer", "Ver y editar PDF", "viewer")]),
    ("organizar", "Organizar", "sec_organize", [("merge", "Unir PDF", "merge"), ("split", "Dividir PDF", "split"),
                                                ("organize", "Organizar páginas", "organize")]),
    ("optimizar", "Optimizar", "compress", [("compress", "Comprimir PDF", "compress")]),
    ("convertir", "Convertir", "sec_convert", [("ocr", "Reconocer texto (OCR)", "ocr"),
                                              ("toimage", "PDF a imágenes", "toimage"),
                                              ("fromimage", "Imágenes a PDF", "fromimage"),
                                              ("extract", "Extraer texto e imágenes", "extract")]),
    ("editar", "Editar", "sec_edit", [("watermark", "Marca de agua", "watermark"),
                                      ("numbers", "Numerar páginas", "numbers"),
                                      ("metadata", "Propiedades", "metadata")]),
    ("seguridad", "Seguridad", "sec_security", [("protect", "Proteger con contraseña", "protect"),
                                                ("unlock", "Quitar contraseña", "unlock")]),
]
HOME = ("home", "Inicio", "home")
extensiones.aplicar(PAGE_CLASSES, SECTIONS)   # herramientas de los módulos *_ui.py


class MainWindow(QMainWindow):
    pagina_creada = Signal(str, object)     # (clave, pantalla): una pantalla se construyó (la primera vez que se abre)

    def __init__(self):
        super().__init__()
        ctx.window = self
        if ctx.settings is None:
            ctx.settings = Settings()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.resize(1240, 820)
        self.setMinimumSize(1000, 660)
        tema.cargar_fuentes()
        instalar_ajustes(QApplication.instance())

        root = QWidget()
        root.setObjectName("Content")
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        # Primero el esqueleto dentro de la ventana: cada parte que se le agrega después cambia de padre una sola
        # vez (con la hoja de estilos, cada cambio de padre vuelve a aplicar el estilo a todo lo que contiene).
        self.setCentralWidget(root)

        self.sidebar = Sidebar(HOME, SECTIONS, ctx.settings)
        self.sidebar.navigate.connect(self.navigate)
        self.stack = QStackedWidget()
        # Cada pantalla se construye la primera vez que se abre (ver pantallas.py).
        self.pages = Pantallas(["home"] + [k for _s, _l, _t, items in SECTIONS for k, _x, _a in items],
                               self._crear_pagina, self._add_page)

        self.topbar = TopBar()
        self.topbar.buscar.connect(lambda: self.abrir_paleta(""))
        self.topbar.inicio.connect(lambda: self.navigate("home"))
        derecha = QWidget()
        dl = QVBoxLayout(derecha)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(0)
        dl.addWidget(self.topbar)
        dl.addWidget(self.stack, 1)
        h.addWidget(self.sidebar)
        h.addWidget(derecha, 1)

        self.home = HomePage(SECTIONS)
        self.home.navigate.connect(self.navigate)
        self.home.files_dropped.connect(self.open_files)
        self.home.buscar.connect(self.abrir_paleta)
        self.pages.registrar("home", self.home)
        self.paleta = PaletaComandos(self, self._herramientas_paleta,
                                     lambda: ctx.settings.recent() if ctx.settings else [])
        self.paleta.abrir_herramienta.connect(self.navigate)
        self.paleta.abrir_archivo.connect(lambda p: self.open_files([p]))
        self.statusBar().setSizeGripEnabled(False)
        self.statusBar().hide()

        sc = QShortcut(QKeySequence("Ctrl+K"), self)
        sc.activated.connect(lambda: self.abrir_paleta(""))
        self._auto_collapsed = False
        self.navigate("home")
        extensiones.al_iniciar(self)      # p. ej. ofrecer la recuperación de un trabajo sin guardar

    def _crear_pagina(self, key: str) -> QWidget:
        visible = self.isVisible()
        if visible:                                    # la primera vez que se abre puede tardar un momento
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            return PAGE_CLASSES[key]()
        finally:
            if visible:
                QApplication.restoreOverrideCursor()

    def _add_page(self, key: str, page: QWidget):
        self.stack.addWidget(page)
        self.pagina_creada.emit(key, page)

    def _herramientas_paleta(self) -> list[tuple[str, str, str, str]]:
        """(clave, título, categoría, glifo) de cada herramienta, en el orden del menú."""
        return [(k, t, sec, a) for sec, _l, _t, items in SECTIONS for k, t, a in items if k in self.pages]

    def abrir_paleta(self, texto: str = "") -> None:
        """Paleta Ctrl K sobre la ventana, con ``texto`` ya escrito."""
        self.paleta.abrir(texto or "")

    def navigate(self, key: str):
        diagnostico.evento(f"pantalla: {key}", memoria=True)
        page = self.pages[key]
        self.stack.setCurrentWidget(page)
        self.sidebar.set_current(key)
        # Barra superior: migas de pan (el visor tiene su propia barra de pestañas y herramientas)
        sec = next(((s, lbl) for s, lbl, _t, items in SECTIONS if any(k == key for k, _x, _a in items)), None)
        titulo = next((t for _s, _l, _t, items in SECTIONS for k, t, _a in items if k == key), None)
        self.topbar.set_ruta(sec[1] if sec else None, titulo)
        self.topbar.setVisible(key != "viewer")
        # En el editor el menú se contrae solo, para dar espacio al documento.
        if key == "viewer":
            if not self.sidebar.is_collapsed():
                self.sidebar.set_collapsed(True, remember=False)
                self._auto_collapsed = True
        elif self._auto_collapsed:
            self._auto_collapsed = False
            self.sidebar.set_collapsed(self._menu_contraido_preferido(), remember=False)
        if key != "viewer":
            self.setWindowTitle(APP_NAME)

    ANCHO_MENU_EXPANDIDO = 1100     # con la ventana más angosta, el menú completo se contrae solo al carril

    def _menu_contraido_preferido(self) -> bool:
        return bool(ctx.settings.get("sidebar_collapsed", True)) or self.width() < self.ANCHO_MENU_EXPANDIDO

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        antes, ahora = e.oldSize().width(), e.size().width()
        limite = self.ANCHO_MENU_EXPANDIDO
        if ahora < limite <= antes or (antes < 0 and ahora < limite):
            # Al angostar la ventana el menú de 248 px se vuelve carril (las pantallas necesitan el ancho);
            # al volver a agrandarla, se respeta otra vez lo que la persona eligió.
            if not self.sidebar.is_collapsed():
                self.sidebar.set_collapsed(True, remember=False)
        elif 0 <= antes < limite <= ahora and self.stack.currentWidget() is not self.pages.get("viewer"):
            self.sidebar.set_collapsed(self._menu_contraido_preferido(), remember=False)

    def set_status(self, msg: str, timeout: int = 0):
        if msg:
            self.statusBar().show()
            self.statusBar().showMessage(msg, timeout)
        else:
            self.statusBar().clearMessage()
            self.statusBar().hide()

    def open_files(self, paths: list[str]):
        """Archivos abiertos desde la línea de comandos, el inicio o soltados en la ventana."""
        key = extensiones.destino(paths)
        if key:
            self.navigate(key)
            self.pages[key].accept_files(paths)
            return
        pdfs = [p for p in paths if os.path.splitext(p)[1].lower() in core.PDF_EXTS]
        images = [p for p in paths if core.is_image(p)]
        if images and not pdfs:
            key = "fromimage"
        elif len(paths) == 1:
            key = "viewer"
        else:
            key = "merge"
        self.navigate(key)
        self.pages[key].accept_files(paths)

    def closeEvent(self, e):
        if getattr(self, "_cerrando", False):          # ya se está preguntando o guardando para cerrar
            e.ignore()
            return
        # Desde el comienzo: esperar un guardado, preguntar o autoguardar procesan eventos, y por ahí podría
        # entrar un segundo cierre mientras el primero sigue a mitad.
        self._cerrando = True
        diagnostico.evento("cierre: inicia")
        try:
            self._cerrar(e)
        finally:
            self._cerrando = False
        diagnostico.evento("cierre: termina" if e.isAccepted() else "cierre: cancelado")

    def _cerrar(self, e):
        from . import guardado_ui
        if guardado_ui.guardando():                    # un «Guardar» a mitad: se espera a que termine
            guardado_ui.esperar_guardado()
        if ctx.busy:
            r, _ = guardado_ui._preguntar(
                self, "Proceso en curso", "Todavía se está procesando un archivo. ¿Salir de todas formas?",
                [("salir", "Salir de todas formas", QMessageBox.ButtonRole.DestructiveRole),
                 ("cancelar", "Seguir trabajando", QMessageBox.ButtonRole.RejectRole)],
                defecto="cancelar", icono="aviso")
            if r != "salir":
                e.ignore()
                return
        # Una por una, las pantallas con cambios sin guardar: «Guardar», «No guardar» o «Cancelar».
        pendientes, vistas = [], set()
        claves = ["viewer", "organize"] + [r["clave"] for r in extensiones.registros()]
        for clave in claves:
            page = self.pages.get(clave)
            if page is None or clave in vistas:
                continue
            vistas.add(clave)
            if callable(getattr(page, "has_unsaved", None)) and page.has_unsaved():
                pendientes.append((clave, page))
        for clave, page in pendientes:
            if not page.has_unsaved():
                continue
            self.navigate(clave)
            titulo = next((t for _s, _l, _t, items in SECTIONS for k, t, _a in items if k == clave), clave)
            ruta = getattr(getattr(page, "canvas", None), "path", None) or getattr(page, "path", None)
            que = f"«{Path(ruta).name}», en «{titulo}»" if ruta else f"«{titulo}»"
            r = guardado_ui.preguntar_cambios(self, "Cambios sin guardar",
                                              f"Tienes cambios sin guardar en {que}. ¿Quieres guardarlos antes "
                                              "de salir?")
            if r == "cancelar":
                e.ignore()
                return
            if r == "guardar" and not guardado_ui.guardar_y_esperar(page):
                e.ignore()                             # no se pudo (o no se quiso) guardar: no se cierra
                return
        extensiones.al_cerrar(self)       # cierre aceptado: las extensiones dejan todo en orden
        e.accept()


def _light_palette() -> QPalette:
    """Paleta de Qt con los tokens del tema activo (lo que la hoja QSS no pinta usa estos colores)."""
    t = tema.tokens()
    pal = QPalette()
    roles = {
        QPalette.ColorRole.Window: t["canvas"], QPalette.ColorRole.WindowText: t["ink"],
        QPalette.ColorRole.Base: t["surface"], QPalette.ColorRole.AlternateBase: t["surface-hover"],
        QPalette.ColorRole.Text: t["ink"], QPalette.ColorRole.Button: t["surface"],
        QPalette.ColorRole.ButtonText: t["ink"], QPalette.ColorRole.Highlight: t["focus"],
        QPalette.ColorRole.HighlightedText: "#FFFFFF", QPalette.ColorRole.ToolTipBase: t["ink"],
        QPalette.ColorRole.ToolTipText: t["surface"], QPalette.ColorRole.PlaceholderText: t["ink-3"],
        QPalette.ColorRole.Link: t["brand-ink"], QPalette.ColorRole.Mid: t["line-strong"],
        QPalette.ColorRole.Midlight: t["line"], QPalette.ColorRole.Dark: t["line-control"],
    }
    for role, color in roles.items():
        pal.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(tema.mezclar("ink", "surface", 0.55)))
    return pal


def _install_excepthook():
    def hook(exc_type, exc, tb):
        diagnostico.excepcion("interfaz", (exc_type, exc, tb))      # al registro, antes del aviso
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            sys.__stderr__ and sys.__stderr__.write(text)
        except Exception:  # noqa: BLE001
            pass
        if QApplication.instance():
            QMessageBox.critical(ctx.window, "Error inesperado",
                                 f"Ocurrió un error inesperado:\n\n{exc_type.__name__}: {exc}")
    sys.excepthook = hook


def comprobar_herramientas(informe: str | None = None) -> int:
    """Modo de comprobación de build.ps1: ¿el ejecutable trae todas las herramientas?

    Escribe en ``informe`` (o en la consola) las páginas registradas y las que faltan.  Devuelve 0 si están
    todas las de ``extensiones.ESENCIALES``, 1 si falta alguna y 2 si algo falló al cargarlas."""
    try:
        claves = [r["clave"] for r in extensiones.registros()]
        faltan = [c for c in extensiones.ESENCIALES if c not in claves]
        texto = f"registradas: {', '.join(claves) or 'ninguna'}\nfaltan: {', '.join(faltan) or 'ninguna'}\n"
        codigo = 1 if faltan else 0
    except Exception as exc:  # noqa: BLE001
        texto, codigo = f"error al cargar las herramientas: {exc!r}\n", 2
    if informe:
        Path(informe).write_text(texto, encoding="utf-8")
    elif sys.stdout is not None:
        sys.stdout.write(texto)
    return codigo


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    if len(argv) > 1 and argv[1] == "--comprobar-herramientas":
        return comprobar_herramientas(argv[2] if len(argv) > 2 else None)
    # En el ejecutable sin consola no hay stdout/stderr: los errores van al registro de diagnóstico.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = diagnostico.salida_errores()
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("RonyPDF.Portable")
        except Exception:  # noqa: BLE001
            pass
    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app._traductor = instalar_traductor(app)     # botones Sí / No / Cancelar de Qt en español
    app.setStyle("Fusion")
    tema.cargar_fuentes()
    try:
        app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    except AttributeError:
        pass
    app.setPalette(_light_palette())
    app.setStyleSheet(QSS)
    app.setWindowIcon(app_icon())
    _install_excepthook()
    diagnostico.entorno()
    win = MainWindow()
    win.show()
    diagnostico.ofrecer_informe(win)    # si la sesión anterior se cerró de golpe: aviso para guardar el informe
    files = [a for a in argv[1:] if os.path.isfile(a)]
    if files:
        win.open_files(files)
    return app.exec()
