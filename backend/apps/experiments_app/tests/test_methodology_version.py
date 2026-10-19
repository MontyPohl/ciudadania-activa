"""Compatibilidad explícita entre `S11A-v1.0` (histórica) y `S11A-v1.1` (vigente).

La enmienda del 26/09/2026 exige que todo run nuevo registre `S11A-v1.1` y que los
runs ya ejecutados sigan validando con la `S11A-v1.0` que registraron. Las pruebas
sobre runs reales trabajan con copias temporales y nunca escriben en `var/`.
"""

import json
import shutil
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from django.conf import settings

from apps.experiments_app.constants import (
    HISTORICAL_METHODOLOGY_RUN_IDS,
    HISTORICAL_METHODOLOGY_VERSION,
    METHODOLOGY_VERSION,
)
from apps.experiments_app.manifest import (
    ArtifactReference,
    ManifestError,
    RunManifest,
    create_manifest,
    methodology_version_error,
    validate_run_manifest,
)

HISTORICAL_EX02_RUN = "s11-4df882ed-9dfe-59a2-888b-ca1efc98d6af"
NEW_RUN = "s11-00000000-0000-5000-8000-000000000009"


def _manifest(**overrides) -> RunManifest:
    base = RunManifest(
        schema_version="s11-run-manifest-v1",
        methodology_version=METHODOLOGY_VERSION,
        run_id=NEW_RUN,
        git_sha="a" * 40,
        git_clean=True,
        started_at="2026-09-27T12:00:00Z",
        finished_at=None,
        environment="s11c3-ex09-openai-ephemeral-container",
        timezone="America/Asuncion",
        seed=20260921,
        dataset_id="s8-evaluation-v1",
        dataset_sha256="b" * 64,
        scenario_id="EX-09",
        experiment_id="S11C3-EX09-OPENAI",
        status="PREPARED",
        configuration={},
        runtime={},
    )
    return replace(base, **overrides)


def test_versions_are_the_amended_and_the_historical_one():
    assert METHODOLOGY_VERSION == "S11A-v1.1"
    assert HISTORICAL_METHODOLOGY_VERSION == "S11A-v1.0"
    assert len(HISTORICAL_METHODOLOGY_RUN_IDS) == 16
    assert isinstance(HISTORICAL_METHODOLOGY_RUN_IDS, frozenset)


def test_a_new_run_records_the_current_version(monkeypatch, tmp_path):
    manifest = create_manifest(
        repo_root=tmp_path,
        started_at=datetime(2026, 9, 27, 12, 0, tzinfo=UTC),
        environment="s11c3-ex09-openai-ephemeral-container",
        timezone="America/Asuncion",
        seed=20260921,
        dataset_id="s8-evaluation-v1",
        dataset_sha256="b" * 64,
        scenario_id="EX-09",
        experiment_id="S11C3-EX09-OPENAI",
        execution_nonce="test-only",
        configuration={},
        supplied_git_sha="a" * 40,
        supplied_git_clean=True,
    )
    assert manifest.methodology_version == "S11A-v1.1"
    validate_run_manifest(manifest)


def test_new_ex09_run_with_current_version_is_valid():
    validate_run_manifest(_manifest())


def test_historical_run_with_historical_version_is_valid():
    validate_run_manifest(
        _manifest(
            methodology_version="S11A-v1.0",
            run_id=HISTORICAL_EX02_RUN,
            scenario_id="EX-02",
            started_at="2026-09-22T10:56:25.508025Z",
        )
    )


def test_new_ex09_run_with_historical_version_is_rejected():
    with pytest.raises(ManifestError, match="sólo es válida para los runs históricos"):
        validate_run_manifest(_manifest(methodology_version="S11A-v1.0"))


def test_no_historical_run_can_be_an_ex09_run():
    # EX-09 nunca se ejecutó bajo S11A-v1.0: ninguna combinación nueva lo admite.
    for run_id in ("s11-00000000-0000-5000-8000-0000000000aa", NEW_RUN):
        assert methodology_version_error("S11A-v1.0", run_id) is not None


@pytest.mark.parametrize(
    "version",
    ["S11A-v1.2", "S11A-v1", "S11A-v2.0", "s11a-v1.1", "S11A-v1.1 ", "S11A-v1.0-rc", "unknown"],
)
def test_unknown_or_look_alike_versions_are_rejected(version):
    assert methodology_version_error(version, NEW_RUN) is not None
    assert methodology_version_error(version, HISTORICAL_EX02_RUN) is not None
    with pytest.raises(ManifestError):
        validate_run_manifest(_manifest(methodology_version=version))


def _historical_manifest_files() -> list[Path]:
    root = Path(settings.REPO_ROOT) / "var" / "s11-runs" / "runs"
    if not root.is_dir():
        return []
    return sorted(root.glob("*/**/run-manifest*.json"))


@pytest.mark.skipif(
    not _historical_manifest_files(),
    reason="Sin evidencia S11 local: la comprobación sobre runs reales sólo corre en el host.",
)
def test_real_historical_manifests_still_validate_from_temporary_copies(tmp_path):
    files = _historical_manifest_files()
    before = {path: path.read_bytes() for path in files}
    run_ids = set()
    for index, original in enumerate(files):
        copy = tmp_path / f"{index:03d}-{original.name}"
        shutil.copyfile(original, copy)
        payload = json.loads(copy.read_text(encoding="utf-8"))
        payload["artifact_references"] = tuple(
            ArtifactReference(**reference) for reference in payload["artifact_references"]
        )
        manifest = RunManifest(**payload)
        assert manifest.methodology_version == "S11A-v1.0"
        assert manifest.scenario_id != "EX-09"
        validate_run_manifest(manifest)
        run_ids.add(manifest.run_id)
    # El registro enumera exactamente los runs custodiados, ni uno más ni uno menos.
    assert run_ids == HISTORICAL_METHODOLOGY_RUN_IDS
    assert {path: path.read_bytes() for path in files} == before
