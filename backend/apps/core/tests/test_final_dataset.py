"""Verifica aislamiento y trazabilidad S15 sin fabricar movimientos de financiación."""

import hashlib
import json
import shutil
import uuid
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from apps.core.final_dataset import generator, verification
from apps.core.final_dataset.files import read_manifest, seal, write_json
from apps.core.final_dataset.isolation import DatasetError, guard_environment
from apps.core.final_dataset.oracle import expected_ledger
from apps.core.final_dataset.recipe import INSTITUTIONS, Recipe, synthetic_image
from apps.rewards_app.models import RewardRedemption, RewardTransaction


def local_settings():
    redis = "redis://ca-s15-redis:6379/0"
    return SimpleNamespace(
        DEBUG=True,
        DATABASES={
            "default": {
                "NAME": "ciudadania_s15",
                "HOST": "ca-s15-db",
                "ENGINE": "django.contrib.gis.db.backends.postgis",
            }
        },
        REDIS_URL=redis,
        CELERY_BROKER_URL=redis,
        CELERY_RESULT_BACKEND=redis,
        CACHES={"default": {"LOCATION": redis}},
        S11_LOCAL_STORAGE_ENABLED=False,
        SUPABASE_URL="http://127.0.0.1:18515",
        SUPABASE_STORAGE_BUCKET_PRIVATE="s15-private",
        SUPABASE_STORAGE_BUCKET_REPORTS="s15-reports",
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("DEBUG", False),
        ("REDIS_URL", "redis://localhost:6379/15"),
        ("CELERY_BROKER_URL", "redis://ca-s14-redis:6379/0"),
        ("SUPABASE_URL", "https://production.supabase.co"),
        ("SUPABASE_STORAGE_BUCKET_REPORTS", "reportes"),
        ("S11_LOCAL_STORAGE_ENABLED", True),
    ],
)
def test_guard_rejects_shared_or_production_targets(field, value):
    config = local_settings()
    setattr(config, field, value)
    with pytest.raises(DatasetError):
        guard_environment(config, Path("/tmp/ciudadania-s15/guard"))


@pytest.mark.parametrize("field,value", [("NAME", "ciudadania"), ("HOST", "db.production.test")])
def test_guard_rejects_production_database(field, value):
    config = local_settings()
    config.DATABASES["default"][field] = value
    with pytest.raises(DatasetError):
        guard_environment(config, Path("/tmp/ciudadania-s15/guard"))


def test_guard_rejects_repository_or_symlink_output(tmp_path):
    with pytest.raises(DatasetError):
        guard_environment(local_settings(), Path.cwd())
    link = tmp_path / "link"
    link.symlink_to(Path("/tmp/ciudadania-s15"))
    with pytest.raises(DatasetError):
        guard_environment(local_settings(), link)


@pytest.mark.parametrize("anchor", [date(2026, 10, 1), date(2026, 10, 20), date(2027, 1, 1)])
def test_recipe_funds_three_real_beneficiaries_and_four_states(anchor):
    recipe = Recipe(anchor)
    rows = expected_ledger(recipe)
    funding = defaultdict(int)
    for row in rows:
        if (
            row["case"]
            and int(row["case"][1:]) <= 30
            and row["event"]
            in {
                "REPORT_CREATED",
                "VALIDATION_BONUS",
                "RESOLUTION_BONUS",
            }
        ):
            funding[row["user"], row["institution"]] += row["credit"]
    assert funding == {(f"C{n:02}", slug): 1000 for n, slug in enumerate(INSTITUTIONS)}
    assert {row["status"] for row in rows} == {"PENDIENTE", "ACREDITADO", "ANULADO", "REVERTIDO"}
    assert (recipe.end - recipe.start).total_seconds() == 60 * 86400
    assert len(recipe.cases()) == 36
    assert sum(len(c["confirmants"]) for c in recipe.cases()) == 32


def test_synthetic_media_are_distinct_repeatable_and_without_metadata():
    from io import BytesIO

    from PIL import Image

    photos = [synthetic_image(15, f"R{n:03}") for n in range(36)]
    assert len({hashlib.sha256(p).hexdigest() for p in photos}) == 36
    assert photos[0] == synthetic_image(15, "R000")
    assert photos[0] != synthetic_image(16, "R000")
    with Image.open(BytesIO(photos[0])) as image:
        assert image.format == "JPEG"
        assert not image.getexif()


def test_manifest_tampering_is_detected(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, seal({"state": "complete", "plan": {"seed": 15}}))
    assert read_manifest(manifest_path)["plan"]["seed"] == 15
    data = json.loads(manifest_path.read_text())
    data["plan"]["seed"] = 16
    write_json(manifest_path, data)
    with pytest.raises(DatasetError, match="Hash"):
        read_manifest(manifest_path)


def test_dry_run_has_no_database_upload_or_filesystem_effects(monkeypatch):
    root = Path(f"/tmp/ciudadania-s15/dry-run-{uuid.uuid4()}")
    monkeypatch.setattr(
        generator,
        "guard_environment",
        lambda settings, path: guard_environment(local_settings(), path),
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("dry-run intentó un efecto")

    monkeypatch.setattr(generator, "_empty_database", forbidden)
    monkeypatch.setattr(generator, "_catalogs", forbidden)
    monkeypatch.setattr(generator, "LocalObjects", forbidden)
    result = generator.generate(Recipe(date(2026, 10, 20)), root, dry_run=True)
    assert result["effects"] == []
    assert result["state"] == "dry-run"
    assert not root.exists()


@pytest.fixture
def dataset_root(settings, document_hmac_secret, monkeypatch):
    root = Path(f"/tmp/ciudadania-s15/pytest-{uuid.uuid4()}")
    settings.SUPABASE_URL = "http://127.0.0.1:18515"
    settings.SUPABASE_SERVICE_ROLE_KEY = "s15-local-synthetic"
    settings.SUPABASE_STORAGE_BUCKET_PRIVATE = "s15-private"
    settings.SUPABASE_STORAGE_BUCKET_REPORTS = "s15-reports"
    settings.REPORT_AUTO_CLOSE_HOURS = 72
    # La DB es la transaccional de pytest; el guard de configuración se prueba arriba.
    for module in (generator, verification):
        monkeypatch.setattr(
            module,
            "guard_environment",
            lambda settings, path: guard_environment(local_settings(), path),
        )
    yield root
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def migrated_catalogs(transactional_db):
    # TransactionTestCase hace flush entre casos y no vuelve a ejecutar la migración de seed.
    call_command("seed_catalogs", verbosity=0)


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_generate_trace_financing_redemption_four_states_and_read_only_replay(
    dataset_root, migrated_catalogs
):
    recipe = Recipe(date(2026, 10, 20))
    generated = generator.generate(recipe, dataset_root, processes_stopped=True)
    assert generated["verification"]["status"] == "PASS", generated["verification"]["failures"]
    assert get_user_model().objects.count() == 16
    assert RewardRedemption.objects.count() == 3
    assert set(RewardTransaction.objects.values_list("status", flat=True)) == {
        "PENDIENTE",
        "ACREDITADO",
        "ANULADO",
        "REVERTIDO",
    }
    original_files = {
        str(p): (p.stat().st_mtime_ns, p.read_bytes())
        for p in dataset_root.rglob("*")
        if p.is_file()
    }
    before = RewardTransaction.objects.count()
    replay = generator.generate(recipe, dataset_root, processes_stopped=True)
    assert replay["state"] == "verified-existing"
    assert RewardTransaction.objects.count() == before
    assert {
        str(p): (p.stat().st_mtime_ns, p.read_bytes())
        for p in dataset_root.rglob("*")
        if p.is_file()
    } == original_files
    manifest = read_manifest(Path(generated["manifest"]))
    assert manifest["checkpoints"]["before_redemption"]["balances"] == {
        "C00": 1000,
        "C01": 1050,
        "C02": 1000,
    }
    assert manifest["checkpoints"]["after_reversal"]["balances"]["C00"] == -100
    assert Counter(c["code"] for c in manifest["negative_controls"]) == {
        "MONTHLY_LIMIT_REACHED": 3,
        "INSUFFICIENT_BALANCE": 1,
        "SELF_CONFIRMATION_NOT_ALLOWED": 1,
        "ALREADY_CONFIRMED": 1,
    }
    assert all(c["before"] == c["after"] for c in manifest["negative_controls"])
    assert "accessToken" not in Path(generated["manifest"]).read_text()


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_verifier_detects_ledger_divergence_and_replay_does_not_repair(
    dataset_root, migrated_catalogs
):
    recipe = Recipe(date(2026, 10, 20))
    generated = generator.generate(recipe, dataset_root, processes_stopped=True)
    manifest_path = Path(generated["manifest"])
    original_manifest = manifest_path.read_bytes()
    # Corrupción intencional del ensayo: el generador nunca usa esta escritura.
    tx = RewardTransaction.objects.filter(event_type="REPORT_CREATED", status="ACREDITADO").first()
    RewardTransaction.objects.filter(pk=tx.pk).update(credit_delta=21)
    result = verification.verify(manifest_path, artifact_root=dataset_root)
    assert result["status"] == "FAIL"
    assert "ledger.exact-values-timestamps-links" in result["failures"]
    with pytest.raises(DatasetError, match="divergente"):
        generator.generate(recipe, dataset_root, processes_stopped=True)
    tx.refresh_from_db()
    assert tx.credit_delta == 21
    assert manifest_path.read_bytes() == original_manifest


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_verifier_rejects_wrong_validation_bonus_audit_id_without_ledger_changes(
    dataset_root, migrated_catalogs
):
    from apps.admin_app.models import AuditLog

    recipe = Recipe(date(2026, 10, 20))
    generated = generator.generate(recipe, dataset_root, processes_stopped=True)
    manifest_path = Path(generated["manifest"])
    original_manifest = manifest_path.read_bytes()
    report_id = read_manifest(manifest_path)["reports"]["R002"]
    audit_row = AuditLog.objects.get(action="report.validated", payload__report_id=report_id)
    wrong_tx = RewardTransaction.objects.get(report_id=report_id, event_type="REPORT_CREATED")
    ledger_before = list(RewardTransaction.objects.order_by("id").values())
    # Solo cambia el ID auditado: mismo reporte/ciclo, pero apunta a REPORT_CREATED.
    broken_payload = {**audit_row.payload, "validation_bonus_transaction_id": str(wrong_tx.pk)}
    AuditLog.objects.filter(pk=audit_row.pk).update(payload=broken_payload)
    result = verification.verify(manifest_path, artifact_root=dataset_root)
    assert result["status"] == "FAIL"
    assert any(label.startswith("audit.links.bonus.") for label in result["failures"])
    assert next(
        check for check in result["checks"] if check["id"] == "ledger.exact-values-timestamps-links"
    )["pass"]
    with pytest.raises(DatasetError, match="divergente"):
        generator.generate(recipe, dataset_root, processes_stopped=True)
    audit_row.refresh_from_db()
    assert audit_row.payload == broken_payload
    assert list(RewardTransaction.objects.order_by("id").values()) == ledger_before
    assert manifest_path.read_bytes() == original_manifest


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
@pytest.mark.parametrize(
    ("action", "case_label", "field", "check_prefix"),
    [
        (
            "report.resolution_evidence_created",
            "R002",
            "evidence_id",
            "audit.links.evidence-created.",
        ),
        ("report.rejected", "R032", "voided_transaction_ids", "audit.links.rejected-voided."),
    ],
)
def test_verifier_rejects_wrong_evidence_or_voided_audit_ids_without_ledger_changes(
    dataset_root, migrated_catalogs, action, case_label, field, check_prefix
):
    from apps.admin_app.models import AuditLog
    from apps.reports_app.models import ResolutionEvidence

    recipe = Recipe(date(2026, 10, 20))
    generated = generator.generate(recipe, dataset_root, processes_stopped=True)
    manifest_path = Path(generated["manifest"])
    original_manifest = manifest_path.read_bytes()
    report_id = read_manifest(manifest_path)["reports"][case_label]
    audit_row = AuditLog.objects.get(action=action, payload__report_id=report_id)
    ledger_before = list(RewardTransaction.objects.order_by("id").values())
    # Solo cambia el payload: sustituye los IDs originales por filas ajenas al evento.
    wrong_id = (
        str(ResolutionEvidence.objects.exclude(report_id=report_id).first().pk)
        if field == "evidence_id"
        else [str(RewardTransaction.objects.filter(status="ACREDITADO").first().pk)]
    )
    broken_payload = {**audit_row.payload, field: wrong_id}
    AuditLog.objects.filter(pk=audit_row.pk).update(payload=broken_payload)
    result = verification.verify(manifest_path, artifact_root=dataset_root)
    assert result["status"] == "FAIL"
    assert any(label.startswith(check_prefix) for label in result["failures"])
    assert next(
        check for check in result["checks"] if check["id"] == "ledger.exact-values-timestamps-links"
    )["pass"]
    with pytest.raises(DatasetError, match="divergente"):
        generator.generate(recipe, dataset_root, processes_stopped=True)
    audit_row.refresh_from_db()
    assert audit_row.payload == broken_payload
    assert list(RewardTransaction.objects.order_by("id").values()) == ledger_before
    assert manifest_path.read_bytes() == original_manifest


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
@pytest.mark.parametrize("field", ["location", "institution"])
def test_verifier_rejects_report_location_or_institution_corruption(
    dataset_root, migrated_catalogs, field
):
    from django.contrib.gis.geos import Point

    from apps.admin_app.models import Institution
    from apps.reports_app.models import Report

    generated = generator.generate(Recipe(date(2026, 10, 20)), dataset_root, processes_stopped=True)
    manifest_path = Path(generated["manifest"])
    report_id = read_manifest(manifest_path)["reports"]["R002"]
    original_manifest = manifest_path.read_bytes()
    ledger_before = list(RewardTransaction.objects.order_by("id").values())
    report = Report.objects.get(pk=report_id)
    if field == "location":
        Report.objects.filter(pk=report_id).update(location=Point(0, 0, srid=4326))
    else:
        wrong = Institution.objects.exclude(pk=report.institution_id).first()
        Report.objects.filter(pk=report_id).update(institution=wrong)
    result = verification.verify(manifest_path, artifact_root=dataset_root)
    assert result["status"] == "FAIL"
    assert f"reports.R002.{field}-projection" in result["failures"]
    assert list(RewardTransaction.objects.order_by("id").values()) == ledger_before
    assert manifest_path.read_bytes() == original_manifest
    with pytest.raises(DatasetError, match="divergente"):
        generator.generate(Recipe(date(2026, 10, 20)), dataset_root, processes_stopped=True)


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_failure_rolls_back_database_and_journals_objects_without_resuming(
    dataset_root, migrated_catalogs, monkeypatch
):
    recipe = Recipe(date(2026, 10, 20))

    def fail(*args, **kwargs):
        raise OSError("fallo sintético")

    monkeypatch.setattr(generator.SyntheticAdapter, "classify", fail)
    with pytest.raises(DatasetError, match="orquestación"):
        generator.generate(recipe, dataset_root, processes_stopped=True)
    assert not get_user_model().objects.exists()
    assert not RewardTransaction.objects.exists()
    directory = dataset_root / recipe.identifier
    journal = json.loads((directory / "journal.json").read_text())
    assert journal["state"] == "incomplete"
    assert journal["objects"]
    assert not (directory / "manifest.json").exists()
    with pytest.raises(DatasetError, match="parciales"):
        generator.generate(recipe, dataset_root, processes_stopped=True)


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_partial_database_is_never_repaired_or_deleted(dataset_root, user_factory):
    foreign = user_factory(email="ajeno@example.test")
    with pytest.raises(DatasetError, match="parcial o ajena"):
        generator.generate(Recipe(date(2026, 10, 20)), dataset_root, processes_stopped=True)
    assert get_user_model().objects.get(pk=foreign.pk).email == "ajeno@example.test"
    assert not dataset_root.exists()
