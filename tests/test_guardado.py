"""Guardado seguro: escritura atómica, «Guardar» directo con copia de respaldo, guardado incremental, rehacer,
autoguardado, recuperación tras un cierre inesperado y la página «Respaldo y recuperación»."""

import datetime as dt
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zlib
from pathlib import Path

import pymupdf
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("RONYPDF_SETTINGS", os.path.join(tempfile.mkdtemp(prefix="ronypdf-guardado-"), "RonyPDF.json"))

from PySide6.QtCore import QPointF, QRectF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ronypdf import core, formularios, guardado, widgets  # noqa: E402

RAIZ = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- utilidades

def _pdf(path: Path, paginas: int = 3, texto: str = "Página", password: str | None = None, adjunto: bool = False,
         toc: bool = False) -> Path:
    with pymupdf.open() as d:
        for i in range(paginas):
            p = d.new_page(width=595, height=842)
            p.insert_text((72, 100), f"{texto} {i + 1} con datos SECRETO{i}", fontsize=14)
            p.insert_text((72, 140), "Segundo renglón de la página", fontsize=11)
        if adjunto:
            d.embfile_add("lista.txt", b"adjunto de prueba", filename="lista.txt")
        if toc:
            d.set_toc([[1, f"Capítulo {i + 1}", i + 1] for i in range(paginas)])
        if password:
            d.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=password, owner_pw="dueño")
        else:
            d.save(path)
    return path


def _flujos(data: bytes) -> bytes:
    """Todo el contenido del archivo con los flujos descomprimidos (también los de versiones anteriores)."""
    out = [data]
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", data, re.S):
        try:
            out.append(zlib.decompress(m.group(1)))
        except zlib.error:
            continue
    return b"\n".join(out)


def _contiene(data: bytes, texto: str) -> bool:
    crudo = _flujos(data)
    b = texto.encode("latin-1")
    return b in crudo or b.hex().encode() in crudo.lower()


def _sin_temporales(carpeta: Path) -> bool:
    return not list(carpeta.glob(".~*.tmp"))


def _texto(path, password=None) -> str:
    with core.open_pdf(path, password) as d:
        return "\n".join(p.get_text() for p in d)


# ================================================================ núcleo (sin interfaz)

@pytest.mark.parametrize("existe", [True, False])
def test_escritura_que_falla_a_mitad_no_toca_el_destino(tmp_path, monkeypatch, existe):
    src = _pdf(tmp_path / "origen.pdf")
    destino = tmp_path / "destino.pdf"
    if existe:
        destino.write_bytes(b"version anterior")
    original = pymupdf.Document.save

    def save_roto(self, filename, *a, **k):
        original(self, filename, *a, **k)            # escribe el temporal entero…
        raise RuntimeError("se fue la luz")          # …y falla antes de terminar

    monkeypatch.setattr(pymupdf.Document, "save", save_roto)
    e = core.Edit("text", 0, lines=[(72, 300, "nuevo")])
    with pytest.raises(RuntimeError):
        core.apply_edits(src, destino, [e])
    assert destino.read_bytes() == b"version anterior" if existe else not destino.exists()
    assert _sin_temporales(tmp_path)
    # lo mismo con el guardado incremental y con formularios
    shutil.copyfile(src, tmp_path / "otro.pdf")
    antes = (tmp_path / "otro.pdf").read_bytes()
    with pytest.raises(RuntimeError):
        core.apply_edits(tmp_path / "otro.pdf", tmp_path / "otro.pdf", [e], incremental=True)
    assert (tmp_path / "otro.pdf").read_bytes() == antes and _sin_temporales(tmp_path)


def test_output_vacia_al_disco_y_avisa_si_esta_abierto(tmp_path, monkeypatch):
    llamadas = []
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (llamadas.append(fd), real(fd))[1])
    with core._output(tmp_path / "a.txt") as t:
        Path(t).write_text("hola")
    assert llamadas and (tmp_path / "a.txt").read_text() == "hola"

    def replace_bloqueado(a, b):
        raise PermissionError(13, "en uso")

    monkeypatch.setattr(os, "replace", replace_bloqueado)
    with pytest.raises(core.UserError, match="¿Está abierto en otro programa?"):
        with core._output(tmp_path / "a.txt") as t:
            Path(t).write_text("otra")
    assert (tmp_path / "a.txt").read_text() == "hola" and _sin_temporales(tmp_path)


def test_output_disco_lleno(tmp_path, monkeypatch):
    def lleno(fd):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "fsync", lleno)
    with pytest.raises(core.UserError, match="espacio"):
        with core._output(tmp_path / "b.pdf") as t:
            Path(t).write_bytes(b"x")
    assert not (tmp_path / "b.pdf").exists() and _sin_temporales(tmp_path)


def test_respaldar_nombre_subcarpeta_y_retencion(tmp_path):
    src = _pdf(tmp_path / "Informe ñandú.pdf")
    carpeta = tmp_path / "RonyPDF_respaldos"
    t0 = dt.datetime(2026, 11, 2, 9, 30, 0)                         # lunes
    copia = guardado.respaldar(src, carpeta, ahora=t0)
    assert copia.name == "Informe ñandú (2026-11-02 09.30.00).pdf"
    assert copia.parent == carpeta / guardado.huella(src) and copia.read_bytes() == src.read_bytes()
    assert guardado.respaldos_de(src, [carpeta])[0].documento == "Informe ñandú.pdf"
    # cantidad: 5 por documento
    for k in range(1, 8):
        guardado.respaldar(src, carpeta, ahora=t0 + dt.timedelta(minutes=k))
    copias = guardado.respaldos_de(src, [carpeta])
    assert len(copias) == 5 and copias[0].fecha == t0 + dt.timedelta(minutes=7)
    # edad: más de 30 días se borra
    otro = _pdf(tmp_path / "viejo.pdf")
    guardado.respaldar(otro, carpeta, ahora=t0 - dt.timedelta(days=40))
    assert len(guardado.respaldos_de(otro, [carpeta])) == 1          # la recién hecha nunca se borra
    guardado.limpiar_respaldos(carpeta, guardado.Retencion(), ahora=t0)
    assert guardado.respaldos_de(otro, [carpeta]) == []
    assert not (carpeta / guardado.huella(otro)).exists()            # carpeta vacía: se quita
    # tamaño: con 1 MB en total, las más viejas se van primero
    grande = tmp_path / "grande.pdf"
    with pymupdf.open() as d:
        p = d.new_page()
        p.insert_image(p.rect, stream=os.urandom(1) * 0 + _imagen_ruido(), keep_proportion=False)
        d.save(grande)
    r = guardado.Retencion(copias=5, dias=30, megas=1)
    for k in range(4):
        guardado.respaldar(grande, carpeta, r, ahora=t0 + dt.timedelta(hours=k + 1))
    total = sum(x.tamano for x in guardado.listar_respaldos([carpeta]))
    assert total <= 1024 * 1024 or len(guardado.respaldos_de(grande, [carpeta])) == 1


def _imagen_ruido() -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 500, 400), False)
    pix.set_rect(pix.irect, (200, 100, 50))
    data = bytearray(pix.samples)
    data[:] = os.urandom(len(data))
    pix = pymupdf.Pixmap(pymupdf.csRGB, 500, 400, bytes(data), False)
    return pix.tobytes("png")


def test_respaldo_fallido_impide_guardar_encima(tmp_path, monkeypatch):
    src = _pdf(tmp_path / "doc.pdf")
    antes = src.read_bytes()

    def lleno(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(guardado.shutil, "copyfile", lleno)
    e = core.Edit("text", 0, lines=[(72, 300, "no debe entrar")])
    res = guardado.guardar_con_respaldo(core.apply_edits, True, tmp_path / "resp", path=str(src), output=str(src),
                                        edits=[e])
    assert res.error_respaldo and "disco" in res.error_respaldo.lower()
    assert src.read_bytes() == antes and _sin_temporales(tmp_path)


def test_incremental_conserva_los_bytes_originales(tmp_path):
    src = _pdf(tmp_path / "doc.pdf", toc=True)
    original = src.read_bytes()

    def nota(doc):                                    # lo que haría una extensión con guardar_visor
        a = doc[1].add_text_annot((100, 300), "Nota de la extensión")
        a.update()

    e = core.Edit("text", 0, lines=[(72, 300, "Texto agregado")])
    core.apply_edits(src, src, [e], extra=nota, incremental=True)
    nuevo = src.read_bytes()
    assert nuevo.startswith(original) and len(nuevo) > len(original)
    with pymupdf.open(src) as d:
        assert "Texto agregado" in d[0].get_text() and len(list(d[1].annots())) == 1
        assert len(d.get_toc()) == 3 and "Página 3" in d[2].get_text()


def test_borrado_de_zona_reescribe_completo_y_no_deja_rastro(tmp_path):
    src = _pdf(tmp_path / "doc.pdf")
    original = src.read_bytes()
    assert _contiene(original, "SECRETO0")           # la búsqueda sí encuentra el texto cuando está
    e = core.Edit("redact", 0, rect=(60, 80, 400, 110))
    core.apply_edits(src, src, [e], incremental=True)
    nuevo = src.read_bytes()
    assert not nuevo.startswith(original) and nuevo.count(b"%%EOF") == 1
    assert not _contiene(nuevo, "SECRETO0") and "SECRETO0" not in _texto(src)
    assert "SECRETO1" in _texto(src)
    # una línea editada también borra (el texto viejo no debe quedar en una versión anterior)
    src2 = _pdf(tmp_path / "doc2.pdf")
    e2 = core.Edit("replace", 0, cover=(70, 85, 400, 104), lines=[(72, 100, "Línea nueva")])
    core.apply_edits(src2, src2, [e2], incremental=True)
    assert src2.read_bytes().count(b"%%EOF") == 1 and "SECRETO0" not in _texto(src2)


def test_documento_reparado_nunca_es_incremental(tmp_path):
    src = _pdf(tmp_path / "doc.pdf")
    data = src.read_bytes()
    i = data.rfind(b"startxref")
    dañado = tmp_path / "dañado.pdf"
    dañado.write_bytes(data[:i] + b"startxref\n999999\n%%EOF\n")     # tabla de referencias rota
    with pymupdf.open(dañado) as d:
        assert d.is_repaired and not guardado.puede_incremental(d)
    antes = dañado.read_bytes()
    core.apply_edits(dañado, dañado, [core.Edit("text", 0, lines=[(72, 300, "ok")])], incremental=True)
    assert not dañado.read_bytes().startswith(antes) and "ok" in _texto(dañado)


def test_formularios_incrementales_y_aplanar_completo(tmp_path):
    src = tmp_path / "form.pdf"
    with pymupdf.open() as d:
        p = d.new_page()
        w = pymupdf.Widget()
        w.field_name, w.field_type, w.rect = "nombre", pymupdf.PDF_WIDGET_TYPE_TEXT, pymupdf.Rect(100, 100, 300, 120)
        p.add_widget(w)
        d.save(src)
    original = src.read_bytes()
    cid = formularios.leer_campos(src)[0].id
    formularios.rellenar(src, src, {cid: "Ana María"}, incremental=True)
    assert src.read_bytes().startswith(original)
    assert formularios.leer_campos(src)[0].valor == "Ana María"
    antes = src.read_bytes()
    formularios.rellenar(src, src, {cid: "Otra"}, aplanar=True, incremental=True)
    assert not src.read_bytes().startswith(antes)
    with pymupdf.open(src) as d:
        assert not d.is_form_pdf and "Otra" in d[0].get_text()
    # con contraseña: sigue cifrado con la misma clave y el adjunto intacto
    cifrado = tmp_path / "form_cifrado.pdf"
    with pymupdf.open() as d:
        p = d.new_page()
        w = pymupdf.Widget()
        w.field_name, w.field_type, w.rect = "nombre", pymupdf.PDF_WIDGET_TYPE_TEXT, pymupdf.Rect(100, 100, 300, 120)
        p.add_widget(w)
        d.new_page().insert_text((72, 100), "Página dos sin tocar", fontsize=12)
        d.embfile_add("anexo.txt", b"anexo del formulario")
        d.save(cifrado, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="1234", owner_pw="dueño")
    original = cifrado.read_bytes()
    cid = formularios.leer_campos(cifrado, "1234")[0].id
    formularios.rellenar(cifrado, cifrado, {cid: "Con clave"}, password="1234", incremental=True)
    assert cifrado.read_bytes().startswith(original)
    assert core.needs_password(cifrado) and core.check_password(cifrado, "1234")
    assert formularios.leer_campos(cifrado, "1234")[0].valor == "Con clave"
    with core.open_pdf(cifrado, "1234") as d:
        assert "Página dos sin tocar" in d[1].get_text() and d.embfile_get(0) == b"anexo del formulario"


def test_contrasena_incremental_completo_y_autoguardado(protegido, tmp_path):
    pw = protegido.password
    # incremental: la misma clave, nada se pierde y los bytes originales quedan como prefijo
    a = tmp_path / "a.pdf"
    shutil.copyfile(protegido.path, a)
    original = a.read_bytes()
    core.apply_edits(a, a, [core.Edit("text", 0, lines=[(72, 600, "Agregado")])], password=pw, incremental=True)
    assert a.read_bytes().startswith(original)
    protegido.comprobar(a, sin_tocar=(1, 2))
    assert "Agregado" in _texto(a, pw)
    # completo (borrado de zona en la página 3)
    b = tmp_path / "b.pdf"
    shutil.copyfile(protegido.path, b)
    core.apply_edits(b, b, [core.Edit("redact", 2, rect=(60, 115, 500, 140))], password=pw, incremental=True)
    protegido.comprobar(b, sin_tocar=(0, 1))
    # autoguardado: el PDF recuperado queda cifrado con la misma clave
    carpeta = tmp_path / "rec"
    datos = guardado.manifiesto("viewer", str(protegido.path), "123-1-aa", protegido=True)
    m = guardado.autoguardar_pdf(core.apply_edits, carpeta, "123-1-aa", "viewer", datos, path=str(protegido.path),
                                 edits=[core.Edit("text", 1, lines=[(72, 700, "Autoguardado")])], password=pw,
                                 incremental=True)
    rec = carpeta / m["pdf"]
    protegido.comprobar(rec, sin_tocar=(0, 2))
    assert "Autoguardado" in _texto(rec, pw)
    assert pw not in (carpeta / "123-1-aa__viewer.json").read_text(encoding="utf-8")


def test_armar_pdf_un_solo_origen_conserva_clave_adjuntos_y_marcadores(protegido, tmp_path):
    out = tmp_path / "org.pdf"
    refs = [core.PageRef(0, 2), core.PageRef(0, 0, 90), core.PageRef(None, 0, 0, 300, 400)]
    assert guardado.armar_pdf([(str(protegido.path), protegido.password)], refs, out) == 3
    assert core.needs_password(out) and core.check_password(out, protegido.password)
    with core.open_pdf(out, protegido.password) as d:
        assert d[0].get_text() == protegido.textos[2] and d[1].rotation == 90
        assert d.embfile_get(0) == protegido.adjunto
    src = _pdf(tmp_path / "toc.pdf", paginas=4, toc=True, adjunto=True)
    out2 = tmp_path / "toc_org.pdf"
    guardado.armar_pdf([(str(src), None)], [core.PageRef(0, 3), core.PageRef(0, 1)], out2)
    with pymupdf.open(out2) as d:
        assert d.get_toc() == [[1, "Capítulo 4", 1], [1, "Capítulo 2", 2]] and d.embfile_count() == 1


def test_huerfanos_y_sesiones_vivas(tmp_path):
    carpeta = tmp_path / "RonyPDF_recuperacion"
    vivo = guardado.nueva_sesion()                          # esta misma sesión (proceso vivo)
    muerto = {"id": "999999-1-abcdef", "pid": 999999, "inicio": time.time() - 60, "creado": 0}
    for s in (vivo, muerto):
        guardado.registrar_sesion(carpeta, s)
        guardado.guardar_manifiesto(carpeta, s["id"], "organize",
                                    guardado.manifiesto("organize", str(tmp_path / "x.pdf"), s["id"],
                                                        descripcion="3 páginas sin guardar", fuentes=[], paginas=[]))
    # una sesión huérfana sin trabajo se limpia sola
    guardado.registrar_sesion(carpeta, {"id": "999998-1-abcdef", "pid": 999998, "inicio": 0, "creado": 0})
    trabajos = guardado.huerfanos([carpeta])
    assert [t.sesion for t in trabajos] == [muerto["id"]] and trabajos[0].herramienta == "organize"
    assert not (carpeta / "sesion_999998-1-abcdef.json").exists()
    # un PID reciclado (mismo número, otra hora de inicio) es otra sesión: huérfana
    otro = dict(vivo, id=f"{os.getpid()}-2-bbbbbb", creado=(vivo["creado"] or 1) + 10**9)
    guardado.registrar_sesion(carpeta, otro)
    guardado.guardar_manifiesto(carpeta, otro["id"], "organize", guardado.manifiesto("organize", "", otro["id"]))
    if sys.platform == "win32" and vivo["creado"]:
        assert otro["id"] in {t.sesion for t in guardado.huerfanos([carpeta])}
    guardado.descartar(trabajos[0])
    assert not list(carpeta.glob(f"{muerto['id']}*")) and not (carpeta / f"sesion_{muerto['id']}.json").exists()
    guardado.cerrar_sesion(carpeta, vivo["id"])
    assert not list(carpeta.glob(f"{vivo['id']}*"))


def test_fecha_amable():
    ahora_ = dt.datetime(2026, 11, 10, 18, 0).timestamp()
    assert guardado.fecha_con_de(dt.datetime(2026, 11, 10, 13, 5).timestamp(), ahora_) == "de hoy a la 1:05 p. m."
    assert guardado.fecha_con_de(dt.datetime(2026, 11, 2, 9, 0).timestamp(), ahora_) == \
        "del lunes 2 de noviembre a las 9:00 a. m."
    assert guardado.frase("a las 9:00 a. m.") == "a las 9:00 a. m." and guardado.frase("hola") == "hola."
    ahora = dt.datetime(2026, 11, 10, 18, 0).timestamp()
    assert guardado.fecha_amable(dt.datetime(2026, 11, 10, 15, 42).timestamp(), ahora) == "hoy 3:42 p. m."
    assert guardado.fecha_amable(dt.datetime(2026, 11, 9, 9, 5).timestamp(), ahora) == "ayer 9:05 a. m."
    assert guardado.fecha_amable(dt.datetime(2026, 11, 2, 0, 15).timestamp(), ahora) == \
        "lunes 2 de noviembre, 12:15 a. m."
    assert guardado.fecha_amable(dt.datetime(2026, 11, 10, 15, 42, 7).timestamp(), ahora, segundos=True) == \
        "hoy 3:42:07 p. m."


def test_carpeta_de_ajustes_sin_permiso_usa_appdata(tmp_path, monkeypatch):
    monkeypatch.delenv("RONYPDF_SETTINGS", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    bloqueada = tmp_path / "programa" / "RonyPDF.json"
    monkeypatch.setattr(guardado, "_escribible", lambda c: "programa" not in str(c) and not c.mkdir(
        parents=True, exist_ok=True))
    c = guardado.carpeta_escritura(guardado.CARPETA_RESPALDOS, bloqueada)
    assert c == (tmp_path / "appdata").resolve() / "RonyPDF" / guardado.CARPETA_RESPALDOS


# ================================================================ PDF reales de Word y Excel

from test_oficina import _docx_muestra, _procesos_office, _sin_procesos_nuevos, con_excel, con_word  # noqa: E402
from ronypdf import oficina  # noqa: E402


@con_word
def test_word_real_guardado_incremental(tmp_path):
    antes = _procesos_office()
    doc = _docx_muestra(tmp_path / "Carta de gestión.docx")
    pdf = tmp_path / "carta.pdf"
    oficina.a_pdf(doc, pdf)
    _sin_procesos_nuevos(antes, espera=90)     # (otros programas pueden estar usando Office a la vez)
    original = pdf.read_bytes()
    with pymupdf.open(pdf) as d:
        toc = d.get_toc()
        assert guardado.modo_incremental(d)             # tabla de referencias comprimida: sí admite incremental
    core.apply_edits(pdf, pdf, [core.Edit("text", 1, lines=[(72, 500, "Visto bueno, Ana")])], incremental=True)
    assert pdf.read_bytes().startswith(original)
    with pymupdf.open(pdf) as d:
        assert not d.is_repaired and d.get_toc() == toc
        assert "Visto bueno, Ana" in d[1].get_text() and "Tercera página" in d[2].get_text()


@con_excel
def test_excel_real_guardado_incremental(tmp_path):
    antes = _procesos_office()
    xlsx = tmp_path / "Presupuesto.xlsx"
    oficina.escribir_xlsx([("Presupuesto", [["Concepto", "Valor"], ["Talleres de niñez", 1250000]])], xlsx)
    pdf = tmp_path / "presupuesto.pdf"
    oficina.a_pdf(xlsx, pdf)
    _sin_procesos_nuevos(antes, espera=90)     # (otros programas pueden estar usando Office a la vez)
    original = pdf.read_bytes()

    def nota(d):
        d[0].add_text_annot((300, 300), "Revisar el valor").update()

    core.apply_edits(pdf, pdf, [], extra=nota, incremental=True)
    assert pdf.read_bytes().startswith(original)
    with pymupdf.open(pdf) as d:
        assert "Talleres de niñez" in d[0].get_text() and len(list(d[0].annots())) == 1


# ================================================================ interfaz sin pantalla

@pytest.fixture(scope="module")
def win(tmp_path_factory):
    from ronypdf.gui import MainWindow, _light_palette
    from ronypdf.settings import Settings
    from ronypdf.style import QSS
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setPalette(_light_palette())
    app.setStyleSheet(QSS)
    anterior = widgets.ctx.settings
    base = tmp_path_factory.mktemp("ajustes_guardado")
    widgets.ctx.settings = Settings(base / "RonyPDF.json")      # copias y recuperación de esta prueba, aparte
    w = MainWindow()
    w.resize(1240, 820)
    w.show()
    app.processEvents()
    w._autoguardado.timer.stop()                                  # el autoguardado se dispara a mano
    yield w
    w._autoguardado.timer.stop()
    w.hide()
    widgets.ctx.settings = anterior
    widgets.ctx.window = None


class Registro(list):
    confirmar: list
    respuestas: dict


@pytest.fixture
def dlg(monkeypatch, tmp_path):
    """Diálogos parcheados: cada pregunta de guardado_ui responde lo que diga ``respuestas``."""
    from ronypdf import formularios_ui, guardado_ui, organizer, viewer
    reg = Registro()
    reg.respuestas = {"confirmar": ("guardar", False), "cambios": "descartar", "externo": "reemplazar",
                      "firma": "copia", "no_escribible": "guardar_como", "respaldo": "cancelar"}
    reg.confirmar = []

    def fake_done(parent, title, detail="", file=None, folder=None):
        reg.append(("done", title, detail, file))

    for mod in (viewer, organizer, formularios_ui, widgets, guardado_ui):
        monkeypatch.setattr(mod, "show_done", fake_done, raising=False)
    monkeypatch.setattr(widgets.QMessageBox, "warning", lambda *a, **k: reg.append(("warning", a[2])))
    monkeypatch.setattr(widgets.QMessageBox, "information", lambda *a, **k: reg.append(("info", a[2])))
    monkeypatch.setattr(widgets.QMessageBox, "question", lambda *a, **k: widgets.QMessageBox.StandardButton.Yes)

    def confirmar(parent, nombre, respaldo):
        reg.confirmar.append((nombre, respaldo))
        return reg.respuestas["confirmar"]

    monkeypatch.setattr(guardado_ui, "confirmar_mismo_archivo", confirmar)
    monkeypatch.setattr(guardado_ui, "preguntar_cambios",
                        lambda p, t, x: reg.append(("cambios", x)) or reg.respuestas["cambios"])
    monkeypatch.setattr(guardado_ui, "preguntar_cambio_externo",
                        lambda p, n: reg.append(("externo", n)) or reg.respuestas["externo"])
    monkeypatch.setattr(guardado_ui, "avisar_firma", lambda p: reg.append(("firma",)) or reg.respuestas["firma"])
    monkeypatch.setattr(guardado_ui, "avisar_no_escribible",
                        lambda p, m: reg.append(("no_escribible", m)) or reg.respuestas["no_escribible"])
    monkeypatch.setattr(guardado_ui, "respaldo_fallido",
                        lambda p, m: reg.append(("respaldo", m)) or reg.respuestas["respaldo"])
    monkeypatch.setattr(guardado_ui, "_preguntar", lambda *a, **k: (reg.respuestas.get("_preguntar", "si"), False))
    guardado_ui._confirmados.clear()
    return reg


def esperar(timeout=60):
    fin = time.time() + timeout
    while time.time() < fin:
        QApplication.processEvents()
        if not widgets.ctx.busy and not getattr(widgets.ctx, "_silenciosas", None):
            QApplication.processEvents()
            return
        time.sleep(0.01)
    raise TimeoutError


def _abrir_visor(win, path):
    v = win.pages["viewer"]
    win.navigate("viewer")
    assert v.load(str(path), confirm=False)
    QApplication.processEvents()
    return v


def _carpeta_respaldos():
    from ronypdf import guardado_ui
    return guardado_ui.carpetas_respaldos()


def test_run_silent(win):
    from ronypdf.widgets import ctx
    hechos, fallos = [], []

    def lento(progress=None, x=0):
        time.sleep(0.2)
        return x * 2

    ctx.run_silent(lento, hechos.append, fallos.append, x=21)
    assert not ctx.busy and win.centralWidget().isEnabled()
    # una tarea normal lanzada mientras tanto espera su turno
    normales = []
    ctx.run_task("Prueba", lambda progress=None: "normal", normales.append)
    esperar()
    assert hechos == [42] and normales == ["normal"]

    def roto(progress=None):
        raise ValueError("falla interna")

    ctx.run_silent(roto, hechos.append, fallos.append)
    ctx.run_silent(lambda progress=None: (_ for _ in ()).throw(core.UserError("sin espacio")), None, fallos.append)
    esperar()
    assert fallos == ["ValueError: falla interna", "sin espacio"]


def test_guardar_directo_visor(win, dlg, tmp_path):
    from ronypdf import guardado_ui
    src = _pdf(tmp_path / "informe.pdf", paginas=5)
    original = src.read_bytes()
    v = _abrir_visor(win, src)
    c = v.canvas
    assert v.b_save.text() == "Guardar como…"
    c.add_text(c.pages[2].pos() + QPointF(72, 300), "Primera nota")
    c.go_to_page(2)
    assert v.b_save.text() == "Guardar" and v.save_group.a_guardar.isEnabled()
    assert v.save_group.width() <= guardado_ui.BotonGuardar.ANCHO_MAX
    # «Cancelar» en la confirmación: no pasa nada
    dlg.respuestas["confirmar"] = ("cancelar", False)
    v.guardar()
    esperar()
    assert src.read_bytes() == original and c.has_changes() and dlg.confirmar == [("informe.pdf", True)]
    # «Guardar»: copia de respaldo, guardado incremental, recarga en la misma página y aviso discreto
    dlg.respuestas["confirmar"] = ("guardar", False)
    v.guardar()
    esperar()
    assert not [x for x in dlg if x[0] == "done"]                     # sin cuadro «Listo»
    assert src.read_bytes().startswith(original) and "Primera nota" in _texto(src)
    assert not c.has_changes() and c.path == str(src) and c.current_page == 2
    assert v.tip_text.text() == "Guardado. La versión anterior quedó en las copias de respaldo."
    copias = guardado.respaldos_de(src, _carpeta_respaldos())
    assert len(copias) == 1 and copias[0].ruta.read_bytes() == original
    # la segunda vez en la sesión ya no pregunta
    c.add_text(c.pages[0].pos() + QPointF(72, 400), "Segunda nota")
    v.guardar()
    esperar()
    assert len(dlg.confirmar) == 2 and "Segunda nota" in _texto(src)
    assert len(guardado.respaldos_de(src, _carpeta_respaldos())) == 2
    # «Guardar como…» desde la confirmación
    otro = _pdf(tmp_path / "otro.pdf")
    _abrir_visor(win, otro)
    c.add_text(c.pages[0].pos() + QPointF(72, 400), "Va a otro archivo")
    dlg.respuestas["confirmar"] = ("guardar_como", False)
    from ronypdf import viewer
    destino = tmp_path / "otro_editado.pdf"
    viewer.ask_save, real = (lambda *a, **k: str(destino)), viewer.ask_save
    try:
        v.guardar()
        esperar()
    finally:
        viewer.ask_save = real
    assert ("done", "Cambios guardados") == dlg[-1][:2] and "Va a otro archivo" in _texto(destino)
    assert "Va a otro archivo" not in _texto(otro)


def test_no_volver_a_preguntar_y_sin_respaldo(win, dlg, tmp_path):
    from ronypdf import guardado_ui
    from ronypdf.widgets import ctx
    src = _pdf(tmp_path / "doc.pdf")
    v = _abrir_visor(win, src)
    v.canvas.add_text(v.canvas.pages[0].pos() + QPointF(72, 300), "Uno")
    dlg.respuestas["confirmar"] = ("guardar", True)
    v.guardar()
    esperar()
    assert ctx.settings.get("guardado_confirmar") is False
    guardado_ui._confirmados.clear()
    v.canvas.add_text(v.canvas.pages[0].pos() + QPointF(72, 330), "Dos")
    ctx.settings.set("guardado_respaldo", False)
    try:
        v.guardar()
        esperar()
    finally:
        ctx.settings.set("guardado_respaldo", True)
        ctx.settings.set("guardado_confirmar", True)
    assert len(dlg.confirmar) == 1 and "Dos" in _texto(src)
    assert len(guardado.respaldos_de(src, _carpeta_respaldos())) == 1      # la segunda vez sin copia
    assert v.tip_text.text() == "Guardado."


def test_archivo_cambiado_en_disco_y_solo_lectura(win, dlg, tmp_path, monkeypatch):
    from ronypdf import viewer
    src = _pdf(tmp_path / "doc.pdf")
    v = _abrir_visor(win, src)
    c = v.canvas
    c.add_text(c.pages[0].pos() + QPointF(72, 300), "Mío")
    # otro programa lo guardó mientras estaba abierto
    with pymupdf.open() as d:
        d.new_page().insert_text((72, 72), "Versión de otro programa")
        externo = d.tobytes()
    c.close_doc()
    src.write_bytes(externo)
    c.reopen()
    dlg.respuestas["externo"] = "cancelar"
    v.guardar()
    esperar()
    assert ("externo", "doc.pdf") in dlg and src.read_bytes() == externo and c.has_changes()
    dlg.respuestas["externo"] = "guardar_como"
    destino = tmp_path / "copia.pdf"
    monkeypatch.setattr(viewer, "ask_save", lambda *a, **k: str(destino))
    v.guardar()
    esperar()
    assert src.read_bytes() == externo and destino.exists()
    # solo lectura: se explica y se ofrece «Guardar como…»
    ro = _pdf(tmp_path / "ro.pdf")
    v = _abrir_visor(win, ro)
    v.canvas.add_text(v.canvas.pages[0].pos() + QPointF(72, 300), "x")
    os.chmod(ro, 0o444)
    try:
        dlg.respuestas["no_escribible"] = "cancelar"
        v.guardar()
        esperar()
        assert any(x[0] == "no_escribible" and "solo lectura" in x[1] for x in dlg)
    finally:
        os.chmod(ro, 0o666)


def test_respaldo_fallido_no_guarda_encima_y_ofrece_guardar_como(win, dlg, tmp_path, monkeypatch):
    src = _pdf(tmp_path / "doc.pdf")
    original = src.read_bytes()
    v = _abrir_visor(win, src)
    v.canvas.add_text(v.canvas.pages[0].pos() + QPointF(72, 300), "Cambio")

    def falla(*a, **k):
        raise core.UserError("No se pudo hacer la copia de respaldo de «doc.pdf»: el disco está lleno.")

    monkeypatch.setattr(guardado, "respaldar", falla)
    v.guardar()
    esperar()
    assert any(x[0] == "respaldo" and "disco" in x[1] for x in dlg)
    assert src.read_bytes() == original and v.canvas.has_changes() and v.canvas.doc is not None


def test_firma_digital_avisa_solo_si_hay_que_reescribir(win, dlg, tmp_path, monkeypatch):
    from ronypdf import viewer
    src = _pdf(tmp_path / "firmado.pdf")
    with pymupdf.open(src) as d:                         # campo de firma y SigFlags = 3 (firmas, solo agregar)
        w = pymupdf.Widget()
        w.field_name, w.field_type = "firma", pymupdf.PDF_WIDGET_TYPE_SIGNATURE
        w.rect = pymupdf.Rect(300, 700, 500, 760)
        d[0].add_widget(w)
        cat = d.pdf_catalog()
        d.xref_set_key(cat, "AcroForm/SigFlags", "3")
        d.save(tmp_path / "f2.pdf")
    shutil.move(tmp_path / "f2.pdf", src)
    original = src.read_bytes()
    v = _abrir_visor(win, src)
    c = v.canvas
    with pymupdf.open(src) as d:
        assert guardado.tiene_firma_digital(d)
    c.add_text(c.pages[0].pos() + QPointF(72, 300), "Nota")
    v.guardar()                                          # incremental: la firma sigue valiendo, sin aviso
    esperar()
    assert not any(x[0] == "firma" for x in dlg) and src.read_bytes().startswith(original)
    c.add_shape("redact", QRectF(c.pages[0].pos() + QPointF(60, 80), c.pages[0].pos() + QPointF(400, 110)))
    destino = tmp_path / "firmado_copia.pdf"
    monkeypatch.setattr(viewer, "ask_save", lambda *a, **k: str(destino))
    antes = src.read_bytes()
    v.guardar()
    esperar()
    assert ("firma",) in dlg and src.read_bytes() == antes and destino.exists()


def test_contrasena_guardado_directo(win, dlg, protegido, tmp_path, monkeypatch):
    src = tmp_path / "protegido.pdf"
    shutil.copyfile(protegido.path, src)
    widgets.ctx.passwords.set(str(src), protegido.password)
    original = src.read_bytes()
    v = _abrir_visor(win, src)
    v.canvas.add_text(v.canvas.pages[0].pos() + QPointF(72, 600), "Revisado")
    v.guardar()
    esperar()
    assert src.read_bytes().startswith(original)
    protegido.comprobar(src, sin_tocar=(1, 2))
    copia = guardado.respaldos_de(src, _carpeta_respaldos())[0]
    assert copia.ruta.read_bytes() == original                 # el respaldo sigue cifrado, idéntico
    assert not v.canvas.has_changes() and v.canvas.password == protegido.password


def test_rehacer_en_el_visor(win, dlg, tmp_path):
    src = _pdf(tmp_path / "doc.pdf")
    v = _abrir_visor(win, src)
    c = v.canvas
    p0 = c.pages[0]
    a = c.add_shape("rect", QRectF(p0.pos() + QPointF(50, 200), p0.pos() + QPointF(150, 260)))
    b = c.add_text(p0.pos() + QPointF(72, 400), "Texto")
    c.scene().clearSelection()
    a.setSelected(True)
    c.delete_selected()
    assert a.scene() is None and not v.b_redo.isEnabled()
    c.undo()                                              # vuelve el rectángulo
    assert a.scene() is not None and v.b_redo.isEnabled()
    c.undo()                                              # se va el texto
    assert b.scene() is None
    c.redo()
    assert b.scene() is not None
    c.redo()
    assert a.scene() is None and not c.redo_stack
    c.undo()
    assert a.scene() is not None and c.redo_stack
    c.add_line("line", p0.pos() + QPointF(10, 10), p0.pos() + QPointF(90, 90))      # acción nueva
    assert not c.redo_stack and not v.b_redo.isEnabled()

    class Accion:                                         # una acción de extensión
        texto = "Nota"

        def __init__(self):
            self.n = {"deshacer": 0, "rehacer": 0}

        def deshacer(self):
            self.n["deshacer"] += 1

        def rehacer(self):
            self.n["rehacer"] += 1

    acc = Accion()
    c.registrar_ext(acc)
    v._undo()
    v._redo()
    v._undo()
    assert acc.n == {"deshacer": 2, "rehacer": 1} and c.redo_stack[-1] == ("ext", acc)
    c.registrar_ext(Accion())
    assert not c.redo_stack
    # mientras se escribe, Ctrl+Z y Ctrl+Y deshacen y rehacen la escritura
    t = c.add_text(p0.pos() + QPointF(72, 500), "")
    t.start_editing()
    from PySide6.QtTest import QTest
    QTest.keyClicks(c, "Hola")
    assert c.editing_text() is t and t.toPlainText() == "Hola"
    v._undo()
    assert t.toPlainText() == ""
    v._redo()
    assert t.toPlainText() == "Hola"
    t.clearFocus()
    # abrir otro documento vacía las dos pilas
    _abrir_visor(win, src)
    assert not c.undo_stack and not c.redo_stack


def test_rehacer_en_formularios(win, dlg, tmp_path):
    src = tmp_path / "form.pdf"
    with pymupdf.open() as d:
        p = d.new_page()
        w = pymupdf.Widget()
        w.field_name, w.field_type, w.rect = "nombre", pymupdf.PDF_WIDGET_TYPE_TEXT, pymupdf.Rect(100, 100, 300, 120)
        p.add_widget(w)
        d.save(src)
    f = win.pages["forms"]
    win.navigate("forms")
    assert f.load(str(src), confirm=False)
    cid = f.navegables[0]
    f.set_valor(cid, "Uno")
    f.set_valor(cid, "Dos")
    f.deshacer()
    assert f.valores()[cid] == "Uno" and f.b_redo.isEnabled()
    f.rehacer()
    assert f.valores()[cid] == "Dos" and not f.redo_stack
    sello = f.agregar_sello("equis", 0, 200, 300)
    f.deshacer()
    assert sello.scene() is None
    f.rehacer()
    assert sello.scene() is not None
    f.canvas.scene().clearSelection()
    sello.setSelected(True)
    f.borrar_seleccion()
    f.deshacer()
    assert sello.scene() is not None
    f.rehacer()
    assert sello.scene() is None
    f.deshacer()
    f.set_valor(cid, "Tres")                              # acción nueva: ya no hay qué rehacer
    assert not f.redo_stack
    f.load(str(src), confirm=False)


def test_guardar_directo_formularios_e_incremental(win, dlg, tmp_path, monkeypatch):
    from ronypdf import formularios_ui
    src = tmp_path / "form.pdf"
    with pymupdf.open() as d:
        p = d.new_page()
        w = pymupdf.Widget()
        w.field_name, w.field_type, w.rect = "nombre", pymupdf.PDF_WIDGET_TYPE_TEXT, pymupdf.Rect(100, 100, 300, 120)
        p.add_widget(w)
        d.save(src)
    original = src.read_bytes()
    f = win.pages["forms"]
    win.navigate("forms")
    f.load(str(src), confirm=False)
    f.set_valor(f.navegables[0], "Ana María")
    assert f.action.text() == "Guardar formulario" and f.action.isEnabled()     # directo, con respaldo
    f.guardar()
    esperar()
    assert src.read_bytes().startswith(original) and formularios.leer_campos(src)[0].valor == "Ana María"
    assert not f.has_unsaved() and f.footer_info.text().startswith("Guardado.")
    assert len(guardado.respaldos_de(src, _carpeta_respaldos())) == 1
    # con «Aplanar» el directo no se ofrece: solo «Guardar como…»
    f.set_valor(f.navegables[0], "Otra")
    f.chk_aplanar.setChecked(True)
    assert f.action.text() == "Guardar copia…" and not f.b_como.isVisible()
    assert "aplanar" in f.footer_info.text().lower()
    destino = tmp_path / "plano.pdf"
    monkeypatch.setattr(formularios_ui, "ask_save", lambda *a, **k: str(destino))
    f._guardar_principal()
    esperar()
    f.chk_aplanar.setChecked(False)
    assert destino.exists() and formularios.leer_campos(src)[0].valor == "Ana María"
    with pymupdf.open(destino) as d:
        assert not d.is_form_pdf


def test_guardar_directo_organizador(win, dlg, protegido, tmp_path):
    src = tmp_path / "org.pdf"
    shutil.copyfile(protegido.path, src)
    widgets.ctx.passwords.set(str(src), protegido.password)
    o = win.pages["organize"]
    win.navigate("organize")
    o.load_files([str(src)])
    assert not o.has_unsaved() and o.b_save.text() == "Guardar como…"
    o.grid.clearSelection()
    o.grid.item(0).setSelected(True)
    o.rotate(90)
    assert o.has_unsaved() and o.b_save.text() == "Guardar"
    o.guardar()
    esperar()
    assert not o.has_unsaved() and o.grid.count() == 3 and not o.undo_stack
    protegido.comprobar(src, sin_tocar=(1, 2))
    with core.open_pdf(src, protegido.password) as d:
        assert d[0].rotation == 90
    assert len(guardado.respaldos_de(src, _carpeta_respaldos())) == 1
    # con dos archivos de origen no hay guardado directo
    otro = _pdf(tmp_path / "otro.pdf")
    o.insert_files([str(otro)])
    assert o.has_unsaved() and o.b_save.text() == "Guardar como…" and not o._directo()[0]
    o.load_files([str(src)])                              # «No guardar» (respuesta parcheada)
    assert ("cambios", "¿Quieres guardar los cambios de las páginas antes de abrir otro PDF?") in dlg


def test_abrir_otro_con_guardar_no_guardar_y_cancelar(win, dlg, tmp_path):
    a = _pdf(tmp_path / "a.pdf")
    b = _pdf(tmp_path / "b.pdf")
    v = _abrir_visor(win, a)
    v.canvas.add_text(v.canvas.pages[0].pos() + QPointF(72, 300), "Cambio en A")
    dlg.respuestas["cambios"] = "cancelar"
    assert not v.load(str(b)) and v.canvas.path == str(a) and v.canvas.has_changes()
    dlg.respuestas["cambios"] = "guardar"                 # guarda de verdad antes de abrir el otro
    assert v.load(str(b)) and v.canvas.path == str(b)
    assert "Cambio en A" in _texto(a)
    v.canvas.add_text(v.canvas.pages[0].pos() + QPointF(72, 300), "Cambio en B")
    dlg.respuestas["cambios"] = "descartar"
    assert v.load(str(a)) and "Cambio en B" not in _texto(b)


def test_cerrar_la_ventana_con_cambios_en_dos_pantallas(win, dlg, tmp_path):
    from PySide6.QtGui import QCloseEvent
    a = _pdf(tmp_path / "a.pdf")
    v = _abrir_visor(win, a)
    v.canvas.add_text(v.canvas.pages[0].pos() + QPointF(72, 300), "Visor")
    o = win.pages["organize"]
    o.load_files([str(_pdf(tmp_path / "o.pdf"))])
    o.grid.item(0).setSelected(True)
    o.rotate(90)
    pantallas = []
    respuestas = iter(["guardar", "cancelar"])

    def preguntar(p, titulo, texto):
        pantallas.append(win.stack.currentWidget())
        return next(respuestas)

    from ronypdf import guardado_ui
    guardado_ui.preguntar_cambios, real = preguntar, guardado_ui.preguntar_cambios
    try:
        e = QCloseEvent()
        win.closeEvent(e)
        assert not e.isAccepted()                          # «Cancelar» en la segunda: no se cierra
        assert pantallas == [v, o] and "Visor" in _texto(a) and not v.has_unsaved() and o.has_unsaved()
        respuestas = iter(["descartar"])
        e = QCloseEvent()
        g = win._autoguardado
        cerrar = g.cerrar
        g.cerrar = lambda: None                            # la ventana de la prueba sigue viva
        try:
            win.closeEvent(e)
        finally:
            g.cerrar = cerrar
        assert e.isAccepted()
    finally:
        guardado_ui.preguntar_cambios = real
    o.load_files([str(a)])


def _forzar_autoguardado(win):
    g = win._autoguardado
    g.ahora()
    esperar()
    return g


def test_autoguardado_visor_formularios_y_organizador(win, dlg, tmp_path):
    from ronypdf import extensiones
    g = win._autoguardado
    src = _pdf(tmp_path / "auto.pdf")
    v = _abrir_visor(win, src)
    c = v.canvas
    _forzar_autoguardado(win)
    assert "viewer" not in g.guardados                    # sin cambios no autoguarda
    t = c.add_text(c.pages[0].pos() + QPointF(72, 300), "")
    t.start_editing()
    from PySide6.QtTest import QTest
    QTest.keyClicks(c, "Escribiendo")
    # una extensión con cambios propios: entran en el autoguardado
    llamadas = []

    def extra_visor(visor):
        def aplicar(doc):
            llamadas.append(1)
            doc[1].add_text_annot((100, 100), "Comentario de extensión").update()
        return aplicar

    real = extensiones.autoguardar_visor          # el autoguardado usa su propio enganche (sin tocar la pantalla)
    extensiones.autoguardar_visor = extra_visor
    try:
        _forzar_autoguardado(win)
    finally:
        extensiones.autoguardar_visor = real
    assert c.editing_text() is t                          # no le quitó el foco al texto en edición
    assert "viewer" in g.guardados and llamadas
    m = guardado._leer_json(g.carpeta / f"{g.sesion['id']}__viewer.json")
    assert m["herramienta"] == "viewer" and m["documento"] == str(src) and m["descripcion"] == "1 cambio sin guardar"
    rec = g.carpeta / m["pdf"]
    with pymupdf.open(rec) as d:
        assert "Escribiendo" in d[0].get_text() and len(list(d[1].annots())) == 1
    # sin cambios nuevos no vuelve a escribir
    antes = rec.stat().st_mtime_ns
    _forzar_autoguardado(win)
    assert rec.stat().st_mtime_ns == antes
    t.clearFocus()
    # al guardar, se borra lo autoguardado
    v.guardar()
    esperar()
    assert not rec.exists() and "viewer" not in g.guardados
    # formularios y organizador
    form = tmp_path / "form.pdf"
    with pymupdf.open() as d:
        p = d.new_page()
        w = pymupdf.Widget()
        w.field_name, w.field_type, w.rect = "nombre", pymupdf.PDF_WIDGET_TYPE_TEXT, pymupdf.Rect(100, 100, 300, 120)
        p.add_widget(w)
        d.save(form)
    f = win.pages["forms"]
    f.load(str(form), confirm=False)
    f.set_valor(f.navegables[0], "Autoguardado")
    o = win.pages["organize"]
    o.load_files([str(src)])
    o.grid.item(0).setSelected(True)
    o.delete()
    _forzar_autoguardado(win)
    assert {"forms", "organize"} <= g.guardados
    mf = guardado._leer_json(g.carpeta / f"{g.sesion['id']}__forms.json")
    assert formularios.leer_campos(g.carpeta / mf["pdf"])[0].valor == "Autoguardado"
    mo = guardado._leer_json(g.carpeta / f"{g.sesion['id']}__organize.json")
    assert mo["fuentes"] == [str(src)] and len(mo["paginas"]) == 2 and mo["pdf"] is None
    # descartar al abrir otro documento borra lo autoguardado de esa herramienta
    dlg.respuestas["cambios"] = "descartar"
    o.load_files([str(src)])
    f.load(str(form))
    assert not list(g.carpeta.glob(f"{g.sesion['id']}__organize*")) and "forms" not in g.guardados


def test_autoguardado_fallido_no_molesta(win, dlg, tmp_path, monkeypatch):
    g = win._autoguardado
    src = _pdf(tmp_path / "doc.pdf")
    v = _abrir_visor(win, src)
    v.canvas.add_text(v.canvas.pages[0].pos() + QPointF(72, 300), "Algo")

    def lleno(*a, **k):
        raise OSError(28, "No space left on device")

    real = guardado.autoguardar_pdf
    monkeypatch.setattr(guardado, "autoguardar_pdf", lleno)
    _forzar_autoguardado(win)
    assert g.ultimo_error and "No space" in g.ultimo_error and "viewer" not in g.guardados
    assert not dlg and v.canvas.has_changes() and v.canvas.doc is not None      # ni cuadros ni daños
    monkeypatch.setattr(guardado, "autoguardar_pdf", real)
    v.load(str(src), confirm=False)


def test_recuperacion_en_el_mismo_proceso(win, dlg, tmp_path, monkeypatch):
    from ronypdf import guardado_ui
    g = win._autoguardado
    carpeta = guardado.carpeta_escritura(guardado.CARPETA_RECUPERACION, g.ajustes)
    sid = "999997-1-cccccc"
    guardado.registrar_sesion(carpeta, {"id": sid, "pid": 999997, "inicio": time.time() - 30, "creado": 0})
    src = _pdf(tmp_path / "Carta ñ.pdf")
    otro = _pdf(tmp_path / "otro.pdf")
    datos = guardado.manifiesto("viewer", str(src), sid, pagina=1, descripcion="1 cambio sin guardar")
    guardado.autoguardar_pdf(core.apply_edits, carpeta, sid, "viewer", datos, path=str(src),
                             edits=[core.Edit("text", 1, lines=[(72, 300, "Recuperado del visor")])])
    guardado.guardar_manifiesto(carpeta, sid, "organize", guardado.manifiesto(
        "organize", str(src), sid, descripcion="4 páginas sin guardar", fuentes=[str(src), str(tmp_path / "ya no está.pdf")],
        paginas=[[0, 2, 90, 595, 842], [1, 0, 0, 595, 842], [-1, 0, 0, 300, 400], [0, 0, 0, 595, 842]]))
    trabajos = guardado_ui.trabajos_pendientes()
    assert {t.herramienta for t in trabajos} == {"viewer", "organize"}
    vistos = []
    monkeypatch.setattr(guardado_ui.DialogoRecuperar, "exec", lambda self: vistos.append(self.trabajos) or 0)
    guardado_ui.ofrecer_recuperacion(win)
    assert len(vistos) == 1 and len(vistos[0]) == 2
    # recuperar el del visor: se guarda junto al original con otro nombre y se abre
    tv = next(t for t in trabajos if t.herramienta == "viewer")
    assert guardado.sugerido_recuperacion(tv).endswith("Carta ñ_recuperado.pdf")
    monkeypatch.setattr(guardado_ui, "ask_save", lambda *a, **k: str(src))           # encima del original no
    pedidos = iter([str(src), str(tmp_path / "Carta ñ_recuperado.pdf")])
    monkeypatch.setattr(guardado_ui, "ask_save", lambda *a, **k: next(pedidos))
    assert guardado_ui.recuperar(tv)
    assert any(x[0] == "info" and "otro nombre" in x[1] for x in dlg)
    v = win.pages["viewer"]
    assert v.canvas.path == str(tmp_path / "Carta ñ_recuperado.pdf") and v.canvas.current_page == 1
    assert "Recuperado del visor" in _texto(tmp_path / "Carta ñ_recuperado.pdf") and "Recuperado" not in _texto(src)
    # el organizador: reabre los orígenes, omite el que ya no está y queda con cambios
    to = next(t for t in trabajos if t.herramienta == "organize")
    assert guardado_ui.recuperar(to)
    o = win.pages["organize"]
    assert o.grid.count() == 3 and o.has_unsaved() and o._row(o.grid.item(0))[2] == 90
    assert any(x[0] == "info" and "ya no está.pdf" in x[1] for x in dlg)
    assert guardado_ui.trabajos_pendientes() == []
    vistos.clear()
    guardado_ui.ofrecer_recuperacion(win)                 # sin nada que recuperar no aparece nada
    assert vistos == []
    dlg.respuestas["cambios"] = "descartar"
    o.load_files([str(otro)])


def test_cierre_normal_no_deja_nada_para_recuperar(win, dlg, tmp_path):
    from ronypdf import guardado_ui
    from ronypdf.gui import MainWindow
    w2 = MainWindow()
    w2._autoguardado.timer.stop()
    v = w2.pages["viewer"]
    src = _pdf(tmp_path / "doc.pdf")
    assert v.load(str(src), confirm=False)
    v.canvas.add_text(v.canvas.pages[0].pos() + QPointF(72, 300), "Pendiente")
    g = w2._autoguardado
    g.ahora()
    esperar()
    archivos = list(g.carpeta.glob(f"*{g.sesion['id']}*"))
    assert len(archivos) == 3                              # sesión, manifiesto y PDF
    dlg.respuestas["cambios"] = "descartar"
    w2.close()
    QApplication.processEvents()
    assert not list(g.carpeta.glob(f"*{g.sesion['id']}*")) if g.carpeta.exists() else True
    assert guardado_ui.trabajos_pendientes() == []
    widgets.ctx.window = win


HIJO = r'''
import os, sys, time
sys.path.insert(0, {raiz!r})
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["RONYPDF_SETTINGS"] = {ajustes!r}
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication
app = QApplication([])
from ronypdf import gui, widgets, guardado_ui
win = gui.MainWindow()
win.show()
app.processEvents()
v = win.pages["viewer"]
v.load({pdf!r}, confirm=False)
v.canvas.add_text(v.canvas.pages[0].pos() + QPointF(72, 300), "Escrito antes del apagón")
f = win.pages["forms"]
f.load({form!r}, confirm=False)
f.set_valor(f.navegables[0], "Campo antes del apagón")
o = win.pages["organize"]
o.load_files([{pdf!r}])
o.grid.item(0).setSelected(True)
o.delete()
g = win._autoguardado
g.ahora()
fin = time.time() + 60
while time.time() < fin and (g.tarea is not None or len(g.guardados) < 3):
    app.processEvents(); time.sleep(0.02)
    if g.tarea is None and len(g.guardados) < 3:
        g.ahora()
print("AUTOGUARDADO", sorted(g.guardados), flush=True)
os._exit(1)                       # cierre inesperado: sin al_cerrar ni limpieza
'''


def test_cierre_inesperado_real_con_un_proceso_hijo(win, dlg, tmp_path, monkeypatch):
    from ronypdf import guardado_ui
    from ronypdf.gui import MainWindow
    from ronypdf.settings import Settings
    ajustes = tmp_path / "ajustes" / "RonyPDF.json"
    pdf = _pdf(tmp_path / "apagón.pdf")
    form = tmp_path / "form.pdf"
    with pymupdf.open() as d:
        p = d.new_page()
        w = pymupdf.Widget()
        w.field_name, w.field_type, w.rect = "nombre", pymupdf.PDF_WIDGET_TYPE_TEXT, pymupdf.Rect(100, 100, 300, 120)
        p.add_widget(w)
        d.save(form)
    script = tmp_path / "hijo.py"
    script.write_text(HIJO.format(raiz=str(RAIZ), ajustes=str(ajustes), pdf=str(pdf), form=str(form)),
                      encoding="utf-8")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", RONYPDF_SETTINGS=str(ajustes), PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, encoding="utf-8",
                       timeout=180, env=env)
    # termina sin cerrar nada (os._exit; en Windows, Qt puede además dar un código de error al salir así)
    assert r.returncode != 0 and "AUTOGUARDADO ['forms', 'organize', 'viewer']" in r.stdout, r.stdout + r.stderr
    # «otra ventana con los mismos ajustes»: ofrece recuperar los tres trabajos
    anterior = widgets.ctx.settings
    widgets.ctx.settings = Settings(ajustes)
    vistos = []
    monkeypatch.setattr(guardado_ui.DialogoRecuperar, "exec", lambda self: vistos.append(self) or 0)
    try:
        w2 = MainWindow()
        w2._autoguardado.timer.stop()
        guardado_ui.ofrecer_recuperacion(w2)
        assert len(vistos) == 1
        dialogo = vistos[0]
        trabajos = {t.herramienta: t for t in dialogo.trabajos}
        assert set(trabajos) == {"viewer", "forms", "organize"}
        assert "Escrito antes del apagón" in _texto(trabajos["viewer"].pdf)
        assert formularios.leer_campos(trabajos["forms"].pdf)[0].valor == "Campo antes del apagón"
        assert len(trabajos["organize"].manifiesto["paginas"]) == 2
        # recuperar desde el diálogo
        destino = tmp_path / "apagón_recuperado.pdf"
        monkeypatch.setattr(guardado_ui, "ask_save", lambda *a, **k: str(destino))
        k = next(i for i, t in enumerate(dialogo.trabajos) if t.herramienta == "viewer")
        dialogo.recuperar(k)
        assert destino.exists() and w2.pages["viewer"].canvas.path == str(destino)
        dialogo.descartar_todo()                           # _preguntar parcheado: «sí»
        assert guardado_ui.trabajos_pendientes(str(ajustes)) == []
        dlg.respuestas["cambios"] = "descartar"
        w2.close()
    finally:
        widgets.ctx.settings = anterior
        widgets.ctx.window = win


def test_pagina_respaldo_y_recuperacion(win, dlg, tmp_path, monkeypatch):
    from ronypdf import guardado_ui
    from ronypdf.widgets import ctx
    src = _pdf(tmp_path / "lunes.pdf")
    original = src.read_bytes()
    carpeta = guardado.carpeta_escritura(guardado.CARPETA_RESPALDOS, guardado_ui.ajustes_path())
    guardado.respaldar(src, carpeta, ahora=dt.datetime.now() - dt.timedelta(days=1))
    v = _abrir_visor(win, src)                            # la persona cambia el archivo y lo guarda
    v.canvas.add_text(v.canvas.pages[0].pos() + QPointF(72, 300), "Cambio del martes")
    v.guardar()
    esperar()
    assert "Cambio del martes" in _texto(src)
    page = win.pages["backup"]
    # desde el menú «Más acciones» del visor
    accion = next(a for a in v.b_mas.menu().actions() if "Versiones anteriores" in a.text())
    accion.trigger()
    assert win.stack.currentWidget() is page and page._filtro == str(src)
    assert page.tabla.topLevelItemCount() == 2 and page.b_todas.isVisible()
    # restaurar la del lunes (la más vieja), reemplazando el archivo actual
    page.tabla.setCurrentItem(page.tabla.topLevelItem(1))
    dlg.respuestas["_preguntar"] = "reemplazar"
    page.restaurar()
    esperar()
    assert src.read_bytes() == original and v.canvas.path == str(src) and v.canvas.doc is not None
    assert len(guardado.respaldos_de(src, guardado_ui.carpetas_respaldos())) == 3     # se respaldó la actual
    assert any(x[0] == "done" and x[1] == "Versión restaurada" for x in dlg)
    # abrir la copia (en el visor) y «Guardar como…»
    page.tabla.setCurrentItem(page.tabla.topLevelItem(0))
    copia = page._seleccion()
    page.abrir_copia()
    assert win.pages["viewer"].canvas.path == str(copia.ruta)
    win.navigate("backup")
    page.tabla.setCurrentItem(page.tabla.topLevelItem(0))
    dlg.respuestas["_preguntar"] = "guardar_como"
    destino = tmp_path / "lunes_restaurado.pdf"
    monkeypatch.setattr(guardado_ui, "ask_save", lambda *a, **k: str(destino))
    page.restaurar()
    assert destino.exists()
    # eliminar una copia (la que está abierta en el visor no se puede: se explica) y borrar todas
    dlg.respuestas["_preguntar"] = "si"
    page.tabla.setCurrentItem(page.tabla.topLevelItem(0))
    page.eliminar()
    assert page.tabla.topLevelItemCount() == 3 and any(x[0] == "info" and "abierta" in x[1] for x in dlg)
    _abrir_visor(win, src)
    win.navigate("backup")
    page.tabla.setCurrentItem(page.tabla.topLevelItem(0))
    page.eliminar()
    assert page.tabla.topLevelItemCount() == 2
    page.filtrar(None)
    assert not page.b_todas.isVisible()
    page.borrar_todas()
    assert guardado.listar_respaldos(guardado_ui.carpetas_respaldos()) == [] and page.tabla.topLevelItemCount() == 0
    # opciones
    page.cmb_intervalo.setCurrentIndex(page.cmb_intervalo.findData(120))
    assert ctx.settings.get("autoguardado_segundos") == 120 and win._autoguardado.timer.interval() == 120_000
    page.sp_copias.setValue(3)
    page.sp_dias.setValue(10)
    assert guardado_ui.retencion() == guardado.Retencion(3, 10, 500)
    page.chk_confirmar.setChecked(False)
    assert ctx.settings.get("guardado_confirmar") is False
    page.chk_confirmar.setChecked(True)
    page.chk_respaldo.setChecked(False)
    assert ctx.settings.get("guardado_respaldo") is False
    page.chk_respaldo.setChecked(True)
    page.cmb_intervalo.setCurrentIndex(page.cmb_intervalo.findData(0))
    assert not win._autoguardado.timer.isActive()
    page.cmb_intervalo.setCurrentIndex(page.cmb_intervalo.findData(60))
    page.sp_copias.setValue(5)
    page.sp_dias.setValue(30)
    win._autoguardado.timer.stop()
    assert page.privacidad.text() == guardado.TEXTO_PRIVACIDAD and page.privacidad.isVisible()
    # accept_files sin diálogos y la lista de trabajo sin guardar
    n = len(dlg)
    page.accept_files([str(src)])
    assert len(dlg) == n and page.filtro_lb.text().startswith("Versiones anteriores de «lunes.pdf»")


def test_pagina_respaldo_tarjetas_y_1000_px(win, dlg, tmp_path):
    from ronypdf import guardado_ui
    src = _pdf(tmp_path / "Informe de gestión con un nombre bastante largo para la tarjeta.pdf")
    carpeta = guardado.carpeta_escritura(guardado.CARPETA_RESPALDOS, guardado_ui.ajustes_path())
    guardado.respaldar(src, carpeta)
    page = win.pages["backup"]
    win.resize(1000, 660)
    win.sidebar.set_collapsed(True, remember=False)
    try:
        win.navigate("backup")
        page.filtrar(None)
        for _ in range(5):
            QApplication.processEvents()
        assert page.minimumSizeHint().width() <= page.width()
        panel = page.panel_scroll
        assert panel.widget().minimumSizeHint().width() <= panel.viewport().width()     # el panel no se recorta
        assert page.tabla.topLevelItemCount() >= 1 and page.action.isEnabled() and page.b_abrir.isVisible()
        assert page.action.text() == "Restaurar versión" and page._chips.count() == 2   # copias y tamaño
        page.tabla.clearSelection()                          # sin copia elegida no hay nada que restaurar
        assert not page.action.isEnabled() and not page.b_abrir.isVisible()
        page.chk_respaldo.setChecked(False)                 # sin copias, no aplica cuántas guardar
        assert not page.caja_retencion.isVisibleTo(page)
        page.chk_respaldo.setChecked(True)
        assert page.caja_retencion.isVisibleTo(page)
    finally:
        guardado.borrar_respaldos(guardado_ui.carpetas_respaldos())
        page.actualizar()
        win.resize(1240, 820)


def test_todo_cabe_a_1000_px_con_el_menu_abierto(win, dlg, tmp_path):
    shots = os.environ.get("RONYPDF_SHOTS")
    src = _pdf(tmp_path / "Informe de gestión con un nombre bastante largo.pdf")
    for ancho, alto in ((1000, 660), (1240, 820)):
        win.resize(ancho, alto)
        v = _abrir_visor(win, src)
        v.canvas.add_text(v.canvas.pages[0].pos() + QPointF(72, 300), "Cambio")
        v.canvas.scene().clearFocus()
        win.sidebar.set_collapsed(False, remember=False)
        for _ in range(8):
            QApplication.processEvents()
        for barra in (v.header, v.toolbar, v.footer):
            assert barra.minimumSizeHint().width() <= v.width(), (ancho, barra.objectName())
        assert v.save_group.width() <= 150 and v.b_redo.isVisible()
        if shots:
            win.grab().save(str(Path(shots) / f"guardado-visor-{ancho}.png"))
        f = win.pages["forms"]
        win.navigate("forms")
        for _ in range(8):
            QApplication.processEvents()
        assert f.header.minimumSizeHint().width() <= f.width() or getattr(f, "_compacto", False)
        win.sidebar.set_collapsed(True, remember=False)
        dlg.respuestas["cambios"] = "descartar"
        win.navigate("viewer")
        v.load(str(src))
    win.resize(1240, 820)


def test_rendimiento_300_paginas(win, dlg, tmp_path):
    src = tmp_path / "grande.pdf"
    with pymupdf.open() as d:
        for i in range(300):
            p = d.new_page()
            p.insert_text((72, 100), f"Página {i + 1} de un documento largo", fontsize=14)
            for k in range(25):
                p.insert_text((72, 130 + k * 24), f"Renglón {k} con texto de relleno para que pese algo", fontsize=10)
        d.save(src, garbage=3, deflate=True)
    edits = [core.Edit("text", 150, lines=[(72, 780, "Nota en la página 151")])]
    t0 = time.perf_counter()
    core.apply_edits(src, tmp_path / "completo.pdf", edits)
    completo = time.perf_counter() - t0
    copia = tmp_path / "incremental.pdf"
    shutil.copyfile(src, copia)
    t0 = time.perf_counter()
    core.apply_edits(copia, copia, edits, incremental=True)
    incremental = time.perf_counter() - t0
    v = _abrir_visor(win, src)
    c = v.canvas
    c.add_text(c.pages[150].pos() + QPointF(72, 300), "Autoguardado de 300 páginas")
    g = win._autoguardado
    t0 = time.perf_counter()
    g.ahora()
    esperar()
    auto = time.perf_counter() - t0
    t0 = time.perf_counter()
    v.guardar()
    esperar()
    directo = time.perf_counter() - t0
    print(f"\n300 páginas: completo {completo:.2f} s · incremental {incremental:.2f} s · "
          f"autoguardado {auto:.2f} s (medido {g.duracion.get('viewer', 0):.2f} s) · guardar directo {directo:.2f} s")
    assert incremental < completo and auto < 10 and directo < 15
    assert "Autoguardado de 300 páginas" in _texto(src)


# ================================================================ cierre de la ronda 2 (regresiones)

def test_respaldo_se_quita_si_el_guardado_falla(tmp_path):
    """Cada «Guardar» que fallaba (p. ej. con el archivo abierto en otra pantalla) dejaba otra copia idéntica,
    que ocupaba uno de los 5 cupos de ese documento."""
    src = _pdf(tmp_path / "doc.pdf")
    carpeta = tmp_path / "respaldos"

    def falla(progress=None, path=None):
        raise core.UserError("No se pudo escribir «doc.pdf». ¿Está abierto en otro programa?")

    for _ in range(3):
        with pytest.raises(core.UserError):
            guardado.guardar_con_respaldo(falla, carpeta_respaldos=carpeta, path=str(src))
    assert guardado.listar_respaldos(carpeta) == []

    def cambia_y_falla(progress=None, path=None):          # si el archivo sí cambió, la copia se conserva
        Path(path).write_bytes(b"%PDF-1.4 a medias")
        raise core.UserError("falló después de escribir")

    with pytest.raises(core.UserError):
        guardado.guardar_con_respaldo(cambia_y_falla, carpeta_respaldos=carpeta, path=str(src))
    assert len(guardado.listar_respaldos(carpeta)) == 1


def test_todas_las_pantallas_con_cambios_saben_guardar_al_cerrar(win):
    """Al cerrar con cambios y elegir «Guardar», la ventana llama ``page.guardar(al_terminar=…)``: toda pantalla
    con ``has_unsaved`` debe aceptarlo («Revisar texto reconocido» no lo aceptaba y se perdía lo corregido)."""
    import inspect
    con_cambios = [k for k, p in win.pages.items() if callable(getattr(p, "has_unsaved", None))]
    assert {"viewer", "organize", "forms", "ocrcheck", "batch", "redact"} <= set(con_cambios)
    for clave in con_cambios:
        fn = getattr(win.pages[clave], "guardar", None)
        assert callable(fn), f"«{clave}» tiene cambios sin guardar pero no sabe guardar"
        assert "al_terminar" in inspect.signature(fn).parameters, clave


def test_guardar_y_esperar_nunca_deja_cerrar_si_la_pantalla_falla(win, dlg, monkeypatch):
    from PySide6.QtGui import QCloseEvent
    from PySide6.QtWidgets import QWidget
    from ronypdf import guardado_ui
    avisos = []
    monkeypatch.setattr(guardado_ui, "no_se_pudo_guardar", lambda p, detalle=None: avisos.append(detalle))

    class Vieja(QWidget):                       # guardar() sin al_terminar, como estaba «Revisar texto reconocido»
        def guardar(self):
            pass

    class Muda(QWidget):                        # no guarda ni avisa nada: no debe colgar la aplicación
        def guardar(self, al_terminar=None):
            pass

    assert not guardado_ui.guardar_y_esperar(Vieja())
    assert "TypeError" in avisos[-1]
    assert not guardado_ui.guardar_y_esperar(QWidget())                 # sin guardar(): explica qué hacer
    assert len(avisos) == 2
    assert not guardado_ui.guardar_y_esperar(Muda())
    # en la ventana: «Guardar» en una pantalla cuyo guardar() falla no cierra (antes se cerraba igual)
    page = win.pages["batch"]
    monkeypatch.setattr(page, "has_unsaved", lambda: True)
    monkeypatch.setattr(page, "guardar", lambda: None)
    monkeypatch.setattr(guardado_ui, "preguntar_cambios",
                        lambda p, t, x: "guardar" if win.stack.currentWidget() is page else "descartar")
    g = win._autoguardado
    cerrar = g.cerrar
    g.cerrar = lambda: None
    try:
        e = QCloseEvent()
        win.closeEvent(e)
    finally:
        g.cerrar = cerrar
    assert not e.isAccepted() and "TypeError" in avisos[-1]


def test_cerrar_con_un_flujo_de_lotes_sin_guardar(win, dlg, monkeypatch):
    """En «Acciones por lotes», «Guardar» al cerrar no hacía nada (ni guardaba ni decía por qué)."""
    from PySide6.QtGui import QCloseEvent
    from ronypdf import guardado_ui, lotes, lotes_ui
    monkeypatch.setattr(lotes_ui, "confirmar", lambda *a, **k: True)
    page = win.pages["batch"]
    win.navigate("batch")
    page._nuevo_lote()
    page.combo.setCurrentIndex(0)                               # flujo nuevo
    page._agregar_paso("comprimir")
    assert page.has_unsaved()
    monkeypatch.setattr(lotes_ui, "pedir_nombre_flujo", lambda *a: None)       # cancela el nombre
    assert not guardado_ui.guardar_y_esperar(page) and page.has_unsaved()
    nombre = f"Flujo guardado al cerrar {time.time_ns()}"
    monkeypatch.setattr(lotes_ui, "pedir_nombre_flujo", lambda *a: nombre)
    monkeypatch.setattr(guardado_ui, "preguntar_cambios",
                        lambda p, t, x: "guardar" if win.stack.currentWidget() is page else "descartar")
    g = win._autoguardado
    cerrar = g.cerrar
    g.cerrar = lambda: None
    try:
        e = QCloseEvent()
        win.closeEvent(e)
    finally:
        g.cerrar = cerrar
    try:
        assert e.isAccepted() and not page.has_unsaved()
        assert (lotes.carpeta_flujos() / f"{nombre}.json").is_file()
    finally:
        lotes.borrar_flujo(nombre)
    # «Censurar datos» también sabe guardar desde la pregunta (sin documento: no hay nada que guardar)
    res = []
    win.pages["redact"].guardar(al_terminar=res.append)
    assert res == [False]


def test_guardar_con_el_mismo_pdf_abierto_en_otra_pantalla(win, dlg, tmp_path, monkeypatch):
    """«Organizar páginas» desde el visor deja el archivo abierto en los dos: el «Guardar» directo fallaba con
    «¿Está abierto en otro programa?» y cada intento dejaba otra copia de respaldo."""
    from ronypdf import guardado_ui
    src = _pdf(tmp_path / "carta.pdf")
    v = _abrir_visor(win, src)
    o = win.pages["organize"]
    win.navigate("organize")
    o.load_files([str(src)])
    assert v.canvas.doc is not None and v.canvas.path == str(src)
    o.grid.clearSelection()
    o.grid.item(2).setSelected(True)
    o.delete()
    o.guardar()
    esperar()
    assert not [x for x in dlg if x[0] == "warning"], dlg
    with pymupdf.open(src) as d:
        assert d.page_count == 2
    assert not o.has_unsaved() and o.grid.count() == 2
    assert v.canvas.doc is not None and v.canvas.page_count == 2             # el visor lo volvió a abrir
    assert len(guardado.respaldos_de(src, _carpeta_respaldos())) == 1
    # el visor guarda con el archivo abierto en el organizador
    win.navigate("viewer")
    v.canvas.add_text(v.canvas.pages[0].pos() + QPointF(72, 300), "Desde el visor")
    v.canvas.scene().clearFocus()
    v.guardar()
    esperar()
    assert not [x for x in dlg if x[0] == "warning"], dlg
    assert "Desde el visor" in _texto(src) and not v.has_unsaved()
    assert o.grid.count() == 2 and not o.has_unsaved() and all(s.doc is None or not s.doc.is_closed for s in o.sources)
    assert len(guardado.respaldos_de(src, _carpeta_respaldos())) == 2
    # si la otra pantalla tiene cambios sin guardar, no se guarda encima ni queda otra copia
    o.grid.clearSelection()
    o.grid.item(0).setSelected(True)
    o.rotate(90)
    v.canvas.add_text(v.canvas.pages[1].pos() + QPointF(72, 300), "Otra vez")
    v.canvas.scene().clearFocus()
    avisos = []
    monkeypatch.setattr(guardado_ui, "_preguntar",
                        lambda p, titulo, texto, *a, **k: avisos.append(texto) or ("ok", False))
    antes = src.read_bytes()
    v.guardar()
    esperar()
    assert src.read_bytes() == antes and v.has_unsaved() and o.has_unsaved()
    assert any("también está abierto en «Organizar páginas»" in a for a in avisos), avisos
    assert len(guardado.respaldos_de(src, _carpeta_respaldos())) == 2 and v.canvas.doc is not None
    # formularios con el archivo abierto en el visor
    dlg.respuestas["cambios"] = "descartar"
    o.load_files([str(_pdf(tmp_path / "otro.pdf"))])
    form = tmp_path / "form.pdf"
    with pymupdf.open() as d:
        p = d.new_page()
        w = pymupdf.Widget()
        w.field_name, w.field_type, w.rect = "nombre", pymupdf.PDF_WIDGET_TYPE_TEXT, pymupdf.Rect(100, 100, 300, 120)
        p.add_widget(w)
        d.save(form)
    v.load(str(form), confirm=False)
    f = win.pages["forms"]
    win.navigate("forms")
    f.load(str(form), confirm=False)
    f.set_valor(f.navegables[0], "Ana Pérez")
    f.guardar()
    esperar()
    assert not f.has_unsaved() and formularios.leer_campos(form)[0].valor == "Ana Pérez"
    assert v.canvas.doc is not None and v.canvas.path == str(form)
