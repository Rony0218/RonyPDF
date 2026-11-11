"""Pruebas de la integración de la ronda 2: lo que une un paquete con otro.

* «Acciones por lotes» reconoce el texto con el motor de ocr.py (enderezar, girar y limpiar).
* «Acciones por lotes» pone los sellos de «Mis sellos» (con el nombre de quien usa la aplicación) y deja fijos los
  comentarios.
* Un comentario hecho en el visor se deshace y se rehace con el «Rehacer» del guardado seguro, se guarda con
  «Guardar» directo y, al recargar, no se duplica.

Los nombres son genéricos (Ana Pérez): nada institucional.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import time
from pathlib import Path

import pymupdf
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("RONYPDF_SETTINGS", os.path.join(tempfile.mkdtemp(prefix="ronypdf-int-"), "RonyPDF.json"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from ronypdf import comentarios, core, extensiones, lotes, widgets  # noqa: E402
from ronypdf.settings import Settings  # noqa: E402

_app = QApplication.instance() or QApplication([])


def _hash(ruta) -> str:
    return hashlib.sha256(Path(ruta).read_bytes()).hexdigest()


@pytest.fixture(scope="module", autouse=True)
def _pasos_de_otros_paquetes():
    lotes.pasos_externos(extensiones.modulos())     # lo mismo que hace la pantalla «Acciones por lotes»


@pytest.fixture
def ajustes(tmp_path, monkeypatch):
    """Ajustes y «Mis sellos» de esta prueba, aparte (nunca los reales)."""
    s = Settings(tmp_path / "ajustes" / "RonyPDF.json")
    s.set("comentarios_autor", "Ana Pérez")
    s.set("comentarios_cargo", "Coordinadora")
    monkeypatch.setattr(widgets.ctx, "settings", s)
    return s


def _escaneo_torcido(ruta: Path, angulo: float = 4.0) -> Path:
    """Una hoja escaneada torcida: solo una imagen, con los renglones inclinados."""
    with pymupdf.open() as src:
        p = src.new_page(width=595, height=842)
        y = 130
        for linea in ("Informe de actividades del trimestre", "Las jornadas de educación avanzaron",
                      "según lo previsto en el plan anual"):
            p.insert_text((72, y), linea, fontsize=20)
            y += 42
        pix = p.get_pixmap(matrix=pymupdf.Matrix(200 / 72, 200 / 72).prerotate(angulo), alpha=False)
    with pymupdf.open() as out:
        q = out.new_page(width=595, height=842)
        q.insert_image(q.rect, pixmap=pix)
        out.save(ruta)
    return ruta


def _pdf_texto(ruta: Path, paginas: int = 2, girar_segunda: bool = False) -> Path:
    with pymupdf.open() as d:
        for i in range(paginas):
            p = d.new_page(width=595, height=842)
            p.insert_text((72, 100), f"Carta de prueba, página {i + 1}", fontsize=16)
        if girar_segunda and paginas > 1:
            d[1].set_rotation(90)
        d.save(ruta)
    return ruta


# ======================================================================== lotes + OCR

def test_lotes_ocr_usa_el_motor_nuevo_y_endereza(tmp_path):
    paso = lotes.PASOS["ocr"]
    assert {"enderezar", "girar", "limpiar"} <= set(paso.defecto())
    # un flujo guardado antes (solo idioma y calidad) sigue sirviendo: lo demás queda por defecto
    viejo = paso.completar({"idioma": "spa+eng", "calidad": 300})
    assert viejo["enderezar"] and viejo["girar"] and viejo["limpiar"] == "auto"
    assert "ppp" in paso.texto_resumen(viejo)

    origen = _escaneo_torcido(tmp_path / "escaneo torcido.pdf")
    antes = _hash(origen)
    inf = lotes.ejecutar(lotes.planificar(lotes.Flujo("x", [("ocr", {"idioma": "spa+eng", "calidad": 300})]),
                                          [str(origen)]))
    r = inf.archivos[0]
    assert r.estado == "hecho", r.detalle
    detalle = r.pasos[0][1]
    assert "1 página reconocida" in detalle and "enderezada" in detalle
    with pymupdf.open(r.salida) as d:
        texto = d[0].get_text().lower()
    assert "educación" in texto and "trimestre" in texto
    assert _hash(origen) == antes

    # sin enderezar ni girar, el resumen lo dice
    assert "sin enderezar ni girar" in paso.texto_resumen({"enderezar": False, "girar": False})


# ======================================================================== lotes + «Mis sellos»

def _preparados(flujo: lotes.Flujo) -> dict:
    out = {}
    for k, (clave, valores) in enumerate(flujo.pasos):
        paso = lotes.PASOS[clave]
        if paso.preparar is not None:
            out[k] = paso.preparar(paso.completar(valores))
    return out


def test_lotes_pone_mi_sello_en_todas_las_paginas_y_lo_deja_fijo(tmp_path, ajustes):
    assert "sello" in lotes.PASOS and "aplanar" in lotes.PASOS
    paso = lotes.PASOS["sello"]
    nombres = [t for _k, t in paso.opciones[0].elegibles()]
    assert "Recibido (para empezar)" in nombres
    origen = _pdf_texto(tmp_path / "carta con tildes ñ.pdf", paginas=2, girar_segunda=True)
    antes = _hash(origen)
    flujo = lotes.Flujo("x", [("sello", {"sello": "inicio-recibido", "paginas": "todas",
                                         "posicion": "arriba-derecha"})])
    assert "arriba a la derecha" in paso.texto_resumen(flujo.pasos[0][1])
    inf = lotes.ejecutar(lotes.planificar(flujo, [str(origen)]), preparados=_preparados(flujo))
    r = inf.archivos[0]
    assert r.estado == "hecho", r.detalle
    assert r.pasos == [("Poner un sello", "«Recibido» en 2 páginas")]
    assert _hash(origen) == antes
    with pymupdf.open(r.salida) as d:
        for page in d:
            annots = list(page.annots())
            assert len(annots) == 1 and annots[0].type[1] == "Stamp"
            info = annots[0].info
            assert info["title"] == "Ana Pérez" and "RECIBIDO" in info["content"] and "Ana Pérez" in info["content"]
            visible = annots[0].rect * page.rotation_matrix          # el rectángulo tal como se ve la página
            visible.normalize()
            assert abs(visible.x1 - (page.rect.width - comentarios.MARGEN_SELLO)) < 2
            assert abs(visible.y0 - comentarios.MARGEN_SELLO) < 2
        leidos = comentarios.leer(d)
        assert [(c.tipo, c.autor) for c in leidos] == [("sello", "Ana Pérez"), ("sello", "Ana Pérez")]

    # «Dejar fijos los comentarios y sellos»: el sello pasa a ser parte de la página
    inf2 = lotes.ejecutar(lotes.planificar(lotes.Flujo("y", [("aplanar", {})]), [r.salida]))
    r2 = inf2.archivos[0]
    assert r2.estado == "hecho" and r2.pasos[0][1].startswith("2 comentarios")
    with pymupdf.open(r2.salida) as d:
        assert all(not list(p.annots()) for p in d)
        assert all(p.get_images() or p.get_xobjects() for p in d)
    # un PDF sin comentarios queda igual, sin error
    inf3 = lotes.ejecutar(lotes.planificar(lotes.Flujo("z", [("aplanar", {})]), [str(origen)]))
    assert inf3.archivos[0].pasos[0][1] == "no tenía comentarios"


def test_lotes_sello_con_contrasena_y_sello_propio(tmp_path, ajustes, protegido):
    from ronypdf import sellos
    propio = sellos.AlmacenSellos().guardar(sellos.Sello(id=sellos.nuevo_id(), nombre="Visto bueno",
                                                         lineas=["VISTO BUENO", "{nombre} · {cargo}", "{fecha}"],
                                                         color="#1F7A4D"))
    paso = lotes.PASOS["sello"]
    assert ("Visto bueno" in [t for _k, t in paso.opciones[0].elegibles()])
    origen = tmp_path / "protegido.pdf"
    origen.write_bytes(Path(protegido.path).read_bytes())
    flujo = lotes.Flujo("x", [("sello", {"sello": propio.id, "paginas": "ultima", "posicion": "abajo-izquierda",
                                         "tamano": "pequeno"})])
    plan = lotes.planificar(flujo, [str(origen)])
    inf = lotes.ejecutar(plan, contrasenas={str(origen): protegido.password}, preparados=_preparados(flujo))
    r = inf.archivos[0]
    assert r.estado == "hecho", r.detalle
    protegido.comprobar(r.salida, sin_tocar=(0, 1, 2))
    with core.open_pdf(r.salida, protegido.password) as d:
        assert not list(d[0].annots()) and not list(d[1].annots())
        ultima = d[2]                          # la anotación necesita su página viva
        a = list(ultima.annots())[0]
        assert "VISTO BUENO" in a.info["content"] and "Coordinadora" in a.info["content"]
        assert a.rect.x0 < 40 and a.rect.y1 > ultima.rect.height - 40

    # si el sello ya no existe, el paso lo dice antes de ejecutar
    sellos.AlmacenSellos().borrar(propio.id)
    assert paso.problema(flujo.pasos[0][1]) == "elige uno de tus sellos"
    with pytest.raises(core.UserError):
        paso.preparar(paso.completar(flujo.pasos[0][1]))


def test_pantalla_de_lotes_ofrece_los_pasos_nuevos(ajustes):
    from ronypdf import lotes_ui
    page = lotes_ui.LotesPage()
    try:
        page._llenar_menu()
        textos = [a.text() for a in page.menu_pasos.actions()]
        assert "Poner un sello" in textos and "Dejar fijos los comentarios y sellos" in textos
        assert textos.index("Reconocer texto (OCR)") < textos.index("Poner un sello")
    finally:
        page.deleteLater()


# ======================================================================== visor: comentarios + guardado seguro

@pytest.fixture(scope="module")
def win(tmp_path_factory):
    from ronypdf.gui import MainWindow, _light_palette
    from ronypdf.style import QSS
    _app.setStyle("Fusion")
    _app.setPalette(_light_palette())
    _app.setStyleSheet(QSS)
    anterior = widgets.ctx.settings
    widgets.ctx.settings = Settings(tmp_path_factory.mktemp("ajustes_integracion") / "RonyPDF.json")
    w = MainWindow()
    w.resize(1240, 820)
    w.show()
    _app.processEvents()
    w._autoguardado.timer.stop()
    yield w
    w._autoguardado.timer.stop()
    w.hide()
    widgets.ctx.settings = anterior
    widgets.ctx.window = None


def _esperar(timeout=60):
    fin = time.time() + timeout
    while time.time() < fin:
        _app.processEvents()
        if not widgets.ctx.busy and not getattr(widgets.ctx, "_silenciosas", None):
            _app.processEvents()
            return
        time.sleep(0.01)
    raise TimeoutError


def test_comentario_rehacer_y_guardar_directo_sin_duplicar(win, tmp_path, monkeypatch):
    from ronypdf import comentarios_ui as cui
    from ronypdf import guardado, guardado_ui, viewer
    widgets.ctx.settings.set("comentarios_autor", "Ana Pérez")
    monkeypatch.setattr(guardado_ui, "confirmar_mismo_archivo", lambda *a, **k: ("guardar", False))
    monkeypatch.setattr(guardado_ui, "preguntar_cambios", lambda *a, **k: "descartar")
    hechos = []
    for mod in (viewer, widgets, guardado_ui):
        monkeypatch.setattr(mod, "show_done", lambda *a, **k: hechos.append(a), raising=False)
    guardado_ui._confirmados.clear()

    src = _pdf_texto(tmp_path / "acta.pdf", paginas=3)
    original = src.read_bytes()
    v = win.pages["viewer"]
    win.navigate("viewer")
    assert v.load(str(src), confirm=False)
    _app.processEvents()
    c = v.canvas
    est = cui._est(v)

    cui.crear_nota(v, 0, 120, 160, "Revisar la fecha", editar=False)
    assert c.has_changes() and len(est.comentarios) == 1
    c.undo()
    _app.processEvents()
    assert not c.has_changes() and not est.comentarios
    c.redo()                                   # el «Rehacer» del visor también rehace lo de las extensiones
    _app.processEvents()
    assert c.has_changes() and [x.texto for x in est.comentarios] == ["Revisar la fecha"]

    v.guardar()                                # «Guardar» directo: copia de respaldo e incremental
    _esperar()
    assert not hechos and not c.has_changes() and c.path == str(src)
    assert src.read_bytes().startswith(original)
    assert len(guardado.respaldos_de(src, guardado_ui.carpetas_respaldos())) == 1
    with pymupdf.open(src) as d:
        primera = d[0]                         # la anotación necesita su página viva
        notas = [a for a in primera.annots() if a.type[1] == "Text"]
        assert len(notas) == 1 and notas[0].info["title"] == "Ana Pérez"
    est = cui._est(v)                          # al recargar, la extensión arma su estado desde el archivo
    assert [x.texto for x in est.comentarios] == ["Revisar la fecha"] and not est.ops      # una sola vez

    # otro comentario y otro «Guardar»: el primero no se duplica
    cui.crear_nota(v, 1, 200, 300, "Falta la firma", editar=False)
    v.guardar()
    _esperar()
    with pymupdf.open(src) as d:
        assert len([a for p in d for a in p.annots() if a.type[1] == "Text"]) == 2
    est = cui._est(v)
    assert sorted(x.texto for x in est.comentarios) == ["Falta la firma", "Revisar la fecha"] and not est.ops
    assert not c.has_changes()
