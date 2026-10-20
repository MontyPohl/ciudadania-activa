"""Carga ficticia de X y reinterpretación de S-10 sin incentivos para la fuente."""

import copy
import json
from io import StringIO
from unittest.mock import Mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.admin_app.models import AuditLog
from apps.ai_client_app.models import AiClassification
from apps.auth_app.enums import VerificationStatus
from apps.core.enums import ActorType, ReportSource, ReportStatus
from apps.notifications_app.models import Notification
from apps.reports_app.models import Report, ReportStatusHistory
from apps.reports_app.services import confirmations, validation
from apps.rewards_app.enums import RewardEventType, RewardTxStatus
from apps.rewards_app.models import RewardTransaction
from apps.social_ingestion_app.models import XMockMessage
from apps.social_ingestion_app.services.x_mock import DATASET_NOTICE, ingest, parse_dataset

REAL_DATASET = "apps/social_ingestion_app/data/x_mock_dataset_v1.json"


def _dataset():
    return {
        "dataset": "x_mock_v1",
        "notice": DATASET_NOTICE,
        "items": [
            {
                "source_handle": f"@ficticio_prueba{index:02d}",
                "simulated_at": f"2026-08-25T0{index}:00:00-03:00",
                "raw_text": "Hay un bache en la calzada pública.",
                "category": "baches",
                "latitude": -25.27465,
                "longitude": -57.63235,
            }
            for index in range(3)
        ],
    }


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("category", "categoria-inexistente"),
        ("source_handle", "@cuenta_no_ficticia"),
        ("latitude", -26),
        ("simulated_at", "duplicado"),
    ],
)
def test_carga_atomica_e_idempotente(
    tmp_path, monkeypatch, django_capture_on_commit_callbacks, field, value
):
    classify = Mock()
    notify = Mock()
    post = Mock()
    invalidate = Mock()
    monkeypatch.setattr("apps.ai_client_app.tasks.classify_report.delay", classify)
    monkeypatch.setattr("apps.notifications_app.tasks.send_notification.delay", notify)
    monkeypatch.setattr("requests.post", post)
    monkeypatch.setattr(
        "apps.social_ingestion_app.services.x_mock.invalidate_public_reports_cache", invalidate
    )
    path = tmp_path / "dataset.json"
    data = _dataset()
    path.write_text(json.dumps(data), encoding="utf-8")

    # Se prueba la ruta relativa a backend/ aun desde otro directorio de trabajo.
    monkeypatch.chdir(tmp_path)
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        output = StringIO()
        call_command("load_x_mock_dataset", REAL_DATASET, "--dry-run", stdout=output)
        assert "creados=30 omitidos=0 total=30" in output.getvalue()
        call_command("load_x_mock_dataset", str(path), "--dry-run", stdout=StringIO())
    assert not callbacks
    for model in (Report, XMockMessage, ReportStatusHistory, AuditLog):
        assert model.objects.count() == 0

    invalid = copy.deepcopy(data)
    if value == "duplicado":
        invalid["items"][2]["source_handle"] = invalid["items"][0]["source_handle"]
        invalid["items"][2][field] = invalid["items"][0][field]
    else:
        invalid["items"][2][field] = value
    path.write_text(json.dumps(invalid), encoding="utf-8")
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with pytest.raises(CommandError) as error:
            call_command("load_x_mock_dataset", str(path), stdout=StringIO())
    assert "índice=2" in str(error.value)
    assert f"campo={field}" in str(error.value)
    assert data["items"][2]["raw_text"] not in str(error.value)
    assert not callbacks
    for model in (Report, XMockMessage, ReportStatusHistory, AuditLog):
        assert model.objects.count() == 0

    path.write_text(json.dumps(data), encoding="utf-8")
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        output = StringIO()
        call_command("load_x_mock_dataset", str(path), stdout=output)
    assert "creados=3 omitidos=0 total=3" in output.getvalue()
    assert len(callbacks) == 1
    invalidate.assert_called_once_with()
    assert Report.objects.count() == XMockMessage.objects.count() == 3
    assert ReportStatusHistory.objects.count() == 3
    assert AuditLog.objects.count() == 3
    for item in parse_dataset(data):
        message = XMockMessage.objects.select_related("report").get(
            source_handle=item.source_handle, simulated_at=item.simulated_at
        )
        report = message.report
        assert message.raw_text == item.raw_text
        assert report.source == ReportSource.X_MOCK
        assert report.citizen_id is None
        assert report.institution_id is None
        assert report.status == ReportStatus.CREADO
        assert report.category == item.category
        assert report.priority == item.category.default_priority
        assert report.location.coords == (item.longitude, item.latitude)
        assert report.location.srid == 4326
        assert report.description == (
            f"[Publicación simulada de X · {item.source_handle}] {item.raw_text}"
        )
        history = report.status_history.get()
        assert history.previous_status == ""
        assert history.new_status == ReportStatus.CREADO
        assert history.changed_by_id is None
        entry = AuditLog.objects.get(payload__report_id=str(report.pk))
        assert entry.action == "report.ingested"
        assert entry.actor_type == ActorType.SYSTEM
        assert entry.actor_user_id is None
        assert entry.payload == {
            "report_id": str(report.pk),
            "x_mock_message_id": str(message.pk),
            "category_id": str(item.category.pk),
            "source": "x_mock",
        }

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        output = StringIO()
        call_command("load_x_mock_dataset", str(path), stdout=output)
    assert "creados=0 omitidos=3 total=3" in output.getvalue()
    assert not callbacks
    invalidate.assert_called_once_with()
    for model in (Report, XMockMessage, ReportStatusHistory, AuditLog):
        assert model.objects.count() == 3
    for model in (RewardTransaction, AiClassification, Notification):
        assert model.objects.count() == 0
    classify.assert_not_called()
    notify.assert_not_called()
    post.assert_not_called()


@pytest.mark.django_db
def test_s10_reporte_x_validado_acredita_al_confirmador_y_no_a_la_fuente(
    other_citizen, operator_user, category, institution, django_capture_on_commit_callbacks
):
    other_citizen.verification_status = VerificationStatus.APROBADO
    other_citizen.save(update_fields=["verification_status"])
    assert category.default_institution == institution
    data = _dataset()
    data["items"] = data["items"][:1]
    with django_capture_on_commit_callbacks(execute=True):
        result = ingest(parse_dataset(data))
    assert result.created == 1
    report = XMockMessage.objects.get().report
    confirmation = confirmations.confirm_report(report_id=report.pk, citizen=other_citizen)
    assert confirmation.reward.status == RewardTxStatus.PENDIENTE
    with django_capture_on_commit_callbacks(execute=True):
        validation.validate_report(
            report_id=report.pk, operator=operator_user, institution=institution
        )
    reward = RewardTransaction.objects.get()
    assert reward.confirmation == confirmation.confirmation
    assert reward.user == other_citizen
    assert reward.institution == institution
    assert reward.event_type == RewardEventType.CONFIRMATION_CREATED
    assert reward.status == RewardTxStatus.ACREDITADO
    assert reward.impact_delta == reward.credit_delta == 10
    assert not RewardTransaction.objects.filter(
        event_type__in=[RewardEventType.REPORT_CREATED, RewardEventType.VALIDATION_BONUS]
    ).exists()
    assert not Notification.objects.exists()
