"""Pantallas «Acciones por lotes» (Optimizar) y «Renombrar por contenido» (Organizar).

La lógica vive en ``lotes.py`` y ``renombrar.py``; aquí solo hay interfaz.  Las dos herramientas se
registran con ``REGISTRO`` (ver ``extensiones.py``).
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from pathlib import Path

import pymupdf
from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame,
                               QGridLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMenu, QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSpinBox,
                               QStackedWidget, QStyle, QStyledItemDelegate, QToolButton, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from . import core, extensiones, lotes, oficina, renombrar, tema
from .icons import _doc, _line, icon, pixmap
from .widgets import (PDF_LOCK, DropArea, FileCardDelegate, FileList, FlowLayout, Note, OptionGroup, Segmented,
                      SwitchRow, ToolPage, ask_dir, ask_open, cat_de, ctx, label, pintar_miniatura, reveal,
                      show_done, view_in_app)

# Colores de los estados (del tema: nada escrito a mano).  AMBAR es el fondo de las filas con un aviso.
AMBAR = QColor(tema.color("warn-tint"))
ROJO = tema.color("danger")
VERDE = tema.color("ok")
GRIS = tema.color("ink-3")
TINTA = tema.color("ink")
COLOR_ESTADO = {"hecho": VERDE, "con aviso": tema.color("warn"), "omitido": GRIS, "error": ROJO}
TONO_ESTADO = {"hecho": "ok", "con aviso": "warn", "omitido": "neutral", "error": "danger"}
TIPOS_LOTE = set(core.PDF_EXTS) | lotes.TIPOS_CONVERTIBLES
FILTRO_LOTE = (f"PDF, Office e imágenes ({' '.join('*' + e for e in sorted(TIPOS_LOTE))});;"
               "Documentos PDF (*.pdf)")
_PRUEBAS: list[str] = []          # carpetas temporales de «Probar con el primer archivo»


def _s(n: int, uno: str, varios: str) -> str:
    return f"{n} {uno if n == 1 else varios}"


# --------------------------------------------------------------------------
# Íconos (cuadrícula de 24 × 24, de línea, con un detalle rojo)
# --------------------------------------------------------------------------

def dibujo_lotes(p: QPainter, base: QColor, acento: QColor) -> None:
    """Varias hojas; en la de adelante, una lista de pasos con el último en rojo."""
    p.drawPolyline([QPointF(5, 6.5), QPointF(5, 21.5), QPointF(16.5, 21.5)])
    _doc(p, 8, 2.5, 13, 16, 3.5)
    for y in (8.0, 11.5):
        p.drawEllipse(QPointF(11.2, y), 0.9, 0.9)
        _line(p, 13.4, y, 17.8, y)
    p.save()
    extensiones.lapiz(p, acento)
    p.setBrush(acento)
    p.drawEllipse(QPointF(11.2, 15.0), 0.9, 0.9)
    p.setBrush(Qt.BrushStyle.NoBrush)
    _line(p, 13.4, 15.0, 16.5, 15.0)
    p.restore()


def dibujo_renombrar(p: QPainter, base: QColor, acento: QColor) -> None:
    """Una hoja y, debajo, la etiqueta de su nombre con el cursor de escritura en rojo."""
    _doc(p, 6, 2, 12, 11.5, 3.2)
    _line(p, 8.5, 6.5, 13, 6.5)
    _line(p, 8.5, 9.5, 15.5, 9.5)
    p.drawRoundedRect(QRectF(2.5, 15.5, 19, 6), 1.6, 1.6)
    _line(p, 5, 18.5, 12.5, 18.5)
    p.save()
    extensiones.lapiz(p, acento)
    _line(p, 16, 16.9, 16, 20.1)
    _line(p, 14.9, 16.9, 17.1, 16.9)
    _line(p, 14.9, 20.1, 17.1, 20.1)
    p.restore()


# --------------------------------------------------------------------------
# Diálogos (funciones del módulo: las pruebas las reemplazan)
# --------------------------------------------------------------------------

def _botones(dlg: QDialog, si: str, no: str = "Cancelar") -> tuple[QHBoxLayout, QPushButton]:
    fila = QHBoxLayout()
    fila.addStretch(1)
    b_no = QPushButton(no)
    b_no.setObjectName("Secondary")
    b_no.clicked.connect(dlg.reject)
    b_si = QPushButton(si)
    b_si.setObjectName("Primary")
    b_si.setDefault(True)
    b_si.clicked.connect(dlg.accept)
    fila.addWidget(b_no)
    fila.addWidget(b_si)
    return fila, b_si


def pedir_clave(parent, nombre: str, error: bool = False, varios: bool = False) -> tuple[str | None, bool]:
    """Contraseña de un PDF protegido del lote: (contraseña o None si se omite, usarla para los demás)."""
    dlg = QDialog(parent)
    dlg.setWindowTitle("Contraseña requerida")
    v = QVBoxLayout(dlg)
    v.setContentsMargins(20, 18, 20, 16)
    v.setSpacing(10)
    texto = (f"La contraseña no es correcta para «{nombre}». Inténtalo de nuevo:" if error else
             f"«{nombre}» está protegido con contraseña. Escríbela para incluirlo en el lote:")
    v.addWidget(label(texto, wrap=True))
    campo = QLineEdit()
    campo.setEchoMode(QLineEdit.EchoMode.Password)
    v.addWidget(campo)
    todos = QCheckBox("Usar esta contraseña para los demás protegidos")
    todos.setChecked(True)
    todos.setVisible(varios)
    v.addWidget(todos)
    v.addWidget(label("Si lo omites, ese archivo queda sin procesar y el lote sigue con los demás.", "Hint", wrap=True))
    fila, _ = _botones(dlg, "Continuar", "Omitir este archivo")
    v.addLayout(fila)
    dlg.setMinimumWidth(420)
    if dlg.exec() == QDialog.DialogCode.Accepted and campo.text():
        return campo.text(), varios and todos.isChecked()
    return None, False


def pedir_clave_nueva(parent, titulo: str = "Proteger con contraseña") -> str | None:
    """Contraseña nueva para el paso «Proteger» (dos campos que deben coincidir)."""
    dlg = QDialog(parent)
    dlg.setWindowTitle(titulo)
    v = QVBoxLayout(dlg)
    v.setContentsMargins(20, 18, 20, 16)
    v.setSpacing(10)
    v.addWidget(label("Escribe la contraseña que pedirán los archivos terminados. No se guarda en el flujo: "
                      "anótala en un lugar seguro.", wrap=True))
    uno, dos = QLineEdit(), QLineEdit()
    for e in (uno, dos):
        e.setEchoMode(QLineEdit.EchoMode.Password)
    v.addWidget(_campo("Contraseña", uno))
    v.addWidget(_campo("Confírmala", dos))
    ver = QCheckBox("Mostrar contraseña")
    v.addWidget(ver)
    aviso = label("", "Hint")
    v.addWidget(aviso)
    fila, b_si = _botones(dlg, "Proteger")
    v.addLayout(fila)

    def revisar():
        ok = bool(uno.text()) and uno.text() == dos.text()
        b_si.setEnabled(ok)
        aviso.setText("Las contraseñas no coinciden" if dos.text() and not ok else "")

    def mostrar(on):
        for e in (uno, dos):
            e.setEchoMode(QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password)

    uno.textChanged.connect(revisar)
    dos.textChanged.connect(revisar)
    ver.toggled.connect(mostrar)
    revisar()
    dlg.setMinimumWidth(420)
    if dlg.exec() == QDialog.DialogCode.Accepted and uno.text() and uno.text() == dos.text():
        return uno.text()
    return None


def editar_opciones(parent, paso: lotes.Paso, valores: dict) -> dict | None:
    dlg = DialogoOpciones(paso, valores, parent)
    if dlg.exec() == QDialog.DialogCode.Accepted:
        return dlg.valores()
    return None


def pedir_nombre_flujo(parent, sugerido: str) -> str | None:
    dlg = QInputDialog(parent)
    dlg.setWindowTitle("Guardar flujo")
    dlg.setLabelText("Nombre del flujo:")
    dlg.setTextValue(sugerido)
    dlg.setOkButtonText("Guardar")
    dlg.setCancelButtonText("Cancelar")
    if dlg.exec() == QDialog.DialogCode.Accepted and dlg.textValue().strip():
        return dlg.textValue().strip()
    return None


def confirmar(parent, titulo: str, texto: str, si: str, no: str = "Cancelar", detalle: str = "") -> bool:
    box = QMessageBox(parent)
    box.setWindowTitle(titulo)
    box.setIcon(QMessageBox.Icon.Question)
    box.setText(texto)
    if detalle:
        box.setInformativeText(detalle)
    b_si = box.addButton(si, QMessageBox.ButtonRole.AcceptRole)
    box.addButton(no, QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(b_si)
    box.exec()
    return box.clickedButton() is b_si


def avisar(parent, titulo: str, texto: str) -> None:
    QMessageBox.warning(parent, titulo, texto)


def pedir_ruta_informe(parent, sugerido: str) -> str | None:
    """«Guardar como» para .xlsx o .txt (widgets.ask_save agregaría .pdf)."""
    ruta, filtro = QFileDialog.getSaveFileName(parent, "Guardar informe", sugerido,
                                               "Libro de Excel (*.xlsx);;Texto (*.txt)")
    if not ruta:
        return None
    if not ruta.lower().endswith((".xlsx", ".txt")):
        ruta += ".txt" if "txt" in (filtro or "") else ".xlsx"
    ctx.remember_dir(ruta)
    return ruta


def elegir_carpeta(parent, titulo: str = "Elige la carpeta") -> str | None:
    return ask_dir(parent, ctx.last_dir, titulo)


def elegir_archivos(parent, filtro: str = FILTRO_LOTE) -> list[str]:
    return ask_open(parent, True, filtro, "Elige los archivos")


def pedir_contrasenas(parent, rutas: list[str]) -> dict[str, str]:
    """Contraseñas de los PDF protegidos: usa las ya conocidas y pregunta las demás (con la opción de usar la
    misma para todos).  Los que se omiten no aparecen en el resultado."""
    out: dict[str, str] = {}
    comun: str | None = None
    for ruta in rutas:
        pw = ctx.passwords.get(ruta)
        if pw:
            out[ruta] = pw
            continue
        if comun:
            with PDF_LOCK:
                vale = core.check_password(ruta, comun)
            if vale:
                out[ruta] = comun
                ctx.passwords.set(ruta, comun)
                continue
        error = False
        while True:
            clave, todos = pedir_clave(parent, Path(ruta).name, error, varios=len(rutas) > 1)
            if clave is None:
                break
            with PDF_LOCK:
                vale = core.check_password(ruta, clave)
            if vale:
                out[ruta] = clave
                ctx.passwords.set(ruta, clave)
                if todos:
                    comun = clave
                break
            error = True
    return out


# --------------------------------------------------------------------------
# Piezas
# --------------------------------------------------------------------------

class _Fondo(QStyledItemDelegate):
    """Pinta el color de fondo de la fila (ámbar): con el `::item` de la hoja de estilos, Qt no lo pinta."""

    def paint(self, painter, option, index):
        fondo = index.data(Qt.ItemDataRole.BackgroundRole)
        if fondo is not None:
            color = fondo.color() if hasattr(fondo, "color") else QColor(fondo)
            if color.alpha():
                painter.fillRect(option.rect, color)
        super().paint(painter, option, index)


class _NoEditar(_Fondo):
    def createEditor(self, *args):
        return None


class _Elidido(QLabel):
    """Etiqueta de un renglón que se acorta con «…» cuando no cabe (el texto completo va en la ayuda)."""

    def __init__(self, texto: str = "", nombre: str | None = None):
        super().__init__()
        if nombre:
            self.setObjectName(nombre)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(10)
        self._texto = ""
        self.poner(texto)

    def poner(self, texto: str) -> None:
        self._texto = texto
        self.setToolTip(texto)
        self._ajustar()

    def _ajustar(self):
        self.setText(self.fontMetrics().elidedText(self._texto, Qt.TextElideMode.ElideRight, max(10, self.width())))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._ajustar()


class DialogoOpciones(QDialog):
    """Opciones de un paso, generadas a partir de sus ``Opcion``."""

    def __init__(self, paso: lotes.Paso, valores: dict, parent=None):
        super().__init__(parent)
        self.paso = paso
        self.setWindowTitle(f"Opciones: {paso.titulo}")
        self.setMinimumWidth(470)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 18)
        v.setSpacing(12)
        v.addWidget(label(paso.titulo, "PanelTitle"))
        if paso.descripcion:
            v.addWidget(label(paso.descripcion, "BodySm", wrap=True))
        self.campos: dict[str, object] = {}
        valores = paso.completar(valores)
        for o in paso.opciones:
            val = valores.get(o.clave, o.valor)
            if o.tipo == "clave":
                v.addWidget(_campo(o.titulo, label("Se pide al ejecutar el lote; nunca se guarda.", "Help", wrap=True)))
                continue
            caja = None
            if o.tipo == "lista":
                w = QComboBox()
                for k, t in o.elegibles():
                    w.addItem(t, k)
                i = w.findData(val)
                if i < 0 and val not in (None, ""):
                    w.addItem(f"{val} (no disponible en este equipo)", val)
                    i = w.count() - 1
                w.setCurrentIndex(max(0, i))
                caja = _campo(o.titulo, w)
            elif o.tipo == "entero":
                w = QSpinBox()
                lo, hi = (tuple(o.opciones) + (0, 9999))[:2] if o.opciones else (0, 9999)
                w.setRange(int(lo), int(hi))
                w.setValue(int(val))
                w.setMinimumWidth(110)
                fila = QHBoxLayout()
                fila.setContentsMargins(0, 0, 0, 0)
                fila.addWidget(w)
                fila.addStretch(1)
                caja = _campo(o.titulo, fila)
            elif o.tipo == "texto":
                w = QLineEdit(str(val or ""))
                caja = _campo(o.titulo, w)
            elif o.tipo == "si_no":
                w = QCheckBox(o.titulo)            # ocupa todo el ancho: el texto nunca se corta
                w.setChecked(bool(val))
                caja = w
            elif o.tipo == "varios":
                cont = QWidget()
                grid = QGridLayout(cont)
                grid.setContentsMargins(0, 0, 0, 0)
                grid.setHorizontalSpacing(14)
                grid.setVerticalSpacing(2)
                marcas = {}
                for k, (clave, texto) in enumerate(o.elegibles()):
                    cb = QCheckBox(texto)
                    cb.setChecked(clave in (val or []))
                    grid.addWidget(cb, k // 2, k % 2)
                    marcas[clave] = cb
                w = marcas
                caja = _campo(o.titulo, cont)
            else:
                continue
            self.campos[o.clave] = (o, w)
            if o.ayuda:
                bloque = QWidget()
                bv = QVBoxLayout(bloque)
                bv.setContentsMargins(0, 0, 0, 0)
                bv.setSpacing(4)
                bv.addWidget(caja)
                bv.addWidget(label(o.ayuda, "Help", wrap=True))
                caja = bloque
            v.addWidget(caja)
        fila, _ = _botones(self, "Guardar opciones")
        v.addSpacing(4)
        v.addLayout(fila)

    def valores(self) -> dict:
        out = {}
        for clave, (o, w) in self.campos.items():
            if o.tipo == "lista":
                out[clave] = w.currentData()
            elif o.tipo == "entero":
                out[clave] = w.value()
            elif o.tipo == "texto":
                out[clave] = w.text()
            elif o.tipo == "si_no":
                out[clave] = w.isChecked()
            elif o.tipo == "varios":
                out[clave] = [k for k, cb in w.items() if cb.isChecked()]
        return self.paso.completar(out)


def _boton(texto: str, cb=None, estilo: str = "Secondary", glifo: str | None = None, tam: str | None = "sm",
           ayuda: str = "") -> QPushButton:
    """Botón del sistema (``Secondary`` o ``Ghost``), en tamaño ``sm`` salvo que se pida otro."""
    b = QPushButton(texto)
    b.setObjectName(estilo)
    if tam:
        b.setProperty("tam", tam)
        if tam == "sm":
            # «sm» (32 px): en la hoja general, el relleno de #Secondary le gana a [tam="sm"].
            b.setStyleSheet("QPushButton { padding: 5px 10px; font-size: 13px; border-radius: 8px; }")
    if glifo:
        b.setIcon(icon(glifo, tema.color("ink-2"), size=16 if tam == "sm" else 18))
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if ayuda:
        b.setToolTip(ayuda)
    if cb is not None:
        b.clicked.connect(cb)
    return b


def _campo(titulo: str, *contenido) -> QWidget:
    """Etiqueta encima del control (o de una fila de controles), a todo el ancho del panel."""
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(6)
    v.addWidget(label(titulo, "Label"))
    for c in contenido:
        if isinstance(c, QWidget):
            v.addWidget(c)
        else:
            v.addLayout(c)
    return w


def _avanzadas(lay: QVBoxLayout) -> tuple[QPushButton, QVBoxLayout]:
    """«Opciones avanzadas» plegadas al final de un paso: devuelve el botón y el layout de lo que se pliega."""
    caja = QWidget()
    v = QVBoxLayout(caja)
    v.setContentsMargins(0, 4, 0, 0)
    v.setSpacing(12)
    caja.hide()
    b = _boton("Opciones avanzadas", estilo="Ghost", glifo="down_chevron")
    b.setCheckable(True)

    def plegar(on: bool):
        caja.setVisible(on)
        b.setIcon(icon("up_chevron" if on else "down_chevron", tema.color("ink-2"), size=16))

    b.toggled.connect(plegar)
    fila = QHBoxLayout()
    fila.setContentsMargins(0, 0, 0, 0)
    fila.addWidget(b)
    fila.addStretch(1)
    lay.addLayout(fila)
    lay.addWidget(caja)
    return b, v


def _barra(titulo: str | QLabel, *derecha: QWidget) -> QHBoxLayout:
    """Barra de arriba del área principal: título ``title-3`` y, a la derecha, acciones ``ghost``."""
    fila = QHBoxLayout()
    fila.setContentsMargins(0, 0, 0, 0)
    fila.setSpacing(4)
    fila.addWidget(titulo if isinstance(titulo, QLabel) else label(titulo, "StepTitle"), 1)
    for w in derecha:
        fila.addWidget(w, 0, Qt.AlignmentFlag.AlignVCenter)
    return fila


class _ZonaSoltar(DropArea):
    """Zona grande para soltar del área principal, que entrega las rutas tal cual (con carpetas): así
    ``lotes.expandir`` decide si entran las subcarpetas.  Tiene un segundo botón, «Elegir carpeta…»."""

    soltados = Signal(list)

    def __init__(self, titulo: str, ayuda: str, tile_key: str, al_archivos, al_carpeta):
        super().__init__(titulo, ayuda, TIPOS_LOTE, multi=True, tile_key=tile_key, zone=True,
                         button_text="Elegir archivos…")
        self.clicked.connect(al_archivos)
        lay = self.layout()
        i = lay.indexOf(self.button)
        lay.removeWidget(self.button)
        fila = QHBoxLayout()
        fila.setSpacing(8)
        fila.addStretch(1)
        fila.addWidget(self.button)
        self.b_carpeta = _boton("Elegir carpeta…", al_carpeta, tam=None)
        fila.addWidget(self.b_carpeta)
        fila.addStretch(1)
        lay.insertLayout(i, fila)

    @staticmethod
    def _rutas(e) -> list[str]:
        return [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()] if e.mimeData().hasUrls() else []

    def dragEnterEvent(self, e):
        if self._rutas(e):
            e.acceptProposedAction()
            self._set_hover(True)

    def dropEvent(self, e):
        self._set_hover(False)
        rutas = self._rutas(e)
        if rutas:
            e.acceptProposedAction()
            self.soltados.emit(rutas)


class _TarjetaArchivo(FileCardDelegate):
    """Fila de archivo como tarjeta (``rp-file``) con el chip en el tono de su estado: listo (ok), con aviso
    (warn), omitido (neutro) o error (danger).  ``tono_fn(item)`` da ``(texto, tono)`` o ``None``."""

    def __init__(self, fl, meta_fn, tono_fn):
        super().__init__(fl, meta_fn, None)
        self.tono_fn = tono_fn

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
        ruta = item.data(0, Qt.ItemDataRole.UserRole) or ""
        ext = Path(ruta).suffix.lower()
        pintar_miniatura(p, QRectF(r.left() + 9, r.center().y() - 22, 34, 44),
                         ext in core.PDF_EXTS or not core.is_image(ruta),
                         "PDF" if ext in core.PDF_EXTS else ext[1:4].upper() or "DOC")
        x = r.left() + 9 + 34 + 12
        derecha = r.right() - (48 if hover else 12)
        chip = self.tono_fn(item)
        if chip:
            texto, tono = chip
            fc = tema.fuente("chip")
            cw = QFontMetrics(fc).horizontalAdvance(texto) + 20
            cr = QRectF(derecha - cw, r.center().y() - 13, cw, 26)
            fondo, tinta = ((t[f"{tono}-tint"], t[tono]) if tono in ("ok", "warn", "danger", "info") else
                            (ton["band"], ton["ink"]) if tono == "cat" else (t["surface-sunken"], t["ink-2"]))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(fondo))
            p.drawRoundedRect(cr, 13, 13)
            p.setFont(fc)
            p.setPen(QColor(tinta))
            p.drawText(cr, Qt.AlignmentFlag.AlignCenter, texto)
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
            p.drawPixmap(QPointF(q.center().x() - 8, q.center().y() - 8), pixmap("remove", t["ink-2"], 16, 1.8))
        p.restore()


class _FilaPaso(QFrame):
    """Un paso del flujo: número en la banda de la categoría, título y resumen de sus opciones.  «Opciones» y
    «Quitar» aparecen al pasar el puntero o al elegir la fila (doble clic también abre las opciones)."""

    def __init__(self, numero: int, paso: lotes.Paso, valores: dict, on_opciones, on_quitar):
        super().__init__()
        self.setObjectName("FilaPaso")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.numero = numero
        h = QHBoxLayout(self)
        h.setContentsMargins(40, 6, 4, 6)
        h.setSpacing(6)
        col = QVBoxLayout()
        col.setSpacing(0)
        self.titulo = _Elidido(paso.titulo, "BodyStrong")
        col.addWidget(self.titulo)
        problema = paso.problema(valores)
        self.problema = problema
        self.resumen = _Elidido(f"Falta: {problema}" if problema else paso.texto_resumen(valores), "Caption")
        if problema:
            self.resumen.setStyleSheet(f"color: {tema.color('danger')};")
        col.addWidget(self.resumen)
        h.addLayout(col, 1)
        self.tiene_opciones = any(o.tipo != "clave" for o in paso.opciones)
        self.b_opciones = QToolButton()
        self.b_opciones.setObjectName("Ghost")
        self.b_opciones.setIcon(icon("edittext", tema.color("ink-2"), size=16))
        self.b_opciones.setIconSize(QSize(16, 16))
        self.b_opciones.setToolTip(f"Opciones de «{paso.titulo}» (doble clic)")
        self.b_opciones.setAccessibleName(f"Opciones de «{paso.titulo}»")
        self.b_opciones.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_opciones.clicked.connect(on_opciones)
        h.addWidget(self.b_opciones)
        self.b_quitar = QToolButton()
        self.b_quitar.setObjectName("Ghost")
        self.b_quitar.setIcon(icon("remove", tema.color("ink-2"), size=16))
        self.b_quitar.setIconSize(QSize(16, 16))
        self.b_quitar.setToolTip("Quitar este paso")
        self.b_quitar.setAccessibleName("Quitar este paso")
        self.b_quitar.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_quitar.clicked.connect(on_quitar)
        h.addWidget(self.b_quitar)
        self.elegida = False
        self.mostrar_quitar(False)

    def mostrar_quitar(self, on: bool) -> None:
        """Las acciones de la fila, solo al pasar el puntero o con la fila elegida."""
        self.b_opciones.setVisible(on and self.tiene_opciones)
        self.b_quitar.setVisible(on)

    def event(self, e):
        if e.type() == QEvent.Type.HoverEnter:
            self.mostrar_quitar(True)
        elif e.type() == QEvent.Type.HoverLeave:
            self.mostrar_quitar(self.elegida)
        return super().event(e)

    def paintEvent(self, _e):
        t = tema.tokens()
        ton = tema.tonos(cat_de(self))
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        circ = QRectF(8, (self.height() - 24) / 2, 24, 24)
        p.setPen(Qt.PenStyle.NoPen)
        if self.problema:
            p.setBrush(QColor(t["danger-tint"]))
            color = t["danger"]
        else:
            p.setBrush(QColor(ton["band"]))
            color = ton["ink"]
        p.drawEllipse(circ)
        p.setFont(tema.fuente("step-n", self.font()))
        p.setPen(QColor(color))
        p.drawText(circ, Qt.AlignmentFlag.AlignCenter, str(self.numero))
        p.end()


class ListaPasos(QListWidget):
    """Pasos del flujo, numerados; se reordenan arrastrando (o con Alt+↑ y Alt+↓, o con el menú contextual)."""
    reordenado = Signal(list)
    mover = Signal(int)          # -1 sube el paso elegido, 1 lo baja
    opciones = Signal(int)
    quitar = Signal(int)

    def __init__(self):
        super().__init__()
        self.setObjectName("ListaPasos")
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSpacing(1)
        self.setMinimumHeight(96)
        self.setToolTip("Arrastra los pasos para cambiar el orden (o Alt+↑ y Alt+↓). «Convertir a PDF» siempre va "
                        "primero; «Proteger» y los pasos finales (exportar e imprimir), al final.")
        self.setStyleSheet("QListWidget#ListaPasos::item { border-radius: 8px; }")
        self.model().rowsMoved.connect(lambda *_: QTimer.singleShot(0, self._emitir))
        self.currentItemChanged.connect(self._resaltar)
        for tecla, delta in (("Alt+Up", -1), ("Alt+Down", 1)):
            sc = QShortcut(QKeySequence(tecla), self)
            sc.setContext(Qt.ShortcutContext.WidgetShortcut)
            sc.activated.connect(lambda d=delta: self.mover.emit(d))
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)

    def _resaltar(self, actual, anterior):
        for it, on in ((anterior, False), (actual, True)):
            w = self.itemWidget(it) if it is not None else None
            if isinstance(w, _FilaPaso):
                w.elegida = on
                w.mostrar_quitar(on or w.underMouse())

    def _menu(self, pos):
        it = self.itemAt(pos)
        if it is None:
            return
        self.setCurrentItem(it)
        k = self.row(it)
        m = QMenu(self)
        fila = self.itemWidget(it)
        if isinstance(fila, _FilaPaso) and fila.tiene_opciones:
            m.addAction("Opciones…", lambda: self.opciones.emit(k))
        a = m.addAction("Subir", lambda: self.mover.emit(-1))
        a.setEnabled(k > 0)
        a = m.addAction("Bajar", lambda: self.mover.emit(1))
        a.setEnabled(k < self.count() - 1)
        m.addSeparator()
        m.addAction("Quitar este paso", lambda: self.quitar.emit(k))
        m.exec(self.viewport().mapToGlobal(pos))

    def _emitir(self):
        self.reordenado.emit([self.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.count())])

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.count() == 0:
            p = QPainter(self.viewport())
            p.setPen(QColor(tema.color("ink-3")))
            p.setFont(tema.fuente("body-sm", self.font()))
            p.drawText(self.viewport().rect().adjusted(16, 0, -16, 0),
                       Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                       "Este flujo no tiene pasos todavía.\nUsa «Agregar paso» o elige un flujo de ejemplo.")
            p.end()


_PENDIENTE = Qt.ItemDataRole.UserRole + 2


class ListaArchivos(FileList):
    """Lista del lote: agrega muchos archivos de una vez (sin llenar los recientes) y recibe carpetas.

    Las páginas de cada PDF se cuentan después, de a poco, para que la ventana nunca se congele (abrir por
    primera vez cientos de archivos recién copiados puede tardar varios segundos por el antivirus)."""
    soltados = Signal(list)
    contadas = Signal()          # se contaron más páginas (no cambia la lista: no hace falta revisar nada más)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._cursor = 0
        self._timer = QTimer(self)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self._contar_paginas)
        self.changed.connect(self._reiniciar)

    def _reiniciar(self):
        self._cursor = 0
        if self.topLevelItemCount():
            self._timer.start()

    def _contar_paginas(self, limite: float = 0.03) -> None:
        if not PDF_LOCK.acquire(blocking=False):
            return                                  # hay una tarea en curso: se sigue después
        contadas = False
        self.blockSignals(True)                     # un aviso por tanda, no uno por archivo
        try:
            fin = time.perf_counter() + limite
            n = self.topLevelItemCount()
            while self._cursor < n and time.perf_counter() < fin:
                it = self.topLevelItem(self._cursor)
                self._cursor += 1
                if it.data(0, _PENDIENTE):
                    it.setText(1, _paginas_pdf(it.data(0, Qt.ItemDataRole.UserRole)))
                    it.setData(0, _PENDIENTE, None)
                    contadas = True
            if self._cursor >= n:
                self._timer.stop()
        finally:
            self.blockSignals(False)
            PDF_LOCK.release()
        if contadas:
            self.contadas.emit()

    def completar(self) -> None:
        """Cuenta ya todas las páginas pendientes."""
        self._cursor = 0
        self._contar_paginas(limite=3600)

    def agregar(self, items: list[tuple[str, str | None]]) -> int:
        existentes = {os.path.normcase(p) for p in self.paths()}
        nuevos = []
        for ruta, raiz in items:
            clave = os.path.normcase(ruta)
            if clave in existentes:
                continue
            cols = self.row_factory(ruta)
            if cols is None:
                continue
            it = QTreeWidgetItem([str(c) for c in cols])
            it.setData(0, Qt.ItemDataRole.UserRole, ruta)
            it.setData(0, Qt.ItemDataRole.UserRole + 1, raiz)
            it.setToolTip(0, ruta)
            it.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsDragEnabled)
            if cols[1] == "…":
                it.setData(0, _PENDIENTE, True)
            nuevos.append(it)
            existentes.add(clave)
        if nuevos:
            self.setUpdatesEnabled(False)
            self.addTopLevelItems(nuevos)
            self.setUpdatesEnabled(True)
            ctx.remember_dir(nuevos[-1].data(0, Qt.ItemDataRole.UserRole))
            self.changed.emit()
        return len(nuevos)

    def add_paths(self, paths):
        self.agregar([(p, None) for p in paths])

    def entradas(self) -> list[tuple[str, str | None]]:
        return [(it.data(0, Qt.ItemDataRole.UserRole), it.data(0, Qt.ItemDataRole.UserRole + 1)) for it in self.items()]

    def dropEvent(self, e):
        if self._external(e):
            rutas = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
            e.acceptProposedAction()
            if rutas:
                self.soltados.emit(rutas)
            return
        super().dropEvent(e)


def _tabla(columnas: list[str], estirar: int) -> QTreeWidget:
    t = QTreeWidget()
    t.setColumnCount(len(columnas))
    t.setHeaderLabels(columnas)
    t.setRootIsDecorated(False)
    t.setUniformRowHeights(True)
    t.setAlternatingRowColors(False)
    t.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    t.setTextElideMode(Qt.TextElideMode.ElideRight)
    t.setItemDelegate(_NoEditar(t))
    hdr = t.header()
    hdr.setStretchLastSection(False)
    for c in range(len(columnas)):
        hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
    hdr.setSectionResizeMode(estirar, QHeaderView.ResizeMode.Stretch)
    return t


def _franja(tono: str = "warn") -> tuple[Note, QLabel]:
    """Aviso corto (``rp-note``) oculto; devuelve la nota y su etiqueta de texto."""
    caja = Note("", tono)
    caja.hide()
    return caja, caja.text_lb


def _paginas_pdf(ruta: str) -> str:
    try:
        with pymupdf.open(ruta) as d:
            # Documento sin autenticar: aquí sí se puede consultar needs_pass.
            return "Protegido" if d.needs_pass else str(d.page_count)
    except Exception:  # noqa: BLE001
        return "No se puede leer"


def _fila_archivo(ruta: str) -> list[str] | None:
    """Columnas (Archivo, Páginas, Tamaño, Estado) de un archivo del lote; las páginas se cuentan después."""
    p = Path(ruta)
    try:
        tam = core.human_size(os.path.getsize(ruta))
    except OSError:
        return None
    ext = p.suffix.lower()
    if ext in core.PDF_EXTS:
        paginas = "…"
    elif core.is_image(ruta):
        paginas = "Imagen"
    else:
        paginas = oficina.NOMBRES.get(oficina.tipo_de(ruta) or "", "Otro")
    return [p.name, paginas, tam, ""]


_ESTADO = Qt.ItemDataRole.UserRole + 3         # estado del último lote («hecho», «error»…) de cada fila


def _meta_archivo(it: QTreeWidgetItem) -> str:
    """Línea de datos de la tarjeta: «6 páginas · 143,5 KB», «Word · 12 KB», «Contando páginas…»."""
    pag, tam = it.text(1), it.text(2)
    if pag.isdigit():
        pag = f"{pag} página{'s' if pag != '1' else ''}"
    elif pag == "…":
        pag = "Contando páginas…"
    elif pag in ("Protegido", "No se puede leer"):
        return f"PDF · {tam}"                    # el chip ya dice qué le pasa
    return f"{pag} · {tam}" if tam else pag


def _chip_archivo(it: QTreeWidgetItem) -> tuple[str, str] | None:
    """Chip de la tarjeta: el estado del último lote o, antes, si algo pide atención."""
    if it.text(3):
        return it.text(3), TONO_ESTADO.get(it.data(0, _ESTADO), "neutral")
    if it.text(1) == "Protegido":
        return "Con contraseña", "warn"
    if it.text(1) == "No se puede leer":
        return "No se puede leer", "danger"
    return None


# --------------------------------------------------------------------------
# Acciones por lotes
# --------------------------------------------------------------------------

GRUPOS_MENU = (("a_pdf",), ("ocr", "comprimir", "numerar", "marca", "censurar", "ocultos", "nombrar", "quitar_clave",
                            "proteger"), ("a_imagenes", "a_texto", "imprimir"))


class LotesPage(ToolPage):
    key = "batch"
    title = "Acciones por lotes"
    subtitle = "Aplica varios pasos seguidos a muchos archivos de una vez, sin tocar los originales."
    icon_name = "batch"
    action_text = "Ejecutar el lote"

    def __init__(self):
        self.modo = "editar"
        self.flujo: lotes.Flujo = lotes.Flujo("Flujo nuevo")
        self.origen_flujo: tuple[str, str] = ("nuevo", "")
        self._sucio = False
        self._plan: lotes.Plan | None = None
        self._informe: lotes.Informe | None = None
        self._cargando = False
        super().__init__()
        self._modo("editar")

    # ------------------------------------------------------------------ construcción
    def build(self):
        lotes.pasos_externos(extensiones.modulos())
        self.footer_info.setWordWrap(True)          # el motivo puede ser largo: nunca se recorta

        # Paso 1: el flujo y sus pasos
        g = self.step("¿Qué pasos se aplican?", help="Arrastra un paso para moverlo; doble clic, para sus opciones.")
        self.card_pasos = g.parentWidget()
        self.combo = QComboBox()
        self.combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # El ancho no depende del nombre más largo: el panel mide 340 px con la ventana angosta.
        self.combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.combo.setMinimumContentsLength(10)
        self.combo.currentIndexChanged.connect(self._elegir_flujo)
        self.b_guardar = _boton("Guardar", self._guardar, estilo="Ghost", glifo="save",
                                ayuda="Guarda los cambios de este flujo para usarlo otra vez")
        self.b_guardar_como = _boton("Guardar como…", self._guardar_como, estilo="Ghost")
        self.b_eliminar = QToolButton()
        self.b_eliminar.setObjectName("Ghost")
        self.b_eliminar.setIcon(icon("trash", tema.color("ink-2"), size=16))
        self.b_eliminar.setIconSize(QSize(16, 16))
        self.b_eliminar.setToolTip("Eliminar este flujo guardado")
        self.b_eliminar.setAccessibleName("Eliminar este flujo guardado")
        self.b_eliminar.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_eliminar.clicked.connect(self._eliminar)
        fila = QHBoxLayout()
        fila.setContentsMargins(0, 0, 0, 0)
        fila.setSpacing(2)
        fila.addStretch(1)
        for b in (self.b_guardar, self.b_guardar_como, self.b_eliminar):
            fila.addWidget(b)
        g.addWidget(_campo("Flujo", self.combo, fila))
        self.aviso_flujo, self.aviso_flujo_txt = _franja()
        g.addWidget(self.aviso_flujo)
        self.pasos = ListaPasos()
        self.pasos.reordenado.connect(self._reordenado)
        self.pasos.itemDoubleClicked.connect(lambda it: self._opciones(it.data(Qt.ItemDataRole.UserRole)))
        self.pasos.mover.connect(self._mover)
        self.pasos.opciones.connect(lambda k: QTimer.singleShot(0, lambda: self._opciones(k)))
        self.pasos.quitar.connect(lambda k: QTimer.singleShot(0, lambda: self._quitar(k)))
        g.addWidget(self.pasos)
        self.b_agregar = _boton("Agregar paso", glifo="add", tam=None)
        self.menu_pasos = QMenu(self.b_agregar)
        self.menu_pasos.aboutToShow.connect(self._llenar_menu)
        self.b_agregar.setMenu(self.menu_pasos)
        fila = QHBoxLayout()
        fila.setContentsMargins(0, 0, 0, 0)
        fila.addWidget(self.b_agregar)
        fila.addStretch(1)
        g.addLayout(fila)
        self.nota_orden, self.nota_orden_txt = _franja("info")
        g.addWidget(self.nota_orden)

        # Paso 2: el destino (y, plegado, lo que casi nadie cambia)
        g = self.step("¿Dónde se guardan los resultados?",
                      help="Los originales no cambian. Si un nombre ya existe, se agrega «(2)».")
        self.card_destino = g.parentWidget()
        self.destino = OptionGroup()
        self.r_junto = self.destino.add("Junto a cada original", "Con un sufijo al final del nombre.", "junto")
        self.r_carpeta = self.destino.add("En otra carpeta", "Todos los resultados en un mismo lugar.", "carpeta")
        g.addWidget(self.destino)
        self.e_sufijo = QLineEdit("_procesado")
        self.e_sufijo.setPlaceholderText("Por ejemplo: _procesado")
        self.ej_sufijo = label("", "Help", wrap=True)
        self.caja_sufijo = _campo("Sufijo del nombre", self.e_sufijo, self.ej_sufijo)
        g.addWidget(self.caja_sufijo)
        self.e_carpeta = QLineEdit()
        self.e_carpeta.setReadOnly(True)
        self.e_carpeta.setPlaceholderText("Ninguna carpeta elegida")
        self.b_carpeta = QPushButton("Elegir…")
        self.b_carpeta.setObjectName("Secondary")
        self.b_carpeta.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_carpeta.clicked.connect(self._elegir_destino)
        fila = QHBoxLayout()
        fila.setContentsMargins(0, 0, 0, 0)
        fila.setSpacing(8)
        fila.addWidget(self.e_carpeta, 1)
        fila.addWidget(self.b_carpeta)
        self.c_conservar = QCheckBox("Conservar las subcarpetas")
        self.c_conservar.setChecked(True)
        self.caja_carpeta = _campo("Carpeta de destino", fila, self.c_conservar)
        g.addWidget(self.caja_carpeta)
        self.b_avanzadas, av = _avanzadas(g)
        self.c_sub = SwitchRow("Incluir subcarpetas", "Al agregar una carpeta, también entran los archivos de sus "
                               "subcarpetas.", checked=True)
        av.addWidget(self.c_sub)
        self.c_error = SwitchRow("Detener el lote si un archivo falla",
                                 "Si no, se sigue con los demás y el error queda en el informe.")
        av.addWidget(self.c_error)
        self.destino.changed.connect(self._destino_cambio)
        self.c_conservar.toggled.connect(self._destino_cambio)
        self.e_sufijo.textChanged.connect(self._destino_cambio)
        self.c_error.toggled.connect(self._destino_cambio)

        # Área principal: zona para soltar, la lista, la vista previa o el resultado
        self.zona = _ZonaSoltar("Arrastra aquí los archivos del lote",
                                "PDF, o también Word, Excel e imágenes si el flujo empieza con «Convertir a PDF». "
                                "Puedes soltar una carpeta entera.", "batch", self._agregar_archivos,
                                self._agregar_carpeta)
        self.zona.soltados.connect(self.accept_files)

        self.lista = ListaArchivos(["Archivo", "Páginas", "Tamaño", "Estado"], TIPOS_LOTE, _fila_archivo,
                                   placeholder="Arrastra aquí archivos o carpetas", allow_duplicates=False)
        self.lista.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.lista.set_cards(_meta_archivo)
        self.lista.setItemDelegate(_TarjetaArchivo(self.lista, _meta_archivo, _chip_archivo))
        self.lista.setMinimumHeight(0)
        self.lista.changed.connect(self._archivos_cambiaron)
        self.lista.contadas.connect(self.refresh)
        self.lista.soltados.connect(self.accept_files)
        self.b_vaciar = _boton("Vaciar lista", lambda: self.lista.clear_all(), estilo="Ghost", tam=None)
        self.aviso_arch, self.aviso_arch_txt = _franja()
        self.b_convertir = _boton("Agregar «Convertir a PDF»", lambda: self._agregar_paso("a_pdf"))
        self.aviso_arch.layout().addWidget(self.b_convertir, 0, Qt.AlignmentFlag.AlignVCenter)
        self.card_archivos = QWidget()
        v = QVBoxLayout(self.card_archivos)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(8)
        v.addLayout(_barra("Archivos del lote", self.b_vaciar))
        v.addWidget(self.lista, 1)
        v.addWidget(self.aviso_arch)

        self.card_vista = QWidget()
        v = QVBoxLayout(self.card_vista)
        v.setContentsMargins(14, 12, 14, 14)
        v.setSpacing(8)
        v.addLayout(_barra("Lo que se hará con cada archivo"))
        self.vista_info = label("", "BodySm", wrap=True)
        v.addWidget(self.vista_info)
        self.vista_aviso, self.vista_aviso_txt = _franja()
        v.addWidget(self.vista_aviso)
        self.tabla_vista = _tabla(["Archivo", "Resultado previsto", "Pasos", "Avisos"], 2)
        self.tabla_vista.setMinimumHeight(160)
        for c, w in ((0, 200), (1, 220), (3, 200)):
            self.tabla_vista.header().resizeSection(c, w)
        v.addWidget(self.tabla_vista, 1)

        self.card_res = QWidget()
        v = QVBoxLayout(self.card_res)
        v.setContentsMargins(14, 12, 14, 14)
        v.setSpacing(8)
        self.res_titulo = label("Resultado del lote", "StepTitle", wrap=True)
        self.b_abrir = _boton("Abrir carpeta", self._abrir_carpeta, estilo="Ghost", glifo="folder", tam=None)
        self.b_informe = _boton("Guardar informe…", self._guardar_informe, estilo="Ghost", glifo="save", tam=None)
        self.b_reintentar = _boton("Reintentar los que fallaron", self._reintentar, estilo="Ghost", glifo="redo",
                                   tam=None)
        v.addLayout(_barra(self.res_titulo, self.b_abrir, self.b_informe, self.b_reintentar))
        self.res_resumen = label("", "BodyStrong", wrap=True)
        v.addWidget(self.res_resumen)
        self.tabla_res = _tabla(["", "Archivo", "Resultado", "Detalle", "Antes → después"], 3)
        self.tabla_res.setMinimumHeight(160)
        hdr = self.tabla_res.header()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hdr.resizeSection(0, 30)
        hdr.resizeSection(1, 180)
        hdr.resizeSection(2, 170)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.tabla_res.itemDoubleClicked.connect(self._abrir_resultado)
        v.addWidget(self.tabla_res, 1)

        self.principal = QStackedWidget()
        for w in (self.zona, self.card_archivos, self.card_vista, self.card_res):
            self.principal.addWidget(w)
        self.set_main(self.principal)

        # Encabezado: agregar más (solo cuando ya hay una lista a la que agregar)
        self.cab = QWidget()
        h = QHBoxLayout(self.cab)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        self.b_add_carpeta = _boton("Agregar carpeta", self._agregar_carpeta, glifo="folder", tam=None)
        self.b_add_archivos = _boton("Agregar archivos", self._agregar_archivos, glifo="add", tam=None)
        h.addWidget(self.b_add_carpeta)
        h.addWidget(self.b_add_archivos)
        self.set_header_widget(self.cab)

        # Barra de acción: secundarios según el modo; el principal ejecuta (o empieza otro lote)
        self.b_volver = self.add_secondary("Volver", lambda: self._modo("editar"))
        self.b_probar = self.add_secondary("Probar con el primer archivo", self._probar)
        self.b_probar.setToolTip("Procesa solo el primer archivo en una carpeta temporal y lo abre aquí. No escribe "
                                 "nada en el destino (y no imprime).")
        self.b_vista = self.add_secondary("Vista previa", self._vista_previa)
        self.b_vista.setToolTip("Muestra, archivo por archivo, qué se hará, sin tocar nada")

        self._cargar_combo(self._flujo_recordado())
        self._elegir_flujo()
        self._botones_lista()

    # ------------------------------------------------------------------ flujos
    def _flujo_recordado(self) -> tuple[str, str]:
        try:
            tipo, nombre = (ctx.settings.get("lotes_ultimo_flujo") if ctx.settings else None) or ("", "")
        except (TypeError, ValueError):
            tipo, nombre = "", ""
        if tipo in ("guardado", "ejemplo") and nombre:
            return tipo, nombre
        return "ejemplo", lotes.ejemplos()[0].nombre

    def _cargar_combo(self, seleccion: tuple[str, str] | None = None):
        self._cargando = True
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItem("Flujo nuevo (vacío)", ("nuevo", ""))
        guardados = lotes.listar_flujos()
        for f in guardados:
            self.combo.addItem(f.nombre, ("guardado", f.nombre))
        self.combo.insertSeparator(self.combo.count())
        self.combo.addItem("Ejemplos")             # encabezado del grupo: no se puede elegir
        cab = self.combo.model().item(self.combo.count() - 1)
        cab.setEnabled(False)
        cab.setForeground(QColor(tema.color("ink-3")))
        for f in lotes.ejemplos():
            self.combo.addItem(f.nombre, ("ejemplo", f.nombre))
            self.combo.setItemData(self.combo.count() - 1, f"{f.nombre} (ejemplo)", Qt.ItemDataRole.ToolTipRole)
        idx = 0
        if seleccion:
            for i in range(self.combo.count()):
                d = self.combo.itemData(i)
                if d and tuple(d) == tuple(seleccion):
                    idx = i
                    break
        self.combo.setCurrentIndex(idx)
        self.combo.blockSignals(False)
        self._cargando = False

    def _elegir_flujo(self, *_):
        if self._cargando:
            return
        datos = self.combo.currentData()
        if not datos:
            return
        tipo, nombre = datos
        if self._sucio and (tipo, nombre) != self.origen_flujo:
            if not confirmar(self, "Cambiar de flujo", f"El flujo «{self.flujo.nombre}» tiene cambios sin guardar.",
                             "Descartar los cambios", "Cancelar", "Si cambias de flujo, esos cambios se pierden."):
                self._cargar_combo(self.origen_flujo)
                return
        if tipo == "guardado":
            flujo = next((f for f in lotes.listar_flujos() if f.nombre == nombre), None)
        elif tipo == "ejemplo":
            flujo = next((f for f in lotes.ejemplos() if f.nombre == nombre), None)
        else:
            flujo = lotes.Flujo("Flujo nuevo")
        if flujo is None:
            flujo, tipo = lotes.Flujo("Flujo nuevo"), "nuevo"
        self._poner_flujo(flujo, (tipo, nombre))

    def _poner_flujo(self, flujo: lotes.Flujo, origen: tuple[str, str]):
        self.flujo = flujo.copia()
        self.origen_flujo = tuple(origen)
        self._sucio = False
        self._cargando = True
        d = self.flujo.destino
        self.destino.set_value("carpeta" if d.get("modo") == "carpeta" else "junto")
        self.e_sufijo.setText(str(d.get("sufijo", "")))
        self.e_carpeta.setText(str(d.get("carpeta", "")))
        self.c_conservar.setChecked(bool(d.get("conservar_subcarpetas", True)))
        self.c_error.setChecked(self.flujo.al_error == "detener")
        self._cargando = False
        if self.flujo.avisos:
            self.aviso_flujo_txt.setText(" ".join(self.flujo.avisos))
            self.aviso_flujo.show()
        else:
            self.aviso_flujo.hide()
        self.nota_orden.hide()
        self._acomodar()
        self._pintar_pasos()
        self._destino_cambio()
        self._sucio = False
        self._botones_flujo()

    def _botones_flujo(self):
        """Lo que no aplica se esconde: «Eliminar» solo con un flujo guardado; guardar, solo si tiene pasos."""
        tipo = self.origen_flujo[0]
        hay = bool(self.flujo.pasos)
        self.b_eliminar.setEnabled(tipo == "guardado")
        self.b_eliminar.setVisible(tipo == "guardado")
        self.b_guardar.setEnabled(hay)
        self.b_guardar_como.setEnabled(hay)
        # Un flujo de ejemplo o nuevo se guarda siempre «como»: basta un botón.
        self.b_guardar.setVisible(hay and tipo == "guardado" and self._sucio)
        self.b_guardar_como.setVisible(hay)
        self.b_guardar_como.setText("Guardar como…" if tipo == "guardado" else "Guardar este flujo…")

    def _guardar(self):
        if self.origen_flujo[0] != "guardado":
            self._guardar_como()
            return
        self._escribir_flujo(self.flujo.nombre)

    def guardar(self, al_terminar=None):
        """Guarda el flujo («Guardar» o, si todavía no tiene nombre, «Guardar como…»).  ``al_terminar(ok)``
        avisa si quedó guardado: lo usa la pregunta de «Cambios sin guardar» al cerrar la aplicación."""
        self._guardar()
        if callable(al_terminar):
            al_terminar(not self.has_unsaved())

    def _guardar_como(self):
        sugerido = self.flujo.nombre if self.origen_flujo[0] != "nuevo" else "Mi flujo"
        if self.origen_flujo[0] == "ejemplo":
            sugerido = f"{self.flujo.nombre} (mío)"
        nombre = pedir_nombre_flujo(self, sugerido)
        if not nombre:
            return
        existe = any(f.nombre.casefold() == nombre.casefold() for f in lotes.listar_flujos())
        if existe and not (self.origen_flujo[0] == "guardado" and self.origen_flujo[1].casefold() == nombre.casefold()):
            if not confirmar(self, "Guardar flujo", f"Ya hay un flujo llamado «{nombre}».", "Reemplazarlo", "Cancelar"):
                return
        self._escribir_flujo(nombre)

    def _escribir_flujo(self, nombre: str):
        flujo = self._flujo_actual()
        flujo.nombre, flujo.ejemplo, flujo.avisos = nombre, False, []
        try:
            lotes.guardar_flujo(flujo)
        except core.UserError as exc:
            avisar(self, "No se pudo guardar", str(exc))
            return
        self.flujo = flujo
        self.origen_flujo = ("guardado", nombre)
        self._sucio = False
        self._cargar_combo(self.origen_flujo)
        self._recordar()
        self._botones_flujo()
        if ctx.window is not None and hasattr(ctx.window, "set_status"):
            ctx.window.set_status(f"Flujo «{nombre}» guardado", 4000)

    def _eliminar(self):
        tipo, nombre = self.origen_flujo
        if tipo != "guardado":
            return
        if not confirmar(self, "Eliminar flujo", f"¿Eliminar el flujo guardado «{nombre}»?", "Eliminar", "Cancelar",
                         "Los archivos que ya procesaste no cambian."):
            return
        try:
            lotes.borrar_flujo(nombre)
        except core.UserError as exc:
            avisar(self, "No se pudo eliminar", str(exc))
            return
        self._sucio = False
        sel = ("ejemplo", lotes.ejemplos()[0].nombre)
        self._cargar_combo(sel)
        self._elegir_flujo()

    def _recordar(self):
        if ctx.settings is not None and self.origen_flujo[0] in ("guardado", "ejemplo"):
            try:
                ctx.settings.set("lotes_ultimo_flujo", list(self.origen_flujo))
            except Exception:  # noqa: BLE001 — recordar el flujo nunca debe interrumpir el trabajo
                pass

    def _flujo_actual(self) -> lotes.Flujo:
        f = self.flujo.copia()
        f.destino = {"modo": "carpeta" if self.r_carpeta.isChecked() else "junto",
                     "sufijo": self.e_sufijo.text(), "carpeta": self.e_carpeta.text(),
                     "conservar_subcarpetas": self.c_conservar.isChecked()}
        f.al_error = "detener" if self.c_error.isChecked() else "seguir"
        return f

    def has_unsaved(self) -> bool:
        return self._sucio and bool(self.flujo.pasos)

    # ------------------------------------------------------------------ pasos
    def _marcar(self):
        self._sucio = True
        self._botones_flujo()

    def _acomodar(self):
        self.flujo.pasos, movidos = lotes.ordenar(self.flujo.pasos)
        if movidos:
            nombres = " y ".join(f"«{m}»" for m in movidos)
            self.nota_orden_txt.setText(f"Se acomodó {nombres}: «Convertir a PDF» va siempre primero; «Proteger» y "
                                        "los pasos finales, al final.")
            self.nota_orden.show()

    def _pintar_pasos(self, seleccion: int | None = None):
        self.pasos.clear()
        for k, (clave, valores) in enumerate(self.flujo.pasos):
            paso = lotes.PASOS[clave]
            it = QListWidgetItem()
            it.setData(Qt.ItemDataRole.UserRole, k)
            # Diferido: el botón que se pulsó se borra al volver a pintar la lista.
            fila = _FilaPaso(k + 1, paso, valores, lambda _=False, k=k: QTimer.singleShot(0, lambda: self._opciones(k)),
                             lambda _=False, k=k: QTimer.singleShot(0, lambda: self._quitar(k)))
            it.setSizeHint(QSize(100, max(50, fila.sizeHint().height())))
            self.pasos.addItem(it)
            self.pasos.setItemWidget(it, fila)
        alto = sum(self.pasos.sizeHintForRow(i) + 2 for i in range(self.pasos.count())) + 10
        self.pasos.setFixedHeight(max(96, min(alto, 330)))
        if seleccion is not None and 0 <= seleccion < self.pasos.count():
            self.pasos.setCurrentRow(seleccion)
        self._botones_flujo()
        self._revisar_tipos()
        self.refresh()

    def _llenar_menu(self):
        self.menu_pasos.clear()
        usados = {c for c, _ in self.flujo.pasos}
        externos = [c for c in lotes.PASOS if c not in lotes.PROPIOS]
        grupos = [list(GRUPOS_MENU[0]), list(GRUPOS_MENU[1]) + externos, list(GRUPOS_MENU[2])]
        for k, grupo in enumerate(grupos):
            if k:
                self.menu_pasos.addSeparator()
            for clave in grupo:
                paso = lotes.PASOS.get(clave)
                if paso is None:
                    continue
                a = self.menu_pasos.addAction(paso.titulo, lambda c=clave: self._agregar_paso(c))
                a.setToolTip(paso.descripcion)
                a.setStatusTip(paso.descripcion)
                if clave in ("a_pdf", "proteger", "quitar_clave", "nombrar") and clave in usados:
                    a.setEnabled(False)
        self.menu_pasos.setToolTipsVisible(True)

    def _agregar_paso(self, clave: str):
        paso = lotes.PASOS[clave]
        if clave == "a_pdf" and any(c == "a_pdf" for c, _ in self.flujo.pasos):
            return
        self.flujo.pasos.append((clave, paso.defecto()))
        self.nota_orden.hide()
        self._acomodar()
        self._marcar()
        indice = max(k for k, (c, _) in enumerate(self.flujo.pasos) if c == clave)
        self._pintar_pasos(indice)

    def _quitar(self, k: int):
        if 0 <= k < len(self.flujo.pasos):
            del self.flujo.pasos[k]
            self.nota_orden.hide()
            self._marcar()
            self._pintar_pasos(min(k, len(self.flujo.pasos) - 1))

    def _opciones(self, k):
        if k is None or not 0 <= k < len(self.flujo.pasos):
            return
        clave, valores = self.flujo.pasos[k]
        paso = lotes.PASOS[clave]
        if not any(o.tipo != "clave" for o in paso.opciones):
            return
        nuevos = editar_opciones(self, paso, valores)
        if nuevos is not None:
            self.flujo.pasos[k] = (clave, nuevos)
            self._marcar()
            self._pintar_pasos(k)

    def _mover(self, delta: int):
        k = self.pasos.currentRow()
        j = k + delta
        if not (0 <= k < len(self.flujo.pasos) and 0 <= j < len(self.flujo.pasos)):
            return
        orden = list(range(len(self.flujo.pasos)))
        orden[k], orden[j] = orden[j], orden[k]
        self._reordenado(orden, seleccion=self.flujo.pasos[k])

    def _reordenado(self, orden: list, seleccion=None):
        if sorted(orden) != list(range(len(self.flujo.pasos))):
            self._pintar_pasos()
            return
        anterior = list(self.flujo.pasos)
        elegido = seleccion if seleccion is not None else (
            anterior[orden[self.pasos.currentRow()]] if 0 <= self.pasos.currentRow() < len(orden) else None)
        self.flujo.pasos = [anterior[i] for i in orden]
        self.nota_orden.hide()
        self._acomodar()
        if self.flujo.pasos != anterior:
            self._marcar()
        indice = next((k for k, p in enumerate(self.flujo.pasos) if p is elegido), None)
        self._pintar_pasos(indice)

    # ------------------------------------------------------------------ archivos y destino
    def _agregar_archivos(self):
        rutas = elegir_archivos(self)
        if rutas:
            self.accept_files(rutas)

    def _agregar_carpeta(self):
        carpeta = elegir_carpeta(self, "Elige la carpeta con los archivos")
        if carpeta:
            self.accept_files([carpeta])

    def accept_files(self, paths: list[str]) -> None:
        if self.modo != "editar":
            self._modo("editar")
        items, avisos = lotes.expandir(list(paths), self.c_sub.isChecked(), TIPOS_LOTE)
        self.lista.agregar(items)
        if avisos and ctx.window is not None and hasattr(ctx.window, "set_status"):
            ctx.window.set_status(" ".join(avisos), 6000)

    def _archivos_cambiaron(self):
        self._plan = None
        self._botones_lista()
        self._revisar_tipos()
        self.refresh()

    def _botones_lista(self):
        n = self.lista.topLevelItemCount()
        self.b_vaciar.setEnabled(n > 0)
        self.cab.setVisible(self.modo == "editar" and n > 0)

    def _revisar_tipos(self):
        if not hasattr(self, "aviso_arch"):
            return
        convierte = bool(self.flujo.pasos) and self.flujo.pasos[0][0] == "a_pdf"
        otros = sum(1 for p in self.lista.paths() if Path(p).suffix.lower() not in core.PDF_EXTS)
        if otros and not convierte:
            self.aviso_arch_txt.setText(f"{_s(otros, 'archivo no es PDF', 'archivos no son PDF')}: agrega el paso "
                                        "«Convertir a PDF» para incluirlos (si no, se omiten).")
            self.aviso_arch.show()
        else:
            self.aviso_arch.hide()

    def _elegir_destino(self):
        carpeta = elegir_carpeta(self, "Elige la carpeta de destino")
        if carpeta:
            self.e_carpeta.setText(carpeta)
            self.r_carpeta.setChecked(True)
            self._destino_cambio()

    def _destino_cambio(self, *_):
        carpeta = self.r_carpeta.isChecked()
        self.caja_carpeta.setVisible(carpeta)
        self.caja_sufijo.setVisible(not carpeta)
        self.c_conservar.setEnabled(carpeta)
        sufijo = self.e_sufijo.text()
        self.ej_sufijo.setText(f"«informe.pdf» queda como «informe{sufijo}.pdf»." if sufijo else
                               "Sin sufijo, el resultado lleva el mismo nombre (más «(2)» si ya existe).")
        if not self._cargando:
            f = self._flujo_actual()
            if f.destino != self.flujo.destino or f.al_error != self.flujo.al_error:
                self.flujo.destino, self.flujo.al_error = f.destino, f.al_error
                self._marcar()
        self.refresh()

    # ------------------------------------------------------------------ estado y barra de acción
    def _motivo(self) -> str | None:
        if not self.flujo.pasos:
            return "Agrega al menos un paso al flujo"
        for clave, valores in self.flujo.pasos:
            problema = lotes.PASOS[clave].problema(valores)
            if problema:
                return f"«{lotes.PASOS[clave].titulo}»: {problema}"
        if self.r_carpeta.isChecked() and not self.e_carpeta.text():
            return "Elige la carpeta de destino"
        if self.lista.topLevelItemCount() == 0:
            return "Agrega archivos o una carpeta para empezar"
        return None

    def ready(self) -> bool:
        if not hasattr(self, "lista"):
            return False
        if self.modo == "resultado":
            return True
        return self._motivo() is None

    def _donde(self) -> str:
        if self.r_carpeta.isChecked():
            carpeta = self.e_carpeta.text()
            return f"Los resultados se guardan en «{Path(carpeta).name or carpeta}»." if carpeta else ""
        sufijo = self.e_sufijo.text()
        return (f"Cada resultado se guarda junto a su original, con «{sufijo}» en el nombre." if sufijo else
                "Cada resultado se guarda junto a su original.")

    def refresh(self):
        super().refresh()
        if not hasattr(self, "b_vista") or not hasattr(self, "principal"):
            return
        n, k = self.lista.topLevelItemCount(), len(self.flujo.pasos)
        if self.modo == "resultado" and self._informe is not None:
            t = self._informe.totales()
            chips = [(_s(t["hecho"], "hecho", "hechos"), "ok")]
            if t["con aviso"]:
                chips.append((f"{t['con aviso']} con aviso", "warn"))
            if t["omitido"]:
                chips.append(_s(t["omitido"], "omitido", "omitidos"))
            if t["error"]:
                chips.append((f"{t['error']} con error", "danger"))
            self.set_summary(chips, "Los originales no cambiaron. Doble clic en un resultado para abrirlo.")
        elif self.modo == "vista" and self._plan is not None:
            plan = self._plan
            chips = [(_s(len(plan.activas), "se procesa", "se procesan"), "cat")]
            omitidos = len(plan.tareas) - len(plan.activas)
            if omitidos:
                chips.append(_s(omitidos, "se omite", "se omiten"))
            if plan.protegidos:
                chips.append((_s(len(plan.protegidos), "con contraseña", "con contraseña"), "warn"))
            chips.append(_s(k, "paso", "pasos"))
            self.set_summary(chips, plan.problema or "Nada se ha tocado todavía.")
        else:
            chips = []
            if n:
                chips.append(_s(n, "archivo", "archivos"))
                paginas = sum(int(it.text(1)) for it in self.lista.items() if it.text(1).isdigit())
                if paginas:
                    chips.append(_s(paginas, "página", "páginas"))
            if k:
                chips.append((_s(k, "paso", "pasos"), "cat"))
            self.set_summary(chips, self._motivo() or self._donde())
            self.principal.setCurrentWidget(self.card_archivos if n else self.zona)
        self.b_vista.setEnabled(n > 0 and k > 0)
        self.b_vista.setVisible(self.modo == "editar" and n > 0 and k > 0)
        self._botones_lista()

    def _modo(self, modo: str):
        self.modo = modo
        # Revisar el plan o el resultado ocupa todo el ancho: el panel de ajustes vuelve con «Volver».
        if self.panel is not None:
            self.panel.setVisible(modo == "editar")
        if modo == "vista":
            self.principal.setCurrentWidget(self.card_vista)
        elif modo == "resultado":
            self.principal.setCurrentWidget(self.card_res)
        self.b_volver.setVisible(modo in ("vista", "resultado"))
        self.b_probar.setVisible(modo == "vista" and self.b_probar.isEnabled())
        self.action.setText("Empezar otro lote" if modo == "resultado" else "Ejecutar el lote")
        self.action.setToolTip("Vacía la lista para empezar otro lote" if modo == "resultado" else
                               "Procesa todos los archivos con este flujo")
        scroll = self.findChild(QScrollArea)
        if scroll is not None:
            scroll.verticalScrollBar().setValue(0)
        self.refresh()

    # ------------------------------------------------------------------ plan y vista previa
    def _planificar(self, entradas=None, flujo: lotes.Flujo | None = None) -> lotes.Plan:
        flujo = flujo or self._flujo_actual()
        with PDF_LOCK:
            return lotes.planificar(flujo, entradas if entradas is not None else self.lista.entradas())

    def _vista_previa(self):
        plan = self._planificar()
        self._plan = plan
        t = self.tabla_vista
        t.setUpdatesEnabled(False)
        t.clear()
        filas = []
        for tarea in plan.tareas:
            if tarea.omitir:
                resultado = f"Omitido: {tarea.omitir}"
            elif tarea.salida:
                resultado = Path(tarea.salida).name
            elif "nombrar" in [plan.flujo.pasos[k][0] for k in tarea.pasos]:
                resultado = "Nombre según su contenido"
            else:
                resultado = "Se imprime (no deja archivo)"
            pasos = ", ".join(plan.titulos(tarea)) or "—"
            it = QTreeWidgetItem([Path(tarea.origen).name, resultado, pasos, "; ".join(tarea.avisos)])
            it.setToolTip(0, tarea.origen)
            it.setToolTip(1, tarea.salida or resultado)
            it.setToolTip(2, pasos)
            it.setToolTip(3, "; ".join(tarea.avisos))
            if tarea.omitir:
                for c in range(4):
                    it.setForeground(c, QColor(GRIS))
            elif tarea.avisos:
                for c in range(4):
                    it.setBackground(c, AMBAR)
            filas.append(it)
        t.addTopLevelItems(filas)
        t.setUpdatesEnabled(True)
        partes = [f"{_s(len(plan.tareas), 'archivo', 'archivos')} en la lista; "
                  f"{_s(len(plan.activas), 'se procesará', 'se procesarán')}"]
        if plan.protegidos:
            partes.append(f"{_s(len(plan.protegidos), 'está protegido', 'están protegidos')} (se pedirá la contraseña)")
        omitidos = len(plan.tareas) - len(plan.activas)
        if omitidos:
            partes.append(f"{_s(omitidos, 'se omite', 'se omiten')}")
        destino = plan.flujo.destino
        donde = (f"en «{destino.get('carpeta')}»" if destino.get("modo") == "carpeta" else
                 f"junto a cada original, con el sufijo «{destino.get('sufijo', '')}»")
        self.vista_info.setText(" · ".join(partes) + f". Los resultados se guardarán {donde}.")
        avisos = list(plan.avisos) + ([plan.problema] if plan.problema else [])
        self.vista_aviso_txt.setText(" ".join(avisos))
        self.vista_aviso.setVisible(bool(avisos))
        self.b_probar.setEnabled(bool(plan.activas) and not plan.problema)
        self._modo("vista")

    # ------------------------------------------------------------------ ejecutar
    def run(self):
        if self.modo == "resultado":
            self._nuevo_lote()
            return
        self._ejecutar()

    def _claves_nuevas(self, flujo: lotes.Flujo) -> dict | None:
        claves = {}
        for k, (clave, _v) in enumerate(flujo.pasos):
            paso = lotes.PASOS[clave]
            for o in paso.opciones:
                if o.tipo == "clave":
                    pw = pedir_clave_nueva(self, paso.titulo)
                    if not pw:
                        return None
                    claves.setdefault(k, {})[o.clave] = pw
        return claves

    def _preparar(self, flujo: lotes.Flujo) -> dict | None:
        preparados = {}
        for k, (clave, valores) in enumerate(flujo.pasos):
            paso = lotes.PASOS[clave]
            if paso.preparar is not None:
                try:
                    preparados[k] = paso.preparar(paso.completar(valores))
                except core.UserError as exc:
                    avisar(self, paso.titulo, str(exc))
                    return None
        return preparados

    def _ejecutar(self, entradas=None):
        flujo = self._flujo_actual()
        plan = self._planificar(entradas, flujo)
        if plan.problema:
            avisar(self, "No se puede ejecutar", plan.problema)
            return
        contrasenas = pedir_contrasenas(self, [t.origen for t in plan.protegidos])
        claves = self._claves_nuevas(flujo)
        if claves is None:
            return
        preparados = self._preparar(flujo)
        if preparados is None:
            return
        self._plan = plan
        self._recordar()
        self.lista.blockSignals(True)               # solo se borra el estado anterior: la lista no cambia
        for it in self.lista.items():
            it.setText(3, "")
            it.setData(0, _ESTADO, None)
        self.lista.blockSignals(False)
        self.lista.viewport().update()
        ctx.run_task("Procesando el lote…", lotes.ejecutar, self._terminado, plan=plan, contrasenas=contrasenas,
                     claves_nuevas=claves, preparados=preparados)

    def _terminado(self, inf: lotes.Informe):
        self._informe = inf
        t = self.tabla_res
        t.setUpdatesEnabled(False)
        t.clear()
        filas = []
        for r, (sim, nombre, resultado, detalle, tam) in zip(inf.archivos, inf.a_filas()):
            it = QTreeWidgetItem([sim, nombre, resultado, detalle, tam])
            color = QColor(COLOR_ESTADO.get(r.estado, TINTA))
            it.setForeground(0, color)
            it.setTextAlignment(0, Qt.AlignmentFlag.AlignCenter)
            it.setToolTip(0, r.estado.capitalize())
            it.setToolTip(1, r.origen)
            it.setToolTip(2, r.salida or "\n".join(r.extras) or "")
            it.setToolTip(3, "\n".join([f"{p}: {d}" for p, d in r.pasos] + ([r.mensaje] if r.mensaje else [])))
            it.setData(0, Qt.ItemDataRole.UserRole, r.salida or (r.extras[0] if r.extras else None))
            if r.estado == "error":
                it.setForeground(3, QColor(ROJO))
            elif r.estado == "con aviso":
                for c in range(5):
                    it.setBackground(c, AMBAR)
            filas.append(it)
        t.addTopLevelItems(filas)
        t.setUpdatesEnabled(True)
        self.res_titulo.setText("Lote cancelado: esto es lo que alcanzó a hacerse" if inf.cancelado
                                else "Resultado del lote")
        self.res_resumen.setText(inf.resumen())
        self.res_resumen.setStyleSheet(f"color: {ROJO};" if inf.totales()["error"] else "")
        por_ruta = {os.path.normcase(r.origen): r for r in inf.archivos}
        self.lista.blockSignals(True)               # solo cambia el estado de cada fila, no la lista
        for it in self.lista.items():
            r = por_ruta.get(os.path.normcase(it.data(0, Qt.ItemDataRole.UserRole)))
            if r is not None:
                it.setText(3, f"{lotes.SIMBOLOS.get(r.estado, '')} {r.estado.capitalize()}")
                it.setToolTip(3, r.detalle)
                it.setData(0, _ESTADO, r.estado)
        self.lista.blockSignals(False)
        self.lista.viewport().update()
        fallidos = inf.totales()["error"] > 0
        hay_salida = any(r.salida or r.extras for r in inf.archivos)
        self.b_reintentar.setEnabled(fallidos)
        self.b_reintentar.setVisible(fallidos)
        self.b_abrir.setEnabled(hay_salida)
        self.b_abrir.setVisible(hay_salida)
        self._modo("resultado")

    def _carpeta_resultado(self) -> str | None:
        inf = self._informe
        if inf is None:
            return None
        if inf.destino and os.path.isdir(inf.destino):
            return inf.destino
        for r in inf.archivos:
            for p in ([r.salida] if r.salida else []) + r.extras:
                if os.path.exists(p):
                    return p
        return None

    def _abrir_carpeta(self):
        destino = self._carpeta_resultado()
        if destino:
            reveal(destino)

    def _abrir_resultado(self, it: QTreeWidgetItem, _col: int = 0):
        ruta = it.data(0, Qt.ItemDataRole.UserRole)
        if ruta and str(ruta).lower().endswith(".pdf") and os.path.exists(ruta):
            view_in_app(ruta)
        elif ruta and os.path.exists(ruta):
            reveal(ruta)

    def _guardar_informe(self):
        if self._informe is None:
            return
        base = self._carpeta_resultado() or ctx.last_dir
        base = base if os.path.isdir(base) else os.path.dirname(base)
        ruta = pedir_ruta_informe(self, str(core.unique_path(Path(base) / "Informe del lote.xlsx")))
        if not ruta:
            return
        try:
            if ruta.lower().endswith(".txt"):
                self._informe.guardar_txt(ruta)
            else:
                self._informe.guardar_xlsx(ruta)
        except (core.UserError, OSError) as exc:
            avisar(self, "No se pudo guardar el informe", str(exc))
            return
        show_done(self, "Informe guardado", f"Se guardó como «{Path(ruta).name}».", file=ruta)

    def _reintentar(self):
        if self._informe is None:
            return
        fallidos = {os.path.normcase(r.origen) for r in self._informe.archivos if r.estado == "error"}
        entradas = [(p, raiz) for p, raiz in self.lista.entradas() if os.path.normcase(p) in fallidos]
        if not entradas:
            return
        for p, _ in entradas:                     # una contraseña equivocada se vuelve a pedir
            if ctx.passwords.get(p):
                with PDF_LOCK:
                    vale = core.check_password(p, ctx.passwords.get(p))
                if not vale:
                    ctx.passwords._pw.pop(ctx.passwords._key(p), None)
        self._ejecutar(entradas)

    def _nuevo_lote(self):
        self.lista.clear_all()
        self._informe = None
        self._plan = None
        self._modo("editar")

    # ------------------------------------------------------------------ probar
    def _probar(self):
        plan = self._plan or self._planificar()
        tarea = next(iter(plan.activas), None)
        if tarea is None:
            avisar(self, "Probar con el primer archivo", plan.problema or "Ningún archivo se puede procesar.")
            return
        flujo = self._flujo_actual()
        flujo.pasos = [(c, v) for c, v in flujo.pasos if c != "imprimir"]
        if not flujo.pasos:
            avisar(self, "Probar con el primer archivo", "Este flujo solo imprime, y en la prueba no se imprime nada.")
            return
        _limpiar_pruebas()
        carpeta = tempfile.mkdtemp(prefix="ronypdf-prueba-")
        _PRUEBAS.append(carpeta)
        flujo.destino = {"modo": "carpeta", "carpeta": carpeta, "sufijo": flujo.destino.get("sufijo") or "_prueba",
                         "conservar_subcarpetas": False}
        plan_p = self._planificar([(tarea.origen, None)], flujo)
        if plan_p.problema:
            avisar(self, "Probar con el primer archivo", plan_p.problema)
            return
        contrasenas = pedir_contrasenas(self, [t.origen for t in plan_p.protegidos])
        claves = self._claves_nuevas(flujo)
        if claves is None:
            return
        ctx.run_task(f"Probando con «{Path(tarea.origen).name}»…", lotes.ejecutar, self._prueba_lista, plan=plan_p,
                     contrasenas=contrasenas, claves_nuevas=claves)

    def _prueba_lista(self, inf: lotes.Informe):
        r = inf.archivos[0] if inf.archivos else None
        if r is None or inf.cancelado:
            return
        if r.estado == "error":
            avisar(self, "La prueba no salió bien", f"«{Path(r.origen).name}»: {r.mensaje}")
            return
        self.vista_aviso_txt.setText("Prueba: " + (r.detalle or "sin cambios") + ". El destino no se tocó.")
        self.vista_aviso.show()
        if r.salida and r.salida.lower().endswith(".pdf"):
            ctx.add_recent = lambda *a, **k: None    # la copia temporal no va a «Archivos recientes»
            try:
                view_in_app(r.salida)
            finally:
                del ctx.add_recent
        elif r.extras:
            show_done(self, "Prueba lista", r.detalle, file=r.extras[0] if os.path.isfile(r.extras[0]) else None,
                      folder=r.extras[0] if os.path.isdir(r.extras[0]) else None)


def _limpiar_pruebas():
    while len(_PRUEBAS) > 1:                      # la última puede seguir abierta en el visor
        shutil.rmtree(_PRUEBAS.pop(0), ignore_errors=True)


def al_cerrar(_ventana) -> None:
    """Al cerrar la aplicación se borran las carpetas temporales de las pruebas."""
    while _PRUEBAS:
        shutil.rmtree(_PRUEBAS.pop(), ignore_errors=True)


# --------------------------------------------------------------------------
# Renombrar por contenido
# --------------------------------------------------------------------------

FICHAS_BOTONES = (("Fecha", "{fecha}"), ("NIT", "{nit}"), ("Cédula", "{cedula}"), ("N.° de factura", "{factura}"),
                  ("Texto después de…", "{texto}"), ("Título", "{titulo}"), ("Nombre original", "{nombre}"),
                  ("Consecutivo", "{n:03}"))
ESTADO_TEXTO = {"ok": "Listo", "igual": "Ya tiene ese nombre"}
_TONO = Qt.ItemDataRole.UserRole + 5           # tono del chip de estado (ok, warn, danger, cat o neutral)


class RenombrarPage(ToolPage):
    key = "rename"
    title = "Renombrar por contenido"
    subtitle = ("Pon a cada archivo un nombre con su fecha, NIT, cédula o número de factura, leídos de su contenido "
                "(también en escaneos).")
    icon_name = "rename"
    action_text = "Renombrar"

    def build(self):
        self.cache: dict[tuple, object] = {}       # (ruta, páginas, ocr) → Lectura o {"_error": …}
        self.manuales: dict[str, str] = {}         # ruta → nombre escrito a mano
        self.errores: dict[str, str] = {}          # ruta → error del último renombrado
        self.hechos: set[str] = set()              # rutas nuevas del último renombrado
        self.propuestas: list[renombrar.Propuesta] = []
        self.items: dict[str, QTreeWidgetItem] = {}
        self._registro = renombrar.ultimo_registro()
        self._problema: str | None = None
        self._leyendo = False
        self.footer_info.setWordWrap(True)

        # Paso 1: la plantilla del nombre
        g = self.step("¿Cómo se llamarán?", help="Escribe el nombre o toca un dato para agregarlo.")
        self.e_plantilla = QLineEdit(renombrar.PLANTILLA_DEFECTO)
        self.e_plantilla.setToolTip("Escribe el nombre con fichas entre llaves. Lo que no esté entre llaves se "
                                    "copia tal cual (guiones, espacios, palabras).")
        self.e_plantilla.setClearButtonEnabled(True)
        self.fichas = QWidget()
        flujo = FlowLayout(self.fichas, spacing=6)
        flujo.setContentsMargins(0, 0, 0, 0)
        for texto, ficha in FICHAS_BOTONES:
            b = _boton(texto, lambda _=False, f=ficha: self._insertar(f), glifo="add",
                       ayuda=f"Agrega {ficha} a la plantilla")
            flujo.addWidget(b)
        self.ejemplo = label("", "Help", wrap=True)
        g.addWidget(_campo("Plantilla", self.e_plantilla, self.fichas, self.ejemplo))
        self.e_palabra = QLineEdit()
        self.e_palabra.setPlaceholderText("Por ejemplo: Radicado")
        self.e_palabra.setToolTip("Para la ficha {texto}: se toma lo que sigue a esta palabra, hasta el fin del renglón")
        self.caja_palabra = _campo("Palabra clave de «Texto después de…»", self.e_palabra)
        g.addWidget(self.caja_palabra)
        self.c_formato = QComboBox()
        self.c_formato.addItem("AAAA-MM-DD (ordena bien)", "AAAA-MM-DD")
        self.c_formato.addItem("AAAAMMDD", "AAAAMMDD")
        self.c_formato.addItem("DD-MM-AAAA", "DD-MM-AAAA")
        g.addWidget(_campo("Formato de fecha", self.c_formato))

        # Paso 2: renombrar o copiar (y, plegado, cómo se leen)
        g = self.step("¿Qué hacer con los archivos?")
        self.modo_og = OptionGroup()
        self.r_renombrar = self.modo_og.add("Cambiarles el nombre", "En su misma carpeta. Se puede deshacer.",
                                            "renombrar")
        self.r_copiar = self.modo_og.add("Dejar copias con el nombre nuevo",
                                         "En otra carpeta; los originales no cambian.", "copiar")
        g.addWidget(self.modo_og)
        self.e_carpeta = QLineEdit()
        self.e_carpeta.setReadOnly(True)
        self.e_carpeta.setPlaceholderText("Ninguna carpeta elegida")
        b = QPushButton("Elegir…")
        b.setObjectName("Secondary")
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(self._elegir_carpeta)
        fila = QHBoxLayout()
        fila.setContentsMargins(0, 0, 0, 0)
        fila.setSpacing(8)
        fila.addWidget(self.e_carpeta, 1)
        fila.addWidget(b)
        self.caja_carpeta = _campo("Carpeta de las copias", fila)
        g.addWidget(self.caja_carpeta)
        self.b_avanzadas, av = _avanzadas(g)
        self.c_ocr = SwitchRow("Leer con OCR los escaneados", "Tarda más, pero encuentra los datos en escaneos y "
                               "fotos.", checked=True)
        av.addWidget(self.c_ocr)
        self.s_paginas = Segmented([("1", 1), ("2", 2), ("3", 3)], current=1)
        self.s_paginas.setToolTip("Cuántas páginas de cada archivo se leen para buscar los datos")
        fila = QHBoxLayout()
        fila.setContentsMargins(0, 0, 0, 0)
        fila.addWidget(self.s_paginas)
        fila.addStretch(1)
        av.addWidget(_campo("Páginas que se leen de cada archivo", fila))
        self.c_omitir = SwitchRow("Si falta un dato, dejar el nombre original",
                                  "Si no, se marca con «sin-fecha», «sin-nit»…")
        av.addWidget(self.c_omitir)
        self.c_sub = SwitchRow("Incluir subcarpetas", "Al agregar una carpeta, también entran los PDF de sus "
                               "subcarpetas.")
        av.addWidget(self.c_sub)

        # Área principal: zona para soltar o la tabla de nombres
        self.zona = _ZonaSoltar("Arrastra aquí facturas, cartas o una carpeta con PDF",
                                "Cada archivo recibe un nombre con los datos que se leen de su contenido.", "rename",
                                self._agregar_archivos, self._agregar_carpeta)
        self.zona.soltados.connect(self.accept_files)
        self.tabla = _TablaNombres(self)
        self.b_vaciar = _boton("Vaciar lista", self._vaciar, estilo="Ghost", tam=None)
        self.aviso, self.aviso_txt = _franja()
        self.b_claves = _boton("Escribir contraseñas…", self._pedir_claves_protegidos)
        self.aviso.layout().addWidget(self.b_claves, 0, Qt.AlignmentFlag.AlignVCenter)
        caja = QWidget()
        v = QVBoxLayout(caja)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(8)
        v.addLayout(_barra("Archivos y nombres nuevos", self.b_vaciar))
        v.addWidget(self.tabla, 1)
        v.addWidget(self.aviso)
        v.addWidget(label("Doble clic en un nombre nuevo para corregirlo. En ámbar: falta un dato o el nombre se "
                          "repetía (se agrega «(2)»). Nunca se reemplaza un archivo que ya exista.", "Help", wrap=True))
        self.caja_tabla = caja
        self.principal = QStackedWidget()
        self.principal.addWidget(self.zona)
        self.principal.addWidget(caja)
        self.set_main(self.principal)

        # Encabezado: agregar más (solo cuando ya hay una lista a la que agregar)
        self.cab = QWidget()
        h = QHBoxLayout(self.cab)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        h.addWidget(_boton("Agregar carpeta", self._agregar_carpeta, glifo="folder", tam=None))
        h.addWidget(_boton("Agregar archivos", self._agregar_archivos, glifo="add", tam=None))
        self.set_header_widget(self.cab)

        self.b_deshacer = self.add_secondary("Deshacer el último renombrado", self._deshacer)

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(150)
        self.timer.timeout.connect(self._actualizar)
        for w in (self.e_plantilla, self.e_palabra):
            w.textChanged.connect(lambda *_: self.timer.start())
        self.e_plantilla.textChanged.connect(self._palabra_visible)
        self.c_formato.currentIndexChanged.connect(lambda *_: self.timer.start())
        self.c_omitir.toggled.connect(lambda *_: self.timer.start())
        self.modo_og.changed.connect(self._modo_cambio)
        self.c_ocr.toggled.connect(lambda *_: self._leer_pendientes())
        self.s_paginas.changed.connect(lambda *_: self._leer_pendientes())
        self._palabra_visible()
        self._modo_cambio()

    def __init__(self):
        super().__init__()
        self._actualizar()

    def _palabra_visible(self, *_):
        """La palabra clave solo hace falta con la ficha {texto}."""
        self.caja_palabra.setVisible("{texto" in self.e_plantilla.text() or bool(self.e_palabra.text()))

    def _modo_cambio(self, *_):
        self.caja_carpeta.setVisible(self.r_copiar.isChecked())
        self.timer.start()

    # ------------------------------------------------------------------ archivos
    def _rutas(self) -> list[str]:
        return list(self.items)

    def _agregar_archivos(self):
        rutas = elegir_archivos(self, "Documentos PDF (*.pdf)")
        if rutas:
            self.accept_files(rutas)

    def _agregar_carpeta(self):
        carpeta = elegir_carpeta(self, "Elige la carpeta con los PDF")
        if carpeta:
            self.accept_files([carpeta])

    def accept_files(self, paths: list[str]) -> None:
        items, avisos = lotes.expandir(list(paths), self.c_sub.isChecked(), set(core.PDF_EXTS))
        nuevos = [r for r, _ in items if Path(r).suffix.lower() in core.PDF_EXTS and r not in self.items]
        for ruta in nuevos:
            it = QTreeWidgetItem([Path(ruta).name, "", "Leyendo…"])
            it.setToolTip(0, ruta)
            it.setIcon(0, icon("pdf", tema.color("brand"), size=18))
            it.setData(0, Qt.ItemDataRole.UserRole, ruta)
            self.items[ruta] = it
        if nuevos:
            self.tabla.setUpdatesEnabled(False)
            self.tabla.addTopLevelItems([self.items[r] for r in nuevos])
            self.tabla.setUpdatesEnabled(True)
            ctx.remember_dir(nuevos[-1])
        if avisos and ctx.window is not None and hasattr(ctx.window, "set_status"):
            ctx.window.set_status(" ".join(avisos), 6000)
        self._leer_pendientes()

    def _quitar(self):
        for it in self.tabla.selectedItems():
            ruta = it.data(0, Qt.ItemDataRole.UserRole)
            self.items.pop(ruta, None)
            self.manuales.pop(ruta, None)
            self.tabla.takeTopLevelItem(self.tabla.indexOfTopLevelItem(it))
        self._actualizar()

    def _vaciar(self):
        self.tabla.clear()
        self.items.clear()
        self.manuales.clear()
        self.errores.clear()
        self.hechos.clear()
        self._actualizar()

    def _elegir_carpeta(self):
        carpeta = elegir_carpeta(self, "Elige dónde dejar las copias")
        if carpeta:
            self.e_carpeta.setText(carpeta)
            self.r_copiar.setChecked(True)
            self._actualizar()

    # ------------------------------------------------------------------ lectura
    def _clave(self, ruta: str) -> tuple:
        return (os.path.normcase(os.path.abspath(ruta)), self.s_paginas.value(), self.c_ocr.isChecked())

    def _leer_pendientes(self, preguntar: bool = True):
        pendientes = [r for r in self._rutas() if self._clave(r) not in self.cache]
        if not pendientes:
            self._actualizar()
            return
        if ctx.busy or self._leyendo:
            QTimer.singleShot(300, lambda: self._leer_pendientes(preguntar))
            return
        with PDF_LOCK:
            protegidos = [r for r in pendientes if not ctx.passwords.get(r) and core.needs_password(r)]
        if protegidos and preguntar:
            pedir_contrasenas(self, protegidos)
        contrasenas = {r: ctx.passwords.get(r) for r in pendientes if ctx.passwords.get(r)}
        opciones = dict(paginas=self.s_paginas.value(), usar_ocr=self.c_ocr.isChecked())
        self._leyendo = True
        for r in pendientes:
            self.items[r].setText(2, "Leyendo…")

        def listo(res: dict):
            for ruta, lectura in res.items():
                self.cache[(os.path.normcase(os.path.abspath(ruta)), opciones["paginas"], opciones["usar_ocr"])] = lectura
            self._leyendo = False
            self._leer_pendientes(preguntar=False)

        def siempre():
            self._leyendo = False
            QTimer.singleShot(0, self._actualizar)

        ctx.run_task("Leyendo los archivos…", renombrar.leer_lote, listo, on_finally=siempre, rutas=pendientes,
                     contrasenas=contrasenas, **opciones)

    def _pedir_claves_protegidos(self):
        protegidos = [p.origen for p in self.propuestas if p.estado == "protegido"]
        if not protegidos:
            return
        pedir_contrasenas(self, protegidos)
        for r in protegidos:
            if ctx.passwords.get(r):
                self.cache.pop(self._clave(r), None)
        self._leer_pendientes(preguntar=False)

    # ------------------------------------------------------------------ propuestas
    def _insertar(self, ficha: str):
        self.e_plantilla.insert(ficha)
        self.e_plantilla.setFocus()
        if ficha == "{texto}" and not self.e_palabra.text().strip():
            self.e_palabra.setFocus()
            if ctx.window is not None and hasattr(ctx.window, "set_status"):
                ctx.window.set_status("Escribe la palabra clave: se tomará lo que la sigue en el documento", 5000)

    def _datos(self) -> dict:
        palabra = self.e_palabra.text()
        out = {}
        for r in self._rutas():
            lectura = self.cache.get(self._clave(r))
            if lectura is None:
                out[r] = {"_error": "Leyendo…" if self._leyendo else "Sin leer: vuelve a agregarlo"}
            elif isinstance(lectura, dict):
                out[r] = lectura
            else:
                out[r] = renombrar.datos_de(lectura, r, palabra)
        return out

    def _actualizar(self):
        if not hasattr(self, "tabla"):
            return
        rutas = self._rutas()
        self._problema = None
        try:
            renombrar.validar_plantilla(self.e_plantilla.text())
        except core.UserError as exc:
            self._problema = str(exc)
        copiar = self.r_copiar.isChecked()
        if not self._problema:
            self.propuestas = renombrar.proponer(
                rutas, self.e_plantilla.text(), formato_fecha=self.c_formato.currentData(),
                si_falta="omitir" if self.c_omitir.isChecked() else "marcar", datos=self._datos(),
                manuales=self.manuales, carpeta=(self.e_carpeta.text() or None) if copiar else None)
        else:
            self.propuestas = []
        self._pintar()
        self.refresh()

    def _pintar(self):
        t = self.tabla
        t.blockSignals(True)
        t.setUpdatesEnabled(False)
        por_ruta = {p.origen: p for p in self.propuestas}
        protegidos = 0
        for ruta, it in self.items.items():
            p = por_ruta.get(ruta)
            fondo = None
            color, tono = QColor(TINTA), "neutral"
            flags = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
            if p is None:
                nuevo, estado = "", "Revisa la plantilla" if self._problema else ""
            else:
                nuevo = p.nuevo if p.incluir or p.estado in ("igual", "falta") else ""
                estado = p.detalle or ESTADO_TEXTO.get(p.estado, "")
                if p.estado == "ok":
                    tono = "cat" if p.manual else "ok"
                    if not p.manual:
                        estado = "Listo"
                if p.estado in ("falta", "duplicado"):
                    fondo, tono = AMBAR, "warn"
                if p.estado in ("error", "protegido"):
                    color, tono = QColor(ROJO), "danger"
                    protegidos += p.estado == "protegido"
                if p.estado == "igual":
                    color = QColor(GRIS)
                if p.estado not in ("error", "protegido"):
                    flags |= Qt.ItemFlag.ItemIsEditable
            if ruta in self.errores:
                estado, color, tono = f"No se pudo: {self.errores[ruta]}", QColor(ROJO), "danger"
            elif ruta in self.hechos and p is not None and p.estado == "igual":
                estado, color, tono = "✓ Nombre cambiado", QColor(VERDE), "ok"
            it.setFlags(flags)
            it.setText(0, Path(ruta).name)
            it.setText(1, nuevo)
            it.setText(2, estado)
            it.setData(2, _TONO, tono)
            it.setToolTip(1, nuevo)
            it.setToolTip(2, estado)
            for c in range(3):
                it.setBackground(c, fondo if fondo is not None else QColor(0, 0, 0, 0))
            it.setForeground(2, color)
            it.setForeground(1, QColor(GRIS) if p is not None and p.estado == "igual" else QColor(TINTA))
        t.setUpdatesEnabled(True)
        t.blockSignals(False)
        if protegidos:
            self.aviso_txt.setText(f"{_s(protegidos, 'archivo está protegido', 'archivos están protegidos')} y no se "
                                   "pudieron leer.")
            self.aviso.show()
        elif self._problema:
            self.aviso_txt.setText(self._problema)
            self.b_claves.hide()
            self.aviso.show()
        else:
            self.aviso.hide()
        self.b_claves.setVisible(bool(protegidos))
        muestra = next((p for p in self.propuestas if p.estado in ("ok", "falta", "duplicado") and p.incluir), None)
        self.ejemplo.setText(f"Ejemplo: {Path(muestra.origen).name}  →  {muestra.nuevo}" if muestra else
                             "Fichas: {fecha} {año} {mes} {dia} {nit} {nit_2} {cedula} {factura} {texto} {titulo} "
                             "{nombre} {paginas} {n} (o {n:03} con ceros).")

    def _editado(self, it: QTreeWidgetItem, col: int):
        if col != 1:
            return
        ruta = it.data(0, Qt.ItemDataRole.UserRole)
        texto = it.text(1).strip()
        propuesta = next((p for p in self.propuestas if p.origen == ruta), None)
        if not texto or (propuesta is not None and texto == propuesta.nuevo and not propuesta.manual):
            self.manuales.pop(ruta, None)
        else:
            self.manuales[ruta] = texto
        QTimer.singleShot(0, self._actualizar)

    def _activas(self) -> list[renombrar.Propuesta]:
        return [p for p in self.propuestas if p.incluir and p.estado not in ("igual", "error", "protegido")]

    def ready(self) -> bool:
        if not hasattr(self, "tabla") or self._problema or self._leyendo:
            return False
        if self.r_copiar.isChecked() and not self.e_carpeta.text():
            return False
        return bool(self._activas())

    def refresh(self):
        super().refresh()
        if not hasattr(self, "b_deshacer"):
            return
        activas = self._activas()
        n = len(self.items)
        copiar = self.r_copiar.isChecked()
        verbo = "Copiar" if copiar else "Renombrar"
        self.action.setText(f"{verbo} {_s(len(activas), 'archivo', 'archivos')}" if activas else verbo)
        chips = [_s(n, "archivo", "archivos")] if n else []
        if self._problema:
            texto = self._problema
        elif not n:
            texto = "Agrega PDF para ver sus nombres nuevos"
        elif self._leyendo:
            texto = "Leyendo los archivos…"
        elif copiar and not self.e_carpeta.text():
            texto = "Elige la carpeta donde se dejarán las copias"
        else:
            faltan = sum(1 for p in self.propuestas if p.estado == "falta")
            dup = sum(1 for p in self.propuestas if p.estado == "duplicado")
            chips.append((f"{len(activas)} {'cambia' if len(activas) == 1 else 'cambian'}", "cat"))
            if faltan:
                chips.append((f"{faltan} con datos faltantes", "warn"))
            if dup:
                chips.append((_s(dup, "duplicado", "duplicados"), "warn"))
            texto = ("Los originales no cambian." if copiar else
                     "Cada archivo cambia de nombre en su carpeta. Se puede deshacer.")
        self.set_summary(chips, texto)
        self.principal.setCurrentWidget(self.caja_tabla if n else self.zona)
        self.cab.setVisible(n > 0)
        if hasattr(self, "b_deshacer"):
            self.b_deshacer.setEnabled(self._registro is not None and not ctx.busy)
            self.b_deshacer.setVisible(self._registro is not None)
            if self._registro is not None:
                self.b_deshacer.setToolTip(f"Devuelve el nombre original a {_s(len(self._registro.pares), 'archivo', 'archivos')} "
                                           f"(renombrado del {self._registro.fecha.replace('T', ' a las ')})")

    # ------------------------------------------------------------------ renombrar y deshacer
    def run(self):
        activas = self._activas()
        if not activas:
            return
        copiar = self.r_copiar.isChecked()
        carpeta = self.e_carpeta.text() or None
        n = len(activas)
        if copiar:
            texto = f"Se dejarán {_s(n, 'copia', 'copias')} con el nombre nuevo en «{carpeta}»."
            detalle = "Los originales no cambian."
            si = "Copiar"
        else:
            texto = f"Se cambiará el nombre de {_s(n, 'archivo', 'archivos')} en su misma carpeta."
            detalle = "Si te arrepientes, usa «Deshacer el último renombrado»."
            si = "Renombrar"
        faltan = sum(1 for p in activas if p.estado == "falta")
        if faltan:
            detalle += f" {_s(faltan, 'archivo queda', 'archivos quedan')} marcados con «sin-…» porque falta un dato."
        if not confirmar(self, "Renombrar archivos", texto, si, "Cancelar", detalle):
            return
        self.errores.clear()
        self.hechos.clear()
        ctx.run_task("Cambiando los nombres…", renombrar.aplicar, self._aplicado, propuestas=activas,
                     modo="copiar" if copiar else "renombrar", carpeta=carpeta)

    def _mover_ruta(self, viejo: str, nuevo: str):
        it = self.items.pop(viejo, None)
        if it is None:
            return
        it.setData(0, Qt.ItemDataRole.UserRole, nuevo)
        it.setToolTip(0, nuevo)
        self.items[nuevo] = it
        for clave in [c for c in self.cache if c[0] == os.path.normcase(os.path.abspath(viejo))]:
            self.cache[(os.path.normcase(os.path.abspath(nuevo)),) + clave[1:]] = self.cache.pop(clave)
        if self.manuales.pop(viejo, None):
            self.manuales[nuevo] = Path(nuevo).name          # sigue siendo un nombre escrito a mano
        pw = ctx.passwords.get(viejo)
        if pw:
            ctx.passwords.set(nuevo, pw)

    def _aplicado(self, res: renombrar.Resultado):
        copiar = res.registro is not None and res.registro.modo == "copiar"
        if not copiar:
            for viejo, nuevo in res.nuevos.items():
                self._mover_ruta(viejo, nuevo)
                self.hechos.add(nuevo)
        self.errores = dict(res.errores)
        if res.registro is not None and res.registro.pares and res.registro.archivo:
            self._registro = res.registro
        self._actualizar()
        partes = []
        if res.errores:
            partes.append(f"{_s(len(res.errores), 'archivo no se pudo cambiar', 'archivos no se pudieron cambiar')}; "
                          "en la columna «Estado» está el motivo.")
        if copiar:
            titulo = f"{_s(res.hechos, 'copia creada', 'copias creadas')}"
            show_done(self, titulo, " ".join(partes) or "Los originales no cambiaron.",
                      folder=self.e_carpeta.text() or None)
        else:
            titulo = f"{_s(res.hechos, 'archivo renombrado', 'archivos renombrados')}"
            partes.append("Puedes deshacerlo con «Deshacer el último renombrado».")
            carpeta = os.path.dirname(next(iter(res.nuevos.values()))) if res.nuevos else None
            show_done(self, titulo, " ".join(partes), folder=carpeta)

    def _deshacer(self):
        reg = renombrar.ultimo_registro() or self._registro
        if reg is None or reg.deshecho:
            self._registro = None
            self.refresh()
            avisar(self, "Deshacer", "No hay ningún renombrado para deshacer.")
            return
        if reg.modo == "copiar":
            if not confirmar(self, "Deshacer las copias", f"Se borrarán las {_s(len(reg.pares), 'copia', 'copias')} "
                             "que se dejaron con el nombre nuevo.", "Borrar las copias", "Cancelar",
                             "Solo se borran las que no se hayan modificado después. Los originales no cambian."):
                return
        hechos = renombrar.deshacer(reg)
        if reg.modo == "renombrar":
            for origen, destino in reg.pares:
                if os.path.exists(origen) and destino in self.items:
                    self._mover_ruta(destino, origen)
        self.hechos.clear()
        self.errores.clear()
        self._registro = renombrar.ultimo_registro()
        self._actualizar()
        verbo = "se borraron" if reg.modo == "copiar" else "volvieron a su nombre original"
        show_done(self, "Se deshizo el último renombrado",
                  f"{hechos} de {_s(len(reg.pares), 'archivo', 'archivos')} {verbo}."
                  + ("" if hechos == len(reg.pares) else " Los demás ya no estaban donde quedaron."))


class _TablaNombres(QTreeWidget):
    """Tabla «Nombre actual → Nombre nuevo → Estado»; el nombre nuevo se corrige con doble clic."""

    def __init__(self, page: RenombrarPage):
        super().__init__()
        self.page = page
        self.setColumnCount(3)
        self.setHeaderLabels(["Nombre actual", "Nombre nuevo", "Estado"])
        self.setRootIsDecorated(False)
        self.setUniformRowHeights(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setMinimumHeight(230)
        self.setIconSize(QSize(18, 18))
        hdr = self.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        hdr.resizeSection(0, 170)
        hdr.resizeSection(2, 170)
        self.setItemDelegate(_Fondo(self))
        self.setItemDelegateForColumn(0, _NoEditar(self))
        self.setItemDelegateForColumn(2, _ChipEstado(self))
        self.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.itemChanged.connect(page._editado)

    def resizeEvent(self, e):
        # El nombre nuevo es lo que más importa: las otras dos columnas se quedan con lo justo.
        super().resizeEvent(e)
        w = self.viewport().width()
        self.header().resizeSection(0, max(140, min(240, int(w * 0.26))))
        self.header().resizeSection(2, max(110, min(220, int(w * 0.24))))

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        rutas = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        e.acceptProposedAction()
        if rutas:
            self.page.accept_files(rutas)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Delete and self.state() != QAbstractItemView.State.EditingState:
            self.page._quitar()
            return
        super().keyPressEvent(e)

    def contextMenuEvent(self, e):
        it = self.itemAt(e.pos())
        if it is None:
            return
        if not it.isSelected():
            self.clearSelection()
            it.setSelected(True)
        m = QMenu(self)
        if it.flags() & Qt.ItemFlag.ItemIsEditable:
            m.addAction("Corregir el nombre nuevo", lambda: self.editItem(it, 1))
        n = len(self.selectedItems())
        m.addAction("Quitar de la lista (Supr)" if n == 1 else f"Quitar {n} de la lista (Supr)", self.page._quitar)
        m.exec(e.globalPos())

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.topLevelItemCount() == 0:
            p = QPainter(self.viewport())
            r = self.viewport().rect()
            p.drawPixmap(r.center().x() - 18, r.center().y() - 46, pixmap("upload", tema.color("line-control"), 36, 1.6))
            p.setPen(QColor(tema.color("ink-3")))
            p.drawText(r.adjusted(20, 10, -20, 10), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                       "Arrastra aquí facturas, cartas o una carpeta con PDF")
            p.end()


class _ChipEstado(_NoEditar):
    """Columna «Estado» de la tabla de nombres: el texto como chip (``rp-chip``) en el tono de su estado."""

    def paint(self, painter, option, index):
        fondo = index.data(Qt.ItemDataRole.BackgroundRole)
        if fondo is not None:
            color = fondo.color() if hasattr(fondo, "color") else QColor(fondo)
            if color.alpha():
                painter.fillRect(option.rect, color)
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, QColor(tema.tonos(cat_de(self.parent()))["band"]))
        texto = index.data(Qt.ItemDataRole.DisplayRole) or ""
        if not texto:
            return
        t = tema.tokens()
        tono = index.data(_TONO) or "neutral"
        bg, fg = ((t[f"{tono}-tint"], t[tono]) if tono in ("ok", "warn", "danger", "info") else
                  (tema.tonos(cat_de(self.parent()))["band"], tema.tonos(cat_de(self.parent()))["ink"])
                  if tono == "cat" else (t["surface-sunken"], t["ink-2"]))
        if tono == "warn" and fondo is not None:
            bg = t["surface"]                      # sobre la fila ámbar, el chip va en blanco
        f = tema.fuente("chip", option.font)
        fm = QFontMetrics(f)
        r = QRectF(option.rect).adjusted(4, 0, -6, 0)
        texto = fm.elidedText(texto, Qt.TextElideMode.ElideRight, int(r.width() - 20))
        cr = QRectF(r.left(), r.center().y() - 12, min(r.width(), fm.horizontalAdvance(texto) + 20), 24)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(bg))
        painter.drawRoundedRect(cr, 12, 12)
        painter.setFont(f)
        painter.setPen(QColor(fg))
        painter.drawText(cr, Qt.AlignmentFlag.AlignCenter, texto)
        painter.restore()


REGISTRO = [
    dict(clave="batch", titulo="Acciones por lotes", seccion="optimizar", orden=30, pagina=LotesPage,
         dibujo=dibujo_lotes, inicio="Aplica varios pasos a muchos archivos de una vez"),
    dict(clave="rename", titulo="Renombrar por contenido", seccion="organizar", orden=30, pagina=RenombrarPage,
         dibujo=dibujo_renombrar, inicio="Nombra facturas y cartas por su fecha, NIT o número"),
]
