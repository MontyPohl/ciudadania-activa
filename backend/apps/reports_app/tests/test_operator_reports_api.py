"""Consulta institucional de reportes (Sprint 9-A).

La sugerencia IA es solamente informativa. El scope de ambos GET se decide con la
institución ya asignada al reporte o, si todavía no existe, con la institución por
defecto de su categoría. Ninguna prueba invoca Celery, Gemini ni otro proveedor.
"""

import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest
from django.contrib.gis.geos import Point
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.admin_app.models import AuditLog, Category, Institution, InstitutionOperator
from apps.ai_client_app.models import AiClassification
from apps.auth_app.enums import UserRole
from apps.core.enums import ActorType, ReportPriority, ReportStatus
from apps.core.services import storage
from apps.reports_app.enums import MediaType
from apps.reports_app.models import Report, ReportConfirmation, ReportMedia

pytestmark = pytest.mark.django_db

LIST_URL = "/api/v1/operator/reports/"
SIGNED_URL = "https://storage.example.test/object/sign/report.jpg?token=firma-efimera"

SUMMARY_FIELDS = {"id", "name", "slug"}
LIST_FIELDS = {
    "id",
    "status",
    "priority",
    "category",
    "institution",
    "effective_read_institution",
    "created_at",
}
DETAIL_FIELDS = LIST_FIELDS | {
    "description",
    "latitude",
    "longitude",
    "photo_signed_url",
    "confirmation_count",
    "ai_classification",
}
AI_FIELDS = {
    "suggested_category",
    "suggested_institution",
    "suggested_priority",
    "confidence",
    "inconsistency_flag",
    "justification",
    "model_name",
    "created_at",
}


def _client(user=None):
    client = APIClient()
    if user is not None:
        client.force_authenticate(user)
    return client


def _detail_url(report, institution_id=None):
    report_id = getattr(report, "pk", report)
    url = f"{LIST_URL}{report_id}/"
    if institution_id is not None:
        return f"{url}?institution_id={institution_id}"
    return url


def _report(category, *, index=0, **overrides):
    values = {
        "category": category,
        "description": f"Incidencia institucional {index}.",
        "location": Point(-57.6359 + index / 10000, -25.2637, srid=4326),
    }
    values.update(overrides)
    return Report.objects.create(**values)


def _set_created_at(instance, value):
    type(instance).objects.filter(pk=instance.pk).update(created_at=value)
    instance.created_at = value
    return instance


def _summary(instance):
    return {
        "id": str(instance.pk),
        "name": instance.name,
        "slug": instance.slug,
    }


@pytest.fixture
def ande(db):
    return Institution.objects.get(slug="ande")


@pytest.fixture
def essap(db):
    return Institution.objects.get(slug="essap")


@pytest.fixture
def ande_category(db):
    return Category.objects.get(slug="alumbrado-publico")


@pytest.fixture
def municipal_category(db):
    return Category.objects.get(slug="basura-acumulada")


@pytest.fixture
def signed_urls(monkeypatch):
    class Signer:
        calls = []
        error = None

        def __call__(self, bucket, key, expires_in):
            self.calls.append({"bucket": bucket, "key": key, "expires_in": expires_in})
            if self.error is not None:
                raise self.error
            return SIGNED_URL

    signer = Signer()
    monkeypatch.setattr(storage, "create_signed_url", signer)
    return signer


# --------------------------------------------------------------------------- #
# Contexto institucional y tenancy compartidos por lista y detalle
# --------------------------------------------------------------------------- #


def test_una_membresia_activa_resuelve_contexto_sin_parametro(
    operator_user,
    institution,
    category,
):
    report = _report(category, institution=None)
    client = _client(operator_user)

    listed = client.get(LIST_URL)
    detailed = client.get(_detail_url(report))

    assert listed.status_code == detailed.status_code == 200
    assert [row["id"] for row in listed.json()["results"]] == [str(report.pk)]
    assert detailed.json()["id"] == str(report.pk)
    assert detailed.json()["institution"] is None
    assert detailed.json()["effective_read_institution"] == _summary(institution)


def test_scope_comun_da_precedencia_a_asignada_y_fallback_solo_si_es_null(
    operator_user,
    institution,
    category,
    ande,
    ande_category,
    user_factory,
):
    fallback_municipal = _report(category, index=1, institution=None)
    assigned_municipal = _report(ande_category, index=2, institution=institution)
    fallback_ande = _report(ande_category, index=3, institution=None)
    assigned_ande = _report(category, index=4, institution=ande)

    ande_operator = user_factory(email="operador-ande@example.com", role=UserRole.OPERATOR)
    InstitutionOperator.objects.create(user=ande_operator, institution=ande, active=True)

    cases = [
        (
            operator_user,
            {fallback_municipal.pk, assigned_municipal.pk},
            {fallback_ande.pk, assigned_ande.pk},
        ),
        (
            ande_operator,
            {fallback_ande.pk, assigned_ande.pk},
            {fallback_municipal.pk, assigned_municipal.pk},
        ),
    ]
    for user, visible, hidden in cases:
        client = _client(user)
        response = client.get(LIST_URL)

        assert response.status_code == 200
        assert {uuid.UUID(row["id"]) for row in response.json()["results"]} == visible
        assert all(client.get(_detail_url(report_id)).status_code == 200 for report_id in visible)
        assert all(client.get(_detail_url(report_id)).status_code == 404 for report_id in hidden)

    assigned_payload = _client(operator_user).get(_detail_url(assigned_municipal)).json()
    assert assigned_payload["institution"] == _summary(institution)
    assert assigned_payload["effective_read_institution"] == _summary(institution)


def test_operador_multiinstitucion_debe_elegir_una_membresia_activa(
    operator_user,
    institution,
    category,
    ande,
    ande_category,
):
    InstitutionOperator.objects.create(user=operator_user, institution=ande, active=True)
    municipal_report = _report(category)
    ande_report = _report(ande_category)
    client = _client(operator_user)

    no_context_list = client.get(LIST_URL)
    no_context_detail = client.get(_detail_url(municipal_report))

    assert no_context_list.status_code == no_context_detail.status_code == 400
    assert "institution_id" in no_context_list.json()["detail"]
    assert "institution_id" in no_context_detail.json()["detail"]

    municipal = client.get(LIST_URL, {"institution_id": str(institution.pk)})
    electrical = client.get(LIST_URL, {"institution_id": str(ande.pk)})

    assert [row["id"] for row in municipal.json()["results"]] == [str(municipal_report.pk)]
    assert [row["id"] for row in electrical.json()["results"]] == [str(ande_report.pk)]
    assert client.get(_detail_url(municipal_report, institution.pk)).status_code == 200
    assert client.get(_detail_url(municipal_report, ande.pk)).status_code == 404


def test_contexto_malformado_es_400(operator_user):
    client = _client(operator_user)

    list_response = client.get(LIST_URL, {"institution_id": "no-es-un-uuid"})
    detail_response = client.get(f"{LIST_URL}{uuid.uuid4()}/?institution_id=no-es-un-uuid")

    assert list_response.status_code == detail_response.status_code == 400
    assert "institution_id" in list_response.json()["detail"]
    assert "institution_id" in detail_response.json()["detail"]


def test_contextos_fuera_de_membresias_activas_comparten_403_generico(
    operator_user,
    essap,
):
    InstitutionOperator.objects.create(user=operator_user, institution=essap, active=False)
    client = _client(operator_user)

    inactive = client.get(LIST_URL, {"institution_id": str(essap.pk)})
    nonexistent = client.get(LIST_URL, {"institution_id": str(uuid.uuid4())})

    assert inactive.status_code == nonexistent.status_code == 403
    assert inactive.json() == nonexistent.json()
    serialized = inactive.content.decode()
    assert str(essap.pk) not in serialized
    assert essap.name not in serialized


def test_fuera_de_scope_e_inexistente_son_el_mismo_404(
    operator_user,
    ande_category,
):
    foreign = _report(ande_category)
    client = _client(operator_user)

    hidden = client.get(_detail_url(foreign))
    missing = client.get(f"{LIST_URL}{uuid.uuid4()}/")

    assert hidden.status_code == missing.status_code == 404
    assert hidden.json() == missing.json()
    assert client.get(LIST_URL).json()["results"] == []


def test_sugerencia_ia_nunca_gobierna_el_scope(
    operator_user,
    category,
    ande,
    user_factory,
):
    report = _report(category, institution=None)
    AiClassification.objects.create(report=report, suggested_institution=ande)
    ande_operator = user_factory(email="ia-no-tenancy@example.com", role=UserRole.OPERATOR)
    InstitutionOperator.objects.create(user=ande_operator, institution=ande, active=True)

    assert _client(operator_user).get(_detail_url(report)).status_code == 200
    assert _client(ande_operator).get(_detail_url(report)).status_code == 404


# --------------------------------------------------------------------------- #
# Roles, membresías y contrato de errores
# --------------------------------------------------------------------------- #


def test_anonimo_recibe_401_en_ambos_get(category):
    report = _report(category)

    assert _client().get(LIST_URL).status_code == 401
    assert _client().get(_detail_url(report)).status_code == 401


@pytest.mark.parametrize("role", [UserRole.CITIZEN, UserRole.ADMIN])
def test_ciudadano_y_admin_reciben_403_en_ambos_get(user_factory, category, role):
    user = user_factory(email=f"rol-{role}@example.com", role=role)
    report = _report(category)
    client = _client(user)

    assert client.get(LIST_URL).status_code == 403
    assert client.get(_detail_url(report)).status_code == 403


def test_operador_sin_membresia_activa_recibe_403_en_ambos_get(
    user_factory,
    institution,
    category,
):
    operator = user_factory(email="operador-inactivo@example.com", role=UserRole.OPERATOR)
    InstitutionOperator.objects.create(user=operator, institution=institution, active=False)
    report = _report(category)
    client = _client(operator)

    assert client.get(LIST_URL).status_code == 403
    assert client.get(_detail_url(report)).status_code == 403


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_coleccion_y_detalle_no_aceptan_escrituras(operator_user, category, method):
    report = _report(category)
    client = _client(operator_user)

    assert getattr(client, method)(LIST_URL, {}).status_code == 405
    assert getattr(client, method)(_detail_url(report), {}).status_code == 405


@pytest.mark.parametrize("action", ["validate", "reject"])
def test_s9_a_no_publica_acciones_de_validacion_o_rechazo(
    operator_user,
    category,
    action,
):
    report = _report(category)

    response = _client(operator_user).post(f"{LIST_URL}{report.pk}/{action}/", {})

    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# Bandeja: forma, filtros, orden y paginación
# --------------------------------------------------------------------------- #


def test_lista_tiene_whitelist_exacta_e_institucion_actual_separada_del_fallback(
    operator_user,
    institution,
    category,
):
    fallback = _report(category, index=1, institution=None)
    assigned = _report(category, index=2, institution=institution)

    response = _client(operator_user).get(LIST_URL)

    assert response.status_code == 200
    assert response["Cache-Control"] == "private, no-store"
    rows = {row["id"]: row for row in response.json()["results"]}
    assert set(rows) == {str(fallback.pk), str(assigned.pk)}
    assert all(set(row) == LIST_FIELDS for row in rows.values())
    assert all(set(row["category"]) == SUMMARY_FIELDS for row in rows.values())
    assert rows[str(fallback.pk)]["institution"] is None
    assert rows[str(fallback.pk)]["effective_read_institution"] == _summary(institution)
    assert rows[str(assigned.pk)]["institution"] == _summary(institution)
    assert rows[str(assigned.pk)]["effective_read_institution"] == _summary(institution)


def test_lista_vacia_es_200_paginado(operator_user):
    response = _client(operator_user).get(LIST_URL)

    assert response.status_code == 200
    assert response.json() == {
        "count": 0,
        "next": None,
        "previous": None,
        "results": [],
    }


def test_filtros_de_estado_prioridad_y_categoria(
    operator_user,
    category,
    municipal_category,
):
    created_high = _report(
        category,
        index=1,
        status=ReportStatus.CREADO,
        priority=ReportPriority.ALTA,
    )
    assigned_medium = _report(
        category,
        index=2,
        status=ReportStatus.ASIGNADO,
        priority=ReportPriority.MEDIA,
    )
    assigned_high_other_category = _report(
        municipal_category,
        index=3,
        status=ReportStatus.ASIGNADO,
        priority=ReportPriority.ALTA,
    )
    client = _client(operator_user)

    by_status = client.get(LIST_URL, {"status": ReportStatus.ASIGNADO}).json()
    by_priority = client.get(LIST_URL, {"priority": ReportPriority.MEDIA}).json()
    by_category = client.get(LIST_URL, {"category": category.slug}).json()

    assert {row["id"] for row in by_status["results"]} == {
        str(assigned_medium.pk),
        str(assigned_high_other_category.pk),
    }
    assert [row["id"] for row in by_priority["results"]] == [str(assigned_medium.pk)]
    assert {row["id"] for row in by_category["results"]} == {
        str(created_high.pk),
        str(assigned_medium.pk),
    }


def test_rango_de_fechas_es_inclusivo_en_zona_horaria_local(operator_user, category):
    selected_day = date(2026, 8, 20)
    tz = timezone.get_current_timezone()
    at_start = timezone.make_aware(datetime.combine(selected_day, time.min), tz)
    at_end = timezone.make_aware(datetime.combine(selected_day, time.max), tz)
    before = _set_created_at(_report(category, index=1), at_start - timedelta(microseconds=1))
    first = _set_created_at(_report(category, index=2), at_start)
    last = _set_created_at(_report(category, index=3), at_end)
    after = _set_created_at(_report(category, index=4), at_end + timedelta(microseconds=1))

    response = _client(operator_user).get(
        LIST_URL,
        {"created_from": selected_day.isoformat(), "created_to": selected_day.isoformat()},
    )

    assert response.status_code == 200
    assert [row["id"] for row in response.json()["results"]] == [
        str(first.pk),
        str(last.pk),
    ]
    assert str(before.pk) not in response.content.decode()
    assert str(after.pk) not in response.content.decode()


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"status": "NUEVO"}, "status"),
        ({"priority": "URGENTE"}, "priority"),
        ({"category": "no-existe"}, "category"),
        ({"created_from": "2026-02-30"}, "created_from"),
        ({"created_to": "ayer"}, "created_to"),
        ({"page": "0"}, "page"),
        ({"page_size": "100"}, "page_size"),
    ],
)
def test_lista_rechaza_parametros_invalidos_o_desconocidos(
    operator_user,
    params,
    field,
):
    response = _client(operator_user).get(LIST_URL, params)

    assert response.status_code == 400
    assert field in response.json()["detail"]


def test_lista_rechaza_categoria_inactiva(operator_user, institution):
    inactive = Category.objects.create(
        name="Categoría inactiva S9",
        slug="categoria-inactiva-s9",
        default_institution=institution,
        is_active=False,
    )

    response = _client(operator_user).get(LIST_URL, {"category": inactive.slug})

    assert response.status_code == 400
    assert "category" in response.json()["detail"]


def test_lista_rechaza_rango_invertido(operator_user):
    response = _client(operator_user).get(
        LIST_URL,
        {"created_from": "2026-08-21", "created_to": "2026-08-20"},
    )

    assert response.status_code == 400


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", ReportStatus.CREADO),
        ("priority", ReportPriority.MEDIA),
        ("category", "baches"),
        ("created_from", "2026-08-20"),
        ("created_to", "2026-08-20"),
        ("page", "1"),
    ],
)
def test_lista_rechaza_filtros_repetidos(operator_user, field, value):
    response = _client(operator_user).get(f"{LIST_URL}?{field}={value}&{field}={value}")

    assert response.status_code == 400
    assert field in response.json()["detail"]


def test_lista_rechaza_contexto_repetido(operator_user, institution):
    value = str(institution.pk)

    response = _client(operator_user).get(
        f"{LIST_URL}?institution_id={value}&institution_id={value}"
    )

    assert response.status_code == 400
    assert "institution_id" in response.json()["detail"]


def test_orden_es_prioridad_descendente_antiguedad_e_id_ascendente(
    operator_user,
    category,
):
    base = timezone.now() - timedelta(days=10)
    medium_oldest = _set_created_at(
        _report(
            category,
            id=uuid.UUID(int=20),
            priority=ReportPriority.MEDIA,
        ),
        base,
    )
    high_old = _set_created_at(
        _report(
            category,
            id=uuid.UUID(int=30),
            priority=ReportPriority.ALTA,
        ),
        base + timedelta(days=1),
    )
    high_tie_low_id = _set_created_at(
        _report(
            category,
            id=uuid.UUID(int=1),
            priority=ReportPriority.ALTA,
        ),
        base + timedelta(days=2),
    )
    high_tie_high_id = _set_created_at(
        _report(
            category,
            id=uuid.UUID(int=2),
            priority=ReportPriority.ALTA,
        ),
        base + timedelta(days=2),
    )
    low = _set_created_at(
        _report(
            category,
            id=uuid.UUID(int=40),
            priority=ReportPriority.BAJA,
        ),
        base - timedelta(days=1),
    )

    response = _client(operator_user).get(LIST_URL)

    assert [row["id"] for row in response.json()["results"]] == [
        str(high_old.pk),
        str(high_tie_low_id.pk),
        str(high_tie_high_id.pk),
        str(medium_oldest.pk),
        str(low.pk),
    ]


def test_lista_pagina_20_sin_perder_el_orden(operator_user, category):
    base = timezone.now() - timedelta(days=30)
    reports = []
    for index in range(23):
        report = _report(
            category,
            index=index,
            id=uuid.UUID(int=100 + index),
            priority=ReportPriority.ALTA,
        )
        reports.append(_set_created_at(report, base + timedelta(minutes=index)))

    first = _client(operator_user).get(LIST_URL)
    second = _client(operator_user).get(LIST_URL, {"page": 2})

    assert first.status_code == second.status_code == 200
    assert first.json()["count"] == second.json()["count"] == 23
    assert len(first.json()["results"]) == 20
    assert len(second.json()["results"]) == 3
    assert "page=2" in first.json()["next"]
    assert first.json()["previous"] is None
    assert second.json()["next"] is None
    assert second.json()["previous"] is not None
    assert [row["id"] for row in first.json()["results"]] == [
        str(report.pk) for report in reports[:20]
    ]
    assert [row["id"] for row in second.json()["results"]] == [
        str(report.pk) for report in reports[20:]
    ]


def test_filtros_y_paginacion_nunca_cuentan_reportes_ajenos(
    operator_user,
    category,
    ande,
):
    own = [
        _report(
            category,
            index=index,
            status=ReportStatus.CREADO,
            priority=ReportPriority.ALTA,
        )
        for index in range(21)
    ]
    foreign = [
        _report(
            category,
            index=100 + index,
            institution=ande,
            status=ReportStatus.CREADO,
            priority=ReportPriority.ALTA,
        )
        for index in range(3)
    ]

    response = _client(operator_user).get(
        LIST_URL,
        {
            "status": ReportStatus.CREADO,
            "priority": ReportPriority.ALTA,
            "category": category.slug,
            "page": 2,
        },
    )

    payload = response.json()
    serialized = response.content.decode()
    assert response.status_code == 200
    assert payload["count"] == len(own)
    assert len(payload["results"]) == 1
    assert all(str(report.pk) not in serialized for report in foreign)


def test_lista_no_firma_fotos(operator_user, category, signed_urls):
    report = _report(category)
    ReportMedia.objects.create(
        report=report,
        file_url="reports/privado/lista.jpg",
        file_hash="1" * 64,
    )

    response = _client(operator_user).get(LIST_URL)

    assert response.status_code == 200
    assert signed_urls.calls == []
    assert "reports/privado/lista.jpg" not in response.content.decode()
    assert "photo_signed_url" not in response.content.decode()


# --------------------------------------------------------------------------- #
# Detalle: foto, confirmaciones, IA nullable y privacidad
# --------------------------------------------------------------------------- #


def test_detalle_shape_con_confirmaciones_sin_foto_ni_ia(
    operator_user,
    institution,
    category,
    user_factory,
    signed_urls,
):
    report = _report(category, institution=None)
    confirmers = [user_factory(email=f"confirmador-s9-{index}@example.com") for index in range(2)]
    for confirmer in confirmers:
        ReportConfirmation.objects.create(report=report, citizen=confirmer)

    response = _client(operator_user).get(_detail_url(report))

    assert response.status_code == 200
    assert response["Cache-Control"] == "private, no-store"
    payload = response.json()
    assert set(payload) == DETAIL_FIELDS
    assert set(payload["category"]) == SUMMARY_FIELDS
    assert payload["institution"] is None
    assert payload["effective_read_institution"] == _summary(institution)
    assert payload["description"] == report.description
    assert payload["latitude"] == pytest.approx(report.location.y)
    assert payload["longitude"] == pytest.approx(report.location.x)
    assert payload["photo_signed_url"] is None
    assert payload["confirmation_count"] == 2
    assert payload["ai_classification"] is None
    assert signed_urls.calls == []


def test_detalle_firma_solo_la_primera_foto_por_created_at_e_id(
    operator_user,
    category,
    storage_config,
    signed_urls,
):
    report = _report(category)
    same_time = timezone.now() - timedelta(days=1)
    later_id = ReportMedia.objects.create(
        id=uuid.UUID(int=2),
        report=report,
        file_url="reports/privado/segunda-por-id.jpg",
        file_hash="2" * 64,
        media_type=MediaType.FOTO,
    )
    earlier_id = ReportMedia.objects.create(
        id=uuid.UUID(int=1),
        report=report,
        file_url="reports/privado/primera-por-id.jpg",
        file_hash="3" * 64,
        media_type=MediaType.FOTO,
    )
    ReportMedia.objects.filter(pk__in=[later_id.pk, earlier_id.pk]).update(created_at=same_time)

    response = _client(operator_user).get(_detail_url(report))

    assert response.status_code == 200
    assert response.json()["photo_signed_url"] == SIGNED_URL
    assert signed_urls.calls == [
        {
            "bucket": "reportes",
            "key": earlier_id.file_url,
            "expires_in": 300,
        }
    ]
    serialized = response.content.decode()
    assert earlier_id.file_url not in serialized
    assert later_id.file_url not in serialized


def test_detalle_no_firma_foto_de_reporte_fuera_de_scope(
    operator_user,
    ande_category,
    signed_urls,
):
    foreign = _report(ande_category)
    ReportMedia.objects.create(
        report=foreign,
        file_url="reports/ajeno/secreto.jpg",
        file_hash="4" * 64,
    )

    response = _client(operator_user).get(_detail_url(foreign))

    assert response.status_code == 404
    assert signed_urls.calls == []
    assert "reports/ajeno/secreto.jpg" not in response.content.decode()


def test_fallo_de_storage_en_detalle_es_503_generico(
    operator_user,
    category,
    storage_config,
    signed_urls,
):
    report = _report(category)
    key = "reports/privado/no-filtrar-error.jpg"
    ReportMedia.objects.create(report=report, file_url=key, file_hash="5" * 64)
    signed_urls.error = storage.StorageUnavailableError("detalle interno del proveedor")

    response = _client(operator_user).get(_detail_url(report))

    assert response.status_code == 503
    assert key not in response.content.decode()
    assert "detalle interno" not in response.content.decode()


def test_detalle_proyecta_solo_campos_seguros_de_la_ultima_clasificacion(
    operator_user,
    category,
    ande,
    ande_category,
):
    report = _report(category)
    older = AiClassification.objects.create(
        id=uuid.UUID(int=1),
        report=report,
        suggested_category=category,
        suggested_institution=category.default_institution,
        suggested_priority=ReportPriority.BAJA,
        confidence=Decimal("0.111"),
        inconsistency_flag=False,
        justification="Clasificación anterior.",
        model_name="modelo-anterior",
    )
    latest_low_id = AiClassification.objects.create(
        id=uuid.UUID(int=2),
        report=report,
        suggested_category=ande_category,
        suggested_institution=ande,
        suggested_priority=ReportPriority.ALTA,
        confidence=Decimal("0.875"),
        inconsistency_flag=True,
        justification="Requiere revisión humana.",
        model_name="modelo-seguro",
    )
    latest_high_id = AiClassification.objects.create(
        id=uuid.UUID(int=3),
        report=report,
        suggested_category=category,
        suggested_institution=category.default_institution,
        suggested_priority=ReportPriority.MEDIA,
        confidence=Decimal("0.625"),
        inconsistency_flag=False,
        justification="Última por desempate de UUID.",
        model_name="modelo-mas-reciente",
    )
    latest_time = timezone.now()
    AiClassification.objects.filter(pk=older.pk).update(
        created_at=latest_time - timedelta(minutes=1)
    )
    AiClassification.objects.filter(pk__in=[latest_low_id.pk, latest_high_id.pk]).update(
        created_at=latest_time
    )

    response = _client(operator_user).get(_detail_url(report))

    assert response.status_code == 200
    ai = response.json()["ai_classification"]
    assert set(ai) == AI_FIELDS
    assert ai["suggested_category"] == _summary(category)
    assert ai["suggested_institution"] == _summary(category.default_institution)
    assert ai["suggested_priority"] == ReportPriority.MEDIA
    assert ai["confidence"] == pytest.approx(0.625)
    assert ai["inconsistency_flag"] is False
    assert ai["justification"] == "Última por desempate de UUID."
    assert ai["model_name"] == "modelo-mas-reciente"
    assert ai["created_at"] is not None
    assert "modelo-anterior" not in response.content.decode()
    assert "modelo-seguro" not in response.content.decode()


def test_detalle_con_clasificacion_nullable_devuelve_estado_neutral(
    operator_user,
    category,
):
    report = _report(category)
    AiClassification.objects.create(report=report)

    response = _client(operator_user).get(_detail_url(report))

    assert response.status_code == 200
    ai = response.json()["ai_classification"]
    assert set(ai) == AI_FIELDS
    assert ai["suggested_category"] is None
    assert ai["suggested_institution"] is None
    assert ai["suggested_priority"] == ""
    assert ai["confidence"] is None
    assert ai["inconsistency_flag"] is False
    assert ai["justification"] == ""
    assert ai["model_name"] == ""


def test_detalle_no_expone_pii_claves_hashes_auditoria_ni_raw_response(
    operator_user,
    category,
    user_factory,
    storage_config,
    signed_urls,
):
    citizen = user_factory(
        email="pii-ciudadana-no-publicar@example.com",
        full_name="Nombre Privado S9",
    )
    report = _report(category, citizen=citizen)
    key = "reports/objeto-secreto-s9/foto.jpg"
    file_hash = "abcdef" * 10 + "abcd"
    ReportMedia.objects.create(
        report=report,
        file_url=key,
        file_hash=file_hash,
        uploaded_by=citizen,
    )
    AuditLog.objects.create(
        actor_user=citizen,
        actor_type=ActorType.AI,
        action="ai.classification.completed",
        payload={"report_id": str(report.pk), "raw_response": "RAW-SECRETO-S9"},
    )

    response = _client(operator_user).get(_detail_url(report))

    assert response.status_code == 200
    serialized = response.content.decode()
    for forbidden in (
        citizen.email,
        citizen.full_name,
        key,
        file_hash,
        "RAW-SECRETO-S9",
        "raw_response",
        "citizen",
        "uploaded_by",
        "validated_by",
        "audit",
    ):
        assert forbidden not in serialized


# --------------------------------------------------------------------------- #
# Pureza de GET y presupuestos de consultas
# --------------------------------------------------------------------------- #


def test_ambos_get_son_read_only_y_no_materializan_el_fallback(
    operator_user,
    category,
):
    report = _report(category, institution=None)
    original_updated_at = report.updated_at
    counts_before = {
        "reports": Report.objects.count(),
        "memberships": InstitutionOperator.objects.count(),
        "audit": AuditLog.objects.count(),
        "ai": AiClassification.objects.count(),
    }
    client = _client(operator_user)

    with CaptureQueriesContext(connection) as captured:
        assert client.get(LIST_URL).status_code == 200
        assert client.get(_detail_url(report)).status_code == 200

    mutating_verbs = ("INSERT ", "UPDATE ", "DELETE ", "ALTER ", "CREATE ", "DROP ")
    assert not [
        query["sql"]
        for query in captured.captured_queries
        if query["sql"].lstrip().upper().startswith(mutating_verbs)
    ]
    report.refresh_from_db()
    assert report.institution_id is None
    assert report.updated_at == original_updated_at
    assert Report.objects.count() == counts_before["reports"]
    assert InstitutionOperator.objects.count() == counts_before["memberships"]
    assert AuditLog.objects.count() == counts_before["audit"]
    assert AiClassification.objects.count() == counts_before["ai"]


def test_lista_de_20_no_tiene_n_mas_uno(
    operator_user,
    category,
):
    for index in range(20):
        _report(category, index=index)
    client = _client(operator_user)

    with CaptureQueriesContext(connection) as captured:
        response = client.get(LIST_URL)

    assert response.status_code == 200
    assert len(response.json()["results"]) == 20
    assert len(captured) <= 6


def test_detalle_completo_tiene_presupuesto_razonable_de_queries(
    operator_user,
    category,
    user_factory,
    storage_config,
    signed_urls,
):
    report = _report(category)
    ReportMedia.objects.create(
        report=report,
        file_url="reports/query-budget/foto.jpg",
        file_hash="9" * 64,
    )
    AiClassification.objects.create(
        report=report,
        suggested_category=category,
        suggested_institution=category.default_institution,
    )
    for index in range(2):
        confirmer = user_factory(email=f"query-confirm-{index}@example.com")
        ReportConfirmation.objects.create(report=report, citizen=confirmer)
    client = _client(operator_user)

    with CaptureQueriesContext(connection) as captured:
        response = client.get(_detail_url(report))

    assert response.status_code == 200
    assert response.json()["confirmation_count"] == 2
    assert response.json()["photo_signed_url"] == SIGNED_URL
    assert response.json()["ai_classification"] is not None
    assert len(captured) <= 8
