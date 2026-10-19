"""Synthetic offline B3 fixtures only: no historical roots or operational keys."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import os
import sqlite3
import sys
import tempfile
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

APP = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get("S11_B3_SOURCE_DIR", str(APP)))
PACKAGE = types.ModuleType("b3_contract_fixture_" + __name__.replace(".", "_"))
PACKAGE.__path__ = [str(SOURCE)]
sys.modules[PACKAGE.__name__] = PACKAGE
MODULES = {}
for name in ("ex10_authority_sqlite", "ex10_authority_witness", "ex10_history_authority"):
    spec = importlib.util.spec_from_file_location(
        PACKAGE.__name__ + "." + name, SOURCE / (name + ".py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    MODULES[name] = module
sql = MODULES["ex10_authority_sqlite"]
wit = MODULES["ex10_authority_witness"]
history = MODULES["ex10_history_authority"]
AUTH_ID = "11111111-2222-3333-4444-555555555555"
RUN_ID = "s11-22222222-3333-4444-5555-666666666666"
OTHER_RUN = "s11-33333333-4444-5555-6666-777777777777"
OP_ZERO = "44444444-5555-6666-7777-888888888888"
OP_NEXT = "55555555-6666-7777-8888-999999999999"
NOW = "2026-10-04T00:00:00.000000Z"


def hashed(label):
    return hashlib.sha256(label.encode()).hexdigest()


def reference(label, schema="s11-synthetic-proof-v1", sha=None):
    return {
        "path": "/synthetic-fixture/" + label,
        "sha256": sha or hashed(label),
        "schema_version": schema,
    }


def artifact(ref, kind="EVIDENCE"):
    value = {**ref, "kind": kind}
    return {**value, "reference_id": sql.digest(value), "introduced_revision": 0}


def timestamp(offset=0):
    return (datetime.now(UTC) + timedelta(seconds=offset)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _test_connection(path):
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=DELETE")
    con.execute("PRAGMA synchronous=EXTRA")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA trusted_schema=OFF")
    con.execute("PRAGMA recursive_triggers=ON")
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert con.execute("PRAGMA synchronous").fetchone()[0] == 3
    return con


class Fixture:
    """All signing keys are generated ephemerally in memory, never serialized."""

    def __init__(self, root, kinds=("ND",), create_db=False):
        self.root = root
        self.database = root / "ledger.sqlite3"
        self.key = Ed25519PrivateKey.generate()
        self.state = sql.empty_state()
        refs = {
            "scope": (reference("scope"), "COVERAGE"),
            "coverage": (reference("coverage"), "COVERAGE"),
            "approval": (reference("approval"), "APPROVAL"),
            "g0": (reference("g0"), "APPROVAL"),
            "evidence": (reference("evidence"), "EVIDENCE"),
            "snapshot": (reference("snapshot"), "SNAPSHOT"),
            "observation": (reference("observation"), "SNAPSHOT"),
            "profile": (reference("profile"), "PROFILE"),
            "session": (reference("session"), "SESSION"),
        }
        self.refs = {k: v[0] for k, v in refs.items()}
        self.ref_ids = {k: artifact(ref, kind)["reference_id"] for k, (ref, kind) in refs.items()}
        self.state["artifact_references"] = [artifact(ref, kind) for ref, kind in refs.values()]
        for number, kind in enumerate(kinds):
            self.state["official_runs"].append(self.run(kind, RUN_ID if number == 0 else OTHER_RUN))
        active = any(r["lifecycle_state"] != "TERMINAL" for r in self.state["official_runs"])
        run_ids = sorted(r["run_id"] for r in self.state["official_runs"])
        facts = []
        for run in self.state["official_runs"]:
            ref = reference("facts-" + run["run_id"], sha=sql.digest(run))
            entry = artifact(ref)
            self.state["artifact_references"].append(entry)
            facts.append(
                {
                    "run_id": run["run_id"],
                    "source_ids": ["synthetic"],
                    "normalized_facts_reference": ref,
                    "contradiction_reference": None,
                }
            )
            self.state["bootstrap_findings"].append(
                {
                    "source_id": "synthetic",
                    "run_id": run["run_id"],
                    "run_version": 0,
                    "finding_reference_id": entry["reference_id"],
                    "is_primary_evidence": True,
                }
            )
        self.core = {
            "schema_version": "s11-ex10-authority-bootstrap-core-v1",
            "authority_id": AUTH_ID,
            "authority_epoch": 1,
            "namespace": "S11C3-EX10",
            "scope_reference": self.refs["scope"],
            "coverage_certificate_reference": self.refs["coverage"],
            "baseline_head": "1" * 40,
            "baseline_tree": "2" * 40,
            "observed_at": NOW,
            "completeness": "COMPLETE",
            "sources": [
                {
                    "source_id": "synthetic",
                    "source_kind": "EVIDENCE_ROOT",
                    "location": "/synthetic-fixture/evidence",
                    "required": True,
                    "status": "SCANNED",
                    "observation_reference": self.refs["observation"],
                    "snapshot_reference": self.refs["snapshot"],
                    "source_digest": self.refs["snapshot"]["sha256"],
                    "run_ids": run_ids,
                    "active_source": active,
                    "custody_references": [self.refs["evidence"]],
                    "diagnostic_codes": [],
                }
            ],
            "run_findings": sorted(facts, key=lambda r: r["run_id"]),
            "unreadable_source_ids": [],
            "active_source_ids": ["synthetic"] if active else [],
            "writer_closure_reference": self.refs["coverage"],
            "run_count": len(kinds),
            "source_count": 1,
        }
        core_ref = reference("core", self.core["schema_version"], sql.digest(self.core))
        self.sealed = {
            "schema_version": "s11-ex10-authority-bootstrap-manifest-v1",
            "core_manifest_reference": core_ref,
            "core_manifest_digest": core_ref["sha256"],
            "operator_approval_reference": self.refs["approval"],
            "approved_at": NOW,
            "completeness": "COMPLETE",
        }
        bootstrap_ref = reference(
            "bootstrap", self.sealed["schema_version"], sql.digest(self.sealed)
        )
        boot_artifact = artifact(bootstrap_ref, "BOOTSTRAP")
        self.state["artifact_references"].append(boot_artifact)
        transport = reference("transport")
        public = self.key.public_key().public_bytes_raw()
        pin_record = {
            "schema_version": "s11-ex10-authority-trust-root-v1",
            "authority_id": AUTH_ID,
            "authority_epoch": 1,
            "namespace": "S11C3-EX10",
            "key_id": "synthetic-key",
            "key_epoch": 1,
            "algorithm": "Ed25519",
            "public_key_hex": public.hex(),
            "fingerprint": hashlib.sha256(public).hexdigest(),
            "approval_reference": self.refs["approval"],
            "key_transition_reference": None,
            "activation_revision": 0,
            "retirement_revision": None,
            "transport_identity_reference": transport,
        }
        self.pin = wit.TrustPin(sql.canonical_bytes(pin_record))
        self.trust_ref = reference("trust", pin_record["schema_version"], sql.digest(pin_record))
        self.descriptor = {
            "schema_version": "s11-ex10-historical-authority-descriptor-v1",
            "authority_id": AUTH_ID,
            "authority_epoch": 1,
            "namespace": "S11C3-EX10",
            "authority_schema_version": sql.SCHEMA_VERSION,
            "bootstrap_manifest_reference": bootstrap_ref,
            "g0_a3_approval_reference": self.refs["g0"],
            "source_baseline_head": "1" * 40,
            "source_baseline_tree": "2" * 40,
            "ledger_locator": str(self.database),
            "trust_root_reference": self.trust_ref,
            "minimum_revision": 0,
            "revision_policy": "IDENTITY_MINIMUM_AND_FRESH_CURRENT_HEAD",
            "witness_head_reference": {
                "schema_version": wit.HEAD_SCHEMA,
                "endpoint": "https://synthetic-witness.invalid/head",
                "transport_identity_reference": transport,
                "authority_id": AUTH_ID,
                "authority_epoch": 1,
                "namespace": "S11C3-EX10",
            },
        }
        self.descriptor_ref = reference(
            "descriptor", self.descriptor["schema_version"], sql.digest(self.descriptor)
        )
        self.state["metadata"] = [
            {
                "singleton": 1,
                "authority_id": AUTH_ID,
                "schema_version": sql.SCHEMA_VERSION,
                "namespace": "S11C3-EX10",
                "epoch": 1,
                "descriptor_sha256": self.descriptor_ref["sha256"],
                "bootstrap_reference_id": boot_artifact["reference_id"],
                "g0_a3_approval_reference_id": self.ref_ids["g0"],
                "source_baseline_head": "1" * 40,
                "source_baseline_tree": "2" * 40,
            }
        ]
        self.state["bootstrap_manifest"] = [
            {
                "singleton": 1,
                "manifest_reference_id": boot_artifact["reference_id"],
                "core_digest": sql.digest(self.core),
                "completeness": "COMPLETE",
                "scope_reference_id": self.ref_ids["scope"],
                "coverage_certificate_reference_id": self.ref_ids["coverage"],
                "operator_approval_reference_id": self.ref_ids["approval"],
                "certified_at": NOW,
                "baseline_head": "1" * 40,
                "baseline_tree": "2" * 40,
                "source_count": 1,
                "run_count": len(kinds),
            }
        ]
        self.state["bootstrap_sources"] = [
            {
                "source_id": "synthetic",
                "scope_reference_id": self.ref_ids["scope"],
                "location": "/synthetic-fixture/evidence",
                "source_kind": "EVIDENCE_ROOT",
                "status": "SCANNED",
                "snapshot_reference_id": self.ref_ids["snapshot"],
                "observation_reference_id": self.ref_ids["observation"],
                "observed_at": NOW,
                "discovered_run_count": len(kinds),
                "has_active_source": active,
            }
        ]
        events = [self.event("GENESIS", sequence=1, run=None)]
        events += [
            self.event("IMPORT_RUN", sequence=i + 2, run=run)
            for i, run in enumerate(self.state["official_runs"])
        ]
        self.state["authority_events"] = events
        revision = {
            "revision": 0,
            "operation_id": OP_ZERO,
            "previous_ledger_digest": None,
            "ledger_digest": "0" * 64,
            "recorded_at": NOW,
            "first_event_sequence": 1,
            "last_event_sequence": len(events),
            "event_count": len(events),
        }
        self.state["history_revisions"] = [revision]
        revision["ledger_digest"] = sql.logical_digest(self.state, 0)
        payload = {
            "witness_schema_version": wit.RECEIPT_SCHEMA,
            "authority_id": AUTH_ID,
            "authority_epoch": 1,
            "namespace": "S11C3-EX10",
            "operation_id": OP_ZERO,
            "revision": 0,
            "ledger_digest": revision["ledger_digest"],
            "previous_witness_digest": None,
            "created_at": NOW,
            "key_id": "synthetic-key",
            "key_epoch": 1,
            "key_fingerprint": pin_record["fingerprint"],
            "genesis": {
                "bootstrap_manifest_sha256": bootstrap_ref["sha256"],
                "g0_a3_approval_reference": self.refs["g0"],
                "source_baseline_head": "1" * 40,
                "source_baseline_tree": "2" * 40,
            },
        }
        self.receipt = wit.sign_witness(wit.build_witness_payload(payload), self.key)
        self.state["witness_checkpoints"] = [history._checkpoint(self.receipt)]
        self.floor = wit.TrustedFloor(AUTH_ID, 1, 0, sql.digest(self.receipt))
        payload_path = root / "genesis-payload.json"
        payload_path.write_bytes(sql.canonical_bytes(sql.logical_projection(self.state, 0)))
        payload_path.chmod(0o600)
        self.state["publication_intents"] = [
            {
                "operation_id": OP_ZERO,
                "base_revision": None,
                "candidate_revision": 0,
                "base_ledger_digest": None,
                "candidate_ledger_digest": revision["ledger_digest"],
                "candidate_payload_path": str(payload_path),
                "candidate_payload_sha256": revision["ledger_digest"],
                "serialization_capability_digest": hashed("synthetic-genesis-capability"),
                "created_at": NOW,
            }
        ]
        self.state["publication_resolutions"] = [
            {
                "operation_id": OP_ZERO,
                "disposition": "COMMITTED",
                "resolved_at": NOW,
                "committed_revision": 0,
                "receipt_digest": sql.digest(self.receipt),
                "operator_approval_path": None,
                "operator_approval_sha256": None,
                "operator_approval_schema": None,
                "fresh_witness_observation_digest": sql.digest(self.observation()["head"]),
            }
        ]
        if create_db:
            self.store()

    def run(self, kind, run_id=RUN_ID):
        terminal = kind in ("ND", "PASS", "FAIL")
        nd = kind == "ND"
        return {
            "run_id": run_id,
            "run_version": 0,
            "previous_run_version": None,
            "source_revision": "3" * 40,
            "protocol": "synthetic-v1",
            "methodology": "synthetic-methodology",
            "mode": "OFFICIAL",
            "provenance_model": "HISTORICAL_V1",
            "lifecycle_state": "TERMINAL" if terminal else kind,
            "phase": "FINALIZED"
            if terminal
            else {"OPEN": "OPENED", "ACTIVE": "EXECUTING", "UNCERTAIN": "UNCERTAIN"}[kind],
            "result": "NOT_DEMONSTRATED" if nd else kind if terminal else None,
            "binding": terminal and not nd,
            "validity_classification": "NON_BINDING_ND"
            if nd
            else "VALID_BINDING"
            if terminal
            else "UNCERTAIN"
            if kind == "UNCERTAIN"
            else "PENDING",
            "created_at": NOW,
            "opened_at": NOW,
            "finalized_at": NOW if terminal else None,
            "ack_required": nd,
            "evidence_authority_reference_id": self.ref_ids["evidence"],
            "profile_reference_id": None,
            "session_reference_id": self.ref_ids["session"],
            "introduced_revision": 0,
        }

    def event(self, kind, *, sequence, run, revision=0, resolves=None):
        return {
            "event_sequence": sequence,
            "revision": revision,
            "event_kind": kind,
            "run_id": None if run is None else run["run_id"],
            "run_version": None if run is None else run["run_version"],
            "evidence_reference_id": self.ref_ids["approval"]
            if kind == "MANUAL_RESOLUTION"
            else self.ref_ids["evidence"],
            "occurred_at": NOW,
            "reason_code": None,
            "resolves_event_sequence": resolves,
        }

    def material(self, state=None):
        return {
            "descriptor": copy.deepcopy(self.descriptor),
            "state": copy.deepcopy(self.state if state is None else state),
            "bootstrap_core": copy.deepcopy(self.core),
            "bootstrap_manifest": copy.deepcopy(self.sealed),
            "descriptor_reference": copy.deepcopy(self.descriptor_ref),
        }

    def approval_verifier(self, core, sealed):
        sql.require(core == self.core and sealed == self.sealed, "TRUST_UNAPPROVED")

    def trust_verifier(self, pins, root_ref):
        sql.require(pins == [self.pin] and root_ref == self.trust_ref, "TRUST_UNAPPROVED")

    def trust(self):
        return {
            "pins": [self.pin],
            "floor": self.floor,
            "genesis_digest": sql.digest(self.receipt),
            "certified_source_ids": {"synthetic"},
            "approval_verifier": self.approval_verifier,
            "trust_root_reference": self.trust_ref,
            "trust_verifier": self.trust_verifier,
            "intent_payload_reader": self.read_payload,
        }

    def read_payload(self, path):
        candidate = Path(path)
        sql.require(
            candidate.parent == self.root
            and candidate.is_file()
            and not candidate.is_symlink()
            and candidate.stat().st_mode & 0o777 == 0o600,
            "UNSAFE_PATH",
        )
        return candidate.read_bytes()

    def observation(self, state=None):
        current = self.state if state is None else state
        cp = max(current["witness_checkpoints"], key=lambda r: r["revision"])
        fields = {
            "witness_schema_version": wit.HEAD_SCHEMA,
            "authority_id": AUTH_ID,
            "authority_epoch": 1,
            "namespace": "S11C3-EX10",
            "challenge": hashed("synthetic-challenge"),
            "revision": cp["revision"],
            "ledger_digest": cp["ledger_digest"],
            "witness_digest": cp["witness_digest"],
            "issued_at": timestamp(),
            "expires_at": timestamp(30),
            "key_id": "synthetic-key",
            "key_epoch": 1,
            "key_fingerprint": self.pin.record["fingerprint"],
        }
        # Exact duration must be <=240 seconds even if the calls cross a microsecond.
        expires = sql.utc_time(fields["issued_at"]) + timedelta(seconds=240)
        fields["expires_at"] = expires.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        return {
            "head": wit._sign_current_head(fields, self.key),
            "challenge": fields["challenge"],
            "now": timestamp(),
            "request_elapsed_seconds": 0,
            "clock_healthy": True,
        }

    def barrier(self, authority_id, epoch, revision, tip):
        sql.require(
            authority_id == AUTH_ID and epoch == 1 and type(revision) is int and len(tip) == 64,
            "B4_BARRIER_UNVERIFIED",
        )

    def verify(
        self,
        state=None,
        material=None,
        trust=None,
        observation=None,
        profile=None,
        session=None,
        barrier=None,
    ):
        value = self.material(state) if material is None else material
        snapshot = history.AuthoritySnapshot(sql.canonical_bytes(value))
        return history.verify_authority(
            snapshot,
            self.trust() if trust is None else trust,
            self.observation(value["state"]) if observation is None else observation,
            self.descriptor_ref if profile is None else profile,
            self.descriptor_ref if session is None else session,
            self.barrier if barrier is None else barrier,
        )

    def store(self):
        self.con = sql._create_test_database(self.database)
        self.con.execute("BEGIN")
        sql._insert_rows(self.con, self.state)
        self.con.commit()
        self.con.close()

    def next_run(self, kind="ACTIVE"):
        run = copy.deepcopy(self.state["official_runs"][0])
        run.update(run_version=1, previous_run_version=0, introduced_revision=1)
        if kind == "ACTIVE":
            run.update(
                lifecycle_state="ACTIVE",
                phase="EXECUTING",
                result=None,
                binding=False,
                ack_required=False,
                validity_classification="PENDING",
                finalized_at=None,
            )
        elif kind == "UNCERTAIN":
            run.update(
                lifecycle_state="UNCERTAIN",
                phase="UNCERTAIN",
                result=None,
                binding=False,
                ack_required=False,
                validity_classification="UNCERTAIN",
                finalized_at=None,
            )
        else:
            nd = kind == "ND"
            run.update(
                lifecycle_state="TERMINAL",
                phase="FINALIZED",
                result="NOT_DEMONSTRATED" if nd else kind,
                binding=not nd,
                ack_required=nd,
                validity_classification="NON_BINDING_ND" if nd else "VALID_BINDING",
                finalized_at=NOW,
            )
        return run

    def publication_args(self, adapter, action="RUN_STATE"):
        capability = {
            "issuer": "synthetic-test-only",
            "authority_id": AUTH_ID,
            "epoch": 1,
            "operation_id": OP_NEXT,
            "expected_revision": 1,
            "action": action,
            "fence": 1,
        }
        return {
            "operation_id": OP_NEXT,
            "occurred_at": NOW,
            "evidence_reference_id": self.ref_ids["evidence"],
            "verifier": TestSerializationVerifier(),
            "capability": capability,
            "adapter": adapter,
            "signer_identity": self.pin,
            "candidate_payload_path": str(self.root / "next-payload.json"),
        }


class TestSerializationVerifier:
    __test__ = False

    def verify(
        self, *, authority_id, epoch, operation_id, revision, action, proof, candidate_state
    ):
        state = sql.parse_canonical(candidate_state)
        sql.require(
            type(proof) is dict
            and proof
            == {
                "issuer": "synthetic-test-only",
                "authority_id": authority_id,
                "epoch": epoch,
                "operation_id": operation_id,
                "expected_revision": revision,
                "action": action,
                "fence": 1,
            },
            "B4_CAPABILITY_REQUIRED",
        )
        sql.require(state["metadata"][0]["authority_id"] == authority_id, "B4_CAPABILITY_REQUIRED")


class TestPublicationAdapter:
    __test__ = False

    def __init__(self, fixture, fail=None):
        self.fixture, self.fail = fixture, fail
        self.calls = []
        self.receipt = None

    def durable_intent(self, intent, candidate_payload):
        self.calls.append("intent")
        path = Path(intent["candidate_payload_path"])
        with path.open("xb") as output:
            output.write(candidate_payload)
        path.chmod(0o600)
        con = _test_connection(self.fixture.database)
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA synchronous=EXTRA")
        sql._insert_rows(con, {"publication_intents": [intent]})
        con.commit()
        con.close()

    def publish_witness(self, payload, expected_previous):
        self.calls.append("witness")
        assert expected_previous == sql.digest(self.fixture.receipt)
        self.receipt = wit.sign_witness(payload, self.fixture.key)
        if self.fail == "response":
            raise RuntimeError("synthetic-response-loss")
        return self.receipt

    def observe_head(self, receipt):
        current = copy.deepcopy(self.fixture.state)
        current["witness_checkpoints"].append(history._checkpoint(receipt))
        return self.fixture.observation(current)

    def atomic_commit(self, additions):
        self.calls.append("commit")
        if self.fail == "commit":
            raise RuntimeError("synthetic-local-commit-failure")
        con = sqlite3.connect(self.fixture.database)
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA synchronous=EXTRA")
        con.execute("BEGIN")
        proposed = copy.deepcopy(additions)
        if proposed.get("publication_intents"):
            present = sql._read_state(con)["publication_intents"]
            assert proposed["publication_intents"][0] in present
            proposed["publication_intents"] = []
        sql._insert_rows(con, proposed)
        con.commit()
        con.close()


@pytest.fixture
def factory():
    base = os.environ["S11_B3_TEST_BASE"]
    roots = []

    def make(kinds=("ND",), create_db=False):
        temporary = tempfile.TemporaryDirectory(prefix="s11-b3-test-", dir=base)
        roots.append(temporary)
        root = Path(temporary.name)
        root.chmod(0o700)
        return Fixture(root, kinds, create_db)

    yield make
    for root in reversed(roots):
        root.cleanup()


def rejection(call):
    with pytest.raises(sql.AuthorityError):
        call()


def successor(fixture, kind="ACTIVE", adapter=None):
    run = fixture.next_run(kind)
    event_kind = {"ACTIVE": "RUN_STATE", "UNCERTAIN": "RUN_UNCERTAIN"}.get(kind, "RUN_TERMINAL")
    event = fixture.event(
        event_kind, sequence=len(fixture.state["authority_events"]) + 1, run=run, revision=1
    )
    adapter = adapter or TestPublicationAdapter(fixture)
    method = {
        "ACTIVE": history._append_run_state,
        "UNCERTAIN": history._append_uncertain_state,
    }.get(kind, history._append_run_terminal)
    return method(
        fixture.verify(),
        run,
        event,
        **fixture.publication_args(adapter, event_kind if kind != "UNCERTAIN" else "UNCERTAINTY"),
    )


PLAN_CASES = [
    "valid_genesis",
    "duplicate_run_version",
    "unknown_enum",
    "null_required_timestamp",
    "invalid_type",
    "broken_fk",
    "revision_gap",
    "revision_reuse",
    "run_transition",
    "terminal_successor",
    "schema_tampering",
    "delete_historical_version",
    "complete",
    "missing_source",
    "unreadable_source",
    "contradictory_source",
    "list_not_coverage",
    "uncoordinated_writer",
    "orphan_object",
    "source_changed",
    "copy_deduplication",
    "separate_approval",
    "unknown_historical_fields",
    "active_history_complete",
    "canonical_stability",
    "all_run_versions",
    "logical_event_change",
    "physical_relayout",
    "self_reference",
    "canonical_reject",
    "prefix_digest",
    "extra_ref_change",
    "valid_receipt",
    "wrong_key",
    "altered_digest",
    "altered_revision",
    "broken_previous",
    "genesis_binding",
    "rollback20to15",
    "same_revision_fork",
    "nonce_replay",
    "witness_offline",
    "key_rotation",
    "ledger_self_key",
    "truststate_corollback",
    "remote_state_lost",
    "nd_not_binding",
    "pass_binding",
    "fail_binding",
    "empty_complete",
    "open_active",
    "uncertain",
    "blocker_precedence",
    "two_terminal",
    "two_binding_runs",
    "ack_exact",
    "reference_mismatch",
    "minimum_vs_current",
    "capability_required",
    "intent_barrier",
    "remote_publish_local_fail",
    "response_loss",
    "manual_cancel",
    "valid_backup_restore",
    "stale_restore",
    "hot_journal",
    "atomic_bootstrap_crash",
    "v1_bytes",
]


def append_open_run(f):
    run = f.run("OPEN", OTHER_RUN)
    run.update(
        provenance_model="PROFILE_V2",
        profile_reference_id=f.ref_ids["profile"],
        introduced_revision=1,
    )
    event = f.event(
        "RUN_OPENED", sequence=len(f.state["authority_events"]) + 1, run=run, revision=1
    )
    return history._append_run_opened(
        f.verify(),
        run,
        event,
        governance_certificate=None,
        governance_certificate_reference=None,
        reference_additions=(),
        **f.publication_args(TestPublicationAdapter(f), "RUN_OPENED"),
    )


def pending_state(f, operation_id=OP_NEXT):
    state = copy.deepcopy(f.state)
    head = len(state["history_revisions"]) - 1
    next_revision = head + 1
    candidate = copy.deepcopy(state)
    revision = {
        "revision": next_revision,
        "operation_id": operation_id,
        "previous_ledger_digest": max(state["history_revisions"], key=lambda r: r["revision"])[
            "ledger_digest"
        ],
        "ledger_digest": "0" * 64,
        "recorded_at": NOW,
        "first_event_sequence": len(state["authority_events"]) + 1,
        "last_event_sequence": len(state["authority_events"]) + 1,
        "event_count": 1,
    }
    candidate["authority_events"].append(
        f.event(
            "AUTHORITY_UNCERTAIN",
            sequence=revision["first_event_sequence"],
            run=None,
            revision=next_revision,
        )
    )
    candidate["history_revisions"].append(revision)
    revision["ledger_digest"] = sql.logical_digest(candidate, next_revision)
    payload = f.root / "pending.json"
    payload.write_bytes(sql.canonical_bytes(sql.logical_projection(candidate, next_revision)))
    payload.chmod(0o600)
    state["publication_intents"].append(
        {
            "operation_id": operation_id,
            "base_revision": head,
            "candidate_revision": next_revision,
            "base_ledger_digest": max(state["history_revisions"], key=lambda r: r["revision"])[
                "ledger_digest"
            ],
            "candidate_ledger_digest": revision["ledger_digest"],
            "candidate_payload_path": str(payload),
            "candidate_payload_sha256": revision["ledger_digest"],
            "serialization_capability_digest": hashed("capability"),
            "created_at": NOW,
        }
    )
    return state


@pytest.mark.parametrize("case", PLAN_CASES)
def test_b3_plan(factory, case):
    if case == "valid_genesis":
        f = factory(create_db=True)
        assert sql.validate_state(f.state)[RUN_ID]["result"] == "NOT_DEMONSTRATED"
        verified = f.verify(sql._load_database(f.database))
        assert history.current_revision(verified)["revision"] == 0
        con = sql._connect_readonly(f.database)
        assert con.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert con.execute("PRAGMA synchronous").fetchone()[0] == 3
        assert len(sql.TABLES) == 11
        con.close()
    elif case == "duplicate_run_version":
        f = factory()
        state = copy.deepcopy(f.state)
        duplicate = copy.deepcopy(state["official_runs"][0])
        duplicate["source_revision"] = "4" * 40
        state["official_runs"].append(duplicate)
        rejection(lambda: sql.validate_state(state))
    elif case == "unknown_enum":
        f = factory()
        row = copy.deepcopy(f.state["official_runs"][0])
        row["lifecycle_state"] = "UNKNOWN"
        rejection(lambda: sql.validate_row("official_runs", row))
    elif case == "null_required_timestamp":
        f = factory()
        row = copy.deepcopy(f.state["official_runs"][0])
        row["opened_at"] = None
        rejection(lambda: sql.validate_row("official_runs", row))
    elif case == "invalid_type":
        f = factory()
        row = copy.deepcopy(f.state["history_revisions"][0])
        row["revision"] = True
        rejection(lambda: sql.validate_row("history_revisions", row))
    elif case == "broken_fk":
        f = factory()
        state = copy.deepcopy(f.state)
        state["official_runs"][0]["evidence_authority_reference_id"] = "f" * 64
        rejection(lambda: sql.validate_state(state))
    elif case == "revision_gap":
        f = factory()
        state = copy.deepcopy(f.state)
        state["history_revisions"][0]["revision"] = 1
        state["history_revisions"][0]["previous_ledger_digest"] = "a" * 64
        rejection(lambda: sql.validate_state(state))
    elif case == "revision_reuse":
        f = factory()
        state = copy.deepcopy(f.state)
        state["history_revisions"].append(copy.deepcopy(state["history_revisions"][0]))
        rejection(lambda: sql.validate_state(state))
    elif case == "run_transition":
        f = factory(("OPEN",), create_db=True)
        result = successor(f)
        assert history.find_run(result, RUN_ID)["lifecycle_state"] == "ACTIVE"
        assert history.current_revision(result)["revision"] == 1
        assert len(result.material["state"]["official_runs"]) == 2
        assert sql._load_database(f.database) == sql._ordered_state(
            result.material["state"]
        ) or sql._ordered_state(sql._load_database(f.database)) == sql._ordered_state(
            result.material["state"]
        )
    elif case == "terminal_successor":
        f = factory(create_db=True)
        rejection(lambda: successor(f))
        assert sql._load_database(f.database)["history_revisions"] == f.state["history_revisions"]
    elif case == "schema_tampering":
        f = factory(create_db=True)
        con = sqlite3.connect(f.database)
        con.execute("PRAGMA synchronous=EXTRA")
        con.execute("DROP TRIGGER reject_update_metadata")
        con.close()
        rejection(lambda: sql._load_database(f.database))
    elif case == "delete_historical_version":
        f = factory(("OPEN",), create_db=True)
        state = successor(f).material["state"]
        state["official_runs"] = state["official_runs"][1:]
        rejection(lambda: sql.validate_state(state))
    elif case == "complete":
        f = factory()
        candidate = history._build_bootstrap_candidate(f.core, certified_source_ids={"synthetic"})
        assert candidate["completeness"] == "COMPLETE"
        assert f.core["completeness"] == "COMPLETE"
    elif case in ("missing_source", "list_not_coverage", "uncoordinated_writer"):
        f = factory()
        expected = {"synthetic", "missing"} if case == "missing_source" else set()
        assert (
            history._build_bootstrap_candidate(f.core, certified_source_ids=expected)[
                "completeness"
            ]
            == "INCOMPLETE"
        )
    elif case == "unreadable_source":
        f = factory()
        core = copy.deepcopy(f.core)
        core["sources"][0].update(status="UNREADABLE", diagnostic_codes=["READ_DENIED"])
        core["unreadable_source_ids"] = ["synthetic"]
        assert (
            history._build_bootstrap_candidate(core, certified_source_ids={"synthetic"})[
                "completeness"
            ]
            == "UNREADABLE"
        )
    elif case == "contradictory_source":
        f = factory()
        core = copy.deepcopy(f.core)
        core["run_findings"][0]["contradiction_reference"] = reference("conflict")
        assert (
            history._build_bootstrap_candidate(core, certified_source_ids={"synthetic"})[
                "completeness"
            ]
            == "CONTRADICTORY"
        )
    elif case == "orphan_object":
        f = factory()
        core = copy.deepcopy(f.core)
        core["sources"][0]["run_ids"].append(OTHER_RUN)
        core["sources"][0]["run_ids"].sort()
        assert (
            history._build_bootstrap_candidate(core, certified_source_ids={"synthetic"})[
                "completeness"
            ]
            == "INCOMPLETE"
        )
    elif case == "source_changed":
        f = factory()
        core = copy.deepcopy(f.core)
        core["sources"][0].update(status="BLOCKED", diagnostic_codes=["SOURCE_CHANGED"])
        assert (
            history._build_bootstrap_candidate(core, certified_source_ids={"synthetic"})[
                "completeness"
            ]
            == "INCOMPLETE"
        )
    elif case == "copy_deduplication":
        f = factory()
        core = copy.deepcopy(f.core)
        copied = copy.deepcopy(core["sources"][0])
        copied.update(source_id="copy", location="/synthetic-fixture/copy")
        core["sources"].insert(0, copied)
        core["source_count"] = 2
        core["run_findings"][0]["source_ids"] = ["copy", "synthetic"]
        result = history._build_bootstrap_candidate(
            core, certified_source_ids={"copy", "synthetic"}
        )
        assert result["completeness"] == "COMPLETE" and result["run_count"] == 1
    elif case == "separate_approval":
        f = factory()
        rejection(
            lambda: history._validate_sealed_bootstrap(
                f.core, f.sealed, certified_source_ids={"synthetic"}, approval_verifier=None
            )
        )
    elif case == "unknown_historical_fields":
        f = factory()
        row = copy.deepcopy(f.state["official_runs"][0])
        row["created_at"] = None
        rejection(lambda: sql.validate_row("official_runs", row))
    elif case == "active_history_complete":
        f = factory(("OPEN",))
        assert (
            history._build_bootstrap_candidate(f.core, certified_source_ids={"synthetic"})[
                "completeness"
            ]
            == "COMPLETE"
        )
        assert history.first_valid_status(f.verify())["status"] == "ACTIVE_BLOCKER"
    elif case == "canonical_stability":
        f = factory()
        reverse = copy.deepcopy(f.state)
        for rows in reverse.values():
            rows.reverse()
        assert sql.logical_digest(reverse, 0) == sql.logical_digest(f.state, 0)
        assert sql.canonical_bytes({"a": 1, "b": "á"}) == sql.canonical_bytes({"b": "á", "a": 1})
    elif case == "all_run_versions":
        f = factory()
        altered = copy.deepcopy(f.state)
        altered["official_runs"][0]["source_revision"] = "4" * 40
        assert sql.logical_digest(altered, 0) != sql.logical_digest(f.state, 0)
    elif case == "logical_event_change":
        f = factory()
        altered = copy.deepcopy(f.state)
        altered["authority_events"][0]["reason_code"] = "CHANGED"
        assert sql.logical_digest(altered, 0) != sql.logical_digest(f.state, 0)
    elif case == "physical_relayout":
        f = factory(create_db=True)
        before = sql.logical_digest(sql._load_database(f.database), 0)
        con = sqlite3.connect(f.database)
        con.execute("PRAGMA synchronous=EXTRA")
        con.execute("PRAGMA page_size=8192")
        con.execute("VACUUM")
        con.close()
        assert sql.logical_digest(sql._load_database(f.database), 0) == before
    elif case == "self_reference":
        f = factory()
        altered = copy.deepcopy(f.state)
        altered["witness_checkpoints"][0]["signature"] = "0" * 128
        assert sql.logical_digest(altered, 0) == sql.logical_digest(f.state, 0)
        rejection(lambda: f.verify(altered))
    elif case == "canonical_reject":
        for value in ({"f": float("nan")}, {"f": 1.5}, {"f": "a\u0301"}):
            rejection(lambda v=value: sql.canonical_bytes(v))
        rejection(lambda: sql.parse_canonical(b'{"a":1,"a":2}\n'))
    elif case == "prefix_digest":
        f = factory(("OPEN",), create_db=True)
        state = successor(f).material["state"]
        state["history_revisions"][1]["previous_ledger_digest"] = "f" * 64
        rejection(lambda: sql.validate_state(state))
    elif case == "extra_ref_change":
        f = factory()
        changed = copy.deepcopy(f.state)
        changed["artifact_references"][0]["path"] = "/synthetic-fixture/changed"
        assert sql.logical_digest(changed, 0) != sql.logical_digest(f.state, 0)
        rejection(lambda: sql.validate_state(changed))
    elif case == "valid_receipt":
        f = factory()
        assert wit.verify_witness(f.receipt, f.pin) == sql.digest(f.receipt)
    elif case == "wrong_key":
        f, other = factory(), factory()
        rejection(lambda: wit.verify_witness(f.receipt, other.pin))
    elif case in ("altered_digest", "altered_revision", "broken_previous"):
        f = factory()
        record = copy.deepcopy(f.receipt)
        if case == "altered_digest":
            record["ledger_digest"] = hashed("altered")
        elif case == "altered_revision":
            record.update(revision=1, genesis=None, previous_witness_digest=hashed("previous"))
        else:
            record["previous_witness_digest"] = hashed("broken")
        rejection(lambda: wit.verify_witness(record, f.pin))
    elif case == "genesis_binding":
        f = factory()
        wrong = copy.deepcopy(f.receipt["genesis"])
        wrong["source_baseline_head"] = "f" * 40
        rejection(
            lambda: wit.verify_chain(
                [f.receipt],
                [f.pin],
                expected_genesis_digest=sql.digest(f.receipt),
                expected_genesis_binding=wrong,
            )
        )
    elif case == "rollback20to15":
        f = factory()
        head = f.observation()["head"]
        floor = wit.TrustedFloor(AUTH_ID, 1, 20, hashed("rev20"))
        rejection(
            lambda: wit._verify_floor(
                15, f.receipt["ledger_digest"], sql.digest(f.receipt), [f.receipt], head, floor
            )
        )
    elif case == "same_revision_fork":
        f = factory()
        fork = copy.deepcopy(f.receipt)
        fork.pop("signature")
        fork["ledger_digest"] = hashed("fork")
        fork = wit.sign_witness(fork, f.key)
        rejection(
            lambda: wit.verify_chain(
                [f.receipt, fork],
                [f.pin],
                expected_genesis_digest=sql.digest(f.receipt),
                expected_genesis_binding=f.receipt["genesis"],
            )
        )
    elif case == "nonce_replay":
        f = factory()
        observation = f.observation()
        rejection(
            lambda: wit._verify_current_head(
                observation["head"],
                f.pin,
                challenge=hashed("other-nonce"),
                now=observation["now"],
                request_elapsed_seconds=0,
                clock_healthy=True,
            )
        )
    elif case == "witness_offline":
        f = factory()
        observation = f.observation()
        observation["clock_healthy"] = False
        rejection(lambda: f.verify(observation=observation))
    elif case == "key_rotation":
        f = factory()
        key = Ed25519PrivateKey.generate()
        pin = f.pin.record
        raw = key.public_key().public_bytes_raw()
        pin.update(
            key_id="synthetic-next",
            key_epoch=2,
            public_key_hex=raw.hex(),
            fingerprint=hashlib.sha256(raw).hexdigest(),
            activation_revision=1,
            key_transition_reference=reference("approved-rotation"),
        )
        pin = wit.TrustPin(sql.canonical_bytes(pin))
        payload = copy.deepcopy(f.receipt)
        payload.pop("signature")
        payload.update(
            revision=1,
            operation_id=OP_NEXT,
            previous_witness_digest=sql.digest(f.receipt),
            genesis=None,
            key_id="synthetic-next",
            key_epoch=2,
            key_fingerprint=pin.record["fingerprint"],
            ledger_digest=hashed("rev1"),
        )
        record = wit.sign_witness(payload, key)
        assert wit.verify_chain(
            [f.receipt, record],
            [f.pin, pin],
            expected_genesis_digest=sql.digest(f.receipt),
            expected_genesis_binding=f.receipt["genesis"],
        ) == sql.digest(record)
    elif case == "ledger_self_key":
        f = factory()
        record = copy.deepcopy(f.receipt)
        record["public_key_hex"] = f.pin.record["public_key_hex"]
        rejection(lambda: wit.verify_witness(record, f.pin))
    elif case == "truststate_corollback":
        f = factory()
        head = f.observation()["head"]
        head["revision"] = 1
        rejection(
            lambda: wit._verify_floor(
                0, f.receipt["ledger_digest"], sql.digest(f.receipt), [f.receipt], head, f.floor
            )
        )
    elif case == "remote_state_lost":
        f = factory()
        trust = f.trust()
        trust["floor"] = None
        rejection(lambda: f.verify(trust=trust))
    elif case in (
        "nd_not_binding",
        "pass_binding",
        "fail_binding",
        "empty_complete",
        "open_active",
        "uncertain",
    ):
        kinds = {
            "nd_not_binding": ("ND",),
            "pass_binding": ("PASS",),
            "fail_binding": ("FAIL",),
            "empty_complete": (),
            "open_active": ("OPEN", "ACTIVE"),
            "uncertain": ("UNCERTAIN",),
        }[case]
        status = {
            "nd_not_binding": "NONE",
            "pass_binding": "PASS_BINDING",
            "fail_binding": "FAIL_BINDING",
            "empty_complete": "NONE",
            "open_active": "ACTIVE_BLOCKER",
            "uncertain": "UNCERTAIN_BLOCKER",
        }[case]
        f = factory(kinds)
        actual = history.first_valid_status(f.verify())
        assert actual["status"] == status and actual["consumed"] is (
            case in ("pass_binding", "fail_binding")
        )
    elif case == "blocker_precedence":
        f = factory(("PASS", "UNCERTAIN"))
        actual = history.first_valid_status(f.verify())
        assert (
            actual["status"] == "UNCERTAIN_BLOCKER"
            and actual["consumed"] is True
            and actual["blocking_run_ids"] == [OTHER_RUN]
        )
    elif case == "two_terminal":
        f = factory()
        changed = copy.deepcopy(f.state)
        conflicting = copy.deepcopy(changed["official_runs"][0])
        conflicting.update(
            result="PASS", binding=True, ack_required=False, validity_classification="VALID_BINDING"
        )
        changed["official_runs"].append(conflicting)
        rejection(lambda: sql.validate_state(changed))
    elif case == "two_binding_runs":
        f = factory(("PASS", "FAIL"))
        rejection(f.verify)
    elif case == "ack_exact":
        f = factory()
        verified = f.verify()
        facts = history.verify_ack_target(verified, RUN_ID)
        assert (
            facts["recognizable"] is True
            and facts["ack_required"] is True
            and facts["binding"] is False
        )
        assert history.verify_ack_target(verified, OTHER_RUN)["recognizable"] is False
        rejection(lambda: history.verify_ack_target(verified, RUN_ID.upper()))
        assert not any(
            "ack_consumed" in column or "acknowledged" in column
            for table in sql.TABLES.values()
            for column in table["columns"]
        )
    elif case == "reference_mismatch":
        f = factory()
        ref = copy.deepcopy(f.descriptor_ref)
        ref["path"] = "/synthetic-fixture/wrong"
        rejection(lambda: f.verify(session=ref))
    elif case == "minimum_vs_current":
        f = factory()
        observation = f.observation()
        head = copy.deepcopy(observation["head"])
        head.pop("signature")
        head.update(
            revision=1, ledger_digest=hashed("remote-next"), witness_digest=hashed("remote-tip")
        )
        observation["head"] = wit._sign_current_head(head, f.key)
        rejection(lambda: f.verify(observation=observation))
        assert history.current_revision(f.verify())["revision"] == 0
    elif case == "capability_required":
        f = factory(("OPEN",), create_db=True)
        run = f.next_run()
        event = f.event("RUN_STATE", sequence=3, run=run, revision=1)
        args = f.publication_args(TestPublicationAdapter(f))
        args["capability"] = True
        rejection(lambda: history._append_run_state(f.verify(), run, event, **args))
        assert sql._load_database(f.database)["history_revisions"] == f.state["history_revisions"]
    elif case == "intent_barrier":
        f = factory()
        verified = f.verify(pending_state(f))
        assert history.first_valid_status(verified)["status"] == "UNCERTAIN_BLOCKER"
    elif case in ("remote_publish_local_fail", "response_loss"):
        f = factory(("OPEN",), create_db=True)
        adapter = TestPublicationAdapter(
            f, "commit" if case == "remote_publish_local_fail" else "response"
        )
        with pytest.raises(RuntimeError, match="synthetic-"):
            successor(f, adapter=adapter)
        assert adapter.calls.count("witness") == 1
        current = sql._load_database(f.database)
        assert len(current["publication_intents"]) == 2 and len(current["history_revisions"]) == 1
    elif case == "manual_cancel":
        f = factory(create_db=True)
        state = pending_state(f)
        con = sqlite3.connect(f.database)
        con.execute("PRAGMA synchronous=EXTRA")
        sql._insert_rows(con, {"publication_intents": [state["publication_intents"][-1]]})
        con.commit()
        con.close()
        verified = f.verify(state)
        resolution = {
            "operation_id": OP_NEXT,
            "disposition": "MANUAL_CANCELLED",
            "resolved_at": NOW,
            "committed_revision": None,
            "receipt_digest": None,
            "operator_approval_path": f.refs["approval"]["path"],
            "operator_approval_sha256": f.refs["approval"]["sha256"],
            "operator_approval_schema": f.refs["approval"]["schema_version"],
            "fresh_witness_observation_digest": sql.digest(
                sql.parse_canonical(verified.canonical_current_head)
            ),
        }
        args = f.publication_args(TestPublicationAdapter(f), "MANUAL_CANCELLED")
        args["capability"]["expected_revision"] = 0
        result = history._append_manual_resolution(
            verified,
            {"publication_resolutions": [resolution]},
            operator_approval_reference=f.refs["approval"],
            resolution_verifier=lambda *_: None,
            **args,
        )
        assert (
            result.write_closed
            and history.first_valid_status(result)["status"] == "UNCERTAIN_BLOCKER"
        )
        rejection(lambda: append_open_run_from_verified(f, result))
    elif case == "valid_backup_restore":
        f = factory(create_db=True)
        verified = f.verify(sql._load_database(f.database))
        target = f.root / "backup.sqlite3"
        manifest = sql._backup_test_database(f.database, target, verified_source=verified)
        assert manifest["ledger_digest"] == history.current_revision(verified)["ledger_digest"]
        restored = history._validate_restore_candidate(
            history.AuthoritySnapshot(sql.canonical_bytes(f.material(sql._load_database(target)))),
            f.trust(),
            f.observation(),
            f.descriptor_ref,
            f.descriptor_ref,
            f.barrier,
            reference("restore-approval"),
            lambda *_: None,
        )
        assert history.current_revision(restored)["revision"] == 0
    elif case == "stale_restore":
        f = factory()
        trust = f.trust()
        trust["floor"] = wit.TrustedFloor(AUTH_ID, 1, 1, hashed("next-tip"))
        rejection(
            lambda: history._validate_restore_candidate(
                history.AuthoritySnapshot(sql.canonical_bytes(f.material())),
                trust,
                f.observation(),
                f.descriptor_ref,
                f.descriptor_ref,
                f.barrier,
                reference("restore-approval"),
                lambda *_: None,
            )
        )
    elif case == "hot_journal":
        f = factory(create_db=True)
        journal = Path(str(f.database) + "-journal")
        journal.write_bytes(b"synthetic-preserved-journal")
        journal.chmod(0o600)
        rejection(lambda: sql._load_database(f.database))
        assert journal.read_bytes() == b"synthetic-preserved-journal"
    elif case == "atomic_bootstrap_crash":
        f = factory(create_db=True)
        initial = sql.empty_state()
        additions = {
            name: copy.deepcopy(rows)
            for name, rows in f.state.items()
            if name
            not in (
                "history_revisions",
                "witness_checkpoints",
                "publication_intents",
                "publication_resolutions",
            )
        }

        class AbortBeforePublication:
            def durable_intent(self, *_):
                raise RuntimeError("synthetic-bootstrap-boundary")

        capability = {
            "issuer": "synthetic-test-only",
            "authority_id": AUTH_ID,
            "epoch": 1,
            "operation_id": OP_ZERO,
            "expected_revision": 0,
            "action": "GENESIS",
            "fence": 1,
        }
        with pytest.raises(RuntimeError, match="synthetic-bootstrap-boundary"):
            history._bootstrap_authority(
                initial,
                additions,
                core=f.core,
                sealed=f.sealed,
                descriptor=f.descriptor,
                descriptor_reference=f.descriptor_ref,
                trust=f.trust(),
                certified_source_ids={"synthetic"},
                approval_verifier=f.approval_verifier,
                operation_id=OP_ZERO,
                occurred_at=NOW,
                evidence_reference_id=f.ref_ids["evidence"],
                verifier=TestSerializationVerifier(),
                capability=capability,
                adapter=AbortBeforePublication(),
                signer_identity=f.pin,
                candidate_payload_path=str(f.root / "bootstrap-candidate.json"),
            )
        assert initial == sql.empty_state()
    elif case == "v1_bytes":
        expected = {
            "ex10_runtime_profile.py": (
                "d69703013e8896a2b16ace12535ec9b2d96f4ea221627ddadba51cd448a" "08662"
            ),
            "ex10_session_v2.py": (
                "00bb55d559e7b677b80e4083034a099f905a78b4d6b58dc64c39669f559" "05b98"
            ),
        }
        for name, sha in expected.items():
            assert hashlib.sha256((APP / name).read_bytes()).hexdigest() == sha
    else:
        raise AssertionError("unimplemented planned case: " + case)


def append_open_run_from_verified(f, verified):
    run = f.run("OPEN", OTHER_RUN)
    run.update(
        provenance_model="PROFILE_V2",
        profile_reference_id=f.ref_ids["profile"],
        introduced_revision=1,
    )
    event = f.event(
        "RUN_OPENED", sequence=len(f.state["authority_events"]) + 1, run=run, revision=1
    )
    return history._append_run_opened(
        verified,
        run,
        event,
        governance_certificate=None,
        governance_certificate_reference=None,
        reference_additions=(),
        **f.publication_args(TestPublicationAdapter(f), "RUN_OPENED"),
    )


@pytest.mark.parametrize("seed", range(8))
def test_property_digest_deterministic(factory, seed):
    f = factory()
    reordered = copy.deepcopy(f.state)
    for rows in reordered.values():
        if rows:
            shift = seed % len(rows)
            rows[:] = rows[shift:] + rows[:shift]
            for i, row in enumerate(rows):
                rows[i] = dict(reversed(list(row.items())))
    assert sql.logical_digest(reordered, 0) == sql.logical_digest(f.state, 0)


@pytest.mark.parametrize("layout", [4096, 8192, 16384])
def test_property_sqlite_layout_irrelevant(factory, layout):
    f = factory(create_db=True)
    before = history.current_revision(f.verify())["ledger_digest"]
    con = sqlite3.connect(f.database)
    con.execute("PRAGMA synchronous=EXTRA")
    con.execute("PRAGMA page_size=" + str(layout))
    con.execute("VACUUM")
    con.close()
    assert (
        history.current_revision(f.verify(sql._load_database(f.database)))["ledger_digest"]
        == before
    )


@pytest.mark.parametrize("kind", ["ACTIVE", "UNCERTAIN", "ND", "PASS", "FAIL"])
def test_property_revision_monotonic(factory, kind):
    f = factory(("OPEN",), create_db=True)
    updated = successor(f, kind)
    assert (
        history.current_revision(updated)["revision"]
        == history.current_revision(f.verify())["revision"] + 1
    )
    assert [r["revision"] for r in updated.material["state"]["history_revisions"]] == [0, 1]


@pytest.mark.parametrize("seed", range(4))
def test_property_witness_deterministic(factory, seed):
    f = factory()
    payload = {k: v for k, v in f.receipt.items() if k != "signature"}
    payload["ledger_digest"] = hashed("deterministic-" + str(seed))
    assert wit.sign_witness(payload, f.key) == wit.sign_witness(payload, f.key)
    assert wit.verify_witness(wit.sign_witness(payload, f.key), f.pin)


def tree_fingerprint(root):
    return {
        str(path.relative_to(root)): (
            path.lstat().st_ino,
            path.lstat().st_size,
            path.lstat().st_mtime_ns,
            path.lstat().st_ctime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in root.iterdir()
        if path.is_file()
    }


def test_property_read_api_no_writes(factory):
    f = factory(create_db=True)
    before = tree_fingerprint(f.root)
    loaded = history.load_authority(
        f.descriptor_ref,
        f.trust(),
        lambda _: f.material(sql._load_database(f.database)),
        f.observation(),
    )
    verified = history.verify_authority(
        loaded, f.trust(), f.observation(), f.descriptor_ref, f.descriptor_ref, f.barrier
    )
    history.current_revision(verified)
    history.list_official_runs(verified)
    history.find_run(verified, RUN_ID)
    history.first_valid_status(verified)
    history.active_or_uncertain_runs(verified)
    history.verify_ack_target(verified, RUN_ID)
    assert tree_fingerprint(f.root) == before


def test_property_failed_verify_no_repair(factory):
    f = factory(create_db=True)
    before = tree_fingerprint(f.root)
    wrong = copy.deepcopy(f.descriptor_ref)
    wrong["sha256"] = hashed("wrong-ref")
    rejection(lambda: f.verify(session=wrong))
    assert tree_fingerprint(f.root) == before


def test_property_restore_validation_no_writes(factory):
    f = factory(create_db=True)
    before = tree_fingerprint(f.root)
    history._validate_restore_candidate(
        history.AuthoritySnapshot(sql.canonical_bytes(f.material(sql._load_database(f.database)))),
        f.trust(),
        f.observation(),
        f.descriptor_ref,
        f.descriptor_ref,
        f.barrier,
        reference("restore-approval"),
        lambda *_: None,
    )
    assert tree_fingerprint(f.root) == before


@pytest.mark.parametrize("fault", ["authority", "schema", "witness", "approval"])
def test_restore_other_rejections(factory, fault):
    f = factory()
    material = f.material()
    approval = reference("restore-approval")
    if fault == "authority":
        material["state"]["metadata"][0]["authority_id"] = "ffffffff-eeee-dddd-cccc-bbbbbbbbbbbb"
    elif fault == "schema":
        material["descriptor"]["authority_schema_version"] = "wrong-schema"
    elif fault == "witness":
        material["state"]["witness_checkpoints"][0]["signature"] = "0" * 128
    else:
        approval = None
    snapshot = history.AuthoritySnapshot(sql.canonical_bytes(material))
    rejection(
        lambda: history._validate_restore_candidate(
            snapshot,
            f.trust(),
            f.observation(),
            f.descriptor_ref,
            f.descriptor_ref,
            f.barrier,
            approval,
            lambda *_: None,
        )
    )


@pytest.mark.parametrize("tamper", ["result", "source", "finding", "source-location"])
def test_approved_manifest_binds_exact_genesis(factory, tamper):
    f = factory()
    state = copy.deepcopy(f.state)
    if tamper == "result":
        run = state["official_runs"][0]
        run.update(
            result="PASS", binding=True, ack_required=False, validity_classification="VALID_BINDING"
        )
    elif tamper == "source":
        state["official_runs"][0]["source_revision"] = "f" * 40
    elif tamper == "finding":
        state["bootstrap_findings"][0]["finding_reference_id"] = f.ref_ids["evidence"]
    else:
        state["bootstrap_sources"][0]["location"] = "/synthetic-fixture/other"
    rejection(lambda: history._reconcile_bootstrap(state, f.core, f.sealed, f.descriptor))


def test_raw_history_not_mutator_authority(factory):
    f = factory(("OPEN",), create_db=True)
    run = f.next_run()
    event = f.event("RUN_STATE", sequence=3, run=run, revision=1)
    rejection(
        lambda: history._append_run_state(
            f.state, run, event, **f.publication_args(TestPublicationAdapter(f))
        )
    )
    assert not (f.root / "next-payload.json").exists()


@pytest.mark.parametrize("callback", ["trust", "approval", "barrier", "serialization"])
def test_boolean_is_not_verified_authority(factory, callback):
    f = factory(("OPEN",), create_db=True)
    if callback in ("trust", "approval"):
        trust = f.trust()
        trust["trust_verifier" if callback == "trust" else "approval_verifier"] = lambda *_: False
        rejection(lambda: f.verify(trust=trust))
    elif callback == "barrier":
        rejection(lambda: f.verify(barrier=lambda *_: False))
    else:

        class FalseVerifier:
            def verify(self, **_):
                return False

        args = f.publication_args(TestPublicationAdapter(f))
        args["verifier"] = FalseVerifier()
        run = f.next_run()
        event = f.event("RUN_STATE", sequence=3, run=run, revision=1)
        rejection(lambda: history._append_run_state(f.verify(), run, event, **args))


def test_expired_snapshot_blocks_queries(factory):
    f = factory()
    verified = f.verify()
    expired = history._VerifiedSnapshot(
        verified.canonical_material,
        verified.observed_at,
        verified.canonical_current_head,
        verified.canonical_trust_pins,
        NOW,
        verified.authority_uncertainty,
        verified.write_closed,
        verified.token,
    )
    assert history.first_valid_status(expired)["status"] == "HISTORY_INVALID"
    rejection(lambda: history.current_revision(expired))


def test_shuffled_revisions_return_actual_head(factory):
    f = factory(("OPEN",), create_db=True)
    state = successor(f).material["state"]
    state["history_revisions"].reverse()
    verified = f.verify(state)
    actual = history.current_revision(verified)
    assert actual["revision"] == 1 and actual["ledger_digest"] == sql.logical_digest(state, 1)


@pytest.mark.parametrize("bad", [None, True, [], {}, "not-snapshot"])
def test_history_invalid_never_defaults_to_none(bad):
    assert history.first_valid_status(bad)["status"] == "HISTORY_INVALID"


def test_sql_constraints_are_effective(factory):
    f = factory(create_db=True)
    con = sqlite3.connect(f.database)
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA synchronous=EXTRA")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE metadata SET epoch=2")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("DELETE FROM official_runs")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT OR REPLACE INTO metadata SELECT * FROM metadata")
    columns = list(sql.TABLES["official_runs"]["columns"])
    row = copy.deepcopy(f.state["official_runs"][0])
    row["run_id"] = OTHER_RUN
    row["lifecycle_state"] = "INVALID"
    values = [int(row[k]) if k in sql.BOOL_COLUMNS["official_runs"] else row[k] for k in columns]
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO official_runs ("
            + ",".join(columns)
            + ") VALUES ("
            + ",".join("?" for _ in columns)
            + ")",
            values,
        )
    con.close()


def test_readonly_sqlite_has_no_repair(factory):
    f = factory(create_db=True)
    con = sql._connect_readonly(f.database)
    with pytest.raises(sqlite3.OperationalError):
        con.execute("INSERT INTO metadata SELECT * FROM metadata")
    con.close()


def test_signer_not_approved_by_base_snapshot(factory):
    f, other = factory(("OPEN",), create_db=True), factory()
    run, event = f.next_run(), f.event("RUN_STATE", sequence=3, run=f.next_run(), revision=1)
    adapter = TestPublicationAdapter(f)
    args = f.publication_args(adapter)
    args["signer_identity"] = other.pin
    rejection(lambda: history._append_run_state(f.verify(), run, event, **args))
    assert adapter.calls == []


def test_bootstrap_is_approved_manifest_only(factory):
    f = factory()
    altered = copy.deepcopy(f.core)
    altered["run_findings"][0]["contradiction_reference"] = reference("conflict")
    rejection(
        lambda: history._validate_sealed_bootstrap(
            altered,
            f.sealed,
            certified_source_ids={"synthetic"},
            approval_verifier=f.approval_verifier,
        )
    )


def test_no_operational_factory_or_b4_api():
    assert tuple(history.__all__) == (
        "load_authority",
        "verify_authority",
        "current_revision",
        "list_official_runs",
        "find_run",
        "first_valid_status",
        "active_or_uncertain_runs",
        "verify_ack_target",
    )
    for module in (sql, wit, history):
        assert not any(
            hasattr(module, name)
            for name in ("reserve_start", "commit_open", "start_official", "global_lock")
        )
    assert not hasattr(wit, "generate_key")


@pytest.mark.parametrize(
    "path",
    [
        "relative-candidate.json",
        "/synthetic-fixture/../escape",
        "https://synthetic.invalid/x?secret=value",
    ],
)
def test_invalid_intent_path_precedes_side_effects(factory, path):
    f = factory(("OPEN",), create_db=True)
    adapter = TestPublicationAdapter(f)
    args = f.publication_args(adapter)
    args["candidate_payload_path"] = path
    run = f.next_run()
    event = f.event("RUN_STATE", sequence=3, run=run, revision=1)
    rejection(lambda: history._append_run_state(f.verify(), run, event, **args))
    assert adapter.calls == []


def test_manual_resolution_of_imported_uncertainty(factory):
    f = factory(("UNCERTAIN",), create_db=True)
    run = f.next_run("ACTIVE")
    event = f.event("MANUAL_RESOLUTION", sequence=3, run=run, revision=1, resolves=2)
    args = f.publication_args(TestPublicationAdapter(f), "MANUAL_RESOLUTION")
    updated = history._append_manual_resolution(
        f.verify(),
        {"official_runs": [run], "authority_events": [event]},
        operator_approval_reference=f.refs["approval"],
        resolution_verifier=lambda *_: None,
        **args,
    )
    assert history.find_run(updated, RUN_ID)["lifecycle_state"] == "ACTIVE"


@pytest.mark.parametrize("fault", ["missing", "hash", "base", "operation"])
def test_unverifiable_intent_is_invalid(factory, fault):
    f = factory()
    state = pending_state(f)
    intent = state["publication_intents"][-1]
    if fault == "missing":
        Path(intent["candidate_payload_path"]).unlink()
        with pytest.raises(sql.AuthorityError):
            f.verify(state)
        return
    payload = sql.parse_canonical(Path(intent["candidate_payload_path"]).read_bytes())
    if fault == "hash":
        intent["candidate_payload_sha256"] = hashed("wrong")
    else:
        payload["history_revisions"][-1][
            "previous_ledger_digest" if fault == "base" else "operation_id"
        ] = hashed("wrong") if fault == "base" else OP_ZERO
        Path(intent["candidate_payload_path"]).write_bytes(sql.canonical_bytes(payload))
        intent["candidate_payload_sha256"] = intent["candidate_ledger_digest"] = sql.digest(payload)
    rejection(lambda: f.verify(state))


def test_successful_offline_approved_bootstrap(factory):
    f = factory()
    sql._create_test_database(f.database).close()
    additions = {
        name: copy.deepcopy(rows)
        for name, rows in f.state.items()
        if name
        not in (
            "history_revisions",
            "witness_checkpoints",
            "publication_intents",
            "publication_resolutions",
        )
    }

    class GenesisAdapter(TestPublicationAdapter):
        def publish_witness(self, payload, expected_previous):
            assert expected_previous is None
            self.calls.append("witness")
            return wit.sign_witness(payload, self.fixture.key)

        def observe_head(self, receipt):
            candidate = copy.deepcopy(self.fixture.state)
            candidate["witness_checkpoints"] = [history._checkpoint(receipt)]
            return self.fixture.observation(candidate)

    capability = {
        "issuer": "synthetic-test-only",
        "authority_id": AUTH_ID,
        "epoch": 1,
        "operation_id": OP_ZERO,
        "expected_revision": 0,
        "action": "GENESIS",
        "fence": 1,
    }
    updated = history._bootstrap_authority(
        sql.empty_state(),
        additions,
        core=f.core,
        sealed=f.sealed,
        descriptor=f.descriptor,
        descriptor_reference=f.descriptor_ref,
        trust=f.trust(),
        certified_source_ids={"synthetic"},
        approval_verifier=f.approval_verifier,
        operation_id=OP_ZERO,
        occurred_at=NOW,
        evidence_reference_id=f.ref_ids["evidence"],
        verifier=TestSerializationVerifier(),
        capability=capability,
        adapter=GenesisAdapter(f),
        signer_identity=f.pin,
        candidate_payload_path=str(f.root / "approved-bootstrap.json"),
    )
    assert history.current_revision(updated)["revision"] == 0
    assert history.current_revision(f.verify(sql._load_database(f.database)))["revision"] == 0


def test_cancellation_preserves_authority_uncertainty(factory):
    f = factory(("OPEN",), create_db=True)
    event = f.event("AUTHORITY_UNCERTAIN", sequence=3, run=None, revision=1)
    updated = history._append_uncertain_state(
        f.verify(), None, event, **f.publication_args(TestPublicationAdapter(f), "UNCERTAINTY")
    )
    f.state = updated.material["state"]
    next_operation = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    state = pending_state(f, next_operation)
    con = _test_connection(f.database)
    sql._insert_rows(con, {"publication_intents": [state["publication_intents"][-1]]})
    con.commit()
    con.close()
    verified = f.verify(state)
    resolution = {
        "operation_id": next_operation,
        "disposition": "MANUAL_CANCELLED",
        "resolved_at": NOW,
        "committed_revision": None,
        "receipt_digest": None,
        "operator_approval_path": f.refs["approval"]["path"],
        "operator_approval_sha256": f.refs["approval"]["sha256"],
        "operator_approval_schema": f.refs["approval"]["schema_version"],
        "fresh_witness_observation_digest": sql.digest(
            sql.parse_canonical(verified.canonical_current_head)
        ),
    }
    args = f.publication_args(TestPublicationAdapter(f), "MANUAL_CANCELLED")
    args["operation_id"] = next_operation
    args["capability"].update(operation_id=next_operation, expected_revision=1)
    result = history._append_manual_resolution(
        verified,
        {"publication_resolutions": [resolution]},
        operator_approval_reference=f.refs["approval"],
        resolution_verifier=lambda *_: None,
        **args,
    )
    assert result.write_closed and result.authority_uncertainty
    assert history.active_or_uncertain_runs(result)["authority_uncertainty"]


def test_future_approved_signer_rejected_before_side_effects(factory):
    f = factory(("OPEN",), create_db=True)
    key = Ed25519PrivateKey.generate()
    record = f.pin.record
    public = key.public_key().public_bytes_raw()
    record.update(
        key_id="synthetic-future",
        key_epoch=2,
        activation_revision=2,
        public_key_hex=public.hex(),
        fingerprint=hashlib.sha256(public).hexdigest(),
        key_transition_reference=reference("future-approval"),
    )
    future = wit.TrustPin(sql.canonical_bytes(record))
    trust = f.trust()
    trust.update(pins=[f.pin, future], trust_verifier=lambda *_: None)
    verified = f.verify(trust=trust)
    adapter = TestPublicationAdapter(f)
    args = f.publication_args(adapter)
    args["signer_identity"] = future
    run = f.next_run()
    event = f.event("RUN_STATE", sequence=3, run=run, revision=1)
    rejection(lambda: history._append_run_state(verified, run, event, **args))
    assert adapter.calls == []


@pytest.mark.parametrize(
    ("value", "accepted"),
    [
        ("https://example.invalid", True),
        ("https://example.invalid/path", True),
        ("https://example.invalid?a=1", True),
        ("http://example.invalid", False),
        ("ftp://example.invalid", False),
        ("httpsx://example.invalid", False),
        (" https://example.invalid", False),
        ("https://example.invalid\nx", False),
        ("", False),
        (42, False),
        (None, False),
        (True, False),
        ([], False),
        ("https://example.invalid\x00x", False),
        ("https://example.invalid\rx", False),
        ("https://example.invalid\u0085x", False),
        ("https://example.invalid\u2028x", False),
        ("https://example.invalid\u2029x", False),
    ],
)
def test_validator_witness_endpoint_contract(value, accepted):
    schema = wit.SCHEMAS["authority_descriptor"]["properties"]["witness_head_reference"][
        "properties"
    ]["endpoint"]
    assert schema == {"type": "string", "pattern": "^https://"}
    if accepted:
        sql.validate_schema(value, schema)
    else:
        rejection(lambda: sql.validate_schema(value, schema))


@pytest.mark.parametrize(
    ("pattern", "value"),
    [
        ("^[0-9a-f]{64}$", "a" * 64),
        ("^[0-9a-f]{40}$", "b" * 40),
        ("^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$", "synthetic-key"),
        ("^[0-9a-f]{128}$", "c" * 128),
    ],
)
def test_validator_other_patterns_keep_fullmatch(pattern, value):
    schema = {"type": "string", "pattern": pattern}
    sql.validate_schema(value, schema)
    rejection(lambda: sql.validate_schema(value + "\n", schema))
    rejection(lambda: sql.validate_schema(" " + value, schema))


def test_m004_resigned_three_revision_chain_rejects_wrong_parent(factory):
    f = factory()
    records = [f.receipt]
    for revision, operation_id in (
        (1, OP_NEXT),
        (2, "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
    ):
        payload = {k: v for k, v in f.receipt.items() if k != "signature"}
        payload.update(
            revision=revision,
            operation_id=operation_id,
            ledger_digest=hashed("m004-revision-" + str(revision)),
            previous_witness_digest=sql.digest(records[-1]),
            genesis=None,
        )
        records.append(wit.sign_witness(payload, f.key))
    assert len({record["ledger_digest"] for record in records}) == 3
    arguments = {
        "expected_genesis_digest": sql.digest(records[0]),
        "expected_genesis_binding": records[0]["genesis"],
    }
    assert wit.verify_chain(records, [f.pin], **arguments) == sql.digest(records[2])
    payload = {k: v for k, v in records[2].items() if k != "signature"}
    payload["previous_witness_digest"] = sql.digest(records[0])
    records[2] = wit.sign_witness(payload, f.key)
    assert all(wit.verify_witness(record, f.pin) == sql.digest(record) for record in records)
    with pytest.raises(sql.AuthorityError, match="FORK_DETECTED"):
        wit.verify_chain(records, [f.pin], **arguments)
