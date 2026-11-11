"""Guardado seguro, sin interfaz: copias de respaldo con fecha, guardado incremental y trabajo sin guardar.

Tres piezas, todas locales (nada sale del equipo):

* **Copias de respaldo** (``RonyPDF_respaldos``, junto a los ajustes).  Antes de que un «Guardar» reemplace
  un PDF, ``respaldar(ruta)`` copia el original a una subcarpeta propia de ese documento (una huella corta de
  su ruta) con el nombre «<nombre> (AAAA-MM-DD HH.MM.SS).pdf».  La retención por defecto es de 5 copias por
  documento, 30 días y 500 MB en total, y se aplica cada vez que se respalda.
* **Guardado incremental**: con ``core.apply_edits(..., incremental=True)`` y
  ``formularios.rellenar(..., incremental=True)`` los cambios se agregan al final de una copia exacta del
  original.  ``modo_incremental`` decide si se puede (nada se borra, el PDF no se reparó al abrirlo).
* **Trabajo sin guardar** (``RonyPDF_recuperacion``).  Cada sesión de RonyPDF deja un archivo con su PID y su
  hora de inicio; el autoguardado escribe por cada herramienta con cambios un manifiesto .json y, para el visor
  y los formularios, un PDF hecho con la misma rutina de guardado (cifrado con la misma contraseña si el
  original la tenía; las contraseñas nunca se escriben).  Al arrancar, ``huerfanos`` devuelve el trabajo de las
  sesiones cuyo proceso ya no existe (un cierre inesperado); las de otra ventana abierta se ignoran.

Si la carpeta de los ajustes no deja escribir, se usa ``%APPDATA%\\RonyPDF`` (el mismo respaldo de settings.py).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import secrets
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

import pymupdf

from . import core
from .core import UserError

CARPETA_RESPALDOS = "RonyPDF_respaldos"
CARPETA_RECUPERACION = "RonyPDF_recuperacion"
META_DOCUMENTO = "documento.json"
TEXTO_PRIVACIDAD = ("Las copias y los trabajos sin guardar se quedan solo en este equipo, en las carpetas "
                    "RonyPDF_respaldos y RonyPDF_recuperacion junto al programa.")
BORRAN = ("redact", "replace")          # cambios que quitan contenido: nunca en una versión incremental
HERRAMIENTAS = {"viewer": "Ver y editar", "forms": "Formularios y firma", "organize": "Organizar páginas"}
_FECHA_NOMBRE = re.compile(r"\((\d{4})-(\d{2})-(\d{2}) (\d{2})\.(\d{2})\.(\d{2})\)(?: \(\d+\))?\.pdf$", re.I)


@dataclass
class Retencion:
    """Cuántas copias de respaldo se conservan."""
    copias: int = 5          # por documento
    dias: int = 30           # las más viejas se borran
    megas: int = 500         # entre todas


def retencion_de(ajustes) -> Retencion:
    """La retención configurada (``ajustes`` es un settings.Settings o None)."""
    if ajustes is None:
        return Retencion()

    def entero(clave, defecto, minimo, maximo):
        try:
            return max(minimo, min(maximo, int(ajustes.get(clave, defecto))))
        except (TypeError, ValueError):
            return defecto

    return Retencion(entero("respaldo_copias", 5, 1, 50), entero("respaldo_dias", 30, 1, 3650),
                     entero("respaldo_megas", 500, 10, 100_000))


# --------------------------------------------------------------------------
# Carpetas
# --------------------------------------------------------------------------

def carpetas(nombre: str, ajustes: str | Path | None = None) -> list[Path]:
    """Dónde puede vivir la carpeta ``nombre``, en orden de preferencia: junto al archivo de ajustes (o junto
    al programa) y, si allí no se puede escribir, en %APPDATA%\\RonyPDF.  Con RONYPDF_SETTINGS (las pruebas)
    solo se usa la carpeta de ese archivo, como en settings.py."""
    out: list[Path] = []
    if ajustes:
        out.append(Path(ajustes).resolve().parent / nombre)
    else:
        from .settings import _candidates
        out += [p.resolve().parent / nombre for p in _candidates()]
    if not os.environ.get("RONYPDF_SETTINGS"):
        appdata = os.environ.get("APPDATA")
        if appdata:
            out.append(Path(appdata).resolve() / "RonyPDF" / nombre)
    vistos, unicas = set(), []
    for p in out:
        k = os.path.normcase(str(p))
        if k not in vistos:
            vistos.add(k)
            unicas.append(p)
    return unicas


def _escribible(carpeta: Path) -> bool:
    try:
        carpeta.mkdir(parents=True, exist_ok=True)
        prueba = carpeta / f".prueba-{os.getpid()}-{secrets.token_hex(3)}"
        prueba.write_bytes(b"")
        prueba.unlink()
        return True
    except OSError:
        return False


def carpeta_escritura(nombre: str, ajustes: str | Path | None = None) -> Path:
    """La primera carpeta de ``carpetas`` donde se puede escribir (se crea si hace falta)."""
    cands = carpetas(nombre, ajustes)
    for c in cands:
        if _escribible(c):
            return c
    raise UserError(f"No hay ninguna carpeta donde guardar «{nombre}». Revisa los permisos de la carpeta del "
                    "programa.")


def carpetas_lectura(nombre: str, ajustes: str | Path | None = None) -> list[Path]:
    """Las carpetas ``nombre`` que ya existen (para listar, sin crear nada)."""
    return [c for c in carpetas(nombre, ajustes) if c.is_dir()]


def _clave_ruta(ruta: str | Path) -> str:
    return os.path.normcase(os.path.abspath(str(ruta)))


def huella(ruta: str | Path) -> str:
    """Identificador corto y estable de un documento (por su ruta normalizada)."""
    return hashlib.sha1(_clave_ruta(ruta).encode("utf-8")).hexdigest()[:12]


def _escribir_json(destino: Path, datos: dict) -> None:
    with core._output(destino) as tmp:
        Path(tmp).write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")


def _leer_json(ruta: Path) -> dict | None:
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        return datos if isinstance(datos, dict) else None
    except (OSError, ValueError):
        return None


def copiar(origen: str | Path, destino: str | Path) -> None:
    """Copia atómica (temporal + fsync + reemplazo)."""
    with core._output(destino) as tmp:
        shutil.copyfile(origen, tmp)


def _motivo(exc: OSError) -> str:
    if getattr(exc, "errno", None) == 28:
        return "el disco está lleno"
    if isinstance(exc, PermissionError):
        return "no hay permiso para escribir en la carpeta de copias"
    return exc.strerror or str(exc)


# --------------------------------------------------------------------------
# Estado del archivo en el disco
# --------------------------------------------------------------------------

def firma_archivo(ruta: str | Path) -> tuple[int, int] | None:
    """(tamaño, fecha de modificación en ns) del archivo, o None si no existe."""
    try:
        st = os.stat(ruta)
    except OSError:
        return None
    return st.st_size, st.st_mtime_ns


def cambio_en_disco(ruta: str | Path, firma: tuple[int, int] | None) -> bool:
    """¿El archivo cambió (otro programa lo guardó) desde que se tomó ``firma``?"""
    if firma is None:
        return False
    actual = firma_archivo(ruta)
    return actual is not None and tuple(actual) != tuple(firma)


def no_escribible(ruta: str | Path) -> str | None:
    """Por qué no se puede reemplazar el archivo (texto para la persona), o None si se puede."""
    p = Path(ruta)
    if not p.is_file():
        return f"«{p.name}» ya no está en su carpeta: se movió o se borró después de abrirlo."
    if not os.access(p, os.W_OK):
        return f"«{p.name}» es de solo lectura."
    try:
        with open(p, "r+b"):
            pass
    except PermissionError:
        return f"«{p.name}» está abierto en otro programa o no tienes permiso para cambiarlo."
    except OSError as exc:
        return f"No se puede escribir en «{p.name}»: {exc.strerror or exc}."
    carpeta = p.parent
    if not os.access(carpeta, os.W_OK):
        return f"No tienes permiso para escribir en la carpeta de «{p.name}»."
    return None


# --------------------------------------------------------------------------
# Guardado incremental y firmas digitales
# --------------------------------------------------------------------------

def puede_incremental(doc: pymupdf.Document) -> bool:
    """¿El documento abierto admite agregar cambios al final?  (No, si MuPDF tuvo que repararlo.)"""
    try:
        return bool(doc.is_pdf and doc.can_save_incrementally() and not doc.is_repaired)
    except Exception:  # noqa: BLE001
        return False


def borra_contenido(edits: Sequence[core.Edit]) -> bool:
    return any(e.kind in BORRAN for e in edits)


def modo_incremental(doc: pymupdf.Document, edits: Sequence[core.Edit] = (), aplanar: bool = False) -> bool:
    """¿Un guardado sobre el mismo archivo puede ser incremental?  Nunca si algo se borra (borrado de zona,
    línea editada, aplanar): lo borrado quedaría en la versión anterior, dentro del archivo."""
    return not aplanar and not borra_contenido(edits) and puede_incremental(doc)


def tiene_firma_digital(doc: pymupdf.Document) -> bool:
    try:
        return int(doc.get_sigflags()) > 0
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------
# Copias de respaldo
# --------------------------------------------------------------------------

@dataclass
class Respaldo:
    ruta: Path              # la copia
    original: str           # ruta del documento respaldado
    documento: str          # nombre del documento
    fecha: _dt.datetime
    tamano: int
    clave: str              # subcarpeta (huella)


def _fecha_de(copia: Path) -> _dt.datetime:
    m = _FECHA_NOMBRE.search(copia.name)
    if m:
        try:
            return _dt.datetime(*(int(x) for x in m.groups()))
        except ValueError:
            pass
    try:
        return _dt.datetime.fromtimestamp(copia.stat().st_mtime)
    except OSError:
        return _dt.datetime.now()


def respaldar(ruta: str | Path, carpeta: str | Path | None = None, retencion: Retencion | None = None,
              ahora: _dt.datetime | None = None) -> Path:
    """Copia el PDF ``ruta`` a las copias de respaldo y devuelve la copia.  Si no se puede crear (disco lleno,
    sin permiso…) lanza UserError: quien llama no debe reemplazar el original."""
    src = Path(ruta)
    if not src.is_file():
        raise UserError(f"No se pudo hacer la copia de respaldo: «{src.name}» ya no está en su carpeta.")
    try:
        base = Path(carpeta) if carpeta else carpeta_escritura(CARPETA_RESPALDOS)
        sub = base / huella(src)
        sub.mkdir(parents=True, exist_ok=True)
        _escribir_json(sub / META_DOCUMENTO, {"ruta": os.path.abspath(str(src)), "nombre": src.name})
        ahora = ahora or _dt.datetime.now()
        stem = core.safe_filename(src.stem)[:70]
        destino = core.unique_path(sub / f"{stem} ({ahora:%Y-%m-%d %H.%M.%S}).pdf")
        copiar(src, destino)
    except UserError as exc:
        raise UserError(f"No se pudo hacer la copia de respaldo de «{src.name}»: {exc}") from exc
    except OSError as exc:
        raise UserError(f"No se pudo hacer la copia de respaldo de «{src.name}»: {_motivo(exc)}.") from exc
    try:
        limpiar_respaldos(base, retencion or Retencion(), conservar=[destino], ahora=ahora)
    except OSError:
        pass                                   # la limpieza nunca impide guardar
    return destino


def listar_respaldos(carpetas_: Sequence[str | Path] | str | Path) -> list[Respaldo]:
    """Todas las copias, de la más nueva a la más vieja."""
    if isinstance(carpetas_, (str, Path)):
        carpetas_ = [carpetas_]
    out: list[Respaldo] = []
    for base in carpetas_:
        base = Path(base)
        if not base.is_dir():
            continue
        try:
            subs = [s for s in base.iterdir() if s.is_dir()]
        except OSError:
            continue
        for sub in subs:
            meta = _leer_json(sub / META_DOCUMENTO) or {}
            try:
                copias = [c for c in sub.glob("*.pdf") if c.is_file()]
            except OSError:
                continue
            for c in copias:
                try:
                    tam = c.stat().st_size
                except OSError:
                    continue
                nombre = meta.get("nombre") or re.sub(r" \(\d{4}-.*$", "", c.stem) + ".pdf"
                out.append(Respaldo(c, meta.get("ruta", ""), nombre, _fecha_de(c), tam, sub.name))
    out.sort(key=lambda r: (r.fecha, r.ruta.name), reverse=True)
    return out


def respaldos_de(ruta: str | Path, carpetas_) -> list[Respaldo]:
    """Las copias de un documento."""
    clave = huella(ruta)
    return [r for r in listar_respaldos(carpetas_) if r.clave == clave]


def limpiar_respaldos(carpeta: str | Path, retencion: Retencion | None = None, conservar: Sequence[Path] = (),
                      ahora: _dt.datetime | None = None) -> list[Path]:
    """Aplica la retención: como máximo ``copias`` por documento, ninguna más vieja que ``dias`` y ``megas``
    entre todas (se borran primero las más viejas).  Nunca borra lo que esté en ``conservar``."""
    r = retencion or Retencion()
    ahora = ahora or _dt.datetime.now()
    fijas = {_clave_ruta(p) for p in conservar}
    todas = listar_respaldos([carpeta])
    borrar: list[Respaldo] = []
    por_doc: dict[str, list[Respaldo]] = {}
    for x in todas:
        por_doc.setdefault(x.clave, []).append(x)
    limite = ahora - _dt.timedelta(days=r.dias)
    for copias in por_doc.values():
        copias.sort(key=lambda x: x.fecha, reverse=True)
        for k, x in enumerate(copias):
            if _clave_ruta(x.ruta) in fijas:
                continue
            if k >= r.copias or x.fecha < limite:
                borrar.append(x)
    quedan = [x for x in todas if x not in borrar]
    total = sum(x.tamano for x in quedan)
    for x in sorted(quedan, key=lambda x: x.fecha):            # las más viejas primero
        if total <= r.megas * 1024 * 1024:
            break
        if _clave_ruta(x.ruta) in fijas:
            continue
        borrar.append(x)
        total -= x.tamano
    hechos = []
    for x in borrar:
        try:
            x.ruta.unlink()
            hechos.append(x.ruta)
        except OSError:
            continue
    _quitar_vacias(Path(carpeta))
    return hechos


def _quitar_vacias(base: Path) -> None:
    try:
        subs = [s for s in base.iterdir() if s.is_dir()]
    except OSError:
        return
    for sub in subs:
        try:
            if not any(sub.glob("*.pdf")):
                for f in sub.iterdir():
                    f.unlink()
                sub.rmdir()
        except OSError:
            continue


def _dentro(ruta: Path, bases: Sequence[str | Path]) -> bool:
    try:
        real = ruta.resolve()
    except OSError:
        return False
    for b in bases:
        try:
            real.relative_to(Path(b).resolve())
            return True
        except (ValueError, OSError):
            continue
    return False


def eliminar_respaldo(copia: str | Path, carpetas_: Sequence[str | Path]) -> None:
    """Borra una copia (solo si está dentro de las carpetas de copias)."""
    p = Path(copia)
    if not _dentro(p, carpetas_):
        raise UserError("Esa copia no está en la carpeta de copias de respaldo.")
    try:
        p.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise UserError(f"No se pudo borrar la copia: {_motivo(exc)}.") from exc
    _quitar_vacias(p.parent.parent)


def borrar_respaldos(carpetas_: Sequence[str | Path]) -> int:
    """Borra todas las copias de respaldo. Devuelve cuántas se borraron."""
    n = 0
    for r in listar_respaldos(carpetas_):
        try:
            r.ruta.unlink()
            n += 1
        except OSError:
            continue
    for b in carpetas_:
        _quitar_vacias(Path(b))
    return n


def restaurar(copia: str | Path, destino: str | Path, carpeta: str | Path | None = None,
              retencion: Retencion | None = None, respaldar_actual: bool = True, progress=None) -> Path | None:
    """Pone la copia ``copia`` en ``destino``.  Si ``destino`` existe, antes se respalda (así restaurar
    también se puede deshacer).  Devuelve la copia de respaldo del archivo reemplazado, si se hizo."""
    copia, destino = Path(copia), Path(destino)
    if not copia.is_file():
        raise UserError("Esa copia de respaldo ya no existe.")
    core._report(progress, 0, 2, "Guardando una copia del archivo actual…")
    previa = None
    if respaldar_actual and destino.is_file():
        previa = respaldar(destino, carpeta, retencion)
    core._report(progress, 1, 2, "Restaurando la versión anterior…")
    copiar(copia, destino)
    core._report(progress, 2, 2, "Listo")
    return previa


# --------------------------------------------------------------------------
# Guardar con respaldo (para ctx.run_task)
# --------------------------------------------------------------------------

@dataclass
class Resultado:
    valor: object = None
    respaldo: Path | None = None
    error_respaldo: str | None = None       # el respaldo falló: no se tocó el archivo
    segundos: float = 0.0


def guardar_con_respaldo(tarea: Callable, respaldo: bool = True, carpeta_respaldos: str | Path | None = None,
                         ajustes: str | Path | None = None, retencion: Retencion | None = None,
                         origen: str | Path | None = None, progress=None, **kwargs) -> Resultado:
    """Hace la copia de respaldo de ``origen`` (por defecto ``kwargs["path"]``) y luego
    ``tarea(progress=..., **kwargs)``, que escribe sobre ese archivo.  Si la copia falla no se escribe nada y
    el error vuelve en el resultado (no como excepción), para que la interfaz pueda ofrecer «Guardar como…»."""
    t0 = time.perf_counter()
    copia = None
    respaldado = origen or kwargs.get("path")
    antes = firma_archivo(respaldado) if respaldo and respaldado else None
    if respaldo:
        core._report(progress, 0, 0, "Guardando una copia de respaldo…")
        try:
            carpeta = carpeta_respaldos or carpeta_escritura(CARPETA_RESPALDOS, ajustes)
            copia = respaldar(origen or kwargs["path"], carpeta, retencion)
        except UserError as exc:
            return Resultado(error_respaldo=str(exc))
    try:
        valor = tarea(progress=progress, **kwargs)
    except BaseException:
        # No se pudo escribir (o se canceló) y el archivo sigue igual: la copia sería idéntica a él y solo
        # ocuparía uno de los cupos de copias de ese documento.
        if copia is not None and antes is not None and firma_archivo(respaldado) == antes:
            try:
                copia.unlink()
                _quitar_vacias(copia.parent.parent)
            except OSError:
                pass
        raise
    return Resultado(valor, copia, None, time.perf_counter() - t0)


# --------------------------------------------------------------------------
# Organizador: armar el documento conservando la contraseña de un único origen
# --------------------------------------------------------------------------

def armar_pdf(sources: Sequence[tuple[str, str | None]], pages: Sequence[core.PageRef], output: str | Path,
              progress=None) -> int:
    """Como ``core.build_document``; pero si todas las páginas vienen de un único PDF (el caso de «Guardar»
    sobre el mismo archivo) además conserva lo que ese documento trae fuera de las páginas: la contraseña,
    los adjuntos, las propiedades y los marcadores que sigan apuntando a páginas que quedan.  En los dos casos
    los comentarios conservan sus respuestas y sus estados (``core.insert_pages``)."""
    usados = {p.source for p in pages if p.source is not None}
    if len(sources) != 1 or usados - {0} or core.is_image(sources[0][0]):
        return core.build_document(sources, pages, output, progress)
    if not pages:
        raise UserError("El documento no tiene páginas.")
    path, password = sources[0]
    with core._output(output) as tmp, core.open_pdf(path, password) as src, pymupdf.open() as out:
        core._copy_metadata(src, out)
        total = len(pages)
        nuevo: dict[int, int] = {}            # página original → primera posición en el resultado
        k = 0
        while k < total:
            ref = pages[k]
            if k % 20 == 0:
                core._report(progress, k, total, "Armando documento")
            if ref.source is None:
                out.new_page(width=ref.width, height=ref.height)
                if ref.rotate:
                    out[-1].set_rotation(ref.rotate % 360)
                k += 1
                continue
            j = k
            while (j + 1 < total and pages[j + 1].source == ref.source and pages[j + 1].page == pages[j].page + 1
                   and not pages[j + 1].rotate and not ref.rotate):
                j += 1
            first = out.page_count
            core.insert_pages(out, src, ref.page, pages[j].page)     # con respuestas y estados
            for off, pno in enumerate(range(ref.page, pages[j].page + 1)):
                nuevo.setdefault(pno, first + off)
            if ref.rotate:
                for pno in range(first, out.page_count):
                    p = out[pno]
                    p.set_rotation((p.rotation + ref.rotate) % 360)
            k = j + 1
        try:                                   # marcadores que siguen apuntando a una página que quedó
            toc, nivel = [], 0
            for lvl, titulo, pagina, *_ in src.get_toc(simple=True):
                if pagina - 1 not in nuevo:
                    continue
                lvl = max(1, min(int(lvl), nivel + 1))
                toc.append([lvl, titulo, nuevo[pagina - 1] + 1])
                nivel = lvl
            if all(t[0] == 1 for t in toc):     # un índice plano sigue el nuevo orden de las páginas
                toc.sort(key=lambda t: t[2])
            if toc:
                out.set_toc(toc)
        except Exception:  # noqa: BLE001 — un índice raro no impide guardar
            pass
        for i in range(src.embfile_count()):   # adjuntos
            try:
                info = src.embfile_info(i)
                out.embfile_add(info["name"], src.embfile_get(i), filename=info.get("filename") or None,
                                desc=info.get("description") or None)
            except Exception:  # noqa: BLE001 — un adjunto dañado no impide guardar
                continue
        core._report(progress, total, total, "Guardando…")
        opts = dict(core.SAVE_OPTS)
        if password and core.is_password_protected(src):
            opts.update(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=password,
                        owner_pw=secrets.token_urlsafe(24), permissions=src.permissions)
        out.save(tmp, **opts)
        return out.page_count

# --------------------------------------------------------------------------
# Trabajo sin guardar: sesiones, autoguardado y recuperación
# --------------------------------------------------------------------------

def _creacion_proceso(pid: int) -> int:
    """Hora de creación del proceso (FILETIME de Windows) o 0 si no se sabe."""
    if sys.platform != "win32":
        return 0
    try:
        import ctypes
        from ctypes import wintypes

        from .oficina import _k32
        k = _k32()
        h = k.OpenProcess(0x1000, False, int(pid))
        if not h:
            return 0
        try:
            c, e, kk, u = (wintypes.FILETIME() for _ in range(4))
            if k.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(kk), ctypes.byref(u)):
                return (c.dwHighDateTime << 32) | c.dwLowDateTime
        finally:
            k.CloseHandle(h)
    except Exception:  # noqa: BLE001
        pass
    return 0


def proceso_vivo(pid: int, creado: int = 0) -> bool:
    """¿Sigue abierta la sesión de RonyPDF de ese proceso?  Compara también la hora de creación, por si
    Windows ya le dio ese número a otro programa."""
    if pid <= 0:
        return False
    if pid == os.getpid():
        vivo = True
    else:
        try:
            from .oficina import proceso_vivo as _vivo
            vivo = _vivo(pid)
        except Exception:  # noqa: BLE001
            vivo = False
    if vivo and creado:
        actual = _creacion_proceso(pid)
        if actual and abs(actual - int(creado)) > 20_000_000:       # más de 2 s: es otro proceso
            return False
    return vivo


def nueva_sesion() -> dict:
    pid = os.getpid()
    return {"id": f"{pid}-{int(time.time() * 1000)}-{secrets.token_hex(3)}", "pid": pid, "inicio": time.time(),
            "creado": _creacion_proceso(pid)}


def _archivo_sesion(carpeta: Path, sesion_id: str) -> Path:
    return carpeta / f"sesion_{sesion_id}.json"


def _base_trabajo(carpeta: Path, sesion_id: str, herramienta: str) -> Path:
    return carpeta / f"{sesion_id}__{herramienta}"


def registrar_sesion(carpeta: str | Path, sesion: dict) -> Path:
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    ruta = _archivo_sesion(carpeta, sesion["id"])
    if not ruta.exists():
        _escribir_json(ruta, {k: sesion[k] for k in ("id", "pid", "inicio", "creado")})
    return ruta


def manifiesto(herramienta: str, documento: str, sesion_id: str, pagina: int = 0, descripcion: str = "",
               protegido: bool = False, **extra) -> dict:
    firma = firma_archivo(documento) if documento else None
    datos = {"version": 1, "herramienta": herramienta, "documento": os.path.abspath(documento) if documento else "",
             "tamano_original": firma[0] if firma else None, "fecha_original": firma[1] if firma else None,
             "autoguardado": time.time(), "pagina": int(pagina), "descripcion": descripcion,
             "sesion": sesion_id, "protegido": bool(protegido), "pdf": None}
    datos.update(extra)
    return datos


def guardar_manifiesto(carpeta: str | Path, sesion_id: str, herramienta: str, datos: dict) -> Path:
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    destino = _base_trabajo(carpeta, sesion_id, herramienta).with_suffix(".json")
    datos = dict(datos, autoguardado=time.time())
    _escribir_json(destino, datos)
    return destino


def autoguardar_pdf(tarea: Callable, carpeta: str | Path, sesion_id: str, herramienta: str, datos: dict,
                    progress=None, **kwargs) -> dict:
    """Autoguardado del visor o de los formularios: ``tarea(output=<recuperado.pdf>, **kwargs)`` escribe el PDF
    con la misma rutina del guardado normal y luego se escribe el manifiesto.  Devuelve el manifiesto."""
    t0 = time.perf_counter()
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    pdf = _base_trabajo(carpeta, sesion_id, herramienta).with_suffix(".pdf")
    tarea(output=str(pdf), progress=progress, **kwargs)
    core._report(progress, 1, 2, "")               # punto de cancelación antes del manifiesto
    datos = dict(datos, pdf=pdf.name)
    guardar_manifiesto(carpeta, sesion_id, herramienta, datos)
    datos["segundos"] = time.perf_counter() - t0
    return datos


def borrar_trabajo(carpeta: str | Path, sesion_id: str, herramienta: str) -> None:
    base = _base_trabajo(Path(carpeta), sesion_id, herramienta)
    for ext in (".json", ".pdf"):
        try:
            base.with_suffix(ext).unlink()
        except FileNotFoundError:
            pass
        except OSError:
            continue


def cerrar_sesion(carpeta: str | Path, sesion_id: str) -> None:
    """Cierre normal: se borra todo lo de la sesión (no queda nada para recuperar)."""
    carpeta = Path(carpeta)
    if not carpeta.is_dir():
        return
    for f in list(carpeta.glob(f"{sesion_id}__*")) + [_archivo_sesion(carpeta, sesion_id)]:
        try:
            f.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            continue
    try:
        if not any(carpeta.iterdir()):
            carpeta.rmdir()
    except OSError:
        pass


@dataclass
class Trabajo:
    """Un trabajo sin guardar de una sesión que terminó de forma inesperada."""
    herramienta: str
    documento: str
    fecha: float
    descripcion: str
    pagina: int
    manifiesto: dict
    json: Path
    pdf: Path | None
    sesion: str
    datos: dict = field(default_factory=dict)

    @property
    def nombre(self) -> str:
        return Path(self.documento).name if self.documento else "Documento sin nombre"

    @property
    def titulo_herramienta(self) -> str:
        return HERRAMIENTAS.get(self.herramienta, self.herramienta)


def _datos_sesion(carpeta: Path, sesion_id: str) -> dict:
    datos = _leer_json(_archivo_sesion(carpeta, sesion_id)) or {}
    if "pid" not in datos:
        try:
            datos["pid"] = int(sesion_id.split("-")[0])
        except ValueError:
            datos["pid"] = 0
    return datos


def huerfanos(carpetas_: Sequence[str | Path], limpiar: bool = True) -> list[Trabajo]:
    """Trabajo sin guardar de las sesiones cuyo proceso ya no existe, del más nuevo al más viejo.  Las
    sesiones huérfanas que no dejaron nada se limpian (``limpiar``)."""
    out: list[Trabajo] = []
    for base in carpetas_:
        base = Path(base)
        if not base.is_dir():
            continue
        try:
            nombres = [f.name for f in base.iterdir() if f.is_file()]
        except OSError:
            continue
        sesiones = {n[len("sesion_"):-len(".json")] for n in nombres if n.startswith("sesion_") and n.endswith(".json")}
        sesiones |= {n.split("__", 1)[0] for n in nombres if "__" in n}
        for sid in sorted(sesiones):
            info = _datos_sesion(base, sid)
            if proceso_vivo(int(info.get("pid") or 0), int(info.get("creado") or 0)):
                continue
            trabajos = []
            for n in nombres:
                if not (n.startswith(f"{sid}__") and n.endswith(".json")):
                    continue
                ruta = base / n
                m = _leer_json(ruta)
                if not m or m.get("herramienta") not in HERRAMIENTAS:
                    continue
                pdf = base / m["pdf"] if m.get("pdf") else None
                if m["herramienta"] in ("viewer", "forms") and (pdf is None or not pdf.is_file()):
                    continue
                trabajos.append(Trabajo(m["herramienta"], m.get("documento", ""), float(m.get("autoguardado") or 0),
                                        m.get("descripcion", ""), int(m.get("pagina") or 0), m, ruta, pdf, sid))
            if trabajos:
                out.extend(trabajos)
            elif limpiar:
                cerrar_sesion(base, sid)
    out.sort(key=lambda t: t.fecha, reverse=True)
    return out


def descartar(trabajo: Trabajo) -> None:
    """Borra un trabajo sin guardar (y su sesión, si ya no le queda nada)."""
    for f in (trabajo.json, trabajo.pdf):
        if f is None:
            continue
        try:
            f.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            continue
    base = trabajo.json.parent
    try:
        if not any(base.glob(f"{trabajo.sesion}__*")):
            cerrar_sesion(base, trabajo.sesion)
    except OSError:
        pass


def sugerido_recuperacion(trabajo: Trabajo, carpeta_defecto: str | Path | None = None) -> str:
    """Dónde dejar un PDF recuperado: junto al original, «<nombre>_recuperado.pdf» (nunca el original)."""
    doc = Path(trabajo.documento) if trabajo.documento else None
    carpeta = doc.parent if doc is not None and doc.parent.is_dir() else Path(carpeta_defecto or Path.home())
    stem = doc.stem if doc is not None else "documento"
    return str(core.unique_path(carpeta / f"{stem}_recuperado.pdf"))


def recuperar_pdf(trabajo: Trabajo, destino: str | Path) -> Path:
    """Copia el PDF recuperado a ``destino`` (nunca encima del original) y borra el trabajo."""
    if trabajo.pdf is None or not trabajo.pdf.is_file():
        raise UserError("Ese trabajo ya no tiene un PDF para recuperar.")
    destino = Path(destino)
    if trabajo.documento and _clave_ruta(destino) == _clave_ruta(trabajo.documento):
        raise UserError("Para no perder el original, el trabajo recuperado se guarda con otro nombre.")
    copiar(trabajo.pdf, destino)
    descartar(trabajo)
    return destino


def fecha_amable(ts: float, ahora: float | None = None, segundos: bool = False) -> str:
    """«hoy 3:42 p. m.», «ayer 9:05 a. m.» o «lunes 9 de noviembre, 3:42 p. m.» (con ``segundos``,
    «hoy 3:42:07 p. m.», para distinguir copias hechas en el mismo minuto)."""
    d = _dt.datetime.fromtimestamp(ts)
    hoy = _dt.datetime.fromtimestamp(ahora if ahora is not None else time.time()).date()
    h = d.hour % 12 or 12
    seg = f":{d.second:02d}" if segundos else ""
    hora = f"{h}:{d.minute:02d}{seg} {'a. m.' if d.hour < 12 else 'p. m.'}"
    if d.date() == hoy:
        return f"hoy {hora}"
    if d.date() == hoy - _dt.timedelta(days=1):
        return f"ayer {hora}"
    dias = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
    meses = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
             "octubre", "noviembre", "diciembre")
    texto = f"{dias[d.weekday()]} {d.day} de {meses[d.month - 1]}"
    if d.year != hoy.year:
        texto += f" de {d.year}"
    return f"{texto}, {hora}"


def fecha_con_de(ts: float, ahora: float | None = None) -> str:
    """Para una frase: «de hoy a las 3:42 p. m.», «de ayer a la 1:05 a. m.» o «del lunes 2 de noviembre a las
    3:42 p. m.»."""
    texto = fecha_amable(ts, ahora)
    dia, hora = (texto.split(", ", 1) if ", " in texto else texto.split(" ", 1))
    articulo = "a la" if hora.startswith("1:") else "a las"
    prefijo = "de" if dia in ("hoy", "ayer") else "del"
    return f"{prefijo} {dia} {articulo} {hora}"


def frase(texto: str) -> str:
    """Termina una oración con punto, sin duplicarlo tras una abreviatura («p. m.»)."""
    return texto if texto.endswith(".") else texto + "."


def plural(n: int, uno: str, varios: str) -> str:
    return f"{n} {uno if n == 1 else varios}"
