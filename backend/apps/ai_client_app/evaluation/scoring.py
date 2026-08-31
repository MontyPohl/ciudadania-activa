"""Métricas de concordancia (S8-E1).

**La métrica oficial de CA-4 es la del Anexo H de la tesis**: un caso concuerda
cuando coinciden exactamente `category_slug`, `institution_slug` y `priority`
—«Exactitud esperada >= 85% sobre categoria/institucion/prioridad»—. Ese es el
criterio normativo y el único al que se aplica el umbral del 85 %.

Junto a ella se calcula una **métrica estricta de diagnóstico** que añade
`inconsistency` a la comparación. No es CA-4 y no tiene umbral propio: sirve para
ver si el modelo, además de clasificar bien, reconoce cuándo la descripción
contradice lo que el ciudadano eligió. Un caso puede cumplir CA-4 y fallar la
estricta; lo contrario es imposible.

`inconsistency` conserva su etiqueta de oro y su exactitud propia. Queda fuera del
criterio oficial, no de la medición.

Fuera de ambas métricas quedan dos cosas, por motivos distintos. `confidence` es
una estimación del propio modelo, no una etiqueta humana: puntuarla mediría cuánto
se cree el modelo a sí mismo. Y `justification` es texto libre: dos redacciones
distintas pueden ser igual de correctas, así que se exige que exista y pase el
parser, pero no se puntúa su semántica.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from ..contracts import ParsedClassification
from .dataset import EvaluationCase

# Umbral metodológico de CA-4 / RNF-05. Pertenece **exclusivamente** a
# `ca4_concordance_pct`: la métrica estricta no tiene umbral porque no es un
# criterio de aceptación de la tesis. Se define aquí para que el número viva en un
# solo sitio, **no** para declarar éxito: con el adaptador falso, superarlo no
# significa nada sobre la capacidad del modelo (contrato S8-A, D19).
CA4_MIN_CONCORDANCE_PCT = Decimal("85.00")

_DOS_DECIMALES = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class CaseResult:
    """Resultado de un caso, con el desglose por campo.

    `ca4_concordant` es el criterio oficial —categoría, institución y prioridad—.
    `strict_concordant` añade `inconsistency` y es solo diagnóstico.

    `error_type` solo lleva el nombre de la excepción: un traceback dentro de la
    evidencia la haría irreproducible y podría arrastrar el contenido de la
    respuesta.
    """

    id: str
    ca4_concordant: bool
    strict_concordant: bool
    category_match: bool
    institution_match: bool
    priority_match: bool
    inconsistency_match: bool
    expected_category_slug: str | None
    expected_institution_slug: str | None
    expected_priority: str
    expected_inconsistency: bool
    predicted_category_slug: str | None = None
    predicted_institution_slug: str | None = None
    predicted_priority: str | None = None
    predicted_inconsistency: bool | None = None
    predicted_confidence: str | None = None
    justification_length: int | None = None
    error_type: str | None = None


def compare_case(case: EvaluationCase, parsed: ParsedClassification) -> CaseResult:
    """Compara una predicción válida con la etiqueta de oro.

    Las comparaciones son de igualdad exacta. `None` solo coincide con `None`: una
    sugerencia ausente no es un acierto parcial de la etiqueta que faltaba.
    """
    category_match = parsed.category_slug == case.expected.category_slug
    institution_match = parsed.institution_slug == case.expected.institution_slug
    priority_match = parsed.priority == case.expected.priority
    inconsistency_match = parsed.inconsistency == case.expected.inconsistency

    # Criterio oficial del Anexo H: los tres campos que la tesis enumera.
    ca4_concordant = category_match and institution_match and priority_match

    return CaseResult(
        id=case.id,
        ca4_concordant=ca4_concordant,
        # Diagnóstico: exige además haber reconocido la inconsistencia.
        strict_concordant=ca4_concordant and inconsistency_match,
        category_match=category_match,
        institution_match=institution_match,
        priority_match=priority_match,
        inconsistency_match=inconsistency_match,
        expected_category_slug=case.expected.category_slug,
        expected_institution_slug=case.expected.institution_slug,
        expected_priority=case.expected.priority,
        expected_inconsistency=case.expected.inconsistency,
        predicted_category_slug=parsed.category_slug,
        predicted_institution_slug=parsed.institution_slug,
        predicted_priority=parsed.priority,
        predicted_inconsistency=parsed.inconsistency,
        # Se registra como cadena para que la serialización sea estable y no
        # dependa de la representación binaria de un float.
        predicted_confidence=str(parsed.confidence),
        justification_length=len(parsed.justification),
    )


def failed_case(case: EvaluationCase, error_type: str) -> CaseResult:
    """Caso que no produjo una predicción válida: cuenta como no concordante.

    Un fallo del proveedor o una respuesta que el parser rechaza no es un empate
    ni un caso excluido: en producción tampoco habría clasificación.
    """
    return CaseResult(
        id=case.id,
        ca4_concordant=False,
        strict_concordant=False,
        category_match=False,
        institution_match=False,
        priority_match=False,
        inconsistency_match=False,
        expected_category_slug=case.expected.category_slug,
        expected_institution_slug=case.expected.institution_slug,
        expected_priority=case.expected.priority,
        expected_inconsistency=case.expected.inconsistency,
        error_type=error_type,
    )


def percentage(numerator: int, denominator: int) -> Decimal:
    """Porcentaje con dos decimales y redondeo explícito.

    `Decimal` y `ROUND_HALF_UP` en lugar de aritmética de coma flotante: la
    comparación con el 85 % no puede depender de cómo el binario represente un
    número que está justo en el límite.
    """
    if denominator == 0:
        return Decimal("0.00")
    valor = Decimal(numerator) * Decimal(100) / Decimal(denominator)
    return valor.quantize(_DOS_DECIMALES, rounding=ROUND_HALF_UP)
