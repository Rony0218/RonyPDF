"""Comentarios y «Mis sellos», personales: modelo (comentarios.py), sellos (sellos.py) y la extensión del visor
y la pantalla «Mis sellos» (comentarios_ui.py), sin pantalla.

Los nombres de las pruebas son de personas genéricas (Ana Pérez, Luis Gómez): nada institucional."""

import base64
import datetime as dt
import json
import os
import shutil
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path

import pymupdf
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("RONYPDF_SETTINGS", os.path.join(tempfile.mkdtemp(prefix="ronypdf-com-"), "RonyPDF.json"))

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QImage, QKeyEvent, QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox  # noqa: E402

from ronypdf import comentarios, core, sellos, viewer, widgets  # noqa: E402
from ronypdf import comentarios_ui as cui  # noqa: E402
from ronypdf.comentarios import Filtro  # noqa: E402
from ronypdf.gui import MainWindow, _light_palette  # noqa: E402
from ronypdf.style import QSS  # noqa: E402

F1 = "D:20250312154200-05'00'"
F2 = "D:20250312160000-05'00'"
F3 = "D:20250312161000-05'00'"
PDFCRAFT = Path(r"C:\Herramientas\PdfCraft\pdfcraft-cli.exe")
_app = QApplication.instance() or QApplication([])


# ---------------------------------------------------------------- utilidades

def _pdf_texto(path: Path, paginas: int = 3, rotar: dict | None = None) -> Path:
    with pymupdf.open() as d:
        for i in range(paginas):
            p = d.new_page(width=612, height=792)
            p.insert_text((72, 90), f"Carta de prueba página {i + 1}", fontsize=16)
            for k in range(6):
                p.insert_text((72, 140 + k * 26), f"Renglón {k + 1}: texto para subrayar y tachar en la revisión",
                              fontsize=12)
        for i, g in (rotar or {}).items():
            d[i].set_rotation(g)
        d.save(path)
    return path


def _png_marcador(w=200, h=80) -> bytes:
    """Imagen con un cuadro rojo arriba a la izquierda y uno azul abajo a la derecha (para ver si quedó derecha)."""
    with pymupdf.open() as s:
        p = s.new_page(width=w, height=h)
        p.draw_rect(pymupdf.Rect(0, 0, 30, 30), color=(1, 0, 0), fill=(1, 0, 0))
        p.draw_rect(pymupdf.Rect(w - 30, h - 30, w, h), color=(0, 0, 1), fill=(0, 0, 1))
        p.insert_text((40, h / 2 + 8), "SELLO", fontsize=24)
        return p.get_pixmap(dpi=144, alpha=True).tobytes("png")


def _color_en(page, x, y):
    pix = page.get_pixmap(dpi=72, clip=pymupdf.Rect(x, y, x + 1, y + 1))
    return pix.pixel(0, 0)


def _crear(tipo, pagina, nm, **k):
    op = {"op": "crear", "tipo": tipo, "pagina": pagina, "nm": nm, "autor": "Ana Pérez", "texto": "", "fecha": F1}
    op.update(k)
    return op


def _palabra(path, pagina, texto, n=0):
    with pymupdf.open(path) as d:
        r = d[pagina].search_for(texto)[n]
        return tuple(r * d[pagina].rotation_matrix) if d[pagina].rotation else tuple(r)


def _guardar(doc, path):
    doc.save(path, garbage=3, deflate=True)
    doc.close()
    return pymupdf.open(path)


def _por_tipo(lista):
    return {c.tipo: c for c in lista}


# ======================================================================== modelo: crear, leer, guardar

def test_crear_cada_tipo_guardar_y_leer(tmp_path):
    src = _pdf_texto(tmp_path / "carta.pdf")
    r_sub = _palabra(src, 0, "subrayar")
    r_tach = _palabra(src, 0, "tachar")
    png = base64.b64encode(_png_marcador()).decode()
    ops = [
        _crear("nota", 0, "rpdf-n1", texto="Revisar la fecha", punto=(420, 60), color="#2563EB"),
        _crear("subrayado", 0, "rpdf-s1", rects=[r_sub]),
        _crear("tachado", 0, "rpdf-t1", texto="sobra", rects=[r_tach]),
        _crear("ondulado", 1, "rpdf-o1", rects=[_palabra(src, 1, "subrayar")]),
        _crear("resaltado", 1, "rpdf-r1", texto="importante", rects=[_palabra(src, 1, "tachar")]),
        _crear("cuadro", 0, "rpdf-c1", texto="Cuadro con tildes: Bogotá, niñez", rect=(72, 400, 300, 460)),
        _crear("sello", 2, "rpdf-st1", texto="REVISADO · Ana Pérez", rect=(300, 500, 450, 560), png=png),
    ]
    resultados = []
    for k in range(2):           # aplicar dos veces sobre el mismo archivo da lo mismo
        doc = pymupdf.open(src)
        comentarios.aplicar(doc, ops)
        doc = _guardar(doc, tmp_path / f"salida{k}.pdf")
        lista = comentarios.leer(doc, marcado=True)
        resultados.append([(c.pagina, c.tipo, c.autor, c.texto, c.texto_marcado, c.color,
                            tuple(round(v, 1) for v in c.rect), c.creado, c.modificado) for c in lista])
        doc.close()
    assert resultados[0] == resultados[1]
    with pymupdf.open(tmp_path / "salida0.pdf") as doc:
        lista = comentarios.leer(doc, marcado=True)
        t = _por_tipo(lista)
        assert set(t) == {"nota", "subrayado", "tachado", "ondulado", "resaltado", "cuadro", "sello"}
        assert all(c.autor == "Ana Pérez" for c in lista)
        fecha = comentarios.fecha_pdf(F1)
        assert all(c.creado == fecha for c in lista)
        assert t["nota"].texto == "Revisar la fecha" and t["nota"].color == "#2563EB"
        assert t["nota"].rect[:2] == pytest.approx((420, 60), abs=0.6)
        assert t["subrayado"].texto_marcado == "subrayar" and t["tachado"].texto_marcado == "tachar"
        assert t["tachado"].texto == "sobra" and t["tachado"].color == comentarios.COLORES["tachado"]
        cs = pymupdf.Rect(r_sub)
        assert pymupdf.Rect(t["subrayado"].rect).contains((cs.tl + cs.br) / 2)
        assert t["cuadro"].texto.startswith("Cuadro con tildes") and t["cuadro"].color == comentarios.COLORES["cuadro"]
        assert t["cuadro"].rect == pytest.approx((72, 400, 300, 460), abs=1)
        assert t["sello"].rect == pytest.approx((300, 500, 450, 560), abs=0.6)
        assert t["sello"].texto == "REVISADO · Ana Pérez" and t["sello"].movible
        assert not t["subrayado"].movible and t["nota"].movible and t["cuadro"].movible
        # son anotaciones PDF estándar, con su tipo y su autor
        tipos_pdf = sorted(a.type[1] for p in doc for a in p.annots())
        assert tipos_pdf == sorted(["Text", "Underline", "StrikeOut", "Squiggly", "Highlight", "FreeText", "Stamp"])
        nms = {a.info["id"] for p in doc for a in p.annots()}
        assert {"rpdf-n1", "rpdf-st1", "rpdf-c1"} <= nms
        # el sello con imagen propia se ve (cuadro rojo arriba a la izquierda)
        assert _color_en(doc[2], 303, 503) == (255, 0, 0)


def test_hilos_y_estados(tmp_path):
    src = _pdf_texto(tmp_path / "carta.pdf")
    ops = [
        _crear("nota", 0, "rpdf-n1", texto="¿Está bien esta cifra?", punto=(400, 100)),
        {"op": "responder", "ref": {"pagina": 0, "nm": "rpdf-n1"}, "nm": "rpdf-r1", "autor": "Luis Gómez",
         "texto": "Sí, la revisé", "fecha": F2},
        {"op": "responder", "ref": {"pagina": 0, "nm": "rpdf-r1"}, "nm": "rpdf-r2", "autor": "Ana Pérez",
         "texto": "Gracias", "fecha": F3},
        {"op": "estado", "ref": {"pagina": 0, "nm": "rpdf-n1"}, "nm": "rpdf-e1", "autor": "Ana Pérez",
         "estado": "Rejected", "fecha": "D:20250312162000-05'00'"},
        {"op": "estado", "ref": {"pagina": 0, "nm": "rpdf-n1"}, "nm": "rpdf-e2", "autor": "Ana Pérez",
         "estado": "Accepted", "fecha": "D:20250312163000-05'00'"},
        _crear("nota", 1, "rpdf-n2", texto="otra", punto=(100, 100)),
        {"op": "responder", "ref": {"pagina": 1, "nm": "rpdf-n2"}, "nm": "rpdf-r3", "autor": "Luis Gómez",
         "texto": "Respuesta que se borra con su nota", "fecha": F2},
    ]
    doc = pymupdf.open(src)
    comentarios.aplicar(doc, ops)
    doc = _guardar(doc, tmp_path / "hilos.pdf")
    lista = comentarios.leer(doc)
    assert len(lista) == 2                       # las respuestas y los estados no son comentarios sueltos
    n1 = next(c for c in lista if c.pagina == 0)
    assert n1.estado == "Accepted"               # vale el último cambio de estado
    textos = [(r.autor, r.texto) for r in n1.respuestas if not r.es_estado]
    assert textos == [("Luis Gómez", "Sí, la revisé"), ("Ana Pérez", "Gracias")]
    estados = [r for r in n1.respuestas if r.es_estado]
    assert [r.estado for r in estados] == ["Rejected", "Accepted"]
    assert estados[-1].texto == "Aceptado por Ana Pérez"
    assert n1.respondido
    # la respuesta va en el mismo rectángulo que la original y no se dibuja encima
    raw = {a.info["id"]: doc.xref_get_key(a.xref, "Rect")[1] for a in doc[0].annots()}
    assert raw["rpdf-r1"] == raw["rpdf-n1"]
    flags = {a.info["id"]: a.flags for a in doc[0].annots()}
    assert flags["rpdf-r1"] == comentarios.BANDERAS_RESPUESTA and flags["rpdf-e2"] == comentarios.BANDERAS_ESTADO
    xref_e2 = next(a.xref for a in doc[0].annots() if a.info["id"] == "rpdf-e2")
    assert doc.xref_get_key(xref_e2, "StateModel")[1] == "Review"
    # borrar un comentario con sus respuestas no deja huérfanas
    comentarios.aplicar(doc, [{"op": "borrar", "ref": {"pagina": 1, "nm": "rpdf-n2"}},
                              {"op": "borrar", "ref": {"pagina": 0, "nm": "rpdf-n1"}}])
    doc = _guardar(doc, tmp_path / "borrado.pdf")
    assert comentarios.leer(doc) == []
    assert [a for p in doc for a in p.annots()] == []
    doc.close()


def test_operaciones_sobre_anotaciones_ajenas(tmp_path):
    """Lo que ya venía en el archivo se ubica por (página, xref): sin /NM, con /NM repetido o dañado."""
    src = tmp_path / "ajeno.pdf"
    with pymupdf.open() as d:
        p = d.new_page()
        p.insert_text((72, 100), "Documento con comentarios hechos en otro programa", fontsize=12)
        a = p.add_text_annot((300, 200), "nota sin NM")
        a.set_info(title="Luis Gómez")
        a.update()
        d.xref_set_key(a.xref, "NM", "null")
        b = p.add_text_annot((300, 300), "primera con NM repetido")
        b.update()
        c = p.add_text_annot((300, 400), "segunda con NM repetido")
        c.update()
        for x in (b.xref, c.xref):
            d.xref_set_key(x, "NM", "(repetido)")
        # como las de Acrobat: además del texto, una versión con formato (/RC)
        d.xref_set_key(c.xref, "RC", pymupdf.get_pdf_str("<body><p>segunda con NM repetido</p></body>"))
        # un sello hecho en otro programa con su propia apariencia (verde)
        s = p.add_stamp_annot(pymupdf.Rect(100, 500, 300, 560), stamp=0)
        f = d.get_new_xref()
        d.update_object(f, "<< /Type /XObject /Subtype /Form /BBox [0 0 200 60] /Resources << >> >>")
        d.update_stream(f, b"0 1 0 rg 0 0 200 60 re f")
        d.xref_set_key(s.xref, "AP", f"<< /N {f} 0 R >>")
        # una anotación dañada: sin rectángulo y con un subtipo desconocido
        z = d.get_new_xref()
        d.update_object(z, "<< /Type /Annot /Subtype /Inventada /Contents (rara) >>")
        d.xref_set_key(p.xref, "Annots", d.xref_get_key(p.xref, "Annots")[1].replace("]", f" {z} 0 R]"))
        d.save(src)
    with pymupdf.open(src) as doc:
        lista = comentarios.leer(doc)
        por_texto = {c.texto: c for c in lista}
        assert {"nota sin NM", "primera con NM repetido", "segunda con NM repetido"} <= set(por_texto)
        ids = [c.id for c in lista]
        assert len(ids) == len(set(ids))         # identificadores únicos aunque el /NM se repita
        sello = next(c for c in lista if c.tipo == "sello")
        xs = {k: v.xref for k, v in por_texto.items()}
    ops = [
        {"op": "editar", "ref": {"pagina": 0, "xref": xs["segunda con NM repetido"]}, "texto": "editada", "fecha": F2},
        {"op": "responder", "ref": {"pagina": 0, "xref": xs["nota sin NM"]}, "nm": "rpdf-x1", "autor": "Ana Pérez",
         "texto": "le respondo", "fecha": F2},
        {"op": "mover", "ref": {"pagina": 0, "xref": sello.xref}, "rect": (150, 600, 350, 660), "fecha": F2},
        {"op": "editar", "ref": {"pagina": 0, "xref": sello.xref}, "texto": "Sello ajeno comentado", "fecha": F2},
        {"op": "estado", "ref": {"pagina": 0, "xref": xs["primera con NM repetido"]}, "nm": "rpdf-x2",
         "autor": "Ana Pérez", "estado": "Completed", "fecha": F3},
        {"op": "borrar", "ref": {"pagina": 0, "xref": 99999}},          # ya no existe: no hace nada
    ]
    doc = pymupdf.open(src)
    comentarios.aplicar(doc, ops)
    doc = _guardar(doc, tmp_path / "ajeno_salida.pdf")
    lista = comentarios.leer(doc)
    por_texto = {c.texto: c for c in lista}
    assert "editada" in por_texto and "primera con NM repetido" in por_texto
    assert doc.xref_get_key(por_texto["editada"].xref, "RC")[0] == "null"      # Acrobat mostrará el texto nuevo
    assert por_texto["primera con NM repetido"].estado == "Completed"
    assert [r.texto for r in por_texto["nota sin NM"].respuestas] == ["le respondo"]
    s2 = next(c for c in lista if c.tipo == "sello")
    assert s2.texto == "Sello ajeno comentado" and s2.rect == pytest.approx((150, 600, 350, 660), abs=0.6)
    assert _color_en(doc[0], 250, 630) == (0, 255, 0)     # conserva su apariencia propia
    doc.close()


def test_pagina_girada_nota_cuadro_y_sello_derechos(tmp_path, scanned):
    """En la segunda página del escaneo (con /Rotate 90) todo queda derecho y en su sitio."""
    png = base64.b64encode(_png_marcador()).decode()
    ops = [
        _crear("nota", 1, "rpdf-n", texto="nota en página girada", punto=(100, 100)),
        _crear("cuadro", 1, "rpdf-c", texto="Cuadro derecho", rect=(100, 200, 300, 260)),
        _crear("sello", 1, "rpdf-s", texto="SELLO", rect=(100, 400, 300, 480), png=png),
        {"op": "mover", "ref": {"pagina": 1, "nm": "rpdf-n"}, "rect": (200, 300, 216, 316), "fecha": F2},
    ]
    doc = pymupdf.open(scanned)
    assert doc[1].rotation == 90
    comentarios.aplicar(doc, ops)
    doc = _guardar(doc, tmp_path / "girada.pdf")
    t = _por_tipo(comentarios.leer(doc, paginas=[1]))
    assert t["nota"].rect[:2] == pytest.approx((200, 300), abs=0.6)
    assert t["cuadro"].rect == pytest.approx((100, 200, 300, 260), abs=1)
    assert t["sello"].rect == pytest.approx((100, 400, 300, 480), abs=0.6)
    pg = doc[1]
    assert _color_en(pg, 104, 404) == (255, 0, 0) and _color_en(pg, 296, 476) == (0, 0, 255)   # derecho
    ft = next(a for a in pg.annots() if a.type[1] == "FreeText")
    assert doc.xref_get_key(ft.xref, "Rotate")[1] == "90"      # el texto del cuadro se lee derecho
    assert pg.rotation == 90 and doc[0].rotation == 0
    doc.close()


def test_filtro_orden_y_fechas():
    hoy = dt.date(2025, 3, 12)
    assert comentarios.fecha_corta(dt.datetime(2025, 3, 12, 15, 42), hoy) == "hoy 3:42 p. m."
    assert comentarios.fecha_corta(dt.datetime(2025, 3, 11, 9, 5), hoy) == "ayer 9:05 a. m."
    assert comentarios.fecha_corta(dt.datetime(2025, 3, 2, 9, 5), hoy) == "2 mar 2025"
    assert comentarios.fecha_larga(dt.datetime(2025, 3, 12, 0, 7)) == "12/03/2025 12:07 a. m."
    assert comentarios.fecha_pdf("D:20250312154200Z").hour == 15
    assert comentarios.fecha_pdf("basura") is None
    f = comentarios.fecha_pdf(comentarios.a_fecha_pdf(dt.datetime(2025, 3, 12, 15, 42)))
    assert f.replace(tzinfo=None) == dt.datetime(2025, 3, 12, 15, 42)

    def c(**k):
        base = dict(id="x", xref=1, pagina=0, tipo="nota", rect=(0, 0, 10, 10), autor="Ana Pérez", texto="",
                    texto_marcado="", creado=None, modificado=None, color="", estado=None, respuestas=[],
                    movible=True)
        base.update(k)
        return comentarios.Comentario(**base)
    a = c(id="a", texto="Revisión de la cifra", pagina=2, creado=dt.datetime(2025, 1, 1))
    b = c(id="b", autor="Luis Gómez", tipo="subrayado", texto_marcado="niñez", pagina=0, estado="Accepted",
          creado=dt.datetime(2025, 2, 1))
    r = comentarios.Respuesta(id="r", xref=9, autor="Luis Gómez", texto="ok", fecha=None)
    d = c(id="d", pagina=1, respuestas=[r], creado=dt.datetime(2024, 5, 1))
    todos = [a, b, d]
    assert [x.id for x in todos if Filtro(texto="revision").acepta(x)] == ["a"]       # sin tildes
    assert [x.id for x in todos if Filtro(texto="NINEZ").acepta(x)] == ["b"]
    assert [x.id for x in todos if Filtro(tipos={"subrayado"}).acepta(x)] == ["b"]
    assert [x.id for x in todos if Filtro(autores={"Luis Gómez"}).acepta(x)] == ["b"]
    assert [x.id for x in todos if Filtro(estados={None}).acepta(x)] == ["a", "d"]
    assert [x.id for x in todos if Filtro(solo_de="ana perez").acepta(x)] == ["a", "d"]
    assert [x.id for x in todos if Filtro(sin_responder=True).acepta(x)] == ["a", "b"]
    assert [x.id for x in comentarios.ordenar(todos, "pagina")] == ["b", "d", "a"]
    assert [x.id for x in comentarios.ordenar(todos, "fecha")] == ["b", "a", "d"]
    assert [x.id for x in comentarios.ordenar(todos, "autor")] == ["d", "a", "b"]     # y por página
    assert Filtro(tipos={"nota"}, sin_responder=True).cuantos() == 2 and not Filtro().activo()


# ======================================================================== resumen y aplanar

def _doc_con_comentarios(tmp_path) -> Path:
    src = _pdf_texto(tmp_path / "carta.pdf", paginas=3)
    ops = [
        _crear("nota", 0, "rpdf-n1", texto="Revisar la fecha de la carta", punto=(420, 60)),
        {"op": "responder", "ref": {"pagina": 0, "nm": "rpdf-n1"}, "nm": "rpdf-r1", "autor": "Luis Gómez",
         "texto": "Ya quedó corregida", "fecha": F2},
        {"op": "estado", "ref": {"pagina": 0, "nm": "rpdf-n1"}, "nm": "rpdf-e1", "autor": "Ana Pérez",
         "estado": "Accepted", "fecha": F3},
        _crear("subrayado", 0, "rpdf-s1", texto="ojo", rects=[_palabra(src, 0, "subrayar")]),
        _crear("cuadro", 2, "rpdf-c1", texto="Cuadro con niñez y Bogotá", rect=(72, 500, 320, 560)),
        {"op": "responder", "ref": {"pagina": 2, "nm": "rpdf-c1"}, "nm": "rpdf-r2", "autor": "Luis Gómez",
         "texto": "Revisado ✓ — listo", "fecha": F3},
    ]
    doc = pymupdf.open(src)
    comentarios.aplicar(doc, ops)
    out = tmp_path / "comentado.pdf"
    doc.save(out)
    doc.close()
    return out


def test_resumen_pdf_xlsx_docx_txt(tmp_path):
    src = _doc_con_comentarios(tmp_path)
    with pymupdf.open(src) as d:
        filas = comentarios.resumen(d)
    assert [f["n"] for f in filas] == [1, 2, 3] and filas[0]["pagina"] == 1 and filas[-1]["pagina"] == 3
    assert any(f["estado"] == "Aceptado" for f in filas)
    salidas = {}
    for fmt in ("pdf", "xlsx", "docx", "txt"):
        out = tmp_path / f"resumen.{fmt}"
        n = comentarios.exportar_resumen(src, out, fmt)
        assert n == 3 and out.exists()
        salidas[fmt] = out
    with pymupdf.open(salidas["pdf"]) as r:
        assert r.page_count >= 2                                           # portada y páginas comentadas
        assert r[0].rect.width == 612 and r[0].rect.height == 792          # tamaño carta
        texto = "\n".join(p.get_text() for p in r)
        for t in ("Resumen de comentarios", "Ana Pérez", "Revisar la fecha de la carta", "Luis Gómez",
                  "Ya quedó corregida", "Aceptado", "«subrayar»", "Página 1", "Página 3", "Por tipo", "Por estado",
                  "Revisado ✓ — listo"):
            assert t in texto, t                                           # se puede buscar y copiar
        assert any(p.get_images() for p in r)                              # miniaturas de las páginas
        assert "1." in texto and "3." in texto                             # lista numerada
    with zipfile.ZipFile(salidas["xlsx"]) as z:
        hoja = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
        assert "Ana Pérez" in hoja and "Ya quedó corregida" in hoja and "Revisar la fecha" in hoja
    with zipfile.ZipFile(salidas["docx"]) as z:
        xml = z.read("word/document.xml").decode("utf-8")
        assert "Ana Pérez" in xml and "Ya quedó corregida" in xml and "Página 3" in xml
    txt = salidas["txt"].read_text(encoding="utf-8-sig")
    assert "Revisar la fecha de la carta" in txt and "Luis Gómez" in txt and "↳" in txt
    # con filtro: solo los de un tipo
    out = tmp_path / "solo_notas.txt"
    assert comentarios.exportar_resumen(src, out, "txt", filtro=Filtro(tipos={"nota"})) == 1
    with pytest.raises(core.UserError):
        comentarios.exportar_resumen(src, tmp_path / "nada.txt", "txt", filtro=Filtro(tipos={"sello"}))
    with pytest.raises(core.UserError):
        comentarios.exportar_resumen(src, src, "pdf")


def test_aplanar_deja_cero_anotaciones_y_el_texto(tmp_path):
    src = _doc_con_comentarios(tmp_path)
    out = tmp_path / "aplanado.pdf"
    assert comentarios.aplanar(src, out) == 3
    with pymupdf.open(out) as d:
        assert sum(len(list(p.annots())) for p in d) == 0
        assert "Bogotá" in d[2].get_text()            # el cuadro de texto quedó como texto de la página
    with pytest.raises(core.UserError):
        comentarios.aplanar(src, src)


def test_aplanar_y_resumen_con_contrasena(tmp_path, protegido):
    ops = [_crear("nota", 0, "rpdf-p1", texto="nota en el protegido", punto=(400, 60))]
    out = tmp_path / "aplanado_protegido.pdf"
    comentarios.aplanar(protegido.path, out, password=protegido.password, ops=ops)
    protegido.comprobar(out, sin_tocar=(1, 2))
    res = tmp_path / "resumen_protegido.txt"
    assert comentarios.exportar_resumen(protegido.path, res, "txt", password=protegido.password, ops=ops) == 1


# ======================================================================== sellos

DATOS = {"nombre": "Ana Pérez", "cargo": "Coordinadora", "fecha": dt.datetime(2025, 3, 12, 15, 42)}


def test_sellos_campos_automaticos_y_texto_plano():
    s = sellos.Sello(id="s-1", nombre="Revisado", lineas=["REVISADO", "{nombre} · {cargo}", "{fecha} {hora}"])
    assert sellos.lineas_resueltas(s, DATOS) == ["REVISADO", "Ana Pérez · Coordinadora", "12/03/2025 3:42 p. m."]
    assert sellos.texto_plano(s, DATOS) == "REVISADO · Ana Pérez · Coordinadora · 12/03/2025 3:42 p. m."
    sin_cargo = dict(DATOS, cargo="")
    assert sellos.lineas_resueltas(s, sin_cargo)[1] == "Ana Pérez"     # no quedan separadores sueltos
    assert sellos.resolver("{fecha_hora}", DATOS) == "12/03/2025 3:42 p. m."
    manana = dict(DATOS, fecha=dt.datetime(2025, 3, 13, 9, 5))
    assert sellos.resolver("{fecha}", manana) == "13/03/2025"           # la fecha es la de cada día
    assert sellos.resolver("-COPIA-", DATOS) == "-COPIA-"


@pytest.mark.parametrize("forma", list(sellos.FORMAS))
def test_sellos_dibujar_cada_forma(forma):
    firma = sellos._png(QImage(120, 40, QImage.Format.Format_ARGB32_Premultiplied))
    s = sellos.Sello(id="s-1", nombre="x", lineas=["APROBADO", "{nombre}", "{fecha}"], forma=forma, borde="doble",
                     relleno=True, imagen=firma, ancho_pt=170)
    png = sellos.dibujar(s, DATOS, escala=4)
    assert png.startswith(b"\x89PNG")
    img = QImage.fromData(png)
    assert img.hasAlphaChannel()
    assert img.pixelColor(0, 0).alpha() == 0                        # fondo transparente
    w, h = sellos.tamano_pt(png, 4)
    esperado = 170 * (0.82 if forma == "circulo" else 1)
    assert esperado < w < esperado + 12 and h > 20
    if forma == "circulo":
        assert abs(w - h) < 2
    inclinado = sellos.dibujar(s.copia(inclinacion=12), DATOS, escala=4)
    assert sellos.tamano_pt(inclinado, 4)[1] > h                      # girado ocupa más alto


def test_almacen_de_sellos(tmp_path):
    alm = sellos.AlmacenSellos(tmp_path / sellos.CARPETA)
    assert alm.listar() == []
    firma = sellos._png(QImage(2400, 600, QImage.Format.Format_ARGB32_Premultiplied))
    s = alm.guardar(sellos.Sello(id="", nombre="Revisado con firma", lineas=["REVISADO", "{nombre}"],
                                 imagen=firma, color="#1E4FA0"))
    assert s.id and (tmp_path / sellos.CARPETA / f"{s.id}.json").exists()
    leido = alm.obtener(s.id)
    assert leido.lineas == ["REVISADO", "{nombre}"] and leido.color == "#1E4FA0"
    assert max(QImage.fromData(leido.imagen).width(), QImage.fromData(leido.imagen).height()) <= 1600
    d = json.loads((tmp_path / sellos.CARPETA / f"{s.id}.json").read_text(encoding="utf-8"))
    assert d["formato"] == "RonyPDF-sello" and isinstance(d["imagen"], str)
    copia = alm.duplicar(s.id)
    assert copia.id != s.id and copia.nombre == "Copia de Revisado con firma"
    ruta = alm.exportar(s.id, tmp_path / "mi_sello")
    assert ruta.suffix == ".ronysello" and ruta.exists()
    imp = alm.importar(ruta)
    assert imp.id not in (s.id, copia.id) and imp.nombre == "Revisado con firma (2)"
    assert len(alm.listar()) == 3
    alm.borrar(copia.id)
    assert {x.id for x in alm.listar()} == {s.id, imp.id}
    (tmp_path / sellos.CARPETA / "danado.json").write_text("{no es json", encoding="utf-8")
    assert len(alm.listar()) == 2                                     # un archivo dañado no rompe la lista
    malo = tmp_path / "malo.ronysello"
    malo.write_text('{"hola": 1}', encoding="utf-8")
    with pytest.raises(core.UserError):
        alm.importar(malo)
    while len(alm.listar()) < sellos.MAX_SELLOS:
        alm.guardar(sellos.Sello(id="", nombre=f"S{len(alm.listar())}", lineas=["OK"]))
    with pytest.raises(core.UserError):
        alm.guardar(sellos.Sello(id="", nombre="uno más", lineas=["NO"]))
    with pytest.raises(core.UserError):
        alm.guardar(sellos.Sello(id="", nombre="vacío", lineas=[""]))


PROHIBIDAS = ("fundaci", "empresa", "entidad", "organizaci", "instituci", "corporaci", "ltda", "s.a.s",
              "membrete", "alcald", "ministerio", "gobierno", "asociaci", "cooperativa")


def test_disenos_de_fabrica_sin_nada_institucional():
    nombres = {s.nombre for s in sellos.PARA_EMPEZAR}
    assert nombres == {"Recibido", "Aprobado", "Revisado", "Copia"}
    recibido = sellos.de_fabrica("inicio-recibido")
    assert "{fecha_hora}" in recibido.lineas and "{nombre}" in recibido.lineas
    aprobado = sellos.de_fabrica("inicio-aprobado")
    assert "{nombre}" in aprobado.lineas and "{fecha}" in aprobado.lineas
    for s in sellos.PARA_EMPEZAR:
        assert s.imagen is None
        texto = " ".join([s.nombre] + s.lineas).lower()
        assert not any(p in texto for p in PROHIBIDAS), texto
    # ni en la interfaz: ningún campo «entidad», «empresa» o «fundación» en los módulos nuevos
    raiz = Path(cui.__file__).parent
    for nombre in ("comentarios.py", "sellos.py", "comentarios_ui.py"):
        codigo = (raiz / nombre).read_text(encoding="utf-8").lower()
        for p in ("fundación", "fundacion", "empresa", "entidad", "membrete"):
            assert p not in codigo, (nombre, p)


# ======================================================================== interfaz: utilidades

@pytest.fixture(scope="module")
def win():
    _app.setStyle("Fusion")
    _app.setPalette(_light_palette())
    _app.setStyleSheet(QSS)
    w = MainWindow()
    w.resize(1240, 820)
    w.show()
    _app.processEvents()
    yield w
    for k in ("comentarios_autor", "comentarios_cargo", "sellos_ultimo"):
        widgets.ctx.settings.data.pop(k, None)
    widgets.ctx.settings.save()


class _Llamadas(list):
    def dialogos(self):
        return [x for x in self if x[0] == "dialogo"]


@pytest.fixture
def done(monkeypatch):
    calls = _Llamadas()

    def fake(parent, title, detail="", file=None, folder=None):
        calls.append(("listo", title, detail, file))

    for mod in (widgets, viewer):
        monkeypatch.setattr(mod, "show_done", fake, raising=False)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: calls.append(("aviso", a[2])))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: calls.append(("info", a[2])))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    from ronypdf import guardado_ui      # «Guardar / No guardar / Cancelar»: «No guardar» (antes, «Sí» descartaba)
    monkeypatch.setattr(guardado_ui, "preguntar_cambios", lambda *a, **k: "descartar")
    # cualquier diálogo que no se espere queda anotado (y cancelado)
    monkeypatch.setattr(QDialog, "exec", lambda self: calls.append(("dialogo", type(self).__name__)) or 0)
    return calls


@pytest.fixture
def visor(win, done):
    v = win.pages["viewer"]
    win.navigate("viewer")
    win.sidebar.set_collapsed(True, remember=False)
    widgets.ctx.settings.set("comentarios_autor", "Ana Pérez")
    widgets.ctx.settings.set("comentarios_cargo", "Coordinadora")
    v.set_view_mode("una", False)
    yield v
    cui._cerrar_editor(v, confirmar=False)
    v.set_tool("select")


def _ev(n=4):
    for _ in range(n):
        _app.processEvents()


def wait(timeout=60):
    fin = time.time() + timeout
    while time.time() < fin:
        _app.processEvents()
        if not widgets.ctx.busy:
            _ev()
            return
        time.sleep(0.01)
    raise TimeoutError


def _mouse(widget, kind, pos, button=Qt.MouseButton.LeftButton, modifiers=Qt.KeyboardModifier.NoModifier, held=None):
    types = {"press": QEvent.Type.MouseButtonPress, "move": QEvent.Type.MouseMove,
             "release": QEvent.Type.MouseButtonRelease, "double": QEvent.Type.MouseButtonDblClick}
    btn = button if kind != "move" else Qt.MouseButton.NoButton
    if held is None:
        held = button if kind in ("press", "double") else Qt.MouseButton.NoButton
    local = QPointF(pos)
    ev = QMouseEvent(types[kind], local, QPointF(widget.mapToGlobal(local.toPoint())), btn, held, modifiers)
    QApplication.sendEvent(widget, ev)
    _app.processEvents()


def _vp(v, pagina, x, y):
    c = v.canvas
    return QPointF(c.mapFromScene(c.pages[pagina].pos() + QPointF(x, y)))


def _ver(v, pagina, y=0):
    v.canvas.go_to_dest(pagina, (0, max(0.0, y - 120)), remember=False)
    _ev()


def clic(v, pagina, x, y, mods=Qt.KeyboardModifier.NoModifier):
    _ver(v, pagina, y)
    p = _vp(v, pagina, x, y)
    vp = v.canvas.viewport()
    _mouse(vp, "move", p, held=Qt.MouseButton.NoButton)
    _mouse(vp, "press", p, modifiers=mods)
    _mouse(vp, "release", p, modifiers=mods)


def arrastrar(v, pagina, a, b, pasos=5):
    _ver(v, pagina, a[1])
    pa, pb = _vp(v, pagina, *a), _vp(v, pagina, *b)
    vp = v.canvas.viewport()
    _mouse(vp, "press", pa)
    for k in range(1, pasos + 1):
        _mouse(vp, "move", pa + (pb - pa) * (k / pasos), held=Qt.MouseButton.LeftButton)
    _mouse(vp, "release", pb)


def tecla(v, key, texto=""):
    QApplication.sendEvent(v.canvas, QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier, texto))
    _ev()


def abrir(v, path) -> None:
    assert v.load(str(path), confirm=False)
    v.canvas.set_fit("width")
    _ev(6)


def anots(path, password=None):
    with core.open_pdf(path, password) as d:
        return [(p.number, a.type[1], a.info.get("title"), a.info.get("content")) for p in d for a in p.annots()]


def _palabras(v, pagina, texto):
    return [w for w in v.canvas.words_for(pagina) if w.texto == texto]


def _est(v):
    return cui._est(v)


def _menu_sel(c):
    """El menú del texto seleccionado, como cuando se abre con el clic derecho."""
    m = c.selection_menu()
    m.aboutToShow.emit()
    return m


# ======================================================================== interfaz: el visor

def test_no_pregunta_el_nombre_al_arrancar_ni_al_abrir(visor, done, tmp_path):
    widgets.ctx.settings.data.pop("comentarios_autor", None)
    abrir(visor, _pdf_texto(tmp_path / "a.pdf"))
    assert done.dialogos() == []
    assert not visor.canvas.has_changes()
    assert visor._paneles["comentarios"][0].text() == "Comentarios (0)"
    panel = cui._panel(visor)
    assert panel.pila.currentIndex() == 1 and "no tiene comentarios" in panel.vacio.text()
    assert "sin nombre" in panel.b_autor.text()


def test_nota_con_clic_primer_comentario_pregunta_el_nombre(visor, done, monkeypatch, tmp_path):
    widgets.ctx.settings.data.pop("comentarios_autor", None)
    abrir(visor, _pdf_texto(tmp_path / "a.pdf"))
    c = visor.canvas
    preguntas = []

    def cancelar(self):
        preguntas.append(self.campo.text())
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(cui.DialogoAutor, "exec", cancelar)
    b_com, b_sello = visor.ext["comentarios_botones"]
    nota = next(a for a in b_com.menu().actions() if a.text().startswith("Nota adhesiva"))
    nota.trigger()
    assert c.tool == "ext:nota" and b_com.isChecked() and not b_sello.isChecked()
    clic(visor, 0, 420, 200)
    assert len(preguntas) == 1                       # se preguntó, con una sugerencia
    assert not list(c.doc[0].annots()) and not c.has_changes()     # cancelar no crea nada

    def aceptar(self):
        preguntas.append(self.campo.text())
        self.campo.setText("Ana Pérez")
        self.campo_cargo.setText("Coordinadora")
        self.guardar()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(cui.DialogoAutor, "exec", aceptar)
    cui.activar(visor, "ext:nota")
    clic(visor, 0, 420, 200)
    assert widgets.ctx.settings.get("comentarios_autor") == "Ana Pérez"
    est = _est(visor)
    assert est.editor is not None and c.tool == "select"
    est.editor.texto.setPlainText("Revisar esta cifra con contabilidad")
    est.editor.terminar()
    assert len(est.ops) == 1 and est.ops[0]["texto"] == "Revisar esta cifra con contabilidad"   # una sola operación
    a = list(c.doc[0].annots())
    assert len(a) == 1 and a[0].info["title"] == "Ana Pérez" and a[0].info["content"].startswith("Revisar")
    assert c.has_changes() and visor.unsaved.isVisible() and visor.b_undo.isEnabled()
    assert visor._paneles["comentarios"][0].text() == "Comentarios (1)"
    assert visor.panel_actual() == "comentarios"
    # la segunda vez ya no pregunta; cambiar el nombre afecta solo a los comentarios nuevos

    def cambiar(self):
        self.campo.setText("Luis Gómez")
        self.guardar()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(cui.DialogoAutor, "exec", cambiar)
    cui._panel(visor).cambiar_autor()
    assert "Luis Gómez" in cui._panel(visor).b_autor.text()
    n_preguntas = len(preguntas)
    cui.crear_nota(visor, 0, 300, 400, "Segunda", editar=False)
    assert len(preguntas) == n_preguntas
    autores = sorted(a.info["title"] for a in c.doc[0].annots())
    assert autores == ["Ana Pérez", "Luis Gómez"]
    widgets.ctx.settings.set("comentarios_autor", "Ana Pérez")


def test_subrayar_tachar_y_nota_desde_la_seleccion(visor, done, tmp_path):
    abrir(visor, _pdf_texto(tmp_path / "a.pdf"))
    c = visor.canvas
    sub = _palabras(visor, 0, "subrayar")[0]
    c.select_words(0, sub.indice, sub.indice)
    menu = c.selection_menu()
    assert "Subrayar" not in [a.text() for a in menu.actions()]     # se agregan al mostrar el menú
    menu.aboutToShow.emit()
    menu.aboutToShow.emit()
    textos = [a.text() for a in menu.actions()]
    assert {"Subrayar", "Tachar", "Agregar una nota a esta selección"} <= set(textos)
    assert textos.count("Subrayar") == 1
    next(a for a in menu.actions() if a.text() == "Subrayar").trigger()
    _ev()
    assert not c.has_selection()
    tach = _palabras(visor, 0, "tachar")[1]
    c.select_words(0, tach.indice, tach.indice)
    next(a for a in _menu_sel(c).actions() if a.text() == "Tachar").trigger()
    rev = _palabras(visor, 0, "revisión")[2]
    c.select_words(0, rev.indice - 1, rev.indice)
    next(a for a in _menu_sel(c).actions() if a.text().startswith("Agregar una nota")).trigger()
    est = _est(visor)
    assert est.editor is not None
    est.editor.texto.setPlainText("¿Esta revisión ya pasó?")
    est.editor.terminar()
    pg = c.doc[0]
    tipos = {a.type[1]: a for a in pg.annots()}
    assert set(tipos) == {"Underline", "StrikeOut", "Highlight"}
    # los quads caen sobre las palabras elegidas
    for tipo, w in (("Underline", sub), ("StrikeOut", tach)):
        q = pymupdf.Rect(comentarios._quads_de(tipos[tipo])[0])
        r = pymupdf.Rect(w.rect)
        assert abs(q.x0 - r.x0) < 1.5 and abs(q.x1 - r.x1) < 1.5 and q.intersects(r)
    assert tipos["Highlight"].info["content"] == "¿Esta revisión ya pasó?"
    assert len(est.ops) == 3
    # el texto marcado se calcula solo, poco a poco
    for _ in range(20):
        _ev()
    hl = next(cm for cm in est.comentarios if cm.tipo == "resaltado")
    assert hl.texto_marcado == "la revisión"


def test_marcar_arrastrando_con_la_herramienta_y_pagina_escaneada(visor, done, tmp_path, scanned):
    abrir(visor, _pdf_texto(tmp_path / "a.pdf"))
    c = visor.canvas
    a = _palabras(visor, 0, "texto")[0]
    b = _palabras(visor, 0, "tachar")[1]
    cui.activar(visor, "ext:ondulado")
    pa = ((a.rect[0] + a.rect[2]) / 2, (a.rect[1] + a.rect[3]) / 2)
    pb = ((b.rect[0] + b.rect[2]) / 2, (b.rect[1] + b.rect[3]) / 2)
    arrastrar(visor, 0, pa, pb)
    pg = c.doc[0]
    sq = [x for x in pg.annots() if x.type[1] == "Squiggly"]
    assert len(sq) == 1 and len(comentarios._quads_de(sq[0])) == 2        # un quad por renglón
    assert c.tool == "ext:ondulado"                                           # sigue lista para otro renglón
    tecla(visor, Qt.Key.Key_Escape)
    assert c.tool == "select"
    # en un escaneo sin texto se explica qué hacer
    abrir(visor, scanned)
    mensajes = []
    c.status.connect(mensajes.append)
    cui.activar(visor, "ext:subrayar")
    clic(visor, 0, 200, 300)
    c.status.disconnect(mensajes.append)
    assert cui.SIN_TEXTO_MSG in mensajes and not list(c.doc[0].annots())
    # nota, cuadro y sello en la página normal y en la girada (/Rotate 90)
    visor.set_tool("select")
    for pagina in (0, 1):
        cui.crear_nota(visor, pagina, 100, 120, f"nota {pagina}", editar=False)
        cui.crear_cuadro(visor, pagina, (100, 300, 320, 360), f"cuadro {pagina}", editar=False)
        cui.poner_sello(visor, sellos.de_fabrica("inicio-aprobado"), pagina, 300, 500)
    t = {(cm.pagina, cm.tipo): cm for cm in _est(visor).comentarios}
    assert t[(1, "nota")].rect[:2] == pytest.approx((100, 120), abs=0.6)
    assert t[(1, "cuadro")].rect == pytest.approx((100, 300, 320, 360), abs=1)
    s1 = pymupdf.Rect(t[(1, "sello")].rect)
    assert abs((s1.x0 + s1.x1) / 2 - 300) < 1 and abs((s1.y0 + s1.y1) / 2 - 500) < 1
    assert s1.width > s1.height                      # derecho: más ancho que alto, también en la página girada


def test_cuadro_y_sello_con_el_raton(visor, done, tmp_path):
    abrir(visor, _pdf_texto(tmp_path / "a.pdf"))
    c = visor.canvas
    cui.activar(visor, "ext:cuadro")
    arrastrar(visor, 0, (100, 450), (320, 520))
    est = _est(visor)
    assert est.editor is not None and c.tool == "select"
    est.editor.texto.setPlainText("Texto del cuadro con ñ")
    est.editor.terminar()
    pg = c.doc[0]
    ft = [a for a in pg.annots() if a.type[1] == "FreeText"]
    assert len(ft) == 1 and ft[0].info["content"] == "Texto del cuadro con ñ"
    assert tuple(comentarios._bound(ft[0])) == pytest.approx((100, 450, 320, 520), abs=1.5)
    # sello: la vista fantasma sigue al ratón y un clic lo pone con la fecha de hoy
    cui.armar_sello(visor, sellos.de_fabrica("inicio-recibido"))
    assert c.tool == "ext:sello" and visor.ext["comentarios_botones"][1].isChecked()
    _ver(visor, 0, 650)
    _mouse(c.viewport(), "move", _vp(visor, 0, 300, 650), held=Qt.MouseButton.NoButton)
    assert est.fantasma is not None and est.fantasma.isVisible()
    clic(visor, 0, 300, 650)
    pg = c.doc[0]
    st = [a for a in pg.annots() if a.type[1] == "Stamp"]
    hoy = dt.date.today()
    assert len(st) == 1 and "RECIBIDO" in st[0].info["content"] and "Ana Pérez" in st[0].info["content"]
    assert f"{hoy.day:02d}/{hoy.month:02d}/{hoy.year}" in st[0].info["content"]
    assert c.tool == "select" and est.fantasma is None
    # con Mayús se queda puesto para seguir sellando; la S repite el último
    tecla(visor, Qt.Key.Key_S, "s")
    assert c.tool == "ext:sello"
    clic(visor, 0, 150, 700, Qt.KeyboardModifier.ShiftModifier)
    assert c.tool == "ext:sello"
    clic(visor, 0, 450, 700)
    assert c.tool == "select"
    assert len([a for a in c.doc[0].annots() if a.type[1] == "Stamp"]) == 3


def test_seleccionar_mover_redimensionar_borrar_y_deshacer(visor, done, tmp_path):
    abrir(visor, _pdf_texto(tmp_path / "a.pdf"))
    c = visor.canvas
    est = _est(visor)
    cui.crear_nota(visor, 0, 300, 300, "Para mover", editar=False)
    cui.seleccionar(visor, None)
    visor.mostrar_panel("props")
    clic(visor, 0, 307, 307)
    nota = cui.seleccionado(visor)
    assert nota is not None and nota.tipo == "nota" and est.marco is not None
    assert visor.panel_actual() == "comentarios" and cui._panel(visor).sel == nota.id
    # clic en una zona vacía la suelta
    clic(visor, 0, 500, 650)
    assert cui.seleccionado(visor) is None
    # arrastrar mueve la nota
    z = c.zoom
    arrastrar(visor, 0, (307, 307), (307 + 60, 307 + 40))
    movida = next(cm for cm in est.comentarios if cm.tipo == "nota")
    assert movida.rect[0] == pytest.approx(300 + 60, abs=2 / z + 0.5)
    assert movida.rect[1] == pytest.approx(300 + 40, abs=2 / z + 0.5)
    # un sello cambia de tamaño con el tirador y conserva su proporción
    cui.poner_sello(visor, sellos.de_fabrica("inicio-aprobado"), 0, 300, 560)
    s = cui.seleccionado(visor)
    assert s.tipo == "sello" and est.tirador is not None
    w0, h0 = s.rect[2] - s.rect[0], s.rect[3] - s.rect[1]
    esquina = (s.rect[2] + 3 / c.zoom, s.rect[3] + 3 / c.zoom)
    arrastrar(visor, 0, esquina, (esquina[0] + 40, esquina[1] + 5))
    s2 = next(cm for cm in est.comentarios if cm.tipo == "sello")
    w1, h1 = s2.rect[2] - s2.rect[0], s2.rect[3] - s2.rect[1]
    assert w1 > w0 + 30 and h1 / w1 == pytest.approx(h0 / w0, rel=0.02)
    # Supr lo borra y Deshacer lo trae de vuelta
    cui.seleccionar(visor, s2)
    tecla(visor, Qt.Key.Key_Delete)
    assert not [cm for cm in est.comentarios if cm.tipo == "sello"]
    visor._undo()
    assert [cm for cm in est.comentarios if cm.tipo == "sello"]
    # doble clic edita el texto
    n = next(cm for cm in est.comentarios if cm.tipo == "nota")
    _ver(visor, 0, n.rect[1])
    p = _vp(visor, 0, n.rect[0] + 6, n.rect[1] + 6)
    _mouse(c.viewport(), "press", p)
    _mouse(c.viewport(), "release", p)
    _mouse(c.viewport(), "double", p)
    _mouse(c.viewport(), "release", p)
    assert est.editor is not None and est.editor.texto.toPlainText() == "Para mover"
    est.editor.texto.setPlainText("Texto editado")
    est.editor.terminar()
    assert next(cm for cm in est.comentarios if cm.tipo == "nota").texto == "Texto editado"
    # menú del clic derecho
    m = cui.menu_comentario(visor, next(cm for cm in est.comentarios if cm.tipo == "nota"))
    assert [a.text() for a in m.actions() if a.text()][:5] == ["Responder", "Estado", "Editar el texto…",
                                                                "Cambiar color", "Copiar el texto"]
    # ayuda al pasar el ratón
    assert "Ana Pérez" in cui._ayuda(n)


def test_deshacer_mezclado_con_el_visor_y_rehacer(visor, done, tmp_path):
    from PySide6.QtCore import QRectF
    abrir(visor, _pdf_texto(tmp_path / "a.pdf"))
    c = visor.canvas
    est = _est(visor)
    page = c.pages[0]
    c.add_shape("rect", QRectF(page.pos() + QPointF(72, 600), page.pos() + QPointF(200, 680)))
    cui.crear_nota(visor, 0, 400, 100, "nota", editar=False)
    acc_nota = c.undo_stack[-1][1]
    cui.poner_sello(visor, sellos.de_fabrica("inicio-copia"), 0, 400, 400)
    acc_sello = c.undo_stack[-1][1]
    nota = next(cm for cm in est.comentarios if cm.tipo == "nota")
    cui.responder(visor, nota, "respuesta")
    cui.cambiar_estado(visor, next(cm for cm in est.comentarios if cm.tipo == "nota"), "Completed")
    assert len(est.ops) == 4 and len(c.undo_stack) == 5
    assert all(hasattr(a, "rehacer") and a.texto for k, a in c.undo_stack if k == "ext")
    c.undo()
    assert next(cm for cm in est.comentarios if cm.tipo == "nota").estado is None
    c.undo()
    assert not next(cm for cm in est.comentarios if cm.tipo == "nota").respuestas
    c.undo()
    assert [cm.tipo for cm in est.comentarios] == ["nota"]
    c.undo()
    assert est.comentarios == [] and est.ops == []
    assert c.has_changes()                                    # queda el rectángulo del visor
    c.undo()
    assert not c.has_changes() and not c.undo_stack
    acc_nota.rehacer()
    acc_sello.rehacer()
    assert sorted(cm.tipo for cm in est.comentarios) == ["nota", "sello"] and c.has_changes()
    acc_sello.deshacer()
    assert [cm.tipo for cm in est.comentarios] == ["nota"]


def test_guardar_comentarios_y_ediciones_del_visor(visor, done, monkeypatch, tmp_path):
    src = _pdf_texto(tmp_path / "a.pdf")
    abrir(visor, src)
    c = visor.canvas
    c.add_text(c.pages[1].pos() + QPointF(72, 700), "Texto agregado con el visor")
    cui.crear_nota(visor, 0, 400, 100, "Nota guardada", editar=False)
    cui.poner_sello(visor, sellos.de_fabrica("inicio-recibido"), 1, 300, 400)
    out = tmp_path / "guardado.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out), "")))
    visor.save()
    wait()
    assert c.path == str(out) and not c.has_changes() and _est(visor).ops == []
    a = anots(out)
    assert (0, "Text", "Ana Pérez", "Nota guardada") in a and any(x[1] == "Stamp" and x[0] == 1 for x in a)
    with pymupdf.open(out) as d:
        assert "Texto agregado con el visor" in d[1].get_text()
    assert visor._paneles["comentarios"][0].text() == "Comentarios (2)"
    # solo comentarios, guardando sobre el mismo archivo; los ya guardados se ubican por xref
    nota = next(cm for cm in _est(visor).comentarios if cm.tipo == "nota")
    cui.responder(visor, nota, "Respuesta guardada")
    cui.cambiar_estado(visor, next(cm for cm in _est(visor).comentarios if cm.tipo == "nota"), "Accepted")
    visor.save()
    wait()
    assert not c.has_changes()
    with pymupdf.open(out) as d:
        lista = comentarios.leer(d)
    n = next(cm for cm in lista if cm.tipo == "nota")
    assert n.estado == "Accepted" and [r.texto for r in n.respuestas if not r.es_estado] == ["Respuesta guardada"]
    assert anots(src) == []                                      # el original no cambió


def test_guardado_que_falla_no_pierde_los_comentarios(visor, done, monkeypatch, tmp_path):
    src = _pdf_texto(tmp_path / "a.pdf")
    abrir(visor, src)
    c = visor.canvas
    cui.crear_nota(visor, 0, 400, 100, "No se pierde", editar=False)

    def falla(*a, **k):
        raise core.UserError("disco lleno")

    monkeypatch.setattr(core, "apply_edits", falla)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(src), "")))
    visor.save()
    wait()
    assert ("aviso", "disco lleno") in done
    assert c.doc is not None and [a.info["content"] for a in c.doc[0].annots()] == ["No se pierde"]
    assert c.has_changes() and len(_est(visor).ops) == 1


def test_guardar_con_contrasena_conserva_todo(visor, done, monkeypatch, tmp_path, protegido):
    widgets.ctx.passwords.set(str(protegido.path), protegido.password)
    abrir(visor, protegido.path)
    cui.crear_nota(visor, 0, 450, 60, "Comentario en el protegido", editar=False)
    out = tmp_path / "protegido_comentado.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out), "")))
    visor.save()
    wait()
    protegido.comprobar(out, sin_tocar=(1, 2))
    assert anots(out, protegido.password) == [(0, "Text", "Ana Pérez", "Comentario en el protegido")]


def test_pdf_que_restringe_comentar_avisa_una_vez(visor, done, tmp_path):
    src = tmp_path / "restringido.pdf"
    with pymupdf.open() as d:
        d.new_page().insert_text((72, 100), "No se permiten comentarios", fontsize=12)
        d.save(src, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="dueña", user_pw="",
               permissions=int(pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_COPY))
    abrir(visor, src)
    assert visor.ext_avisos.isVisible()
    aviso = cui.aviso_visor(visor)
    assert aviso and "no se le agreguen comentarios" in aviso[0]
    aviso[2]()
    assert cui.aviso_visor(visor) is None
    assert cui.crear_nota(visor, 0, 300, 300, "igual se puede", editar=False) is not None


def test_panel_filtros_busqueda_orden_responder_y_estado(visor, done, tmp_path):
    src = _pdf_texto(tmp_path / "a.pdf")
    ops = [
        _crear("nota", 0, "rpdf-a", texto="Revisar la fecha", punto=(400, 60)),
        dict(_crear("nota", 1, "rpdf-b", texto="Cifra de presupuesto", punto=(400, 60)), autor="Luis Gómez"),
        _crear("subrayado", 2, "rpdf-c", texto="", rects=[_palabra(src, 2, "subrayar")]),
        {"op": "responder", "ref": {"pagina": 1, "nm": "rpdf-b"}, "nm": "rpdf-d", "autor": "Ana Pérez",
         "texto": "Ya la corregí", "fecha": F2},
    ]
    with pymupdf.open(src) as d:
        comentarios.aplicar(d, ops)
        d.save(tmp_path / "con_comentarios.pdf")
    abrir(visor, tmp_path / "con_comentarios.pdf")
    panel = cui._panel(visor)
    assert visor._paneles["comentarios"][0].text() == "Comentarios (3)" and panel.modelo.rowCount() == 3
    panel.buscar.setText("presupuesto")
    panel._aplicar_busqueda()
    assert panel.modelo.rowCount() == 1 and panel.cuenta.text() == "1 de 3"
    panel.buscar.setText("corregi")                              # busca también en las respuestas, sin tildes
    panel._aplicar_busqueda()
    assert panel.modelo.rowCount() == 1
    panel.quitar_filtros()
    panel.filtrar(tipos={"subrayado"})
    assert [cm.tipo for cm in panel.modelo.filas] == ["subrayado"]
    panel.filtrar(autores={"Luis Gómez"})
    assert [cm.autor for cm in panel.modelo.filas] == ["Luis Gómez"]
    panel.filtrar()
    panel.solo_mios(True)
    assert {cm.autor for cm in panel.modelo.filas} == {"Ana Pérez"}
    panel.solo_mios(False)
    panel.sin_responder(True)
    assert panel.modelo.rowCount() == 2
    panel._llenar_filtros()
    assert panel.b_filtros.text() == "Filtros (1) ▾"
    panel.quitar_filtros()
    panel.ordenar("autor")
    assert [cm.autor for cm in panel.modelo.filas] == ["Ana Pérez", "Ana Pérez", "Luis Gómez"]
    panel.ordenar("pagina")
    assert [cm.pagina for cm in panel.modelo.filas] == [0, 1, 2]
    # clic en una tarjeta: lleva a su página y la selecciona en el lienzo
    luis = panel.modelo.filas[1]
    panel.elegir(luis.id)
    _ev()
    assert cui.seleccionado(visor).id == luis.id and _est(visor).marco is not None
    assert visor.canvas.current_page == 1
    assert panel._acciones is not None
    # responder: Enter envía (Mayús+Enter salta de línea)
    panel.abrir_respuesta()
    caja = panel._acciones.caja
    caja.setPlainText("Gracias, Ana")
    QApplication.sendEvent(caja, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier))
    _ev()
    cm = cui.seleccionado(visor)
    assert [r.texto for r in cm.respuestas if not r.es_estado] == ["Ya la corregí", "Gracias, Ana"]
    panel.estado("Accepted")
    assert cui.seleccionado(visor).estado == "Accepted"
    panel.filtrar(estados={"Accepted"})
    assert [x.id for x in panel.modelo.filas] == [luis.id]
    panel.quitar_filtros()
    # sin un widget por comentario: solo la tarjeta seleccionada tiene botones (los anteriores se borran
    # al volver al ciclo de eventos)
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert len(panel.lista.findChildren(cui._Acciones)) <= 1


def test_trescientas_paginas_y_mil_quinientos_comentarios(visor, done, tmp_path):
    src = tmp_path / "grande.pdf"
    with pymupdf.open() as d:
        for i in range(300):
            p = d.new_page(width=612, height=792)
            for k in range(10):
                p.insert_text((72, 90 + k * 30), f"Renglón {k} de la página {i + 1} para comentar", fontsize=11)
        d.save(src)
    with pymupdf.open(src) as d:
        for i in range(300):
            p = d[i]
            ws = p.get_text("words")
            n = p.add_text_annot((420, 60), f"Nota {i}")
            n.set_info(title="Ana Pérez" if i % 2 else "Luis Gómez", content=f"Nota número {i}")
            n.update()
            for w in (ws[2], ws[12]):
                h = p.add_highlight_annot([pymupdf.Rect(w[:4]).quad])
                h.set_info(title="Luis Gómez")
                h.update()
            u = p.add_underline_annot([pymupdf.Rect(ws[20][:4]).quad])
            u.set_info(title="Ana Pérez")
            u.update()
            f = p.add_freetext_annot(pymupdf.Rect(300, 600, 500, 650), f"cuadro {i}", fontsize=11, fontname="helv",
                                     text_color=(0, 0, 0), fill_color=(1, 1, .8), border_width=1)
            f.set_info(title="Ana Pérez")
            f.update()
            r = p.add_text_annot((420, 60), "respuesta")
            r.set_irt_xref(n.xref)
            r.set_info(title="Marta Ruiz")
            r.update()
        d.save(tmp_path / "grande_c.pdf", garbage=3, deflate=True)
    t0 = time.perf_counter()
    assert visor.load(str(tmp_path / "grande_c.pdf"), confirm=False)
    _ev(2)
    abrir_s = time.perf_counter() - t0
    panel = cui._panel(visor)
    assert visor._paneles["comentarios"][0].text() == "Comentarios (1500)"
    assert abrir_s < 2.0, f"abrir tardó {abrir_s:.2f} s"
    t0 = time.perf_counter()
    panel.buscar.setText("numero 137")
    panel._aplicar_busqueda()
    _ev(2)
    buscar_s = time.perf_counter() - t0
    assert panel.modelo.rowCount() == 1 and buscar_s < 0.6, f"buscar tardó {buscar_s:.2f} s"
    t0 = time.perf_counter()
    panel.quitar_filtros()
    panel.filtrar(tipos={"resaltado"})
    _ev(2)
    filtrar_s = time.perf_counter() - t0
    assert panel.modelo.rowCount() == 600 and filtrar_s < 0.8, f"filtrar tardó {filtrar_s:.2f} s"
    panel.quitar_filtros()
    t0 = time.perf_counter()
    while _est(visor).pend_marcado and time.perf_counter() - t0 < 30:
        _ev(1)
    marcado_s = time.perf_counter() - t0
    assert not _est(visor).pend_marcado
    t0 = time.perf_counter()
    cui.crear_nota(visor, 150, 300, 300, "nueva", editar=False)
    nota_s = time.perf_counter() - t0
    assert visor._paneles["comentarios"][0].text() == "Comentarios (1501)" and nota_s < 1.0
    t0 = time.perf_counter()
    visor._undo()
    deshacer_s = time.perf_counter() - t0
    assert visor._paneles["comentarios"][0].text() == "Comentarios (1500)" and deshacer_s < 2.0
    print(f"\n300 páginas / 1500 comentarios: abrir {abrir_s:.2f} s · buscar {buscar_s:.3f} s · filtrar "
          f"{filtrar_s:.3f} s · texto marcado en segundo plano {marcado_s:.2f} s · nota nueva {nota_s:.3f} s · "
          f"deshacer {deshacer_s:.2f} s")


def test_resumen_y_aplanar_desde_el_visor(visor, done, tmp_path):
    src = _doc_con_comentarios(tmp_path)
    abrir(visor, src)
    cui.crear_nota(visor, 1, 300, 300, "Nota sin guardar todavía", editar=False)
    acciones = [a.text() for a in visor.b_mas.menu().actions()]
    assert "Resumen de comentarios…" in acciones and "Aplanar comentarios en una copia…" in acciones
    out = tmp_path / "resumen_visor.pdf"
    cui.resumen_desde_visor(visor, "pdf", False, str(out))
    wait()
    with pymupdf.open(out) as r:
        assert "Nota sin guardar todavía" in "".join(p.get_text() for p in r)
    assert done[-1][0] == "listo"
    out_x = tmp_path / "resumen_visor.xlsx"
    cui.resumen_desde_visor(visor, "xlsx", False, str(out_x))
    wait()
    assert zipfile.is_zipfile(out_x)
    plano = tmp_path / "aplanado_visor.pdf"
    cui.aplanar_desde_visor(visor, str(plano))
    wait()
    with pymupdf.open(plano) as d:
        assert sum(len(list(p.annots())) for p in d) == 0
    cui.aplanar_desde_visor(visor, str(src))                 # nunca sobre el original
    assert done[-1][0] == "aviso"


def test_capturas_del_visor_a_1000_y_1240(visor, done, win, tmp_path):
    abrir(visor, _doc_con_comentarios(tmp_path))
    salida = Path(os.environ.get("RONYPDF_CAPTURAS") or tmp_path)
    salida.mkdir(parents=True, exist_ok=True)
    cui.seleccionar(visor, _est(visor).comentarios[0])
    for ancho, alto in ((1000, 660), (1240, 820)):
        for menu in (False, True):
            win.resize(ancho, alto)
            win.sidebar.set_collapsed(not menu, remember=False)
            _ev(8)
            for barra in (visor.toolbar, visor.header, visor.footer):
                assert barra.minimumSizeHint().width() <= visor.width(), (ancho, menu, barra.objectName())
            b_com, b_sello = visor.ext["comentarios_botones"]
            assert b_com.isVisible() and b_sello.isVisible()
            win.grab().save(str(salida / f"comentarios-visor-{ancho}{'-menu' if menu else ''}.png"))
    win.resize(1000, 660)
    win.sidebar.set_collapsed(False, remember=False)
    _ev(8)
    assert visor.width() <= 1000 - 248          # menú lateral abierto (248 px desde el rediseño 2.0)
    b_com, _ = visor.ext["comentarios_botones"]
    assert not b_com.icon().isNull() and b_com.toolTip()
    win.resize(1240, 820)
    win.sidebar.set_collapsed(True, remember=False)
    _ev(4)


def test_teclas_color_tamano_seleccion_de_texto_y_vistas(visor, done, win, monkeypatch, tmp_path):
    abrir(visor, _pdf_texto(tmp_path / "a.pdf"))
    c = visor.canvas
    est = _est(visor)
    for letra, herr in (("n", "ext:nota"), ("u", "ext:subrayar"), ("k", "ext:tachar"), ("x", "ext:cuadro")):
        tecla(visor, getattr(Qt.Key, f"Key_{letra.upper()}"), letra)
        assert c.tool == herr
        tecla(visor, Qt.Key.Key_Escape)
        assert c.tool == "select"
    tecla(visor, Qt.Key.Key_T, "t")                     # las letras del visor siguen siendo suyas
    assert c.tool == "text"
    visor.set_tool("select")
    # color de una nota, de un cuadro y de un subrayado
    cui.crear_nota(visor, 0, 420, 80, "color", editar=False)
    cui.crear_cuadro(visor, 0, (72, 500, 260, 550), "cuadro", editar=False)
    sub = _palabras(visor, 0, "subrayar")[0]
    c.select_words(0, sub.indice, sub.indice)
    cui.marcar_seleccion(visor, "subrayado")
    for tipo, hexa in (("nota", "#2563EB"), ("cuadro", "#B42323"), ("subrayado", "#D63A3A")):
        cm = next(x for x in est.comentarios if x.tipo == tipo)
        cui.cambiar_color(visor, cm, hexa)
        assert next(x for x in est.comentarios if x.tipo == tipo).color == hexa
    # el cuadro cambia de tamaño (y el texto se vuelve a acomodar)
    cuadro = next(x for x in est.comentarios if x.tipo == "cuadro")
    cui.mover(visor, cuadro, (72, 500, 360, 600))
    assert next(x for x in est.comentarios if x.tipo == "cuadro").rect == pytest.approx((72, 500, 360, 600), abs=1)
    # arrastrar desde un subrayado selecciona el texto (no mueve la marca)
    sub_cm = next(x for x in est.comentarios if x.tipo == "subrayado")
    otra = _palabras(visor, 0, "revisión")[0]
    a = ((sub.rect[0] + sub.rect[2]) / 2, (sub.rect[1] + sub.rect[3]) / 2)
    b = ((otra.rect[0] + otra.rect[2]) / 2, (otra.rect[1] + otra.rect[3]) / 2)
    arrastrar(visor, 0, a, b)
    assert c.has_selection() and "subrayar y tachar en la revisión" in c.selected_text()
    assert cui.seleccionado(visor) is None
    assert next(x for x in est.comentarios if x.tipo == "subrayado").rect == sub_cm.rect
    c.clear_selection()
    # ayuda al pasar el ratón
    nota = next(x for x in est.comentarios if x.tipo == "nota")
    _ver(visor, 0, nota.rect[1])
    _mouse(c.viewport(), "move", _vp(visor, 0, nota.rect[0] + 5, nota.rect[1] + 5), held=Qt.MouseButton.NoButton)
    assert est.hover_id == nota.id and c.viewport().cursor().shape() == Qt.CursorShape.SizeAllCursor
    # dos páginas y modo noche: el marco va dentro de la página
    visor.set_view_mode("dos", False)
    visor.set_night(True)
    _ev()
    cui.seleccionar(visor, nota)
    assert est.marco.parentItem() is c.pages[0]
    visor.set_night(False)
    visor.set_view_mode("una", False)
    # con la vista girada las herramientas no se activan
    est.ops.clear()
    c.undo_stack.clear()
    c.edits_changed.emit()
    visor.rotate_view()
    cui.activar(visor, "ext:nota")
    assert c.tool == "select"
    while c.view_rot:
        visor.rotate_view()
    # cerrar la aplicación con comentarios sin guardar pregunta
    cui.crear_nota(visor, 0, 300, 300, "sin guardar", editar=False)
    preguntas = []
    # «Guardar / No guardar / Cancelar» (paquete de guardado seguro): «Cancelar» deja la aplicación abierta
    from ronypdf import guardado_ui
    monkeypatch.setattr(guardado_ui, "preguntar_cambios",
                        lambda _p, _t, texto: preguntas.append(texto) or "cancelar")
    assert not win.close()
    assert preguntas and "Ver y editar PDF" in preguntas[0]
    assert win.isVisible()


# ======================================================================== interfaz: «Mis sellos»

def test_pagina_mis_sellos_y_disenador(win, done, monkeypatch, tmp_path):
    widgets.ctx.settings.set("comentarios_autor", "Ana Pérez")
    pagina = win.pages["stamps"]
    win.navigate("stamps")
    carpeta = pagina.almacen.carpeta
    if carpeta.exists():
        shutil.rmtree(carpeta)
    pagina.recargar()
    pdf = _pdf_texto(tmp_path / "para sellar.pdf")
    pagina.accept_files([str(pdf)])
    assert done.dialogos() == [] and "para sellar.pdf" in pagina.action.text()
    firma = sellos._png(QImage(300, 100, QImage.Format.Format_ARGB32_Premultiplied))

    def disenar(self):
        self.e_nombre.setText("Revisado con mi firma")
        self.e_principal.setText("REVISADO")
        self.e_lineas.setPlainText("{nombre}\n{fecha}")
        self.c_forma.setCurrentIndex(list(sellos.FORMAS).index("rectangulo"))
        self.poner_color("#1E4FA0")
        self.poner_imagen(firma)
        self.e_mi_cargo.setText("Coordinadora")
        self.actualizar()
        assert self.vista.png is not None
        self.guardar()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(cui.DialogoSello, "exec", disenar)
    s = pagina.nuevo()
    assert s is not None and pagina.cuadricula.count() == 1
    guardado = pagina.almacen.obtener(s.id)
    assert guardado.lineas == ["REVISADO", "{nombre}", "{fecha}"] and guardado.imagen and guardado.color == "#1E4FA0"
    assert widgets.ctx.settings.get("comentarios_cargo") == "Coordinadora"
    assert (carpeta / f"{s.id}.json").exists() and carpeta.name == "RonyPDF_sellos"

    def editar(self):
        self.e_lineas.setPlainText("{nombre} · {cargo}\n{fecha_hora}")
        self.guardar()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(cui.DialogoSello, "exec", editar)
    pagina.cuadricula.item(0).setSelected(True)
    pagina.editar()
    assert pagina.almacen.obtener(s.id).lineas[1:] == ["{nombre} · {cargo}", "{fecha_hora}"]
    dup = pagina.duplicar()
    assert dup is not None and pagina.cuadricula.count() == 2
    exp = tmp_path / "exportado.ronysello"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(exp), "")))
    assert pagina.exportar() and exp.exists()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(exp), "")))
    assert pagina.importar() is not None and pagina.cuadricula.count() == 3
    assert pagina.eliminar(confirmar=False) and pagina.cuadricula.count() == 2
    # «Usar como base» copia un diseño genérico para editarlo
    monkeypatch.setattr(cui.DialogoSello, "exec", lambda self: (self.guardar(), QDialog.DialogCode.Accepted)[-1])
    pagina.inicio.item(0).setSelected(True)
    base = pagina.usar_como_base()
    assert base is not None and base.nombre == "Recibido (mío)" and pagina.cuadricula.count() == 3
    # aparece en el menú «Sello» del visor, con los cuatro de «Para empezar»
    v = win.pages["viewer"]
    menu = v.ext["comentarios_botones"][1].menu()
    cui.llenar_menu_sellos(v, menu)
    nombres = [a.text() for a in menu.actions()]
    assert "Revisado con mi firma" in nombres and "Diseñar un sello nuevo…" in nombres
    assert {"Recibido", "Aprobado", "Revisado", "Copia"} <= set(nombres)
    # capturas de la página a 1000 (menú abierto) y 1240
    salida = Path(os.environ.get("RONYPDF_CAPTURAS") or tmp_path)
    salida.mkdir(parents=True, exist_ok=True)
    for ancho, alto, menu_abierto in ((1000, 660, True), (1240, 820, False)):
        win.resize(ancho, alto)
        win.sidebar.set_collapsed(not menu_abierto, remember=False)
        _ev(8)
        assert pagina.minimumSizeHint().width() <= pagina.width() + 1
        win.grab().save(str(salida / f"mis-sellos-{ancho}.png"))
    win.resize(1240, 820)
    win.sidebar.set_collapsed(True, remember=False)
    # el diseñador mismo
    d = cui.DialogoSello(None, None, pagina.almacen)
    d.resize(820, 620)
    d.e_principal.setText("REVISADO")
    d.e_lineas.setPlainText("{nombre} · {cargo}\n{fecha}")
    d.poner_imagen(firma)
    d.actualizar()
    d.grab().save(str(salida / "disenador-de-sellos.png"))
    d.deleteLater()
    # «Poner un sello en «archivo.pdf»» abre el visor con la herramienta lista
    pagina.cuadricula.clearSelection()
    pagina.cuadricula.item(0).setSelected(True)
    pagina.run()
    _ev(4)
    assert win.stack.currentWidget() is v and v.canvas.tool == "ext:sello" and v.canvas.path == str(pdf)
    v.set_tool("select")


# ======================================================================== archivos reales

def _con_word() -> bool:
    try:
        from ronypdf import oficina
        return os.name == "nt" and bool(oficina.detectar().word)
    except Exception:  # noqa: BLE001
        return False


con_word = pytest.mark.skipif(not _con_word(), reason="Microsoft Word no está instalado")


def _procesos_office(*nombres: str) -> set[int]:
    nombres = nombres or ("WINWORD.EXE", "EXCEL.EXE")
    out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    pids = set()
    for linea in out.stdout.splitlines():
        partes = [x.strip('"') for x in linea.split('","')]
        if len(partes) > 1 and partes[0].upper() in nombres:
            pids.add(int(partes[1]))
    return pids


@con_word
def test_carta_real_de_word_subrayar_y_tachar(visor, done, tmp_path):
    from ronypdf import oficina
    antes = _procesos_office("WINWORD.EXE")
    P = oficina.Parrafo
    docx = tmp_path / "Carta con tildes.docx"
    oficina.escribir_docx([
        P("Bogotá, 12 de marzo de 2025"), P("Señora Ana Pérez", negrita=True),
        P("Le escribo para confirmar el presupuesto del taller de lectura y la fecha de entrega del informe."),
        P("Quedo atento a sus comentarios sobre el cronograma propuesto."),
        P("Segunda página de la carta: anexos y firmas.", salto_pagina=True),
        P("El valor total incluye refrigerios y materiales."),
    ], docx)
    pdf = tmp_path / "carta.pdf"
    assert oficina.a_pdf(docx, pdf) == 2
    abrir(visor, pdf)
    c = visor.canvas
    for pagina, palabra, accion in ((0, "presupuesto", "Subrayar"), (0, "cronograma", "Tachar"),
                                    (1, "refrigerios", "Subrayar")):
        w = _palabras(visor, pagina, palabra)[0]
        c.select_words(pagina, w.indice, w.indice)
        next(a for a in _menu_sel(c).actions() if a.text() == accion).trigger()
        tipo = "Underline" if accion == "Subrayar" else "StrikeOut"
        pg = c.doc[pagina]
        a = next(x for x in pg.annots() if x.type[1] == tipo and
                 pymupdf.Rect(comentarios._quads_de(x)[0]).intersects(pymupdf.Rect(w.rect)))
        q = pymupdf.Rect(comentarios._quads_de(a)[0])
        r = pg.search_for(palabra)[0]
        assert abs(q.x0 - r.x0) < 2 and abs(q.x1 - r.x1) < 2 and abs(q.y0 - r.y0) < 3 and abs(q.y1 - r.y1) < 3
    for _ in range(20):
        _ev()
    marcados = sorted(cm.texto_marcado for cm in _est(visor).comentarios)
    assert marcados == ["cronograma", "presupuesto", "refrigerios"]
    # Word se cierra solo (se le dan unos segundos; otras pruebas del equipo pueden abrir Excel a la vez)
    fin = time.time() + 20
    while time.time() < fin and _procesos_office("WINWORD.EXE") - antes:
        time.sleep(0.5)
    assert not (_procesos_office("WINWORD.EXE") - antes), "quedó un Word abierto"


def test_escaneo_con_sello_digital(visor, done, tmp_path, scanned):
    src = tmp_path / "escaneo_sellado.pdf"
    with pymupdf.open(scanned) as d:
        d[0].insert_text((380, 60), "RADICADO 2025-0042 · 12/03/2025", fontsize=9, color=(0.6, 0, 0))
        d.save(src)
    abrir(visor, src)
    c = visor.canvas
    w = _palabras(visor, 0, "2025-0042")[0]
    c.select_words(0, w.indice, w.indice)
    assert len(cui.marcar_seleccion(visor, "subrayado")) == 1          # el texto del sello digital sí se marca
    cui.crear_nota(visor, 0, 100, 400, "nota sobre el escaneo", editar=False)
    cui.crear_cuadro(visor, 0, (100, 500, 300, 560), "cuadro sobre el escaneo", editar=False)
    cui.poner_sello(visor, sellos.de_fabrica("inicio-recibido"), 0, 400, 700)
    assert sorted(cm.tipo for cm in _est(visor).comentarios) == ["cuadro", "nota", "sello", "subrayado"]


@pytest.mark.skipif(not PDFCRAFT.exists(), reason="PdfCraft no está instalado")
def test_pdfcraft_lee_autor_respuestas_y_estado(tmp_path):
    src = _pdf_texto(tmp_path / "carta.pdf")
    ops = [
        _crear("nota", 0, "rpdf-n1", texto="¿Confirmamos la fecha?", punto=(400, 100)),
        {"op": "responder", "ref": {"pagina": 0, "nm": "rpdf-n1"}, "nm": "rpdf-r1", "autor": "Luis Gómez",
         "texto": "Sí, el viernes", "fecha": F2},
        {"op": "estado", "ref": {"pagina": 0, "nm": "rpdf-n1"}, "nm": "rpdf-e1", "autor": "Ana Pérez",
         "estado": "Accepted", "fecha": F3},
        _crear("subrayado", 0, "rpdf-s1", texto="ojo", rects=[_palabra(src, 0, "subrayar")]),
        _crear("sello", 0, "rpdf-st", texto="REVISADO · Ana Pérez", rect=(300, 500, 450, 560),
               png=base64.b64encode(_png_marcador()).decode()),
    ]
    with pymupdf.open(src) as d:
        comentarios.aplicar(d, ops)
        d.save(tmp_path / "para_pdfcraft.pdf")
    script = tmp_path / "pasos.json"
    script.write_text(json.dumps([{"tool": "doc_open", "args": {"path": "para_pdfcraft.pdf"}},
                                  {"tool": "comment_list", "args": {"doc": 1}}]), encoding="utf-8")
    r = subprocess.run([str(PDFCRAFT), "run", "--script", str(script), "--root", str(tmp_path), "--report"],
                       capture_output=True, text=True, encoding="utf-8", timeout=120,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    rep = json.loads(r.stdout)
    lista = rep["steps"][1]["result"][0]["comments"]
    nota = next(x for x in lista if x["type"] == "Text")
    assert nota["author"] == "Ana Pérez" and nota["contents"] == "¿Confirmamos la fecha?"
    assert nota["status"] == "Accepted"
    assert [(x["author"], x["contents"]) for x in nota["replies"]] == [("Luis Gómez", "Sí, el viernes")]
    assert any(x["type"] == "Underline" and x["author"] == "Ana Pérez" for x in lista)
    assert any(x["type"] == "Stamp" and x["contents"] == "REVISADO · Ana Pérez" for x in lista)


# ======================================================================== cierre de la ronda 2 (regresiones)

def test_fechas_con_el_desfase_horario_vigente():
    """La hora de comentarios, respuestas, estados y sellos es la de este equipo.  ``pymupdf.get_pdf_now``
    usaba el desfase del horario de verano aunque no rigiera: en Colombia escribía «-04'00'» (una hora menos)."""
    ahora = dt.datetime.now().astimezone()
    for fuente in (comentarios.ahora_pdf, lambda: core.fecha_pdf_ahora()):
        texto = fuente()
        f = comentarios.fecha_pdf(texto)
        assert f.utcoffset() == ahora.utcoffset(), texto
        assert abs((f - ahora).total_seconds()) < 60, texto
    hoy = comentarios.fecha_corta(comentarios.fecha_pdf(comentarios.ahora_pdf()))
    assert hoy == f"hoy {comentarios.hora_texto(dt.datetime.now())}" or hoy.startswith("ayer")


def _doc_con_hilos(path: Path) -> Path:
    """Tres páginas; en la 1 y en la 3, una nota con una respuesta, una respuesta a esa respuesta y un estado."""
    d = pymupdf.open(_pdf_texto(path.with_name(f"base_{path.name}")))
    for pag in (0, 2):
        nm = f"rpdf-padre{pag}"
        comentarios.aplicar(d, [
            _crear("nota", pag, nm, texto=f"Nota de la página {pag + 1}", punto=(400, 100)),
            {"op": "responder", "ref": {"pagina": pag, "nm": nm}, "nm": f"rpdf-resp{pag}", "autor": "Luis Gómez",
             "texto": "De acuerdo", "fecha": F2},
            {"op": "responder", "ref": {"pagina": pag, "nm": f"rpdf-resp{pag}"}, "nm": f"rpdf-otra{pag}",
             "autor": "Ana Pérez", "texto": "Gracias", "fecha": F3},
            {"op": "estado", "ref": {"pagina": pag, "nm": nm}, "nm": f"rpdf-estado{pag}", "autor": "Luis Gómez",
             "estado": "Accepted", "fecha": F3},
        ])
    _guardar(d, path).close()
    return path


def _hilos(path, password=None):
    with core.open_pdf(path, password) as d:
        out = []
        for cm in comentarios.leer(d):
            out.append((cm.pagina, cm.texto, cm.estado,
                        [(r.autor, r.texto) for r in cm.respuestas if not r.es_estado],
                        len(list(d[cm.pagina].annots()))))
        return sorted(out)


HILO = ("Accepted", [("Luis Gómez", "De acuerdo"), ("Ana Pérez", "Gracias")])


def test_organizar_unir_y_extraer_conservan_respuestas_y_estados(tmp_path):
    """insert_pdf (PyMuPDF) deja por fuera las anotaciones con /IRT: «Organizar páginas → Guardar», «Unir» y
    «Dividir / Extraer» borraban en silencio las respuestas y los estados."""
    from ronypdf import guardado
    src = _doc_con_hilos(tmp_path / "hilos.pdf")
    assert _hilos(src) == [(0, "Nota de la página 1", *HILO, 4), (2, "Nota de la página 3", *HILO, 4)]
    # organizar (un solo origen, el «Guardar» directo): reordenar, girar, duplicar, quitar y una en blanco
    org = tmp_path / "org.pdf"
    guardado.armar_pdf([(str(src), None)], [core.PageRef(0, 2), core.PageRef(None), core.PageRef(0, 0, 90),
                                            core.PageRef(0, 1)], org)
    assert _hilos(org) == [(0, "Nota de la página 3", *HILO, 4), (2, "Nota de la página 1", *HILO, 4)]
    with pymupdf.open(org) as d:
        assert d[2].rotation == 90 and d.page_count == 4
        for p in (d[0], d[2]):                  # cada respuesta apunta a la copia de su comentario, en su página
            xrefs = {a.xref for a in p.annots()}
            assert all(a.irt_xref in xrefs for a in p.annots() if a.irt_xref)
    # varios orígenes, unir, extraer y dividir
    otro = _pdf_texto(tmp_path / "otro.pdf", paginas=1)
    varios = tmp_path / "varios.pdf"
    core.build_document([(str(otro), None), (str(src), None)], [core.PageRef(0, 0), core.PageRef(1, 0)], varios)
    assert _hilos(varios) == [(1, "Nota de la página 1", *HILO, 4)]
    unido = tmp_path / "unido.pdf"
    core.merge([core.MergeItem(str(src)), core.MergeItem(str(src), pages="3")], unido)
    assert [h[:4] for h in _hilos(unido)] == [(0, "Nota de la página 1", *HILO), (2, "Nota de la página 3", *HILO),
                                             (3, "Nota de la página 3", *HILO)]
    ext = tmp_path / "ext.pdf"
    core.extract_pages(str(src), "3", ext)
    assert _hilos(ext) == [(0, "Nota de la página 3", *HILO, 4)]
    partes = core.split_every(str(src), 1, tmp_path / "partes")
    assert [len(_hilos(p)) for p in partes] == [1, 0, 1] and _hilos(partes[2])[0][2:4] == HILO


def test_resaltado_del_visor_es_un_comentario_con_autor_y_fecha(tmp_path):
    """El «Resaltar» del visor guardaba un Highlight sin autor ni fecha: en el panel salía sin nombre y
    «Solo los míos» lo dejaba por fuera."""
    src = _pdf_texto(tmp_path / "a.pdf")
    out = tmp_path / "b.pdf"
    r = _palabra(src, 0, "Carta")
    core.apply_edits(src, out, [core.Edit("highlight", 0, rects=[r], color=(1, 0.9, 0.2), autor="Ana Pérez",
                                          fecha=F1, nm="rpdf-hl1")])
    with pymupdf.open(out) as d:
        cm, = comentarios.leer(d, marcado=True)
    assert (cm.tipo, cm.autor, cm.nm, cm.texto_marcado) == ("resaltado", "Ana Pérez", "rpdf-hl1", "Carta")
    assert cm.creado == comentarios.fecha_pdf(F1) and Filtro(solo_de="Ana Pérez").acepta(cm)
    core.apply_edits(src, out, [core.Edit("highlight", 0, rects=[r], color=(1, 0.9, 0.2))])   # sin fecha: ahora
    with pymupdf.open(out) as d:
        cm, = comentarios.leer(d)
    assert abs((cm.creado - dt.datetime.now().astimezone()).total_seconds()) < 60


def _pdf_denso(path: Path, paginas: int) -> Path:
    with pymupdf.open() as d:
        for i in range(paginas):
            p = d.new_page(width=612, height=792)
            p.insert_text((72, 90), f"Capítulo {i + 1}", fontsize=16)
            for k in range(28):
                p.insert_text((72, 130 + k * 20), f"Renglón {k + 1} de la página {i + 1}: el presupuesto del "
                                                  f"proyecto se revisa cada trimestre ({i * 31 + k}).", fontsize=10)
        d.save(path)
    return path


def test_resumen_pdf_con_miniaturas_livianas(tmp_path, samples):
    """Cada miniatura iba sin pérdida a 2,2×: el resumen de un documento de 2,4 MB pesaba 17,5 MB."""
    src = _pdf_denso(tmp_path / "denso.pdf", 10)
    with pymupdf.open(src) as d:
        comentarios.aplicar(d, [_crear("nota", i, f"rpdf-n{i}", texto=f"Revisar {i}", punto=(400, 60))
                                for i in range(10)])
        d.save(tmp_path / "comentado.pdf")
    out = tmp_path / "resumen.pdf"
    assert comentarios.exportar_resumen(tmp_path / "comentado.pdf", out, "pdf") == 10
    assert out.stat().st_size < 10 * 30_000, out.stat().st_size          # antes, unos 55 KB por miniatura
    with pymupdf.open(out) as r:
        assert sum(len(p.get_images()) for p in r) == 10
    # con fotos (o escaneos) la miniatura va en JPEG
    with pymupdf.open(samples["a"]) as d:
        comentarios.aplicar(d, [_crear("nota", 0, "rpdf-f", texto="Foto", punto=(400, 60))])
        d.save(tmp_path / "foto.pdf")
    comentarios.exportar_resumen(tmp_path / "foto.pdf", tmp_path / "resumen_foto.pdf", "pdf")
    with pymupdf.open(tmp_path / "resumen_foto.pdf") as r:
        assert [r.extract_image(x[0])["ext"] for p in r for x in p.get_images()] == ["jpeg"]


def test_sello_con_fondo_blanco_se_lee_sobre_el_texto():
    """Los sellos iban transparentes por dentro: sobre un párrafo, sus renglones pequeños no se leían."""
    s = sellos.Sello(id="s-prueba", nombre="Visto bueno", lineas=["VISTO BUENO", "{nombre}"], forma="rectangulo")
    assert s.fondo_blanco                                       # activo por defecto
    e = 4.0
    grosor = 2 * e
    x = int(grosor + 2 * e + 2 * grosor + 4)                   # dentro del borde, antes del texto
    img = QImage.fromData(sellos.dibujar(s, {"nombre": "Ana Pérez"}, e))
    c = img.pixelColor(x, img.height() // 2)
    assert c.alpha() >= 220 and min(c.red(), c.green(), c.blue()) >= 245
    assert img.pixelColor(1, 1).alpha() == 0                   # por fuera del contorno sigue transparente
    sin = QImage.fromData(sellos.dibujar(s.copia(fondo_blanco=False), {"nombre": "Ana Pérez"}, e))
    assert sin.pixelColor(x, img.height() // 2).alpha() == 0
    sin = s.copia(fondo_blanco=False)
    d = sellos.a_dict(sin)
    assert d["fondo_blanco"] is False and not sellos.desde_dict(d).fondo_blanco
    d.pop("fondo_blanco")                                      # un diseño guardado antes de esta opción
    assert sellos.desde_dict(d).fondo_blanco
    # puesto en la página tapa los renglones que quedan debajo
    with pymupdf.open() as doc:
        p = doc.new_page(width=612, height=792)
        for k in range(12):
            p.insert_text((40, 100 + k * 9), "Texto del párrafo de la carta " * 4, fontsize=8)
        png = sellos.dibujar(s, {"nombre": "Ana Pérez"}, e)
        w, h = sellos.tamano_pt(png, e)
        comentarios.aplicar(doc, [_crear("sello", 0, "rpdf-st", rect=(60, 95, 60 + w, 95 + h),
                                         png=base64.b64encode(png).decode())])
        borde = p.get_pixmap(dpi=144, clip=pymupdf.Rect(60 + 7, 95 + h / 2 - 3, 60 + 11, 95 + h / 2 + 3))
        assert min(min(borde.pixel(i, j)) for i in range(borde.width) for j in range(borde.height)) > 200


def test_disenador_de_sellos_tiene_fondo_blanco(win, done):
    d = cui.DialogoSello(sellos.de_fabrica("inicio-aprobado"))
    assert d.k_blanco.isChecked() and d.sello().fondo_blanco
    d.k_blanco.setChecked(False)
    assert not d.sello().fondo_blanco
    d.deleteLater()


def test_resaltar_con_el_visor_lleva_mi_nombre(visor, done, monkeypatch, tmp_path):
    from PySide6.QtCore import QRectF
    src = _pdf_texto(tmp_path / "a.pdf")
    abrir(visor, src)
    c = visor.canvas
    p0 = c.pages[0]
    c.add_highlight(p0, QRectF(p0.pos() + QPointF(70, 75), p0.pos() + QPointF(260, 95)))
    out = tmp_path / "resaltado.pdf"
    monkeypatch.setattr(viewer, "ask_save", lambda *a, **k: str(out))
    visor.save()
    wait()
    with pymupdf.open(out) as d:
        cm, = comentarios.leer(d)
    assert cm.tipo == "resaltado" and cm.autor == "Ana Pérez" and cm.nm.startswith("rpdf-")
    assert abs((cm.creado - dt.datetime.now().astimezone()).total_seconds()) < 120
    hl = next(x for x in _est(visor).comentarios if x.tipo == "resaltado")       # en el panel, con su nombre
    assert hl.autor == "Ana Pérez"
    # la primera vez (sin nombre todavía) se pregunta al guardar; si se cancela, se guarda igual
    widgets.ctx.settings.data.pop("comentarios_autor", None)
    preguntas = []

    def aceptar(self):
        preguntas.append(1)
        self.campo.setText("Luis Gómez")
        self.guardar()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(cui.DialogoAutor, "exec", aceptar)
    abrir(visor, src)
    c.add_highlight(c.pages[1], QRectF(c.pages[1].pos() + QPointF(70, 75), c.pages[1].pos() + QPointF(260, 95)))
    out2 = tmp_path / "resaltado2.pdf"
    monkeypatch.setattr(viewer, "ask_save", lambda *a, **k: str(out2))
    visor.save()
    wait()
    with pymupdf.open(out2) as d:
        assert preguntas == [1] and [x.autor for x in comentarios.leer(d)] == ["Luis Gómez"]
    widgets.ctx.settings.set("comentarios_autor", "Ana Pérez")


def _contiene(data: bytes, texto: str) -> bool:
    """¿El texto está en algún lugar del archivo, también en los flujos comprimidos y en versiones anteriores?"""
    import re
    import zlib
    partes = [data]
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", data, re.S):
        try:
            partes.append(zlib.decompress(m.group(1)))
        except zlib.error:
            continue
    crudo = b"\n".join(partes)
    b = texto.encode("latin-1")
    return b in crudo or b.hex().encode() in crudo.lower()


def test_borrar_o_editar_un_comentario_y_guardar_no_deja_el_texto_viejo(visor, done, monkeypatch, tmp_path):
    """Borrar o editar un comentario y pulsar «Guardar» lo dejaba en una versión anterior dentro del archivo
    (guardado incremental)."""
    from ronypdf import guardado, guardado_ui
    src = _pdf_texto(tmp_path / "a.pdf")
    with pymupdf.open(src) as d:
        comentarios.aplicar(d, [_crear("nota", 0, "rpdf-x1", texto="SALARIO-SECRETO-98765", punto=(400, 100)),
                                _crear("nota", 1, "rpdf-x2", texto="CEDULA-1032456789", punto=(400, 100))])
        d.save(tmp_path / "b.pdf")
    src = tmp_path / "b.pdf"
    monkeypatch.setattr(guardado_ui, "confirmar_mismo_archivo", lambda *a, **k: ("guardar", False))
    abrir(visor, src)
    cm = next(x for x in _est(visor).comentarios if x.texto == "SALARIO-SECRETO-98765")
    assert cui.borrar(visor, cm)
    visor.guardar()
    wait()
    data = src.read_bytes()
    assert not visor.has_unsaved() and not _contiene(data, "SALARIO-SECRETO-98765") and data.count(b"%%EOF") == 1
    # editar el texto de uno que ya estaba en el archivo
    cm = next(x for x in _est(visor).comentarios if x.texto == "CEDULA-1032456789")
    assert cui.editar_texto(visor, cm, "Cédula verificada")
    visor.guardar()
    wait()
    data = src.read_bytes()
    assert not _contiene(data, "CEDULA-1032456789") and _contiene(data, "verificada")
    # un comentario nuevo (que todavía no estaba en el archivo) sí se guarda de forma incremental
    cui.crear_nota(visor, 2, 300, 300, "Nueva", editar=False)
    assert not cui.guardar_visor(visor).borra
    assert cui.quita_contenido([{"op": "borrar", "ref": {"pagina": 0, "xref": 12}}])
    assert not cui.quita_contenido([{"op": "editar", "ref": {"pagina": 0, "nm": "rpdf-1"}, "texto": "x"}])
    visor.guardar()
    wait()
    assert src.read_bytes().count(b"%%EOF") == 2
    assert guardado.respaldos_de(src, guardado_ui.carpetas_respaldos())


def test_autoguardado_no_cierra_la_nota_que_se_esta_escribiendo(visor, done, win, monkeypatch, tmp_path):
    """El autoguardado cerraba el recuadro de la nota en mitad de una frase: las teclas siguientes iban al lienzo
    (N, U, K, X cambiaban de herramienta y Retroceso borraba la nota)."""
    from PySide6.QtTest import QTest
    from ronypdf import guardado
    g = win._autoguardado
    g.timer.stop()
    abrir(visor, _pdf_texto(tmp_path / "a.pdf"))
    cui.crear_nota(visor, 0, 150, 200, "", editar=True)
    est = _est(visor)
    ed = est.editor
    # el foco dentro de la ventana (QApplication.focusWidget depende de que la ventana esté activa)
    assert ed is not None and win.focusWidget() is ed.texto
    _ev(4)                                       # el autoguardado vigila también el recuadro (se agregó al lienzo)
    QTest.keyClicks(ed.texto, "Revisar la fecha")
    assert time.monotonic() - g.actividad < 2    # escribir en el recuadro cuenta como actividad
    g.revisar()                                  # el tic del temporizador: espera a que se deje de escribir
    assert g.tarea is None and est.editor is ed
    g.ahora()                                    # sin esperar: autoguarda ya
    wait()
    for _ in range(100):
        if g.tarea is None:
            break
        _ev()
        time.sleep(0.02)
    assert est.editor is ed and win.focusWidget() is ed.texto and visor.canvas.tool == "select"
    m = guardado._leer_json(g.carpeta / f"{g.sesion['id']}__viewer.json")
    with pymupdf.open(g.carpeta / m["pdf"]) as d:
        assert [a.info["content"] for a in d[0].annots()] == ["Revisar la fecha"]
    # se sigue escribiendo: las teclas van al recuadro, no al lienzo
    QTest.keyClicks(ed.texto, " unxk")
    QTest.keyClick(ed.texto, Qt.Key.Key_Backspace)
    assert ed.texto.toPlainText() == "Revisar la fecha unx" and visor.canvas.tool == "select"
    assert len(est.comentarios) == 1
    # lo nuevo también se autoguarda (lo escrito cambia lo que hay que guardar)
    g.ahora()
    for _ in range(200):
        if g.tarea is None:
            break
        _ev()
        time.sleep(0.02)
    m = guardado._leer_json(g.carpeta / f"{g.sesion['id']}__viewer.json")
    with pymupdf.open(g.carpeta / m["pdf"]) as d:
        assert [a.info["content"] for a in d[0].annots()] == ["Revisar la fecha unx"]
    assert est.editor is ed
    ed.terminar()
    assert est.ops[0]["texto"] == "Revisar la fecha unx"
    g.limpiar("viewer")


def test_comentar_y_sello_conservan_su_texto_a_1240(visor, done, win, tmp_path):
    """A 1240 × 820 con el menú lateral abierto, «Comentar» y «Sello» quedaban solo con el ícono mientras
    «Resaltar» y «Dibujar» conservaban su texto: ahora son los últimos en perderlo."""
    abrir(visor, _pdf_texto(tmp_path / "a.pdf"))
    b_com, b_sello = visor.ext["comentarios_botones"]
    texto = Qt.ToolButtonStyle.ToolButtonTextBesideIcon
    try:
        for ancho, alto in ((1240, 820), (1000, 660)):
            win.resize(ancho, alto)
            win.sidebar.set_collapsed(False, remember=False)
            _ev(8)
            assert visor.toolbar.minimumSizeHint().width() <= visor.width()
            propios = [visor.tool_buttons[k] for k in ("select", "text", "edittext", "highlight", "ink", "redact")]
            propios += [visor.shape_btn, visor.b_image]
            if ancho == 1240:
                assert b_com.toolButtonStyle() == texto and b_sello.toolButtonStyle() == texto
            # mientras alguno de los botones propios conserve su texto, los de comentarios también
            if any(b.toolButtonStyle() == texto for b in propios):
                assert b_com.toolButtonStyle() == texto and b_sello.toolButtonStyle() == texto
    finally:
        win.resize(1240, 820)
        win.sidebar.set_collapsed(True, remember=False)
        _ev(4)
