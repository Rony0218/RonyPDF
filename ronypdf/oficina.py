"""Office y PDF en los dos sentidos (sin interfaz).

* Word, Excel y PowerPoint → PDF con el Microsoft Office instalado (automatización COM desde un
  subproceso de PowerShell, sin dependencias nuevas) o, si no hay Office, con LibreOffice.
* PDF → Word: con el propio Word (conserva el diseño) o con un camino interno que saca el texto y
  los títulos (y reconoce el texto de las páginas escaneadas).
* PDF → Excel/CSV: las tablas, con los números en formato colombiano convertidos a números.

Las funciones públicas reciben rutas, ``password`` y ``progress(i, n, mensaje)`` como el resto de
``core``; los errores para el usuario se lanzan como ``core.UserError`` en español.
"""

from __future__ import annotations

import csv
import io
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence
from xml.sax.saxutils import escape

import pymupdf

from . import core
from .core import UserError, _report

WORD_EXTS = {".doc", ".docx", ".docm", ".rtf", ".odt"}
EXCEL_EXTS = {".xls", ".xlsx", ".xlsm", ".xlsb", ".ods"}
PPT_EXTS = {".ppt", ".pptx", ".pptm", ".pps", ".ppsx", ".odp"}
OFFICE_EXTS = WORD_EXTS | EXCEL_EXTS | PPT_EXTS

# Formatos Office Open XML: son un ZIP; si están cifrados con contraseña se guardan como OLE.
_OOXML = {".docx", ".docm", ".xlsx", ".xlsm", ".xlsb", ".pptx", ".pptm", ".ppsx"}
_ZIP_MAGIC = b"PK"
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

SIN_MOTOR = ("Para convertir documentos de Word, Excel o PowerPoint se necesita Microsoft Office o "
             "LibreOffice (gratuito) instalado en este equipo.")
MSG_CLAVE = "Está protegido con contraseña: ábrelo en su programa y guárdalo sin clave."
MSG_TIEMPO = ("El programa tardó demasiado (más de {s} segundos) y se detuvo. Si el archivo es muy grande, "
              "ábrelo en su programa y guárdalo como PDF desde allí; si Office muestra avisos al abrirse, "
              "ábrelo una vez y acéptalos.")
MSG_SIN_TABLAS = ("No se encontraron tablas en esas páginas. Si el PDF es escaneado, pásalo primero por "
                  "Reconocer texto (OCR).")
LIMITE_ARCHIVO = 180          # segundos por archivo de Office antes de detener el programa…
LIMITE_POR_MB = 20            # …más estos segundos por cada MB por encima de 5 MB (archivos muy grandes)
LIMITE_WORD_BASE = 120        # PDF → Word oculto: 120 s más 2 s por página
ESPERA_CANCELAR = 20          # al cancelar, segundos para que Office termine el archivo en curso y cierre
RUTA_LARGA_OFFICE = 200       # Office no abre rutas muy largas: se convierte una copia con un nombre corto
NOMBRES = {"word": "Word", "excel": "Excel", "powerpoint": "PowerPoint"}

if sys.platform == "win32":
    _SIN_VENTANA = subprocess.CREATE_NO_WINDOW
else:  # pragma: no cover — la aplicación es para Windows
    _SIN_VENTANA = 0


def tipo_de(path: str | Path) -> str | None:
    """'word', 'excel', 'powerpoint', 'pdf', 'imagen' o None."""
    ext = Path(path).suffix.lower()
    if ext in WORD_EXTS:
        return "word"
    if ext in EXCEL_EXTS:
        return "excel"
    if ext in PPT_EXTS:
        return "powerpoint"
    if ext in core.PDF_EXTS:
        return "pdf"
    if ext in core.IMAGE_EXTS:
        return "imagen"
    return None


# --------------------------------------------------------------------------
# Detección de Office y LibreOffice
# --------------------------------------------------------------------------

@dataclass
class Motores:
    word: bool = False
    excel: bool = False
    powerpoint: bool = False
    libreoffice: str | None = None      # ruta a soffice.exe

    @property
    def office(self) -> bool:
        return self.word or self.excel or self.powerpoint

    @property
    def alguno(self) -> bool:
        return self.office or bool(self.libreoffice)

    def puede(self, tipo: str) -> bool:
        return bool(getattr(self, tipo, False) or self.libreoffice)


_cache_motores: Motores | None = None


def _clave_existe(nombre: str) -> bool:
    """¿Existe HKEY_CLASSES_ROOT\\<nombre>? (solo lectura)."""
    try:
        import winreg
    except ImportError:  # pragma: no cover
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, nombre, 0, winreg.KEY_READ):
            return True
    except OSError:
        return False


def _junto_al_exe() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _buscar_libreoffice() -> str | None:
    candidatos: list[Path] = []
    for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        base = os.environ.get(var)
        if base:
            candidatos.append(Path(base) / "LibreOffice" / "program" / "soffice.exe")
    en_path = shutil.which("soffice.exe") or shutil.which("soffice")
    if en_path:
        candidatos.append(Path(en_path))
    base = _junto_al_exe()
    for sub in (("LibreOfficePortable", "App", "libreoffice", "program", "soffice.exe"),
                ("LibreOfficePortable", "App", "LibreOffice", "program", "soffice.exe"),
                ("LibreOffice", "program", "soffice.exe")):
        candidatos.append(base.joinpath(*sub))
    for c in candidatos:
        try:
            if c.is_file():
                return str(c)
        except OSError:
            continue
    return None


def detectar(refrescar: bool = False) -> Motores:
    """Programas disponibles para convertir documentos de Office (con caché)."""
    global _cache_motores
    if _cache_motores is None or refrescar:
        _cache_motores = Motores(word=_clave_existe("Word.Application"),
                                 excel=_clave_existe("Excel.Application"),
                                 powerpoint=_clave_existe("PowerPoint.Application"),
                                 libreoffice=_buscar_libreoffice())
    return _cache_motores


def estado_motores(m: Motores) -> tuple[str, bool]:
    """Texto para el usuario sobre qué programa se usará, y si se puede convertir algo."""
    if m.word and m.excel and m.powerpoint:
        return "Se usará Microsoft Office, que ya está instalado en este equipo.", True
    if m.office:
        tiene = [NOMBRES[t] for t in ("word", "excel", "powerpoint") if getattr(m, t)]
        falta = [NOMBRES[t] for t in ("word", "excel", "powerpoint") if not getattr(m, t)]
        texto = (f"Se usará Microsoft Office ({', '.join(tiene)}), que ya está instalado en este equipo.")
        if m.libreoffice:
            texto += f" Para {' y '.join(falta)} se usará LibreOffice."
        else:
            texto += f" No se encontró {' ni '.join(falta)}: esos archivos no se podrán convertir."
        return texto, True
    if m.libreoffice:
        return "Se usará LibreOffice.", True
    return SIN_MOTOR, False


def _version_word() -> str | None:
    """«16.0» a partir de HKCR\\Word.Application\\CurVer («Word.Application.16»)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Word.Application\CurVer", 0, winreg.KEY_READ) as k:
            valor, _ = winreg.QueryValueEx(k, "")
    except (ImportError, OSError):
        return None
    m = re.search(r"(\d+)$", str(valor))
    return f"{m.group(1)}.0" if m else None


def aviso_word_desactivado() -> bool:
    """¿El usuario ya marcó «No volver a mostrar este mensaje» en el aviso de Word al abrir un PDF?

    Lee (nunca escribe) HKCU\\Software\\Microsoft\\Office\\<versión>\\Word\\Options\\
    DisableConvertPdfWarning.  Solo con ese aviso desactivado se puede convertir con Word oculto:
    si no, Word se queda esperando para siempre a que alguien pulse «Aceptar».
    """
    try:
        import winreg
    except ImportError:  # pragma: no cover
        return False
    actual = _version_word()
    versiones: list[str] = [actual] if actual else []
    if not versiones:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Office", 0, winreg.KEY_READ) as k:
                i = 0
                while True:
                    try:
                        nombre = winreg.EnumKey(k, i)
                    except OSError:
                        break
                    i += 1
                    if re.fullmatch(r"\d+\.\d+", nombre):
                        versiones.append(nombre)
        except OSError:
            return False
    for v in versiones:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\Office\{v}\Word\Options",
                                0, winreg.KEY_READ) as k:
                valor, _ = winreg.QueryValueEx(k, "DisableConvertPdfWarning")
            if int(str(valor).strip() or 0) == 1:
                return True
        except (OSError, ValueError, TypeError):
            continue
    return False


# --------------------------------------------------------------------------
# Escritores mínimos de .docx y .xlsx (sin dependencias)
# --------------------------------------------------------------------------

_XML_PROHIBIDOS = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff\ud800-\udfff]")


def _xml(texto: str) -> str:
    return escape(_XML_PROHIBIDOS.sub("", str(texto)), {'"': "&quot;"})


@dataclass
class Parrafo:
    texto: str = ""
    estilo: str = "normal"            # 'normal' | 'titulo1' | 'titulo2'
    negrita: bool = False
    cursiva: bool = False
    salto_pagina: bool = False        # empieza en una página nueva
    tramos: list[tuple[str, bool, bool]] | None = None   # (texto, negrita, cursiva) por tramos


_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'

_DOCX_TIPOS = (
    _DECL + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '<Override PartName="/word/styles.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
    '</Types>')
_DOCX_RELS = (
    _DECL + f'<Relationships xmlns="{_PKG_REL}">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
    'officeDocument" Target="word/document.xml"/></Relationships>')
_DOCX_DOC_RELS = (
    _DECL + f'<Relationships xmlns="{_PKG_REL}">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
    'styles" Target="styles.xml"/></Relationships>')


def _estilo_titulo(sid: str, nombre: str, nivel: int, tam: int, antes: int) -> str:
    return (f'<w:style w:type="paragraph" w:styleId="{sid}"><w:name w:val="{nombre}"/>'
            '<w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:uiPriority w:val="9"/><w:qFormat/>'
            f'<w:pPr><w:keepNext/><w:keepLines/><w:spacing w:before="{antes}" w:after="120"/>'
            f'<w:outlineLvl w:val="{nivel}"/></w:pPr>'
            f'<w:rPr><w:b/><w:bCs/><w:color w:val="1F3864"/><w:sz w:val="{tam}"/><w:szCs w:val="{tam}"/></w:rPr>'
            '</w:style>')


_DOCX_ESTILOS = (
    _DECL + f'<w:styles xmlns:w="{_W}">'
    '<w:docDefaults><w:rPrDefault><w:rPr>'
    '<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Calibri" w:cs="Calibri"/>'
    '<w:sz w:val="22"/><w:szCs w:val="22"/><w:lang w:val="es-CO" w:eastAsia="en-US" w:bidi="ar-SA"/>'
    '</w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="160" w:line="259" w:lineRule="auto"/>'
    '</w:pPr></w:pPrDefault></w:docDefaults>'
    '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>'
    + _estilo_titulo("Heading1", "heading 1", 0, 32, 360)
    + _estilo_titulo("Heading2", "heading 2", 1, 26, 240)
    + '<w:style w:type="character" w:default="1" w:styleId="DefaultParagraphFont">'
    '<w:name w:val="Default Paragraph Font"/><w:uiPriority w:val="1"/><w:semiHidden/><w:unhideWhenUsed/>'
    '</w:style></w:styles>')

_ESTILOS_WORD = {"titulo1": "Heading1", "titulo2": "Heading2"}


def _run_docx(texto: str, negrita: bool, cursiva: bool) -> str:
    props = ("<w:b/><w:bCs/>" if negrita else "") + ("<w:i/><w:iCs/>" if cursiva else "")
    rpr = f"<w:rPr>{props}</w:rPr>" if props else ""
    partes = []
    for k, trozo in enumerate(texto.split("\t")):
        if k:
            partes.append("<w:tab/>")
        if trozo:
            partes.append(f'<w:t xml:space="preserve">{_xml(trozo)}</w:t>')
    return f"<w:r>{rpr}{''.join(partes)}</w:r>" if partes else ""


def _parrafo_docx(p: Parrafo) -> str:
    ppr = ""
    sid = _ESTILOS_WORD.get(p.estilo)
    if sid:
        ppr += f'<w:pStyle w:val="{sid}"/>'
    if p.salto_pagina:
        ppr += "<w:pageBreakBefore/>"
    tramos = p.tramos if p.tramos else [(p.texto, p.negrita, p.cursiva)]
    runs = "".join(_run_docx(t.replace("\r", "").replace("\n", " "), b, i) for t, b, i in tramos if t)
    return f"<w:p>{'<w:pPr>' + ppr + '</w:pPr>' if ppr else ''}{runs}</w:p>"


def escribir_docx(parrafos: Sequence[Parrafo], output: str | Path,
                  pagina: tuple[float, float] = (612.0, 792.0)) -> None:
    """Crea un .docx mínimo y válido (abre en Word sin ofrecer reparación).

    ``pagina``: tamaño en puntos (por defecto, Carta).
    """
    ancho, alto = (int(round(v * 20)) for v in pagina)            # puntos → veinteavos de punto
    margen = 1417 if min(ancho, alto) > 9000 else 720
    orientacion = ' w:orient="landscape"' if ancho > alto else ""
    seccion = (f'<w:sectPr><w:pgSz w:w="{ancho}" w:h="{alto}"{orientacion}/>'
               f'<w:pgMar w:top="{margen}" w:right="{margen}" w:bottom="{margen}" w:left="{margen}" '
               'w:header="708" w:footer="708" w:gutter="0"/></w:sectPr>')
    cuerpo = "".join(_parrafo_docx(p) for p in parrafos) or "<w:p/>"
    documento = (_DECL + f'<w:document xmlns:w="{_W}" xmlns:r="{_R}"><w:body>{cuerpo}{seccion}'
                 '</w:body></w:document>')
    with core._output(output) as tmp:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", _DOCX_TIPOS)
            z.writestr("_rels/.rels", _DOCX_RELS)
            z.writestr("word/document.xml", documento)
            z.writestr("word/_rels/document.xml.rels", _DOCX_DOC_RELS)
            z.writestr("word/styles.xml", _DOCX_ESTILOS)


class Porcentaje(float):
    """Valor de porcentaje ya dividido entre 100 (12,5 % → 0.125)."""


class Codigo(str):
    """Texto que parece número pero debe quedar como texto (cédulas, NIT, códigos)."""


_S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_XLSX_ESTILOS = (
    _DECL + f'<styleSheet xmlns="{_S}">'
    '<fonts count="2"><font><sz val="11"/><name val="Calibri"/><family val="2"/></font>'
    '<font><b/><sz val="11"/><name val="Calibri"/><family val="2"/></font></fonts>'
    '<fills count="2"><fill><patternFill patternType="none"/></fill>'
    '<fill><patternFill patternType="gray125"/></fill></fills>'
    '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="6">'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
    '<xf numFmtId="3" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
    '<xf numFmtId="4" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
    '<xf numFmtId="10" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
    '<xf numFmtId="49" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
    '</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
    '</styleSheet>')
_E_NEGRITA, _E_ENTERO, _E_DECIMAL, _E_PORCENTAJE, _E_TEXTO = 1, 2, 3, 4, 5
_NOMBRE_HOJA_MALO = re.compile(r"[\[\]:*?/\\]")


def _columna(n: int) -> str:
    """0 → A, 25 → Z, 26 → AA."""
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def nombre_hoja(nombre: str, usados: set[str]) -> str:
    """Nombre válido para Excel: sin []:*?/\\, máximo 31 caracteres y sin repetir."""
    base = _NOMBRE_HOJA_MALO.sub("-", _XML_PROHIBIDOS.sub("", str(nombre))).strip().strip("'").strip()
    base = base[:31] or "Hoja"
    cand, k = base, 2
    while cand.lower() in usados:
        sufijo = f" ({k})"
        cand = base[:31 - len(sufijo)].rstrip() + sufijo
        k += 1
    usados.add(cand.lower())
    return cand


def _celda_xlsx(ref: str, valor: object, negrita: bool) -> str:
    if valor is None or valor == "":
        return ""
    if isinstance(valor, bool):
        estilo = _E_NEGRITA if negrita else 0
        s = f' s="{estilo}"' if estilo else ""
        return f'<c r="{ref}" t="b"{s}><v>{int(valor)}</v></c>'
    if isinstance(valor, (int, float)):
        if negrita:
            estilo = _E_NEGRITA
        elif isinstance(valor, Porcentaje):
            estilo = _E_PORCENTAJE
        elif isinstance(valor, float) and not float(valor).is_integer():
            estilo = _E_DECIMAL
        elif abs(valor) >= 1000:
            estilo = _E_ENTERO
        else:
            estilo = 0
        num = repr(float(valor)) if isinstance(valor, float) else str(int(valor))
        s = f' s="{estilo}"' if estilo else ""
        return f'<c r="{ref}"{s}><v>{num}</v></c>'
    texto = str(valor)[:32767]
    estilo = _E_NEGRITA if negrita else (_E_TEXTO if isinstance(valor, Codigo) else 0)
    s = f' s="{estilo}"' if estilo else ""
    return f'<c r="{ref}" t="inlineStr"{s}><is><t xml:space="preserve">{_xml(texto)}</t></is></c>'


def _hoja_xlsx(filas: list[list[object]], primera: bool, ajustar: bool = True) -> str:
    anchos: dict[int, int] = {}
    filas_xml = []
    for r, fila in enumerate(filas):
        celdas = []
        for c, valor in enumerate(fila):
            celda = _celda_xlsx(f"{_columna(c)}{r + 1}", valor, r == 0)
            if celda:
                celdas.append(celda)
                largo = len(f"{valor:,.2f}") if isinstance(valor, float) else len(str(valor))
                anchos[c] = max(anchos.get(c, 0), min(largo, 60))
        filas_xml.append(f'<row r="{r + 1}">{"".join(celdas)}</row>')
    ncol = max((len(f) for f in filas), default=0)
    dim = f'<dimension ref="A1:{_columna(max(ncol - 1, 0))}{max(len(filas), 1)}"/>'
    sel = ' tabSelected="1"' if primera else ""
    if len(filas) > 1:
        vista = (f'<sheetViews><sheetView{sel} workbookViewId="0"><pane ySplit="1" topLeftCell="A2" '
                 'activePane="bottomLeft" state="frozen"/><selection pane="bottomLeft" activeCell="A2" '
                 'sqref="A2"/></sheetView></sheetViews>')
    else:
        vista = f'<sheetViews><sheetView{sel} workbookViewId="0"/></sheetViews>'
    cols = "".join(f'<col min="{c + 1}" max="{c + 1}" width="{max(9.0, a * 1.15 + 2):.1f}" customWidth="1"/>'
                   for c, a in sorted(anchos.items()))
    cols = f"<cols>{cols}</cols>" if cols else ""
    # Al imprimir, las columnas caben a lo ancho de la página (horizontal).
    pr = '<sheetPr><pageSetUpPr fitToPage="1"/></sheetPr>' if ajustar else ""
    impresion = '<pageSetup orientation="landscape" fitToWidth="1" fitToHeight="0"/>' if ajustar else ""
    return (_DECL + f'<worksheet xmlns="{_S}" xmlns:r="{_R}">'
            f'{pr}{dim}{vista}<sheetFormatPr defaultRowHeight="15"/>{cols}'
            f'<sheetData>{"".join(filas_xml)}</sheetData>'
            '<pageMargins left="0.5" right="0.5" top="0.75" bottom="0.75" header="0.3" footer="0.3"/>'
            f'{impresion}</worksheet>')


def escribir_xlsx(hojas: Sequence[tuple[str, list[list[object]]]], output: str | Path,
                  ajustar_impresion: bool = True) -> None:
    """Crea un .xlsx mínimo y válido: cadenas en línea, números como números y la primera fila en
    negrita.  Los nombres de hoja se ajustan a las reglas de Excel.  ``ajustar_impresion``: al
    imprimirlo, cada hoja cabe a lo ancho de una página."""
    if not hojas:
        hojas = [("Hoja1", [])]
    usados: set[str] = set()
    nombres = [nombre_hoja(n, usados) for n, _ in hojas]
    n = len(hojas)
    tipos = (_DECL + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="xml" ContentType="application/xml"/>'
             '<Override PartName="/xl/workbook.xml" '
             'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
             '<Override PartName="/xl/styles.xml" '
             'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
             + "".join(f'<Override PartName="/xl/worksheets/sheet{k + 1}.xml" '
                       'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                       for k in range(n))
             + '</Types>')
    rels = (_DECL + f'<Relationships xmlns="{_PKG_REL}">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            'officeDocument" Target="xl/workbook.xml"/></Relationships>')
    libro = (_DECL + f'<workbook xmlns="{_S}" xmlns:r="{_R}"><bookViews><workbookView activeTab="0"/></bookViews>'
             '<sheets>' + "".join(f'<sheet name="{_xml(nm)}" sheetId="{k + 1}" r:id="rId{k + 1}"/>'
                                  for k, nm in enumerate(nombres))
             + '</sheets></workbook>')
    libro_rels = (_DECL + f'<Relationships xmlns="{_PKG_REL}">'
                  + "".join(f'<Relationship Id="rId{k + 1}" Type="http://schemas.openxmlformats.org/'
                            f'officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{k + 1}.xml"/>'
                            for k in range(n))
                  + f'<Relationship Id="rId{n + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                  'relationships/styles" Target="styles.xml"/></Relationships>')
    with core._output(output) as tmp:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", tipos)
            z.writestr("_rels/.rels", rels)
            z.writestr("xl/workbook.xml", libro)
            z.writestr("xl/_rels/workbook.xml.rels", libro_rels)
            z.writestr("xl/styles.xml", _XLSX_ESTILOS)
            for k, (_, filas) in enumerate(hojas):
                z.writestr(f"xl/worksheets/sheet{k + 1}.xml",
                           _hoja_xlsx(list(filas), k == 0, ajustar_impresion))


def _numero_csv(valor: object) -> str:
    if isinstance(valor, Porcentaje):
        return f"{float(valor) * 100:.10g}".replace(".", ",") + "%"
    if isinstance(valor, bool):
        return "VERDADERO" if valor else "FALSO"
    if isinstance(valor, float):
        return f"{valor:.15g}".replace(".", ",")
    if isinstance(valor, int):
        return str(valor)
    return "" if valor is None else str(valor)


def escribir_csv(filas: Sequence[Sequence[object]], output: str | Path) -> None:
    """CSV para Excel en español: separado por punto y coma, coma decimal y UTF-8 con BOM."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    for fila in filas:
        w.writerow([_numero_csv(v) for v in fila])
    with core._output(output) as tmp:
        Path(tmp).write_text(buf.getvalue(), encoding="utf-8-sig", newline="")


# --------------------------------------------------------------------------
# Números con formato colombiano
# --------------------------------------------------------------------------

_MILES_PUNTO = re.compile(r"^\d{1,3}(?:\.\d{3})+(?:,\d+)?$")       # 1.250.000  1.250.000,50
_DECIMAL_COMA = re.compile(r"^\d+,\d+$")                             # 1250,5
_ENTERO = re.compile(r"^\d+$")
_MILES_COMA = re.compile(r"^\d{1,3}(?:,\d{3})+(?:\.\d+)?$")         # 1,250,000.50 (formato inglés)
_DECIMAL_PUNTO = re.compile(r"^\d+\.\d{1,2}$")                       # 12.5
_MILES_ESPACIO = re.compile(r"^\d{1,3}(?: \d{3})+(?:,\d+)?$")       # 1 250 000
_MONEDA = re.compile(r"^(?:COP|USD|US\$|\$)\s*|\s*(?:COP|USD|pesos)$", re.IGNORECASE)
_PALABRAS_CODIGO = re.compile(
    r"\b(c\.?\s?c|cedula|nit|documento|identificacion|id|telefono|tel|celular|movil|codigo|cod|cuenta|"
    r"radicado|referencia|ref|factura|contrato|expediente|placa|pin|nuip|pasaporte|ruc)\b")


def _sin_tildes(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


def es_columna_de_codigos(encabezado: str) -> bool:
    """«Cédula», «NIT», «Teléfono», «No. de cuenta»…: sus valores quedan como texto."""
    return bool(_PALABRAS_CODIGO.search(_sin_tildes(str(encabezado)).lower()))


def numero_colombiano(texto: str) -> int | float | None:
    """Convierte «1.250.000», «1.250.000,50», «$ 800.000», «12,5 %» o «(3.500)» en número.

    Devuelve None si no es un número (fechas, códigos, cédulas sin separadores, textos…).  Los
    porcentajes salen como ``Porcentaje`` (12,5 % → 0.125).
    """
    t = str(texto).strip().replace("\u00a0", " ").replace("\u2212", "-")
    if not t or len(t) > 40:
        return None
    negativo = False
    if t.startswith("(") and t.endswith(")"):
        negativo, t = True, t[1:-1].strip()
    porcentaje = t.endswith("%")
    if porcentaje:
        t = t[:-1].strip()
    moneda = False
    for _ in range(3):
        if t and t[0] in "-+":
            negativo ^= t[0] == "-"
            t = t[1:].strip()
        nuevo = _MONEDA.sub("", t).strip()
        if nuevo != t:
            moneda, t = True, nuevo
        else:
            break
    if t.endswith("-"):                      # «800.000-» (negativo contable)
        negativo, t = not negativo, t[:-1].strip()
    if not t:
        return None
    if _MILES_PUNTO.match(t):
        valor_txt = t.replace(".", "").replace(",", ".")
    elif _MILES_ESPACIO.match(t):
        valor_txt = t.replace(" ", "").replace(",", ".")
    elif _DECIMAL_COMA.match(t):
        valor_txt = t.replace(",", ".")
    elif _ENTERO.match(t):
        if not (moneda or porcentaje):
            if len(t) > 1 and t.startswith("0"):
                return None                  # código con ceros a la izquierda
            if len(t) >= 10:
                return None                  # celular, cédula o cuenta sin separadores
        valor_txt = t
    elif _MILES_COMA.match(t) and (t.count(",") > 1 or "." in t):
        valor_txt = t.replace(",", "")
    elif _DECIMAL_PUNTO.match(t):
        valor_txt = t
    else:
        return None
    if len(valor_txt.replace(".", "").lstrip("0")) > 15:
        return None                          # más dígitos de los que Excel guarda sin perder
    valor: int | float = float(valor_txt) if "." in valor_txt else int(valor_txt)
    if isinstance(valor, float) and valor.is_integer() and not porcentaje:
        valor = int(valor)
    if negativo:
        valor = -valor
    if porcentaje:
        return Porcentaje(float(valor) / 100)
    return valor


def valor_celda(texto: str, como_texto: bool = False) -> object:
    """Valor para Excel: número si lo es, o el texto (marcado como código si parece uno)."""
    t = " ".join(str(texto or "").split())
    if not t:
        return ""
    if como_texto:
        return Codigo(t) if re.search(r"\d", t) else t
    n = numero_colombiano(t)
    if n is not None:
        return n
    if re.fullmatch(r"[\d.\-\s/]+", t) and re.search(r"\d", t):
        return Codigo(t)                     # fechas, NIT con guion, cédulas: texto con formato «@»
    return t


# --------------------------------------------------------------------------
# Office → PDF: el script de PowerShell
# --------------------------------------------------------------------------

_SCRIPT_PS = r"""
param([Parameter(Mandatory = $true)][string]$Trabajo)

# Script generado por RonyPDF: convierte documentos con el Microsoft Office instalado.
# Cada línea de salida es un JSON con el avance, el resultado o el error de un archivo.
$ErrorActionPreference = 'Stop'
$CLAVE = 'RonyPDF-sin-clave-4f1d9c'
$PROGRAMAS = @{
    word       = @('Word.Application', 'WINWORD', 'Word')
    excel      = @('Excel.Application', 'EXCEL', 'Excel')
    powerpoint = @('PowerPoint.Application', 'POWERPNT', 'PowerPoint')
}
$script:apps = @{}
$script:propios = @{}
$script:pidsTipo = @{}
$script:dejarWord = $false

function Emitir([hashtable]$datos) {
    $json = ConvertTo-Json -InputObject $datos -Compress
    $json = [regex]::Replace($json, '[^\x00-\x7F]', { param($m) '\u{0:x4}' -f [int][char]$m.Value })
    [Console]::Out.WriteLine($json)
    [Console]::Out.Flush()
}

function Liberar($objeto) {
    if ($null -ne $objeto) {
        try { [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($objeto) } catch { }
    }
}

function Ids([string]$nombre) {
    return @(Get-Process -Name $nombre -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
}

function Abrir([string]$tipo, [bool]$visible) {
    if ($script:apps.ContainsKey($tipo)) {
        try { $null = $script:apps[$tipo].Name; return } catch { $script:apps.Remove($tipo) }
    }
    $prog = $PROGRAMAS[$tipo]
    $antes = Ids $prog[1]
    try {
        $app = New-Object -ComObject $prog[0]
    } catch {
        throw ('RONYPDF-INICIO: ' + $_.Exception.Message)
    }
    $nuevos = @(Ids $prog[1] | Where-Object { $antes -notcontains $_ })
    if (-not $script:pidsTipo.ContainsKey($tipo)) { $script:pidsTipo[$tipo] = @() }
    foreach ($id in $nuevos) {
        $inicio = 0
        try { $inicio = (Get-Process -Id $id).StartTime.ToFileTimeUtc() } catch { }
        $script:pidsTipo[$tipo] += $id
        Emitir @{ evento = 'proceso'; pid = $id; nombre = $prog[1]; inicio = $inicio }
    }
    $script:propios[$tipo] = ($nuevos.Count -gt 0)
    if ($tipo -eq 'word') {
        try { $app.Visible = $visible } catch { }
        try { $app.DisplayAlerts = 0 } catch { }
        try { $app.AutomationSecurity = 3 } catch { }
    } elseif ($tipo -eq 'excel') {
        try { $app.Visible = $false } catch { }
        try { $app.DisplayAlerts = $false } catch { }
        try { $app.AutomationSecurity = 3 } catch { }
        try { $app.ScreenUpdating = $false } catch { }
        try { $app.AskToUpdateLinks = $false } catch { }
        try { $app.EnableEvents = $false } catch { }
    } else {
        # PowerPoint: no se toca Visible (lanza error); se abre todo sin ventana.
        try { $app.DisplayAlerts = 1 } catch { }
        try { $app.AutomationSecurity = 3 } catch { }
    }
    $script:apps[$tipo] = $app
}

function WordAPdf([string]$ruta, [string]$salida, $op) {
    Abrir 'word' $false
    $w = $script:apps['word']
    $d = $null
    try {
        # Solo lectura y con una contraseña ficticia: un documento protegido da error en vez de un diálogo.
        $d = $w.Documents.Open($ruta, $false, $true, $false, $CLAVE, $CLAVE)
        $marcadores = 0
        if ($op.word_marcadores) { $marcadores = 1 }
        $d.ExportAsFixedFormat($salida, 17, $false, 0, 0, 1, 1, 0, $true, $true, $marcadores, $true, $true, [bool]$op.pdfa)
    } finally {
        if ($null -ne $d) { try { $d.Close([ref]0) } catch { }; Liberar $d }
    }
}

function ExcelAPdf([string]$ruta, [string]$salida, $op) {
    Abrir 'excel' $false
    $x = $script:apps['excel']
    $libro = $null
    try {
        $libro = $x.Workbooks.Open($ruta, 0, $true, 5, $CLAVE)
        $activa = ($op.excel_hojas -eq 'activa')
        $hojas = @()
        if ($activa) { $hojas = @($libro.ActiveSheet) } else { foreach ($h in $libro.Worksheets) { $hojas += $h } }
        if ($op.excel_ajustar_ancho) {
            try { $x.PrintCommunication = $false } catch { }
            foreach ($h in $hojas) {
                try {
                    $pg = $h.PageSetup
                    $pg.Zoom = $false
                    $pg.FitToPagesWide = 1
                    $pg.FitToPagesTall = $false
                    Liberar $pg
                } catch { }
            }
            try { $x.PrintCommunication = $true } catch { }
        }
        if ($activa) { $libro.ActiveSheet.ExportAsFixedFormat(0, $salida) } else { $libro.ExportAsFixedFormat(0, $salida) }
        foreach ($h in $hojas) { Liberar $h }
    } finally {
        if ($null -ne $libro) { try { $libro.Close($false) } catch { }; Liberar $libro }
    }
}

function PowerPointAPdf([string]$ruta, [string]$salida, $op) {
    Abrir 'powerpoint' $false
    $p = $script:apps['powerpoint']
    $pres = $null
    try {
        # Solo lectura, sin título nuevo y sin ventana.  «ruta::clave::» con una clave ficticia: una
        # presentación protegida da error enseguida en vez de esperar una contraseña para siempre.
        $pres = $p.Presentations.Open($ruta + '::' + $CLAVE + '::', -1, 0, 0)
        $pres.SaveAs($salida, 32)
    } finally {
        if ($null -ne $pres) { try { $pres.Close() } catch { }; Liberar $pres }
    }
}

function PdfAWord([string]$ruta, [string]$salida, [bool]$visible) {
    Abrir 'word' $visible
    $w = $script:apps['word']
    if ($visible) { try { $w.Visible = $true; $w.Activate() } catch { } }
    $d = $w.Documents.Open($ruta, $false, $true)
    try {
        $d.SaveAs2($salida, 16)
    } catch {
        try { $d.Close([ref]0) } catch { }
        Liberar $d
        throw
    }
    if ($visible) {
        try { $d.Activate() } catch { }
        Liberar $d
        $script:dejarWord = $true
    } else {
        try { $d.Close([ref]0) } catch { }
        Liberar $d
    }
}

function Amable([string]$accion, [string]$detalle) {
    if ($detalle -like 'RONYPDF-INICIO:*') {
        $prog = 'Office'
        if ($accion -eq 'excel') { $prog = 'Excel' } elseif ($accion -eq 'powerpoint') { $prog = 'PowerPoint' } else { $prog = 'Word' }
        return "No se pudo iniciar Microsoft $prog en este equipo."
    }
    if ($detalle -match 'contrase|password|clave') {
        return 'Está protegido con contraseña: ábrelo en su programa y guárdalo sin clave.'
    }
    if ($accion -eq 'pdf_a_word') {
        return 'Word no pudo convertir el PDF. Si cerraste su aviso con «Cancelar», inténtalo de nuevo, o usa «Solo texto y títulos».'
    }
    $prog = $PROGRAMAS[$accion][2]
    return "No se pudo convertir: el archivo puede estar dañado o no ser un documento válido de $prog."
}

function Cerrar {
    foreach ($tipo in @($script:apps.Keys)) {
        $app = $script:apps[$tipo]
        $abiertos = -1
        try {
            if ($tipo -eq 'word') { $abiertos = $app.Documents.Count }
            elseif ($tipo -eq 'excel') { $abiertos = $app.Workbooks.Count }
            else { $abiertos = $app.Presentations.Count }
        } catch { }
        $propio = [bool]$script:propios[$tipo]
        $entregar = $propio -and (($tipo -eq 'word' -and $script:dejarWord) -or $abiertos -gt 0)
        if ($entregar) {
            # Quedó con documentos abiertos (el convertido o uno del usuario): se le deja a la vista.
            try { if ($tipo -ne 'powerpoint') { $app.Visible = $true } } catch { }
            foreach ($id in @($script:pidsTipo[$tipo])) { Emitir @{ evento = 'entregado'; pid = $id } }
            $script:pidsTipo[$tipo] = @()
        } elseif ($propio) {
            try { if ($tipo -eq 'word') { $app.Quit([ref]0) } else { $app.Quit() } } catch { }
        }
        Liberar $app
    }
    $script:apps.Clear()
    [GC]::Collect(); [GC]::WaitForPendingFinalizers(); [GC]::Collect(); [GC]::WaitForPendingFinalizers()
    # Solo los procesos que creó este script: si no terminan solos, se terminan.
    $limite = (Get-Date).AddSeconds(15)
    foreach ($tipo in @($script:pidsTipo.Keys)) {
        foreach ($id in @($script:pidsTipo[$tipo])) {
            $p = Get-Process -Id $id -ErrorAction SilentlyContinue
            if ($null -eq $p) { continue }
            $ms = [int][Math]::Max(0, ($limite - (Get-Date)).TotalMilliseconds)
            try { [void]$p.WaitForExit($ms) } catch { }
            if (-not $p.HasExited) { try { Stop-Process -Id $id -Force -ErrorAction Stop } catch { } }
        }
    }
}

$job = [System.IO.File]::ReadAllText($Trabajo, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
try {
    foreach ($it in @($job.archivos)) {
        if ($job.cancelar -and (Test-Path -LiteralPath $job.cancelar)) { Emitir @{ evento = 'cancelado' }; break }
        Emitir @{ evento = 'inicio'; i = $it.i }
        try {
            switch ($it.accion) {
                'word' { WordAPdf $it.ruta $it.salida $job.opciones }
                'excel' { ExcelAPdf $it.ruta $it.salida $job.opciones }
                'powerpoint' { PowerPointAPdf $it.ruta $it.salida $job.opciones }
                'pdf_a_word' { PdfAWord $it.ruta $it.salida ([bool]$it.visible) }
            }
            Emitir @{ evento = 'ok'; i = $it.i }
        } catch {
            $detalle = [string]$_.Exception.Message
            Emitir @{ evento = 'error'; i = $it.i; mensaje = (Amable $it.accion $detalle); detalle = $detalle }
        }
    }
} finally {
    Cerrar
    Emitir @{ evento = 'fin' }
}
"""


def _powershell_exe() -> str:
    raiz = os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\Windows"
    ruta = Path(raiz) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(ruta) if ruta.is_file() else "powershell.exe"


def comando_powershell(script: str | Path, trabajo: str | Path, por_comando: bool = False) -> list[str]:
    """Línea de comandos para ejecutar el script.  Las rutas de los documentos NO van aquí: van en el
    JSON de trabajo (así no importan las tildes, los espacios ni el largo de los nombres)."""
    base = [_powershell_exe(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass"]
    if not por_comando:
        return base + ["-File", str(script), "-Trabajo", str(trabajo)]
    # Plan B para equipos donde una directiva impide ejecutar archivos .ps1.
    s, t = str(script).replace("'", "''"), str(trabajo).replace("'", "''")
    return base + ["-Command", f"& ([scriptblock]::Create([IO.File]::ReadAllText('{s}', "
                               f"[Text.Encoding]::UTF8))) -Trabajo '{t}'"]


def escribir_script(carpeta: str | Path) -> Path:
    """Guarda el script en UTF-8 con BOM (PowerShell 5.1 así lee bien las tildes)."""
    ruta = Path(carpeta) / "convertir_office.ps1"
    ruta.write_text(_SCRIPT_PS.lstrip("\n"), encoding="utf-8-sig")
    return ruta


def escribir_trabajo(carpeta: str | Path, tareas: Sequence[dict], opciones: "OpcionesOffice",
                     cancelar: str | Path) -> Path:
    ruta = Path(carpeta) / f"trabajo_{time.monotonic_ns()}.json"
    datos = {"archivos": [{k: (str(v) if isinstance(v, Path) else v) for k, v in t.items()} for t in tareas],
             "opciones": {"word_marcadores": bool(opciones.word_marcadores),
                          "excel_ajustar_ancho": bool(opciones.excel_ajustar_ancho),
                          "excel_hojas": "activa" if opciones.excel_hojas == "activa" else "todas",
                          "pdfa": bool(opciones.pdfa)},
             "cancelar": str(cancelar)}
    ruta.write_text(json.dumps(datos, ensure_ascii=True), encoding="utf-8")
    return ruta


# --------------------------------------------------------------------------
# Procesos de Office creados por la aplicación (ctypes, sin dependencias)
# --------------------------------------------------------------------------

def _k32():
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = wintypes.HANDLE
    k.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k.CloseHandle.argtypes = (wintypes.HANDLE,)
    k.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    k.WaitForSingleObject.restype = wintypes.DWORD
    k.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    k.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
    k.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                             ctypes.POINTER(wintypes.DWORD))
    return k


def _es_el_mismo(k, h, nombre: str, inicio: int) -> bool:
    """El proceso abierto es el que anotó el script (mismo programa y misma hora de inicio)."""
    import ctypes
    from ctypes import wintypes
    buf = ctypes.create_unicode_buffer(1024)
    n = wintypes.DWORD(len(buf))
    if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
        if Path(buf.value).stem.upper() != nombre.upper():
            return False
    if inicio:
        c, e, kk, u = (wintypes.FILETIME() for _ in range(4))
        if k.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(kk), ctypes.byref(u)):
            creado = (c.dwHighDateTime << 32) | c.dwLowDateTime
            if abs(creado - int(inicio)) > 20_000_000:        # más de 2 s de diferencia: es otro
                return False
    return True


def proceso_vivo(pid: int) -> bool:
    if sys.platform != "win32":  # pragma: no cover
        return False
    k = _k32()
    h = k.OpenProcess(0x1000 | 0x100000, False, int(pid))
    if not h:
        return False
    try:
        return k.WaitForSingleObject(h, 0) != 0
    finally:
        k.CloseHandle(h)


def terminar_procesos(procesos: dict[int, tuple[str, int]], espera: float = 5.0) -> None:
    """Espera a que terminen los procesos anotados (pid → (programa, inicio)); los que sigan vivos
    se terminan.  Nunca toca un proceso que no sea exactamente el anotado."""
    if sys.platform != "win32" or not procesos:
        return
    k = _k32()
    limite = time.monotonic() + max(0.0, espera)
    for pid, (nombre, inicio) in list(procesos.items()):
        h = k.OpenProcess(0x1000 | 0x100000 | 0x0001, False, int(pid))
        if not h:
            continue
        try:
            if not _es_el_mismo(k, h, nombre, inicio):
                continue
            restante = int(max(0.0, limite - time.monotonic()) * 1000)
            if k.WaitForSingleObject(h, restante) != 0:
                k.TerminateProcess(h, 1)
                k.WaitForSingleObject(h, 3000)
        finally:
            k.CloseHandle(h)


def _matar_arbol(pid: int) -> None:
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], creationflags=_SIN_VENTANA,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
    except (OSError, subprocess.SubprocessError):
        pass


# --------------------------------------------------------------------------
# Ejecución del script con avance, cancelación y tiempo límite
# --------------------------------------------------------------------------

def _leer_lineas(flujo, cola: queue.Queue) -> None:
    try:
        for linea in iter(flujo.readline, b""):
            cola.put(linea)
    except (OSError, ValueError):
        pass
    finally:
        cola.put(None)


def _es_politica(texto: str) -> bool:
    t = texto.lower()
    return any(p in t for p in ("psssecurityexception", "pssecurityexception", "unauthorizedaccess",
                                "executionpolicy", "execution policy", "directiva de ejecuci",
                                "deshabilitada la ejecuci", "disabled on this system"))


@dataclass
class _Salida:
    resultados: dict[int, tuple[str, str] | None] = field(default_factory=dict)   # None = bien
    hubo_eventos: bool = False
    ruido: str = ""


class _SesionOffice:
    """Una ejecución del script de PowerShell para una lista de tareas."""

    def __init__(self, tareas: list[dict], opciones: "OpcionesOffice", carpeta: Path,
                 progress, total: int, etiqueta: Callable[[int | None], str],
                 limite: Callable[[int], float | None], al_terminar: Callable[[int], None],
                 por_comando: bool = False):
        self.tareas = tareas
        self.opciones = opciones
        self.carpeta = carpeta
        self.progress = progress
        self.total = total
        self.etiqueta = etiqueta
        self.limite = limite
        self.al_terminar = al_terminar
        self.por_comando = por_comando
        self.procesos: dict[int, tuple[str, int]] = {}
        self.salida = _Salida()
        self.actual: int | None = None
        self.t_actual = 0.0
        self.cancelando = False

    def _evento(self, crudo: bytes) -> None:
        texto = crudo.decode("utf-8", errors="replace").strip().lstrip("\ufeff")
        if not texto:
            return
        try:
            ev = json.loads(texto)
            if not isinstance(ev, dict):
                raise ValueError
        except ValueError:
            self.salida.ruido += texto + "\n"
            return
        self.salida.hubo_eventos = True
        tipo = ev.get("evento")
        if tipo == "proceso":
            self.procesos[int(ev["pid"])] = (str(ev.get("nombre", "")), int(ev.get("inicio") or 0))
        elif tipo == "entregado":
            self.procesos.pop(int(ev["pid"]), None)
        elif tipo == "inicio":
            self.actual, self.t_actual = int(ev["i"]), time.monotonic()
        elif tipo in ("ok", "error"):
            i = int(ev["i"])
            self.actual = None
            if self.cancelando:
                return
            if tipo == "error":
                self.salida.resultados[i] = (str(ev.get("mensaje") or "No se pudo convertir."),
                                             str(ev.get("detalle") or ""))
                return
            try:
                self.al_terminar(i)
                self.salida.resultados[i] = None
            except UserError as exc:
                self.salida.resultados[i] = (str(exc), "")
            except OSError as exc:
                self.salida.resultados[i] = (f"No se pudo guardar el PDF: {exc.strerror or exc}", str(exc))

    def _vaciar(self, cola: queue.Queue) -> bool:
        """Procesa lo que haya en la cola; devuelve True cuando el script terminó."""
        while True:
            try:
                crudo = cola.get_nowait()
            except queue.Empty:
                return False
            if crudo is None:
                return True
            self._evento(crudo)

    def ejecutar(self) -> _Salida:
        bandera = self.carpeta / f"cancelar_{time.monotonic_ns()}.txt"
        script = escribir_script(self.carpeta)
        trabajo = escribir_trabajo(self.carpeta, self.tareas, self.opciones, bandera)
        cmd = comando_powershell(script, trabajo, self.por_comando)
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, creationflags=_SIN_VENTANA)
        except OSError as exc:
            raise UserError("No se pudo iniciar Windows PowerShell, que se necesita para usar Microsoft "
                            "Office desde RonyPDF.") from exc
        cola: queue.Queue = queue.Queue()
        lector = threading.Thread(target=_leer_lineas, args=(proc.stdout, cola), daemon=True)
        lector.start()
        self.espera_final = 5.0
        try:
            terminado = False
            while not terminado:
                try:
                    crudo = cola.get(timeout=0.2)
                except queue.Empty:
                    crudo = b""
                if crudo is None:
                    terminado = True
                elif crudo:
                    self._evento(crudo)
                    terminado = self._vaciar(cola)
                indice = self.actual if self.actual is not None else len(self.salida.resultados)
                try:
                    _report(self.progress, min(indice, max(self.total - 1, 0)), self.total,
                            self.etiqueta(self.actual))
                except BaseException:
                    self._parar_con_calma(proc, cola, bandera)
                    raise
                if self.actual is not None:
                    tope = self.limite(self.actual)
                    if tope and time.monotonic() - self.t_actual > tope:
                        self.salida.resultados[self.actual] = (MSG_TIEMPO.format(s=int(tope)), "")
                        self.actual = None
                        self.espera_final = 0.0
                        proc.kill()
                        break
            if self.actual is not None and self.actual not in self.salida.resultados:
                self.salida.resultados[self.actual] = ("El programa se cerró de forma inesperada.",
                                                       self.salida.ruido[-500:])
        finally:
            if proc.poll() is None:
                proc.kill()
                self.espera_final = 0.0
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                pass
            lector.join(timeout=5)
            self._vaciar(cola)
            try:
                proc.stdout.close()
            except OSError:
                pass
            terminar_procesos(self.procesos, self.espera_final)
            for f in (bandera, trabajo):
                try:
                    f.unlink()
                except OSError:
                    pass
        return self.salida

    def _parar_con_calma(self, proc, cola: queue.Queue, bandera: Path) -> None:
        """Cancelación: el script termina el archivo en curso, cierra Office y sale.  Si no responde
        a tiempo, se termina PowerShell y solo los procesos de Office que él creó."""
        self.cancelando = True
        try:
            bandera.write_text("cancelar", encoding="ascii")
        except OSError:
            pass
        limite = time.monotonic() + ESPERA_CANCELAR
        while proc.poll() is None and time.monotonic() < limite:
            if self._vaciar(cola):
                break
            time.sleep(0.1)
        if proc.poll() is None:
            proc.kill()
            self.espera_final = 0.0


def _convertir_con_office(tareas: list[dict], opciones: "OpcionesOffice", carpeta: Path, progress,
                          total: int, etiqueta: Callable[[int | None], str],
                          limite: Callable[[int], float | None],
                          al_terminar: Callable[[int], None]) -> dict[int, tuple[str, str] | None]:
    """Ejecuta las tareas con Office; si el script se cae a mitad, sigue con las que faltan."""
    resultados: dict[int, tuple[str, str] | None] = {}
    pendientes = list(tareas)
    por_comando = False
    while pendientes:
        salida = _SesionOffice(pendientes, opciones, carpeta, progress, total, etiqueta, limite,
                               al_terminar, por_comando).ejecutar()
        resultados.update(salida.resultados)
        if not salida.hubo_eventos:
            if not por_comando and _es_politica(salida.ruido):
                por_comando = True
                continue
            for t in pendientes:
                resultados.setdefault(t["i"], ("No se pudo usar Microsoft Office desde RonyPDF.",
                                               salida.ruido[-500:]))
            break
        pendientes = [t for t in pendientes if t["i"] not in resultados]
    return resultados


# --------------------------------------------------------------------------
# LibreOffice
# --------------------------------------------------------------------------

def comando_libreoffice(soffice: str, entrada: str | Path, carpeta_salida: str | Path,
                        perfil: str | Path) -> list[str]:
    """soffice --headless con un perfil temporal propio (no choca con un LibreOffice abierto)."""
    return [str(soffice), "--headless", "--norestore", "--nolockcheck",
            f"-env:UserInstallation={Path(perfil).resolve().as_uri()}",
            "--convert-to", "pdf", "--outdir", str(carpeta_salida), str(entrada)]


def _convertir_con_libreoffice(soffice: str, tareas: list[dict], carpeta: Path, progress, total: int,
                               etiqueta: Callable[[int | None], str], limite: Callable[[int], float],
                               al_terminar: Callable[[int], None]) -> dict[int, tuple[str, str] | None]:
    """Un soffice por archivo (con su tiempo límite); el documento se copia antes a un nombre corto."""
    resultados: dict[int, tuple[str, str] | None] = {}
    perfil = carpeta / "lo_perfil"
    perfil.mkdir(exist_ok=True)
    for t in tareas:
        i = t["i"]
        entrada_dir, salida_dir = carpeta / f"lo_in_{i}", carpeta / f"lo_out_{i}"
        entrada_dir.mkdir(exist_ok=True)
        salida_dir.mkdir(exist_ok=True)
        entrada = entrada_dir / f"documento{Path(t['ruta']).suffix.lower()}"
        try:
            shutil.copyfile(_ruta_larga(t["ruta"]), entrada)
        except OSError as exc:
            resultados[i] = ("No se pudo leer el archivo. ¿Está abierto en otro programa?", str(exc))
            continue
        _report(progress, i, total, etiqueta(i))
        try:
            proc = subprocess.Popen(comando_libreoffice(soffice, entrada, salida_dir, perfil),
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, creationflags=_SIN_VENTANA)
        except OSError as exc:
            raise UserError("No se pudo iniciar LibreOffice.") from exc
        inicio, tope, vencido = time.monotonic(), limite(i), False
        try:
            while proc.poll() is None:
                time.sleep(0.2)
                _report(progress, i, total, etiqueta(i))
                if time.monotonic() - inicio > tope:
                    vencido = True
                    _matar_arbol(proc.pid)
                    break
        except BaseException:
            _matar_arbol(proc.pid)
            raise
        finally:
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                pass
        pdf = salida_dir / "documento.pdf"
        if vencido:
            resultados[i] = (MSG_TIEMPO.format(s=int(tope)), "")
        elif not pdf.is_file() or pdf.stat().st_size == 0:
            resultados[i] = ("LibreOffice no pudo convertirlo: puede estar dañado o protegido con contraseña.", "")
        else:
            try:
                os.replace(pdf, t["salida"])
                al_terminar(i)
                resultados[i] = None
            except UserError as exc:
                resultados[i] = (str(exc), "")
            except OSError as exc:
                resultados[i] = (f"No se pudo guardar el PDF: {exc.strerror or exc}", str(exc))
    return resultados


# --------------------------------------------------------------------------
# Office → PDF: funciones públicas
# --------------------------------------------------------------------------

@dataclass
class OpcionesOffice:
    excel_ajustar_ancho: bool = True
    excel_hojas: str = "todas"        # 'todas' | 'activa'
    word_marcadores: bool = True
    pdfa: bool = False


@dataclass
class ResultadoArchivo:
    ruta: str
    salida: str | None = None
    paginas: int = 0
    error: str | None = None
    detalle: str = ""
    sin_cambios: bool = False         # ya era un PDF y no hizo falta convertirlo


@dataclass
class ResultadoLote:
    creados: list[str] = field(default_factory=list)
    errores: list[tuple[str, str]] = field(default_factory=list)
    paginas: int = 0
    archivos: list[ResultadoArchivo] = field(default_factory=list)   # uno por archivo, en orden


def _ruta_larga(ruta: str) -> str:
    """Prefijo \\\\?\\ para que Windows acepte rutas de más de 260 caracteres."""
    if os.name != "nt" or ruta.startswith("\\\\?\\") or len(ruta) < 240:
        return ruta
    if ruta.startswith("\\\\"):
        return "\\\\?\\UNC\\" + ruta[2:]
    return "\\\\?\\" + ruta


def limite_archivo(path: str | Path) -> float:
    """Segundos que se le dan a Office para un archivo: 180, y más si el archivo es muy grande."""
    try:
        mb = os.path.getsize(_ruta_larga(os.path.abspath(path))) / 1_048_576
    except OSError:
        mb = 0.0
    return LIMITE_ARCHIVO + max(0.0, mb - 5) * LIMITE_POR_MB


def ruta_para_office(path: str | Path, carpeta: Path, i: int) -> str:
    """Ruta que se le pasa a Office: la original o, si es muy larga, una copia con nombre corto."""
    fuente = os.path.abspath(path)
    if len(fuente) <= RUTA_LARGA_OFFICE:
        return fuente
    corto = carpeta / f"doc_{i}{Path(fuente).suffix.lower()}"
    shutil.copyfile(_ruta_larga(fuente), corto)
    return str(corto)


def revisar_documento(path: str | Path) -> str | None:
    """Revisión rápida antes de abrir Office: devuelve el motivo si no se puede convertir."""
    p = Path(_ruta_larga(os.path.abspath(path)))
    if not p.is_file():
        return "No se encontró el archivo."
    try:
        with open(p, "rb") as fh:
            cabecera = fh.read(8)
    except OSError:
        return "No se pudo leer el archivo. ¿Está abierto en otro programa?"
    if not cabecera:
        return "El archivo está vacío."
    if p.suffix.lower() in _OOXML:
        if cabecera.startswith(_OLE_MAGIC):
            return MSG_CLAVE
        if not cabecera.startswith(_ZIP_MAGIC):
            return (f"El archivo está dañado o no es un documento válido de "
                    f"{NOMBRES.get(tipo_de(p) or '', 'Office')}.")
    return None


def _contar_paginas(pdf: str | Path) -> int:
    try:
        with pymupdf.open(str(pdf)) as d:
            n = d.page_count
    except Exception as exc:  # noqa: BLE001
        raise UserError("El programa no creó un PDF válido.") from exc
    if n < 1:
        raise UserError("El PDF creado no tiene páginas: ¿el documento está vacío?")
    return n


def _toc_anidado(partes: list[tuple[str, str, int]], unido: str) -> None:
    """Al unir: un marcador por archivo y, debajo, los marcadores propios de cada PDF."""
    toc = []
    for titulo, pdf, inicio in partes:
        toc.append([1, titulo, inicio + 1])
        try:
            with pymupdf.open(pdf) as d:
                for nivel, texto, pagina in d.get_toc(simple=True):
                    if pagina >= 1 and nivel >= 1:
                        toc.append([nivel + 1, texto, inicio + pagina])
        except Exception:  # noqa: BLE001
            continue
    if len(toc) == len(partes):
        return                               # no hay marcadores internos: core.merge ya los puso
    try:
        with pymupdf.open(unido) as d:
            if d.needs_pass:
                return
            d.set_toc(toc)
            d.saveIncr()
    except Exception:  # noqa: BLE001 — los marcadores internos son un extra
        pass


def lote_a_pdf(archivos: Sequence[str], *, carpeta: str | Path | None = None, unir_en: str | Path | None = None,
               opciones: OpcionesOffice | None = None, progress=None,
               destinos: Sequence[str | Path | None] | None = None,
               contrasenas: dict[str, str] | None = None) -> ResultadoLote:
    """Convierte documentos de Office (y deja pasar PDF e imágenes) a PDF.

    * ``carpeta=None``: cada PDF queda junto a su original (sin sobrescribir nada).
    * ``unir_en``: une todo en un solo PDF en el orden dado, con un marcador por archivo.
    * ``destinos``: ruta exacta de salida de cada archivo (la elige el usuario).
    Un archivo dañado o protegido se informa en su resultado y el lote continúa.
    """
    opciones = opciones or OpcionesOffice()
    archivos = [str(a) for a in archivos]
    contrasenas = contrasenas or {}
    if not archivos:
        raise UserError("Agrega al menos un archivo.")
    n = len(archivos)
    tipos = [tipo_de(a) for a in archivos]
    m = detectar()
    if any(t in NOMBRES for t in tipos) and not m.alguno:
        raise UserError(SIN_MOTOR)
    res = ResultadoLote(archivos=[ResultadoArchivo(a) for a in archivos])
    tmp = Path(tempfile.mkdtemp(prefix="ronypdf-office-"))
    convertidos: dict[int, str] = {}          # i → PDF temporal listo para unir

    def destino(i: int) -> Path:
        if destinos and i < len(destinos) and destinos[i]:
            return Path(destinos[i])
        origen = Path(archivos[i])
        base = Path(carpeta) if carpeta else origen.parent
        return core.unique_path(base / f"{origen.stem}.pdf")

    def colocar(i: int, pdf_tmp: Path) -> None:
        paginas = _contar_paginas(pdf_tmp)
        r = res.archivos[i]
        if unir_en:
            sub = tmp / f"u{i}"
            sub.mkdir(exist_ok=True)
            final = sub / f"{core.safe_filename(Path(archivos[i]).stem)[:100]}.pdf"
            os.replace(pdf_tmp, final)
            convertidos[i] = str(final)
        else:
            final_path = destino(i)
            with core._output(final_path) as t:
                shutil.copyfile(pdf_tmp, t)
            r.salida = str(final_path)
        r.paginas = paginas

    def etiqueta(i: int | None) -> str:
        if i is None:
            return "Preparando…"
        return f"Convirtiendo «{Path(archivos[i]).name}» ({i + 1} de {n})"

    try:
        # 1) Documentos de Office: Office si está; si no, LibreOffice.
        por_office: list[dict] = []
        por_libre: list[dict] = []
        limites: dict[int, float] = {}
        for i, (ruta, tipo) in enumerate(zip(archivos, tipos)):
            if tipo not in NOMBRES:
                continue
            motivo = revisar_documento(ruta)
            if motivo:
                res.archivos[i].error = motivo
                continue
            try:
                fuente = ruta_para_office(ruta, tmp, i)
            except OSError:
                res.archivos[i].error = "No se pudo leer el archivo. ¿Está abierto en otro programa?"
                continue
            tarea = {"i": i, "accion": tipo, "ruta": fuente, "salida": str(tmp / f"{i}.pdf")}
            limites[i] = limite_archivo(ruta)
            if getattr(m, tipo):
                por_office.append(tarea)
            elif m.libreoffice:
                por_libre.append(tarea)
            else:
                res.archivos[i].error = (f"No se encontró Microsoft {NOMBRES[tipo]} ni LibreOffice para "
                                         "convertir este archivo.")
        resultados: dict[int, tuple[str, str] | None] = {}
        if por_office:
            resultados.update(_convertir_con_office(
                por_office, opciones, tmp, progress, n, etiqueta, lambda i: limites.get(i, LIMITE_ARCHIVO),
                lambda i: colocar(i, tmp / f"{i}.pdf")))
        if por_libre:
            resultados.update(_convertir_con_libreoffice(
                m.libreoffice, por_libre, tmp, progress, n, etiqueta, lambda i: limites.get(i, LIMITE_ARCHIVO),
                lambda i: colocar(i, tmp / f"{i}.pdf")))
        for i, r in resultados.items():
            if r is not None:
                res.archivos[i].error, res.archivos[i].detalle = r
        for t in por_office + por_libre:
            if t["i"] not in resultados:
                res.archivos[t["i"]].error = "No se pudo convertir."

        # 2) PDF e imágenes.
        for i, (ruta, tipo) in enumerate(zip(archivos, tipos)):
            r = res.archivos[i]
            if tipo in NOMBRES:
                continue
            _report(progress, i, n, f"Revisando «{Path(ruta).name}»")
            if tipo is None:
                r.error = "Este tipo de archivo no se puede convertir a PDF."
                continue
            try:
                with core.open_pdf(ruta, contrasenas.get(ruta)) as d:
                    r.paginas = d.page_count
            except UserError as exc:
                r.error = str(exc)
                continue
            if unir_en:
                continue
            if tipo == "pdf":
                r.sin_cambios = True
                continue
            final = destino(i)
            try:
                core.images_to_pdf([ruta], final)
                r.salida = str(final)
            except UserError as exc:
                r.error = str(exc)

        # 3) Unir todo en el orden de la lista.
        if unir_en:
            items, partes, inicio = [], [], 0
            for i, r in enumerate(res.archivos):
                if r.error:
                    continue
                ruta = convertidos.get(i, archivos[i])
                items.append(core.MergeItem(ruta, "", None if i in convertidos else contrasenas.get(archivos[i])))
                partes.append((Path(archivos[i]).stem, ruta, inicio))
                inicio += r.paginas
            if items:                      # si nada se pudo convertir, los motivos quedan en cada archivo
                _report(progress, n, n + 1, "Uniendo todo en un solo PDF…")
                res.paginas = core.merge(items, unir_en, bookmarks=True)
                _toc_anidado(partes, str(unir_en))
                res.creados.append(str(unir_en))
        else:
            for r in res.archivos:
                if r.salida:
                    res.creados.append(r.salida)
                    res.paginas += r.paginas
        res.errores = [(r.ruta, r.error) for r in res.archivos if r.error]
        _report(progress, n, n, "Listo")
        return res
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def a_pdf(path: str | Path, output: str | Path, opciones: OpcionesOffice | None = None,
          progress=None) -> int:
    """Convierte un documento de Word, Excel o PowerPoint a PDF.  Devuelve las páginas del PDF."""
    if tipo_de(path) == "pdf":
        raise UserError("Ese archivo ya es un PDF.")
    res = lote_a_pdf([str(path)], destinos=[str(output)], opciones=opciones, progress=progress)
    r = res.archivos[0]
    if r.error:
        raise UserError(f"«{Path(path).name}»: {r.error}")
    return r.paginas


# --------------------------------------------------------------------------
# PDF → Word
# --------------------------------------------------------------------------

@dataclass
class ResultadoWord:
    modo: str                        # 'word' | 'interno'
    paginas: int
    avisos: list[str] = field(default_factory=list)


def _pdf_a_word_con_word(path: str, output: str, password: str | None, visible: bool, paginas: int,
                         progress) -> list[str]:
    tmp = Path(tempfile.mkdtemp(prefix="ronypdf-word-"))
    try:
        if password or core.needs_password(path):
            fuente = str(tmp / "documento.pdf")          # Word no recibe la contraseña: copia descifrada
            core.unlock(path, fuente, password)
        else:
            fuente = ruta_para_office(path, tmp, 0)
        salida = os.path.abspath(output) if visible else str(tmp / "documento.docx")
        if visible:
            try:
                Path(output).parent.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
        tarea = {"i": 0, "accion": "pdf_a_word", "ruta": fuente, "salida": salida, "visible": bool(visible)}
        tope = None if visible else LIMITE_WORD_BASE + 2 * paginas
        if visible and sys.platform == "win32":
            try:                      # que la ventana de Word pueda pasar al frente con su aviso
                import ctypes
                ctypes.windll.user32.AllowSetForegroundWindow(0xFFFFFFFF)   # ASFW_ANY
            except Exception:  # noqa: BLE001
                pass

        def etiqueta(_i):
            if visible:
                return "Word se abrió: pulsa «Aceptar» en su aviso para que convierta el PDF."
            return "Word está convirtiendo el PDF (puede tardar unos minutos)…"

        def terminado(_i):
            if not Path(salida).is_file():
                raise UserError("Word no guardó el documento.")

        res = _convertir_con_office([tarea], OpcionesOffice(), tmp, progress, 1, etiqueta,
                                    lambda _i: tope, terminado)
        r = res.get(0, ("No se pudo convertir con Word.", ""))
        if r is not None:
            if r[0].startswith("El programa tardó demasiado"):
                raise UserError("Word tardó demasiado en convertir el PDF y se detuvo. "
                                "Prueba con «Solo texto y títulos».")
            raise UserError(r[0])
        if not visible:
            with core._output(output) as t:
                shutil.copyfile(salida, t)
            return []
        return ["El documento quedó abierto en Word para que lo revises."]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def pdf_a_word(path: str | Path, output: str | Path, *, modo: str = "auto", visible: bool = False,
               idioma: str = "spa+eng", password: str | None = None, progress=None) -> ResultadoWord:
    """PDF → .docx.

    * ``modo='word'``: usa Microsoft Word, que conserva el diseño.  Word solo se usa oculto si su
      aviso «Word convertirá ahora el PDF…» ya está desactivado; si no, se abre a la vista para que
      la persona pulse «Aceptar» (con Word oculto se quedaría esperando para siempre).
    * ``modo='interno'``: texto, títulos, negritas y cursivas; con OCR en las páginas escaneadas.
    * ``modo='auto'``: escaneado → interno; si hay Word → word; si no → interno.
    """
    path, output = str(path), str(output)
    with core.open_pdf(path, password) as doc:
        paginas = doc.page_count
        escaneado = core.looks_scanned_without_text(doc)
    if modo == "auto":
        modo = "interno" if escaneado or not detectar().word else "word"
    if modo == "word":
        if not detectar().word:
            raise UserError("No se encontró Microsoft Word en este equipo. Usa «Solo texto y títulos».")
        ver = bool(visible) or not aviso_word_desactivado()
        avisos = _pdf_a_word_con_word(path, output, password, ver, paginas, progress)
        return ResultadoWord("word", paginas, avisos)
    if modo != "interno":
        raise ValueError(modo)
    return _pdf_a_word_interno(path, output, idioma, password, progress)


def _mediana_ponderada(pares: list[tuple[float, int]]) -> float:
    pares = sorted((t, w) for t, w in pares if w > 0)
    if not pares:
        return 11.0
    total = sum(w for _, w in pares)
    acumulado = 0
    for tam, w in pares:
        acumulado += w
        if acumulado * 2 >= total:
            return tam
    return pares[-1][0]


def _lineas_de_pagina(page: pymupdf.Page, textpage=None) -> list[list[dict]]:
    """Bloques de texto → lista de bloques; cada bloque, lista de líneas con sus tramos."""
    datos = page.get_text("dict", textpage=textpage) if textpage is not None else page.get_text("dict")
    bloques = []
    for b in datos.get("blocks", []):
        if b.get("type") != 0:
            continue
        lineas = []
        for ln in b.get("lines", []):
            tramos = []
            for s in ln.get("spans", []):
                texto = s.get("text", "")
                if not texto:
                    continue
                _fam, negrita, cursiva = core._span_style(s)
                tramos.append({"texto": texto, "tam": round(float(s.get("size", 0)) * 2) / 2,
                               "negrita": negrita, "cursiva": cursiva})
            if tramos and "".join(t["texto"] for t in tramos).strip():
                x0, y0, x1, y1 = ln.get("bbox", (0, 0, 0, 0))
                lineas.append({"tramos": tramos, "y0": y0, "y1": y1})
        if lineas:
            bloques.append(lineas)
    return bloques


def _tam_dominante(tramos: list[dict]) -> float:
    peso: dict[float, int] = {}
    for t in tramos:
        peso[t["tam"]] = peso.get(t["tam"], 0) + len(t["texto"].strip())
    return max(peso.items(), key=lambda kv: (kv[1], kv[0]))[0] if peso else 0.0


def _unir_tramos(lineas: list[dict]) -> list[tuple[str, bool, bool]]:
    salida: list[list] = []
    for k, ln in enumerate(lineas):
        tramos = [dict(t) for t in ln["tramos"]]
        if k and salida:
            anterior = salida[-1][0]
            primero = tramos[0]["texto"].lstrip()
            if (anterior.endswith("-") and len(anterior) > 1 and anterior[-2].isalpha()
                    and primero[:1].islower()):
                salida[-1][0] = anterior[:-1]                    # palabra cortada con guion
            elif not anterior.endswith((" ", "\t")):
                salida[-1][0] = anterior + " "
            tramos[0]["texto"] = primero
        for t in tramos:
            if salida and salida[-1][1] == t["negrita"] and salida[-1][2] == t["cursiva"]:
                salida[-1][0] += t["texto"]
            else:
                salida.append([t["texto"], t["negrita"], t["cursiva"]])
    if salida:
        salida[0][0] = salida[0][0].lstrip()
        salida[-1][0] = salida[-1][0].rstrip()
    return [(t, b, i) for t, b, i in salida if t]


def _parrafos_de_bloque(lineas: list[dict]) -> list[list[dict]]:
    """Separa un bloque en párrafos cuando cambia el tamaño de letra o hay un salto grande."""
    grupos: list[list[dict]] = []
    for ln in lineas:
        ln["tam"] = _tam_dominante(ln["tramos"])
        if grupos:
            prev = grupos[-1][-1]
            alto = max(prev["y1"] - prev["y0"], 1.0)
            cambio = abs(ln["tam"] - prev["tam"]) > 0.15 * max(prev["tam"], 1.0)
            salto = ln["y0"] - prev["y1"] > 0.9 * alto
            if not cambio and not salto:
                grupos[-1].append(ln)
                continue
        grupos.append([ln])
    return grupos


def _pdf_a_word_interno(path: str, output: str, idioma: str, password: str | None,
                        progress) -> ResultadoWord:
    avisos: list[str] = []
    paginas_ocr = 0
    sin_ocr = False
    tessdata = core.tessdata_dir()
    contenido: list[list[list[dict]]] = []
    with core.open_pdf(path, password) as doc:
        n = doc.page_count
        tam_pagina = (doc[0].rect.width, doc[0].rect.height) if n else (612.0, 792.0)
        for i in range(n):
            page = doc[i]
            _report(progress, i, n + 1, f"Leyendo la página {i + 1} de {n}")
            texto = page.get_text("text").strip()
            # OCR si la página es (casi) solo imagen: sin texto y con una imagen grande, o con una imagen que
            # la cubre casi toda, sin capa de OCR y con poco texto digital encima (un sello de radicado, un
            # encabezado, una marca de agua): ese texto no es el contenido de la página.
            usar_ocr = ((not texto and core.image_coverage(page) > 0.3)
                        or (bool(texto) and core.page_needs_ocr(page)))
            bloques = []
            if usar_ocr:
                if tessdata and all((Path(tessdata) / f"{lang}.traineddata").exists()
                                    for lang in idioma.split("+")):
                    _report(progress, i, n + 1, f"Reconociendo el texto de la página {i + 1} de {n}")
                    if page.rotation:
                        page.remove_rotation()          # el documento no se guarda: solo para leer derecho
                    try:
                        tp = page.get_textpage_ocr(language=idioma, dpi=300, full=True, tessdata=tessdata)
                        bloques = _lineas_de_pagina(page, tp)
                        paginas_ocr += 1
                    except Exception:  # noqa: BLE001 — si el OCR falla se usa lo que haya
                        bloques = []
                else:
                    sin_ocr = True
            if not bloques:
                bloques = _lineas_de_pagina(page)
            contenido.append(bloques)
    pares = [(t["tam"], len(t["texto"].strip())) for pag in contenido for b in pag for ln in b
             for t in ln["tramos"]]
    mediana = _mediana_ponderada(pares)
    parrafos: list[Parrafo] = []
    for k, pag in enumerate(contenido):
        primero = True
        for bloque in pag:
            for grupo in _parrafos_de_bloque(bloque):
                tramos = _unir_tramos(grupo)
                texto = "".join(t for t, _, _ in tramos)
                if not texto.strip():
                    continue
                tam = _tam_dominante([t for ln in grupo for t in ln["tramos"]])
                razon = tam / mediana if mediana else 1.0
                estilo = "normal"
                if razon >= 1.25 and len(texto) <= 200:
                    estilo = "titulo1" if razon >= 1.6 else "titulo2"
                parrafos.append(Parrafo(texto=texto, estilo=estilo, negrita=all(b for _, b, _ in tramos),
                                        cursiva=all(c for _, _, c in tramos), salto_pagina=k > 0 and primero,
                                        tramos=tramos))
                primero = False
        if primero and k > 0:
            parrafos.append(Parrafo(salto_pagina=True))          # página sin texto: se conserva
    _report(progress, n, n + 1, "Guardando el documento de Word…")
    escribir_docx(parrafos, output, pagina=tam_pagina)
    if paginas_ocr:
        avisos.append(f"Se reconoció el texto de {paginas_ocr} página{'s' if paginas_ocr != 1 else ''} "
                      "escaneada" + ("s" if paginas_ocr != 1 else "") + ": revisa que haya quedado bien.")
    if sin_ocr:
        avisos.append("Algunas páginas escaneadas quedaron sin texto: faltan los modelos de idioma del OCR.")
    if not any(p.texto.strip() for p in parrafos):
        avisos.append("No se encontró texto en el PDF.")
    return ResultadoWord("interno", n, avisos)


# --------------------------------------------------------------------------
# PDF → Excel
# --------------------------------------------------------------------------

def _limpiar_tabla(filas: list[list]) -> list[list[str]]:
    limpias = [[" ".join(str(c or "").split()) for c in fila] for fila in filas]
    limpias = [f for f in limpias if any(c for c in f)]
    if not limpias:
        return []
    ncol = max(len(f) for f in limpias)
    limpias = [f + [""] * (ncol - len(f)) for f in limpias]
    usadas = [c for c in range(ncol) if any(f[c] for f in limpias)]
    return [[f[c] for c in usadas] for f in limpias]


def _es_tabla_util(filas: list[list[str]]) -> bool:
    if len(filas) < 2 or max((len(f) for f in filas), default=0) < 2:
        return False
    return sum(1 for f in filas if sum(1 for c in f if c) >= 2) >= 2


def _tiene_encabezado(filas: list[list[str]]) -> bool:
    if len(filas) < 2:
        return False
    primera = [c for c in filas[0] if c]
    return bool(primera) and all(numero_colombiano(c) is None for c in primera)


def _convertir_tabla(filas: list[list[str]]) -> list[list[object]]:
    encabezado = _tiene_encabezado(filas)
    codigos = {c for c, t in enumerate(filas[0]) if encabezado and es_columna_de_codigos(t)}
    out: list[list[object]] = []
    for r, fila in enumerate(filas):
        if r == 0 and encabezado:
            out.append(list(fila))
        else:
            out.append([valor_celda(v, c in codigos) for c, v in enumerate(fila)])
    return out


def _sin_titulos(filas: list[list[str]]) -> list[list[str]]:
    """Tablas sin líneas: quita renglones de una sola celda arriba y abajo (títulos, totales en prosa)."""
    while len(filas) > 2 and sum(1 for c in filas[0] if c) <= 1:
        filas = filas[1:]
    while len(filas) > 2 and sum(1 for c in filas[-1] if c) <= 1:
        filas = filas[:-1]
    return _limpiar_tabla(filas)


def _celdas_de_renglon(palabras: list, umbral: float) -> list[list]:
    """Une las palabras de un renglón en celdas [x0, x1, texto]: un hueco mayor que ``umbral`` las separa."""
    celdas: list[list] = []
    for w in sorted(palabras, key=lambda w: w[0]):
        if celdas and w[0] - celdas[-1][1] <= umbral:
            celdas[-1][1] = max(celdas[-1][1], w[2])
            celdas[-1][2] += " " + w[4]
        else:
            celdas.append([w[0], w[2], w[4]])
    return celdas


def _banda_de(celda: list, bandas: list[list[float]]) -> int:
    """La columna que más se solapa con la celda o, si ninguna, la más cercana."""
    def puntaje(b):
        solape = min(celda[1], b[1]) - max(celda[0], b[0])
        return solape if solape > 0 else -min(abs(celda[0] - b[1]), abs(b[0] - celda[1]))
    return max(range(len(bandas)), key=lambda i: puntaje(bandas[i]))


def _tabla_de_bloque(filas: list[list[list]]) -> list[list[str]]:
    """Arma la tabla de un bloque de renglones a partir de las columnas que forman sus celdas."""
    conteos = Counter(len(f) for f in filas if len(f) >= 2)
    k = max(conteos, key=lambda n: (conteos[n], n))
    # Columnas: la unión de las celdas de los renglones típicos (los que tienen k celdas).  Así un texto
    # que se desborda sobre la columna vecina vacía, o un título de una sola celda, no las mezcla.
    intervalos = sorted((c[0], c[1]) for f in filas if len(f) == k for c in f)
    bandas: list[list[float]] = []
    for x0, x1 in intervalos:
        if bandas and x0 <= bandas[-1][1] + 1:
            bandas[-1][1] = max(bandas[-1][1], x1)
        else:
            bandas.append([x0, x1])
    tabla = []
    for f in filas:
        fila = [""] * len(bandas)
        for c in f:
            i = _banda_de(c, bandas)
            fila[i] = f"{fila[i]} {c[2]}".strip()
        tabla.append(fila)
    # Un encabezado alineado a la izquierda sobre números alineados a la derecha queda en una columna
    # aparte que ningún otro renglón usa: si dos columnas vecinas nunca coinciden en un mismo renglón y una
    # de ellas solo tiene un dato, son la misma columna.
    i = 0
    while i < len(tabla[0]) - 1:
        a = [r for r, fila in enumerate(tabla) if fila[i]]
        b = [r for r, fila in enumerate(tabla) if fila[i + 1]]
        if not set(a) & set(b) and min(len(a), len(b)) <= 1:
            for fila in tabla:
                fila[i] = fila[i] or fila[i + 1]
                del fila[i + 1]
        else:
            i += 1
    return tabla


def _tablas_por_columnas(page: pymupdf.Page) -> list[list[list[str]]]:
    """Tablas sin líneas (como las de un PDF hecho desde Excel): agrupa las palabras en renglones, une las
    que van juntas en celdas y deduce las columnas por la posición de las celdas, sin importar si cada
    columna está alineada a la izquierda, al centro o a la derecha."""
    palabras = [w for w in page.get_text("words") if w[4].strip()]
    if not palabras:
        return []
    altos = sorted(w[3] - w[1] for w in palabras)
    alto = altos[len(altos) // 2] or 10.0
    renglones: list[list] = []                      # [centro, y_abajo, palabras]
    for w in sorted(palabras, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        cy = (w[1] + w[3]) / 2
        if renglones and abs(cy - renglones[-1][0]) <= alto * 0.5:
            r = renglones[-1]
            r[2].append(w)
            r[0] = sum((p[1] + p[3]) / 2 for p in r[2]) / len(r[2])
            r[1] = max(r[1], w[3])
        else:
            renglones.append([cy, w[3], [w]])
    filas = [(r[0], _celdas_de_renglon(r[2], alto * 0.9)) for r in renglones]

    # Bloques: renglones seguidos con dos o más celdas.  Un renglón de una sola celda en medio (dato suelto)
    # no corta el bloque si el siguiente vuelve a tener varias; un salto vertical grande sí lo corta.
    bloques: list[list[list[list]]] = []
    actual: list[list[list]] = []
    previo = None
    for n, (cy, celdas) in enumerate(filas):
        lejos = previo is not None and cy - previo > alto * 3
        varias = len(celdas) >= 2
        suelto_en_medio = (not varias and actual and not lejos and n + 1 < len(filas)
                           and len(filas[n + 1][1]) >= 2 and filas[n + 1][0] - cy <= alto * 3)
        if lejos or not (varias or suelto_en_medio):
            if sum(1 for f in actual if len(f) >= 2) >= 2:
                bloques.append(actual)
            actual = []
        if varias or suelto_en_medio:
            actual.append(celdas)
        previo = cy
    if sum(1 for f in actual if len(f) >= 2) >= 2:
        bloques.append(actual)
    return [_tabla_de_bloque(b) for b in bloques]


def _tablas_de_pagina(page: pymupdf.Page) -> list[list[list[str]]]:
    # use_layout=False: sin él, find_tables intenta cargar un módulo que necesita numpy (el .exe no lo lleva).
    encontradas = page.find_tables(use_layout=False)
    tablas = [_limpiar_tabla(t.extract()) for t in encontradas.tables]
    tablas = [t for t in tablas if _es_tabla_util(t)]
    if not tablas:
        # Sin líneas: columnas deducidas por la posición de las celdas (sirve con números alineados a la
        # derecha, como en Excel).
        tablas = [_sin_titulos(_limpiar_tabla(t)) for t in _tablas_por_columnas(page)]
        tablas = [t for t in tablas if _es_tabla_util(t)]
    if not tablas:
        # Último recurso: la alineación del texto según find_tables.
        encontradas = page.find_tables(use_layout=False, strategy="text")
        tablas = [_sin_titulos(_limpiar_tabla(t.extract())) for t in encontradas.tables]
        tablas = [t for t in tablas if _es_tabla_util(t)]
    return tablas


def pdf_a_excel(path: str | Path, output: str | Path, *, paginas: str = "", agrupar: str = "tabla",
                password: str | None = None, progress=None) -> tuple[int, int]:
    """Saca las tablas del PDF a .xlsx o .csv (según la extensión de ``output``).

    ``agrupar='tabla'``: una hoja por tabla.  ``agrupar='todo'``: todas en una hoja; si una tabla
    sigue en la página siguiente con el mismo encabezado, no se repite.  Devuelve (tablas, filas).
    """
    ext = Path(output).suffix.lower()
    if ext not in (".xlsx", ".csv"):
        raise UserError("El archivo de salida debe ser .xlsx o .csv.")
    tablas: list[tuple[int, list[list[str]]]] = []
    with core.open_pdf(path, password) as doc:
        idx = core.parse_pages(paginas, doc.page_count)
        for k, i in enumerate(idx):
            _report(progress, k, len(idx) + 1, f"Buscando tablas en la página {i + 1}")
            for t in _tablas_de_pagina(doc[i]):
                tablas.append((i, t))
    if not tablas:
        raise UserError(MSG_SIN_TABLAS)
    _report(progress, len(idx), len(idx) + 1, "Guardando…")
    convertidas = [(i, _convertir_tabla(t)) for i, t in tablas]
    filas_total = sum(len(t) for _, t in convertidas)
    if agrupar == "todo":
        unidas: list[list[object]] = []
        encabezado_previo = None
        for _i, t in convertidas:
            encabezado = [str(c).lower() for c in t[0]]
            if unidas and encabezado == encabezado_previo:
                unidas.extend(t[1:])                 # la misma tabla continúa en otra página
                filas_total -= 1
            else:
                if unidas:
                    unidas.append([])
                unidas.extend(t)
            encabezado_previo = encabezado
        hojas = [("Tablas", unidas)]
    else:
        por_pagina: dict[int, int] = {}
        for i, _t in convertidas:
            por_pagina[i] = por_pagina.get(i, 0) + 1
        vistos: dict[int, int] = {}
        hojas = []
        for i, t in convertidas:
            vistos[i] = vistos.get(i, 0) + 1
            nombre = f"Página {i + 1}" if por_pagina[i] == 1 else f"Página {i + 1} - tabla {vistos[i]}"
            hojas.append((nombre, t))
    if ext == ".csv":
        filas: list[list[object]] = []
        for k, (_n, t) in enumerate(hojas):
            if k:
                filas.append([])
            filas.extend(t)
        escribir_csv(filas, output)
    else:
        escribir_xlsx(hojas, output)
    _report(progress, len(idx) + 1, len(idx) + 1, "Listo")
    return len(tablas), filas_total
