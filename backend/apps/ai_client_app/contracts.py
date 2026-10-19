"""Contratos de datos del núcleo de clasificación (S8-B).

Estructuras puras: no consultan la base de datos, no llaman a ningún proveedor y
no persisten nada. Materializan el contrato cerrado en S8-A
(`docs/AI_CLASSIFICATION_CONTRACT.md`, D1 y D3).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

# Claves exactas del esquema de salida v1 (D3). El parser rechaza cualquier
# respuesta cuyo conjunto de claves no sea exactamente este.
OUTPUT_FIELDS: frozenset[str] = frozenset(
    {
        "category_slug",
        "institution_slug",
        "priority",
        "confidence",
        "inconsistency",
        "justification",
    }
)

MAX_JUSTIFICATION_CHARS = 1000


@dataclass(frozen=True, slots=True)
class ClassificationRequest:
    """Lo único que puede viajar hacia el proveedor (D1, lista blanca).

    Deliberadamente ausentes: `report_id`, ciudadano, coordenadas, dirección,
    timestamps, hashes, puntos y confirmaciones. La estructura no los acepta, de
    modo que no puede filtrarlos ni siquiera por descuido: para enviar un dato
    prohibido habría que modificar este contrato.

    Los catálogos permitidos son tuplas y no listas porque el conjunto es parte
    del contrato de la petición, no una colección que el adapter pueda mutar.
    """

    description: str
    current_category_slug: str
    current_institution_slug: str | None
    allowed_category_slugs: tuple[str, ...]
    allowed_institution_slugs: tuple[str, ...]
    allowed_priorities: tuple[str, ...]
    image_bytes: bytes
    # Metadata técnica de la imagen, necesaria porque la pipeline admite JPEG y PNG
    # y el proveedor debe saber cuál recibe. `None` cuando no hay imagen. No es una
    # ruta ni una URL: no dice dónde vive el objeto, solo qué es.
    image_mime_type: str | None = None


@dataclass(frozen=True, slots=True)
class ParsedClassification:
    """Salida del proveedor ya validada contra el esquema v1.

    No incluye `hubo_cambio`: lo deriva el backend comparando con el reporte
    (D4, `policy.derive_has_changes`). Tampoco incluye `raw_response`, `prompt`
    ni `model_name`: pertenecen a la capa de auditoría, que es de S8-C.

    Que un slug esté presente aquí no significa que exista en el catálogo. Esta
    estructura garantiza forma, no existencia; resolverla es trabajo de
    `catalog_mapping`.
    """

    category_slug: str | None
    institution_slug: str | None
    priority: str
    confidence: Decimal
    inconsistency: bool
    justification: str


@dataclass(frozen=True, slots=True)
class ProviderUsage:
    """Tokens informados por el proveedor; `None` indica un contador ausente."""

    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
