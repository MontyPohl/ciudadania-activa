from __future__ import annotations

import json
import mimetypes
import os
import re
import tempfile
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .canonical import canonical_json_bytes, sha256_bytes, sha256_file
from .constants import ArtifactKind, Classification
from .redaction import contains_secret, redact

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_APPEND_LOCK = threading.Lock()


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
    size_bytes: int
    mime_type: str
    sha256: str
    source: str
    transformation: str | None = None
    parent_artifact_id: str | None = None


class EvidenceStore:
    """Custodia local persistente y fail-closed para artefactos S11."""

    def __init__(
        self,
        root: Path,
        *,
        allow_sensitive: bool = False,
        clock=lambda: datetime.now(UTC),
    ) -> None:
        self.root = root.resolve()
        self.allow_sensitive = allow_sensitive
        self.clock = clock
        if self.root == Path("/tmp") or Path("/tmp") in self.root.parents:
            raise EvidencePolicyError("La custodia S11 no puede residir en /tmp.")

    def initialize_run(self, run_id: str) -> Path:
        self._identifier(run_id, "run_id")
        run_root = self.root / "runs" / run_id
        for kind in ArtifactKind:
            (run_root / kind.value).mkdir(parents=True, exist_ok=True)
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
    ) -> ArtifactRecord:
        self._validate_policy(classification)
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
        directory = run_root / kind.value / scenario_id
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / safe_name
        if target.exists():
            raise EvidencePolicyError(f"El artefacto ya existe y es inmutable: {safe_name}")
        self._atomic_write(target, content)

        created_at = self.clock().astimezone(UTC).isoformat().replace("+00:00", "Z")
        digest = sha256_bytes(content)
        relative_path = target.relative_to(run_root).as_posix()
        artifact_id = f"S11-{scenario_id}-{run_id}-{artifact_type}"
        record = ArtifactRecord(
            artifact_id=artifact_id,
            run_id=run_id,
            scenario_id=scenario_id,
            artifact_type=artifact_type,
            kind=kind.value,
            classification=classification.value,
            relative_path=relative_path,
            created_at=created_at,
            size_bytes=len(content),
            mime_type=mime_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream",
            sha256=digest,
            source=source,
            transformation=transformation,
            parent_artifact_id=parent_artifact_id,
        )
        self._append_index(run_root, record)
        self.write_checksums(run_id)
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
        run_root = self.initialize_run(run_id)
        path = run_root / ArtifactKind.LOG.value / scenario_id / f"{stream}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        line = canonical_json_bytes(sanitized)
        with _APPEND_LOCK, path.open("ab") as destination:
            destination.write(line)
            destination.flush()
            os.fsync(destination.fileno())
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
    ) -> ArtifactRecord:
        """Cierra e indexa un stream ya persistido sin copiarlo ni modificarlo."""
        self._validate_policy(classification)
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
        target = run_root / kind.value / scenario_id / safe_name
        if target.is_symlink() or not target.is_file():
            raise EvidencePolicyError("El artefacto a indexar no es un archivo regular existente.")
        relative_path = target.relative_to(run_root).as_posix()
        if any(
            record["artifact_type"] == artifact_type or record["relative_path"] == relative_path
            for record in self.records(run_id)
        ):
            raise EvidencePolicyError("El artefacto existente ya fue indexado y es inmutable.")

        record = ArtifactRecord(
            artifact_id=f"S11-{scenario_id}-{run_id}-{artifact_type}",
            run_id=run_id,
            scenario_id=scenario_id,
            artifact_type=artifact_type,
            kind=kind.value,
            classification=classification.value,
            relative_path=relative_path,
            created_at=self.clock().astimezone(UTC).isoformat().replace("+00:00", "Z"),
            size_bytes=target.stat().st_size,
            mime_type=mimetypes.guess_type(safe_name)[0] or "application/octet-stream",
            sha256=sha256_file(target),
            source=source,
            transformation=transformation,
        )
        self._append_index(run_root, record)
        self.write_checksums(run_id)
        return record

    def records(self, run_id: str) -> list[dict[str, Any]]:
        path = self.initialize_run(run_id) / ArtifactKind.METADATA.value / "artifacts.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def verify(self, run_id: str) -> list[str]:
        run_root = self.initialize_run(run_id)
        errors: list[str] = []
        for record in self.records(run_id):
            path = run_root / record["relative_path"]
            if not path.is_file():
                errors.append(f"missing:{record['artifact_id']}")
            elif sha256_file(path) != record["sha256"]:
                errors.append(f"sha256:{record['artifact_id']}")
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

    def _append_index(self, run_root: Path, record: ArtifactRecord) -> None:
        path = run_root / ArtifactKind.METADATA.value / "artifacts.jsonl"
        line = canonical_json_bytes(asdict(record))
        with _APPEND_LOCK, path.open("ab") as destination:
            destination.write(line)
            destination.flush()
            os.fsync(destination.fileno())

    def _validate_policy(self, classification: Classification) -> None:
        if classification is Classification.SECRET:
            raise EvidencePolicyError("Los artefactos SECRET nunca se persisten.")
        if classification is Classification.SENSITIVE and not self.allow_sensitive:
            raise EvidencePolicyError(
                "SENSITIVE requiere que S11A-P02/P03 estén resueltas y autorización explícita."
            )

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
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
