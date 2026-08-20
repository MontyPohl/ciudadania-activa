"""Resolución de la cuenta local a partir de una identidad de Google (ADR-0005).

La identidad de confianza es el claim **`sub`**, que se persiste en
`User.google_oauth_id`. El email es un atributo mutable y solo actúa como puente en
un caso concreto (C), y allí no basta con que el token esté verificado: Google debe
ser **autoridad sobre la propiedad actual** de esa dirección
(`GoogleIdentity.google_is_authoritative_email`).

`role` y `verification_status` **nunca** provienen del token ni del cuerpo de la
petición: un usuario nuevo nace `citizen` / `PENDIENTE` por el `default` del modelo.

No se crea `CitizenProfile`: nace cuando el ciudadano envía su verificación de
identidad en el Sprint 4 (decisión D2-06).
"""

from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.auth_app.services.google import GoogleIdentity

logger = logging.getLogger(__name__)

User = get_user_model()


class AccountConflictError(Exception):
    """La identidad no puede vincularse sin intervención humana. Se traduce a 409."""


class InactiveAccountError(Exception):
    """La cuenta local está desactivada. Se traduce a 401 genérico."""


def login_with_google(identity: GoogleIdentity) -> User:
    """Resuelve la cuenta local, comprueba que esté activa y registra el acceso."""
    user = resolve_user(identity)

    if not user.is_active:
        # No se reactiva nunca, y el motivo no se distingue hacia fuera: durante la
        # autenticación, "cuenta desactivada" no debe ser observable.
        raise InactiveAccountError("La cuenta no está disponible.")

    user.last_login = timezone.now()
    user.save(update_fields=["last_login"])
    return user


def resolve_user(identity: GoogleIdentity) -> User:
    """Devuelve el `User` correspondiente a la identidad, creándolo si hace falta.

    Concurrencia: dos peticiones simultáneas con el mismo `sub` deben producir **un**
    usuario. El árbitro real es el índice único de `google_oauth_id`: la petición
    perdedora recibe `IntegrityError`, y entonces se reintenta **una sola vez** en una
    transacción nueva, donde ya encuentra el usuario creado por la ganadora. Una
    transacción marcada como fallida no se reutiliza: el reintento abre otra.
    """
    try:
        with transaction.atomic():
            return _resolve(identity)
    except IntegrityError:
        logger.info("Conflicto de unicidad al resolver la cuenta de Google; se reintenta una vez.")

    with transaction.atomic():
        return _resolve(identity)


def _resolve(identity: GoogleIdentity) -> User:
    por_sub = User.objects.filter(google_oauth_id=identity.sub).first()
    if por_sub is not None:
        return _sync_email(por_sub, identity)

    # El índice único de `email` es sensible a mayúsculas (btree simple), así que la
    # búsqueda se hace con `iexact` para no crear duplicados por diferencias de
    # capitalización. La restricción de base de datos no se modifica en este sprint.
    candidatos = list(User.objects.filter(email__iexact=identity.email)[:2])

    if len(candidatos) > 1:
        # Duplicado preexistente por capitalización: no se elige uno arbitrariamente.
        raise AccountConflictError("La cuenta no puede vincularse automáticamente.")

    if candidatos:
        return _link_existing(candidatos[0], identity)

    return _create(identity)


def _create(identity: GoogleIdentity) -> User:
    """Caso A: primer login. Nace `citizen` / `PENDIENTE`, sin contraseña usable."""
    user = User.objects.create_user(
        email=identity.email,
        password=None,  # `set_password(None)` deja la contraseña inusable
        full_name=identity.full_name,
        google_oauth_id=identity.sub,
    )
    logger.info("Cuenta creada desde Google para el usuario %s.", user.pk)
    return user


def _link_existing(user: User, identity: GoogleIdentity) -> User:
    """Casos C y D: existe una cuenta local con ese email."""
    if user.google_oauth_id is not None:
        # Caso D: el email pertenece a una cuenta ya vinculada a otra identidad de
        # Google. No se fusiona nada de forma automática.
        raise AccountConflictError("La cuenta no puede vincularse automáticamente.")

    # Caso C. Vincular una identidad de Google a una cuenta local **preexistente**
    # significa entregar esa cuenta a quien controle el email, así que no basta con
    # `email_verified`: Google debe ser autoridad sobre la propiedad actual de la
    # dirección, es decir `@gmail.com` o un dominio de Google Workspace. Con un email
    # externo se exige resolución manual, porque Google verificó esa dirección alguna
    # vez pero no garantiza que siga en las mismas manos.
    if not identity.google_is_authoritative_email:
        logger.info(
            "Vinculación no automática: Google no es autoridad sobre el email del usuario %s.",
            user.pk,
        )
        raise AccountConflictError("La cuenta no puede vincularse automáticamente.")

    # Solo se escribe la identidad. `full_name` no se toca ni estando vacío: el claim
    # `name` de Google se usa **únicamente** al crear un usuario nuevo (decisión W-9).
    # `role`, `verification_status`, `is_staff` e `is_superuser` tampoco se modifican.
    user.google_oauth_id = identity.sub
    user.save(update_fields=["google_oauth_id"])
    logger.info("Identidad de Google vinculada al usuario existente %s.", user.pk)
    return user


def _sync_email(user: User, identity: GoogleIdentity) -> User:
    """Caso B y caso E: la identidad ya es conocida.

    Nunca se modifican `role`, `verification_status` ni `full_name`: el nombre puede
    haberse corregido a mano y no debe pisarse en cada login.
    """
    if user.email.lower() == identity.email:
        return user

    # Caso E: Google entrega un email distinto y verificado.
    ocupado = User.objects.filter(email__iexact=identity.email).exclude(pk=user.pk).exists()
    if ocupado:
        # No se autentica conservando en silencio el email anterior.
        raise AccountConflictError("La cuenta no puede actualizarse automáticamente.")

    user.email = identity.email
    user.save(update_fields=["email"])
    logger.info("Email actualizado desde Google para el usuario %s.", user.pk)
    return user
