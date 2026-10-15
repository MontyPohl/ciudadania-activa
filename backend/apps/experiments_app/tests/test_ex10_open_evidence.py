"""Offline OPEN_EVIDENCE tests with independent signed wire vectors and private roots."""

from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import types
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

APP = Path(__file__).resolve().parents[1]
PACKAGE = types.ModuleType("w01_open_evidence_fixture")
PACKAGE.__path__ = [str(APP)]
sys.modules[PACKAGE.__name__] = PACKAGE
SPEC = importlib.util.spec_from_file_location(
    PACKAGE.__name__ + ".ex10_open_evidence",
    os.environ.get("S11_OPEN_EVIDENCE_MODULE_PATH", str(APP / "ex10_open_evidence.py")),
)
evd = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = evd
SPEC.loader.exec_module(evd)


def wire(value):
    return (
        json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        )
        + "\n"
    ).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def sign(value, key):
    result = copy.deepcopy(value)
    result["receipt_digest"] = sha(
        wire({k: v for k, v in result.items() if k not in {"receipt_digest", "executor_signature"}})
    )
    result["executor_signature"] = key.sign(
        wire({k: v for k, v in result.items() if k != "executor_signature"})
    ).hex()
    return wire(result)


def required_checks():
    tree = ast.parse((APP / "ex10_preflight.py").read_text())
    plan = next(
        n.value
        for n in tree.body
        if isinstance(n, ast.AnnAssign)
        and isinstance(n.target, ast.Name)
        and n.target.id == "CHECK_PLAN"
    )
    return frozenset(entry.elts[0].value for step in plan.elts for entry in step.elts[1].elts) - {
        "ready_state"
    }


class MemoryResolver:
    def __init__(self, artifacts):
        self.artifacts = artifacts
        self.calls = []

    def read_artifacts(self, expectations, references):
        self.calls.append(tuple(s.field for s in expectations))
        return dict(self.artifacts)


@pytest.fixture
def sample():
    with tempfile.TemporaryDirectory(
        prefix="w01-", dir=os.environ["S11_B3_TEST_BASE"]
    ) as directory:
        root = Path(directory)
        root.chmod(0o700)
        key = Ed25519PrivateKey.generate()  # Ephemeral only; never serialized.
        now = datetime(2026, 10, 5, 8, tzinfo=UTC)
        identity = {
            "started_at": "2026-10-05T08:00:00.000000Z",
            "git_sha": "a" * 40,
            "dataset_id": "s11-load-v1",
            "scenario_id": "EX-10",
            "seed": 20260921,
            "execution_nonce": "b" * 64,
        }
        identity["run_id"] = evd.make_run_id(
            started_at=now,
            git_sha=identity["git_sha"],
            dataset_id=identity["dataset_id"],
            scenario_id=identity["scenario_id"],
            seed=identity["seed"],
            execution_nonce=identity["execution_nonce"],
        )
        identity["identity_digest"] = sha(wire(identity))
        load = evd.ex10_load
        plan = load.build_plan(load.load_dataset()).to_json()
        assert sha(wire(plan)) == "2b50aa48cfa975029ab38a4c0e17430f67469b185fce5aaed89414728e6934d7"
        fingerprint = "c" * 64
        checks = [
            {
                "check": name,
                "result": "PASS",
                "expected": None,
                "observed_sanitized": None,
                "detail_code": None,
            }
            for name in sorted(required_checks() | {"ready_state:fixture_actors"})
        ]
        preflight = {
            "schema_version": "s11c3-ex10-preflight-v1",
            "protocol_version": "s11-ex10-v1",
            "methodology_version": "S11A-v1.1",
            "scenario_id": "EX-10",
            "result": "PASS",
            "gate": "READY_FOR_EXECUTOR_INTEGRATION",
            "ready_for_execution": False,
            "run_id": None,
            "evidence_written": False,
            "report_posts": 0,
            "provider_calls": 0,
            "task_publishes": 0,
            "health_requests": 1,
            "control_probes": 2,
            "expected_git_sha": identity["git_sha"],
            "git_sha": identity["git_sha"],
            "checked_at": identity["started_at"],
            "checks": checks,
        }
        manifest = {
            "schema_version": "s11-run-manifest-v1",
            "methodology_version": "S11A-v1.1",
            "run_id": identity["run_id"],
            "git_sha": identity["git_sha"],
            "git_clean": True,
            "started_at": identity["started_at"],
            "finished_at": None,
            "environment": "isolated-experiment",
            "timezone": "America/Asuncion",
            "seed": 20260921,
            "dataset_id": "s11-load-v1",
            "dataset_sha256": load.DATASET_SHA256,
            "scenario_id": "EX-10",
            "experiment_id": "S11C3-EX10",
            "status": "PREPARED",
            "configuration": {
                "git_sha": identity["git_sha"],
                "evidence_mode": "OFFICIAL",
                "experiment_id": "S11C3-EX10",
                "protocol": load.protocol_identity(),
                "plan_sha256": sha(wire(plan)),
                "executor_fingerprint": fingerprint,
            },
            "runtime": {"python": "3.11"},
            "artifact_references": [],
            "invalidity_reason": None,
        }
        start = {
            "schema_version": "s11c3-ex10-start-v1",
            "scenario_id": "EX-10",
            "run_id": identity["run_id"],
            "recorded_at": identity["started_at"],
            "evidence_mode": "OFFICIAL",
            "git_sha": identity["git_sha"],
            "plan_sha256": sha(wire(plan)),
            "dataset_sha256": load.DATASET_SHA256,
            "preflight": {
                "sha256": sha(wire(preflight)),
                "checked_at": preflight["checked_at"],
                "result": "PASS",
                "gate": preflight["gate"],
                "checks": len(checks),
            },
            "executor_sources": {"files": 30, "match": True},
            "executor_fingerprint": fingerprint,
            "prior_ex10_runs": [],
            "acknowledged_not_demonstrated": [],
        }
        payloads = dict(zip(evd.REFERENCE_FIELDS, (manifest, start, preflight, plan), strict=True))
        artifacts = {field: wire(value) for field, value in payloads.items()}
        specs = tuple(
            evd.ArtifactExpectation(field, str(root / (field + ".json")), 1048576)
            for field in evd.REFERENCE_FIELDS
        )
        for spec in specs:
            Path(spec.path).write_bytes(artifacts[spec.field])
            Path(spec.path).chmod(0o600)
        receipt = {
            "schema_version": "s11-ex10-b4-open-evidence-v1",
            "reserve_request_id": "10000000-0000-4000-8000-000000000001",
            "reservation_id": "10000000-0000-4000-8000-000000000002",
            "fencing_token": 1,
            "open_permit_digest": "d" * 64,
            "run_id": identity["run_id"],
            "identity_digest": identity["identity_digest"],
            "runtime_profile_sha256": "e" * 64,
            "session_header_sha256": "f" * 64,
            "opened_at": identity["started_at"],
            "receipt_digest": "0" * 64,
            "executor_signature": "0" * 128,
        }
        for spec in specs:
            receipt[spec.field] = {
                "path": spec.path,
                "sha256": sha(artifacts[spec.field]),
                "schema_version": evd.ARTIFACT_SCHEMAS[spec.field],
            }
        context = evd.OpenEvidenceContext(
            receipt["reserve_request_id"],
            receipt["reservation_id"],
            1,
            "d" * 64,
            "e" * 64,
            "f" * 64,
            wire(identity),
            now,
            now + timedelta(seconds=120),
            now,
            fingerprint,
            required_checks(),
            specs,
        )
        yield {
            "root": root,
            "key": key,
            "context": context,
            "receipt": receipt,
            "raw": sign(receipt, key),
            "artifacts": artifacts,
            "payloads": payloads,
        }


def verify(sample, *, raw=None, context=None, key=None, resolver=None):
    return evd.verify_open_evidence(
        sample["raw"] if raw is None else raw,
        expected_context=sample["context"] if context is None else context,
        executor_public_key=sample["key"].public_key() if key is None else key,
        artifact_resolver=MemoryResolver(sample["artifacts"]) if resolver is None else resolver,
    )


def rejected(sample, raw, code, **kwargs):
    resolver = kwargs.pop("resolver", MemoryResolver(sample["artifacts"]))
    with pytest.raises(evd.OpenEvidenceError) as error:
        verify(sample, raw=raw, resolver=resolver, **kwargs)
    assert error.value.code == code
    assert str(error.value) == code
    return resolver


def test_signed_baseline_and_three_distinct_preimages(sample):
    result = verify(sample)
    data = json.loads(sample["raw"])
    digest14 = sha(
        wire({k: v for k, v in data.items() if k not in {"receipt_digest", "executor_signature"}})
    )
    assert result.receipt_digest == digest14 == data["receipt_digest"]
    assert result.sha256 == sha(sample["raw"]) != digest14
    sample["key"].public_key().verify(
        bytes.fromhex(data["executor_signature"]),
        wire({k: v for k, v in data.items() if k != "executor_signature"}),
    )
    assert len(result.artifacts) == 4
    value = result.receipt.to_dict()
    value["plan_reference"]["sha256"] = "0" * 64
    assert result.receipt.to_dict() == data


@pytest.mark.parametrize("field", sorted(evd.FIELDS))
def test_each_required_field_missing(sample, field):
    value = copy.deepcopy(sample["receipt"])
    del value[field]
    rejected(sample, wire(value), "EVD_PARSE_INVALID")


@pytest.mark.parametrize(
    "variant",
    [
        "unknown",
        "duplicate",
        "BOM",
        "space",
        "noLF",
        "extraLF",
        "float",
        "bool",
        "NaN",
        "nonNFC",
        "surrogate",
        "invalidDate",
        "overflow",
        "zeroFence",
        "badSignature",
        "badRun",
    ],
)
def test_noncanonical_or_invalid_receipt(sample, variant):
    raw = sample["raw"]
    value = json.loads(raw)
    if variant == "unknown":
        value["unknown"] = 1
    elif variant == "duplicate":
        raw = b'{"fencing_token":1,' + raw[1:]
    elif variant == "BOM":
        raw = b"\xef\xbb\xbf" + raw
    elif variant == "space":
        raw = b" " + raw
    elif variant == "noLF":
        raw = raw[:-1]
    elif variant == "extraLF":
        raw += b"\n"
    elif variant in {"float", "bool", "overflow", "zeroFence"}:
        value["fencing_token"] = {"float": 1.0, "bool": True, "overflow": 2**53, "zeroFence": 0}[
            variant
        ]
    elif variant == "NaN":
        raw = raw.replace(b'"fencing_token":1', b'"fencing_token":NaN')
    elif variant == "nonNFC":
        value["plan_reference"]["path"] += "e\u0301"
    elif variant == "surrogate":
        raw = raw.replace(b'"fencing_token":1', b'"fencing_token":"\\ud800"')
    elif variant == "invalidDate":
        value["opened_at"] = "2026-02-30T00:00:00.000000Z"
    elif variant == "badSignature":
        value["executor_signature"] = "a" * 127
    elif variant == "badRun":
        value["run_id"] = value["run_id"].replace("s11-", "other-")
    if variant in {
        "unknown",
        "float",
        "bool",
        "overflow",
        "zeroFence",
        "nonNFC",
        "invalidDate",
        "badSignature",
        "badRun",
    }:
        raw = wire(value)
    resolver = rejected(sample, raw, "EVD_PARSE_INVALID")
    assert resolver.calls == []


def test_wrong_digest_rejected_before_io(sample):
    assert verify(sample).sha256 == sha(sample["raw"])
    value = json.loads(sample["raw"])
    value["receipt_digest"] = "0" * 64
    value["executor_signature"] = (
        sample["key"]
        .sign(wire({k: v for k, v in value.items() if k != "executor_signature"}))
        .hex()
    )
    assert rejected(sample, wire(value), "EVD_DIGEST_MISMATCH").calls == []


def test_wrong_valid_signing_key_rejected_before_io(sample):
    assert verify(sample).sha256 == sha(sample["raw"])
    raw = sign(sample["receipt"], Ed25519PrivateKey.generate())
    assert rejected(sample, raw, "EVD_POP_INVALID").calls == []


def test_attacker_cannot_supply_executor_key_in_receipt(sample):
    assert verify(sample).sha256 == sha(sample["raw"])
    attacker = Ed25519PrivateKey.generate()
    value = copy.deepcopy(sample["receipt"])
    value["executor_public_key"] = attacker.public_key().public_bytes_raw().hex()
    raw = sign(value, attacker)
    assert rejected(sample, raw, "EVD_PARSE_INVALID").calls == []


@pytest.mark.parametrize(
    "field",
    [
        "reserve_request_id",
        "reservation_id",
        "fencing_token",
        "open_permit_digest",
        "runtime_profile_sha256",
        "session_header_sha256",
        "run_id",
        "identity_digest",
    ],
)
def test_wrong_signed_context_before_io(sample, field):
    assert verify(sample).sha256 == sha(sample["raw"])
    value = copy.deepcopy(sample["receipt"])
    if field == "fencing_token":
        value[field] = 2
    elif field in {"reservation_id", "reserve_request_id"}:
        value[field] = "20000000-0000-4000-8000-000000000003"
    elif field == "run_id":
        value[field] = "s11-00000000-0000-5000-8000-000000000000"
    else:
        value[field] = "9" * 64
    assert rejected(sample, sign(value, sample["key"]), "EVD_CONTEXT_MISMATCH").calls == []


@pytest.mark.parametrize("change", ["future", "beforeGrant", "expired", "badIdentity", "boolSeed"])
def test_qualified_context_time_and_identity(sample, change):
    assert verify(sample).sha256 == sha(sample["raw"])
    value = copy.deepcopy(sample["receipt"])
    context = sample["context"]
    if change == "future":
        value["opened_at"] = "2026-10-05T08:00:01.000000Z"
    elif change == "beforeGrant":
        value["opened_at"] = "2026-10-05T07:59:59.000000Z"
    elif change == "expired":
        context = replace(context, now=context.deadline + timedelta(microseconds=1))
    else:
        identity = json.loads(context.run_identity)
        if change == "badIdentity":
            identity["run_id"] = "s11-00000000-0000-5000-8000-000000000000"
        else:
            identity["seed"] = True
        context = replace(context, run_identity=wire(identity))
    assert (
        rejected(sample, sign(value, sample["key"]), "EVD_CONTEXT_MISMATCH", context=context).calls
        == []
    )


@pytest.mark.parametrize("field", evd.REFERENCE_FIELDS)
@pytest.mark.parametrize("change", ["SHA", "schema", "artifactSchema", "unknown", "kind", "path"])
def test_each_reference_and_artifact_binding(sample, field, change):
    assert verify(sample).sha256 == sha(sample["raw"])
    value = copy.deepcopy(sample["receipt"])
    artifacts = dict(sample["artifacts"])
    code = "EVD_REFERENCE_INVALID"
    if change == "SHA":
        value[field]["sha256"] = "9" * 64
    elif change == "schema":
        value[field]["schema_version"] = "wrong-schema-v1"
    elif change == "artifactSchema":
        payload = json.loads(artifacts[field])
        payload["schema_version"] = "wrong-schema-v1"
        artifacts[field] = wire(payload)
        value[field]["sha256"] = sha(artifacts[field])
        value[field]["schema_version"] = "wrong-schema-v1"
    elif change in {"unknown", "kind"}:
        value[field][change] = "EVIDENCE"
        code = "EVD_PARSE_INVALID"
    elif change == "path":
        value[field]["path"] = "/unapproved-root/private.json"
        code = "EVD_PATH_UNSAFE"
    rejected(sample, sign(value, sample["key"]), code, resolver=MemoryResolver(artifacts))


@pytest.mark.parametrize("field", evd.REFERENCE_FIELDS)
def test_missing_artifact(sample, field):
    artifacts = dict(sample["artifacts"])
    del artifacts[field]
    rejected(sample, sample["raw"], "EVD_REFERENCE_INVALID", resolver=MemoryResolver(artifacts))


@pytest.mark.parametrize(
    "field,path,new",
    [
        ("manifest_initial_reference", ("run_id",), "s11-00000000-0000-5000-8000-000000000000"),
        ("manifest_initial_reference", ("status",), "COMPLETED"),
        ("manifest_initial_reference", ("configuration", "executor_fingerprint"), "9" * 64),
        ("start_reference", ("run_id",), "s11-00000000-0000-5000-8000-000000000000"),
        ("start_reference", ("preflight", "sha256"), "9" * 64),
        ("start_reference", ("plan_sha256",), "9" * 64),
        ("preflight_reference", ("git_sha",), "9" * 40),
        ("preflight_reference", ("report_posts",), False),
        ("preflight_reference", ("checked_at",), "2026-10-05T08:00:01.000000Z"),
        ("preflight_reference", ("checked_at",), "2026-10-05T07:44:59.999999Z"),
        ("preflight_reference", ("checks",), []),
        ("plan_reference", ("total_requests",), 49),
    ],
)
def test_signed_artifact_cross_binding(sample, field, path, new):
    assert verify(sample).sha256 == sha(sample["raw"])
    value = copy.deepcopy(sample["receipt"])
    artifacts = dict(sample["artifacts"])
    payload = json.loads(artifacts[field])
    target = payload
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = new
    artifacts[field] = wire(payload)
    value[field]["sha256"] = sha(artifacts[field])
    rejected(
        sample,
        sign(value, sample["key"]),
        "EVD_REFERENCE_INVALID",
        resolver=MemoryResolver(artifacts),
    )


def test_confined_four_artifact_baseline(sample):
    with evd.ConfinedArtifactResolver(str(sample["root"]), expected_owner=os.getuid()) as resolver:
        result = verify(sample, resolver=resolver)
    assert result.sha256 == sha(sample["raw"])
    assert {a.field: a.canonical for a in result.artifacts} == sample["artifacts"]


@pytest.mark.parametrize(
    "path",
    [
        "../escape",
        "runs/../escape",
        "./escape",
        "runs//escape",
        "runs/escape/",
        "runs\\escape",
        "runs/escape\x00",
    ],
)
def test_path_escape_rejected_before_resolver(sample, path):
    value = copy.deepcopy(sample["receipt"])
    value["manifest_initial_reference"]["path"] = path
    assert rejected(sample, sign(value, sample["key"]), "EVD_PATH_UNSAFE").calls == []


@pytest.mark.parametrize(
    "kind", ["symlink", "hardlink", "mode", "directory", "rootAlias", "rootMode", "wrongOwner"]
)
def test_confined_resolver_rejects_unsafe_identity(sample, kind):
    path = Path(sample["context"].artifacts[0].path)
    root = sample["root"]
    owner = os.getuid()
    if kind in {"symlink", "hardlink", "directory"}:
        path.unlink()
        if kind == "symlink":
            path.symlink_to(Path(sample["context"].artifacts[1].path))
        elif kind == "hardlink":
            os.link(sample["context"].artifacts[1].path, path)
        else:
            path.mkdir(mode=0o700)
    elif kind == "mode":
        path.chmod(0o644)
    elif kind == "rootAlias":
        alias = root.parent / (root.name + "-alias")
        alias.symlink_to(root, target_is_directory=True)
        try:
            with pytest.raises(evd.OpenEvidenceError, match="EVD_PATH_UNSAFE"):
                evd.ConfinedArtifactResolver(str(alias), expected_owner=owner)
        finally:
            alias.unlink()
        return
    elif kind == "rootMode":
        root.chmod(0o755)
    else:
        owner += 1
    with pytest.raises(evd.OpenEvidenceError) as error:
        with evd.ConfinedArtifactResolver(str(root), expected_owner=owner) as resolver:
            verify(sample, resolver=resolver)
    assert error.value.code == "EVD_PATH_UNSAFE"


@pytest.mark.parametrize("kind", ["file", "root", "fileDuringLaterRead"])
def test_replacement_races_fail_closed(sample, monkeypatch, kind):
    root = sample["root"]
    with evd.ConfinedArtifactResolver(str(root), expected_owner=os.getuid()) as resolver:
        if kind == "root":
            moved = root.parent / (root.name + "-moved")
            root.rename(moved)
            root.mkdir(mode=0o700)
            try:
                with pytest.raises(evd.OpenEvidenceError, match="EVD_IDENTITY_CHANGED"):
                    verify(sample, resolver=resolver)
            finally:
                root.rmdir()
                moved.rename(root)
        else:
            original = evd.os.read
            reads = 0
            first = Path(sample["context"].artifacts[0].path)

            def racing_read(fd, amount):
                nonlocal reads
                content = original(fd, amount)
                reads += 1
                if reads == (1 if kind == "file" else 3):
                    replacement = root / "replacement.json"
                    replacement.write_bytes(first.read_bytes())
                    replacement.chmod(0o600)
                    os.replace(replacement, first)
                return content

            monkeypatch.setattr(evd.os, "read", racing_read)
            with pytest.raises(evd.OpenEvidenceError, match="EVD_IDENTITY_CHANGED"):
                verify(sample, resolver=resolver)


def test_artifact_byte_bound_rejected(sample):
    context = replace(
        sample["context"],
        artifacts=tuple(replace(spec, max_bytes=1) for spec in sample["context"].artifacts),
    )
    with evd.ConfinedArtifactResolver(str(sample["root"]), expected_owner=os.getuid()) as resolver:
        rejected(sample, sample["raw"], "EVD_REFERENCE_INVALID", context=context, resolver=resolver)


def test_additional_independent_binding_is_enforced(sample):
    spec = sample["context"].artifacts[0]
    expected = replace(spec, bindings=((("timezone",), "UTC"),))
    context = replace(sample["context"], artifacts=(expected, *sample["context"].artifacts[1:]))
    rejected(sample, sample["raw"], "EVD_REFERENCE_INVALID", context=context)


def test_external_symlink_is_rejected_before_any_artifact_read(sample, monkeypatch):
    assert verify(sample).sha256 == sha(sample["raw"])
    path = Path(sample["context"].artifacts[0].path)
    outside = sample["root"].parent / (sample["root"].name + "-outside.json")
    outside.write_bytes(path.read_bytes())
    outside.chmod(0o600)
    path.unlink()
    path.symlink_to(outside)
    reads = []
    original = evd.os.read

    def observed(fd, amount):
        reads.append(fd)
        return original(fd, amount)

    monkeypatch.setattr(evd.os, "read", observed)
    try:
        with evd.ConfinedArtifactResolver(
            str(sample["root"]), expected_owner=os.getuid()
        ) as resolver:
            rejected(sample, sample["raw"], "EVD_PATH_UNSAFE", resolver=resolver)
        assert reads == []
    finally:
        outside.unlink()


@pytest.mark.parametrize(
    "path,new",
    [
        (("timezone",), None),
        (("runtime",), []),
        (("runtime", "python"), 311),
        (("configuration", "protocol", "load", "retries"), False),
        (("configuration", "protocol", "request", "new_tcp_connection_per_request"), 1),
    ],
)
def test_signed_manifest_nested_types_match_existing_contract(sample, path, new):
    assert verify(sample).sha256 == sha(sample["raw"])
    artifacts = dict(sample["artifacts"])
    payload = json.loads(artifacts["manifest_initial_reference"])
    node = payload
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = new
    artifacts["manifest_initial_reference"] = wire(payload)
    receipt = copy.deepcopy(sample["receipt"])
    receipt["manifest_initial_reference"]["sha256"] = sha(artifacts["manifest_initial_reference"])
    rejected(
        sample,
        sign(receipt, sample["key"]),
        "EVD_REFERENCE_INVALID",
        resolver=MemoryResolver(artifacts),
    )


@pytest.mark.parametrize(
    "path,new",
    [
        (("prior_ex10_runs",), False),
        (("acknowledged_not_demonstrated",), 0),
        (("executor_sources", "files"), False),
        (("executor_sources", "extra"), "unexpected"),
        (("preflight", "checks"), True),
    ],
)
def test_signed_start_nested_types_match_existing_contract(sample, path, new):
    artifacts = dict(sample["artifacts"])
    payload = json.loads(artifacts["start_reference"])
    node = payload
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = new
    artifacts["start_reference"] = wire(payload)
    receipt = copy.deepcopy(sample["receipt"])
    receipt["start_reference"]["sha256"] = sha(artifacts["start_reference"])
    rejected(
        sample,
        sign(receipt, sample["key"]),
        "EVD_REFERENCE_INVALID",
        resolver=MemoryResolver(artifacts),
    )


def test_initial_preflight_cannot_postdate_start_or_open(sample):
    assert verify(sample).sha256 == sha(sample["raw"])
    artifacts = dict(sample["artifacts"])
    preflight = json.loads(artifacts["preflight_reference"])
    start = json.loads(artifacts["start_reference"])
    preflight["checked_at"] = "2026-10-05T08:00:30.000000Z"
    artifacts["preflight_reference"] = wire(preflight)
    start["preflight"]["checked_at"] = preflight["checked_at"]
    start["preflight"]["sha256"] = sha(artifacts["preflight_reference"])
    artifacts["start_reference"] = wire(start)
    receipt = copy.deepcopy(sample["receipt"])
    for field in ("preflight_reference", "start_reference"):
        receipt[field]["sha256"] = sha(artifacts[field])
    context = replace(sample["context"], now=sample["context"].now + timedelta(seconds=60))
    rejected(
        sample,
        sign(receipt, sample["key"]),
        "EVD_REFERENCE_INVALID",
        context=context,
        resolver=MemoryResolver(artifacts),
    )
