"""Tokens del sistema de diseño de RonyPDF 2.0 (colores, tipografía, espacios, radios y sombras).

Los valores salen tal cual de ``tokens.json`` del sistema de diseño.  Hay un diccionario por tema
(``CLARO`` y ``OSCURO``); el tema activo es el claro.  Todo lo que pinta (la hoja QSS de ``style.py`` y
los componentes dibujados con QPainter) lee los colores con ``color("nombre")`` o ``tokens()``, así un
cambio de tema solo tiene que llamar a ``usar_tema("dark")`` y volver a aplicar la hoja.

Cada categoría de herramientas tiene cuatro tonos (``tonos(cat)``):
    c     azulejo, interruptor encendido, borde de la ficha elegida
    hi    parte alta del degradado del azulejo
    band  banda pastel, fondo del elemento activo y de las fichas elegidas
    ink   texto de la categoría sobre su banda o sobre ``surface``
La categoría «marca» (Inicio) usa el rojo de la marca con los mismos cuatro papeles.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# ---------------------------------------------------------------- colores

CLARO: dict[str, str] = {
    "canvas": "#f4f5f9", "surface": "#ffffff", "surface-sunken": "#eceef4", "surface-hover": "#f7f8fb",
    "field": "#f6f7fa", "line": "#e3e6ee", "line-strong": "#c5cad6", "line-control": "#878ea0",
    "ink": "#161a25", "ink-2": "#4a5266", "ink-3": "#6a7184", "focus": "#2563eb",
    "brand": "#d7373a", "brand-hover": "#be2c2f", "brand-ink": "#b42a2d", "brand-tint": "#fdecec",
    "on-brand": "#ffffff", "on-cat": "#ffffff",
    "cat-ver": "#7b5ce5", "cat-ver-hi": "#9b83f0", "cat-ver-band": "#efeafd", "cat-ver-ink": "#5b3fc4",
    "cat-organizar": "#2e8bd8", "cat-organizar-hi": "#5baae8", "cat-organizar-band": "#e2f0fb",
    "cat-organizar-ink": "#1d68a8",
    "cat-optimizar": "#e5672f", "cat-optimizar-hi": "#f08a5a", "cat-optimizar-band": "#fdebe2",
    "cat-optimizar-ink": "#b04716",
    "cat-convertir": "#129c80", "cat-convertir-hi": "#36b89b", "cat-convertir-band": "#ddf4ee",
    "cat-convertir-ink": "#0b7560",
    "cat-editar": "#d8437f", "cat-editar-hi": "#e86e9e", "cat-editar-band": "#fce6ef", "cat-editar-ink": "#ae2a5e",
    "cat-seguridad": "#4f63e0", "cat-seguridad-hi": "#7584ec", "cat-seguridad-band": "#e7eafc",
    "cat-seguridad-ink": "#3446be",
    "ok": "#1e7b45", "ok-tint": "#e5f4ea", "warn": "#8f5a00", "warn-tint": "#fff2d3",
    "danger": "#bd2b2b", "danger-tint": "#fdecec", "info": "#2559c7", "info-tint": "#e7effd",
    "scrim": "rgba(14, 16, 22, 0.36)",
}

OSCURO: dict[str, str] = {
    "canvas": "#0e1016", "surface": "#171a22", "surface-sunken": "#12151c", "surface-hover": "#1e222c",
    "field": "#1b1f28", "line": "#272c38", "line-strong": "#3a4152", "line-control": "#697185",
    "ink": "#eceef4", "ink-2": "#b0b6c5", "ink-3": "#8e95a6", "focus": "#6e9bff",
    "brand": "#c9363a", "brand-hover": "#d6474a", "brand-ink": "#ff8a8a", "brand-tint": "#3a1c1f",
    "on-brand": "#ffffff", "on-cat": "#ffffff",
    "cat-ver": "#7b5ce5", "cat-ver-hi": "#9077ee", "cat-ver-band": "#251f3d", "cat-ver-ink": "#b7a5fa",
    "cat-organizar": "#2e8bd8", "cat-organizar-hi": "#4c9fe2", "cat-organizar-band": "#172a3b",
    "cat-organizar-ink": "#8cc6f2",
    "cat-optimizar": "#e5672f", "cat-optimizar-hi": "#ec7c48", "cat-optimizar-band": "#36211a",
    "cat-optimizar-ink": "#f5a27c",
    "cat-convertir": "#129c80", "cat-convertir-hi": "#28ad91", "cat-convertir-band": "#12302a",
    "cat-convertir-ink": "#6fd5bc",
    "cat-editar": "#d8437f", "cat-editar-hi": "#e25b91", "cat-editar-band": "#351b28", "cat-editar-ink": "#f39bbe",
    "cat-seguridad": "#4f63e0", "cat-seguridad-hi": "#6878ea", "cat-seguridad-band": "#1d2242",
    "cat-seguridad-ink": "#a5b0f7",
    "ok": "#6fd39a", "ok-tint": "#15301f", "warn": "#f2c266", "warn-tint": "#33280f",
    "danger": "#ff8a8a", "danger-tint": "#3a1c1f", "info": "#8db2ff", "info-tint": "#172440",
    "scrim": "rgba(0, 0, 0, 0.6)",
}

TEMAS = {"light": CLARO, "dark": OSCURO}
_actual = "light"


def usar_tema(nombre: str) -> None:
    """Cambia el tema activo ("light" o "dark").  Después hay que volver a aplicar ``style.build_qss()``."""
    global _actual
    if nombre not in TEMAS:
        raise ValueError(f"Tema desconocido: {nombre}")
    _actual = nombre


def tema_actual() -> str:
    return _actual


def tokens() -> dict[str, str]:
    """Los colores del tema activo."""
    return TEMAS[_actual]


def color(nombre: str) -> str:
    """Un color del tema activo, por su nombre de token («ink-2», «cat-ver-band»…)."""
    return TEMAS[_actual][nombre]


def qcolor(nombre: str):
    """El token como ``QColor`` (entiende también ``rgba(r, g, b, a)``)."""
    from PySide6.QtGui import QColor
    v = color(nombre) if not nombre.startswith(("#", "rgba")) else nombre
    if v.startswith("rgba"):
        r, g, b, a = (float(x) for x in v[v.index("(") + 1: v.index(")")].split(","))
        return QColor(int(r), int(g), int(b), int(round(a * 255)))
    return QColor(v)


def mezclar(a: str, b: str, t: float) -> str:
    """Mezcla dos colores #rrggbb: ``t`` = 0 da ``a``, 1 da ``b`` (para estados desactivados)."""
    a, b = color(a) if not a.startswith("#") else a, color(b) if not b.startswith("#") else b
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(ca, cb))


# ---------------------------------------------------------------- categorías

CATEGORIAS = ("ver", "organizar", "optimizar", "convertir", "editar", "seguridad")
NOMBRES_CATEGORIA = {"ver": "Ver y editar", "organizar": "Organizar", "optimizar": "Optimizar",
                     "convertir": "Convertir", "editar": "Editar", "seguridad": "Seguridad", "marca": "Inicio"}

# Respaldo por si gui.SECTIONS todavía no existe (p. ej. una página construida suelta en una prueba).
_CATEGORIA_FIJA = {
    "viewer": "ver", "forms": "ver", "print": "ver", "compare": "ver",
    "merge": "organizar", "split": "organizar", "organize": "organizar", "rename": "organizar",
    "compress": "optimizar", "batch": "optimizar",
    "ocr": "convertir", "toimage": "convertir", "fromimage": "convertir", "extract": "convertir",
    "ocrcheck": "convertir", "office2pdf": "convertir", "pdf2office": "convertir",
    "watermark": "editar", "numbers": "editar", "metadata": "editar", "stamps": "editar",
    "protect": "seguridad", "unlock": "seguridad", "redact": "seguridad", "sanitize": "seguridad",
    "backup": "seguridad",
}


def tonos(cat: str | None) -> dict[str, str]:
    """Los cuatro tonos de una categoría: ``{"c", "hi", "band", "ink"}``.

    ``"marca"`` usa el rojo de la marca; ``None`` o una categoría desconocida, tonos neutros de tinta."""
    t = tokens()
    if cat in CATEGORIAS:
        p = f"cat-{cat}"
        return {"c": t[p], "hi": t[p + "-hi"], "band": t[p + "-band"], "ink": t[p + "-ink"]}
    if cat == "marca":
        return {"c": t["brand"], "hi": t["brand-hover"], "band": t["brand-tint"], "ink": t["brand-ink"]}
    return {"c": t["ink-2"], "hi": t["ink-3"], "band": t["surface-sunken"], "ink": t["ink"]}


def secciones():
    """gui.SECTIONS (vacío si todavía no existe)."""
    gui = sys.modules.get(f"{__package__}.gui")
    secciones = getattr(gui, "SECTIONS", None) if gui is not None else None
    if secciones is None:
        try:
            from . import gui as gui_mod   # noqa: PLC0415 — import tardío: gui importa casi todo
            secciones = getattr(gui_mod, "SECTIONS", None)
        except ImportError:
            secciones = None
    return secciones or ()


def categoria_de(clave: str | None) -> str:
    """Categoría de una herramienta según ``gui.SECTIONS`` (Inicio y lo desconocido: «marca»)."""
    if not clave or clave == "home":
        return "marca"
    for sec, _label, _tile, items in secciones():
        if any(k == clave for k, _t, _a in items):
            return sec
    return _CATEGORIA_FIJA.get(clave, "marca")


def azulejo_de(clave: str) -> str:
    """Nombre del glifo de una herramienta (el «azulejo» de ``gui.SECTIONS``; por defecto, la clave)."""
    for _sec, _label, _tile, items in secciones():
        for k, _t, a in items:
            if k == clave:
                return a
    return clave


# ---------------------------------------------------------------- espacios, radios y sombras

SPACE = {1: 4, 2: 8, 3: 12, 4: 16, 5: 20, 6: 24, 8: 32, 10: 40}
RADIUS = {"xs": 6, "sm": 8, "md": 12, "lg": 16, "xl": 22, "full": 999}

# Tamaños del azulejo de categoría (GlyphTile)
TILE_XS, TILE_SM, TILE_MD, TILE_LG, TILE_XL = 22, 28, 40, 52, 64

# Sombras para QGraphicsDropShadowEffect: (desenfoque, desplazamiento vertical, alfa 0-255)
SOMBRA_2 = (30, 8, 36)      # tarjeta bajo el puntero, barra de comandos
SOMBRA_3 = (64, 18, 70)     # paleta Ctrl K, menús flotantes

# Anchos de la plantilla de herramienta
PANEL_ANCHO = 380
PANEL_ANCHO_ESTRECHO = 340      # ventana de menos de 1100 px
ANCHO_VENTANA_ESTRECHA = 1100
COLUMNA_UNICA_MAX = 860         # páginas sin migrar: una sola columna centrada


def sombra(widget, nivel: int = 2):
    """Pone a ``widget`` la sombra ``shadow-2`` o ``shadow-3`` (úsala solo donde el sistema lo pide)."""
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QGraphicsDropShadowEffect
    blur, dy, alfa = SOMBRA_3 if nivel == 3 else SOMBRA_2
    eff = QGraphicsDropShadowEffect(widget)
    eff.setBlurRadius(blur)
    eff.setOffset(0, dy)
    eff.setColor(QColor(22, 26, 37, alfa) if _actual == "light" else QColor(0, 0, 0, min(255, alfa * 4)))
    widget.setGraphicsEffect(eff)
    return eff


# ---------------------------------------------------------------- tipografía

FAMILIA_DISPLAY = ["Space Grotesk", "Segoe UI Variable Display", "Segoe UI"]
FAMILIA_TEXTO = ["Segoe UI Variable Text", "Segoe UI"]

# estilo → (px, peso, interletrado en %, familia "display" o "text")
TIPOS = {
    "display": (34, 600, -2.0, "display"),
    "title-1": (26, 600, -1.5, "display"),
    "title-2": (17, 600, -1.0, "display"),
    "title-3": (15, 600, 0.0, "display"),
    "tile-title": (16, 600, -1.0, "display"),
    "step-n": (13, 700, 0.0, "display"),
    "body-strong": (14, 600, 0.0, "text"),
    "body": (14, 400, 0.0, "text"),
    "body-sm": (13, 400, 0.0, "text"),
    "label": (13, 600, 0.0, "text"),
    "caption": (12, 400, 0.0, "text"),
    "chip": (12, 600, 0.0, "text"),
}

_fuentes_cargadas: list[str] | None = None


def fonts_dir() -> Path:
    base = getattr(sys, "_MEIPASS", None)
    root = Path(base) / "ronypdf" if base else Path(__file__).resolve().parent
    return root / "assets" / "fonts"


def cargar_fuentes() -> list[str]:
    """Registra las letras de ``assets/fonts`` (Space Grotesk).  Se puede llamar varias veces.

    Necesita una QGuiApplication.  Devuelve las familias registradas."""
    global _fuentes_cargadas
    if _fuentes_cargadas is not None:
        return _fuentes_cargadas
    from PySide6.QtGui import QFontDatabase, QGuiApplication
    if QGuiApplication.instance() is None:
        return []
    familias: list[str] = []
    carpeta = fonts_dir()
    if carpeta.is_dir():
        for f in sorted(os.listdir(carpeta)):
            if f.lower().endswith((".ttf", ".otf")):
                fid = QFontDatabase.addApplicationFont(str(carpeta / f))
                if fid >= 0:
                    familias += [x for x in QFontDatabase.applicationFontFamilies(fid) if x not in familias]
    _fuentes_cargadas = familias
    return familias


def familia_display() -> str:
    """«Space Grotesk» si está registrada; si no, «Segoe UI Variable Display» o «Segoe UI»."""
    from PySide6.QtGui import QFontDatabase
    cargar_fuentes()
    disponibles = set(QFontDatabase.families())
    return next((f for f in FAMILIA_DISPLAY if f in disponibles), "Segoe UI")


def familia_texto() -> str:
    from PySide6.QtGui import QFontDatabase
    disponibles = set(QFontDatabase.families())
    return next((f for f in FAMILIA_TEXTO if f in disponibles), "Segoe UI")


def fuente(estilo: str = "body", base=None):
    """``QFont`` del estilo tipográfico pedido («title-1», «caption»…), para texto pintado a mano."""
    from PySide6.QtGui import QFont
    px, peso, espacio, fam = TIPOS[estilo]
    f = QFont(base) if base is not None else QFont()
    cargar_fuentes()
    f.setFamilies(FAMILIA_DISPLAY if fam == "display" else FAMILIA_TEXTO)
    f.setPixelSize(px)
    f.setWeight(QFont.Weight(peso))
    if espacio:
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 100 + espacio)
    f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return f


def qss_familia(fam: str) -> str:
    """Lista de familias para la hoja QSS («display» o «text»)."""
    return ", ".join(f'"{x}"' for x in (FAMILIA_DISPLAY if fam == "display" else FAMILIA_TEXTO))
