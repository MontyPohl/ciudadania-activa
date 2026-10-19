"""Pruebas locales de prompts, sin llamadas al proveedor ni escrituras del flujo real."""

import csv
import io
import json
from types import SimpleNamespace

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from PIL import Image

from apps.admin_app.models import AuditLog, Category, Institution
from apps.ai_client_app import prompt_trial
from apps.ai_client_app.contracts import ProviderUsage
from apps.ai_client_app.exceptions import PermanentAiProviderError
from apps.ai_client_app.management.commands import ai_compare_prompts, ai_try_classification
from apps.ai_client_app.models import AiClassification
from apps.ai_client_app.prompt_trial import (
    build_trial_request,
    load_photo,
    normalize_catalog_label,
    normalize_priority,
    run_trial,
)
from apps.reports_app.models import Report


class ScriptedAdapter:
    def __init__(self):
        self.calls = []

    def classify_with_metadata(self, request):
        self.calls.append(request)
        return SimpleNamespace(
            text=json.dumps(
                {
                    "category_slug": request.current_category_slug,
                    "institution_slug": request.current_institution_slug,
                    "priority": "ALTA",
                    "confidence": 0.99,
                    "inconsistency": False,
                    "justification": "Prioridad del guion.",
                }
            ),
            usage=ProviderUsage(input_tokens=10, output_tokens=5, total_tokens=15),
        )


@pytest.mark.django_db
@pytest.mark.parametrize("delimiter", [",", ";"])
def test_comparacion_cuenta_prioridad_erronea_y_exporta(tmp_path, monkeypatch, category, delimiter):
    photo = tmp_path / "foto.png"
    Image.new("RGB", (8, 8), "red").save(photo)
    source = tmp_path / "casos.csv"
    with source.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=ai_compare_prompts.COLUMNS, delimiter=delimiter)
        writer.writeheader()
        for case_id, priority in (("1", "Alta"), ("2", "baja")):
            writer.writerow(
                {
                    "id": case_id,
                    "foto": photo.name,
                    "descripcion": "Bache en la calle.",
                    "categoria_elegida": category.name.upper(),
                    "categoria_esperada": category.slug,
                    "institucion_esperada": category.default_institution.name,
                    "prioridad_esperada": priority,
                    "por_que": "Referencia",
                    "tipo_caso": "normal",
                }
            )
        writer.writerow({})
        for label, filename in (("no-existe", photo.name), (category.slug, "ausente.png")):
            writer.writerow(
                {
                    "id": "inválido",
                    "foto": filename,
                    "descripcion": "Bache",
                    "categoria_elegida": category.slug,
                    "categoria_esperada": label,
                    "institucion_esperada": category.default_institution.slug,
                    "prioridad_esperada": "ALTA",
                    "por_que": "Referencia",
                    "tipo_caso": "normal",
                }
            )
    adapters = {}

    def build(version):
        adapters[version] = ScriptedAdapter()
        return adapters[version]

    monkeypatch.setattr(ai_compare_prompts, "build_trial_adapter", build)
    output = tmp_path / "salida.csv"
    stdout, stderr = io.StringIO(), io.StringIO()
    before = (Report.objects.count(), AuditLog.objects.count(), AiClassification.objects.count())
    call_command(
        "ai_compare_prompts",
        cases=str(source),
        photos=str(tmp_path),
        out=str(output),
        stdout=stdout,
        stderr=stderr,
    )
    text = stdout.getvalue()
    assert "Llamadas al proveedor: 4" in text
    assert text.count("casos=2") == 2
    assert text.count("Aciertos de categoría: 2/2 (100.00%)") == 2
    assert text.count("Aciertos de institución: 2/2 (100.00%)") == 2
    assert text.count("Aciertos de prioridad: 1/2 (50.00%)") == 2
    assert text.count("Aciertos de las tres juntas: 1/2 (50.00%)") == 2
    assert text.count("Se aplicarían solas: 2") == 2
    assert text.count("Se aplicarían solas con prioridad equivocada: 1") == 2
    assert text.count("Errores: 0") == 2
    assert text.count("Tokens totales: 30; promedio por caso: 15.00") == 2
    assert stderr.getvalue().count("omitida") == 2
    with output.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 4
    assert {(row["id"], row["prompt"]) for row in rows} == {
        (case_id, version) for case_id in ("1", "2") for version in ("v1", "v2")
    }
    assert [row["acierto_prioridad"] for row in rows] == ["True", "False", "True", "False"]
    assert all(row["auto_apply"] == "True" and row["total_tokens"] == "15" for row in rows)
    assert all(len(adapter.calls) == 2 for adapter in adapters.values())
    assert all(
        request.image_bytes == load_photo(photo)[0]
        for adapter in adapters.values()
        for request in adapter.calls
    )
    assert before == (
        Report.objects.count(),
        AuditLog.objects.count(),
        AiClassification.objects.count(),
    )


@pytest.mark.django_db
def test_peticion_y_etiquetas(category, institution):
    institution.name = "Municipalidad de Asunción"
    institution.save(update_fields=["name"])
    assert normalize_catalog_label("MUNICIPALIDAD DE ASUNCION", Institution) == institution.slug
    request = build_trial_request("Texto", category.name, None)
    assert request.current_institution_slug == category.default_institution.slug
    assert request.allowed_category_slugs == tuple(
        Category.objects.filter(is_active=True).order_by("slug").values_list("slug", flat=True)
    )
    assert request.allowed_institution_slugs == tuple(
        Institution.objects.filter(is_active=True).order_by("slug").values_list("slug", flat=True)
    )
    with pytest.raises(ValueError):
        build_trial_request("Texto", "no-existe")
    assert normalize_priority("mEdIa") == "MEDIA"
    with pytest.raises(ValueError):
        normalize_priority("urgente")


def test_foto_quita_metadata(tmp_path):
    path = tmp_path / "foto.png"
    exif = Image.Exif()
    exif[0x010E] = "dato privado"
    Image.new("RGB", (8, 8)).save(path, exif=exif)
    content, mime = load_photo(path)
    assert mime == "image/png"
    with Image.open(io.BytesIO(content)) as image:
        assert not image.getexif()
    assert b"dato privado" not in content


@pytest.mark.parametrize("parser_error", [False, True])
def test_fallos_solo_muestran_tipo(parser_error):
    class BrokenAdapter:
        def classify_with_metadata(self, request):
            if not parser_error:
                raise PermanentAiProviderError("secreto")
            return SimpleNamespace(text="secreto", usage=ProviderUsage(1, 2, 3))

    result = run_trial(BrokenAdapter(), object())
    expected_error = "ClassificationParseError" if parser_error else "PermanentAiProviderError"
    assert result.error == expected_error
    assert "secreto" not in repr(result)
    assert not result.auto_apply
    assert result.total_tokens == (3 if parser_error else None)


def test_adapter_no_depende_del_interruptor(settings, monkeypatch):
    settings.AI_CLASSIFICATION_ENABLED = False
    settings.OPENAI_API_KEY = "clave-ficticia"
    settings.OPENAI_MODEL = "modelo-prueba"
    monkeypatch.setattr(prompt_trial, "build_client", lambda **kwargs: object())
    adapter = prompt_trial.build_trial_adapter("v1")
    assert adapter._prompt_version == "v1"
    settings.OPENAI_API_KEY = ""
    with pytest.raises(CommandError):
        prompt_trial.build_trial_adapter("v1")


@pytest.mark.django_db
def test_comando_individual_reporta_error_sin_abortar(category, monkeypatch):
    class BrokenAdapter:
        def classify_with_metadata(self, request):
            raise PermanentAiProviderError("dato privado")

    monkeypatch.setattr(
        ai_try_classification, "build_trial_adapter", lambda version: BrokenAdapter()
    )
    output = io.StringIO()
    call_command(
        "ai_try_classification",
        description="Bache",
        category=category.slug,
        prompt="both",
        stdout=output,
    )
    assert output.getvalue().count("Error: PermanentAiProviderError") == 2
    assert "dato privado" not in output.getvalue()
    assert "Se aplicaría sola: no" in output.getvalue()


@pytest.mark.django_db
@pytest.mark.parametrize("destination", ["casos.csv", "foto.png"])
def test_salida_no_sobrescribe_entradas(tmp_path, category, monkeypatch, destination):
    photo = tmp_path / "foto.png"
    Image.new("RGB", (8, 8)).save(photo)
    source = tmp_path / "casos.csv"
    with source.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=ai_compare_prompts.COLUMNS)
        writer.writeheader()
        writer.writerow(
            {
                "id": "1",
                "foto": photo.name,
                "descripcion": "Bache",
                "categoria_elegida": category.slug,
                "categoria_esperada": category.slug,
                "institucion_esperada": category.default_institution.slug,
                "prioridad_esperada": "ALTA",
                "por_que": "Referencia",
                "tipo_caso": "normal",
            }
        )
    adapter = ScriptedAdapter()
    monkeypatch.setattr(ai_compare_prompts, "build_trial_adapter", lambda version: adapter)
    before = (source.read_bytes(), photo.read_bytes())
    with pytest.raises(CommandError, match="sobrescribir"):
        call_command(
            "ai_compare_prompts",
            cases=str(source),
            photos=str(tmp_path),
            out=str(tmp_path / destination),
            stdout=io.StringIO(),
        )
    assert before == (source.read_bytes(), photo.read_bytes())
    assert adapter.calls == []
