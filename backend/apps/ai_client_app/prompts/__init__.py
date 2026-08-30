"""Renderizado del prompt versionado (S8-D).

El archivo `classification_v1.txt` es el contrato y **no se modifica aquí**: se
lee tal cual está versionado en Git. Lo único que hace este módulo es rellenar
sus marcadores con los datos permitidos.

El prompt renderizado no se persiste ni se audita: la auditoría guarda
`prompt_version`, para no duplicar la descripción del ciudadano en `audit_logs`
en cada clasificación y cada reproceso (contrato S8-A, D15).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from ..contracts import ClassificationRequest

PROMPT_VERSION = "s8-classification-v1"

_PROMPT_PATH = Path(__file__).resolve().parent / "classification_v1.txt"

# Separa las instrucciones del sistema de la sección de entrada. Lo que se envía
# como `system_instruction` es solo la primera mitad: los datos del caso viajan en
# el turno del usuario, que es donde corresponde el contenido no confiable.
_MARCA_ENTRADA = "ENTRADA"


@lru_cache(maxsize=1)
def load_prompt_template() -> str:
    """Contenido íntegro del prompt versionado."""
    return _PROMPT_PATH.read_text(encoding="utf-8")


def render_classification_prompt(request: ClassificationRequest) -> str:
    """Instrucciones del sistema con los catálogos permitidos ya resueltos.

    La `description` **no** se interpola aquí: viaja en el turno del usuario,
    delimitada como contenido no confiable. Mezclarla con las instrucciones sería
    exactamente lo que las reglas del prompt piden evitar.
    """
    plantilla = load_prompt_template()
    return (
        plantilla.replace(
            "{allowed_category_slugs}",
            json.dumps(list(request.allowed_category_slugs)),
        )
        .replace(
            "{allowed_institution_slugs}",
            json.dumps(list(request.allowed_institution_slugs)),
        )
        .replace("{allowed_priorities}", json.dumps(list(request.allowed_priorities)))
        .replace("{current_category_slug}", str(request.current_category_slug))
        .replace(
            "{current_institution_slug}",
            request.current_institution_slug or "null",
        )
        .replace("{image}", "adjunta" if request.image_bytes else "no disponible")
        .replace(
            "{description}",
            "(se envía por separado como contenido no confiable del usuario)",
        )
    )


__all__ = ["PROMPT_VERSION", "load_prompt_template", "render_classification_prompt"]
