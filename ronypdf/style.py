"""Hoja de estilos (QSS) de RonyPDF 2.0, armada desde los tokens de ``tema.py``.

``build_qss()`` devuelve la hoja del tema activo; ``QSS`` es la del tema claro, para quien la importe
directamente.  Las constantes ACCENT, MUTED… se conservan (otros módulos las importan) y apuntan a los
tokens nuevos.  Los valores replican ``bundle.base.css`` del sistema de diseño.

Color por categoría: la página de una herramienta lleva la propiedad ``cat`` («ver», «seguridad»…) y las
reglas ``QWidget[cat="…"] …`` pintan con su color las casillas marcadas, los botones de opción, las barras
de progreso, los deslizadores y las filas elegidas que haya dentro.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QAbstractSpinBox

from . import imagenes_qss as img
from . import tema

_T = tema.CLARO
ACCENT = _T["brand"]
ACCENT_HOVER = _T["brand-hover"]
ACCENT_SOFT = _T["brand-tint"]
BG = _T["canvas"]
SURFACE = _T["surface"]
BORDER = _T["line"]
TEXT = _T["ink"]
MUTED = _T["ink-3"]


def _por_categoria(t: dict[str, str]) -> str:
    reglas = []
    for cat in tema.CATEGORIAS + ("marca",):
        if cat == "marca":
            c, band, ink = t["brand"], t["brand-tint"], t["brand-ink"]
        else:
            c, band, ink = t[f"cat-{cat}"], t[f"cat-{cat}-band"], t[f"cat-{cat}-ink"]
        sel = f'QWidget[cat="{cat}"]'
        on_check = img.casilla(c, t["on-cat"])
        on_radio = img.opcion(c, t["surface"], 5)
        reglas.append(f"""
{sel} QCheckBox::indicator:checked, {sel} QTreeView::indicator:checked, {sel} QListView::indicator:checked {{
    image: url({on_check}); }}
{sel} QRadioButton::indicator:checked {{ image: url({on_radio}); }}
{sel} QProgressBar::chunk {{ background: {c}; }}
{sel} QSlider::sub-page:horizontal {{ background: {c}; }}
{sel} QSlider::handle:horizontal {{ background: {c}; }}
{sel} QTreeWidget::item:selected, {sel} QListWidget::item:selected {{ background: {band}; color: {t['ink']}; }}
{sel} QToolButton#SegButton:checked, {sel} QToolButton#SegItem:checked {{ color: {ink}; }}
QPushButton#NavItem[cat="{cat}"]:checked {{ background: {band}; color: {ink}; font-weight: 600; }}
""")
    return "".join(reglas)


def build_qss(t: dict[str, str] | None = None) -> str:
    """Hoja QSS completa para los tokens ``t`` (por defecto, los del tema activo)."""
    t = t or tema.tokens()
    disp = tema.qss_familia("display")
    text = tema.qss_familia("text")
    dis = lambda nombre: tema.mezclar(nombre, t["surface"], 0.55) if nombre.startswith("#") else \
        tema.mezclar(t[nombre], t["surface"], 0.55)  # noqa: E731 — «opacity: .45» sobre surface
    chk_on = img.casilla(t["ink"], t["on-cat"])
    chk_on_dis = img.casilla(t["ink"], t["on-cat"], 0.4)
    chk_off = img.casilla_vacia(t["line-control"], t["field"])
    chk_off_dis = img.casilla_vacia(t["line-control"], t["field"], 0.45)
    rad_on = img.opcion(t["ink"], t["surface"], 5)
    rad_on_dis = img.opcion(t["ink"], t["surface"], 5, 0.4)
    rad_off = img.opcion(t["line-control"], t["field"], 1)
    rad_off_dis = img.opcion(t["line-control"], t["field"], 1, 0.45)
    flecha = img.flecha(t["ink-2"], "abajo", 14)
    flecha_dis = img.flecha(t["line-strong"], "abajo", 14)
    sube = img.flecha(t["ink-2"], "arriba", 10)
    baja = img.flecha(t["ink-2"], "abajo", 10)

    return f"""
/* ================================================================ base */
QWidget {{ font-family: {text}; font-size: 14px; color: {t['ink']}; }}
QMainWindow, #Content, #Page, #ScrollBody {{ background: {t['canvas']}; }}
QScrollArea {{ background: transparent; border: none; }}
QDialog, QMessageBox {{ background: {t['surface']}; }}
QMessageBox QLabel {{ min-width: 280px; }}
QLabel {{ background: transparent; }}

/* ================================================================ textos */
#PageTitle, #ToolTitle {{ font-family: {disp}; font-size: 26px; font-weight: 600; color: {t['ink']}; }}
#PageSubtitle, #ToolSubtitle {{ font-size: 13px; color: {t['ink-2']}; }}
#Hint, #Help {{ font-size: 12px; color: {t['ink-3']}; }}
#Caption {{ font-size: 12px; color: {t['ink-3']}; }}
#BodySm {{ font-size: 13px; color: {t['ink-2']}; }}
#GroupTitle, #StepTitle {{ font-family: {disp}; font-size: 15px; font-weight: 600; color: {t['ink']}; }}
#SectionTitle, #PanelTitle, #Title2 {{ font-family: {disp}; font-size: 17px; font-weight: 600; color: {t['ink']}; }}
#DisplayTitle {{ font-family: {disp}; font-size: 34px; font-weight: 600; color: {t['ink']}; }}
#CardTitle, #FileName, #DocName, #BodyStrong {{ font-size: 14px; font-weight: 600; color: {t['ink']}; }}
#FieldLabel, #Label {{ font-size: 13px; font-weight: 600; color: {t['ink']}; }}
#OptTitle {{ font-size: 14px; font-weight: 600; color: {t['ink']}; }}
#AppTitle, #NavName {{ font-family: {disp}; font-size: 17px; font-weight: 600; color: {t['ink']}; }}
#AppVersion {{ color: {t['ink-3']}; font-size: 12px; }}
#Eyebrow {{ color: {t['brand-ink']}; font-weight: 600; font-size: 13px; }}
#HomeTitle {{ font-family: {disp}; font-size: 34px; font-weight: 600; }}
#HeroTitle {{ font-family: {disp}; font-size: 17px; font-weight: 600; }}
#DropTitle {{ font-family: {disp}; font-size: 17px; font-weight: 600; color: {t['ink']}; }}
#Breadcrumb {{ font-size: 13px; color: {t['ink-3']}; }}

/* ================================================================ superficies */
#Card, #StepsPanel, #ToolPanel {{ background: {t['surface']}; border: 1px solid {t['line']}; border-radius: 16px; }}
#StepsPanel[plano="true"] {{ background: transparent; border: none; border-radius: 0; }}
#ToolMain, #Preview {{ background: {t['surface-sunken']}; border: none; border-radius: 16px; }}
#Footer, #ActionBar {{ background: {t['surface']}; border: none; border-top: 1px solid {t['line']}; }}
#TopBar {{ background: {t['surface']}; border: none; border-bottom: 1px solid {t['line']}; }}
#Sidebar {{ background: {t['surface']}; border: none; border-right: 1px solid {t['line']}; }}
#Sidebar QScrollArea, #SidebarBody {{ background: {t['surface']}; }}
#RailSep {{ background: {t['line']}; border: none; }}
#Flyout {{ background: {t['surface']}; border: 1px solid {t['line']}; border-radius: 12px; }}
#Palette {{ background: {t['surface']}; border: 1px solid {t['line']}; border-radius: 22px; }}
#PaletteInputRow {{ background: transparent; border: none; border-bottom: 1px solid {t['line']}; }}
#PaletteFoot {{ background: transparent; border: none; border-top: 1px solid {t['line']}; }}
#PaletteScroll, #PaletteScroll > QWidget > QWidget {{ background: transparent; }}
#CommandBar {{ background: {t['surface']}; border: 1px solid {t['line']}; border-radius: 22px; }}
QToolButton#Send {{ background: {t['brand']}; border: none; border-radius: 19px; padding: 0; }}
QToolButton#Send:hover {{ background: {t['brand-hover']}; }}
#CommandBar[drop="true"] {{ background: {t['brand-tint']}; border: 2px dashed {t['brand']}; }}
#FlyoutTitle {{ font-size: 12px; font-weight: 600; color: {t['ink-3']}; }}
#EditorHeader, #EditorToolbar {{ background: {t['surface']}; border: none; border-bottom: 1px solid {t['line']}; }}
#EditorFooter {{ background: {t['surface']}; border: none; border-top: 1px solid {t['line']}; }}
#SidePanel, #PanelInterior {{ background: {t['surface']}; }}
#FileCard {{ background: {t['surface']}; border: 1px solid {t['line']}; border-radius: 12px; }}
#Hero {{ background: {t['surface']}; border: 1px solid {t['line']}; border-radius: 22px; }}
#ToolCard, #ToolCard2 {{ background: {t['surface']}; border: 1px solid {t['line']}; border-radius: 16px; }}
#ToolCard:hover, #ToolCard2:hover {{ border-color: {t['line-strong']}; }}
#ToolCardTitle {{ font-family: {disp}; font-weight: 600; font-size: 16px; }}
#ToolCardText {{ color: {t['ink-2']}; font-size: 13px; }}
#RecentRow {{ border-top: 1px solid {t['line']}; }}
#Tip {{ background: {t['info-tint']}; border-radius: 12px; color: {t['info']}; }}
#TipWarn {{ background: {t['warn-tint']}; border-radius: 12px; color: {t['warn']}; }}
#Tip QLabel {{ color: {t['info']}; }}
#TipWarn QLabel {{ color: {t['warn']}; }}
#DropArea, #DropWell {{ border: 2px dashed {t['line-strong']}; border-radius: 16px; background: {t['surface']}; }}
#DropArea[hover="true"], #DropWell[hover="true"] {{ border-color: {t['brand']}; background: {t['brand-tint']}; }}
#DropZone {{ border: 2px dashed {t['line-strong']}; border-radius: 16px; background: transparent; margin: 12px; }}
#DropZone[hover="true"] {{ border-color: {t['brand']}; background: {t['brand-tint']}; }}

/* ================================================================ plantilla de herramienta y componentes */
#ToolHead, #ToolBody, #PanelBody, #MainStack, #PanelScroll {{ background: transparent; }}
#Card[plano="true"] {{ background: transparent; border: none; border-radius: 0; }}
#Summary {{ font-size: 13px; color: {t['ink-2']}; }}
#ActionBar QPushButton {{ padding: 13px 16px; }}
#ActionBar QPushButton[tam="lg"] {{ padding: 13px 22px; font-size: 15px; }}
#ActionBar QPushButton#Flat, #ActionBar QPushButton#Ghost {{ padding: 13px 16px; }}
#FileChip {{ background: {t['surface']}; border: 1px solid {t['line']}; border-radius: 12px; }}
#FileChipName {{ font-size: 13px; font-weight: 600; color: {t['ink']}; }}
#SwitchText {{ font-size: 14px; color: {t['ink']}; }}
#Kbd {{ background: {t['surface-sunken']}; border: 1px solid {t['line']}; border-radius: 6px; padding: 1px 6px;
    color: {t['ink-2']}; font-size: 11px; font-weight: 600; }}
QFrame#Note {{ border-radius: 12px; background: {t['info-tint']}; }}
QFrame#Note[tone="ok"] {{ background: {t['ok-tint']}; }}
QFrame#Note[tone="warn"] {{ background: {t['warn-tint']}; }}
QFrame#Note[tone="danger"] {{ background: {t['danger-tint']}; }}
QFrame#Note QLabel {{ font-size: 13px; color: {t['info']}; }}
QFrame#Note[tone="ok"] QLabel {{ color: {t['ok']}; }}
QFrame#Note[tone="warn"] QLabel {{ color: {t['warn']}; }}
QFrame#Note[tone="danger"] QLabel {{ color: {t['danger']}; }}

/* ================================================================ chips heredados */
#ChipOk {{ background: {t['ok-tint']}; color: {t['ok']}; border-radius: 13px; padding: 0 10px;
    font-weight: 600; font-size: 12px; }}
#UnsavedChip {{ background: {t['warn-tint']}; color: {t['warn']}; border-radius: 11px; padding: 4px 10px;
    font-weight: 600; font-size: 12px; }}
#KindChip {{ background: {t['info-tint']}; color: {t['info']}; border-radius: 11px; padding: 4px 10px;
    font-weight: 600; font-size: 12px; }}

/* ================================================================ botones */
QPushButton {{
    background: {t['surface']}; border: 1px solid {t['line-strong']}; border-radius: 12px;
    padding: 9px 14px; font-weight: 600; color: {t['ink']};
}}
QPushButton:hover {{ background: {t['surface-hover']}; }}
QPushButton:pressed {{ background: {t['surface-sunken']}; }}
QPushButton:disabled {{ color: {dis('ink')}; border-color: {dis('line-strong')}; background: {t['surface']}; }}
QPushButton#Secondary {{ background: {t['surface']}; border: 1px solid {t['line-strong']}; color: {t['ink']};
    padding: 9px 16px; }}
QPushButton#Secondary:hover {{ background: {t['surface-hover']}; }}
QPushButton#Secondary:disabled {{ color: {dis('ink')}; border-color: {dis('line-strong')}; }}
QPushButton#Primary {{ background: {t['brand']}; color: {t['on-brand']}; border: 1px solid {t['brand']};
    padding: 9px 16px; }}
QPushButton#Primary:hover {{ background: {t['brand-hover']}; border-color: {t['brand-hover']}; }}
QPushButton#Primary:disabled {{ background: {dis('brand')}; border-color: {dis('brand')}; color: {t['on-brand']}; }}
QPushButton[tam="lg"] {{ padding: 13px 22px; font-size: 15px; }}
QPushButton[tam="sm"] {{ padding: 6px 12px; font-size: 13px; border-radius: 8px; }}
QPushButton#Flat, QPushButton#Ghost {{ border: 1px solid transparent; background: transparent; color: {t['ink-2']};
    padding: 6px 12px; border-radius: 8px; font-size: 13px; }}
QPushButton#Flat:hover, QPushButton#Ghost:hover {{ background: {t['surface-hover']}; color: {t['ink']}; }}
QPushButton#Flat:disabled, QPushButton#Ghost:disabled {{ color: {dis('ink-2')}; }}
QPushButton#Danger {{ background: {t['danger-tint']}; border: 1px solid {t['danger-tint']}; color: {t['danger']}; }}
QPushButton#Danger:hover {{ border-color: {tema.mezclar(t['danger-tint'], t['danger'], 0.25)}; }}
QPushButton#CommandChip {{ border: 1px solid {t['line']}; background: {t['surface']}; color: {t['ink-2']};
    border-radius: 15px; padding: 0 12px; font-size: 13px; }}
QPushButton#CommandChip:hover {{ background: {t['surface-hover']}; color: {t['ink']}; }}

QToolButton {{ border: 1px solid transparent; background: transparent; border-radius: 8px; padding: 4px; }}
QToolButton:hover {{ background: {t['surface-hover']}; }}
QToolButton:pressed {{ background: {t['surface-sunken']}; }}
QToolButton#Flat, QToolButton#Tool, QToolButton#IconButton, QToolButton#Ghost {{
    border: 1px solid transparent; background: transparent; border-radius: 8px; padding: 6px; color: {t['ink-2']};
}}
QToolButton#Flat:hover, QToolButton#Tool:hover, QToolButton#IconButton:hover, QToolButton#Ghost:hover {{
    background: {t['surface-hover']}; color: {t['ink']}; }}
QToolButton#Tool:pressed, QToolButton#IconButton:pressed {{ background: {t['surface-sunken']}; }}
QToolButton#Tool:disabled, QToolButton#IconButton:disabled {{ color: {dis('ink-2')}; background: transparent; }}
QToolButton#Tool {{ padding: 5px 8px; }}
QToolButton#ToolToggle {{ border: 1px solid transparent; background: transparent; border-radius: 8px; padding: 4px;
    min-width: 22px; }}
QToolButton#ToolToggle:hover {{ background: {t['surface-hover']}; }}
QToolButton#ToolToggle:checked {{ background: {t['surface-sunken']}; border-color: {t['line']}; color: {t['ink']}; }}
QToolButton#RailButton {{ border: none; border-radius: 12px; padding: 7px; background: transparent; }}
QToolButton#RailButton:hover {{ background: {t['surface-hover']}; }}
QToolButton#RailButton:checked {{ background: {t['surface-sunken']}; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}

/* segmentos (rp-seg / rp-toolgroup) */
QFrame#SegGroup, QFrame#Segmented {{ background: {t['surface-sunken']}; border: none; border-radius: 12px; }}
QToolButton#SegButton, QToolButton#SegItem {{
    border: 1px solid transparent; border-radius: 8px; padding: 6px 10px; background: transparent;
    color: {t['ink-2']}; font-size: 13px; font-weight: 500;
}}
QToolButton#SegItem {{ padding: 6px 12px; min-height: 18px; }}
QToolButton#SegButton:hover, QToolButton#SegItem:hover {{ color: {t['ink']}; background: transparent; }}
QToolButton#SegButton:checked, QToolButton#SegItem:checked {{
    background: {t['surface']}; color: {t['ink']}; font-weight: 600; border: 1px solid {t['line']}; }}
QToolButton#SegButton:disabled, QToolButton#SegItem:disabled {{ color: {dis('ink-2')}; }}
QToolButton#SegButton::menu-indicator {{ image: none; width: 0; }}
QToolButton#Swatch {{ border-radius: 14px; min-width: 28px; min-height: 28px; max-width: 28px; max-height: 28px; }}

/* menú lateral */
QPushButton#NavItem {{
    border: none; border-radius: 8px; padding: 7px 8px; text-align: left; color: {t['ink']};
    background: transparent; font-size: 14px; font-weight: 400; min-height: 22px;
}}
QPushButton#NavItem:hover {{ background: {t['surface-hover']}; }}
QPushButton#NavItem:checked {{ background: {t['surface-sunken']}; font-weight: 600; }}
QToolButton#NavButton {{ border: none; border-radius: 8px; padding: 6px 10px; text-align: left; color: {t['ink']}; }}
QToolButton#NavButton:hover {{ background: {t['surface-hover']}; }}

/* ================================================================ campos */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QTimeEdit {{
    background: {t['field']}; border: 1px solid {t['line-control']}; border-radius: 12px;
    padding: 8px 12px; min-height: 22px; color: {t['ink']};
    selection-background-color: {t['focus']}; selection-color: #ffffff;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QDateEdit:focus, QTimeEdit:focus {{
    border: 2px solid {t['focus']}; background: {t['surface']}; padding: 7px 11px; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    background: {t['surface-sunken']}; color: {dis('ink')}; border-color: {t['line-strong']}; }}
QLineEdit[error="true"] {{ border-color: {t['danger']}; }}
QComboBox {{ padding-right: 34px; }}
QComboBox:focus {{ padding-right: 33px; }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; border: none; width: 30px; }}
QComboBox::down-arrow {{ image: url({flecha}); width: 14px; height: 14px; }}
QComboBox::down-arrow:disabled {{ image: url({flecha_dis}); }}
QComboBox QAbstractItemView {{
    background: {t['surface']}; border: 1px solid {t['line']}; outline: none; padding: 4px;
    selection-background-color: {t['surface-sunken']}; selection-color: {t['ink']};
}}
QSpinBox, QDoubleSpinBox, QDateEdit, QTimeEdit {{ padding: 7px 28px 7px 12px; min-height: 0; }}
QSpinBox:focus, QDoubleSpinBox:focus, QDateEdit:focus, QTimeEdit:focus {{ padding: 6px 27px 6px 11px; }}
QAbstractSpinBox QLineEdit, QAbstractSpinBox QLineEdit:focus, QAbstractSpinBox QLineEdit:disabled {{
    padding: 0; min-height: 0; border: none; border-radius: 0; background: transparent; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-origin: border; subcontrol-position: top right;
    width: 24px; border: none; background: transparent; margin: 3px 3px 0 0; }}
QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-origin: border; subcontrol-position: bottom right;
    width: 24px; border: none; background: transparent; margin: 0 3px 3px 0; }}
QSpinBox::up-button:hover, QSpinBox::down-button:hover, QDoubleSpinBox::up-button:hover,
QDoubleSpinBox::down-button:hover {{ background: {t['surface-sunken']}; border-radius: 6px; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url({sube}); width: 10px; height: 10px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url({baja}); width: 10px; height: 10px; }}
QPlainTextEdit, QTextEdit {{
    background: {t['field']}; border: 1px solid {t['line-control']}; border-radius: 12px; padding: 6px 8px;
    color: {t['ink']}; selection-background-color: {t['focus']}; selection-color: #ffffff;
}}
QPlainTextEdit:focus, QTextEdit:focus {{ border: 2px solid {t['focus']}; background: {t['surface']}; padding: 5px 7px; }}
QTextBrowser {{ background: transparent; border: none; padding: 0; }}
QLineEdit#NavSearch {{ background: {t['field']}; border: 1px solid {t['line']}; border-radius: 12px;
    padding: 5px 8px; min-height: 22px; }}
QLineEdit#NavSearch:focus {{ background: {t['surface']}; border: 2px solid {t['focus']}; padding: 4px 7px; }}
QLineEdit#CommandInput, QLineEdit#PaletteInput {{ background: transparent; border: none; padding: 0;
    min-height: 22px; font-size: 15px; }}
QLineEdit#PaletteInput {{ font-size: 16px; }}

/* pantallas sin migrar (columna única): controles a 13 px, como los de las barras densas del sistema */
QWidget[denso="true"] QPushButton, QWidget[denso="true"] QComboBox, QWidget[denso="true"] QLineEdit,
QWidget[denso="true"] QSpinBox, QWidget[denso="true"] QDoubleSpinBox, QWidget[denso="true"] QCheckBox,
QWidget[denso="true"] QRadioButton, QWidget[denso="true"] QLabel, QWidget[denso="true"] QToolButton {{
    font-size: 13px; }}
QWidget[denso="true"] QPushButton {{ padding: 9px 12px; }}
QWidget[denso="true"] QComboBox {{ padding: 8px 30px 8px 10px; }}
QWidget[denso="true"] QComboBox:focus {{ padding: 7px 29px 7px 9px; }}

/* campos compactos dentro de los paneles del visor */
#SidePanel QLineEdit, #SidePanel QComboBox, #SidePanel QSpinBox, #SidePanel QDoubleSpinBox,
#EditorToolbar QComboBox, #EditorToolbar QSpinBox, #EditorHeader QComboBox, #EditorFooter QComboBox,
#EditorFooter QSpinBox, #EditorFooter QLineEdit {{
    border-radius: 8px; padding: 4px 8px; min-height: 22px; }}
#SidePanel QLineEdit:focus, #SidePanel QComboBox:focus, #SidePanel QSpinBox:focus,
#SidePanel QDoubleSpinBox:focus, #EditorToolbar QComboBox:focus, #EditorToolbar QSpinBox:focus,
#EditorFooter QComboBox:focus, #EditorFooter QSpinBox:focus, #EditorFooter QLineEdit:focus {{ padding: 3px 7px; }}
#SidePanel QComboBox, #EditorToolbar QComboBox, #EditorFooter QComboBox, #EditorHeader QComboBox {{
    padding-right: 28px; }}
#SidePanel QSpinBox, #SidePanel QDoubleSpinBox, #EditorToolbar QSpinBox, #EditorFooter QSpinBox {{
    padding: 3px 26px 3px 8px; }}
#SidePanel QSpinBox:focus, #SidePanel QDoubleSpinBox:focus, #EditorToolbar QSpinBox:focus,
#EditorFooter QSpinBox:focus {{ padding: 2px 25px 2px 7px; }}

/* casillas y botones de opción */
QCheckBox, QRadioButton {{ spacing: 10px; padding: 3px 0; background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator, QTreeView::indicator, QListView::indicator {{
    width: 18px; height: 18px; }}
QCheckBox::indicator:unchecked, QTreeView::indicator:unchecked, QListView::indicator:unchecked {{
    image: url({chk_off}); }}
QCheckBox::indicator:unchecked:disabled {{ image: url({chk_off_dis}); }}
QCheckBox::indicator:checked, QTreeView::indicator:checked, QListView::indicator:checked {{ image: url({chk_on}); }}
QCheckBox::indicator:checked:disabled {{ image: url({chk_on_dis}); }}
QCheckBox::indicator:indeterminate {{ image: url({chk_on}); }}
QRadioButton::indicator:unchecked {{ image: url({rad_off}); }}
QRadioButton::indicator:unchecked:disabled {{ image: url({rad_off_dis}); }}
QRadioButton::indicator:checked {{ image: url({rad_on}); }}
QRadioButton::indicator:checked:disabled {{ image: url({rad_on_dis}); }}
QCheckBox:disabled, QRadioButton:disabled {{ color: {dis('ink')}; }}
QRadioButton#OptionCard {{
    background: {t['surface']}; border: 1px solid {t['line']}; border-radius: 12px; padding: 12px 14px; }}
QRadioButton#OptionCard:hover {{ border-color: {t['line-strong']}; }}
QRadioButton#OptionCard:checked {{ border: 2px solid {t['ink']}; background: {t['surface-sunken']}; padding: 11px 13px; }}

/* ================================================================ listas y tablas */
QTreeWidget, QListWidget, QTreeView, QListView, QTableWidget, QTableView {{
    background: {t['surface']}; border: 1px solid {t['line']}; border-radius: 12px; padding: 4px; outline: none;
    alternate-background-color: {t['surface-hover']};
}}
QTreeWidget::item, QTreeView::item {{ padding: 6px 4px; border: none; }}
QTreeWidget::item:selected, QListWidget::item:selected, QTreeView::item:selected, QListView::item:selected,
QTableView::item:selected {{ background: {t['surface-sunken']}; color: {t['ink']}; }}
QTreeWidget::item:hover:!selected, QListWidget::item:hover:!selected {{ background: {t['surface-hover']}; }}
QListWidget#PageGrid {{ background: {t['surface-sunken']}; border: none; border-radius: 0; padding: 10px; }}
QListWidget#ThumbList {{ background: {t['surface']}; border: none; padding: 4px 0; }}
QTreeWidget#FileCards {{ background: transparent; border: none; padding: 0; }}
QTreeWidget#FileCards::item, QTreeWidget#FileCards::item:selected, QTreeWidget#FileCards::item:hover {{
    background: transparent; border: none; padding: 0; }}
QHeaderView {{ background: transparent; border: none; }}
QHeaderView::section {{
    background: {t['surface']}; border: none; border-bottom: 1px solid {t['line']}; padding: 8px;
    color: {t['ink-3']}; font-weight: 600; font-size: 12px;
}}
QTableCornerButton::section {{ background: {t['surface']}; border: none; }}

/* pestañas */
QTabWidget::pane {{ border: none; background: transparent; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{ background: transparent; border: none; border-bottom: 2px solid transparent; padding: 8px 12px;
    color: {t['ink-2']}; font-size: 13px; font-weight: 500; }}
QTabBar::tab:hover {{ color: {t['ink']}; }}
QTabBar::tab:selected {{ color: {t['ink']}; border-bottom-color: {t['ink']}; font-weight: 600; }}
QGroupBox {{ border: 1px solid {t['line']}; border-radius: 12px; margin-top: 12px; padding: 12px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {t['ink']}; font-weight: 600; }}
QSplitter::handle {{ background: {t['line']}; }}

/* ================================================================ progreso, deslizadores, barras */
QProgressBar {{ border: none; background: {t['surface-sunken']}; border-radius: 3px; max-height: 6px;
    min-height: 6px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {t['brand']}; border-radius: 3px; }}
QProgressDialog QProgressBar {{ max-height: 8px; min-height: 8px; }}
QSlider::groove:horizontal {{ height: 4px; background: {t['surface-sunken']}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {t['ink']}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {t['ink']}; width: 16px; height: 16px; margin: -6px 0; border-radius: 8px; }}
QSlider::handle:horizontal:disabled {{ background: {t['line-strong']}; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t['line-strong']}; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {t['line-control']}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {t['line-strong']}; border-radius: 3px; min-width: 30px; }}
QScrollBar::handle:horizontal:hover {{ background: {t['line-control']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QToolTip {{ background: {t['ink']}; color: {t['surface']}; border: none; padding: 6px 8px; font-size: 12px; }}
QMenu {{ background: {t['surface']}; border: 1px solid {t['line']}; padding: 6px; }}
QMenu::item {{ padding: 7px 24px 7px 12px; border-radius: 8px; color: {t['ink']}; }}
QMenu::item:selected {{ background: {t['surface-sunken']}; color: {t['ink']}; }}
QMenu::item:disabled {{ color: {dis('ink')}; }}
QMenu::separator {{ height: 1px; background: {t['line']}; margin: 4px 8px; }}
QMenu::icon {{ padding-left: 6px; }}
QStatusBar {{ background: {t['surface']}; border-top: 1px solid {t['line']}; color: {t['ink-2']}; }}
QStatusBar QLabel {{ color: {t['ink-2']}; }}
{_por_categoria(t)}
"""


class _Ajustes(QObject):
    """Lo que QSS no alcanza: el texto de los campos numéricos queda pegado al borde izquierdo (Qt no aplica
    ``padding-left`` a su campo interno), así que se le da margen al pulirlos."""

    def eventFilter(self, obj, e):  # noqa: N802
        if e.type() == QEvent.Type.Polish and isinstance(obj, QAbstractSpinBox):
            le = obj.lineEdit()
            if le is not None:
                compacto = any(w.objectName() in ("SidePanel", "EditorToolbar", "EditorFooter", "EditorHeader")
                               for w in _antecesores(obj))
                le.setTextMargins(7 if compacto else 11, 0, 0, 0)
        return False


def _antecesores(w):
    w = w.parentWidget()
    while w is not None:
        yield w
        w = w.parentWidget()


_ajustes: _Ajustes | None = None


def instalar_ajustes(app) -> None:
    """Instala (una vez) los ajustes de estilo que QSS no puede hacer.  Lo llama la ventana principal."""
    global _ajustes
    if _ajustes is None and app is not None:
        _ajustes = _Ajustes(app)
        app.installEventFilter(_ajustes)


QSS = build_qss(tema.CLARO)
