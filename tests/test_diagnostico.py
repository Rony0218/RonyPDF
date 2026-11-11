"""Registro de cierres (ronypdf/diagnostico.py) y los blindajes de la ronda de estabilidad."""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

import pytest
import shiboken6

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["RONYPDF_SETTINGS"] = os.path.join(tempfile.mkdtemp(prefix="ronypdf-diag-"), "RonyPDF.json")

from PySide6.QtCore import QEvent, QRectF  # noqa: E402
from PySide6.QtGui import QCloseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget  # noqa: E402

from ronypdf import diagnostico  # noqa: E402

RAIZ = Path(__file__).resolve().parents[1]
RUTA = r"C:\Users\Ana María\Documentos\contrato secreto.pdf"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def sesion(tmp_path):
    """Registro de diagnóstico en ``tmp_path`` (sin faulthandler ni ganchos globales: eso va en un proceso hijo)."""
    carpeta = tmp_path / diagnostico.CARPETA
    assert diagnostico.iniciar(["run.py"], carpeta, fallos=False, qt=False, ganchos=False)
    yield carpeta
    diagnostico.detener()


def _texto(carpeta: Path) -> str:
    return "".join(p.read_text(encoding="utf-8") for p in sorted(carpeta.glob(diagnostico.PATRON)))


def _procesar(segundos: float = 0.0):
    fin = time.monotonic() + segundos
    while True:
        QApplication.processEvents()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        if time.monotonic() >= fin:
            return
        time.sleep(0.01)


# ---------------------------------------------------------------- datos sin información de la persona

def test_limpiar_quita_rutas_nombres_y_textos():
    tb = (
        'Traceback (most recent call last):\n'
        '  File "C:\\Users\\Ana\\AppData\\Local\\Temp\\_MEI4410\\ronypdf\\widgets.py", line 105, in run\n'
        '    result = self.fn(progress=self._progress, **self.kwargs)\n'
        f"FileNotFoundError: [Errno 2] No such file or directory: {RUTA!r}\n"
        "ValueError: invalid literal for int() with base 10: 'Juan Pérez García'\n"
        "KeyError: 'viewer'\n"
        "TypeError: can't multiply sequence by non-int of type 'float'\n"
        "No hay permiso para escribir el archivo:\n"
        "D:\\Mis documentos\\nómina 2026.pdf\n"
        "No se pudo abrir «contrato secreto.pdf»\n")
    out = diagnostico.limpiar(tb)
    for sensible in ("Ana", "contrato", "Juan", "Documentos", "nómina", "_MEI4410"):
        assert sensible not in out, (sensible, out)
    assert 'File "…\\ronypdf\\widgets.py", line 105, in run' in out
    assert "No such file or directory: '<ruta>'" in out
    assert "KeyError: 'viewer'" in out and "of type 'float'" in out and "can't multiply" in out
    assert "\n<ruta>\n" in out and "«…»" in out
    assert diagnostico.etiqueta("Guardando «Acta de la reunión.pdf»…\nPágina 2") == "Guardando «…»… / Página 2"


# ---------------------------------------------------------------- sesión, marcador, rotación

def test_registro_marcador_y_salida_normal(sesion):
    logs = list(sesion.glob(diagnostico.PATRON))
    assert len(logs) == 1 and logs[0].name.startswith("sesion-") and logs[0] == diagnostico.ruta_registro()
    marcador = (sesion / diagnostico.MARCADOR).read_text(encoding="utf-8")
    assert f"pid={os.getpid()}" in marcador and f"registro={logs[0].name}" in marcador
    diagnostico.evento("pantalla: viewer", memoria=True)
    try:
        open(RUTA, encoding="utf-8")
    except OSError:
        diagnostico.excepcion("prueba")
    err = diagnostico.salida_errores()
    err.write(f"Traceback: algo falló con {RUTA}\n")
    err.flush()
    texto = logs[0].read_text(encoding="utf-8")
    assert f"RonyPDF {__import__('ronypdf').__version__}" in texto and "Python 3." in texto and "Windows" in texto
    assert "pantalla: viewer · memoria" in texto
    assert "Excepción (prueba):" in texto and "FileNotFoundError" in texto and "stderr: Traceback" in texto
    assert "Ana" not in texto and "contrato" not in texto
    assert diagnostico.cierre_anterior() is None
    diagnostico.terminar(0)
    assert not (sesion / diagnostico.MARCADOR).exists()
    assert "fin de la sesión (código 0)" in logs[0].read_text(encoding="utf-8")


def test_otra_ventana_abierta_no_cuenta_como_cierre(tmp_path):
    carpeta = tmp_path / diagnostico.CARPETA
    carpeta.mkdir()
    hijo = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        for _ in range(100):
            creado = diagnostico._creacion_proceso(hijo.pid)
            if creado:
                break
            time.sleep(0.05)
        (carpeta / diagnostico.MARCADOR).write_text(f"pid={hijo.pid}\ncreado={creado}\nregistro=sesion-x.log\n",
                                                    encoding="utf-8")
        assert diagnostico.iniciar(["run.py"], carpeta, fallos=False, qt=False, ganchos=False)
        assert diagnostico.cierre_anterior() is None           # esa sesión sigue abierta en otra ventana
        diagnostico.detener()
        # el mismo número de proceso pero otra hora de creación: es otro programa, la sesión anterior murió
        (carpeta / diagnostico.MARCADOR).write_text(f"pid={hijo.pid}\ncreado={creado - 10**9}\nregistro=sesion-x.log\n",
                                                    encoding="utf-8")
        assert diagnostico.iniciar(["run.py"], carpeta, fallos=False, qt=False, ganchos=False)
        assert diagnostico.cierre_anterior()["registro"] == "sesion-x.log"
    finally:
        diagnostico.detener()
        hijo.kill()
        hijo.wait()


def test_rotacion_conserva_las_ultimas_10(tmp_path):
    carpeta = tmp_path / diagnostico.CARPETA
    carpeta.mkdir()
    ahora = time.time()
    for i in range(14):
        p = carpeta / f"sesion-2026010{i // 10}-1000{i % 10:02d}.log"
        p.write_text(f"vieja {i}\n", encoding="utf-8")
        os.utime(p, (ahora - 1000 + i, ahora - 1000 + i))
    try:
        assert diagnostico.iniciar(["run.py"], carpeta, fallos=False, qt=False, ganchos=False)
        quedan = sorted(p.name for p in carpeta.glob(diagnostico.PATRON))
        assert len(quedan) == diagnostico.MAX_SESIONES and diagnostico.ruta_registro().name in quedan
        textos = _texto(carpeta)
        assert "vieja 13" in textos and "vieja 5" in textos and "vieja 4" not in textos
    finally:
        diagnostico.detener()


def test_comprobar_herramientas_no_abre_sesion(tmp_path):
    assert not diagnostico.iniciar(["RonyPDF.exe", "--comprobar-herramientas"], tmp_path / "r")
    assert not (tmp_path / "r").exists()


HIJO = r'''
import os, sys, threading
sys.path.insert(0, {raiz!r})
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["RONYPDF_SETTINGS"] = {ajustes!r}
from ronypdf import diagnostico
diagnostico.iniciar(["run.py"])
modo = {modo!r}
if modo == "caer":
    from PySide6.QtCore import qWarning
    from PySide6.QtWidgets import QApplication
    app = QApplication([])
    diagnostico.evento("pantalla: forms")
    qWarning("QPainter::begin: prueba de advertencia en {ruta}")
    def falla():
        raise ValueError("falló el hilo con {ruta}")
    t = threading.Thread(target=falla); t.start(); t.join()
    import faulthandler
    faulthandler._sigsegv()            # cierre nativo, como el 0xc0000005 de Qt6Widgets.dll
else:
    import json
    print("ANTERIOR", json.dumps(diagnostico.cierre_anterior()), flush=True)
    informe = diagnostico.generar_informe()
    open({informe!r}, "w", encoding="utf-8").write(informe)
    diagnostico.terminar(0)
'''


def _hijo(tmp_path, modo):
    ajustes = tmp_path / "ajustes" / "RonyPDF.json"
    script = tmp_path / f"hijo_{modo}.py"
    script.write_text(HIJO.format(raiz=str(RAIZ), ajustes=str(ajustes), modo=modo, ruta=RUTA.replace("\\", "\\\\"),
                                  informe=str(tmp_path / "informe.txt")), encoding="utf-8")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    env.pop("RONYPDF_SETTINGS", None)
    return subprocess.run([sys.executable, "-X", "utf8", str(script)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120, env=env), ajustes.parent / diagnostico.CARPETA


def test_cierre_nativo_real_deja_registro_y_se_detecta_al_volver(tmp_path):
    r, carpeta = _hijo(tmp_path, "caer")
    assert r.returncode != 0, r.stdout + r.stderr
    logs = list(carpeta.glob(diagnostico.PATRON))
    assert len(logs) == 1, list(carpeta.iterdir())
    texto = logs[0].read_text(encoding="utf-8", errors="replace")
    assert "PySide6 6." in texto and "Qt 6." in texto
    assert "pantalla: forms" in texto
    assert "Qt advertencia: QPainter::begin: prueba de advertencia en <ruta>" in texto
    assert "Excepción (hilo" in texto and "ValueError" in texto
    assert "Windows fatal exception: access violation" in texto or "Fatal Python error" in texto
    assert "Current thread" in texto or "Thread 0x" in texto            # la pila de Python de los hilos
    assert "Ana" not in texto and "contrato" not in texto
    assert (carpeta / diagnostico.MARCADOR).exists()                     # no salió bien: el marcador sigue
    # siguiente arranque: se detecta el cierre y el informe junta los registros, sin datos de la persona
    r, _ = _hijo(tmp_path, "volver")
    assert r.returncode == 0, r.stdout + r.stderr
    anterior = json.loads(r.stdout.split("ANTERIOR", 1)[1].strip().splitlines()[0])
    assert anterior["registro"] == logs[0].name
    informe = (tmp_path / "informe.txt").read_text(encoding="utf-8")
    assert "Informe de diagnóstico de RonyPDF" in informe and "Datos técnicos" in informe
    assert f"Sesión que se cerró de forma inesperada: {logs[0].name}" in informe
    assert f"===== {logs[0].name} =====" in informe
    assert "access violation" in informe or "Fatal Python" in informe
    assert "Ana" not in informe and "contrato" not in informe
    usuario = os.environ.get("USERNAME", "")
    assert len(usuario) < 5 or usuario not in informe                    # ni el nombre de usuario de Windows
    assert not (carpeta / diagnostico.MARCADOR).exists()                 # esta vez salió bien
    r, _ = _hijo(tmp_path, "volver")
    assert "ANTERIOR null" in r.stdout


# ---------------------------------------------------------------- aviso e informe desde la interfaz

def test_aviso_de_cierre_guarda_el_informe(app, tmp_path, monkeypatch):
    from ronypdf import diagnostico_aviso, widgets
    carpeta = tmp_path / diagnostico.CARPETA
    carpeta.mkdir()
    (carpeta / diagnostico.MARCADOR).write_text("pid=999999\ncreado=1\nregistro=sesion-anterior.log\n", encoding="utf-8")
    (carpeta / "sesion-anterior.log").write_text("10:00:00.000 [principal] pantalla: forms\nstderr: <ruta>\n",
                                                 encoding="utf-8")
    try:
        assert diagnostico.iniciar(["run.py"], carpeta, fallos=False, qt=False, ganchos=False)
        assert diagnostico.cierre_anterior()["registro"] == "sesion-anterior.log"
        ventana = QMainWindow()
        ventana.resize(1000, 660)
        ventana.show()
        aviso = diagnostico_aviso.mostrar_aviso(ventana)
        _procesar()
        assert aviso.isVisible() and aviso.titulo.text() == "RonyPDF se cerró de forma inesperada la última vez."
        assert aviso.b_guardar.text() == "Guardar informe…" and aviso.b_no.text() == "Ahora no"
        assert aviso.geometry().right() < ventana.width() and aviso.geometry().bottom() < ventana.height()
        ventana.resize(1200, 800)                      # sigue abajo a la derecha
        _procesar()
        assert ventana.width() - aviso.geometry().right() < 40
        destino = tmp_path / "mi informe"
        monkeypatch.setattr(widgets.QFileDialog, "getSaveFileName", lambda *a, **k: (str(destino), ""))
        hechos = []
        monkeypatch.setattr(widgets, "show_done", lambda *a, **k: hechos.append(k.get("file")))
        aviso.b_guardar.click()
        _procesar()
        guardado = Path(str(destino) + ".txt")
        assert guardado.exists() and hechos == [str(guardado)]
        informe = guardado.read_text(encoding="utf-8")
        assert "Sesión que se cerró de forma inesperada: sesion-anterior.log" in informe
        assert "===== sesion-anterior.log =====" in informe and "pantalla: forms" in informe
        assert not shiboken6.isValid(aviso) or not aviso.isVisible()
        # «Ahora no» solo lo quita
        otro = diagnostico_aviso.mostrar_aviso(ventana)
        otro.b_no.click()
        _procesar()
        assert not ventana.findChildren(diagnostico_aviso.AvisoCierre)
        ventana.close()
    finally:
        diagnostico.detener()


# ---------------------------------------------------------------- transiciones: pantallas, tareas, cierre

def test_registro_de_pantallas_tareas_y_cierre(app, sesion, monkeypatch):
    from ronypdf import gui, guardado_ui
    from ronypdf.widgets import ctx
    win = gui.MainWindow()
    win.show()
    win.navigate("merge")
    listos = []
    ctx.run_task("Uniendo «Acta privada.pdf»…", lambda progress=None: 7, listos.append)
    ctx.run_task("Otra", lambda progress=None: 1, listos.append)        # ocupado: no se lanza
    falla = []
    for _ in range(300):
        _procesar(0.01)
        if not ctx.busy:
            break
    monkeypatch.setattr(guardado_ui.QMessageBox, "warning", lambda *a, **k: falla.append(a[2]))

    def rompe(progress=None):
        raise ZeroDivisionError(f"con {RUTA}")

    ctx.run_task("Probando…", rompe, listos.append)
    for _ in range(300):
        _procesar(0.01)
        if not ctx.busy:
            break
    win.close()
    texto = _texto(sesion)
    assert listos == [7] and falla and "ZeroDivisionError" in falla[0]
    assert "pantalla: home" in texto and "pantalla: merge · memoria" in texto
    assert "test_registro_de_pantallas_tareas_y_cierre.<locals>.<lambda> · Uniendo «…»…" in texto
    assert texto.count("tarea inicia: ") == 2                         # la que llegó con otra en curso no corre
    assert "tarea termina: " in texto and "tarea falla: " in texto
    assert "Excepción (tarea):" in texto and "ZeroDivisionError: con <ruta>" in texto
    assert "cierre: inicia" in texto and "cierre: termina" in texto
    assert "Acta" not in texto and "Ana" not in texto
