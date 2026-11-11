"""Comentarios y «Mis sellos», personales: la extensión de «Ver y editar PDF» y la pantalla «Mis sellos».

En el visor agrega dos botones a la barra («Comentar» y «Sello», cada uno con su menú), el panel
«Comentarios (n)» junto a «Propiedades» y dos acciones en «Más acciones» (resumen y aplanar).  Los
comentarios llevan el nombre de quien usa la aplicación (se pregunta la primera vez que comenta o sella,
nunca al arrancar ni al abrir un PDF) y los sellos son los que cada persona diseña.

Cómo funciona (ver ``comentarios.py``): el archivo del disco más una lista de operaciones es la verdad.
Cada operación se aplica enseguida al documento en memoria del visor con ``comentarios.aplicar``, la misma
función que se usa al guardar, así MuPDF dibuja el comentario tal como quedará y los comentarios hechos en
otros programas se tratan igual que los propios.  Deshacer quita la operación y vuelve a armar la vista
desde el archivo; rehacer la vuelve a poner.

Enganches usados (extensiones.py): barra_visor, paneles_visor, al_abrir_visor, evento_visor,
menu_seleccion_visor, cambios_visor, guardar_visor, aviso_visor y ACCIONES_VISOR.  Además, como el visor no
avisa cuando vuelve a abrir el archivo después de un guardado que falló, la extensión envuelve
``visor.canvas.reopen`` de esa instancia para volver a poner los comentarios que aún no se han guardado.
"""

from __future__ import annotations

import base64
import copy
import datetime as _dt
import getpass
import math
import os
import re
import time
from pathlib import Path

from PySide6.QtCore import QAbstractListModel, QEvent, QModelIndex, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap, QRegion
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QColorDialog, QComboBox, QDialog,
                               QFileDialog, QFrame, QGraphicsPixmapItem, QGraphicsRectItem, QHBoxLayout, QLabel,
                               QLineEdit, QListView, QListWidget, QListWidgetItem, QMenu, QMessageBox,
                               QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSlider,
                               QStackedWidget, QStyle, QStyledItemDelegate, QToolButton, QToolTip, QVBoxLayout,
                               QWidget, QWidgetAction)

from . import art, comentarios, core, extensiones, firmas, icons, sellos, tema, visor_texto, widgets
from .comentarios import ESTADOS, ESTADOS_LISTA, MARCAS, TIPOS, Comentario, Filtro
from .sellos import Sello
from .widgets import (PDF_LOCK, Chip, FileChip, FlowLayout, GlyphTile, Note, Segmented, StepList, SwitchRow,
                      ToolPage, ctx, label)

CLAVE = "comentarios"
ESCALA_SELLO = 4.0
HERRAMIENTAS = {
    "ext:nota": "Nota adhesiva", "ext:subrayar": "Subrayar", "ext:tachar": "Tachar",
    "ext:ondulado": "Subrayado ondulado", "ext:cuadro": "Cuadro de texto", "ext:sello": "Sello",
}
TIPO_DE_HERRAMIENTA = {"ext:subrayar": "subrayado", "ext:tachar": "tachado", "ext:ondulado": "ondulado"}
TECLAS = {"N": "ext:nota", "U": "ext:subrayar", "K": "ext:tachar", "X": "ext:cuadro"}
AYUDAS = {
    "ext:nota": "Haz clic donde quieras dejar la nota y escribe. Esc o un clic afuera la termina.",
    "ext:subrayar": "Arrastra sobre el texto que quieres subrayar. Esc para terminar.",
    "ext:tachar": "Arrastra sobre el texto que quieres tachar. Esc para terminar.",
    "ext:ondulado": "Arrastra sobre el texto que quieres marcar con una línea ondulada. Esc para terminar.",
    "ext:cuadro": "Arrastra para dibujar el cuadro (o haz clic) y escribe dentro.",
    "ext:sello": "Haz clic donde va el sello. Con Mayús presionada puedes poner varios seguidos.",
}
SIN_TEXTO_MSG = "Para subrayar o tachar, primero reconoce el texto (OCR)."
VACIO_MSG = "Este PDF no tiene comentarios. Usa «Comentar» para agregar el primero."
TONO_ESTADO = {"Accepted": "ok", "Rejected": "danger", "Cancelled": "neutral", "Completed": "info"}
PALETA = {
    "nota": ["#F5B700", "#2563EB", "#1F7A4D", "#D63A3A", "#7C3AED"],
    "marca": ["#FFE14D", "#1F6FD1", "#D63A3A", "#1F7A4D", "#D97706"],
    "cuadro": ["#1F2937", "#1E4FA0", "#B42323", "#1F7A4D", "#6D28D9"],
}
NOMBRES_COLOR = {"#F5B700": "Amarillo", "#FFE14D": "Amarillo", "#2563EB": "Azul", "#1F6FD1": "Azul",
                 "#1E4FA0": "Azul", "#1F7A4D": "Verde", "#D63A3A": "Rojo", "#B42323": "Rojo",
                 "#7C3AED": "Morado", "#6D28D9": "Morado", "#D97706": "Naranja", "#1F2937": "Negro"}


# ======================================================================== colores e íconos

def color_estado(estado: str | None) -> tuple[str, str]:
    """(fondo, tinta) de la pastilla de un estado, con los tonos del sistema (``ok``, ``danger``, ``info``)."""
    tono = TONO_ESTADO.get(estado or "", "neutral")
    if tono == "neutral":
        return tema.color("surface-sunken"), tema.color("ink-2")
    return tema.color(f"{tono}-tint"), tema.color(tono)


def _gl(nombre: str, color: str = "ink-2", size: int = 16) -> QIcon:
    """Glifo teñido para un menú o un botón (``art.glyph_icon``) en un token de ``tema``."""
    return art.glyph_icon(nombre, color if color.startswith("#") else tema.color(color), size)


def _gl_barra(nombre: str) -> QIcon:
    """Glifo de la barra del visor (``ink-2``; encendido, la tinta de «ver»), el mismo de sus herramientas."""
    from .viewer import glifo_barra
    return glifo_barra(nombre)


def _color_marco() -> QColor:
    """Marco de lo seleccionado en la página: el mismo color que usa el visor (categoría «ver»)."""
    return QColor(tema.tonos("ver")["c"])


def _qss_comentarios() -> str:
    """Hoja del panel «Comentarios» y de los botones de su tarjeta, desde los tokens."""
    t = tema.tokens()
    return f"""
QToolButton#FiltroComentarios {{ border: 1px solid {t['line-strong']}; border-radius: 8px; padding: 5px 10px;
    background: {t['surface']}; color: {t['ink']}; font-size: 13px; font-weight: 500; }}
QToolButton#FiltroComentarios:hover {{ background: {t['surface-hover']}; }}
QToolButton#FiltroComentarios::menu-indicator {{ image: none; width: 0; }}
QToolButton#FiltroComentarios[tam="sm"] {{ padding: 4px 8px; }}
QPushButton#FiltroComentarios {{ border: 1px solid {t['line-strong']}; border-radius: 8px; padding: 4px 10px;
    background: {t['surface']}; color: {t['ink']}; font-size: 13px; font-weight: 600; }}
QPushButton#FiltroComentarios:hover {{ background: {t['surface-hover']}; }}
QToolButton#AutorComentarios {{ border: none; border-radius: 8px; padding: 5px 8px; background: transparent;
    color: {t['ink-2']}; font-size: 12px; }}
QToolButton#AutorComentarios:hover {{ background: {t['surface-hover']}; color: {t['ink']}; }}
QToolButton#AutorComentarios::menu-indicator {{ image: none; width: 0; }}
QListView#ListaComentarios {{ background: {t['surface']}; border: none; outline: none; padding: 0; }}
QPlainTextEdit#CajaRespuesta {{ background: {t['surface']}; border: 1px solid {t['line-control']}; border-radius: 8px;
    padding: 4px 6px; font-size: 13px; }}
QPlainTextEdit#CajaRespuesta:focus {{ border: 2px solid {t['focus']}; padding: 3px 5px; }}
"""


def _l(p, x1, y1, x2, y2):
    p.drawLine(QPointF(x1, y1), QPointF(x2, y2))


def _dib_comentar(p: QPainter, base: QColor, acento: QColor):
    path = QPainterPath()
    path.moveTo(6.5, 4.5)
    path.lineTo(17.5, 4.5)
    path.quadTo(20.5, 4.5, 20.5, 7.5)
    path.lineTo(20.5, 13.5)
    path.quadTo(20.5, 16.5, 17.5, 16.5)
    path.lineTo(11.5, 16.5)
    path.lineTo(7.5, 20.2)
    path.lineTo(7.8, 16.5)
    path.lineTo(6.5, 16.5)
    path.quadTo(3.5, 16.5, 3.5, 13.5)
    path.lineTo(3.5, 7.5)
    path.quadTo(3.5, 4.5, 6.5, 4.5)
    p.drawPath(path)
    extensiones.lapiz(p, acento)
    _l(p, 7.5, 9, 16.5, 9)
    _l(p, 7.5, 12.3, 13.5, 12.3)


def _dib_sello(p: QPainter, base: QColor, acento: QColor):
    p.drawEllipse(QRectF(9, 2.5, 6, 5))
    _l(p, 10.6, 7.3, 10.2, 11.5)
    _l(p, 13.4, 7.3, 13.8, 11.5)
    p.drawRoundedRect(QRectF(4, 11.5, 16, 4.6), 1.2, 1.2)
    extensiones.lapiz(p, acento)
    _l(p, 4.5, 19.8, 19.5, 19.8)


def _dib_nota(p: QPainter, base: QColor, acento: QColor):
    path = QPainterPath()
    path.moveTo(4, 4)
    path.lineTo(20, 4)
    path.lineTo(20, 14.5)
    path.lineTo(14.5, 20)
    path.lineTo(4, 20)
    path.closeSubpath()
    p.drawPath(path)
    p.drawPolyline([QPointF(20, 14.5), QPointF(14.5, 14.5), QPointF(14.5, 20)])
    extensiones.lapiz(p, acento)
    _l(p, 7.5, 8.5, 16.5, 8.5)
    _l(p, 7.5, 11.5, 13, 11.5)


def _dib_subrayar(p: QPainter, base: QColor, acento: QColor):
    p.drawPolyline([QPointF(7, 4), QPointF(7, 11.5)])
    p.drawPolyline([QPointF(17, 4), QPointF(17, 11.5)])
    path = QPainterPath(QPointF(7, 11.5))
    path.cubicTo(7, 17, 17, 17, 17, 11.5)
    p.drawPath(path)
    extensiones.lapiz(p, acento)
    _l(p, 4.5, 20, 19.5, 20)


def _dib_tachar(p: QPainter, base: QColor, acento: QColor):
    path = QPainterPath(QPointF(16.5, 6.5))
    path.cubicTo(15, 3.5, 7, 3.5, 7.5, 7.5)
    path.cubicTo(8, 10.5, 16.5, 11, 16.5, 15.5)
    path.cubicTo(16.5, 20, 8, 20, 7, 16.5)
    p.drawPath(path)
    extensiones.lapiz(p, acento)
    _l(p, 4, 12, 20, 12)


def _dib_ondulado(p: QPainter, base: QColor, acento: QColor):
    _l(p, 4.5, 6, 19.5, 6)
    _l(p, 4.5, 10.5, 15, 10.5)
    extensiones.lapiz(p, acento)
    path = QPainterPath(QPointF(4, 17))
    for k in range(4):
        x = 4 + k * 4
        path.quadTo(x + 1, 14, x + 2, 17)
        path.quadTo(x + 3, 20, x + 4, 17)
    p.drawPath(path)


def _dib_cuadro(p: QPainter, base: QColor, acento: QColor):
    p.drawRoundedRect(QRectF(3, 5, 18, 14), 2, 2)
    extensiones.lapiz(p, acento)
    _l(p, 8, 9, 16, 9)
    _l(p, 12, 9, 12, 15.5)


def _dib_resumen(p: QPainter, base: QColor, acento: QColor):
    p.drawRoundedRect(QRectF(4.5, 3, 15, 18), 2, 2)
    for y in (8, 12, 16):
        _l(p, 10.5, y, 16.5, y)
    extensiones.lapiz(p, acento)
    for y in (8, 12, 16):
        p.drawEllipse(QPointF(7.6, y), 0.9, 0.9)


def _dib_aplanar(p: QPainter, base: QColor, acento: QColor):
    p.drawPolygon([QPointF(12, 4), QPointF(20.5, 8.5), QPointF(12, 13), QPointF(3.5, 8.5)])
    p.drawPolyline([QPointF(3.5, 12.5), QPointF(12, 17), QPointF(20.5, 12.5)])
    extensiones.lapiz(p, acento)
    p.drawPolyline([QPointF(3.5, 16.5), QPointF(12, 21), QPointF(20.5, 16.5)])


def dibujo_sellos(p: QPainter, base: QColor, acento: QColor):
    """Ícono de «Mis sellos»: un sello de goma con la marca roja."""
    p.drawEllipse(QRectF(9, 2, 6, 5))
    _l(p, 10.6, 6.8, 10.2, 10.5)
    _l(p, 13.4, 6.8, 13.8, 10.5)
    p.drawRoundedRect(QRectF(4.5, 10.5, 15, 4.5), 1.2, 1.2)
    extensiones.lapiz(p, acento)
    p.drawRoundedRect(QRectF(3, 17, 18, 4.5), 1.5, 1.5)


for _n, _f in (("comentar", _dib_comentar), ("sello_visor", _dib_sello), ("com_nota", _dib_nota),
               ("com_subrayar", _dib_subrayar), ("com_tachar", _dib_tachar), ("com_ondulado", _dib_ondulado),
               ("com_cuadro", _dib_cuadro), ("com_resumen", _dib_resumen), ("com_aplanar", _dib_aplanar)):
    icons.EXTRA.setdefault(_n, _f)

ICONO_TIPO = {"nota": "com_nota", "resaltado": "highlighter", "subrayado": "com_subrayar", "tachado": "com_tachar",
              "ondulado": "com_ondulado", "cuadro": "com_cuadro", "sello": "sello_visor", "otro": "comentar"}
ICONO_HERRAMIENTA = {"ext:nota": "com_nota", "ext:subrayar": "com_subrayar", "ext:tachar": "com_tachar",
                     "ext:ondulado": "com_ondulado", "ext:cuadro": "com_cuadro", "ext:sello": "sello_visor"}


# ======================================================================== autor

def autor() -> str:
    s = ctx.settings
    return (s.get("comentarios_autor", "") or "").strip() if s is not None else ""


def cargo() -> str:
    s = ctx.settings
    return (s.get("comentarios_cargo", "") or "").strip() if s is not None else ""


def poner_autor(nombre: str, cargo_: str | None = None) -> None:
    s = ctx.settings
    if s is None:
        return
    s.set("comentarios_autor", (nombre or "").strip()[:80])
    if cargo_ is not None:
        s.set("comentarios_cargo", (cargo_ or "").strip()[:80])


def sugerencia_nombre() -> str:
    """El nombre de usuario de Windows, como sugerencia («ana.perez» → «Ana Perez»)."""
    try:
        u = getpass.getuser() or ""
    except Exception:  # noqa: BLE001
        u = os.environ.get("USERNAME", "")
    u = re.sub(r"[._\-]+", " ", u).strip()
    return " ".join(w[:1].upper() + w[1:] for w in u.split())


def datos_sello() -> dict:
    return {"nombre": autor(), "cargo": cargo(), "fecha": _dt.datetime.now()}


class DialogoAutor(QDialog):
    """«¿Con qué nombre quieres firmar tus comentarios y sellos?» (la primera vez, o desde el panel)."""

    def __init__(self, parent=None, cambiar: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Tu nombre")
        self.setModal(True)
        self.nombre = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 18)
        lay.setSpacing(6)
        titulo = label("¿Con qué nombre firmas tus comentarios y sellos?", "Title2", wrap=True)
        lay.addWidget(titulo)
        lay.addWidget(label("Se guarda solo en este equipo. Puedes cambiarlo cuando quieras en el panel "
                            "«Comentarios».", "BodySm", wrap=True))
        lay.addSpacing(10)
        lay.addWidget(label("Tu nombre", "Label"))
        self.campo = QLineEdit(autor() or sugerencia_nombre())
        self.campo.setPlaceholderText("Por ejemplo: Ana Pérez")
        self.campo.setMaxLength(80)
        self.campo.selectAll()
        lay.addWidget(self.campo)
        lay.addSpacing(8)
        lay.addWidget(label("Tu cargo (opcional, para tus sellos)", "Label"))
        self.campo_cargo = QLineEdit(cargo())
        self.campo_cargo.setPlaceholderText("Por ejemplo: Coordinadora")
        self.campo_cargo.setMaxLength(80)
        lay.addWidget(self.campo_cargo)
        lay.addSpacing(8)
        self.error = Note("", "danger")
        self.error.hide()
        lay.addWidget(self.error)
        lay.addSpacing(8)
        fila = QHBoxLayout()
        fila.setSpacing(8)
        fila.addStretch(1)
        b_cancel = QPushButton("Cancelar")
        b_cancel.setObjectName("Secondary")
        b_cancel.clicked.connect(self.reject)
        self.b_ok = QPushButton("Guardar mi nombre")
        self.b_ok.setObjectName("Primary")
        self.b_ok.setDefault(True)
        self.b_ok.clicked.connect(self.guardar)
        fila.addWidget(b_cancel)
        fila.addWidget(self.b_ok)
        lay.addLayout(fila)
        self.setMinimumWidth(440)

    def guardar(self):
        nombre = " ".join(self.campo.text().split())
        if not nombre:
            self.error.setText("Escribe tu nombre para continuar.")
            self.error.show()
            return
        poner_autor(nombre, self.campo_cargo.text())
        self.nombre = nombre
        self.accept()


def preguntar_autor(parent=None, cambiar: bool = False) -> str | None:
    d = DialogoAutor(parent, cambiar)
    return d.nombre if d.exec() == QDialog.DialogCode.Accepted and d.nombre else None


def asegurar_autor(parent=None) -> str | None:
    """El nombre de quien comenta; la primera vez se pregunta.  None si la persona cancela."""
    return autor() or preguntar_autor(parent)


# ======================================================================== estado por visor

class _Estado:
    def __init__(self):
        self.ops: list[dict] = []
        self.comentarios: list[Comentario] = []
        self.por_pagina: dict[int, list[Comentario]] = {}
        self.por_id: dict[str, Comentario] = {}
        self.sel: str | None = None
        self.marco = None
        self.tirador = None
        self.arrastre: dict | None = None
        self.fantasma = None
        self.sello: Sello | None = None
        self.png_sello: bytes | None = None
        self.editor: "_Editor | None" = None
        self.aviso_permiso = False
        self.propio = False
        self.hover_id: str | None = None
        self.pend_marcado: list[int] = []
        self.busqueda: dict[int, str] = {}

    @property
    def creados(self) -> set[str]:
        return {op["nm"] for op in self.ops if op.get("nm")}


def _est(visor) -> _Estado:
    est = visor.ext.get(CLAVE)
    if est is None:
        est = visor.ext[CLAVE] = _Estado()
    return est


def _panel(visor) -> "PanelComentarios | None":
    return visor.ext.get(CLAVE + "_panel")


def _ref(est: _Estado, c) -> dict:
    return comentarios.referencia(c, c.pagina if isinstance(c, Comentario) else 0, est.creados)


# ======================================================================== deshacer

class _Accion:
    """Una acción de comentarios en la pila de «Deshacer» del visor (compartida con sus ediciones)."""

    def __init__(self, visor, ops: list[dict], texto: str):
        self.visor, self.ops, self.texto = visor, ops, texto

    def deshacer(self):
        est = _est(self.visor)
        for op in self.ops:
            for k in range(len(est.ops) - 1, -1, -1):
                if est.ops[k] is op:
                    del est.ops[k]
                    break
        _rehacer_vista(self.visor, comentarios.paginas_de(self.ops))

    def rehacer(self):
        est = _est(self.visor)
        est.ops.extend(op for op in self.ops if not any(op is o for o in est.ops))
        _rehacer_vista(self.visor, comentarios.paginas_de(self.ops))


def _aplicar_ops(visor, ops: list[dict], texto: str, recordar: bool = True) -> bool:
    """Aplica operaciones nuevas al documento en memoria, las anota y las apila en «Deshacer»."""
    c = visor.canvas
    if c.doc is None or ctx.busy or not ops:
        return False
    est = _est(visor)
    try:
        with PDF_LOCK:
            comentarios.aplicar(c.doc, ops)
    except Exception as exc:  # noqa: BLE001 — se vuelve a armar la vista sin estas operaciones
        _rehacer_vista(visor)
        QMessageBox.warning(visor, "Comentarios", f"No se pudo hacer el cambio:\n{exc}")
        return False
    paginas = comentarios.paginas_de(ops)
    if recordar:
        est.ops.extend(ops)
        c.registrar_ext(_Accion(visor, list(ops), texto))
    c.refrescar(sorted(paginas))
    _recargar(visor, paginas)
    return True


def _rehacer_vista(visor, paginas=None) -> None:
    """Deja el documento en memoria igual al del disco más las operaciones vigentes."""
    c = visor.canvas
    est = _est(visor)
    if not c.path or ctx.busy:
        return
    _cerrar_editor(visor, confirmar=False)
    est.arrastre = None
    fallidas = []
    est.propio = True
    try:
        with PDF_LOCK:
            c.close_doc()
            c.reopen()
            if c.doc is not None:
                for op in list(est.ops):
                    try:
                        comentarios.aplicar(c.doc, [op])
                    except Exception:  # noqa: BLE001 — una operación imposible no impide ver las demás
                        fallidas.append(op)
    finally:
        est.propio = False
    for op in fallidas:
        est.ops.remove(op)
    c.refrescar(None if paginas is None else sorted(paginas))
    _recargar(visor)
    c.edits_changed.emit()


def _preparar(visor) -> None:
    """Una sola vez por visor: envuelve ``canvas.reopen`` y conecta las señales del lienzo."""
    if visor.ext.get(CLAVE + "_listo"):
        return
    visor.ext[CLAVE + "_listo"] = True
    c = visor.canvas
    original = c.reopen

    def reopen():
        original()
        est = visor.ext.get(CLAVE)
        if est is not None and not est.propio and est.ops and c.doc is not None:
            # p. ej. después de un guardado sobre el mismo archivo que falló: los comentarios sin guardar
            # vuelven al documento en memoria
            with PDF_LOCK:
                for op in est.ops:
                    try:
                        comentarios.aplicar(c.doc, [op])
                    except Exception:  # noqa: BLE001
                        pass
            c.refrescar(sorted(comentarios.paginas_de(est.ops)))

    c.reopen = reopen
    c.tool_changed.connect(lambda t: _herramienta_cambio(visor, t))
    c.zoom_changed.connect(lambda _z: _pintar_marco(visor))
    for barra in (c.verticalScrollBar(), c.horizontalScrollBar()):
        barra.valueChanged.connect(lambda _v: _reubicar_editor(visor))


# ======================================================================== modelo en memoria

def _indexar(est: _Estado) -> None:
    est.por_pagina = {}
    est.por_id = {}
    for cm in est.comentarios:
        est.por_pagina.setdefault(cm.pagina, []).append(cm)
        est.por_id[cm.id] = cm
    est.busqueda = {}


def _recargar(visor, paginas=None) -> None:
    """Vuelve a leer los comentarios (todos, o los de esas páginas) del documento en memoria."""
    c = visor.canvas
    est = _est(visor)
    if c.doc is None or c.doc.is_closed:
        return
    with PDF_LOCK:
        if paginas is None:
            est.comentarios = comentarios.leer(c.doc)
        else:
            pags = set(paginas)
            nuevos = comentarios.leer(c.doc, pags)
            est.comentarios = [cm for cm in est.comentarios if cm.pagina not in pags] + nuevos
    _indexar(est)
    if est.sel and est.sel not in est.por_id:
        est.sel = None
    est.pend_marcado = sorted({cm.pagina for cm in est.comentarios if cm.tipo in MARCAS and cm.texto_marcado is None})
    _programar_marcado(visor)
    _pintar_marco(visor)
    p = _panel(visor)
    if p is not None:
        p.recargar()


def _programar_marcado(visor) -> None:
    t = visor.ext.get(CLAVE + "_timer")
    if t is None:
        t = QTimer(visor)
        t.setSingleShot(True)
        t.setInterval(0)
        t.timeout.connect(lambda: _paso_marcado(visor))
        visor.ext[CLAVE + "_timer"] = t
    if _est(visor).pend_marcado and not t.isActive():
        t.start()


def _paso_marcado(visor) -> None:
    """Calcula, poco a poco y sin trabar la pantalla, el texto que cubren los subrayados y resaltados."""
    est = _est(visor)
    c = visor.canvas
    if c.doc is None or ctx.busy:
        if est.pend_marcado:
            visor.ext[CLAVE + "_timer"].start(200)
        return
    inicio = time.perf_counter()
    hechos = 0
    while est.pend_marcado and time.perf_counter() - inicio < 0.025:
        p = est.pend_marcado.pop(0)
        palabras = c.words_for(p)
        for cm in est.por_pagina.get(p, []):
            if cm.tipo in MARCAS and cm.texto_marcado is None:
                cm.texto_marcado = _texto_bajo(palabras, cm.zonas)
                est.busqueda.pop(id(cm), None)
                hechos += 1
    panel = _panel(visor)
    if est.pend_marcado:
        visor.ext[CLAVE + "_timer"].start(0)
    if hechos and panel is not None:
        panel.marcado_listo(final=not est.pend_marcado)


def _texto_bajo(palabras, zonas) -> str:
    out = []
    for w in palabras:
        x0, y0, x1, y1 = w.rect
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if any(z[0] - 1 <= cx <= z[2] + 1 and z[1] - 1 <= cy <= z[3] + 1 for z in zonas):
            out.append(w.texto)
    return " ".join(out)


def _por_nm(est: _Estado, nm: str) -> Comentario | None:
    return next((cm for cm in est.comentarios if cm.nm == nm), None)


def comentario_en(visor, pos) -> Comentario | None:
    """El comentario bajo esa posición del lienzo (el más pequeño, si hay varios encima)."""
    c = visor.canvas
    est = _est(visor)
    r = c.punto_en_pagina(pos)
    if r is None:
        return None
    p, x, y = r
    tol = 3.0 / max(c.zoom, 0.05)
    mejor = None
    for cm in est.por_pagina.get(p, []):
        if cm.oculto:
            continue
        for z in cm.zonas:
            if z[0] - tol <= x <= z[2] + tol and z[1] - tol <= y <= z[3] + tol:
                area = (cm.rect[2] - cm.rect[0]) * (cm.rect[3] - cm.rect[1])
                if mejor is None or area < mejor[0]:
                    mejor = (area, cm)
                break
    return mejor[1] if mejor else None


# ======================================================================== selección y marco

def _quitar_items(*items) -> None:
    for it in items:
        if it is None:
            continue
        try:
            sc = it.scene()
            if sc is not None:
                sc.removeItem(it)
        except RuntimeError:          # ya lo borró la escena (otro documento)
            pass


def _pintar_marco(visor) -> None:
    """Marco punteado alrededor del comentario seleccionado (hijo de su página: se mueve con ella)."""
    est = _est(visor)
    c = visor.canvas
    _quitar_items(est.marco)
    est.marco = est.tirador = None
    cm = est.por_id.get(est.sel) if est.sel else None
    if cm is None or not (0 <= cm.pagina < len(c.pages)):
        return
    try:
        page = c.pages[cm.pagina]
    except (IndexError, RuntimeError):
        return
    z = max(c.zoom, 0.05)
    m = 3.0 / z
    caja = QRectF(QPointF(cm.rect[0], cm.rect[1]), QPointF(cm.rect[2], cm.rect[3])).adjusted(-m, -m, m, m)
    marco = QGraphicsRectItem(caja, page)
    pen = QPen(_color_marco(), 1.6, Qt.PenStyle.DashLine)
    pen.setCosmetic(True)
    marco.setPen(pen)
    velo = _color_marco()
    velo.setAlpha(18)
    marco.setBrush(velo)
    marco.setZValue(50)
    est.marco = marco
    if cm.tipo in ("sello", "cuadro"):
        s = 9.0 / z
        r = marco.rect()
        tir = QGraphicsRectItem(QRectF(r.right() - s / 2, r.bottom() - s / 2, s, s), marco)
        pt = QPen(_color_marco(), 1.4)
        pt.setCosmetic(True)
        tir.setPen(pt)
        tir.setBrush(QColor("white"))
        est.tirador = tir


def seleccionar(visor, cm: Comentario | None, mostrar_panel: bool = True, desde_panel: bool = False) -> None:
    est = _est(visor)
    est.sel = cm.id if cm is not None else None
    _pintar_marco(visor)
    p = _panel(visor)
    if p is not None and not desde_panel:
        p.seleccionar(est.sel)
    if cm is not None and mostrar_panel and visor.panel_actual() != CLAVE:
        visor.mostrar_panel(CLAVE)


def seleccionado(visor) -> Comentario | None:
    est = _est(visor)
    return est.por_id.get(est.sel) if est.sel else None


def ir_a(visor, cm: Comentario) -> None:
    """Lleva la vista al comentario (si no se ve ya) y lo selecciona."""
    c = visor.canvas
    if 0 <= cm.pagina < len(c.pages):
        page = c.pages[cm.pagina]
        r = QRectF(QPointF(cm.rect[0], cm.rect[1]), QPointF(cm.rect[2], cm.rect[3])).translated(page.pos())
        visible = c.mapToScene(c.viewport().rect()).boundingRect()
        if not visible.contains(r.center()):
            c.go_to_dest(cm.pagina, (cm.rect[0], max(0.0, cm.rect[1] - 30)), remember=False)
    seleccionar(visor, cm, mostrar_panel=False, desde_panel=True)


def _sobre_tirador(visor, pos) -> bool:
    est = _est(visor)
    if est.tirador is None:
        return False
    try:
        centro = est.tirador.sceneBoundingRect().center()
    except RuntimeError:
        return False
    v = visor.canvas.mapFromScene(centro)
    return abs(v.x() - pos.x()) <= 8 and abs(v.y() - pos.y()) <= 8


# ======================================================================== editor en la página

class _Texto(QPlainTextEdit):
    terminar = Signal()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape or (e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                                             and e.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.terminar.emit()
            return
        super().keyPressEvent(e)


class _Editor(QFrame):
    """Cuadro pequeño junto al comentario para escribir su texto.  Esc o un clic afuera lo confirma."""

    def __init__(self, visor, titulo: str, texto: str, pagina: int, rect, al_terminar, parcial=None):
        super().__init__(visor.canvas.viewport())
        self.visor, self.pagina, self.rect_pag, self.al_terminar = visor, pagina, rect, al_terminar
        self.parcial = parcial          # parcial(texto, ops) → ops con lo escrito hasta ahora (autoguardado)
        self._hecho = False
        self.setObjectName("EditorNota")
        t = tema.tokens()
        self.setStyleSheet(f"#EditorNota {{ background: {t['surface']}; border: 1px solid {t['line']}; "
                           "border-radius: 12px; }"
                           f"#EditorNota QPlainTextEdit {{ border-radius: 8px; padding: 4px 6px; font-size: 13px; }}")
        tema.sombra(self, 3)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)
        cab = label(titulo, "Label")
        lay.addWidget(cab)
        self.texto = _Texto()
        self.texto.setPlainText(texto)
        self.texto.setPlaceholderText("Escribe aquí…")
        self.texto.setMinimumHeight(70)
        self.texto.terminar.connect(self.terminar)
        self.texto.installEventFilter(self)
        lay.addWidget(self.texto, 1)
        ayuda = label("Esc o un clic afuera para terminar", "Caption")
        lay.addWidget(ayuda)
        self.resize(272, 164)
        self.reubicar()
        self.show()
        self.raise_()
        self.texto.setFocus()
        cur = self.texto.textCursor()
        cur.movePosition(cur.MoveOperation.End)
        self.texto.setTextCursor(cur)

    def reubicar(self):
        c = self.visor.canvas
        if not (0 <= self.pagina < len(c.pages)):
            return
        try:
            page = c.pages[self.pagina]
        except RuntimeError:
            return
        r = self.rect_pag
        a = c.mapFromScene(page.pos() + QPointF(r[2], r[1]))
        b = c.mapFromScene(page.pos() + QPointF(r[0], r[1]))
        vp = c.viewport().rect()
        x = a.x() + 10
        if x + self.width() > vp.right() - 6:
            x = b.x() - self.width() - 10
        x = max(6, min(x, vp.right() - self.width() - 6))
        y = max(6, min(a.y(), vp.bottom() - self.height() - 6))
        self.move(int(x), int(y))

    def eventFilter(self, obj, ev):
        if obj is self.texto and ev.type() == QEvent.Type.FocusOut:
            if ev.reason() not in (Qt.FocusReason.PopupFocusReason, Qt.FocusReason.ActiveWindowFocusReason):
                QTimer.singleShot(0, self, self._quizas_terminar)     # se cancela si el editor ya no existe
        return False

    def _quizas_terminar(self):
        if not self._hecho and not self.isAncestorOf(QApplication.focusWidget() or self):
            self.terminar()

    def terminar(self, confirmar: bool = True):
        if self._hecho:
            return
        self._hecho = True
        texto = self.texto.toPlainText()
        est = self.visor.ext.get(CLAVE)
        if est is not None and est.editor is self:
            est.editor = None
        self.hide()
        self.deleteLater()
        if confirmar:
            self.al_terminar(texto)
        self.visor.canvas.setFocus()


def _abrir_editor(visor, titulo: str, texto: str, pagina: int, rect, al_terminar, parcial=None) -> _Editor:
    _cerrar_editor(visor)
    ed = _Editor(visor, titulo, texto, pagina, rect, al_terminar, parcial)
    _est(visor).editor = ed
    return ed


def _texto_en_curso(visor, ops: list[dict]) -> list[dict]:
    """``ops`` (una copia de las operaciones) con lo que se está escribiendo en el editor abierto, sin
    cerrarlo: así el autoguardado guarda también la nota a medio escribir."""
    est = visor.ext.get(CLAVE)
    ed = est.editor if est is not None else None
    if ed is None or ed.parcial is None:
        return ops
    try:
        texto = ed.texto.toPlainText()
    except RuntimeError:                       # el editor ya se cerró
        return ops
    return ed.parcial(texto, ops)


def _cerrar_editor(visor, confirmar: bool = True) -> None:
    est = visor.ext.get(CLAVE)
    ed = est.editor if est is not None else None
    if ed is not None:
        try:
            ed.terminar(confirmar)
        except RuntimeError:
            est.editor = None


def _reubicar_editor(visor) -> None:
    est = visor.ext.get(CLAVE)
    if est is not None and est.editor is not None:
        try:
            est.editor.reubicar()
        except RuntimeError:
            est.editor = None


def _parcial_al_crear(op: dict):
    """Para el autoguardado: el texto escrito hasta ahora va en la copia de la operación que lo creó."""
    def parcial(texto: str, ops: list[dict]) -> list[dict]:
        for o in ops:
            if o.get("op") == "crear" and o.get("nm") == op.get("nm"):
                o["texto"] = texto
        return ops
    return parcial


def _texto_al_crear(visor, op: dict):
    """Lo que se escribe justo después de crear una nota o un cuadro va en la misma operación (deshacer la
    quita entera); en memoria se pone con «editar», que deja el mismo resultado."""
    def fin(texto: str):
        if texto == (op.get("texto") or "") or not any(op is o for o in _est(visor).ops):
            return
        op["texto"] = texto
        c = visor.canvas
        if c.doc is None:
            return
        with PDF_LOCK:
            comentarios.aplicar(c.doc, [{"op": "editar", "ref": {"pagina": op["pagina"], "nm": op["nm"]},
                                         "texto": texto, "fecha": op["fecha"]}])
        c.refrescar([op["pagina"]])
        _recargar(visor, [op["pagina"]])
        c.edits_changed.emit()
    return fin


# ======================================================================== acciones sobre comentarios

def _op_base(tipo: str, pagina: int, **extra) -> dict:
    op = {"op": "crear", "tipo": tipo, "pagina": pagina, "nm": comentarios.nuevo_nm(), "autor": autor(),
          "texto": "", "fecha": comentarios.ahora_pdf(), "color": comentarios.COLORES[tipo]}
    op.update(extra)
    return op


def crear_nota(visor, pagina: int, x: float, y: float, texto: str = "", editar: bool = True) -> dict | None:
    if not asegurar_autor(visor):
        return None
    c = visor.canvas
    pg = c.pages[pagina]
    x = max(0.0, min(x, pg.w - 18))
    y = max(0.0, min(y, pg.h - 18))
    op = _op_base("nota", pagina, punto=(round(x, 2), round(y, 2)), texto=texto)
    if not _aplicar_ops(visor, [op], "Agregar nota"):
        return None
    cm = _por_nm(_est(visor), op["nm"])
    if cm is not None:
        seleccionar(visor, cm)
        if editar:
            _abrir_editor(visor, f"Nota de {op['autor']}", texto, pagina, cm.rect, _texto_al_crear(visor, op),
                          _parcial_al_crear(op))
    return op


def crear_cuadro(visor, pagina: int, rect, texto: str = "", editar: bool = True) -> dict | None:
    if not asegurar_autor(visor):
        return None
    op = _op_base("cuadro", pagina, rect=tuple(round(v, 2) for v in rect), texto=texto,
                  letra=comentarios.LETRA_CUADRO, fondo=comentarios.FONDO_CUADRO)
    if not _aplicar_ops(visor, [op], "Agregar cuadro de texto"):
        return None
    cm = _por_nm(_est(visor), op["nm"])
    if cm is not None:
        seleccionar(visor, cm)
        if editar:
            _abrir_editor(visor, "Cuadro de texto", texto, pagina, cm.rect, _texto_al_crear(visor, op),
                          _parcial_al_crear(op))
    return op


def marcar(visor, tipo: str, por_pagina, texto: str = "", accion: str | None = None) -> list[dict]:
    """Crea marcas de texto (una por página): ``por_pagina`` = [(página, texto marcado, rectángulos)]."""
    por_pagina = [(p, t, r) for p, t, r in por_pagina if r]
    if not por_pagina or not asegurar_autor(visor):
        return []
    ops = [_op_base(tipo, p, rects=[tuple(round(v, 2) for v in x) for x in rects], texto=texto, marcado=t)
           for p, t, rects in por_pagina]
    nombre = accion or {"subrayado": "Subrayar", "tachado": "Tachar", "ondulado": "Subrayado ondulado",
                        "resaltado": "Resaltar"}[tipo]
    return ops if _aplicar_ops(visor, ops, nombre) else []


def marcar_seleccion(visor, tipo: str) -> list[dict]:
    c = visor.canvas
    if c.view_rot:
        visor.set_tool("select")
        return []
    sel = c.seleccion_por_pagina()
    if not sel:
        return []
    ops = marcar(visor, tipo, sel)
    if ops:
        c.clear_selection()
    return ops


def nota_en_seleccion(visor) -> list[dict]:
    """«Agregar una nota a esta selección»: resalta el texto y abre el editor para escribir la nota."""
    c = visor.canvas
    sel = c.seleccion_por_pagina()
    if not sel or c.view_rot:
        return []
    ops = marcar(visor, "resaltado", sel, accion="Agregar una nota")
    if not ops:
        return []
    c.clear_selection()
    op = ops[-1]
    cm = _por_nm(_est(visor), op["nm"])
    if cm is not None:
        seleccionar(visor, cm)
        _abrir_editor(visor, f"Nota de {op['autor']}", "", op["pagina"], cm.rect, _texto_al_crear(visor, op),
                      _parcial_al_crear(op))
    return ops


def poner_sello(visor, sello: Sello, pagina: int, x: float, y: float) -> dict | None:
    """Pone el sello centrado en (x, y), con la fecha y la hora de este momento."""
    if not asegurar_autor(visor):
        return None
    c = visor.canvas
    datos = datos_sello()
    png = sellos.dibujar(sello, datos, ESCALA_SELLO)
    w, h = sellos.tamano_pt(png, ESCALA_SELLO)
    pg = c.pages[pagina]
    if w > pg.w * 0.95:
        f = pg.w * 0.95 / w
        w, h = w * f, h * f
    x0 = max(0.0, min(x - w / 2, pg.w - w))
    y0 = max(0.0, min(y - h / 2, pg.h - h))
    op = _op_base("sello", pagina, rect=(round(x0, 2), round(y0, 2), round(x0 + w, 2), round(y0 + h, 2)),
                  texto=sellos.texto_plano(sello, datos), png=base64.b64encode(png).decode("ascii"),
                  color=sello.color, asunto=f"Sello: {sello.nombre}")
    if not _aplicar_ops(visor, [op], f"Poner el sello «{sello.nombre}»"):
        return None
    if ctx.settings is not None:
        ctx.settings.set("sellos_ultimo", sello.id)
    cm = _por_nm(_est(visor), op["nm"])
    if cm is not None:
        seleccionar(visor, cm)
    return op


def responder(visor, cm: Comentario, texto: str) -> dict | None:
    texto = (texto or "").strip()
    if not texto or not asegurar_autor(visor):
        return None
    est = _est(visor)
    op = {"op": "responder", "ref": _ref(est, cm), "pagina": cm.pagina, "nm": comentarios.nuevo_nm(),
          "autor": autor(), "texto": texto, "fecha": comentarios.ahora_pdf()}
    return op if _aplicar_ops(visor, [op], "Responder") else None


def cambiar_estado(visor, cm: Comentario, estado: str | None) -> dict | None:
    if cm.estado == estado or not asegurar_autor(visor):
        return None
    est = _est(visor)
    op = {"op": "estado", "ref": _ref(est, cm), "pagina": cm.pagina, "nm": comentarios.nuevo_nm(),
          "autor": autor(), "estado": estado, "fecha": comentarios.ahora_pdf()}
    return op if _aplicar_ops(visor, [op], f"Marcar como «{ESTADOS[estado]}»") else None


def editar_texto(visor, cm: Comentario, texto: str) -> dict | None:
    if texto == cm.texto:
        return None
    est = _est(visor)
    op = {"op": "editar", "ref": _ref(est, cm), "pagina": cm.pagina, "texto": texto, "fecha": comentarios.ahora_pdf()}
    return op if _aplicar_ops(visor, [op], "Editar comentario") else None


def abrir_edicion(visor, cm: Comentario) -> None:
    titulo = f"{cm.nombre_tipo} de {cm.autor}" if cm.autor else cm.nombre_tipo
    cid = cm.id

    def fin(texto: str):
        actual = _est(visor).por_id.get(cid)
        if actual is not None:
            editar_texto(visor, actual, texto)

    def parcial(texto: str, ops: list[dict]) -> list[dict]:
        est = _est(visor)
        actual = est.por_id.get(cid)
        if actual is not None and texto != actual.texto:
            ops.append({"op": "editar", "ref": _ref(est, actual), "pagina": actual.pagina, "texto": texto,
                        "fecha": comentarios.ahora_pdf()})
        return ops
    _abrir_editor(visor, titulo, cm.texto, cm.pagina, cm.rect, fin, parcial)


def cambiar_color(visor, cm: Comentario, hexa: str) -> dict | None:
    if not hexa or hexa.upper() == (cm.color or "").upper() or cm.tipo == "sello":
        return None
    est = _est(visor)
    op = {"op": "color", "ref": _ref(est, cm), "pagina": cm.pagina, "color": hexa.upper(),
          "fecha": comentarios.ahora_pdf()}
    return op if _aplicar_ops(visor, [op], "Cambiar color") else None


def mover(visor, cm: Comentario, rect) -> dict | None:
    est = _est(visor)
    op = {"op": "mover", "ref": _ref(est, cm), "pagina": cm.pagina, "rect": tuple(round(v, 2) for v in rect),
          "fecha": comentarios.ahora_pdf()}
    return op if _aplicar_ops(visor, [op], "Mover comentario") else None


def borrar(visor, cm: Comentario) -> dict | None:
    est = _est(visor)
    op = {"op": "borrar", "ref": _ref(est, cm), "pagina": cm.pagina}
    if est.sel == cm.id:
        est.sel = None
    return op if _aplicar_ops(visor, [op], "Eliminar comentario") else None


def copiar_texto(cm: Comentario) -> str:
    t = cm.texto or cm.texto_marcado or ""
    QApplication.clipboard().setText(t)
    return t


def menu_comentario(visor, cm: Comentario) -> QMenu:
    """Menú del clic derecho sobre un comentario: responder, estado, editar, color y eliminar."""
    m = QMenu(visor.canvas)
    m.addAction(_gl("comentar"), "Responder", lambda: _responder_desde_panel(visor, cm))
    sub = m.addMenu("Estado")
    for k in ESTADOS_LISTA:
        a = sub.addAction(ESTADOS[k], lambda k_=k: cambiar_estado(visor, cm, k_))
        a.setCheckable(True)
        a.setChecked(cm.estado == k)
    m.addAction(_gl("edittext"), "Editar el texto…", lambda: abrir_edicion(visor, cm))
    if cm.tipo != "sello" and cm.subtipo not in ("FileAttachment", "Sound"):
        sub_c = m.addMenu("Cambiar color")
        for hexa in _paleta(cm):
            sub_c.addAction(_muestra(hexa), NOMBRES_COLOR.get(hexa, hexa), lambda h=hexa: cambiar_color(visor, cm, h))
        sub_c.addSeparator()
        sub_c.addAction("Otro color…", lambda: _otro_color(visor, cm))
    m.addAction(_gl("duplicate"), "Copiar el texto", lambda: copiar_texto(cm))
    m.addSeparator()
    m.addAction(_gl("delete"), "Eliminar\tSupr", lambda: borrar(visor, cm))
    return m


def _paleta(cm: Comentario) -> list[str]:
    if cm.tipo == "nota":
        return PALETA["nota"]
    if cm.tipo == "cuadro":
        return PALETA["cuadro"]
    return PALETA["marca"]


def _muestra(hexa: str) -> QIcon:
    pm = QPixmap(16, 16)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(hexa))                        # el color del comentario (no es de la interfaz)
    p.setPen(QPen(QColor(22, 26, 37, 40), 1))
    p.drawEllipse(QRectF(1.5, 1.5, 13, 13))
    p.end()
    return QIcon(pm)


def _otro_color(visor, cm: Comentario) -> None:
    col = QColorDialog.getColor(QColor(cm.color or "#000000"), visor, "Elige un color")
    if col.isValid():
        cambiar_color(visor, cm, col.name().upper())


def _responder_desde_panel(visor, cm: Comentario) -> None:
    seleccionar(visor, cm)
    p = _panel(visor)
    if p is not None:
        visor.mostrar_panel(CLAVE)
        p.abrir_respuesta()


# ======================================================================== herramientas y eventos

def activar(visor, herramienta: str) -> None:
    c = visor.canvas
    if c.doc is None:
        return
    visor.set_tool(herramienta)
    if c.tool == herramienta:
        c.notice.emit(AYUDAS.get(herramienta, ""))
        if visor.panel_actual() != CLAVE:
            visor.mostrar_panel(CLAVE)


def armar_sello(visor, sello: Sello) -> None:
    """Deja la herramienta de sello lista con este diseño (una vista fantasma sigue al ratón)."""
    c = visor.canvas
    if c.doc is None:
        return
    est = _est(visor)
    est.sello = sello
    est.png_sello = sellos.dibujar(sello, datos_sello(), ESCALA_SELLO)
    if ctx.settings is not None:
        ctx.settings.set("sellos_ultimo", sello.id)
    _quitar_items(est.fantasma)
    est.fantasma = None
    activar(visor, "ext:sello")


def _ultimo_sello() -> Sello | None:
    sid = ctx.settings.get("sellos_ultimo", "") if ctx.settings is not None else ""
    if not sid:
        return None
    return sellos.de_fabrica(sid) or sellos.AlmacenSellos().obtener(sid)


def repetir_sello(visor) -> bool:
    est = _est(visor)
    s = est.sello or _ultimo_sello()
    if s is None:
        visor.canvas.notice.emit("Todavía no has puesto ningún sello: elige uno en el botón «Sello».")
        return False
    armar_sello(visor, s)
    return True


def _herramienta_cambio(visor, tool: str) -> None:
    est = visor.ext.get(CLAVE)
    if est is None:
        return
    if tool != "ext:sello":
        _quitar_items(est.fantasma)
        est.fantasma = None
    if tool not in HERRAMIENTAS and est.arrastre and est.arrastre.get("modo") in ("marca", "cuadro"):
        _quitar_items(est.arrastre.get("item"))
        est.arrastre = None
    botones = visor.ext.get(CLAVE + "_botones")
    if botones:
        b_com, b_sello = botones
        b_com.setChecked(tool in HERRAMIENTAS and tool != "ext:sello")
        b_sello.setChecked(tool == "ext:sello")
        if tool in HERRAMIENTAS and tool != "ext:sello":
            b_com.setIcon(_gl_barra(ICONO_HERRAMIENTA[tool]))
        else:
            b_com.setIcon(_gl_barra("comentar"))
    if tool not in HERRAMIENTAS and tool != "select" and visor.panel_actual() == CLAVE:
        visor.mostrar_panel("props")       # las herramientas del visor usan «Propiedades»


def _local(c, pagina: int, pos) -> tuple[float, float]:
    """Punto del ratón en coordenadas de esa página (aunque esté fuera de ella)."""
    sp = c.mapToScene(pos.toPoint())
    o = c.pages[pagina].pos()
    return sp.x() - o.x(), sp.y() - o.y()


def _palabra(palabras, x: float, y: float) -> int | None:
    w = visor_texto.palabra_en(palabras, x, y, 3.0)
    return w if w is not None else visor_texto.palabra_cercana(palabras, x, y)


def _sobre_elemento_del_visor(c, pos) -> bool:
    """¿Hay ahí algo que se agregó con las herramientas del propio visor (que se escribe al guardar)?"""
    return any(callable(getattr(it, "to_edit", None)) for it in c.items(pos.toPoint()))


def _ayuda(cm: Comentario) -> str:
    partes = [f"{cm.autor or 'Sin autor'} · {cm.nombre_tipo}"]
    t = (cm.texto or "").strip()
    if t:
        partes.append(t[:120] + ("…" if len(t) > 120 else ""))
    n = sum(1 for r in cm.respuestas if not r.es_estado)
    extra = []
    if n:
        extra.append(f"{n} respuesta{'s' if n != 1 else ''}")
    if cm.estado:
        extra.append(ESTADOS[cm.estado])
    if extra:
        partes.append(" · ".join(extra))
    return "\n".join(partes)


def _ev_seleccionar(visor, est: _Estado, tipo: str, e) -> bool:
    c = visor.canvas
    if tipo == "press":
        if e.button() != Qt.MouseButton.LeftButton or getattr(c, "_space_down", False):
            return False
        pos = e.position()
        if est.sel and _sobre_tirador(visor, pos):
            cm = est.por_id.get(est.sel)
            if cm is not None:
                est.arrastre = {"modo": "tamano", "id": cm.id, "pos": QPointF(pos), "movido": False,
                                "rect": cm.rect, "pagina": cm.pagina}
                return True
        cm = comentario_en(visor, pos)
        if cm is not None and _sobre_elemento_del_visor(c, pos):
            cm = None                 # lo que se agregó con el visor (texto, firma, forma) queda encima
        if cm is None:
            if est.sel:
                seleccionar(visor, None)
            return False
        _cerrar_editor(visor)
        c.clear_selection()
        if c.scene().focusItem() is not None:          # termina la escritura de un texto del visor
            c.scene().setFocusItem(None, Qt.FocusReason.MouseFocusReason)
        c.scene().clearSelection()
        seleccionar(visor, cm)
        modo = "mover" if cm.movible else ("texto" if cm.tipo in MARCAS else "desplazar")
        est.arrastre = {"modo": modo, "id": cm.id, "pos": QPointF(pos), "movido": False, "rect": cm.rect,
                        "pagina": cm.pagina, "h0": c.horizontalScrollBar().value(),
                        "v0": c.verticalScrollBar().value()}
        return True
    if tipo == "move":
        a = est.arrastre
        if a is not None and e.buttons() & Qt.MouseButton.LeftButton:
            _arrastrar(visor, est, a, e)
            return True
        if not e.buttons():
            est.arrastre = None
            if getattr(c, "_ext_captura", False):
                # Un diálogo (p. ej. el del nombre) pudo quedarse con el «soltar» del ratón: se libera.
                c._ext_captura = False
            return _hover(visor, est, e)
        return False
    if tipo == "release":
        a, est.arrastre = est.arrastre, None
        if a is None:
            return False
        _soltar(visor, est, a)
        return True
    if tipo == "doble":
        cm = comentario_en(visor, e.position())
        if cm is None:
            return False
        seleccionar(visor, cm)
        abrir_edicion(visor, cm)
        return True
    if tipo == "menu":
        sobre_sel = getattr(c, "_over_selection", None)
        if c.has_selection() and sobre_sel is not None and sobre_sel(c.mapToScene(e.pos())):
            return False
        cm = comentario_en(visor, QPointF(e.pos()))
        if cm is None:
            return False
        seleccionar(visor, cm)
        menu_comentario(visor, cm).exec(e.globalPos())
        return True
    return False


def _arrastrar(visor, est: _Estado, a: dict, e) -> None:
    c = visor.canvas
    d = e.position() - a["pos"]
    if not a["movido"]:
        if d.manhattanLength() < QApplication.startDragDistance():
            return
        a["movido"] = True
        if a["modo"] == "texto":
            palabras = c.words_for(a["pagina"])
            x, y = _local(c, a["pagina"], a["pos"])
            a["w0"] = _palabra(palabras, x, y) if palabras else None
            seleccionar(visor, None, mostrar_panel=False)
            QToolTip.hideText()
    modo = a["modo"]
    if modo in ("mover", "tamano"):
        cm = est.por_id.get(a["id"])
        if cm is None or est.marco is None:
            return
        pg = c.pages[a["pagina"]]
        x0, y0, x1, y1 = a["rect"]
        dx = (c.mapToScene(e.position().toPoint()) - c.mapToScene(a["pos"].toPoint()))
        if modo == "mover":
            ddx = max(-x0, min(dx.x(), pg.w - x1))
            ddy = max(-y0, min(dx.y(), pg.h - y1))
            a["nuevo"] = (x0 + ddx, y0 + ddy, x1 + ddx, y1 + ddy)
            est.marco.setPos(QPointF(ddx, ddy))
        else:
            w0, h0 = x1 - x0, y1 - y0
            w = max(24.0, min(w0 + dx.x(), pg.w - x0))
            if cm.tipo == "sello":
                h = w * h0 / max(w0, 1)
                if y0 + h > pg.h:
                    h = pg.h - y0
                    w = h * w0 / max(h0, 1)
            else:
                h = max(16.0, min(h0 + dx.y(), pg.h - y0))
            a["nuevo"] = (x0, y0, x0 + w, y0 + h)
            m = 3.0 / max(c.zoom, 0.05)
            est.marco.setRect(QRectF(x0 - m, y0 - m, w + 2 * m, h + 2 * m))
            if est.tirador is not None:
                s = 9.0 / max(c.zoom, 0.05)
                est.tirador.setRect(QRectF(x0 + w + m - s / 2, y0 + h + m - s / 2, s, s))
    elif modo == "texto":
        if a.get("w0") is None:
            return
        palabras = c.words_for(a["pagina"])
        x, y = _local(c, a["pagina"], e.position())
        w1 = visor_texto.palabra_cercana(palabras, x, y)
        if w1 is not None:
            c.select_words(a["pagina"], a["w0"], w1)
    elif modo == "desplazar":
        c.horizontalScrollBar().setValue(a["h0"] - round(d.x()))
        c.verticalScrollBar().setValue(a["v0"] - round(d.y()))


def _soltar(visor, est: _Estado, a: dict) -> None:
    if not a.get("movido"):
        return
    if a["modo"] in ("mover", "tamano"):
        cm = est.por_id.get(a["id"])
        nuevo = a.get("nuevo")
        if cm is None or nuevo is None or all(abs(u - v) < 0.01 for u, v in zip(nuevo, a["rect"])):
            _pintar_marco(visor)
            return
        mover(visor, cm, nuevo)


def _hover(visor, est: _Estado, e) -> bool:
    c = visor.canvas
    pos = e.position()
    if est.sel and _sobre_tirador(visor, pos):
        c.viewport().setCursor(Qt.CursorShape.SizeFDiagCursor)
        return True
    cm = comentario_en(visor, pos)
    if cm is not None and _sobre_elemento_del_visor(c, pos):
        cm = None
    if cm is None:
        if est.hover_id is not None:
            QToolTip.hideText()
            est.hover_id = None
        return False
    c.viewport().setCursor(Qt.CursorShape.SizeAllCursor if cm.movible else Qt.CursorShape.PointingHandCursor)
    if est.hover_id != cm.id:
        est.hover_id = cm.id
        QToolTip.showText(c.viewport().mapToGlobal(pos.toPoint()), _ayuda(cm), c.viewport())
    return True


def _ev_herramienta(visor, est: _Estado, tipo: str, e) -> bool:
    c = visor.canvas
    tool = c.tool
    if tipo == "menu":
        return False
    izq = tipo in ("press", "doble") and e.button() == Qt.MouseButton.LeftButton
    if tool == "ext:sello":
        if tipo == "move":
            _mover_fantasma(visor, est, e.position())
            return True
        if izq:
            r = c.punto_en_pagina(e.position())
            if r is not None and est.sello is not None:
                seguir = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                sello = est.sello
                op = poner_sello(visor, sello, r[0], r[1], r[2])
                if op is not None and not seguir:
                    visor.set_tool("select")
            return True
        return tipo in ("press", "release", "doble")
    if tool == "ext:nota":
        if izq:
            r = c.punto_en_pagina(e.position())
            if r is not None:
                visor.set_tool("select")
                crear_nota(visor, r[0], r[1], r[2])
            return True
        return tipo in ("press", "release", "doble")
    if tool in TIPO_DE_HERRAMIENTA:
        if izq:
            r = c.punto_en_pagina(e.position())
            if r is None:
                return True
            p, x, y = r
            palabras = c.words_for(p)
            if not palabras:
                c.status.emit(SIN_TEXTO_MSG)
                return True
            w = _palabra(palabras, x, y)
            est.arrastre = {"modo": "marca", "pagina": p, "a": w, "b": w}
            c.select_words(p, w, w)
            return True
        a = est.arrastre
        if tipo == "move":
            if a is not None and a.get("modo") == "marca" and e.buttons() & Qt.MouseButton.LeftButton:
                palabras = c.words_for(a["pagina"])
                x, y = _local(c, a["pagina"], e.position())
                w = visor_texto.palabra_cercana(palabras, x, y)
                if w is not None and w != a["b"]:
                    a["b"] = w
                    c.select_words(a["pagina"], a["a"], w)
            return True
        if tipo == "release":
            est.arrastre = None
            if a is None or a.get("modo") != "marca":
                return True
            lo, hi = sorted((a["a"], a["b"]))
            texto, rects = visor_texto.rango(c.words_for(a["pagina"]), lo, hi)
            c.clear_selection()
            marcar(visor, TIPO_DE_HERRAMIENTA[tool], [(a["pagina"], texto, rects)])
            return True
        return tipo == "doble"
    if tool == "ext:cuadro":
        if izq:
            r = c.punto_en_pagina(e.position())
            if r is None:
                return True
            p, x, y = r
            item = QGraphicsRectItem(QRectF(x, y, 0, 0), c.pages[p])
            pen = QPen(_color_marco(), 1.4, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            item.setPen(pen)
            item.setBrush(QColor(255, 249, 219, 150))
            item.setZValue(40)
            est.arrastre = {"modo": "cuadro", "pagina": p, "x": x, "y": y, "item": item}
            return True
        a = est.arrastre
        if tipo == "move":
            if a is not None and a.get("modo") == "cuadro" and e.buttons() & Qt.MouseButton.LeftButton:
                pg = c.pages[a["pagina"]]
                x, y = _local(c, a["pagina"], e.position())
                x, y = max(0.0, min(x, pg.w)), max(0.0, min(y, pg.h))
                a["item"].setRect(QRectF(QPointF(a["x"], a["y"]), QPointF(x, y)).normalized())
            return True
        if tipo == "release":
            est.arrastre = None
            if a is None or a.get("modo") != "cuadro":
                return True
            r = a["item"].rect()
            _quitar_items(a["item"])
            pg = c.pages[a["pagina"]]
            if r.width() < 24 or r.height() < 14:
                w, h = min(200.0, pg.w), min(60.0, pg.h)
                x0 = max(0.0, min(a["x"], pg.w - w))
                y0 = max(0.0, min(a["y"], pg.h - h))
                r = QRectF(x0, y0, w, h)
            visor.set_tool("select")
            crear_cuadro(visor, a["pagina"], (r.left(), r.top(), r.right(), r.bottom()))
            return True
        return tipo == "doble"
    return False


def _mover_fantasma(visor, est: _Estado, pos) -> None:
    c = visor.canvas
    if est.png_sello is None:
        return
    if est.fantasma is None:
        img = QImage.fromData(est.png_sello)
        it = QGraphicsPixmapItem(QPixmap.fromImage(img))
        it.setScale(1 / ESCALA_SELLO)
        it.setOpacity(0.7)
        it.setZValue(200)
        it.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        it.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        c.scene().addItem(it)
        est.fantasma = it
    w, h = sellos.tamano_pt(est.png_sello, ESCALA_SELLO)
    sp = c.mapToScene(pos.toPoint())
    try:
        est.fantasma.setPos(sp.x() - w / 2, sp.y() - h / 2)
        est.fantasma.setVisible(c.page_at(sp) is not None)
    except RuntimeError:
        est.fantasma = None


def _tecla(visor, est: _Estado, e) -> bool:
    c = visor.canvas
    mods = e.modifiers()
    km = Qt.KeyboardModifier
    if mods & (km.ControlModifier | km.AltModifier | km.MetaModifier):
        return False
    k = e.key()
    if k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and est.sel and c.tool == "select":
        cm = est.por_id.get(est.sel)
        if cm is not None:
            borrar(visor, cm)
            return True
        return False
    if k == Qt.Key.Key_Escape:
        if est.arrastre is not None:
            _quitar_items(est.arrastre.get("item"))
            est.arrastre = None
            _pintar_marco(visor)
        if est.sel:
            seleccionar(visor, None, mostrar_panel=False)
        return False
    letra = e.text().upper()
    if letra == "S" and not e.isAutoRepeat():
        repetir_sello(visor)
        return True
    if letra in TECLAS and not e.isAutoRepeat():
        activar(visor, TECLAS[letra])
        return True
    return False


# ======================================================================== panel «Comentarios»

ROL = Qt.ItemDataRole.UserRole + 31


class _Modelo(QAbstractListModel):
    def __init__(self):
        super().__init__()
        self.filas: list[Comentario] = []

    def rowCount(self, parent=QModelIndex()):  # noqa: N802 — método de Qt
        return 0 if parent.isValid() else len(self.filas)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.filas):
            return None
        cm = self.filas[index.row()]
        if role == ROL:
            return cm
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.AccessibleTextRole):
            return f"{cm.nombre_tipo} de {cm.autor or 'sin autor'}, página {cm.pagina + 1}: {cm.texto}"
        return None

    def poner(self, filas: list[Comentario]) -> None:
        self.beginResetModel()
        self.filas = filas
        self.endResetModel()

    def fila_de(self, cid: str | None) -> int:
        if cid is None:
            return -1
        return next((k for k, cm in enumerate(self.filas) if cm.id == cid), -1)


def _partir(texto: str, fm: QFontMetrics, ancho: int, max_lineas: int) -> list[str]:
    lineas: list[str] = []
    cortado = False
    for parrafo in (texto or "").replace("\r", "").split("\n"):
        actual = ""
        for w in parrafo.split(" "):
            prueba = f"{actual} {w}" if actual else w
            if fm.horizontalAdvance(prueba) <= ancho:
                actual = prueba
                continue
            if actual:
                lineas.append(actual)
            while fm.horizontalAdvance(w) > ancho and len(w) > 1:
                k = max(1, int(len(w) * ancho / max(1, fm.horizontalAdvance(w))))
                while k > 1 and fm.horizontalAdvance(w[:k]) > ancho:
                    k -= 1
                lineas.append(w[:k])
                w = w[k:]
            actual = w
        lineas.append(actual)
        if len(lineas) > max_lineas:
            break
    if len(lineas) > max_lineas:
        lineas = lineas[:max_lineas]
        cortado = True
    if cortado and lineas:
        lineas[-1] = fm.elidedText(lineas[-1] + " …", Qt.TextElideMode.ElideRight, ancho)
    return lineas


class _Tarjeta(QStyledItemDelegate):
    """Tarjeta de un comentario (como ``rp-file``: ``surface``, línea fina, radio 12): ícono del tipo, autor,
    fecha, página, texto marcado, texto, estado y respuestas.  La elegida lleva la banda y el borde de «ver»."""
    M, P, BADGE = 4, 12, 24

    def __init__(self, panel: "PanelComentarios"):
        super().__init__(panel)
        self.panel = panel
        self._cache: dict = {}
        self.f_normal = tema.fuente("body-sm")
        self.f_negrita = tema.fuente("label")
        self.f_chica = tema.fuente("caption")
        self.f_cursiva = QFont(self.f_normal)
        self.f_cursiva.setItalic(True)
        self.f_chica_negrita = tema.fuente("chip")
        self._iconos: dict = {}

    def limpiar(self):
        self._cache.clear()

    def _clave(self, cm: Comentario, w: int, sel: bool):
        extra = self.panel.altura_acciones() if sel else 0
        return (id(cm), w, extra, len(cm.texto_marcado or ""), cm.texto_marcado is None)

    def _disposicion(self, cm: Comentario, w: int, sel: bool):
        clave = self._clave(cm, w, sel)
        d = self._cache.get(clave)
        if d is not None:
            return d
        M, P, B = self.M, self.P, self.BADGE
        fm_n, fm_b, fm_c, fm_i = (QFontMetrics(f) for f in (self.f_normal, self.f_negrita, self.f_chica,
                                                            self.f_cursiva))
        fm_cb = QFontMetrics(self.f_chica_negrita)
        ancho = w - 2 * M - 2 * P
        items = []
        y = M + P
        x_txt = M + P + B + 8
        ancho_txt = w - M - P - x_txt
        fecha = comentarios.fecha_corta(cm.creado or cm.modificado)
        fw = fm_c.horizontalAdvance(fecha)
        items.append(("badge", QRectF(M + P, y, B, B)))
        nombre = fm_b.elidedText(cm.autor or "Sin autor", Qt.TextElideMode.ElideRight, max(20, ancho_txt - fw - 6))
        items.append(("texto", QRectF(x_txt, y, ancho_txt - fw - 6, fm_b.height()), nombre, self.f_negrita,
                      "ink", 0))
        items.append(("texto", QRectF(w - M - P - fw, y + 1, fw, fm_c.height()), fecha, self.f_chica, "ink-3", 0))
        y += fm_b.height()
        meta = f"Pág. {cm.pagina + 1} · {cm.nombre_tipo}"
        pill_w = 0
        if cm.estado:
            et = ESTADOS[cm.estado]
            pill_w = fm_cb.horizontalAdvance(et) + 14
            items.append(("pill", QRectF(w - M - P - pill_w, y + 1, pill_w, fm_cb.height() + 2), et, cm.estado))
        items.append(("texto", QRectF(x_txt, y + 1, ancho_txt - pill_w - 4, fm_c.height()),
                      fm_c.elidedText(meta, Qt.TextElideMode.ElideRight, max(20, ancho_txt - pill_w - 4)),
                      self.f_chica, "ink-3", 0))
        y = max(y + fm_c.height() + 2, M + P + B) + 6
        if cm.tipo in MARCAS and cm.texto_marcado:
            for ln in _partir(f"«{cm.texto_marcado}»", fm_i, ancho, 2):
                items.append(("texto", QRectF(M + P, y, ancho, fm_i.height()), ln, self.f_cursiva, "ink-2", 0))
                y += fm_i.height()
            y += 3
        texto = (cm.texto or "").strip()
        if texto and texto != (cm.texto_marcado or ""):
            for ln in _partir(texto, fm_n, ancho, 8):
                items.append(("texto", QRectF(M + P, y, ancho, fm_n.height()), ln, self.f_normal, "ink", 0))
                y += fm_n.height()
            y += 3
        elif cm.tipo == "nota" and not texto:
            items.append(("texto", QRectF(M + P, y, ancho, fm_i.height()), "(sin texto)", self.f_cursiva, "ink-3", 0))
            y += fm_i.height() + 3
        resp = [r for r in cm.respuestas if not r.es_estado]
        for r in resp[:4]:
            y += 3
            y_ini = y
            cab = f"{r.autor or 'Sin autor'} · {comentarios.fecha_corta(r.fecha)}".rstrip(" ·")
            items.append(("texto", QRectF(M + P + 12, y, ancho - 12, fm_cb.height()),
                          fm_cb.elidedText(cab, Qt.TextElideMode.ElideRight, ancho - 12), self.f_chica_negrita,
                          "ink-2", 0))
            y += fm_cb.height()
            for ln in _partir(r.texto, fm_n, ancho - 12, 3):
                items.append(("texto", QRectF(M + P + 12, y, ancho - 12, fm_n.height()), ln, self.f_normal,
                              "ink", 0))
                y += fm_n.height()
            items.append(("barra", QRectF(M + P + 3, y_ini + 1, 2, y - y_ini - 2)))
        if len(resp) > 4:
            y += 2
            mas = f"y {len(resp) - 4} respuesta{'s' if len(resp) - 4 != 1 else ''} más"
            items.append(("texto", QRectF(M + P + 12, y, ancho - 12, fm_c.height()), mas, self.f_chica, "ink-3", 0))
            y += fm_c.height()
        y += P + M
        if sel:
            y += self.panel.altura_acciones()
        d = (int(math.ceil(y)), items)
        if len(self._cache) > 6000:
            self._cache.clear()
        self._cache[clave] = d
        return d

    def _ancho(self, option) -> int:
        w = self.panel.lista.viewport().width()
        return max(160, w if w > 0 else option.rect.width())

    def sizeHint(self, option, index):  # noqa: N802
        cm = index.data(ROL)
        if cm is None:
            return QSize(0, 0)
        w = self._ancho(option)
        sel = cm.id == self.panel.sel
        return QSize(w, self._disposicion(cm, w, sel)[0])

    def _icono(self, tipo: str, claro: bool) -> QPixmap:
        k = (tipo, claro)
        if k not in self._iconos:
            self._iconos[k] = art.glyph(ICONO_TIPO.get(tipo, "comentar"), 14,
                                        tema.color("ink") if claro else tema.color("on-cat"))
        return self._iconos[k]

    def paint(self, p: QPainter, option, index):
        cm = index.data(ROL)
        if cm is None:
            return
        w = option.rect.width()
        sel = cm.id == self.panel.sel
        alto, items = self._disposicion(cm, w, sel)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.translate(option.rect.topLeft())
        caja = QRectF(self.M, self.M / 2, w - 2 * self.M, alto - self.M)
        t, ton = tema.tokens(), tema.tonos("ver")
        if sel:
            p.setPen(QPen(QColor(ton["c"]), 1.5))
            p.setBrush(QColor(ton["band"]))
        else:
            hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
            p.setPen(QPen(QColor(t["line-strong"] if hover else t["line"]), 1))
            p.setBrush(QColor(t["surface"]))
        p.drawRoundedRect(caja.adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)
        for it in items:
            k = it[0]
            if k == "badge":
                col = QColor(cm.color or comentarios.COLORES.get(cm.tipo) or t["ink-3"])
                claro = col.lightnessF() > 0.72
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(col)
                p.drawEllipse(it[1])
                pm = self._icono(cm.tipo, claro)
                r = it[1]
                p.drawPixmap(QPointF(r.center().x() - 7, r.center().y() - 7), pm)
            elif k == "texto":
                _, r, texto, f, col, _fl = it
                p.setFont(f)
                p.setPen(QColor(t[col] if col in t else col))
                p.drawText(r, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), texto)
            elif k == "pill":
                _, r, texto, estado = it
                fondo, tinta = color_estado(estado)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(fondo))
                p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
                p.setFont(self.f_chica_negrita)
                p.setPen(QColor(tinta))
                p.drawText(r, int(Qt.AlignmentFlag.AlignCenter), texto)
            elif k == "barra":
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(t["line-strong"]))
                p.drawRoundedRect(it[1], 1, 1)
        p.restore()


class _CajaRespuesta(QPlainTextEdit):
    enviar = Signal()
    cancelar = Signal()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.enviar.emit()
            return
        if e.key() == Qt.Key.Key_Escape:
            self.cancelar.emit()
            return
        super().keyPressEvent(e)


class _Acciones(QWidget):
    """Botones de la tarjeta seleccionada: «Responder», «Estado ▾» y «⋯» (y la caja de respuesta)."""
    ALTO_FILA, ALTO_CAJA = 40, 64

    def __init__(self, panel: "PanelComentarios", cm: Comentario):
        super().__init__()
        self.panel, self.cm = panel, cm
        self.setAutoFillBackground(False)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 0, 12, 12)
        lay.setSpacing(6)
        lay.addStretch(1)
        self.caja = _CajaRespuesta()
        self.caja.setPlaceholderText("Escribe tu respuesta… (Enter envía, Mayús+Enter salta de línea)")
        self.caja.setFixedHeight(self.ALTO_CAJA - 6)
        self.caja.setObjectName("CajaRespuesta")
        self.caja.enviar.connect(self._enviar)
        self.caja.cancelar.connect(lambda: panel.abrir_respuesta(False))
        self.caja.setVisible(panel.respuesta_abierta)
        lay.addWidget(self.caja)
        fila = QHBoxLayout()
        fila.setSpacing(6)
        self.b_resp = QPushButton("Enviar" if panel.respuesta_abierta else "Responder")
        self.b_resp.setObjectName("FiltroComentarios")     # los tres botones de la tarjeta, del mismo tamaño
        self.b_resp.setProperty("tam", "sm")
        self.b_resp.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_resp.clicked.connect(self._boton_responder)
        fila.addWidget(self.b_resp)
        self.b_estado = QToolButton()
        self.b_estado.setText("Estado ▾")
        self.b_estado.setObjectName("FiltroComentarios")
        self.b_estado.setProperty("tam", "sm")
        self.b_estado.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_estado.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        m = QMenu(self.b_estado)
        for k in ESTADOS_LISTA:
            a = m.addAction(ESTADOS[k], lambda k_=k: panel.estado(k_))
            a.setCheckable(True)
            a.setChecked(cm.estado == k)
        self.b_estado.setMenu(m)
        fila.addWidget(self.b_estado)
        self.b_mas = QToolButton()
        self.b_mas.setText("⋯")
        self.b_mas.setToolTip("Más: editar, copiar el texto o eliminar")
        self.b_mas.setAccessibleName("Más acciones del comentario")
        self.b_mas.setObjectName("FiltroComentarios")
        self.b_mas.setProperty("tam", "sm")
        self.b_mas.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_mas.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        mm = QMenu(self.b_mas)
        mm.addAction(_gl("edittext"), "Editar", panel.editar)
        mm.addAction(_gl("duplicate"), "Copiar texto", panel.copiar)
        mm.addSeparator()
        mm.addAction(_gl("delete"), "Eliminar", panel.eliminar)
        self.b_mas.setMenu(mm)
        fila.addWidget(self.b_mas)
        fila.addStretch(1)
        lay.addLayout(fila)

    def altura(self) -> int:
        return self.ALTO_FILA + (self.ALTO_CAJA if self.panel.respuesta_abierta else 0)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        a = self.altura()
        self.setMask(QRegion(0, max(0, self.height() - a), self.width(), a))

    def _boton_responder(self):
        if self.panel.respuesta_abierta:
            self._enviar()
        else:
            self.panel.abrir_respuesta(True)

    def _enviar(self):
        self.panel.enviar_respuesta(self.caja.toPlainText())


class PanelComentarios(QWidget):
    """Pestaña «Comentarios (n)» del panel derecho del visor."""

    def __init__(self, visor):
        super().__init__()
        self.visor = visor
        self.filtro = Filtro()
        self.orden = "pagina"
        self.sel: str | None = None
        self.respuesta_abierta = False
        self._acciones: _Acciones | None = None
        self.setStyleSheet(_qss_comentarios())
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)
        self.buscar = QLineEdit()
        self.buscar.setPlaceholderText("Buscar en los comentarios")
        self.buscar.setClearButtonEnabled(True)
        self.buscar.addAction(_gl("search", "ink-3"), QLineEdit.ActionPosition.LeadingPosition)
        self._t_buscar = QTimer(self)
        self._t_buscar.setSingleShot(True)
        self._t_buscar.setInterval(150)
        self._t_buscar.timeout.connect(self._aplicar_busqueda)
        self.buscar.textChanged.connect(lambda _t: self._t_buscar.start())
        lay.addWidget(self.buscar)
        fila = QHBoxLayout()
        fila.setSpacing(6)
        self.b_filtros = QToolButton()
        self.b_filtros.setText("Filtros ▾")
        self.b_filtros.setToolTip("Filtrar por tipo, autor o estado")
        self.b_filtros.setObjectName("FiltroComentarios")
        self.b_filtros.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_filtros.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.menu_filtros = QMenu(self.b_filtros)
        self.menu_filtros.aboutToShow.connect(self._llenar_filtros)
        self.b_filtros.setMenu(self.menu_filtros)
        fila.addWidget(self.b_filtros)
        self.b_orden = QToolButton()
        self.b_orden.setObjectName("FiltroComentarios")
        self.b_orden.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_orden.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        mo = QMenu(self.b_orden)
        for k, t in comentarios.ORDENES.items():
            a = mo.addAction(f"Por {t.lower()}", lambda k_=k: self.ordenar(k_))
            a.setCheckable(True)
        mo.aboutToShow.connect(lambda: [a.setChecked(list(comentarios.ORDENES)[i] == self.orden)
                                        for i, a in enumerate(mo.actions())])
        self.b_orden.setMenu(mo)
        fila.addWidget(self.b_orden)
        fila.addStretch(1)
        self.cuenta = label("", "Caption")
        fila.addWidget(self.cuenta)
        lay.addLayout(fila)

        self.pila = QStackedWidget()
        self.modelo = _Modelo()
        self.lista = QListView()
        self.lista.setObjectName("ListaComentarios")
        self.lista.setModel(self.modelo)
        self.tarjeta = _Tarjeta(self)
        self.lista.setItemDelegate(self.tarjeta)
        self.lista.setUniformItemSizes(False)
        self.lista.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.lista.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.lista.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.lista.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.lista.setLayoutMode(QListView.LayoutMode.Batched)
        self.lista.setBatchSize(150)
        self.lista.setResizeMode(QListView.ResizeMode.Adjust)
        self.lista.setMouseTracking(True)
        self.lista.selectionModel().currentChanged.connect(self._actual_cambio)
        self.pila.addWidget(self.lista)
        self.vacio = label(VACIO_MSG, "BodySm", wrap=True)
        self.vacio.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.vacio.setContentsMargins(12, 8, 12, 8)
        caja_vacio = QWidget()
        cv = QVBoxLayout(caja_vacio)
        cv.setContentsMargins(0, 24, 0, 0)
        cv.setSpacing(8)
        icono_vacio = QLabel()
        icono_vacio.setPixmap(art.glyph_tile("comentar", 40, "ver", soft=True, dpr=2.0))
        icono_vacio.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        cv.addWidget(icono_vacio)
        cv.addWidget(self.vacio)
        self.b_quitar = QPushButton("Quitar los filtros")
        self.b_quitar.setObjectName("Secondary")
        self.b_quitar.setProperty("tam", "sm")
        self.b_quitar.clicked.connect(self.quitar_filtros)
        cv.addWidget(self.b_quitar, 0, Qt.AlignmentFlag.AlignHCenter)
        cv.addStretch(1)
        self.pila.addWidget(caja_vacio)
        lay.addWidget(self.pila, 1)

        pie = QHBoxLayout()
        pie.setSpacing(6)
        self.b_resumen = QPushButton("Resumen…")
        self.b_resumen.setObjectName("Secondary")
        self.b_resumen.setProperty("tam", "sm")
        self.b_resumen.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_resumen.setToolTip("Exportar el resumen de comentarios (PDF con miniaturas, Excel, Word o texto)")
        self.b_resumen.clicked.connect(lambda: resumen_desde_visor(self.visor))
        pie.addWidget(self.b_resumen)
        self.b_autor = QToolButton()
        self.b_autor.setObjectName("AutorComentarios")
        self.b_autor.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_autor.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.b_autor.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        ma = QMenu(self.b_autor)
        ma.addAction("Cambiar el nombre con que firmo…", self.cambiar_autor)
        self.b_autor.setMenu(ma)
        pie.addWidget(self.b_autor, 1)
        lay.addLayout(pie)
        self._actualizar_botones()
        self.actualizar_autor()

    # ---------------------------------------------------------------- datos
    def _clave_busqueda(self, cm: Comentario) -> str:
        est = _est(self.visor)
        k = id(cm)
        t = est.busqueda.get(k)
        if t is None:
            t = est.busqueda[k] = comentarios.texto_busqueda(cm)
        return t

    def recargar(self) -> None:
        est = _est(self.visor)
        total = len(est.comentarios)
        if self.filtro.solo_de is not None:
            self.filtro.solo_de = autor() or self.filtro.solo_de
        filas = [cm for cm in est.comentarios if self.filtro.acepta(cm, self._clave_busqueda(cm))]
        filas = comentarios.ordenar(filas, self.orden)
        barra = self.lista.verticalScrollBar()
        antes = barra.value()
        self.tarjeta.limpiar()
        self._soltar_acciones()
        self.modelo.poner(filas)
        self.sel = est.sel if est.sel in {cm.id for cm in filas} else None
        if self.sel is None:
            self.respuesta_abierta = False
            if antes:                   # la lista no salta al principio después de un cambio
                # con la barra como contexto: si el panel se destruye antes, Qt cancela la llamada
                QTimer.singleShot(0, barra, lambda: barra.setValue(min(antes, barra.maximum())))
        self._poner_acciones(desplazar=False)
        self.visor.titulo_panel(CLAVE, f"Comentarios ({total})")
        if self.filtro.activo():
            self.cuenta.setText(f"{len(filas)} de {total}")
        else:
            self.cuenta.setText("")
        sin = not filas
        self.pila.setCurrentIndex(1 if sin else 0)
        self.vacio.setText(VACIO_MSG if not total else "Ningún comentario coincide con la búsqueda o los filtros.")
        self.b_quitar.setVisible(bool(total) and sin)
        self._actualizar_botones()
        self.actualizar_autor()

    def marcado_listo(self, final: bool) -> None:
        """Ya se calculó el texto marcado de más subrayados: se vuelven a medir las tarjetas."""
        if self.filtro.texto.strip():
            if final:
                self.recargar()
            return
        if final:
            self.tarjeta.limpiar()
            self.lista.doItemsLayout()
            self._poner_acciones(desplazar=False)
        else:
            self.lista.viewport().update()

    def _actualizar_botones(self) -> None:
        n = self.filtro.cuantos()
        self.b_filtros.setText(f"Filtros ({n}) ▾" if n else "Filtros ▾")
        self.b_orden.setText(f"Orden: {comentarios.ORDENES[self.orden].lower()} ▾")

    def actualizar_autor(self) -> None:
        nombre = autor()
        self.b_autor.setText(f"Autor: {nombre} ▾" if nombre else "Autor: sin nombre ▾")
        self.b_autor.setToolTip(f"Tus comentarios y sellos se firman como «{nombre}»." if nombre
                                else "La primera vez que comentes se te preguntará tu nombre.")

    def cambiar_autor(self) -> None:
        if preguntar_autor(self, cambiar=True):
            self.actualizar_autor()
            if self.filtro.solo_de:
                self.filtro.solo_de = autor()
                self.recargar()

    # ---------------------------------------------------------------- filtros
    def _aplicar_busqueda(self) -> None:
        self.filtro.texto = self.buscar.text()
        self.recargar()

    def _llenar_filtros(self) -> None:
        m = self.menu_filtros
        m.clear()
        est = _est(self.visor)
        st = m.addMenu("Tipo")
        tipos = sorted({cm.tipo for cm in est.comentarios}, key=list(TIPOS).index)
        for t in tipos or list(TIPOS)[:7]:
            a = st.addAction(TIPOS[t], lambda t_=t: self._alternar("tipos", t_, tipos))
            a.setCheckable(True)
            a.setChecked(self.filtro.tipos is None or t in self.filtro.tipos)
        sa = m.addMenu("Autor")
        autores = sorted({cm.autor or "" for cm in est.comentarios}, key=lambda s: s.lower())
        for au in autores:
            a = sa.addAction(au or "Sin autor", lambda a_=au: self._alternar("autores", a_, autores))
            a.setCheckable(True)
            a.setChecked(self.filtro.autores is None or au in self.filtro.autores)
        se = m.addMenu("Estado")
        for k in ESTADOS_LISTA:
            a = se.addAction(ESTADOS[k], lambda k_=k: self._alternar("estados", k_, ESTADOS_LISTA))
            a.setCheckable(True)
            a.setChecked(self.filtro.estados is None or k in self.filtro.estados)
        m.addSeparator()
        a = m.addAction("Solo los míos", lambda: self.solo_mios(not bool(self.filtro.solo_de)))
        a.setCheckable(True)
        a.setChecked(bool(self.filtro.solo_de))
        a = m.addAction("Sin responder", lambda: self.sin_responder(not self.filtro.sin_responder))
        a.setCheckable(True)
        a.setChecked(self.filtro.sin_responder)
        m.addSeparator()
        q = m.addAction("Quitar los filtros", self.quitar_filtros)
        q.setEnabled(self.filtro.activo())

    def _alternar(self, campo: str, valor, todos) -> None:
        actual = getattr(self.filtro, campo)
        conjunto = set(todos) if actual is None else set(actual)
        conjunto ^= {valor}
        setattr(self.filtro, campo, None if conjunto >= set(todos) else conjunto)
        self.recargar()

    def filtrar(self, tipos=None, autores=None, estados=None) -> None:
        """Para el código y las pruebas: None = todos."""
        self.filtro.tipos = None if tipos is None else set(tipos)
        self.filtro.autores = None if autores is None else set(autores)
        self.filtro.estados = None if estados is None else set(estados)
        self.recargar()

    def solo_mios(self, on: bool) -> None:
        self.filtro.solo_de = (autor() or None) if on else None
        if on and not self.filtro.solo_de:
            self.visor.canvas.notice.emit("Todavía no has firmado ningún comentario con tu nombre.")
        self.recargar()

    def sin_responder(self, on: bool) -> None:
        self.filtro.sin_responder = bool(on)
        self.recargar()

    def quitar_filtros(self) -> None:
        self.filtro = Filtro()
        self.buscar.blockSignals(True)
        self.buscar.clear()
        self.buscar.blockSignals(False)
        self.recargar()

    def ordenar(self, orden: str) -> None:
        self.orden = orden if orden in comentarios.ORDENES else "pagina"
        self.recargar()

    # ---------------------------------------------------------------- selección
    def seleccionar(self, cid: str | None) -> None:
        """Selección que viene del lienzo."""
        if cid != self.sel:
            self.respuesta_abierta = False
        self.sel = cid
        fila = self.modelo.fila_de(cid)
        sm = self.lista.selectionModel()
        sm.blockSignals(True)
        if fila >= 0:
            idx = self.modelo.index(fila)
            self.lista.setCurrentIndex(idx)
        else:
            sm.clearSelection()
            self.lista.setCurrentIndex(QModelIndex())
        sm.blockSignals(False)
        self._poner_acciones(desplazar=True)

    def _actual_cambio(self, actual: QModelIndex, _anterior) -> None:
        cm = actual.data(ROL) if actual.isValid() else None
        if cm is None:
            return
        if cm.id != self.sel:
            self.respuesta_abierta = False
        self.sel = cm.id
        self._poner_acciones(desplazar=True)
        ir_a(self.visor, cm)

    def elegir(self, cid: str) -> None:
        """Como hacer clic en la tarjeta (para el código y las pruebas)."""
        fila = self.modelo.fila_de(cid)
        if fila >= 0:
            self.lista.setCurrentIndex(self.modelo.index(fila))

    def altura_acciones(self) -> int:
        return _Acciones.ALTO_FILA + (_Acciones.ALTO_CAJA if self.respuesta_abierta else 0)

    def _soltar_acciones(self) -> None:
        """Quita los botones de la tarjeta que estaba seleccionada (se ocultan ya; Qt los borra después)."""
        viejo, self._acciones = self._acciones, None
        if viejo is None:
            return
        try:
            viejo.hide()
            fila = self.modelo.fila_de(viejo.cm.id)
            if fila >= 0 and self.lista.indexWidget(self.modelo.index(fila)) is viejo:
                self.lista.setIndexWidget(self.modelo.index(fila), None)
            else:
                viejo.deleteLater()
        except RuntimeError:          # ya lo había borrado la lista
            pass

    def _poner_acciones(self, desplazar: bool) -> None:
        self._soltar_acciones()
        self.tarjeta.limpiar()
        self.lista.doItemsLayout()
        fila = self.modelo.fila_de(self.sel)
        if fila < 0:
            return
        idx = self.modelo.index(fila)
        cm = self.modelo.filas[fila]
        self._acciones = _Acciones(self, cm)
        self.lista.setIndexWidget(idx, self._acciones)
        if desplazar:
            self.lista.scrollTo(idx, QAbstractItemView.ScrollHint.EnsureVisible)
        if self.respuesta_abierta:
            self._acciones.caja.setFocus()

    def actual(self) -> Comentario | None:
        return _est(self.visor).por_id.get(self.sel) if self.sel else None

    def abrir_respuesta(self, on: bool = True) -> None:
        if self.sel is None:
            return
        self.respuesta_abierta = bool(on)
        self._poner_acciones(desplazar=True)
        if not on:
            self.visor.canvas.setFocus()

    def enviar_respuesta(self, texto: str) -> None:
        cm = self.actual()
        if cm is None or not texto.strip():
            return
        cid = cm.id
        self.respuesta_abierta = False
        if responder(self.visor, cm, texto) is None:
            self.respuesta_abierta = True
        self.sel = cid
        self._poner_acciones(desplazar=True)

    def estado(self, estado: str | None) -> None:
        cm = self.actual()
        if cm is not None:
            cambiar_estado(self.visor, cm, estado)

    def editar(self) -> None:
        cm = self.actual()
        if cm is not None:
            ir_a(self.visor, cm)
            abrir_edicion(self.visor, cm)

    def copiar(self) -> None:
        cm = self.actual()
        if cm is not None:
            t = copiar_texto(cm)
            self.visor.canvas.notice.emit(f"Texto copiado ({len(t)} caracteres)")

    def eliminar(self) -> None:
        cm = self.actual()
        if cm is not None:
            borrar(self.visor, cm)


# ======================================================================== resumen y aplanar

class DialogoResumen(QDialog):
    def __init__(self, parent=None, hay_filtro: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Resumen de comentarios")
        self.formato = "pdf"
        self.solo_filtrados = False
        self.setProperty("cat", "ver")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 18)
        lay.setSpacing(6)
        lay.addWidget(label("Resumen de comentarios", "Title2"))
        lay.addWidget(label("Todos los comentarios del documento, con su autor, fecha, estado y respuestas.",
                            "BodySm", wrap=True))
        lay.addSpacing(10)
        lay.addWidget(label("¿En qué formato?", "Label"))
        self.formatos = widgets.OptionGroup()
        for k, titulo, ayuda in (("pdf", "PDF", "Con la miniatura de cada página y marcadores numerados."),
                                 ("xlsx", "Excel", "Una fila por comentario, para filtrar y ordenar."),
                                 ("docx", "Word", "Para editarlo o enviarlo."),
                                 ("txt", "Texto plano", "Solo el texto, para pegarlo donde quieras.")):
            self.formatos.add(titulo, ayuda, k)
        self.opciones = {k: self.formatos.card(k) for k in ("pdf", "xlsx", "docx", "txt")}
        lay.addWidget(self.formatos)
        lay.addSpacing(6)
        self.chk = widgets.SwitchRow("Solo los que se ven en el panel", "Con la búsqueda y los filtros de ahora.",
                                     checked=hay_filtro)
        self.chk.setVisible(hay_filtro)              # sin filtro no aplica: se esconde
        lay.addWidget(self.chk)
        lay.addSpacing(10)
        fila = QHBoxLayout()
        fila.setSpacing(8)
        fila.addStretch(1)
        bc = QPushButton("Cancelar")
        bc.setObjectName("Secondary")
        bc.clicked.connect(self.reject)
        bo = QPushButton("Guardar resumen…")
        bo.setObjectName("Primary")
        bo.setDefault(True)
        bo.clicked.connect(self._ok)
        fila.addWidget(bc)
        fila.addWidget(bo)
        lay.addLayout(fila)

    def _ok(self):
        self.formato = self.formatos.value() or "pdf"
        self.solo_filtrados = not self.chk.isHidden() and self.chk.isChecked()
        self.accept()


FILTROS_ARCHIVO = {"pdf": "Documentos PDF (*.pdf)", "xlsx": "Libro de Excel (*.xlsx)",
                   "docx": "Documento de Word (*.docx)", "txt": "Texto (*.txt)"}


def pedir_salida(parent, sugerido: str, formato: str) -> str | None:
    """Ruta de destino.  Para .xlsx y .docx no se usa ask_save (que le pondría «.pdf»)."""
    flt = FILTROS_ARCHIVO[formato]
    if formato in ("pdf", "txt"):
        return widgets.ask_save(parent, sugerido, flt, "Guardar el resumen")
    ruta, _ = QFileDialog.getSaveFileName(parent, "Guardar el resumen", sugerido, flt)
    if not ruta:
        return None
    if not ruta.lower().endswith("." + formato):
        ruta += "." + formato
    ctx.remember_dir(ruta)
    return ruta


def resumen_desde_visor(visor, formato: str | None = None, solo_filtrados: bool | None = None,
                        salida: str | None = None) -> None:
    c = visor.canvas
    if c.doc is None or ctx.busy:
        return
    _cerrar_editor(visor)
    est = _est(visor)
    panel = _panel(visor)
    if not est.comentarios:
        QMessageBox.information(visor, "Resumen de comentarios", "Este PDF todavía no tiene comentarios.")
        return
    if formato is None:
        d = DialogoResumen(visor, hay_filtro=bool(panel and panel.filtro.activo()))
        if d.exec() != QDialog.DialogCode.Accepted:
            return
        formato, solo_filtrados = d.formato, d.solo_filtrados
    if salida is None:
        salida = pedir_salida(visor, widgets.suggest_output(c.path, "_comentarios", "." + formato), formato)
    if not salida:
        return
    filtro = copy.deepcopy(panel.filtro) if (solo_filtrados and panel is not None) else None

    def listo(n):
        widgets.show_done(visor, "Resumen listo",
                          f"Se resumieron {n} comentario{'s' if n != 1 else ''} en «{Path(salida).name}».",
                          file=salida)

    ctx.run_task("Preparando el resumen de comentarios…", comentarios.exportar_resumen, listo, path=c.path,
                 salida=salida, formato=formato, filtro=filtro, password=c.password, ops=copy.deepcopy(est.ops))


def aplanar_desde_visor(visor, salida: str | None = None) -> None:
    c = visor.canvas
    if c.doc is None or ctx.busy:
        return
    _cerrar_editor(visor)
    est = _est(visor)
    if not est.comentarios:
        QMessageBox.information(visor, "Aplanar comentarios", "Este PDF no tiene comentarios para aplanar.")
        return
    edits, _ = c.collect_edits()
    if edits:
        QMessageBox.information(visor, "Aplanar comentarios",
                                "La copia aplanada lleva los comentarios. Los demás cambios del visor (textos, "
                                "dibujos, imágenes) entran solo si los guardas antes.")
    if salida is None:
        salida = widgets.ask_save(visor, widgets.suggest_output(c.path, "_aplanado"), title="Guardar la copia aplanada")
    if not salida:
        return
    if os.path.normcase(os.path.abspath(salida)) == os.path.normcase(os.path.abspath(c.path)):
        QMessageBox.warning(visor, "Aplanar comentarios",
                            "Aplanar crea siempre una copia: elige otro nombre para no reemplazar el original.")
        return
    password = c.password

    def listo(n):
        if password:
            ctx.passwords.set(salida, password)
        widgets.show_done(visor, "Copia aplanada",
                          f"{n} comentario{'s' if n != 1 else ''} quedaron fijos en «{Path(salida).name}». "
                          "El original no cambió.", file=salida)

    ctx.run_task("Aplanando los comentarios…", comentarios.aplanar, listo, path=c.path, output=salida,
                 password=password, ops=copy.deepcopy(est.ops))


# ======================================================================== barra del visor: menús

class _FilaSello(QWidget):
    """Una fila del menú «Sello»: miniatura y nombre."""

    def __init__(self, accion, png: bytes, nombre: str, menu: QMenu):
        super().__init__()
        self.accion, self.menu = accion, menu
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 4, 14, 4)
        lay.setSpacing(10)
        img = QLabel()
        pm = QPixmap.fromImage(QImage.fromData(png))
        dpr = 2.0
        pm = pm.scaled(int(84 * dpr), int(34 * dpr), Qt.AspectRatioMode.KeepAspectRatio,
                       Qt.TransformationMode.SmoothTransformation)
        pm.setDevicePixelRatio(dpr)
        img.setPixmap(pm)
        img.setFixedSize(86, 36)
        img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(img)
        lay.addWidget(QLabel(nombre), 1)
        self._hover = False

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, e):
        if self._hover:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(tema.qcolor("surface-sunken"))     # como la opción elegida de un QMenu
            p.drawRoundedRect(QRectF(self.rect()).adjusted(2, 1, -2, -1), 8, 8)
            p.end()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.menu.hide()
            self.accion.trigger()


_MINIS: dict = {}


def _mini(sello: Sello, escala: float = 1.6) -> bytes:
    """Miniatura (en caché) de un diseño para los menús y la pantalla «Mis sellos»."""
    k = (repr(sellos.a_dict(sello)), _dt.date.today().isoformat(), autor(), cargo(), escala)
    if k not in _MINIS:
        if len(_MINIS) > 120:
            _MINIS.clear()
        _MINIS[k] = sellos.dibujar(sello, datos_sello(), escala)
    return _MINIS[k]


def _titulo_menu(menu: QMenu, texto: str) -> None:
    a = QWidgetAction(menu)
    lb = QLabel(texto)
    lb.setStyleSheet(f"color: {tema.color('ink-3')}; font-size: 12px; font-weight: 600; padding: 8px 12px 4px 12px;")
    a.setDefaultWidget(lb)
    a.setEnabled(False)
    menu.addAction(a)


def llenar_menu_sellos(visor, menu: QMenu) -> None:
    menu.clear()
    try:
        mios = sellos.AlmacenSellos().listar()
    except Exception:  # noqa: BLE001 — una carpeta inaccesible no debe romper el menú
        mios = []

    def fila(s: Sello):
        a = QWidgetAction(menu)
        a.setText(s.nombre)
        a.triggered.connect(lambda _=False, s_=s: armar_sello(visor, s_))
        a.setDefaultWidget(_FilaSello(a, _mini(s), s.nombre, menu))
        menu.addAction(a)

    if mios:
        _titulo_menu(menu, "Mis sellos")
        for s in mios:
            fila(s)
    _titulo_menu(menu, "Para empezar")
    for s in sellos.PARA_EMPEZAR:
        fila(s)
    menu.addSeparator()
    menu.addAction(_gl("add"), "Diseñar un sello nuevo…", lambda: disenar_desde_visor(visor))
    menu.addAction(_gl("sello_visor"), "Administrar mis sellos…", lambda: extensiones.ir_a("stamps"))
    u = _ultimo_sello()
    if u is not None:
        menu.addAction(f"Repetir «{u.nombre}»\tS", lambda: repetir_sello(visor))


def disenar_desde_visor(visor) -> None:
    d = DialogoSello(None, visor)
    if d.exec() == QDialog.DialogCode.Accepted and d.resultado is not None:
        armar_sello(visor, d.resultado)


def _menu_comentar(visor, boton: QToolButton) -> QMenu:
    m = QMenu(boton)
    for herr, tecla in (("ext:nota", "N"), ("ext:subrayar", "U"), ("ext:tachar", "K"), ("ext:ondulado", ""),
                        ("ext:cuadro", "X")):
        texto = HERRAMIENTAS[herr] + (f"\t{tecla}" if tecla else "")
        m.addAction(_gl(ICONO_HERRAMIENTA[herr]), texto, lambda h=herr: activar(visor, h))
    m.addSeparator()
    m.addAction(_gl("com_resumen"), "Resumen de comentarios…", lambda: resumen_desde_visor(visor))
    return m


# ======================================================================== enganches del visor

def barra_visor(visor):
    _preparar(visor)
    b_com = extensiones.boton_barra("Comentar", "comentar", "Comentar: nota adhesiva (N), subrayar (U), tachar (K), "
                                    "subrayado ondulado o cuadro de texto (X)")
    b_com.setProperty("glifo", "comentar")          # el visor le pone su glifo con los colores de la barra
    b_com.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    b_com.setMenu(_menu_comentar(visor, b_com))
    b_sello = extensiones.boton_barra("Sello", "sello_visor", "Sello: pon uno de tus sellos (S repite el último)")
    b_sello.setProperty("glifo", "sello_visor")
    b_sello.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    ms = QMenu(b_sello)
    ms.aboutToShow.connect(lambda: llenar_menu_sellos(visor, ms))
    b_sello.setMenu(ms)
    for b in (b_com, b_sello):
        b.menu().aboutToHide.connect(lambda: _herramienta_cambio(visor, visor.canvas.tool))
    visor.ext[CLAVE + "_botones"] = (b_com, b_sello)
    return [b_com, b_sello]


def paneles_visor(visor):
    _preparar(visor)
    panel = PanelComentarios(visor)
    visor.ext[CLAVE + "_panel"] = panel
    return [{"clave": CLAVE, "titulo": "Comentarios", "widget": panel,
             "ayuda": "Comentarios del documento: buscar, filtrar, responder y cambiar su estado"}]


def al_abrir_visor(visor):
    _preparar(visor)
    viejo = visor.ext.get(CLAVE)
    if viejo is not None and viejo.editor is not None:
        try:
            viejo.editor.terminar(confirmar=False)
        except RuntimeError:
            pass
    QToolTip.hideText()
    est = _Estado()
    visor.ext[CLAVE] = est
    c = visor.canvas
    if c.doc is not None:
        with PDF_LOCK:
            est.comentarios = comentarios.leer(c.doc)
            est.aviso_permiso = not comentarios.permite_comentar(c.doc)
    _indexar(est)
    est.pend_marcado = sorted({cm.pagina for cm in est.comentarios if cm.tipo in MARCAS})
    _programar_marcado(visor)
    p = _panel(visor)
    if p is not None:
        p.sel = None
        p.respuesta_abierta = False
        p.recargar()


def evento_visor(visor, tipo, e):
    est = visor.ext.get(CLAVE)
    c = visor.canvas
    if est is None or c.doc is None or ctx.busy:
        return False
    if tipo == "tecla":
        return _tecla(visor, est, e)
    if c.tool in HERRAMIENTAS:
        return _ev_herramienta(visor, est, tipo, e)
    if c.tool != "select":
        return False
    return _ev_seleccionar(visor, est, tipo, e)


def menu_seleccion_visor(visor, menu):
    """«Subrayar», «Tachar» y «Agregar una nota a esta selección» en el menú del texto seleccionado.  Se
    agregan cuando el menú se va a mostrar (así el menú básico del visor queda igual para quien lo revise
    sin abrirlo)."""

    def agregar():
        if menu.property("comentarios_listo"):
            return
        menu.setProperty("comentarios_listo", True)
        rot = bool(visor.canvas.view_rot)
        menu.addSeparator()
        for texto, ic, fn in (("Subrayar", "com_subrayar", lambda: marcar_seleccion(visor, "subrayado")),
                              ("Tachar", "com_tachar", lambda: marcar_seleccion(visor, "tachado")),
                              ("Agregar una nota a esta selección", "com_nota", lambda: nota_en_seleccion(visor))):
            a = menu.addAction(_gl(ic), texto, fn)
            a.setEnabled(not rot)

    menu.aboutToShow.connect(agregar)


def cambios_visor(visor):
    est = visor.ext.get(CLAVE)
    return bool(est is not None and est.ops)


def quita_contenido(ops) -> bool:
    """¿Alguna operación quita algo que ya estaba en el archivo (borrar un comentario o cambiarle el texto)?
    Entonces el guardado no puede ser incremental: lo anterior quedaría en la versión vieja, dentro del archivo.
    Lo creado en esta sesión (referido por /NM) todavía no está en el archivo."""
    return any(op.get("op") in ("borrar", "editar") and "xref" in (op.get("ref") or {}) for op in ops)


def _aplicador(ops: list[dict]):
    def aplicar(doc):
        comentarios.aplicar(doc, ops)

    aplicar.borra = quita_contenido(ops)
    return aplicar


def guardar_visor(visor):
    est = visor.ext.get(CLAVE)
    if est is None:
        return None
    _cerrar_editor(visor)            # lo que se estaba escribiendo también se guarda
    if not est.ops:
        return None
    return _aplicador(copy.deepcopy(est.ops))


def autoguardar_visor(visor):
    """Para el autoguardado: las mismas operaciones, con lo que se está escribiendo, pero sin cerrar el
    editor ni tocar nada de la pantalla (la persona puede estar en medio de una frase)."""
    est = visor.ext.get(CLAVE)
    if est is None:
        return None
    ops = _texto_en_curso(visor, copy.deepcopy(est.ops))
    return _aplicador(ops) if ops else None


def aviso_visor(visor):
    est = visor.ext.get(CLAVE)
    if est is None or not est.aviso_permiso:
        return None

    def entendido():
        est.aviso_permiso = False
        visor.ext_avisos.actualizar()

    return ("Este PDF pide que no se le agreguen comentarios. Puedes comentarlo igual: el original no cambia y "
            "la copia que guardes conserva esa indicación.", "Entendido", entendido)


ACCIONES_VISOR = [
    dict(icono="com_resumen", ayuda="Resumen de comentarios", texto="Resumen de comentarios…",
         accion=resumen_desde_visor, solo_menu=True),
    dict(icono="com_aplanar", ayuda="Aplanar comentarios en una copia", texto="Aplanar comentarios en una copia…",
         accion=aplanar_desde_visor, solo_menu=True),
]


# ======================================================================== diseñador de sellos

class _VistaSello(QWidget):
    """Vista previa del sello: sobre cuadros (la transparencia) y en una hoja carta a su tamaño real."""

    def __init__(self):
        super().__init__()
        self.png: bytes | None = None
        self.img = QImage()
        self.setMinimumSize(300, 380)

    def poner(self, png: bytes | None):
        self.png = png
        self.img = QImage.fromData(png) if png else QImage()
        self.update()

    def paintEvent(self, e):
        t = tema.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect())
        alto1 = round(r.height() * 0.38)
        caja = QRectF(0.5, 0.5, r.width() - 1, alto1 - 1)
        borde = QPainterPath()
        borde.addRoundedRect(caja, 12, 12)
        s = 10
        p.save()
        p.setClipPath(borde)
        claro, oscuro = QColor(t["surface"]), QColor(t["surface-sunken"])
        for y in range(0, int(alto1) + s, s):
            for x in range(0, int(r.width()) + s, s):
                p.fillRect(x, y, s, s, claro if (x // s + y // s) % 2 else oscuro)
        p.restore()
        p.setPen(QPen(QColor(t["line"]), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(borde)
        if not self.img.isNull():
            area = caja.adjusted(18, 18, -18, -18)
            tam = self.img.size().scaled(area.size().toSize(), Qt.AspectRatioMode.KeepAspectRatio)
            p.drawImage(QRectF(area.center().x() - tam.width() / 2, area.center().y() - tam.height() / 2,
                               tam.width(), tam.height()), self.img)
        f = tema.fuente("caption", self.font())
        p.setFont(f)
        p.setPen(QColor(t["ink-3"]))
        p.drawText(QRectF(2, alto1 + 8, r.width() - 4, 18), int(Qt.AlignmentFlag.AlignLeft), "En una hoja carta, a tamaño real")
        # hoja de ejemplo (carta, a escala) con el sello a su tamaño real; la hoja es blanca en los dos temas
        zona = QRectF(0, alto1 + 32, r.width(), r.height() - alto1 - 34)
        esc = max(0.05, min((zona.width() - 8) / 612, zona.height() / 792))
        hoja = QRectF(zona.center().x() - 306 * esc, zona.top(), 612 * esc, 792 * esc)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(22, 26, 37, 22))
        p.drawRoundedRect(hoja.translated(0, 2), 3, 3)
        p.setPen(QPen(QColor(t["line"]), 1))
        p.setBrush(QColor("white"))
        p.drawRoundedRect(hoja, 3, 3)
        p.setPen(QPen(QColor(tema.CLARO["line-strong"]), max(1.0, 3 * esc)))       # renglones de la hoja
        y = hoja.top() + 70 * esc
        k = 0
        while y < hoja.bottom() - 60 * esc:
            largo = 470 if k % 5 != 4 else 300
            p.drawLine(QPointF(hoja.left() + 70 * esc, y), QPointF(hoja.left() + (70 + largo) * esc, y))
            y += 16 * esc
            k += 1
        if not self.img.isNull():
            w, h = self.img.width() / ESCALA_SELLO * esc, self.img.height() / ESCALA_SELLO * esc
            p.drawImage(QRectF(hoja.right() - 70 * esc - w, hoja.top() + 90 * esc, w, h), self.img)
        p.end()


class DialogoSello(QDialog):
    """Diseñador de sellos: sus ajustes en pasos a la izquierda (texto, forma y color, imagen, tus datos) y la
    vista previa en vivo a la derecha."""

    def __init__(self, sello: Sello | None = None, parent=None, almacen: sellos.AlmacenSellos | None = None):
        super().__init__(parent)
        from .viewer import _Muestra
        self.setWindowTitle("Editar sello" if sello is not None else "Diseñar un sello")
        self.setModal(True)
        self.setObjectName("Page")                 # fondo «canvas»: el panel y la vista se ven como en las herramientas
        self.setProperty("cat", "editar")          # «Mis sellos» es de la categoría Editar
        self.setStyleSheet("QPushButton::menu-indicator { image: none; width: 0px; }")
        self.almacen = almacen or sellos.AlmacenSellos()
        self.base = sello.copia() if sello is not None else Sello(
            id="", nombre="", lineas=["REVISADO", "{nombre}", "{fecha}"], forma="redondeado", color="#1E4FA0")
        self.resultado: Sello | None = None
        self.imagen: bytes | None = self.base.imagen
        self.color = self.base.color
        self._ultimo_campo = None
        raiz = QVBoxLayout(self)
        raiz.setContentsMargins(24, 20, 24, 18)
        raiz.setSpacing(16)
        cab = QHBoxLayout()
        cab.setSpacing(14)
        cab.addWidget(GlyphTile("stamps", tema.TILE_MD, "editar"), 0, Qt.AlignmentFlag.AlignVCenter)
        titulos = QVBoxLayout()
        titulos.setSpacing(0)
        titulos.addWidget(label("Diseña tu sello" if sello is None else "Edita tu sello", "Title2"))
        titulos.addWidget(label("Lo que cambies a la izquierda se ve enseguida a la derecha.", "BodySm"))
        cab.addLayout(titulos, 1)
        raiz.addLayout(cab)
        cuerpo = QHBoxLayout()
        cuerpo.setSpacing(16)
        panel = QFrame()
        panel.setObjectName("ToolPanel")
        panel.setFixedWidth(380)
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(1, 1, 1, 1)
        scroll = QScrollArea()
        scroll.setObjectName("PanelScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.viewport().setAutoFillBackground(False)
        izq = QWidget()
        izq.setObjectName("PanelBody")
        il = QVBoxLayout(izq)
        il.setContentsMargins(18, 18, 18, 18)
        self.pasos = StepList()
        il.addWidget(self.pasos)
        il.addStretch(1)
        scroll.setWidget(izq)
        pl.addWidget(scroll)

        def campo(lay, titulo: str, w: QWidget) -> QWidget:
            caja = QWidget()
            cl = QVBoxLayout(caja)
            cl.setContentsMargins(0, 0, 0, 0)
            cl.setSpacing(6)
            cl.addWidget(label(titulo, "Label"))
            cl.addWidget(w)
            lay.addWidget(caja)
            return caja

        # 1. el texto
        f = self.pasos.add_step("Escribe el texto", "Los campos se llenan solos al poner el sello.").body
        self.e_nombre = QLineEdit(self.base.nombre)
        self.e_nombre.setPlaceholderText("Cómo aparece en el menú, p. ej. «Revisado con fecha»")
        self.e_nombre.setMaxLength(60)
        campo(f, "Nombre del sello", self.e_nombre)
        self.e_principal = QLineEdit(self.base.lineas[0] if self.base.lineas else "")
        self.e_principal.setPlaceholderText("Por ejemplo: REVISADO")
        self.e_principal.setMaxLength(sellos.MAX_LARGO_LINEA)
        campo(f, "Texto principal", self.e_principal)
        self.e_lineas = QPlainTextEdit("\n".join(self.base.lineas[1:]))
        self.e_lineas.setFixedHeight(76)
        self.e_lineas.setPlaceholderText("{nombre}\n{fecha}")
        campo(f, "Renglones adicionales (uno por renglón)", self.e_lineas)
        campos_w = QWidget()
        campos = FlowLayout(campos_w, spacing=6)
        for clave, t in (("nombre", "Mi nombre"), ("cargo", "Mi cargo"), ("fecha", "Fecha"), ("hora", "Hora")):
            b = QPushButton(f"+ {t}")
            b.setObjectName("Secondary")
            b.setProperty("tam", "sm")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip(f"Inserta {{{clave}}}: se cambia por {t.lower()} al poner el sello")
            b.clicked.connect(lambda _=False, k=clave: self.insertar(k))
            campos.addWidget(b)
        f.addWidget(campos_w)
        f.addWidget(label("La fecha y la hora se ponen solas al sellar (12/03/2025, 3:42 p. m.).", "Caption",
                          wrap=True))
        for w in (self.e_principal, self.e_lineas):
            w.installEventFilter(self)

        # 2. forma y color
        f = self.pasos.add_step("Elige la forma y el color").body
        self.c_forma = QComboBox()
        for k, t in sellos.FORMAS.items():
            self.c_forma.addItem(t, k)
        self.c_forma.setCurrentIndex(list(sellos.FORMAS).index(self.base.forma))
        campo(f, "Forma", self.c_forma)
        self.c_borde = Segmented([(t, k) for k, t in sellos.BORDES.items()],
                                 current=list(sellos.BORDES).index(self.base.borde), full=True)
        campo(f, "Borde", self.c_borde)
        colores_w = QWidget()
        colores = FlowLayout(colores_w, spacing=2)
        self.b_colores = []
        for nombre, hexa in sellos.COLORES:
            b = _Muestra()
            b.set_color(hexa)
            b.setToolTip(nombre)
            b.setAccessibleName(f"Color {nombre.lower()}")
            b.clicked.connect(lambda _=False, h=hexa: self.poner_color(h))
            self.b_colores.append(b)
            colores.addWidget(b)
        b_otro = _Muestra(otro=True)
        b_otro.clicked.connect(self._otro_color)
        colores.addWidget(b_otro)
        campo(f, "Color", colores_w)
        self.c_letra = QComboBox()
        for k, t in sellos.LETRAS.items():
            self.c_letra.addItem(t, k)
        self.c_letra.setCurrentIndex(list(sellos.LETRAS).index(self.base.letra))
        campo(f, "Letra", self.c_letra)
        self.k_negrita = SwitchRow("Negrita", checked=self.base.negrita)
        f.addWidget(self.k_negrita)
        self.k_relleno = SwitchRow("Fondo suave", "Un tono claro del color del sello dentro del borde.",
                                   checked=self.base.relleno)
        f.addWidget(self.k_relleno)
        self.k_blanco = SwitchRow("Fondo blanco", "Tapa el texto de debajo para que el sello se lea bien.",
                                  checked=self.base.fondo_blanco)
        f.addWidget(self.k_blanco)
        self.l_incl = label("", "Label")
        f.addWidget(self.l_incl)
        self.s_incl = QSlider(Qt.Orientation.Horizontal)
        self.s_incl.setRange(-15, 15)
        self.s_incl.setValue(self.base.inclinacion)
        f.addWidget(self.s_incl)

        # 3. imagen
        f = self.pasos.add_step("Agrega una imagen", "Opcional: tu firma o tu logo, junto al texto o de fondo.").body
        fila3 = QHBoxLayout()
        fila3.setSpacing(8)
        self.b_imagen = QPushButton("Elegir firma o logo…")
        self.b_imagen.setObjectName("Secondary")
        self.b_imagen.setIcon(_gl("image"))
        self.b_imagen.setCursor(Qt.CursorShape.PointingHandCursor)
        self.menu_imagen = QMenu(self.b_imagen)
        self.menu_imagen.aboutToShow.connect(self._llenar_imagenes)
        self.b_imagen.setMenu(self.menu_imagen)
        fila3.addWidget(self.b_imagen)
        self.b_quitar = QPushButton("Quitar")
        self.b_quitar.setObjectName("Ghost")
        self.b_quitar.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_quitar.clicked.connect(lambda: self.poner_imagen(None))
        fila3.addWidget(self.b_quitar)
        fila3.addStretch(1)
        f.addLayout(fila3)
        self.c_posicion = QComboBox()
        for k, t in sellos.POSICIONES_IMAGEN.items():
            self.c_posicion.addItem(t, k)
        self.c_posicion.setCurrentIndex(list(sellos.POSICIONES_IMAGEN).index(self.base.posicion_imagen))
        self.f_posicion = campo(f, "Posición de la imagen", self.c_posicion)

        # 4. tus datos
        f = self.pasos.add_step("Tus datos", "Van donde pusiste {nombre} y {cargo}.").body
        self.e_mi_nombre = QLineEdit(autor())
        self.e_mi_nombre.setPlaceholderText("Ana Pérez")
        campo(f, "Mi nombre", self.e_mi_nombre)
        self.e_mi_cargo = QLineEdit(cargo())
        self.e_mi_cargo.setPlaceholderText("Coordinadora")
        campo(f, "Mi cargo (opcional)", self.e_mi_cargo)

        cuerpo.addWidget(panel)
        pozo = QFrame()
        pozo.setObjectName("ToolMain")
        vl = QVBoxLayout(pozo)
        vl.setContentsMargins(20, 18, 20, 18)
        self.vista = _VistaSello()
        vl.addWidget(self.vista)
        cuerpo.addWidget(pozo, 1)
        raiz.addLayout(cuerpo, 1)
        pie = QHBoxLayout()
        pie.setSpacing(8)
        pie.addWidget(label(sellos.AVISO_PRIVACIDAD, "Caption", wrap=True), 1)
        bc = QPushButton("Cancelar")
        bc.setObjectName("Secondary")
        bc.clicked.connect(self.reject)
        self.b_guardar = QPushButton("Guardar sello")
        self.b_guardar.setObjectName("Primary")
        self.b_guardar.setDefault(True)
        self.b_guardar.clicked.connect(self.guardar)
        pie.addWidget(bc)
        pie.addWidget(self.b_guardar)
        raiz.addLayout(pie)

        self._t = QTimer(self)
        self._t.setSingleShot(True)
        self._t.setInterval(60)
        self._t.timeout.connect(self.actualizar)
        for w in (self.e_nombre, self.e_principal, self.e_mi_nombre, self.e_mi_cargo):
            w.textChanged.connect(lambda _=None: self._t.start())
        self.e_lineas.textChanged.connect(self._t.start)
        for w in (self.c_forma, self.c_letra, self.c_posicion):
            w.currentIndexChanged.connect(lambda _=None: self._t.start())
        self.c_borde.changed.connect(lambda *_: self._t.start())
        for w in (self.k_negrita, self.k_relleno, self.k_blanco):
            w.toggled.connect(lambda _=None: self._t.start())
        self.s_incl.valueChanged.connect(lambda _=None: self._t.start())
        self.poner_color(self.color)
        self.actualizar()
        self.resize(900, 680)

    # ---------------------------------------------------------------- campos
    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.FocusIn and obj in (self.e_principal, self.e_lineas):
            self._ultimo_campo = obj
        return False

    def insertar(self, clave: str) -> None:
        texto = "{" + clave + "}"
        destino = self._ultimo_campo or self.e_lineas
        if destino is self.e_principal:
            self.e_principal.insert(texto)
        else:
            cur = self.e_lineas.textCursor()
            cur.insertText(texto)
            self.e_lineas.setTextCursor(cur)
        destino.setFocus()

    def poner_color(self, hexa: str) -> None:
        self.color = hexa.upper()
        for b in self.b_colores:
            b.setChecked((b.property("color") or "").upper() == self.color)
        self._t.start()

    def _otro_color(self) -> None:
        col = QColorDialog.getColor(QColor(self.color), self, "Color del sello")
        if col.isValid():
            self.poner_color(col.name())

    def _llenar_imagenes(self) -> None:
        m = self.menu_imagen
        m.clear()
        m.addAction(_gl("folder_open"), "Desde un archivo…", self._imagen_archivo)
        firmas_guardadas = []
        try:
            firmas_guardadas = firmas.AlmacenFirmas().listar()
        except Exception:  # noqa: BLE001
            pass
        if firmas_guardadas:
            m.addSeparator()
            _titulo_menu(m, "Mis firmas guardadas")
            for k, ruta in enumerate(firmas_guardadas, 1):
                m.addAction(QIcon(firmas.miniatura(ruta, 96, 34)), f"Firma {k}",
                            lambda r=ruta: self.poner_imagen(Path(r).read_bytes()))
        else:
            a = m.addAction("No tienes firmas guardadas (créalas en «Formularios y firma»)")
            a.setEnabled(False)

    def _imagen_archivo(self) -> None:
        rutas = widgets.ask_open(self, False, widgets.IMAGE_FILTER, "Elige tu firma o tu logo (PNG con fondo "
                                 "transparente queda mejor)")
        if rutas:
            try:
                self.poner_imagen(sellos.imagen_desde_archivo(rutas[0]))
            except core.UserError as exc:
                QMessageBox.warning(self, "Imagen", str(exc))

    def poner_imagen(self, png: bytes | None) -> None:
        self.imagen = png
        self._t.start()

    def sello(self) -> Sello:
        """El diseño tal como está en el formulario."""
        lineas = [self.e_principal.text().strip()] + [ln.rstrip() for ln in self.e_lineas.toPlainText().split("\n")
                                                       if ln.strip()]
        return Sello(
            id=self.base.id, nombre=self.e_nombre.text().strip() or self.e_principal.text().strip()[:60],
            lineas=lineas[:sellos.MAX_LINEAS], forma=self.c_forma.currentData(), color=self.color,
            borde=self.c_borde.value(), relleno=self.k_relleno.isChecked(),
            fondo_blanco=self.k_blanco.isChecked(), letra=self.c_letra.currentData(),
            negrita=self.k_negrita.isChecked(), inclinacion=self.s_incl.value(), imagen=self.imagen,
            posicion_imagen=self.c_posicion.currentData(), ancho_pt=self.base.ancho_pt, creado=self.base.creado)

    def actualizar(self) -> None:
        v = self.s_incl.value()
        self.l_incl.setText(f"Inclinación: {v}°" if v else "Inclinación: derecho")
        hay = self.imagen is not None
        self.b_quitar.setVisible(hay)               # lo que no aplica se esconde
        self.f_posicion.setVisible(hay)
        self.b_imagen.setText("Cambiar la imagen…" if hay else "Elegir firma o logo…")
        s = self.sello()
        datos = {"nombre": self.e_mi_nombre.text().strip() or "Tu nombre", "cargo": self.e_mi_cargo.text().strip(),
                 "fecha": _dt.datetime.now()}
        if not s.lineas[0]:
            s.lineas[0] = "SELLO"
        self.vista.poner(sellos.dibujar(s, datos, ESCALA_SELLO))

    def guardar(self) -> None:
        s = self.sello()
        if not s.lineas or not s.lineas[0]:
            QMessageBox.warning(self, "Sello", "Escribe el texto principal del sello.")
            self.e_principal.setFocus()
            return
        nombre = " ".join(self.e_mi_nombre.text().split())
        if nombre or self.e_mi_cargo.text().strip() or autor():
            poner_autor(nombre or autor(), self.e_mi_cargo.text())
        try:
            self.resultado = self.almacen.guardar(s)
        except core.UserError as exc:
            QMessageBox.warning(self, "Sello", str(exc))
            return
        self.accept()


# ======================================================================== página «Mis sellos»

class _TarjetaSello(QStyledItemDelegate):
    """Un sello como tarjeta (``rp-file``): la miniatura arriba y el nombre abajo.  El elegido lleva la banda
    y el borde de la categoría (Editar)."""
    W, H = 122, 96

    def sizeHint(self, option, index):  # noqa: N802
        return QSize(self.W, self.H)

    def paint(self, p: QPainter, option, index):
        t, ton = tema.tokens(), tema.tonos("editar")
        r = QRectF(option.rect).adjusted(3, 3, -3, -3)
        sel = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setPen(QPen(QColor(ton["c"] if sel else t["line-strong"] if hover else t["line"]), 1.5 if sel else 1))
        p.setBrush(QColor(ton["band"] if sel else t["surface"]))
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)
        ic = index.data(Qt.ItemDataRole.DecorationRole)
        if isinstance(ic, QIcon):
            dpr = option.widget.devicePixelRatioF() if option.widget is not None else 2.0
            pm = ic.pixmap(QSize(int(r.width() - 16), 44), dpr)
            w, h = pm.width() / pm.devicePixelRatio(), pm.height() / pm.devicePixelRatio()
            p.drawPixmap(QRectF(r.center().x() - w / 2, r.top() + 10 + (44 - h) / 2, w, h), pm,
                         QRectF(0, 0, pm.width(), pm.height()))
        f = tema.fuente("label" if sel else "body-sm", p.font())
        p.setFont(f)
        p.setPen(QColor(ton["ink"] if sel else t["ink"]))
        nombre = QFontMetrics(f).elidedText(index.data(Qt.ItemDataRole.DisplayRole) or "", Qt.TextElideMode.ElideRight,
                                            int(r.width() - 12))
        p.drawText(QRectF(r.left() + 6, r.bottom() - 28, r.width() - 12, 20), int(Qt.AlignmentFlag.AlignCenter), nombre)
        p.restore()


class _Cuadricula(QListWidget):
    """Tarjetas de sellos que crecen hacia abajo según cuántas haya (sin barra de desplazamiento propia)."""

    def __init__(self):
        super().__init__()
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setIconSize(QSize(108, 44))
        self.setGridSize(QSize(_TarjetaSello.W, _TarjetaSello.H))
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setUniformItemSizes(True)
        self.setSpacing(0)
        self.setMouseTracking(True)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setItemDelegate(_TarjetaSello(self))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet("QListWidget { background: transparent; border: none; padding: 0px; }")
        self._ajustar()

    def _ajustar(self):
        w = max(1, self.viewport().width() or self.width())
        por_fila = max(1, w // self.gridSize().width())
        filas = max(1, math.ceil(self.count() / por_fila)) if self.count() else 1
        self.setFixedHeight(filas * self.gridSize().height() + 4)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._ajustar()


def _icono_sello(s: Sello) -> QIcon:
    png = _mini(s, 2.4)
    pm = QPixmap.fromImage(QImage.fromData(png))
    lienzo = QPixmap(300, 128)
    lienzo.fill(Qt.GlobalColor.transparent)
    p = QPainter(lienzo)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    esc = pm.scaled(292, 120, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    p.drawPixmap((300 - esc.width()) // 2, (128 - esc.height()) // 2, esc)
    p.end()
    lienzo.setDevicePixelRatio(2)
    return QIcon(lienzo)


class SellosPage(ToolPage):
    """«Mis sellos» con la plantilla de herramienta: elegir el sello, ajustarlo y el PDF en pasos; el sello
    elegido, grande y en una hoja a tamaño real, a la derecha."""
    key = "stamps"
    title = "Mis sellos"
    subtitle = "Diseña sellos con tu nombre, tu cargo, la fecha o tu firma, y ponlos con un clic en cualquier PDF."
    icon_name = "stamps"
    action_text = "Poner un sello en un PDF…"

    def build(self):
        self.archivo: str | None = None
        self.almacen = sellos.AlmacenSellos()
        self.footer_info.setWordWrap(True)         # con la ventana angosta, la frase pasa a dos renglones
        cab = QWidget()
        hl = QHBoxLayout(cab)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(8)
        self.b_importar = QPushButton("Importar…")
        self.b_importar.setObjectName("Ghost")
        self.b_importar.setToolTip(f"Traer un sello exportado desde otro equipo (*{sellos.EXTENSION})")
        self.b_nuevo = QPushButton("Nuevo sello")
        self.b_nuevo.setObjectName("Secondary")
        self.b_nuevo.setIcon(_gl("add", "ink"))
        for b, fn in ((self.b_importar, self.importar), (self.b_nuevo, self.nuevo)):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, f=fn: f())
            hl.addWidget(b)
        self.set_header_widget(cab)

        g = self.step("Elige un sello", help="Doble clic sobre uno tuyo para editarlo.")
        self.t_mios = label("Tus sellos", "Label")
        g.addWidget(self.t_mios)
        self.cuadricula = _Cuadricula()
        self.cuadricula.itemSelectionChanged.connect(lambda: self._eleccion(self.cuadricula, self.inicio))
        self.cuadricula.itemDoubleClicked.connect(lambda _it: self.editar())
        g.addWidget(self.cuadricula)
        self.vacio = Note("Todavía no tienes sellos propios. Crea uno con «Nuevo sello» o parte de uno de "
                          "«Para empezar».")
        g.addWidget(self.vacio)
        g.addWidget(label("Para empezar", "Label"))
        self.inicio = _Cuadricula()
        for s in sellos.PARA_EMPEZAR:
            it = QListWidgetItem(_icono_sello(s), s.nombre)
            it.setData(Qt.ItemDataRole.UserRole, s.id)
            it.setToolTip(" · ".join(s.lineas))
            self.inicio.addItem(it)
        self.inicio._ajustar()
        self.inicio.itemSelectionChanged.connect(lambda: self._eleccion(self.inicio, self.cuadricula))
        self.inicio.itemDoubleClicked.connect(lambda _it: self.usar_como_base())
        g.addWidget(self.inicio)

        a = self.step("Ajústalo")
        self.paso_ajuste = a.parentWidget()
        acciones = QWidget()
        fa = FlowLayout(acciones, spacing=8)
        self.b_editar = QPushButton("Editar")
        self.b_duplicar = QPushButton("Duplicar")
        self.b_exportar = QPushButton("Exportar…")
        self.b_eliminar = QPushButton("Eliminar")
        self.b_base = QPushButton("Usar como base")
        for b, fn, glifo in ((self.b_editar, self.editar, "edittext"), (self.b_duplicar, self.duplicar, "duplicate"),
                             (self.b_exportar, self.exportar, None), (self.b_eliminar, self.eliminar, "trash"),
                             (self.b_base, self.usar_como_base, "duplicate")):
            b.setObjectName("Danger" if b is self.b_eliminar else "Secondary")
            b.setProperty("tam", "sm")
            if glifo:
                b.setIcon(_gl(glifo, "danger" if b is self.b_eliminar else "ink-2"))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, f=fn: f())
            fa.addWidget(b)
        self.b_exportar.setToolTip("Guardar el sello en un archivo para llevarlo a otro equipo")
        a.addWidget(acciones)

        d = self.step("¿En qué PDF lo pones?", help="Se abre en «Ver y editar PDF» con el sello listo para poner.")
        self.ficha = FileChip()
        self.ficha.change_clicked.connect(self._elegir_pdf)
        self.ficha.hide()
        d.addWidget(self.ficha)
        self.b_elegir = QPushButton("Elegir el PDF…")
        self.b_elegir.setObjectName("Secondary")
        self.b_elegir.setProperty("tam", "sm")
        self.b_elegir.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_elegir.clicked.connect(self._elegir_pdf)
        d.addWidget(self.b_elegir, 0, Qt.AlignmentFlag.AlignLeft)
        d.addWidget(label(sellos.AVISO_PRIVACIDAD, "Caption", wrap=True))

        # área principal: el sello elegido, grande y en una hoja a tamaño real
        principal = QWidget()
        vl = QVBoxLayout(principal)
        vl.setContentsMargins(24, 20, 24, 20)
        vl.setSpacing(4)
        fila = QHBoxLayout()
        fila.setSpacing(10)
        self.v_nombre = label("", "Title2")
        fila.addWidget(self.v_nombre)
        self.v_tipo = Chip("", "cat")
        fila.addWidget(self.v_tipo)
        fila.addStretch(1)
        vl.addLayout(fila)
        self.v_lineas = label("", "BodySm", wrap=True)
        vl.addWidget(self.v_lineas)
        vl.addSpacing(12)
        self.vista = _VistaSello()
        self.vista.setMinimumSize(260, 340)
        vl.addWidget(self.vista, 1)
        self.set_main(principal)
        self.recargar()

    # ---------------------------------------------------------------- lista
    def recargar(self, elegir: str | None = None) -> None:
        actual = elegir or self.seleccionado()
        self.cuadricula.blockSignals(True)
        self.cuadricula.clear()
        mios = self.almacen.listar()
        for s in mios:
            it = QListWidgetItem(_icono_sello(s), s.nombre)
            it.setData(Qt.ItemDataRole.UserRole, s.id)
            it.setToolTip(" · ".join(s.lineas))
            self.cuadricula.addItem(it)
            if s.id == actual:
                it.setSelected(True)
                self.cuadricula.setCurrentItem(it)
        self.cuadricula.blockSignals(False)
        self.cuadricula.setVisible(bool(mios))
        self.vacio.setVisible(not mios)
        self.cuadricula._ajustar()
        if self.seleccionado() is not None:
            self._eleccion(self.cuadricula, self.inicio)
        else:
            self._botones()

    def seleccionado(self) -> str | None:
        its = self.cuadricula.selectedItems() if hasattr(self, "cuadricula") else []
        return its[0].data(Qt.ItemDataRole.UserRole) if its else None

    def _eleccion(self, lista: QListWidget, otra: QListWidget) -> None:
        """Se elige un sello en una de las dos cuadrículas: la otra suelta el suyo."""
        if lista.selectedItems() and otra.selectedItems():
            otra.blockSignals(True)
            otra.clearSelection()
            otra.blockSignals(False)
            otra.viewport().update()
        self._botones()

    def _sello_actual(self) -> tuple[Sello, str]:
        """El sello que se pondría ahora y de dónde sale: uno tuyo elegido, uno de «Para empezar» elegido, el
        último que usaste o, si no, el primero que haya."""
        sid = self.seleccionado()
        s = self.almacen.obtener(sid) if sid else None
        if s is not None:
            return s, "mio"
        its = self.inicio.selectedItems()
        s = sellos.de_fabrica(its[0].data(Qt.ItemDataRole.UserRole)) if its else None
        if s is not None:
            return s, "inicio"
        s = _ultimo_sello()
        if s is not None:
            return s, "ultimo"
        mios = self.almacen.listar()
        return (mios[0], "mio") if mios else (sellos.PARA_EMPEZAR[0], "inicio")

    def _botones(self) -> None:
        mio = self.seleccionado() is not None
        de_inicio = bool(self.inicio.selectedItems())
        for b in (self.b_editar, self.b_duplicar, self.b_exportar, self.b_eliminar):
            b.setVisible(mio)
        self.b_base.setVisible(de_inicio)
        self.paso_ajuste.setVisible(mio or de_inicio)     # sin un sello elegido, el paso no aplica
        st = self.steps.steps[1] if self.steps is not None else None
        if st is not None:
            st.set_help("Cámbialo, haz una copia o llévalo a otro equipo." if mio else
                        "Copia este diseño para ponerle tu nombre, tu firma y tus colores.")
        s, origen = self._sello_actual()
        self.v_nombre.setText(s.nombre)
        self.v_tipo.setText({"mio": "Tuyo", "inicio": "Para empezar", "ultimo": "El último que usaste"}[origen])
        self.v_lineas.setText(" · ".join(ln for ln in s.lineas if ln))
        try:
            self.vista.poner(_mini(s, ESCALA_SELLO))
        except Exception:  # noqa: BLE001 — un sello dañado no debe romper la pantalla
            self.vista.poner(None)
        self.refresh()

    def refresh(self):
        super().refresh()
        if not hasattr(self, "inicio"):
            return
        s, _origen = self._sello_actual()
        n = len(self.almacen.listar())
        chips = [(s.nombre, "cat")] + ([f"{n} sello{'s' if n != 1 else ''} tuyo{'s' if n != 1 else ''}"] if n else [])
        self.set_summary(chips, "Se abre en «Ver y editar PDF» con el sello listo para poner." if self.archivo
                         else "Al pulsar el botón eliges el PDF y se abre en «Ver y editar PDF».")

    # ---------------------------------------------------------------- acciones
    def _dialogo(self, sello: Sello | None) -> Sello | None:
        d = DialogoSello(sello, self, self.almacen)
        if d.exec() == QDialog.DialogCode.Accepted and d.resultado is not None:
            self.recargar(d.resultado.id)
            return d.resultado
        return None

    def nuevo(self) -> Sello | None:
        if self.almacen.lleno():
            QMessageBox.information(self, "Mis sellos",
                                    f"Ya tienes {sellos.MAX_SELLOS} sellos. Borra uno para crear otro.")
            return None
        return self._dialogo(None)

    def editar(self) -> Sello | None:
        sid = self.seleccionado()
        s = self.almacen.obtener(sid) if sid else None
        return self._dialogo(s) if s is not None else None

    def duplicar(self) -> Sello | None:
        sid = self.seleccionado()
        if not sid:
            return None
        try:
            s = self.almacen.duplicar(sid)
        except core.UserError as exc:
            QMessageBox.warning(self, "Mis sellos", str(exc))
            return None
        self.recargar(s.id)
        return s

    def eliminar(self, confirmar: bool = True) -> bool:
        sid = self.seleccionado()
        s = self.almacen.obtener(sid) if sid else None
        if s is None:
            return False
        if confirmar:
            box = QMessageBox(self)
            box.setWindowTitle("Eliminar sello")
            box.setText(f"¿Eliminar el sello «{s.nombre}» de este equipo?")
            box.setInformativeText("Los PDF que ya sellaste no cambian.")
            b_si = box.addButton("Eliminar", QMessageBox.ButtonRole.DestructiveRole)
            box.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is not b_si:
                return False
        try:
            self.almacen.borrar(sid)
        except core.UserError as exc:
            QMessageBox.warning(self, "Mis sellos", str(exc))
            return False
        self.recargar()
        return True

    def exportar(self) -> str | None:
        sid = self.seleccionado()
        s = self.almacen.obtener(sid) if sid else None
        if s is None:
            return None
        sugerido = str(Path(ctx.last_dir) / f"{core.safe_filename(s.nombre, 'sello')}{sellos.EXTENSION}")
        ruta, _ = QFileDialog.getSaveFileName(self, "Exportar sello", sugerido,
                                              f"Sello de RonyPDF (*{sellos.EXTENSION})")
        if not ruta:
            return None
        try:
            final = self.almacen.exportar(sid, ruta)
        except core.UserError as exc:
            QMessageBox.warning(self, "Mis sellos", str(exc))
            return None
        widgets.show_done(self, "Sello exportado", f"Puedes llevar «{final.name}» a otro equipo e importarlo allá.",
                          file=str(final))
        return str(final)

    def importar(self) -> Sello | None:
        ruta, _ = QFileDialog.getOpenFileName(self, "Importar sello", ctx.last_dir,
                                              f"Sello de RonyPDF (*{sellos.EXTENSION})")
        if not ruta:
            return None
        try:
            s = self.almacen.importar(ruta)
        except core.UserError as exc:
            QMessageBox.warning(self, "Mis sellos", str(exc))
            return None
        self.recargar(s.id)
        return s

    def usar_como_base(self) -> Sello | None:
        its = self.inicio.selectedItems()
        base = sellos.de_fabrica(its[0].data(Qt.ItemDataRole.UserRole)) if its else None
        if base is None:
            return None
        if self.almacen.lleno():
            QMessageBox.information(self, "Mis sellos",
                                    f"Ya tienes {sellos.MAX_SELLOS} sellos. Borra uno para crear otro.")
            return None
        return self._dialogo(base.copia(id="", nombre=f"{base.nombre} (mío)", creado=""))

    # ---------------------------------------------------------------- poner en un PDF
    def _elegir_pdf(self) -> None:
        rutas = widgets.ask_open(self, False, title="Elige el PDF que quieres sellar")
        if rutas:
            self.accept_files(rutas[:1])

    def accept_files(self, paths):
        pdfs = [p for p in paths if str(p).lower().endswith(".pdf")]
        if pdfs:
            self.archivo = pdfs[0]
            nombre = Path(self.archivo).name
            corto = nombre if len(nombre) <= 34 else nombre[:22] + "…" + nombre[-10:]
            self.action.setText(f"Poner un sello en «{corto}»")
            self.action.setToolTip(self.archivo)
            try:
                tam = core.human_size(os.path.getsize(self.archivo)).replace(".", ",")
            except OSError:
                tam = ""
            self.ficha.set_file(nombre, tam, self.archivo)
            self.ficha.show()
            self.b_elegir.hide()
            self.refresh()

    def run(self):
        ruta = self.archivo
        if not ruta:
            rutas = widgets.ask_open(self, False, title="Elige el PDF que quieres sellar")
            if not rutas:
                return
            ruta = rutas[0]
        visor = extensiones.ir_a("viewer")
        if visor is None or not visor.load(ruta):
            return
        s, _origen = self._sello_actual()
        armar_sello(visor, s)


REGISTRO = dict(clave="stamps", titulo="Mis sellos", seccion="editar", orden=30, pagina=SellosPage,
                dibujo=dibujo_sellos, inicio="Diseña tus sellos y úsalos en cualquier PDF")


# ======================================================================== pasos de «Acciones por lotes»
# «Poner un sello» usa los sellos de «Mis sellos» (y los genéricos de «Para empezar»), con el nombre de quien usa
# la aplicación como autor.  El sello se dibuja una sola vez, en la interfaz, antes de lanzar el lote: todos los
# archivos llevan la fecha y la hora de ese momento.

TAMANOS_SELLO = {"pequeno": ("Pequeño", 0.75), "normal": ("Normal", 1.0), "grande": ("Grande", 1.35)}


def _sellos_lote() -> list[tuple[str, str]]:
    try:
        mios = sellos.AlmacenSellos().listar()
    except Exception:  # noqa: BLE001 — una carpeta inaccesible no debe romper la lista
        mios = []
    return [(s.id, s.nombre) for s in mios] + [(s.id, f"{s.nombre} (para empezar)") for s in sellos.PARA_EMPEZAR]


def _sello_por_id(sid) -> Sello | None:
    if not sid:
        return None
    try:
        return sellos.de_fabrica(str(sid)) or sellos.AlmacenSellos().obtener(str(sid))
    except Exception:  # noqa: BLE001
        return None


def preparar_sello_lote(v: dict, preguntar: bool = True) -> dict:
    """Dibuja el sello elegido con el nombre, el cargo, la fecha y la hora de este momento."""
    s = _sello_por_id(v.get("sello"))
    if s is None:
        raise core.UserError("El sello elegido ya no está en «Mis sellos»: elige otro en las opciones del paso.")
    nombre = (asegurar_autor(QApplication.activeWindow()) if preguntar else autor()) or ""
    if preguntar and not nombre:
        raise core.UserError("Los sellos llevan tu nombre como autor: escríbelo para poder sellar.")
    datos = {"nombre": nombre, "cargo": cargo(), "fecha": _dt.datetime.now()}
    png = sellos.dibujar(s, datos, ESCALA_SELLO)
    w, h = sellos.tamano_pt(png, ESCALA_SELLO)
    f = TAMANOS_SELLO.get(v.get("tamano"), TAMANOS_SELLO["normal"])[1]
    return {"png": png, "ancho": w * f, "alto": h * f, "autor": nombre, "texto": sellos.texto_plano(s, datos),
            "color": s.color, "asunto": f"Sello: {s.nombre}", "nombre": s.nombre}


def _ej_sello(entrada, salida, v, pw, progress):
    prep = v.get("_preparado") or preparar_sello_lote(v, preguntar=False)   # «Probar con el primer archivo»
    n = comentarios.sellar(entrada, salida, prep["png"], prep["ancho"], prep["alto"], autor=prep["autor"],
                           texto=prep["texto"], color=prep["color"], asunto=prep["asunto"], paginas=v["paginas"],
                           posicion=v["posicion"], password=pw, progress=progress)
    return f"«{prep['nombre']}» en {n} página{'s' if n != 1 else ''}"


def _ej_aplanar(entrada, salida, v, pw, progress):
    try:
        n = comentarios.aplanar(entrada, salida, password=pw, progress=progress)
    except core.UserError as exc:
        if "no tiene comentarios" not in str(exc):
            raise
        if os.path.exists(salida):
            os.remove(salida)
        return "no tenía comentarios"
    return f"{n} comentario{'s' if n != 1 else ''} y sello{'s' if n != 1 else ''} fijos en la página"


def _resumen_sello(v: dict) -> str:
    s = _sello_por_id(v.get("sello"))
    return (f"«{s.nombre if s else '¿?'}», {comentarios.POSICIONES_SELLO.get(v['posicion'], '').lower()}, "
            f"{comentarios.PAGINAS_SELLO.get(v['paginas'], '').replace('Solo en', 'solo en').replace('En', 'en')}")


def _pasos_lote() -> list:
    from .lotes import Opcion, Paso
    return [
        Paso("sello", "Poner un sello",
             "Pone uno de tus sellos de «Mis sellos», con tu nombre como autor. La fecha y la hora son las del "
             "momento en que empieza el lote. Queda como un sello normal: se puede mover o borrar en cualquier "
             "lector de PDF.",
             [Opcion("sello", "Sello", "lista", sellos.PARA_EMPEZAR[0].id, _sellos_lote, comprobar=False),
              Opcion("paginas", "En qué páginas", "lista", "primera", list(comentarios.PAGINAS_SELLO.items())),
              Opcion("posicion", "Dónde", "lista", "arriba-derecha", list(comentarios.POSICIONES_SELLO.items())),
              Opcion("tamano", "Tamaño", "lista", "normal", [(k, t) for k, (t, _f) in TAMANOS_SELLO.items()])],
             _ej_sello, preparar=preparar_sello_lote, resumen=_resumen_sello,
             validar=lambda v: None if _sello_por_id(v.get("sello")) else "elige uno de tus sellos"),
        Paso("aplanar", "Dejar fijos los comentarios y sellos",
             "Los comentarios, resaltados y sellos pasan a ser parte de la página: ya no se pueden mover ni borrar.",
             [], _ej_aplanar, resumen=lambda v: "Ya no se podrán mover ni borrar"),
    ]


PASOS_LOTE = _pasos_lote()
