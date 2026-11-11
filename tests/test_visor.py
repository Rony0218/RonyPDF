"""Visor de primer nivel: seleccionar y copiar texto, marcadores, enlaces, dos páginas, pantalla completa,
modo lectura, modo noche, continuar donde quedaste y girar la vista."""

import os
import tempfile
import time

import pymupdf
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Los ajustes de las pruebas nunca deben tocar los reales.
os.environ.setdefault("RONYPDF_SETTINGS", os.path.join(tempfile.mkdtemp(prefix="ronypdf-visor-"), "RonyPDF.json"))

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QDesktopServices, QMouseEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ronypdf import core, viewer, visor_paneles, visor_texto, widgets  # noqa: E402
from ronypdf.gui import MainWindow, _light_palette  # noqa: E402
from ronypdf.style import QSS  # noqa: E402


# ---------------------------------------------------------------- utilidades

@pytest.fixture(scope="module")
def win():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setPalette(_light_palette())
    app.setStyleSheet(QSS)
    w = MainWindow()
    w.resize(1200, 800)
    w.show()
    app.processEvents()
    yield w
    # deja los ajustes como estaban para las demás pruebas
    for key in ("visor_vista", "visor_noche", "visor_posiciones"):
        widgets.ctx.settings.data.pop(key, None)
    widgets.ctx.settings.save()


class Calls(list):
    opened: list


@pytest.fixture
def done(monkeypatch):
    calls = Calls()

    def fake_done(parent, title, detail="", file=None, folder=None):
        calls.append((title, detail, file, folder))

    for mod in (widgets, viewer):
        monkeypatch.setattr(mod, "show_done", fake_done, raising=False)
    monkeypatch.setattr(widgets.QMessageBox, "warning", lambda *a, **k: calls.append(("warning", a[2])))
    monkeypatch.setattr(widgets.QMessageBox, "information", lambda *a, **k: calls.append(("info", a[2])))
    monkeypatch.setattr(widgets.QMessageBox, "question", lambda *a, **k: widgets.QMessageBox.StandardButton.Yes)
    from ronypdf import guardado_ui      # «Guardar / No guardar / Cancelar»: «No guardar» (antes, «Sí» descartaba)
    monkeypatch.setattr(guardado_ui, "preguntar_cambios", lambda *a, **k: "descartar")
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True)
    monkeypatch.setattr(visor_paneles, "confirmar_web", lambda *a, **k: False)
    calls.opened = opened
    return calls


@pytest.fixture
def page(win, done):
    """El visor, con la vista por defecto al empezar y al terminar cada prueba."""
    p = win.pages["viewer"]
    win.navigate("viewer")
    _reset_view(p)
    yield p
    _reset_view(p)


def _reset_view(p):
    p.set_fullscreen(False)
    p.set_reading_mode(False)
    p.set_view_mode("una", False)
    p.set_night(False)
    c = p.canvas
    while c.view_rot:
        c.view_rot = 0
        c.relayout()


def wait(timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        QApplication.processEvents()
        if not widgets.ctx.busy:
            QApplication.processEvents()
            return
        time.sleep(0.01)
    raise TimeoutError


def _mouse(widget, kind, pos, button=Qt.MouseButton.LeftButton, modifiers=Qt.KeyboardModifier.NoModifier):
    types = {"press": QEvent.Type.MouseButtonPress, "move": QEvent.Type.MouseMove,
             "release": QEvent.Type.MouseButtonRelease, "double": QEvent.Type.MouseButtonDblClick}
    btn = button if kind != "move" else Qt.MouseButton.NoButton
    held = button if kind in ("press", "double") else Qt.MouseButton.NoButton
    if kind == "move" and getattr(widget, "_held", None):
        held = widget._held
    local = QPointF(pos)
    ev = QMouseEvent(types[kind], local, widget.mapToGlobal(local), btn, held, modifiers)
    QApplication.sendEvent(widget, ev)
    QApplication.processEvents()


def drag(c, a, b, steps=4, button=Qt.MouseButton.LeftButton):
    vp = c.viewport()
    _mouse(vp, "press", a, button)
    vp._held = button
    for k in range(1, steps + 1):
        _mouse(vp, "move", a + (b - a) * (k / steps))
    vp._held = None
    _mouse(vp, "release", b, button)


def click(c, pos):
    _mouse(c.viewport(), "press", pos)
    _mouse(c.viewport(), "release", pos)


def word_scene(c, pno, i, fx=0.5):
    x0, y0, x1, y1 = c.words_for(pno)[i].rect
    return c.pages[pno].pos() + QPointF(x0 + (x1 - x0) * fx, (y0 + y1) / 2)


def vpos(c, sp):
    return QPointF(c.mapFromScene(sp))


def load_copy(page, src, tmp_path, name="doc.pdf"):
    f = tmp_path / name
    f.write_bytes(src.read_bytes())
    assert page.load(str(f), confirm=False)
    QApplication.processEvents()
    page.canvas.render_visible()
    return f


@pytest.fixture(scope="module")
def textos(tmp_path_factory):
    """PDF con dos bloques de dos renglones y una segunda página."""
    path = tmp_path_factory.mktemp("visor") / "textos.pdf"
    with pymupdf.open() as d:
        p = d.new_page(width=595, height=842)
        p.insert_text((72, 100), "Primer renglón del bloque", fontsize=14)
        p.insert_text((72, 118), "segundo renglón del bloque", fontsize=14)
        p.insert_text((72, 300), "Otro bloque aparte", fontsize=14)
        q = d.new_page(width=595, height=842)
        q.insert_text((72, 100), "Texto de la segunda página", fontsize=14)
        d.save(path)
    return path


@pytest.fixture(scope="module")
def enlaces(tmp_path_factory):
    """PDF de 4 páginas con un enlace interno, uno web, uno de correo y uno que abriría un programa.
    Se guarda y se reabre: antes de eso, get_links no ve los enlaces recién insertados."""
    path = tmp_path_factory.mktemp("links") / "indice.pdf"
    with pymupdf.open() as d:
        for i in range(4):
            p = d.new_page(width=595, height=842)
            p.insert_text((72, 80), f"Sección {i + 1}", fontsize=20)
        p0 = d[0]
        p0.insert_text((72, 200), "Ir a la sección tres", fontsize=14)
        p0.insert_link({"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(70, 185, 300, 205), "page": 2,
                        "to": pymupdf.Point(72, 300)})
        p0.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(70, 250, 300, 270),
                        "uri": "https://example.org/informe"})
        p0.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(70, 300, 300, 320),
                        "uri": "mailto:contacto@example.org"})
        p0.insert_link({"kind": pymupdf.LINK_LAUNCH, "from": pymupdf.Rect(70, 350, 300, 370), "file": "calc.exe"})
        p0.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(70, 400, 300, 420),
                        "uri": "javascript:alert(1)"})
        d.save(path)
    return path


# ---------------------------------------------------------------- visor_texto (sin interfaz)

def test_palabras_y_rango_en_orden_de_lectura(samples, textos):
    with pymupdf.open(samples["a"]) as d:
        words = visor_texto.palabras_pagina(d[0])
        assert [w.texto for w in words] == ["Informe", "-", "Página", "1"]
        text, rects = visor_texto.rango(words, 0, 3)
        assert text == "Informe - Página 1" and len(rects) == 1
        assert visor_texto.texto_pagina(d[0]) == "Informe - Página 1"
    with pymupdf.open(textos) as d:
        words = visor_texto.palabras_pagina(d[0])
        n = len(words)
        text, rects = visor_texto.rango(words, 0, n - 1)
        assert text == "Primer renglón del bloque\nsegundo renglón del bloque\n\nOtro bloque aparte"
        assert len(rects) == 3                        # un rectángulo por renglón
        assert rects[0][1] < rects[1][1] < rects[2][1]  # en orden, de arriba abajo
        # rango invertido: el mismo texto
        assert visor_texto.rango(words, n - 1, 0) == (text, rects)
        # parte de un renglón
        assert visor_texto.rango(words, 1, 2)[0] == "renglón del"
        # palabra bajo el punto, y la más cercana del mismo renglón si cae entre dos
        x0, y0, x1, y1 = words[1].rect
        assert visor_texto.palabra_en(words, (x0 + x1) / 2, (y0 + y1) / 2) == 1
        assert visor_texto.palabra_en(words, x1 + 1.5, (y0 + y1) / 2) in (1, 2)
        assert visor_texto.palabra_en(words, 500, 600) is None
        assert visor_texto.renglon_de(words, 1) == (0, 3)
        # al arrastrar: por debajo de todo el texto se toma la última palabra
        assert visor_texto.palabra_cercana(words, 80, 800) == n - 1
        assert visor_texto.palabra_cercana(words, 560, (y0 + y1) / 2) == 3   # a la derecha del renglón


def test_palabras_en_pagina_girada(samples):
    with pymupdf.open(samples["b"]) as d:
        page = d[1]
        assert page.rotation == 90
        words = visor_texto.palabras_pagina(page)
        assert [w.texto for w in words] == ["Anexo", "2"]
        visible = page.rect
        hit = core._visual(page, page.search_for("Anexo")[0])
        for w in words:
            r = pymupdf.Rect(w.rect)
            assert visible.contains(r)
        assert pymupdf.Rect(words[0].rect).intersects(hit)
        # en la página girada los renglones van de arriba abajo: el texto ocupa una franja vertical
        r = pymupdf.Rect(words[0].rect)
        assert r.height > r.width


def test_enlaces_pagina(enlaces):
    with pymupdf.open(enlaces) as d:
        links = visor_texto.enlaces_pagina(d[0])
    tipos = sorted((e.tipo, e.url or str(e.pagina)) for e in links)
    assert ("pagina", "2") in tipos
    assert ("web", "https://example.org/informe") in tipos and ("web", "mailto:contacto@example.org") in tipos
    otros = [e for e in links if e.tipo == "otro"]
    assert len(otros) == 2                     # el programa y el javascript
    interno = next(e for e in links if e.tipo == "pagina")
    assert interno.ayuda == "Ir a la página 3" and interno.punto == pytest.approx((72, 300), abs=1)
    assert visor_texto.normalizar("Introducción ANEXOS") == "introduccion anexos"


# ---------------------------------------------------------------- A. seleccionar y copiar

def test_arrastrar_selecciona_y_copia(page, samples, tmp_path):
    load_copy(page, samples["a"], tmp_path)
    c = page.canvas
    n_items = len(c.items_order)
    a = vpos(c, word_scene(c, 0, 0, 0.1))
    b = vpos(c, word_scene(c, 0, 3, 0.9))
    drag(c, a, b)
    assert c.selected_text() == "Informe - Página 1"
    assert c.overlay.rects and len(c.overlay.rects) == 1
    text = c.copy_selection()
    assert QApplication.clipboard().text() == text == "Informe - Página 1"
    assert page.tip_text.text() == "Texto copiado (18 caracteres)"
    # la selección no es un cambio: no se guarda ni entra en la lista de elementos
    assert not c.has_changes() and len(c.items_order) == n_items
    assert page.b_save.text() == "Guardar como…"
    assert c.collect_edits() == ([], 0)
    # Ctrl+C también copia
    QApplication.clipboard().setText("")
    c.setFocus()
    QTest.keyClick(c, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert QApplication.clipboard().text() == "Informe - Página 1"
    # un clic fuera la quita
    click(c, vpos(c, c.pages[0].pos() + QPointF(30, 700)))
    assert not c.has_selection() and not c.overlay.rects


def test_doble_clic_triple_clic_ctrl_a_y_esc(page, textos, tmp_path):
    load_copy(page, textos, tmp_path)
    c = page.canvas
    vp = c.viewport()
    p = vpos(c, word_scene(c, 0, 1))
    _mouse(vp, "press", p)
    _mouse(vp, "release", p)
    _mouse(vp, "double", p)
    _mouse(vp, "release", p)
    assert c.selected_text() == "renglón"
    # tercer clic: el renglón completo
    _mouse(vp, "press", p)
    _mouse(vp, "release", p)
    assert c.selected_text() == "Primer renglón del bloque"
    # Mayús + clic extiende la selección
    _mouse(vp, "press", vpos(c, word_scene(c, 0, 5)), modifiers=Qt.KeyboardModifier.ShiftModifier)
    _mouse(vp, "release", vpos(c, word_scene(c, 0, 5)))
    assert c.selected_text() == "Primer renglón del bloque\nsegundo renglón"
    c.setFocus()
    QTest.keyClick(c, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    assert c.selected_text() == "Primer renglón del bloque\nsegundo renglón del bloque\n\nOtro bloque aparte"
    QTest.keyClick(c, Qt.Key.Key_Escape)
    assert not c.has_selection() and c.selected_text() == ""
    # select_words y la API del lienzo
    c.select_words(0, 4, 7)
    assert c.selected_text() == "segundo renglón del bloque"
    c.clear_selection()
    assert c.selected_text() == ""


def test_seleccion_entre_paginas(page, textos, tmp_path):
    load_copy(page, textos, tmp_path)
    c = page.canvas
    c.set_zoom(0.35)
    c.go_to_page(0)
    QApplication.processEvents()
    a = vpos(c, word_scene(c, 0, 8, 0.1))          # «Otro»
    b = vpos(c, word_scene(c, 1, 2, 0.9))          # «la» de la página 2
    assert c.viewport().rect().contains(b.toPoint())
    drag(c, a, b, steps=8)
    assert c.selected_text() == "Otro bloque aparte\n\nTexto de la"
    assert len(c.overlay.rects) == 2


def test_resaltar_desde_la_seleccion_y_guardar(page, samples, tmp_path, monkeypatch, done):
    load_copy(page, samples["a"], tmp_path)
    c = page.canvas
    # el resaltado es un comentario con el nombre de quien lo hizo: sin nombre todavía, se pregunta al guardar
    widgets.ctx.settings.data.pop("comentarios_autor", None)
    preguntas = []
    monkeypatch.setattr(viewer.ViewerPage, "_preguntar_autor", lambda self: preguntas.append(1) or "Ana Pérez")
    c.select_words(0, 2, 3)                         # «Página 1»
    sel = list(c.overlay.rects)
    menu = c.selection_menu()
    textos = [a.text() for a in menu.actions()]
    assert textos == ["Copiar\tCtrl+C", "Resaltar", "Buscar en el documento"]
    items = c.highlight_selection()
    assert len(items) == 1 and isinstance(items[0], viewer.HighlightItem)
    assert items[0].rects == sel and not c.has_selection()
    assert c.has_changes() and items[0] in c.items_order
    out = tmp_path / "resaltado.pdf"
    monkeypatch.setattr(viewer, "ask_save", lambda *a, **k: str(out))
    page.save()
    wait()
    assert done[-1][0] == "Cambios guardados", done
    with pymupdf.open(out) as d:
        pg = d[0]
        annots = list(pg.annots())
        assert [a.type[1] for a in annots] == ["Highlight"]
        assert preguntas == [1] and annots[0].info["title"] == "Ana Pérez" and annots[0].info["creationDate"]
        rect = annots[0].rect
        words = [w for w in pg.get_text("words") if w[4] in ("Página", "1")]
        assert len(words) == 2
        for w in words:
            assert rect.intersects(pymupdf.Rect(w[:4]))
        assert not rect.intersects(pymupdf.Rect(pg.get_text("words")[0][:4]))   # «Informe» no


def test_buscar_desde_la_seleccion(page, samples, tmp_path):
    load_copy(page, samples["a"], tmp_path)
    c = page.canvas
    c.select_words(0, 0, 0)
    assert c.search_selection() == "Informe"
    assert page.search.text() == "Informe" and len(c.hits) == 5


def test_seleccionar_mueve_elementos_y_desplaza(page, samples, tmp_path):
    load_copy(page, samples["a"], tmp_path)
    c = page.canvas
    p0 = c.pages[0]
    shape = c.add_shape("rect", QRectF(p0.pos() + QPointF(100, 560), p0.pos() + QPointF(220, 640)))
    page.set_tool("select")
    start = shape.pos()
    a = vpos(c, p0.pos() + QPointF(160, 600))
    drag(c, a, a + QPointF(40, 30))
    assert (shape.pos() - start).manhattanLength() > 20     # se movió
    assert not c.has_selection()
    # zona vacía: desplaza y no selecciona
    c.set_zoom(2.0)
    c.go_to_page(0)
    QApplication.processEvents()
    bar = c.verticalScrollBar()
    v0 = bar.value()
    a = vpos(c, p0.pos() + QPointF(40, 130))
    drag(c, a, a + QPointF(0, -120))
    assert bar.value() > v0 and not c.has_selection()
    # con la barra espaciadora presionada, arrastrar sobre el texto también desplaza
    c.go_to_page(0)
    QApplication.processEvents()
    v0 = bar.value()
    c.setFocus()
    QTest.keyPress(c, Qt.Key.Key_Space)
    a = vpos(c, word_scene(c, 0, 0))
    drag(c, a, a + QPointF(0, -60))
    QTest.keyRelease(c, Qt.Key.Key_Space)
    assert bar.value() > v0 and not c.has_selection()
    # el botón central también desplaza
    v0 = bar.value()
    a = vpos(c, word_scene(c, 0, 2))
    drag(c, a, a + QPointF(0, -50), button=Qt.MouseButton.MiddleButton)
    assert bar.value() > v0 and not c.has_selection()
    # las herramientas de edición siguen funcionando igual
    page.set_tool("highlight")
    a = vpos(c, word_scene(c, 0, 0, 0.0))
    b = vpos(c, word_scene(c, 0, 3, 1.0))
    drag(c, a, b)
    assert isinstance(c.items_order[-1], viewer.HighlightItem)
    assert c.tool == "highlight"


def test_pdf_escaneado_avisa_al_seleccionar(page, scanned):
    page.load(str(scanned), confirm=False)
    c = page.canvas
    QApplication.processEvents()
    c._last_scan_msg = 0
    click(c, vpos(c, c.pages[0].pos() + QPointF(300, 400)))
    assert page.tip_text.text() == viewer.SCANNED_MSG
    page._refresh_props()
    c.select_all_page()
    assert page.tip_text.text() == viewer.SCANNED_MSG and not c.has_selection()


# ---------------------------------------------------------------- B. marcadores

def test_panel_de_marcadores(page, samples, tmp_path):
    load_copy(page, samples["a"], tmp_path)
    m = page.marcadores
    assert m.total == 3 and page.tab_marks.text() == "Marcadores (3)"
    page.show_left_tab(1)
    assert page.left_stack.currentWidget() is m
    m.filtro.setText("anex")
    vis = m.visibles()
    assert [it.text(0) for it in vis] == ["Anexos"]
    m._activar(vis[0])
    assert page.canvas.current_page == 4
    assert page.canvas.can_go_back()                 # el salto se puede deshacer con «Volver»
    m.filtro.setText("INTRODUCCION")                 # sin tildes ni mayúsculas
    assert [it.text(0) for it in m.visibles()] == ["Introducción"]
    m.filtro.clear()
    assert len(m.visibles()) == 3
    # la sección actual queda señalada
    page.canvas.go_to_page(3)
    assert m._marcado.text(0) == "Capítulo dos" and m._marcado.font(0).bold()
    page.canvas.go_to_page(0)
    assert m._marcado.text(0) == "Introducción"
    # un PDF sin marcadores
    load_copy(page, samples["b"], tmp_path, "sin.pdf")
    assert m.total == 0 and m.aviso.isVisibleTo(m) and m.aviso.text() == "Este PDF no tiene marcadores."
    assert page.tab_marks.text() == "Marcadores (0)"
    page.show_left_tab(0)


def test_marcadores_anidados_filtro_conserva_padres():
    m = visor_paneles.PanelMarcadores()
    toc = [[1, "Capítulo uno", 1, {"collapse": True}], [2, "Población atendida", 2, {}],
           [2, "Métodos", 3, {"to": pymupdf.Point(0, 400)}], [1, "Malo", 99, {}], [1, "Anexos", 4, {}]]
    m.cargar(toc, 4)
    assert m.total == 5
    padre = m.entradas[0]
    assert padre.childCount() == 2 and not padre.isExpanded()
    m.filtro.setText("poblacion")
    assert [it.text(0) for it in m.visibles()] == ["Capítulo uno", "Población atendida"]
    assert padre.isExpanded()
    m.filtro.setText("")
    assert len(m.visibles()) == 5 and not padre.isExpanded()   # vuelve como estaba
    got = []
    m.ir_a.connect(lambda p, y: got.append((p, y)))
    m._activar(m.entradas[2])
    m._activar(m.entradas[3])                                  # página fuera de rango: se ignora
    assert got == [(2, 400.0)]
    m.filtro.setText("zzz")
    assert m.aviso.isVisibleTo(m) and "Ningún marcador" in m.aviso.text()


# ---------------------------------------------------------------- C. enlaces

def test_enlaces_en_el_lienzo(page, enlaces, tmp_path, monkeypatch, done):
    load_copy(page, enlaces, tmp_path)
    c = page.canvas
    p0 = c.pages[0]
    assert not page.b_back.isVisibleTo(page)
    # pasar por encima: mano y ayuda
    _mouse(c.viewport(), "move", vpos(c, p0.pos() + QPointF(150, 195)))
    assert c.viewport().cursor().shape() == Qt.CursorShape.PointingHandCursor
    assert c._hover_link is not None and c._hover_link.ayuda == "Ir a la página 3"
    # clic sin arrastrar: salta al destino y se puede volver
    click(c, vpos(c, p0.pos() + QPointF(150, 195)))
    assert c.current_page == 2 and not c.has_selection()
    assert page.b_back.isVisibleTo(page)
    centro = c.mapToScene(c.viewport().rect().center())
    assert c.pages[2].sceneBoundingRect().contains(centro)
    page.go_back()
    assert c.current_page == 0 and c.can_go_forward()
    page.go_forward()
    assert c.current_page == 2
    page.go_back()
    assert c.current_page == 0
    # arrastrar sobre el enlace selecciona el texto en lugar de seguirlo
    drag(c, vpos(c, word_scene(c, 0, 2, 0.1)), vpos(c, word_scene(c, 0, 6, 0.9)))
    assert c.current_page == 0 and c.selected_text() == "Ir a la sección tres"
    c.clear_selection()
    # web: solo con confirmación
    click(c, vpos(c, p0.pos() + QPointF(150, 260)))
    assert done.opened == []
    monkeypatch.setattr(visor_paneles, "confirmar_web", lambda *a, **k: True)
    click(c, vpos(c, p0.pos() + QPointF(150, 260)))
    assert done.opened == ["https://example.org/informe"]
    click(c, vpos(c, p0.pos() + QPointF(150, 310)))
    assert done.opened[-1] == "mailto:contacto@example.org"
    # otros tipos: nunca se abren
    click(c, vpos(c, p0.pos() + QPointF(150, 360)))
    click(c, vpos(c, p0.pos() + QPointF(150, 410)))
    assert len(done.opened) == 2
    assert page.tip_text.text() == "Por seguridad, RonyPDF no abre este tipo de enlace."


def test_atajos_alt_izquierda(page, enlaces, tmp_path):
    load_copy(page, enlaces, tmp_path)
    c = page.canvas
    c.go_to_dest(3, (0, 100))
    assert c.current_page == 3
    page.window().activateWindow()
    QApplication.processEvents()
    c.setFocus()
    QTest.keyClick(c, Qt.Key.Key_Left, Qt.KeyboardModifier.AltModifier)
    assert c.current_page == 0
    QTest.keyClick(c, Qt.Key.Key_Right, Qt.KeyboardModifier.AltModifier)
    assert c.current_page == 3
    # Ctrl+H (modo lectura), F11 (pantalla completa) y Ctrl+G (número de página)
    QTest.keyClick(c, Qt.Key.Key_H, Qt.KeyboardModifier.ControlModifier)
    assert page._reading and not page.toolbar.isVisibleTo(page)
    QTest.keyClick(c, Qt.Key.Key_H, Qt.KeyboardModifier.ControlModifier)
    assert not page._reading and page.toolbar.isVisibleTo(page)
    QTest.keyClick(c, Qt.Key.Key_F11)
    assert page._full
    QTest.keyClick(c, Qt.Key.Key_Escape)
    assert not page._full and page.header.isVisibleTo(page)
    QTest.keyClick(c, Qt.Key.Key_G, Qt.KeyboardModifier.ControlModifier)
    assert page.page_spin.hasFocus()


# ---------------------------------------------------------------- D. dos páginas

def test_vista_de_dos_paginas(page, samples, tmp_path, monkeypatch, done):
    load_copy(page, samples["a"], tmp_path)
    c = page.canvas
    p = c.pages
    # un texto agregado en la página 3 y una línea editada, antes de cambiar de vista
    texto = c.add_text(p[2].pos() + QPointF(100, 620), "Nota en la tres")
    rel = texto.pos() - p[2].pos()
    edit = c.edit_text_at(p[2], p[2].pos() + QPointF(80, 92))
    edit.setPlainText("Informe - Hoja 3")
    edit.clearFocus()
    rel_edit = edit.pos() - p[2].pos()
    page.set_view_mode("dos")
    assert c.view_mode == "dos" and widgets.ctx.settings.get("visor_vista") == "dos"
    assert p[0].y() == p[1].y() and p[2].y() == p[3].y() and p[4].y() > p[2].y()
    assert p[1].x() > p[0].x() + p[0].w and p[3].x() > p[2].x()
    # «Ajustar al ancho» usa el ancho de la fila
    c.set_fit("width")
    vp_w = c.viewport().width()
    assert c.zoom * (p[0].w * 2 + viewer.GAP + 2 * viewer.MARGIN) <= vp_w
    assert c.zoom * (p[0].w + 2 * viewer.MARGIN) < vp_w * 0.6
    # los elementos siguen sobre su página
    assert (texto.pos() - p[2].pos() - rel).manhattanLength() < 0.01
    assert (edit.pos() - p[2].pos() - rel_edit).manhattanLength() < 0.01 and edit.unchanged() is False
    assert edit.cover.sceneBoundingRect().intersects(p[2].sceneBoundingRect())
    # navegación
    c.go_to_page(3)
    assert c.current_page == 3
    assert c.page_index_at_y(p[4].y() + 10) == 4
    # portada sola
    page.set_view_mode("dos", True)
    assert widgets.ctx.settings.get("visor_vista") == "dos_portada"
    assert p[1].y() > p[0].y() and p[1].y() == p[2].y() and p[3].y() == p[4].y()
    assert p[0].x() >= p[1].x() + p[1].w                       # la portada va a la derecha
    assert (texto.pos() - p[2].pos() - rel).manhattanLength() < 0.01
    # al guardar, todo queda en la página 3 y en la misma posición
    out = tmp_path / "dos.pdf"
    monkeypatch.setattr(viewer, "ask_save", lambda *a, **k: str(out))
    page.save()
    wait()
    assert done[-1][0] == "Cambios guardados", done
    with pymupdf.open(out) as d:
        assert "Nota en la tres" in d[2].get_text() and "Nota en la tres" not in d[3].get_text()
        assert "Informe - Hoja 3" in d[2].get_text() and "Página 3" not in d[2].get_text()
        w = next(w for w in d[2].get_text("words") if w[4] == "Nota")
        assert abs(w[0] - 100) < 2 and 600 < w[3] < 640
    # volver a una página (tras guardar, el visor muestra el archivo nuevo)
    page.set_view_mode("una")
    p = c.pages
    assert c.path == str(out) and p[1].y() > p[0].y() and abs(p[0].x() - p[1].x()) < 0.01


# ---------------------------------------------------------------- E. pantalla completa y modo lectura

def test_modo_lectura_y_pantalla_completa(page, samples, tmp_path, win):
    load_copy(page, samples["a"], tmp_path)
    sb = win.sidebar
    assert sb.is_collapsed()                      # el visor contrae el menú solo
    page.set_reading_mode(True)
    assert not page.toolbar.isVisibleTo(page) and not page.left_panel.isVisibleTo(page)
    assert not page.props_panel.isVisibleTo(page)
    assert page.header.isVisibleTo(page) and page.footer.isVisibleTo(page)
    assert page.view_menu.act_lectura.isChecked()
    page.set_reading_mode(False)
    assert page.toolbar.isVisibleTo(page) and page.left_panel.isVisibleTo(page) and page.props_panel.isVisibleTo(page)
    # pantalla completa con el menú contraído
    page.set_fullscreen(True)
    QApplication.processEvents()
    assert not sb.isVisibleTo(win) and not page.header.isVisibleTo(page)
    assert not page.toolbar.isVisibleTo(page) and page.footer.isVisibleTo(page) and page.b_exit_full.isVisibleTo(page)
    assert win.windowState() & Qt.WindowState.WindowFullScreen
    page._escape_fullscreen()                     # Esc
    QApplication.processEvents()
    assert sb.isVisibleTo(win) and sb.is_collapsed()
    assert page.header.isVisibleTo(page) and page.toolbar.isVisibleTo(page) and page.left_panel.isVisibleTo(page)
    assert not win.windowState() & Qt.WindowState.WindowFullScreen
    # con el menú expandido y el modo lectura puesto antes: al salir vuelve igual
    sb.set_collapsed(False, remember=False)
    page.set_reading_mode(True)
    page.toggle_fullscreen()                      # F11
    assert not sb.isVisibleTo(win)
    page.toggle_fullscreen()
    assert sb.isVisibleTo(win) and not sb.is_collapsed()
    assert page._reading and not page.toolbar.isVisibleTo(page)
    page.set_reading_mode(False)
    # salir del visor apaga la pantalla completa
    page.set_fullscreen(True)
    win.navigate("home")
    assert not page._full and sb.isVisibleTo(win)
    win.navigate("viewer")


def test_teclas_de_lectura(page, samples, tmp_path):
    load_copy(page, samples["a"], tmp_path)
    c = page.canvas
    c.setFocus()
    bar = c.verticalScrollBar()
    v0 = bar.value()
    QTest.keyClick(c, Qt.Key.Key_PageDown)
    assert bar.value() > v0
    v1 = bar.value()
    QTest.keyClick(c, Qt.Key.Key_Space)
    assert bar.value() > v1
    QTest.keyClick(c, Qt.Key.Key_PageUp)
    assert bar.value() < bar.maximum()
    QTest.keyClick(c, Qt.Key.Key_End)
    assert c.current_page == 4
    QTest.keyClick(c, Qt.Key.Key_Home)
    assert c.current_page == 0
    # las letras siguen eligiendo herramienta
    QTest.keyClick(c, "t")
    assert c.tool == "text"
    QTest.keyClick(c, "v")
    assert c.tool == "select"


# ---------------------------------------------------------------- F, G, H

def _pixel(page_item, x, y):
    img = page_item.pix.pixmap().toImage()
    s = page_item.rendered
    return img.pixelColor(int(x * s), int(y * s))


def test_modo_noche(page, samples, tmp_path):
    f = load_copy(page, samples["a"], tmp_path)
    c = page.canvas
    c.add_text(c.pages[0].pos() + QPointF(72, 650), "Hola")
    before = [(e.kind, e.page, e.lines) for e in c.collect_edits()[0]]
    assert _pixel(c.pages[0], 20, 20).lightness() > 240            # margen blanco
    page.set_night(True)
    c.render_visible()
    assert _pixel(c.pages[0], 20, 20).lightness() < 15             # invertido: negro
    assert c.backgroundBrush().color() == viewer.CANVAS_BG_NIGHT
    assert widgets.ctx.settings.get("visor_noche") is True
    assert [(e.kind, e.page, e.lines) for e in c.collect_edits()[0]] == before
    page.set_night(False)
    c.render_visible()
    assert _pixel(c.pages[0], 20, 20).lightness() > 240
    assert f.read_bytes() == samples["a"].read_bytes()             # el archivo no cambia


def test_continuar_donde_quedaste(page, samples, tmp_path):
    f = load_copy(page, samples["a"], tmp_path, "largo.pdf")
    page.canvas.go_to_page(3)
    load_copy(page, samples["b"], tmp_path, "otro.pdf")          # al cambiar de documento se recuerda
    pos = widgets.ctx.settings.get("visor_posiciones")
    assert pos[os.path.normcase(os.path.abspath(f))] == 4
    page.load(str(f), confirm=False)
    assert page.canvas.current_page == 3
    assert page.tip_text.text() == "Continuaste en la página 4."
    # volver a la primera página borra la posición; nunca se guardan más de 50
    page.canvas.go_to_page(0)
    page._remember_position()
    assert os.path.normcase(os.path.abspath(f)) not in widgets.ctx.settings.get("visor_posiciones")
    many = {f"C:\\doc{k}.pdf": 3 for k in range(60)}
    widgets.ctx.settings.set("visor_posiciones", many)
    page.canvas.go_to_page(2)
    page._remember_position()
    pos = widgets.ctx.settings.get("visor_posiciones")
    assert len(pos) == viewer.MAX_POSITIONS and pos[os.path.normcase(os.path.abspath(f))] == 3


def test_girar_la_vista(page, samples, tmp_path):
    f = load_copy(page, samples["a"], tmp_path)
    c = page.canvas
    p0 = c.pages[0]
    page.rotate_view()
    assert c.view_rot == 90 and "90°" in page.view_menu.act_girar.text()
    assert page.tip_text.text() == viewer.ROTATED_MSG
    assert not page.tool_buttons["text"].isEnabled() and page.tool_buttons["select"].isEnabled()
    page.set_tool("text")
    assert c.tool == "select"
    # en pantalla, las páginas quedan una debajo de otra y giradas (ancho y alto intercambiados)
    r0 = c.mapFromScene(p0.sceneBoundingRect()).boundingRect()
    r1 = c.mapFromScene(c.pages[1].sceneBoundingRect()).boundingRect()
    assert r0.width() > r0.height() and r1.top() > r0.bottom()
    # la selección, la búsqueda y los enlaces siguen funcionando
    c.go_to_page(0)
    QApplication.processEvents()
    a = vpos(c, word_scene(c, 0, 0, 0.1))
    b = vpos(c, word_scene(c, 0, 3, 0.9))
    assert c.viewport().rect().contains(a.toPoint()) and c.viewport().rect().contains(b.toPoint())
    drag(c, a, b)
    assert c.selected_text() == "Informe - Página 1"
    assert c.search("Página 3") == 1
    c.render_visible()
    assert p0.rendered > 0
    for _ in range(3):
        page.rotate_view()
    assert c.view_rot == 0 and page.tool_buttons["text"].isEnabled()
    # con cambios pendientes no se gira
    c.add_text(p0.pos() + QPointF(72, 650), "Cambio")
    page.rotate_view()
    assert c.view_rot == 0 and page.tip_text.text() == "Guarda o descarta tus cambios antes de girar la vista."
    assert f.read_bytes() == samples["a"].read_bytes()


def test_pagina_girada_del_documento_selecciona_bien(page, samples, tmp_path):
    load_copy(page, samples["b"], tmp_path)
    c = page.canvas
    c.go_to_page(1)
    QApplication.processEvents()
    words = c.words_for(1)
    assert [w.texto for w in words] == ["Anexo", "2"]
    a = vpos(c, word_scene(c, 1, 0))
    _mouse(c.viewport(), "press", a)
    _mouse(c.viewport(), "release", a)
    _mouse(c.viewport(), "double", a)
    _mouse(c.viewport(), "release", a)
    assert c.selected_text() == "Anexo"
    c.select_words(1, 0, 1)
    assert c.selected_text() == "Anexo 2"
    r = c.overlay.rects[0]
    assert c.pages[1].sceneBoundingRect().contains(r)


def test_pdf_con_contrasena_y_vista_girada_a_dos_paginas(page, samples, tmp_path, monkeypatch):
    monkeypatch.setattr(widgets.QInputDialog, "getText", lambda *a, **k: ("1234", True))
    f = tmp_path / "secreto.pdf"
    f.write_bytes(samples["locked"].read_bytes())
    assert page.load(str(f), confirm=False)
    c = page.canvas
    assert c.password == "1234" and page.marcadores.total == 3
    c.select_words(1, 0, 3)
    assert c.selected_text() == "Informe - Página 2"
    # dos páginas con la vista girada: cada fila sigue teniendo dos páginas, una al lado de la otra
    page.set_view_mode("dos")
    page.rotate_view()
    assert c.view_rot == 90
    r = [c.mapFromScene(p.sceneBoundingRect()).boundingRect() for p in c.pages[:3]]
    assert abs(r[0].top() - r[1].top()) <= 1 and r[1].left() > r[0].right() and r[2].top() > r[0].bottom()
    assert r[0].width() > r[0].height()          # girada: más ancha que alta
    c.go_to_page(2)
    assert c.current_page == 2
    c.select_words(2, 0, 3)
    assert c.selected_text() == "Informe - Página 3"
    assert c.overlay.rects and c.pages[2].sceneBoundingRect().contains(c.overlay.rects[0])
