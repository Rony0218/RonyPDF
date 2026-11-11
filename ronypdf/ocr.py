"""OCR de primer nivel (sin interfaz): enderezar, girar, limpiar, estimar la confianza y escribir la capa.

El reconocimiento lo hace el Tesseract que trae MuPDF, en este equipo y sin internet.  Encima de eso:

* **Enderezar:** la inclinación se mide con perfiles de proyección (``detectar_inclinacion``).  Si la página es
  solo un escaneo, su imagen se cambia por la enderezada y la página se ve derecha; si tiene contenido digital
  encima (un sello, un encabezado), la página no cambia: se reconoce sobre una copia enderezada y la capa de
  texto se escribe girada para que caiga sobre las palabras que se ven.
* **Girar:** si la lectura casi no trae palabras del diccionario, se prueban 90°, 180° y 270°.
* **Limpiar:** el fondo gris o manchado se lleva a blanco antes de reconocer (tabla de 256 valores aplicada
  con ``bytes.translate``, sin numpy).
* **Confianza estimada:** MuPDF no entrega la de Tesseract, así que se estima con dos lecturas distintas, un
  diccionario sacado del propio modelo de idioma (``diccionario``), caracteres raros y la forma de la caja.

Las coordenadas de las palabras (``PalabraOcr.rect``) son puntos de la página tal como se ve, después de girarla
y enderezarla.  ``analizar`` no escribe nada; ``escribir`` hace los cambios en una copia (sufijo _ocr) y
conserva la contraseña del original.
"""

from __future__ import annotations

import collections
import difflib
import functools
import math
import os
import re
import struct
import sys
import threading
import time
import unicodedata
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterable, Sequence

import pymupdf

from . import core
from .core import Progress, UserError, _report

Rect4 = tuple[float, float, float, float]

UMBRAL_SEGURA = 0.80        # 0,80 o más: segura
UMBRAL_MUY_DUDOSA = 0.50    # de 0,50 a 0,79: dudosa (ámbar); menos de 0,50: muy dudosa (rojo)

INCLINACION_MIN = 0.3       # grados: por debajo no vale la pena enderezar
INCLINACION_MAX = 15.0      # por encima no es una página torcida sino otra cosa (se deja como está)

NOMBRE_PERSONAL = "RonyPDF_diccionario.txt"
ruta_personal: Path | None = None   # la interfaz o las pruebas pueden fijarla; por defecto, junto a los ajustes

# Motivos (en palabras claras: se muestran en la pantalla de revisión)
M_DIFIEREN = "las dos lecturas no coinciden"
M_FALTA = "solo apareció en una de las dos lecturas"
M_DICCIONARIO = "no está en el diccionario"
M_NO_PALABRA = "no parece una palabra ni un número"
M_MEZCLA = "tiene letras y números mezclados"
M_SIMBOLOS = "tiene símbolos raros"
M_MAYUSCULA = "tiene una mayúscula en medio"
M_ILEGIBLE = "tiene un carácter que no se pudo leer"
M_REPETIDAS = "tiene tres letras iguales seguidas"
M_TAMANO = "su tamaño no corresponde al de su renglón"
M_SUELTA = "es una letra suelta"


# --------------------------------------------------------------------------
# Datos
# --------------------------------------------------------------------------

@dataclass
class OpcionesOcr:
    idioma: str = "spa+eng"
    ppp: int = 300
    solo_faltantes: bool = True
    paginas: str = ""
    enderezar: bool = True
    girar: bool = True
    limpiar: str = "auto"           # 'no' | 'auto' | 'fuerte'
    enderezar_pagina: bool = True   # en un escaneo puro, la imagen se cambia por la enderezada
    limpiar_pagina: bool = False    # la imagen visible también se limpia (solo escaneos puros)
    dos_pasadas: bool = True


@dataclass
class PalabraOcr:
    texto: str
    rect: Rect4                       # puntos de la página tal como se ve
    confianza: float = 1.0            # 0 a 1 (estimada)
    motivos: list[str] = field(default_factory=list)
    renglon: int = 0
    corregida: str | None = None      # lo que escribió la persona ("" = quitar la palabra)
    revisada: bool = False            # la persona ya la vio (aceptada o corregida)
    otra: str | None = None           # lo que leyó la otra pasada, si fue distinto
    marco: Rect4 | None = None        # caja sin inclinación, cuando la página se deja torcida (ver PaginaOcr)

    @property
    def final(self) -> str:
        return self.texto if self.corregida is None else self.corregida

    @property
    def nivel(self) -> str:
        """'segura', 'dudosa' o 'muy dudosa'."""
        return nivel(self.confianza)


@dataclass
class PaginaOcr:
    indice: int                                   # base 0
    palabras: list[PalabraOcr] = field(default_factory=list)
    angulo: float = 0.0                           # inclinación medida, en grados (positivo: sube a la derecha)
    giro: int = 0                                 # 0, 90, 180 o 270: lo que se suma a /Rotate
    limpiada: bool = False                        # se limpió el fondo para reconocer
    omitida: bool = False                         # no se reconoció (ver motivo)
    motivo: str = ""
    enderezada: bool = False                      # la imagen del escaneo se cambia por la enderezada
    limpiar_imagen: bool = False                  # la imagen visible también se limpia
    tabla: bytes | None = field(default=None, repr=False)   # tabla de limpieza usada
    ppp: int = 300
    lecturas: int = 0                             # 1 o 2 pasadas

    @property
    def girada_sin_cambiar(self) -> bool:
        """La página tiene inclinación pero no cambia de aspecto: la capa de texto va girada."""
        return bool(self.angulo) and not self.enderezada


@dataclass
class ResultadoOcr:
    paginas: list[PaginaOcr]
    idioma: str
    ppp: int
    segundos: float = 0.0
    origen: str = "ocr"            # 'ocr' (reconocido ahora) | 'capa' (leído de un PDF que ya era buscable)
    total_paginas: int = 0         # páginas del documento

    def reconocidas(self) -> list[PaginaOcr]:
        return [p for p in self.paginas if not p.omitida]

    def todas(self) -> list[tuple[PaginaOcr, PalabraOcr]]:
        return [(p, w) for p in self.paginas if not p.omitida for w in p.palabras]

    def dudosas(self, umbral: float = UMBRAL_SEGURA) -> list[tuple[PaginaOcr, PalabraOcr]]:
        """Palabras con confianza menor que ``umbral`` que la persona no ha revisado."""
        return [(p, w) for p, w in self.todas() if w.confianza < umbral and not w.revisada]

    def promedio(self) -> float:
        ws = [w.confianza for _p, w in self.todas()]
        return sum(ws) / len(ws) if ws else 1.0

    def palabras(self) -> int:
        return sum(1 for _p, w in self.todas() if w.final.strip())

    def correcciones(self) -> int:
        return sum(1 for _p, w in self.todas() if w.corregida is not None and w.corregida != w.texto)

    def reemplazar(self, pagina: PaginaOcr) -> None:
        """Cambia la página con el mismo índice (p. ej. después de volver a leerla)."""
        for k, p in enumerate(self.paginas):
            if p.indice == pagina.indice:
                self.paginas[k] = pagina
                return
        self.paginas.append(pagina)
        self.paginas.sort(key=lambda p: p.indice)


@dataclass
class ResumenOcr:
    paginas: int
    palabras: int
    dudosas: int
    salida: str | None
    resultado: ResultadoOcr
    segundos: float = 0.0


@dataclass
class ArchivoOcr:
    ruta: str
    estado: str                    # 'hecho' | 'ya tenía texto' | 'error'
    paginas: int = 0
    palabras: int = 0
    dudosas: int = 0
    salida: str | None = None
    mensaje: str = ""
    resultado: ResultadoOcr | None = field(default=None, repr=False)


def nivel(confianza: float) -> str:
    if confianza >= UMBRAL_SEGURA:
        return "segura"
    return "dudosa" if confianza >= UMBRAL_MUY_DUDOSA else "muy dudosa"


# --------------------------------------------------------------------------
# Diccionario sacado del modelo de idioma (sin dependencias ni internet)
# --------------------------------------------------------------------------

_DAWG = 19          # lstm-word-dawg
_UNICHARSET = 21    # lstm-unicharset


def _componentes(path: str | Path) -> dict[int, bytes]:
    """Componentes de un .traineddata: int32 n y n desplazamientos int64 (−1 = ausente)."""
    data = Path(path).read_bytes()
    n = struct.unpack_from("<i", data, 0)[0]
    if not 0 < n < 100:
        raise ValueError("no es un modelo de Tesseract")
    offs = struct.unpack_from(f"<{n}q", data, 4)
    out = {}
    for i, o in enumerate(offs):
        if o < 0:
            continue
        fin = min([x for x in offs if x > o] + [len(data)])
        out[i] = data[o:fin]
    return out


def _unicharset(blob: bytes) -> list[str]:
    lines = blob.decode("utf-8", errors="replace").split("\n")
    n = int(lines[0].strip())
    chars = []
    for ln in lines[1:n + 1]:
        tok = ln.split(" ")[0]
        chars.append(" " if tok == "NULL" else tok)
    return chars


def _palabras_dawg(blob: bytes, chars: list[str]) -> set[str]:
    """Recorre el DAWG desde el nodo 0 por las aristas que no van hacia atrás."""
    magic, usize = struct.unpack_from("<hi", blob, 0)
    if magic != 42:
        raise ValueError("DAWG desconocido")
    nedges = struct.unpack_from("<i", blob, 6)[0]
    edges = struct.unpack_from(f"<{nedges}Q", blob, 10)
    b = math.ceil(math.log2(usize + 1))
    mask = (1 << b) - 1
    out: set[str] = set()
    pila = [(0, "")]
    vistos = 0
    while pila:
        nodo, prefijo = pila.pop()
        i = nodo
        while i < nedges:
            e = edges[i]
            banderas = (e >> b) & 7
            if not banderas & 2:                       # 2 = hacia atrás
                letra = e & mask
                w = prefijo + (chars[letra] if letra < len(chars) else "")
                if banderas & 4:                       # 4 = fin de palabra
                    out.add(w)
                sig = e >> (b + 3)
                if sig:
                    pila.append((sig, w))
            vistos += 1
            if banderas & 1 or vistos > 50_000_000:    # 1 = última arista del nodo
                break
            i += 1
    return out


def _palabras_modelo(path: Path) -> set[str]:
    try:
        comp = _componentes(path)
        if _DAWG not in comp or _UNICHARSET not in comp:
            return set()
        return _palabras_dawg(comp[_DAWG], _unicharset(comp[_UNICHARSET]))
    except (OSError, ValueError, struct.error, IndexError, UnicodeDecodeError):
        return set()


_LOCK_DIC = threading.Lock()


@functools.lru_cache(maxsize=8)
def _diccionario(idiomas: str, carpeta: str | None) -> frozenset[str]:
    if not carpeta:
        return frozenset()
    palabras: set[str] = set()
    for lang in dict.fromkeys(idiomas.split("+")):
        palabras |= _palabras_modelo(Path(carpeta) / f"{lang}.traineddata")
    return frozenset(w.lower() for w in palabras if w.strip())


def diccionario(idiomas: str = "spa+eng") -> frozenset[str]:
    """Palabras (en minúsculas) de los modelos de idioma, p. ej. 179 000 del español.  Queda en caché."""
    with _LOCK_DIC:
        return _diccionario(idiomas, core.tessdata_dir())


diccionario.cache_info = _diccionario.cache_info      # type: ignore[attr-defined]
diccionario.cache_clear = _diccionario.cache_clear    # type: ignore[attr-defined]


def precargar(idiomas: str = "spa+eng") -> None:
    """Carga el diccionario en segundo plano (la primera vez tarda unas décimas de segundo)."""
    threading.Thread(target=diccionario, args=(idiomas,), daemon=True, name="diccionario-ocr").start()


# --- diccionario personal ---------------------------------------------------

_personal_cache: dict[str, tuple[float, frozenset[str]]] = {}


def _ruta_personal() -> Path:
    if ruta_personal is not None:
        return Path(ruta_personal)
    widgets = sys.modules.get(f"{__package__}.widgets")         # con la aplicación abierta, sus ajustes
    ajustes = getattr(getattr(widgets, "ctx", None), "settings", None)
    if ajustes is None:
        from .settings import Settings
        ajustes = Settings()
    return Path(ajustes.path).parent / NOMBRE_PERSONAL


def diccionario_personal() -> frozenset[str]:
    """Palabras que la persona agregó con «Agregar a mi diccionario» (una por renglón)."""
    ruta = _ruta_personal()
    try:
        mtime = ruta.stat().st_mtime
    except OSError:
        return frozenset()
    clave = str(ruta)
    if clave in _personal_cache and _personal_cache[clave][0] == mtime:
        return _personal_cache[clave][1]
    try:
        texto = ruta.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return frozenset()
    palabras = frozenset(p for p in (_normal(ln) for ln in texto.splitlines()) if p)
    _personal_cache[clave] = (mtime, palabras)
    return palabras


def agregar_al_diccionario(palabra: str) -> bool:
    """Agrega una palabra al diccionario personal.  Devuelve False si ya estaba o no es una palabra."""
    p = _normal(palabra)
    if not p or p in diccionario_personal():
        return False
    ruta = _ruta_personal()
    try:
        actual = ruta.read_text(encoding="utf-8-sig") if ruta.exists() else ""
    except (OSError, UnicodeDecodeError):
        actual = ""
    lineas = [ln for ln in actual.splitlines() if ln.strip()] + [p]
    try:
        with core._output(ruta) as tmp:
            Path(tmp).write_text("\n".join(lineas) + "\n", encoding="utf-8")
    except OSError as exc:
        raise UserError(f"No se pudo guardar tu diccionario: {exc}") from exc
    _personal_cache.pop(str(ruta), None)
    return True


# --------------------------------------------------------------------------
# Confianza estimada
# --------------------------------------------------------------------------

_PUNTA = "¿?¡!.,;:\"'()[]{}«»“”‘’…–—-*•·"
_RAROS = set("|~^¬¦\\¤§¨`")
_PUNTUACION_SOLA = set(_PUNTA) | set("$%&/+=#@°ºª<>_")
_LETRAS_VALIDAS_SUELTAS = set("aeouyAEOUYáéóú")
_NUMERICO = re.compile(r"^[($¢€+\-]*\d[\d.,:/%°ºª$€\-–]*\)?$")
_MEZCLA = re.compile(r"[^\W\d_]\d+[^\W\d_]|\d[^\W\d_]+\d")
_MAYUSCULA_MEDIO = re.compile(r"[a-záéíóúñü][A-ZÁÉÍÓÚÑÜ]")
_REPETIDAS = re.compile(r"([^\W\d_])\1\1", re.IGNORECASE)
_CONFUSIONES = str.maketrans({"0": "o", "1": "l", "5": "s", "8": "b", "3": "e", "4": "a", "7": "t", "6": "b"})


def _normal(texto: str) -> str:
    """La palabra sin signos alrededor y en minúsculas."""
    return texto.strip().strip(_PUNTA).lower()


def _exenta(texto: str) -> bool:
    """Números, fechas, valores en pesos, correos y documentos no se castigan por el diccionario."""
    t = texto.strip().strip(_PUNTA.replace("$", "").replace("(", "").replace(")", ""))
    if not t:
        return False
    if _NUMERICO.match(t):
        return True
    from . import censura
    norm = censura.normalizar(t)
    for pat in censura.PATRONES.values():
        m = pat.regex.search(norm)
        if m and m.end() - m.start() >= max(3, len(norm) - 2):
            return True
    return False


def _en_diccionario(palabra: str, dic: frozenset[str], personal: frozenset[str]) -> bool:
    low = palabra.lower()
    return low in dic or low in personal


def _caso_normal(palabra: str) -> bool:
    """minúsculas, Primera mayúscula o TODO EN MAYÚSCULAS (no «niÑez»)."""
    return palabra.islower() or palabra.isupper() or (palabra[:1].isupper() and palabra[1:].islower())


def confianza(texto: str, otra_lectura: str | None = None, falta_en_otra: bool = False,
              forma_rara: bool = False, dic: frozenset[str] | None = None,
              personal: frozenset[str] | None = None, idioma: str = "spa+eng") -> tuple[float, list[str]]:
    """Confianza estimada (0 a 1) de una palabra leída y sus motivos en palabras claras.

    Parte de 1,0 y resta: lecturas distintas −0,45; falta en la otra −0,25; fuera del diccionario −0,30;
    caracteres raros −0,30; forma −0,15.  Lo que está en el diccionario personal no se marca.
    """
    if dic is None:
        dic = diccionario(idioma)
    if personal is None:
        personal = diccionario_personal()
    t = texto.strip()
    if not t:
        return 1.0, []
    if _normal(t) and _normal(t) in personal:
        return 1.0, []
    motivos: list[str] = []
    resta = 0.0
    nucleo = t.strip(_PUNTA)
    exenta = _exenta(t)

    # 1) dos lecturas
    if otra_lectura is not None and otra_lectura.strip(_PUNTA) != nucleo:
        resta += 0.45
        motivos.append(M_DIFIEREN)
    elif falta_en_otra:
        resta += 0.25
        motivos.append(M_FALTA)

    # 2) diccionario
    if not exenta and dic:
        partes = [p for p in re.split(r"[-/]", nucleo) if p]
        fuera = False
        for p in partes:
            if p.isalpha() and len(p) >= 4:
                acronimo = p.isupper() and len(p) <= 5
                if not acronimo and (not _caso_normal(p) or not _en_diccionario(p, dic, personal)):
                    fuera = True
            elif sum(c.isalpha() for c in p) >= 2 and (_MEZCLA.search(p) or any(c in _RAROS or c == "\ufffd"
                                                                                for c in p)):
                fuera = True                           # «c0mo», «gesti¬n»: así escrita no es una palabra
        if fuera:
            resta += 0.30
            motivos.append(M_DICCIONARIO)
    if not any(c.isalnum() for c in t) and not all(c in _PUNTUACION_SOLA for c in t):
        resta += 0.30
        motivos.append(M_NO_PALABRA)

    # 3) caracteres raros
    raro = None
    if "\ufffd" in t:
        raro = M_ILEGIBLE
    elif any(c in _RAROS for c in t):
        raro = M_SIMBOLOS
    elif not exenta and _MEZCLA.search(nucleo):
        raro = M_MEZCLA
    elif _MAYUSCULA_MEDIO.search(nucleo):
        raro = M_MAYUSCULA
    elif not exenta and _REPETIDAS.search(nucleo) and "www" not in nucleo.lower():
        raro = M_REPETIDAS
    if raro:
        resta += 0.30
        motivos.append(raro)

    # 4) forma
    if len(nucleo) == 1 and nucleo.isalpha() and nucleo not in _LETRAS_VALIDAS_SUELTAS:
        resta += 0.15
        motivos.append(M_SUELTA)
    elif forma_rara:
        resta += 0.15
        motivos.append(M_TAMANO)
    return max(0.0, round(1.0 - resta, 2)), motivos


def sugerencias(palabra: str, idioma: str = "spa+eng", n: int = 3) -> list[str]:
    """Hasta ``n`` palabras parecidas del diccionario (misma primera letra, largo ±1)."""
    t = palabra.strip()
    nucleo = t.strip(_PUNTA)
    if not nucleo or _exenta(t):
        return []
    base = nucleo.translate(_CONFUSIONES) if _MEZCLA.search(nucleo) else nucleo
    base = "".join(c for c in base if c.isalpha()).lower()
    if len(base) < 2:
        return []
    indice = _indice(idioma)
    primera = _sin_tilde(base[0])
    candidatas: list[str] = []
    for largo in (len(base) - 1, len(base), len(base) + 1):
        candidatas.extend(indice.get((primera, largo), ()))
    candidatas.extend(p for p in diccionario_personal() if p[:1] and _sin_tilde(p[0]) == primera)
    encontradas = difflib.get_close_matches(base, candidatas, n=n + 1, cutoff=0.65)
    out = []
    for c in encontradas:
        c = _mismo_caso(nucleo, c)
        if c != nucleo and c not in out:
            out.append(c)
    return out[:n]


def _sin_tilde(c: str) -> str:
    d = unicodedata.normalize("NFD", c)
    return d[0] if d else c


def _mismo_caso(modelo: str, palabra: str) -> str:
    letras = [c for c in modelo if c.isalpha()]
    if len(letras) > 1 and all(c.isupper() for c in letras):
        return palabra.upper()
    if letras and letras[0].isupper():
        return palabra[:1].upper() + palabra[1:]
    return palabra


@functools.lru_cache(maxsize=4)
def _indice_cache(idiomas: str, carpeta: str | None) -> dict[tuple[str, int], tuple[str, ...]]:
    grupos: dict[tuple[str, int], list[str]] = collections.defaultdict(list)
    for w in _diccionario(idiomas, carpeta):
        if w.isalpha():
            grupos[(_sin_tilde(w[0]), len(w))].append(w)
    return {k: tuple(v) for k, v in grupos.items()}


def _indice(idiomas: str) -> dict[tuple[str, int], tuple[str, ...]]:
    with _LOCK_DIC:
        return _indice_cache(idiomas, core.tessdata_dir())


# --------------------------------------------------------------------------
# Imágenes: tinta, limpieza e inclinación
# --------------------------------------------------------------------------

_BINARIA = bytes(1 if v < 140 else 0 for v in range(256))


def _gris(page: pymupdf.Page, ppp: float, matriz: pymupdf.Matrix | None = None) -> pymupdf.Pixmap:
    m = matriz if matriz is not None else pymupdf.Matrix(ppp / 72, ppp / 72)
    return page.get_pixmap(matrix=m, colorspace=pymupdf.csGRAY, alpha=False)


def _tinta(gris: pymupdf.Pixmap) -> float:
    """Fracción de píxeles oscuros (para no reconocer páginas en blanco)."""
    muestra = gris.samples[::5]
    return muestra.translate(_BINARIA).count(1) / max(len(muestra), 1)


def _histograma(gris: pymupdf.Pixmap) -> collections.Counter:
    return collections.Counter(gris.samples[::7])


def tabla_limpieza(hist: collections.Counter, modo: str = "auto") -> bytes | None:
    """Tabla de 256 valores que lleva el tono del papel a blanco y oscurece los trazos, o None si no hace falta.

    'auto' limpia solo si el fondo es gris (pico claro por debajo de ~235) o si hay poco contraste."""
    if modo == "no" or not hist:
        return None
    total = sum(hist.values())
    papel = max(range(90, 256), key=lambda v: hist.get(v, 0))
    acum, tinta = 0, 0
    for v in range(256):
        acum += hist.get(v, 0)
        if acum >= total * 0.005:
            tinta = v
            break
    contraste = papel - tinta
    if modo == "auto" and papel >= 235 and contraste >= 140:
        return None
    if contraste < 25:                                  # página lisa: no hay nada que realzar
        return None
    blanco = papel - (0.25 if modo == "fuerte" else 0.12) * contraste
    negro = tinta + 0.10 * contraste
    gamma = 2.0 if modo == "fuerte" else 1.5
    tabla = bytearray(256)
    for v in range(256):
        if v <= negro:
            tabla[v] = 0
        elif v >= blanco:
            tabla[v] = 255
        else:
            tabla[v] = round(255 * ((v - negro) / (blanco - negro)) ** gamma)
    return bytes(tabla)


def limpiar_pixmap(pix: pymupdf.Pixmap, tabla: bytes) -> pymupdf.Pixmap:
    """Aplica la tabla a cada canal (a velocidad de C) y rearma el pixmap."""
    if pix.alpha:
        pix = pymupdf.Pixmap(pix, 0)
    datos = pix.samples.translate(tabla)
    nuevo = pymupdf.Pixmap(pix.colorspace, pix.width, pix.height, datos, 0)
    nuevo.set_origin(pix.x, pix.y)
    nuevo.set_dpi(pix.xres, pix.yres)
    return nuevo


def _franjas(gris: pymupdf.Pixmap, ancho: int) -> tuple[list[list[int]], list[float], int]:
    """Píxeles oscuros por renglón en franjas verticales de ``ancho`` píxeles."""
    w, h, stride = gris.width, gris.height, gris.stride
    b = gris.samples.translate(_BINARIA)
    n = max(6, w // ancho)
    paso = w / n
    cols, xs = [], []
    for s in range(n):
        x0, x1 = int(s * paso), int((s + 1) * paso)
        xs.append((x0 + x1) / 2 - w / 2)
        cols.append([b.count(1, r * stride + x0, r * stride + x1) for r in range(h)])
    return cols, xs, h


def _puntaje(cols: list[list[int]], xs: list[float], h: int, ang: float) -> int:
    t = math.tan(math.radians(ang))
    margen = int(abs(t) * max(abs(x) for x in xs)) + 2
    tot = [0] * (h + 2 * margen)
    for col, x in zip(cols, xs):
        d = margen + int(round(x * t))
        tot[d:d + h] = [a + c for a, c in zip(tot[d:d + h], col)]
    return sum(v * v for v in tot)


def detectar_inclinacion(fuente: pymupdf.Page | pymupdf.Pixmap, max_ang: float = INCLINACION_MAX) -> float:
    """Inclinación del texto en grados: positiva si los renglones suben hacia la derecha.

    Perfiles de proyección: se busca el ángulo en que los renglones dejan más «picos» al sumar los píxeles
    oscuros por fila (grueso a 60 ppp, fino a 150 ppp).  Devuelve 0 si la página no tiene renglones claros.
    (No usa ``fz_detect_skew`` de MuPDF: en las pruebas erraba hasta 5° y se caía con imágenes pequeñas.)"""
    if isinstance(fuente, pymupdf.Page):
        bajo, alto = _gris(fuente, 60), _gris(fuente, 150)
    else:
        g = fuente if fuente.n == 1 and not fuente.alpha else pymupdf.Pixmap(pymupdf.csGRAY, fuente)
        if g.alpha:
            g = pymupdf.Pixmap(g, 0)
        alto = g
        bajo = pymupdf.Pixmap(g, 0)                    # copia sin alfa (Pixmap(g) le agregaría alfa)
        f = max(0, int(math.log2(max(1, g.width / 520))))
        if f:
            bajo.shrink(f)
    if bajo.width < 60 or bajo.height < 60 or _tinta(bajo) < 0.002:
        return 0.0
    cols, xs, h = _franjas(bajo, 14)
    pasos = [k / 2 for k in range(int(-max_ang * 2), int(max_ang * 2) + 1)]
    puntajes = {a: _puntaje(cols, xs, h, a) for a in pasos}
    mejor = max(puntajes, key=puntajes.get)
    ordenados = sorted(puntajes.values())
    mediana = ordenados[len(ordenados) // 2] or 1
    if puntajes[mejor] < 1.08 * mediana or abs(mejor) >= max_ang:
        return 0.0                                     # sin renglones claros (una foto, una página casi vacía)
    cols, xs, h = _franjas(alto, 40)
    centro = mejor
    for paso, n in ((0.05, 12), (0.01, 6)):
        centro = max((centro + k * paso for k in range(-n, n + 1)), key=lambda a: _puntaje(cols, xs, h, a))
    return round(centro, 2)


# --------------------------------------------------------------------------
# Reconocimiento de un pixmap
# --------------------------------------------------------------------------

def _ocr_pixmap(pix: pymupdf.Pixmap, idioma: str, tessdata: str) -> list[tuple]:
    """Palabras (x0, y0, x1, y1, texto, bloque, renglón, n) en píxeles del pixmap."""
    if pix.alpha:
        pix = pymupdf.Pixmap(pix, 0)
    if pix.n != 3:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)       # el OCR de MuPDF no lee pixmaps en gris
    try:
        datos = pix.pdfocr_tobytes(language=idioma, tessdata=tessdata)
    except Exception as exc:  # noqa: BLE001
        raise UserError(f"El reconocimiento de texto falló: {exc}") from exc
    with pymupdf.open("pdf", datos) as d:
        pg = d[0]
        sx = pix.width / max(pg.rect.width, 1e-6)
        sy = pix.height / max(pg.rect.height, 1e-6)
        return [(w[0] * sx, w[1] * sy, w[2] * sx, w[3] * sy, w[4], w[5], w[6], w[7])
                for w in pg.get_text("words") if w[4].strip()]


def _matriz(rect: pymupdf.Rect, zoom: float, angulo: float) -> pymupdf.Matrix:
    """Puntos visibles → píxeles de la copia enderezada (giro de −ángulo alrededor del centro)."""
    m = pymupdf.Matrix(zoom, zoom)
    if not angulo:
        return m
    c = (rect.tl + rect.br) / 2
    return pymupdf.Matrix(1, 0, 0, 1, -c.x, -c.y) * pymupdf.Matrix(angulo) * pymupdf.Matrix(1, 0, 0, 1, c.x, c.y) * m


def _girar_rect(r: Rect4, rect: pymupdf.Rect, angulo: float) -> Rect4:
    """Caja sin inclinación → caja (que la contiene) en la página tal como se ve, torcida ``angulo``."""
    if not angulo:
        return tuple(r)
    c = (rect.tl + rect.br) / 2
    inv = pymupdf.Matrix(1, 0, 0, 1, -c.x, -c.y) * pymupdf.Matrix(-angulo) * pymupdf.Matrix(1, 0, 0, 1, c.x, c.y)
    q = pymupdf.Rect(r).quad * inv
    return tuple(q.rect)


def esquinas(palabra: PalabraOcr, pagina: PaginaOcr, rect: pymupdf.Rect) -> list[tuple[float, float]]:
    """Las cuatro esquinas de la palabra en la página tal como se ve (giradas si la página quedó torcida)."""
    if palabra.marco is None or not pagina.girada_sin_cambiar:
        x0, y0, x1, y1 = palabra.rect
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    c = (rect.tl + rect.br) / 2
    inv = pymupdf.Matrix(1, 0, 0, 1, -c.x, -c.y) * pymupdf.Matrix(-pagina.angulo) * pymupdf.Matrix(1, 0, 0, 1, c.x, c.y)
    q = pymupdf.Rect(palabra.marco).quad * inv
    return [(p.x, p.y) for p in (q.ul, q.ur, q.lr, q.ll)]


def _leer(page: pymupdf.Page, ppp: int, angulo: float, tabla: bytes | None, idioma: str,
          tessdata: str, gris: bool = True) -> list[tuple]:
    """Reconoce la página (enderezada y limpia si corresponde).  Palabras en coordenadas sin inclinación.
    ``gris``: con limpieza, dibuja la página en gris (si no, en color y la tabla se aplica a cada canal)."""
    z = ppp / 72
    mat = _matriz(page.rect, z, angulo)
    if tabla is not None:
        cs = pymupdf.csGRAY if gris else pymupdf.csRGB
        pix = limpiar_pixmap(page.get_pixmap(matrix=mat, colorspace=cs, alpha=False), tabla)
    else:
        pix = page.get_pixmap(matrix=mat, alpha=False)
    ox, oy = pix.x, pix.y
    return [((x0 + ox) / z, (y0 + oy) / z, (x1 + ox) / z, (y1 + oy) / z, t, b, ln, n)
            for x0, y0, x1, y1, t, b, ln, n in _ocr_pixmap(pix, idioma, tessdata)]


def calidad_lectura(palabras: Sequence, dic: frozenset[str]) -> tuple[float, int]:
    """(fracción de letras que forman palabras del diccionario o números con formato, letras contadas)."""
    validas = total = 0
    for w in palabras:
        t = (w[4] if isinstance(w, tuple) else w.texto).strip().strip(_PUNTA)
        if not t or t.isdigit():
            continue
        total += len(t)
        # Solo cuentan las palabras de 3 letras o más: una lectura de lado está llena de letras sueltas y
        # sílabas que, por casualidad, también están en el diccionario.
        if len(t) >= 3 and ((t.isalpha() and t.lower() in dic) or "@" in t
                            or (_NUMERICO.match(t) and any(c in t for c in ".,/$"))):
            validas += len(t)
    return (validas / total if total else 0.0), total


# --------------------------------------------------------------------------
# Página a página
# --------------------------------------------------------------------------

def _escaneo_puro(page: pymupdf.Page, compartidas: set[int]) -> int | None:
    """xref de la imagen si la página es solo un escaneo (una imagen que cubre al menos el 90 % y nada más)."""
    try:
        imgs = page.get_images(full=True)
        if len(imgs) != 1:
            return None
        xref, smask = imgs[0][0], imgs[0][1]
        if smask or xref in compartidas or page.parent.xref_get_key(xref, "ImageMask")[1] == "true":
            return None
        if core.image_coverage(page) < 0.9:
            return None
        vis, inv, _area = core.text_layer(page)
        if vis or inv or page.get_drawings():
            return None
        return xref
    except Exception:  # noqa: BLE001
        return None


def _imagenes_compartidas(doc: pymupdf.Document, indices: Iterable[int]) -> set[int]:
    vistos: collections.Counter = collections.Counter()
    for i in range(doc.page_count):
        try:
            for x in {im[0] for im in doc[i].get_images()}:
                vistos[x] += 1
        except Exception:  # noqa: BLE001
            continue
    return {x for x, n in vistos.items() if n > 1}


def _forma_rara(ws: list[tuple]) -> list[bool]:
    """Cajas muy altas o muy bajas, o muy anchas o angostas por letra, para su renglón."""
    por_renglon: dict[tuple, list[int]] = collections.defaultdict(list)
    for k, w in enumerate(ws):
        por_renglon[(w[5], w[6])].append(k)
    out = [False] * len(ws)
    for ks in por_renglon.values():
        if len(ks) < 3:
            continue
        altos = sorted(ws[k][3] - ws[k][1] for k in ks)
        anchos = sorted((ws[k][2] - ws[k][0]) / len(ws[k][4]) for k in ks if len(ws[k][4]) >= 2)
        h_med = altos[len(altos) // 2] or 1
        a_med = anchos[len(anchos) // 2] if anchos else 0
        for k in ks:
            x0, y0, x1, y1, t = ws[k][:5]
            alto = y1 - y0
            if alto > 1.6 * h_med or alto < 0.6 * h_med:
                out[k] = True
            elif a_med and len(t) >= 2 and not (0.4 * a_med <= (x1 - x0) / len(t) <= 2.4 * a_med):
                out[k] = True
    return out


def _emparejar(a: list[tuple], b: list[tuple]) -> list[tuple[str | None, bool, bool]]:
    """Para cada palabra de la lectura A: (texto de la lectura B en esa caja, ¿falta en B?, ¿mismas letras con
    otra separación?).  «niñez y» contra «niñezy» no es una lectura distinta: solo cambia el espacio."""
    import bisect
    orden = sorted(range(len(b)), key=lambda k: (b[k][1] + b[k][3]) / 2)
    ys = [(b[k][1] + b[k][3]) / 2 for k in orden]
    grupos: list[list[int]] = []
    for x0, y0, x1, y1, *_ in a:
        lo, hi = bisect.bisect_left(ys, y0), bisect.bisect_right(ys, y1)
        area = max((x1 - x0) * (y1 - y0), 1e-6)
        js = []
        for j in range(lo, hi):
            w = b[orden[j]]
            ix = min(w[2], x1) - max(w[0], x0)
            iy = min(w[3], y1) - max(w[1], y0)
            if ix <= 0 or iy <= 0:
                continue
            area_b = max((w[2] - w[0]) * (w[3] - w[1]), 1e-6)
            if ix * iy >= 0.4 * min(area, area_b):
                js.append(orden[j])
        grupos.append(js)
    a_de_b: dict[int, list[int]] = collections.defaultdict(list)
    for i, js in enumerate(grupos):
        for j in js:
            a_de_b[j].append(i)
    out = []
    for i, js in enumerate(grupos):
        if not js:
            out.append((None, True, False))
            continue
        js = sorted(js, key=lambda j: b[j][0])
        texto_b = " ".join(b[j][4] for j in js)
        ais = sorted({k for j in js for k in a_de_b[j]}, key=lambda k: a[k][0])
        mismo = "".join(a[k][4] for k in ais) == "".join(b[j][4] for j in js)
        out.append((texto_b, False, mismo))
    return out


def _puntuar(a: list[tuple], b: list[tuple] | None, dic, personal) -> list[tuple[float, list[str], str | None]]:
    formas = _forma_rara(a)
    pares = _emparejar(a, b) if b is not None else [(None, False, False)] * len(a)
    if b is not None and len(a) >= 10 and sum(1 for _o, falta, _m in pares if not falta) < 0.6 * len(a):
        # La otra lectura falló casi por completo (p. ej. una página muy borrosa a 200 ppp): no dice nada de
        # cada palabra, así que se puntúa como con una sola lectura.
        b, pares = None, [(None, False, False)] * len(a)
    out = []
    for w, forma, (otra, falta, mismo) in zip(a, formas, pares):
        comparar = None
        if b is not None and not falta:
            comparar = w[4] if mismo else otra
        conf, motivos = confianza(w[4], otra_lectura=comparar, falta_en_otra=falta, forma_rara=forma,
                                  dic=dic, personal=personal)
        out.append((conf, motivos, otra if otra is not None and otra.strip(_PUNTA) != w[4].strip(_PUNTA) else None))
    return out


def _probar_giros(page: pymupdf.Page, base: int, idioma: str, tessdata: str, dic) -> tuple[int, float]:
    """Prueba 180, 90 y 270° a baja resolución.  Devuelve (mejor giro, su calidad).  (No hay modelo «osd».)"""
    mejor, mejor_q = 0, -1.0
    for g in (180, 90, 270):
        page.set_rotation((base + g) % 360)
        try:
            q, n = calidad_lectura(_leer(page, 150, 0.0, None, idioma, tessdata), dic)
        finally:
            page.set_rotation(base)
        q *= min(1.0, n / 30)                          # unas pocas letras sueltas no prueban nada
        if q > mejor_q:
            mejor, mejor_q = g, q
        if q >= 0.75:                                  # claramente al derecho: no hace falta probar más
            break
    return mejor, mejor_q


def _analizar_pagina(doc: pymupdf.Document, i: int, op: OpcionesOcr, tessdata: str, dic, personal,
                     compartidas: set[int]) -> PaginaOcr:
    page = doc[i]
    pag = PaginaOcr(i, ppp=op.ppp)
    if op.solo_faltantes and not core._needs_ocr_pass(page):
        pag.omitida, pag.motivo = True, "ya tiene texto"
        return pag
    gris = _gris(page, 100)
    if _tinta(gris) < 0.0005:
        pag.motivo = "página en blanco"
        return pag
    tabla = tabla_limpieza(_histograma(gris), op.limpiar)
    pag.tabla, pag.limpiada = tabla, tabla is not None
    angulo = _medir(page, tabla) if op.enderezar else 0.0
    a = _leer(page, op.ppp, angulo, tabla, op.idioma, tessdata)
    if op.girar:
        q, n = calidad_lectura(a, dic)
        if (n >= 15 and q < 0.4) or (n < 15 and _tinta(gris) > 0.01):
            base = page.rotation
            g, qg = _probar_giros(page, base, op.idioma, tessdata, dic)
            if qg >= 0.5 and qg >= q + 0.25:
                pag.giro = g
                page.set_rotation((base + g) % 360)          # solo en memoria; escribir() lo repite
                angulo = _medir(page, tabla) if op.enderezar else 0.0
                a = _leer(page, op.ppp, angulo, tabla, op.idioma, tessdata)
    pag.angulo = angulo
    if angulo and op.enderezar_pagina and _escaneo_puro(page, compartidas) is not None:
        pag.enderezada = True
    if op.limpiar_pagina and tabla is not None and _escaneo_puro(page, compartidas) is not None:
        pag.limpiar_imagen = True
    b = None
    if op.dos_pasadas:
        # Segunda lectura con otra configuración: otra resolución y en color.  Sin limpiar, salvo que la
        # página lo necesite (con el fondo gris la segunda lectura casi no leería nada y no serviría de nada).
        ppp_b = 200 if op.ppp >= 300 else 300
        b = _leer(page, ppp_b, angulo, tabla, op.idioma, tessdata, gris=False)
    pag.lecturas = 2 if b is not None else 1
    puntos = _puntuar(a, b, dic, personal)
    existentes = core._text_boxes(page)
    palabras = []
    for w, (conf, motivos, otra) in zip(a, puntos):
        marco = tuple(round(v, 2) for v in w[:4])
        rect = marco if not angulo or pag.enderezada else tuple(round(v, 2) for v in _girar_rect(marco, page.rect, angulo))
        palabras.append(PalabraOcr(w[4], rect, conf, motivos, renglon=w[5] * 1000 + w[6], otra=otra,
                                   marco=marco if pag.girada_sin_cambiar else None))
    pag.palabras = core._drop_covered(palabras, existentes, rect_of=lambda p: p.rect)
    return pag


def _medir(page: pymupdf.Page, tabla: bytes | None = None) -> float:
    """Inclinación que vale la pena corregir (0,3–15°), medida sobre la página limpia si hace falta."""
    ang = detectar_inclinacion(page if tabla is None else limpiar_pixmap(_gris(page, 150), tabla))
    return ang if INCLINACION_MIN <= abs(ang) <= INCLINACION_MAX else 0.0


def analizar(path: str | Path, opciones: OpcionesOcr | None = None, password: str | None = None,
             progress: Progress = None) -> ResultadoOcr:
    """Reconoce el texto de las páginas que lo necesitan y estima la confianza de cada palabra.  No escribe nada."""
    op = opciones or OpcionesOcr()
    tessdata = core._check_ocr(op.idioma)
    t0 = time.time()
    dic = diccionario(op.idioma)
    personal = diccionario_personal()
    paginas: list[PaginaOcr] = []
    with core.open_pdf(path, password) as doc:
        idx = core.parse_pages(op.paginas, doc.page_count)
        compartidas = _imagenes_compartidas(doc, idx) if op.enderezar_pagina or op.limpiar_pagina else set()
        for k, i in enumerate(idx):
            _report(progress, k, len(idx), f"Reconociendo texto: página {i + 1} de {doc.page_count}")
            paginas.append(_analizar_pagina(doc, i, op, tessdata, dic, personal, compartidas))
        total = doc.page_count
    _report(progress, len(idx), len(idx), "Listo")
    return ResultadoOcr(paginas, op.idioma, op.ppp, round(time.time() - t0, 2), "ocr", total)


def releer_pagina(path: str | Path, indice: int, opciones: OpcionesOcr | None = None, ppp: int = 400,
                  password: str | None = None, progress: Progress = None) -> PaginaOcr:
    """Vuelve a leer una página con más calidad.  Devuelve la página nueva (``ResultadoOcr.reemplazar``)."""
    op = replace(opciones or OpcionesOcr(), ppp=ppp, paginas=str(indice + 1), solo_faltantes=False)
    return analizar(path, op, password, progress).paginas[0]


# --------------------------------------------------------------------------
# Cambios en la página: girar, enderezar y limpiar la imagen
# --------------------------------------------------------------------------

def _imagen_enderezada(page: pymupdf.Page, xref: int, angulo: float, tabla: bytes | None) -> pymupdf.Pixmap:
    """La imagen del escaneo vuelta a dibujar derecha (y limpia), a su resolución original.

    Se dibuja la página girada −ángulo alrededor de su centro, directamente en el espacio de píxeles de la
    imagen, así sirve aunque la imagen esté puesta girada o la página tenga /Rotate."""
    doc = page.parent
    info = next(d for d in page.get_image_info(xrefs=True) if d["xref"] == xref)
    iw, ih = info["width"], info["height"]
    t_vis = pymupdf.Matrix(info["transform"]) * page.rotation_matrix        # cuadro unidad → página visible
    a_pix = ~t_vis * pymupdf.Matrix(iw, ih)                                  # página visible → píxeles
    mat = _matriz(page.rect, 1.0, angulo) * a_pix if angulo else a_pix
    gris = pymupdf.Pixmap(doc, xref).n == 1
    cs = pymupdf.csGRAY if gris else pymupdf.csRGB
    pix = page.get_pixmap(matrix=mat, colorspace=cs, alpha=False)
    destino = pymupdf.Pixmap(cs, pymupdf.IRect(0, 0, iw, ih), False)
    destino.clear_with(255)
    destino.copy(pix, destino.irect)
    destino.set_origin(0, 0)
    if tabla is not None:
        destino = limpiar_pixmap(destino, tabla)
    return destino


def _reemplazar_imagen(doc: pymupdf.Document, xref: int, pix: pymupdf.Pixmap) -> None:
    """Cambia los píxeles de la imagen ``xref`` en su sitio (sin duplicarla en el archivo): JPEG calidad 85 si
    el original era JPEG; si no (p. ej. de 1 bit), sin pérdida (Flate)."""
    filtro = doc.xref_get_key(xref, "Filter")[1]
    if "DCTDecode" in filtro or "JPXDecode" in filtro:
        doc.update_stream(xref, pix.tobytes("jpg", jpg_quality=85), compress=False)
        doc.xref_set_key(xref, "Filter", "/DCTDecode")
    else:
        doc.update_stream(xref, pix.samples, compress=True)
    doc.xref_set_key(xref, "Width", str(pix.width))
    doc.xref_set_key(xref, "Height", str(pix.height))
    doc.xref_set_key(xref, "ColorSpace", "/DeviceGray" if pix.n == 1 else "/DeviceRGB")
    doc.xref_set_key(xref, "BitsPerComponent", "8")
    for clave in ("DecodeParms", "Decode", "ImageMask", "Mask"):
        if doc.xref_get_key(xref, clave)[0] != "null":
            doc.xref_set_key(xref, clave, "null")


def preparar_pagina(page: pymupdf.Page, pag: PaginaOcr) -> None:
    """Aplica a la página (en memoria) el giro y el enderezado o la limpieza que decidió ``analizar``."""
    if pag.giro:
        page.set_rotation((page.rotation + pag.giro) % 360)
    if pag.enderezada or pag.limpiar_imagen:
        imgs = page.get_images(full=True)
        if len(imgs) == 1:
            xref = imgs[0][0]
            pix = _imagen_enderezada(page, xref, pag.angulo if pag.enderezada else 0.0,
                                     pag.tabla if pag.limpiar_imagen else None)
            _reemplazar_imagen(page.parent, xref, pix)


def renderizar(page: pymupdf.Page, pag: PaginaOcr | None, zoom: float, clip: pymupdf.Rect | None = None) -> pymupdf.Pixmap:
    """La página tal como quedará (girada y enderezada), sin modificar el documento."""
    if pag is None or (not pag.giro and not pag.enderezada and not pag.limpiar_imagen):
        return page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, alpha=False)
    base = page.rotation
    try:
        if pag.giro:
            page.set_rotation((base + pag.giro) % 360)
        rect = page.rect
        mat = _matriz(rect, zoom, pag.angulo if pag.enderezada else 0.0)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        zona = pymupdf.IRect((clip or rect) * pymupdf.Matrix(zoom, zoom))
        destino = pymupdf.Pixmap(pymupdf.csRGB, zona, False)
        destino.clear_with(255)
        destino.copy(pix, zona)
        if pag.limpiar_imagen and pag.tabla is not None:
            destino = limpiar_pixmap(destino, pag.tabla)
        destino.set_origin(0, 0)
        return destino
    finally:
        if pag.giro:
            page.set_rotation(base)


# --------------------------------------------------------------------------
# Escribir la capa de texto
# --------------------------------------------------------------------------

def _escribir_palabras(page: pymupdf.Page, palabras: list[PalabraOcr], pag: PaginaOcr | None) -> int:
    font = core._font(False)
    tw = pymupdf.TextWriter(page.rect)
    n = 0
    girada = pag is not None and pag.girada_sin_cambiar
    for w in palabras:
        texto = w.final.strip()
        r = w.marco if girada and w.marco is not None else w.rect
        x0, y0, x1, y1 = r
        h, ancho = y1 - y0, x1 - x0
        if h <= 0 or ancho <= 0 or not texto:
            continue
        size = h * 0.85
        largo = font.text_length(texto, fontsize=size)
        if largo > 0:
            size *= min(1.0, ancho / largo)        # que la palabra no se salga de su caja
            largo = min(largo, ancho)
        # centrada en su caja: así queda sobre la palabra que se ve aunque la letra sea más angosta
        tw.append((x0 + (ancho - largo) / 2, y1 - h * 0.2), texto, font=font, fontsize=max(size, 1))
        n += 1
    if n:
        page.wrap_contents()                       # aísla el contenido original (q … Q)
        morph = None
        if girada:
            c = (page.rect.tl + page.rect.br) / 2
            morph = (c, pymupdf.Matrix(pag.angulo))
        tw.write_text(page, render_mode=3, morph=morph)     # 3 = texto invisible
    return n


def _quitar_capa_invisible(page: pymupdf.Page) -> None:
    """Borra el texto invisible (la capa de OCR anterior) sin tocar imágenes, dibujos ni el texto visible."""
    zonas = []
    flags = pymupdf.TEXT_PRESERVE_SPANS | pymupdf.TEXT_COLLECT_STYLES
    for block in page.get_text("dict", flags=flags)["blocks"]:
        for line in block.get("lines", ()):
            for span in line["spans"]:
                if core.span_is_invisible(span) and span.get("text", "").strip():
                    zonas.append(pymupdf.Rect(span["bbox"]))
    for r in zonas:
        page.add_redact_annot(r)
    if zonas:
        core.apply_redactions(page, images=pymupdf.PDF_REDACT_IMAGE_NONE,
                              graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)


def escribir(path: str | Path, output: str | Path, resultado: ResultadoOcr, opciones: OpcionesOcr | None = None,
             password: str | None = None, progress: Progress = None) -> tuple[int, int]:
    """Escribe la capa de texto invisible con las correcciones en una copia.  Devuelve (páginas, palabras)."""
    paginas = [p for p in resultado.paginas if not p.omitida]
    if resultado.origen == "capa":
        paginas = [p for p in paginas if any(w.corregida is not None and w.corregida != w.texto for w in p.palabras)]
    hechas = escritas = 0
    with core._output(output) as tmp, core.open_pdf(path, password) as doc:
        for k, pag in enumerate(paginas):
            _report(progress, k, len(paginas), f"Escribiendo el texto: página {pag.indice + 1} de {doc.page_count}")
            if pag.indice >= doc.page_count:
                raise UserError("El documento cambió desde que se reconoció: vuelve a reconocerlo.")
            page = doc[pag.indice]
            if resultado.origen == "ocr" and not any(w.final.strip() for w in pag.palabras) \
                    and not (pag.giro or pag.enderezada or pag.limpiar_imagen):
                hechas += 1                    # nada que escribir ni que cambiar: la página queda intacta
                continue
            if resultado.origen == "capa":
                if page.rotation:
                    page.remove_rotation()
                _quitar_capa_invisible(page)
            else:
                preparar_pagina(page, pag)
                if page.rotation:
                    page.remove_rotation()
            escritas += _escribir_palabras(page, pag.palabras, pag)
            hechas += 1
        _report(progress, len(paginas), len(paginas), "Guardando…")
        core._save_keeping_password(doc, tmp, password)
    if resultado.origen == "capa":
        hechas = len([p for p in resultado.paginas if not p.omitida])
        escritas = resultado.palabras()
    return hechas, escritas


def reconocer(path: str | Path, output: str | Path, opciones: OpcionesOcr | None = None,
              password: str | None = None, progress: Progress = None) -> ResumenOcr:
    """``analizar`` + ``escribir``: el PDF buscable de una vez (lo que usa el paso de lotes)."""
    op = opciones or OpcionesOcr()
    t0 = time.time()

    def parte(desde: int, hasta: int):
        def fn(i, n, msg=""):
            _report(progress, desde + (hasta - desde) * i // max(n, 1), 100, msg)
        return fn

    res = analizar(path, op, password, parte(0, 90) if progress else None)
    if not res.reconocidas():                      # nada que reconocer: no se crea una copia igual al original
        return ResumenOcr(0, 0, 0, None, res, round(time.time() - t0, 2))
    paginas, palabras = escribir(path, output, res, op, password, parte(90, 100) if progress else None)
    return ResumenOcr(paginas, palabras, len(res.dudosas()), str(output), res, round(time.time() - t0, 2))


def _es_cancelacion(exc: BaseException) -> bool:
    return type(exc).__name__ in ("Cancelled", "KeyboardInterrupt")


def reconocer_carpeta(archivos: Sequence[str | Path], destino: str | Path | None, opciones: OpcionesOcr | None,
                      contrasenas: dict[str, str | None] | None = None,
                      progress: Progress = None) -> list[ArchivoOcr]:
    """Reconoce varios archivos.  Un error en uno no detiene a los demás y nunca se pisa un archivo.

    ``destino`` None: cada resultado queda junto a su original con el sufijo _ocr."""
    op = opciones or OpcionesOcr()
    contrasenas = {os.path.normcase(os.path.abspath(k)): v for k, v in (contrasenas or {}).items()}
    out: list[ArchivoOcr] = []
    n = len(archivos)
    for k, ruta in enumerate(archivos):
        ruta = str(ruta)
        nombre = Path(ruta).name
        _report(progress, k, n, f"{nombre} ({k + 1} de {n})")

        def sub(i, total, msg="", k=k, nombre=nombre):
            _report(progress, k, n, f"{nombre} ({k + 1} de {n}) — {msg}")

        pw = contrasenas.get(os.path.normcase(os.path.abspath(ruta)))
        try:
            if core.needs_password(ruta) and not pw:
                raise UserError("Está protegido con contraseña y no se escribió la clave.")
            res = analizar(ruta, op, pw, sub)
            if not res.reconocidas():
                out.append(ArchivoOcr(ruta, "ya tenía texto", resultado=res,
                                      mensaje="Todas las páginas ya tenían texto: no se creó una copia."))
                continue
            carpeta = Path(destino) if destino else Path(ruta).parent
            salida = core.unique_path(carpeta / f"{Path(ruta).stem}_ocr.pdf")
            paginas, palabras = escribir(ruta, salida, res, op, pw, sub)
            out.append(ArchivoOcr(ruta, "hecho", paginas, palabras, len(res.dudosas()), str(salida), resultado=res))
        except Exception as exc:  # noqa: BLE001 — un archivo dañado no detiene a los demás
            if _es_cancelacion(exc):
                raise
            if isinstance(exc, UserError):
                msg = str(exc)
            elif isinstance(exc, MemoryError):
                msg = "No hay suficiente memoria para este archivo."
            else:
                msg = f"No se pudo procesar: {exc}"
            out.append(ArchivoOcr(ruta, "error", mensaje=msg))
    _report(progress, n, n, "Listo")
    return out


def informe_carpeta(resultados: Sequence[ArchivoOcr]) -> str:
    """Informe en texto de un OCR por carpeta (para «Guardar informe…»)."""
    hechos = sum(1 for r in resultados if r.estado == "hecho")
    lineas = [f"Informe de reconocimiento de texto (OCR) — {time.strftime('%d/%m/%Y %H:%M')}",
              f"{len(resultados)} archivo(s): {hechos} reconocido(s), "
              f"{sum(1 for r in resultados if r.estado == 'ya tenía texto')} ya tenían texto, "
              f"{sum(1 for r in resultados if r.estado == 'error')} con error.", ""]
    for r in resultados:
        lineas.append(Path(r.ruta).name)
        lineas.append(f"    Resultado: {r.estado}")
        if r.estado == "hecho":
            lineas.append(f"    Páginas reconocidas: {r.paginas} · palabras: {r.palabras} · palabras dudosas: {r.dudosas}")
            lineas.append(f"    Guardado en: {r.salida}")
        if r.mensaje:
            lineas.append(f"    {r.mensaje}")
        lineas.append("")
    return "\n".join(lineas)


# --------------------------------------------------------------------------
# Revisar un PDF que ya trae su capa de OCR
# --------------------------------------------------------------------------

def leer_capa(path: str | Path, password: str | None = None, idioma: str = "spa+eng",
              progress: Progress = None) -> ResultadoOcr:
    """Las palabras de la capa invisible de un PDF ya buscable, con su confianza por diccionario y forma."""
    dic = diccionario(idioma)
    personal = diccionario_personal()
    paginas = []
    flags = pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES
    t0 = time.time()
    with core.open_pdf(path, password) as doc:
        total = doc.page_count
        for i in range(total):
            _report(progress, i, total, f"Leyendo la página {i + 1} de {total}")
            page = doc[i]
            mat = page.rotation_matrix
            ws: list[tuple] = []
            for b, block in enumerate(page.get_text("rawdict", flags=flags)["blocks"]):
                if block.get("type") != 0:
                    continue
                for ln, line in enumerate(block["lines"]):
                    actual: list = []
                    for span in line["spans"]:
                        if not core.span_is_invisible(span):
                            continue
                        for ch in span["chars"]:
                            c = ch["c"]
                            if not c or c.isspace():
                                if actual:
                                    ws.append(_palabra_de(actual, b, ln, len(ws), mat))
                                    actual = []
                                continue
                            actual.append(ch)
                    if actual:
                        ws.append(_palabra_de(actual, b, ln, len(ws), mat))
            if not ws:
                paginas.append(PaginaOcr(i, omitida=True, motivo="no tiene capa de texto reconocido"))
                continue
            puntos = _puntuar(ws, None, dic, personal)
            pag = PaginaOcr(i, lecturas=1)
            pag.palabras = [PalabraOcr(w[4], tuple(round(v, 2) for v in w[:4]), c, m, renglon=w[5] * 1000 + w[6])
                            for w, (c, m, _o) in zip(ws, puntos)]
            paginas.append(pag)
    _report(progress, total, total, "Listo")
    return ResultadoOcr(paginas, idioma, 0, round(time.time() - t0, 2), "capa", total)


def _palabra_de(chars: list[dict], b: int, ln: int, n: int, mat: pymupdf.Matrix) -> tuple:
    r = pymupdf.Rect(chars[0]["bbox"])
    for ch in chars[1:]:
        r |= pymupdf.Rect(ch["bbox"])
    r = r * mat
    return (r.x0, r.y0, r.x1, r.y1, "".join(ch["c"] for ch in chars), b, ln, n)


def estado_documento(path: str | Path, password: str | None = None) -> tuple[str, int, int]:
    """('escaneo' | 'capa' | 'digital', páginas que necesitan OCR, páginas con capa de OCR)."""
    faltan = con_capa = 0
    with core.open_pdf(path, password) as doc:
        for page in doc:
            if core.page_needs_ocr(page):
                faltan += 1
            else:
                _vis, inv, _a = core.text_layer(page)
                if inv >= 20:
                    con_capa += 1
    if faltan:
        return "escaneo", faltan, con_capa
    return ("capa" if con_capa else "digital"), faltan, con_capa


# --------------------------------------------------------------------------
# Texto plano (.txt) con el mismo motor
# --------------------------------------------------------------------------

def texto(path: str | Path, output: str | Path, opciones: OpcionesOcr | None = None, password: str | None = None,
          progress: Progress = None) -> int:
    """Extrae el texto a .txt: el digital tal cual y el de los escaneos con enderezado, giro y limpieza."""
    op = opciones or OpcionesOcr()
    res = analizar(path, replace(op, dos_pasadas=False), password, progress)
    partes = []
    with core.open_pdf(path, password) as doc:
        for pag in res.paginas:
            page = doc[pag.indice]
            if pag.omitida:
                t = page.get_text("text", sort=True)
            else:
                lineas: dict[int, list[PalabraOcr]] = collections.defaultdict(list)
                for w in pag.palabras:
                    lineas[w.renglon].append(w)
                renglones = []
                for ws in lineas.values():
                    caja = pymupdf.Rect(ws[0].marco or ws[0].rect)
                    for w in ws[1:]:
                        caja |= pymupdf.Rect(w.marco or w.rect)
                    renglones.append((caja, " ".join(w.final for w in ws if w.final.strip())))
                if pag.giro:
                    page.set_rotation((page.rotation + pag.giro) % 360)
                mat = page.rotation_matrix
                for block in page.get_text("dict")["blocks"]:
                    for line in block.get("lines", ()):
                        renglones.append((pymupdf.Rect(line["bbox"]) * mat,
                                          "".join(s.get("text", "") for s in line["spans"])))
                t = core._reading_order(renglones)
            partes.append(f"===== Página {pag.indice + 1} =====\n{t.strip()}\n")
    with core._output(output) as tmp:
        Path(tmp).write_text("\n".join(partes), encoding="utf-8-sig")
    return sum(len(p) for p in partes)
