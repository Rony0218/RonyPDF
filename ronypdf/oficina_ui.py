"""Pantallas «Word y Excel a PDF» y «PDF a Word y Excel» (lógica en oficina.py).

Las dos usan la plantilla 2.0 (``ToolPage``): pasos a la izquierda, la lista de documentos o la página del PDF
a la derecha y una barra de acción con un solo botón principal.
"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QMessageBox, QPushButton, QStackedWidget, QStyle,
                               QVBoxLayout, QWidget)

from . import core, extensiones, icons, oficina, tema
from .icons import _arrowhead, _doc, _line, icon, pixmap
from .tools import check_spec, pages_edit, pdf_row
from .widgets import (PDF_LOCK, DropArea, FileCardDelegate, FileList, Note, OptionGroup, PreviewPane, Segmented,
                      SingleFilePicker, SwitchRow, ToolPage, ask_open, ask_save, cat_de, ctx, label,
                      pintar_miniatura, show_done, suggest_output)

TIPOS_LISTA = oficina.OFFICE_EXTS | core.PDF_EXTS | core.IMAGE_EXTS
# Distintivo de cada programa en la miniatura de la lista: siglas y un tono del sistema parecido al del programa.
DISTINTIVOS = {"word": ("DOC", "cat-organizar"), "excel": ("XLS", "cat-convertir"),
               "powerpoint": ("PPT", "cat-optimizar")}


def _patron(exts) -> str:
    return " ".join(f"*{e}" for e in sorted(exts))


FILTRO_ENTRADA = ";;".join([
    f"Documentos de Office, PDF e imágenes ({_patron(TIPOS_LISTA)})",
    f"Word ({_patron(oficina.WORD_EXTS)})",
    f"Excel ({_patron(oficina.EXCEL_EXTS)})",
    f"PowerPoint ({_patron(oficina.PPT_EXTS)})",
    "Documentos PDF (*.pdf)",
    f"Imágenes ({_patron(core.IMAGE_EXTS)})",
])


def _plural(n: int, uno: str, varios: str) -> str:
    return f"{n} {uno if n == 1 else varios}"


# --------------------------------------------------------------------------
# Íconos (cuadrícula de 24 × 24, de línea, con un detalle rojo)
# --------------------------------------------------------------------------

def _flecha_roja(p, acento):
    extensiones.lapiz(p, acento)
    p.drawPolyline([QPointF(6.5, 18.2), QPointF(6.5, 20.5), QPointF(10.6, 20.5)])
    _arrowhead(p, QPointF(11.2, 20.5), 1, 0, 2.6)


def dibujo_office2pdf(p, base, acento):
    """Hoja con una W y una flecha roja hacia un PDF."""
    _doc(p, 2.5, 2.5, 10.5, 13.5, 3)
    p.drawPolyline([QPointF(4.6, 7.2), QPointF(5.9, 12.4), QPointF(7.75, 8.7), QPointF(9.6, 12.4),
                    QPointF(10.9, 7.2)])
    _doc(p, 13, 8, 8.5, 13.5, 3)
    _line(p, 15.2, 14.2, 19.3, 14.2)
    _line(p, 15.2, 17.2, 18.0, 17.2)
    _flecha_roja(p, acento)


def dibujo_pdf2office(p, base, acento):
    """Hoja PDF con una flecha roja hacia una cuadrícula."""
    _doc(p, 2.5, 2.5, 10.5, 13.5, 3)
    _line(p, 5, 8, 10.5, 8)
    _line(p, 5, 10.8, 10.5, 10.8)
    _line(p, 5, 13.6, 8.5, 13.6)
    p.drawRoundedRect(QRectF(13, 10, 8.5, 11.5), 1.2, 1.2)
    _line(p, 13, 13.8, 21.5, 13.8)
    _line(p, 13, 17.6, 21.5, 17.6)
    _line(p, 17.25, 10, 17.25, 21.5)
    _flecha_roja(p, acento)


def _icono_word(p, base, acento):
    _doc(p, 4, 2, 16, 20, 5)
    p.drawPolyline([QPointF(7.3, 9), QPointF(9.1, 16.5), QPointF(12, 11.2), QPointF(14.9, 16.5),
                    QPointF(16.7, 9)])


def _icono_excel(p, base, acento):
    _doc(p, 4, 2, 16, 20, 5)
    _line(p, 7, 10, 17, 10)
    _line(p, 7, 14, 17, 14)
    _line(p, 7, 18, 17, 18)
    _line(p, 12, 10, 12, 18)


def _icono_powerpoint(p, base, acento):
    _doc(p, 4, 2, 16, 20, 5)
    p.drawRoundedRect(QRectF(7, 9, 10, 7), 1, 1)
    _line(p, 9, 19, 15, 19)


ICONOS = {"word": "oficina_word", "excel": "oficina_excel", "powerpoint": "oficina_powerpoint"}
icons.EXTRA.setdefault("oficina_word", _icono_word)
icons.EXTRA.setdefault("oficina_excel", _icono_excel)
icons.EXTRA.setdefault("oficina_powerpoint", _icono_powerpoint)


# --------------------------------------------------------------------------
# Diálogos (funciones del módulo: las pruebas las reemplazan)
# --------------------------------------------------------------------------

def pedir_destino(parent, sugerido: str, filtro: str, ext: str) -> str | None:
    """Diálogo «Guardar como» para .docx, .xlsx o .csv (widgets.ask_save solo sirve para .pdf/.txt)."""
    ruta, _ = QFileDialog.getSaveFileName(parent, "Guardar como", sugerido, filtro)
    if not ruta:
        return None
    if not ruta.lower().endswith(ext):
        ruta += ext
    ctx.remember_dir(ruta)
    return ruta


def confirmar_word_visible(parent) -> bool:
    box = QMessageBox(parent)
    box.setWindowTitle("Convertir con Microsoft Word")
    box.setIcon(QMessageBox.Icon.Information)
    box.setText("<b>Word se abrirá y mostrará un aviso: pulsa Aceptar.</b>")
    box.setInformativeText("Si marcas «No volver a mostrar este mensaje», la próxima vez la conversión "
                           "será automática.")
    seguir = box.addButton("Continuar", QMessageBox.ButtonRole.AcceptRole)
    box.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(seguir)
    box.exec()
    return box.clickedButton() is seguir


def _bloque(page: ToolPage, *widgets: QWidget) -> QWidget:
    """Bloque sin número dentro del panel de pasos (para avisos).  Devuelve el bloque: ocultarlo lo quita."""
    lay = page.step(None)
    for w in widgets:
        lay.addWidget(w)
    return lay.parentWidget()


# --------------------------------------------------------------------------
# Word, Excel y PowerPoint → PDF
# --------------------------------------------------------------------------

RES_TIPO = Qt.ItemDataRole.UserRole       # en la columna 3: «ok», «igual» o «error»


class _TarjetaOffice(FileCardDelegate):
    """Tarjeta de archivo (``rp-file``) con el distintivo de su programa y, después de convertir, el resultado:
    chip con las páginas, «Se deja igual» o «No se convirtió» y el motivo en la línea de datos."""

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
        # miniatura: distintivo PDF, siglas del programa o (imagen) sin distintivo
        ruta = item.data(0, Qt.ItemDataRole.UserRole) or ""
        tipo = oficina.tipo_de(ruta)
        mini = QRectF(r.left() + 9, r.center().y() - 22, 34, 44)
        pintar_miniatura(p, mini, tipo == "pdf", "PDF")
        if tipo in DISTINTIVOS:
            sigla, tono = DISTINTIVOS[tipo]
            f = tema.fuente("chip")
            f.setPixelSize(8)
            f.setWeight(f.Weight.Bold)
            bw = QFontMetrics(f).horizontalAdvance(sigla) + 8
            b = QRectF(mini.left() + 4, mini.bottom() - 5 - 14, bw, 14)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t[tono]))
            p.drawRoundedRect(b, 4, 4)
            p.setFont(f)
            p.setPen(QColor(t["on-cat"]))
            p.drawText(b, Qt.AlignmentFlag.AlignCenter, sigla)
        x = r.left() + 9 + 34 + 12
        derecha = r.right() - (48 if hover else 12)
        estado = item.data(3, RES_TIPO)
        chip = {"ok": item.text(3), "igual": "Se deja igual", "error": "No se convirtió"}.get(estado)
        if chip:
            fondo, tinta = {"ok": (ton["band"], ton["ink"]), "igual": (t["surface-sunken"], t["ink-2"]),
                            "error": (t["danger-tint"], t["danger"])}[estado]
            if sel and estado == "ok":
                fondo = t["surface"]
            fc = tema.fuente("chip")
            cw = QFontMetrics(fc).horizontalAdvance(chip) + 20
            cr = QRectF(derecha - cw, r.center().y() - 13, cw, 26)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(fondo))
            p.drawRoundedRect(cr, 13, 13)
            p.setFont(fc)
            p.setPen(QColor(tinta))
            p.drawText(cr, Qt.AlignmentFlag.AlignCenter, chip)
            derecha = cr.left() - 12
        ancho = max(40.0, derecha - x)
        fn = tema.fuente("body-strong")
        p.setFont(fn)
        p.setPen(QColor(t["ink"]))
        nombre = QFontMetrics(fn).elidedText(item.text(0), Qt.TextElideMode.ElideMiddle, int(ancho))
        p.drawText(QRectF(x, r.top() + 10, ancho, 20), Qt.AlignmentFlag.AlignVCenter, nombre)
        fm = tema.fuente("caption")
        p.setFont(fm)
        if estado == "error":
            p.setPen(QColor(t["danger"]))
            meta = item.text(3)
        else:
            p.setPen(QColor(t["ink-3"]))
            meta = self.meta_fn(item) or ""
        meta = QFontMetrics(fm).elidedText(meta, Qt.TextElideMode.ElideRight, int(ancho))
        p.drawText(QRectF(x, r.top() + 30, ancho, 16), Qt.AlignmentFlag.AlignVCenter, meta)
        if hover:
            q = self._quitar(r)
            p.drawPixmap(QPointF(q.center().x() - 8, q.center().y() - 8), pixmap("close", t["ink-2"], 16, 1.8))
        p.restore()


class ListaOffice(FileList):
    """Lista de documentos de Office, PDF e imágenes, como tarjetas con el distintivo de cada programa."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.set_cards(lambda it: f"{it.text(1)} · {it.text(2)}")
        self.setItemDelegate(_TarjetaOffice(self, lambda it: f"{it.text(1)} · {it.text(2)}"))


class OfficeToPdfPage(ToolPage):
    key = "office2pdf"
    title = "Word, Excel y PowerPoint a PDF"
    subtitle = "Convierte tus documentos de Office a PDF, uno por uno o todos unidos en un solo archivo."
    icon_name = "office2pdf"
    action_text = "Convertir a PDF"

    def build(self):
        m = self.motores = oficina.detectar()
        texto, self.puede = oficina.estado_motores(m)
        todo_office = m.word and m.excel and m.powerpoint
        completo = self.puede and (todo_office or bool(m.libreoffice))

        # Aviso del programa que convierte: solo si falta algo (si está todo, lo dice la ayuda del paso 1)
        self.estado = Note(texto if self.puede else
                           f"<b>{texto}</b><br>Mientras tanto, «PDF a Word y Excel» sí funciona en este equipo.",
                           "warn")
        self.estado_texto = self.estado.text_lb
        self.bloque_estado = _bloque(self, self.estado)
        self.bloque_estado.setVisible(not completo)
        programa = ("Microsoft Office" if todo_office else
                    "Microsoft Office y LibreOffice" if m.office else "LibreOffice")

        # Paso 1: uno por archivo o todos unidos (cada opción necesita su explicación)
        p1 = self.step("¿Cómo quieres los PDF?",
                       help=f"Se convierten con {programa}, sin ventanas y sin internet." if completo else None)
        self.modo = OptionGroup()
        self.r_cada = self.modo.add("Un PDF por cada archivo",
                                    "Junto a su original, con el mismo nombre.", "cada")
        self.r_unir = self.modo.add("Todo en un solo PDF",
                                    "En el orden de la lista, con un marcador por archivo.", "unir")
        p1.addWidget(self.modo)

        # Paso 2: ajustes de cada programa (solo los que aplican a la lista)
        p2 = self.step("Ajustes")
        self.paso_ajustes = p2.parentWidget()
        self.caja_excel = QWidget()
        ex = QVBoxLayout(self.caja_excel)
        ex.setContentsMargins(0, 0, 0, 0)
        ex.setSpacing(12)
        self.c_ancho = SwitchRow("Ajustar las tablas de Excel", "Las columnas caben en la página, sin cortes.",
                                 checked=True)
        ex.addWidget(self.c_ancho)
        ex.addWidget(label("Hojas de cada libro de Excel", "Label"))
        self.hojas = Segmented([("Todas", "todas"), ("Solo la activa", "activa")], full=True)
        self.r_todas, self.r_activa = self.hojas.button(0), self.hojas.button(1)
        ex.addWidget(self.hojas)
        p2.addWidget(self.caja_excel)
        self.c_marcadores = SwitchRow("Marcadores con los títulos de Word", "El índice del PDF lleva a cada título.",
                                      checked=True)
        p2.addWidget(self.c_marcadores)

        # Área principal: zona para soltar mientras no hay archivos; después, la lista como tarjetas
        self.list = ListaOffice(["Archivo", "Tipo", "Tamaño", "Resultado"], TIPOS_LISTA, self._row,
                                placeholder="Arrastra aquí tus documentos de Word, Excel o PowerPoint",
                                allow_duplicates=False)
        self.list.setMinimumHeight(0)
        self.zone = DropArea("Arrastra aquí tus documentos de Word, Excel o PowerPoint",
                             "También PDF e imágenes, para unirlos en un solo PDF.", TIPOS_LISTA, multi=True,
                             tile_key=self.key, zone=True, button_text="Elegir archivos…")
        self.zone.clicked.connect(self.add)
        self.zone.files_dropped.connect(self.accept_files)
        lista = QWidget()
        ll = QVBoxLayout(lista)
        ll.setContentsMargins(14, 12, 14, 8)
        ll.setSpacing(6)
        barra = QHBoxLayout()
        barra.addWidget(label("Documentos para convertir", "StepTitle"))
        barra.addStretch(1)
        self.b_ordenar = QPushButton("Ordenar por nombre")
        self.b_ordenar.setObjectName("Ghost")
        self.b_ordenar.setToolTip("Ordenar la lista de la A a la Z (el orden del PDF unido)")
        self.b_ordenar.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_ordenar.clicked.connect(self.list.sort_by_name)
        barra.addWidget(self.b_ordenar)
        b_vaciar = QPushButton("Vaciar lista")
        b_vaciar.setObjectName("Ghost")
        b_vaciar.setCursor(Qt.CursorShape.PointingHandCursor)
        b_vaciar.clicked.connect(self.list.clear_all)
        barra.addWidget(b_vaciar)
        ll.addLayout(barra)
        self.leyenda_orden = label("Arrastra las filas para cambiar el orden del PDF unido.", "Caption")
        ll.addWidget(self.leyenda_orden)
        ll.addWidget(self.list, 1)
        self.main_stack = QStackedWidget()
        self.main_stack.addWidget(self.zone)
        self.main_stack.addWidget(lista)
        self.set_main(self.main_stack)

        self.b_add = QPushButton("Agregar archivos")
        self.b_add.setObjectName("Secondary")
        self.b_add.setIcon(icon("add", tema.color("ink-2"), size=18))
        self.b_add.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_add.clicked.connect(self.add)
        self.set_header_widget(self.b_add)
        self.footer_info.setWordWrap(True)

        self.list.changed.connect(self.refresh)
        self.modo.changed.connect(lambda *_: self.refresh())

    # --- lista
    def _row(self, path: str):
        t = oficina.tipo_de(path)
        if t is None:
            return None
        try:
            size = core.human_size(os.path.getsize(path))
        except OSError:
            return None
        if t == "pdf":
            if not pdf_row(self, path):
                return None
            return [Path(path).name, "PDF", size, ""]
        if t == "imagen":
            return [Path(path).name, "Imagen", size, ""]
        return [Path(path).name, oficina.NOMBRES[t], size, ""]

    def add(self):
        self.list.add_paths(ask_open(self, True, FILTRO_ENTRADA, "Elige los documentos"))

    def accept_files(self, paths):
        self.list.add_paths([p for p in paths if oficina.tipo_de(p)])

    def _de_office(self) -> int:
        return sum(1 for p in self.list.paths() if oficina.tipo_de(p) in oficina.NOMBRES)

    def ready(self):
        if not getattr(self, "list", None) or not self.puede:
            return False
        n = self.list.topLevelItemCount()
        return n > 0 and (self._de_office() > 0 or self.r_unir.isChecked())

    def refresh(self):
        super().refresh()
        if not hasattr(self, "c_marcadores"):
            return
        n, de_office = self.list.topLevelItemCount(), self._de_office()
        tipos = {oficina.tipo_de(p) for p in self.list.paths()}
        self.main_stack.setCurrentIndex(1 if n else 0)
        self.b_add.setVisible(n > 0)
        # ajustes: solo los del programa de los documentos de la lista (con la lista vacía, todos)
        excel, word = not n or "excel" in tipos, not n or "word" in tipos
        self.caja_excel.setVisible(excel)
        self.c_marcadores.setVisible(word)
        self.paso_ajustes.setVisible(excel or word)
        unir = self.r_unir.isChecked()
        self.action.setText("Unir en un solo PDF" if unir else "Convertir a PDF")
        self.leyenda_orden.setVisible(unir and n > 1)
        self.b_ordenar.setVisible(unir and n > 1)
        if not n:
            self.set_summary([], "Agrega documentos de Word, Excel o PowerPoint para empezar.")
            return
        chips = [_plural(n, "archivo", "archivos")]
        if de_office < n:
            chips.append(f"{de_office} de Office")
        if not self.puede:
            texto = "Instala Microsoft Office o LibreOffice para convertir."
        elif not de_office and not unir:
            texto = "No hay documentos de Office: elige «Todo en un solo PDF» para unir los de la lista."
        elif unir:
            texto = "Eliges dónde guardar el PDF unido."
        elif n == 1:
            texto = "Eliges dónde guardar el PDF."
        else:
            texto = "Cada PDF se guarda junto a su original. Los PDF de la lista se dejan como están."
        self.set_summary(chips, texto)

    def opciones(self) -> oficina.OpcionesOffice:
        return oficina.OpcionesOffice(excel_ajustar_ancho=self.c_ancho.isChecked(),
                                      excel_hojas="activa" if self.r_activa.isChecked() else "todas",
                                      word_marcadores=self.c_marcadores.isChecked())

    # --- convertir
    def run(self):
        paths = self.list.paths()
        for it in self.list.items():
            it.setText(3, "")
            it.setToolTip(3, "")
            it.setData(3, RES_TIPO, None)
            it.setToolTip(0, it.data(0, Qt.ItemDataRole.UserRole))
        self.list.viewport().update()
        kwargs = dict(archivos=paths, opciones=self.opciones(),
                      contrasenas={p: ctx.passwords.get(p) for p in paths if ctx.passwords.get(p)})
        destino = None
        if self.r_unir.isChecked():
            destino = ask_save(self, str(core.unique_path(Path(paths[0]).with_name("documentos_unidos.pdf"))))
            if not destino:
                return
            kwargs["unir_en"] = destino
        elif len(paths) == 1:
            destino = ask_save(self, suggest_output(paths[0], "", ".pdf"))
            if not destino:
                return
            kwargs["destinos"] = [destino]
        self._unir = self.r_unir.isChecked()
        ctx.run_task("Convirtiendo a PDF…", oficina.lote_a_pdf, self._terminado, **kwargs)

    def _terminado(self, res: oficina.ResultadoLote):
        por_ruta = {os.path.normcase(r.ruta): r for r in res.archivos}
        for it in self.list.items():
            ruta = it.data(0, Qt.ItemDataRole.UserRole)
            r = por_ruta.get(os.path.normcase(ruta))
            if r is None:
                continue
            if r.error:
                detalle = r.error + (f"\n\nDetalle: {r.detalle}" if r.detalle else "")
                it.setText(3, r.error)
                it.setToolTip(3, detalle)
                it.setToolTip(0, f"{ruta}\n\n{detalle}")
                it.setData(3, RES_TIPO, "error")
            elif r.sin_cambios:
                it.setText(3, "Ya es un PDF: se deja igual")
                it.setData(3, RES_TIPO, "igual")
            else:
                it.setText(3, _plural(r.paginas, "página", "páginas"))
                it.setData(3, RES_TIPO, "ok")
                if r.salida:
                    it.setToolTip(3, r.salida)
                    it.setToolTip(0, f"{ruta}\n→ {r.salida}")
        self.list.viewport().update()
        n_err = len(res.errores)
        nota = ""
        if n_err:
            nota = ("1 archivo no se pudo convertir" if n_err == 1
                    else f"{n_err} archivos no se pudieron convertir") + "; en su tarjeta de la lista está el motivo."
        if not res.creados:
            QMessageBox.warning(self, "No se pudo convertir", nota or "No se creó ningún PDF.")
            return
        if self._unir:
            out = res.creados[0]
            detalle = f"{res.paginas} páginas en «{Path(out).name}»."
            show_done(self, "PDF unido creado", f"{detalle}\n{nota}".strip(), file=out)
        elif len(res.creados) == 1:
            out = res.creados[0]
            detalle = f"{_plural(res.paginas, 'página', 'páginas')} en «{Path(out).name}»."
            show_done(self, "PDF creado", f"{detalle}\n{nota}".strip(), file=out)
        else:
            detalle = "Cada PDF quedó en la carpeta de su original."
            show_done(self, f"{len(res.creados)} PDF creados", f"{detalle}\n{nota}".strip(),
                      folder=os.path.dirname(res.creados[0]))


# --------------------------------------------------------------------------
# PDF → Word y Excel
# --------------------------------------------------------------------------

class PdfToOfficePage(ToolPage):
    key = "pdf2office"
    title = "PDF a Word y Excel"
    subtitle = "Pasa un PDF a Word para editar su texto o saca sus tablas a Excel."
    icon_name = "pdf2office"
    action_text = "Convertir a Word"

    def build(self):
        self.motores = oficina.detectar()
        self.escaneado = False
        self.picker = SingleFilePicker()
        self.picker.changed.connect(self._archivo)
        self.use_picker(self.picker, "Arrastra aquí el PDF que quieres pasar a Word o Excel")
        self.preview = PreviewPane(self._render)
        self.set_main(self.preview)

        # Aviso de PDF escaneado (arriba del panel), con el atajo a «Reconocer texto»
        self.aviso = Note("", "warn")
        self.aviso_texto = self.aviso.text_lb
        self.b_ocr = QPushButton("Reconocer texto primero")
        self.b_ocr.setObjectName("Secondary")
        self.b_ocr.setProperty("tam", "sm")
        self.b_ocr.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_ocr.clicked.connect(self._ir_ocr)
        fila = self.aviso.layout()
        fila.removeWidget(self.aviso_texto)
        col = QVBoxLayout()
        col.setSpacing(8)
        col.addWidget(self.aviso_texto)
        col.addWidget(self.b_ocr, 0, Qt.AlignmentFlag.AlignLeft)
        fila.addLayout(col, 1)
        self.bloque_aviso = _bloque(self, self.aviso)
        self.bloque_aviso.hide()

        # Paso 1: el formato de destino
        p1 = self.step("¿A qué lo quieres pasar?")
        self.destino = OptionGroup()
        self.r_word = self.destino.add("Word (.docx)", "Para editar el texto: párrafos, títulos y negritas.", "word")
        self.r_excel = self.destino.add("Excel (.xlsx o .csv)", "Para trabajar con las tablas y sus cifras.", "excel")
        p1.addWidget(self.destino)

        # Paso 2 (Word): con el diseño o solo el texto
        w = self.step("¿Cómo lo quieres?")
        self.caja_word = w.parentWidget()
        self.modo_word = OptionGroup()
        self.r_diseno = self.modo_word.add("Conservar el diseño", "Se ve casi igual que el PDF. Usa Microsoft Word.",
                                           "diseno")
        self.r_texto = self.modo_word.add("Solo texto y títulos", "Más limpio para editar. También lee escaneados.",
                                          "texto")
        w.addWidget(self.modo_word)
        self.word_hint = self.caja_word.help_lb
        if not self.motores.word:
            self.r_diseno.setEnabled(False)
            self.r_diseno.help_lb.setText("Necesita Microsoft Word, que no está instalado.")
            self.r_texto.setChecked(True)
            self.caja_word.set_help("No se encontró Microsoft Word, así que se usará «Solo texto y títulos».")

        # Pasos 2 y 3 (Excel): qué tablas y en qué formato
        ex = self.step("¿Qué tablas?", help="Las cifras quedan como números; las cédulas y fechas, como texto.")
        self.caja_excel = ex.parentWidget()
        ex.addWidget(label("Páginas", "Label"))
        self.paginas = pages_edit("Vacío = todas (o por ejemplo: 1-3, 5)")
        ex.addWidget(self.paginas)
        ex.addWidget(label("Hojas del libro", "Label"))
        self.agrupar = OptionGroup()
        self.r_por_tabla = self.agrupar.add("Una hoja por cada tabla", None, "tabla")
        self.r_todo = self.agrupar.add("Todo en una sola hoja", "Une las tablas que siguen en otra página.", "todo")
        ex.addWidget(self.agrupar)
        fm = self.step("Elige el formato", help="El CSV usa punto y coma, como lo abre Excel en español.")
        self.caja_formato = fm.parentWidget()
        self.formato = Segmented([("Excel (.xlsx)", ".xlsx"), ("Texto (.csv)", ".csv")], full=True)
        fm.addWidget(self.formato)

        self.footer_info.setWordWrap(True)
        self.destino.changed.connect(lambda *_: self._mostrar())
        self._mostrar()

    def _render(self):
        if not self.picker.path:
            return None
        with core.open_pdf(self.picker.path, self.picker.password) as d:
            if not d.page_count:
                return None
            lado = max(self.preview.width(), self.preview.height(), 400) * self.preview.devicePixelRatioF()
            return core.render_page(d, 0, int(min(1600, lado)))

    def _mostrar(self):
        es_word = self.r_word.isChecked()
        self.caja_word.setVisible(es_word)
        self.caja_excel.setVisible(not es_word)
        self.caja_formato.setVisible(not es_word)
        self.action.setText("Convertir a Word" if es_word else "Sacar tablas a Excel")
        self._actualizar_aviso()
        self.refresh()

    def _actualizar_aviso(self):
        if not self.escaneado:
            self.bloque_aviso.hide()
            return
        if self.r_word.isChecked():
            self.aviso_texto.setText("Este PDF parece escaneado: se usará «Solo texto y títulos», que reconoce "
                                     "el texto de cada página.")
            self.b_ocr.hide()
        else:
            self.aviso_texto.setText("Este PDF parece escaneado, así que sus tablas son imágenes. Pásalo "
                                     "primero por «Reconocer texto (OCR)» y luego saca las tablas.")
            self.b_ocr.show()
        self.bloque_aviso.show()

    def refresh(self):
        super().refresh()
        if not hasattr(self, "formato"):
            return
        if not self.picker.path:
            self.set_summary([], "Elige el PDF que quieres convertir.")
            return
        chips = []
        if self.picker.info:
            chips.append(_plural(self.picker.info.pages, "página", "páginas"))
        if self.escaneado:
            chips.append(("Escaneado", "warn"))
        self.set_summary(chips, "Eliges dónde guardar el documento de Word." if self.r_word.isChecked()
                         else "Eliges dónde guardar las tablas.")

    def _archivo(self, path):
        self.escaneado = False
        if path:
            try:
                with PDF_LOCK:
                    with core.open_pdf(path, self.picker.password) as d:
                        self.escaneado = core.looks_scanned_without_text(d)
            except core.UserError:
                self.escaneado = False
            if self.escaneado:
                self.r_texto.setChecked(True)
        self._actualizar_aviso()
        self.preview.schedule()
        self.refresh()

    def _ir_ocr(self):
        if self.picker.path:
            extensiones.ir_a("ocr", [self.picker.path])

    def accept_files(self, paths):
        pdfs = [p for p in paths if oficina.tipo_de(p) == "pdf"]
        if pdfs:
            self.picker.set_path(pdfs[0])

    def ready(self):
        return bool(getattr(self, "picker", None) and self.picker.path)

    def run(self):
        path, pw = self.picker.path, self.picker.password
        if self.r_word.isChecked():
            out = pedir_destino(self, str(core.unique_path(Path(path).with_suffix(".docx"))),
                                "Documento de Word (*.docx)", ".docx")
            if not out:
                return
            modo = "word" if self.r_diseno.isChecked() and self.r_diseno.isEnabled() else "interno"
            visible = False
            if modo == "word" and not oficina.aviso_word_desactivado():
                if not confirmar_word_visible(self):
                    return
                visible = True
            texto = "Convirtiendo con Microsoft Word…" if modo == "word" else "Convirtiendo a Word…"
            ctx.run_task(texto, oficina.pdf_a_word, lambda r: self._listo_word(r, out),
                         path=path, output=out, modo=modo, visible=visible, password=pw)
            return
        spec = self.paginas.text()
        if not check_spec(self, spec, self.picker.info.pages if self.picker.info else None):
            return
        ext = self.formato.value()
        filtro = "Libro de Excel (*.xlsx)" if ext == ".xlsx" else "Texto separado por punto y coma (*.csv)"
        out = pedir_destino(self, str(core.unique_path(Path(path).with_name(f"{Path(path).stem}_tablas{ext}"))),
                            filtro, ext)
        if not out:
            return
        ctx.run_task("Buscando tablas…", oficina.pdf_a_excel, lambda r: self._listo_excel(r, out),
                     path=path, output=out, paginas=spec,
                     agrupar="todo" if self.r_todo.isChecked() else "tabla", password=pw)

    def _listo_word(self, r: oficina.ResultadoWord, out: str):
        avisos = " ".join(r.avisos)
        if r.modo == "word" and r.avisos:
            show_done(self, "Documento de Word listo",
                      f"Quedó abierto en Word para que lo revises. También se guardó como «{Path(out).name}».",
                      file=out)
            return
        detalle = f"Se guardó como «{Path(out).name}» ({_plural(r.paginas, 'página', 'páginas')} del PDF)."
        show_done(self, "Documento de Word creado", f"{detalle}\n{avisos}".strip(), file=out)

    def _listo_excel(self, r: tuple[int, int], out: str):
        tablas, filas = r
        show_done(self, "Tablas exportadas",
                  f"{_plural(tablas, 'tabla', 'tablas')} con {_plural(filas, 'fila', 'filas')} "
                  f"en «{Path(out).name}».", file=out)


REGISTRO = [
    dict(clave="office2pdf", titulo="Word y Excel a PDF", seccion="convertir", orden=30,
         pagina=OfficeToPdfPage, dibujo=dibujo_office2pdf, inicio="Convierte documentos de Office",
         tipos=oficina.OFFICE_EXTS, filtro="Word, Excel y PowerPoint"),
    dict(clave="pdf2office", titulo="PDF a Word y Excel", seccion="convertir", orden=31,
         pagina=PdfToOfficePage, dibujo=dibujo_pdf2office, inicio="Para editar el contenido"),
]
