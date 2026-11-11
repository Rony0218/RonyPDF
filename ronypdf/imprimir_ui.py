"""Pantalla «Imprimir» y botón de impresora del visor (Ctrl+P).

La lógica (distribución de hojas, pintado e impresión) está en ``imprimir.py``; aquí solo va la
interfaz, con la plantilla 2.0 (``ToolPage``): impresora, páginas, distribución y ajustes en pasos a la
izquierda, la hoja de papel tal como saldrá a la derecha y, abajo, el resumen con «Guardar como PDF…» e
«Imprimir».
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
import time
import traceback
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QRectF, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtPrintSupport import QPrinter, QPrinterInfo
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QSizePolicy, QSpinBox, QToolButton, QVBoxLayout,
                               QWidget)

from . import core, extensiones, tema
from . import imprimir as imp
from .icons import icon
from .widgets import (PDF_AND_IMAGES_FILTER, PDF_LOCK, GlyphTile, Note, OptionGroup, Segmented, SingleFilePicker,
                      SwitchRow, ToolPage, ask_open, ask_save, ctx, label, repolish, show_done, suggest_output)


# --------------------------------------------------------------------------
# Ícono
# --------------------------------------------------------------------------

def dibujo_impresora(p: QPainter, base: QColor, acento: QColor) -> None:
    """Impresora de línea con la hoja saliendo; la línea impresa, en rojo."""
    p.drawPolyline([QPointF(7, 8), QPointF(7, 3.5), QPointF(17, 3.5), QPointF(17, 8)])
    p.drawRoundedRect(QRectF(3, 8, 18, 8.5), 2, 2)
    p.save()
    p.setBrush(QColor(Qt.GlobalColor.white))
    p.drawRect(QRectF(7, 13, 10, 7.5))
    p.restore()
    p.drawPoint(QPointF(18, 10.8))
    extensiones.lapiz(p, acento)
    p.drawLine(QPointF(9.5, 15.8), QPointF(14.5, 15.8))
    extensiones.lapiz(p, base)
    p.drawLine(QPointF(9.5, 18.2), QPointF(12.8, 18.2))


# --------------------------------------------------------------------------
# Copia temporal con los cambios sin guardar del visor
# --------------------------------------------------------------------------

CARPETA_TEMPORAL = Path(tempfile.gettempdir()) / "RonyPDF-imprimir"


def _carpeta_proceso() -> Path:
    return CARPETA_TEMPORAL / str(os.getpid())


def _limpiar_viejas() -> None:
    """Restos de sesiones que se cerraron de golpe (más de 12 horas)."""
    try:
        for d in CARPETA_TEMPORAL.iterdir():
            if d.is_dir() and d.name != str(os.getpid()) and time.time() - d.stat().st_mtime > 12 * 3600:
                shutil.rmtree(d, ignore_errors=True)
    except OSError:
        pass


def nueva_temporal(origen: str) -> str:
    _limpiar_viejas()
    carpeta = _carpeta_proceso() / f"{time.time_ns()}"
    carpeta.mkdir(parents=True, exist_ok=True)
    return str(carpeta / f"{Path(origen).stem}.pdf")


def borrar_temporal(ruta: str | None) -> None:
    if not ruta:
        return
    p = Path(ruta)
    try:
        if p.exists():
            p.unlink()
        if p.parent.parent == _carpeta_proceso():
            p.parent.rmdir()
    except OSError:
        pass


def borrar_temporales() -> None:
    shutil.rmtree(_carpeta_proceso(), ignore_errors=True)
    try:
        CARPETA_TEMPORAL.rmdir()            # solo si quedó vacía
    except OSError:
        pass


atexit.register(borrar_temporales)


@contextmanager
def _sin_recientes():
    """La copia temporal no debe aparecer en «Archivos recientes»."""
    ctx.add_recent = lambda *a, **k: None
    try:
        yield
    finally:
        del ctx.add_recent


def imprimir_desde_visor(visor) -> None:
    """Botón de impresora / Ctrl+P en «Ver y editar»: abre «Imprimir» con el documento que se ve."""
    c = visor.canvas
    win = ctx.window
    if c.doc is None or not c.path or ctx.busy or win is None:
        return
    page = getattr(win, "pages", {}).get("print")
    if page is None:
        return
    pagina = c.current_page
    if visor.has_unsaved():
        focus = c.scene().focusItem()
        if focus:
            focus.clearFocus()
        edits, _ = c.collect_edits()
        if edits:
            page.soltar_temporal()
            destino = nueva_temporal(c.path)
            origen, pw = c.path, c.password

            def listo(_n):
                if pw:
                    ctx.passwords.set(destino, pw)
                page.preparar_desde_visor(origen, destino, pagina)
                extensiones.ir_a("print", [destino])

            def al_final():                    # (corre antes que «listo»)
                if not os.path.exists(destino):
                    borrar_temporal(destino)   # falló o se canceló: no dejar ni la carpeta

            ctx.run_task("Preparando el documento con tus cambios…", core.apply_edits, listo,
                         on_finally=al_final, path=origen, output=destino, edits=edits, password=pw)
            return
    page.preparar_desde_visor(c.path, None, pagina)
    extensiones.ir_a("print", [c.path])


# --------------------------------------------------------------------------
# Vista previa
# --------------------------------------------------------------------------

class HojaPreview(QWidget):
    """La hoja de papel con sombra, tal como saldrá de la impresora."""
    anterior = Signal()
    siguiente = Signal()
    redimensionada = Signal()

    def __init__(self):
        super().__init__()
        self.setMinimumSize(200, 260)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Vista previa de la hoja")
        self._img: QImage | None = None
        self._texto = "Elige un PDF o una imagen para ver cómo saldrá cada hoja"

    def set_imagen(self, img: QImage | None) -> None:
        self._img = img
        self.update()

    def set_texto(self, texto: str) -> None:
        self._img = None
        self._texto = texto
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(22, 16, -22, -22)
        if self._img is None or self._img.isNull():
            p.setPen(QColor(tema.color("ink-3")))
            p.drawText(r, int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap), self._texto)
            return
        iw, ih = self._img.width(), self._img.height()
        s = min(r.width() / iw, r.height() / ih)
        w, h = iw * s, ih * s
        target = QRectF(r.center().x() - w / 2, r.center().y() - h / 2, w, h)
        p.setPen(Qt.PenStyle.NoPen)
        for i, a in enumerate((30, 20, 12, 6)):        # sombra suave
            p.setBrush(QColor(0, 0, 0, a))
            p.drawRoundedRect(target.adjusted(-i - 0.5, -i + 1, i + 0.5, i + 3), 2 + i, 2 + i)
        p.drawImage(target, self._img)
        p.setPen(QPen(QColor(tema.color("line-strong")), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(target)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Left, Qt.Key.Key_PageUp, Qt.Key.Key_Up):
            self.anterior.emit()
        elif e.key() in (Qt.Key.Key_Right, Qt.Key.Key_PageDown, Qt.Key.Key_Down, Qt.Key.Key_Space):
            self.siguiente.emit()
        else:
            super().keyPressEvent(e)

    def wheelEvent(self, e):
        d = e.angleDelta().y()
        if d:
            (self.siguiente if d < 0 else self.anterior).emit()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.redimensionada.emit()


class _Senal(QObject):
    lista = Signal(int, object)      # generación, QImage o texto de error


class _Render(QRunnable):
    """Pinta una hoja en segundo plano (con el candado de PyMuPDF) para no congelar la ventana."""

    def __init__(self, page: "PrintPage", gen: int, path: str, password: str | None, hoja, op, papel, max_px):
        super().__init__()
        self.page, self.gen = page, gen
        self.args = (path, password, hoja, op, papel, max_px)

    def run(self):
        path, password, hoja, op, papel, max_px = self.args
        while not PDF_LOCK.acquire(timeout=0.2):
            if self.gen != self.page._gen:
                return                       # ya se pidió otra hoja
        try:
            if self.gen != self.page._gen:
                return
            with core.open_pdf(path, password) as doc:
                img = imp.imagen_hoja(doc, hoja, op, papel, max_px)
            self.page._senal.lista.emit(self.gen, img)
        except core.UserError as exc:
            self.page._senal.lista.emit(self.gen, str(exc))
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self.page._senal.lista.emit(self.gen, f"No se pudo dibujar la vista previa ({type(exc).__name__}).")
        finally:
            PDF_LOCK.release()


# --------------------------------------------------------------------------
# Pantalla
# --------------------------------------------------------------------------

DISTRIBUCIONES = [
    ("una", "Una página por hoja", "Ajustada al papel, a tamaño real o a tu escala."),
    ("varias", "Varias páginas por hoja", "2, 4, 6, 9 o 16 por hoja, para ahorrar papel."),
    ("folleto", "Folleto", "Para doblar y grapar por la mitad."),
    ("poster", "Póster", "Una página en varias hojas, para pegarlas."),
]
TAMANOS_UNA = [("Ajustar a la hoja", "ajustar"), ("Tamaño real (100 %)", "real"),
               ("Reducir solo si no cabe", "reducir"), ("Escala personalizada", "escala")]
AYUDA_MODOS = {
    "ajustar": "Agranda o reduce cada página para que ocupe todo el papel.",
    "real": "Imprime al 100 %, como mide de verdad el documento. Lo que no quepa en el papel se corta.",
    "reducir": "Las páginas más grandes que el papel se achican; las demás salen al 100 %.",
    "escala": "Imprime todas las páginas al porcentaje que elijas.",
    "varias": "Ahorra papel poniendo varias páginas, más pequeñas, en cada hoja.",
    "folleto": "Imprime dos páginas por hoja en el orden correcto para doblar y grapar por la mitad.",
    "poster": "Amplía cada página y la reparte en varias hojas para pegarlas.",
}
DOBLE_CARA = [("No", "no"), ("Sí, voltear por el lado largo", "largo"), ("Sí, voltear por el lado corto", "corto"),
              ("Manual: te guiamos paso a paso", "manual")]
TEXTO_DORSO = ("Ya salió la primera cara. Toma las hojas, voltéalas y vuelve a ponerlas en la bandeja en el "
               "mismo orden. Cuando estén listas, pulsa Continuar.")
SIN_ARCHIVO = "Elige un PDF o una imagen para ver cómo saldrá cada hoja"


def _combo(opciones, indice: int = 0) -> QComboBox:
    c = QComboBox()
    c.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    c.setMinimumContentsLength(8)
    for texto, dato in opciones:
        c.addItem(texto, dato)
    c.setCurrentIndex(indice)
    return c


def _spin(lo: int, hi: int, valor: int, sufijo: str = "") -> QSpinBox:
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setValue(valor)
    s.setSuffix(sufijo)
    s.setMinimumWidth(96)
    return s


def _campo(etiqueta: str, *widgets: QWidget) -> QWidget:
    """Etiqueta encima y, debajo, el control (o varios en fila)."""
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(6)
    v.addWidget(label(etiqueta, "Label"))
    if len(widgets) == 1:
        v.addWidget(widgets[0])
    else:
        fila = QHBoxLayout()
        fila.setSpacing(8)
        for x in widgets:
            fila.addWidget(x, 1 if isinstance(x, QComboBox) else 0)
        v.addLayout(fila)
    return w


def _columna(*widgets: QWidget, espacio: int = 12) -> QWidget:
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(espacio)
    for x in widgets:
        v.addWidget(x)
    return w


def _plural(n: int, uno: str, varios: str) -> str:
    return f"{n} {uno if n == 1 else varios}"


class _Selector(SingleFilePicker):
    """Selector de un PDF que también acepta imágenes (se imprimen como un PDF de una página)."""

    def __init__(self):
        super().__init__(title="Arrastra aquí el PDF o la imagen que quieres imprimir")
        self.drop.exts = core.PDF_EXTS | core.IMAGE_EXTS

    def drop_zone(self, title=None, hint=None, tile_key=None):
        zona = super().drop_zone(title, hint, tile_key)
        zona.exts = core.PDF_EXTS | core.IMAGE_EXTS
        return zona

    def browse(self):
        paths = ask_open(self, False, PDF_AND_IMAGES_FILTER, "Elige qué quieres imprimir")
        if paths:
            self.set_path(paths[0])


class PrintPage(ToolPage):
    """Plantilla 2.0: impresora, páginas, distribución y ajustes en pasos; la hoja tal como saldrá a la derecha;
    «Guardar como PDF…» (secundario) e «Imprimir» (principal) en la barra de acción."""

    key = "print"
    title = "Imprimir"
    subtitle = "Mira cada hoja antes de imprimir y ahorra papel."
    icon_name = "print"
    action_text = "Imprimir"

    # ------------------------------------------------------------------ construcción
    def build(self):
        self._impresoras: list[imp.InfoImpresora] | None = None   # se consultan al abrir la pantalla
        self._papeles: dict = {}
        self._tamanos: list[tuple[float, float]] = []
        self._plan: list[imp.Hoja] = []
        self._op = imp.OpcionesImpresion()
        self._hoja = 0
        self._gen = 0
        self._error = ""
        self._origen: str | None = None         # archivo original (para nombres)
        self._temporal: str | None = None       # copia con los cambios sin guardar del visor
        self._pendiente: dict | None = None     # lo que manda el visor antes de entregar el archivo
        self._quieto = True                     # True mientras se rellenan los controles
        self._pr = None
        self.ultima_imagen: QImage | None = None
        self._senal = _Senal()
        self._senal.lista.connect(self._imagen_lista)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)             # el mismo retardo de widgets.PreviewPane
        self._timer.timeout.connect(self._lanzar_render)

        self.picker = _Selector()
        self.picker.changed.connect(self._archivo_cambiado)
        self.use_picker(self.picker, "Arrastra aquí el PDF o la imagen que quieres imprimir",
                        "o elígelo desde tu equipo. Se imprime tal como lo ves en la vista previa.")
        self._paso_impresora()
        self._paso_paginas()
        self._paso_distribucion()
        self._paso_ajustes()
        self.set_main(self._columna_vista())

        self.b_imprimir = self.action
        self.b_pdf = self.add_secondary("Guardar como PDF…", self._guardar_como)
        self.b_pdf.setToolTip("Guarda un PDF con cada hoja tal como se ve en la vista previa, para imprimirlo "
                              "después o llevarlo a una papelería.")
        self.resumen = self.footer_info
        self.footer_info.setWordWrap(True)

        atajo = QShortcut(QKeySequence("Ctrl+P"), self)
        atajo.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        atajo.activated.connect(self._imprimir)
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._al_salir)
        self._quieto = False
        self._mostrar_sub()
        self._replanificar()

    def _paso_impresora(self):
        lay = self.step("¿En qué impresora?")
        self.c_impresora = QComboBox()
        self.c_impresora.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.c_impresora.setMinimumContentsLength(10)
        self.c_impresora.currentIndexChanged.connect(self._impresora_cambiada)
        b = QToolButton()
        b.setObjectName("IconButton")
        b.setIcon(icon("rotate_right", tema.color("ink-2"), size=16))
        b.setIconSize(QSize(16, 16))
        b.setToolTip("Volver a buscar impresoras")
        b.setAccessibleName("Volver a buscar impresoras")
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(lambda: self._cargar_impresoras(forzar=True))
        lay.addWidget(_campo("Impresora", self.c_impresora, b))
        self.aviso_sin_impresoras = Note("No se encontró ninguna impresora en este equipo. Puedes guardar un PDF "
                                         "listo para imprimir.", "warn")
        self.aviso_sin_impresoras.texto = self.aviso_sin_impresoras.text_lb
        self.aviso_sin_impresoras.hide()
        lay.addWidget(self.aviso_sin_impresoras)
        self.c_papel = _combo([])
        self.c_doble = _combo([])
        lay.addWidget(_campo("Papel", self.c_papel))
        lay.addWidget(_campo("Doble cara", self.c_doble))
        self.h_impresora = label("", "Help", wrap=True)
        self.h_impresora.hide()
        lay.addWidget(self.h_impresora)
        for c in (self.c_papel, self.c_doble):
            c.currentIndexChanged.connect(self._ajuste_cambiado)

    def _paso_paginas(self):
        lay = self.step("¿Qué páginas?")
        self.seg_paginas = Segmented([("Todas", "todas"), ("Solo estas", "rango")], full=True)
        self.r_todas, self.r_rango = self.seg_paginas.button(0), self.seg_paginas.button(1)
        lay.addWidget(self.seg_paginas)
        self.e_rango = QLineEdit()
        self.e_rango.setPlaceholderText("Por ejemplo: 1-3, 5, 8-")
        self.e_rango.setToolTip("Páginas o rangos separados por comas. «8-» significa de la 8 al final.")
        self.e_rango.hide()
        lay.addWidget(self.e_rango)
        self.h_rango = Note("", "danger")
        self.h_rango.hide()
        lay.addWidget(self.h_rango)
        self.c_sub = Segmented([("Todas", "todas"), ("Impares", "impares"), ("Pares", "pares")], full=True)
        self.c_sub.setToolTip("Todas, solo impares o solo pares (según el número de página).")
        lay.addWidget(_campo("De esas, imprimir", self.c_sub))
        self.s_copias = _spin(1, 999, 1)
        self.ch_intercalar = QCheckBox("Intercalar")
        self.ch_intercalar.setChecked(True)
        self.ch_intercalar.setToolTip("Intercalar: 1, 2, 3 · 1, 2, 3.\nSin intercalar: 1, 1 · 2, 2 · 3, 3.")
        caja = QWidget()
        fila = QHBoxLayout(caja)
        fila.setContentsMargins(0, 0, 0, 0)
        fila.setSpacing(14)
        fila.addWidget(self.s_copias)
        fila.addWidget(self.ch_intercalar)
        fila.addStretch(1)
        lay.addWidget(_campo("Copias", caja))
        self.seg_paginas.changed.connect(self._paginas_cambio)
        self.e_rango.textEdited.connect(lambda t: t.strip() and self.seg_paginas.set_value("rango"))
        self.e_rango.textChanged.connect(self._cambio)
        self.c_sub.changed.connect(self._cambio)
        self.s_copias.valueChanged.connect(self._cambio)
        self.ch_intercalar.toggled.connect(self._cambio)

    def _paso_distribucion(self):
        lay = self.step("¿Cómo van en la hoja?")
        self.distribucion = OptionGroup()
        self.r_distribucion = {k: self.distribucion.add(t, h, k) for k, t, h in DISTRIBUCIONES}
        lay.addWidget(self.distribucion)
        self.sub: dict[str, QWidget] = {}

        self.c_tamano = _combo(TAMANOS_UNA)
        self.s_escala = _spin(10, 400, 100, " %")
        self.fila_escala = _campo("Porcentaje", self.s_escala)
        self.ch_centrar = SwitchRow("Centrar la página en la hoja", checked=True)
        self.sub["una"] = _columna(_campo("Tamaño", self.c_tamano), self.fila_escala, self.ch_centrar)

        self.c_por_hoja = Segmented([(str(n), n) for n in imp.POR_HOJA], current=0, full=True)
        self.c_orden = _combo([("De izquierda a derecha", "horizontal"), ("De arriba abajo", "vertical")])
        self.ch_borde = SwitchRow("Un marco en cada página")
        self.sub["varias"] = _columna(_campo("Páginas por hoja", self.c_por_hoja), _campo("Orden", self.c_orden),
                                      self.ch_borde)

        self.c_encuad = Segmented([("A la izquierda", "izquierda"), ("A la derecha", "derecha")], full=True)
        self.c_encuad.setToolTip("A la derecha: para documentos que se leen de derecha a izquierda.")
        self.sub["folleto"] = _columna(_campo("Encuadernación", self.c_encuad))

        self.s_poster = _spin(100, 1000, 200, " %")
        self.s_solape = _spin(0, 30, 5, " mm")
        self.s_solape.setToolTip("Franja que se repite en las hojas vecinas para pegarlas sin que se note.")
        dos = QWidget()
        g = QHBoxLayout(dos)
        g.setContentsMargins(0, 0, 0, 0)
        g.setSpacing(12)
        g.addWidget(_campo("Tamaño", self.s_poster), 1)
        g.addWidget(_campo("Solape", self.s_solape), 1)
        self.ch_marcas = SwitchRow("Marcas de corte y letras", "Dicen por dónde cortar y en qué orden pegar.",
                                   checked=True)
        self.sub["poster"] = _columna(dos, self.ch_marcas)
        for w in self.sub.values():
            w.hide()
            lay.addWidget(w)
        self.h_modo = label("", "Help", wrap=True)
        lay.addWidget(self.h_modo)

        self.distribucion.changed.connect(self._modo_cambiado)
        self.c_tamano.currentIndexChanged.connect(self._modo_cambiado)
        for c in (self.s_escala, self.s_poster, self.s_solape):
            c.valueChanged.connect(self._cambio)
        self.c_orden.currentIndexChanged.connect(self._cambio)
        for c in (self.c_por_hoja, self.c_encuad):
            c.changed.connect(self._cambio)
        for c in (self.ch_borde, self.ch_marcas, self.ch_centrar):
            c.toggled.connect(self._cambio)

    def _paso_ajustes(self):
        lay = self.step("Ajustes de impresión")
        self.c_color = Segmented([("A color", False), ("Blanco y negro", True)], full=True)
        lay.addWidget(_campo("Color", self.c_color))
        # (tres segmentos no caben en el panel angosto: lista desplegable)
        self.orient = _combo([("Automática", "auto"), ("Vertical", "vertical"),
                              ("Horizontal", "horizontal")])
        self.orient.setToolTip("Automática gira las páginas apaisadas para aprovechar el papel.")
        lay.addWidget(_campo("Orientación", self.orient))

        self.b_avanzadas = QPushButton("Opciones avanzadas")
        self.b_avanzadas.setObjectName("Ghost")
        self.b_avanzadas.setCheckable(True)
        self.b_avanzadas.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_avanzadas.setIcon(icon("chevron_down", tema.color("ink-2"), size=14))
        lay.addWidget(self.b_avanzadas, 0, Qt.AlignmentFlag.AlignLeft)
        self.c_calidad = Segmented([("Borrador", 150), ("Normal", 300), ("Alta", 600)], current=1, full=True)
        self.c_calidad.setToolTip("Borrador es más rápido; Alta se nota en fotos y letra muy pequeña.")
        self.ch_anot = SwitchRow("Comentarios y campos de formulario", "Se imprimen como se ven en pantalla.",
                                 checked=True)
        self.ch_inverso = SwitchRow("Orden inverso", "La última página sale primero.")
        self.avanzadas = _columna(_campo("Calidad", self.c_calidad), self.ch_anot, self.ch_inverso)
        self.avanzadas.hide()
        lay.addWidget(self.avanzadas)
        self.b_avanzadas.toggled.connect(self._ver_avanzadas)

        self.c_color.changed.connect(self._ajuste_cambiado)
        self.c_calidad.changed.connect(self._ajuste_cambiado)
        self.orient.currentIndexChanged.connect(self._cambio)
        self.ch_anot.toggled.connect(self._cambio)
        self.ch_inverso.toggled.connect(self._cambio)

    def _ver_avanzadas(self, on: bool):
        self.avanzadas.setVisible(on)
        self.b_avanzadas.setIcon(icon("up_chevron" if on else "chevron_down", tema.color("ink-2"), size=14))

    def _columna_vista(self) -> QWidget:
        col = QWidget()
        v = QVBoxLayout(col)
        v.setContentsMargins(16, 14, 16, 12)
        v.setSpacing(8)
        cab = QHBoxLayout()
        cab.addWidget(label("Así saldrá cada hoja", "StepTitle"))
        cab.addStretch(1)
        self.lb_detalle = label("", "Caption")
        cab.addWidget(self.lb_detalle)
        v.addLayout(cab)
        self.aviso_cambios = Note("<b>Incluye tus cambios sin guardar.</b> Se imprime lo que ves en «Ver y "
                                  "editar», aunque todavía no lo hayas guardado.", "info")
        self.aviso_cambios.hide()
        v.addWidget(self.aviso_cambios)
        self.vista = HojaPreview()
        self.vista.anterior.connect(lambda: self.ir_a_hoja(self._hoja - 1))
        self.vista.siguiente.connect(lambda: self.ir_a_hoja(self._hoja + 1))
        self.vista.redimensionada.connect(self._programar)
        v.addWidget(self.vista, 1)
        nav = QHBoxLayout()
        nav.setSpacing(8)
        nav.addStretch(1)
        self.b_ant = self._boton_nav("prev", "Hoja anterior", lambda: self.ir_a_hoja(self._hoja - 1))
        self.lb_hoja = label("", "BodyStrong")
        self.lb_hoja.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lb_hoja.setMinimumWidth(110)
        self.b_sig = self._boton_nav("next", "Hoja siguiente", lambda: self.ir_a_hoja(self._hoja + 1))
        for w in (self.b_ant, self.lb_hoja, self.b_sig):
            nav.addWidget(w)
        nav.addStretch(1)
        v.addLayout(nav)
        return col

    def _boton_nav(self, nombre: str, ayuda: str, fn) -> QToolButton:
        b = QToolButton()
        b.setObjectName("IconButton")
        b.setIcon(icon(nombre, tema.color("ink-2"), size=16))
        b.setIconSize(QSize(16, 16))
        b.setToolTip(ayuda)
        b.setAccessibleName(ayuda)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(fn)
        return b

    # ------------------------------------------------------------------ contrato de la plantilla
    def ready(self) -> bool:
        return bool(getattr(self, "_plan", None)) and self._info() is not None and bool(self.picker.path)

    def run(self):
        self._imprimir()

    def refresh(self):
        super().refresh()
        if hasattr(self, "lb_hoja"):
            self._actualizar()

    # ------------------------------------------------------------------ impresoras y ajustes
    def showEvent(self, e):
        super().showEvent(e)
        self._cargar_impresoras()

    def _ajustes(self) -> dict:
        s = ctx.settings.get("imprimir", {}) if ctx.settings is not None else {}
        return s if isinstance(s, dict) else {}

    def _guardar_ajustes(self, **extra):
        if ctx.settings is None or self._quieto or self._impresoras is None:
            return
        datos = dict(self._ajustes())
        info = self._info()
        if info is not None:
            datos["impresora"] = info.nombre
        datos.update(papel=self.c_papel.currentData(), doble_cara=self.c_doble.currentData() or "no",
                     grises=bool(self.c_color.value()), ppp=int(self.c_calidad.value()), **extra)
        try:
            ctx.settings.set("imprimir", datos)
        except Exception:  # noqa: BLE001 — los ajustes nunca deben interrumpir el trabajo
            pass

    def _info(self) -> imp.InfoImpresora | None:
        if not self._impresoras:
            return None
        i = self.c_impresora.currentIndex()
        return self._impresoras[i] if 0 <= i < len(self._impresoras) else None

    def _cargar_impresoras(self, forzar: bool = False):
        if self._impresoras is not None and not forzar:
            return
        try:
            lista = imp.impresoras()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            lista = []
        anterior = self._info().nombre if self._info() else self._ajustes().get("impresora")
        self._impresoras = lista
        self._papeles.clear()
        self._quieto = True
        try:
            self.c_impresora.clear()
            for info in lista:
                self.c_impresora.addItem(info.nombre, info.nombre)
                if info.predeterminada:
                    self.c_impresora.setItemData(self.c_impresora.count() - 1, "La predeterminada de Windows",
                                                 Qt.ItemDataRole.ToolTipRole)
            if not lista:
                self.c_impresora.addItem("Ninguna impresora", None)
            nombres = [i.nombre for i in lista]
            self.c_impresora.setCurrentIndex(nombres.index(anterior) if anterior in nombres else 0)
            self.c_impresora.setEnabled(bool(lista))
            self.aviso_sin_impresoras.setVisible(not lista)
            self._llenar_segun_impresora(primera=True)
        finally:
            self._quieto = False
        self._replanificar()

    def _impresora_cambiada(self, *_):
        if self._quieto:
            return
        self._quieto = True
        try:
            self._llenar_segun_impresora()
        finally:
            self._quieto = False
        self._guardar_ajustes()
        self._replanificar()

    def _llenar_segun_impresora(self, primera: bool = False):
        """Papeles y doble cara que admite la impresora elegida (y lo que se recordaba)."""
        info = self._info()
        guardado = self._ajustes()
        papel_antes = self.c_papel.currentData() or (guardado.get("papel") if primera else None)
        doble_antes = self.c_doble.currentData() or (guardado.get("doble_cara") if primera else None)
        nombres = info.papeles if info else list(imp.PAPELES)
        self.c_papel.clear()
        for n in nombres:
            self.c_papel.addItem(n, n)
        if papel_antes in nombres:
            self.c_papel.setCurrentIndex(nombres.index(papel_antes))
        else:
            defecto = info.papel_defecto if info else "Carta"
            self.c_papel.setCurrentIndex(nombres.index(defecto) if defecto in nombres else 0)
        opciones = [o for o in DOBLE_CARA if info is None and o[1] != "manual"
                    or info is not None and (info.duplex or o[1] in ("no", "manual"))]
        self.c_doble.clear()
        for texto, dato in opciones:
            self.c_doble.addItem(texto, dato)
        datos = [d for _, d in opciones]
        self.c_doble.setCurrentIndex(datos.index(doble_antes) if doble_antes in datos else 0)
        if primera:
            if "grises" in guardado:
                self.c_color.set_value(bool(guardado["grises"]))
            elif info is not None and not info.color:
                self.c_color.set_value(True)
            if guardado.get("ppp") in (150, 300, 600):
                self.c_calidad.set_value(guardado["ppp"])

    def _ajuste_cambiado(self, *_):
        if self._quieto:
            return
        self._guardar_ajustes()
        self._cambio()

    def _papel_actual(self) -> imp.Papel:
        info = self._info()
        nombre = self.c_papel.currentData() or "Carta"
        clave = (info.nombre if info else None, nombre)
        if clave not in self._papeles:
            try:
                self._papeles[clave] = imp.papel(nombre, clave[0])
            except core.UserError:
                self._papeles[clave] = imp.papel("Carta")
            except Exception:  # noqa: BLE001 — un controlador raro: sin márgenes
                traceback.print_exc()
                self._papeles[clave] = imp.papel(nombre if nombre in imp.PAPELES else "Carta")
        return self._papeles[clave]

    # ------------------------------------------------------------------ opciones
    def _modo(self) -> str:
        d = self.distribucion.value()
        return (self.c_tamano.currentData() or "ajustar") if d == "una" else d

    def _poner_modo(self, modo: str) -> None:
        if modo in ("varias", "folleto", "poster"):
            self.distribucion.set_value(modo)
        else:
            self.distribucion.set_value("una")
            self._elegir(self.c_tamano, modo)

    def opciones(self) -> imp.OpcionesImpresion:
        modo = self._modo()
        return imp.OpcionesImpresion(
            paginas=self.e_rango.text().strip() if self.seg_paginas.value() == "rango" else "",
            subconjunto=self.c_sub.value(), invertir=self.ch_inverso.isChecked(),
            copias=self.s_copias.value(), intercalar=self.ch_intercalar.isChecked(), modo=modo,
            escala=float(self.s_poster.value() if modo == "poster" else self.s_escala.value()),
            por_hoja=self.c_por_hoja.value(), orden=self.c_orden.currentData(),
            borde=self.ch_borde.isChecked(), encuadernacion=self.c_encuad.value(),
            solape_mm=float(self.s_solape.value()), marcas_corte=self.ch_marcas.isChecked(),
            orientacion=self.orient.currentData(), centrar=self.ch_centrar.isChecked(),
            grises=bool(self.c_color.value()), anotaciones=self.ch_anot.isChecked(),
            ppp=int(self.c_calidad.value()), doble_cara=self.c_doble.currentData() or "no")

    def set_opciones(self, **cambios) -> None:
        """Cambia opciones como si se hubieran elegido en pantalla (útil para pruebas y atajos)."""
        op = replace(self.opciones(), **cambios)
        self._quieto = True
        try:
            if "paginas" in cambios:
                self.e_rango.setText(op.paginas)
                self.seg_paginas.set_value("rango" if op.paginas.strip() else "todas")
            self.c_sub.set_value(op.subconjunto)
            self.ch_inverso.setChecked(op.invertir)
            self.s_copias.setValue(int(op.copias))
            self.ch_intercalar.setChecked(op.intercalar)
            if "modo" in cambios:
                self._poner_modo(op.modo)
            if "escala" in cambios:
                (self.s_poster if op.modo == "poster" else self.s_escala).setValue(round(op.escala))
            self.c_por_hoja.set_value(op.por_hoja)
            self._elegir(self.c_orden, op.orden)
            self.ch_borde.setChecked(op.borde)
            self.c_encuad.set_value(op.encuadernacion)
            self.s_solape.setValue(round(op.solape_mm))
            self.ch_marcas.setChecked(op.marcas_corte)
            self._elegir(self.orient, op.orientacion)
            self.ch_centrar.setChecked(op.centrar)
            self.c_color.set_value(bool(op.grises))
            self.ch_anot.setChecked(op.anotaciones)
            self.c_calidad.set_value(int(op.ppp))
            self._elegir(self.c_doble, op.doble_cara)
        finally:
            self._quieto = False
        self._mostrar_sub()
        self._guardar_ajustes()
        self._replanificar()

    @staticmethod
    def _elegir(combo: QComboBox, dato) -> None:
        i = combo.findData(dato)
        if i >= 0:
            combo.setCurrentIndex(i)

    def _paginas_cambio(self, _i: int, valor):
        rango = valor == "rango"
        self.e_rango.setVisible(rango)
        if rango and not self._quieto and self.isVisible():
            self.e_rango.setFocus()
        self._cambio()

    def _modo_cambiado(self, *_):
        op = self.opciones()
        if op.modo == "folleto" and op.doble_cara == "no" and not self._quieto:
            # un folleto necesita las dos caras: se elige la que haya
            info = self._info()
            preferida = "corto" if info is None or info.duplex else "manual"
            if self.c_doble.findData(preferida) >= 0:
                self._quieto = True
                self._elegir(self.c_doble, preferida)
                self._quieto = False
                self._guardar_ajustes()
        self._mostrar_sub()
        self._cambio()

    def _mostrar_sub(self):
        d, modo = self.distribucion.value(), self._modo()
        for clave, w in self.sub.items():
            w.setVisible(clave == d)
        self.fila_escala.setVisible(modo == "escala")
        self.ch_centrar.setVisible(modo in ("real", "reducir", "escala"))
        self.e_rango.setVisible(self.seg_paginas.value() == "rango")

    def _cambio(self, *_):
        if self._quieto:
            return
        self._replanificar()

    # ------------------------------------------------------------------ archivo
    def preparar_desde_visor(self, origen: str, temporal: str | None, pagina: int) -> None:
        """El visor avisa qué va a entregar: el original o una copia con los cambios sin guardar."""
        self._pendiente = {"ruta": temporal or origen, "origen": origen, "temporal": temporal, "pagina": pagina}

    def soltar_temporal(self) -> None:
        """Deja de usar (y borra) la copia temporal, p. ej. antes de crear otra."""
        if self._temporal:
            vieja = self._temporal
            if self.picker.path and os.path.normcase(self.picker.path) == os.path.normcase(vieja):
                self.picker.clear()
            borrar_temporal(vieja)
            self._temporal = None

    def accept_files(self, paths: list[str]) -> None:
        if paths:
            self.load(paths[0])

    def load(self, path: str) -> None:
        self._cargar_impresoras()
        pend = self._pendiente
        if pend and pend["temporal"] and os.path.normcase(pend["ruta"]) == os.path.normcase(path):
            with _sin_recientes():
                self.picker.set_path(path)
        else:
            self.picker.set_path(path)
        self._pendiente = None

    def _archivo_cambiado(self, path):
        pend = self._pendiente
        self._pendiente = None
        if path and pend and os.path.normcase(pend["ruta"]) == os.path.normcase(path):
            origen, temporal, inicio = pend["origen"], pend["temporal"], pend["pagina"]
        else:
            origen, temporal, inicio = path, None, None
        if self._temporal and self._temporal != temporal:
            borrar_temporal(self._temporal)
        self._temporal, self._origen = temporal, origen
        self.aviso_cambios.setVisible(bool(path and temporal))
        if path and temporal:              # la ficha del encabezado lleva el nombre del original
            self.picker.file_chip().set_file(Path(origen).name, self.picker.detail + " · con tus cambios", origen)
        self._tamanos = []
        if path:
            try:
                with PDF_LOCK:
                    self._tamanos = core.page_sizes(path, self.picker.password)
            except core.UserError as exc:
                QMessageBox.warning(self, "Imprimir", str(exc))
        self._hoja = 0
        self._replanificar(pagina=inicio)

    # ------------------------------------------------------------------ plan, resumen y vista previa
    @property
    def hojas(self) -> int:
        """Caras que se imprimirán (= páginas del PDF listo para imprimir), con copias."""
        return len(self._plan)

    def _replanificar(self, pagina: int | None = None):
        anterior = self._plan[self._hoja].paginas[:1] if 0 <= self._hoja < len(self._plan) else []
        op = self.opciones()
        self._op = op
        self._plan, self._error = [], ""
        if self._tamanos:
            try:
                self._plan = imp.planificar(self._tamanos, self._papel_actual().area, op)
            except core.UserError as exc:
                self._error = str(exc)
        buscada = pagina if pagina is not None else (anterior[0] if anterior else None)
        if buscada is not None:
            self._hoja = next((k for k, h in enumerate(self._plan) if buscada in h.paginas), self._hoja)
        self._hoja = max(0, min(self._hoja, len(self._plan) - 1))
        self.refresh()
        self._programar()

    def _actualizar(self):
        op = self._op
        hay = bool(self._plan)
        info = self._info()
        self.h_rango.setText(self._error)
        self.h_rango.setVisible(bool(self._error))
        self.e_rango.setProperty("error", "true" if self._error and op.paginas else "false")
        repolish(self.e_rango)
        self.ch_intercalar.setVisible(op.copias > 1)
        dos = op.doble_cara in ("largo", "corto", "manual")
        if hay:
            caras, hojas = imp.contar(self._plan, op)
            paginas = len(imp.paginas_elegidas(len(self._tamanos), op))
            chips = [f"{_plural(hojas, 'hoja', 'hojas')} por las dos caras ({caras} caras)" if dos
                     else _plural(hojas, "hoja de papel", "hojas de papel"),
                     _plural(paginas, "página", "páginas")]
            if op.copias > 1:
                chips.append(f"{op.copias} copias")
            if info is not None:
                texto = f"Se envía a «{info.nombre}»."
            else:
                texto = "Sin impresora: guarda un PDF listo para imprimir."
            self.set_summary(chips, texto)
        elif self._error:
            self.set_summary([("Revisa las páginas", "danger")], self._error)
        else:
            self.set_summary([], SIN_ARCHIVO + ".")
        self.b_pdf.setEnabled(hay)
        self.b_imprimir.setEnabled(hay and info is not None)
        self.b_imprimir.setToolTip("" if info is not None else "No hay ninguna impresora instalada en este equipo.")
        # navegación
        n = len(self._plan)
        if hay:
            self.lb_hoja.setText(f"{'Cara' if dos else 'Hoja'} {self._hoja + 1} de {n}")
            h = self._plan[self._hoja]
            partes = []
            if dos:
                partes.append(f"Hoja de papel {self._hoja // 2 + 1}, "
                              f"{'por delante' if h.cara == 'frente' else 'por detrás'}")
            elif op.modo == "folleto":
                partes.append("Frente de la hoja" if h.cara == "frente" else "Dorso de la hoja")
            if h.etiqueta:
                partes.append(f"Pieza {h.etiqueta}")
            if op.copias > 1:
                partes.append(f"Copia {h.copia} de {op.copias}")
            self.lb_detalle.setText(" · ".join(partes))
        else:
            self.lb_hoja.setText("")
            self.lb_detalle.setText("")
        self.b_ant.setVisible(hay)
        self.b_sig.setVisible(hay)
        self.b_ant.setEnabled(hay and self._hoja > 0)
        self.b_sig.setEnabled(hay and self._hoja < n - 1)
        self._pistas()

    def _pistas(self):
        """Avisos cortos debajo de las opciones de la impresora y del modo."""
        op, info = self._op, self._info()
        pistas = []
        sin_duplex = info is not None and not info.duplex
        if op.doble_cara == "manual":
            pistas.append("Primero saldrán las caras de adelante; luego te diremos cómo volver a poner las hojas.")
        elif op.modo == "folleto" and op.doble_cara == "no":
            pistas.append("Un folleto se imprime por las dos caras: elige "
                          + ("«Manual» y te guiamos." if sin_duplex else "«Sí, voltear por el lado corto»."))
        elif op.modo == "folleto" and op.doble_cara == "largo":
            pistas.append("Para un folleto conviene voltear por el lado corto; si no, el dorso sale de cabeza.")
        elif sin_duplex:
            pistas.append("Esta impresora no imprime sola por las dos caras; si las necesitas, elige «Manual» y "
                          "te guiamos.")
        self.h_impresora.setText("\n".join(pistas))
        self.h_impresora.setVisible(bool(pistas))
        # en «Una página por hoja» la tarjeta no dice qué hace cada tamaño: lo dice esta línea
        texto = AYUDA_MODOS.get(op.modo, "") if op.modo in ("ajustar", "real", "reducir", "escala") else ""
        if op.modo == "poster" and self._tamanos and self._plan:
            primera = self._plan[0].paginas[:1]
            if primera:
                cols, filas, esc, _ = imp.cuadricula_poster(self._tamanos[primera[0]], self._papel_actual().area, op)
                texto = f"Cada página sale en {cols} × {filas} hojas ({cols * filas})."
                if esc < op.escala - 0.5:
                    texto += f" Se ajustó al {esc:.0f} % para no gastar otra fila de hojas."
        elif op.modo == "folleto" and self._plan:
            texto = "Dobla las hojas juntas por la mitad, con la primera hoja por fuera."
        self.h_modo.setText(texto)
        self.h_modo.setVisible(bool(texto))

    def ir_a_hoja(self, i: int) -> None:
        if not self._plan:
            return
        i = max(0, min(i, len(self._plan) - 1))
        if i != self._hoja:
            self._hoja = i
            self._actualizar()
            self._lanzar_render()

    def _programar(self, *_):
        self._timer.start()

    def _lanzar_render(self):
        self._gen += 1
        if not self._plan or not self.picker.path:
            self.ultima_imagen = None
            self.vista.set_texto(self._error or SIN_ARCHIVO)
            return
        dpr = self.vista.devicePixelRatioF() or 1.0
        lado = max(self.vista.width(), self.vista.height(), 300)
        max_px = int(min(1800, max(400, lado * dpr)))
        self._pool.start(_Render(self, self._gen, self.picker.path, self.picker.password, self._plan[self._hoja],
                                 self._op, self._papel_actual(), max_px))

    def _imagen_lista(self, gen: int, resultado):
        if gen != self._gen:
            return
        if isinstance(resultado, QImage):
            self.ultima_imagen = resultado
            self.vista.set_imagen(resultado)
        else:
            self.ultima_imagen = None
            self.vista.set_texto(str(resultado))

    # ------------------------------------------------------------------ guardar e imprimir
    def _estado(self, msg: str) -> None:
        win = ctx.window
        if win is not None and hasattr(win, "set_status"):
            win.set_status(msg, 10000)

    def _comprobar(self) -> bool:
        if not self.picker.path or ctx.busy:
            return False
        if self._error or not self._plan:
            if self._error:
                QMessageBox.warning(self, "Imprimir", self._error)
            return False
        return True

    def _guardar_como(self):
        if not self._comprobar():
            return
        base = self._origen or self.picker.path
        out = ask_save(self, suggest_output(base, "_para_imprimir"), title="Guardar PDF listo para imprimir")
        if out:
            self.guardar_pdf(out)

    def guardar_pdf(self, ruta: str) -> None:
        """Guarda el PDF listo para imprimir (una página por hoja) en segundo plano."""
        if not self._comprobar():
            return
        origenes = {os.path.normcase(os.path.abspath(p)) for p in (self.picker.path, self._origen) if p}
        if os.path.normcase(os.path.abspath(ruta)) in origenes:
            QMessageBox.warning(self, "Guardar como PDF", "Elige otro nombre: así reemplazarías el documento que "
                                "estás imprimiendo.")
            return
        op, p = self.opciones(), self._papel_actual()

        clave = bool(self.picker.password)

        def listo(n):
            show_done(self, "PDF listo para imprimir",
                      f"{_plural(n, 'página', 'páginas')} en «{Path(ruta).name}», una por cada hoja, tal como se "
                      f"ven en la vista previa. Es una copia en imagen: el texto no se puede seleccionar ni buscar."
                      + (" Pide la misma contraseña que el original." if clave else ""), file=ruta)

        ctx.run_task("Creando el PDF listo para imprimir…", imp.imprimir_a_pdf, listo, path=self.picker.path,
                     output=ruta, op=op, papel_=p, password=self.picker.password)

    def _crear_impresora(self, nombre: str, op: imp.OpcionesImpresion, p: imp.Papel, titulo: str):
        """QPrinter listo para la impresora elegida (no imprime nada todavía)."""
        pi = QPrinterInfo.printerInfo(nombre)
        if pi.isNull():
            QMessageBox.warning(self, "Imprimir", imp._error_impresora(nombre))
            self._cargar_impresoras(forzar=True)
            return None
        pr = QPrinter(pi, QPrinter.PrinterMode.HighResolution)
        imp.preparar_impresora(pr, op, p, titulo)
        return pr

    def _imprimir(self):
        if not self._comprobar():
            return
        info = self._info()
        if info is None:
            return
        op = self.opciones()
        _, hojas = imp.contar(self._plan, op)
        trabajo = dict(path=self.picker.path, password=self.picker.password, op=op)
        titulo = Path(self._origen or self.picker.path).name
        if op.doble_cara == "manual":
            self._imprimir_caras(info.nombre, op, titulo, trabajo, hojas, "frente")
            return
        pr = self._crear_impresora(info.nombre, op, self._papel_actual(), titulo)
        if pr is None:
            return
        self._pr = pr
        ctx.run_task(f"Imprimiendo en «{info.nombre}»…", imp.imprimir,
                     lambda _n: self._estado(f"Se enviaron {_plural(hojas, 'hoja', 'hojas')} a {info.nombre}"),
                     on_finally=self._soltar_impresora, printer=pr, **trabajo)

    def _soltar_impresora(self):
        self._pr = None

    def _imprimir_caras(self, nombre: str, op, titulo: str, trabajo: dict, hojas: int, caras: str,
                        invertir: bool = False):
        pr = self._crear_impresora(nombre, op, self._papel_actual(), titulo)
        if pr is None:
            return
        self._pr = pr

        def listo(_n):
            if caras == "frente":
                seguir, inv = self._preguntar_dorso()
                if seguir:
                    self._imprimir_caras(nombre, op, titulo, trabajo, hojas, "dorso", inv)
                else:
                    self._estado("Se imprimieron solo las caras de adelante")
            else:
                self._estado(f"Se enviaron {_plural(hojas, 'hoja', 'hojas')} a {nombre} por las dos caras")

        texto = "Imprimiendo las caras de adelante…" if caras == "frente" else "Imprimiendo las caras de atrás…"
        ctx.run_task(texto, imp.imprimir, listo, on_finally=self._soltar_impresora, printer=pr, caras=caras,
                     invertir_dorso=invertir, **trabajo)

    def _preguntar_dorso(self) -> tuple[bool, bool]:
        """Instrucciones entre las dos caras de la doble cara manual: (continuar, invertir el orden)."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Doble cara manual")
        v = QVBoxLayout(dlg)
        v.setContentsMargins(22, 20, 22, 16)
        v.setSpacing(12)
        fila = QHBoxLayout()
        fila.setSpacing(14)
        fila.addWidget(GlyphTile("print", tema.TILE_MD, "ver"), 0, Qt.AlignmentFlag.AlignTop)
        texto = QLabel(TEXTO_DORSO)
        texto.setWordWrap(True)
        texto.setMinimumWidth(380)
        fila.addWidget(texto, 1)
        v.addLayout(fila)
        ch = QCheckBox("Invertir el orden de la segunda cara (si tu impresora deja las hojas boca arriba)")
        ch.setChecked(bool(self._ajustes().get("invertir_dorso", False)))
        v.addWidget(ch)
        v.addWidget(label("Si es la primera vez con esta impresora, prueba antes con dos hojas.", "Help", wrap=True))
        botones = QDialogButtonBox()
        b_ok = botones.addButton("Continuar", QDialogButtonBox.ButtonRole.AcceptRole)
        b_ok.setObjectName("Primary")
        botones.addButton("Cancelar", QDialogButtonBox.ButtonRole.RejectRole)
        botones.accepted.connect(dlg.accept)
        botones.rejected.connect(dlg.reject)
        v.addWidget(botones)
        ok = dlg.exec() == QDialog.DialogCode.Accepted
        if ok:
            self._guardar_ajustes(invertir_dorso=ch.isChecked())
        return ok, ch.isChecked()

    def _al_salir(self):
        borrar_temporal(self._temporal)
        borrar_temporales()


REGISTRO = dict(clave="print", titulo="Imprimir", seccion="ver", orden=15, pagina=PrintPage, dibujo=dibujo_impresora)
ACCIONES_VISOR = [dict(icono="print", ayuda="Imprimir (Ctrl+P)", atajo="Ctrl+P", accion=imprimir_desde_visor)]
