"""Operaciones PDF sin interfaz gráfica.

Todas las funciones públicas trabajan con rutas de archivo y aceptan un
callback opcional ``progress(actual, total, mensaje)``.  Los errores
pensados para mostrarse al usuario se lanzan como ``UserError``.
"""

from __future__ import annotations

import datetime as _dt
import math
import os
import re
import secrets
import shutil
import sys
from collections import defaultdict
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, Sequence

import pymupdf

pymupdf.TOOLS.mupdf_display_errors(False)
pymupdf.TOOLS.mupdf_display_warnings(False)

Progress = Callable[[int, int, str], None] | None

PDF_EXTS = {".pdf"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tif", ".tiff",
              ".jxr", ".jpx", ".jp2", ".pnm", ".pbm", ".pgm", ".ppm", ".psd"}

# Tamaños en puntos (1 pt = 1/72 pulgada)
PAGE_SIZES = {
    "A4": (595.28, 841.89),
    "Carta": (612.0, 792.0),
    "Oficio": (612.0, 1008.0),
    "A3": (841.89, 1190.55),
    "A5": (419.53, 595.28),
}

SAVE_OPTS = dict(garbage=3, deflate=True, use_objstms=1)


class UserError(Exception):
    """Error con un mensaje apto para el usuario."""


class PasswordError(UserError):
    pass


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------

def _report(progress: Progress, i: int, n: int, msg: str = "") -> None:
    if progress:
        progress(i, n, msg)


def human_size(n: float) -> str:
    """Tamaño legible con coma decimal, como pide el sistema de diseño: «859 B», «2,8 KB», «1,2 MB»."""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}".replace(".", ",")
        n /= 1024
    return f"{n:.1f} GB".replace(".", ",")


def safe_filename(name: str, fallback: str = "documento") -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name[:120] or fallback


def unique_path(path: str | Path) -> Path:
    """Devuelve ``path`` o una variante «nombre (2).ext» que no exista."""
    p = Path(path)
    if not p.exists():
        return p
    k = 2
    while True:
        cand = p.with_name(f"{p.stem} ({k}){p.suffix}")
        if not cand.exists():
            return cand
        k += 1


@contextmanager
def _output(path: str | Path) -> Iterator[str]:
    """Entrega una ruta temporal y, si todo sale bien, la mueve al destino.

    Permite sobrescribir el archivo de origen: el reemplazo ocurre cuando ya
    se cerraron los documentos abiertos dentro del bloque ``with``.  Antes de
    reemplazar, el temporal se vacía al disco (``os.fsync``): si se va la luz
    justo después, queda el archivo nuevo completo o el anterior, nunca uno a
    medias.  Si algo falla, el destino queda como estaba y el temporal se borra.
    """
    final = Path(path)
    final.parent.mkdir(parents=True, exist_ok=True)
    tmp = final.with_name(f".~{final.stem[:80]}.{os.getpid()}.{secrets.token_hex(3)}.tmp")
    try:
        yield str(tmp)
        try:
            if tmp.exists():
                fd = os.open(tmp, os.O_RDWR | getattr(os, "O_BINARY", 0))
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            os.replace(tmp, final)
        except PermissionError as exc:
            raise UserError(
                f"No se pudo escribir «{final.name}». ¿Está abierto en otro programa?"
            ) from exc
        except OSError as exc:
            if getattr(exc, "errno", None) == 28:          # ENOSPC: el disco se llenó
                raise UserError(f"No hay espacio suficiente en el disco para guardar «{final.name}».") from exc
            raise
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def is_image(path: str | Path) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXTS


def needs_password(path: str | Path) -> bool:
    try:
        with pymupdf.open(path) as doc:
            return bool(doc.needs_pass)
    except Exception:
        return False


def check_password(path: str | Path, password: str) -> bool:
    with pymupdf.open(path) as doc:
        return not doc.needs_pass or bool(doc.authenticate(password))


def open_pdf(path: str | Path, password: str | None = None) -> pymupdf.Document:
    """Abre un PDF (o una imagen, convertida a PDF de una página)."""
    path = str(path)
    if not os.path.isfile(path):
        raise UserError(f"No se encontró el archivo «{path}».")
    try:
        if is_image(path):
            with pymupdf.open(path) as img:
                data = img.convert_to_pdf()
            return pymupdf.open("pdf", data)
        doc = pymupdf.open(path)
    except UserError:
        raise
    except Exception as exc:
        raise UserError(f"No se pudo abrir «{Path(path).name}»: no parece un PDF válido.") from exc
    if not doc.is_pdf:
        try:
            data = doc.convert_to_pdf()
        finally:
            doc.close()
        return pymupdf.open("pdf", data)
    if doc.needs_pass:
        if not password or not doc.authenticate(password):
            doc.close()
            raise PasswordError(f"«{Path(path).name}» está protegido y la contraseña no es correcta.")
    return doc


def fecha_pdf_ahora() -> str:
    """Fecha y hora actuales en el formato de fechas de PDF, con el desfase horario vigente
    («D:20250312154200-05'00'»).  (``pymupdf.get_pdf_now`` usa el del horario de verano aunque no rija.)"""
    ahora = _dt.datetime.now().astimezone()
    minutos = int((ahora.utcoffset() or _dt.timedelta(0)).total_seconds() // 60)
    signo = "+" if minutos >= 0 else "-"
    minutos = abs(minutos)
    return ahora.strftime("D:%Y%m%d%H%M%S") + f"{signo}{minutos // 60:02d}'{minutos % 60:02d}'"


_SIN_COPIA = ("Link", "Popup", "Widget")      # lo que insert_pdf no copia como anotación de la página


def _reponer_hilos(out: pymupdf.Document, src: pymupdf.Document, desde: int, hasta: int, primera: int) -> None:
    """``insert_pdf`` (PyMuPDF) no copia las anotaciones que responden a otra (las que llevan /IRT): las
    respuestas y los cambios de estado de los comentarios se perderían al organizar, unir o extraer páginas.
    Aquí se copian con el mismo mapa de injertos que usó ``insert_pdf``, así su /IRT apunta a la copia de su
    comentario.  Solo las que responden a una anotación que sí se copió (o a otra respuesta ya copiada).
    Si algo falla, el documento queda como lo dejó ``insert_pdf``: nunca impide guardar."""
    try:
        gmap = out.Graftmaps.get(src._graft_id)
        if gmap is None:
            return
        m = pymupdf.mupdf
        psrc = pymupdf._as_pdf_document(src)
        pout = pymupdf._as_pdf_document(out)
        for k, pno in enumerate(range(desde, hasta + 1)):
            anots = m.pdf_dict_gets(m.pdf_lookup_page_obj(psrc, pno), "Annots")
            n = m.pdf_array_len(anots) if anots.m_internal else 0
            copiadas, respuestas = set(), []
            for i in range(n):
                o = m.pdf_array_get(anots, i)
                if not o.m_internal or not m.pdf_is_dict(o):
                    continue
                if m.pdf_dict_gets(o, "IRT").m_internal:
                    respuestas.append(o)
                elif m.pdf_to_name(m.pdf_dict_gets(o, "Subtype")) not in _SIN_COPIA:
                    copiadas.add(m.pdf_to_num(o))
            if not respuestas:
                continue
            destino = m.pdf_lookup_page_obj(pout, primera + k)
            lista = m.pdf_dict_gets(destino, "Annots")
            if not lista.m_internal or not m.pdf_is_array(lista):
                lista = m.pdf_new_array(pout, len(respuestas))
                m.pdf_dict_puts(destino, "Annots", lista)
            ya = {m.pdf_to_num(m.pdf_array_get(lista, j)) for j in range(m.pdf_array_len(lista))}
            avanzo = True
            while respuestas and avanzo:           # una respuesta a otra respuesta va después de su padre
                avanzo = False
                for o in list(respuestas):
                    if m.pdf_to_num(m.pdf_dict_gets(o, "IRT")) not in copiadas:
                        continue
                    respuestas.remove(o)
                    m.pdf_dict_dels(o, "P")        # como insert_pdf: sin página ni ventana emergente propias
                    m.pdf_dict_dels(o, "Popup")
                    copia = m.pdf_graft_mapped_object(gmap.this, o)
                    num = m.pdf_to_num(copia)
                    if num not in ya:
                        m.pdf_array_push(lista, m.pdf_new_indirect(pout, num, 0))
                        ya.add(num)
                    copiadas.add(m.pdf_to_num(o))
                    avanzo = True
        out._reset_page_refs()
    except Exception:  # noqa: BLE001 — sin las respuestas, pero con el documento completo
        pass


def insert_pages(out: pymupdf.Document, src: pymupdf.Document, desde: int, hasta: int) -> None:
    """Agrega al final de ``out`` las páginas ``desde``..``hasta`` de ``src`` con sus comentarios completos
    (también las respuestas y los estados, que ``insert_pdf`` deja por fuera)."""
    primera = out.page_count
    out.insert_pdf(src, from_page=desde, to_page=hasta, final=False)
    _reponer_hilos(out, src, desde, hasta, primera)


def _page_groups(n: int, indices: Sequence[int]) -> list[tuple[int, int]]:
    """Agrupa índices en tramos contiguos (ascendentes) para insert_pdf."""
    runs: list[tuple[int, int]] = []
    for i in indices:
        if runs and i == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], i)
        else:
            runs.append((i, i))
    return runs


def _copy_pages(out: pymupdf.Document, src: pymupdf.Document, indices: Sequence[int]) -> None:
    for a, b in _page_groups(src.page_count, indices):
        insert_pages(out, src, a, b)


def is_password_protected(doc: pymupdf.Document) -> bool:
    """¿El documento está cifrado?  Seguro para un documento ya autenticado.

    No usa ``doc.needs_pass``: en MuPDF 1.28 esa consulta vuelve a probar la contraseña
    vacía e invalida la clave ya aceptada, y al guardar los flujos que aún no se habían
    leído (páginas, imágenes, adjuntos) salen vacíos.
    """
    try:
        return bool((doc.metadata or {}).get("encryption"))
    except Exception:  # noqa: BLE001
        return False


def _save_keeping_password(doc: pymupdf.Document, target: str, password: str | None, incremental: bool = False,
                           **opts) -> None:
    """Guarda; si el original pedía contraseña, el resultado la sigue pidiendo.

    Con ``incremental=True`` (el documento debe haberse abierto desde ``target``) solo se agregan al final
    del archivo los cambios: los bytes anteriores quedan intactos, el cifrado se conserva tal cual y una
    firma digital sigue cubriendo la versión que firmó."""
    if incremental:
        doc.save(target, incremental=True, encryption=pymupdf.PDF_ENCRYPT_KEEP)
        return
    opts = {**SAVE_OPTS, **opts}
    # Ojo: aquí no se puede leer doc.needs_pass (ver is_password_protected).
    if password and is_password_protected(doc):
        opts.update(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=password,
                    owner_pw=secrets.token_urlsafe(24), permissions=doc.permissions)
    doc.save(target, **opts)


def _copy_metadata(src: pymupdf.Document, dst: pymupdf.Document) -> None:
    keys = ("title", "author", "subject", "keywords", "creator", "producer")
    meta = {k: v for k, v in (src.metadata or {}).items() if k in keys and v}
    if meta:
        dst.set_metadata(meta)


# --------------------------------------------------------------------------
# Rangos de páginas
# --------------------------------------------------------------------------

_LAST_WORDS = re.compile(r"\b(fin|final|ultima|última|z|n)\b", re.IGNORECASE)
_RANGE_RE = re.compile(r"^\s*(\d*)\s*(-)?\s*(\d*)\s*$")
_ALL_WORDS = {"", "todas", "todo", "all", "*"}


def _parse_part(part: str, n: int) -> list[int]:
    raw = part.strip()
    p = raw.replace("–", "-").replace("—", "-").replace(" a ", "-")
    p = _LAST_WORDS.sub(str(n), p)
    m = _RANGE_RE.match(p)
    if not m or (not m.group(1) and not m.group(3)):
        raise UserError(f"No entiendo el rango «{raw}». Usa algo como 1-3, 5, 8-")
    a, dash, b = m.groups()
    if dash:
        start = int(a) if a else 1
        end = int(b) if b else n
    else:
        start = end = int(a)
    for v in (start, end):
        if v < 1 or v > n:
            raise UserError(f"La página {v} no existe (el documento tiene {n} páginas).")
    step = 1 if end >= start else -1
    return list(range(start - 1, end - 1 + step, step))


def parse_pages(spec: str | None, page_count: int) -> list[int]:
    """«1-3, 5, 8-» → índices base 0.  Vacío o «todas» → todas las páginas."""
    spec = (spec or "").strip()
    if spec.lower() in _ALL_WORDS:
        return list(range(page_count))
    out: list[int] = []
    for part in re.split(r"[,;]", spec):
        if part.strip():
            out.extend(_parse_part(part, page_count))
    if not out:
        raise UserError("No se seleccionó ninguna página.")
    return out


def parse_groups(spec: str, page_count: int) -> list[list[int]]:
    """Cada elemento separado por comas es un grupo (un archivo)."""
    groups = [_parse_part(p, page_count) for p in re.split(r"[,;]", spec or "") if p.strip()]
    if not groups:
        raise UserError("Escribe al menos un rango, por ejemplo: 1-3, 4-10")
    return groups


# --------------------------------------------------------------------------
# Información
# --------------------------------------------------------------------------

@dataclass
class PdfInfo:
    path: str
    pages: int
    size: int
    encrypted: bool
    title: str
    author: str
    has_toc: bool
    first_page_size: tuple[float, float]


def pdf_info(path: str | Path, password: str | None = None) -> PdfInfo:
    with open_pdf(path, password) as doc:
        meta = doc.metadata or {}
        try:
            toc = doc.get_toc(simple=True)
        except Exception:
            toc = []
        r = doc[0].rect if doc.page_count else pymupdf.Rect(0, 0, 0, 0)
        return PdfInfo(
            path=str(path),
            pages=doc.page_count,
            size=os.path.getsize(path),
            # «encryption» también detecta PDF que abren sin clave pero tienen restricciones.
            # No se lee doc.needs_pass: tras autenticar invalidaría la clave (ver is_password_protected).
            encrypted=bool(meta.get("encryption")),
            title=meta.get("title", "") or "",
            author=meta.get("author", "") or "",
            has_toc=any(level == 1 for level, *_ in toc),
            first_page_size=(r.width, r.height),
        )


def page_sizes(path: str | Path, password: str | None = None) -> list[tuple[float, float]]:
    with open_pdf(path, password) as doc:
        return [(p.rect.width, p.rect.height) for p in doc]


def render_page(doc: pymupdf.Document, index: int, max_px: int = 300,
                rotate: int = 0) -> tuple[int, int, int, bytes]:
    """Renderiza una página para miniatura → (ancho, alto, stride, RGB)."""
    page = doc[index]
    r = page.rect
    zoom = max_px / max(r.width, r.height, 1)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom).prerotate(rotate), alpha=False)
    return pix.width, pix.height, pix.stride, bytes(pix.samples)


# --------------------------------------------------------------------------
# Unir
# --------------------------------------------------------------------------

@dataclass
class MergeItem:
    path: str
    pages: str = ""
    password: str | None = None


def merge(items: Sequence[MergeItem], output: str | Path, bookmarks: bool = True,
          progress: Progress = None) -> int:
    if not items:
        raise UserError("Agrega al menos un archivo.")
    with _output(output) as tmp, pymupdf.open() as out:
        toc = []
        for i, it in enumerate(items):
            name = Path(it.path).name
            _report(progress, i, len(items), f"Agregando {name}")
            with open_pdf(it.path, it.password) as src:
                try:
                    idx = parse_pages(it.pages, src.page_count)
                except UserError as exc:
                    raise UserError(f"{name}: {exc}") from exc
                start = out.page_count
                _copy_pages(out, src, idx)
                if i == 0:
                    _copy_metadata(src, out)
                if bookmarks:
                    toc.append([1, Path(it.path).stem, start + 1])
        if out.page_count == 0:
            raise UserError("El resultado no tiene páginas.")
        if bookmarks:
            out.set_toc(toc)
        _report(progress, len(items), len(items), "Guardando…")
        out.save(tmp, **SAVE_OPTS)
        return out.page_count


# --------------------------------------------------------------------------
# Dividir / extraer
# --------------------------------------------------------------------------

def _write_groups(path: str | Path, password: str | None, groups: list[tuple[str, list[int]]],
                  out_dir: str | Path, progress: Progress) -> list[str]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    with open_pdf(path, password) as src:
        for k, (name, idx) in enumerate(groups):
            _report(progress, k, len(groups), f"Creando {name}")
            target = unique_path(out_dir / name)
            with pymupdf.open() as out:
                _copy_pages(out, src, idx)
                _copy_metadata(src, out)
                with _output(target) as tmp:
                    out.save(tmp, **SAVE_OPTS)
            written.append(str(target))
    _report(progress, len(groups), len(groups), "Listo")
    return written


def _label(idx: list[int]) -> str:
    a, b = idx[0] + 1, idx[-1] + 1
    return f"{a}" if a == b and len(idx) == 1 else f"{a}-{b}"


def split_every(path: str | Path, n: int, out_dir: str | Path, password: str | None = None,
                progress: Progress = None) -> list[str]:
    if n < 1:
        raise UserError("El número de páginas por archivo debe ser al menos 1.")
    stem = Path(path).stem
    with open_pdf(path, password) as doc:
        count = doc.page_count
    groups = []
    for start in range(0, count, n):
        idx = list(range(start, min(start + n, count)))
        groups.append((f"{stem}_{_label(idx)}.pdf", idx))
    return _write_groups(path, password, groups, out_dir, progress)


def split_ranges(path: str | Path, spec: str, out_dir: str | Path, password: str | None = None,
                 progress: Progress = None) -> list[str]:
    stem = Path(path).stem
    with open_pdf(path, password) as doc:
        count = doc.page_count
    groups = [(f"{stem}_{_label(g)}.pdf", g) for g in parse_groups(spec, count)]
    return _write_groups(path, password, groups, out_dir, progress)


def split_bookmarks(path: str | Path, out_dir: str | Path, password: str | None = None,
                    progress: Progress = None) -> list[str]:
    with open_pdf(path, password) as doc:
        count = doc.page_count
        toc = [(t, p) for lvl, t, p in doc.get_toc(simple=True) if lvl == 1 and p >= 1]
    if not toc:
        raise UserError("Este PDF no tiene marcadores (índice) de primer nivel.")
    toc.sort(key=lambda x: x[1])
    groups = []
    if toc[0][1] > 1:
        groups.append(("00 - Inicio.pdf", list(range(0, toc[0][1] - 1))))
    for k, (title, page) in enumerate(toc):
        end = toc[k + 1][1] - 1 if k + 1 < len(toc) else count
        if end < page:
            continue
        groups.append((f"{k + 1:02d} - {safe_filename(title)}.pdf", list(range(page - 1, end))))
    return _write_groups(path, password, groups, out_dir, progress)


def extract_pages(path: str | Path, spec: str, output: str | Path, password: str | None = None,
                  progress: Progress = None) -> int:
    with _output(output) as tmp, open_pdf(path, password) as src, pymupdf.open() as out:
        idx = parse_pages(spec, src.page_count)
        _report(progress, 0, 1, "Extrayendo páginas")
        _copy_pages(out, src, idx)
        _copy_metadata(src, out)
        out.save(tmp, **SAVE_OPTS)
        return out.page_count


# --------------------------------------------------------------------------
# Organizar (reordenar, rotar, eliminar, duplicar, insertar)
# --------------------------------------------------------------------------

@dataclass
class PageRef:
    source: int | None          # índice en la lista de fuentes; None = página en blanco
    page: int = 0
    rotate: int = 0             # rotación adicional (múltiplo de 90)
    width: float = 595.28       # solo para páginas en blanco
    height: float = 841.89


def build_document(sources: Sequence[tuple[str, str | None]], pages: Sequence[PageRef],
                   output: str | Path, progress: Progress = None) -> int:
    if not pages:
        raise UserError("El documento no tiene páginas.")
    with _output(output) as tmp:
        with ExitStack() as stack:
            docs = [stack.enter_context(open_pdf(p, pw)) for p, pw in sources]
            out = stack.enter_context(pymupdf.open())
            if docs:
                _copy_metadata(docs[0], out)
            k = 0
            total = len(pages)
            while k < total:
                ref = pages[k]
                if k % 20 == 0:
                    _report(progress, k, total, "Armando documento")
                if ref.source is None:
                    out.new_page(width=ref.width, height=ref.height)
                    if ref.rotate:
                        out[-1].set_rotation(ref.rotate % 360)
                    k += 1
                    continue
                # tramo contiguo de la misma fuente sin rotación extra
                j = k
                while (j + 1 < total and pages[j + 1].source == ref.source
                       and pages[j + 1].page == pages[j].page + 1
                       and not pages[j + 1].rotate and not ref.rotate):
                    j += 1
                first = out.page_count
                insert_pages(out, docs[ref.source], ref.page, pages[j].page)
                if ref.rotate:
                    for pno in range(first, out.page_count):
                        p = out[pno]
                        p.set_rotation((p.rotation + ref.rotate) % 360)
                k = j + 1
            _report(progress, total, total, "Guardando…")
            out.save(tmp, **SAVE_OPTS)
            return out.page_count


def rotate_pages(path: str | Path, output: str | Path, angle: int, spec: str = "",
                 password: str | None = None, progress: Progress = None) -> int:
    with _output(output) as tmp, open_pdf(path, password) as doc:
        idx = parse_pages(spec, doc.page_count)
        for i in idx:
            p = doc[i]
            p.set_rotation((p.rotation + angle) % 360)
        _save_keeping_password(doc, tmp, password)
        return len(idx)


# --------------------------------------------------------------------------
# Comprimir
# --------------------------------------------------------------------------

COMPRESSION_LEVELS = {
    # nivel: (umbral dpi, dpi destino, calidad jpeg, ¿imágenes sin pérdida a JPEG?)
    # None = no se tocan las imágenes
    "baja": None,
    "media": (180, 150, 75, False),
    "alta": (130, 110, 60, True),
    "maxima": (90, 72, 45, True),
}


def _image_options(threshold: int, target: int, quality: int, lossless_to_jpeg: bool):
    """Opciones de MuPDF con remuestreo bicúbico (reduce a la resolución exacta,
    no solo en factores de 2 como hace el modo por defecto)."""
    m = pymupdf.mupdf
    opts = m.PdfImageRewriterOptions()
    for kind in ("color", "gray"):
        for loss in ("lossy", "lossless"):
            prefix = f"{kind}_{loss}_image_"
            if loss == "lossy" or lossless_to_jpeg:
                setattr(opts, prefix + "recompress_method", m.FZ_RECOMPRESS_JPEG)
                setattr(opts, prefix + "recompress_quality", str(quality))
            else:
                setattr(opts, prefix + "recompress_method", m.FZ_RECOMPRESS_LOSSLESS)
            setattr(opts, prefix + "subsample_method", m.FZ_SUBSAMPLE_BICUBIC)
            setattr(opts, prefix + "subsample_threshold", threshold)
            setattr(opts, prefix + "subsample_to", target)
    return opts


def compress(path: str | Path, output: str | Path, level: str = "media", grayscale: bool = False,
             password: str | None = None, progress: Progress = None) -> tuple[int, int]:
    """Devuelve (tamaño original, tamaño final) en bytes."""
    if level not in COMPRESSION_LEVELS:
        raise ValueError(level)
    original = os.path.getsize(path)
    params = COMPRESSION_LEVELS[level]
    same_file = Path(path).resolve() == Path(output).resolve()
    with _output(output) as tmp, open_pdf(path, password) as doc:
        _report(progress, 0, 3, "Optimizando imágenes")
        if grayscale:
            doc.recolor(1)  # todo el documento (texto, gráficos e imágenes) a grises
        if params:
            doc.rewrite_images(options=_image_options(*params))
        _report(progress, 1, 3, "Optimizando fuentes")
        try:
            doc.subset_fonts()
        except Exception:
            pass
        _report(progress, 2, 3, "Guardando…")
        _save_keeping_password(doc, tmp, password, garbage=4, deflate_images=True, deflate_fonts=True)
    final = os.path.getsize(output)
    if final >= original and not same_file and not grayscale:
        # No se logró reducir: entregamos una copia del original.
        shutil.copyfile(path, output)
        final = original
    _report(progress, 3, 3, "Listo")
    return original, final


# --------------------------------------------------------------------------
# Imágenes
# --------------------------------------------------------------------------

def pdf_to_images(path: str | Path, out_dir: str | Path, fmt: str = "png", dpi: int = 150,
                  spec: str = "", password: str | None = None, progress: Progress = None) -> list[str]:
    fmt = fmt.lower().replace("jpeg", "jpg")
    if fmt not in ("png", "jpg"):
        raise UserError("Formato no soportado.")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(path).stem
    written = []
    with open_pdf(path, password) as doc:
        idx = parse_pages(spec, doc.page_count)
        width = len(str(doc.page_count))
        for k, i in enumerate(idx):
            _report(progress, k, len(idx), f"Página {i + 1}")
            pix = doc[i].get_pixmap(dpi=dpi, alpha=False)
            target = unique_path(out_dir / f"{stem}_{i + 1:0{width}d}.{fmt}")
            if fmt == "jpg":
                pix.save(str(target), output="jpg", jpg_quality=90)
            else:
                pix.save(str(target), output="png")
            written.append(str(target))
    _report(progress, len(idx), len(idx), "Listo")
    return written


def images_to_pdf(images: Sequence[str | tuple[str, int]], output: str | Path,
                  page_size: str = "imagen", orientation: str = "auto", margin: float = 0,
                  progress: Progress = None) -> int:
    """``images`` puede incluir tuplas (ruta, rotación extra en grados).

    page_size: «imagen» (la página toma el tamaño de la imagen) o una clave de PAGE_SIZES.
    orientation: «auto», «vertical» u «horizontal».  margin: en puntos.
    La orientación EXIF de las fotos se respeta automáticamente.
    """
    if not images:
        raise UserError("Agrega al menos una imagen.")
    items = [(i, 0) if isinstance(i, (str, Path)) else i for i in images]
    with _output(output) as tmp, pymupdf.open() as out:
        for k, (img_path, rot) in enumerate(items):
            name = Path(img_path).name
            _report(progress, k, len(items), f"Agregando {name}")
            try:
                # Convertir con MuPDF conserva el JPEG original y aplica la rotación EXIF.
                with pymupdf.open(str(img_path)) as img:
                    img_pdf = pymupdf.open("pdf", img.convert_to_pdf())
            except Exception as exc:
                raise UserError(f"No se pudo leer la imagen «{name}».") from exc
            r = img_pdf[0].rect
            iw, ih = (r.height, r.width) if rot % 180 else (r.width, r.height)
            if page_size == "imagen":
                pw, ph = iw + 2 * margin, ih + 2 * margin
            else:
                pw, ph = PAGE_SIZES[page_size]
                landscape = (orientation == "horizontal") or (orientation == "auto" and iw > ih)
                if landscape:
                    pw, ph = ph, pw
            page = out.new_page(width=pw, height=ph)
            box = pymupdf.Rect(margin, margin, pw - margin, ph - margin)
            with img_pdf:
                page.show_pdf_page(box, img_pdf, 0, keep_proportion=True, rotate=rot)
        _report(progress, len(items), len(items), "Guardando…")
        out.save(tmp, **SAVE_OPTS)
        return out.page_count


def extract_images(path: str | Path, out_dir: str | Path, min_px: int = 64, spec: str = "",
                   password: str | None = None, progress: Progress = None) -> list[str]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(path).stem
    seen: set[int] = set()
    written: list[str] = []
    with open_pdf(path, password) as doc:
        idx = parse_pages(spec, doc.page_count)
        for k, i in enumerate(idx):
            _report(progress, k, len(idx), f"Página {i + 1}")
            for img in doc[i].get_images(full=True):
                xref, smask, w, h = img[0], img[1], img[2], img[3]
                if xref in seen or min(w, h) < min_px:
                    continue
                seen.add(xref)
                try:
                    if smask:
                        pix = pymupdf.Pixmap(doc, xref)
                        mask = pymupdf.Pixmap(doc, smask)
                        if pix.alpha:
                            pix = pymupdf.Pixmap(pix, 0)
                        if pix.colorspace and pix.colorspace.n > 3:
                            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
                        pix = pymupdf.Pixmap(pix, mask)
                        data, ext = pix.tobytes("png"), "png"
                    else:
                        info = doc.extract_image(xref)
                        data, ext = info["image"], info["ext"]
                        if ext not in ("png", "jpeg", "jpg", "jpx", "bmp", "tiff", "gif"):
                            pix = pymupdf.Pixmap(doc, xref)
                            if pix.colorspace and pix.colorspace.n > 3:
                                pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
                            data, ext = pix.tobytes("png"), "png"
                except Exception:
                    continue
                ext = "jpg" if ext == "jpeg" else ext
                target = unique_path(out_dir / f"{stem}_p{i + 1}_{len(written) + 1}.{ext}")
                target.write_bytes(data)
                written.append(str(target))
    _report(progress, len(idx), len(idx), "Listo")
    return written


# --------------------------------------------------------------------------
# Sellos: marca de agua y numeración
# --------------------------------------------------------------------------

_FONTS: dict[str, pymupdf.Font] = {}


def _font(bold: bool = False) -> pymupdf.Font:
    key = "hebo" if bold else "helv"
    if key not in _FONTS:
        _FONTS[key] = pymupdf.Font(key)
    return _FONTS[key]


def _stamp_text(page: pymupdf.Page, text: str, anchor: pymupdf.Point, align: str,
                fontsize: float, color: tuple[float, float, float], opacity: float,
                angle: float = 0, bold: bool = False) -> None:
    """Escribe ``text`` con su centro vertical en ``anchor`` (coordenadas visibles)."""
    font = _font(bold)
    width = font.text_length(text, fontsize=fontsize)
    dx = {"left": 0.0, "center": -width / 2, "right": -width}[align]
    # Ajuste para centrar verticalmente sobre la altura de mayúsculas.
    start = anchor + (dx, fontsize * 0.35)
    tw = pymupdf.TextWriter(page.rect, opacity=opacity, color=color)
    tw.append(start, text, font=font, fontsize=fontsize)
    morph = (anchor, pymupdf.Matrix(angle)) if angle else None  # ángulo positivo = sube hacia la derecha
    tw.write_text(page, morph=morph, overlay=True)


def _prepare_page(page: pymupdf.Page) -> None:
    # Hornea la rotación para trabajar en coordenadas visibles.
    if page.rotation:
        page.remove_rotation()
    page.wrap_contents()


def apply_watermark(doc: pymupdf.Document, text: str, *, fontsize: float = 60,
                    color: tuple[float, float, float] = (0.6, 0.6, 0.6), opacity: float = 0.3,
                    angle: float = 45, position: str = "centro", bold: bool = True,
                    indices: Sequence[int] | None = None, progress: Progress = None) -> None:
    if not text.strip():
        raise UserError("Escribe el texto de la marca de agua.")
    indices = list(range(doc.page_count)) if indices is None else indices
    for k, i in enumerate(indices):
        if k % 10 == 0:
            _report(progress, k, len(indices), f"Página {i + 1}")
        page = doc[i]
        _prepare_page(page)
        r = page.rect
        if position == "mosaico":
            # Cuadrícula alineada con la dirección del texto y centrada en la página.
            w = _font(bold).text_length(text, fontsize=fontsize)
            along = w + fontsize * 2.5
            across = fontsize * 4
            rad = math.radians(angle)
            ux, uy = math.cos(rad), -math.sin(rad)      # dirección del texto
            vx, vy = math.sin(rad), math.cos(rad)       # perpendicular
            cx, cy = (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2
            reach = math.hypot(r.width, r.height) / 2 + w
            ni, nj = int(reach / along) + 1, int(reach / across) + 1
            for j in range(-nj, nj + 1):
                shift = along / 2 if j % 2 else 0
                for i in range(-ni, ni + 1):
                    d = i * along + shift
                    x, y = cx + d * ux + j * across * vx, cy + d * uy + j * across * vy
                    if r.x0 - w < x < r.x1 + w and r.y0 - w < y < r.y1 + w:
                        _stamp_text(page, text, pymupdf.Point(x, y), "center", fontsize, color,
                                    opacity, angle, bold)
        else:
            # Arriba/abajo la marca va horizontal (como un encabezado o pie).
            margin = max(fontsize, 36)
            y = {"superior": r.y0 + margin, "inferior": r.y1 - margin}.get(position, (r.y0 + r.y1) / 2)
            a = angle if position == "centro" else 0
            _stamp_text(page, text, pymupdf.Point((r.x0 + r.x1) / 2, y), "center", fontsize,
                        color, opacity, a, bold)


def watermark(path: str | Path, output: str | Path, text: str, spec: str = "",
              password: str | None = None, progress: Progress = None, **opts) -> int:
    with _output(output) as tmp, open_pdf(path, password) as doc:
        idx = parse_pages(spec, doc.page_count)
        apply_watermark(doc, text, indices=idx, progress=progress, **opts)
        _report(progress, len(idx), len(idx), "Guardando…")
        _save_keeping_password(doc, tmp, password)
        return len(idx)


NUMBER_POSITIONS = {
    "abajo-centro": ("bottom", "center"), "abajo-derecha": ("bottom", "right"),
    "abajo-izquierda": ("bottom", "left"), "arriba-centro": ("top", "center"),
    "arriba-derecha": ("top", "right"), "arriba-izquierda": ("top", "left"),
}


def page_number_text(fmt: str, n: int, total: int) -> str:
    try:
        return fmt.format(n=n, total=total, N=total)
    except (KeyError, IndexError, ValueError, AttributeError) as exc:
        raise UserError("Formato no válido. Usa {n} para el número y {total} para el total.") from exc


def stamp_page_number(page: pymupdf.Page, text: str, *, position: str = "abajo-centro",
                      fontsize: float = 11, margin: float = 28,
                      color: tuple[float, float, float] = (0, 0, 0), bold: bool = False) -> None:
    vpos, align = NUMBER_POSITIONS[position]
    _prepare_page(page)
    r = page.rect
    x = {"left": r.x0 + margin, "center": (r.x0 + r.x1) / 2, "right": r.x1 - margin}[align]
    y = r.y0 + margin if vpos == "top" else r.y1 - margin
    _stamp_text(page, text, pymupdf.Point(x, y), align, fontsize, color, 1.0, 0, bold)


def apply_page_numbers(doc: pymupdf.Document, *, fmt: str = "{n}", position: str = "abajo-centro",
                       fontsize: float = 11, margin: float = 28, start: int = 1,
                       skip_first: bool = False, color: tuple[float, float, float] = (0, 0, 0),
                       bold: bool = False, progress: Progress = None) -> None:
    first = 1 if skip_first else 0
    numbered = doc.page_count - first
    total = numbered + start - 1
    page_number_text(fmt, start, total)  # valida el formato antes de tocar páginas
    for k, i in enumerate(range(first, doc.page_count)):
        if k % 10 == 0:
            _report(progress, k, numbered, f"Página {i + 1}")
        stamp_page_number(doc[i], page_number_text(fmt, start + k, total), position=position,
                          fontsize=fontsize, margin=margin, color=color, bold=bold)


def page_numbers(path: str | Path, output: str | Path, password: str | None = None,
                 progress: Progress = None, **opts) -> int:
    with _output(output) as tmp, open_pdf(path, password) as doc:
        apply_page_numbers(doc, progress=progress, **opts)
        _save_keeping_password(doc, tmp, password)
        return doc.page_count


def preview_first_page(path: str | Path, password: str | None, apply: Callable[[pymupdf.Document], None],
                       max_px: int = 700, index: int = 0) -> tuple[int, int, int, bytes]:
    """Aplica ``apply`` a una copia de una página y la renderiza (vista previa)."""
    with open_pdf(path, password) as src, pymupdf.open() as tmp:
        index = max(0, min(index, src.page_count - 1))
        tmp.insert_pdf(src, from_page=index, to_page=index)
        apply(tmp)
        data = tmp.tobytes()
    with pymupdf.open("pdf", data) as doc:
        return render_page(doc, 0, max_px)


# --------------------------------------------------------------------------
# Seguridad
# --------------------------------------------------------------------------

def protect(path: str | Path, output: str | Path, user_pw: str, owner_pw: str = "",
            allow_print: bool = True, allow_copy: bool = True, allow_modify: bool = False,
            allow_annotate: bool = True, password: str | None = None,
            progress: Progress = None) -> None:
    if not user_pw and not owner_pw:
        raise UserError("Escribe una contraseña.")
    perm = int(pymupdf.PDF_PERM_ACCESSIBILITY)
    if allow_print:
        perm |= pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_PRINT_HQ
    if allow_copy:
        perm |= pymupdf.PDF_PERM_COPY
    if allow_modify:
        perm |= pymupdf.PDF_PERM_MODIFY | pymupdf.PDF_PERM_ASSEMBLE
    if allow_annotate:
        perm |= pymupdf.PDF_PERM_ANNOTATE | pymupdf.PDF_PERM_FORM
    owner = owner_pw or secrets.token_urlsafe(24)
    _report(progress, 0, 1, "Cifrando…")
    with _output(output) as tmp, open_pdf(path, password) as doc:
        doc.save(tmp, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=user_pw, owner_pw=owner,
                 permissions=perm, garbage=3, deflate=True)


def unlock(path: str | Path, output: str | Path, password: str | None,
           progress: Progress = None) -> None:
    _report(progress, 0, 1, "Quitando protección…")
    with _output(output) as tmp, open_pdf(path, password) as doc:
        if not doc.is_encrypted and not needs_password(path):
            # Puede tener solo restricciones (contraseña de propietario).
            pass
        doc.save(tmp, encryption=pymupdf.PDF_ENCRYPT_NONE, **SAVE_OPTS)


# --------------------------------------------------------------------------
# Texto y metadatos
# --------------------------------------------------------------------------

def extract_text(path: str | Path, output: str | Path, spec: str = "", password: str | None = None,
                 progress: Progress = None) -> int:
    """Devuelve la cantidad de caracteres extraídos."""
    parts = []
    with open_pdf(path, password) as doc:
        idx = parse_pages(spec, doc.page_count)
        for k, i in enumerate(idx):
            _report(progress, k, len(idx), f"Página {i + 1}")
            parts.append(f"===== Página {i + 1} =====\n{doc[i].get_text('text', sort=True).strip()}\n")
    text = "\n".join(parts)
    with _output(output) as tmp:
        Path(tmp).write_text(text, encoding="utf-8-sig")
    return sum(len(p) for p in parts)


META_KEYS = ("title", "author", "subject", "keywords", "creator", "producer")


def get_metadata(path: str | Path, password: str | None = None) -> dict[str, str]:
    with open_pdf(path, password) as doc:
        meta = doc.metadata or {}
        return {k: meta.get(k, "") or "" for k in META_KEYS}


def set_metadata(path: str | Path, output: str | Path, meta: dict[str, str],
                 password: str | None = None, progress: Progress = None) -> None:
    with _output(output) as tmp, open_pdf(path, password) as doc:
        current = doc.metadata or {}
        new = {k: current.get(k, "") or "" for k in META_KEYS}
        new.update({k: v for k, v in meta.items() if k in META_KEYS})
        doc.set_metadata(new)
        _save_keeping_password(doc, tmp, password)


# --------------------------------------------------------------------------
# Visor: render, búsqueda y texto bajo el cursor (coordenadas visibles)
# --------------------------------------------------------------------------

def render_scaled(doc: pymupdf.Document, index: int, scale: float) -> tuple[int, int, int, bytes]:
    pix = doc[index].get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
    return pix.width, pix.height, pix.stride, bytes(pix.samples)


def _visual(page: pymupdf.Page, rect) -> pymupdf.Rect:
    """Convierte un rectángulo del texto extraído (sin rotar) a coordenadas visibles."""
    r = pymupdf.Rect(rect)
    return r * page.rotation_matrix if page.rotation else r


def search_text(doc: pymupdf.Document, term: str) -> list[tuple[int, pymupdf.Rect]]:
    term = term.strip()
    if not term:
        return []
    hits = []
    for page in doc:
        for r in page.search_for(term):
            hits.append((page.number, _visual(page, r)))
    return hits


SANS, SERIF, MONO = "sans", "serif", "mono"
_BASE14 = {
    (SANS, False, False): "helv", (SANS, True, False): "hebo", (SANS, False, True): "heit", (SANS, True, True): "hebi",
    (SERIF, False, False): "tiro", (SERIF, True, False): "tibo", (SERIF, False, True): "tiit", (SERIF, True, True): "tibi",
    (MONO, False, False): "cour", (MONO, True, False): "cobo", (MONO, False, True): "coit", (MONO, True, True): "cobi",
}


def base14_font(family: str, bold: bool, italic: bool) -> str:
    return _BASE14[(family if family in (SANS, SERIF, MONO) else SANS, bool(bold), bool(italic))]


def _span_style(span: dict) -> tuple[str, bool, bool]:
    name = (span.get("font") or "").lower()
    flags = span.get("flags", 0)
    bold = bool(flags & 16) or "bold" in name or "black" in name or "heavy" in name
    italic = bool(flags & 2) or "italic" in name or "oblique" in name
    if flags & 8 or "courier" in name or "mono" in name or "consol" in name:
        family = MONO
    elif flags & 4 or "times" in name or "serif" in name and "sans" not in name or "georgia" in name \
            or "garamond" in name or "cambria" in name:
        family = SERIF
    else:
        family = SANS
    return family, bold, italic


def text_line_at(page: pymupdf.Page, x: float, y: float) -> dict | None:
    """Línea de texto en el punto visible (x, y), con su estilo, para «editar texto»."""
    pt = pymupdf.Point(x, y) * page.derotation_matrix if page.rotation else pymupdf.Point(x, y)
    best = None
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            bbox = pymupdf.Rect(line["bbox"])
            if not (bbox + (-2, -2, 2, 2)).contains(pt):
                continue
            spans = [s for s in line["spans"] if s["text"].strip()]
            if not spans:
                continue
            area = bbox.width * bbox.height
            if best is None or area < best[0]:
                best = (area, line, spans)
    if best is None:
        return None
    _, line, spans = best
    first = max(spans, key=lambda s: len(s["text"]))
    family, bold, italic = _span_style(first)
    c = first.get("color", 0)
    color = ((c >> 16 & 255) / 255, (c >> 8 & 255) / 255, (c & 255) / 255)
    dx, dy = line["dir"]
    if page.rotation:
        m = page.rotation_matrix
        dx, dy = dx * m.a + dy * m.c, dx * m.b + dy * m.d
    origin = pymupdf.Point(line["spans"][0]["origin"])
    if page.rotation:
        origin = origin * page.rotation_matrix
    text = "".join(s["text"] for s in line["spans"]).strip()
    return {
        "text": text, "rect": tuple(_visual(page, line["bbox"])), "origin": (origin.x, origin.y),
        "size": round(first["size"], 2), "family": family, "bold": bold, "italic": italic, "color": color,
        "horizontal": abs(dy) < 0.05 and dx > 0,
    }


def words_in_rect(page: pymupdf.Page, rect) -> list[tuple[float, float, float, float]]:
    """Rectángulos (uno por línea) de las palabras que toca ``rect`` (coordenadas visibles)."""
    area = pymupdf.Rect(rect)
    lines: dict[tuple[int, int], pymupdf.Rect] = {}
    for x0, y0, x1, y1, _w, b, ln, _n in page.get_text("words"):
        r = _visual(page, (x0, y0, x1, y1))
        if r.intersects(area):
            key = (b, ln)
            lines[key] = lines[key] | r if key in lines else r
    return [tuple(r) for r in lines.values()]


def page_has_text(page: pymupdf.Page, min_chars: int = 20) -> bool:
    return len(page.get_text("text").strip()) >= min_chars


def image_coverage(page: pymupdf.Page) -> float:
    """Fracción de la página cubierta por imágenes (0 a 1)."""
    area = page.rect.width * page.rect.height or 1
    covered = 0.0
    for info in page.get_image_info():
        r = pymupdf.Rect(info["bbox"]) & page.rect
        if not r.is_empty:
            covered += r.width * r.height
    return min(1.0, covered / area)


def looks_scanned(doc: pymupdf.Document, sample: int = 3) -> bool:
    """Las primeras páginas son escaneos sin capa de texto, aunque traigan encima un sello, un encabezado o
    un número de página digital (se decide con ``page_needs_ocr``, no con «la página no tiene texto»)."""
    pages = [doc[i] for i in range(min(sample, doc.page_count))]
    return bool(pages) and all(page_needs_ocr(p) for p in pages)


def span_is_invisible(span: dict) -> bool:
    """Texto que no se ve (la capa de un OCR): fuente GlyphLessFont, transparente o sin relleno ni contorno."""
    font = (span.get("font") or "").split("+")[-1]
    if font == "GlyphLessFont" or span.get("alpha", 255) == 0:
        return True
    flags = span.get("char_flags")
    return isinstance(flags, int) and not flags & 48       # ni relleno (16) ni contorno (32)


def text_layer(page: pymupdf.Page) -> tuple[int, int, float]:
    """(letras visibles, letras invisibles, fracción de la página que ocupan los renglones visibles)."""
    visibles = invisibles = 0
    area = 0.0
    flags = pymupdf.TEXT_PRESERVE_SPANS | pymupdf.TEXT_COLLECT_STYLES
    for block in page.get_text("dict", flags=flags)["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            en_renglon = 0
            for span in line["spans"]:
                n = len("".join(span.get("text", "").split()))
                if span_is_invisible(span):
                    invisibles += n
                else:
                    visibles += n
                    en_renglon += n
            if en_renglon:
                r = pymupdf.Rect(line["bbox"])
                area += abs(r.width * r.height)
    total = abs(page.rect.width * page.rect.height) or 1
    return visibles, invisibles, min(1.0, area / total)


def page_needs_ocr(page: pymupdf.Page, min_cover: float = 0.6) -> bool:
    """Página escaneada cuyo contenido no está como texto, aunque tenga encima un poco de texto digital
    (un sello de radicado, un encabezado, «Página 1 de 2»): una imagen grande, sin capa de OCR y con
    poco texto visible."""
    if image_coverage(page) <= min_cover:
        return False
    visibles, invisibles, area = text_layer(page)
    return invisibles < 20 and (visibles < 20 or area < 0.15)


def looks_scanned_without_text(doc: pymupdf.Document, sample: int = 3) -> bool:
    """Como looks_scanned, pero también cuenta los escaneos con un sello o un encabezado digital encima."""
    pages = [doc[i] for i in range(min(sample, doc.page_count))]
    return bool(pages) and all(page_needs_ocr(p) for p in pages)


# --------------------------------------------------------------------------
# Edición: textos, imágenes, formas, resaltados y borrado de zonas
# --------------------------------------------------------------------------

@dataclass
class Edit:
    """Una modificación sobre una página. Coordenadas en puntos, tal como se ve la página."""
    kind: str                      # text | replace | image | rect | ellipse | line | arrow | ink | highlight | redact
    page: int
    rect: tuple[float, float, float, float] = (0, 0, 0, 0)
    color: tuple[float, float, float] = (0, 0, 0)
    fill: tuple[float, float, float] | None = None
    width: float = 1.5
    opacity: float = 1.0
    fontsize: float = 12
    font: str = "helv"
    lines: list[tuple[float, float, str]] = field(default_factory=list)   # (x, línea base, texto)
    points: list[tuple[float, float]] = field(default_factory=list)
    rects: list[tuple[float, float, float, float]] = field(default_factory=list)
    image: bytes | None = None
    cover: tuple[float, float, float, float] | None = None                 # texto original a reemplazar
    autor: str = ""                # resaltado: quien lo hizo, su fecha (formato PDF) y su /NM
    fecha: str = ""
    nm: str = ""


def _arrow_head(p1: pymupdf.Point, p2: pymupdf.Point, width: float) -> list[pymupdf.Point]:
    size = max(6.0, width * 4)
    ang = math.atan2(p2.y - p1.y, p2.x - p1.x)
    left = pymupdf.Point(p2.x - size * math.cos(ang - 0.45), p2.y - size * math.sin(ang - 0.45))
    right = pymupdf.Point(p2.x - size * math.cos(ang + 0.45), p2.y - size * math.sin(ang + 0.45))
    return [left, p2, right]


def _apply_page_edits(page: pymupdf.Page, edits: list[Edit]) -> None:
    if page.rotation:
        page.remove_rotation()  # así las coordenadas visibles son las de la página
    covers = [e for e in edits if e.kind == "replace" and e.cover]
    if covers:
        for e in covers:
            page.add_redact_annot(pymupdf.Rect(e.cover))
        # solo se quita el texto original; imágenes y dibujos quedan intactos
        apply_redactions(page, images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
    redactions = [e for e in edits if e.kind == "redact"]
    if redactions:
        for e in redactions:
            page.add_redact_annot(pymupdf.Rect(e.rect), fill=e.fill or (1, 1, 1))
        apply_redactions(page, images=pymupdf.PDF_REDACT_IMAGE_PIXELS,
                         graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED)
    page.wrap_contents()
    for e in edits:
        op = dict(stroke_opacity=e.opacity, fill_opacity=e.opacity)
        if e.kind in ("text", "replace"):
            for x, y, t in e.lines:
                if t.strip():
                    page.insert_text((x, y), t, fontsize=e.fontsize, fontname=e.font, color=e.color,
                                     fill_opacity=e.opacity)
        elif e.kind == "image" and e.image:
            page.insert_image(pymupdf.Rect(e.rect), stream=e.image, keep_proportion=False)
        elif e.kind == "rect":
            page.draw_rect(pymupdf.Rect(e.rect), color=e.color, fill=e.fill, width=e.width, **op)
        elif e.kind == "ellipse":
            page.draw_oval(pymupdf.Rect(e.rect), color=e.color, fill=e.fill, width=e.width, **op)
        elif e.kind in ("line", "arrow") and len(e.points) >= 2:
            p1, p2 = pymupdf.Point(e.points[0]), pymupdf.Point(e.points[-1])
            page.draw_line(p1, p2, color=e.color, width=e.width, lineCap=1, stroke_opacity=e.opacity)
            if e.kind == "arrow":
                page.draw_polyline(_arrow_head(p1, p2, e.width), color=e.color, fill=e.color,
                                   width=e.width, closePath=True, lineJoin=1, **op)
        elif e.kind == "ink" and len(e.points) >= 2:
            page.draw_polyline([pymupdf.Point(p) for p in e.points], color=e.color, width=e.width,
                               lineCap=1, lineJoin=1, stroke_opacity=e.opacity)
        elif e.kind == "highlight" and e.rects:
            # un comentario más: con el nombre de quien lo hizo y su fecha, como los del panel de comentarios
            annot = page.add_highlight_annot([pymupdf.Rect(r) for r in e.rects])
            annot.set_colors(stroke=e.color)
            fecha = e.fecha or fecha_pdf_ahora()
            annot.set_info(title=e.autor or "", subject="Resaltado", creationDate=fecha, modDate=fecha)
            annot.update()
            if e.nm:
                page.parent.xref_set_key(annot.xref, "NM", pymupdf.get_pdf_str(e.nm))


def apply_edits(path: str | Path, output: str | Path, edits: Sequence[Edit], password: str | None = None,
                progress: Progress = None, extra: Callable[[pymupdf.Document], None] | None = None,
                incremental: bool = False) -> int:
    """Escribe los cambios del visor en una copia del PDF.  ``extra(doc)``, si se da, aplica además los
    cambios de las extensiones (comentarios, sellos…) sobre el documento abierto, antes de guardarlo.

    Con ``incremental=True`` los cambios se agregan al final de una copia exacta del original (sus bytes
    quedan intactos y una firma digital no se rompe).  Solo se hace si ningún cambio borra contenido (un
    borrado de zona o una línea editada dejarían lo borrado en la versión anterior, dentro del archivo) y si
    el PDF lo admite (no tuvo que repararse al abrirlo); si no, se reescribe completo, como siempre."""
    if not edits and extra is None:
        raise UserError("No hay cambios para guardar.")
    by_page: dict[int, list[Edit]] = defaultdict(list)
    for e in edits:
        by_page[e.page].append(e)
    if incremental and any(e.kind in ("redact", "replace") for e in edits):
        incremental = False
    if incremental:
        with open_pdf(path, password) as doc:      # una imagen o un PDF reparado no admiten incremental
            incremental = bool(doc.name == str(path) and doc.can_save_incrementally() and not doc.is_repaired)
    with _output(output) as tmp:
        if incremental:
            shutil.copyfile(path, tmp)
        with open_pdf(tmp if incremental else path, password) as doc:
            pages = sorted(p for p in by_page if 0 <= p < doc.page_count)
            for k, pno in enumerate(pages):
                _report(progress, k, len(pages), f"Aplicando cambios en la página {pno + 1}")
                _apply_page_edits(doc[pno], by_page[pno])
            if extra is not None:
                _report(progress, len(pages), len(pages), "Aplicando los demás cambios…")
                extra(doc)
            _report(progress, len(pages), len(pages), "Guardando…")
            _save_keeping_password(doc, tmp, password, incremental=incremental)
    return len(edits)


# --------------------------------------------------------------------------
# Censura (redacción) sin corrimientos
# --------------------------------------------------------------------------
#
# MuPDF 1.28 (PyMuPDF 2.1) tiene un defecto al quitar texto: cuando borra letras en medio de un
# texto escribe en el TJ un ajuste que no cuenta el avance de la primera letra quitada, así que lo que
# sigue en el renglón se corre a la izquierda y queda en parte debajo del recuadro negro (se tapan
# letras que no se querían censurar).  ``apply_redactions`` compara la posición de cada letra antes y
# después y corrige esos ajustes en el contenido de la página.  Si algo no cuadra (fuentes raras,
# escritura vertical…), deja la página como la dejó MuPDF.

_TOL_CORRIMIENTO = 0.05          # puntos
_WS = frozenset(b" \t\r\n\f\x00")
_DELIM = frozenset(b"()<>[]{}/%")
_KEYWORDS = {b"true", b"false", b"null"}
_ESCAPES = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12}


class _NoCuadra(Exception):
    """El contenido no se puede interpretar con seguridad: no se corrige nada."""


def _literal(data: bytes, i: int) -> tuple[bytes, int]:
    """Cadena literal que empieza en data[i] == '('.  Devuelve (bytes, posición tras el ')')."""
    out = bytearray()
    depth = 1
    i += 1
    n = len(data)
    while i < n:
        c = data[i]
        if c == 0x5C:                                   # barra invertida
            i += 1
            if i >= n:
                break
            c = data[i]
            if c in _ESCAPES:
                out.append(_ESCAPES[c])
            elif 0x30 <= c <= 0x37:
                j = i
                while j < n and j < i + 3 and 0x30 <= data[j] <= 0x37:
                    j += 1
                out.append(int(data[i:j], 8) & 0xFF)
                i = j
                continue
            elif c == 0x0D:                             # continuación de renglón
                if i + 1 < n and data[i + 1] == 0x0A:
                    i += 1
            elif c != 0x0A:
                out.append(c)
            i += 1
            continue
        if c == 0x28:
            depth += 1
        elif c == 0x29:
            depth -= 1
            if depth == 0:
                return bytes(out), i + 1
        if c == 0x0D:                                   # fin de renglón sin escapar = \n
            out.append(0x0A)
            if i + 1 < n and data[i + 1] == 0x0A:
                i += 1
            i += 1
            continue
        out.append(c)
        i += 1
    raise _NoCuadra("cadena sin cerrar")


def _tokens(data: bytes):
    """Tokens de un flujo de contenido: (tipo, valor, inicio, fin)."""
    i, n = 0, len(data)
    while i < n:
        c = data[i]
        if c in _WS:
            i += 1
        elif c == 0x25:                                 # comentario
            while i < n and data[i] not in (0x0A, 0x0D):
                i += 1
        elif c == 0x28:
            val, j = _literal(data, i)
            yield ("str", val, i, j)
            i = j
        elif c == 0x3C:
            if i + 1 < n and data[i + 1] == 0x3C:
                yield ("<<", None, i, i + 2)
                i += 2
            else:
                j = data.find(b">", i)
                if j < 0:
                    raise _NoCuadra("cadena hexadecimal sin cerrar")
                h = bytes(ch for ch in data[i + 1:j] if ch not in _WS)
                if len(h) % 2:
                    h += b"0"
                try:
                    val = bytes.fromhex(h.decode("ascii"))
                except ValueError as exc:
                    raise _NoCuadra("cadena hexadecimal inválida") from exc
                yield ("str", val, i, j + 1)
                i = j + 1
        elif c == 0x3E:
            if i + 1 < n and data[i + 1] == 0x3E:
                yield (">>", None, i, i + 2)
                i += 2
            else:
                raise _NoCuadra("'>' suelto")
        elif c in b"[]{}":
            yield (chr(c), None, i, i + 1)
            i += 1
        else:
            j = i + 1
            while j < n and data[j] not in _WS and data[j] not in _DELIM:
                j += 1
            word = data[i:j]
            if c == 0x2F:
                yield ("name", word[1:], i, j)
            elif word in _KEYWORDS:
                yield ("kw", word, i, j)
            else:
                try:
                    yield ("num", float(word), i, j)
                except ValueError:
                    yield ("op", word, i, j)
                    if word == b"ID":                   # imagen en línea: datos binarios hasta EI
                        k = j + 1
                        while True:
                            k = data.find(b"EI", k)
                            if k < 0:
                                raise _NoCuadra("imagen en línea sin EI")
                            fin = k + 2
                            if data[k - 1] in _WS and (fin >= n or data[fin] in _WS or data[fin] in _DELIM):
                                break
                            k += 2
                        yield ("op", b"EI", k, fin)
                        j = fin
            i = j


def _operaciones(data: bytes) -> list[tuple]:
    """[(operador, operandos, inicio, fin)].  Los arreglos llegan como ('array', elementos, inicio, fin)."""
    ops: list[tuple] = []
    stack: list[tuple] = []
    nest: list[tuple] = []
    for t in _tokens(data):
        typ = t[0]
        if typ in ("[", "<<"):
            nest.append((typ, [], t[2]))
        elif typ in ("]", ">>"):
            if not nest:
                raise _NoCuadra("cierre suelto")
            kind, items, ini = nest.pop()
            val = ("array" if kind == "[" else "dict", items, ini, t[3])
            (nest[-1][1] if nest else stack).append(val)
        elif typ == "op" and not nest:
            ops.append((t[1], stack, stack[0][2] if stack else t[2], t[3]))
            stack = []
        elif typ in ("{", "}"):
            continue
        else:
            (nest[-1][1] if nest else stack).append(t)
    if nest:
        raise _NoCuadra("arreglo sin cerrar")
    return ops


def _bytes_por_codigo(tipo: str, codificacion: str) -> int | None:
    if tipo != "Type0":
        return 1
    if codificacion in ("Identity-H", "Identity-V") or "UCS2" in codificacion or "UTF16" in codificacion:
        return 2
    return None                                         # CMap de longitud variable: no se toca


_RE_RECURSO = re.compile(r"/([^\s/<>\[\]()]+)\s*(\d+)\s+\d+\s+R")
_RE_RANGOS = re.compile(rb"begincodespacerange(.*?)endcodespacerange", re.S)


class _Recursos:
    """Fuentes y XObjects de la página y de sus formularios XObject, leídos de cada diccionario /Resources.

    (``page.get_fonts`` no sirve aquí: si una fuente la usan la página y un formulario, solo informa uno.)"""

    def __init__(self, doc: pymupdf.Document, pagina: int):
        self.doc, self.pagina = doc, pagina
        self._cache: dict = {}

    def tiene(self, xref: int) -> bool:
        return self.doc.xref_get_key(xref, "Resources")[0] != "null"

    def _nombres(self, xref: int, clave: str) -> dict[str, int]:
        doc = self.doc
        t, v = doc.xref_get_key(xref, f"Resources/{clave}")
        x = xref
        if t == "null" and xref == self.pagina and not self.tiene(xref):
            for _ in range(32):                         # recursos heredados del árbol de páginas
                tp, vp = doc.xref_get_key(x, "Parent")
                if tp != "xref":
                    break
                x = int(vp.split()[0])
                t, v = doc.xref_get_key(x, f"Resources/{clave}")
                if t != "null":
                    break
        if t == "xref":
            t, v = "dict", doc.xref_object(int(v.split()[0]), compressed=True) or ""
        if t != "dict":
            return {}
        return {n: int(r) for n, r in _RE_RECURSO.findall(v)}

    def fuentes(self, xref: int) -> dict[str, int | None]:
        if ("Font", xref) not in self._cache:
            self._cache[("Font", xref)] = {n: self._bytes_por_codigo(fx)
                                           for n, fx in self._nombres(xref, "Font").items()}
        return self._cache[("Font", xref)]

    def xobjetos(self, xref: int) -> dict[str, int]:
        if ("XObject", xref) not in self._cache:
            self._cache[("XObject", xref)] = self._nombres(xref, "XObject")
        return self._cache[("XObject", xref)]

    def _bytes_por_codigo(self, fx: int) -> int | None:
        doc = self.doc
        if doc.xref_get_key(fx, "Subtype")[1] != "/Type0":
            return 1
        t, enc = doc.xref_get_key(fx, "Encoding")
        if t == "name":
            return _bytes_por_codigo("Type0", enc.lstrip("/"))
        if t == "xref":                                 # CMap incrustado: se miran sus rangos de códigos
            cx = int(enc.split()[0])
            por_nombre = _bytes_por_codigo("Type0", doc.xref_get_key(cx, "CMapName")[1].lstrip("/"))
            if por_nombre:
                return por_nombre
            largos = {len(h) for m in _RE_RANGOS.findall(doc.xref_stream(cx) or b"")
                      for h in re.findall(rb"<([0-9A-Fa-f]+)>", m)}
            if largos == {4}:
                return 2
            if largos == {2}:
                return 1
        return None


_POSICION = {b"BT", b"Td", b"TD", b"Tm", b"T*"}
_MUESTRA = {b"Tj", b"TJ", b"'", b'"'}


class _Ranura:
    """Una letra pintada: en qué flujo de contenido (0 = el de la página, o el xref de un formulario
    XObject), en qué operación y en qué byte de qué cadena está.  ``op`` = -1: no se corrige."""
    __slots__ = ("flujo", "op", "elem", "off", "reinicio")

    def __init__(self, flujo: int, op: int, elem: int, off: int, reinicio: bool):
        self.flujo, self.op, self.elem, self.off, self.reinicio = flujo, op, elem, off, reinicio


def _elementos(op: tuple) -> list[tuple]:
    """Cadenas y números que muestra una operación de texto (lo de dentro de un TJ)."""
    nombre, operandos = op[0], op[1]
    if nombre == b"TJ":
        if not operandos or operandos[-1][0] != "array":
            raise _NoCuadra("TJ sin arreglo")
        return [e for e in operandos[-1][1] if e[0] in ("str", "num")]
    if not operandos or operandos[-1][0] != "str":
        raise _NoCuadra("texto sin cadena")
    return [operandos[-1]]


class _Recorrido:
    """Recorre el contenido de una página (y de sus formularios XObject) letra por letra, en el mismo
    orden en que MuPDF las pinta."""

    def __init__(self, doc: pymupdf.Document, recursos: _Recursos):
        self.doc, self.recursos = doc, recursos
        self.ranuras: list[_Ranura] = []
        self.flujos: dict[int, tuple[bytes, list[tuple]]] = {}
        self.usos: dict[int, int] = defaultdict(int)

    def recorrer(self, data: bytes, flujo: int, ref: int, estado: tuple = (None, 0), profundidad: int = 0) -> None:
        """``flujo``: 0 para la página o el xref del formulario; ``ref``: dueño de sus /Resources."""
        doc, recursos = self.doc, self.recursos
        ops = _operaciones(data)
        self.flujos.setdefault(flujo, (data, ops))
        fuente, modo = estado
        pila: list[tuple] = []
        reinicio = True
        for k, op in enumerate(ops):
            nombre, operandos = op[0], op[1]
            if nombre == b"q":
                pila.append((fuente, modo))
            elif nombre == b"Q":
                if pila:
                    fuente, modo = pila.pop()
            elif nombre == b"Tf":
                if not operandos or operandos[0][0] != "name":
                    raise _NoCuadra("Tf sin nombre")
                clave = operandos[0][1].decode("latin-1")
                fuentes = recursos.fuentes(ref)
                if clave not in fuentes:
                    raise _NoCuadra(f"fuente desconocida: {clave}")
                fuente = fuentes[clave]
                if fuente is None:
                    raise _NoCuadra("fuente con CMap variable")
            elif nombre == b"Tr" and operandos and operandos[0][0] == "num":
                modo = int(operandos[0][1])
            elif nombre in _POSICION:
                reinicio = True
            elif nombre in _MUESTRA:
                if nombre in (b"'", b'"'):
                    reinicio = True
                if fuente is None:
                    raise _NoCuadra("texto sin fuente")
                for ei, el in enumerate(_elementos(op)):
                    if el[0] != "str":
                        continue
                    if len(el[1]) % fuente:
                        raise _NoCuadra("cadena con bytes sueltos")
                    if modo == 7:                       # solo recorte: MuPDF no lo informa
                        continue
                    for off in range(0, len(el[1]), fuente):
                        self.ranuras.append(_Ranura(flujo, k, ei, off, reinicio))
                        reinicio = False
            elif nombre == b"Do" and operandos and operandos[0][0] == "name":
                xref = recursos.xobjetos(ref).get(operandos[0][1].decode("latin-1"))
                if xref and doc.xref_get_key(xref, "Subtype")[1] == "/Form":
                    if profundidad > 6:
                        raise _NoCuadra("formularios demasiado anidados")
                    self.usos[xref] += 1
                    # un formulario sin /Resources propios usa los de quien lo pinta (PDF antiguos)
                    dueño = xref if recursos.tiene(xref) else ref
                    self.recorrer(doc.xref_stream(xref) or b"", xref, dueño, (fuente, modo), profundidad + 1)
                    reinicio = True

    def terminar(self) -> None:
        """Un formulario que se pinta más de una vez no se corrige (el arreglo valdría para todas sus copias)."""
        for r in self.ranuras:
            if r.flujo and self.usos[r.flujo] > 1:
                r.op = -1


def _traza_letras(page: pymupdf.Page) -> list[tuple] | None:
    """Letras del contenido de la página (sin anotaciones), en el orden en que se pintan:
    (gid, origen, dirección, tamaño, avance, ascendente, descendente, invisible)."""
    try:
        from pymupdf import extra, mupdf
    except ImportError:
        return None
    rot = page.rotation
    rc: list = []
    try:
        if rot:
            page.set_rotation(0)
        dev = extra.JM_new_texttrace_device(rc)
        prect = mupdf.fz_bound_page(page.this)
        dev.ptm = mupdf.FzMatrix(1, 0, 0, -1, 0, prect.y1)
        mupdf.fz_run_page_contents(page.this, dev, mupdf.FzMatrix(), mupdf.FzCookie())
        mupdf.fz_close_device(dev)
    except Exception:  # noqa: BLE001 — sin la traza no se corrige, pero la censura sigue
        return None
    finally:
        if rot:
            page.set_rotation(rot)
    out: list[tuple] = []
    previo = None
    for span in rc:
        if span.get("wmode"):
            return None                                 # escritura vertical: no se toca
        chars = span.get("chars") or ()
        firma = tuple((c[1], c[2]) for c in chars)
        if firma and firma == previo:                   # relleno y contorno: la misma letra dos veces
            continue
        previo = firma
        dx, dy = span.get("dir", (1, 0))
        tam = span.get("size", 0) or 0
        asc = span.get("ascender", 0.9) or 0.9
        dsc = span.get("descender", -0.2) or -0.2
        invisible = span.get("type") == 3
        for c in chars:
            if c[1] < 0:                                # letra extra de una ligadura (fi → f, i)
                continue
            b = c[3]
            avance = (b[2] - b[0]) if abs(dx) >= abs(dy) else (b[3] - b[1])
            out.append((c[1], c[2], (dx, dy), tam, avance, asc, dsc, invisible))
    return out


def _caja_borrado(letra: tuple, encoger: float) -> tuple[float, float, float, float]:
    """Caja con la que MuPDF decide si borra una letra: la del texto extraído, encogida un 10 % por lado."""
    _gid, (ox, oy), (dx, dy), tam, avance, asc, dsc, _inv = letra
    vx, vy = dy, -dx                                    # «arriba» de la letra, en la página
    a0, a1 = avance * encoger, avance * (1 - encoger)
    alto = (asc - dsc) * tam
    b0, b1 = dsc * tam + alto * encoger, asc * tam - alto * encoger
    xs, ys = [], []
    for a in (a0, a1):
        for b in (b0, b1):
            xs.append(ox + a * dx + b * vx)
            ys.append(oy + a * dy + b * vy)
    return min(xs), min(ys), max(xs), max(ys)


def _alinear(antes: list[tuple], despues: list[tuple], zonas: list[pymupdf.Rect],
             solo_invisible: bool) -> list[float] | None:
    """Para cada letra que quedó: cuánto se corrió hacia atrás (en puntos) en su dirección de escritura.

    Las letras borradas se deducen con el mismo criterio de MuPDF; si el resultado no coincide letra por
    letra con lo que quedó en la página, devuelve None (no se corrige nada)."""
    objetivo = [g[0] for g in despues]
    for encoger in (0.1, 0.12, 0.08, 0.15, 0.05, 0.2, 0.0):
        quedan = []
        for g in antes:
            if not solo_invisible or g[7]:
                x0, y0, x1, y1 = _caja_borrado(g, encoger)
                if any(x0 < z.x1 and z.x0 < x1 and y0 < z.y1 and z.y0 < y1 for z in zonas):
                    continue
            quedan.append(g)
        if [g[0] for g in quedan] != objetivo:
            continue
        corr = []
        for a, b in zip(quedan, despues):
            (ax, ay), (bx, by) = a[1], b[1]
            dx, dy = a[2]
            if abs((ax - bx) * dy - (ay - by) * dx) > 0.5:   # cambió de renglón: no es la misma letra
                break
            corr.append((ax - bx) * dx + (ay - by) * dy)
        else:
            return corr
    return None


def _num_pdf(v: float) -> bytes:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return (s if s not in ("", "-0") else "0").encode("ascii")


def _reescribir(data: bytes, ops: list[tuple], cambios: dict[int, dict[tuple[int, int], float]]) -> bytes:
    """Inserta ajustes en los TJ: ``cambios[op][(elemento, byte)] = número`` (va antes de esa letra)."""
    out = data
    for k in sorted(cambios, reverse=True):
        op = ops[k]
        nombre, operandos = op[0], op[1]
        nuevos: list[list] = []
        for ei, el in enumerate(_elementos(op)):
            if el[0] == "num":
                nuevos.append(["num", el[1]])
                continue
            b = el[1]
            pos = 0
            for off in sorted(o for (e, o) in cambios[k] if e == ei):
                if off > pos:
                    nuevos.append(["str", b[pos:off]])
                    pos = off
                if nuevos and nuevos[-1][0] == "num":
                    nuevos[-1][1] += cambios[k][(ei, off)]
                else:
                    nuevos.append(["num", cambios[k][(ei, off)]])
            if pos < len(b):
                nuevos.append(["str", b[pos:]])
        arreglo = b"[" + b" ".join(b"<" + v.hex().upper().encode("ascii") + b">" if t == "str" else _num_pdf(v)
                                    for t, v in nuevos) + b"] TJ"
        if nombre == b"'":
            arreglo = b"T* " + arreglo
        elif nombre == b'"':
            aw, ac = operandos[0], operandos[1]
            arreglo = data[aw[2]:aw[3]] + b" Tw " + data[ac[2]:ac[3]] + b" Tc T* " + arreglo
        out = out[:op[2]] + arreglo + out[op[3]:]
    return out


def _corregir_corrimientos(page: pymupdf.Page, antes: list[tuple], zonas: list[pymupdf.Rect],
                           solo_invisible: bool) -> None:
    doc = page.parent
    despues = _traza_letras(page)
    if not despues:
        return
    corr = _alinear(antes, despues, zonas, solo_invisible)
    if not corr or max(abs(c) for c in corr) <= _TOL_CORRIMIENTO:
        return
    xrefs = page.get_contents()
    if not xrefs:
        return
    recursos = _Recursos(doc, page.xref)
    peor = max(abs(c) for c in corr)
    for _vuelta in range(3):
        try:
            rec = _Recorrido(doc, recursos)
            rec.recorrer(b"\n".join(doc.xref_stream(x) or b"" for x in xrefs), 0, page.xref)
            rec.terminar()
            if len(rec.ranuras) != len(despues):
                return
            cambios: dict[tuple[int, int], dict[tuple[int, int], float]] = defaultdict(dict)
            previo = 0.0
            for r, g, c in zip(rec.ranuras, despues, corr):
                if r.reinicio:
                    previo = 0.0
                d = c - previo
                if r.op >= 0 and abs(d) > _TOL_CORRIMIENTO and g[3] > 0:
                    cambios[(r.flujo, r.op)][(r.elem, r.off)] = -d * 1000.0 / g[3]
                previo = c
            if not cambios:
                return
            nuevos = {}
            for flujo in {f for f, _op in cambios}:
                data, ops = rec.flujos[flujo]
                nuevos[flujo] = _reescribir(data, ops, {op: v for (f, op), v in cambios.items() if f == flujo})
        except _NoCuadra:
            return
        respaldo = {flujo: rec.flujos[flujo][0] for flujo in nuevos}
        for flujo, datos in nuevos.items():
            doc.update_stream(xrefs[0] if flujo == 0 else flujo, datos)
        if 0 in nuevos and len(xrefs) > 1:
            doc.xref_set_key(page.xref, "Contents", f"{xrefs[0]} 0 R")
            xrefs = xrefs[:1]
        trazo = _traza_letras(page)
        nuevo_corr = None
        if trazo is not None and [g[0] for g in trazo] == [g[0] for g in despues]:
            nuevo_corr = _alinear(antes, trazo, zonas, solo_invisible)
        if nuevo_corr is None or max(abs(c) for c in nuevo_corr) >= peor:
            for flujo, datos in respaldo.items():       # no mejoró: se deja como estaba
                doc.update_stream(xrefs[0] if flujo == 0 else flujo, datos)
            return
        despues, corr = trazo, nuevo_corr
        peor = max(abs(c) for c in corr)
        if peor <= _TOL_CORRIMIENTO:
            return


def _zonas_de_censura(page: pymupdf.Page) -> list[pymupdf.Rect]:
    """Rectángulos de las censuras marcadas, en coordenadas de la página sin girar."""
    doc = page.parent
    out = []
    for annot in page.annots(types=[pymupdf.PDF_ANNOT_REDACT]):
        try:
            _t, v = doc.xref_get_key(annot.xref, "Rect")
            nums = [float(x) for x in v.strip("[]").split()]
            out.append((pymupdf.Rect(nums) * page.transformation_matrix).normalize())
        except Exception:  # noqa: BLE001
            out.append(pymupdf.Rect(annot.rect))
    return out


def apply_redactions(page: pymupdf.Page, images: int = pymupdf.PDF_REDACT_IMAGE_PIXELS,
                     graphics: int = pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                     text: int = pymupdf.PDF_REDACT_TEXT_REMOVE) -> bool:
    """``page.apply_redactions`` sin correr el texto vecino (defecto de MuPDF 1.28, ver arriba)."""
    antes = zonas = None
    if text != pymupdf.PDF_REDACT_TEXT_NONE:
        zonas = _zonas_de_censura(page)
        antes = _traza_letras(page) if zonas else None
    hecho = page.apply_redactions(images=images, graphics=graphics, text=text)
    if antes:
        try:
            _corregir_corrimientos(page, antes, zonas, text == 2)
        except Exception:  # noqa: BLE001 — la corrección es un extra: la censura ya quedó aplicada
            pass
    return hecho


# --------------------------------------------------------------------------
# OCR (Tesseract integrado en MuPDF)
# --------------------------------------------------------------------------

OCR_LANGUAGE_NAMES = {"spa": "Español", "eng": "Inglés", "por": "Portugués", "fra": "Francés",
                      "deu": "Alemán", "ita": "Italiano"}


def tessdata_dir() -> str | None:
    """Carpeta con los modelos de idioma. Una carpeta «tessdata» junto al .exe tiene prioridad,
    así se pueden agregar idiomas sin recompilar."""
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "tessdata")
        candidates.append(Path(getattr(sys, "_MEIPASS", "")) / "ronypdf" / "tessdata")
    candidates.append(Path(__file__).resolve().parent / "tessdata")
    if os.environ.get("TESSDATA_PREFIX"):
        candidates.append(Path(os.environ["TESSDATA_PREFIX"]))
    for c in candidates:
        if c.is_dir() and any(c.glob("*.traineddata")):
            return str(c)
    return None


def ocr_languages() -> list[str]:
    d = tessdata_dir()
    return sorted(p.stem for p in Path(d).glob("*.traineddata") if p.stem != "osd") if d else []


def _check_ocr(language: str) -> str:
    d = tessdata_dir()
    if not d:
        raise UserError("No se encontraron los modelos de idioma del OCR (carpeta «tessdata»).")
    missing = [lang for lang in language.split("+") if not (Path(d) / f"{lang}.traineddata").exists()]
    if missing:
        raise UserError(f"Falta el modelo de idioma «{', '.join(missing)}» para el OCR.")
    return d


def _ocr_words(page: pymupdf.Page, language: str, dpi: int, tessdata: str) -> list[tuple]:
    try:
        tp = page.get_textpage_ocr(language=language, dpi=dpi, full=True, tessdata=tessdata)
    except Exception as exc:  # noqa: BLE001
        raise UserError(f"El OCR falló en la página {page.number + 1}: {exc}") from exc
    return page.get_text("words", textpage=tp)


def _needs_ocr_pass(page: pymupdf.Page) -> bool:
    """¿Hay que reconocer esta página?  Sí si es un escaneo sin capa de texto (aunque traiga encima un sello
    o un encabezado digital) o si no tiene nada de texto (p. ej. letras convertidas en dibujos); no si es
    digital, aunque tenga poco texto, ni si ya trae su capa invisible."""
    return page_needs_ocr(page) or not page.get_text("text").strip()


_TEXT_BOX_FLAGS = pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES


def _text_boxes(page: pymupdf.Page) -> list[tuple[pymupdf.Rect, list[pymupdf.Rect]]]:
    """Letras que la página ya tiene (visibles o invisibles), agrupadas por renglón: [(caja del renglón,
    [cajas de sus letras])], en coordenadas visibles."""
    mat = page.rotation_matrix if page.rotation else None
    out = []
    for block in page.get_text("rawdict", flags=_TEXT_BOX_FLAGS)["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            chars = []
            for span in line["spans"]:
                for ch in span["chars"]:
                    if ch["c"].strip():
                        r = pymupdf.Rect(ch["bbox"])
                        if mat is not None:
                            r = r * mat
                        if not r.is_empty:
                            chars.append(r)
            if chars:
                box = pymupdf.Rect(chars[0])
                for r in chars[1:]:
                    box |= r
                out.append((box, chars))
    return out


def _drop_covered(words: Sequence, boxes: list, rect_of: Callable = lambda w: w[:4]) -> list:
    """Quita las palabras del OCR que caen (al menos la mitad de su área) sobre texto que la página ya tiene:
    así un sello, un encabezado o una capa de OCR anterior no quedan repetidos."""
    if not boxes:
        return list(words)
    out = []
    for w in words:
        x0, y0, x1, y1 = rect_of(w)
        area = max((x1 - x0) * (y1 - y0), 1e-6)
        covered = 0.0
        for box, chars in boxes:
            if box.x1 <= x0 or box.x0 >= x1 or box.y1 <= y0 or box.y0 >= y1:
                continue
            for b in chars:
                ix = min(b.x1, x1) - max(b.x0, x0)
                iy = min(b.y1, y1) - max(b.y0, y0)
                if ix > 0 and iy > 0:
                    covered += ix * iy
        if covered < 0.5 * area:
            out.append(w)
    return out


def ocr_pdf(path: str | Path, output: str | Path, language: str = "spa+eng", dpi: int = 300,
            only_missing: bool = True, spec: str = "", password: str | None = None,
            progress: Progress = None) -> tuple[int, int]:
    """Agrega una capa de texto invisible sobre las páginas (PDF buscable).

    La apariencia de las páginas no cambia.  Con ``only_missing`` solo reconoce las páginas que lo necesitan
    (``_needs_ocr_pass``): los escaneos, también los que traen un sello digital encima.  Las palabras que caen
    sobre texto que la página ya tiene no se repiten.  Devuelve (páginas reconocidas, palabras escritas).
    """
    tessdata = _check_ocr(language)
    pages_done = words_total = 0
    font = _font(False)
    with _output(output) as tmp, open_pdf(path, password) as doc:
        idx = parse_pages(spec, doc.page_count)
        for k, i in enumerate(idx):
            _report(progress, k, len(idx), f"Reconociendo texto: página {i + 1} de {doc.page_count}")
            page = doc[i]
            if only_missing and not _needs_ocr_pass(page):
                continue
            if page.rotation:
                page.remove_rotation()
            words = _drop_covered(_ocr_words(page, language, dpi, tessdata), _text_boxes(page))
            pages_done += 1
            if not words:
                continue
            # Aísla el contenido original (q … Q): si dejó una transformación abierta,
            # el texto nuevo quedaría desplazado.
            page.wrap_contents()
            tw = pymupdf.TextWriter(page.rect)
            for x0, y0, x1, y1, word, *_ in words:
                h, w = y1 - y0, x1 - x0
                if h <= 0 or w <= 0 or not word.strip():
                    continue
                size = h * 0.85
                length = font.text_length(word, fontsize=size)
                if length > 0:
                    size *= min(1.0, w / length)   # que la palabra no se salga de su caja
                tw.append((x0, y1 - h * 0.2), word, font=font, fontsize=max(size, 1))
                words_total += 1
            tw.write_text(page, render_mode=3)     # 3 = texto invisible
        _report(progress, len(idx), len(idx), "Guardando…")
        _save_keeping_password(doc, tmp, password)
    return pages_done, words_total


def _reading_order(lines: list[tuple[pymupdf.Rect, str]]) -> str:
    """Une renglones (caja visible, texto) de arriba abajo; los que comparten altura van en una línea."""
    lines = sorted(((r, t) for r, t in lines if t.strip()), key=lambda it: (it[0].y0 + it[0].y1) / 2)
    rows: list[list[tuple[pymupdf.Rect, str]]] = []
    for r, t in lines:
        yc, h = (r.y0 + r.y1) / 2, max(r.height, 1)
        if rows:
            last = rows[-1][-1][0]
            if abs((last.y0 + last.y1) / 2 - yc) < 0.5 * min(h, max(last.height, 1)):
                rows[-1].append((r, t))
                continue
        rows.append([(r, t)])
    return "\n".join("   ".join(t.strip() for _r, t in sorted(row, key=lambda it: it[0].x0)) for row in rows)


def _ocr_page_text(page: pymupdf.Page, language: str, dpi: int, tessdata: str) -> str:
    """Texto de una página escaneada: el digital que ya tenga (un sello, un encabezado) más el reconocido
    que no repite ese texto."""
    try:
        tp = page.get_textpage_ocr(language=language, dpi=dpi, full=True, tessdata=tessdata)
    except Exception as exc:  # noqa: BLE001
        raise UserError(f"El OCR falló en la página {page.number + 1}: {exc}") from exc
    boxes = _text_boxes(page)
    if not boxes:
        return page.get_text("text", textpage=tp, sort=True)
    mat = page.rotation_matrix
    words = _drop_covered([(*(pymupdf.Rect(w[:4]) * mat), *w[4:]) for w in page.get_text("words", textpage=tp)],
                          boxes)
    lines: dict[tuple[int, int], list] = {}
    for w in words:
        lines.setdefault((w[5], w[6]), []).append(w)
    out = []
    for ws in lines.values():
        box = pymupdf.Rect(ws[0][:4])
        for w in ws[1:]:
            box |= pymupdf.Rect(w[:4])
        out.append((box, " ".join(w[4] for w in sorted(ws, key=lambda w: w[7]))))
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", ()):
            text = "".join(s.get("text", "") for s in line["spans"])
            out.append((pymupdf.Rect(line["bbox"]) * mat, text))
    return _reading_order(out)


def ocr_text(path: str | Path, output: str | Path, language: str = "spa+eng", dpi: int = 300,
             spec: str = "", password: str | None = None, progress: Progress = None) -> int:
    """Extrae el texto a .txt usando OCR en las páginas que lo necesitan (``_needs_ocr_pass``; en un escaneo
    con un sello digital encima van el texto del sello y el reconocido, sin repetir).  Devuelve caracteres."""
    tessdata = _check_ocr(language)
    parts = []
    with open_pdf(path, password) as doc:
        idx = parse_pages(spec, doc.page_count)
        for k, i in enumerate(idx):
            _report(progress, k, len(idx), f"Reconociendo texto: página {i + 1} de {doc.page_count}")
            page = doc[i]
            if not _needs_ocr_pass(page):
                text = page.get_text("text", sort=True)
            else:
                text = _ocr_page_text(page, language, dpi, tessdata)
            parts.append(f"===== Página {i + 1} =====\n{text.strip()}\n")
    with _output(output) as tmp:
        Path(tmp).write_text("\n".join(parts), encoding="utf-8-sig")
    return sum(len(p) for p in parts)
