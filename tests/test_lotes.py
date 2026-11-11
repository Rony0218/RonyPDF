"""Acciones por lotes y Renombrar por contenido: núcleo (lotes.py, renombrar.py) e interfaz sin pantalla."""

import csv
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import pymupdf
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Los ajustes (y las carpetas RonyPDF_flujos y RonyPDF_lotes junto a ellos) nunca deben tocar los reales.
os.environ.setdefault("RONYPDF_SETTINGS", os.path.join(tempfile.mkdtemp(prefix="ronypdf-"), "RonyPDF.json"))

from ronypdf import censura, core, lotes, oficina, renombrar  # noqa: E402
from ronypdf.widgets import Cancelled  # noqa: E402

MOTORES = oficina.detectar(refrescar=True)
WINDOWS = sys.platform == "win32"
con_word = pytest.mark.skipif(not (WINDOWS and MOTORES.word), reason="Microsoft Word no está instalado")
con_excel = pytest.mark.skipif(not (WINDOWS and MOTORES.excel), reason="Microsoft Excel no está instalado")
SIN_VENTANA = subprocess.CREATE_NO_WINDOW if WINDOWS else 0
SHOTS = os.environ.get("RONYPDF_SHOTS")
TIEMPOS = Path(tempfile.gettempdir()) / "ronypdf-lotes-tiempos.json"
DV = censura.digito_verificacion("900123456")
NIT = f"900123456-{DV}"


def _procesos_office() -> set[int]:
    if not WINDOWS:
        return set()
    out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                         creationflags=SIN_VENTANA).stdout
    return {int(f[1]) for f in csv.reader(out.splitlines())
            if len(f) > 1 and f[0].upper() in ("WINWORD.EXE", "EXCEL.EXE", "POWERPNT.EXE")}


def _sin_procesos_nuevos(antes: set[int], espera: float = 20):
    fin = time.time() + espera
    nuevos = set()
    while time.time() < fin:
        nuevos = _procesos_office() - antes
        if not nuevos:
            return
        time.sleep(0.5)
    raise AssertionError(f"Quedaron procesos de Office creados por la prueba: {nuevos}")


def _anotar_tiempo(clave: str, segundos: float) -> None:
    try:
        datos = json.loads(TIEMPOS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        datos = {}
    datos[clave] = round(segundos, 3)
    TIEMPOS.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n[tiempo] {clave}: {segundos:.2f} s")


def _hash(ruta) -> str:
    return hashlib.sha256(Path(ruta).read_bytes()).hexdigest()


def _pdf(ruta, textos=("Documento de prueba",), titulo="", tam=14):
    with pymupdf.open() as d:
        for t in textos:
            p = d.new_page()
            p.insert_text((72, 90), t, fontsize=tam)
        if titulo:
            d.set_metadata({"title": titulo})
        d.save(ruta)
    return Path(ruta)


def _factura_pdf(ruta, fecha="Fecha: 12 de marzo de 2025", factura="Factura electrónica de venta No. FE-1045"):
    with pymupdf.open() as d:
        p = d.new_page()
        p.insert_text((72, 80), "Papelería El Lápiz S.A.S.", fontsize=22)
        p.insert_text((72, 120), factura, fontsize=14)
        p.insert_text((72, 150), f"NIT 900.123.456-{DV}", fontsize=12)
        p.insert_text((72, 175), fecha, fontsize=12)
        p.insert_text((72, 200), "Cliente: Ana Pérez, C.C. 1.032.456.789", fontsize=12)
        p.insert_text((72, 225), "Fecha de vencimiento: 12/04/2025", fontsize=12)
        d.save(ruta)
    return Path(ruta)


def _rasterizar(pdf, salida, sello="Radicado 2025-00123", dpi=200):
    """Escaneo: cada página como imagen, con un sello de texto digital corto encima."""
    with pymupdf.open(pdf) as src, pymupdf.open() as out:
        for page in src:
            pix = page.get_pixmap(dpi=dpi)
            q = out.new_page(width=page.rect.width, height=page.rect.height)
            q.insert_image(q.rect, pixmap=pix)
        if sello:
            out[0].insert_text((out[0].rect.width - 180, 30), sello, fontsize=9)
        out.save(salida)
    return Path(salida)


def _texto(ruta, pw=None) -> str:
    with core.open_pdf(ruta, pw) as d:
        return "\n".join(p.get_text() for p in d)


def _temporales() -> set[str]:
    return {p.name for p in Path(tempfile.gettempdir()).glob("ronypdf-lote-*")}


# ======================================================================== renombrar: extracción

@pytest.mark.parametrize("texto, esperado", [
    ("Fecha: 12/03/2025", "2025-03-12"),
    ("Fecha: 12-03-2025", "2025-03-12"),
    ("Fecha 2025-03-12", "2025-03-12"),
    ("Bogotá, 12 de marzo de 2025", "2025-03-12"),
    ("marzo 12 de 2025", "2025-03-12"),
    ("12-mar-2025", "2025-03-12"),
    ("12 Sept. 2024", "2024-09-12"),
    ("Expedida el 3 de ene. del 2025", "2025-01-03"),
    ("Fecha de vencimiento: 30/04/2025\nFecha de expedición: 01/04/2025", "2025-04-01"),
    ("Recibido 05/02/2025\nFecha: 07/02/2025", "2025-02-07"),
    ("31/02/2025", ""),                          # no existe
    ("Fecha de nacimiento 15/03/1975", ""),      # fuera de rango
])
def test_fechas(texto, esperado):
    assert renombrar.analizar(texto)["fecha"] == esperado


def test_nit_solo_con_digito_correcto():
    malo = (DV + 1) % 10
    d = renombrar.analizar(f"NIT 900.123.456-{malo}\nProveedor NIT: 900.123.456-{DV}\nNIT 899.999.068-1")
    assert d["nit"] == NIT and d["nit_2"] == "899999068-1"
    assert renombrar.analizar("NIT 900123456")["nit"] == ""           # sin dígito de verificación
    assert renombrar.analizar(f"NIT 900.123.456‑{DV}")["nit"] == NIT   # guion de no separación de Word


def test_cedula_y_factura():
    d = renombrar.analizar(f"Cliente C.C. 1.032.456.789\nNIT 900.123.456-{DV}")
    assert d["cedula"] == "1032456789"
    assert renombrar.analizar(f"Documento 900.123.456-{DV} NIT 900.123.456-{DV}")["cedula"] == ""
    for texto, num in [("Factura electrónica de venta No. FE-1045", "FE-1045"), ("FACTURA DE VENTA N.° FV 00123", "FV 00123"),
                       ("Factura # 45", "45"), ("Factura: SETP990000002", "SETP990000002"),
                       ("Cuenta de cobro No. 15", "15"), ("Número de factura: fe - 77", "FE-77"),
                       ("Factura de venta\nCliente: Ana", "")]:
        assert renombrar.analizar(texto)["factura"] == num, texto


def test_palabra_clave():
    t = "Radicado No. 2025-00123 del 3 de marzo\nAsunto: Solicitud de información\nRADICADO\n\nX"
    assert renombrar.analizar(t, "radicado")["texto"] == "2025-00123 del 3 de marzo"
    assert renombrar.analizar(t, "Asunto")["texto"] == "Solicitud de información"
    assert renombrar.analizar("Referencia:\nContrato 45 de 2025", "Referencia")["texto"] == "Contrato 45 de 2025"
    assert len(renombrar.analizar("Nota: " + "x" * 90, "Nota")["texto"]) == 40
    assert renombrar.analizar(t, "")["texto"] == ""


def test_titulo_por_metadato_y_por_letra_grande(tmp_path):
    a = _pdf(tmp_path / "a.pdf", ["Contenido"], titulo="Informe trimestral de compras")
    assert renombrar.extraer(a)["titulo"] == "Informe trimestral de compras"
    b = tmp_path / "b.pdf"
    with pymupdf.open() as d:
        p = d.new_page()
        p.insert_text((72, 60), "pie pequeño", fontsize=8)
        p.insert_text((72, 120), "Acta de reunión del comité", fontsize=24)
        p.insert_text((72, 160), "Texto normal del acta", fontsize=11)
        d.set_metadata({"title": "Microsoft Word - acta.docx"})
        d.save(b)
    datos = renombrar.extraer(b)
    assert datos["titulo"] == "Acta de reunión del comité"
    assert datos["nombre"] == "b" and datos["paginas"] == "1"


def test_extraer_no_escribe_el_pdf_y_respeta_paginas(tmp_path):
    a = _pdf(tmp_path / "a.pdf", ["Primera", "Segunda", "Fecha: 12/03/2025"])
    antes = _hash(a)
    assert renombrar.extraer(a, paginas=2)["fecha"] == ""
    assert renombrar.extraer(a, paginas=3)["fecha"] == "2025-03-12"
    assert _hash(a) == antes


def test_extraer_escaneo_con_sello_digital(tmp_path):
    factura = _factura_pdf(tmp_path / "factura.pdf")
    escaneo = _rasterizar(factura, tmp_path / "escaneo.pdf")
    with pymupdf.open(escaneo) as d:
        assert core.page_needs_ocr(d[0]) and "Radicado" in d[0].get_text()
    sin_ocr = renombrar.extraer(escaneo, usar_ocr=False, palabra_clave="Radicado")
    assert sin_ocr["texto"] == "2025-00123" and sin_ocr["nit"] == ""      # solo el sello es texto
    datos = renombrar.extraer(escaneo, palabra_clave="Radicado")
    assert datos["fecha"] == "2025-03-12" and datos["nit"] == NIT and datos["factura"] == "FE-1045"
    assert datos["cedula"] == "1032456789" and datos["texto"] == "2025-00123"
    assert "Papeler" in datos["titulo"]


def test_extraer_protegido(protegido):
    with pytest.raises(core.PasswordError):
        renombrar.extraer(protegido.path)
    datos = renombrar.extraer(protegido.path, protegido.password)
    assert datos["paginas"] == "3"
    res = renombrar.leer_lote([str(protegido.path)])
    assert res[str(protegido.path)]["_protegido"]
    protegido.comprobar(protegido.path)            # leer nunca toca el archivo


# ======================================================================== renombrar: plantillas

def test_plantillas_y_fichas():
    datos = {"fecha": "2025-03-12", "nit": NIT, "factura": "FE/1045", "nombre": "scan001", "paginas": "3",
             "titulo": "Acta: comité"}
    assert renombrar.componer(datos, "{fecha}_{nit}_{factura}") == (f"2025-03-12_{NIT}_FE_1045", [])
    assert renombrar.componer(datos, "{año}{mes}{dia} {titulo}")[0] == "20250312 Acta_ comité"
    assert renombrar.componer(datos, "{n:03}-{nombre} ({paginas} págs)", n=7)[0] == "007-scan001 (3 págs)"
    assert renombrar.componer(datos, "{fecha}", formato_fecha="AAAAMMDD")[0] == "20250312"
    assert renombrar.componer(datos, "{fecha}", formato_fecha="DD-MM-AAAA")[0] == "12-03-2025"
    nombre, faltan = renombrar.componer({"nombre": "x"}, "{fecha}_{cedula}_{nombre}")
    assert nombre == "sin-fecha_sin-cedula_x" and faltan == ["fecha", "cedula"]
    assert renombrar.componer({"nombre": "x"}, "  ..{texto}..  ", si_falta="omitir")[0] == "x"
    with pytest.raises(core.UserError, match="no existe"):
        renombrar.validar_plantilla("{fecha}_{cosa}")
    with pytest.raises(core.UserError):
        renombrar.validar_plantilla("   ")


def test_proponer_falta_duplicados_y_existentes(tmp_path):
    rutas = [str(_pdf(tmp_path / f"f{k}.pdf")) for k in range(4)]
    (tmp_path / "2025-01-01.pdf").write_bytes(b"ya existe")
    datos = {rutas[0]: {"fecha": "2025-01-01"}, rutas[1]: {"fecha": "2025-01-01"}, rutas[2]: {"fecha": ""},
             rutas[3]: {"_error": "Protegido: no se pudo abrir", "_protegido": True}}
    props = renombrar.proponer(rutas, "{fecha}", datos=datos)
    assert [p.nuevo for p in props] == ["2025-01-01 (2).pdf", "2025-01-01 (3).pdf", "sin-fecha.pdf", "f3.pdf"]
    assert [p.estado for p in props] == ["duplicado", "duplicado", "falta", "protegido"]
    assert props[2].detalle == "Falta: fecha" and not props[3].incluir
    props = renombrar.proponer(rutas, "{fecha}", datos=datos, si_falta="omitir")
    assert props[2].nuevo == "f2.pdf" and not props[2].incluir and props[2].estado == "falta"
    props = renombrar.proponer(rutas[:1], "{nombre}", datos={rutas[0]: {}})
    assert props[0].estado == "igual" and not props[0].incluir
    props = renombrar.proponer(rutas[:2], "{fecha}", datos=datos, manuales={rutas[1]: "Mi factura: marzo"})
    assert props[1].nuevo == "Mi factura_ marzo.pdf" and props[1].manual
    props = renombrar.proponer(rutas[:1], 'a<b>c:"d"|e?*', datos={rutas[0]: {}})
    assert props[0].nuevo == "a_b_c_d_e.pdf"


# ======================================================================== renombrar: aplicar y deshacer

def test_aplicar_renombrar_y_deshacer(tmp_path):
    carpeta = tmp_path / "Facturas de marzo ñ"
    carpeta.mkdir()
    a = _factura_pdf(carpeta / "scan001.pdf")
    b = _factura_pdf(carpeta / "scan002.pdf", fecha="Fecha: 13/03/2025", factura="Factura No. FE-1046")
    c = _factura_pdf(carpeta / "scan003.pdf")                 # mismos datos que a: duplicado
    hashes = {p.name: _hash(p) for p in (a, b, c)}
    props = renombrar.proponer([str(a), str(b), str(c)], "{fecha}_{nit}_{factura}")
    nombres = [p.nuevo for p in props]
    assert nombres == [f"2025-03-12_{NIT}_FE-1045.pdf", f"2025-03-13_{NIT}_FE-1046.pdf",
                       f"2025-03-12_{NIT}_FE-1045 (2).pdf"]
    regs = tmp_path / "registros"
    res = renombrar.aplicar(props, carpeta_registro=regs)
    assert res.hechos == 3 and not res.errores
    assert sorted(p.name for p in carpeta.iterdir()) == sorted(nombres)
    assert {_hash(carpeta / n) for n in nombres} == set(hashes.values())     # solo cambió el nombre
    reg = renombrar.ultimo_registro(regs)
    assert reg is not None and len(reg.pares) == 3
    assert renombrar.deshacer(reg) == 3
    assert sorted(p.name for p in carpeta.iterdir()) == ["scan001.pdf", "scan002.pdf", "scan003.pdf"]
    assert renombrar.ultimo_registro(regs) is None                          # ya se deshizo


def test_aplicar_archivo_bloqueado_y_destino_existente(tmp_path, monkeypatch):
    a, b = _pdf(tmp_path / "a.pdf"), _pdf(tmp_path / "b.pdf")
    props = renombrar.proponer([str(a), str(b)], "nuevo_{nombre}", datos={str(a): {}, str(b): {}})
    (tmp_path / "nuevo_b.pdf").write_bytes(b"lo creo otra persona mientras tanto")
    rename = os.rename

    def falso(src, dst):
        if Path(src).name == "a.pdf":
            raise PermissionError(32, "El proceso no tiene acceso al archivo")
        return rename(src, dst)

    monkeypatch.setattr(os, "rename", falso)
    res = renombrar.aplicar(props, carpeta_registro=tmp_path / "reg")
    assert res.hechos == 1 and res.errores == [(str(a), "Está abierto en otro programa (o no hay permiso para cambiarlo)")]
    assert (tmp_path / "nuevo_b (2).pdf").exists() and (tmp_path / "nuevo_b.pdf").read_bytes().startswith(b"lo creo")
    assert a.exists()


def test_aplicar_copiar_y_deshacer_copias(tmp_path):
    a = _factura_pdf(tmp_path / "a.pdf")
    destino = tmp_path / "copias"
    props = renombrar.proponer([str(a)], "{factura}", carpeta=str(destino))
    res = renombrar.aplicar(props, "copiar", str(destino), carpeta_registro=tmp_path / "reg")
    assert res.hechos == 1 and (destino / "FE-1045.pdf").exists() and a.exists()
    reg = renombrar.ultimo_registro(tmp_path / "reg")
    assert reg.modo == "copiar"
    assert renombrar.deshacer(reg) == 1 and not (destino / "FE-1045.pdf").exists() and a.exists()
    with pytest.raises(core.UserError):
        renombrar.aplicar(props, "copiar", None)


# ======================================================================== renombrar: archivos reales

@con_word
def test_factura_real_de_word_y_su_escaneo(tmp_path):
    antes = _procesos_office()
    docx = tmp_path / "Factura de marzo.docx"
    oficina.escribir_docx([
        oficina.Parrafo("Papelería El Lápiz S.A.S.", estilo="titulo1"),
        oficina.Parrafo("Factura electrónica de venta No. FE-1045", negrita=True),
        oficina.Parrafo(f"NIT 900.123.456-{DV}"),
        oficina.Parrafo("Fecha: 12 de marzo de 2025"),
        oficina.Parrafo("Cliente: Ana Pérez, C.C. 1.032.456.789"),
        oficina.Parrafo("Total a pagar: $ 1.250.000"),
    ], docx)
    pdf = tmp_path / "factura_word.pdf"
    assert oficina.a_pdf(docx, pdf) == 1
    _sin_procesos_nuevos(antes)
    datos = renombrar.extraer(pdf)
    assert (datos["fecha"], datos["nit"], datos["factura"], datos["cedula"]) == \
           ("2025-03-12", NIT, "FE-1045", "1032456789")
    assert "Papeler" in datos["titulo"]
    escaneo = _rasterizar(pdf, tmp_path / "factura_escaneada.pdf")
    datos_ocr = renombrar.extraer(escaneo, palabra_clave="Radicado")
    assert (datos_ocr["fecha"], datos_ocr["nit"], datos_ocr["factura"]) == ("2025-03-12", NIT, "FE-1045")
    assert datos_ocr["texto"] == "2025-00123"


# ======================================================================== lotes: flujos

def test_flujo_json_ida_y_vuelta_sin_claves(tmp_path):
    f = lotes.Flujo("Mío", [("ocr", {"idioma": "spa", "calidad": 400}),
                            ("proteger", {"clave": "secreta", "imprimir": False, "copiar": True}),
                            ("censurar", {"patrones": ["cedula", "inventado"], "palabras": "Ana, Luis"})],
                    destino={"modo": "carpeta", "sufijo": "_listo", "carpeta": str(tmp_path / "salida"),
                             "conservar_subcarpetas": False}, al_error="detener")
    ruta = lotes.guardar_flujo(f, tmp_path)
    contenido = ruta.read_text(encoding="utf-8")
    assert "secreta" not in contenido and '"clave"' not in contenido and '"version": 1' in contenido
    g = lotes.listar_flujos(tmp_path)[0]
    assert g.nombre == "Mío" and g.al_error == "detener" and g.destino["sufijo"] == "_listo"
    # las opciones que trae el motor de OCR (enderezar, girar, limpiar) quedan con su valor por defecto
    assert g.pasos[0] == ("ocr", {"idioma": "spa", "calidad": 400, "enderezar": True, "girar": True,
                                  "limpiar": "auto"})
    assert g.pasos[1][1] == {"imprimir": False, "copiar": True}
    assert g.pasos[2][1]["patrones"] == ["cedula"]          # el patrón inventado se descarta
    assert g.a_json() == lotes.Flujo.de_json(json.loads(contenido)).a_json()
    assert lotes.borrar_flujo("Mío", tmp_path) and not lotes.listar_flujos(tmp_path)


def test_flujo_con_paso_desconocido(tmp_path):
    datos = {"version": 1, "nombre": "Viejo", "pasos": [{"paso": "teletransportar", "valores": {}},
                                                        {"paso": "comprimir", "valores": {"nivel": "alta"}}]}
    (tmp_path / "Viejo.json").write_text(json.dumps(datos), encoding="utf-8")
    f = lotes.listar_flujos(tmp_path)[0]
    assert [c for c, _ in f.pasos] == ["comprimir"]
    assert any("teletransportar" in a for a in f.avisos)
    plan = lotes.planificar(f, [])
    assert any("teletransportar" in a for a in plan.avisos)


def test_ejemplos_genericos_y_orden():
    nombres = [f.nombre for f in lotes.ejemplos()]
    assert nombres == ["Escaneos listos para archivar", "Para enviar por correo", "Publicar sin datos personales",
                       "Numerar y marcar como copia"]
    texto = json.dumps([f.a_json() for f in lotes.ejemplos()], ensure_ascii=False).lower()
    assert "fundaci" not in texto
    pasos, movidos = lotes.ordenar([("proteger", {}), ("imprimir", {}), ("ocr", {}), ("a_pdf", {}), ("numerar", {})])
    assert [c for c, _ in pasos] == ["a_pdf", "ocr", "numerar", "proteger", "imprimir"]
    assert set(movidos) == {"Convertir a PDF", "Proteger con contraseña", "Imprimir"}


def test_pasos_externos():
    class Modulo:
        PASOS_LOTE = [lotes.Paso("prueba_ext", "Paso de otro módulo", "", [], lambda *a: "listo")]
    try:
        assert [p.clave for p in lotes.pasos_externos([Modulo])] == ["prueba_ext"]
        assert lotes.pasos_externos([Modulo]) == []
    finally:
        lotes.PASOS.pop("prueba_ext", None)


# ======================================================================== lotes: plan

def test_planificar_carpetas_tipos_protegidos_y_colisiones(tmp_path, protegido):
    raiz = tmp_path / "origen"
    (raiz / "sub" / "subsub").mkdir(parents=True)
    _pdf(raiz / "a.pdf")
    _pdf(raiz / "sub" / "a.pdf")
    _pdf(raiz / "sub" / "subsub" / "b.pdf")
    (raiz / "carta.docx").write_bytes(b"x")
    (raiz / "notas.txt").write_text("x")
    (raiz / ".~a.123.tmp.pdf").write_bytes(b"x")
    _pdf(raiz / "a_procesado.pdf")                              # ya existe un resultado
    prot = raiz / "protegido.pdf"
    prot.write_bytes(Path(protegido.path).read_bytes())
    flujo = lotes.Flujo("x", [("numerar", {})])
    plan = lotes.planificar(flujo, [str(raiz)], recursivo=False)
    nombres = sorted(Path(t.origen).name for t in plan.tareas)
    assert nombres == ["a.pdf", "a_procesado.pdf", "protegido.pdf"]
    plan = lotes.planificar(flujo, [str(raiz), str(raiz / "a.pdf"), str(raiz / "carta.docx")], recursivo=True)
    por = {os.path.relpath(t.origen, raiz): t for t in plan.tareas}
    assert len(plan.tareas) == 6                                # sin repetir a.pdf
    assert por["a.pdf"].salida.endswith("a_procesado (2).pdf")
    assert any("ya existe" in a for a in por["a.pdf"].avisos)
    assert por["carta.docx"].omitir == "no es un PDF" and "no es PDF y el flujo no convierte" in por["carta.docx"].avisos
    assert por["protegido.pdf"].protegido and "protegido: se pedirá la contraseña" in por["protegido.pdf"].avisos
    assert plan.problema is None and len(plan.protegidos) == 1

    destino = raiz / "salida"
    flujo2 = lotes.Flujo("x", [("a_pdf", {}), ("numerar", {})],
                         destino={"modo": "carpeta", "carpeta": str(destino), "sufijo": "", "conservar_subcarpetas": False})
    destino.mkdir()
    _pdf(destino / "viejo.pdf")
    plan2 = lotes.planificar(flujo2, [str(raiz)], recursivo=True)
    rutas = {os.path.relpath(t.origen, raiz) for t in plan2.tareas}
    assert os.path.join("salida", "viejo.pdf") not in rutas      # no recorre la carpeta de destino
    assert "carta.docx" in rutas and not next(t for t in plan2.tareas if t.origen.endswith("carta.docx")).omitir
    salidas = [Path(t.salida).name for t in plan2.tareas if t.salida]
    assert salidas.count("a.pdf") == 1 and "a (2).pdf" in salidas    # dos «a.pdf» en la misma carpeta de destino

    assert lotes.planificar(lotes.Flujo("x"), [str(raiz)]).problema.startswith("El flujo no tiene pasos")
    assert lotes.planificar(flujo, []).problema == "Agrega archivos o una carpeta."
    sin_carpeta = lotes.Flujo("x", [("numerar", {})], destino={"modo": "carpeta", "carpeta": ""})
    assert lotes.planificar(sin_carpeta, [str(raiz)]).problema == "Elige la carpeta de destino."
    malo = lotes.Flujo("x", [("censurar", {"patrones": [], "palabras": ""})])
    assert "Censurar" in lotes.planificar(malo, [str(raiz)]).problema


def test_planificar_subcarpetas_conservadas(tmp_path):
    raiz = tmp_path / "origen"
    (raiz / "2025" / "marzo").mkdir(parents=True)
    _pdf(raiz / "2025" / "marzo" / "x.pdf")
    destino = tmp_path / "destino"
    flujo = lotes.Flujo("x", [("numerar", {})], destino={"modo": "carpeta", "carpeta": str(destino),
                                                          "sufijo": "_n", "conservar_subcarpetas": True})
    plan = lotes.planificar(flujo, [str(raiz)], recursivo=True)
    assert Path(plan.tareas[0].salida) == destino / "2025" / "marzo" / "x_n.pdf"
    inf = lotes.ejecutar(plan)
    assert (destino / "2025" / "marzo" / "x_n.pdf").exists() and inf.totales()["hecho"] == 1


# ======================================================================== lotes: ejecución

def test_ocr_comprimir_numerar_sobre_escaneo(scanned, tmp_path):
    origen = tmp_path / "escaneo con tildes ñ.pdf"
    origen.write_bytes(Path(scanned).read_bytes())
    antes = _hash(origen)
    flujo = lotes.Flujo("x", [("ocr", {"idioma": "spa+eng", "calidad": 300}), ("comprimir", {"nivel": "media"}),
                              ("numerar", {"formato": "Página {n} de {total}"})])
    temporales = _temporales()
    inf = lotes.ejecutar(lotes.planificar(flujo, [str(origen)]))
    r = inf.archivos[0]
    assert r.estado == "hecho", r.detalle
    salida = Path(r.salida)
    assert salida.name == "escaneo con tildes ñ_procesado.pdf"
    texto = _texto(salida)
    assert "niñez" in texto and "Página 1 de 2" in texto and "Página 2 de 2" in texto
    assert [p for p, _ in r.pasos] == ["Reconocer texto (OCR)", "Comprimir", "Numerar páginas"]
    assert _hash(origen) == antes                                # el original queda byte a byte igual
    assert _temporales() == temporales
    # pasarlo otra vez: el OCR ya no hace nada
    inf2 = lotes.ejecutar(lotes.planificar(lotes.Flujo("x", [("ocr", {})]), [str(salida)]))
    assert inf2.archivos[0].pasos[0][1] == "ya tenía texto"


def test_escaneos_listos_para_archivar(scanned, tmp_path):
    origen = tmp_path / "scan.pdf"
    origen.write_bytes(Path(scanned).read_bytes())
    flujo = lotes.ejemplos()[0]
    inf = lotes.ejecutar(lotes.planificar(flujo, [str(origen)]))
    r = inf.archivos[0]
    assert r.estado == "hecho" and "niñez" in _texto(r.salida)
    with pymupdf.open(r.salida) as d:
        assert d[0].search_for("niñez")


def test_cada_paso_contra_su_funcion(samples, tmp_path):
    a = tmp_path / "informe.pdf"
    a.write_bytes(Path(samples["a"]).read_bytes())

    def uno(clave, **valores):
        inf = lotes.ejecutar(lotes.planificar(lotes.Flujo(clave, [(clave, valores)]), [str(a)]))
        return inf.archivos[0]

    r = uno("comprimir", nivel="maxima")
    esperado = tmp_path / "esperado.pdf"
    core.compress(a, esperado, "maxima")
    assert r.estado == "hecho" and abs(r.despues - os.path.getsize(esperado)) < 200 and "→" in r.pasos[0][1]
    r = uno("marca", texto="BORRADOR {archivo}")
    assert "BORRADOR informe" in _texto(r.salida)
    r = uno("numerar", formato="{n} / {total}", posicion="arriba-derecha", saltar_primera=True)
    with core.open_pdf(r.salida) as d:
        assert "1 / 4" in d[1].get_text() and "/ 4" not in d[0].get_text()
    r = uno("ocultos", categorias=["metadatos", "marcadores"])
    with core.open_pdf(r.salida) as d:
        assert not (d.metadata or {}).get("title") and not d.get_toc()
    assert "metadatos" in r.pasos[0][1]
    r = uno("a_texto")
    assert r.salida is None and r.extras[0].endswith("informe_procesado.txt")
    assert "Informe - Página 3" in Path(r.extras[0]).read_text(encoding="utf-8-sig")
    r = uno("a_imagenes", formato="jpg", ppp=72)
    carpeta = Path(r.extras[0])
    assert carpeta.name == "informe_procesado_imagenes" and len(list(carpeta.glob("informe_*.jpg"))) == 5
    r = uno("censurar", patrones=["cedula"], palabras="Informe")
    assert r.estado in ("hecho", "con aviso") and "censurados" in r.pasos[0][1]
    assert "Informe" not in _texto(r.salida)
    con_datos = _pdf(tmp_path / "datos.pdf", ["Contacto: Ana, C.C. 1.032.456.789, correo ana@correo.com"])
    inf = lotes.ejecutar(lotes.planificar(lotes.ejemplos()[2], [str(con_datos)]))
    texto = _texto(inf.archivos[0].salida)
    assert "1.032.456.789" not in texto and "ana@correo.com" not in texto and "Contacto" in texto


def test_convertir_imagen_y_nombrar(samples, tmp_path):
    foto = tmp_path / "foto.jpg"
    foto.write_bytes(Path(samples["jpg"]).read_bytes())
    factura = _factura_pdf(tmp_path / "scan9.pdf")
    flujo = lotes.Flujo("x", [("a_pdf", {}), ("nombrar", {"plantilla": "{fecha}_{factura}", "usar_ocr": False})])
    plan = lotes.planificar(flujo, [str(foto), str(factura)])
    assert all(t.salida is None and "el nombre se decide al leer el contenido" in t.avisos for t in plan.tareas)
    inf = lotes.ejecutar(plan)
    r_foto, r_fact = inf.archivos
    assert r_fact.estado == "hecho" and Path(r_fact.salida).name == "2025-03-12_FE-1045.pdf"
    assert r_foto.estado == "con aviso" and Path(r_foto.salida).name == "sin-fecha_sin-factura.pdf"
    assert r_foto.pasos[0][1] == "imagen convertida a PDF"
    assert lotes.ejecutar(lotes.planificar(lotes.Flujo("x", [("numerar", {})]), [str(foto)])).archivos[0].estado == "omitido"


def test_sin_office_el_error_va_al_informe(tmp_path, monkeypatch):
    monkeypatch.setattr(oficina, "_cache_motores", oficina.Motores())
    docx = tmp_path / "carta.docx"
    oficina.escribir_docx([oficina.Parrafo("Hola")], docx)
    otro = _pdf(tmp_path / "otro.pdf")
    inf = lotes.ejecutar(lotes.planificar(lotes.Flujo("x", [("a_pdf", {}), ("numerar", {})]), [str(docx), str(otro)]))
    assert inf.archivos[0].estado == "error" and oficina.SIN_MOTOR in inf.archivos[0].mensaje
    assert inf.archivos[1].estado == "hecho"


def test_protegido_conserva_clave_paginas_imagen_y_adjunto(protegido, tmp_path):
    origen = tmp_path / "protegido.pdf"
    origen.write_bytes(Path(protegido.path).read_bytes())
    flujo = lotes.Flujo("x", [("numerar", {"saltar_primera": True}), ("comprimir", {"nivel": "media"})])
    plan = lotes.planificar(flujo, [str(origen)])
    assert plan.tareas[0].protegido
    sin_clave = lotes.ejecutar(plan)
    assert sin_clave.archivos[0].estado == "error" and "Protegido" in sin_clave.archivos[0].mensaje
    mala = lotes.ejecutar(plan, {str(origen): "otra"})
    assert mala.archivos[0].estado == "error" and "contraseña" in mala.archivos[0].mensaje
    inf = lotes.ejecutar(plan, {str(origen): protegido.password})
    r = inf.archivos[0]
    assert r.estado == "hecho", r.detalle
    protegido.comprobar(r.salida, sin_tocar=(0,))
    with core.open_pdf(r.salida, protegido.password) as d:
        assert protegido.textos[1].split("\n")[0] in d[1].get_text() and "Página 1 de 2" in d[1].get_text()
        xref = d[1].get_images()[0][0]
        assert len(d.extract_image(xref)["image"]) > 1000
    assert _hash(origen) == _hash(protegido.path)


def test_proteger_y_luego_otro_paso_usa_la_clave_nueva(protegido, samples, tmp_path):
    origen = tmp_path / "p.pdf"
    origen.write_bytes(Path(protegido.path).read_bytes())
    # Orden armado a mano (la interfaz deja «Proteger» al final): el paso siguiente abre con la clave nueva.
    flujo = lotes.Flujo("x", [("proteger", {"imprimir": True}), ("numerar", {})])
    plan = lotes.planificar(flujo, [str(origen)])
    inf = lotes.ejecutar(plan, {str(origen): protegido.password}, {0: {"clave": "nueva"}})
    r = inf.archivos[0]
    assert r.estado == "hecho", r.detalle
    assert core.check_password(r.salida, "nueva") and not core.check_password(r.salida, protegido.password)
    with core.open_pdf(r.salida, "nueva") as d:
        assert "1 de 3" in d[0].get_text() and d.embfile_count() == 1 and len(d[1].get_images()) == 1
    sin = lotes.ejecutar(plan, {str(origen): protegido.password})
    assert sin.archivos[0].estado == "error" and "contraseña nueva" in sin.archivos[0].mensaje
    # quitar la contraseña y luego numerar
    flujo2 = lotes.Flujo("x", [("quitar_clave", {}), ("numerar", {})])
    r2 = lotes.ejecutar(lotes.planificar(flujo2, [str(origen)]), {str(origen): protegido.password}).archivos[0]
    assert r2.estado == "hecho" and not core.needs_password(r2.salida)
    with core.open_pdf(r2.salida) as d:
        assert d.embfile_get(0) == protegido.adjunto
    libre = tmp_path / "libre.pdf"
    libre.write_bytes(Path(samples["b"]).read_bytes())
    r3 = lotes.ejecutar(lotes.planificar(flujo2, [str(libre)])).archivos[0]
    assert r3.pasos[0][1] == "no tenía contraseña"


def test_pdf_danado_en_medio_y_detener(tmp_path):
    a, c = _pdf(tmp_path / "a.pdf"), _pdf(tmp_path / "c.pdf")
    b = tmp_path / "b.pdf"
    b.write_bytes(b"%PDF-1.7\nesto no es un PDF de verdad")
    flujo = lotes.Flujo("x", [("numerar", {}), ("comprimir", {})])
    temporales = _temporales()
    inf = lotes.ejecutar(lotes.planificar(flujo, [str(a), str(b), str(c)]))
    assert [r.estado for r in inf.archivos] == ["hecho", "error", "hecho"]
    assert "Numerar páginas" in inf.archivos[1].mensaje and "no parece un PDF válido" in inf.archivos[1].mensaje
    assert not (tmp_path / "b_procesado.pdf").exists() and inf.archivos[1].salida is None
    assert _temporales() == temporales
    flujo.al_error = "detener"
    for p in tmp_path.glob("*_procesado*.pdf"):
        p.unlink()
    inf = lotes.ejecutar(lotes.planificar(flujo, [str(a), str(b), str(c)]))
    assert [r.estado for r in inf.archivos] == ["hecho", "error", "omitido"]
    assert "se detuvo" in inf.archivos[2].mensaje and not (tmp_path / "c_procesado.pdf").exists()


def test_cancelar_a_mitad(tmp_path):
    rutas = [str(_pdf(tmp_path / f"d{k}.pdf", ["x"] * 3)) for k in range(5)]
    hashes = [_hash(r) for r in rutas]
    llamadas = {"n": 0}

    def progreso(i, n, msg):
        if i == 2 and "Comprimir" in msg:                   # a mitad del tercer archivo, entre pasos
            raise Cancelled()
        llamadas["n"] += 1

    temporales = _temporales()
    flujo = lotes.Flujo("x", [("numerar", {}), ("comprimir", {})])
    inf = lotes.ejecutar(lotes.planificar(flujo, rutas), progress=progreso)
    assert inf.cancelado and "Cancelado" in inf.resumen()
    assert [r.estado for r in inf.archivos] == ["hecho", "hecho", "omitido", "omitido", "omitido"]
    assert sorted(p.name for p in tmp_path.glob("*_procesado.pdf")) == ["d0_procesado.pdf", "d1_procesado.pdf"]
    assert _temporales() == temporales
    assert [_hash(r) for r in rutas] == hashes


def test_destino_sin_permiso(tmp_path, monkeypatch):
    a = _pdf(tmp_path / "a.pdf")
    destino = tmp_path / "bloqueada"
    flujo = lotes.Flujo("x", [("numerar", {})], destino={"modo": "carpeta", "carpeta": str(destino), "sufijo": ""})
    original = Path.mkdir

    def mkdir(self, *a, **k):
        if Path(self) == destino:
            raise PermissionError(13, "Acceso denegado")
        return original(self, *a, **k)

    monkeypatch.setattr(Path, "mkdir", mkdir)
    inf = lotes.ejecutar(lotes.planificar(flujo, [str(a)]))
    r = inf.archivos[0]
    assert r.estado == "error" and "No hay permiso para escribir" in r.mensaje and "bloqueada" in r.mensaje


def test_rutas_largas_y_nombres_con_tildes(tmp_path):
    carpeta = tmp_path / "Informes de gestión del año 2025, niñez y educación (versión final)"
    carpeta.mkdir()
    a = _pdf(carpeta / "Acta de reunión con tildes áéíóú y eñes, segunda versión revisada.pdf")
    assert len(str(a)) > 180
    inf = lotes.ejecutar(lotes.planificar(lotes.Flujo("x", [("numerar", {})]), [str(a)]))
    assert inf.archivos[0].estado == "hecho" and Path(inf.archivos[0].salida).exists()


def test_imprimir_prepara_en_la_interfaz_y_la_tarea_solo_pinta(tmp_path, monkeypatch):
    from PySide6.QtGui import QPdfWriter
    from PySide6.QtWidgets import QApplication

    from ronypdf import imprimir as imp
    QApplication.instance() or QApplication([])
    a = _pdf(tmp_path / "a.pdf", ["uno", "dos"])
    salida_pdf = tmp_path / "impreso.pdf"

    def preparar(v):                     # en lugar de una impresora real, un QPdfWriter (nada sale del equipo)
        pr = QPdfWriter(str(salida_pdf))
        op = imp.OpcionesImpresion(copias=int(v["copias"]))
        imp.preparar_impresora(pr, op, imp.papel("Carta"))
        return {"printer": pr, "op": op, "nombre": "PDF de prueba", "aviso": ""}

    monkeypatch.setattr(lotes.PASOS["imprimir"], "preparar", preparar)
    flujo = lotes.Flujo("x", [("imprimir", {"copias": 1})])
    plan = lotes.planificar(flujo, [str(a)])
    assert plan.tareas[0].salida is None
    preparados = {0: lotes.PASOS["imprimir"].preparar({"copias": 1})}
    inf = lotes.ejecutar(plan, preparados=preparados)
    r = inf.archivos[0]
    assert r.estado == "hecho" and r.pasos[0][1] == "2 hojas enviadas a PDF de prueba" and r.salida is None
    assert salida_pdf.exists() and not list(tmp_path.glob("a_procesado*"))
    assert lotes.ejecutar(plan).archivos[0].estado == "error"          # sin preparar no imprime


# ======================================================================== lotes: informe

def test_informe_totales_xlsx_y_txt(tmp_path):
    a, b = _pdf(tmp_path / "a.pdf"), tmp_path / "b.pdf"
    b.write_bytes(b"roto")
    inf = lotes.ejecutar(lotes.planificar(lotes.Flujo("Mi flujo", [("numerar", {})]), [str(a), str(b)]))
    assert inf.totales() == {"hecho": 1, "con aviso": 0, "omitido": 0, "error": 1}
    assert inf.resumen().startswith("1 hecho · 1 con error · ")
    filas = inf.a_filas()
    assert filas[0][0] == "✓" and filas[0][2] == "a_procesado.pdf" and filas[1][0] == "✗"
    xlsx = tmp_path / "informe.xlsx"
    inf.guardar_xlsx(xlsx)
    with zipfile.ZipFile(xlsx) as z:
        hoja = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
        resumen = z.read("xl/worksheets/sheet2.xml").decode("utf-8")
    assert "a_procesado.pdf" in hoja and "b.pdf" in hoja and "Mi flujo" in resumen
    txt = tmp_path / "informe.txt"
    inf.guardar_txt(txt)
    contenido = txt.read_text(encoding="utf-8-sig")
    assert "Mi flujo" in contenido and "ERROR" in contenido and "a_procesado.pdf" in contenido
    assert lotes.duracion_texto(134) == "2 min 14 s" and lotes.duracion_texto(7) == "7 s"


# ======================================================================== rendimiento

def test_rendimiento_300_archivos(tmp_path):
    carpeta = tmp_path / "trescientos"
    carpeta.mkdir()
    for k in range(300):
        _pdf(carpeta / f"doc_{k:03}.pdf", [f"Documento {k}, Fecha: {1 + k % 28:02}/03/2025"])
    t0 = time.perf_counter()
    plan = lotes.planificar(lotes.Flujo("x", [("numerar", {})]), [str(carpeta)])
    t_frio = time.perf_counter() - t0               # la primera vez que se abren (el antivirus los revisa)
    t0 = time.perf_counter()
    plan = lotes.planificar(lotes.Flujo("x", [("numerar", {})]), [str(carpeta)])
    t_plan = time.perf_counter() - t0
    assert len(plan.activas) == 300
    t0 = time.perf_counter()
    inf = lotes.ejecutar(plan)
    t_ej = time.perf_counter() - t0
    assert inf.totales()["hecho"] == 300
    rutas = sorted(str(p) for p in carpeta.glob("doc_*.pdf") if "_procesado" not in p.name)
    t0 = time.perf_counter()
    lecturas = renombrar.leer_lote(rutas)
    t_leer = time.perf_counter() - t0
    datos = {r: renombrar.datos_de(lecturas[r], r) for r in rutas}
    t0 = time.perf_counter()
    props = renombrar.proponer(rutas, "{fecha}_{n:03}", datos=datos)
    t_prop = time.perf_counter() - t0
    assert len(props) == 300 and props[0].nuevo == "2025-03-01_001.pdf"
    _anotar_tiempo("plan_300_archivos_recien_creados", t_frio)
    _anotar_tiempo("plan_300_archivos", t_plan)
    _anotar_tiempo("ejecutar_numerar_300_archivos", t_ej)
    _anotar_tiempo("renombrar_leer_300_archivos", t_leer)
    _anotar_tiempo("renombrar_proponer_300_archivos", t_prop)
    assert t_plan < 3 and t_prop < 2


def test_rendimiento_documento_de_300_paginas(tmp_path):
    grande = _pdf(tmp_path / "grande.pdf", [f"Página {k} del informe" for k in range(320)])
    t0 = time.perf_counter()
    inf = lotes.ejecutar(lotes.planificar(lotes.Flujo("x", [("numerar", {}), ("comprimir", {})]), [str(grande)]))
    t = time.perf_counter() - t0
    assert inf.archivos[0].estado == "hecho"
    with core.open_pdf(inf.archivos[0].salida) as d:
        assert d.page_count == 320 and "320 de 320" in d[-1].get_text()
    t0 = time.perf_counter()
    renombrar.extraer(grande)
    _anotar_tiempo("numerar_comprimir_320_paginas", t)
    _anotar_tiempo("renombrar_extraer_320_paginas", time.perf_counter() - t0)


# ======================================================================== archivos reales de Office por el lote

@con_excel
def test_lote_con_excel_real_convertir_y_nombrar(tmp_path):
    antes = _procesos_office()
    xlsx = tmp_path / "Cuenta de cobro.xlsx"
    oficina.escribir_xlsx([("Cobro", [["Cuenta de cobro No. 15", ""], ["Proveedor", f"NIT 900.123.456-{DV}"],
                                      ["Fecha de expedición", "12/03/2025"], ["Valor", 850000]])], xlsx)
    pdf = _pdf(tmp_path / "otro.pdf")
    flujo = lotes.Flujo("x", [("a_pdf", {}), ("numerar", {}), ("nombrar", {"plantilla": "{fecha}_{factura}_{nit}"})],
                        destino={"modo": "carpeta", "carpeta": str(tmp_path / "salida"), "sufijo": ""})
    inf = lotes.ejecutar(lotes.planificar(flujo, [str(xlsx), str(pdf)]))
    _sin_procesos_nuevos(antes)
    r = inf.archivos[0]
    assert r.estado == "hecho", r.detalle
    assert Path(r.salida).name == f"2025-03-12_15_{NIT}.pdf"
    assert "documento de Excel convertido" in r.pasos[0][1]
    assert "Página 1 de 1" in _texto(r.salida)


# ======================================================================== interfaz

from PySide6.QtCore import QModelIndex  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ronypdf import lotes_ui, widgets  # noqa: E402
from ronypdf.gui import MainWindow, _light_palette  # noqa: E402
from ronypdf.style import QSS  # noqa: E402


@pytest.fixture(scope="module")
def win():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setPalette(_light_palette())
    app.setStyleSheet(QSS)
    w = MainWindow()
    w.resize(1240, 820)
    w.show()
    app.processEvents()
    return w


@pytest.fixture
def done(monkeypatch):
    calls = []

    def fake_done(parent, title, detail="", file=None, folder=None):
        calls.append(("done", title, detail, file, folder))

    monkeypatch.setattr(lotes_ui, "show_done", fake_done)
    monkeypatch.setattr(widgets, "show_done", fake_done)
    monkeypatch.setattr(lotes_ui, "avisar", lambda p, t, m: calls.append(("aviso", t, m)))
    monkeypatch.setattr(lotes_ui, "confirmar", lambda *a, **k: calls.append(("confirmar", a[1], a[2])) or True)
    monkeypatch.setattr(lotes_ui, "reveal", lambda p: calls.append(("reveal", p)))
    monkeypatch.setattr(lotes_ui, "view_in_app", lambda p: calls.append(("ver", p)))
    monkeypatch.setattr(widgets.QMessageBox, "warning", lambda *a, **k: calls.append(("warning", a[2])))
    monkeypatch.setattr(widgets.QMessageBox, "information", lambda *a, **k: calls.append(("info", a[2])))
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


def procesar(seg=0.2):
    end = time.time() + seg
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.01)


def captura(w, nombre):
    if SHOTS:
        procesar(0.2)
        w.grab().save(str(Path(SHOTS) / f"{nombre}.png"))


def _sin_recortes(page):
    """Ningún botón, casilla ni texto de una línea de la página queda más angosto de lo que pide."""
    from PySide6.QtWidgets import QAbstractButton, QComboBox, QLabel, QLineEdit
    malos = []
    for w in page.findChildren(QAbstractButton) + page.findChildren(QComboBox):
        if isinstance(w.parent(), QLineEdit):          # el botón «borrar» que Qt pone dentro de un campo
            continue
        if w.isVisible() and w.width() + 1 < w.minimumSizeHint().width():
            malos.append(f"{type(w).__name__} «{getattr(w, 'text', lambda: '')()}»: {w.width()} < {w.minimumSizeHint().width()}")
    for lb in page.findChildren(QLabel):
        if lb.isVisible() and not lb.wordWrap() and lb.text() and lb.sizePolicy().horizontalPolicy().name != "Ignored" \
                and lb.width() + 1 < lb.sizeHint().width() and lb.pixmap().isNull():
            malos.append(f"QLabel «{lb.text()[:40]}»: {lb.width()} < {lb.sizeHint().width()}")
    return malos


def test_ui_lotes_completo(win, done, tmp_path, monkeypatch, scanned, protegido):
    page = win.pages["batch"]
    win.navigate("batch")
    win.resize(1000, 660)
    procesar()
    assert win.stack.currentWidget() is page
    page._nuevo_lote()
    carpeta = tmp_path / "Lote de marzo"
    (carpeta / "sub").mkdir(parents=True)
    _pdf(carpeta / "a.pdf", ["Primera página del documento", "Segunda página del documento"])
    _pdf(carpeta / "sub" / "b.pdf")
    (carpeta / "scan.pdf").write_bytes(Path(scanned).read_bytes())
    (carpeta / "prot.pdf").write_bytes(Path(protegido.path).read_bytes())
    (carpeta / "carta.docx").write_bytes(b"PK no importa")
    hashes = {p: _hash(p) for p in carpeta.rglob("*.*")}
    page.accept_files([str(carpeta)])
    assert page.lista.topLevelItemCount() == 5
    page.lista.completar()
    filas = {it.text(0): it.text(1) for it in page.lista.items()}
    assert filas["prot.pdf"] == "Protegido" and filas["a.pdf"] == "2" and filas["carta.docx"] == "Word"
    assert page.aviso_arch.isVisible() and "no es PDF" in page.aviso_arch_txt.text()

    # elegir el ejemplo «Escaneos listos para archivar» y agregar un paso
    i = next(k for k in range(page.combo.count()) if (page.combo.itemData(k) or ("", ""))[1] == "Numerar y marcar como copia")
    page.combo.setCurrentIndex(i)
    assert [c for c, _ in page.flujo.pasos] == ["numerar", "marca"] and page.pasos.count() == 2
    i = next(k for k in range(page.combo.count()) if (page.combo.itemData(k) or ("", ""))[1] == "Escaneos listos para archivar")
    page.combo.setCurrentIndex(i)
    assert [c for c, _ in page.flujo.pasos] == ["ocr", "comprimir"] and not page._sucio
    page._agregar_paso("proteger")
    page._agregar_paso("numerar")
    assert [c for c, _ in page.flujo.pasos] == ["ocr", "comprimir", "numerar", "proteger"]
    assert page.nota_orden.isVisible() and "Proteger" in page.nota_orden_txt.text()
    # reordenar arrastrando: numerar arriba del todo
    assert page.pasos.model().moveRow(QModelIndex(), 2, QModelIndex(), 0)
    procesar()
    assert [c for c, _ in page.flujo.pasos] == ["numerar", "ocr", "comprimir", "proteger"]
    # intentar subir «Proteger»: la lista lo deja al final
    page.pasos.setCurrentRow(3)
    page._mover(-1)
    assert page.flujo.pasos[-1][0] == "proteger"
    page._quitar(3)
    assert [c for c, _ in page.flujo.pasos] == ["numerar", "ocr", "comprimir"] and page._sucio
    # opciones del paso (diálogo generado)
    dlg = lotes_ui.DialogoOpciones(lotes.PASOS["numerar"], page.flujo.pasos[0][1])
    assert set(dlg.campos) == {"formato", "posicion", "tamano", "inicio", "saltar_primera"}
    dlg.campos["formato"][1].setCurrentIndex(dlg.campos["formato"][1].findData("{n} / {total}"))
    nuevos = dlg.valores()
    assert nuevos["formato"] == "{n} / {total}"
    monkeypatch.setattr(lotes_ui, "editar_opciones", lambda *a: nuevos)
    page._opciones(0)
    assert page.flujo.pasos[0][1]["formato"] == "{n} / {total}"
    dlg_c = lotes_ui.DialogoOpciones(lotes.PASOS["censurar"], {})
    assert dlg_c.valores()["patrones"] == ["cedula", "celular", "correo"]
    captura(win, "lotes-1000-editar")
    assert _sin_recortes(page) == []

    # vista previa
    page._vista_previa()
    assert page.modo == "vista" and page.tabla_vista.topLevelItemCount() == 5
    vista = {page.tabla_vista.topLevelItem(k).text(0): page.tabla_vista.topLevelItem(k) for k in range(5)}
    assert vista["carta.docx"].text(1) == "Omitido: no es un PDF"
    assert vista["a.pdf"].text(1) == "a_procesado.pdf"
    assert "protegido" in vista["prot.pdf"].text(3)
    captura(win, "lotes-1000-vista")
    assert _sin_recortes(page) == []
    # probar con el primer archivo: no escribe en el destino
    page._probar()
    wait()
    assert done[-1][0] == "ver" and "ronypdf-prueba-" in done[-1][1]
    assert not list(carpeta.rglob("*_procesado*"))

    # ejecutar: la contraseña del protegido se pide (y se puede usar para los demás)
    pedidas = []
    monkeypatch.setattr(lotes_ui, "pedir_clave", lambda p, n, e=False, varios=False: pedidas.append(n) or (protegido.password, True))
    page.run()
    wait()
    assert pedidas == ["prot.pdf"]
    assert page.modo == "resultado"
    inf = page._informe
    assert inf.totales() == {"hecho": 4, "con aviso": 0, "omitido": 1, "error": 0}, [r.detalle for r in inf.archivos]
    assert page.tabla_res.topLevelItemCount() == 5 and "4 hechos" in page.res_resumen.text()
    estados = {it.text(0): it.text(3) for it in page.lista.items()}
    assert estados["a.pdf"] == "✓ Hecho" and estados["carta.docx"] == "– Omitido"
    prot = next(r for r in inf.archivos if r.origen.endswith("prot.pdf"))
    assert core.check_password(prot.salida, protegido.password)
    assert "niñez" in _texto(next(r.salida for r in inf.archivos if r.origen.endswith("scan.pdf")))
    assert (carpeta / "sub" / "b_procesado.pdf").exists()
    assert {p: _hash(p) for p in hashes} == hashes            # ningún original cambió
    captura(win, "lotes-1000-resultado")
    assert _sin_recortes(page) == []
    win.resize(1240, 820)
    procesar()
    captura(win, "lotes-1240-resultado")
    assert _sin_recortes(page) == []

    # informe y carpeta
    ruta_xlsx = tmp_path / "informe.xlsx"
    monkeypatch.setattr(lotes_ui, "pedir_ruta_informe", lambda *a: str(ruta_xlsx))
    page._guardar_informe()
    assert ruta_xlsx.exists() and done[-1][1] == "Informe guardado"
    page._abrir_carpeta()
    assert done[-1][0] == "reveal"
    assert not page.b_reintentar.isEnabled()

    # nuevo lote: la lista queda vacía y Ejecutar se deshabilita con la explicación
    page.run()
    assert page.modo == "editar" and page.lista.topLevelItemCount() == 0
    assert not page.action.isEnabled() and page.footer_info.text() == "Agrega archivos o una carpeta para empezar"


def test_ui_lotes_guardar_y_recargar_flujo(win, done, monkeypatch, tmp_path):
    page = win.pages["batch"]
    win.navigate("batch")
    page._nuevo_lote()
    page.combo.setCurrentIndex(0)                               # flujo nuevo, vacío
    assert page.flujo.pasos == [] and not page.action.isEnabled()
    assert page.footer_info.text() == "Agrega al menos un paso al flujo"
    page._agregar_paso("comprimir")
    page._agregar_paso("marca")
    page._agregar_paso("proteger")
    page.r_carpeta.setChecked(True)
    assert page.footer_info.text() == "Elige la carpeta de destino"
    page.e_carpeta.setText(str(tmp_path / "salida"))
    page._destino_cambio()
    nombre = f"Mi flujo de prueba {time.time_ns()}"
    monkeypatch.setattr(lotes_ui, "pedir_nombre_flujo", lambda *a: nombre)
    page._guardar()
    assert page.origen_flujo == ("guardado", nombre) and not page._sucio
    archivo = lotes.carpeta_flujos() / f"{nombre}.json"
    contenido = json.loads(archivo.read_text(encoding="utf-8"))
    assert [p["paso"] for p in contenido["pasos"]] == ["comprimir", "marca", "proteger"]
    assert "clave" not in contenido["pasos"][2]["valores"] and contenido["destino"]["modo"] == "carpeta"
    # recargar: se ve igual
    page.combo.setCurrentIndex(0)
    i = next(k for k in range(page.combo.count()) if (page.combo.itemData(k) or ("", ""))[1] == nombre)
    page.combo.setCurrentIndex(i)
    assert [c for c, _ in page.flujo.pasos] == ["comprimir", "marca", "proteger"]
    assert page.r_carpeta.isChecked() and page.e_carpeta.text() == str(tmp_path / "salida")
    # ejecutar con la contraseña nueva y el destino en otra carpeta
    a = _pdf(tmp_path / "a.pdf")
    page.accept_files([str(a)])
    monkeypatch.setattr(lotes_ui, "pedir_clave_nueva", lambda *a: "clave-nueva")
    page.run()
    wait()
    r = page._informe.archivos[0]
    assert r.estado == "hecho" and Path(r.salida).parent == tmp_path / "salida"
    assert core.check_password(r.salida, "clave-nueva")
    assert "clave-nueva" not in archivo.read_text(encoding="utf-8")
    # cancelar la contraseña nueva no ejecuta nada
    page._modo("editar")
    monkeypatch.setattr(lotes_ui, "pedir_clave_nueva", lambda *a: None)
    antes = len(list((tmp_path / "salida").iterdir()))
    page.run()
    wait()
    assert len(list((tmp_path / "salida").iterdir())) == antes
    # eliminar el flujo
    page._eliminar()
    assert not archivo.exists() and page.origen_flujo[0] == "ejemplo"
    page._nuevo_lote()


def test_ui_lotes_reintentar_fallidos(win, done, monkeypatch, tmp_path, protegido):
    page = win.pages["batch"]
    win.navigate("batch")
    page._nuevo_lote()
    i = next(k for k in range(page.combo.count()) if (page.combo.itemData(k) or ("", ""))[1] == "Para enviar por correo")
    page.combo.setCurrentIndex(i)
    p = tmp_path / "prot.pdf"
    p.write_bytes(Path(protegido.path).read_bytes())
    ok = _pdf(tmp_path / "ok.pdf")
    page.accept_files([str(p), str(ok)])
    monkeypatch.setattr(lotes_ui, "pedir_clave", lambda *a, **k: (None, False))     # se omite el protegido
    page.run()
    wait()
    assert [r.estado for r in page._informe.archivos] == ["error", "hecho"]
    assert page.b_reintentar.isEnabled() and "Protegido" in page._informe.archivos[0].mensaje
    monkeypatch.setattr(lotes_ui, "pedir_clave", lambda *a, **k: (protegido.password, False))
    page._reintentar()
    wait()
    assert [r.estado for r in page._informe.archivos] == ["hecho"]
    protegido.comprobar(page._informe.archivos[0].salida, sin_tocar=(0, 1, 2))
    page._nuevo_lote()


def test_ui_lotes_300_archivos_fluidos(win, done, tmp_path):
    page = win.pages["batch"]
    win.navigate("batch")
    page._nuevo_lote()
    carpeta = tmp_path / "muchos"
    carpeta.mkdir()
    for k in range(300):
        _pdf(carpeta / f"f{k:03}.pdf")
    t0 = time.perf_counter()
    page.accept_files([str(carpeta)])
    t_lista = time.perf_counter() - t0
    # Mientras se cuentan las páginas, la ventana sigue respondiendo: ningún tramo pasa de ~0,1 s.
    peor, t0 = 0.0, time.perf_counter()
    while page.lista._timer.isActive() and time.perf_counter() - t0 < 60:
        t1 = time.perf_counter()
        QApplication.processEvents()
        peor = max(peor, time.perf_counter() - t1)
    t_paginas = time.perf_counter() - t0
    assert all(it.text(1) == "1" for it in page.lista.items())
    t0 = time.perf_counter()
    page._vista_previa()
    t_vista = time.perf_counter() - t0
    assert page.tabla_vista.topLevelItemCount() == 300
    _anotar_tiempo("ui_lista_300_archivos", t_lista)
    _anotar_tiempo("ui_contar_paginas_300_en_segundo_plano", t_paginas)
    _anotar_tiempo("ui_peor_pausa_de_la_ventana", peor)
    _anotar_tiempo("ui_vista_previa_300_archivos", t_vista)
    assert t_lista < 3 and t_vista < 5 and peor < 0.5
    page._nuevo_lote()


def test_ui_renombrar(win, done, monkeypatch, tmp_path):
    page = win.pages["rename"]
    win.navigate("rename")
    win.resize(1000, 660)
    page._vaciar()
    carpeta = tmp_path / "Facturas"
    carpeta.mkdir()
    a = _factura_pdf(carpeta / "scan001.pdf")
    b = _factura_pdf(carpeta / "scan002.pdf", fecha="Sin fecha visible", factura="Factura No. FE-1046")
    c = _factura_pdf(carpeta / "scan003.pdf")
    escaneo = _rasterizar(_factura_pdf(tmp_path / "base.pdf", factura="Factura No. FE-2000"), carpeta / "scan004.pdf")
    page.accept_files([str(carpeta)])
    wait()
    procesar(0.3)
    assert page.tabla.topLevelItemCount() == 4
    filas = {page.tabla.topLevelItem(k).text(0): page.tabla.topLevelItem(k) for k in range(4)}
    assert filas["scan001.pdf"].text(1) == f"2025-03-12_{NIT}_FE-1045.pdf"
    assert filas["scan002.pdf"].text(1) == f"sin-fecha_{NIT}_FE-1046.pdf" and filas["scan002.pdf"].text(2) == "Falta: fecha"
    assert filas["scan002.pdf"].background(0).color() == lotes_ui.AMBAR
    assert filas["scan003.pdf"].text(1) == f"2025-03-12_{NIT}_FE-1045 (2).pdf"
    assert filas["scan003.pdf"].background(0).color() == lotes_ui.AMBAR
    assert filas["scan004.pdf"].text(1) == f"2025-03-12_{NIT}_FE-2000.pdf"           # escaneo leído con OCR
    assert page.action.text() == "Renombrar 4 archivos"
    # cambiar la plantilla actualiza al instante (sin volver a leer)
    leidas = len(page.cache)
    page.e_plantilla.setText("{factura} {texto}")
    page.e_palabra.setText("Radicado")
    page._actualizar()
    assert len(page.cache) == leidas
    assert filas["scan004.pdf"].text(1) == "FE-2000 2025-00123.pdf"
    page.e_plantilla.setText("{fecha}_{nit}_{factura}")
    page.e_palabra.setText("")
    page._actualizar()
    # corregir a mano la fila en ámbar
    filas["scan002.pdf"].setText(1, "2025-03-15_factura corregida")
    procesar()
    assert filas["scan002.pdf"].text(1) == "2025-03-15_factura corregida.pdf"
    assert filas["scan002.pdf"].text(2) == "Nombre escrito a mano"
    page.e_plantilla.setText("{fecha}_{cosa}")
    page._actualizar()
    assert not page.action.isEnabled() and "no existe" in page.footer_info.text()
    page.e_plantilla.setText("{fecha}_{nit}_{factura}")
    page._actualizar()
    captura(win, "renombrar-1000")
    assert _sin_recortes(page) == []
    win.resize(1240, 820)
    procesar()
    captura(win, "renombrar-1240")
    assert _sin_recortes(page) == []
    # renombrar (confirmación propia) y deshacer
    page.run()
    wait()
    procesar()
    assert done[-2][0] == "confirmar" and "4 archivos" in done[-2][2]
    assert done[-1][1] == "4 archivos renombrados"
    nombres = sorted(p.name for p in carpeta.iterdir())
    assert nombres == sorted([f"2025-03-12_{NIT}_FE-1045.pdf", f"2025-03-12_{NIT}_FE-1045 (2).pdf",
                              "2025-03-15_factura corregida.pdf", f"2025-03-12_{NIT}_FE-2000.pdf"])
    assert all(page.tabla.topLevelItem(k).text(2) == "✓ Nombre cambiado" for k in range(4))
    assert page.b_deshacer.isEnabled()
    page._deshacer()
    assert sorted(p.name for p in carpeta.iterdir()) == ["scan001.pdf", "scan002.pdf", "scan003.pdf", "scan004.pdf"]
    assert done[-1][1] == "Se deshizo el último renombrado" and done[-1][2].startswith("4 de 4")
    assert sorted(page.tabla.topLevelItem(k).text(0) for k in range(4)) == ["scan001.pdf", "scan002.pdf",
                                                                           "scan003.pdf", "scan004.pdf"]
    del a, b, c, escaneo


def test_ui_renombrar_protegidos_y_copias(win, done, monkeypatch, tmp_path, protegido):
    page = win.pages["rename"]
    win.navigate("rename")
    page._vaciar()
    p = tmp_path / "prot.pdf"
    p.write_bytes(Path(protegido.path).read_bytes())
    f = _factura_pdf(tmp_path / "f.pdf")
    monkeypatch.setattr(lotes_ui, "pedir_clave", lambda *a, **k: (None, False))
    page.accept_files([str(p), str(f)])
    wait()
    procesar(0.3)
    filas = {page.tabla.topLevelItem(k).text(0): page.tabla.topLevelItem(k) for k in range(2)}
    assert filas["prot.pdf"].text(2) == "Protegido: no se pudo abrir" and page.b_claves.isVisible()
    monkeypatch.setattr(lotes_ui, "pedir_clave", lambda *a, **k: (protegido.password, False))
    page._pedir_claves_protegidos()
    wait()
    procesar(0.3)
    assert filas["prot.pdf"].text(2) == "Falta: fecha, NIT, factura"
    destino = tmp_path / "copias"
    monkeypatch.setattr(lotes_ui, "elegir_carpeta", lambda *a: str(destino))
    page._elegir_carpeta()
    page.e_plantilla.setText("{nombre}-{factura}")
    page._actualizar()
    assert page.action.text() == "Copiar 2 archivos"
    page.run()
    wait()
    assert sorted(x.name for x in destino.iterdir()) == ["f-FE-1045.pdf", "prot-sin-factura.pdf"]
    assert p.exists() and f.exists()
    protegido.comprobar(destino / "prot-sin-factura.pdf")
    page.r_renombrar.setChecked(True)
    page.e_plantilla.setText(renombrar.PLANTILLA_DEFECTO)
    page._vaciar()


def test_ui_renombrar_300_archivos(win, done, tmp_path):
    page = win.pages["rename"]
    win.navigate("rename")
    page._vaciar()
    carpeta = tmp_path / "facturas300"
    carpeta.mkdir()
    for k in range(300):
        _pdf(carpeta / f"f{k:03}.pdf", [f"Factura No. FV-{k:04}  Fecha: {1 + k % 28:02}/03/2025"])
    t0 = time.perf_counter()
    page.accept_files([str(carpeta)])
    wait()
    t_leer = time.perf_counter() - t0
    t0 = time.perf_counter()
    page.e_plantilla.setText("{fecha}_{factura}")
    page._actualizar()
    t_plantilla = time.perf_counter() - t0
    assert page.tabla.topLevelItemCount() == 300 and page.action.text() == "Renombrar 300 archivos"
    assert page.tabla.topLevelItem(0).text(1) == "2025-03-01_FV-0000.pdf"
    _anotar_tiempo("ui_renombrar_leer_300", t_leer)
    _anotar_tiempo("ui_renombrar_cambiar_plantilla_300", t_plantilla)
    assert t_plantilla < 1.5
    page.e_plantilla.setText(renombrar.PLANTILLA_DEFECTO)
    page._vaciar()


def test_registro_y_iconos():
    regs = {r["clave"]: r for r in lotes_ui.REGISTRO}
    assert regs["batch"]["seccion"] == "optimizar" and regs["rename"]["seccion"] == "organizar"
    assert regs["batch"]["titulo"] == "Acciones por lotes" and regs["rename"]["titulo"] == "Renombrar por contenido"
    from ronypdf import extensiones
    for r in regs.values():
        pm = extensiones._azulejo(r["dibujo"])
        assert not pm.isNull()
