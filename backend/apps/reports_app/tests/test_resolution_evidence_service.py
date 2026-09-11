import uuid

import pytest
from django.db import DatabaseError
from django.utils import timezone

from apps.admin_app.models import AuditLog
from apps.core.enums import ReportStatus
from apps.core.services import storage
from apps.reports_app.models import ResolutionEvidence
from apps.reports_app.services import resolution_evidence

pytestmark = pytest.mark.django_db


def _in_process(report, operator_user, institution):
    report.status = ReportStatus.ASIGNADO
    report.institution = institution
    report.validated_at = timezone.now()
    report.validated_by = operator_user
    report.resolution_cycle = 1
    report.save(
        update_fields=[
            "status",
            "institution",
            "validated_at",
            "validated_by",
            "resolution_cycle",
            "updated_at",
        ]
    )
    report.status = ReportStatus.EN_PROCESO
    report.save(update_fields=["status", "updated_at"])
    return report


def _create(
    *,
    report,
    operator_user,
    institution,
    uploaded_file,
    key=None,
    description="Trabajo concluido sin datos personales.",
    cycle=1,
):
    return resolution_evidence.create_resolution_evidence(
        report_id=report.pk,
        operator_id=operator_user.pk,
        institution_id=institution.pk,
        expected_cycle=cycle,
        idempotency_key=key or uuid.uuid4(),
        description=description,
        uploaded_file=uploaded_file,
    )


def test_create_evidence_normaliza_sube_persiste_y_audita_sin_filtraciones(
    report,
    operator_user,
    institution,
    storage_config,
    fake_storage,
    selfie_factory,
    jpeg_bytes,
):
    _in_process(report, operator_user, institution)

    result = _create(
        report=report,
        operator_user=operator_user,
        institution=institution,
        uploaded_file=selfie_factory(jpeg_bytes, name="cliente.jpg"),
        description="  Reparacio\u0301n terminada.\r\nSin novedad.  ",
    )

    evidence = result.evidence
    entry = AuditLog.objects.get(action="report.resolution_evidence_created")
    assert result.created is True
    assert evidence.description == "Reparación terminada.\nSin novedad."
    assert evidence.resolution_cycle == 1
    assert evidence.evidence_url == (
        f"resolution-evidence/{report.pk}/1/{evidence.pk}.jpg"
    )
    assert len(evidence.content_hash) == 64
    assert len(fake_storage.uploads) == 1
    assert fake_storage.uploads[0]["key"] == evidence.evidence_url
    assert fake_storage.uploads[0]["content_type"] == "image/jpeg"
    assert entry.payload == {
        "report_id": str(report.pk),
        "institution_id": str(institution.pk),
        "cycle": 1,
        "evidence_id": str(evidence.pk),
    }
    assert evidence.evidence_url not in str(entry.payload)
    assert evidence.description not in str(entry.payload)


def test_same_idempotency_key_and_content_returns_original_without_upload(
    report,
    operator_user,
    institution,
    storage_config,
    fake_storage,
    selfie_factory,
    jpeg_bytes,
):
    _in_process(report, operator_user, institution)
    key = uuid.uuid4()
    first = _create(
        report=report,
        operator_user=operator_user,
        institution=institution,
        uploaded_file=selfie_factory(jpeg_bytes),
        key=key,
    )
    second = _create(
        report=report,
        operator_user=operator_user,
        institution=institution,
        uploaded_file=selfie_factory(jpeg_bytes),
        key=key,
    )

    assert second.created is False
    assert second.evidence.pk == first.evidence.pk
    assert len(fake_storage.uploads) == 1
    assert ResolutionEvidence.objects.count() == 1
    assert AuditLog.objects.count() == 1


def test_idempotent_replay_returns_historical_result_after_new_cycle(
    report,
    operator_user,
    institution,
    storage_config,
    fake_storage,
    selfie_factory,
    jpeg_bytes,
):
    _in_process(report, operator_user, institution)
    key = uuid.uuid4()
    first = _create(
        report=report,
        operator_user=operator_user,
        institution=institution,
        uploaded_file=selfie_factory(jpeg_bytes),
        key=key,
    )
    report.status = ReportStatus.CLASIFICADO
    report.validated_at = None
    report.validated_by = None
    report.save(update_fields=["status", "validated_at", "validated_by", "updated_at"])
    report.status = ReportStatus.ASIGNADO
    report.validated_at = timezone.now()
    report.validated_by = operator_user
    report.resolution_cycle = 2
    report.save(
        update_fields=[
            "status",
            "validated_at",
            "validated_by",
            "resolution_cycle",
            "updated_at",
        ]
    )

    replay = _create(
        report=report,
        operator_user=operator_user,
        institution=institution,
        uploaded_file=selfie_factory(jpeg_bytes),
        key=key,
        cycle=1,
    )

    assert replay.created is False
    assert replay.evidence.pk == first.evidence.pk
    assert len(fake_storage.uploads) == 1


def test_reused_key_with_different_content_conflicts(
    report,
    operator_user,
    institution,
    storage_config,
    fake_storage,
    selfie_factory,
    jpeg_bytes,
):
    _in_process(report, operator_user, institution)
    key = uuid.uuid4()
    _create(
        report=report,
        operator_user=operator_user,
        institution=institution,
        uploaded_file=selfie_factory(jpeg_bytes),
        key=key,
    )

    with pytest.raises(resolution_evidence.IdempotencyKeyReused):
        _create(
            report=report,
            operator_user=operator_user,
            institution=institution,
            uploaded_file=selfie_factory(jpeg_bytes),
            key=key,
            description="Contenido distinto para la misma clave.",
        )

    assert len(fake_storage.uploads) == 1
    assert ResolutionEvidence.objects.count() == 1


def test_limit_is_five_per_report_cycle(
    report,
    operator_user,
    institution,
    storage_config,
    fake_storage,
    selfie_factory,
    jpeg_bytes,
):
    _in_process(report, operator_user, institution)
    for index in range(5):
        _create(
            report=report,
            operator_user=operator_user,
            institution=institution,
            uploaded_file=selfie_factory(jpeg_bytes),
            description=f"Evidencia número {index}",
        )

    with pytest.raises(resolution_evidence.EvidenceLimitReached):
        _create(
            report=report,
            operator_user=operator_user,
            institution=institution,
            uploaded_file=selfie_factory(jpeg_bytes),
            description="Sexta evidencia no permitida",
        )
    assert len(fake_storage.uploads) == 5
    assert ResolutionEvidence.objects.count() == 5


def test_db_failure_after_upload_triggers_best_effort_cleanup(
    report,
    operator_user,
    institution,
    storage_config,
    fake_storage,
    selfie_factory,
    jpeg_bytes,
    monkeypatch,
):
    _in_process(report, operator_user, institution)

    def fail_create(**kwargs):
        raise DatabaseError("fallo inyectado")

    monkeypatch.setattr(ResolutionEvidence.objects, "create", fail_create)
    with pytest.raises(DatabaseError, match="fallo inyectado"):
        _create(
            report=report,
            operator_user=operator_user,
            institution=institution,
            uploaded_file=selfie_factory(jpeg_bytes),
        )

    assert len(fake_storage.uploads) == 1
    assert fake_storage.deletes == [
        {"bucket": fake_storage.uploads[0]["bucket"], "key": fake_storage.uploads[0]["key"]}
    ]
    assert ResolutionEvidence.objects.count() == 0
    assert AuditLog.objects.count() == 0


def test_revalidates_state_after_upload_and_cleans_race_loser(
    report,
    operator_user,
    institution,
    storage_config,
    fake_storage,
    selfie_factory,
    jpeg_bytes,
    monkeypatch,
):
    _in_process(report, operator_user, institution)
    recorded_upload = storage.upload

    def upload_then_resolve(*args, **kwargs):
        recorded_upload(*args, **kwargs)
        type(report).objects.filter(pk=report.pk).update(status=ReportStatus.RESUELTO)

    monkeypatch.setattr(storage, "upload", upload_then_resolve)
    with pytest.raises(resolution_evidence.EvidenceStateConflict):
        _create(
            report=report,
            operator_user=operator_user,
            institution=institution,
            uploaded_file=selfie_factory(jpeg_bytes),
        )

    assert len(fake_storage.uploads) == 1
    assert len(fake_storage.deletes) == 1
    assert ResolutionEvidence.objects.count() == 0
