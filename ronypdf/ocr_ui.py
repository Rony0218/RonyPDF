"""Pantalla «Revisar texto reconocido» (sección Convertir).

Muestra las palabras dudosas del OCR (ámbar: dudosa; rojo: muy dudosa) sobre la página y en una lista con el
recorte de la imagen, para corregirlas con el teclado antes de guardar el PDF buscable.  La lógica vive en
``ocr.py``; aquí solo hay interfaz.  Se registra con ``REGISTRO`` (ver ``extensiones.py``).

Con documentos largos no se crean miles de widgets: la lista es un modelo con delegado y la página se dibuja
cuando se muestra.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
from PySide6.QtCore import QAbstractListModel, QEvent, QModelIndex, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QImage, QKeySequence, QPainter, QPainterPath, QPen, QPixmap,
                           QPolygonF)
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QGraphicsPixmapItem, QGraphicsPolygonItem, QGraphicsScene,
                               QGraphicsView, QHBoxLayout, QLabel, QLineEdit, QListView, QMessageBox, QPushButton,
                               QSizePolicy, QStackedWidget, QStyle, QStyledItemDelegate, QToolButton, QVBoxLayout,
                               QWidget)

from . import core, extensiones, guardado_ui, memoria, ocr, tema
from .icons import icon
from .widgets import (PDF_LOCK, Chip, DropArea, FileChip, FlowLayout, Note, Segmented, ToolPage, ask_open, ask_save,
                      cat_de, ctx, label, show_done, suggest_output)

# Colores de las marcas (los mismos tonos de estado del tema que usan los chips de la leyenda).
AMBAR = QColor(tema.color("warn"))
ROJO = QColor(tema.color("danger"))
VERDE = QColor(tema.color("ok"))
GRIS = QColor(tema.color("line-control"))
FILTROS = (("Muy dudosas", "muy"), ("Dudosas", "dudosas"), ("Todas", "todas"))
SIN_NADA = "Este PDF ya tiene texto digital: no hay nada que revisar"


def _color(w: ocr.PalabraOcr) -> QColor:
    if w.revisada:
        return VERDE
    if w.confianza < ocr.UMBRAL_MUY_DUDOSA:
        return ROJO
    if w.confianza < ocr.UMBRAL_SEGURA:
        return AMBAR
    return GRIS


def _s(n: int, uno: str, varios: str) -> str:
    return f"{n} {uno if n == 1 else varios}"


def _motivo(w: ocr.PalabraOcr) -> str:
    if w.corregida is not None and w.corregida != w.texto:
        return f"Corregida (se leyó «{w.texto}»)"
    if w.revisada:
        return "Revisada"
    if not w.motivos:
        return "Lectura segura"
    texto = " · ".join(w.motivos)
    if w.otra and ocr.M_DIFIEREN in w.motivos:
        texto = texto.replace(ocr.M_DIFIEREN, f"{ocr.M_DIFIEREN} (la otra dice «{w.otra}»)")
    return texto[:1].upper() + texto[1:]


# --------------------------------------------------------------------------
# Ícono (cuadrícula de 24 × 24, de línea, con un detalle rojo)
# --------------------------------------------------------------------------

def dibujo_revisar(p: QPainter, base: QColor, acento: QColor) -> None:
    """Hoja con renglones y una palabra enmarcada en rojo."""
    path = QPainterPath()
    path.moveTo(4, 2)
    path.lineTo(15.5, 2)
    path.lineTo(20, 6.5)
    path.lineTo(20, 22)
    path.lineTo(4, 22)
    path.closeSubpath()
    p.drawPath(path)
    p.drawPolyline([QPointF(15.5, 2), QPointF(15.5, 6.5), QPointF(20, 6.5)])
    for y, x1 in ((7.5, 12.5), (11.0, 16.5), (18.8, 13.0)):
        p.drawLine(QPointF(7.2, y), QPointF(x1, y))
    p.save()
    extensiones.lapiz(p, acento)
    p.drawRoundedRect(QRectF(6.0, 12.9, 11.6, 3.9), 0.9, 0.9)
    p.restore()


# --------------------------------------------------------------------------
# Lista de palabras (modelo + delegado: sirve igual con 30 o con 30 000 palabras)
# --------------------------------------------------------------------------

class _Modelo(QAbstractListModel):
    def __init__(self):
        super().__init__()
        self.filas: list[tuple[ocr.PaginaOcr, ocr.PalabraOcr]] = []
        self.fila_de: dict[int, int] = {}

    def rowCount(self, parent=QModelIndex()):  # noqa: N802 — método de Qt
        return 0 if parent.isValid() else len(self.filas)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self.filas):
            return None
        p, w = self.filas[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return w.final
        if role == Qt.ItemDataRole.ToolTipRole:
            return f"Página {p.indice + 1}: {_motivo(w)}"
        if role == Qt.ItemDataRole.UserRole:
            return p, w
        return None

    def poner(self, filas):
        self.beginResetModel()
        self.filas = filas
        self.fila_de = {id(w): k for k, (_p, w) in enumerate(filas)}
        self.endResetModel()

    def cambio(self, w: ocr.PalabraOcr):
        k = self.fila_de.get(id(w))
        if k is not None:
            i = self.index(k)
            self.dataChanged.emit(i, i)


class _Delegado(QStyledItemDelegate):
    ALTO = 48

    def __init__(self, pagina: "RevisarOcrPage"):
        super().__init__(pagina)
        self.pagina = pagina

    def sizeHint(self, option, index):  # noqa: N802
        return QSize(option.rect.width(), self.ALTO)

    def paint(self, p: QPainter, option, index):
        p.save()
        r = option.rect
        t = tema.tokens()
        sel = bool(option.state & QStyle.StateFlag.State_Selected)
        p.fillRect(r, QColor(tema.tonos(cat_de(self.pagina))["band"] if sel else t["surface"]))
        pag, w = index.data(Qt.ItemDataRole.UserRole)
        p.fillRect(QRectF(r.x() + 2, r.y() + 6, 4, r.height() - 12), _color(w))
        caja = QRectF(r.x() + 12, r.y() + 6, 104, r.height() - 12)
        img = self.pagina.recorte(pag, w, int(caja.height()))
        if img is not None and not img.isNull():
            esc = min(caja.width() / max(img.width(), 1), caja.height() / max(img.height(), 1))
            dw, dh = img.width() * esc, img.height() * esc
            p.drawImage(QRectF(caja.x(), caja.y() + (caja.height() - dh) / 2, dw, dh), img)
        else:
            p.fillRect(caja, QColor(t["surface-sunken"]))
        x = caja.right() + 10
        p.setFont(tema.fuente("body-strong", option.font))
        p.setPen(QColor(t["ink"]))
        texto = w.final if w.final.strip() else "(se quita)"
        p.drawText(QRectF(x, r.y() + 5, r.right() - x - 6, 20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   p.fontMetrics().elidedText(texto, Qt.TextElideMode.ElideRight, int(r.right() - x - 6)))
        p.setFont(tema.fuente("caption", option.font))
        p.setPen(QColor(t["ink-3"]))
        sub = f"Pág. {pag.indice + 1} · {_motivo(w)}"
        p.drawText(QRectF(x, r.y() + 25, r.right() - x - 6, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   p.fontMetrics().elidedText(sub, Qt.TextElideMode.ElideRight, int(r.right() - x - 6)))
        p.setPen(QColor(t["line"]))
        p.drawLine(r.bottomLeft(), r.bottomRight())
        p.restore()


class _Lista(QListView):
    def __init__(self):
        super().__init__()
        self.vacio = ""

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.model() is not None and self.model().rowCount() == 0 and self.vacio:
            p = QPainter(self.viewport())
            p.setPen(QColor(tema.color("ink-3")))
            p.setFont(tema.fuente("body-sm", self.font()))
            p.drawText(self.viewport().rect().adjusted(16, 16, -16, -16),
                       Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.vacio)
            p.end()


# --------------------------------------------------------------------------
# Página con las palabras marcadas
# --------------------------------------------------------------------------

class _Vista(QGraphicsView):
    palabra_clic = Signal(object)
    zoom_rueda = Signal(int)

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(QColor(tema.color("surface-sunken")))      # el pozo de la vista previa
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            for it in self.items(e.position().toPoint()):
                if isinstance(it, QGraphicsPolygonItem) and it.data(0) is not None:
                    self.palabra_clic.emit(it.data(0))
                    return
        super().mousePressEvent(e)

    def wheelEvent(self, e):
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom_rueda.emit(1 if e.angleDelta().y() > 0 else -1)
            return
        super().wheelEvent(e)


# --------------------------------------------------------------------------
# Pantalla
# --------------------------------------------------------------------------

class _Filtro(Segmented):
    """Segmentos «Muy dudosas · Dudosas · Todas» con la interfaz de la lista desplegable que tenía antes
    (``findData``, ``currentData``, ``setCurrentIndex`` y la señal ``currentIndexChanged``)."""

    currentIndexChanged = Signal(int)

    def __init__(self):
        super().__init__(FILTROS, current=1, full=True)
        self.changed.connect(lambda k, _v: self.currentIndexChanged.emit(k))

    def findData(self, valor) -> int:  # noqa: N802 — misma API que QComboBox
        return self._values.index(valor) if valor in self._values else -1

    def currentData(self):  # noqa: N802
        return self.value()

    def currentIndex(self) -> int:  # noqa: N802
        return self.index()

    def setCurrentIndex(self, k: int) -> None:  # noqa: N802
        self.set_index(k)


def _herramienta(nombre: str, ayuda: str, cb, texto: str = "") -> QToolButton:
    b = QToolButton()
    b.setObjectName("Tool" if texto else "Flat")
    b.setIcon(icon(nombre, tema.color("ink-2"), size=18))
    b.setIconSize(QSize(18, 18))
    if texto:
        b.setText(texto)
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    b.setToolTip(ayuda)
    b.setAccessibleName(ayuda)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.clicked.connect(cb)
    return b


def _ghost(texto: str, ayuda: str, cb) -> QPushButton:
    b = QPushButton(texto)
    b.setObjectName("Ghost")
    b.setToolTip(ayuda)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.clicked.connect(cb)
    return b


class RevisarOcrPage(ToolPage):
    """Revisar texto reconocido, con la forma de las demás herramientas: el documento en la ficha del
    encabezado, la palabra actual y la lista de dudosas en el panel, la página en el área principal y una sola
    acción principal, «Guardar PDF buscable…»."""

    key = "ocrcheck"
    title = "Revisar texto reconocido"
    subtitle = "Corrige las palabras dudosas del reconocimiento antes de guardar el PDF buscable."
    icon_name = "ocrcheck"
    action_text = "Guardar PDF buscable…"
    MARGEN = 8          # puntos de aire alrededor de la página en la vista

    def __init__(self):
        self.path: str | None = None
        self.password: str | None = None
        self.resultado: ocr.ResultadoOcr | None = None
        self.opciones: ocr.OpcionesOcr | None = None
        self.salida: str | None = None
        self.doc: pymupdf.Document | None = None
        self._tamanos: list[tuple[float, float]] = []      # (ancho, alto) de cada página, leídos al cargar
        self._sucio = False
        self._pendiente: str | None = None          # PDF escaneado esperando «Reconocer ahora»
        self.pagina_actual = -1
        self.actual: tuple[ocr.PaginaOcr, ocr.PalabraOcr] | None = None
        self.deshacer_pila: list[list[tuple[ocr.PalabraOcr, str | None, bool]]] = []
        self.zoom = 1.0
        self._items: dict[int, QGraphicsPolygonItem] = {}
        # recortes de palabras y páginas enteras (giradas o enderezadas) con presupuesto en bytes
        self._recortes = memoria.CacheImagenes(24 * memoria.MB, maximo=600)
        self._paginas_img = memoria.CacheImagenes(32 * memoria.MB, maximo=3)
        self._render_hecho = None
        self._pendientes_pagina: dict[int, int] = {}
        super().__init__()
        # El panel de la palabra ocupa el alto que quede: la lista de dudosas crece hasta abajo.
        if self._steps_panel is not None:
            self.body.setStretchFactor(self._steps_panel, 1)
            self.body.setStretch(self.body.count() - 1, 0)
        self._pila_cambio(self.pilas.currentIndex())
        self._actualizar()
        self._vp.installEventFilter(self)      # al final: el filtro usa widgets que se crean arriba

    # ------------------------------------------------------------------ construcción
    def build(self):
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._render)
        self._timer_sug = QTimer(self)
        self._timer_sug.setSingleShot(True)
        self._timer_sug.setInterval(150)
        self._timer_sug.timeout.connect(self._mostrar_sugerencias)
        self.info = self.footer_info
        self.footer_info.setWordWrap(True)
        self.b_guardar = self.action
        self.b_guardar.setToolTip("Guarda una copia buscable del PDF con tus correcciones (el original no cambia)")
        self.b_descartar = self.add_secondary("Descartar", self.descartar)
        self.b_descartar.setToolTip("Cierra esta revisión sin guardar")

        # Encabezado: el documento como ficha; «Cambiar» abre otro
        self.ficha = FileChip()
        self.ficha.b_change.setToolTip("Revisar otro PDF")
        self.ficha.change_clicked.connect(self._elegir)
        self.ficha.hide()
        self.set_header_widget(self.ficha)

        self._construir_panel()
        self.pilas = QStackedWidget()
        self._construir_vacio()
        self._construir_revision()
        self.pilas.currentChanged.connect(self._pila_cambio)
        self.set_main(self.pilas)

    def _construir_panel(self):
        # Bloque 1: la palabra actual
        g = self.step(None)
        cab = QHBoxLayout()
        cab.setContentsMargins(0, 0, 0, 0)
        cab.addWidget(label("Palabra actual", "StepTitle"), 1)
        self.chip_palabra = Chip("", "neutral", dot=True)
        cab.addWidget(self.chip_palabra, 0, Qt.AlignmentFlag.AlignVCenter)
        g.addLayout(cab)
        t = tema.tokens()
        self.recorte_lb = QLabel()
        self.recorte_lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.recorte_lb.setFixedHeight(60)
        self.recorte_lb.setStyleSheet(f"background: {t['surface']}; border: 1px solid {t['line']}; "
                                      "border-radius: 12px;")
        self.recorte_lb.setToolTip("Lo que se ve en la imagen: compáralo con lo que se leyó")
        g.addWidget(self.recorte_lb)
        self.editor = QLineEdit()
        self.editor.setStyleSheet("QLineEdit { font-size: 16px; }")
        self.editor.setPlaceholderText("Texto leído")
        self.editor.setToolTip("Corrige la palabra y pulsa Enter (F2 para editar)")
        self.editor.installEventFilter(self)
        g.addWidget(self.editor)
        self.caja_sug = QWidget()
        fila = FlowLayout(self.caja_sug, spacing=6)
        self.b_sug: list[QPushButton] = []
        for _ in range(3):
            b = QPushButton()
            b.setObjectName("Secondary")
            b.setStyleSheet("QPushButton { padding: 5px 10px; font-size: 13px; border-radius: 8px; }")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, b=b: self.usar_sugerencia(b.text()))
            b.hide()
            fila.addWidget(b)
        self.fila_sug = fila
        self.caja_sug.hide()
        g.addWidget(self.caja_sug)
        self.motivo = label("", "Help", wrap=True)
        g.addWidget(self.motivo)
        acciones = QHBoxLayout()
        acciones.setContentsMargins(0, 0, 0, 0)
        acciones.setSpacing(6)
        self.b_aceptar = QPushButton("Aceptar")
        self.b_aceptar.setObjectName("Secondary")
        self.b_aceptar.setIcon(icon("check", tema.color("ink-2"), size=18))
        self.b_aceptar.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_aceptar.setToolTip("Acepta la palabra (con tu corrección) y pasa a la siguiente (Enter)")
        self.b_aceptar.clicked.connect(self.aceptar)
        self.b_diccionario = _ghost("Agregar a mi diccionario", "La palabra deja de marcarse como dudosa, también "
                                    "en otros documentos. Tu diccionario se guarda solo en este equipo.",
                                    self.agregar_diccionario)
        acciones.addWidget(self.b_aceptar)
        acciones.addWidget(self.b_diccionario)
        acciones.addStretch(1)
        g.addLayout(acciones)

        # Bloque 2: las palabras para revisar (crece hasta abajo)
        g = self.step(None)
        g.parentWidget().setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        cab = QVBoxLayout()
        cab.setContentsMargins(0, 0, 0, 0)
        cab.setSpacing(2)
        cab.addWidget(label("Palabras para revisar", "StepTitle"))
        self.resumen = label("", "Help", wrap=True)
        cab.addWidget(self.resumen)
        g.addLayout(cab)
        self.filtro = _Filtro()
        self.filtro.setToolTip("Qué palabras se muestran en la lista y en la página")
        self.filtro.currentIndexChanged.connect(lambda _i: self._poblar())
        g.addWidget(self.filtro)
        self.modelo = _Modelo()
        self.lista = _Lista()
        self.lista.setModel(self.modelo)
        self.lista.setItemDelegate(_Delegado(self))
        self.lista.setUniformItemSizes(True)
        self.lista.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.lista.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.lista.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.lista.setMinimumHeight(150)
        self.lista.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self.lista.selectionModel().currentChanged.connect(self._fila_actual)
        self.lista.installEventFilter(self)
        g.addWidget(self.lista, 1)

    def _construir_vacio(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 12)
        v.setSpacing(0)
        self.drop = DropArea("Suelta aquí un PDF escaneado o uno al que ya le reconociste el texto",
                             "o elígelo desde tu equipo. Las palabras dudosas se marcan en ámbar y las muy dudosas en "
                             "rojo.", core.PDF_EXTS, tile_key="ocrcheck", zone=True, button_text="Elegir PDF…")
        self.drop.clicked.connect(self._elegir)
        self.drop.files_dropped.connect(self.accept_files)
        v.addWidget(self.drop, 1)
        self.franja = Note("", "info")
        self.estado = self.franja.text_lb
        self.b_reconocer = QPushButton("Reconocer ahora")
        self.b_reconocer.setObjectName("Secondary")
        self.b_reconocer.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_reconocer.clicked.connect(self.reconocer_ahora)
        self.franja.layout().addWidget(self.b_reconocer, 0, Qt.AlignmentFlag.AlignVCenter)
        self.franja.hide()
        caja = QHBoxLayout()
        caja.setContentsMargins(12, 0, 12, 0)
        caja.addWidget(self.franja)
        v.addLayout(caja)
        ayuda = label("Enter acepta y pasa a la siguiente; Tab y Mayús+Tab recorren la lista; F2 edita; Ctrl+Z "
                      "deshace la última corrección.", "Help", wrap=True)
        ayuda.setAlignment(Qt.AlignmentFlag.AlignCenter)
        caja = QHBoxLayout()
        caja.setContentsMargins(24, 10, 24, 0)
        caja.addWidget(ayuda)
        v.addLayout(caja)
        self.pilas.addWidget(w)

    def _construir_revision(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(8)
        # arriba: qué significa cada color y el zoom
        barra = QHBoxLayout()
        barra.setContentsMargins(0, 0, 0, 0)
        barra.setSpacing(6)
        for tono, texto, ayuda in (
                ("warn", "Dudosa", "Confianza estimada entre 50 % y 79 %: conviene mirarla"),
                ("danger", "Muy dudosa", "Confianza estimada menor de 50 %: casi seguro hay un error"),
                ("ok", "Revisada", "Ya la aceptaste o la corregiste")):
            chip = Chip(texto, tono, dot=True)
            chip.setToolTip(ayuda)
            barra.addWidget(chip)
        barra.addStretch(1)
        barra.addWidget(_herramienta("minus", "Alejar", lambda: self._cambiar_zoom(-1)))
        self.lb_zoom = label("100 %", "Caption")
        barra.addWidget(self.lb_zoom)
        barra.addWidget(_herramienta("add", "Acercar", lambda: self._cambiar_zoom(1)))
        barra.addWidget(_ghost("Ajustar", "Ajustar la página al ancho", lambda: self._cambiar_zoom(0)))
        v.addLayout(barra)
        self.vista = _Vista()
        self.vista.palabra_clic.connect(self._clic_en_pagina)
        self.vista.zoom_rueda.connect(self._cambiar_zoom)
        self._vp = self.vista.viewport()
        v.addWidget(self.vista, 1)
        # abajo: las páginas y lo que se hace con la que se ve
        barra = QHBoxLayout()
        barra.setContentsMargins(0, 0, 0, 0)
        barra.setSpacing(2)
        self.b_ant = _herramienta("prev", "Página anterior", lambda: self._ir_pagina(-1))
        self.lb_pagina = label("", "Caption")
        self.b_sig = _herramienta("next", "Página siguiente", lambda: self._ir_pagina(1))
        barra.addWidget(self.b_ant)
        barra.addWidget(self.lb_pagina)
        barra.addWidget(self.b_sig)
        barra.addStretch(1)
        self.b_todas_pagina = _ghost("Aceptar toda la página", "Marca como revisadas todas las palabras dudosas "
                                     "de la página que se ve", self.aceptar_pagina)
        self.b_releer = _ghost("Releer a 400 ppp", "Vuelve a reconocer la página que se ve a 400 ppp (tarda un poco "
                               "más y lee mejor la letra pequeña). Se pierden las correcciones de esa página.",
                               self.releer)
        barra.addWidget(self.b_todas_pagina)
        barra.addWidget(self.b_releer)
        v.addLayout(barra)
        self.pilas.addWidget(w)

    def _pila_cambio(self, k: int):
        """Sin documento, la zona para soltar ocupa todo el ancho; con la revisión, vuelve el panel."""
        if self.panel is not None:
            self.panel.setVisible(k == 1)

    # ------------------------------------------------------------------ contrato de la plantilla
    def ready(self) -> bool:
        return getattr(self, "resultado", None) is not None

    def run(self):
        self.guardar()

    # ------------------------------------------------------------------ archivo
    def _elegir(self):
        rutas = ask_open(self, False)
        if rutas:
            self.accept_files(rutas)

    def showEvent(self, e):  # noqa: N802
        super().showEvent(e)
        ocr.precargar()                 # para las sugerencias; en segundo plano, no al abrir la aplicación

    def accept_files(self, paths):
        """Carga un PDF y dice qué se puede revisar (sin diálogos si no hace falta una contraseña)."""
        pdfs = [p for p in paths if str(p).lower().endswith(".pdf")]
        if not pdfs:
            return
        path = str(pdfs[0])
        if not self._puede_descartar():
            return
        if not ctx.passwords.ensure(path, self):
            return
        pw = ctx.passwords.get(path)
        try:
            with PDF_LOCK:
                estado, faltan, _capa = ocr.estado_documento(path, pw)
        except core.UserError as exc:
            self._limpiar()
            self._mostrar_estado(str(exc))
            return
        self._limpiar()
        ctx.remember_dir(path)
        nombre = Path(path).name
        if estado == "digital":
            self._mostrar_estado(SIN_NADA)
        elif estado == "escaneo":
            self._pendiente = path
            self.password = pw
            self._mostrar_estado(f"«{nombre}» es escaneado: {_s(faltan, 'página no tiene', 'páginas no tienen')} "
                                 "texto. Reconócelo para revisar las palabras dudosas.", boton=True)
            self._ficha(path, f"Escaneado · {_s(faltan, 'página sin texto', 'páginas sin texto')}")
        else:
            ctx.run_task("Leyendo el texto reconocido…", ocr.leer_capa,
                         lambda r: self.cargar(path, pw, r, None),
                         path=path, password=pw)

    def _mostrar_estado(self, texto: str, boton: bool = False):
        self.pilas.setCurrentIndex(0)
        self.estado.setText(texto)
        self.franja.set_tone("info" if boton or texto == SIN_NADA else "warn")
        self.b_reconocer.setVisible(boton)
        self.franja.setVisible(bool(texto))
        self._actualizar()

    def _ficha(self, path: str | None, meta: str = "") -> None:
        """El documento en el encabezado (oculto si no hay ninguno)."""
        if path:
            self.ficha.set_file(Path(path).name, meta, path)
        self.ficha.setVisible(bool(path))

    def reconocer_ahora(self):
        path = self._pendiente
        if not path or ctx.busy:
            return
        op = ocr.OpcionesOcr()
        pw = self.password
        ctx.run_task("Reconociendo texto…", ocr.analizar, lambda r: self.cargar(path, pw, r, op),
                     path=path, opciones=op, password=pw)

    def cargar(self, path: str, password: str | None, resultado: ocr.ResultadoOcr,
               opciones: ocr.OpcionesOcr | None, salida: str | None = None) -> None:
        """Muestra un resultado de ``ocr.analizar`` (o de ``ocr.leer_capa``) para revisarlo."""
        if resultado is not self.resultado and not self._puede_descartar():
            return                              # hay otra revisión sin guardar y la persona la conserva
        self._limpiar()
        self.path, self.password, self.resultado = path, password, resultado
        self.opciones, self.salida = opciones, salida
        if not resultado.reconocidas():
            self.resultado = None
            self._mostrar_estado(SIN_NADA)
            return
        try:
            with PDF_LOCK:
                self.doc = core.open_pdf(path, password)
                self._tamanos = [(p.rect.width, p.rect.height) for p in self.doc]
        except core.UserError as exc:
            self.resultado = None
            self._mostrar_estado(str(exc))
            return
        self._sucio = resultado.origen == "ocr"
        self.pilas.setCurrentIndex(1)
        self._ficha(path, _s(len(resultado.reconocidas()), "página reconocida", "páginas reconocidas"))
        self.filtro.blockSignals(True)
        hay_dudosas = bool(resultado.dudosas())
        self.filtro.setCurrentIndex(1 if hay_dudosas else 2)
        self.filtro.blockSignals(False)
        self.zoom = 1.0
        self._poblar()
        if self.modelo.rowCount():
            self.lista.setCurrentIndex(self.modelo.index(0))
        else:
            self._mostrar_pagina(resultado.reconocidas()[0].indice)
        self.lista.setFocus()

    def _limpiar(self):
        if self.doc is not None:
            with PDF_LOCK:
                self.doc.close()
        self.doc = None
        self._tamanos = []
        self.path = self.resultado = self.opciones = self.salida = None
        self._pendiente = None
        self._sucio = False
        self.actual = None
        self.pagina_actual = -1
        self.deshacer_pila.clear()
        self._recortes.clear()
        self._paginas_img.clear()
        self._items.clear()
        self.vista.scene().clear()
        self.modelo.poner([])
        self.franja.hide()
        self._ficha(None)
        self._actualizar()

    # ------------------------------------------------------------------ estado
    def has_unsaved(self) -> bool:
        r = self.resultado
        if r is None or not self._sucio:
            return False
        return r.origen != "capa" or bool(r.correcciones())     # sin correcciones no hay nada que guardar

    def _puede_descartar(self) -> bool:
        if not self.has_unsaved():
            return True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Texto sin guardar")
        box.setText("El texto reconocido todavía no se ha guardado. ¿Lo descartas?")
        b_desc = box.addButton("Descartar", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return box.clickedButton() is b_desc

    def descartar(self):
        if not self._puede_descartar():
            return
        self._limpiar()
        self.pilas.setCurrentIndex(0)
        self._actualizar()

    def _actualizar(self):
        """Resumen, pie y botones (recorre todas las palabras: se llama al cargar y cuando algo cambia)."""
        r = self.resultado
        hay = r is not None
        self.b_guardar.setEnabled(hay and not ctx.busy)
        self.b_descartar.setEnabled(hay)
        self.b_descartar.setVisible(hay)
        self._pendientes_pagina = {}
        if not hay:
            self.set_summary([], "Abre un PDF escaneado, o uno con texto reconocido, para revisar sus palabras.")
            self._botones()
            return
        pendientes = r.dudosas()
        for p, _w in pendientes:
            self._pendientes_pagina[p.indice] = self._pendientes_pagina.get(p.indice, 0) + 1
        self.resumen.setText(f"Confianza promedio {r.promedio() * 100:.0f} % · "
                             f"{_s(len(pendientes), 'palabra dudosa', 'palabras dudosas')} en "
                             f"{_s(len(self._pendientes_pagina), 'página', 'páginas')}")
        cambios = r.correcciones()
        chips = [(_s(len(pendientes), "por revisar", "por revisar") if pendientes else "Todo revisado", "cat")]
        if cambios:
            chips.append((_s(cambios, "corrección", "correcciones"), "cat"))
        if self.salida and not self._sucio:
            texto = f"Guardado en «{Path(self.salida).name}»."
        elif self._sucio:
            texto = "Sin guardar. El original no cambia: se guarda una copia buscable."
        else:
            texto = "Sin correcciones todavía."
        self.set_summary(chips, texto)
        self._botones()

    def _botones(self):
        """Botones que dependen de la página que se ve (barato: se llama al moverse por la lista)."""
        r = self.resultado
        pend = getattr(self, "_pendientes_pagina", {})
        releer = r is not None and r.origen == "ocr" and self.pagina_actual >= 0
        todas = r is not None and pend.get(self.pagina_actual, 0) > 0
        self.b_releer.setEnabled(releer)
        self.b_releer.setVisible(releer)            # un PDF con su capa de texto no se vuelve a leer aquí
        self.b_todas_pagina.setEnabled(todas)
        self.b_todas_pagina.setVisible(todas)

    # ------------------------------------------------------------------ lista
    def _poblar(self):
        r = self.resultado
        if r is None:
            self.modelo.poner([])
            return
        clave = self.filtro.currentData()
        if clave == "muy":
            filas = [(p, w) for p, w in r.todas() if w.confianza < ocr.UMBRAL_MUY_DUDOSA]
            self.lista.vacio = "No hay palabras muy dudosas."
        elif clave == "dudosas":
            filas = [(p, w) for p, w in r.todas() if w.confianza < ocr.UMBRAL_SEGURA]
            self.lista.vacio = "No hay palabras dudosas: puedes guardar el PDF buscable."
        else:
            filas = r.todas()
            self.lista.vacio = "No se reconoció ninguna palabra."
        actual = self.actual
        self.modelo.poner(filas)
        if actual is not None and id(actual[1]) in self.modelo.fila_de:
            self.lista.setCurrentIndex(self.modelo.index(self.modelo.fila_de[id(actual[1])]))
        elif self.pagina_actual >= 0:
            self._dibujar_palabras()
        self._actualizar()

    def _fila_actual(self, actual: QModelIndex, _anterior=None):
        if not actual.isValid():
            return
        p, w = actual.data(Qt.ItemDataRole.UserRole)
        self.mostrar_palabra(p, w)

    def mostrar_palabra(self, pag: ocr.PaginaOcr, w: ocr.PalabraOcr):
        anterior = self.actual
        self.actual = (pag, w)
        if pag.indice != self.pagina_actual:
            self._mostrar_pagina(pag.indice)
        else:
            if anterior is not None:
                self._estilo(anterior[1])
            self._estilo(w)
        it = self._items.get(id(w))
        if it is not None:
            self.vista.ensureVisible(it, 60, 60)
        self.editor.setText(w.final)
        self.editor.selectAll()
        img = self.recorte(pag, w, 60, contexto=True)
        if img is not None:
            pm = QPixmap.fromImage(img)
            pm.setDevicePixelRatio(self.devicePixelRatioF())
            ancho = (self.recorte_lb.width() - 8) * self.devicePixelRatioF()
            if pm.width() > ancho:
                pm = pm.scaledToWidth(int(ancho), Qt.TransformationMode.SmoothTransformation)
                pm.setDevicePixelRatio(self.devicePixelRatioF())
            self.recorte_lb.setPixmap(pm)
        else:
            self.recorte_lb.clear()
        for b in self.b_sug:
            b.hide()
        self.caja_sug.hide()
        self._timer_sug.start()        # las sugerencias, cuando la persona se detiene en una palabra
        self.motivo.setText(_motivo(w))
        self._chip_palabra(w)
        self._botones()

    def _chip_palabra(self, w: ocr.PalabraOcr) -> None:
        """Estado de la palabra actual, con el mismo color que su marca en la página."""
        if w.corregida is not None and w.corregida != w.texto:
            texto, tono = "Corregida", "ok"
        elif w.revisada:
            texto, tono = "Revisada", "ok"
        elif w.confianza < ocr.UMBRAL_MUY_DUDOSA:
            texto, tono = "Muy dudosa", "danger"
        elif w.confianza < ocr.UMBRAL_SEGURA:
            texto, tono = "Dudosa", "warn"
        else:
            texto, tono = "Segura", "neutral"
        self.chip_palabra.setText(texto)
        self.chip_palabra.set_tone(tono)

    def _mostrar_sugerencias(self):
        if self.actual is None or self.resultado is None:
            return
        _p, w = self.actual
        sugs = []
        if w.otra and w.otra.strip() and w.otra != w.texto and " " not in w.otra.strip():
            sugs.append(w.otra.strip())
        if w.confianza < ocr.UMBRAL_SEGURA:
            for s in ocr.sugerencias(w.texto, self.resultado.idioma):
                if s not in sugs and s != w.texto:
                    sugs.append(s)
        for b, s in zip(self.b_sug, sugs[:3] + [None] * 3):
            b.setVisible(bool(s))
            b.setText(s or "")
            b.setToolTip(f"Cambiar por «{s}»" if s else "")
        self.caja_sug.setVisible(bool(sugs))

    def _seleccionar(self, w: ocr.PalabraOcr) -> bool:
        k = self.modelo.fila_de.get(id(w))
        if k is None:
            return False
        self.lista.setCurrentIndex(self.modelo.index(k))
        self.lista.scrollTo(self.modelo.index(k))
        return True

    def _mover(self, paso: int, solo_pendientes: bool = False):
        n = self.modelo.rowCount()
        if not n:
            return
        k = self.lista.currentIndex().row()
        for _ in range(n):
            k = (k + paso) % n if k >= 0 else 0
            _p, w = self.modelo.filas[k]
            if not solo_pendientes or not w.revisada:
                break
        else:
            k = min(max(self.lista.currentIndex().row() + paso, 0), n - 1)
        self.lista.setCurrentIndex(self.modelo.index(k))
        self.lista.scrollTo(self.modelo.index(k))

    def siguiente(self):
        self._mover(1)

    def anterior(self):
        self._mover(-1)

    def editar(self):
        self.editor.setFocus()
        self.editor.selectAll()

    # ------------------------------------------------------------------ correcciones
    def _registrar(self, ws: list[ocr.PalabraOcr]):
        self.deshacer_pila.append([(w, w.corregida, w.revisada) for w in ws])
        del self.deshacer_pila[:-500]

    def _cambiada(self, ws: list[ocr.PalabraOcr]):
        self._sucio = True
        ids = {id(w) for w in ws}
        for k in [k for k in self._recortes if k[0] in ids]:     # el marco del recorte cambia de color
            del self._recortes[k]
        for w in ws:
            self.modelo.cambio(w)
            self._estilo(w)
        self._actualizar()

    def aceptar(self):
        """Acepta la palabra actual con lo que diga el campo y pasa a la siguiente pendiente."""
        if self.actual is None:
            return
        _p, w = self.actual
        texto = self.editor.text().strip()
        self._registrar([w])
        w.corregida = None if texto == w.texto else texto
        w.revisada = True
        self._cambiada([w])
        self.motivo.setText(_motivo(w))
        self._mover(1, solo_pendientes=True)

    def usar_sugerencia(self, texto: str):
        if texto:
            self.editor.setText(texto)
            self.aceptar()

    def deshacer(self):
        if not self.deshacer_pila:
            return
        grupo = self.deshacer_pila.pop()
        for w, corregida, revisada in grupo:
            w.corregida, w.revisada = corregida, revisada
        self._cambiada([w for w, _c, _r in grupo])
        w = grupo[0][0]
        if not self._seleccionar(w) and self.actual is not None:
            self.mostrar_palabra(*self.actual)
        elif self.actual is not None and self.actual[1] is w:
            self.mostrar_palabra(*self.actual)

    def agregar_diccionario(self):
        if self.actual is None or self.resultado is None:
            return
        _p, w = self.actual
        palabra = self.editor.text().strip() or w.final
        try:
            ocr.agregar_al_diccionario(palabra)
        except core.UserError as exc:
            QMessageBox.warning(self, "Mi diccionario", str(exc))
            return
        clave = ocr._normal(palabra)
        iguales = [x for _q, x in self.resultado.todas() if ocr._normal(x.final) == clave or x is w]
        self._registrar(iguales)
        if palabra != w.final:
            w.corregida = None if palabra == w.texto else palabra
        for x in iguales:
            x.confianza, x.motivos, x.revisada = 1.0, [], True
        self._cambiada(iguales)
        self.info.setText(f"«{palabra}» quedó en tu diccionario: ya no se marcará como dudosa.")
        self._mover(1, solo_pendientes=True)

    def aceptar_pagina(self):
        if self.resultado is None:
            return
        ws = [w for p, w in self.resultado.dudosas() if p.indice == self.pagina_actual]
        if not ws:
            return
        self._registrar(ws)
        for w in ws:
            w.revisada = True
        self._cambiada(ws)
        if self.actual is not None:
            self.mostrar_palabra(*self.actual)

    def releer(self):
        if self.resultado is None or self.pagina_actual < 0 or ctx.busy:
            return
        indice = self.pagina_actual

        def listo(pag: ocr.PaginaOcr):
            if self.resultado is None:
                return
            self.resultado.reemplazar(pag)
            self._sucio = True
            self._recortes.clear()
            self._paginas_img.clear()
            self.actual = None
            self.pagina_actual = -1
            self._poblar()
            for k, (p, _w) in enumerate(self.modelo.filas):
                if p.indice == indice:
                    self.lista.setCurrentIndex(self.modelo.index(k))
                    break
            else:
                self._mostrar_pagina(indice)
            self.info.setText(f"La página {indice + 1} se volvió a leer a 400 ppp.")

        ctx.run_task("Volviendo a leer la página…", ocr.releer_pagina, listo, path=self.path, indice=indice,
                     opciones=self.opciones or ocr.OpcionesOcr(idioma=self.resultado.idioma), ppp=400,
                     password=self.password)

    # ------------------------------------------------------------------ guardar
    def guardar(self, al_terminar=None):
        """Guarda el PDF buscable (siempre una copia con otro nombre).  ``al_terminar(ok)``, si se da, avisa
        cuando termina: así lo usa la pregunta de «Cambios sin guardar» al cerrar la aplicación."""
        fin = guardado_ui.callback(al_terminar)
        r = self.resultado
        if r is None or ctx.busy:
            return fin(False)
        if r.origen == "capa" and not r.correcciones():
            QMessageBox.information(self, "Guardar", "No hay correcciones para guardar.")
            self._sucio = False
            self._actualizar()
            return fin(True)                  # no queda nada pendiente
        sugerida = self.salida or suggest_output(self.path, "_ocr")
        out = ask_save(self, sugerida, title="Guardar PDF buscable")
        if not out:
            return fin(False)
        hecho: dict = {}

        def listo(res):
            hecho["ok"] = True
            paginas, palabras = res
            self._sucio = False
            self.salida = out
            self._actualizar()
            show_done(self, "Texto reconocido",
                      f"Se guardaron {palabras} palabras en {_s(paginas, 'página', 'páginas')}, con "
                      f"{_s(r.correcciones(), 'corrección', 'correcciones')}. Ahora puedes buscar y copiar el "
                      "texto.", file=out)
            fin(True)

        ctx.run_task("Guardando el PDF buscable…", ocr.escribir, listo,
                     on_finally=lambda: guardado_ui.revisar_fin(hecho, fin), path=self.path, output=out,
                     resultado=r, opciones=self.opciones, password=self.password)

    # ------------------------------------------------------------------ página
    def _pag(self, indice: int) -> ocr.PaginaOcr | None:
        if self.resultado is None:
            return None
        return next((p for p in self.resultado.paginas if p.indice == indice), None)

    def _rect_visible(self, indice: int) -> pymupdf.Rect:
        """Tamaño de la página tal como se ve (después del giro).  Sale de la caché: sin tocar MuPDF."""
        r = pymupdf.Rect(0, 0, *self._tamanos[indice]) if indice < len(self._tamanos) else pymupdf.Rect(0, 0, 595, 842)
        pag = self._pag(indice)
        if pag is not None and pag.giro % 180:
            return pymupdf.Rect(0, 0, r.height, r.width)
        return pymupdf.Rect(r)

    def _ir_pagina(self, paso: int):
        if self.resultado is None:
            return
        paginas = [p.indice for p in self.resultado.reconocidas()]
        if not paginas:
            return
        if self.pagina_actual in paginas:
            k = paginas.index(self.pagina_actual) + paso
        else:
            k = 0
        k = max(0, min(k, len(paginas) - 1))
        destino = paginas[k]
        for fila, (p, _w) in enumerate(self.modelo.filas):
            if p.indice == destino:
                self.lista.setCurrentIndex(self.modelo.index(fila))
                return
        self._mostrar_pagina(destino)

    def _mostrar_pagina(self, indice: int):
        self.pagina_actual = indice
        paginas = [p.indice for p in self.resultado.reconocidas()] if self.resultado else []
        pos = paginas.index(indice) + 1 if indice in paginas else 0
        total = len(self._tamanos)
        self.lb_pagina.setText(f"Página {indice + 1} de {total}")
        self.lb_pagina.setToolTip(f"Reconocida {pos} de {len(paginas)}: las flechas pasan por las páginas "
                                  "reconocidas")
        self.b_ant.setEnabled(pos > 1)
        self.b_sig.setEnabled(0 < pos < len(paginas))
        self._render_hecho = None
        self._render()
        self._botones()

    def _escala(self) -> float:
        if self.doc is None or self.pagina_actual < 0:
            return 1.0
        r = self._rect_visible(self.pagina_actual)
        ancho = self.vista.width() - 2 * self.vista.frameWidth() - self.vista.verticalScrollBar().sizeHint().width()
        return max(200, ancho - 6) / max(r.width + 2 * self.MARGEN, 1) * self.zoom

    def _render(self):
        if self.doc is None or self.pagina_actual < 0:
            return
        escala = self._escala()
        clave = (self.pagina_actual, round(escala, 3))
        if clave == self._render_hecho:
            return
        r = self._rect_visible(self.pagina_actual)          # de la caché, sin tocar MuPDF
        if ctx.busy or not PDF_LOCK.acquire(blocking=False):
            self._timer.start()
            return
        try:
            page = self.doc[self.pagina_actual]
            # zoom hasta 6, pero sin pasar de MAX_PIXELES: una página grande con zoom alto pediría cientos de MB
            zoom = memoria.zoom_tope(r.width, r.height, min(6.0, escala * self.devicePixelRatioF()))
            pix = ocr.renderizar(page, self._pag(self.pagina_actual), zoom)
            img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
            pix = None                              # sin esperar: así no conviven tres copias de la página
        except Exception:  # noqa: BLE001 — una página que no se puede dibujar no debe tumbar la pantalla
            img = None
        finally:
            PDF_LOCK.release()
        sc = self.vista.scene()
        sc.clear()
        self._items.clear()
        if img is not None:
            ancho_img = img.width()
            pm = QPixmap.fromImage(img)
            img = None                              # el QPixmap ya tiene su copia
            it = QGraphicsPixmapItem(pm)
            it.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
            it.setScale(r.width / max(ancho_img, 1))
            sc.addItem(it)
        m = self.MARGEN
        sc.setSceneRect(QRectF(-m, -m, r.width + 2 * m, r.height + 2 * m))
        self.vista.resetTransform()
        self.vista.scale(escala, escala)
        self.lb_zoom.setText(f"{self.zoom * 100:.0f} %")
        self._render_hecho = clave
        self._dibujar_palabras()
        if self.actual is not None and self.actual[0].indice == self.pagina_actual:
            it = self._items.get(id(self.actual[1]))
            if it is not None:
                self.vista.ensureVisible(it, 60, 60)

    def _dibujar_palabras(self):
        sc = self.vista.scene()
        for it in list(self._items.values()):
            if it.scene() is sc:
                sc.removeItem(it)
        self._items.clear()
        pag = self._pag(self.pagina_actual)
        if pag is None or self.doc is None:
            return
        r = self._rect_visible(self.pagina_actual)
        for p, w in self.modelo.filas:
            if p is not pag:
                continue
            poly = QPolygonF([QPointF(x, y) for x, y in ocr.esquinas(w, pag, r)])
            it = QGraphicsPolygonItem(poly)
            it.setData(0, (p, w))
            it.setZValue(2)
            sc.addItem(it)
            self._items[id(w)] = it
            self._estilo(w)

    def _estilo(self, w: ocr.PalabraOcr):
        it = self._items.get(id(w))
        if it is None:
            return
        color = _color(w)
        actual = self.actual is not None and self.actual[1] is w
        pen = QPen(color, 2.4 if actual else 1.3)
        pen.setCosmetic(True)
        it.setPen(pen)
        fondo = QColor(color)
        fondo.setAlpha(70 if actual else 28)
        it.setBrush(QBrush(fondo))
        it.setZValue(3 if actual else 2)

    def _clic_en_pagina(self, dato):
        _p, w = dato
        self._seleccionar(w)
        self.lista.setFocus()

    def _cambiar_zoom(self, paso: int):
        if paso == 0:
            self.zoom = 1.0
        else:
            self.zoom = max(0.5, min(4.0, self.zoom * (1.25 if paso > 0 else 0.8)))
        self._render()

    # ------------------------------------------------------------------ recortes
    def recorte(self, pag: ocr.PaginaOcr, w: ocr.PalabraOcr, alto: int, contexto: bool = False) -> QImage | None:
        """Recorte ampliado de la imagen donde está la palabra (para comparar con lo que se leyó)."""
        if self.doc is None:
            return None
        clave = (id(w), alto, contexto)
        if clave in self._recortes:
            return self._recortes.get(clave)
        if ctx.busy or not PDF_LOCK.acquire(blocking=False):
            QTimer.singleShot(300, self.lista.viewport().update)
            return None
        try:
            img = self._hacer_recorte(pag, w, alto, contexto)
        except Exception:  # noqa: BLE001
            img = None
        finally:
            PDF_LOCK.release()
        self._recortes[clave] = img            # la caché suelta las más viejas (600 o 24 MB, lo que llegue antes)
        return img

    def _hacer_recorte(self, pag, w, alto, contexto) -> QImage | None:
        x0, y0, x1, y1 = w.rect
        h = max(y1 - y0, 4)
        mx = h * (1.6 if contexto else 0.4)
        my = h * (0.45 if contexto else 0.15)
        rect = self._rect_visible(pag.indice)
        zona = pymupdf.Rect(x0 - mx, y0 - my, x1 + mx, y1 + my) & rect
        if zona.is_empty:
            return None
        dpr = self.devicePixelRatioF()
        zoom = max(1.0, min(8.0, alto * dpr / max(zona.height, 1)))
        page = self.doc[pag.indice]
        if pag.giro or pag.enderezada or pag.limpiar_imagen:
            base = self._pagina_img(pag)                      # se dibuja una vez y se recorta de ahí
            if base is None:
                return None
            img, z = base
            q = QRectF(zona.x0 * z, zona.y0 * z, zona.width * z, zona.height * z).toRect()
            img = img.copy(q)
        else:
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=zona, alpha=False)   # clip: visible
            img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
        if contexto and not img.isNull():                    # la palabra enmarcada dentro de su contexto
            ex = img.width() / max(zona.width, 1)
            ey = img.height() / max(zona.height, 1)
            p = QPainter(img)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            pen = QPen(_color(w), max(1.5, 1.6 * dpr))
            p.setPen(pen)
            p.drawRoundedRect(QRectF((x0 - zona.x0) * ex - 2, (y0 - zona.y0) * ey - 2,
                                     (x1 - x0) * ex + 4, (y1 - y0) * ey + 4), 3, 3)
            p.end()
        return img

    def _pagina_img(self, pag: ocr.PaginaOcr):
        hecha = self._paginas_img.get(pag.indice)
        if hecha is not None:
            return hecha
        r = self._rect_visible(pag.indice)
        z = memoria.zoom_tope(r.width, r.height, 3.0, memoria.MAX_PIXELES // 2)
        pix = ocr.renderizar(self.doc[pag.indice], pag, z)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
        pix = None
        self._paginas_img[pag.indice] = (img, z)   # hasta 3 páginas o 32 MB
        return img, z

    # ------------------------------------------------------------------ teclado y tamaño
    def eventFilter(self, obj, ev):
        try:
            t = ev.type()
        except RuntimeError:                       # el objeto ya se destruyó (al cerrar)
            return False
        if obj is getattr(self, "_vp", None) and t == QEvent.Type.Resize:
            self._render_hecho = None
            self._timer.start()
            return False
        editor, lista = getattr(self, "editor", None), getattr(self, "lista", None)
        if editor is not None and obj in (editor, lista) and t in (QEvent.Type.KeyPress,
                                                                   QEvent.Type.ShortcutOverride):
            k, mods = ev.key(), ev.modifiers()
            deshacer = ev.matches(QKeySequence.StandardKey.Undo) and (
                obj is self.lista or self.actual is None or self.editor.text() == self.actual[1].final)
            manejada = k in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab, Qt.Key.Key_Backtab,
                             Qt.Key.Key_F2) or deshacer
            if not manejada:
                return False
            if t == QEvent.Type.ShortcutOverride:
                ev.accept()
                return True
            if deshacer:
                self.deshacer()
            elif k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.aceptar()
                self.editor.setFocus()
            elif k == Qt.Key.Key_Backtab or (k == Qt.Key.Key_Tab and mods & Qt.KeyboardModifier.ShiftModifier):
                self.anterior()
            elif k == Qt.Key.Key_Tab:
                self.siguiente()
            elif k == Qt.Key.Key_F2:
                self.editar()
            return True
        return super().eventFilter(obj, ev)

    def keyPressEvent(self, e):
        if self.resultado is not None and e.key() == Qt.Key.Key_F2:
            self.editar()
            return
        super().keyPressEvent(e)


REGISTRO = dict(clave="ocrcheck", titulo="Revisar texto reconocido", seccion="convertir", orden=12,
                pagina=RevisarOcrPage, dibujo=dibujo_revisar)
