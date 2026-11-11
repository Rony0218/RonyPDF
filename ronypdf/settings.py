"""Ajustes portables: se guardan en un archivo JSON junto al ejecutable.

Si esa carpeta no permite escribir (por ejemplo, el .exe está en una carpeta protegida),
se usa %APPDATA%\\RonyPDF como respaldo. Nunca se toca el registro de Windows.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

FILE_NAME = "RonyPDF.json"
MAX_RECENT = 8


def _candidates() -> list[Path]:
    override = os.environ.get("RONYPDF_SETTINGS")
    if override:
        return [Path(override)]
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parent.parent
    out = [base / FILE_NAME]
    appdata = os.environ.get("APPDATA")
    if appdata:
        out.append(Path(appdata) / "RonyPDF" / FILE_NAME)
    return out


class Settings:
    def __init__(self, path: Path | None = None):
        self._path = path
        self.data: dict = {}
        self.load()

    @property
    def path(self) -> Path:
        if self._path is None:
            cands = _candidates()
            existing = [p for p in cands if p.exists()]
            self._path = existing[0] if existing else cands[0]
        return self._path

    def load(self) -> None:
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(self.data, dict):
                self.data = {}
        except (OSError, ValueError):
            self.data = {}

    def save(self) -> None:
        text = json.dumps(self.data, ensure_ascii=False, indent=2)
        for target in [self.path] + [p for p in _candidates() if p != self.path]:
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp = target.with_suffix(".tmp")
                tmp.write_text(text, encoding="utf-8")
                os.replace(tmp, target)
                self._path = target
                return
            except OSError:
                continue

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def set(self, key: str, value) -> None:
        self.data[key] = value
        self.save()

    # ---------------------------------------------------------------- recientes
    def recent(self) -> list[dict]:
        items = self.data.get("recent", [])
        return [r for r in items if isinstance(r, dict) and r.get("path")]

    def add_recent(self, path: str, pages: int | None = None) -> None:
        if not self.data.get("recent_enabled", True):
            return
        path = os.path.abspath(path)
        key = os.path.normcase(path)
        items = [r for r in self.recent() if os.path.normcase(r["path"]) != key]
        entry = {"path": path, "opened": time.time()}
        if pages:
            entry["pages"] = pages
        items.insert(0, entry)
        self.data["recent"] = items[:MAX_RECENT]
        self.save()

    def remove_recent(self, path: str) -> None:
        key = os.path.normcase(os.path.abspath(path))
        self.data["recent"] = [r for r in self.recent() if os.path.normcase(r["path"]) != key]
        self.save()

    def clear_recent(self) -> None:
        self.data["recent"] = []
        self.save()
