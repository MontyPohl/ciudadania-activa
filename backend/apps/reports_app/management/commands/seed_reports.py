"""Carga determinista de snapshots sintéticos para demostrar el mapa público."""

import uuid
from dataclasses import dataclass

from django.contrib.gis.geos import Point
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.admin_app.models import Category
from apps.core.enums import ReportSource, ReportStatus
from apps.map_app.cache import invalidate_public_reports_cache
from apps.reports_app.models import Report

SEED_NAMESPACE = uuid.UUID("b63c21b4-6ac8-4ed3-b1ca-c6ca6862ab1d")


@dataclass(frozen=True, slots=True)
class SeedReport:
    code: str
    status: str
    category_slug: str
    longitude: float
    latitude: float
    description: str

    @property
    def report_id(self) -> uuid.UUID:
        return uuid.uuid5(SEED_NAMESPACE, self.code)


SEED_REPORTS = (
    SeedReport(
        "s5-map-01",
        ReportStatus.CREADO,
        "baches",
        -57.6359,
        -25.2637,
        "Dato sintético S5-A 01: bache de demostración en la calzada.",
    ),
    SeedReport(
        "s5-map-02",
        ReportStatus.CREADO,
        "alumbrado-publico",
        -57.489,
        -25.267,
        "Dato sintético S5-A 02: luminaria de demostración sin servicio.",
    ),
    SeedReport(
        "s5-map-03",
        ReportStatus.CLASIFICADO,
        "basura-acumulada",
        -57.665,
        -25.290,
        "Dato sintético S5-A 03: acumulación de residuos de demostración.",
    ),
    SeedReport(
        "s5-map-04",
        ReportStatus.CLASIFICADO,
        "agua-potable-perdidas",
        -57.520,
        -25.342,
        "Dato sintético S5-A 04: pérdida de agua de demostración.",
    ),
    SeedReport(
        "s5-map-05",
        ReportStatus.ASIGNADO,
        "semaforos",
        -57.577,
        -25.300,
        "Dato sintético S5-A 05: semáforo de demostración fuera de servicio.",
    ),
    SeedReport(
        "s5-map-06",
        ReportStatus.ASIGNADO,
        "cables-expuestos-riesgo-electrico",
        -57.533,
        -25.208,
        "Dato sintético S5-A 06: cableado de demostración expuesto.",
    ),
    SeedReport(
        "s5-map-07",
        ReportStatus.EN_PROCESO,
        "drenaje-desague-cloaca",
        -57.558,
        -25.321,
        "Dato sintético S5-A 07: drenaje de demostración obstruido.",
    ),
    SeedReport(
        "s5-map-08",
        ReportStatus.EN_PROCESO,
        "escombros",
        -57.574,
        -25.253,
        "Dato sintético S5-A 08: escombros de demostración en la vía.",
    ),
    SeedReport(
        "s5-map-09",
        ReportStatus.RESUELTO,
        "arbol-caido",
        -57.608,
        -25.284,
        "Dato sintético S5-A 09: árbol de demostración ya retirado.",
    ),
    SeedReport(
        "s5-map-10",
        ReportStatus.RESUELTO,
        "alumbrado-publico",
        -57.445,
        -25.355,
        "Dato sintético S5-A 10: luminaria de demostración reparada.",
    ),
    SeedReport(
        "s5-map-11",
        ReportStatus.CERRADO,
        "otros",
        -57.652,
        -25.312,
        "Dato sintético S5-A 11: incidencia de demostración cerrada.",
    ),
    SeedReport(
        "s5-map-12",
        ReportStatus.CERRADO,
        "agua-potable-perdidas",
        -57.607,
        -25.347,
        "Dato sintético S5-A 12: pérdida de demostración cerrada.",
    ),
    SeedReport(
        "s5-map-13",
        ReportStatus.RECHAZADO,
        "baches",
        -57.596,
        -25.326,
        "Dato sintético S5-A 13: reporte rechazado de demostración.",
    ),
    SeedReport(
        "s5-map-14",
        ReportStatus.RECHAZADO,
        "basura-acumulada",
        -57.547,
        -25.235,
        "Dato sintético S5-A 14: segundo rechazo de demostración.",
    ),
)


class Command(BaseCommand):
    help = "Carga 14 reportes sintéticos del mapa público. Idempotente."

    @transaction.atomic
    def handle(self, *args, **options):
        required_slugs = {item.category_slug for item in SEED_REPORTS}
        categories = {
            category.slug: category
            for category in Category.objects.select_related("default_institution").filter(
                slug__in=required_slugs
            )
        }
        missing = sorted(required_slugs - categories.keys())
        if missing:
            raise CommandError(
                "Faltan categorías requeridas; ejecutá seed_catalogs primero: " + ", ".join(missing)
            )

        created_count = 0
        for item in SEED_REPORTS:
            category = categories[item.category_slug]
            _, created = Report.objects.update_or_create(
                pk=item.report_id,
                defaults={
                    "citizen": None,
                    "category": category,
                    "institution": category.default_institution,
                    "description": item.description,
                    "status": item.status,
                    "priority": category.default_priority,
                    "source": ReportSource.PLATFORM,
                    "location": Point(item.longitude, item.latitude, srid=4326),
                    "validated_at": None,
                    "validated_by": None,
                    "resolved_at": None,
                    "rejection_reason": "",
                },
            )
            created_count += int(created)

        transaction.on_commit(invalidate_public_reports_cache)
        self.stdout.write(
            self.style.SUCCESS(
                f"Reportes sintéticos: {len(SEED_REPORTS)} ({created_count} nuevos)."
            )
        )
