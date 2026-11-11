"""Paleta de comandos Ctrl K (``rp-palette``): busca herramientas por nombre o por sinónimo y archivos
recientes, y abre lo elegido con Enter.

Es una capa sobre la ventana principal: vela (``scrim``) a todo lo ancho y, arriba al centro, el cuadro de
600 px.  Flechas para moverse, Enter para abrir, Esc (o un clic fuera) para cerrar.  Se abre con Ctrl K en
cualquier pantalla, con el buscador de la barra superior y al escribir en la barra de comandos de Inicio.
"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QScrollArea, QSizePolicy, QVBoxLayout,
                               QWidget)

from . import art, catalogo, tema
from .componentes import Kbd
from .icons import pixmap

ANCHO = 600
ALTO_FILA = 52
ALTO_GRUPO = 30


class ListaPaleta(QWidget):
    """Resultados agrupados, pintados a mano (filas de 52 px)."""

    activar = Signal(int)

    def __init__(self):
        super().__init__()
        self.filas: list[dict] = []
        self.activa = -1
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_filas(self, filas: list[dict]) -> None:
        self.filas = filas
        elegibles = self.elegibles()
        self.activa = elegibles[0] if elegibles else -1
        self.setFixedHeight(self._alto_total())
        self.update()

    def elegibles(self) -> list[int]:
        return [i for i, f in enumerate(self.filas) if f["tipo"] in ("herr", "archivo")]

    def _alto(self, f: dict) -> int:
        return {"grupo": ALTO_GRUPO, "vacio": 64}.get(f["tipo"], ALTO_FILA + 2)

    def _alto_total(self) -> int:
        return 16 + sum(self._alto(f) for f in self.filas)

    def rect_de(self, i: int) -> QRectF:
        y = 8
        for k, f in enumerate(self.filas):
            h = self._alto(f)
            if k == i:
                return QRectF(8, y, self.width() - 16, h - (2 if f["tipo"] in ("herr", "archivo") else 0))
            y += h
        return QRectF()

    def mover(self, paso: int) -> None:
        el = self.elegibles()
        if not el:
            return
        k = el.index(self.activa) if self.activa in el else -1
        self.activa = el[(k + paso) % len(el)]
        self.update()

    def _fila_en(self, pos) -> int:
        for i in self.elegibles():
            if self.rect_de(i).contains(pos):
                return i
        return -1

    def mouseMoveEvent(self, e):  # noqa: N802
        i = self._fila_en(e.position())
        if i >= 0 and i != self.activa:
            self.activa = i
            self.update()
        self.setCursor(Qt.CursorShape.PointingHandCursor if i >= 0 else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, e):  # noqa: N802
        i = self._fila_en(e.position())
        if i >= 0 and e.button() == Qt.MouseButton.LeftButton:
            self.activar.emit(i)

    def paintEvent(self, _e):  # noqa: N802
        t = tema.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        dpr = self.devicePixelRatioF()
        for i, f in enumerate(self.filas):
            r = self.rect_de(i)
            if f["tipo"] == "grupo":
                p.setFont(tema.fuente("chip", self.font()))
                p.setPen(QColor(t["ink-3"]))
                p.drawText(r.adjusted(10, 10, -10, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                           f["texto"])
                continue
            if f["tipo"] == "vacio":
                p.setFont(tema.fuente("body-sm", self.font()))
                p.setPen(QColor(t["ink-2"]))
                p.drawText(r.adjusted(12, 0, -12, 0), Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap,
                           f["texto"])
                continue
            if i == self.activa:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(t["surface-sunken"]))
                p.drawRoundedRect(r, 12, 12)
            tile = art.glyph_tile(f["glifo"], tema.TILE_SM, f["cat"], soft=f["tipo"] == "archivo", dpr=dpr)
            p.drawPixmap(QPointF(r.left() + 10, r.center().y() - tema.TILE_SM / 2), tile)
            x = r.left() + 10 + tema.TILE_SM + 12
            kbd_w = 0
            if i == self.activa:
                kf = tema.fuente("chip", self.font())
                kf.setPixelSize(11)
                kbd_w = QFontMetrics(kf).horizontalAdvance("Enter") + 12
                kr = QRectF(r.right() - 10 - kbd_w, r.center().y() - 9, kbd_w, 18)
                p.setPen(QColor(t["line"]))
                p.setBrush(QColor(t["surface-sunken"]))
                p.drawRoundedRect(kr, 6, 6)
                p.setFont(kf)
                p.setPen(QColor(t["ink-2"]))
                p.drawText(kr, Qt.AlignmentFlag.AlignCenter, "Enter")
            ancho = r.right() - x - kbd_w - 20
            fb = tema.fuente("body-strong", self.font())
            p.setFont(fb)
            p.setPen(QColor(t["ink"]))
            p.drawText(QRectF(x, r.top() + 8, ancho, 20), Qt.AlignmentFlag.AlignVCenter,
                       QFontMetrics(fb).elidedText(f["titulo"], Qt.TextElideMode.ElideMiddle, int(ancho)))
            fc = tema.fuente("caption", self.font())
            fcb = tema.fuente("caption", self.font())
            fcb.setWeight(fcb.Weight.DemiBold)
            y = r.top() + 28
            xx = x
            for texto, negrita in ((f.get("pre", ""), False), (f.get("match", ""), True), (f.get("post", ""), False)):
                if not texto:
                    continue
                fnt = fcb if negrita else fc
                p.setFont(fnt)
                p.setPen(QColor(t["ink-2"] if negrita else t["ink-3"]))
                w = QFontMetrics(fnt).horizontalAdvance(texto)
                visible = QFontMetrics(fnt).elidedText(texto, Qt.TextElideMode.ElideRight, int(max(0, x + ancho - xx)))
                p.drawText(QRectF(xx, y, w + 2, 16), Qt.AlignmentFlag.AlignVCenter, visible)
                xx += w
        p.end()


class PaletaComandos(QWidget):
    """La paleta Ctrl K, como capa hija de la ventana principal."""

    abrir_herramienta = Signal(str)
    abrir_archivo = Signal(str)

    def __init__(self, ventana: QWidget, herramientas, recientes):
        """``herramientas()`` → [(clave, título, categoría, glifo)]; ``recientes()`` → [dict con «path»…]."""
        super().__init__(ventana)
        self.ventana = ventana
        self.herramientas = herramientas
        self.recientes = recientes
        self.setObjectName("PaletteScrim")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.hide()
        self._previo: QWidget | None = None
        self._resultados: list[dict] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 112, 24, 24)
        lay.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        self.caja = QFrame()
        self.caja.setObjectName("Palette")
        self.caja.setFixedWidth(ANCHO)
        tema.sombra(self.caja, 3)
        self.caja.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Maximum)
        lay.addWidget(self.caja, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        lay.addStretch(1)
        cl = QVBoxLayout(self.caja)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)

        fila = QFrame()
        fila.setObjectName("PaletteInputRow")
        fl = QHBoxLayout(fila)
        fl.setContentsMargins(18, 16, 18, 16)
        fl.setSpacing(12)
        lupa = QLabel()
        lupa.setPixmap(pixmap("search", tema.color("ink-3"), 20, 1.7))
        fl.addWidget(lupa)
        self.entrada = QLineEdit()
        self.entrada.setObjectName("PaletteInput")
        self.entrada.setPlaceholderText("Escribe qué quieres hacer")
        self.entrada.setAccessibleName("Buscar herramienta o archivo")
        self.entrada.textChanged.connect(self.actualizar)
        self.entrada.installEventFilter(self)
        fl.addWidget(self.entrada, 1)
        esc = Kbd("Esc")
        esc.setCursor(Qt.CursorShape.PointingHandCursor)
        esc.mouseReleaseEvent = lambda _e: self.cerrar()
        fl.addWidget(esc)
        cl.addWidget(fila)

        self.lista = ListaPaleta()
        self.lista.activar.connect(self._abrir)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("PaletteScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.viewport().setAutoFillBackground(False)
        self.scroll.setWidget(self.lista)
        cl.addWidget(self.scroll)

        pie = QFrame()
        pie.setObjectName("PaletteFoot")
        pl = QHBoxLayout(pie)
        pl.setContentsMargins(18, 10, 18, 10)
        pl.setSpacing(6)
        for teclas, texto in ((("↑", "↓"), "moverse"), (("Enter",), "abrir"), (("Esc",), "cerrar")):
            for k in teclas:
                pl.addWidget(Kbd(k))
            lb = QLabel(texto)
            lb.setObjectName("Caption")
            pl.addWidget(lb)
            pl.addSpacing(10)
        pl.addStretch(1)
        prueba = QLabel("Prueba: «achicar», «tapar cédulas», «clave»")
        prueba.setObjectName("Caption")
        pl.addWidget(prueba)
        cl.addWidget(pie)
        ventana.installEventFilter(self)

    # ------------------------------------------------------------------ abrir y cerrar
    def abrir(self, texto: str = "") -> None:
        if not self.isVisible():
            self._previo = self.ventana.focusWidget()
        self.setGeometry(self.ventana.rect())
        self.raise_()
        self.show()
        self.entrada.setText(texto)
        self.actualizar(texto)
        self.entrada.setFocus(Qt.FocusReason.PopupFocusReason)
        self.entrada.end(False)

    def cerrar(self) -> None:
        if not self.isVisible():
            return
        self.hide()
        if self._previo is not None:
            try:
                self._previo.setFocus()
            except RuntimeError:
                pass
        self._previo = None

    # ------------------------------------------------------------------ resultados
    def actualizar(self, texto: str | None = None) -> None:
        q = self.entrada.text() if texto is None else texto
        herr = list(self.herramientas())
        por_clave = {h[0]: h for h in herr}
        filas: list[dict] = []
        if q.strip():
            encontradas = catalogo.buscar(q, [(k, t, c) for k, t, c, _g in herr])
        else:
            encontradas = [dict(clave=k, titulo=por_clave[k][1], cat=por_clave[k][2],
                                pre=f"En {tema.NOMBRES_CATEGORIA.get(por_clave[k][2], '')}", match="", post="")
                           for k in catalogo.SUGERIDAS if k in por_clave]
        if encontradas:
            filas.append({"tipo": "grupo", "texto": "Herramientas" if q.strip() else "Sugeridas"})
            for d in encontradas:
                filas.append(dict(d, tipo="herr", glifo=por_clave[d["clave"]][3]))
        nq = catalogo.normalizar(q.strip())
        archivos = []
        for r in self.recientes():
            nombre = Path(r["path"]).name
            if nq and nq not in catalogo.normalizar(nombre):
                continue
            archivos.append(r)
            if len(archivos) == 3:
                break
        if archivos:
            filas.append({"tipo": "grupo", "texto": "Archivos recientes"})
            for r in archivos:
                filas.append({"tipo": "archivo", "titulo": Path(r["path"]).name, "path": r["path"],
                              "glifo": "recent", "cat": "marca", "pre": self._meta(r), "match": "", "post": ""})
        if not filas:
            filas.append({"tipo": "vacio", "texto": f"No encontramos «{q.strip()}». Prueba con otra palabra: "
                                                    "unir, firmar, comprimir, clave."})
        self.lista.set_filas(filas)
        self._ajustar_alto()

    @staticmethod
    def _meta(r: dict) -> str:
        from .home import relative_day
        import time
        carpeta = Path(r["path"]).parent.name or str(Path(r["path"]).parent)
        partes = [carpeta, relative_day(r.get("opened", time.time())).lower()]
        if not os.path.isfile(r["path"]):
            partes.append("no se encuentra")
        return " · ".join(partes)

    def _ajustar_alto(self) -> None:
        disponible = max(160, self.height() - 112 - 24 - 56 - 44)
        self.scroll.setFixedHeight(min(self.lista.height(), disponible))
        self.caja.setFixedWidth(min(ANCHO, max(320, self.width() - 48)))

    def _abrir(self, i: int) -> None:
        if not (0 <= i < len(self.lista.filas)):
            return
        f = self.lista.filas[i]
        self.cerrar()
        if f["tipo"] == "herr":
            self.abrir_herramienta.emit(f["clave"])
        elif f["tipo"] == "archivo":
            self.abrir_archivo.emit(f["path"])

    # ------------------------------------------------------------------ eventos
    def eventFilter(self, obj, e):  # noqa: N802
        if obj is self.ventana and e.type() == QEvent.Type.Resize and self.isVisible():
            self.setGeometry(self.ventana.rect())
            self._ajustar_alto()
        elif obj is self.entrada and e.type() == QEvent.Type.KeyPress:
            k = e.key()
            if k in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                self.lista.mover(1 if k == Qt.Key.Key_Down else -1)
                r = self.lista.rect_de(self.lista.activa)
                self.scroll.ensureVisible(0, int(r.center().y()), 0, int(r.height()))
                return True
            if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._abrir(self.lista.activa)
                return True
            if k == Qt.Key.Key_Escape:
                self.cerrar()
                return True
        return False

    def keyPressEvent(self, e):  # noqa: N802
        if e.key() == Qt.Key.Key_Escape:
            self.cerrar()
            return
        super().keyPressEvent(e)

    def mousePressEvent(self, e):  # noqa: N802
        if not self.caja.geometry().contains(e.position().toPoint()):
            self.cerrar()
            return
        super().mousePressEvent(e)

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), tema.qcolor("scrim"))
        p.end()

    def sizeHint(self):  # noqa: N802
        return QSize(ANCHO, 400)
