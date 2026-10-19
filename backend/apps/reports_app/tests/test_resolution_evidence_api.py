import time
import uuid
from urllib.parse import urlsplit

import pytest
from django.core import signing
from django.urls import Resolver404, resolve
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.enums import ReportStatus
from apps.core.services import storage
from apps.reports_app.models import ResolutionEvidence
from apps.reports_app.services import evidence_access

pytestmark = pytest.mark.django_db


def _client(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


def _in_process(report, operator_user, institution):
    report.status = ReportStatus.ASIGNADO
    report.institution = institution
    report.validated_at = timezone.now()
    report.validated_by = operator_user
    report.resolution_cycle = 1
    report.save(
        update_fields=[
            "status",
            "institution",
            "validated_at",
            "validated_by",
            "resolution_cycle",
            "updated_at",
        ]
    )
    report.status = ReportStatus.EN_PROCESO
    report.save(update_fields=["status", "updated_at"])


def _operator_url(report, institution):
    return f"/api/v1/operator/reports/{report.pk}/evidence/?institution_id={institution.pk}"


def _evidence(report, operator_user):
    return ResolutionEvidence.objects.create(
        report=report,
        description="Trabajo terminado.",
        evidence_url=f"resolution-evidence/{report.pk}/1/{uuid.uuid4()}.jpg",
        uploaded_by=operator_user,
        resolution_cycle=1,
        idempotency_key=uuid.uuid4(),
        content_hash="a" * 64,
    )


def test_operator_post_creates_private_evidence_contract(
    report,
    operator_user,
    institution,
    storage_config,
    fake_storage,
    selfie_factory,
    jpeg_bytes,
):
    _in_process(report, operator_user, institution)

    response = _client(operator_user).post(
        _operator_url(report, institution),
        {
            "file": selfie_factory(jpeg_bytes, name="evidence.jpg"),
            "description": "Trabajo terminado.",
            "cycle": 1,
        },
        format="multipart",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )

    assert response.status_code == 201
    assert response["Cache-Control"] == "private, no-store"
    assert set(response.json()) == {
        "id",
        "cycle",
        "description",
        "created_at",
        "signed_url",
        "expires_in",
    }
    assert response.json()["cycle"] == 1
    assert response.json()["signed_url"].startswith(
        "http://testserver/api/v1/reports/evidence-access/"
    )
    assert str(report.pk) not in response.json()["signed_url"]
    assert "resolution-evidence" not in response.json()["signed_url"]
    assert response.json()["expires_in"] == 300
    assert "evidence_url" not in response.json()
    assert "content_hash" not in response.json()


def test_operator_post_requires_uuid_idempotency_key(
    report,
    operator_user,
    institution,
    selfie_factory,
    jpeg_bytes,
):
    _in_process(report, operator_user, institution)

    response = _client(operator_user).post(
        _operator_url(report, institution),
        {"file": selfie_factory(jpeg_bytes), "description": "Lista.", "cycle": 1},
        format="multipart",
    )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_REQUEST"
    assert response["Cache-Control"] == "private, no-store"


def test_operator_cross_tenant_is_indistinguishable_from_missing(
    report,
    operator_user,
    institution,
    storage_config,
    selfie_factory,
    jpeg_bytes,
):
    from apps.admin_app.models import Institution

    other = Institution.objects.get(slug="ande")
    _in_process(report, operator_user, other)

    response = _client(operator_user).post(
        _operator_url(report, institution),
        {"file": selfie_factory(jpeg_bytes), "description": "Lista.", "cycle": 1},
        format="multipart",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )

    assert response.status_code == 404
    assert response.json()["code"] == "REPORT_NOT_FOUND"


class _SignedUrl(str):
    """Una signed_url que un fallo nunca muestra entera.

    pytest imprime el repr de los argumentos de cada frame y de los operandos de una aserción;
    éste omite el token. No se compara con `==`: el diff de texto de pytest lo mostraría.
    """

    def __repr__(self):
        return "<signed_url omitida>"


def _signed_urls(response):
    return [_SignedUrl(item["signed_url"]) for item in response.json()]


def _without_signed_urls(payload):
    """La evidencia sin su URL efímera: el token lleva el instante de emisión (TTL 300 s)."""
    return [{key: value for key, value in item.items() if key != "signed_url"} for item in payload]


def _evidence_id_of(signed_url):
    """La evidencia que abre una URL firmada, validada como la descarga: firma, salt y TTL.

    Un fallo no muestra el token: `signed_url` llega como _SignedUrl, ninguna variable local
    lo guarda en claro (tampoco con --showlocals), las aserciones sólo ven booleanos y los
    errores de la validación se sustituyen por un código constante lanzado fuera del `except`,
    para que no quede encadenada la causa original (sus args llevan la ruta o la firma).
    """
    parts = urlsplit(signed_url)
    on_download_route = (parts.scheme, parts.netloc) == ("http", "testserver") and (
        parts.path.startswith("/api/v1/reports/evidence-access/")
    )
    path = _SignedUrl(parts.path)
    del parts  # su repr incluye la ruta con el token
    assert on_download_route, "signed-url-route-mismatch"
    try:
        match = resolve(path)
    except Resolver404:
        match = None
    assert match is not None, "signed-url-unresolvable"
    on_download_view = match.url_name == "resolution-evidence-download"
    token = _SignedUrl(match.kwargs["token"])
    del match  # su repr incluye el token
    assert on_download_view, "signed-url-route-mismatch"
    try:
        return evidence_access.evidence_id_from_token(token)
    except signing.SignatureExpired:
        failure = signing.SignatureExpired("signed-url-expired")
    except signing.BadSignature:
        failure = signing.BadSignature("signed-url-invalid")
    except Exception as exc:  # cualquier otro error, sin el token de los frames de Django
        failure = AssertionError(f"unexpected-signed-url-validation-error: {type(exc).__name__}")
    raise failure


def _list_as_author_other_and_admin(report, citizen, other_citizen, admin_user):
    return (
        _client(citizen).get(f"/api/v1/reports/{report.pk}/evidence/"),
        _client(other_citizen).get(f"/api/v1/reports/{report.pk}/evidence/"),
        _client(admin_user).get(f"/api/v1/admin/reports/{report.pk}/evidence/"),
    )


def _assert_same_evidence_for_author_and_admin(evidence, author_response, admin_response):
    # Las mismas evidencias; cada URL firmada es válida y abre esa evidencia, aunque sus
    # tokens difieran por haberse emitido en segundos distintos.
    author_stable = _without_signed_urls(author_response.json())
    admin_stable = _without_signed_urls(admin_response.json())
    expected = str(evidence.pk)
    assert author_stable == admin_stable, "stable-evidence-payload-mismatch"
    assert [item["id"] for item in author_stable] == [expected], "evidence-id-mismatch"
    for signed_url in (*_signed_urls(author_response), *_signed_urls(admin_response)):
        assert _evidence_id_of(signed_url) == expected, "signed-url-resource-mismatch"


def test_author_and_admin_can_list_but_other_citizen_cannot(
    report,
    operator_user,
    institution,
    citizen,
    other_citizen,
    admin_user,
    storage_config,
):
    _in_process(report, operator_user, institution)
    evidence = _evidence(report, operator_user)

    author_response, other_response, admin_response = _list_as_author_other_and_admin(
        report, citizen, other_citizen, admin_user
    )

    assert author_response.status_code == admin_response.status_code == 200
    _assert_same_evidence_for_author_and_admin(evidence, author_response, admin_response)
    assert other_response.status_code == 404
    for response in (author_response, other_response, admin_response):
        assert response["Cache-Control"] == "private, no-store"


def test_author_and_admin_signed_urls_from_different_seconds_are_equivalent(
    report,
    operator_user,
    institution,
    citizen,
    other_citizen,
    admin_user,
    storage_config,
    monkeypatch,
):
    # Regresión: cada firma en su propio segundo, sin dormir. Los tokens difieren (la igualdad
    # estricta anterior fallaba aquí) y ambos siguen abriendo la misma evidencia.
    _in_process(report, operator_user, institution)
    evidence = _evidence(report, operator_user)
    seconds = iter(range(int(time.time()) - 10, int(time.time())))
    monkeypatch.setattr(
        signing.TimestampSigner, "timestamp", lambda self: signing.b62_encode(next(seconds))
    )

    author_response, other_response, admin_response = _list_as_author_other_and_admin(
        report, citizen, other_citizen, admin_user
    )

    assert author_response.status_code == admin_response.status_code == 200
    author_url, admin_url = _signed_urls(author_response)[0], _signed_urls(admin_response)[0]
    urls_differ = author_url != admin_url
    old_equality_holds = author_response.json() == admin_response.json()
    assert urls_differ, "signed-urls-not-distinct: el segundo forzado no se aplicó"
    assert not old_equality_holds, "old-strict-equality-would-not-fail"
    _assert_same_evidence_for_author_and_admin(evidence, author_response, admin_response)
    assert other_response.status_code == 404


def test_a_signed_url_for_another_evidence_is_not_this_evidence(
    report, operator_user, institution, citizen, storage_config
):
    # La validación por URL no acepta la de otro objeto ni una firma alterada.
    _in_process(report, operator_user, institution)
    first, second = _evidence(report, operator_user), _evidence(report, operator_user)
    response = _client(citizen).get(f"/api/v1/reports/{report.pk}/evidence/")
    urls = dict(zip((item["id"] for item in response.json()), _signed_urls(response), strict=True))
    assert _evidence_id_of(urls[str(first.pk)]) == str(first.pk), "signed-url-resource-mismatch"
    assert _evidence_id_of(urls[str(second.pk)]) == str(second.pk), "signed-url-resource-mismatch"
    url = _SignedUrl(urls[str(first.pk)].rstrip("/"))
    tampered = _SignedUrl(url[:-1] + ("A" if url[-1] != "A" else "B") + "/")
    with pytest.raises(signing.BadSignature):
        _evidence_id_of(tampered)


def test_signed_access_url_proxies_bytes_without_exposing_object_key(
    report,
    operator_user,
    institution,
    citizen,
    storage_config,
    monkeypatch,
):
    _in_process(report, operator_user, institution)
    evidence = _evidence(report, operator_user)
    downloads = []

    def download(bucket, key):
        downloads.append((bucket, key))
        return b"normalized-image"

    monkeypatch.setattr(storage, "download_private", download)
    listing = _client(citizen).get(f"/api/v1/reports/{report.pk}/evidence/")
    signed_url = listing.json()[0]["signed_url"]

    response = APIClient().get(signed_url)

    assert response.status_code == 200
    assert response.content == b"normalized-image"
    assert response["Content-Type"] == "image/jpeg"
    assert response["Cache-Control"] == "private, no-store"
    assert evidence.evidence_url not in signed_url
    assert str(report.pk) not in signed_url
    assert downloads == [(storage.reports_bucket(), evidence.evidence_url)]


@pytest.mark.parametrize("mode", ["demoted", "inactive_institution"])
def test_operator_get_revalidates_role_and_active_institution(
    mode,
    report,
    operator_user,
    institution,
):
    from apps.auth_app.enums import UserRole

    _in_process(report, operator_user, institution)
    _evidence(report, operator_user)
    if mode == "demoted":
        operator_user.role = UserRole.CITIZEN
        operator_user.save(update_fields=["role"])
    else:
        institution.is_active = False
        institution.save(update_fields=["is_active", "updated_at"])

    response = _client(operator_user).get(_operator_url(report, institution))

    assert response.status_code == 403
    assert response.json()["code"] == "INSTITUTION_OPERATOR_REQUIRED"
    assert response["Cache-Control"] == "private, no-store"


def test_private_evidence_get_rejects_unknown_query_with_stable_code(
    report,
    citizen,
):
    response = _client(citizen).get(f"/api/v1/reports/{report.pk}/evidence/?unexpected=true")

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_REQUEST"
    assert response["Cache-Control"] == "private, no-store"


def test_admin_evidence_get_uses_stable_auth_codes(report, citizen):
    anonymous = APIClient().get(f"/api/v1/admin/reports/{report.pk}/evidence/")
    wrong_role = _client(citizen).get(f"/api/v1/admin/reports/{report.pk}/evidence/")

    assert anonymous.status_code == 401
    assert anonymous.json()["code"] == "AUTHENTICATION_REQUIRED"
    assert wrong_role.status_code == 403
    assert wrong_role.json()["code"] == "ADMIN_REQUIRED"


@pytest.mark.parametrize(
    "url_factory",
    [
        lambda report, institution: f"/api/v1/reports/{report.pk}/evidence/",
        lambda report, institution: f"/api/v1/admin/reports/{report.pk}/evidence/",
        lambda report, institution: _operator_url(report, institution),
    ],
)
def test_invalid_jwt_uses_stable_authentication_code(url_factory, report, institution):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer invalid-token")

    response = client.get(url_factory(report, institution))

    assert response.status_code == 401
    assert response.json() == {
        "detail": "Se requiere autenticación.",
        "code": "AUTHENTICATION_REQUIRED",
    }
    assert response["Cache-Control"] == "private, no-store"
