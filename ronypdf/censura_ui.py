"""Pantallas «Censurar datos» y «Quitar datos ocultos» (sección Seguridad).

La lógica vive en ``censura.py``; aquí solo hay interfaz.  Las dos herramientas se registran con
``REGISTRO`` (ver ``extensiones.py``).  Las dos usan la plantilla 2.0 (``docs/diseno/PLANTILLA.md``):

* **Censurar datos:** el PDF en ficha en el encabezado; los pasos «¿Qué quieres censurar?» (fichas con el
  conteo de hallazgos), «¿Cómo se ve lo censurado?» (segmentos) y «Revisa lo encontrado» (la lista por tipo,
  con sus casillas); a la derecha la página con las marcas, en dos vistas: «Lo encontrado» y «Cómo quedará».
* **Quitar datos ocultos:** lo que viaja dentro del PDF como lista de tarjetas en el área principal y, en
  el panel, qué quitar (lo recomendado, todo o uno por uno).
"""

from __future__ import annotations

import dataclasses
from collections import defaultdict
from pathlib import Path

import pymupdf
from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QFrame, QHBoxLayout, QHeaderView,
                               QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
                               QStyle, QStyledItemDelegate, QToolButton, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from . import censura, core, extensiones, tema
from .icons import icon, pixmap
from .tools import check_spec, pages_edit
from .widgets import (PDF_LOCK, Chip, Choices, GlyphTile, Note, OptionGroup, Segmented, SingleFilePicker,
                      ToolPage, ask_open, ask_save, cat_de, ctx, label, show_done, suggest_output)

AVISO_ESCANEADO = ("Este PDF es escaneado: los datos se buscan con reconocimiento de texto (OCR), que puede "
                   "fallar con letra borrosa o manuscrita. Revisa cada página antes de compartir.")
NOTA_ORIGINAL = "El original no cambia. En la copia, lo censurado se borra de verdad."
SIN_RESULTADOS = "No se encontraron datos con esos criterios."
ANTES_DE_BUSCAR = ("Pulsa «Buscar datos» para encontrar los datos personales. También puedes marcar zonas a mano "
                   "sobre la página.")
ORDEN_GRUPOS = list(censura.PATRONES) + ["palabra", "regex", "falsa", "manual"]

# Nombres cortos para las fichas y los grupos de la lista (el nombre completo va en la ayuda emergente).
FICHAS = {"cedula": "Cédulas", "nit": "NIT", "celular": "Celulares", "telefono": "Fijos", "correo": "Correos",
          "cuenta": "Cuentas", "tarjeta": "Tarjetas", "direccion": "Direcciones", "placa": "Placas",
          "fecha": "Fechas"}
GRUPOS = {**FICHAS, "palabra": "Palabras y nombres", "regex": "Expresión propia",
          "falsa": "Recuadros que no borraban", "manual": "Zonas marcadas a mano"}

RELLENOS = [("Barra negra", (0, 0, 0)), ("Blanco", (1, 1, 1))]
VISTAS = [("Lo encontrado", "antes"), ("Cómo quedará", "despues")]


def _s(n: int, uno: str, varios: str) -> str:
    return f"{n} {uno if n == 1 else varios}"


def _color(nombre: str, alfa: int | None = None) -> QColor:
    c = QColor(tema.color(nombre))
    if alfa is not None:
        c.setAlpha(alfa)
    return c


# --------------------------------------------------------------------------
# Íconos (cuadrícula de 24 × 24, de línea, como los de icons.py)
# --------------------------------------------------------------------------

def _hoja(p: QPainter, x: float, y: float, w: float, h: float, doblez: float = 4.5) -> None:
    path = QPainterPath()
    path.moveTo(x, y)
    path.lineTo(x + w - doblez, y)
    path.lineTo(x + w, y + doblez)
    path.lineTo(x + w, y + h)
    path.lineTo(x, y + h)
    path.closeSubpath()
    p.drawPath(path)
    p.drawPolyline([QPointF(x + w - doblez, y), QPointF(x + w - doblez, y + doblez), QPointF(x + w, y + doblez)])


def dibujo_censurar(p: QPainter, base: QColor, acento: QColor) -> None:
    """Hoja con renglones y uno tachado con una barra roja."""
    _hoja(p, 4, 2, 16, 20)
    for y, x1 in ((8.0, 13.0), (11.5, 16.5), (19.0, 12.5)):
        p.drawLine(QPointF(7.5, y), QPointF(x1, y))
    p.save()
    extensiones.lapiz(p, acento)
    p.setBrush(acento)
    p.drawRoundedRect(QRectF(7.0, 14.0, 9.8, 2.4), 0.7, 0.7)
    p.restore()


def _destello(p: QPainter, cx: float, cy: float, r: float) -> None:
    k = r * 0.25
    path = QPainterPath()
    path.moveTo(cx, cy - r)
    path.quadTo(cx + k, cy - k, cx + r, cy)
    path.quadTo(cx + k, cy + k, cx, cy + r)
    path.quadTo(cx - k, cy + k, cx - r, cy)
    path.quadTo(cx - k, cy - k, cx, cy - r)
    p.drawPath(path)


def dibujo_limpiar(p: QPainter, base: QColor, acento: QColor) -> None:
    """Hoja con un destello: el documento queda limpio."""
    _hoja(p, 3, 2, 11.5, 17, 3.8)
    p.drawLine(QPointF(6, 8.5), QPointF(10.5, 8.5))
    p.drawLine(QPointF(6, 12), QPointF(11, 12))
    p.save()
    extensiones.lapiz(p, acento)
    _destello(p, 18.2, 16.8, 4.4)
    _destello(p, 19.5, 6.5, 2.0)
    p.restore()


# --------------------------------------------------------------------------
# Piezas pequeñas de la interfaz
# --------------------------------------------------------------------------

def _boton(texto: str, nombre: str = "Secondary", glifo: str | None = None, tam: str | None = "sm",
           cb=None) -> QPushButton:
    b = QPushButton(texto)
    b.setObjectName(nombre)
    if tam:
        b.setProperty("tam", tam)
    if glifo:
        b.setIcon(icon(glifo, tema.color("ink-2"), size=16))
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if cb:
        b.clicked.connect(cb)
    return b


def _boton_icono(glifo: str, ayuda: str, cb) -> QToolButton:
    b = QToolButton()
    b.setObjectName("Ghost")
    b.setIcon(icon(glifo, tema.color("ink-2"), size=18))
    b.setIconSize(QSize(18, 18))
    b.setToolTip(ayuda)
    b.setAccessibleName(ayuda)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.clicked.connect(cb)
    return b


def _nota_con_boton(tono: str, texto_boton: str, cb) -> tuple[Note, QPushButton]:
    """Aviso (``Note``) con un botón debajo del texto, dentro del mismo recuadro."""
    nota = Note("", tono)
    h = nota.layout()
    h.removeWidget(nota.text_lb)
    col = QVBoxLayout()
    col.setSpacing(8)
    col.addWidget(nota.text_lb)
    b = _boton(texto_boton, cb=cb)
    col.addWidget(b, 0, Qt.AlignmentFlag.AlignLeft)
    h.addLayout(col, 1)
    return nota, b


def _boton_avanzadas(cb) -> QPushButton:
    b = _boton("Opciones avanzadas", "Ghost", "down_chevron")
    b.setCheckable(True)
    b.toggled.connect(cb)
    return b


# --------------------------------------------------------------------------
# Vista de una página con los hallazgos marcados
# --------------------------------------------------------------------------

class _Lienzo(QWidget):
    """La página (blanca, como en el sistema) con las marcas encima.  Clic en una marca: la cambia; con
    «Marcar zona a mano», arrastrar dibuja una zona nueva."""

    def __init__(self, vista: "VistaPagina"):
        super().__init__()
        self.vista = vista
        self._desde: QPointF | None = None
        self._hasta: QPointF | None = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(420, 594)

    def escala(self) -> float:
        return self.width() / max(self.vista.tam[0], 1)

    def a_pantalla(self, r) -> QRectF:
        s = self.escala()
        return QRectF(r[0] * s, r[1] * s, (r[2] - r[0]) * s, (r[3] - r[1]) * s)

    def paintEvent(self, e):
        ton = tema.tonos(cat_de(self) or "seguridad")
        incl = QColor(ton["c"])
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#ffffff"))         # la página es blanca en los dos temas
        if self.vista.img is not None:
            p.drawImage(QRectF(self.rect()), self.vista.img)
        despues = self.vista.despues
        for i, r, incluir, _origen in self.vista.marcas():
            q = self.a_pantalla(r).adjusted(-1, -1, 1, 1)
            sel = i == self.vista.seleccion
            if despues:
                if incluir:
                    p.setPen(QPen(_color("line-strong"), 1) if self.vista.relleno.lightness() > 200
                             else Qt.PenStyle.NoPen)
                    p.setBrush(self.vista.relleno)
                    p.drawRect(q)
                continue
            if incluir:
                fondo = QColor(incl)
                fondo.setAlpha(62 if sel else 36)
                p.setPen(QPen(incl, 2.4 if sel else 1.5))
                p.setBrush(fondo)
            else:
                pen = QPen(_color("ink-3"), 2.0 if sel else 1.3)
                pen.setStyle(Qt.PenStyle.DashLine)
                p.setPen(pen)
                p.setBrush(_color("ink-3", 26))
            p.drawRoundedRect(q, 2, 2)
            if sel:
                halo = QColor(incl if incluir else _color("ink-3"))
                halo.setAlpha(90)
                p.setPen(QPen(halo, 4))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(q.adjusted(-3, -3, 3, 3), 4, 4)
        if self._desde is not None and self._hasta is not None:
            pen = QPen(incl, 1.5)
            pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            fondo = QColor(incl)
            fondo.setAlpha(45)
            p.setBrush(fondo)
            p.drawRect(QRectF(self._desde, self._hasta).normalized())
        p.setPen(QPen(_color("line"), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5))
        p.end()

    def _en_pagina(self, pt: QPointF) -> tuple[float, float]:
        s = self.escala()
        return pt.x() / s, pt.y() / s

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or self.vista.ruta is None:
            return
        if self.vista.modo_zona():
            self._desde = self._hasta = e.position()
            self.update()
            return
        x, y = self._en_pagina(e.position())
        i = self.vista.marca_en(x, y)
        if i is not None:
            self.vista.marca_clic.emit(i)

    def mouseMoveEvent(self, e):
        if self._desde is not None:
            self._hasta = e.position()
            self.update()

    def mouseReleaseEvent(self, e):
        if self._desde is None:
            return
        self._hasta = e.position()
        a, b = self._en_pagina(self._desde), self._en_pagina(self._hasta)
        self._desde = self._hasta = None
        self.update()
        r = (min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))
        w, h = self.vista.tam
        r = (max(0.0, r[0]), max(0.0, r[1]), min(w, r[2]), min(h, r[3]))
        if r[2] - r[0] >= 4 and r[3] - r[1] >= 4:
            self.vista.zona_nueva.emit(self.vista.pagina, r)


class VistaPagina(QFrame):
    """Vista previa del área principal: una barra (vista «Lo encontrado» o «Cómo quedará», páginas y «Marcar
    zona a mano») y la página con los hallazgos encima: marca llena = se censura, punteada = se deja."""
    marca_clic = Signal(int)
    zona_nueva = Signal(int, object)
    pagina_cambiada = Signal(int)

    def __init__(self, proveedor):
        super().__init__()
        self.proveedor = proveedor          # función(página) → [(índice, rect, incluir, origen)]
        self.ruta: str | None = None
        self.password: str | None = None
        self.total = 0
        self.pagina = 0
        self.seleccion: int | None = None
        self.tam = (595.0, 842.0)
        self.img: QImage | None = None
        self.despues = False                # «Cómo quedará»: las zonas incluidas ya rellenas
        self.relleno = QColor(0, 0, 0)
        self._clave = None

        v = QVBoxLayout(self)
        v.setContentsMargins(16, 12, 16, 12)
        v.setSpacing(10)
        self._compacta: int | None = None
        self.fila1 = QHBoxLayout()
        self.fila1.setSpacing(4)
        self.seg_vista = Segmented(VISTAS)
        self.seg_vista.changed.connect(lambda _k, valor: self.set_despues(valor == "despues"))
        self.fila1.addWidget(self.seg_vista)
        self.fila1.addStretch(1)
        # páginas, tamaño y «Marcar zona a mano»: con poco ancho bajan a una segunda fila
        self.nav = QWidget()
        nl = QHBoxLayout(self.nav)
        nl.setContentsMargins(0, 0, 0, 0)
        nl.setSpacing(4)
        self.b_prev = _boton_icono("prev", "Página anterior", lambda: self.mostrar(self.pagina - 1))
        self.b_next = _boton_icono("next", "Página siguiente", lambda: self.mostrar(self.pagina + 1))
        self.lb_pagina = label("", "Caption")
        self.b_ancho = _boton_icono("zoom", "Ver la página más grande (ajustada al ancho)", lambda: None)
        self.b_ancho.setCheckable(True)
        self.b_ancho.toggled.connect(self._ancho_cambiado)
        nl.addWidget(self.b_prev)
        nl.addWidget(self.lb_pagina)
        nl.addWidget(self.b_next)
        nl.addWidget(self.b_ancho)
        nl.addSpacing(6)
        self.b_zona = _boton("Marcar zona a mano", glifo="square")
        self.b_zona.setCheckable(True)
        self.b_zona.setToolTip("Arrastra un rectángulo sobre una firma, una foto o un sello para censurarlo")
        self.b_zona.setAccessibleName("Marcar zona a mano")
        self.b_zona.toggled.connect(self._modo_cambiado)
        nl.addWidget(self.b_zona)
        self.fila1.addWidget(self.nav)
        v.addLayout(self.fila1)
        self.fila2 = QHBoxLayout()
        self.fila2.setContentsMargins(0, 0, 0, 0)
        self.fila2.addStretch(1)
        v.addLayout(self.fila2)

        self.scroll = QScrollArea()
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidgetResizable(False)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; }")
        self.scroll.viewport().setAutoFillBackground(False)
        self.lienzo = _Lienzo(self)
        self.scroll.setWidget(self.lienzo)
        self.scroll.viewport().installEventFilter(self)
        v.addWidget(self.scroll, 1)
        self.leyenda = label("", "Caption", wrap=True)
        self.leyenda.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self.leyenda)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._render)
        self._estilos()
        self._actualizar_barra()
        self.setMinimumWidth(280)        # la barra se compacta sola según el ancho (ver resizeEvent)

    # --- estado
    def modo_zona(self) -> bool:
        return self.b_zona.isChecked()

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        ancho = self.width()
        self._compactar(2 if ancho < 500 else 1 if ancho < 680 else 0)

    def _compactar(self, nivel: int):
        """0: todo con texto; 1: «Marcar zona a mano» solo con el ícono y «1 de 4»; 2: además, la navegación
        baja a una segunda fila (el menú abierto en la ventana mínima)."""
        if nivel == self._compacta:
            return
        self._compacta = nivel
        self.b_zona.setText("" if nivel else "Marcar zona a mano")
        destino = self.fila2 if nivel == 2 else self.fila1
        if self.nav.parentWidget() is not None and destino.indexOf(self.nav) < 0:
            (self.fila1 if destino is self.fila2 else self.fila2).removeWidget(self.nav)
            destino.addWidget(self.nav)
        self._actualizar_barra()

    def _ancho_cambiado(self, on: bool):
        self.b_ancho.setToolTip("Ver la página entera" if on else "Ver la página más grande (ajustada al ancho)")
        self._clave = None
        self._render()

    def _estilos(self):
        """«Marcar zona a mano» y la lupa encendidos se ven en el color de la categoría (como una ficha
        elegida)."""
        ton = tema.tonos(cat_de(self) or "seguridad")
        self.b_zona.setStyleSheet(f"QPushButton#Secondary:checked {{ background: {ton['band']}; "
                                  f"border-color: {ton['c']}; color: {ton['ink']}; }}")
        self.b_ancho.setStyleSheet(f"QToolButton#Ghost:checked {{ background: {ton['band']}; }}")

    def _modo_cambiado(self, on: bool):
        self.lienzo.setCursor(Qt.CursorShape.CrossCursor if on else Qt.CursorShape.PointingHandCursor)
        if on and self.despues:
            self.seg_vista.set_value("antes")
        self._actualizar_leyenda()

    def set_despues(self, on: bool):
        self.despues = bool(on)
        if self.seg_vista.value() != ("despues" if on else "antes"):
            self.seg_vista.set_value("despues" if on else "antes")
        if on and self.b_zona.isChecked():
            self.b_zona.setChecked(False)
        self._actualizar_leyenda()
        self.lienzo.update()

    def set_relleno(self, rgb) -> None:
        self.relleno = QColor.fromRgbF(*rgb)
        self.lienzo.update()

    def _actualizar_leyenda(self):
        if not self.ruta:
            texto = ""
        elif self.modo_zona():
            texto = "Arrastra un rectángulo sobre una firma, una foto o un sello."
        elif self.despues:
            texto = "Así quedará la copia. Haz clic en una zona para dejarla sin censurar."
        else:
            texto = "Marca llena: se censura · Punteada: se deja. Haz clic en una marca para cambiarla."
        self.leyenda.setText(texto)

    def marcas(self) -> list:
        return self.proveedor(self.pagina) if self.ruta else []

    def marca_en(self, x: float, y: float) -> int | None:
        mejor = None
        for i, r, inc, _o in self.marcas():
            if self.despues and not inc:
                continue                       # en «Cómo quedará» lo que se deja no se ve
            if r[0] - 3 <= x <= r[2] + 3 and r[1] - 3 <= y <= r[3] + 3:
                area = (r[2] - r[0]) * (r[3] - r[1])
                if mejor is None or area < mejor[0]:
                    mejor = (area, i)
        return mejor[1] if mejor else None

    def cargar(self, ruta: str | None, password: str | None, total: int):
        self.ruta, self.password, self.total = ruta, password, total
        self.pagina, self.seleccion, self.img, self._clave = 0, None, None, None
        self.b_zona.setChecked(False)
        self.set_despues(False)
        if ruta:
            self.mostrar(0)
        else:
            self._actualizar_barra()
            self.lienzo.update()

    def mostrar(self, pagina: int, seleccion: int | None = None):
        if not self.ruta or self.total <= 0:
            return
        pagina = max(0, min(pagina, self.total - 1))
        cambio = pagina != self.pagina
        self.pagina = pagina
        self.seleccion = seleccion
        self._actualizar_barra()
        self._render()
        self.lienzo.update()
        if cambio:
            self.pagina_cambiada.emit(pagina)
        if seleccion is not None:
            for i, r, _inc, _o in self.marcas():
                if i == seleccion:
                    q = self.lienzo.a_pantalla(r)
                    self.scroll.ensureVisible(int(q.center().x()), int(q.center().y()), 80, 120)
                    break

    def _actualizar_barra(self):
        hay = bool(self.ruta)
        corto = bool(self._compacta)
        self.lb_pagina.setText((f"{self.pagina + 1} de {self.total}" if corto else
                                f"Página {self.pagina + 1} de {self.total}") if hay else "")
        self.b_prev.setEnabled(hay and self.pagina > 0)
        self.b_next.setEnabled(hay and self.pagina < self.total - 1)
        for b in (self.b_prev, self.b_next):
            b.setVisible(hay and self.total > 1)      # con una sola página no hay a dónde ir
        self.b_zona.setEnabled(hay)
        self._actualizar_leyenda()

    # --- dibujo de la página
    def _ancho(self) -> int:
        """Ancho de la página en pantalla: entera a la vista (o, con la lupa, a todo el ancho), sin bajar de
        280 px."""
        vp = self.scroll.viewport()
        if self.b_ancho.isChecked():
            return max(280, vp.width() - 14)
        w, h = self.tam
        por_alto = int((vp.height() - 12) * w / max(h, 1))
        return max(280, min(vp.width() - 14, por_alto))

    def _render(self):
        if not self.ruta:
            return
        vp = self.scroll.viewport()
        clave = (self.ruta, self.pagina, vp.width(), vp.height())
        if clave != self._clave:
            if ctx.busy or not PDF_LOCK.acquire(blocking=False):
                self._timer.start()          # hay una tarea usando el PDF: se reintenta en un momento
                return
            try:
                with core.open_pdf(self.ruta, self.password) as doc:
                    page = doc[self.pagina]
                    r = page.rect
                    self.tam = (r.width, r.height)
                    zoom = min(4.0, self._ancho() * self.devicePixelRatioF() / max(r.width, 1))
                    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
                    self.img = QImage(pix.samples, pix.width, pix.height, pix.stride,
                                      QImage.Format.Format_RGB888).copy()
            except core.UserError:
                self.img = None
            finally:
                PDF_LOCK.release()
            self._clave = clave
        ancho = self._ancho()
        self.lienzo.setFixedSize(ancho, max(1, int(ancho * self.tam[1] / max(self.tam[0], 1))))
        self.lienzo.update()

    def eventFilter(self, obj, ev):
        if obj is self.scroll.viewport() and ev.type() == QEvent.Type.Resize:
            self._timer.start()
        return False


# --------------------------------------------------------------------------
# Censurar datos
# --------------------------------------------------------------------------

def _texto_corto(h: censura.Hallazgo, ancho: int = 9) -> str:
    """«…C.C. 1.032.456.789 de Bog…» a partir del contexto guardado."""
    ctxt = h.contexto.strip("…")
    t = " ".join(h.texto.split())
    k = ctxt.find(t) if t else -1
    if k < 0:
        return h.contexto if len(h.contexto) <= 2 * ancho + 20 else h.contexto[:2 * ancho + 20] + "…"
    antes, despues = ctxt[max(0, k - ancho):k], ctxt[k + len(t):k + len(t) + ancho]
    pre = "…" if k > ancho or h.contexto.startswith("…") else ""
    post = "…" if k + len(t) + ancho < len(ctxt) or h.contexto.endswith("…") else ""
    if len(t) > 60:
        t = t[:57] + "…"
    return f"{pre}{antes}{t}{despues}{post}"


def _texto_restos(restos: list[censura.Resto]) -> list[str]:
    grupos: dict[tuple[str, str], list[str]] = defaultdict(list)
    for r in restos:
        if r.donde not in grupos[(r.tipo, r.texto)]:
            grupos[(r.tipo, r.texto)].append(r.donde)
    lineas = []
    for (tipo, texto), dondes in grupos.items():
        t = texto if len(texto) <= 50 else texto[:47] + "…"
        if tipo == "zona":
            cual = f"La {texto}" if texto in ("zona marcada a mano", "zona del recuadro") else f"La zona de «{t}»"
            lineas.append(f"{cual} en la {', '.join(dondes)} no quedó de un solo color.")
        else:
            lineas.append(f"El texto «{t}» sigue en: {', '.join(dondes)}")
    if len(lineas) > 12:
        lineas = lineas[:12] + [f"…y {len(lineas) - 12} más."]
    return lineas


def _aviso_restos(parent: QWidget, titulo: str, texto: str, detalle: str) -> bool:
    """Advertencia con la lista de restos.  Devuelve True si se quiere abrir «Quitar datos ocultos»."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle("Revisa la copia")
    box.setText(f"<b style='font-size:11pt'>{titulo}</b>")
    box.setInformativeText(texto)
    if detalle:
        box.setDetailedText(detalle)
    b_limpiar = box.addButton("Abrir «Quitar datos ocultos»", QMessageBox.ButtonRole.AcceptRole)
    box.addButton("Cerrar", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(b_limpiar)
    box.exec()
    return box.clickedButton() is b_limpiar


def _cargar_lista(path: str) -> list[str]:
    """Lee una lista de palabras (una por renglón) de un .txt."""
    data = Path(path).read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            texto = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    return [ln.strip() for ln in texto.splitlines() if ln.strip() and not ln.strip().startswith("#")]


class RedactPage(ToolPage):
    key = "redact"
    title = "Censurar datos personales"
    subtitle = "Encuentra cédulas, celulares y correos, y bórralos de verdad antes de compartir."
    action_text = "Censurar y guardar"

    def __init__(self):
        super().__init__()
        self.footer_info.setWordWrap(True)
        self.refresh()

    # ------------------------------------------------------------------ construcción
    def build(self):
        self.hallazgos: list[censura.Hallazgo] = []
        self.falsas: list[censura.Hallazgo] = []
        self.resultado: censura.ListaHallazgos | None = None
        self._items: dict[int, QTreeWidgetItem] = {}
        self._grupos: dict[str, QTreeWidgetItem] = {}
        self._por_pagina: dict[int, list[int]] | None = None
        self._construyendo = False
        self._sin_guardar = False
        self._paginas = 0
        self._activos = 0
        self._buscadas: set[str] = set()     # tipos de dato de la última búsqueda (sus fichas llevan conteo)
        self._paginas_activas: dict[int, int] = defaultdict(int)
        self._conteo: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        self._refresco = QTimer(self)
        self._refresco.setSingleShot(True)
        self._refresco.setInterval(0)
        self._refresco.timeout.connect(self._refrescar)

        self.picker = SingleFilePicker()
        self.picker.changed.connect(self._archivo)
        self.use_picker(self.picker, "Arrastra aquí el PDF que quieres censurar",
                        "o elígelo desde tu equipo. Buscas los datos, los revisas y guardas una copia censurada.")

        # avisos sobre el documento (bloque sin número, arriba del panel)
        av = self.step(None)
        self.bloque_avisos = av.parentWidget()
        self.aviso_falsas, self.b_falsas = _nota_con_boton("warn", "Agregar a la lista", self.agregar_falsas)
        self.aviso_falsas_texto = self.aviso_falsas.text_lb
        self.aviso_escaneado = Note(AVISO_ESCANEADO, "warn")
        self.aviso_ocr = Note("", "info")
        for w in (self.aviso_falsas, self.aviso_escaneado, self.aviso_ocr):
            w.hide()
            av.addWidget(w)
        self.bloque_avisos.hide()

        # 1. qué censurar: fichas con su conteo, y nombres o palabras
        g = self.step("¿Qué quieres censurar?")
        self.fichas = Choices()
        self.casillas: dict[str, QWidget] = {}
        for clave, pat in censura.PATRONES.items():
            c = self.fichas.add(FICHAS.get(clave, pat.nombre), pat.activo, clave)
            c.setToolTip(f"{pat.nombre}. Por ejemplo: {pat.ejemplo}")
            c.toggled.connect(lambda on, k=clave: self._ficha(k, on))
            self.casillas[clave] = c
        g.addWidget(self.fichas)
        fila = QHBoxLayout()
        fila.setSpacing(8)
        fila.addWidget(label("Nombres o palabras", "Label"), 1)
        b_lista = _boton("Cargar lista…", "Ghost", cb=self._elegir_lista)
        b_lista.setToolTip("Lee los nombres de un archivo de texto (.txt), uno por renglón")
        fila.addWidget(b_lista)
        caja = QVBoxLayout()
        caja.setSpacing(6)
        caja.addLayout(fila)
        self.palabras = QPlainTextEdit()
        self.palabras.setPlaceholderText("Uno por renglón. Ej.: María José Gómez")
        self.palabras.setFixedHeight(76)
        caja.addWidget(self.palabras)
        caja.addWidget(label("Se encuentran aunque cambien las tildes o las mayúsculas; una cédula, con o sin "
                             "puntos.", "Help", wrap=True))
        g.addLayout(caja)

        # 2. cómo se ve lo censurado
        r2 = self.step("¿Cómo se ve lo censurado?")
        self.relleno = Segmented(RELLENOS, full=True)
        self.relleno.changed.connect(self._relleno_cambiado)
        r2.addWidget(self.relleno)

        # 3. revisar lo encontrado (la lista por tipo, con sus casillas) y las opciones avanzadas
        r = self.step("Revisa lo encontrado")
        self.tarjeta_resultados = r.parentWidget()
        self.chip_total = Chip("", "cat")
        self.chip_total.hide()
        self.tarjeta_resultados.add_head_widget(self.chip_total)
        self.resumen = label(ANTES_DE_BUSCAR, "Help", wrap=True)
        r.addWidget(self.resumen)
        self.arbol = QTreeWidget()
        self.arbol.setColumnCount(2)
        self.arbol.setHeaderHidden(True)
        self.arbol.setUniformRowHeights(True)
        self.arbol.setIndentation(14)
        self.arbol.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.arbol.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.arbol.header().setStretchLastSection(False)
        self.arbol.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.arbol.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.arbol.itemChanged.connect(self._item_cambiado)
        self.arbol.currentItemChanged.connect(self._item_actual)
        self.arbol.itemExpanded.connect(lambda *_: self._alto_arbol())
        self.arbol.itemCollapsed.connect(lambda *_: self._alto_arbol())
        self.arbol.hide()
        r.addWidget(self.arbol)

        self.b_avanzadas = _boton_avanzadas(self._avanzadas)
        r.addWidget(self.b_avanzadas, 0, Qt.AlignmentFlag.AlignLeft)
        self.panel_avanzado = QWidget()
        fa = QVBoxLayout(self.panel_avanzado)
        fa.setContentsMargins(0, 0, 0, 0)
        fa.setSpacing(6)
        fa.addWidget(label("Expresión regular", "Label"))
        self.regex = QLineEdit()
        self.regex.setPlaceholderText(r"Ej.: Radicado \d{6}")
        self.regex.textChanged.connect(lambda: self.regex_error.hide())
        fa.addWidget(self.regex)
        self.regex_error = label("", "Help", wrap=True)
        self.regex_error.setStyleSheet(f"color: {tema.color('danger')};")
        self.regex_error.hide()
        fa.addWidget(self.regex_error)
        self.mayusculas = QCheckBox("Distinguir mayúsculas y tildes")
        self.completa = QCheckBox("Solo palabras completas")
        fa.addWidget(self.mayusculas)
        fa.addWidget(self.completa)
        fa.addSpacing(6)
        fa.addWidget(label("Páginas", "Label"))
        self.paginas = pages_edit("Vacío = todas. Ej.: 1-3, 5")
        fa.addWidget(self.paginas)
        fa.addSpacing(4)
        self.usar_ocr = QCheckBox("Usar OCR en páginas escaneadas")
        self.usar_ocr.setToolTip("Busca también en las páginas escaneadas, con reconocimiento de texto (OCR)")
        self.usar_ocr.setChecked(True)
        fa.addWidget(self.usar_ocr)
        self.panel_avanzado.hide()
        r.addWidget(self.panel_avanzado)
        self.tarjeta_resultados.hide()

        # barra de acción: «Buscar datos» al lado del principal
        self.b_buscar = self.add_secondary("Buscar datos", self.buscar_ahora)
        self.b_buscar.setIcon(icon("search", tema.color("ink-2"), size=18))

        # área principal: la página con las marcas
        self.vista = VistaPagina(self._marcas_de)
        self.vista.marca_clic.connect(self._marca_clic)
        self.vista.zona_nueva.connect(self.agregar_zona)
        self.set_main(self.vista)

        if not core.ocr_languages():
            self.usar_ocr.setChecked(False)
            self.usar_ocr.setEnabled(False)
            self.usar_ocr.hide()                 # sin modelos de OCR la opción no aplica
            self.aviso_ocr.setText("No se encontraron los modelos de reconocimiento de texto (OCR): en los "
                                   "documentos escaneados solo se buscará en el texto que el PDF ya tenga.")

    def _avisos(self):
        """El bloque de avisos se ve solo si alguno de sus avisos está a la vista."""
        hay = any(not w.isHidden() for w in (self.aviso_falsas, self.aviso_escaneado, self.aviso_ocr))
        self.bloque_avisos.setVisible(hay)

    def _avanzadas(self, on: bool):
        self.b_avanzadas.setIcon(icon("up_chevron" if on else "down_chevron", tema.color("ink-2"), size=16))
        self.panel_avanzado.setVisible(on)

    def _relleno_cambiado(self, _k, valor):
        self.vista.set_relleno(valor)
        if self.picker.path:
            self.vista.set_despues(True)          # se ve enseguida cómo quedará

    def _elegir_lista(self):
        paths = ask_open(self, False, "Texto (*.txt)", "Elige la lista de palabras")
        if not paths:
            return
        try:
            nuevas = _cargar_lista(paths[0])
        except OSError as exc:
            QMessageBox.warning(self, "Cargar lista", f"No se pudo leer el archivo: {exc}")
            return
        actuales = [ln for ln in self.palabras.toPlainText().splitlines() if ln.strip()]
        self.palabras.setPlainText("\n".join(dict.fromkeys(actuales + nuevas)))

    # ------------------------------------------------------------------ archivo
    def accept_files(self, paths):
        self.picker.set_path(paths[0])

    def _archivo(self, path):
        self.hallazgos, self.falsas, self.resultado = [], [], None
        self._sin_guardar = False
        self._buscadas = set()
        self._poblar()
        self.aviso_falsas.hide()
        self.aviso_escaneado.hide()
        self.aviso_ocr.setVisible(not core.ocr_languages() and bool(path))
        self.tarjeta_resultados.setVisible(bool(path))
        self.resumen.setText(ANTES_DE_BUSCAR)
        if not path:
            self._paginas = 0
            self.vista.cargar(None, None, 0)
            self._avisos()
            self.refresh()
            return
        pw = self.picker.password
        info = self.picker.info
        self._paginas = info.pages if info else 0
        try:
            with PDF_LOCK, core.open_pdf(path, pw) as doc:
                self._paginas = doc.page_count
                escaneado = core.looks_scanned_without_text(doc)
        except core.UserError:
            escaneado = False
        self.aviso_escaneado.setVisible(escaneado)
        self._avisos()
        self.vista.cargar(path, pw, self._paginas)
        self.vista.set_relleno(self.relleno.value())
        self.refresh()
        ctx.run_task("Revisando el documento…", _auditar_seguro, self._auditoria_lista, path=path, password=pw)

    def _auditoria_lista(self, falsas):
        self.falsas = list(falsas or [])
        n = len(self.falsas)
        if n and any(h.texto for h in self.falsas):
            self.aviso_falsas_texto.setText(
                f"Este PDF tiene {_s(n, 'recuadro que tapa', 'recuadros que tapan')} texto sin borrarlo: lo que "
                f"hay debajo todavía se puede copiar.")
        elif n:
            self.aviso_falsas_texto.setText(
                f"Este PDF tiene {_s(n, 'recuadro que tapa', 'recuadros que tapan')} parte de la imagen escaneada "
                f"sin borrarla: lo que hay debajo todavía se puede recuperar.")
        self.aviso_falsas.setVisible(bool(n))
        self._avisos()

    def agregar_falsas(self):
        ya = {(h.pagina, tuple(h.rect)) for h in self.hallazgos if h.categoria == "falsa"}
        nuevas = [dataclasses.replace(h) for h in self.falsas if (h.pagina, tuple(h.rect)) not in ya]
        self.hallazgos.extend(nuevas)
        self.aviso_falsas.hide()
        self._avisos()
        if nuevas:
            self._sin_guardar = True
            self._poblar(seleccionar=len(self.hallazgos) - len(nuevas))

    # ------------------------------------------------------------------ búsqueda
    def buscar_ahora(self):
        path = self.picker.path
        if not path or ctx.busy:
            return
        patrones = [k for k, cb in self.casillas.items() if cb.isChecked()]
        palabras = [ln.strip() for ln in self.palabras.toPlainText().splitlines() if ln.strip()]
        rx = self.regex.text().strip()
        if rx:
            try:
                censura.compilar_regex(rx, self.mayusculas.isChecked())
            except core.UserError as exc:
                self.regex_error.setText(str(exc))
                self.regex_error.show()
                self.b_avanzadas.setChecked(True)
                return
        if not patrones and not palabras and not rx:
            self.resumen.setText("Elige al menos un tipo de dato o escribe una palabra para buscar.")
            return
        spec = self.paginas.text()
        if not check_spec(self, spec, self._paginas or None):
            return
        buscadas = set(patrones)

        def lista(res):
            self._buscadas = buscadas
            self._busqueda_lista(res)

        ctx.run_task("Buscando datos personales…", censura.buscar, lista, path=path,
                     patrones=patrones, palabras=palabras, regex=[rx] if rx else [], paginas=spec,
                     mayusculas=self.mayusculas.isChecked(), palabra_completa=self.completa.isChecked(),
                     usar_ocr=self.usar_ocr.isChecked() and self.usar_ocr.isEnabled(),
                     password=self.picker.password)

    def _busqueda_lista(self, res: censura.ListaHallazgos):
        conservar = [h for h in self.hallazgos if h.categoria in ("manual", "falsa")]
        self.resultado = res
        self.hallazgos = list(res) + conservar
        self._sin_guardar = bool(self.hallazgos)
        if res.escaneadas:
            self.aviso_escaneado.show()
        avisos = list(res.avisos)
        if not core.ocr_languages() and res.escaneadas:
            avisos = avisos or [self.aviso_ocr.text()]
        self.aviso_ocr.setText(" ".join(avisos))
        self.aviso_ocr.setVisible(bool(avisos))
        self._avisos()
        if not res:
            self.resumen.setText(SIN_RESULTADOS)
        else:
            paginas = len({h.pagina for h in res})
            ocr = (" Los marcados «OCR: revisa» vienen de páginas escaneadas: confirma que la marca cae sobre el "
                   "dato." if any(h.origen == "ocr" for h in res) else "")
            self.resumen.setText(f"Se encontraron {_s(len(res), 'dato', 'datos')} en "
                                 f"{_s(paginas, 'página', 'páginas')}. Desmarca lo que no se debe censurar.{ocr}")
        self.vista.set_despues(False)
        self._poblar(seleccionar=0 if res else None)

    def _ficha(self, clave: str, on: bool):
        """Una ficha de un tipo ya buscado incluye o deja todos sus hallazgos; la de un tipo nuevo pide buscar."""
        if clave in self._buscadas:
            cambio = False
            self._construyendo = True
            for i, h in enumerate(self.hallazgos):
                if h.categoria == clave and self._cambiar(i, on):
                    it = self._items.get(i)
                    if it is not None:
                        it.setCheckState(0, Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
                    cambio = True
            self._construyendo = False
            if cambio:
                self._refrescar()
        elif on and self.resultado is not None:
            self.resumen.setText(f"Pulsa «Buscar datos» para buscar también: {FICHAS.get(clave, clave).lower()}.")

    # ------------------------------------------------------------------ lista y vista
    def _marcas_de(self, pagina: int) -> list:
        if self._por_pagina is None:
            self._por_pagina = defaultdict(list)
            for i, h in enumerate(self.hallazgos):
                self._por_pagina[h.pagina].append(i)
        return [(i, self.hallazgos[i].rect, self.hallazgos[i].incluir, self.hallazgos[i].origen)
                for i in self._por_pagina.get(pagina, ())]

    def _poblar(self, seleccionar: int | None = None):
        self._construyendo = True
        self._por_pagina = None
        self.arbol.setUpdatesEnabled(False)
        self.arbol.clear()
        self._items.clear()
        self._grupos.clear()
        por_cat: dict[str, list[int]] = defaultdict(list)
        for i, h in enumerate(self.hallazgos):
            por_cat[h.categoria].append(i)
        for cat in ORDEN_GRUPOS:
            idxs = por_cat.get(cat)
            if not idxs:
                continue
            grupo = QTreeWidgetItem([GRUPOS.get(cat, censura.NOMBRES_CATEGORIA.get(cat, cat)), ""])
            grupo.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
                           | Qt.ItemFlag.ItemIsAutoTristate | Qt.ItemFlag.ItemIsSelectable)
            grupo.setToolTip(0, censura.NOMBRES_CATEGORIA.get(cat, cat))
            f = grupo.font(0)
            f.setBold(True)
            grupo.setFont(0, f)
            self.arbol.addTopLevelItem(grupo)
            self._grupos[cat] = grupo
            hijos = []
            for i in idxs:
                h = self.hallazgos[i]
                if h.categoria == "manual":
                    texto = f"pág. {h.pagina + 1} · Zona marcada a mano"
                elif h.categoria == "falsa":
                    texto = f"pág. {h.pagina + 1} · {h.contexto}"
                else:
                    texto = f"pág. {h.pagina + 1} · {_texto_corto(h)}"
                etiqueta = "OCR: revisa" if h.origen == "ocr" else ""
                it = QTreeWidgetItem([texto, etiqueta])
                it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable)
                it.setData(0, Qt.ItemDataRole.UserRole, i)
                it.setToolTip(0, h.contexto or h.texto)
                if etiqueta:
                    it.setForeground(1, _color("warn"))
                    it.setToolTip(1, "Encontrado con reconocimiento de texto: revisa que la marca esté en el lugar "
                                     "correcto.")
                hijos.append(it)
                self._items[i] = it
            grupo.addChildren(hijos)
            for i, it in zip(idxs, hijos):
                it.setCheckState(0, Qt.CheckState.Checked if self.hallazgos[i].incluir else Qt.CheckState.Unchecked)
            grupo.setExpanded(len(idxs) <= 300)
        self._recontar()
        self.arbol.setUpdatesEnabled(True)
        self._construyendo = False
        self.arbol.setVisible(bool(self.hallazgos))
        self._alto_arbol()
        if seleccionar is not None and seleccionar in self._items:
            self.arbol.setCurrentItem(self._items[seleccionar])
        self._refrescar()

    def _alto_arbol(self):
        """La lista crece con sus renglones hasta 300 px (después se desplaza sola): el panel ya se desplaza."""
        filas = 0
        for k in range(self.arbol.topLevelItemCount()):
            g = self.arbol.topLevelItem(k)
            filas += 1 + (g.childCount() if g.isExpanded() else 0)
        alto = self.arbol.sizeHintForRow(0) if filas else 30
        self.arbol.setFixedHeight(max(60, min(300, filas * max(alto, 24) + 12)))

    # Contadores incrementales: con miles de hallazgos, marcar o desmarcar uno no recorre toda la lista.
    def _recontar(self):
        self._activos = 0
        self._paginas_activas: dict[int, int] = defaultdict(int)
        self._conteo: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for h in self.hallazgos:
            c = self._conteo[h.categoria]
            c[1] += 1
            if h.incluir:
                c[0] += 1
                self._activos += 1
                self._paginas_activas[h.pagina] += 1
        for cat in self._grupos:
            self._actualizar_grupo(cat)
        for clave, ficha in self.casillas.items():
            ficha.set_count(self._conteo[clave][1] if clave in self._buscadas else None)
        self.fichas.flow.invalidate()
        self.fichas.updateGeometry()

    def _actualizar_grupo(self, cat: str):
        grupo = self._grupos.get(cat)
        if grupo is None:
            return
        incl, n = self._conteo[cat]
        nombre = GRUPOS.get(cat, censura.NOMBRES_CATEGORIA.get(cat, cat))
        grupo.setText(0, f"{nombre} ({n})" if incl == n else f"{nombre} ({incl} de {n})")

    def _cambiar(self, i: int, nuevo: bool) -> bool:
        h = self.hallazgos[i]
        if h.incluir == nuevo:
            return False
        h.incluir = nuevo
        d = 1 if nuevo else -1
        self._activos += d
        self._paginas_activas[h.pagina] += d
        if self._paginas_activas[h.pagina] <= 0:
            del self._paginas_activas[h.pagina]
        self._conteo[h.categoria][0] += d
        self._actualizar_grupo(h.categoria)
        self._sin_guardar = True
        return True

    def _item_cambiado(self, item: QTreeWidgetItem, col: int):
        if self._construyendo or col != 0:
            return
        i = item.data(0, Qt.ItemDataRole.UserRole)
        if i is not None and self._cambiar(i, item.checkState(0) == Qt.CheckState.Checked):
            self._refresco.start()

    def _item_actual(self, actual, _anterior):
        if actual is None:
            return
        i = actual.data(0, Qt.ItemDataRole.UserRole)
        if i is not None:
            self.vista.mostrar(self.hallazgos[i].pagina, seleccion=i)

    def _marca_clic(self, i: int):
        self.set_incluir(i, not self.hallazgos[i].incluir)
        it = self._items.get(i)
        if it is not None:
            self._construyendo = True
            self.arbol.setCurrentItem(it)
            self._construyendo = False
            self.arbol.scrollToItem(it)
        self.vista.seleccion = i
        self.vista.lienzo.update()

    def set_incluir(self, indice: int, valor: bool):
        self._cambiar(indice, bool(valor))
        it = self._items.get(indice)
        if it is not None:
            it.setCheckState(0, Qt.CheckState.Checked if valor else Qt.CheckState.Unchecked)
        self._refrescar()

    def agregar_zona(self, pagina: int, rect):
        r = tuple(float(v) for v in rect)
        self.hallazgos.append(censura.Hallazgo(pagina=int(pagina), rect=r, texto="", categoria="manual",
                                               origen="manual", contexto="Zona marcada a mano"))
        self._sin_guardar = True
        self._poblar(seleccionar=len(self.hallazgos) - 1)

    def _refrescar(self):
        self.vista.lienzo.update()
        self.refresh()

    # ------------------------------------------------------------------ guardar
    def resumen_total(self) -> str:
        """«6 datos en 1 página» (lo que se va a censurar), o vacío."""
        if not getattr(self, "_activos", 0):
            return ""
        return f"{_s(self._activos, 'dato', 'datos')} en {_s(len(self._paginas_activas), 'página', 'páginas')}"

    def refresh(self):
        super().refresh()
        if not hasattr(self, "_activos") or not hasattr(self, "b_buscar"):
            return
        hay = bool(self.picker.path)
        self.b_buscar.setVisible(hay)
        self.chip_total.setVisible(bool(self._activos))
        self.chip_total.setText(_s(self._activos, "dato", "datos"))
        if not hay:
            self.set_summary([], "Abre o arrastra el PDF que quieres censurar.")
        elif self._activos:
            self.set_summary([(self.resumen_total(), "cat")], NOTA_ORIGINAL)
        else:
            self.set_summary([], "Elige qué censurar y pulsa «Buscar datos», o marca zonas a mano sobre la página.")

    def ready(self) -> bool:
        return bool(getattr(self, "picker", None) and self.picker.path and getattr(self, "_activos", 0))

    def has_unsaved(self) -> bool:
        return bool(getattr(self, "_sin_guardar", False) and getattr(self, "_activos", 0))

    def run(self):
        self.guardar()

    def guardar(self, al_terminar=None):
        """Censura y guarda la copia.  ``al_terminar(ok)``, si se da, avisa cuando termina: lo usa la pregunta
        de «Cambios sin guardar» al cerrar la aplicación."""
        hecho: dict = {}

        def fin(ok: bool):
            if callable(al_terminar):
                al_terminar(bool(ok))

        path = self.picker.path
        zonas = [dataclasses.replace(h) for h in self.hallazgos]
        if not path or not any(z.incluir for z in zonas) or ctx.busy:
            return fin(False)
        out = ask_save(self, suggest_output(path, "_censurado"))
        if not out:
            return fin(False)

        def listo(res):
            hecho["ok"] = True
            self._listo(res, out)
            fin(True)

        def finalmente():
            QTimer.singleShot(0, lambda: None if hecho.get("ok") else fin(False))

        ctx.run_task("Censurando y verificando…", censura.censurar, listo, on_finally=finalmente, path=path,
                     output=out, zonas=zonas, color=self.relleno.value(), password=self.picker.password)

    def _listo(self, res: censura.ResultadoCensura, out: str):
        self._sin_guardar = False
        datos = _s(res.zonas, "dato", "datos")
        paginas = _s(res.paginas, "página", "páginas")
        if not res.restos:
            show_done(self, "Documento censurado",
                      f"Se borraron {datos} en {paginas}. Verificación: ninguno de esos textos sigue en el archivo.",
                      file=out)
            return
        lineas = _texto_restos(res.restos)
        texto = ("La verificación encontró datos que siguen en la copia:<br><br>"
                 + "<br>".join(_escapar(ln) for ln in lineas[:6])
                 + ("<br>…" if len(lineas) > 6 else "")
                 + "<br><br>Pasa la copia por «Quitar datos ocultos» para eliminar metadatos, comentarios y "
                   "campos de formulario, y revisa las páginas indicadas antes de compartirla.")
        if _aviso_restos(self, f"Se borraron {datos} en {paginas}, pero quedaron restos", texto, "\n".join(lineas)):
            extensiones.ir_a("sanitize", [out])


def _escapar(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _auditar_seguro(progress, path: str, password: str | None):
    """La auditoría al abrir un archivo nunca debe molestar: si falla, simplemente no avisa."""
    try:
        return censura.auditar(path, password, progress)
    except core.UserError:
        return []


# --------------------------------------------------------------------------
# Quitar datos ocultos
# --------------------------------------------------------------------------

def _pintar_casilla(p: QPainter, r: QRectF, marcada: bool, cat: str | None) -> None:
    """La casilla del sistema (``rp-check``): 18 × 18, radio 6, llena en el color de la categoría."""
    t = tema.tokens()
    ton = tema.tonos(cat)
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if marcada:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(ton["c"]))
        p.drawRoundedRect(r, 5, 5)
        p.drawPixmap(QPointF(r.center().x() - 7, r.center().y() - 7), pixmap("check", t["on-cat"], 14, 2.4))
    else:
        p.setPen(QPen(QColor(t["line-control"]), 1))
        p.setBrush(QColor(t["field"]))
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 5, 5)
    p.restore()


class _TarjetaOculto(QStyledItemDelegate):
    """Cada dato oculto como tarjeta: casilla, nombre, cantidad y el detalle en una línea.  Un clic en
    cualquier parte de la tarjeta la marca o la desmarca."""

    ALTO = 70

    def __init__(self, tabla: QTreeWidget):
        super().__init__(tabla)
        self.tabla = tabla

    def sizeHint(self, option, index):  # noqa: N802
        return QSize(max(200, option.rect.width()), self.ALTO)

    def createEditor(self, *args):  # noqa: N802
        return None

    def paint(self, p, option, index):
        it = self.tabla.itemFromIndex(index)
        if it is None:
            return
        t = tema.tokens()
        cat = cat_de(self.tabla)
        ton = tema.tonos(cat)
        marcada = it.checkState(0) == Qt.CheckState.Checked
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        r = QRectF(option.rect).adjusted(0.5, 4.5, -0.5, -4.5)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(ton["c"] if marcada else t["line-strong"] if hover else t["line"]),
                      1.5 if marcada else 1))
        p.setBrush(QColor(ton["band"] if marcada else t["surface"]))
        p.drawRoundedRect(r, 12, 12)
        _pintar_casilla(p, QRectF(r.left() + 16, r.top() + 14, 18, 18), marcada, cat)
        x = r.left() + 48
        fc = tema.fuente("chip")
        cant = it.text(1)
        cw = QFontMetrics(fc).horizontalAdvance(cant) + 20
        cr = QRectF(r.right() - 14 - cw, r.top() + 11, cw, 24)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(t["surface"] if marcada else t["surface-sunken"]))
        p.drawRoundedRect(cr, 12, 12)
        p.setFont(fc)
        p.setPen(QColor(t["ink-2"]))
        p.drawText(cr, Qt.AlignmentFlag.AlignCenter, cant)
        ancho = max(40.0, cr.left() - 12 - x)
        fn = tema.fuente("body-strong")
        p.setFont(fn)
        p.setPen(QColor(t["ink"]))
        p.drawText(QRectF(x, r.top() + 12, ancho, 22), Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(fn).elidedText(it.text(0), Qt.TextElideMode.ElideRight, int(ancho)))
        fm = tema.fuente("body-sm")
        p.setFont(fm)
        p.setPen(QColor(t["ink-2"]))
        det = it.text(2) or ("Recomendado quitarlo" if it.data(0, Qt.ItemDataRole.UserRole + 1) else "")
        ancho_det = max(40.0, r.right() - 16 - x)
        p.drawText(QRectF(x, r.top() + 34, ancho_det, 20), Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(fm).elidedText(det, Qt.TextElideMode.ElideRight, int(ancho_det)))
        if option.state & QStyle.StateFlag.State_HasFocus and self.tabla.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush)              # con el teclado: Espacio marca o desmarca
            p.setPen(QPen(QColor(t["focus"]), 2))
            p.drawRoundedRect(r.adjusted(-1, -1, 1, 1), 13, 13)
        p.restore()

    def editorEvent(self, event, model, option, index):  # noqa: N802
        if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            it = self.tabla.itemFromIndex(index)
            if it is not None:
                it.setCheckState(0, Qt.CheckState.Unchecked if it.checkState(0) == Qt.CheckState.Checked
                                 else Qt.CheckState.Checked)
                return True
        if event.type() == QEvent.Type.MouseButtonDblClick:
            return True
        return super().editorEvent(event, model, option, index)


class SanitizePage(ToolPage):
    key = "sanitize"
    title = "Quitar datos ocultos"
    subtitle = "Quita lo que viaja oculto en el PDF: autor, comentarios y adjuntos."
    action_text = "Limpiar y guardar"

    MODOS = [("recomendado", "Lo recomendado", "Autor, comentarios, adjuntos y más. Conserva el texto del OCR."),
             ("todo", "Todo lo que se encontró", "Incluye el texto invisible del OCR: el PDF dejará de ser "
                                                 "buscable."),
             ("elegir", "Elegir uno por uno", "Marca a la derecha lo que quieres quitar.")]

    def build(self):
        self.datos: list[censura.DatoOculto] = []
        self._cargando = False
        self.picker = SingleFilePicker()
        self.picker.changed.connect(self._archivo)
        self.use_picker(self.picker, "Arrastra aquí el PDF que quieres limpiar",
                        "o elígelo desde tu equipo. Verás todo lo que viaja dentro de él antes de quitarlo.")

        g = self.step("¿Qué quieres quitar?")
        self.modo = OptionGroup()
        for clave, titulo, ayuda in self.MODOS:
            self.modo.add(titulo, ayuda, clave)
        self.modo.changed.connect(self._modo)
        g.addWidget(self.modo)
        r = self.step("Revisa la lista", "Desmarca a la derecha lo que quieras conservar.")
        self.nota_ocr = Note(f"«Texto invisible»: {censura.NOTA_TEXTO_INVISIBLE}", "info")
        self.nota_ocr.hide()
        r.addWidget(self.nota_ocr)

        # área principal: lo que viaja dentro del PDF, como tarjetas
        self.tarjeta = QWidget()
        lay = QVBoxLayout(self.tarjeta)
        lay.setContentsMargins(16, 14, 16, 8)
        lay.setSpacing(6)
        self.titulo_lista = label("Lo que viaja dentro de este PDF", "StepTitle", wrap=True)
        lay.addWidget(self.titulo_lista)
        self.tabla = QTreeWidget()
        self.tabla.setObjectName("FileCards")
        self.tabla.setColumnCount(3)
        self.tabla.setHeaderLabels(["Qué", "Cuántos", "Detalle"])
        self.tabla.setHeaderHidden(True)
        self.tabla.setRootIsDecorated(False)
        self.tabla.setIndentation(0)
        self.tabla.setColumnHidden(1, True)
        self.tabla.setColumnHidden(2, True)
        self.tabla.setMouseTracking(True)
        self.tabla.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.tabla.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tabla.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.tabla.setItemDelegate(_TarjetaOculto(self.tabla))
        self.tabla.itemChanged.connect(self._marcas_cambiaron)
        lay.addWidget(self.tabla, 1)
        self.vacio_caja = QWidget()
        vl = QVBoxLayout(self.vacio_caja)
        vl.addStretch(1)
        vl.addWidget(GlyphTile("sanitize", tema.TILE_XL, soft=True), 0, Qt.AlignmentFlag.AlignHCenter)
        vl.addSpacing(8)
        self.vacio = label("Este PDF no tiene datos ocultos.", "DropTitle")
        self.vacio.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vl.addWidget(self.vacio)
        h = label("No hay nada que quitar: puedes compartirlo como está.", "Hint", wrap=True)
        h.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vl.addWidget(h)
        vl.addStretch(2)
        self.vacio_caja.hide()
        lay.addWidget(self.vacio_caja, 1)
        self.set_main(self.tarjeta)

    def accept_files(self, paths):
        self.picker.set_path(paths[0])

    def _archivo(self, path):
        self.datos = []
        self._llenar([], analizado=False)
        if path:
            ctx.run_task("Revisando el documento…", censura.datos_ocultos, self._mostrar, path=path,
                         password=self.picker.password)
        self.refresh()

    def _mostrar(self, datos):
        self.datos = list(datos)
        self._llenar(self.datos, analizado=True)
        self.refresh()

    def _llenar(self, datos, analizado: bool = True):
        self._cargando = True
        self.tabla.clear()
        modo = self.modo.value()
        for d in datos:
            it = QTreeWidgetItem([d.nombre, str(d.cantidad), d.detalle])
            it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            marcar = d.recomendado if modo != "todo" else True
            it.setCheckState(0, Qt.CheckState.Checked if marcar else Qt.CheckState.Unchecked)
            it.setData(0, Qt.ItemDataRole.UserRole, d.clave)
            it.setData(0, Qt.ItemDataRole.UserRole + 1, d.recomendado)
            it.setToolTip(0, f"{d.nombre}: {d.detalle}" if d.detalle else d.nombre)
            self.tabla.addTopLevelItem(it)
        self._cargando = False
        self.tabla.setVisible(bool(datos))
        self.vacio_caja.setVisible(analizado and bool(self.picker.path) and not datos)
        self.titulo_lista.setVisible(bool(datos))
        self.nota_ocr.setVisible(any(d.clave == "texto_invisible" for d in datos))

    def _marcar(self, regla, modo: str | None = None):
        self._cargando = True
        for k in range(self.tabla.topLevelItemCount()):
            it = self.tabla.topLevelItem(k)
            d = next((x for x in self.datos if x.clave == it.data(0, Qt.ItemDataRole.UserRole)), None)
            it.setCheckState(0, Qt.CheckState.Checked if d is not None and regla(d) else Qt.CheckState.Unchecked)
        self._cargando = False
        self.tabla.viewport().update()
        self._marcas_cambiaron(modo=modo or "elegir")

    def _modo(self, valor):
        if self._cargando:
            return
        if valor == "recomendado":
            self._marcar(lambda d: d.recomendado, "recomendado")
        elif valor == "todo":
            self._marcar(lambda d: True, "todo")

    def _marcas_cambiaron(self, *_, modo: str | None = None):
        if self._cargando:
            return
        if modo is None:
            modo = "elegir"
        if self.modo.value() != modo:
            self._cargando = True
            self.modo.set_value(modo)
            self._cargando = False
        self.refresh()

    def categorias(self) -> list[str]:
        out = []
        for k in range(self.tabla.topLevelItemCount()):
            it = self.tabla.topLevelItem(k)
            if it.checkState(0) == Qt.CheckState.Checked:
                out.append(it.data(0, Qt.ItemDataRole.UserRole))
        return out

    def ready(self) -> bool:
        return bool(getattr(self, "picker", None) and self.picker.path and self.categorias())

    def refresh(self):
        super().refresh()
        if not hasattr(self, "tabla"):
            return
        n = len(self.categorias())
        if not self.picker.path:
            self.set_summary([], "Abre o arrastra el PDF que quieres limpiar.")
        elif n:
            self.set_summary([(f"Se quitan {_s(n, 'tipo de dato', 'tipos de datos')}", "cat")],
                             "Se guarda una copia limpia. El original no cambia.")
        elif self.datos:
            self.set_summary([], "Marca al menos un tipo de dato para quitarlo.")
        else:
            self.set_summary([], "")

    def run(self):
        path = self.picker.path
        cats = self.categorias()
        if not path or not cats:
            return
        out = ask_save(self, suggest_output(path, "_limpio"))
        if not out:
            return
        nombres = {d.clave: d.nombre for d in self.datos}

        def listo(hecho):
            if hecho:
                partes = [f"{nombres.get(k, k)} ({n})" for k, n in hecho.items()]
                detalle = "Se quitó: " + "; ".join(partes) + "."
            else:
                detalle = "No había nada que quitar en lo que elegiste."
            show_done(self, "Datos ocultos eliminados", detalle, file=out)

        ctx.run_task("Limpiando el documento…", censura.limpiar, listo, path=path, output=out, categorias=cats,
                     password=self.picker.password)


# --------------------------------------------------------------------------
# Visor y registro
# --------------------------------------------------------------------------

def _censurar_desde_visor(visor):
    path = getattr(getattr(visor, "canvas", None), "path", None)
    if not path:
        return
    if callable(getattr(visor, "has_unsaved", None)) and visor.has_unsaved():
        QMessageBox.information(visor, "Censurar datos", "Primero guarda los cambios del documento: la censura se "
                                "hace sobre el archivo guardado.")
        return
    extensiones.ir_a("redact", [path])


ACCIONES_VISOR = [dict(icono="redact", ayuda="Censurar datos personales de este PDF", accion=_censurar_desde_visor)]

REGISTRO = [
    dict(clave="redact", titulo="Censurar datos", seccion="seguridad", orden=20, pagina=RedactPage,
         dibujo=dibujo_censurar, inicio="Borra cédulas y datos personales"),
    dict(clave="sanitize", titulo="Quitar datos ocultos", seccion="seguridad", orden=21, pagina=SanitizePage,
         dibujo=dibujo_limpiar),
]
