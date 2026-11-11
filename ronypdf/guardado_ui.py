"""Guardado seguro en la interfaz: «Guardar» con copia de respaldo, autoguardado, recuperación y la página
«Respaldo y recuperación».

Lo usan el visor, los formularios y el organizador (que llaman a ``preparar_directo``, ``lanzar_guardado``,
``preguntar_cambios``…) y la ventana principal, a través de los enganches ``al_iniciar`` y ``al_cerrar`` de
extensiones.py: al iniciar se prepara el autoguardado y, si una sesión anterior terminó de forma inesperada,
se ofrece recuperar su trabajo; al cerrar normalmente se borra lo autoguardado de la sesión.
"""

from __future__ import annotations

import copy
import os
import time
import traceback
from collections import defaultdict
from pathlib import Path

from PySide6.QtCore import QEvent, QEventLoop, QObject, QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QFrame,
                               QGridLayout, QHBoxLayout, QLabel, QMenu, QMessageBox, QPushButton,
                               QSizePolicy, QSpinBox, QStyle, QStyledItemDelegate, QToolButton, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from . import core, extensiones, formularios, guardado, icons, tema
from .icons import icon
from .widgets import (Chip, GlyphTile, Note, PdfThumb, SwitchRow, ToolPage, ask_save, cat_de, ctx, label,
                      pintar_miniatura, reveal, show_done, suggest_output)

INTERVALOS = [(0, "Desactivado"), (30, "30 segundos"), (60, "1 minuto"), (120, "2 minutos"), (300, "5 minutos")]
TITULOS = {"viewer": "Ver y editar PDF", "forms": "Formularios y firma", "organize": "Organizar páginas"}


# --------------------------------------------------------------------------
# Ícono: hoja con una flecha circular (versiones anteriores), detalle rojo
# --------------------------------------------------------------------------

def dibujo(p, base, acento):
    path = QPainterPath()
    path.moveTo(4, 2.5)
    path.lineTo(12.5, 2.5)
    path.lineTo(16, 6)
    path.lineTo(16, 10)
    p.drawPath(path)
    p.drawPolyline([QPointF(12.5, 2.5), QPointF(12.5, 6), QPointF(16, 6)])
    p.drawPolyline([QPointF(4, 2.5), QPointF(4, 19.5), QPointF(10, 19.5)])
    p.drawLine(QPointF(7, 8), QPointF(12, 8))
    p.drawLine(QPointF(7, 11.5), QPointF(10.5, 11.5))
    extensiones.lapiz(p, acento)
    arco = QPainterPath()
    arco.arcMoveTo(QRectF(11, 11, 10, 10), 150)
    arco.arcTo(QRectF(11, 11, 10, 10), 150, -300)
    p.drawPath(arco)
    ini = QPointF(16 + 5 * -0.866, 16 - 5 * 0.5)
    p.drawPolyline([QPointF(ini.x() - 0.4, ini.y() - 2.6), ini, QPointF(ini.x() + 2.5, ini.y() - 0.6)])
    p.drawPolyline([QPointF(16, 13.6), QPointF(16, 16), QPointF(17.8, 17.2)])


icons.EXTRA.setdefault("backup", dibujo)


# --------------------------------------------------------------------------
# Ajustes y carpetas
# --------------------------------------------------------------------------

def ajuste(clave: str, defecto):
    return ctx.settings.get(clave, defecto) if ctx.settings is not None else defecto


def poner_ajuste(clave: str, valor) -> None:
    if ctx.settings is not None:
        try:
            ctx.settings.set(clave, valor)
        except Exception:  # noqa: BLE001 — los ajustes nunca interrumpen el trabajo
            pass


def ajustes_path() -> str | None:
    try:
        return str(ctx.settings.path) if ctx.settings is not None else None
    except Exception:  # noqa: BLE001
        return None


def respaldo_activo() -> bool:
    return bool(ajuste("guardado_respaldo", True))


def retencion() -> guardado.Retencion:
    return guardado.retencion_de(ctx.settings)


def carpetas_respaldos() -> list[Path]:
    return guardado.carpetas_lectura(guardado.CARPETA_RESPALDOS, ajustes_path())


def carpetas_recuperacion(ajustes: str | None = None) -> list[Path]:
    return guardado.carpetas_lectura(guardado.CARPETA_RECUPERACION, ajustes or ajustes_path())


def intervalo() -> int:
    try:
        v = int(ajuste("autoguardado_segundos", 60))
    except (TypeError, ValueError):
        v = 60
    return v if v in dict(INTERVALOS) else 60


# --------------------------------------------------------------------------
# Preguntas con botones propios (se pueden parchear en las pruebas)
# --------------------------------------------------------------------------

_ICONOS = {"pregunta": QMessageBox.Icon.Question, "aviso": QMessageBox.Icon.Warning,
           "info": QMessageBox.Icon.Information}


def _preguntar(parent, titulo: str, texto: str, botones, defecto: str | None = None, icono: str = "pregunta",
               casilla: str | None = None, detalle: str | None = None) -> tuple[str, bool]:
    """Diálogo con botones de texto propio.  ``botones`` = [(clave, texto, rol)].  Devuelve (clave, casilla)."""
    box = QMessageBox(parent)
    box.setWindowTitle(titulo)
    box.setIcon(_ICONOS.get(icono, QMessageBox.Icon.Question))
    box.setText(texto)
    if detalle:
        box.setInformativeText(detalle)
    chk = None
    if casilla:
        chk = QCheckBox(casilla)
        box.setCheckBox(chk)
    mapa = {}
    for clave, texto_b, rol in botones:
        b = box.addButton(texto_b, rol)
        mapa[b] = clave
        if clave == defecto:
            box.setDefaultButton(b)
        if clave == "cancelar":
            box.setEscapeButton(b)
    box.exec()
    return mapa.get(box.clickedButton(), "cancelar"), bool(chk is not None and chk.isChecked())


_ACEPTAR = QMessageBox.ButtonRole.AcceptRole
_DESTRUCTIVO = QMessageBox.ButtonRole.DestructiveRole
_OTRO = QMessageBox.ButtonRole.ActionRole
_RECHAZAR = QMessageBox.ButtonRole.RejectRole


def preguntar_cambios(parent, titulo: str, texto: str) -> str:
    """«Guardar», «No guardar» o «Cancelar».  Devuelve 'guardar', 'descartar' o 'cancelar'."""
    r, _ = _preguntar(parent, titulo, texto, [("guardar", "Guardar", _ACEPTAR), ("descartar", "No guardar", _DESTRUCTIVO),
                                              ("cancelar", "Cancelar", _RECHAZAR)], defecto="guardar", icono="aviso")
    return r


def confirmar_mismo_archivo(parent, nombre: str, respaldo: bool) -> tuple[str, bool]:
    """Confirmación del primer «Guardar» de un documento.  (respuesta, «No volver a preguntar»)."""
    texto = f"Los cambios se guardarán en «{nombre}» y reemplazarán la versión anterior. "
    texto += ("Antes se guarda una copia de respaldo, por si quieres volver atrás." if respaldo
              else "No se guardará copia de respaldo.")
    return _preguntar(parent, "Guardar en el mismo archivo", texto,
                      [("guardar", "Guardar", _ACEPTAR), ("guardar_como", "Guardar como…", _OTRO),
                       ("cancelar", "Cancelar", _RECHAZAR)], defecto="guardar", casilla="No volver a preguntar")


def preguntar_cambio_externo(parent, nombre: str) -> str:
    r, _ = _preguntar(parent, "El archivo cambió", f"«{nombre}» cambió fuera de RonyPDF desde que lo abriste.",
                      [("guardar_como", "Guardar como…", _ACEPTAR), ("reemplazar", "Reemplazar de todas formas",
                                                                     _DESTRUCTIVO),
                       ("cancelar", "Cancelar", _RECHAZAR)], defecto="guardar_como", icono="aviso",
                      detalle="Si lo reemplazas, se pierde lo que otro programa le haya cambiado (antes se guarda "
                              "una copia de respaldo).")
    return r


def avisar_firma(parent) -> str:
    r, _ = _preguntar(parent, "Firma digital", "Este PDF tiene una firma digital. Guardar estos cambios la invalidará.",
                      [("copia", "Guardar como copia…", _ACEPTAR), ("cancelar", "Cancelar", _RECHAZAR)],
                      defecto="copia", icono="aviso",
                      detalle="Para no romperla, guarda tus cambios en una copia con otro nombre.")
    return r


def avisar_no_escribible(parent, motivo: str) -> str:
    r, _ = _preguntar(parent, "No se puede guardar en el mismo archivo", motivo,
                      [("guardar_como", "Guardar como…", _ACEPTAR), ("cancelar", "Cancelar", _RECHAZAR)],
                      defecto="guardar_como", icono="aviso", detalle="Puedes guardar tus cambios en otro archivo.")
    return r


def respaldo_fallido(parent, mensaje: str) -> str:
    r, _ = _preguntar(parent, "No se pudo hacer la copia de respaldo",
                      f"{mensaje}\n\nPor seguridad no se reemplazó el archivo.",
                      [("guardar_como", "Guardar como…", _ACEPTAR), ("cancelar", "Cancelar", _RECHAZAR)],
                      defecto="guardar_como", icono="aviso",
                      detalle="Puedes guardar tus cambios en otro archivo, o liberar espacio e intentarlo de nuevo.")
    return r


def aviso_guardado(con_respaldo: bool) -> str:
    return ("Guardado. La versión anterior quedó en las copias de respaldo." if con_respaldo
            else "Guardado.")


# --------------------------------------------------------------------------
# «Guardar» con menú (Guardar · Guardar como…)
# --------------------------------------------------------------------------

class BotonGuardar(QFrame):
    """Botón principal rojo con una flecha de menú: «Guardar   Ctrl+S» y «Guardar como…   Ctrl+Mayús+S».
    Sin cambios (o si el guardado directo no aplica), el botón dice «Guardar como…»."""

    ANCHO_MAX = 150

    def __init__(self, principal, guardar, guardar_como, ayuda_directo: str = ""):
        super().__init__()
        self.setObjectName("BotonGuardar")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.principal = QPushButton("Guardar como…")
        self.principal.setObjectName("Primary")
        self.principal.setCursor(Qt.CursorShape.PointingHandCursor)
        self.principal.setStyleSheet("QPushButton#Primary { border-top-right-radius: 0px; "
                                     "border-bottom-right-radius: 0px; padding: 10px 10px 10px 14px; }")
        self.principal.clicked.connect(lambda: principal())
        self.flecha = QToolButton()
        self.flecha.setObjectName("GuardarFlecha")
        self.flecha.setIcon(icon("chevron_down", tema.color("on-brand"), size=14))
        self.flecha.setIconSize(QSize(14, 14))
        self.flecha.setCursor(Qt.CursorShape.PointingHandCursor)
        self.flecha.setToolTip("Más formas de guardar")
        self.flecha.setAccessibleName("Más formas de guardar")
        self.flecha.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        marca, encima = tema.color("brand"), tema.color("brand-hover")
        apagado = tema.mezclar(marca, tema.color("surface"), 0.55)      # como el principal desactivado
        self.flecha.setStyleSheet(
            f"QToolButton#GuardarFlecha {{ background: {marca}; border: 1px solid {marca}; "
            "border-left: 1px solid rgba(255,255,255,0.55); border-top-right-radius: 10px; "
            "border-bottom-right-radius: 10px; padding: 0px 5px; }"
            f"QToolButton#GuardarFlecha:hover {{ background: {encima}; }}"
            f"QToolButton#GuardarFlecha:disabled {{ background: {apagado}; border-color: {apagado}; }}"
            "QToolButton#GuardarFlecha::menu-indicator { image: none; width: 0px; }")
        menu = QMenu(self.flecha)
        self.a_guardar = menu.addAction(icon("save"), "Guardar\tCtrl+S", lambda: guardar())
        self.a_como = menu.addAction("Guardar como…\tCtrl+Mayús+S", lambda: guardar_como())
        self.flecha.setMenu(menu)
        lay.addWidget(self.principal)
        lay.addWidget(self.flecha)
        self.ayuda_directo = ayuda_directo
        self._alto()

    def _alto(self):
        self.principal.ensurePolished()
        self.flecha.setFixedSize(24, max(28, self.principal.sizeHint().height()))

    def showEvent(self, e):
        super().showEvent(e)
        self._alto()

    def actualizar(self, habilitado: bool, cambios: bool, directo: bool, motivo: str = "") -> None:
        """``directo``: se puede guardar en el mismo archivo.  ``motivo``: por qué no (para la ayuda)."""
        self.principal.setEnabled(habilitado)
        self.flecha.setEnabled(habilitado)
        guardar = cambios and directo
        self.principal.setText("Guardar" if guardar else "Guardar como…")
        if guardar:
            self.principal.setToolTip("Guardar en el mismo archivo, con una copia de respaldo (Ctrl+S)")
        elif motivo:
            self.principal.setToolTip(motivo)
        else:
            self.principal.setToolTip("Guardar una copia con otro nombre (Ctrl+Mayús+S)")
        self.a_guardar.setEnabled(guardar)
        self.a_guardar.setText("Guardar\tCtrl+S" if not motivo or guardar else f"Guardar ({motivo})")


# --------------------------------------------------------------------------
# Guardar en el mismo archivo
# --------------------------------------------------------------------------

_confirmados: set[str] = set()       # documentos ya confirmados en esta sesión
_guardando = [0]


def _clave(ruta: str) -> str:
    return os.path.normcase(os.path.abspath(ruta))


def callback(al_terminar):
    """Función ``fin(ok)`` que avisa a ``al_terminar`` (si hay) y devuelve None."""
    def fin(ok: bool = True):
        if callable(al_terminar):
            al_terminar(bool(ok))
        return None
    return fin


def preparar_directo(parent, ruta: str, firma_disco, incremental: bool, firmado: bool) -> str:
    """Preguntas antes de un «Guardar» sobre el mismo archivo.  Devuelve 'guardar', 'guardar_como' o
    'cancelar'.  Revisa que el archivo siga ahí y se pueda escribir, que no haya cambiado fuera de RonyPDF,
    que guardar no rompa una firma digital y, la primera vez en la sesión, lo confirma."""
    motivo = guardado.no_escribible(ruta)
    if motivo:
        return avisar_no_escribible(parent, motivo)
    nombre = Path(ruta).name
    if guardado.cambio_en_disco(ruta, firma_disco):
        r = preguntar_cambio_externo(parent, nombre)
        if r != "reemplazar":
            return r
    if firmado and not incremental:
        return "guardar_como" if avisar_firma(parent) == "copia" else "cancelar"
    if ajuste("guardado_confirmar", True) and _clave(ruta) not in _confirmados:
        r, no_preguntar = confirmar_mismo_archivo(parent, nombre, respaldo_activo())
        if r == "guardar":
            _confirmados.add(_clave(ruta))
            if no_preguntar:
                poner_ajuste("guardado_confirmar", False)
        return r
    return "guardar"


def lanzar_guardado(label_: str, fn, done, on_finally=None, **kwargs) -> None:
    """``ctx.run_task`` que además anota que hay un guardado en curso (para no cerrar a mitad)."""
    if ctx.busy:
        if on_finally:
            on_finally()
        return
    _guardando[0] += 1

    def fin():
        _guardando[0] = max(0, _guardando[0] - 1)
        if on_finally:
            on_finally()

    ctx.run_task(label_, fn, done, on_finally=fin, **kwargs)


def guardando() -> bool:
    return _guardando[0] > 0


def esperar_guardado(limite: float = 600.0) -> None:
    fin = time.monotonic() + limite
    while guardando() and time.monotonic() < fin:
        QApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
        time.sleep(0.01)


def revisar_fin(hecho: dict, al_terminar) -> None:
    """Desde ``on_finally``: si la tarea no terminó bien (falló o se canceló), avisa ``al_terminar(False)``."""
    QTimer.singleShot(0, lambda: None if hecho.get("ok") else al_terminar(False))


def no_se_pudo_guardar(parent, detalle: str | None = None) -> None:
    """La pantalla no pudo guardar desde la pregunta de «Cambios sin guardar»: que la persona sepa qué hacer."""
    _preguntar(parent, "No se pudo guardar", "Los cambios no se pudieron guardar desde aquí. Guárdalos desde "
               "esta pantalla y vuelve a intentarlo.", [("ok", "Entendido", _ACEPTAR)], defecto="ok", icono="aviso",
               detalle=detalle)


def guardar_y_esperar(page) -> bool:
    """Guarda (en el mismo archivo si se puede; si no, «Guardar como…») y espera a que termine.
    True si quedó guardado.  Ante cualquier problema devuelve False (y lo explica): una pantalla que no sabe
    guardar así nunca debe dejar cerrar la ventana con el trabajo sin guardar."""
    fn = getattr(page, "guardar", None)
    if not callable(fn):
        no_se_pudo_guardar(page)
        return False
    estado: dict = {}
    loop = QEventLoop()

    def fin(ok):
        estado["ok"] = bool(ok)
        loop.quit()

    try:
        fn(al_terminar=fin)
    except Exception as exc:  # noqa: BLE001 — p. ej. una pantalla cuyo guardar() no acepta al_terminar
        traceback.print_exc()
        no_se_pudo_guardar(page, f"{type(exc).__name__}: {exc}")
        return False
    if "ok" not in estado and not ctx.busy and not guardando():
        QApplication.processEvents()          # un aviso pendiente (QTimer.singleShot) de quien guardó
    if "ok" not in estado:
        if not ctx.busy and not guardando():
            return False                      # no quedó nada en curso que vaya a avisar: no se guardó
        loop.exec()
    return estado.get("ok", False)


# --------------------------------------------------------------------------
# El mismo archivo abierto en otra pantalla
# --------------------------------------------------------------------------

def _pantallas():
    win = ctx.window
    pages = getattr(win, "pages", {}) if win is not None else {}
    return pages.get("viewer"), pages.get("forms"), pages.get("organize")


def abierto_en(ruta: str, excepto=None, con_cambios: bool = False) -> str | None:
    """Título de la pantalla (que no sea ``excepto``) que tiene ese archivo abierto; con ``con_cambios``,
    solo si además tiene cambios sin guardar."""
    k = _clave(ruta)
    v, f, o = _pantallas()
    if (v is not None and v is not excepto and v.canvas.path and _clave(v.canvas.path) == k
            and (not con_cambios or v.has_unsaved())):
        return TITULOS["viewer"]
    if (f is not None and f is not excepto and getattr(f, "path", None) and _clave(f.path) == k
            and (not con_cambios or f.has_unsaved())):
        return TITULOS["forms"]
    if (o is not None and o is not excepto and any(_clave(x.path) == k for x in o.sources)
            and (not con_cambios or o.has_unsaved())):
        return TITULOS["organize"]
    return None


def liberar(ruta: str, excepto=None) -> list:
    """Cierra el archivo en las pantallas que lo tengan abierto (salvo ``excepto``), para que Windows deje
    reemplazarlo.  Devuelve cómo volver a abrirlo en cada una (para ``reabrir``)."""
    k = _clave(ruta)
    volver = []
    v, f, o = _pantallas()
    if v is not None and v is not excepto and v.canvas.path and _clave(v.canvas.path) == k:
        ruta_v = v.canvas.path
        v.canvas.close_doc()
        volver.append(lambda: v.load(ruta_v, confirm=False))
    if f is not None and f is not excepto and getattr(f, "path", None) and _clave(f.path) == k:
        ruta_f = f.path
        f._cerrar_doc()
        volver.append(lambda: f.load(ruta_f, confirm=False))
    if o is not None and o is not excepto and any(_clave(x.path) == k for x in o.sources):
        rutas = [x.path for x in o.sources]
        o._close_docs()
        volver.append(lambda: o.load_files(rutas))
    return volver


def reabrir(volver) -> None:
    for fn in volver or ():
        try:
            fn()
        except Exception:  # noqa: BLE001 — volver a abrirlo es una cortesía, no debe fallar
            pass


def preparar_reemplazo(page, ruta: str) -> list | None:
    """Antes de que ``page`` reemplace ``ruta``: si otra pantalla lo tiene abierto con cambios sin guardar,
    lo avisa y devuelve None (no se guarda y nada se pierde); si no, lo cierra en esas pantallas y devuelve
    cómo volver a abrirlo cuando termine el guardado."""
    otra = abierto_en(ruta, excepto=page, con_cambios=True)
    if otra:
        _preguntar(page, "Guardar en el mismo archivo",
                   f"Este PDF también está abierto en «{otra}» con cambios sin guardar.",
                   [("ok", "Entendido", _ACEPTAR)], defecto="ok", icono="aviso",
                   detalle=f"Guarda o descarta primero esos cambios en «{otra}», o usa «Guardar como…» para "
                           "guardar estos en otro archivo.")
        return None
    return liberar(ruta, excepto=page)


# --------------------------------------------------------------------------
# Autoguardado
# --------------------------------------------------------------------------

class Autoguardado(QObject):
    """Autoguarda cada cierto tiempo el visor, los formularios y el organizador si tienen cambios nuevos,
    en segundo plano (``ctx.run_silent``), sin cuadros y sin quitarle el foco a lo que se esté escribiendo."""

    HERRAMIENTAS = ("viewer", "forms", "organize")

    def __init__(self, ventana):
        super().__init__(ventana)
        self.ventana = ventana
        self.ajustes = ajustes_path()
        self.sesion = guardado.nueva_sesion()
        self.carpeta: Path | None = None
        self.firmas: dict[str, object] = {}
        self.guardados: set[str] = set()
        self.duracion: dict[str, float] = {}
        self.contador: dict[str, int] = defaultdict(int)
        self.actividad = time.monotonic()
        self.tarea = None
        self.en_curso: str | None = None
        self.borrar_luego: set[str] = set()
        self.cola: list[str] = []
        self.ultimo_error: str | None = None
        self.cerrada = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.revisar)
        self.reintento = QTimer(self)
        self.reintento.setSingleShot(True)
        self.reintento.timeout.connect(self.revisar)
        self._enganchar()
        self.aplicar_intervalo()

    # ---- enganches
    def _enganchar(self):
        pages = getattr(self.ventana, "pages", {})
        v = pages.get("viewer")
        if v is not None:
            v.canvas.edits_changed.connect(lambda: self._cambio("viewer"))
            self._vigilar("viewer", v.canvas, v.canvas.viewport())
        f = pages.get("forms")
        if f is not None and hasattr(f, "canvas"):
            self._vigilar("forms", f.canvas, f.canvas.viewport())
        o = pages.get("organize")
        if o is not None and hasattr(o, "grid"):
            self._vigilar("organize", o.grid, o.grid.viewport())

    def _vigilar(self, herr: str, widget, viewport) -> None:
        """Cuenta como actividad lo que se haga en el lienzo y en los recuadros que se le pongan encima (el
        de escribir una nota, por ejemplo, vive dentro del viewport)."""
        for w in (widget, viewport):
            w.setProperty("autoguardado_herr", herr)
            w.installEventFilter(self)
        viewport.setProperty("autoguardado_raiz", True)

    def _vigilar_hijos(self, raiz) -> None:
        """Los widgets nuevos dentro del viewport (ya construidos: se llama en el siguiente ciclo)."""
        try:
            herr = raiz.property("autoguardado_herr")
            for w in raiz.findChildren(QWidget):
                if not w.property("autoguardado_editor"):
                    w.setProperty("autoguardado_editor", True)
                    w.setProperty("autoguardado_herr", herr)
                    w.installEventFilter(self)
        except RuntimeError:                   # ya se cerró
            pass

    def eventFilter(self, obj, ev):  # noqa: N802 — método de Qt
        t = ev.type()
        if t in (QEvent.Type.KeyPress, QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease,
                 QEvent.Type.Wheel) or (t == QEvent.Type.MouseMove and ev.buttons() != Qt.MouseButton.NoButton):
            self.actividad = time.monotonic()
            if t == QEvent.Type.KeyPress and obj.property("autoguardado_editor"):
                # se escribe en un recuadro dentro del lienzo: lo escrito es trabajo nuevo para autoguardar
                self.contador[obj.property("autoguardado_herr")] += 1
        elif t == QEvent.Type.ChildAdded and obj.property("autoguardado_raiz"):
            # no se toca el hijo aquí: todavía se está construyendo
            QTimer.singleShot(0, lambda o=obj: self._vigilar_hijos(o))
        return False

    def _cambio(self, herr: str):
        self.contador[herr] += 1
        self.actividad = time.monotonic()

    def aplicar_intervalo(self):
        s = intervalo()
        if s and not self.cerrada:
            self.timer.start(s * 1000)
        else:
            self.timer.stop()

    # ---- estado de cada herramienta
    def _pagina(self, herr):
        return getattr(self.ventana, "pages", {}).get(herr)

    def estado(self, herr: str):
        """(hay cambios, firma de esos cambios).  La firma cambia cada vez que el trabajo cambia."""
        p = self._pagina(herr)
        if p is None:
            return False, None
        if herr == "viewer":
            c = p.canvas
            if c.doc is None or not c.path or not c.has_changes():
                return False, None
            edits, _ = c.collect_edits()
            return True, (_clave(c.path), edits, self.contador["viewer"], len(c.undo_stack))
        if herr == "forms":
            if getattr(p, "doc", None) is None or not p.path or not p.has_unsaved():
                return False, None
            sellos, _ = p._recoger_sellos()
            return True, (_clave(p.path), copy.deepcopy(p.actuales), sellos, len(p.undo_stack))
        if herr == "organize":
            if not p.has_unsaved():
                return False, None
            return True, ([s.path for s in p.sources], p.snapshot())
        return False, None

    # ---- ciclo
    def revisar(self, solo=None):
        if self.cerrada or self.tarea is not None or not intervalo():
            return
        if ctx.busy:
            self._reintentar(5.0)
            return
        pendientes = []
        for herr in (solo or self.HERRAMIENTAS):
            try:
                hay, firma = self.estado(herr)
            except Exception:  # noqa: BLE001 — el autoguardado nunca debe romper la pantalla
                continue
            if not hay:
                if herr in self.guardados:
                    self._borrar(herr)
                continue
            if firma == self.firmas.get(herr):
                continue
            pendientes.append((herr, firma))
        if not pendientes:
            return
        espera = max(5.0 if self.duracion.get(h, 0.0) > 1.0 else 2.0 for h, _ in pendientes)
        quieto = time.monotonic() - self.actividad
        if quieto < espera:
            self._reintentar(espera - quieto + 0.1)
            return
        herr, firma = pendientes[0]
        self.cola = [h for h, _ in pendientes[1:]]
        self._lanzar(herr, firma)

    def _reintentar(self, segundos: float):
        if not self.reintento.isActive():
            self.reintento.start(int(segundos * 1000))

    def ahora(self):
        """Autoguarda ya lo que tenga cambios (sin esperar el intervalo ni la inactividad)."""
        self.actividad = 0.0
        self.revisar()

    def _preparar_carpeta(self) -> Path:
        if self.carpeta is None:
            self.carpeta = guardado.carpeta_escritura(guardado.CARPETA_RECUPERACION, self.ajustes)
        guardado.registrar_sesion(self.carpeta, self.sesion)
        return self.carpeta

    def _lanzar(self, herr: str, firma):
        try:
            carpeta = self._preparar_carpeta()
        except (core.UserError, OSError) as exc:
            self.ultimo_error = str(exc)
            self.firmas[herr] = firma
            return
        p = self._pagina(herr)
        sid = self.sesion["id"]
        if herr == "organize":
            n = p.grid.count()
            datos = guardado.manifiesto("organize", p.sources[0].path if p.sources else "", sid,
                                        descripcion=f"{guardado.plural(n, 'página', 'páginas')} sin guardar",
                                        fuentes=[s.path for s in p.sources], paginas=p.snapshot())
            try:
                guardado.guardar_manifiesto(carpeta, sid, "organize", datos)
            except (core.UserError, OSError) as exc:
                self.ultimo_error = str(exc)
            else:
                self.guardados.add(herr)
            self.firmas[herr] = firma
            self._siguiente()
            return
        if herr == "viewer":
            c = p.canvas
            edits, _ = c.collect_edits()
            extra = extensiones.autoguardar_visor(p)        # sin cerrar lo que se esté escribiendo
            n = len(edits) + sum(1 for a, _i in c.undo_stack if a == "ext")
            datos = guardado.manifiesto("viewer", c.path, sid, pagina=c.current_page,
                                        descripcion=f"{guardado.plural(max(n, 1), 'cambio', 'cambios')} sin guardar",
                                        protegido=bool(c.password))
            kwargs = dict(tarea=core.apply_edits, path=c.path, edits=edits, password=c.password, extra=extra,
                          incremental=not guardado.borra_contenido(edits) and not getattr(extra, "borra", False))
        else:
            sellos, _ = p._recoger_sellos()
            nombres = {p.por_id[c].nombre for c, v in p.actuales.items()
                       if c in p.por_id and v != p.originales.get(c)}
            partes = []
            if nombres:
                partes.append(guardado.plural(len(nombres), "campo", "campos"))
            if sellos:
                partes.append(guardado.plural(len(sellos), "elemento", "elementos"))
            datos = guardado.manifiesto("forms", p.path, sid, pagina=p.canvas.current_page,
                                        descripcion=(" y ".join(partes) or "Cambios") + " sin guardar",
                                        protegido=bool(p.password))
            kwargs = dict(tarea=formularios.rellenar, path=p.path, valores=p.valores(), sellos=sellos,
                          aplanar=False, password=p.password, incremental=True)
        self.en_curso = herr

        def ok(res):
            self.tarea, self.en_curso = None, None
            self.firmas[herr] = firma
            self.guardados.add(herr)
            self.duracion[herr] = float((res or {}).get("segundos", 0.0))
            self.ultimo_error = None
            if herr in self.borrar_luego:
                self.borrar_luego.discard(herr)
                self._borrar(herr)
            self._siguiente()

        def mal(mensaje):
            self.tarea, self.en_curso = None, None
            self.ultimo_error = mensaje
            self.firmas[herr] = firma           # no se reintenta enseguida: en el próximo turno, si cambia
            if herr in self.borrar_luego:
                self.borrar_luego.discard(herr)
                self._borrar(herr)
            self._siguiente()

        self.tarea = ctx.run_silent(guardado.autoguardar_pdf, ok, mal, carpeta=str(carpeta), sesion_id=sid,
                                    herramienta=herr, datos=datos, **kwargs)

    def _siguiente(self):
        if self.cola and not self.cerrada:
            cola, self.cola = self.cola, []
            QTimer.singleShot(0, lambda: self.revisar(solo=cola))

    def _borrar(self, herr: str):
        self.firmas.pop(herr, None)
        self.guardados.discard(herr)
        if self.carpeta is not None:
            guardado.borrar_trabajo(self.carpeta, self.sesion["id"], herr)

    def limpiar(self, herr: str):
        """El trabajo de ``herr`` quedó guardado o se descartó: ya no hay nada que recuperar."""
        if self.en_curso == herr:
            self.borrar_luego.add(herr)
        self._borrar(herr)

    def esperar(self, limite: float = 30.0):
        fin = time.monotonic() + limite
        while self.tarea is not None and time.monotonic() < fin:
            QApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
            time.sleep(0.01)

    def cerrar(self):
        """Cierre normal de la aplicación: se borra todo lo de esta sesión."""
        self.cerrada = True
        self.timer.stop()
        self.reintento.stop()
        if self.tarea is not None:
            self.tarea.cancel_requested = True
            self.esperar()
        if self.carpeta is not None:
            guardado.cerrar_sesion(self.carpeta, self.sesion["id"])


def gestor(widget=None) -> Autoguardado | None:
    w = widget.window() if widget is not None else ctx.window
    g = getattr(w, "_autoguardado", None)
    if g is None and ctx.window is not None:
        g = getattr(ctx.window, "_autoguardado", None)
    return g


def trabajo_guardado(page, herr: str) -> None:
    g = gestor(page)
    if g is not None:
        g.limpiar(herr)


trabajo_descartado = trabajo_guardado


def aplicar_intervalo_todas() -> None:
    for w in QApplication.topLevelWidgets():
        g = getattr(w, "_autoguardado", None)
        if g is not None:
            g.aplicar_intervalo()


# --------------------------------------------------------------------------
# Recuperación
# --------------------------------------------------------------------------

def recuperar(trabajo: guardado.Trabajo, parent=None) -> bool:
    """Recupera un trabajo: el PDF del visor o de los formularios se guarda donde la persona diga (nunca
    encima del original) y se abre en su herramienta; el organizador reabre sus archivos y restaura la
    cuadrícula, con los cambios pendientes."""
    win = ctx.window
    if win is None or trabajo.herramienta not in getattr(win, "pages", {}):
        return False
    page = win.pages[trabajo.herramienta]
    if trabajo.herramienta == "organize":
        win.navigate("organize")
        faltan = page.restaurar([str(x) for x in trabajo.manifiesto.get("fuentes") or []],
                                trabajo.manifiesto.get("paginas") or [])
        if faltan is None:
            return False
        guardado.descartar(trabajo)
        if faltan:
            QMessageBox.information(parent or win, "Recuperar trabajo",
                                    "Estos archivos ya no están en su carpeta y se omitieron sus páginas:\n"
                                    + "\n".join(f"• {Path(x).name}" for x in faltan))
        return True
    while True:
        destino = ask_save(parent or win, guardado.sugerido_recuperacion(trabajo, ctx.last_dir),
                           title="Guardar el trabajo recuperado")
        if not destino:
            return False
        if trabajo.documento and _clave(destino) == _clave(trabajo.documento):
            QMessageBox.information(parent or win, "Recuperar trabajo",
                                    "Para no perder el original, guarda el trabajo recuperado con otro nombre.")
            continue
        break
    try:
        guardado.recuperar_pdf(trabajo, destino)
    except core.UserError as exc:
        QMessageBox.warning(parent or win, "No se pudo recuperar", str(exc))
        return False
    clave = ctx.passwords.get(trabajo.documento) if trabajo.documento else None
    if clave:                                  # tiene la misma contraseña que el original
        ctx.passwords.set(destino, clave)
    win.navigate(trabajo.herramienta)
    if page.load(destino) and trabajo.pagina:
        canvas = getattr(page, "canvas", None)
        if canvas is not None and trabajo.pagina < canvas.page_count:
            canvas.go_to_page(trabajo.pagina)
    return True


class DialogoRecuperar(QDialog):
    """«Recuperar trabajo sin guardar»: un renglón por documento, con «Recuperar» y «Descartar»."""

    def __init__(self, parent, trabajos: list[guardado.Trabajo]):
        super().__init__(parent)
        self.setWindowTitle("Recuperar trabajo sin guardar")
        self.setMinimumWidth(560)
        self.trabajos = list(trabajos)
        self.filas: dict[int, QFrame] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(12)
        lay.addWidget(label("RonyPDF se cerró sin que guardaras estos cambios", "SectionTitle"))
        lay.addWidget(label("Puedes recuperarlos ahora. El documento original no ha cambiado: lo recuperado se "
                            "guarda con otro nombre.", "Hint", wrap=True))
        self.caja = QVBoxLayout()
        self.caja.setSpacing(8)
        for k, t in enumerate(self.trabajos):
            self.caja.addWidget(self._fila(k, t))
        lay.addLayout(self.caja)
        lay.addWidget(label(guardado.TEXTO_PRIVACIDAD, "Hint", wrap=True))
        abajo = QHBoxLayout()
        self.b_todo = QPushButton("Recuperar todo")
        self.b_todo.setObjectName("Primary")
        self.b_todo.clicked.connect(self.recuperar_todo)
        self.b_descartar = QPushButton("Descartar todo")
        self.b_descartar.setObjectName("Secondary")
        self.b_descartar.clicked.connect(self.descartar_todo)
        self.b_luego = QPushButton("Decidir después")
        self.b_luego.setObjectName("Secondary")
        self.b_luego.clicked.connect(self.reject)
        abajo.addWidget(self.b_descartar)
        abajo.addStretch(1)
        abajo.addWidget(self.b_luego)
        abajo.addWidget(self.b_todo)
        lay.addLayout(abajo)

    def _fila(self, k: int, t: guardado.Trabajo) -> QFrame:
        fila = QFrame()
        fila.setObjectName("Card")
        hl = QHBoxLayout(fila)
        hl.setContentsMargins(14, 10, 10, 10)
        hl.addWidget(PdfThumb(28, 36))
        col = QVBoxLayout()
        col.setSpacing(1)
        nombre = label(t.nombre, "CardTitle")
        nombre.setToolTip(t.documento)
        col.addWidget(nombre)
        col.addWidget(label(f"{t.titulo_herramienta} · {guardado.fecha_amable(t.fecha)} · {t.descripcion}", "Hint",
                            wrap=True))
        hl.addLayout(col, 1)
        b_rec = QPushButton("Recuperar")
        b_rec.setObjectName("Secondary")
        b_rec.clicked.connect(lambda _=False, i=k: self.recuperar(i))
        b_des = QPushButton("Descartar")
        b_des.setObjectName("Flat")
        b_des.clicked.connect(lambda _=False, i=k: self.descartar(i))
        hl.addWidget(b_rec)
        hl.addWidget(b_des)
        self.filas[k] = fila
        return fila

    def _quitar(self, k: int):
        fila = self.filas.pop(k, None)
        if fila is not None:
            fila.hide()
            fila.deleteLater()
        if not self.filas:
            self.accept()

    def recuperar(self, k: int):
        if recuperar(self.trabajos[k], self):
            self._quitar(k)

    def descartar(self, k: int):
        guardado.descartar(self.trabajos[k])
        self._quitar(k)

    def recuperar_todo(self):
        for k in list(self.filas):
            if not recuperar(self.trabajos[k], self):
                return                        # se canceló uno: el resto queda para decidir
            self._quitar(k)

    def descartar_todo(self):
        n = len(self.filas)
        r, _ = _preguntar(self, "Descartar todo", f"Se borrará el trabajo sin guardar de "
                          f"{guardado.plural(n, 'documento', 'documentos')}. No se puede deshacer.",
                          [("si", "Descartar todo", _DESTRUCTIVO), ("cancelar", "Cancelar", _RECHAZAR)],
                          defecto="cancelar", icono="aviso")
        if r != "si":
            return
        for k in list(self.filas):
            guardado.descartar(self.trabajos[k])
            self._quitar(k)


def trabajos_pendientes(ajustes: str | None = None) -> list[guardado.Trabajo]:
    try:
        return guardado.huerfanos(carpetas_recuperacion(ajustes))
    except Exception:  # noqa: BLE001 — revisar la recuperación nunca debe impedir abrir la aplicación
        return []


def ofrecer_recuperacion(ventana) -> None:
    """Si una sesión anterior terminó de forma inesperada, ofrece recuperar su trabajo.  Si no hay nada, no
    aparece nada."""
    g = getattr(ventana, "_autoguardado", None)
    trabajos = trabajos_pendientes(g.ajustes if g is not None else None)
    if trabajos:
        DialogoRecuperar(ventana, trabajos).exec()


# --------------------------------------------------------------------------
# Enganches con la ventana principal (ver extensiones.py)
# --------------------------------------------------------------------------

def al_iniciar(ventana) -> None:
    g = Autoguardado(ventana)
    ventana._autoguardado = g
    t = QTimer(g)
    t.setSingleShot(True)
    t.timeout.connect(lambda: ofrecer_recuperacion(ventana))
    t.start(700)                  # con la ventana ya a la vista


def al_cerrar(ventana) -> None:
    g = getattr(ventana, "_autoguardado", None)
    if g is not None:
        g.cerrar()


# --------------------------------------------------------------------------
# Página «Respaldo y recuperación»
# --------------------------------------------------------------------------

R_RUTA = Qt.ItemDataRole.UserRole
R_IDX = Qt.ItemDataRole.UserRole + 1


def _contar_pendientes(n: int) -> None:
    """Contador rojo de «Respaldo y recuperación» en el menú y en Inicio: trabajo sin guardar por recuperar."""
    win = ctx.window
    for parte in (getattr(win, "sidebar", None), getattr(win, "home", None)):
        if parte is not None and callable(getattr(parte, "set_count", None)):
            try:
                parte.set_count("backup", n or None)
            except Exception:  # noqa: BLE001 — un contador nunca debe estorbar
                pass


def _boton(texto: str, nombre: str = "Secondary", glifo: str | None = None, cb=None, tam: str | None = "sm"):
    b = QPushButton(texto)
    b.setObjectName(nombre)
    if tam:
        b.setProperty("tam", tam)
    if glifo:
        b.setIcon(icon(glifo, tema.color("danger" if nombre == "Danger" else "ink-2"), size=16))
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if cb:
        b.clicked.connect(cb)
    return b


class _Elidida(QLabel):
    """Etiqueta de una línea que se acorta con «…» si no cabe (la tarjeta nunca ensancha la página)."""

    def __init__(self, texto: str, nombre: str, modo=Qt.TextElideMode.ElideRight):
        super().__init__(texto)
        self.setObjectName(nombre)
        self.setToolTip(texto)
        self.modo = modo
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def paintEvent(self, e):  # noqa: N802
        p = QPainter(self)
        p.setPen(self.palette().color(self.foregroundRole()))
        p.setFont(self.font())
        r = self.contentsRect()
        p.drawText(r, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                   self.fontMetrics().elidedText(self.text(), self.modo, r.width()))
        p.end()


class _TarjetaTrabajo(QFrame):
    """Un trabajo sin guardar como tarjeta de archivo: miniatura, nombre, de dónde viene y cuándo, y sus dos
    acciones («Recuperar» y «Descartar»)."""

    def __init__(self, t: guardado.Trabajo, recuperar_cb, descartar_cb):
        super().__init__()
        self.setObjectName("Card")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        h = QHBoxLayout(self)
        h.setContentsMargins(10, 10, 12, 10)
        h.setSpacing(12)
        h.addWidget(PdfThumb(34, 44))
        col = QVBoxLayout()
        col.setSpacing(1)
        nombre = _Elidida(t.nombre, "BodyStrong", Qt.TextElideMode.ElideMiddle)
        nombre.setToolTip(t.documento)
        col.addWidget(nombre)
        col.addWidget(_Elidida(f"{t.titulo_herramienta} · autoguardado {guardado.fecha_amable(t.fecha)}"
                               + (f" · {t.descripcion}" if t.descripcion else ""), "Caption"))
        h.addLayout(col, 1)
        h.addWidget(_boton("Recuperar", cb=recuperar_cb))
        h.addWidget(_boton("Descartar", "Ghost", cb=descartar_cb))


class _TarjetaCopia(QStyledItemDelegate):
    """Cada copia de respaldo como tarjeta (``rp-file``): miniatura, documento, cuándo se hizo y su tamaño.
    La elegida va en el color de la categoría; al pasar el puntero aparece «Eliminar» (papelera)."""

    ALTO = 62

    def __init__(self, tabla: QTreeWidget, eliminar_cb):
        super().__init__(tabla)
        self.tabla, self.eliminar_cb = tabla, eliminar_cb

    def sizeHint(self, option, index):  # noqa: N802
        return QSize(max(200, option.rect.width()), self.ALTO)

    def createEditor(self, *args):  # noqa: N802
        return None

    @staticmethod
    def _papelera(r: QRectF) -> QRectF:
        return QRectF(r.right() - 42, r.center().y() - 16, 32, 32)

    def paint(self, p, option, index):
        it = self.tabla.itemFromIndex(index)
        if it is None:
            return
        t = tema.tokens()
        ton = tema.tonos(cat_de(self.tabla))
        r = QRectF(option.rect).adjusted(0.5, 3.5, -0.5, -3.5)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        sel = bool(option.state & QStyle.StateFlag.State_Selected)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(ton["c"] if sel else t["line-strong"] if hover else t["line"]), 1.5 if sel else 1))
        p.setBrush(QColor(ton["band"] if sel else t["surface"]))
        p.drawRoundedRect(r, 12, 12)
        pintar_miniatura(p, QRectF(r.left() + 9, r.center().y() - 22, 34, 44))
        x = r.left() + 9 + 34 + 12
        derecha = r.right() - (50 if hover else 14)
        tam = it.text(2)
        fc = tema.fuente("caption")
        tw = QFontMetrics(fc).horizontalAdvance(tam)
        p.setFont(fc)
        p.setPen(QColor(t["ink-3"]))
        p.drawText(QRectF(derecha - tw, r.top(), tw, r.height()), Qt.AlignmentFlag.AlignVCenter, tam)
        ancho = max(40.0, derecha - tw - 16 - x)
        fn = tema.fuente("body-strong")
        p.setFont(fn)
        p.setPen(QColor(t["ink"]))
        p.drawText(QRectF(x, r.top() + 10, ancho, 20), Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(fn).elidedText(it.text(0), Qt.TextElideMode.ElideMiddle, int(ancho)))
        p.setFont(fc)
        p.setPen(QColor(t["ink-2"]))
        p.drawText(QRectF(x, r.top() + 30, ancho, 16), Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(fc).elidedText(it.text(1), Qt.TextElideMode.ElideRight, int(ancho)))
        if hover:
            q = self._papelera(r)
            p.drawPixmap(QPointF(q.center().x() - 9, q.center().y() - 9), icons.pixmap("trash", t["ink-2"], 18, 1.7))
        p.restore()

    def editorEvent(self, event, model, option, index):  # noqa: N802
        if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            r = QRectF(option.rect).adjusted(0.5, 3.5, -0.5, -3.5)
            if self._papelera(r).contains(event.position()):
                it = self.tabla.itemFromIndex(index)
                if it is not None:
                    self.tabla.setCurrentItem(it)
                    QTimer.singleShot(0, self.eliminar_cb)
                return True
        return super().editorEvent(event, model, option, index)


class RespaldoPage(ToolPage):
    """Plantilla 2.0: los ajustes del guardado seguro en pasos (panel) y, en el área principal, el trabajo sin
    guardar y las copias de respaldo como tarjetas.  El principal restaura la copia elegida."""

    key = "backup"
    title = "Respaldo y recuperación"
    subtitle = "Vuelve a una versión anterior de tus PDF y recupera lo que no alcanzaste a guardar."
    icon_name = "backup"
    action_text = "Restaurar versión"

    def build(self):
        self._filtro: str | None = None
        self._respaldos: list[guardado.Respaldo] = []
        self._trabajos: list[guardado.Trabajo] = []
        self._tarjetas: list[QWidget] = []

        # ---- panel: los ajustes, en pasos
        s1 = self.step("¿Cada cuánto autoguardar?", "Por si el equipo se apaga o RonyPDF se cierra de golpe.")
        self.cmb_intervalo = QComboBox()
        for seg, texto in INTERVALOS:
            self.cmb_intervalo.addItem(texto, seg)
        self.cmb_intervalo.setToolTip("Cada cuánto se guarda en silencio lo que no has guardado en «Ver y editar», "
                                      "«Formularios y firma» y «Organizar páginas»")
        s1.addWidget(self.cmb_intervalo)

        s2 = self.step("¿Guardar copias de respaldo?")
        self.chk_respaldo = SwitchRow("Copia antes de reemplazar un archivo",
                                      "Cada «Guardar» deja antes la versión anterior.")
        s2.addWidget(self.chk_respaldo)
        self.caja_retencion = QWidget()
        rejilla = QGridLayout(self.caja_retencion)
        rejilla.setContentsMargins(0, 4, 0, 0)
        rejilla.setHorizontalSpacing(12)
        rejilla.setVerticalSpacing(6)
        rejilla.addWidget(label("Copias por PDF", "Label", wrap=True), 0, 0)
        rejilla.addWidget(label("Por cuántos días", "Label", wrap=True), 0, 1)
        self.sp_copias = QSpinBox()
        self.sp_copias.setRange(1, 50)
        self.sp_dias = QSpinBox()
        self.sp_dias.setRange(1, 3650)
        for sp in (self.sp_copias, self.sp_dias):
            sp.setMinimumWidth(84)               # caben los dos lado a lado en el panel angosto
        rejilla.addWidget(self.sp_copias, 1, 0)
        rejilla.addWidget(self.sp_dias, 1, 1)
        rejilla.setColumnStretch(0, 1)
        rejilla.setColumnStretch(1, 1)
        s2.addWidget(self.caja_retencion)

        s3 = self.step("Al guardar")
        self.chk_confirmar = SwitchRow("Preguntar antes de guardar en el mismo archivo",
                                       "La primera vez de cada documento.")
        s3.addWidget(self.chk_confirmar)
        # dónde quedan las copias: debajo de los pasos, a la vista
        self.privacidad = Note(guardado.TEXTO_PRIVACIDAD, "ok", glyph="shield")
        self.body.addWidget(self.privacidad)

        # ---- área principal: lo recuperado y las copias, como tarjetas
        principal = QWidget()
        vl = QVBoxLayout(principal)
        vl.setContentsMargins(16, 14, 16, 8)
        vl.setSpacing(8)

        self.caja_trabajo = QWidget()
        vt = QVBoxLayout(self.caja_trabajo)
        vt.setContentsMargins(0, 0, 0, 10)
        vt.setSpacing(6)
        cab = QHBoxLayout()
        cab.setSpacing(8)
        cab.addWidget(label("Trabajo sin guardar", "StepTitle"))
        self.chip_trabajo = Chip("", "warn")
        cab.addWidget(self.chip_trabajo)
        cab.addStretch(1)
        vt.addLayout(cab)
        self.trabajo_lb = label("", "Help", wrap=True)
        vt.addWidget(self.trabajo_lb)
        self.lista_trabajo = QVBoxLayout()
        self.lista_trabajo.setSpacing(8)
        vt.addLayout(self.lista_trabajo)
        vl.addWidget(self.caja_trabajo)

        cab2 = QHBoxLayout()
        cab2.setSpacing(4)
        cab2.addWidget(label("Copias de respaldo", "StepTitle"), 1)
        self.b_todas = _boton("Ver todas", "Ghost", cb=lambda: self.filtrar(None))
        cab2.addWidget(self.b_todas)
        self.b_borrar = _boton("Borrar todas", "Danger", "trash", self.borrar_todas)
        self.b_borrar.setToolTip("Borra las copias de respaldo de todos los documentos")
        cab2.addWidget(self.b_borrar)
        vl.addLayout(cab2)
        self.filtro_lb = label("", "Help", wrap=True)
        vl.addWidget(self.filtro_lb)

        self.tabla = QTreeWidget()
        self.tabla.setObjectName("FileCards")
        self.tabla.setColumnCount(3)
        self.tabla.setHeaderLabels(["Documento", "Fecha", "Tamaño"])
        self.tabla.setHeaderHidden(True)
        self.tabla.setRootIsDecorated(False)
        self.tabla.setIndentation(0)
        self.tabla.setUniformRowHeights(True)
        self.tabla.setColumnHidden(1, True)
        self.tabla.setColumnHidden(2, True)
        self.tabla.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tabla.setMouseTracking(True)
        self.tabla.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.tabla.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.tabla.setItemDelegate(_TarjetaCopia(self.tabla, self.eliminar))
        self.tabla.itemSelectionChanged.connect(self._botones)
        self.tabla.itemDoubleClicked.connect(lambda *_: self.abrir_copia())
        vl.addWidget(self.tabla, 1)

        self.vacio = QWidget()
        ev = QVBoxLayout(self.vacio)
        ev.addStretch(1)
        ev.addWidget(GlyphTile("backup", tema.TILE_XL, soft=True), 0, Qt.AlignmentFlag.AlignHCenter)
        ev.addSpacing(6)
        t = label("Todavía no hay copias de respaldo", "DropTitle")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ev.addWidget(t)
        self.vacio_lb = label("Se crean solas cada vez que guardas un PDF sobre sí mismo con «Guardar».", "Hint",
                              wrap=True)
        self.vacio_lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ev.addWidget(self.vacio_lb)
        ev.addStretch(2)
        vl.addWidget(self.vacio, 1)
        self.set_main(principal)

        # ---- encabezado y barra de acción
        self.b_carpeta = _boton("Abrir carpeta", glifo="folder_open", cb=self.abrir_carpeta, tam=None)
        self.b_carpeta.setToolTip("Abre la carpeta de las copias de respaldo en el explorador")
        self.set_header_widget(self.b_carpeta)
        self.b_abrir = self.add_secondary("Abrir la copia", self.abrir_copia)
        self.b_restaurar = self.action
        self.footer_info.setWordWrap(True)

        self._cargar_opciones()
        self.cmb_intervalo.currentIndexChanged.connect(self._opciones_cambiaron)
        self.chk_respaldo.toggled.connect(self._opciones_cambiaron)
        self.sp_copias.valueChanged.connect(self._opciones_cambiaron)
        self.sp_dias.valueChanged.connect(self._opciones_cambiaron)
        self.chk_confirmar.toggled.connect(self._opciones_cambiaron)
        self.actualizar()

    # ---- base
    def ready(self) -> bool:
        return hasattr(self, "tabla") and self._seleccion() is not None

    def run(self):
        self.restaurar()

    def refresh(self):
        super().refresh()
        if not hasattr(self, "tabla"):
            return
        chips = []                    # el trabajo por recuperar ya se ve arriba de la lista y en el menú
        if self._respaldos:
            docs = len({r.clave for r in self._respaldos})
            chips += [f"{guardado.plural(len(self._respaldos), 'copia', 'copias')} de "
                      f"{guardado.plural(docs, 'documento', 'documentos')}",
                      core.human_size(sum(r.tamano for r in self._respaldos))]
        if self._seleccion() is not None:
            texto = "Reemplaza el archivo o guárdala con otro nombre."
        elif self._respaldos:
            texto = "Elige una copia para restaurarla."
        else:
            texto = ""
        self.set_summary(chips, texto)

    def accept_files(self, paths: list[str]) -> None:
        pdfs = [p for p in paths if str(p).lower().endswith(".pdf")]
        self.filtrar(pdfs[0] if pdfs else None)

    def showEvent(self, e):
        super().showEvent(e)
        self.actualizar()

    def filtrar(self, ruta: str | None):
        self._filtro = ruta
        self.actualizar()

    # ---- listas
    def actualizar(self):
        self._respaldos = guardado.listar_respaldos(carpetas_respaldos())
        visibles = self._respaldos
        if self._filtro:
            clave = guardado.huella(self._filtro)
            visibles = [r for r in self._respaldos if r.clave == clave]
            self.filtro_lb.setText(f"Versiones anteriores de «{Path(self._filtro).name}»"
                                   + ("" if visibles else ": todavía no tiene copias de respaldo."))
        else:
            self.filtro_lb.setText(
                "De la más nueva a la más vieja. Doble clic para abrir una copia en el visor."
                if self._respaldos else "")
        self.filtro_lb.setVisible(bool(self.filtro_lb.text()))
        self.b_todas.setVisible(bool(self._filtro))
        self.b_borrar.setVisible(bool(self._respaldos))
        self.tabla.clear()
        for r in sorted(visibles, key=lambda r: (r.documento.lower(), -r.fecha.timestamp())):
            it = QTreeWidgetItem([r.documento, guardado.fecha_amable(r.fecha.timestamp(), segundos=True),
                                  core.human_size(r.tamano)])
            it.setData(0, R_RUTA, str(r.ruta))
            it.setToolTip(0, r.original or r.documento)
            self.tabla.addTopLevelItem(it)
        if self.tabla.topLevelItemCount():
            self.tabla.setCurrentItem(self.tabla.topLevelItem(0))
        self.tabla.setVisible(bool(visibles))
        self.vacio.setVisible(not visibles and not self._filtro)
        self._trabajos = trabajos_pendientes()
        self._poner_trabajos()
        _contar_pendientes(len(self._trabajos))
        self._botones()

    def _poner_trabajos(self):
        for w in self._tarjetas:
            w.hide()
            w.deleteLater()
        self._tarjetas = []
        for k, t in enumerate(self._trabajos):
            tarjeta = _TarjetaTrabajo(t, lambda _=False, i=k: self.recuperar_trabajo(i),
                                      lambda _=False, i=k: self.descartar_trabajo(i))
            self.lista_trabajo.addWidget(tarjeta)
            self._tarjetas.append(tarjeta)
        n = len(self._trabajos)
        self.caja_trabajo.setVisible(bool(n))
        self.chip_trabajo.setText(guardado.plural(n, "documento", "documentos"))
        self.trabajo_lb.setText("Esto quedó sin guardar cuando RonyPDF se cerró de forma inesperada. Lo recuperado "
                                "se guarda con otro nombre: el original no cambia.")

    def _botones(self):
        sel = self._seleccion() is not None
        self.b_abrir.setVisible(sel)
        self.refresh()

    def _seleccion(self) -> guardado.Respaldo | None:
        it = self.tabla.currentItem()
        if it is None or not it.isSelected():
            return None
        ruta = it.data(0, R_RUTA)
        return next((r for r in self._respaldos if str(r.ruta) == ruta), None)

    def _trabajo_sel(self, k: int | None = None) -> guardado.Trabajo | None:
        if k is None:
            k = 0 if len(self._trabajos) == 1 else None
        return self._trabajos[k] if isinstance(k, int) and 0 <= k < len(self._trabajos) else None

    # ---- copias
    def abrir_copia(self):
        r = self._seleccion()
        if r is not None:
            extensiones.ir_a("viewer", [str(r.ruta)])

    def abrir_carpeta(self):
        try:
            carpeta = guardado.carpeta_escritura(guardado.CARPETA_RESPALDOS, ajustes_path())
        except core.UserError as exc:
            QMessageBox.warning(self, "Copias de respaldo", str(exc))
            return
        reveal(str(carpeta))

    def eliminar(self):
        r = self._seleccion()
        if r is None:
            return
        abierta = self._abierto(str(r.ruta))
        if abierta:
            QMessageBox.information(self, "Eliminar copia", f"Esa copia está abierta en «{abierta}». Abre otro "
                                    "documento allí y vuelve a intentarlo.")
            return
        cuando = guardado.fecha_con_de(r.fecha.timestamp())
        resp, _ = _preguntar(self, "Eliminar copia",
                             guardado.frase(f"Se borrará la copia de «{r.documento}» {cuando}")
                             + " No se puede deshacer.",
                             [("si", "Eliminar", _DESTRUCTIVO), ("cancelar", "Cancelar", _RECHAZAR)],
                             defecto="cancelar", icono="aviso")
        if resp != "si":
            return
        try:
            guardado.eliminar_respaldo(r.ruta, carpetas_respaldos())
        except core.UserError as exc:
            QMessageBox.warning(self, "No se pudo eliminar", str(exc))
        self.actualizar()

    def borrar_todas(self):
        n = len(self._respaldos)
        if not n:
            return
        resp, _ = _preguntar(self, "Borrar todas las copias",
                             f"Se borrarán las {guardado.plural(n, 'copia', 'copias')} de respaldo de todos los "
                             "documentos. No se puede deshacer.",
                             [("si", "Borrar todas", _DESTRUCTIVO), ("cancelar", "Cancelar", _RECHAZAR)],
                             defecto="cancelar", icono="aviso")
        if resp == "si":
            borradas = guardado.borrar_respaldos(carpetas_respaldos())
            self.actualizar()
            if borradas < n:
                QMessageBox.information(self, "Borrar todas las copias",
                                        f"{guardado.plural(n - borradas, 'copia no se pudo borrar', 'copias no se pudieron borrar')}: "
                                        "puede que estén abiertas en otra pantalla o en otro programa.")

    def _abierto(self, ruta: str) -> str | None:
        """Título de la pantalla que tiene ese archivo abierto (con cambios o sin ellos)."""
        return abierto_en(ruta)

    def _abierto_con_cambios(self, ruta: str) -> str | None:
        """Título de la pantalla que tiene ese archivo abierto con cambios sin guardar."""
        return abierto_en(ruta, con_cambios=True)

    def _liberar(self, ruta: str):
        """Cierra el archivo en las pantallas que lo tengan abierto; devuelve cómo volver a abrirlo."""
        return liberar(ruta)

    def restaurar(self):
        r = self._seleccion()
        if r is None or ctx.busy:
            return
        cuando = guardado.fecha_con_de(r.fecha.timestamp())
        original = r.original if r.original and Path(r.original).parent.is_dir() else ""
        botones = [("guardar_como", "Guardar como…", _OTRO), ("cancelar", "Cancelar", _RECHAZAR)]
        if original:
            botones.insert(0, ("reemplazar", "Reemplazar el archivo actual", _ACEPTAR))
        resp, _ = _preguntar(self, "Restaurar una versión anterior",
                             f"¿Qué quieres hacer con la versión de «{r.documento}» {cuando}?", botones,
                             defecto="reemplazar" if original else "guardar_como",
                             detalle=("Si reemplazas el archivo actual, antes se guarda una copia de él, por si "
                                      "quieres volver atrás." if original else
                                      "La carpeta del documento original ya no existe: guárdala con otro nombre."))
        if resp == "reemplazar":
            ocupado = self._abierto_con_cambios(original)
            if ocupado:
                QMessageBox.information(self, "Restaurar", f"«{r.documento}» está abierto con cambios sin guardar "
                                        f"en «{ocupado}». Guárdalos o descártalos antes de restaurar.")
                return
            try:
                carpeta = guardado.carpeta_escritura(guardado.CARPETA_RESPALDOS, ajustes_path())
            except core.UserError as exc:
                QMessageBox.warning(self, "No se pudo restaurar", str(exc))
                return
            volver = self._liberar(original)
            hecho: dict = {}

            def done(_previa):
                hecho["ok"] = True
                reabrir(volver)
                self.actualizar()
                show_done(self, "Versión restaurada",
                          guardado.frase(f"«{Path(original).name}» volvió a la versión {cuando}")
                          + " La que tenías quedó en las copias de respaldo.", file=original)

            def fin():
                if not hecho.get("ok"):          # falló o se canceló: el archivo sigue como estaba
                    reabrir(volver)

            ctx.run_task("Restaurando la versión anterior…", guardado.restaurar, done,
                         on_finally=lambda: QTimer.singleShot(0, fin), copia=str(r.ruta), destino=original,
                         carpeta=str(carpeta), retencion=retencion())
        elif resp == "guardar_como":
            base = original or str(Path(ctx.last_dir) / r.documento)
            out = ask_save(self, suggest_output(base, "_restaurado"), title="Guardar la versión anterior como")
            if not out:
                return
            if _clave(out) == _clave(str(r.ruta)):
                return
            try:
                guardado.copiar(r.ruta, out)
            except (OSError, core.UserError) as exc:
                QMessageBox.warning(self, "No se pudo guardar", str(exc))
                return
            show_done(self, "Versión guardada", f"La versión {cuando} quedó en «{Path(out).name}».", file=out)

    # ---- trabajo sin guardar
    def recuperar_trabajo(self, k: int | None = None):
        t = self._trabajo_sel(k)
        if t is not None:
            recuperar(t, self)
            self.actualizar()

    def descartar_trabajo(self, k: int | None = None):
        t = self._trabajo_sel(k)
        if t is None:
            return
        resp, _ = _preguntar(self, "Descartar trabajo", f"Se borrará el trabajo sin guardar de «{t.nombre}». "
                             "No se puede deshacer.", [("si", "Descartar", _DESTRUCTIVO),
                                                       ("cancelar", "Cancelar", _RECHAZAR)],
                             defecto="cancelar", icono="aviso")
        if resp == "si":
            guardado.descartar(t)
            self.actualizar()

    # ---- opciones
    def _cargar_opciones(self):
        for w in (self.cmb_intervalo, self.chk_respaldo, self.sp_copias, self.sp_dias, self.chk_confirmar):
            w.blockSignals(True)
        k = self.cmb_intervalo.findData(intervalo())
        self.cmb_intervalo.setCurrentIndex(k if k >= 0 else 2)
        self.chk_respaldo.setChecked(respaldo_activo())
        r = retencion()
        self.sp_copias.setValue(r.copias)
        self.sp_dias.setValue(r.dias)
        self.chk_confirmar.setChecked(bool(ajuste("guardado_confirmar", True)))
        for w in (self.cmb_intervalo, self.chk_respaldo, self.sp_copias, self.sp_dias, self.chk_confirmar):
            w.blockSignals(False)
        self._retencion_visible()

    def _retencion_visible(self):
        """Cuántas copias y por cuántos días solo aplica si se guardan copias."""
        self.caja_retencion.setVisible(self.chk_respaldo.isChecked())

    def _opciones_cambiaron(self, *_):
        poner_ajuste("autoguardado_segundos", int(self.cmb_intervalo.currentData()))
        poner_ajuste("guardado_respaldo", self.chk_respaldo.isChecked())
        poner_ajuste("respaldo_copias", self.sp_copias.value())
        poner_ajuste("respaldo_dias", self.sp_dias.value())
        if self.chk_confirmar.isChecked() and not ajuste("guardado_confirmar", True):
            _confirmados.clear()              # vuelve a preguntar desde ya
        poner_ajuste("guardado_confirmar", self.chk_confirmar.isChecked())
        aplicar_intervalo_todas()
        self._retencion_visible()


def _versiones_desde_visor(visor) -> None:
    ruta = visor.canvas.path
    extensiones.ir_a("backup", [ruta] if ruta else None)


REGISTRO = dict(clave="backup", titulo="Respaldo y recuperación", seccion="seguridad", orden=40, pagina=RespaldoPage,
                dibujo=dibujo)
ACCIONES_VISOR = [dict(icono="backup", ayuda="Ver las versiones anteriores de este PDF",
                       texto="Versiones anteriores de este PDF…", accion=_versiones_desde_visor, solo_menu=True)]
