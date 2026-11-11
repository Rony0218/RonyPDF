"""Caché de imágenes con presupuesto en bytes, para que un PDF de cientos de páginas no llene la memoria de un
equipo de 4 GB.

Las miniaturas del visor y del organizador y los recortes de la revisión del OCR se guardan aquí: cuando la
suma pasa del presupuesto, se sueltan las que se usaron hace más tiempo (y se vuelven a dibujar si hacen falta).
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Hashable, Iterator

MB = 1024 * 1024
MINIATURAS = 64 * MB          # presupuesto de miniaturas por pantalla (visor, organizador)
MAX_PIXELES = 8_000_000       # tope de una imagen de página renderizada para verla (unos 24 MB en RGB)
_NADA = object()


def tamano(img) -> int:
    """Bytes que ocupa una QImage o QPixmap (0 para None; en una tupla, la suma de sus imágenes)."""
    if img is None:
        return 0
    if isinstance(img, tuple):
        return sum(tamano(x) for x in img)
    try:
        return int(img.sizeInBytes())                      # QImage
    except AttributeError:
        try:
            return int(img.width() * img.height() * max(img.depth(), 8) // 8)   # QPixmap
        except AttributeError:
            return 0


class CacheImagenes:
    """Diccionario LRU de imágenes con presupuesto en bytes.

    ``cache[clave] = img`` guarda (``None`` también vale: p. ej. «esta página no se pudo dibujar»; y una tupla
    que lleve imágenes, como ``(img, zoom)``); ``cache.get(clave)`` la marca como usada hace poco.  Si la suma
    pasa de ``presupuesto`` (o hay más de ``maximo`` entradas), se sueltan las menos usadas, salvo la que se
    acaba de guardar."""

    def __init__(self, presupuesto: int = MINIATURAS, maximo: int | None = None):
        self.presupuesto = int(presupuesto)
        self.maximo = maximo
        self._datos: OrderedDict[Hashable, object] = OrderedDict()
        self._bytes: dict[Hashable, int] = {}
        self.bytes = 0

    def get(self, clave, default=None):
        img = self._datos.get(clave, _NADA)
        if img is _NADA:
            return default
        self._datos.move_to_end(clave)
        return img

    def __getitem__(self, clave):
        img = self.get(clave, _NADA)
        if img is _NADA:
            raise KeyError(clave)
        return img

    def __setitem__(self, clave, img) -> None:
        self.pop(clave)
        n = tamano(img)
        self._datos[clave] = img
        self._bytes[clave] = n
        self.bytes += n
        while len(self._datos) > 1 and (self.bytes > self.presupuesto
                                        or (self.maximo is not None and len(self._datos) > self.maximo)):
            vieja = next(iter(self._datos))
            if vieja == clave:
                break
            self.pop(vieja)

    def pop(self, clave, default=None):
        if clave not in self._datos:
            return default
        self.bytes -= self._bytes.pop(clave, 0)
        return self._datos.pop(clave)

    def __delitem__(self, clave) -> None:
        if clave not in self._datos:
            raise KeyError(clave)
        self.pop(clave)

    def clear(self) -> None:
        self._datos.clear()
        self._bytes.clear()
        self.bytes = 0

    def __contains__(self, clave) -> bool:
        return clave in self._datos

    def __len__(self) -> int:
        return len(self._datos)

    def __iter__(self) -> Iterator:
        return iter(list(self._datos))

    def keys(self):
        return list(self._datos)


def zoom_tope(ancho: float, alto: float, zoom: float, max_pixeles: int = MAX_PIXELES) -> float:
    """``zoom`` reducido lo necesario para que una página de ``ancho`` × ``alto`` puntos no pase de
    ``max_pixeles`` al renderizarla (una página grande con zoom alto puede pedir cientos de MB)."""
    area = max(ancho, 1.0) * max(alto, 1.0)
    return max(0.05, min(zoom, (max_pixeles / area) ** 0.5))


def ventana(primera: int, ultima: int, total: int, margen: int) -> list[int]:
    """Filas por dibujar en orden de prioridad: las visibles (``primera``…``ultima``, de arriba abajo) y luego
    ``margen`` filas a cada lado, de la más cercana a la más lejana (primero hacia abajo)."""
    if total <= 0:
        return []
    primera = max(0, min(primera, total - 1))
    ultima = max(primera, min(ultima, total - 1))
    filas = list(range(primera, ultima + 1))
    for d in range(1, margen + 1):
        if ultima + d < total:
            filas.append(ultima + d)
        if primera - d >= 0:
            filas.append(primera - d)
    return filas
