import json

import pytest
from django.contrib.gis.geos import Point
from django.db import connection

from apps.admin_app.models import Category, Institution
from apps.core.enums import ReportPriority, ReportStatus
from apps.map_app.bbox import parse_and_canonicalize_bbox
from apps.map_app.selectors import public_reports
from apps.reports_app.models import Report

pytestmark = pytest.mark.django_db


def _params(raw_bbox="-57.7,-25.35,-57.5,-25.2"):
    return {
        "bbox": parse_and_canonicalize_bbox(raw_bbox),
        "state_filter": "active",
        "page": 1,
    }


def _nodes(plan):
    yield plan
    for child in plan.get("Plans", []):
        yield from _nodes(child)


def _gist_index_name():
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT indexname
            FROM pg_indexes
            WHERE tablename = 'reports'
              AND indexdef ILIKE '%%USING gist%%'
              AND indexdef ILIKE '%%location%%'
            """
        )
        return cursor.fetchone()[0]


def test_lookup_respeta_empates_negativos_en_bordes(public_report_factory):
    bbox = parse_and_canonicalize_bbox("-57.7,-25.35,-57.5,-25.2")
    excluded_southwest = public_report_factory(
        location=Point(float(bbox.west), float(bbox.south), srid=4326)
    )
    included_northeast = public_report_factory(
        location=Point(float(bbox.east), float(bbox.north), srid=4326)
    )

    ids = set(public_reports(_params()).values_list("id", flat=True))

    assert excluded_southwest.pk not in ids
    assert included_northeast.pk in ids


def test_lookup_respeta_empates_positivos_en_bordes(public_report_factory):
    raw = "25.2,25.2,25.4,25.4"
    bbox = parse_and_canonicalize_bbox(raw)
    included_southwest = public_report_factory(
        location=Point(float(bbox.west), float(bbox.south), srid=4326)
    )
    excluded_northeast = public_report_factory(
        location=Point(float(bbox.east), float(bbox.north), srid=4326)
    )

    ids = set(public_reports(_params(raw)).values_list("id", flat=True))

    assert included_southwest.pk in ids
    assert excluded_northeast.pk not in ids


@pytest.mark.parametrize(
    ("raw", "point"),
    [
        ("-180,-90,-179.9,-89.9", Point(-180, -90, srid=4326)),
        ("179.9,89.9,180,90", Point(180, 90, srid=4326)),
    ],
)
def test_limites_mundiales_son_inclusivos(raw, point, public_report_factory):
    report = public_report_factory(location=point)

    ids = set(public_reports(_params(raw)).values_list("id", flat=True))

    assert report.pk in ids


def test_sql_usa_prefiltro_gist_y_recheck_sin_transformar_location():
    sql = str(public_reports(_params()).query)
    where = sql.split("WHERE", maxsplit=1)[1]

    assert "&&" in where
    assert "ST_Intersects" in where
    assert '"reports"."location"' in where
    for forbidden in ("ST_X", "ST_Y", "ST_Transform", "geography", "ROUND("):
        assert forbidden not in where


def test_gist_es_elegible_en_plan_controlado(category):
    bbox = parse_and_canonicalize_bbox("-57.7,-25.35,-57.5,-25.2")
    queryset = Report.objects.filter(location__bboverlaps=bbox.envelope())

    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL enable_seqscan = off")
        plan = json.loads(queryset.explain(format="json"))[0]["Plan"]

    gist_name = _gist_index_name()
    index_nodes = [
        node
        for node in _nodes(plan)
        if node.get("Node Type") in {"Index Scan", "Bitmap Index Scan"}
    ]

    assert any(node.get("Index Name") == gist_name for node in index_nodes)
    assert any("&&" in node.get("Index Cond", "") for node in index_nodes)


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_planner_elige_gist_con_cardinalidad_representativa():
    institution, _ = Institution.objects.get_or_create(
        slug="institucion-plan-gist",
        defaults={"name": "Institución sintética para plan GIST"},
    )
    category, _ = Category.objects.get_or_create(
        slug="categoria-plan-gist",
        defaults={
            "name": "Categoría sintética para plan GIST",
            "default_institution": institution,
            "default_priority": ReportPriority.MEDIA,
        },
    )
    outside = [
        Report(
            citizen=None,
            category=category,
            description=f"Dato sintético de plan {number}",
            status=ReportStatus.CREADO,
            location=Point(-60 + (number % 100) / 1000, -27 + (number // 100) / 1000, srid=4326),
        )
        for number in range(3000)
    ]
    Report.objects.bulk_create(outside, batch_size=500)
    Report.objects.create(
        citizen=None,
        category=category,
        description="Dato sintético dentro del bbox",
        status=ReportStatus.CREADO,
        location=Point(-57.6359, -25.2637, srid=4326),
    )
    with connection.cursor() as cursor:
        cursor.execute("ANALYZE reports")

    plan = json.loads(public_reports(_params())[:20].explain(format="json"))[0]["Plan"]
    gist_name = _gist_index_name()

    assert any(
        node.get("Index Name") == gist_name
        for node in _nodes(plan)
        if node.get("Node Type") in {"Index Scan", "Bitmap Index Scan"}
    )
