import base64
import io
import json
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.ai_client_app.contracts import ClassificationRequest
from apps.auth_app.services.identity import InvalidSelfieError, validate_selfie
from apps.experiments_app.datasets import (
    AI_DATASET_ID,
    FUNCTIONAL_DATASET_ID,
    LOAD_DATASET_ID,
    dataset_registry,
    generate_dataset,
)
from apps.experiments_app.synthetic_ai import S11SyntheticAiAdapter
from apps.reports_app.services.photos import InvalidReportPhotoError, prepare_report_photo


@pytest.mark.parametrize("dataset_id", [FUNCTIONAL_DATASET_ID, LOAD_DATASET_ID])
def test_same_seed_produces_identical_dataset_and_hash(dataset_id):
    first = generate_dataset(dataset_id, seed=20260921)
    second = generate_dataset(dataset_id, seed=20260921)
    assert first.bytes == second.bytes
    assert first.sha256 == second.sha256


def test_different_seed_changes_dataset():
    first = generate_dataset(FUNCTIONAL_DATASET_ID, seed=20260921)
    second = generate_dataset(FUNCTIONAL_DATASET_ID, seed=20260922)
    assert first.sha256 != second.sha256


def test_functional_dataset_has_frozen_shape_and_no_real_data():
    dataset = generate_dataset(FUNCTIONAL_DATASET_ID).payload
    assert len(dataset["reports"]) == 20
    assert len(dataset["institutions"]) == 3
    assert len({row["category"]["slug"] for row in dataset["reports"]}) == 10
    assert all(
        sum(report["category"]["slug"] == slug for report in dataset["reports"]) == 2
        for slug in {row["category"]["slug"] for row in dataset["reports"]}
    )
    serialized = str(dataset).lower()
    assert "synthetic" in serialized
    assert "@gmail" not in serialized
    assert "document_number" not in serialized


def test_load_dataset_has_ten_by_five_unique_payloads_and_pngs():
    dataset = generate_dataset(LOAD_DATASET_ID).payload
    assert len(dataset["users"]) == 10
    assert len(dataset["requests"]) == 50
    assert {request["sequence_for_user"] for request in dataset["requests"]} == {1, 2, 3, 4, 5}
    assert len({request["request_key"] for request in dataset["requests"]}) == 50
    assert len({request["image"]["sha256"] for request in dataset["requests"]}) == 50
    assert all(
        base64.b64decode(item["image"]["base64"]).startswith(b"\x89PNG")
        for item in dataset["requests"]
    )


def test_registry_covers_only_ex02_to_ex10():
    registry = dataset_registry()
    scenarios = {scenario for metadata in registry.values() for scenario in metadata["scenarios"]}
    assert scenarios == {f"EX-{number:02d}" for number in range(2, 11)}
    assert registry[AI_DATASET_ID]["reference_only"] is True
    assert "EX-01" not in scenarios
    assert "EX-11" not in scenarios


def test_versioned_datasets_match_registry_hashes():
    data_root = Path(__file__).resolve().parents[1] / "data"
    versioned = json.loads((data_root / "dataset-registry.json").read_text(encoding="utf-8"))
    generated = dataset_registry()
    for dataset_id in (FUNCTIONAL_DATASET_ID, LOAD_DATASET_ID):
        metadata = versioned["datasets"][dataset_id]
        assert metadata["sha256"] == generated[dataset_id]["sha256"]
        assert (data_root / metadata["path"]).read_bytes() == generate_dataset(dataset_id).bytes


def test_s11_fake_preserves_dataset_catalog_mapping_without_network():
    request = ClassificationRequest(
        description="SYNTHETIC",
        current_category_slug="agua-potable-perdidas",
        current_institution_slug=None,
        allowed_category_slugs=("agua-potable-perdidas",),
        allowed_institution_slugs=("ande", "essap", "municipalidad-de-asuncion"),
        allowed_priorities=("BAJA", "MEDIA", "ALTA"),
        image_bytes=b"synthetic",
        image_mime_type="image/png",
    )
    result = json.loads(S11SyntheticAiAdapter().classify(request))
    assert result["category_slug"] == "agua-potable-perdidas"
    assert result["institution_slug"] == "essap"
    assert result["priority"] == "MEDIA"


# --------------------------------------------------------------------------- #
# Regresión A6: las imágenes que consumen los fixtures S11 deben ser válidas
# --------------------------------------------------------------------------- #


def test_las_imagenes_del_dataset_funcional_pasan_la_validacion_real_del_backend():
    """Los fixtures S11 toman sus bytes de aquí: deben superar los validadores reales.

    Se reutilizan los validadores del proyecto en vez de reimplementar la
    comprobación: `prepare_report_photo` —que es también el que aplica
    `create_resolution_evidence` a la evidencia de resolución— y `validate_selfie`,
    ambos apoyados en `Image.verify()`.
    """
    dataset = generate_dataset(FUNCTIONAL_DATASET_ID).payload
    assert dataset["reports"]

    for report in dataset["reports"]:
        imagen = report["image"]
        crudo = base64.b64decode(imagen["base64"])
        nombre = imagen["filename"]

        preparada = prepare_report_photo(
            SimpleUploadedFile(nombre, crudo, content_type=imagen["mime_type"])
        )
        assert preparada.format == "PNG"

        # El mismo insumo alimenta la selfie sintética de EX-02.
        assert (
            validate_selfie(SimpleUploadedFile(nombre, crudo, content_type="image/png")).format
            == "PNG"
        )

        # Decodificable de verdad, no sólo abrible: `Image.open` es perezoso.
        with Image.open(io.BytesIO(crudo)) as abierta:
            abierta.load()


def test_una_firma_png_intacta_no_basta_para_aceptar_la_imagen():
    """A6: el PNG del spec conservaba la firma y tenía el IDAT corrupto.

    `startswith(b"\\x89PNG")` lo habría dado por bueno; los validadores reales no.
    """
    dataset = generate_dataset(FUNCTIONAL_DATASET_ID).payload
    crudo = bytearray(base64.b64decode(dataset["reports"][0]["image"]["base64"]))
    inicio_payload = crudo.find(b"IDAT") + 4
    crudo[inicio_payload + 5] ^= 0xFF
    corrupto = bytes(crudo)

    assert corrupto.startswith(b"\x89PNG"), "la firma debe seguir intacta para que la prueba valga"

    with pytest.raises(InvalidReportPhotoError):
        prepare_report_photo(SimpleUploadedFile("corrupta.png", corrupto, content_type="image/png"))
    with pytest.raises(InvalidSelfieError):
        validate_selfie(SimpleUploadedFile("corrupta.png", corrupto, content_type="image/png"))
