"""Blindajes de la ronda de estabilidad: cuadro de progreso sin reentrada (S2), temporizadores y referencias a
objetos ya destruidos (S4, S9, S10), cierre sin reentrada (S5) y autoguardado que no se borra a medias (S6)."""

import os
import tempfile
import threading
import time
import types

import shiboken6

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["RONYPDF_SETTINGS"] = os.path.join(tempfile.mkdtemp(prefix="ronypdf-estab-"), "RonyPDF.json")

import pytest  # noqa: E402
from PySide6.QtCore import QEvent, QRectF  # noqa: E402
from PySide6.QtGui import QCloseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _procesar(segundos: float = 0.0):
    fin = time.monotonic() + segundos
    while True:
        QApplication.processEvents()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        if time.monotonic() >= fin:
            return
        time.sleep(0.01)


def _ventana_simple():
    from ronypdf.widgets import ctx
    padre = QMainWindow()
    padre.setCentralWidget(QWidget())
    padre.show()
    ctx.window = padre
    return padre


def _hasta_libre():
    from ronypdf.widgets import ctx
    for _ in range(500):
        _procesar(0.01)
        if not ctx.busy:
            return


# ---------------------------------------------------------------- S2: cuadro de progreso propio

def test_dialogo_progreso_sin_reentrada_y_fin_idempotente(app):
    from ronypdf.widgets import DialogoProgreso
    d = DialogoProgreso("Uniendo PDF…")
    cancelados = []
    d.canceled.connect(lambda: cancelados.append(1))
    d.empezar()
    d.avanzar(1, 4, "Página 1 de 4")
    assert (d.barra.minimum(), d.barra.maximum(), d.barra.value()) == (0, 4, 1)
    assert d.etiqueta.text() == "Uniendo PDF…\nPágina 1 de 4"
    d.avanzar(9, 4, "")                                    # nunca pasa del máximo
    assert d.barra.value() == 4
    d.reject()                                             # Esc = «Cancelar»; el cuadro sigue
    d.boton.click()
    assert len(cancelados) == 2 and d.etiqueta.text() == "Cancelando…"
    assert not d.isVisible()                               # antes de los 400 ms no aparece
    d.terminar()
    d.terminar()                                           # idempotente
    d.avanzar(2, 4, "tarde")                               # un aviso que llega después: se descarta
    assert d.barra.value() == 4 and d.etiqueta.text() == "Cancelando…"
    assert not d._aparecer.isActive()                      # terminó antes de los 400 ms: ya no aparecerá
    _procesar(0.5)
    assert not shiboken6.isValid(d)                        # y se destruyó


def test_dialogo_progreso_se_deja_cerrar_y_no_aparece_sobre_una_ventana_cerrada(app):
    from ronypdf.widgets import DialogoProgreso
    padre = QWidget()
    padre.show()
    d = DialogoProgreso("Probando…", padre)
    cancelados = []
    d.canceled.connect(lambda: cancelados.append(1))
    d.empezar()
    _procesar(0.5)
    assert d.isVisible()
    # Al salir, Qt cierra primero la ventana modal: si se negara, la salida se anularía (RonyPDF colgado).
    assert d.close() is True and cancelados == [1] and not d.isVisible()
    _procesar(0.5)
    assert not d.isVisible()                               # el temporizador no lo vuelve a mostrar
    d.terminar()
    otro = DialogoProgreso("Probando…", padre)
    otro.empezar()
    padre.hide()                                           # la ventana se cerró antes de los 400 ms
    _procesar(0.5)
    assert not otro.isVisible()
    otro.terminar()
    padre.close()


HIJO_SALIR = r'''
import os, sys, time
sys.path.insert(0, {raiz!r})
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox
from ronypdf import gui, guardado_ui, widgets
guardado_ui._preguntar = lambda *a, **k: ("salir", False)          # «Salir de todas formas»
guardado_ui.ofrecer_recuperacion = lambda *a, **k: None
for f in ("information", "warning", "critical", "question"):
    setattr(QMessageBox, f, staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))

def lenta(progress=None):
    progress(0, 0, "")
    sum(range(60_000_000))        # sin soltar el GIL, como MuPDF al dibujar: la interfaz se atrasa
    progress(1, 1, "")

_init = gui.MainWindow.__init__
def init(self, *a, **k):
    _init(self, *a, **k)
    def lanzar():
        widgets.ctx.run_task("Probando…", lenta, lambda r: print("TERMINÓ", flush=True))
        QTimer.singleShot({demora}, self.close)
    QTimer.singleShot(300, lanzar)
gui.MainWindow.__init__ = init
print("SALIÓ", gui.main(["run.py"]), flush=True)
'''


@pytest.mark.parametrize("demora", [150, 700])     # antes de que aparezca el cuadro (400 ms) y con él a la vista
def test_salir_con_una_tarea_en_curso_no_deja_la_app_colgada(tmp_path, demora):
    import subprocess
    import sys
    from pathlib import Path
    script = tmp_path / "hijo.py"
    script.write_text(HIJO_SALIR.format(raiz=str(Path(__file__).resolve().parents[1]), demora=demora),
                      encoding="utf-8")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8",
               RONYPDF_SETTINGS=str(tmp_path / "ajustes" / "RonyPDF.json"))
    try:
        r = subprocess.run([sys.executable, "-X", "utf8", str(script)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=45, env=env)
    except subprocess.TimeoutExpired:
        pytest.fail("RonyPDF quedó abierto sin ventanas después de salir")
    assert r.returncode == 0 and "SALIÓ 0" in r.stdout, r.stdout + r.stderr


def test_run_task_cuadro_a_los_400ms_y_avisos_tardios(app):
    from ronypdf.widgets import DialogoProgreso, ctx
    padre = _ventana_simple()
    listos, finales = [], []
    soltar = threading.Event()

    def trabajo(progress=None):
        progress(0, 0, "preparando")
        soltar.wait(5)
        progress(1, 2, "mitad")
        return "hecho"

    ctx.run_task("Guardando…", trabajo, listos.append, on_finally=lambda: finales.append(1))
    task = ctx._task

    def dialogos():
        return [w for w in QApplication.topLevelWidgets() if isinstance(w, DialogoProgreso) and w.isVisible()]

    _procesar(0.15)
    assert not dialogos() and not padre.centralWidget().isEnabled()
    _procesar(0.45)                                        # a los 400 ms aparece, aunque no haya porcentaje
    assert len(dialogos()) == 1 and dialogos()[0].etiqueta.text() == "Guardando…\npreparando"
    assert dialogos()[0].barra.maximum() == 0              # indeterminado
    soltar.set()
    _hasta_libre()
    assert listos == ["hecho"] and finales == [1] and not dialogos() and padre.centralWidget().isEnabled()
    task.signals.progress.emit(2, 2, "tarde")              # avisos después del fin: no tocan nada
    task.signals.done.emit("otra vez")
    task.signals.failed.emit("otra vez")
    _procesar(0.05)
    assert listos == ["hecho"] and finales == [1]
    ctx.window = None
    padre.close()


def test_run_task_cancelar_desde_el_cuadro(app):
    from ronypdf.widgets import DialogoProgreso, ctx
    padre = _ventana_simple()
    listos = []

    def lento(progress=None):
        for i in range(400):
            progress(i, 400, f"paso {i}")
            time.sleep(0.01)

    ctx.run_task("Probando…", lento, listos.append)
    _procesar(0.5)
    dlg = next(w for w in QApplication.topLevelWidgets() if isinstance(w, DialogoProgreso) and w.isVisible())
    dlg.boton.click()
    _hasta_libre()
    assert not ctx.busy and listos == [] and padre.centralWidget().isEnabled()
    ctx.window = None
    padre.close()


def test_run_task_con_la_ventana_destruida_a_mitad(app):
    from ronypdf.widgets import ctx
    padre = _ventana_simple()
    finales = []
    soltar = threading.Event()
    ctx.run_task("Probando…", lambda progress=None: soltar.wait(5), lambda r: None,
                 on_finally=lambda: finales.append(1))
    _procesar(0.5)
    padre.deleteLater()                                    # la ventana (y su cuadro) mueren con la tarea en curso
    del padre
    _procesar(0.05)
    soltar.set()
    _hasta_libre()
    assert not ctx.busy and finales == [1]                 # sin RuntimeError al tocar lo ya destruido
    ctx.window = None


# ---------------------------------------------------------------- S5 y S6: cierre

def test_un_segundo_cierre_durante_la_espera_se_ignora(app, monkeypatch):
    from ronypdf import gui, guardado_ui
    win = gui.MainWindow()
    win.show()
    anidados = []

    def esperar(*_a, **_k):
        # (QWidget.close() no reenvía un cierre a mitad; un evento de cierre entregado directamente sí llega)
        ev = QCloseEvent()
        win.closeEvent(ev)
        anidados.append(ev.isAccepted())

    monkeypatch.setattr(guardado_ui, "guardando", lambda: True)
    monkeypatch.setattr(guardado_ui, "esperar_guardado", esperar)
    assert win.close() is True                             # el primer cierre sigue y termina
    assert anidados == [False]                             # el que entró mientras se esperaba, no
    assert win._cerrando is False


def test_cierre_del_autoguardado_conserva_la_recuperacion_si_vence_la_espera(app, tmp_path):
    from ronypdf import guardado, guardado_ui
    ventana = QWidget()
    ventana.pages = {}
    g = guardado_ui.Autoguardado(ventana)
    g.carpeta = tmp_path / guardado.CARPETA_RECUPERACION
    guardado.registrar_sesion(g.carpeta, g.sesion)
    g.tarea = types.SimpleNamespace(cancel_requested=False)   # un autoguardado que no termina
    g.ESPERA_CIERRE = 0.05
    g.cerrar()
    assert g.tarea.cancel_requested and list(g.carpeta.glob("sesion_*.json"))     # se conserva
    g.tarea = None
    g.cerrar()
    assert not g.carpeta.exists() or not list(g.carpeta.glob("sesion_*.json"))   # salida limpia: se borra


# ---------------------------------------------------------------- S4, S9, S10: objetos ya destruidos

def test_destacar_del_formulario_se_cancela_al_limpiar(app):
    from ronypdf.formularios_ui import FormCanvas
    c = FormCanvas(None)
    c.destacar(QRectF(10, 10, 50, 20))
    it = c._destacado
    assert it is not None and it.scene() is c.scene() and c._destacado_timer.isActive()
    c.destacar(QRectF(20, 20, 50, 20))                     # uno nuevo reemplaza al anterior
    assert c._destacado is not it and len(c.scene().items()) == 1
    c.limpiar()                                            # cambiar de documento antes de 1,4 s
    assert c._destacado is None and not c._destacado_timer.isActive() and not c.scene().items()
    c.destacar(QRectF(0, 0, 5, 5))
    c._destacado_timer.setInterval(10)
    c._destacado_timer.start()
    _procesar(0.1)
    assert c._destacado is None and not c.scene().items()


def test_menu_flotante_y_registro_de_soltar_al_destruirse(app):
    from ronypdf import gui
    from ronypdf.widgets import DropArea, _ReenviarSoltar
    win = gui.MainWindow()
    win.show()
    sb = win.sidebar
    sb.set_collapsed(True)
    a = sb.abrir_categoria("convertir")
    assert sb.flyout is a
    otro = types.SimpleNamespace(close=lambda: None)
    sb.flyout = otro                                       # como si ya se hubiera abierto otro menú
    a.close()
    _procesar(0.05)
    assert sb.flyout is otro                               # destruir el primero no borra la referencia al otro
    b = sb.abrir_categoria("editar")
    assert sb.flyout is b
    b.close()
    _procesar(0.05)
    assert sb.flyout is None                               # el menú destruido no queda como referencia
    # S10: el widget vigilado sale del registro al destruirse (Python puede darle su id() a otro)
    zona = DropArea("t", "h", {".pdf"})
    filtro = _ReenviarSoltar(zona)
    w = QWidget()
    filtro.vigilar(w)
    clave = id(w)
    assert clave in filtro._vigilados
    w.deleteLater()
    del w
    _procesar(0.05)
    assert clave not in filtro._vigilados
    win.close()
