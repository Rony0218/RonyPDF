"""Pruebas de «Imprimir»: planificación de hojas, PDF listo para imprimir, vista previa e interfaz.

Ninguna prueba envía trabajos a una impresora real: todo sale por imprimir_a_pdf o por un QPdfWriter
(el motor PDF de QPrinter), y la lista de impresoras se reemplaza por una falsa.
"""

import os
import re
import tempfile
import time
from dataclasses import replace

import pymupdf
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("RONYPDF_SETTINGS", os.path.join(tempfile.mkdtemp(prefix="ronypdf-"), "RonyPDF.json"))

from PySide6.QtWidgets import QApplication  # noqa: E402

APP = QApplication.instance() or QApplication([])

from ronypdf import core  # noqa: E402
from ronypdf import imprimir as imp  # noqa: E402
from ronypdf.imprimir import OpcionesImpresion as Op  # noqa: E402

CARTA = (612.0, 792.0)


def centro(c):
    x, y, w, h = c.destino
    return (x + w / 2, y + h / 2)


@pytest.fixture(scope="module")
def colores(tmp_path_factory):
    """Tres páginas, cada una llena de un color distinto (rojo, verde, azul)."""
    path = tmp_path_factory.mktemp("colores") / "colores.pdf"
    with pymupdf.open() as d:
        for c in ((1, 0, 0), (0, 0.6, 0), (0, 0, 1)):
            p = d.new_page(width=612, height=792)
            p.draw_rect(p.rect, color=c, fill=c)
        d.save(path)
    return path


def color_de(page):
    pix = page.get_pixmap(dpi=20)
    r, g, b = pix.pixel(pix.width // 2, pix.height // 2)
    return "R" if r > 200 and g < 100 else "G" if g > 100 and r < 100 and b < 100 else "B" if b > 200 else "?"


def tinta(page, rect_frac):
    """¿Hay algo dibujado (no blanco) en esa parte de la página?"""
    pix = page.get_pixmap(dpi=30)
    x0, y0, w, h = rect_frac
    for yy in range(int(y0 * pix.height) + 2, int((y0 + h) * pix.height) - 2, 3):
        for xx in range(int(x0 * pix.width) + 2, int((x0 + w) * pix.width) - 2, 3):
            if min(pix.pixel(xx, yy)) < 200:
                return True
    return False


# ---------------------------------------------------------------- planificar

def test_varias_cuatro_por_hoja_orden_horizontal_y_vertical():
    tam = [(595, 842)] * 10
    hojas = imp.planificar(tam, CARTA, Op(modo="varias", por_hoja=4))
    assert len(hojas) == 3 and [len(h.celdas) for h in hojas] == [4, 4, 2]
    assert not hojas[0].horizontal
    c = [centro(x) for x in hojas[0].celdas]
    assert [x.pagina for x in hojas[0].celdas] == [0, 1, 2, 3]
    assert c[0][0] < c[1][0] and abs(c[0][1] - c[1][1]) < 1e-6        # 1 y 2 en la misma fila
    assert c[2][1] > c[0][1] and abs(c[2][0] - c[0][0]) < 1e-6        # 3 debajo de 1
    v = imp.planificar(tam, CARTA, Op(modo="varias", por_hoja=4, orden="vertical"))
    c = [centro(x) for x in v[0].celdas]
    assert c[1][1] > c[0][1] and abs(c[1][0] - c[0][0]) < 1e-6        # 2 debajo de 1
    assert c[2][0] > c[0][0] and abs(c[2][1] - c[0][1]) < 1e-6        # 3 a la derecha de 1
    assert [x.pagina for x in v[2].celdas] == [8, 9]


def test_dos_y_seis_por_hoja_usan_la_hoja_apaisada():
    tam = [(612, 792)] * 7
    for n, hojas_esperadas in ((2, 4), (6, 2)):
        hojas = imp.planificar(tam, CARTA, Op(modo="varias", por_hoja=n))
        assert len(hojas) == hojas_esperadas and all(h.horizontal for h in hojas)
    dos = imp.planificar(tam, CARTA, Op(modo="varias", por_hoja=2))
    (a, b) = dos[0].celdas
    assert centro(a)[0] < 0.5 < centro(b)[0]                          # lado a lado
    # con orientación vertical forzada, las dos van una encima de la otra
    vert = imp.planificar(tam, CARTA, Op(modo="varias", por_hoja=2, orientacion="vertical"))
    assert not vert[0].horizontal


def test_orden_folleto():
    assert imp.orden_folleto(8) == [(7, 0), (1, 6), (5, 2), (3, 4)]
    assert [(a + 1, b + 1) for a, b in imp.orden_folleto(8)] == [(8, 1), (2, 7), (6, 3), (4, 5)]
    assert imp.orden_folleto(5) == [(None, 0), (1, None), (None, 2), (3, 4)]
    assert len(imp.orden_folleto(9)) == 6 and imp.orden_folleto(0) == []


def test_folleto_en_hojas_y_encuadernacion_derecha():
    tam = [(612, 792)] * 5
    hojas = imp.planificar(tam, CARTA, Op(modo="folleto"))
    assert len(hojas) == 4 and all(h.horizontal for h in hojas)
    assert [h.cara for h in hojas] == ["frente", "dorso", "frente", "dorso"]
    izq, der = hojas[0].celdas
    assert izq.pagina is None and der.pagina == 0 and centro(izq)[0] < centro(der)[0]
    derecha = imp.planificar(tam, CARTA, Op(modo="folleto", encuadernacion="derecha"))
    izq, der = derecha[0].celdas
    assert izq.pagina == 0 and der.pagina is None and centro(izq)[0] < centro(der)[0]


def test_rango_impares_pares_e_inverso():
    tam = [(612, 792)] * 10
    pags = lambda **k: [h.paginas[0] for h in imp.planificar(tam, CARTA, Op(**k))]  # noqa: E731
    assert pags(paginas="2-5") == [1, 2, 3, 4]
    assert pags(paginas="2-5", subconjunto="impares") == [2, 4]       # páginas 3 y 5
    assert pags(paginas="2-5", subconjunto="pares") == [1, 3]         # páginas 2 y 4
    assert pags(paginas="2-5", invertir=True) == [4, 3, 2, 1]
    assert pags(subconjunto="pares", invertir=True) == [9, 7, 5, 3, 1]
    with pytest.raises(core.UserError):
        imp.planificar(tam, CARTA, Op(paginas="12"))
    with pytest.raises(core.UserError):
        imp.planificar(tam, CARTA, Op(paginas="2", subconjunto="impares"))


def test_poster_carta_al_200():
    op = Op(modo="poster", escala=200)
    hojas = imp.planificar([CARTA], CARTA, op)
    assert len(hojas) == 4 and not hojas[0].horizontal
    rec = [h.celdas[0].recorte for h in hojas]
    # cubren toda la página…
    assert min(r[0] for r in rec) == 0 and min(r[1] for r in rec) == 0
    assert max(r[0] + r[2] for r in rec) == pytest.approx(1) and max(r[1] + r[3] for r in rec) == pytest.approx(1)
    a, b, c, _ = rec                                                   # A1, A2, B1, B2
    # …y las vecinas se solapan exactamente 5 mm (medidos sobre el póster impreso)
    cols, filas, escala_real, _ = imp.cuadricula_poster(CARTA, CARTA, op)
    assert (cols, filas) == (2, 2) and 190 < escala_real <= 200
    ancho_poster = CARTA[0] * escala_real / 100
    assert (a[0] + a[2] - b[0]) * ancho_poster == pytest.approx(5 * imp.MM, rel=1e-3)
    alto_poster = CARTA[1] * escala_real / 100
    assert (a[1] + a[3] - c[1]) * alto_poster == pytest.approx(5 * imp.MM, rel=1e-3)
    assert [h.etiqueta for h in hojas] == ["A1", "A2", "B1", "B2"] and all(h.marcas for h in hojas)
    # a 300 % hacen falta más hojas
    assert len(imp.planificar([CARTA], CARTA, Op(modo="poster", escala=300))) == 9


def test_ajustar_real_y_reducir():
    # página apaisada en papel vertical: se gira
    h = imp.planificar([(792, 612)], CARTA, Op())[0]
    assert h.celdas[0].girar == 90 and not h.horizontal
    assert h.celdas[0].destino == pytest.approx((0, 0, 1, 1))
    assert imp.planificar([(792, 612)], CARTA, Op(orientacion="vertical"))[0].celdas[0].girar == 0
    assert imp.planificar([(792, 612)], CARTA, Op(orientacion="horizontal"))[0].horizontal
    # tamaño real: media carta queda al 100 % y centrada
    c = imp.planificar([(306, 396)], CARTA, Op(modo="real"))[0].celdas[0]
    assert c.destino == pytest.approx((0.25, 0.25, 0.5, 0.5)) and c.recorte is None
    # tamaño real de una página más grande que el papel: se corta (y se dice qué parte)
    c = imp.planificar([(1224, 792)], CARTA, Op(modo="real", orientacion="vertical"))[0].celdas[0]
    assert c.destino == pytest.approx((0, 0, 1, 1)) and c.recorte == pytest.approx((0.25, 0, 0.5, 1))
    # reducir: la pequeña no cambia, la grande se achica hasta caber
    assert imp.planificar([(306, 396)], CARTA, Op(modo="reducir"))[0].celdas[0].destino == \
        pytest.approx((0.25, 0.25, 0.5, 0.5))
    assert imp.planificar([(1224, 1584)], CARTA, Op(modo="reducir"))[0].celdas[0].destino == \
        pytest.approx((0, 0, 1, 1))
    # escala personalizada al 50 %, sin centrar
    c = imp.planificar([CARTA], CARTA, Op(modo="escala", escala=50, centrar=False))[0].celdas[0]
    assert c.destino == pytest.approx((0, 0, 0.5, 0.5))


def test_copias_y_doble_cara():
    tam = [CARTA] * 3
    pags = lambda hs: [h.paginas[0] if h.paginas else None for h in hs]  # noqa: E731
    assert pags(imp.planificar(tam, CARTA, Op(copias=2))) == [0, 1, 2, 0, 1, 2]
    assert pags(imp.planificar(tam, CARTA, Op(copias=2, intercalar=False))) == [0, 0, 1, 1, 2, 2]
    # doble cara: cada copia empieza en una hoja nueva (blanco al final de la copia)
    d = imp.planificar(tam, CARTA, Op(copias=2, doble_cara="largo"))
    assert pags(d) == [0, 1, 2, None, 0, 1, 2, None]
    assert [h.cara for h in d[:4]] == ["frente", "dorso", "frente", "dorso"]
    assert imp.contar(d, Op(doble_cara="largo")) == (8, 4)
    # sin intercalar en doble cara se repite la hoja de papel completa (frente y dorso)
    u = imp.planificar(tam, CARTA, Op(copias=2, intercalar=False, doble_cara="corto"))
    assert pags(u) == [0, 1, 0, 1, 2, None, 2, None]
    # doble cara manual: frentes y dorsos (al revés si se pide)
    m = imp.planificar([CARTA] * 5, CARTA, Op(doble_cara="manual"))
    assert pags(imp.seleccionar_caras(m, "frente")) == [0, 2, 4]
    assert pags(imp.seleccionar_caras(m, "dorso")) == [1, 3, None]
    assert pags(imp.seleccionar_caras(m, "dorso", invertir_dorso=True)) == [None, 3, 1]


def test_planificar_quinientas_paginas_es_instantaneo():
    tam = [(595, 842), (842, 595)] * 250
    t = time.perf_counter()
    for modo in ("ajustar", "varias", "folleto"):
        imp.planificar(tam, CARTA, Op(modo=modo, por_hoja=2))
    assert time.perf_counter() - t < 1.0
    assert len(imp.planificar(tam, CARTA, Op(modo="varias", por_hoja=2))) == 250


def test_tamanos_mezclados_y_rotate(samples):
    with core.open_pdf(samples["b"]) as d:
        tam = imp._tamanos(d)
    assert tam[1] == (792, 612)                                        # la 2 está girada (/Rotate 90)
    hojas = imp.planificar(tam, CARTA, Op())
    assert [h.celdas[0].girar for h in hojas] == [0, 90, 0]


# ---------------------------------------------------------------- PDF listo para imprimir

def test_imprimir_a_pdf_una_hoja_por_cara(samples, tmp_path):
    out = tmp_path / "a.pdf"
    n = imp.imprimir_a_pdf(samples["a"], out, Op(ppp=150))
    with pymupdf.open(out) as d:
        assert n == d.page_count == 5
        assert d[0].rect.width == pytest.approx(612, abs=1) and d[0].rect.height == pytest.approx(792, abs=1)
        assert tinta(d[0], (0.1, 0.05, 0.8, 0.5))
    # dos por hoja: tinta en las dos mitades; en la última hoja solo en la izquierda
    out2 = tmp_path / "a2.pdf"
    op = Op(modo="varias", por_hoja=2, ppp=150)
    n = imp.imprimir_a_pdf(samples["a"], out2, op)
    with pymupdf.open(out2) as d:
        assert n == d.page_count == 3
        assert d[0].rect.width > d[0].rect.height                     # hoja apaisada
        assert tinta(d[0], (0, 0, 0.5, 1)) and tinta(d[0], (0.5, 0, 0.5, 1))
        assert tinta(d[2], (0, 0, 0.5, 1)) and not tinta(d[2], (0.5, 0, 0.5, 1))


def test_imprimir_a_pdf_en_grises(colores, tmp_path):
    out = tmp_path / "gris.pdf"
    imp.imprimir_a_pdf(colores, out, Op(grises=True, ppp=150))
    with pymupdf.open(out) as d:
        for page in d:
            pix = page.get_pixmap(dpi=30)
            for x, y in ((5, 5), (pix.width // 2, pix.height // 2), (pix.width - 5, pix.height - 5)):
                r, g, b = pix.pixel(x, y)
                assert r == g == b
            assert pix.pixel(pix.width // 2, pix.height // 2)[0] < 230        # no quedó en blanco


def test_imprimir_a_pdf_copias(colores, tmp_path):
    out = tmp_path / "c.pdf"
    imp.imprimir_a_pdf(colores, out, Op(copias=2, ppp=150))
    with pymupdf.open(out) as d:
        assert "".join(color_de(p) for p in d) == "RGBRGB"
    out = tmp_path / "u.pdf"
    imp.imprimir_a_pdf(colores, out, Op(copias=2, intercalar=False, ppp=150))
    with pymupdf.open(out) as d:
        assert "".join(color_de(p) for p in d) == "RRGGBB"


def test_imprimir_a_pdf_con_contrasena(samples, tmp_path):
    out = tmp_path / "s.pdf"
    assert imp.imprimir_a_pdf(samples["locked"], out, Op(ppp=150, paginas="1-2"), password="1234") == 2
    # la copia pide la misma contraseña que el original, y no quedó en blanco
    assert core.needs_password(out) and core.check_password(out, "1234") and not core.check_password(out, "x")
    with core.open_pdf(out, "1234") as d:
        assert d.page_count == 2 and not d[0].get_pixmap(dpi=20).is_unicolor
    # un PDF sin contraseña sigue saliendo sin contraseña
    libre = tmp_path / "libre.pdf"
    imp.imprimir_a_pdf(samples["a"], libre, Op(ppp=72, paginas="1"))
    assert not core.needs_password(libre)
    with pytest.raises(core.PasswordError):
        imp.imprimir_a_pdf(samples["locked"], tmp_path / "x.pdf", Op())
    assert not (tmp_path / "x.pdf").exists()


def test_imprimir_a_pdf_folleto_poster_e_imagen(samples, tmp_path):
    out = tmp_path / "f.pdf"
    assert imp.imprimir_a_pdf(samples["a"], out, Op(modo="folleto", ppp=150), papel_="A4") == 4
    with pymupdf.open(out) as d:
        assert d[0].rect.width == pytest.approx(842, abs=1)
        # cara 1: (blanco, 1) → la mitad izquierda vacía
        assert not tinta(d[0], (0, 0, 0.5, 1)) and tinta(d[0], (0.5, 0, 0.5, 1))
    out = tmp_path / "p.pdf"
    assert imp.imprimir_a_pdf(samples["a"], out, Op(modo="poster", escala=200, paginas="1", ppp=150), papel_="A4") == 4
    out = tmp_path / "j.pdf"
    assert imp.imprimir_a_pdf(samples["jpg"], out, Op(ppp=150)) == 1
    with pymupdf.open(out) as d:
        assert tinta(d[0], (0, 0, 1, 1))


def test_imprimir_doble_cara_manual_en_dos_tandas(colores, tmp_path):
    """imprimir() con caras «frente» y luego «dorso» (en un QPdfWriter: nada va a una impresora)."""
    from PySide6.QtGui import QPdfWriter
    op = Op(doble_cara="manual", ppp=150)
    salidas = {}
    for caras, inv in (("frente", False), ("dorso", False), ("dorso", True)):
        out = tmp_path / f"{caras}{inv}.pdf"
        w = QPdfWriter(str(out))
        imp.preparar_impresora(w, op, imp.papel("Carta"), "prueba")
        n = imp.imprimir(colores, w, op, caras=caras, invertir_dorso=inv)
        del w
        with pymupdf.open(out) as d:
            salidas[(caras, inv)] = "".join(color_de(p) if tinta(p, (0, 0, 1, 1)) else "-" for p in d)
        assert n == len(salidas[(caras, inv)])
    assert salidas[("frente", False)] == "RB"          # páginas 1 y 3
    assert salidas[("dorso", False)] == "G-"           # página 2 y el dorso en blanco de la hoja 2
    assert salidas[("dorso", True)] == "-G"


def test_cancelar_entre_hojas_y_errores(samples, tmp_path):
    class Cancelado(Exception):
        pass

    vistos = []

    def progreso(i, n, msg):
        vistos.append(msg)
        if i == 2:
            raise Cancelado

    out = tmp_path / "cancelado.pdf"
    with pytest.raises(Cancelado):
        imp.imprimir_a_pdf(samples["a"], out, Op(ppp=150), progress=progreso)
    assert vistos[:3] == ["Hoja 1 de 5", "Hoja 2 de 5", "Hoja 3 de 5"] and not out.exists()
    assert list(tmp_path.iterdir()) == []                            # ni archivos temporales
    # un PDF que no se puede escribir da un error claro
    from PySide6.QtGui import QPdfWriter
    w = QPdfWriter(str(tmp_path / "no" / "existe" / "x.pdf"))
    with core.open_pdf(samples["a"]) as d, pytest.raises(core.UserError, match="No se pudo escribir"):
        imp._enviar(d, w, imp.planificar(imp._tamanos(d), CARTA, Op()), Op(), None)
    assert "Revisa que la impresora esté encendida" in imp._error_impresora("HP LaserJet")


def test_render_por_franjas_igual_al_normal(samples, tmp_path, monkeypatch):
    """Las piezas muy grandes se renderizan por franjas; el resultado debe ser el mismo."""
    src = tmp_path / "foto_girada.pdf"                                # una foto en una página normal
    with pymupdf.open(samples["a"]) as d:                             # y otra con /Rotate 90
        d.select([0, 1])
        d[1].set_rotation(90)
        d.save(src)
    op = Op(ppp=150)
    normal, franjas = tmp_path / "normal.pdf", tmp_path / "franjas.pdf"
    imp.imprimir_a_pdf(src, normal, op)
    monkeypatch.setattr(imp, "MAX_PX", 40_000)
    imp.imprimir_a_pdf(src, franjas, op)
    with pymupdf.open(normal) as a, pymupdf.open(franjas) as b:
        for pa, pb in zip(a, b):
            xa, xb = pa.get_pixmap(dpi=40), pb.get_pixmap(dpi=40)
            dif = sum(abs(p - q) for p, q in zip(xa.samples, xb.samples)) / len(xa.samples)
            assert dif < 2.0
        assert len(b[0].get_images()) > 3                             # de verdad se partió en franjas


def test_pagina_girada_sale_derecha(samples, tmp_path):
    """La página 2 de anexo.pdf tiene /Rotate 90: al imprimirla se gira para llenar el papel."""
    out = tmp_path / "r.pdf"
    imp.imprimir_a_pdf(samples["b"], out, Op(paginas="2", ppp=150))
    with pymupdf.open(out) as d:
        assert d.page_count == 1 and d[0].rect.height > d[0].rect.width
        assert tinta(d[0], (0, 0, 1, 1))


def test_vista_previa(samples):
    img = imp.vista_previa(samples["a"], 0, Op(), "Carta", max_px=400)
    assert not img.isNull() and img.height() == 400 and img.width() == round(400 * 612 / 792)
    # hoja apaisada: el lado largo es el ancho
    img = imp.vista_previa(samples["a"], 1, Op(modo="varias", por_hoja=2), "Carta", max_px=500)
    assert img.width() == 500 and img.height() < 500
    # con márgenes de impresora, el borde queda blanco
    p = replace(imp.papel("Carta"), margenes=(36, 36, 36, 36))
    img = imp.vista_previa(samples["jpg"], 0, Op(), p, max_px=400)
    assert img.pixelColor(3, 200).name() == "#ffffff"
    with pytest.raises(core.PasswordError):
        imp.vista_previa(samples["locked"], 0, Op(), "Carta")


def test_papeles():
    assert imp.papel("Carta").ancho == pytest.approx(612) and imp.papel("Oficio").alto == pytest.approx(1008)
    assert imp.papel((842, 595)).ancho == 595
    assert imp.papel("A4").area == pytest.approx((595.3, 841.9), abs=0.5)
    with pytest.raises(core.UserError):
        imp.papel("Servilleta")


# ---------------------------------------------------------------- interfaz (sin pantalla)

from PySide6.QtGui import QPdfWriter  # noqa: E402

from ronypdf import imprimir_ui, viewer, widgets  # noqa: E402
from ronypdf.gui import MainWindow, _light_palette  # noqa: E402
from ronypdf.style import QSS  # noqa: E402

PRUEBA = imp.InfoImpresora("Impresora de prueba", True, True, True, ["Carta", "Oficio", "A4"], "Carta")


@pytest.fixture(scope="module")
def win():
    APP.setStyle("Fusion")
    APP.setPalette(_light_palette())
    APP.setStyleSheet(QSS)
    w = MainWindow()
    w.resize(1200, 800)
    w.show()
    APP.processEvents()
    return w


@pytest.fixture
def done(monkeypatch):
    calls = []

    def fake_done(parent, title, detail="", file=None, folder=None):
        calls.append((title, detail, file, folder))

    for mod in (imprimir_ui, widgets, viewer):
        monkeypatch.setattr(mod, "show_done", fake_done, raising=False)
    monkeypatch.setattr(widgets.QMessageBox, "warning", lambda *a, **k: calls.append(("warning", a[2])))
    monkeypatch.setattr(widgets.QMessageBox, "information", lambda *a, **k: calls.append(("info", a[2])))
    monkeypatch.setattr(widgets.QMessageBox, "question", lambda *a, **k: widgets.QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(widgets.QInputDialog, "getText", lambda *a, **k: ("1234", True))
    monkeypatch.setattr(widgets.QFileDialog, "getSaveFileName", lambda *a, **k: ("", ""))
    monkeypatch.setattr(widgets.QFileDialog, "getOpenFileName", lambda *a, **k: ("", ""))
    return calls


@pytest.fixture
def impresoras(monkeypatch):
    """Una impresora falsa: nada de lo que se imprima en estas pruebas llega a un aparato real."""
    lista = [PRUEBA]
    monkeypatch.setattr(imp, "impresoras", lambda: list(lista))
    return lista


def wait(timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        QApplication.processEvents()
        if not widgets.ctx.busy:
            QApplication.processEvents()
            return
        time.sleep(0.01)
    raise TimeoutError


def esperar(cond, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        QApplication.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def resumen(page):
    """Los chips de la barra de acción y su frase, en un solo texto."""
    chips = [page._chips.itemAt(i).widget().text() for i in range(page._chips.count())]
    return re.sub(r"<[^>]+>", "", " · ".join(chips + [page.resumen.text()]))


def pantalla(win):
    page = win.pages["print"]
    win.navigate("print")
    page._cargar_impresoras(forzar=True)
    return page


def test_registro_menu_busqueda_y_visor(win):
    assert "print" in win.pages and isinstance(win.pages["print"], imprimir_ui.PrintPage)
    sb = win.sidebar
    win.navigate("home")
    sb.set_collapsed(False)
    sb.search.setText("imprimir")
    assert sb.items["print"].isVisible() and not sb.items["merge"].isVisible()
    sb.search.clear()
    from PySide6.QtWidgets import QToolButton
    botones = [b for b in win.pages["viewer"].findChildren(QToolButton) if b.toolTip() == "Imprimir (Ctrl+P)"]
    assert len(botones) == 1


def test_pantalla_resumen_vista_previa_y_guardar_pdf(win, samples, done, impresoras, tmp_path):
    page = pantalla(win)
    page.load(str(samples["a"]))
    assert page.hojas == 5 and "5 hojas de papel" in resumen(page) and "5 páginas" in resumen(page)
    assert page.b_imprimir.isEnabled() and page.b_pdf.isEnabled()
    page.set_opciones(modo="varias", por_hoja=4)
    assert page.opciones().modo == "varias" and page.opciones().por_hoja == 4
    assert page.hojas == 2 and "2 hojas de papel" in resumen(page)
    assert page.lb_hoja.text() == "Hoja 1 de 2"
    assert esperar(lambda: page.ultima_imagen is not None)          # la vista previa llega sola
    page.ir_a_hoja(1)
    assert page.lb_hoja.text() == "Hoja 2 de 2" and not page.b_sig.isEnabled()
    # doble cara y copias en el resumen
    page.set_opciones(doble_cara="largo", copias=2)
    assert "por las dos caras" in resumen(page) and "2 copias" in resumen(page)
    assert page.lb_hoja.text().startswith("Cara ")
    page.set_opciones(doble_cara="no", copias=1, paginas="2-4")
    assert page.hojas == 1
    # un rango imposible se explica y desactiva los botones
    page.set_opciones(paginas="9")
    assert page.h_rango.isVisibleTo(page) and "no existe" in page.h_rango.text()
    assert not page.b_pdf.isEnabled() and not page.b_imprimir.isEnabled()
    page.set_opciones(paginas="", modo="folleto")                  # 5 páginas → 2 hojas, 4 caras
    assert page.hojas == 4 and "4 hojas de papel" in resumen(page)
    page.set_opciones(doble_cara="corto")
    assert "2 hojas por las dos caras (4 caras)" in resumen(page)
    out = tmp_path / "listo.pdf"
    page.guardar_pdf(str(out))
    wait()
    assert done[-1][0] == "PDF listo para imprimir", done
    with pymupdf.open(out) as d:
        assert d.page_count == page.hojas == 4
    page.set_opciones(doble_cara="no")
    page.set_opciones(modo="ajustar")


def test_sin_impresoras(win, samples, done, monkeypatch, tmp_path):
    monkeypatch.setattr(imp, "impresoras", lambda: [])
    page = pantalla(win)
    page.load(str(samples["b"]))
    assert page.aviso_sin_impresoras.isVisibleTo(page)
    assert "No se encontró ninguna impresora" in page.aviso_sin_impresoras.texto.text()
    assert not page.b_imprimir.isEnabled() and page.b_pdf.isEnabled()
    assert not page.c_impresora.isEnabled()
    out = tmp_path / "sin.pdf"
    page.guardar_pdf(str(out))
    wait()
    with pymupdf.open(out) as d:
        assert d.page_count == page.hojas == 3


def test_ajustes_se_recuerdan(win, samples, done, impresoras):
    page = pantalla(win)
    page.load(str(samples["a"]))
    page.c_papel.setCurrentIndex(page.c_papel.findData("A4"))
    page.set_opciones(grises=True, ppp=600, doble_cara="corto")
    guardado = widgets.ctx.settings.get("imprimir")
    assert guardado["impresora"] == "Impresora de prueba" and guardado["papel"] == "A4"
    assert guardado["grises"] is True and guardado["ppp"] == 600 and guardado["doble_cara"] == "corto"
    otra = imprimir_ui.PrintPage()
    otra._cargar_impresoras()
    op = otra.opciones()
    assert otra.c_papel.currentData() == "A4" and op.grises and op.ppp == 600 and op.doble_cara == "corto"
    otra.deleteLater()
    page.set_opciones(grises=False, ppp=300, doble_cara="no")
    page.c_papel.setCurrentIndex(page.c_papel.findData("Carta"))


def test_doble_cara_manual_desde_la_pantalla(win, colores, done, impresoras, monkeypatch, tmp_path):
    page = pantalla(win)
    page.load(str(colores))
    page.set_opciones(doble_cara="manual", ppp=150)
    salidas, preguntas = [], []

    def falsa(nombre, op, papel, titulo):                 # un PDF en lugar de la impresora
        ruta = tmp_path / f"tanda{len(salidas)}.pdf"
        w = QPdfWriter(str(ruta))
        imp.preparar_impresora(w, op, papel, titulo)
        salidas.append(ruta)
        return w

    def pregunta():
        preguntas.append(len(salidas))
        return True, True                                   # continuar e invertir el orden del dorso

    monkeypatch.setattr(page, "_crear_impresora", falsa)
    monkeypatch.setattr(page, "_preguntar_dorso", pregunta)
    page._imprimir()
    wait()
    assert preguntas == [1] and len(salidas) == 2
    resultado = []
    for s in salidas:
        with pymupdf.open(s) as d:
            resultado.append("".join(color_de(p) if tinta(p, (0, 0, 1, 1)) else "-" for p in d))
    assert resultado == ["RB", "-G"]
    assert "Se enviaron 2 hojas a Impresora de prueba" in win.statusBar().currentMessage()
    page.set_opciones(doble_cara="no", ppp=300)


def test_contrasena_en_la_pantalla(win, samples, done, impresoras, tmp_path):
    page = pantalla(win)
    page.load(str(samples["locked"]))
    assert page.hojas == 5
    assert esperar(lambda: page.ultima_imagen is not None)
    out = tmp_path / "secreto_listo.pdf"
    page.set_opciones(paginas="1", ppp=150)
    page.guardar_pdf(str(out))
    wait()
    with pymupdf.open(out) as d:
        assert d.page_count == 1
    page.set_opciones(paginas="", ppp=300)


def test_imprimir_desde_el_visor_con_cambios_sin_guardar(win, samples, done, impresoras, tmp_path):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QShortcut
    v = win.pages["viewer"]
    win.navigate("viewer")
    src = tmp_path / "doc.pdf"
    src.write_bytes(samples["a"].read_bytes())
    assert v.load(str(src), confirm=False)
    c = v.canvas
    c.add_text(c.pages[1].pos() + QPointF(72, 600), "Texto para imprimir")
    c.go_to_page(1)
    assert v.has_unsaved()
    imprimir_ui.imprimir_desde_visor(v)
    wait()
    page = win.pages["print"]
    assert win.stack.currentWidget() is page
    temporal = page.picker.path
    assert temporal != str(src) and "RonyPDF-imprimir" in temporal and os.path.basename(temporal) == "doc.pdf"
    assert page.aviso_cambios.isVisibleTo(page) and page._hoja == 1
    with pymupdf.open(temporal) as d:
        assert "Texto para imprimir" in d[1].get_text()
    assert not any("RonyPDF-imprimir" in r["path"] for r in widgets.ctx.settings.recent())
    # al cambiar de archivo, la copia temporal se borra
    page.load(str(samples["b"]))
    assert not os.path.exists(temporal) and not page.aviso_cambios.isVisibleTo(page)
    # sin cambios pendientes, Ctrl+P lleva el original y la página que se estaba viendo
    win.navigate("viewer")
    assert v.load(str(src), confirm=False) and not v.has_unsaved()
    c.go_to_page(3)
    atajo = [s for s in v.findChildren(QShortcut) if s.key().toString() == "Ctrl+P"]
    assert len(atajo) == 1
    atajo[0].activated.emit()
    QApplication.processEvents()
    assert win.stack.currentWidget() is page and page.picker.path == str(src)
    assert page._hoja == 3 and not page.aviso_cambios.isVisibleTo(page)


def test_temporales_se_borran_al_salir(tmp_path):
    ruta = imprimir_ui.nueva_temporal(str(tmp_path / "x.pdf"))
    open(ruta, "wb").close()
    imprimir_ui.borrar_temporales()
    assert not os.path.exists(ruta) and not imprimir_ui._carpeta_proceso().exists()
