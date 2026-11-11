"""Pantalla de inicio (``Main.dc.html``): el saludo, la barra de comandos (buscar, abrir y soltar archivos), las
herramientas filtradas por categoría y «Seguir donde ibas» con los archivos recientes."""

from __future__ import annotations

import os
import time
from datetime import date, datetime
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QFontMetrics, QIcon, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractButton, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QScrollArea, QSizePolicy, QToolButton,
                               QVBoxLayout, QWidget)

from . import art, catalogo, core, extensiones, tema
from .componentes import Count, FlowLayout, Kbd, pintar_miniatura
from .icons import icon, pixmap
from .widgets import PDF_AND_IMAGES_FILTER, ask_open, collect_paths, ctx, label, reveal

DAYS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
FORMATOS = "PDF, JPG, PNG, TIFF, Word y Excel"


def relative_day(ts: float) -> str:
    d = datetime.fromtimestamp(ts).date()
    delta = (date.today() - d).days
    if delta <= 0:
        return "Hoy"
    if delta == 1:
        return "Ayer"
    if delta < 7:
        return f"{DAYS[d.weekday()]} {d.day}"
    return f"{d.day} {MONTHS[d.month - 1]}"


# ---------------------------------------------------------------- barra de comandos

class CommandBar(QFrame):
    """Buscador, zona para soltar archivos y entrada a la paleta Ctrl K a la vez (``rp-command``)."""

    buscar = Signal(str)
    enviar = Signal(str)
    abrir_archivos = Signal()
    abrir_carpeta = Signal()
    ver_recientes = Signal()

    TEXTO = "Arrastra un PDF aquí o escribe qué quieres hacer: unir, quitar la clave, comprimir…"

    def __init__(self):
        super().__init__()
        self.setObjectName("CommandBar")
        self.setProperty("drop", "false")
        self._sombra = tema.sombra(self, 2)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 16, 14)
        lay.setSpacing(14)
        fila = QHBoxLayout()
        fila.setSpacing(12)
        lupa = QLabel()
        lupa.setPixmap(pixmap("search", tema.color("ink-3"), 20, 1.7))
        fila.addWidget(lupa)
        self.entrada = QLineEdit()
        self.entrada.setObjectName("CommandInput")
        self.entrada.setPlaceholderText(self.TEXTO)
        self.entrada.setAccessibleName("Buscar herramienta o escribir qué quieres hacer")
        self.entrada.textEdited.connect(self._escrito)
        self.entrada.returnPressed.connect(lambda: self.enviar.emit(self.entrada.text()))
        fila.addWidget(self.entrada, 1)
        fila.addWidget(Kbd("Ctrl K"))
        lay.addLayout(fila)
        fila2 = QHBoxLayout()
        fila2.setSpacing(8)
        for texto, glifo, senal in (("Abrir archivos", "folder_open", self.abrir_archivos),
                                    ("Abrir carpeta", "folder", self.abrir_carpeta),
                                    ("Recientes", "clock", self.ver_recientes)):
            b = QPushButton(texto)
            b.setObjectName("CommandChip")
            b.setIcon(icon(glifo, tema.color("ink-2"), size=16))
            b.setIconSize(QSize(16, 16))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFixedHeight(32)
            b.clicked.connect(senal)
            fila2.addWidget(b)
            if texto == "Recientes":
                self.b_recientes = b
        self.formatos = label(FORMATOS, "Caption")
        fila2.addSpacing(6)
        fila2.addWidget(self.formatos)
        fila2.addStretch(1)
        self.b_enviar = QToolButton()
        self.b_enviar.setObjectName("Send")
        self.b_enviar.setIcon(QIcon(pixmap("next", tema.color("on-brand"), 18, 2.2)))
        self.b_enviar.setIconSize(QSize(18, 18))
        self.b_enviar.setFixedSize(38, 38)
        self.b_enviar.setToolTip("Buscar")
        self.b_enviar.setAccessibleName("Buscar")
        self.b_enviar.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_enviar.clicked.connect(lambda: self.enviar.emit(self.entrada.text()))
        fila2.addWidget(self.b_enviar)
        lay.addLayout(fila2)

    def _escrito(self, texto: str):
        if texto.strip():
            self.entrada.clear()
            self.buscar.emit(texto)

    def set_drop(self, n: int | None) -> None:
        """Estado «soltar» (borde punteado rojo): ``n`` archivos por abrir, o None para volver al normal."""
        on = n is not None
        self.setProperty("drop", "true" if on else "false")
        self.setGraphicsEffect(None)
        self._sombra = None if on else tema.sombra(self, 2)
        if on:
            self.entrada.setPlaceholderText(f"Suelta para abrir {n} archivo{'s' if n != 1 else ''}" if n else
                                            "Suelta para abrir los archivos")
        else:
            self.entrada.setPlaceholderText(self.TEXTO)
        self.style().unpolish(self)
        self.style().polish(self)


# ---------------------------------------------------------------- píldoras de categoría

class Pill(QAbstractButton):
    """Píldora de filtro (``rp-pill``): azulejo xs y nombre; la activa con borde de 1,5 px en ``ink``."""

    def __init__(self, text: str, cat: str | None, tile_key: str | None):
        super().__init__()
        self.cat, self.tile_key = cat, tile_key
        self.setText(text)
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAccessibleName(text)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def sizeHint(self):  # noqa: N802
        f = tema.fuente("body-strong")
        w = QFontMetrics(f).horizontalAdvance(self.text()) + (8 + 22 + 8 + 14 if self.tile_key else 28)
        return QSize(w, 36)

    def paintEvent(self, e):  # noqa: N802
        t = tema.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        on = self.isChecked()
        if on:
            r = QRectF(self.rect()).adjusted(0.75, 0.75, -0.75, -0.75)
            p.setPen(QPen(QColor(t["ink"]), 1.5))
        else:
            r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            p.setPen(QPen(QColor(t["line-strong"] if self.underMouse() else t["line"]), 1))
        p.setBrush(QColor(t["surface"]))
        p.drawRoundedRect(r, 12, 12)
        if self.hasFocus():
            p.setPen(QPen(QColor(t["focus"]), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(1.5, 1.5, -1.5, -1.5), 10, 10)
        x = 14.0
        if self.tile_key:
            p.drawPixmap(QPointF(8, 7), art.glyph_tile(self.tile_key, tema.TILE_XS, self.cat,
                                                       dpr=self.devicePixelRatioF()))
            x = 38.0
        f = tema.fuente("body", self.font())
        f.setWeight(f.Weight.DemiBold if on else f.Weight.Medium)
        p.setFont(f)
        p.setPen(QColor(t["ink"] if on or self.underMouse() else t["ink-2"]))
        p.drawText(QRectF(x, 0, self.width() - x, self.height()), Qt.AlignmentFlag.AlignVCenter, self.text())
        p.end()


# ---------------------------------------------------------------- tarjeta de herramienta

class ToolTile(QAbstractButton):
    """Tarjeta de Inicio (``rp-tile``): banda pastel de 46 px, azulejo montado sobre su borde, título y una línea.
    Al pasar el puntero sube 2 px y gana la sombra ``shadow-2``."""

    ALTO = 158

    def __init__(self, key: str, title: str, text: str, tile_key: str, cat: str):
        super().__init__()
        self.key, self.titulo, self.texto, self.tile_key, self.cat = key, title, text, tile_key, cat
        self.count: int | None = None
        self.setText(title)
        self.setAccessibleName(title)
        self.setAccessibleDescription(text)
        self.setToolTip(text)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedHeight(self.ALTO)
        self.setMinimumWidth(150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_count(self, n: int | None) -> None:
        self.count = n or None
        self.update()

    def enterEvent(self, e):  # noqa: N802
        tema.sombra(self, 2)
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):  # noqa: N802
        self.setGraphicsEffect(None)
        self.update()
        super().leaveEvent(e)

    def keyPressEvent(self, e):  # noqa: N802
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.click()
            return
        super().keyPressEvent(e)

    def paintEvent(self, e):  # noqa: N802
        t = tema.tokens()
        ton = tema.tonos(self.cat)
        hover = self.underMouse()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        dy = 0 if hover else 2
        r = QRectF(0.5, dy + 0.5, self.width() - 1, self.height() - 3)
        path = QPainterPath()
        path.addRoundedRect(r, 16, 16)
        p.fillPath(path, QColor(t["surface"]))
        p.save()
        p.setClipPath(path)
        p.fillRect(QRectF(r.left(), r.top(), r.width(), 46), QColor(ton["band"]))
        p.restore()
        p.setPen(QPen(QColor(t["surface"] if hover else t["line"]), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        if self.hasFocus():
            p.setPen(QPen(QColor(t["focus"]), 2))
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 15, 15)
        p.drawPixmap(QPointF(r.left() + 16, r.top() + 24),
                     art.glyph_tile(self.tile_key, tema.TILE_MD, self.cat, dpr=self.devicePixelRatioF()))
        if self.count:
            Count.pintar(p, QPointF(r.right() - 12, r.top() + 22), self.count)
        x, w = r.left() + 16, r.width() - 32
        ft = tema.fuente("tile-title", self.font())
        p.setFont(ft)
        p.setPen(QColor(t["ink"]))
        p.drawText(QRectF(x, r.top() + 76, w, 22), Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(ft).elidedText(self.titulo, Qt.TextElideMode.ElideRight, int(w)))
        fb = tema.fuente("body-sm", self.font())
        p.setFont(fb)
        p.setPen(QColor(t["ink-2"]))
        p.drawText(QRectF(x, r.top() + 102, w, 38), Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, self.texto)
        p.end()


# ---------------------------------------------------------------- archivo reciente

class RecentCard(QAbstractButton):
    """Archivo reciente como tarjeta (``rp-file``).  Al pasar el puntero: «Mostrar en la carpeta» o, si ya no
    existe, «Quitar de la lista»."""

    quitar = Signal(str)

    def __init__(self, entry: dict):
        super().__init__()
        self.path = entry["path"]
        self.existe = os.path.isfile(self.path)
        nombre = Path(self.path).name
        carpeta = Path(self.path).parent.name or str(Path(self.path).parent)
        pages = entry.get("pages")
        dia = relative_day(entry.get("opened", time.time())).lower()
        if self.existe:
            partes = ([f"{pages} página{'s' if pages != 1 else ''}"] if pages else
                      [core.human_size(os.path.getsize(self.path))])
            self.meta = " · ".join(partes + [dia, carpeta])
        else:
            self.meta = f"No se encuentra · {carpeta}"
        self.setText(nombre)
        self.setAccessibleName(nombre)
        self.setToolTip(self.path)
        self.setFixedHeight(62)
        self.setMinimumWidth(180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor if self.existe else Qt.CursorShape.ArrowCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.accion = QToolButton(self)
        self.accion.setObjectName("Ghost")
        glifo, ayuda = ("folder", "Mostrar en la carpeta") if self.existe else ("close", "Quitar de la lista")
        self.accion.setIcon(icon(glifo, tema.color("ink-2"), size=16))
        self.accion.setIconSize(QSize(16, 16))
        self.accion.setToolTip(ayuda)
        self.accion.setAccessibleName(ayuda)
        self.accion.setCursor(Qt.CursorShape.PointingHandCursor)
        self.accion.setFixedSize(32, 32)
        self.accion.clicked.connect(lambda: reveal(self.path) if self.existe else self.quitar.emit(self.path))
        self.accion.hide()

    def resizeEvent(self, e):  # noqa: N802
        self.accion.move(self.width() - 42, (self.height() - 32) // 2)
        super().resizeEvent(e)

    def enterEvent(self, e):  # noqa: N802
        self.accion.show()
        super().enterEvent(e)

    def leaveEvent(self, e):  # noqa: N802
        if not self.hasFocus():
            self.accion.hide()
        super().leaveEvent(e)

    def focusInEvent(self, e):  # noqa: N802
        self.accion.show()
        super().focusInEvent(e)

    def focusOutEvent(self, e):  # noqa: N802
        if not self.underMouse():
            self.accion.hide()
        super().focusOutEvent(e)

    def paintEvent(self, e):  # noqa: N802
        t = tema.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(t["focus"] if self.hasFocus() else t["line-strong"] if self.underMouse() else t["line"]),
                      2 if self.hasFocus() else 1))
        p.setBrush(QColor(t["surface"]))
        p.drawRoundedRect(r, 12, 12)
        if not self.existe:
            p.setOpacity(0.55)
        pintar_miniatura(p, QRectF(9, (self.height() - 44) / 2, 34, 44), self.text().lower().endswith(".pdf"))
        x = 9 + 34 + 12
        w = self.width() - x - (52 if self.accion.isVisible() else 14)
        fb = tema.fuente("body-strong", self.font())
        p.setFont(fb)
        p.setPen(QColor(t["ink"]))
        p.drawText(QRectF(x, 11, w, 20), Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(fb).elidedText(self.text(), Qt.TextElideMode.ElideMiddle, int(w)))
        fc = tema.fuente("caption", self.font())
        p.setFont(fc)
        p.setPen(QColor(t["ink-3"]))
        p.drawText(QRectF(x, 32, w, 16), Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(fc).elidedText(self.meta, Qt.TextElideMode.ElideRight, int(w)))
        p.end()


# ---------------------------------------------------------------- pantalla

class _Flujo(QWidget):
    """Contenedor con ``FlowLayout`` que informa su alto según el ancho (las píldoras se parten en dos filas)."""

    def __init__(self, align=Qt.AlignmentFlag.AlignLeft):
        super().__init__()
        self.flow = FlowLayout(self, 8, align)
        sp = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)

    def hasHeightForWidth(self):  # noqa: N802
        return True

    def heightForWidth(self, w):  # noqa: N802
        return self.flow.heightForWidth(w)

    def sizeHint(self):  # noqa: N802
        w = max(self.width(), 300)
        return QSize(w, self.flow.heightForWidth(w))

    def minimumSizeHint(self):  # noqa: N802
        return QSize(120, 36)


class HomePage(QWidget):
    navigate = Signal(str)
    files_dropped = Signal(list)
    buscar = Signal(str)          # abrir la paleta Ctrl K con este texto

    def __init__(self, sections=None):
        super().__init__()
        self.setObjectName("Page")
        self.setProperty("cat", "marca")
        self.setAcceptDrops(True)
        self.sections = sections if sections is not None else tema.secciones()
        self.tiles: dict[str, ToolTile] = {}
        self._cols = 0
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("ScrollBody")
        center = QHBoxLayout(body)
        center.setContentsMargins(48, 44, 48, 40)
        center.addStretch(1)
        col = QWidget()
        col.setMaximumWidth(1100)
        col.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        center.addWidget(col, 1000)
        center.addStretch(1)
        lay = QVBoxLayout(col)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(28)
        self.col = col

        # Saludo y barra de comandos
        top = QVBoxLayout()
        top.setSpacing(22)
        titulo = label("¿Qué quieres hacer hoy?", "DisplayTitle")
        titulo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top.addWidget(titulo)
        self.command = CommandBar()
        self.command.setMaximumWidth(780)
        self.command.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.command.buscar.connect(self.buscar)
        self.command.enviar.connect(self._enviar)
        self.command.abrir_archivos.connect(self._open_files)
        self.command.abrir_carpeta.connect(self._open_folder)
        self.command.ver_recientes.connect(self._ver_recientes)
        fila = QHBoxLayout()
        fila.addStretch(1)
        fila.addWidget(self.command, 100)
        fila.addStretch(1)
        top.addLayout(fila)
        lay.addLayout(top)

        # Herramientas: título y píldoras de categoría
        head = QGridLayout()
        head.setHorizontalSpacing(16)
        head.setVerticalSpacing(12)
        self.head = head
        self.head_title = label("Herramientas", "Title2")
        head.addWidget(self.head_title, 0, 0, Qt.AlignmentFlag.AlignVCenter)
        pills = _Flujo(Qt.AlignmentFlag.AlignRight)
        self.pills_flow = pills.flow
        self.pills: dict[str, Pill] = {}
        todas = Pill("Todas", None, None)
        todas.clicked.connect(lambda: self.filtrar("todas"))
        self.pills["todas"] = todas
        self.pills_flow.addWidget(todas)
        for key, lbl, tkey, _items in self.sections:
            pl = Pill(lbl, key, tkey)
            pl.clicked.connect(lambda _=False, k=key: self.filtrar(k))
            self.pills[key] = pl
            self.pills_flow.addWidget(pl)
        self.pills_w = pills
        self._pills_abajo = None
        self._colocar_pildoras()
        lay.addLayout(head)

        self.grid = QGridLayout()
        self.grid.setSpacing(16)
        inicio = {k: d for k, _a, _t, d in extensiones.inicio()}
        for sec, _lbl, _tkey, items in self.sections:
            for key, title, tkey in items:
                corto, linea = catalogo.tarjeta(key, title, inicio.get(key))
                tile = ToolTile(key, corto, linea, tkey, sec)
                tile.clicked.connect(lambda _=False, k=key: self.navigate.emit(k))
                self.tiles[key] = tile
        lay.addLayout(self.grid)

        # Seguir donde ibas
        self.recent_box = QWidget()
        rl = QVBoxLayout(self.recent_box)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(12)
        rh = QHBoxLayout()
        rh.addWidget(label("Seguir donde ibas", "Title2"))
        rh.addStretch(1)
        self.b_clear = QPushButton("Borrar lista")
        self.b_clear.setObjectName("Ghost")
        self.b_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_clear.setToolTip("Quita la lista de recientes (no borra ningún archivo). Solo se guardan los accesos, "
                                "nunca copias de tus documentos.")
        self.b_clear.clicked.connect(self._clear_recent)
        rh.addWidget(self.b_clear)
        rl.addLayout(rh)
        self.rows = QGridLayout()
        self.rows.setSpacing(12)
        rl.addLayout(self.rows)
        lay.addWidget(self.recent_box)
        lay.addStretch(1)
        self.scroll.setWidget(body)
        outer.addWidget(self.scroll)

        actual = (ctx.settings.get("inicio_categoria") if ctx.settings else None) or "todas"
        self.filtrar(actual if actual in self.pills else "todas", recordar=False)

    # ------------------------------------------------------------------ herramientas
    def filtrar(self, cat: str, recordar: bool = True) -> None:
        """Muestra solo las herramientas de una categoría («todas» = todas).  Recuerda la última elección."""
        self.categoria = cat
        self.pills[cat].setChecked(True)
        if recordar and ctx.settings is not None:
            ctx.settings.set("inicio_categoria", cat)
        self._cols = 0
        self._reacomodar()

    def _columnas(self) -> int:
        w = self.col.width() if self.col.width() > 100 else self.width() - 96
        return 5 if w >= 1000 else 4 if w >= 760 else 3 if w >= 560 else 2

    def _reacomodar(self) -> None:
        cols = self._columnas()
        if cols == self._cols:
            return
        self._cols = cols
        while self.grid.count():
            self.grid.takeAt(0)
        visibles = [t for t in self.tiles.values() if self.categoria == "todas" or t.cat == self.categoria]
        for k, t in enumerate(visibles):
            self.grid.addWidget(t, k // cols, k % cols)
        for t in self.tiles.values():          # después de agregarlas: sin padre, mostrarlas crearía ventanas
            t.setVisible(t in visibles)
        for c in range(6):
            self.grid.setColumnStretch(c, 1 if c < cols else 0)
            self.grid.setColumnMinimumWidth(c, 0)

    def _colocar_pildoras(self) -> None:
        """Las píldoras van a la derecha del título si caben en una fila; si no, en su propia fila debajo."""
        ancho = self.col.width() if self.col.width() > 100 else 1000
        necesario = sum(pl.sizeHint().width() for pl in self.pills.values()) + 8 * (len(self.pills) - 1)
        abajo = self.head_title.sizeHint().width() + 16 + necesario > ancho
        if abajo == self._pills_abajo:
            return
        self._pills_abajo = abajo
        self.head.removeWidget(self.pills_w)
        if abajo:
            self.pills_w.flow._align = Qt.AlignmentFlag.AlignLeft
            self.head.addWidget(self.pills_w, 1, 0, 1, 2)
        else:
            self.pills_w.flow._align = Qt.AlignmentFlag.AlignRight
            self.head.addWidget(self.pills_w, 0, 1)
        self.head.setColumnStretch(1, 1)
        self.pills_w.flow.invalidate()

    def set_count(self, key: str, n: int | None) -> None:
        """Contador rojo de pendientes reales en la tarjeta de una herramienta."""
        if key in self.tiles:
            self.tiles[key].set_count(n)

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        QTimer.singleShot(0, self._reacomodar)
        QTimer.singleShot(0, self._colocar_pildoras)

    # ------------------------------------------------------------------ recientes
    def refresh_recent(self):
        while self.rows.count():
            it = self.rows.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        items = ctx.settings.recent() if ctx.settings else []
        self.b_clear.setVisible(bool(items))
        self.recent_box.setVisible(bool(items))
        for k, entry in enumerate(items[:6]):
            card = RecentCard(entry)
            if card.existe:
                card.clicked.connect(lambda _=False, p=entry["path"]: self.files_dropped.emit([p]))
            card.quitar.connect(lambda p: (ctx.settings.remove_recent(p), self.refresh_recent()))
            self.rows.addWidget(card, k // 3, k % 3)
        for c in range(3):
            self.rows.setColumnStretch(c, 1)

    def _clear_recent(self):
        if ctx.settings:
            ctx.settings.clear_recent()
        self.refresh_recent()

    def _ver_recientes(self):
        if self.recent_box.isVisible():
            self.scroll.ensureWidgetVisible(self.recent_box, 0, 40)
        else:
            self.buscar.emit("")

    # ------------------------------------------------------------------ abrir
    def _enviar(self, texto: str):
        """Botón redondo: abre la primera herramienta que coincide; sin texto, la paleta."""
        texto = texto.strip()
        if texto:
            herr = [(k, t, s) for s, _l, _a, items in self.sections for k, t, _x in items]
            r = catalogo.buscar(texto, herr, 1)
            if r:
                self.command.entrada.clear()
                self.navigate.emit(r[0]["clave"])
                return
        self.buscar.emit(texto)

    def _open_files(self):
        paths = ask_open(self, True, extensiones.filtro_inicio(PDF_AND_IMAGES_FILTER), "Abrir archivos")
        if paths:
            self.files_dropped.emit(paths)

    def _open_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Abrir carpeta", ctx.last_dir)
        if not folder:
            return
        ctx.remember_dir(folder)
        paths = collect_paths([QUrl.fromLocalFile(folder)], extensiones.tipos_inicio())
        if not paths:
            QMessageBox.information(self, "Abrir carpeta", "Esa carpeta no tiene PDF ni imágenes.")
            return
        self.files_dropped.emit(paths)

    # ------------------------------------------------------------------ eventos
    def showEvent(self, e):
        super().showEvent(e)
        self.refresh_recent()
        self._cols = 0
        self._reacomodar()
        self._colocar_pildoras()
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(0))

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            n = len([u for u in e.mimeData().urls() if u.isLocalFile()])
            self.command.set_drop(n)

    def dragLeaveEvent(self, e):
        self.command.set_drop(None)

    def dropEvent(self, e):
        self.command.set_drop(None)
        paths = collect_paths(e.mimeData().urls(), extensiones.tipos_inicio())
        if paths:
            self.files_dropped.emit(paths)
