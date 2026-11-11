"""Toma las capturas de RonyPDF 2.0 con los documentos de ejemplo, para el README.

Uso:
  python -X utf8 scripts/capturas.py [--docs carpeta] [--out docs/capturas] [--scale 2]

Corre la interfaz real sin ventana (Qt «offscreen»), preparada igual que la app (Fusion, fuentes del tema,
paleta clara y hoja de estilos), recorre las herramientas con los documentos de ejemplo de la empresa ficticia
«Café La Montaña» y guarda cada pantalla en PNG. Si no se indica --docs, crea los documentos con
scripts/demo_docs.py en una carpeta temporal. Usa ajustes temporales: nunca toca los recientes reales.
"""

from __future__ import annotations

import argparse
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ap = argparse.ArgumentParser()
ap.add_argument("--docs", default=None, help="carpeta con los documentos de ejemplo (por defecto se crean)")
ap.add_argument("--out", default=str(ROOT / "docs" / "capturas"))
ap.add_argument("--scale", default="2", help="factor de escala de la interfaz (2 = el doble de píxeles)")
args = ap.parse_args()

work = Path(tempfile.mkdtemp(prefix="ronypdf-capturas-"))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
os.environ["QT_SCALE_FACTOR"] = args.scale
os.environ["RONYPDF_SETTINGS"] = str(work / "RonyPDF.json")
sys.path.insert(0, str(ROOT))

import pymupdf  # noqa: E402
from PySide6.QtCore import QPointF, QRectF, QThreadPool  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

app = QApplication([])
QMessageBox.exec = lambda *a, **k: 0  # los avisos no detienen las capturas


def _aviso(_parent, titulo, texto, *a, **k):
    print(f"   [aviso] {titulo}: {texto}", flush=True)
    return QMessageBox.StandardButton.Ok


for _f in ("information", "warning", "critical", "question"):
    setattr(QMessageBox, _f, staticmethod(_aviso))

from ronypdf import comentarios_ui, guardado_ui, gui, sellos, tema, tools, widgets  # noqa: E402
from ronypdf.viewer import InkItem  # noqa: E402

guardado_ui.preguntar_cambios = lambda *a, **k: "descartar"  # las ediciones de muestra no se guardan

app.setStyle("Fusion")
tema.cargar_fuentes()
app.setPalette(gui._light_palette())
app.setStyleSheet(gui.QSS)

OUT = Path(args.out)
OUT.mkdir(parents=True, exist_ok=True)
DOCS = work / "Documentos"
if args.docs:
    shutil.copytree(args.docs, DOCS)
else:
    subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "scripts" / "demo_docs.py"), str(DOCS)], check=True)


def ficha_proveedor(path: Path) -> None:
    """Una ficha con datos personales inventados, para mostrar lo que encuentra Censurar."""
    fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    doc = pymupdf.open()
    pg = doc.new_page(width=612, height=792)
    for name, f in (("body", "segoeui.ttf"), ("bold", "segoeuib.ttf"), ("serif", "georgiab.ttf")):
        pg.insert_font(fontname=name, fontfile=str(fonts / f))
    ink, muted, green = (0.12, 0.16, 0.22), (0.42, 0.45, 0.50), (0.13, 0.30, 0.22)
    pg.draw_rect(pymupdf.Rect(0, 0, 612, 92), color=None, fill=green)
    pg.insert_text((54, 50), "CAFÉ LA MONTAÑA S.A.S.", fontname="bold", fontsize=11, color=(0.98, 0.96, 0.92))
    pg.insert_text((54, 70), "Medellín · Colombia", fontname="body", fontsize=9, color=(0.85, 0.88, 0.85))
    pg.insert_text((54, 140), "Ficha de proveedor", fontname="serif", fontsize=24, color=ink)
    pg.insert_text((54, 162), "Actualización de datos · noviembre de 2026", fontname="body", fontsize=10, color=muted)
    filas = [("Proveedor", "Estudio Digital Andino S.A.S."), ("NIT", "901.234.567-8"),
             ("Representante legal", "Andrés Gómez Restrepo"), ("Cédula", "C.C. 1.036.654.321"),
             ("Celular", "310 456 7890"), ("Correo", "andres.gomez@correo.com"),
             ("Cuenta de ahorros", "4561 2378 9012")]
    y = 205
    for k, v in filas:
        pg.draw_line((54, y + 12), (558, y + 12), color=(0.88, 0.89, 0.91), width=0.8)
        pg.insert_text((54, y), k, fontname="body", fontsize=10, color=muted)
        pg.insert_text((210, y), v, fontname="bold", fontsize=11, color=ink)
        y += 36
    y += 18
    pg.insert_text((54, y), "Contacto en Café La Montaña", fontname="bold", fontsize=12, color=ink)
    texto = ("Laura Restrepo, jefa de compras. Celular 315 222 3344, correo laura.restrepo@correo.com. "
             "Los pagos se consignan en la cuenta de ahorros indicada, dentro de los 30 días siguientes a la factura.")
    pg.insert_textbox(pymupdf.Rect(54, y + 10, 558, y + 90), texto, fontname="body", fontsize=10.5, color=ink,
                      lineheight=1.45)
    pg.insert_text((54, 740), "Documento de ejemplo creado para mostrar RonyPDF. Personas y datos ficticios.",
                   fontname="body", fontsize=8, color=muted)
    doc.subset_fonts()
    doc.save(path, garbage=3, deflate=True)


ficha_proveedor(DOCS / "Ficha de proveedor.pdf")
D = {k: str(DOCS / f) for k, f in {
    "informe": "Informe anual 2026.pdf", "contrato": "Contrato de servicios.pdf",
    "propuesta": "Propuesta comercial.pdf", "factura": "Factura FE-0142.pdf",
    "catalogo": "Catálogo de cafés.pdf", "acta": "Acta escaneada.pdf", "ficha": "Ficha de proveedor.pdf"}.items()}

win = gui.MainWindow()
win.resize(1280, 800)
win.show()
app.processEvents()


def esperar(seg: float) -> None:
    fin = time.monotonic() + seg
    while time.monotonic() < fin:
        app.processEvents()
        time.sleep(0.02)


def tranquilo(seg: float = 1.2, hilos: int = 60000) -> None:
    """Espera a que terminen las tareas de fondo (vistas previas, búsquedas, OCR) y a que se pinte todo."""
    esperar(0.4)
    QThreadPool.globalInstance().waitForDone(hilos)
    esperar(seg)


def ir(key: str, archivos: list[str] | None = None, seg: float = 1.5):
    win.navigate(key)
    app.processEvents()
    pagina = win.pages.get(key)
    if archivos:
        pagina.accept_files(archivos)
    tranquilo(seg)
    return pagina


def shot(name: str) -> None:
    esperar(0.3)
    win.grab().save(str(OUT / f"{name}.png"))
    print("  ", name, flush=True)


def guardar_en(path: str):
    return lambda *a, **k: path


print("Capturas en", OUT)

# 1. Inicio con recientes
s = widgets.ctx.settings
for key, pages, ago in (("factura", 1, 86400 * 3), ("catalogo", 8, 86400), ("contrato", 1, 3600 * 5),
                        ("informe", 6, 600)):
    s.add_recent(D[key], pages)
    s.data["recent"][0]["opened"] = time.time() - ago
s.save()
ir("home")
win.pages["home"].refresh_recent()
shot("01-inicio")

# 2. Visor y editor: resaltar la cláusula de pago, encerrar la cifra, firmar y sellar
v = ir("viewer", [D["contrato"]], 2.0)
c = v.canvas
c.set_zoom(1.35)
tranquilo(1.0)
p0 = c.pages[0]
with pymupdf.open(D["contrato"]) as doc:
    pag = doc[0]
    valor = pag.search_for("El valor total es de")[0]
    cifra = pag.search_for("38.000.000")[0]
    laura = pag.search_for("Laura Restrepo")[0]
    fin_parrafo = pag.search_for("puesta en marcha")[0]
o = p0.pos()
z = c.transform().m11()
c.centerOn(o.x() + valor.x0 - 14 / z + c.viewport().width() / z / 2, o.y() + (valor.y0 + laura.y1) / 2)
tranquilo(0.8)
shot("02a-visor-limpio")
c.add_highlight(p0, QRectF(o.x() + valor.x0 - 1, o.y() + valor.y0, 470, fin_parrafo.y1 - valor.y0))
cx, cy = o.x() + (cifra.x0 + cifra.x1) / 2, o.y() + (cifra.y0 + cifra.y1) / 2
rx, ry = (cifra.x1 - cifra.x0) / 2 + 9, (cifra.y1 - cifra.y0) / 2 + 5
tinta = InkItem(QPointF(cx + rx, cy), QColor("#D63A3A"), 1.8)
for k in range(1, 58):
    a = k / 52 * 2 * math.pi
    tinta.add_point(QPointF(cx + rx * math.cos(a) * (1 + 0.04 * math.sin(3 * a)), cy - ry * math.sin(a) - k * 0.05))
c._register([tinta])
# la firma de Laura sobre su renglón: un trazo continuo (Catmull-Rom) por estos puntos
fx, fy = o.x() + laura.x0 + 4, o.y() + laura.y0 - 13
ctrl = [(0, 2), (3, -10), (6, -26), (3, -24), (1, -12), (2, 0), (8, 3), (14, -2), (18, -8), (22, -6), (21, 0),
        (25, -1), (28, -8), (30, -1), (34, -2), (36, -9), (38, -4), (39, 0), (44, -1), (47, -8), (50, -7), (49, 0),
        (53, -2), (58, -4), (62, -24), (66, -26), (74, -22), (72, -15), (64, -13), (70, -10), (80, 1), (86, 3),
        (70, 8), (40, 10), (15, 9), (30, 6), (70, 5), (118, 2)]
ctrl = [(fx + 1.15 * x, fy + y) for x, y in ctrl]
ext = [ctrl[0]] + ctrl + [ctrl[-1]]
pts = []
for i in range(1, len(ext) - 2):
    q0, q1, q2, q3 = ext[i - 1], ext[i], ext[i + 1], ext[i + 2]
    for k in range(12):
        t = k / 12
        pts.append(tuple(0.5 * (2 * q1[j] + (-q0[j] + q2[j]) * t + (2 * q0[j] - 5 * q1[j] + 4 * q2[j] - q3[j]) * t * t
                                + (-q0[j] + 3 * q1[j] - 3 * q2[j] + q3[j]) * t ** 3) for j in (0, 1)))
firma = InkItem(QPointF(*pts[0]), QColor("#1E3A8A"), 1.3)
for x, y in pts[1:]:
    firma.add_point(QPointF(x, y))
c._register([firma])
comentarios_ui.poner_autor("Laura Restrepo", "Jefa de compras")
sello = sellos.Sello(id=sellos.nuevo_id(), nombre="Aprobado", lineas=["APROBADO", "{fecha}"], forma="redondeado",
                     color="#1F7A4D", borde="doble", inclinacion=-8, ancho_pt=150)
comentarios_ui.poner_sello(v, sello, 0, laura.x1 + 175, laura.y0 - 52)
comentarios_ui.seleccionar(v, None, mostrar_panel=True)
c.scene().clearSelection()
v.set_tool("select")
tranquilo(0.8)
shot("02-visor")

# 3. Unir
ir("merge", [D["propuesta"], D["contrato"], D["informe"], D["factura"]])
shot("03-unir")

# 4. Organizar páginas
ir("organize", [D["informe"]], 2.5)
shot("04-organizar")

# 5. Comprimir (de verdad) y su resultado
comp = ir("compress", [D["catalogo"]])
comp.levels.set_value("alta")
shot("05-comprimir")
tools.ask_save = guardar_en(str(DOCS / "Catálogo de cafés_comprimido.pdf"))
comp.run()
tranquilo(1.5, 180000)
shot("05b-comprimir-resultado")

# 6. OCR: el visor detecta el escaneado y lo vuelve texto buscable
v = ir("viewer", [D["acta"]], 2.0)
v.canvas.verticalScrollBar().setValue(0)
tranquilo(0.8)
shot("06-ocr-antes")
v._go_ocr()
tranquilo(1.5)
shot("06b-ocr-herramienta")
buscable = str(DOCS / "Acta escaneada_texto.pdf")
tools.ask_save = guardar_en(buscable)
win.pages["ocr"].revisar.setChecked(False)  # guarda de una vez, sin pasar por la revisión de palabras dudosas
win.pages["ocr"].run()
tranquilo(2.0, 300000)
if not Path(buscable).exists():
    raise SystemExit("El OCR no produjo el PDF buscable")
v = ir("viewer", [buscable], 2.0)
v.search.setText("Acuerdos")
v._search_enter()
tranquilo(1.0)
shot("06c-ocr-buscar")

# 7. Proteger con contraseña
prot = ir("protect", [D["contrato"]])
prot.pw.setText("cafe-2026")
prot.pw2.setText("cafe-2026")
shot("07-proteger")

# 8. Censurar datos personales: lo que encuentra y cómo queda
red = ir("redact", [D["ficha"]], 2.0)
red.buscar_ahora()
tranquilo(1.5)
shot("09-censurar")
red.vista.set_despues(True)
tranquilo(1.0)
shot("09b-censurar-despues")

# 9. La paleta Ctrl K entiende palabras coloquiales: «juntar» encuentra Unir PDF
ir("home")
win.abrir_paleta("")
win.paleta.entrada.setText("juntar")
win.paleta.entrada.end(False)
esperar(0.8)
shot("10-paleta")
win.paleta.cerrar()
esperar(0.3)

# 10. Menú contraído
ir("home")
win.sidebar.set_collapsed(True, remember=False)
esperar(0.5)
shot("08-menu-contraido")

print("listo:", len(list(OUT.glob("*.png"))), "imágenes")
shutil.rmtree(work, ignore_errors=True)
