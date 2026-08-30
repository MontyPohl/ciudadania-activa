"""Carga de la fotografía del reporte para la clasificación (S8-D).

Descarga server-side desde el bucket privado. No genera URLs firmadas ni públicas,
no entrega credenciales al proveedor y no persiste nada (contrato S8-A, D11).

Los bytes que devuelve ya están normalizados: la pipeline de creación de reportes
solo admite JPEG y PNG, verifica el formato real con Pillow y recodifica sin
metadata antes de subirlos, de modo que la imagen que llega al proveedor no
arrastra EXIF ni geolocalización de cámara (ADR-0007).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from apps.core.services import storage
from apps.reports_app.enums import MediaType

from .exceptions import PermanentAiMediaError, TransientAiMediaError

logger = logging.getLogger(__name__)

# La pipeline de S6 garantiza que la extensión de la clave coincide con el formato
# real del archivo, porque ambas se derivan de lo que Pillow detectó, nunca del
# nombre que envió el navegador.
_MIME_POR_EXTENSION = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}


@dataclass(frozen=True, slots=True)
class ReportImage:
    """Bytes de la fotografía y su tipo, sin rastro de dónde vive el objeto."""

    content: bytes
    mime_type: str


def load_report_image(report) -> ReportImage | None:
    """Descarga la fotografía del reporte, o `None` si no tiene ninguna.

    Un reporte sin fotografía se clasifica solo con texto. Uno que **sí** tiene
    evidencia registrada y no se puede leer no se clasifica en silencio sin ella:
    sería clasificar con menos información de la que el ciudadano aportó, sin que
    nadie lo note.
    """
    media = report.media.filter(media_type=MediaType.FOTO).order_by("created_at", "id").first()
    if media is None:
        return None

    key = media.file_url
    mime_type = _mime_por_clave(key)
    if mime_type is None:
        # La clave no tiene una extensión que la pipeline pueda haber producido.
        # Insistir no la va a arreglar.
        logger.warning(
            "La evidencia del reporte %s no tiene una extensión de imagen reconocible.",
            report.pk,
        )
        raise PermanentAiMediaError("La referencia de la imagen no es válida.")

    try:
        content = storage.download_private(storage.reports_bucket(), key)
    except storage.StorageObjectNotFoundError as exc:
        raise PermanentAiMediaError("La imagen del reporte no existe.") from exc
    except storage.StorageConfigurationError as exc:
        raise PermanentAiMediaError("El almacenamiento no está configurado.") from exc
    except storage.StorageUnavailableError as exc:
        raise TransientAiMediaError("El almacenamiento no está disponible.") from exc

    if not content:
        raise PermanentAiMediaError("La imagen del reporte está vacía.")

    return ReportImage(content=content, mime_type=mime_type)


def _mime_por_clave(key: str) -> str | None:
    lowered = key.lower()
    for extension, mime_type in _MIME_POR_EXTENSION.items():
        if lowered.endswith(extension):
            return mime_type
    return None
