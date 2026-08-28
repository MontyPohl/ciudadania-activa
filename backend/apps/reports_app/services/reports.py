"""Única vía de dominio para crear reportes de plataforma."""

from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass
from functools import partial

from django.contrib.gis.geos import Point
from django.db import IntegrityError, transaction

from apps.admin_app.models import Category
from apps.ai_client_app import tasks as ai_tasks
from apps.auth_app.enums import UserRole, VerificationStatus
from apps.core.enums import ActorType, ReportSource, ReportStatus
from apps.core.services import audit, storage
from apps.map_app.cache import invalidate_public_reports_cache
from apps.reports_app.enums import MediaType
from apps.reports_app.models import Report, ReportMedia, ReportStatusHistory
from apps.reports_app.services.photos import PreparedReportPhoto, prepare_report_photo
from apps.rewards_app.models import RewardTransaction
from apps.rewards_app.services import ledger

logger = logging.getLogger(__name__)

PHOTO_HASH_CONSTRAINT = "report_media_foto_hash_unico"


class ReportCreationError(ValueError):
    """Base de errores de dominio esperables durante la creación."""


class InvalidReportDataError(ReportCreationError):
    """Un dato no cumple las reglas del dominio aunque no provenga de HTTP."""

    def __init__(self, field: str, message: str):
        self.field = field
        super().__init__(message)


class DuplicatePhotoError(ReportCreationError):
    """Los bytes originales de la foto ya fueron usados por otro reporte."""


@dataclass(frozen=True)
class ReportCreationResult:
    report: Report
    reward: RewardTransaction


def validate_report_description(description) -> str:
    if not isinstance(description, str):
        raise InvalidReportDataError("description", "La descripción debe ser texto.")
    if not description.strip():
        raise InvalidReportDataError("description", "La descripción no puede estar vacía.")
    if any(ord(character) < 32 and character not in "\t\n\r" for character in description):
        raise InvalidReportDataError(
            "description", "La descripción contiene caracteres de control no permitidos."
        )
    return description


def validate_report_coordinates(latitude, longitude) -> tuple[float, float]:
    return (
        _finite_coordinate("lat", latitude, -90, 90),
        _finite_coordinate("lng", longitude, -180, 180),
    )


def create_report(
    *,
    citizen,
    category: Category,
    description: str,
    photo,
    latitude: float,
    longitude: float,
) -> ReportCreationResult:
    """Crea Report, media, historial, reward y auditoría de manera atómica."""
    _ensure_verified_citizen(citizen)
    category = _active_category(category)
    description = validate_report_description(description)
    latitude, longitude = validate_report_coordinates(latitude, longitude)
    bucket = storage.reports_bucket()

    prepared = prepare_report_photo(photo)
    if _photo_hash_exists(prepared.original_hash):
        raise DuplicatePhotoError("Esta fotografía ya fue utilizada en otro reporte.")

    report_id = uuid.uuid4()
    media_id = uuid.uuid4()
    object_key = build_report_photo_key(report_id, media_id, prepared.extension)
    storage.upload(bucket, object_key, prepared.content, prepared.content_type)

    try:
        return _persist_report(
            report_id=report_id,
            media_id=media_id,
            object_key=object_key,
            prepared=prepared,
            citizen=citizen,
            category=category,
            description=description,
            latitude=latitude,
            longitude=longitude,
        )
    except IntegrityError as exc:
        safe_delete_uploaded_report_photo(bucket, object_key)
        if _constraint_name(exc) == PHOTO_HASH_CONSTRAINT:
            raise DuplicatePhotoError("Esta fotografía ya fue utilizada en otro reporte.") from exc
        raise
    except Exception:
        # El upload ya fue confirmado: cualquier fallo DB exige compensar únicamente
        # este object. El borrado no puede ocultar la causa original.
        safe_delete_uploaded_report_photo(bucket, object_key)
        raise


def build_report_photo_key(report_id, media_id, extension: str) -> str:
    if extension not in {".jpg", ".png"}:
        raise ValueError("Extensión canónica de fotografía inválida.")
    return f"reports/{report_id}/{media_id}{extension}"


def safe_delete_uploaded_report_photo(bucket: str, object_key: str) -> None:
    """Compensación best-effort que nunca sustituye el error de creación."""
    try:
        storage.delete(bucket, object_key)
    except Exception:
        # Es una frontera externa best-effort. Se captura ampliamente para que ni un
        # doble defectuoso ni el proveedor tapen el error transaccional original.
        logger.warning(
            "No se pudo eliminar una fotografía huérfana de reporte en %s.",
            storage.redact_key(object_key),
        )


def _persist_report(
    *,
    report_id,
    media_id,
    object_key: str,
    prepared: PreparedReportPhoto,
    citizen,
    category: Category,
    description: str,
    latitude: float,
    longitude: float,
) -> ReportCreationResult:
    with transaction.atomic():
        report = Report.objects.create(
            id=report_id,
            citizen=citizen,
            category=category,
            institution=None,
            description=description,
            status=ReportStatus.CREADO,
            priority=category.default_priority,
            source=ReportSource.PLATFORM,
            location=Point(longitude, latitude, srid=4326),
        )
        ReportMedia.objects.create(
            id=media_id,
            report=report,
            file_url=object_key,
            file_hash=prepared.original_hash,
            media_type=MediaType.FOTO,
            uploaded_by=citizen,
        )
        ReportStatusHistory.objects.create(
            report=report,
            previous_status="",
            new_status=ReportStatus.CREADO,
            changed_by=citizen,
        )
        reward = ledger.on_report_created(report)
        audit.log(
            citizen,
            "report.created",
            {
                "report_id": str(report.id),
                "category_id": str(category.id),
                "status": ReportStatus.CREADO,
            },
            actor_type=ActorType.HUMAN,
        )
        transaction.on_commit(partial(_invalidate_public_cache, report.id), robust=True)
        transaction.on_commit(partial(_enqueue_ai_classification, report.id), robust=True)

    return ReportCreationResult(report=report, reward=reward)


def _ensure_verified_citizen(citizen) -> None:
    valid = (
        citizen is not None
        and getattr(citizen, "is_authenticated", False)
        and getattr(citizen, "is_active", False)
        and getattr(citizen, "role", None) == UserRole.CITIZEN
        and getattr(citizen, "verification_status", None) == VerificationStatus.APROBADO
    )
    if not valid:
        raise InvalidReportDataError("citizen", "Se requiere una identidad ciudadana verificada.")


def _active_category(category) -> Category:
    category_id = getattr(category, "pk", None)
    active = Category.objects.filter(pk=category_id, is_active=True).first()
    if active is None:
        raise InvalidReportDataError("category", "La categoría no existe o no está activa.")
    return active


def _finite_coordinate(field: str, value, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        raise InvalidReportDataError(field, "La coordenada debe ser un número finito.")
    try:
        coordinate = float(value)
    except (TypeError, ValueError) as exc:
        raise InvalidReportDataError(field, "La coordenada debe ser un número finito.") from exc
    if not math.isfinite(coordinate):
        raise InvalidReportDataError(field, "La coordenada debe ser un número finito.")
    if not minimum <= coordinate <= maximum:
        raise InvalidReportDataError(
            field, f"La coordenada debe estar entre {minimum:g} y {maximum:g}."
        )
    return coordinate


def _constraint_name(exc: IntegrityError) -> str | None:
    cause = exc.__cause__
    diagnostic = getattr(cause, "diag", None)
    return getattr(diagnostic, "constraint_name", None)


def _photo_hash_exists(original_hash: str) -> bool:
    return ReportMedia.objects.filter(media_type=MediaType.FOTO, file_hash=original_hash).exists()


def _invalidate_public_cache(report_id) -> None:
    try:
        invalidate_public_reports_cache()
    except Exception:
        # Callback post-commit y best-effort: el reporte ya existe. No se incluyen
        # ubicación, descripción ni claves en el log.
        logger.warning("No se pudo invalidar el mapa público tras crear %s.", report_id)


def _enqueue_ai_classification(report_id) -> None:
    try:
        ai_tasks.classify_report.delay(str(report_id))
    except Exception:
        # El broker no forma parte de la atomicidad del reporte y S6 clasifica no-op.
        logger.warning("No se pudo encolar la clasificación del reporte %s.", report_id)
