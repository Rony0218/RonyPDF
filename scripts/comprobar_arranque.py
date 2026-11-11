"""Prueba de arranque sin pantalla: construye la ventana principal y abre cada herramienta registrada.

Uso, desde la carpeta del proyecto:
    .venv\\Scripts\\python.exe scripts\\comprobar_arranque.py
    (con CAPTURAS=carpeta guarda además una captura de cada pantalla)

Recorre todas las herramientas de los módulos ``*_ui.py`` (ver ``ronypdf/extensiones.py``): las abre con un
archivo de ejemplo, en el tamaño mínimo de la ventana y en uno normal, y prueba los enganches con el inicio, el
visor y el buscador del menú. Termina con código 1 si aparece una advertencia de Qt o de Python, una excepción,
un diálogo modal inesperado o cualquier texto en la consola de errores (también al cerrar).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import warnings
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
HIJO = "_RONYPDF_ARRANQUE_HIJO"
# Las herramientas de las rondas 1 y 2 deben estar siempre; las que se agreguen después se recorren igual.
ESPERADAS = ("forms", "print", "redact", "sanitize", "office2pdf", "pdf2office",
             "batch", "rename", "ocrcheck", "stamps", "backup", "compare")


def _padre() -> int:
    """Corre la prueba en un proceso aparte para revisar también lo que se escriba en stderr al cerrar."""
    env = dict(os.environ, **{HIJO: "1", "PYTHONIOENCODING": "utf-8"})
    r = subprocess.run([sys.executable, __file__], cwd=RAIZ, env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=600)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stdout.write(r.stdout)
    if r.stderr.strip():
        print("\nPROBLEMAS: la consola de errores no quedó vacía:\n" + r.stderr)
        return 1
    return r.returncode


if __name__ == "__main__" and os.environ.get(HIJO) != "1":
    sys.exit(_padre())

# ---------------------------------------------------------------- proceso hijo
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
TMP = Path(tempfile.mkdtemp(prefix="ronypdf-arranque-"))
os.environ["RONYPDF_SETTINGS"] = str(TMP / "RonyPDF.json")      # nunca toca los ajustes reales
sys.path.insert(0, str(RAIZ))

PROBLEMAS: list[str] = []
DIALOGOS: list[str] = []
CAPTURAS = os.environ.get("CAPTURAS")

warnings.simplefilter("always")
_showwarning_original = warnings.showwarning
warnings.showwarning = lambda msg, cat, archivo, linea, *a, **k: PROBLEMAS.append(
    f"Advertencia de Python: {cat.__name__}: {msg} ({archivo}:{linea})")
sys.excepthook = lambda t, e, tb: PROBLEMAS.append("Excepción: " + "".join(traceback.format_exception(t, e, tb)))

import pymupdf  # noqa: E402
from PySide6.QtCore import QtMsgType, qInstallMessageHandler  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox  # noqa: E402


def _qt(tipo, _contexto, mensaje):
    if tipo != QtMsgType.QtDebugMsg:
        PROBLEMAS.append(f"Mensaje de Qt ({tipo.name}): {mensaje}")


qInstallMessageHandler(_qt)


def _dialogo(nombre, respuesta):
    def fn(*a, **_k):
        DIALOGOS.append(f"{nombre}: " + next((x for x in a if isinstance(x, str)), ""))
        return respuesta
    return staticmethod(fn)


# Un diálogo modal dejaría colgada la prueba: se anotan y cuentan como problema.
for _n in ("information", "warning", "critical"):
    setattr(QMessageBox, _n, _dialogo(_n, QMessageBox.StandardButton.Ok))
QMessageBox.question = _dialogo("question", QMessageBox.StandardButton.No)
QMessageBox.exec = lambda self: DIALOGOS.append(f"QMessageBox: {self.text()}") or 0
QDialog.exec = lambda self: DIALOGOS.append(f"QDialog: {type(self).__name__}") or 0
QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: ("", ""))
QFileDialog.getOpenFileNames = staticmethod(lambda *a, **k: ([], ""))
QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: ("", ""))
QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: "")


def esperar(seg: float = 0.3) -> None:
    from ronypdf.widgets import ctx
    fin = time.time() + seg
    while time.time() < fin or ctx.busy:
        QApplication.processEvents()
        time.sleep(0.01)
        if time.time() > fin + 60:
            PROBLEMAS.append("Una tarea en segundo plano no terminó en 60 s")
            break


def captura(win, nombre: str) -> None:
    if CAPTURAS:
        os.makedirs(CAPTURAS, exist_ok=True)
        win.grab().save(os.path.join(CAPTURAS, nombre + ".png"))


def muestras() -> dict[str, str]:
    """Un PDF con marcadores, enlace y datos personales; un formulario; una imagen y un .docx."""
    pdf = TMP / "informe con tildes ñ.pdf"
    with pymupdf.open() as d:
        for i in range(4):
            p = d.new_page()
            p.insert_text((72, 90), f"Capítulo {i + 1}: niñez y educación", fontsize=20)
            p.insert_text((72, 130), "Cédula C.C. 1.020.304.050 · celular 300 123 4567 · correo ana@ejemplo.org",
                          fontsize=10)
        d[0].insert_link({"kind": pymupdf.LINK_GOTO, "page": 2, "from": pymupdf.Rect(72, 75, 300, 95)})
        d.set_toc([[1, "Introducción", 1], [1, "Desarrollo", 2], [2, "Detalle", 3], [1, "Anexos", 4]])
        d.set_metadata({"title": "Informe", "author": "Prueba"})
        d.save(pdf)

    form = TMP / "formulario.pdf"
    with pymupdf.open() as d:
        p = d.new_page()
        p.insert_text((72, 80), "Nombre:", fontsize=12)
        for nombre, tipo, rect, opciones in (
                ("nombre", pymupdf.PDF_WIDGET_TYPE_TEXT, (140, 66, 400, 86), None),
                ("acepta", pymupdf.PDF_WIDGET_TYPE_CHECKBOX, (140, 100, 156, 116), None),
                ("ciudad", pymupdf.PDF_WIDGET_TYPE_COMBOBOX, (140, 130, 300, 150), ["Bogotá", "Medellín", "Cali"])):
            w = pymupdf.Widget()
            w.field_name, w.field_type, w.rect = nombre, tipo, pymupdf.Rect(rect)
            if opciones:
                w.choice_values = opciones
            p.add_widget(w)
        d.save(form)

    png = TMP / "foto.png"
    with pymupdf.open() as d:
        p = d.new_page(width=300, height=200)
        p.insert_text((40, 100), "FOTO", fontsize=40)
        p.get_pixmap(dpi=72).save(png)

    from ronypdf import oficina
    docx = TMP / "carta.docx"
    oficina.escribir_docx([oficina.Parrafo("Carta de prueba con tildes: Bogotá, niñez.")], docx)
    return {"pdf": str(pdf), "form": str(form), "png": str(png), "docx": str(docx)}


def main() -> int:
    app = QApplication([sys.argv[0]])
    from ronypdf import extensiones
    from ronypdf.gui import SECTIONS, MainWindow, _light_palette
    from ronypdf.style import QSS
    app.setStyle("Fusion")
    app.setPalette(_light_palette())
    app.setStyleSheet(QSS)

    registros = extensiones.registros()
    claves = [r["clave"] for r in registros]
    faltan = [k for k in ESPERADAS if k not in claves]
    if faltan:
        PROBLEMAS.append(f"Herramientas sin registrar: {', '.join(faltan)}")

    win = MainWindow()
    win.resize(1000, 660)                      # tamaño mínimo de la ventana
    win.show()
    esperar()
    print(f"Ventana principal: {len(win.pages)} pantallas")
    for _sec, titulo, _t, items in SECTIONS:
        print(f"  {titulo}: " + ", ".join(t for _k, t, _a in items))
    print("  Tarjetas del inicio: " + ", ".join(t for _k, _a, t, _d in extensiones.inicio()))
    captura(win, "inicio")

    m = muestras()
    for r in registros:
        clave = r["clave"]
        tipos = {t.lower() for t in r.get("tipos", ())}
        archivo = m["docx"] if ".docx" in tipos else m["form"] if clave == "forms" else m["pdf"]
        antes = len(PROBLEMAS) + len(DIALOGOS)
        win.navigate(clave)
        esperar()
        page = win.pages[clave]
        page.accept_files([archivo])
        esperar(1.0)
        captura(win, f"{clave}-1000")
        win.resize(1240, 820)
        esperar(0.2)
        captura(win, f"{clave}-1240")
        win.resize(1000, 660)
        esperar(0.2)
        bien = len(PROBLEMAS) + len(DIALOGOS) == antes
        print(f"Abierta «{r['titulo']}» ({type(page).__name__}) con {Path(archivo).name}: "
              f"{'bien' if bien else 'CON PROBLEMAS'}")

    # inicio: un .docx va a la conversión de Office
    win.navigate("home")
    esperar()
    win.open_files([m["docx"]])
    esperar()
    if "office2pdf" in win.pages and win.stack.currentWidget() is not win.pages["office2pdf"]:
        PROBLEMAS.append("Abrir un .docx desde el inicio no llevó a «Word y Excel a PDF»")

    # visor: franja de formulario, botones de las extensiones, imprimir y censurar el documento abierto
    v = win.pages["viewer"]
    win.navigate("viewer")
    v.load(m["form"])
    esperar(0.5)
    if not v.ext_avisos.isVisible():
        PROBLEMAS.append("El visor no mostró la franja «Rellenar formulario» con un formulario")
    v.load(m["pdf"])
    esperar(0.5)
    captura(win, "visor-1000")
    # la barra del visor debe caber también con el menú lateral abierto (y en una ventana normal)
    contraido = win.sidebar.is_collapsed()
    win.sidebar.set_collapsed(False, remember=False)
    esperar(0.2)
    captura(win, "visor-1000-menu")
    win.resize(1240, 820)
    esperar(0.2)
    captura(win, "visor-1240")
    win.resize(1000, 660)
    win.sidebar.set_collapsed(contraido, remember=False)
    esperar(0.2)
    print("Botones del visor agregados por extensiones: "
          + ", ".join(b.toolTip() for b in extensiones.botones_visor(v)))
    for accion, clave in (("imprimir_ui.imprimir_desde_visor", "print"),
                          ("censura_ui._censurar_desde_visor", "redact")):
        modulo, funcion = accion.split(".")
        win.navigate("viewer")
        esperar()
        getattr(__import__(f"ronypdf.{modulo}", fromlist=[funcion]), funcion)(v)
        esperar(1.0)
        if win.stack.currentWidget() is not win.pages[clave]:
            PROBLEMAS.append(f"El botón del visor no abrió «{clave}»")

    # buscador del menú (Ctrl K): cada herramienta aparece al escribir su título
    win.navigate("home")
    esperar()
    sb = win.sidebar
    for r in registros:
        sb.search.setText(r["titulo"])
        if sb.items[r["clave"]].isHidden():
            PROBLEMAS.append(f"El buscador del menú no encuentra «{r['titulo']}»")
    sb.search.clear()

    win.close()
    esperar(0.5)
    PROBLEMAS.extend("Diálogo inesperado: " + d for d in DIALOGOS)
    if PROBLEMAS:
        print("\nPROBLEMAS:")
        for p in PROBLEMAS:
            print(" - " + p)
        return 1
    print("\nArranque correcto: sin errores ni advertencias.")
    return 0


if __name__ == "__main__":
    try:
        codigo = main()
    except Exception:  # noqa: BLE001
        traceback.print_exc(file=sys.stdout)
        for p in PROBLEMAS:
            print(" - " + p)
        codigo = 1
    sys.stdout.flush()
    # Desde aquí, lo que Qt o Python digan al cerrar va a stderr, que revisa el proceso padre.
    qInstallMessageHandler(None)
    sys.excepthook = sys.__excepthook__
    warnings.showwarning = _showwarning_original
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(codigo)
