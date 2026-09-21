from __future__ import annotations

import platform
import re
import subprocess
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .canonical import canonical_json_bytes, sha256_bytes
from .constants import METHODOLOGY_VERSION, RunStatus

_SHA = re.compile(r"^[0-9a-f]{40}$")
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
