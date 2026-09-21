import json
import shutil
from pathlib import Path

import pytest

from apps.experiments_app.canonical import sha256_bytes
from apps.experiments_app.constants import ArtifactKind, Classification
from apps.experiments_app.evidence import EvidencePolicyError, EvidenceStore


@pytest.fixture
def persistent_root(request):
    root = Path.cwd() / ".s11-test-artifacts" / request.node.name
    shutil.rmtree(root, ignore_errors=True)
    yield root
    shutil.rmtree(root, ignore_errors=True)


def test_rejects_tmp_as_authoritative_storage():
    with pytest.raises(EvidencePolicyError, match="/tmp"):
        EvidenceStore(Path("/tmp/s11"))


@pytest.mark.parametrize(
    "classification", [Classification.PUBLIC, Classification.INTERNAL, Classification.EXPERIMENTAL]
)
def test_persists_hashes_and_classification(persistent_root, classification):
    store = EvidenceStore(persistent_root)
    record = store.persist_json(
        run_id="s11-00000000-0000-5000-8000-000000000001",
        scenario_id="EX-02",
        artifact_type=f"payload-{classification.value.lower()}",
        kind=ArtifactKind.RAW,
        classification=classification,
        filename=f"{classification.value.lower()}.json",
        payload={"value": 1},
        source="test",
    )
    content = (persistent_root / "runs" / record.run_id / record.relative_path).read_bytes()
    assert record.sha256 == sha256_bytes(content)
    assert record.classification == classification.value
    assert store.verify(record.run_id) == []
    assert (persistent_root / "runs" / record.run_id / "SHA256SUMS").is_file()


def test_redacts_before_persisting(persistent_root):
    store = EvidenceStore(persistent_root)
    record = store.persist_json(
        run_id="s11-00000000-0000-5000-8000-000000000002",
        scenario_id="EX-03",
        artifact_type="redacted-log",
        kind=ArtifactKind.LOG,
        classification=Classification.INTERNAL,
        filename="redacted.json",
        payload={"Authorization": "Bearer abc.def.ghi", "email": "persona@example.com"},
        source="test",
    )
    path = persistent_root / "runs" / record.run_id / record.relative_path
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["Authorization"] == "[REDACTED]"
    assert payload["email"] == "[REDACTED:EMAIL]"
    assert "abc.def.ghi" not in path.read_text(encoding="utf-8")


def test_rejects_secret_and_unapproved_sensitive(persistent_root):
    store = EvidenceStore(persistent_root)
    common = {
        "run_id": "s11-00000000-0000-5000-8000-000000000003",
        "scenario_id": "EX-04",
        "artifact_type": "blocked",
        "kind": ArtifactKind.RAW,
        "filename": "blocked.bin",
        "content": b"content",
        "mime_type": "application/octet-stream",
        "source": "test",
    }
    with pytest.raises(EvidencePolicyError, match="SECRET"):
        store.persist_bytes(classification=Classification.SECRET, **common)
    with pytest.raises(EvidencePolicyError, match="P02/P03"):
        store.persist_bytes(classification=Classification.SENSITIVE, **common)


def test_artifacts_are_immutable(persistent_root):
    store = EvidenceStore(persistent_root)
    kwargs = {
        "run_id": "s11-00000000-0000-5000-8000-000000000004",
        "scenario_id": "EX-05",
        "artifact_type": "immutable",
        "kind": ArtifactKind.RAW,
        "classification": Classification.EXPERIMENTAL,
        "filename": "immutable.bin",
        "content": b"first",
        "mime_type": "application/octet-stream",
        "source": "test",
    }
    store.persist_bytes(**kwargs)
    with pytest.raises(EvidencePolicyError, match="inmutable"):
        store.persist_bytes(**kwargs)


def test_existing_technical_log_is_indexed_with_complete_metadata(persistent_root):
    run_id = "s11-00000000-0000-5000-8000-000000000005"
    store = EvidenceStore(persistent_root)
    path = store.append_jsonl(
        run_id=run_id,
        scenario_id="EX-10",
        stream="http",
        payload={"event": "http.finished", "run_id": run_id, "scenario_id": "EX-10"},
    )
    record = store.register_existing(
        run_id=run_id,
        scenario_id="EX-10",
        artifact_type="http-events",
        kind=ArtifactKind.LOG,
        classification=Classification.INTERNAL,
        filename="http.jsonl",
        source="s11-instrumentation",
        transformation="automatic-redaction+append-only-jsonl",
    )

    assert record.relative_path == "logs/EX-10/http.jsonl"
    assert record.sha256 == sha256_bytes(path.read_bytes())
    assert record.created_at.endswith("Z")
    assert record.artifact_type == "http-events"
    assert record.classification == "INTERNAL"
    assert store.verify(run_id) == []
    with pytest.raises(EvidencePolicyError, match="inmutable"):
        store.register_existing(
            run_id=run_id,
            scenario_id="EX-10",
            artifact_type="http-events",
            kind=ArtifactKind.LOG,
            classification=Classification.INTERNAL,
            filename="http.jsonl",
            source="s11-instrumentation",
        )
