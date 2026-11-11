"""Office ↔ PDF: escritores .docx/.xlsx, números colombianos, script de PowerShell, conversión real
con Office (se salta donde no está instalado) e interfaz sin pantalla."""

import csv
import json
import os
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pymupdf
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Los ajustes (recientes, menú) de las pruebas nunca deben tocar los reales.
os.environ.setdefault("RONYPDF_SETTINGS", os.path.join(tempfile.mkdtemp(prefix="ronypdf-"), "RonyPDF.json"))

from ronypdf import core, oficina  # noqa: E402
from ronypdf.oficina import Codigo, OpcionesOffice, Parrafo, Porcentaje  # noqa: E402

MOTORES = oficina.detectar(refrescar=True)
WINDOWS = sys.platform == "win32"
con_word = pytest.mark.skipif(not (WINDOWS and MOTORES.word), reason="Microsoft Word no está instalado")
con_excel = pytest.mark.skipif(not (WINDOWS and MOTORES.excel), reason="Microsoft Excel no está instalado")
con_ppt = pytest.mark.skipif(not (WINDOWS and MOTORES.powerpoint), reason="PowerPoint no está instalado")
SIN_VENTANA = subprocess.CREATE_NO_WINDOW if WINDOWS else 0
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


# ---------------------------------------------------------------- utilidades

def _procesos_office() -> set[int]:
    if not WINDOWS:
        return set()
    out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                         creationflags=SIN_VENTANA).stdout
    pids = set()
    for fila in csv.reader(out.splitlines()):
        if len(fila) > 1 and fila[0].upper() in ("WINWORD.EXE", "EXCEL.EXE", "POWERPNT.EXE"):
            pids.add(int(fila[1]))
    return pids


def _sin_procesos_nuevos(antes: set[int], permitidos: set[int] = frozenset(), espera: float = 20):
    fin = time.time() + espera
    nuevos = set()
    while time.time() < fin:
        nuevos = _procesos_office() - set(antes) - set(permitidos)
        if not nuevos:
            return
        time.sleep(0.5)
    raise AssertionError(f"Quedaron procesos de Office creados por la prueba: {nuevos}")


def _xml_de(zip_path, nombre):
    with zipfile.ZipFile(zip_path) as z:
        return ET.fromstring(z.read(nombre))


def _texto_pdf(pdf) -> str:
    with pymupdf.open(pdf) as d:
        return "\n".join(p.get_text() for p in d)


def _docx_muestra(path: Path) -> Path:
    oficina.escribir_docx([
        Parrafo("Informe de gestión 2025", "titulo1"),
        Parrafo(tramos=[("Niñez ", True, False), ("y educación", False, True), (" en Antioquia.", False, False)]),
        Parrafo("Segunda sección", "titulo2", salto_pagina=True),
        Parrafo("Texto de la página dos.\tCon tabulador."),
        Parrafo("Tercera página", "titulo1", salto_pagina=True),
    ], path)
    return path


def _tabla_pdf(path: Path, lineas: bool, paginas: int = 1) -> Path:
    filas = [["Nombre", "Cédula", "Valor", "Fecha", "Avance"],
             ["Ana Pérez", "0012345678", "1.250.000", "15/03/2025", "12,5 %"],
             ["Luis Gómez", "79.456.123", "$ 800.000", "2025-04-01", "100 %"],
             ["Niñez Ruiz", "1020304050", "1.250.000,50", "01/12/2024", "0,5 %"]]
    with pymupdf.open() as d:
        for _ in range(paginas):
            p = d.new_page()
            p.insert_text((60, 60), "Extracto de pagos de la fundación", fontsize=13)
            x0, y0, cw, rh = 50, 100, 100, 24
            for r, fila in enumerate(filas):
                for c, val in enumerate(fila):
                    p.insert_text((x0 + c * cw + 4, y0 + r * rh + 16), val, fontsize=10)
            if lineas:
                for r in range(len(filas) + 1):
                    p.draw_line((x0, y0 + r * rh), (x0 + 5 * cw, y0 + r * rh))
                for c in range(6):
                    p.draw_line((x0 + c * cw, y0), (x0 + c * cw, y0 + len(filas) * rh))
        d.save(path)
    return path


def _celdas(xlsx, hoja=1) -> dict[str, tuple[str | None, str, str | None]]:
    """ref → (tipo, valor, estilo)."""
    raiz = _xml_de(xlsx, f"xl/worksheets/sheet{hoja}.xml")
    out = {}
    for c in raiz.iter(f"{S}c"):
        t = c.get("t")
        if t == "inlineStr":
            valor = "".join(x.text or "" for x in c.iter(f"{S}t"))
        else:
            v = c.find(f"{S}v")
            valor = v.text if v is not None else ""
        out[c.get("r")] = (t, valor, c.get("s"))
    return out


# ---------------------------------------------------------------- detección

def test_detectar_combinaciones(monkeypatch):
    monkeypatch.setattr(oficina, "_cache_motores", None)
    casos = [
        ({"Word.Application", "Excel.Application", "PowerPoint.Application"}, None,
         (True, True, True), "Se usará Microsoft Office, que ya está instalado en este equipo.", True),
        (set(), r"C:\LO\soffice.exe", (False, False, False), "Se usará LibreOffice.", True),
        (set(), None, (False, False, False), oficina.SIN_MOTOR, False),
        ({"Word.Application", "Excel.Application"}, None, (True, True, False), "No se encontró PowerPoint", True),
        ({"Word.Application"}, r"C:\LO\soffice.exe", (True, False, False), "se usará LibreOffice", True),
    ]
    for claves, lo, esperado, texto, puede in casos:
        monkeypatch.setattr(oficina, "_clave_existe", lambda n, c=claves: n in c)
        monkeypatch.setattr(oficina, "_buscar_libreoffice", lambda lo=lo: lo)
        m = oficina.detectar(refrescar=True)
        assert (m.word, m.excel, m.powerpoint) == esperado and m.libreoffice == lo
        t, ok = oficina.estado_motores(m)
        assert texto in t and ok is puede
    # con caché: no vuelve a consultar el registro
    monkeypatch.setattr(oficina, "_clave_existe", lambda n: pytest.fail("no debía consultar"))
    assert oficina.detectar() is m


def test_buscar_libreoffice(monkeypatch, tmp_path):
    pf, pf86, exe = tmp_path / "pf", tmp_path / "pf86", tmp_path / "app"
    for d in (pf, pf86, exe):
        d.mkdir()
    monkeypatch.setenv("ProgramFiles", str(pf))
    monkeypatch.setenv("ProgramW6432", str(pf))
    monkeypatch.setenv("ProgramFiles(x86)", str(pf86))
    monkeypatch.setattr(oficina.shutil, "which", lambda *_a, **_k: None)
    monkeypatch.setattr(oficina, "_junto_al_exe", lambda: exe)
    assert oficina._buscar_libreoffice() is None
    portable = exe / "LibreOfficePortable" / "App" / "libreoffice" / "program" / "soffice.exe"
    portable.parent.mkdir(parents=True)
    portable.write_bytes(b"x")
    assert oficina._buscar_libreoffice() == str(portable)
    en_path = tmp_path / "bin" / "soffice.exe"
    en_path.parent.mkdir()
    en_path.write_bytes(b"x")
    monkeypatch.setattr(oficina.shutil, "which", lambda n, **_k: str(en_path) if n == "soffice.exe" else None)
    assert oficina._buscar_libreoffice() == str(en_path)
    x86 = pf86 / "LibreOffice" / "program" / "soffice.exe"
    x86.parent.mkdir(parents=True)
    x86.write_bytes(b"x")
    assert oficina._buscar_libreoffice() == str(x86)
    normal = pf / "LibreOffice" / "program" / "soffice.exe"
    normal.parent.mkdir(parents=True)
    normal.write_bytes(b"x")
    assert oficina._buscar_libreoffice() == str(normal)


class _RegistroFalso:
    """winreg falso: solo permite leer y anota cada llamada."""
    HKEY_CLASSES_ROOT, HKEY_CURRENT_USER, KEY_READ = 1, 2, 0x20019

    def __init__(self, valores):
        self.valores = valores          # (raíz, ruta) → {nombre: valor}
        self.llamadas = []

    def OpenKey(self, raiz, ruta, reservado=0, acceso=0x20019):
        self.llamadas.append(("OpenKey", ruta, acceso))
        assert acceso == self.KEY_READ, "solo lectura"
        if (raiz, ruta) not in self.valores:
            raise OSError(ruta)
        reg = self

        class Clave:
            def __enter__(self):
                return (raiz, ruta)

            def __exit__(self, *a):
                return False
        reg._ultima = (raiz, ruta)
        return Clave()

    def QueryValueEx(self, clave, nombre):
        datos = self.valores[clave]
        if nombre not in datos:
            raise OSError(nombre)
        return datos[nombre], 4

    def EnumKey(self, clave, i):
        hijos = sorted({r.split("\\")[3] for (_, r) in self.valores if r.startswith("Software\\Microsoft\\Office\\")
                        and len(r.split("\\")) > 3})
        if i >= len(hijos):
            raise OSError
        return hijos[i]

    def __getattr__(self, nombre):            # SetValueEx, CreateKey, DeleteValue…: prohibidos
        if nombre.startswith("__"):
            raise AttributeError(nombre)
        raise AssertionError(f"no se debe usar winreg.{nombre}")


def test_aviso_word_desactivado_solo_lee(monkeypatch):
    opciones = "Software\\Microsoft\\Office\\16.0\\Word\\Options"
    base = {(1, "Word.Application\\CurVer"): {"": "Word.Application.16"}, (2, "Software\\Microsoft\\Office"): {}}
    for valor, esperado in ((1, True), (0, False), ("1", True), (None, False)):
        datos = dict(base)
        if valor is not None:
            datos[(2, opciones)] = {"DisableConvertPdfWarning": valor}
        falso = _RegistroFalso(datos)
        monkeypatch.setitem(sys.modules, "winreg", falso)
        assert oficina.aviso_word_desactivado() is esperado
        assert all(n == "OpenKey" for n, *_ in falso.llamadas)
    # una versión vieja con el aviso desactivado no cuenta si Word es la 16
    falso = _RegistroFalso({**base, (2, "Software\\Microsoft\\Office\\15.0\\Word\\Options"):
                            {"DisableConvertPdfWarning": 1}})
    monkeypatch.setitem(sys.modules, "winreg", falso)
    assert oficina.aviso_word_desactivado() is False


# ---------------------------------------------------------------- script de PowerShell y LibreOffice

def test_script_generado_y_trabajo(tmp_path):
    script = oficina.escribir_script(tmp_path)
    crudo = script.read_bytes()
    assert crudo.startswith(b"\xef\xbb\xbf"), "UTF-8 con BOM para PowerShell 5.1"
    s = crudo.decode("utf-8-sig")
    for esperado in ("[string]$ruta, [string]$salida",
                     "$w.Documents.Open($ruta, $false, $true",
                     "$d.ExportAsFixedFormat($salida, 17",
                     "$d.Close([ref]0)", "$app.Quit([ref]0)",
                     "$x.Workbooks.Open($ruta, 0, $true",
                     "$pg.Zoom = $false", "$pg.FitToPagesWide = 1", "$pg.FitToPagesTall = $false",
                     "$libro.ExportAsFixedFormat(0, $salida)", "$libro.Close($false)",
                     "$p.Presentations.Open($ruta + '::' + $CLAVE + '::', -1, 0, 0)",
                     "$pres.SaveAs($salida, 32)",
                     "$app.AutomationSecurity = 3", "$app.DisplayAlerts = 0",
                     "$d.SaveAs2($salida, 16)", "ReleaseComObject", "[GC]::Collect()",
                     "Stop-Process -Id $id", "Test-Path -LiteralPath $job.cancelar"):
        assert esperado in s, esperado
    assert "[ref]$ruta" not in s and "[ref]$salida" not in s and "Join-Path" not in s
    assert "$app.Visible = $false" in s and "$p.Visible" not in s       # PowerPoint: nunca Visible
    ruta = tmp_path / "Carpeta con espacios y tildes ñandú" / ("Informe de gestión " + "x" * 120 + ".docx")
    trabajo = oficina.escribir_trabajo(tmp_path, [{"i": 0, "accion": "word", "ruta": ruta,
                                                   "salida": tmp_path / "0.pdf"}],
                                       OpcionesOffice(excel_hojas="activa"), tmp_path / "cancelar.txt")
    datos = trabajo.read_bytes()
    assert all(b < 128 for b in datos), "el JSON va en ASCII (\\u escapes)"
    job = json.loads(datos)
    assert job["archivos"][0]["ruta"] == str(ruta)
    assert job["opciones"] == {"word_marcadores": True, "excel_ajustar_ancho": True, "excel_hojas": "activa",
                               "pdfa": False}
    cmd = oficina.comando_powershell(script, trabajo)
    assert cmd[0].lower().endswith("powershell.exe")
    assert cmd[1:] == ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script),
                       "-Trabajo", str(trabajo)]
    assert not any("ñandú" in c or ".docx" in c for c in cmd), "las rutas de los documentos van en el JSON"
    plan_b = oficina.comando_powershell(script, trabajo, por_comando=True)
    assert plan_b[-2] == "-Command" and "[scriptblock]::Create" in plan_b[-1]


def test_comando_libreoffice(tmp_path):
    perfil = tmp_path / "perfil con espacio ñ"
    cmd = oficina.comando_libreoffice(r"C:\LibreOffice\program\soffice.exe", tmp_path / "doc.docx",
                                      tmp_path / "salida", perfil)
    assert cmd[0] == r"C:\LibreOffice\program\soffice.exe"
    assert "--headless" in cmd and "--norestore" in cmd
    env = [c for c in cmd if c.startswith("-env:UserInstallation=")]
    assert env and env[0].startswith("-env:UserInstallation=file:///") and "%20" in env[0] and " " not in env[0]
    assert cmd[cmd.index("--convert-to") + 1] == "pdf"
    assert cmd[cmd.index("--outdir") + 1] == str(tmp_path / "salida")
    assert cmd[-1] == str(tmp_path / "doc.docx")


_SOFFICE_FALSO = r"""
import sys, time
from pathlib import Path
args = sys.argv[1:]
assert "--headless" in args and "--norestore" in args, args
assert any(a.startswith("-env:UserInstallation=file:///") for a in args), args
assert args[args.index("--convert-to") + 1] == "pdf"
salida, entrada = Path(args[args.index("--outdir") + 1]), Path(args[-1])
if entrada.suffix == ".xlsx":
    time.sleep(60)                                   # se queda colgado
elif entrada.suffix == ".docx":
    import pymupdf
    with pymupdf.open() as d:
        d.new_page().insert_text((72, 72), "Convertido por LibreOffice: " + entrada.name)
        d.new_page()
        d.save(salida / (entrada.stem + ".pdf"))
# .pptx: no produce nada (como un archivo dañado)
"""


def test_libreoffice_con_programa_falso(monkeypatch, tmp_path):
    """Sin LibreOffice instalado aquí: se prueba el flujo completo con un soffice falso."""
    falso = tmp_path / "soffice_falso.py"
    falso.write_text(_SOFFICE_FALSO, encoding="utf-8")
    monkeypatch.setattr(oficina, "_cache_motores", oficina.Motores(libreoffice=r"C:\LO\program\soffice.exe"))
    original = oficina.comando_libreoffice
    vistos = []

    def comando(soffice, entrada, salida, perfil):
        cmd = original(soffice, entrada, salida, perfil)
        vistos.append(cmd)
        return [sys.executable, str(falso)] + cmd[1:]
    monkeypatch.setattr(oficina, "comando_libreoffice", comando)
    monkeypatch.setattr(oficina, "LIMITE_ARCHIVO", 3)
    doc = _docx_muestra(tmp_path / "Carta con tildes ñ.docx")
    pptx = tmp_path / "charla.pptx"
    pptx.write_bytes(b"PK\x03\x04" + b"\0" * 100)
    xlsx = tmp_path / "colgado.xlsx"
    oficina.escribir_xlsx([("Hoja", [["a"]])], xlsx)
    inicio = time.time()
    res = oficina.lote_a_pdf([str(doc), str(pptx), str(xlsx)])
    assert time.time() - inicio < 40
    r_doc, r_ppt, r_xls = res.archivos
    assert r_doc.error is None and r_doc.paginas == 2 and Path(r_doc.salida).name == "Carta con tildes ñ.pdf"
    assert "documento.docx" in _texto_pdf(r_doc.salida)                  # se convirtió una copia corta
    assert "LibreOffice no pudo convertirlo" in r_ppt.error
    assert r_xls.error.startswith("El programa tardó demasiado")
    assert all(c[0] == r"C:\LO\program\soffice.exe" for c in vistos) and len(vistos) == 3
    assert res.creados == [r_doc.salida] and len(res.errores) == 2


def test_rutas_largas_y_limite_por_tamano(monkeypatch, tmp_path):
    monkeypatch.setattr(oficina, "_cache_motores", oficina.Motores(word=True))
    carpeta = tmp_path / ("Carpeta con un nombre muy largo de la fundación " + "x" * 60)
    carpeta.mkdir()
    doc = _docx_muestra(carpeta / ("Informe trimestral de gestión de la niñez " + "y" * 50 + ".docx"))
    assert len(str(doc)) > oficina.RUTA_LARGA_OFFICE
    vistas = []

    def falso(tareas, opciones, carpeta_tmp, progress, total, etiqueta, limite, al_terminar):
        for t in tareas:
            vistas.append((t["ruta"], limite(t["i"])))
            with pymupdf.open() as d:
                d.new_page()
                d.save(t["salida"])
            al_terminar(t["i"])
        return {t["i"]: None for t in tareas}
    monkeypatch.setattr(oficina, "_convertir_con_office", falso)
    res = oficina.lote_a_pdf([str(doc)])
    ruta, tope = vistas[0]
    assert len(ruta) <= oficina.RUTA_LARGA_OFFICE and ruta.endswith(".docx") and Path(ruta) != doc
    assert tope == oficina.LIMITE_ARCHIVO
    assert res.archivos[0].salida == str(doc.with_suffix(".pdf")) and doc.with_suffix(".pdf").exists()
    monkeypatch.setattr(oficina.os.path, "getsize", lambda p: 15 * 1_048_576)
    assert oficina.limite_archivo(doc) == oficina.LIMITE_ARCHIVO + 10 * oficina.LIMITE_POR_MB


def test_sin_motor_lanza_error(monkeypatch, tmp_path):
    monkeypatch.setattr(oficina, "_cache_motores", oficina.Motores())
    doc = _docx_muestra(tmp_path / "carta.docx")
    with pytest.raises(core.UserError, match="Microsoft Office o LibreOffice"):
        oficina.a_pdf(doc, tmp_path / "carta.pdf")


def test_revisar_documento(tmp_path):
    bueno = _docx_muestra(tmp_path / "bueno.docx")
    assert oficina.revisar_documento(bueno) is None
    basura = tmp_path / "basura.docx"
    basura.write_bytes(b"esto no es un documento" * 20)
    assert "dañado" in oficina.revisar_documento(basura)
    cifrado = tmp_path / "cifrado.xlsx"
    cifrado.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 600)
    assert oficina.revisar_documento(cifrado) == oficina.MSG_CLAVE
    vacio = tmp_path / "vacio.pptx"
    vacio.write_bytes(b"")
    assert "vacío" in oficina.revisar_documento(vacio)
    assert "No se encontró" in oficina.revisar_documento(tmp_path / "no_existe.docx")
    antiguo = tmp_path / "antiguo.doc"                       # .doc es OLE de por sí: lo decide Word
    antiguo.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 600)
    assert oficina.revisar_documento(antiguo) is None


# ---------------------------------------------------------------- tiempo límite y cancelación

_SCRIPT_FALSO = r"""
param([string]$Trabajo)
$job = [IO.File]::ReadAllText($Trabajo, [Text.Encoding]::UTF8) | ConvertFrom-Json
$p = Start-Process -FilePath "$env:SystemRoot\System32\PING.EXE" -ArgumentList '-n','300','127.0.0.1' -WindowStyle Hidden -PassThru
[Console]::Out.WriteLine((ConvertTo-Json -Compress -InputObject @{evento='proceso'; pid=$p.Id; nombre='PING'; inicio=$p.StartTime.ToFileTimeUtc()}))
# Un proceso ajeno anotado con otra hora de inicio (como si el número se hubiera reutilizado): no se toca.
[Console]::Out.WriteLine((ConvertTo-Json -Compress -InputObject @{evento='proceso'; pid=[int]$env:RONYPDF_PID_AJENO; nombre='PING'; inicio=1}))
[Console]::Out.WriteLine((ConvertTo-Json -Compress -InputObject @{evento='inicio'; i=0}))
[Console]::Out.Flush()
Start-Sleep -Seconds 300
"""


@pytest.mark.skipif(not WINDOWS, reason="solo Windows")
@pytest.mark.parametrize("caso", ["tiempo", "cancelar"])
def test_tiempo_limite_y_cancelar_terminan_solo_lo_propio(caso, monkeypatch, tmp_path):
    ajeno = subprocess.Popen([os.path.join(os.environ["SystemRoot"], "System32", "PING.EXE"), "-n", "300",
                              "127.0.0.1"], stdout=subprocess.DEVNULL, creationflags=SIN_VENTANA)
    try:
        monkeypatch.setenv("RONYPDF_PID_AJENO", str(ajeno.pid))
        monkeypatch.setattr(oficina, "_SCRIPT_PS", _SCRIPT_FALSO)
        monkeypatch.setattr(oficina, "ESPERA_CANCELAR", 1.5)
        propios = []
        sesion_orig = oficina._SesionOffice._evento

        def evento(self, crudo):
            sesion_orig(self, crudo)
            propios[:] = [p for p in self.procesos if p != ajeno.pid]
        monkeypatch.setattr(oficina._SesionOffice, "_evento", evento)

        class Cancelado(Exception):
            pass

        def progreso(i, n, msg):
            if caso == "cancelar" and "archivo 0" in msg:
                raise Cancelado()

        tarea = {"i": 0, "accion": "word", "ruta": str(tmp_path / "x.docx"), "salida": str(tmp_path / "x.pdf")}
        inicio = time.time()
        if caso == "tiempo":
            res = oficina._convertir_con_office([tarea], OpcionesOffice(), tmp_path, progreso, 1,
                                                lambda i: "" if i is None else f"archivo {i}",
                                                lambda i: 2, lambda i: None)
            assert res[0][0].startswith("El programa tardó demasiado")
        else:
            with pytest.raises(Cancelado):
                oficina._convertir_con_office([tarea], OpcionesOffice(), tmp_path, progreso, 1,
                                              lambda i: "" if i is None else f"archivo {i}",
                                              lambda i: None, lambda i: None)
        assert time.time() - inicio < 30
        assert propios, "el script anotó su proceso"
        time.sleep(0.5)
        assert not oficina.proceso_vivo(propios[0]), "el proceso creado por el script se terminó"
        assert oficina.proceso_vivo(ajeno.pid), "un proceso ajeno nunca se cierra"
    finally:
        ajeno.kill()


# ---------------------------------------------------------------- escritores

def test_escribir_docx_valido(tmp_path):
    doc = _docx_muestra(tmp_path / "muestra.docx")
    with zipfile.ZipFile(doc) as z:
        assert z.testzip() is None
        nombres = set(z.namelist())
        assert {"[Content_Types].xml", "_rels/.rels", "word/document.xml", "word/styles.xml",
                "word/_rels/document.xml.rels"} <= nombres
        for n in nombres:
            ET.fromstring(z.read(n))                         # XML bien formado
        xml = z.read("word/document.xml").decode("utf-8")
    assert "Niñez " in xml and "educación" in xml and "Informe de gestión 2025" in xml
    raiz = _xml_de(doc, "word/document.xml")
    estilos = [e.get(f"{W}val") for e in raiz.iter(f"{W}pStyle")]
    assert estilos == ["Heading1", "Heading2", "Heading1"]
    assert len(list(raiz.iter(f"{W}pageBreakBefore"))) == 2
    assert list(raiz.iter(f"{W}b")) and list(raiz.iter(f"{W}i")) and list(raiz.iter(f"{W}tab"))
    estilos_xml = _xml_de(doc, "word/styles.xml")
    ids = {s.get(f"{W}styleId") for s in estilos_xml.iter(f"{W}style")}
    assert {"Normal", "Heading1", "Heading2"} <= ids
    # caracteres de control (que harían que Word pida reparar) se eliminan
    raro = tmp_path / "raro.docx"
    oficina.escribir_docx([Parrafo("hola\x00\x07 mundo <&>")], raro)
    assert "hola mundo &lt;&amp;&gt;" in zipfile.ZipFile(raro).read("word/document.xml").decode()


def test_escribir_xlsx_valido(tmp_path):
    xlsx = tmp_path / "libro.xlsx"
    oficina.escribir_xlsx([
        ("Presupuesto", [["Concepto", "Cédula", "Valor", "IVA", "Activo"],
                         ["Talleres de niñez", Codigo("0012345678"), 1250000, Porcentaje(0.19), True],
                         ["Refrigerios", Codigo("79.456.123"), 800000.5, Porcentaje(0.05), False]]),
        ("Hoja: con/raros*y un nombre larguísimo de más de treinta y un caracteres", [["a"], [1]]),
        ("presupuesto", [["repetida"]]),
    ], xlsx)
    with zipfile.ZipFile(xlsx) as z:
        assert z.testzip() is None
        for n in z.namelist():
            ET.fromstring(z.read(n))
        assert {"[Content_Types].xml", "_rels/.rels", "xl/workbook.xml", "xl/_rels/workbook.xml.rels",
                "xl/styles.xml", "xl/worksheets/sheet1.xml", "xl/worksheets/sheet3.xml"} <= set(z.namelist())
    nombres = [s.get("name") for s in _xml_de(xlsx, "xl/workbook.xml").iter(f"{S}sheet")]
    assert nombres[0] == "Presupuesto" and len(nombres[1]) <= 31 and not set(nombres[1]) & set("[]:*?/\\")
    assert nombres[2].lower() != nombres[0].lower()
    c = _celdas(xlsx)
    assert c["A1"] == ("inlineStr", "Concepto", "1")                      # encabezado en negrita
    assert c["A2"] == ("inlineStr", "Talleres de niñez", None)
    assert c["B2"] == ("inlineStr", "0012345678", "5")                    # cédula como texto «@»
    assert c["C2"] == (None, "1250000", "2") and c["C3"] == (None, "800000.5", "3")
    assert c["D2"] == (None, "0.19", "4") and c["E2"][0] == "b"


@pytest.mark.parametrize("texto,esperado", [
    ("1.250.000", 1250000), ("1.250.000,50", 1250000.5), ("$ 800.000", 800000), ("$800.000", 800000),
    ("12,5 %", Porcentaje(0.125)), ("100%", Porcentaje(1.0)), ("(3.500)", -3500), ("-1.250", -1250),
    ("$ -45.000", -45000), ("800.000-", -800000), ("COP 15.000", 15000), ("1250,5", 1250.5), ("2025", 2025),
    ("12.5", 12.5), ("1,250,000.75", 1250000.75), ("1 250 000", 1250000), ("0", 0),
    ("0012345678", None), ("1020304050", None), ("3001234567", None), ("15/03/2025", None),
    ("2025-04-01", None), ("900.123.456-7", None), ("Ana", None), ("", None), ("1.2.3", None),
    ("12345678901234567890", None),
])
def test_numero_colombiano(texto, esperado):
    valor = oficina.numero_colombiano(texto)
    if esperado is None:
        assert valor is None
    else:
        assert valor == pytest.approx(esperado) and type(valor) is type(esperado)


def test_valor_celda_y_columnas_de_codigos():
    assert oficina.valor_celda("1.250.000") == 1250000
    assert isinstance(oficina.valor_celda("15/03/2025"), Codigo)
    assert isinstance(oficina.valor_celda("900.123.456-7"), Codigo)
    assert oficina.valor_celda("79.456.123", como_texto=True) == "79.456.123"
    assert isinstance(oficina.valor_celda("79.456.123", como_texto=True), Codigo)
    assert oficina.valor_celda("Pagado") == "Pagado"
    for enc in ("Cédula", "C.C.", "NIT", "No. de cuenta", "Teléfono", "Celular", "Código", "Documento"):
        assert oficina.es_columna_de_codigos(enc), enc
    for enc in ("Valor", "Total", "Dirección", "Saldo", "Fecha"):
        assert not oficina.es_columna_de_codigos(enc), enc
    usados: set[str] = set()
    assert oficina.nombre_hoja("Página 1", usados) == "Página 1"
    assert oficina.nombre_hoja("página 1", usados) == "página 1 (2)"
    assert oficina.nombre_hoja("a/b:c*?[d]", usados) == "a-b-c---d-"
    assert len(oficina.nombre_hoja("x" * 40, usados)) == 31
    assert oficina.nombre_hoja("x" * 40, usados) == "x" * 27 + " (2)"
    assert oficina.nombre_hoja("''", usados) == "Hoja"


# ---------------------------------------------------------------- PDF → Word (interno)

def test_pdf_a_word_interno_informe_y_clave(samples, tmp_path):
    out = tmp_path / "informe.docx"
    res = oficina.pdf_a_word(samples["a"], out, modo="interno")
    assert res.modo == "interno" and res.paginas == 5
    xml = zipfile.ZipFile(out).read("word/document.xml").decode("utf-8")
    for i in range(1, 6):
        assert f"Informe - Página {i}" in xml
    assert xml.count("<w:pageBreakBefore/>") == 4
    # PDF con contraseña
    out2 = tmp_path / "secreto.docx"
    oficina.pdf_a_word(samples["locked"], out2, modo="interno", password="1234")
    assert "Informe - Página 3" in zipfile.ZipFile(out2).read("word/document.xml").decode("utf-8")
    with pytest.raises(core.PasswordError):
        oficina.pdf_a_word(samples["locked"], tmp_path / "x.docx", modo="interno", password="mala")


def test_pdf_a_word_interno_escaneado_con_ocr(scanned, tmp_path):
    out = tmp_path / "escaneado.docx"
    res = oficina.pdf_a_word(scanned, out, modo="auto")                # escaneado → interno con OCR
    assert res.modo == "interno"
    xml = zipfile.ZipFile(out).read("word/document.xml").decode("utf-8")
    assert "niñez" in xml.lower()
    assert any("reconoció el texto" in a for a in res.avisos)


def test_pdf_a_word_escaneado_con_sello_digital(scanned, tmp_path):
    """Un escaneo con un sello de radicado digital encima: antes salía un Word con solo el sello."""
    sellado = tmp_path / "sellado.pdf"
    with pymupdf.open(scanned) as d:
        for p in d:
            p.insert_text((300, 30), "Radicado No. 2026-004512 del 09/11/2026", fontsize=9)
        d.save(sellado)
    out = tmp_path / "sellado.docx"
    res = oficina.pdf_a_word(sellado, out, modo="auto")                # se trata como escaneado → interno
    assert res.modo == "interno"
    xml = zipfile.ZipFile(out).read("word/document.xml").decode("utf-8")
    assert "niñez" in xml.lower() and "cooperativa" in xml.lower()
    assert any("reconoció el texto de 2 páginas" in a for a in res.avisos)


def test_pdf_a_word_interno_titulos_y_estilos(tmp_path):
    src = tmp_path / "titulos.pdf"
    with pymupdf.open() as d:
        p = d.new_page()
        p.insert_text((72, 80), "Informe anual de gestión", fontsize=24)
        p.insert_text((72, 130), "Resumen ejecutivo", fontsize=16)
        y = 170
        for linea in ("Este año la fundación amplió sus programas de educa-",
                      "ción para la niñez en cinco municipios de Antioquia y",
                      "Chocó, con apoyo de voluntarios y aliados locales."):
            p.insert_text((72, y), linea, fontsize=11)
            y += 14
        p.insert_text((72, y + 20), "Dato destacado:", fontsize=11, fontname="hebo")
        p.insert_text((72, y + 34), "cifras sujetas a revisión", fontsize=11, fontname="heit")
        d.save(src)
    out = tmp_path / "titulos.docx"
    oficina.pdf_a_word(src, out, modo="interno")
    raiz = _xml_de(out, "word/document.xml")
    parrafos = []
    for p in raiz.iter(f"{W}p"):
        estilo = p.find(f"{W}pPr/{W}pStyle")
        texto = "".join(t.text or "" for t in p.iter(f"{W}t"))
        parrafos.append((estilo.get(f"{W}val") if estilo is not None else None, texto, p))
    assert parrafos[0][:2] == ("Heading1", "Informe anual de gestión")
    assert parrafos[1][:2] == ("Heading2", "Resumen ejecutivo")
    cuerpo = parrafos[2][1]
    assert "educación para la niñez" in cuerpo and cuerpo.endswith("aliados locales.")
    negrita = [p for e, t, p in parrafos if "Dato destacado" in t][0]
    cursiva = [p for e, t, p in parrafos if "sujetas a revisión" in t][0]
    assert list(negrita.iter(f"{W}b")) and list(cursiva.iter(f"{W}i"))


# ---------------------------------------------------------------- PDF → Word (con Word): nunca oculto sin permiso

def test_pdf_a_word_con_word_no_se_invoca_oculto(monkeypatch, samples, tmp_path):
    monkeypatch.setattr(oficina, "_cache_motores", oficina.Motores(word=True))
    llamadas = []

    def falso(tareas, opciones, carpeta, progress, total, etiqueta, limite, al_terminar):
        t = tareas[0]
        llamadas.append((dict(t), limite(0), etiqueta(0)))
        oficina.escribir_docx([Parrafo("Convertido por Word")], t["salida"])
        al_terminar(0)
        return {0: None}
    monkeypatch.setattr(oficina, "_convertir_con_office", falso)

    monkeypatch.setattr(oficina, "aviso_word_desactivado", lambda: False)
    out = tmp_path / "visible.docx"
    res = oficina.pdf_a_word(samples["a"], out, modo="word")
    tarea, tope, etiqueta = llamadas[-1]
    assert tarea["accion"] == "pdf_a_word" and tarea["visible"] is True and tope is None
    assert tarea["salida"] == os.path.abspath(out) and "Aceptar" in etiqueta
    assert res.modo == "word" and res.avisos and out.exists()

    monkeypatch.setattr(oficina, "aviso_word_desactivado", lambda: True)
    out2 = tmp_path / "oculto.docx"
    oficina.pdf_a_word(samples["a"], out2, modo="word")
    tarea, tope, _ = llamadas[-1]
    assert tarea["visible"] is False and tope == oficina.LIMITE_WORD_BASE + 2 * 5
    assert tarea["salida"] != str(out2) and out2.exists()                 # pasa por un temporal

    # con contraseña se descifra a un temporal que después se borra
    oficina.pdf_a_word(samples["locked"], tmp_path / "clave.docx", modo="word", password="1234")
    fuente = Path(llamadas[-1][0]["ruta"])
    assert fuente != samples["locked"] and not fuente.exists()

    monkeypatch.setattr(oficina, "_cache_motores", oficina.Motores())
    with pytest.raises(core.UserError, match="No se encontró Microsoft Word"):
        oficina.pdf_a_word(samples["a"], tmp_path / "y.docx", modo="word")
    assert oficina.pdf_a_word(samples["a"], tmp_path / "auto.docx", modo="auto").modo == "interno"


def test_pdf_a_word_tiempo_agotado(monkeypatch, samples, tmp_path):
    monkeypatch.setattr(oficina, "_cache_motores", oficina.Motores(word=True))
    monkeypatch.setattr(oficina, "aviso_word_desactivado", lambda: True)
    monkeypatch.setattr(oficina, "_convertir_con_office",
                        lambda *a, **k: {0: (oficina.MSG_TIEMPO.format(s=130), "")})
    with pytest.raises(core.UserError, match="Word tardó demasiado"):
        oficina.pdf_a_word(samples["a"], tmp_path / "z.docx", modo="word")
    assert not (tmp_path / "z.docx").exists()


# ---------------------------------------------------------------- PDF → Excel

def test_pdf_a_excel_con_y_sin_lineas(tmp_path):
    for lineas in (True, False):
        src = _tabla_pdf(tmp_path / f"tabla_{lineas}.pdf", lineas)
        out = tmp_path / f"tabla_{lineas}.xlsx"
        tablas, filas = oficina.pdf_a_excel(src, out)
        assert (tablas, filas) == (1, 4), lineas
        c = _celdas(out)
        assert c["A1"][1] == "Nombre" and c["A1"][2] == "1"
        assert c["A2"][1] == "Ana Pérez"
        assert c["B2"] == ("inlineStr", "0012345678", "5")                # cédulas como texto
        assert c["B3"] == ("inlineStr", "79.456.123", "5")
        assert c["B4"] == ("inlineStr", "1020304050", "5")
        assert c["C2"] == (None, "1250000", "2")                          # 1.250.000 como número
        assert c["C3"] == (None, "800000", "2") and c["C4"] == (None, "1250000.5", "3")
        assert c["D2"][0] == "inlineStr" and c["D2"][1] == "15/03/2025"   # fechas como texto
        assert c["E2"] == (None, "0.125", "4")
        with zipfile.ZipFile(out) as z:
            for n in z.namelist():
                ET.fromstring(z.read(n))


def test_pdf_a_excel_sin_lineas_numeros_a_la_derecha_y_dos_tablas(tmp_path):
    """Como un PDF hecho desde Excel: sin bordes, encabezados a la izquierda y valores a la derecha, celdas
    vacías, palabras sueltas dentro de una celda y texto corrido entre dos tablas."""
    def tabla(p, y, filas, anchos):
        for r, fila in enumerate(filas):
            x = 50
            for c, (v, w) in enumerate(zip(fila, anchos)):
                derecha = c == len(fila) - 1 and r > 0
                p.insert_text((x + w - pymupdf.get_text_length(v, fontsize=10) if derecha else x, y + r * 18),
                              v, fontsize=10)
                x += w
    src = tmp_path / "excel_sin_bordes.pdf"
    with pymupdf.open() as d:
        p = d.new_page()
        p.insert_textbox(pymupdf.Rect(50, 40, 560, 110), "Informe de gestión. Durante el trimestre la fundación "
                         "atendió a familias de Urabá según el detalle siguiente.", fontsize=10, align=3)
        tabla(p, 140, [["Donante", "Municipio", "Valor"], ["Empresa 1", "Turbo", "1.250.000"],
                       ["Empresa 2", "", "300.000"], ["Empresa 3", "Carepa", "45.000,50"]], [140, 140, 150])
        p.insert_textbox(pymupdf.Rect(50, 230, 560, 280), "Los recursos provinieron de estos aportantes.", fontsize=10)
        tabla(p, 300, [["Aportante", "NIT", "Valor"], ["Empresa Uno S.A.S.", "900.123.456-8", "$ 12.500.000"],
                       ["Cooperativa Dos", "800.987.654-1", "$ 800.000"]], [170, 140, 110])
        d.save(src)
    with pymupdf.open(src) as d:
        tablas = oficina._tablas_de_pagina(d[0])
    assert tablas == [
        [["Donante", "Municipio", "Valor"], ["Empresa 1", "Turbo", "1.250.000"], ["Empresa 2", "", "300.000"],
         ["Empresa 3", "Carepa", "45.000,50"]],
        [["Aportante", "NIT", "Valor"], ["Empresa Uno S.A.S.", "900.123.456-8", "$ 12.500.000"],
         ["Cooperativa Dos", "800.987.654-1", "$ 800.000"]]]
    out = tmp_path / "excel_sin_bordes.xlsx"
    assert oficina.pdf_a_excel(src, out) == (2, 7)
    c = _celdas(out)
    assert c["C2"] == (None, "1250000", "2")
    assert c.get("B3", (None, "", None))[1] == ""                       # la celda vacía sigue vacía
    assert c["A4"][1] == "Empresa 3" and c["C4"][0] is None and float(c["C4"][1]) == 45000.5


def test_pdf_a_excel_hojas_todo_csv_y_sin_tablas(tmp_path, samples):
    src = _tabla_pdf(tmp_path / "dos.pdf", True, paginas=2)
    por_tabla = tmp_path / "por_tabla.xlsx"
    assert oficina.pdf_a_excel(src, por_tabla) == (2, 8)
    nombres = [s.get("name") for s in _xml_de(por_tabla, "xl/workbook.xml").iter(f"{S}sheet")]
    assert nombres == ["Página 1", "Página 2"]
    todo = tmp_path / "todo.xlsx"
    assert oficina.pdf_a_excel(src, todo, agrupar="todo") == (2, 7)        # el encabezado no se repite
    assert len(_xml_de(todo, "xl/workbook.xml").findall(f".//{S}sheet")) == 1
    c = _celdas(todo)
    assert c["A1"][1] == "Nombre" and c["A7"][1] == "Niñez Ruiz" and "A8" not in c
    solo2 = tmp_path / "solo2.xlsx"
    assert oficina.pdf_a_excel(src, solo2, paginas="2") == (1, 4)
    csv_out = tmp_path / "tablas.csv"
    oficina.pdf_a_excel(src, csv_out, agrupar="todo")
    crudo = csv_out.read_bytes()
    assert crudo.startswith(b"\xef\xbb\xbf")
    lineas = crudo.decode("utf-8-sig").splitlines()
    assert lineas[0] == "Nombre;Cédula;Valor;Fecha;Avance"
    assert lineas[1] == "Ana Pérez;0012345678;1250000;15/03/2025;12,5%"
    assert "1250000,5" in lineas[3]
    with pytest.raises(core.UserError, match="No se encontraron tablas"):
        oficina.pdf_a_excel(samples["b"], tmp_path / "nada.xlsx")
    with pytest.raises(core.UserError):
        oficina.pdf_a_excel(src, tmp_path / "mal.txt")


def test_pdf_a_excel_funciona_sin_numpy(tmp_path):
    """El .exe no lleva numpy: find_tables debe ir con use_layout=False."""
    src = _tabla_pdf(tmp_path / "t.pdf", True)
    out = tmp_path / "t.xlsx"
    raiz = Path(__file__).resolve().parents[1]
    codigo = ("import sys; sys.modules['numpy'] = None; sys.modules['pymupdf_layout'] = None; "
              f"sys.path.insert(0, {str(raiz)!r}); from ronypdf import oficina; "
              f"print(oficina.pdf_a_excel({str(src)!r}, {str(out)!r}))")
    r = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    assert "(1, 4)" in r.stdout and out.exists()


# ---------------------------------------------------------------- Office real (se saltan sin Office)

_ABRIR_EN_WORD = r"""
param([string]$Ruta, [string]$Parar)
$antes = @(Get-Process WINWORD -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
$w = New-Object -ComObject Word.Application
$w.Visible = $false
$nuevo = @(Get-Process WINWORD | ForEach-Object { $_.Id } | Where-Object { $antes -notcontains $_ })
$d = $w.Documents.Open($Ruta)
[Console]::Out.WriteLine(($nuevo -join ','))
[Console]::Out.Flush()
$fin = (Get-Date).AddSeconds(120)
while (-not (Test-Path -LiteralPath $Parar) -and (Get-Date) -lt $fin) { Start-Sleep -Milliseconds 200 }
$d.Close([ref]0)
$w.Quit([ref]0)
[void][Runtime.InteropServices.Marshal]::ReleaseComObject($w)
"""


@con_word
def test_word_real_docx_propio_y_archivo_abierto_por_el_usuario(tmp_path):
    antes = _procesos_office()
    carpeta = tmp_path / "Carpeta de la fundación ñ"
    carpeta.mkdir()
    doc = _docx_muestra(carpeta / "Carta de gestión con tildes áéíóú.docx")
    # «el usuario» tiene el documento abierto en su propio Word
    script = tmp_path / "abrir.ps1"
    script.write_text(_ABRIR_EN_WORD, encoding="utf-8-sig")
    parar = tmp_path / "parar.txt"
    usuario = subprocess.Popen([oficina._powershell_exe(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                                str(script), "-Ruta", str(doc), "-Parar", str(parar)],
                               stdout=subprocess.PIPE, text=True, creationflags=SIN_VENTANA)
    try:
        pids_usuario = {int(x) for x in usuario.stdout.readline().strip().split(",") if x}
        assert pids_usuario, "no se pudo abrir el Word de prueba"
        pdf = carpeta / "carta.pdf"
        n = oficina.a_pdf(doc, pdf)
        assert n == 3
        texto = _texto_pdf(pdf)
        assert "Informe de gestión 2025" in texto and "Niñez" in texto and "Tercera página" in texto
        with pymupdf.open(pdf) as d:
            assert [t[1] for t in d.get_toc()] == ["Informe de gestión 2025", "Segunda sección", "Tercera página"]
        assert all(oficina.proceso_vivo(p) for p in pids_usuario), "nunca se cierra el Word del usuario"
        _sin_procesos_nuevos(antes, pids_usuario)
    finally:
        parar.write_text("ya")
        try:
            usuario.wait(timeout=60)
        except subprocess.TimeoutExpired:
            usuario.kill()
    _sin_procesos_nuevos(antes)


@con_excel
def test_excel_real_xlsx_propio_y_ajuste_al_ancho(tmp_path):
    antes = _procesos_office()
    xlsx = tmp_path / "Presupuesto 2025.xlsx"
    oficina.escribir_xlsx([("Presupuesto", [["Concepto", "Valor"], ["Talleres de niñez", 1250000],
                                            ["Refrigerios", 800000]]),
                           ("Segunda", [["Hoja dos"]])], xlsx)
    pdf = tmp_path / "presupuesto.pdf"
    assert oficina.a_pdf(xlsx, pdf) == 2
    texto = _texto_pdf(pdf)
    assert "Talleres de niñez" in texto and "Hoja dos" in texto
    assert oficina.a_pdf(xlsx, tmp_path / "activa.pdf", OpcionesOffice(excel_hojas="activa")) == 1
    ancho = tmp_path / "ancho.xlsx"
    oficina.escribir_xlsx([("Ancha", [[f"Columna {c}" for c in range(40)]]
                            + [[c * 1000 + r for c in range(40)] for r in range(5)])], ancho,
                          ajustar_impresion=False)
    ajustado = oficina.a_pdf(ancho, tmp_path / "ajustado.pdf", OpcionesOffice(excel_ajustar_ancho=True))
    cortado = oficina.a_pdf(ancho, tmp_path / "cortado.pdf", OpcionesOffice(excel_ajustar_ancho=False))
    assert ajustado == 1 and cortado > 1
    _sin_procesos_nuevos(antes)


@con_word
def test_lote_unir_mezcla_office_pdf_imagen_y_errores(samples, tmp_path):
    antes = _procesos_office()
    doc = _docx_muestra(tmp_path / "carta.docx")
    basura = tmp_path / "basura.docx"
    basura.write_bytes(b"contenido basura, no es Word" * 40)
    roto = tmp_path / "roto.docx"
    with zipfile.ZipFile(roto, "w") as z:                    # ZIP válido con XML roto: lo rechaza Word
        z.writestr("[Content_Types].xml", "<Types><roto")
        z.writestr("word/document.xml", "no es xml <<<")
    unido = tmp_path / "unido.pdf"
    res = oficina.lote_a_pdf([str(doc), str(basura), str(samples["b"]), str(roto), str(samples["png"])],
                             unir_en=str(unido))
    assert res.creados == [str(unido)] and res.paginas == 3 + 3 + 1
    errores = {Path(r).name: m for r, m in res.errores}
    assert set(errores) == {"basura.docx", "roto.docx"}
    assert "dañado" in errores["basura.docx"] and "dañado" in errores["roto.docx"]
    with pymupdf.open(unido) as d:
        assert d.page_count == 7
        assert "Informe de gestión 2025" in d[0].get_text() and "Anexo 1" in d[3].get_text()
        toc = d.get_toc()
    nivel1 = [(t, p) for lvl, t, p in toc if lvl == 1]
    assert nivel1 == [("carta", 1), ("anexo", 4), ("imagen", 7)]
    assert [2, "Informe de gestión 2025", 1] in toc                       # marcadores internos de Word
    # sin unir: cada PDF junto a su original, sin reemplazar archivos que ya existan
    (tmp_path / "carta.pdf").write_bytes(b"ya existia")
    res = oficina.lote_a_pdf([str(doc), str(samples["png"])], carpeta=str(tmp_path))
    salidas = [Path(r.salida).name for r in res.archivos]
    assert salidas == ["carta (2).pdf", "imagen.pdf"] and (tmp_path / "carta.pdf").read_bytes() == b"ya existia"
    _sin_procesos_nuevos(antes)


_CREAR_PPTX = r"""
param([string]$Ruta)
$p = New-Object -ComObject PowerPoint.Application
$pres = $p.Presentations.Add(0)
$s = $pres.Slides.Add(1, 1)
$s.Shapes.Item(1).TextFrame.TextRange.Text = 'Presentacion de la fundacion'
$s.Shapes.Item(2).TextFrame.TextRange.Text = 'Programa de lectura'
$s2 = $pres.Slides.Add(2, 2)
$s2.Shapes.Item(1).TextFrame.TextRange.Text = 'Resultados del ano'
$pres.SaveAs($Ruta)
$pres.Close()
if ($p.Presentations.Count -eq 0) { $p.Quit() }
[void][Runtime.InteropServices.Marshal]::ReleaseComObject($p)
"""


@con_ppt
def test_powerpoint_real(tmp_path):
    antes = _procesos_office()
    script = tmp_path / "crear.ps1"
    script.write_text(_CREAR_PPTX, encoding="utf-8-sig")
    pptx = tmp_path / "presentación.pptx"
    subprocess.run([oficina._powershell_exe(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                    "-Ruta", str(pptx)], check=True, timeout=120, creationflags=SIN_VENTANA)
    _sin_procesos_nuevos(antes)
    pdf = tmp_path / "presentacion.pdf"
    assert oficina.a_pdf(pptx, pdf) == 2
    texto = _texto_pdf(pdf)
    assert "Presentacion de la fundacion" in texto and "Resultados del ano" in texto
    _sin_procesos_nuevos(antes)


# ---------------------------------------------------------------- interfaz

from PySide6.QtWidgets import QApplication  # noqa: E402

from ronypdf import oficina_ui, widgets  # noqa: E402
from ronypdf.gui import MainWindow, _light_palette  # noqa: E402
from ronypdf.style import QSS  # noqa: E402


@pytest.fixture(scope="module")
def win():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setPalette(_light_palette())
    app.setStyleSheet(QSS)
    w = MainWindow()
    w.resize(1200, 800)
    w.show()
    app.processEvents()
    return w


@pytest.fixture
def done(monkeypatch):
    calls = []

    def fake_done(parent, title, detail="", file=None, folder=None):
        calls.append((title, detail, file, folder))

    for mod in (oficina_ui, widgets):
        monkeypatch.setattr(mod, "show_done", fake_done, raising=False)
    monkeypatch.setattr(widgets.QMessageBox, "warning", lambda *a, **k: calls.append(("warning", a[2])))
    monkeypatch.setattr(widgets.QMessageBox, "information", lambda *a, **k: calls.append(("info", a[2])))
    monkeypatch.setattr(widgets.QMessageBox, "question", lambda *a, **k: widgets.QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(oficina_ui.QFileDialog, "getSaveFileName",
                        lambda *a, **k: pytest.fail("diálogo de guardar sin parchear"))
    return calls


def wait(app_win, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        QApplication.processEvents()
        if not widgets.ctx.busy:
            QApplication.processEvents()
            return
        time.sleep(0.01)
    raise TimeoutError


def test_ui_registro_menu_busqueda_e_inicio(win, tmp_path, done):
    from ronypdf import extensiones
    sb = win.sidebar
    assert {"office2pdf", "pdf2office"} <= set(sb.items)
    from ronypdf.gui import SECTIONS
    convertir = [k for s in SECTIONS if s[0] == "convertir" for k, *_ in s[3]]
    assert convertir.index("office2pdf") + 1 == convertir.index("pdf2office")
    sb.set_collapsed(False)
    sb.search.setText("Excel")
    assert sb.items["office2pdf"].isVisible() and sb.items["pdf2office"].isVisible()
    assert not sb.items["merge"].isVisible()
    sb.search.setText("")
    tarjetas = {k: d for k, _t, _ti, d in extensiones.inicio()}
    assert tarjetas["office2pdf"] == "Convierte documentos de Office"
    assert tarjetas["pdf2office"] == "Para editar el contenido"
    assert ".docx" in extensiones.tipos_inicio() and "Word, Excel y PowerPoint" in \
        extensiones.filtro_inicio(widgets.PDF_AND_IMAGES_FILTER)
    # soltar un .docx en el inicio abre «Word y Excel a PDF» con el archivo en la lista
    doc = _docx_muestra(tmp_path / "carta.docx")
    win.navigate("home")
    win.open_files([str(doc)])
    page = win.pages["office2pdf"]
    assert win.stack.currentWidget() is page and str(doc) in page.list.paths()
    page.list.clear_all()


def test_ui_lista_y_resultados_por_fila(win, samples, tmp_path, done, monkeypatch):
    page = win.pages["office2pdf"]
    win.navigate("office2pdf")
    page.list.clear_all()
    doc = _docx_muestra(tmp_path / "carta.docx")
    xlsx = tmp_path / "presupuesto.xlsx"
    oficina.escribir_xlsx([("Hoja", [["a"], [1]])], xlsx)
    pptx = tmp_path / "charla.pptx"
    pptx.write_bytes(b"PK\x03\x04 falso")
    txt = tmp_path / "notas.txt"
    txt.write_text("no")
    page.accept_files([str(doc), str(xlsx), str(pptx), str(samples["b"]), str(samples["png"]), str(txt)])
    assert [it.text(1) for it in page.list.items()] == ["Word", "Excel", "PowerPoint", "PDF", "Imagen"]
    page.puede = True
    page.refresh()
    chips = [page._chips.itemAt(i).widget().text() for i in range(page._chips.count())]   # resumen en chips
    assert page.action.isEnabled() and "4 de Office" not in chips and "3 de Office" in chips

    recibido = {}

    def falso_lote(progress=None, **kw):
        recibido.update(kw)
        archivos = kw["archivos"]
        res = oficina.ResultadoLote(archivos=[
            oficina.ResultadoArchivo(archivos[0], str(tmp_path / "carta.pdf"), 3),
            oficina.ResultadoArchivo(archivos[1], str(tmp_path / "presupuesto.pdf"), 1),
            oficina.ResultadoArchivo(archivos[2], error=oficina.MSG_CLAVE),
            oficina.ResultadoArchivo(archivos[3], paginas=3, sin_cambios=True),
            oficina.ResultadoArchivo(archivos[4], error="No se pudo leer la imagen."),
        ])
        res.creados = [str(tmp_path / "carta.pdf"), str(tmp_path / "presupuesto.pdf")]
        res.errores = [(archivos[2], oficina.MSG_CLAVE), (archivos[4], "No se pudo leer la imagen.")]
        res.paginas = 4
        return res
    monkeypatch.setattr(oficina, "lote_a_pdf", falso_lote)
    page.c_ancho.setChecked(False)
    page.r_activa.setChecked(True)
    page.run()
    wait(win)
    op = recibido["opciones"]
    assert op.excel_ajustar_ancho is False and op.excel_hojas == "activa" and op.word_marcadores is True
    assert "unir_en" not in recibido and "destinos" not in recibido
    filas = [it.text(3) for it in page.list.items()]
    assert filas == ["3 páginas", "1 página", oficina.MSG_CLAVE, "Ya es un PDF: se deja igual",
                     "No se pudo leer la imagen."]
    titulo, detalle, archivo, carpeta = done[-1]
    assert titulo == "2 PDF creados" and "2 archivos no se pudieron convertir" in detalle
    assert carpeta == str(tmp_path)

    # unir: pregunta dónde guardar
    unido = tmp_path / "unido.pdf"
    monkeypatch.setattr(oficina_ui, "ask_save", lambda *a, **k: str(unido))

    def falso_unir(progress=None, **kw):
        recibido.update(kw)
        res = oficina.ResultadoLote(archivos=[oficina.ResultadoArchivo(a, paginas=1) for a in kw["archivos"]])
        res.creados, res.paginas = [kw["unir_en"]], 5
        return res
    monkeypatch.setattr(oficina, "lote_a_pdf", falso_unir)
    page.r_unir.setChecked(True)
    page.run()
    wait(win)
    assert recibido["unir_en"] == str(unido)
    assert done[-1][0] == "PDF unido creado" and done[-1][2] == str(unido) and "5 páginas" in done[-1][1]
    page.r_cada.setChecked(True)
    page.list.clear_all()


@con_word
def test_ui_conversion_real_un_archivo(win, tmp_path, done, monkeypatch):
    page = win.pages["office2pdf"]
    win.navigate("office2pdf")
    page.list.clear_all()
    doc = _docx_muestra(tmp_path / "informe.docx")
    page.accept_files([str(doc)])
    out = tmp_path / "elegido.pdf"
    monkeypatch.setattr(oficina_ui, "ask_save", lambda *a, **k: str(out))
    page.run()
    wait(win, 120)
    assert done[-1][0] == "PDF creado" and done[-1][2] == str(out), done
    assert page.list.items()[0].text(3) == "3 páginas" and out.exists()
    page.list.clear_all()


def test_ui_sin_office_lo_explica(win, monkeypatch, done, tmp_path):
    monkeypatch.setattr(oficina, "_cache_motores", oficina.Motores())
    page = oficina_ui.OfficeToPdfPage()
    assert page.estado.property("tone") == "warn" and page.bloque_estado.isVisibleTo(page)
    assert "Microsoft Office o LibreOffice" in page.estado_texto.text()
    page.accept_files([str(_docx_muestra(tmp_path / "carta.docx"))])
    assert page.list.topLevelItemCount() == 1 and not page.action.isEnabled()
    otra = oficina_ui.PdfToOfficePage()
    assert not otra.r_diseno.isEnabled() and otra.r_texto.isChecked()


def test_ui_pdf_a_excel_y_word(win, tmp_path, done, monkeypatch):
    page = win.pages["pdf2office"]
    win.navigate("pdf2office")
    src = _tabla_pdf(tmp_path / "extracto.pdf", True)
    page.picker.set_path(str(src))
    assert page.action.isEnabled()
    page.r_excel.setChecked(True)
    assert page.caja_excel.isVisibleTo(page) and not page.caja_word.isVisibleTo(page)
    out = tmp_path / "extracto.xlsx"
    pedidos = []
    monkeypatch.setattr(oficina_ui, "pedir_destino", lambda *a: pedidos.append(a) or str(out))
    page.run()
    wait(win)
    assert pedidos[-1][3] == ".xlsx"
    assert done[-1][0] == "Tablas exportadas" and done[-1][2] == str(out), done
    assert _celdas(out)["C2"] == (None, "1250000", "2")

    # Word con diseño: si el aviso de Word no está desactivado, se explica antes y se pide modo visible
    page.r_word.setChecked(True)
    page.r_diseno.setEnabled(True)
    page.r_diseno.setChecked(True)
    docx = tmp_path / "extracto.docx"
    monkeypatch.setattr(oficina_ui, "pedir_destino", lambda *a: str(docx))
    monkeypatch.setattr(oficina, "aviso_word_desactivado", lambda: False)
    explicado = []
    monkeypatch.setattr(oficina_ui, "confirmar_word_visible", lambda parent: explicado.append(1) or True)
    llamadas = []

    def falso_word(progress=None, **kw):
        llamadas.append(kw)
        return oficina.ResultadoWord("word", 1, ["El documento quedó abierto en Word para que lo revises."])
    monkeypatch.setattr(oficina, "pdf_a_word", falso_word)
    page.run()
    wait(win)
    assert explicado and llamadas[-1]["modo"] == "word" and llamadas[-1]["visible"] is True
    assert done[-1][0] == "Documento de Word listo"
    # si la persona cancela la explicación, no se hace nada
    monkeypatch.setattr(oficina_ui, "confirmar_word_visible", lambda parent: False)
    n = len(llamadas)
    page.run()
    wait(win)
    assert len(llamadas) == n
    # solo texto: no pregunta nada
    page.r_texto.setChecked(True)
    monkeypatch.setattr(oficina_ui, "confirmar_word_visible", lambda parent: pytest.fail("no debía preguntar"))
    page.run()
    wait(win)
    assert llamadas[-1]["modo"] == "interno" and llamadas[-1]["visible"] is False


def test_ui_pdf_escaneado_sugiere_texto_y_ocr(win, scanned, done):
    page = win.pages["pdf2office"]
    win.navigate("pdf2office")
    page.picker.set_path(str(scanned))
    assert page.escaneado and page.r_texto.isChecked() and page.aviso.isVisibleTo(page)
    page.r_excel.setChecked(True)
    assert "Reconocer texto" in page.aviso_texto.text() and page.b_ocr.isVisibleTo(page)
    page.b_ocr.click()
    assert win.stack.currentWidget() is win.pages["ocr"] and win.pages["ocr"].picker.path == str(scanned)
    page.r_word.setChecked(True)
