"""Resolución del proveedor y configuración de runtime (S8-D).

La garantía central: **hacen falta las tres condiciones**. El interruptor está
separado de la credencial a propósito, porque una `GEMINI_API_KEY` olvidada en
las variables de un entorno no puede bastar para que un despliegue empiece a
clasificar reportes reales.
"""

import pytest
from django.conf import settings as django_settings

from apps.ai_client_app.adapters import runtime as runtime_module
from apps.ai_client_app.adapters.runtime import RuntimeAdapter, get_runtime_adapter

CLAVE_DE_PRUEBA = "test-api-key-not-real"
MODELO_DE_PRUEBA = "gemini-modelo-de-prueba"


@pytest.fixture
def sin_cliente_real(monkeypatch):
    """Impide que cualquier ruta construya un cliente de verdad."""
    creados = []

    def fake_build_client(*, api_key, timeout_seconds):
        creados.append({"api_key": api_key, "timeout_seconds": timeout_seconds})
        return object()

    monkeypatch.setattr("apps.ai_client_app.adapters.gemini.build_client", fake_build_client)
    return creados


# --------------------------------------------------------------------------- #
# Defaults de configuración
# --------------------------------------------------------------------------- #


def test_la_clasificacion_esta_apagada_por_defecto():
    assert django_settings.AI_CLASSIFICATION_ENABLED is False


def test_no_hay_credencial_ni_modelo_por_defecto():
    assert django_settings.GEMINI_API_KEY == ""
    assert django_settings.GEMINI_MODEL == ""


def test_los_valores_del_contrato_son_los_defaults():
    from decimal import Decimal

    assert django_settings.AI_CONFIDENCE_THRESHOLD == Decimal("0.75")
    assert django_settings.AI_MAX_RETRIES == 3
    assert django_settings.AI_REQUEST_TIMEOUT_SECONDS == 20


# --------------------------------------------------------------------------- #
# El proveedor solo se activa con las tres condiciones
# --------------------------------------------------------------------------- #


def test_sin_configuracion_no_hay_proveedor():
    assert get_runtime_adapter() is None


def test_con_credencial_pero_sin_interruptor_no_hay_proveedor(settings):
    """El caso que motiva el flag: la clave está, pero nadie activó nada."""
    settings.AI_CLASSIFICATION_ENABLED = False
    settings.GEMINI_API_KEY = CLAVE_DE_PRUEBA
    settings.GEMINI_MODEL = MODELO_DE_PRUEBA

    assert get_runtime_adapter() is None


def test_con_interruptor_pero_sin_credencial_no_hay_proveedor(settings):
    settings.AI_CLASSIFICATION_ENABLED = True
    settings.GEMINI_API_KEY = ""
    settings.GEMINI_MODEL = MODELO_DE_PRUEBA

    assert get_runtime_adapter() is None


def test_con_interruptor_pero_sin_modelo_no_hay_proveedor(settings):
    settings.AI_CLASSIFICATION_ENABLED = True
    settings.GEMINI_API_KEY = CLAVE_DE_PRUEBA
    settings.GEMINI_MODEL = ""

    assert get_runtime_adapter() is None


def test_con_las_tres_condiciones_se_resuelve_gemini(settings, sin_cliente_real):
    settings.AI_CLASSIFICATION_ENABLED = True
    settings.GEMINI_API_KEY = CLAVE_DE_PRUEBA
    settings.GEMINI_MODEL = MODELO_DE_PRUEBA

    runtime = get_runtime_adapter()

    assert isinstance(runtime, RuntimeAdapter)
    assert runtime.model_name == MODELO_DE_PRUEBA
    assert type(runtime.adapter).__name__ == "GeminiAiClassificationAdapter"


def test_el_timeout_contractual_llega_al_cliente(settings, sin_cliente_real):
    settings.AI_CLASSIFICATION_ENABLED = True
    settings.GEMINI_API_KEY = CLAVE_DE_PRUEBA
    settings.GEMINI_MODEL = MODELO_DE_PRUEBA

    get_runtime_adapter()

    assert sin_cliente_real[0]["timeout_seconds"] == 20


@pytest.mark.parametrize(
    "overrides",
    [
        {"AI_CLASSIFICATION_ENABLED": False},
        {"GEMINI_API_KEY": ""},
        {"GEMINI_MODEL": ""},
    ],
)
def test_sin_proveedor_no_se_construye_ningun_cliente(settings, sin_cliente_real, overrides):
    settings.AI_CLASSIFICATION_ENABLED = True
    settings.GEMINI_API_KEY = CLAVE_DE_PRUEBA
    settings.GEMINI_MODEL = MODELO_DE_PRUEBA
    for clave, valor in overrides.items():
        setattr(settings, clave, valor)

    assert get_runtime_adapter() is None
    assert sin_cliente_real == [], "se construyó un cliente sin proveedor activo"


# --------------------------------------------------------------------------- #
# El adaptador falso no vive en producción
# --------------------------------------------------------------------------- #


def test_el_runtime_no_conoce_el_adaptador_falso():
    import inspect

    fuente = inspect.getsource(runtime_module)

    assert "FakeAiClassificationAdapter" not in fuente
    assert "fake" not in fuente.lower()


def test_la_credencial_no_aparece_en_la_representacion(settings, sin_cliente_real):
    settings.AI_CLASSIFICATION_ENABLED = True
    settings.GEMINI_API_KEY = CLAVE_DE_PRUEBA
    settings.GEMINI_MODEL = MODELO_DE_PRUEBA

    runtime = get_runtime_adapter()

    assert CLAVE_DE_PRUEBA not in repr(runtime)
    assert CLAVE_DE_PRUEBA not in repr(runtime.adapter)
