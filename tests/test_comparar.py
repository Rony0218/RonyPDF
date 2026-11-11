"""Comparar dos PDF: texto que sigue al texto aunque cambie de página, páginas agregadas, quitadas y
movidas, apariencia, escaneos, contraseñas, límites, informes y la pantalla (sin pantalla real)."""

import hashlib
import io
import os
import tempfile
import time
import zipfile
from pathlib import Path

import pymupdf
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Los ajustes de las pruebas nunca deben tocar los reales.
os.environ.setdefault("RONYPDF_SETTINGS", os.path.join(tempfile.mkdtemp(prefix="ronypdf-comparar-"), "RonyPDF.json"))

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QToolButton  # noqa: E402

from ronypdf import comparar, comparar_ui, core, oficina, widgets  # noqa: E402
from ronypdf.comparar import Opciones  # noqa: E402
from ronypdf.gui import MainWindow, _light_palette  # noqa: E402
from ronypdf.style import QSS  # noqa: E402
from test_oficina import _procesos_office, _sin_procesos_nuevos, con_excel, con_word  # noqa: E402

SHOTS = os.environ.get("RONYPDF_SHOTS")
CON_OCR = pytest.mark.skipif("spa" not in core.ocr_languages(), reason="Falta el modelo de OCR en español")


# ---------------------------------------------------------------- utilidades

def _hash(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _pdf(path, paginas, size=(595, 842)):
    """``paginas``: lista de listas de (x, y, texto, tamaño)."""
    with pymupdf.open() as d:
        for renglones in paginas:
            p = d.new_page(width=size[0], height=size[1])
            for x, y, t, s in renglones:
                p.insert_text((x, y), t, fontsize=s)
        d.save(path)
    return path


_FRASES = ["El contrato de prestación de servicios se firma entre las partes", "con el fin de establecer las "
           "condiciones generales de la relación comercial", "y las obligaciones de cada una durante la vigencia "
           "del acuerdo", "que podrá renovarse por periodos iguales", "si ninguna de las partes manifiesta lo "
           "contrario por escrito", "con un mes de anticipación al vencimiento"]


def _parrafo(i: int, palabras: int = 60) -> str:
    """Párrafo distinto para cada i (así ningún renglón se repite tal cual)."""
    base = " ".join(_FRASES[(i + k) % len(_FRASES)] for k in range(8)).split()
    return f"Cláusula {i + 1}. " + " ".join(base[:palabras]) + f" Referencia {i * 7 + 3}."


def _flujo(path, parrafos, pie=True, encabezado="Contrato de servicios - Borrador"):
    """PDF de texto corrido (como el de un procesador de textos): cada párrafo se acomoda en renglones y
    pasa a la página siguiente cuando no cabe; con encabezado y «Página N de M»."""
    html = "".join(f"<p style='margin-bottom:8pt'>{p}</p>" for p in parrafos)
    buf = io.BytesIO()
    w = pymupdf.DocumentWriter(buf)
    story = pymupdf.Story(html=html, user_css="p{font-family:sans-serif;font-size:11pt;}")
    mas = True
    while mas:
        dev = w.begin_page(pymupdf.paper_rect("letter"))
        mas, _ = story.place(pymupdf.Rect(72, 72, 540, 720))
        story.draw(dev)
        w.end_page()
    w.close()
    with pymupdf.open("pdf", buf.getvalue()) as d:
        if pie:
            for k, p in enumerate(d):
                p.insert_text((260, 760), f"Página {k + 1} de {d.page_count}", fontsize=9)
                p.insert_text((72, 40), encabezado, fontsize=9)
        d.save(path)
        return d.page_count


def _foto(semilla=0, dpi=100) -> bytes:
    with pymupdf.open() as d:
        p = d.new_page(width=400, height=300)
        for k in range(30):
            c = (((k + semilla) * 37) % 255 / 255, ((k + semilla) * 91) % 255 / 255, ((k + semilla) * 53) % 255 / 255)
            p.draw_circle((20 + k * 13, 40 + (k * 29) % 220), 25 + k % 7 * 6, color=c, fill=c)
        return p.get_pixmap(dpi=dpi).tobytes("png")


def _informe(path, fotos=(0, 0, 0, 0, 0), extra=None):
    """Como el «informe» de conftest: un título por página y una foto (``fotos`` elige la de cada página)."""
    cache = {}
    with pymupdf.open() as d:
        for i, s in enumerate(fotos):
            p = d.new_page(width=595, height=842)
            p.insert_text((72, 100), f"Informe - Página {i + 1}", fontsize=28)
            if s not in cache:
                cache[s] = _foto(s)
            p.insert_image(pymupdf.Rect(72, 150, 523, 488), stream=cache[s])
            if extra:
                extra(i, p)
        d.save(path)
    return path


def _tipos(res):
    return [c.tipo for c in res.cambios]


def _escaneo(path, renglones, sello=None):
    """PDF escaneado de dos páginas (la segunda apaisada con /Rotate 90, como el fixture «scanned»)."""
    src = pymupdf.open()
    p = src.new_page(width=595, height=842)
    y = 120
    for t, s in renglones:
        p.insert_text((72, y), t, fontsize=s)
        y += s * 2
    pix = p.get_pixmap(dpi=200)
    out = pymupdf.open()
    q = out.new_page(width=595, height=842)
    q.insert_image(q.rect, pixmap=pix)
    q = out.new_page(width=842, height=595)
    q.insert_image(q.rect, pixmap=pix, rotate=90)
    q.set_rotation(90)
    if sello:
        _sellar(out[0], sello)
    out.save(path)
    return path


def _sellar(page, texto="RECIBIDO\n10/11/2026"):
    r = pymupdf.Rect(380, 700, 540, 760)
    page.draw_rect(r, color=(0.8, 0, 0), width=1.5)
    page.insert_textbox(r + (4, 8, -4, -4), texto, fontsize=12, color=(0.8, 0, 0), align=1)


# ---------------------------------------------------------------- texto

def test_palabra_cambiada_agregada_y_quitada(tmp_path):
    base = [(72, 100, "El plazo del contrato vence en 2024 sin excepciones.", 12),
            (72, 130, "Las partes firman de mutuo acuerdo en Bogotá.", 12),
            (72, 160, "El pago se hará de forma mensual y anticipada en pesos.", 12)]
    nuevo = [(72, 100, "El plazo del contrato vence en 2025 sin excepciones.", 12),
             (72, 130, "Las partes firman de mutuo y libre acuerdo en Bogotá.", 12),
             (72, 160, "El pago se hará de forma mensual en pesos.", 12)]
    a = _pdf(tmp_path / "a.pdf", [base])
    b = _pdf(tmp_path / "b.pdf", [nuevo])
    res = comparar.comparar(a, b)
    assert [(c.tipo, c.antes, c.despues) for c in res.cambios] == [
        ("cambiado", "2024", "2025"), ("agregado", "", "y libre"), ("quitado", "y anticipada", "")]
    c = res.cambios[0]
    assert c.id == 1 and c.pagina_a == 0 and c.pagina_b == 0
    with pymupdf.open(b) as d:
        objetivo = d[0].search_for("2025")[0]
    r = c.rects_b[0]
    assert abs(r[0] - objetivo.x0) < 1 and abs(r[2] - objetivo.x1) < 1 and abs(r[1] - objetivo.y0) < 2
    assert "vence en" in c.contexto and "sin excepciones." in c.contexto
    agregado, quitado = res.cambios[1], res.cambios[2]
    assert agregado.marca_a is not None and not agregado.rects_a and agregado.pagina_a == 0
    with pymupdf.open(a) as d:
        mutuo = d[0].search_for("mutuo")[0]
        y_ant = d[0].search_for("anticipada")[0]
    assert abs(agregado.marca_a[0] - mutuo.x1) < 3                   # la marca va justo después de «mutuo»
    assert quitado.rects_a[0][2] >= y_ant.x1 - 1 and quitado.marca_b is not None
    assert "forma mensual" in quitado.contexto
    assert res.texto_resumen() == "3 cambios: 1 agregado · 1 quitado · 1 cambiado"


def test_reflujo_parrafo_insertado_no_crea_falsos_cambios(tmp_path):
    ps = [_parrafo(i) for i in range(34)]
    na = _flujo(tmp_path / "a.pdf", ps)
    nuevo = list(ps)
    insertado = ("Cláusula nueva de confidencialidad. " + " ".join(_parrafo(99).split()[2:])
                 + " " + " ".join(_parrafo(98).split()[2:]))
    nuevo.insert(1, insertado)
    nb = _flujo(tmp_path / "b.pdf", nuevo)
    res = comparar.comparar(tmp_path / "a.pdf", tmp_path / "b.pdf")
    assert _tipos(res) == ["agregado"], [comparar.texto_lista(c) for c in res.cambios]
    c = res.cambios[0]
    assert c.despues.startswith("Cláusula nueva de confidencialidad.") and c.pagina_b == 0
    assert len(c.rects_b) >= 8                # un rectángulo por renglón
    # el resto del texto se corrió de página (y se renumeraron las páginas) sin falsos cambios; si quedó
    # una página de más, solo recibió texto corrido: no es una «página agregada»
    assert res.resumen["pagina_agregada"] == 0
    if nb > na:
        assert (None, nb - 1) in res.pares
    # palabra insertada que reacomoda todo un párrafo: solo esa palabra
    otro = list(ps)
    otro[3] = otro[3].replace("relación comercial", "relación comercial y laboral", 1)
    _flujo(tmp_path / "c.pdf", otro)
    res = comparar.comparar(tmp_path / "a.pdf", tmp_path / "c.pdf")
    assert [(c.tipo, c.despues) for c in res.cambios] == [("agregado", "y laboral")]


def _docx_contrato(path, version: int):
    P = oficina.Parrafo
    parrafos = [P("Contrato de prestación de servicios", "titulo1"),
                P("El plazo del contrato vence el 31 de diciembre de "
                  f"{'2024' if version == 1 else '2025'} según lo acordado por las partes."),
                P(_parrafo(50, 40)), P(_parrafo(51, 40))]
    cuerpo = [_parrafo(i, 62) for i in range(12)]
    cuerpo[10] = cuerpo[10].replace("Referencia", "Esta cláusula aplica sin perjuicio de las garantías legales "
                                                  "en todos los casos. Referencia")
    if version == 2:
        cuerpo.insert(2, "Las partes acuerdan además una cláusula nueva de confidencialidad sobre toda la "
                         "información que reciban durante la ejecución del contrato.")
        cuerpo[11] = cuerpo[11].replace(" sin perjuicio de las garantías legales", "")
    parrafos += [P(t, salto_pagina=(k == 0)) for k, t in enumerate(cuerpo)]
    oficina.escribir_docx(parrafos, path)
    return path


@con_word
def test_word_real_tres_cambios_exactos(tmp_path):
    """Dos versiones hechas con Word: palabra cambiada (pág. 1), párrafo nuevo en la página 2 que corre el
    texto a la 3, y una frase quitada (pág. 3).  Deben salir esos tres cambios y nada más."""
    antes = _procesos_office()
    a, b = tmp_path / "v1.pdf", tmp_path / "v2.pdf"
    assert oficina.a_pdf(_docx_contrato(tmp_path / "v1.docx", 1), a) == 3
    oficina.a_pdf(_docx_contrato(tmp_path / "v2.docx", 2), b)
    _sin_procesos_nuevos(antes)
    copia_a, copia_b = _hash(a), _hash(b)
    res = comparar.comparar(a, b)
    lista = [comparar.texto_lista(c) for c in res.cambios]
    assert sorted(_tipos(res)) == ["agregado", "cambiado", "quitado"], lista
    cambio = next(c for c in res.cambios if c.tipo == "cambiado")
    assert (cambio.antes, cambio.despues, cambio.pagina_a, cambio.pagina_b) == ("2024", "2025", 0, 0)
    nuevo = next(c for c in res.cambios if c.tipo == "agregado")
    assert nuevo.despues.startswith("Las partes acuerdan además") and nuevo.pagina_b == 1
    quitado = next(c for c in res.cambios if c.tipo == "quitado")
    assert quitado.antes == "sin perjuicio de las garantías legales" and quitado.pagina_a == 2
    assert res.pares == [(0, 0), (1, 1), (2, 2)]
    assert (_hash(a), _hash(b)) == (copia_a, copia_b)           # los originales no se tocan


@con_excel
def test_excel_real_un_valor_cambiado(tmp_path):
    antes = _procesos_office()
    filas = [["Concepto", "Valor", "Responsable"], ["Talleres de niñez", 1250000, "Ana"],
             ["Refrigerios", 800000, "Luis"], ["Transporte", 450000, "Marta"]]
    oficina.escribir_xlsx([("Presupuesto", filas)], tmp_path / "v1.xlsx")
    filas[2][1] = 850000
    oficina.escribir_xlsx([("Presupuesto", filas)], tmp_path / "v2.xlsx")
    oficina.a_pdf(tmp_path / "v1.xlsx", tmp_path / "v1.pdf")
    oficina.a_pdf(tmp_path / "v2.xlsx", tmp_path / "v2.pdf")
    _sin_procesos_nuevos(antes)
    res = comparar.comparar(tmp_path / "v1.pdf", tmp_path / "v2.pdf")
    assert _tipos(res) == ["cambiado"], [comparar.texto_lista(c) for c in res.cambios]
    c = res.cambios[0]
    assert c.antes.replace(".", "").replace(",", "").replace("$", "").strip().startswith("800000")
    assert c.despues.replace(".", "").replace(",", "").replace("$", "").strip().startswith("850000")


# ---------------------------------------------------------------- páginas

def test_paginas_quitada_agregada_y_movida(samples, tmp_path):
    a = str(samples["a"])                       # 5 páginas: título y foto
    core.extract_pages(a, "1,3-5", tmp_path / "quitada.pdf")
    res = comparar.comparar(a, tmp_path / "quitada.pdf")
    assert _tipos(res) == ["pagina_quitada"] and res.cambios[0].pagina_a == 1
    assert res.pares == [(0, 0), (1, None), (2, 1), (3, 2), (4, 3)]
    assert (res.paginas_a, res.paginas_b) == (5, 4)

    nueva = _pdf(tmp_path / "nueva.pdf", [[(72, 300, "Anexo técnico con el cronograma de actividades", 16)]])
    core.build_document([(a, None), (str(nueva), None)],
                        [core.PageRef(0, 0), core.PageRef(1, 0)] + [core.PageRef(0, k) for k in range(1, 5)],
                        tmp_path / "agregada.pdf")
    res = comparar.comparar(a, tmp_path / "agregada.pdf")
    assert _tipos(res) == ["pagina_agregada"] and res.cambios[0].pagina_b == 1
    assert res.pares == [(0, 0), (None, 1), (1, 2), (2, 3), (3, 4), (4, 5)]
    assert res.cambios[0].rects_b and not res.cambios[0].rects_a

    core.build_document([(a, None)], [core.PageRef(0, k) for k in (0, 2, 3, 1, 4)], tmp_path / "movida.pdf")
    res = comparar.comparar(a, tmp_path / "movida.pdf")
    assert _tipos(res) == ["pagina_movida"]
    c = res.cambios[0]
    assert (c.pagina_a, c.pagina_b) == (1, 3) and comparar.detalle(c) == "De la página 2 a la página 4"
    assert res.pares == [(0, 0), (1, None), (2, 1), (3, 2), (None, 3), (4, 4)]


def test_paginas_con_texto_corrido_y_distinto_numero_de_paginas(tmp_path):
    ps = [_parrafo(i) for i in range(24)]
    _flujo(tmp_path / "a.pdf", ps)
    menos = ps[:8] + ps[16:]                   # se quitan ocho párrafos (casi una página entera)
    nb = _flujo(tmp_path / "b.pdf", menos)
    res = comparar.comparar(tmp_path / "a.pdf", tmp_path / "b.pdf")
    assert res.paginas_b == nb and res.paginas_a > nb
    assert res.cambios and {c.tipo for c in res.cambios} <= {"quitado", "pagina_quitada"}
    texto = " ".join(c.antes for c in res.cambios if c.tipo == "quitado")
    assert "Cláusula 17." not in texto and "Cláusula 8." not in texto
    if "pagina_quitada" not in _tipos(res):
        assert "Cláusula 9." in texto and "Cláusula 16." in texto
    assert len(res.pares) >= res.paginas_a
    assert [a for a, _b in res.pares if a is not None] == list(range(res.paginas_a))
    assert [b for _a, b in res.pares if b is not None] == list(range(res.paginas_b))


def test_ignorar_encabezados_y_mayusculas(tmp_path):
    ps = [_parrafo(i) for i in range(30)]
    _flujo(tmp_path / "a.pdf", ps)
    _flujo(tmp_path / "b.pdf", ps, encabezado="Contrato de servicios - Versión final")
    res = comparar.comparar(tmp_path / "a.pdf", tmp_path / "b.pdf")
    assert res.cambios and all(c.zona == "margen" for c in res.cambios)
    assert {(c.antes, c.despues) for c in res.cambios} == {("Borrador", "Versión final")}
    res = comparar.comparar(tmp_path / "a.pdf", tmp_path / "b.pdf", Opciones(ignorar_encabezados=True))
    assert res.cambios == [] and res.texto_resumen() == comparar.SIN_DIFERENCIAS
    # «Página N de M» que cambia porque se agregaron páginas no cuenta, aunque no se ignoren
    nuevo = ps[:2] + [_parrafo(70, 200)] + ps[2:]
    _flujo(tmp_path / "c.pdf", nuevo)
    res = comparar.comparar(tmp_path / "a.pdf", tmp_path / "c.pdf")
    assert _tipos(res) == ["agregado"]
    # mayúsculas
    _flujo(tmp_path / "d.pdf", [p.replace("contrato", "CONTRATO") for p in ps])
    res = comparar.comparar(tmp_path / "a.pdf", tmp_path / "d.pdf")
    assert res.cambios and all((c.antes, c.despues) == ("contrato", "CONTRATO") for c in res.cambios)
    res = comparar.comparar(tmp_path / "a.pdf", tmp_path / "d.pdf", Opciones(ignorar_mayusculas=True))
    assert res.cambios == []


# ---------------------------------------------------------------- apariencia

def test_apariencia_imagen_rectangulo_e_iguales(tmp_path):
    a = _informe(tmp_path / "a.pdf")
    b = _informe(tmp_path / "b.pdf", fotos=(0, 0, 7, 0, 0),
                 extra=lambda i, p: i == 3 and p.draw_rect(pymupdf.Rect(300, 600, 450, 700), color=(1, 0, 0),
                                                           fill=(1, 0.8, 0.8)))
    res = comparar.comparar(a, b)
    assert _tipos(res) == ["apariencia", "apariencia"]
    imagen, rect = res.cambios
    assert (imagen.pagina_a, imagen.pagina_b, rect.pagina_b) == (2, 2, 3)
    r = imagen.rects_b[0]
    assert r[0] < 200 and r[2] > 400 and r[1] < 250 and r[3] > 400          # sobre la foto
    r = rect.rects_b[0]
    assert 280 <= r[0] <= 302 and 445 <= r[2] <= 470 and 585 <= r[1] <= 602 and 698 <= r[3] <= 720
    assert imagen.descripcion == comparar.DESC_IMAGEN
    assert comparar.comparar(a, _informe(tmp_path / "c.pdf")).cambios == []        # idénticos (otro archivo)


def test_apariencia_tolerancia_tamano_y_texto_ya_encontrado(tmp_path):
    def con_gris(path, g, texto="Total aprobado: 1.250.000"):
        with pymupdf.open() as d:
            p = d.new_page(width=595, height=842)
            p.insert_text((72, 100), texto, fontsize=14)
            p.draw_rect(pymupdf.Rect(72, 300, 300, 400), color=None, fill=(g, g, g))
            d.save(path)
        return path
    a = con_gris(tmp_path / "a.pdf", 0.50)
    b = con_gris(tmp_path / "b.pdf", 0.52)     # unos 5 niveles de gris: por debajo de la tolerancia
    assert comparar.comparar(a, b).cambios == []
    res = comparar.comparar(a, b, Opciones(tolerancia=2))
    assert _tipos(res) == ["apariencia"]
    # el texto cambiado no se repite como apariencia
    c = con_gris(tmp_path / "c.pdf", 0.50, "Total aprobado: 1.900.000")
    res = comparar.comparar(a, c)
    assert _tipos(res) == ["cambiado"]
    # tamaños distintos: aviso y se compara la zona común
    with pymupdf.open() as d:
        p = d.new_page(width=612, height=792)
        p.insert_text((72, 100), "Total aprobado: 1.250.000", fontsize=14)
        p.draw_rect(pymupdf.Rect(72, 300, 300, 400), color=None, fill=(0.5, 0.5, 0.5))
        d.save(tmp_path / "carta.pdf")
    res = comparar.comparar(a, tmp_path / "carta.pdf")
    assert res.cambios == [] and any("otro tamaño" in av for av in res.avisos)


def test_paginas_giradas(samples, tmp_path):
    b = str(samples["b"])                      # «anexo»: la página 2 tiene /Rotate 90
    assert comparar.comparar(b, b).cambios == []
    with pymupdf.open(b) as d:
        d[1].insert_text((72, 200), "Nota agregada", fontsize=14)
        d.save(tmp_path / "girada.pdf")
    res = comparar.comparar(b, tmp_path / "girada.pdf")
    assert _tipos(res) == ["agregado"] and res.cambios[0].pagina_b == 1
    w, h = res.tamanos_b[1]
    assert w > h                                # tal como se ve: apaisada
    r = res.cambios[0].rects_b[0]
    assert 0 <= r[0] < r[2] <= w and 0 <= r[1] < r[3] <= h
    assert r[3] - r[1] > r[2] - r[0]            # el texto queda vertical en la página girada


# ---------------------------------------------------------------- escaneos

def test_escaneo_frente_a_copia_con_sello_digital(scanned, tmp_path):
    with pymupdf.open(scanned) as d:
        _sellar(d[0])
        d.save(tmp_path / "sellado.pdf")
    res = comparar.comparar(scanned, tmp_path / "sellado.pdf")
    assert _tipos(res) == ["apariencia"]
    c = res.cambios[0]
    r = c.rects_b[0]
    assert r[0] <= 380 and r[2] >= 540 and r[1] <= 700 and r[3] >= 760          # la zona del sello
    assert c.descripcion == "Sello o texto agregado sobre la imagen: «RECIBIDO 10/11/2026»"
    assert res.pares == [(0, 0), (1, 1)]
    aviso = res.avisos[0]
    assert "escaneadas: solo se comparó su imagen" in aviso and "Leer con OCR las páginas escaneadas" in aviso
    assert res.escaneadas_a == [0, 1] and res.escaneadas_b == [0, 1]


def test_escaneo_aviso_de_una_pagina(tmp_path):
    a = tmp_path / "a.pdf"
    with pymupdf.open() as d:
        src = pymupdf.open()
        q = src.new_page()
        q.insert_text((72, 120), "Acta de reunión del comité", fontsize=20)
        p = d.new_page()
        p.insert_image(p.rect, pixmap=q.get_pixmap(dpi=150))
        p = d.new_page()
        p.insert_text((72, 120), "Segunda página con texto digital suficiente para no ser escaneada.", fontsize=12)
        d.save(a)
    res = comparar.comparar(a, a)
    assert res.avisos[0].startswith("La página 1 está escaneada: solo se comparó su imagen. Marca «Leer con OCR")


@CON_OCR
def test_escaneo_con_ocr_encuentra_la_palabra_cambiada(tmp_path):
    a = _escaneo(tmp_path / "a.pdf", [("Cooperativa de caficultores", 26), ("Informe de gestión: niñez y educación", 18)])
    b = _escaneo(tmp_path / "b.pdf", [("Cooperativa de caficultores", 26),
                                      ("Informe de resultados: niñez y educación", 18)])
    sin = comparar.comparar(a, b)
    assert set(_tipos(sin)) == {"apariencia"} and sin.avisos
    res = comparar.comparar(a, b, Opciones(usar_ocr=True))
    assert [(c.tipo, c.antes, c.despues) for c in res.cambios] == [("cambiado", "gestión:", "resultados:")] * 2
    assert not any("escaneada" in av for av in res.avisos)
    r = res.cambios[1].rects_b[0]              # página girada: coordenadas tal como se ve
    assert 150 < r[0] < 175 and 150 < r[1] < 170


# ---------------------------------------------------------------- contraseñas y archivos dañados

def test_protegido_frente_a_su_copia_sin_clave(protegido, tmp_path):
    abierto = tmp_path / "abierto.pdf"
    core.unlock(protegido.path, abierto, protegido.password)
    antes = (_hash(protegido.path), _hash(abierto))
    res = comparar.comparar(protegido.path, abierto, password_a=protegido.password)
    assert res.cambios == [] and res.texto_resumen() == comparar.SIN_DIFERENCIAS
    assert (_hash(protegido.path), _hash(abierto)) == antes
    protegido.comprobar(protegido.path)
    with pytest.raises(core.PasswordError):
        comparar.comparar(protegido.path, abierto)


def test_pdf_danado_nombra_el_archivo(samples, tmp_path):
    roto = tmp_path / "contrato roto.pdf"
    roto.write_bytes(b"%PDF-1.7\nesto no es un PDF de verdad" * 10)
    with pytest.raises(core.UserError, match="contrato roto.pdf"):
        comparar.comparar(samples["a"], roto)
    with pytest.raises(core.UserError):
        comparar.comparar(samples["a"], samples["a"], Opciones(texto=False, apariencia=False))


def test_documentos_sin_texto(tmp_path):
    def solo_fotos(path, semillas):
        with pymupdf.open() as d:
            for s in semillas:
                p = d.new_page(width=595, height=842)
                p.insert_image(pymupdf.Rect(72, 150, 523, 488), stream=_foto(s))
            d.save(path)
        return path
    a = solo_fotos(tmp_path / "a.pdf", (0, 3, 5))
    assert comparar.comparar(a, a).cambios == []
    res = comparar.comparar(a, solo_fotos(tmp_path / "b.pdf", (0, 9, 5)))
    assert _tipos(res) == ["apariencia"] and res.cambios[0].pagina_b == 1
    assert res.pares == [(0, 0), (1, 1), (2, 2)] and not res.avisos


# ---------------------------------------------------------------- límites y cancelación

def test_documentos_totalmente_distintos_terminan_pronto(tmp_path):
    import random
    random.seed(4)
    voc = "alfa beta gama delta épsilon zeta eta theta iota kappa lambda mu nu xi ómicron pi rho sigma".split()
    _flujo(tmp_path / "a.pdf", [_parrafo(i) for i in range(400)])
    _flujo(tmp_path / "b.pdf", [" ".join(random.choice(voc) for _ in range(40)) for _ in range(380)])
    inicio = time.perf_counter()
    res = comparar.comparar(tmp_path / "a.pdf", tmp_path / "b.pdf")
    assert time.perf_counter() - inicio < 20
    assert comparar.AVISO_DISTINTOS in res.avisos and res.cambios


def test_bloque_de_mas_de_5000_palabras(tmp_path):
    import random
    random.seed(5)
    voc = [f"palabra{k}" for k in range(300)]
    comun = [_parrafo(i) for i in range(10)]
    bloque_a = [" ".join(random.choice(voc) for _ in range(60)) for _ in range(100)]       # 6000 palabras
    voc_b = [f"termino{k}" for k in range(300)]
    bloque_b = [" ".join(random.choice(voc_b) for _ in range(60)) for _ in range(100)]
    _flujo(tmp_path / "a.pdf", comun + bloque_a + comun[:3], pie=False)
    _flujo(tmp_path / "b.pdf", comun + bloque_b + comun[:3], pie=False)
    comp = comparar._Comparador()
    a = " ".join(bloque_a).split()
    b = " ".join(bloque_b).split()
    assert len(a) > comparar.LIMITE_PALABRAS
    inicio = time.perf_counter()
    ops = comp.opcodes(a, b)
    assert time.perf_counter() - inicio < 2 and ops == [("replace", 0, len(a), 0, len(b), True)]
    assert comp.por_bloques
    inicio = time.perf_counter()
    res = comparar.comparar(tmp_path / "a.pdf", tmp_path / "b.pdf")
    assert time.perf_counter() - inicio < 15
    assert comparar.AVISO_DISTINTOS in res.avisos
    assert all("Cláusula 3." not in c.antes for c in res.cambios)      # lo igual sigue siendo igual


def test_bloque_grande_parecido_se_compara_al_detalle():
    """Más de 5000 palabras casi iguales (texto corrido sin cortes de renglón comunes): se parte por anclas y
    sale solo lo distinto, sin volverse cuadrático."""
    import random
    random.seed(6)
    a = [f"w{random.randint(0, 3000)}" for _ in range(20000)]
    b = list(a)
    b[100:100] = ["insertada"]
    b[15000] = "cambiada"
    comp = comparar._Comparador()
    inicio = time.perf_counter()
    ops = [op for op in comp.opcodes(a, b) if op[0] != "equal"]
    assert time.perf_counter() - inicio < 5
    assert [(op[0], b[op[3]:op[4]]) for op in ops] == [("insert", ["insertada"]), ("replace", ["cambiada"])]
    assert not comp.por_bloques


def test_cancelar_a_mitad(tmp_path):
    _flujo(tmp_path / "a.pdf", [_parrafo(i) for i in range(200)])
    _flujo(tmp_path / "b.pdf", [_parrafo(i) for i in range(200)][::-1])
    llamadas = []

    def progreso(i, n, msg):
        llamadas.append(msg)
        if len(llamadas) > 5:
            raise widgets.Cancelled()

    with pytest.raises(widgets.Cancelled):
        comparar.comparar(tmp_path / "a.pdf", tmp_path / "b.pdf", progress=progreso)
    assert llamadas[0].startswith("Leyendo el texto")
    fases = []
    comparar.comparar(tmp_path / "a.pdf", tmp_path / "a.pdf", progress=lambda i, n, m: fases.append(m))
    assert any(m.startswith("Comparando el texto") for m in fases)
    assert any(m.startswith("Comparando la apariencia: página 3 de") for m in fases)


def test_trescientas_paginas(tmp_path):
    """Dos documentos de unas 300 páginas con cinco cambios (texto y apariencia), e idénticos."""
    ps = [_parrafo(i, 30 + i % 20) for i in range(4000)]
    na = _flujo(tmp_path / "a.pdf", ps)
    assert na >= 300
    nuevo = list(ps)
    nuevo[10] = nuevo[10].replace("Referencia 73.", "Referencia 7300.")
    nuevo.insert(1500, "Párrafo nuevo con una obligación adicional para las partes.")
    nuevo[2201] = nuevo[2201].replace(f" Referencia {2200 * 7 + 3}.", "")
    nuevo[3501] = nuevo[3501] + " Texto agregado al final de la cláusula."
    _flujo(tmp_path / "b.pdf", nuevo)
    with pymupdf.open(tmp_path / "b.pdf") as d:          # un recuadro en el margen inferior de la pág. 251
        d[250].draw_rect(pymupdf.Rect(400, 724, 520, 750), color=(0, 0, 1), fill=(0.8, 0.8, 1))
        d.saveIncr()
    inicio = time.perf_counter()
    res = comparar.comparar(tmp_path / "a.pdf", tmp_path / "b.pdf")
    con_cambios = time.perf_counter() - inicio
    assert sorted(_tipos(res)) == ["agregado", "agregado", "apariencia", "cambiado", "quitado"], \
        [comparar.texto_lista(c) for c in res.cambios]
    inicio = time.perf_counter()
    assert comparar.comparar(tmp_path / "a.pdf", tmp_path / "a.pdf").cambios == []
    iguales = time.perf_counter() - inicio
    print(f"\n300 páginas ({na} y {res.paginas_b}): con cinco cambios {con_cambios:.1f} s; idénticos {iguales:.1f} s")
    assert con_cambios < 30 and iguales < 10


# ---------------------------------------------------------------- informes

def test_informe_pdf_portada_hojas_lista_y_cifrado(samples, tmp_path):
    a = str(samples["a"])
    core.build_document([(a, None)], [core.PageRef(0, k) for k in (0, 2, 3, 1, 4)], tmp_path / "movida.pdf")
    base = [(72, 100, "El plazo del contrato vence en 2024 sin excepciones.", 12)]
    pa = _pdf(tmp_path / "pa.pdf", [base, [(72, 100, "Página dos igual.", 12)]])
    pb = _pdf(tmp_path / "pb.pdf", [[(72, 100, "El plazo del contrato vence en 2025 sin excepciones.", 12)],
                                    [(72, 100, "Página dos igual.", 12)]])
    res = comparar.comparar(pa, pb)
    out = tmp_path / "informe.pdf"
    n = comparar.informe_pdf(pa, pb, res, out)
    with pymupdf.open(out) as d:
        assert n == d.page_count == 3              # portada + una hoja con cambios + lista
        portada = d[0].get_text()
        assert "pa.pdf" in portada and "pb.pdf" in portada and "1 cambio: 1 cambiado" in portada
        assert "rojo" in portada and "ámbar" in portada and "azul" in portada
        hoja = d[1].get_text()
        assert "Versión anterior · página 1" in hoja and "Versión nueva · página 1" in hoja
        assert "2024" in hoja and "2025" in hoja               # las dos versiones, como texto (vectores)
        assert d[1].rect.width > d[1].rect.height
        lista = d[2].get_text()
        assert "«2024» → «2025»" in lista and "Lista de cambios" in lista
    n_todas = comparar.informe_pdf(pa, pb, res, tmp_path / "todas.pdf", solo_con_cambios=False)
    assert n_todas == 4
    res = comparar.comparar(a, tmp_path / "movida.pdf")
    comparar.informe_pdf(a, tmp_path / "movida.pdf", res, tmp_path / "movida_informe.pdf")
    with pymupdf.open(tmp_path / "movida_informe.pdf") as d:
        texto = " ".join(p.get_text() for p in d)
        assert "pasó a la página 4" in texto and "estaba en la página 2" in texto
    comparar.informe_pdf(pa, pb, comparar.comparar(pa, pb), tmp_path / "cifrado.pdf", proteger_con="clave")
    assert core.needs_password(tmp_path / "cifrado.pdf") and core.check_password(tmp_path / "cifrado.pdf", "clave")


def test_informe_pdf_con_documentos_protegidos(protegido, tmp_path):
    with core.open_pdf(protegido.path, protegido.password) as d:
        d[1].insert_text((72, 500), "Observación nueva", fontsize=12)
        d.save(tmp_path / "nuevo.pdf", encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="otra", owner_pw="x")
    res = comparar.comparar(protegido.path, tmp_path / "nuevo.pdf", password_a=protegido.password,
                            password_b="otra")
    assert _tipos(res) == ["agregado"]
    out = tmp_path / "informe.pdf"
    comparar.informe_pdf(protegido.path, tmp_path / "nuevo.pdf", res, out, password_a=protegido.password,
                         password_b="otra", proteger_con="otra")
    with core.open_pdf(out, "otra") as d:
        assert "Observación nueva" in d[1].get_text() and "Página 2: informe" in d[1].get_text()
    protegido.comprobar(protegido.path)


def test_informe_excel_una_fila_por_cambio(samples, tmp_path):
    a = str(samples["a"])
    core.extract_pages(a, "1,3-5", tmp_path / "q.pdf")
    base = [(72, 100, "Valor 100 y nota.", 12), (72, 130, "Otra línea.", 12)]
    pa = _pdf(tmp_path / "pa.pdf", [base])
    pb = _pdf(tmp_path / "pb.pdf", [[(72, 100, "Valor 200 y nota.", 12)]])
    res = comparar.comparar(pa, pb)
    out = tmp_path / "lista.xlsx"
    comparar.informe_xlsx(res, out, "pa.pdf", "pb.pdf")
    with zipfile.ZipFile(out) as z:
        hoja = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
        libro = z.read("xl/workbook.xml").decode("utf-8")
    assert hoja.count("<row ") == len(res.cambios) + 1
    assert "N.°" in hoja and "Página anterior" in hoja and "Antes" in hoja and "Después" in hoja
    assert ">100<" in hoja and ">200<" in hoja and "Otra línea." in hoja
    assert 'name="Cambios"' in libro and 'name="Resumen"' in libro


# ---------------------------------------------------------------- interfaz

@pytest.fixture(scope="module")
def win():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setPalette(_light_palette())
    app.setStyleSheet(QSS)
    w = MainWindow()
    w.resize(1000, 660)
    w.show()
    app.processEvents()
    return w


@pytest.fixture
def dialogos(monkeypatch):
    llamadas = []

    def fake_done(parent, title, detail="", file=None, folder=None):
        llamadas.append(("listo", title, detail, file))

    monkeypatch.setattr(widgets, "show_done", fake_done)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: llamadas.append(("warning", a[2])))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: llamadas.append(("info", a[2])))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    return llamadas


def esperar(segundos: float = 0.2, limite: float = 60):
    fin = time.time() + segundos
    tope = time.time() + limite
    while time.time() < fin or widgets.ctx.busy:
        QApplication.processEvents()
        time.sleep(0.01)
        if time.time() > tope:
            raise TimeoutError
    QApplication.processEvents()


def foto(w, nombre):
    if SHOTS:
        QApplication.processEvents()
        w.grab().save(str(Path(SHOTS) / f"{nombre}.png"))


@pytest.fixture
def versiones(tmp_path):
    ps = [_parrafo(i) for i in range(30)]
    a, b = tmp_path / "contrato v1.pdf", tmp_path / "contrato v2.pdf"
    _flujo(a, ps)
    nuevo = list(ps)
    nuevo[0] = nuevo[0].replace("relación comercial", "relación comercial y laboral", 1)
    nuevo.insert(4, "Cláusula nueva de confidencialidad sobre la información de las partes.")
    nuevo[12] = nuevo[12].replace("Referencia 80.", "Referencia 81.")
    nuevo[20] = nuevo[20].replace(" Referencia 136.", "")
    _flujo(b, nuevo)
    with pymupdf.open(b) as d:                            # un recuadro en el margen inferior de la pág. 2
        d[1].draw_rect(pymupdf.Rect(420, 724, 520, 750), color=(0, 0, 1), fill=(0.85, 0.85, 1))
        d.saveIncr()
    t = time.time()
    os.utime(a, (t - 3600, t - 3600))          # la anterior es más antigua
    os.utime(b, (t, t))
    return str(a), str(b)


def test_interfaz_archivos_uno_y_dos(win, dialogos, versiones):
    a, b = versiones
    win.resize(1000, 660)
    win.sidebar.set_collapsed(False, remember=False)     # desde el rediseño 2.0 el menú empieza contraído
    win.navigate("compare")
    esperar()
    page = win.pages["compare"]
    page.nueva_comparacion()
    page.sel_a.clear()
    page.sel_b.clear()
    page.accept_files([b])                    # un archivo: queda como anterior y no se compara
    esperar(0.3)
    assert page.sel_a.path == b and page.sel_b.path is None and page.res is None
    assert not page.b_comparar.isEnabled() and not widgets.ctx.busy
    page.accept_files([a])                    # otro: queda como nueva
    assert (page.sel_a.path, page.sel_b.path) == (b, a) and page.b_comparar.isEnabled()
    page.intercambiar()
    assert (page.sel_a.path, page.sel_b.path) == (a, b)
    page.accept_files([b, a])                 # dos a la vez: el más antiguo es la anterior
    assert (page.sel_a.path, page.sel_b.path) == (a, b)
    page.sel_b.set_path(a)                    # el mismo dos veces
    assert page.aviso_mismo.isVisibleTo(page) and not page.b_comparar.isEnabled()
    assert "mismo archivo dos veces" in page.aviso_mismo.text()
    page.sel_b.set_path(b)
    assert not page.aviso_mismo.isVisibleTo(page) and page.b_comparar.isEnabled()
    assert dialogos == []
    # a 1000 × 660 con el menú abierto, las dos versiones caben lado a lado sin desbordar
    assert not win.sidebar.is_collapsed() and page.width() <= 1000 - 248   # menú abierto: 248 px
    assert page.caja_b.geometry().right() <= page.width()
    foto(win, "comparar_preparacion_1000")


def test_interfaz_comparar_navegar_filtrar_y_solo_nueva(win, dialogos, versiones):
    a, b = versiones
    win.resize(1000, 660)
    win.navigate("compare")
    page = win.pages["compare"]
    page.accept_files([a, b])
    page.comparar_ahora()
    esperar(0.6)
    res = page.res
    assert res is not None and page._pila.currentIndex() == 1
    assert sorted(_tipos(res)) == ["agregado", "agregado", "apariencia", "cambiado", "quitado"], \
        [comparar.texto_lista(c) for c in res.cambios]
    assert page.resumen.text() == res.texto_resumen() and page.resumen.text().startswith("5 cambios:")
    assert page.lista.count() == 5 and set(page.filtros) == {"agregado", "cambiado", "quitado", "apariencia"}
    assert page.lista.currentItem() is not None                       # el primer cambio queda elegido
    primero = page.lista.currentItem().data(Qt.ItemDataRole.UserRole)
    assert primero == 1 and page.vista.seleccion.id == 1
    texto = page.lista.item(0).text()
    assert texto.startswith("Pág. 1 → 1 · Agregado:") and "«y laboral»" in texto
    foto(win, "comparar_resultado_1000")
    # siguiente y anterior (F7 / Mayús+F7)
    page.mover(1)
    assert page.vista.seleccion.id == 2
    page.setFocus()
    QTest.keyClick(page, Qt.Key.Key_F7)
    assert page.vista.seleccion.id == 3
    QTest.keyClick(page, Qt.Key.Key_F7, Qt.KeyboardModifier.ShiftModifier)
    assert page.vista.seleccion.id == 2
    # filtros: sin apariencia, la lista y los resaltados la ocultan y «Siguiente» la salta
    apariencia = next(c for c in res.cambios if c.tipo == "apariencia")
    page.set_filtro("apariencia", False)
    assert page._items[apariencia.id].isHidden() and "apariencia" not in page.vista.tipos
    ids = []
    for _ in range(6):
        page.mover(1)
        ids.append(page.vista.seleccion.id)
    assert apariencia.id not in ids
    page.set_filtro("apariencia", True)
    assert not page._items[apariencia.id].isHidden()
    # clic en un cambio de la lista: las dos vistas van allí
    page.lista.setCurrentItem(page._items[apariencia.id])
    esperar(0.2)
    assert page.vista.seleccion is apariencia
    # clic en un resaltado: se elige su cambio en la lista
    cambiado = next(c for c in res.cambios if c.tipo == "cambiado")
    vista = page.vista
    page.lista.setCurrentItem(page._items[1])
    esperar(0.2)
    vista.ir_a(cambiado, parpadear=False)            # solo lleva la vista allí (sin tocar la lista)
    vista.seleccion = None
    fila = next(f for f in vista._filas if f["b"] == cambiado.pagina_b)
    r = vista._rect_pagina(fila, "b")
    q = vista._a_pantalla(r, cambiado.rects_b[0])
    assert vista._cambio_en(q.center()) is cambiado
    QTest.mouseClick(vista.viewport(), Qt.MouseButton.LeftButton, pos=q.center().toPoint())
    assert page.lista.currentItem() is page._items[cambiado.id]
    # con poco ancho aparece «Ver solo la versión nueva»
    assert page.c_solo.isVisibleTo(page) and page.width() < comparar_ui.ANCHO_ESTRECHO
    page.c_solo.setChecked(True)
    esperar(0.3)
    assert vista.solo_nueva and not page.cab_a.isVisibleTo(page)
    foto(win, "comparar_solo_nueva_1000")
    page.c_solo.setChecked(False)
    # todo cabe a 1000 × 660 con el menú abierto
    for w in (page.b_informe, page.b_nueva, page.b_sig, page.b_mas, page.c_solo):
        assert w.isVisibleTo(page) and w.mapTo(page, QPoint(w.width(), 0)).x() <= page.width(), w.text()
    assert vista.viewport().width() > 400 and page.lista.width() <= 260
    win.resize(1240, 820)
    esperar(0.4)
    assert not page.c_solo.isVisibleTo(page) and not vista.solo_nueva
    foto(win, "comparar_resultado_1240")
    win.resize(1000, 660)
    esperar(0.2)
    assert dialogos == []


def test_interfaz_informes(win, dialogos, versiones, tmp_path, monkeypatch):
    a, b = versiones
    win.navigate("compare")
    page = win.pages["compare"]
    page.accept_files([a, b])
    page.comparar_ahora()
    esperar(0.5)
    destino = tmp_path / "informe"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(destino), "")))
    page.guardar_informe(False)
    esperar(0.5)
    assert (tmp_path / "informe.pdf").exists() and dialogos[-1][0] == "listo"
    assert dialogos[-1][3] == str(tmp_path / "informe.pdf")
    with pymupdf.open(tmp_path / "informe.pdf") as d:
        assert d.page_count >= 3 and "Comparación de dos PDF" in d[0].get_text()
    page.guardar_excel()
    esperar(0.5)
    assert (tmp_path / "informe.xlsx").exists()
    with zipfile.ZipFile(tmp_path / "informe.xlsx") as z:
        assert z.read("xl/worksheets/sheet1.xml").decode("utf-8").count("<row ") == len(page.res.cambios) + 1
    page.nueva_comparacion()
    assert page._pila.currentIndex() == 0 and page.res is None and page.sel_a.path == a


def test_interfaz_con_contrasena_e_informe_protegido(win, dialogos, protegido, tmp_path, monkeypatch):
    nuevo = tmp_path / "nuevo protegido.pdf"
    with core.open_pdf(protegido.path, protegido.password) as d:
        d[0].insert_text((72, 400), "Párrafo agregado en la versión nueva", fontsize=12)
        d.save(nuevo, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="5678", owner_pw="x")
    widgets.ctx.passwords.set(str(protegido.path), protegido.password)
    widgets.ctx.passwords.set(str(nuevo), "5678")
    antes = (_hash(protegido.path), _hash(nuevo))
    win.navigate("compare")
    page = win.pages["compare"]
    page.nueva_comparacion()
    page.sel_a.clear()
    page.sel_b.clear()
    page.accept_files([str(protegido.path)])
    page.accept_files([str(nuevo)])
    page.comparar_ahora()
    esperar(0.5)
    assert _tipos(page.res) == ["agregado"]
    mensajes = []

    def responder(box):
        mensajes.append((box.text(), box.checkBox().text(), box.checkBox().isChecked()))
        next(b for b in box.buttons() if b.text() == "Guardar informe").click()
        return 0

    monkeypatch.setattr(QMessageBox, "exec", responder)
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(tmp_path / "informe protegido.pdf"), "")))
    page.guardar_informe(True)
    esperar(0.5)
    assert mensajes and comparar_ui.AVISO_INFORME in mensajes[0][0]
    assert mensajes[0][1] == "Proteger el informe con la contraseña de la versión nueva" and mensajes[0][2]
    out = tmp_path / "informe protegido.pdf"
    assert core.needs_password(out) and core.check_password(out, "5678")
    assert (_hash(protegido.path), _hash(nuevo)) == antes
    protegido.comprobar(protegido.path)
    page.nueva_comparacion()


def test_interfaz_sin_diferencias(win, dialogos, samples):
    win.navigate("compare")
    page = win.pages["compare"]
    page.nueva_comparacion()
    page.sel_a.clear()
    page.sel_b.clear()
    a = str(samples["a"])
    copia = Path(samples["dir"]) / "informe copia.pdf"
    copia.write_bytes(Path(a).read_bytes())
    page.accept_files([a])
    page.accept_files([str(copia)])
    page.comparar_ahora()
    esperar(0.5)
    assert page.res.cambios == [] and page.resumen.text() == comparar.SIN_DIFERENCIAS
    assert page.lista.count() == 1 and page.lista.item(0).text() == comparar.SIN_DIFERENCIAS
    assert page.zona_chips.findChildren(QToolButton) == []          # no quedan filtros de la anterior
    assert not page.b_sig.isEnabled()
    page.mover(1)                              # no falla sin cambios
    page.nueva_comparacion()


def test_visor_comparar_con_otro_pdf(win, dialogos, samples):
    v = win.pages["viewer"]
    win.navigate("viewer")
    v.load(str(samples["a"]))
    esperar(0.3)
    textos = [a.text() for a in v.b_mas.menu().actions()]
    assert any(t.startswith("Comparar con otro PDF…") for t in textos)
    accion = next(a for a in v.b_mas.menu().actions() if a.text().startswith("Comparar con otro PDF…"))
    accion.trigger()
    esperar(0.3)
    page = win.pages["compare"]
    assert win.stack.currentWidget() is page and page.sel_a.path == str(samples["a"])
    assert page._pila.currentIndex() == 0 and not widgets.ctx.busy
