"""Comparar dos PDF: texto, páginas y apariencia, más los informes en PDF y en Excel.

Sin interfaz: recibe rutas y devuelve datos.  Las coordenadas son puntos de la página tal como se ve
(con su giro aplicado), las mismas que usa el visor.

Cómo se compara:

* **Texto.**  No se compara página contra página: cada documento se convierte en una secuencia de
  renglones y se alinean los dos documentos completos (``difflib``).  Los renglones iguales quedan
  emparejados aunque hayan pasado a otra página; dentro de cada tramo distinto se compara palabra por
  palabra, así un párrafo que Word reacomodó por una palabra insertada da solo esa palabra.  Los
  encabezados y pies que se repiten (con «Página 3 de 10») se comparan aparte, página contra su pareja,
  para que el corrimiento entre páginas no los convierta en cambios.
* **Páginas.**  Cada página tiene una firma (su texto o, si no tiene, una miniatura de su imagen).  Las
  firmas alinean los documentos; lo que no se alinea se empareja por parecido de sus palabras.  Lo que
  sobra son páginas agregadas o quitadas, y una página quitada que reaparece igual en otro sitio, movida.
* **Apariencia.**  Cada par de páginas se dibuja en gris y se compara por celdas.  Lo que ya explica el
  texto (palabras cambiadas, o que solo se corrieron de sitio) se descarta; lo demás son imágenes,
  gráficos, sellos o formato distinto.

Ninguno de los dos documentos se modifica: solo se leen.
"""

from __future__ import annotations

import bisect
import difflib
import hashlib
import html
import io
import math
import os
import re
import secrets
import time
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

import pymupdf

from . import core, visor_texto

Progress = Callable[[int, int, str], None] | None

TIPOS = ("agregado", "quitado", "cambiado", "apariencia", "pagina_agregada", "pagina_quitada", "pagina_movida")
TIPOS_PAGINA = ("pagina_agregada", "pagina_quitada", "pagina_movida")
NOMBRES = {
    "agregado": "Agregado", "quitado": "Quitado", "cambiado": "Cambiado", "apariencia": "Apariencia",
    "pagina_agregada": "Página agregada", "pagina_quitada": "Página quitada", "pagina_movida": "Página movida",
}
# (singular, plural) para el resumen: «5 agregados · 1 página movida»
PLURALES = {
    "agregado": ("agregado", "agregados"), "quitado": ("quitado", "quitados"),
    "cambiado": ("cambiado", "cambiados"), "apariencia": ("de apariencia", "de apariencia"),
    "pagina_agregada": ("página agregada", "páginas agregadas"),
    "pagina_quitada": ("página quitada", "páginas quitadas"),
    "pagina_movida": ("página movida", "páginas movidas"),
}
# Colores de la leyenda (rojo = quitado, verde = agregado, ámbar = cambiado, azul = apariencia).
COLORES = {
    "agregado": "#1F9D55", "quitado": "#D63A3A", "cambiado": "#D98A00", "apariencia": "#2563EB",
    "pagina_agregada": "#1F9D55", "pagina_quitada": "#D63A3A", "pagina_movida": "#7C5CC4",
}

LIMITE_PALABRAS = 5000      # más que esto por lado: no se compara palabra por palabra de una vez
PARECIDO_MINIMO = 0.2       # por debajo, dos textos se tratan como distintos (sin detalle)
UMBRAL_PAGINA = 0.3         # parecido mínimo de palabras para emparejar dos páginas
CELDA = 16                  # píxeles por celda en la comparación de apariencia
AVISO_DISTINTOS = "Los documentos son muy distintos: se muestran las diferencias por bloques."
SIN_DIFERENCIAS = "No hay diferencias: los dos documentos tienen el mismo texto y la misma apariencia."
DESC_IMAGEN = "Imagen o gráfico distinto"
DESC_FORMATO = "Formato del texto distinto (color, letra o tamaño)"

_DIGITOS = re.compile(r"\d+")
_INVISIBLES = re.compile("[­​‌‍⁠﻿]")


# --------------------------------------------------------------------------
# Datos públicos
# --------------------------------------------------------------------------

@dataclass
class Opciones:
    texto: bool = True
    apariencia: bool = True
    ignorar_mayusculas: bool = False
    ignorar_encabezados: bool = False
    usar_ocr: bool = False
    idioma: str = "spa+eng"
    ppp: int = 72
    tolerancia: int = 24


@dataclass
class Cambio:
    id: int
    tipo: str                         # uno de TIPOS
    pagina_a: int | None              # página de la versión anterior (desde 0)
    pagina_b: int | None              # página de la versión nueva (desde 0)
    rects_a: list[tuple[float, float, float, float]]
    rects_b: list[tuple[float, float, float, float]]
    antes: str
    despues: str
    contexto: str
    marca_a: tuple[float, float, float, float] | None = None   # dónde estaría lo agregado en la anterior
    marca_b: tuple[float, float, float, float] | None = None   # dónde estaba lo quitado, en la nueva
    descripcion: str = ""             # apariencia y páginas: qué se ve distinto
    zona: str = "cuerpo"              # 'cuerpo' | 'margen' (encabezado o pie de página)


@dataclass
class Resultado:
    pares: list[tuple[int | None, int | None]]
    cambios: list[Cambio]
    resumen: dict[str, int]
    avisos: list[str]
    paginas_a: int
    paginas_b: int
    segundos: float
    tamanos_a: list[tuple[float, float]] = field(default_factory=list)
    tamanos_b: list[tuple[float, float]] = field(default_factory=list)
    escaneadas_a: list[int] = field(default_factory=list)     # escaneadas cuyo texto no se comparó
    escaneadas_b: list[int] = field(default_factory=list)
    opciones: Opciones = field(default_factory=Opciones)

    @property
    def total(self) -> int:
        return len(self.cambios)

    def texto_resumen(self) -> str:
        return frase_resumen(self.resumen, self.opciones)


# --------------------------------------------------------------------------
# Textos para mostrar
# --------------------------------------------------------------------------

def _plural(n: int, uno: str, varios: str) -> str:
    return f"{n} {uno if n == 1 else varios}"


def frase_resumen(resumen: dict[str, int], opciones: Opciones | None = None) -> str:
    """«12 cambios: 5 agregados · 3 quitados · 4 cambiados · 1 página agregada»."""
    total = sum(resumen.values())
    if not total:
        op = opciones or Opciones()
        if op.texto and op.apariencia:
            return SIN_DIFERENCIAS
        if op.texto:
            return "No hay diferencias: los dos documentos tienen el mismo texto."
        return "No hay diferencias: los dos documentos tienen la misma apariencia."
    partes = [_plural(resumen[t], *PLURALES[t]) for t in TIPOS if resumen.get(t)]
    return f"{_plural(total, 'cambio', 'cambios')}: " + " · ".join(partes)


def _corto(texto: str, n: int = 60) -> str:
    t = " ".join(texto.split())
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def detalle(c: Cambio, n: int = 60) -> str:
    """Lo que cambió, en pocas palabras: «2024» → «2025»."""
    if c.tipo == "cambiado":
        return f"«{_corto(c.antes, n // 2)}» → «{_corto(c.despues, n // 2)}»"
    if c.tipo == "agregado":
        return f"«{_corto(c.despues, n)}»"
    if c.tipo == "quitado":
        return f"«{_corto(c.antes, n)}»"
    return c.descripcion


def ubicacion(c: Cambio) -> str:
    """«Pág. 3 → 4» (página de la anterior → página de la nueva)."""
    a = str(c.pagina_a + 1) if c.pagina_a is not None else "—"
    b = str(c.pagina_b + 1) if c.pagina_b is not None else "—"
    return f"Pág. {a} → {b}"


def texto_lista(c: Cambio, n: int = 60) -> str:
    """«Pág. 3 → 3 · Cambiado: «2024» → «2025»»."""
    tipo = NOMBRES[c.tipo]
    if c.zona == "margen":
        tipo += " en el encabezado o pie"
    d = detalle(c, n)
    return f"{ubicacion(c)} · {tipo}" + (f": {d}" if d else "")


def lista_paginas(paginas: list[int]) -> str:
    """[1, 2, 3, 5] (desde 1) → «1 a 3 y 5»."""
    ps = sorted(set(paginas))
    tramos: list[str] = []
    k = 0
    while k < len(ps):
        j = k
        while j + 1 < len(ps) and ps[j + 1] == ps[j] + 1:
            j += 1
        if j - k >= 2:
            tramos.append(f"{ps[k]} a {ps[j]}")
        else:
            tramos.extend(str(p) for p in ps[k:j + 1])
        k = j + 1
    if len(tramos) <= 1:
        return "".join(tramos)
    return ", ".join(tramos[:-1]) + " y " + tramos[-1]


def filas_de(resultado: Resultado) -> tuple[dict[int, int], dict[int, int]]:
    """En qué fila de ``pares`` está cada página: ({página A: fila}, {página B: fila})."""
    fa, fb = {}, {}
    for k, (a, b) in enumerate(resultado.pares):
        if a is not None:
            fa[a] = k
        if b is not None:
            fb[b] = k
    return fa, fb


def lados(c: Cambio) -> tuple[bool, bool]:
    """¿El cambio se marca en la versión anterior? ¿Y en la nueva?"""
    en_a = c.pagina_a is not None and (bool(c.rects_a) or c.marca_a is not None)
    en_b = c.pagina_b is not None and (bool(c.rects_b) or c.marca_b is not None)
    return en_a, en_b


def cambios_por_fila(resultado: Resultado) -> dict[int, list[Cambio]]:
    fa, fb = filas_de(resultado)
    out: dict[int, list[Cambio]] = defaultdict(list)
    for c in resultado.cambios:
        en_a, en_b = lados(c)
        filas = set()
        if en_a and c.pagina_a in fa:
            filas.add(fa[c.pagina_a])
        if en_b and c.pagina_b in fb:
            filas.add(fb[c.pagina_b])
        for f in filas:
            out[f].append(c)
    return out


# --------------------------------------------------------------------------
# Lectura de las páginas
# --------------------------------------------------------------------------

@dataclass(slots=True)
class _Renglon:
    norm: str
    palabras: list            # [(texto, norm, rect)]
    banda: str | None         # 'sup' | 'inf' | None
    margen: bool = False


@dataclass(slots=True)
class _Pagina:
    indice: int
    ancho: float
    alto: float
    escaneada: bool = False             # escaneada y sin OCR: su texto no se compara
    renglones: list = field(default_factory=list)
    firma: str = ""
    blanca: bool = False
    w0: int = 0                         # palabras del cuerpo: [w0, w1) en la secuencia global
    w1: int = 0
    palabras_margen: list = field(default_factory=list)   # [(texto, norm, rect)]

    @property
    def n_palabras(self) -> int:
        return self.w1 - self.w0


@dataclass(slots=True)
class _Palabra:
    norm: str
    texto: str
    pagina: int
    rect: tuple
    linea: int


def _norm(texto: str, mayusculas: bool) -> str:
    # NFKC une las variantes de una misma letra; sin espacios internos (un espacio duro dentro de una
    # «palabra» de MuPDF se volvería un espacio y descuadraría los renglones).
    t = "".join(_INVISIBLES.sub("", unicodedata.normalize("NFKC", texto)).split())
    return t.casefold() if mayusculas else t


def _banda(rect: tuple, alto: float) -> str | None:
    cy = (rect[1] + rect[3]) / 2
    if cy < alto * 0.12:
        return "sup"
    if cy > alto * 0.88:
        return "inf"
    return None


def _union(rects) -> tuple[float, float, float, float]:
    rs = list(rects)
    return (min(r[0] for r in rs), min(r[1] for r in rs), max(r[2] for r in rs), max(r[3] for r in rs))


def _miniatura(page: pymupdf.Page) -> tuple[str, bool]:
    """Huella de la imagen de una página sin texto (muy reducida) y si está en blanco."""
    r = page.rect
    z = 48 / max(r.width, r.height, 1)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(z, z), colorspace=pymupdf.csGRAY, alpha=False)
    q = bytes(pix.samples).translate(bytes(v // 32 for v in range(256)))
    blanca = q.count(7) == len(q)
    return "i:" + hashlib.blake2b(q, digest_size=12).hexdigest(), blanca


def _palabras_visibles(page: pymupdf.Page) -> list[tuple]:
    """(texto, rect, bloque, renglón) de cada palabra, como ``visor_texto.palabras_pagina``.  En una página
    sin giro las coordenadas ya son las visibles y se evita convertir palabra por palabra (consultar el
    giro de la página por cada palabra costaba más que leer el texto)."""
    if page.rotation:
        return [(w.texto, w.rect, w.bloque, w.linea) for w in visor_texto.palabras_pagina(page)]
    return [(t, (x0, y0, x1, y1), b, ln)
            for x0, y0, x1, y1, t, b, ln, _n in page.get_text("words", flags=visor_texto.WORD_FLAGS) if t.strip()]


def _leer_documento(doc: pymupdf.Document, op: Opciones, tess: str | None, progress: Progress,
                    base: int, total: int, cual: str, avisos: list[str], nombre: str) -> list[_Pagina]:
    paginas = []
    n = doc.page_count
    for i in range(n):
        core._report(progress, base + i, total, f"Leyendo el texto: página {i + 1} de {n} de la {cual}")
        page = doc[i]
        r = page.rect
        p = _Pagina(i, r.width, r.height)
        crudas: list[tuple] = []          # (texto, rect, bloque, linea)
        try:
            crudas = _palabras_visibles(page)
            # Solo puede ser un escaneo si tiene imágenes o casi no tiene texto (así no se paga el análisis
            # de cada página de un documento de texto).
            escaneada = False
            if page.get_images(full=False) or sum(len(c[0]) for c in crudas) < 400:
                escaneada = core.page_needs_ocr(page)
            if escaneada and op.usar_ocr and tess:
                core._report(progress, base + i, total, f"Leyendo con OCR la página {i + 1} de la {cual}…")
                crudas = []
                for x0, y0, x1, y1, t, b, ln, *_ in core._ocr_words(page, op.idioma, 200, tess):
                    if t.strip():
                        v = core._visual(page, (x0, y0, x1, y1))
                        crudas.append((t, (v.x0, v.y0, v.x1, v.y1), b, ln))
                escaneada = False
            elif escaneada:
                crudas = []
            p.escaneada = escaneada
        except core.UserError:
            raise
        except Exception:  # noqa: BLE001 — una página dañada no debe impedir comparar el resto
            avisos.append(f"No se pudo leer el texto de la página {i + 1} de «{nombre}».")
            crudas = []
        # renglones en orden de lectura
        clave = None
        for t, rect, b, ln in crudas:
            if (b, ln) != clave:
                p.renglones.append(_Renglon("", [], None))
                clave = (b, ln)
            p.renglones[-1].palabras.append((t, _norm(t, op.ignorar_mayusculas), rect))
        for rg in p.renglones:
            rg.norm = " ".join(w[1] for w in rg.palabras)
        _bandas(p)
        paginas.append(p)
    return paginas


def _bandas(p: _Pagina) -> None:
    """Marca los renglones que pueden ser encabezado o pie: en la franja de arriba (o de abajo) y separados
    del resto del texto por un espacio mayor que el de un renglón (el primer renglón del cuerpo no lo es)."""
    cajas = [_union(w[2] for w in rg.palabras) for rg in p.renglones]
    for rg, r in zip(p.renglones, cajas):
        banda = _banda(r, p.alto)
        if banda is None:
            continue
        alto = max(r[3] - r[1], 1.0)
        separacion = max(8.0, 1.2 * alto)
        if banda == "sup":
            debajo = [s[1] - r[3] for s in cajas if s is not r and s[1] >= r[1] + alto * 0.5]
            libre = min(debajo, default=separacion) >= separacion
        else:
            encima = [r[1] - s[3] for s in cajas if s is not r and s[3] <= r[3] - alto * 0.5]
            libre = min(encima, default=separacion) >= separacion
        rg.banda = banda if libre else None


def _marcar_margenes(paginas: list[_Pagina]) -> None:
    """Encabezados y pies: renglones que se repiten en la misma franja (arriba o abajo) en al menos la
    mitad de las páginas, comparados con los dígitos cambiados por «#» («Página 3 de 10»)."""
    con_texto = [p for p in paginas if p.renglones]
    if len(con_texto) < 2:
        return
    umbral = max(2, math.ceil(len(paginas) / 2))
    conteo: Counter = Counter()
    for p in con_texto:
        conteo.update({(rg.banda, _DIGITOS.sub("#", rg.norm)) for rg in p.renglones if rg.banda})
    repetidos = {k for k, c in conteo.items() if c >= umbral}
    if not repetidos:
        return
    for p in con_texto:
        for rg in p.renglones:
            if rg.banda and (rg.banda, _DIGITOS.sub("#", rg.norm)) in repetidos:
                rg.margen = True


def _firmar(doc: pymupdf.Document, paginas: list[_Pagina]) -> None:
    for p in paginas:
        cuerpo = [rg.norm for rg in p.renglones if not rg.margen]
        todo = cuerpo or [rg.norm for rg in p.renglones]
        if todo:
            p.firma = "t:" + hashlib.blake2b("\n".join(todo).encode("utf-8", "replace"),
                                             digest_size=16).hexdigest()
        else:
            try:
                p.firma, p.blanca = _miniatura(doc[p.indice])
            except Exception:  # noqa: BLE001
                p.firma, p.blanca = f"x:{p.indice}", False


def _secuencia(paginas: list[_Pagina]) -> tuple[list[_Palabra], list[tuple[str, int, int]]]:
    """Palabras del cuerpo de todo el documento y sus renglones: (texto normalizado, desde, hasta)."""
    palabras: list[_Palabra] = []
    renglones: list[tuple[str, int, int]] = []
    for p in paginas:
        p.w0 = len(palabras)
        for rg in p.renglones:
            if rg.margen:
                p.palabras_margen.extend(rg.palabras)
                continue
            ini = len(palabras)
            k = len(renglones)
            for t, nm, rect in rg.palabras:
                palabras.append(_Palabra(nm, t, p.indice, rect, k))
            renglones.append((rg.norm, ini, len(palabras)))
        p.w1 = len(palabras)
    return palabras, renglones


# --------------------------------------------------------------------------
# Comparación de secuencias (sin volverse cuadrática)
# --------------------------------------------------------------------------

def _parecido(a: list[str], b: list[str]) -> float:
    """Proporción de pares de palabras seguidas que comparten (0 = nada, 1 = iguales)."""
    if len(a) < 2 or len(b) < 2:
        return 1.0 if a == b else 0.0
    ca, cb = Counter(zip(a, a[1:])), Counter(zip(b, b[1:]))
    comun = sum((ca & cb).values())
    return 2 * comun / (len(a) + len(b) - 2)


def _lis(pares: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Subsecuencia creciente más larga (en j) de pares ya ordenados por i."""
    colas: list[int] = []
    idx: list[int] = []
    previo = [-1] * len(pares)
    for k, (_i, j) in enumerate(pares):
        pos = bisect.bisect_left(colas, j)
        if pos == len(colas):
            colas.append(j)
            idx.append(k)
        else:
            colas[pos] = j
            idx[pos] = k
        previo[k] = idx[pos - 1] if pos else -1
    out = []
    k = idx[-1] if idx else -1
    while k >= 0:
        out.append(pares[k])
        k = previo[k]
    return out[::-1]


def _fusionar(ops: list[tuple]) -> list[tuple]:
    out: list[list] = []
    for op in ops:
        if op[1] == op[2] and op[3] == op[4]:
            continue
        if out and out[-1][0] == op[0] and out[-1][5] == op[5] and out[-1][2] == op[1] and out[-1][4] == op[3]:
            out[-1][2], out[-1][4] = op[2], op[4]
        else:
            out.append(list(op))
    # un «delete» seguido de un «insert» (o al revés) es un reemplazo
    final: list[list] = []
    for op in out:
        if final and {final[-1][0], op[0]} == {"delete", "insert"} and final[-1][5] == op[5] \
                and final[-1][2] == op[1] and final[-1][4] == op[3]:
            final[-1] = ["replace", final[-1][1], op[2], final[-1][3], op[4], op[5]]
        else:
            final.append(op)
    return [tuple(op) for op in final]


class _Comparador:
    """Compara secuencias de palabras con límites: nunca se vuelve cuadrático sin control."""

    def __init__(self):
        self.por_bloques = False      # algún tramo enorme y distinto quedó como bloque (para el aviso)

    def opcodes(self, a: list[str], b: list[str], prof: int = 0) -> list[tuple]:
        """Como SequenceMatcher.get_opcodes, con una sexta columna: True = tramo «por bloque»."""
        n, m = len(a), len(b)
        if not n and not m:
            return []
        if not n:
            return [("insert", 0, 0, 0, m, False)]
        if not m:
            return [("delete", 0, n, 0, 0, False)]
        if n <= LIMITE_PALABRAS and m <= LIMITE_PALABRAS:
            if n >= 8 and m >= 8 and _parecido(a, b) < PARECIDO_MINIMO:
                return [("replace", 0, n, 0, m, True)]
            sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
            return [(t, i1, i2, j1, j2, False) for t, i1, i2, j1, j2 in sm.get_opcodes()]
        # Tramo grande: se parte por palabras que aparecen una sola vez en cada lado (como el «patience
        # diff») y se compara cada pedazo por separado.
        if prof < 6 and _parecido(a, b) >= PARECIDO_MINIMO:
            anclas = self._anclas(a, b)
            if anclas:
                out: list[tuple] = []
                pa = pb = 0
                for i, j in anclas + [(n, m)]:
                    if i > pa or j > pb:
                        for t, i1, i2, j1, j2, blq in self.opcodes(a[pa:i], b[pb:j], prof + 1):
                            out.append((t, pa + i1, pa + i2, pb + j1, pb + j2, blq))
                    if i < n:
                        out.append(("equal", i, i + 1, j, j + 1, False))
                    pa, pb = i + 1, j + 1
                return _fusionar(out)
        self.por_bloques = True
        return [("replace", 0, n, 0, m, True)]

    @staticmethod
    def _anclas(a: list[str], b: list[str]) -> list[tuple[int, int]]:
        ca, cb = Counter(a), Counter(b)
        pos_b = {w: j for j, w in enumerate(b) if cb[w] == 1}
        pares = [(i, pos_b[w]) for i, w in enumerate(a) if ca[w] == 1 and w in pos_b]
        return _lis(pares)


# --------------------------------------------------------------------------
# Apariencia
# --------------------------------------------------------------------------

_TABLAS: dict[int, tuple[bytes, bytes, bytes]] = {}


def _tablas(tol: int) -> tuple[bytes, bytes, bytes]:
    t = max(2, int(tol))
    if t not in _TABLAS:
        _TABLAS[t] = (bytes(min(255, v // t) for v in range(256)),
                      bytes(min(255, (v + t // 2) // t) for v in range(256)),
                      bytes([0] + [1] * 255))
    return _TABLAS[t]


def _mapa(sa: bytes, sb: bytes, tol: int) -> bytearray | None:
    """1 donde los dos grises difieren más que la tolerancia (aproximadamente), 0 donde no.

    Sin numpy: se cuantiza dos veces (con un corrimiento de media tolerancia) y se comparan los dos
    resultados con XOR de enteros grandes; un píxel cuenta solo si difiere en las dos cuantizaciones, así
    el ruido del suavizado de bordes no cuenta."""
    if sa == sb:
        return None
    q1, q2, nz = _tablas(tol)
    n = len(sa)
    x1 = int.from_bytes(sa.translate(q1), "little") ^ int.from_bytes(sb.translate(q1), "little")
    if not x1:
        return None
    x2 = int.from_bytes(sa.translate(q2), "little") ^ int.from_bytes(sb.translate(q2), "little")
    if not x2:
        return None
    d = int.from_bytes(x1.to_bytes(n, "little").translate(nz), "little") \
        & int.from_bytes(x2.to_bytes(n, "little").translate(nz), "little")
    return bytearray(d.to_bytes(n, "little")) if d else None


def _gris(page: pymupdf.Page, ppp: int) -> tuple[int, int, bytes]:
    pix = page.get_pixmap(dpi=ppp, colorspace=pymupdf.csGRAY, alpha=False)
    w, h, st = pix.width, pix.height, pix.stride
    s = bytes(pix.samples)
    if st != w:
        s = b"".join(s[y * st:y * st + w] for y in range(h))
    return w, h, s


def _recortar(w: int, h: int, s: bytes, wc: int, hc: int) -> bytes:
    if w == wc and h >= hc:
        return s[: wc * hc]
    return b"".join(s[y * w:y * w + wc] for y in range(hc))


def _borrar(d: bytearray, w: int, h: int, rects, escala: float, margen: int = 1) -> None:
    for r in rects:
        x0 = max(0, int(r[0] * escala) - margen)
        x1 = min(w, int(math.ceil(r[2] * escala)) + margen)
        y0 = max(0, int(r[1] * escala) - margen)
        y1 = min(h, int(math.ceil(r[3] * escala)) + margen)
        if x1 <= x0 or y1 <= y0:
            continue
        cero = bytes(x1 - x0)
        for y in range(y0, y1):
            d[y * w + x0:y * w + x1] = cero


def _regiones(d: bytearray, w: int, h: int, escala: float, minimo: int = 3) -> list[tuple]:
    """Regiones distintas (en puntos), uniendo celdas vecinas de ~16 × 16 píxeles."""
    celdas: dict[tuple[int, int], int] = {}
    cols = (w + CELDA - 1) // CELDA
    for cy in range((h + CELDA - 1) // CELDA):
        y0, y1 = cy * CELDA, min(h, cy * CELDA + CELDA)
        banda = d[y0 * w:y1 * w]
        if banda.find(1) < 0:
            continue
        suma = 0
        for r in range(y1 - y0):              # suma de columnas: cada byte queda entre 0 y 16
            suma += int.from_bytes(banda[r * w:(r + 1) * w], "little")
        col = suma.to_bytes(w, "little")
        for cx in range(cols):
            c = sum(col[cx * CELDA:cx * CELDA + CELDA])
            if c >= minimo:
                celdas[(cx, cy)] = c
    regiones = []
    vistos: set = set()
    for inicio in celdas:
        if inicio in vistos:
            continue
        pila, grupo = [inicio], []
        vistos.add(inicio)
        while pila:
            cx, cy = pila.pop()
            grupo.append((cx, cy))
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    v = (cx + dx, cy + dy)
                    if v in celdas and v not in vistos:
                        vistos.add(v)
                        pila.append(v)
        peso = sum(celdas[g] for g in grupo)
        if len(grupo) == 1 and peso < 12:        # una celda suelta con muy poca diferencia
            continue
        x0 = min(g[0] for g in grupo) * CELDA
        y0 = min(g[1] for g in grupo) * CELDA
        x1 = min(w, (max(g[0] for g in grupo) + 1) * CELDA)
        y1 = min(h, (max(g[1] for g in grupo) + 1) * CELDA)
        regiones.append((x0 / escala, y0 / escala, x1 / escala, y1 / escala))
    return regiones


def _cruza(r, s, margen: float = 0.0) -> bool:
    return r[0] - margen < s[2] and s[0] - margen < r[2] and r[1] - margen < s[3] and s[1] - margen < r[3]


def _area(r) -> float:
    return max(0.0, r[2] - r[0]) * max(0.0, r[3] - r[1])


def _inter(r, s) -> float:
    return _area((max(r[0], s[0]), max(r[1], s[1]), min(r[2], s[2]), min(r[3], s[3])))


def _objetos(page: pymupdf.Page) -> list[tuple[tuple, tuple]]:
    """Imágenes y trazos de la página: (firma sin posición, rectángulo visible)."""
    out = []
    try:
        for info in page.get_image_info(hashes=True):
            r = core._visual(page, info["bbox"])
            dig = info.get("digest") or b""
            out.append((("img", bytes(dig), round(r.width), round(r.height)), (r.x0, r.y0, r.x1, r.y1)))
    except Exception:  # noqa: BLE001
        pass
    try:
        for dr in page.get_drawings():
            r = core._visual(page, dr["rect"])

            def _c(c):
                return tuple(round(v, 2) for v in c) if c else None
            firma = ("dib", dr.get("type"), round(dr.get("width") or 0, 1), _c(dr.get("color")),
                     _c(dr.get("fill")), round(r.width), round(r.height), len(dr.get("items") or ()))
            out.append((firma, (r.x0, r.y0, r.x1, r.y1)))
    except Exception:  # noqa: BLE001
        pass
    return out


def _objetos_movidos(oa: list, ob: list) -> list[tuple]:
    """Rectángulos de los objetos que están en las dos páginas pero en otro sitio (se corrieron con el
    texto): se descartan de la comparación de apariencia en sus dos posiciones."""
    por_a: dict = defaultdict(list)
    por_b: dict = defaultdict(list)
    for f, r in oa:
        por_a[f].append(r)
    for f, r in ob:
        por_b[f].append(r)
    out = []
    for f, ras in por_a.items():
        rbs = list(por_b.get(f, ()))
        if not rbs:
            continue
        resto_a = []
        for r in ras:                              # los que no se movieron, fuera
            k = next((k for k, s in enumerate(rbs) if abs(s[0] - r[0]) <= 1.5 and abs(s[1] - r[1]) <= 1.5), None)
            if k is None:
                resto_a.append(r)
            else:
                rbs.pop(k)
        for r in resto_a:                          # cada uno con el más cercano que quede
            if not rbs:
                break
            k = min(range(len(rbs)), key=lambda k: abs(rbs[k][0] - r[0]) + abs(rbs[k][1] - r[1]))
            out.append(r)
            out.append(rbs.pop(k))
    return out


# --------------------------------------------------------------------------
# La comparación
# --------------------------------------------------------------------------

class _Trabajo:
    def __init__(self, da, db, op: Opciones, progress: Progress, nombre_a: str, nombre_b: str):
        self.da, self.db, self.op, self.progress = da, db, op, progress
        self.nombre_a, self.nombre_b = nombre_a, nombre_b
        self.avisos: list[str] = []
        self.cambios: list[Cambio] = []

    # ------------------------------------------------------------------ lectura
    def leer(self):
        op = self.op
        tess = core._check_ocr(op.idioma) if op.usar_ocr else None
        total = self.da.page_count + self.db.page_count
        self.pa = _leer_documento(self.da, op, tess, self.progress, 0, total, "versión anterior", self.avisos,
                                  self.nombre_a)
        self.pb = _leer_documento(self.db, op, tess, self.progress, self.da.page_count, total, "versión nueva",
                                  self.avisos, self.nombre_b)
        core._report(self.progress, total, total, "Comparando el texto…")
        for paginas, doc in ((self.pa, self.da), (self.pb, self.db)):
            _marcar_margenes(paginas)
            _firmar(doc, paginas)
        self.wa, self.la = _secuencia(self.pa)
        self.wb, self.lb = _secuencia(self.pb)

    # ------------------------------------------------------------------ texto
    def comparar_texto(self):
        """Alinea todo el texto (renglones y, dentro de cada tramo distinto, palabras)."""
        wa, wb = self.wa, self.wb
        self.par_a = [-1] * len(wa)          # palabra emparejada en el otro documento (-1 = ninguna)
        self.par_b = [-1] * len(wb)
        self.tramos: list[tuple] = []         # (tipo, a1, a2, b1, b2, por_bloque) en índices de palabras
        comp = _Comparador()
        muy_distintos = (len(wa) >= 50 and len(wb) >= 50
                         and _parecido([w.norm for w in wa], [w.norm for w in wb]) < PARECIDO_MINIMO)
        ops = difflib.SequenceMatcher(None, [r[0] for r in self.la], [r[0] for r in self.lb],
                                      autojunk=False).get_opcodes()
        for t, i1, i2, j1, j2 in ops:
            a1 = self.la[i1][1] if i1 < len(self.la) else len(wa)
            a2 = self.la[i2 - 1][2] if i2 > i1 else a1
            b1 = self.lb[j1][1] if j1 < len(self.lb) else len(wb)
            b2 = self.lb[j2 - 1][2] if j2 > j1 else b1
            if t == "equal" and a2 - a1 == b2 - b1:
                self._emparejar(a1, b1, a2 - a1)
                continue
            if muy_distintos and t != "equal":
                self.tramos.append(("replace" if a2 > a1 and b2 > b1 else "delete" if a2 > a1 else "insert",
                                    a1, a2, b1, b2, True))
                continue
            sub = comp.opcodes([w.norm for w in wa[a1:a2]], [w.norm for w in wb[b1:b2]])
            for st, x1, x2, y1, y2, blq in sub:
                if st == "equal":
                    self._emparejar(a1 + x1, b1 + y1, x2 - x1)
                else:
                    self.tramos.append((st, a1 + x1, a1 + x2, b1 + y1, b1 + y2, blq))
        if muy_distintos or comp.por_bloques:
            self.avisos.append(AVISO_DISTINTOS)
        # cuántas palabras comparte cada par de páginas (para emparejar las páginas)
        self.solape: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
        self.emparejadas_a = [0] * len(self.pa)
        self.emparejadas_b = [0] * len(self.pb)
        for i, j in enumerate(self.par_a):
            if j >= 0:
                pa, pb = wa[i].pagina, wb[j].pagina
                self.solape[pa][pb] += 1
                self.emparejadas_a[pa] += 1
                self.emparejadas_b[pb] += 1

    def _emparejar(self, a: int, b: int, n: int):
        for k in range(n):
            self.par_a[a + k] = b + k
            self.par_b[b + k] = a + k

    # ------------------------------------------------------------------ páginas
    def alinear_paginas(self):
        sa, sb = [p.firma for p in self.pa], [p.firma for p in self.pb]
        pares: list[tuple] = []
        for t, i1, i2, j1, j2 in _corregir_corrimientos(
                difflib.SequenceMatcher(None, sa, sb, autojunk=False).get_opcodes(), sa, sb):
            if t == "zip":
                pares += [(i1 + k, j1 + k) for k in range(i2 - i1)]
            elif t == "equal":
                pares += [(i1 + k, j1 + k) for k in range(i2 - i1)]
            elif t == "delete":
                pares += [(i, None) for i in range(i1, i2)]
            elif t == "insert":
                pares += [(None, j) for j in range(j1, j2)]
            else:
                pares += self._emparejar_bloque(i1, i2, j1, j2)
        self.pares = pares

    def _parecido_paginas(self, a: int, b: int) -> float:
        n = max(self.pa[a].n_palabras, self.pb[b].n_palabras, 1)
        return self.solape.get(a, {}).get(b, 0) / n

    def _emparejar_bloque(self, i1, i2, j1, j2) -> list[tuple]:
        cands = []
        for a in range(i1, i2):
            for b in self.solape.get(a, {}):
                if j1 <= b < j2:
                    s = self._parecido_paginas(a, b)
                    if s >= UMBRAL_PAGINA:
                        cands.append((-s, a, b))
        cands.sort()
        aceptados: dict[int, int] = {}
        orden: list[int] = []
        usados_b: set[int] = set()
        for _s, a, b in cands:
            if a in aceptados or b in usados_b:
                continue
            k = bisect.bisect_left(orden, a)
            if (k > 0 and aceptados[orden[k - 1]] > b) or (k < len(orden) and aceptados[orden[k]] < b):
                continue                       # cruzaría otra pareja: las páginas van en orden
            orden.insert(k, a)
            aceptados[a] = b
            usados_b.add(b)
        filas: list[tuple] = []
        pa, pb = i1, j1
        for a in orden + [i2]:
            b = aceptados.get(a, j2)
            filas += self._hueco(list(range(pa, a)), list(range(pb, b)))
            if a < i2:
                filas.append((a, b))
                pa, pb = a + 1, b + 1
        return filas

    def _hueco(self, ga: list[int], gb: list[int]) -> list[tuple]:
        """Páginas sin pareja entre dos parejas: si son tantas de un lado como del otro, se reemplazaron
        en su sitio; si no, se emparejan solo las que no tienen texto (imágenes, escaneos), en orden."""
        if not ga or not gb:
            return [(a, None) for a in ga] + [(None, b) for b in gb]
        if len(ga) == len(gb):
            return list(zip(ga, gb))
        ta = [a for a in ga if not self.pa[a].n_palabras]
        tb = [b for b in gb if not self.pb[b].n_palabras]
        filas: list[tuple] = []
        ia = ib = 0
        for a, b in zip(ta, tb):
            while ga[ia] != a:
                filas.append((ga[ia], None))
                ia += 1
            while gb[ib] != b:
                filas.append((None, gb[ib]))
                ib += 1
            filas.append((a, b))
            ia += 1
            ib += 1
        filas += [(a, None) for a in ga[ia:]] + [(None, b) for b in gb[ib:]]
        return filas

    def paginas_sueltas(self):
        """Páginas agregadas, quitadas y movidas, y qué palabras ya no se listan una por una."""
        op = self.op

        def real(p: _Pagina, emparejadas: int) -> bool:
            # sin texto (imagen o escaneo) o con la mayoría de sus palabras nuevas: es una página de verdad
            # agregada o quitada; si casi todo su texto existe en el otro documento, solo recibió el texto
            # que se corrió de la página anterior.
            return not p.n_palabras or emparejadas / p.n_palabras < 0.5

        sueltas_a = [a for a, b in self.pares if b is None and real(self.pa[a], self.emparejadas_a[a])]
        sueltas_b = [b for a, b in self.pares if a is None and real(self.pb[b], self.emparejadas_b[b])]
        por_firma: dict[str, list[int]] = defaultdict(list)
        for b in sueltas_b:
            if not self.pb[b].blanca:
                por_firma[self.pb[b].firma].append(b)
        self.movidas: list[tuple[int, int]] = []
        movida_b: set[int] = set()
        for a in sueltas_a:
            cand = por_firma.get(self.pa[a].firma) if not self.pa[a].blanca else None
            if cand:
                b = cand.pop(0)
                self.movidas.append((a, b))
                movida_b.add(b)
        movida_a = {a for a, _b in self.movidas}
        for a in sueltas_a:
            if a not in movida_a:
                p = self.pa[a]
                self.cambios.append(Cambio(0, "pagina_quitada", a, None, [(0, 0, p.ancho, p.alto)], [], "", "",
                                           "", descripcion=f"Página {a + 1} de la versión anterior"))
        for b in sueltas_b:
            if b not in movida_b:
                p = self.pb[b]
                self.cambios.append(Cambio(0, "pagina_agregada", None, b, [], [(0, 0, p.ancho, p.alto)], "", "",
                                           "", descripcion=f"Página {b + 1} de la versión nueva"))
        for a, b in self.movidas:
            pa_, pb_ = self.pa[a], self.pb[b]
            self.cambios.append(Cambio(0, "pagina_movida", a, b, [(0, 0, pa_.ancho, pa_.alto)],
                                       [(0, 0, pb_.ancho, pb_.alto)], "", "", "",
                                       descripcion=f"De la página {a + 1} a la página {b + 1}"))
        # Palabras que no se listan una por una: las de páginas agregadas, quitadas o movidas (ya son un
        # cambio de página), las de páginas cuya pareja es un escaneo sin OCR y las de parejas idénticas.
        self.supr_a = set(sueltas_a)
        self.supr_b = set(sueltas_b)
        for a, b in self.pares:
            if a is None or b is None:
                continue
            if self.pa[a].escaneada:
                self.supr_b.add(b)
            if self.pb[b].escaneada:
                self.supr_a.add(a)
            if self.pa[a].firma == self.pb[b].firma and self.pa[a].n_palabras:
                self.supr_a.add(a)
                self.supr_b.add(b)
        esc_a = [p.indice + 1 for p in self.pa if p.escaneada]
        esc_b = [p.indice + 1 for p in self.pb if p.escaneada]
        if (esc_a or esc_b) and op.texto:
            self.avisos.insert(0, _aviso_escaneadas(esc_a, esc_b))

    # ------------------------------------------------------------------ cambios de texto
    def cambios_de_texto(self):
        if not self.op.texto:
            return
        wa, wb = self.wa, self.wb
        for t, a1, a2, b1, b2, _blq in self.tramos:
            ia = [k for k in range(a1, a2) if wa[k].pagina not in self.supr_a]
            ib = [k for k in range(b1, b2) if wb[k].pagina not in self.supr_b]
            if not ia and not ib:
                continue
            ga, gb = _por_pagina(wa, ia), _por_pagina(wb, ib)
            marca_a = self._marca(wa, self.pa, a1, a2)
            marca_b = self._marca(wb, self.pb, b1, b2)
            for k in range(max(len(ga), len(gb))):
                xa = ga[k] if k < len(ga) else None
                xb = gb[k] if k < len(gb) else None
                if xa and xb:
                    self._agregar("cambiado", xa, xb, wa, wb, a1, a2, b1, b2)
                elif xa:
                    self._agregar("quitado", xa, None, wa, wb, a1, a2, b1, b2, marca_b=marca_b)
                else:
                    self._agregar("agregado", None, xb, wa, wb, a1, a2, b1, b2, marca_a=marca_a)

    @staticmethod
    def _marca(palabras: list[_Palabra], paginas: list[_Pagina], i1: int, i2: int):
        """Dónde está (o estaría) un tramo en el otro documento: un trazo fino después de la palabra
        anterior o, si no hay, antes de la siguiente."""
        if i2 > i1 or not palabras:
            return None
        if i1 > 0:
            w = palabras[i1 - 1]
            x = w.rect[2] + 1
        else:
            w = palabras[0]
            x = w.rect[0] - 2
        return w.pagina, (x - 0.75, w.rect[1], x + 0.75, w.rect[3])

    def _agregar(self, tipo, xa, xb, wa, wb, a1, a2, b1, b2, marca_a=None, marca_b=None):
        def rects(palabras, idx):
            por_linea: dict[int, list] = {}
            for k in idx:
                por_linea.setdefault(palabras[k].linea, []).append(palabras[k].rect)
            return [_union(rs) for rs in por_linea.values()]

        def texto(palabras, idx):
            t = " ".join(palabras[k].texto for k in idx)
            return t if len(t) <= 1000 else t[:999] + "…"

        pag_a = wa[xa[0]].pagina if xa else (marca_a[0] if marca_a else None)
        pag_b = wb[xb[0]].pagina if xb else (marca_b[0] if marca_b else None)
        if xb:
            contexto = _contexto(wb, xb[0], xb[-1] + 1)
        else:
            contexto = _contexto(wa, xa[0], xa[-1] + 1)
        self.cambios.append(Cambio(
            0, tipo, pag_a, pag_b, rects(wa, xa) if xa else [], rects(wb, xb) if xb else [],
            texto(wa, xa) if xa else "", texto(wb, xb) if xb else "", contexto,
            marca_a=marca_a[1] if marca_a and not xa else None,
            marca_b=marca_b[1] if marca_b and not xb else None))

    def cambios_de_margen(self):
        """Encabezados y pies, página contra su pareja.  Un número de página que solo cambió porque se
        agregaron o quitaron páginas no cuenta."""
        if not self.op.texto or self.op.ignorar_encabezados:
            return
        na, nb = len(self.pa), len(self.pb)
        for a, b in self.pares:
            if a is None or b is None:
                continue
            pa_, pb_ = self.pa[a], self.pb[b]
            if pa_.escaneada or pb_.escaneada:
                continue
            ma, mb = pa_.palabras_margen, pb_.palabras_margen
            ta, tb = [w[1] for w in ma], [w[1] for w in mb]
            if ta == tb:
                continue
            if [_numero_pagina(t, a, na) for t in ta] == [_numero_pagina(t, b, nb) for t in tb]:
                continue
            sm = difflib.SequenceMatcher(None, ta, tb, autojunk=False)
            for t, i1, i2, j1, j2 in sm.get_opcodes():
                if t == "equal":
                    continue
                xa, xb = ma[i1:i2], mb[j1:j2]
                if xa and xb and [_numero_pagina(w[1], a, na) for w in xa] == \
                        [_numero_pagina(w[1], b, nb) for w in xb]:
                    continue
                tipo = "cambiado" if xa and xb else "quitado" if xa else "agregado"
                marca_a = marca_b = None
                if not xa and ma:
                    w = ma[i1 - 1] if i1 > 0 else ma[0]
                    x = w[2][2] + 1 if i1 > 0 else w[2][0] - 2
                    marca_a = (x - 0.75, w[2][1], x + 0.75, w[2][3])
                if not xb and mb:
                    w = mb[j1 - 1] if j1 > 0 else mb[0]
                    x = w[2][2] + 1 if j1 > 0 else w[2][0] - 2
                    marca_b = (x - 0.75, w[2][1], x + 0.75, w[2][3])
                ctx_src = mb if xb else ma
                k0, k1 = (j1, j2) if xb else (i1, i2)
                contexto = " ".join(w[0] for w in ctx_src[max(0, k0 - 5):k1 + 5])
                self.cambios.append(Cambio(
                    0, tipo, a, b, [_union(w[2] for w in xa)] if xa else [],
                    [_union(w[2] for w in xb)] if xb else [], " ".join(w[0] for w in xa),
                    " ".join(w[0] for w in xb), contexto, marca_a=marca_a, marca_b=marca_b, zona="margen"))

    # ------------------------------------------------------------------ apariencia
    def comparar_apariencia(self):
        if not self.op.apariencia:
            return
        trabajo = [(a, b) for a, b in self.pares if a is not None and b is not None] + list(self.movidas)
        n = len(trabajo)
        rects_texto_a: dict[int, list] = defaultdict(list)
        rects_texto_b: dict[int, list] = defaultdict(list)
        for c in self.cambios:
            if c.tipo in TIPOS_PAGINA:
                continue
            if c.pagina_a is not None:
                rects_texto_a[c.pagina_a] += c.rects_a + ([c.marca_a] if c.marca_a else [])
            if c.pagina_b is not None:
                rects_texto_b[c.pagina_b] += c.rects_b + ([c.marca_b] if c.marca_b else [])
        tamanos: list[int] = []
        for k, (a, b) in enumerate(trabajo):
            core._report(self.progress, k, n, f"Comparando la apariencia: página {b + 1} de {len(self.pb)}")
            try:
                regiones, distinto = self._apariencia_par(a, b, rects_texto_a[a], rects_texto_b[b])
            except core.UserError:
                raise
            except Exception:  # noqa: BLE001 — una página que no se puede dibujar no detiene la comparación
                self.avisos.append(f"No se pudo dibujar la página {b + 1} de «{self.nombre_b}» para comparar su "
                                   f"apariencia.")
                continue
            if distinto:
                tamanos.append(b + 1)
            for r, desc in regiones:
                self.cambios.append(Cambio(0, "apariencia", a, b, [r], [r], "", "", "", descripcion=desc))
        core._report(self.progress, n, n, "Comparando la apariencia…")
        if tamanos:
            if len(tamanos) == 1:
                self.avisos.append(f"La página {tamanos[0]} tiene otro tamaño en la versión nueva: su apariencia "
                                   f"se comparó solo en la zona común.")
            else:
                self.avisos.append(f"Las páginas {lista_paginas(tamanos)} tienen otro tamaño en la versión nueva: "
                                   f"su apariencia se comparó solo en la zona común.")

    def _apariencia_par(self, a: int, b: int, texto_a: list, texto_b: list):
        pa_, pb_ = self.pa[a], self.pb[b]
        ppp = max(36, min(int(self.op.ppp), 200))
        escala = ppp / 72
        wa_, ha_, sa = _gris(self.da[a], ppp)
        wb_, hb_, sb = _gris(self.db[b], ppp)
        distinto = abs(pa_.ancho - pb_.ancho) > 1 or abs(pa_.alto - pb_.alto) > 1
        wc, hc = min(wa_, wb_), min(ha_, hb_)
        if (wa_, ha_) != (wc, hc):
            sa = _recortar(wa_, ha_, sa, wc, hc)
        if (wb_, hb_) != (wc, hc):
            sb = _recortar(wb_, hb_, sb, wc, hc)
        d = _mapa(sa, sb, self.op.tolerancia)
        if d is None:
            return [], distinto
        # Lo que ya explica el texto no se compara aquí: las palabras cambiadas, las que se corrieron de
        # sitio y los encabezados y pies (que se comparan aparte).
        estables, movidas = self._palabras(a, b)
        tapar = list(texto_a) + list(texto_b) + movidas
        tapar += [w[2] for w in pa_.palabras_margen] + [w[2] for w in pb_.palabras_margen]
        if movidas:              # el texto se corrió: las imágenes y trazos que se corrieron con él, también
            tapar += _objetos_movidos(_objetos(self.da[a]), _objetos(self.db[b]))
        _borrar(d, wc, hc, tapar, escala)
        if d.find(1) < 0:
            return [], distinto
        regiones = _regiones(d, wc, hc, escala)
        out = []
        explicados = [(s[0] - 2, s[1] - 2, s[2] + 2, s[3] + 2) for s in list(texto_a) + list(texto_b)]
        for r in regiones:
            # lo que cae sobre un cambio de texto ya encontrado no se lista otra vez (su interior ya se
            # descartó; aquí se van los bordes que quedan alrededor)
            if explicados and sum(_inter(r, s) for s in explicados) >= 0.6 * _area(r):
                continue
            out.append((r, self._describir(a, b, r, estables)))
        return out, distinto

    def _palabras(self, a: int, b: int) -> tuple[list, list]:
        """Rectángulos de las palabras del par que siguen en el mismo sitio y de las que se movieron."""
        estables, movidas = [], []
        wa, wb = self.wa, self.wb
        pa_, pb_ = self.pa[a], self.pb[b]
        # (una palabra que solo cambió de mayúsculas, con «Ignorar mayúsculas», tampoco cuenta como formato)
        # Se juntan por renglón: muchos menos rectángulos que borrar del mapa de diferencias.
        est: dict[int, list] = defaultdict(list)
        mov: dict[tuple, list] = defaultdict(list)
        for i in range(pa_.w0, pa_.w1):
            j = self.par_a[i]
            if j >= 0 and wb[j].pagina == b and _mismo_sitio(wa[i].rect, wb[j].rect) \
                    and wa[i].texto == wb[j].texto:
                est[wa[i].linea].append(wa[i].rect)
            else:
                mov[("a", wa[i].linea)].append(wa[i].rect)
        for j in range(pb_.w0, pb_.w1):
            i = self.par_b[j]
            if not (i >= 0 and wa[i].pagina == a and _mismo_sitio(wa[i].rect, wb[j].rect)
                    and wa[i].texto == wb[j].texto):
                mov[("b", wb[j].linea)].append(wb[j].rect)
        estables = [_union(rs) for rs in est.values()]
        movidas = [_union(rs) for rs in mov.values()]
        return estables, movidas

    def _describir(self, a: int, b: int, r, estables: list) -> str:
        pa_, pb_ = self.pa[a], self.pb[b]
        if pa_.escaneada or pb_.escaneada:
            # escaneos: ¿hay texto digital (un sello, una firma escrita) en un lado y no en el otro?
            ta = _texto_en(self.da[a], r)
            tb = _texto_en(self.db[b], r)
            if tb and not ta:
                return f"Sello o texto agregado sobre la imagen: «{_corto(tb, 50)}»"
            if ta and not tb:
                return f"Sello o texto quitado de la imagen: «{_corto(ta, 50)}»"
            return DESC_IMAGEN
        if estables:
            cubierto = sum(_inter(r, s) for s in estables)
            if cubierto >= 0.4 * _area(r):
                return DESC_FORMATO
        return DESC_IMAGEN

    # ------------------------------------------------------------------ cierre
    def ordenar(self):
        fa, fb = {}, {}
        for k, (a, b) in enumerate(self.pares):
            if a is not None:
                fa[a] = k
            if b is not None:
                fb[b] = k

        def clave(c: Cambio):
            if c.tipo in ("quitado", "pagina_quitada") or c.pagina_b is None:
                fila = fa.get(c.pagina_a, 0)
                r = (c.rects_a or [c.marca_a or (0, 0, 0, 0)])[0]
            else:
                fila = fb.get(c.pagina_b, 0)
                r = (c.rects_b or [c.marca_b or (0, 0, 0, 0)])[0]
            pagina = 0 if c.tipo in TIPOS_PAGINA else 1
            return (fila, pagina, round(r[1] / 4), r[0])

        self.cambios.sort(key=clave)
        for k, c in enumerate(self.cambios, 1):
            c.id = k


def _corregir_corrimientos(ops: list[tuple], sa: list[str], sb: list[str]) -> list[tuple]:
    """Con páginas que se ven iguales entre sí (dos caras de un escaneo, páginas repetidas), la alineación
    puede correr una pareja: «agregada, igual, quitada» en lugar de dos parejas en su sitio.  Si emparejar
    en su sitio da tantas páginas iguales como antes, se prefiere eso («zip»)."""
    ops = list(ops)
    out: list[tuple] = []
    k = 0
    while k < len(ops):
        if k + 2 < len(ops) and ops[k + 1][0] == "equal" and {ops[k][0], ops[k + 2][0]} == {"insert", "delete"}:
            t0, t1, t2 = ops[k], ops[k + 1], ops[k + 2]
            largo0 = (t0[2] - t0[1]) or (t0[4] - t0[3])
            largo2 = (t2[2] - t2[1]) or (t2[4] - t2[3])
            if largo0 == largo2:
                i1, j1 = t0[1], t0[3]
                n = (t1[2] - t1[1]) + largo0
                iguales = sum(1 for d in range(n) if sa[i1 + d] == sb[j1 + d])
                if iguales >= t1[2] - t1[1]:
                    out.append(("zip", i1, i1 + n, j1, j1 + n))
                    k += 3
                    continue
        out.append(ops[k])
        k += 1
    return out


def _mismo_sitio(r, s, tol: float = 1.0) -> bool:
    return abs(r[0] - s[0]) <= tol and abs(r[1] - s[1]) <= tol and abs(r[3] - s[3]) <= tol


def _texto_en(page: pymupdf.Page, r) -> str:
    try:
        clip = pymupdf.Rect(r) * page.derotation_matrix if page.rotation else pymupdf.Rect(r)
        return " ".join(w[4] for w in page.get_text("words", clip=clip))
    except Exception:  # noqa: BLE001
        return ""


def _numero_pagina(t: str, indice: int, total: int) -> str:
    """Cambia por «#» un número que es el de la página o el total de páginas."""
    def rep(m):
        v = int(m.group(0)) if len(m.group(0)) < 7 else -1
        return "#" if v in (indice + 1, total) else m.group(0)
    return _DIGITOS.sub(rep, t)


def _por_pagina(palabras: list[_Palabra], idx: list[int]) -> list[list[int]]:
    grupos: list[list[int]] = []
    for k in idx:
        if grupos and palabras[grupos[-1][-1]].pagina == palabras[k].pagina:
            grupos[-1].append(k)
        else:
            grupos.append([k])
    return grupos


def _contexto(palabras: list[_Palabra], i1: int, i2: int, n: int = 5) -> str:
    antes = " ".join(w.texto for w in palabras[max(0, i1 - n):i1])
    medio = " ".join(w.texto for w in palabras[i1:i2])
    if len(medio) > 120:
        medio = medio[:119] + "…"
    despues = " ".join(w.texto for w in palabras[i2:i2 + n])
    return (("…" + antes + " ") if antes else "") + medio + ((" " + despues + "…") if despues else "")


def _aviso_escaneadas(esc_a: list[int], esc_b: list[int]) -> str:
    cola = "Marca «Leer con OCR las páginas escaneadas» para comparar también su texto."
    if esc_a == esc_b or not esc_a or not esc_b:
        ps = esc_b or esc_a
        cual = "" if esc_a == esc_b else (" de la versión nueva" if esc_b else " de la versión anterior")
        if len(ps) == 1:
            return f"La página {ps[0]}{cual} está escaneada: solo se comparó su imagen. {cola}"
        return f"Las páginas {lista_paginas(ps)}{cual} están escaneadas: solo se comparó su imagen. {cola}"
    return (f"Hay páginas escaneadas (en la versión anterior: {lista_paginas(esc_a)}; en la nueva: "
            f"{lista_paginas(esc_b)}): solo se comparó su imagen. {cola}")


def comparar(path_a: str | Path, path_b: str | Path, opciones: Opciones | None = None,
             password_a: str | None = None, password_b: str | None = None, progress: Progress = None) -> Resultado:
    """Compara la versión anterior (A) con la nueva (B).  No modifica ninguno de los dos archivos."""
    op = opciones or Opciones()
    if not op.texto and not op.apariencia:
        raise core.UserError("Elige qué comparar: el texto, la apariencia o los dos.")
    inicio = time.perf_counter()
    nombre_a, nombre_b = Path(path_a).name, Path(path_b).name
    with core.open_pdf(path_a, password_a) as da, core.open_pdf(path_b, password_b) as db:
        if not da.page_count or not db.page_count:
            vacio = nombre_a if not da.page_count else nombre_b
            raise core.UserError(f"«{vacio}» no tiene páginas.")
        t = _Trabajo(da, db, op, progress, nombre_a, nombre_b)
        t.leer()
        t.comparar_texto()
        t.alinear_paginas()
        t.paginas_sueltas()
        t.cambios_de_texto()
        t.cambios_de_margen()
        t.comparar_apariencia()
        t.ordenar()
        resumen = {k: 0 for k in TIPOS}
        for c in t.cambios:
            resumen[c.tipo] += 1
        return Resultado(
            pares=t.pares, cambios=t.cambios, resumen=resumen, avisos=list(dict.fromkeys(t.avisos)),
            paginas_a=da.page_count, paginas_b=db.page_count, segundos=time.perf_counter() - inicio,
            tamanos_a=[(p.ancho, p.alto) for p in t.pa], tamanos_b=[(p.ancho, p.alto) for p in t.pb],
            escaneadas_a=[p.indice for p in t.pa if p.escaneada],
            escaneadas_b=[p.indice for p in t.pb if p.escaneada], opciones=op)


# --------------------------------------------------------------------------
# Informe en PDF
# --------------------------------------------------------------------------

_HOJA = (842.0, 595.0)          # A4 horizontal
_MARGEN = 28.0


def _rgb(hexa: str) -> tuple[float, float, float]:
    h = hexa.lstrip("#")
    return tuple(int(h[k:k + 2], 16) / 255 for k in (0, 2, 4))


def _fecha_archivo(path) -> str:
    try:
        return datetime.fromtimestamp(os.path.getmtime(path)).strftime("%d/%m/%Y %H:%M")
    except OSError:
        return "—"


def _escapar(t: str) -> str:
    return html.escape(t, quote=False)


_CSS = """
* { font-family: sans-serif; }
body { font-size: 10pt; color: #1F2328; }
h1 { font-size: 20pt; margin: 0 0 4pt 0; }
h2 { font-size: 12pt; margin: 14pt 0 4pt 0; }
p { margin: 2pt 0; }
.gris { color: #6B7280; }
table { border-collapse: collapse; width: 100%; }
td, th { border-bottom: 0.5pt solid #D5D8DD; padding: 3pt 4pt; vertical-align: top; text-align: left; }
th { color: #6B7280; font-size: 9pt; }
.aviso { background-color: #FFF7E0; color: #5A4300; padding: 4pt; margin: 2pt 0; }
"""


def _portada(out: pymupdf.Document, path_a, path_b, resultado: Resultado) -> None:
    w, h = _HOJA
    page = out.new_page(width=w, height=h)
    na, nb = Path(path_a).name, Path(path_b).name
    filas = "".join(
        f"<tr><td><span style='color:{COLORES[t]}'>&#9632;</span> {_escapar(NOMBRES[t])}</td>"
        f"<td>{resultado.resumen.get(t, 0)}</td></tr>" for t in TIPOS)
    avisos = "".join(f"<p class='aviso'>{_escapar(a)}</p>" for a in resultado.avisos[:8])
    if len(resultado.avisos) > 8:
        avisos += f"<p class='gris'>…y {len(resultado.avisos) - 8} avisos más.</p>"
    cuerpo = f"""
    <h1>Comparación de dos PDF</h1>
    <p class='gris'>Informe generado el {datetime.now().strftime('%d/%m/%Y a las %H:%M')} con RonyPDF.</p>
    <h2>Documentos</h2>
    <table>
      <tr><th></th><th>Archivo</th><th>Páginas</th><th>Modificado</th></tr>
      <tr><td>Versión anterior</td><td>{_escapar(na)}</td><td>{resultado.paginas_a}</td>
          <td>{_fecha_archivo(path_a)}</td></tr>
      <tr><td>Versión nueva</td><td>{_escapar(nb)}</td><td>{resultado.paginas_b}</td>
          <td>{_fecha_archivo(path_b)}</td></tr>
    </table>
    <h2>{_escapar(frase_resumen(resultado.resumen, resultado.opciones))}</h2>
    <table style='width:55%'><tr><th>Tipo de cambio</th><th>Cantidad</th></tr>{filas}</table>
    <h2>Cómo leer este informe</h2>
    <p>Cada hoja muestra la versión anterior a la izquierda y la nueva a la derecha. Los cambios están
    numerados y coloreados: <b style='color:{COLORES['quitado']}'>rojo</b> = quitado,
    <b style='color:{COLORES['agregado']}'>verde</b> = agregado, <b style='color:{COLORES['cambiado']}'>ámbar</b>
    = cambiado y <b style='color:{COLORES['apariencia']}'>azul</b> = apariencia (imágenes, gráficos, sellos o
    formato). Al final está la lista numerada de todos los cambios.</p>
    {('<h2>Avisos</h2>' + avisos) if avisos else ''}
    """
    page.insert_htmlbox(pymupdf.Rect(_MARGEN + 8, _MARGEN, w - _MARGEN - 8, h - _MARGEN), cuerpo, css=_CSS,
                        scale_low=0)


def _sin_giro(cache: dict, doc: pymupdf.Document, pno: int):
    """La página lista para ``show_pdf_page`` tal como se ve: si tiene /Rotate, una copia con el giro
    horneado (show_pdf_page no aplica el giro de la página de origen)."""
    page = doc[pno]
    if not page.rotation:
        return doc, pno
    if pno not in cache:
        tmp = pymupdf.open()
        tmp.insert_pdf(doc, from_page=pno, to_page=pno)
        tmp[0].remove_rotation()
        cache[pno] = tmp
    return cache[pno], 0


def _hoja_comparada(out: pymupdf.Document, da, db, cache_a, cache_b, fila: tuple, cambios: list[Cambio],
                    resultado: Resultado, nombre_a: str, nombre_b: str, movidas: dict) -> None:
    w, h = _HOJA
    page = out.new_page(width=w, height=h)
    a, b = fila
    gap = 18.0
    col_w = (w - 2 * _MARGEN - gap) / 2
    top = _MARGEN + 30
    alto = h - top - _MARGEN
    gris = (0.42, 0.45, 0.5)
    page.insert_text((_MARGEN, _MARGEN + 6), f"{_corto(nombre_a, 48)}  frente a  {_corto(nombre_b, 48)}",
                     fontsize=7.5, color=gris)
    destinos = {}
    for lado, doc, pno, x0, titulo, cache in (
            ("a", da, a, _MARGEN, "Versión anterior", cache_a),
            ("b", db, b, _MARGEN + col_w + gap, "Versión nueva", cache_b)):
        etiqueta = f"{titulo} · página {pno + 1}" if pno is not None else titulo
        page.insert_text((x0, top - 8), etiqueta, fontsize=10.5, color=(0.12, 0.14, 0.16))
        caja = pymupdf.Rect(x0, top, x0 + col_w, top + alto)
        page.draw_rect(caja, color=None, fill=(0.93, 0.94, 0.95), width=0)
        if pno is None:
            otro = b if lado == "a" else a
            texto = "(no existe en esta versión)"
            if lado == "a" and otro in movidas.get("b", {}):
                texto = f"(estaba en la página {movidas['b'][otro] + 1} de esta versión)"
            elif lado == "b" and otro in movidas.get("a", {}):
                texto = f"(pasó a la página {movidas['a'][otro] + 1} de esta versión)"
            page.insert_textbox(caja + (10, alto / 2 - 10, -10, 0), texto, fontsize=11, color=gris, align=1)
            continue
        src, sp = _sin_giro(cache, doc, pno)
        pw, ph = (resultado.tamanos_a if lado == "a" else resultado.tamanos_b)[pno]
        esc = min(col_w / max(pw, 1), alto / max(ph, 1))
        dx = x0 + (col_w - pw * esc) / 2
        dy = top + (alto - ph * esc) / 2
        destino = pymupdf.Rect(dx, dy, dx + pw * esc, dy + ph * esc)
        page.draw_rect(destino, color=None, fill=(1, 1, 1), width=0)
        try:
            page.show_pdf_page(destino, src, sp)
        except Exception:  # noqa: BLE001 — una página vacía o dañada: se deja en blanco
            pass
        page.draw_rect(destino, color=(0.8, 0.82, 0.85), width=0.5)
        destinos[lado] = (dx, dy, esc)
    # resaltados y números
    for c in cambios:
        color = _rgb(COLORES[c.tipo])
        for lado, rects, marca in (("a", c.rects_a, c.marca_a), ("b", c.rects_b, c.marca_b)):
            pno = a if lado == "a" else b
            if lado not in destinos or (c.pagina_a if lado == "a" else c.pagina_b) != pno:
                continue
            dx, dy, esc = destinos[lado]

            def tr(r):
                return pymupdf.Rect(dx + r[0] * esc, dy + r[1] * esc, dx + r[2] * esc, dy + r[3] * esc)
            primero = None
            if c.tipo in TIPOS_PAGINA:
                for r in rects:
                    q = tr(r)
                    page.draw_rect(q, color=color, width=2.2)
                    primero = primero or q
            elif c.tipo == "apariencia":
                for r in rects:
                    q = tr(r) + (-1, -1, 1, 1)
                    page.draw_rect(q, color=color, fill=color, width=1, fill_opacity=0.10, stroke_opacity=0.9)
                    primero = primero or q
            else:
                for r in rects:
                    q = tr(r) + (-0.8, -0.8, 0.8, 0.8)
                    page.draw_rect(q, color=color, fill=color, width=0.6, fill_opacity=0.28, stroke_opacity=0.85)
                    primero = primero or q
                if marca and not rects:
                    q = tr(marca)
                    x = (q.x0 + q.x1) / 2
                    page.draw_line((x, q.y0 - 1), (x, q.y1 + 1), color=color, width=1.4)
                    page.draw_polyline([(x - 2.5, q.y0 - 3.5), (x + 2.5, q.y0 - 3.5), (x, q.y0 - 0.5)],
                                       color=color, fill=color, width=0.5, closePath=True)
                    primero = q
            if primero is not None:
                _numero(page, primero, c.id, color)


def _numero(page: pymupdf.Page, r: pymupdf.Rect, n: int, color) -> None:
    texto = str(n)
    ancho = 4 + 4.2 * len(texto)
    x0 = max(2.0, r.x0 - 2)
    y0 = max(2.0, r.y0 - 9.5)
    caja = pymupdf.Rect(x0, y0, x0 + ancho, y0 + 9)
    page.draw_rect(caja, color=color, fill=color, width=0.5, radius=0.3)
    page.insert_text((x0 + 2, y0 + 7), texto, fontsize=7, color=(1, 1, 1))


def _lista_html(resultado: Resultado) -> str:
    filas = []
    for c in resultado.cambios:
        a = str(c.pagina_a + 1) if c.pagina_a is not None else "—"
        b = str(c.pagina_b + 1) if c.pagina_b is not None else "—"
        tipo = NOMBRES[c.tipo] + (" (encabezado o pie)" if c.zona == "margen" else "")
        if c.tipo == "cambiado":
            que = f"«{_escapar(_corto(c.antes, 160))}» → «{_escapar(_corto(c.despues, 160))}»"
        elif c.tipo == "quitado":
            que = f"«{_escapar(_corto(c.antes, 320))}»"
        elif c.tipo == "agregado":
            que = f"«{_escapar(_corto(c.despues, 320))}»"
        else:
            que = _escapar(c.descripcion)
        filas.append(f"<tr><td><b style='color:{COLORES[c.tipo]}'>{c.id}</b></td><td>{a} → {b}</td>"
                     f"<td style='color:{COLORES[c.tipo]}'>{_escapar(tipo)}</td><td>{que}</td></tr>")
    return (f"<h2>Lista de cambios</h2><p class='gris'>{_escapar(frase_resumen(resultado.resumen, resultado.opciones))}"
            f"</p><table><tr><th>N.°</th><th>Página</th><th>Tipo</th><th>Antes → después</th></tr>"
            + "".join(filas) + "</table>")


def _paginas_html(cuerpo: str) -> pymupdf.Document:
    """Escribe HTML en hojas A4 horizontales, con tantas páginas como haga falta."""
    w, h = _HOJA
    buf = io.BytesIO()
    writer = pymupdf.DocumentWriter(buf)
    story = pymupdf.Story(html=cuerpo, user_css=_CSS)
    donde = pymupdf.Rect(_MARGEN + 8, _MARGEN, w - _MARGEN - 8, h - _MARGEN)
    mas = True
    while mas:
        dev = writer.begin_page(pymupdf.Rect(0, 0, w, h))
        mas, _ = story.place(donde)
        story.draw(dev)
        writer.end_page()
    writer.close()
    return pymupdf.open("pdf", buf.getvalue())


def informe_pdf(path_a: str | Path, path_b: str | Path, resultado: Resultado, output: str | Path,
                password_a: str | None = None, password_b: str | None = None, solo_con_cambios: bool = True,
                proteger_con: str | None = None, progress: Progress = None) -> int:
    """Informe con portada, las dos versiones enfrentadas (con los cambios numerados) y la lista.

    Devuelve el número de páginas.  Con ``proteger_con`` el informe queda cifrado (AES-256) con esa
    contraseña.  Se escribe de forma atómica."""
    nombre_a, nombre_b = Path(path_a).name, Path(path_b).name
    por_fila = cambios_por_fila(resultado)
    movidas = {"a": {}, "b": {}}
    for c in resultado.cambios:
        if c.tipo == "pagina_movida":
            movidas["a"][c.pagina_a] = c.pagina_b
            movidas["b"][c.pagina_b] = c.pagina_a
    filas = [k for k in range(len(resultado.pares)) if not solo_con_cambios or por_fila.get(k)]
    cache_a: dict = {}
    cache_b: dict = {}
    try:
        with core.open_pdf(path_a, password_a) as da, core.open_pdf(path_b, password_b) as db, \
                pymupdf.open() as out:
            core._report(progress, 0, len(filas) + 2, "Preparando la portada…")
            _portada(out, path_a, path_b, resultado)
            for k, fila in enumerate(filas):
                core._report(progress, k + 1, len(filas) + 2, f"Armando la hoja {k + 1} de {len(filas)}…")
                _hoja_comparada(out, da, db, cache_a, cache_b, resultado.pares[fila], por_fila.get(fila, []),
                                resultado, nombre_a, nombre_b, movidas)
            if resultado.cambios:
                core._report(progress, len(filas) + 1, len(filas) + 2, "Escribiendo la lista de cambios…")
                with _paginas_html(_lista_html(resultado)) as lista:
                    out.insert_pdf(lista)
            out.set_metadata({"title": f"Comparación: {nombre_a} frente a {nombre_b}", "creator": "RonyPDF",
                              "producer": "RonyPDF"})
            core._report(progress, len(filas) + 2, len(filas) + 2, "Guardando…")
            opts = dict(garbage=3, deflate=True)
            if proteger_con:
                opts.update(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=proteger_con,
                            owner_pw=secrets.token_urlsafe(24),
                            permissions=pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_COPY | pymupdf.PDF_PERM_ACCESSIBILITY)
            with core._output(output) as tmp:
                out.save(tmp, **opts)
            return out.page_count
    finally:
        for d in list(cache_a.values()) + list(cache_b.values()):
            d.close()


# --------------------------------------------------------------------------
# Lista en Excel
# --------------------------------------------------------------------------

def informe_xlsx(resultado: Resultado, output: str | Path, nombre_a: str, nombre_b: str) -> None:
    """Lista de cambios en Excel: una fila por cambio (y una hoja «Resumen»)."""
    from . import oficina
    filas: list[list[object]] = [["N.°", "Tipo", "Página anterior", "Página nueva", "Antes", "Después"]]
    for c in resultado.cambios:
        tipo = NOMBRES[c.tipo] + (" (encabezado o pie)" if c.zona == "margen" else "")
        antes, despues = c.antes, c.despues
        if c.tipo in ("apariencia",) + TIPOS_PAGINA:
            despues = c.descripcion
        filas.append([c.id, tipo, c.pagina_a + 1 if c.pagina_a is not None else "",
                      c.pagina_b + 1 if c.pagina_b is not None else "", antes, despues])
    resumen: list[list[object]] = [
        ["Comparación de dos PDF", ""],
        ["Versión anterior", nombre_a], ["Páginas", resultado.paginas_a],
        ["Versión nueva", nombre_b], ["Páginas", resultado.paginas_b],
        ["", ""], ["Tipo de cambio", "Cantidad"],
    ] + [[NOMBRES[t], resultado.resumen.get(t, 0)] for t in TIPOS] + [["Total", len(resultado.cambios)]]
    if resultado.avisos:
        resumen += [["", ""], ["Avisos", ""]] + [[a, ""] for a in resultado.avisos]
    oficina.escribir_xlsx([("Cambios", filas), ("Resumen", resumen)], output)
