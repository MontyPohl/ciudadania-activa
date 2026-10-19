"""Resolución del proveedor y configuración de runtime (S8-D).

La garantía central: **hacen falta todas las condiciones** —interruptor,
proveedor explícito, clave y modelo—. El interruptor está separado de la
credencial a propósito, porque una clave olvidada en las variables de un entorno
no puede bastar para que un despliegue empiece a clasificar reportes reales. Y el
proveedor es explícito (ADR-0016) para que tener claves de dos proveedores no
decida por sí solo cuál clasifica.
"""

import pytest
from django.conf import settings as django_settings

from apps.ai_client_app.adapters import runtime as runtime_module
from apps.ai_client_app.adapters.runtime import RuntimeAdapter, get_runtime_adapter

CLAVE_DE_PRUEBA = "test-api-key-not-real"
MODELO_DE_PRUEBA = "gemini-modelo-de-prueba"
MODELO_OPENAI = "openai-modelo-de-prueba"


@pytest.fixture
def sin_cliente_real(monkeypatch):
    """Impide que cualquier ruta construya un cliente de verdad."""
    creados = []

    def fake_build_client(*, api_key, timeout_seconds):
        creados.append({"api_key": api_key, "timeout_seconds": timeout_seconds})
        return object()

    monkeypatch.setattr("apps.ai_client_app.adapters.gemini.build_client", fake_build_client)
    monkeypatch.setattr("apps.ai_client_app.adapters.openai.build_client", fake_build_client)
    return creados


@pytest.fixture
def gemini_configurado(settings):
    settings.AI_CLASSIFICATION_ENABLED = True
    settings.AI_PROVIDER = "gemini"
    settings.GEMINI_API_KEY = CLAVE_DE_PRUEBA
    settings.GEMINI_MODEL = MODELO_DE_PRUEBA
    return settings


@pytest.fixture
def openai_configurado(settings):
    settings.AI_CLASSIFICATION_ENABLED = True
    settings.AI_PROVIDER = "openai"
    settings.OPENAI_API_KEY = CLAVE_DE_PRUEBA
    settings.OPENAI_MODEL = MODELO_OPENAI
    return settings


# --------------------------------------------------------------------------- #
# Defaults de configuración
# --------------------------------------------------------------------------- #


def test_la_clasificacion_esta_apagada_por_defecto():
    assert django_settings.AI_CLASSIFICATION_ENABLED is False


def test_no_hay_proveedor_credencial_ni_modelo_por_defecto():
    assert django_settings.AI_PROVIDER == ""
    assert django_settings.OPENAI_API_KEY == ""
    assert django_settings.OPENAI_MODEL == ""
    assert django_settings.OPENAI_REASONING_EFFORT == ""
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
    settings.AI_PROVIDER = "gemini"
    settings.GEMINI_API_KEY = CLAVE_DE_PRUEBA
    settings.GEMINI_MODEL = MODELO_DE_PRUEBA

    assert get_runtime_adapter() is None


def test_con_interruptor_pero_sin_credencial_no_hay_proveedor(settings):
    settings.AI_CLASSIFICATION_ENABLED = True
    settings.AI_PROVIDER = "gemini"
    settings.GEMINI_API_KEY = ""
    settings.GEMINI_MODEL = MODELO_DE_PRUEBA

    assert get_runtime_adapter() is None


def test_con_interruptor_pero_sin_modelo_no_hay_proveedor(settings):
    settings.AI_CLASSIFICATION_ENABLED = True
    settings.AI_PROVIDER = "gemini"
    settings.GEMINI_API_KEY = CLAVE_DE_PRUEBA
    settings.GEMINI_MODEL = ""

    assert get_runtime_adapter() is None


def test_con_las_tres_condiciones_se_resuelve_gemini(settings, sin_cliente_real):
    settings.AI_CLASSIFICATION_ENABLED = True
    settings.AI_PROVIDER = "gemini"
    settings.GEMINI_API_KEY = CLAVE_DE_PRUEBA
    settings.GEMINI_MODEL = MODELO_DE_PRUEBA

    runtime = get_runtime_adapter()

    assert isinstance(runtime, RuntimeAdapter)
    assert runtime.model_name == MODELO_DE_PRUEBA
    assert type(runtime.adapter).__name__ == "GeminiAiClassificationAdapter"


def test_el_timeout_contractual_llega_al_cliente(settings, sin_cliente_real):
    settings.AI_CLASSIFICATION_ENABLED = True
    settings.AI_PROVIDER = "gemini"
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
    settings.AI_PROVIDER = "gemini"
    settings.GEMINI_API_KEY = CLAVE_DE_PRUEBA
    settings.GEMINI_MODEL = MODELO_DE_PRUEBA
    for clave, valor in overrides.items():
        setattr(settings, clave, valor)

    assert get_runtime_adapter() is None
    assert sin_cliente_real == [], "se construyó un cliente sin proveedor activo"


# --------------------------------------------------------------------------- #
# Selección explícita del proveedor (ADR-0016)
# --------------------------------------------------------------------------- #


def test_con_openai_configurado_se_resuelve_openai(openai_configurado, sin_cliente_real):
    runtime = get_runtime_adapter()

    assert isinstance(runtime, RuntimeAdapter)
    assert runtime.model_name == MODELO_OPENAI
    assert type(runtime.adapter).__name__ == "OpenAiClassificationAdapter"
    assert sin_cliente_real == [{"api_key": CLAVE_DE_PRUEBA, "timeout_seconds": 20}]


def test_con_gemini_configurado_se_resuelve_gemini(gemini_configurado, sin_cliente_real):
    runtime = get_runtime_adapter()

    assert type(runtime.adapter).__name__ == "GeminiAiClassificationAdapter"


@pytest.mark.parametrize("valor", ["", "   ", "anthropic", "gemini-openai", "fake"])
def test_un_proveedor_vacio_o_desconocido_no_clasifica(openai_configurado, sin_cliente_real, valor):
    """Con claves presentes, lo único que falta es decir quién clasifica."""
    openai_configurado.AI_PROVIDER = valor
    openai_configurado.GEMINI_API_KEY = CLAVE_DE_PRUEBA
    openai_configurado.GEMINI_MODEL = MODELO_DE_PRUEBA

    assert get_runtime_adapter() is None
    assert sin_cliente_real == []


@pytest.mark.parametrize("valor", ["OpenAI", " openai ", "OPENAI"])
def test_el_proveedor_se_normaliza(openai_configurado, sin_cliente_real, valor):
    openai_configurado.AI_PROVIDER = valor

    assert type(get_runtime_adapter().adapter).__name__ == "OpenAiClassificationAdapter"


def test_las_claves_de_un_proveedor_no_activan_el_otro(openai_configurado, sin_cliente_real):
    """`AI_PROVIDER=gemini` con solo credenciales de OpenAI no clasifica con nadie."""
    openai_configurado.AI_PROVIDER = "gemini"

    assert get_runtime_adapter() is None
    assert sin_cliente_real == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"AI_CLASSIFICATION_ENABLED": False},
        {"OPENAI_API_KEY": ""},
        {"OPENAI_MODEL": ""},
    ],
)
def test_openai_incompleto_no_construye_cliente(openai_configurado, sin_cliente_real, overrides):
    for clave, valor in overrides.items():
        setattr(openai_configurado, clave, valor)

    assert get_runtime_adapter() is None
    assert sin_cliente_real == [], "se construyó un cliente sin proveedor activo"


def test_el_esfuerzo_de_razonamiento_llega_al_adaptador(openai_configurado, sin_cliente_real):
    openai_configurado.OPENAI_REASONING_EFFORT = "low"

    runtime = get_runtime_adapter()

    assert runtime.adapter._reasoning_effort == "low"


def test_la_credencial_de_openai_no_aparece_en_la_representacion(
    openai_configurado, sin_cliente_real
):
    runtime = get_runtime_adapter()

    assert CLAVE_DE_PRUEBA not in repr(runtime)
    assert CLAVE_DE_PRUEBA not in repr(runtime.adapter)


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
    settings.AI_PROVIDER = "gemini"
    settings.GEMINI_API_KEY = CLAVE_DE_PRUEBA
    settings.GEMINI_MODEL = MODELO_DE_PRUEBA

    runtime = get_runtime_adapter()

    assert CLAVE_DE_PRUEBA not in repr(runtime)
    assert CLAVE_DE_PRUEBA not in repr(runtime.adapter)


def test_openai_registra_version_v2(openai_configurado, sin_cliente_real):
    openai_configurado.AI_PROMPT_VERSION = "v2"
    runtime = get_runtime_adapter()
    assert runtime.prompt_version == "classification-v2"
    assert runtime.adapter._prompt_version == "v2"


def test_version_invalida_falla_cerrado(openai_configurado, sin_cliente_real, caplog):
    openai_configurado.AI_PROMPT_VERSION = "version-secreta-no-registrable"
    assert get_runtime_adapter() is None
    assert sin_cliente_real == []
    assert "Versión de prompt" in caplog.text
    assert "version-secreta" not in caplog.text


def test_gemini_conserva_v1_aunque_se_configure_v2(gemini_configurado, sin_cliente_real):
    gemini_configurado.AI_PROMPT_VERSION = "v2"
    assert get_runtime_adapter().prompt_version == "s8-classification-v1"
