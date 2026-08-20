"""Resolución de la cuenta local: casos A-F, concurrencia y `is_active`."""

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.utils import timezone

from apps.auth_app.enums import UserRole, VerificationStatus
from apps.auth_app.models import CitizenProfile
from apps.auth_app.services import accounts as accounts_service
from apps.auth_app.services.google import GoogleIdentity

pytestmark = pytest.mark.django_db

User = get_user_model()

SUB = "115000000000000000001"
OTRO_SUB = "115000000000000000002"


def identidad(sub=SUB, email="nuevo@example.com", full_name="Nombre Apellido", hd=""):
    """Identidad ya verificada. Por defecto, un email **externo** y sin `hd`."""
    return GoogleIdentity(sub=sub, email=email, full_name=full_name, hosted_domain=hd)


def identidad_gmail(sub=SUB, email="persona@gmail.com", **kwargs):
    """Cuenta `@gmail.com`: Google administra la dirección."""
    return identidad(sub=sub, email=email, **kwargs)


def identidad_workspace(sub=SUB, email="persona@empresa.com", hd="empresa.com", **kwargs):
    """Cuenta de Google Workspace: el claim `hd` acredita el dominio."""
    return identidad(sub=sub, email=email, hd=hd, **kwargs)


# --------------------------------------------------------------------------- #
# Caso A — primer login
# --------------------------------------------------------------------------- #


def test_caso_a_primer_login_crea_ciudadano_pendiente():
    user = accounts_service.resolve_user(identidad())

    assert user.google_oauth_id == SUB
    assert user.email == "nuevo@example.com"
    assert user.full_name == "Nombre Apellido"
    assert user.role == UserRole.CITIZEN
    assert user.verification_status == VerificationStatus.PENDIENTE
    assert User.objects.count() == 1


def test_caso_a_la_contrasena_queda_inusable():
    user = accounts_service.resolve_user(identidad())

    assert user.has_usable_password() is False


def test_caso_a_admite_email_externo_sin_hd():
    """Crear no es apropiarse: no hay identidad local preexistente que proteger."""
    user = accounts_service.resolve_user(identidad(email="persona@example.com"))

    assert user.google_oauth_id == SUB
    assert user.email == "persona@example.com"
    assert User.objects.count() == 1


def test_caso_a_no_crea_perfil_de_ciudadano():
    """D2-06: el perfil nace al enviar la verificación de identidad (Sprint 4)."""
    accounts_service.resolve_user(identidad())

    assert CitizenProfile.objects.count() == 0


# --------------------------------------------------------------------------- #
# Caso B — mismo sub
# --------------------------------------------------------------------------- #


def test_caso_b_segundo_login_reutiliza_el_usuario():
    primero = accounts_service.resolve_user(identidad())
    segundo = accounts_service.resolve_user(identidad())

    assert primero.pk == segundo.pk
    assert User.objects.count() == 1


def test_caso_b_no_modifica_rol_verificacion_ni_nombre(user_factory):
    user = user_factory(
        email="operador2@example.com",
        role=UserRole.OPERATOR,
        verification_status=VerificationStatus.APROBADO,
        full_name="Nombre Corregido A Mano",
        google_oauth_id=SUB,
    )

    resuelto = accounts_service.resolve_user(
        identidad(email="operador2@example.com", full_name="Nombre Que Manda Google")
    )
    resuelto.refresh_from_db()

    assert resuelto.pk == user.pk
    assert resuelto.role == UserRole.OPERATOR
    assert resuelto.verification_status == VerificationStatus.APROBADO
    assert resuelto.full_name == "Nombre Corregido A Mano"


# --------------------------------------------------------------------------- #
# Caso C — email existente sin identidad de Google
# --------------------------------------------------------------------------- #


def test_caso_c_gmail_vincula_la_identidad_a_la_cuenta_existente(user_factory):
    """Escenario real: un operador creado por consola antes de su primer login."""
    user = user_factory(email="operador@gmail.com", role=UserRole.OPERATOR)
    assert user.google_oauth_id is None

    resuelto = accounts_service.resolve_user(identidad_gmail(email="operador@gmail.com"))
    resuelto.refresh_from_db()

    assert resuelto.pk == user.pk
    assert resuelto.google_oauth_id == SUB
    assert resuelto.role == UserRole.OPERATOR
    assert User.objects.count() == 1


def test_caso_c_workspace_vincula_por_el_claim_hd(user_factory):
    """Cuenta de Google Workspace: `hd` acredita que Google administra el dominio."""
    user = user_factory(email="operador@empresa.com", role=UserRole.OPERATOR)

    resuelto = accounts_service.resolve_user(
        identidad_workspace(email="operador@empresa.com", hd="empresa.com")
    )
    resuelto.refresh_from_db()

    assert resuelto.pk == user.pk
    assert resuelto.google_oauth_id == SUB
    assert User.objects.count() == 1


def test_caso_c_email_externo_sin_hd_no_se_vincula(user_factory):
    """`email_verified` no basta para apropiarse de una cuenta local preexistente.

    Con un email externo, Google verificó la dirección alguna vez pero no es autoridad
    sobre quién la controla hoy.
    """
    user = user_factory(email="operador@example.com", role=UserRole.OPERATOR)

    with pytest.raises(accounts_service.AccountConflictError):
        accounts_service.resolve_user(identidad(email="operador@example.com"))

    user.refresh_from_db()
    assert user.google_oauth_id is None
    assert user.role == UserRole.OPERATOR
    assert User.objects.count() == 1  # tampoco se crea un segundo usuario


def test_caso_c_gmail_vincula_ignorando_diferencias_de_mayusculas(user_factory):
    user = user_factory(email="Persona@Gmail.com")

    resuelto = accounts_service.resolve_user(identidad_gmail(email="persona@gmail.com"))

    assert resuelto.pk == user.pk
    assert User.objects.count() == 1


def test_caso_c_no_modifica_full_name_ni_estando_vacio(user_factory):
    """W-9: el claim `name` solo se usa al **crear** un usuario nuevo."""
    user_factory(email="sinnombre@gmail.com", full_name="")

    resuelto = accounts_service.resolve_user(
        identidad_gmail(email="sinnombre@gmail.com", full_name="Nombre De Google")
    )
    resuelto.refresh_from_db()

    assert resuelto.full_name == ""


def test_caso_c_no_pisa_un_nombre_corregido_a_mano(user_factory):
    user_factory(email="operador@gmail.com", full_name="Nombre Corregido A Mano")

    resuelto = accounts_service.resolve_user(
        identidad_gmail(email="operador@gmail.com", full_name="Nombre Que Manda Google")
    )
    resuelto.refresh_from_db()

    assert resuelto.full_name == "Nombre Corregido A Mano"


def test_caso_c_no_toca_rol_ni_verificacion_ni_flags_de_django(user_factory):
    user = user_factory(
        email="operador@gmail.com",
        role=UserRole.OPERATOR,
        verification_status=VerificationStatus.APROBADO,
    )

    resuelto = accounts_service.resolve_user(identidad_gmail(email="operador@gmail.com"))
    resuelto.refresh_from_db()

    assert resuelto.pk == user.pk
    assert resuelto.role == UserRole.OPERATOR
    assert resuelto.verification_status == VerificationStatus.APROBADO
    assert resuelto.is_staff is False
    assert resuelto.is_superuser is False


# --------------------------------------------------------------------------- #
# Caso D — email vinculado a otra identidad
# --------------------------------------------------------------------------- #


def test_caso_d_email_vinculado_a_otro_sub_se_rechaza(user_factory):
    user_factory(email="ocupado@example.com", google_oauth_id=OTRO_SUB)

    with pytest.raises(accounts_service.AccountConflictError):
        accounts_service.resolve_user(identidad(email="ocupado@example.com"))

    assert User.objects.count() == 1


def test_caso_d_no_fusiona_cuentas(user_factory):
    existente = user_factory(email="ocupado@example.com", google_oauth_id=OTRO_SUB)

    with pytest.raises(accounts_service.AccountConflictError):
        accounts_service.resolve_user(identidad(email="ocupado@example.com"))

    existente.refresh_from_db()
    assert existente.google_oauth_id == OTRO_SUB


# --------------------------------------------------------------------------- #
# Caso E — email nuevo para una identidad conocida
# --------------------------------------------------------------------------- #


def test_caso_e_email_libre_se_actualiza(user_factory):
    user = user_factory(email="viejo@example.com", google_oauth_id=SUB)

    resuelto = accounts_service.resolve_user(identidad(email="nuevo@example.com"))
    resuelto.refresh_from_db()

    assert resuelto.pk == user.pk
    assert resuelto.email == "nuevo@example.com"


def test_caso_e_email_ocupado_por_otro_usuario_se_rechaza(user_factory):
    user = user_factory(email="viejo@example.com", google_oauth_id=SUB)
    user_factory(email="ocupado@example.com")

    with pytest.raises(accounts_service.AccountConflictError):
        accounts_service.resolve_user(identidad(email="ocupado@example.com"))

    user.refresh_from_db()
    assert user.email == "viejo@example.com"


def test_caso_e_solo_cambio_de_capitalizacion_no_es_conflicto(user_factory):
    user = user_factory(email="Persona@example.com", google_oauth_id=SUB)

    resuelto = accounts_service.resolve_user(identidad(email="persona@example.com"))

    assert resuelto.pk == user.pk


# --------------------------------------------------------------------------- #
# Email sensible a mayúsculas: limitación estructural documentada
# --------------------------------------------------------------------------- #


def test_duplicado_preexistente_por_capitalizacion_da_conflicto(user_factory):
    """El índice único de `users.email` es sensible a mayúsculas (ADR-0005).

    Si ya existieran dos cuentas que solo difieren en capitalización, el servicio no
    elige una arbitrariamente: exige intervención humana.
    """
    user_factory(email="Persona@example.com")
    user_factory(email="persona@example.com")

    with pytest.raises(accounts_service.AccountConflictError):
        accounts_service.resolve_user(identidad(email="persona@example.com"))


# --------------------------------------------------------------------------- #
# Concurrencia
# --------------------------------------------------------------------------- #


def test_conflicto_de_unicidad_se_reintenta_una_vez(monkeypatch):
    """Simula la carrera: la primera pasada falla, el reintento encuentra el usuario.

    Comprueba además que la transacción rota no se reutiliza: si el reintento
    ocurriera dentro del `atomic` fallido, Django levantaría
    `TransactionManagementError`.
    """
    original = accounts_service._resolve
    llamadas = {"n": 0}

    def flaky(identity):
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            User.objects.create_user(
                email=identity.email, password=None, google_oauth_id=identity.sub
            )
            raise IntegrityError("duplicate key value violates unique constraint")
        return original(identity)

    monkeypatch.setattr(accounts_service, "_resolve", flaky)

    user = accounts_service.resolve_user(identidad())

    assert llamadas["n"] == 2
    assert user.google_oauth_id == SUB
    assert User.objects.count() == 1


def test_dos_resoluciones_del_mismo_sub_producen_un_solo_usuario():
    accounts_service.resolve_user(identidad())
    accounts_service.resolve_user(identidad())

    assert User.objects.filter(google_oauth_id=SUB).count() == 1
    assert User.objects.count() == 1


# --------------------------------------------------------------------------- #
# login_with_google: cuenta inactiva y last_login
# --------------------------------------------------------------------------- #


def test_cuenta_inactiva_no_inicia_sesion(user_factory):
    user_factory(email="inactivo@example.com", google_oauth_id=SUB, is_active=False)

    with pytest.raises(accounts_service.InactiveAccountError):
        accounts_service.login_with_google(identidad(email="inactivo@example.com"))


def test_cuenta_inactiva_no_se_reactiva(user_factory):
    user = user_factory(email="inactivo@example.com", google_oauth_id=SUB, is_active=False)

    with pytest.raises(accounts_service.InactiveAccountError):
        accounts_service.login_with_google(identidad(email="inactivo@example.com"))

    user.refresh_from_db()
    assert user.is_active is False


def test_login_actualiza_last_login():
    antes = timezone.now()

    user = accounts_service.login_with_google(identidad())
    user.refresh_from_db()

    assert user.last_login is not None
    assert user.last_login >= antes
