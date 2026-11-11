"""Pruebas de «Censurar datos» y «Quitar datos ocultos» (ronypdf/censura.py y ronypdf/censura_ui.py)."""

import hashlib
import os
import tempfile
import time

import pymupdf
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Los ajustes (recientes, menú) de las pruebas nunca deben tocar los reales.
os.environ.setdefault("RONYPDF_SETTINGS", os.path.join(tempfile.mkdtemp(prefix="ronypdf-"), "RonyPDF.json"))

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ronypdf import censura, censura_ui, core, extensiones, gui, icons, widgets  # noqa: E402
from ronypdf.gui import MainWindow, _light_palette  # noqa: E402
from ronypdf.style import QSS  # noqa: E402

ACTIVOS = [k for k, p in censura.PATRONES.items() if p.activo]

# ---------------------------------------------------------------- patrones

POSITIVOS = {
    "cedula": [("C.C. 1.032.456.789 de Bogotá", "1.032.456.789"), ("CC 79123456", "79123456"),
               ("cédula de ciudadanía No. 52.123.456", "52.123.456"), ("T.I. 1002345678", "1002345678"),
               ("identificación: 1032456789", "1032456789"), ("NUIP 1234567890", "1234567890"),
               ("Documento de identidad # 98.765.432", "98.765.432"), ("C. C. 1.234.567", "1.234.567")],
    "nit": [("NIT 899.999.068-1", "899.999.068-1"), ("Nit: 900.123.456-7", "900.123.456-7"),
            ("899999068-1", "899999068-1"), ("NIT. 800197268", "800197268"), ("proveedor 899.999.068 - 1",
                                                                              "899.999.068 - 1")],
    "celular": [("310 456 7890", "310 456 7890"), ("+57 310 456 7890", "+57 310 456 7890"),
                ("3104567890", "3104567890"), ("(310) 456-7890", "(310) 456-7890"),
                ("Cel. 310.456.7890", "310.456.7890"), ("57 300 123 4567", "57 300 123 4567")],
    "telefono": [("(601) 345 6789", "(601) 345 6789"), ("Tel. 601 3456789", "601 3456789"),
                 ("+57 604 444 1234", "+57 604 444 1234"), ("6013456789", "6013456789"),
                 ("608-123-4567", "608-123-4567")],
    "correo": [("juan.perez@gmail.com", "juan.perez@gmail.com"), ("a_b+c@empresa.com.co", "a_b+c@empresa.com.co"),
               ("CONTACTO@FUNDACION.ORG", "CONTACTO@FUNDACION.ORG"), ("escribe a x@y.co hoy", "x@y.co"),
               ("nombre-apellido@dominio.gov.co", "nombre-apellido@dominio.gov.co")],
    "cuenta": [("Cuenta de ahorros 0123-4567-8901", "0123-4567-8901"),
               ("cuenta corriente No. 12345678", "12345678"), ("Cta. No. 1234 5678 9012", "1234 5678 9012"),
               ("cuenta bancaria: 98765432101", "98765432101"), ("Ahorros # 123456789", "123456789")],
    "tarjeta": [("4111 1111 1111 1111", "4111 1111 1111 1111"), ("5500-0000-0000-0004", "5500-0000-0000-0004"),
                ("4111111111111111", "4111111111111111"), ("Amex 378282246310005", "378282246310005"),
                ("6011111111111117", "6011111111111117")],
    "direccion": [("Calle 45 # 12-30", "Calle 45 # 12-30"), ("Cra. 7 No. 32-16", "Cra. 7 No. 32-16"),
                  ("Vive en Cll 100 #15-20 Sur.", "Cll 100 #15-20 Sur"),
                  ("Carrera 15A Bis # 93-47", "Carrera 15A Bis # 93-47"),
                  ("Diagonal 23 Sur # 4-56", "Diagonal 23 Sur # 4-56"),
                  ("Transversal 5 # 10 - 20", "Transversal 5 # 10 - 20"), ("Kr30 #45A-12 Este", "Kr30 #45A-12 Este"),
                  ("Av. Boyacá No. 12-34", "Av. Boyacá No. 12-34"),
                  ("Calle 26 # 50-20 Apto 301", "Calle 26 # 50-20 Apto 301")],
    "placa": [("ABC123", "ABC123"), ("moto ABC12D", "ABC12D"), ("XYZ-987", "XYZ-987"),
              ("placa KLM 456", "KLM 456"), ("MNO12A", "MNO12A")],
    "fecha": [("15/03/1985", "15/03/1985"), ("1/1/2020", "1/1/2020"), ("31-12-1999", "31-12-1999")],
}

NEGATIVOS = {
    "cedula": ["Total 1.032.456.789", "Teléfono 3001234567", "C.C. 12345", "en 2024 hubo 1032456789",
               "CC 12345678901", "1032456789", "Código 79.123.456"],
    "nit": ["8999990681", "899.999.068-2", "899.999.068", "1.032.456.789", "3001234567", "Radicado 123456789"],
    "celular": ["1234567890", "601 234 5678", "3601234567", "31045678", "310456789012", "C.C. 1.032.456.789"],
    "telefono": ["609 123 4567", "300 123 4567", "601234", "60112345678", "345 6789", "1234567890"],
    "correo": ["usuario@", "@dominio.com", "correo arroba dominio.com", "a@b", "usuario@dominio", "www.sitio.com"],
    "cuenta": ["cuenta 1234567", "cuenta con 12345678 habitantes", "Cuentas por cobrar 12345678", "123456789012",
               "cuenta de ahorros 12", "Ahorramos 12345678"],
    "tarjeta": ["4111 1111 1111 1112", "1234 5678 9012 3456", "0000000000000000", "4111 1111 111", "3001234567",
                "9111111111111111"],
    "direccion": ["Calle 45", "la calle principal", "Cra 7 32-16", "Avenida 68", "Calle 5 de mayo",
                  "carrera de observación 5 km"],
    "placa": ["AB1234", "ABCD123", "abc123", "ABC1234", "AB12C", "Calle ab-123", "NIT 899.999.068-1",
              "NIT 899", "COP 500.000", "COP 500", "LEY 158 de 1998", "USD 250", "IVA 190"],
    "fecha": ["32/01/2000", "15/13/2000", "2000/01/15"],
}


@pytest.mark.parametrize("clave,texto,esperado", [(k, t, e) for k, casos in POSITIVOS.items() for t, e in casos])
def test_patron_positivo(clave, texto, esperado):
    assert censura.coincidencias(clave, texto) == [esperado]


@pytest.mark.parametrize("clave,texto", [(k, t) for k, casos in NEGATIVOS.items() for t in casos])
def test_patron_negativo(clave, texto):
    assert censura.coincidencias(clave, texto) == []


def test_direccion_leida_por_ocr():
    # Tesseract lee el «#» como «++», «+», «*» o «H»: en el texto del OCR también se reconoce
    for texto in ("Calle 45 ++ 12-30 Sur, apto 301", "Cra. 7 + 32-16", "Carrera 15A * 93-47", "Calle 26 H 50-20"):
        assert censura.coincidencias("direccion", texto, ocr=True), texto
        assert not censura.coincidencias("direccion", texto), texto            # en texto digital, no
    assert censura.coincidencias("direccion", "Calle 45 ++ 12-30 Sur, apto 301", ocr=True) == [
        "Calle 45 ++ 12-30 Sur, apto 301"]
    for texto in ("Cra 7 32-16", "Calle 45", "Avenida 68"):
        assert not censura.coincidencias("direccion", texto, ocr=True), texto


def test_patrones_activos_por_defecto():
    assert set(ACTIVOS) == {"cedula", "nit", "celular", "telefono", "correo", "cuenta", "tarjeta", "direccion"}
    assert not censura.PATRONES["placa"].activo and not censura.PATRONES["fecha"].activo


@pytest.mark.parametrize("guion", ["\u2010", "\u2011", "\u2012", "\u2013", "\u2212", "\u00ad", "\uff0d"])
def test_guiones_tipograficos_cuentan_como_guion(guion):
    """El guion de un PDF con fuentes incrustadas sale como U+2010 y otros: el NIT conserva su dígito de
    verificación y el celular con guiones se encuentra (antes quedaba «█████-8» y el celular sin censurar)."""
    texto = f"NIT 901.234.567{guion}8 y celular 310{guion}456{guion}7890"
    assert censura.coincidencias("nit", texto) == [f"901.234.567{guion}8"]
    assert censura.coincidencias("celular", texto) == [f"310{guion}456{guion}7890"]
    assert len(censura.normalizar(texto)) == len(texto)          # misma longitud: las zonas no se corren


def test_guiones_en_un_pdf_real_con_segoe_ui(tmp_path):
    """Un PDF hecho con Segoe UI incrustada: MuPDF devuelve U+2010 y aun así se censura completo."""
    fuente = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "segoeuib.ttf")
    if not os.path.exists(fuente):
        pytest.skip("Segoe UI no está instalada")
    doc = pymupdf.open()
    pag = doc.new_page()
    pag.insert_font(fontname="s", fontfile=fuente)
    pag.insert_text((50, 100), "NIT 901.234.567-8 · Celular 310-456-7890", fontname="s", fontsize=11)
    doc.subset_fonts()
    ruta = tmp_path / "ficha.pdf"
    doc.save(ruta)
    texto = pymupdf.open(ruta)[0].get_text()
    assert censura.coincidencias("nit", texto)[0].endswith("8")
    assert len(censura.coincidencias("celular", texto)) == 1


def test_validar_nit_y_luhn():
    assert censura.validar_nit("899.999.068-1")
    assert censura.validar_nit("8999990681")
    for dv in "023456789":
        assert not censura.validar_nit(f"899.999.068-{dv}")
    assert censura.digito_verificacion("800197268") == 4 and censura.validar_nit("800.197.268-4")
    assert censura.luhn("4111 1111 1111 1111")
    assert not censura.luhn("4111 1111 1111 1112")


# ---------------------------------------------------------------- documentos de prueba

LINEAS = ["Listado de beneficiarios 2024", "Nombre: María José Gómez Peña", "C.C. 1.032.456.789 de Bogotá",
          "Empresa aliada NIT 899.999.068-1", "Celular: 310 456 7890 (casa)", "Correo: maria.gomez@correo.com",
          "Cuenta de ahorros 0123-4567-8901 Bancolombia", "Dirección: Calle 45 # 12-30 Sur, Bogotá",
          "Código interno 2087654321 sin contexto", "Fin del listado"]


def _nuevo_pdf(path, paginas, **save):
    with pymupdf.open() as d:
        for lineas in paginas:
            p = d.new_page()
            for k, t in enumerate(lineas):
                p.insert_text((72, 100 + k * 14), t, fontsize=11)
        d.save(path, **save)
    return path


@pytest.fixture
def lista(tmp_path):
    return _nuevo_pdf(tmp_path / "beneficiarios.pdf", [LINEAS])


def _sha(path) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


# ---------------------------------------------------------------- buscar

def test_buscar_categorias_y_rectangulos(lista):
    res = censura.buscar(lista, patrones=ACTIVOS)
    assert {h.categoria for h in res} == {"cedula", "nit", "celular", "correo", "cuenta", "direccion"}
    assert len(res) == 6 and res.paginas == 1 and not res.escaneadas
    assert all(h.pagina == 0 and h.origen == "texto" and h.incluir for h in res)
    por = {h.categoria: h for h in res}
    assert por["cedula"].texto == "1.032.456.789"          # solo el número, no «C.C.»
    assert por["cuenta"].texto == "0123-4567-8901"
    assert por["direccion"].texto == "Calle 45 # 12-30 Sur"
    assert "C.C. 1.032.456.789 de Bog" in por["cedula"].contexto
    assert not any("2087654321" in h.texto for h in res)  # un número suelto no es una cédula
    with pymupdf.open(lista) as d:
        for h in res:
            real = d[0].search_for(h.texto)[0]
            r = pymupdf.Rect(h.rect)
            assert abs(r.x0 - real.x0) < 1 and abs(r.x1 - real.x1) < 1, h
            # el rectángulo queda dentro de su renglón, sin invadir los de arriba y abajo
            assert real.y0 <= r.y0 < r.y1 <= real.y1 and r.height > 6, (h, real)


def test_buscar_palabras_tildes_completa_paginas(tmp_path):
    path = _nuevo_pdf(tmp_path / "nombres.pdf", [
        ["Beneficiaria: María José Gómez", "Madre: Mariana Ospina", "C.C. 1.032.456.789"],
        ["MARIA JOSE GOMEZ firmó el acta", "Radicado 004512"]])
    res = censura.buscar(path, palabras=["maria jose gomez"])
    assert [(h.pagina, h.texto) for h in res] == [(0, "María José Gómez"), (1, "MARIA JOSE GOMEZ")]
    assert all(h.categoria == "palabra" for h in res)
    assert len(censura.buscar(path, palabras=["ana"])) == 1                         # dentro de «Mariana»
    assert len(censura.buscar(path, palabras=["ana"], palabra_completa=True)) == 0
    assert [h.pagina for h in censura.buscar(path, palabras=["maria jose gomez"], paginas="2")] == [1]
    exacta = censura.buscar(path, palabras=["María José Gómez"], mayusculas=True)
    assert [h.pagina for h in exacta] == [0]
    # un número escrito sin puntos encuentra la cédula escrita con puntos
    assert [h.texto for h in censura.buscar(path, palabras=["1032456789"])] == ["1.032.456.789"]
    assert [h.texto for h in censura.buscar(path, regex=[r"Radicado \d{6}"])] == ["Radicado 004512"]
    with pytest.raises(core.UserError, match="La expresión no es válida"):
        censura.buscar(path, regex=["(["])
    with pytest.raises(core.UserError):
        censura.buscar(path)


def test_buscar_coincidencia_en_dos_renglones(tmp_path):
    path = _nuevo_pdf(tmp_path / "corte.pdf", [["Firmó la señora María José", "Gómez Peña en Bogotá"]])
    res = censura.buscar(path, palabras=["María José Gómez"])
    assert [h.texto for h in res] == ["María José", "Gómez"]
    assert res[0].rect[1] < res[1].rect[1]


# ---------------------------------------------------------------- censurar

def test_censurar_borra_de_verdad_y_respeta_vecinos(lista, tmp_path):
    with pymupdf.open(lista) as d:            # un enlace «mailto:» encima del correo también guarda el dato
        d[0].insert_link({"kind": pymupdf.LINK_URI, "from": d[0].search_for("maria.gomez@correo.com")[0],
                          "uri": "mailto:maria.gomez@correo.com"})
        d.saveIncr()
    antes = _sha(lista)
    res = censura.buscar(lista, patrones=ACTIVOS)
    out = tmp_path / "censurado.pdf"
    r = censura.censurar(lista, out, res)
    assert (r.zonas, r.paginas, r.restos) == (6, 1, [])
    assert _sha(lista) == antes                        # el original no cambia
    with pymupdf.open(out) as d:
        texto = d[0].get_text()
        for h in res:
            assert h.texto not in texto and not d[0].search_for(h.texto)
        for vecino in ("Listado de beneficiarios 2024", "Nombre: María José Gómez Peña", "C.C.", "de Bogotá",
                       "Empresa aliada NIT", "(casa)", "Correo:", "Bancolombia", ", Bogotá",
                       "Código interno 2087654321 sin contexto", "Fin del listado"):
            assert vecino in texto, vecino
        pix = d[0].get_pixmap()
        x0, y0, x1, y1 = res[0].rect
        assert pix.pixel(int((x0 + x1) / 2), int((y0 + y1) / 2)) == (0, 0, 0)    # recuadro negro
        assert d[0].get_links() == []
    data = out.read_bytes()
    assert data.count(b"%%EOF") == 1                   # archivo reescrito completo, sin guardado incremental


def test_censurar_respeta_excluidos(lista, tmp_path):
    res = censura.buscar(lista, patrones=ACTIVOS)
    correo = next(h for h in res if h.categoria == "correo")
    correo.incluir = False
    out = tmp_path / "parcial.pdf"
    r = censura.censurar(lista, out, res)
    assert r.zonas == 5 and r.restos == []
    with pymupdf.open(out) as d:
        texto = d[0].get_text()
    assert "maria.gomez@correo.com" in texto and "1.032.456.789" not in texto


def test_censurar_pagina_girada(samples, tmp_path):
    res = censura.buscar(samples["b"], palabras=["Anexo"], paginas="2")
    assert len(res) == 1 and res[0].pagina == 1
    with pymupdf.open(samples["b"]) as d:
        visible = [r for p, r in core.search_text(d, "Anexo") if p == 1][0]
    r = pymupdf.Rect(res[0].rect)
    assert abs((r.x0 + r.x1) / 2 - (visible.x0 + visible.x1) / 2) < 2
    assert abs((r.y0 + r.y1) / 2 - (visible.y0 + visible.y1) / 2) < 2
    out = tmp_path / "girado.pdf"
    # la verificación avisa que «Anexo» sigue en las páginas que no se revisaron
    restos = censura.censurar(samples["b"], out, res).restos
    assert {(x.donde, x.texto) for x in restos} == {("página 1", "Anexo"), ("página 3", "Anexo")}
    with pymupdf.open(out) as d:
        assert "Anexo" not in d[1].get_text()
        assert "Anexo 1" in d[0].get_text() and "Anexo 3" in d[2].get_text()
        page = d[1]
        assert page.rect.width > page.rect.height                       # se sigue viendo apaisada
        pix = page.get_pixmap()
        assert pix.pixel(int((r.x0 + r.x1) / 2), int((r.y0 + r.y1) / 2)) == (0, 0, 0)


def test_censurar_conserva_contrasena(samples, tmp_path):
    res = censura.buscar(samples["locked"], palabras=["Informe"], password="1234")
    assert len(res) == 5
    out = tmp_path / "secreto_censurado.pdf"
    r = censura.censurar(samples["locked"], out, res, password="1234")
    assert [(x.donde, x.texto) for x in r.restos] == [("metadatos", "Informe")]    # título «Informe anual»
    assert core.needs_password(out)
    with core.open_pdf(out, "1234") as d:
        assert all("Informe" not in p.get_text() for p in d)
        assert all("Página" in p.get_text() for p in d)
    with pytest.raises(core.PasswordError):
        core.open_pdf(out, "otra")


def test_censurar_y_limpiar_con_contrasena_no_borran_lo_demas(protegido, tmp_path):
    """Las páginas sin hallazgos, las imágenes y los adjuntos siguen completos (antes salían en blanco)."""
    pw = protegido.password
    res = censura.buscar(protegido.path, patrones=["correo"], paginas="1", password=pw)
    assert [h.texto for h in res] == ["ana0@correo.com"]
    out = tmp_path / "censurado.pdf"
    censura.censurar(protegido.path, out, res, password=pw)
    protegido.comprobar(out, sin_tocar=(1, 2))
    with core.open_pdf(out, pw) as d:
        assert "ana0@correo.com" not in d[0].get_text() and "informe de gestión" in d[0].get_text()
    out = tmp_path / "limpio.pdf"
    censura.limpiar(protegido.path, out, ["metadatos"], password=pw)
    protegido.comprobar(out)


def _palabras(path, pagina=0):
    with pymupdf.open(path) as d:
        return [(w[4], w[0], w[1]) for w in d[pagina].get_text("words")]


def _corridas(antes, despues):
    """Palabras que siguen en la página pero ya no están donde estaban (mismo renglón, otra x)."""
    sitio: dict = {}
    for t, x, y in antes:
        sitio.setdefault((t, round(y)), []).append(x)
    return [(t, round(x, 1)) for t, x, y in despues
            if (t, round(y)) in sitio and min(abs(x - x0) for x0 in sitio[(t, round(y))]) > 0.3]


def test_censurar_no_corre_el_texto_vecino(tmp_path):
    """MuPDF 1.28 corría a la izquierda lo que sigue a la censura en el renglón (quedaba bajo el recuadro)."""
    path = tmp_path / "vecinos.pdf"
    with pymupdf.open() as d:
        p = d.new_page()
        for k, t in enumerate(["Nombre: María Fernanda Peñaloza Gómez", "C.C. 1.032.456.789 de Bogotá",
                               "Cuenta de ahorros 0123-4567-8901 Bancolombia",
                               "Observaciones: la señora María Fernanda vive con su hijo José Peñaloza."]):
            p.insert_text((72, 100 + k * 16), t, fontsize=11)
        arial = r"C:\Windows\Fonts\arial.ttf"
        if os.path.exists(arial):                       # fuente Type0 (dos bytes por letra), como las de Word
            p.insert_font(fontname="ar", fontfile=arial)
            p.insert_text((72, 200), "Celular 310 456 7890 de Ana y correo ana@x.com al final", fontname="ar",
                          fontsize=11)
        # TJ con ajustes de espaciado entre letras y Tc, como los que escribe Word
        fuente = next(f[4] for f in p.get_fonts(full=True) if f[3] == "Helvetica")
        d.update_stream(p.get_contents()[0], d.xref_stream(p.get_contents()[0]) + (
            f"\nBT /{fuente} 11 Tf 0.3 Tc 72 560 Td [(Se reun)4(i\\363)7( la jun)4(ta. )9(M)-3(ar\\355)4(a )9"
            f"(P)6(e\\361al)4(o)-5(z)4(a pres)11(ent)10(\\363 el informe)] TJ ET").encode("latin-1"))
        d.save(path)
    antes = _palabras(path)
    res = censura.buscar(path, patrones=ACTIVOS, palabras=["María Fernanda", "Peñaloza", "María Peñaloza"])
    assert len(res) >= 8
    out = tmp_path / "vecinos_censurado.pdf"
    assert censura.censurar(path, out, res).restos == []
    despues = _palabras(out)
    assert _corridas(antes, despues) == []
    for vecina in ("Gómez", "Bogotá", "Bancolombia", "vive", "José", "presentó", "informe"):
        assert vecina in {t for t, _x, _y in despues}, vecina
    assert censura.auditar(out) == []                     # nada queda debajo de un recuadro
    # «Borrar zona» del visor usa el mismo arreglo
    with pymupdf.open(path) as d:
        zona = tuple(d[0].search_for("1.032.456.789")[0])
    out2 = tmp_path / "zona.pdf"
    core.apply_edits(path, out2, [core.Edit("redact", 0, rect=zona, fill=(0, 0, 0))])
    assert _corridas(antes, _palabras(out2)) == []


def test_escaneado_con_ocr(scanned, tmp_path):
    if not core.ocr_languages():
        pytest.skip("no hay modelos de OCR")
    res = censura.buscar(scanned, palabras=["Cooperativa"])
    assert len(res) == 2 and {h.pagina for h in res} == {0, 1}
    assert all(h.origen == "ocr" and h.texto == "Cooperativa" for h in res)
    assert res.escaneadas == [0, 1]
    out = tmp_path / "escaneado_censurado.pdf"
    r = censura.censurar(scanned, out, res)
    assert r.restos == []
    # la copia censurada no da falsas alarmas: los píxeles de debajo de cada recuadro ya se borraron
    assert censura.auditar(out) == []
    tess = core._check_ocr("spa+eng")
    with pymupdf.open(out) as d:
        for h in res:
            page = d[h.pagina]
            palabras = [w[4].lower() for w in core._ocr_words(page, "spa+eng", 300, tess)]
            assert not any("cooperativa" in w for w in palabras)
            assert any("caficultores" in w for w in palabras)       # la palabra vecina sigue ahí
            assert censura._zona_uniforme(page, h.rect)
            x0, y0, x1, y1 = h.rect
            pix = page.get_pixmap(clip=pymupdf.Rect(x0 + 2, y0 + 2, x1 - 2, y1 - 2))
            assert pix.is_unicolor


def test_escaneado_con_capa_ocr_borra_texto_y_pixeles(scanned, tmp_path):
    if not core.ocr_languages():
        pytest.skip("no hay modelos de OCR")
    buscable = tmp_path / "buscable.pdf"
    core.ocr_pdf(scanned, buscable, spec="1")
    res = censura.buscar(buscable, palabras=["Cooperativa"], paginas="1")
    assert len(res) == 1 and res[0].origen == "texto" and res[0].rect_texto is not None
    out = tmp_path / "buscable_censurado.pdf"
    assert censura.censurar(buscable, out, res).restos == []
    with pymupdf.open(out) as d:
        texto = d[0].get_text()
        assert "Cooperativa" not in texto and "caficultores" in texto
        assert censura._zona_uniforme(d[0], res[0].rect)
    assert not core.page_needs_ocr(pymupdf.open(buscable)[0])     # ya tiene su capa de texto


SELLO = "Radicado No. 2026-004512 del 09/11/2026"


def _con_sello(scanned, path):
    """El escaneo con un sello de radicado digital encima (más de 20 letras de texto propio)."""
    with pymupdf.open(scanned) as d:
        for p in d:
            p.insert_text((300, 30), SELLO, fontsize=9)
        d.save(path)
    return path


def test_escaneado_con_sello_digital_se_revisa_con_ocr(scanned, tmp_path):
    if not core.ocr_languages():
        pytest.skip("no hay modelos de OCR")
    sellado = _con_sello(scanned, tmp_path / "sellado.pdf")
    with pymupdf.open(sellado) as d:
        assert core.page_has_text(d[0]) and core.looks_scanned(d)     # el sello ya no engaña a looks_scanned
        assert core.page_needs_ocr(d[0]) and core.looks_scanned_without_text(d)
    res = censura.buscar(sellado, palabras=["Cooperativa"])
    assert len(res) == 2 and all(h.origen == "ocr" for h in res)
    assert res.escaneadas == [0, 1] and res.sin_revisar == [] and res.avisos == []
    # el sello se busca en su texto y no sale repetido por el OCR
    res = censura.buscar(sellado, palabras=["Radicado"])
    assert [(h.pagina, h.origen) for h in res] == [(0, "texto"), (1, "texto")]
    # sin OCR la imagen queda sin revisar, y se avisa
    res = censura.buscar(sellado, palabras=["Cooperativa"], usar_ocr=False)
    assert len(res) == 0 and res.sin_revisar == [0, 1]
    assert res.avisos and "páginas 1 y 2" in res.avisos[0]


def test_pagina_digital_no_necesita_ocr(lista, samples):
    with pymupdf.open(lista) as d:
        assert not core.page_needs_ocr(d[0]) and not core.looks_scanned_without_text(d)
    with pymupdf.open(samples["a"]) as d:            # texto e imagen grande, pero no a toda la página
        assert not core.page_needs_ocr(d[0])


# ---------------------------------------------------------------- verificar

def test_verificar_encuentra_restos(tmp_path):
    path = tmp_path / "acta.pdf"
    with pymupdf.open() as d:
        p = d.new_page()
        p.insert_text((72, 100), "Beneficiario: Juan Pérez Rodríguez", fontsize=12)
        p.insert_text((72, 130), "Firma del acta", fontsize=12)
        p.add_text_annot((400, 60), "Llamar a Juan Pérez Rodríguez mañana")
        w = pymupdf.Widget()
        w.field_name = "nombre"
        w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        w.rect = pymupdf.Rect(72, 300, 300, 320)
        w.field_value = "Juan Pérez Rodríguez"
        p.add_widget(w)
        d.set_metadata({"title": "Acta de Juan Pérez Rodríguez"})
        d.save(path)
    antes = {x.donde for x in censura.verificar(path, ["Juan Pérez Rodríguez"])}
    assert antes == {"página 1", "metadatos", "comentario de la página 1", "campo de formulario"}
    res = censura.buscar(path, palabras=["Juan Pérez Rodríguez"])
    assert len(res) == 2                      # en el texto y en el valor del campo de formulario
    out = tmp_path / "acta_censurada.pdf"
    r = censura.censurar(path, out, res)
    # el campo se aplanó y su dato se borró; quedan el comentario y los metadatos, que no se ven en la página
    assert {x.donde for x in r.restos} == {"metadatos", "comentario de la página 1"}
    assert all(x.texto == "Juan Pérez Rodríguez" and x.tipo == "texto" for x in r.restos)
    with pymupdf.open(out) as d:
        assert not list(d[0].widgets()) and "Juan" not in d[0].get_text()
        assert "Firma del acta" in d[0].get_text()


# ---------------------------------------------------------------- auditar

def test_auditar_falsas_censuras(tmp_path):
    path = tmp_path / "tapado.pdf"
    with pymupdf.open() as d:
        p = d.new_page()
        p.insert_text((72, 100), "Cédula del titular 1032456789", fontsize=12)
        num = p.search_for("1032456789")[0]
        p.draw_rect(num + (-2, -3, 2, 3), color=(0, 0, 0), fill=(0, 0, 0))       # pintado DESPUÉS del texto
        p.draw_rect(pymupdf.Rect(72, 200, 330, 232), color=(0, 0, 0), fill=(0, 0, 0))
        p.insert_text((80, 222), "TÍTULO EN BLANCO", fontsize=16, color=(1, 1, 1))  # pintado ENCIMA: no es falsa
        q = d.new_page()
        q.insert_text((72, 100), "Dato pendiente 555 del expediente", fontsize=12)
        q.add_redact_annot(q.search_for("pendiente 555")[0])                   # marcada sin aplicar
        d.save(path)
    falsas = censura.auditar(path)
    assert [h.pagina for h in falsas] == [0, 1]
    assert all(h.categoria == "falsa" for h in falsas)
    assert "1032456789" in falsas[0].texto and "pendiente 555" in falsas[1].texto
    assert not any("BLANCO" in h.texto for h in falsas)
    out = tmp_path / "tapado_censurado.pdf"
    r = censura.censurar(path, out, falsas)
    assert r.restos == []
    with pymupdf.open(out) as d:
        t0, t1 = d[0].get_text(), d[1].get_text()
    assert "1032456789" not in t0 and "Cédula del titular" in t0 and "TÍTULO EN BLANCO" in t0
    assert "pendiente" not in t1 and "Dato" in t1 and "del expediente" in t1


def test_auditar_recuadro_sobre_escaneo(scanned, tmp_path):
    path = tmp_path / "escaneo_tapado.pdf"
    with pymupdf.open(scanned) as d:
        p = d[0]
        p.draw_rect(pymupdf.Rect(70, 95, 215, 130), color=(0, 0, 0), fill=(0, 0, 0))      # sobre «Cooperativa»
        p.draw_rect(pymupdf.Rect(300, 600, 400, 650), color=(1, 1, 1), fill=(1, 1, 1))    # blanco: no se marca
        d.save(path)
    falsas = censura.auditar(path)
    assert len(falsas) == 1 and falsas[0].pagina == 0 and falsas[0].texto == ""
    assert "imagen escaneada" in falsas[0].contexto
    out = tmp_path / "escaneo_tapado_censurado.pdf"
    assert censura.censurar(path, out, falsas).restos == []
    with pymupdf.open(out) as d:
        # los píxeles de debajo se borraron: sin el recuadro, la zona de la imagen ya está vacía
        img = d[0].get_images(full=True)[0]
        pix = pymupdf.Pixmap(d, img[0])
        bbox = d[0].get_image_bbox(img[7])
        sx, sy = pix.width / bbox.width, pix.height / bbox.height
        x0, y0, x1, y1 = falsas[0].rect
        clip = pymupdf.IRect(int((x0 - bbox.x0) * sx) + 3, int((y0 - bbox.y0) * sy) + 3,
                             int((x1 - bbox.x0) * sx) - 3, int((y1 - bbox.y0) * sy) - 3)
        assert pix.color_topusage(clip=clip)[0] > 0.99
    # y al auditar la copia ya no aparece: el recuadro sigue, pero debajo no queda nada que recuperar
    assert censura.auditar(out) == []


def test_auditar_copias_censuradas_sin_falsas_alarmas(lista, tmp_path):
    res = censura.buscar(lista, patrones=ACTIVOS, palabras=["María José"])
    out = tmp_path / "censurado.pdf"
    censura.censurar(lista, out, res)
    assert censura.auditar(out) == []
    # un recuadro que solo roza el borde de una letra vecina no es una falsa censura…
    path = tmp_path / "roza.pdf"
    with pymupdf.open() as d:
        p = d.new_page()
        p.insert_text((72, 100), "Gómez Peña", fontsize=12)
        g = p.search_for("Gómez")[0]
        p.draw_rect(pymupdf.Rect(g.x0 - 40, g.y0, g.x0 + 3, g.y1), color=(0, 0, 0), fill=(0, 0, 0))
        p.insert_text((72, 160), "Clave 4821 del cajero", fontsize=12)
        n = p.search_for("4821")[0]
        p.draw_rect(n + (-1, -1, 1, 1), color=(0, 0, 0), fill=(0, 0, 0))     # …pero tapar un dato sí
        d.save(path)
    falsas = censura.auditar(path)
    assert [h.texto for h in falsas] == ["4821"]


def test_texto_de_restos():
    lineas = censura_ui._texto_restos([
        censura.Resto("metadatos", "Juan Pérez"), censura.Resto("comentario de la página 2", "Juan Pérez"),
        censura.Resto("página 3", "zona marcada a mano", "zona"), censura.Resto("página 1", "Cooperativa", "zona")])
    assert lineas == ["El texto «Juan Pérez» sigue en: metadatos, comentario de la página 2",
                      "La zona marcada a mano en la página 3 no quedó de un solo color.",
                      "La zona de «Cooperativa» en la página 1 no quedó de un solo color."]


# ---------------------------------------------------------------- datos ocultos

def _pdf_con_ocultos(path, password=None):
    with pymupdf.open() as d:
        p = d.new_page()
        p.insert_text((72, 100), "Informe confidencial", fontsize=14)
        p.insert_text((72, 140), "Texto del OCR", fontsize=12, render_mode=3)
        p.add_text_annot((400, 80), "Revisar con María")
        p.add_file_annot((400, 200), b"anexo", "anexo.txt")
        d.embfile_add("lista.txt", b"cedula 1032456789", filename="lista.txt")
        d.set_metadata({"author": "María Gómez", "title": "Listado de beneficiarios", "creator": "Word"})
        d.xref_set_key(d.pdf_catalog(), "OpenAction", "<</S/JavaScript/JS(app.alert\\(1\\);)>>")
        opts = dict(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=password, owner_pw="x") if password else {}
        d.save(path, **opts)
    return path


def test_datos_ocultos_y_limpiar(tmp_path):
    path = _pdf_con_ocultos(tmp_path / "oculto.pdf")
    datos = {x.clave: x for x in censura.datos_ocultos(path)}
    assert {"metadatos", "adjuntos", "comentarios", "javascript", "texto_invisible"} <= set(datos)
    assert "Autor: María Gómez" in datos["metadatos"].detalle
    assert datos["adjuntos"].cantidad == 2 and "lista.txt" in datos["adjuntos"].detalle
    assert datos["comentarios"].cantidad == 1 and "nota" in datos["comentarios"].detalle
    for k in ("metadatos", "adjuntos", "comentarios", "javascript"):
        assert datos[k].recomendado
    assert not datos["texto_invisible"].recomendado
    assert censura.NOTA_TEXTO_INVISIBLE in datos["texto_invisible"].detalle
    out = tmp_path / "limpio.pdf"
    hecho = censura.limpiar(path, out, ["metadatos", "adjuntos", "comentarios", "javascript"])
    assert hecho["metadatos"] >= 3 and hecho["adjuntos"] == 2 and hecho["comentarios"] == 1
    assert hecho["javascript"] >= 1
    with pymupdf.open(out) as d:
        meta = d.metadata
        assert not meta["author"] and not meta["title"] and not meta["creator"]
        assert d.get_xml_metadata() == "" and d.embfile_count() == 0
        assert list(d[0].annots()) == []
        assert censura._xrefs_js(d) == [] and d.xref_get_key(d.pdf_catalog(), "OpenAction")[0] == "null"
        assert "Informe confidencial" in d[0].get_text()
        assert "Texto del OCR" in d[0].get_text()          # no se eligió quitar el texto invisible
    restantes = {x.clave for x in censura.datos_ocultos(out)}
    assert restantes == {"texto_invisible"}


def test_limpiar_javascript_por_referencia(tmp_path):
    """/OpenAction, /AA y /Next que apuntan a la acción con una referencia: la limpieza queda en cero."""
    path = tmp_path / "scripts.pdf"
    with pymupdf.open() as d:
        p = d.new_page()
        p.insert_text((72, 72), "Documento con scripts", fontsize=12)
        js = []
        for k in range(4):
            x = d.get_new_xref()
            d.update_object(x, f"<</S/JavaScript/JS(app.alert\\({k}\\))>>")
            js.append(x)
        d.xref_set_key(d.pdf_catalog(), "OpenAction", f"{js[0]} 0 R")
        d.xref_set_key(p.xref, "AA", f"<</O {js[1]} 0 R>>")
        ir = d.get_new_xref()                                  # una acción normal que encadena un script
        d.update_object(ir, f"<</S/GoTo/D[{p.xref} 0 R/Fit]/Next {js[2]} 0 R>>")
        aa = d.get_new_xref()
        d.update_object(aa, f"<</WC {js[3]} 0 R/WS {ir} 0 R>>")
        d.xref_set_key(d.pdf_catalog(), "AA", f"{aa} 0 R")
        d.save(path)
    assert {x.clave: x.cantidad for x in censura.datos_ocultos(path)}["javascript"] == 4
    out = tmp_path / "scripts_limpio.pdf"
    assert censura.limpiar(path, out, list(censura.ORDEN_OCULTOS))["javascript"] == 4
    assert "javascript" not in {x.clave for x in censura.datos_ocultos(out)}
    with pymupdf.open(out) as d:
        assert d.xref_get_key(d.pdf_catalog(), "OpenAction")[0] == "null"
        assert censura._xrefs_js(d) == []
        assert b"app.alert" not in d.tobytes(expand=255)
        assert "Documento con scripts" in d[0].get_text()
    # una acción ya vaciada no cuenta como JavaScript
    with pymupdf.open() as d:
        d.new_page()
        x = d.get_new_xref()
        d.update_object(x, "<</S/JavaScript/JS()>>")
        d.xref_set_key(d.pdf_catalog(), "OpenAction", f"{x} 0 R")
        assert censura._xrefs_js(d) == []


def test_limpiar_texto_invisible_formularios_censuras_y_contrasena(tmp_path):
    path = tmp_path / "varios.pdf"
    with pymupdf.open() as d:
        p = d.new_page()
        p.insert_text((72, 100), "Texto visible", fontsize=12)
        p.insert_text((72, 140), "Texto del OCR", fontsize=12, render_mode=3)
        p.insert_text((72, 180), "Dato pendiente 555", fontsize=12)
        p.add_redact_annot(p.search_for("pendiente 555")[0])
        w = pymupdf.Widget()
        w.field_name = "nombre"
        w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        w.rect = pymupdf.Rect(72, 300, 300, 320)
        w.field_value = "Ana Ruiz"
        p.add_widget(w)
        d.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="abc", owner_pw="x")
    datos = {x.clave: x for x in censura.datos_ocultos(path, "abc")}
    assert datos["censuras"].recomendado and not datos["formularios"].recomendado
    out = tmp_path / "varios_limpio.pdf"
    hecho = censura.limpiar(path, out, ["texto_invisible", "formularios", "censuras"], password="abc")
    assert hecho == {"texto_invisible": 3, "formularios": 1, "censuras": 1}
    assert core.needs_password(out)
    with core.open_pdf(out, "abc") as d:
        texto = d[0].get_text()
        assert "Texto visible" in texto and "Texto del OCR" not in texto and "pendiente" not in texto
        assert "Ana Ruiz" in texto and not list(d[0].widgets())   # el valor sigue visible, aplanado
    with pytest.raises(core.UserError):
        censura.limpiar(path, tmp_path / "nada.pdf", [], password="abc")


def test_limpiar_enlaces_marcadores_y_miniaturas(tmp_path):
    path = tmp_path / "navegable.pdf"
    with pymupdf.open() as d:
        for k in range(2):
            d.new_page().insert_text((72, 100), f"Capítulo {k + 1}", fontsize=14)
        d[0].insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(72, 80, 200, 105),
                          "uri": "https://fundacion.example/beneficiarios"})
        d.set_toc([[1, "Capítulo 1", 1], [1, "Capítulo 2", 2]])
        xref = d.get_new_xref()
        d.update_object(xref, "<</Type/XObject/Subtype/Image/Width 1/Height 1/ColorSpace/DeviceGray"
                              "/BitsPerComponent 8>>")
        d.update_stream(xref, b"\x00")
        d.xref_set_key(d[0].xref, "Thumb", f"{xref} 0 R")
        d.save(path)
    datos = {x.clave: x for x in censura.datos_ocultos(path)}
    assert datos["miniaturas"].recomendado and datos["miniaturas"].cantidad == 1
    assert not datos["enlaces"].recomendado and "fundacion.example" in datos["enlaces"].detalle
    assert not datos["marcadores"].recomendado and datos["marcadores"].cantidad == 2
    out = tmp_path / "navegable_limpio.pdf"
    hecho = censura.limpiar(path, out, ["enlaces", "marcadores", "miniaturas"])
    assert hecho == {"enlaces": 1, "marcadores": 2, "miniaturas": 1}
    assert censura.datos_ocultos(out) == []
    with pymupdf.open(out) as d:
        assert "Capítulo 2" in d[1].get_text()


def test_cargar_lista_de_palabras(tmp_path):
    f = tmp_path / "nombres.txt"
    f.write_bytes("# beneficiarios\nMaría Gómez\n\n  José Peña  \n".encode("cp1252"))
    assert censura_ui._cargar_lista(str(f)) == ["María Gómez", "José Peña"]


# ---------------------------------------------------------------- interfaz

@pytest.fixture(scope="module")
def win():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setPalette(_light_palette())
    app.setStyleSheet(QSS)
    w = MainWindow()
    w.resize(1280, 860)
    w.show()
    app.processEvents()
    return w


@pytest.fixture
def done(monkeypatch):
    calls = []

    def fake_done(parent, title, detail="", file=None, folder=None):
        calls.append((title, detail, file, folder))

    monkeypatch.setattr(censura_ui, "show_done", fake_done)
    monkeypatch.setattr(widgets, "show_done", fake_done)
    monkeypatch.setattr(censura_ui, "_aviso_restos",
                        lambda parent, titulo, texto, detalle: calls.append(("restos", titulo, detalle)) and False)
    monkeypatch.setattr(censura_ui, "ask_open", lambda *a, **k: [])
    monkeypatch.setattr(widgets.QMessageBox, "warning", lambda *a, **k: calls.append(("warning", a[2])))
    monkeypatch.setattr(widgets.QMessageBox, "information", lambda *a, **k: calls.append(("info", a[2])))
    monkeypatch.setattr(widgets.QMessageBox, "question", lambda *a, **k: widgets.QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(widgets.QFileDialog, "getSaveFileName", lambda *a, **k: ("", ""))
    monkeypatch.setattr(widgets.QFileDialog, "getOpenFileName", lambda *a, **k: ("", ""))
    monkeypatch.setattr(widgets.QInputDialog, "getText", lambda *a, **k: ("", False))
    return calls


def wait(timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        QApplication.processEvents()
        if not widgets.ctx.busy:
            QApplication.processEvents()
            return
        time.sleep(0.01)
    raise TimeoutError


def _mouse(widget, kind, pos):
    types = {"press": QEvent.Type.MouseButtonPress, "move": QEvent.Type.MouseMove,
             "release": QEvent.Type.MouseButtonRelease}
    btn = Qt.MouseButton.LeftButton if kind != "move" else Qt.MouseButton.NoButton
    held = Qt.MouseButton.LeftButton if kind != "release" else Qt.MouseButton.NoButton
    local = QPointF(pos)
    ev = QMouseEvent(types[kind], local, widget.mapToGlobal(local), btn, held, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, ev)
    QApplication.processEvents()


def test_registro_menu_busqueda_e_inicio(win):
    assert isinstance(win.pages["redact"], censura_ui.RedactPage)
    assert isinstance(win.pages["sanitize"], censura_ui.SanitizePage)
    seguridad = next(s for s in gui.SECTIONS if s[0] == "seguridad")
    claves = [k for k, *_ in seguridad[3]]
    assert claves.index("redact") < claves.index("sanitize")
    sb = win.sidebar
    win.navigate("home")
    sb.set_collapsed(False)
    sb.search.setText("censurar")
    assert sb.items["redact"].isVisible() and not sb.items["merge"].isVisible()
    sb.search.setText("ocultos")
    assert sb.items["sanitize"].isVisible()
    sb.search.clear()
    assert ("redact", "redact", "Censurar datos", "Borra cédulas y datos personales") in extensiones.inicio()
    assert not icons.pixmap("redact", "#1F2937", 24).isNull() and not icons.pixmap("sanitize").isNull()


def test_censurar_desde_la_pantalla(win, done, lista, tmp_path, monkeypatch):
    page = win.pages["redact"]
    win.navigate("redact")
    page.accept_files([str(lista)])
    wait()
    assert page.tarjeta_resultados.isVisibleTo(page) and not page.aviso_falsas.isVisibleTo(page)
    assert not page.action.isEnabled()
    page.buscar_ahora()
    wait()
    assert {h.categoria for h in page.hallazgos} == {"cedula", "nit", "celular", "correo", "cuenta", "direccion"}
    assert page.arbol.topLevelItemCount() == 6 and page.action.isEnabled()
    assert page._chips.itemAt(0).widget().text() == "6 datos en 1 página"     # chip del resumen
    i = next(k for k, h in enumerate(page.hallazgos) if h.categoria == "correo")
    page.set_incluir(i, False)
    assert page._items[i].checkState(0) == Qt.CheckState.Unchecked
    assert page._chips.itemAt(0).widget().text() == "5 datos en 1 página"
    # cada hallazgo se ve sobre la página; un clic en la marca lo vuelve a incluir
    page.vista.mostrar(0)
    QApplication.processEvents()
    assert len(page.vista.marcas()) == 6
    centro = page.vista.lienzo.a_pantalla(page.hallazgos[i].rect).center()
    _mouse(page.vista.lienzo, "press", centro)
    _mouse(page.vista.lienzo, "release", centro)
    assert page.hallazgos[i].incluir and page._items[i].checkState(0) == Qt.CheckState.Checked
    page._items[i].setCheckState(0, Qt.CheckState.Unchecked)          # desde la lista
    assert not page.hallazgos[i].incluir
    # una zona a mano, arrastrando sobre la página
    page.vista.b_zona.setChecked(True)
    lz = page.vista.lienzo
    a, b = lz.a_pantalla((60, 600, 220, 680)).topLeft(), lz.a_pantalla((60, 600, 220, 680)).bottomRight()
    _mouse(lz, "press", a)
    _mouse(lz, "move", (a + b) / 2)
    _mouse(lz, "release", b)
    page.vista.b_zona.setChecked(False)
    manual = page.hallazgos[-1]
    assert manual.categoria == "manual" and manual.origen == "manual"
    assert abs(manual.rect[0] - 60) < 2 and abs(manual.rect[3] - 680) < 2
    page.agregar_zona(0, (300, 700, 400, 760))
    assert sum(h.categoria == "manual" for h in page.hallazgos) == 2
    assert page.has_unsaved()
    out = tmp_path / "beneficiarios_censurado.pdf"
    monkeypatch.setattr(censura_ui, "ask_save", lambda *a, **k: str(out))
    page.run()
    wait()
    assert done[-1][0] == "Documento censurado", done
    assert done[-1][1] == ("Se borraron 7 datos en 1 página. Verificación: ninguno de esos textos sigue en "
                           "el archivo.")
    assert done[-1][2] == str(out)
    with pymupdf.open(out) as d:
        texto = d[0].get_text()
    assert "1.032.456.789" not in texto and "maria.gomez@correo.com" in texto and "Fin del listado" in texto
    assert not page.has_unsaved()


def test_auditoria_regex_y_restos_en_pantalla(win, done, tmp_path, monkeypatch):
    path = tmp_path / "acta.pdf"
    with pymupdf.open() as d:
        p = d.new_page()
        p.insert_text((72, 100), "Cédula del titular 1032456789", fontsize=12)
        p.draw_rect(p.search_for("1032456789")[0] + (-2, -3, 2, 3), color=(0, 0, 0), fill=(0, 0, 0))
        p.insert_text((72, 140), "Beneficiario: Juan Pérez", fontsize=12)
        d.set_metadata({"title": "Acta de Juan Pérez"})
        d.save(path)
    page = win.pages["redact"]
    win.navigate("redact")
    page.accept_files([str(path)])
    wait()
    assert page.aviso_falsas.isVisibleTo(page)
    assert page.aviso_falsas_texto.text() == ("Este PDF tiene 1 recuadro que tapa texto sin borrarlo: lo que hay "
                                              "debajo todavía se puede copiar.")
    page.b_falsas.click()
    assert [h.categoria for h in page.hallazgos] == ["falsa"] and not page.aviso_falsas.isVisibleTo(page)
    # expresión mal escrita: aviso en la pantalla, sin cerrarla ni lanzar la búsqueda
    page.regex.setText("([")
    page.buscar_ahora()
    assert not widgets.ctx.busy and page.regex_error.text().startswith("La expresión no es válida")
    page.regex.setText("")
    casillas = {k: cb.isChecked() for k, cb in page.casillas.items()}
    try:
        for cb in page.casillas.values():
            cb.setChecked(False)
        page.palabras.setPlainText("juan perez")
        page.buscar_ahora()
        wait()
        assert sorted(h.categoria for h in page.hallazgos) == ["falsa", "palabra"]   # la falsa se conserva
        out = tmp_path / "acta_censurada.pdf"
        monkeypatch.setattr(censura_ui, "ask_save", lambda *a, **k: str(out))
        page.run()
        wait()
        assert done[-1][0] == "restos", done
        assert "El texto «Juan Pérez» sigue en: metadatos" in done[-1][2]
        with pymupdf.open(out) as d:
            texto = d[0].get_text()
        assert "1032456789" not in texto and "Juan Pérez" not in texto
    finally:
        page.palabras.setPlainText("")
        for k, cb in page.casillas.items():
            cb.setChecked(casillas[k])


def test_pantalla_documento_escaneado(win, done, scanned):
    if not core.ocr_languages():
        pytest.skip("no hay modelos de OCR")
    page = win.pages["redact"]
    win.navigate("redact")
    page.accept_files([str(scanned)])
    wait()
    assert page.aviso_escaneado.isVisibleTo(page) and page.aviso_escaneado.text() == censura_ui.AVISO_ESCANEADO
    casillas = {k: cb.isChecked() for k, cb in page.casillas.items()}
    try:
        for cb in page.casillas.values():
            cb.setChecked(False)
        page.palabras.setPlainText("Cooperativa")
        page.buscar_ahora()
        wait()
        assert len(page.hallazgos) == 2 and all(h.origen == "ocr" for h in page.hallazgos)
        assert all(it.text(1) == "OCR: revisa" for it in page._items.values())
        page.palabras.setPlainText("palabra que no existe")
        page.buscar_ahora()
        wait()
        assert page.hallazgos == [] and page.resumen.text() == censura_ui.SIN_RESULTADOS
    finally:
        page.palabras.setPlainText("")
        for k, cb in page.casillas.items():
            cb.setChecked(casillas[k])


def test_quitar_datos_ocultos_en_pantalla(win, done, tmp_path, monkeypatch):
    path = _pdf_con_ocultos(tmp_path / "oculto.pdf")
    page = win.pages["sanitize"]
    win.navigate("sanitize")
    page.accept_files([str(path)])
    wait()
    filas = {page.tabla.topLevelItem(k).data(0, Qt.ItemDataRole.UserRole): page.tabla.topLevelItem(k)
             for k in range(page.tabla.topLevelItemCount())}
    assert {"metadatos", "adjuntos", "comentarios", "javascript", "texto_invisible"} <= set(filas)
    assert "Autor: María Gómez" in filas["metadatos"].text(2)
    assert filas["metadatos"].checkState(0) == Qt.CheckState.Checked
    assert filas["texto_invisible"].checkState(0) == Qt.CheckState.Unchecked
    assert page.nota_ocr.isVisibleTo(page) and page.action.isEnabled()
    out = tmp_path / "oculto_limpio.pdf"
    monkeypatch.setattr(censura_ui, "ask_save", lambda *a, **k: str(out))
    page.run()
    wait()
    assert done[-1][0] == "Datos ocultos eliminados" and done[-1][2] == str(out), done
    with pymupdf.open(out) as d:
        assert not d.metadata["author"] and d.embfile_count() == 0 and list(d[0].annots()) == []
        assert "Texto del OCR" in d[0].get_text()
    # un PDF sin nada oculto
    limpio = tmp_path / "sin_nada.pdf"
    with pymupdf.open() as d:
        d.new_page().insert_text((72, 100), "Hola", fontsize=12)
        d.set_metadata({})
        d.save(limpio)
    page.accept_files([str(limpio)])
    wait()
    assert page.vacio.isVisibleTo(page) and page.vacio.text() == "Este PDF no tiene datos ocultos."
    assert not page.action.isEnabled()


def _sin_recortes(page):
    """Ningún botón ni texto de una línea queda más angosto de lo que pide; la página y su panel caben enteros."""
    from PySide6.QtWidgets import QAbstractButton, QLabel, QLineEdit
    malos = []
    if page.minimumSizeHint().width() > page.width():
        malos.append(f"página: {page.minimumSizeHint().width()} > {page.width()}")
    panel = page.panel_scroll
    if panel is not None and panel.widget().minimumSizeHint().width() > panel.viewport().width():
        malos.append(f"panel: {panel.widget().minimumSizeHint().width()} > {panel.viewport().width()}")
    for w in page.findChildren(QAbstractButton):
        if isinstance(w.parent(), QLineEdit):
            continue
        if w.isVisible() and w.width() + 1 < w.minimumSizeHint().width():
            malos.append(f"{type(w).__name__} «{getattr(w, 'text', lambda: '')()}»")
    for lb in page.findChildren(QLabel):
        if (lb.isVisible() and not lb.wordWrap() and lb.text() and lb.sizePolicy().horizontalPolicy().name != "Ignored"
                and lb.width() + 1 < lb.sizeHint().width() and lb.pixmap().isNull()):
            malos.append(f"QLabel «{lb.text()[:40]}»")
    return malos


def test_fichas_relleno_y_vistas_de_censurar(win, done, lista):
    page = win.pages["redact"]
    win.navigate("redact")
    page.accept_files([str(lista)])
    wait()
    page.buscar_ahora()
    wait()
    correo = page.casillas["correo"]
    n = sum(h.categoria == "correo" for h in page.hallazgos)
    assert n and correo.count == n and page.casillas["fecha"].count is None      # «Fechas» no se buscó
    assert page.chip_total.isVisibleTo(page) and page.chip_total.text() == f"{len(page.hallazgos)} datos"
    # la ficha de un tipo ya buscado deja o vuelve a incluir todos sus hallazgos
    correo.setChecked(False)
    idx = [i for i, h in enumerate(page.hallazgos) if h.categoria == "correo"]
    assert not any(page.hallazgos[i].incluir for i in idx)
    assert all(page._items[i].checkState(0) == Qt.CheckState.Unchecked for i in idx)
    correo.setChecked(True)
    assert all(page.hallazgos[i].incluir for i in idx)
    # el relleno se ve enseguida en «Cómo quedará»; marcar a mano vuelve a «Lo encontrado»
    page.relleno.set_index(1)
    assert page.vista.despues and page.vista.seg_vista.value() == "despues" and page.relleno.value() == (1, 1, 1)
    page.vista.b_zona.setChecked(True)
    assert not page.vista.despues
    page.vista.b_zona.setChecked(False)
    page.relleno.set_index(0)


def test_censurar_y_limpiar_caben_a_1000_px(win, done, lista):
    win.resize(1000, 660)
    win.sidebar.set_collapsed(True, remember=False)
    try:
        for clave in ("redact", "sanitize"):
            page = win.pages[clave]
            win.navigate(clave)
            page.accept_files([str(lista)])
            wait()
            if clave == "redact":
                page.buscar_ahora()
                wait()
                page.b_avanzadas.setChecked(True)            # sus campos también caben en el panel
            for _ in range(5):
                QApplication.processEvents()
            assert _sin_recortes(page) == [], clave
        assert win.pages["redact"].vista._compacta == 1        # «Marcar zona a mano» queda solo con el ícono
        win.sidebar.set_collapsed(False, remember=False)     # con el menú abierto, la navegación baja de fila
        win.navigate("redact")
        for _ in range(5):
            QApplication.processEvents()
        assert win.pages["redact"].vista._compacta == 2 and _sin_recortes(win.pages["redact"]) == []
    finally:
        win.pages["redact"].b_avanzadas.setChecked(False)
        win.sidebar.set_collapsed(True, remember=False)
        win.resize(1280, 860)


def test_censurar_desde_el_visor(win, done, lista):
    visor = win.pages["viewer"]
    win.navigate("viewer")
    assert visor.load(str(lista), confirm=False)
    censura_ui._censurar_desde_visor(visor)
    wait()
    page = win.pages["redact"]
    assert win.stack.currentWidget() is page and page.picker.path == str(lista)
