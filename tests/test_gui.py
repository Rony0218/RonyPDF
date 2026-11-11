"""Prueba de humo de la interfaz (sin pantalla): carga archivos en cada herramienta y ejecuta tareas."""

import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Los ajustes (recientes, menú) de las pruebas nunca deben tocar los reales.
import tempfile  # noqa: E402
os.environ["RONYPDF_SETTINGS"] = os.path.join(tempfile.mkdtemp(prefix="ronypdf-"), "RonyPDF.json")

from PySide6.QtWidgets import QApplication  # noqa: E402

from ronypdf import organizer, tools, viewer, widgets  # noqa: E402
from ronypdf.gui import MainWindow, _light_palette  # noqa: E402
from ronypdf.style import QSS  # noqa: E402

SHOTS = os.environ.get("RONYPDF_SHOTS")


@pytest.fixture(scope="module")
def win(tmp_path_factory):
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setPalette(_light_palette())
    app.setStyleSheet(QSS)
    w = MainWindow()
    w.resize(1200, 800)
    w.show()
    app.processEvents()
    return w


@pytest.fixture
def done(monkeypatch, tmp_path):
    calls = []

    def fake_done(parent, title, detail="", file=None, folder=None):
        calls.append((title, detail, file, folder))

    for mod in (tools, organizer, widgets, viewer):
        monkeypatch.setattr(mod, "show_done", fake_done, raising=False)
    monkeypatch.setattr(widgets.QMessageBox, "warning", lambda *a, **k: calls.append(("warning", a[2])))
    monkeypatch.setattr(widgets.QMessageBox, "information", lambda *a, **k: calls.append(("info", a[2])))
    monkeypatch.setattr(widgets.QMessageBox, "question",
                        lambda *a, **k: widgets.QMessageBox.StandardButton.Yes)
    # «Guardar / No guardar / Cancelar» al abrir otro archivo con cambios: «No guardar» (antes, «Sí» descartaba)
    from ronypdf import guardado_ui
    monkeypatch.setattr(guardado_ui, "preguntar_cambios", lambda *a, **k: "descartar")
    return calls


def wait(app_win, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        QApplication.processEvents()
        if not widgets.ctx.busy:
            QApplication.processEvents()
            return
        time.sleep(0.01)
    raise TimeoutError


def shot(w, name):
    if SHOTS:
        QApplication.processEvents()
        w.grab().save(str(Path(SHOTS) / f"{name}.png"))


def test_home(win):
    win.navigate("home")
    shot(win, "00_inicio")


def test_merge(win, samples, done, tmp_path, monkeypatch):
    page = win.pages["merge"]
    win.navigate("merge")
    page.list.add_paths([str(samples["a"]), str(samples["b"]), str(samples["jpg"])])
    assert page.list.topLevelItemCount() == 3
    page.list.topLevelItem(0).setText(2, "1-2")
    assert page.action.isEnabled()
    shot(win, "01_unir")
    out = tmp_path / "unido.pdf"
    monkeypatch.setattr(tools, "ask_save", lambda *a, **k: str(out))
    page.run()
    wait(win)
    assert done and done[-1][0] == "PDF unido", done
    assert out.exists()


def test_split(win, samples, done, tmp_path, monkeypatch):
    page = win.pages["split"]
    win.navigate("split")
    page.picker.set_path(str(samples["a"]))
    assert page.r_bm.isEnabled()
    page.r_ranges.setChecked(True)
    page.e_ranges.setText("1-2, 3-")
    shot(win, "02_dividir")
    monkeypatch.setattr(tools, "ask_dir", lambda *a, **k: str(tmp_path))
    page.run()
    wait(win)
    assert done[-1][0] == "PDF dividido"
    assert len(list(Path(done[-1][3]).glob("*.pdf"))) == 2


def test_organize(win, samples, done, tmp_path, monkeypatch):
    page = win.pages["organize"]
    win.navigate("organize")
    page.load_files([str(samples["a"])])
    page.insert_files([str(samples["b"])])
    for _ in range(100):
        QApplication.processEvents()
        if not page.pending:
            break
        time.sleep(0.01)
    assert page.grid.count() == 8 and not page.pending
    # el modelo admite mover filas (lo que usa arrastrar y soltar internamente)
    from PySide6.QtCore import QModelIndex
    first = page._row(page.grid.item(0))
    assert page.grid.model().moveRow(QModelIndex(), 0, QModelIndex(), 3)
    assert page._row(page.grid.item(2)) == first
    assert page.grid.model().moveRow(QModelIndex(), 2, QModelIndex(), 0)
    assert page._row(page.grid.item(0)) == first
    page.grid.clearSelection()
    page.grid.item(0).setSelected(True)
    page.rotate(90)
    page.grid.clearSelection()
    page.grid.item(7).setSelected(True)
    page.grid.item(6).setSelected(True)
    page.delete()
    assert page.grid.count() == 6
    page.undo()
    assert page.grid.count() == 8
    page.redo()
    assert page.grid.count() == 6
    page.grid.clearSelection()
    page.grid.item(1).setSelected(True)
    page.insert_blank()
    assert page.grid.count() == 7
    shot(win, "03_organizar")
    out = tmp_path / "org.pdf"
    monkeypatch.setattr(organizer, "ask_save", lambda *a, **k: str(out))
    page.save()
    wait(win)
    assert done[-1][0] == "PDF guardado"
    import pymupdf
    with pymupdf.open(out) as d:
        assert d.page_count == 7 and d[0].rotation == 90 and d[2].get_text().strip() == ""
    # sobrescribir el original recarga el organizador
    src = tmp_path / "copia.pdf"
    src.write_bytes(samples["a"].read_bytes())
    page.load_files([str(src)])
    page.undo_stack.clear()
    page.grid.clearSelection()
    page.grid.item(0).setSelected(True)
    page.delete()
    monkeypatch.setattr(organizer, "ask_save", lambda *a, **k: str(src))
    page.save()
    wait(win)
    assert page.grid.count() == 4 and not page.undo_stack


def test_compress(win, samples, done, tmp_path, monkeypatch):
    page = win.pages["compress"]
    win.navigate("compress")
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    a.write_bytes(samples["a"].read_bytes())
    b.write_bytes(samples["b"].read_bytes())
    page.list.add_paths([str(a), str(b), str(a)])
    assert page.list.topLevelItemCount() == 2  # sin duplicados
    page.run()
    wait(win)
    assert done[-1][0] == "2 archivos comprimidos"
    assert (tmp_path / "a_comprimido.pdf").exists()
    assert "%" in page.list.topLevelItem(0).text(3)
    shot(win, "04_comprimir")


def test_watermark_preview_and_run(win, samples, done, tmp_path, monkeypatch):
    page = win.pages["watermark"]
    win.navigate("watermark")
    page.picker.set_path(str(samples["a"]))
    page.pos.setCurrentIndex(1)
    page.preview._refresh()
    assert page.preview._qimg is not None
    shot(win, "05_marca")
    out = tmp_path / "wm.pdf"
    monkeypatch.setattr(tools, "ask_save", lambda *a, **k: str(out))
    page.run()
    wait(win)
    assert out.exists()


def test_numbers_preview(win, samples, done, tmp_path, monkeypatch):
    page = win.pages["numbers"]
    win.navigate("numbers")
    page.picker.set_path(str(samples["b"]))
    page.fmt.setCurrentIndex(2)
    page.skip_first.setChecked(True)
    page.preview._refresh()
    assert page.preview._qimg is not None
    shot(win, "06_numerar")
    out = tmp_path / "num.pdf"
    monkeypatch.setattr(tools, "ask_save", lambda *a, **k: str(out))
    page.run()
    wait(win)
    import pymupdf
    with pymupdf.open(out) as d:
        assert "Página 1 de 2" in d[1].get_text()


def test_protect_unlock(win, samples, done, tmp_path, monkeypatch):
    page = win.pages["protect"]
    win.navigate("protect")
    page.picker.set_path(str(samples["b"]))
    page.pw.setText("abc")
    page.pw2.setText("abd")
    assert not page.action.isEnabled()
    page.pw2.setText("abc")
    assert page.action.isEnabled()
    shot(win, "07_proteger")
    out = tmp_path / "p.pdf"
    monkeypatch.setattr(tools, "ask_save", lambda *a, **k: str(out))
    page.run()
    wait(win)
    from ronypdf import core
    assert core.needs_password(out)
    # desbloquear: la contraseña se pide con un diálogo
    monkeypatch.setattr(widgets.QInputDialog, "getText", lambda *a, **k: ("abc", True))
    up = win.pages["unlock"]
    win.navigate("unlock")
    up.picker.set_path(str(out))
    assert up.action.isEnabled()
    shot(win, "08_desbloquear")
    free = tmp_path / "free.pdf"
    monkeypatch.setattr(tools, "ask_save", lambda *a, **k: str(free))
    up.run()
    wait(win)
    assert not core.needs_password(free)


def test_images_extract_metadata(win, samples, done, tmp_path, monkeypatch):
    page = win.pages["fromimage"]
    win.navigate("fromimage")
    page.list.add_paths([str(samples["png"]), str(samples["jpg"])])
    shot(win, "09_imagenes")
    out = tmp_path / "imgs.pdf"
    monkeypatch.setattr(tools, "ask_save", lambda *a, **k: str(out))
    page.run()
    wait(win)
    assert out.exists()

    page = win.pages["toimage"]
    win.navigate("toimage")
    page.picker.set_path(str(samples["b"]))
    monkeypatch.setattr(tools, "ask_dir", lambda *a, **k: str(tmp_path))
    page.run()
    wait(win)
    assert done[-1][0] == "Conversión terminada"

    page = win.pages["extract"]
    win.navigate("extract")
    page.picker.set_path(str(samples["a"]))
    txt = tmp_path / "t.txt"
    monkeypatch.setattr(tools, "ask_save", lambda *a, **k: str(txt))
    page.run()
    wait(win)
    assert "Informe" in txt.read_text(encoding="utf-8-sig")

    page = win.pages["metadata"]
    win.navigate("metadata")
    page.picker.set_path(str(samples["a"]))
    assert page.fields["title"].text() == "Informe anual"
    shot(win, "10_propiedades")


# ---------------------------------------------------------------- visor / editor / OCR

def _mouse(widget, kind, pos, buttons=None):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    types = {"press": QEvent.Type.MouseButtonPress, "move": QEvent.Type.MouseMove,
             "release": QEvent.Type.MouseButtonRelease}
    btn = Qt.MouseButton.LeftButton if kind != "move" else Qt.MouseButton.NoButton
    held = Qt.MouseButton.LeftButton if kind != "release" else Qt.MouseButton.NoButton
    local = QPointF(pos)
    ev = QMouseEvent(types[kind], local, widget.mapToGlobal(local), btn, held, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, ev)
    QApplication.processEvents()


def test_viewer_edit_and_save(win, samples, done, tmp_path, monkeypatch):
    import pymupdf
    from PySide6.QtCore import QPointF, QRectF
    from PySide6.QtTest import QTest
    from ronypdf import viewer
    page = win.pages["viewer"]
    win.navigate("viewer")
    src = tmp_path / "doc.pdf"
    src.write_bytes(samples["a"].read_bytes())
    assert page.load(str(src))
    c = page.canvas
    QApplication.processEvents()
    c.render_visible()
    assert c.pages[0].rendered > 0 and c.page_count == 5
    assert not page.banner.isVisible()

    # búsqueda y navegación
    page.search.setText("Página 3")
    page._search_enter()
    assert len(c.hits) == 1 and page.hits_label.text() == "1 de 1"
    c.go_to_page(3)
    assert c.current_page == 3
    c.set_zoom(1.5)
    c.render_visible()
    c.go_to_page(0)

    p0, p1 = c.pages[0], c.pages[1]
    # texto nuevo escrito con el teclado (la «H» no debe activar la herramienta de resaltar)
    c.setFocus()
    page.set_tool("text")
    vp = c.viewport()
    _mouse(vp, "press", c.mapFromScene(p0.pos() + QPointF(72, 620)))
    _mouse(vp, "release", c.mapFromScene(p0.pos() + QPointF(72, 620)))
    QTest.keyClicks(c, "Hola mundo")
    typed = c.editing_text()
    assert typed is not None and typed.toPlainText() == "Hola mundo"
    assert c.tool == "select"
    typed.clearFocus()

    # editar una línea existente
    item = c.edit_text_at(p0, p0.pos() + QPointF(80, 92))
    assert isinstance(item, viewer.ReplaceTextItem) and item.toPlainText() == "Informe - Página 1"
    item.setPlainText("Reporte - Hoja 1")
    item.clearFocus()

    # formas con el ratón: rectángulo arrastrando
    page.set_tool("rect")
    a = c.mapFromScene(p0.pos() + QPointF(60, 520))
    b = c.mapFromScene(p0.pos() + QPointF(200, 580))
    _mouse(vp, "press", a)
    _mouse(vp, "move", a + (b - a) / 2)
    _mouse(vp, "move", b)
    _mouse(vp, "release", b)
    rects = [i for i in c.items_order if isinstance(i, viewer.ShapeItem) and i.kind == "rect"]
    assert len(rects) == 1 and abs(rects[0].r.width() - 140) < 2

    # atajo de teclado de herramienta (sin texto en edición)
    QTest.keyClick(c, "a")
    assert c.tool == "arrow"
    c.add_line("arrow", p0.pos() + QPointF(300, 520), p0.pos() + QPointF(400, 580))
    widgets.ctx.settings.set("comentarios_autor", "Ana Pérez")     # el resaltado lleva el nombre de quien lo hace
    c.add_highlight(p1, QRectF(p1.pos() + QPointF(60, 70), p1.pos() + QPointF(400, 110)))
    c.add_image(str(samples["png"]))
    c.add_shape("redact", QRectF(p1.pos() + QPointF(60, 150), p1.pos() + QPointF(530, 500)))
    extra = c.add_shape("ellipse", QRectF(p0.pos() + QPointF(10, 10), p0.pos() + QPointF(50, 50)))
    c.undo()
    assert extra.scene() is None
    page.set_tool("select")
    shot(win, "11_visor")
    assert c.has_changes() and page.b_save.text() == "Guardar"        # «Guardar» directo; la flecha, «Guardar como…»

    out = tmp_path / "doc_editado.pdf"
    monkeypatch.setattr(viewer, "ask_save", lambda *a, **k: str(out))
    page.save()
    wait(win)
    assert done[-1][0] == "Cambios guardados", done
    with pymupdf.open(out) as d:
        t0, t1 = d[0].get_text(), d[1].get_text()
        assert "Hola mundo" in t0 and "Reporte - Hoja 1" in t0 and "Informe - Página 1" not in t0
        assert [a.type[1] for a in d[1].annots()] == ["Highlight"]
        assert [a.info["title"] for a in d[1].annots()] == ["Ana Pérez"]
        assert "FOTO DE PRUEBA" not in t1          # la zona borrada se eliminó de verdad
        assert len(d[0].get_drawings()) >= 2
    # tras guardar, el visor muestra el archivo nuevo sin cambios pendientes
    assert c.path == str(out) and not c.has_changes()


def test_viewer_scanned_banner_and_ocr(win, scanned, done, tmp_path, monkeypatch):
    import pymupdf
    page = win.pages["viewer"]
    win.navigate("viewer")
    page.load(str(scanned))
    assert page.banner.isVisibleTo(page)
    page._go_ocr()
    ocr = win.pages["ocr"]
    assert win.stack.currentWidget() is ocr and ocr.picker.path == str(scanned)
    assert "escaneado" in ocr.status.text()
    shot(win, "12_ocr")
    out = tmp_path / "buscable.pdf"
    monkeypatch.setattr(tools, "ask_save", lambda *a, **k: str(out))
    ocr.revisar.setChecked(False)       # sin la pantalla de revisión: se guarda directamente
    ocr.run()
    wait(win, 60)
    assert done[-1][0] == "Texto reconocido", done
    with pymupdf.open(out) as d:
        assert "niñez" in d[0].get_text()



# ---------------------------------------------------------------- rediseño: menú, inicio y propiedades

def test_sidebar_collapse_sections_and_search(win):
    sb = win.sidebar
    win.navigate("home")
    sb.set_collapsed(False)
    assert sb.width() == 248 or sb.maximumWidth() == 248
    # contraer y expandir
    sb.set_collapsed(True)
    assert sb.is_collapsed() and sb.maximumWidth() == 64
    sb.rail_buttons["sec:convertir"].click()            # tocar una categoría del carril abre su menú flotante
    menu = sb.flyout
    assert menu is not None and menu.isVisible() and sb.is_collapsed()
    assert [f.key for f in menu.filas][:2] == ["ocr", "toimage"]
    next(f for f in menu.filas if f.key == "toimage").click()   # elegir una herramienta la abre
    assert win.stack.currentWidget() is win.pages["toimage"] and sb.rail_buttons["sec:convertir"].isChecked()
    win.navigate("home")
    sb.set_collapsed(False)
    assert not sb.is_collapsed() and sb.headers["convertir"].isChecked()
    # cerrar una sección oculta sus herramientas y lo recuerda
    sb.headers["organizar"].click()
    assert not sb.bodies["organizar"].isVisible()
    from ronypdf.widgets import ctx
    assert ctx.settings.get("sidebar_open")["organizar"] is False
    sb.headers["organizar"].click()
    # buscador: filtra y Enter abre la primera coincidencia
    sb.search.setText("contraseña")
    assert sb.items["protect"].isVisible() and not sb.items["merge"].isVisible()
    sb.search.returnPressed.emit()
    assert win.stack.currentWidget() is win.pages["protect"] and sb.search.text() == ""
    assert sb.items["merge"].isVisible()
    shot(win, "13_menu")


def test_viewer_auto_collapses_menu(win, samples):
    sb = win.sidebar
    sb.set_collapsed(False)
    win.navigate("viewer")
    assert sb.is_collapsed()
    win.navigate("home")
    assert not sb.is_collapsed()


def test_home_recent_files(win, samples, tmp_path):
    from ronypdf.widgets import ctx
    ctx.settings.clear_recent()
    win.navigate("viewer")
    f = tmp_path / "reciente.pdf"
    f.write_bytes(samples["a"].read_bytes())
    win.pages["viewer"].load(str(f), confirm=False)
    assert ctx.settings.recent()[0]["path"] == str(f) and ctx.settings.recent()[0]["pages"] == 5
    win.navigate("home")
    home = win.pages["home"]
    home.refresh_recent()
    assert home.rows.count() == 1 and home.b_clear.isVisible()
    shot(win, "14_inicio_recientes")
    home._clear_recent()
    assert ctx.settings.recent() == []


def test_viewer_properties_panel(win, samples, tmp_path):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QColor
    page = win.pages["viewer"]
    win.navigate("viewer")
    f = tmp_path / "props.pdf"
    f.write_bytes(samples["a"].read_bytes())
    page.load(str(f), confirm=False)
    c = page.canvas
    assert page.thumbs.count() == 5
    item = c.add_text(c.pages[0].pos() + QPointF(72, 600), "Hola")
    c.scene().clearSelection()
    item.setSelected(True)
    QApplication.processEvents()
    assert page.kind_chip.text() == "Texto agregado" and page.f_content.isVisibleTo(page)
    page.content_edit.setPlainText("Hola mundo")
    assert item.toPlainText() == "Hola mundo"
    page.swatches[2].click()                          # rojo de la paleta de texto
    assert item.defaultTextColor().name().upper() == "#D63A3A"
    page.size_spin.setValue(20)
    assert item.size == 20
    c.scene().clearSelection()
    page.set_tool("rect")
    assert page.shape_btn.isChecked() and page.shape_btn.text() == "Rectángulo"
    assert page.f_width.isVisibleTo(page) and not page.f_font.isVisibleTo(page)
    page.set_tool("select")


def test_botones_de_qt_en_espanol(win):
    """Las preguntas con QMessageBox.question mostraban «Yes» / «No»: el traductor los pone en español."""
    from PySide6.QtWidgets import QDialogButtonBox, QMessageBox
    app = QApplication.instance()
    traductor = widgets.instalar_traductor(app)
    try:
        box = QMessageBox(QMessageBox.Icon.Question, "Cambios sin guardar", "¿Descartarlos?",
                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        assert sorted(b.text() for b in box.buttons()) == ["&No", "&Sí"]
        caja = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        assert sorted(b.text() for b in caja.buttons()) == ["Aceptar", "Cancelar"]
        # lo que no está en la lista queda como lo trae Qt
        from PySide6.QtCore import QCoreApplication
        assert QCoreApplication.translate("QFileDialog", "Look in:") == "Look in:"
    finally:
        app.removeTranslator(traductor)
    import inspect
    from ronypdf import gui
    assert "instalar_traductor(app)" in inspect.getsource(gui.main)       # la aplicación real lo instala


def test_comprobar_herramientas_para_el_ejecutable(tmp_path, monkeypatch):
    """build.ps1 ejecuta «RonyPDF.exe --comprobar-herramientas» y falla si falta alguna página nueva."""
    from ronypdf import extensiones, gui
    informe = tmp_path / "informe.txt"
    assert gui.main(["RonyPDF.exe", "--comprobar-herramientas", str(informe)]) == 0
    texto = informe.read_text(encoding="utf-8")
    assert "faltan: ninguna" in texto and all(c in texto for c in extensiones.ESENCIALES)
    # si el ejecutable no trae los módulos (PyInstaller no los vio), la comprobación lo detecta
    monkeypatch.setattr(extensiones, "registros", lambda: [])
    assert gui.comprobar_herramientas(str(informe)) == 1
    assert "faltan: forms, print" in informe.read_text(encoding="utf-8")
    build = (Path(__file__).resolve().parents[1] / "build.ps1").read_text(encoding="utf-8")
    assert '"--hidden-import", "ronypdf.$($m.BaseName)"' in build and "--comprobar-herramientas" in build
