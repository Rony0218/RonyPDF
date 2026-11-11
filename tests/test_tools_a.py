"""Rediseño 2.0, fase 2 (grupo A): Unir, Dividir, PDF a imágenes, Imágenes a PDF, Extraer, Marca de agua,
Numerar, Propiedades, Proteger, Quitar contraseña y OCR en la plantilla de dos columnas."""

import os
import tempfile
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["RONYPDF_SETTINGS"] = os.path.join(tempfile.mkdtemp(prefix="ronypdf-"), "RonyPDF.json")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (QAbstractButton, QApplication, QComboBox, QFormLayout, QLabel,  # noqa: E402
                               QLineEdit, QRadioButton)

from ronypdf import core, tools, widgets  # noqa: E402
from ronypdf.gui import MainWindow, _light_palette  # noqa: E402
from ronypdf.style import QSS  # noqa: E402

CLAVES = ["merge", "split", "toimage", "fromimage", "extract", "watermark", "numbers", "metadata", "protect",
          "unlock", "ocr"]


@pytest.fixture(scope="module")
def win():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setPalette(_light_palette())
    app.setStyleSheet(QSS)
    w = MainWindow()
    w.sidebar.set_collapsed(True, remember=False)
    w.resize(1240, 820)
    w.show()
    app.processEvents()
    return w


@pytest.fixture
def avisos(monkeypatch):
    calls = []
    monkeypatch.setattr(widgets.QMessageBox, "warning", lambda *a, **k: calls.append(("warning", a[2])))
    monkeypatch.setattr(tools, "show_done", lambda *a, **k: calls.append(("done", a[1])))
    return calls


def procesar(seg: float = 0.05):
    fin = time.time() + seg
    while True:
        QApplication.processEvents()
        if time.time() >= fin:
            return
        time.sleep(0.01)


def recortes(page) -> list[str]:
    """Botones, listas y textos de una línea que no caben, o que se salen por la derecha de la página."""
    malos = []
    ancho = page.width()
    for w in page.findChildren(QAbstractButton) + page.findChildren(QComboBox) + page.findChildren(QLabel):
        if not w.isVisible() or isinstance(w.parent(), QLineEdit):
            continue
        texto = getattr(w, "text", lambda: "")()
        if w.mapTo(page, w.rect().topLeft()).x() + w.width() > ancho + 1:
            malos.append(f"se sale: {type(w).__name__} «{texto}»")
        if isinstance(w, (QAbstractButton, QComboBox)) and w.width() + 1 < w.minimumSizeHint().width():
            malos.append(f"recortado: {type(w).__name__} «{texto}» {w.width()} < {w.minimumSizeHint().width()}")
        if isinstance(w, QLabel) and not w.wordWrap() and w.text() and w.pixmap().isNull() \
                and w.sizePolicy().horizontalPolicy().name != "Ignored" and w.width() + 1 < w.sizeHint().width():
            malos.append(f"etiqueta cortada: «{w.text()[:40]}»")
    return malos


def cargar(page, samples):
    """Contenido de ejemplo para cada pantalla."""
    if page.key == "merge":
        page.list.add_paths([str(samples["a"]), str(samples["b"]), str(samples["jpg"])])
    elif page.key == "fromimage":
        page.list.add_paths([str(samples["png"]), str(samples["jpg"])])
    elif page.key == "ocr":
        page.b_uno.setChecked(True)
        page.picker.set_path(str(samples["a"]))
    else:
        page.picker.set_path(str(samples["a"]))


# ---------------------------------------------------------------- estructura común

def test_todas_en_dos_columnas_sin_controles_viejos_y_sin_recortes(win, samples):
    for clave in CLAVES:
        page = win.pages[clave]
        win.navigate(clave)
        procesar()
        assert page.main_frame is not None and page.panel is not None, clave
        visibles = [s for s in page.steps.steps if not s.isHidden()]
        assert 1 <= len(visibles) <= 4, (clave, len(visibles))
        assert [s.number for s in visibles] == list(range(1, len(visibles) + 1))
        assert not page.findChildren(QRadioButton) and not page.findChildren(QFormLayout), clave
        assert page.action.objectName() == "Primary"
        principales = [b for b in page.findChildren(QAbstractButton) if b.objectName() == "Primary"]
        assert principales == [page.action], clave
        assert not page.action.isEnabled(), clave                 # vacía: falta el archivo
        assert page.footer_info.text(), clave                     # el resumen dice qué falta
        for tam in ((1000, 700), (1240, 820)):
            win.resize(*tam)
            procesar()
            assert recortes(page) == [], (clave, tam, "vacía")
        cargar(page, samples)
        for tam in ((1000, 700), (1240, 820)):
            win.resize(*tam)
            procesar()
            assert recortes(page) == [], (clave, tam, "con archivo")
            assert page.panel_scroll.horizontalScrollBar().maximum() == 0, clave


def test_human_size_en_la_ficha_del_archivo(win, samples):
    page = win.pages["protect"]
    win.navigate("protect")
    page.picker.set_path(str(samples["a"]))
    assert "," in page.picker.file_chip().meta_lb.text() and "KB" in page.picker.file_chip().meta_lb.text()


# ---------------------------------------------------------------- Unir

def test_unir_rango_en_la_tarjeta_orden_y_resumen(win, samples, avisos, monkeypatch, tmp_path):
    page = win.pages["merge"]
    win.navigate("merge")
    page.list.clear_all()
    assert page.varios.currentIndex() == 0 and not page.b_add.isVisible()      # vacía: zona para soltar
    page.list.add_paths([str(samples["a"]), str(samples["b"]), str(samples["jpg"])])
    procesar()
    assert page.varios.currentIndex() == 1 and page.b_add.isVisible()
    assert page.paginas.value() == "todas" and page.bookmarks.isChecked()
    assert "9 páginas" in [page._chips.itemAt(k).widget().text() for k in range(page._chips.count())]
    # editar el rango en la tarjeta: pasa a «Elegir por archivo» y se guarda en la columna 2
    item = page.list.topLevelItem(0)
    page._editar_rango(item)
    ed = page._editor
    assert ed is not None and ed.isVisible() and page.paginas.value() == "elegir"
    ed.setText("1-2")
    ed.editingFinished.emit()
    assert page._editor is None and item.text(2) == "1-2"
    chips = [page._chips.itemAt(k).widget().text() for k in range(page._chips.count())]
    assert chips == ["3 archivos", "6 páginas"]
    # Escape descarta lo escrito
    page._editar_rango(item)
    page._editor.setText("4")
    QTest.keyClick(page._editor, Qt.Key.Key_Escape)
    assert page._editor is None and item.text(2) == "1-2"
    # un rango que no se entiende desactiva el botón y el resumen dice por qué
    item.setText(2, "9-12")
    assert not page.action.isEnabled() and "informe.pdf" in page.footer_info.text()
    item.setText(2, "1-2")
    assert page.action.isEnabled()
    # orden por nombre (natural) y de nuevo a mano
    page.varios.orden.set_value("nombre")
    assert [it.text(0) for it in page.list.items()] == ["anexo.pdf", "foto.jpg", "informe.pdf"]
    page.varios.orden.set_value("mano")
    page.list.add_paths([str(samples["png"])])
    assert page.list.topLevelItem(3).text(0) == "imagen.png"                  # a mano: al final
    # «Todas» ignora los rangos al unir
    page.paginas.set_value("todas")
    capturados = {}
    monkeypatch.setattr(tools.ctx, "run_task", lambda *a, **k: capturados.update(k))
    monkeypatch.setattr(tools, "ask_save", lambda *a, **k: str(tmp_path / "u.pdf"))
    page.run()
    assert [i.pages for i in capturados["items"]] == ["", "", "", ""] and capturados["bookmarks"]
    page.paginas.set_value("elegir")
    page.run()
    rangos = {os.path.basename(i.path): i.pages for i in capturados["items"]}
    assert rangos["informe.pdf"] == "1-2" and rangos["anexo.pdf"] == ""
    page.list.clear_all()


# ---------------------------------------------------------------- Dividir

def test_dividir_grupos_en_las_miniaturas(win, samples):
    page = win.pages["split"]
    win.navigate("split")
    page.picker.set_path(str(samples["b"]))                        # sin marcadores
    assert not page.r_bm.isVisible() and page.bm_hint.isVisible()
    page.picker.set_path(str(samples["a"]))                        # 5 páginas, con marcadores
    assert page.r_bm.isVisibleTo(page) and page.r_bm.isEnabled() and not page.bm_hint.isVisibleTo(page)
    assert page.paso_detalle.isHidden() and page.grid.count() == 5
    assert page.grid.marcas == {i: str(i + 1) for i in range(5)}
    page.r_every.setChecked(True)
    assert not page.paso_detalle.isHidden() and page.paso_detalle.title_lb.text() == "¿Cada cuántas páginas?"
    page.n_every.setValue(2)
    assert page.grid.marcas == {0: "1", 1: "1", 2: "2", 3: "2", 4: "3"}
    page.r_bm.setChecked(True)
    assert page.paso_detalle.isHidden() and page.grid.marcas == {0: "1", 1: "1", 2: "2", 3: "2", 4: "3"}
    page.r_extract.setChecked(True)
    assert page.action.text() == "Extraer páginas" and not page.action.isEnabled()
    page.e_extract.setText("2, 4")
    assert page.grid.marcas == {1: "✓", 3: "✓"} and page.action.isEnabled()
    page.r_ranges.setChecked(True)
    page.e_ranges.setText("1-9")
    assert not page.action.isEnabled() and page.grid.marcas == {}
    page.e_ranges.setText("1-2, 3-")
    assert page.action.isEnabled() and page.action.text() == "Dividir PDF"
    procesar(0.5)
    assert page.grid.thumbs                                         # las miniaturas se dibujan solas


# ---------------------------------------------------------------- convertir

def test_pdf_a_imagenes_y_extraer(win, samples):
    page = win.pages["toimage"]
    win.navigate("toimage")
    page.picker.set_path(str(samples["a"]))
    assert page.fmt.currentData() == "png" and page.dpi.currentData() == 150
    page.dpi.setCurrentIndex(page.dpi.findData(300))
    assert page.steps.steps[1].help_lb.text().startswith("300 ppp")
    page.pages.setText("2-3")
    assert page.grid.marcas == {1: "✓", 2: "✓"} and page.action.isEnabled()
    page.pages.setText("8")
    assert not page.action.isEnabled()
    page.pages.setText("")

    page = win.pages["extract"]
    win.navigate("extract")
    page.picker.set_path(str(samples["a"]))
    assert not page.nota_ocr.isVisibleTo(page) and not page.campo_min.isVisibleTo(page)
    page.r_imgs.setChecked(True)
    assert page.campo_min.isVisibleTo(page) and page.action.text() == "Extraer imágenes"
    page.r_text.setChecked(True)


def test_extraer_avisa_si_es_escaneado(win, scanned):
    page = win.pages["extract"]
    win.navigate("extract")
    page.picker.set_path(str(scanned))
    assert page.nota_ocr.isVisibleTo(page) and "OCR" in page.nota_ocr.text()


def test_imagenes_a_pdf_tamano_de_la_imagen(win, samples, monkeypatch, tmp_path):
    page = win.pages["fromimage"]
    win.navigate("fromimage")
    page.list.clear_all()
    page.list.add_paths([str(samples["png"])])
    assert page.campo_orient.isVisibleTo(page)
    page.ajustar.setChecked(True)
    assert not page.campo_orient.isVisibleTo(page) and not page.papel.isVisibleTo(page)
    capturados = {}
    monkeypatch.setattr(tools.ctx, "run_task", lambda *a, **k: capturados.update(k))
    monkeypatch.setattr(tools, "ask_save", lambda *a, **k: str(tmp_path / "i.pdf"))
    page.run()
    assert capturados["page_size"] == "imagen" and capturados["margin"] == 18
    page.ajustar.setChecked(False)
    page.papel.setCurrentIndex(page.papel.findData("Carta"))
    page.run()
    assert capturados["page_size"] == "Carta"
    page.list.clear_all()


# ---------------------------------------------------------------- numerar y marca de agua

def test_posicion_del_numero_en_la_hoja(win, samples):
    page = win.pages["numbers"]
    win.navigate("numbers")
    pos = page.pos
    assert pos.currentData() == "abajo-centro" and pos.count() == 6
    vistos = []
    pos.currentIndexChanged.connect(vistos.append)
    QTest.mouseClick(pos, Qt.MouseButton.LeftButton, pos=pos._lugar(pos.findData("arriba-derecha")).center().toPoint())
    assert pos.currentData() == "arriba-derecha" and vistos == [pos.findData("arriba-derecha")]
    pos.setFocus()
    QTest.keyClick(pos, Qt.Key.Key_Left)
    QTest.keyClick(pos, Qt.Key.Key_Down)
    assert pos.currentData() == "abajo-centro"
    page.fmt.setCurrentIndex(page.fmt.count() - 1)                  # «Personalizado…»
    assert page.custom.isVisibleTo(page) and "{n}" in page.steps.steps[0].help_lb.text()
    page.fmt.setCurrentIndex(0)
    assert not page.custom.isVisibleTo(page)


def test_marca_de_agua_angulo_solo_si_aplica(win, samples):
    page = win.pages["watermark"]
    win.navigate("watermark")
    page.picker.set_path(str(samples["a"]))
    page.pos.setCurrentIndex(page.pos.findData("superior"))
    assert not page.campo_angle.isVisibleTo(page)
    page.pos.setCurrentIndex(page.pos.findData("mosaico"))
    assert page.campo_angle.isVisibleTo(page)
    page.pages.setText("2-")
    assert "4 páginas" in [page._chips.itemAt(k).widget().text() for k in range(page._chips.count())]
    page.pages.setText("")


# ---------------------------------------------------------------- propiedades y seguridad

def test_propiedades_cuenta_los_cambios(win, samples):
    page = win.pages["metadata"]
    win.navigate("metadata")
    page.picker.set_path(str(samples["a"]))
    assert page._chips.count() == 0
    page.fields["author"].setText("Laura Restrepo")
    assert [page._chips.itemAt(k).widget().text() for k in range(page._chips.count())] == ["1 cambio"]
    assert not page.fields["creator"].isVisibleTo(page)            # plegado en «Datos del programa»
    page.avanzadas.boton.setChecked(True)
    assert page.fields["creator"].isVisibleTo(page)
    page.avanzadas.boton.setChecked(False)


def test_proteger_permisos_y_contrasenas(win, samples, monkeypatch, tmp_path):
    page = win.pages["protect"]
    win.navigate("protect")
    page.picker.set_path(str(samples["b"]))
    page.pw.setText("abc")
    page.pw2.setText("abd")
    assert page.pw2.property("error") and "no coinciden" in page.footer_info.text()
    page.pw2.setText("abc")
    assert not page.pw2.property("error") and page.action.isEnabled()
    page.p_modify.setChecked(True)
    page.p_print.setChecked(False)
    capturados = {}
    monkeypatch.setattr(tools.ctx, "run_task", lambda *a, **k: capturados.update(k))
    monkeypatch.setattr(tools, "ask_save", lambda *a, **k: str(tmp_path / "p.pdf"))
    page.run()
    assert capturados["allow_modify"] and not capturados["allow_print"] and capturados["allow_copy"]
    page.p_modify.setChecked(False)
    page.p_print.setChecked(True)


def test_quitar_contrasena_estado(win, samples, monkeypatch):
    page = win.pages["unlock"]
    win.navigate("unlock")
    page.picker.set_path(str(samples["b"]))
    assert page.status.property("tone") == "warn" and not page.action.isEnabled()
    monkeypatch.setattr(widgets.QInputDialog, "getText", lambda *a, **k: ("1234", True))
    page.picker.set_path(str(samples["locked"]))
    assert page.status.property("tone") == "ok" and page.action.isEnabled()


# ---------------------------------------------------------------- OCR

def test_ocr_modos_y_paginas_que_se_reconocen(win, scanned, samples):
    page = win.pages["ocr"]
    win.navigate("ocr")
    page.b_uno.setChecked(True)
    page.picker.set_path(str(scanned))
    assert page.main_stack.currentWidget() is page.lienzo and page.chip_archivo.isVisibleTo(page)
    assert page.grid.marcas == {0: "✓", 1: "✓"}
    assert not page.caja_carpeta.isVisibleTo(page) and page.paso_destino.isHidden()
    page.b_varios.setChecked(True)
    assert page.main_stack.currentWidget() is page.lista_varios and not page.chip_archivo.isVisibleTo(page)
    assert page.caja_carpeta.isVisibleTo(page) and not page.paso_destino.isHidden()
    assert not page.b_dest.isVisibleTo(page)
    page.r_carpeta.setChecked(True)
    assert page.b_dest.isVisibleTo(page)
    page.r_junto.setChecked(True)
    page.b_uno.setChecked(True)
    page.picker.set_path(str(samples["a"]))                          # con texto: nada que reconocer
    assert page.grid.marcas == {} and page.status.property("tone") == "ok"
    page.scope.setCurrentIndex(page.scope.findData(False))           # «Todas»
    assert len(page.grid.marcas) == 5
    page.scope.setCurrentIndex(0)
