"""Comando de promoción de roles: única vía de asignación de `operator` y `admin`."""

import pytest
from django.core.management import CommandError, call_command
from django.db import transaction

from apps.admin_app.models import AuditLog, Institution, InstitutionOperator
from apps.auth_app.enums import UserRole, VerificationStatus
from apps.core.enums import ActorType

pytestmark = pytest.mark.django_db


def promover(email, role, institution=None):
    argumentos = [email, "--role", role]
    if institution:
        argumentos += ["--institution", institution]
    call_command("promote_user", *argumentos, verbosity=0)


# --------------------------------------------------------------------------- #
# operator
# --------------------------------------------------------------------------- #


def test_promueve_a_operador_creando_el_vinculo(citizen, institution):
    promover(citizen.email, UserRole.OPERATOR, institution.slug)
    citizen.refresh_from_db()

    assert citizen.role == UserRole.OPERATOR
    vinculo = InstitutionOperator.objects.get(user=citizen, institution=institution)
    assert vinculo.active is True


def test_reactiva_un_vinculo_desactivado(citizen, institution):
    InstitutionOperator.objects.create(user=citizen, institution=institution, active=False)

    promover(citizen.email, UserRole.OPERATOR, institution.slug)

    assert InstitutionOperator.objects.get(user=citizen, institution=institution).active is True
    assert InstitutionOperator.objects.filter(user=citizen).count() == 1


def test_es_idempotente(citizen, institution):
    promover(citizen.email, UserRole.OPERATOR, institution.slug)
    promover(citizen.email, UserRole.OPERATOR, institution.slug)

    assert InstitutionOperator.objects.filter(user=citizen).count() == 1
    assert AuditLog.objects.filter(action="user.role_promoted").count() == 2


def test_conserva_los_vinculos_previos_de_otras_instituciones(citizen, institution):
    otra = Institution.objects.get(slug="ande")
    promover(citizen.email, UserRole.OPERATOR, institution.slug)
    promover(citizen.email, UserRole.OPERATOR, otra.slug)

    activos = InstitutionOperator.objects.filter(user=citizen, active=True)
    assert activos.count() == 2


def test_operador_sin_institucion_es_error(citizen):
    with pytest.raises(CommandError):
        promover(citizen.email, UserRole.OPERATOR)

    citizen.refresh_from_db()
    assert citizen.role == UserRole.CITIZEN


def test_institucion_inexistente_es_error(citizen):
    with pytest.raises(CommandError):
        promover(citizen.email, UserRole.OPERATOR, "no-existe")

    assert InstitutionOperator.objects.count() == 0


def test_el_email_se_busca_sin_distinguir_mayusculas(citizen, institution):
    promover(citizen.email.upper(), UserRole.OPERATOR, institution.slug)
    citizen.refresh_from_db()

    assert citizen.role == UserRole.OPERATOR


# --------------------------------------------------------------------------- #
# admin
# --------------------------------------------------------------------------- #


def test_promueve_a_admin_sin_institucion(citizen):
    promover(citizen.email, UserRole.ADMIN)
    citizen.refresh_from_db()

    assert citizen.role == UserRole.ADMIN
    assert InstitutionOperator.objects.filter(user=citizen).count() == 0


def test_admin_no_recibe_flags_de_django(citizen):
    promover(citizen.email, UserRole.ADMIN)
    citizen.refresh_from_db()

    assert citizen.is_staff is False
    assert citizen.is_superuser is False


def test_admin_con_institucion_es_combinacion_invalida(citizen, institution):
    with pytest.raises(CommandError):
        promover(citizen.email, UserRole.ADMIN, institution.slug)

    citizen.refresh_from_db()
    assert citizen.role == UserRole.CITIZEN


# --------------------------------------------------------------------------- #
# Reglas transversales
# --------------------------------------------------------------------------- #


def test_el_comando_no_crea_usuarios():
    from django.contrib.auth import get_user_model

    with pytest.raises(CommandError):
        promover("inexistente@example.com", UserRole.ADMIN)

    assert get_user_model().objects.filter(email="inexistente@example.com").count() == 0


def test_no_modifica_el_estado_de_verificacion(citizen, institution):
    promover(citizen.email, UserRole.OPERATOR, institution.slug)
    citizen.refresh_from_db()

    assert citizen.verification_status == VerificationStatus.PENDIENTE


# --------------------------------------------------------------------------- #
# Auditoría
# --------------------------------------------------------------------------- #


def test_audita_la_promocion_como_accion_del_sistema(citizen, institution):
    promover(citizen.email, UserRole.OPERATOR, institution.slug)

    entrada = AuditLog.objects.get(action="user.role_promoted")
    assert entrada.actor_user is None
    assert entrada.actor_type == ActorType.SYSTEM
    assert entrada.payload == {
        "target_user_id": str(citizen.pk),
        "new_role": UserRole.OPERATOR,
        "source": "management_command",
        "institution_id": str(institution.pk),
        "institution_slug": institution.slug,
    }


def test_la_auditoria_de_admin_no_incluye_institucion(citizen):
    promover(citizen.email, UserRole.ADMIN)

    payload = AuditLog.objects.get(action="user.role_promoted").payload
    assert "institution_id" not in payload
    assert "institution_slug" not in payload


def test_la_auditoria_no_guarda_datos_sensibles(citizen, institution):
    promover(citizen.email, UserRole.OPERATOR, institution.slug)

    payload = AuditLog.objects.get(action="user.role_promoted").payload
    serializado = str(payload)
    assert citizen.email not in serializado
    for prohibido in ("google", "token", "password"):
        assert prohibido not in serializado.lower()


def test_el_cambio_de_rol_y_la_auditoria_comparten_transaccion(citizen, institution, monkeypatch):
    """Si la auditoría falla, el rol no debe quedar cambiado."""
    from apps.core.services import audit

    def falla(*args, **kwargs):
        raise RuntimeError("fallo simulado de auditoría")

    monkeypatch.setattr(audit, "log", falla)

    with pytest.raises(RuntimeError), transaction.atomic():
        promover(citizen.email, UserRole.OPERATOR, institution.slug)

    citizen.refresh_from_db()
    assert citizen.role == UserRole.CITIZEN
    assert InstitutionOperator.objects.count() == 0
