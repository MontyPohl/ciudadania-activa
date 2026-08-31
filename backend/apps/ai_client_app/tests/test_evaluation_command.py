"""Comando de evaluación (S8-E1).

Solo admite el adaptador falso. Medir contra el proveedor real cuesta dinero y
cuota, así que no puede dispararse desde un comando de conveniencia: pedirlo se
rechaza en lugar de degradarse en silencio.
"""

import json
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


def ejecutar(**opciones) -> str:
    salida = StringIO()
    call_command("evaluate_ai_classification", stdout=salida, **opciones)
    return salida.getvalue()


# --------------------------------------------------------------------------- #
# Solo el adaptador falso
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("adapter", ["gemini", "runtime", "real", "production", ""])
def test_rechaza_cualquier_adaptador_que_no_sea_el_falso(adapter):
    with pytest.raises(CommandError, match="no admitido"):
        ejecutar(adapter=adapter)


def test_el_adaptador_por_defecto_es_el_falso():
    assert "fake" in ejecutar()


def test_no_consulta_la_configuracion_del_proveedor():
    import inspect

    from apps.ai_client_app.management.commands import evaluate_ai_classification as comando

    fuente = inspect.getsource(comando)

    assert "GEMINI_API_KEY" not in fuente
    assert "get_runtime_adapter" not in fuente
    assert "GeminiAiClassificationAdapter" not in fuente


# --------------------------------------------------------------------------- #
# Salida
# --------------------------------------------------------------------------- #


def test_la_salida_de_texto_incluye_las_metricas():
    salida = ejecutar()

    for esperado in (
        "s8-evaluation-v1",
        "casos",
        "CA-4 (cat+inst+prio)",
        "estricta (+inconsist)",
        "categoría",
        "institución",
        "prioridad",
        "inconsistencia",
        "errores",
    ):
        assert esperado in salida


def test_la_salida_distingue_la_metrica_oficial_de_la_diagnostica():
    """CA-4 y la estricta no pueden confundirse al leer el resultado."""
    salida = ejecutar()

    assert "CA-4" in salida
    assert "estricta" in salida
    assert salida.index("CA-4") < salida.index("estricta"), "lo oficial va primero"


def test_la_salida_advierte_que_el_resultado_no_demuestra_ca4():
    """El aviso es la parte importante de este comando."""
    salida = ejecutar()

    assert "NO demuestra CA-4" in salida
    assert "proveedor real" in salida


def test_la_salida_json_es_valida_y_estable():
    primera = ejecutar(format="json")
    segunda = ejecutar(format="json")

    assert primera == segunda

    datos = json.loads(primera)
    assert datos["dataset_version"] == "s8-evaluation-v1"
    assert datos["adapter_name"] == "fake"
    assert datos["total_cases"] == 28
    assert datos["errors_count"] == 0
    assert "ca4_concordance_pct" in datos
    assert "strict_concordance_pct" in datos


def test_el_json_no_declara_si_supera_el_umbral():
    datos = json.loads(ejecutar(format="json"))

    assert "meets_threshold" not in datos


def test_la_salida_no_revela_entorno_ni_rutas():
    salida = ejecutar(format="json")

    for prohibido in ("/home/", "GEMINI", "SECRET", "hostname", "DATABASE_URL"):
        assert prohibido not in salida


@pytest.mark.django_db
def test_el_comando_no_consulta_la_base_de_datos(django_assert_num_queries):
    """El benchmark trabaja sobre el dataset congelado, no sobre el catálogo vivo."""
    with django_assert_num_queries(0):
        ejecutar(format="json")
