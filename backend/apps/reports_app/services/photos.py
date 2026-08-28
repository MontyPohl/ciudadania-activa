"""Validación y normalización segura de fotografías de reportes."""

from __future__ import annotations

import hashlib
import io
import warnings
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_REPORT_PHOTO_BYTES = 5 * 1024 * 1024

_FORMAT_SPECS = {
    "JPEG": {
        "extensions": frozenset({".jpg", ".jpeg"}),
        "canonical_extension": ".jpg",
        "content_type": "image/jpeg",
    },
    "PNG": {
        "extensions": frozenset({".png"}),
        "canonical_extension": ".png",
        "content_type": "image/png",
    },
}
_ALLOWED_EXTENSIONS = frozenset(
    extension for spec in _FORMAT_SPECS.values() for extension in spec["extensions"]
)
_ALLOWED_CONTENT_TYPES = frozenset(spec["content_type"] for spec in _FORMAT_SPECS.values())


class InvalidReportPhotoError(ValueError):
    """La fotografía no cumple el contrato JPEG/PNG privado de S6."""


@dataclass(frozen=True)
class PreparedReportPhoto:
    """Bytes normalizados y datos derivados del contenido original."""

    content: bytes
    original_hash: str
    format: str
    extension: str
    content_type: str


def prepare_report_photo(photo) -> PreparedReportPhoto:
    """Lee una vez, valida el archivo real y produce una copia sin metadata."""
    original = _read_original_bytes(photo)
    original_hash = hashlib.sha256(original).hexdigest()
    declared_extension = _declared_extension(photo)
    declared_content_type = (getattr(photo, "content_type", "") or "").lower()

    if declared_extension not in _ALLOWED_EXTENSIONS:
        raise InvalidReportPhotoError("La fotografía debe tener extensión .jpg, .jpeg o .png.")
    if declared_content_type not in _ALLOWED_CONTENT_TYPES:
        raise InvalidReportPhotoError("La fotografía debe declarar MIME image/jpeg o image/png.")

    image_format = _verify_real_image(original)
    spec = _FORMAT_SPECS.get(image_format)
    if spec is None:
        raise InvalidReportPhotoError("La fotografía debe estar en formato JPEG o PNG.")
    if (
        declared_extension not in spec["extensions"]
        or declared_content_type != spec["content_type"]
    ):
        raise InvalidReportPhotoError(
            "La extensión y el MIME deben coincidir con el formato real de la fotografía."
        )

    normalized = _normalize_image(original, image_format)
    if len(normalized) > MAX_REPORT_PHOTO_BYTES:
        raise InvalidReportPhotoError("La fotografía normalizada supera el máximo de 5 MiB.")

    return PreparedReportPhoto(
        content=normalized,
        original_hash=original_hash,
        format=image_format,
        extension=spec["canonical_extension"],
        content_type=spec["content_type"],
    )


def _read_original_bytes(photo) -> bytes:
    # `UploadedFile.size` es metadata declarativa: puede mentir en ambos sentidos.
    # La autoridad es el stream, leído como máximo hasta límite + 1 para no cargar
    # arbitrariamente un archivo enorme antes de rechazarlo.
    try:
        photo.seek(0)
        original = photo.read(MAX_REPORT_PHOTO_BYTES + 1)
    except (AttributeError, OSError, ValueError) as exc:
        raise InvalidReportPhotoError("No se pudo leer la fotografía.") from exc
    finally:
        try:
            photo.seek(0)
        except (AttributeError, OSError, ValueError):
            pass

    if not original:
        raise InvalidReportPhotoError("La fotografía está vacía.")
    if len(original) > MAX_REPORT_PHOTO_BYTES:
        raise InvalidReportPhotoError("La fotografía supera el máximo de 5 MiB.")
    return original


def _declared_extension(photo) -> str:
    name = (getattr(photo, "name", "") or "").strip()
    last_segment = name.replace("\\", "/").rsplit("/", 1)[-1]
    dot = last_segment.rfind(".")
    return last_segment[dot:].lower() if dot > 0 else ""


def _verify_real_image(original: bytes) -> str:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(original)) as image:
                image_format = image.format
                image.verify()
    except Image.DecompressionBombWarning as exc:
        raise InvalidReportPhotoError("La fotografía no es válida.") from exc
    except Image.DecompressionBombError as exc:
        raise InvalidReportPhotoError("La fotografía no es válida.") from exc
    except UnidentifiedImageError as exc:
        raise InvalidReportPhotoError("El archivo no es una imagen válida.") from exc
    except (OSError, SyntaxError, ValueError) as exc:
        raise InvalidReportPhotoError("El archivo no es una imagen válida.") from exc

    if not image_format:
        raise InvalidReportPhotoError("No se pudo determinar el formato de la fotografía.")
    return image_format


def _normalize_image(original: bytes, image_format: str) -> bytes:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(original)) as source:
                transposed = ImageOps.exif_transpose(source)
                transposed.load()
                clean = _copy_clean_pixels(transposed, image_format)
    except Image.DecompressionBombWarning as exc:
        raise InvalidReportPhotoError("La fotografía no es válida.") from exc
    except Image.DecompressionBombError as exc:
        raise InvalidReportPhotoError("La fotografía no es válida.") from exc
    except (OSError, SyntaxError, ValueError) as exc:
        raise InvalidReportPhotoError("No se pudo normalizar la fotografía.") from exc

    output = io.BytesIO()
    try:
        if image_format == "JPEG":
            clean.save(
                output,
                format="JPEG",
                quality=90,
                optimize=True,
                progressive=False,
                subsampling=2,
            )
        else:
            clean.save(output, format="PNG", optimize=True, compress_level=9)
    except (OSError, ValueError) as exc:
        raise InvalidReportPhotoError("No se pudo normalizar la fotografía.") from exc
    finally:
        clean.close()
    return output.getvalue()


def _copy_clean_pixels(image, image_format: str):
    if image_format == "JPEG":
        converted = image.convert("RGB")
        mode = "RGB"
    else:
        has_transparency = "A" in image.getbands() or "transparency" in image.info
        mode = "RGBA" if has_transparency else "RGB"
        converted = image.convert(mode)

    try:
        return Image.frombytes(mode, converted.size, converted.tobytes())
    finally:
        if converted is not image:
            converted.close()
