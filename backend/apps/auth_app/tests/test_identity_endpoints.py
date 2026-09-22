"""Endpoints ciudadanos de verificación de identidad (Sprint 4).

`POST /api/v1/identity/submit/` y `GET /api/v1/identity/status/`.

Storage está doblado en todas las pruebas: **ninguna sale a la red**. La clave del HMAC
y la configuración de Supabase son ficticias.

Hay un bloque final dedicado a comprobar, de forma transversal, que ni el número de
cédula, ni el `document_hmac`, ni la clave del objeto aparecen jamás en una respuesta.
"""

import logging

import pytest
from django.core.cache import cache
from rest_framework_simplejwt.tokens import RefreshToken

from apps.admin_app.models import AuditLog
from apps.auth_app.enums import UserRole, VerificationStatus
from apps.auth_app.models import CitizenProfile
from apps.auth_app.services import identity
from apps.core.services import storage

pytestmark = pytest.mark.django_db

SUBMIT_URL = "/api/v1/identity/submit/"
STATUS_URL = "/api/v1/identity/status/"

CEDULA = "1.234.567"
CEDULA_NORMALIZADA = "1234567"
OTRA_CEDULA = "7.654.321"

CAMPOS_STATUS = {"has_submission", "verification_status", "reviewed_at", "rejection_reason"}


@pytest.fixture(autouse=True)
def cache_limpia():
    """El throttle cuenta en la caché por defecto: sin limpiar, se filtra entre pruebas."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def api():
    from rest_framework.test import APIClient

    return APIClient()


@pytest.fixture
def como(api):
    """Autentica el cliente con un access real, como en las pruebas del Sprint 3."""

    def autenticar(user):
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
        return api

    return autenticar


@pytest.fixture
def envio(selfie_factory, jpeg_bytes):
    """Cuerpo multipart válido del envío."""

    def make(document_number=CEDULA, content=None, name="selfie.jpg"):
        return {
            "document_number": document_number,
            "selfie": selfie_factory(content if content is not None else jpeg_bytes, name=name),
        }

    return make


@pytest.fixture
def entorno(document_hmac_secret, storage_config, fake_storage):
    """Configuración completa y Storage doblado."""
    return fake_storage


def _rechazar(user, motivo="La imagen no permite leer el documento."):
    """Deja al ciudadano en RECHAZADO, como lo dejará la revisión administrativa."""
    from django.utils import timezone

    perfil = CitizenProfile.objects.get(user=user)
    perfil.verified_at = timezone.now()
    perfil.rejection_reason = motivo
    perfil.save(update_fields=["verified_at", "rejection_reason"])
    user.verification_status = VerificationStatus.RECHAZADO
    user.save(update_fields=["verification_status"])
    return perfil


# --------------------------------------------------------------------------- #
# Primer envío
# --------------------------------------------------------------------------- #


def test_primer_envio_valido(como, citizen, envio, entorno):
    respuesta = como(citizen).post(SUBMIT_URL, envio(), format="multipart")

    assert respuesta.status_code == 201
    assert respuesta.json() == {"verification_status": "PENDIENTE"}


def test_el_primer_envio_crea_el_perfil(como, citizen, envio, entorno):
    como(citizen).post(SUBMIT_URL, envio(), format="multipart")

    perfil = CitizenProfile.objects.get(user=citizen)
    assert perfil.rejection_reason == ""
    assert perfil.verified_at is None
    assert perfil.verified_by is None
    citizen.refresh_from_db()
    assert citizen.verification_status == VerificationStatus.PENDIENTE


def test_persiste_el_hmac_y_no_la_cedula(como, citizen, envio, entorno, document_hmac_secret):
    como(citizen).post(SUBMIT_URL, envio(), format="multipart")

    perfil = CitizenProfile.objects.get(user=citizen)
    assert perfil.document_hmac == identity.compute_document_hmac(CEDULA)
    assert len(perfil.document_hmac) == 64
    assert CEDULA not in perfil.document_hmac
    assert CEDULA_NORMALIZADA not in perfil.document_hmac
    # La cédula no está en ninguna columna de la fila.
    fila = CitizenProfile.objects.filter(user=citizen).values().first()
    assert CEDULA_NORMALIZADA not in " ".join(str(v) for v in fila.values())


def test_la_clave_del_objeto_se_guarda_internamente(como, citizen, envio, entorno):
    como(citizen).post(SUBMIT_URL, envio(), format="multipart")

    perfil = CitizenProfile.objects.get(user=citizen)
    assert perfil.verification_selfie_url.startswith(f"verifications/{citizen.pk}/")
    assert perfil.verification_selfie_url.endswith(".jpg")


def test_storage_recibe_exactamente_una_subida(como, citizen, envio, entorno, jpeg_bytes):
    como(citizen).post(SUBMIT_URL, envio(), format="multipart")

    assert len(entorno.uploads) == 1
    assert entorno.deletes == []
    subida = entorno.uploads[0]
    assert subida["bucket"] == "verificaciones"
    assert subida["content"] == jpeg_bytes
    assert subida["content_type"] == "image/jpeg"
    assert subida["key"] == CitizenProfile.objects.get(user=citizen).verification_selfie_url


def test_el_mime_y_la_extension_salen_del_contenido_real(como, citizen, envio, entorno, png_bytes):
    """Un `.jpg` cuyos bytes son PNG se guarda como PNG."""
    como(citizen).post(SUBMIT_URL, envio(content=png_bytes, name="foto.jpg"), format="multipart")

    assert entorno.uploads[0]["content_type"] == "image/png"
    assert entorno.uploads[0]["key"].endswith(".png")


def test_el_envio_queda_auditado(como, citizen, envio, entorno):
    como(citizen).post(SUBMIT_URL, envio(), format="multipart")

    entrada = AuditLog.objects.get(action=identity.ACCION_ENVIO)
    perfil = CitizenProfile.objects.get(user=citizen)
    assert entrada.actor_user == citizen
    assert entrada.payload == {"profile_id": str(perfil.pk), "user_id": str(citizen.pk)}


def test_la_auditoria_no_lleva_datos_sensibles(como, citizen, envio, entorno):
    como(citizen).post(SUBMIT_URL, envio(), format="multipart")

    perfil = CitizenProfile.objects.get(user=citizen)
    texto = str(AuditLog.objects.get(action=identity.ACCION_ENVIO).payload)
    assert CEDULA not in texto
    assert CEDULA_NORMALIZADA not in texto
    assert perfil.document_hmac not in texto
    assert perfil.verification_selfie_url not in texto


# --------------------------------------------------------------------------- #
# Errores del envío
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "ausente",
    [
        "DOCUMENT_HMAC_SECRET",
        "SUPABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_STORAGE_BUCKET_PRIVATE",
    ],
)
def test_sin_configuracion_responde_503_sin_tocar_storage_ni_base(
    como, citizen, envio, entorno, settings, ausente
):
    setattr(settings, ausente, "")

    respuesta = como(citizen).post(SUBMIT_URL, envio(), format="multipart")

    assert respuesta.status_code == 503
    assert entorno.uploads == []
    assert entorno.deletes == []
    assert not CitizenProfile.objects.filter(user=citizen).exists()


def test_el_envio_funciona_con_storage_local_y_supabase_en_blanco(
    como, citizen, envio, s11_local_storage, document_hmac_secret
):
    """Remediación A5: el overlay S11 deja Supabase en blanco y usa el adaptador local.

    Deliberadamente **no** usa `entorno`: la subida debe recorrer el adaptador real,
    que es lo que EX-02 ejercita. Antes de A5 esto respondía 503 porque la guarda de
    configuración exigía `SUPABASE_URL` y la service-role aunque nunca se usaran.
    """
    respuesta = como(citizen).post(SUBMIT_URL, envio(), format="multipart")

    assert respuesta.status_code == 201
    perfil = CitizenProfile.objects.get(user=citizen)
    assert len(perfil.document_hmac) == 64
    assert CEDULA not in perfil.document_hmac
    assert CEDULA_NORMALIZADA not in perfil.document_hmac


def test_el_503_no_nombra_ninguna_variable(como, citizen, envio, entorno, settings):
    settings.SUPABASE_SERVICE_ROLE_KEY = ""

    cuerpo = como(citizen).post(SUBMIT_URL, envio(), format="multipart").json()

    assert "SUPABASE" not in cuerpo["detail"]
    assert "HMAC" not in cuerpo["detail"]


@pytest.mark.parametrize("cedula", ["abc123", "12/34", "١٢٣٤", "..."])
def test_una_cedula_invalida_responde_400(como, citizen, envio, entorno, cedula):
    respuesta = como(citizen).post(SUBMIT_URL, envio(document_number=cedula), format="multipart")

    assert respuesta.status_code == 400
    assert entorno.uploads == []
    assert not CitizenProfile.objects.filter(user=citizen).exists()


def test_la_cedula_invalida_no_se_devuelve_en_el_error(como, citizen, envio, entorno):
    respuesta = como(citizen).post(
        SUBMIT_URL, envio(document_number="abc987654"), format="multipart"
    )
    cuerpo = respuesta.json()

    assert "987654" not in str(cuerpo)


def test_una_selfie_invalida_responde_400(como, citizen, envio, entorno):
    respuesta = como(citizen).post(
        SUBMIT_URL, envio(content=b"%PDF-1.7 no soy una imagen"), format="multipart"
    )

    assert respuesta.status_code == 400
    assert entorno.uploads == []
    assert not CitizenProfile.objects.filter(user=citizen).exists()


def test_falta_la_cedula_o_la_selfie(como, citizen, entorno, selfie_factory, jpeg_bytes):
    cliente = como(citizen)
    sin_selfie = cliente.post(SUBMIT_URL, {"document_number": CEDULA}, format="multipart")
    sin_cedula = cliente.post(
        SUBMIT_URL, {"selfie": selfie_factory(jpeg_bytes)}, format="multipart"
    )

    assert sin_selfie.status_code == 400
    assert sin_cedula.status_code == 400


def test_un_fallo_de_storage_responde_503_y_no_crea_perfil(como, citizen, envio, entorno):
    entorno.upload_error = storage.StorageUnavailableError("Supabase no responde.")

    respuesta = como(citizen).post(SUBMIT_URL, envio(), format="multipart")

    assert respuesta.status_code == 503
    assert not CitizenProfile.objects.filter(user=citizen).exists()
    assert entorno.deletes == []  # No hay nada que compensar: la subida nunca ocurrió.


# --------------------------------------------------------------------------- #
# Cédula duplicada
# --------------------------------------------------------------------------- #


def test_una_cedula_ya_registrada_responde_409_generico(
    como, citizen, other_citizen, envio, entorno
):
    como(citizen).post(SUBMIT_URL, envio(), format="multipart")

    respuesta = como(other_citizen).post(SUBMIT_URL, envio(), format="multipart")

    assert respuesta.status_code == 409
    detalle = respuesta.json()["detail"]
    assert citizen.email not in detalle
    assert str(citizen.pk) not in detalle
    assert CEDULA not in detalle
    assert CitizenProfile.objects.count() == 1


def test_la_cedula_duplicada_borra_el_objeto_recien_subido(
    como, citizen, other_citizen, envio, entorno
):
    como(citizen).post(SUBMIT_URL, envio(), format="multipart")
    clave_del_primero = CitizenProfile.objects.get(user=citizen).verification_selfie_url

    como(other_citizen).post(SUBMIT_URL, envio(), format="multipart")

    assert len(entorno.uploads) == 2
    assert len(entorno.deletes) == 1
    borrada = entorno.deletes[0]["key"]
    assert borrada == entorno.uploads[1]["key"]
    assert borrada != clave_del_primero
    # El objeto del primer ciudadano sigue intacto.
    assert CitizenProfile.objects.get(user=citizen).verification_selfie_url == clave_del_primero


def test_si_falla_la_compensacion_el_error_original_sigue_siendo_el_409(
    como, citizen, other_citizen, envio, entorno, caplog
):
    como(citizen).post(SUBMIT_URL, envio(), format="multipart")
    entorno.delete_error = storage.StorageUnavailableError("Supabase no responde.")

    with caplog.at_level(logging.DEBUG):
        respuesta = como(other_citizen).post(SUBMIT_URL, envio(), format="multipart")

    assert respuesta.status_code == 409
    assert len(entorno.deletes) == 1  # Se intentó
    clave_huerfana = entorno.uploads[1]["key"]
    assert clave_huerfana not in caplog.text
    assert str(other_citizen.pk) not in caplog.text
    assert "verifications/…" in caplog.text


# --------------------------------------------------------------------------- #
# Estados que no admiten un envío
# --------------------------------------------------------------------------- #


def test_un_segundo_envio_estando_pendiente_responde_409(como, citizen, envio, entorno):
    como(citizen).post(SUBMIT_URL, envio(), format="multipart")

    respuesta = como(citizen).post(
        SUBMIT_URL, envio(document_number=OTRA_CEDULA), format="multipart"
    )

    assert respuesta.status_code == 409
    assert len(entorno.uploads) == 1  # El segundo ni siquiera sube nada.


def test_un_envio_estando_aprobado_responde_409(como, citizen, envio, entorno):
    como(citizen).post(SUBMIT_URL, envio(), format="multipart")
    citizen.verification_status = VerificationStatus.APROBADO
    citizen.save(update_fields=["verification_status"])

    respuesta = como(citizen).post(
        SUBMIT_URL, envio(document_number=OTRA_CEDULA), format="multipart"
    )

    assert respuesta.status_code == 409
    assert len(entorno.uploads) == 1


def test_un_estado_incoherente_sin_perfil_responde_409(como, citizen, envio, entorno):
    """APROBADO sin perfil solo puede venir de una edición manual: no se recompone."""
    citizen.verification_status = VerificationStatus.APROBADO
    citizen.save(update_fields=["verification_status"])

    respuesta = como(citizen).post(SUBMIT_URL, envio(), format="multipart")

    assert respuesta.status_code == 409
    assert entorno.uploads == []


# --------------------------------------------------------------------------- #
# Reenvío tras rechazo
# --------------------------------------------------------------------------- #


def test_el_reenvio_tras_rechazo_reutiliza_el_perfil(
    como, citizen, envio, entorno, django_capture_on_commit_callbacks
):
    como(citizen).post(SUBMIT_URL, envio(), format="multipart")
    perfil = _rechazar(citizen)
    clave_anterior = perfil.verification_selfie_url

    with django_capture_on_commit_callbacks(execute=True):
        respuesta = como(citizen).post(
            SUBMIT_URL, envio(document_number=OTRA_CEDULA), format="multipart"
        )

    assert respuesta.status_code == 201
    assert respuesta.json() == {"verification_status": "PENDIENTE"}
    assert CitizenProfile.objects.count() == 1

    perfil.refresh_from_db()
    citizen.refresh_from_db()
    assert citizen.verification_status == VerificationStatus.PENDIENTE
    assert perfil.rejection_reason == ""
    assert perfil.verified_at is None
    assert perfil.verified_by is None
    assert perfil.document_hmac == identity.compute_document_hmac(OTRA_CEDULA)
    assert perfil.verification_selfie_url != clave_anterior


def test_el_reenvio_se_audita_como_resubmitted(
    como, citizen, envio, entorno, django_capture_on_commit_callbacks
):
    como(citizen).post(SUBMIT_URL, envio(), format="multipart")
    _rechazar(citizen)

    with django_capture_on_commit_callbacks(execute=True):
        como(citizen).post(SUBMIT_URL, envio(document_number=OTRA_CEDULA), format="multipart")

    assert AuditLog.objects.filter(action=identity.ACCION_ENVIO).count() == 1
    entrada = AuditLog.objects.get(action=identity.ACCION_REENVIO)
    perfil = CitizenProfile.objects.get(user=citizen)
    assert entrada.payload == {"profile_id": str(perfil.pk), "user_id": str(citizen.pk)}


def test_el_objeto_anterior_se_borra_solo_despues_del_commit(
    como, citizen, envio, entorno, django_capture_on_commit_callbacks
):
    como(citizen).post(SUBMIT_URL, envio(), format="multipart")
    clave_anterior = _rechazar(citizen).verification_selfie_url

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        como(citizen).post(SUBMIT_URL, envio(document_number=OTRA_CEDULA), format="multipart")
        # Todavía dentro del bloque: el borrado está encolado, no ejecutado.
        assert entorno.deletes == []

    # `captureOnCommitCallbacks` recoge la lista al salir del bloque, no durante.
    assert len(callbacks) == 1
    assert [borrado["key"] for borrado in entorno.deletes] == [clave_anterior]


def test_sin_commit_no_se_borra_el_objeto_anterior(como, citizen, envio, entorno):
    """Sin ejecutar los callbacks, el archivo vigente sigue donde estaba."""
    como(citizen).post(SUBMIT_URL, envio(), format="multipart")
    _rechazar(citizen)

    como(citizen).post(SUBMIT_URL, envio(document_number=OTRA_CEDULA), format="multipart")

    assert entorno.deletes == []


def test_si_falla_el_borrado_del_anterior_la_peticion_sigue_siendo_exitosa(
    como, citizen, envio, entorno, django_capture_on_commit_callbacks, caplog
):
    como(citizen).post(SUBMIT_URL, envio(), format="multipart")
    clave_anterior = _rechazar(citizen).verification_selfie_url
    entorno.delete_error = storage.StorageUnavailableError("Supabase no responde.")

    with caplog.at_level(logging.DEBUG), django_capture_on_commit_callbacks(execute=True):
        respuesta = como(citizen).post(
            SUBMIT_URL, envio(document_number=OTRA_CEDULA), format="multipart"
        )

    assert respuesta.status_code == 201
    citizen.refresh_from_db()
    assert citizen.verification_status == VerificationStatus.PENDIENTE
    assert clave_anterior not in caplog.text
    assert "verifications/…" in caplog.text


def test_un_reenvio_con_cedula_ya_usada_por_otro_conserva_el_objeto_anterior(
    como, citizen, other_citizen, envio, entorno
):
    como(other_citizen).post(SUBMIT_URL, envio(document_number=OTRA_CEDULA), format="multipart")
    como(citizen).post(SUBMIT_URL, envio(), format="multipart")
    perfil = _rechazar(citizen)
    clave_anterior = perfil.verification_selfie_url
    hmac_anterior = perfil.document_hmac

    respuesta = como(citizen).post(
        SUBMIT_URL, envio(document_number=OTRA_CEDULA), format="multipart"
    )

    assert respuesta.status_code == 409
    perfil.refresh_from_db()
    citizen.refresh_from_db()
    assert perfil.verification_selfie_url == clave_anterior
    assert perfil.document_hmac == hmac_anterior
    assert citizen.verification_status == VerificationStatus.RECHAZADO
    # Se borró el objeto nuevo, no el anterior.
    assert len(entorno.deletes) == 1
    assert entorno.deletes[0]["key"] == entorno.uploads[-1]["key"]
    assert entorno.deletes[0]["key"] != clave_anterior


def test_el_reenvio_admite_repetir_la_misma_cedula(
    como, citizen, envio, entorno, django_capture_on_commit_callbacks
):
    """Reintentar con la misma cédula y mejor foto es el caso más natural."""
    como(citizen).post(SUBMIT_URL, envio(), format="multipart")
    perfil = _rechazar(citizen)
    hmac_original = perfil.document_hmac

    with django_capture_on_commit_callbacks(execute=True):
        respuesta = como(citizen).post(SUBMIT_URL, envio(), format="multipart")

    assert respuesta.status_code == 201
    perfil.refresh_from_db()
    assert perfil.document_hmac == hmac_original


# --------------------------------------------------------------------------- #
# GET /identity/status/
# --------------------------------------------------------------------------- #


def test_status_sin_perfil(como, citizen):
    respuesta = como(citizen).get(STATUS_URL)

    assert respuesta.status_code == 200
    assert respuesta.json() == {
        "has_submission": False,
        "verification_status": "PENDIENTE",
        "reviewed_at": None,
        "rejection_reason": "",
    }


def test_status_pendiente_tras_enviar(como, citizen, envio, entorno):
    como(citizen).post(SUBMIT_URL, envio(), format="multipart")

    cuerpo = como(citizen).get(STATUS_URL).json()

    assert cuerpo["has_submission"] is True
    assert cuerpo["verification_status"] == "PENDIENTE"
    assert cuerpo["reviewed_at"] is None
    assert cuerpo["rejection_reason"] == ""


def test_status_rechazado_muestra_el_motivo(como, citizen, envio, entorno):
    como(citizen).post(SUBMIT_URL, envio(), format="multipart")
    _rechazar(citizen, motivo="La cédula no se lee con claridad.")

    cuerpo = como(citizen).get(STATUS_URL).json()

    assert cuerpo["verification_status"] == "RECHAZADO"
    assert cuerpo["rejection_reason"] == "La cédula no se lee con claridad."
    assert cuerpo["reviewed_at"] is not None


def test_status_aprobado(como, citizen, envio, entorno):
    from django.utils import timezone

    como(citizen).post(SUBMIT_URL, envio(), format="multipart")
    perfil = CitizenProfile.objects.get(user=citizen)
    perfil.verified_at = timezone.now()
    perfil.save(update_fields=["verified_at"])
    citizen.verification_status = VerificationStatus.APROBADO
    citizen.save(update_fields=["verification_status"])

    cuerpo = como(citizen).get(STATUS_URL).json()

    assert cuerpo["verification_status"] == "APROBADO"
    assert cuerpo["rejection_reason"] == ""
    assert cuerpo["reviewed_at"] is not None


def test_status_devuelve_el_conjunto_exacto_de_campos(como, citizen, envio, entorno):
    como(citizen).post(SUBMIT_URL, envio(), format="multipart")

    assert set(como(citizen).get(STATUS_URL).json()) == CAMPOS_STATUS


def test_status_no_expone_submitted_at_ni_updated_at(como, citizen, envio, entorno):
    como(citizen).post(SUBMIT_URL, envio(), format="multipart")

    cuerpo = como(citizen).get(STATUS_URL).json()

    assert "submitted_at" not in cuerpo
    assert "updated_at" not in cuerpo
    assert "created_at" not in cuerpo


# --------------------------------------------------------------------------- #
# Permisos
# --------------------------------------------------------------------------- #


def test_el_envio_anonimo_responde_401(api, envio, entorno):
    assert api.post(SUBMIT_URL, envio(), format="multipart").status_code == 401


def test_el_status_anonimo_responde_401(api):
    assert api.get(STATUS_URL).status_code == 401


@pytest.mark.parametrize("rol", ["operator", "admin"])
def test_ni_operador_ni_admin_pueden_enviar_o_consultar(como, user_factory, envio, entorno, rol):
    user = user_factory(email=f"{rol}-s4@example.com", role=rol)
    cliente = como(user)

    assert cliente.post(SUBMIT_URL, envio(), format="multipart").status_code == 403
    assert cliente.get(STATUS_URL).status_code == 403
    assert entorno.uploads == []


def test_un_superusuario_sin_rol_ciudadano_no_pasa(como, user_factory, envio, entorno):
    from django.contrib.auth import get_user_model

    root = get_user_model().objects.create_superuser(
        email="root-endpoints@example.com", password="clave-segura-123", role=UserRole.ADMIN
    )

    assert como(root).post(SUBMIT_URL, envio(), format="multipart").status_code == 403


def test_un_ciudadano_ya_verificado_puede_consultar_su_estado(como, user_factory):
    verificado = user_factory(
        email="verificado-status@example.com", verification_status=VerificationStatus.APROBADO
    )

    assert como(verificado).get(STATUS_URL).status_code == 200


# --------------------------------------------------------------------------- #
# Throttle
# --------------------------------------------------------------------------- #


def test_la_sexta_peticion_de_envio_responde_429(como, citizen, envio, entorno):
    cliente = como(citizen)
    # Cinco intentos inválidos: el throttle cuenta peticiones, no éxitos.
    for _ in range(5):
        fallido = cliente.post(SUBMIT_URL, envio(document_number="abc"), format="multipart")
        assert fallido.status_code == 400

    assert cliente.post(SUBMIT_URL, envio(), format="multipart").status_code == 429


def test_el_throttle_del_envio_no_afecta_al_status(como, citizen, envio, entorno):
    cliente = como(citizen)
    for _ in range(5):
        cliente.post(SUBMIT_URL, envio(document_number="abc"), format="multipart")

    assert cliente.post(SUBMIT_URL, envio(), format="multipart").status_code == 429
    assert cliente.get(STATUS_URL).status_code == 200


def test_el_throttle_cuenta_por_usuario(como, citizen, other_citizen, envio, entorno):
    cliente = como(citizen)
    for _ in range(5):
        cliente.post(SUBMIT_URL, envio(document_number="abc"), format="multipart")
    assert cliente.post(SUBMIT_URL, envio(), format="multipart").status_code == 429

    assert como(other_citizen).post(SUBMIT_URL, envio(), format="multipart").status_code == 201


# --------------------------------------------------------------------------- #
# Privacidad transversal
# --------------------------------------------------------------------------- #


def test_ninguna_respuesta_expone_datos_sensibles(como, citizen, envio, entorno):
    cliente = como(citizen)
    cuerpos = [cliente.post(SUBMIT_URL, envio(), format="multipart").content.decode()]
    cuerpos.append(cliente.get(STATUS_URL).content.decode())

    perfil = CitizenProfile.objects.get(user=citizen)
    for cuerpo in cuerpos:
        assert CEDULA not in cuerpo
        assert CEDULA_NORMALIZADA not in cuerpo
        assert perfil.document_hmac not in cuerpo
        assert perfil.verification_selfie_url not in cuerpo
        assert "verifications/" not in cuerpo
        assert "supabase" not in cuerpo.lower()
        assert "service-role-de-prueba" not in cuerpo


def test_los_logs_del_envio_no_llevan_la_cedula(como, citizen, envio, entorno, caplog):
    with caplog.at_level(logging.DEBUG):
        como(citizen).post(SUBMIT_URL, envio(), format="multipart")

    assert CEDULA not in caplog.text
    assert CEDULA_NORMALIZADA not in caplog.text
    assert CitizenProfile.objects.get(user=citizen).document_hmac not in caplog.text


# --------------------------------------------------------------------------- #
# Defensa en profundidad: el servicio no delega en la vista
# --------------------------------------------------------------------------- #


def test_el_servicio_exige_rol_ciudadano_aunque_el_permiso_ya_lo_haga(
    operator_user, selfie_factory, jpeg_bytes, entorno
):
    """`IsCitizen` ya lo impide por HTTP; la precondición vive igual en el dominio."""
    with pytest.raises(identity.NotACitizenError):
        identity.submit_verification(
            user=operator_user,
            document_number=CEDULA,
            selfie=selfie_factory(jpeg_bytes),
        )

    assert entorno.uploads == []


def test_la_vista_traduce_el_rol_no_ciudadano_a_403(como, citizen, envio, entorno, monkeypatch):
    """Camino inalcanzable por HTTP, pero la traducción debe estar bien cableada."""

    def rechazar(**kwargs):
        raise identity.NotACitizenError("Solo un ciudadano puede enviar su verificación.")

    monkeypatch.setattr(identity, "submit_verification", rechazar)

    assert como(citizen).post(SUBMIT_URL, envio(), format="multipart").status_code == 403
