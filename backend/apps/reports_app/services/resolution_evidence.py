"""Evidencia privada e idempotente del ciclo de resolución vigente."""

from __future__ import annotations

import hashlib
import logging
import unicodedata
import uuid
from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.db import transaction

from apps.admin_app.models import Category, Institution, InstitutionOperator
from apps.auth_app.enums import UserRole
from apps.core.enums import ActorType, ReportStatus
from apps.core.services import audit, storage
from apps.reports_app.models import Report, ResolutionEvidence
from apps.reports_app.services.photos import InvalidReportPhotoError, prepare_report_photo

logger = logging.getLogger(__name__)

EVIDENCE_LIMIT = 5


class EvidenceError(Exception):
    code = "INVALID_REQUEST"


class EvidenceNotFound(EvidenceError):
    code = "REPORT_NOT_FOUND"


class EvidenceOperatorRequired(EvidenceError):
    code = "INSTITUTION_OPERATOR_REQUIRED"


class EvidenceStateConflict(EvidenceError):
    code = "REPORT_STATE_NOT_ALLOWED"


class StaleEvidenceCycle(EvidenceError):
    code = "STALE_REPORT_CYCLE"


class EvidenceLimitReached(EvidenceError):
    code = "EVIDENCE_LIMIT_REACHED"


class IdempotencyKeyReused(EvidenceError):
    code = "IDEMPOTENCY_KEY_REUSED"


class EvidenceTooLarge(EvidenceError):
    code = "EVIDENCE_TOO_LARGE"


class UnsupportedEvidenceType(EvidenceError):
    code = "UNSUPPORTED_EVIDENCE_TYPE"


class InvalidEvidenceDescription(EvidenceError):
    code = "INVALID_REQUEST"


@dataclass(frozen=True)
class EvidenceCreationResult:
    evidence: ResolutionEvidence
    created: bool


def normalize_description(value: str) -> str:
    if not isinstance(value, str):
        raise InvalidEvidenceDescription("La descripción debe ser texto plano.")
    normalized = unicodedata.normalize("NFC", value.replace("\r\n", "\n")).strip()
    if any(character == "\x00" or _forbidden_control(character) for character in normalized):
        raise InvalidEvidenceDescription("La descripción contiene caracteres de control.")
    if not 1 <= len(normalized) <= 1000:
        raise InvalidEvidenceDescription("La descripción debe tener entre 1 y 1000 caracteres.")
    return normalized


def create_resolution_evidence(
    *,
    report_id,
    operator_id,
    institution_id,
    expected_cycle: int,
    idempotency_key: uuid.UUID,
    description: str,
    uploaded_file,
) -> EvidenceCreationResult:
    normalized_description = normalize_description(description)
    try:
        prepared = prepare_report_photo(uploaded_file)
    except InvalidReportPhotoError as exc:
        if "5 MiB" in str(exc):
            raise EvidenceTooLarge(str(exc)) from exc
        raise UnsupportedEvidenceType(str(exc)) from exc

    content_hash = hashlib.sha256(
        prepared.content
        + b"\x00"
        + normalized_description.encode("utf-8")
        + b"\x00"
        + str(expected_cycle).encode("ascii")
    ).hexdigest()

    with transaction.atomic():
        report, operator = _locked_context(
            report_id=report_id,
            operator_id=operator_id,
            institution_id=institution_id,
        )
        existing = _idempotent_existing(
            report=report,
            cycle=expected_cycle,
            key=idempotency_key,
            content_hash=content_hash,
        )
        if existing is not None:
            return EvidenceCreationResult(evidence=existing, created=False)
        _ensure_current_cycle(report, expected_cycle)
        _ensure_evidence_allowed(report)
        _ensure_limit(report, expected_cycle)

    evidence_id = uuid.uuid4()
    object_key = (
        f"resolution-evidence/{report_id}/{expected_cycle}/"
        f"{evidence_id}{prepared.extension}"
    )
    bucket = storage.reports_bucket()
    storage.upload(bucket, object_key, prepared.content, prepared.content_type)

    cleanup_required = True
    try:
        with transaction.atomic():
            report, operator = _locked_context(
                report_id=report_id,
                operator_id=operator_id,
                institution_id=institution_id,
            )
            existing = _idempotent_existing(
                report=report,
                cycle=expected_cycle,
                key=idempotency_key,
                content_hash=content_hash,
            )
            if existing is not None:
                result = EvidenceCreationResult(evidence=existing, created=False)
            else:
                _ensure_current_cycle(report, expected_cycle)
                _ensure_evidence_allowed(report)
                _ensure_limit(report, expected_cycle)
                evidence = ResolutionEvidence.objects.create(
                    id=evidence_id,
                    report=report,
                    description=normalized_description,
                    evidence_url=object_key,
                    uploaded_by=operator,
                    resolution_cycle=expected_cycle,
                    idempotency_key=idempotency_key,
                    content_hash=content_hash,
                )
                audit.log(
                    operator,
                    "report.resolution_evidence_created",
                    {
                        "report_id": str(report.pk),
                        "institution_id": str(report.institution_id),
                        "cycle": expected_cycle,
                        "evidence_id": str(evidence.pk),
                    },
                    actor_type=ActorType.HUMAN,
                )
                cleanup_required = False
                result = EvidenceCreationResult(evidence=evidence, created=True)
    except Exception:
        _cleanup_uploaded_object(bucket, object_key)
        raise

    if cleanup_required:
        _cleanup_uploaded_object(bucket, object_key)
    return result


def get_operator_evidence_report(*, report_id, operator_id, institution_id):
    with transaction.atomic():
        report, _operator = _locked_context(
            report_id=report_id,
            operator_id=operator_id,
            institution_id=institution_id,
        )
        return report


def _locked_context(*, report_id, operator_id, institution_id):
    user_model = get_user_model()
    try:
        operator = user_model.objects.select_for_update().get(pk=operator_id)
    except user_model.DoesNotExist as exc:
        raise EvidenceOperatorRequired from exc
    if not operator.is_active or operator.role != UserRole.OPERATOR:
        raise EvidenceOperatorRequired

    membership = (
        InstitutionOperator.objects.select_for_update()
        .filter(user=operator, institution_id=institution_id, active=True)
        .first()
    )
    if membership is None:
        raise EvidenceOperatorRequired
    try:
        report = Report.objects.select_for_update().get(
            pk=report_id,
            institution_id=institution_id,
        )
    except Report.DoesNotExist as exc:
        raise EvidenceNotFound from exc

    Category.objects.select_for_update().only("id").get(pk=report.category_id)
    institution = Institution.objects.select_for_update().only("is_active").get(
        pk=institution_id
    )
    if not institution.is_active:
        raise EvidenceOperatorRequired
    return report, operator


def _ensure_current_cycle(report, expected_cycle):
    if expected_cycle != report.resolution_cycle:
        raise StaleEvidenceCycle


def _ensure_evidence_allowed(report):
    if report.status != ReportStatus.EN_PROCESO or report.validated_at is None:
        raise EvidenceStateConflict


def _ensure_limit(report, cycle):
    if (
        ResolutionEvidence.objects.filter(report=report, resolution_cycle=cycle).count()
        >= EVIDENCE_LIMIT
    ):
        raise EvidenceLimitReached


def _idempotent_existing(*, report, cycle, key, content_hash):
    existing = ResolutionEvidence.objects.filter(
        report=report,
        resolution_cycle=cycle,
        idempotency_key=key,
    ).first()
    if existing is None:
        return None
    if existing.content_hash != content_hash:
        raise IdempotencyKeyReused
    return existing


def _cleanup_uploaded_object(bucket, object_key):
    try:
        storage.delete(bucket, object_key)
    except storage.StorageError:
        logger.error(
            "No se pudo limpiar evidencia S10 recién subida en %s.",
            storage.redact_key(object_key),
        )


def _forbidden_control(character):
    return character != "\n" and unicodedata.category(character) in {"Cc", "Cf"}
