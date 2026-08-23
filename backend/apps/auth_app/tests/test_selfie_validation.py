"""Validación de la selfie de verificación (§29 del plan: extensión, MIME real y tamaño).

Ninguna prueba toca la base de datos ni la red: la validación es pura y las imágenes se
generan en memoria.

La regla que estas pruebas fijan es que **la autoridad es el contenido**. La extensión
declarada y el `Content-Type` que envía el cliente son pistas baratas, no evidencia.
"""

import pytest
from PIL import Image

from apps.auth_app.services import identity


def _archivo(selfie_factory, content, name="selfie.jpg", content_type="image/jpeg"):
    return selfie_factory(content, name=name, content_type=content_type)


# --------------------------------------------------------------------------- #
# Casos válidos
# --------------------------------------------------------------------------- #


def test_jpeg_valido(selfie_factory, jpeg_bytes):
    resultado = identity.validate_selfie(_archivo(selfie_factory, jpeg_bytes, "foto.jpg"))

    assert resultado.format == "JPEG"
    assert resultado.extension == ".jpg"
    assert resultado.content_type == "image/jpeg"


def test_png_valido(selfie_factory, png_bytes):
    resultado = identity.validate_selfie(
        _archivo(selfie_factory, png_bytes, "foto.png", "image/png")
    )

    assert resultado.format == "PNG"
    assert resultado.extension == ".png"
    assert resultado.content_type == "image/png"


def test_la_extension_jpeg_alternativa_se_acepta(selfie_factory, jpeg_bytes):
    resultado = identity.validate_selfie(_archivo(selfie_factory, jpeg_bytes, "foto.JPEG"))

    assert resultado.format == "JPEG"


def test_un_jpg_cuyo_contenido_es_png_se_acepta_como_png(selfie_factory, png_bytes):
    """La extensión declarada es un pre-filtro; el formato real manda."""
    resultado = identity.validate_selfie(_archivo(selfie_factory, png_bytes, "foto.jpg"))

    assert resultado.format == "PNG"
    assert resultado.extension == ".png"
    assert resultado.content_type == "image/png"


def test_el_content_type_declarado_por_el_cliente_se_ignora(selfie_factory, png_bytes):
    resultado = identity.validate_selfie(
        _archivo(selfie_factory, png_bytes, "foto.png", content_type="image/jpeg")
    )

    assert resultado.content_type == "image/png"


# --------------------------------------------------------------------------- #
# Tamaño
# --------------------------------------------------------------------------- #


def test_el_limite_por_defecto_son_5_mb(settings):
    assert settings.IDENTITY_SELFIE_MAX_BYTES == 5 * 1024 * 1024


def test_un_archivo_mayor_al_limite_se_rechaza(selfie_factory, settings):
    settings.IDENTITY_SELFIE_MAX_BYTES = 5 * 1024 * 1024
    demasiado = b"\0" * (settings.IDENTITY_SELFIE_MAX_BYTES + 1)

    with pytest.raises(identity.InvalidSelfieError):
        identity.validate_selfie(_archivo(selfie_factory, demasiado, "foto.jpg"))


def test_el_limite_se_comprueba_antes_de_abrir_la_imagen(
    selfie_factory, settings, jpeg_bytes, monkeypatch
):
    """Un archivo excedido no llega a Pillow: se rechaza por tamaño."""
    settings.IDENTITY_SELFIE_MAX_BYTES = 10

    def no_deberia_abrirse(*args, **kwargs):  # pragma: no cover - debe no ejecutarse
        raise AssertionError("Pillow no debería abrir un archivo excedido.")

    monkeypatch.setattr(Image, "open", no_deberia_abrirse)

    with pytest.raises(identity.InvalidSelfieError):
        identity.validate_selfie(_archivo(selfie_factory, jpeg_bytes, "foto.jpg"))


def test_un_archivo_en_el_limite_exacto_se_acepta(selfie_factory, settings, jpeg_bytes):
    settings.IDENTITY_SELFIE_MAX_BYTES = len(jpeg_bytes)
    resultado = identity.validate_selfie(_archivo(selfie_factory, jpeg_bytes, "foto.jpg"))

    assert resultado.format == "JPEG"


# --------------------------------------------------------------------------- #
# Extensión declarada
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("nombre", ["foto.pdf", "foto.gif", "foto.txt", "foto", "foto.", ".jpg"])
def test_una_extension_no_admitida_se_rechaza(selfie_factory, jpeg_bytes, nombre):
    with pytest.raises(identity.InvalidSelfieError):
        identity.validate_selfie(_archivo(selfie_factory, jpeg_bytes, nombre))


def test_el_nombre_con_recorrido_de_rutas_no_altera_la_salida(selfie_factory, png_bytes):
    """El nombre solo sirve para mirar la extensión; nunca para construir nada."""
    resultado = identity.validate_selfie(
        _archivo(selfie_factory, png_bytes, "../../etc/passwd.jpg")
    )

    assert resultado.extension == ".png"
    assert resultado.content_type == "image/png"


# --------------------------------------------------------------------------- #
# Contenido inválido
# --------------------------------------------------------------------------- #


def test_un_pdf_renombrado_a_jpg_se_rechaza(selfie_factory):
    pdf = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n%%EOF\n"

    with pytest.raises(identity.InvalidSelfieError):
        identity.validate_selfie(_archivo(selfie_factory, pdf, "foto.jpg"))


@pytest.mark.parametrize("formato", ["GIF", "BMP", "WEBP", "TIFF"])
def test_otros_formatos_de_imagen_se_rechazan(selfie_factory, imagen_factory, formato):
    """Válidos como imagen, fuera del conjunto admitido: solo JPEG y PNG."""
    if formato not in Image.SAVE:
        pytest.skip(f"Pillow no puede escribir {formato} en este entorno.")

    with pytest.raises(identity.InvalidSelfieError):
        identity.validate_selfie(_archivo(selfie_factory, imagen_factory(formato), "foto.jpg"))


def test_un_archivo_vacio_se_rechaza(selfie_factory):
    with pytest.raises(identity.InvalidSelfieError):
        identity.validate_selfie(_archivo(selfie_factory, b"", "foto.jpg"))


def test_un_png_truncado_se_rechaza(selfie_factory, png_bytes):
    truncado = png_bytes[: len(png_bytes) // 2]

    with pytest.raises(identity.InvalidSelfieError):
        identity.validate_selfie(_archivo(selfie_factory, truncado, "foto.png"))


def test_un_jpeg_con_bytes_corrompidos_se_rechaza(selfie_factory, jpeg_bytes):
    corrupto = bytearray(jpeg_bytes)
    del corrupto[len(corrupto) // 2 :]

    with pytest.raises(identity.InvalidSelfieError):
        identity.validate_selfie(_archivo(selfie_factory, bytes(corrupto), "foto.jpg"))


def test_basura_binaria_con_extension_valida_se_rechaza(selfie_factory):
    with pytest.raises(identity.InvalidSelfieError):
        identity.validate_selfie(_archivo(selfie_factory, b"\x00\x01\x02\x03" * 32, "foto.png"))


# --------------------------------------------------------------------------- #
# Bombas de descompresión
# --------------------------------------------------------------------------- #


def test_la_advertencia_de_bomba_de_descompresion_se_trata_como_invalida(
    selfie_factory, png_bytes, monkeypatch
):
    """Pillow solo advierte por encima de MAX_IMAGE_PIXELS: aquí se escala a error."""
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 3000)  # 64x64 = 4096 px

    with pytest.raises(identity.InvalidSelfieError):
        identity.validate_selfie(_archivo(selfie_factory, png_bytes, "foto.png"))


def test_el_error_de_bomba_de_descompresion_se_trata_como_invalido(
    selfie_factory, png_bytes, monkeypatch
):
    """Por encima del doble del umbral, Pillow levanta DecompressionBombError."""
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1)

    with pytest.raises(identity.InvalidSelfieError):
        identity.validate_selfie(_archivo(selfie_factory, png_bytes, "foto.png"))


def test_no_se_impone_ningun_limite_propio_de_dimensiones(selfie_factory, imagen_factory):
    """El criterio es el de Pillow; el proyecto no inventa un máximo de píxeles."""
    grande = imagen_factory("PNG", size=(2000, 1500))
    resultado = identity.validate_selfie(_archivo(selfie_factory, grande, "foto.png"))

    assert resultado.format == "PNG"


# --------------------------------------------------------------------------- #
# El archivo queda utilizable
# --------------------------------------------------------------------------- #


def test_el_archivo_queda_reposicionado_y_legible(selfie_factory, png_bytes):
    """Tras `verify()` el archivo debe poder leerse íntegro para subirlo."""
    archivo = _archivo(selfie_factory, png_bytes, "foto.png")

    identity.validate_selfie(archivo)

    assert archivo.read() == png_bytes


def test_el_archivo_queda_reposicionado_incluso_si_la_validacion_falla(selfie_factory):
    basura = b"\x00\x01\x02\x03" * 32
    archivo = _archivo(selfie_factory, basura, "foto.png")

    with pytest.raises(identity.InvalidSelfieError):
        identity.validate_selfie(archivo)

    assert archivo.read() == basura


# --------------------------------------------------------------------------- #
# Ramas defensivas
# --------------------------------------------------------------------------- #


def test_un_archivo_sin_tamano_declarado_se_rechaza():
    """Defensa contra objetos que no son un `UploadedFile` de Django."""

    class SinTamano:
        name = "foto.jpg"

    with pytest.raises(identity.InvalidSelfieError):
        identity.validate_selfie(SinTamano())


def test_una_imagen_sin_formato_reconocible_se_rechaza(selfie_factory, png_bytes, monkeypatch):
    """Pillow puede abrir un objeto imagen y no reportar formato alguno."""

    class ImagenSinFormato:
        format = None

        def verify(self):
            return None

    monkeypatch.setattr(Image, "open", lambda *args, **kwargs: ImagenSinFormato())

    with pytest.raises(identity.InvalidSelfieError):
        identity.validate_selfie(_archivo(selfie_factory, png_bytes, "foto.png"))
