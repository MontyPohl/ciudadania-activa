"""Pruebas del adaptador falso (S8-B).

Lo que se verifica no es que clasifique bien —no es su trabajo— sino que sea
determinista y que su salida atraviese el parser real. Un doble que produjera
algo que el parser rechaza daría una confianza falsa sobre el camino de
producción.
"""

import inspect
import json

import pytest

from apps.ai_client_app.adapters import AiClassificationAdapter, FakeAiClassificationAdapter
from apps.ai_client_app.adapters import fake as fake_module
from apps.ai_client_app.contracts import OUTPUT_FIELDS, ClassificationRequest
from apps.ai_client_app.parser import parse_classification_response
from apps.core.enums import ReportPriority

from ._ast_checks import code_identifiers, imported_names

CATEGORIAS = ("agua-potable-perdidas", "baches", "otros", "semaforos")
INSTITUCIONES = ("ande", "essap", "municipalidad-de-asuncion")
PRIORIDADES = tuple(ReportPriority.values)


def build_request(**overrides) -> ClassificationRequest:
    datos = {
        "description": "Bache profundo en la calzada frente al mercado.",
        "current_category_slug": "baches",
        "current_institution_slug": "municipalidad-de-asuncion",
        "allowed_category_slugs": CATEGORIAS,
        "allowed_institution_slugs": INSTITUCIONES,
        "allowed_priorities": PRIORIDADES,
        "image_bytes": b"\xff\xd8\xff\xe0 bytes de una foto normalizada",
    }
    datos.update(overrides)
    return ClassificationRequest(**datos)


@pytest.fixture
def adapter():
    return FakeAiClassificationAdapter()


# --------------------------------------------------------------------------- #
# Interfaz
# --------------------------------------------------------------------------- #


def test_implementa_el_protocolo(adapter):
    assert isinstance(adapter, AiClassificationAdapter)


def test_la_firma_coincide_con_la_del_protocolo(adapter):
    esperada = inspect.signature(AiClassificationAdapter.classify)

    assert inspect.signature(type(adapter).classify) == esperada


def test_devuelve_texto_crudo_y_no_una_estructura(adapter):
    """El parser es la frontera: el adaptador no puede saltársela."""
    assert isinstance(adapter.classify(build_request()), str)


# --------------------------------------------------------------------------- #
# Determinismo
# --------------------------------------------------------------------------- #


def test_la_misma_peticion_produce_la_misma_salida_byte_a_byte(adapter):
    primera = adapter.classify(build_request())

    for _ in range(20):
        assert adapter.classify(build_request()) == primera


def test_dos_instancias_distintas_coinciden(adapter):
    assert adapter.classify(build_request()) == FakeAiClassificationAdapter().classify(
        build_request()
    )


@pytest.mark.parametrize(
    "prohibido", ["random", "datetime", "time", "uuid", "os", "secrets", "django.conf"]
)
def test_no_importa_nada_que_rompa_el_determinismo(prohibido):
    """Se mira el AST, no el texto: los docstrings hablan de estas palabras."""
    assert prohibido not in imported_names(fake_module)


@pytest.mark.parametrize("prohibido", ["now", "utcnow", "getenv", "environ", "settings", "uuid4"])
def test_no_invoca_reloj_entorno_ni_azar(prohibido):
    assert prohibido not in code_identifiers(fake_module)


def test_una_peticion_distinta_puede_producir_una_salida_distinta(adapter):
    """Determinista no es constante: la salida depende de la entrada."""
    otra = adapter.classify(build_request(current_category_slug="semaforos"))

    assert json.loads(otra)["category_slug"] == "semaforos"


# --------------------------------------------------------------------------- #
# La salida cumple el contrato
# --------------------------------------------------------------------------- #


def test_la_salida_pasa_el_parser_real(adapter):
    parsed = parse_classification_response(adapter.classify(build_request()))

    assert parsed.category_slug == "baches"
    assert parsed.institution_slug == "municipalidad-de-asuncion"
    assert parsed.priority in ReportPriority.values
    assert 0 <= parsed.confidence <= 1
    assert parsed.inconsistency is False
    assert parsed.justification


def test_la_salida_tiene_exactamente_los_seis_campos(adapter):
    assert set(json.loads(adapter.classify(build_request()))) == set(OUTPUT_FIELDS)


def test_conserva_el_valor_actual_cuando_esta_permitido(adapter):
    """Comportamiento documentado: confirmar lo que ya eligió el ciudadano."""
    payload = json.loads(adapter.classify(build_request()))

    assert payload["category_slug"] == "baches"
    assert payload["institution_slug"] == "municipalidad-de-asuncion"


def test_cae_al_menor_permitido_cuando_el_actual_no_lo_esta(adapter):
    payload = json.loads(
        adapter.classify(
            build_request(
                current_category_slug="categoria-retirada",
                current_institution_slug="institucion-retirada",
            )
        )
    )

    assert payload["category_slug"] == min(CATEGORIAS)
    assert payload["institution_slug"] == min(INSTITUCIONES)


def test_institucion_actual_nula(adapter):
    payload = json.loads(adapter.classify(build_request(current_institution_slug=None)))

    assert payload["institution_slug"] == min(INSTITUCIONES)


def test_sin_catalogo_permitido_devuelve_null_y_sigue_siendo_parseable(adapter):
    raw = adapter.classify(build_request(allowed_category_slugs=(), allowed_institution_slugs=()))
    parsed = parse_classification_response(raw)

    assert parsed.category_slug is None
    assert parsed.institution_slug is None


def test_la_confianza_supera_el_umbral_del_contrato(adapter):
    from apps.ai_client_app.policy import AI_CONFIDENCE_THRESHOLD

    parsed = parse_classification_response(adapter.classify(build_request()))

    assert parsed.confidence >= AI_CONFIDENCE_THRESHOLD


# --------------------------------------------------------------------------- #
# Aislamiento: sin base de datos y sin red
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_no_consulta_la_base_de_datos(adapter, django_assert_num_queries):
    with django_assert_num_queries(0):
        adapter.classify(build_request())


def test_funciona_sin_base_de_datos(adapter):
    """Sin marca `django_db`: tocar la base aquí sería un error de pytest-django."""
    assert adapter.classify(build_request())


@pytest.mark.parametrize(
    "prohibido",
    [
        "google",
        "google.genai",
        "google.generativeai",
        "anthropic",
        "openai",
        "requests",
        "httpx",
        "aiohttp",
        "urllib",
        "urllib.request",
        "socket",
    ],
)
def test_el_modulo_no_importa_ningun_sdk_ni_cliente_http(prohibido):
    assert prohibido not in imported_names(fake_module)
