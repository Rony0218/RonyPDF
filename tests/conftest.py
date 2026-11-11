import sys
from pathlib import Path

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _photo_bytes(fmt: str = "jpg", dpi: int = 300) -> bytes:
    """Genera una «foto» rasterizada grande dibujando formas y renderizándolas."""
    with pymupdf.open() as d:
        p = d.new_page(width=400, height=300)
        for k in range(30):
            c = ((k * 37) % 255 / 255, (k * 91) % 255 / 255, (k * 53) % 255 / 255)
            p.draw_circle((20 + k * 13, 40 + (k * 29) % 220), 25 + k % 7 * 6, color=c, fill=c)
        p.insert_text((40, 160), "FOTO DE PRUEBA", fontsize=36)
        pix = p.get_pixmap(dpi=dpi, alpha=False)
        return pix.tobytes(fmt)


@pytest.fixture(scope="session")
def samples(tmp_path_factory):
    base = tmp_path_factory.mktemp("samples")
    photo = _photo_bytes()

    a = base / "informe.pdf"
    with pymupdf.open() as d:
        for i in range(5):
            p = d.new_page(width=595, height=842)
            p.insert_text((72, 100), f"Informe - Página {i + 1}", fontsize=28)
            p.insert_image(pymupdf.Rect(72, 150, 523, 488), stream=photo)
        d.set_toc([[1, "Introducción", 1], [1, "Capítulo dos", 3], [1, "Anexos", 5]])
        d.set_metadata({"title": "Informe anual", "author": "Prueba"})
        d.save(a)

    b = base / "anexo.pdf"
    with pymupdf.open() as d:
        for i in range(3):
            p = d.new_page(width=612, height=792)
            p.insert_text((72, 100), f"Anexo {i + 1}", fontsize=28)
        d[1].set_rotation(90)
        d.save(b)

    png = base / "imagen.png"
    png.write_bytes(_photo_bytes("png", 100))
    jpg = base / "foto.jpg"
    jpg.write_bytes(photo)

    locked = base / "secreto.pdf"
    with pymupdf.open(a) as d:
        d.save(locked, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="1234", owner_pw="dueño")

    return {"a": a, "b": b, "png": png, "jpg": jpg, "locked": locked, "dir": base}


@pytest.fixture(scope="session")
def scanned(tmp_path_factory):
    """PDF «escaneado»: cada página es solo una imagen con texto."""
    base = tmp_path_factory.mktemp("scan")
    src = pymupdf.open()
    p = src.new_page(width=595, height=842)
    p.insert_text((72, 120), "Cooperativa de caficultores", fontsize=26)
    p.insert_text((72, 170), "Informe de gestión: niñez y educación", fontsize=18)
    pix = p.get_pixmap(dpi=200)
    out = pymupdf.open()
    q = out.new_page(width=595, height=842)
    q.insert_image(q.rect, pixmap=pix)
    # escaneo apaisado que se ve derecho gracias a /Rotate 90 (caso típico de escáner)
    q = out.new_page(width=842, height=595)
    q.insert_image(q.rect, pixmap=pix, rotate=90)
    q.set_rotation(90)
    path = base / "escaneado.pdf"
    out.save(path)
    return path


class Protegido:
    """PDF de 3 páginas con contraseña, una imagen en la página 2 y un adjunto.

    ``comprobar(salida, sin_tocar)`` revisa que el resultado de una operación siga pidiendo la misma
    contraseña y conserve completo lo que la operación no tocó: el texto y la imagen de esas páginas y los
    bytes del adjunto.  (En MuPDF 1.28, leer ``doc.needs_pass`` tras autenticar dejaba esas partes vacías.)
    """

    password = "1234"
    adjunto = b"Listado de beneficiarios: 1032456789"

    def __init__(self, path):
        self.path = path
        with pymupdf.open(path) as d:
            d.authenticate(self.password)
            self.textos = [p.get_text() for p in d]
            self.imagenes = [len(p.get_images()) for p in d]

    def comprobar(self, salida, sin_tocar=(0, 1, 2)):
        from ronypdf import core
        assert core.needs_password(salida) and core.check_password(salida, self.password)
        assert not core.check_password(salida, "otra")
        with core.open_pdf(salida, self.password) as d:
            for i in sin_tocar:
                assert d[i].get_text() == self.textos[i], f"la página {i + 1} perdió su texto"
                assert len(d[i].get_images()) == self.imagenes[i], f"la página {i + 1} perdió su imagen"
            if self.imagenes[1] and 1 in sin_tocar:
                xref = d[1].get_images()[0][0]
                assert len(d.extract_image(xref)["image"]) > 1000          # la imagen no quedó vacía
            assert d.embfile_count() == 1 and d.embfile_get(0) == self.adjunto


@pytest.fixture(scope="session")
def protegido(tmp_path_factory):
    path = tmp_path_factory.mktemp("protegido") / "protegido.pdf"
    with pymupdf.open() as d:
        for i in range(3):
            p = d.new_page(width=595, height=842)
            p.insert_text((72, 100), f"Página {i + 1}: informe de gestión de la fundación", fontsize=14)
            p.insert_text((72, 130), f"Contacto ana{i}@correo.com y celular 310 456 789{i}", fontsize=11)
            if i == 1:
                p.insert_image(pymupdf.Rect(72, 200, 372, 425), stream=_photo_bytes("png", 72))
        d.embfile_add("lista.txt", Protegido.adjunto, filename="lista.txt")
        d.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=Protegido.password, owner_pw="dueño")
    return Protegido(path)
