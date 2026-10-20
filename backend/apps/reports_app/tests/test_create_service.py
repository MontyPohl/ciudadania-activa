import hashlib
import logging
import math
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connections

from apps.admin_app.models import AuditLog, Category, Institution
from apps.auth_app.enums import VerificationStatus
from apps.core.enums import ActorType, ReportSource, ReportStatus
from apps.core.services import storage
from apps.notifications_app.enums import NotificationType
from apps.notifications_app.models import Notification
from apps.reports_app.models import Report, ReportMedia, ReportStatusHistory
from apps.reports_app.services import reports as report_service
from apps.rewards_app.models import RewardTransaction

pytestmark = pytest.mark.django_db


@pytest.fixture
def verified_citizen(citizen):
    citizen.verification_status = VerificationStatus.APROBADO
    citizen.save(update_fields=["verification_status"])
    return citizen


@pytest.fixture
def photo(jpeg_bytes):
    def make(content=None):
        return SimpleUploadedFile(
            "photo.jpg",
            jpeg_bytes if content is None else content,
            content_type="image/jpeg",
        )

    return make


@pytest.fixture
def report_environment(storage_config, fake_storage):
    return fake_storage


def _create(verified_citizen, category, photo, **overrides):
    values = {
        "citizen": verified_citizen,
        "category": category,
        "description": "  <script>alert(1)</script> como texto\n",
        "photo": photo(),
        "latitude": -25.263712345,
        "longitude": -57.635912345,
    }
    values.update(overrides)
    return report_service.create_report(**values)


def test_create_report_persiste_el_agregado_exacto(
    verified_citizen,
    category,
    photo,
    report_environment,
    django_capture_on_commit_callbacks,
):
    with django_capture_on_commit_callbacks(execute=True):
        result = _create(verified_citizen, category, photo)

    report = result.report
    report.refresh_from_db()
    assert report.citizen == verified_citizen
    assert report.category == category
    assert report.institution is None
    assert report.description == "  <script>alert(1)</script> como texto\n"
    assert report.status == ReportStatus.CREADO
    assert report.source == ReportSource.PLATFORM
    assert report.priority == category.default_priority
    assert report.location.y == pytest.approx(-25.263712345)
    assert report.location.x == pytest.approx(-57.635912345)
    assert report.location.srid == 4326

    media = ReportMedia.objects.get(report=report)
    assert media.uploaded_by == verified_citizen
    assert media.file_url == report_environment.uploads[0]["key"]
    assert media.file_url.startswith(f"reports/{report.id}/")
    assert media.file_url.endswith(".jpg")
    assert media.file_hash == hashlib.sha256(photo().read()).hexdigest()
    assert report_environment.uploads[0]["bucket"] == "reportes"
    assert report_environment.uploads[0]["content_type"] == "image/jpeg"
    assert report_environment.deletes == []

    history = ReportStatusHistory.objects.get(report=report)
    assert history.previous_status == ""
    assert history.new_status == ReportStatus.CREADO
    assert history.changed_by == verified_citizen

    reward = result.reward
    assert RewardTransaction.objects.filter(report=report).count() == 1
    assert reward.user == verified_citizen
    assert (reward.impact_delta, reward.credit_delta, reward.status) == (20, 20, "PENDIENTE")

    audit = AuditLog.objects.get(action="report.created")
    assert audit.actor_user == verified_citizen
    assert audit.actor_type == ActorType.HUMAN
    assert audit.payload == {
        "report_id": str(report.id),
        "category_id": str(category.id),
        "status": ReportStatus.CREADO,
    }


@pytest.mark.parametrize("failure_point", ["report", "media", "history", "ledger", "audit"])
def test_fallo_db_revierte_todo_y_compensa_storage(
    failure_point,
    monkeypatch,
    verified_citizen,
    category,
    photo,
    report_environment,
):
    def fail(*args, **kwargs):
        raise RuntimeError(f"fallo-{failure_point}")

    targets = {
        "report": (Report.objects, "create"),
        "media": (ReportMedia.objects, "create"),
        "history": (ReportStatusHistory.objects, "create"),
        "ledger": (report_service.ledger, "on_report_created"),
        "audit": (report_service.audit, "log"),
    }
    monkeypatch.setattr(*targets[failure_point], fail)

    with pytest.raises(RuntimeError, match=f"fallo-{failure_point}"):
        _create(verified_citizen, category, photo)

    assert Report.objects.count() == 0
    assert ReportMedia.objects.count() == 0
    assert ReportStatusHistory.objects.count() == 0
    assert RewardTransaction.objects.count() == 0
    assert AuditLog.objects.count() == 0
    assert len(report_environment.uploads) == 1
    assert report_environment.deletes == [
        {
            "bucket": "reportes",
            "key": report_environment.uploads[0]["key"],
        }
    ]


def test_fallo_de_delete_no_tapa_error_db(
    monkeypatch,
    caplog,
    verified_citizen,
    category,
    photo,
    report_environment,
):
    report_environment.delete_error = storage.StorageUnavailableError("fallo delete")

    def fail(*args, **kwargs):
        raise RuntimeError("fallo original")

    monkeypatch.setattr(report_service.audit, "log", fail)

    with caplog.at_level(logging.WARNING), pytest.raises(RuntimeError, match="fallo original"):
        _create(verified_citizen, category, photo)

    assert len(report_environment.deletes) == 1
    assert report_environment.uploads[0]["key"] not in caplog.text
    assert "reports/…" in caplog.text


def test_fallo_de_upload_no_crea_filas_ni_intenta_delete(
    verified_citizen,
    category,
    photo,
    report_environment,
):
    report_environment.upload_error = storage.StorageUnavailableError("fallo upload")

    with pytest.raises(storage.StorageUnavailableError):
        _create(verified_citizen, category, photo)

    assert Report.objects.count() == 0
    assert report_environment.uploads == []
    assert report_environment.deletes == []


def test_precheck_de_hash_rechaza_antes_del_upload(
    report,
    verified_citizen,
    category,
    photo,
    jpeg_bytes,
    report_environment,
):
    ReportMedia.objects.create(
        report=report,
        file_url="reports/existente.jpg",
        file_hash=hashlib.sha256(jpeg_bytes).hexdigest(),
    )

    with pytest.raises(report_service.DuplicatePhotoError):
        _create(verified_citizen, category, photo)

    assert report_environment.uploads == []


def test_race_simulada_la_decide_constraint_y_limpia_perdedor(
    monkeypatch,
    verified_citizen,
    category,
    photo,
    report_environment,
):
    monkeypatch.setattr(report_service, "_photo_hash_exists", lambda original_hash: False)

    first = _create(verified_citizen, category, photo)
    with pytest.raises(report_service.DuplicatePhotoError):
        _create(verified_citizen, category, photo)

    assert Report.objects.count() == 1
    assert ReportMedia.objects.count() == 1
    assert RewardTransaction.objects.count() == 1
    assert first.report.pk == Report.objects.get().pk
    assert len(report_environment.uploads) == 2
    assert report_environment.deletes == [
        {"bucket": "reportes", "key": report_environment.uploads[1]["key"]}
    ]


def test_integrity_error_de_otro_constraint_no_se_convierte_en_duplicado(
    monkeypatch,
    verified_citizen,
    category,
    photo,
    report_environment,
):
    class OtherConstraintError(Exception):
        diag = SimpleNamespace(constraint_name="otro_constraint")

    def fail(*args, **kwargs):
        raise IntegrityError("otro") from OtherConstraintError()

    monkeypatch.setattr(report_service.ledger, "on_report_created", fail)

    with pytest.raises(IntegrityError):
        _create(verified_citizen, category, photo)

    assert len(report_environment.deletes) == 1


def test_constraint_real_del_reward_no_se_convierte_en_duplicado_de_foto(
    monkeypatch,
    verified_citizen,
    category,
    photo,
    report_environment,
):
    original = report_service.ledger.on_report_created

    def create_twice(report):
        original(report)
        return original(report)

    monkeypatch.setattr(report_service.ledger, "on_report_created", create_twice)

    with pytest.raises(IntegrityError) as excinfo:
        _create(verified_citizen, category, photo)

    assert excinfo.value.__cause__.diag.constraint_name == "reward_tx_report_created_unico"
    assert Report.objects.count() == 0
    assert RewardTransaction.objects.count() == 0
    assert len(report_environment.deletes) == 1


def test_callbacks_se_registran_y_no_corren_antes_del_commit(
    monkeypatch,
    verified_citizen,
    category,
    photo,
    report_environment,
    django_capture_on_commit_callbacks,
):
    effects = []
    monkeypatch.setattr(
        report_service, "invalidate_public_reports_cache", lambda: effects.append("cache")
    )
    monkeypatch.setattr(
        report_service.ai_tasks.classify_report,
        "delay",
        lambda report_id: effects.append(("ai", report_id)),
    )

    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        result = _create(verified_citizen, category, photo)
        assert effects == []

    # Caché, IA y, desde S13, el encolado del aviso REPORTE_CREADO (que corre en modo
    # eager contra el Brevo doblado y no altera los efectos observados).
    assert len(callbacks) == 3
    for callback in callbacks:
        callback()
    assert effects == ["cache", ("ai", str(result.report.id))]
    aviso = Notification.objects.get(notification_type=NotificationType.REPORTE_CREADO)
    assert aviso.user == verified_citizen
    assert aviso.report == result.report
    assert aviso.institution is None
    assert aviso.dedup_key == f"REPORTE_CREADO:{result.report.id}"


@pytest.mark.parametrize("effect", ["cache", "broker"])
def test_fallo_post_commit_no_revierte_reporte(
    effect,
    monkeypatch,
    verified_citizen,
    category,
    photo,
    report_environment,
    django_capture_on_commit_callbacks,
):
    effects = []

    def fail(*args, **kwargs):
        raise RuntimeError("side effect caído")

    if effect == "cache":
        monkeypatch.setattr(report_service, "invalidate_public_reports_cache", fail)
        monkeypatch.setattr(
            report_service.ai_tasks.classify_report,
            "delay",
            lambda report_id: effects.append(("ai", report_id)),
        )
    else:
        monkeypatch.setattr(
            report_service, "invalidate_public_reports_cache", lambda: effects.append("cache")
        )
        monkeypatch.setattr(report_service.ai_tasks.classify_report, "delay", fail)

    with django_capture_on_commit_callbacks(execute=True):
        result = _create(verified_citizen, category, photo)

    assert Report.objects.filter(pk=result.report.pk).exists()
    assert RewardTransaction.objects.filter(report=result.report).count() == 1
    assert report_environment.deletes == []
    expected = [("ai", str(result.report.id))] if effect == "cache" else ["cache"]
    assert effects == expected


def test_rollback_no_ejecuta_callbacks(
    monkeypatch,
    verified_citizen,
    category,
    photo,
    report_environment,
    django_capture_on_commit_callbacks,
):
    effects = []
    monkeypatch.setattr(
        report_service, "invalidate_public_reports_cache", lambda: effects.append("cache")
    )
    monkeypatch.setattr(
        report_service.ai_tasks.classify_report,
        "delay",
        lambda report_id: effects.append(("ai", report_id)),
    )
    monkeypatch.setattr(
        report_service.audit,
        "log",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("rollback")),
    )

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with pytest.raises(RuntimeError, match="rollback"):
            _create(verified_citizen, category, photo)

    assert callbacks == []
    assert effects == []
    assert len(report_environment.deletes) == 1


def test_validaciones_de_dominio_no_dependenden_del_serializer(
    verified_citizen,
    category,
    photo,
    report_environment,
):
    with pytest.raises(report_service.InvalidReportDataError):
        _create(verified_citizen, category, photo, description=" \t\n ")
    with pytest.raises(report_service.InvalidReportDataError):
        _create(verified_citizen, category, photo, description="texto\x00")
    with pytest.raises(report_service.InvalidReportDataError):
        _create(verified_citizen, category, photo, description="texto\x01")
    with pytest.raises(report_service.InvalidReportDataError):
        _create(verified_citizen, category, photo, latitude=float("nan"))
    with pytest.raises(report_service.InvalidReportDataError):
        _create(verified_citizen, category, photo, longitude=181)
    assert report_environment.uploads == []


@pytest.mark.parametrize(
    "description",
    [
        "Texto normal",
        "  Texto con espacio alrededor  ",
        "línea uno\nlínea dos",
        "tab\tpermitido",
        "retorno\rpermitido",
        "<script>alert(1)</script>",
    ],
)
def test_descripcion_valida_se_conserva_byte_por_byte(description):
    assert report_service.validate_report_description(description) == description


@pytest.mark.parametrize("description", ["   ", "\n\t"])
def test_descripcion_solo_whitespace_se_rechaza(description):
    with pytest.raises(report_service.InvalidReportDataError):
        report_service.validate_report_description(description)


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [
        (-90.0, -180.0),
        (90.0, 180.0),
        (-0.0, -0.0),
    ],
)
def test_coordenadas_limite_se_persisten_sin_redondeo(
    latitude,
    longitude,
    verified_citizen,
    category,
    photo,
    report_environment,
):
    result = _create(
        verified_citizen,
        category,
        photo,
        latitude=latitude,
        longitude=longitude,
    )

    result.report.refresh_from_db()
    assert result.report.location.y == latitude
    assert result.report.location.x == longitude
    if latitude == longitude == 0:
        assert math.copysign(1, result.report.location.y) == math.copysign(1, latitude)
        assert math.copysign(1, result.report.location.x) == math.copysign(1, longitude)


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [
        (-90.000001, 0),
        (90.000001, 0),
        (0, -180.000001),
        (0, 180.000001),
        (float("nan"), 0),
        (float("inf"), 0),
        (float("-inf"), 0),
    ],
)
def test_coordenadas_fuera_de_contrato_se_rechazan(latitude, longitude):
    with pytest.raises(report_service.InvalidReportDataError):
        report_service.validate_report_coordinates(latitude, longitude)


def test_categoria_inactiva_se_revalida_en_servicio(
    verified_citizen,
    category,
    photo,
    report_environment,
):
    category.is_active = False
    category.save(update_fields=["is_active"])

    with pytest.raises(report_service.InvalidReportDataError):
        _create(verified_citizen, category, photo)

    assert report_environment.uploads == []


@pytest.mark.django_db(transaction=True)
def test_dos_transacciones_reales_con_mismo_hash_dejan_un_solo_agregado(
    monkeypatch,
    verified_citizen,
    photo,
    report_environment,
):
    institution = Institution.objects.create(name="Institución concurrente", slug="concurrente")
    category = Category.objects.create(
        name="Categoría concurrente",
        slug="categoria-concurrente",
        default_institution=institution,
    )
    barrier = threading.Barrier(2)
    connection_ids = set()
    connection_lock = threading.Lock()

    def synchronized_precheck(original_hash):
        with connection_lock:
            connection_ids.add(id(connections["default"].connection))
        barrier.wait(timeout=10)
        return False

    monkeypatch.setattr(report_service, "_photo_hash_exists", synchronized_precheck)
    monkeypatch.setattr(report_service, "_invalidate_public_cache", lambda report_id: None)
    monkeypatch.setattr(report_service, "_enqueue_ai_classification", lambda report_id: None)

    def attempt(_):
        try:
            return _create(verified_citizen, category, photo)
        except report_service.DuplicatePhotoError as exc:
            return exc
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(attempt, range(2)))

    assert (
        sum(isinstance(outcome, report_service.ReportCreationResult) for outcome in outcomes) == 1
    )
    assert sum(isinstance(outcome, report_service.DuplicatePhotoError) for outcome in outcomes) == 1
    assert Report.objects.count() == 1
    assert ReportMedia.objects.count() == 1
    assert RewardTransaction.objects.count() == 1
    assert len(report_environment.uploads) == 2
    assert len(report_environment.deletes) == 1
    assert len(connection_ids) == 2
