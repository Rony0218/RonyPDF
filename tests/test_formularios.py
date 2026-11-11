"""Formularios y firma: lógica (leer, rellenar, aplanar, datos), firmas guardadas y pantalla sin pantalla."""

import datetime
import hashlib
import os
import tempfile
import time

import pymupdf
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
# Los ajustes (y la carpeta de firmas, que va junto a ellos) nunca deben tocar los reales.
os.environ.setdefault("RONYPDF_SETTINGS", os.path.join(tempfile.mkdtemp(prefix="ronypdf-"), "RonyPDF.json"))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ronypdf import core, extensiones, firmas, widgets  # noqa: E402
from ronypdf import formularios as F  # noqa: E402

T = pymupdf


# ---------------------------------------------------------------- formularios de prueba

def _widget(page, tipo, nombre, rect, **kw):
    w = pymupdf.Widget()
    w.field_type = tipo
    w.field_name = nombre
    w.rect = pymupdf.Rect(rect)
    for k, v in kw.items():
        setattr(w, k, v)
    page.add_widget(w)


def _grupo_opciones(doc, page, nombre, opciones):
    """Botones de opción construidos a bajo nivel (los de page.add_widget salen sin estado «encendido»).

    ``opciones`` = [(estado, rect en coordenadas de PyMuPDF)].
    """
    def forma(contenido: bytes) -> int:
        x = doc.get_new_xref()
        doc.update_object(x, "<< /Type /XObject /Subtype /Form /BBox [0 0 14 14] /Resources << >> >>")
        doc.update_stream(x, contenido)
        return x

    apagado = forma(b"")
    encendido = forma(b"q 0 0 0 rg 3 3 8 8 re f Q")
    padre = doc.get_new_xref()
    alto = page.mediabox.height
    hijos = []
    for estado, r in opciones:
        k = doc.get_new_xref()
        r = pymupdf.Rect(r)
        doc.update_object(k, f"<< /Type /Annot /Subtype /Widget /Rect [{r.x0} {alto - r.y1} {r.x1} {alto - r.y0}] "
                             f"/F 4 /P {page.xref} 0 R /Parent {padre} 0 R /AS /Off /MK << /CA (l) >> "
                             f"/AP << /N << /{estado} {encendido} 0 R /Off {apagado} 0 R >> >> >>")
        hijos.append(k)
    doc.update_object(padre, f"<< /FT /Btn /Ff 49152 /T ({nombre}) /V /Off "
                             f"/Kids [{' '.join(f'{k} 0 R' for k in hijos)}] >>")
    t, v = doc.xref_get_key(page.xref, "Annots")
    actuales = v[1:-1] if t == "array" else ""
    doc.xref_set_key(page.xref, "Annots", f"[{actuales} {' '.join(f'{k} 0 R' for k in hijos)}]")
    cat = doc.pdf_catalog()
    campos = doc.xref_get_key(cat, "AcroForm/Fields")[1][1:-1]
    doc.xref_set_key(cat, "AcroForm/Fields", f"[{campos} {padre} 0 R]")


@pytest.fixture(scope="module")
def formulario(tmp_path_factory):
    """Todos los tipos de campo. La página 2 está girada 90° y repite el campo «nombre»."""
    path = tmp_path_factory.mktemp("formularios") / "solicitud.pdf"
    doc = pymupdf.open()
    p = doc.new_page(width=595, height=842)
    p.insert_text((72, 60), "Solicitud de apoyo", fontsize=16)
    _widget(p, T.PDF_WIDGET_TYPE_TEXT, "nombre", (72, 72, 300, 92), field_label="Nombre completo",
            field_flags=T.PDF_FIELD_IS_REQUIRED)
    _widget(p, T.PDF_WIDGET_TYPE_TEXT, "observaciones", (72, 100, 300, 160), field_flags=T.PDF_TX_FIELD_IS_MULTILINE)
    _widget(p, T.PDF_WIDGET_TYPE_TEXT, "radicado", (72, 170, 300, 190), text_maxlen=10,
            field_flags=T.PDF_FIELD_IS_READ_ONLY, field_value="R-123")
    _widget(p, T.PDF_WIDGET_TYPE_CHECKBOX, "acepto", (72, 200, 86, 214), field_value=False)
    _widget(p, T.PDF_WIDGET_TYPE_LISTBOX, "ciudad", (72, 220, 200, 280),
            choice_values=["Bogotá", "Medellín", "Cali"], field_value="Cali")
    _widget(p, T.PDF_WIDGET_TYPE_COMBOBOX, "depto", (72, 290, 200, 310),
            choice_values=[["ANT", "Antioquia"], ["CUN", "Cundinamarca"]], field_value="ANT")
    _widget(p, T.PDF_WIDGET_TYPE_SIGNATURE, "firma", (72, 330, 250, 380))
    _widget(p, T.PDF_WIDGET_TYPE_TEXT, "telefono", (320, 72, 500, 92), text_maxlen=10)
    _grupo_opciones(doc, p, "tipo", [("Natural", (72, 400, 86, 414)), ("Juridica", (160, 400, 174, 414))])
    p2 = doc.new_page(width=595, height=842)
    p2.set_rotation(90)
    _widget(p2, T.PDF_WIDGET_TYPE_TEXT, "nombre", (72, 100, 300, 120))
    _widget(p2, T.PDF_WIDGET_TYPE_LISTBOX, "apoyos", (72, 140, 220, 200), choice_values=["Kits", "Becas", "Niñez"],
            field_flags=T.PDF_CH_FIELD_IS_MULTI_SELECT)
    doc.save(path)
    return path


def _ids(path, password=None):
    out = {}
    for c in F.leer_campos(path, password):
        out.setdefault(c.nombre, []).append(c)
    return out


def _sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


# ---------------------------------------------------------------- lógica

def test_leer_campos_tipos_banderas_y_rect(formulario):
    campos = F.leer_campos(formulario)
    por = _ids(formulario)
    assert [c.tipo for c in campos[:7]] == ["texto", "texto", "texto", "casilla", "lista", "desplegable", "firma"]
    nombre = por["nombre"][0]
    assert nombre.etiqueta == "Nombre completo" and nombre.titulo == "Nombre completo"
    assert nombre.obligatorio and not nombre.solo_lectura and nombre.pagina == 0
    assert nombre.rect == (72, 72, 300, 92) and nombre.id.startswith("0:")
    assert por["observaciones"][0].multilinea
    rad = por["radicado"][0]
    assert rad.solo_lectura and rad.max_largo == 10 and rad.valor == "R-123"
    assert por["acepto"][0].valor is False and por["acepto"][0].estado == "Yes"
    assert por["ciudad"][0].opciones == ["Bogotá", "Medellín", "Cali"] and por["ciudad"][0].valor == "Cali"
    depto = por["depto"][0]
    assert depto.opciones == ["Antioquia", "Cundinamarca"] and depto.valores_opciones == ["ANT", "CUN"]
    assert depto.valor == "ANT" and depto.texto_de("CUN") == "Cundinamarca"
    assert por["firma"][0].tipo == "firma" and not por["firma"][0].firmado
    tipo = por["tipo"]
    assert [c.tipo for c in tipo] == ["opcion", "opcion"] and [c.estado for c in tipo] == ["Natural", "Juridica"]
    assert not any(c.valor for c in tipo)
    # página girada 90°: el rectángulo viene en coordenadas visibles
    n2 = por["nombre"][1]
    assert n2.pagina == 1
    with pymupdf.open(formulario) as d:
        page = d[1]
        esperado = pymupdf.Rect(72, 100, 300, 120) * page.rotation_matrix
        assert pymupdf.Rect(n2.rect) == esperado
        assert page.rect.contains(pymupdf.Rect(n2.rect))
        assert pymupdf.Rect(n2.rect).height > pymupdf.Rect(n2.rect).width     # el campo se ve en vertical
    assert por["apoyos"][0].multiple and por["apoyos"][0].valor == []
    info = F.info_formulario(formulario)
    assert info.campos >= 10 and not info.xfa and not info.firmado and not info.calculos


def test_rellenar_ida_y_vuelta_con_tildes(formulario, tmp_path):
    por = _ids(formulario)
    valores = {
        por["nombre"][0].id: "José Ñoño Peña",
        por["observaciones"][0].id: "Primera línea\nSegunda: niñez y educación",
        por["acepto"][0].id: True,
        por["ciudad"][0].id: "Medellín",
        por["depto"][0].id: "CUN",
        por["telefono"][0].id: "3001234567999",          # más largo que el máximo de 10
        por["tipo"][1].id: True,
        por["apoyos"][0].id: ["Becas", "Niñez"],
        por["radicado"][0].id: "no se cambia",           # solo lectura
    }
    out = tmp_path / "rellenado.pdf"
    n, m = F.rellenar(formulario, out, valores)
    assert (n, m) == (8, 0)
    leidos = _ids(out)
    assert [c.valor for c in leidos["nombre"]] == ["José Ñoño Peña"] * 2      # mismo nombre: sincronizados
    assert leidos["observaciones"][0].valor == "Primera línea\nSegunda: niñez y educación"
    assert leidos["acepto"][0].valor is True
    assert leidos["ciudad"][0].valor == "Medellín"
    assert leidos["depto"][0].valor == "CUN"                                  # se guarda el valor…
    assert leidos["telefono"][0].valor == "3001234567"
    assert [c.valor for c in leidos["tipo"]] == [False, True]
    assert leidos["apoyos"][0].valor == ["Becas", "Niñez"]
    assert leidos["radicado"][0].valor == "R-123"
    with pymupdf.open(out) as d:
        texto = d[0].get_text()
        assert "José Ñoño Peña" in texto and "Cundinamarca" in texto           # …y se ve el texto
        assert "Medellín" in texto and "Niñez" in d[1].get_text()
    # cambiar de opción desmarca la anterior; desmarcar la casilla la apaga
    leidos = _ids(out)
    out2 = tmp_path / "rellenado2.pdf"
    F.rellenar(out, out2, {leidos["tipo"][0].id: True, leidos["acepto"][0].id: False})
    l2 = _ids(out2)
    assert [c.valor for c in l2["tipo"]] == [True, False] and l2["acepto"][0].valor is False
    with pymupdf.open(out2) as d:
        hijo = int(l2["tipo"][0].id.split(":")[1])
        padre = int(d.xref_get_key(hijo, "Parent")[1].split()[0])
        assert d.xref_get_key(padre, "V")[1] == "/Natural"
    # el campo de la página girada sigue en su sitio
    assert l2["nombre"][1].rect == _ids(formulario)["nombre"][1].rect


def test_rellenar_y_aplanar(formulario, tmp_path):
    por = _ids(formulario)
    out = tmp_path / "plano.pdf"
    F.rellenar(formulario, out, {por["nombre"][0].id: "Fundación Ñandú", por["depto"][0].id: "CUN"}, aplanar=True)
    with pymupdf.open(out) as d:
        assert not d.is_form_pdf and d.xref_get_key(d.pdf_catalog(), "AcroForm")[0] == "null"
        assert all(not list(p.widgets()) for p in d)
        texto = d[0].get_text()
        assert "Fundación Ñandú" in texto and "Cundinamarca" in texto
        assert d[0].search_for("Fundación Ñandú")
    assert F.leer_campos(out) == []
    with pytest.raises(core.UserError):
        F.aplanar(out, tmp_path / "otra.pdf")
    assert F.aplanar(formulario, tmp_path / "plano2.pdf") >= 10


def test_rellenar_con_sellos(formulario, scanned, tmp_path):
    png = firmas.firma_desde_trazos([[(0, 20), (20, 0), (40, 30), (70, 5)]])
    sellos = [
        core.Edit("text", 0, lines=[(320, 500, "Recibido: niñez")], fontsize=11),
        [core.Edit("line", 0, points=[(100, 600), (112, 612)]), core.Edit("line", 0, points=[(100, 612), (112, 600)])],
        core.Edit("ink", 0, points=[(150, 607), (155, 612), (162, 600)]),
        core.Edit("image", 0, rect=(80, 335, 240, 375), image=png),
        core.Edit("text", 1, lines=[(50, 60, "Girada")], fontsize=12),
    ]
    out = tmp_path / "sellado.pdf"
    n, m = F.rellenar(formulario, out, {}, sellos=sellos)
    assert (n, m) == (0, 5)
    with pymupdf.open(out) as d:
        assert "Recibido: niñez" in d[0].get_text() and "Recibido" not in d[1].get_text()
        assert len(d[0].get_images()) == 1 and not d[1].get_images()
        assert len(d[0].get_drawings()) >= 3
        # el campo de firma vacío que quedó bajo la firma en imagen se quita (nada la tapa)
        assert not [w for w in d[0].widgets() if w.field_type == pymupdf.PDF_WIDGET_TYPE_SIGNATURE]
        assert "SIGN" not in d[0].get_text() and len(list(d[0].widgets())) == 9
        # página girada: el texto queda donde se ve, y los campos siguen ahí
        page = d[1]
        r = next(pymupdf.Rect(w[:4]) for w in page.get_text("words") if w[4] == "Girada") * page.rotation_matrix
        assert abs(r.x0 - 50) < 2 and abs(r.y1 - 60) < 6
        assert len(list(page.widgets())) == 2
    # también sobre un PDF escaneado sin campos
    out2 = tmp_path / "escaneado_firmado.pdf"
    assert F.rellenar(scanned, out2, {}, sellos=[core.Edit("image", 0, rect=(300, 700, 460, 740), image=png)]) == (0, 1)
    with pymupdf.open(out2) as d:
        assert len(d[0].get_images()) == 2
    with pytest.raises(core.UserError):
        F.rellenar(scanned, tmp_path / "nada.pdf", {})


def _cifrado_completo(formulario, path):
    """El formulario con contraseña, un adjunto y una imagen en la página 2 (para ver que nada se pierde)."""
    with pymupdf.open(formulario) as d:
        d[1].insert_text((72, 400), "Anexo: datos del grupo familiar", fontsize=12)
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 40), False)
        pix.set_rect(pix.irect, (30, 120, 200))
        d[1].insert_image(pymupdf.Rect(72, 420, 192, 500), pixmap=pix)
        d.embfile_add("soporte.txt", b"cedula 1032456789", filename="soporte.txt")
        d.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="1234", owner_pw="dueño")
    return path


def _sigue_completo(path):
    """Los rótulos fijos, la imagen y el adjunto siguen después de guardar (antes salían en blanco)."""
    with core.open_pdf(path, "1234") as d:
        assert "Solicitud de apoyo" in d[0].get_text()
        assert "Anexo: datos del grupo familiar" in d[1].get_text()
        assert len(d[1].get_images()) == 1
        assert len(d.extract_image(d[1].get_images()[0][0])["image"]) > 50
        assert d.embfile_get(0) == b"cedula 1032456789"


def test_formulario_con_contrasena(formulario, tmp_path):
    cifrado = _cifrado_completo(formulario, tmp_path / "cifrado.pdf")
    antes, tam = _sha(cifrado), os.path.getsize(cifrado)
    with pytest.raises(core.PasswordError):
        F.leer_campos(cifrado)
    por = _ids(cifrado, "1234")
    out = tmp_path / "cifrado_rellenado.pdf"
    F.rellenar(cifrado, out, {por["nombre"][0].id: "Ana"}, password="1234")
    assert core.needs_password(out) and core.check_password(out, "1234") and not core.check_password(out, "x")
    assert _ids(out, "1234")["nombre"][0].valor == "Ana"
    _sigue_completo(out)
    assert _sha(cifrado) == antes and os.path.getsize(cifrado) == tam       # el original no cambia
    # aplanar y vaciar con contraseña tampoco dejan la página en blanco
    plano = tmp_path / "cifrado_plano.pdf"
    F.aplanar(out, plano, password="1234")
    assert core.check_password(plano, "1234") and core.needs_password(plano)
    _sigue_completo(plano)
    with core.open_pdf(plano, "1234") as d:
        assert "Ana" in d[0].get_text() and not d.is_form_pdf
    vacio = tmp_path / "cifrado_vacio.pdf"
    F.vaciar(out, vacio, password="1234")
    _sigue_completo(vacio)


def test_vaciar_exportar_importar(formulario, tmp_path):
    por = _ids(formulario)
    lleno = tmp_path / "lleno.pdf"
    F.rellenar(formulario, lleno, {por["nombre"][0].id: "María", por["observaciones"][0].id: "Uno\nDos ñ",
                                   por["acepto"][0].id: True, por["ciudad"][0].id: "Bogotá",
                                   por["depto"][0].id: "CUN", por["tipo"][0].id: True,
                                   por["apoyos"][0].id: ["Kits"]})
    esperado = F.datos_de(F.leer_campos(lleno))
    assert esperado["tipo"] == "Natural" and esperado["acepto"] is True and esperado["apoyos"] == ["Kits"]
    vacio = tmp_path / "vacio.pdf"
    assert F.vaciar(lleno, vacio) == 7                                 # los que tenían algo
    v = F.datos_de(F.leer_campos(vacio))
    assert v["nombre"] == "" and v["observaciones"] == "" and v["acepto"] is False and v["tipo"] == ""
    assert v["ciudad"] == "" and v["depto"] == "" and v["apoyos"] == [] and v["radicado"] == "R-123"
    for ext in ("json", "csv"):
        datos = tmp_path / f"datos.{ext}"
        assert F.exportar_datos(lleno, datos) == len(esperado)
        leidos = F.importar_datos(datos)
        leidos["no_existe"] = "x"
        valores, aplicados, ignorados = F.valores_desde_datos(F.leer_campos(vacio), leidos)
        assert ignorados == 2 and aplicados == len(esperado) - 1          # «radicado» es de solo lectura
        out = tmp_path / f"importado_{ext}.pdf"
        F.rellenar(vacio, out, valores)
        assert F.datos_de(F.leer_campos(out)) == esperado
    assert (tmp_path / "datos.csv").read_text(encoding="utf-8-sig").splitlines()[0] == "campo;valor"
    with pytest.raises(core.UserError):
        F.exportar_datos(lleno, tmp_path / "datos.txt")
    (tmp_path / "malo.json").write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(core.UserError):
        F.importar_datos(tmp_path / "malo.json")


def test_info_formulario_xfa_firma_y_calculos(formulario, tmp_path):
    with pymupdf.open(formulario) as d:
        cat = d.pdf_catalog()
        x = d.get_new_xref()
        d.update_object(x, "<< >>")
        d.update_stream(x, b"<xdp:xdp xmlns:xdp='http://ns.adobe.com/xdp/'></xdp:xdp>")
        d.xref_set_key(cat, "AcroForm/XFA", f"{x} 0 R")
        d.xref_set_key(cat, "AcroForm/CO", "[]")
        d.save(tmp_path / "xfa.pdf")
    info = F.info_formulario(tmp_path / "xfa.pdf")
    assert info.xfa and not info.calculos and not info.firmado
    with pymupdf.open(formulario) as d:
        cat = d.pdf_catalog()
        tel = next(w.xref for w in d[0].widgets() if w.field_name == "telefono")
        d.xref_set_key(cat, "AcroForm/CO", f"[{tel} 0 R]")
        firma = next(w.xref for w in d[0].widgets() if w.field_type == pymupdf.PDF_WIDGET_TYPE_SIGNATURE)
        d.xref_set_key(firma, "V", "<< /Type /Sig /Filter /Adobe.PPKLite /SubFilter /adbe.pkcs7.detached "
                                   "/ByteRange [0 0 0 0] /Contents <00> >>")
        d.xref_set_key(cat, "AcroForm/SigFlags", "3")
        d.save(tmp_path / "firmado.pdf")
    info = F.info_formulario(tmp_path / "firmado.pdf")
    assert info.calculos and info.firmado and not info.xfa
    assert next(c for c in F.leer_campos(tmp_path / "firmado.pdf") if c.tipo == "firma").firmado


# ---------------------------------------------------------------- firmas

@pytest.fixture(scope="module")
def app_qt():
    return QApplication.instance() or QApplication([])


def _transparente(png: bytes) -> QImage:
    img = QImage.fromData(png)
    assert not img.isNull() and img.hasAlphaChannel()
    assert img.pixelColor(0, 0).alpha() == 0                      # fondo transparente
    opacos = sum(img.pixelColor(x, img.height() // 2).alpha() > 200 for x in range(img.width()))
    assert opacos > 0                                             # y algo de tinta
    return img


def test_firmas_crear_y_guardar(app_qt, tmp_path):
    img = _transparente(firmas.firma_desde_trazos([[(10, 50), (30, 20), (50, 60), (80, 30)], [(60, 80), (140, 75)]]))
    assert img.width() > img.height()
    with pytest.raises(core.UserError):
        firmas.firma_desde_trazos([])
    _transparente(firmas.firma_desde_texto("María Ñoño", "Segoe Script"))
    with pytest.raises(core.UserError):
        firmas.firma_desde_texto("  ", "Segoe Script")
    assert set(firmas.fuentes_disponibles()) <= set(firmas.FUENTES_MANUSCRITAS)
    # foto con el papel grisáceo: el fondo se vuelve transparente
    with pymupdf.open() as d:
        p = d.new_page(width=300, height=150)
        p.draw_rect(p.rect, color=None, fill=(0.93, 0.93, 0.9))
        p.insert_text((40, 95), "Firma", fontsize=52, fontname="tiit", color=(0.1, 0.15, 0.45))
        p.get_pixmap(dpi=150).save(tmp_path / "foto.jpg")
    img = _transparente(firmas.firma_desde_imagen(tmp_path / "foto.jpg"))
    assert img.width() < 600                                       # recortada a la tinta
    plano = QImage.fromData(firmas.firma_desde_imagen(tmp_path / "foto.jpg", quitar_fondo=False))
    assert plano.pixelColor(0, 0).alpha() == 255
    with pytest.raises(core.UserError):
        firmas.firma_desde_imagen(tmp_path / "no_existe.png")

    almacen = firmas.AlmacenFirmas(tmp_path / "RonyPDF_firmas")
    assert almacen.listar() == []
    png = firmas.firma_desde_texto("Ana", "Ink Free")
    guardadas = []
    for _ in range(firmas.MAX_FIRMAS):
        guardadas.append(almacen.guardar(png))
    assert len(almacen.listar()) == firmas.MAX_FIRMAS and almacen.lleno()
    with pytest.raises(core.UserError):
        almacen.guardar(png)
    almacen.borrar(guardadas[0])
    assert len(almacen.listar()) == firmas.MAX_FIRMAS - 1 and not guardadas[0].exists()
    with pytest.raises(core.UserError):
        almacen.borrar(tmp_path / "foto.jpg")                     # fuera de la carpeta: no se toca
    assert (tmp_path / "foto.jpg").exists()
    with pytest.raises(core.UserError):
        almacen.guardar(b"no es png")
    # carpeta por defecto: junto al archivo de ajustes
    from ronypdf.settings import Settings
    assert firmas.AlmacenFirmas().carpeta.name == firmas.CARPETA
    assert firmas.AlmacenFirmas().carpeta.parent == (widgets.ctx.settings.path.parent if widgets.ctx.settings
                                                     else Settings().path.parent)


def test_dialogo_firma_sin_exec(app_qt, tmp_path, monkeypatch):
    dlg = firmas.DialogoFirma()
    assert firmas.AVISO_PRIVACIDAD.startswith("Tus firmas se guardan solo en este equipo")
    assert dlg.chk_guardar.isChecked() and [dlg.tabs.tabText(i) for i in range(3)] == ["Dibujar", "Escribir", "Imagen"]
    with pytest.raises(core.UserError):
        dlg.crear_png()
    dlg.panel.trazos = [[(10, 10), (60, 40), (120, 20)]]
    _transparente(dlg.crear_png())
    dlg.panel.borrar()
    assert dlg.panel.vacio()
    if firmas.fuentes_disponibles():
        dlg.tabs.setCurrentIndex(1)
        dlg.nombre.setText("Ana Peña")
        _transparente(dlg.crear_png())
    with pymupdf.open() as d:
        p = d.new_page(width=200, height=80)
        p.insert_text((20, 50), "Ana", fontsize=30)
        p.get_pixmap(dpi=100).save(tmp_path / "f.png")
    dlg.tabs.setCurrentIndex(2)
    dlg.cargar_imagen(str(tmp_path / "f.png"))
    assert dlg.vista.img is not None
    dlg._aceptar()
    assert dlg.png and dlg.guardar
    # administrar: borrar con confirmación
    almacen = firmas.AlmacenFirmas(tmp_path / "firmas")
    almacen.guardar(dlg.png)
    adm = firmas.DialogoAdministrar(almacen)
    assert adm.lista.count() == 1
    adm.lista.item(0).setSelected(True)
    monkeypatch.setattr(firmas.QMessageBox, "question", lambda *a, **k: firmas.QMessageBox.StandardButton.Yes)
    adm.borrar_seleccionada()
    assert almacen.listar() == []


# ---------------------------------------------------------------- interfaz

from ronypdf import formularios_ui as fui  # noqa: E402


@pytest.fixture(scope="module")
def win():
    app = QApplication.instance() or QApplication([])
    from ronypdf.gui import MainWindow, _light_palette
    from ronypdf.style import QSS
    app.setStyle("Fusion")
    app.setPalette(_light_palette())
    app.setStyleSheet(QSS)
    w = MainWindow()
    w.resize(1240, 820)
    w.show()
    app.processEvents()
    yield w
    w.hide()


@pytest.fixture
def done(monkeypatch, tmp_path):
    calls = []

    def fake_done(parent, title, detail="", file=None, folder=None):
        calls.append((title, detail, file, folder))

    from ronypdf import viewer
    for mod in (fui, widgets, viewer):
        monkeypatch.setattr(mod, "show_done", fake_done, raising=False)
    monkeypatch.setattr(widgets.QMessageBox, "warning", lambda *a, **k: calls.append(("warning", a[2])))
    monkeypatch.setattr(widgets.QMessageBox, "information", lambda *a, **k: calls.append(("info", a[2])))
    monkeypatch.setattr(widgets.QMessageBox, "question", lambda *a, **k: widgets.QMessageBox.StandardButton.Yes)
    from ronypdf import guardado_ui      # «Guardar / No guardar / Cancelar»: «No guardar» (antes, «Sí» descartaba)
    monkeypatch.setattr(guardado_ui, "preguntar_cambios", lambda *a, **k: "descartar")
    # la carpeta de firmas de las pruebas, nunca la real
    monkeypatch.setattr(fui.FormsPage, "almacen", lambda self: firmas.AlmacenFirmas(tmp_path / "RonyPDF_firmas"))
    return calls


def wait(timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        QApplication.processEvents()
        if not widgets.ctx.busy:
            QApplication.processEvents()
            return
        time.sleep(0.01)
    raise TimeoutError


def _escribir(widget, texto):
    """Teclea en un widget. (QTest.keyClicks se cae en PySide6 con letras como «ñ» o «í».)"""
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent
    for ch in texto:
        key = ord(ch.upper()) if ch.isascii() else 0
        for tipo in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            QApplication.sendEvent(widget, QKeyEvent(tipo, key, Qt.KeyboardModifier.NoModifier, ch))
    QApplication.processEvents()


def _clic(canvas, punto_escena):
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent
    vp = canvas.viewport()
    local = QPointF(canvas.mapFromScene(punto_escena))
    for tipo, botones in ((QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton),
                          (QEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton)):
        QApplication.sendEvent(vp, QMouseEvent(tipo, local, vp.mapToGlobal(local), Qt.MouseButton.LeftButton,
                                               botones, Qt.KeyboardModifier.NoModifier))
    QApplication.processEvents()


def _abrir(win, path):
    page = win.pages["forms"]
    win.navigate("forms")
    assert page.load(str(path), confirm=False)
    QApplication.processEvents()
    page.canvas.render_visible()
    return page


def test_registro_menu_inicio_y_aviso_del_visor(win, formulario, samples, done):
    assert isinstance(win.pages["forms"], fui.FormsPage)
    sb = win.sidebar
    assert "forms" in sb.items and sb.page_section["forms"] == "ver"
    sb.search.setText("firma")                                   # búsqueda Ctrl K
    assert not sb.items["forms"].isHidden() and sb.items["merge"].isHidden()
    sb.search.clear()
    assert ("forms", "forms", "Formularios y firma", "Rellena y firma sin imprimir") in extensiones.inicio()
    from PySide6.QtWidgets import QWidget
    from ronypdf import art, icons
    assert any(w.accessibleName() == "Formularios y firma" for w in win.home.findChildren(QWidget))
    assert icons.EXTRA["forms"] is fui.dibujo and "forms" in art.EXTRA          # ícono de línea propio
    viewer = win.pages["viewer"]
    win.navigate("viewer")
    viewer.load(str(samples["a"]), confirm=False)
    assert fui.aviso_visor(viewer) is None and not viewer.ext_avisos.isVisibleTo(viewer)
    viewer.load(str(formulario), confirm=False)
    texto, boton, accion = fui.aviso_visor(viewer)
    assert "formulario" in texto and boton == "Rellenar formulario"
    assert viewer.ext_avisos.isVisibleTo(viewer)
    accion()
    page = win.pages["forms"]
    assert win.stack.currentWidget() is page and page.path == str(formulario)
    assert page.stack.currentIndex() == 1 and page.canvas.page_count == 2


def test_ui_rellenar_con_teclado_y_guardar(win, formulario, done, tmp_path, monkeypatch):
    from PySide6.QtTest import QTest
    src = tmp_path / "solicitud.pdf"
    src.write_bytes(formulario.read_bytes())
    page = _abrir(win, src)
    assert not page.has_unsaved() and page.canvas.editores           # editores de la página visible
    por = {}
    for c in page.campos:
        por.setdefault(c.nombre, []).append(c.id)
    # orden de tabulación: Tab y Shift+Tab, saltando los de solo lectura y las firmas
    primero = page.navegables[0]
    assert page.canvas.enfocar(primero) and page.canvas.campo_con_foco() == primero
    _escribir(page.canvas.editores[primero], "Ana María Peña")
    assert page.valores()[primero] == "Ana María Peña"
    assert all(page.valores()[i] == "Ana María Peña" for i in por["nombre"])   # mismo nombre, página 2
    QTest.keyClick(page.canvas, Qt.Key.Key_Tab)
    assert page.canvas.campo_con_foco() == page.navegables[1] == por["observaciones"][0]
    QTest.keyClick(page.canvas, Qt.Key.Key_Tab)
    assert page.canvas.campo_con_foco() == por["acepto"][0]                     # «radicado» es de solo lectura
    QTest.keyClick(page.canvas.editores[por["acepto"][0]], Qt.Key.Key_Space)
    assert page.valores()[por["acepto"][0]] is True
    QTest.keyClick(page.canvas, Qt.Key.Key_Backtab)
    assert page.canvas.campo_con_foco() == por["observaciones"][0]
    # Enter en un campo de una línea pasa al siguiente
    page.canvas.enfocar(por["telefono"][0])
    _escribir(page.canvas.editores[por["telefono"][0]], "30012345678")
    assert page.valores()[por["telefono"][0]] == "3001234567"                  # respeta el máximo
    QTest.keyClick(page.canvas.editores[por["telefono"][0]], Qt.Key.Key_Return)
    assert page.canvas.campo_con_foco() == page.navegables[page.navegables.index(por["telefono"][0]) + 1]
    # el editor de solo lectura existe, se ve y no se edita
    assert not page.canvas.editores[por["radicado"][0]].isEnabled()
    page.set_valor(por["radicado"][0], "otro")
    assert page.valores()[por["radicado"][0]] == "R-123"
    # opciones, lista y desplegable
    page.set_valor(por["tipo"][1], True)
    assert [page.valores()[i] for i in por["tipo"]] == [False, True]
    page.set_valor(por["depto"][0], "CUN")
    assert page.canvas.editores[por["depto"][0]].currentText() == "Cundinamarca"
    page.set_valor(por["ciudad"][0], "Bogotá")
    page.set_valor(por["apoyos"][0], ["Kits", "Niñez"])
    page.refrescar()
    assert page.resumen.text().startswith("8 campos · 7 rellenados")
    # deshacer y panel de campos
    page.set_valor(por["observaciones"][0], "temporal")
    page.deshacer()
    assert page.valores()[por["observaciones"][0]] == ""
    page.chk_pendientes.setChecked(True)
    visibles = [page.lista.item(i) for i in range(page.lista.count()) if not page.lista.item(i).isHidden()]
    assert [it.data(fui.ROL_ID) for it in visibles] == [por["observaciones"][0], por["firma"][0]]
    page._campo_elegido(visibles[0])
    assert page.canvas.campo_con_foco() == por["observaciones"][0]
    page.chk_pendientes.setChecked(False)
    assert page.has_unsaved() and page.unsaved.isVisibleTo(page)

    out = tmp_path / "solicitud_rellenado.pdf"
    monkeypatch.setattr(fui, "ask_save", lambda *a, **k: str(out))
    page.save()
    wait()
    assert done[-1][0] == "Formulario guardado", done
    assert done[-1][1] == "Se rellenaron 7 campos." and done[-1][2] == str(out)
    assert page.path == str(out) and not page.has_unsaved()
    d = {c.nombre: c.valor for c in F.leer_campos(out)}
    assert d["nombre"] == "Ana María Peña" and d["acepto"] is True and d["depto"] == "CUN"
    assert d["ciudad"] == "Bogotá" and d["apoyos"] == ["Kits", "Niñez"] and d["telefono"] == "3001234567"
    assert F.leer_campos(src)[0].valor == ""                                 # el original no cambió


def test_ui_sellos_en_pdf_sin_campos(win, scanned, done, tmp_path, monkeypatch):
    src = tmp_path / "escaneado.pdf"
    src.write_bytes(scanned.read_bytes())
    page = _abrir(win, src)
    assert fui.AVISOS["sin_campos"] in page.textos_avisos and page.avisos.isVisibleTo(page)
    assert page.resumen.text().startswith("Sin campos")
    png = firmas.firma_desde_trazos([[(0, 20), (20, 0), (40, 30), (70, 5)]])
    # la firma se elige en el menú y se coloca con un clic
    monkeypatch.setattr(fui.FormsPage, "pedir_firma", lambda self: png)
    page.nueva_firma()
    assert page.canvas.tool == "firma" and page.firma_png == png
    page.colocar("firma", 0, 300, 620)
    assert page.canvas.tool == "rellenar"
    firma = page.sellos[-1]
    firma.moveBy(20, 0)
    texto = page.agregar_sello("texto", 0, 72, 700, texto="Recibido conforme")
    fecha = page.agregar_sello("fecha", 0, 72, 730)
    hoy = datetime.date.today().strftime("%d/%m/%Y")
    assert fecha.toPlainText() == hoy
    equis = page.agregar_sello("equis", 0, 100, 300)
    page.agregar_sello("chulo", 1, 100, 300)                                  # página girada 90°
    page.set_tamano("grande")
    assert texto.size == 11                                                   # solo cambia lo seleccionado
    page.canvas.scene().clearSelection()
    equis.setSelected(True)
    page.set_tamano("grande")
    assert equis.lado == fui.TAMANOS["grande"]["marca"]
    page.borrar_seleccion()
    assert equis.scene() is None
    page.deshacer()
    assert equis.scene() is not None
    vacio = page.agregar_sello("texto", 0, 300, 300, texto="")
    vacio.clearFocus()
    assert page.has_unsaved() and len(page._sellos_vivos()) == 5
    out = tmp_path / "escaneado_rellenado.pdf"
    monkeypatch.setattr(fui, "ask_save", lambda *a, **k: str(out))
    page.save()
    wait()
    assert done[-1][0] == "Formulario guardado", done
    assert done[-1][1] == "Se agregaron 5 elementos."
    with pymupdf.open(out) as d:
        t0 = d[0].get_text()
        assert "Recibido conforme" in t0 and hoy in t0
        assert len(d[0].get_images()) == 2 and len(d[0].get_drawings()) >= 2
        assert d[1].get_drawings()
    assert os.path.getsize(src) == os.path.getsize(scanned)


def test_ui_firmar_campo_de_firma(win, formulario, done, tmp_path, monkeypatch):
    page = _abrir(win, formulario)
    firma = next(c for c in page.campos if c.tipo == "firma")
    assert firma.id in page.canvas.zonas
    png = firmas.firma_desde_texto("Ana", "Ink Free")
    monkeypatch.setattr(fui.FormsPage, "pedir_firma", lambda self: png)
    page.firma_png = None
    page.firmar_campo(firma)
    item = page.sellos[-1]
    zona = page.canvas.rect_escena(firma)
    assert zona.contains(item.sceneBoundingRect().center()) and item.r.width() <= zona.width()
    page.refrescar()
    estados = {page.lista.item(i).data(fui.ROL_ID): page.lista.item(i).data(fui.ROL_ESTADO)
               for i in range(page.lista.count())}
    assert estados[firma.id] == "firmada"
    # Chulo y Fecha sobre campos del formulario actúan sobre el campo, no ponen un sello encima
    acepto = next(c for c in page.campos if c.nombre == "acepto")
    obs = next(c for c in page.campos if c.nombre == "observaciones")
    sellos = len(page._sellos_vivos())
    page.set_tool("chulo")
    _clic(page.canvas, page.canvas.rect_escena(acepto).center())
    assert page.valores()[acepto.id] is True and page.canvas.tool == "chulo"
    page.set_tool("fecha")
    _clic(page.canvas, page.canvas.rect_escena(obs).center())
    assert page.valores()[obs.id] == datetime.date.today().strftime("%d/%m/%Y") and page.canvas.tool == "rellenar"
    assert len(page._sellos_vivos()) == sellos
    # y fuera de los campos sí ponen el sello
    page.set_tool("chulo")
    from PySide6.QtCore import QPointF
    _clic(page.canvas, page.canvas.pages[0].pos() + QPointF(400, 600))
    assert len(page._sellos_vivos()) == sellos + 1 and page.sellos[-1].sello == "chulo"
    page.set_tool("rellenar")


def test_ui_contrasena_y_avisos(win, formulario, done, tmp_path, monkeypatch):
    cifrado = tmp_path / "cifrado.pdf"
    with pymupdf.open(formulario) as d:
        cat = d.pdf_catalog()
        tel = next(w.xref for w in d[0].widgets() if w.field_name == "telefono")
        d.xref_set_key(cat, "AcroForm/CO", f"[{tel} 0 R]")
        d.save(cifrado, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="1234", owner_pw="dueño")
    antes = _sha(cifrado)
    monkeypatch.setattr(widgets.QInputDialog, "getText", lambda *a, **k: ("1234", True))
    page = _abrir(win, cifrado)
    assert fui.AVISOS["calculos"] in page.textos_avisos
    cid = next(c.id for c in page.campos if c.nombre == "nombre")
    page.set_valor(cid, "Clave segura")
    out = tmp_path / "cifrado_rellenado.pdf"
    monkeypatch.setattr(fui, "ask_save", lambda *a, **k: str(out))
    page.chk_aplanar.setChecked(True)
    page.save()
    wait()
    page.chk_aplanar.setChecked(False)
    assert done[-1][0] == "Formulario guardado", done
    assert core.needs_password(out) and core.check_password(out, "1234")
    with core.open_pdf(out, "1234") as d:
        assert not d.is_form_pdf and "Clave segura" in d[0].get_text()
        assert "Solicitud de apoyo" in d[0].get_text()          # el rótulo fijo de la página sigue ahí
    assert _sha(cifrado) == antes


def test_ui_datos_vaciar_exportar_importar(win, formulario, done, tmp_path, monkeypatch):
    page = _abrir(win, formulario)
    cid = next(c.id for c in page.campos if c.nombre == "nombre")
    page.set_valor(cid, "Datos de prueba")
    destino = tmp_path / "datos.csv"
    monkeypatch.setattr(fui.QFileDialog, "getSaveFileName", lambda *a, **k: (str(destino), "Excel (CSV) (*.csv)"))
    page.exportar_datos()
    assert done[-1][0] == "Datos exportados" and destino.exists()
    page.vaciar_formulario()
    assert page.valores()[cid] == "" and page.valores()[next(c.id for c in page.campos if c.nombre == "ciudad")] == ""
    page.deshacer()
    assert page.valores()[cid] == "Datos de prueba"
    page.vaciar_formulario()
    destino.write_text(destino.read_text(encoding="utf-8-sig") + "inventado;x\n", encoding="utf-8-sig")
    monkeypatch.setattr(fui, "ask_open", lambda *a, **k: [str(destino)])
    page.importar_datos()
    assert page.valores()[cid] == "Datos de prueba"
    assert done[-1][0] == "info" and "Se aplicaron 8 campos" in done[-1][1] and "ignoraron" in done[-1][1]
