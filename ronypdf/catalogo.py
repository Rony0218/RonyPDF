"""Textos del catálogo de herramientas: el nombre corto y la línea de cada tarjeta de Inicio, y los sinónimos
en español coloquial (de Colombia) con los que la paleta Ctrl K encuentra cada herramienta.

Una herramienta nueva que no esté aquí funciona igual: su tarjeta usa el título del menú y la descripción
``inicio`` de su REGISTRO (ver extensiones.py), y la paleta la encuentra por su título.
"""

from __future__ import annotations

import unicodedata

# clave → (título corto de la tarjeta, una línea escrita desde la tarea)
TARJETAS: dict[str, tuple[str, str]] = {
    "viewer": ("Ver y editar", "Lee, escribe, resalta y firma"),
    "forms": ("Formularios y firma", "Rellena y firma sin imprimir"),
    "print": ("Imprimir", "Varias páginas por hoja, a doble cara"),
    "compare": ("Comparar dos PDF", "Qué cambió entre dos versiones"),
    "merge": ("Unir PDF", "Varios archivos en uno solo"),
    "split": ("Dividir PDF", "Saca páginas o parte por rangos"),
    "organize": ("Organizar páginas", "Reordena, gira y elimina"),
    "rename": ("Renombrar", "Nombres con fecha, NIT o número"),
    "compress": ("Comprimir PDF", "Para enviarlo por correo"),
    "batch": ("Acciones por lotes", "Varios pasos a muchos archivos"),
    "ocr": ("Reconocer texto", "Vuelve buscable un escaneo"),
    "ocrcheck": ("Revisar texto", "Corrige las palabras dudosas"),
    "toimage": ("PDF a imágenes", "Cada página en JPG o PNG"),
    "fromimage": ("Imágenes a PDF", "Fotos y escaneos en un PDF"),
    "extract": ("Extraer texto", "Copia el texto y las fotos"),
    "office2pdf": ("Word y Excel a PDF", "Convierte documentos de Office"),
    "pdf2office": ("PDF a Word y Excel", "Para seguir editando"),
    "watermark": ("Marca de agua", "Texto o logo en cada página"),
    "numbers": ("Numerar páginas", "Pie de página con «1 de 12»"),
    "metadata": ("Propiedades", "Título, autor y palabras clave"),
    "stamps": ("Mis sellos", "Tu nombre, la fecha y tu firma"),
    "protect": ("Proteger", "Ponle contraseña al archivo"),
    "unlock": ("Quitar contraseña", "Si ya la conoces"),
    "redact": ("Censurar datos", "Cédulas, celulares y correos"),
    "sanitize": ("Quitar datos ocultos", "Autor, historial y comentarios"),
    "backup": ("Respaldo", "Copias y trabajo sin guardar"),
}

# clave → palabras con las que la gente pide la herramienta (la primera que coincide se muestra en «También:»)
SINONIMOS: dict[str, list[str]] = {
    "viewer": ["firmar", "escribir", "resaltar", "subrayar", "abrir", "leer", "anotar", "editar texto",
               "corregir", "tachar"],
    "forms": ["llenar", "rellenar", "diligenciar", "firmar formulario", "formulario", "campos", "firma"],
    "print": ["impresora", "sacar copia", "doble cara", "varias por hoja", "folleto", "cartilla"],
    "compare": ["diferencias", "qué cambió", "versiones", "cotejar", "revisar cambios"],
    "merge": ["juntar", "combinar", "pegar archivos", "un solo pdf", "agrupar", "unir"],
    "split": ["separar", "cortar", "partir", "sacar páginas", "extraer páginas", "dividir"],
    "organize": ["ordenar", "rotar", "girar", "voltear", "borrar páginas", "eliminar hojas", "mover páginas",
                 "reordenar", "página en blanco"],
    "rename": ["cambiar nombre", "nombre por fecha", "nit", "número de factura", "organizar archivos",
               "renombrar"],
    "compress": ["achicar", "bajar peso", "reducir", "muy pesado", "pesa mucho", "liviano", "correo",
                 "comprimir"],
    "batch": ["muchos archivos juntos", "varios", "carpeta entera", "lote", "masivo", "automatizar"],
    "ocr": ["escaneado", "texto de una foto", "buscar en escaneo", "digitalizar", "copiar de un escaneo",
            "reconocer"],
    "ocrcheck": ["palabras dudosas", "corregir el ocr", "errores del escaneo", "revisar ocr"],
    "toimage": ["pasar a jpg", "pasar a png", "pantallazo", "foto de la página", "convertir a imagen"],
    "fromimage": ["juntar fotos", "fotos", "escaneos", "fotos del celular", "jpg a pdf", "imagen"],
    "extract": ["sacar texto", "copiar texto", "sacar fotos", "extraer imágenes", "txt"],
    "office2pdf": ["word", "excel", "docx", "xlsx", "powerpoint", "pasar a pdf", "convertir a pdf"],
    "pdf2office": ["pasar a word", "pasar a excel", "editable", "convertir a word", "tabla a excel"],
    "watermark": ["sello de agua", "confidencial", "borrador", "copia controlada", "logo en cada página"],
    "numbers": ["numerar", "paginar", "foliar", "número de página", "pie de página"],
    "metadata": ["título", "autor", "palabras clave", "metadatos", "datos del archivo"],
    "stamps": ["sello", "recibido", "aprobado", "radicado", "visto bueno", "firma escaneada"],
    "protect": ["clave", "contraseña", "bloquear", "cifrar", "encriptar", "poner clave"],
    "unlock": ["quitar clave", "desbloquear", "clave", "abrir sin clave", "olvidé quitar la clave"],
    "redact": ["tapar cédulas", "tapar", "ocultar datos", "anonimizar", "cédula", "celulares",
               "datos personales", "habeas data", "borrar datos"],
    "sanitize": ["quitar autor", "limpiar", "metadatos", "historial", "comentarios ocultos", "privacidad"],
    "backup": ["recuperar", "copia de seguridad", "se cerró", "se perdió", "versión anterior", "autoguardado",
               "respaldo"],
}

# sugerencias cuando la paleta se abre sin escribir nada
SUGERIDAS = ["viewer", "merge", "compress", "protect", "redact"]


def normalizar(texto: str) -> str:
    """Minúsculas y sin tildes: «Cédula» y «cedula» son lo mismo."""
    t = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def tarjeta(clave: str, titulo_menu: str, descripcion: str | None = None) -> tuple[str, str]:
    """(título corto, línea) de la tarjeta de Inicio de una herramienta."""
    if clave in TARJETAS:
        return TARJETAS[clave]
    return titulo_menu, descripcion or ""


def buscar(consulta: str, herramientas: list[tuple[str, str, str]], limite: int = 5) -> list[dict]:
    """Herramientas que coinciden con ``consulta``.

    ``herramientas`` = [(clave, título, categoría)] en el orden de preferencia.  Devuelve diccionarios con
    ``clave``, ``titulo``, ``cat`` y la línea «por qué»: ``pre``, ``match`` (lo encontrado, va en negrita) y
    ``post``.  Sin tildes ni mayúsculas; varias palabras deben aparecer todas."""
    q = normalizar(consulta.strip())
    if not q:
        return []
    from .tema import NOMBRES_CATEGORIA
    palabras = q.split()
    puntuadas = []
    for orden, (clave, titulo, cat) in enumerate(herramientas):
        nt = normalizar(titulo)
        sin = SINONIMOS.get(clave, [])
        r = None
        k = nt.find(q)
        if k >= 0:
            pts = 0 if k == 0 else 1 if nt[k - 1] == " " else 2
            r = (pts, {"pre": f"En {NOMBRES_CATEGORIA.get(cat, cat)}", "match": "", "post": ""})
        else:
            for s in sin:
                ns = normalizar(s)
                k = ns.find(q)
                if k >= 0:
                    pts = 3 if k == 0 or ns[k - 1] == " " else 4
                    r = (pts, {"pre": "También: " + s[:k], "match": s[k:k + len(q)], "post": s[k + len(q):]})
                    break
        if r is None and len(palabras) > 1:
            todo = " ".join([nt] + [normalizar(s) for s in sin])
            if all(p in todo for p in palabras):
                s = next((x for x in sin if palabras[0] in normalizar(x)), None)
                if s:
                    k = normalizar(s).find(palabras[0])
                    r = (5, {"pre": "También: " + s[:k], "match": s[k:k + len(palabras[0])],
                             "post": s[k + len(palabras[0]):]})
                else:
                    r = (5, {"pre": f"En {NOMBRES_CATEGORIA.get(cat, cat)}", "match": "", "post": ""})
        if r is not None:
            pts, why = r
            puntuadas.append((pts, orden, dict(clave=clave, titulo=titulo, cat=cat, **why)))
    puntuadas.sort(key=lambda x: (x[0], x[1]))
    return [d for _p, _o, d in puntuadas[:limite]]
