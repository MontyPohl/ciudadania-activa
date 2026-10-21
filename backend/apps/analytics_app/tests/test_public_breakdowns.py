from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from apps.analytics_app import selectors
from apps.core.enums import ReportStatus
from apps.reports_app.models import ReportStatusHistory

pytestmark = pytest.mark.django_db
TZ = ZoneInfo("America/Asuncion")
NOW = datetime(2026, 10, 20, 12, tzinfo=TZ)


def set_created(obj, when):
    type(obj).objects.filter(pk=obj.pk).update(created_at=when)


def resolve(report, when):
    entry = ReportStatusHistory.objects.create(report=report, new_status=ReportStatus.RESUELTO)
    set_created(entry, when)


def test_empty_six_months_and_categories():
    result = selectors.public_metrics(now=NOW)
    assert set(result) == {
        "window",
        "generated_at",
        "min_cell_size",
        "totals",
        "institutions",
        "trend",
        "current_month_by_category",
    }
    assert [point["period"] for point in result["trend"]] == [
        "2026-05",
        "2026-06",
        "2026-07",
        "2026-08",
        "2026-09",
        "2026-10",
    ]
    assert all(
        point
        == {
            "period": point["period"],
            "received": None,
            "resolved": None,
            "suppressed": ["received", "resolved"],
        }
        for point in result["trend"]
    )
    assert len(result["current_month_by_category"]) == 10
    assert all(
        row["received"] is None and row["suppressed"] == ["received"]
        for row in result["current_month_by_category"]
    )
    assert result["totals"]["received"] == result["totals"]["resolved"] == 0


@pytest.mark.parametrize("count", [4, 5])
def test_threshold_first_resolution_and_current_category(report, count):
    set_created(report, datetime(2026, 4, 1, tzinfo=TZ))
    for _ in range(count):
        report.pk = None
        report.save()
        set_created(report, datetime(2026, 10, 1, tzinfo=TZ))
        resolve(report, datetime(2026, 10, 2, tzinfo=TZ))
        resolve(report, datetime(2026, 10, 3, tzinfo=TZ))
    result = selectors.public_metrics(now=NOW)
    point = result["trend"][-1]
    category = next(
        row
        for row in result["current_month_by_category"]
        if row["category"]["id"] == str(report.category_id)
    )
    expected = count if count >= 5 else None
    assert point["received"] == point["resolved"] == category["received"] == expected
    assert result["totals"]["resolved"] == count
    assert point["suppressed"] == ([] if count >= 5 else ["received", "resolved"])


def test_local_boundaries_and_no_future_data(report):
    # Una segunda resolución dentro de la ventana no repite la primera de abril.
    set_created(report, datetime(2026, 4, 30, 23, 59, tzinfo=TZ))
    resolve(report, datetime(2026, 4, 30, 23, 59, tzinfo=TZ))
    resolve(report, datetime(2026, 5, 1, tzinfo=TZ))
    for _ in range(5):
        report.pk = None
        report.save()
        set_created(report, datetime(2026, 5, 1, tzinfo=TZ))
        resolve(report, datetime(2026, 5, 1, tzinfo=TZ))
    report.pk = None
    report.save()
    set_created(report, datetime(2026, 10, 21, tzinfo=TZ))
    resolve(report, datetime(2026, 10, 21, tzinfo=TZ))
    result = selectors.public_metrics(now=NOW)
    assert result["trend"][0]["received"] == result["trend"][0]["resolved"] == 5
    assert result["totals"]["received"] == result["totals"]["resolved"] == 5
    assert result["trend"][-1]["received"] is None


def test_complementary_suppression_preserves_existing_totals(report):
    set_created(report, datetime(2026, 4, 1, tzinfo=TZ))
    for month, count in [(5, 1), (6, 5), (7, 6), (8, 7), (9, 8), (10, 9)]:
        for _ in range(count):
            report.pk = None
            report.save()
            set_created(report, datetime(2026, month, 1, tzinfo=TZ))
    result = selectors.public_metrics(now=NOW)
    assert result["totals"]["received"] == 36
    assert [point["received"] for point in result["trend"]] == [None, None, 6, 7, 8, 9]
    assert "received" in result["trend"][1]["suppressed"]


def test_window_crosses_year_boundary():
    result = selectors.public_metrics(now=datetime(2027, 1, 2, tzinfo=TZ))
    assert [point["period"] for point in result["trend"]] == [
        "2026-08",
        "2026-09",
        "2026-10",
        "2026-11",
        "2026-12",
        "2027-01",
    ]


def test_category_complement_hides_smallest_published_cell(report):
    from apps.admin_app.models import Category

    set_created(report, datetime(2026, 4, 1, tzinfo=TZ))
    categories = list(Category.objects.order_by("name", "pk"))
    for index, category in enumerate(categories):
        for _ in range(4 if index == 0 else 5):
            report.pk = None
            report.category = category
            report.save()
            set_created(report, datetime(2026, 10, 1, tzinfo=TZ))
    result = selectors.public_metrics(now=NOW)
    rows = result["current_month_by_category"]
    assert [row["received"] for row in rows] == [None, None] + [5] * (len(rows) - 2)
    assert rows[1]["suppressed"] == ["received"]
    assert result["trend"][-1]["received"] == result["totals"]["received"] == 49


@pytest.mark.parametrize("current_count", [5, 7])
def test_current_categories_follow_month_after_complement(report, current_count):
    set_created(report, datetime(2026, 5, 1, tzinfo=TZ))
    for month, count in [(6, 6), (7, 6), (8, 6), (9, 6), (10, current_count)]:
        for _ in range(count):
            report.pk = None
            report.save()
            set_created(report, datetime(2026, month, 1, tzinfo=TZ))
    before_trend, before_categories = selectors.public_breakdowns(now=NOW)
    assert before_trend[-1]["received"] == current_count
    assert any(row["received"] == current_count for row in before_categories)

    result = selectors.public_metrics(now=NOW)
    assert result["totals"]["received"] == 25 + current_count
    if current_count == 5:
        assert [point["received"] for point in result["trend"]] == [None, 6, 6, 6, 6, None]
        assert all(
            row["received"] is None and row["suppressed"] == ["received"]
            for row in result["current_month_by_category"]
        )
    else:
        assert result["trend"][-1]["received"] == current_count
        assert result["current_month_by_category"] == before_categories
