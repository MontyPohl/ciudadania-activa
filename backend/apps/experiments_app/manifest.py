from __future__ import annotations

import platform
import re
import subprocess
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from .canonical import canonical_json_bytes, sha256_bytes
from .constants import METHODOLOGY_VERSION, RunStatus

_SHA = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,511}$")
_RUN_NAMESPACE = uuid.UUID("25523ed9-19c1-5c0d-812d-2f97d3825894")


class ManifestError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ArtifactReference:
    artifact_id: str
    relative_path: str
    sha256: str


@dataclass(frozen=True, slots=True)
class RunManifest:
    schema_version: str
    methodology_version: str
    run_id: str
    git_sha: str
    git_clean: bool
    started_at: str
    finished_at: str | None
    environment: str
    timezone: str
    seed: int
    dataset_id: str
    dataset_sha256: str
    scenario_id: str
    experiment_id: str
    status: str
    configuration: dict[str, Any]
    runtime: dict[str, str]
    artifact_references: tuple[ArtifactReference, ...] = field(default_factory=tuple)
    invalidity_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def sha256(self) -> str:
        return sha256_bytes(canonical_json_bytes(self.to_dict()))


def validate_run_manifest(manifest: RunManifest) -> None:
    """Valida en runtime el contrato que las anotaciones de la dataclass no imponen."""
    string_fields = (
        "schema_version",
        "methodology_version",
        "run_id",
        "git_sha",
        "started_at",
        "environment",
        "timezone",
        "dataset_id",
        "dataset_sha256",
        "scenario_id",
        "experiment_id",
        "status",
    )
    if any(
        not isinstance(getattr(manifest, field), str) or not getattr(manifest, field)
        for field in string_fields
    ):
        raise ManifestError("El manifest contiene strings obligatorios inválidos.")
    if manifest.schema_version != "s11-run-manifest-v1":
        raise ManifestError("schema_version no corresponde al contrato S11.")
    if manifest.methodology_version != METHODOLOGY_VERSION:
        raise ManifestError("methodology_version no corresponde al baseline congelado.")
    if not _IDENTIFIER.fullmatch(manifest.run_id):
        raise ManifestError("run_id no tiene un formato seguro.")
    if not _SHA.fullmatch(manifest.git_sha):
        raise ManifestError("git_sha no es un SHA-1 completo.")
    if not _SHA256.fullmatch(manifest.dataset_sha256):
        raise ManifestError("dataset_sha256 no es un SHA-256 completo.")
    if type(manifest.git_clean) is not bool or type(manifest.seed) is not int:
        raise ManifestError("git_clean y seed no conservan sus tipos contractuales.")
    if not isinstance(manifest.configuration, dict) or not all(
        isinstance(key, str) for key in manifest.configuration
    ):
        raise ManifestError("configuration debe ser un objeto con claves string.")
    if not isinstance(manifest.runtime, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in manifest.runtime.items()
    ):
        raise ManifestError("runtime debe mapear strings a strings.")
    if not isinstance(manifest.artifact_references, tuple):
        raise ManifestError("artifact_references debe ser una tupla validada.")
    for reference in manifest.artifact_references:
        if not isinstance(reference, ArtifactReference):
            raise ManifestError("artifact_references contiene un elemento inválido.")
        path = PurePosixPath(reference.relative_path)
        if (
            not isinstance(reference.artifact_id, str)
            or not _IDENTIFIER.fullmatch(reference.artifact_id)
            or not isinstance(reference.relative_path, str)
            or not reference.relative_path
            or path.is_absolute()
            or ".." in path.parts
            or not isinstance(reference.sha256, str)
            or not _SHA256.fullmatch(reference.sha256)
        ):
            raise ManifestError("Una referencia de artefacto no cumple el contrato.")

    started_at = _parse_utc_timestamp(manifest.started_at, "started_at")
    try:
        status = RunStatus(manifest.status)
    except ValueError as exc:
        raise ManifestError("status no pertenece al contrato S11.") from exc
    if manifest.finished_at is None:
        if status is not RunStatus.PREPARED or manifest.invalidity_reason is not None:
            raise ManifestError("Sólo PREPARED puede carecer de finished_at.")
    else:
        finished_at = _parse_utc_timestamp(manifest.finished_at, "finished_at")
        if finished_at < started_at:
            raise ManifestError("finished_at no puede preceder a started_at.")
        if status not in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.INVALID}:
            raise ManifestError("Un manifest final requiere status terminal.")
        if status is RunStatus.INVALID:
            if not isinstance(manifest.invalidity_reason, str) or not manifest.invalidity_reason:
                raise ManifestError("Un manifest INVALID requiere invalidity_reason.")
        elif manifest.invalidity_reason is not None:
            raise ManifestError("Sólo INVALID admite invalidity_reason.")


def _parse_utc_timestamp(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ManifestError(f"{field} no es ISO-8601 válido.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ManifestError(f"{field} debe representar UTC explícitamente.")
    return parsed.astimezone(UTC)


def make_run_id(
    *,
    started_at: datetime,
    git_sha: str,
    dataset_id: str,
    scenario_id: str,
    seed: int,
    execution_nonce: str,
) -> str:
    if not execution_nonce.strip():
        raise ManifestError("execution_nonce es obligatorio para garantizar unicidad controlada.")
    identity = "|".join(
        (
            started_at.astimezone(UTC).isoformat(timespec="microseconds"),
            git_sha,
            dataset_id,
            scenario_id,
            str(seed),
            execution_nonce,
        )
    )
    return f"s11-{uuid.uuid5(_RUN_NAMESPACE, identity)}"


def create_manifest(
    *,
    repo_root: Path,
    started_at: datetime,
    environment: str,
    timezone: str,
    seed: int,
    dataset_id: str,
    dataset_sha256: str,
    scenario_id: str,
    experiment_id: str,
    execution_nonce: str,
    configuration: dict[str, Any],
    expected_git_sha: str | None = None,
    supplied_git_sha: str | None = None,
    supplied_git_clean: bool | None = None,
) -> RunManifest:
    if (supplied_git_sha is None) != (supplied_git_clean is None):
        raise ManifestError("git_sha y git_clean externos deben suministrarse juntos.")
    git_sha = supplied_git_sha if supplied_git_sha is not None else git_head(repo_root)
    if expected_git_sha is not None and git_sha != expected_git_sha:
        raise ManifestError(f"HEAD no coincide con el commit esperado: {git_sha}")
    if not _SHA.fullmatch(git_sha):
        raise ManifestError("git_sha no es un SHA-1 completo.")
    if not re.fullmatch(r"[0-9a-f]{64}", dataset_sha256):
        raise ManifestError("dataset_sha256 no es un SHA-256 completo.")
    clean = supplied_git_clean if supplied_git_clean is not None else git_is_clean(repo_root)
    run_id = make_run_id(
        started_at=started_at,
        git_sha=git_sha,
        dataset_id=dataset_id,
        scenario_id=scenario_id,
        seed=seed,
        execution_nonce=execution_nonce,
    )
    return RunManifest(
        schema_version="s11-run-manifest-v1",
        methodology_version=METHODOLOGY_VERSION,
        run_id=run_id,
        git_sha=git_sha,
        git_clean=clean,
        started_at=started_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        finished_at=None,
        environment=environment,
        timezone=timezone,
        seed=seed,
        dataset_id=dataset_id,
        dataset_sha256=dataset_sha256,
        scenario_id=scenario_id,
        experiment_id=experiment_id,
        status=RunStatus.PREPARED.value,
        configuration=configuration,
        runtime={
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
    )


def finalize_manifest(
    manifest: RunManifest,
    *,
    finished_at: datetime,
    status: RunStatus,
    artifacts: list[ArtifactReference],
    invalidity_reason: str | None = None,
) -> RunManifest:
    if status is RunStatus.INVALID and not invalidity_reason:
        raise ManifestError("Un run INVALID debe registrar invalidity_reason.")
    data = manifest.to_dict()
    data.update(
        {
            "finished_at": finished_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "status": status.value,
            "artifact_references": tuple(artifacts),
            "invalidity_reason": invalidity_reason,
        }
    )
    return RunManifest(**data)


def git_head(repo_root: Path) -> str:
    return _git(repo_root, "rev-parse", "HEAD")


def git_is_clean(repo_root: Path) -> bool:
    return _git(repo_root, "status", "--porcelain") == ""


def _git(repo_root: Path, *args: str) -> str:
    process = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if process.returncode != 0:
        raise ManifestError("No se pudo consultar Git para construir el manifest.")
    return process.stdout.strip()
