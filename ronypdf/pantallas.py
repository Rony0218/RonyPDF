"""Las pantallas de la ventana principal, construidas la primera vez que se abren.

Construir las 27 pantallas al arrancar tomaba varios segundos en un equipo modesto; casi nadie las usa todas
en una sesión.  ``MainWindow.pages`` es un ``Pantallas``:

- ``clave in pages``, ``len(pages)`` e iterar ven todas las pantallas registradas, construidas o no.
- ``pages[clave]`` la construye si todavía no existe (y avisa a ``al_construir``, que la agrega al
  QStackedWidget).  Así siguen funcionando ``win.pages["viewer"].load(...)`` y compañía.
- ``pages.get(clave)`` devuelve la pantalla solo si ya está construida (None si no).  Es lo que necesita quien
  pregunta «¿tiene algo abierto?» o «¿tiene cambios sin guardar?»: una pantalla que nunca se abrió no tiene
  nada, y construirla solo para preguntarle haría lento el cierre.
- ``items()`` y ``values()`` construyen todas (como pedirlas una por una): úsalos solo si de verdad hace falta.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping

from PySide6.QtWidgets import QWidget


class Pantallas(Mapping):
    def __init__(self, claves: list[str], fabrica: Callable[[str], QWidget],
                 al_construir: Callable[[str, QWidget], None]):
        self._claves = list(dict.fromkeys(claves))
        self._fabrica = fabrica
        self._al_construir = al_construir
        self._hechas: dict[str, QWidget] = {}
        self._construyendo: set[str] = set()

    def registrar(self, clave: str, pagina: QWidget) -> None:
        """Agrega una pantalla ya construida (p. ej. el inicio)."""
        if clave not in self._claves:
            self._claves.append(clave)
        self._hechas[clave] = pagina
        self._al_construir(clave, pagina)

    def __getitem__(self, clave: str) -> QWidget:
        pagina = self._hechas.get(clave)
        if pagina is not None:
            return pagina
        if clave not in self._claves:
            raise KeyError(clave)
        if clave in self._construyendo:
            raise RuntimeError(f"La pantalla «{clave}» se pidió a sí misma mientras se construía")
        self._construyendo.add(clave)
        try:
            pagina = self._fabrica(clave)
        finally:
            self._construyendo.discard(clave)
        self._hechas[clave] = pagina
        self._al_construir(clave, pagina)
        return pagina

    def get(self, clave: str, default=None):
        """La pantalla si ya se construyó; si no, ``default`` (no la construye)."""
        return self._hechas.get(clave, default)

    def construida(self, clave: str) -> bool:
        return clave in self._hechas

    def construidas(self) -> dict[str, QWidget]:
        """Las pantallas ya construidas, en el orden del menú."""
        return {k: self._hechas[k] for k in self._claves if k in self._hechas}

    def __contains__(self, clave) -> bool:
        return clave in self._claves

    def __iter__(self) -> Iterator[str]:
        return iter(list(self._claves))

    def __len__(self) -> int:
        return len(self._claves)

    def __repr__(self) -> str:
        return f"Pantallas({len(self._hechas)} de {len(self._claves)} construidas)"
