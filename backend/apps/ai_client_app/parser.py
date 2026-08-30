"""Parser estricto de la respuesta del proveedor (S8-B).

Es la frontera de seguridad del contrato: el prompt reduce la probabilidad de una
salida inválida, pero solo esto la impide (D13). Función pura — sin base de
datos, sin logging del contenido y sin efectos.

La política es **rechazar**, nunca corregir. No hay coerción de tipos, no se
adivina un valor a partir de otro parecido y no se ignoran campos sobrantes: un
proveedor que empieza a devolver algo distinto es un cambio de contrato que debe
verse, no absorberse (D3).
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from apps.core.enums import ReportPriority

from .contracts import MAX_JUSTIFICATION_CHARS, OUTPUT_FIELDS, ParsedClassification

_PRIORITIES: frozenset[str] = frozenset(ReportPriority.values)

_CONFIDENCE_MIN = Decimal("0")
_CONFIDENCE_MAX = Decimal("1")


class ClassificationParseError(ValueError):
    """La respuesta del proveedor no cumple el esquema v1.

    Excepción pública del parser: quien lo invoca captura esto y no
    `JSONDecodeError`, `KeyError` o `TypeError`.

    Sus mensajes son deliberadamente pobres. Nunca incluyen la respuesta cruda,
    la descripción del ciudadano ni valor alguno del payload, porque terminan en
    logs y en auditoría. Nombran el campo que falló, no lo que contenía.
    """


def parse_classification_response(raw: str) -> ParsedClassification:
    """Valida `raw` contra el esquema v1 y devuelve la clasificación.

    Rechaza todo lo que el JSON estándar aceptaría de más: `NaN`, `Infinity`,
    `-Infinity`, claves duplicadas, cualquier top-level que no sea un objeto y,
    por extensión natural, las vallas Markdown y el texto suelto alrededor del
    objeto, que no son JSON válido.
    """
    data = _load_strict_json(raw)

    if not isinstance(data, dict):
        raise ClassificationParseError("invalid top-level type")

    _check_exact_fields(data)

    return ParsedClassification(
        category_slug=_slug_or_none("category_slug", data["category_slug"]),
        institution_slug=_slug_or_none("institution_slug", data["institution_slug"]),
        priority=_priority(data["priority"]),
        confidence=_confidence(data["confidence"]),
        inconsistency=_boolean("inconsistency", data["inconsistency"]),
        justification=_justification(data["justification"]),
    )


def _load_strict_json(raw: str) -> Any:
    if not isinstance(raw, str):
        raise ClassificationParseError("invalid raw type")
    try:
        return json.loads(
            raw,
            parse_constant=_reject_constant,
            parse_float=Decimal,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except ClassificationParseError:
        raise
    except (ValueError, TypeError, RecursionError) as exc:
        # `json` no distingue una valla Markdown de basura cualquiera: ambas son
        # JSON inválido, que es exactamente el veredicto correcto.
        raise ClassificationParseError("invalid json") from exc


def _reject_constant(_name: str) -> Any:
    # `json` llama aquí para NaN, Infinity y -Infinity. Ninguno es un número
    # representable en el contrato ni comparable con el umbral de confianza.
    raise ClassificationParseError("invalid json constant")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    # Sin este hook, `json` conserva en silencio la última aparición: una
    # respuesta con dos `confidence` distintos entraría como si fuese una sola.
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ClassificationParseError(f"duplicate field: {key}")
        result[key] = value
    return result


def _check_exact_fields(data: dict[str, Any]) -> None:
    keys = set(data)
    missing = OUTPUT_FIELDS - keys
    if missing:
        raise ClassificationParseError(f"missing field: {min(missing)}")
    unknown = keys - OUTPUT_FIELDS
    if unknown:
        raise ClassificationParseError(f"unknown field: {min(unknown)}")


def _slug_or_none(field: str, value: Any) -> str | None:
    if value is None:
        return None
    # `bool` primero: es subclase de `int`, pero tampoco sería `str`; se
    # comprueba igual para que el rechazo sea explícito y no incidental.
    if type(value) is bool or not isinstance(value, str) or not value:
        raise ClassificationParseError(f"invalid field: {field}")
    # Sin `strip()`: recortar aquí convertiría un slug inválido en uno válido y
    # el mapeo exacto dejaría de serlo. Un slug con espacios pasa como cadena y
    # falla después contra el catálogo, que es donde debe fallar (D2).
    return value


def _priority(value: Any) -> str:
    if type(value) is bool or not isinstance(value, str) or value not in _PRIORITIES:
        raise ClassificationParseError("invalid field: priority")
    return value


def _confidence(value: Any) -> Decimal:
    # `isinstance(True, int)` es verdadero en Python, así que el booleano se
    # descarta antes de cualquier otra comprobación numérica.
    if type(value) is bool or not isinstance(value, int | Decimal):
        raise ClassificationParseError("invalid field: confidence")
    confidence = Decimal(value) if isinstance(value, int) else value
    if not confidence.is_finite():
        raise ClassificationParseError("invalid field: confidence")
    if not _CONFIDENCE_MIN <= confidence <= _CONFIDENCE_MAX:
        raise ClassificationParseError("invalid field: confidence")
    return confidence


def _boolean(field: str, value: Any) -> bool:
    if type(value) is not bool:
        raise ClassificationParseError(f"invalid field: {field}")
    return value


def _justification(value: Any) -> str:
    if type(value) is bool or not isinstance(value, str):
        raise ClassificationParseError("invalid field: justification")
    # Única normalización admitida: recorte exterior. El interior no se toca.
    # El límite se mide sobre el texto ya recortado, que es el que se persistirá.
    justification = value.strip()
    if not justification or len(justification) > MAX_JUSTIFICATION_CHARS:
        raise ClassificationParseError("invalid field: justification")
    return justification
