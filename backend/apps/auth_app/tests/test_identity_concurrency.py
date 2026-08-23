"""Concurrencia del reenvío tras rechazo.

La garantía que se prueba aquí es una sola: **el estado autoritativo es el que se relee
dentro del lock**, no el que se leyó antes. Sin eso, dos reenvíos simultáneos podrían
pasar los dos la comprobación previa y el segundo pisaría el trabajo del primero.

Se prueba de dos maneras complementarias:

- de forma **determinista**, forzando exactamente la ventana entre la comprobación
  previa y la adquisición del lock, sin hilos ni azar;
- de forma **realista**, con dos hilos que compiten de verdad contra PostgreSQL.
"""

import threading

import pytest
from django.db import connection
from django.utils import timezone

from apps.admin_app.models import AuditLog
from apps.auth_app.enums import VerificationStatus
from apps.auth_app.models import CitizenProfile
from apps.auth_app.services import identity

CEDULA_INICIAL = "1234567"
CEDULA_A = "2222222"
CEDULA_B = "3333333"

ESPERA_MAXIMA = 15


def _dejar_rechazado(user, clave="verifications/previa/anterior.jpg"):
    perfil = CitizenProfile.objects.create(
        user=user,
        document_hmac=identity.compute_document_hmac(CEDULA_INICIAL),
        verification_selfie_url=clave,
        verified_at=timezone.now(),
        rejection_reason="La imagen no permite leer el documento.",
    )
    user.verification_status = VerificationStatus.RECHAZADO
    user.save(update_fields=["verification_status"])
    return perfil


# --------------------------------------------------------------------------- #
# Determinista: la comprobación previa no es la autoridad
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_el_lock_relee_el_estado_y_rechaza_si_ya_cambio(
    citizen,
    selfie_factory,
    jpeg_bytes,
    document_hmac_secret,
    storage_config,
    fake_storage,
    monkeypatch,
):
    """Simula la ventana exacta entre la comprobación previa y el lock.

    Se neutraliza la comprobación previa —como si en ese instante el estado todavía
    fuera RECHAZADO— y se deja el estado real en PENDIENTE, que es lo que habría hecho
    otra petición al adelantarse. Si la relectura bajo lock no existiera, el reenvío
    prosperaría y pisaría el envío del competidor.
    """
    perfil = _dejar_rechazado(citizen)
    hmac_anterior = perfil.document_hmac

    monkeypatch.setattr(identity, "_asegurar_estado_admite_envio", lambda user, perfil: None)
    citizen.verification_status = VerificationStatus.PENDIENTE
    citizen.save(update_fields=["verification_status"])

    with pytest.raises(identity.VerificationConflictError):
        identity.submit_verification(
            user=citizen,
            document_number=CEDULA_A,
            selfie=selfie_factory(jpeg_bytes, name="reintento.jpg"),
        )

    perfil.refresh_from_db()
    assert perfil.document_hmac == hmac_anterior
    assert perfil.verification_selfie_url == "verifications/previa/anterior.jpg"
    assert not AuditLog.objects.filter(action=identity.ACCION_REENVIO).exists()


@pytest.mark.django_db
def test_la_carrera_perdida_borra_el_objeto_nuevo_y_conserva_el_anterior(
    citizen,
    selfie_factory,
    jpeg_bytes,
    document_hmac_secret,
    storage_config,
    fake_storage,
    monkeypatch,
):
    _dejar_rechazado(citizen)
    monkeypatch.setattr(identity, "_asegurar_estado_admite_envio", lambda user, perfil: None)
    citizen.verification_status = VerificationStatus.PENDIENTE
    citizen.save(update_fields=["verification_status"])

    with pytest.raises(identity.VerificationConflictError):
        identity.submit_verification(
            user=citizen,
            document_number=CEDULA_A,
            selfie=selfie_factory(jpeg_bytes, name="reintento.jpg"),
        )

    assert len(fake_storage.uploads) == 1
    assert len(fake_storage.deletes) == 1
    assert fake_storage.deletes[0]["key"] == fake_storage.uploads[0]["key"]
    assert fake_storage.deletes[0]["key"] != "verifications/previa/anterior.jpg"


# --------------------------------------------------------------------------- #
# Realista: dos hilos contra PostgreSQL
# --------------------------------------------------------------------------- #


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_dos_reenvios_concurrentes_dejan_una_sola_actualizacion(
    user_factory, selfie_factory, jpeg_bytes, document_hmac_secret, storage_config, fake_storage
):
    """Uno gana, el otro recibe conflicto. Nunca dos actualizaciones válidas.

    Las dos peticiones llevan cédulas **distintas** a propósito: así el conflicto no
    puede venir del índice único de `document_hmac`, sino de la relectura del estado
    bajo el lock, que es lo que se quiere probar.
    """
    citizen = user_factory(email="carrera@example.com")
    _dejar_rechazado(citizen)

    barrera = threading.Barrier(2, timeout=ESPERA_MAXIMA)
    resultados: dict[str, str] = {}

    def intentar(nombre: str, cedula: str) -> None:
        try:
            # Cada hilo carga su propia instancia: compartir modelos entre hilos
            # escondería justamente la carrera que se quiere observar.
            usuario = type(citizen).objects.get(pk=citizen.pk)
            barrera.wait()
            identity.submit_verification(
                user=usuario,
                document_number=cedula,
                selfie=selfie_factory(jpeg_bytes, name=f"{nombre}.jpg"),
            )
            resultados[nombre] = "exito"
        except identity.VerificationConflictError:
            resultados[nombre] = "conflicto"
        except Exception as exc:  # pragma: no cover - solo si algo se rompe de verdad
            resultados[nombre] = f"inesperado: {exc!r}"
        finally:
            connection.close()

    hilos = [
        threading.Thread(target=intentar, args=("a", CEDULA_A)),
        threading.Thread(target=intentar, args=("b", CEDULA_B)),
    ]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=ESPERA_MAXIMA)
        assert not hilo.is_alive(), "Un hilo quedó bloqueado: posible interbloqueo."

    assert sorted(resultados.values()) == ["conflicto", "exito"], resultados

    citizen.refresh_from_db()
    assert citizen.verification_status == VerificationStatus.PENDIENTE
    assert CitizenProfile.objects.count() == 1
    assert AuditLog.objects.filter(action=identity.ACCION_REENVIO).count() == 1

    # El HMAC persistido es el del ganador, y solo el de uno de los dos.
    perfil = CitizenProfile.objects.get(user=citizen)
    ganador = "a" if resultados["a"] == "exito" else "b"
    cedula_ganadora = CEDULA_A if ganador == "a" else CEDULA_B
    assert perfil.document_hmac == identity.compute_document_hmac(cedula_ganadora)
    assert perfil.rejection_reason == ""
    assert perfil.verified_at is None
