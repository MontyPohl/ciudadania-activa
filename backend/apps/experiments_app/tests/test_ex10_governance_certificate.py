"""B3-REF-001 synthetic offline tests; no B4 runtime or operational artifacts."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
# Isolate the existing B3 fixture loader; SOURCE_DIR selects disposable mutants.
_B3_FIXTURE_NAME = "gov_b3_test_fixture_" + __name__.replace(".", "_")
spec = importlib.util.spec_from_file_location(
    _B3_FIXTURE_NAME, APP / "tests/test_ex10_history_authority.py"
)
assert spec is not None and spec.loader is not None
b = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = b
spec.loader.exec_module(b)
sql, history = b.sql, b.history
gov = sys.modules[history.__package__ + ".ex10_governance_certificate"]
NEW_RUN = "s11-22222222-3333-5444-8555-666666666666"
ND_RUN = "s11-11111111-2222-5333-8444-555555555555"
UUID = "55555555-6666-7777-8888-999999999999"
FIXTURES = Path(__file__).parent / "fixtures"
CONTRACT = json.loads((FIXTURES / "ex10_governance_contract_v1.json").read_bytes())
PLAN = CONTRACT["test_plan"]
PATHS = json.loads((FIXTURES / "ex10_governance_security_paths_v1.json").read_bytes())[
    "certificate_security_field_paths"
]


@pytest.fixture
def fixture():
    with tempfile.TemporaryDirectory(
        prefix="s11-b3-test-", dir=os.environ["S11_B3_TEST_BASE"]
    ) as root:
        path = Path(root)
        path.chmod(0o700)
        yield b.Fixture(path, (), create_db=True)


def fill(schema, key=""):
    if "const" in schema:
        return schema["const"]
    kind = schema["type"]
    if kind == "object":
        return {k: fill(s, k) for k, s in schema["properties"].items()}
    if kind == "array":
        return []
    if kind == "integer":
        return schema.get("minimum", 0)
    pattern = schema.get("pattern", "")
    if "[0-9a-f]{64}" in pattern:
        return b.hashed(key)
    if "[0-9a-f]{40}" in pattern:
        return "3" * 40
    if pattern.startswith("^s11-"):
        return NEW_RUN
    if "[0-9a-f]{8}" in pattern:
        return UUID
    if key == "issued_at":
        return b.NOW
    if key == "path":
        return "/synthetic-fixture/" + key
    return "fixture"


def candidate(f, verified=None, reuse=False):
    verified = f.verify() if verified is None else verified
    value = fill(gov.certificate_schema())
    value.update(
        authority_id=b.AUTH_ID,
        authority_epoch=1,
        authority_revision_before=0,
        authority_revision_after=1,
        authority_descriptor_reference=f.descriptor_ref,
        run_id=NEW_RUN,
        protocol="synthetic-v1",
        methodology="synthetic-methodology",
        commit_request_id=b.OP_NEXT,
        issued_at=b.NOW,
    )
    witness_sha = sql.digest(f.receipt)
    value["b3_base_witness"].update(
        authority_id=b.AUTH_ID,
        authority_epoch=1,
        revision=0,
        ledger_digest=f.receipt["ledger_digest"],
        witness_digest=witness_sha,
    )
    value["b3_base_witness"]["receipt_reference"]["sha256"] = witness_sha
    value["b3_base_current_head_reference"]["sha256"] = hashlib.sha256(
        verified.canonical_current_head
    ).hexdigest()
    value["session"]["header_reference"]["sha256"] = value["session"]["header_sha256"]
    value["runtime_profile"]["reference"]["sha256"] = value["runtime_profile"]["sha256"]
    value["session"]["header_reference"]["path"] = "/synthetic-fixture/header"
    value["runtime_profile"]["reference"]["path"] = "/synthetic-fixture/profile-v1"
    value["certificate_id"] = gov.certificate_identity(value)
    value["acknowledgment_digest"] = sql.digest(value["acknowledgments"])
    cert = gov.validate_governance_certificate(value)
    ref = b.artifact(
        {
            "path": str(f.root / (value["certificate_id"] + ".json")),
            "sha256": gov.governance_certificate_sha256(cert),
            "schema_version": gov.SCHEMA_VERSION,
        },
        "GOVERNANCE",
    )
    ref["introduced_revision"] = 1
    refs = []
    for item, kind in [
        (value["runtime_profile"]["reference"], "PROFILE"),
        (value["session"]["header_reference"], "SESSION"),
    ]:
        row = b.artifact(item, kind)
        row["introduced_revision"] = 1
        refs.append(row)
    if reuse:
        raise AssertionError("Use explicit existing-reference fixture below")
    run = f.run("OPEN", NEW_RUN)
    run.update(
        provenance_model="PROFILE_V2",
        introduced_revision=1,
        profile_reference_id=refs[0]["reference_id"],
        session_reference_id=refs[1]["reference_id"],
        evidence_authority_reference_id=ref["reference_id"],
    )
    event = f.event(
        "RUN_OPENED", sequence=len(f.state["authority_events"]) + 1, run=run, revision=1
    )
    event["evidence_reference_id"] = ref["reference_id"]
    adapter = b.TestPublicationAdapter(f)
    publication = f.publication_args(adapter, "RUN_OPENED")
    publication["evidence_reference_id"] = ref["reference_id"]
    return {
        "state": verified,
        "run": run,
        "event": event,
        "governance_certificate": cert,
        "governance_certificate_reference": ref,
        "reference_additions": tuple(refs),
        **publication,
    }


def clone_arguments(args):
    cloned = dict(args)
    for key in ["run", "event", "governance_certificate_reference", "reference_additions"]:
        cloned[key] = copy.deepcopy(args[key])
    return cloned


def opened(args):
    return history._append_run_opened(**args)


def context_rejection(value, key, change):
    expected = copy.deepcopy(value)
    mutated = copy.deepcopy(value)
    change(mutated)
    mutated["certificate_id"] = gov.certificate_identity(mutated)
    with pytest.raises(sql.AuthorityError):
        gov.validate_governance_certificate(mutated, context={key: expected[key]})


def mutated_leaf(value, path):
    changed = copy.deepcopy(value)
    parts = path.strip("/").split("/")
    node = changed
    for part in parts[:-1]:
        node = node[0] if part == "*" else node[part]
    key = 0 if parts[-1] == "*" else parts[-1]
    old = node[key]
    if isinstance(old, str):
        node[key] = old + "x"
    elif isinstance(old, int):
        node[key] = old + 1
    elif isinstance(old, list):
        node[key] = []
    else:
        raise AssertionError(path)
    return changed


def with_ack(value):
    result = copy.deepcopy(value)
    result["acknowledgments"] = [
        {
            "run_id": ND_RUN,
            "authority_revision": 0,
            "terminal_evidence_reference": b.reference("nd-final"),
        }
    ]
    result["acknowledgment_digest"] = sql.digest(result["acknowledgments"])
    return result


class RollbackAdapter(b.TestPublicationAdapter):
    __test__ = False

    def __init__(self, fixture, stage):
        super().__init__(fixture)
        self.stage = stage
        self.pre_atomic_state = None
        self.intent = None
        self.candidate_payload = None
        self.additions = None

    def durable_intent(self, intent, candidate_payload):
        self.intent = copy.deepcopy(intent)
        self.candidate_payload = candidate_payload
        super().durable_intent(intent, candidate_payload)

    def atomic_commit(self, additions):
        # Capture the exact DB boundary after durable intent, before any INSERT.
        self.pre_atomic_state = sql._load_database(self.fixture.database)
        self.additions = copy.deepcopy(additions)
        self.calls.append("commit")
        con = b._test_connection(self.fixture.database)
        try:
            con.execute("BEGIN")
            # Independent fault after successful physical ref insertion / run
            # insertion, rather than throwing before the transaction starts.
            sql._insert_rows(con, {"artifact_references": additions["artifact_references"]})
            if self.stage == "reference":
                raise RuntimeError("synthetic-reference-insert-fault")
            sql._insert_rows(con, {"official_runs": additions["official_runs"]})
            raise RuntimeError("synthetic-run-insert-fault")
        finally:
            con.rollback()
            con.close()


def _assert_rollback_snapshots(
    *, pre_opened_state, pre_atomic_state, post_failure_state, expected_publication_intent
):
    """Validate atomic rollback and the separate durable publication barrier."""
    assert (
        set(pre_opened_state) == set(pre_atomic_state) == set(post_failure_state) == set(sql.TABLES)
    )
    sql.validate_row("publication_intents", expected_publication_intent)
    operation_id = expected_publication_intent["operation_id"]
    assert all(
        row["operation_id"] != operation_id for row in pre_opened_state["publication_intents"]
    ), "intent already existed before opened"
    expected_intents = sql._ordered_state(
        {
            "publication_intents": [
                *pre_opened_state["publication_intents"],
                expected_publication_intent,
            ]
        }
    )["publication_intents"]
    for snapshot in (pre_atomic_state, post_failure_state):
        assert (
            sql._ordered_state(snapshot)["publication_intents"] == expected_intents
        ), "unexpected durable intents"
        assert all(
            row["operation_id"] != operation_id for row in snapshot["publication_resolutions"]
        ), "failed intent falsely resolved"
    before = sql._ordered_state(pre_opened_state)
    atomic = sql._ordered_state(pre_atomic_state)
    after = sql._ordered_state(post_failure_state)
    for name in sql.TABLES:
        if name != "publication_intents":
            assert atomic[name] == before[name], "unexpected change before atomic commit: " + name
        assert after[name] == atomic[name], "atomic state changed: " + name
    revision = max(row["revision"] for row in pre_opened_state["history_revisions"])
    assert (
        max(row["revision"] for row in post_failure_state["history_revisions"]) == revision
    ), "revision advanced"
    pre_digest = sql.logical_digest(pre_opened_state, revision)
    assert sql.logical_digest(pre_atomic_state, revision) == pre_digest, "pre-atomic digest changed"
    assert sql.logical_digest(post_failure_state, revision) == pre_digest, "rollback digest changed"


def assert_rollback(f, args, stage):
    pre_opened_state = sql._load_database(f.database)
    artifact = f.root / (args["governance_certificate"].value["certificate_id"] + ".json")
    artifact.write_bytes(args["governance_certificate"].canonical_bytes)
    adapter = RollbackAdapter(f, stage)
    args["adapter"] = adapter
    with pytest.raises(RuntimeError, match="synthetic-"):
        opened(args)
    post_failure_state = sql._load_database(f.database)
    assert adapter.pre_atomic_state is not None and adapter.additions is not None
    proposed = copy.deepcopy(pre_opened_state)
    for name, rows in adapter.additions.items():
        proposed[name].extend(copy.deepcopy(rows))
    revision = max(row["revision"] for row in pre_opened_state["history_revisions"])
    payload = sql.canonical_bytes(sql.logical_projection(proposed, revision + 1))
    payload_sha = hashlib.sha256(payload).hexdigest()
    expected_intent = {
        "operation_id": args["operation_id"],
        "base_revision": revision,
        "candidate_revision": revision + 1,
        "base_ledger_digest": sql.logical_digest(pre_opened_state, revision),
        "candidate_ledger_digest": payload_sha,
        "candidate_payload_path": args["candidate_payload_path"],
        "candidate_payload_sha256": payload_sha,
        "serialization_capability_digest": sql.digest(args["capability"]),
        "created_at": args["occurred_at"],
    }
    assert adapter.intent == expected_intent
    assert adapter.candidate_payload == payload
    assert Path(expected_intent["candidate_payload_path"]).read_bytes() == payload
    _assert_rollback_snapshots(
        pre_opened_state=pre_opened_state,
        pre_atomic_state=adapter.pre_atomic_state,
        post_failure_state=post_failure_state,
        expected_publication_intent=expected_intent,
    )
    assert all(
        row["run_id"] != args["run"]["run_id"] for row in post_failure_state["official_runs"]
    )
    assert all(
        row["run_id"] != args["run"]["run_id"] or row["event_kind"] != "RUN_OPENED"
        for row in post_failure_state["authority_events"]
    )
    assert args["governance_certificate_reference"] not in post_failure_state["artifact_references"]
    assert artifact.read_bytes() == args["governance_certificate"].canonical_bytes
    assert adapter.calls == ["intent", "witness", "commit"]
    proof = {
        "pre_opened_state": pre_opened_state,
        "pre_atomic_state": adapter.pre_atomic_state,
        "post_failure_state": post_failure_state,
        "expected_publication_intent": expected_intent,
    }
    evidence_base = os.environ.get("S11_B3_ROLLBACK_EVIDENCE")
    if evidence_base:
        path = Path(evidence_base) / ("rollback-" + stage + "-" + f.root.name + ".json")
        with path.open("x") as output:
            json.dump(proof, output, indent=2, sort_keys=True)
            output.write("\n")
    return proof


@pytest.mark.parametrize(
    "case", [t["name"] for t in PLAN["tests"]], ids=[t["id"] for t in PLAN["tests"]]
)
def test_governance_plan(fixture, case):
    f = fixture
    args = candidate(f)
    value = args["governance_certificate"].value
    cert = args["governance_certificate"]
    ref = args["governance_certificate_reference"]
    if case == "complete_certificate":
        assert len(value) == 31
        result = opened(args)
        state = result.material["state"]
        assert ref in state["artifact_references"]
        assert state["official_runs"] == [args["run"]]
        assert sql._ordered_state(sql._load_database(f.database)) == sql._ordered_state(state)
        assert state["witness_checkpoints"][-1]["ledger_digest"] == sql.logical_digest(state, 1)
    elif case == "canonical_roundtrip":
        encoded = gov.canonical_governance_certificate_bytes(cert)
        assert gov.parse_governance_certificate_bytes(encoded) == cert
        assert gov.canonical_governance_reference_bytes(ref, cert) == sql.canonical_bytes(ref)
        assert gov.parse_governance_reference_bytes(sql.canonical_bytes(ref), cert) == ref
        value["session"]["session_id"] = "changed"
        assert cert.value["session"]["session_id"] == "fixture"
    elif case == "missing_field":
        for key in list(value):
            bad = copy.deepcopy(value)
            del bad[key]
            with pytest.raises(sql.AuthorityError):
                gov.validate_governance_certificate(bad)
    elif case == "unknown_field":
        for bad in [{**value, "extra": 1}, {**value, "session": {**value["session"], "blob": {}}}]:
            with pytest.raises(sql.AuthorityError):
                gov.validate_governance_certificate(bad)
    elif case == "duplicate_key":
        raw = cert.canonical_bytes
        bad = b'{"kind":"GOVERNANCE_CERTIFICATE",' + raw[1:]
        with pytest.raises(sql.AuthorityError):
            gov.parse_governance_certificate_bytes(bad)
    elif case == "malformed_sha":
        for sha in ["A" * 64, "f" * 63, "z" * 64]:
            bad = copy.deepcopy(value)
            bad["run_identity_digest"] = sha
            with pytest.raises(sql.AuthorityError):
                gov.validate_governance_certificate(bad)
    elif case == "invalid_uuid":
        for rid in [
            NEW_RUN.replace("-5444-", "-4444-"),
            NEW_RUN.replace("-8555-", "-7555-"),
            NEW_RUN.upper(),
        ]:
            with pytest.raises(sql.AuthorityError):
                gov.validate_governance_certificate({**value, "run_id": rid})
    elif case == "invalid_revision":
        for after in [True, -1, sql.MAX_COUNTER + 1, 2]:
            with pytest.raises(sql.AuthorityError):
                gov.validate_governance_certificate({**value, "authority_revision_after": after})
    elif case == "invalid_project":
        for project in ["UPPER", "../escape", "x" * 64]:
            with pytest.raises(sql.AuthorityError):
                gov.validate_governance_certificate({**value, "project": project})
    elif case == "invalid_timestamp":
        for issued in [
            "2026-02-30T00:00:00.000000Z",
            "2026-10-04T00:00:00Z",
            "2026-10-04T00:00:00.000000+00:00",
        ]:
            with pytest.raises(sql.AuthorityError):
                gov.validate_governance_certificate({**value, "issued_at": issued})
    elif case == "noncanonical_json":
        for raw in [
            b"\xef\xbb\xbf" + cert.canonical_bytes,
            cert.canonical_bytes + b"\n",
            json.dumps(value).encode(),
            cert.canonical_bytes.replace(b'"authority_epoch":1', b'"authority_epoch":1.0'),
            cert.canonical_bytes.replace(b'"authority_epoch":1', b'"authority_epoch":NaN'),
        ]:
            with pytest.raises(sql.AuthorityError):
                gov.parse_governance_certificate_bytes(raw)
        with pytest.raises(sql.AuthorityError):
            gov.validate_governance_certificate({**value, "methodology": "e\u0301"})
    elif case == "secret_field":
        for key in [
            "private_key",
            "bridge_secret",
            "capability_secret",
            "raw_authentication_token",
            "db_credentials",
        ]:
            with pytest.raises(sql.AuthorityError):
                gov.validate_governance_certificate({**value, key: "secret"})
    elif case in [
        "wrong_reservation",
        "wrong_b4_witness",
        "wrong_permit",
        "wrong_evidence",
        "wrong_request",
        "wrong_run",
        "wrong_source",
    ]:
        key, sub = {
            "wrong_reservation": ("reservation", "reservation_id"),
            "wrong_b4_witness": ("reservation", "witness_sequence"),
            "wrong_permit": ("open_one_permit", "permit_digest"),
            "wrong_evidence": ("open_evidence_reference", "sha256"),
            "wrong_request": ("commit_request_digest", None),
            "wrong_run": ("run_id", None),
            "wrong_source": ("source_revision", None),
        }[case]

        def change(v):
            replacement = (
                b.hashed("different")
                if (sub or key).endswith("digest") or sub == "sha256"
                else 2
                if sub == "witness_sequence"
                else "3" * 40
                if key == "source_revision"
                else "s11-33333333-4444-5555-8666-777777777777"
                if key == "run_id"
                else b.OP_ZERO
            )
            if key == "source_revision":
                replacement = "4" * 40
            if sub:
                v[key][sub] = replacement
            else:
                v[key] = replacement

        context_rejection(value, key, change)
        if case in ["wrong_run", "wrong_source", "wrong_request"]:
            bad = clone_arguments(args)
            if case == "wrong_request":
                bad["operation_id"] = b.OP_ZERO
            else:
                bad["run"]["run_id" if case == "wrong_run" else "source_revision"] = (
                    "s11-33333333-4444-5555-8666-777777777777" if case == "wrong_run" else "4" * 40
                )
            with pytest.raises(sql.AuthorityError):
                opened(bad)
            assert args["adapter"].calls == []
    elif case in ["wrong_session", "header_not_full_record"]:
        bad = copy.deepcopy(value)
        bad["session"]["header_sha256"] = b.hashed("different")
        with pytest.raises(sql.AuthorityError):
            gov.validate_governance_certificate(bad)
    elif case == "wrong_profile":
        bad = copy.deepcopy(value)
        bad["runtime_profile"]["sha256"] = b.hashed("different")
        with pytest.raises(sql.AuthorityError):
            gov.validate_governance_certificate(bad)
    elif case == "wrong_authority":
        bad = clone_arguments(args)
        bad["governance_certificate"] = gov.validate_governance_certificate(
            {
                **value,
                "authority_descriptor_reference": b.reference(
                    "wrong", value["authority_descriptor_reference"]["schema_version"]
                ),
            }
        )
        rebind(bad)
        with pytest.raises(sql.AuthorityError):
            opened(bad)
    elif case == "wrong_b3_witness":
        bad = copy.deepcopy(value)
        bad["b3_base_witness"]["ledger_digest"] = b.hashed("wrong")
        badargs = clone_arguments(args)
        badargs["governance_certificate"] = gov.validate_governance_certificate(bad)
        # Rebind a structurally correct row to exercise base equality, not SHA rejection.
        rebind(badargs)
        with pytest.raises(sql.AuthorityError):
            opened(badargs)
        assert badargs["adapter"].calls == []
    elif case == "wrong_ack_set":
        badargs = clone_arguments(args)
        badargs["governance_certificate"] = gov.validate_governance_certificate(with_ack(value))
        rebind(badargs)
        with pytest.raises(sql.AuthorityError):
            opened(badargs)
        assert badargs["adapter"].calls == []
    elif case == "non_none_first_valid":
        with pytest.raises(sql.AuthorityError):
            gov.validate_governance_certificate({**value, "first_valid_status": "PASS_BINDING"})
    elif case == "header_projection_only":
        assert (
            value["session"]["header_reference"]["schema_version"]
            == "s11-ex10-session-v2-header-projection-v1"
        )
        assert value["session"]["schema_version"] == "s11c3-ex10-official-launcher-session-v2"
        assert len(value["session"]) == 4
    elif case == "deterministic_hash":
        assert (
            gov.governance_certificate_sha256(cert)
            == hashlib.sha256(cert.canonical_bytes).hexdigest()
        )
        identity = {k: value[k] for k in ["namespace", "authority_id", "authority_epoch"]}
        identity["reservation_id"] = value["reservation"]["reservation_id"]
        preimage = b"S11-EX10-GOVERNANCE-ID-v1\n" + sql.canonical_bytes(identity)
        assert value["certificate_id"] == hashlib.sha256(preimage).hexdigest()
        assert value["certificate_id"] != hashlib.sha256(sql.canonical_bytes(identity)).hexdigest()
        altered = copy.deepcopy(value)
        altered["authority_epoch"] += 1
        assert gov.certificate_identity(altered) != value["certificate_id"]
    elif case == "every_security_field":
        assert len(set(PATHS)) == 74
        original = with_ack(value)
        assert all(
            gov._canonical_sha256(sql.canonical_bytes(mutated_leaf(original, p)))
            != gov._canonical_sha256(sql.canonical_bytes(original))
            for p in PATHS
        )
    elif case == "identity_not_content_hash":
        changed = {**value, "project": "changed"}
        assert gov.certificate_identity(changed) == value["certificate_id"]
        assert gov.governance_certificate_sha256(changed) != gov.governance_certificate_sha256(cert)
        result = opened(args)
        bad = clone_arguments(args)
        bad["state"] = result
        bad["governance_certificate"] = gov.validate_governance_certificate(changed)
        rebind(bad)
        with pytest.raises(sql.AuthorityError):
            opened(bad)
    elif case == "no_self_reference":
        for key in ["governance_digest", "b3_open_witness_head", "b4_committed_receipt"]:
            with pytest.raises(sql.AuthorityError):
                gov.validate_governance_certificate({**value, key: "x"})
    elif case in ["b3_reference_projection", "transitive_byte_change"]:
        result = opened(args)
        state = result.material["state"]
        for key in ref:
            changed = copy.deepcopy(state)
            r = next(
                r
                for r in changed["artifact_references"]
                if r["reference_id"] == ref["reference_id"]
            )
            r[key] = 2 if key == "introduced_revision" else r[key] + "x"
            assert sql.logical_digest(changed, 1) != sql.logical_digest(state, 1)
        assert state["witness_checkpoints"][-1]["ledger_digest"] == sql.logical_digest(state, 1)
    elif case == "sha_matches_artifact":
        assert (
            gov.validate_governance_reference(ref, cert)["sha256"]
            == hashlib.sha256(cert.canonical_bytes).hexdigest()
        )
    elif case in ["sha_mismatch", "wrong_schema", "wrong_kind"]:
        key = {"sha_mismatch": "sha256", "wrong_schema": "schema_version", "wrong_kind": "kind"}[
            case
        ]
        bad = copy.deepcopy(ref)
        bad[key] = (
            b.hashed("wrong")
            if key == "sha256"
            else "GOVERNANCE_CERTIFICATE"
            if key == "kind"
            else "wrong-v1"
        )
        bad["reference_id"] = sql.digest(
            {k: bad[k] for k in ["path", "sha256", "schema_version", "kind"]}
        )
        with pytest.raises(sql.AuthorityError):
            gov.validate_governance_reference(bad, cert)
    elif case in ["rewrite_rejected", "duplicate_different_certificate"]:
        result = opened(args)
        bad = clone_arguments(args)
        bad["state"] = result
        bad["governance_certificate"] = gov.validate_governance_certificate(
            {**value, "commit_request_digest": b.hashed("changed")}
        )
        rebind(bad)
        with pytest.raises(sql.AuthorityError):
            opened(bad)
        assert args["adapter"].calls == ["intent", "witness", "commit"]
    elif case == "reference_id_formula":
        assert ref["reference_id"] == sql.digest(
            {k: ref[k] for k in ["path", "sha256", "schema_version", "kind"]}
        )
    elif case == "unapproved_namespace":
        with pytest.raises(sql.AuthorityError):
            gov.validate_governance_reference(ref, cert, artifact_namespace="/unapproved")
        assert gov.validate_governance_reference(ref, cert, artifact_namespace=str(f.root)) == ref
    elif case == "reuse_profile_session":
        # Build a separate genesis with those two already-approved rows; do not
        # mutate an existing committed history or insert refs pre-OPEN.
        with _nested_b3_test_directory(f.root) as root:
            reused = ExistingReferencesFixture(Path(root), (), create_db=True)
            reused_args = candidate(reused)
            reused_args["reference_additions"] = ()
            result = opened(reused_args)
            assert (
                len(result.material["state"]["artifact_references"])
                == len(reused.state["artifact_references"]) + 1
            )
    elif case in ["artifact_exists_b3_fails", "reference_insert_rollback"]:
        assert_rollback(f, args, "run" if case == "artifact_exists_b3_fails" else "reference")
    elif case in ["duplicate_same_certificate", "response_lost"]:
        result = opened(args)
        args["state"] = result
        assert opened(args) is result
        assert args["adapter"].calls == ["intent", "witness", "commit"]
        assert history.current_revision(result)["revision"] == 1
    elif case == "witness_ahead_ledger":
        args["adapter"] = b.TestPublicationAdapter(f, fail="commit")
        with pytest.raises(RuntimeError, match="synthetic-local-commit-failure"):
            opened(args)
        state = sql._load_database(f.database)
        assert len(state["history_revisions"]) == 1 and len(state["publication_intents"]) == 2
        assert args["adapter"].receipt["revision"] == 1
        assert args["adapter"].calls == ["intent", "witness", "commit"]
    elif case in ["certificate_missing_committed", "certificate_tampered_committed"]:
        path = Path(ref["path"])
        path.write_bytes(cert.canonical_bytes)
        result = opened(args)
        assert history.current_revision(result)["revision"] == 1
        path.unlink()
        if case == "certificate_tampered_committed":
            path.write_bytes(cert.canonical_bytes + b" ")
            with pytest.raises(sql.AuthorityError):
                gov.parse_governance_certificate_bytes(path.read_bytes())
        else:
            with pytest.raises(FileNotFoundError):
                path.read_bytes()
        # No B4 qualification/repair implemented: ledger signature is distinct
        # from external bytes qualification performed by a future consumer.
        assert gov.governance_certificate_sha256(cert) == ref["sha256"]
    elif case == "run1_no_certificate":
        with _nested_b3_test_directory(f.root) as root:
            historic = b.Fixture(Path(root), ("ND",), create_db=True)
            assert history.first_valid_status(historic.verify())["status"] == "NONE"
            assert historic.state["official_runs"][0]["provenance_model"] == "HISTORICAL_V1"
            assert not any(r["kind"] == "GOVERNANCE" for r in historic.state["artifact_references"])
    elif case == "new_b4_requires_certificate":
        del args["governance_certificate"]
        with pytest.raises(TypeError):
            opened(args)
        args["governance_certificate"] = None
        with pytest.raises(sql.AuthorityError):
            opened(args)
        assert args["adapter"].calls == []
    elif case == "terminal_preserves_open":
        result = opened(args)
        state = result.material["state"]
        terminal = copy.deepcopy(args["run"])
        terminal.update(
            run_version=1,
            previous_run_version=0,
            introduced_revision=2,
            lifecycle_state="TERMINAL",
            phase="FINALIZED",
            result="NOT_DEMONSTRATED",
            validity_classification="NON_BINDING_ND",
            finalized_at=b.NOW,
            ack_required=True,
            evidence_authority_reference_id=f.ref_ids["evidence"],
        )
        f.state = state
        f.receipt = history._receipts(state)[-1]
        event = f.event(
            "RUN_TERMINAL", sequence=len(state["authority_events"]) + 1, run=terminal, revision=2
        )
        pub = f.publication_args(b.TestPublicationAdapter(f), "RUN_TERMINAL")
        pub["operation_id"] = "66666666-7777-8888-9999-aaaaaaaaaaaa"
        pub["capability"].update(operation_id=pub["operation_id"], expected_revision=2)
        pub["candidate_payload_path"] = str(f.root / "terminal-payload.json")
        final = history._append_run_terminal(result, terminal, event, **pub)
        args["state"] = final
        assert opened(args) is final
        assert args["run"] in final.material["state"]["official_runs"]
        assert ref in final.material["state"]["artifact_references"]
        assert history.find_run(final, NEW_RUN)["lifecycle_state"] == "TERMINAL"
    elif case == "local_test_no_promotion":
        args["run"]["mode"] = "LOCAL_TEST"
        with pytest.raises(sql.AuthorityError):
            opened(args)
    elif case == "b1_b2_b3_custody":
        baseline = CONTRACT["baseline"]
        repo = APP.parents[2]
        for path, sha in baseline["frozen_source_hashes"].items():
            if "runtime_profile" in path or "session_v2" in path:
                raw = (repo / path).read_bytes()
                if path == "backend/apps/experiments_app/ex10_runtime_profile.py":
                    raw = b.runtime_profile_as_frozen(raw)
                assert hashlib.sha256(raw).hexdigest() == sha
        for row in baseline["attested_files"]:
            raw = (repo / row["path"]).read_bytes()
            if row["path"] == "docs/tesis/S11A_POLITICA_CUSTODIA_Y_ACCESO.md":
                # W11 permits only the exact §5.1 addendum; baseline metadata
                # remains historical and v10 custody remains pending.
                assert row["baseline_sha256"] == (
                    "ab1125cdb59fabb9483ae936d7b2c9b1329b693f3865d4f87579a443a06249ec"
                )
                assert hashlib.sha256(raw).hexdigest() == (
                    "dc8d13ea92f397d96578ba0990807e87f078c7b2cdaccd29ed92db9cdca2b0f9"
                )
                prefix, heading, addendum = raw.partition(
                    b"\n\n### 5.1. EX-10 v2: roots externas y autoridad operacional\n"
                )
                assert heading
                _, next_heading, suffix = addendum.partition(
                    b"\n## 6. Matriz nominal y de procesos\n"
                )
                assert next_heading
                assert (
                    hashlib.sha256(prefix + next_heading + suffix).hexdigest()
                    == (row["baseline_sha256"])
                )
            elif row["path"] == "backend/apps/experiments_app/evidence.py":
                # W12 separates path reads from pure final-seal validation;
                # the B3 baseline remains historical and the admitted delta
                # stays byte-exact.
                assert row["baseline_sha256"] == (
                    "0e10cba4e74ed5de2d5c427d31fc4fac82025c04f9025792496348880014935f"
                )
                assert hashlib.sha256(raw).hexdigest() == (
                    "94a32406df188f77ed073a19799ef8ffef670010aca60a561f81b3045a221b2f"
                )
            elif row["path"] == "backend/config/settings/base.py":
                # Product settings keep evolving after B3 (9fd05d6): S12 throttles and
                # ranking TTL, Redis TLS normalization. The baseline stays historical;
                # the S11-B instrumentation block, to end of file, stays byte-exact.
                assert row["baseline_sha256"] == (
                    "37e55f0acd938b2f0d39c7ae22328c26adfcd6a2d6fb1588342b7852ad70c34c"
                )
                assert raw.count(b"# --- S11-B") == 1
                _, marker, block = raw.partition(b"# --- S11-B")
                assert hashlib.sha256(marker + block).hexdigest() == (
                    "ed0b8f7f8c890174c8be8492314ce666b02fc3372fba39ffa0d0916e17fe052f"
                )
            else:
                assert hashlib.sha256(raw).hexdigest() == row["baseline_sha256"]
    else:
        raise AssertionError("Unimplemented approved scenario: " + case)


def rebind(args):
    ref = args["governance_certificate_reference"]
    ref["sha256"] = gov.governance_certificate_sha256(args["governance_certificate"])
    ref["reference_id"] = sql.digest(
        {k: ref[k] for k in ["path", "sha256", "schema_version", "kind"]}
    )
    args["run"]["evidence_authority_reference_id"] = args["event"]["evidence_reference_id"] = args[
        "evidence_reference_id"
    ] = ref["reference_id"]


class ExistingReferencesFixture(b.Fixture):
    def store(self):
        # Change only synthetic genesis before its first physical persistence,
        # then re-seal that genesis with its ephemeral witness key.
        value = fill(gov.certificate_schema())
        refs = [
            b.artifact(
                {
                    "path": "/synthetic-fixture/profile-v1",
                    "schema_version": value["runtime_profile"]["schema_version"],
                    "sha256": value["runtime_profile"]["sha256"],
                },
                "PROFILE",
            ),
            b.artifact(
                {
                    "path": "/synthetic-fixture/header",
                    "schema_version": value["session"]["header_reference"]["schema_version"],
                    "sha256": value["session"]["header_sha256"],
                },
                "SESSION",
            ),
        ]
        self.state["artifact_references"].extend(
            row
            for row in refs
            if row["kind"] in getattr(self, "reused_kinds", ("PROFILE", "SESSION"))
        )
        revision = self.state["history_revisions"][0]
        revision["ledger_digest"] = sql.logical_digest(self.state, 0)
        receipt = {k: v for k, v in self.receipt.items() if k != "signature"}
        receipt["ledger_digest"] = revision["ledger_digest"]
        self.receipt = b.wit.sign_witness(b.wit.build_witness_payload(receipt), self.key)
        self.state["witness_checkpoints"] = [history._checkpoint(self.receipt)]
        self.floor = b.wit.TrustedFloor(b.AUTH_ID, 1, 0, sql.digest(self.receipt))
        intent = self.state["publication_intents"][0]
        intent["candidate_payload_sha256"] = intent["candidate_ledger_digest"] = revision[
            "ledger_digest"
        ]
        Path(intent["candidate_payload_path"]).write_bytes(
            sql.canonical_bytes(sql.logical_projection(self.state, 0))
        )
        resolution = self.state["publication_resolutions"][0]
        resolution["receipt_digest"] = sql.digest(self.receipt)
        resolution["fresh_witness_observation_digest"] = sql.digest(self.observation()["head"])
        super().store()


@pytest.mark.parametrize("path", PATHS)
def test_security_path_hash_binding(fixture, path):
    original = with_ack(candidate(fixture)["governance_certificate"].value)
    # Coverage includes invalid changes to const fields; independent raw
    # canonical preimages prove hashing even when validation rejects a change.
    assert gov._canonical_sha256(
        sql.canonical_bytes(mutated_leaf(original, path))
    ) != gov._canonical_sha256(sql.canonical_bytes(original))


def test_reference_ordering_contract():
    """Equal complete rows normalize identically across physical insert orders."""
    with (
        tempfile.TemporaryDirectory(
            prefix="s11-b3-test-", dir=os.environ["S11_B3_TEST_BASE"]
        ) as first_root,
        tempfile.TemporaryDirectory(
            prefix="s11-b3-test-", dir=os.environ["S11_B3_TEST_BASE"]
        ) as second_root,
    ):
        fixture = b.Fixture(Path(first_root), (), create_db=False)
        expected = copy.deepcopy(fixture.state)
        refs = sorted(expected["artifact_references"], key=lambda r: r["reference_id"])
        # R1/R2/R3 denote ascending reference_id, not arbitrary ordinal values.
        fixture.state["artifact_references"] = [refs[2], refs[0], refs[1], *refs[3:]]
        fixture.store()
        second = Path(second_root) / "ledger.sqlite3"
        con = sql._create_test_database(second)
        try:
            con.execute("BEGIN")
            other = copy.deepcopy(expected)
            other["artifact_references"] = refs
            sql._insert_rows(con, other)
            con.commit()
        finally:
            con.close()
        first_loaded = sql._load_database(fixture.database)
        second_loaded = sql._load_database(second)
        canonical = sql._ordered_state(expected)
        assert sql._ordered_state(first_loaded) == canonical
        assert sql._ordered_state(second_loaded) == canonical
        assert sql.ORDERS["artifact_references"] == ["reference_id"]
        columns = list(sql.TABLES["artifact_references"]["columns"])
        query = "SELECT " + ",".join(columns) + " FROM artifact_references ORDER BY reference_id"
        con = sql._connect_readonly(fixture.database)
        try:
            ordered = [dict(zip(columns, row, strict=True)) for row in con.execute(query)]
        finally:
            con.close()
        assert ordered == refs
        first_projection = sql.logical_projection(first_loaded, 0)
        second_projection = sql.logical_projection(second_loaded, 0)
        assert first_projection == second_projection
        assert [r["reference_id"] for r in first_projection["artifact_references"]] == [
            r["reference_id"] for r in refs
        ]
        assert sql.logical_digest(first_loaded, 0) == sql.logical_digest(second_loaded, 0)
        assert (
            sql.logical_digest(first_loaded, 0) == expected["history_revisions"][0]["ledger_digest"]
        )


def _nested_b3_test_directory(parent):
    """Each synthetic SQLite parent must satisfy the existing B3 test domain."""
    return tempfile.TemporaryDirectory(prefix="s11-b3-test-", dir=parent)


@pytest.mark.parametrize(
    "fixture_type",
    [b.Fixture, ExistingReferencesFixture],
    ids=["nested_base", "nested_reused_profile_session"],
)
def test_nested_fixture_test_domain(fixture, fixture_type):
    with _nested_b3_test_directory(fixture.root) as nested:
        root = Path(nested)
        assert root.parent == fixture.root
        assert root.name.startswith("s11-b3-test-")
        created = fixture_type(root, (), create_db=True)
        assert created.database.parent == root
        assert created.database.is_file()
        assert sql._load_database(created.database)["metadata"] == created.state["metadata"]


def test_test_domain_is_each_immediate_db_parent(fixture):
    path = fixture.root / "not-a-b3-domain" / "probe.sqlite3"
    assert "s11-b3-test-" in fixture.root.name
    assert "s11-b3-test-" not in path.parent.name
    with pytest.raises(sql.AuthorityError) as error:
        sql._create_test_database(path)
    assert error.value.code == "TEST_DOMAIN_REQUIRED"
    assert not path.exists() and not path.parent.exists()


@pytest.mark.parametrize("case", range(1, 13), ids=[f"R{i}" for i in range(1, 13)])
def test_rollback_helper_contract(fixture, monkeypatch, case):
    args = candidate(fixture)
    proof = assert_rollback(fixture, args, "run")
    post = proof["post_failure_state"]
    expected_intent = proof["expected_publication_intent"]
    if case == 1:
        # The real SQLite read differs from the canonical reference ordering.
        proof["pre_atomic_state"] = sql._ordered_state(proof["pre_atomic_state"])
        assert post["artifact_references"] != proof["pre_atomic_state"]["artifact_references"]
    elif case == 2:
        assert (
            len(post["publication_intents"])
            == len(proof["pre_opened_state"]["publication_intents"]) + 1
        )
        assert expected_intent in post["publication_intents"]
    elif case == 3:
        unexpected = {**expected_intent, "operation_id": "66666666-7777-5888-8999-000000000000"}
        post["publication_intents"].append(unexpected)
    elif case == 4:
        row = next(
            r
            for r in post["publication_intents"]
            if r["operation_id"] == expected_intent["operation_id"]
        )
        row["candidate_payload_sha256"] = b.hashed("wrong-payload")
    elif case == 5:
        resolution = copy.deepcopy(post["publication_resolutions"][0])
        resolution.update(
            operation_id=expected_intent["operation_id"],
            committed_revision=1,
            disposition="COMMITTED",
        )
        post["publication_resolutions"].append(resolution)
    elif case == 6:
        post["artifact_references"].append(copy.deepcopy(args["governance_certificate_reference"]))
    elif case == 7:
        post["official_runs"].append(copy.deepcopy(args["run"]))
    elif case == 8:
        post["authority_events"].append(copy.deepcopy(args["event"]))
    elif case == 9:
        post["history_revisions"].append(
            copy.deepcopy(args["adapter"].additions["history_revisions"][0])
        )
    elif case == 10:
        post["artifact_references"][0]["sha256"] = b.hashed("changed-reference")
    elif case == 11:
        post["artifact_references"].pop()
    elif case == 12:
        original_digest = sql.logical_digest

        def altered_digest(state, revision):
            return (
                b.hashed("wrong-logical-digest")
                if state is post
                else original_digest(state, revision)
            )

        monkeypatch.setattr(sql, "logical_digest", altered_digest)
    if case in (1, 2):
        _assert_rollback_snapshots(**proof)
    else:
        expected_error = (
            "unexpected durable intents"
            if case in (3, 4)
            else "failed intent falsely resolved"
            if case == 5
            else "rollback digest changed"
            if case == 12
            else "atomic state changed"
        )
        with pytest.raises(AssertionError, match=expected_error):
            _assert_rollback_snapshots(**proof)


B4_RESERVATION_DESIGN = CONTRACT["reservation_design"]
RESERVATION_INTENT_FIELDS = tuple(
    B4_RESERVATION_DESIGN["x-canonicalization"]["digest_preimages"]["reservation_digest"]
    .split("EXACT fields: ", 1)[1]
    .split(".", 1)[0]
    .split(",")
)
RESERVE_SIGNATURE_DOMAIN = b"S11-EX10-B4-RESERVATION-v1\n"


def _reservation_schema_value(schema, key=""):
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    if "anyOf" in schema:
        return (
            None
            if any(s.get("type") == "null" for s in schema["anyOf"])
            else _reservation_schema_value(schema["anyOf"][0], key)
        )
    if schema["type"] == "object":
        return {k: _reservation_schema_value(v, k) for k, v in schema["properties"].items()}
    if schema.get("pattern", "").startswith("^[0-9]{4}"):
        return b.NOW
    if key == "signature":
        return "0" * 128
    return fill(schema, key)


def _reservation_intent_digest(record):
    assert len(RESERVATION_INTENT_FIELDS) == 27
    return sql.digest({k: record[k] for k in RESERVATION_INTENT_FIELDS})


def _reservation_record_digest(record):
    return sql.digest({k: v for k, v in record.items() if k != "record_digest"})


def _sign_test_reserve_receipt(receipt, fixture):
    # Test-only existing ephemeral key; use the distinct approved B4 domain.
    assert (
        "S11-EX10-B4-RESERVATION-v1 LF"
        in B4_RESERVATION_DESIGN["x-canonicalization"]["digest_preimages"][
            "reservation_receipt_signature"
        ]
    )
    message = RESERVE_SIGNATURE_DOMAIN + sql.canonical_bytes(
        {k: v for k, v in receipt.items() if k != "signature"}
    )
    receipt["signature"] = fixture.key.sign(message).hex()
    fixture.key.public_key().verify(bytes.fromhex(receipt["signature"]), message)


def _write_test_reservation_sources(source):
    receipt_bytes = sql.canonical_bytes(source["receipt"])
    source["record"]["reservation_receipt_reference"] = {
        "path": str(source["receipt_path"]),
        "sha256": hashlib.sha256(receipt_bytes).hexdigest(),
        "schema_version": source["receipt"]["schema_version"],
    }
    source["record"]["record_digest"] = _reservation_record_digest(source["record"])
    source["receipt_path"].write_bytes(receipt_bytes)
    source["record_path"].write_bytes(sql.canonical_bytes(source["record"]))


def _reservation_context_from_sources(source):
    # The GOV codec receives caller-qualified claims. Derive this test context
    # from actual canonical source bytes, never from certificate.reservation.
    record_bytes = source["record_path"].read_bytes()
    receipt_bytes = source["receipt_path"].read_bytes()
    record, receipt = sql.parse_canonical(record_bytes), sql.parse_canonical(receipt_bytes)
    sql.validate_schema(record, B4_RESERVATION_DESIGN["$defs"]["reservation"])
    sql.validate_schema(receipt, B4_RESERVATION_DESIGN["$defs"]["reservation_receipt"])
    assert record["reservation_digest"] == _reservation_intent_digest(record)
    assert record["record_digest"] == _reservation_record_digest(record)
    receipt_reference = {
        "path": str(source["receipt_path"]),
        "sha256": hashlib.sha256(receipt_bytes).hexdigest(),
        "schema_version": receipt["schema_version"],
    }
    assert record["reservation_receipt_reference"] == receipt_reference
    return {
        "reservation_id": record["reservation_id"],
        "reservation_digest": _reservation_intent_digest(record),
        "record_reference": {
            "path": str(source["record_path"]),
            "sha256": hashlib.sha256(record_bytes).hexdigest(),
            "schema_version": record["schema_version"],
        },
        "record_sequence": record["record_sequence"],
        "fencing_token": record["fencing_token"],
        "state_at_open": record["state"],
        "witness_receipt_reference": receipt_reference,
        "witness_sequence": receipt["sequence"],
    }


def _reservation_binding_fixture(fixture):
    value = candidate(fixture)["governance_certificate"].value
    record = _reservation_schema_value(B4_RESERVATION_DESIGN["$defs"]["reservation"])
    manifest = importlib.import_module(history.__package__ + ".manifest")
    identity = record["run_identity"]
    identity.update(
        started_at=b.NOW,
        git_sha=value["source_revision"],
        execution_nonce=record["nonce"],
    )
    identity["run_id"] = manifest.make_run_id(
        started_at=b.datetime.fromisoformat(b.NOW.replace("Z", "+00:00")),
        git_sha=identity["git_sha"],
        dataset_id=identity["dataset_id"],
        scenario_id=identity["scenario_id"],
        seed=identity["seed"],
        execution_nonce=identity["execution_nonce"],
    )
    identity["identity_digest"] = sql.digest(
        {k: v for k, v in identity.items() if k != "identity_digest"}
    )
    record.update(
        reservation_id=value["reservation"]["reservation_id"],
        authority_id=value["authority_id"],
        authority_epoch=value["authority_epoch"],
        session_id=value["session"]["session_id"],
        session_header_sha256=value["session"]["header_sha256"],
        runtime_profile_sha256=value["runtime_profile"]["sha256"],
        source_revision=value["source_revision"],
        project=value["project"],
        request_id=value["reserve_request_id"],
        request_digest=value["reserve_request_digest"],
        acknowledgment_digest=value["acknowledgment_digest"],
        previous_authority_revision=value["authority_revision_before"],
        previous_ledger_digest=value["b3_base_witness"]["ledger_digest"],
        previous_witness_digest=value["b3_base_witness"]["witness_digest"],
        expected_next_revision=value["authority_revision_after"],
        run_id=identity["run_id"],
        freshness_deadline="2026-10-04T00:02:00.000000Z",
        state="PENDING",
        fencing_token=7,
        record_sequence=1,
    )
    record["reservation_digest"] = _reservation_intent_digest(record)
    record["record_digest"] = _reservation_record_digest(record)
    before_grant_digest = record["record_digest"]
    receipt = _reservation_schema_value(B4_RESERVATION_DESIGN["$defs"]["reservation_receipt"])
    receipt.update(
        namespace=record["namespace"],
        authority_id=record["authority_id"],
        authority_epoch=record["authority_epoch"],
        reservation_id=record["reservation_id"],
        reservation_digest=record["reservation_digest"],
        state="PENDING",
        transition="RESERVE",
        fencing_token=record["fencing_token"],
        sequence=1,
        base_authority_revision=record["previous_authority_revision"],
        base_authority_witness_digest=record["previous_witness_digest"],
        operation_id=record["request_id"],
        key_id=fixture.pin.record["key_id"],
        key_epoch=fixture.pin.record["key_epoch"],
    )
    _sign_test_reserve_receipt(receipt, fixture)
    # Attach the already-existing receipt in a successor PENDING record; no
    # receipt/record digest cycle and no conflation of the two sequences.
    record.update(record_sequence=2, previous_reservation_record_digest=before_grant_digest)
    source = {
        "record": record,
        "receipt": receipt,
        "record_path": fixture.root / "reservation-pending.json",
        "receipt_path": fixture.root / "reserve-receipt.json",
        "certificate": value,
    }
    _write_test_reservation_sources(source)
    value["reservation"] = _reservation_context_from_sources(source)
    value["run_id"] = identity["run_id"]
    value["run_identity_digest"] = identity["identity_digest"]
    value["certificate_id"] = gov.certificate_identity(value)
    gov.validate_governance_certificate(
        value, context={"reservation": _reservation_context_from_sources(source)}
    )
    return source


def _save_reservation_test_evidence(source, case, code):
    base = os.environ.get("S11_RESERVATION_BINDING_EVIDENCE")
    if base:
        path = Path(base) / (case + "-" + source["record_path"].parent.name + ".json")
        with path.open("x") as output:
            json.dump(
                {
                    "case": case,
                    "rejection_code": code,
                    "certificate": source["certificate"],
                    "pending_record": sql.parse_canonical(source["record_path"].read_bytes()),
                    "reserve_receipt": sql.parse_canonical(source["receipt_path"].read_bytes()),
                    "source_expected_reservation": _reservation_context_from_sources(source),
                },
                output,
                indent=2,
                sort_keys=True,
            )
            output.write("\n")


def _assert_reservation_source_rejected(source, case):
    expected = _reservation_context_from_sources(source)
    with pytest.raises(sql.AuthorityError) as error:
        gov.validate_governance_certificate(
            source["certificate"], context={"reservation": expected}
        )
    assert error.value.code == "GOV_BINDING"
    _save_reservation_test_evidence(source, case, error.value.code)


def test_reservation_sources_cross_binding(fixture):
    source = _reservation_binding_fixture(fixture)
    value, record, receipt = source["certificate"], source["record"], source["receipt"]
    expected = _reservation_context_from_sources(source)
    assert value["reservation"] == expected
    assert expected["reservation_id"] == record["reservation_id"] == receipt["reservation_id"]
    assert (
        expected["reservation_digest"]
        == record["reservation_digest"]
        == receipt["reservation_digest"]
    )
    assert expected["record_sequence"] == record["record_sequence"] == 2
    assert expected["witness_sequence"] == receipt["sequence"] == 1
    assert expected["fencing_token"] == record["fencing_token"] == receipt["fencing_token"]
    assert record["state"] == receipt["state"] == expected["state_at_open"] == "PENDING"
    assert receipt["transition"] == "RESERVE"
    assert receipt["base_authority_revision"] == record["previous_authority_revision"]
    assert receipt["base_authority_witness_digest"] == record["previous_witness_digest"]
    _save_reservation_test_evidence(source, "cross-binding", None)


@pytest.mark.parametrize("field", ["reservation_digest", "record_sequence", "fencing_token"])
def test_reservation_claim_rejection(fixture, field):
    source = _reservation_binding_fixture(fixture)
    claim = source["certificate"]["reservation"]
    claim[field] = b.hashed("wrong-intent") if field == "reservation_digest" else claim[field] + 1
    # Structurally and internally valid; rejection must come from source equality.
    gov.validate_governance_certificate(source["certificate"])
    _assert_reservation_source_rejected(source, "claim-" + field)


@pytest.mark.parametrize("field", ["record_sequence", "updated_at"])
def test_pending_record_bytes_rejection(fixture, field):
    source = _reservation_binding_fixture(fixture)
    original = copy.deepcopy(source["certificate"]["reservation"])
    if field == "record_sequence":
        source["record"]["previous_reservation_record_digest"] = source["record"]["record_digest"]
        source["record"][field] += 1
    else:
        source["record"][field] = "2026-10-04T00:00:01.000000Z"
    _write_test_reservation_sources(source)
    expected = _reservation_context_from_sources(source)
    assert expected["reservation_id"] == original["reservation_id"]
    assert expected["reservation_digest"] == original["reservation_digest"]
    assert expected["record_reference"]["sha256"] != original["record_reference"]["sha256"]
    _assert_reservation_source_rejected(source, "pending-bytes-" + field)


@pytest.mark.parametrize(
    "field", ["sequence", "fencing_token", "reservation_id", "base_authority_witness_digest"]
)
def test_reserve_receipt_bytes_rejection(fixture, field):
    source = _reservation_binding_fixture(fixture)
    original = copy.deepcopy(source["certificate"]["reservation"])
    receipt = source["receipt"]
    receipt[field] = (
        receipt[field] + 1
        if field in ("sequence", "fencing_token")
        else b.OP_ZERO
        if field == "reservation_id"
        else b.hashed("different-base-witness")
    )
    # A different, validly signed synthetic source must not reuse the old cert.
    # This does not give an attacker the fixture key or implement B4 signing.
    _sign_test_reserve_receipt(receipt, fixture)
    _write_test_reservation_sources(source)
    expected = _reservation_context_from_sources(source)
    assert (
        expected["witness_receipt_reference"]["sha256"]
        != original["witness_receipt_reference"]["sha256"]
    )
    _assert_reservation_source_rejected(source, "reserve-receipt-" + field)


@pytest.fixture
def session_binding_source(fixture, monkeypatch):
    # Reuse only the accepted B2 synthetic fixture setup, never its test functions
    # or operational record writer. All files remain in disposable test roots.
    loader_spec = importlib.util.spec_from_file_location(
        "gov_session_binding_b2_fixture", APP / "tests/test_ex10_session_v2.py"
    )
    assert loader_spec is not None and loader_spec.loader is not None
    loader = importlib.util.module_from_spec(loader_spec)
    loader_spec.loader.exec_module(loader)
    monkeypatch.setenv(
        "S11_B2_TEST_BASE",
        os.environ.get("S11_SESSION_B2_TEST_BASE", os.environ["S11_B2_TEST_BASE"]),
    )
    setup = loader.fixture.__wrapped__()
    profile, header_a, _, record_parent, _ = next(setup)
    try:
        b2 = loader.session
        arguments = {
            "session_id": header_a["session_id"],
            "created_at": "2026-10-04T00:02:00.000000Z",
            "branch": header_a["branch"],
            "launcher_blobs": header_a["launcher_blobs"],
            "runtime_profile_approval_reference": header_a["runtime_profile_approval_reference"],
            "path_identity_binding": header_a["path_identity_binding"],
        }
        header_b = b2.new_session_v2_header(profile, **arguments)
        arguments["session_id"] = "synthetic-session-b"
        header_c = b2.new_session_v2_header(profile, **arguments)
        assert len(header_a) == len(loader.EXPECTED_HEADER_KEYS) == 27
        assert set(header_a) == set(loader.EXPECTED_HEADER_KEYS)
        path = fixture.root / "expected-b2-header.json"
        path.write_bytes(b2.canonical_session_header_bytes(header_a))
        path.chmod(0o600)
        source = {
            "b2": b2,
            "profile": profile,
            "record_parent": record_parent,
            "header_a": header_a,
            "header_b": header_b,
            "header_c": header_c,
            "path": path,
            "certificate": candidate(fixture)["governance_certificate"].value,
        }
        source["certificate"]["session"] = _session_context_from_b2(source)
        yield source
    finally:
        setup.close()


def _session_context_from_b2(source):
    header = json.loads(source["path"].read_bytes())
    b2 = source["b2"]
    b2._validate_header(header)
    expected_session = {
        "session_id": header["session_id"],
        "schema_version": header["schema_version"],
        "header_sha256": b2.session_header_sha256(header),
    }
    return {
        **expected_session,
        "header_reference": {
            "path": str(source["path"]),
            "sha256": expected_session["header_sha256"],
            "schema_version": "s11-ex10-session-v2-header-projection-v1",
        },
    }


def _session_binding_proof(source, test_id, expected, error_code=None):
    destination = os.environ.get("S11_SESSION_BINDING_EVIDENCE")
    if destination:
        proof = {
            "test_id": test_id,
            "certificate": source["certificate"],
            "expected_session": {
                key: expected[key] for key in ("session_id", "schema_version", "header_sha256")
            },
            "expected_gov_session": expected,
            "header_source": json.loads(source["path"].read_bytes()),
            "header_a": source["header_a"],
            "header_b": source["header_b"],
            "header_c": source["header_c"],
            "header_fields_bound": 27,
            "header_sha_api": "ex10_session_v2.session_header_sha256",
            "error_code": error_code,
        }
        target = Path(destination) / ("session-proof-" + test_id + ".json")
        with target.open("x") as stream:
            json.dump(proof, stream, sort_keys=True, indent=2)
            stream.write("\n")


def _session_binding_reject(source, test_id, expected, code="GOV_BINDING"):
    with pytest.raises(sql.AuthorityError) as error:
        gov.validate_governance_certificate(source["certificate"], context={"session": expected})
    assert error.value.code == code
    _session_binding_proof(source, test_id, expected, error.value.code)


def test_session_binding_valid_b2_baseline(session_binding_source):
    source = session_binding_source
    expected = _session_context_from_b2(source)
    assert source["certificate"]["session"] == expected
    accepted = gov.validate_governance_certificate(
        source["certificate"], context={"session": expected}
    )
    assert accepted.value["session"] == expected
    _session_binding_proof(source, "SESSION-B001", expected)


def test_session_binding_wrong_id(session_binding_source):
    source = session_binding_source
    expected = _session_context_from_b2(source)
    source["certificate"]["session"]["session_id"] = "synthetic-wrong-session"
    assert set(
        key for key in expected if expected[key] != source["certificate"]["session"][key]
    ) == {"session_id"}
    _session_binding_reject(source, "SESSION-B002", expected)


def test_session_binding_wrong_schema(session_binding_source):
    source = session_binding_source
    expected = _session_context_from_b2(source)
    source["certificate"]["session"]["schema_version"] = "s11c3-ex10-official-launcher-session-v3"
    assert set(
        key for key in expected if expected[key] != source["certificate"]["session"][key]
    ) == {"schema_version"}
    _session_binding_reject(source, "SESSION-B003", expected, "GOV_SCHEMA")


def test_session_binding_other_valid_header(session_binding_source):
    source = session_binding_source
    a, other = source["header_a"], source["header_b"]
    assert a["session_id"] == other["session_id"]
    assert a["schema_version"] == other["schema_version"]
    assert {key for key in a if a[key] != other[key]} == {"created_at"}
    source["path"].write_bytes(source["b2"].canonical_session_header_bytes(other))
    expected = _session_context_from_b2(source)
    assert source["certificate"]["session"]["header_sha256"] != expected["header_sha256"]
    # The certificate is internally consistent and the locator/ID/schema equal;
    # only the actual expected B2 header's SHA pair differs.
    gov.validate_governance_certificate(source["certificate"])
    _session_binding_reject(source, "SESSION-B004", expected)


def test_session_binding_header_derived_from_b2(session_binding_source):
    source = session_binding_source
    expected = _session_context_from_b2(source)
    raw = source["path"].read_bytes()
    assert raw == source["b2"].canonical_session_header_bytes(source["header_a"])
    assert len(json.loads(raw)) == 27
    assert expected["header_sha256"] == source["b2"].session_header_sha256(source["header_a"])
    _session_binding_proof(source, "SESSION-B005", expected)


def test_session_binding_cross_session_mix(session_binding_source):
    source = session_binding_source
    expected = _session_context_from_b2(source)
    other = source["header_c"]
    assert other["session_id"] != expected["session_id"]
    assert other["schema_version"] == expected["schema_version"]
    digest_b = source["b2"].session_header_sha256(other)
    assert digest_b != expected["header_sha256"]
    mixed = source["certificate"]["session"]
    mixed["header_sha256"] = mixed["header_reference"]["sha256"] = digest_b
    assert mixed["session_id"] == expected["session_id"]
    assert mixed["schema_version"] == expected["schema_version"]
    gov.validate_governance_certificate(source["certificate"])
    _session_binding_reject(source, "SESSION-B006", expected)


def test_session_fixture_b2_pure_baseline(session_binding_source):
    source = session_binding_source
    b2, header, profile = source["b2"], source["header_a"], source["profile"]
    assert len(header) == len(b2.HEADER_KEYS) == 27
    assert set(header) == set(b2.HEADER_KEYS)
    raw = b2.canonical_session_header_bytes(header)
    expected_header_sha = b2.session_header_sha256(header)
    assert source["path"].read_bytes() == raw
    assert _session_context_from_b2(source)["header_sha256"] == expected_header_sha
    # All three APIs below are pure codecs: no operational record writer is called.
    session = b2.new_official_session_v2(header, profile=profile)
    loaded = b2.load_session_v2(
        session._bytes, profile=profile, expected_tip=expected_header_sha, expected_count=0
    )
    verified = b2.verify_official_session_v2(loaded, profile=profile)
    assert session.header == loaded.header == verified.header == header
    assert verified.to_dict()["header_sha256"] == expected_header_sha
    assert verified.to_dict()["history"] == []
    assert list(source["record_parent"].iterdir()) == []
    proof_path = os.environ.get("S11_SESSION_BASELINE_EVIDENCE")
    if proof_path is None:
        return  # The proof above is unconditional; writing a report is optional.
    with Path(proof_path).open("x") as stream:
        json.dump(
            {
                "header": header,
                "header_field_count": len(header),
                "header_fields": list(b2.HEADER_KEYS),
                "expected_header_sha": expected_header_sha,
                "sha_api": "ex10_session_v2.session_header_sha256",
                "canonical_header_api": "ex10_session_v2.canonical_session_header_bytes",
                "pure_load_and_verify": True,
                "operational_record_written": False,
            },
            stream,
            sort_keys=True,
            indent=2,
        )
        stream.write("\n")


@pytest.fixture
def profile_binding_source(session_binding_source):
    source = session_binding_source
    rp = source["b2"].rp
    # Reuse the accepted B2 fixture's actual B1 mapping. Validate it before
    # projecting any expected GOV profile claim; all B1 paths are outside /tmp.
    profile_a = rp.validate_runtime_profile(source["profile"])
    raw_a = rp.canonical_runtime_profile_bytes(profile_a)
    assert rp.parse_runtime_profile_bytes(raw_a).to_dict() == profile_a.to_dict()
    profile_b_value = profile_a.to_dict()
    profile_b_value["project"] = "synthetic-project-b"
    profile_b = rp.validate_runtime_profile(profile_b_value)
    assert rp.runtime_profile_sha256(profile_a) != rp.runtime_profile_sha256(profile_b)
    home = source["record_parent"].parent
    assert not str(home).startswith("/tmp/")
    source.update(
        rp=rp,
        profile_a=profile_a,
        profile_b=profile_b,
        profile_path=home / "expected-b1-profile.json",
        path=home / "expected-b2-profile-header.json",
    )
    header_a = source["header_a"]
    source["profile_header_a"] = header_a
    source["profile_header_b"] = source["b2"].new_session_v2_header(
        profile_b,
        session_id=header_a["session_id"],
        created_at=header_a["created_at"],
        branch=header_a["branch"],
        launcher_blobs=header_a["launcher_blobs"],
        runtime_profile_approval_reference=header_a["runtime_profile_approval_reference"],
        path_identity_binding=header_a["path_identity_binding"],
    )
    _write_profile_binding_sources(source, "a", "a")
    source["certificate"]["runtime_profile"] = _profile_context_from_b1(source)
    source["certificate"]["session"] = _session_context_from_b2(source)
    return source


def _write_profile_binding_sources(source, profile_name, session_name):
    profile = source["profile_" + profile_name]
    header = source["profile_header_" + session_name]
    session_profile = source["profile_" + session_name]
    source["profile_path"].write_bytes(source["rp"].canonical_runtime_profile_bytes(profile))
    source["profile_path"].chmod(0o600)
    source["path"].write_bytes(source["b2"].canonical_session_header_bytes(header))
    source["path"].chmod(0o600)
    # Pure in-memory codec: no create/update_official_session record writer.
    source["profile_session_bytes"] = (
        source["b2"].new_official_session_v2(header, profile=session_profile)._bytes
    )


def _profile_context_from_b1(source):
    profile = source["rp"].parse_runtime_profile_bytes(source["profile_path"].read_bytes())
    value = profile.to_dict()
    sha = source["rp"].runtime_profile_sha256(profile)
    return {
        "profile_id": value["profile_id"],
        "schema_version": value["schema_version"],
        "sha256": sha,
        "reference": {
            "path": str(source["profile_path"]),
            "schema_version": value["schema_version"],
            "sha256": sha,
        },
    }


def _profile_context_from_sources(source):
    profile = source["rp"].parse_runtime_profile_bytes(source["profile_path"].read_bytes())
    header = json.loads(source["path"].read_bytes())
    # Native B2 load enforces ID/schema/SHA and the complete B1 profile binding.
    loaded = source["b2"].load_session_v2(
        source["profile_session_bytes"],
        profile=profile,
        expected_tip=source["b2"].session_header_sha256(header),
        expected_count=0,
    )
    assert loaded.header == header
    return {
        "runtime_profile": _profile_context_from_b1(source),
        "session": _session_context_from_b2(source),
    }


def _validate_profile_bound_certificate(source):
    return gov.validate_governance_certificate(
        source["certificate"], context=_profile_context_from_sources(source)
    )


def _profile_binding_proof(source, test_id, error_code=None, error_api=None):
    destination = os.environ.get("S11_PROFILE_BINDING_EVIDENCE")
    if destination:
        expected = _profile_context_from_b1(source)
        proof = {
            "test_id": test_id,
            "certificate": source["certificate"],
            "expected_gov_profile": expected,
            "expected_profile": json.loads(source["profile_path"].read_bytes()),
            "profile_a": source["profile_a"].to_dict(),
            "profile_b": source["profile_b"].to_dict(),
            "profile_a_sha": source["rp"].runtime_profile_sha256(source["profile_a"]),
            "profile_b_sha": source["rp"].runtime_profile_sha256(source["profile_b"]),
            "session_header": json.loads(source["path"].read_bytes()),
            "session_header_sha": source["b2"].session_header_sha256(
                json.loads(source["path"].read_bytes())
            ),
            "expected_gov_session": _session_context_from_b2(source),
            "profile_sha_api": "ex10_runtime_profile.runtime_profile_sha256",
            "canonical_profile_api": "ex10_runtime_profile.canonical_runtime_profile_bytes",
            "cross_binding_api": "ex10_session_v2.load_session_v2(profile=expected_profile)",
            "error_code": error_code,
            "error_api": error_api,
            "operational_record_written": False,
        }
        with (Path(destination) / ("profile-proof-" + test_id + ".json")).open("x") as stream:
            json.dump(proof, stream, indent=2, sort_keys=True)
            stream.write("\n")


def test_profile_binding_native_b1_baseline(profile_binding_source):
    source = profile_binding_source
    raw = source["profile_path"].read_bytes()
    profile = source["rp"].parse_runtime_profile_bytes(raw)
    expected = _profile_context_from_b1(source)
    assert source["rp"].validate_runtime_profile(profile).to_dict() == source["profile_a"].to_dict()
    assert raw == source["rp"].canonical_runtime_profile_bytes(profile)
    assert expected["profile_id"] == profile.to_dict()["profile_id"]
    assert expected["schema_version"] == source["rp"].SCHEMA_VERSION
    assert expected["sha256"] == source["rp"].runtime_profile_sha256(profile)
    assert list(source["record_parent"].iterdir()) == []
    _profile_binding_proof(source, "PROFILE-B001")


def test_profile_binding_valid_b1_b2_baseline(profile_binding_source):
    source = profile_binding_source
    expected = _profile_context_from_sources(source)
    accepted = _validate_profile_bound_certificate(source)
    assert accepted.value["runtime_profile"] == expected["runtime_profile"]
    assert accepted.value["session"] == expected["session"]
    _profile_binding_proof(source, "PROFILE-B002")


def test_profile_binding_wrong_id(profile_binding_source):
    source = profile_binding_source
    before = copy.deepcopy(source["certificate"])
    source["certificate"]["runtime_profile"]["profile_id"] = "synthetic-wrong-profile"
    expected = _profile_context_from_b1(source)
    assert {
        key for key in expected if expected[key] != source["certificate"]["runtime_profile"][key]
    } == {"profile_id"}
    assert {key for key in before if before[key] != source["certificate"][key]} == {
        "runtime_profile"
    }
    with pytest.raises(sql.AuthorityError) as error:
        _validate_profile_bound_certificate(source)
    assert error.value.code == "GOV_BINDING"
    _profile_binding_proof(source, "PROFILE-B003", error.value.code, "GOV_EXPECTED_CONTEXT")


def test_profile_binding_wrong_schema(profile_binding_source):
    source = profile_binding_source
    expected = _profile_context_from_b1(source)
    source["certificate"]["runtime_profile"]["schema_version"] = "s11-ex10-runtime-profile-v2"
    assert {
        key for key in expected if expected[key] != source["certificate"]["runtime_profile"][key]
    } == {"schema_version"}
    with pytest.raises(sql.AuthorityError) as error:
        _validate_profile_bound_certificate(source)
    assert error.value.code == "GOV_SCHEMA"
    _profile_binding_proof(source, "PROFILE-B004", error.value.code, "GOV_CLOSED_SCHEMA")


def test_profile_binding_other_valid_profile_sha(profile_binding_source):
    source = profile_binding_source
    _write_profile_binding_sources(source, "b", "b")
    expected = _profile_context_from_sources(source)["runtime_profile"]
    actual = source["certificate"]["runtime_profile"]
    assert actual["profile_id"] == expected["profile_id"]
    assert actual["schema_version"] == expected["schema_version"]
    assert actual["reference"]["path"] == expected["reference"]["path"]
    assert actual["sha256"] == actual["reference"]["sha256"] != expected["sha256"]
    assert {
        key
        for key in source["profile_a"].to_dict()
        if source["profile_a"].to_dict()[key] != source["profile_b"].to_dict()[key]
    } == {"project"}
    gov.validate_governance_certificate(source["certificate"])
    with pytest.raises(sql.AuthorityError) as error:
        gov.validate_governance_certificate(
            source["certificate"], context={"runtime_profile": expected}
        )
    assert error.value.code == "GOV_BINDING"
    _profile_binding_proof(source, "PROFILE-B005", error.value.code, "GOV_EXPECTED_PROFILE_CONTEXT")


def test_profile_binding_sha_derived_from_b1(profile_binding_source):
    source = profile_binding_source
    profile = source["rp"].parse_runtime_profile_bytes(source["profile_path"].read_bytes())
    expected = _profile_context_from_sources(source)
    assert expected["runtime_profile"]["sha256"] == source["rp"].runtime_profile_sha256(profile)
    assert (
        expected["runtime_profile"]["reference"]["sha256"] == expected["runtime_profile"]["sha256"]
    )
    assert (
        expected["runtime_profile"]["sha256"]
        == json.loads(source["path"].read_bytes())["runtime_profile_sha256"]
    )
    _profile_binding_proof(source, "PROFILE-B006")


def test_profile_binding_profile_b_with_session_a(profile_binding_source):
    source = profile_binding_source
    _write_profile_binding_sources(source, "b", "a")
    source["certificate"]["runtime_profile"] = _profile_context_from_b1(source)
    # The cert matches B1 B and remains internally valid, but native B2 loading
    # must reject the expected B1 B paired with session A before GOV acceptance.
    gov.validate_governance_certificate(source["certificate"])
    with pytest.raises(source["b2"].OfficialSessionError) as error:
        _validate_profile_bound_certificate(source)
    assert error.value.code == "profile-binding"
    _profile_binding_proof(
        source, "PROFILE-B007", error.value.code, "NATIVE_B2_PROFILE_QUALIFICATION"
    )


def test_profile_binding_profile_a_with_session_b(profile_binding_source):
    source = profile_binding_source
    _write_profile_binding_sources(source, "a", "b")
    source["certificate"]["session"] = _session_context_from_b2(source)
    gov.validate_governance_certificate(source["certificate"])
    with pytest.raises(source["b2"].OfficialSessionError) as error:
        _validate_profile_bound_certificate(source)
    assert error.value.code == "profile-binding"
    _profile_binding_proof(
        source, "PROFILE-B008", error.value.code, "NATIVE_B2_PROFILE_QUALIFICATION"
    )


@pytest.fixture
def source_binding_source(fixture, profile_binding_source):
    source = profile_binding_source
    source["source_fixture"] = fixture
    profile_b_value = source["profile_a"].to_dict()
    profile_b_value["source_revision"] = (
        "b" * 40 if profile_b_value["source_revision"] != "b" * 40 else "c" * 40
    )
    source["profile_b"] = source["rp"].validate_runtime_profile(profile_b_value)
    header_a = source["profile_header_a"]
    source["profile_header_b"] = source["b2"].new_session_v2_header(
        source["profile_b"],
        session_id=header_a["session_id"],
        created_at=header_a["created_at"],
        branch=header_a["branch"],
        launcher_blobs=header_a["launcher_blobs"],
        runtime_profile_approval_reference=header_a["runtime_profile_approval_reference"],
        path_identity_binding=header_a["path_identity_binding"],
    )
    source["source_sets"] = {}
    for name in ("a", "b"):
        _write_profile_binding_sources(source, name, name)
        expected = _source_binding_context(source)
        args = candidate(fixture)
        value = args["governance_certificate"].value
        value.update(expected)
        args["governance_certificate"] = gov.validate_governance_certificate(
            value, context=expected
        )
        # Reuse the accepted synthetic OPEN row and UUID5-shaped run_id.
        # Only the source claim is derived here; no real run/launcher is created.
        args["run"]["source_revision"] = expected["source_revision"]
        additions = []
        for claim, kind, field in (
            (value["runtime_profile"]["reference"], "PROFILE", "profile_reference_id"),
            (value["session"]["header_reference"], "SESSION", "session_reference_id"),
        ):
            row = b.artifact(claim, kind)
            row["introduced_revision"] = value["authority_revision_after"]
            additions.append(row)
            args["run"][field] = row["reference_id"]
        args["reference_additions"] = tuple(additions)
        rebind(args)
        assert args["run"]["run_id"] == value["run_id"] == NEW_RUN
        source["source_sets"][name] = {"args": args, "expected_source": expected["source_revision"]}
    assert (
        source["source_sets"]["a"]["expected_source"]
        != source["source_sets"]["b"]["expected_source"]
    )
    _source_binding_select(source, "a")
    return source


def _source_binding_context(source, expected_from="profile"):
    profile = source["rp"].parse_runtime_profile_bytes(source["profile_path"].read_bytes())
    header = json.loads(source["path"].read_bytes())
    loaded = source["b2"].load_session_v2(
        source["profile_session_bytes"],
        profile=profile,
        expected_tip=source["b2"].session_header_sha256(header),
        expected_count=0,
    )
    assert loaded.header == header
    revision = (
        profile.to_dict()["source_revision"]
        if expected_from == "profile"
        else loaded.header["expected_git_sha"]
    )
    # Check the source claim first, with caller-qualified native B1/B2 evidence.
    return {"source_revision": revision, **_profile_context_from_sources(source)}


def _source_binding_select(source, name):
    _write_profile_binding_sources(source, name, name)
    source["source_args"] = clone_arguments(source["source_sets"][name]["args"])
    source["certificate"] = source["source_args"]["governance_certificate"].value


def _source_binding_reseal(source):
    args = source["source_args"]
    args["governance_certificate"] = gov.validate_governance_certificate(source["certificate"])
    rebind(args)


def _source_binding_validate(source, expected_from="profile"):
    return gov.validate_governance_certificate(
        source["certificate"], context=_source_binding_context(source, expected_from)
    )


def _source_binding_proof(source, test_id, error_code=None, error_api=None, published_run=None):
    destination = os.environ.get("S11_SOURCE_BINDING_EVIDENCE")
    if destination:
        profile = source["rp"].parse_runtime_profile_bytes(source["profile_path"].read_bytes())
        header = json.loads(source["path"].read_bytes())
        args = source["source_args"]
        proof = {
            "test_id": test_id,
            "profile": profile.to_dict(),
            "header": header,
            "header_sha": source["b2"].session_header_sha256(header),
            "certificate": source["certificate"],
            "run_claim": args["run"],
            "published_run": published_run,
            "governance_reference": args["governance_certificate_reference"],
            "source_revision_fields": {
                "b1": "source_revision",
                "b2": "expected_git_sha",
                "gov": "source_revision",
                "run": "source_revision",
            },
            "source_s1": source["source_sets"]["a"]["expected_source"],
            "source_s2": source["source_sets"]["b"]["expected_source"],
            "profile_a": source["profile_a"].to_dict(),
            "profile_b": source["profile_b"].to_dict(),
            "valid_header_a": source["profile_header_a"],
            "valid_header_b": source["profile_header_b"],
            "error_code": error_code,
            "error_api": error_api,
            "publication_adapter_calls": args["adapter"].calls,
            "real_operational_run_created": False,
            "native_source_qualification_api": (
                "ex10_session_v2.load_session_v2(profile=expected_B1)"
            ),
            "expected_source_api": (
                "ex10_runtime_profile.parse_runtime_profile_bytes(...)"
                ".to_dict()[source_revision]"
            ),
            "run_id_unchanged_from_existing_synthetic_fixture": args["run"]["run_id"] == NEW_RUN,
        }
        with (Path(destination) / ("source-proof-" + test_id + ".json")).open("x") as stream:
            json.dump(proof, stream, sort_keys=True, indent=2)
            stream.write("\n")


@pytest.mark.parametrize("name", ["a", "b"])
def test_source_binding_valid_chain(source_binding_source, name):
    source = source_binding_source
    _source_binding_select(source, name)
    expected = _source_binding_context(source)["source_revision"]
    args = source["source_args"]
    args["governance_certificate"] = _source_binding_validate(source)
    result = opened(args)
    run = result.material["state"]["official_runs"][0]
    profile = source["rp"].parse_runtime_profile_bytes(source["profile_path"].read_bytes())
    header = json.loads(source["path"].read_bytes())
    assert (
        profile.to_dict()["source_revision"]
        == header["expected_git_sha"]
        == source["certificate"]["source_revision"]
        == args["run"]["source_revision"]
        == run["source_revision"]
        == expected
    )
    stored = sql._load_database(source["source_fixture"].database)["official_runs"]
    assert stored == [run]
    assert args["adapter"].calls == ["intent", "witness", "commit"]
    _source_binding_proof(source, "SOURCE-B001-" + name, published_run=run)


def test_source_binding_native_b2_revision_mismatch(source_binding_source):
    source = source_binding_source
    original_header = source["profile_header_a"]
    header = copy.deepcopy(original_header)
    header["expected_git_sha"] = source["source_sets"]["b"]["expected_source"]
    assert {key for key in header if header[key] != original_header[key]} == {"expected_git_sha"}
    value = json.loads(source["profile_session_bytes"])
    value["expected_git_sha"] = header["expected_git_sha"]
    value["header_sha256"] = source["b2"].session_header_sha256(header)
    source["profile_session_bytes"] = source["b2"]._dump(value)
    source["path"].write_bytes(source["b2"].canonical_session_header_bytes(header))
    # Shape/header/checkpoint are valid; only the expected B1 source disagrees.
    standalone = source["b2"].load_session_v2(
        source["profile_session_bytes"], expected_tip=value["header_sha256"], expected_count=0
    )
    assert standalone.header == header
    with pytest.raises(source["b2"].OfficialSessionError) as error:
        _source_binding_validate(source)
    assert error.value.code == "profile-binding"
    assert source["source_args"]["adapter"].calls == []
    _source_binding_proof(
        source, "SOURCE-B002", error.value.code, "NATIVE_B2_SOURCE_PROFILE_BINDING"
    )


def test_source_binding_profile_certificate_mismatch(source_binding_source):
    source = source_binding_source
    before = copy.deepcopy(source["certificate"])
    source["certificate"]["source_revision"] = source["source_sets"]["b"]["expected_source"]
    assert {key for key in before if before[key] != source["certificate"][key]} == {
        "source_revision"
    }
    _source_binding_reseal(source)
    with pytest.raises(sql.AuthorityError) as error:
        _source_binding_validate(source, "profile")
    assert error.value.code == "GOV_BINDING"
    assert source["source_args"]["adapter"].calls == []
    _source_binding_proof(source, "SOURCE-B003", error.value.code, "GOV_EXPECTED_B1_SOURCE_CONTEXT")


def test_source_binding_session_certificate_mismatch(source_binding_source):
    source = source_binding_source
    revision_b = source["source_sets"]["b"]["expected_source"]
    source["certificate"]["source_revision"] = revision_b
    source["source_args"]["run"]["source_revision"] = revision_b
    _source_binding_reseal(source)
    assert (
        source["certificate"]["source_revision"] == source["source_args"]["run"]["source_revision"]
    )
    with pytest.raises(sql.AuthorityError) as error:
        _source_binding_validate(source, "session")
    assert error.value.code == "GOV_BINDING"
    assert source["source_args"]["adapter"].calls == []
    _source_binding_proof(source, "SOURCE-B004", error.value.code, "GOV_EXPECTED_B2_SOURCE_CONTEXT")


def test_source_binding_certificate_run_mismatch(source_binding_source):
    source = source_binding_source
    args = source["source_args"]
    args["run"]["source_revision"] = source["source_sets"]["b"]["expected_source"]
    args["governance_certificate"] = _source_binding_validate(source)
    with pytest.raises(sql.AuthorityError) as error:
        opened(args)
    assert error.value.code == "GOV_BINDING"
    assert args["adapter"].calls == []
    assert sql._load_database(source["source_fixture"].database)["official_runs"] == []
    _source_binding_proof(
        source, "SOURCE-B005", error.value.code, "NATIVE_B3_CERTIFICATE_RUN_SOURCE_BINDING"
    )


@pytest.mark.parametrize("component", ["gov_b", "session_b"])
def test_source_binding_cross_mix(source_binding_source, component):
    source = source_binding_source
    if component == "gov_b":
        source["source_args"] = clone_arguments(source["source_sets"]["b"]["args"])
        source["source_args"]["run"] = copy.deepcopy(source["source_sets"]["a"]["args"]["run"])
        source["certificate"] = source["source_args"]["governance_certificate"].value
        with pytest.raises(sql.AuthorityError) as error:
            _source_binding_validate(source)
        assert error.value.code == "GOV_BINDING"
        error_api = "GOV_EXPECTED_B1_SOURCE_CROSS_MIX"
    else:
        _write_profile_binding_sources(source, "a", "b")
        with pytest.raises(source["b2"].OfficialSessionError) as error:
            _source_binding_validate(source)
        assert error.value.code == "profile-binding"
        error_api = "NATIVE_B2_SOURCE_CROSS_MIX"
    assert source["source_args"]["adapter"].calls == []
    _source_binding_proof(source, "SOURCE-B006-" + component, error.value.code, error_api)


# The approved B4 identity is an external frozen executor tuple, not the
# differently shaped ex10_executor.run_identity protocol metadata object.
RUN_IDENTITY_SCHEMA = B4_RESERVATION_DESIGN["$defs"]["reservation"]["properties"]["run_identity"]
RUN_IDENTITY_PREIMAGE_FIELDS = tuple(
    key for key in RUN_IDENTITY_SCHEMA["required"] if key != "identity_digest"
)


def _derive_run_binding_identity(inputs):
    assert set(inputs) == set(RUN_IDENTITY_PREIMAGE_FIELDS) - {"run_id"}
    sql.utc_time(inputs["started_at"])
    manifest = importlib.import_module(history.__package__ + ".manifest")
    identity = copy.deepcopy(inputs)
    identity["run_id"] = manifest.make_run_id(
        started_at=b.datetime.fromisoformat(inputs["started_at"].replace("Z", "+00:00")),
        git_sha=inputs["git_sha"],
        dataset_id=inputs["dataset_id"],
        scenario_id=inputs["scenario_id"],
        seed=inputs["seed"],
        execution_nonce=inputs["execution_nonce"],
    )
    # Approved B4 preimage: canonical whole identity excluding identity_digest;
    # includes the derived run_id, has one LF, and has no domain prefix.
    identity["identity_digest"] = sql.digest(
        {key: identity[key] for key in RUN_IDENTITY_PREIMAGE_FIELDS}
    )
    sql.validate_schema(identity, RUN_IDENTITY_SCHEMA)
    return identity


@pytest.fixture
def run_binding_source(fixture):
    # Reuse only reservation fixture setup, never a previous test function.
    source = _reservation_binding_fixture(fixture)
    record = sql.parse_canonical(source["record_path"].read_bytes())
    inputs = {
        key: record["run_identity"][key] for key in RUN_IDENTITY_PREIMAGE_FIELDS if key != "run_id"
    }
    identity_a = _derive_run_binding_identity(inputs)
    assert identity_a == record["run_identity"]
    assert identity_a["git_sha"] == record["source_revision"]
    inputs_b = {**inputs, "execution_nonce": b.hashed("run-binding-identity-b-nonce")}
    identity_b = _derive_run_binding_identity(inputs_b)
    assert identity_a["run_id"] != identity_b["run_id"]
    assert identity_a["identity_digest"] != identity_b["identity_digest"]
    paths = {}
    for name, identity in (("a", identity_a), ("b", identity_b)):
        path = fixture.root / ("expected-run-identity-" + name + ".json")
        with path.open("xb") as output:
            output.write(sql.canonical_bytes(identity))
        path.chmod(0o600)
        paths[name] = path
    source["run_identity_paths"] = paths
    return source


def _run_binding_context(source, name):
    # The expected object is read independently from the certificate. Native
    # product codecs and UUID5/digest functions qualify the approved preimage.
    _reservation_context_from_sources(source)
    record = sql.parse_canonical(source["record_path"].read_bytes())
    identity = sql.parse_canonical(source["run_identity_paths"][name].read_bytes())
    sql.validate_schema(identity, RUN_IDENTITY_SCHEMA)
    inputs = {key: identity[key] for key in RUN_IDENTITY_PREIMAGE_FIELDS if key != "run_id"}
    assert identity == _derive_run_binding_identity(inputs)
    assert identity["git_sha"] == record["source_revision"]
    return {
        "run_id": identity["run_id"],
        "run_identity_digest": identity["identity_digest"],
        "source_revision": record["source_revision"],
    }


def _run_binding_certificate(source, name):
    value = copy.deepcopy(source["certificate"])
    value.update(_run_binding_context(source, name))
    value["certificate_id"] = gov.certificate_identity(value)
    return value


def _run_binding_proof(source, test_id, certificate, expected_name, error_code=None):
    destination = os.environ.get("S11_RUN_BINDING_EVIDENCE")
    if destination:
        identities = {
            name: sql.parse_canonical(path.read_bytes())
            for name, path in source["run_identity_paths"].items()
        }
        record = sql.parse_canonical(source["record_path"].read_bytes())
        cert = gov.validate_governance_certificate(certificate)
        proof = {
            "test_id": test_id,
            "identity_a": identities["a"],
            "identity_b": identities["b"],
            "identity_source_bytes": {
                name: path.read_text() for name, path in source["run_identity_paths"].items()
            },
            "identity_source_sha256": {
                name: hashlib.sha256(path.read_bytes()).hexdigest()
                for name, path in source["run_identity_paths"].items()
            },
            "preimage_fields": list(RUN_IDENTITY_PREIMAGE_FIELDS),
            "domain_separator": None,
            "expected_context": _run_binding_context(source, expected_name),
            "expected_identity_name": expected_name,
            "certificate": certificate,
            "certificate_sha256": gov.governance_certificate_sha256(cert),
            "expected_source_revision": record["source_revision"],
            "error_code": error_code,
            "standalone_certificate_shape_valid": True,
            "derivation_apis": ["manifest.make_run_id", "ex10_authority_sqlite.digest"],
            "validation_api": "validate_governance_certificate(..., context=expected)",
            "native_open_called": False,
            "b4_operational_qualification_claimed": False,
            "real_operational_run_created": False,
        }
        with (Path(destination) / ("run-proof-" + test_id + ".json")).open("x") as output:
            json.dump(proof, output, sort_keys=True, indent=2)
            output.write("\n")


@pytest.mark.parametrize("name", ["a", "b"])
def test_run_binding_valid_identity(run_binding_source, name):
    source = run_binding_source
    value = _run_binding_certificate(source, name)
    expected = _run_binding_context(source, name)
    cert = gov.validate_governance_certificate(value, context=expected)
    assert cert.value["run_id"] == expected["run_id"]
    assert cert.value["run_identity_digest"] == expected["run_identity_digest"]
    assert cert.value["source_revision"] == expected["source_revision"]
    _run_binding_proof(source, "RUN-B001-" + name, value, name)


@pytest.mark.parametrize("name", ["a", "b"])
def test_run_binding_valid_wrong_digest_cross_identity(run_binding_source, name):
    source = run_binding_source
    value = _run_binding_certificate(source, name)
    expected = _run_binding_context(source, name)
    other = _run_binding_context(source, "b" if name == "a" else "a")
    value["run_identity_digest"] = other["run_identity_digest"]
    # Only the digest claim differs; both identities/digests are natively derived
    # and valid. Whole certificate shape/canonicalization still succeeds.
    cert = gov.validate_governance_certificate(value)
    assert cert.value["run_id"] == expected["run_id"]
    assert cert.value["source_revision"] == expected["source_revision"]
    assert cert.value["run_identity_digest"] != expected["run_identity_digest"]
    with pytest.raises(sql.AuthorityError) as error:
        gov.validate_governance_certificate(value, context=expected)
    assert error.value.code == "GOV_BINDING"
    _run_binding_proof(source, "RUN-B002-" + name, value, name, error.value.code)


def test_run_binding_identity_source_revision(run_binding_source):
    source = run_binding_source
    identity_a = sql.parse_canonical(source["run_identity_paths"]["a"].read_bytes())
    expected = _run_binding_context(source, "a")
    assert "git_sha" in RUN_IDENTITY_PREIMAGE_FIELDS
    assert identity_a["git_sha"] == expected["source_revision"]
    inputs = {key: identity_a[key] for key in RUN_IDENTITY_PREIMAGE_FIELDS if key != "run_id"}
    inputs["git_sha"] = "b" * 40 if inputs["git_sha"] != "b" * 40 else "c" * 40
    changed = _derive_run_binding_identity(inputs)
    assert {k for k in inputs if inputs[k] != identity_a[k]} == {"git_sha"}
    assert changed["run_id"] != identity_a["run_id"]
    assert changed["identity_digest"] != identity_a["identity_digest"]
    value = _run_binding_certificate(source, "a")
    gov.validate_governance_certificate(value, context=expected)
    _run_binding_proof(source, "RUN-B003", value, "a")
    if destination := os.environ.get("S11_RUN_BINDING_EVIDENCE"):
        with (Path(destination) / "run-source-sensitivity.json").open("x") as output:
            json.dump({"identity_a": identity_a, "changed_source_identity": changed}, output)
            output.write("\n")


def test_fixture_loader_has_independent_namespace():
    """Repeated B3 imports must not replace the standalone suite's modules."""
    assert b.PACKAGE.__name__ == "b3_contract_fixture_" + _B3_FIXTURE_NAME
    assert b.sql.__package__ == b.PACKAGE.__name__
    assert history.__package__ == b.PACKAGE.__name__
    assert sys.modules[b.sql.__name__] is b.sql
    assert sys.modules[history.__name__] is history


def test_vendored_contract_is_self_contained():
    assert len(PLAN["tests"]) == 54
    assert len(set(PATHS)) == 74
    assert len(B4_RESERVATION_DESIGN["$defs"]["open_evidence"]["required"]) == 16
    assert CONTRACT["provenance"]["reservation-schema.json"] == (
        "bc431fe7b242f1a21f2f635123a33776d080b185ed24079de13a1ae313ff826b"
    )


def _harness_module():
    spec = importlib.util.spec_from_file_location("w00_harness_review", APP / "tests/conftest.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_harness_preserves_explicit_roots_without_home(monkeypatch):
    harness = _harness_module()
    for name in ("S11_B1_TEST_BASE", "S11_B2_TEST_BASE", "S11_B3_TEST_BASE"):
        monkeypatch.setenv(name, "/explicit-existing-root/" + name)
    monkeypatch.setattr(harness, "_default_fixture_parent", lambda: pytest.fail("HOME consulted"))
    setup = harness.s11_synthetic_roots.__wrapped__()
    assert next(setup) is None
    with pytest.raises(StopIteration):
        next(setup)
    for name in ("S11_B1_TEST_BASE", "S11_B2_TEST_BASE", "S11_B3_TEST_BASE"):
        assert os.environ[name] == "/explicit-existing-root/" + name


@pytest.mark.parametrize("home", ["/tmp", "/var/tmp", str(APP.parents[2]), str(APP)])
def test_harness_rejects_default_parent_inside_protected_roots(monkeypatch, home):
    harness = _harness_module()
    monkeypatch.setattr(harness.Path, "home", lambda: Path(home))
    with pytest.raises(RuntimeError, match="outside checkout"):
        harness._default_fixture_parent()


class ExactAckFixture(b.Fixture):
    def run(self, kind, run_id=b.RUN_ID):
        row = super().run(kind, run_id)
        if kind == "ND":
            row["run_id"] = (
                ND_RUN if run_id == b.RUN_ID else "s11-33333333-4444-5555-8666-777777777777"
            )
        return row


def _qualified_ack_set(snapshot):
    revision = history.current_revision(snapshot)["revision"]
    result = []
    for run in history.list_official_runs(snapshot):
        target = history.verify_ack_target(snapshot, run["run_id"])
        if target["recognizable"]:
            ref = target["evidence_reference"]
            result.append(
                {
                    "run_id": target["run_id"],
                    "authority_revision": revision,
                    "terminal_evidence_reference": {
                        key: ref[key] for key in ("path", "sha256", "schema_version")
                    },
                }
            )
    return sorted(result, key=lambda ack: ack["run_id"])


def test_ack_exact_complete_qualified_nd_set_can_publish(fixture):
    with _nested_b3_test_directory(fixture.root) as root:
        prior = ExactAckFixture(Path(root), ("ND", "ND"), create_db=True)
        verified = prior.verify()
        assert history.first_valid_status(verified)["status"] == "NONE"
        acknowledgments = _qualified_ack_set(verified)
        assert len(acknowledgments) == 2
        args = candidate(prior, verified)
        value = args["governance_certificate"].value
        value.update(
            acknowledgments=acknowledgments, acknowledgment_digest=sql.digest(acknowledgments)
        )
        args["governance_certificate"] = gov.validate_governance_certificate(value)
        rebind(args)
        result = opened(args)
        assert history.current_revision(result)["revision"] == 1
        assert prior.state["official_runs"][0]["binding"] is False
        assert args["adapter"].calls == ["intent", "witness", "commit"]


@pytest.mark.parametrize(
    "change", ["missing", "extra", "duplicate", "unknown", "reference", "revision"]
)
def test_ack_wrong_complete_set_rejected_before_publication(fixture, change):
    with _nested_b3_test_directory(fixture.root) as root:
        prior = ExactAckFixture(Path(root), ("ND", "ND"), create_db=True)
        verified = prior.verify()
        acks = _qualified_ack_set(verified)
        args = candidate(prior, verified)
        value = args["governance_certificate"].value
        if change == "missing":
            acks.pop()
        elif change in {"extra", "unknown"}:
            extra = copy.deepcopy(acks[0])
            extra["run_id"] = "s11-44444444-5555-5666-8777-888888888888"
            acks.append(extra)
            acks.sort(key=lambda ack: ack["run_id"])
        elif change == "duplicate":
            acks.append(copy.deepcopy(acks[0]))
        elif change == "reference":
            acks[0]["terminal_evidence_reference"]["sha256"] = b.hashed("other-terminal")
        else:
            acks[0]["authority_revision"] += 1
        value.update(acknowledgments=acks, acknowledgment_digest=sql.digest(acks))
        with pytest.raises(sql.AuthorityError):
            args["governance_certificate"] = gov.validate_governance_certificate(value)
            rebind(args)
            opened(args)
        assert args["adapter"].calls == []
        assert history.current_revision(verified)["revision"] == 0


@pytest.mark.parametrize("kind", ["PASS", "FAIL", "OPEN", "ACTIVE", "UNCERTAIN"])
def test_first_valid_qualified_non_none_blocks_governance_publication(fixture, kind):
    with _nested_b3_test_directory(fixture.root) as root:
        prior = b.Fixture(Path(root), (kind,), create_db=True)
        verified = prior.verify()
        expected = (
            kind + "_BINDING"
            if kind in {"PASS", "FAIL"}
            else "UNCERTAIN_BLOCKER"
            if kind == "UNCERTAIN"
            else "ACTIVE_BLOCKER"
        )
        assert history.first_valid_status(verified)["status"] == expected
        target = history.verify_ack_target(verified, b.RUN_ID)
        assert target["recognizable"] is False
        args = candidate(prior, verified)
        with pytest.raises(sql.AuthorityError, match="UNCERTAIN_PUBLICATION"):
            opened(args)
        assert args["adapter"].calls == []


def test_expired_base_is_history_invalid_and_cannot_seal_governance(fixture, monkeypatch):
    verified = fixture.verify()
    assert history.first_valid_status(verified)["status"] == "NONE"
    args = candidate(fixture, verified)
    monkeypatch.setattr(history, "_now", lambda: verified.expires_at)
    assert history.first_valid_status(verified)["status"] == "HISTORY_INVALID"
    with pytest.raises(sql.AuthorityError, match="EXPIRED_OBSERVATION"):
        opened(args)
    assert args["adapter"].calls == []


@pytest.mark.parametrize("table", ["certificate", "profile", "session", "run", "event"])
def test_each_introduced_revision_bound_before_any_publication(fixture, table):
    args = candidate(fixture)
    if table == "certificate":
        args["governance_certificate_reference"]["introduced_revision"] = 2
    elif table in {"profile", "session"}:
        index = 0 if table == "profile" else 1
        args["reference_additions"][index]["introduced_revision"] = 2
    elif table == "run":
        args["run"]["introduced_revision"] = 2
    else:
        args["event"]["revision"] = 2
    with pytest.raises(sql.AuthorityError):
        opened(args)
    assert args["adapter"].calls == []


def _load_downstream_builder(name, filename):
    spec = importlib.util.spec_from_file_location(name, APP / "tests" / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def qualified_evidence_source():
    builder = _load_downstream_builder("w03_evidence_builder", "test_ex10_open_evidence.py")
    setup = builder.sample.__wrapped__()
    sample = next(setup)
    try:
        yield builder, sample
    finally:
        setup.close()


@pytest.mark.parametrize(
    "field", ["none", "sha256", "run_id", "run_identity_digest", "reserve_request_id"]
)
def test_governance_evidence_binding_uses_w01_qualified_artifacts(
    fixture, qualified_evidence_source, field
):
    builder, sample = qualified_evidence_source
    api = builder.evd
    with api.ConfinedArtifactResolver(str(sample["root"]), expected_owner=os.geteuid()) as resolver:
        source = api.verify_open_evidence(
            sample["raw"],
            expected_context=sample["context"],
            executor_public_key=sample["key"].public_key(),
            artifact_resolver=resolver,
        )
    receipt = source.receipt.to_dict()
    path = sample["root"] / "open-evidence.json"
    path.write_bytes(sample["raw"])
    path.chmod(0o600)
    expected = {
        "open_evidence_reference": {
            "path": str(path),
            "sha256": source.sha256,
            "schema_version": receipt["schema_version"],
        },
        "run_id": receipt["run_id"],
        "run_identity_digest": receipt["identity_digest"],
        "reserve_request_id": receipt["reserve_request_id"],
    }
    assert source.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert source.sha256 != source.receipt_digest
    value = candidate(fixture)["governance_certificate"].value
    value.update(copy.deepcopy(expected))
    gov.validate_governance_certificate(value, context=expected)
    if field == "none":
        return
    if field == "sha256":
        value["open_evidence_reference"]["sha256"] = source.receipt_digest
    elif field == "run_id":
        value[field] = NEW_RUN
    elif field == "reserve_request_id":
        value[field] = b.OP_ZERO
    else:
        value[field] = b.hashed("wrong-identity")
    gov.validate_governance_certificate(value)
    with pytest.raises(sql.AuthorityError, match="GOV_BINDING"):
        gov.validate_governance_certificate(value, context=expected)


@pytest.mark.parametrize(
    "field",
    [
        "none",
        "reserve_request_id",
        "reserve_request_digest",
        "commit_request_id",
        "commit_request_digest",
    ],
)
def test_governance_request_claims_use_authenticated_b4_preimages(fixture, field):
    builder = _load_downstream_builder("w03_bridge_builder", "test_ex10_bridge_protocol.py")
    reserve = builder.sample_for("reserve-start")
    commit = builder.sample_for("commit-open")
    # Distinct requests/nonces are authenticated independently against their
    # externally supplied expected context and pins, before any GOV claim.
    value = copy.deepcopy(commit["request"])
    value["request_id"] = b.OP_NEXT
    value["capability"]["request_id"] = b.OP_NEXT
    commit["bridge"], commit["holder"], commit["pin"] = (
        reserve["bridge"],
        reserve["holder"],
        reserve["pin"],
    )
    holder_hex = reserve["context"].holder_public_key_hex
    value["capability"]["holder_public_key_hex"] = holder_hex
    commit["context"] = builder.replace(
        commit["context"], request_id=b.OP_NEXT, holder_public_key_hex=holder_hex
    )
    commit["raw"] = builder.signed_request(value, commit["bridge"], commit["holder"])
    accepted_reserve = builder.verify(reserve).request
    accepted_commit = builder.verify(commit).request
    r, c = accepted_reserve.to_dict(), accepted_commit.to_dict()
    assert r["request_id"] != c["request_id"] and r["nonce"] != c["nonce"]
    assert all(
        r[key] == c[key]
        for key in (
            "session_id",
            "session_header_sha256",
            "runtime_profile_sha256",
            "authority_id",
            "authority_epoch",
            "authority_revision",
            "source_revision",
            "project",
        )
    )
    expected = {
        "reserve_request_id": r["request_id"],
        "reserve_request_digest": builder.b4.request_digest(accepted_reserve),
        "commit_request_id": c["request_id"],
        "commit_request_digest": builder.b4.request_digest(accepted_commit),
    }
    assert expected["reserve_request_digest"] != accepted_reserve.sha256
    assert expected["commit_request_digest"] != accepted_commit.sha256
    cert = candidate(fixture)["governance_certificate"].value
    cert.update(copy.deepcopy(expected))
    gov.validate_governance_certificate(cert, context=expected)
    if field == "none":
        return
    cert[field] = (
        b.OP_ZERO
        if field.endswith("_id")
        else accepted_commit.sha256
        if field == "commit_request_digest"
        else accepted_reserve.sha256
    )
    gov.validate_governance_certificate(cert)
    with pytest.raises(sql.AuthorityError, match="GOV_BINDING"):
        gov.validate_governance_certificate(cert, context=expected)


@pytest.mark.parametrize("field", ["none", "permit_id", "permit_digest", "sha256"])
def test_governance_open_one_binding_uses_verified_permit(fixture, field):
    builder = _load_downstream_builder("w03_permit_builder", "test_ex10_bridge_protocol.py")
    sample = builder.sample_for("reserve-start")
    value, raw, context, pin = builder.permit_sample(sample)
    permit = builder.b4.verify_open_one(
        raw,
        expected_context=context,
        bridge_pin=pin,
        holder_public_key=sample["holder"].public_key(),
    )
    parsed = permit.to_dict()
    path = fixture.root / "open-one.json"
    path.write_bytes(raw)
    path.chmod(0o600)
    expected = {
        "open_one_permit": {
            "permit_id": parsed["permit_id"],
            "permit_digest": builder.b4.open_permit_digest(permit),
            "reference": {
                "path": str(path),
                "sha256": permit.sha256,
                "schema_version": parsed["schema_version"],
            },
        }
    }
    assert expected["open_one_permit"]["permit_digest"] != permit.sha256
    cert = candidate(fixture)["governance_certificate"].value
    cert.update(copy.deepcopy(expected))
    gov.validate_governance_certificate(cert, context=expected)
    if field == "none":
        return
    claim = cert["open_one_permit"]
    if field == "sha256":
        claim["reference"]["sha256"] = claim["permit_digest"]
    elif field == "permit_id":
        claim[field] = b.OP_ZERO
    else:
        claim[field] = permit.sha256
    gov.validate_governance_certificate(cert)
    with pytest.raises(sql.AuthorityError, match="GOV_BINDING"):
        gov.validate_governance_certificate(cert, context=expected)


@pytest.mark.parametrize("field", ["none", "receipt", "head", "ledger", "authority", "revision"])
def test_governance_b3_witness_sources_are_verified_and_bound(fixture, field):
    verified = fixture.verify()
    receipt = history._receipts(verified.material["state"])[-1]
    assert b.wit.verify_witness(receipt, fixture.pin) == sql.digest(receipt)
    receipt_path, head_path = fixture.root / "base-receipt.json", fixture.root / "base-head.json"
    receipt_path.write_bytes(sql.canonical_bytes(receipt))
    head_path.write_bytes(verified.canonical_current_head)
    receipt_path.chmod(0o600)
    head_path.chmod(0o600)
    current = history.current_revision(verified)
    expected = {
        "b3_base_witness": {
            "authority_id": current["authority_id"],
            "authority_epoch": current["epoch"],
            "revision": current["revision"],
            "ledger_digest": current["ledger_digest"],
            "witness_digest": current["witness_digest"],
            "receipt_reference": {
                "path": str(receipt_path),
                "sha256": sql.digest(receipt),
                "schema_version": receipt["witness_schema_version"],
            },
        },
        "b3_base_current_head_reference": {
            "path": str(head_path),
            "sha256": hashlib.sha256(head_path.read_bytes()).hexdigest(),
            "schema_version": b.wit.HEAD_SCHEMA,
        },
    }
    args = candidate(fixture, verified)
    value = args["governance_certificate"].value
    value.update(copy.deepcopy(expected))
    gov.validate_governance_certificate(value, context=expected)
    if field == "none":
        args["governance_certificate"] = gov.validate_governance_certificate(value)
        rebind(args)
        assert history.current_revision(opened(args))["revision"] == 1
        return
    base = value["b3_base_witness"]
    if field == "receipt":
        base["witness_digest"] = base["receipt_reference"]["sha256"] = b.hashed("foreign-receipt")
    elif field == "head":
        value["b3_base_current_head_reference"]["sha256"] = b.hashed("foreign-head")
    elif field == "ledger":
        base["ledger_digest"] = b.hashed("foreign-ledger")
    elif field == "authority":
        base["authority_id"] = value["authority_id"] = b.OP_ZERO
        value["certificate_id"] = gov.certificate_identity(value)
    else:
        base["revision"] = value["authority_revision_before"] = 1
        value["authority_revision_after"] = 2
    gov.validate_governance_certificate(value)
    with pytest.raises(sql.AuthorityError, match="GOV_BINDING"):
        gov.validate_governance_certificate(value, context=expected)
    args["governance_certificate"] = gov.validate_governance_certificate(value)
    args["governance_certificate_reference"]["introduced_revision"] = value[
        "authority_revision_after"
    ]
    rebind(args)
    with pytest.raises(sql.AuthorityError):
        opened(args)
    assert args["adapter"].calls == []


@pytest.mark.parametrize("field", ["none", "sequence", "token", "record", "receipt", "base"])
def test_governance_b4_reserve_witness_uses_product_qualifiers(fixture, field):
    source = _reservation_binding_fixture(fixture)
    builder = _load_downstream_builder("w03_reservation_builder", "test_ex10_bridge_protocol.py")
    api = builder.b4
    record = api.qualify_reservation(source["record_path"].read_bytes())
    receipt_value = source["receipt"]
    context = api.ReceiptContext(
        builder.wire(
            {k: v for k, v in receipt_value.items() if k not in {"issued_at", "signature"}}
        ),
        b.datetime.fromisoformat(b.NOW.replace("Z", "+00:00")),
    )
    pin = api.PublicKeyPin(
        receipt_value["key_id"], receipt_value["key_epoch"], fixture.key.public_key()
    )
    receipt = api.verify_reservation_receipt(
        source["receipt_path"].read_bytes(), expected_context=context, witness_pin=pin
    )
    parsed_record, parsed_receipt = record.to_dict(), receipt.to_dict()
    assert parsed_record["reservation_digest"] == api.reservation_digest(record)
    assert parsed_record["reservation_digest"] == parsed_receipt["reservation_digest"]
    assert parsed_record["fencing_token"] == parsed_receipt["fencing_token"]
    assert (
        parsed_record["previous_witness_digest"] == parsed_receipt["base_authority_witness_digest"]
    )
    assert receipt.sha256 == parsed_record["reservation_receipt_reference"]["sha256"]
    expected = {
        "reservation": {
            "reservation_id": parsed_record["reservation_id"],
            "reservation_digest": api.reservation_digest(record),
            "record_reference": {
                "path": str(source["record_path"]),
                "sha256": record.sha256,
                "schema_version": parsed_record["schema_version"],
            },
            "record_sequence": parsed_record["record_sequence"],
            "fencing_token": parsed_receipt["fencing_token"],
            "state_at_open": parsed_record["state"],
            "witness_receipt_reference": {
                "path": str(source["receipt_path"]),
                "sha256": receipt.sha256,
                "schema_version": parsed_receipt["schema_version"],
            },
            "witness_sequence": parsed_receipt["sequence"],
        }
    }
    cert = copy.deepcopy(source["certificate"])
    gov.validate_governance_certificate(cert, context=expected)
    if field == "none":
        return
    if field == "base":
        changed = copy.deepcopy(receipt_value)
        changed["base_authority_witness_digest"] = b.hashed("foreign-signed-base")
        raw = builder.signed(changed, fixture.key, b"S11-EX10-B4-RESERVATION-v1\n")
        with pytest.raises(api.BridgeProtocolError, match="REFERENCE_MISMATCH"):
            api.verify_reservation_receipt(raw, expected_context=context, witness_pin=pin)
        return
    claim = cert["reservation"]
    if field == "sequence":
        claim["witness_sequence"] += 1
    elif field == "token":
        claim["fencing_token"] += 1
    elif field == "record":
        claim["record_reference"]["sha256"] = b.hashed("foreign-record")
    else:
        claim["witness_receipt_reference"]["sha256"] = b.hashed("foreign-receipt")
    gov.validate_governance_certificate(cert)
    with pytest.raises(sql.AuthorityError, match="GOV_BINDING"):
        gov.validate_governance_certificate(cert, context=expected)


class ProfileOnlyReferencesFixture(ExistingReferencesFixture):
    reused_kinds = ("PROFILE",)


class SessionOnlyReferencesFixture(ExistingReferencesFixture):
    reused_kinds = ("SESSION",)


@pytest.mark.parametrize(
    "fixture_type,new_count",
    [
        (b.Fixture, 2),
        (ProfileOnlyReferencesFixture, 1),
        (SessionOnlyReferencesFixture, 1),
        (ExistingReferencesFixture, 0),
    ],
)
def test_introduced_reference_matrix_zero_one_two_atomic(fixture, fixture_type, new_count):
    with _nested_b3_test_directory(fixture.root) as root:
        prior = fixture_type(Path(root), (), create_db=True)
        args = candidate(prior)
        old = {row["reference_id"]: row for row in prior.state["artifact_references"]}
        args["reference_additions"] = tuple(
            row for row in args["reference_additions"] if row["reference_id"] not in old
        )
        assert len(args["reference_additions"]) == new_count
        result = opened(args)
        refs = result.material["state"]["artifact_references"]
        assert len(refs) == len(old) + new_count + 1
        assert all(row in refs for row in old.values())
        new = [row for row in refs if row["reference_id"] not in old]
        assert all(row["introduced_revision"] == 1 for row in new)
        assert args["governance_certificate_reference"] in new
        args["state"] = result
        assert opened(args) is result
        assert args["adapter"].calls == ["intent", "witness", "commit"]


@pytest.mark.parametrize(
    "change",
    ["none", "challenge", "sequence", "fence", "receipt", "active", "authority", "expired"],
)
def test_b4_reserve_receipt_requires_current_head_and_monotonic_fence(fixture, change):
    source = _reservation_binding_fixture(fixture)
    builder = _load_downstream_builder("w03_coordination_builder", "test_ex10_bridge_protocol.py")
    api = builder.b4
    record, receipt = source["record"], source["receipt"]
    value = builder.fill(api.schema_definitions()["coordination_head"])
    value.update(
        authority_id=record["authority_id"],
        authority_epoch=record["authority_epoch"],
        sequence=receipt["sequence"],
        fencing_token=receipt["fencing_token"],
        active_reservation_id=record["reservation_id"],
        receipt_digest=hashlib.sha256(source["receipt_path"].read_bytes()).hexdigest(),
        key_id=receipt["key_id"],
        key_epoch=receipt["key_epoch"],
        issued_at=b.NOW,
        expires_at="2026-10-04T00:00:30.000000Z",
        challenge=b.hashed("independent-current-challenge"),
    )
    omitted = {"signature", "issued_at", "expires_at", "sequence", "fencing_token"}
    context = api.CoordinationHeadContext(
        builder.wire({k: v for k, v in value.items() if k not in omitted}),
        receipt["sequence"],
        receipt["fencing_token"],
        b.datetime.fromisoformat(b.NOW.replace("Z", "+00:00")),
    )
    pin = api.PublicKeyPin(value["key_id"], value["key_epoch"], fixture.key.public_key())
    domain = b"S11-EX10-B4-COORDINATION-HEAD-v1\n"
    raw = builder.signed(value, fixture.key, domain)
    head = api.verify_coordination_head(raw, expected_context=context, witness_pin=pin)
    assert head.to_dict()["receipt_digest"] == record["reservation_receipt_reference"]["sha256"]
    assert head.to_dict()["fencing_token"] == source["certificate"]["reservation"]["fencing_token"]
    if change == "none":
        return
    fields = {
        "challenge": "challenge",
        "sequence": "sequence",
        "fence": "fencing_token",
        "receipt": "receipt_digest",
        "active": "active_reservation_id",
        "authority": "authority_id",
    }
    if change == "expired":
        value["expires_at"] = b.NOW
    elif change in {"sequence", "fence"}:
        value[fields[change]] -= 1
    elif change in {"active", "authority"}:
        value[fields[change]] = b.OP_ZERO
    else:
        value[fields[change]] = b.hashed("foreign-head-binding")
    with pytest.raises(api.BridgeProtocolError):
        api.verify_coordination_head(
            builder.signed(value, fixture.key, domain), expected_context=context, witness_pin=pin
        )


def _full_source_request(builder, operation, common, payload, bridge, holder, pin):
    """Test-only request producer; expected claims come from the caller's source table."""
    api = builder.b4
    schema = next(
        s for s in api.request_schemas() if s["properties"]["operation"]["const"] == operation
    )
    value = builder.fill(schema)
    value.update(copy.deepcopy(common), operation=operation, payload=copy.deepcopy(payload))
    claims = value["capability"]
    for key in (
        "request_id",
        "exchange_id",
        "operation",
        "session_id",
        "session_header_sha256",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "source_revision",
        "authorization_reference",
        "issued_at",
        "expires_at",
    ):
        claims[key] = copy.deepcopy(value[key])
    claims.update(
        bridge_key_id=pin.key_id,
        bridge_key_epoch=pin.key_epoch,
        request_nonce=common["nonce"],
        expected_revision=common["authority_revision"],
        expected_run_id=payload["run_identity"]["run_id"],
        reservation_id=payload.get("reservation_id"),
        reservation_digest=payload.get("reservation_digest"),
        fencing_token=payload.get("fencing_token"),
        holder_public_key_hex=holder.public_key().public_bytes_raw().hex(),
        deployment_verification_sha256=common.pop("deployment_sha"),
        container_identity_digest=common.pop("container_sha"),
    )
    # The two explicit approval digests are capability claims, not request schema fields.
    value.pop("deployment_sha")
    value.pop("container_sha")
    context = api.RequestContext(
        **{
            key: common[key]
            for key in (
                "request_id",
                "nonce",
                "exchange_id",
                "session_id",
                "session_header_sha256",
                "runtime_profile_sha256",
                "authority_id",
                "authority_epoch",
                "authority_revision",
                "source_revision",
                "project",
            )
        },
        operation=operation,
        authorization_reference=builder.wire(common["authorization_reference"]),
        deployment_verification_sha256=claims["deployment_verification_sha256"],
        container_identity_digest=claims["container_identity_digest"],
        expected_run_id=payload["run_identity"]["run_id"],
        reservation_id=payload.get("reservation_id"),
        reservation_digest=payload.get("reservation_digest"),
        fencing_token=payload.get("fencing_token"),
        holder_public_key_hex=claims["holder_public_key_hex"],
        expected_payload=builder.wire(payload),
        now=b.datetime.fromisoformat(common["issued_at"].replace("Z", "+00:00")),
    )
    raw = builder.signed_request(value, bridge, holder)
    accepted = api.verify_request(
        raw, expected_context=context, bridge_pin=pin, holder_public_key=holder.public_key()
    )
    return accepted, context


@pytest.fixture
def full_governance_source(fixture, profile_binding_source, qualified_evidence_source):
    """One coherent synthetic source vector; no operational qualification is inferred."""
    f, profile_source = fixture, profile_binding_source
    evidence_builder, evidence_sample = qualified_evidence_source
    builder = _load_downstream_builder("w03_full_bridge_builder", "test_ex10_bridge_protocol.py")
    api, evd, rp, b2 = builder.b4, evidence_builder.evd, profile_source["rp"], profile_source["b2"]
    descriptor_path = f.root / "qualified-descriptor.json"
    descriptor_path.write_bytes(sql.canonical_bytes(f.descriptor))
    descriptor_path.chmod(0o600)
    f.descriptor_ref = {**f.descriptor_ref, "path": str(descriptor_path)}
    raw_profile = profile_source["profile_a"].to_dict()
    old_descriptor_path = raw_profile["historical_lifecycle_authority_reference"]["path"]
    raw_profile["historical_lifecycle_authority_reference"] = f.descriptor_ref
    profile = rp.validate_runtime_profile(raw_profile)
    header0 = profile_source["header_a"]
    bindings = copy.deepcopy(header0["path_identity_binding"])
    info = descriptor_path.stat()
    for entry in bindings:
        if entry["path"] == old_descriptor_path:
            entry.update(
                path=str(descriptor_path),
                device=info.st_dev,
                inode=info.st_ino,
                uid=info.st_uid,
                gid=info.st_gid,
                mode="0600",
            )
    bindings.sort(key=lambda row: row["path"])
    header = b2.new_session_v2_header(
        profile,
        session_id=header0["session_id"],
        created_at=header0["created_at"],
        branch=header0["branch"],
        launcher_blobs=header0["launcher_blobs"],
        runtime_profile_approval_reference=header0["runtime_profile_approval_reference"],
        path_identity_binding=bindings,
    )
    profile_path, header_path = f.root / "qualified-profile.json", f.root / "qualified-header.json"
    profile_path.write_bytes(rp.canonical_runtime_profile_bytes(profile))
    profile_path.chmod(0o600)
    header_path.write_bytes(b2.canonical_session_header_bytes(header))
    header_path.chmod(0o600)
    profile = rp.parse_runtime_profile_bytes(profile_path.read_bytes())
    b2._validate_header(json.loads(header_path.read_bytes()))
    profile_sha, header_sha = rp.runtime_profile_sha256(profile), b2.session_header_sha256(header)

    def ref(path, schema):
        return {
            "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "schema_version": schema,
        }

    profile_ref, header_ref = (
        ref(profile_path, rp.SCHEMA_VERSION),
        ref(header_path, "s11-ex10-session-v2-header-projection-v1"),
    )
    verified = f.verify()
    assert history.first_valid_status(verified)["status"] == "NONE"
    acks = _qualified_ack_set(verified)
    base = history.current_revision(verified)
    receipt_path, head_path = (
        f.root / "qualified-b3-receipt.json",
        f.root / "qualified-b3-head.json",
    )
    base_receipt = history._receipts(verified.material["state"])[-1]
    receipt_path.write_bytes(sql.canonical_bytes(base_receipt))
    receipt_path.chmod(0o600)
    head_path.write_bytes(verified.canonical_current_head)
    head_path.chmod(0o600)
    b3_base = {
        "authority_id": base["authority_id"],
        "authority_epoch": base["epoch"],
        "revision": base["revision"],
        "ledger_digest": base["ledger_digest"],
        "witness_digest": b.wit.verify_witness(base_receipt, f.pin),
        "receipt_reference": ref(receipt_path, b.wit.RECEIPT_SCHEMA),
    }
    assert b3_base["witness_digest"] == b3_base["receipt_reference"]["sha256"]
    now = b.datetime.now(b.UTC)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    expires = (now + b.timedelta(seconds=120)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    identity = {
        "started_at": stamp,
        "git_sha": raw_profile["source_revision"],
        "dataset_id": "s11-load-v1",
        "scenario_id": "EX-10",
        "seed": 20260921,
        "execution_nonce": "b" * 64,
    }
    identity["run_id"] = api.make_run_id(
        started_at=now,
        git_sha=identity["git_sha"],
        dataset_id=identity["dataset_id"],
        scenario_id=identity["scenario_id"],
        seed=identity["seed"],
        execution_nonce=identity["execution_nonce"],
    )
    identity["identity_digest"] = sql.digest(identity)
    api.qualify_run_identity(identity)
    root = Path(raw_profile["host_roots"]["evidence"]["path"]) / (
        "qualified-opening-" + f.root.name
    )
    root.mkdir(mode=0o700)
    payloads = copy.deepcopy(evidence_sample["payloads"])
    manifest, start, preflight = (payloads[k] for k in evd.REFERENCE_FIELDS[:3])
    manifest.update(run_id=identity["run_id"], git_sha=identity["git_sha"], started_at=stamp)
    manifest["configuration"]["git_sha"] = identity["git_sha"]
    preflight.update(
        expected_git_sha=identity["git_sha"], git_sha=identity["git_sha"], checked_at=stamp
    )
    start.update(run_id=identity["run_id"], git_sha=identity["git_sha"], recorded_at=stamp)
    start["preflight"].update(sha256=sql.digest(preflight), checked_at=stamp)
    start["acknowledged_not_demonstrated"] = [ack["run_id"] for ack in acks]
    artifacts = {key: evidence_builder.wire(value) for key, value in payloads.items()}
    specs = tuple(
        evd.ArtifactExpectation(key, str(root / (key + ".json")), 1048576)
        for key in evd.REFERENCE_FIELDS
    )
    initial_refs = {}
    for spec in specs:
        path = Path(spec.path)
        path.write_bytes(artifacts[spec.field])
        path.chmod(0o600)
        initial_refs[spec.field] = ref(path, evd.ARTIFACT_SCHEMAS[spec.field])
    sample = builder.sample_for("reserve-start")
    bridge, holder = sample["bridge"], evidence_sample["key"]
    pin = api.PublicKeyPin(sample["pin"].key_id, sample["pin"].key_epoch, bridge.public_key())
    deployment = raw_profile["deployment_verification_reference"]
    approved_container = b.hashed("synthetic-approved-container-identity")
    common = {
        "request_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "w03-request-" + f.root.name)),
        "nonce": identity["execution_nonce"],
        "exchange_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "w03-exchange-" + f.root.name)),
        "session_id": header["session_id"],
        "session_header_sha256": header_sha,
        "runtime_profile_sha256": profile_sha,
        "authority_id": base["authority_id"],
        "authority_epoch": base["epoch"],
        "authority_revision": base["revision"],
        "source_revision": identity["git_sha"],
        "project": raw_profile["project"],
        "issued_at": stamp,
        "expires_at": expires,
        "authorization_reference": header["runtime_profile_approval_reference"],
        "deployment_sha": deployment["sha256"],
        "container_sha": approved_container,
    }
    # W07/W06 qualify the operational envelope; this W03 fixture binds an
    # explicit synthetic prerequisite reference, never a claimed B5 PASS.
    envelope_path = f.root / "synthetic-envelope-prerequisite.json"
    envelope_path.write_bytes(
        sql.canonical_bytes(
            {
                "schema_version": "s11-ex10-synthetic-envelope-prerequisite-v1",
                "status": "W07_W06_QUALIFICATION_PENDING",
            }
        )
    )
    envelope_path.chmod(0o600)
    envelope_ref = ref(envelope_path, "s11-ex10-synthetic-envelope-prerequisite-v1")
    reserve_payload = {
        "acknowledgments": acks,
        "deployment_verification_reference": deployment,
        "descriptor_reference": f.descriptor_ref,
        "expected_head": b3_base,
        "operational_envelope_reference": envelope_ref,
        "preflight_reference": initial_refs["preflight_reference"],
        "profile_reference": profile_ref,
        "run_identity": identity,
        "session_reference": header_ref,
    }
    reserve, reserve_context = _full_source_request(
        builder, "reserve-start", copy.deepcopy(common), reserve_payload, bridge, holder, pin
    )
    record = builder.fill(api.schema_definitions()["reservation"])
    record.update(
        authority_id=base["authority_id"],
        authority_epoch=base["epoch"],
        session_id=header["session_id"],
        session_header_sha256=header_sha,
        runtime_profile_sha256=profile_sha,
        source_revision=identity["git_sha"],
        project=raw_profile["project"],
        deployment_verification_reference=deployment,
        requested_at=stamp,
        updated_at=stamp,
        freshness_deadline=expires,
        nonce=identity["execution_nonce"],
        request_id=reserve.request.to_dict()["request_id"],
        request_digest=api.request_digest(reserve.request),
        acknowledgment_digest=sql.digest(acks),
        previous_authority_revision=base["revision"],
        previous_ledger_digest=base["ledger_digest"],
        previous_witness_digest=base["witness_digest"],
        expected_next_revision=base["revision"] + 1,
        run_id=identity["run_id"],
        run_identity=identity,
        fencing_token=7,
        state="PENDING",
        record_sequence=1,
        operator_authorization_reference=common["authorization_reference"],
        host_root_binding_sha256=sql.digest(raw_profile["host_roots"]),
    )
    record["reservation_id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, "w03-reservation-" + f.root.name))
    record["reservation_digest"] = sql.digest(
        {key: record[key] for key in api.RESERVATION_INTENT_FIELDS}
    )
    record["record_digest"] = sql.digest(
        {key: value for key, value in record.items() if key != "record_digest"}
    )
    initial_record = api.qualify_reservation(builder.wire(record))
    receipt_fields = {
        "schema_version": "s11-ex10-b4-reservation-receipt-v1",
        "namespace": record["namespace"],
        "authority_id": base["authority_id"],
        "authority_epoch": base["epoch"],
        "reservation_id": record["reservation_id"],
        "reservation_digest": api.reservation_digest(initial_record),
        "state": "PENDING",
        "transition": "RESERVE",
        "fencing_token": record["fencing_token"],
        "sequence": 1,
        "previous_receipt_digest": None,
        "base_authority_revision": base["revision"],
        "base_authority_witness_digest": base["witness_digest"],
        "open_witness_digest": None,
        "terminal_witness_digest": None,
        "operation_id": reserve.request.to_dict()["request_id"],
        "key_id": f.pin.record["key_id"],
        "key_epoch": f.pin.record["key_epoch"],
    }
    reserve_receipt_value = {**receipt_fields, "issued_at": stamp, "signature": "0" * 128}
    reserve_receipt_raw = builder.signed(
        reserve_receipt_value, f.key, b"S11-EX10-B4-RESERVATION-v1\n"
    )
    witness_pin = api.PublicKeyPin(
        f.pin.record["key_id"], f.pin.record["key_epoch"], f.key.public_key()
    )
    accepted_receipt = api.verify_reservation_receipt(
        reserve_receipt_raw,
        expected_context=api.ReceiptContext(builder.wire(receipt_fields), now),
        witness_pin=witness_pin,
    )
    b4receipt_path = f.root / "qualified-reserve-receipt.json"
    b4receipt_path.write_bytes(reserve_receipt_raw)
    b4receipt_path.chmod(0o600)
    record.update(
        record_sequence=2,
        previous_reservation_record_digest=initial_record.to_dict()["record_digest"],
        reservation_receipt_reference=ref(b4receipt_path, "s11-ex10-b4-reservation-receipt-v1"),
    )
    record["record_digest"] = sql.digest(
        {key: value for key, value in record.items() if key != "record_digest"}
    )
    accepted_record = api.qualify_reservation(builder.wire(record))
    record_path = f.root / "qualified-reservation.json"
    record_path.write_bytes(accepted_record.canonical)
    record_path.chmod(0o600)
    permit = builder.fill(api.schema_definitions()["open_one_permit"])
    permit.update(
        session_id=header["session_id"],
        session_header_sha256=header_sha,
        runtime_profile_sha256=profile_sha,
        authority_id=base["authority_id"],
        authority_epoch=base["epoch"],
        source_revision=identity["git_sha"],
        project=raw_profile["project"],
        authorization_reference=common["authorization_reference"],
        base_authority_revision=base["revision"],
        expected_open_revision=base["revision"] + 1,
        bridge_key_id=pin.key_id,
        bridge_key_epoch=pin.key_epoch,
        container_identity_digest=approved_container,
        deployment_verification_sha256=deployment["sha256"],
        exchange_id=common["exchange_id"],
        holder_public_key_hex=holder.public_key().public_bytes_raw().hex(),
        issued_at=stamp,
        expires_at=expires,
        attempt_nonce=identity["execution_nonce"],
        run_identity=identity,
        reservation_id=record["reservation_id"],
        reservation_digest=api.reservation_digest(accepted_record),
        reservation_receipt_reference=record["reservation_receipt_reference"],
        reserve_request_id=record["request_id"],
        reserve_request_digest=record["request_digest"],
        fencing_token=record["fencing_token"],
        acknowledgment_digest=sql.digest(acks),
        preflight_reference=initial_refs["preflight_reference"],
    )
    omitted = {
        "signature",
        "permit_digest",
        "issued_at",
        "expires_at",
        "permit_id",
        "permit_nonce",
        "kind",
        "schema_version",
    }
    permit_context = api.PermitContext(
        builder.wire({k: v for k, v in permit.items() if k not in omitted}),
        now + b.timedelta(seconds=120),
        now,
    )
    permit["permit_digest"] = sql.digest(
        {k: v for k, v in permit.items() if k not in {"permit_digest", "signature"}}
    )
    permit_raw = builder.signed(permit, bridge, b"S11-EX10-B4-OPEN-ONE-v1\n")
    accepted_permit = api.verify_open_one(
        permit_raw,
        expected_context=permit_context,
        bridge_pin=pin,
        holder_public_key=holder.public_key(),
    )
    permit_path = f.root / "qualified-permit.json"
    permit_path.write_bytes(permit_raw)
    permit_path.chmod(0o600)
    opening = {
        "schema_version": "s11-ex10-b4-open-evidence-v1",
        "reserve_request_id": record["request_id"],
        "reservation_id": record["reservation_id"],
        "fencing_token": record["fencing_token"],
        "open_permit_digest": api.open_permit_digest(accepted_permit),
        "run_id": identity["run_id"],
        "identity_digest": identity["identity_digest"],
        "runtime_profile_sha256": profile_sha,
        "session_header_sha256": header_sha,
        "opened_at": stamp,
        "receipt_digest": "0" * 64,
        "executor_signature": "0" * 128,
        **initial_refs,
    }
    opening_raw = evidence_builder.sign(opening, holder)
    evidence_context = evd.OpenEvidenceContext(
        record["request_id"],
        record["reservation_id"],
        record["fencing_token"],
        api.open_permit_digest(accepted_permit),
        profile_sha,
        header_sha,
        builder.wire(identity),
        now,
        now + b.timedelta(seconds=120),
        now,
        evidence_sample["context"].executor_fingerprint,
        evidence_sample["context"].preflight_required_checks,
        specs,
    )
    with evd.ConfinedArtifactResolver(str(root), expected_owner=os.geteuid()) as resolver:
        accepted_evidence = evd.verify_open_evidence(
            opening_raw,
            expected_context=evidence_context,
            executor_public_key=holder.public_key(),
            artifact_resolver=resolver,
        )
    evidence_path = root / "open-evidence.json"
    evidence_path.write_bytes(opening_raw)
    evidence_path.chmod(0o600)
    commit_payload = {
        "expected_head": b3_base,
        "fencing_token": record["fencing_token"],
        "open_evidence": accepted_evidence.receipt.to_dict(),
        "open_permit_digest": api.open_permit_digest(accepted_permit),
        "reservation_digest": api.reservation_digest(accepted_record),
        "reservation_id": record["reservation_id"],
        "reservation_receipt_reference": record["reservation_receipt_reference"],
        "reserve_request_id": record["request_id"],
        "run_identity": identity,
    }
    commit_common = {**common, "request_id": b.OP_NEXT, "nonce": "c" * 64}
    commit, commit_context = _full_source_request(
        builder, "commit-open", copy.deepcopy(commit_common), commit_payload, bridge, holder, pin
    )
    head = builder.fill(api.schema_definitions()["coordination_head"])
    head.update(
        authority_id=base["authority_id"],
        authority_epoch=base["epoch"],
        sequence=receipt_fields["sequence"],
        receipt_digest=accepted_receipt.sha256,
        active_reservation_id=record["reservation_id"],
        fencing_token=record["fencing_token"],
        challenge=b.hashed("full-independent-challenge"),
        key_id=witness_pin.key_id,
        key_epoch=witness_pin.key_epoch,
        issued_at=stamp,
        expires_at=(now + b.timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
    )
    head_omitted = {"signature", "issued_at", "expires_at", "sequence", "fencing_token"}
    head_context = api.CoordinationHeadContext(
        builder.wire({k: v for k, v in head.items() if k not in head_omitted}),
        receipt_fields["sequence"],
        record["fencing_token"],
        now,
    )
    accepted_head = api.verify_coordination_head(
        builder.signed(head, f.key, b"S11-EX10-B4-COORDINATION-HEAD-v1\n"),
        expected_context=head_context,
        witness_pin=witness_pin,
    )
    expected = {
        "schema_version": gov.SCHEMA_VERSION,
        "kind": "GOVERNANCE_CERTIFICATE",
        "issued_at": stamp,
        "issuer": "HOST_GOVERNANCE_AUTHORITY",
        "namespace": record["namespace"],
        "authority_id": base["authority_id"],
        "authority_epoch": base["epoch"],
        "authority_descriptor_reference": f.descriptor_ref,
        "authority_revision_before": base["revision"],
        "authority_revision_after": base["revision"] + 1,
        "b3_base_witness": b3_base,
        "b3_base_current_head_reference": ref(head_path, b.wit.HEAD_SCHEMA),
        "reservation": {
            "reservation_id": record["reservation_id"],
            "reservation_digest": api.reservation_digest(accepted_record),
            "record_reference": ref(record_path, record["schema_version"]),
            "record_sequence": record["record_sequence"],
            "fencing_token": accepted_receipt.to_dict()["fencing_token"],
            "state_at_open": accepted_record.to_dict()["state"],
            "witness_receipt_reference": record["reservation_receipt_reference"],
            "witness_sequence": accepted_head.to_dict()["sequence"],
        },
        "session": {
            "session_id": header["session_id"],
            "schema_version": header["schema_version"],
            "header_sha256": header_sha,
            "header_reference": header_ref,
        },
        "runtime_profile": {
            "profile_id": raw_profile["profile_id"],
            "schema_version": raw_profile["schema_version"],
            "sha256": profile_sha,
            "reference": profile_ref,
        },
        "project": raw_profile["project"],
        "source_revision": identity["git_sha"],
        "protocol": payloads["plan_reference"]["protocol_id"],
        "methodology": payloads["plan_reference"]["methodology_version"],
        "run_id": identity["run_id"],
        "run_identity_digest": identity["identity_digest"],
        "open_evidence_reference": ref(evidence_path, opening["schema_version"]),
        "acknowledgments": acks,
        "acknowledgment_digest": sql.digest(acks),
        "first_valid_status": history.first_valid_status(verified)["status"],
        "reserve_request_id": reserve.request.to_dict()["request_id"],
        "reserve_request_digest": api.request_digest(reserve.request),
        "commit_request_id": commit.request.to_dict()["request_id"],
        "commit_request_digest": api.request_digest(commit.request),
        "open_one_permit": {
            "permit_id": accepted_permit.to_dict()["permit_id"],
            "permit_digest": api.open_permit_digest(accepted_permit),
            "reference": ref(permit_path, permit["schema_version"]),
        },
    }
    expected["certificate_id"] = gov.certificate_identity(expected)
    assert len(expected) == 31
    cert = gov.validate_governance_certificate(copy.deepcopy(expected), context=expected)
    args = candidate(f, verified)
    rows = [b.artifact(profile_ref, "PROFILE"), b.artifact(header_ref, "SESSION")]
    for row in rows:
        row["introduced_revision"] = base["revision"] + 1
    certificate_ref = b.artifact(
        {
            "path": str(f.root / (expected["certificate_id"] + ".json")),
            "sha256": gov.governance_certificate_sha256(cert),
            "schema_version": gov.SCHEMA_VERSION,
        },
        "GOVERNANCE",
    )
    certificate_ref["introduced_revision"] = base["revision"] + 1
    args.update(
        governance_certificate=cert,
        governance_certificate_reference=certificate_ref,
        reference_additions=tuple(rows),
        occurred_at=stamp,
    )
    args["run"].update(
        run_id=identity["run_id"],
        source_revision=identity["git_sha"],
        protocol=expected["protocol"],
        methodology=expected["methodology"],
        created_at=identity["started_at"],
        opened_at=opening["opened_at"],
        profile_reference_id=rows[0]["reference_id"],
        session_reference_id=rows[1]["reference_id"],
        evidence_authority_reference_id=certificate_ref["reference_id"],
    )
    args["event"].update(
        run_id=identity["run_id"],
        occurred_at=stamp,
        evidence_reference_id=certificate_ref["reference_id"],
    )
    args["evidence_reference_id"] = certificate_ref["reference_id"]
    yield {
        "expected": expected,
        "args": args,
        "builder": builder,
        "permit": accepted_permit,
        "permit_context": permit_context,
        "bridge": bridge,
        "holder": holder,
        "bridge_pin": pin,
        "record": accepted_record,
        "receipt": accepted_receipt,
        "evidence": accepted_evidence,
        "base": verified,
        "reserve": reserve,
        "commit": commit,
    }


def test_full_qualified_governance_vector_publishes_atomically(full_governance_source):
    source = full_governance_source
    expected, args = source["expected"], source["args"]
    assert (
        expected["session"]["header_sha256"]
        == source["evidence"].receipt.to_dict()["session_header_sha256"]
    )
    assert (
        expected["runtime_profile"]["sha256"]
        == source["reserve"].request.to_dict()["runtime_profile_sha256"]
    )
    assert source["reserve"].holder_public_key_hex == source["commit"].holder_public_key_hex
    result = opened(args)
    assert history.current_revision(result)["revision"] == expected["authority_revision_after"]
    assert args["run"] in result.material["state"]["official_runs"]
    assert args["event"] in result.material["state"]["authority_events"]
    assert (
        args["governance_certificate_reference"] in result.material["state"]["artifact_references"]
    )
    assert args["adapter"].calls == ["intent", "witness", "commit"]


@pytest.mark.parametrize(
    "change", ["duplicate", "extra", "kind", "missingProfile", "missingSession", "resupplyReused"]
)
def test_reference_additions_illegal_set_rejected_before_publication(fixture, change):
    if change == "resupplyReused":
        with _nested_b3_test_directory(fixture.root) as root:
            prior = ExistingReferencesFixture(Path(root), (), create_db=True)
            args = candidate(prior)
            with pytest.raises(sql.AuthorityError, match="GOV_REFERENCE"):
                opened(args)
            assert args["adapter"].calls == []
        return
    args = candidate(fixture)
    rows = list(copy.deepcopy(args["reference_additions"]))
    if change == "duplicate":
        rows = [rows[0], rows[0]]
    elif change == "extra":
        extra = b.artifact(b.reference("unused-addition"), "PROFILE")
        extra["introduced_revision"] = 1
        rows = [rows[0], extra]
    elif change == "kind":
        rows[0]["kind"] = "SESSION"
        rows[0]["reference_id"] = sql.digest(
            {k: rows[0][k] for k in ("path", "sha256", "schema_version", "kind")}
        )
        args["run"]["profile_reference_id"] = rows[0]["reference_id"]
    elif change == "missingProfile":
        rows = rows[1:]
    else:
        rows = rows[:1]
    args["reference_additions"] = tuple(rows)
    with pytest.raises(sql.AuthorityError, match="GOV_REFERENCE"):
        opened(args)
    assert args["adapter"].calls == []


@pytest.mark.parametrize(
    "field",
    [
        "authority_descriptor_reference",
        "b3_base_witness",
        "b3_base_current_head_reference",
        "reservation",
        "session",
        "runtime_profile",
        "project",
        "source_revision",
        "run_id",
        "run_identity_digest",
        "open_evidence_reference",
        "reserve_request_id",
        "reserve_request_digest",
        "commit_request_digest",
        "open_one_permit",
    ],
)
def test_full_vector_rejects_genuinely_qualified_other_context(
    full_governance_source, fixture, profile_binding_source, qualified_evidence_source, field
):
    original = full_governance_source
    with _nested_b3_test_directory(fixture.root) as other_root:
        other = b.Fixture(Path(other_root), (), create_db=True)
        source = dict(profile_binding_source)
        different_profile = source["profile_a"].to_dict()
        different_profile.update(project="synthetic-qualified-project-b", source_revision="b" * 40)
        source["profile_a"] = source["rp"].validate_runtime_profile(different_profile)
        setup = full_governance_source_fixture(other, source, qualified_evidence_source)
        try:
            qualified_other = next(setup)
            # All B claims came through their real qualifier APIs before use.
            gov.validate_governance_certificate(
                qualified_other["args"]["governance_certificate"],
                context=qualified_other["expected"],
            )
            expected = original["expected"]
            assert expected[field] != qualified_other["expected"][field]
            substituted = copy.deepcopy(expected)
            substituted[field] = copy.deepcopy(qualified_other["expected"][field])
            if field == "b3_base_witness":
                # The other source has the same authority/base revision but a
                # different approved lineage, descriptor and signed receipt.
                assert substituted[field]["authority_id"] == expected["authority_id"]
            substituted["certificate_id"] = gov.certificate_identity(substituted)
            gov.validate_governance_certificate(substituted)
            with pytest.raises(sql.AuthorityError, match="GOV_BINDING"):
                gov.validate_governance_certificate(substituted, context=expected)
            assert original["args"]["adapter"].calls == []
            assert qualified_other["args"]["adapter"].calls == []
        finally:
            setup.close()


# Explicit generator alias for constructing a second independent synthetic
# source context. It does not invoke a test function or bypass a product guard.
full_governance_source_fixture = full_governance_source.__wrapped__
