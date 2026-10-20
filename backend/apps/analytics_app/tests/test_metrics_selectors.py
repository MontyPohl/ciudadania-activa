"""Reglas de métricas sobre un conjunto conocido, sin servicios ni colas."""

from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from django.contrib.gis.geos import Point

from apps.admin_app.models import Category, Institution
from apps.analytics_app import selectors
from apps.core.enums import ReportPriority, ReportStatus
from apps.reports_app.models import Report, ReportConfirmation, ReportStatusHistory
from apps.rewards_app.enums import RewardEventType, RewardTxStatus
from apps.rewards_app.models import Reward, RewardRedemption, RewardTransaction

TZ = ZoneInfo("America/Asuncion")
PERIOD = date(2026, 9, 1)
NOW = datetime(2026, 10, 20, 12, tzinfo=TZ)


def at(month, day, hour=0, minute=0):
    return datetime(2026, month, day, hour, minute, tzinfo=TZ)


def dated(model, when, **fields):
    obj = model.objects.create(**fields)
    model.objects.filter(pk=obj.pk).update(created_at=when)
    obj.refresh_from_db()
    return obj


@pytest.fixture
def metrics_world(institution, category, citizen, other_citizen):
    institution.sla_hours = 48
    institution.save(update_fields=["sla_hours"])
    b = Institution.objects.create(name="Institución B de prueba", slug="metrics-b")
    alpha = Category.objects.create(
        name="Alpha de prueba", slug="metrics-alpha", default_institution=institution
    )
    beta = Category.objects.create(
        name="Beta de prueba", slug="metrics-beta", default_institution=institution
    )
    other = Category.objects.create(
        name="Otra de prueba", slug="metrics-other", default_institution=b
    )

    def report(when, status, cat=alpha, **extra):
        return dated(
            Report,
            when,
            citizen=citizen,
            category=cat,
            status=status,
            description="Incidencia ficticia",
            location=Point(-57.63, -25.26, srid=4326),
            **extra,
        )

    def history(obj, status, when, **extra):
        return dated(ReportStatusHistory, when, report=obj, new_status=status, **extra)

    r1 = report(at(9, 1), ReportStatus.RESUELTO, priority=ReportPriority.ALTA)
    history(r1, ReportStatus.ASIGNADO, at(9, 1, 1))
    history(r1, ReportStatus.RESUELTO, at(9, 2))
    # Una segunda resolución no altera el primer evento ni la duración.
    history(r1, ReportStatus.EN_PROCESO, at(9, 3))
    history(r1, ReportStatus.RESUELTO, at(9, 4))
    r2 = report(at(8, 30), ReportStatus.CERRADO)
    history(r2, ReportStatus.ASIGNADO, at(9, 1))
    history(r2, ReportStatus.RESUELTO, at(9, 2))
    rejected = report(at(9, 3), ReportStatus.RECHAZADO, beta)
    history(rejected, ReportStatus.RECHAZADO, at(9, 4))
    revalidated = report(at(8, 10), ReportStatus.ASIGNADO)
    history(revalidated, ReportStatus.ASIGNADO, at(8, 11))
    history(revalidated, ReportStatus.CREADO, at(9, 5))
    history(revalidated, ReportStatus.ASIGNADO, at(9, 6))
    classified = report(at(9, 8), ReportStatus.CLASIFICADO)
    history(
        classified,
        ReportStatus.CLASIFICADO,
        at(9, 8, 1),
        previous_status=ReportStatus.CREADO,
        changed_by=None,
    )
    edge = report(at(9, 30, 23, 30), ReportStatus.CREADO, priority=ReportPriority.BAJA)
    reassigned = report(at(9, 1), ReportStatus.RESUELTO, institution=b)
    history(reassigned, ReportStatus.ASIGNADO, at(9, 1))
    history(reassigned, ReportStatus.RESUELTO, at(9, 4))
    report(at(9, 5), ReportStatus.CREADO, other)
    for user in (citizen, other_citizen):
        dated(ReportConfirmation, at(9, 10), citizen=user, report=classified)

    def tx(amount, **extra):
        return RewardTransaction.objects.create(
            user=citizen, institution=institution, impact_delta=amount, credit_delta=amount, **extra
        )

    tx(
        30,
        report=r1,
        event_type=RewardEventType.REPORT_CREATED,
        status=RewardTxStatus.ACREDITADO,
        credited_at=at(9, 2),
    )
    tx(
        50,
        report=r2,
        event_type=RewardEventType.VALIDATION_BONUS,
        resolution_cycle=1,
        status=RewardTxStatus.ACREDITADO,
        credited_at=at(9, 2),
    )
    original = tx(
        20,
        report=classified,
        event_type=RewardEventType.VALIDATION_BONUS,
        resolution_cycle=1,
        status=RewardTxStatus.REVERTIDO,
        credited_at=at(8, 20),
        reversed_at=at(9, 10),
    )
    tx(
        -20,
        report=classified,
        event_type=RewardEventType.REVERSAL,
        resolution_cycle=1,
        related_transaction=original,
        status=RewardTxStatus.REVERTIDO,
        reversed_at=at(9, 10),
    )
    reward = Reward.objects.create(institution=institution, name="Vale ficticio")
    redemption = dated(
        RewardRedemption,
        at(9, 12),
        user=citizen,
        institution=institution,
        reward=reward,
        period_month=PERIOD,
        code="metrics-test",
    )
    RewardTransaction.objects.create(
        user=citizen,
        institution=institution,
        redemption=redemption,
        event_type=RewardEventType.REDEMPTION,
        status=RewardTxStatus.ACREDITADO,
        impact_delta=0,
        credit_delta=-1000,
        credited_at=at(9, 12),
    )
    return SimpleNamespace(a=institution, b=b, alpha=alpha, beta=beta, edge=edge)


def test_flujo_y_decisiones_de_la_institucion(metrics_world):
    world = metrics_world
    result = selectors.institution_metrics(institution=world.a, period=PERIOD, now=NOW)
    flow = result["reports"]
    assert flow["received"] == 4
    assert flow["by_status"] == {
        "CREADO": 1,
        "CLASIFICADO": 1,
        "ASIGNADO": 0,
        "EN_PROCESO": 0,
        "RESUELTO": 1,
        "CERRADO": 0,
        "RECHAZADO": 1,
    }
    assert flow["by_priority"] == {"ALTA": 1, "MEDIA": 2, "BAJA": 1}
    assert flow["by_category"] == [
        {
            "category": {
                "id": str(world.alpha.pk),
                "name": world.alpha.name,
                "slug": world.alpha.slug,
            },
            "count": 3,
        },
        {
            "category": {
                "id": str(world.beta.pk),
                "name": world.beta.name,
                "slug": world.beta.slug,
            },
            "count": 1,
        },
    ]
    assert flow["resolution_rate"] == 0.333
    assert result["decisions"] == {
        "validated": 2,
        "rejected": 1,
        "validation_rate": 0.667,
        "rejection_rate": 0.333,
    }
    assert result["series"]["points"][-1] == {"date": "2026-09-30", "received": 1, "resolved": 0}
    other = selectors.institution_metrics(institution=world.b, period=PERIOD, now=NOW)
    assert other["reports"]["received"] == 2
    assert other["decisions"]["validated"] == 1


@pytest.mark.parametrize("scope", ["a", "b", "all"])
def test_resolucion_y_sla(metrics_world, scope):
    if scope == "all":
        result = selectors.consolidated_metrics(period=PERIOD, now=NOW)
    else:
        result = selectors.institution_metrics(
            institution=getattr(metrics_world, scope), period=PERIOD, now=NOW
        )
    inst = None if scope == "all" else getattr(metrics_world, scope)
    start, end = selectors.periods.month_range(PERIOD)
    summary, durations = selectors.resolution(institution=inst, start=start, end=end)
    assert summary == result["resolution"]
    assert selectors.sla(institution=inst, durations=durations, now=NOW) == result["sla"]
    count, avg, middle = {"a": (2, 48.0, 48.0), "b": (1, 72.0, 72.0), "all": (3, 56.0, 72.0)}[scope]
    assert result["resolution"] == {"resolved": count, "mean_hours": avg, "median_hours": middle}
    if scope == "b":
        assert result["sla"] == {
            "hours": None,
            "configured": False,
            "within": None,
            "breached": None,
            "compliance_rate": None,
            "open_over_sla": None,
        }
    else:
        assert result["sla"] == {
            "hours": 48 if scope == "a" else None,
            "configured": True,
            "within": 1,
            "breached": 1,
            "compliance_rate": 0.5,
            "open_over_sla": 3,
        }
    if scope == "all":
        rows = result["by_institution"]
        assert [r["institution"]["name"] for r in rows] == sorted(
            r["institution"]["name"] for r in rows
        )
        row = next(r for r in rows if r["institution"]["id"] == str(metrics_world.b.pk))
        assert (row["received"], row["resolved"], row["median_hours"]) == (2, 1, 72.0)
        assert row["sla"]["configured"] is False


def test_incentivos_y_series(metrics_world):
    result = selectors.institution_metrics(institution=metrics_world.a, period=PERIOD, now=NOW)
    assert result["incentives"] == {
        "points_credited": 80,
        "credits_issued": 80,
        "credits_reverted": 20,
        "redemptions": 1,
        "credits_redeemed": 1000,
    }
    assert result["confirmations"] == {"count": 2}
    points = result["series"]["points"]
    assert len(points) == 30
    assert sum(p["received"] for p in points) == result["reports"]["received"]
    assert sum(p["resolved"] for p in points) == 2
    assert result["trend"] == [
        {
            "period": f"2026-{m:02}",
            "received": 2 if m == 8 else 4 if m == 9 else 0,
            "resolved": 2 if m == 9 else 0,
        }
        for m in range(4, 10)
    ]
    current = selectors.institution_metrics(
        institution=metrics_world.a, period=date(2026, 10, 1), now=NOW
    )
    assert len(current["series"]["points"]) == 20


@pytest.mark.parametrize(
    "citizens,resolutions,sla_hours",
    [
        (4, 1, None),
        (4, 4, 48),
        (5, 4, None),
        (5, 5, 48),
    ],
)
def test_supresion_publica(
    institution, category, report, user_factory, citizens, resolutions, sla_hours
):
    institution.sla_hours = sla_hours
    institution.save(update_fields=["sla_hours"])
    Report.objects.filter(pk=report.pk).update(created_at=at(9, 1), status=ReportStatus.RESUELTO)
    dated(ReportStatusHistory, at(9, 2), report=report, new_status=ReportStatus.RESUELTO)
    reward = Reward.objects.create(institution=institution, name="Vale público ficticio")
    for i in range(citizens):
        user = user_factory(email=f"metricas-{i}@example.com")
        dated(ReportConfirmation, at(9, 3), report=report, citizen=user)
        dated(
            RewardRedemption,
            at(9, 4),
            user=user,
            institution=institution,
            reward=reward,
            period_month=PERIOD,
            code=f"public-{i}",
        )
        RewardTransaction.objects.create(
            user=user,
            institution=institution,
            report=report,
            event_type=RewardEventType.VALIDATION_BONUS,
            resolution_cycle=i + 1,
            status=RewardTxStatus.ACREDITADO,
            impact_delta=10,
            credit_delta=10,
            credited_at=at(9, 5),
        )
    # El umbral usa casos para tasas/mediana y ciudadanos distintos para acciones.
    for _ in range(resolutions - 1):
        obj = dated(
            Report,
            at(9, 1),
            citizen=None,
            category=category,
            status=ReportStatus.RESUELTO,
            description="Ficticio",
            location=report.location,
        )
        dated(ReportStatusHistory, at(9, 2), report=obj, new_status=ReportStatus.RESUELTO)
    payload = selectors.public_metrics(now=NOW)
    row = next(r for r in payload["institutions"] if r["institution"]["id"] == str(institution.pk))
    for cells in (payload["totals"], row):
        assert cells["received"] == cells["resolved"] == resolutions
        for name, value in [
            ("confirmations", citizens),
            ("redemptions", citizens),
            ("credits_issued", citizens * 10),
        ]:
            assert cells[name] == (value if citizens == 5 else None)
            assert (name in cells["suppressed"]) == (citizens < 5)
        assert cells["median_resolution_hours"] == (24.0 if resolutions >= 5 else None)
        assert ("median_resolution_hours" in cells["suppressed"]) == (resolutions < 5)
        assert cells["resolution_rate"] == (1.0 if resolutions >= 5 else None)
        assert ("resolution_rate" in cells["suppressed"]) == (resolutions < 5)
    assert row["sla"]["configured"] == (sla_hours is not None)
    assert row["sla"]["compliance_rate"] == (1.0 if sla_hours and resolutions >= 5 else None)
    assert ("sla.compliance_rate" in row["suppressed"]) == (
        sla_hours is not None and resolutions < 5
    )
