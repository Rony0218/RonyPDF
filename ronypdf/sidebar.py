"""Menú lateral (``rp-nav``) y su carril de categorías (``rp-rail``).

Por defecto se ve el carril de 64 px: logo, Inicio, un azulejo por categoría y, abajo, Respaldo.  Tocar una
categoría abre un menú flotante con sus herramientas.  El menú completo de 248 px (grupos por categoría,
buscador y contadores) sigue disponible con el botón de expandir, y se recuerda la elección.
"""

from __future__ import annotations

import unicodedata

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QIcon, QPainter, QPen
from PySide6.QtWidgets import (QAbstractButton, QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QScrollArea, QSizePolicy, QStackedLayout, QToolButton,
                               QVBoxLayout, QWidget)

from . import APP_NAME, art, catalogo, tema
from .componentes import Count, GlyphTile, Kbd
from .icons import icon, pixmap

EXPANDED_W = 248
RAIL_W = 64


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


# ---------------------------------------------------------------- menú expandido

class SectionHeader(QAbstractButton):
    """Encabezado de grupo (``rp-nav-group``): punto del color de la categoría, nombre, contador cuando está
    cerrado y flecha.  Se puede abrir y cerrar."""

    def __init__(self, label: str, count: int, cat: str | None = None):
        super().__init__()
        self.label = label
        self.count = count
        self.cat = cat
        self.setCheckable(True)
        self.setChecked(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFixedHeight(34)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAccessibleName(label)
        self.setToolTip(f"Mostrar u ocultar «{label}»")

    def sizeHint(self) -> QSize:
        return QSize(200, 34)

    def paintEvent(self, e):  # noqa: N802
        t = tema.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0, 10, 0, 0)
        if self.underMouse() or self.hasFocus():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t["surface-hover"]))
            p.drawRoundedRect(r.adjusted(0, -2, 0, 0), 8, 8)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(tema.tonos(self.cat)["c"]))
        p.drawRoundedRect(QRectF(8, r.center().y() - 4, 8, 8), 3, 3)
        p.setFont(tema.fuente("chip", self.font()))
        p.setPen(QColor(t["ink-3"]))
        p.drawText(r.adjusted(24, 0, -40, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.label)
        right = r.right() - 10
        cx, cy = right - 4, r.center().y()
        pen = QPen(QColor(t["ink-3"]), 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        if self.isChecked():
            p.drawPolyline([QPointF(cx - 3.5, cy - 1.5), QPointF(cx, cy + 2), QPointF(cx + 3.5, cy - 1.5)])
        else:
            p.drawPolyline([QPointF(cx - 1.5, cy - 3.5), QPointF(cx + 2, cy), QPointF(cx - 1.5, cy + 3.5)])
            Count.pintar(p, QPointF(right - 14, cy), self.count, quiet=True)
        p.end()


class NavItem(QPushButton):
    """Elemento del menú (``rp-nav-item``): azulejo xs y nombre; activo = banda y tinta de su categoría."""

    def __init__(self, key: str, title: str, tile_key: str, cat: str):
        super().__init__("  " + title)
        self.key = key
        self.count: int | str | None = None
        self.setObjectName("NavItem")
        self.setProperty("cat", cat)
        self.setAccessibleName(title)
        if key == "home":
            ic = QIcon()
            ic.addPixmap(art.glyph("home", 20, tema.color("ink-2")), QIcon.Mode.Normal, QIcon.State.Off)
            ic.addPixmap(art.glyph("home", 20, tema.color("brand-ink")), QIcon.Mode.Normal, QIcon.State.On)
            self.setIcon(ic)
            self.setIconSize(QSize(20, 20))
        else:
            self.setIcon(art.glyph_tile_icon(tile_key, tema.TILE_XS, cat))
            self.setIconSize(QSize(tema.TILE_XS, tema.TILE_XS))
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_count(self, n) -> None:
        self.count = n or None
        self.update()

    def paintEvent(self, e):  # noqa: N802
        super().paintEvent(e)
        if self.count:
            p = QPainter(self)
            Count.pintar(p, QPointF(self.width() - 8, self.height() / 2), self.count)
            p.end()


# ---------------------------------------------------------------- carril

class RailItem(QAbstractButton):
    """Entrada del carril (``rp-rail-item``): 44 × 44, azulejo sm de la categoría o un glifo de línea."""

    def __init__(self, title: str, tile_key: str | None = None, cat: str | None = None, glyph: str | None = None):
        super().__init__()
        self.tile_key, self.cat, self.glyph = tile_key, cat, glyph
        self.setCheckable(True)
        self.setFixedSize(44, 44)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setToolTip(title)
        self.setAccessibleName(title)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._kbd = False

    def focusInEvent(self, e):  # noqa: N802
        self._kbd = e.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason)
        super().focusInEvent(e)

    def focusOutEvent(self, e):  # noqa: N802
        self._kbd = False
        super().focusOutEvent(e)

    def paintEvent(self, e):  # noqa: N802
        t = tema.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        if self.isChecked():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(tema.tonos(self.cat)["band"] if self.cat else t["surface-sunken"]))
            p.drawRoundedRect(r, 12, 12)
        elif self.underMouse():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t["surface-hover"]))
            p.drawRoundedRect(r, 12, 12)
        if self.hasFocus() and self._kbd:
            p.setPen(QPen(QColor(t["focus"]), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 11, 11)
        dpr = self.devicePixelRatioF()
        if self.tile_key:
            pm = art.glyph_tile(self.tile_key, tema.TILE_SM, self.cat, dpr=dpr)
            p.drawPixmap(QPointF((44 - tema.TILE_SM) / 2, (44 - tema.TILE_SM) / 2), pm)
        elif self.glyph:
            activo = self.isChecked() and self.cat == "marca"
            color = t["brand-ink"] if activo else t["ink"] if self.isChecked() else t["ink-2"]
            if self.glyph in ("panel_open", "panel_close", "clock"):
                pm = pixmap(self.glyph, color, 22, 1.7)
            else:
                pm = art.glyph(self.glyph, 22, color, dpr)
            p.drawPixmap(QPointF(11, 11), pm)
        p.end()


class FilaMenu(QAbstractButton):
    """Fila del menú flotante de una categoría: azulejo xs y nombre; la herramienta abierta, en su banda."""

    def __init__(self, key: str, title: str, tile_key: str, cat: str, activa: bool, count=None):
        super().__init__()
        self.key, self.tile_key, self.cat, self.activa, self.count = key, tile_key, cat, activa, count
        self.setText(title)
        self.setFixedHeight(36)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(title)
        self._kbd = False

    def focusInEvent(self, e):  # noqa: N802
        self._kbd = e.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason)
        super().focusInEvent(e)

    def focusOutEvent(self, e):  # noqa: N802
        self._kbd = False
        super().focusOutEvent(e)

    def sizeHint(self):  # noqa: N802
        return QSize(44 + QFontMetrics(tema.fuente("body-strong")).horizontalAdvance(self.text()) + 40, 36)

    def keyPressEvent(self, e):  # noqa: N802
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.click()
            return
        super().keyPressEvent(e)

    def paintEvent(self, e):  # noqa: N802
        t = tema.tokens()
        ton = tema.tonos(self.cat)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        if self.activa:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(ton["band"]))
            p.drawRoundedRect(r, 8, 8)
        elif self.underMouse() or (self.hasFocus() and self._kbd):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t["surface-sunken"] if self.hasFocus() and self._kbd else t["surface-hover"]))
            p.drawRoundedRect(r, 8, 8)
        p.drawPixmap(QPointF(8, 7), art.glyph_tile(self.tile_key, tema.TILE_XS, self.cat,
                                                   dpr=self.devicePixelRatioF()))
        f = tema.fuente("body-strong" if self.activa else "body", self.font())
        p.setFont(f)
        p.setPen(QColor(ton["ink"] if self.activa else t["ink"]))
        p.drawText(r.adjusted(40, 0, -36 if self.count else -8, 0), Qt.AlignmentFlag.AlignVCenter, self.text())
        if self.count:
            Count.pintar(p, QPointF(r.right() - 8, r.center().y()), self.count)
        p.end()


class MenuCategoria(QWidget):
    """Menú flotante con las herramientas de una categoría (se abre desde el carril)."""

    elegida = Signal(str)
    MARGEN = (34, 22, 34, 50)      # aire transparente para la sombra shadow-3

    def __init__(self, parent: QWidget, cat: str, label: str, items: list[tuple[str, str, str]], actual: str,
                 counts: dict | None = None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.cat = cat
        outer = QVBoxLayout(self)
        outer.setContentsMargins(*self.MARGEN)
        caja = QFrame()
        caja.setObjectName("Flyout")
        tema.sombra(caja, 3)
        outer.addWidget(caja)
        lay = QVBoxLayout(caja)
        lay.setContentsMargins(6, 8, 6, 6)
        lay.setSpacing(2)
        head = QHBoxLayout()
        head.setContentsMargins(8, 2, 8, 6)
        head.setSpacing(8)
        dot = QLabel()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(f"background: {tema.tonos(cat)['c']}; border-radius: 3px;")
        head.addWidget(dot)
        t = QLabel(label)
        t.setObjectName("FlyoutTitle")
        head.addWidget(t, 1)
        lay.addLayout(head)
        self.filas: list[FilaMenu] = []
        counts = counts or {}
        for key, title, tile_key in items:
            f = FilaMenu(key, title, tile_key, cat, key == actual, counts.get(key))
            f.clicked.connect(lambda _=False, k=key: self._elegir(k))
            lay.addWidget(f)
            self.filas.append(f)
        self.setMinimumWidth(max(260, max((f.sizeHint().width() for f in self.filas), default=0) + 40))

    def _elegir(self, key: str):
        self.close()
        self.elegida.emit(key)

    def abrir(self, junto_a: QWidget) -> None:
        self.adjustSize()
        izq, arriba = self.MARGEN[0], self.MARGEN[1]
        g = junto_a.mapToGlobal(QPoint(junto_a.width() + 10 - izq, -8 - arriba))
        pantalla = junto_a.screen().availableGeometry() if junto_a.screen() else None
        ventana = junto_a.window().frameGeometry()
        y = min(g.y(), ventana.bottom() - self.height() + self.MARGEN[3])
        if pantalla is not None:
            y = min(y, pantalla.bottom() - self.height() + self.MARGEN[3])
        self.move(QPoint(g.x(), max(ventana.top() - arriba, y)))
        self.show()
        activa = next((f for f in self.filas if f.activa), self.filas[0] if self.filas else None)
        if activa is not None:
            activa.setFocus(Qt.FocusReason.PopupFocusReason)

    def keyPressEvent(self, e):  # noqa: N802
        if e.key() == Qt.Key.Key_Escape:
            self.close()
            return
        if e.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up) and self.filas:
            actual = next((i for i, f in enumerate(self.filas) if f.hasFocus()), -1)
            paso = 1 if e.key() == Qt.Key.Key_Down else -1
            self.filas[(actual + paso) % len(self.filas)].setFocus(Qt.FocusReason.TabFocusReason)
            for f in self.filas:
                f.update()
            return
        super().keyPressEvent(e)


# ---------------------------------------------------------------- menú lateral

class Sidebar(QFrame):
    navigate = Signal(str)

    def __init__(self, home: tuple[str, str, str], sections: list[tuple[str, str, str, list[tuple[str, str, str]]]],
                 settings=None):
        """``home`` = (clave, título, azulejo); ``sections`` = [(clave, título, azulejo, [(clave, título, azulejo)])]."""
        super().__init__()
        self.setObjectName("Sidebar")
        self.settings = settings
        self.sections = sections
        self.home = home
        self.items: dict[str, QPushButton] = {}
        self.rail_buttons: dict[str, QAbstractButton] = {}
        self.headers: dict[str, SectionHeader] = {}
        self.bodies: dict[str, QWidget] = {}
        self.page_section: dict[str, str] = {}
        self.counts: dict[str, int] = {}
        self.current = home[0]
        self._collapsed = False
        self.flyout: MenuCategoria | None = None

        self.stack = QStackedLayout(self)
        self.stack.addWidget(self._build_expanded(home))
        self.stack.addWidget(self._build_rail(home))

        saved_open = (settings.get("sidebar_open") if settings else None) or {}
        for key, header in self.headers.items():
            self._set_open(key, saved_open.get(key, True), persist=False)
        self.set_collapsed(bool(settings.get("sidebar_collapsed", True)) if settings else True, remember=False)

    # ------------------------------------------------------------------ construcción
    def _nav_button(self, key: str, title: str, tile_key: str, cat: str) -> QPushButton:
        b = NavItem(key, title, tile_key, cat)
        b.clicked.connect(lambda: self.navigate.emit(key))
        self.items[key] = b
        return b

    def _icon_button(self, glyph: str, tip: str, cb) -> QToolButton:
        b = QToolButton()
        b.setObjectName("IconButton")
        b.setIcon(icon(glyph, tema.color("ink-2"), size=18))
        b.setIconSize(QSize(18, 18))
        b.setToolTip(tip)
        b.setAccessibleName(tip)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(cb)
        return b

    def _build_expanded(self, home) -> QWidget:
        w = QWidget()
        w.setObjectName("SidebarBody")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 14, 12, 12)
        lay.setSpacing(6)

        brand = QHBoxLayout()
        brand.setContentsMargins(6, 2, 0, 6)
        brand.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(art.brand_logo(34))
        brand.addWidget(logo)
        name = QLabel(APP_NAME)
        name.setObjectName("NavName")
        brand.addWidget(name, 1)
        self.b_collapse = self._icon_button("panel_close", "Contraer menú", lambda: self.set_collapsed(True))
        brand.addWidget(self.b_collapse)
        lay.addLayout(brand)

        self.search = QLineEdit()
        self.search.setObjectName("NavSearch")
        self.search.setPlaceholderText("Buscar herramienta")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icon("search", tema.color("ink-3")), QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(self._filter)
        self.search.returnPressed.connect(self._open_first_match)
        self.kbd = Kbd("Ctrl K", self.search)
        self.search.textChanged.connect(lambda s: self.kbd.setVisible(not s))
        self.search.installEventFilter(self)
        lay.addWidget(self.search)
        lay.addSpacing(4)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("SidebarBody")
        bl = QVBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(2)
        self.home_button = self._nav_button(home[0], home[1], home[2], "marca")
        bl.addWidget(self.home_button)
        for key, label, _tile, items in self.sections:
            header = SectionHeader(label, len(items), key)
            header.toggled.connect(lambda on, k=key: self._set_open(k, on))
            self.headers[key] = header
            bl.addWidget(header)
            box = QWidget()
            box.setObjectName("SidebarBody")
            vl = QVBoxLayout(box)
            vl.setContentsMargins(0, 0, 0, 0)
            vl.setSpacing(2)
            for pkey, title, tkey in items:
                vl.addWidget(self._nav_button(pkey, title, tkey, key))
                self.page_section[pkey] = key
            self.bodies[key] = box
            bl.addWidget(box)
        bl.addStretch(1)
        scroll.setWidget(body)
        lay.addWidget(scroll, 1)
        return w

    def _build_rail(self, home) -> QWidget:
        w = QWidget()
        w.setObjectName("Rail")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 14, 0, 14)
        lay.setSpacing(6)
        lay.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        logo = QLabel()
        logo.setPixmap(art.brand_logo(36))
        logo.setToolTip(APP_NAME)
        lay.addWidget(logo, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addSpacing(8)

        b = RailItem(home[1], glyph="home", cat="marca")
        b.clicked.connect(lambda: self.navigate.emit(home[0]))
        self.rail_buttons[home[0]] = b
        lay.addWidget(b, 0, Qt.AlignmentFlag.AlignHCenter)
        sep = QFrame()
        sep.setObjectName("RailSep")
        sep.setFixedSize(28, 1)
        lay.addSpacing(4)
        lay.addWidget(sep, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addSpacing(4)
        for key, label, tkey, _items in self.sections:
            rb = RailItem(label, tile_key=tkey, cat=key)
            rb.clicked.connect(lambda _=False, k=key: self.abrir_categoria(k))
            self.rail_buttons["sec:" + key] = rb
            lay.addWidget(rb, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addStretch(1)
        if any(k == "backup" for _s, _l, _t, items in self.sections for k, _ti, _a in items):
            rb = RailItem("Respaldo y recuperación", glyph="clock", cat="seguridad")
            rb.clicked.connect(lambda: self.navigate.emit("backup"))
            self.rail_buttons["backup"] = rb
            lay.addWidget(rb, 0, Qt.AlignmentFlag.AlignHCenter)
        rb = RailItem("Expandir menú", glyph="panel_open")
        rb.setCheckable(False)
        rb.clicked.connect(lambda: self.set_collapsed(False))
        self.rail_buttons["expand"] = rb
        lay.addWidget(rb, 0, Qt.AlignmentFlag.AlignHCenter)
        return w

    def eventFilter(self, obj, e):  # noqa: N802
        if obj is self.search and e.type() == e.Type.Resize:
            k = self.kbd
            k.adjustSize()
            k.move(obj.width() - k.width() - 10, (obj.height() - k.height()) // 2)
        return False

    # ------------------------------------------------------------------ carril: menú flotante
    def abrir_categoria(self, key: str) -> MenuCategoria | None:
        """Abre el menú flotante de la categoría ``key`` junto a su entrada del carril."""
        sec = next((s for s in self.sections if s[0] == key), None)
        if sec is None:
            return None
        if self.flyout is not None:
            try:
                self.flyout.close()
            except RuntimeError:
                pass
        _k, label, _t, items = sec
        menu = MenuCategoria(self, key, label, items, self.current, self.counts)
        menu.elegida.connect(self.navigate.emit)
        menu.destroyed.connect(lambda *_: self._sync_checks_seguro())
        self.flyout = menu
        boton = self.rail_buttons.get("sec:" + key)
        if boton is not None:
            boton.setChecked(True)
        menu.abrir(boton if boton is not None else self)
        return menu

    # ------------------------------------------------------------------ estado
    def is_collapsed(self) -> bool:
        return self._collapsed

    def set_collapsed(self, collapsed: bool, remember: bool = True):
        self._collapsed = collapsed
        self.stack.setCurrentIndex(1 if collapsed else 0)
        self.setFixedWidth(RAIL_W if collapsed else EXPANDED_W)
        if remember and self.settings is not None:
            self.settings.set("sidebar_collapsed", collapsed)
        self._sync_checks()

    def _set_open(self, key: str, on: bool, persist: bool = True):
        header = self.headers[key]
        if header.isChecked() != on:
            header.blockSignals(True)
            header.setChecked(on)
            header.blockSignals(False)
        header.update()
        if not self.search.text():
            self.bodies[key].setVisible(on)
        if persist and self.settings is not None:
            state = dict(self.settings.get("sidebar_open") or {})
            state[key] = on
            self.settings.set("sidebar_open", state)

    def reveal_section(self, key: str):
        self.set_collapsed(False)
        self._set_open(key, True)

    def set_current(self, key: str):
        self.current = key
        sec = self.page_section.get(key)
        if sec and not self.headers[sec].isChecked() and not self._collapsed:
            self._set_open(sec, True)
        self._sync_checks()

    def set_count(self, key: str, n: int | None) -> None:
        """Contador rojo de pendientes reales de una herramienta (archivos recuperados, páginas por revisar)."""
        if n:
            self.counts[key] = n
        else:
            self.counts.pop(key, None)
        if key in self.items:
            self.items[key].set_count(n)

    def _sync_checks_seguro(self):
        # El menú flotante puede destruirse al cerrar la app, cuando las entradas del carril ya no existen.
        try:
            self._sync_checks()
        except RuntimeError:
            pass

    def _sync_checks(self):
        for k, b in self.items.items():
            b.setChecked(k == self.current)
        sec = self.page_section.get(self.current)
        for k, b in self.rail_buttons.items():
            if b.isCheckable():
                b.setChecked(k == self.current or k == f"sec:{sec}")
                b.update()

    # ------------------------------------------------------------------ búsqueda
    def focus_search(self):
        self.set_collapsed(False)
        self.search.setFocus()
        self.search.selectAll()

    def _matches(self, q: str, key: str, title: str, label: str) -> bool:
        if q in _norm(title) or q in _norm(label):
            return True
        return any(q in catalogo.normalizar(s) for s in catalogo.SINONIMOS.get(key, ()))

    def _filter(self, text: str):
        q = _norm(text.strip())
        if not q:
            self.home_button.setVisible(True)
            for key, header in self.headers.items():
                header.setVisible(True)
                self.bodies[key].setVisible(header.isChecked())
                for b in self.bodies[key].findChildren(QPushButton):
                    b.setVisible(True)
            return
        self.home_button.setVisible(q in _norm("inicio"))
        for key, label, _t, items in self.sections:
            any_match = False
            for pkey, title, _tk in items:
                match = self._matches(q, pkey, title, label)
                self.items[pkey].setVisible(match)
                any_match |= match
            self.headers[key].setVisible(any_match)
            self.bodies[key].setVisible(any_match)

    def _open_first_match(self):
        for key, _label, _t, items in self.sections:
            for pkey, _title, _tk in items:
                if not self.items[pkey].isHidden() and not self.bodies[key].isHidden():
                    self.navigate.emit(pkey)
                    self.search.clear()
                    return
        if not self.home_button.isHidden():
            self.navigate.emit("home")
            self.search.clear()

    def keyPressEvent(self, e):  # noqa: N802
        if e.key() == Qt.Key.Key_Escape and self.search.hasFocus():
            self.search.clear()
            return
        super().keyPressEvent(e)


# ---------------------------------------------------------------- barra superior

class Migas(QWidget):
    """Migas de pan «Inicio / Categoría / Herramienta» (en Inicio: el ícono de la casa e «Inicio»)."""

    inicio = Signal()

    def __init__(self):
        super().__init__()
        self.partes: list[str] = []
        self._r_inicio = QRectF()
        self.setMouseTracking(True)
        self.setMinimumWidth(150)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

    def set_ruta(self, categoria: str | None, herramienta: str | None) -> None:
        self.partes = [p for p in (categoria, herramienta) if p]
        self.setAccessibleName(" / ".join(["Inicio"] + self.partes))
        self.update()

    def sizeHint(self):  # noqa: N802
        return QSize(260, 40)

    def ancho_necesario(self) -> int:
        if not self.partes:
            return 26 + QFontMetrics(tema.fuente("body-strong")).horizontalAdvance("Inicio")
        fm = QFontMetrics(tema.fuente("body-sm"))
        fb = tema.fuente("body-sm")
        fb.setWeight(fb.Weight.DemiBold)
        segs = ["Inicio"] + self.partes[:-1]
        w = sum(fm.horizontalAdvance(x) + 16 + fm.horizontalAdvance("/") for x in segs)
        return w + QFontMetrics(fb).horizontalAdvance(self.partes[-1]) + 4

    def mouseMoveEvent(self, e):  # noqa: N802
        sobre = bool(self.partes) and self._r_inicio.contains(e.position())
        self.setCursor(Qt.CursorShape.PointingHandCursor if sobre else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, e):  # noqa: N802
        if self.partes and self._r_inicio.contains(e.position()):
            self.inicio.emit()

    def paintEvent(self, e):  # noqa: N802
        t = tema.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        h = self.height()
        if not self.partes:
            p.drawPixmap(QPointF(0, h / 2 - 9), art.glyph("home", 18, t["ink-2"], self.devicePixelRatioF()))
            p.setFont(tema.fuente("body-strong", self.font()))
            p.setPen(QColor(t["ink"]))
            p.drawText(QRectF(26, 0, self.width() - 26, h), Qt.AlignmentFlag.AlignVCenter, "Inicio")
            p.end()
            return
        f = tema.fuente("body-sm", self.font())
        fb = tema.fuente("body-sm", self.font())
        fb.setWeight(fb.Weight.DemiBold)
        fm, fmb = QFontMetrics(f), QFontMetrics(fb)
        x = 0.0
        p.setFont(f)
        p.setPen(QColor(t["ink-3"]))
        segs = ["Inicio"] + self.partes[:-1]
        for k, s in enumerate(segs):
            w = fm.horizontalAdvance(s)
            p.drawText(QRectF(x, 0, w + 1, h), Qt.AlignmentFlag.AlignVCenter, s)
            if k == 0:
                self._r_inicio = QRectF(x - 2, 0, w + 4, h)
            x += w + 8
            p.drawText(QRectF(x, 0, 10, h), Qt.AlignmentFlag.AlignVCenter, "/")
            x += fm.horizontalAdvance("/") + 8
        ultimo = fmb.elidedText(self.partes[-1], Qt.TextElideMode.ElideRight, int(max(20, self.width() - x)))
        p.setFont(fb)
        p.setPen(QColor(t["ink"]))
        p.drawText(QRectF(x, 0, self.width() - x, h), Qt.AlignmentFlag.AlignVCenter, ultimo)
        p.end()


class BuscadorFalso(QAbstractButton):
    """Parece un buscador («Buscar herramienta o archivo», Ctrl K); al tocarlo abre la paleta."""

    def __init__(self, texto: str = "Buscar herramienta o archivo"):
        super().__init__()
        self.setText(texto)
        self.setAccessibleName(texto)
        self.setToolTip("Buscar herramienta o archivo (Ctrl K)")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFixedHeight(38)
        self.setMaximumWidth(440)
        self.setMinimumWidth(220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self):  # noqa: N802
        return QSize(440, 38)

    def paintEvent(self, e):  # noqa: N802
        t = tema.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(t["focus"] if self.hasFocus() else t["line-strong"] if self.underMouse() else t["line"]),
                      2 if self.hasFocus() else 1))
        p.setBrush(QColor(t["field"]))
        p.drawRoundedRect(r, 12, 12)
        p.drawPixmap(QPointF(12, r.center().y() - 9), pixmap("search", t["ink-3"], 18, 1.7))
        f = tema.fuente("body", self.font())
        p.setFont(f)
        p.setPen(QColor(t["ink-3"]))
        kf = tema.fuente("chip", self.font())
        kf.setPixelSize(11)
        kw = QFontMetrics(kf).horizontalAdvance("Ctrl K") + 12
        p.drawText(QRectF(40, 0, r.width() - 40 - kw - 16, self.height()), Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(f).elidedText(self.text(), Qt.TextElideMode.ElideRight, int(r.width() - kw - 60)))
        kr = QRectF(r.right() - 10 - kw, r.center().y() - 9, kw, 18)
        p.setPen(QPen(QColor(t["line"]), 1))
        p.setBrush(QColor(t["surface-sunken"]))
        p.drawRoundedRect(kr, 6, 6)
        p.setFont(kf)
        p.setPen(QColor(t["ink-2"]))
        p.drawText(kr, Qt.AlignmentFlag.AlignCenter, "Ctrl K")
        p.end()


class TopBar(QFrame):
    """Barra superior de 60 px: migas de pan, buscador (abre la paleta) y el chip «Sin conexión · privado»."""

    buscar = Signal()
    inicio = Signal()

    def __init__(self):
        super().__init__()
        from .componentes import Chip
        self.setObjectName("TopBar")
        self.setFixedHeight(60)
        h = QHBoxLayout(self)
        h.setContentsMargins(28, 0, 24, 0)
        h.setSpacing(16)
        self.migas = Migas()
        self.migas.inicio.connect(self.inicio)
        izq = QWidget()
        il = QHBoxLayout(izq)
        il.setContentsMargins(0, 0, 0, 0)
        il.addWidget(self.migas)
        self.izq = izq
        h.addWidget(izq)
        self.buscador = BuscadorFalso()
        self.buscador.clicked.connect(self.buscar)
        h.addWidget(self.buscador, 1, Qt.AlignmentFlag.AlignHCenter)
        der = QWidget()
        dl = QHBoxLayout(der)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.addStretch(1)
        self.chip = Chip("Sin conexión · privado", "ok", glyph="shield")
        self.chip.setToolTip("RonyPDF funciona sin internet: tus archivos no salen de este equipo.")
        dl.addWidget(self.chip)
        self.der = der
        h.addWidget(der)
        self._lados()

    def _lados(self):
        """Con espacio, los dos costados miden 260 px y el buscador queda centrado.  En una ventana angosta
        las migas de pan tienen prioridad: el buscador se encoge (hasta 300 px) antes de cortarlas."""
        disponible = self.width() - 52 - 32
        necesario = max(170, self.migas.ancho_necesario())
        chip = self.chip.sizeHint().width()
        lado = min(max(260, necesario), (disponible - 360) // 2)
        if lado >= max(necesario, chip):
            izq = der = lado
        else:
            der = chip
            izq = max(150, min(necesario, disponible - der - 300))
        self.izq.setFixedWidth(izq)
        self.der.setFixedWidth(der)

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._lados()

    def set_ruta(self, categoria: str | None, herramienta: str | None) -> None:
        self.migas.set_ruta(categoria, herramienta)
        self._lados()
