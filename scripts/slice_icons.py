"""Recorta una hoja de íconos generada con IA (5 × 5) en PNG transparentes.

Uso:
  python scripts/slice_icons.py HOJA.png [--out CARPETA] [--uniform] [--no-clip] [--detect 200] [--key 222]

El juego actual de RonyPDF sale de:
  python scripts/slice_icons.py docs/diseno/fuentes/hoja-iconos-F.png --uniform --out build/tiles-F
  python scripts/finish_icons.py build/tiles-F --logo docs/diseno/fuentes/logos-ronypdf.png

Por defecto escribe en ronypdf/assets/tiles/<clave>.png (256 × 256) y una vista previa
junto a la hoja. --no-clip es para estilos sin azulejo (por ejemplo, objetos 3D).
--detect y --key ajustan qué tan claro debe ser un píxel para contarlo como fondo
(súbelos para estilos pastel muy claros).
"""

import argparse
import os
import sys
from pathlib import Path

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter, QPainterPath  # noqa: E402

from ronypdf.art import SHEET_ORDER  # noqa: E402

SIZE = 256


def runs(profile: np.ndarray, min_len: int = 40) -> list[tuple[int, int]]:
    out, start = [], None
    for i, v in enumerate(profile):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start >= min_len:
                out.append((start, i))
            start = None
    if start is not None and len(profile) - start >= min_len:
        out.append((start, len(profile)))
    return out


def to_array(img: QImage) -> np.ndarray:
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = img.width(), img.height()
    buf = np.frombuffer(img.constBits(), dtype=np.uint8, count=img.bytesPerLine() * h)
    return buf.reshape(h, img.bytesPerLine())[:, : w * 4].reshape(h, w, 4).copy()


def from_array(arr: np.ndarray) -> QImage:
    h, w, _ = arr.shape
    arr = np.ascontiguousarray(arr)
    return QImage(arr.data, w, h, w * 4, QImage.Format.Format_RGBA8888).copy()


def key_out_background(tile: np.ndarray, key: int) -> np.ndarray:
    """Hace transparente el fondo claro conectado con los bordes; el borde queda suavizado."""
    rgb = tile[:, :, :3].astype(np.int16)
    mn = rgb.min(axis=2)
    light = mn > key
    reach = np.zeros_like(light)
    reach[0, :], reach[-1, :], reach[:, 0], reach[:, -1] = light[0, :], light[-1, :], light[:, 0], light[:, -1]
    while True:
        grown = reach.copy()
        grown[1:, :] |= reach[:-1, :]
        grown[:-1, :] |= reach[1:, :]
        grown[:, 1:] |= reach[:, :-1]
        grown[:, :-1] |= reach[:, 1:]
        grown &= light
        if (grown == reach).all():
            break
        reach = grown
    out = tile.copy()
    alpha = np.full(mn.shape, 255, dtype=np.uint8)
    alpha[reach] = 0
    # anillo de 1 px junto al fondo: alfa proporcional a lo oscuro que es (antialias)
    edge = np.zeros_like(reach)
    edge[1:, :] |= reach[:-1, :]
    edge[:-1, :] |= reach[1:, :]
    edge[:, 1:] |= reach[:, :-1]
    edge[:, :-1] |= reach[:, 1:]
    edge &= ~reach
    soft = np.clip((255 - mn) * 255 // max(1, 255 - key), 0, 255).astype(np.uint8)
    alpha[edge] = np.minimum(alpha[edge], np.maximum(soft[edge], 60))
    out[:, :, 3] = alpha
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("sheet", nargs="?", default=str(ROOT / "docs" / "diseno" / "fuentes" / "hoja-iconos-F.png"))
    ap.add_argument("--out", default=str(ROOT / "ronypdf" / "assets" / "tiles"))
    ap.add_argument("--no-clip", action="store_true", help="no recortar con esquinas redondeadas")
    ap.add_argument("--detect", type=int, default=200, help="umbral para detectar contenido (0-255)")
    ap.add_argument("--key", type=int, default=222, help="umbral de fondo claro a quitar (0-255)")
    ap.add_argument("--preview", default=None, help="ruta de la vista previa (por defecto junto a la hoja)")
    ap.add_argument("--uniform", action="store_true",
                    help="íconos de línea: recorta todos con el mismo cuadro, para conservar tamaño y grosor")
    args = ap.parse_args()

    app = QGuiApplication([])  # noqa: F841
    sheet = QImage(args.sheet)
    if sheet.isNull():
        raise SystemExit(f"No se pudo leer {args.sheet}")
    arr = to_array(sheet)
    strong = arr[:, :, :3].min(axis=2) < args.detect
    cols = runs(strong.sum(axis=0) > 3)
    rows = runs(strong.sum(axis=1) > 3)
    if len(cols) != 5 or len(rows) != 5:
        raise SystemExit(f"Se esperaban 5×5 íconos y se detectaron {len(rows)}×{len(cols)} "
                         f"(prueba con otro --detect)")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    preview = QImage(5 * 72 + 6 * 12, 5 * 72 + 6 * 12, QImage.Format.Format_ARGB32)
    preview.fill(Qt.GlobalColor.white)
    pp = QPainter(preview)
    pp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    # Para --uniform: cuadro común = el mayor ícono de la hoja, con un pequeño margen.
    side_common = 0
    if args.uniform:
        for (y0, y1) in rows:
            for (x0, x1) in cols:
                cell = strong[y0:y1, x0:x1]
                ys, xs = np.nonzero(cell)
                if len(ys):
                    side_common = max(side_common, ys.max() - ys.min() + 1, xs.max() - xs.min() + 1)
        side_common = int(side_common * 1.08)
    for r, (y0, y1) in enumerate(rows):
        for c, (x0, x1) in enumerate(cols):
            key = SHEET_ORDER[r * 5 + c]
            pad = 8
            if args.uniform:
                cell = strong[y0:y1, x0:x1]
                ys, xs = np.nonzero(cell)
                cy = y0 + (ys.min() + ys.max()) // 2
                cx = x0 + (xs.min() + xs.max()) // 2
                half = side_common // 2
                Y0, X0 = max(0, cy - half), max(0, cx - half)
                crop = arr[Y0: Y0 + side_common, X0: X0 + side_common]
                keyed = key_out_background(crop, args.key)
            else:
                crop = arr[max(0, y0 - pad): y1 + pad, max(0, x0 - pad): x1 + pad]
                keyed = key_out_background(crop, args.key)
                ys, xs = np.nonzero(keyed[:, :, 3] > 0)
                keyed = keyed[ys.min(): ys.max() + 1, xs.min(): xs.max() + 1]
            h, w = keyed.shape[:2]
            side = max(h, w)
            square = np.zeros((side, side, 4), dtype=np.uint8)
            oy, ox = (side - h) // 2, (side - w) // 2
            square[oy: oy + h, ox: ox + w] = keyed
            img = from_array(square)
            final = QImage(SIZE, SIZE, QImage.Format.Format_ARGB32_Premultiplied)
            final.fill(Qt.GlobalColor.transparent)
            p = QPainter(final)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            if not args.no_clip:
                clip = QPainterPath()
                clip.addRoundedRect(QRectF(0, 0, SIZE, SIZE), SIZE * 0.2, SIZE * 0.2)
                p.setClipPath(clip)
            p.drawImage(QRectF(0, 0, SIZE, SIZE), img)
            p.end()
            final.save(str(out / f"{key}.png"))
            pp.drawImage(QRectF(12 + c * 84, 12 + r * 84, 72, 72), final)
    pp.end()
    prev_path = args.preview or str(Path(args.sheet).with_name(Path(args.sheet).stem + "-recortes.png"))
    preview.save(prev_path)
    print(f"25 íconos guardados en {out}")


if __name__ == "__main__":
    main()
