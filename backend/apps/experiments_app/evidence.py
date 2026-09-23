from __future__ import annotations

import fcntl
import json
import mimetypes
import os
import re
import tempfile
import threading
from base64 import b64decode, b64encode
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .canonical import canonical_json_bytes, sha256_bytes, sha256_file
from .constants import METHODOLOGY_VERSION, ArtifactKind, Classification, RunStatus
from .custody_policy import (
    CustodyPolicyError,
    PublicationApproval,
    create_custody_finalization_signature,
    create_custody_index_signature,
    create_custody_record_signature,
    create_custody_seal_signature,
    expiration_for,
    validate_custody_finalization_signature,
    validate_custody_index_signature,
    validate_custody_record_signature,
    validate_custody_seal_signature,
    validate_public_approval,
)
from .manifest import ArtifactReference, ManifestError, RunManifest, validate_run_manifest
from .redaction import contains_secret, redact

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_FINALIZED_MARKER = ".s11-finalized"
_APPEND_LOCK = threading.RLock()


class EvidenceError(RuntimeError):
    pass


class EvidencePolicyError(EvidenceError):
    pass


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    artifact_id: str
    run_id: str
    scenario_id: str
    artifact_type: str
    kind: str
    classification: str
    relative_path: str
    created_at: str
    expires_at: str | None
    retention_anchor: str
    retention_started_at: str
    size_bytes: int
    mime_type: str
    sha256: str
    source: str
    transformation: str | None = None
    parent_artifact_id: str | None = None
    publication_approval: dict[str, str] | None = None
    custody_signature: str | None = None


class EvidenceStore:
    """Custodia local persistente y fail-closed para artefactos S11."""

    def __init__(
        self,
        root: Path,
        *,
        allow_sensitive: bool = False,
        custody_hmac_key: bytes | None = None,
        publication_verification_key: bytes | None = None,
        clock=lambda: datetime.now(UTC),
        rolling_checksums: bool = True,
    ) -> None:
        self.root = root.resolve()
        self.allow_sensitive = allow_sensitive
        self.custody_hmac_key = custody_hmac_key
        self.publication_verification_key = publication_verification_key
        self.clock = clock
        # `False` sólo para runs de varias sesiones (S11-C3/EX-09): entre lotes el run
        # queda abierto sin `SHA256SUMS`, que se escribe una única vez al finalizar.
        self.rolling_checksums = rolling_checksums
        self._mutation_depth = 0
        self._mutation_lock_file = None
        if self.root == Path("/tmp") or Path("/tmp") in self.root.parents:
            raise EvidencePolicyError("La custodia S11 no puede residir en /tmp.")
        if self.custody_hmac_key is None or len(self.custody_hmac_key) < 32:
            raise EvidencePolicyError("La custodia S11 requiere una clave HMAC de 32 bytes.")
        if (
            self.publication_verification_key is None
            or len(self.publication_verification_key) != 32
        ):
            raise EvidencePolicyError(
                "La custodia S11 requiere una clave pública Ed25519 de 32 bytes."
            )

    def initialize_run(self, run_id: str) -> Path:
        self._identifier(run_id, "run_id")
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o700)
        runs_root = self.root / "runs"
        runs_root.mkdir(parents=True, exist_ok=True)
        runs_root.chmod(0o700)
        run_root = runs_root / run_id
        for kind in ArtifactKind:
            directory = run_root / kind.value
            directory.mkdir(parents=True, exist_ok=True)
            directory.chmod(0o700)
        run_root.chmod(0o700)
        marker = run_root / ".s11-evidence-root"
        if not marker.exists():
            self._atomic_write(marker, b"S11-B persistent evidence root\n")
        return run_root

    def persist_json(
        self,
        *,
        run_id: str,
        scenario_id: str,
        artifact_type: str,
        kind: ArtifactKind,
        classification: Classification,
        filename: str,
        payload: Any,
        source: str,
        transformation: str | None = None,
        parent_artifact_id: str | None = None,
        publication_approval: PublicationApproval | None = None,
        retention_started_at: datetime | None = None,
        retention_anchor: str = "created_at",
    ) -> ArtifactRecord:
        sanitized = redact(payload)
        if contains_secret(sanitized):
            raise EvidencePolicyError("La evidencia conserva un patrón de secreto tras redacción.")
        return self.persist_bytes(
            run_id=run_id,
            scenario_id=scenario_id,
            artifact_type=artifact_type,
            kind=kind,
            classification=classification,
            filename=filename,
            content=canonical_json_bytes(sanitized),
            mime_type="application/json",
            source=source,
            transformation=transformation or "automatic-redaction+canonical-json",
            parent_artifact_id=parent_artifact_id,
            publication_approval=publication_approval,
            retention_started_at=retention_started_at,
            retention_anchor=retention_anchor,
        )

    def persist_bytes(
        self,
        *,
        run_id: str,
        scenario_id: str,
        artifact_type: str,
        kind: ArtifactKind,
        classification: Classification,
        filename: str,
        content: bytes,
        mime_type: str | None,
        source: str,
        transformation: str | None = None,
        parent_artifact_id: str | None = None,
        publication_approval: PublicationApproval | None = None,
        retention_started_at: datetime | None = None,
        retention_anchor: str = "created_at",
    ) -> ArtifactRecord:
        with self._mutation_lock():
            return self._persist_bytes_locked(
                run_id=run_id,
                scenario_id=scenario_id,
                artifact_type=artifact_type,
                kind=kind,
                classification=classification,
                filename=filename,
                content=content,
                mime_type=mime_type,
                source=source,
                transformation=transformation,
                parent_artifact_id=parent_artifact_id,
                publication_approval=publication_approval,
                retention_started_at=retention_started_at,
                retention_anchor=retention_anchor,
            )

    def _persist_bytes_locked(
        self,
        *,
        run_id: str,
        scenario_id: str,
        artifact_type: str,
        kind: ArtifactKind,
        classification: Classification,
        filename: str,
        content: bytes,
        mime_type: str | None,
        source: str,
        transformation: str | None = None,
        parent_artifact_id: str | None = None,
        publication_approval: PublicationApproval | None = None,
        retention_started_at: datetime | None = None,
        retention_anchor: str = "created_at",
    ) -> ArtifactRecord:
        for value, label in (
            (run_id, "run_id"),
            (scenario_id, "scenario_id"),
            (artifact_type, "artifact_type"),
        ):
            self._identifier(value, label)
        safe_name = Path(filename).name
        if safe_name != filename or not safe_name:
            raise EvidencePolicyError(
                "El nombre del artefacto debe ser un archivo relativo simple."
            )

        digest = sha256_bytes(content)
        artifact_id = f"S11-{scenario_id}-{run_id}-{artifact_type}"
        publication_approval_record = self._validate_policy(
            classification,
            publication_approval=publication_approval,
            artifact_id=artifact_id,
            content_sha256=digest,
        )
        created_at_value = self.clock()
        if classification is Classification.PUBLIC and publication_approval_record is not None:
            retention_started_at = _parse_utc(publication_approval_record["approved_at"])
            retention_anchor = "publication_approval"
        else:
            retention_started_at = retention_started_at or created_at_value
        try:
            expires_at_value = expiration_for(classification, retention_started_at)
        except CustodyPolicyError as exc:
            raise EvidencePolicyError(str(exc)) from exc
        created_at = created_at_value.astimezone(UTC).isoformat().replace("+00:00", "Z")
        expires_at = (
            expires_at_value.astimezone(UTC).isoformat().replace("+00:00", "Z")
            if expires_at_value is not None
            else None
        )

        run_root = self.initialize_run(run_id)
        self._require_mutable_run(run_root)
        relative_path = f"{kind.value}/{scenario_id}/{safe_name}"
        if any(
            record["artifact_id"] == artifact_id or record["relative_path"] == relative_path
            for record in self.records(run_id)
        ):
            raise EvidencePolicyError(
                "El artifact_id o la ruta ya están indexados y son inmutables."
            )
        directory = run_root / kind.value / scenario_id
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o700)
        target = directory / safe_name
        if target.exists():
            raise EvidencePolicyError(f"El artefacto ya existe y es inmutable: {safe_name}")
        self._atomic_write(target, content)

        relative_path = target.relative_to(run_root).as_posix()
        record = ArtifactRecord(
            artifact_id=artifact_id,
            run_id=run_id,
            scenario_id=scenario_id,
            artifact_type=artifact_type,
            kind=kind.value,
            classification=classification.value,
            relative_path=relative_path,
            created_at=created_at,
            expires_at=expires_at,
            retention_anchor=retention_anchor,
            retention_started_at=_format_utc(retention_started_at),
            size_bytes=len(content),
            mime_type=mime_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream",
            sha256=digest,
            source=source,
            transformation=transformation,
            parent_artifact_id=parent_artifact_id,
            publication_approval=publication_approval_record,
        )
        record = self._sign_record(record)
        self._append_index(run_root, record)
        return record

    def append_jsonl(
        self,
        *,
        run_id: str,
        scenario_id: str,
        stream: str,
        payload: Any,
    ) -> Path:
        for value, label in ((run_id, "run_id"), (scenario_id, "scenario_id"), (stream, "stream")):
            self._identifier(value, label)
        sanitized = redact(payload)
        if contains_secret(sanitized):
            raise EvidencePolicyError("El evento técnico conserva un patrón de secreto.")
        with self._mutation_lock():
            run_root = self.initialize_run(run_id)
            self._require_mutable_run(run_root)
            path = run_root / ArtifactKind.LOG.value / scenario_id / f"{stream}.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.parent.chmod(0o700)
            line = canonical_json_bytes(sanitized)
            with path.open("ab") as destination:
                destination.write(line)
                destination.flush()
                os.fsync(destination.fileno())
            path.chmod(0o600)
            return path

    def register_existing(
        self,
        *,
        run_id: str,
        scenario_id: str,
        artifact_type: str,
        kind: ArtifactKind,
        classification: Classification,
        filename: str,
        source: str,
        transformation: str | None = None,
        publication_approval: PublicationApproval | None = None,
        retention_started_at: datetime | None = None,
        retention_anchor: str = "created_at",
    ) -> ArtifactRecord:
        with self._mutation_lock():
            return self._register_existing_locked(
                run_id=run_id,
                scenario_id=scenario_id,
                artifact_type=artifact_type,
                kind=kind,
                classification=classification,
                filename=filename,
                source=source,
                transformation=transformation,
                publication_approval=publication_approval,
                retention_started_at=retention_started_at,
                retention_anchor=retention_anchor,
            )

    def _register_existing_locked(
        self,
        *,
        run_id: str,
        scenario_id: str,
        artifact_type: str,
        kind: ArtifactKind,
        classification: Classification,
        filename: str,
        source: str,
        transformation: str | None = None,
        publication_approval: PublicationApproval | None = None,
        retention_started_at: datetime | None = None,
        retention_anchor: str = "created_at",
    ) -> ArtifactRecord:
        """Cierra e indexa un stream ya persistido sin copiarlo ni modificarlo."""
        for value, label in (
            (run_id, "run_id"),
            (scenario_id, "scenario_id"),
            (artifact_type, "artifact_type"),
        ):
            self._identifier(value, label)
        safe_name = Path(filename).name
        if safe_name != filename or not safe_name:
            raise EvidencePolicyError(
                "El nombre del artefacto debe ser un archivo relativo simple."
            )

        run_root = self.initialize_run(run_id)
        self._require_mutable_run(run_root)
        target = run_root / kind.value / scenario_id / safe_name
        if target.is_symlink() or not target.is_file():
            raise EvidencePolicyError("El artefacto a indexar no es un archivo regular existente.")
        relative_path = target.relative_to(run_root).as_posix()
        if any(
            record["artifact_type"] == artifact_type or record["relative_path"] == relative_path
            for record in self.records(run_id)
        ):
            raise EvidencePolicyError("El artefacto existente ya fue indexado y es inmutable.")

        artifact_id = f"S11-{scenario_id}-{run_id}-{artifact_type}"
        content_sha256 = sha256_file(target)
        publication_approval_record = self._validate_policy(
            classification,
            publication_approval=publication_approval,
            artifact_id=artifact_id,
            content_sha256=content_sha256,
        )
        created_at_value = self.clock()
        if classification is Classification.PUBLIC and publication_approval_record is not None:
            retention_started_at = _parse_utc(publication_approval_record["approved_at"])
            retention_anchor = "publication_approval"
        else:
            retention_started_at = retention_started_at or created_at_value
        try:
            expires_at_value = expiration_for(classification, retention_started_at)
        except CustodyPolicyError as exc:
            raise EvidencePolicyError(str(exc)) from exc
        record = ArtifactRecord(
            artifact_id=artifact_id,
            run_id=run_id,
            scenario_id=scenario_id,
            artifact_type=artifact_type,
            kind=kind.value,
            classification=classification.value,
            relative_path=relative_path,
            created_at=created_at_value.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            expires_at=(
                expires_at_value.astimezone(UTC).isoformat().replace("+00:00", "Z")
                if expires_at_value is not None
                else None
            ),
            retention_anchor=retention_anchor,
            retention_started_at=_format_utc(retention_started_at),
            size_bytes=target.stat().st_size,
            mime_type=mimetypes.guess_type(safe_name)[0] or "application/octet-stream",
            sha256=content_sha256,
            source=source,
            transformation=transformation,
            publication_approval=publication_approval_record,
        )
        record = self._sign_record(record)
        self._append_index(run_root, record)
        return record

    def finalize_verification_retention(
        self, run_id: str, *, finished_at: datetime
    ) -> list[dict[str, Any]]:
        """Fija retención sólo para el canario sintético P02/P03 sin manifest experimental."""
        with self._mutation_lock():
            self._require_mutable_run(self.initialize_run(run_id))
            errors = self.verify(run_id)
            if errors:
                raise EvidencePolicyError(
                    f"No se puede reanclar un run cuya evidencia no es íntegra: {errors}"
                )
            records = self.records(run_id)
            if not records:
                raise EvidencePolicyError(
                    "No se puede finalizar retención de un run sin artefactos."
                )
            if any(record["scenario_id"] != "P02-P03" for record in records):
                raise EvidencePolicyError(
                    "La retención sin manifest sólo está permitida para el canario P02/P03."
                )
            return self._reanchor_retention_locked(
                run_id,
                records=records,
                finished_at=finished_at,
                retention_anchor="verification_finished_at",
            )

    def _reanchor_retention_locked(
        self,
        run_id: str,
        *,
        records: list[dict[str, Any]],
        finished_at: datetime,
        retention_anchor: str,
    ) -> list[dict[str, Any]]:
        finished_at = _aware_utc(finished_at)
        finalized = []
        for record in records:
            try:
                classification = Classification(record["classification"])
                if classification is Classification.PUBLIC:
                    approval = record.get("publication_approval")
                    if not approval:
                        raise CustodyPolicyError("PUBLIC carece de aprobación para su retención.")
                    started_at = _parse_utc(approval["approved_at"])
                    anchor = "publication_approval"
                else:
                    started_at = finished_at
                    anchor = retention_anchor
                expires_at = expiration_for(classification, started_at)
            except (KeyError, TypeError, ValueError, CustodyPolicyError) as exc:
                raise EvidencePolicyError(str(exc)) from exc
            updated = {
                **record,
                "expires_at": _format_utc(expires_at) if expires_at is not None else None,
                "retention_anchor": anchor,
                "retention_started_at": _format_utc(started_at),
            }
            finalized.append(self._sign_record_dict(updated))
        index = self.initialize_run(run_id) / ArtifactKind.METADATA.value / "artifacts.jsonl"
        self._atomic_replace(
            index,
            b"".join(canonical_json_bytes(record) for record in finalized),
        )
        self.write_index_signature(run_id)
        self.write_checksums(run_id)
        return finalized

    def finalize_with_json_artifact(
        self,
        *,
        finished_at: datetime,
        run_id: str,
        scenario_id: str,
        artifact_type: str,
        kind: ArtifactKind,
        classification: Classification,
        filename: str,
        payload: Any,
        source: str,
    ) -> ArtifactRecord:
        with self._mutation_lock():
            return self._finalize_with_json_artifact_locked(
                finished_at=finished_at,
                run_id=run_id,
                scenario_id=scenario_id,
                artifact_type=artifact_type,
                kind=kind,
                classification=classification,
                filename=filename,
                payload=payload,
                source=source,
            )

    def _finalize_with_json_artifact_locked(
        self,
        *,
        finished_at: datetime,
        run_id: str,
        scenario_id: str,
        artifact_type: str,
        kind: ArtifactKind,
        classification: Classification,
        filename: str,
        payload: Any,
        source: str,
    ) -> ArtifactRecord:
        """Reancla retención y añade el manifest final mediante journal recuperable."""
        finished_at = _aware_utc(finished_at)
        if (
            artifact_type != "run-manifest-final"
            or kind is not ArtifactKind.METADATA
            or classification is not Classification.INTERNAL
            or filename != "run-manifest.final.json"
        ):
            raise EvidencePolicyError(
                "La finalización exige el contrato exacto del manifest final INTERNAL."
            )
        if not isinstance(payload, dict) or payload.get("finished_at") != _format_utc(finished_at):
            raise EvidencePolicyError(
                "El finished_at del manifest final no coincide con la retención solicitada."
            )
        self.recover_finalization(run_id)
        run_root = self.initialize_run(run_id)
        self._require_mutable_run(run_root)
        safe_name = Path(filename).name
        if safe_name != filename or not safe_name:
            raise EvidencePolicyError(
                "El nombre del artefacto debe ser un archivo relativo simple."
            )
        index = run_root / ArtifactKind.METADATA.value / "artifacts.jsonl"
        index_signature = index.with_suffix(index.suffix + ".hmac")
        checksums = run_root / "SHA256SUMS"
        original_index = index.read_bytes()
        original_records = [
            json.loads(line) for line in original_index.decode("utf-8").splitlines()
        ]
        self._validate_final_manifest_payload(
            payload,
            run_root=run_root,
            run_id=run_id,
            scenario_id=scenario_id,
            finished_at=finished_at,
            records=original_records,
        )
        original_index_signature = (
            index_signature.read_bytes() if index_signature.is_file() else None
        )
        original_checksums = checksums.read_bytes() if checksums.is_file() else None
        target = run_root / kind.value / scenario_id / safe_name
        if target.exists():
            raise EvidencePolicyError(f"El artefacto ya existe y es inmutable: {safe_name}")
        journal_root = self.root / "finalization-transactions"
        journal_root.mkdir(parents=True, exist_ok=True)
        journal_root.chmod(0o700)
        journal = journal_root / f"{run_id}.json"
        journal_payload = {
            "original_checksums_b64": (
                b64encode(original_checksums).decode() if original_checksums is not None else None
            ),
            "original_index_b64": b64encode(original_index).decode(),
            "original_index_signature_b64": (
                b64encode(original_index_signature).decode()
                if original_index_signature is not None
                else None
            ),
            "run_id": run_id,
            "seal_relative_path": _FINALIZED_MARKER,
            "state": "PREPARED",
            "target_relative_path": target.relative_to(run_root).as_posix(),
        }
        self._write_finalization_journal(journal, journal_payload, replace=False)
        try:
            self._reanchor_retention_locked(
                run_id,
                records=original_records,
                finished_at=finished_at,
                retention_anchor="finished_at",
            )
            record = self.persist_json(
                run_id=run_id,
                scenario_id=scenario_id,
                artifact_type=artifact_type,
                kind=kind,
                classification=classification,
                filename=filename,
                payload=payload,
                source=source,
                retention_started_at=finished_at,
                retention_anchor="finished_at",
            )
            self._write_finalized_seal(
                run_root,
                artifact_id=record.artifact_id,
                manifest_sha256=record.sha256,
                finished_at=finished_at,
                run_id=run_id,
            )
            self.write_checksums(run_id)
            self._commit_finalization_journal(
                journal,
                {**journal_payload, "state": "COMMITTED"},
            )
            journal.unlink()
            return record
        except Exception:
            self.recover_finalization(run_id)
            raise

    def recover_finalization(self, run_id: str) -> str:
        with self._mutation_lock():
            return self._recover_finalization_locked(run_id)

    def _recover_finalization_locked(self, run_id: str) -> str:
        """Completa la decisión del journal después de excepción, kill o crash."""
        self._identifier(run_id, "run_id")
        journal = self.root / "finalization-transactions" / f"{run_id}.json"
        if not journal.exists():
            return "NONE"
        try:
            envelope = json.loads(journal.read_text(encoding="utf-8"))
            payload = envelope["payload"]
            signature = envelope["signature"]
            if not isinstance(payload, dict) or not isinstance(signature, str):
                raise EvidencePolicyError("El journal de finalización no es un objeto firmado.")
            validate_custody_finalization_signature(
                canonical_json_bytes(payload),
                signature,
                custody_hmac_key=self.custody_hmac_key,
            )
            state = payload["state"]
            if payload["run_id"] != run_id:
                raise EvidencePolicyError("El journal pertenece a otro run.")
            if payload["seal_relative_path"] != _FINALIZED_MARKER:
                raise EvidencePolicyError("El journal contiene un sello de cierre desconocido.")
            if state == "PREPARED":
                run_root = self.initialize_run(run_id)
                target = (run_root / payload["target_relative_path"]).resolve()
                if run_root not in target.parents:
                    raise EvidencePolicyError("El journal contiene una ruta insegura.")
                if target.is_file() and not target.is_symlink():
                    target.unlink()
                seal = run_root / _FINALIZED_MARKER
                if seal.exists():
                    if seal.is_symlink() or not seal.is_file():
                        raise EvidencePolicyError("El sello de cierre no es un archivo regular.")
                    seal.unlink()
                index = run_root / ArtifactKind.METADATA.value / "artifacts.jsonl"
                index_signature = index.with_suffix(index.suffix + ".hmac")
                checksums = run_root / "SHA256SUMS"
                self._atomic_replace(index, b64decode(payload["original_index_b64"]))
                encoded_signature = payload["original_index_signature_b64"]
                if encoded_signature is None:
                    if index_signature.exists():
                        index_signature.unlink()
                else:
                    self._atomic_replace(index_signature, b64decode(encoded_signature))
                encoded_checksums = payload["original_checksums_b64"]
                if encoded_checksums is None:
                    if checksums.exists():
                        checksums.unlink()
                else:
                    self._atomic_replace(checksums, b64decode(encoded_checksums))
            elif state == "COMMITTED":
                run_root = self.initialize_run(run_id)
                validate_finalized_state(
                    run_root,
                    self.records(run_id),
                    custody_hmac_key=self.custody_hmac_key,
                )
            else:
                raise EvidencePolicyError("El journal tiene un estado desconocido.")
        except (CustodyPolicyError, KeyError, TypeError, ValueError) as exc:
            raise EvidencePolicyError("El journal de finalización no es recuperable.") from exc
        journal.unlink()
        return state

    def _commit_finalization_journal(self, path: Path, payload: dict[str, Any]) -> None:
        self._write_finalization_journal(path, payload, replace=True)

    def _write_finalization_journal(
        self,
        path: Path,
        payload: dict[str, Any],
        *,
        replace: bool,
    ) -> None:
        content = canonical_json_bytes(payload)
        envelope = canonical_json_bytes(
            {
                "payload": payload,
                "signature": create_custody_finalization_signature(
                    content,
                    custody_hmac_key=self.custody_hmac_key,
                ),
            }
        )
        if replace:
            self._atomic_replace(path, envelope)
        else:
            self._atomic_write(path, envelope)

    def _write_finalized_seal(
        self,
        run_root: Path,
        *,
        artifact_id: str,
        manifest_sha256: str,
        finished_at: datetime,
        run_id: str,
    ) -> None:
        payload = {
            "artifact_id": artifact_id,
            "finished_at": _format_utc(finished_at),
            "manifest_sha256": manifest_sha256,
            "run_id": run_id,
        }
        content = canonical_json_bytes(payload)
        envelope = canonical_json_bytes(
            {
                "payload": payload,
                "signature": create_custody_seal_signature(
                    content,
                    custody_hmac_key=self.custody_hmac_key,
                ),
            }
        )
        self._atomic_write(run_root / _FINALIZED_MARKER, envelope)

    @staticmethod
    def _validate_final_manifest_payload(
        payload: dict[str, Any],
        *,
        run_root: Path,
        run_id: str,
        scenario_id: str,
        finished_at: datetime,
        records: list[dict[str, Any]],
    ) -> None:
        required_fields = {
            "artifact_references",
            "configuration",
            "dataset_id",
            "dataset_sha256",
            "environment",
            "experiment_id",
            "finished_at",
            "git_clean",
            "git_sha",
            "invalidity_reason",
            "methodology_version",
            "run_id",
            "runtime",
            "scenario_id",
            "schema_version",
            "seed",
            "started_at",
            "status",
            "timezone",
        }
        if set(payload) != required_fields:
            raise EvidencePolicyError("El manifest final no conserva su schema exacto.")
        try:
            references = tuple(
                ArtifactReference(**reference) for reference in payload["artifact_references"]
            )
            manifest = RunManifest(**{**payload, "artifact_references": references})
            validate_run_manifest(manifest)
            status = RunStatus(manifest.status)
        except (ManifestError, TypeError, ValueError) as exc:
            raise EvidencePolicyError("El manifest final no cumple el schema RunManifest.") from exc
        if (
            manifest.schema_version != "s11-run-manifest-v1"
            or manifest.methodology_version != METHODOLOGY_VERSION
            or manifest.run_id != run_id
            or manifest.scenario_id != scenario_id
            or manifest.finished_at != _format_utc(finished_at)
            or status not in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.INVALID}
        ):
            raise EvidencePolicyError("El manifest final no coincide con el run que se cierra.")
        if status is RunStatus.INVALID and not manifest.invalidity_reason:
            raise EvidencePolicyError("Un manifest INVALID requiere invalidity_reason.")
        if status is not RunStatus.INVALID and manifest.invalidity_reason is not None:
            raise EvidencePolicyError("Sólo un manifest INVALID admite invalidity_reason.")
        expected_references = tuple(
            ArtifactReference(
                artifact_id=record["artifact_id"],
                relative_path=record["relative_path"],
                sha256=record["sha256"],
            )
            for record in records
        )
        if references != expected_references:
            raise EvidencePolicyError(
                "El manifest final no referencia exactamente el inventario firmado."
            )
        initial = [
            record for record in records if record.get("artifact_type") == "run-manifest-initial"
        ]
        if len(initial) != 1 or initial[0].get("scenario_id") != scenario_id:
            raise EvidencePolicyError("El manifest final requiere un único manifest inicial.")
        try:
            initial_path = (run_root / initial[0]["relative_path"]).resolve()
            if run_root not in initial_path.parents or initial_path.is_symlink():
                raise EvidencePolicyError("El manifest inicial usa una ruta insegura.")
            initial_payload = json.loads(initial_path.read_text(encoding="utf-8"))
            if set(initial_payload) != required_fields:
                raise EvidencePolicyError("El manifest inicial no conserva su schema exacto.")
            initial_references = tuple(
                ArtifactReference(**reference)
                for reference in initial_payload["artifact_references"]
            )
            initial_manifest = RunManifest(
                **{**initial_payload, "artifact_references": initial_references}
            )
            validate_run_manifest(initial_manifest)
        except (
            json.JSONDecodeError,
            KeyError,
            ManifestError,
            OSError,
            TypeError,
            ValueError,
        ) as exc:
            raise EvidencePolicyError(
                "El manifest inicial no cumple el schema RunManifest."
            ) from exc
        mutable_fields = {
            "artifact_references",
            "finished_at",
            "invalidity_reason",
            "status",
        }
        if any(
            initial_payload[field] != payload[field] for field in required_fields - mutable_fields
        ):
            raise EvidencePolicyError(
                "El manifest final altera procedencia fijada por el manifest inicial."
            )

    def records(self, run_id: str) -> list[dict[str, Any]]:
        path = self.initialize_run(run_id) / ArtifactKind.METADATA.value / "artifacts.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def verify(self, run_id: str) -> list[str]:
        run_root = self.initialize_run(run_id)
        errors: list[str] = []
        index = run_root / ArtifactKind.METADATA.value / "artifacts.jsonl"
        index_signature = index.with_suffix(index.suffix + ".hmac")
        try:
            validate_custody_index_signature(
                index.read_bytes(),
                index_signature.read_text(encoding="ascii").strip(),
                custody_hmac_key=self.custody_hmac_key,
            )
        except (CustodyPolicyError, OSError):
            errors.append("index-signature")
        for record in self.records(run_id):
            path = run_root / record["relative_path"]
            if not path.is_file():
                errors.append(f"missing:{record['artifact_id']}")
            elif sha256_file(path) != record["sha256"]:
                errors.append(f"sha256:{record['artifact_id']}")
            try:
                validate_custody_record_signature(
                    record,
                    custody_hmac_key=self.custody_hmac_key,
                )
                approval_payload = record.get("publication_approval")
                if approval_payload is not None and not isinstance(approval_payload, dict):
                    raise CustodyPolicyError("La aprobación almacenada no es un objeto.")
                approval = (
                    PublicationApproval(**approval_payload)
                    if approval_payload is not None
                    else None
                )
                validate_public_approval(
                    Classification(record["classification"]),
                    approval,
                    expected_artifact_id=record["artifact_id"],
                    expected_content_sha256=record["sha256"],
                    verification_key=self.publication_verification_key,
                )
            except (CustodyPolicyError, TypeError, ValueError):
                errors.append(f"policy:{record['artifact_id']}")
        try:
            validate_finalized_state(
                run_root,
                self.records(run_id),
                custody_hmac_key=self.custody_hmac_key,
            )
        except EvidencePolicyError:
            errors.append("finalized-state")
        return errors

    def write_checksums(self, run_id: str) -> Path:
        run_root = self.initialize_run(run_id)
        index = run_root / ArtifactKind.METADATA.value / "artifacts.jsonl"
        paths = [path for path in run_root.rglob("*") if path.is_file()]
        checksums = run_root / "SHA256SUMS"
        paths = [path for path in paths if path != checksums]
        lines = [
            f"{sha256_file(path)}  {path.relative_to(run_root).as_posix()}"
            for path in sorted(paths)
        ]
        self._atomic_replace(checksums, ("\n".join(lines) + ("\n" if lines else "")).encode())
        if index.exists() and index not in paths:
            raise EvidenceError("El índice de artefactos no entró en SHA256SUMS.")
        return checksums

    def write_index_signature(self, run_id: str) -> Path:
        run_root = self.initialize_run(run_id)
        index = run_root / ArtifactKind.METADATA.value / "artifacts.jsonl"
        signature_path = index.with_suffix(index.suffix + ".hmac")
        signature = create_custody_index_signature(
            index.read_bytes(),
            custody_hmac_key=self.custody_hmac_key,
        )
        self._atomic_replace(signature_path, f"{signature}\n".encode())
        return signature_path

    def _append_index(self, run_root: Path, record: ArtifactRecord) -> None:
        path = run_root / ArtifactKind.METADATA.value / "artifacts.jsonl"
        line = canonical_json_bytes(asdict(record))
        with self._mutation_lock():
            with path.open("ab") as destination:
                destination.write(line)
                destination.flush()
                os.fsync(destination.fileno())
            path.chmod(0o600)
            self.write_index_signature(run_root.name)
            if self.rolling_checksums:
                self.write_checksums(run_root.name)

    @contextmanager
    def mutation(self):
        """Mantiene la exclusión de mutaciones para una operación compuesta del caller."""
        with self._mutation_lock():
            yield

    @contextmanager
    def _mutation_lock(self):
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o700)
        lock_path = self.root / ".s11-custody.lock"
        _APPEND_LOCK.acquire()
        try:
            if self._mutation_depth == 0:
                self._mutation_lock_file = lock_path.open("a+b")
                lock_path.chmod(0o600)
                fcntl.flock(self._mutation_lock_file.fileno(), fcntl.LOCK_EX)
            self._mutation_depth += 1
            try:
                yield
            finally:
                self._mutation_depth -= 1
                if self._mutation_depth == 0:
                    fcntl.flock(self._mutation_lock_file.fileno(), fcntl.LOCK_UN)
                    self._mutation_lock_file.close()
                    self._mutation_lock_file = None
        finally:
            _APPEND_LOCK.release()

    def _require_mutable_run(self, run_root: Path) -> None:
        index = run_root / ArtifactKind.METADATA.value / "artifacts.jsonl"
        signature = index.with_suffix(index.suffix + ".hmac")
        seal = run_root / _FINALIZED_MARKER
        if not index.exists():
            if seal.exists():
                raise EvidencePolicyError("El run tiene un sello huérfano y falla cerrado.")
            return
        try:
            content = index.read_bytes()
            validate_custody_index_signature(
                content,
                signature.read_text(encoding="ascii").strip(),
                custody_hmac_key=self.custody_hmac_key,
            )
            records = [json.loads(line) for line in content.decode().splitlines()]
            finalized = validate_finalized_state(
                run_root,
                records,
                custody_hmac_key=self.custody_hmac_key,
            )
        except (CustodyPolicyError, OSError, TypeError, ValueError) as exc:
            raise EvidencePolicyError(
                "El estado de cierre del run no es íntegro; la mutación falla cerrado."
            ) from exc
        if finalized is not None:
            raise EvidencePolicyError("El run está finalizado y no admite nuevas mutaciones.")

    def _validate_policy(
        self,
        classification: Classification,
        *,
        publication_approval: PublicationApproval | None,
        artifact_id: str,
        content_sha256: str,
    ) -> dict[str, str] | None:
        if classification is Classification.SECRET:
            raise EvidencePolicyError("Los artefactos SECRET nunca se persisten.")
        if classification is Classification.SENSITIVE and not self.allow_sensitive:
            raise EvidencePolicyError(
                "SENSITIVE permanece deshabilitado durante S11-C y no se persiste."
            )
        try:
            return validate_public_approval(
                classification,
                publication_approval,
                expected_artifact_id=artifact_id,
                expected_content_sha256=content_sha256,
                verification_key=self.publication_verification_key,
            )
        except CustodyPolicyError as exc:
            raise EvidencePolicyError(str(exc)) from exc

    def _sign_record(self, record: ArtifactRecord) -> ArtifactRecord:
        payload = self._sign_record_dict(asdict(record))
        return ArtifactRecord(**payload)

    def _sign_record_dict(self, record: dict[str, Any]) -> dict[str, Any]:
        payload = {**record, "custody_signature": None}
        payload["custody_signature"] = create_custody_record_signature(
            payload,
            custody_hmac_key=self.custody_hmac_key,
        )
        return payload

    @staticmethod
    def _identifier(value: str, label: str) -> None:
        if not _IDENTIFIER.fullmatch(value):
            raise EvidencePolicyError(f"{label} no tiene un formato seguro.")

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as destination:
            destination.write(content)
            destination.flush()
            os.fsync(destination.fileno())
        path.chmod(0o600)

    @staticmethod
    def _atomic_replace(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as destination:
                destination.write(content)
                destination.flush()
                os.fsync(destination.fileno())
            os.replace(temporary, path)
            path.chmod(0o600)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def validate_finalized_state(
    run_root: Path,
    records: list[dict[str, Any]],
    *,
    custody_hmac_key: bytes | None,
) -> dict[str, str] | None:
    """Valida el sello autenticado y su vínculo exacto con el manifest final indexado."""
    final_records = [
        record for record in records if record.get("artifact_type") == "run-manifest-final"
    ]
    seal = run_root / _FINALIZED_MARKER
    if not final_records and not seal.exists():
        return None
    if len(final_records) > 1:
        raise EvidencePolicyError("El run no contiene exactamente un manifest final.")
    if seal.is_symlink() or not seal.is_file():
        raise EvidencePolicyError("Falta el sello autenticado del run finalizado.")
    try:
        envelope = json.loads(seal.read_text(encoding="utf-8"))
        payload = envelope["payload"]
        signature = envelope["signature"]
        if not isinstance(payload, dict) or not isinstance(signature, str):
            raise TypeError
        validate_custody_seal_signature(
            canonical_json_bytes(payload),
            signature,
            custody_hmac_key=custody_hmac_key,
        )
        if not final_records:
            if payload.get("run_id") != run_root.name:
                raise EvidencePolicyError("El sello histórico pertenece a otro run.")
            if set(payload) != {
                "artifact_id",
                "finished_at",
                "manifest_sha256",
                "run_id",
            }:
                raise EvidencePolicyError("El sello histórico tiene un esquema inválido.")
            if not re.fullmatch(r"[0-9a-f]{64}", payload.get("manifest_sha256", "")):
                raise EvidencePolicyError("El sello histórico no conserva un SHA-256 válido.")
            if any(
                record.get("classification")
                not in {Classification.PUBLIC.value, Classification.EXPERIMENTAL.value}
                for record in records
            ):
                raise EvidencePolicyError(
                    "El manifest final sólo puede faltar tras vencer todos los INTERNAL."
                )
            finished_at = _parse_utc(payload["finished_at"])
            for record in records:
                classification = Classification(record["classification"])
                started_at = _parse_utc(record["retention_started_at"])
                if classification is Classification.EXPERIMENTAL:
                    if (
                        record.get("retention_anchor") != "finished_at"
                        or started_at != finished_at
                        or record.get("expires_at")
                        != _format_utc(expiration_for(classification, finished_at))
                    ):
                        raise EvidencePolicyError(
                            "La retención EXPERIMENTAL no coincide con el sello histórico."
                        )
                else:
                    approval = record.get("publication_approval")
                    if (
                        not isinstance(approval, dict)
                        or record.get("retention_anchor") != "publication_approval"
                        or started_at != _parse_utc(approval["approved_at"])
                        or record.get("expires_at") is not None
                    ):
                        raise EvidencePolicyError(
                            "La retención PUBLIC no coincide con su aprobación."
                        )
            return payload
        record = final_records[0]
        manifest_path = (run_root / record["relative_path"]).resolve()
        if run_root not in manifest_path.parents or manifest_path.is_symlink():
            raise EvidencePolicyError("El manifest final usa una ruta insegura.")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected = {
            "artifact_id": record["artifact_id"],
            "finished_at": manifest["finished_at"],
            "manifest_sha256": record["sha256"],
            "run_id": record["run_id"],
        }
        if payload != expected:
            raise EvidencePolicyError("El sello no coincide con el manifest final indexado.")
        _parse_utc(payload["finished_at"])
    except (CustodyPolicyError, KeyError, OSError, TypeError, ValueError) as exc:
        raise EvidencePolicyError("El sello de cierre no es válido.") from exc
    return payload


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise EvidencePolicyError("El timestamp de retención no es ISO-8601 válido.") from exc
    return _aware_utc(parsed)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise EvidencePolicyError("Los timestamps de retención deben incluir zona horaria.")
    return value.astimezone(UTC)


def _format_utc(value: datetime) -> str:
    return _aware_utc(value).isoformat().replace("+00:00", "Z")
