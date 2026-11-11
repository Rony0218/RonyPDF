"""Punto de entrada: python run.py [archivos...]"""

import sys

from ronypdf import diagnostico

# Antes de importar la interfaz: si el proceso muere (también durante las importaciones), queda registro.
diagnostico.iniciar()

from ronypdf.gui import main  # noqa: E402

if __name__ == "__main__":
    codigo = main()
    diagnostico.terminar(codigo)
    sys.exit(codigo)
