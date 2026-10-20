"""Validación e ingesta local de publicaciones ficticias de X, sin efectos externos."""

import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime

from django.contrib.gis.geos import Point
from django.db import connection, transaction
from django.utils import timezone

from apps.admin_app.models import Category
from apps.core.enums import ActorType, ReportSource, ReportStatus
from apps.core.services import audit
from apps.map_app.cache import invalidate_public_reports_cache
from apps.reports_app.models import Report, ReportStatusHistory
from apps.reports_app.services.reports import validate_report_description
from apps.social_ingestion_app.models import XMockMessage

DATASET_NAME = "x_mock_v1"
DATASET_NOTICE = (
    "Publicaciones ficticias creadas por el proyecto. No corresponden a cuentas reales."
)
ITEM_FIELDS = {
    "source_handle",
    "simulated_at",
    "raw_text",
    "category",
    "latitude",
    "longitude",
}


@dataclass(frozen=True)
class XMockItem:
    source_handle: str
    simulated_at: datetime
    raw_text: str
    category: Category
    latitude: float
    longitude: float


@dataclass(frozen=True)
class IngestResult:
    created: int
    skipped: int


class DatasetError(Exception):
    """Error seguro: índice de base cero (-1 para la raíz), campo y motivo fijo."""

    def __init__(self, index: int, field: str, reason: str):
        self.index = index
        self.field = field
        super().__init__(f"índice={index} campo={field}: {reason}")


def parse_dataset(data: dict) -> list[XMockItem]:
    """Valida el documento completo antes de permitir cualquier escritura."""
    if not isinstance(data, dict):
        raise DatasetError(-1, "dataset", "Se esperaba un objeto.")
    if data.get("dataset") != DATASET_NAME:
        raise DatasetError(-1, "dataset", "Versión inválida.")
    if data.get("notice") != DATASET_NOTICE:
        raise DatasetError(-1, "notice", "Aviso de ficción inválido.")
    items = data.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= 200:
        raise DatasetError(-1, "items", "Se requieren entre 1 y 200 ítems.")

    categories = {category.slug: category for category in Category.objects.filter(is_active=True)}
    now = timezone.now()
    seen = set()
    parsed = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise DatasetError(index, "items", "Se esperaba un objeto.")
        for field in sorted(ITEM_FIELDS):
            if field not in item:
                raise DatasetError(index, field, "Falta el campo obligatorio.")
        handle = item["source_handle"]
        if not isinstance(handle, str) or not re.fullmatch(r"@ficticio_[a-z0-9_]{2,24}", handle):
            raise DatasetError(index, "source_handle", "Handle ficticio inválido.")
        timestamp = item["simulated_at"]
        if not isinstance(timestamp, str) or "T" not in timestamp:
            raise DatasetError(index, "simulated_at", "Se requiere una fecha ISO con zona.")
        try:
            simulated_at = datetime.fromisoformat(timestamp)
        except ValueError:
            raise DatasetError(index, "simulated_at", "Fecha ISO inválida.") from None
        if timezone.is_naive(simulated_at) or simulated_at > now:
            raise DatasetError(index, "simulated_at", "Fecha sin zona o futura.")
        key = (handle, simulated_at)
        if key in seen:
            raise DatasetError(index, "simulated_at", "Par de handle y fecha repetido.")
        seen.add(key)

        raw_text = item["raw_text"]
        if not isinstance(raw_text, str) or not 1 <= len(raw_text) <= 280:
            raise DatasetError(index, "raw_text", "Se requieren entre 1 y 280 caracteres.")
        try:
            validate_report_description(raw_text)
        except ValueError:
            raise DatasetError(index, "raw_text", "Texto inválido.") from None
        if any(unicodedata.category(character) == "Cc" for character in raw_text):
            raise DatasetError(index, "raw_text", "No se permiten caracteres de control.")
        slug = item["category"]
        if not isinstance(slug, str) or slug not in categories:
            raise DatasetError(index, "category", "La categoría no existe o está inactiva.")
        latitude = _coordinate(item["latitude"], index, "latitude", -25.45, -25.15)
        longitude = _coordinate(item["longitude"], index, "longitude", -57.75, -57.40)
        parsed.append(
            XMockItem(handle, simulated_at, raw_text, categories[slug], latitude, longitude)
        )
    return parsed


def _coordinate(value, index: int, field: str, minimum: float, maximum: float) -> float:
    if (
        type(value) not in (int, float)
        or not minimum <= value <= maximum
        or not math.isfinite(value)
    ):
        raise DatasetError(index, field, "Coordenada fuera del Gran Asunción o no finita.")
    return float(value)


@transaction.atomic
def ingest(items: list[XMockItem], *, dry_run=False) -> IngestResult:
    """Persiste el lote validado y omite claves existentes, en una sola transacción."""
    # El esquema no tiene UNIQUE para la clave natural. Serializamos los cargadores
    # incluso cuando la tabla está vacía, sin añadir modelos ni migraciones.
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(7313, 4)")
    created = skipped = 0
    seen = set()
    for item in items:
        key = (item.source_handle, item.simulated_at)
        if (
            key in seen
            or XMockMessage.objects.filter(
                source_handle=item.source_handle, simulated_at=item.simulated_at
            ).exists()
        ):
            skipped += 1
            continue
        seen.add(key)
        created += 1
        if dry_run:
            continue
        report = Report.objects.create(
            source=ReportSource.X_MOCK,
            citizen=None,
            institution=None,
            category=item.category,
            status=ReportStatus.CREADO,
            priority=item.category.default_priority,
            location=Point(item.longitude, item.latitude, srid=4326),
            description=f"[Publicación simulada de X · {item.source_handle}] {item.raw_text}",
        )
        ReportStatusHistory.objects.create(
            report=report, previous_status="", new_status=ReportStatus.CREADO, changed_by=None
        )
        message = XMockMessage.objects.create(
            source_handle=item.source_handle,
            raw_text=item.raw_text,
            simulated_at=item.simulated_at,
            report=report,
        )
        audit.log(
            None,
            "report.ingested",
            {
                "report_id": str(report.pk),
                "x_mock_message_id": str(message.pk),
                "category_id": str(item.category.pk),
                "source": ReportSource.X_MOCK,
            },
            actor_type=ActorType.SYSTEM,
        )
    if created and not dry_run:
        transaction.on_commit(invalidate_public_reports_cache, robust=True)
    return IngestResult(created=created, skipped=skipped)
