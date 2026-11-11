"""Paneles del visor: marcadores con búsqueda, menú «Vista» y confirmación de enlaces web."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QActionGroup, QColor, QFont
from PySide6.QtWidgets import (QFrame, QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from . import art, tema
from .visor_texto import normalizar

R_PAGE = Qt.ItemDataRole.UserRole
R_Y = Qt.ItemDataRole.UserRole + 1
R_COLLAPSE = Qt.ItemDataRole.UserRole + 2
MARK_COLOR = QColor(tema.tonos("ver")["ink"])      # el marcador de la sección que se lee: tinta de «ver»


class PanelMarcadores(QWidget):
    """Árbol de marcadores (índice) del PDF, con un buscador que no distingue tildes ni mayúsculas."""
    ir_a = Signal(int, object)   # página (base 0), altura de destino o None

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.filtro = QLineEdit()
        self.filtro.setPlaceholderText("Buscar en marcadores")
        self.filtro.setClearButtonEnabled(True)
        self.filtro.addAction(art.glyph_icon("search", tema.color("ink-3"), 16),
                              QLineEdit.ActionPosition.LeadingPosition)
        self.filtro.textChanged.connect(self.filtrar)
        lay.addWidget(self.filtro)
        self.arbol = QTreeWidget()
        self.arbol.setColumnCount(2)
        self.arbol.setHeaderHidden(True)
        self.arbol.setFrameShape(QFrame.Shape.NoFrame)
        self.arbol.setIndentation(12)
        self.arbol.setUniformRowHeights(True)
        self.arbol.setExpandsOnDoubleClick(False)
        self.arbol.setStyleSheet(f"QTreeWidget {{ border: none; padding: 0; background: {tema.color('surface')}; }}"
                                 " QTreeWidget::item { padding: 5px 2px; border-radius: 6px; }")
        hdr = self.arbol.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.arbol.itemClicked.connect(self._activar)
        self.arbol.itemActivated.connect(self._activar)
        lay.addWidget(self.arbol, 1)
        self.aviso = QLabel("Este PDF no tiene marcadores.")
        self.aviso.setObjectName("Hint")
        self.aviso.setWordWrap(True)
        self.aviso.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        lay.addWidget(self.aviso)
        self.entradas: list[QTreeWidgetItem] = []   # en el orden del documento
        self._expandidos: list[tuple[QTreeWidgetItem, bool]] | None = None
        self._marcado: QTreeWidgetItem | None = None
        self.cargar([], 0)

    # ---------------------------------------------------------------- datos
    @property
    def total(self) -> int:
        return len(self.entradas)

    def cargar(self, toc: list, n_paginas: int) -> None:
        """``toc`` es ``doc.get_toc(simple=False)``: [nivel, título, página (base 1), destino]."""
        self.filtro.blockSignals(True)
        self.filtro.clear()
        self.filtro.blockSignals(False)
        self._expandidos = None
        self._marcado = None
        self.arbol.clear()
        self.entradas = []
        pila: list[tuple[int, QTreeWidgetItem]] = []
        for entry in toc or []:
            try:
                nivel, titulo, pagina = int(entry[0]), str(entry[1]), int(entry[2])
            except (IndexError, TypeError, ValueError):
                continue
            dest = entry[3] if len(entry) > 3 and isinstance(entry[3], dict) else {}
            titulo = " ".join(titulo.split()) or "(sin título)"
            while pila and pila[-1][0] >= nivel:
                pila.pop()
            item = QTreeWidgetItem([titulo, ""])
            valida = 1 <= pagina <= n_paginas
            y = None
            if valida:
                item.setText(1, str(pagina))
                item.setForeground(1, tema.qcolor("ink-3"))
                item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                item.setToolTip(0, f"{titulo}\nPágina {pagina}")
                to = dest.get("to")
                try:
                    y = float(to.y) if to is not None else None
                except (AttributeError, TypeError, ValueError):
                    y = None
            else:
                item.setForeground(0, tema.qcolor("ink-3"))
                item.setToolTip(0, f"{titulo}\nEste marcador no lleva a ninguna página de este documento.")
            item.setData(0, R_PAGE, pagina - 1 if valida else -1)
            item.setData(0, R_Y, y)
            if pila:
                pila[-1][1].addChild(item)
            else:
                self.arbol.addTopLevelItem(item)
            item.setData(0, R_COLLAPSE, bool(dest.get("collapse", False)))
            pila.append((nivel, item))
            self.entradas.append(item)
        for item in self.entradas:
            item.setExpanded(not item.data(0, R_COLLAPSE))
        vacio = not self.entradas
        self.aviso.setText("Este PDF no tiene marcadores.")
        self.aviso.setVisible(vacio)
        self.arbol.setVisible(not vacio)
        self.filtro.setVisible(not vacio)

    def visibles(self) -> list[QTreeWidgetItem]:
        return [it for it in self.entradas if not it.isHidden()]

    # ---------------------------------------------------------------- búsqueda
    def filtrar(self, texto: str) -> None:
        q = normalizar(texto.strip())
        if not q:
            for it in self.entradas:
                it.setHidden(False)
            if self._expandidos is not None:
                for it, on in self._expandidos:
                    it.setExpanded(on)
            self._expandidos = None
            self.aviso.setVisible(not self.entradas)
            self.arbol.setVisible(bool(self.entradas))
            return
        if self._expandidos is None:
            self._expandidos = [(it, it.isExpanded()) for it in self.entradas]

        def visitar(item: QTreeWidgetItem) -> bool:
            hijos = [visitar(item.child(k)) for k in range(item.childCount())]
            coincide = q in normalizar(item.text(0))
            if any(hijos):
                item.setExpanded(True)
            item.setHidden(not (coincide or any(hijos)))
            return coincide or any(hijos)

        hay = [visitar(self.arbol.topLevelItem(k)) for k in range(self.arbol.topLevelItemCount())]
        self.arbol.setVisible(any(hay))
        self.aviso.setText("Ningún marcador coincide con la búsqueda.")
        self.aviso.setVisible(not any(hay))

    # ---------------------------------------------------------------- lectura
    def marcar_actual(self, pagina: int) -> QTreeWidgetItem | None:
        """Señala el marcador de la sección que se está leyendo (el último que empieza en o antes de
        ``pagina``). Si está dentro de una sección contraída, se señala esa sección."""
        actual = None
        for it in self.entradas:
            p = it.data(0, R_PAGE)
            if isinstance(p, int) and 0 <= p <= pagina:
                actual = it
        if actual is not None:
            cadena = []
            parent = actual.parent()
            while parent is not None:
                cadena.append(parent)
                parent = parent.parent()
            for anc in reversed(cadena):
                if not anc.isExpanded():
                    actual = anc
                    break
        if actual is self._marcado:
            return actual
        for it in (self._marcado, actual):
            if it is None:
                continue
            on = it is actual
            f = QFont(it.font(0))
            f.setBold(on)
            it.setFont(0, f)
            if it.data(0, R_PAGE) is not None and it.data(0, R_PAGE) >= 0:
                it.setForeground(0, MARK_COLOR if on else tema.qcolor("ink"))
        self._marcado = actual
        if actual is not None and self.isVisible():
            self.arbol.scrollToItem(actual)
        return actual

    def _activar(self, item: QTreeWidgetItem, _col: int = 0) -> None:
        p = item.data(0, R_PAGE)
        if isinstance(p, int) and p >= 0:
            self.ir_a.emit(p, item.data(0, R_Y))


class MenuVista(QMenu):
    """Menú «Vista» del pie del visor."""

    def __init__(self, parent=None):
        super().__init__(parent)
        grupo = QActionGroup(self)
        grupo.setExclusive(True)
        self.act_una = self.addAction("Una página")
        self.act_dos = self.addAction("Dos páginas")
        for a in (self.act_una, self.act_dos):
            a.setCheckable(True)
            grupo.addAction(a)
        self.act_una.setChecked(True)
        self.act_portada = self.addAction("Portada sola")
        self.act_portada.setCheckable(True)
        self.act_portada.setToolTip("La primera página queda sola, como la portada de un libro")
        self.addSeparator()
        self.act_lectura = self.addAction("Modo lectura\tCtrl+H")
        self.act_lectura.setCheckable(True)
        self.act_completa = self.addAction("Pantalla completa\tF11")
        self.act_completa.setCheckable(True)
        self.addSeparator()
        self.act_noche = self.addAction("Modo noche")
        self.act_noche.setCheckable(True)
        self.act_girar = self.addAction(art.glyph_icon("rotate_right", tema.color("ink-2"), 16),
                                        "Girar la vista\tCtrl+R")
        self.setToolTipsVisible(True)


def confirmar_web(parent: QWidget | None, url: str) -> bool:
    """Pide confirmación antes de abrir un enlace web: RonyPDF no se conecta a internet."""
    box = QMessageBox(parent)
    box.setWindowTitle("Abrir enlace")
    box.setIcon(QMessageBox.Icon.Question)
    box.setTextFormat(Qt.TextFormat.PlainText)
    if url.lower().startswith("mailto:"):
        box.setText(f"Este enlace abre tu programa de correo para escribir a: {url[7:]}.\n\n"
                    "RonyPDF no se conecta a internet; lo abrirá ese programa.")
    else:
        box.setText(f"Este enlace abre una página en tu navegador: {url}.\n\n"
                    "RonyPDF no se conecta a internet; lo abrirá tu navegador.")
    abrir = box.addButton("Abrir", QMessageBox.ButtonRole.AcceptRole)
    cancelar = box.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(cancelar)
    box.setEscapeButton(cancelar)
    box.exec()
    return box.clickedButton() is abrir
