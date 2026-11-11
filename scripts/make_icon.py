"""Genera el ícono .ico de la aplicación a partir del logotipo dibujado con Qt."""

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QBuffer, QIODevice  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

from ronypdf.art import tile  # noqa: E402


def logo_pixmap(size: int):
    return tile("logo", size, 1.0)


def main(out: str) -> None:
    app = QGuiApplication([])  # noqa: F841
    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = []
    for s in sizes:
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        logo_pixmap(s).save(buf, "PNG")
        images.append(bytes(buf.data()))
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = b""
    for s, data in zip(sizes, images):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_bytes(header + entries + b"".join(images))
    print(f"Ícono creado: {out}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "build/app.ico")
