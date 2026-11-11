"""Registro de diagnóstico: si RonyPDF se cierra de golpe, que quede evidencia de dónde estaba.

``iniciar()`` se llama desde run.py antes de importar la interfaz (este módulo no importa Qt Widgets: así
también queda constancia de un cierre durante las importaciones).  Cada sesión escribe un archivo
``RonyPDF_registros/sesion-AAAAMMDD-HHMMSS.log`` junto a los ajustes, con la misma política de respaldo que
settings.py y guardado.py (si allí no se puede escribir, en ``%APPDATA%\\RonyPDF``; con ``RONYPDF_SETTINGS``,
las pruebas, solo junto a ese archivo).  El registro tiene:

* la versión del programa, de Python, PySide6, Qt y PyMuPDF, y datos del equipo (Windows, memoria, núcleos);
* ``faulthandler`` con el archivo abierto toda la sesión: si el proceso muere en código nativo, la pila de
  Python de cada hilo;
* los mensajes de Qt de advertencia hacia arriba y las excepciones de Python (también las de las tareas);
* transiciones mínimas: la pantalla que se abre, el inicio y el fin de cada tarea, el cierre.

Nunca se escribe texto de documentos, contraseñas ni rutas de archivos de la persona: ``limpiar`` cambia las rutas
por «<ruta>» (las del propio programa quedan como «…\\ronypdf\\x.py») y los textos entre comillas que no parecen
un identificador por «…».

Un cierre inesperado se detecta solo con el marcador ``sesion-activa`` (se crea al iniciar y se borra al salir
bien), nunca por el contenido del registro: en Windows faulthandler anota también excepciones que el programa
maneja sin cerrarse (por ejemplo ``0x80040155`` al listar impresoras).  Se conservan las últimas 10 sesiones.
Nada sale del equipo: el informe se guarda donde la persona diga.
"""

from __future__ import annotations

import atexit
import faulthandler
import io
import os
import re
import sys
import threading
import time
import traceback
from pathlib import Path

from . import APP_NAME, __version__

CARPETA = "RonyPDF_registros"
MARCADOR = "sesion-activa"
PATRON = "sesion-*.log"
MAX_SESIONES = 10                  # registros que se conservan (incluida la sesión actual)
MAX_BYTES = 2 * 1024 * 1024        # tope por sesión: un bucle de avisos no debe llenar el disco
SESIONES_INFORME = 5               # registros que van en el informe
COLA_INFORME = 200 * 1024          # de cada uno, como máximo su parte final

_lock = threading.RLock()
_archivo = None                    # el registro abierto (lo usa faulthandler toda la sesión)
_ruta: Path | None = None
_carpeta: Path | None = None
_anterior: dict | None = None      # la sesión anterior, si terminó de forma inesperada
_escritos = 0
_recortado = False
_terminada = False
_ganchos: dict = {}


# --------------------------------------------------------------------------
# Datos sin información de la persona
# --------------------------------------------------------------------------

_RE_LINEA_FILE = re.compile(r'^(\s*File ")([^"]+)(")')
_RE_RUTA_COMILLAS = re.compile(r"""(['"])(?:[A-Za-z]:[\\/]|\\\\|file:/)(?:(?!\1)[^\n])*\1""")
_RE_RUTA = re.compile(r"""(?:(?<![A-Za-z0-9])[A-Za-z]:[\\/]|\\\\[^\\\s]|file:/)[^\n'"«»]*""")
_RE_LITERAL = re.compile(r"""(?<![A-Za-z0-9])(['"])((?:(?!\1)[^\n])*)\1(?![A-Za-z0-9])""")
_RE_ANGULARES = re.compile(r"«[^»\n]*»")
_RE_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_.:\-]{0,60}")


def _ruta_codigo(ruta: str) -> str:
    """De la ruta de un módulo en un traceback, solo su carpeta y su nombre («…\\ronypdf\\widgets.py»)."""
    if ruta.startswith("<"):                       # «<frozen importlib._bootstrap>», «<string>»
        return ruta
    partes = [p for p in re.split(r"[\\/]", ruta) if p]
    if ruta.lower().endswith((".py", ".pyw", ".pyc")) and partes:
        return "…\\" + "\\".join(partes[-2:])
    return "<ruta>"


def _literal(m: re.Match) -> str:
    q, contenido = m.group(1), m.group(2)
    if not contenido or contenido == "<ruta>" or contenido == "…" or _RE_IDENT.fullmatch(contenido):
        return m.group(0)
    return f"{q}…{q}"


def limpiar(texto: str) -> str:
    """``texto`` (un mensaje, un traceback) sin rutas de la persona ni textos que puedan venir de un documento.

    Las rutas absolutas pasan a «<ruta>» (las de los módulos del programa en un traceback conservan su carpeta y
    su nombre), lo que va entre «» pasa a «…» y, fuera de las líneas de código de un traceback, los textos entre
    comillas que no parecen un identificador pasan a '…'."""
    out = []
    for linea in str(texto).split("\n"):
        m = _RE_LINEA_FILE.match(linea)
        if m:
            out.append(m.group(1) + _ruta_codigo(m.group(2)) + m.group(3) + linea[m.end():])
            continue
        linea = _RE_RUTA_COMILLAS.sub(lambda r: f"{r.group(1)}<ruta>{r.group(1)}", linea)
        linea = _RE_RUTA.sub("<ruta>", linea)
        if not linea[:1].isspace():                # las líneas con sangría de un traceback son código
            linea = _RE_LITERAL.sub(_literal, linea)
            linea = _RE_ANGULARES.sub("«…»", linea)
        out.append(linea)
    return "\n".join(out)


def etiqueta(texto: str) -> str:
    """El texto de una tarea («Guardando «contrato.pdf»…») sin nombres de archivo, en una sola línea."""
    return " / ".join(p.strip() for p in limpiar(texto or "").splitlines() if p.strip())


def nombre_operacion(fn) -> str:
    """«core.merge_pdfs» para una función de RonyPDF (sin argumentos: pueden traer rutas)."""
    fn = getattr(fn, "func", fn)                   # functools.partial
    nombre = getattr(fn, "__qualname__", None) or getattr(fn, "__name__", None) or type(fn).__name__
    modulo = getattr(fn, "__module__", "") or ""
    if modulo.startswith("ronypdf."):
        modulo = modulo[len("ronypdf."):]
    return f"{modulo}.{nombre}" if modulo else str(nombre)


# --------------------------------------------------------------------------
# Equipo y versiones
# --------------------------------------------------------------------------

def _windows() -> str:
    if sys.platform != "win32":
        return sys.platform
    try:
        v = sys.getwindowsversion()
        mayor, menor, build = v.major, v.minor, v.build
        nombre = "Windows 11" if (mayor, build) >= (10, 22000) else f"Windows {mayor}"
        return f"{nombre} ({mayor}.{menor}, compilación {build})"
    except Exception:  # noqa: BLE001
        return "Windows"


def _memoria_total() -> str:
    if sys.platform != "win32":
        return ""
    try:
        import ctypes

        class _Estado(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        e = _Estado()
        e.dwLength = ctypes.sizeof(_Estado)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(e)):
            return f"{e.ullTotalPhys / 2**30:.1f} GB de memoria ({e.ullAvailPhys / 2**30:.1f} GB libres al iniciar)"
    except Exception:  # noqa: BLE001
        pass
    return ""


def memoria_proceso() -> str:
    """Memoria que usa RonyPDF ahora («412 MB»), o "" si no se sabe."""
    if sys.platform != "win32":
        return ""
    try:
        import ctypes
        from ctypes import wintypes

        class _Contadores(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (n, ctypes.c_size_t) for n in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                                               "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                                               "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
        c = _Contadores()
        c.cb = ctypes.sizeof(_Contadores)
        psapi = ctypes.WinDLL("psapi")
        psapi.GetProcessMemoryInfo.argtypes = (wintypes.HANDLE, ctypes.POINTER(_Contadores), wintypes.DWORD)
        k = ctypes.WinDLL("kernel32")
        k.GetCurrentProcess.restype = wintypes.HANDLE
        if psapi.GetProcessMemoryInfo(k.GetCurrentProcess(), ctypes.byref(c), c.cb):
            return f"{c.WorkingSetSize / 2**20:.0f} MB"
    except Exception:  # noqa: BLE001
        pass
    return ""


def _git_hash(raiz: Path) -> str:
    """El commit del código fuente (sin ejecutar git), o "" si no hay repositorio."""
    try:
        git = raiz / ".git"
        if git.is_file():                          # worktree: «gitdir: …»
            txt = git.read_text(encoding="utf-8").strip()
            if txt.startswith("gitdir:"):
                git = (raiz / txt[7:].strip()).resolve()
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            return head[:10]
        ref = head[4:].strip()
        bases = [git]
        if (git / "commondir").is_file():
            bases.append((git / (git / "commondir").read_text(encoding="utf-8").strip()).resolve())
        for base in bases:
            if (base / ref).is_file():
                return (base / ref).read_text(encoding="utf-8").strip()[:10]
        for base in bases:
            if (base / "packed-refs").is_file():
                for linea in (base / "packed-refs").read_text(encoding="utf-8").splitlines():
                    partes = linea.split()
                    if len(partes) == 2 and partes[1] == ref:
                        return partes[0][:10]
    except (OSError, ValueError):
        pass
    return ""


def _compilacion() -> str:
    if getattr(sys, "frozen", False):
        try:
            st = os.stat(sys.executable)
            return (f"ejecutable de {st.st_size / 2**20:.1f} MB, del "
                    f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(st.st_mtime))}")
        except OSError:
            return "ejecutable"
    h = _git_hash(Path(__file__).resolve().parent.parent)
    return f"código fuente (git {h})" if h else "código fuente"


def _versiones_qt() -> list[str]:
    out = []
    try:
        import PySide6
        out.append(f"PySide6 {PySide6.__version__}")
        qtcore = sys.modules.get("PySide6.QtCore")
        if qtcore is not None:
            out.append(f"Qt {qtcore.qVersion()}")
    except Exception:  # noqa: BLE001
        pass
    return out


def _version_pymupdf() -> str:
    m = sys.modules.get("pymupdf")
    if m is None:
        return ""
    try:
        return f"PyMuPDF {getattr(m, 'VersionBind', '?')} (MuPDF {getattr(m, 'VersionFitz', '?')})"
    except Exception:  # noqa: BLE001
        return "PyMuPDF"


def _pantallas() -> list[str]:
    try:
        gui = sys.modules.get("PySide6.QtGui")
        app = gui.QGuiApplication.instance() if gui is not None else None
        if app is None:
            return []
        out = []
        for s in app.screens():
            g = s.geometry()
            out.append(f"{g.width()} × {g.height()} a {s.devicePixelRatio() * 100:.0f} %")
        return out
    except Exception:  # noqa: BLE001
        return []


def _datos_base() -> list[str]:
    equipo = ", ".join(x for x in (f"{os.cpu_count() or '?'} núcleos", _memoria_total()) if x)
    return [f"{APP_NAME} {__version__} · {_compilacion()}",
            f"Python {sys.version.split()[0]} ({'64' if sys.maxsize > 2**32 else '32'} bits)",
            _windows(), f"Equipo: {equipo}"]


def datos_tecnicos() -> list[str]:
    """Versiones y datos del equipo, sin nada de la persona (ni rutas ni nombre de usuario)."""
    lineas = _datos_base()
    qt = _versiones_qt()
    if qt:
        lineas.append(" · ".join(qt))
    mu = _version_pymupdf()
    if mu:
        lineas.append(mu)
    pant = _pantallas()
    if pant:
        lineas.append("Monitor: " + "; ".join(pant))
    return lineas


# --------------------------------------------------------------------------
# Carpeta, marcador y rotación
# --------------------------------------------------------------------------

def carpetas() -> list[Path]:
    """Dónde puede vivir ``RonyPDF_registros``, en orden: junto a los ajustes (o al programa) y, si allí no se
    puede escribir, en %APPDATA%\\RonyPDF.  Con RONYPDF_SETTINGS solo junto a ese archivo (como guardado.py)."""
    from .settings import _candidates
    out = [p.resolve().parent / CARPETA for p in _candidates()]
    if not os.environ.get("RONYPDF_SETTINGS"):
        appdata = os.environ.get("APPDATA")
        if appdata:
            out.append(Path(appdata).resolve() / "RonyPDF" / CARPETA)
    vistos, unicas = set(), []
    for p in out:
        k = os.path.normcase(str(p))
        if k not in vistos:
            vistos.add(k)
            unicas.append(p)
    return unicas


def _creacion_proceso(pid: int) -> int:
    """Hora de creación del proceso (FILETIME de Windows) o 0 si no se sabe."""
    if sys.platform != "win32" or pid <= 0:
        return 0
    try:
        import ctypes
        from ctypes import wintypes
        k = ctypes.WinDLL("kernel32")
        k.OpenProcess.restype = wintypes.HANDLE
        k.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        k.CloseHandle.argtypes = (wintypes.HANDLE,)
        k.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
        k.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        k.WaitForSingleObject.restype = wintypes.DWORD
        h = k.OpenProcess(0x1000 | 0x100000, False, int(pid))      # consultar y esperar
        if not h:
            return 0
        try:
            if k.WaitForSingleObject(h, 0) == 0:                   # ya terminó
                return 0
            c, e, kk, u = (wintypes.FILETIME() for _ in range(4))
            if k.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(kk), ctypes.byref(u)):
                return (c.dwHighDateTime << 32) | c.dwLowDateTime
        finally:
            k.CloseHandle(h)
    except Exception:  # noqa: BLE001
        pass
    return 0


def _proceso_vivo(pid: int, creado: int) -> bool:
    """¿Sigue abierta la sesión de ese proceso?  (Mismo número y misma hora de creación: Windows reutiliza los
    números de proceso.)"""
    actual = _creacion_proceso(pid)
    if not actual:
        return False
    return not creado or abs(actual - creado) <= 20_000_000                  # 2 s de tolerancia


def _leer_marcador(carpeta: Path) -> dict | None:
    try:
        texto = (carpeta / MARCADOR).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return None
    except OSError:
        return {}
    datos = {}
    for linea in texto.splitlines():
        clave, _, valor = linea.partition("=")
        if clave.strip():
            datos[clave.strip()] = valor.strip()
    return datos


def _entero(valor) -> int:
    try:
        return int(valor)
    except (TypeError, ValueError):
        return 0


def _escribir_marcador(carpeta: Path, registro: str) -> None:
    pid = os.getpid()
    texto = (f"pid={pid}\ncreado={_creacion_proceso(pid)}\nregistro={registro}\n"
             f"inicio={time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    tmp = carpeta / f"{MARCADOR}.{pid}.tmp"
    tmp.write_text(texto, encoding="utf-8", newline="\n")
    os.replace(tmp, carpeta / MARCADOR)


def _registros(carpeta: Path) -> list[Path]:
    """Los registros de la carpeta, del más viejo al más nuevo."""
    def clave(p: Path):
        try:
            return (p.stat().st_mtime, p.name)
        except OSError:
            return (0.0, p.name)
    return sorted((p for p in carpeta.glob(PATRON) if p.is_file()), key=clave)


def rotar(carpeta: Path, conservar: int = MAX_SESIONES) -> None:
    """Borra los registros más viejos hasta dejar ``conservar`` (uno abierto en otra ventana no se puede borrar
    y se deja)."""
    viejos = _registros(carpeta)
    for p in viejos[:max(0, len(viejos) - conservar)]:
        if p == _ruta:
            continue
        try:
            p.unlink()
        except OSError:
            pass


def _crear_registro(carpeta: Path):
    carpeta.mkdir(parents=True, exist_ok=True)
    base = f"sesion-{time.strftime('%Y%m%d-%H%M%S')}"
    for n in range(1, 50):
        ruta = carpeta / (f"{base}.log" if n == 1 else f"{base}-{n}.log")
        try:
            return ruta, open(ruta, "x", encoding="utf-8", errors="replace", newline="\n", buffering=1)
        except FileExistsError:
            continue
    raise OSError("no se pudo crear el registro")


# --------------------------------------------------------------------------
# Escritura
# --------------------------------------------------------------------------

def _hilo() -> str:
    ident = threading.get_ident()
    return "principal" if ident == threading.main_thread().ident else f"hilo {ident % 100000}"


def _escribir(texto: str) -> None:
    global _escritos, _recortado
    with _lock:
        f = _archivo
        if f is None or f.closed:
            return
        if _escritos > MAX_BYTES:
            if not _recortado:
                _recortado = True
                try:
                    f.write("… (el registro llegó a su tamaño máximo; lo que sigue no se anota)\n")
                except Exception:  # noqa: BLE001
                    pass
            return
        try:
            f.write(texto)
            f.flush()
            _escritos += len(texto)
        except Exception:  # noqa: BLE001 — anotar nunca debe romper la aplicación
            pass


def _linea(texto: str) -> None:
    t = time.time()
    marca = time.strftime("%H:%M:%S", time.localtime(t)) + f".{int(t * 1000) % 1000:03d}"
    _escribir(f"{marca} [{_hilo()}] {texto}\n")


def evento(texto: str, memoria: bool = False) -> None:
    """Una transición («pantalla: viewer», «tarea inicia: …»).  ``texto`` no debe traer datos de la persona."""
    if _archivo is None:
        return
    if memoria:
        m = memoria_proceso()
        if m:
            texto = f"{texto} · memoria {m}"
    _linea(texto)


def excepcion(origen: str, info=None, consola: bool = False) -> None:
    """Anota el traceback de una excepción (por defecto, la que se está atendiendo), sin datos de la persona.
    Con ``consola`` también lo escribe tal cual en la consola de errores, si la hay (como traceback.print_exc)."""
    try:
        info = info or sys.exc_info()
        if not info or info[0] is None:
            return
        texto = "".join(traceback.format_exception(*info))
        if _archivo is not None:
            _linea(f"Excepción ({origen}):")
            _escribir("".join(f"    {x}\n" for x in limpiar(texto).rstrip("\n").split("\n")))
        if consola:
            err = sys.stderr
            if err is not None and not isinstance(err, _SalidaRegistro):
                err.write(texto)
    except Exception:  # noqa: BLE001
        pass


class _SalidaRegistro(io.TextIOBase):
    """``sys.stderr`` del ejecutable sin consola: lo que se escriba (los traceback.print_exc) va al registro,
    limpio, en vez de perderse."""

    def __init__(self):
        super().__init__()
        self._pendiente = ""

    def writable(self) -> bool:
        return True

    def write(self, s) -> int:
        s = str(s)
        with _lock:
            self._pendiente += s
            if "\n" not in self._pendiente:
                if len(self._pendiente) > 4000:
                    self._volcar(self._pendiente)
                    self._pendiente = ""
                return len(s)
            completo, _, self._pendiente = self._pendiente.rpartition("\n")
            self._volcar(completo)
        return len(s)

    def flush(self) -> None:
        with _lock:
            if self._pendiente:
                self._volcar(self._pendiente)
                self._pendiente = ""

    @staticmethod
    def _volcar(texto: str) -> None:
        for x in limpiar(texto).split("\n"):
            _linea(f"stderr: {x}")


def salida_errores():
    """Lo que debe ser ``sys.stderr`` cuando no hay consola (el ejecutable): el registro o, sin él, nada."""
    if _archivo is not None:
        return _SalidaRegistro()
    return open(os.devnull, "w", encoding="utf-8")


# --------------------------------------------------------------------------
# Ganchos: Qt, excepciones
# --------------------------------------------------------------------------

def _instalar_qt() -> None:
    from PySide6.QtCore import QtMsgType, qInstallMessageHandler
    nombres = {QtMsgType.QtWarningMsg: "advertencia", QtMsgType.QtCriticalMsg: "error",
               QtMsgType.QtFatalMsg: "fatal"}

    def manejador(tipo, contexto, mensaje):
        # Breve, sin cuadros y seguro entre hilos: Qt lo llama desde cualquier hilo.
        try:
            nombre = nombres.get(tipo)
            if nombre is not None:
                cat = getattr(contexto, "category", None) or ""
                cat = f" [{cat}]" if cat and cat != "default" else ""
                _linea(f"Qt {nombre}{cat}: {limpiar(mensaje)}")
            err = sys.stderr                        # lo que Qt haría sin manejador: escribirlo en la consola
            if err is not None and not isinstance(err, _SalidaRegistro):
                err.write(f"{mensaje}\n")
        except Exception:  # noqa: BLE001
            pass

    _ganchos["qt"] = manejador                      # se guarda la referencia mientras dure la sesión
    qInstallMessageHandler(manejador)
    atexit.register(_quitar_qt)


def _quitar_qt() -> None:
    """Antes de que Python termine: Qt vuelve a su manejador (lo que Qt diga después ya no llega a Python)."""
    if _ganchos.pop("qt", None) is not None:
        try:
            from PySide6.QtCore import qInstallMessageHandler
            qInstallMessageHandler(None)
        except Exception:  # noqa: BLE001
            pass


def _instalar_excepciones() -> None:
    anterior = sys.excepthook

    def gancho(t, e, tb):
        excepcion("sin atrapar", (t, e, tb))
        anterior(t, e, tb)

    sys.excepthook = gancho
    anterior_hilos = threading.excepthook

    def gancho_hilos(args):
        if args.exc_type is not SystemExit:
            excepcion(f"hilo {getattr(args.thread, 'name', '?')}", (args.exc_type, args.exc_value, args.exc_traceback))
        anterior_hilos(args)

    threading.excepthook = gancho_hilos


# --------------------------------------------------------------------------
# Sesión
# --------------------------------------------------------------------------

def activo() -> bool:
    return _archivo is not None


def ruta_registro() -> Path | None:
    return _ruta


def carpeta_registros() -> Path | None:
    return _carpeta


def iniciar(argv: list[str] | None = None, carpeta: str | Path | None = None, *, fallos: bool = True,
            qt: bool = True, ganchos: bool = True) -> bool:
    """Empieza el registro de esta sesión.  Nunca lanza: si no hay dónde escribir, RonyPDF funciona igual.

    ``carpeta`` (las pruebas) reemplaza a ``carpetas()``; ``fallos``, ``qt`` y ``ganchos`` activan faulthandler,
    el manejador de mensajes de Qt y los ganchos de excepciones."""
    global _archivo, _ruta, _carpeta, _anterior, _escritos, _recortado, _terminada
    argv = sys.argv if argv is None else argv
    if _archivo is not None:
        return True
    if len(argv) > 1 and argv[1] == "--comprobar-herramientas":       # build.ps1: no es una sesión de verdad
        return False
    try:
        for c in ([Path(carpeta)] if carpeta else carpetas()):
            try:
                anterior = _leer_marcador(c)
                if c.is_dir():
                    rotar(c, MAX_SESIONES - 1)
                ruta, archivo = _crear_registro(c)
            except OSError:
                continue
            _carpeta, _ruta, _archivo = c, ruta, archivo
            _escritos, _recortado, _terminada = 0, False, False
            _anterior = None
            if anterior is not None:
                pid, creado = _entero(anterior.get("pid")), _entero(anterior.get("creado"))
                if not (pid and pid != os.getpid() and _proceso_vivo(pid, creado)):   # otra ventana abierta
                    _anterior = anterior
            break
        else:
            return False
        _escribir(f"=== {APP_NAME} · sesión iniciada {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
        for linea in _datos_base():
            _escribir(linea + "\n")
        if _anterior is not None:
            _escribir(f"La sesión anterior ({limpiar(_anterior.get('registro', '?'))}) no terminó bien.\n")
        if fallos:
            faulthandler.enable(file=_archivo, all_threads=True)
        try:
            _escribir_marcador(_carpeta, _ruta.name)
        except OSError:
            pass
        if qt:
            _instalar_qt()
            qt_lineas = _versiones_qt()
            if qt_lineas:
                _escribir(" · ".join(qt_lineas) + "\n")
        if ganchos:
            _instalar_excepciones()
        _escribir("---\n")
        return True
    except Exception:  # noqa: BLE001 — el diagnóstico nunca debe impedir abrir RonyPDF
        return _archivo is not None


def entorno() -> None:
    """Con la aplicación de Qt ya creada: lo que no se sabía al iniciar (PyMuPDF, pantallas)."""
    if _archivo is None:
        return
    mu = _version_pymupdf()
    if mu:
        _linea(mu)
    pantallas = _pantallas()
    if pantallas:
        _linea("Monitor: " + "; ".join(pantallas))


def cierre_anterior() -> dict | None:
    """Datos de la sesión anterior si se cerró de forma inesperada (su marcador seguía ahí), o None."""
    return dict(_anterior) if _anterior is not None else None


def olvidar_cierre_anterior() -> None:
    global _anterior
    _anterior = None


def terminar(codigo: int = 0) -> None:
    """Salida normal: lo anota y borra el marcador (si sigue siendo el de esta sesión).  El registro queda
    abierto y faulthandler activo hasta el final del proceso, por si algo falla al desmontar."""
    global _terminada
    if _archivo is None or _terminada:
        return
    _terminada = True
    _linea(f"fin de la sesión (código {codigo})")
    try:
        datos = _leer_marcador(_carpeta)
        if datos is not None and datos.get("registro") == _ruta.name and _entero(datos.get("pid")) == os.getpid():
            (_carpeta / MARCADOR).unlink()
    except OSError:
        pass


def detener() -> None:
    """Deja todo como antes de ``iniciar`` (para las pruebas)."""
    global _archivo, _ruta, _carpeta, _anterior, _terminada
    _quitar_qt()
    with _lock:
        if _archivo is not None:
            try:
                if faulthandler.is_enabled():
                    faulthandler.disable()
            except Exception:  # noqa: BLE001
                pass
            try:
                _archivo.close()
            except Exception:  # noqa: BLE001
                pass
        _archivo = _ruta = _carpeta = _anterior = None
        _terminada = False


# --------------------------------------------------------------------------
# Informe
# --------------------------------------------------------------------------

def generar_informe(carpeta: Path | None = None) -> str:
    """Texto del informe: datos técnicos y los registros de las últimas sesiones, sin contenido de documentos."""
    carpeta = carpeta or _carpeta
    partes = [f"Informe de diagnóstico de {APP_NAME}", "=" * 40,
              f"Creado: {time.strftime('%Y-%m-%d %H:%M:%S')}",
              "Tiene datos técnicos y los registros de las últimas sesiones de RonyPDF en este equipo. No incluye "
              "el contenido de tus documentos, sus nombres ni contraseñas. RonyPDF no lo envía a ningún lado.",
              "", "Datos técnicos", "-" * 14]
    partes += datos_tecnicos()
    if _anterior is not None:
        partes.append(f"Sesión que se cerró de forma inesperada: {limpiar(_anterior.get('registro', '?'))} "
                      f"(iniciada {limpiar(_anterior.get('inicio', '?'))})")
    registros = _registros(carpeta)[-SESIONES_INFORME:] if carpeta is not None and carpeta.is_dir() else []
    for p in registros:
        try:
            with open(p, "rb") as f:
                tam = f.seek(0, os.SEEK_END)
                f.seek(max(0, tam - COLA_INFORME))
                texto = f.read().decode("utf-8", errors="replace")
        except OSError:
            continue
        partes += ["", f"===== {p.name} =====" + (" (solo la parte final)" if tam > COLA_INFORME else "")]
        partes.append(limpiar(texto.replace("\r\n", "\n")).rstrip("\n"))
    if not registros:
        partes += ["", "No hay registros de sesiones anteriores."]
    return "\n".join(partes) + "\n"


def guardar_informe(destino: str | Path) -> Path:
    destino = Path(destino)
    destino.write_text(generar_informe(), encoding="utf-8", newline="\r\n")
    return destino


def nombre_informe() -> str:
    return f"RonyPDF-informe-{time.strftime('%Y%m%d-%H%M%S')}.txt"


def ofrecer_informe(ventana, espera_ms: int = 800) -> None:
    """Con la ventana ya a la vista: si la sesión anterior se cerró de golpe, el aviso para guardar el informe."""
    if _anterior is None:
        return
    from PySide6.QtCore import QTimer

    from .diagnostico_aviso import mostrar_aviso          # Qt Widgets solo aquí (ver la nota del módulo)
    QTimer.singleShot(espera_ms, ventana, lambda: mostrar_aviso(ventana))
