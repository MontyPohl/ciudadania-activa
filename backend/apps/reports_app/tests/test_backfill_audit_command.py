import json

import pytest
from django.contrib.gis.geos import Point
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.admin_app.models import AuditLog
from apps.core.enums import ActorType, ReportStatus
from apps.reports_app.models import Report, ReportStatusHistory

pytestmark = pytest.mark.django_db


def test_audit_exige_confirmacion_de_todos_los_writers():
    with pytest.raises(CommandError, match="scheduled writers"):
        call_command(
            "audit_resolution_cycle_backfill",
            web_writers_off=True,
            worker_writers_off=True,
        )


def test_audit_identifica_el_universo_aprobado(
    category,
    capsys,
):
    call_command("seed_reports", verbosity=0)
    capsys.readouterr()
    for index in range(2):
        report = Report.objects.create(
            category=category,
            description=f"Reporte histórico real {index}",
            status=ReportStatus.CREADO,
            location=Point(-57.63 + index / 100, -25.26, srid=4326),
        )
        ReportStatusHistory.objects.create(report=report, new_status=ReportStatus.CREADO)
        AuditLog.objects.create(
            actor_type=ActorType.HUMAN,
            action="report.created",
            payload={"report_id": str(report.id)},
        )
        # Uno de los dos reportes lleva además una confirmación ciudadana, que es el
        # caso que traía el dataset real de producción y que las fixtures no cubrían.
        # Una confirmación no abre ciclo, de modo que el reporte sigue en CYCLE_0.
        if index == 0:
            AuditLog.objects.create(
                actor_type=ActorType.HUMAN,
                action="report.confirmed",
                payload={"report_id": str(report.id)},
            )

    call_command(
        "audit_resolution_cycle_backfill",
        web_writers_off=True,
        worker_writers_off=True,
        scheduled_writers_off=True,
    )

    output = capsys.readouterr().out
    payload = json.loads(output[: output.index("}\n") + 1])
    assert payload["total_reports"] == 16
    assert payload["synthetic"] == 14
    assert payload["cycle_0"] == 2
    assert payload["cycle_1"] == 0
    assert payload["unknown"] == payload["inconsistent"] == 0
    assert "READONLY_AUDIT_COMPLETE" in output
