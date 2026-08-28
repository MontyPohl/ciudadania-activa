import hashlib
import uuid

import pytest
import requests
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test.client import BOUNDARY, MULTIPART_CONTENT, encode_multipart
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.auth_app.enums import UserRole, VerificationStatus
from apps.core.services import storage
from apps.reports_app.models import ReportMedia
from apps.reports_app.services import reports as report_service

pytestmark = pytest.mark.django_db

CREATE_URL = "/api/v1/reports/"


@pytest.fixture(autouse=True)
def isolated_throttles():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def verified_citizen(citizen):
    citizen.verification_status = VerificationStatus.APROBADO
    citizen.save(update_fields=["verification_status"])
    return citizen


def _authenticate(api, user):
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    return api


def _photo(jpeg_bytes, *, name="photo.jpg", content_type="image/jpeg"):
    return SimpleUploadedFile(name, jpeg_bytes, content_type=content_type)


def _valid_payload(category, jpeg_bytes):
    return {
        "category": str(category.id),
        "description": "  Texto <script>literal</script>\n",
        "photo": _photo(jpeg_bytes),
        "lat": "-25.263712345",
        "lng": "-57.635912345",
    }


def _post_repeated(api, data):
    body = encode_multipart(BOUNDARY, data)
    return api.generic("POST", CREATE_URL, body, content_type=MULTIPART_CONTENT)


def test_create_valido_devuelve_whitelist_exacta(
    api,
    verified_citizen,
    category,
    jpeg_bytes,
    storage_config,
    fake_storage,
    django_capture_on_commit_callbacks,
):
    with django_capture_on_commit_callbacks(execute=True):
        response = _authenticate(api, verified_citizen).post(
            CREATE_URL, _valid_payload(category, jpeg_bytes), format="multipart"
        )

    assert response.status_code == 201
    assert set(response.json()) == {"id", "status", "reward"}
    assert response.json()["status"] == "CREADO"
    assert response.json()["reward"] == {
        "impact_points": 20,
        "credits": 20,
        "status": "PENDIENTE",
    }
    assert len(fake_storage.uploads) == 1


@pytest.mark.parametrize("missing", ["category", "description", "photo", "lat", "lng"])
def test_create_rechaza_cada_campo_ausente(
    missing,
    api,
    verified_citizen,
    category,
    jpeg_bytes,
    storage_config,
    fake_storage,
):
    payload = _valid_payload(category, jpeg_bytes)
    payload.pop(missing)

    response = _authenticate(api, verified_citizen).post(CREATE_URL, payload, format="multipart")

    assert response.status_code == 400
    assert missing in response.json()["detail"]
    assert fake_storage.uploads == []


@pytest.mark.parametrize(
    "extra",
    [
        "extra",
        "citizen",
        "citizen_id",
        "status",
        "priority",
        "institution",
        "source",
        "reward",
        "file_hash",
        "location",
        "created_at",
    ],
)
def test_create_rechaza_campos_fuera_de_whitelist(
    extra,
    api,
    verified_citizen,
    category,
    jpeg_bytes,
    storage_config,
    fake_storage,
):
    payload = _valid_payload(category, jpeg_bytes)
    payload[extra] = "valor-no-permitido"

    response = _authenticate(api, verified_citizen).post(CREATE_URL, payload, format="multipart")

    assert response.status_code == 400
    assert response.json()["detail"][extra] == ["Este campo no está permitido."]
    assert fake_storage.uploads == []


@pytest.mark.parametrize("repeated", ["category", "description", "photo", "lat", "lng"])
def test_create_rechaza_campos_multipart_repetidos(
    repeated,
    api,
    verified_citizen,
    category,
    jpeg_bytes,
    storage_config,
    fake_storage,
):
    payload = _valid_payload(category, jpeg_bytes)
    if repeated == "photo":
        payload[repeated] = [_photo(jpeg_bytes), _photo(jpeg_bytes)]
    else:
        payload[repeated] = [payload[repeated], payload[repeated]]

    response = _post_repeated(_authenticate(api, verified_citizen), payload)

    assert response.status_code == 400
    assert response.json()["detail"][repeated] == ["Este campo debe aparecer exactamente una vez."]
    assert fake_storage.uploads == []


def test_create_reporta_a_la_vez_repetido_y_campo_de_servidor(
    api,
    verified_citizen,
    category,
    jpeg_bytes,
    storage_config,
    fake_storage,
):
    payload = _valid_payload(category, jpeg_bytes)
    payload["description"] = ["uno", "dos"]
    payload["source"] = "ADMIN"

    response = _post_repeated(_authenticate(api, verified_citizen), payload)

    assert response.status_code == 400
    assert response.json()["detail"] == {
        "description": ["Este campo debe aparecer exactamente una vez."],
        "source": ["Este campo no está permitido."],
    }
    assert fake_storage.uploads == []


@pytest.mark.parametrize(
    ("description", "lat", "lng", "field"),
    [
        (" \t\n", "-25", "-57", "description"),
        ("texto\x00", "-25", "-57", "description"),
        ("texto", "NaN", "-57", "lat"),
        ("texto", "Infinity", "-57", "lat"),
        ("texto", "-Infinity", "-57", "lat"),
        ("texto", "91", "-57", "lat"),
        ("texto", "-90.000001", "-57", "lat"),
        ("texto", "-25", "-181", "lng"),
        ("texto", "-25", "180.000001", "lng"),
    ],
)
def test_create_rechaza_texto_o_coordenadas_invalidas(
    description,
    lat,
    lng,
    field,
    api,
    verified_citizen,
    category,
    jpeg_bytes,
    storage_config,
    fake_storage,
):
    payload = _valid_payload(category, jpeg_bytes)
    payload.update(description=description, lat=lat, lng=lng)

    response = _authenticate(api, verified_citizen).post(CREATE_URL, payload, format="multipart")

    assert response.status_code == 400
    assert field in response.json()["detail"]
    assert fake_storage.uploads == []


def test_create_rechaza_categoria_inactiva(
    api,
    verified_citizen,
    category,
    jpeg_bytes,
    storage_config,
    fake_storage,
):
    category.is_active = False
    category.save(update_fields=["is_active"])

    response = _authenticate(api, verified_citizen).post(
        CREATE_URL, _valid_payload(category, jpeg_bytes), format="multipart"
    )

    assert response.status_code == 400
    assert "category" in response.json()["detail"]
    assert fake_storage.uploads == []


def test_create_rechaza_categoria_uuid_inexistente(
    api,
    verified_citizen,
    category,
    jpeg_bytes,
    storage_config,
    fake_storage,
):
    payload = _valid_payload(category, jpeg_bytes)
    payload["category"] = str(uuid.uuid4())

    response = _authenticate(api, verified_citizen).post(CREATE_URL, payload, format="multipart")

    assert response.status_code == 400
    assert "category" in response.json()["detail"]
    assert fake_storage.uploads == []


def test_create_rechaza_foto_invalida_como_error_de_campo(
    api,
    verified_citizen,
    category,
    storage_config,
    fake_storage,
):
    payload = _valid_payload(category, b"no es imagen")

    response = _authenticate(api, verified_citizen).post(CREATE_URL, payload, format="multipart")

    assert response.status_code == 400
    assert set(response.json()) == {"detail"}
    assert set(response.json()["detail"]) == {"photo"}
    assert fake_storage.uploads == []


def test_create_anonimo_401(api, category, jpeg_bytes):
    response = api.post(CREATE_URL, _valid_payload(category, jpeg_bytes), format="multipart")

    assert response.status_code == 401


@pytest.mark.parametrize(
    "user_kind",
    ["operator", "unverified"],
)
def test_create_no_autorizado_403(
    user_kind,
    api,
    user_factory,
    citizen,
    category,
    jpeg_bytes,
):
    user = (
        user_factory(email="operador-s6@example.com", role=UserRole.OPERATOR)
        if user_kind == "operator"
        else citizen
    )

    response = _authenticate(api, user).post(
        CREATE_URL, _valid_payload(category, jpeg_bytes), format="multipart"
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Se requiere una identidad ciudadana verificada."}


def test_create_foto_duplicada_409_sin_nuevo_upload(
    api,
    verified_citizen,
    report,
    category,
    jpeg_bytes,
    storage_config,
    fake_storage,
):
    ReportMedia.objects.create(
        report=report,
        file_url="reports/existente.jpg",
        file_hash=hashlib.sha256(jpeg_bytes).hexdigest(),
    )

    response = _authenticate(api, verified_citizen).post(
        CREATE_URL, _valid_payload(category, jpeg_bytes), format="multipart"
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "Esta fotografía ya fue utilizada en otro reporte."}
    assert fake_storage.uploads == []


def test_create_race_de_constraint_devuelve_409_y_limpia_object_perdedor(
    monkeypatch,
    api,
    verified_citizen,
    category,
    jpeg_bytes,
    storage_config,
    fake_storage,
):
    monkeypatch.setattr(report_service, "_photo_hash_exists", lambda original_hash: False)
    client = _authenticate(api, verified_citizen)

    first = client.post(CREATE_URL, _valid_payload(category, jpeg_bytes), format="multipart")
    second = client.post(CREATE_URL, _valid_payload(category, jpeg_bytes), format="multipart")

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json() == {"detail": "Esta fotografía ya fue utilizada en otro reporte."}
    assert len(fake_storage.uploads) == 2
    assert fake_storage.deletes == [{"bucket": "reportes", "key": fake_storage.uploads[1]["key"]}]


@pytest.mark.parametrize("failure", ["configuration", "unavailable"])
def test_create_storage_503_generico(
    failure,
    api,
    verified_citizen,
    category,
    jpeg_bytes,
    storage_config,
    fake_storage,
    settings,
):
    if failure == "configuration":
        settings.SUPABASE_STORAGE_BUCKET_REPORTS = ""
    else:
        fake_storage.upload_error = storage.StorageUnavailableError("detalle privado")

    response = _authenticate(api, verified_citizen).post(
        CREATE_URL, _valid_payload(category, jpeg_bytes), format="multipart"
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "No se pudo guardar el reporte en este momento. Intentá más tarde."
    }
    assert "detalle privado" not in response.content.decode()


@pytest.mark.parametrize("external_failure", ["timeout", "http"])
def test_create_upload_externo_falla_como_503_generico(
    external_failure,
    monkeypatch,
    api,
    verified_citizen,
    category,
    jpeg_bytes,
    storage_config,
):
    class FailedResponse:
        ok = False
        status_code = 500

        def close(self):
            pass

    def fail_request(*args, **kwargs):
        if external_failure == "timeout":
            raise requests.Timeout("detalle privado del timeout")
        return FailedResponse()

    monkeypatch.setattr(storage.requests, "request", fail_request)

    response = _authenticate(api, verified_citizen).post(
        CREATE_URL, _valid_payload(category, jpeg_bytes), format="multipart"
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "No se pudo guardar el reporte en este momento. Intentá más tarde."
    }
    assert "privado" not in response.content.decode()


def test_create_fallo_db_no_se_disfraza_de_503(
    monkeypatch,
    api,
    verified_citizen,
    category,
    jpeg_bytes,
):
    monkeypatch.setattr(
        "apps.reports_app.views.create_report",
        lambda **kwargs: (_ for _ in ()).throw(IntegrityError("fallo DB")),
    )
    api.raise_request_exception = False

    response = _authenticate(api, verified_citizen).post(
        CREATE_URL, _valid_payload(category, jpeg_bytes), format="multipart"
    )

    assert response.status_code == 500


def test_create_throttle_es_por_usuario_y_scope(
    api,
    verified_citizen,
    category,
    jpeg_bytes,
    storage_config,
    fake_storage,
    settings,
    monkeypatch,
):
    monkeypatch.setitem(
        settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"], "report_create", "1/hour"
    )
    client = _authenticate(api, verified_citizen)

    first = client.post(CREATE_URL, _valid_payload(category, jpeg_bytes), format="multipart")
    second = client.post(CREATE_URL, _valid_payload(category, jpeg_bytes), format="multipart")

    assert first.status_code == 201
    assert second.status_code == 429
    assert len(fake_storage.uploads) == 1
