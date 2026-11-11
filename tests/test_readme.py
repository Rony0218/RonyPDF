"""El botón de descarga del README lleva directo al zip de la versión actual.

Al subir la versión en ronypdf/__init__.py hay que actualizar también esos enlaces; si no, esta prueba falla y la
publicación no sigue (el flujo «Publicar versión» corre las pruebas antes de compilar).
"""
import re
from pathlib import Path

from ronypdf import __version__

RAIZ = Path(__file__).resolve().parent.parent
README = (RAIZ / "README.md").read_text(encoding="utf-8")


def test_enlace_directo_al_zip_de_esta_version():
    enlaces = re.findall(r"releases/download/v([\d.]+)/RonyPDF-([\d.]+)-portable\.zip", README)
    assert enlaces, "El README no tiene el enlace directo al zip"
    assert all(etiqueta == nombre == __version__ for etiqueta, nombre in enlaces), (enlaces, __version__)


def test_textos_de_version_al_dia():
    versiones = re.findall(r"Descargar RonyPDF ([\d.]+) para Windows|Versión ([\d.]+) ·", README)
    assert versiones, "El README no dice qué versión descarga el botón"
    assert {v for par in versiones for v in par if v} == {__version__}


def test_imagenes_de_descarga_existen():
    for ruta in re.findall(r'src="(docs/descarga/[^"]+)"', README):
        assert (RAIZ / ruta).is_file(), ruta
