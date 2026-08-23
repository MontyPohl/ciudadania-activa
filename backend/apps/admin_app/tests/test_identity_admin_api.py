"""Panel administrativo de identidades: cola, detalle y revisión (Sprint 4).

Los tres endpoints son exclusivos de `IsAdmin`. Storage está doblado y el correo usa el
backend `locmem`: **ninguna prueba sale a la red**.

Dos cosas se comprueban una y otra vez, porque son las que más caro cuesta equivocar:
que la URL firmada se genera **solo** en el detalle, y que ni el `document_hmac` ni la
clave del objeto aparecen en ninguna respuesta.
"""

import logging

import pytest
from django.core import mail
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken

from apps.admin_app.models import AuditLog
from apps.admin_app.services import identity_review
from apps.auth_app.enums import UserRole, VerificationStatus
from apps.auth_app.models import CitizenProfile
from apps.auth_app.services import identity as identity_service
from apps.core.services import storage
from apps.notifications_app.enums import NotificationStatus, NotificationType
from apps.notifications_app.models import Notification

pytestmark = pytest.mark.django_db

COLA_URL = "/api/v1/admin/identity/"
FIRMADA = "https://proyecto-de-prueba.supabase.co/storage/v1/object/sign/x?token=jwt"

CAMPOS_COLA = {"id", "user", "created_at", "verification_status"}
CAMPOS_DETALLE = CAMPOS_COLA | {
    "reviewed_at",
    "rejection_reason",
    "verified_by",
    "selfie_signed_url",
}
CAMPOS_REVIEW = {"id", "verification_status", "reviewed_at", "verified_by"}


@pytest.fixture(autouse=True)
def bandeja_limpia():
    mail.outbox.clear()
    yield
    mail.outbox.clear()


@pytest.fixture
def api():
    from rest_framework.test import APIClient

    return APIClient()


@pytest.fixture
def como(api):
    def autenticar(user):
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
        return api

    return autenticar


@pytest.fixture
def firma(monkeypatch):
    """Doble de la firma de Storage. Registra cada llamada y permite inyectar fallos."""
    llamadas = []

    def falsa(bucket, key, expires_in):
        llamadas.append({"bucket": bucket, "key": key, "expires_in": expires_in})
        if getattr(falsa, "error", None) is not None:
            raise falsa.error
        return FIRMADA

    falsa.error = None
    falsa.llamadas = llamadas
    monkeypatch.setattr(storage, "create_signed_url", falsa)
    return falsa


@pytest.fixture
def perfil_factory(user_factory, storage_config, document_hmac_secret):
    """Perfil ya enviado, en el estado que se pida, con el HMAC **real** de su cédula.

    El número queda colgado del objeto como `perfil.cedula`. Es un atributo de la
    instancia y no una columna: la cédula no se persiste en ninguna parte, y las pruebas
    lo necesitan solo para poder transcribirla al aprobar, igual que hace el
    administrador mirando la selfie.

    Cada perfil recibe un número distinto porque `document_hmac` es único: dos perfiles
    con la misma cédula son, por definición del modelo, la misma identidad.
    """
    contador = {"n": 0}

    def make(email=None, estado=VerificationStatus.PENDIENTE, **extra):
        contador["n"] += 1
        n = contador["n"]
        user = user_factory(email=email or f"ciudadano{n}@example.com", full_name=f"Persona {n}")
        user.verification_status = estado
        user.save(update_fields=["verification_status"])
        # Distintiva y con cero inicial: si apareciera en una respuesta, un log o un
        # correo, la aserción que la busca no puede confundirla con un identificador.
        cedula = f"{n:02d}845376"
        perfil = CitizenProfile.objects.create(
            user=user,
            document_hmac=identity_service.compute_document_hmac(cedula),
            verification_selfie_url=f"verifications/{user.pk}/selfie-{n}.jpg",
            **extra,
        )
        perfil.cedula = cedula
        return perfil

    return make


# --------------------------------------------------------------------------- #
# GET /api/v1/admin/identity/
# --------------------------------------------------------------------------- #


def test_la_cola_devuelve_las_pendientes_por_defecto(como, admin_user, perfil_factory):
    pendiente = perfil_factory()
    perfil_factory(estado=VerificationStatus.APROBADO)
    perfil_factory(estado=VerificationStatus.RECHAZADO)

    cuerpo = como(admin_user).get(COLA_URL).json()

    assert cuerpo["count"] == 1
    assert cuerpo["results"][0]["id"] == str(pendiente.pk)
    assert cuerpo["results"][0]["verification_status"] == "PENDIENTE"


@pytest.mark.parametrize("estado", ["PENDIENTE", "APROBADO", "RECHAZADO"])
def test_la_cola_admite_los_tres_estados(como, admin_user, perfil_factory, estado):
    perfil_factory(estado=estado)
    perfil_factory(estado="PENDIENTE" if estado != "PENDIENTE" else "APROBADO")

    cuerpo = como(admin_user).get(COLA_URL, {"status": estado}).json()

    assert cuerpo["count"] == 1
    assert cuerpo["results"][0]["verification_status"] == estado


@pytest.mark.parametrize("estado", ["VALIDADO", "pendiente", "", "DROP TABLE"])
def test_un_estado_inexistente_responde_400(como, admin_user, perfil_factory, estado):
    perfil_factory()

    assert como(admin_user).get(COLA_URL, {"status": estado}).status_code == 400


def test_la_cola_devuelve_el_conjunto_exacto_de_campos(como, admin_user, perfil_factory):
    perfil_factory()

    fila = como(admin_user).get(COLA_URL).json()["results"][0]

    assert set(fila) == CAMPOS_COLA
    assert set(fila["user"]) == {"id", "email", "full_name"}


def test_la_cola_no_expone_ni_el_hmac_ni_la_clave_del_objeto(como, admin_user, perfil_factory):
    perfil = perfil_factory()

    cuerpo = como(admin_user).get(COLA_URL).content.decode()

    assert perfil.document_hmac not in cuerpo
    assert perfil.verification_selfie_url not in cuerpo
    assert "verifications/" not in cuerpo


def test_la_cola_no_firma_ninguna_url(como, admin_user, perfil_factory, firma):
    perfil_factory()
    perfil_factory()

    cuerpo = como(admin_user).get(COLA_URL).content.decode()

    assert firma.llamadas == []
    assert "signed" not in cuerpo
    assert "token=" not in cuerpo


def test_la_cola_esta_paginada_y_ordenada(como, admin_user, perfil_factory, settings):
    settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, "PAGE_SIZE": 2}
    perfiles = [perfil_factory() for _ in range(3)]

    cuerpo = como(admin_user).get(COLA_URL).json()

    assert cuerpo["count"] == 3
    assert len(cuerpo["results"]) <= 3  # el PAGE_SIZE global aplica igual
    devueltos = [fila["id"] for fila in como(admin_user).get(COLA_URL).json()["results"]]
    esperados = [str(p.pk) for p in perfiles][: len(devueltos)]
    assert devueltos == esperados


def test_la_cola_vacia_no_es_un_error(como, admin_user):
    cuerpo = como(admin_user).get(COLA_URL).json()

    assert cuerpo["count"] == 0
    assert cuerpo["results"] == []


# --------------------------------------------------------------------------- #
# GET /api/v1/admin/identity/{id}/
# --------------------------------------------------------------------------- #


def test_el_detalle_incluye_la_url_firmada(como, admin_user, perfil_factory, firma):
    perfil = perfil_factory()

    cuerpo = como(admin_user).get(f"{COLA_URL}{perfil.pk}/").json()

    assert set(cuerpo) == CAMPOS_DETALLE
    assert cuerpo["selfie_signed_url"] == FIRMADA


def test_la_firma_usa_el_bucket_privado_la_clave_real_y_el_ttl(
    como, admin_user, perfil_factory, firma, settings
):
    perfil = perfil_factory()

    como(admin_user).get(f"{COLA_URL}{perfil.pk}/")

    assert firma.llamadas == [
        {
            "bucket": "verificaciones",
            "key": perfil.verification_selfie_url,
            "expires_in": settings.IDENTITY_SIGNED_URL_TTL,
        }
    ]
    assert settings.IDENTITY_SIGNED_URL_TTL == 300


def test_el_detalle_no_devuelve_la_clave_del_objeto_ni_el_hmac(
    como, admin_user, perfil_factory, firma
):
    perfil = perfil_factory()

    cuerpo = como(admin_user).get(f"{COLA_URL}{perfil.pk}/").content.decode()

    assert perfil.document_hmac not in cuerpo
    assert perfil.verification_selfie_url not in cuerpo
    assert "verifications/" not in cuerpo


def test_el_detalle_de_una_revisada_muestra_motivo_y_revisor(
    como, admin_user, perfil_factory, firma
):
    perfil = perfil_factory(
        estado=VerificationStatus.RECHAZADO,
        verified_at=timezone.now(),
        verified_by=admin_user,
        rejection_reason="La cédula no se lee.",
    )

    cuerpo = como(admin_user).get(f"{COLA_URL}{perfil.pk}/").json()

    assert cuerpo["verification_status"] == "RECHAZADO"
    assert cuerpo["rejection_reason"] == "La cédula no se lee."
    assert cuerpo["verified_by"] == str(admin_user.pk)
    assert cuerpo["reviewed_at"] is not None


def test_un_perfil_inexistente_responde_404(como, admin_user, firma):
    import uuid

    respuesta = como(admin_user).get(f"{COLA_URL}{uuid.uuid4()}/")

    assert respuesta.status_code == 404
    assert firma.llamadas == []


def test_si_storage_no_responde_el_detalle_da_503(como, admin_user, perfil_factory, firma):
    firma.error = storage.StorageUnavailableError("El almacenamiento respondió con el estado 500.")
    perfil = perfil_factory()

    respuesta = como(admin_user).get(f"{COLA_URL}{perfil.pk}/")

    assert respuesta.status_code == 503


def test_si_storage_no_esta_configurado_el_detalle_da_503(
    como, admin_user, perfil_factory, settings
):
    perfil = perfil_factory()
    settings.SUPABASE_STORAGE_BUCKET_PRIVATE = ""

    respuesta = como(admin_user).get(f"{COLA_URL}{perfil.pk}/")

    assert respuesta.status_code == 503
    assert "SUPABASE" not in respuesta.json()["detail"]


def test_el_error_de_storage_no_propaga_su_cuerpo(como, admin_user, perfil_factory, firma):
    firma.error = storage.StorageUnavailableError("bucket ciudadania-prod /var/lib/storage")

    respuesta = como(admin_user).get(f"{COLA_URL}{perfil_factory().pk}/")

    assert "ciudadania-prod" not in respuesta.content.decode()
    assert "/var/lib" not in respuesta.content.decode()


def test_un_perfil_sin_archivo_devuelve_url_nula(como, admin_user, perfil_factory, firma):
    perfil = perfil_factory()
    CitizenProfile.objects.filter(pk=perfil.pk).update(verification_selfie_url="")

    cuerpo = como(admin_user).get(f"{COLA_URL}{perfil.pk}/").json()

    assert cuerpo["selfie_signed_url"] is None
    assert firma.llamadas == []


# --------------------------------------------------------------------------- #
# PATCH /api/v1/admin/identity/{id}/review/
# --------------------------------------------------------------------------- #


def test_aprobar(como, admin_user, perfil_factory):
    perfil = perfil_factory()

    respuesta = como(admin_user).patch(
        f"{COLA_URL}{perfil.pk}/review/",
        {"decision": "APROBADO", "document_number": perfil.cedula},
        format="json",
    )

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert set(cuerpo) == CAMPOS_REVIEW
    assert cuerpo["id"] == str(perfil.pk)
    assert cuerpo["verification_status"] == "APROBADO"
    assert cuerpo["verified_by"] == str(admin_user.pk)
    assert cuerpo["reviewed_at"] is not None

    perfil.refresh_from_db()
    perfil.user.refresh_from_db()
    assert perfil.user.verification_status == VerificationStatus.APROBADO
    assert perfil.verified_by == admin_user
    assert perfil.rejection_reason == ""


def test_aprobar_ignora_el_motivo_recibido(como, admin_user, perfil_factory):
    perfil = perfil_factory()

    como(admin_user).patch(
        f"{COLA_URL}{perfil.pk}/review/",
        {
            "decision": "APROBADO",
            "reason": "esto no debería guardarse",
            "document_number": perfil.cedula,
        },
        format="json",
    )

    perfil.refresh_from_db()
    assert perfil.rejection_reason == ""


def test_rechazar_con_motivo(como, admin_user, perfil_factory):
    perfil = perfil_factory()

    respuesta = como(admin_user).patch(
        f"{COLA_URL}{perfil.pk}/review/",
        {"decision": "RECHAZADO", "reason": "  La imagen está borrosa.  "},
        format="json",
    )

    assert respuesta.status_code == 200
    perfil.refresh_from_db()
    perfil.user.refresh_from_db()
    assert perfil.user.verification_status == VerificationStatus.RECHAZADO
    assert perfil.rejection_reason == "La imagen está borrosa."
    assert perfil.verified_at is not None


@pytest.mark.parametrize(
    "payload", [{"decision": "RECHAZADO"}, {"decision": "RECHAZADO", "reason": "   "}]
)
def test_rechazar_sin_motivo_responde_400(como, admin_user, perfil_factory, payload):
    perfil = perfil_factory()

    respuesta = como(admin_user).patch(f"{COLA_URL}{perfil.pk}/review/", payload, format="json")

    assert respuesta.status_code == 400
    perfil.user.refresh_from_db()
    assert perfil.user.verification_status == VerificationStatus.PENDIENTE


@pytest.mark.parametrize("decision", ["PENDIENTE", "aprobado", "SI", ""])
def test_una_decision_invalida_responde_400(como, admin_user, perfil_factory, decision):
    perfil = perfil_factory()

    respuesta = como(admin_user).patch(
        f"{COLA_URL}{perfil.pk}/review/", {"decision": decision}, format="json"
    )

    assert respuesta.status_code == 400


def test_un_payload_sin_decision_responde_400(como, admin_user, perfil_factory):
    perfil = perfil_factory()

    respuesta = como(admin_user).patch(f"{COLA_URL}{perfil.pk}/review/", {}, format="json")

    assert respuesta.status_code == 400


def test_revisar_una_identidad_inexistente_responde_404(como, admin_user):
    import uuid

    respuesta = como(admin_user).patch(
        f"{COLA_URL}{uuid.uuid4()}/review/",
        # Payload completo y válido: el 404 tiene que venir de que la verificación no
        # existe, no de que falte el número.
        {"decision": "APROBADO", "document_number": "1234567"},
        format="json",
    )

    assert respuesta.status_code == 404


@pytest.mark.parametrize("ya", ["APROBADO", "RECHAZADO"])
def test_revisar_dos_veces_responde_409(como, admin_user, perfil_factory, ya):
    perfil = perfil_factory(estado=ya)

    respuesta = como(admin_user).patch(
        f"{COLA_URL}{perfil.pk}/review/",
        {"decision": "APROBADO", "document_number": perfil.cedula},
        format="json",
    )

    assert respuesta.status_code == 409


def test_la_segunda_revision_no_pisa_la_primera(como, admin_user, perfil_factory):
    perfil = perfil_factory()
    cliente = como(admin_user)
    cliente.patch(
        f"{COLA_URL}{perfil.pk}/review/",
        {"decision": "APROBADO", "document_number": perfil.cedula},
        format="json",
    )

    cliente.patch(
        f"{COLA_URL}{perfil.pk}/review/",
        {"decision": "RECHAZADO", "reason": "cambio de opinión"},
        format="json",
    )

    perfil.refresh_from_db()
    perfil.user.refresh_from_db()
    assert perfil.user.verification_status == VerificationStatus.APROBADO
    assert perfil.rejection_reason == ""
    assert AuditLog.objects.filter(action=identity_review.ACCION_APROBACION).count() == 1
    assert Notification.objects.count() == 1


def test_la_revision_no_expone_datos_sensibles(como, admin_user, perfil_factory):
    perfil = perfil_factory()

    cuerpo = (
        como(admin_user)
        .patch(
            f"{COLA_URL}{perfil.pk}/review/",
            {"decision": "APROBADO", "document_number": perfil.cedula},
            format="json",
        )
        .content.decode()
    )

    assert perfil.document_hmac not in cuerpo
    assert perfil.verification_selfie_url not in cuerpo
    assert perfil.user.email not in cuerpo
    assert admin_user.email not in cuerpo


# --------------------------------------------------------------------------- #
# La cédula transcrita por el administrador, sobre HTTP
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "plantilla",
    [
        pytest.param("{n}", id="exacta"),
        pytest.param("{a}.{b}", id="con-punto"),
        pytest.param("{a}-{b}", id="con-guion"),
        pytest.param("{a} {b}", id="con-espacio"),
        pytest.param(" {a}.{b} ", id="mezcla-y-bordes"),
    ],
)
def test_aprobar_admite_las_formas_equivalentes_del_numero(
    como, admin_user, perfil_factory, plantilla
):
    perfil = perfil_factory()
    escrito = plantilla.format(n=perfil.cedula, a=perfil.cedula[:3], b=perfil.cedula[3:])

    respuesta = como(admin_user).patch(
        f"{COLA_URL}{perfil.pk}/review/",
        {"decision": "APROBADO", "document_number": escrito},
        format="json",
    )

    assert respuesta.status_code == 200
    perfil.user.refresh_from_db()
    assert perfil.user.verification_status == VerificationStatus.APROBADO


def test_un_numero_que_no_coincide_responde_400_y_no_revisa(como, admin_user, perfil_factory):
    perfil = perfil_factory()

    respuesta = como(admin_user).patch(
        f"{COLA_URL}{perfil.pk}/review/",
        {"decision": "APROBADO", "document_number": "9999999"},
        format="json",
    )

    assert respuesta.status_code == 400
    perfil.refresh_from_db()
    perfil.user.refresh_from_db()
    assert perfil.user.verification_status == VerificationStatus.PENDIENTE
    assert perfil.verified_at is None
    assert perfil.verified_by is None
    assert not AuditLog.objects.exists()
    assert not Notification.objects.exists()
    assert mail.outbox == []


def test_el_400_del_desajuste_no_devuelve_el_hmac_ni_ningun_numero(
    como, admin_user, perfil_factory
):
    """400, no 409: el conflicto está reservado al estado de la verificación."""
    perfil = perfil_factory()

    respuesta = como(admin_user).patch(
        f"{COLA_URL}{perfil.pk}/review/",
        {"decision": "APROBADO", "document_number": "9999999"},
        format="json",
    )

    cuerpo = respuesta.content.decode()
    assert respuesta.status_code == 400
    assert perfil.document_hmac not in cuerpo
    assert perfil.cedula not in cuerpo
    assert "9999999" not in cuerpo


@pytest.mark.parametrize("escrito", ["12a3456", "1234567/8", "１２３４５６７", "..."])
def test_un_numero_con_caracteres_no_admitidos_responde_400(
    como, admin_user, perfil_factory, escrito
):
    perfil = perfil_factory()

    respuesta = como(admin_user).patch(
        f"{COLA_URL}{perfil.pk}/review/",
        {"decision": "APROBADO", "document_number": escrito},
        format="json",
    )

    assert respuesta.status_code == 400
    perfil.user.refresh_from_db()
    assert perfil.user.verification_status == VerificationStatus.PENDIENTE
    assert not AuditLog.objects.exists()


@pytest.mark.parametrize(
    "payload",
    [
        {"decision": "APROBADO"},
        {"decision": "APROBADO", "document_number": ""},
        {"decision": "APROBADO", "document_number": "   "},
    ],
)
def test_aprobar_sin_el_numero_responde_400(como, admin_user, perfil_factory, payload):
    perfil = perfil_factory()

    respuesta = como(admin_user).patch(f"{COLA_URL}{perfil.pk}/review/", payload, format="json")

    assert respuesta.status_code == 400
    # El manejador de errores del proyecto envuelve los del serializador en `detail`.
    assert "document_number" in respuesta.json()["detail"]
    perfil.user.refresh_from_db()
    assert perfil.user.verification_status == VerificationStatus.PENDIENTE
    assert not AuditLog.objects.exists()
    assert not Notification.objects.exists()


def test_rechazar_sigue_sin_pedir_el_numero(como, admin_user, perfil_factory):
    """El rechazo no cambió: motivo obligatorio y ninguna cédula."""
    perfil = perfil_factory()

    respuesta = como(admin_user).patch(
        f"{COLA_URL}{perfil.pk}/review/",
        {"decision": "RECHAZADO", "reason": "La cédula no se lee en la imagen."},
        format="json",
    )

    assert respuesta.status_code == 200
    perfil.refresh_from_db()
    perfil.user.refresh_from_db()
    assert perfil.user.verification_status == VerificationStatus.RECHAZADO
    assert perfil.rejection_reason == "La cédula no se lee en la imagen."


def test_rechazar_ignora_el_numero_si_llegara(como, admin_user, perfil_factory):
    """Simétrico a que aprobar ignore `reason`. Y no se persiste en ningún campo."""
    perfil = perfil_factory()

    respuesta = como(admin_user).patch(
        f"{COLA_URL}{perfil.pk}/review/",
        {"decision": "RECHAZADO", "reason": "Ilegible.", "document_number": perfil.cedula},
        format="json",
    )

    assert respuesta.status_code == 200
    perfil.refresh_from_db()
    assert perfil.rejection_reason == "Ilegible."
    # Relectura desde la base: `perfil.cedula` es un atributo que pone la factoría para
    # las pruebas, así que mirar la instancia en memoria encontraría siempre el número.
    guardado = CitizenProfile.objects.get(pk=perfil.pk)
    assert perfil.cedula not in str(guardado.__dict__)
    assert perfil.cedula not in str(AuditLog.objects.get().payload)


def test_sin_clave_de_hmac_la_aprobacion_responde_503(como, admin_user, perfil_factory, settings):
    """Fallo cerrado y opaco: el 503 no nombra la variable que falta."""
    perfil = perfil_factory()
    settings.DOCUMENT_HMAC_SECRET = ""

    respuesta = como(admin_user).patch(
        f"{COLA_URL}{perfil.pk}/review/",
        {"decision": "APROBADO", "document_number": perfil.cedula},
        format="json",
    )

    assert respuesta.status_code == 503
    assert "DOCUMENT_HMAC_SECRET" not in respuesta.content.decode()
    perfil.user.refresh_from_db()
    assert perfil.user.verification_status == VerificationStatus.PENDIENTE
    assert not AuditLog.objects.exists()
    assert not Notification.objects.exists()


def test_una_aprobacion_correcta_no_deja_la_cedula_en_ningun_lado(
    como, admin_user, perfil_factory, django_capture_on_commit_callbacks, caplog
):
    """La respuesta, la auditoría, la notificación, el correo y los logs, de una vez."""
    perfil = perfil_factory()

    with caplog.at_level(logging.DEBUG), django_capture_on_commit_callbacks(execute=True):
        respuesta = como(admin_user).patch(
            f"{COLA_URL}{perfil.pk}/review/",
            {"decision": "APROBADO", "document_number": perfil.cedula},
            format="json",
        )

    assert respuesta.status_code == 200
    fila = AuditLog.objects.get()
    correo = mail.outbox[0]
    # Fila releída de la base, no la instancia de la factoría: esa lleva `cedula` colgada
    # como atributo de prueba y la aserción se dispararía siempre.
    guardado = CitizenProfile.objects.get(pk=perfil.pk)

    lugares = [
        respuesta.content.decode(),
        str(fila.payload),
        str(guardado.__dict__),
        str(Notification.objects.get().__dict__),
        correo.subject,
        correo.body,
        caplog.text,
    ]
    for texto in lugares:
        assert perfil.cedula not in texto

    assert fila.action == identity_review.ACCION_APROBACION


# --------------------------------------------------------------------------- #
# Permisos
# --------------------------------------------------------------------------- #


def _rutas(perfil):
    return [
        ("get", COLA_URL, None),
        ("get", f"{COLA_URL}{perfil.pk}/", None),
        (
            "patch",
            f"{COLA_URL}{perfil.pk}/review/",
            {"decision": "APROBADO", "document_number": perfil.cedula},
        ),
    ]


def _pedir(cliente, metodo, url, cuerpo):
    if cuerpo is None:
        return getattr(cliente, metodo)(url)
    return getattr(cliente, metodo)(url, cuerpo, format="json")


def test_el_acceso_anonimo_responde_401(api, perfil_factory, firma):
    perfil = perfil_factory()

    for metodo, url, cuerpo in _rutas(perfil):
        assert _pedir(api, metodo, url, cuerpo).status_code == 401, url


@pytest.mark.parametrize("rol", ["citizen", "operator"])
def test_ni_ciudadano_ni_operador_acceden(como, user_factory, perfil_factory, firma, rol):
    perfil = perfil_factory()
    actor = user_factory(email=f"{rol}-admin-api@example.com", role=rol)
    cliente = como(actor)

    for metodo, url, cuerpo in _rutas(perfil):
        assert _pedir(cliente, metodo, url, cuerpo).status_code == 403, url

    assert firma.llamadas == []


def test_is_staff_y_is_superuser_no_sustituyen_al_rol_admin(
    como, user_factory, perfil_factory, firma
):
    """ADR-0005: los flags del admin de Django no son autorización de dominio."""
    from django.contrib.auth import get_user_model

    perfil = perfil_factory()
    root = get_user_model().objects.create_superuser(
        email="root-admin-api@example.com", password="clave-segura-123", role=UserRole.CITIZEN
    )
    staff = user_factory(email="staff-admin-api@example.com", role=UserRole.OPERATOR, is_staff=True)

    for actor in (root, staff):
        cliente = como(actor)
        for metodo, url, cuerpo in _rutas(perfil):
            assert _pedir(cliente, metodo, url, cuerpo).status_code == 403, (actor.email, url)


def test_el_admin_de_dominio_sin_flags_de_django_si_accede(como, admin_user, perfil_factory, firma):
    perfil = perfil_factory()

    assert admin_user.is_staff is False
    assert admin_user.is_superuser is False
    assert como(admin_user).get(COLA_URL).status_code == 200
    assert como(admin_user).get(f"{COLA_URL}{perfil.pk}/").status_code == 200


# --------------------------------------------------------------------------- #
# Fallo al encolar el correo
# --------------------------------------------------------------------------- #
#
# Estas pruebas son **transaccionales a propósito**. Con el `django_db` normal la
# transacción de la prueba nunca se confirma, así que el callback de `on_commit` no
# llega a ejecutarse y la prueba pasaría sin comprobar nada. Solo con un commit real el
# callback corre **dentro** de la petición, que es donde una excepción suya se
# convertiría en un 500.


@pytest.fixture
def broker_caido(monkeypatch):
    """El encolado revienta con una credencial dentro del mensaje de error."""
    from apps.notifications_app import tasks

    def falla(*args, **kwargs):
        raise ConnectionError("Error 111 connecting to redis://:clave@host:6379.")

    monkeypatch.setattr(tasks.send_notification, "delay", falla)


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_si_el_broker_falla_la_revision_sigue_siendo_200(
    como, user_factory, perfil_factory, broker_caido
):
    from apps.auth_app.enums import UserRole

    admin = user_factory(email="admin-broker@example.com", role=UserRole.ADMIN)
    perfil = perfil_factory()

    respuesta = como(admin).patch(
        f"{COLA_URL}{perfil.pk}/review/",
        {"decision": "APROBADO", "document_number": perfil.cedula},
        format="json",
    )

    assert respuesta.status_code == 200


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_si_el_broker_falla_la_decision_y_la_auditoria_persisten(
    como, user_factory, perfil_factory, broker_caido
):
    from apps.auth_app.enums import UserRole

    admin = user_factory(email="admin-broker2@example.com", role=UserRole.ADMIN)
    perfil = perfil_factory()

    como(admin).patch(
        f"{COLA_URL}{perfil.pk}/review/",
        {"decision": "APROBADO", "document_number": perfil.cedula},
        format="json",
    )

    perfil.user.refresh_from_db()
    assert perfil.user.verification_status == VerificationStatus.APROBADO
    assert AuditLog.objects.filter(action=identity_review.ACCION_APROBACION).count() == 1


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_si_el_broker_falla_la_notificacion_queda_fallida(
    como, user_factory, perfil_factory, broker_caido
):
    """No puede quedarse PENDIENTE para siempre: el fallo tiene que verse."""
    from apps.auth_app.enums import UserRole

    admin = user_factory(email="admin-broker3@example.com", role=UserRole.ADMIN)
    perfil = perfil_factory()

    como(admin).patch(
        f"{COLA_URL}{perfil.pk}/review/",
        {"decision": "APROBADO", "document_number": perfil.cedula},
        format="json",
    )

    notificacion = Notification.objects.get()
    assert notificacion.status == NotificationStatus.FALLIDO
    assert notificacion.notification_type == NotificationType.VERIFICACION_APROBADA
    assert mail.outbox == []


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_los_logs_del_fallo_de_encolado_no_llevan_datos_sensibles(
    como, user_factory, perfil_factory, broker_caido, caplog
):
    from apps.auth_app.enums import UserRole

    admin = user_factory(email="admin-broker4@example.com", role=UserRole.ADMIN)
    perfil = perfil_factory()

    with caplog.at_level(logging.DEBUG):
        como(admin).patch(
            f"{COLA_URL}{perfil.pk}/review/",
            {"decision": "APROBADO", "document_number": perfil.cedula},
            format="json",
        )

    assert "clave@host" not in caplog.text
    assert perfil.document_hmac not in caplog.text
    assert perfil.verification_selfie_url not in caplog.text
    assert perfil.user.email not in caplog.text
    assert "ConnectionError" in caplog.text


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_con_el_broker_sano_la_revision_entrega_el_correo(como, user_factory, perfil_factory):
    """Contrapunto: con commit real y broker sano, el correo sí sale."""
    from apps.auth_app.enums import UserRole

    admin = user_factory(email="admin-ok@example.com", role=UserRole.ADMIN)
    perfil = perfil_factory()

    respuesta = como(admin).patch(
        f"{COLA_URL}{perfil.pk}/review/",
        {"decision": "APROBADO", "document_number": perfil.cedula},
        format="json",
    )

    assert respuesta.status_code == 200
    assert Notification.objects.get().status == NotificationStatus.ENVIADO
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [perfil.user.email]


# --------------------------------------------------------------------------- #
# Defensa en profundidad
# --------------------------------------------------------------------------- #


def test_la_vista_traduce_una_revision_invalida_del_servicio_a_400(
    como, admin_user, perfil_factory, monkeypatch
):
    """El serializador ya filtra estos casos; la traducción debe existir igualmente.

    El servicio revalida por su cuenta —no delega en la vista— así que su error tiene
    que tener un código asignado aunque por HTTP no se llegue nunca.
    """

    def rechazar(*args, **kwargs):
        raise identity_review.InvalidReviewError("La decisión debe ser APROBADO o RECHAZADO.")

    perfil = perfil_factory()
    monkeypatch.setattr(identity_review, "review", rechazar)

    respuesta = como(admin_user).patch(
        f"{COLA_URL}{perfil.pk}/review/",
        {"decision": "APROBADO", "document_number": perfil.cedula},
        format="json",
    )

    assert respuesta.status_code == 400
