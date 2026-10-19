"""Pruebas del resumen de uso, sin llamadas al proveedor."""

from datetime import datetime
from io import StringIO
from zoneinfo import ZoneInfo

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.ai_client_app.models import AiClassification

pytestmark = pytest.mark.django_db


def resumen(**opciones):
    salida = StringIO()
    call_command(
        "ai_usage_summary",
        input_usd_per_1m="2.50",
        output_usd_per_1m="10",
        stdout=salida,
        **opciones,
    )
    return salida.getvalue()


def test_costo_y_promedios_excluyen_clasificaciones_sin_uso(report):
    AiClassification.objects.create(
        report=report, input_tokens=1000, output_tokens=100, total_tokens=1100
    )
    AiClassification.objects.create(
        report=report, input_tokens=2000, output_tokens=200, total_tokens=2200
    )
    AiClassification.objects.create(report=report)

    salida = resumen()

    assert "Clasificaciones totales: 3" in salida
    assert "Con uso registrado: 2" in salida
    assert "Sin uso registrado: 1" in salida
    assert "Tokens de entrada: 3000" in salida
    assert "Tokens de salida: 300" in salida
    assert "Promedio de tokens de entrada por clasificación con uso: 1500.000000" in salida
    assert "Promedio de tokens de salida por clasificación con uso: 150.000000" in salida
    assert "Costo estimado total (USD): 0.010500" in salida
    assert "Costo estimado por clasificación con uso (USD): 0.005250" in salida
    assert AiClassification.objects.count() == 3


@pytest.mark.parametrize("campo", ["input_usd_per_1m", "output_usd_per_1m"])
def test_rechaza_precios_negativos(campo):
    precios = {"input_usd_per_1m": "2.50", "output_usd_per_1m": "10"}
    precios[campo] = "-1"

    with pytest.raises(CommandError, match="no negativos"):
        call_command("ai_usage_summary", **precios, stdout=StringIO())


@pytest.mark.parametrize("precio", ["NaN", "Infinity"])
def test_rechaza_precios_no_finitos(precio):
    with pytest.raises(CommandError, match="finitos"):
        call_command(
            "ai_usage_summary",
            input_usd_per_1m=precio,
            output_usd_per_1m="0",
            stdout=StringIO(),
        )


def test_filtra_por_fecha_inclusiva_y_modelo(report):
    anterior = AiClassification.objects.create(report=report, model_name="modelo-a")
    vigente = AiClassification.objects.create(report=report, model_name="modelo-a")
    AiClassification.objects.create(report=report, model_name="modelo-b")
    AiClassification.objects.filter(pk=anterior.pk).update(
        created_at=datetime(2026, 9, 30, 12, tzinfo=ZoneInfo("UTC"))
    )
    AiClassification.objects.filter(pk=vigente.pk).update(
        created_at=datetime(2026, 10, 1, 12, tzinfo=ZoneInfo("UTC"))
    )

    salida = resumen(since="2026-10-01", model="modelo-a")

    assert "Clasificaciones totales: 1" in salida
    assert "Sin uso registrado: 1" in salida


def test_rechaza_fecha_invalida():
    with pytest.raises(CommandError, match="fecha ISO"):
        resumen(since="2026-02-30")


def test_sin_clasificaciones_no_divide_por_cero():
    salida = resumen()

    assert "Clasificaciones totales: 0" in salida
    assert "Costo estimado total (USD): 0.000000" in salida
    assert "Costo estimado por clasificación con uso (USD): 0.000000" in salida


def test_contadores_parciales_y_cero_cuentan_como_uso(report):
    AiClassification.objects.create(report=report, input_tokens=1)
    AiClassification.objects.create(report=report, total_tokens=0)
    AiClassification.objects.create(report=report)

    salida = resumen()

    assert "Con uso registrado: 2" in salida
    assert "Sin uso registrado: 1" in salida
    assert "Costo estimado total (USD): 0.000003" in salida
    assert "Costo estimado por clasificación con uso (USD): 0.000001" in salida


def test_rechaza_precio_malformado_desde_argumentos_de_consola():
    with pytest.raises(CommandError, match="decimales finitos"):
        call_command(
            "ai_usage_summary",
            "--input-usd-per-1m",
            "abc",
            "--output-usd-per-1m",
            "0",
            stdout=StringIO(),
        )
