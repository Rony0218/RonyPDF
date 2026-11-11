"""Registro de herramientas que viven en módulos propios (``ronypdf/<nombre>_ui.py``).

Este archivo, y los pocos puntos de enganche que usa en gui.py, home.py, viewer.py, icons.py y
art.py, son IDÉNTICOS en todas las ramas de trabajo: así se fusionan sin conflictos.  No lo edites
para registrar una herramienta; basta con crear un módulo ``ronypdf/<nombre>_ui.py`` que defina:

``REGISTRO``  (un diccionario o una lista de diccionarios), con estas claves:
    clave      identificador de la página, único (también es el nombre de su ícono).
    titulo     texto del menú lateral.
    seccion    clave de la sección del menú: ver, organizar, optimizar, convertir, editar, seguridad.
    pagina     clase de la pantalla (QWidget con ``accept_files(rutas)``; normalmente un ToolPage).
    dibujo     función ``dibujo(p, base, acento)`` que pinta el ícono con QPainter en una cuadrícula
               de 24 × 24 (el lápiz ya viene puesto con el color ``base``; usa ``acento`` en un
               detalle, con ``extensiones.lapiz(p, acento)``).  También puede ser el nombre de un
               ícono que ya exista en icons.py.
    orden      (opcional) posición entre las herramientas nuevas; menor va primero.  Por defecto 100.
    inicio     (opcional) descripción corta para mostrarla como tarjeta en la pantalla de inicio.
    tipos      (opcional) extensiones de archivo, p. ej. {".docx"}, que la página recibe cuando se
               abren o se sueltan en el inicio.
    filtro     (opcional) nombre para esos tipos en el diálogo «Abrir archivos», p. ej. "Office".

``ACCIONES_VISOR``  (opcional) lista de diccionarios: botones en el encabezado de «Ver y editar».
    icono, ayuda, accion (función que recibe la página del visor) y, opcional, atajo ("Ctrl+P"),
    texto (cómo se llama en el menú «Más acciones»; por defecto, la ayuda) y solo_menu (True: no
    ocupa un botón del encabezado, solo aparece en ese menú).  Cuando la ventana es angosta, todos
    los botones pasan al menú «Más acciones».

``aviso_visor(visor)``  (opcional) se llama cada vez que el visor abre un PDF.  Devuelve None o
    ``(texto, texto_del_boton, funcion)`` para mostrar una franja de aviso con un botón.

Una página puede definir ``has_unsaved()`` para que se pregunte antes de cerrar la aplicación.

Enganches con «Ver y editar» (ronda 2).  Todos son funciones opcionales del módulo ``*_ui.py`` y reciben
la página del visor (``ViewerPage``); ninguno obliga a tocar viewer.py:

``barra_visor(visor)``   se llama una vez, al construir el visor.  Devuelve una lista de botones (créalos
    con ``extensiones.boton_barra``) que se agregan como un grupo a la barra de herramientas.  Con la
    ventana angosta la barra muestra solo los íconos: todo botón necesita ícono y ayuda.
``paneles_visor(visor)`` se llama una vez.  Devuelve una lista de diccionarios ``clave, titulo, widget``:
    cada uno es una pestaña del panel derecho, junto a «Propiedades».  Luego se usan
    ``visor.mostrar_panel(clave)`` y ``visor.titulo_panel(clave, "Comentarios (3)")``.
``al_abrir_visor(visor)`` se llama cada vez que el visor abre un PDF (también al recargarlo después de
    guardar), con el documento ya listo (``visor.canvas.doc`` y ``.pages``) y antes de actualizar el
    resto de la pantalla.  Aquí la extensión suelta lo del documento anterior; lo que hubiera puesto
    en la escena ya no existe.
``evento_visor(visor, tipo, evento)``  ratón y teclado del lienzo, antes que el propio visor.  ``tipo`` es
    "press", "move", "release", "doble", "menu" o "tecla".  Si devuelve True, el visor no hace nada más
    con ese evento; tras un "press" consumido, los "move" y el "release" de ese arrastre llegan solo a
    las extensiones.  No se llama mientras el visor tiene en curso un arrastre propio ni, para "tecla",
    mientras se escribe en un texto.
``menu_seleccion_visor(visor, menu)``  agrega acciones al menú del texto seleccionado (clic derecho).
``cambios_visor(visor)``  True si la extensión tiene cambios sin guardar en el PDF abierto.
``guardar_visor(visor)``  devuelve None o una función ``aplicar(doc)`` que escribe esos cambios en un
    ``pymupdf.Document``.  Se llama en el hilo de la interfaz al empezar a guardar y ``aplicar`` corre
    después en segundo plano, sobre una copia recién abierta del archivo y después de los cambios del
    propio visor: no debe tocar la interfaz ni depender de ``visor.canvas.doc``.  Si esos cambios quitan
    algo que ya estaba en el archivo (borrar un comentario, cambiarle el texto), ``aplicar.borra = True``:
    así el guardado no es incremental y lo anterior no queda dentro del archivo.
``autoguardar_visor(visor)``  (opcional) como ``guardar_visor``, pero para el autoguardado: no debe tocar la
    interfaz (la persona puede estar escribiendo).  Si no existe, el autoguardado usa ``guardar_visor``.

Lo que el visor ofrece a las extensiones:
    ``visor.ext``                      diccionario libre para guardar su estado (usa tu clave).
    ``visor.set_tool("ext:nombre")``   activa una herramienta propia: el visor suelta las suyas y los
                                       eventos llegan por ``evento_visor`` (mira ``visor.canvas.tool``).
                                       ``visor.canvas.tool_changed`` avisa cuando cambia la herramienta.
    ``visor.canvas.registrar_ext(a)``  apila en «Deshacer» una acción con ``deshacer()`` y ``rehacer()``
                                       (y, opcional, ``texto``).
    ``visor.canvas.refrescar(pags)``   vuelve a dibujar esas páginas (None = todas) y sus miniaturas,
                                       después de cambiar ``visor.canvas.doc`` en memoria.
    ``visor.canvas.edits_changed.emit()``  actualiza «Cambios sin guardar» y los botones.
    ``visor.canvas.punto_en_pagina(e.position())``  (página, x, y) bajo el ratón, o None.
    ``visor.canvas.seleccion_por_pagina()``  texto seleccionado: (página, texto, rectángulos por renglón).
    Las coordenadas son puntos PDF de la página tal como se ve (si la página tiene /Rotate, pásalas a
    las del archivo con ``page.derotation_matrix``).  ``visor.canvas.pages[i]`` es el elemento de la
    escena de cada página: lo que se le agregue como hijo se mueve con ella.

Enganches con la aplicación: ``al_iniciar(ventana)`` se llama una vez, con la ventana principal ya
construida (antes de mostrarla), y ``al_cerrar(ventana)`` cuando ya se aceptó cerrarla.
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Callable

from PySide6.QtCore import QPointF, QSize, Qt
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QToolButton, QVBoxLayout

from . import art, core, icons

BASE = "#1F2937"      # los dos colores de los íconos de la aplicación
ACENTO = "#D63A3A"
CONOCIDOS = ("censura_ui", "comentarios_ui", "comparar_ui", "formularios_ui", "guardado_ui", "imprimir_ui",
             "lotes_ui", "ocr_ui", "oficina_ui", "visor_ui")
# Páginas que el ejecutable debe traer siempre (build.ps1 lo comprueba con «RonyPDF.exe --comprobar-herramientas»).
ESENCIALES = ("forms", "print", "redact", "sanitize", "office2pdf", "pdf2office",          # ronda 1
              "batch", "rename", "ocrcheck", "stamps", "backup", "compare")                 # ronda 2

_modulos: list | None = None
_registros: list[dict] = []


def modulos() -> list:
    """Importa (una sola vez) todos los módulos ``ronypdf.*_ui``."""
    global _modulos
    if _modulos is None:
        nombres = set(CONOCIDOS)
        try:
            paquete = importlib.import_module(__package__)
            nombres |= {m.name for m in pkgutil.iter_modules(paquete.__path__) if m.name.endswith("_ui")}
        except Exception:  # noqa: BLE001 — en el ejecutable puede no haber listado de módulos
            pass
        _modulos = []
        for nombre in sorted(nombres):
            completo = f"{__package__}.{nombre}"
            try:
                _modulos.append(importlib.import_module(completo))
            except ModuleNotFoundError as exc:
                if exc.name != completo:      # el módulo existe, pero le falta algo: es un error real
                    raise
    return _modulos


def registros() -> list[dict]:
    if not _registros:
        for m in modulos():
            reg = getattr(m, "REGISTRO", None)
            for r in ([reg] if isinstance(reg, dict) else list(reg or [])):
                _registros.append(r)
        _registros.sort(key=lambda r: (r.get("orden", 100), r["titulo"]))
    return _registros


def _azulejo(dibujo: Callable) -> QPixmap:
    """Ícono grande con el estilo de la aplicación: línea oscura y un detalle rojo."""
    pm = QPixmap(256, 256)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.translate(22, 22)
    p.scale(212 / 24, 212 / 24)
    pen = QPen(QColor(BASE), 1.35)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    dibujo(p, QColor(BASE), QColor(ACENTO))
    p.end()
    return pm


def lapiz(p: QPainter, color: QColor) -> None:
    """Cambia solo el color del lápiz (para el detalle de acento de un ícono)."""
    pen = QPen(p.pen())
    pen.setColor(color)
    p.setPen(pen)


def aplicar(page_classes: dict, sections: list) -> None:
    """Agrega las páginas registradas al menú lateral y a la lista de pantallas."""
    por_seccion = {s[0]: s[3] for s in sections}
    for r in registros():
        clave, dibujo = r["clave"], r.get("dibujo")
        if clave in page_classes:
            continue
        if callable(dibujo):
            icons.EXTRA[clave] = dibujo
            art.EXTRA[clave] = lambda d=dibujo: _azulejo(d)
            nombre = clave
        else:
            nombre = dibujo or "pdf"
        r["_azulejo"] = nombre
        page_classes[clave] = r["pagina"]
        por_seccion[r["seccion"]].append((clave, r["titulo"], nombre))


# ---------------------------------------------------------------- inicio y apertura de archivos

def inicio() -> list[tuple[str, str, str, str]]:
    """Tarjetas para la pantalla de inicio: (página, azulejo, título, descripción)."""
    return [(r["clave"], r.get("_azulejo", r["clave"]), r["titulo"], r["inicio"])
            for r in registros() if r.get("inicio")]


def tipos_inicio() -> set[str]:
    out = set(core.PDF_EXTS) | set(core.IMAGE_EXTS)
    for r in registros():
        out |= {t.lower() for t in r.get("tipos", ())}
    return out


def filtro_inicio(base: str) -> str:
    """Filtro del diálogo «Abrir archivos» con los tipos que agregan las extensiones."""
    grupos, todos = [], []
    for r in registros():
        tipos = sorted(t.lower() for t in r.get("tipos", ()))
        if tipos:
            patron = " ".join(f"*{t}" for t in tipos)
            todos.append(patron)
            grupos.append(f"{r.get('filtro', r['titulo'])} ({patron})")
    if not grupos:
        return base
    propios = base[base.index("(") + 1: base.rindex(")")]
    return ";;".join([f"Todos los compatibles ({propios} {' '.join(todos)})", base] + grupos)


def destino(paths: list[str]) -> str | None:
    """Página que debe recibir estos archivos, si alguno es de un tipo propio de una extensión."""
    exts = {p[p.rfind("."):].lower() for p in paths if "." in p}
    for r in registros():
        if exts & {t.lower() for t in r.get("tipos", ())}:
            return r["clave"]
    return None


def pendientes(pages: dict) -> list[str]:
    """Títulos de las páginas registradas que tienen cambios sin guardar."""
    out = []
    for r in registros():
        page = pages.get(r["clave"])
        if page is not None and callable(getattr(page, "has_unsaved", None)) and page.has_unsaved():
            out.append(f"«{r['titulo']}»")
    return out


def ir_a(clave: str, rutas: list[str] | None = None):
    """Muestra una página (y le entrega archivos). Devuelve la página, o None si no hay ventana."""
    from .widgets import ctx
    win = ctx.window
    if win is None or clave not in getattr(win, "pages", {}):
        return None
    win.navigate(clave)
    if rutas:
        win.pages[clave].accept_files(list(rutas))
    return win.pages[clave]


# ---------------------------------------------------------------- visor

def botones_visor(visor) -> list[QToolButton]:
    """Botones que las extensiones agregan al encabezado de «Ver y editar»."""
    out = []
    for m in modulos():
        for a in getattr(m, "ACCIONES_VISOR", ()):
            if a.get("atajo"):
                sc = QShortcut(QKeySequence(a["atajo"]), visor)
                sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
                sc.activated.connect(lambda fn=a["accion"]: fn(visor))
            if a.get("solo_menu"):
                continue
            b = QToolButton()
            b.setObjectName("IconButton")
            b.setIcon(icons.icon(a["icono"], "#374151", size=17))
            b.setIconSize(QSize(17, 17))
            b.setToolTip(a["ayuda"])
            b.setAccessibleName(a["ayuda"])
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, fn=a["accion"]: fn(visor))
            out.append(b)
    return out


def _dibujo_mas(p: QPainter, base: QColor, _acento: QColor) -> None:
    p.save()
    p.setBrush(base)
    for x in (5.5, 12.0, 18.5):
        p.drawEllipse(QPointF(x, 12.0), 1.1, 1.1)
    p.restore()


icons.EXTRA.setdefault("mas", _dibujo_mas)


def menu_visor(visor) -> QToolButton | None:
    """Botón «Más acciones» del encabezado del visor: todas las acciones de las extensiones, con texto."""
    acciones = [a for m in modulos() for a in getattr(m, "ACCIONES_VISOR", ())]
    if not acciones:
        return None
    b = QToolButton()
    b.setObjectName("IconButton")
    b.setIcon(icons.icon("mas", "#374151", size=17))
    b.setIconSize(QSize(17, 17))
    b.setToolTip("Más acciones")
    b.setAccessibleName("Más acciones")
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    b.setStyleSheet("QToolButton::menu-indicator { image: none; width: 0; }")
    menu = QMenu(b)
    for a in acciones:
        texto = a.get("texto") or a["ayuda"]
        if a.get("atajo") and a["atajo"] not in texto:
            texto = f"{texto}\t{a['atajo']}"
        menu.addAction(icons.icon(a["icono"], "#374151", size=17), texto, lambda fn=a["accion"]: fn(visor))
    b.setMenu(menu)
    return b


def solo_menu_visor() -> bool:
    """¿Hay acciones que solo viven en el menú «Más acciones»?  (Entonces ese botón se ve siempre.)"""
    return any(a.get("solo_menu") for m in modulos() for a in getattr(m, "ACCIONES_VISOR", ()))


def boton_barra(texto: str, icono: str, ayuda: str, checkable: bool = True) -> QToolButton:
    """Botón para la barra de herramientas del visor, con el mismo aspecto que los suyos."""
    b = QToolButton()
    b.setObjectName("SegButton")
    b.setText(texto)
    b.setIcon(icons.icon(icono, "#374151", "#111827", 17))
    b.setIconSize(QSize(17, 17))
    b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon if texto
                         else Qt.ToolButtonStyle.ToolButtonIconOnly)
    b.setCheckable(checkable)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setToolTip(ayuda)
    b.setAccessibleName(ayuda)
    return b


_enganches: dict[str, list[Callable]] = {}


def _funciones(nombre: str) -> list[Callable]:
    """Las funciones ``nombre`` que definan los módulos de extensión (en caché: se consultan mucho)."""
    if nombre not in _enganches:
        _enganches[nombre] = [fn for fn in (getattr(m, nombre, None) for m in modulos()) if callable(fn)]
    return _enganches[nombre]


def grupos_visor(visor) -> list[list[QToolButton]]:
    """Grupos de botones que las extensiones agregan a la barra de herramientas del visor."""
    return [g for g in (list(fn(visor) or ()) for fn in _funciones("barra_visor")) if g]


def paneles_visor(visor) -> list[dict]:
    """Pestañas que las extensiones agregan al panel derecho del visor."""
    return [dict(d) for fn in _funciones("paneles_visor") for d in (fn(visor) or ())]


def visor_abierto(visor) -> None:
    for fn in _funciones("al_abrir_visor"):
        fn(visor)


def evento_visor(visor, tipo: str, evento) -> bool:
    for fn in _funciones("evento_visor"):
        if fn(visor, tipo, evento):
            return True
    return False


def menu_seleccion_visor(visor, menu: QMenu) -> None:
    for fn in _funciones("menu_seleccion_visor"):
        fn(visor, menu)


def cambios_visor(visor) -> bool:
    return any(fn(visor) for fn in _funciones("cambios_visor"))


def _juntar(pasos: list[Callable]) -> Callable | None:
    if not pasos:
        return None

    def aplicar(doc) -> None:
        for paso in pasos:
            paso(doc)

    aplicar.borra = any(getattr(p, "borra", False) for p in pasos)
    return aplicar


def guardar_visor(visor) -> Callable | None:
    """Función ``aplicar(doc)`` con los cambios de todas las extensiones, o None si no hay ninguno.
    ``aplicar.borra`` dice si alguno quita contenido que ya estaba en el archivo."""
    return _juntar([paso for paso in (fn(visor) for fn in _funciones("guardar_visor")) if paso is not None])


def autoguardar_visor(visor) -> Callable | None:
    """Como ``guardar_visor``, para el autoguardado: sin tocar la interfaz del visor."""
    pasos = []
    for m in modulos():
        fn = getattr(m, "autoguardar_visor", None) or getattr(m, "guardar_visor", None)
        if callable(fn):
            paso = fn(visor)
            if paso is not None:
                pasos.append(paso)
    return _juntar(pasos)


def al_iniciar(ventana) -> None:
    for fn in _funciones("al_iniciar"):
        fn(ventana)


def al_cerrar(ventana) -> None:
    for fn in _funciones("al_cerrar"):
        fn(ventana)


class AvisosVisor(QFrame):
    """Franjas de aviso bajo la barra del visor (una por cada extensión que tenga algo que decir)."""

    def __init__(self, visor):
        super().__init__()
        self.visor = visor
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(0)
        self.hide()

    def actualizar(self) -> None:
        while self.lay.count():
            it = self.lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        n = 0
        for m in modulos():
            fn = getattr(m, "aviso_visor", None)
            aviso = fn(self.visor) if callable(fn) else None
            if not aviso:
                continue
            texto, boton, accion = aviso
            fila = QFrame()
            fila.setObjectName("AvisoExtension")
            fila.setStyleSheet("#AvisoExtension { background: #F3F7FE; border-bottom: 1px solid #C9DAF8; }")
            hl = QHBoxLayout(fila)
            hl.setContentsMargins(18, 6, 18, 6)
            lb = QLabel(texto)
            lb.setWordWrap(True)
            hl.addWidget(lb, 1)
            b = QPushButton(boton)
            b.setObjectName("Secondary")
            b.clicked.connect(lambda _=False, fn=accion: fn())
            hl.addWidget(b)
            self.lay.addWidget(fila)
            n += 1
        self.setVisible(n > 0)
