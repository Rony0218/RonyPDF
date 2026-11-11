"""Deja listo un juego de íconos de línea recortado por slice_icons.py, y el logo de la app.

Uso:
  python scripts/finish_icons.py CARPETA_TILES [--logo HOJA_LOGOS.png --logo-cell 1] [--out CARPETA]

1. Paleta exacta: cada píxel pasa a gris pizarra (#1F2937) o rojo de marca (#D63A3A), y su transparencia sale
   de qué tan oscuro era. Así el interior de las figuras queda transparente (la IA lo dibuja gris muy claro) y
   los íconos se ven igual sobre cualquier fondo.
2. Íconos anchos («PDF a imágenes» e «Imágenes a PDF» son dos dibujos lado a lado): se reacomodan en diagonal,
   el de adelante recorta al de atrás y la flecha roja va en la esquina libre, para que llenen el cuadro.
3. Logo: recorta el azulejo rojo de la celda indicada de una hoja 2 × 2 de logos y quita el fondo blanco.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter  # noqa: E402

from ronypdf.art import SHEET_ORDER  # noqa: E402
from slice_icons import from_array, key_out_background, to_array  # noqa: E402

SIZE = 256
SLATE = np.array([0x1F, 0x29, 0x37], dtype=np.float32)
RED = np.array([0xD6, 0x3A, 0x3A], dtype=np.float32)
WIDE = ("toimage", "fromimage")


def clean_palette(a: np.ndarray) -> np.ndarray:
    """RGBA → mismo dibujo con solo dos colores exactos; la tinta (0-1) se vuelve el canal alfa."""
    rgb = a[:, :, :3].astype(np.float32)
    alpha0 = a[:, :, 3].astype(np.float32) / 255
    r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    is_red = (r - np.maximum(g, b)) > 40
    # píxel = t·color + (1 - t)·blanco  →  t = (255 - píxel) / (255 - color), con los canales más informativos
    t_red = (255 - (g + b) / 2) / (255 - (RED[1] + RED[2]) / 2)
    t_slate = (255 - rgb.mean(axis=2)) / (255 - SLATE.mean())
    t = np.where(is_red, t_red, t_slate)
    t = np.clip((t - 0.12) / 0.88, 0, 1) * alpha0  # lo casi blanco (interiores) desaparece
    out = np.zeros_like(a)
    out[:, :, :3] = np.where(is_red[:, :, None], RED, SLATE).astype(np.uint8)
    out[:, :, 3] = (t * 255).round().astype(np.uint8)
    return out


def _bbox_crop(a: np.ndarray, mask: np.ndarray) -> np.ndarray:
    m = a.copy()
    m[:, :, 3] = np.where(mask, a[:, :, 3], 0)
    ys, xs = np.nonzero(m[:, :, 3] > 0)
    return m[ys.min(): ys.max() + 1, xs.min(): xs.max() + 1]


def split_wide(a: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(dibujo izquierdo, dibujo derecho, flecha roja) de un ícono ya limpio."""
    ink = a[:, :, 3] > 0
    red = ink & (a[:, :, 0] > 150)
    dark = ink & ~red
    cols = dark.sum(axis=0)
    rx = np.nonzero(red.sum(axis=0))[0]
    cut = (rx.min() + rx.max()) // 2 if len(rx) else a.shape[1] // 2
    xs = np.nonzero(cols)[0]
    best, run = 0, None
    for x in range(xs.min(), xs.max() + 1):  # el mayor hueco entre columnas con tinta separa los dibujos
        if cols[x] == 0:
            run = x if run is None else run
        elif run is not None:
            if x - run > best:
                best, cut = x - run, (run + x) // 2
            run = None
    left, right = dark.copy(), dark.copy()
    left[:, cut:] = False
    right[:, :cut] = False
    return _bbox_crop(a, left), _bbox_crop(a, right), _bbox_crop(a, red)


def _draw(canvas: QImage, part: np.ndarray, x: float, y: float, k: float) -> QRectF:
    h, w = part.shape[:2]
    rect = QRectF(x, y, w * k, h * k)
    p = QPainter(canvas)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.drawImage(rect, from_array(part))
    p.end()
    return rect


def _thin(a: np.ndarray, steps: int) -> np.ndarray:
    """Adelgaza el trazo (mínimo de 3 × 3 sobre el alfa) para compensar que se agrandó."""
    al = a[:, :, 3].astype(np.int16)
    for _ in range(steps):
        p = np.pad(al, 1, mode="edge")
        al = np.minimum.reduce([p[1:-1, 1:-1], p[:-2, 1:-1], p[2:, 1:-1], p[1:-1, :-2], p[1:-1, 2:]])
    out = a.copy()
    out[:, :, 3] = np.clip(al, 0, 255).astype(np.uint8)
    return out


def recompose(a: np.ndarray) -> np.ndarray:
    back, front, arrow = split_wide(a)
    k = (SIZE * 0.6) / max(back.shape[0], front.shape[0])
    img = QImage(SIZE, SIZE, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    pad = 16
    _draw(img, back, pad, pad, k)
    fw, fh = front.shape[1] * k, front.shape[0] * k
    fx, fy = SIZE - pad - fw, SIZE - pad - fh
    p = QPainter(img)  # el dibujo de adelante recorta al de atrás, con un margen
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
    p.setPen(QColor(0, 0, 0, 0))
    p.setBrush(QColor(0, 0, 0))
    p.drawRoundedRect(QRectF(fx, fy, fw, fh).adjusted(-12, -12, 12, 12), 20, 20)
    p.end()
    _draw(img, front, fx, fy, k)
    ka = 1.8
    aw, ah = arrow.shape[1] * ka, arrow.shape[0] * ka
    ax = pad + 4
    if ax + aw > fx - 12:
        ka = (fx - 12 - ax) / arrow.shape[1]
        aw, ah = arrow.shape[1] * ka, arrow.shape[0] * ka
    _draw(img, arrow, ax, fy + fh * 0.62 - ah / 2, ka)
    return _thin(to_array(img), 1)


def cut_logo(sheet_path: str, cell: int) -> np.ndarray:
    """Azulejo rojo de la celda ``cell`` (0-3, de izquierda a derecha y de arriba abajo) de una hoja 2 × 2."""
    a = to_array(QImage(sheet_path))
    h, w = a.shape[:2]
    r0, c0 = divmod(cell, 2)
    q = a[r0 * h // 2: (r0 + 1) * h // 2, c0 * w // 2: (c0 + 1) * w // 2]
    r, g = q[:, :, 0].astype(int), q[:, :, 1].astype(int)
    red = (r > 180) & (r - g > 80)
    ys, xs = np.nonzero(red)
    crop = q[max(0, ys.min() - 3): ys.max() + 4, max(0, xs.min() - 3): xs.max() + 4]
    keyed = key_out_background(crop, 235)
    side = max(keyed.shape[:2])
    sq = np.zeros((side, side, 4), dtype=np.uint8)
    oy, ox = (side - keyed.shape[0]) // 2, (side - keyed.shape[1]) // 2
    sq[oy: oy + keyed.shape[0], ox: ox + keyed.shape[1]] = keyed
    img = QImage(SIZE, SIZE, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.drawImage(QRectF(0, 0, SIZE, SIZE), from_array(sq))
    p.end()
    return to_array(img)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tiles", help="carpeta con los 25 PNG recortados por slice_icons.py")
    ap.add_argument("--out", default=str(ROOT / "ronypdf" / "assets" / "tiles"))
    ap.add_argument("--logo", help="hoja 2 × 2 de logos de la que sale logo.png")
    ap.add_argument("--logo-cell", type=int, default=1)
    args = ap.parse_args()

    app = QGuiApplication([])  # noqa: F841
    src, out = Path(args.tiles), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for key in SHEET_ORDER:
        if key == "logo" and args.logo:
            arr = cut_logo(args.logo, args.logo_cell)
        else:
            arr = clean_palette(to_array(QImage(str(src / f"{key}.png"))))
            if key in WIDE:
                arr = recompose(arr)
        from_array(arr).save(str(out / f"{key}.png"))
    print(f"{len(SHEET_ORDER)} íconos listos en {out}")


if __name__ == "__main__":
    main()
