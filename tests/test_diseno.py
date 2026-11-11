"""Rediseño 2.0: tokens, catálogo y sinónimos, componentes, plantilla de herramienta, inicio, carril y paleta."""

import os
import tempfile

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["RONYPDF_SETTINGS"] = os.path.join(tempfile.mkdtemp(prefix="ronypdf-"), "RonyPDF.json")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ronypdf import art, catalogo, componentes, tema  # noqa: E402
from ronypdf.gui import SECTIONS, MainWindow, _light_palette  # noqa: E402
from ronypdf.style import QSS, build_qss  # noqa: E402


@pytest.fixture(scope="module")
def win():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setPalette(_light_palette())
    app.setStyleSheet(QSS)
    w = MainWindow()
    w.sidebar.set_collapsed(True, remember=False)       # el carril, como viene por defecto
    w.resize(1240, 820)
    w.show()
    app.processEvents()
    return w


def _todas():
    return [(k, t, sec) for sec, _l, _a, items in SECTIONS for k, t, _x in items]


# ---------------------------------------------------------------- tokens y catálogo

def test_tokens_y_categorias():
    assert tema.color("brand") == "#d7373a" and tema.OSCURO["canvas"] == "#0e1016"
    assert set(tema.CLARO) == set(tema.OSCURO)
    for k, _t, sec in _todas():
        assert tema.categoria_de(k) == sec
    assert tema.categoria_de("home") == "marca" and tema.categoria_de("redact") == "seguridad"
    assert tema.tonos("convertir") == {"c": "#129c80", "hi": "#36b89b", "band": "#ddf4ee", "ink": "#0b7560"}
    assert tema.tonos("marca")["c"] == tema.color("brand")
    assert tema.mezclar("#000000", "#ffffff", 0.5) == "#808080"
    assert "Space Grotesk" in build_qss(tema.CLARO) and "#ToolPanel" in QSS


def test_fuentes_registradas(win):
    familias = tema.cargar_fuentes()
    if (tema.fonts_dir() / "SpaceGrotesk-SemiBold.ttf").exists():
        assert "Space Grotesk" in familias and tema.familia_display() == "Space Grotesk"
    assert tema.fuente("title-1").pixelSize() == 26


def test_catalogo_completo_y_sinonimos():
    for k, _t, _s in _todas():
        assert k in catalogo.TARJETAS and catalogo.SINONIMOS.get(k), k
    herr = _todas()
    assert catalogo.buscar("junt", herr)[0]["clave"] == "merge"
    r = catalogo.buscar("junt", herr)[0]
    assert (r["pre"], r["match"], r["post"]) == ("También: ", "junt", "ar")
    assert catalogo.buscar("achicar", herr)[0]["clave"] == "compress"
    assert catalogo.buscar("CEDULA", herr)[0]["clave"] == "redact"             # sin tildes ni mayúsculas
    assert catalogo.buscar("tapar cédulas", herr)[0]["clave"] == "redact"
    assert {"protect", "unlock"} <= {d["clave"] for d in catalogo.buscar("clave", herr)}
    assert catalogo.buscar("comprimir", herr)[0]["pre"] == "En Optimizar"     # coincide con el título
    assert catalogo.buscar("zzzz", herr) == []


def test_azulejo_blanco_y_suave(win):
    pm = art.glyph_tile("merge", 40, "organizar", dpr=2.0)
    assert pm.devicePixelRatio() == 2.0 and pm.width() == 80
    img = pm.toImage()
    abajo, arriba = img.pixelColor(40, 78), img.pixelColor(40, 3)   # degradado cat-organizar-hi → cat-organizar
    assert abs(abajo.red() - 0x2e) < 6 and abs(abajo.blue() - 0xd8) < 6
    assert abs(arriba.red() - 0x5b) < 12 and arriba.red() > abajo.red()
    blancos = sum(1 for x in range(16, 64) for y in range(16, 64)
                  if img.pixelColor(x, y).red() > 245 and img.pixelColor(x, y).blue() > 245)
    assert blancos > 40                                              # el glifo va en blanco
    suave = art.glyph_tile("merge", 40, "organizar", soft=True, dpr=1.0).toImage()
    assert suave.pixelColor(3, 20) == QColor("#e2f0fb")              # banda pastel
    assert not art.glyph_tile("forms", 22, "ver").isNull()           # glifo de una extensión


# ---------------------------------------------------------------- componentes

def test_componentes_basicos(win):
    sw = componentes.SwitchRow("Pasar a escala de grises", "Ayuda")
    assert not sw.isChecked()
    sw.switch.click()
    assert sw.isChecked()
    seg = componentes.Segmented(["Baja", ("Recomendada", "media"), "Alta"], current=1, full=True)
    vistos = []
    seg.changed.connect(lambda i, v: vistos.append((i, v)))
    assert seg.value() == "media"
    seg.button(2).click()
    assert seg.index() == 2 and vistos == [(2, "Alta")]
    ch = componentes.Choices()
    a = ch.add("Cédulas", True)
    ch.add("Placas", value="placa")
    ch.chips[1].click()
    assert ch.values() == ["Cédulas", "placa"] and a.isChecked()
    og = componentes.OptionGroup()
    og.add("Para correo", "Unos 2 MB", "media")
    og.add("Para imprimir", None, "baja")
    assert og.value() == "media"
    og.cards[1].click()
    assert og.value() == "baja" and not og.cards[0].isChecked()
    assert componentes.Chip("Listo", "ok").sizeHint().height() == 26
    sl = componentes.StepList()
    s1, s2, s3 = sl.add_step("Uno"), sl.add_step("Dos"), sl.add_step("Tres")
    assert [s.number for s in (s1, s2, s3)] == [1, 2, 3] and s3.last
    s2.hide()
    assert s3.number == 2


# ---------------------------------------------------------------- plantilla de herramienta

def test_plantilla_comprimir_y_paginas_heredadas(win, samples):
    page = win.pages["compress"]
    win.navigate("compress")
    QApplication.processEvents()
    assert page.property("cat") == "optimizar" and page.main_frame is not None
    assert [s.number for s in page.steps.steps] == [1, 2]
    assert page.main_stack.currentIndex() == 0 and not page.b_add.isVisible()      # vacía: zona para soltar
    assert not page.action.isEnabled()
    page.list.add_paths([str(samples["a"])])
    QApplication.processEvents()
    assert page.main_stack.currentIndex() == 1 and page.b_add.isVisible() and page.action.isEnabled()
    assert page._chips.count() == 2 and "Eliges dónde guardar" in page.footer_info.text()
    assert page.levels.value() == "media" and not page.gray.isChecked()
    assert page.panel.width() == tema.PANEL_ANCHO
    win.resize(1000, 700)
    QApplication.processEvents()
    assert page.panel.width() == tema.PANEL_ANCHO_ESTRECHO
    assert page.minimumSizeHint().width() <= page.width()
    win.resize(1240, 820)
    page.list.clear_all()
    # una página sin migrar queda en una columna, con los pasos numerados y sin el «1.» del título
    # («Acciones por lotes» ya está migrada: se prueba con una página heredada mínima)
    from ronypdf.widgets import ToolPage

    class Heredada(ToolPage):
        key = "batch"
        title = "Heredada"

        def build(self):
            self.group("1. Archivos")

        def run(self):
            pass

    vieja = Heredada()
    assert vieja.main_frame is None and vieja.steps.steps[0].title_lb.text() == "Archivos"
    vieja.deleteLater()
    assert win.pages["merge"].main_frame is not None          # Unir ya está migrada (fase 2)


def test_plantilla_api_nueva(win, samples):
    from ronypdf.widgets import PreviewPane, SingleFilePicker, ToolPage

    class Prueba(ToolPage):
        key = "protect"
        title = "Proteger"

        def build(self):
            self.picker = SingleFilePicker()
            self.use_picker(self.picker)
            self.paso = self.step("¿Qué contraseña?", "Una línea de ayuda")
            self.prev = PreviewPane(lambda: None)
            self.set_main(self.prev)
            self.b2 = self.add_secondary("Vista previa")

        def ready(self):
            return bool(self.picker.path)

    p = Prueba()
    p.resize(1100, 700)
    p.show()
    QApplication.processEvents()
    assert p.cat == "seguridad" and p._main_stack.currentWidget() is p.picker.drop_zone()
    assert not p.picker.file_chip().isVisible()
    p.picker.set_path(str(samples["a"]))
    p.refresh()
    QApplication.processEvents()
    assert p._main_stack.currentWidget() is p.prev and p.picker.file_chip().isVisible()
    assert p.picker.file_chip().name() == samples["a"].name and "páginas" in p.picker.file_chip().meta_lb.text()
    assert p.action.isEnabled()
    lay = p.actionbar.layout()
    assert lay.indexOf(p.b2) == lay.indexOf(p.action) - 1
    p.set_summary(["3 archivos", ("Listo", "ok")], "Frase")
    assert p._chips.count() == 2 and p.footer_info.text() == "Frase"
    p.paso.parentWidget().hide()
    assert p.steps.steps[0].isHidden()
    p.close()


# ---------------------------------------------------------------- inicio, barra superior, carril y paleta

def test_inicio_filtro_y_barra_de_comandos(win):
    win.sidebar.set_collapsed(True, remember=False)      # carril: a 1240 px caben 5 columnas
    win.navigate("home")
    home = win.home
    QApplication.processEvents()
    assert len(home.tiles) == len(_todas())
    assert home.tiles["redact"].titulo == "Censurar datos" and home.tiles["redact"].cat == "seguridad"
    home.filtrar("seguridad")
    QApplication.processEvents()
    assert home.tiles["protect"].isVisible() and not home.tiles["merge"].isVisible()
    home.filtrar("todas")
    QApplication.processEvents()
    assert home._cols == 5
    # escribir en la barra de comandos abre la paleta con ese texto
    QTest.keyClicks(home.command.entrada, "c")
    QApplication.processEvents()
    assert win.paleta.isVisible() and win.paleta.entrada.text() == "c" and home.command.entrada.text() == ""
    win.paleta.cerrar()
    assert not win.paleta.isVisible()


def test_barra_superior_y_carril(win):
    win.navigate("redact")
    assert win.topbar.isVisible() and win.topbar.migas.partes == ["Seguridad", "Censurar datos"]
    assert win.sidebar.rail_buttons["sec:seguridad"].isChecked()
    win.navigate("viewer")
    assert not win.topbar.isVisible()                    # el visor tiene su propia barra
    win.navigate("home")
    assert win.topbar.migas.partes == [] and win.sidebar.rail_buttons["home"].isChecked()
    win.sidebar.set_count("backup", 1)
    assert win.sidebar.items["backup"].count == 1
    win.sidebar.set_count("backup", None)


def test_menu_se_contrae_en_ventana_angosta(win):
    sb = win.sidebar
    win.resize(1240, 820)
    sb.set_collapsed(False, remember=False)
    win.resize(1000, 700)                                # al angostar: carril
    assert sb.is_collapsed()
    sb.set_collapsed(False, remember=False)              # pero se puede abrir a mano
    assert not sb.is_collapsed()
    sb.set_collapsed(True, remember=False)
    win.resize(1240, 820)


def test_paleta_teclado(win):
    win.navigate("home")
    win.abrir_paleta("achicar")
    pal = win.paleta
    QApplication.processEvents()
    filas = pal.lista.filas
    activa = filas[pal.lista.activa]
    assert activa["clave"] == "compress" and activa["match"] == "achicar"
    QTest.keyClick(pal.entrada, Qt.Key.Key_Return)
    assert not pal.isVisible() and win.stack.currentWidget() is win.pages["compress"]
    win.abrir_paleta("")                                   # sin texto: sugeridas
    assert pal.lista.filas[0]["texto"] == "Sugeridas"
    QTest.keyClick(pal.entrada, Qt.Key.Key_Down)
    assert pal.lista.filas[pal.lista.activa]["clave"] == catalogo.SUGERIDAS[1]
    QTest.keyClick(pal.entrada, Qt.Key.Key_Escape)
    assert not pal.isVisible()
    win.abrir_paleta("qwxz")
    assert pal.lista.filas[0]["tipo"] == "vacio" and "qwxz" in pal.lista.filas[0]["texto"]
    pal.cerrar()
