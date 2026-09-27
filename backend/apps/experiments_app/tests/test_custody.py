import json
import shutil
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import apps.experiments_app.custody as custody_module
from apps.experiments_app.canonical import canonical_json_bytes, sha256_bytes
from apps.experiments_app.constants import METHODOLOGY_VERSION, ArtifactKind, Classification
from apps.experiments_app.custody import (
    ATTESTED_SOURCE_PATHS,
    EXPECTED_BASELINE_SHA,
    CustodyError,
    CustodyManager,
    build_commit_source_snapshot,
    complete_custody_verification,
    prepare_custody_verification,
)
from apps.experiments_app.custody_policy import (
    ACADEMIC_RESPONSIBLE,
    COAUTHOR_RESEARCHER,
    PRIMARY_CUSTODIAN,
    PUBLICATION_AUTHORITY,
    AccessOperation,
    create_publication_approval,
    evaluate_access,
    expiration_for,
    publication_public_key_from_private,
)
from apps.experiments_app.evidence import EvidenceStore

_CUSTODY_KEY = b"synthetic-custody-test-key-32-bytes"
_PUBLICATION_SIGNING_KEY = bytes(range(32))
_PUBLICATION_VERIFICATION_KEY = publication_public_key_from_private(_PUBLICATION_SIGNING_KEY)


def _source_snapshot():
    files = {path: sha256_bytes(path.encode()) for path in ATTESTED_SOURCE_PATHS}
    return {
        "aggregate_sha256": sha256_bytes(canonical_json_bytes(files)),
        "baseline_sha": EXPECTED_BASELINE_SHA,
        "files": files,
    }


def _manager(primary, backup, restore):
    return CustodyManager(
        primary,
        backup,
        restore,
        custody_hmac_key=_CUSTODY_KEY,
        publication_verification_key=_PUBLICATION_VERIFICATION_KEY,
    )


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
def custody_roots(request):
    base = Path.cwd() / ".s11-test-artifacts" / request.node.name
    shutil.rmtree(base, ignore_errors=True)
    roots = (base / "primary", base / "backup", base / "restore")
    yield roots
    shutil.rmtree(base, ignore_errors=True)


def test_retention_periods_are_exact_and_public_is_permanent_only_after_approval():
    created_at = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    assert expiration_for(Classification.INTERNAL, created_at) == created_at + timedelta(days=90)
    assert expiration_for(Classification.EXPERIMENTAL, created_at) == created_at + timedelta(
        days=365
    )
    assert expiration_for(Classification.PUBLIC, created_at) is None


def test_nominal_access_matrix_is_deny_by_default_and_delete_is_procedural():
    assert evaluate_access(
        PRIMARY_CUSTODIAN, Classification.INTERNAL, AccessOperation.WRITE
    ).allowed
    assert evaluate_access(
        COAUTHOR_RESEARCHER, Classification.EXPERIMENTAL, AccessOperation.EXECUTE
    ).allowed
    assert not evaluate_access(
        COAUTHOR_RESEARCHER, Classification.PUBLIC, AccessOperation.PUBLISH
    ).allowed
    assert not evaluate_access(
        ACADEMIC_RESPONSIBLE, Classification.INTERNAL, AccessOperation.READ
    ).allowed
    assert not evaluate_access(
        PRIMARY_CUSTODIAN, Classification.INTERNAL, AccessOperation.DELETE
    ).allowed
    assert evaluate_access(
        PRIMARY_CUSTODIAN,
        Classification.INTERNAL,
        AccessOperation.DELETE,
        destruction_authorized=True,
    ).allowed
    assert evaluate_access(PRIMARY_CUSTODIAN, Classification.PUBLIC, AccessOperation.BACKUP).allowed
    assert not evaluate_access(
        PRIMARY_CUSTODIAN, Classification.SENSITIVE, AccessOperation.READ
    ).allowed
    assert not evaluate_access(
        "UNAUTHORIZED", Classification.INTERNAL, AccessOperation.READ
    ).allowed


def test_backup_and_restore_preserve_metadata_expiration_and_hashes(custody_roots):
    primary, backup, restore = custody_roots
    created_at = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    manager = _manager(primary, backup, restore)
    manager.initialize_roots()
    store = _store(primary, clock=lambda: created_at)
    record = store.persist_json(
        run_id="custody-backup-canary",
        scenario_id="P02-P03",
        artifact_type="internal-canary",
        kind=ArtifactKind.METADATA,
        classification=Classification.INTERNAL,
        filename="canary.json",
        payload={"synthetic": True},
        source="test",
    )

    backup_result = manager.backup_run(record.run_id, identity=PRIMARY_CUSTODIAN)
    restore_result = manager.restore_run(record.run_id, identity=PRIMARY_CUSTODIAN)

    assert backup_result.sha256_match
    assert restore_result.sha256_match
    for root in (primary, backup, restore):
        index = root / "runs" / record.run_id / "metadata" / "artifacts.jsonl"
        metadata = json.loads(index.read_text(encoding="utf-8"))
        assert metadata["classification"] == "INTERNAL"
        assert metadata["run_id"] == record.run_id
        assert metadata["expires_at"] == "2026-12-20T12:00:00Z"


def test_backup_and_restore_hold_the_primary_mutation_lock(custody_roots, monkeypatch):
    primary, backup, restore = custody_roots
    manager = _manager(primary, backup, restore)
    manager.initialize_roots()
    store = _store(primary)
    record = store.persist_json(
        run_id="custody-copy-lock",
        scenario_id="P02-P03",
        artifact_type="internal-canary",
        kind=ArtifactKind.METADATA,
        classification=Classification.INTERNAL,
        filename="canary.json",
        payload={"synthetic": True},
        source="test",
    )
    original_mutation = EvidenceStore.mutation
    original_copy = manager._copy_and_verify
    mutation_active = False
    copies_under_lock = 0

    @contextmanager
    def observed_mutation(evidence_store):
        nonlocal mutation_active
        with original_mutation(evidence_store):
            mutation_active = True
            try:
                yield
            finally:
                mutation_active = False

    def observed_copy(*args, **kwargs):
        nonlocal copies_under_lock
        if mutation_active:
            copies_under_lock += 1
        return original_copy(*args, **kwargs)

    monkeypatch.setattr(EvidenceStore, "mutation", observed_mutation)
    monkeypatch.setattr(manager, "_copy_and_verify", observed_copy)

    manager.backup_run(record.run_id, identity=PRIMARY_CUSTODIAN)
    manager.restore_run(record.run_id, identity=PRIMARY_CUSTODIAN)

    assert copies_under_lock == 2


def test_destruction_requires_expiration_and_removes_all_copies(custody_roots):
    primary, backup, restore = custody_roots
    created_at = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    manager = _manager(primary, backup, restore)
    manager.initialize_roots()
    store = _store(primary, clock=lambda: created_at)
    record = store.persist_json(
        run_id="custody-destruction-canary",
        scenario_id="P02-P03",
        artifact_type="internal-canary",
        kind=ArtifactKind.RAW,
        classification=Classification.INTERNAL,
        filename="canary.json",
        payload={"synthetic": True},
        source="test",
    )
    store.finalize_verification_retention(record.run_id, finished_at=created_at)
    manager.backup_run(record.run_id, identity=PRIMARY_CUSTODIAN)
    manager.restore_run(record.run_id, identity=PRIMARY_CUSTODIAN)

    with pytest.raises(CustodyError, match="Ningún artefacto"):
        manager.destroy_expired_run(
            record.run_id,
            verification_id="custody-destruction-check",
            identity=PRIMARY_CUSTODIAN,
            now=created_at,
        )

    receipt = manager.destroy_expired_run(
        record.run_id,
        verification_id="custody-destruction-check",
        identity=PRIMARY_CUSTODIAN,
        now=created_at + timedelta(days=90, seconds=1),
    )
    assert receipt.result == "DESTROYED"
    assert not receipt.payload_retained
    assert Path(receipt.intent_path).is_file()
    assert Path(receipt.receipt_path).is_file()
    assert all(not (root / "runs" / record.run_id).exists() for root in custody_roots)


def test_destruction_holds_the_primary_mutation_lock_for_the_whole_delete(
    custody_roots, monkeypatch
):
    primary, backup, restore = custody_roots
    created_at = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    manager = _manager(primary, backup, restore)
    manager.initialize_roots()
    store = _store(primary, clock=lambda: created_at)
    record = store.persist_json(
        run_id="custody-destruction-lock",
        scenario_id="P02-P03",
        artifact_type="internal-canary",
        kind=ArtifactKind.RAW,
        classification=Classification.INTERNAL,
        filename="canary.json",
        payload={"synthetic": True},
        source="test",
    )
    store.finalize_verification_retention(record.run_id, finished_at=created_at)
    manager.backup_run(record.run_id, identity=PRIMARY_CUSTODIAN)
    manager.restore_run(record.run_id, identity=PRIMARY_CUSTODIAN)
    original_mutation = EvidenceStore.mutation
    original_destroy = custody_module._destroy_records_in_run
    mutation_active = False
    delete_observed_under_lock = False

    @contextmanager
    def observed_mutation(evidence_store):
        nonlocal mutation_active
        with original_mutation(evidence_store):
            mutation_active = True
            try:
                yield
            finally:
                mutation_active = False

    def observed_destroy(*args, **kwargs):
        nonlocal delete_observed_under_lock
        delete_observed_under_lock = delete_observed_under_lock or mutation_active
        return original_destroy(*args, **kwargs)

    monkeypatch.setattr(EvidenceStore, "mutation", observed_mutation)
    monkeypatch.setattr(custody_module, "_destroy_records_in_run", observed_destroy)

    manager.destroy_expired_run(
        record.run_id,
        verification_id="custody-destruction-lock-check",
        identity=PRIMARY_CUSTODIAN,
        now=created_at + timedelta(days=90, seconds=1),
    )

    assert delete_observed_under_lock


@pytest.mark.parametrize(
    "interrupted_name",
    [
        "custody-bundle-recovery-check.json",
        "custody-bundle-recovery-check.json.sha256",
        "custody-bundle-recovery-check.json.hmac",
        "destruction.intent.json",
        "destruction.intent.json.sha256",
        "destruction.intent.json.hmac",
        "destruction.json",
        "destruction.json.sha256",
        "destruction.json.hmac",
    ],
)
def test_destruction_recovers_an_uncommitted_authenticated_bundle(
    custody_roots, monkeypatch, interrupted_name
):
    primary, backup, restore = custody_roots
    created_at = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    manager = _manager(primary, backup, restore)
    manager.initialize_roots()
    store = _store(primary, clock=lambda: created_at)
    record = store.persist_json(
        run_id="custody-bundle-recovery",
        scenario_id="P02-P03",
        artifact_type="internal-canary",
        kind=ArtifactKind.RAW,
        classification=Classification.INTERNAL,
        filename="canary.json",
        payload={"synthetic": True},
        source="test",
    )
    store.finalize_verification_retention(record.run_id, finished_at=created_at)
    manager.backup_run(record.run_id, identity=PRIMARY_CUSTODIAN)
    manager.restore_run(record.run_id, identity=PRIMARY_CUSTODIAN)
    original_write = custody_module._write_private
    interrupted = False

    def interrupt_after_write(path, content):
        nonlocal interrupted
        original_write(path, content)
        if not interrupted and path.name == interrupted_name:
            interrupted = True
            raise SystemExit("synthetic bundle interruption")

    monkeypatch.setattr(custody_module, "_write_private", interrupt_after_write)
    with pytest.raises(SystemExit, match="synthetic bundle interruption"):
        manager.destroy_expired_run(
            record.run_id,
            verification_id="custody-bundle-recovery-check",
            identity=PRIMARY_CUSTODIAN,
            now=created_at + timedelta(days=90, seconds=1),
        )
    assert interrupted

    monkeypatch.setattr(custody_module, "_write_private", original_write)
    receipt = manager.destroy_expired_run(
        record.run_id,
        verification_id="custody-bundle-recovery-check",
        identity=PRIMARY_CUSTODIAN,
        now=created_at + timedelta(days=90, seconds=2),
    )

    assert receipt.result == "DESTROYED"
    verification_root = primary / "custody-verifications" / "custody-bundle-recovery-check"
    for filename in ("destruction.intent.json", "destruction.json"):
        assert (verification_root / filename).is_file()
        assert (verification_root / f"{filename}.sha256").is_file()
        assert (verification_root / f"{filename}.hmac").is_file()
        assert (verification_root / f"{filename}.commit").is_file()
    assert not (primary / "custody-transactions" / "custody-bundle-recovery-check.json").exists()


def test_destruction_rejects_provisional_created_at_retention(custody_roots):
    primary, backup, restore = custody_roots
    created_at = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    manager = _manager(primary, backup, restore)
    manager.initialize_roots()
    store = _store(primary, clock=lambda: created_at)
    record = store.persist_json(
        run_id="custody-provisional-retention",
        scenario_id="P02-P03",
        artifact_type="internal-canary",
        kind=ArtifactKind.RAW,
        classification=Classification.INTERNAL,
        filename="canary.json",
        payload={"synthetic": True},
        source="test",
    )

    with pytest.raises(CustodyError, match="finished_at"):
        manager.destroy_expired_run(
            record.run_id,
            verification_id="custody-provisional-retention-check",
            identity=PRIMARY_CUSTODIAN,
            now=created_at + timedelta(days=90, seconds=1),
        )

    assert (primary / "runs" / record.run_id / record.relative_path).is_file()
    assert not (primary / "custody-transactions").exists()


def test_destruction_rejects_verification_id_traversal_before_writing(custody_roots):
    primary, backup, restore = custody_roots
    created_at = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    manager = _manager(primary, backup, restore)
    manager.initialize_roots()
    store = _store(primary, clock=lambda: created_at)
    record = store.persist_json(
        run_id="custody-verification-id-traversal",
        scenario_id="P02-P03",
        artifact_type="internal-canary",
        kind=ArtifactKind.RAW,
        classification=Classification.INTERNAL,
        filename="canary.json",
        payload={"synthetic": True},
        source="test",
    )
    manager.backup_run(record.run_id, identity=PRIMARY_CUSTODIAN)
    manager.restore_run(record.run_id, identity=PRIMARY_CUSTODIAN)
    escaped_transaction = primary / "custody-traversal.json"
    escaped_checksum = primary / "custody-traversal.json.sha256"

    with pytest.raises(CustodyError, match="verification_id"):
        manager.destroy_expired_run(
            record.run_id,
            verification_id="../custody-traversal",
            identity=PRIMARY_CUSTODIAN,
            now=created_at + timedelta(days=90, seconds=1),
        )

    assert not escaped_transaction.exists()
    assert not escaped_checksum.exists()


def test_destruction_does_not_extend_internal_retention_to_experimental(custody_roots):
    primary, backup, restore = custody_roots
    created_at = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    manager = _manager(primary, backup, restore)
    manager.initialize_roots()
    store = _store(primary, clock=lambda: created_at)
    run_id = "custody-mixed-retention-canary"
    internal = store.persist_json(
        run_id=run_id,
        scenario_id="P02-P03",
        artifact_type="internal-canary",
        kind=ArtifactKind.RAW,
        classification=Classification.INTERNAL,
        filename="internal.json",
        payload={"synthetic": True},
        source="test",
    )
    experimental = store.persist_json(
        run_id=run_id,
        scenario_id="P02-P03",
        artifact_type="experimental-canary",
        kind=ArtifactKind.DERIVED,
        classification=Classification.EXPERIMENTAL,
        filename="experimental.json",
        payload={"synthetic": True},
        source="test",
    )
    store.finalize_verification_retention(run_id, finished_at=created_at)
    manager.backup_run(run_id, identity=PRIMARY_CUSTODIAN)
    manager.restore_run(run_id, identity=PRIMARY_CUSTODIAN)

    receipt = manager.destroy_expired_run(
        run_id,
        verification_id="custody-mixed-retention-internal",
        identity=PRIMARY_CUSTODIAN,
        now=created_at + timedelta(days=90, seconds=1),
    )

    assert receipt.artifact_ids == (internal.artifact_id,)
    assert receipt.remaining_artifact_ids == (experimental.artifact_id,)
    for root in custody_roots:
        run_root = root / "runs" / run_id
        assert not (run_root / internal.relative_path).exists()
        assert (run_root / experimental.relative_path).is_file()
        assert _store(root).verify(run_id) == []


def test_manifest_bound_internal_destruction_preserves_public_and_authenticated_seal(
    custody_roots,
):
    primary, backup, restore = custody_roots
    created_at = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    finished_at = created_at + timedelta(hours=1)
    manager = _manager(primary, backup, restore)
    manager.initialize_roots()
    store = _store(primary, clock=lambda: created_at)
    run_id = "custody-final-manifest-public-survivor"
    internal = store.persist_json(
        run_id=run_id,
        scenario_id="EX-02",
        artifact_type="run-manifest-initial",
        kind=ArtifactKind.METADATA,
        classification=Classification.INTERNAL,
        filename="run-manifest.initial.json",
        payload=_initial_payload(run_id, "EX-02"),
        source="test",
    )
    experimental = store.persist_json(
        run_id=run_id,
        scenario_id="EX-02",
        artifact_type="experimental-canary",
        kind=ArtifactKind.DERIVED,
        classification=Classification.EXPERIMENTAL,
        filename="experimental.json",
        payload={"synthetic": True},
        source="test",
    )
    public_payload = {"aggregate": 1}
    public_artifact_type = "public-canary"
    approval = create_publication_approval(
        approval_id="S11-PUBLIC-MANIFEST-RETENTION-TEST",
        artifact_id=f"S11-EX-02-{run_id}-{public_artifact_type}",
        content_sha256=sha256_bytes(canonical_json_bytes(public_payload)),
        approved_at=created_at,
        signing_key=_PUBLICATION_SIGNING_KEY,
    )
    public = store.persist_json(
        run_id=run_id,
        scenario_id="EX-02",
        artifact_type=public_artifact_type,
        kind=ArtifactKind.DERIVED,
        classification=Classification.PUBLIC,
        filename="public.json",
        payload=public_payload,
        source="test",
        publication_approval=approval,
    )
    final = store.finalize_with_json_artifact(
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
    manager.backup_run(run_id, identity=PRIMARY_CUSTODIAN)
    manager.restore_run(run_id, identity=PRIMARY_CUSTODIAN)

    receipt = manager.destroy_expired_run(
        run_id,
        verification_id="custody-final-manifest-public-survivor-check",
        identity=PRIMARY_CUSTODIAN,
        now=finished_at + timedelta(days=90, seconds=1),
    )

    assert set(receipt.artifact_ids) == {internal.artifact_id, final.artifact_id}
    assert set(receipt.remaining_artifact_ids) == {
        experimental.artifact_id,
        public.artifact_id,
    }
    for root in custody_roots:
        run_root = root / "runs" / run_id
        assert (run_root / public.relative_path).is_file()
        assert (run_root / experimental.relative_path).is_file()
        assert not (run_root / internal.relative_path).exists()
        assert not (run_root / final.relative_path).exists()
        assert (run_root / ".s11-finalized").is_file()
        assert _store(root).verify(run_id) == []

    experimental_receipt = manager.destroy_expired_run(
        run_id,
        verification_id="custody-final-manifest-experimental-survivor-check",
        identity=PRIMARY_CUSTODIAN,
        now=finished_at + timedelta(days=365, seconds=1),
    )

    assert experimental_receipt.artifact_ids == (experimental.artifact_id,)
    assert experimental_receipt.remaining_artifact_ids == (public.artifact_id,)
    for root in custody_roots:
        run_root = root / "runs" / run_id
        assert (run_root / public.relative_path).is_file()
        assert not (run_root / experimental.relative_path).exists()
        assert (run_root / ".s11-finalized").is_file()
        assert _store(root).verify(run_id) == []


def test_destruction_resumes_idempotently_after_partial_multi_root_failure(
    custody_roots, monkeypatch
):
    primary, backup, restore = custody_roots
    created_at = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    manager = _manager(primary, backup, restore)
    manager.initialize_roots()
    store = _store(primary, clock=lambda: created_at)
    record = store.persist_json(
        run_id="custody-resumable-destruction",
        scenario_id="P02-P03",
        artifact_type="internal-canary",
        kind=ArtifactKind.RAW,
        classification=Classification.INTERNAL,
        filename="canary.json",
        payload={"synthetic": True},
        source="test",
    )
    store.finalize_verification_retention(record.run_id, finished_at=created_at)
    manager.backup_run(record.run_id, identity=PRIMARY_CUSTODIAN)
    manager.restore_run(record.run_id, identity=PRIMARY_CUSTODIAN)
    original_destroy = custody_module._destroy_records_in_run
    calls = 0

    def fail_second_root(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("synthetic partial failure")
        return original_destroy(*args, **kwargs)

    monkeypatch.setattr(custody_module, "_destroy_records_in_run", fail_second_root)
    with pytest.raises(OSError, match="synthetic partial failure"):
        manager.destroy_expired_run(
            record.run_id,
            verification_id="custody-resumable-destruction-check",
            identity=PRIMARY_CUSTODIAN,
            now=created_at + timedelta(days=90, seconds=1),
        )

    monkeypatch.setattr(custody_module, "_destroy_records_in_run", original_destroy)
    receipt = manager.destroy_expired_run(
        record.run_id,
        verification_id="custody-resumable-destruction-check",
        identity=PRIMARY_CUSTODIAN,
        now=created_at + timedelta(days=90, seconds=2),
    )
    repeated = manager.destroy_expired_run(
        record.run_id,
        verification_id="custody-resumable-destruction-check",
        identity=PRIMARY_CUSTODIAN,
        now=created_at + timedelta(days=90, seconds=3),
    )

    assert receipt == repeated
    assert receipt.result == "DESTROYED"
    assert all(not (root / "runs" / record.run_id).exists() for root in custody_roots)
    assert not (
        primary / "custody-transactions" / "custody-resumable-destruction-check.json"
    ).exists()


def test_tampered_destruction_journal_cannot_add_public_artifact(custody_roots, monkeypatch):
    primary, backup, restore = custody_roots
    created_at = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    manager = _manager(primary, backup, restore)
    manager.initialize_roots()
    store = _store(primary, clock=lambda: created_at)
    run_id = "custody-journal-public-attack"
    internal = store.persist_json(
        run_id=run_id,
        scenario_id="P02-P03",
        artifact_type="internal-canary",
        kind=ArtifactKind.RAW,
        classification=Classification.INTERNAL,
        filename="internal.json",
        payload={"synthetic": True},
        source="test",
    )
    public_payload = {"aggregate": 1}
    public_artifact_type = "public-canary"
    public_approval = create_publication_approval(
        approval_id="S11-PUBLIC-JOURNAL-ATTACK-TEST",
        artifact_id=f"S11-P02-P03-{run_id}-{public_artifact_type}",
        content_sha256=sha256_bytes(canonical_json_bytes(public_payload)),
        approved_at=created_at,
        signing_key=_PUBLICATION_SIGNING_KEY,
    )
    public = store.persist_json(
        run_id=run_id,
        scenario_id="P02-P03",
        artifact_type=public_artifact_type,
        kind=ArtifactKind.DERIVED,
        classification=Classification.PUBLIC,
        filename="public.json",
        payload=public_payload,
        source="test",
        publication_approval=public_approval,
    )
    store.finalize_verification_retention(run_id, finished_at=created_at)
    manager.backup_run(run_id, identity=PRIMARY_CUSTODIAN)
    manager.restore_run(run_id, identity=PRIMARY_CUSTODIAN)

    def interrupt_before_delete(*args, **kwargs):
        raise SystemExit("synthetic interruption before delete")

    monkeypatch.setattr(custody_module, "_destroy_records_in_run", interrupt_before_delete)
    verification_id = "custody-journal-public-attack-check"
    with pytest.raises(SystemExit, match="before delete"):
        manager.destroy_expired_run(
            run_id,
            verification_id=verification_id,
            identity=PRIMARY_CUSTODIAN,
            now=created_at + timedelta(days=90, seconds=1),
        )

    transaction = primary / "custody-transactions" / f"{verification_id}.json"
    payload = json.loads(transaction.read_text(encoding="utf-8"))
    public_record = next(
        record
        for record in payload["remaining_records"]
        if record["artifact_id"] == public.artifact_id
    )
    payload["artifact_ids"].append(public.artifact_id)
    payload["artifact_ids"].sort()
    payload["classification"] = "INTERNAL,PUBLIC"
    payload["expired_records"].append(public_record)
    payload["remaining_records"] = []
    transaction.write_bytes(canonical_json_bytes(payload))
    transaction.with_suffix(".json.sha256").write_text(
        f"{sha256_bytes(transaction.read_bytes())}  {transaction.name}\n",
        encoding="ascii",
    )

    monkeypatch.setattr(custody_module, "_destroy_records_in_run", lambda *args, **kwargs: None)
    with pytest.raises(CustodyError, match="marker del bundle"):
        manager.destroy_expired_run(
            run_id,
            verification_id=verification_id,
            identity=PRIMARY_CUSTODIAN,
            now=created_at + timedelta(days=90, seconds=2),
        )

    for root in custody_roots:
        run_root = root / "runs" / run_id
        assert (run_root / internal.relative_path).is_file()
        assert (run_root / public.relative_path).is_file()


def test_resumed_destruction_refuses_public_artifact_created_after_journal(
    custody_roots, monkeypatch
):
    primary, backup, restore = custody_roots
    created_at = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    manager = _manager(primary, backup, restore)
    manager.initialize_roots()
    store = _store(primary, clock=lambda: created_at)
    run_id = "custody-valid-journal-late-public"
    internal = store.persist_json(
        run_id=run_id,
        scenario_id="P02-P03",
        artifact_type="internal-canary",
        kind=ArtifactKind.RAW,
        classification=Classification.INTERNAL,
        filename="internal.json",
        payload={"synthetic": True},
        source="test",
    )
    store.finalize_verification_retention(run_id, finished_at=created_at)
    manager.backup_run(run_id, identity=PRIMARY_CUSTODIAN)
    manager.restore_run(run_id, identity=PRIMARY_CUSTODIAN)
    original_destroy = custody_module._destroy_records_in_run

    def interrupt_before_delete(*args, **kwargs):
        raise SystemExit("synthetic interruption before delete")

    monkeypatch.setattr(custody_module, "_destroy_records_in_run", interrupt_before_delete)
    verification_id = "custody-valid-journal-late-public-check"
    with pytest.raises(SystemExit, match="before delete"):
        manager.destroy_expired_run(
            run_id,
            verification_id=verification_id,
            identity=PRIMARY_CUSTODIAN,
            now=created_at + timedelta(days=90, seconds=1),
        )

    public_payload = {"aggregate": 1}
    public_artifact_type = "late-public-canary"
    public_approval = create_publication_approval(
        approval_id="S11-PUBLIC-LATE-JOURNAL-TEST",
        artifact_id=f"S11-P02-P03-{run_id}-{public_artifact_type}",
        content_sha256=sha256_bytes(canonical_json_bytes(public_payload)),
        approved_at=created_at,
        signing_key=_PUBLICATION_SIGNING_KEY,
    )
    public = store.persist_json(
        run_id=run_id,
        scenario_id="P02-P03",
        artifact_type=public_artifact_type,
        kind=ArtifactKind.DERIVED,
        classification=Classification.PUBLIC,
        filename="public.json",
        payload=public_payload,
        source="test",
        publication_approval=public_approval,
    )

    monkeypatch.setattr(custody_module, "_destroy_records_in_run", original_destroy)
    with pytest.raises(CustodyError, match="nuevo o alterado"):
        manager.destroy_expired_run(
            run_id,
            verification_id=verification_id,
            identity=PRIMARY_CUSTODIAN,
            now=created_at + timedelta(days=90, seconds=2),
        )

    assert (primary / "runs" / run_id / internal.relative_path).is_file()
    assert (primary / "runs" / run_id / public.relative_path).is_file()


def test_destruction_resumes_after_interruption_inside_one_root(custody_roots, monkeypatch):
    primary, backup, restore = custody_roots
    created_at = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    manager = _manager(primary, backup, restore)
    manager.initialize_roots()
    store = _store(primary, clock=lambda: created_at)
    run_id = "custody-intra-root-resume"
    internal = store.persist_json(
        run_id=run_id,
        scenario_id="P02-P03",
        artifact_type="internal-canary",
        kind=ArtifactKind.RAW,
        classification=Classification.INTERNAL,
        filename="internal.json",
        payload={"synthetic": True},
        source="test",
    )
    experimental = store.persist_json(
        run_id=run_id,
        scenario_id="P02-P03",
        artifact_type="experimental-canary",
        kind=ArtifactKind.DERIVED,
        classification=Classification.EXPERIMENTAL,
        filename="experimental.json",
        payload={"synthetic": True},
        source="test",
    )
    store.finalize_verification_retention(run_id, finished_at=created_at)
    manager.backup_run(run_id, identity=PRIMARY_CUSTODIAN)
    manager.restore_run(run_id, identity=PRIMARY_CUSTODIAN)
    original_replace = custody_module._replace_private

    def interrupt_after_expired_unlink(path, content):
        if path.name == "artifacts.jsonl":
            raise SystemExit("synthetic intra-root interruption")
        return original_replace(path, content)

    monkeypatch.setattr(custody_module, "_replace_private", interrupt_after_expired_unlink)
    with pytest.raises(SystemExit, match="synthetic intra-root interruption"):
        manager.destroy_expired_run(
            run_id,
            verification_id="custody-intra-root-resume-check",
            identity=PRIMARY_CUSTODIAN,
            now=created_at + timedelta(days=90, seconds=1),
        )

    assert not (primary / "runs" / run_id / internal.relative_path).exists()
    monkeypatch.setattr(custody_module, "_replace_private", original_replace)
    receipt = manager.destroy_expired_run(
        run_id,
        verification_id="custody-intra-root-resume-check",
        identity=PRIMARY_CUSTODIAN,
        now=created_at + timedelta(days=90, seconds=2),
    )

    assert receipt.artifact_ids == (internal.artifact_id,)
    assert receipt.remaining_artifact_ids == (experimental.artifact_id,)
    for root in custody_roots:
        run_root = root / "runs" / run_id
        assert not (run_root / internal.relative_path).exists()
        assert (run_root / experimental.relative_path).is_file()
        assert _store(root).verify(run_id) == []


def test_two_phase_verification_preserves_then_destroys_synthetic_canary(custody_roots):
    primary, backup, restore = custody_roots
    manager = _manager(primary, backup, restore)
    created_at = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)

    prepared = prepare_custody_verification(
        manager,
        verification_id="custody-two-phase-canary",
        clock=lambda: created_at,
        execution_context={"hostname": "test-node", "pid": 100},
        source_snapshot=_source_snapshot(),
    )
    assert prepared["gate_checks"] == {
        "public_requires_signed_monty_approval": True,
        "public_runtime_rejects_untrusted_private_key": True,
        "secret_non_persistent": True,
        "sensitive_disabled": True,
    }
    assert prepared["retention_anchor"] == "verification_finished_at"
    assert Path(prepared["evidence_path"]).is_file()

    completed = complete_custody_verification(
        _manager(primary, backup, restore),
        verification_id="custody-two-phase-canary",
        persistence_boundary="independent-process",
        execution_context={"hostname": "test-node", "pid": 101},
        source_snapshot=_source_snapshot(),
    )
    assert completed["phase"] == "COMPLETED"
    assert completed["persistence"]["run_recovered"]
    assert completed["persistence"]["boundary_verified"]
    assert completed["backup"]["sha256_match"]
    assert completed["restore"]["sha256_match"]
    assert completed["destruction"]["result"] == "DESTROYED"
    assert Path(completed["evidence_path"]).is_file()

    with pytest.raises(CustodyError, match="otro run"):
        manager.destroy_expired_run(
            "custody-other-run",
            verification_id="custody-two-phase-canary",
            identity=PRIMARY_CUSTODIAN,
            now=created_at + timedelta(days=90, seconds=2),
        )
    with pytest.raises(CustodyError, match="otra identidad"):
        manager.destroy_expired_run(
            "custody-two-phase-canary",
            verification_id="custody-two-phase-canary",
            identity=COAUTHOR_RESEARCHER,
            now=created_at + timedelta(days=90, seconds=2),
        )


def test_two_phase_verification_rejects_an_unproven_container_recreation(custody_roots):
    primary, backup, restore = custody_roots
    manager = _manager(primary, backup, restore)
    prepare_custody_verification(
        manager,
        verification_id="custody-same-container-canary",
        execution_context={"hostname": "same-container", "pid": 100},
        source_snapshot=_source_snapshot(),
    )

    with pytest.raises(CustodyError, match="contenedores distintos"):
        complete_custody_verification(
            _manager(primary, backup, restore),
            verification_id="custody-same-container-canary",
            persistence_boundary="container-recreated",
            execution_context={"hostname": "same-container", "pid": 101},
            source_snapshot=_source_snapshot(),
        )


def test_custody_roots_are_private_and_must_be_separate(custody_roots):
    primary, backup, restore = custody_roots
    manager = _manager(primary, backup, restore)
    manager.initialize_roots()
    assert all(root.stat().st_mode & 0o077 == 0 for root in custody_roots)

    with pytest.raises(CustodyError, match="ubicaciones distintas"):
        CustodyManager(
            primary,
            primary,
            restore,
            custody_hmac_key=_CUSTODY_KEY,
            publication_verification_key=_PUBLICATION_VERIFICATION_KEY,
        )
    with pytest.raises(CustodyError, match="/tmp"):
        CustodyManager(
            Path("/tmp/s11-primary"),
            backup,
            restore,
            custody_hmac_key=_CUSTODY_KEY,
            publication_verification_key=_PUBLICATION_VERIFICATION_KEY,
        )


def test_publication_authority_is_exactly_the_approved_identity():
    assert PUBLICATION_AUTHORITY == "Monty Emmanuel Pohl Amarilla"


def test_custody_revalidates_stored_public_approval_against_content(custody_roots):
    primary, backup, restore = custody_roots
    created_at = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    manager = _manager(primary, backup, restore)
    manager.initialize_roots()
    store = _store(primary, clock=lambda: created_at)
    run_id = "custody-public-forgery-canary"
    artifact_type = "public-canary"
    payload = {"aggregate": 1}
    content_sha256 = sha256_bytes(canonical_json_bytes(payload))
    approval = create_publication_approval(
        approval_id="S11-PUBLIC-CUSTODY-TEST",
        artifact_id=f"S11-P02-P03-{run_id}-{artifact_type}",
        content_sha256=content_sha256,
        approved_at=created_at,
        signing_key=_PUBLICATION_SIGNING_KEY,
    )
    record = store.persist_json(
        run_id=run_id,
        scenario_id="P02-P03",
        artifact_type=artifact_type,
        kind=ArtifactKind.DERIVED,
        classification=Classification.PUBLIC,
        filename="public.json",
        payload=payload,
        source="test",
        publication_approval=approval,
    )
    run_root = primary / "runs" / run_id
    artifact = run_root / record.relative_path
    forged_content = canonical_json_bytes({"aggregate": 999})
    artifact.write_bytes(forged_content)
    index = run_root / "metadata" / "artifacts.jsonl"
    forged_record = json.loads(index.read_text(encoding="utf-8"))
    forged_record["sha256"] = sha256_bytes(forged_content)
    forged_record["size_bytes"] = len(forged_content)
    index.write_bytes(canonical_json_bytes(forged_record))
    store.write_checksums(run_id)

    with pytest.raises(CustodyError, match="firma del inventario"):
        manager.backup_run(run_id, identity=PRIMARY_CUSTODIAN)


def test_custody_signature_rejects_public_to_internal_downgrade(custody_roots):
    primary, backup, restore = custody_roots
    created_at = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    manager = _manager(primary, backup, restore)
    manager.initialize_roots()
    store = _store(primary, clock=lambda: created_at)
    run_id = "custody-public-downgrade-canary"
    artifact_type = "public-canary"
    payload = {"aggregate": 1}
    content_sha256 = sha256_bytes(canonical_json_bytes(payload))
    approval = create_publication_approval(
        approval_id="S11-PUBLIC-DOWNGRADE-TEST",
        artifact_id=f"S11-P02-P03-{run_id}-{artifact_type}",
        content_sha256=content_sha256,
        approved_at=created_at,
        signing_key=_PUBLICATION_SIGNING_KEY,
    )
    store.persist_json(
        run_id=run_id,
        scenario_id="P02-P03",
        artifact_type=artifact_type,
        kind=ArtifactKind.DERIVED,
        classification=Classification.PUBLIC,
        filename="public.json",
        payload=payload,
        source="test",
        publication_approval=approval,
    )
    run_root = primary / "runs" / run_id
    index = run_root / "metadata" / "artifacts.jsonl"
    downgraded = json.loads(index.read_text(encoding="utf-8"))
    downgraded["classification"] = Classification.INTERNAL.value
    downgraded["publication_approval"] = None
    downgraded["expires_at"] = "2026-12-20T12:00:00Z"
    index.write_bytes(canonical_json_bytes(downgraded))
    store.write_checksums(run_id)

    with pytest.raises(CustodyError, match="firma del inventario"):
        manager.backup_run(run_id, identity=PRIMARY_CUSTODIAN)


def test_index_signature_rejects_omission_of_public_record(custody_roots):
    primary, backup, restore = custody_roots
    created_at = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    manager = _manager(primary, backup, restore)
    manager.initialize_roots()
    store = _store(primary, clock=lambda: created_at)
    run_id = "custody-public-omission-canary"
    artifact_type = "public-canary"
    payload = {"aggregate": 1}
    approval = create_publication_approval(
        approval_id="S11-PUBLIC-OMISSION-TEST",
        artifact_id=f"S11-P02-P03-{run_id}-{artifact_type}",
        content_sha256=sha256_bytes(canonical_json_bytes(payload)),
        approved_at=created_at,
        signing_key=_PUBLICATION_SIGNING_KEY,
    )
    public = store.persist_json(
        run_id=run_id,
        scenario_id="P02-P03",
        artifact_type=artifact_type,
        kind=ArtifactKind.DERIVED,
        classification=Classification.PUBLIC,
        filename="public.json",
        payload=payload,
        source="test",
        publication_approval=approval,
    )
    store.persist_json(
        run_id=run_id,
        scenario_id="P02-P03",
        artifact_type="internal-canary",
        kind=ArtifactKind.RAW,
        classification=Classification.INTERNAL,
        filename="internal.json",
        payload={"synthetic": True},
        source="test",
    )
    run_root = primary / "runs" / run_id
    (run_root / public.relative_path).unlink()
    index = run_root / "metadata" / "artifacts.jsonl"
    remaining = [
        record for record in store.records(run_id) if record["artifact_id"] != public.artifact_id
    ]
    index.write_bytes(b"".join(canonical_json_bytes(record) for record in remaining))
    EvidenceStore(
        primary,
        custody_hmac_key=b"attacker-does-not-know-custody-key!",
        publication_verification_key=_PUBLICATION_VERIFICATION_KEY,
    ).write_checksums(run_id)

    with pytest.raises(CustodyError, match="firma del inventario"):
        manager.backup_run(run_id, identity=PRIMARY_CUSTODIAN)


def test_closed_custody_verifier_and_its_tests_are_attested_sources():
    # Decisión del autor tras el incidente del 26/09/2026: P02/P03 atesta el verificador
    # read-only de épocas cerradas y sus pruebas.
    assert {
        "backend/scripts/s11_verify_closed_custody.py",
        "backend/apps/experiments_app/tests/test_s11_verify_closed_custody.py",
    } <= set(ATTESTED_SOURCE_PATHS)


def test_commit_snapshot_rejects_commit_outside_approved_baseline(monkeypatch, tmp_path):
    commit_sha = "a" * 40

    def fake_git_output(root, *arguments):
        assert root == tmp_path.resolve()
        if arguments == ("rev-parse", "HEAD"):
            return f"{commit_sha}\n".encode()
        if arguments == ("cat-file", "-e", f"{commit_sha}^{{commit}}"):
            return b""
        if arguments == (
            "merge-base",
            "--is-ancestor",
            EXPECTED_BASELINE_SHA,
            commit_sha,
        ):
            raise CustodyError("synthetic non-ancestor")
        raise AssertionError(arguments)

    monkeypatch.setattr(custody_module, "_git_output", fake_git_output)

    with pytest.raises(CustodyError, match="baseline aprobado no es ancestro"):
        build_commit_source_snapshot(
            tmp_path,
            baseline_sha=EXPECTED_BASELINE_SHA,
            commit_sha=commit_sha,
        )
