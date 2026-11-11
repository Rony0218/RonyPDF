"""OCR de primer nivel: escaneos con sello, diccionario, enderezar, girar, limpiar, confianza, carpetas,
contraseñas e interfaz sin pantalla («Reconocer texto (OCR)» y «Revisar texto reconocido»)."""

import csv
import math
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pymupdf
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Los ajustes y el diccionario personal de las pruebas nunca tocan los reales.
os.environ["RONYPDF_SETTINGS"] = os.path.join(tempfile.mkdtemp(prefix="ronypdf-ocr-"), "RonyPDF.json")

from conftest import Protegido, _photo_bytes  # noqa: E402
from ronypdf import core, ocr  # noqa: E402

pytestmark = pytest.mark.skipif(not core.ocr_languages(), reason="no hay modelos de OCR")
SHOTS = os.environ.get("RONYPDF_SHOTS")
WINDOWS = sys.platform == "win32"

LINEAS = [
    "Informe de gestión de la cooperativa de caficultores del municipio",
    "La niñez y la educación son prioridad en el plan de desarrollo",
    "Factura número 004512 del 12/03/2025 por un valor de $ 1.250.000",
    "Contacto: ana.gomez@correo.com y cédula 1.032.456.789",
]
SELLO = "Radicado 2025-00123"
SELLO_LARGO = "Radicado No. 2025-00123 del 09/11/2025"    # más de 20 letras: antes engañaba a page_has_text


class Cancelado(Exception):
    """Como widgets.Cancelled (el motor no importa la interfaz: reconoce la cancelación por el nombre)."""


Cancelado.__name__ = "Cancelled"


# ---------------------------------------------------------------- documentos de prueba

def _digital(renglones: int = 20) -> pymupdf.Document:
    d = pymupdf.open()
    p = d.new_page(width=595, height=842)
    y = 90
    for k in range(renglones):
        p.insert_text((60, y), LINEAS[k % 4], fontsize=11)
        y += 34
    return d


def _rasterizar(pagina_pdf: pymupdf.Page, ancho: float, alto: float, dpi: int = 200) -> pymupdf.Document:
    pix = pagina_pdf.get_pixmap(dpi=dpi)
    d = pymupdf.open()
    q = d.new_page(width=ancho, height=alto)
    q.insert_image(q.rect, pixmap=pix)
    return d


def escaneo(angulo: float = 0.0, giro: int = 0, gris: bool = False, renglones: int = 20, sello: str | None = None):
    """Una página con renglones en español rasterizada a 200 ppp; torcida ``angulo`` grados, de lado o al
    revés (``giro``), con fondo gris o con un sello digital encima.  Devuelve (documento, página vectorial
    con la misma geometría, para saber dónde están de verdad las palabras)."""
    src = _digital(renglones)
    if gris:
        g = pymupdf.open()
        q = g.new_page(width=595, height=842)
        q.draw_rect(q.rect, color=None, fill=(0.8, 0.8, 0.8))
        q.show_pdf_page(q.rect, src, 0)
        src = g
    vector = pymupdf.open()
    w, h = (842, 595) if giro in (90, 270) else (595, 842)
    v = vector.new_page(width=w, height=h)
    v.show_pdf_page(v.rect, src, 0, rotate=giro or angulo)
    d = _rasterizar(v, w, h)
    if sello:
        d[0].insert_text((380, 40), sello, fontsize=9, color=(0, 0, 1))
    return d, vector


@pytest.fixture(scope="module")
def docs(tmp_path_factory):
    base = tmp_path_factory.mktemp("ocr")
    out = {}
    for nombre, kw in {"derecho": {}, "torcido": {"angulo": 3.5}, "torcido_neg": {"angulo": -3.5},
                       "lado": {"giro": 90, "renglones": 8}, "reves": {"giro": 180, "renglones": 8},
                       "gris": {"gris": True, "renglones": 8},
                       "sello": {"angulo": 3.5, "sello": SELLO}}.items():
        d, vector = escaneo(**kw)
        path = base / f"{nombre}.pdf"
        d.save(path)
        vector.save(base / f"{nombre}_vector.pdf")
        out[nombre] = path
    blanco = base / "blanco.pdf"
    with pymupdf.open() as d:
        d.new_page()
        d.save(blanco)
    out["blanco"] = blanco
    out["dir"] = base
    return out


def _verdad(path_vector) -> dict[str, list[tuple[float, float]]]:
    """Centros reales de las palabras (de la página vectorial con la que se hizo el escaneo)."""
    out: dict[str, list[tuple[float, float]]] = {}
    with pymupdf.open(path_vector) as d:
        for w in d[0].get_text("words"):
            out.setdefault(w[4], []).append(((w[0] + w[2]) / 2, (w[1] + w[3]) / 2))
    return out


def _distancias(cajas: list[tuple[str, tuple]], verdad) -> list[float]:
    out = []
    for texto, (x0, y0, x1, y1) in cajas:
        if len(texto) >= 6 and texto in verdad:
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            out.append(min(math.hypot(cx - a, cy - b) for a, b in verdad[texto]))
    return sorted(out)


def _con_sello(scanned, path):
    with pymupdf.open(scanned) as d:
        for p in d:
            p.insert_text((300, 30), SELLO_LARGO, fontsize=9)
        d.save(path)
    return path


@pytest.fixture
def personal(tmp_path, monkeypatch):
    ruta = tmp_path / ocr.NOMBRE_PERSONAL
    monkeypatch.setattr(ocr, "ruta_personal", ruta)
    return ruta


# ---------------------------------------------------------------- core: escaneos con sello

def test_core_escaneo_con_sello_ya_no_se_salta(scanned, samples, tmp_path):
    sellado = _con_sello(scanned, tmp_path / "sellado.pdf")
    mixto = tmp_path / "mixto.pdf"
    with pymupdf.open() as d:
        d.insert_pdf(pymupdf.open(scanned), from_page=0, to_page=0)       # escaneo puro
        d.insert_pdf(pymupdf.open(sellado), from_page=0, to_page=0)       # escaneo con sello digital
        d.insert_pdf(pymupdf.open(samples["a"]), from_page=0, to_page=0)  # página digital
        d.save(mixto)
    with pymupdf.open(sellado) as d:
        assert core.page_has_text(d[0]) and core.page_needs_ocr(d[0])
        assert core.looks_scanned(d)                                     # el visor ya muestra su aviso
    with pymupdf.open(samples["a"]) as d:
        assert not core.looks_scanned(d)
    out = tmp_path / "mixto_ocr.pdf"
    paginas, palabras = core.ocr_pdf(mixto, out, "spa")
    assert paginas == 2 and palabras >= 8
    with pymupdf.open(out) as d:
        assert "niñez" in d[0].get_text() and "niñez" in d[1].get_text()
        assert d[1].get_text().count("Radicado") == 1                    # el sello no queda duplicado
        assert d[2].get_text() == pymupdf.open(samples["a"])[0].get_text()
        assert not core.looks_scanned(d) and not any(core.page_needs_ocr(p) for p in d)
    # segunda pasada: las páginas ya traen su capa invisible
    assert core.ocr_pdf(out, tmp_path / "otra.pdf", "spa") == (0, 0)
    # «todas las páginas» tampoco duplica: lo que ya está como texto no se vuelve a escribir
    core.ocr_pdf(out, tmp_path / "todas.pdf", "spa", only_missing=False, spec="2")
    with pymupdf.open(tmp_path / "todas.pdf") as d:
        assert d[1].get_text().count("Radicado") == 1 and d[1].get_text().count("niñez") == 1


def test_core_ocr_text_escaneo_con_sello(scanned, tmp_path):
    sellado = _con_sello(scanned, tmp_path / "sellado.pdf")
    txt = tmp_path / "sellado.txt"
    core.ocr_text(sellado, txt, "spa", spec="1")
    texto = txt.read_text(encoding="utf-8-sig")
    assert texto.count("Radicado") == 1 and "Cooperativa" in texto and "educación" in texto
    assert texto.index("Radicado") < texto.index("Cooperativa")           # en orden de lectura


# ---------------------------------------------------------------- diccionario

def test_diccionario_sale_del_modelo_rapido_y_en_cache():
    ocr.diccionario.cache_clear()
    t = time.time()
    dic = ocr.diccionario("spa")
    assert time.time() - t < 1.0
    # 179 447 entradas del modelo (con mayúsculas y minúsculas) → unas 100 000 palabras distintas sin mayúsculas
    assert len(ocr._palabras_modelo(Path(core.tessdata_dir()) / "spa.traineddata")) > 170_000
    assert len(dic) > 90_000
    for palabra in ("niñez", "educación", "cédula", "factura", "gestión", "bogotá"):
        assert palabra in dic, palabra
    assert "xqztrabajo" not in dic and "cooperatlva" not in dic
    t = time.time()
    assert ocr.diccionario("spa") is dic and time.time() - t < 0.01                  # caché
    assert ocr.diccionario.cache_info().hits >= 1
    assert len(ocr.diccionario("spa+eng")) > len(dic)                                # suma el inglés


def test_diccionario_contenedor_y_modelo_sin_dawg(tmp_path, monkeypatch):
    real = Path(core.tessdata_dir()) / "spa.traineddata"
    comp = ocr._componentes(real)
    assert ocr._DAWG in comp and ocr._UNICHARSET in comp
    chars = ocr._unicharset(comp[ocr._UNICHARSET])
    assert " " in chars and "ñ" in chars
    # un modelo sin «lstm-word-dawg»: ese idioma no aporta palabras, y no es un error
    import struct
    datos = b"version 1\n"
    n = 24
    offs = [-1] * n
    offs[23] = 4 + 8 * n
    falso = tmp_path / "xyz.traineddata"
    falso.write_bytes(struct.pack("<i", n) + struct.pack(f"<{n}q", *offs) + datos)
    monkeypatch.setattr(core, "tessdata_dir", lambda: str(tmp_path))
    assert ocr._palabras_modelo(falso) == set()
    assert ocr._diccionario("xyz", str(tmp_path)) == frozenset()
    (tmp_path / "roto.traineddata").write_bytes(b"no es un modelo")
    assert ocr._diccionario("roto", str(tmp_path)) == frozenset()


def test_diccionario_personal(personal):
    assert ocr.diccionario_personal() == frozenset()
    c, motivos = ocr.confianza("Guaviyú", otra_lectura="Guaviyú")
    assert c < 0.8 and ocr.M_DICCIONARIO in motivos
    assert ocr.agregar_al_diccionario("Guaviyú") and not ocr.agregar_al_diccionario("guaviyú")
    assert "guaviyú" in ocr.diccionario_personal()
    assert ocr.confianza("Guaviyú") == (1.0, [])
    assert personal.read_text(encoding="utf-8").splitlines() == ["guaviyú"]


# ---------------------------------------------------------------- confianza y sugerencias

def test_confianza_estimada():
    dic = ocr.diccionario()
    vacio = frozenset()
    seguro = ocr.confianza("educación", otra_lectura="educación", dic=dic, personal=vacio)
    assert seguro == (1.0, [])
    for texto in ("c0mo", "niÑez", "|", "~", "gesti¬n"):
        c, motivos = ocr.confianza(texto, otra_lectura=texto, dic=dic, personal=vacio)
        assert c < 0.5 and motivos, (texto, c, motivos)
    for texto in ("$1.250.000", "12/03/2025", "004512", "1.032.456.789", "ana.gomez@correo.com", "10%",
                  "NIT", "ICBF", "y", "a"):
        assert ocr.confianza(texto, otra_lectura=texto, dic=dic, personal=vacio) == (1.0, []), texto
    c, motivos = ocr.confianza("niñez", otra_lectura="niñes", dic=dic, personal=vacio)
    assert 0.5 <= c < 0.8 and motivos == [ocr.M_DIFIEREN]
    c, motivos = ocr.confianza("niñez", falta_en_otra=True, dic=dic, personal=vacio)
    assert c == 0.75 and motivos == [ocr.M_FALTA]
    c, motivos = ocr.confianza("cooperatlva", otra_lectura="cooperativa", dic=dic, personal=vacio)
    assert c < 0.5 and ocr.M_DICCIONARIO in motivos
    c, motivos = ocr.confianza("q", dic=dic, personal=vacio)
    assert c < 1.0 and motivos == [ocr.M_SUELTA]
    assert ocr.nivel(0.9) == "segura" and ocr.nivel(0.6) == "dudosa" and ocr.nivel(0.3) == "muy dudosa"


def test_otra_separacion_no_es_otra_lectura():
    # «niñez y» en una lectura y «niñezy» en la otra: mismas letras, solo cambia el espacio
    a = [(10, 10, 40, 20, "niñez", 0, 0, 0), (42, 10, 48, 20, "y", 0, 0, 1)]
    b = [(10, 10, 48, 20, "niñezy", 0, 0, 0)]
    puntos = ocr._puntuar(a, b, ocr.diccionario(), frozenset())
    assert [c for c, _m, _o in puntos] == [1.0, 1.0]
    assert puntos[0][2] == "niñezy"                       # la otra lectura queda para mostrarla
    b = [(10, 10, 48, 20, "nifiezy", 0, 0, 0)]
    assert ocr._puntuar(a, b, ocr.diccionario(), frozenset())[0][0] < 0.8


def test_sugerencias():
    assert "como" in ocr.sugerencias("c0mo")
    assert "educación" in ocr.sugerencias("educasión")
    assert ocr.sugerencias("Cooperatlva")[0] == "Cooperativa"            # conserva la mayúscula
    assert ocr.sugerencias("12/03/2025") == [] and len(ocr.sugerencias("niñes")) <= 3
    t = time.time()
    for _ in range(20):
        ocr.sugerencias("gestlón")
    assert time.time() - t < 2.0


# ---------------------------------------------------------------- enderezar, girar y limpiar

def test_detectar_inclinacion(docs):
    def medir(nombre):
        with pymupdf.open(docs[nombre]) as d:
            return ocr.detectar_inclinacion(d[0])
    assert abs(medir("derecho")) < ocr.INCLINACION_MIN
    assert 3.2 <= medir("torcido") <= 3.8                 # positiva: los renglones suben hacia la derecha
    assert -3.8 <= medir("torcido_neg") <= -3.2
    # fuera de 0,3–15°: la página no se toca
    d, _v = escaneo(angulo=0.15)
    assert ocr._medir(d[0]) == 0.0
    d, _v = escaneo(angulo=25)
    assert ocr._medir(d[0]) == 0.0
    # una página sin renglones (una foto) no se endereza
    foto = pymupdf.open()
    foto.new_page().insert_image(pymupdf.Rect(0, 0, 595, 842), stream=_photo_bytes("png", 100))
    assert ocr.detectar_inclinacion(foto[0]) == 0.0
    # también sirve con un pixmap
    with pymupdf.open(docs["torcido"]) as d:
        assert 3.2 <= ocr.detectar_inclinacion(d[0].get_pixmap(dpi=150)) <= 3.8


@pytest.fixture(scope="module")
def torcido_hecho(docs, tmp_path_factory):
    out = tmp_path_factory.mktemp("torcido") / "torcido_ocr.pdf"
    res = ocr.reconocer(docs["torcido"], out, ocr.OpcionesOcr(dos_pasadas=False))
    return res, out


def test_escaneo_torcido_queda_derecho_y_buscable(torcido_hecho, docs):
    resumen, out = torcido_hecho
    pag = resumen.resultado.paginas[0]
    assert pag.enderezada and 3.2 <= pag.angulo <= 3.8
    with pymupdf.open(out) as d:
        page = d[0]
        assert abs(ocr.detectar_inclinacion(page)) < 0.3                   # inclinación residual
        texto = page.get_text()
        assert texto.count("niñez") == 5 and texto.count("cooperativa") == 5
        assert len(page.get_images()) == 1 and not core.page_needs_ocr(page)
        # la imagen se cambió por la enderezada, a su resolución y en JPEG si el original lo era
        info = d.extract_image(page.get_images()[0][0])
        with pymupdf.open(docs["torcido"]) as o:
            antes = o.extract_image(o[0].get_images()[0][0])
        assert (info["width"], info["height"]) == (antes["width"], antes["height"])
        # el texto invisible está sobre las palabras que se ven: se vuelve a reconocer la página nueva
        leidas = ocr._leer(page, 200, 0.0, None, "spa", core.tessdata_dir())
        vistas = {}
        for w in leidas:
            vistas.setdefault(w[4], []).append(((w[0] + w[2]) / 2, (w[1] + w[3]) / 2))
        capa = [(w[4], w[:4]) for w in page.get_text("words")]
        dist = _distancias(capa, vistas)
        assert len(dist) > 30 and dist[len(dist) // 2] < 3 and dist[-1] < 6


def test_escaneo_con_sello_no_cambia_y_la_capa_cae_sobre_lo_que_se_ve(docs, tmp_path):
    res = ocr.analizar(docs["sello"], ocr.OpcionesOcr(dos_pasadas=False))
    pag = res.paginas[0]
    assert 3.2 <= pag.angulo <= 3.8 and not pag.enderezada and pag.girada_sin_cambiar
    assert not any(SELLO.split()[0] in w.texto for w in pag.palabras)      # el sello ya es texto: no se repite
    verdad = _verdad(docs["dir"] / "sello_vector.pdf")
    dist = _distancias([(w.texto, w.rect) for w in pag.palabras], verdad)
    assert len(dist) > 30 and dist[len(dist) // 2] <= 3.0                  # cajas en su sitio (±3 pt)
    out = tmp_path / "sello_ocr.pdf"
    ocr.escribir(docs["sello"], out, res)
    with pymupdf.open(docs["sello"]) as a, pymupdf.open(out) as b:
        assert a[0].get_pixmap(dpi=60).samples == b[0].get_pixmap(dpi=60).samples   # se ve igual
        texto = b[0].get_text()
        assert texto.count("Radicado") == 1 and texto.count("niñez") == 5
        capa = [(w[4], w[:4]) for w in b[0].get_text("words")]
        dist = _distancias(capa, verdad)
        assert len(dist) > 30 and dist[len(dist) // 2] <= 3.0              # la capa cae sobre lo que se ve
        assert any(abs(ln["dir"][1]) > 0.03 for bl in b[0].get_text("dict")["blocks"]
                   for ln in bl.get("lines", ()))                          # escrita girada, como el escaneo


@pytest.mark.parametrize("nombre, giros", [("lado", (90, 270)), ("reves", (180,))])
def test_giro_automatico(docs, tmp_path, nombre, giros):
    out = tmp_path / f"{nombre}_ocr.pdf"
    res = ocr.reconocer(docs[nombre], out, ocr.OpcionesOcr(dos_pasadas=False, idioma="spa"))
    pag = res.resultado.paginas[0]
    assert pag.giro in giros
    with pymupdf.open(out) as d:
        page = d[0]
        assert page.rect.width < page.rect.height                          # queda al derecho (vertical)
        texto = page.get_text()
        assert "cooperativa" in texto and "niñez" in texto and "educación" in texto
    # sin «girar» la lectura no sirve (y no se gira nada)
    res2 = ocr.analizar(docs[nombre], ocr.OpcionesOcr(dos_pasadas=False, girar=False, idioma="spa"))
    assert res2.paginas[0].giro == 0
    assert "cooperativa" not in " ".join(w.texto for w in res2.paginas[0].palabras)


def test_limpieza_del_fondo_gris(docs):
    with pymupdf.open(docs["gris"]) as d:
        page = d[0]
        gris = ocr._gris(page, 100)
        tabla = ocr.tabla_limpieza(ocr._histograma(gris), "auto")
        assert tabla is not None and tabla[204] == 255 and tabla[0] == 0
        limpio = ocr.limpiar_pixmap(gris, tabla)
        assert limpio.pixel(5, 5) == (255,) and gris.pixel(5, 5)[0] < 215    # el fondo queda blanco
    with pymupdf.open(docs["derecho"]) as d:                                  # papel blanco: no hace falta
        assert ocr.tabla_limpieza(ocr._histograma(ocr._gris(d[0], 100)), "auto") is None
    dic = ocr.diccionario("spa")
    op = ocr.OpcionesOcr(dos_pasadas=False, idioma="spa", enderezar=False, girar=False)
    con = ocr.analizar(docs["gris"], op)
    sin = ocr.analizar(docs["gris"], ocr.OpcionesOcr(**{**op.__dict__, "limpiar": "no"}))
    assert con.paginas[0].limpiada and not sin.paginas[0].limpiada
    bien = lambda r: sum(1 for w in r.paginas[0].palabras if w.texto.strip(".,:").lower() in dic)  # noqa: E731
    assert bien(con) >= bien(sin) >= 20


# ---------------------------------------------------------------- analizar, corregir y escribir

def test_analizar_y_escribir_con_correcciones(docs, tmp_path):
    res = ocr.analizar(docs["derecho"], ocr.OpcionesOcr(dos_pasadas=True))
    pag = res.paginas[0]
    assert not pag.omitida and pag.lecturas == 2 and len(pag.palabras) > 150
    assert res.promedio() > 0.9 and res.segundos > 0
    informes = [w for w in pag.palabras if w.texto == "Informe"]
    assert len(informes) == 5
    informes[0].corregida = "Reporte"
    quitar = next(w for w in pag.palabras if w.texto == "municipio")
    quitar.corregida = ""                                            # la persona borró la palabra
    assert res.correcciones() == 2
    out = tmp_path / "corregido.pdf"
    paginas, palabras = ocr.escribir(docs["derecho"], out, res)
    assert paginas == 1 and palabras == len(pag.palabras) - 1
    with pymupdf.open(out) as d:
        texto = d[0].get_text()
        assert "Reporte" in texto and texto.count("Informe") == 4 and texto.count("municipio") == 4
        assert d[0].get_pixmap(dpi=50).samples == pymupdf.open(docs["derecho"])[0].get_pixmap(dpi=50).samples


def test_pagina_en_blanco_y_documento_mixto(docs, samples, tmp_path):
    res = ocr.analizar(docs["blanco"])
    assert len(res.paginas) == 1 and res.paginas[0].palabras == [] and not res.paginas[0].omitida
    mixto = tmp_path / "mixto.pdf"
    with pymupdf.open() as d:
        d.insert_pdf(pymupdf.open(samples["b"]))                           # 3 páginas digitales
        d.insert_pdf(pymupdf.open(docs["reves"]))
        d.save(mixto)
    res = ocr.analizar(mixto, ocr.OpcionesOcr(dos_pasadas=False))
    assert [p.indice for p in res.reconocidas()] == [3]
    assert all(p.omitida and p.motivo == "ya tiene texto" for p in res.paginas[:3])


def test_rotate_90_del_fixture_cajas_en_su_sitio(scanned, tmp_path):
    res = ocr.analizar(scanned, ocr.OpcionesOcr(dos_pasadas=False))
    p1, p2 = res.paginas
    assert p1.giro == p2.giro == 0
    c1 = {w.texto: w.rect for w in p1.palabras}
    c2 = {w.texto: w.rect for w in p2.palabras}
    comunes = [t for t in c1 if t in c2 and len(t) > 4]
    assert len(comunes) >= 4
    for t in comunes:                                    # la página girada se ve igual que la primera
        assert all(abs(a - b) < 3 for a, b in zip(c1[t], c2[t])), t
    out = tmp_path / "escaneado_ocr.pdf"
    ocr.escribir(scanned, out, res)
    with pymupdf.open(out) as d:
        h1, h2 = d[0].search_for("niñez"), d[1].search_for("niñez")
        assert h1 and h2 and all(abs(a - b) < 3 for a, b in zip(h1[0], h2[0]))


def test_contrasena_imagen_y_adjunto(scanned, tmp_path):
    path = tmp_path / "protegido_escaneado.pdf"
    with pymupdf.open() as d:
        d.insert_pdf(pymupdf.open(scanned), from_page=0, to_page=0)
        for i in (1, 2):
            p = d.new_page(width=595, height=842)
            p.insert_text((72, 100), f"Página {i + 1}: informe de gestión", fontsize=14)
            if i == 1:
                p.insert_image(pymupdf.Rect(72, 200, 372, 425), stream=_photo_bytes("png", 72))
        d.embfile_add("lista.txt", Protegido.adjunto, filename="lista.txt")
        d.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=Protegido.password, owner_pw="dueño")
    prot = Protegido(path)
    with pytest.raises(core.PasswordError):
        ocr.analizar(path)
    out = tmp_path / "protegido_ocr.pdf"
    resumen = ocr.reconocer(path, out, ocr.OpcionesOcr(dos_pasadas=False), password=Protegido.password)
    assert resumen.paginas == 1
    prot.comprobar(out, sin_tocar=(1, 2))                 # misma clave; páginas, imagen y adjunto completos
    with core.open_pdf(out, Protegido.password) as d:
        assert "niñez" in d[0].get_text()
    # la revisión de un PDF ya buscable con contraseña también conserva todo
    capa = ocr.leer_capa(out, Protegido.password)
    assert capa.origen == "capa" and [p.indice for p in capa.reconocidas()] == [0]
    w = next(w for w in capa.paginas[0].palabras if w.texto == "Cooperativa")
    w.corregida = "Cooperativo"
    out2 = tmp_path / "protegido_corregido.pdf"
    ocr.escribir(out, out2, capa, password=Protegido.password)
    prot.comprobar(out2, sin_tocar=(1, 2))
    with core.open_pdf(out2, Protegido.password) as d:
        texto = d[0].get_text()
        assert "Cooperativo" in texto and "Cooperativa" not in texto and "niñez" in texto


def test_documento_danado_y_falta_el_modelo(tmp_path, docs):
    roto = tmp_path / "roto.pdf"
    roto.write_bytes(b"%PDF-1.4\nesto no es un PDF de verdad")
    with pytest.raises(core.UserError, match="no parece un PDF válido"):
        ocr.analizar(roto)
    with pytest.raises(core.UserError, match="Falta el modelo de idioma «jpn»"):
        ocr.analizar(docs["derecho"], ocr.OpcionesOcr(idioma="jpn"))


def test_cancelar_no_deja_archivos_a_medias(docs, tmp_path):
    def cancelar_en(fase):
        def progress(i, n, msg=""):
            if fase in msg:
                raise Cancelado()
        return progress
    out = tmp_path / "cancelado.pdf"
    with pytest.raises(Cancelado):
        ocr.reconocer(docs["derecho"], out, ocr.OpcionesOcr(dos_pasadas=False), progress=cancelar_en("Reconociendo"))
    res = ocr.analizar(docs["derecho"], ocr.OpcionesOcr(dos_pasadas=False))
    with pytest.raises(Cancelado):
        ocr.escribir(docs["derecho"], out, res, progress=cancelar_en("Guardando"))
    assert list(tmp_path.iterdir()) == []                 # ni el resultado ni temporales


# ---------------------------------------------------------------- carpetas

def test_reconocer_carpeta(docs, samples, tmp_path):
    carpeta = tmp_path / "entrada"
    carpeta.mkdir()
    escaneo_pdf = carpeta / "escaneo.pdf"
    escaneo_pdf.write_bytes(docs["reves"].read_bytes())
    digital = carpeta / "digital.pdf"
    digital.write_bytes(samples["b"].read_bytes())
    roto = carpeta / "roto.pdf"
    roto.write_bytes(b"%PDF-1.4 roto")
    protegido = carpeta / "protegido.pdf"
    with pymupdf.open(docs["derecho"]) as d:
        d.save(protegido, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="1234", owner_pw="x")
    destino = tmp_path / "salida"
    destino.mkdir()
    (destino / "escaneo_ocr.pdf").write_bytes(b"no se pisa")
    archivos = [escaneo_pdf, roto, protegido, digital]
    pasos = []
    res = ocr.reconocer_carpeta(archivos, destino, ocr.OpcionesOcr(dos_pasadas=False), {},
                                progress=lambda i, n, m="": pasos.append((i, n, m)))
    estados = [(Path(r.ruta).name, r.estado) for r in res]
    assert estados == [("escaneo.pdf", "hecho"), ("roto.pdf", "error"), ("protegido.pdf", "error"),
                       ("digital.pdf", "ya tenía texto")]
    assert (destino / "escaneo_ocr.pdf").read_bytes() == b"no se pisa"              # nunca se pisa
    assert res[0].salida == str(destino / "escaneo_ocr (2).pdf") and res[0].paginas == 1 and res[0].palabras > 20
    assert "contraseña" in res[2].mensaje and res[1].mensaje
    assert res[3].salida is None and not (destino / "digital_ocr.pdf").exists()
    assert any("roto.pdf" in m for _i, _n, m in pasos)
    informe = ocr.informe_carpeta(res)
    assert "4 archivo(s): 1 reconocido(s), 1 ya tenían texto, 2 con error." in informe
    assert "escaneo.pdf" in informe and "palabras dudosas" in informe
    # con la clave y sin destino: junto al original, con el sufijo _ocr
    res = ocr.reconocer_carpeta([protegido], None, ocr.OpcionesOcr(dos_pasadas=False), {str(protegido): "1234"})
    assert res[0].estado == "hecho" and res[0].salida == str(carpeta / "protegido_ocr.pdf")
    assert core.needs_password(res[0].salida)


# ---------------------------------------------------------------- rendimiento

def test_digital_de_300_paginas_se_salta_en_segundos(tmp_path):
    path = tmp_path / "largo.pdf"
    with pymupdf.open() as d:
        for i in range(300):
            p = d.new_page()
            p.insert_text((72, 90), f"Capítulo {i + 1}: informe de gestión y niñez", fontsize=14)
            p.insert_text((72, 120), "Texto digital que no necesita reconocimiento de texto.", fontsize=11)
        d.save(path)
    t = time.time()
    res = ocr.analizar(path)
    segundos = time.time() - t
    assert len(res.paginas) == 300 and not res.reconocidas()
    assert segundos < 20, segundos
    assert ocr.estado_documento(path)[0] == "digital"
    print(f"\n300 páginas digitales: {segundos:.2f} s ({segundos / 300 * 1000:.1f} ms por página)")


# ---------------------------------------------------------------- documento mixto real (Word)

def _procesos_office() -> dict[int, float]:
    """PID → hora de inicio (epoch) de los procesos de Office.  Otras pruebas u otros programas del equipo
    pueden abrir Office al mismo tiempo: por eso se miran solo los que nacieron durante la conversión."""
    if not WINDOWS:
        return {}
    ps = ("Get-Process WINWORD,EXCEL,POWERPNT -ErrorAction SilentlyContinue | ForEach-Object { "
          "'{0},{1}' -f $_.Id, ([DateTimeOffset]$_.StartTime).ToUnixTimeMilliseconds() }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                         creationflags=subprocess.CREATE_NO_WINDOW).stdout
    return {int(f[0]): int(f[1]) / 1000 for f in csv.reader(out.splitlines()) if len(f) == 2}


def _hay_word() -> bool:
    try:
        from ronypdf import oficina
        return WINDOWS and bool(oficina.detectar().word)
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _hay_word(), reason="Microsoft Word no está instalado")
def test_documento_mixto_real_word_mas_escaneo(docs, tmp_path):
    from ronypdf import oficina
    inicio = time.time() - 1
    docx = tmp_path / "carta.docx"
    oficina.escribir_docx([oficina.Parrafo("Carta de la cooperativa", estilo="titulo1"),
                           oficina.Parrafo("Bogotá, 12 de marzo de 2025. Señora Ana Gómez: le enviamos el informe "
                                           "de gestión con los anexos escaneados.")], docx)
    carta = tmp_path / "carta.pdf"
    assert oficina.a_pdf(docx, carta) >= 1
    fin_conversion = time.time() + 1
    nuestros = {pid for pid, t in _procesos_office().items() if inicio <= t <= fin_conversion}
    mixto = tmp_path / "carta_y_anexos.pdf"
    with pymupdf.open(carta) as d:
        n_carta = d.page_count
        texto_carta = [p.get_text() for p in d]
        d.insert_pdf(pymupdf.open(docs["derecho"]))
        d.insert_pdf(pymupdf.open(docs["sello"]))
        d.save(mixto)
    out = tmp_path / "carta_y_anexos_ocr.pdf"
    resumen = ocr.reconocer(mixto, out, ocr.OpcionesOcr(dos_pasadas=False))
    assert [p.indice for p in resumen.resultado.reconocidas()] == [n_carta, n_carta + 1]
    with pymupdf.open(out) as d:
        assert [d[i].get_text() for i in range(n_carta)] == texto_carta           # la carta no se toca
        assert d[n_carta].get_text().count("niñez") == 5
        assert d[n_carta + 1].get_text().count("Radicado") == 1
    fin = time.time() + 90                 # Word tarda en cerrarse del todo después de convertir
    while time.time() < fin and nuestros & set(_procesos_office()):
        time.sleep(1)
    assert not nuestros & set(_procesos_office()), "quedó abierto el Word que abrió la conversión"


# ---------------------------------------------------------------- interfaz sin pantalla

@pytest.fixture(scope="module")
def win():
    from PySide6.QtWidgets import QApplication
    from ronypdf.gui import MainWindow, _light_palette
    from ronypdf.style import QSS
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setPalette(_light_palette())
    app.setStyleSheet(QSS)
    w = MainWindow()
    w.resize(1000, 660)
    w.show()
    app.processEvents()
    yield w
    w.pages["ocrcheck"]._sucio = False


@pytest.fixture
def done(monkeypatch):
    from ronypdf import ocr_ui, tools, widgets
    calls = []

    def fake_done(parent, title, detail="", file=None, folder=None):
        calls.append((title, detail, file, folder))

    for mod in (tools, widgets, ocr_ui):
        monkeypatch.setattr(mod, "show_done", fake_done, raising=False)
    monkeypatch.setattr(widgets.QMessageBox, "warning", lambda *a, **k: calls.append(("warning", a[2])))
    monkeypatch.setattr(widgets.QMessageBox, "information", lambda *a, **k: calls.append(("info", a[2])))
    monkeypatch.setattr(widgets.QMessageBox, "question", lambda *a, **k: widgets.QMessageBox.StandardButton.Yes)
    return calls


def esperar(timeout=120):
    from PySide6.QtWidgets import QApplication
    from ronypdf.widgets import ctx
    fin = time.time() + timeout
    while time.time() < fin:
        QApplication.processEvents()
        if not ctx.busy:
            QApplication.processEvents()
            return
        time.sleep(0.01)
    raise TimeoutError


def captura(win, nombre):
    from PySide6.QtWidgets import QApplication
    fin = time.time() + 0.3                # deja correr los temporizadores (la página se vuelve a dibujar)
    while time.time() < fin:
        QApplication.processEvents()
        time.sleep(0.02)
    if SHOTS:
        win.grab().save(str(Path(SHOTS) / f"{nombre}.png"))


def _cabe(win, page):
    """Nada de la pantalla queda fuera de la ventana ni recortado a 1000 × 660 y a 1240 × 820."""
    from PySide6.QtWidgets import QAbstractButton, QApplication, QLabel
    QApplication.processEvents()
    ancho = page.width()
    for w in page.findChildren(QAbstractButton) + page.findChildren(QLabel):
        if not w.isVisibleTo(page) or w.window() is not win:
            continue
        x = w.mapTo(page, w.rect().topLeft()).x()
        assert x + w.width() <= ancho + 1, f"«{getattr(w, 'text', lambda: '')()}» se sale de la ventana"
        if isinstance(w, QAbstractButton) and w.text():
            assert w.width() >= w.sizeHint().width() - 2, f"el botón «{w.text()}» queda recortado"


def test_ui_revisar_vacia_y_pdf_digital_sin_dialogos(win, done, samples):
    page = win.pages["ocrcheck"]
    win.sidebar.set_collapsed(False)
    win.navigate("ocrcheck")
    page.accept_files([str(samples["a"])])
    assert page.estado.text() == ocr_ui_sin_nada() and not page.b_reconocer.isVisibleTo(page)
    assert done == [] and not page.has_unsaved()
    for tam in ((1000, 660), (1240, 820)):
        win.resize(*tam)
        _cabe(win, page)
        captura(win, f"ocrcheck_vacia_{tam[0]}")
    win.resize(1000, 660)


def ocr_ui_sin_nada():
    from ronypdf import ocr_ui
    return ocr_ui.SIN_NADA


def test_ui_ocr_un_documento_con_revision(win, done, docs, personal, tmp_path, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from ronypdf import ocr_ui, tools
    page = win.pages["ocr"]
    win.sidebar.set_collapsed(False)
    win.navigate("ocr")
    monkeypatch.setattr(tools, "ask_save", lambda *a, **k: None)     # si no hubiera dudosas, no se cuelga
    src = tmp_path / "escaneo.pdf"
    src.write_bytes(docs["torcido"].read_bytes())
    page.accept_files([str(src)])
    assert not page.varios() and page.picker.path == str(src) and "escaneado" in page.status.text()
    assert page.enderezar.isChecked() and page.girar.isChecked() and page.limpiar.isChecked()
    assert page.revisar.isChecked() and page.revisar.isVisibleTo(page)
    for tam in ((1000, 660), (1240, 820)):
        win.resize(*tam)
        _cabe(win, page)
        captura(win, f"ocr_un_documento_{tam[0]}")
    win.resize(1000, 660)
    page.run()
    esperar()
    revisar = win.pages["ocrcheck"]
    assert win.stack.currentWidget() is revisar and revisar.resultado is not None, done
    assert revisar.has_unsaved() and revisar.pilas.currentIndex() == 1
    assert revisar.resultado.paginas[0].enderezada
    n_dudosas = len(revisar.resultado.dudosas())
    assert n_dudosas >= 1 and revisar.modelo.rowCount() >= n_dudosas
    assert "Confianza promedio" in revisar.resumen.text() and "palabras dudosas" in revisar.resumen.text() \
        or "palabra dudosa" in revisar.resumen.text()
    assert revisar.vista.scene().items() and revisar._items                  # página con las dudosas marcadas
    for tam in ((1000, 660), (1240, 820)):
        win.resize(*tam)
        _cabe(win, revisar)
        captura(win, f"ocrcheck_revision_{tam[0]}")
    win.resize(1000, 660)

    # corregir la palabra actual con el teclado: escribir y Enter
    p0, w0 = revisar.actual
    assert revisar.recorte_lb.pixmap() is not None and not revisar.recorte_lb.pixmap().isNull()
    revisar.editor.setFocus()
    revisar.editor.setText("Corrección")
    QTest.keyClick(revisar.editor, Qt.Key.Key_Return)
    assert w0.corregida == "Corrección" and w0.revisada and revisar.actual[1] is not w0 or n_dudosas == 1
    # Ctrl+Z deshace la última corrección
    QTest.keyClick(revisar.editor, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert w0.corregida is None and not w0.revisada and revisar.actual[1] is w0
    QTest.keyClick(revisar.editor, Qt.Key.Key_Return)                   # aceptar tal cual
    assert w0.corregida is None and w0.revisada
    # Tab y Mayús+Tab recorren la lista; F2 edita
    fila = revisar.lista.currentIndex().row()
    QTest.keyClick(revisar.lista, Qt.Key.Key_Tab)
    assert revisar.lista.currentIndex().row() == (fila + 1) % revisar.modelo.rowCount()
    QTest.keyClick(revisar.lista, Qt.Key.Key_Backtab)
    assert revisar.lista.currentIndex().row() == fila
    QTest.keyClick(revisar.lista, Qt.Key.Key_F2)
    assert revisar.editor.hasFocus() or not revisar.isActiveWindow()
    # clic en una palabra de la página la selecciona en la lista
    otra_fila = revisar.modelo.rowCount() - 1
    p, w = revisar.modelo.filas[otra_fila]
    revisar._clic_en_pagina((p, w))
    assert revisar.actual[1] is w and revisar.lista.currentIndex().row() == otra_fila
    # una corrección de verdad para el PDF guardado
    revisar.editor.setText("PalabraCorregida")
    revisar.aceptar()
    # «Agregar a mi diccionario» y «Aceptar todas las de esta página»
    filtro_todas = revisar.filtro.findData("todas")
    revisar.filtro.setCurrentIndex(filtro_todas)
    caf = next((pw for pw in revisar.modelo.filas if pw[1].texto == "Contacto:"), revisar.modelo.filas[0])
    revisar._seleccionar(caf[1])
    revisar.editor.setText("Guaviyú")
    revisar.agregar_diccionario()
    assert "guaviyú" in ocr.diccionario_personal()
    revisar.filtro.setCurrentIndex(revisar.filtro.findData("dudosas"))
    revisar.aceptar_pagina()
    assert not revisar.resultado.dudosas() and "0 palabras dudosas" in revisar.resumen.text()
    assert not revisar.b_todas_pagina.isEnabled()

    out = tmp_path / "escaneo_revisado.pdf"
    monkeypatch.setattr(ocr_ui, "ask_save", lambda *a, **k: str(out))
    revisar.guardar()
    esperar()
    assert done[-1][0] == "Texto reconocido" and done[-1][2] == str(out), done
    assert not revisar.has_unsaved() and "guardado en" in revisar.info.text().lower()
    with pymupdf.open(out) as d:
        texto = d[0].get_text()
        assert "PalabraCorregida" in texto and "Guaviyú" in texto and texto.count("niñez") >= 4
        assert abs(ocr.detectar_inclinacion(d[0])) < 0.3
    assert src.read_bytes() == docs["torcido"].read_bytes()             # el original no cambia
    # la palabra del diccionario personal no se marca en otro documento
    otro = ocr.confianza("Guaviyú")
    assert otro == (1.0, [])
    monkeypatch.setattr(tools, "ask_save", lambda *a, **k: None)


def test_ui_ocr_sin_revision_y_texto(win, done, docs, tmp_path, monkeypatch):
    from ronypdf import tools
    page = win.pages["ocr"]
    win.navigate("ocr")
    page.accept_files([str(docs["reves"])])
    page.revisar.setChecked(False)
    out = tmp_path / "reves_ocr.pdf"
    monkeypatch.setattr(tools, "ask_save", lambda *a, **k: str(out))
    page.run()
    esperar()
    assert done[-1][0] == "Texto reconocido" and "palabras" in done[-1][1], done
    with pymupdf.open(out) as d:
        assert "cooperativa" in d[0].get_text()
    page.output.setCurrentIndex(page.output.findData("txt"))
    assert not page.revisar.isVisibleTo(page)
    txt = tmp_path / "reves.txt"
    monkeypatch.setattr(tools, "ask_save", lambda *a, **k: str(txt))
    page.run()
    esperar()
    assert "cooperativa" in txt.read_text(encoding="utf-8-sig")
    page.output.setCurrentIndex(0)
    page.revisar.setChecked(True)


def test_ui_ocr_varios_archivos(win, done, docs, samples, tmp_path, monkeypatch):
    from ronypdf import tools, widgets
    page = win.pages["ocr"]
    win.sidebar.set_collapsed(False)
    win.navigate("ocr")
    carpeta = tmp_path / "lote"
    (carpeta / "sub").mkdir(parents=True)
    (carpeta / "a_escaneo.pdf").write_bytes(docs["reves"].read_bytes())
    (carpeta / "b_digital.pdf").write_bytes(samples["b"].read_bytes())
    (carpeta / "sub" / "c_roto.pdf").write_bytes(b"%PDF roto")
    with pymupdf.open(docs["gris"]) as d:
        d.save(carpeta / "d_clave.pdf", encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="abc", owner_pw="x")
    page.accept_files([str(carpeta)])
    assert page.varios() and page.lista.topLevelItemCount() == 3          # sin subcarpetas
    assert not page.revisar.isVisibleTo(page) and page.action.text() == "Reconocer los archivos"
    page.lista.clear_all()
    page.subcarpetas.setChecked(True)
    monkeypatch.setattr(tools, "ask_dir", lambda *a, **k: str(carpeta))
    page._agregar_carpeta()
    assert page.lista.topLevelItemCount() == 4
    assert "protegido" in [it.text(1) for it in page.lista.items()]
    for tam in ((1000, 660), (1240, 820)):
        win.resize(*tam)
        _cabe(win, page)
        captura(win, f"ocr_varios_{tam[0]}")
    win.resize(1000, 660)
    # la contraseña se pide antes de empezar, una sola vez
    pedidas = []
    monkeypatch.setattr(widgets.QInputDialog, "getText", lambda *a, **k: (pedidas.append(1) or ("abc", True)))
    page.run()
    esperar(300)
    assert pedidas == [1]
    estados = [r.estado for r in page.resultados_carpeta]
    assert estados == ["hecho", "ya tenía texto", "hecho", "error"], [(r.ruta, r.estado, r.mensaje)
                                                                      for r in page.resultados_carpeta]
    assert (carpeta / "a_escaneo_ocr.pdf").exists() and (carpeta / "d_clave_ocr.pdf").exists()
    assert core.needs_password(carpeta / "d_clave_ocr.pdf")
    assert page.caja_resultados.isVisibleTo(page) and page.tabla.topLevelItemCount() == 4
    assert "2 reconocido(s)" in page.footer_info.text() and "1 con error" in page.footer_info.text()
    b = page.tabla.itemWidget(page.tabla.topLevelItem(0), 4)
    assert b is not None and b.text() == "Revisar"
    for tam in ((1000, 660), (1240, 820)):
        win.resize(*tam)
        captura(win, f"ocr_varios_resultados_{tam[0]}")
    win.resize(1000, 660)
    informe = tmp_path / "informe.txt"
    monkeypatch.setattr(tools, "ask_save", lambda *a, **k: str(informe))
    page._guardar_informe()
    assert "a_escaneo.pdf" in informe.read_text(encoding="utf-8-sig")
    b.click()
    revisar = win.pages["ocrcheck"]
    assert win.stack.currentWidget() is revisar and revisar.path == str(carpeta / "a_escaneo.pdf")
    assert revisar.salida == str(carpeta / "a_escaneo_ocr.pdf")
    revisar._sucio = False
    revisar.descartar()
    assert revisar.resultado is None and revisar.pilas.currentIndex() == 0
    page.b_uno.setChecked(True)


def test_ui_revisar_pdf_escaneado_y_ya_buscable(win, done, docs, tmp_path):
    revisar = win.pages["ocrcheck"]
    win.navigate("ocrcheck")
    revisar.accept_files([str(docs["reves"])])
    assert revisar.b_reconocer.isVisibleTo(revisar) and "escaneado" in revisar.estado.text()
    assert done == []
    revisar.reconocer_ahora()
    esperar()
    assert revisar.resultado is not None and revisar.resultado.paginas[0].giro == 180
    revisar.filtro.setCurrentIndex(revisar.filtro.findData("todas"))
    assert revisar.modelo.rowCount() == len(revisar.resultado.todas())
    # «Volver a leer esta página con más calidad (400 ppp)»
    assert revisar.b_releer.isEnabled()
    antes = revisar.resultado.paginas[0]
    revisar.releer()
    esperar()
    nueva = revisar.resultado.paginas[0]
    assert nueva is not antes and nueva.ppp == 400 and nueva.giro == 180 and nueva.palabras
    assert revisar.modelo.rowCount() == len(revisar.resultado.todas()) and "400 ppp" in revisar.info.text()
    revisar._sucio = False
    # un PDF que ya tiene su capa: se revisa con leer_capa
    buscable = tmp_path / "buscable.pdf"
    core.ocr_pdf(docs["reves"], buscable, "spa")
    revisar.accept_files([str(buscable)])
    esperar()
    assert revisar.resultado is not None and revisar.resultado.origen == "capa"
    assert not revisar.has_unsaved() and not revisar.b_releer.isEnabled()
    revisar.descartar()


def test_ui_cerrar_con_texto_reconocido_sin_guardar(win, done, scanned, tmp_path, monkeypatch):
    """Cerrar con texto reconocido sin guardar y elegir «Guardar» lanzaba TypeError dentro de closeEvent
    (``guardar()`` no aceptaba ``al_terminar``): la ventana se cerraba igual, sin preguntar dónde guardar, y se
    perdía todo lo reconocido y corregido."""
    from PySide6.QtGui import QCloseEvent
    from ronypdf import guardado_ui, ocr_ui
    revisar = win.pages["ocrcheck"]
    op = ocr.OpcionesOcr(dos_pasadas=False)
    revisar.cargar(str(scanned), None, ocr.analizar(scanned, op), op)
    assert revisar.has_unsaved()
    monkeypatch.setattr(guardado_ui, "preguntar_cambios",
                        lambda p, t, x: "guardar" if win.stack.currentWidget() is revisar else "descartar")
    pedidos = []
    monkeypatch.setattr(ocr_ui, "ask_save", lambda *a, **k: pedidos.append(a[1]) or None)   # cancela «dónde»
    g = win._autoguardado
    cerrar = g.cerrar
    g.cerrar = lambda: None                            # la ventana de la prueba sigue viva
    try:
        e = QCloseEvent()
        win.closeEvent(e)
        assert len(pedidos) == 1 and pedidos[0].endswith("_ocr.pdf")      # preguntó dónde guardar
        assert not e.isAccepted() and revisar.has_unsaved()                # cancelar no cierra ni pierde nada
        out = tmp_path / "buscable.pdf"
        monkeypatch.setattr(ocr_ui, "ask_save", lambda *a, **k: str(out))
        e = QCloseEvent()
        win.closeEvent(e)
        assert e.isAccepted() and not revisar.has_unsaved()
        with pymupdf.open(out) as d:
            assert "Cooperativa" in d[0].get_text()
    finally:
        g.cerrar = cerrar
