"""Capturas reales de RonyPDF 2.0 para el video, con la interfaz de verdad (sin ventana, a escala 2).

Uso, con el Python de la app (PySide6 + PyMuPDF):
    python -X utf8 video/capturas.py [carpeta] [tomas…]   → video/capturas/*.png (2480 × 1640) y datos.json

«tomas» limita la corrida a esas partes (iconos, inicio, paleta, visor, organizar, ocr, convertir, censurar, mas).

Prepara la app como en las capturas del rediseño (Fusion, fuentes del tema, paleta clara y gui.QSS), crea los
documentos de ejemplo de «Café La Montaña» con scripts/demo_docs.py en una carpeta temporal y recorre las
pantallas que salen en el video. Algunas tienen interacción de verdad: la paleta Ctrl K escribiendo «juntar»,
las ediciones del visor (resaltado, dibujo, firma y sello), los datos que encuentra Censurar y cómo quedan,
el OCR de un acta escaneada y la compresión de un catálogo con fotos (sus tamaños reales van a datos.json).

Para Censurar se crea además una «Ficha de proveedor» con datos personales inventados (cédula, NIT, celular,
correo y cuenta), porque los documentos de ejemplo no traen datos de ese tipo.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

AQUI = Path(__file__).resolve().parent
WT = AQUI.parent
TOMAS = ["iconos", "inicio", "paleta", "visor", "organizar", "ocr", "convertir", "censurar", "mas"]
_ARGS = [a for a in sys.argv[1:] if a not in TOMAS]
SOLO = [a for a in sys.argv[1:] if a in TOMAS] or TOMAS
OUT = Path(_ARGS[0]) if _ARGS else AQUI / "capturas"
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_SCALE_FACTOR"] = "2"
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
os.environ["RONYPDF_SETTINGS"] = os.path.join(tempfile.mkdtemp(), "s.json")
sys.path.insert(0, str(WT))

import pymupdf  # noqa: E402
from PySide6.QtCore import QPointF, QRectF, QThreadPool  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

app = QApplication([])
QMessageBox.exec = lambda *a, **k: 0          # los avisos de «Listo» no detienen las capturas
from ronypdf import gui, tema  # noqa: E402

app.setStyle("Fusion")
tema.cargar_fuentes()
app.setPalette(gui._light_palette())
app.setStyleSheet(gui.QSS)


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


# ---------------------------------------------------------------- documentos de ejemplo
DEMO = Path(tempfile.mkdtemp(prefix="ronypdf-demo-"))
subprocess.run([sys.executable, "-X", "utf8", str(WT / "scripts" / "demo_docs.py"), str(DEMO)], check=True)


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
    filas = [
        ("Proveedor", "Estudio Digital Andino S.A.S."),
        ("NIT", "901.234.567-8"),
        ("Representante legal", "Andrés Gómez Restrepo"),
        ("Cédula", "C.C. 1.036.654.321"),
        ("Celular", "310 456 7890"),
        ("Correo", "andres.gomez@correo.com"),
        ("Dirección", "Calle 45 # 12–30, Medellín"),   # raya: el guion de Segoe UI se extrae como U+2010
        ("Cuenta de ahorros", "4561 2378 9012"),
    ]
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


ficha_proveedor(DEMO / "Ficha de proveedor.pdf")
D = {p.stem: str(p) for p in DEMO.glob("*.pdf")}
CUATRO = [D[k] for k in ("Informe anual 2026", "Contrato de servicios", "Propuesta comercial", "Factura FE-0142")]

# ---------------------------------------------------------------- la ventana
win = gui.MainWindow()
win.resize(1240, 820)
win.show()
app.processEvents()
OUT.mkdir(parents=True, exist_ok=True)
DATOS: dict = {}
hechas: list[str] = []


def foto(nombre: str) -> None:
    img = win.grab()
    img.save(str(OUT / f"{nombre}.png"))
    hechas.append(nombre)
    print(f"  {nombre}.png  {img.width() * img.devicePixelRatio():.0f} × {img.height() * img.devicePixelRatio():.0f}",
          flush=True)


def ir(key: str, archivos: list[str] | None = None, seg: float = 1.5) -> object:
    win.navigate(key)
    app.processEvents()
    pagina = win.pages[key] if key != "home" else None
    if archivos:
        pagina.accept_files(archivos)
    tranquilo(seg)
    return pagina


def paso(nombre: str, fn) -> None:
    try:
        fn()
    except Exception as e:  # noqa: BLE001 — una toma fallida no detiene las demás
        import traceback
        traceback.print_exc()
        print("FALLÓ", nombre, type(e).__name__, e, flush=True)


# 1. Inicio
def inicio():
    ir("home")
    foto("inicio")


# 2. Paleta Ctrl K escribiendo «juntar»
def paleta():
    ir("home")
    win.abrir_paleta("")
    for i, txt in enumerate(["", "j", "ju", "jun", "junt", "junta", "juntar"]):
        win.paleta.entrada.setText(txt)          # como si se escribiera letra por letra
        win.paleta.entrada.end(False)
        esperar(0.5)
        foto(f"paleta-{i}")
    win.paleta.cerrar()
    esperar(0.3)


# 3. Visor: resaltar, dibujar, firmar y sellar sobre el contrato
def visor():
    from ronypdf import comentarios_ui, sellos
    from ronypdf.viewer import InkItem
    v = ir("viewer", [D["Contrato de servicios"]], 2.0)
    c = v.canvas
    c.set_zoom(1.35)
    tranquilo(1.0)
    pg = c.pages[0]
    with pymupdf.open(D["Contrato de servicios"]) as doc:
        page = doc[0]
        valor = page.search_for("El valor total es de")[0]
        cifra = page.search_for("38.000.000")[0]
        laura = page.search_for("Laura Restrepo")[0]
        fin_parrafo = page.search_for("puesta en marcha")[0]
    o = pg.pos()
    # que se vea de la cláusula tercera a las firmas, con un poco de margen a la izquierda del texto
    z = c.transform().m11()
    ancho = c.viewport().width() / z
    c.centerOn(o.x() + valor.x0 - 14 / z * 1.0 + ancho / 2, o.y() + (valor.y0 + laura.y1) / 2)
    tranquilo(0.8)
    DATOS["visor"] = {"zoom": 1.35}
    v.set_tool("highlight")
    esperar(0.4)
    foto("visor-0")
    # resaltado de la cláusula de pago (dos renglones)
    c.add_highlight(pg, QRectF(o.x() + valor.x0 - 1, o.y() + valor.y0, 470, fin_parrafo.y1 - valor.y0))
    c.scene().clearSelection()
    esperar(0.4)
    foto("visor-1")
    # un círculo a mano alrededor de la cifra
    v.set_tool("ink")
    cx, cy = o.x() + (cifra.x0 + cifra.x1) / 2, o.y() + (cifra.y0 + cifra.y1) / 2
    rx, ry = (cifra.x1 - cifra.x0) / 2 + 9, (cifra.y1 - cifra.y0) / 2 + 5
    import math
    tinta = InkItem(QPointF(cx + rx, cy), QColor("#D63A3A"), 1.8)
    for k in range(1, 58):
        a = k / 52 * 2 * math.pi
        tinta.add_point(QPointF(cx + rx * math.cos(a) * (1 + 0.04 * math.sin(3 * a)), cy - ry * math.sin(a) - k * 0.05))
    c._register([tinta])
    c.scene().clearSelection()
    esperar(0.4)
    foto("visor-2")
    # la firma de Laura sobre su renglón, a mano alzada
    fx, fy = o.x() + laura.x0 + 4, o.y() + laura.y0 - 13
    ctrl = [(0, 2), (3, -10), (6, -26), (3, -24), (1, -12), (2, 0), (8, 3), (14, -2),
            (18, -8), (22, -6), (21, 0), (25, -1), (28, -8), (30, -1), (34, -2), (36, -9), (38, -4), (39, 0),
            (44, -1), (47, -8), (50, -7), (49, 0), (53, -2),
            (58, -4), (62, -24), (66, -26), (74, -22), (72, -15), (64, -13), (70, -10), (80, 1),
            (86, 3), (70, 8), (40, 10), (15, 9), (30, 6), (70, 5), (118, 2)]
    ctrl = [(fx + 1.15 * x, fy + 1.0 * y) for x, y in ctrl]
    pts = []                                   # Catmull-Rom: un trazo continuo y suave por esos puntos
    ext = [ctrl[0]] + ctrl + [ctrl[-1]]
    for i in range(1, len(ext) - 2):
        p0, p1, p2, p3 = ext[i - 1], ext[i], ext[i + 1], ext[i + 2]
        for k in range(12):
            t = k / 12
            pts.append(tuple(0.5 * (2 * p1[j] + (-p0[j] + p2[j]) * t + (2 * p0[j] - 5 * p1[j] + 4 * p2[j] - p3[j]) * t * t
                                    + (-p0[j] + 3 * p1[j] - 3 * p2[j] + p3[j]) * t ** 3) for j in (0, 1)))
    firma = InkItem(QPointF(*pts[0]), QColor("#1E3A8A"), 1.3)
    for x, y in pts[1:]:
        firma.add_point(QPointF(x, y))
    c._register([firma])
    c.scene().clearSelection()
    esperar(0.4)
    foto("visor-3")
    # un sello propio «APROBADO» con la fecha
    comentarios_ui.poner_autor("Laura Restrepo", "Jefa de compras")
    sello = sellos.Sello(id=sellos.nuevo_id(), nombre="Aprobado", lineas=["APROBADO", "{fecha}"], forma="redondeado",
                         color="#1F7A4D", borde="doble", inclinacion=-8, ancho_pt=150)
    comentarios_ui.poner_sello(v, sello, 0, laura.x1 + 175, laura.y0 - 52)
    comentarios_ui.seleccionar(v, None, mostrar_panel=True)
    c.scene().clearSelection()
    v.set_tool("select")
    tranquilo(0.8)
    foto("visor-4")
    # para la escena: dónde quedó cada cosa en la captura (píxeles de la captura de 2480 × 1640)
    vp = c.viewport()
    escala = 2.0

    def en_captura(x, y):
        p = c.mapFromScene(QPointF(x, y))
        g = vp.mapTo(win, p)
        return [round(g.x() * escala), round(g.y() * escala)]
    DATOS["visor"].update({
        "resaltado": en_captura(o.x() + valor.x0, o.y() + valor.y0) + en_captura(o.x() + valor.x0 + 470, o.y() + fin_parrafo.y1),
        "cifra": en_captura(cx, cy),
        "firma": en_captura(fx, fy) + en_captura(fx + 136, fy),
        "sello": en_captura(o.x() + laura.x1 + 175, o.y() + laura.y0 - 52),
    })


# 4. Unir, dividir, organizar y comprimir
def organizar():
    ir("merge", CUATRO)
    foto("unir")
    ir("split", [D["Informe anual 2026"]], 2.0)
    foto("dividir")
    ir("organize", [D["Informe anual 2026"]], 2.0)
    foto("organizar")
    archivos = [D["Catálogo de cafés"], D["Informe anual 2026"], D["Propuesta comercial"]]
    p = ir("compress", archivos, 1.5)
    foto("comprimir")
    antes = os.path.getsize(D["Catálogo de cafés"])
    p.run()
    tranquilo(1.5, 180000)
    foto("comprimir-listo")
    hecho = Path(D["Catálogo de cafés"]).with_name("Catálogo de cafés_comprimido.pdf")
    if hecho.exists():
        DATOS["comprimir"] = {"antes": antes, "despues": os.path.getsize(hecho)}


# 5. OCR: el acta escaneada y la revisión del texto reconocido
def ocr():
    ir("ocr", [D["Acta escaneada"]], 2.0)
    foto("ocr")
    p = ir("ocrcheck", [D["Acta escaneada"]], 1.5)
    foto("ocr-revisar-0")
    p.b_reconocer.click()
    tranquilo(2.0, 300000)
    foto("ocr-revisar")


# 6. Convertir
def convertir():
    ir("pdf2office", [D["Informe anual 2026"]], 2.0)
    foto("word")
    ir("toimage", [D["Informe anual 2026"]], 2.0)
    foto("imagenes")
    ir("office2pdf", None, 1.0)
    foto("office")


# 7. Censurar la ficha del proveedor
def censurar():
    p = ir("redact", [D["Ficha de proveedor"]], 2.0)
    foto("censurar-0")
    p.buscar_ahora()
    tranquilo(1.5)
    foto("censurar-1")
    DATOS["censurar"] = {"hallazgos": len(p.hallazgos)}
    p.vista.set_despues(True)
    tranquilo(1.0)
    foto("censurar-2")


# 8. Y además…
def mas():
    for key, archivos in [("forms", None), ("protect", [D["Informe anual 2026"]]),
                          ("watermark", [D["Informe anual 2026"]]),
                          ("compare", [D["Contrato de servicios"], D["Propuesta comercial"]]),
                          ("numbers", [D["Informe anual 2026"]]), ("batch", CUATRO), ("stamps", None),
                          ("rename", CUATRO), ("sanitize", [D["Informe anual 2026"]])]:
        ir(key, archivos, 1.8)
        foto(f"mas-{key}")


# 9. Los íconos de las herramientas y el logo, dibujados por la propia app (para los rótulos del video)
def iconos():
    from ronypdf import art
    d = OUT / "iconos"
    d.mkdir(exist_ok=True)
    claves = ["home", "viewer", "forms", "print", "compare", "merge", "split", "organize", "rename", "compress",
              "batch", "ocr", "ocrcheck", "toimage", "fromimage", "extract", "office2pdf", "pdf2office",
              "watermark", "numbers", "metadata", "stamps", "protect", "unlock", "redact", "sanitize", "backup"]
    for k in claves:
        art.glyph_tile(k, 64, tema.categoria_de(k), False, 4.0).save(str(d / f"{k}.png"))
    art.brand_logo(64, 4.0).save(str(d / "logo.png"))
    print(f"  iconos: {len(claves) + 1}", flush=True)


for nombre, fn in [("iconos", iconos), ("inicio", inicio), ("paleta", paleta), ("visor", visor),
                   ("organizar", organizar), ("ocr", ocr), ("convertir", convertir), ("censurar", censurar),
                   ("mas", mas)]:
    if nombre not in SOLO:
        continue
    print(nombre, flush=True)
    paso(nombre, fn)

previos = json.loads((OUT / "datos.json").read_text("utf-8")) if (OUT / "datos.json").exists() else {}
(OUT / "datos.json").write_text(json.dumps({**previos, **DATOS}, ensure_ascii=False, indent=1), encoding="utf-8")
print("capturas:", len(hechas), "·", OUT)
shutil.rmtree(DEMO, ignore_errors=True)
