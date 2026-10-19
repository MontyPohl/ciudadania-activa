"""Fixture de los 10 ciudadanos sintéticos de `s11-load-v1` para EX-10 (fase 2B-1).

Crea o verifica, en una sola transacción, exactamente los diez actores que define el
dataset (G0: 10 usuarios `APROBADO`, un VU por ciudadano). La identidad es determinista:
el `id` es el UUID del dataset y el email, el nombre y el `document_hmac` se derivan del
alias. Nunca corrige una divergencia: la informa como `DRIFT_DETECTED` y no escribe nada.

Los tokens se emiten aparte, sólo cuando se piden, y viven en `EphemeralTokens`: sin
persistencia, sin `repr`, sin `pickle` y sin forma serializable. `s11_fixture_actors` sigue
siendo exclusivo de `s11-functional-v1` (EX-02…EX-08).
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.utils import timezone
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken

from apps.auth_app.enums import UserRole, VerificationStatus
from apps.auth_app.models import CitizenProfile

from . import ex10_load

FIXTURE_SCHEMA_VERSION = "s11-ex10-fixture-v1"
#: Dominio reservado (RFC 2606): ningún email del fixture puede pertenecer a nadie.
EMAIL_DOMAIN = "example.invalid"
#: G0: los tokens se verifican antes de la barrera con al menos 10 min de vida restante.
MIN_TOKEN_REMAINING = timedelta(minutes=10)
_ALIAS = re.compile(r"^load-citizen-(?:0[1-9]|10)$")
_USER_FIELDS = frozenset({"alias", "id", "role", "verified"})
#: Clave de `pg_advisory_xact_lock`: serializa fixtures concurrentes sobre la misma base.
_LOCK_KEY = int.from_bytes(
    hashlib.sha256(f"{ex10_load.PROTOCOL_ID}:fixture".encode()).digest()[:8], "big", signed=True
)


@dataclass(frozen=True, slots=True)
class DatasetIssue:
    """Problema de un dataset alterado, sólo con constantes de este módulo y enteros.

    Nunca lleva un valor del documento: un alias, email, UUID o clave inválidos podrían
    contener rutas, credenciales o caracteres de control. `actor_index` es la posición en
    `users` desde 1, la misma numeración que los VU.
    """

    field: str
    reason: str
    actor_index: int | None = None
    detail: str | None = None

    def __str__(self) -> str:
        parts = [] if self.actor_index is None else [f"actor_index={self.actor_index}"]
        parts += [f"field={self.field}", f"reason={self.reason}"]
        return " ".join(parts + ([] if self.detail is None else [f"detail={self.detail}"]))


class Ex10FixtureError(RuntimeError):
    """Error del fixture con un código estable. Nunca lleva tokens, emails ni valores."""

    def __init__(self, code: str, issues: tuple[DatasetIssue, ...] = ()):
        self.code = code
        self.issues = issues
        super().__init__(code if not issues else f"{code}: " + "; ".join(map(str, issues)))


class ActorResult(StrEnum):
    CREATED = "CREATED"
    ALREADY_MATCHED = "ALREADY_MATCHED"
    DRIFT_DETECTED = "DRIFT_DETECTED"
    #: No existe y no se creó porque otro actor tenía drift y el fixture abortó.
    MISSING = "MISSING"


class FixtureOutcome(StrEnum):
    PREPARED = "PREPARED"
    DRIFT_DETECTED = "DRIFT_DETECTED"


@dataclass(frozen=True, slots=True)
class ActorSpec:
    """Identidad determinista de un actor. `email` y `document_hmac` no salen del proceso."""

    alias: str
    user_id: uuid.UUID
    request_keys: tuple[str, ...]

    @property
    def email(self) -> str:
        return f"s11-ex10-{self.alias}@{EMAIL_DOMAIN}"

    @property
    def full_name(self) -> str:
        return f"SYNTHETIC {self.alias}"

    @property
    def document_hmac(self) -> str:
        # Huella sintética de 64 hex: no es el HMAC de ningún documento real.
        return hashlib.sha256(
            f"SYNTHETIC:{ex10_load.PROTOCOL_ID}:{self.alias}".encode()
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class ActorOutcome:
    alias: str
    user_id: str
    profile_id: str | None
    result: ActorResult
    #: Nombres de los campos divergentes; nunca sus valores.
    drift: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "alias": self.alias,
            "user_id": self.user_id,
            "profile_id": self.profile_id,
            "role": str(UserRole.CITIZEN),
            "verification_status": str(VerificationStatus.APROBADO),
            "result": str(self.result),
            "drift": list(self.drift),
        }


@dataclass(frozen=True, slots=True)
class FixtureResult:
    outcome: FixtureOutcome
    actors: tuple[ActorOutcome, ...]
    prepared_at: datetime | None

    def to_json(self) -> dict[str, Any]:
        """Payload seguro para un artefacto futuro: sin tokens, emails ni hashes."""
        return {
            "schema_version": FIXTURE_SCHEMA_VERSION,
            "protocol_id": ex10_load.PROTOCOL_ID,
            "dataset_id": ex10_load.DATASET_ID,
            "dataset_sha256": ex10_load.DATASET_SHA256,
            "count": len(self.actors),
            "outcome": str(self.outcome),
            "prepared_at": (
                None
                if self.prepared_at is None
                else self.prepared_at.isoformat().replace("+00:00", "Z")
            ),
            "actors": [actor.to_json() for actor in self.actors],
        }


# --- Dataset -------------------------------------------------------------------------------


def actor_specs(dataset: ex10_load.LoadDataset | None = None) -> tuple[ActorSpec, ...]:
    """Los 10 actores de `s11-load-v1`, validados antes de cualquier escritura."""
    dataset = ex10_load.load_dataset() if dataset is None else dataset
    issues: list[DatasetIssue] = []
    if dataset.sha256 != ex10_load.DATASET_SHA256:
        issues.append(DatasetIssue("sha256", "frozen-sha256-required"))
    users = dataset.payload.get("users")
    if not isinstance(users, list) or len(users) != ex10_load.VIRTUAL_USERS:
        raise Ex10FixtureError("DATASET_USERS")
    for index, user in enumerate(users, start=1):
        if not isinstance(user, dict):
            issues.append(DatasetIssue("user", "object-required", index))
            continue
        if set(user) != _USER_FIELDS:
            # Campos extra podrían ser PII: sólo se informan los esperados que faltan y
            # cuántos sobran, nunca sus nombres ni sus valores.
            missing = "+".join(sorted(_USER_FIELDS - set(user))) or "none"
            extra = len(set(user) - _USER_FIELDS)
            issues.append(
                DatasetIssue(
                    "fields", "exact-fields-required", index, f"missing:{missing},extra:{extra}"
                )
            )
            continue
        if not isinstance(user["alias"], str) or not _ALIAS.fullmatch(user["alias"]):
            issues.append(DatasetIssue("alias", "canonical-alias-required", index))
        if not _is_canonical_uuid(user["id"]):
            issues.append(DatasetIssue("id", "canonical-uuid-required", index))
        if user["role"] != UserRole.CITIZEN:
            issues.append(DatasetIssue("role", "citizen-required", index))
        if user["verified"] is not True:
            issues.append(DatasetIssue("verified", "true-required", index))
    if issues:
        raise Ex10FixtureError("DATASET_INVALID", tuple(issues))
    try:
        plan = ex10_load.build_plan(dataset)
    except ex10_load.Ex10Error as exc:
        # Las razones del plan pueden terminar en un `request_key` del documento: sólo se
        # conserva su prefijo constante, y la excepción original no se encadena.
        raise Ex10FixtureError("DATASET_PLAN", _plan_issues(exc)) from None
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        # Fila de `requests` malformada; un KeyError llevaría el alias crudo.
        issue = DatasetIssue("requests", "malformed-request", detail=type(exc).__name__)
        raise Ex10FixtureError("DATASET_PLAN", (issue,)) from None
    specs = tuple(
        ActorSpec(
            alias=items[0].user_alias,
            user_id=uuid.UUID(items[0].dataset_user_id),
            request_keys=tuple(item.request_key for item in items),
        )
        for items in (plan.for_vu(vu) for vu in plan.vus)
    )
    if len({s.alias for s in specs}) != len(specs) or len({s.user_id for s in specs}) != len(specs):
        raise Ex10FixtureError("DATASET_DUPLICATE_ACTOR")
    return specs


def _is_canonical_uuid(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


_PLAN_REASON = re.compile(r"[a-z0-9-]+")


def _plan_issues(exc: ex10_load.Ex10Error) -> tuple[DatasetIssue, ...]:
    prefixes: list[str] = []
    for reason in getattr(exc, "reasons", ()):
        prefix = str(reason).split(":", 1)[0]
        prefix = prefix if _PLAN_REASON.fullmatch(prefix) else "unrecognized"
        if prefix not in prefixes:
            prefixes.append(prefix)
    return tuple(
        DatasetIssue("requests", "plan-rejected", detail=prefix) for prefix in prefixes
    ) or (DatasetIssue("requests", "plan-rejected"),)


# --- Fixture -------------------------------------------------------------------------------


def prepare_fixture(
    dataset: ex10_load.LoadDataset | None = None, *, clock=timezone.now
) -> FixtureResult:
    """Crea los actores que falten o confirma los existentes, en una única transacción.

    Primero inspecciona los diez sin escribir. Con cualquier divergencia devuelve
    `DRIFT_DETECTED` y no toca nada; si no, crea sólo los ausentes. Un fallo a mitad
    revierte todo: nunca quedan actores ni perfiles parciales.
    """
    specs = actor_specs(dataset)
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [_LOCK_KEY])
        inspected = [(spec, *_inspect(spec)) for spec in specs]
        if any(drift for _, _, _, drift in inspected):
            return FixtureResult(
                FixtureOutcome.DRIFT_DETECTED,
                tuple(
                    ActorOutcome(
                        alias=spec.alias,
                        user_id=str(spec.user_id),
                        profile_id=None if profile is None else str(profile.pk),
                        result=(
                            ActorResult.DRIFT_DETECTED
                            if drift
                            else ActorResult.MISSING
                            if user is None
                            else ActorResult.ALREADY_MATCHED
                        ),
                        drift=drift,
                    )
                    for spec, user, profile, drift in inspected
                ),
                None,
            )
        prepared_at = clock()
        outcomes = []
        for spec, user, profile, _ in inspected:
            result = ActorResult.ALREADY_MATCHED
            if user is None:
                profile = _create_actor(spec, verified_at=prepared_at)
                result = ActorResult.CREATED
            outcomes.append(ActorOutcome(spec.alias, str(spec.user_id), str(profile.pk), result))
    return FixtureResult(FixtureOutcome.PREPARED, tuple(outcomes), prepared_at)


def inspect_actor(spec: ActorSpec) -> tuple[bool, tuple[str, ...]]:
    """`(existe, campos divergentes)` sin escribir; lo usa también `ex10_state`."""
    user, _, drift = _inspect(spec)
    return user is not None, drift


def _inspect(spec: ActorSpec):
    user_model = get_user_model()
    user = user_model.objects.filter(pk=spec.user_id).first()
    if user is None:
        # No se reutiliza una cuenta ajena aunque coincida en parte.
        drift = []
        if user_model.objects.filter(email=spec.email).exists():
            drift.append("foreign-account:email")
        if CitizenProfile.objects.filter(document_hmac=spec.document_hmac).exists():
            drift.append("foreign-account:document_hmac")
        return None, None, tuple(drift)
    profile = CitizenProfile.objects.filter(user=user).first()
    expected = {
        "email": spec.email,
        "full_name": spec.full_name,
        "role": UserRole.CITIZEN,
        "verification_status": VerificationStatus.APROBADO,
        "is_active": True,
        "is_staff": False,
        "is_superuser": False,
        "google_oauth_id": None,
    }
    drift = [name for name, value in expected.items() if getattr(user, name) != value]
    if user.has_usable_password():
        drift.append("password-usable")
    if profile is None:
        drift.append("citizen-profile:missing")
    else:
        if profile.document_hmac != spec.document_hmac:
            drift.append("citizen-profile:document_hmac")
        if profile.verified_at is None:
            drift.append("citizen-profile:verified_at")
        if profile.verification_selfie_url or profile.rejection_reason:
            drift.append("citizen-profile:identity-material")
    return user, profile, tuple(drift)


def _create_actor(spec: ActorSpec, *, verified_at: datetime) -> CitizenProfile:
    user = get_user_model().objects.create_user(
        email=spec.email,
        password=None,  # sin contraseña utilizable: sólo se autentica con el token efímero
        id=spec.user_id,
        full_name=spec.full_name,
        role=UserRole.CITIZEN,
        verification_status=VerificationStatus.APROBADO,
        is_active=True,
    )
    return CitizenProfile.objects.create(
        user=user,
        document_hmac=spec.document_hmac,
        verification_selfie_url="",
        verified_at=verified_at,
    )


# --- Tokens --------------------------------------------------------------------------------


class EphemeralTokens:
    """Tokens de acceso por alias, sólo en memoria.

    No tiene `repr` con valores, no se puede copiar con `pickle` y no tiene forma
    serializable. `release()` suelta las referencias; después, `mapping()` falla.
    """

    __slots__ = ("_tokens",)

    def __init__(self, tokens: Mapping[str, str]):
        self._tokens: dict[str, str] | None = dict(tokens)

    def mapping(self) -> Mapping[str, str]:
        if self._tokens is None:
            raise Ex10FixtureError("TOKENS_RELEASED")
        return MappingProxyType(self._tokens)

    def aliases(self) -> tuple[str, ...]:
        return () if self._tokens is None else tuple(sorted(self._tokens))

    def release(self) -> None:
        if self._tokens is not None:
            self._tokens.clear()
        self._tokens = None

    @property
    def released(self) -> bool:
        return self._tokens is None

    def __len__(self) -> int:
        return 0 if self._tokens is None else len(self._tokens)

    def __repr__(self) -> str:
        return f"EphemeralTokens(count={len(self)}, released={self.released})"

    __str__ = __repr__

    def __reduce__(self):
        raise TypeError("EphemeralTokens no se serializa: contiene tokens de acceso.")


@dataclass(frozen=True, slots=True)
class TokenCheck:
    alias: str
    signature_valid: bool
    user_matches: bool
    remaining_ok: bool

    @property
    def ok(self) -> bool:
        return self.signature_valid and self.user_matches and self.remaining_ok

    def to_json(self) -> dict[str, Any]:
        return {
            "alias": self.alias,
            "signature_valid": self.signature_valid,
            "user_matches": self.user_matches,
            "remaining_ok": self.remaining_ok,
        }


def issue_tokens(result: FixtureResult) -> EphemeralTokens:
    """Un access token por actor de un fixture `PREPARED`. No persiste nada."""
    if result.outcome is not FixtureOutcome.PREPARED:
        raise Ex10FixtureError("FIXTURE_NOT_PREPARED")
    users = get_user_model().objects.in_bulk([actor.user_id for actor in result.actors])
    if len(users) != len(result.actors) or len(result.actors) != ex10_load.VIRTUAL_USERS:
        raise Ex10FixtureError("FIXTURE_ACTORS_MISSING")
    return EphemeralTokens(
        {
            actor.alias: str(AccessToken.for_user(users[uuid.UUID(actor.user_id)]))
            for actor in result.actors
        }
    )


def verify_tokens(
    tokens: EphemeralTokens, result: FixtureResult, *, now: datetime | None = None
) -> tuple[TokenCheck, ...]:
    """Verificación local previa a la barrera: firma, usuario y vida restante (G0)."""
    now = timezone.now() if now is None else now
    values = tokens.mapping()
    checks = []
    for actor in result.actors:
        token = values.get(actor.alias)
        try:
            decoded = AccessToken(token) if token else None
        except TokenError:
            decoded = None
        if decoded is None:
            checks.append(TokenCheck(actor.alias, False, False, False))
            continue
        remaining = datetime.fromtimestamp(decoded["exp"], tz=now.tzinfo) - now
        checks.append(
            TokenCheck(
                actor.alias,
                True,
                str(decoded.get("user_id")) == actor.user_id,
                remaining >= MIN_TOKEN_REMAINING,
            )
        )
    return tuple(checks)
