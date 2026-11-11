"""Rendimiento del arranque: pantallas que se construyen al abrirlas, sin ventanas nativas sueltas y sin armar el
diccionario del OCR al abrir la aplicación."""

import os
import tempfile

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["RONYPDF_SETTINGS"] = os.path.join(tempfile.mkdtemp(prefix="ronypdf-"), "RonyPDF.json")

from PySide6.QtCore import QEvent, QObject  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QWidget  # noqa: E402

from ronypdf import componentes, ocr, widgets  # noqa: E402
from ronypdf.gui import MainWindow, _light_palette  # noqa: E402
from ronypdf.pantallas import Pantallas  # noqa: E402
from ronypdf.style import QSS  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    a.setStyle("Fusion")
    a.setPalette(_light_palette())
    a.setStyleSheet(QSS)
    return a


class _Ventanas(QObject):
    """Cuenta las ventanas nativas que se crean (evento PlatformSurface de cada widget)."""

    def __init__(self):
        super().__init__()
        self.creadas: list[str] = []

    def eventFilter(self, obj, ev):  # noqa: N802
        if ev.type() == QEvent.Type.PlatformSurface and isinstance(obj, QWidget) and ev.surfaceEventType() \
                == ev.SurfaceEventType.SurfaceCreated:
            self.creadas.append(f"{type(obj).__name__} {obj.objectName()}")
        return False


@pytest.fixture(scope="module")
def win(app):
    vigia = _Ventanas()
    app.installEventFilter(vigia)
    try:
        w = MainWindow()
    finally:
        app.removeEventFilter(vigia)
    w.ventanas_al_construir = vigia.creadas
    w.resize(1240, 820)
    w.show()
    app.processEvents()
    if getattr(w, "_autoguardado", None) is not None:
        w._autoguardado.timer.stop()
    yield w
    if getattr(w, "_autoguardado", None) is not None:
        w._autoguardado.timer.stop()
    w.hide()


# ---------------------------------------------------------------- Pantallas

def test_pantallas_construye_al_pedirla_y_get_no_construye():
    hechas, avisos = [], []

    def fabrica(clave):
        hechas.append(clave)
        return f"pantalla {clave}"

    p = Pantallas(["a", "b", "c"], fabrica, lambda k, w: avisos.append((k, w)))
    assert len(p) == 3 and list(p) == ["a", "b", "c"] and "b" in p and "z" not in p
    assert p.get("b") is None and not p.construida("b") and hechas == []
    assert p["b"] == "pantalla b" and p["b"] == "pantalla b"          # una sola vez
    assert hechas == ["b"] and avisos == [("b", "pantalla b")]
    assert p.get("b") == "pantalla b" and p.construidas() == {"b": "pantalla b"}
    with pytest.raises(KeyError):
        p["z"]
    p.registrar("inicio", "pantalla inicio")
    assert "inicio" in p and p.get("inicio") == "pantalla inicio" and avisos[-1] == ("inicio", "pantalla inicio")
    assert hechas == ["b"]


def test_pantallas_no_se_pide_a_si_misma():
    p = None

    def fabrica(clave):
        return p[clave]

    p = Pantallas(["a"], fabrica, lambda k, w: None)
    with pytest.raises(RuntimeError):
        p["a"]
    assert p.get("a") is None


# ---------------------------------------------------------------- ventana principal

def test_al_abrir_solo_se_construye_el_inicio(win):
    assert list(win.pages.construidas()) == ["home"]
    assert win.stack.count() == 1 and win.stack.currentWidget() is win.home
    assert "viewer" in win.pages and len(win.pages) >= 27
    assert win.pages.get("viewer") is None and win.pages.get("print") is None
    # el buscador Ctrl K ve todas las herramientas, construidas o no
    claves = [k for k, *_ in win._herramientas_paleta()]
    assert "viewer" in claves and "print" in claves and "backup" in claves


def test_sin_ventanas_nativas_sueltas_al_construir(win):
    """Un widget sin padre que se hace visible crea una ventana nativa (lenta y puede parpadear en Windows)."""
    assert win.ventanas_al_construir == []


def test_abrir_una_pantalla_la_construye_y_avisa(win):
    avisos = []
    win.pagina_creada.connect(lambda k, p: avisos.append(k))
    assert win.pages.get("merge") is None
    win.navigate("merge")
    QApplication.processEvents()
    page = win.pages.get("merge")
    assert page is not None and win.stack.currentWidget() is page and page.isVisible()
    assert avisos == ["merge"]
    win.navigate("home")
    win.navigate("merge")
    assert avisos == ["merge"] and win.pages["merge"] is page          # no se vuelve a construir
    win.navigate("home")


def test_el_autoguardado_vigila_el_visor_aunque_se_construya_despues(win):
    assert win.pages.get("viewer") is None
    v = win.pages["viewer"]
    assert v.canvas.viewport().property("autoguardado_herr") == "viewer"
    o = win.pages["organize"]
    assert o.grid.viewport().property("autoguardado_herr") == "organize"


def test_preguntar_por_cambios_no_construye_pantallas(win):
    """Al cerrar (y en el autoguardado) se pregunta con ``pages.get``: solo mira las pantallas ya construidas."""
    from ronypdf import extensiones, guardado_ui
    antes = set(win.pages.construidas())
    for clave in ["viewer", "organize"] + [r["clave"] for r in extensiones.registros()]:
        page = win.pages.get(clave)
        assert page is None or clave in antes
    assert extensiones.pendientes(win.pages) == []
    assert guardado_ui.abierto_en(os.path.join(tempfile.gettempdir(), "nada.pdf")) is None
    win._autoguardado.estado("forms")
    assert set(win.pages.construidas()) == antes


def test_imprimir_desde_el_visor_construye_imprimir(win, tmp_path, monkeypatch):
    import pymupdf
    from ronypdf import imprimir_ui
    pdf = tmp_path / "corto.pdf"
    with pymupdf.open() as d:
        d.new_page().insert_text((72, 72), "Hola")
        d.save(pdf)
    v = win.pages["viewer"]
    win.navigate("viewer")
    v.load(str(pdf))
    QApplication.processEvents()
    pedidas = []
    monkeypatch.setattr(imprimir_ui.extensiones, "ir_a", lambda clave, rutas=None: pedidas.append((clave, rutas)))
    imprimir_ui.imprimir_desde_visor(v)
    assert win.pages.get("print") is not None and pedidas == [("print", [str(pdf)])]
    win.navigate("home")


# ---------------------------------------------------------------- componentes

def test_componentes_con_ayuda_igual_que_antes(app):
    con = componentes.OptionCard("Título", "Una ayuda")
    sin = componentes.OptionCard("Título")
    assert not con.help_lb.isHidden() and sin.help_lb.isHidden()
    fila = componentes.SwitchRow("Texto", "Ayuda")
    assert not fila.help_lb.isHidden() and componentes.SwitchRow("Texto").help_lb.isHidden()
    paso = componentes.Step("Paso", "Ayuda del paso")
    assert not paso.help_lb.isHidden() and componentes.Step("Paso").help_lb.isHidden()
    ficha = componentes.FileChip("a.pdf", removable=True)
    assert not ficha.b_remove.isHidden() and componentes.FileChip("a.pdf").b_remove.isHidden()
    for w in (con.help_lb, fila.help_lb, paso.help_lb, ficha.b_remove):
        assert not w.isWindow()


def test_componentes_no_crean_ventanas_nativas(app):
    vigia = _Ventanas()
    app.installEventFilter(vigia)
    try:
        hechos = [componentes.OptionCard("Título", "Ayuda"), componentes.SwitchRow("Texto", "Ayuda"),
                  componentes.Step("Paso", "Ayuda"), componentes.FileChip("a.pdf", removable=True)]

        class Pagina(widgets.ToolPage):
            key = "merge"
            title = "Prueba"
            subtitle = "Subtítulo"

            def build(self):
                self.body.addWidget(QLabel("cuerpo"))

        hechos.append(Pagina())
    finally:
        app.removeEventFilter(vigia)
    assert vigia.creadas == [] and len(hechos) == 5


# ---------------------------------------------------------------- diccionario del OCR

def test_el_diccionario_del_ocr_se_pide_al_entrar_y_una_sola_vez(app, monkeypatch):
    from ronypdf import tools
    hilos = []

    class Hilo:
        def __init__(self, target=None, args=(), **_k):
            hilos.append((target, args))

        def start(self):
            pass

    monkeypatch.setattr(ocr.threading, "Thread", Hilo)
    monkeypatch.setattr(ocr, "_precargados", set())
    page = tools.OcrPage()
    assert hilos == []                       # construirla no arma el diccionario
    page.show()
    QApplication.processEvents()
    assert len(hilos) == 1 and hilos[0][0] is ocr._precargar
    page.hide()
    page.show()
    ocr.precargar()
    assert len(hilos) == 1                   # un solo hilo por idioma
    page.hide()


def test_la_precarga_del_diccionario_cede_el_gil(monkeypatch):
    """Armarlo en otro hilo sin pausas dejaba la interfaz esperando el GIL (abrir el OCR tardaba 1 s más)."""
    import threading
    pausas = []
    monkeypatch.setattr(ocr, "_ceder", lambda: pausas.append(1))
    ocr.diccionario.cache_clear()
    hilo = threading.Thread(target=ocr._precargar, args=("spa",))
    hilo.start()
    hilo.join(60)
    assert len(pausas) > 50 and "niñez" in ocr.diccionario("spa")
    pausas.clear()
    ocr.diccionario.cache_clear()
    assert "niñez" in ocr.diccionario("spa") and pausas == []          # fuera de la precarga, sin pausas


# ---------------------------------------------------------------- memoria: miniaturas y revisión del OCR

def _pdf_largo(ruta, paginas: int, ancho: float = 300, alto: float = 420):
    import pymupdf
    with pymupdf.open() as d:
        for i in range(paginas):
            d.new_page(width=ancho, height=alto).insert_text((30, 60), f"Página {i + 1}", fontsize=20)
        d.save(ruta)
    return str(ruta)


def _hasta_quieto(timer, seg: float = 30.0):
    import time
    fin = time.time() + seg
    while time.time() < fin:
        QApplication.processEvents()
        if not timer.isActive() and not widgets.ctx.busy:
            QApplication.processEvents()
            if not timer.isActive():
                return
        time.sleep(0.002)
    raise AssertionError("las miniaturas no terminaron")


def test_cache_de_imagenes_con_presupuesto_en_bytes(app):
    from PySide6.QtGui import QImage
    from ronypdf import memoria

    def img():
        return QImage(100, 100, QImage.Format.Format_RGB888)      # 30 000 bytes (con relleno de renglón)

    n = img().sizeInBytes()
    c = memoria.CacheImagenes(presupuesto=3 * n)
    for k in range(3):
        c[k] = img()
    assert len(c) == 3 and c.bytes == 3 * n
    assert c.get(0) is not None                  # 0 se usó hace poco: la próxima en salir es 1
    c[3] = img()
    assert 1 not in c and {0, 2, 3} <= set(c) and c.bytes == 3 * n
    c["rota"] = None                             # «no se pudo dibujar» no ocupa nada
    assert "rota" in c and c.get("rota", "x") is None
    c["grande"] = QImage(400, 400, QImage.Format.Format_RGB888)   # más grande que todo el presupuesto
    assert list(c) == ["grande"] and c.bytes == c.get("grande").sizeInBytes()
    del c["grande"]
    assert len(c) == 0 and c.bytes == 0
    t = memoria.CacheImagenes(presupuesto=10 * n, maximo=2)
    t["a"], t["b"], t["c"] = (img(), 1.0), (img(), 2.0), (img(), 3.0)
    assert list(t) == ["b", "c"] and t.bytes == 2 * n


def test_ventana_y_tope_de_pixeles():
    from ronypdf import memoria
    assert memoria.ventana(10, 12, 100, 2) == [10, 11, 12, 13, 9, 14, 8]
    assert memoria.ventana(0, 3, 5, 3) == [0, 1, 2, 3, 4]
    assert memoria.ventana(0, -1, 0, 3) == []
    z = memoria.zoom_tope(595, 842, 6.0)                       # A4 a 6×: 18 MP → hasta 8 MP
    assert 595 * 842 * z * z <= memoria.MAX_PIXELES * 1.0001 and z < 6.0
    assert memoria.zoom_tope(595, 842, 1.5) == 1.5


def test_miniaturas_del_visor_solo_las_que_se_ven(win, tmp_path):
    pdf = _pdf_largo(tmp_path / "largo.pdf", 150)
    v = win.pages["viewer"]
    win.navigate("viewer")
    v.load(pdf)
    strip = v.thumbs
    _hasta_quieto(strip.timer)
    assert strip.count() == 150 and strip.isVisible()
    assert 0 in strip.images and 0 < len(strip.images) < 60          # no las 150
    sb = strip.verticalScrollBar()
    sb.setValue(sb.maximum())
    _hasta_quieto(strip.timer)
    assert 149 in strip.images
    # sin verse no se dibuja: se marcan todas para volver a dibujar y se va a otra pantalla
    strip.invalidar()
    win.navigate("home")
    _hasta_quieto(strip.timer)
    assert 149 in strip.pending and len(strip.pending) == 150
    win.navigate("viewer")
    _hasta_quieto(strip.timer)
    assert 149 not in strip.pending and 0 in strip.pending            # solo las visibles (y cercanas)
    win.navigate("home")


def test_miniaturas_del_organizador_solo_las_que_se_ven(win, tmp_path):
    pdf = _pdf_largo(tmp_path / "largo_org.pdf", 300)
    o = win.pages["organize"]
    win.navigate("organize")
    o.load_files([pdf])
    _hasta_quieto(o.timer)
    assert o.grid.count() == 300
    hechas = [k for k in o.thumbs if len(k) == 2]
    assert (0, 0) in o.thumbs and 0 < len(hechas) < 200
    sb = o.grid.verticalScrollBar()
    sb.setValue(sb.maximum())
    _hasta_quieto(o.timer)
    assert (0, 299) in o.thumbs
    win.navigate("home")
    o.reset()


def test_revision_ocr_con_tope_de_pixeles(app, tmp_path, monkeypatch):
    """Una página muy larga (un recibo escaneado) con zoom alto pedía cientos de MB: ahora hasta 8 MP."""
    import pymupdf
    from PySide6.QtWidgets import QGraphicsPixmapItem
    from ronypdf import memoria, ocr_ui
    monkeypatch.setattr(ocr, "precargar", lambda *a, **k: None)
    pdf = tmp_path / "recibo.pdf"
    with pymupdf.open() as d:
        d.new_page(width=595, height=14400).insert_text((40, 80), "Recibo", fontsize=30)
        d.save(pdf)
    page = ocr_ui.RevisarOcrPage()
    page.resize(1240, 820)
    page.show()
    QApplication.processEvents()
    page.doc = pymupdf.open(pdf)
    page._tamanos = [(595.0, 14400.0)]
    page.pagina_actual = 0
    page.zoom = 6.0
    page._render_hecho = None
    page._render()
    pm = [it for it in page.vista.scene().items() if isinstance(it, QGraphicsPixmapItem)][0].pixmap()
    assert 7_000_000 < pm.width() * pm.height() <= memoria.MAX_PIXELES * 1.01
    page.doc.close()
    page.doc = None
    page.hide()
