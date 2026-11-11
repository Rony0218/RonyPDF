"""Crea documentos de ejemplo (empresa ficticia «Café La Montaña») para las capturas y el video.

Uso: python scripts/demo_docs.py [CARPETA]      (por defecto build/demo)

Genera un informe anual con gráficos, un contrato para firmar, una propuesta, una factura, un catálogo con
fotos pesadas (para mostrar la compresión) y un acta escaneada sin texto (para mostrar el OCR).
Usa Segoe UI y Georgia si están en Windows; si no, las fuentes base de PDF.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pymupdf

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QBuffer, QIODevice, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QTransform  # noqa: E402

FONTS = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
FACES = {"body": "segoeui.ttf", "semi": "seguisb.ttf", "bold": "segoeuib.ttf", "serif": "georgiab.ttf",
         "type": "times.ttf"}
BASE14 = {"body": "helv", "semi": "hebo", "bold": "hebo", "serif": "tibo", "type": "tiro"}

INK = (0.12, 0.16, 0.22)
MUTED = (0.42, 0.45, 0.50)
LINE = (0.88, 0.89, 0.91)
GREEN = (0.13, 0.30, 0.22)
GREEN2 = (0.33, 0.55, 0.40)
CREAM = (0.98, 0.96, 0.92)
COPPER = (0.78, 0.45, 0.22)
W, H = pymupdf.paper_size("letter")


def rgb(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    return tuple(int(h[i: i + 2], 16) / 255 for i in (0, 2, 4))


class Pg:
    """Ayudas de dibujo sobre una página con las fuentes de la marca."""

    def __init__(self, doc: pymupdf.Document):
        self.p = doc.new_page(width=W, height=H)
        self._loaded: set[str] = set()

    def _font(self, face: str) -> str:
        """Incrusta la fuente solo en las páginas que la usan (después se reduce a los glifos usados)."""
        f = FONTS / FACES[face]
        if not f.exists():
            return BASE14[face]
        if face not in self._loaded:
            self.p.insert_font(fontname=face, fontfile=str(f))
            self._loaded.add(face)
        return face

    def text(self, x, y, s, size=11, face="body", color=INK):
        self.p.insert_text((x, y), s, fontsize=size, fontname=self._font(face), color=color)

    def para(self, rect, s, size=10.5, face="body", color=INK, align=0, lh=1.45):
        self.p.insert_textbox(pymupdf.Rect(rect), s, fontsize=size, fontname=self._font(face), color=color,
                              align=align, lineheight=lh)

    def rect(self, r, fill=None, color=None, width=0.8, radius=None):
        self.p.draw_rect(pymupdf.Rect(r), color=color, fill=fill, width=width if color else 0,
                         radius=radius)

    def line(self, a, b, color=LINE, width=0.8):
        self.p.draw_line(a, b, color=color, width=width)

    def circle(self, c, r, fill, opacity=1.0):
        self.p.draw_circle(c, r, color=None, fill=fill, fill_opacity=opacity)

    def header(self, title, n, total):
        self.text(54, 40, "CAFÉ LA MONTAÑA", 8, "semi", GREEN2)
        self.text(W - 54 - 60, 40, "Informe anual 2026", 8, "body", MUTED)
        self.line((54, 50), (W - 54, 50))
        self.text(54, 100, title, 24, "serif", INK)
        self.line((54, H - 48), (W - 54, H - 48))
        self.text(54, H - 32, "Café La Montaña S.A.S. · Documento de ejemplo", 8, "body", MUTED)
        self.text(W - 54 - 40, H - 32, f"{n} de {total}", 8, "body", MUTED)


def bar_chart(pg: Pg, x, y, w, h, labels, values, color=GREEN, unit=""):
    top = max(values) * 1.15
    for i in range(5):
        yy = y + h - h * i / 4
        pg.line((x, yy), (x + w, yy), LINE, 0.6)
        pg.text(x - 30, yy + 3, f"{top * i / 4:,.0f}".replace(",", "."), 7, "body", MUTED)
    bw = w / len(values) * 0.55
    for i, (lab, v) in enumerate(zip(labels, values)):
        cx = x + w / len(values) * (i + 0.5)
        bh = h * v / top
        pg.rect((cx - bw / 2, y + h - bh, cx + bw / 2, y + h), fill=color if i < len(values) - 1 else COPPER,
                radius=0.08)
        pg.text(cx - 14, y + h - bh - 6, f"{v:,.0f}{unit}".replace(",", "."), 8, "semi", INK)
        pg.text(cx - 8, y + h + 14, lab, 8, "body", MUTED)


def informe(path: Path) -> None:
    doc = pymupdf.open()
    total = 6
    # 1. Portada
    pg = Pg(doc)
    pg.rect((0, 0, W, H), fill=CREAM)
    pg.rect((0, 0, W, 470), fill=GREEN)
    for cx, cy, r, op in ((480, 120, 150, 0.12), (560, 330, 90, 0.10), (90, 420, 60, 0.08)):
        pg.circle((cx, cy), r, (1, 1, 1), op)
    pg.text(60, 90, "CAFÉ LA MONTAÑA", 11, "semi", (0.85, 0.92, 0.86))
    pg.text(60, 330, "Informe", 54, "serif", (1, 1, 1))
    pg.text(60, 392, "anual 2026", 54, "serif", (1, 1, 1))
    pg.text(60, 440, "Resultados, ventas y proyectos del año", 14, "body", (0.85, 0.92, 0.86))
    pg.text(60, 540, "Contenido", 12, "semi", INK)
    for i, s in enumerate(("Resumen del año", "Ventas por trimestre", "Producción por región",
                           "Proyectos 2027", "Estados financieros")):
        pg.text(60, 570 + i * 22, f"{i + 2:02d}", 10, "semi", COPPER)
        pg.text(90, 570 + i * 22, s, 11, "body", INK)
    pg.text(60, H - 50, "Documento de ejemplo creado para mostrar RonyPDF. Empresa y cifras ficticias.", 8,
            "body", MUTED)
    # 2. Resumen
    pg = Pg(doc)
    pg.header("Resumen del año", 2, total)
    pg.para((54, 120, W - 54, 250),
            "2026 fue el mejor año de nuestra historia. Las ventas crecieron un 18 % frente a 2025, sumamos "
            "doce fincas aliadas y abrimos dos mercados de exportación. La calidad de taza promedio subió a "
            "86,4 puntos y redujimos en un 22 % el consumo de agua en el beneficio.\n\nEste informe resume los "
            "resultados por trimestre, la producción de cada región y los proyectos que vienen.")
    kpis = (("$ 4.820 M", "Ventas del año"), ("+18 %", "Frente a 2025"), ("46", "Fincas aliadas"))
    for i, (v, lab) in enumerate(kpis):
        x = 54 + i * 172
        pg.rect((x, 280, x + 160, 370), fill=(0.96, 0.97, 0.96), color=LINE, radius=0.08)
        pg.text(x + 16, 325, v, 22, "serif", GREEN if i != 1 else COPPER)
        pg.text(x + 16, 350, lab, 9, "body", MUTED)
    pg.text(54, 420, "Lo más importante", 13, "semi", INK)
    for i, s in enumerate(("Exportamos por primera vez a Japón y Canadá.",
                           "Certificamos 30 fincas con sello de comercio justo.",
                           "Inauguramos la tienda y escuela de barismo en Medellín.",
                           "Bajamos 22 % el uso de agua con el nuevo beneficio ecológico.")):
        pg.circle((60, 446 + i * 26), 3, COPPER)
        pg.text(72, 450 + i * 26, s, 10.5, "body", INK)
    pg.rect((54, 570, W - 54, 690), fill=(0.97, 0.95, 0.91), radius=0.06)
    pg.para((74, 590, W - 74, 680), "«Crecimos sin dejar de lado lo que nos hace distintos: café de origen, "
            "comprado a precio justo y tostado con cuidado.»\n— Gerencia general", 12, "serif", GREEN, lh=1.5)
    # 3. Ventas
    pg = Pg(doc)
    pg.header("Ventas por trimestre", 3, total)
    pg.text(54, 130, "Millones de pesos", 9, "body", MUTED)
    bar_chart(pg, 90, 150, W - 160, 260, ["T1", "T2", "T3", "T4"], [1020, 1110, 1240, 1450])
    rows = [("Trimestre", "Ventas", "Variación", "Margen"), ("T1", "$ 1.020 M", "+9 %", "31 %"),
            ("T2", "$ 1.110 M", "+12 %", "32 %"), ("T3", "$ 1.240 M", "+19 %", "33 %"),
            ("T4", "$ 1.450 M", "+29 %", "35 %"), ("Total", "$ 4.820 M", "+18 %", "33 %")]
    y = 480
    for i, row in enumerate(rows):
        if i == 0:
            pg.rect((54, y - 16, W - 54, y + 8), fill=GREEN)
        elif i == len(rows) - 1:
            pg.rect((54, y - 16, W - 54, y + 8), fill=(0.96, 0.97, 0.96))
        for j, cell in enumerate(row):
            pg.text(70 + j * 125, y, cell, 10, "semi" if i in (0, len(rows) - 1) else "body",
                    (1, 1, 1) if i == 0 else INK)
        pg.line((54, y + 8), (W - 54, y + 8))
        y += 28
    # 4. Producción
    pg = Pg(doc)
    pg.header("Producción por región", 4, total)
    pg.text(54, 130, "Sacos de 70 kg", 9, "body", MUTED)
    regiones = (("Antioquia", 5200), ("Huila", 4300), ("Nariño", 3100), ("Cauca", 2600), ("Tolima", 1900),
                ("Santander", 1200))
    for i, (r, v) in enumerate(regiones):
        y = 160 + i * 44
        pg.text(54, y + 15, r, 10.5, "body", INK)
        pg.rect((150, y, W - 54, y + 22), fill=(0.95, 0.96, 0.95), radius=0.25)
        pg.rect((150, y, 150 + (W - 220) * v / 5200, y + 22), fill=GREEN if i else COPPER, radius=0.25)
        pg.text(160, y + 15, f"{v:,}".replace(",", "."), 9, "semi", (1, 1, 1))
    pg.para((54, 450, W - 54, 640), "Antioquia sigue siendo la región con más producción, pero Nariño y Huila "
            "tuvieron el mayor crecimiento gracias a las nuevas fincas aliadas. El 64 % del café se exporta y el "
            "36 % se vende en nuestras tiendas y en supermercados del país.")
    # 5. Proyectos
    pg = Pg(doc)
    pg.header("Proyectos 2027", 5, total)
    proyectos = (("Tostadora solar", "Una planta de tostión con energía solar en Jardín, Antioquia."),
                 ("Escuela de caficultores", "Formación para 200 jóvenes de las fincas aliadas."),
                 ("Café en cápsulas", "Línea compostable para tiendas y oficinas."),
                 ("Trazabilidad", "Código en cada empaque para conocer la finca de origen."))
    for i, (t, d) in enumerate(proyectos):
        y = 140 + i * 110
        pg.rect((54, y, W - 54, y + 92), fill=(0.97, 0.97, 0.96), color=LINE, radius=0.06)
        pg.circle((92, y + 46), 20, GREEN if i % 2 == 0 else COPPER)
        pg.text(86, y + 52, str(i + 1), 16, "serif", (1, 1, 1))
        pg.text(130, y + 40, t, 14, "semi", INK)
        pg.text(130, y + 62, d, 10.5, "body", MUTED)
    # 6. Estados financieros
    pg = Pg(doc)
    pg.header("Estados financieros", 6, total)
    filas = [("Concepto", "2025", "2026"), ("Ingresos operacionales", "4.085", "4.820"),
             ("Costo de ventas", "2.776", "3.230"), ("Utilidad bruta", "1.309", "1.590"),
             ("Gastos de administración", "512", "560"), ("Gastos de ventas", "388", "421"),
             ("Utilidad operacional", "409", "609"), ("Impuestos", "143", "213"), ("Utilidad neta", "266", "396")]
    y = 150
    for i, row in enumerate(filas):
        bold = i in (0, 3, 6, 8)
        if i == 0:
            pg.rect((54, y - 16, W - 54, y + 8), fill=GREEN)
        for j, cell in enumerate(row):
            x = 70 if j == 0 else 330 + (j - 1) * 120
            pg.text(x, y, cell, 10.5, "semi" if bold else "body", (1, 1, 1) if i == 0 else INK)
        pg.line((54, y + 8), (W - 54, y + 8))
        y += 30
    pg.text(54, y + 20, "Cifras en millones de pesos. Datos ficticios.", 9, "body", MUTED)
    doc.set_metadata({"title": "Informe anual 2026", "author": "Café La Montaña", "subject": "Documento de ejemplo"})
    doc.subset_fonts()
    doc.save(path, garbage=3, deflate=True)


def contrato(path: Path) -> None:
    doc = pymupdf.open()
    pg = Pg(doc)
    pg.text(54, 70, "CONTRATO DE PRESTACIÓN DE SERVICIOS", 16, "serif", INK)
    pg.text(54, 92, "N.º CLM-2026-118 · Medellín, 3 de noviembre de 2026", 9.5, "body", MUTED)
    pg.line((54, 104), (W - 54, 104))
    pg.para((54, 118, W - 54, 200),
            "Entre CAFÉ LA MONTAÑA S.A.S., en adelante EL CONTRATANTE, y ESTUDIO DIGITAL ANDINO, en adelante "
            "EL CONTRATISTA, se celebra el presente contrato, que se rige por las siguientes cláusulas:", 10.5)
    clausulas = (
        ("Primera. Objeto.", "EL CONTRATISTA diseñará y pondrá en marcha la tienda en línea de EL CONTRATANTE, "
         "con catálogo, pagos y envíos a todo el país."),
        ("Segunda. Plazo.", "El plazo de ejecución es de noventa (90) días calendario, contados desde la firma "
         "de este documento."),
        ("Tercera. Valor y forma de pago.", "El valor total es de $ 38.000.000, pagaderos así: 40 % a la firma, "
         "30 % con la entrega del diseño y 30 % con la puesta en marcha."),
        ("Cuarta. Obligaciones.", "EL CONTRATISTA entregará informes quincenales de avance y capacitará al equipo "
         "de EL CONTRATANTE en el manejo de la tienda."),
        ("Quinta. Confidencialidad.", "Las partes guardarán reserva sobre la información que conozcan con ocasión "
         "de este contrato."),
    )
    y = 205
    for t, d in clausulas:
        pg.text(54, y, t, 10.5, "semi", INK)
        pg.para((54, y + 6, W - 54, y + 60), d, 10.5)
        y += 74
    pg.para((54, y, W - 54, y + 40), "Para constancia, se firma en Medellín en dos ejemplares del mismo tenor.",
            10.5)
    for i, (who, name) in enumerate((("EL CONTRATANTE", "Laura Restrepo"), ("EL CONTRATISTA", "Andrés Gómez"))):
        x = 54 + i * 270
        pg.line((x, 690), (x + 220, 690), INK, 0.8)
        pg.text(x, 706, name, 10.5, "semi", INK)
        pg.text(x, 721, who, 9, "body", MUTED)
    pg.text(54, H - 32, "Documento de ejemplo creado para mostrar RonyPDF. Partes y cifras ficticias.", 8,
            "body", MUTED)
    doc.set_metadata({"title": "Contrato de servicios", "author": "Café La Montaña"})
    doc.subset_fonts()
    doc.save(path, garbage=3, deflate=True)


def propuesta(path: Path) -> None:
    doc = pymupdf.open()
    for n in range(1, 4):
        pg = Pg(doc)
        pg.rect((0, 0, W, 120), fill=COPPER if n == 1 else (0.97, 0.95, 0.91))
        pg.text(54, 80, "Propuesta comercial" if n == 1 else f"Propuesta comercial · {n}", 26, "serif",
                (1, 1, 1) if n == 1 else INK)
        pg.para((54, 160, W - 54, 400), "Café de origen para oficinas: suscripción mensual con entrega "
                "programada, molienda a la medida y capacitación para el equipo. Incluye máquina en comodato "
                "y reposición sin costo.", 12)
        for i in range(3):
            x = 54 + i * 172
            pg.rect((x, 420, x + 160, 560), fill=(0.97, 0.97, 0.96), color=LINE, radius=0.06)
            pg.text(x + 16, 460, ("Básico", "Equipo", "Empresa")[i], 14, "semi", INK)
            pg.text(x + 16, 500, ("$ 290.000", "$ 540.000", "$ 980.000")[i], 16, "serif", GREEN)
            pg.text(x + 16, 525, "al mes", 9, "body", MUTED)
    doc.subset_fonts()
    doc.save(path, garbage=3, deflate=True)


def factura(path: Path) -> None:
    doc = pymupdf.open()
    pg = Pg(doc)
    pg.text(54, 70, "Factura electrónica de venta", 18, "serif", INK)
    pg.text(54, 92, "FE-0142 · 28 de octubre de 2026", 10, "body", MUTED)
    pg.rect((W - 220, 50, W - 54, 100), fill=GREEN, radius=0.1)
    pg.text(W - 204, 82, "CAFÉ LA MONTAÑA", 11, "semi", (1, 1, 1))
    items = (("Café de origen Huila, 500 g", "24", "$ 32.000"), ("Café descafeinado, 250 g", "12", "$ 21.000"),
             ("Molienda para prensa francesa", "36", "$ 1.500"), ("Envío", "1", "$ 18.000"))
    y = 160
    for i, row in enumerate((("Producto", "Cantidad", "Valor unitario"),) + items):
        for j, cell in enumerate(row):
            pg.text(54 + (0, 300, 400)[j], y, cell, 10.5, "semi" if i == 0 else "body", INK)
        pg.line((54, y + 8), (W - 54, y + 8))
        y += 28
    pg.text(354, y + 30, "Total:  $ 1.092.000", 14, "semi", INK)
    doc.subset_fonts()
    doc.save(path, garbage=3, deflate=True)


def _photo(seed: int, w=2400, h=1600) -> bytes:
    """«Foto» sintética (degradado cálido con bokeh y grano), en JPEG de alta calidad: pesa como una real."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    base = np.array([[0.36, 0.22, 0.13], [0.62, 0.42, 0.25], [0.18, 0.30, 0.20], [0.80, 0.62, 0.40]])
    c1, c2 = base[seed % 4], base[(seed + 1) % 4]
    t = (xx / w * 0.6 + yy / h * 0.4)[..., None]
    img = c1 * (1 - t) + c2 * t
    for _ in range(14):  # luces desenfocadas
        cx, cy, r = rng.uniform(0, w), rng.uniform(0, h), rng.uniform(60, 260)
        d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
        img += (np.clip(1 - d / r, 0, 1) ** 1.6)[..., None] * rng.uniform(0.08, 0.25) * np.array([1, 0.9, 0.7])
    img += rng.normal(0, 0.035, img.shape)  # grano
    arr = (np.clip(img, 0, 1) * 255).astype(np.uint8)
    q = QImage(arr.data, w, h, w * 3, QImage.Format.Format_RGB888).copy()
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    q.save(buf, "JPG", 95)
    return bytes(buf.data())


def catalogo(path: Path) -> None:
    doc = pymupdf.open()
    nombres = ("Huila Reserva", "Nariño Altura", "Antioquia Clásico", "Cauca Floral", "Tolima Miel",
               "Santander Cacao", "Descafeinado Suave", "Edición Navidad")
    for i, n in enumerate(nombres):
        pg = Pg(doc)
        pg.p.insert_image(pymupdf.Rect(0, 0, W, 470), stream=_photo(i + 3))
        pg.text(54, 530, n, 26, "serif", INK)
        pg.para((54, 550, W - 54, 700), "Notas de chocolate, panela y frutos rojos. Tostión media. "
                "Ideal para métodos de filtrado y espresso.", 12, color=MUTED)
        pg.text(54, 720, f"$ {32 + i * 2}.000 · 500 g", 14, "semi", GREEN)
    doc.subset_fonts()
    doc.save(path, garbage=3, deflate=True)


def acta_escaneada(path: Path) -> None:
    """Acta escrita a máquina, «impresa y escaneada»: solo imagen, sin texto seleccionable."""
    src = pymupdf.open()
    pg = Pg(src)
    pg.text(72, 90, "ACTA DE REUNIÓN N.º 27", 15, "type", (0, 0, 0))
    pg.text(72, 112, "Comité de calidad · 14 de octubre de 2026", 11.5, "type", (0, 0, 0))
    pg.para((72, 140, W - 72, 700),
            "Asistentes: Laura Restrepo, Andrés Gómez, María Fernanda Ruiz y Carlos Mejía.\n\n"
            "1. Resultados de catación. El lote de Nariño obtuvo 87 puntos y se aprueba para exportación. El "
            "lote de Tolima requiere un nuevo secado antes de su venta.\n\n"
            "2. Tienda en línea. Se revisó la propuesta del contratista y se acordó iniciar en noviembre.\n\n"
            "3. Agua y beneficio. El nuevo beneficio ecológico redujo el consumo de agua en un 22 por ciento.\n\n"
            "Acuerdos: aprobar la exportación del lote de Nariño, firmar el contrato de la tienda en línea y "
            "presentar el informe anual en la próxima reunión.\n\n"
            "Se levanta la sesión a las 11:40 a. m.", 12, "type", (0, 0, 0), lh=1.5)
    pix = src[0].get_pixmap(dpi=200, colorspace=pymupdf.csGRAY)
    a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width).astype(np.float32)
    rng = np.random.default_rng(7)
    a = a * 0.93 + 8 + rng.normal(0, 6, a.shape)  # papel gris y algo de ruido
    a = np.clip(a, 0, 255).astype(np.uint8)
    q = QImage(a.data, pix.width, pix.height, pix.width, QImage.Format.Format_Grayscale8).copy()
    rot = q.convertToFormat(QImage.Format.Format_ARGB32).transformed(QTransform().rotate(0.6),
                                                                      Qt.TransformationMode.SmoothTransformation)
    q = QImage(rot.size(), QImage.Format.Format_RGB32)
    q.fill(QColor(236, 236, 234))  # las esquinas que deja el giro quedan color papel, no negras
    p = QPainter(q)
    p.drawImage(0, 0, rot)
    p.end()
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    q.save(buf, "JPG", 80)
    out = pymupdf.open()
    page = out.new_page(width=W, height=H)
    page.insert_image(page.rect, stream=bytes(buf.data()))
    out.save(path, garbage=3, deflate=True)


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "build" / "demo")
    out.mkdir(parents=True, exist_ok=True)
    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841
    informe(out / "Informe anual 2026.pdf")
    contrato(out / "Contrato de servicios.pdf")
    propuesta(out / "Propuesta comercial.pdf")
    factura(out / "Factura FE-0142.pdf")
    catalogo(out / "Catálogo de cafés.pdf")
    acta_escaneada(out / "Acta escaneada.pdf")
    for f in sorted(out.glob("*.pdf")):
        print(f"{f.stat().st_size / 1e6:6.1f} MB  {f.name}")


if __name__ == "__main__":
    main()
