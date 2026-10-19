import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from apps.experiments_app.canonical import canonical_json_bytes, sha256_bytes
from apps.experiments_app.constants import METHODOLOGY_VERSION, ArtifactKind, Classification
from apps.experiments_app.custody_policy import (
    PUBLICATION_AUTHORITY,
    create_publication_approval,
    publication_public_key_from_private,
)
from apps.experiments_app.evidence import EvidencePolicyError, EvidenceStore

_CUSTODY_KEY = b"synthetic-custody-test-key-32-bytes"
_PUBLICATION_SIGNING_KEY = bytes(range(32))
_PUBLICATION_VERIFICATION_KEY = publication_public_key_from_private(_PUBLICATION_SIGNING_KEY)


def _store(root, **kwargs):
    return EvidenceStore(
        root,
        custody_hmac_key=_CUSTODY_KEY,
        publication_verification_key=_PUBLICATION_VERIFICATION_KEY,
        **kwargs,
    )


def _initial_payload(run_id, scenario_id):
    return {
        "artifact_references": [],
        "configuration": {},
        "dataset_id": "synthetic-test-v1",
        "dataset_sha256": "d" * 64,
        "environment": "isolated-experiment",
        "experiment_id": "P02-P03-TEST",
        "finished_at": None,
        "git_clean": True,
        "git_sha": "a" * 40,
        "invalidity_reason": None,
        "methodology_version": METHODOLOGY_VERSION,
        "run_id": run_id,
        "runtime": {"python": "test", "platform": "test"},
        "scenario_id": scenario_id,
        "schema_version": "s11-run-manifest-v1",
        "seed": 20260921,
        "started_at": "2026-09-21T12:00:00Z",
        "status": "PREPARED",
        "timezone": "America/Asuncion",
    }


def _final_payload(store, run_id, scenario_id, finished_at):
    return {
        **_initial_payload(run_id, scenario_id),
        "artifact_references": [
            {
                "artifact_id": record["artifact_id"],
                "relative_path": record["relative_path"],
                "sha256": record["sha256"],
            }
            for record in store.records(run_id)
        ],
        "finished_at": finished_at.isoformat().replace("+00:00", "Z"),
        "status": "COMPLETED",
    }


@pytest.fixture
def persistent_root(request):
    root = Path.cwd() / ".s11-test-artifacts" / request.node.name
    shutil.rmtree(root, ignore_errors=True)
    yield root
    shutil.rmtree(root, ignore_errors=True)


def test_rejects_tmp_as_authoritative_storage():
    with pytest.raises(EvidencePolicyError, match="/tmp"):
        EvidenceStore(Path("/tmp/s11"))


def test_requires_custody_hmac_key(persistent_root):
    with pytest.raises(EvidencePolicyError, match="clave HMAC de 32 bytes"):
        EvidenceStore(persistent_root)


def test_requires_publication_verification_key(persistent_root):
    with pytest.raises(EvidencePolicyError, match="clave pública Ed25519 de 32 bytes"):
        EvidenceStore(persistent_root, custody_hmac_key=_CUSTODY_KEY)


@pytest.mark.parametrize(
    "classification", [Classification.PUBLIC, Classification.INTERNAL, Classification.EXPERIMENTAL]
)
def test_persists_hashes_and_classification(persistent_root, classification):
    created_at = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    store = _store(persistent_root, clock=lambda: created_at)
    artifact_type = f"payload-{classification.value.lower()}"
    run_id = "s11-00000000-0000-5000-8000-000000000001"
    payload = {"value": 1}
    approval = None
    if classification is Classification.PUBLIC:
        content_sha256 = sha256_bytes(canonical_json_bytes(payload))
        approval = create_publication_approval(
            approval_id="S11-PUBLIC-TEST-001",
            artifact_id=f"S11-EX-02-{run_id}-{artifact_type}",
            content_sha256=content_sha256,
            approved_at=created_at,
            signing_key=_PUBLICATION_SIGNING_KEY,
        )
    record = store.persist_json(
        run_id=run_id,
        scenario_id="EX-02",
        artifact_type=artifact_type,
        kind=ArtifactKind.RAW,
        classification=classification,
        filename=f"{classification.value.lower()}.json",
        payload=payload,
        source="test",
        publication_approval=approval,
    )
    content = (persistent_root / "runs" / record.run_id / record.relative_path).read_bytes()
    assert record.sha256 == sha256_bytes(content)
    assert record.classification == classification.value
    if classification is Classification.PUBLIC:
        assert record.expires_at is None
    else:
        assert record.expires_at is not None
    assert record.retention_anchor == (
        "publication_approval" if classification is Classification.PUBLIC else "created_at"
    )
    assert record.publication_approval == (approval.to_dict() if approval else None)
    assert store.verify(record.run_id) == []
    assert (persistent_root / "runs" / record.run_id / "SHA256SUMS").is_file()


def test_redacts_before_persisting(persistent_root):
    store = _store(persistent_root)
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
    store = _store(persistent_root)
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
    with pytest.raises(EvidencePolicyError, match="SENSITIVE"):
        store.persist_bytes(classification=Classification.SENSITIVE, **common)


def test_public_requires_explicit_approval_from_monty(persistent_root):
    approved_at = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    store = _store(persistent_root)
    common = {
        "run_id": "s11-00000000-0000-5000-8000-000000000006",
        "scenario_id": "EX-02",
        "artifact_type": "public-result",
        "kind": ArtifactKind.DERIVED,
        "classification": Classification.PUBLIC,
        "filename": "public.json",
        "payload": {"aggregate": 1},
        "source": "test",
    }
    with pytest.raises(EvidencePolicyError, match="Monty Emmanuel Pohl Amarilla"):
        store.persist_json(**common)
    artifact_id = f"S11-EX-02-{common['run_id']}-{common['artifact_type']}"
    content_sha256 = sha256_bytes(canonical_json_bytes(common["payload"]))
    approval = create_publication_approval(
        approval_id="S11-PUBLIC-TEST-002",
        artifact_id=artifact_id,
        content_sha256=content_sha256,
        approved_at=approved_at,
        signing_key=_PUBLICATION_SIGNING_KEY,
    )
    tampered = type(approval)(
        **{
            **approval.to_dict(),
            "approved_by": "Guadalupe Galeano",
        }
    )
    with pytest.raises(EvidencePolicyError, match="autoridad aprobada"):
        store.persist_json(publication_approval=tampered, **common)

    approval_from_untrusted_private_key = create_publication_approval(
        approval_id="S11-PUBLIC-TEST-UNTRUSTED-KEY",
        artifact_id=artifact_id,
        content_sha256=content_sha256,
        approved_at=approved_at,
        signing_key=bytes(reversed(range(32))),
    )
    with pytest.raises(EvidencePolicyError, match="otra clave de autoridad"):
        store.persist_json(publication_approval=approval_from_untrusted_private_key, **common)

    record = store.persist_json(publication_approval=approval, **common)
    assert record.classification == "PUBLIC"
    assert record.expires_at is None
    assert record.publication_approval is not None
    assert record.publication_approval["approved_by"] == PUBLICATION_AUTHORITY
    assert record.publication_approval["content_sha256"] == record.sha256


def test_artifacts_are_immutable(persistent_root):
    store = _store(persistent_root)
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


def test_artifact_id_cannot_be_reindexed_under_a_different_filename(persistent_root):
    store = _store(persistent_root)
    common = {
        "run_id": "s11-00000000-0000-5000-8000-000000000016",
        "scenario_id": "EX-05",
        "artifact_type": "unique-artifact",
        "kind": ArtifactKind.RAW,
        "classification": Classification.EXPERIMENTAL,
        "content": b"synthetic",
        "mime_type": "application/octet-stream",
        "source": "test",
    }
    store.persist_bytes(filename="first.bin", **common)

    with pytest.raises(EvidencePolicyError, match="artifact_id"):
        store.persist_bytes(filename="second.bin", **common)

    run_root = persistent_root / "runs" / common["run_id"]
    assert not (run_root / "raw" / "EX-05" / "second.bin").exists()
    assert len(store.records(common["run_id"])) == 1


def test_verification_retention_reanchors_synthetic_canary(persistent_root):
    created_at = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    finished_at = created_at + timedelta(hours=2)
    store = _store(persistent_root, clock=lambda: created_at)
    run_id = "s11-00000000-0000-5000-8000-000000000007"
    store.persist_json(
        run_id=run_id,
        scenario_id="P02-P03",
        artifact_type="internal-before-final",
        kind=ArtifactKind.METADATA,
        classification=Classification.INTERNAL,
        filename="internal.json",
        payload={"synthetic": True},
        source="test",
    )
    store.persist_json(
        run_id=run_id,
        scenario_id="P02-P03",
        artifact_type="experimental-before-final",
        kind=ArtifactKind.RAW,
        classification=Classification.EXPERIMENTAL,
        filename="experimental.json",
        payload={"synthetic": True},
        source="test",
    )

    records = store.finalize_verification_retention(run_id, finished_at=finished_at)

    by_class = {record["classification"]: record for record in records}
    assert by_class["INTERNAL"]["expires_at"] == "2026-12-20T14:00:00Z"
    assert by_class["EXPERIMENTAL"]["expires_at"] == "2027-09-21T14:00:00Z"
    assert all(record["retention_anchor"] == "verification_finished_at" for record in records)
    assert all(record["retention_started_at"] == "2026-09-21T14:00:00Z" for record in records)
    assert store.verify(run_id) == []


def test_finalize_retention_refuses_to_resign_a_tampered_inventory(persistent_root):
    created_at = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    store = _store(persistent_root, clock=lambda: created_at)
    run_id = "s11-00000000-0000-5000-8000-000000000010"
    store.persist_json(
        run_id=run_id,
        scenario_id="P02-P03",
        artifact_type="internal-before-final",
        kind=ArtifactKind.METADATA,
        classification=Classification.INTERNAL,
        filename="internal.json",
        payload={"synthetic": True},
        source="test",
    )
    index = persistent_root / "runs" / run_id / "metadata" / "artifacts.jsonl"
    record = json.loads(index.read_text(encoding="utf-8"))
    record["classification"] = Classification.EXPERIMENTAL.value
    index.write_bytes(canonical_json_bytes(record))
    EvidenceStore(
        persistent_root,
        custody_hmac_key=b"attacker-does-not-know-custody-key!",
        publication_verification_key=_PUBLICATION_VERIFICATION_KEY,
    ).write_checksums(run_id)

    with pytest.raises(EvidencePolicyError, match="no es íntegro"):
        store.finalize_verification_retention(run_id, finished_at=created_at + timedelta(hours=2))


def test_finalize_with_manifest_rolls_back_retention_if_manifest_write_fails(
    persistent_root, monkeypatch
):
    created_at = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    finished_at = created_at + timedelta(hours=2)
    store = _store(persistent_root, clock=lambda: created_at)
    run_id = "s11-00000000-0000-5000-8000-000000000008"
    store.persist_json(
        run_id=run_id,
        scenario_id="EX-02",
        artifact_type="run-manifest-initial",
        kind=ArtifactKind.METADATA,
        classification=Classification.INTERNAL,
        filename="initial.json",
        payload=_initial_payload(run_id, "EX-02"),
        source="test",
    )
    original_records = store.records(run_id)
    original_checksums = (persistent_root / "runs" / run_id / "SHA256SUMS").read_bytes()

    def fail_manifest_write(**kwargs):
        raise OSError("synthetic write failure")

    monkeypatch.setattr(store, "persist_json", fail_manifest_write)
    with pytest.raises(OSError, match="synthetic write failure"):
        store.finalize_with_json_artifact(
            finished_at=finished_at,
            run_id=run_id,
            scenario_id="EX-02",
            artifact_type="run-manifest-final",
            kind=ArtifactKind.METADATA,
            classification=Classification.INTERNAL,
            filename="run-manifest.final.json",
            payload=_final_payload(store, run_id, "EX-02", finished_at),
            source="test",
        )

    assert store.records(run_id) == original_records
    assert (persistent_root / "runs" / run_id / "SHA256SUMS").read_bytes() == original_checksums


def test_finalize_journal_recovers_after_process_interruption(persistent_root, monkeypatch):
    created_at = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    finished_at = created_at + timedelta(hours=2)
    store = _store(persistent_root, clock=lambda: created_at)
    run_id = "s11-00000000-0000-5000-8000-000000000009"
    store.persist_json(
        run_id=run_id,
        scenario_id="EX-02",
        artifact_type="run-manifest-initial",
        kind=ArtifactKind.METADATA,
        classification=Classification.INTERNAL,
        filename="initial.json",
        payload=_initial_payload(run_id, "EX-02"),
        source="test",
    )
    original_records = store.records(run_id)

    def interrupt_before_commit(*args, **kwargs):
        raise SystemExit("synthetic process interruption")

    monkeypatch.setattr(store, "_commit_finalization_journal", interrupt_before_commit)
    with pytest.raises(SystemExit, match="synthetic process interruption"):
        store.finalize_with_json_artifact(
            finished_at=finished_at,
            run_id=run_id,
            scenario_id="EX-02",
            artifact_type="run-manifest-final",
            kind=ArtifactKind.METADATA,
            classification=Classification.INTERNAL,
            filename="run-manifest.final.json",
            payload=_final_payload(store, run_id, "EX-02", finished_at),
            source="test",
        )

    recovered_store = _store(persistent_root, clock=lambda: created_at)
    assert recovered_store.recover_finalization(run_id) == "PREPARED"
    assert recovered_store.records(run_id) == original_records
    assert not (
        persistent_root / "runs" / run_id / "metadata" / "EX-02" / "run-manifest.final.json"
    ).exists()
    assert recovered_store.verify(run_id) == []


def test_finalize_rejects_tampered_recovery_journal_before_mutating_run(
    persistent_root, monkeypatch
):
    created_at = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    store = _store(persistent_root, clock=lambda: created_at)
    run_id = "s11-00000000-0000-5000-8000-000000000012"
    initial = store.persist_json(
        run_id=run_id,
        scenario_id="EX-02",
        artifact_type="run-manifest-initial",
        kind=ArtifactKind.METADATA,
        classification=Classification.INTERNAL,
        filename="initial.json",
        payload=_initial_payload(run_id, "EX-02"),
        source="test",
    )

    def interrupt_before_commit(*args, **kwargs):
        raise SystemExit("synthetic process interruption")

    monkeypatch.setattr(store, "_commit_finalization_journal", interrupt_before_commit)
    with pytest.raises(SystemExit, match="synthetic process interruption"):
        store.finalize_with_json_artifact(
            finished_at=created_at + timedelta(hours=2),
            run_id=run_id,
            scenario_id="EX-02",
            artifact_type="run-manifest-final",
            kind=ArtifactKind.METADATA,
            classification=Classification.INTERNAL,
            filename="run-manifest.final.json",
            payload=_final_payload(store, run_id, "EX-02", created_at + timedelta(hours=2)),
            source="test",
        )

    journal = persistent_root / "finalization-transactions" / f"{run_id}.json"
    envelope = json.loads(journal.read_text(encoding="utf-8"))
    envelope["payload"]["target_relative_path"] = initial.relative_path
    journal.write_bytes(canonical_json_bytes(envelope))
    initial_path = persistent_root / "runs" / run_id / initial.relative_path

    with pytest.raises(EvidencePolicyError, match="no es recuperable"):
        _store(persistent_root).recover_finalization(run_id)

    assert initial_path.is_file()


def test_finalize_holds_mutation_lock_through_journal_commit(persistent_root, monkeypatch):
    created_at = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    store = _store(persistent_root, clock=lambda: created_at)
    run_id = "s11-00000000-0000-5000-8000-000000000011"
    store.persist_json(
        run_id=run_id,
        scenario_id="EX-02",
        artifact_type="run-manifest-initial",
        kind=ArtifactKind.METADATA,
        classification=Classification.INTERNAL,
        filename="initial.json",
        payload=_initial_payload(run_id, "EX-02"),
        source="test",
    )
    original_commit = store._commit_finalization_journal
    lock_observed = False

    def assert_lock_then_commit(path, payload):
        nonlocal lock_observed
        lock_observed = store._mutation_depth == 1 and store._mutation_lock_file is not None
        return original_commit(path, payload)

    monkeypatch.setattr(store, "_commit_finalization_journal", assert_lock_then_commit)
    store.finalize_with_json_artifact(
        finished_at=created_at + timedelta(hours=1),
        run_id=run_id,
        scenario_id="EX-02",
        artifact_type="run-manifest-final",
        kind=ArtifactKind.METADATA,
        classification=Classification.INTERNAL,
        filename="run-manifest.final.json",
        payload=_final_payload(store, run_id, "EX-02", created_at + timedelta(hours=1)),
        source="test",
    )

    assert lock_observed
    assert store.verify(run_id) == []


def test_finalized_run_rejects_new_artifacts_and_log_appends(persistent_root):
    created_at = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    store = _store(persistent_root, clock=lambda: created_at)
    run_id = "s11-00000000-0000-5000-8000-000000000013"
    store.persist_json(
        run_id=run_id,
        scenario_id="EX-02",
        artifact_type="run-manifest-initial",
        kind=ArtifactKind.METADATA,
        classification=Classification.INTERNAL,
        filename="initial.json",
        payload=_initial_payload(run_id, "EX-02"),
        source="test",
    )
    store.finalize_with_json_artifact(
        finished_at=created_at + timedelta(hours=1),
        run_id=run_id,
        scenario_id="EX-02",
        artifact_type="run-manifest-final",
        kind=ArtifactKind.METADATA,
        classification=Classification.INTERNAL,
        filename="run-manifest.final.json",
        payload=_final_payload(store, run_id, "EX-02", created_at + timedelta(hours=1)),
        source="test",
    )

    with pytest.raises(EvidencePolicyError, match="finalizado"):
        store.persist_json(
            run_id=run_id,
            scenario_id="EX-02",
            artifact_type="late-artifact",
            kind=ArtifactKind.DERIVED,
            classification=Classification.INTERNAL,
            filename="late.json",
            payload={"late": True},
            source="test",
        )
    with pytest.raises(EvidencePolicyError, match="finalizado"):
        store.append_jsonl(
            run_id=run_id,
            scenario_id="EX-02",
            stream="http",
            payload={"event": "late"},
        )

    assert store.verify(run_id) == []

    records = store.records(run_id)
    assert all(record["retention_anchor"] == "finished_at" for record in records)
    assert all(record["retention_started_at"] == "2026-09-21T13:00:00Z" for record in records)


def test_finalization_requires_exact_manifest_contract(persistent_root):
    created_at = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    store = _store(persistent_root, clock=lambda: created_at)
    run_id = "s11-00000000-0000-5000-8000-000000000014"
    store.persist_json(
        run_id=run_id,
        scenario_id="EX-02",
        artifact_type="run-manifest-initial",
        kind=ArtifactKind.METADATA,
        classification=Classification.INTERNAL,
        filename="initial.json",
        payload=_initial_payload(run_id, "EX-02"),
        source="test",
    )

    with pytest.raises(EvidencePolicyError, match="contrato exacto"):
        store.finalize_with_json_artifact(
            finished_at=created_at + timedelta(hours=1),
            run_id=run_id,
            scenario_id="EX-02",
            artifact_type="not-a-final-manifest",
            kind=ArtifactKind.METADATA,
            classification=Classification.INTERNAL,
            filename="run-manifest.final.json",
            payload={"finished_at": "2026-09-21T13:00:00Z"},
            source="test",
        )
    mismatched_payload = _final_payload(
        store,
        run_id,
        "EX-02",
        created_at + timedelta(hours=1),
    )
    mismatched_payload["finished_at"] = "2026-09-21T14:00:00Z"
    with pytest.raises(EvidencePolicyError, match="no coincide"):
        store.finalize_with_json_artifact(
            finished_at=created_at + timedelta(hours=1),
            run_id=run_id,
            scenario_id="EX-02",
            artifact_type="run-manifest-final",
            kind=ArtifactKind.METADATA,
            classification=Classification.INTERNAL,
            filename="run-manifest.final.json",
            payload=mismatched_payload,
            source="test",
        )


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("git_sha", 123),
        ("dataset_sha256", "bad"),
        ("started_at", "not-a-date"),
        ("configuration", "not-a-dict"),
        ("runtime", []),
        ("seed", "bad"),
        ("timezone", None),
    ],
)
def test_finalization_rejects_runtime_invalid_manifest_fields(
    persistent_root, field, invalid_value
):
    created_at = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    store = _store(persistent_root, clock=lambda: created_at)
    run_id = "s11-00000000-0000-5000-8000-000000000017"
    store.persist_json(
        run_id=run_id,
        scenario_id="EX-02",
        artifact_type="run-manifest-initial",
        kind=ArtifactKind.METADATA,
        classification=Classification.INTERNAL,
        filename="initial.json",
        payload=_initial_payload(run_id, "EX-02"),
        source="test",
    )
    payload = _final_payload(store, run_id, "EX-02", created_at + timedelta(hours=1))
    payload[field] = invalid_value

    with pytest.raises(EvidencePolicyError, match="schema RunManifest"):
        store.finalize_with_json_artifact(
            finished_at=created_at + timedelta(hours=1),
            run_id=run_id,
            scenario_id="EX-02",
            artifact_type="run-manifest-final",
            kind=ArtifactKind.METADATA,
            classification=Classification.INTERNAL,
            filename="run-manifest.final.json",
            payload=payload,
            source="test",
        )


def test_finalization_preserves_initial_manifest_provenance(persistent_root):
    created_at = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    store = _store(persistent_root, clock=lambda: created_at)
    run_id = "s11-00000000-0000-5000-8000-000000000018"
    store.persist_json(
        run_id=run_id,
        scenario_id="EX-02",
        artifact_type="run-manifest-initial",
        kind=ArtifactKind.METADATA,
        classification=Classification.INTERNAL,
        filename="initial.json",
        payload=_initial_payload(run_id, "EX-02"),
        source="test",
    )
    payload = _final_payload(store, run_id, "EX-02", created_at + timedelta(hours=1))
    payload["environment"] = "another-isolated-environment"

    with pytest.raises(EvidencePolicyError, match="altera procedencia"):
        store.finalize_with_json_artifact(
            finished_at=created_at + timedelta(hours=1),
            run_id=run_id,
            scenario_id="EX-02",
            artifact_type="run-manifest-final",
            kind=ArtifactKind.METADATA,
            classification=Classification.INTERNAL,
            filename="run-manifest.final.json",
            payload=payload,
            source="test",
        )


def test_removing_authenticated_seal_does_not_reopen_finalized_run(persistent_root):
    created_at = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    store = _store(persistent_root, clock=lambda: created_at)
    run_id = "s11-00000000-0000-5000-8000-000000000015"
    store.persist_json(
        run_id=run_id,
        scenario_id="EX-02",
        artifact_type="run-manifest-initial",
        kind=ArtifactKind.METADATA,
        classification=Classification.INTERNAL,
        filename="initial.json",
        payload=_initial_payload(run_id, "EX-02"),
        source="test",
    )
    store.finalize_with_json_artifact(
        finished_at=created_at + timedelta(hours=1),
        run_id=run_id,
        scenario_id="EX-02",
        artifact_type="run-manifest-final",
        kind=ArtifactKind.METADATA,
        classification=Classification.INTERNAL,
        filename="run-manifest.final.json",
        payload=_final_payload(store, run_id, "EX-02", created_at + timedelta(hours=1)),
        source="test",
    )
    (persistent_root / "runs" / run_id / ".s11-finalized").unlink()

    with pytest.raises(EvidencePolicyError, match="sello autenticado"):
        store.persist_json(
            run_id=run_id,
            scenario_id="EX-02",
            artifact_type="late-artifact",
            kind=ArtifactKind.DERIVED,
            classification=Classification.INTERNAL,
            filename="late.json",
            payload={"late": True},
            source="test",
        )

    assert not (
        persistent_root / "runs" / run_id / ArtifactKind.DERIVED.value / "EX-02" / "late.json"
    ).exists()


def test_existing_technical_log_is_indexed_with_complete_metadata(persistent_root):
    run_id = "s11-00000000-0000-5000-8000-000000000005"
    store = _store(persistent_root)
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
