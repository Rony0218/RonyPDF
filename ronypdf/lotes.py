"""Acciones por lotes (sin interfaz): pasos, flujos guardados, plan, ejecución e informe.

Un *flujo* es una lista de pasos con sus opciones («Reconocer texto», «Comprimir», «Numerar»…) que se
aplica, uno tras otro, a cada archivo de una lista o de una carpeta.  Cada archivo se procesa en una
carpeta temporal y solo cuando todos sus pasos terminan bien el resultado se copia al destino (sin pisar
nada: si ya existe, «nombre (2).pdf»).  El original no se toca jamás.

Los pasos reutilizan las funciones de ``core``, ``censura``, ``oficina``, ``imprimir`` y ``renombrar``.
Otro módulo puede agregar pasos sin tocar este archivo: basta con que un ``ronypdf/<nombre>_ui.py``
defina ``PASOS_LOTE = [Paso(...), ...]`` (ver ``pasos_externos``).
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import tempfile
import time
import traceback
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Iterable, Sequence

from . import censura, core, oficina, renombrar
from .core import Progress, UserError, _report
from .tools import FORMATS, POSITIONS
from .widgets import Cancelled

VERSION = 1
CARPETA_FLUJOS = "RonyPDF_flujos"
LIMITE_ARCHIVOS = 10_000
DESTINO_DEFECTO = {"modo": "junto", "sufijo": "_procesado", "carpeta": "", "conservar_subcarpetas": True}
ORDEN_POSICION = {"primero": 0, "medio": 1, "ultimo": 2, "final": 3}
SIMBOLOS = {"hecho": "✓", "con aviso": "⚠", "omitido": "–", "error": "✗"}
TIPOS_CONVERTIBLES = set(oficina.OFFICE_EXTS) | set(core.IMAGE_EXTS)


def _s(n: int, uno: str, varios: str) -> str:
    return f"{n} {uno if n == 1 else varios}"


class Aviso(str):
    """Detalle de un paso que terminó, pero que conviene revisar: el archivo queda «con aviso»."""


# --------------------------------------------------------------------------
# Pasos
# --------------------------------------------------------------------------

@dataclass
class Opcion:
    clave: str
    titulo: str
    tipo: str                           # lista | entero | texto | si_no | clave | varios
    valor: object = None
    opciones: Sequence | Callable = ()  # lista/varios: [(valor, texto)…] (o una función); entero: (mínimo, máximo)
    ayuda: str = ""
    comprobar: bool = True              # lista: descartar valores que ya no están entre las opciones

    def elegibles(self) -> list[tuple[object, str]]:
        ops = self.opciones() if callable(self.opciones) else self.opciones
        return [o if isinstance(o, tuple) else (o, str(o)) for o in ops]

    def normalizar(self, v: object) -> object:
        """El valor ``v`` convertido al tipo de la opción (o el valor por defecto si no sirve)."""
        try:
            if self.tipo == "entero":
                lo, hi = (tuple(self.opciones) + (None, None))[:2] if self.opciones else (None, None)
                x = int(v)
                if lo is not None:
                    x = max(int(lo), x)
                if hi is not None:
                    x = min(int(hi), x)
                return x
            if self.tipo == "si_no":
                return bool(v)
            if self.tipo == "texto":
                return "" if v is None else str(v)
            if self.tipo == "clave":
                return None
            if self.tipo == "lista":
                if self.comprobar and v not in [o[0] for o in self.elegibles()]:
                    return self.valor
                return v
            if self.tipo == "varios":
                validos = [o[0] for o in self.elegibles()]
                return [x for x in v if x in validos] if isinstance(v, (list, tuple)) else list(self.valor or [])
        except (TypeError, ValueError):
            pass
        return list(self.valor) if isinstance(self.valor, list) else self.valor


@dataclass
class Paso:
    clave: str
    titulo: str
    descripcion: str
    opciones: list[Opcion]
    ejecutar: Callable                  # ejecutar(entrada, salida, valores, password, progress) -> str
    entra: set = field(default_factory=lambda: {".pdf"})
    sale: str = ".pdf"                  # .pdf | .txt | carpeta | "" (no deja archivo, p. ej. imprimir)
    final: bool = False                 # va al final (consume el PDF terminado)
    resumen: Callable[[dict], str] = lambda _v: ""
    preparar: Callable[[dict], object] | None = None   # corre en la interfaz antes de lanzar la tarea
    posicion: str = "medio"             # primero | medio | ultimo (los finales van después de todos)
    cambia_clave: Callable[[dict, str | None], str | None] | None = None
    validar: Callable[[dict], str | None] | None = None

    def defecto(self) -> dict:
        return {o.clave: (list(o.valor) if isinstance(o.valor, list) else o.valor)
                for o in self.opciones if o.tipo != "clave"}

    def completar(self, valores: dict | None) -> dict:
        """Opciones del paso con los valores dados (los que falten o no sirvan quedan por defecto)."""
        valores = valores or {}
        out = {}
        for o in self.opciones:
            if o.tipo == "clave":
                continue
            out[o.clave] = o.normalizar(valores[o.clave]) if o.clave in valores else o.normalizar(o.valor)
        return out

    def texto_resumen(self, valores: dict | None) -> str:
        try:
            return self.resumen(self.completar(valores))
        except Exception:  # noqa: BLE001 — un resumen nunca debe tumbar la pantalla
            return ""

    def problema(self, valores: dict | None) -> str | None:
        return self.validar(self.completar(valores)) if self.validar else None

    @property
    def rango(self) -> int:
        return ORDEN_POSICION["final"] if self.final else ORDEN_POSICION.get(self.posicion, 1)

    @property
    def pide_clave(self) -> bool:
        return any(o.tipo == "clave" for o in self.opciones)


def _borrar(ruta) -> None:
    try:
        p = Path(ruta)
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.exists():
            p.unlink()
    except OSError:
        pass


def _archivo(v: dict) -> dict:
    return v.get("_archivo") or {}


def _origen(v: dict, entrada: str) -> str:
    return _archivo(v).get("origen") or entrada


# --- 1. Convertir a PDF -----------------------------------------------------

def _ej_a_pdf(entrada, salida, v, pw, progress):
    ext = Path(entrada).suffix.lower()
    if ext in core.PDF_EXTS:
        return "ya era un PDF"
    if core.is_image(entrada):
        core.images_to_pdf([entrada], salida, progress=progress)
        return "imagen convertida a PDF"
    if ext in oficina.OFFICE_EXTS:
        opciones = oficina.OpcionesOffice(excel_ajustar_ancho=bool(v.get("excel_ancho", True)))
        res = oficina.lote_a_pdf([entrada], destinos=[salida], opciones=opciones, progress=progress)
        r = res.archivos[0]
        if r.error:
            raise UserError(r.error)
        tipo = oficina.NOMBRES.get(oficina.tipo_de(entrada) or "", "Office")
        return f"documento de {tipo} convertido ({_s(r.paginas, 'página', 'páginas')})"
    raise UserError("Este tipo de archivo no se puede convertir a PDF.")


# --- 2. OCR -----------------------------------------------------------------

def idiomas_ocr() -> list[tuple[str, str]]:
    langs = core.ocr_languages()
    out = []
    if "spa" in langs and "eng" in langs:
        out.append(("spa+eng", "Español e inglés"))
    for code in sorted(langs, key=lambda c: (c != "spa", c != "eng", c)):
        out.append((code, core.OCR_LANGUAGE_NAMES.get(code, code)))
    return out or [("spa+eng", "Español e inglés")]


def _nombre_de(opcion: Opcion, valor) -> str:
    return next((t for k, t in opcion.elegibles() if k == valor), str(valor))


LIMPIEZAS = [("auto", "Solo si el fondo es gris o manchado"), ("fuerte", "Siempre, más fuerte"), ("no", "No limpiar")]


def _ej_ocr(entrada, salida, v, pw, progress):
    # El motor de ocr.py: endereza, gira y limpia antes de reconocer (en lote no hay revisión, así que
    # basta con una lectura).  Solo se reconocen las páginas escaneadas; un sello digital no las excluye.
    from . import ocr
    opciones = ocr.OpcionesOcr(idioma=v["idioma"], ppp=int(v["calidad"]), solo_faltantes=True,
                               enderezar=bool(v.get("enderezar", True)), girar=bool(v.get("girar", True)),
                               limpiar=str(v.get("limpiar", "auto")), dos_pasadas=False)
    res = ocr.reconocer(entrada, salida, opciones, password=pw, progress=progress)
    if not res.salida:
        _borrar(salida)
        return "ya tenía texto"
    hechas, palabras = res.paginas, res.palabras
    paginas = res.resultado.reconocidas()
    arreglos = []
    enderezadas = sum(1 for p in paginas if p.angulo)
    giradas = sum(1 for p in paginas if p.giro)
    if enderezadas:
        arreglos.append(f"{enderezadas} enderezada{'s' if enderezadas != 1 else ''}")
    if giradas:
        arreglos.append(f"{giradas} girada{'s' if giradas != 1 else ''}")
    detalle = f"{_s(hechas, 'página reconocida', 'páginas reconocidas')} ({_s(palabras, 'palabra', 'palabras')}"
    detalle += (f"; {', '.join(arreglos)})" if arreglos else ")")
    if palabras == 0:
        return Aviso(f"{detalle}: no se encontró texto en las páginas escaneadas")
    return detalle


# --- 3. Comprimir -------------------------------------------------------------

NIVELES = [("baja", "Baja: sin pérdida de calidad"), ("media", "Recomendada: imágenes a 150 ppp"),
           ("alta", "Alta: imágenes a 110 ppp"), ("maxima", "Máxima: imágenes a 72 ppp")]


def _ej_comprimir(entrada, salida, v, pw, progress):
    grises = bool(v.get("grises"))
    antes, despues = core.compress(entrada, salida, v["nivel"], grises, pw, progress)
    if despues >= antes and not grises:
        _borrar(salida)
        return "ya estaba optimizado"
    ahorro = round(100 * (antes - despues) / antes) if antes else 0
    return f"{core.human_size(antes)} → {core.human_size(despues)}" + (f" (−{ahorro} %)" if ahorro > 0 else "")


# --- 4. Numerar -----------------------------------------------------------------

FORMATOS_NUMERO = [(fmt, texto) for texto, fmt in FORMATS if fmt]
POSICIONES_NUMERO = [(pos, texto) for texto, pos in POSITIONS]


def _ej_numerar(entrada, salida, v, pw, progress):
    n = core.page_numbers(entrada, salida, password=pw, fmt=v["formato"], position=v["posicion"],
                          fontsize=int(v["tamano"]), start=int(v["inicio"]), skip_first=bool(v["saltar_primera"]),
                          progress=progress)
    return f"{_s(n, 'página', 'páginas')} numeradas" if n != 1 else "1 página numerada"


# --- 5. Marca de agua ---------------------------------------------------------------

POSICIONES_MARCA = [("centro", "En el centro, inclinada"), ("superior", "Arriba"), ("inferior", "Abajo"),
                    ("mosaico", "En mosaico (repetida)")]
OPACIDADES = [(10, "10 %"), (20, "20 %"), (30, "30 %"), (45, "45 %"), (60, "60 %")]


def texto_marca(plantilla: str, origen: str) -> str:
    return (plantilla.replace("{archivo}", Path(origen).stem)
            .replace("{fecha}", _dt.date.today().strftime("%d/%m/%Y")))


def _ej_marca(entrada, salida, v, pw, progress):
    texto = texto_marca(v["texto"], _origen(v, entrada)).strip()
    if not texto:
        raise UserError("Escribe el texto de la marca de agua (en las opciones del paso).")
    n = core.watermark(entrada, salida, texto, password=pw, progress=progress, fontsize=int(v["tamano"]),
                       opacity=int(v["opacidad"]) / 100, angle=int(v["angulo"]), position=v["posicion"])
    return f"«{texto}» en {_s(n, 'página', 'páginas')}"


# --- 6. Proteger y 7. quitar la contraseña --------------------------------------------

def _ej_proteger(entrada, salida, v, pw, progress):
    clave = v.get("clave")
    if not clave:
        raise UserError("Falta la contraseña nueva del paso «Proteger con contraseña».")
    core.protect(entrada, salida, clave, "", bool(v["imprimir"]), bool(v["copiar"]), False, True, password=pw,
                 progress=progress)
    return "protegido con contraseña (AES de 256 bits)"


def _ej_quitar_clave(entrada, salida, v, pw, progress):
    with core.open_pdf(entrada, pw) as d:
        cifrado = core.is_password_protected(d)
    if not cifrado:
        return "no tenía contraseña"
    core.unlock(entrada, salida, pw, progress)
    return "contraseña quitada"


# --- 8. Censurar y 9. quitar datos ocultos ------------------------------------------------

def _patrones() -> list[tuple[str, str]]:
    return [(k, p.nombre) for k, p in censura.PATRONES.items()]


def _palabras(texto: str) -> list[str]:
    return [p.strip() for p in str(texto or "").replace(";", ",").replace("\n", ",").split(",") if p.strip()]


def _ej_censurar(entrada, salida, v, pw, progress):
    hallazgos = censura.buscar(entrada, patrones=list(v["patrones"]), palabras=_palabras(v["palabras"]),
                               usar_ocr=bool(v["usar_ocr"]), password=pw, progress=progress)
    avisos = list(getattr(hallazgos, "avisos", []))
    if not hallazgos:
        return Aviso("sin datos para censurar; " + " ".join(avisos)) if avisos else "sin datos para censurar"
    r = censura.censurar(entrada, salida, hallazgos, password=pw, progress=progress)
    detalle = f"{_s(r.zonas, 'dato censurado', 'datos censurados')}"
    if r.restos:
        return Aviso(f"{detalle}; con aviso: revisar ({_s(len(r.restos), 'texto', 'textos')} sin borrar del todo, "
                     f"p. ej. en {r.restos[0].donde})")
    if avisos:
        return Aviso(f"{detalle}; {' '.join(avisos)}")
    return detalle


NOMBRES_OCULTOS = {
    "metadatos": "Metadatos (autor, título y otros)", "adjuntos": "Archivos adjuntos", "javascript": "JavaScript",
    "comentarios": "Comentarios y anotaciones", "censuras": "Censuras marcadas sin aplicar",
    "miniaturas": "Miniaturas de página", "formularios": "Campos de formulario (se aplanan)", "enlaces": "Enlaces",
    "texto_invisible": "Texto invisible (también el del OCR)", "marcadores": "Marcadores (índice)",
}
CORTOS_OCULTOS = {"metadatos": "metadatos", "adjuntos": "adjuntos", "javascript": "JavaScript",
                  "comentarios": "comentarios", "censuras": "censuras pendientes", "miniaturas": "miniaturas",
                  "formularios": "formularios", "enlaces": "enlaces", "texto_invisible": "texto invisible",
                  "marcadores": "marcadores"}
OCULTOS_RECOMENDADOS = ["metadatos", "adjuntos", "javascript", "comentarios", "censuras", "miniaturas"]


def _ej_ocultos(entrada, salida, v, pw, progress):
    hecho = censura.limpiar(entrada, salida, list(v["categorias"]), password=pw, progress=progress)
    if not hecho:
        _borrar(salida)
        return "no había datos ocultos"
    return "se quitó: " + ", ".join(f"{CORTOS_OCULTOS.get(k, k)} ({n})" for k, n in hecho.items())


# --- 10. Pasos finales: imágenes, texto e imprimir ------------------------------------------

def _ej_a_imagenes(entrada, salida, v, pw, progress):
    hechas = core.pdf_to_images(entrada, salida, fmt=v["formato"], dpi=int(v["ppp"]), password=pw,
                                progress=progress)
    return _s(len(hechas), "imagen", "imágenes")


def _ej_a_texto(entrada, salida, v, pw, progress):
    core.extract_text(entrada, salida, password=pw, progress=progress)
    texto = Path(salida).read_text(encoding="utf-8-sig")
    letras = sum(len(r.strip()) for r in texto.splitlines() if not r.startswith("===== Página"))
    if letras == 0:
        return Aviso("el PDF no tiene texto: si es escaneado, agrega antes «Reconocer texto (OCR)»")
    return f"{letras:,} caracteres".replace(",", ".")


def _impresoras() -> list[tuple[str, str]]:
    try:
        from . import imprimir as imp
        lista = [(i.nombre, i.nombre + (" (predeterminada)" if i.predeterminada else "")) for i in imp.impresoras()]
    except Exception:  # noqa: BLE001 — sin servicio de impresión
        lista = []
    return [("", "La predeterminada del equipo")] + lista


def _prep_imprimir(v: dict) -> dict:
    """Crea y prepara la impresora en el hilo de la interfaz (la tarea solo pinta)."""
    from PySide6.QtPrintSupport import QPrinter, QPrinterInfo

    from . import imprimir as imp
    infos = imp.impresoras()
    nombre = v.get("impresora") or ""
    info = (next((i for i in infos if i.nombre == nombre), None)
            or next((i for i in infos if i.predeterminada), None) or (infos[0] if infos else None))
    if info is None:
        raise UserError("No hay ninguna impresora instalada en este equipo.")
    op = imp.OpcionesImpresion(copias=int(v.get("copias", 1)), doble_cara=v.get("doble_cara", "no"),
                               grises=bool(v.get("grises")))
    pr = QPrinter(QPrinterInfo.printerInfo(info.nombre), QPrinter.PrinterMode.HighResolution)
    imp.preparar_impresora(pr, op, imp.papel(info.papel_defecto, info.nombre), "RonyPDF: acciones por lotes")
    aviso = "" if not nombre or nombre == info.nombre else f"«{nombre}» no está instalada; se usa «{info.nombre}»"
    return {"printer": pr, "op": op, "nombre": info.nombre, "aviso": aviso}


def _ej_imprimir(entrada, salida, v, pw, progress):
    from . import imprimir as imp
    prep = v.get("_preparado")
    if not prep:
        raise UserError("La impresora no quedó lista. Vuelve a ejecutar el lote.")
    pr = prep["printer"]
    try:
        pr.setDocName(Path(_origen(v, entrada)).name)
    except Exception:  # noqa: BLE001
        pass
    hojas = imp.imprimir(entrada, pr, prep["op"], password=pw, progress=progress)
    detalle = f"{_s(hojas, 'hoja enviada', 'hojas enviadas')} a {prep['nombre']}"
    return Aviso(f"{detalle} ({prep['aviso']})") if prep.get("aviso") else detalle


# --- 11. Nombrar por contenido ----------------------------------------------------------------

def _ej_nombrar(entrada, salida, v, pw, progress):
    arch = _archivo(v)
    datos = renombrar.extraer(entrada, pw, paginas=2, usar_ocr=bool(v["usar_ocr"]),
                              palabra_clave=v.get("palabra_clave", ""))
    datos["nombre"] = Path(_origen(v, entrada)).stem
    nombre, faltan = renombrar.componer(datos, v["plantilla"], formato_fecha=v["formato_fecha"],
                                        n=int(arch.get("indice", 0)) + 1)
    if arch is not None:
        arch["nombre"] = nombre
    detalle = f"se llamará «{nombre}»"
    if faltan:
        return Aviso(f"{detalle} (falta: {', '.join(renombrar.NOMBRES_DATO.get(f, f) for f in faltan)})")
    return detalle


def _validar_plantilla(v: dict) -> str | None:
    try:
        renombrar.validar_plantilla(v.get("plantilla", ""))
    except UserError as exc:
        return str(exc)
    return None


def _crear_pasos() -> dict[str, Paso]:
    idioma = Opcion("idioma", "Idioma del texto", "lista", "spa+eng", idiomas_ocr)
    calidad = Opcion("calidad", "Calidad del reconocimiento", "lista", 300,
                     [(200, "Rápida (200 ppp)"), (300, "Normal (300 ppp)"), (400, "Alta, para letra pequeña (400 ppp)")])
    limpieza = Opcion("limpiar", "Limpiar el fondo antes de leer", "lista", "auto", LIMPIEZAS)
    nivel = Opcion("nivel", "Nivel de compresión", "lista", "media", NIVELES)
    formato_n = Opcion("formato", "Formato", "lista", "Página {n} de {total}", FORMATOS_NUMERO)
    posicion_n = Opcion("posicion", "Posición", "lista", "abajo-centro", POSICIONES_NUMERO)
    posicion_m = Opcion("posicion", "Posición", "lista", "centro", POSICIONES_MARCA)
    opacidad = Opcion("opacidad", "Opacidad", "lista", 30, OPACIDADES)
    patrones = Opcion("patrones", "Datos que se buscan", "varios", ["cedula", "celular", "correo"], _patrones)
    categorias = Opcion("categorias", "Qué se quita", "varios", list(OCULTOS_RECOMENDADOS),
                        [(k, NOMBRES_OCULTOS[k]) for k in censura.ORDEN_OCULTOS])
    formato_f = Opcion("formato_fecha", "Formato de la fecha", "lista", "AAAA-MM-DD",
                       [("AAAA-MM-DD", "AAAA-MM-DD (ordena bien)"), ("AAAAMMDD", "AAAAMMDD"),
                        ("DD-MM-AAAA", "DD-MM-AAAA")])

    pasos = [
        Paso("a_pdf", "Convertir a PDF",
             "Pasa documentos de Word, Excel y PowerPoint, y las imágenes, a PDF. Los PDF siguen igual.",
             [Opcion("excel_ancho", "Excel: ajustar las columnas al ancho de la página", "si_no", True)],
             _ej_a_pdf, entra=set(core.PDF_EXTS) | TIPOS_CONVERTIBLES, posicion="primero",
             resumen=lambda v: "Word, Excel, PowerPoint e imágenes" + ("" if v["excel_ancho"] else
                                                                       "; Excel sin ajustar al ancho")),
        Paso("ocr", "Reconocer texto (OCR)",
             "Hace buscables los escaneos. Solo se reconocen las páginas escaneadas, aunque traigan un sello "
             "digital encima; antes se enderezan, se giran y se limpian si hace falta.",
             [idioma, calidad,
              Opcion("enderezar", "Enderezar las páginas torcidas", "si_no", True),
              Opcion("girar", "Girar las páginas que están de lado o al revés", "si_no", True),
              limpieza], _ej_ocr,
             resumen=lambda v: f"{_nombre_de(idioma, v['idioma'])}, {v['calidad']} ppp"
                               + ("" if v["enderezar"] or v["girar"] else ", sin enderezar ni girar")
                               + ("" if v["limpiar"] == "auto" else
                                  ", sin limpiar el fondo" if v["limpiar"] == "no" else ", limpieza fuerte")),
        Paso("comprimir", "Comprimir", "Reduce el tamaño del archivo. Si no se logra reducir, queda igual.",
             [nivel, Opcion("grises", "Pasar a escala de grises", "si_no", False)], _ej_comprimir,
             resumen=lambda v: _nombre_de(nivel, v["nivel"]).split(":")[0] + (", en grises" if v["grises"] else "")),
        Paso("numerar", "Numerar páginas", "Escribe el número de página en cada hoja.",
             [formato_n, posicion_n, Opcion("tamano", "Tamaño de la letra", "entero", 11, (6, 36)),
              Opcion("inicio", "Empezar en el número", "entero", 1, (0, 99999)),
              Opcion("saltar_primera", "No numerar la primera página (portada)", "si_no", False)],
             _ej_numerar,
             resumen=lambda v: f"«{core.page_number_text(v['formato'], 1, 10)}», "
                               f"{_nombre_de(posicion_n, v['posicion']).lower()}"
                               + (", sin la portada" if v["saltar_primera"] else "")),
        Paso("marca", "Marca de agua", "Estampa un texto sobre las páginas. Admite {archivo} y {fecha}.",
             [Opcion("texto", "Texto", "texto", "COPIA", ayuda="Puedes usar {archivo} (nombre del archivo) y "
                                                               "{fecha} (fecha de hoy)."),
              Opcion("tamano", "Tamaño de la letra", "entero", 60, (8, 200)), opacidad,
              Opcion("angulo", "Inclinación (grados)", "entero", 45, (-90, 90)), posicion_m],
             _ej_marca,
             resumen=lambda v: f"«{v['texto']}», {_nombre_de(posicion_m, v['posicion']).lower()}, "
                               f"opacidad {v['opacidad']} %",
             validar=lambda v: None if str(v.get("texto", "")).strip() else "escribe el texto de la marca de agua"),
        Paso("proteger", "Proteger con contraseña",
             "Cifra el resultado (AES de 256 bits). La contraseña se pide al ejecutar y nunca se guarda.",
             [Opcion("clave", "Contraseña nueva", "clave", None, ayuda="Se pide al ejecutar; nunca se guarda."),
              Opcion("imprimir", "Permitir imprimir", "si_no", True),
              Opcion("copiar", "Permitir copiar texto e imágenes", "si_no", True)],
             _ej_proteger, posicion="ultimo", cambia_clave=lambda v, pw: v.get("clave") or pw,
             resumen=lambda v: "La contraseña se pide al ejecutar"
                               + ("" if v["imprimir"] else "; sin imprimir") + ("" if v["copiar"] else "; sin copiar")),
        Paso("quitar_clave", "Quitar contraseña",
             "Guarda el resultado sin contraseña (se usa la que escribas al ejecutar).", [], _ej_quitar_clave,
             cambia_clave=lambda v, pw: None, resumen=lambda v: "El resultado no pedirá contraseña"),
        Paso("censurar", "Censurar datos personales",
             "Borra de verdad cédulas, celulares, correos y otros datos (también en escaneos, con OCR).",
             [patrones, Opcion("palabras", "Palabras o nombres", "texto", "",
                               ayuda="Sepáralos con comas, por ejemplo: Ana Pérez, Luis Gómez."),
              Opcion("usar_ocr", "Buscar también en las páginas escaneadas (OCR)", "si_no", True)],
             _ej_censurar,
             resumen=lambda v: (", ".join(_nombre_de(patrones, k).split(" y ")[0].lower() for k in v["patrones"])
                                or "sin patrones") + (f"; {len(_palabras(v['palabras']))} palabras" if
                                                      _palabras(v["palabras"]) else ""),
             validar=lambda v: None if (v["patrones"] or _palabras(v["palabras"])) else
             "elige al menos un tipo de dato o escribe una palabra"),
        Paso("ocultos", "Quitar datos ocultos",
             "Quita metadatos, adjuntos, JavaScript, comentarios y otros datos que no se ven.",
             [categorias], _ej_ocultos,
             resumen=lambda v: ", ".join(CORTOS_OCULTOS.get(k, k) for k in v["categorias"]) or "nada",
             validar=lambda v: None if v["categorias"] else "elige al menos un tipo de dato oculto"),
        Paso("nombrar", "Nombrar por contenido",
             "El resultado toma un nombre con su fecha, NIT o número de factura, en lugar del sufijo.",
             [Opcion("plantilla", "Plantilla del nombre", "texto", renombrar.PLANTILLA_DEFECTO,
                     ayuda="Fichas: {fecha} {año} {mes} {dia} {nit} {nit_2} {cedula} {factura} {texto} {titulo} "
                           "{nombre} {paginas} {n} (o {n:03})."),
              formato_f, Opcion("palabra_clave", "Palabra clave para {texto}", "texto", ""),
              Opcion("usar_ocr", "Leer con OCR los escaneados", "si_no", True)],
             _ej_nombrar, resumen=lambda v: f"{v['plantilla']} · fecha {v['formato_fecha']}",
             validar=_validar_plantilla),
        Paso("a_imagenes", "Exportar como imágenes",
             "Guarda cada página como imagen, en una carpeta por archivo.",
             [Opcion("formato", "Formato", "lista", "png", [("png", "PNG (sin pérdida)"), ("jpg", "JPG (más liviano)")]),
              Opcion("ppp", "Resolución", "lista", 150, [(72, "72 ppp"), (150, "150 ppp"), (300, "300 ppp")])],
             _ej_a_imagenes, sale="carpeta", final=True,
             resumen=lambda v: f"{v['formato'].upper()} a {v['ppp']} ppp, una carpeta por archivo"),
        Paso("a_texto", "Exportar el texto (.txt)", "Guarda el texto de todas las páginas en un archivo .txt.",
             [], _ej_a_texto, sale=".txt", final=True, resumen=lambda v: "Un .txt junto al resultado"),
        Paso("imprimir", "Imprimir", "Envía cada archivo terminado a la impresora, sin preguntar uno por uno.",
             [Opcion("impresora", "Impresora", "lista", "", _impresoras, comprobar=False),
              Opcion("copias", "Copias", "entero", 1, (1, 99)),
              Opcion("doble_cara", "Doble cara", "lista", "no",
                     [("no", "No"), ("largo", "Sí, borde largo"), ("corto", "Sí, borde corto")]),
              Opcion("grises", "En blanco y negro", "si_no", False)],
             _ej_imprimir, sale="", final=True, preparar=_prep_imprimir,
             resumen=lambda v: f"{v['impresora'] or 'Impresora predeterminada'}, {_s(v['copias'], 'copia', 'copias')}"
                               + ("" if v["doble_cara"] == "no" else ", doble cara") + (", grises" if v["grises"] else "")),
    ]
    return {p.clave: p for p in pasos}


PASOS: dict[str, Paso] = _crear_pasos()
PROPIOS = tuple(PASOS)


def pasos_externos(modulos: Iterable) -> list[Paso]:
    """Agrega a ``PASOS`` los pasos que otros módulos declaran en ``PASOS_LOTE``.  Devuelve los nuevos."""
    nuevos = []
    for m in modulos:
        for p in getattr(m, "PASOS_LOTE", ()) or ():
            if isinstance(p, Paso) and p.clave not in PASOS:
                PASOS[p.clave] = p
                nuevos.append(p)
    return nuevos


def ordenar(pasos: Sequence[tuple[str, dict]]) -> tuple[list[tuple[str, dict]], list[str]]:
    """Acomoda los pasos: «Convertir a PDF» primero; «Proteger» y los finales al final.

    Devuelve (pasos ordenados, títulos de los que se movieron)."""
    def rango(item):
        p = PASOS.get(item[0])
        return p.rango if p else 1
    ordenados = sorted(pasos, key=rango)        # sort es estable: el resto conserva su orden
    movidos = []
    for k, item in enumerate(pasos):
        if ordenados.index(item) != k and item[0] in PASOS and PASOS[item[0]].rango != 1:
            titulo = PASOS[item[0]].titulo
            if titulo not in movidos:
                movidos.append(titulo)
    return list(ordenados), movidos


# --------------------------------------------------------------------------
# Flujos
# --------------------------------------------------------------------------

@dataclass
class Flujo:
    nombre: str
    pasos: list[tuple[str, dict]] = field(default_factory=list)
    destino: dict = field(default_factory=lambda: dict(DESTINO_DEFECTO))
    al_error: str = "seguir"                     # seguir | detener
    ejemplo: bool = False
    avisos: list[str] = field(default_factory=list)

    def copia(self, **cambios) -> "Flujo":
        f = Flujo(self.nombre, [(c, json.loads(json.dumps(v))) for c, v in self.pasos], dict(self.destino),
                  self.al_error, self.ejemplo, list(self.avisos))
        return replace(f, **cambios) if cambios else f

    def a_json(self) -> dict:
        pasos = []
        for clave, valores in self.pasos:
            paso = PASOS.get(clave)
            secretas = {o.clave for o in paso.opciones if o.tipo == "clave"} if paso else set()
            limpios = {k: v for k, v in (valores or {}).items() if k not in secretas and not str(k).startswith("_")}
            pasos.append({"paso": clave, "valores": limpios})
        destino = {k: self.destino.get(k, v) for k, v in DESTINO_DEFECTO.items()}
        return {"version": VERSION, "nombre": self.nombre, "pasos": pasos, "destino": destino,
                "al_error": self.al_error}

    @classmethod
    def de_json(cls, data: dict) -> "Flujo":
        if not isinstance(data, dict):
            raise UserError("El archivo del flujo no es válido.")
        avisos = []
        if int(data.get("version", 1) or 1) > VERSION:
            avisos.append("Este flujo se guardó con una versión más nueva de RonyPDF: revisa sus pasos.")
        pasos = []
        for item in data.get("pasos", []):
            clave = item.get("paso") if isinstance(item, dict) else None
            paso = PASOS.get(clave)
            if paso is None:
                avisos.append(f"El paso «{clave}» ya no existe en esta versión: se omitió.")
                continue
            pasos.append((clave, paso.completar(item.get("valores") or {})))
        destino = dict(DESTINO_DEFECTO)
        destino.update({k: v for k, v in (data.get("destino") or {}).items() if k in DESTINO_DEFECTO})
        destino["conservar_subcarpetas"] = bool(destino["conservar_subcarpetas"])
        al_error = data.get("al_error") if data.get("al_error") in ("seguir", "detener") else "seguir"
        return cls(str(data.get("nombre") or "Flujo sin nombre"), pasos, destino, al_error, avisos=avisos)


def ejemplos() -> list[Flujo]:
    """Flujos de ejemplo (genéricos) que se pueden duplicar y editar."""
    def con(clave, **valores):
        return (clave, PASOS[clave].completar(valores))
    return [
        Flujo("Escaneos listos para archivar", [con("ocr"), con("comprimir", nivel="media")], ejemplo=True),
        Flujo("Para enviar por correo", [con("comprimir", nivel="alta")], ejemplo=True),
        Flujo("Publicar sin datos personales",
              [con("censurar", patrones=["cedula", "celular", "correo"]), con("ocultos"),
               con("comprimir", nivel="media")], ejemplo=True),
        Flujo("Numerar y marcar como copia",
              [con("numerar", formato="Página {n} de {total}"), con("marca", texto="COPIA", posicion="centro")],
              ejemplo=True),
    ]


def carpeta_flujos(carpeta: str | Path | None = None) -> Path:
    return Path(carpeta) if carpeta else renombrar.carpeta_datos(CARPETA_FLUJOS)


def _archivo_flujo(nombre: str, carpeta) -> Path:
    return carpeta_flujos(carpeta) / f"{core.safe_filename(nombre.strip(), 'flujo')}.json"


def guardar_flujo(flujo: Flujo, carpeta: str | Path | None = None) -> Path:
    """Guarda el flujo como un .json legible (sin contraseñas)."""
    if not flujo.nombre.strip():
        raise UserError("Escribe un nombre para el flujo.")
    ruta = _archivo_flujo(flujo.nombre, carpeta)
    try:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        with core._output(ruta) as tmp:
            Path(tmp).write_text(json.dumps(flujo.a_json(), ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        raise UserError(f"No se pudo guardar el flujo en «{ruta.parent}».") from exc
    return ruta


def cargar_flujo(ruta: str | Path) -> Flujo:
    try:
        data = json.loads(Path(ruta).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UserError(f"No se pudo leer el flujo «{Path(ruta).stem}».") from exc
    return Flujo.de_json(data)


def listar_flujos(carpeta: str | Path | None = None) -> list[Flujo]:
    base = carpeta_flujos(carpeta)
    out = []
    if base.is_dir():
        for p in base.glob("*.json"):
            try:
                out.append(cargar_flujo(p))
            except UserError:
                continue
    return sorted(out, key=lambda f: f.nombre.casefold())


def borrar_flujo(nombre: str, carpeta: str | Path | None = None) -> bool:
    ruta = _archivo_flujo(nombre, carpeta)
    try:
        ruta.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise UserError(f"No se pudo eliminar el flujo «{nombre}».") from exc


# --------------------------------------------------------------------------
# Plan
# --------------------------------------------------------------------------

@dataclass
class Tarea:
    origen: str
    raiz: str | None = None                       # carpeta agregada de la que viene (para las subcarpetas)
    salida: str | None = None                     # resultado previsto (None: lo decide «Nombrar» o no hay archivo)
    pasos: list[int] = field(default_factory=list)   # índices de flujo.pasos que se le aplican
    avisos: list[str] = field(default_factory=list)
    omitir: str | None = None                     # motivo para no procesarlo
    protegido: bool = False


@dataclass
class Plan:
    flujo: Flujo
    tareas: list[Tarea] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    problema: str | None = None                   # si no es None, no se puede ejecutar

    @property
    def activas(self) -> list[Tarea]:
        return [t for t in self.tareas if not t.omitir]

    @property
    def protegidos(self) -> list[Tarea]:
        return [t for t in self.activas if t.protegido]

    def titulos(self, t: Tarea) -> list[str]:
        return [PASOS[self.flujo.pasos[k][0]].titulo for k in t.pasos]


def tipos_entrada(flujo: Flujo) -> set[str]:
    """Extensiones que acepta el flujo (PDF, y Office e imágenes si empieza por «Convertir a PDF»)."""
    if flujo.pasos and flujo.pasos[0][0] in PASOS:
        return set(PASOS[flujo.pasos[0][0]].entra) | set(core.PDF_EXTS)
    return set(core.PDF_EXTS)


def _dentro(ruta: str, carpeta: str | None) -> bool:
    if not carpeta:
        return False
    try:
        r, c = os.path.normcase(os.path.abspath(ruta)), os.path.normcase(os.path.abspath(carpeta))
        return os.path.commonpath([r, c]) == c
    except ValueError:                             # unidades distintas
        return False


def expandir(entradas: Iterable, recursivo: bool = False, tipos: set[str] | None = None,
             excluir: str | None = None, limite: int = LIMITE_ARCHIVOS) -> tuple[list[tuple[str, str | None]], list[str]]:
    """Archivos de la lista y de las carpetas (sin repetir).  Devuelve ([(ruta, raíz)], avisos).

    ``entradas``: rutas o tuplas (ruta, raíz).  De las carpetas solo se toman los tipos de ``tipos``; un
    archivo suelto se toma siempre (el plan dirá si no se puede procesar).  ``excluir``: carpeta que no se
    recorre (la de destino, si queda dentro de la de origen)."""
    tipos = {t.lower() for t in (tipos or set(core.PDF_EXTS) | TIPOS_CONVERTIBLES)}
    vistos: set[str] = set()
    out: list[tuple[str, str | None]] = []
    avisos: list[str] = []
    lleno = False

    def agregar(ruta: str, raiz: str | None) -> bool:
        nonlocal lleno
        clave = os.path.normcase(os.path.abspath(ruta))
        if clave in vistos:
            return True
        if len(out) >= limite:
            lleno = True
            return False
        vistos.add(clave)
        out.append((ruta, raiz))
        return True

    for e in entradas:
        ruta, raiz = (e if isinstance(e, tuple) else (e, None))
        ruta = str(ruta)
        if os.path.isdir(ruta):
            if recursivo:
                for actual, dirs, archivos in os.walk(ruta):
                    dirs[:] = sorted((d for d in dirs if not _dentro(os.path.join(actual, d), excluir)),
                                     key=str.casefold)
                    for a in sorted(archivos, key=str.casefold):
                        if Path(a).suffix.lower() in tipos and not a.startswith(".~"):
                            if not agregar(os.path.join(actual, a), ruta):
                                break
                    if lleno:
                        break
            else:
                try:
                    hijos = sorted(os.scandir(ruta), key=lambda d: d.name.casefold())
                except OSError:
                    avisos.append(f"No se pudo leer la carpeta «{ruta}».")
                    continue
                for d in hijos:
                    if d.is_file() and Path(d.name).suffix.lower() in tipos and not d.name.startswith(".~"):
                        if not agregar(d.path, ruta):
                            break
        elif os.path.isfile(ruta):
            agregar(ruta, raiz)
        else:
            avisos.append(f"No se encontró «{ruta}».")
        if lleno:
            break
    if lleno:
        avisos.append(f"Se tomaron solo los primeros {limite:,} archivos.".replace(",", "."))
    return out, avisos


def carpeta_destino(flujo: Flujo, origen: str, raiz: str | None) -> Path:
    d = flujo.destino
    if d.get("modo") == "carpeta" and d.get("carpeta"):
        base = Path(d["carpeta"])
        if d.get("conservar_subcarpetas", True) and raiz:
            try:
                rel = Path(origen).parent.relative_to(Path(raiz))
            except ValueError:
                rel = Path()
            base = base / rel
        return base
    return Path(origen).parent


def _salida_libre(ruta: Path, reservadas: set[str]) -> Path:
    cand, k = ruta, 1
    while os.path.normcase(str(cand)) in reservadas or cand.exists():
        k += 1
        cand = ruta.with_name(f"{ruta.stem} ({k}){ruta.suffix}")
    return cand


def produce_pdf(pasos: Sequence[str]) -> bool:
    """¿El resultado incluye un PDF?  (Si solo hay pasos finales, como imprimir, no se guarda una copia.)"""
    return any(not PASOS[c].final for c in pasos if c in PASOS)


def planificar(flujo: Flujo, entradas: Iterable, recursivo: bool = False) -> Plan:
    """Qué se hará con cada archivo, sin tocar nada."""
    plan = Plan(flujo)
    plan.avisos.extend(flujo.avisos)
    if not flujo.pasos:
        plan.problema = "El flujo no tiene pasos: agrega al menos uno."
    for k, (clave, valores) in enumerate(flujo.pasos):
        paso = PASOS.get(clave)
        if paso is None:
            plan.problema = plan.problema or f"El paso «{clave}» no existe."
            continue
        msg = paso.problema(valores)
        if msg and not plan.problema:
            plan.problema = f"«{paso.titulo}»: {msg[0].upper() + msg[1:]}."
    d = flujo.destino
    excluir = None
    if d.get("modo") == "carpeta":
        if not d.get("carpeta"):
            plan.problema = plan.problema or "Elige la carpeta de destino."
        else:
            excluir = d["carpeta"]
    archivos, avisos = expandir(entradas, recursivo, tipos_entrada(flujo), excluir)
    plan.avisos.extend(avisos)
    convierte = bool(flujo.pasos) and flujo.pasos[0][0] == "a_pdf"
    claves = [c for c, _ in flujo.pasos]
    reservadas: set[str] = set()
    sufijo = str(d.get("sufijo", "") or "")
    nombra = "nombrar" in claves
    for ruta, raiz in archivos:
        t = Tarea(ruta, raiz)
        plan.tareas.append(t)
        if excluir and _dentro(ruta, excluir):
            t.omitir = "está en la carpeta de destino"
            continue
        ext = Path(ruta).suffix.lower()
        es_pdf = ext in core.PDF_EXTS
        if not es_pdf and not (convierte and ext in TIPOS_CONVERTIBLES):
            t.omitir = "no es un PDF"
            t.avisos.append("no es PDF y el flujo no convierte" if ext in TIPOS_CONVERTIBLES
                            else "este tipo de archivo no se puede procesar")
            continue
        t.pasos = [k for k, c in enumerate(claves) if c in PASOS and not (es_pdf and c == "a_pdf")]
        if not t.pasos:
            t.omitir = "ya es un PDF: no hay nada que hacer"
            continue
        if es_pdf:
            t.protegido = core.needs_password(ruta)
            if t.protegido:
                t.avisos.append("protegido: se pedirá la contraseña")
        pasos_t = [claves[k] for k in t.pasos]
        base = carpeta_destino(flujo, ruta, raiz)
        stem = core.safe_filename(Path(ruta).stem)
        if produce_pdf(pasos_t):
            prevista = base / f"{stem}{sufijo}.pdf"
        elif "a_texto" in pasos_t:
            prevista = base / f"{stem}{sufijo}.txt"
        elif "a_imagenes" in pasos_t:
            prevista = base / f"{stem}{sufijo}_imagenes"
        else:
            prevista = None
        if prevista is not None and nombra:
            t.avisos.append("el nombre se decide al leer el contenido")
            prevista = None
        if prevista is not None:
            libre = _salida_libre(prevista, reservadas)
            if libre != prevista:
                t.avisos.append(f"ya existe: se guardará como «{libre.name}»")
            reservadas.add(os.path.normcase(str(libre)))
            t.salida = str(libre)
    if not plan.tareas:
        plan.problema = plan.problema or "Agrega archivos o una carpeta."
    elif not plan.activas:
        plan.problema = plan.problema or "Ningún archivo de la lista se puede procesar con este flujo."
    return plan


# --------------------------------------------------------------------------
# Ejecución
# --------------------------------------------------------------------------

@dataclass
class ResultadoArchivo:
    origen: str
    estado: str = "hecho"                        # hecho | con aviso | omitido | error
    salida: str | None = None
    extras: list[str] = field(default_factory=list)
    pasos: list[tuple[str, str]] = field(default_factory=list)    # (paso, detalle)
    mensaje: str = ""                            # motivo del error o de la omisión
    segundos: float = 0.0
    antes: int = 0
    despues: int = 0

    @property
    def detalle(self) -> str:
        partes = [f"{p}: {d}" for p, d in self.pasos]
        if self.mensaje:
            partes.append(self.mensaje)
        return " · ".join(partes)


@dataclass
class Informe:
    flujo: str = ""
    inicio: float = 0.0
    archivos: list[ResultadoArchivo] = field(default_factory=list)
    duracion: float = 0.0
    cancelado: bool = False
    destino: str | None = None                   # carpeta de destino (si es una sola)

    def totales(self) -> dict[str, int]:
        t = {"hecho": 0, "con aviso": 0, "omitido": 0, "error": 0}
        for r in self.archivos:
            t[r.estado] = t.get(r.estado, 0) + 1
        return t

    def resumen(self) -> str:
        t = self.totales()
        partes = [_s(t["hecho"], "hecho", "hechos")]
        if t["con aviso"]:
            partes.append(f"{t['con aviso']} con aviso")
        if t["omitido"]:
            partes.append(_s(t["omitido"], "omitido", "omitidos"))
        if t["error"]:
            partes.append(f"{t['error']} con error")
        partes.append(duracion_texto(self.duracion))
        if self.cancelado:
            partes.append("Cancelado")
        return " · ".join(partes)

    def a_filas(self) -> list[tuple[str, str, str, str, str]]:
        """(símbolo, archivo, resultado, detalle, antes → después) por archivo."""
        filas = []
        for r in self.archivos:
            resultado = Path(r.salida).name if r.salida else (Path(r.extras[0]).name if r.extras else "—")
            tam = (f"{core.human_size(r.antes)} → {core.human_size(r.despues)}" if r.salida and r.antes
                   else (core.human_size(r.antes) if r.antes else ""))
            filas.append((SIMBOLOS.get(r.estado, ""), Path(r.origen).name, resultado, r.detalle, tam))
        return filas

    def _filas_completas(self) -> list[list[object]]:
        filas: list[list[object]] = [["Estado", "Archivo", "Carpeta", "Resultado", "Detalle", "Tamaño antes (KB)",
                                      "Tamaño después (KB)", "Segundos"]]
        for r in self.archivos:
            filas.append([r.estado, Path(r.origen).name, str(Path(r.origen).parent), r.salida or
                          (r.extras[0] if r.extras else ""), r.detalle, round(r.antes / 1024, 1) if r.antes else "",
                          round(r.despues / 1024, 1) if r.despues else "", round(r.segundos, 1)])
        return filas

    def guardar_xlsx(self, ruta) -> None:
        t = self.totales()
        resumen = [["Dato", "Valor"], ["Flujo", self.flujo],
                   ["Fecha", _dt.datetime.fromtimestamp(self.inicio or time.time()).strftime("%d/%m/%Y %H:%M")],
                   ["Archivos", len(self.archivos)], ["Hechos", t["hecho"]], ["Con aviso", t["con aviso"]],
                   ["Omitidos", t["omitido"]], ["Con error", t["error"]], ["Duración", duracion_texto(self.duracion)],
                   ["Cancelado", "Sí" if self.cancelado else "No"]]
        oficina.escribir_xlsx([("Informe", self._filas_completas()), ("Resumen", resumen)], ruta)

    def guardar_txt(self, ruta) -> None:
        lineas = [f"Informe de acciones por lotes · {self.flujo}",
                  _dt.datetime.fromtimestamp(self.inicio or time.time()).strftime("%d/%m/%Y %H:%M"),
                  self.resumen(), ""]
        for r in self.archivos:
            lineas.append(f"{SIMBOLOS.get(r.estado, '')} {r.estado.upper()}: {r.origen}")
            if r.salida:
                lineas.append(f"    Resultado: {r.salida}")
            for e in r.extras:
                lineas.append(f"    También: {e}")
            for p, d in r.pasos:
                lineas.append(f"    · {p}: {d}")
            if r.mensaje:
                lineas.append(f"    {r.mensaje}")
        with core._output(ruta) as tmp:
            Path(tmp).write_text("\n".join(lineas) + "\n", encoding="utf-8-sig")


def duracion_texto(seg: float) -> str:
    seg = int(round(seg))
    if seg < 60:
        return f"{seg} s"
    m, s = divmod(seg, 60)
    if m < 60:
        return f"{m} min {s} s" if s else f"{m} min"
    h, m = divmod(m, 60)
    return f"{h} h {m} min"


def _clave_nueva(claves: dict | None, k: int, opcion: str) -> str | None:
    if not claves:
        return None
    por_paso = claves.get(k)
    if isinstance(por_paso, dict) and por_paso.get(opcion):
        return por_paso[opcion]
    if isinstance(por_paso, str):
        return por_paso
    v = claves.get(opcion)
    return v if isinstance(v, str) else None


def _copiar_al_destino(fuente: str | Path, destino: Path) -> None:
    """Copia de forma atómica (archivo temporal en la carpeta de destino y luego reemplazo)."""
    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        if Path(fuente).is_dir():
            tmp = destino.with_name(f".~{destino.name}.{os.getpid()}.tmp")
            try:
                shutil.copytree(fuente, tmp)
                os.rename(tmp, destino)
            finally:
                if tmp.exists():
                    shutil.rmtree(tmp, ignore_errors=True)
        else:
            with core._output(destino) as tmp:
                shutil.copyfile(fuente, tmp)
    except PermissionError as exc:
        raise UserError(f"No hay permiso para escribir en la carpeta «{destino.parent}».") from exc
    except UserError:
        raise
    except OSError as exc:
        raise UserError(f"No se pudo guardar «{destino.name}» en «{destino.parent}»: "
                        f"{exc.strerror or exc}.") from exc


def _procesar(plan: Plan, t: Tarea, i: int, r: ResultadoArchivo, pw: str | None, claves_nuevas, preparados,
              avanzar: Callable[[str], None]) -> None:
    flujo = plan.flujo
    tmp = Path(tempfile.mkdtemp(prefix="ronypdf-lote-"))
    creados: list[Path] = []
    try:
        actual = t.origen
        stem = core.safe_filename(Path(t.origen).stem)
        archivo = {"origen": t.origen, "indice": i, "nombre": None}
        finales: list[tuple[str, Path]] = []
        hubo_pdf = False
        for k in t.pasos:
            clave, valores = flujo.pasos[k]
            paso = PASOS[clave]
            v = paso.completar(valores)
            for o in paso.opciones:
                if o.tipo == "clave":
                    v[o.clave] = _clave_nueva(claves_nuevas, k, o.clave)
            v["_archivo"] = archivo
            if paso.preparar is not None:
                v["_preparado"] = (preparados or {}).get(k)
            avanzar(paso.titulo)                 # aquí también se puede cancelar, entre paso y paso
            carpeta = tmp / str(k)
            carpeta.mkdir()
            salida = carpeta / {".pdf": f"{stem}.pdf", ".txt": f"{stem}.txt",
                                "carpeta": f"{stem}_imagenes"}.get(paso.sale, "sin_salida")
            try:
                detalle = paso.ejecutar(actual, str(salida), v, pw,
                                        lambda a, b, m="", _t=paso.titulo: avanzar(f"{_t}: {m}" if m else _t))
            except (Cancelled, MemoryError, core.PasswordError):
                raise
            except UserError as exc:
                raise UserError(f"{paso.titulo}: {exc}") from exc
            except Exception as exc:  # noqa: BLE001 — p. ej. un PDF dañado que MuPDF no puede leer
                raise UserError(f"{paso.titulo}: error inesperado ({type(exc).__name__}: {exc})") from exc
            r.pasos.append((paso.titulo, str(detalle)))
            if isinstance(detalle, Aviso):
                r.estado = "con aviso"
            if paso.sale == ".pdf":
                if salida.exists():
                    actual = str(salida)
                if not paso.final:
                    hubo_pdf = True
            elif paso.sale in (".txt", "carpeta") and salida.exists():
                finales.append((paso.sale, salida))
            if paso.cambia_clave is not None:
                pw = paso.cambia_clave(v, pw)
        # Todo salió bien: se copian los resultados al destino (sin pisar nada).
        base = carpeta_destino(flujo, t.origen, t.raiz)
        nombre = archivo["nombre"] or f"{stem}{flujo.destino.get('sufijo', '') or ''}"
        nombre = core.safe_filename(nombre)
        try:
            if hubo_pdf:
                destino = core.unique_path(base / f"{nombre}.pdf")
                _copiar_al_destino(actual, destino)
                creados.append(destino)
                r.salida = str(destino)
                r.despues = destino.stat().st_size
            for tipo, fuente in finales:
                destino = core.unique_path(base / (f"{nombre}.txt" if tipo == ".txt" else f"{nombre}_imagenes"))
                _copiar_al_destino(fuente, destino)
                creados.append(destino)
                r.extras.append(str(destino))
        except BaseException:
            for c in creados:                    # sin resultados a medias
                _borrar(c)
            r.salida, r.extras = None, []
            raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def ejecutar(plan: Plan, contrasenas: dict[str, str] | None = None, claves_nuevas: dict | None = None,
             progress: Progress = None, preparados: dict | None = None) -> Informe:
    """Procesa cada archivo del plan.  Un error en un archivo no detiene a los demás (salvo con «detener»).

    ``contrasenas``: ruta → contraseña de los PDF protegidos.  ``claves_nuevas``: índice del paso →
    {opción: contraseña} (las opciones de tipo «clave», como la de «Proteger»).  ``preparados``: índice del
    paso → lo que devolvió su ``preparar`` (la impresora).  Si se cancela, se devuelve el informe con lo
    hecho hasta ese momento y ``cancelado=True``."""
    contrasenas = {os.path.normcase(os.path.abspath(k)): v for k, v in (contrasenas or {}).items()}
    inf = Informe(flujo=plan.flujo.nombre, inicio=time.time())
    if plan.flujo.destino.get("modo") == "carpeta":
        inf.destino = plan.flujo.destino.get("carpeta") or None
    n = len(plan.tareas)
    detener = False
    for i, t in enumerate(plan.tareas):
        r = ResultadoArchivo(t.origen)
        inf.archivos.append(r)
        try:
            r.antes = os.path.getsize(t.origen)
        except OSError:
            r.antes = 0
        if inf.cancelado:
            r.estado, r.mensaje = "omitido", "No se procesó: se canceló el lote"
            continue
        if detener:
            r.estado, r.mensaje = "omitido", "No se procesó: el lote se detuvo por un error"
            continue
        if t.omitir:
            r.estado, r.mensaje = "omitido", f"Omitido: {t.omitir}"
            continue
        nombre = Path(t.origen).name
        pw = contrasenas.get(os.path.normcase(os.path.abspath(t.origen)))

        def avanzar(msg: str, _i=i, _n=nombre) -> None:
            _report(progress, _i, n, f"«{_n}» ({_i + 1} de {n})" + (f"\n{msg}" if msg else ""))

        t0 = time.perf_counter()
        try:
            avanzar("")
            if t.protegido and not pw:
                raise UserError("Protegido: no se pudo abrir (falta la contraseña)")
            _procesar(plan, t, i, r, pw, claves_nuevas, preparados, avanzar)
        except Cancelled:
            inf.cancelado = True
            r.estado, r.pasos = "omitido", []
            r.mensaje = "Cancelado: no quedó ningún resultado a medias"
        except core.PasswordError:
            r.estado, r.mensaje = "error", "Protegido: la contraseña no es correcta"
        except UserError as exc:
            r.estado, r.mensaje = "error", str(exc)
        except MemoryError:
            r.estado, r.mensaje = "error", "No hay suficiente memoria para procesar este archivo."
        except PermissionError as exc:
            r.estado, r.mensaje = "error", f"No hay permiso para escribir: {exc.filename or exc}"
        except Exception as exc:  # noqa: BLE001 — un archivo dañado no detiene el lote
            traceback.print_exc()
            r.estado, r.mensaje = "error", f"Error inesperado: {type(exc).__name__}: {exc}"
        r.segundos = time.perf_counter() - t0
        if r.estado == "error":
            r.salida, r.extras = None, []
            if plan.flujo.al_error == "detener":
                detener = True
    inf.duracion = time.time() - inf.inicio
    if not inf.cancelado:
        try:
            _report(progress, n, n, "Listo")
        except Cancelled:
            pass
    return inf
