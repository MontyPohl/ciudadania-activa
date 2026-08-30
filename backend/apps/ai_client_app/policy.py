"""Política de confianza y revisión humana (S8-B).

Decide si una clasificación ya validada y resuelta es elegible para aplicarse de
forma automática asistida, o si debe esperar a un operador (D5, D16).

Nada de lo que hay aquí persiste. `ResolvedClassification` es un objeto en
memoria: no crea `AiClassification`, no escribe `AuditLog` ni
`ReportStatusHistory`, y no toca `Report`. Aplicar la decisión es trabajo de
S8-C.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings

from apps.admin_app.models import Category, Institution
from apps.core.enums import ReportPriority

from .catalog_mapping import resolve_category, resolve_institution
from .contracts import ParsedClassification

# Valor del contrato S8-A (D5). Es el respaldo cuando no hay configuración, no una
# segunda fuente: `confidence_threshold()` es la única que consulta la política, de
# modo que no puede haber dos umbrales divergiendo en silencio.
DEFAULT_AI_CONFIDENCE_THRESHOLD = Decimal("0.75")

# Nombre conservado por compatibilidad con el contrato escrito y con las pruebas
# que fijan el valor por defecto.
AI_CONFIDENCE_THRESHOLD = DEFAULT_AI_CONFIDENCE_THRESHOLD


def confidence_threshold() -> Decimal:
    """Umbral efectivo: el configurado, o el del contrato si no hay ninguno."""
    configured = getattr(settings, "AI_CONFIDENCE_THRESHOLD", None)
    if configured is None:
        return DEFAULT_AI_CONFIDENCE_THRESHOLD
    return configured if isinstance(configured, Decimal) else Decimal(str(configured))


_PRIORITIES: frozenset[str] = frozenset(ReportPriority.values)


@dataclass(frozen=True, slots=True)
class ResolvedClassification:
    """Clasificación validada, resuelta contra el catálogo y evaluada.

    `effective_inconsistency` no es lo mismo que `parsed.inconsistency`: el
    modelo puede declararse plenamente coherente y aun así haber propuesto un
    slug que no existe. Esa contradicción entre lo que dice y lo que devuelve es
    una inconsistencia real, aunque él no la vea.
    """

    parsed: ParsedClassification
    category: Category | None
    institution: Institution | None
    effective_inconsistency: bool
    requires_human_review: bool
    eligible_for_auto_apply: bool


def resolve_classification(parsed: ParsedClassification) -> ResolvedClassification:
    """Resuelve `parsed` contra el catálogo y aplica la política de confianza."""
    category = resolve_category(parsed.category_slug)
    institution = resolve_institution(parsed.institution_slug)

    # Un slug ausente y uno inexistente llegan al mismo sitio: en ambos casos no
    # hay sugerencia aplicable para ese campo (D16). La ausencia de sugerencia
    # es motivo de revisión, no permiso para conservar el valor actual y dar la
    # clasificación por buena.
    unresolved = category is None or institution is None
    effective_inconsistency = parsed.inconsistency or unresolved

    eligible = (
        parsed.confidence >= confidence_threshold()
        and not parsed.inconsistency
        and category is not None
        and institution is not None
        and parsed.priority in _PRIORITIES
        and not effective_inconsistency
    )

    return ResolvedClassification(
        parsed=parsed,
        category=category,
        institution=institution,
        effective_inconsistency=effective_inconsistency,
        requires_human_review=not eligible,
        eligible_for_auto_apply=eligible,
    )


def derive_has_changes(
    current_category_slug: str | None,
    current_institution_slug: str | None,
    current_priority: str | None,
    parsed: ParsedClassification,
) -> bool:
    """Indica si la sugerencia difiere del estado actual del reporte.

    El equivalente de `hubo_cambio` del Anexo H.3, pero derivado aquí en lugar de
    pedírselo al modelo (D4): es una comparación que el backend conoce con
    certeza y el modelo solo puede estimar.

    Devuelve `False` únicamente si los tres campos coinciden exactamente. Un
    `None` frente a un valor cuenta como cambio; dos `None` no.
    """
    return (
        current_category_slug != parsed.category_slug
        or current_institution_slug != parsed.institution_slug
        or current_priority != parsed.priority
    )
