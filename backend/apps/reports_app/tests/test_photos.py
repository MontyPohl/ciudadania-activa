import hashlib
import io
import re
import warnings

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image, PngImagePlugin

from apps.reports_app.services import photos


def _upload(content, name="photo.jpg", content_type="image/jpeg"):
    return SimpleUploadedFile(name, content, content_type=content_type)


def _image_bytes(format_name, *, size=(24, 16), mode="RGB", exif=None, pnginfo=None):
    buffer = io.BytesIO()
    image = Image.new(mode, size, (10, 20, 30, 120) if mode == "RGBA" else (10, 20, 30))
    kwargs = {}
    if exif is not None:
        kwargs["exif"] = exif
    if pnginfo is not None:
        kwargs["pnginfo"] = pnginfo
    image.save(buffer, format=format_name, **kwargs)
    image.close()
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("format_name", "name", "mime", "canonical_extension"),
    [
        ("JPEG", "photo.jpeg", "image/jpeg", ".jpg"),
        ("PNG", "photo.png", "image/png", ".png"),
    ],
)
def test_prepara_jpeg_y_png_reales(format_name, name, mime, canonical_extension):
    original = _image_bytes(format_name)

    prepared = photos.prepare_report_photo(_upload(original, name, mime))

    assert prepared.format == format_name
    assert prepared.extension == canonical_extension
    assert prepared.content_type == mime
    assert prepared.original_hash == hashlib.sha256(original).hexdigest()
    with Image.open(io.BytesIO(prepared.content)) as normalized:
        assert normalized.format == format_name
        normalized.verify()


@pytest.mark.parametrize(
    ("name", "mime", "content_format"),
    [
        ("photo.png", "image/png", "JPEG"),
        ("photo.jpg", "image/jpeg", "PNG"),
        ("photo.jpg", "image/png", "JPEG"),
        ("photo.png", "image/jpeg", "PNG"),
    ],
)
def test_rechaza_extension_o_mime_spoof(name, mime, content_format):
    with pytest.raises(photos.InvalidReportPhotoError, match="coincidir"):
        photos.prepare_report_photo(_upload(_image_bytes(content_format), name, mime))


@pytest.mark.parametrize(
    ("content", "name", "mime"),
    [
        (b"no es imagen", "malware.exe", "application/octet-stream"),
        (b"no es imagen", "photo.jpg", "image/jpeg"),
        (_image_bytes("JPEG")[:20], "photo.jpg", "image/jpeg"),
        (_image_bytes("PNG")[:20], "photo.png", "image/png"),
    ],
)
def test_rechaza_archivo_no_imagen_o_truncado(content, name, mime):
    with pytest.raises(photos.InvalidReportPhotoError):
        photos.prepare_report_photo(_upload(content, name, mime))


@pytest.mark.parametrize(
    ("name", "mime"),
    [("photo.webp", "image/webp"), ("photo.heic", "image/heic")],
)
def test_rechaza_webp_y_heic(name, mime):
    content = _image_bytes("WEBP")

    with pytest.raises(photos.InvalidReportPhotoError):
        photos.prepare_report_photo(_upload(content, name, mime))


def test_rechaza_formato_real_webp_aunque_nombre_y_mime_digan_jpeg():
    with pytest.raises(photos.InvalidReportPhotoError, match="JPEG o PNG"):
        photos.prepare_report_photo(_upload(_image_bytes("WEBP")))


def test_rechaza_original_mayor_a_cinco_mib():
    oversized = b"x" * (photos.MAX_REPORT_PHOTO_BYTES + 1)

    with pytest.raises(photos.InvalidReportPhotoError, match="5 MiB"):
        photos.prepare_report_photo(_upload(oversized))


def test_rechaza_original_vacio():
    with pytest.raises(photos.InvalidReportPhotoError, match="vacía"):
        photos.prepare_report_photo(_upload(b""))


def test_limite_no_confia_solo_en_size_declarado():
    liar = io.BytesIO(b"x" * (photos.MAX_REPORT_PHOTO_BYTES + 1))
    liar.size = 1
    liar.name = "photo.jpg"
    liar.content_type = "image/jpeg"

    with pytest.raises(photos.InvalidReportPhotoError, match="5 MiB"):
        photos.prepare_report_photo(liar)


def test_size_declarado_grande_no_rechaza_stream_real_pequeno():
    original = _image_bytes("JPEG")
    upload = _upload(original)
    upload.size = photos.MAX_REPORT_PHOTO_BYTES + 1

    prepared = photos.prepare_report_photo(upload)

    assert prepared.original_hash == hashlib.sha256(original).hexdigest()


def test_error_de_lectura_se_traduce_sin_propagar_detalle():
    class Unreadable:
        size = 1
        name = "photo.jpg"
        content_type = "image/jpeg"

        def seek(self, position):
            return None

        def read(self, size):
            raise OSError("detalle interno")

    with pytest.raises(photos.InvalidReportPhotoError, match="No se pudo leer") as excinfo:
        photos.prepare_report_photo(Unreadable())
    assert "detalle interno" not in str(excinfo.value)


def test_rechaza_salida_normalizada_mayor_a_cinco_mib(monkeypatch):
    monkeypatch.setattr(
        photos,
        "_normalize_image",
        lambda original, image_format: b"x" * (photos.MAX_REPORT_PHOTO_BYTES + 1),
    )

    with pytest.raises(photos.InvalidReportPhotoError, match="normalizada"):
        photos.prepare_report_photo(_upload(_image_bytes("JPEG")))


def test_hash_es_sha256_lowercase_de_bytes_originales():
    original = _image_bytes("JPEG") + b"contenido-posterior-tolerado-por-jpeg"

    prepared = photos.prepare_report_photo(_upload(original))

    assert prepared.original_hash == hashlib.sha256(original).hexdigest()
    assert re.fullmatch(r"[0-9a-f]{64}", prepared.original_hash)


def test_misma_imagen_normalizada_con_exif_distinto_conserva_hashes_originales_distintos():
    first_exif = Image.Exif()
    first_exif[270] = "metadata uno"
    second_exif = Image.Exif()
    second_exif[270] = "metadata dos"
    first_original = _image_bytes("JPEG", exif=first_exif)
    second_original = _image_bytes("JPEG", exif=second_exif)

    first = photos.prepare_report_photo(_upload(first_original))
    second = photos.prepare_report_photo(_upload(second_original))

    assert first.content == second.content
    assert first.original_hash != second.original_hash
    assert first.original_hash != hashlib.sha256(first.content).hexdigest()
    assert second.original_hash != hashlib.sha256(second.content).hexdigest()


def test_normaliza_orientacion_exif():
    exif = Image.Exif()
    exif[274] = 6
    original = _image_bytes("JPEG", size=(12, 20), exif=exif)

    prepared = photos.prepare_report_photo(_upload(original))

    with Image.open(io.BytesIO(prepared.content)) as normalized:
        assert normalized.size == (20, 12)


def test_jpeg_elimina_exif_e_icc():
    exif = Image.Exif()
    exif[270] = "metadata privada"
    original = _image_bytes("JPEG", exif=exif)

    prepared = photos.prepare_report_photo(_upload(original))

    with Image.open(io.BytesIO(prepared.content)) as normalized:
        assert not normalized.getexif()
        assert "icc_profile" not in normalized.info


def test_png_conserva_alpha_y_elimina_metadata():
    pnginfo = PngImagePlugin.PngInfo()
    pnginfo.add_text("Comment", "metadata privada")
    original = _image_bytes("PNG", mode="RGBA", pnginfo=pnginfo)

    prepared = photos.prepare_report_photo(_upload(original, "photo.png", "image/png"))

    with Image.open(io.BytesIO(prepared.content)) as normalized:
        assert normalized.mode == "RGBA"
        assert "Comment" not in normalized.info
        assert "icc_profile" not in normalized.info
        assert not normalized.getexif()


@pytest.mark.parametrize(
    "pillow_signal",
    [Image.DecompressionBombWarning("warning"), Image.DecompressionBombError("error")],
)
def test_rechaza_las_dos_senales_de_bomba(monkeypatch, pillow_signal):
    def unsafe_open(*args, **kwargs):
        raise pillow_signal

    monkeypatch.setattr(photos.Image, "open", unsafe_open)

    with pytest.raises(photos.InvalidReportPhotoError, match="no es válida"):
        photos.prepare_report_photo(_upload(_image_bytes("JPEG")))


def test_filtro_de_warnings_se_restaura_despues_de_validar():
    before = list(warnings.filters)

    photos.prepare_report_photo(_upload(_image_bytes("JPEG")))

    assert warnings.filters == before


def test_rechaza_imagen_cuyo_decoder_no_reporta_formato(monkeypatch):
    class UnknownFormat:
        format = None

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def verify(self):
            return None

    monkeypatch.setattr(photos.Image, "open", lambda stream: UnknownFormat())

    with pytest.raises(photos.InvalidReportPhotoError, match="determinar el formato"):
        photos.prepare_report_photo(_upload(_image_bytes("JPEG")))


def test_bomba_detectada_durante_normalizacion_tambien_se_rechaza(monkeypatch):
    monkeypatch.setattr(photos, "_verify_real_image", lambda original: "JPEG")

    def unsafe_open(*args, **kwargs):
        raise Image.DecompressionBombWarning("warning tardío")

    monkeypatch.setattr(photos.Image, "open", unsafe_open)

    with pytest.raises(photos.InvalidReportPhotoError, match="no es válida"):
        photos.prepare_report_photo(_upload(_image_bytes("JPEG")))


def test_fallo_de_encoder_se_traduce_y_cierra_imagen_limpia(monkeypatch):
    clean = type(
        "FailingImage",
        (),
        {
            "closed": False,
            "save": lambda self, *args, **kwargs: (_ for _ in ()).throw(OSError("encoder")),
            "close": lambda self: setattr(self, "closed", True),
        },
    )()
    monkeypatch.setattr(photos, "_copy_clean_pixels", lambda image, image_format: clean)

    with pytest.raises(photos.InvalidReportPhotoError, match="normalizar"):
        photos.prepare_report_photo(_upload(_image_bytes("JPEG")))
    assert clean.closed is True
