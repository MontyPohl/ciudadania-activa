"""Servicio de revisión administrativa: efectos exactos, atomicidad y concurrencia.

Estas pruebas atacan el servicio directamente, sin pasar por HTTP: las reglas viven ahí
y deben sostenerse aunque se les llame desde una consola.

La garantía central es que **una identidad se revisa una sola vez**. Se prueba de dos
maneras: de forma determinista, forzando la ventana entre la lectura y la escritura, y
de forma realista, con dos administradores compitiendo de verdad contra PostgreSQL.
"""

import logging
import threading
import uuid

import pytest
from django.core import mail
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext

from apps.admin_app.models import AuditLog
from apps.admin_app.services import identity_review
from apps.auth_app.enums import VerificationStatus
from apps.auth_app.models import CitizenProfile
from apps.notifications_app.enums import NotificationStatus, NotificationType
from apps.notifications_app.models import Notification

ESPERA_MAXIMA = 15
MOTIVO = "La cédula está tapada en la foto."


@pytest.fixture(autouse=True)
def bandeja_limpia():
    mail.outbox.clear()
    yield
    mail.outbox.clear()


@pytest.fixture
def perfil(citizen):
    return CitizenProfile.objects.create(
        user=citizen,
        document_hmac="c" * 64,
        verification_selfie_url=f"verifications/{citizen.pk}/selfie.jpg",
    )


# --------------------------------------------------------------------------- #
# Efectos de la aprobación
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_aprobar_fija_los_campos_exactos(perfil, admin_user, citizen):
    resultado = identity_review.review(
        perfil.pk, decision=VerificationStatus.APROBADO, actor=admin_user
    )

    assert resultado.pk == perfil.pk
    perfil.refresh_from_db()
    citizen.refresh_from_db()
    assert citizen.verification_status == VerificationStatus.APROBADO
    assert perfil.verified_at is not None
    assert perfil.verified_by == admin_user
    assert perfil.rejection_reason == ""


@pytest.mark.django_db
def test_aprobar_descarta_el_motivo_recibido(perfil, admin_user):
    identity_review.review(
        perfil.pk, decision=VerificationStatus.APROBADO, reason="no aplica", actor=admin_user
    )

    perfil.refresh_from_db()
    assert perfil.rejection_reason == ""


@pytest.mark.django_db
def test_aprobar_audita_con_el_payload_minimo(perfil, admin_user, citizen):
    identity_review.review(perfil.pk, decision=VerificationStatus.APROBADO, actor=admin_user)

    entrada = AuditLog.objects.get(action=identity_review.ACCION_APROBACION)
    assert entrada.actor_user == admin_user
    assert entrada.payload == {
        "profile_id": str(perfil.pk),
        "target_user_id": str(citizen.pk),
    }
    assert "reason" not in entrada.payload


@pytest.mark.django_db
def test_aprobar_crea_exactamente_una_notificacion(perfil, admin_user, citizen):
    identity_review.review(perfil.pk, decision=VerificationStatus.APROBADO, actor=admin_user)

    notificacion = Notification.objects.get()
    assert notificacion.user == citizen
    assert notificacion.notification_type == NotificationType.VERIFICACION_APROBADA
    assert notificacion.report is None


# --------------------------------------------------------------------------- #
# Efectos del rechazo
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_rechazar_fija_los_campos_exactos(perfil, admin_user, citizen):
    identity_review.review(
        perfil.pk, decision=VerificationStatus.RECHAZADO, reason=f"  {MOTIVO}  ", actor=admin_user
    )

    perfil.refresh_from_db()
    citizen.refresh_from_db()
    assert citizen.verification_status == VerificationStatus.RECHAZADO
    assert perfil.verified_at is not None
    assert perfil.verified_by == admin_user
    assert perfil.rejection_reason == MOTIVO


@pytest.mark.django_db
def test_rechazar_audita_con_el_motivo(perfil, admin_user, citizen):
    """§30 exige registrar la revisión «con actor y motivo»."""
    identity_review.review(
        perfil.pk, decision=VerificationStatus.RECHAZADO, reason=MOTIVO, actor=admin_user
    )

    entrada = AuditLog.objects.get(action=identity_review.ACCION_RECHAZO)
    assert entrada.payload == {
        "profile_id": str(perfil.pk),
        "target_user_id": str(citizen.pk),
        "reason": MOTIVO,
    }


@pytest.mark.django_db
def test_rechazar_crea_la_notificacion_de_rechazo(perfil, admin_user):
    identity_review.review(
        perfil.pk, decision=VerificationStatus.RECHAZADO, reason=MOTIVO, actor=admin_user
    )

    assert Notification.objects.get().notification_type == (NotificationType.VERIFICACION_RECHAZADA)


@pytest.mark.django_db
@pytest.mark.parametrize("motivo", ["", "   ", None])
def test_rechazar_sin_motivo_es_invalido(perfil, admin_user, motivo):
    with pytest.raises(identity_review.InvalidReviewError):
        identity_review.review(
            perfil.pk, decision=VerificationStatus.RECHAZADO, reason=motivo, actor=admin_user
        )

    perfil.user.refresh_from_db()
    assert perfil.user.verification_status == VerificationStatus.PENDIENTE


# --------------------------------------------------------------------------- #
# La auditoría no lleva nada más
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("decision", "reason"),
    [(VerificationStatus.APROBADO, ""), (VerificationStatus.RECHAZADO, MOTIVO)],
)
def test_la_auditoria_no_lleva_datos_sensibles(perfil, admin_user, citizen, decision, reason):
    identity_review.review(perfil.pk, decision=decision, reason=reason, actor=admin_user)

    texto = str(AuditLog.objects.get().payload)
    assert perfil.document_hmac not in texto
    assert perfil.verification_selfie_url not in texto
    assert "verifications/" not in texto
    assert citizen.email not in texto
    assert admin_user.email not in texto
    assert "token=" not in texto


# --------------------------------------------------------------------------- #
# Entradas inválidas y estados que no admiten revisión
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
@pytest.mark.parametrize("decision", ["PENDIENTE", "aprobado", "", None])
def test_una_decision_invalida_no_escribe_nada(perfil, admin_user, decision):
    with pytest.raises(identity_review.InvalidReviewError):
        identity_review.review(perfil.pk, decision=decision, actor=admin_user)

    assert not AuditLog.objects.exists()
    assert not Notification.objects.exists()


@pytest.mark.django_db
def test_revisar_una_identidad_inexistente(admin_user):
    with pytest.raises(CitizenProfile.DoesNotExist):
        identity_review.review(uuid.uuid4(), decision=VerificationStatus.APROBADO, actor=admin_user)


@pytest.mark.django_db
@pytest.mark.parametrize("ya", [VerificationStatus.APROBADO, VerificationStatus.RECHAZADO])
def test_una_identidad_ya_revisada_no_se_revisa_de_nuevo(perfil, admin_user, citizen, ya):
    citizen.verification_status = ya
    citizen.save(update_fields=["verification_status"])

    with pytest.raises(identity_review.AlreadyReviewedError):
        identity_review.review(perfil.pk, decision=VerificationStatus.APROBADO, actor=admin_user)

    assert not AuditLog.objects.exists()
    assert not Notification.objects.exists()


# --------------------------------------------------------------------------- #
# Atomicidad
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_un_conflicto_no_deja_auditoria_ni_notificacion(perfil, admin_user, citizen):
    """La decisión, su auditoría y su aviso nacen o mueren juntos."""
    citizen.verification_status = VerificationStatus.APROBADO
    citizen.save(update_fields=["verification_status"])

    with pytest.raises(identity_review.AlreadyReviewedError):
        identity_review.review(
            perfil.pk, decision=VerificationStatus.RECHAZADO, reason=MOTIVO, actor=admin_user
        )

    perfil.refresh_from_db()
    assert perfil.verified_at is None
    assert perfil.verified_by is None
    assert Notification.objects.count() == 0
    assert AuditLog.objects.count() == 0


@pytest.mark.django_db
def test_si_la_transaccion_se_deshace_no_se_encola_nada(perfil, admin_user, monkeypatch):
    """El envío se registra con `on_commit`: sin commit, no hay aviso."""
    encolados = []
    monkeypatch.setattr(identity_review, "_encolar_envio", encolados.append)

    with pytest.raises(RuntimeError), transaction.atomic():
        identity_review.review(perfil.pk, decision=VerificationStatus.APROBADO, actor=admin_user)
        raise RuntimeError("algo falla después de revisar")

    assert encolados == []
    assert Notification.objects.count() == 0
    assert AuditLog.objects.count() == 0
    perfil.user.refresh_from_db()
    assert perfil.user.verification_status == VerificationStatus.PENDIENTE


@pytest.mark.django_db
def test_el_envio_se_encola_al_confirmar(perfil, admin_user, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        identity_review.review(perfil.pk, decision=VerificationStatus.APROBADO, actor=admin_user)
        assert mail.outbox == []  # todavía nada: está encolado, no enviado

    assert len(callbacks) == 1
    assert len(mail.outbox) == 1
    assert Notification.objects.get().status == NotificationStatus.ENVIADO


# --------------------------------------------------------------------------- #
# Bloqueo explícito de las dos filas
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_el_lock_alcanza_a_citizen_profiles_y_a_users(perfil, admin_user):
    """`of=("self", "user")`: las dos tablas deben aparecer en el FOR UPDATE emitido."""
    with CaptureQueriesContext(connection) as consultas:
        identity_review.review(perfil.pk, decision=VerificationStatus.APROBADO, actor=admin_user)

    bloqueos = [c["sql"] for c in consultas.captured_queries if "FOR UPDATE" in c["sql"]]

    assert len(bloqueos) == 1, bloqueos
    sql = bloqueos[0]
    assert "FOR UPDATE OF" in sql
    assert '"citizen_profiles"' in sql
    assert '"users"' in sql


@pytest.mark.django_db
def test_el_estado_se_relee_dentro_del_lock(perfil, admin_user, citizen, monkeypatch):
    """Determinista: se simula que el estado cambió después de la lectura previa.

    Se sustituye el bloqueo por uno que, tras adquirirlo, devuelve el perfil con el
    estado ya modificado por «otra petición». Si la relectura no gobernase la decisión,
    la revisión prosperaría y pisaría a quien se adelantó.
    """
    original = identity_review._bloquear

    def bloquear_y_adelantarse(profile_id):
        bloqueado = original(profile_id)
        # Otra transacción confirmó una revisión entre la lectura previa y este punto.
        type(citizen).objects.filter(pk=citizen.pk).update(
            verification_status=VerificationStatus.APROBADO
        )
        bloqueado.user.refresh_from_db()
        return bloqueado

    monkeypatch.setattr(identity_review, "_bloquear", bloquear_y_adelantarse)

    with pytest.raises(identity_review.AlreadyReviewedError):
        identity_review.review(
            perfil.pk, decision=VerificationStatus.RECHAZADO, reason=MOTIVO, actor=admin_user
        )

    perfil.refresh_from_db()
    assert perfil.verified_by is None
    assert perfil.rejection_reason == ""
    assert AuditLog.objects.count() == 0
    assert Notification.objects.count() == 0


# --------------------------------------------------------------------------- #
# Fallo al encolar
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_encolar_envio_absorbe_el_fallo_del_broker(perfil, admin_user, monkeypatch, caplog):
    from apps.notifications_app import tasks
    from apps.notifications_app.services import dispatch

    notificacion = dispatch.create(perfil.user, NotificationType.VERIFICACION_APROBADA)
    monkeypatch.setattr(
        tasks.send_notification,
        "delay",
        lambda *a, **k: (_ for _ in ()).throw(ConnectionError("redis://:clave@host:6379")),
    )

    with caplog.at_level(logging.DEBUG):
        identity_review._encolar_envio(notificacion.pk)  # no debe levantar

    notificacion.refresh_from_db()
    assert notificacion.status == NotificationStatus.FALLIDO
    assert "clave@host" not in caplog.text
    assert "ConnectionError" in caplog.text


@pytest.mark.django_db
def test_encolar_envio_absorbe_incluso_el_fallo_al_marcar_fallida(monkeypatch, caplog):
    """Último recurso: ni el broker caído ni la base caída pueden llegar al endpoint."""
    from apps.notifications_app import tasks
    from apps.notifications_app.models import Notification as ModeloNotificacion

    class ManagerRoto:
        def filter(self, **kwargs):
            raise RuntimeError("la base no responde")

    monkeypatch.setattr(
        tasks.send_notification, "delay", lambda *a, **k: (_ for _ in ()).throw(ConnectionError())
    )
    monkeypatch.setattr(ModeloNotificacion, "objects", ManagerRoto())

    with caplog.at_level(logging.DEBUG):
        identity_review._encolar_envio(uuid.uuid4())  # no debe levantar

    assert "la base no responde" not in caplog.text
    assert "RuntimeError" in caplog.text


# --------------------------------------------------------------------------- #
# Dos administradores concurrentes
# --------------------------------------------------------------------------- #


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_dos_admins_concurrentes_dejan_una_sola_decision(user_factory):
    """Uno obtiene su decisión aplicada; el otro, conflicto. Nunca las dos.

    Cada administrador pide una decisión **distinta**, de modo que si ambas se
    aplicaran el estado final lo delataría.
    """
    from apps.auth_app.enums import UserRole

    citizen = user_factory(email="revisado-concurrente@example.com")
    perfil = CitizenProfile.objects.create(
        user=citizen,
        document_hmac="d" * 64,
        verification_selfie_url=f"verifications/{citizen.pk}/selfie.jpg",
    )
    admins = {
        "a": user_factory(email="admin-a@example.com", role=UserRole.ADMIN),
        "b": user_factory(email="admin-b@example.com", role=UserRole.ADMIN),
    }
    decisiones = {"a": VerificationStatus.APROBADO, "b": VerificationStatus.RECHAZADO}

    barrera = threading.Barrier(2, timeout=ESPERA_MAXIMA)
    resultados: dict[str, str] = {}

    def revisar(nombre: str) -> None:
        try:
            actor = type(admins[nombre]).objects.get(pk=admins[nombre].pk)
            barrera.wait()
            identity_review.review(
                perfil.pk,
                decision=decisiones[nombre],
                reason=MOTIVO,
                actor=actor,
            )
            resultados[nombre] = "exito"
        except identity_review.AlreadyReviewedError:
            resultados[nombre] = "conflicto"
        except Exception as exc:  # pragma: no cover - solo si algo se rompe de verdad
            resultados[nombre] = f"inesperado: {exc!r}"
        finally:
            connection.close()

    hilos = [threading.Thread(target=revisar, args=(nombre,)) for nombre in ("a", "b")]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=ESPERA_MAXIMA)
        assert not hilo.is_alive(), "Un hilo quedó bloqueado: posible interbloqueo."

    assert sorted(resultados.values()) == ["conflicto", "exito"], resultados

    ganador = "a" if resultados["a"] == "exito" else "b"
    citizen.refresh_from_db()
    perfil.refresh_from_db()

    assert citizen.verification_status == decisiones[ganador]
    assert perfil.verified_by_id == admins[ganador].pk
    assert perfil.verified_at is not None

    # Un solo rastro de cada cosa.
    assert AuditLog.objects.count() == 1
    assert AuditLog.objects.get().action == (
        identity_review.ACCION_APROBACION
        if decisiones[ganador] == VerificationStatus.APROBADO
        else identity_review.ACCION_RECHAZO
    )
    assert Notification.objects.count() == 1
    assert len(mail.outbox) == 1
