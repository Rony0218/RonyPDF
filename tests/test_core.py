from pathlib import Path

import pymupdf
import pytest

from ronypdf import core


def pages_text(path, password=None):
    with core.open_pdf(path, password) as d:
        return [p.get_text().strip().splitlines()[0] if p.get_text().strip() else "" for p in d]


# ---------------------------------------------------------------- rangos

@pytest.mark.parametrize("spec,expected", [
    ("", [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]),
    ("todas", list(range(10))),
    ("1-3, 5", [0, 1, 2, 4]),
    ("8-", [7, 8, 9]),
    ("-2", [0, 1]),
    ("3-1", [2, 1, 0]),
    ("fin", [9]),
    ("2 a 4", [1, 2, 3]),
    ("1;10", [0, 9]),
])
def test_parse_pages(spec, expected):
    assert core.parse_pages(spec, 10) == expected


@pytest.mark.parametrize("spec", ["0", "11", "abc", "1--2", "-"])
def test_parse_pages_invalid(spec):
    with pytest.raises(core.UserError):
        core.parse_pages(spec, 10)


def test_parse_groups():
    assert core.parse_groups("1-2, 3, 4-", 5) == [[0, 1], [2], [3, 4]]


# ---------------------------------------------------------------- unir / dividir

def test_merge_with_ranges_images_and_bookmarks(samples, tmp_path):
    out = tmp_path / "unido.pdf"
    items = [core.MergeItem(str(samples["a"]), "1-2"), core.MergeItem(str(samples["b"])),
             core.MergeItem(str(samples["jpg"])), core.MergeItem(str(samples["locked"]), "5", "1234")]
    n = core.merge(items, out)
    assert n == 2 + 3 + 1 + 1
    t = pages_text(out)
    assert t[:2] == ["Informe - Página 1", "Informe - Página 2"]
    assert t[2] == "Anexo 1" and t[-1] == "Informe - Página 5"
    with pymupdf.open(out) as d:
        assert [e[1] for e in d.get_toc()] == ["informe", "anexo", "foto", "secreto"]
        assert d[3].rotation == 90  # se conserva la rotación original


def test_merge_wrong_password(samples, tmp_path):
    with pytest.raises(core.PasswordError):
        core.merge([core.MergeItem(str(samples["locked"]), "", "mala")], tmp_path / "x.pdf")


def test_split_every_and_ranges(samples, tmp_path):
    files = core.split_every(samples["a"], 2, tmp_path / "cada2")
    assert [Path(f).name for f in files] == ["informe_1-2.pdf", "informe_3-4.pdf", "informe_5.pdf"]
    files = core.split_ranges(samples["a"], "1, 2-3, 5-4", tmp_path / "rangos")
    assert len(files) == 3
    assert pages_text(files[2]) == ["Informe - Página 5", "Informe - Página 4"]


def test_split_bookmarks(samples, tmp_path):
    files = core.split_bookmarks(samples["a"], tmp_path / "marc")
    names = [Path(f).name for f in files]
    assert names == ["01 - Introducción.pdf", "02 - Capítulo dos.pdf", "03 - Anexos.pdf"]
    assert [len(pages_text(f)) for f in files] == [2, 2, 1]


def test_extract_pages(samples, tmp_path):
    out = tmp_path / "ext.pdf"
    assert core.extract_pages(samples["a"], "5, 1", out) == 2
    assert pages_text(out) == ["Informe - Página 5", "Informe - Página 1"]


# ---------------------------------------------------------------- organizar

def test_build_document(samples, tmp_path):
    out = tmp_path / "org.pdf"
    refs = [core.PageRef(1, 2), core.PageRef(0, 0, rotate=90), core.PageRef(None),
            core.PageRef(0, 1), core.PageRef(0, 2), core.PageRef(0, 1)]
    n = core.build_document([(str(samples["a"]), None), (str(samples["b"]), None)], refs, out)
    assert n == 6
    with pymupdf.open(out) as d:
        assert d[1].rotation == 90
        assert d[2].get_text().strip() == ""
    assert pages_text(out)[3:] == ["Informe - Página 2", "Informe - Página 3", "Informe - Página 2"]


def test_build_document_overwrites_source(samples, tmp_path):
    src = tmp_path / "copia.pdf"
    src.write_bytes(samples["a"].read_bytes())
    core.build_document([(str(src), None)], [core.PageRef(0, 4), core.PageRef(0, 0)], src)
    assert pages_text(src) == ["Informe - Página 5", "Informe - Página 1"]


# ---------------------------------------------------------------- comprimir

@pytest.mark.parametrize("level", ["baja", "media", "alta", "maxima"])
def test_compress_levels(samples, tmp_path, level):
    out = tmp_path / f"c_{level}.pdf"
    before, after = core.compress(samples["a"], out, level)
    assert after <= before
    assert len(pages_text(out)) == 5
    if level != "baja":
        assert after < before * 0.8


def test_compress_encrypted_keeps_password(samples, tmp_path):
    out = tmp_path / "c_lock.pdf"
    core.compress(samples["locked"], out, "alta", password="1234")
    assert core.needs_password(out)
    assert core.check_password(out, "1234")


# ---------------------------------------------------------------- imágenes

def test_pdf_to_images(samples, tmp_path):
    files = core.pdf_to_images(samples["a"], tmp_path / "img", "jpg", 72, "1-2")
    assert len(files) == 2 and all(Path(f).stat().st_size > 1000 for f in files)
    files = core.pdf_to_images(samples["b"], tmp_path / "img", "png", 50)
    assert len(files) == 3


@pytest.mark.parametrize("size,orient", [("imagen", "auto"), ("A4", "auto"), ("Carta", "vertical")])
def test_images_to_pdf(samples, tmp_path, size, orient):
    out = tmp_path / f"imgs_{size}.pdf"
    n = core.images_to_pdf([str(samples["png"]), (str(samples["jpg"]), 90)], out, size, orient, 20)
    assert n == 2
    with pymupdf.open(out) as d:
        assert len(d[0].get_images()) == 1
        if size == "A4" and orient == "auto":
            assert d[0].rect.width > d[0].rect.height  # imagen apaisada → página horizontal
            assert d[1].rect.width < d[1].rect.height  # rotada 90° → vertical


def test_extract_images(samples, tmp_path):
    files = core.extract_images(samples["a"], tmp_path / "ext")
    assert len(files) == 1  # la misma imagen se reutiliza en todas las páginas


# ---------------------------------------------------------------- sellos

def test_watermark_and_numbers(samples, tmp_path):
    out = tmp_path / "wm.pdf"
    for pos in ("centro", "mosaico", "superior"):
        core.watermark(samples["b"], out, "CONFIDENCIAL ñ", position=pos)
        with pymupdf.open(out) as d:
            assert all("CONFIDENCIAL ñ" in p.get_text() for p in d)
    out2 = tmp_path / "num.pdf"
    core.page_numbers(samples["b"], out2, fmt="Página {n} de {total}", skip_first=True,
                      position="abajo-derecha")
    with pymupdf.open(out2) as d:
        assert "Página" not in d[0].get_text()
        assert "Página 1 de 2" in d[1].get_text()
        assert "Página 2 de 2" in d[2].get_text()
        # el número debe quedar abajo a la derecha aunque la página esté rotada
        for p in d[1:]:
            hit = p.search_for("de 2")[0]
            assert hit.y0 > p.rect.height * 0.85 and hit.x0 > p.rect.width * 0.6


def test_page_numbers_bad_format(samples, tmp_path):
    with pytest.raises(core.UserError):
        core.page_numbers(samples["b"], tmp_path / "x.pdf", fmt="{pagina}")


def test_preview(samples):
    w, h, stride, data = core.preview_first_page(
        samples["a"], None, lambda d: core.apply_watermark(d, "BORRADOR"), 300)
    assert max(w, h) == 300 and len(data) == stride * h


# ---------------------------------------------------------------- seguridad / texto

def test_protect_and_unlock(samples, tmp_path):
    prot = tmp_path / "prot.pdf"
    core.protect(samples["b"], prot, "clave", allow_print=False)
    assert core.needs_password(prot)
    assert not core.check_password(prot, "otra")
    assert core.check_password(prot, "clave")
    free = tmp_path / "libre.pdf"
    core.unlock(prot, free, "clave")
    assert not core.needs_password(free)
    with pymupdf.open(free) as d:
        assert not d.is_encrypted
    with pytest.raises(core.PasswordError):
        core.unlock(prot, tmp_path / "x.pdf", "mala")


def test_owner_only_restrictions_removed(samples, tmp_path):
    restricted = tmp_path / "restr.pdf"
    core.protect(samples["b"], restricted, "", owner_pw="dueño", allow_copy=False)
    assert not core.needs_password(restricted)
    assert core.pdf_info(restricted).encrypted
    assert not core.pdf_info(samples["b"]).encrypted
    free = tmp_path / "libre2.pdf"
    core.unlock(restricted, free, None)
    with pymupdf.open(free) as d:
        assert not d.is_encrypted


def test_extract_text_and_metadata(samples, tmp_path):
    txt = tmp_path / "t.txt"
    core.extract_text(samples["a"], txt, "2-3")
    content = txt.read_text(encoding="utf-8-sig")
    assert "Página 2" in content and "Informe - Página 3" in content and "Página 4" not in content
    meta = core.get_metadata(samples["a"])
    assert meta["title"] == "Informe anual"
    out = tmp_path / "meta.pdf"
    core.set_metadata(samples["a"], out, {"title": "Nuevo", "author": "Yo"})
    assert core.get_metadata(out)["title"] == "Nuevo"


def test_cancel_leaves_no_partial_files(samples, tmp_path):
    class Stop(Exception):
        pass

    def progress(i, n, msg):
        raise Stop()

    with pytest.raises(Stop):
        core.compress(samples["a"], tmp_path / "x.pdf", "media", progress=progress)
    with pytest.raises(Stop):
        core.merge([core.MergeItem(str(samples["a"]))], tmp_path / "y.pdf", progress=progress)
    assert list(tmp_path.iterdir()) == []


def test_info(samples):
    info = core.pdf_info(samples["a"])
    assert info.pages == 5 and info.has_toc and info.title == "Informe anual"
    assert core.pdf_info(samples["locked"], "1234").encrypted


def test_human_size_con_coma_decimal():
    assert core.human_size(859) == "859 B"
    assert core.human_size(2867) == "2,8 KB"
    assert core.human_size(1.2 * 1024 ** 2) == "1,2 MB"
    assert core.human_size(3 * 1024 ** 4) == "3072,0 GB"


# ---------------------------------------------------------------- visor / edición / OCR

def test_search_and_text_helpers(samples):
    with pymupdf.open(samples["b"]) as d:
        hits = core.search_text(d, "anexo")
        assert [h[0] for h in hits] == [0, 1, 2]
        # página 2 está rotada 90°: el resultado debe estar en coordenadas visibles
        r = hits[1][1]
        assert d[1].rect.contains(r)
        info = core.text_line_at(d[0], 80, 92)
        assert info["text"] == "Anexo 1" and info["horizontal"] and info["family"] == "sans"
        assert abs(info["size"] - 28) < 0.5
        assert core.text_line_at(d[0], 500, 700) is None
        center = (pymupdf.Rect(r).tl + pymupdf.Rect(r).br) / 2
        vertical = core.text_line_at(d[1], center.x, center.y)
        assert vertical and not vertical["horizontal"]
        rects = core.words_in_rect(d[0], (60, 60, 300, 120))
        assert len(rects) == 1 and pymupdf.Rect(rects[0]).contains(pymupdf.Point(80, 92))


def test_apply_edits_all_kinds(samples, tmp_path):
    out = tmp_path / "edit.pdf"
    png = samples["png"].read_bytes()
    with pymupdf.open(samples["b"]) as d:
        line = core.text_line_at(d[0], 80, 92)
        rot_rect = core.search_text(d, "Anexo 2")[0][1]
    edits = [
        core.Edit("replace", 0, cover=line["rect"], lines=[(line["origin"][0], line["origin"][1], "Apéndice A")],
                  fontsize=line["size"], font="helv"),
        core.Edit("text", 0, lines=[(72, 300, "Texto nuevo con ñ"), (72, 316, "segunda línea")], fontsize=12,
                  color=(0.8, 0, 0)),
        core.Edit("image", 0, rect=(300, 400, 400, 475), image=png),
        core.Edit("rect", 0, rect=(50, 500, 150, 560), color=(0, 0, 1), width=2),
        core.Edit("ellipse", 0, rect=(200, 500, 300, 560), color=(0, 0.5, 0), fill=(0.9, 1, 0.9)),
        core.Edit("arrow", 0, points=[(50, 600), (200, 650)], color=(1, 0, 0), width=2),
        core.Edit("ink", 0, points=[(300, 600), (320, 620), (340, 600), (360, 620)], width=3),
        core.Edit("highlight", 0, rects=[(72, 290, 200, 305)], color=(1, 0.9, 0)),
        core.Edit("redact", 1, rect=tuple(rot_rect)),
    ]
    assert core.apply_edits(samples["b"], out, edits) == len(edits)
    with pymupdf.open(out) as d:
        t0 = d[0].get_text()
        assert "Apéndice A" in t0 and "Anexo 1" not in t0
        assert "Texto nuevo con ñ" in t0 and "segunda línea" in t0
        assert len(d[0].get_images()) == 1
        assert [a.type[1] for a in d[0].annots()] == ["Highlight"]
        assert len(d[0].get_drawings()) >= 4
        assert "Anexo 2" not in d[1].get_text()          # zona borrada de verdad
        assert d[1].rotation == 0 and d[1].rect.width > d[1].rect.height  # misma apariencia
        assert "Anexo 3" in d[2].get_text()


def test_apply_edits_keeps_password(samples, protegido, tmp_path):
    out = tmp_path / "e.pdf"
    core.apply_edits(samples["locked"], out, [core.Edit("text", 0, lines=[(72, 700, "Hola")])], "1234")
    assert core.needs_password(out) and core.check_password(out, "1234")
    # lo que no se editó sigue completo (antes salía en blanco: páginas, imágenes y adjuntos)
    out = tmp_path / "p.pdf"
    core.apply_edits(protegido.path, out, [core.Edit("text", 0, lines=[(72, 700, "Hola")])], protegido.password)
    protegido.comprobar(out, sin_tocar=(1, 2))
    with core.open_pdf(out, protegido.password) as d:
        assert "Hola" in d[0].get_text() and "informe de gestión" in d[0].get_text()


def test_operaciones_con_contrasena_conservan_todo(protegido, tmp_path):
    pw = protegido.password
    out = tmp_path / "girado.pdf"
    core.rotate_pages(protegido.path, out, 90, password=pw)
    protegido.comprobar(out)
    out = tmp_path / "comprimido.pdf"
    core.compress(protegido.path, out, password=pw)
    with core.open_pdf(out, pw) as d:
        assert [p.get_text() for p in d] == protegido.textos
    out = tmp_path / "numerado.pdf"
    core.page_numbers(protegido.path, out, password=pw)
    with core.open_pdf(out, pw) as d:
        assert all(protegido.textos[i].strip() in d[i].get_text() for i in range(3))
        assert d.embfile_get(0) == protegido.adjunto


def test_is_password_protected_no_invalida_la_clave(protegido, samples):
    # doc.needs_pass, leído tras autenticar, vuelve a probar la clave vacía y deja el documento sin descifrar
    with core.open_pdf(protegido.path, protegido.password) as d:
        assert core.is_password_protected(d)
        assert d[0].get_text() == protegido.textos[0]
    with core.open_pdf(samples["a"]) as d:
        assert not core.is_password_protected(d)
    assert core.pdf_info(protegido.path, protegido.password).encrypted
    assert not core.pdf_info(samples["a"]).encrypted


def test_ocr_makes_searchable(scanned, tmp_path):
    with pymupdf.open(scanned) as d:
        assert core.looks_scanned(d)
        assert not core.search_text(d, "gestión")
    out = tmp_path / "ocr.pdf"
    pages, words = core.ocr_pdf(scanned, out, "spa")
    assert pages == 2 and words >= 10
    with pymupdf.open(out) as d:
        hits = core.search_text(d, "niñez")
        assert [h[0] for h in hits] == [0, 1]
        assert not core.looks_scanned(d)
    # segunda pasada: «solo páginas sin texto» ya no tiene nada que hacer
    assert core.ocr_pdf(out, tmp_path / "ocr2.pdf", "spa")[0] == 0


def test_ocr_text(scanned, tmp_path):
    txt = tmp_path / "ocr.txt"
    core.ocr_text(scanned, txt, "spa+eng", spec="1")
    content = txt.read_text(encoding="utf-8-sig")
    assert "Cooperativa" in content and "educación" in content


def test_ocr_missing_language(scanned, tmp_path):
    with pytest.raises(core.UserError):
        core.ocr_pdf(scanned, tmp_path / "x.pdf", "jpn")


def test_tessdata_lookup_when_frozen(tmp_path, monkeypatch):
    bundle = tmp_path / "meipass" / "ronypdf" / "tessdata"
    bundle.mkdir(parents=True)
    (bundle / "spa.traineddata").write_bytes(b"x")
    exe_dir = tmp_path / "usb"
    exe_dir.mkdir()
    monkeypatch.setattr(core.sys, "frozen", True, raising=False)
    monkeypatch.setattr(core.sys, "_MEIPASS", str(tmp_path / "meipass"), raising=False)
    monkeypatch.setattr(core.sys, "executable", str(exe_dir / "RonyPDF.exe"))
    assert core.tessdata_dir() == str(bundle)
    # una carpeta «tessdata» junto al .exe tiene prioridad (para agregar idiomas)
    extra = exe_dir / "tessdata"
    extra.mkdir()
    (extra / "por.traineddata").write_bytes(b"x")
    assert core.tessdata_dir() == str(extra)
    assert core.ocr_languages() == ["por"]
