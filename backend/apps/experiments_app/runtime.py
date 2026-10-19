from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings

from .custody import CustodyManager
from .evidence import EvidenceStore
from .isolation import IsolationConfig, IsolationReport, verify_isolation

_RUN_ID = re.compile(r"^s11-[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SCENARIO_ID = re.compile(r"^EX-(?:0[2-9]|10)$")


def enabled() -> bool:
    return bool(getattr(settings, "S11_EXPERIMENTS_ENABLED", False))


def isolation_config() -> IsolationConfig:
    database = settings.DATABASES["default"]
    database_url = getattr(settings, "S11_DATABASE_URL", "")
    if not database_url:
        database_url = (
            f"postgis://{database.get('USER', '')}@{database.get('HOST', '')}:"
            f"{database.get('PORT', '')}/{database.get('NAME', '')}"
        )
    return IsolationConfig(
        enabled=enabled(),
        environment=getattr(settings, "S11_ENVIRONMENT", ""),
        database_url=database_url,
        redis_url=settings.REDIS_URL,
        object_root=Path(settings.S11_OBJECT_ROOT),
    )


def require_isolation() -> IsolationReport:
    return verify_isolation(isolation_config())


def evidence_store(*, rolling_checksums: bool = True) -> EvidenceStore:
    require_isolation()
    return EvidenceStore(
        Path(settings.S11_ARTIFACT_ROOT),
        allow_sensitive=bool(getattr(settings, "S11_ALLOW_SENSITIVE_PERSISTENCE", False)),
        custody_hmac_key=(
            settings.S11_CUSTODY_HMAC_KEY.encode() if settings.S11_CUSTODY_HMAC_KEY else None
        ),
        publication_verification_key=_publication_verification_key(),
        rolling_checksums=rolling_checksums,
    )


def custody_manager() -> CustodyManager:
    require_isolation()
    return CustodyManager(
        Path(settings.S11_ARTIFACT_ROOT),
        Path(settings.S11_BACKUP_ROOT),
        Path(settings.S11_RESTORE_ROOT),
        custody_hmac_key=(
            settings.S11_CUSTODY_HMAC_KEY.encode() if settings.S11_CUSTODY_HMAC_KEY else None
        ),
        publication_verification_key=_publication_verification_key(),
    )


def _publication_verification_key() -> bytes | None:
    value = settings.S11_PUBLICATION_APPROVAL_PUBLIC_KEY
    if not value:
        return None
    try:
        return bytes.fromhex(value)
    except ValueError:
        return None


def valid_run_id(value: str) -> bool:
    return _RUN_ID.fullmatch(value) is not None


def valid_request_id(value: str) -> bool:
    return _REQUEST_ID.fullmatch(value) is not None


def valid_scenario_id(value: str) -> bool:
    return _SCENARIO_ID.fullmatch(value) is not None
