"""Agregados de solo lectura, con alcance institucional efectivo compartido."""

from collections import defaultdict
from datetime import timedelta
from functools import cached_property
from statistics import mean, median
from zoneinfo import ZoneInfo

from django.db.models import Count, Min, OuterRef, Q, Subquery, Sum, UUIDField
from django.db.models.functions import Coalesce, TruncDate, TruncMonth
from django.utils import timezone

from apps.admin_app.models import Institution
from apps.analytics_app import periods
from apps.analytics_app.constants import MIN_CELL_SIZE, PUBLIC_WINDOW_MONTHS, TREND_MONTHS
from apps.analytics_app.privacy import complement, suppress
from apps.core.enums import ReportPriority, ReportStatus
from apps.reports_app.models import Report, ReportConfirmation, ReportStatusHistory
from apps.rewards_app.enums import RewardEventType, RewardTxStatus
from apps.rewards_app.models import RewardRedemption, RewardTransaction

LOCAL_TZ = ZoneInfo("America/Asuncion")
ACTIVE = [
    ReportStatus.CREADO,
    ReportStatus.CLASIFICADO,
    ReportStatus.ASIGNADO,
    ReportStatus.EN_PROCESO,
]
EFFECTIVE_INSTITUTION = Coalesce(
    "institution_id", "category__default_institution_id", output_field=UUIDField()
)


def scoped_reports(*, institution=None):
    qs = Report.objects.annotate(effective_institution_id=EFFECTIVE_INSTITUTION)
    return qs if institution is None else qs.filter(effective_institution_id=institution.pk)


def _first(qs, status, field):
    history = (
        ReportStatusHistory.objects.filter(report_id=OuterRef("pk"), new_status=status)
        .order_by()
        .values("report_id")
        .annotate(first=Min("created_at"))
        .values("first")
    )
    return qs.annotate(**{field: Subquery(history)})


def _between(qs, field, start, end):
    return qs.filter(**{f"{field}__gte": start, f"{field}__lt": end})


def _ratio(a, b):
    return round(a / b, 3) if b else None


def _ref(obj):
    return {"id": str(obj.pk), "name": obj.name, "slug": obj.slug}


def _child(qs, institution):
    qs = qs.annotate(
        effective_institution_id=Coalesce(
            "report__institution_id",
            "report__category__default_institution_id",
            output_field=UUIDField(),
        )
    )
    return qs if institution is None else qs.filter(effective_institution_id=institution.pk)


def _own(qs, institution):
    return qs if institution is None else qs.filter(institution=institution)


class _Durations(list[float]):
    """Horas con respaldo por institución para aplicar SLA distintos al consolidar."""

    def __init__(self, rows):
        super().__init__()
        self.by_institution = defaultdict(list)
        for row in rows:
            hours = (row["first_resolved_at"] - row["created_at"]).total_seconds() / 3600
            self.append(hours)
            self.by_institution[row["effective_institution_id"]].append(hours)


class _Metrics:
    """Una consulta por agregado para todos los alcances; sin consultas por fila."""

    def __init__(self, *, institution, start, end, now):
        self.institution = institution
        self.start, self.end, self.now = start, end, now
        self.base = scoped_reports(institution=institution)

    @cached_property
    def institutions(self):
        return list(Institution.objects.order_by("name", "pk"))

    @cached_property
    def received(self):
        start = self.start
        end = self.end
        base = self.base
        return list(
            _between(base, "created_at", start, end)
            .order_by()
            .annotate(day=TruncDate("created_at", tzinfo=LOCAL_TZ))
            .values(
                "effective_institution_id",
                "day",
                "status",
                "priority",
                "category_id",
                "category__name",
                "category__slug",
            )
            .annotate(count=Count("pk"))
        )

    @cached_property
    def validated(self):
        start = self.start
        end = self.end
        base = self.base
        return list(
            _between(
                _first(base, ReportStatus.ASIGNADO, "first_validated_at"),
                "first_validated_at",
                start,
                end,
            )
            .order_by()
            .values("effective_institution_id")
            .annotate(count=Count("pk"))
        )

    @cached_property
    def rejected(self):
        institution = self.institution
        start = self.start
        end = self.end
        rejected = _child(
            ReportStatusHistory.objects.filter(new_status=ReportStatus.RECHAZADO), institution
        )
        return list(
            _between(rejected, "created_at", start, end)
            .order_by()
            .values("effective_institution_id")
            .annotate(count=Count("report_id", distinct=True))
        )

    @cached_property
    def resolved(self):
        start = self.start
        end = self.end
        base = self.base
        return list(
            _between(
                _first(base, ReportStatus.RESUELTO, "first_resolved_at"),
                "first_resolved_at",
                start,
                end,
            )
            .order_by()
            .values("effective_institution_id", "created_at", "first_resolved_at")
        )

    @cached_property
    def confirmed(self):
        institution = self.institution
        start = self.start
        end = self.end
        return list(
            _between(
                _child(ReportConfirmation.objects.all(), institution), "created_at", start, end
            )
            .order_by()
            .values("effective_institution_id")
            .annotate(count=Count("pk"), citizens=Count("citizen_id", distinct=True))
        )

    @cached_property
    def ledger(self):
        institution = self.institution
        start = self.start
        end = self.end
        tx = _own(RewardTransaction.objects.all(), institution)
        credited = Q(
            status=RewardTxStatus.ACREDITADO, credited_at__gte=start, credited_at__lt=end
        ) & ~Q(event_type=RewardEventType.REDEMPTION)
        reverted = Q(
            status=RewardTxStatus.REVERTIDO, reversed_at__gte=start, reversed_at__lt=end
        ) & ~Q(event_type__in=[RewardEventType.REVERSAL, RewardEventType.REDEMPTION])
        return list(
            tx.order_by()
            .values("institution_id")
            .annotate(
                points=Sum("impact_delta", filter=credited),
                credits=Sum("credit_delta", filter=credited),
                reverted=Sum("credit_delta", filter=reverted),
                citizens=Count("user_id", distinct=True, filter=credited),
            )
        )

    @cached_property
    def redeemed(self):
        institution = self.institution
        start = self.start
        end = self.end
        return list(
            _between(_own(RewardRedemption.objects.all(), institution), "created_at", start, end)
            .order_by()
            .values("institution_id")
            .annotate(
                count=Count("pk"),
                credits=Sum("credits_spent"),
                citizens=Count("user_id", distinct=True),
            )
        )

    @cached_property
    def overdue(self):
        now = self.now
        base = self.base
        overdue = Q(pk__in=[])
        for inst in self.institutions:
            if inst.sla_hours is not None:
                overdue |= Q(
                    effective_institution_id=inst.pk,
                    created_at__lt=now - timedelta(hours=inst.sla_hours),
                )
        return list(
            base.filter(overdue, status__in=ACTIVE)
            .order_by()
            .values("effective_institution_id")
            .annotate(count=Count("pk"))
        )

    def rows(self, rows, institution, *, own=False):
        key = "institution_id" if own else "effective_institution_id"
        return rows if institution is None else [r for r in rows if r[key] == institution.pk]

    def flow(self, institution):
        statuses = dict.fromkeys(ReportStatus.values, 0)
        priorities = dict.fromkeys(ReportPriority.values, 0)
        categories = {}
        for row in self.rows(self.received, institution):
            count = row["count"]
            statuses[row["status"]] += count
            priorities[row["priority"]] += count
            entry = categories.setdefault(
                row["category_id"],
                {
                    "category": {
                        "id": str(row["category_id"]),
                        "name": row["category__name"],
                        "slug": row["category__slug"],
                    },
                    "count": 0,
                },
            )
            entry["count"] += count
        total = sum(statuses.values())
        return {
            "received": total,
            "by_status": statuses,
            "by_priority": priorities,
            "by_category": sorted(
                categories.values(), key=lambda r: (-r["count"], r["category"]["name"])
            ),
            "resolution_rate": _ratio(
                statuses[ReportStatus.RESUELTO] + statuses[ReportStatus.CERRADO],
                total - statuses[ReportStatus.RECHAZADO],
            ),
        }

    def decisions(self, institution):
        validated = sum(r["count"] for r in self.rows(self.validated, institution))
        rejected = sum(r["count"] for r in self.rows(self.rejected, institution))
        return {
            "validated": validated,
            "rejected": rejected,
            "validation_rate": _ratio(validated, validated + rejected),
            "rejection_rate": _ratio(rejected, validated + rejected),
        }

    def resolution(self, institution):
        durations = _Durations(self.rows(self.resolved, institution))
        return {
            "resolved": len(durations),
            "mean_hours": round(mean(durations), 1) if durations else None,
            "median_hours": round(median(durations), 1) if durations else None,
        }, durations

    def sla(self, institution, *, durations=None):
        institutions = self.institutions if institution is None else [institution]
        configured = [i for i in institutions if i.sla_hours is not None]
        result = {
            "hours": institution.sla_hours if institution else None,
            "configured": bool(configured),
            "within": None,
            "breached": None,
            "compliance_rate": None,
            "open_over_sla": None,
        }
        if configured:
            within = breached = overdue = 0
            for inst in configured:
                if durations is None:
                    _, hours = self.resolution(inst)
                elif institution is None:
                    hours = durations.by_institution[inst.pk]
                else:
                    hours = durations
                within += sum(d <= inst.sla_hours for d in hours)
                breached += sum(d > inst.sla_hours for d in hours)
                overdue += sum(r["count"] for r in self.rows(self.overdue, inst))
            result.update(
                within=within,
                breached=breached,
                compliance_rate=_ratio(within, within + breached),
                open_over_sla=overdue,
            )
        return result

    def incentives(self, institution):
        tx = self.rows(self.ledger, institution, own=True)
        redemptions = self.rows(self.redeemed, institution, own=True)
        return {
            "points_credited": sum(r["points"] or 0 for r in tx),
            "credits_issued": sum(r["credits"] or 0 for r in tx),
            "credits_reverted": sum(r["reverted"] or 0 for r in tx),
            "redemptions": sum(r["count"] for r in redemptions),
            "credits_redeemed": sum(r["credits"] or 0 for r in redemptions),
        }

    def series(self, institution):
        counts = defaultdict(lambda: {"received": 0, "resolved": 0})
        for row in self.rows(self.received, institution):
            counts[row["day"]]["received"] += row["count"]
        for row in self.rows(self.resolved, institution):
            counts[row["first_resolved_at"].astimezone(LOCAL_TZ).date()]["resolved"] += 1
        day = self.start.astimezone(LOCAL_TZ).date()
        last = min(
            (self.end - timedelta(microseconds=1)).astimezone(LOCAL_TZ).date(),
            self.now.astimezone(LOCAL_TZ).date(),
        )
        result = []
        while day <= last:
            result.append({"date": day.isoformat(), **counts[day]})
            day += timedelta(days=1)
        return result


def _dataset(institution, start, end, now=None):
    return _Metrics(
        institution=institution,
        start=start,
        end=end,
        now=now if now is not None else timezone.now(),
    )


def report_flow(*, institution, start, end):
    return _dataset(institution, start, end).flow(institution)


def decisions(*, institution, start, end):
    return _dataset(institution, start, end).decisions(institution)


def resolution(*, institution, start, end):
    return _dataset(institution, start, end).resolution(institution)


def sla(*, institution, durations, now):
    # El consolidado conserva el alcance de cada duración: un único umbral no
    # permite evaluar instituciones que tienen SLA diferentes (o no lo tienen).
    if institution is None and not isinstance(durations, _Durations):
        raise ValueError("Usá las duraciones devueltas por resolution para el consolidado.")
    start, end = periods.month_range(periods.current_period(now))
    return _dataset(institution, start, end, now).sla(institution, durations=durations)


def confirmations(*, institution, start, end):
    data = _dataset(institution, start, end)
    return {"count": sum(r["count"] for r in data.rows(data.confirmed, institution))}


def incentives(*, institution, start, end):
    return _dataset(institution, start, end).incentives(institution)


def daily_series(*, institution, start, end, now):
    return _dataset(institution, start, end, now).series(institution)


def monthly_trend(*, institution, period):
    months = periods.trailing_months(period, TREND_MONTHS)
    start = periods.month_range(months[0])[0]
    end = periods.month_range(period)[1]
    base = scoped_reports(institution=institution)
    result = {
        periods.format_period(m): {"period": periods.format_period(m), "received": 0, "resolved": 0}
        for m in months
    }
    for field, qs, name in [
        ("created_at", base, "received"),
        ("first_resolved_at", _first(base, ReportStatus.RESUELTO, "first_resolved_at"), "resolved"),
    ]:
        rows = (
            _between(qs, field, start, end)
            .order_by()
            .annotate(month=TruncMonth(field, tzinfo=LOCAL_TZ))
            .values("month")
            .annotate(count=Count("pk"))
        )
        for row in rows:
            result[row["month"].strftime("%Y-%m")][name] = row["count"]
    return list(result.values())


def _dashboard(*, institution, period, now):
    now = now if now is not None else timezone.now()
    start, end = periods.month_range(period)
    data = _dataset(institution, start, end, now)
    result = {
        "scope": "institution" if institution else "all",
        "institution": _ref(institution) if institution else None,
        "period": periods.format_period(period),
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "is_current_period": period == periods.current_period(now),
        "generated_at": now.astimezone(LOCAL_TZ).isoformat(),
        "reports": data.flow(institution),
        "decisions": data.decisions(institution),
        "resolution": data.resolution(institution)[0],
        "sla": data.sla(institution),
        "confirmations": {"count": sum(r["count"] for r in data.confirmed)},
        "incentives": data.incentives(institution),
        "series": {"granularity": "day", "points": data.series(institution)},
        "trend": monthly_trend(institution=institution, period=period),
        "by_institution": None,
    }
    if institution is None:
        result["by_institution"] = []
        for inst in data.institutions:
            dec, res, inst_sla = data.decisions(inst), data.resolution(inst)[0], data.sla(inst)
            result["by_institution"].append(
                {
                    "institution": _ref(inst),
                    "received": data.flow(inst)["received"],
                    "validated": dec["validated"],
                    "rejected": dec["rejected"],
                    "resolved": res["resolved"],
                    "median_hours": res["median_hours"],
                    "sla": {k: inst_sla[k] for k in ["hours", "configured", "compliance_rate"]},
                }
            )
    return result


def institution_metrics(*, institution, period, now=None):
    return _dashboard(institution=institution, period=period, now=now)


def consolidated_metrics(*, period, now=None):
    return _dashboard(institution=None, period=period, now=now)


def public_metrics(*, now=None):
    """Publica únicamente agregados con supresión antes de entregarlos a la caché."""
    now = now if now is not None else timezone.now()
    start, end = periods.public_window(now)
    data = _dataset(None, start, end, now)
    # Respaldos globales distintos, independientes del número de instituciones.
    global_counts = {
        "confirmations": _between(ReportConfirmation.objects.all(), "created_at", start, end)
        .order_by()
        .aggregate(n=Count("citizen_id", distinct=True))["n"],
        "redemptions": _between(RewardRedemption.objects.all(), "created_at", start, end)
        .order_by()
        .aggregate(n=Count("user_id", distinct=True))["n"],
        "credits_issued": _between(
            RewardTransaction.objects.filter(status=RewardTxStatus.ACREDITADO).exclude(
                event_type=RewardEventType.REDEMPTION
            ),
            "credited_at",
            start,
            end,
        )
        .order_by()
        .aggregate(n=Count("user_id", distinct=True))["n"],
    }

    def cells(inst):
        flow, dec, res, inst_sla = (
            data.flow(inst),
            data.decisions(inst),
            data.resolution(inst)[0],
            data.sla(inst),
        )
        inc = data.incentives(inst)
        result = {
            "received": flow["received"],
            "validated": dec["validated"],
            "rejected": dec["rejected"],
            "resolved": res["resolved"],
            "resolution_rate": flow["resolution_rate"],
            "median_resolution_hours": res["median_hours"],
            "confirmations": sum(r["count"] for r in data.rows(data.confirmed, inst)),
            "redemptions": inc["redemptions"],
            "credits_issued": inc["credits_issued"],
        }
        counts = (
            global_counts.copy()
            if inst is None
            else {
                "confirmations": sum(r["citizens"] for r in data.rows(data.confirmed, inst)),
                "redemptions": sum(r["citizens"] for r in data.rows(data.redeemed, inst, own=True)),
                "credits_issued": sum(
                    r["citizens"] for r in data.rows(data.ledger, inst, own=True)
                ),
            }
        )
        counts.update(
            resolution_rate=flow["received"] - flow["by_status"][ReportStatus.RECHAZADO],
            median_resolution_hours=res["resolved"],
        )
        if inst is not None:
            result.update(
                institution=_ref(inst),
                sla={k: inst_sla[k] for k in ["configured", "hours", "compliance_rate"]},
            )
            if inst_sla["configured"]:
                counts["sla.compliance_rate"] = inst_sla["within"] + inst_sla["breached"]
        return suppress(result, counts=counts)

    totals = cells(None)
    rows = [cells(i) for i in data.institutions if i.is_active]
    complement(totals, rows)
    return {
        "window": {
            "from": start.date().isoformat(),
            "to": now.astimezone(LOCAL_TZ).date().isoformat(),
            "months": PUBLIC_WINDOW_MONTHS,
        },
        "generated_at": now.astimezone(LOCAL_TZ).isoformat(),
        "min_cell_size": MIN_CELL_SIZE,
        "totals": totals,
        "institutions": rows,
    }
