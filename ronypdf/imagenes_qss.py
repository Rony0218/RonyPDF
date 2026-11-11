"""Imágenes pequeñas para la hoja QSS (casillas, botones de opción, flechas), sin depender de Qt.

QSS solo dibuja palomitas y flechas con ``image: url(...)``.  Estas imágenes se generan en Python puro
(campos de distancia con borde suavizado y un codificador PNG mínimo), así existen antes de que haya una
QApplication y sirven para cualquier tema: el nombre del archivo lleva los colores.  Se escriben una sola
vez en la carpeta temporal del sistema, en tamaño normal y ``@2x`` (Qt elige la de alta densidad).
"""

from __future__ import annotations

import math
import os
import struct
import tempfile
import zlib
from pathlib import Path

VERSION = "1"


def _carpeta() -> Path:
    d = Path(tempfile.gettempdir()) / f"ronypdf-qss-{VERSION}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _png(w: int, h: int, px: list[tuple[float, float, float, float]]) -> bytes:
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        for x in range(w):
            r, g, b, a = px[y * w + x]
            raw += bytes((int(r + 0.5), int(g + 0.5), int(b + 0.5), int(a * 255 + 0.5)))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


# ---------------------------------------------------------------- campos de distancia (en px del lienzo)

def _d_seg(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy or 1)))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def _d_rrect(px, py, cx, cy, hw, hh, r):
    qx, qy = abs(px - cx) - hw + r, abs(py - cy) - hh + r
    return math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - r


def _render(size: int, capas) -> list:
    """``capas`` = [(color, funcion_distancia)]: cada capa se pinta encima con borde suavizado."""
    out = []
    for y in range(size):
        for x in range(size):
            px, py = x + 0.5, y + 0.5
            r = g = b = 0.0
            a = 0.0
            for col, dist in capas:
                cov = max(0.0, min(1.0, 0.5 - dist(px, py)))
                if cov <= 0:
                    continue
                cr, cg, cb, ca = col
                sa = cov * ca
                na = sa + a * (1 - sa)
                if na > 0:
                    r = (cr * sa + r * a * (1 - sa)) / na
                    g = (cg * sa + g * a * (1 - sa)) / na
                    b = (cb * sa + b * a * (1 - sa)) / na
                a = na
            out.append((r, g, b, a))
    return out


def _escribir(nombre: str, dibujo) -> str:
    """Escribe ``nombre.png`` y ``nombre@2x.png`` (si no existen) y devuelve la ruta para QSS."""
    d = _carpeta()
    for escala, sufijo in ((1, ""), (2, "@2x")):
        f = d / f"{nombre}{sufijo}.png"
        if not f.exists():
            size, capas = dibujo(escala)
            tmp = f.with_suffix(f".{os.getpid()}.tmp")
            try:
                tmp.write_bytes(_png(size, size, _render(size, capas)))
                os.replace(tmp, f)
            except OSError:                    # otro proceso la está escribiendo: basta con la suya
                tmp.unlink(missing_ok=True)
    return (d / f"{nombre}.png").as_posix()


def _c(h: str, alfa: float = 1.0):
    r, g, b = _rgb(h)
    return (r, g, b, alfa)


# ---------------------------------------------------------------- imágenes

def casilla(fondo: str, palomita: str, alfa: float = 1.0) -> str:
    """Casilla marcada de 18 px: cuadrado redondeado (radio 6) relleno y palomita."""
    def dibujo(s):
        n = 18 * s
        k = s
        return n, [
            (_c(fondo, alfa), lambda x, y: _d_rrect(x, y, n / 2, n / 2, n / 2, n / 2, 6 * k)),
            (_c(palomita, alfa), lambda x, y: min(_d_seg(x, y, 5 * k, 9.4 * k, 7.8 * k, 12.2 * k),
                                                  _d_seg(x, y, 7.8 * k, 12.2 * k, 13 * k, 6.4 * k)) - 1.0 * k),
        ]
    return _escribir(f"casilla-{fondo[1:]}-{palomita[1:]}-{int(alfa * 100)}", dibujo)


def casilla_vacia(borde: str, fondo: str, alfa: float = 1.0) -> str:
    def dibujo(s):
        n = 18 * s
        return n, [
            (_c(borde, alfa), lambda x, y: _d_rrect(x, y, n / 2, n / 2, n / 2, n / 2, 6 * s)),
            (_c(fondo, alfa), lambda x, y: _d_rrect(x, y, n / 2, n / 2, n / 2 - s, n / 2 - s, 5 * s)),
        ]
    return _escribir(f"casillav-{borde[1:]}-{fondo[1:]}-{int(alfa * 100)}", dibujo)


def opcion(borde: str, centro: str, grosor: float, alfa: float = 1.0) -> str:
    """Botón de opción de 18 px: círculo con borde de ``grosor`` px y centro de otro color."""
    def dibujo(s):
        n = 18 * s
        return n, [
            (_c(borde, alfa), lambda x, y: math.hypot(x - n / 2, y - n / 2) - n / 2),
            (_c(centro, alfa), lambda x, y: math.hypot(x - n / 2, y - n / 2) - (n / 2 - grosor * s)),
        ]
    return _escribir(f"opcion-{borde[1:]}-{centro[1:]}-{int(grosor * 10)}-{int(alfa * 100)}", dibujo)


def flecha(color: str, direccion: str = "abajo", tam: int = 12) -> str:
    """Chevrón de ``tam`` px (para listas desplegables y campos numéricos)."""
    def dibujo(s):
        n = tam * s
        m = n / 2
        a = n * 0.28
        if direccion == "abajo":
            p1, p2, p3 = (m - a, m - a / 2), (m, m + a / 2), (m + a, m - a / 2)
        else:
            p1, p2, p3 = (m - a, m + a / 2), (m, m - a / 2), (m + a, m + a / 2)
        w = 0.8 * s
        return n, [(_c(color), lambda x, y: min(_d_seg(x, y, *p1, *p2), _d_seg(x, y, *p2, *p3)) - w)]
    return _escribir(f"flecha-{direccion}-{color[1:]}-{tam}", dibujo)
