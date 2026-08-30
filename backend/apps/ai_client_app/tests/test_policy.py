"""Pruebas de la política de confianza y de la derivación de cambios (S8-B).

Dos invariantes gobiernan el archivo: nada llega a aplicarse automáticamente por
accidente, y ninguna ruta del núcleo puede ordenar un rechazo.
"""

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from apps.admin_app.models import Category, Institution
from apps.ai_client_app import policy as policy_module
from apps.ai_client_app.contracts import ParsedClassification
from apps.ai_client_app.models import AiClassification
from apps.ai_client_app.policy import (
    AI_CONFIDENCE_THRESHOLD,
    derive_has_changes,
    resolve_classification,
)
from apps.core.enums import ReportPriority, ReportStatus
from apps.reports_app.models import Report, ReportStatusHistory

from ._ast_checks import code_identifiers, imported_names, string_literals

pytestmark = pytest.mark.django_db


def parsed(**overrides) -> ParsedClassification:
    datos = {
        "category_slug": "baches",
        "institution_slug": "municipalidad-de-asuncion",
        "priority": "ALTA",
        "confidence": Decimal("0.87"),
        "inconsistency": False,
        "justification": "Bache profundo en vía principal.",
    }
    datos.update(overrides)
    return ParsedClassification(**datos)


# --------------------------------------------------------------------------- #
# El umbral y su frontera
# --------------------------------------------------------------------------- #


def test_el_umbral_es_el_del_contrato():
    assert AI_CONFIDENCE_THRESHOLD == Decimal("0.75")


def test_el_umbral_por_defecto_es_el_del_contrato():
    """S8-D cableó la configuración; el valor del contrato sigue siendo el respaldo."""
    assert policy_module.DEFAULT_AI_CONFIDENCE_THRESHOLD == Decimal("0.75")
    assert policy_module.confidence_threshold() == Decimal("0.75")


@pytest.mark.parametrize("valor", [Decimal("0.9"), "0.6", 0.85])
def test_el_umbral_configurado_manda(settings, valor):
    """Acepta Decimal, cadena o número, y siempre devuelve Decimal."""
    settings.AI_CONFIDENCE_THRESHOLD = valor

    resultado = policy_module.confidence_threshold()

    assert isinstance(resultado, Decimal)
    assert resultado == Decimal(str(valor))


def test_sin_configuracion_cae_al_valor_del_contrato(settings):
    del settings.AI_CONFIDENCE_THRESHOLD

    assert policy_module.confidence_threshold() == Decimal("0.75")


def test_hay_una_sola_fuente_efectiva_del_umbral(settings):
    """Dos umbrales capaces de divergir en silencio serían peor que ninguno."""
    settings.AI_CONFIDENCE_THRESHOLD = Decimal("0.99")

    resuelta = resolve_classification(parsed(confidence=Decimal("0.95")))

    assert resuelta.eligible_for_auto_apply is False, "la policy ignoró el umbral configurado"


def test_el_umbral_no_se_lee_del_entorno_directamente():
    """La configuración entra por `settings`, no leyendo variables a mano."""
    assert code_identifiers(policy_module).isdisjoint({"environ", "getenv", "os"})


@pytest.mark.parametrize("valor", ["0.75", "0.751", "0.87", "0.95", "1", "1.0"])
def test_confianza_suficiente_con_todo_lo_demas_valido(valor):
    resuelta = resolve_classification(parsed(confidence=Decimal(valor)))

    assert resuelta.eligible_for_auto_apply is True
    assert resuelta.requires_human_review is False
    assert resuelta.effective_inconsistency is False


def test_el_limite_exacto_es_elegible():
    """0.75 alcanza el umbral: la comparación es `>=`, no `>`."""
    assert resolve_classification(parsed(confidence=Decimal("0.75"))).eligible_for_auto_apply


@pytest.mark.parametrize("valor", ["0", "0.0", "0.5", "0.74", "0.749", "0.7499999"])
def test_confianza_insuficiente_va_a_revision(valor):
    resuelta = resolve_classification(parsed(confidence=Decimal(valor)))

    assert resuelta.eligible_for_auto_apply is False
    assert resuelta.requires_human_review is True


def test_confianza_baja_no_es_inconsistencia():
    """Son cosas distintas: la duda del modelo no contradice su propia salida."""
    resuelta = resolve_classification(parsed(confidence=Decimal("0.10")))

    assert resuelta.requires_human_review is True
    assert resuelta.effective_inconsistency is False


# --------------------------------------------------------------------------- #
# Inconsistencia declarada y efectiva
# --------------------------------------------------------------------------- #


def test_inconsistencia_declarada_bloquea_aunque_la_confianza_sea_maxima():
    resuelta = resolve_classification(parsed(inconsistency=True, confidence=Decimal("1.0")))

    assert resuelta.eligible_for_auto_apply is False
    assert resuelta.requires_human_review is True
    assert resuelta.effective_inconsistency is True


@pytest.mark.parametrize("campo", ["category_slug", "institution_slug"])
def test_slug_desconocido_produce_inconsistencia_efectiva(campo):
    """El modelo se declara coherente y aun así devuelve algo que no existe."""
    resuelta = resolve_classification(parsed(**{campo: "no-existe-en-el-catalogo"}))

    assert resuelta.parsed.inconsistency is False
    assert resuelta.effective_inconsistency is True
    assert resuelta.requires_human_review is True
    assert resuelta.eligible_for_auto_apply is False


@pytest.mark.parametrize("campo", ["category_slug", "institution_slug"])
def test_slug_nulo_va_a_revision(campo):
    """Ausencia de sugerencia no es permiso para conservar el valor actual (D16)."""
    resuelta = resolve_classification(parsed(**{campo: None}))

    assert resuelta.effective_inconsistency is True
    assert resuelta.requires_human_review is True
    assert resuelta.eligible_for_auto_apply is False


def test_categoria_inactiva_va_a_revision(category):
    Category.objects.filter(pk=category.pk).update(is_active=False)

    resuelta = resolve_classification(parsed())

    assert resuelta.category is None
    assert resuelta.requires_human_review is True


def test_institucion_inactiva_va_a_revision(institution):
    Institution.objects.filter(pk=institution.pk).update(is_active=False)

    resuelta = resolve_classification(parsed())

    assert resuelta.institution is None
    assert resuelta.requires_human_review is True


def test_caso_valido_resuelve_los_objetos_del_catalogo(category, institution):
    resuelta = resolve_classification(parsed())

    assert resuelta.category == category
    assert resuelta.institution == institution


@pytest.mark.parametrize(
    "overrides",
    [
        {"confidence": Decimal("0.5"), "inconsistency": True},
        {"confidence": Decimal("0.5"), "category_slug": None},
        {"confidence": Decimal("0.99"), "inconsistency": True, "institution_slug": "no-existe"},
        {"category_slug": None, "institution_slug": None},
        {"confidence": Decimal("0"), "inconsistency": True, "category_slug": "no-existe"},
    ],
)
def test_ninguna_combinacion_de_fallos_produce_auto_aplicacion(overrides):
    resuelta = resolve_classification(parsed(**overrides))

    assert resuelta.eligible_for_auto_apply is False
    assert resuelta.requires_human_review is True


def test_revision_y_elegibilidad_son_siempre_opuestas():
    for overrides in ({}, {"inconsistency": True}, {"confidence": Decimal("0.1")}):
        resuelta = resolve_classification(parsed(**overrides))
        assert resuelta.eligible_for_auto_apply is not resuelta.requires_human_review


def test_el_resultado_es_inmutable():
    resuelta = resolve_classification(parsed())

    with pytest.raises(FrozenInstanceError):
        resuelta.eligible_for_auto_apply = True


# --------------------------------------------------------------------------- #
# Derivación de `hubo_cambio`
# --------------------------------------------------------------------------- #


def test_sin_diferencias():
    assert derive_has_changes("baches", "municipalidad-de-asuncion", "ALTA", parsed()) is False


@pytest.mark.parametrize(
    ("categoria", "institucion", "prioridad"),
    [
        ("semaforos", "municipalidad-de-asuncion", "ALTA"),
        ("baches", "ande", "ALTA"),
        ("baches", "municipalidad-de-asuncion", "MEDIA"),
        ("semaforos", "ande", "ALTA"),
        ("semaforos", "municipalidad-de-asuncion", "BAJA"),
        ("baches", "essap", "BAJA"),
        ("semaforos", "essap", "BAJA"),
    ],
    ids=[
        "categoria",
        "institucion",
        "prioridad",
        "categoria+institucion",
        "categoria+prioridad",
        "institucion+prioridad",
        "los-tres",
    ],
)
def test_cualquier_diferencia_cuenta_como_cambio(categoria, institucion, prioridad):
    assert derive_has_changes(categoria, institucion, prioridad, parsed()) is True


def test_actual_nulo_frente_a_sugerencia_con_valor():
    assert derive_has_changes(None, "municipalidad-de-asuncion", "ALTA", parsed()) is True
    assert derive_has_changes("baches", None, "ALTA", parsed()) is True


def test_actual_con_valor_frente_a_sugerencia_nula():
    assert (
        derive_has_changes(
            "baches", "municipalidad-de-asuncion", "ALTA", parsed(category_slug=None)
        )
        is True
    )


def test_nulo_frente_a_nulo_no_es_cambio():
    assert derive_has_changes("baches", None, "ALTA", parsed(institution_slug=None)) is False


def test_no_depende_de_ninguna_bandera_del_modelo():
    """`hubo_cambio` no existe en ParsedClassification: se calcula aquí (D4)."""
    assert not hasattr(parsed(), "hubo_cambio")

    sin_cambios = parsed()
    assert derive_has_changes("baches", "municipalidad-de-asuncion", "ALTA", sin_cambios) is False


# --------------------------------------------------------------------------- #
# Invariantes: la IA no rechaza y el núcleo no persiste
# --------------------------------------------------------------------------- #


def test_ningun_modulo_del_nucleo_puede_ordenar_un_rechazo():
    """La IA no puede llevar un reporte a RECHAZADO bajo ninguna ruta (D6).

    Se comprueba sobre el AST: ningún módulo importa `ReportStatus` ni escribe
    el literal en el código, de modo que no existe forma de emitirlo.
    """
    from apps.ai_client_app import catalog_mapping, contracts, parser
    from apps.ai_client_app.adapters import base, fake

    for modulo in (policy_module, contracts, parser, catalog_mapping, base, fake):
        assert "ReportStatus" not in imported_names(modulo)
        assert "ReportStatus" not in code_identifiers(modulo)
        assert "RECHAZADO" not in string_literals(modulo)


def test_la_clasificacion_no_expone_ningun_campo_de_estado():
    campos = set(ParsedClassification.__dataclass_fields__)

    assert campos.isdisjoint({"status", "new_status", "reject", "rejected", "decision_status"})
    assert campos == {
        "category_slug",
        "institution_slug",
        "priority",
        "confidence",
        "inconsistency",
        "justification",
    }


def test_el_resultado_resuelto_no_expone_estado():
    campos = set(policy_module.ResolvedClassification.__dataclass_fields__)

    assert campos.isdisjoint({"status", "new_status", "rejected", "decision_status"})


@pytest.mark.parametrize(
    "overrides",
    [{}, {"inconsistency": True}, {"confidence": Decimal("0")}, {"category_slug": None}],
)
def test_ningun_campo_del_resultado_contiene_un_estado(overrides):
    """Ni el peor caso produce algo parecido a una decisión de estado."""
    resuelta = resolve_classification(parsed(**overrides))

    valores = [
        getattr(resuelta, campo)
        for campo in policy_module.ResolvedClassification.__dataclass_fields__
    ]
    for estado in ReportStatus.values:
        assert estado not in valores


def test_el_nucleo_no_persiste_nada(report, category, institution):
    """Ni AiClassification, ni AuditLog, ni historial, ni cambios en Report."""
    from apps.admin_app.models import AuditLog

    antes = (
        AiClassification.objects.count(),
        AuditLog.objects.count(),
        ReportStatusHistory.objects.count(),
        Report.objects.count(),
    )
    estado_previo = (report.status, report.category_id, report.priority)

    resuelta = resolve_classification(parsed())
    derive_has_changes(report.category.slug, None, report.priority, resuelta.parsed)

    report.refresh_from_db()
    assert (
        AiClassification.objects.count(),
        AuditLog.objects.count(),
        ReportStatusHistory.objects.count(),
        Report.objects.count(),
    ) == antes
    assert (report.status, report.category_id, report.priority) == estado_previo


def test_la_prioridad_valida_sigue_siendo_la_del_enum_del_proyecto():
    """Sin enum duplicado: el contrato usa ReportPriority, no una copia."""
    assert policy_module._PRIORITIES == frozenset(ReportPriority.values)
