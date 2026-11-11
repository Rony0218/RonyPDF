"""Pantalla «Comparar dos PDF» (sección Ver) y la acción «Comparar con otro PDF…» del visor.

La lógica vive en ``comparar.py``; aquí solo va la interfaz:

* **Preparación** (plantilla 2.0): «Versión anterior» y «Versión nueva» como fichas en el encabezado, con
  «Intercambiar» entre ellas; en el panel, qué comparar y qué no debe contar; en el área principal, la
  primera página de cada versión (o la zona para soltarla).  Principal: «Comparar PDF».
* **Resultado:** el panel se recoge; arriba el resumen, los avisos, las fichas con las diferencias
  encontradas (filtran por tipo), «Anterior» / «Siguiente» (F7 y Mayús+F7) y el zoom; abajo la lista de
  cambios y las dos versiones enfrentadas, emparejadas por contenido: cada par de páginas ocupa la misma
  franja en los dos lados.  Principal: «Guardar informe»; secundario: «Nueva comparación».

Se registra con ``REGISTRO`` (ver ``extensiones.py``).
"""

from __future__ import annotations

import math
import os
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QImage, QKeySequence, QPainter, QPen, QPixmap, \
    QShortcut
from PySide6.QtWidgets import (QAbstractScrollArea, QCheckBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton,
                               QSizePolicy, QSplitter, QStackedWidget, QToolButton, QToolTip, QVBoxLayout,
                               QWidget)

from . import comparar, core, extensiones, icons, tema, widgets
from .comparar import Cambio, Opciones, Resultado
from .icons import icon
from .widgets import (PDF_LOCK, ChoiceChip, FlowLayout, GlyphTile, Note, PdfThumb, SingleFilePicker, SwitchRow,
                      ToolPage, cat_de, ctx, label, qimage_from_render, suggest_output)

ANCHO_ESTRECHO = 820            # por debajo, la lista se angosta y aparece «Ver solo la versión nueva»
MAX_PIXELES = 12_000_000        # tope por página dibujada (como el visor)
NOMBRES_FILTRO = {
    "agregado": "Agregados", "quitado": "Quitados", "cambiado": "Cambiados", "apariencia": "Apariencia",
    "pagina_agregada": "Páginas agregadas", "pagina_quitada": "Páginas quitadas", "pagina_movida": "Páginas movidas",
}
MISMO_ARCHIVO = "Elegiste el mismo archivo dos veces: elige la otra versión."
AVISO_INFORME = "El informe muestra partes de los dos documentos."


# --------------------------------------------------------------------------
# Íconos (cuadrícula de 24 × 24, de línea, con un detalle rojo)
# --------------------------------------------------------------------------

def _hoja(p: QPainter, x: float, y: float, w: float, h: float, doblez: float = 3.2) -> None:
    p.drawPolygon([QPointF(x, y), QPointF(x + w - doblez, y), QPointF(x + w, y + doblez), QPointF(x + w, y + h),
                   QPointF(x, y + h)])
    p.drawPolyline([QPointF(x + w - doblez, y), QPointF(x + w - doblez, y + doblez), QPointF(x + w, y + doblez)])


def dibujo_comparar(p: QPainter, base: QColor, acento: QColor) -> None:
    """Dos hojas lado a lado; en la de la derecha, el renglón que cambió va en rojo."""
    _hoja(p, 2.5, 4.0, 8.6, 15.5)
    _hoja(p, 12.9, 4.0, 8.6, 15.5)
    for y in (9.0, 12.0, 15.0):
        p.drawLine(QPointF(4.6, y), QPointF(9.0, y))
    for y in (9.0, 15.0):
        p.drawLine(QPointF(15.0, y), QPointF(19.4, y))
    p.save()
    extensiones.lapiz(p, acento)
    pen = QPen(p.pen())
    pen.setWidthF(pen.widthF() * 1.5)
    p.setPen(pen)
    p.drawLine(QPointF(15.0, 12.0), QPointF(19.4, 12.0))
    p.restore()


def _dibujo_intercambiar(p: QPainter, base: QColor, _acento: QColor) -> None:
    for y, x0, x1 in ((8.5, 5.0, 19.0), (15.5, 19.0, 5.0)):
        p.drawLine(QPointF(x0, y), QPointF(x1, y))
        d = 3.0 if x1 > x0 else -3.0
        p.drawPolyline([QPointF(x1 - d, y - 3.0), QPointF(x1, y), QPointF(x1 - d, y + 3.0)])


icons.EXTRA.setdefault("intercambiar", _dibujo_intercambiar)


def _punto(color: str, d: int = 10) -> QIcon:
    pm = QPixmap(d * 2, d * 2)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(QRectF(d * 0.4, d * 0.4, d * 1.2, d * 1.2))
    p.end()
    pm.setDevicePixelRatio(2)
    return QIcon(pm)


def _plural(n: int, uno: str, varios: str) -> str:
    return f"{n} {uno if n == 1 else varios}"


# --------------------------------------------------------------------------
# Etiqueta recortada
# --------------------------------------------------------------------------

class _Elidida(QLabel):
    """Etiqueta de una línea que se acorta con «…» cuando no cabe."""

    def paintEvent(self, e):
        p = QPainter(self)
        p.setPen(self.palette().color(self.foregroundRole()))
        r = self.contentsRect()
        p.drawText(r, int(self.alignment() | Qt.AlignmentFlag.AlignVCenter),
                   self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideMiddle, r.width()))
        p.end()


# --------------------------------------------------------------------------
# Las dos versiones enfrentadas
# --------------------------------------------------------------------------

class VistaComparada(QAbstractScrollArea):
    """Las dos versiones con el mismo zoom y desplazamiento: cada par de páginas ocupa la misma franja
    vertical en los dos lados; una página sin pareja deja a su lado un hueco con «(no existe en esta
    versión)».  Las páginas se dibujan a demanda (solo las visibles), como el visor."""

    cambio_elegido = Signal(int)          # id del cambio sobre el que se hizo clic

    MARGEN = 14
    HUECO = 18
    ETIQUETA = 20
    SEPARACION = 18
    FILA_DELGADA = 46

    def __init__(self):
        super().__init__()
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.viewport().setMouseTracking(True)
        self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        self.res: Resultado | None = None
        self.docs: tuple = (None, None)
        self.zoom = 1.0
        self.ajustar = True
        self.solo_nueva = False
        self.tipos: set[str] = set(comparar.TIPOS)
        self.seleccion: Cambio | None = None
        self._parpadeo = 0
        self._filas: list[dict] = []
        self._por_fila: dict[int, list[Cambio]] = {}
        self._movidas: dict[str, dict[int, int]] = {"a": {}, "b": {}}
        self._cache: dict[tuple[str, int], tuple[float, QImage]] = {}
        self._alto = 0
        self._ancho = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._dibujar_visibles)
        self._blink = QTimer(self)
        self._blink.setInterval(170)
        self._blink.timeout.connect(self._tic)
        self.verticalScrollBar().valueChanged.connect(self._movido)
        self.horizontalScrollBar().valueChanged.connect(self._movido)
        self.verticalScrollBar().setSingleStep(40)

    # ------------------------------------------------------------------ datos
    def cargar(self, res: Resultado | None, docs: tuple):
        self.res, self.docs = res, docs
        self.seleccion = None
        self._cache.clear()
        self._por_fila = comparar.cambios_por_fila(res) if res else {}
        self._movidas = {"a": {}, "b": {}}
        for c in (res.cambios if res else []):
            if c.tipo == "pagina_movida":
                self._movidas["a"][c.pagina_a] = c.pagina_b
                self._movidas["b"][c.pagina_b] = c.pagina_a
        self.ajustar = True
        self.verticalScrollBar().setValue(0)
        self._maquetar()

    def soltar(self):
        self._timer.stop()
        self._blink.stop()
        self.res, self.docs = None, (None, None)
        self._cache.clear()
        self._filas = []
        self.viewport().update()

    def set_tipos(self, tipos: set[str]):
        self.tipos = set(tipos)
        self.viewport().update()

    def set_solo_nueva(self, on: bool):
        if on == self.solo_nueva:
            return
        ancla = self._ancla()
        self.solo_nueva = on
        self._maquetar()
        self._restaurar(ancla)

    def set_zoom(self, z: float | None):
        """None = ajustar al ancho."""
        ancla = self._ancla()
        if z is None:
            self.ajustar = True
        else:
            self.ajustar = False
            self.zoom = max(0.15, min(4.0, z))
        self._maquetar()
        self._restaurar(ancla)

    # ------------------------------------------------------------------ geometría
    def _max_ancho(self, lado: str) -> float:
        if not self.res:
            return 612.0
        tam = self.res.tamanos_a if lado == "a" else self.res.tamanos_b
        return max((w for w, _h in tam), default=612.0)

    def _ancho_columna(self) -> float:
        if self.solo_nueva:
            return self._max_ancho("b") * self.zoom
        return max(self._max_ancho("a"), self._max_ancho("b")) * self.zoom

    def _maquetar(self):
        vw = max(50, self.viewport().width())
        if self.res is None:
            self._filas, self._alto, self._ancho = [], 0, 0
            self.verticalScrollBar().setRange(0, 0)
            self.horizontalScrollBar().setRange(0, 0)
            self.viewport().update()
            return
        if self.ajustar:
            if self.solo_nueva:
                self.zoom = (vw - 2 * self.MARGEN) / max(self._max_ancho("b"), 1)
            else:
                self.zoom = (vw - 2 * self.MARGEN - self.HUECO) / (2 * max(self._max_ancho("a"),
                                                                           self._max_ancho("b"), 1))
            self.zoom = max(0.15, min(4.0, self.zoom))
        z = self.zoom
        ta, tb = self.res.tamanos_a, self.res.tamanos_b
        filas = []
        y = 10
        for k, (a, b) in enumerate(self.res.pares):
            if self.solo_nueva and b is None:
                filas.append(dict(fila=k, a=a, b=None, y=y, h=self.FILA_DELGADA, delgada=True))
                y += self.FILA_DELGADA + 8
                continue
            ha = ta[a][1] if a is not None else 0
            hb = tb[b][1] if b is not None else 0
            alto = (hb if self.solo_nueva else max(ha, hb)) * z
            filas.append(dict(fila=k, a=a, b=b, y=y, h=self.ETIQUETA + alto, delgada=False))
            y += self.ETIQUETA + alto + self.SEPARACION
        self._filas = filas
        self._alto = y
        columnas = 1 if self.solo_nueva else 2
        self._ancho = int(2 * self.MARGEN + columnas * self._ancho_columna() + (columnas - 1) * self.HUECO)
        vh = self.viewport().height()
        self.verticalScrollBar().setRange(0, max(0, int(self._alto - vh)))
        self.verticalScrollBar().setPageStep(max(1, vh))
        self.horizontalScrollBar().setRange(0, max(0, self._ancho - vw))
        self.horizontalScrollBar().setPageStep(max(1, vw))
        self.viewport().update()
        self._timer.start()

    def _x0(self) -> float:
        vw = self.viewport().width()
        extra = max(0, (vw - self._ancho) / 2)
        return extra + self.MARGEN - self.horizontalScrollBar().value()

    def _columna(self, lado: str) -> float:
        x = self._x0()
        if lado == "b" and not self.solo_nueva:
            x += self._ancho_columna() + self.HUECO
        return x

    def _rect_pagina(self, fila: dict, lado: str) -> QRectF | None:
        """Rectángulo en pantalla de la página de ese lado (o del hueco, si no existe)."""
        if not self.res:
            return None
        if self.solo_nueva and lado == "a":
            return None
        z = self.zoom
        pno = fila[lado]
        otro = fila["b" if lado == "a" else "a"]
        tam = self.res.tamanos_a if lado == "a" else self.res.tamanos_b
        tam_otro = self.res.tamanos_b if lado == "a" else self.res.tamanos_a
        if pno is not None:
            w, h = tam[pno]
        elif otro is not None:
            w, h = tam_otro[otro]
        else:
            return None
        colw = self._ancho_columna()
        x = self._columna(lado) + (colw - w * z) / 2
        y = fila["y"] + self.ETIQUETA - self.verticalScrollBar().value()
        return QRectF(x, y, w * z, h * z)

    def _filas_visibles(self, extra: float = 0.0) -> list[dict]:
        top = self.verticalScrollBar().value() - extra
        bottom = top + self.viewport().height() + 2 * extra
        k = 0
        lo, hi = 0, len(self._filas)
        while lo < hi:                       # búsqueda binaria de la primera fila visible
            mid = (lo + hi) // 2
            f = self._filas[mid]
            if f["y"] + f["h"] < top:
                lo = mid + 1
            else:
                hi = mid
        k = lo
        out = []
        while k < len(self._filas) and self._filas[k]["y"] <= bottom:
            out.append(self._filas[k])
            k += 1
        return out

    def _ancla(self) -> tuple[int, float] | None:
        """Fila en la parte de arriba y qué fracción de ella ya pasó (para conservar el sitio al cambiar el
        zoom o el modo)."""
        vis = self._filas_visibles()
        if not vis:
            return None
        f = vis[0]
        top = self.verticalScrollBar().value()
        return f["fila"], (top - f["y"]) / max(f["h"], 1)

    def _restaurar(self, ancla):
        if not ancla:
            return
        fila, frac = ancla
        for f in self._filas:
            if f["fila"] >= fila:
                self.verticalScrollBar().setValue(int(f["y"] + frac * f["h"]))
                break

    # ------------------------------------------------------------------ dibujo
    def resizeEvent(self, e):
        super().resizeEvent(e)
        ancla = self._ancla()
        self._maquetar()
        self._restaurar(ancla)

    def _movido(self, *_):
        self.viewport().update()
        self._timer.start()

    def _dibujar_visibles(self):
        """Dibuja (con MuPDF) las páginas visibles que aún no tienen imagen a este zoom."""
        if self.res is None or self.docs[0] is None:
            return
        if ctx.busy or not PDF_LOCK.acquire(blocking=False):
            self._timer.start(150)          # hay una tarea usando los PDF: se reintenta en un momento
            return
        pendiente = False
        try:
            dpr = self.devicePixelRatioF()
            inicio = time.perf_counter()
            necesarias = set()
            for f in self._filas_visibles(extra=self.viewport().height() * 0.5):
                for lado, doc in (("a", self.docs[0]), ("b", self.docs[1])):
                    pno = f[lado]
                    if pno is None or (lado == "a" and self.solo_nueva) or f["delgada"]:
                        continue
                    necesarias.add((lado, pno))
                    w, h = (self.res.tamanos_a if lado == "a" else self.res.tamanos_b)[pno]
                    s = min(self.zoom * dpr, math.sqrt(MAX_PIXELES / max(w * h, 1)))
                    hecho = self._cache.get((lado, pno))
                    if hecho and abs(hecho[0] - s) / s < 0.02:
                        continue
                    if time.perf_counter() - inicio > 0.06:
                        pendiente = True
                        break
                    try:
                        img = qimage_from_render(core.render_scaled(doc, pno, s))
                    except Exception:  # noqa: BLE001 — una página dañada se deja en blanco
                        img = QImage()
                    self._cache[(lado, pno)] = (s, img)
                if pendiente:
                    break
            if len(self._cache) > 40:          # memoria acotada: se sueltan las que ya no se ven
                for clave in [k for k in self._cache if k not in necesarias][: len(self._cache) - 40]:
                    del self._cache[clave]
        finally:
            PDF_LOCK.release()
        self.viewport().update()
        if pendiente:
            self._timer.start(0)

    def paintEvent(self, e):
        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.fillRect(self.viewport().rect(), QColor(tema.color("surface-sunken")))
        if self.res is None:
            p.end()
            return
        chica = QFont(self.font())
        chica.setPointSizeF(8.5)
        for f in self._filas_visibles():
            if f["delgada"]:
                self._pintar_delgada(p, f, chica)
                continue
            for lado in ("a", "b"):
                if lado == "a" and self.solo_nueva:
                    continue
                self._pintar_lado(p, f, lado, chica)
        p.end()

    def _pintar_delgada(self, p: QPainter, f: dict, fuente: QFont):
        y = f["y"] - self.verticalScrollBar().value()
        x = self._columna("b")
        r = QRectF(x, y, self._ancho_columna(), f["h"])
        pen = QPen(QColor(tema.color("line-strong")), 1, Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(QColor(tema.color("surface-hover")))
        p.drawRoundedRect(r, 6, 6)
        p.setFont(fuente)
        p.setPen(QColor(tema.color("ink-3")))
        a = f["a"]
        destino = self._movidas["a"].get(a)
        if destino is not None:
            texto = f"Página {a + 1} de la versión anterior: pasó a la página {destino + 1}"
        else:
            texto = f"Página {a + 1} de la versión anterior: no existe en esta versión"
        p.drawText(r.adjusted(10, 0, -10, 0), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, texto)
        for c in self._por_fila.get(f["fila"], []):
            if c.tipo == "pagina_quitada" and c.tipo in self.tipos and c.pagina_a == a:
                p.setPen(QPen(QColor(comparar.COLORES[c.tipo]), 2.5 if c is self.seleccion else 1.6))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(r, 6, 6)

    def _pintar_lado(self, p: QPainter, f: dict, lado: str, fuente: QFont):
        r = self._rect_pagina(f, lado)
        if r is None:
            return
        pno = f[lado]
        p.setFont(fuente)
        p.setPen(QColor(tema.color("ink-3")))
        if pno is not None:
            nombre = ("Anterior" if lado == "a" else "Nueva") if not self.solo_nueva else "Página"
            p.drawText(QRectF(r.x(), r.y() - self.ETIQUETA, r.width(), self.ETIQUETA - 4),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
                       f"{nombre} · pág. {pno + 1}" if not self.solo_nueva else f"Página {pno + 1}")
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 28))
            p.drawRect(r.translated(1.5, 2))
            p.fillRect(r, QColor(Qt.GlobalColor.white))          # la página es blanca en los dos temas
            hecho = self._cache.get((lado, pno))
            if hecho and not hecho[1].isNull():
                p.drawImage(r, hecho[1])
            p.setPen(QPen(QColor(tema.color("line")), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(r)
            self._pintar_cambios(p, f, lado, r)
            return
        # hueco: la página no existe en esta versión
        otro = f["b" if lado == "a" else "a"]
        p.setPen(QPen(QColor(tema.color("line-strong")), 1.2, Qt.PenStyle.DashLine))
        p.setBrush(QColor(tema.color("surface-hover")))
        p.drawRoundedRect(r, 6, 6)
        texto = "(no existe en esta versión)"
        if lado == "a" and otro in self._movidas["b"]:
            texto = f"(estaba en la página {self._movidas['b'][otro] + 1})"
        elif lado == "b" and otro in self._movidas["a"]:
            texto = f"(pasó a la página {self._movidas['a'][otro] + 1})"
        p.setPen(QColor(tema.color("ink-3")))
        p.drawText(r.adjusted(8, 0, -8, 0), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, texto)

    def _a_pantalla(self, r_pagina: QRectF, rect) -> QRectF:
        z = self.zoom
        return QRectF(r_pagina.x() + rect[0] * z, r_pagina.y() + rect[1] * z,
                      (rect[2] - rect[0]) * z, (rect[3] - rect[1]) * z)

    def _pintar_cambios(self, p: QPainter, f: dict, lado: str, r: QRectF):
        pno = f[lado]
        for c in self._por_fila.get(f["fila"], []):
            if c.tipo not in self.tipos:
                continue
            if (c.pagina_a if lado == "a" else c.pagina_b) != pno:
                continue
            rects = c.rects_a if lado == "a" else c.rects_b
            marca = c.marca_a if lado == "a" else c.marca_b
            sel = c is self.seleccion
            brillo = sel and self._parpadeo % 2 == 1
            self._pintar_cambio(p, c, lado, r, rects, marca, sel, brillo)

    def _color(self, c: Cambio, lado: str) -> QColor:
        """Rojo en la anterior (quitado, cambiado) y verde en la nueva (agregado, cambiado); en la vista de
        una sola versión, lo cambiado va en ámbar para distinguirlo de lo agregado."""
        if c.tipo == "cambiado":
            if self.solo_nueva:
                return QColor(comparar.COLORES["cambiado"])
            return QColor(comparar.COLORES["quitado" if lado == "a" else "agregado"])
        return QColor(comparar.COLORES[c.tipo])

    def _pintar_cambio(self, p, c: Cambio, lado: str, r: QRectF, rects, marca, sel: bool, brillo: bool):
        color = self._color(c, lado)
        if c.tipo in comparar.TIPOS_PAGINA:
            p.setPen(QPen(color, 4 if sel else 3))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(r.adjusted(-2, -2, 2, 2))
            etiqueta = comparar.NOMBRES[c.tipo]
            fm = p.fontMetrics()
            caja = QRectF(r.x(), r.y(), fm.horizontalAdvance(etiqueta) + 14, fm.height() + 6)
            p.fillRect(caja, color)
            p.setPen(QColor(tema.color("on-cat")))
            p.drawText(caja, Qt.AlignmentFlag.AlignCenter, etiqueta)
            return
        relleno = QColor(color)
        if c.tipo == "apariencia":
            relleno.setAlpha(110 if brillo else 28)
            p.setPen(QPen(color, 2.6 if sel else 1.5))
            p.setBrush(relleno)
            for rc in rects:
                p.drawRect(self._a_pantalla(r, rc).adjusted(-1, -1, 1, 1))
            return
        relleno.setAlpha(170 if brillo else (95 if sel else 70))
        for rc in rects:
            q = self._a_pantalla(r, rc).adjusted(-1, -1, 1, 1)
            p.setPen(QPen(color.darker(115), 2.2) if sel else Qt.PenStyle.NoPen)
            p.setBrush(relleno)
            p.drawRoundedRect(q, 2, 2)
        if marca and not rects:
            q = self._a_pantalla(r, marca)
            x = q.center().x()
            p.setPen(QPen(color, 3 if sel or brillo else 2))
            p.drawLine(QPointF(x, q.top() - 1), QPointF(x, q.bottom() + 1))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            t = 4.5 if sel else 3.5
            p.drawPolygon([QPointF(x - t, q.top() - 1 - t), QPointF(x + t, q.top() - 1 - t), QPointF(x, q.top() - 1)])

    # ------------------------------------------------------------------ ratón
    def _cambio_en(self, pos: QPointF) -> Cambio | None:
        mejor = None
        for f in self._filas_visibles():
            for lado in ("a", "b"):
                r = self._rect_pagina(f, lado) if not f["delgada"] else None
                if f["delgada"] and lado == "b":
                    y = f["y"] - self.verticalScrollBar().value()
                    caja = QRectF(self._columna("b"), y, self._ancho_columna(), f["h"])
                    if caja.contains(pos):
                        for c in self._por_fila.get(f["fila"], []):
                            if c.tipo in self.tipos:
                                return c
                    continue
                if r is None or f[lado] is None or not r.adjusted(-6, -6, 6, 6).contains(pos):
                    continue
                for c in self._por_fila.get(f["fila"], []):
                    if c.tipo not in self.tipos or (c.pagina_a if lado == "a" else c.pagina_b) != f[lado]:
                        continue
                    rects = c.rects_a if lado == "a" else c.rects_b
                    marca = c.marca_a if lado == "a" else c.marca_b
                    zonas = [self._a_pantalla(r, rc) for rc in rects]
                    if marca and not rects:
                        zonas.append(self._a_pantalla(r, marca).adjusted(-5, -7, 5, 3))
                    if c.tipo in comparar.TIPOS_PAGINA:
                        zonas = [r]
                    for q in zonas:
                        q = q.adjusted(-3, -3, 3, 3)
                        if q.contains(pos):
                            area = q.width() * q.height()
                            if mejor is None or area < mejor[0]:
                                mejor = (area, c)
        return mejor[1] if mejor else None

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            c = self._cambio_en(e.position())
            if c is not None:
                self.cambio_elegido.emit(c.id)
                return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        c = self._cambio_en(e.position())
        self.viewport().setCursor(Qt.CursorShape.PointingHandCursor if c else Qt.CursorShape.ArrowCursor)
        if c is not None:
            QToolTip.showText(e.globalPosition().toPoint(), f"{c.id}. {comparar.texto_lista(c, 140)}", self)
        else:
            QToolTip.hideText()
        super().mouseMoveEvent(e)

    def wheelEvent(self, e):
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
            self.set_zoom(self.zoom * factor)
            e.accept()
            return
        super().wheelEvent(e)

    # ------------------------------------------------------------------ ir a un cambio
    def ir_a(self, c: Cambio, parpadear: bool = True):
        self.seleccion = c
        en_a, en_b = comparar.lados(c)
        lado = "b" if en_b else "a"
        if self.solo_nueva:
            lado = "b"
        pno = c.pagina_b if lado == "b" else c.pagina_a
        fila = None
        for f in self._filas:
            if (lado == "b" and f["b"] == pno and pno is not None) or \
                    (lado == "a" and f["a"] == pno and pno is not None):
                fila = f
                break
        if fila is None and c.pagina_a is not None:        # en la vista de una sola versión: fila delgada
            fila = next((f for f in self._filas if f["a"] == c.pagina_a), None)
        if fila is not None:
            rects = c.rects_b if lado == "b" else c.rects_a
            marca = c.marca_b if lado == "b" else c.marca_a
            y = fila["y"] + self.ETIQUETA
            if fila["delgada"] or c.tipo in comparar.TIPOS_PAGINA:
                destino = fila["y"] - 8
            elif rects or marca:
                rc = (rects or [marca])[0]
                destino = y + rc[1] * self.zoom - self.viewport().height() * 0.35
            else:
                destino = fila["y"]
            self.verticalScrollBar().setValue(int(max(0, destino)))
            if not fila["delgada"] and (rects or marca) and self._ancho > self.viewport().width():
                rc = (rects or [marca])[0]
                r = self._rect_pagina(fila, lado)
                if r is not None:
                    x = r.x() + self.horizontalScrollBar().value() + rc[0] * self.zoom
                    self.horizontalScrollBar().setValue(int(max(0, x - self.viewport().width() * 0.3)))
        self._parpadeo = 6 if parpadear else 0
        if parpadear:
            self._blink.start()
        self.viewport().update()

    def _tic(self):
        self._parpadeo -= 1
        if self._parpadeo <= 0:
            self._parpadeo = 0
            self._blink.stop()
        self.viewport().update()


# --------------------------------------------------------------------------
# Piezas de la pantalla
# --------------------------------------------------------------------------

class _FichaVersion(QFrame):
    """Cada versión como ficha en el encabezado (``rp-filechip`` compacta): miniatura, nombre, qué versión es
    y sus páginas; «Cambiar» y «Quitar» como íconos.  Clic en la ficha: cambiar el archivo.  Se encoge (con
    el nombre recortado) para que quepan las dos junto al título."""

    change_clicked = Signal()
    remove_clicked = Signal()

    def __init__(self, rotulo: str):
        super().__init__()
        self.setObjectName("FileChip")
        self.setFixedHeight(48)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.rotulo = rotulo
        h = QHBoxLayout(self)
        h.setContentsMargins(8, 0, 4, 0)
        h.setSpacing(6)
        self.thumb = PdfThumb(22, 28)
        h.addWidget(self.thumb)
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addStretch(1)
        self.nombre = _Elidida()
        self.nombre.setObjectName("FileChipName")
        self.meta = _Elidida()
        self.meta.setObjectName("Caption")
        for lb in (self.nombre, self.meta):
            lb.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            col.addWidget(lb)
        col.addStretch(1)
        h.addLayout(col, 1)
        self.b_cambiar = self._icono("folder_open", f"Cambiar la {rotulo.lower()}", self.change_clicked)
        self.b_quitar = self._icono("remove", f"Quitar la {rotulo.lower()}", self.remove_clicked)
        h.addWidget(self.b_cambiar)
        h.addWidget(self.b_quitar)

    @staticmethod
    def _icono(glifo: str, ayuda: str, senal) -> QToolButton:
        b = QToolButton()
        b.setObjectName("Ghost")
        b.setIcon(icon(glifo, tema.color("ink-2"), size=16))
        b.setIconSize(QSize(16, 16))
        b.setToolTip(ayuda)
        b.setAccessibleName(ayuda)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(senal)
        return b

    def set_file(self, nombre: str, meta: str, ruta: str) -> None:
        self.nombre.setText(nombre)
        self.meta.setText(meta)
        self.setToolTip(f"{self.rotulo}: {ruta}")
        self.updateGeometry()

    def sizeHint(self):  # noqa: N802
        fm_n = QFontMetrics(tema.fuente("label"))
        fm_m = QFontMetrics(tema.fuente("caption"))
        texto = max(fm_n.horizontalAdvance(self.nombre.text()), fm_m.horizontalAdvance(self.meta.text()))
        return QSize(min(300, 8 + 22 + 8 + texto + 6 + 2 * 28 + 4), 48)

    def minimumSizeHint(self):  # noqa: N802
        return QSize(150, 48)

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self.b_quitar.setVisible(self.width() >= 200)     # muy angosta: que se lea algo del nombre

    def mouseReleaseEvent(self, e):  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.change_clicked.emit()
        super().mouseReleaseEvent(e)


class _HojaVersion(QWidget):
    """La primera página de una versión ya elegida, blanca sobre el pozo, con «Página 1 de N» debajo."""

    def __init__(self):
        super().__init__()
        self.img: QImage | None = None
        self.paginas = 0
        self.setMinimumSize(120, 120)

    def cargar(self, ruta: str | None, clave: str | None) -> None:
        self.img, self.paginas = None, 0
        if ruta:
            try:
                with PDF_LOCK, core.open_pdf(ruta, clave) as doc:
                    self.paginas = doc.page_count
                    self.img = qimage_from_render(core.render_page(doc, 0, 640))
            except Exception:  # noqa: BLE001 — sin vista previa no se pierde nada: la ficha ya dice cuál es
                self.img = None
        self.update()

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        caja = QRectF(self.rect()).adjusted(4, 4, -4, -28)
        if self.img is not None and not self.img.isNull():
            w, h = self.img.width(), self.img.height()
            k = min(caja.width() / max(w, 1), caja.height() / max(h, 1))
            r = QRectF(0, 0, w * k, h * k)
            r.moveCenter(caja.center())
        else:
            r = QRectF(0, 0, caja.height() * 0.77, caja.height())
            r.moveCenter(caja.center())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 22))
        p.drawRoundedRect(r.translated(0, 2), 3, 3)
        p.fillRect(r, QColor(Qt.GlobalColor.white))          # la página es blanca en los dos temas
        if self.img is not None and not self.img.isNull():
            p.drawImage(r, self.img)
        p.setPen(QPen(QColor(tema.color("line")), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(r.adjusted(0.5, 0.5, -0.5, -0.5))
        p.setFont(tema.fuente("caption", self.font()))
        p.setPen(QColor(tema.color("ink-3")))
        texto = f"Página 1 de {self.paginas}" if self.paginas else "Sin vista previa"
        p.drawText(QRectF(0, self.height() - 22, self.width(), 18), Qt.AlignmentFlag.AlignCenter, texto)
        p.end()


class _CajaVersion(QWidget):
    """Una de las dos versiones en el área principal: la zona para soltarla o, ya elegida, su primera
    página."""

    def __init__(self, titulo: str, sel: SingleFilePicker, tile_key: str):
        super().__init__()
        self.sel = sel
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        self.titulo = label(titulo, "StepTitle")
        v.addWidget(self.titulo)
        self.pila = QStackedWidget()
        self.zona = sel.drop_zone(f"Suelta aquí la {titulo.lower()}", "o haz clic para buscarla en tu equipo",
                                  tile_key)
        self.zona.setStyleSheet("#DropZone { margin: 0px; }")        # alineada con su título
        self.hoja = _HojaVersion()
        self.pila.addWidget(self.zona)
        self.pila.addWidget(self.hoja)
        v.addWidget(self.pila, 1)

    def actualizar(self) -> None:
        ruta = self.sel.path
        if ruta and self.hoja.property("ruta") != ruta:
            self.hoja.cargar(ruta, ctx.passwords.get(ruta))
            self.hoja.setProperty("ruta", ruta)
        elif not ruta:
            self.hoja.setProperty("ruta", None)
        self.pila.setCurrentWidget(self.hoja if ruta else self.zona)

    def compactar(self, on: bool) -> None:
        """Una sobre otra con poco ancho: la zona pierde el azulejo grande y se hace más baja."""
        azulejo = self.zona.findChild(GlyphTile)
        if azulejo is not None:
            azulejo.setVisible(not on)
        self.zona.setMinimumHeight(120 if on else 190)
        self.hoja.setMinimumHeight(110 if on else 160)


class _Preparacion(QWidget):
    """El área principal antes de comparar: el aviso del archivo repetido y las dos versiones (lado a lado,
    o una sobre otra si no caben)."""

    def __init__(self, al_cambiar_ancho):
        super().__init__()
        self._al_cambiar_ancho = al_cambiar_ancho

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._al_cambiar_ancho(self.width())


class _FiltroChip(ChoiceChip):
    """Ficha de filtro por tipo de cambio: el punto del color con que se resalta ese tipo, el nombre y
    cuántos hay.  Elegida = se ve en la lista y en las páginas."""

    def __init__(self, texto: str, color: str, n: int):
        super().__init__(texto, True, n)
        self.color = color

    def sizeHint(self):  # noqa: N802
        f, fc = self._fuentes()
        w = 12 + 10 + 8 + QFontMetrics(f).horizontalAdvance(self.text()) + 12
        if self.count not in (None, ""):
            w += 6 + QFontMetrics(fc).horizontalAdvance(str(self.count))
        return QSize(w, 32)

    minimumSizeHint = sizeHint

    def paintEvent(self, _e):  # noqa: N802
        t = tema.tokens()
        ton = tema.tonos(self.cat or cat_de(self))
        on = self.isChecked()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if on:
            p.setPen(QPen(QColor(ton["c"]), 1))
            p.setBrush(QColor(ton["band"]))
        else:
            p.setPen(QPen(QColor(t["line-control"]), 1))
            p.setBrush(QColor(t["surface-hover"] if self.underMouse() else t["surface"]))
        p.drawRoundedRect(r, 8, 8)
        punto = QRectF(12, self.height() / 2 - 5, 10, 10)
        if on:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(self.color))
        else:
            p.setPen(QPen(QColor(self.color), 1.6))
            p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(punto.adjusted(0.8, 0.8, -0.8, -0.8) if not on else punto)
        f, fc = self._fuentes()
        x = 30.0
        p.setFont(f)
        p.setPen(QColor(ton["ink"] if on else t["ink-2"]))
        tw = QFontMetrics(f).horizontalAdvance(self.text())
        p.drawText(QRectF(x, 0, tw + 2, self.height()), Qt.AlignmentFlag.AlignVCenter, self.text())
        if self.count not in (None, ""):
            p.setFont(fc)
            p.setPen(QColor(t["ink-3"]))
            p.drawText(QRectF(x + tw + 6, 0, self.width() - x - tw - 6, self.height()),
                       Qt.AlignmentFlag.AlignVCenter, str(self.count))
        if self._kbd and self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(t["focus"]), 2))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 8, 8)
        p.end()


def _boton(texto: str, nombre: str = "Secondary", glifo: str | None = None, cb=None, tam: str | None = "sm"):
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


# --------------------------------------------------------------------------
# La pantalla
# --------------------------------------------------------------------------

class CompararPage(ToolPage):
    """Plantilla 2.0 con dos etapas.

    * **Preparación:** las dos versiones como fichas en el encabezado (con «Intercambiar» entre ellas); en
      el panel, qué comparar y qué no debe contar; en el área principal, la primera página de cada versión
      (o la zona para soltarla).  Principal: «Comparar PDF».
    * **Resultado:** el panel se recoge y el área principal muestra el resumen, las fichas con las
      diferencias encontradas (filtran por tipo), la lista de cambios y las dos versiones enfrentadas.
      Principal: «Guardar informe» (PDF con las páginas con cambios, con todas, o la lista en Excel).
    """

    key = "compare"
    title = "Comparar dos PDF"
    subtitle = "Encuentra qué cambió entre dos versiones: texto, páginas, imágenes y sellos."
    action_text = "Comparar PDF"

    def build(self):
        self.setAcceptDrops(True)
        self.res: Resultado | None = None
        self._rutas: tuple[str, str] | None = None        # las comparadas (y sus contraseñas)
        self._claves: tuple[str | None, str | None] = (None, None)
        self._docs: tuple = (None, None)
        self._items: dict[int, QListWidgetItem] = {}
        self._cambios: dict[int, Cambio] = {}
        self.filtros: dict[str, _FiltroChip] = {}
        self._estrecho: bool | None = None
        self._lado_a_lado: bool | None = None
        self._resultado = False

        # ---- selectores (invisibles: se ven como fichas en el encabezado y zonas en el área principal)
        self.sel_a = SingleFilePicker("Suelta aquí la versión anterior", "o haz clic para buscarla en tu equipo")
        self.sel_b = SingleFilePicker("Suelta aquí la versión nueva", "o haz clic para buscarla en tu equipo")
        for sel in (self.sel_a, self.sel_b):
            sel.setParent(self)
            sel.hide()
            sel.changed.connect(self._version_cambiada)

        # ---- encabezado: las dos versiones como fichas, con «Intercambiar» entre ellas
        cab = QWidget()
        hl = QHBoxLayout(cab)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(6)
        self.ficha_a = _FichaVersion("Versión anterior")
        self.ficha_b = _FichaVersion("Versión nueva")
        self.b_intercambiar = QToolButton()
        self.b_intercambiar.setObjectName("Ghost")
        self.b_intercambiar.setIcon(icon("intercambiar", tema.color("ink-2"), size=18))
        self.b_intercambiar.setIconSize(QSize(18, 18))
        self.b_intercambiar.setToolTip("Intercambiar: la versión anterior pasa a ser la nueva, y al revés")
        self.b_intercambiar.setAccessibleName("Intercambiar las versiones")
        self.b_intercambiar.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_intercambiar.clicked.connect(self.intercambiar)
        for ficha, sel in ((self.ficha_a, self.sel_a), (self.ficha_b, self.sel_b)):
            ficha.change_clicked.connect(sel.browse)
            ficha.remove_clicked.connect(sel.clear)
            ficha.hide()
        hl.addWidget(self.ficha_a, 1)
        hl.addWidget(self.b_intercambiar)
        hl.addWidget(self.ficha_b, 1)
        self.set_header_widget(cab)
        self.header_slot.setStretch(0, 0)
        self.cab_fichas = cab

        # ---- panel: qué comparar y qué no debe contar
        p1 = self.step("¿Qué quieres comparar?")
        self.c_texto = SwitchRow("El texto", "Palabras agregadas, quitadas o cambiadas.", True)
        self.c_apariencia = SwitchRow("La apariencia", "Imágenes, sellos, firmas, gráficos y formato.", True)
        for w in (self.c_texto, self.c_apariencia):
            p1.addWidget(w)
        p2 = self.step("¿Qué no debe contar?")
        self.c_encabezados = SwitchRow("Encabezados, pies y números de página",
                                       "Los renglones que se repiten arriba o abajo de cada página.")
        self.c_mayusculas = SwitchRow("Mayúsculas y minúsculas")
        for w in (self.c_encabezados, self.c_mayusculas):
            p2.addWidget(w)
        p3 = self.step("Páginas escaneadas")
        self.paso_ocr = p3.parentWidget()
        self.c_ocr = SwitchRow("Leer con OCR las páginas escaneadas", "Reconoce su texto para compararlo. Es más "
                               "lento.")
        p3.addWidget(self.c_ocr)
        for w in (self.c_texto, self.c_apariencia, self.c_encabezados, self.c_mayusculas, self.c_ocr):
            w.toggled.connect(lambda *_: self._refrescar())
        self._idioma = self._idioma_ocr()
        if not self._idioma:                      # sin modelos de OCR el paso no aplica
            self.c_ocr.setChecked(False)
            self.c_ocr.setEnabled(False)
            self.paso_ocr.hide()

        # ---- área principal: dos etapas
        self._pila = QStackedWidget()
        self._pila.addWidget(self._construir_preparacion())
        self._pila.addWidget(self._construir_resultado())
        self.set_main(self._pila)

        # ---- barra de acción
        self.b_comparar = self.action                # en la preparación: «Comparar PDF»
        self.b_informe = self.action                 # en el resultado: «Guardar informe», con su menú
        self.menu_informe = QMenu(self.action)
        self.menu_informe.addAction(icon("pdf"), "Informe en PDF (páginas con cambios)…",
                                    lambda: self.guardar_informe(False))
        self.menu_informe.addAction(icon("pdf"), "Informe en PDF (todas las páginas)…",
                                    lambda: self.guardar_informe(True))
        self.menu_informe.addSeparator()
        self.menu_informe.addAction(icon("text"), "Lista de cambios en Excel…", self.guardar_excel)
        self.b_nueva = self.add_secondary("Nueva comparación", self.nueva_comparacion)
        self.b_nueva.hide()
        self.footer_info.setWordWrap(True)

        for atajo, paso in (("F7", 1), ("Shift+F7", -1)):
            sc = QShortcut(QKeySequence(atajo), self)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(lambda d=paso: self.mover(d))
        self._cargar_opciones()
        self._refrescar()

    # ================================================================== preparación
    def _construir_preparacion(self) -> QWidget:
        hoja = _Preparacion(self._ancho_preparacion)
        v = QVBoxLayout(hoja)
        v.setContentsMargins(16, 14, 16, 16)
        v.setSpacing(10)
        self.aviso_mismo = Note(MISMO_ARCHIVO, "warn")
        self.aviso_mismo.hide()
        v.addWidget(self.aviso_mismo)
        self.caja_a = _CajaVersion("Versión anterior", self.sel_a, "compare")
        self.caja_b = _CajaVersion("Versión nueva", self.sel_b, "compare")
        for caja in (self.caja_a, self.caja_b):
            for w in (caja.zona, caja.hoja):       # dos archivos soltados a la vez: la página decide el orden
                w.installEventFilter(self)
                w.setAcceptDrops(True)
        self.rejilla = QGridLayout()
        self.rejilla.setHorizontalSpacing(16)
        self.rejilla.setVerticalSpacing(12)
        self._acomodar_selectores(True)
        v.addLayout(self.rejilla, 1)
        return hoja

    def _ancho_preparacion(self, ancho: int):
        self._acomodar_selectores(ancho >= 440)

    def _acomodar_selectores(self, lado_a_lado: bool):
        if lado_a_lado == self._lado_a_lado:
            return
        self._lado_a_lado = lado_a_lado
        self.rejilla.removeWidget(self.caja_a)
        self.rejilla.removeWidget(self.caja_b)
        if lado_a_lado:
            self.rejilla.addWidget(self.caja_a, 0, 0)
            self.rejilla.addWidget(self.caja_b, 0, 1)
            self.rejilla.setColumnStretch(0, 1)
            self.rejilla.setColumnStretch(1, 1)
            self.rejilla.setRowStretch(1, 0)
        else:
            self.rejilla.addWidget(self.caja_a, 0, 0)
            self.rejilla.addWidget(self.caja_b, 1, 0)
            self.rejilla.setColumnStretch(1, 0)
            self.rejilla.setRowStretch(1, 1)
        for caja in (self.caja_a, self.caja_b):
            caja.compactar(not lado_a_lado)

    @staticmethod
    def _idioma_ocr() -> str:
        langs = core.ocr_languages()
        partes = [lg for lg in ("spa", "eng") if lg in langs]
        return "+".join(partes) if partes else (langs[0] if langs else "")

    def _cargar_opciones(self):
        datos = ctx.settings.get("comparar_opciones", {}) if ctx.settings is not None else {}
        if not isinstance(datos, dict):
            return
        for clave, cb in (("texto", self.c_texto), ("apariencia", self.c_apariencia),
                          ("encabezados", self.c_encabezados), ("mayusculas", self.c_mayusculas),
                          ("ocr", self.c_ocr)):
            if isinstance(datos.get(clave), bool) and cb.isEnabled():
                cb.setChecked(datos[clave])

    def _guardar_opciones(self):
        if ctx.settings is None:
            return
        try:
            ctx.settings.set("comparar_opciones", dict(
                texto=self.c_texto.isChecked(), apariencia=self.c_apariencia.isChecked(),
                encabezados=self.c_encabezados.isChecked(), mayusculas=self.c_mayusculas.isChecked(),
                ocr=self.c_ocr.isChecked()))
        except Exception:  # noqa: BLE001 — los ajustes nunca deben interrumpir el trabajo
            pass

    def opciones(self) -> Opciones:
        return Opciones(texto=self.c_texto.isChecked(), apariencia=self.c_apariencia.isChecked(),
                        ignorar_mayusculas=self.c_mayusculas.isChecked(),
                        ignorar_encabezados=self.c_encabezados.isChecked(),
                        usar_ocr=self.c_ocr.isChecked() and self.c_ocr.isEnabled(), idioma=self._idioma or "spa")

    def _mismo(self) -> bool:
        a, b = self.sel_a.path, self.sel_b.path
        return bool(a and b and os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b)))

    def listo(self) -> bool:
        return bool(self.sel_a.path and self.sel_b.path and not self._mismo()
                    and (self.c_texto.isChecked() or self.c_apariencia.isChecked()))

    def ready(self) -> bool:
        if not hasattr(self, "sel_b"):
            return False
        return self.res is not None if self._resultado else self.listo()

    def run(self):
        if not self._resultado:
            self.comparar_ahora()

    def _version_cambiada(self, *_):
        if self._resultado:                 # cambiar una versión deja atrás el resultado
            self.nueva_comparacion()
        else:
            self._refrescar()

    def _fichas(self):
        for ficha, sel, caja in ((self.ficha_a, self.sel_a, self.caja_a), (self.ficha_b, self.sel_b, self.caja_b)):
            if sel.path:
                pags = f"{_plural(sel.info.pages, 'página', 'páginas')}" if sel.info else "protegido"
                cual = "Anterior" if sel is self.sel_a else "Nueva"
                ficha.set_file(Path(sel.path).name, f"{cual} · {pags}", sel.path)
            ficha.setVisible(bool(sel.path))
            caja.actualizar()
        self.b_intercambiar.setVisible(bool(self.sel_a.path or self.sel_b.path))
        # con las dos fichas en el encabezado (o ya en el resultado) la línea de ayuda sobra: más aire
        self.subtitle_lb.setVisible(not (self.sel_a.path and self.sel_b.path) and not self._resultado)

    def _refrescar(self):
        if not hasattr(self, "aviso_mismo"):
            return
        self._fichas()
        self.aviso_mismo.setVisible(self._mismo())
        self.refresh()
        if self._resultado:
            return
        chips = []
        if not (self.sel_a.path and self.sel_b.path):
            falta = "las dos versiones" if not (self.sel_a.path or self.sel_b.path) else \
                ("la versión nueva" if self.sel_a.path else "la versión anterior")
            texto = f"Elige {falta} para compararlas."
        elif self._mismo():
            texto = "Elige dos archivos distintos."
        elif not (self.c_texto.isChecked() or self.c_apariencia.isChecked()):
            texto = "Enciende «El texto», «La apariencia» o los dos."
        else:
            pa = self.sel_a.info.pages if self.sel_a.info else None
            pb = self.sel_b.info.pages if self.sel_b.info else None
            if pa and pb:
                chips = [f"{_plural(pa, 'página', 'páginas')} frente a {pb}"]
            texto = "Ninguno de los dos documentos se modifica."
        self.info_texto = texto
        self.set_summary(chips, texto)

    # ------------------------------------------------------------------ archivos
    def accept_files(self, paths: list[str]) -> None:
        """Un archivo: queda como versión anterior (o como nueva si la anterior ya está); no se compara.
        Dos: el de fecha más antigua queda como anterior."""
        pdfs = [p for p in paths if str(p).lower().endswith(".pdf")]
        if not pdfs:
            return
        self.nueva_comparacion()
        if len(pdfs) >= 2:
            def fecha(p):
                try:
                    return os.path.getmtime(p)
                except OSError:
                    return 0.0
            a, b = sorted(pdfs[:2], key=fecha)
            self.sel_a.set_path(a)
            self.sel_b.set_path(b)
        elif not self.sel_a.path:
            self.sel_a.set_path(pdfs[0])
        else:
            self.sel_b.set_path(pdfs[0])
        self._refrescar()

    def cargar_anterior(self, path: str) -> None:
        """Desde el visor: el PDF abierto queda como versión anterior."""
        self.nueva_comparacion()
        if self.sel_b.path and os.path.normcase(os.path.abspath(self.sel_b.path)) == \
                os.path.normcase(os.path.abspath(path)):
            self.sel_b.clear()
        self.sel_a.set_path(path)
        self._refrescar()

    def intercambiar(self):
        if self._resultado:
            self.nueva_comparacion()
        a, b = self.sel_a.path, self.sel_b.path
        for sel, ruta in ((self.sel_a, b), (self.sel_b, a)):
            if ruta:
                sel.set_path(ruta)
            else:
                sel.clear()
        self._refrescar()

    def _rutas_soltadas(self, e) -> list[str]:
        if not e.mimeData().hasUrls():
            return []
        return widgets.collect_paths(e.mimeData().urls(), core.PDF_EXTS, allow_dirs=False)

    def eventFilter(self, obj, ev):
        if ev.type() in (QEvent.Type.DragEnter, QEvent.Type.Drop):
            rutas = self._rutas_soltadas(ev)
            if len(rutas) >= 2:
                ev.acceptProposedAction()
                if ev.type() == QEvent.Type.Drop:
                    self.accept_files(rutas)
                return True
            if obj in (self.caja_a.hoja, self.caja_b.hoja) and rutas:
                ev.acceptProposedAction()        # sobre la página ya elegida: la reemplaza
                if ev.type() == QEvent.Type.Drop:
                    (self.sel_a if obj is self.caja_a.hoja else self.sel_b).set_path(rutas[0])
                return True
        return super().eventFilter(obj, ev)

    def dragEnterEvent(self, e):
        if self._rutas_soltadas(e):
            e.acceptProposedAction()

    def dropEvent(self, e):
        rutas = self._rutas_soltadas(e)
        if rutas:
            e.acceptProposedAction()
            self.accept_files(rutas)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._ajustar_resultado()

    # ------------------------------------------------------------------ comparar
    def comparar_ahora(self):
        if not self.listo() or ctx.busy:
            return
        a, b = self.sel_a.path, self.sel_b.path
        pa, pb = ctx.passwords.get(a), ctx.passwords.get(b)
        self._guardar_opciones()

        def listo(res):
            self._mostrar(res, (a, b), (pa, pb))

        ctx.run_task("Comparando los documentos…", comparar.comparar, listo, path_a=a, path_b=b,
                     opciones=self.opciones(), password_a=pa, password_b=pb)

    # ================================================================== resultado
    def _construir_resultado(self) -> QWidget:
        hoja = QWidget()
        v = QVBoxLayout(hoja)
        v.setContentsMargins(16, 12, 16, 14)
        v.setSpacing(8)

        fila1 = QHBoxLayout()
        fila1.setSpacing(6)
        self.resumen = QLabel("")
        self.resumen.setObjectName("StepTitle")
        self.resumen.setWordWrap(True)
        self.resumen.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        fila1.addWidget(self.resumen, 1)
        self.b_ant = _boton("Anterior", glifo="prev")
        self.b_ant.setToolTip("Cambio anterior (Mayús+F7)")
        self.b_sig = _boton("Siguiente", glifo="next")
        self.b_sig.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.b_sig.setToolTip("Cambio siguiente (F7)")
        for b, paso in ((self.b_ant, -1), (self.b_sig, 1)):
            b.clicked.connect(lambda _=False, d=paso: self.mover(d))
            fila1.addWidget(b, 0, Qt.AlignmentFlag.AlignTop)
        fila1.addSpacing(6)
        self.b_menos = self._boton_zoom("−", "Alejar", lambda: self.vista.set_zoom(self.vista.zoom / 1.25))
        self.b_ajustar = self._boton_zoom("Ajustar", "Ajustar al ancho", lambda: self.vista.set_zoom(None))
        self.b_mas = self._boton_zoom("+", "Acercar", lambda: self.vista.set_zoom(self.vista.zoom * 1.25))
        for b in (self.b_menos, self.b_ajustar, self.b_mas):
            fila1.addWidget(b, 0, Qt.AlignmentFlag.AlignTop)
        v.addLayout(fila1)

        self.aviso = Note("", "warn")
        self.aviso.text_lb.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.aviso.hide()
        v.addWidget(self.aviso)

        self.zona_chips = QWidget()
        self.flujo = FlowLayout(self.zona_chips, 8)
        sp = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        sp.setHeightForWidth(True)
        self.zona_chips.setSizePolicy(sp)
        v.addWidget(self.zona_chips)

        cuerpo = QSplitter(Qt.Orientation.Horizontal)
        cuerpo.setChildrenCollapsible(False)
        cuerpo.setHandleWidth(10)
        cuerpo.setStyleSheet("QSplitter::handle { background: transparent; }")
        self.lista = QListWidget()
        self.lista.setObjectName("ListaCambios")
        self.lista.setWordWrap(True)
        self.lista.setIconSize(QSize(10, 10))
        self.lista.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.lista.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.lista.setStyleSheet(f"QListWidget#ListaCambios::item {{ padding: 7px 4px; "
                                 f"border-bottom: 1px solid {tema.color('line')}; }}")
        self.lista.currentItemChanged.connect(self._item_actual)
        self.lista.itemClicked.connect(self._item_clic)
        cuerpo.addWidget(self.lista)

        derecha = QWidget()
        dv = QVBoxLayout(derecha)
        dv.setContentsMargins(0, 0, 0, 0)
        dv.setSpacing(4)
        cab = QHBoxLayout()
        cab.setContentsMargins(4, 0, 4, 0)
        cab.setSpacing(10)
        self.cab_a = _Elidida()
        self.cab_b = _Elidida()
        for lb in (self.cab_a, self.cab_b):
            lb.setObjectName("Caption")
            lb.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        cab.addWidget(self.cab_a, 1)
        cab.addWidget(self.cab_b, 1)
        self.c_solo = QCheckBox("Ver solo la versión nueva")
        self.c_solo.setToolTip("Una sola vista con todos los resaltados: lo agregado en verde, lo cambiado en "
                               "ámbar y una marca roja donde se quitó texto")
        self.c_solo.toggled.connect(self._solo_cambiado)
        self.c_solo.hide()
        cab.addWidget(self.c_solo)
        dv.addLayout(cab)
        self.vista = VistaComparada()
        self.vista.cambio_elegido.connect(self.elegir)
        dv.addWidget(self.vista, 1)
        cuerpo.addWidget(derecha)
        cuerpo.setStretchFactor(0, 0)
        cuerpo.setStretchFactor(1, 1)
        self.divisor = cuerpo
        v.addWidget(cuerpo, 1)
        return hoja

    def _boton_zoom(self, texto: str, ayuda: str, cb) -> QToolButton:
        b = QToolButton()
        b.setObjectName("IconButton")
        b.setText(texto)
        b.setToolTip(ayuda)
        b.setAccessibleName(ayuda)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setMinimumWidth(30)
        b.clicked.connect(cb)
        return b

    def _ajustar_resultado(self):
        """Con poco ancho la lista se angosta y aparece «Ver solo la versión nueva»."""
        if not hasattr(self, "lista"):
            return
        estrecho = self.width() < ANCHO_ESTRECHO
        ancho_lista = 196 if estrecho else min(300, max(240, int(self.width() * 0.26)))
        self.lista.setMinimumWidth(150)
        self.lista.setMaximumWidth(ancho_lista + 60)
        if estrecho != self._estrecho:
            self._estrecho = estrecho
            self.divisor.setSizes([ancho_lista, max(200, self.width() - ancho_lista)])
            self.c_solo.setVisible(estrecho)
            if not estrecho and self.c_solo.isChecked():
                self.c_solo.setChecked(False)
            self._cabeceras()

    def _cabeceras(self):
        if not self._rutas:
            return
        na, nb = Path(self._rutas[0]).name, Path(self._rutas[1]).name
        solo = self.vista.solo_nueva
        self.cab_a.setVisible(not solo)
        self.cab_a.setText(f"Versión anterior: {na}")
        self.cab_b.setText(f"Versión nueva: {nb}")
        self.cab_a.setToolTip(self._rutas[0])
        self.cab_b.setToolTip(self._rutas[1])

    def _solo_cambiado(self, on: bool):
        self.vista.set_solo_nueva(on)
        self._cabeceras()
        if self.vista.seleccion is not None:
            self.vista.ir_a(self.vista.seleccion, parpadear=False)

    def _modo_resultado(self, on: bool):
        """Cambia de etapa: en el resultado el panel se recoge y el principal pasa a «Guardar informe»."""
        self._resultado = on
        self._pila.setCurrentIndex(1 if on else 0)
        if self.panel is not None:
            self.panel.setVisible(not on)
        self.subtitle_lb.setVisible(not on and not (self.sel_a.path and self.sel_b.path))
        self.b_nueva.setVisible(on)
        self.action.setText("Guardar informe" if on else self.action_text)
        self.action.setMenu(self.menu_informe if on else None)
        # la flecha del menú, a la derecha y en el color del texto del principal
        self.action.setIcon(icon("chevron_down", tema.color("on-brand"), size=16) if on else QIcon())
        self.action.setLayoutDirection(Qt.LayoutDirection.RightToLeft if on else Qt.LayoutDirection.LeftToRight)
        self.action.setStyleSheet("QPushButton::menu-indicator { image: none; width: 0px; }" if on else "")
        self.action.setToolTip("Guarda un informe en PDF con las dos versiones enfrentadas, o la lista de cambios "
                               "en Excel" if on else "")
        self.refresh()

    # ------------------------------------------------------------------ mostrar el resultado
    def _mostrar(self, res: Resultado, rutas: tuple[str, str], claves: tuple):
        self._soltar_docs()
        try:
            with PDF_LOCK:
                da = core.open_pdf(rutas[0], claves[0])
                db = core.open_pdf(rutas[1], claves[1])
        except core.UserError as exc:
            QMessageBox.warning(self, "Comparar dos PDF", str(exc))
            return
        self.res, self._rutas, self._claves, self._docs = res, rutas, claves, (da, db)
        self._cambios = {c.id: c for c in res.cambios}
        self.resumen.setText(res.texto_resumen())
        self.resumen.setStyleSheet(f"color: {tema.color('ok')};" if not res.cambios else "")
        avisos = list(res.avisos)
        texto = " ".join(avisos[:3]) + (f" (y {len(avisos) - 3} avisos más)" if len(avisos) > 3 else "")
        self.aviso.setText(texto)
        self.aviso.setToolTip("\n".join(avisos))
        self.aviso.setVisible(bool(avisos))
        self._poner_chips()
        self._llenar_lista()
        self.vista.cargar(res, self._docs)
        self.vista.set_tipos(self._tipos_activos())
        self._cabeceras()
        self._modo_resultado(True)
        n = len(res.cambios)
        self.set_summary([(_plural(n, "cambio", "cambios") if n else "Sin diferencias", "cat" if n else "ok")],
                         "Ninguno de los dos documentos se modifica.")
        self._estrecho = None
        self._ajustar_resultado()
        self._botones()
        ctx.add_recent(rutas[1], res.paginas_b)
        QTimer.singleShot(0, self._primero)

    def _primero(self):
        """Selecciona el primer cambio cuando la vista ya tiene su tamaño definitivo."""
        if self.res is None or self.lista.currentItem() is not None:
            return
        primero = self._siguiente(None, 1)
        if primero is not None:
            self.lista.setCurrentItem(self._items[primero])

    def _poner_chips(self):
        while self.flujo.count():
            it = self.flujo.takeAt(0)
            w = it.widget()
            if w is not None:                 # fuera ya: deleteLater solo borra al volver al ciclo de eventos
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self.filtros = {}
        if not self.res:
            return
        for t in comparar.TIPOS:
            n = self.res.resumen.get(t, 0)
            if not n:
                continue
            b = _FiltroChip(NOMBRES_FILTRO[t], comparar.COLORES[t], n)
            b.setToolTip(f"Mostrar u ocultar: {NOMBRES_FILTRO[t].lower()}")
            b.toggled.connect(lambda _on, tt=t: self._filtro(tt))
            self.flujo.addWidget(b)
            self.filtros[t] = b
        self.flujo.invalidate()
        self.zona_chips.setVisible(bool(self.filtros))
        self.zona_chips.updateGeometry()

    def _tipos_activos(self) -> set[str]:
        return {t for t in comparar.TIPOS if t not in self.filtros or self.filtros[t].isChecked()}

    def set_filtro(self, tipo: str, visible: bool):
        if tipo in self.filtros:
            self.filtros[tipo].setChecked(visible)

    def _filtro(self, _tipo: str):
        activos = self._tipos_activos()
        self.vista.set_tipos(activos)
        actual = self.lista.currentItem()
        for cid, it in self._items.items():
            it.setHidden(self._cambios[cid].tipo not in activos)
        if actual is not None and actual.isHidden():
            sig = self._siguiente(actual.data(Qt.ItemDataRole.UserRole), 1) or \
                self._siguiente(actual.data(Qt.ItemDataRole.UserRole), -1)
            if sig is not None:
                self.lista.setCurrentItem(self._items[sig])
            else:
                self.lista.setCurrentItem(None)
                self.vista.seleccion = None
                self.vista.viewport().update()
        self._botones()

    def _llenar_lista(self):
        self.lista.blockSignals(True)
        self.lista.clear()
        self._items = {}
        if self.res and not self.res.cambios:
            it = QListWidgetItem(comparar.SIN_DIFERENCIAS if (self.res.opciones.texto and self.res.opciones.apariencia)
                                 else self.res.texto_resumen())
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            it.setIcon(icon("check", tema.color("ok")))
            self.lista.addItem(it)
        for c in (self.res.cambios if self.res else []):
            cab = f"{comparar.ubicacion(c)} · {comparar.NOMBRES[c.tipo]}"
            if c.zona == "margen":
                cab += " (encabezado o pie)"
            d = comparar.detalle(c, 70)
            it = QListWidgetItem(f"{cab}:\n{d}" if d else cab)
            it.setIcon(_punto(comparar.COLORES[c.tipo]))
            it.setData(Qt.ItemDataRole.UserRole, c.id)
            it.setToolTip(f"{c.id}. {comparar.texto_lista(c, 200)}" + (f"\n{c.contexto}" if c.contexto else ""))
            self.lista.addItem(it)
            self._items[c.id] = it
        self.lista.blockSignals(False)

    # ------------------------------------------------------------------ navegación
    def _orden(self) -> list[int]:
        return [self.lista.item(k).data(Qt.ItemDataRole.UserRole) for k in range(self.lista.count())
                if self.lista.item(k).data(Qt.ItemDataRole.UserRole) is not None]

    def _siguiente(self, desde: int | None, paso: int) -> int | None:
        orden = [cid for cid in self._orden() if not self._items[cid].isHidden()]
        if not orden:
            return None
        if desde is None or desde not in self._items:
            return orden[0] if paso > 0 else orden[-1]
        todos = self._orden()
        k = todos.index(desde) if desde in todos else -1
        rango = range(k + 1, len(todos)) if paso > 0 else range(k - 1, -1, -1)
        for j in rango:
            if not self._items[todos[j]].isHidden():
                return todos[j]
        return None

    def mover(self, paso: int):
        if self._pila.currentIndex() != 1 or not self._items:
            return
        actual = self.lista.currentItem()
        desde = actual.data(Qt.ItemDataRole.UserRole) if actual is not None else None
        sig = self._siguiente(desde, paso)
        if sig is None and desde is None:
            return
        if sig is None:               # al final (o al principio): vuelve a empezar, como en un buscador
            sig = self._siguiente(None, paso)
        if sig is not None:
            self.lista.setCurrentItem(self._items[sig])
            self.lista.scrollToItem(self._items[sig])

    def elegir(self, cid: int):
        """Clic en un resaltado: selecciona su cambio en la lista."""
        it = self._items.get(cid)
        if it is None:
            return
        if self.lista.currentItem() is it:
            self.vista.ir_a(self._cambios[cid], parpadear=True)
        else:
            self.lista.setCurrentItem(it)
        self.lista.scrollToItem(it)

    def _item_actual(self, actual, _anterior):
        if actual is None:
            return
        cid = actual.data(Qt.ItemDataRole.UserRole)
        if cid is not None and cid in self._cambios:
            self.vista.ir_a(self._cambios[cid])
        self._botones()

    def _item_clic(self, item):
        cid = item.data(Qt.ItemDataRole.UserRole)
        if cid is not None and cid in self._cambios and self.vista.seleccion is self._cambios[cid]:
            self.vista.ir_a(self._cambios[cid])          # volver a pulsar el mismo: vuelve a llevar y parpadea

    def _botones(self):
        hay = any(not it.isHidden() for it in self._items.values())
        self.b_ant.setEnabled(hay)
        self.b_sig.setEnabled(hay)
        self.b_informe.setEnabled(self.res is not None)

    # ------------------------------------------------------------------ nueva comparación
    def nueva_comparacion(self):
        self._soltar_docs()
        self.res = None
        self._cambios, self._items = {}, {}
        self.lista.clear()
        self._poner_chips()
        self.c_solo.setChecked(False)
        self._modo_resultado(False)
        self._refrescar()

    def _soltar_docs(self):
        self.vista.soltar()
        da, db = self._docs
        self._docs = (None, None)
        if da is not None or db is not None:
            with PDF_LOCK:
                for d in (da, db):
                    if d is not None:
                        d.close()

    def resultado(self) -> Resultado | None:
        return self.res

    # ------------------------------------------------------------------ informes
    def _proteccion(self) -> tuple[bool, str | None]:
        """Si alguno de los PDF tenía contraseña, avisa y ofrece proteger el informe.  (seguir, contraseña)"""
        pa, pb = self._claves
        if not (pa or pb):
            return True, None
        clave = pb or pa
        cual = "de la versión nueva" if pb else "de la versión anterior"
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Informe de la comparación")
        box.setText(f"<b style='font-size:11pt'>{AVISO_INFORME}</b>")
        box.setInformativeText("Uno de los PDF está protegido con contraseña. Si proteges el informe, pedirá la "
                               "misma contraseña para abrirlo.")
        casilla = QCheckBox(f"Proteger el informe con la contraseña {cual}")
        casilla.setChecked(True)
        box.setCheckBox(casilla)
        b_si = box.addButton("Guardar informe", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(b_si)
        box.exec()
        if box.clickedButton() is not b_si:
            return False, None
        return True, (clave if casilla.isChecked() else None)

    def guardar_informe(self, todas: bool = False):
        if self.res is None or not self._rutas or ctx.busy:
            return
        a, b = self._rutas
        out = widgets.ask_save(self, suggest_output(b, "_comparacion"), title="Guardar el informe de la comparación")
        if not out:
            return
        if os.path.normcase(os.path.abspath(out)) in {os.path.normcase(os.path.abspath(p)) for p in (a, b)}:
            QMessageBox.warning(self, "Informe de la comparación",
                                "Elige otro nombre: el informe no puede reemplazar uno de los documentos comparados.")
            return
        seguir, proteger = self._proteccion()
        if not seguir:
            return
        pa, pb = self._claves

        def listo(n):
            detalle = f"{_plural(n, 'página', 'páginas')} con las dos versiones enfrentadas y la lista de cambios."
            if proteger:
                detalle += " Está protegido con la misma contraseña."
            widgets.show_done(self, "Informe guardado", detalle, file=out)

        ctx.run_task("Preparando el informe…", comparar.informe_pdf, listo, path_a=a, path_b=b, resultado=self.res,
                     output=out, password_a=pa, password_b=pb, solo_con_cambios=not todas, proteger_con=proteger)

    def guardar_excel(self):
        if self.res is None or not self._rutas or ctx.busy:
            return
        a, b = self._rutas
        out, _ = QFileDialog.getSaveFileName(self, "Guardar la lista de cambios",
                                             suggest_output(b, "_comparacion", ".xlsx"), "Libro de Excel (*.xlsx)")
        if not out:
            return
        if not out.lower().endswith(".xlsx"):
            out += ".xlsx"
        ctx.remember_dir(out)

        def listo(_r):
            widgets.show_done(self, "Lista de cambios guardada",
                              f"{_plural(len(self.res.cambios), 'fila', 'filas')}: una por cada cambio.", file=out)

        ctx.run_task("Guardando la lista de cambios…", _lista_excel, listo, resultado=self.res, output=out,
                     nombre_a=Path(a).name, nombre_b=Path(b).name)


def _lista_excel(progress, resultado: Resultado, output: str, nombre_a: str, nombre_b: str) -> None:
    core._report(progress, 0, 1, "Guardando la lista de cambios…")
    comparar.informe_xlsx(resultado, output, nombre_a, nombre_b)


# --------------------------------------------------------------------------
# Visor y registro
# --------------------------------------------------------------------------

def comparar_desde_visor(visor) -> None:
    """«Comparar con otro PDF…» (menú «Más acciones» del visor): el PDF abierto queda como versión anterior."""
    path = getattr(getattr(visor, "canvas", None), "path", None)
    if path and callable(getattr(visor, "has_unsaved", None)) and visor.has_unsaved():
        QMessageBox.information(visor, "Comparar con otro PDF",
                                "Se compara el archivo guardado: los cambios que aún no guardaste no se incluyen.")
    page = extensiones.ir_a("compare")
    if page is not None and path:
        page.cargar_anterior(path)


ACCIONES_VISOR = [dict(icono="compare", ayuda="Comparar este PDF con otro", texto="Comparar con otro PDF…",
                       accion=comparar_desde_visor, solo_menu=True)]

REGISTRO = dict(clave="compare", titulo="Comparar dos PDF", seccion="ver", orden=20, pagina=CompararPage,
                dibujo=dibujo_comparar, inicio="Encuentra qué cambió entre dos versiones")
