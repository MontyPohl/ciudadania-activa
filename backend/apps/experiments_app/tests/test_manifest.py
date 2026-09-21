from datetime import UTC, datetime
from pathlib import Path

import pytest

from apps.experiments_app.constants import RunStatus
from apps.experiments_app.manifest import (
    ArtifactReference,
    ManifestError,
    create_manifest,
    finalize_manifest,
    make_run_id,
)

STARTED = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
GIT_SHA = "a" * 40
DATASET_SHA = "b" * 64


def test_run_id_is_deterministic_and_nonce_controlled():
    kwargs = {
        "started_at": STARTED,
        "git_sha": GIT_SHA,
        "dataset_id": "s11-functional-v1",
        "scenario_id": "EX-02",
        "seed": 20260921,
        "execution_nonce": "operator-approved-001",
    }
    assert make_run_id(**kwargs) == make_run_id(**kwargs)
    assert make_run_id(**kwargs) != make_run_id(**(kwargs | {"execution_nonce": "002"}))


def test_run_id_requires_explicit_nonce():
    with pytest.raises(ManifestError, match="nonce"):
        make_run_id(
            started_at=STARTED,
            git_sha=GIT_SHA,
            dataset_id="dataset",
            scenario_id="EX-02",
            seed=1,
            execution_nonce="",
        )


def test_manifest_records_git_dataset_seed_and_serializes_reproducibly(monkeypatch):
    monkeypatch.setattr("apps.experiments_app.manifest.git_head", lambda root: GIT_SHA)
    monkeypatch.setattr("apps.experiments_app.manifest.git_is_clean", lambda root: True)
    kwargs = {
        "repo_root": Path.cwd(),
        "started_at": STARTED,
        "environment": "isolated-experiment",
        "timezone": "America/Asuncion",
        "seed": 20260921,
        "dataset_id": "s11-functional-v1",
        "dataset_sha256": DATASET_SHA,
        "scenario_id": "EX-02",
        "experiment_id": "HT3-01",
        "execution_nonce": "001",
        "configuration": {"worker_concurrency": 2},
    }
    first = create_manifest(**kwargs)
    second = create_manifest(**kwargs)
    assert first.to_dict() == second.to_dict()
    assert first.sha256 == second.sha256
    assert first.git_sha == GIT_SHA
    assert first.dataset_sha256 == DATASET_SHA
    assert first.seed == 20260921


def test_manifest_accepts_host_verified_git_without_container_repository(monkeypatch):
    monkeypatch.setattr(
        "apps.experiments_app.manifest.git_head",
        lambda root: (_ for _ in ()).throw(AssertionError("no debe consultar Git")),
    )
    manifest = create_manifest(
        repo_root=Path("/repository-not-mounted"),
        started_at=STARTED,
        environment="isolated-experiment",
        timezone="America/Asuncion",
        seed=20260921,
        dataset_id="s11-functional-v1",
        dataset_sha256=DATASET_SHA,
        scenario_id="EX-02",
        experiment_id="EX-02",
        execution_nonce="host-verified",
        configuration={},
        expected_git_sha=GIT_SHA,
        supplied_git_sha=GIT_SHA,
        supplied_git_clean=True,
    )
    assert manifest.git_sha == GIT_SHA
    assert manifest.git_clean is True


def test_finalize_manifest_references_artifact_hashes():
    base = create_base_manifest()
    finished = finalize_manifest(
        base,
        finished_at=datetime(2026, 9, 21, 12, 1, tzinfo=UTC),
        status=RunStatus.COMPLETED,
        artifacts=[ArtifactReference("a", "raw/a.json", "c" * 64)],
    )
    assert finished.status == "COMPLETED"
    assert finished.artifact_references[0].sha256 == "c" * 64


def test_invalid_manifest_requires_reason():
    with pytest.raises(ManifestError, match="invalidity_reason"):
        finalize_manifest(
            create_base_manifest(),
            finished_at=STARTED,
            status=RunStatus.INVALID,
            artifacts=[],
        )


def create_base_manifest():
    from apps.experiments_app.manifest import RunManifest

    return RunManifest(
        schema_version="s11-run-manifest-v1",
        methodology_version="S11A-v1.0",
        run_id="s11-00000000-0000-5000-8000-000000000001",
        git_sha=GIT_SHA,
        git_clean=True,
        started_at="2026-09-21T12:00:00Z",
        finished_at=None,
        environment="isolated-experiment",
        timezone="America/Asuncion",
        seed=20260921,
        dataset_id="s11-functional-v1",
        dataset_sha256=DATASET_SHA,
        scenario_id="EX-02",
        experiment_id="HT3-01",
        status="PREPARED",
        configuration={},
        runtime={},
    )
