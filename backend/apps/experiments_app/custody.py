from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .canonical import canonical_json_bytes, sha256_bytes, sha256_file
from .constants import ArtifactKind, Classification
from .custody_policy import (
    ACADEMIC_RESPONSIBLE,
    APPLICATION_PROCESS,
    BACKUP_PROCESS,
    COAUTHOR_RESEARCHER,
    PRIMARY_CUSTODIAN,
    RESTORE_PROCESS,
    STORAGE_PROCESS,
    TEST_RUNNER,
    AccessOperation,
    CustodyPolicyError,
    PublicationApproval,
    create_custody_transaction_signature,
    create_custody_verification_signature,
    create_publication_approval,
    evaluate_access,
    expiration_for,
    publication_public_key_from_private,
    require_access,
    validate_custody_index_signature,
    validate_custody_record_signature,
    validate_custody_transaction_signature,
    validate_custody_verification_signature,
    validate_public_approval,
)
from .evidence import EvidencePolicyError, EvidenceStore, validate_finalized_state

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PRIVATE_DIRECTORY_MODE = 0o700
_PRIVATE_FILE_MODE = 0o600
EXPECTED_BASELINE_SHA = "d3b492a7f5d203fc1dbd8c63e1732b7fd9227656"
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
ATTESTED_SOURCE_PATHS = (
    ".env.example",
    ".gitignore",
    "backend/apps/experiments_app/canonical.py",
    "backend/apps/experiments_app/constants.py",
    "backend/apps/experiments_app/custody.py",
    "backend/apps/experiments_app/custody_policy.py",
    "backend/apps/experiments_app/evidence.py",
    "backend/apps/experiments_app/isolation.py",
    "backend/apps/experiments_app/manifest.py",
    "backend/apps/experiments_app/management/commands/s11_finalize_run.py",
    "backend/apps/experiments_app/management/commands/s11_verify_custody.py",
    "backend/apps/experiments_app/runtime.py",
    "backend/apps/experiments_app/redaction.py",
    "backend/apps/experiments_app/tests/test_custody.py",
    "backend/apps/experiments_app/tests/test_evidence.py",
    "backend/Dockerfile.dev",
    "backend/config/settings/base.py",
    "backend/config/settings/dev.py",
    "backend/config/settings/test.py",
    "backend/manage.py",
    "backend/requirements/base.txt",
    "backend/requirements/dev.txt",
    "backend/requirements/test.txt",
    "docs/tesis/S11A_POLITICA_CUSTODIA_Y_ACCESO.md",
    "infra/docker-compose.yml",
    "infra/docker-compose.s11.yml",
)


class CustodyError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CopyVerification:
    run_id: str
    source: str
    destination: str
    file_count: int
    sha256_match: bool


@dataclass(frozen=True, slots=True)
class DestructionReceipt:
    verification_id: str
    run_id: str
    classification: str
    artifact_ids: tuple[str, ...]
    remaining_artifact_ids: tuple[str, ...]
    locations_affected: tuple[str, ...]
    destroyed_at: str
    executing_identity: str
    method: str
    result: str
    payload_retained: bool
    intent_path: str
    intent_sha256: str
    receipt_path: str
    receipt_sha256: str


class CustodyManager:
    """Opera copias S11 sobre raíces locales, separadas y no productivas."""

    def __init__(
        self,
        primary_root: Path,
        backup_root: Path,
        restore_root: Path,
        *,
        custody_hmac_key: bytes | None = None,
        publication_verification_key: bytes | None = None,
    ) -> None:
        self.primary_root = primary_root.resolve()
        self.backup_root = backup_root.resolve()
        self.restore_root = restore_root.resolve()
        self.custody_hmac_key = custody_hmac_key
        self.publication_verification_key = publication_verification_key
        if self.custody_hmac_key is None or len(self.custody_hmac_key) < 32:
            raise CustodyError("La custodia S11 requiere una clave HMAC de 32 bytes.")
        if (
            self.publication_verification_key is None
            or len(self.publication_verification_key) != 32
        ):
            raise CustodyError("La custodia S11 requiere una clave pública Ed25519 de 32 bytes.")
        self._validate_roots()

    def initialize_roots(self) -> None:
        for root in (self.primary_root, self.backup_root, self.restore_root):
            root.mkdir(parents=True, exist_ok=True)
            root.chmod(_PRIVATE_DIRECTORY_MODE)

    def backup_run(self, run_id: str, *, identity: str) -> CopyVerification:
        with self._primary_mutation():
            source = self._run_path(self.primary_root, run_id)
            destination = self._run_path(self.backup_root, run_id)
            records = self._validated_records(source)
            for classification in self._classifications(records):
                require_access(identity, classification, AccessOperation.BACKUP)
            return self._copy_and_verify(run_id, source, destination)

    def restore_run(self, run_id: str, *, identity: str) -> CopyVerification:
        with self._primary_mutation():
            source = self._run_path(self.backup_root, run_id)
            destination = self._run_path(self.restore_root, run_id)
            records = self._validated_records(source)
            for classification in self._classifications(records):
                require_access(identity, classification, AccessOperation.RESTORE)
            return self._copy_and_verify(run_id, source, destination)

    def destroy_expired_run(
        self,
        run_id: str,
        *,
        verification_id: str,
        identity: str,
        now: datetime,
    ) -> DestructionReceipt:
        with self._primary_mutation():
            return self._destroy_expired_run_locked(
                run_id,
                verification_id=verification_id,
                identity=identity,
                now=now,
            )

    @contextmanager
    def _primary_mutation(self):
        store = EvidenceStore(
            self.primary_root,
            custody_hmac_key=self.custody_hmac_key,
            publication_verification_key=self.publication_verification_key,
        )
        with store.mutation():
            yield

    def _destroy_expired_run_locked(
        self,
        run_id: str,
        *,
        verification_id: str,
        identity: str,
        now: datetime,
    ) -> DestructionReceipt:
        self._identifier(verification_id, "verification_id")
        now = _aware_utc(now)
        targets = tuple(
            self._run_path(root, run_id)
            for root in (self.primary_root, self.backup_root, self.restore_root)
        )
        verification_root = self.primary_root / "custody-verifications" / verification_id
        receipt_file = verification_root / "destruction.json"
        transaction_root = self.primary_root / "custody-transactions"
        transaction_path = transaction_root / f"{verification_id}.json"
        transaction_checksum = transaction_path.with_suffix(".json.sha256")
        transaction_signature = transaction_path.with_suffix(".json.hmac")

        _discard_incomplete_bundle(receipt_file)
        if _bundle_committed(receipt_file):
            payload = self.verify_verification_record(verification_id, "destruction.json")
            if payload.get("verification_id") != verification_id:
                raise CustodyError("La constancia pertenece a otra verificación.")
            if payload.get("run_id") != run_id:
                raise CustodyError("La constancia pertenece a otro run.")
            if payload.get("executing_identity") != identity:
                raise CustodyError("La constancia pertenece a otra identidad ejecutora.")
            _remove_transaction(transaction_path, transaction_checksum, transaction_signature)
            return _destruction_receipt_from_payload(
                payload,
                receipt_file,
                sha256_file(receipt_file),
            )

        if _bundle_committed(transaction_path):
            transaction = _read_transaction(
                transaction_path,
                transaction_checksum,
                transaction_signature,
                self.custody_hmac_key,
            )
            if transaction.get("verification_id") != verification_id:
                raise CustodyError("La transacción pertenece a otra verificación.")
            if transaction.get("run_id") != run_id:
                raise CustodyError("La transacción pertenece a otro run.")
            if transaction.get("executing_identity") != identity:
                raise CustodyError("La transacción pertenece a otra identidad ejecutora.")
        else:
            _discard_incomplete_bundle(transaction_path)
            primary = self._run_path(self.primary_root, run_id)
            records = self._validated_records(primary)
            _validate_retention_for_destruction(
                primary,
                records,
                custody_hmac_key=self.custody_hmac_key,
            )
            existing_targets = tuple(path for path in targets if path.exists())
            for target in existing_targets:
                if self._validated_records(target) != records:
                    raise CustodyError("Una copia no conserva el mismo índice que primary.")

            expired_records = []
            remaining_records = []
            for record in records:
                expires_at = record.get("expires_at")
                if expires_at is not None and _parse_utc(expires_at) <= now:
                    expired_records.append(record)
                else:
                    remaining_records.append(record)
            if not expired_records:
                raise CustodyError("Ningún artefacto alcanzó expires_at.")

            classifications = self._classifications(expired_records)
            for classification in classifications:
                require_access(
                    identity,
                    classification,
                    AccessOperation.DELETE,
                    destruction_authorized=True,
                )
            transaction = {
                "artifact_ids": sorted(record["artifact_id"] for record in expired_records),
                "classification": ",".join(
                    sorted(classification.value for classification in classifications)
                ),
                "directory_hashes": {
                    str(path): _directory_hashes(path) for path in existing_targets
                },
                "executing_identity": identity,
                "expired_records": expired_records,
                "locations_planned": [str(path) for path in existing_targets],
                "method": "artifact-delete+reindex+absence-check",
                "original_records": records,
                "remaining_records": remaining_records,
                "requested_at": _format_utc(now),
                "run_id": run_id,
                "verification_id": verification_id,
            }
            _write_transaction(
                transaction_path,
                transaction_checksum,
                transaction_signature,
                transaction,
                self.custody_hmac_key,
            )

        _validate_destruction_transaction(
            transaction,
            custody_hmac_key=self.custody_hmac_key,
            publication_verification_key=self.publication_verification_key,
        )
        expired_records = transaction["expired_records"]
        remaining_records = transaction["remaining_records"]
        classifications = self._classifications(expired_records)
        for classification in classifications:
            require_access(
                identity,
                classification,
                AccessOperation.DELETE,
                destruction_authorized=True,
            )
        expected_targets = {str(path): path for path in targets}
        try:
            planned_targets = tuple(
                expected_targets[path] for path in transaction["locations_planned"]
            )
        except (KeyError, TypeError) as exc:
            raise CustodyError("La transacción contiene una ubicación no aprobada.") from exc
        locations = tuple(str(path) for path in planned_targets)

        intent_file = verification_root / "destruction.intent.json"
        _discard_incomplete_bundle(intent_file)
        if _bundle_committed(intent_file):
            intent_payload = self.verify_verification_record(
                verification_id,
                "destruction.intent.json",
            )
            intent_path = intent_file
            intent_digest = sha256_file(intent_file)
        else:
            intent_payload = {
                "artifact_ids": transaction["artifact_ids"],
                "classification": transaction["classification"],
                "executing_identity": identity,
                "locations_planned": list(locations),
                "method": transaction["method"],
                "payload_retained": False,
                "requested_at": transaction["requested_at"],
                "result": "PENDING",
                "run_id": run_id,
                "transaction_sha256": sha256_file(transaction_path),
                "verification_id": verification_id,
            }
            intent_path, intent_digest = self.write_verification_record(
                verification_id,
                "destruction.intent.json",
                intent_payload,
            )
        if intent_payload.get("transaction_sha256") != sha256_file(transaction_path):
            raise CustodyError("La intención no coincide con la transacción recuperable.")
        expected_intent = {
            "artifact_ids": transaction["artifact_ids"],
            "classification": transaction["classification"],
            "executing_identity": identity,
            "locations_planned": list(locations),
            "method": transaction["method"],
            "payload_retained": False,
            "requested_at": transaction["requested_at"],
            "result": "PENDING",
            "run_id": run_id,
            "verification_id": verification_id,
        }
        if any(intent_payload.get(key) != value for key, value in expected_intent.items()):
            raise CustodyError("La intención no conserva el contenido de la transacción.")

        for target in planned_targets:
            if not target.exists():
                if remaining_records:
                    raise CustodyError("Falta una copia que debía conservar artefactos vigentes.")
                continue
            try:
                original_hashes = transaction["directory_hashes"][str(target)]
            except (KeyError, TypeError) as exc:
                raise CustodyError("Falta el inventario original de una ubicación.") from exc
            _destroy_records_in_run(
                target,
                expired_records,
                remaining_records,
                original_hashes,
                self.custody_hmac_key,
                self.publication_verification_key,
            )

        for target in planned_targets:
            if remaining_records:
                verified = self._validated_records(target)
                if verified != remaining_records:
                    raise CustodyError("La copia reindexada no conserva los artefactos vigentes.")
            elif target.exists():
                raise CustodyError("La verificación posterior detectó una copia residual.")

        receipt_payload = {
            "artifact_ids": transaction["artifact_ids"],
            "classification": transaction["classification"],
            "destroyed_at": _format_utc(now),
            "executing_identity": identity,
            "locations_affected": locations,
            "intent_path": str(intent_path),
            "intent_sha256": intent_digest,
            "method": "artifact-delete+reindex+absence-check",
            "payload_retained": False,
            "remaining_artifact_ids": sorted(record["artifact_id"] for record in remaining_records),
            "result": "DESTROYED",
            "run_id": run_id,
            "verification_id": verification_id,
        }
        receipt_path, receipt_digest = self.write_verification_record(
            verification_id,
            "destruction.json",
            receipt_payload,
        )
        _remove_transaction(transaction_path, transaction_checksum, transaction_signature)
        return _destruction_receipt_from_payload(
            receipt_payload,
            receipt_path,
            receipt_digest,
        )

    def write_verification_record(
        self, verification_id: str, filename: str, payload: dict[str, Any]
    ) -> tuple[Path, str]:
        self._identifier(verification_id, "verification_id")
        safe_name = Path(filename).name
        if safe_name != filename or not safe_name.endswith(".json"):
            raise CustodyError("El registro de verificación requiere un nombre JSON simple.")
        verification_root = self.primary_root / "custody-verifications"
        verification_root.mkdir(parents=True, exist_ok=True)
        verification_root.chmod(_PRIVATE_DIRECTORY_MODE)
        directory = verification_root / verification_id
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(_PRIVATE_DIRECTORY_MODE)
        path = directory / safe_name
        if _bundle_committed(path):
            raise CustodyError(f"El registro de verificación ya existe: {safe_name}")
        _discard_incomplete_bundle(path)
        content = canonical_json_bytes(payload)
        signature_value = create_custody_verification_signature(
            content,
            custody_hmac_key=self.custody_hmac_key,
        )
        digest = _write_authenticated_bundle(path, content, signature_value)
        return path, digest

    def verify_verification_record(self, verification_id: str, filename: str) -> dict[str, Any]:
        self._identifier(verification_id, "verification_id")
        path = self.primary_root / "custody-verifications" / verification_id / filename
        checksum = path.with_suffix(path.suffix + ".sha256")
        signature = path.with_suffix(path.suffix + ".hmac")
        _validate_bundle_commit(path)
        content = path.read_bytes()
        expected = checksum.read_text(encoding="utf-8").split()[0]
        if sha256_bytes(content) != expected:
            raise CustodyError("La evidencia de la fase previa no coincide con su SHA-256.")
        try:
            validate_custody_verification_signature(
                content,
                signature.read_text(encoding="ascii").strip(),
                custody_hmac_key=self.custody_hmac_key,
            )
        except (CustodyPolicyError, OSError) as exc:
            raise CustodyError("La evidencia de verificación no tiene una firma válida.") from exc
        return json.loads(content)

    def _copy_and_verify(self, run_id: str, source: Path, destination: Path) -> CopyVerification:
        source_hashes = _directory_hashes(source)
        if destination.exists():
            raise CustodyError(f"La copia de destino ya existe: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.parent.chmod(_PRIVATE_DIRECTORY_MODE)
        shutil.copytree(source, destination, symlinks=False)
        _harden_tree(destination)
        destination_hashes = _directory_hashes(destination)
        if source_hashes != destination_hashes:
            raise CustodyError("La copia no conserva exactamente los SHA-256 del origen.")
        return CopyVerification(
            run_id=run_id,
            source=str(source),
            destination=str(destination),
            file_count=len(source_hashes),
            sha256_match=True,
        )

    def _validated_records(self, run_root: Path) -> list[dict[str, Any]]:
        if run_root.is_symlink() or not run_root.is_dir():
            raise CustodyError(f"El run no existe como directorio regular: {run_root}")
        marker = run_root / ".s11-evidence-root"
        index = run_root / ArtifactKind.METADATA.value / "artifacts.jsonl"
        index_signature = index.with_suffix(index.suffix + ".hmac")
        checksums = run_root / "SHA256SUMS"
        if not all(path.is_file() for path in (marker, index, index_signature, checksums)):
            raise CustodyError("El run carece de marcador, índice firmado o SHA256SUMS.")
        index_content = index.read_bytes()
        try:
            validate_custody_index_signature(
                index_content,
                index_signature.read_text(encoding="ascii").strip(),
                custody_hmac_key=self.custody_hmac_key,
            )
        except (CustodyPolicyError, OSError) as exc:
            raise CustodyError("La firma del inventario de custodia no es válida.") from exc
        records = [json.loads(line) for line in index_content.decode().splitlines()]
        if not records:
            raise CustodyError("El run no contiene registros de artefactos.")
        required = {
            "artifact_id",
            "classification",
            "custody_signature",
            "expires_at",
            "relative_path",
            "retention_anchor",
            "retention_started_at",
            "run_id",
            "sha256",
        }
        for record in records:
            if not required.issubset(record):
                raise CustodyError("El índice no contiene metadata de custodia completa.")
            if record["run_id"] != run_root.name:
                raise CustodyError("El índice no corresponde al run solicitado.")
            artifact_path = (run_root / record["relative_path"]).resolve()
            if run_root not in artifact_path.parents:
                raise CustodyError("El índice contiene una ruta fuera del run.")
            if artifact_path.is_symlink() or not artifact_path.is_file():
                raise CustodyError("El índice referencia un artefacto ausente o no regular.")
            if sha256_file(artifact_path) != record["sha256"]:
                raise CustodyError("El SHA-256 del índice no coincide con el artefacto.")
            try:
                validate_custody_record_signature(
                    record,
                    custody_hmac_key=self.custody_hmac_key,
                )
                classification = Classification(record["classification"])
                approval_payload = record.get("publication_approval")
                if approval_payload is not None and not isinstance(approval_payload, dict):
                    raise CustodyPolicyError("La aprobación almacenada no es un objeto.")
                approval = (
                    PublicationApproval(**approval_payload)
                    if approval_payload is not None
                    else None
                )
                validate_public_approval(
                    classification,
                    approval,
                    expected_artifact_id=record["artifact_id"],
                    expected_content_sha256=record["sha256"],
                    verification_key=self.publication_verification_key,
                )
            except (CustodyPolicyError, TypeError, ValueError) as exc:
                raise CustodyError(
                    "La política o firma de custodia almacenada no es válida."
                ) from exc
        try:
            validate_finalized_state(
                run_root,
                records,
                custody_hmac_key=self.custody_hmac_key,
            )
        except EvidencePolicyError as exc:
            raise CustodyError("El estado de cierre del run no es válido.") from exc
        expected_lines = {relative: digest for digest, relative in _read_checksum_lines(checksums)}
        actual_hashes = _directory_hashes(run_root, exclude={"SHA256SUMS"})
        if expected_lines != actual_hashes:
            raise CustodyError("SHA256SUMS no representa exactamente el contenido del run.")
        return records

    @staticmethod
    def _classifications(records: list[dict[str, Any]]) -> set[Classification]:
        try:
            return {Classification(record["classification"]) for record in records}
        except ValueError as exc:
            raise CustodyError("El índice contiene una clasificación desconocida.") from exc

    def _validate_roots(self) -> None:
        roots = (self.primary_root, self.backup_root, self.restore_root)
        for root in roots:
            if root == Path("/tmp") or Path("/tmp") in root.parents:
                raise CustodyError("La custodia, backup y restore no pueden residir en /tmp.")
            if root in {Path("/"), Path.home().resolve()}:
                raise CustodyError("Una raíz de custodia no puede ser amplia o personal completa.")
        if len(set(roots)) != len(roots):
            raise CustodyError("Primary, backup y restore deben usar ubicaciones distintas.")
        if any(left in right.parents for left in roots for right in roots if left != right):
            raise CustodyError("Las raíces de custodia no pueden contenerse entre sí.")

    @staticmethod
    def _run_path(root: Path, run_id: str) -> Path:
        CustodyManager._identifier(run_id, "run_id")
        return root / "runs" / run_id

    @staticmethod
    def _identifier(value: str, label: str) -> None:
        if not _IDENTIFIER.fullmatch(value):
            raise CustodyError(f"{label} no tiene un formato seguro.")


def prepare_custody_verification(
    manager: CustodyManager,
    *,
    verification_id: str,
    clock=lambda: datetime.now(UTC),
    execution_context: dict[str, str | int] | None = None,
    source_snapshot: dict[str, Any],
) -> dict[str, Any]:
    manager.initialize_roots()
    execution_context = _validated_execution_context(
        execution_context or _current_execution_context()
    )
    source_snapshot = _validated_source_snapshot(source_snapshot)
    store = EvidenceStore(
        manager.primary_root,
        clock=clock,
        custody_hmac_key=manager.custody_hmac_key,
        publication_verification_key=manager.publication_verification_key,
    )
    canary = store.persist_json(
        run_id=verification_id,
        scenario_id="P02-P03",
        artifact_type="custody-canary",
        kind=ArtifactKind.RAW,
        classification=Classification.INTERNAL,
        filename="custody-canary.json",
        payload={
            "classification": "INTERNAL",
            "secret": "S11-CANARY-MUST-NOT-PERSIST",
            "synthetic": True,
            "verification_id": verification_id,
        },
        source="s11-custody-verification",
    )
    finalized_records = store.finalize_verification_retention(
        verification_id,
        finished_at=_parse_utc(canary.created_at),
    )
    finalized_canary = finalized_records[0]
    gate_checks = _policy_gate_checks(store, verification_id)
    access_checks = _access_checks()
    report = {
        "access_checks": access_checks,
        "artifact_id": canary.artifact_id,
        "classification": Classification.INTERNAL.value,
        "created_at": canary.created_at,
        "expires_at": finalized_canary["expires_at"],
        "execution_context": execution_context,
        "gate_checks": gate_checks,
        "phase": "PREPARED",
        "primary_root": str(manager.primary_root),
        "publication_verification_key_id": sha256_bytes(manager.publication_verification_key)[:16],
        "retention_anchor": finalized_canary["retention_anchor"],
        "run_id": verification_id,
        "sha256": canary.sha256,
        "source_snapshot": source_snapshot,
        "synthetic": True,
        "verification_id": verification_id,
    }
    path, digest = manager.write_verification_record(verification_id, "prepare.json", report)
    return {**report, "evidence_path": str(path), "evidence_sha256": digest}


def complete_custody_verification(
    manager: CustodyManager,
    *,
    verification_id: str,
    persistence_boundary: str,
    execution_context: dict[str, str | int] | None = None,
    source_snapshot: dict[str, Any],
) -> dict[str, Any]:
    manager.initialize_roots()
    prepare_report = manager.verify_verification_record(verification_id, "prepare.json")
    prepare_context = _validated_execution_context(prepare_report.get("execution_context"))
    source_snapshot = _validated_source_snapshot(source_snapshot)
    if prepare_report.get("source_snapshot") != source_snapshot:
        raise CustodyError("El código o la política cambiaron entre prepare y complete.")
    complete_context = _validated_execution_context(
        execution_context or _current_execution_context()
    )
    _verify_persistence_boundary(
        persistence_boundary,
        prepare_context=prepare_context,
        complete_context=complete_context,
    )
    store = EvidenceStore(
        manager.primary_root,
        custody_hmac_key=manager.custody_hmac_key,
        publication_verification_key=manager.publication_verification_key,
    )
    errors = store.verify(verification_id)
    if errors:
        raise CustodyError(f"El canario persistente falló verificación: {errors}")

    backup = manager.backup_run(verification_id, identity=PRIMARY_CUSTODIAN)
    restored = manager.restore_run(verification_id, identity=PRIMARY_CUSTODIAN)
    source_hashes = _directory_hashes(manager.primary_root / "runs" / verification_id)
    backup_hashes = _directory_hashes(manager.backup_root / "runs" / verification_id)
    restore_hashes = _directory_hashes(manager.restore_root / "runs" / verification_id)
    if source_hashes != backup_hashes or source_hashes != restore_hashes:
        raise CustodyError("Primary, backup y restore no coinciden byte a byte.")
    _assert_canary_secret_absent(
        manager.primary_root / "runs" / verification_id,
        manager.backup_root / "runs" / verification_id,
        manager.restore_root / "runs" / verification_id,
    )

    records = store.records(verification_id)
    expires_at = _parse_utc(records[0]["expires_at"])
    receipt = manager.destroy_expired_run(
        verification_id,
        verification_id=verification_id,
        identity=PRIMARY_CUSTODIAN,
        now=expires_at + timedelta(seconds=1),
    )
    gate_checks = _policy_gate_checks(store, f"{verification_id}-complete")
    access_checks = _access_checks()
    report = {
        "access_checks": access_checks,
        "backup": asdict(backup),
        "classification": Classification.INTERNAL.value,
        "destruction": asdict(receipt),
        "gate_checks": gate_checks,
        "persistence": {
            "boundary": persistence_boundary,
            "boundary_verified": True,
            "complete_execution_context": complete_context,
            "prepare_execution_context": prepare_context,
            "prepare_evidence_recovered": True,
            "run_recovered": True,
            "sha256_preserved": True,
        },
        "phase": "COMPLETED",
        "prepare_evidence_sha256": sha256_file(Path(prepare_report_path(manager, verification_id))),
        "primary_root": str(manager.primary_root),
        "production_modified": False,
        "publication_verification_key_id": sha256_bytes(manager.publication_verification_key)[:16],
        "restore": asdict(restored),
        "run_id": verification_id,
        "sensitive_persistence": False,
        "source_snapshot": source_snapshot,
        "synthetic": True,
        "verification_id": verification_id,
    }
    path, digest = manager.write_verification_record(verification_id, "complete.json", report)
    return {**report, "evidence_path": str(path), "evidence_sha256": digest}


def attest_custody_verification(
    manager: CustodyManager,
    *,
    verification_id: str,
    source_snapshot: dict[str, Any],
    commit_sha: str,
    source_root: Path,
    baseline_sha: str,
) -> dict[str, Any]:
    if not _COMMIT_SHA.fullmatch(commit_sha):
        raise CustodyError("commit_sha no es un SHA Git completo.")
    source_snapshot = _validated_source_snapshot(source_snapshot)
    commit_snapshot = build_commit_source_snapshot(
        source_root,
        baseline_sha=baseline_sha,
        commit_sha=commit_sha,
    )
    if commit_snapshot != source_snapshot:
        raise CustodyError("El commit no contiene exactamente las fuentes verificadas.")
    prepare_report = manager.verify_verification_record(verification_id, "prepare.json")
    complete_report = manager.verify_verification_record(verification_id, "complete.json")
    if prepare_report.get("source_snapshot") != source_snapshot:
        raise CustodyError("El commit no conserva el snapshot usado por prepare.")
    if complete_report.get("source_snapshot") != source_snapshot:
        raise CustodyError("El commit no conserva el snapshot usado por complete.")
    report = {
        "commit_sha": commit_sha,
        "commit_source_snapshot_sha256": commit_snapshot["aggregate_sha256"],
        "complete_evidence_sha256": sha256_file(
            manager.primary_root / "custody-verifications" / verification_id / "complete.json"
        ),
        "phase": "ATTESTED",
        "production_modified": False,
        "source_snapshot": source_snapshot,
        "synthetic": True,
        "verification_id": verification_id,
    }
    path, digest = manager.write_verification_record(
        verification_id,
        "attestation.json",
        report,
    )
    return {**report, "evidence_path": str(path), "evidence_sha256": digest}


def build_source_snapshot(source_root: Path, *, baseline_sha: str) -> dict[str, Any]:
    if baseline_sha != EXPECTED_BASELINE_SHA:
        raise CustodyError("El baseline no coincide con el aprobado para P02/P03.")
    root = source_root.resolve()
    files = {}
    for relative in ATTESTED_SOURCE_PATHS:
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise CustodyError(f"Falta una fuente regular para la atestación: {relative}")
        files[relative] = sha256_file(path)
    return {
        "aggregate_sha256": sha256_bytes(canonical_json_bytes(files)),
        "baseline_sha": baseline_sha,
        "files": files,
    }


def build_commit_source_snapshot(
    source_root: Path,
    *,
    baseline_sha: str,
    commit_sha: str,
) -> dict[str, Any]:
    if baseline_sha != EXPECTED_BASELINE_SHA:
        raise CustodyError("El baseline no coincide con el aprobado para P02/P03.")
    if not _COMMIT_SHA.fullmatch(commit_sha):
        raise CustodyError("commit_sha no es un SHA Git completo.")
    root = source_root.resolve()
    resolved_head = _git_output(root, "rev-parse", "HEAD").decode().strip()
    if resolved_head != commit_sha:
        raise CustodyError("commit_sha no coincide con HEAD.")
    _git_output(root, "cat-file", "-e", f"{commit_sha}^{{commit}}")
    try:
        _git_output(root, "merge-base", "--is-ancestor", baseline_sha, commit_sha)
    except CustodyError as exc:
        raise CustodyError("El baseline aprobado no es ancestro del commit atestado.") from exc
    files = {}
    for relative in ATTESTED_SOURCE_PATHS:
        content = _git_output(root, "show", f"{commit_sha}:{relative}")
        files[relative] = sha256_bytes(content)
    return {
        "aggregate_sha256": sha256_bytes(canonical_json_bytes(files)),
        "baseline_sha": baseline_sha,
        "files": files,
    }


def _git_output(root: Path, *arguments: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True,
            capture_output=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise CustodyError("No se pudo verificar el objeto Git de la atestación.") from exc
    return result.stdout


def prepare_report_path(manager: CustodyManager, verification_id: str) -> str:
    return str(manager.primary_root / "custody-verifications" / verification_id / "prepare.json")


def _policy_gate_checks(
    store: EvidenceStore,
    run_id: str,
) -> dict[str, bool]:
    common = {
        "run_id": run_id,
        "scenario_id": "P02-P03",
        "kind": ArtifactKind.RAW,
        "filename": "blocked.bin",
        "content": b"synthetic",
        "mime_type": "application/octet-stream",
        "source": "s11-custody-verification",
    }
    public_without_approval_denied = _persistence_denied(
        store,
        classification=Classification.PUBLIC,
        artifact_type="public-without-approval",
        **common,
    )
    approved_at = datetime.now(UTC)
    approved_artifact_type = "public-signed-approval"
    approved_artifact_id = f"S11-P02-P03-{run_id}-{approved_artifact_type}"
    approved_content_sha256 = sha256_bytes(common["content"])
    synthetic_signing_key = os.urandom(32)
    synthetic_verification_key = publication_public_key_from_private(synthetic_signing_key)
    valid_approval = create_publication_approval(
        approval_id=f"S11-PUBLIC-{approved_content_sha256[:16]}",
        artifact_id=approved_artifact_id,
        content_sha256=approved_content_sha256,
        approved_at=approved_at,
        signing_key=synthetic_signing_key,
    )
    wrong_approval = replace(valid_approval, approved_by=COAUTHOR_RESEARCHER)
    public_wrong_approver_denied = _persistence_denied(
        store,
        classification=Classification.PUBLIC,
        artifact_type="public-wrong-approver",
        publication_approval=wrong_approval,
        **common,
    )
    public_untrusted_key_denied = _persistence_denied(
        store,
        classification=Classification.PUBLIC,
        artifact_type=approved_artifact_type,
        publication_approval=valid_approval,
        **common,
    )
    signed_public_approval_verified = (
        validate_public_approval(
            Classification.PUBLIC,
            valid_approval,
            expected_artifact_id=approved_artifact_id,
            expected_content_sha256=approved_content_sha256,
            verification_key=synthetic_verification_key,
        )
        == valid_approval.to_dict()
    )
    secret_denied = _persistence_denied(
        store,
        classification=Classification.SECRET,
        artifact_type="secret-blocked",
        **common,
    )
    sensitive_denied = _persistence_denied(
        store,
        classification=Classification.SENSITIVE,
        artifact_type="sensitive-blocked",
        **common,
    )
    checks = {
        "public_requires_signed_monty_approval": (
            public_without_approval_denied
            and public_wrong_approver_denied
            and public_untrusted_key_denied
            and signed_public_approval_verified
        ),
        "public_runtime_rejects_untrusted_private_key": public_untrusted_key_denied,
        "secret_non_persistent": secret_denied,
        "sensitive_disabled": sensitive_denied,
    }
    if not all(checks.values()):
        raise CustodyError(f"Falló un gate de clasificación: {checks}")
    return checks


def _persistence_denied(store: EvidenceStore, **kwargs: Any) -> bool:
    try:
        store.persist_bytes(**kwargs)
    except (CustodyPolicyError, EvidencePolicyError):
        return True
    return False


def _access_checks() -> list[dict[str, str | bool]]:
    cases = (
        (PRIMARY_CUSTODIAN, Classification.INTERNAL, AccessOperation.READ, True),
        (PRIMARY_CUSTODIAN, Classification.INTERNAL, AccessOperation.WRITE, True),
        (COAUTHOR_RESEARCHER, Classification.EXPERIMENTAL, AccessOperation.READ, True),
        (COAUTHOR_RESEARCHER, Classification.EXPERIMENTAL, AccessOperation.WRITE, True),
        (COAUTHOR_RESEARCHER, Classification.EXPERIMENTAL, AccessOperation.EXECUTE, True),
        (COAUTHOR_RESEARCHER, Classification.EXPERIMENTAL, AccessOperation.REVIEW, True),
        (COAUTHOR_RESEARCHER, Classification.PUBLIC, AccessOperation.READ, True),
        (COAUTHOR_RESEARCHER, Classification.PUBLIC, AccessOperation.WRITE, False),
        (COAUTHOR_RESEARCHER, Classification.PUBLIC, AccessOperation.DELETE, False),
        (COAUTHOR_RESEARCHER, Classification.PUBLIC, AccessOperation.PUBLISH, False),
        (ACADEMIC_RESPONSIBLE, Classification.INTERNAL, AccessOperation.READ, False),
        ("UNAUTHORIZED", Classification.INTERNAL, AccessOperation.READ, False),
        (COAUTHOR_RESEARCHER, Classification.INTERNAL, AccessOperation.DELETE, False),
        (PRIMARY_CUSTODIAN, Classification.INTERNAL, AccessOperation.DELETE, False),
        (APPLICATION_PROCESS, Classification.INTERNAL, AccessOperation.WRITE, True),
        (TEST_RUNNER, Classification.EXPERIMENTAL, AccessOperation.EXECUTE, True),
        (STORAGE_PROCESS, Classification.INTERNAL, AccessOperation.DELETE, False),
        (BACKUP_PROCESS, Classification.INTERNAL, AccessOperation.BACKUP, True),
        (BACKUP_PROCESS, Classification.PUBLIC, AccessOperation.BACKUP, True),
        (RESTORE_PROCESS, Classification.INTERNAL, AccessOperation.RESTORE, True),
        (RESTORE_PROCESS, Classification.PUBLIC, AccessOperation.RESTORE, True),
        (PRIMARY_CUSTODIAN, Classification.SENSITIVE, AccessOperation.READ, False),
        (PRIMARY_CUSTODIAN, Classification.SECRET, AccessOperation.READ, False),
    )
    decisions = []
    for identity, classification, operation, expected in cases:
        decision = evaluate_access(identity, classification, operation)
        if decision.allowed is not expected:
            raise CustodyError(f"La matriz nominal no produjo el resultado esperado: {decision}")
        decisions.append(decision.to_dict())
    return decisions


def _directory_hashes(path: Path, *, exclude: set[str] | None = None) -> dict[str, str]:
    if path.is_symlink() or not path.is_dir():
        raise CustodyError(f"La ruta de custodia no es un directorio regular: {path}")
    excluded = exclude or set()
    result = {}
    for candidate in sorted(path.rglob("*")):
        if candidate.is_symlink():
            raise CustodyError("La custodia no admite enlaces simbólicos.")
        if candidate.is_file():
            relative = candidate.relative_to(path).as_posix()
            if relative not in excluded:
                result[relative] = sha256_file(candidate)
    return result


def _validate_retention_for_destruction(
    run_root: Path,
    records: list[dict[str, Any]],
    *,
    custody_hmac_key: bytes,
) -> None:
    finalized_state = validate_finalized_state(
        run_root,
        records,
        custody_hmac_key=custody_hmac_key,
    )
    final_records = [
        record for record in records if record.get("artifact_type") == "run-manifest-final"
    ]
    if final_records:
        manifest_path = (run_root / final_records[0]["relative_path"]).resolve()
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            expected_started_at = _parse_utc(manifest["finished_at"])
        except (KeyError, OSError, TypeError, json.JSONDecodeError) as exc:
            raise CustodyError("No se pudo validar finished_at del manifest final.") from exc
        required_anchor = "finished_at"
    elif finalized_state is not None:
        expected_started_at = _parse_utc(finalized_state["finished_at"])
        required_anchor = "finished_at"
    else:
        if not records or any(record.get("scenario_id") != "P02-P03" for record in records):
            raise CustodyError("La destrucción requiere un manifest final autenticado.")
        expected_started_at = None
        required_anchor = "verification_finished_at"

    for record in records:
        classification = Classification(record["classification"])
        started_at = _parse_utc(record["retention_started_at"])
        if classification is Classification.PUBLIC:
            approval = record.get("publication_approval")
            if (
                not isinstance(approval, dict)
                or record["retention_anchor"] != "publication_approval"
                or started_at != _parse_utc(approval["approved_at"])
                or record.get("expires_at") is not None
            ):
                raise CustodyError("La retención PUBLIC no está ligada a su aprobación.")
            continue
        if classification not in {Classification.INTERNAL, Classification.EXPERIMENTAL}:
            raise CustodyError("La custodia contiene una clasificación no destruible.")
        if record["retention_anchor"] != required_anchor:
            raise CustodyError("La retención no está ligada al finished_at aprobado del run.")
        if expected_started_at is not None and started_at != expected_started_at:
            raise CustodyError("La retención no coincide con finished_at del manifest final.")
        expected_expiration = expiration_for(classification, started_at)
        if record.get("expires_at") != _format_utc(expected_expiration):
            raise CustodyError("expires_at no coincide con la política de retención.")


def _read_checksum_lines(path: Path) -> list[tuple[str, str]]:
    result = []
    for line in path.read_text(encoding="utf-8").splitlines():
        digest, separator, relative = line.partition("  ")
        if not separator or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise CustodyError("SHA256SUMS contiene una línea inválida.")
        result.append((digest, relative))
    return result


def _write_private(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(_PRIVATE_DIRECTORY_MODE)
    with path.open("xb") as destination:
        destination.write(content)
        destination.flush()
        os.fsync(destination.fileno())
    path.chmod(_PRIVATE_FILE_MODE)


def _atomic_write_private(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(_PRIVATE_DIRECTORY_MODE)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, _PRIVATE_FILE_MODE)
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(content)
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary, path)
        path.chmod(_PRIVATE_FILE_MODE)
        directory_descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if temporary.exists():
            temporary.unlink()


def _bundle_paths(path: Path) -> tuple[Path, Path, Path, Path]:
    return (
        path,
        path.with_suffix(path.suffix + ".sha256"),
        path.with_suffix(path.suffix + ".hmac"),
        path.with_suffix(path.suffix + ".commit"),
    )


def _bundle_committed(path: Path) -> bool:
    return _bundle_paths(path)[3].is_file()


def _discard_incomplete_bundle(path: Path) -> None:
    files = _bundle_paths(path)
    if files[3].is_file():
        return
    for candidate in files:
        if candidate.is_symlink() or candidate.is_file():
            candidate.unlink()
        elif candidate.exists():
            raise CustodyError("Un bundle incompleto contiene una ruta no regular.")


def _write_authenticated_bundle(path: Path, content: bytes, signature_value: str) -> str:
    _discard_incomplete_bundle(path)
    data_path, checksum_path, signature_path, commit_path = _bundle_paths(path)
    digest = sha256_bytes(content)
    checksum_content = f"{digest}  {path.name}\n".encode()
    signature_content = f"{signature_value}\n".encode()
    try:
        _write_private(data_path, content)
        _write_private(checksum_path, checksum_content)
        _write_private(signature_path, signature_content)
        commit_content = canonical_json_bytes(
            {
                "checksum_sha256": sha256_bytes(checksum_content),
                "content_sha256": digest,
                "signature_sha256": sha256_bytes(signature_content),
            }
        )
        _atomic_write_private(commit_path, commit_content)
    except BaseException:
        # El bundle sin marker no es visible como evidencia confirmada y se recupera al reintentar.
        raise
    return digest


def _validate_bundle_commit(path: Path) -> None:
    data_path, checksum_path, signature_path, commit_path = _bundle_paths(path)
    if not all(
        candidate.is_file() and not candidate.is_symlink() for candidate in _bundle_paths(path)
    ):
        raise CustodyError("La evidencia autenticada no está confirmada o es incompleta.")
    try:
        commit = json.loads(commit_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CustodyError("El marker del bundle autenticado no es válido.") from exc
    expected = {
        "checksum_sha256": sha256_file(checksum_path),
        "content_sha256": sha256_file(data_path),
        "signature_sha256": sha256_file(signature_path),
    }
    if commit != expected:
        raise CustodyError("El marker del bundle autenticado no coincide con sus archivos.")


def _write_transaction(
    path: Path,
    checksum: Path,
    signature: Path,
    payload: dict[str, Any],
    custody_hmac_key: bytes,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(_PRIVATE_DIRECTORY_MODE)
    content = canonical_json_bytes(payload)
    signature_value = create_custody_transaction_signature(
        content,
        custody_hmac_key=custody_hmac_key,
    )
    expected_checksum = path.with_suffix(path.suffix + ".sha256")
    expected_signature = path.with_suffix(path.suffix + ".hmac")
    if checksum != expected_checksum or signature != expected_signature:
        raise CustodyError("Las rutas del bundle de transacción no son canónicas.")
    _write_authenticated_bundle(path, content, signature_value)


def _read_transaction(
    path: Path,
    checksum: Path,
    signature: Path,
    custody_hmac_key: bytes,
) -> dict[str, Any]:
    _validate_bundle_commit(path)
    content = path.read_bytes()
    expected = checksum.read_text(encoding="utf-8").split()[0]
    if sha256_bytes(content) != expected:
        raise CustodyError("La transacción de destrucción no coincide con su SHA-256.")
    try:
        validate_custody_transaction_signature(
            content,
            signature.read_text(encoding="ascii").strip(),
            custody_hmac_key=custody_hmac_key,
        )
    except (CustodyPolicyError, OSError) as exc:
        raise CustodyError("La transacción de destrucción no tiene una firma válida.") from exc
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise CustodyError("La transacción de destrucción no contiene JSON válido.") from exc
    if not isinstance(payload, dict):
        raise CustodyError("La transacción de destrucción no es un objeto JSON.")
    return payload


def _validate_destruction_transaction(
    transaction: dict[str, Any],
    *,
    custody_hmac_key: bytes,
    publication_verification_key: bytes,
) -> None:
    required = {
        "artifact_ids",
        "classification",
        "directory_hashes",
        "executing_identity",
        "expired_records",
        "locations_planned",
        "method",
        "original_records",
        "remaining_records",
        "requested_at",
        "run_id",
        "verification_id",
    }
    if not required.issubset(transaction):
        raise CustodyError("La transacción de destrucción no contiene el esquema completo.")
    expired = transaction["expired_records"]
    remaining = transaction["remaining_records"]
    original = transaction["original_records"]
    if not all(isinstance(records, list) for records in (expired, remaining, original)):
        raise CustodyError("La transacción de destrucción contiene inventarios inválidos.")
    try:
        original_by_id = {record["artifact_id"]: record for record in original}
        partition = expired + remaining
        partition_by_id = {record["artifact_id"]: record for record in partition}
    except (KeyError, TypeError) as exc:
        raise CustodyError("La transacción contiene un registro inválido.") from exc
    if (
        len(original_by_id) != len(original)
        or len(partition_by_id) != len(partition)
        or original_by_id != partition_by_id
    ):
        raise CustodyError("La transacción no conserva una partición exacta del inventario.")

    requested_at = _parse_utc(transaction["requested_at"])
    run_id = transaction["run_id"]
    expired_ids = []
    expired_classifications = set()
    for record in original:
        try:
            validate_custody_record_signature(
                record,
                custody_hmac_key=custody_hmac_key,
            )
            classification = Classification(record["classification"])
            approval_payload = record.get("publication_approval")
            approval = PublicationApproval(**approval_payload) if approval_payload else None
            validate_public_approval(
                classification,
                approval,
                expected_artifact_id=record["artifact_id"],
                expected_content_sha256=record["sha256"],
                verification_key=publication_verification_key,
            )
        except (CustodyPolicyError, KeyError, TypeError, ValueError) as exc:
            raise CustodyError("La transacción contiene un registro sin firma válida.") from exc
        if record.get("run_id") != run_id:
            raise CustodyError("La transacción mezcla registros de otro run.")
        if classification in {Classification.INTERNAL, Classification.EXPERIMENTAL}:
            if record.get("retention_anchor") not in {
                "finished_at",
                "verification_finished_at",
            }:
                raise CustodyError("La transacción contiene retención provisional.")
            started_at = _parse_utc(record.get("retention_started_at"))
            expected_expiration = expiration_for(classification, started_at)
            if record.get("expires_at") != _format_utc(expected_expiration):
                raise CustodyError("La transacción contiene una expiración inconsistente.")

    for record in expired:
        expires_at = record.get("expires_at")
        if expires_at is None or _parse_utc(expires_at) > requested_at:
            raise CustodyError("La transacción intenta destruir un artefacto vigente.")
        classification = Classification(record["classification"])
        if classification not in {Classification.INTERNAL, Classification.EXPERIMENTAL}:
            raise CustodyError("La transacción intenta destruir una clasificación permanente.")
        expired_ids.append(record["artifact_id"])
        expired_classifications.add(classification.value)
    for record in remaining:
        expires_at = record.get("expires_at")
        if expires_at is not None and _parse_utc(expires_at) <= requested_at:
            raise CustodyError("La transacción omite un artefacto que ya estaba vencido.")

    if sorted(expired_ids) != transaction["artifact_ids"]:
        raise CustodyError("La transacción no conserva los IDs de artefactos vencidos.")
    if ",".join(sorted(expired_classifications)) != transaction["classification"]:
        raise CustodyError("La transacción no conserva las clasificaciones vencidas.")
    if transaction["method"] != "artifact-delete+reindex+absence-check":
        raise CustodyError("La transacción usa un método de destrucción no aprobado.")


def _remove_transaction(path: Path, checksum: Path, signature: Path) -> None:
    for candidate in (
        path,
        checksum,
        signature,
        path.with_suffix(path.suffix + ".commit"),
    ):
        if candidate.exists():
            candidate.unlink()


def _destruction_receipt_from_payload(
    payload: dict[str, Any],
    receipt_path: Path,
    receipt_digest: str,
) -> DestructionReceipt:
    return DestructionReceipt(
        verification_id=payload["verification_id"],
        run_id=payload["run_id"],
        classification=payload["classification"],
        artifact_ids=tuple(payload["artifact_ids"]),
        remaining_artifact_ids=tuple(payload["remaining_artifact_ids"]),
        locations_affected=tuple(payload["locations_affected"]),
        destroyed_at=payload["destroyed_at"],
        executing_identity=payload["executing_identity"],
        method=payload["method"],
        result=payload["result"],
        payload_retained=payload["payload_retained"],
        intent_path=payload["intent_path"],
        intent_sha256=payload["intent_sha256"],
        receipt_path=str(receipt_path),
        receipt_sha256=receipt_digest,
    )


def _replace_private(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(content)
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary, path)
        path.chmod(_PRIVATE_FILE_MODE)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _destroy_records_in_run(
    run_root: Path,
    expired_records: list[dict[str, Any]],
    remaining_records: list[dict[str, Any]],
    original_hashes: dict[str, str],
    custody_hmac_key: bytes | None,
    publication_verification_key: bytes | None,
) -> None:
    expired_paths = {record["relative_path"] for record in expired_records}
    mutable_metadata = {
        "SHA256SUMS",
        f"{ArtifactKind.METADATA.value}/artifacts.jsonl",
        f"{ArtifactKind.METADATA.value}/artifacts.jsonl.hmac",
    }
    current_hashes = _directory_hashes(run_root)
    for relative, digest in current_hashes.items():
        if relative in mutable_metadata:
            continue
        if relative not in original_hashes or digest != original_hashes[relative]:
            raise CustodyError("Una copia parcial contiene un archivo nuevo o alterado.")
    for relative, digest in original_hashes.items():
        if relative in mutable_metadata or relative in expired_paths:
            continue
        if current_hashes.get(relative) != digest:
            raise CustodyError("Una copia parcial perdió un artefacto que debía conservarse.")
    if not remaining_records:
        shutil.rmtree(run_root)
        return
    for record in expired_records:
        target = (run_root / record["relative_path"]).resolve()
        if run_root not in target.parents or target.is_symlink():
            raise CustodyError("No se puede destruir una ruta de artefacto insegura.")
        if target.exists():
            if not target.is_file() or sha256_file(target) != record["sha256"]:
                raise CustodyError("Un artefacto vencido fue alterado antes de destruirse.")
            target.unlink()
    index = run_root / ArtifactKind.METADATA.value / "artifacts.jsonl"
    _replace_private(
        index,
        b"".join(canonical_json_bytes(record) for record in remaining_records),
    )
    custody_root = run_root.parents[1]
    store = EvidenceStore(
        custody_root,
        custody_hmac_key=custody_hmac_key,
        publication_verification_key=publication_verification_key,
    )
    store.write_index_signature(run_root.name)
    store.write_checksums(run_root.name)


def _harden_tree(root: Path) -> None:
    root.chmod(_PRIVATE_DIRECTORY_MODE)
    for path in root.rglob("*"):
        if path.is_symlink():
            raise CustodyError("La custodia copiada no admite enlaces simbólicos.")
        path.chmod(_PRIVATE_DIRECTORY_MODE if path.is_dir() else _PRIVATE_FILE_MODE)


def _assert_canary_secret_absent(*roots: Path) -> None:
    canary = b"S11-CANARY-MUST-NOT-PERSIST"
    for root in roots:
        for path in root.rglob("*"):
            if path.is_file() and canary in path.read_bytes():
                raise CustodyError(f"El canario SECRET apareció en evidencia: {path}")


def _current_execution_context() -> dict[str, str | int]:
    return {"hostname": socket.gethostname(), "pid": os.getpid()}


def _validated_execution_context(value: Any) -> dict[str, str | int]:
    if not isinstance(value, dict):
        raise CustodyError("La evidencia no contiene un contexto de ejecución válido.")
    hostname = value.get("hostname")
    pid = value.get("pid")
    if not isinstance(hostname, str) or not hostname or not isinstance(pid, int) or pid <= 0:
        raise CustodyError("La evidencia no contiene hostname y PID válidos.")
    return {"hostname": hostname, "pid": pid}


def _validated_source_snapshot(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CustodyError("La evidencia no contiene un snapshot de fuentes válido.")
    if value.get("baseline_sha") != EXPECTED_BASELINE_SHA:
        raise CustodyError("El snapshot no corresponde al baseline aprobado.")
    files = value.get("files")
    if not isinstance(files, dict) or set(files) != set(ATTESTED_SOURCE_PATHS):
        raise CustodyError("El snapshot no contiene exactamente las fuentes requeridas.")
    if any(not re.fullmatch(r"[0-9a-f]{64}", digest) for digest in files.values()):
        raise CustodyError("El snapshot contiene un SHA-256 inválido.")
    aggregate = sha256_bytes(canonical_json_bytes(files))
    if value.get("aggregate_sha256") != aggregate:
        raise CustodyError("El hash agregado del snapshot no coincide.")
    return {
        "aggregate_sha256": aggregate,
        "baseline_sha": EXPECTED_BASELINE_SHA,
        "files": {path: files[path] for path in sorted(files)},
    }


def _verify_persistence_boundary(
    boundary: str,
    *,
    prepare_context: dict[str, str | int],
    complete_context: dict[str, str | int],
) -> None:
    if boundary == "container-recreated":
        if prepare_context["hostname"] == complete_context["hostname"]:
            raise CustodyError("La persistencia no fue verificada entre contenedores distintos.")
        return
    if boundary == "independent-process":
        if prepare_context == complete_context:
            raise CustodyError("La persistencia no fue verificada entre procesos distintos.")
        return
    raise CustodyError("El límite de persistencia solicitado no está soportado.")


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise CustodyError("expires_at no es un timestamp ISO-8601 válido.") from exc
    return _aware_utc(parsed)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CustodyError("Los timestamps de custodia deben incluir zona horaria.")
    return value.astimezone(UTC)


def _format_utc(value: datetime) -> str:
    return _aware_utc(value).isoformat().replace("+00:00", "Z")
