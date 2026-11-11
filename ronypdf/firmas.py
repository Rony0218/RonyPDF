"""Firmas manuscritas: dibujadas, escritas con letra manuscrita o tomadas de una foto o escaneo.

Todas se convierten en un PNG con fondo transparente, recortado a la tinta.  Las firmas que la
persona decide guardar viven solo en este equipo, en la carpeta «RonyPDF_firmas» junto al
archivo de ajustes (es decir, junto al programa portable).
"""

from __future__ import annotations

import math
import secrets
import time
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QIcon, QImage, QImageReader, QPainter, QPainterPath,
                           QPen, QPixmap, QTransform)
from PySide6.QtWidgets import (QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton, QSizePolicy, QSlider, QTabWidget,
                               QToolButton, QVBoxLayout, QWidget)

from . import core
from .widgets import IMAGE_FILTER, ask_open, ctx, label, reveal

MAX_FIRMAS = 6
CARPETA = "RonyPDF_firmas"
FUENTES_MANUSCRITAS = ("Segoe Script", "Ink Free", "Lucida Handwriting")
COLOR_TINTA = "#1E3A5F"
TINTAS = (("Azul", "#1E3A5F"), ("Negra", "#111827"))
GROSORES = (("Fino", 1.6), ("Medio", 2.5), ("Grueso", 3.6))
AVISO_PRIVACIDAD = ("Tus firmas se guardan solo en este equipo, en la carpeta RonyPDF_firmas junto al "
                    "programa. Puedes borrarlas cuando quieras.")
_MAX_LADO = 1600


# --------------------------------------------------------------------------
# Creación de firmas (sin diálogos)
# --------------------------------------------------------------------------

def fuentes_disponibles() -> list[str]:
    """Letras manuscritas instaladas en este equipo (solo las que existen)."""
    familias = set(QFontDatabase.families())
    return [f for f in FUENTES_MANUSCRITAS if f in familias]


def _png(img: QImage) -> bytes:
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


def _filas_con_tinta(alfa: QImage) -> list[int]:
    """Índices de las filas que tienen algún píxel visible (alfa en formato Alpha8/Grayscale8)."""
    w, bpl = alfa.width(), alfa.bytesPerLine()
    data = bytes(alfa.constBits())
    return [y for y in range(alfa.height()) if data[y * bpl: y * bpl + w].strip(b"\x00")]


def _recortar(img: QImage, margen: int = 6) -> QImage:
    """Recorta la imagen a la tinta visible (más un margen)."""
    img = img.convertToFormat(QImage.Format.Format_ARGB32)
    alfa = img.convertToFormat(QImage.Format.Format_Alpha8)
    # Ruido casi invisible (polvo del escaneo) no cuenta como tinta.
    lut = bytes(0 if v < 24 else v for v in range(256))
    datos = bytes(alfa.constBits()).translate(lut)
    alfa = QImage(datos, alfa.width(), alfa.height(), alfa.bytesPerLine(), QImage.Format.Format_Alpha8).copy()
    filas = _filas_con_tinta(alfa)
    if not filas:
        raise core.UserError("La imagen no tiene trazos visibles: prueba con otra foto o desmarca "
                             "«Quitar el fondo blanco».")
    traspuesta = alfa.transformed(QTransform(0, 1, 1, 0, 0, 0))       # las columnas pasan a ser filas
    columnas = _filas_con_tinta(traspuesta.convertToFormat(QImage.Format.Format_Alpha8))
    x0, x1 = max(0, columnas[0] - margen), min(img.width() - 1, columnas[-1] + margen)
    y0, y1 = max(0, filas[0] - margen), min(img.height() - 1, filas[-1] + margen)
    return img.copy(x0, y0, x1 - x0 + 1, y1 - y0 + 1)


def firma_desde_trazos(trazos: list[list[tuple[float, float]]], grosor: float = 2.5,
                       color: str = COLOR_TINTA) -> bytes:
    """PNG transparente con los trazos dibujados (suavizados), recortado a la tinta."""
    trazos = [[(float(x), float(y)) for x, y in t] for t in trazos if t]
    puntos = [p for t in trazos for p in t]
    if not puntos:
        raise core.UserError("Dibuja tu firma en el recuadro antes de continuar.")
    escala = 3.0                                   # más resolución que la pantalla: se ve nítida al imprimir
    minx, maxx = min(p[0] for p in puntos), max(p[0] for p in puntos)
    miny, maxy = min(p[1] for p in puntos), max(p[1] for p in puntos)
    pad = grosor * escala + 4
    w = max(1, math.ceil((maxx - minx) * escala + 2 * pad))
    h = max(1, math.ceil((maxy - miny) * escala + 2 * pad))
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), grosor * escala)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)

    def m(pt):
        return QPointF((pt[0] - minx) * escala + pad, (pt[1] - miny) * escala + pad)

    for t in trazos:
        if len(t) == 1:
            p.drawPoint(m(t[0]))
            continue
        path = QPainterPath(m(t[0]))
        for a, b in zip(t[1:-1], t[2:]):           # curvas por los puntos medios: trazo suave
            pa, pb = m(a), m(b)
            path.quadTo(pa, (pa + pb) / 2)
        path.lineTo(m(t[-1]))
        p.drawPath(path)
    p.end()
    return _png(img)


def firma_desde_texto(nombre: str, fuente: str, color: str = COLOR_TINTA) -> bytes:
    """PNG transparente con el nombre escrito en letra manuscrita."""
    nombre = (nombre or "").strip()
    if not nombre:
        raise core.UserError("Escribe tu nombre para crear la firma.")
    f = QFont(fuente)
    f.setPixelSize(120)
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.WindingFill)      # las letras que se cruzan no dejan huecos
    path.addText(0, 0, f, nombre)
    r = path.boundingRect()
    pad = 10
    img = QImage(max(1, math.ceil(r.width() + 2 * pad)), max(1, math.ceil(r.height() + 2 * pad)),
                 QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.translate(pad - r.x(), pad - r.y())
    p.fillPath(path, QColor(color))
    p.end()
    return _png(img)


def _alfa(v: int, umbral: int) -> int:
    """Gris (0 negro … 255 blanco) → opacidad: lo claro desaparece y la tinta queda opaca."""
    if v >= umbral:
        return 0
    lo = max(0, umbral - 140)
    if v <= lo:
        return 255
    return int(round((umbral - v) * 255 / (umbral - lo)))


def firma_desde_imagen(path, quitar_fondo: bool = True, umbral: int = 200) -> bytes:
    """Firma tomada de una foto o un escaneo; con ``quitar_fondo`` el papel blanco se vuelve transparente."""
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)                  # respeta la orientación de las fotos del celular
    img = reader.read()
    if img.isNull():
        raise core.UserError(f"No se pudo leer la imagen «{Path(path).name}».")
    if max(img.width(), img.height()) > _MAX_LADO:
        img = img.scaled(_MAX_LADO, _MAX_LADO, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    img = img.convertToFormat(QImage.Format.Format_ARGB32)
    if quitar_fondo:
        umbral = max(60, min(250, int(umbral)))
        gris = img.convertToFormat(QImage.Format.Format_Grayscale8)
        # Tabla de 256 valores aplicada de una vez (bytes.translate): sin bucles por píxel.
        tabla = bytes(_alfa(v, umbral) for v in range(256))
        datos = bytes(gris.constBits()).translate(tabla)
        alfa = QImage(datos, gris.width(), gris.height(), gris.bytesPerLine(), QImage.Format.Format_Grayscale8).copy()
        img.setAlphaChannel(alfa)
    return _png(_recortar(img))


# --------------------------------------------------------------------------
# Firmas guardadas en el equipo
# --------------------------------------------------------------------------

class AlmacenFirmas:
    """Firmas guardadas como PNG en una carpeta local (máximo seis)."""

    def __init__(self, carpeta=None):
        if carpeta is None:
            if ctx.settings is not None:
                base = Path(ctx.settings.path).parent
            else:
                from .settings import Settings
                base = Path(Settings().path).parent
            carpeta = base / CARPETA
        self.carpeta = Path(carpeta)

    def listar(self) -> list[Path]:
        if not self.carpeta.is_dir():
            return []
        try:
            files = [p for p in self.carpeta.glob("*.png") if p.is_file()]
            return sorted(files, key=lambda p: (p.stat().st_mtime, p.name), reverse=True)
        except OSError:
            return []

    def lleno(self) -> bool:
        return len(self.listar()) >= MAX_FIRMAS

    def guardar(self, png: bytes) -> Path:
        if not png or not png.startswith(b"\x89PNG"):
            raise core.UserError("La firma no es una imagen PNG válida.")
        if self.lleno():
            raise core.UserError(f"Ya tienes {MAX_FIRMAS} firmas guardadas. Borra una para guardar otra.")
        destino = self.carpeta / f"firma_{time.strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(2)}.png"
        try:
            with core._output(destino) as tmp:
                Path(tmp).write_bytes(png)
        except OSError as exc:
            raise core.UserError(f"No se pudo guardar la firma en «{self.carpeta}».") from exc
        return destino

    def borrar(self, path) -> None:
        p = Path(path)
        try:
            dentro = p.resolve().parent == self.carpeta.resolve()
        except OSError:
            dentro = False
        if not dentro:
            raise core.UserError("Esa firma no está en la carpeta de firmas.")
        try:
            p.unlink(missing_ok=True)
        except OSError as exc:
            raise core.UserError(f"No se pudo borrar «{p.name}».") from exc


def miniatura(path_o_png, ancho: int = 120, alto: int = 44) -> QPixmap:
    """Miniatura de una firma sobre fondo blanco (para menús y listas)."""
    img = QImage.fromData(path_o_png) if isinstance(path_o_png, (bytes, bytearray)) else QImage(str(path_o_png))
    dpr = 2
    pm = QPixmap(ancho * dpr, alto * dpr)
    pm.fill(QColor("white"))
    pm.setDevicePixelRatio(dpr)
    if not img.isNull():
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        s = img.scaled(int((ancho - 8) * dpr), int((alto - 6) * dpr), Qt.AspectRatioMode.KeepAspectRatio,
                       Qt.TransformationMode.SmoothTransformation)
        s.setDevicePixelRatio(dpr)
        p.drawImage(QPointF((ancho - s.width() / dpr) / 2, (alto - s.height() / dpr) / 2), s)
        p.end()
    return pm


# --------------------------------------------------------------------------
# Diálogos
# --------------------------------------------------------------------------

class PanelFirma(QWidget):
    """Recuadro para firmar con el ratón, la pantalla táctil o un lápiz."""
    cambio = Signal()

    def __init__(self):
        super().__init__()
        self.trazos: list[list[tuple[float, float]]] = []
        self.grosor = 2.5
        self.color = QColor(COLOR_TINTA)
        self._trazando = False
        self.setMinimumSize(520, 190)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_StaticContents)
        self.setAccessibleName("Recuadro para dibujar la firma")

    def borrar(self):
        self.trazos = []
        self.update()
        self.cambio.emit()

    def vacio(self) -> bool:
        return not any(self.trazos)

    def _empezar(self, pos: QPointF):
        self._trazando = True
        self.trazos.append([(pos.x(), pos.y())])
        self.update()

    def _seguir(self, pos: QPointF):
        if self._trazando and self.trazos:
            last = self.trazos[-1][-1]
            if abs(pos.x() - last[0]) + abs(pos.y() - last[1]) >= 0.8:
                self.trazos[-1].append((pos.x(), pos.y()))
                self.update()

    def _terminar(self):
        if self._trazando:
            self._trazando = False
            self.cambio.emit()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._empezar(e.position())

    def mouseMoveEvent(self, e):
        self._seguir(e.position())

    def mouseReleaseEvent(self, e):
        self._terminar()

    def tabletEvent(self, e):
        t = e.type()
        if t == e.Type.TabletPress:
            self._empezar(e.position())
        elif t == e.Type.TabletMove:
            self._seguir(e.position())
        elif t == e.Type.TabletRelease:
            self._terminar()
        e.accept()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor("#D5D8DD"), 1))
        p.setBrush(QColor("white"))
        p.drawRoundedRect(r, 12, 12)
        base = r.top() + r.height() * 0.72
        p.setPen(QPen(QColor("#C9CED6"), 1.2))
        p.drawLine(QPointF(r.left() + 36, base), QPointF(r.right() - 24, base))
        p.setPen(QPen(QColor("#9AA1AC"), 1.6))
        p.drawLine(QPointF(r.left() + 16, base - 12), QPointF(r.left() + 26, base - 2))
        p.drawLine(QPointF(r.left() + 26, base - 12), QPointF(r.left() + 16, base - 2))
        if self.vacio():
            p.setPen(QColor("#9AA1AC"))
            p.drawText(r.adjusted(0, 0, 0, -r.height() * 0.4), Qt.AlignmentFlag.AlignCenter,
                       "Firma aquí con el ratón, el dedo o un lápiz")
        pen = QPen(self.color, self.grosor)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        for t in self.trazos:
            if len(t) == 1:
                p.drawPoint(QPointF(*t[0]))
                continue
            path = QPainterPath(QPointF(*t[0]))
            for a, b in zip(t[1:-1], t[2:]):
                pa, pb = QPointF(*a), QPointF(*b)
                path.quadTo(pa, (pa + pb) / 2)
            path.lineTo(QPointF(*t[-1]))
            p.drawPath(path)
        p.end()


def _seg(texto: str, checkable: bool = True) -> QToolButton:
    b = QToolButton()
    b.setObjectName("SegButton")
    b.setText(texto)
    b.setCheckable(checkable)
    b.setAutoExclusive(checkable)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def _grupo(*botones) -> QFrame:
    box = QFrame()
    box.setObjectName("SegGroup")
    lay = QHBoxLayout(box)
    lay.setContentsMargins(3, 3, 3, 3)
    lay.setSpacing(2)
    for b in botones:
        lay.addWidget(b)
    return box


class _Ajedrez(QLabel):
    """Vista previa de una imagen con transparencia sobre un fondo de cuadros."""

    def __init__(self):
        super().__init__()
        self.setMinimumSize(520, 140)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.img: QImage | None = None
        self.texto = "Elige una foto o un escaneo de tu firma"

    def set_png(self, png: bytes | None):
        self.img = QImage.fromData(png) if png else None
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        r = self.rect()
        s = 10
        for y in range(0, r.height(), s):
            for x in range(0, r.width(), s):
                p.fillRect(x, y, s, s, QColor("#FFFFFF" if (x // s + y // s) % 2 else "#EEF0F3"))
        p.setPen(QPen(QColor("#D5D8DD"), 1))
        p.drawRect(r.adjusted(0, 0, -1, -1))
        if self.img is not None and not self.img.isNull():
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            area = QRectF(r).adjusted(14, 14, -14, -14)
            sz = self.img.size().scaled(area.size().toSize(), Qt.AspectRatioMode.KeepAspectRatio)
            dst = QRectF(area.center().x() - sz.width() / 2, area.center().y() - sz.height() / 2,
                         sz.width(), sz.height())
            p.drawImage(dst, self.img)
        else:
            p.setPen(QColor("#6B7280"))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.texto)
        p.end()


class DialogoFirma(QDialog):
    """Crear una firma: dibujarla, escribirla en letra manuscrita o tomarla de una imagen."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Nueva firma")
        self.setModal(True)
        self.png: bytes | None = None
        self.guardar = True
        self.ruta_imagen: str | None = None
        self.color = COLOR_TINTA
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(12)
        lay.addWidget(label("Crea tu firma", "PageTitle"))
        lay.addWidget(label("Dibújala, escríbela con letra manuscrita o tómala de una foto. "
                            "Después la pones con un clic donde haga falta.", "PageSubtitle", wrap=True))
        self.tabs = QTabWidget()
        self.tabs.addTab(self._tab_dibujar(), "Dibujar")
        self.tabs.addTab(self._tab_escribir(), "Escribir")
        self.tabs.addTab(self._tab_imagen(), "Imagen")
        lay.addWidget(self.tabs, 1)
        self._actualizar_fuentes()

        self.fila_tinta = QWidget()
        fila = QHBoxLayout(self.fila_tinta)
        fila.setContentsMargins(0, 0, 0, 0)
        fila.addWidget(QLabel("Tinta:"))
        self.b_tintas = []
        for nombre, hexa in TINTAS:
            b = _seg(nombre)
            b.setChecked(hexa == self.color)
            b.clicked.connect(lambda _=False, h=hexa: self._set_color(h))
            self.b_tintas.append(b)
        fila.addWidget(_grupo(*self.b_tintas))
        fila.addStretch(1)
        lay.addWidget(self.fila_tinta)
        # la tinta no aplica a una foto: conserva sus colores
        self.tabs.currentChanged.connect(lambda i: self.fila_tinta.setVisible(i != 2))

        self.chk_guardar = QCheckBox("Guardar esta firma para usarla después")
        self.chk_guardar.setChecked(True)
        lay.addWidget(self.chk_guardar)
        aviso = label(AVISO_PRIVACIDAD, "Hint", wrap=True)
        lay.addWidget(aviso)

        botones = QHBoxLayout()
        botones.addStretch(1)
        b_cancel = QPushButton("Cancelar")
        b_cancel.setObjectName("Secondary")
        b_cancel.clicked.connect(self.reject)
        self.b_ok = QPushButton("Usar esta firma")
        self.b_ok.setObjectName("Primary")
        self.b_ok.setDefault(True)
        self.b_ok.clicked.connect(self._aceptar)
        botones.addWidget(b_cancel)
        botones.addWidget(self.b_ok)
        lay.addLayout(botones)
        minimo = self.minimumSizeHint()
        self.resize(max(640, minimo.width()), max(640, minimo.height()))

    # ------------------------------------------------------------------ pestañas
    def _tab_dibujar(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(12, 12, 12, 12)
        self.panel = PanelFirma()
        v.addWidget(self.panel, 1)
        fila = QHBoxLayout()
        b_borrar = QPushButton("Borrar")
        b_borrar.setObjectName("Secondary")
        b_borrar.clicked.connect(self.panel.borrar)
        fila.addWidget(b_borrar)
        fila.addSpacing(10)
        fila.addWidget(QLabel("Grosor:"))
        self.b_grosor = []
        for nombre, g in GROSORES:
            b = _seg(nombre)
            b.setChecked(g == self.panel.grosor)
            b.clicked.connect(lambda _=False, gg=g: self._set_grosor(gg))
            self.b_grosor.append(b)
        fila.addWidget(_grupo(*self.b_grosor))
        fila.addStretch(1)
        v.addLayout(fila)
        return w

    def _tab_escribir(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(12, 12, 12, 12)
        self.nombre = QLineEdit()
        self.nombre.setPlaceholderText("Escribe tu nombre completo")
        self.nombre.textChanged.connect(self._actualizar_fuentes)
        v.addWidget(self.nombre)
        self.fuentes = QListWidget()
        self.fuentes.setObjectName("ListaFuentes")
        self.fuentes.setStyleSheet("QListWidget#ListaFuentes { selection-background-color: #EEF3FE; "
                                   "selection-color: #1E3A5F; } QListWidget#ListaFuentes::item { padding: 8px 10px; "
                                   "border-radius: 8px; } QListWidget#ListaFuentes::item:selected { "
                                   "border: 1px solid #9DB8F0; }")
        disponibles = fuentes_disponibles()
        for fam in disponibles:
            it = QListWidgetItem(fam)
            it.setData(Qt.ItemDataRole.UserRole, fam)
            f = QFont(fam)
            f.setPointSize(20)
            it.setFont(f)
            self.fuentes.addItem(it)
        if disponibles:
            self.fuentes.setCurrentRow(0)
        v.addWidget(self.fuentes, 1)
        if not disponibles:
            v.addWidget(label("Este equipo no tiene letras manuscritas instaladas. Usa «Dibujar» o «Imagen».",
                              "Hint", wrap=True))
        return w

    def _tab_imagen(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(12, 12, 12, 12)
        fila = QHBoxLayout()
        b = QPushButton("Elegir foto o escaneo…")
        b.setObjectName("Secondary")
        b.clicked.connect(self._elegir_imagen)
        fila.addWidget(b)
        self.lbl_imagen = label("", "Hint")
        fila.addWidget(self.lbl_imagen, 1)
        v.addLayout(fila)
        self.vista = _Ajedrez()
        v.addWidget(self.vista, 1)
        self.chk_fondo = QCheckBox("Quitar el fondo blanco")
        self.chk_fondo.setChecked(True)
        self.chk_fondo.toggled.connect(self._actualizar_imagen)
        v.addWidget(self.chk_fondo)
        fila2 = QHBoxLayout()
        fila2.addWidget(label("Limpieza del fondo:", None))
        self.umbral = QSlider(Qt.Orientation.Horizontal)
        self.umbral.setRange(120, 245)
        self.umbral.setValue(200)
        self.umbral.setToolTip("Súbela si el papel se ve gris en la foto; bájala si se borran partes de la firma.")
        self.umbral.valueChanged.connect(self._actualizar_imagen)
        fila2.addWidget(self.umbral, 1)
        v.addLayout(fila2)
        return w

    # ------------------------------------------------------------------ acciones
    def _set_grosor(self, g: float):
        self.panel.grosor = g
        self.panel.update()

    def _set_color(self, hexa: str):
        self.color = hexa
        self.panel.color = QColor(hexa)
        self.panel.update()
        self._actualizar_fuentes()

    def _actualizar_fuentes(self, *_):
        texto = self.nombre.text().strip() or "Tu nombre"
        for i in range(self.fuentes.count()):
            it = self.fuentes.item(i)
            it.setText(texto)
            it.setForeground(QColor(self.color))
            it.setToolTip(it.data(Qt.ItemDataRole.UserRole))

    def _elegir_imagen(self):
        paths = ask_open(self, False, IMAGE_FILTER, "Elige una foto o un escaneo de tu firma")
        if paths:
            self.cargar_imagen(paths[0])

    def cargar_imagen(self, path: str):
        self.ruta_imagen = path
        self.lbl_imagen.setText(Path(path).name)
        self._actualizar_imagen()

    def _actualizar_imagen(self, *_):
        if not self.ruta_imagen:
            return
        try:
            png = firma_desde_imagen(self.ruta_imagen, self.chk_fondo.isChecked(), self.umbral.value())
        except core.UserError as exc:
            self.vista.texto = str(exc)
            png = None
        self.vista.set_png(png)

    def crear_png(self) -> bytes:
        """La firma de la pestaña activa como PNG (lanza UserError si falta algo)."""
        tab = self.tabs.currentIndex()
        if tab == 0:
            return firma_desde_trazos(self.panel.trazos, self.panel.grosor, self.color)
        if tab == 1:
            it = self.fuentes.currentItem()
            if it is None:
                raise core.UserError("No hay letras manuscritas en este equipo: usa «Dibujar» o «Imagen».")
            return firma_desde_texto(self.nombre.text(), it.data(Qt.ItemDataRole.UserRole), self.color)
        if not self.ruta_imagen:
            raise core.UserError("Elige primero una foto o un escaneo de tu firma.")
        return firma_desde_imagen(self.ruta_imagen, self.chk_fondo.isChecked(), self.umbral.value())

    def _aceptar(self):
        try:
            self.png = self.crear_png()
        except core.UserError as exc:
            QMessageBox.warning(self, "Firma", str(exc))
            return
        self.guardar = self.chk_guardar.isChecked()
        self.accept()


class DialogoAdministrar(QDialog):
    """Ver y borrar las firmas guardadas en este equipo."""

    def __init__(self, almacen: AlmacenFirmas, parent=None):
        super().__init__(parent)
        self.almacen = almacen
        self.setWindowTitle("Firmas guardadas")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(10)
        lay.addWidget(label("Firmas guardadas", "PageTitle"))
        lay.addWidget(label(AVISO_PRIVACIDAD, "Hint", wrap=True))
        self.lista = QListWidget()
        self.lista.setIconSize(QSize(150, 54))
        self.lista.setObjectName("ListaFirmas")
        self.lista.setStyleSheet("QListWidget#ListaFirmas::item { padding: 6px; }")
        lay.addWidget(self.lista, 1)
        fila = QHBoxLayout()
        self.b_borrar = QPushButton("Borrar la seleccionada")
        self.b_borrar.setObjectName("Danger")
        self.b_borrar.clicked.connect(self.borrar_seleccionada)
        fila.addWidget(self.b_borrar)
        b_carpeta = QPushButton("Abrir la carpeta")
        b_carpeta.setObjectName("Secondary")
        b_carpeta.clicked.connect(self._abrir_carpeta)
        fila.addWidget(b_carpeta)
        fila.addStretch(1)
        b_cerrar = QPushButton("Cerrar")
        b_cerrar.setObjectName("Primary")
        b_cerrar.clicked.connect(self.accept)
        fila.addWidget(b_cerrar)
        lay.addLayout(fila)
        self.lista.itemSelectionChanged.connect(lambda: self.b_borrar.setEnabled(bool(self.lista.selectedItems())))
        self.recargar()
        self.resize(460, 440)

    def recargar(self):
        self.lista.clear()
        firmas = self.almacen.listar()
        for k, path in enumerate(firmas, 1):
            it = QListWidgetItem(QIcon(miniatura(path, 150, 54)), f"Firma {k}")
            it.setData(Qt.ItemDataRole.UserRole, str(path))
            it.setToolTip(time.strftime("Guardada el %d/%m/%Y a las %H:%M", time.localtime(path.stat().st_mtime)))
            self.lista.addItem(it)
        if not firmas:
            it = QListWidgetItem("Todavía no tienes firmas guardadas.")
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            self.lista.addItem(it)
        self.b_borrar.setEnabled(False)

    def borrar_seleccionada(self, confirmar: bool = True):
        items = self.lista.selectedItems()
        if not items or not items[0].data(Qt.ItemDataRole.UserRole):
            return
        if confirmar:
            r = QMessageBox.question(self, "Borrar firma", "¿Borrar esta firma de este equipo? No se puede deshacer.")
            if r != QMessageBox.StandardButton.Yes:
                return
        try:
            self.almacen.borrar(items[0].data(Qt.ItemDataRole.UserRole))
        except core.UserError as exc:
            QMessageBox.warning(self, "Firma", str(exc))
        self.recargar()

    def _abrir_carpeta(self):
        self.almacen.carpeta.mkdir(parents=True, exist_ok=True)
        reveal(str(self.almacen.carpeta))
