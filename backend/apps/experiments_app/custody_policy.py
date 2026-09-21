from __future__ import annotations

import hashlib
import hmac
import re
from base64 import b64decode, b64encode
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .canonical import canonical_json_bytes
from .constants import Classification

PUBLICATION_AUTHORITY = "Monty Emmanuel Pohl Amarilla"
PRIMARY_CUSTODIAN = PUBLICATION_AUTHORITY
COAUTHOR_RESEARCHER = "Guadalupe Galeano"
ACADEMIC_RESPONSIBLE = "Edward Vazquez"

APPLICATION_PROCESS = "S11_APPLICATION_PROCESS"
TEST_RUNNER = "S11_TEST_RUNNER"
STORAGE_PROCESS = "S11_STORAGE_PROCESS"
BACKUP_PROCESS = "S11_BACKUP_PROCESS"
RESTORE_PROCESS = "S11_RESTORE_PROCESS"

RETENTION_DAYS = {
    Classification.INTERNAL: 90,
    Classification.EXPERIMENTAL: 365,
}
PUBLICATION_POLICY_VERSION = "S11-CUSTODY-v1.0"
_APPROVAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ARTIFACT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,511}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PUBLICATION_SIGNATURE_DOMAIN = b"S11-PUBLICATION-APPROVAL\0"
_RECORD_SIGNATURE_DOMAIN = b"S11-CUSTODY-RECORD\0"
_INDEX_SIGNATURE_DOMAIN = b"S11-CUSTODY-INDEX\0"
_FINALIZATION_SIGNATURE_DOMAIN = b"S11-CUSTODY-FINALIZATION\0"
_SEAL_SIGNATURE_DOMAIN = b"S11-CUSTODY-SEAL\0"
_TRANSACTION_SIGNATURE_DOMAIN = b"S11-CUSTODY-TRANSACTION\0"
_VERIFICATION_SIGNATURE_DOMAIN = b"S11-CUSTODY-VERIFICATION\0"


class CustodyPolicyError(RuntimeError):
    pass


class AccessOperation(StrEnum):
    READ = "READ"
    WRITE = "WRITE"
    DELETE = "DELETE"
    ADMIN = "ADMIN"
    EXECUTE = "EXECUTE"
    REVIEW = "REVIEW"
    PUBLISH = "PUBLISH"
    BACKUP = "BACKUP"
    RESTORE = "RESTORE"


@dataclass(frozen=True, slots=True)
class AccessDecision:
    identity: str
    classification: str
    operation: str
    allowed: bool
    reason: str

    def to_dict(self) -> dict[str, str | bool]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PublicationApproval:
    approval_id: str
    artifact_id: str
    content_sha256: str
    approved_at: str
    approved_by: str
    key_id: str
    policy_version: str
    signature: str

    def signed_payload(self) -> dict[str, str]:
        return {
            "approval_id": self.approval_id,
            "approved_at": self.approved_at,
            "approved_by": self.approved_by,
            "artifact_id": self.artifact_id,
            "content_sha256": self.content_sha256,
            "key_id": self.key_id,
            "policy_version": self.policy_version,
        }

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


_READ = frozenset({AccessOperation.READ})
_RESEARCH = frozenset(
    {
        AccessOperation.READ,
        AccessOperation.WRITE,
        AccessOperation.EXECUTE,
        AccessOperation.REVIEW,
    }
)
_CUSTODY = frozenset(
    {
        AccessOperation.READ,
        AccessOperation.WRITE,
        AccessOperation.DELETE,
        AccessOperation.ADMIN,
        AccessOperation.EXECUTE,
        AccessOperation.REVIEW,
        AccessOperation.BACKUP,
        AccessOperation.RESTORE,
    }
)

ACCESS_MATRIX: dict[str, dict[Classification, frozenset[AccessOperation]]] = {
    PRIMARY_CUSTODIAN: {
        Classification.PUBLIC: frozenset(
            {
                AccessOperation.READ,
                AccessOperation.WRITE,
                AccessOperation.ADMIN,
                AccessOperation.PUBLISH,
                AccessOperation.BACKUP,
                AccessOperation.RESTORE,
            }
        ),
        Classification.INTERNAL: _CUSTODY,
        Classification.EXPERIMENTAL: _CUSTODY,
    },
    COAUTHOR_RESEARCHER: {
        Classification.PUBLIC: _READ,
        Classification.INTERNAL: _RESEARCH,
        Classification.EXPERIMENTAL: _RESEARCH,
    },
    ACADEMIC_RESPONSIBLE: {},
    APPLICATION_PROCESS: {
        Classification.INTERNAL: frozenset({AccessOperation.READ, AccessOperation.WRITE}),
        Classification.EXPERIMENTAL: frozenset({AccessOperation.READ, AccessOperation.WRITE}),
    },
    TEST_RUNNER: {
        Classification.INTERNAL: frozenset(
            {AccessOperation.READ, AccessOperation.WRITE, AccessOperation.EXECUTE}
        ),
        Classification.EXPERIMENTAL: frozenset(
            {AccessOperation.READ, AccessOperation.WRITE, AccessOperation.EXECUTE}
        ),
    },
    STORAGE_PROCESS: {
        Classification.INTERNAL: frozenset(
            {AccessOperation.READ, AccessOperation.WRITE, AccessOperation.DELETE}
        ),
        Classification.EXPERIMENTAL: frozenset(
            {AccessOperation.READ, AccessOperation.WRITE, AccessOperation.DELETE}
        ),
    },
    BACKUP_PROCESS: {
        Classification.PUBLIC: frozenset(
            {AccessOperation.READ, AccessOperation.WRITE, AccessOperation.BACKUP}
        ),
        Classification.INTERNAL: frozenset(
            {
                AccessOperation.READ,
                AccessOperation.WRITE,
                AccessOperation.DELETE,
                AccessOperation.BACKUP,
            }
        ),
        Classification.EXPERIMENTAL: frozenset(
            {
                AccessOperation.READ,
                AccessOperation.WRITE,
                AccessOperation.DELETE,
                AccessOperation.BACKUP,
            }
        ),
    },
    RESTORE_PROCESS: {
        Classification.PUBLIC: frozenset(
            {AccessOperation.READ, AccessOperation.WRITE, AccessOperation.RESTORE}
        ),
        Classification.INTERNAL: frozenset(
            {
                AccessOperation.READ,
                AccessOperation.WRITE,
                AccessOperation.DELETE,
                AccessOperation.RESTORE,
            }
        ),
        Classification.EXPERIMENTAL: frozenset(
            {
                AccessOperation.READ,
                AccessOperation.WRITE,
                AccessOperation.DELETE,
                AccessOperation.RESTORE,
            }
        ),
    },
}


def expiration_for(classification: Classification, created_at: datetime) -> datetime | None:
    """Calcula la expiración aprobada sin introducir defaults silenciosos."""
    created_at = _require_aware(created_at)
    if classification is Classification.PUBLIC:
        return None
    if classification in RETENTION_DAYS:
        return created_at + timedelta(days=RETENTION_DAYS[classification])
    if classification is Classification.SENSITIVE:
        raise CustodyPolicyError("SENSITIVE no se persiste durante S11-C.")
    raise CustodyPolicyError("SECRET no tiene retención porque nunca se persiste.")


def create_publication_approval(
    *,
    approval_id: str,
    artifact_id: str,
    content_sha256: str,
    approved_at: datetime,
    signing_key: bytes,
) -> PublicationApproval:
    private_key = _publication_private_key(signing_key)
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    if not _APPROVAL_ID.fullmatch(approval_id):
        raise CustodyPolicyError("approval_id no tiene un formato seguro.")
    if not _ARTIFACT_ID.fullmatch(artifact_id):
        raise CustodyPolicyError("artifact_id no tiene un formato seguro.")
    if not _SHA256.fullmatch(content_sha256):
        raise CustodyPolicyError("content_sha256 no es un SHA-256 válido.")
    approved_at_value = _format_utc(_require_aware(approved_at))
    unsigned = PublicationApproval(
        approval_id=approval_id,
        artifact_id=artifact_id,
        content_sha256=content_sha256,
        approved_at=approved_at_value,
        approved_by=PUBLICATION_AUTHORITY,
        key_id=_publication_key_id(public_key),
        policy_version=PUBLICATION_POLICY_VERSION,
        signature="",
    )
    signature = b64encode(
        private_key.sign(
            _PUBLICATION_SIGNATURE_DOMAIN + canonical_json_bytes(unsigned.signed_payload())
        )
    ).decode("ascii")
    return PublicationApproval(**unsigned.signed_payload(), signature=signature)


def validate_public_approval(
    classification: Classification,
    approval: PublicationApproval | None,
    *,
    expected_artifact_id: str,
    expected_content_sha256: str,
    verification_key: bytes | None,
) -> dict[str, str] | None:
    if classification is not Classification.PUBLIC:
        if approval is not None:
            raise CustodyPolicyError(
                "La aprobación PUBLIC no puede adjuntarse a otra clasificación."
            )
        return None
    if approval is None:
        raise CustodyPolicyError(
            "PUBLIC requiere una aprobación firmada de Monty Emmanuel Pohl Amarilla."
        )
    public_key = _publication_public_key(verification_key)
    if approval.approved_by != PUBLICATION_AUTHORITY:
        raise CustodyPolicyError("La aprobación PUBLIC no pertenece a la autoridad aprobada.")
    if approval.artifact_id != expected_artifact_id:
        raise CustodyPolicyError("La aprobación PUBLIC no corresponde al artefacto.")
    if approval.content_sha256 != expected_content_sha256:
        raise CustodyPolicyError("La aprobación PUBLIC no corresponde al SHA-256 del contenido.")
    if approval.policy_version != PUBLICATION_POLICY_VERSION:
        raise CustodyPolicyError("La aprobación PUBLIC usa otra versión de política.")
    if approval.key_id != _publication_key_id(verification_key):
        raise CustodyPolicyError("La aprobación PUBLIC usa otra clave de autoridad.")
    if not _APPROVAL_ID.fullmatch(approval.approval_id):
        raise CustodyPolicyError("approval_id no tiene un formato seguro.")
    _parse_utc(approval.approved_at)
    try:
        signature = b64decode(approval.signature, validate=True)
        public_key.verify(
            signature,
            _PUBLICATION_SIGNATURE_DOMAIN + canonical_json_bytes(approval.signed_payload()),
        )
    except (InvalidSignature, ValueError) as exc:
        raise CustodyPolicyError("La firma de aprobación PUBLIC no es válida.") from exc
    if len(signature) != 64:
        raise CustodyPolicyError("La firma de aprobación PUBLIC no es válida.")
    return approval.to_dict()


def create_custody_record_signature(record: dict, *, custody_hmac_key: bytes) -> str:
    """Autentica el registro completo para impedir reclasificación o borrado de aprobación."""
    _require_hmac_key(custody_hmac_key)
    payload = {key: value for key, value in record.items() if key != "custody_signature"}
    return hmac.new(
        custody_hmac_key,
        _RECORD_SIGNATURE_DOMAIN + canonical_json_bytes(payload),
        hashlib.sha256,
    ).hexdigest()


def validate_custody_record_signature(
    record: dict,
    *,
    custody_hmac_key: bytes | None,
) -> None:
    _require_hmac_key(custody_hmac_key)
    signature = record.get("custody_signature")
    if not isinstance(signature, str) or not _SHA256.fullmatch(signature):
        raise CustodyPolicyError("El registro carece de firma de custodia válida.")
    expected = create_custody_record_signature(record, custody_hmac_key=custody_hmac_key)
    if not hmac.compare_digest(signature, expected):
        raise CustodyPolicyError("La firma de custodia del registro no es válida.")


def create_custody_index_signature(content: bytes, *, custody_hmac_key: bytes) -> str:
    """Compromete el inventario completo para detectar altas, bajas o reordenamientos."""
    _require_hmac_key(custody_hmac_key)
    return hmac.new(
        custody_hmac_key,
        _INDEX_SIGNATURE_DOMAIN + content,
        hashlib.sha256,
    ).hexdigest()


def validate_custody_index_signature(
    content: bytes,
    signature: str,
    *,
    custody_hmac_key: bytes | None,
) -> None:
    _require_hmac_key(custody_hmac_key)
    if not _SHA256.fullmatch(signature):
        raise CustodyPolicyError("La firma del inventario no tiene formato válido.")
    expected = create_custody_index_signature(content, custody_hmac_key=custody_hmac_key)
    if not hmac.compare_digest(signature, expected):
        raise CustodyPolicyError("La firma del inventario de custodia no es válida.")


def create_custody_transaction_signature(content: bytes, *, custody_hmac_key: bytes) -> str:
    return _create_custody_blob_signature(
        content,
        custody_hmac_key=custody_hmac_key,
        domain=_TRANSACTION_SIGNATURE_DOMAIN,
    )


def create_custody_finalization_signature(content: bytes, *, custody_hmac_key: bytes) -> str:
    return _create_custody_blob_signature(
        content,
        custody_hmac_key=custody_hmac_key,
        domain=_FINALIZATION_SIGNATURE_DOMAIN,
    )


def validate_custody_finalization_signature(
    content: bytes,
    signature: str,
    *,
    custody_hmac_key: bytes | None,
) -> None:
    _validate_custody_blob_signature(
        content,
        signature,
        custody_hmac_key=custody_hmac_key,
        domain=_FINALIZATION_SIGNATURE_DOMAIN,
        label="journal de finalización",
    )


def create_custody_seal_signature(content: bytes, *, custody_hmac_key: bytes) -> str:
    return _create_custody_blob_signature(
        content,
        custody_hmac_key=custody_hmac_key,
        domain=_SEAL_SIGNATURE_DOMAIN,
    )


def validate_custody_seal_signature(
    content: bytes,
    signature: str,
    *,
    custody_hmac_key: bytes | None,
) -> None:
    _validate_custody_blob_signature(
        content,
        signature,
        custody_hmac_key=custody_hmac_key,
        domain=_SEAL_SIGNATURE_DOMAIN,
        label="sello de cierre",
    )


def validate_custody_transaction_signature(
    content: bytes,
    signature: str,
    *,
    custody_hmac_key: bytes | None,
) -> None:
    _validate_custody_blob_signature(
        content,
        signature,
        custody_hmac_key=custody_hmac_key,
        domain=_TRANSACTION_SIGNATURE_DOMAIN,
        label="transacción de destrucción",
    )


def create_custody_verification_signature(content: bytes, *, custody_hmac_key: bytes) -> str:
    return _create_custody_blob_signature(
        content,
        custody_hmac_key=custody_hmac_key,
        domain=_VERIFICATION_SIGNATURE_DOMAIN,
    )


def validate_custody_verification_signature(
    content: bytes,
    signature: str,
    *,
    custody_hmac_key: bytes | None,
) -> None:
    _validate_custody_blob_signature(
        content,
        signature,
        custody_hmac_key=custody_hmac_key,
        domain=_VERIFICATION_SIGNATURE_DOMAIN,
        label="evidencia de verificación",
    )


def evaluate_access(
    identity: str,
    classification: Classification,
    operation: AccessOperation,
    *,
    destruction_authorized: bool = False,
) -> AccessDecision:
    if classification is Classification.SECRET:
        return AccessDecision(
            identity,
            classification.value,
            operation.value,
            False,
            "SECRET no pertenece al almacenamiento de evidencia.",
        )
    if classification is Classification.SENSITIVE:
        return AccessDecision(
            identity,
            classification.value,
            operation.value,
            False,
            "SENSITIVE permanece deshabilitado durante S11-C.",
        )

    allowed_operations = ACCESS_MATRIX.get(identity, {}).get(classification, frozenset())
    if operation not in allowed_operations:
        return AccessDecision(
            identity,
            classification.value,
            operation.value,
            False,
            "La matriz nominal no concede la operación solicitada.",
        )
    if operation is AccessOperation.DELETE and not destruction_authorized:
        return AccessDecision(
            identity,
            classification.value,
            operation.value,
            False,
            "DELETE sólo se permite dentro del procedimiento de destrucción autorizado.",
        )
    return AccessDecision(
        identity,
        classification.value,
        operation.value,
        True,
        "Permiso nominal concedido por la política S11 P02/P03.",
    )


def require_access(
    identity: str,
    classification: Classification,
    operation: AccessOperation,
    *,
    destruction_authorized: bool = False,
) -> AccessDecision:
    decision = evaluate_access(
        identity,
        classification,
        operation,
        destruction_authorized=destruction_authorized,
    )
    if not decision.allowed:
        raise CustodyPolicyError(decision.reason)
    return decision


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CustodyPolicyError("Los timestamps de custodia deben incluir zona horaria.")
    return value.astimezone(UTC)


def publication_public_key_from_private(signing_key: bytes) -> bytes:
    return (
        _publication_private_key(signing_key)
        .public_key()
        .public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    )


def _publication_private_key(value: bytes) -> Ed25519PrivateKey:
    if len(value) != 32:
        raise CustodyPolicyError("La firma PUBLIC requiere una clave privada Ed25519 de 32 bytes.")
    return Ed25519PrivateKey.from_private_bytes(value)


def _publication_public_key(value: bytes | None) -> Ed25519PublicKey:
    if value is None or len(value) != 32:
        raise CustodyPolicyError(
            "La verificación PUBLIC requiere una clave pública Ed25519 de 32 bytes."
        )
    return Ed25519PublicKey.from_public_bytes(value)


def _publication_key_id(public_key: bytes | None) -> str:
    _publication_public_key(public_key)
    return hashlib.sha256(public_key).hexdigest()[:16]


def _require_hmac_key(value: bytes | None) -> bytes:
    if value is None or len(value) < 32:
        raise CustodyPolicyError("La custodia requiere una clave HMAC estable de 32 bytes.")
    return value


def _create_custody_blob_signature(
    content: bytes,
    *,
    custody_hmac_key: bytes,
    domain: bytes,
) -> str:
    _require_hmac_key(custody_hmac_key)
    return hmac.new(custody_hmac_key, domain + content, hashlib.sha256).hexdigest()


def _validate_custody_blob_signature(
    content: bytes,
    signature: str,
    *,
    custody_hmac_key: bytes | None,
    domain: bytes,
    label: str,
) -> None:
    _require_hmac_key(custody_hmac_key)
    if not _SHA256.fullmatch(signature):
        raise CustodyPolicyError(f"La firma de {label} no tiene formato válido.")
    expected = _create_custody_blob_signature(
        content,
        custody_hmac_key=custody_hmac_key,
        domain=domain,
    )
    if not hmac.compare_digest(signature, expected):
        raise CustodyPolicyError(f"La firma de {label} no es válida.")


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CustodyPolicyError("approved_at no es un timestamp ISO-8601 válido.") from exc
    return _require_aware(parsed)


def _format_utc(value: datetime) -> str:
    return _require_aware(value).isoformat().replace("+00:00", "Z")
