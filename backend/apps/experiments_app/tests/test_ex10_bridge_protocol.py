"""Offline B4 wire tests. Ephemeral keys stay in memory; no authority/Docker effects."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import sys
import types
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

APP = Path(__file__).resolve().parents[1]
PACKAGE = types.ModuleType("w02_bridge_protocol_fixture")
PACKAGE.__path__ = [str(APP)]
sys.modules[PACKAGE.__name__] = PACKAGE
SPEC = importlib.util.spec_from_file_location(
    PACKAGE.__name__ + ".ex10_bridge_protocol",
    os.environ.get("S11_BRIDGE_PROTOCOL_MODULE_PATH", str(APP / "ex10_bridge_protocol.py")),
)
b4 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = b4
SPEC.loader.exec_module(b4)
NOW = datetime(2026, 10, 5, 8, 20, tzinfo=UTC)
STAMP = "2026-10-05T08:20:00.000000Z"
EXPIRES = "2026-10-05T08:22:00.000000Z"
CAP_DOMAIN = b"S11-EX10-B4-CAPABILITY-v1\n"
REQUEST_DOMAIN = b"S11-EX10-B4-REQUEST-v1\n"
UUID = "10000000-0000-4000-8000-000000000001"


def wire(value):
    return (
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        )
        + "\n"
    ).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def fill(schema, field=""):
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    if "anyOf" in schema:
        if any(part.get("type") == "null" for part in schema["anyOf"]):
            return None
        return fill(schema["anyOf"][0], field)
    if "oneOf" in schema:
        return fill(schema["oneOf"][0], field)
    kind = schema["type"]
    if kind == "object":
        return {k: fill(v, k) for k, v in schema["properties"].items()}
    if kind == "array":
        return [fill(schema["items"]) for _ in range(schema.get("minItems", 0))]
    if kind == "integer":
        return schema.get("minimum", 0)
    if kind == "boolean":
        return False
    if kind == "null":
        return None
    if "pattern" in schema:
        pattern = schema["pattern"]
        if pattern.startswith("^[0-9]{4}"):
            return EXPIRES if field in {"expires_at", "freshness_deadline"} else STAMP
        if pattern.startswith("^[0-9a-f]{8}-"):
            return UUID
        if pattern.startswith("^s11-"):
            return "s11-00000000-0000-5000-8000-000000000000"
        if pattern.startswith("^[0-9a-f]{"):
            return "a" * int(pattern.split("{")[1].split("}")[0])
    if field == "path":
        return "/synthetic/" + field
    return "synthetic-1"


def identity():
    result = {
        "started_at": STAMP,
        "git_sha": "a" * 40,
        "dataset_id": "s11-load-v1",
        "scenario_id": "EX-10",
        "seed": 20260921,
        "execution_nonce": "b" * 64,
    }
    result["run_id"] = b4.make_run_id(
        started_at=NOW,
        git_sha=result["git_sha"],
        dataset_id=result["dataset_id"],
        scenario_id=result["scenario_id"],
        seed=result["seed"],
        execution_nonce=result["execution_nonce"],
    )
    result["identity_digest"] = sha(wire(result))
    return result


def signed_request(value, bridge, holder):
    request = copy.deepcopy(value)
    request["request_digest"] = sha(
        wire(
            {
                k: v
                for k, v in request.items()
                if k not in {"request_digest", "sender_signature", "capability"}
            }
        )
    )
    request["capability"]["request_body_digest"] = request["request_digest"]
    request["capability"]["signature"] = bridge.sign(
        CAP_DOMAIN + wire({k: v for k, v in request["capability"].items() if k != "signature"})
    ).hex()
    request["sender_signature"] = holder.sign(
        REQUEST_DOMAIN + wire({k: v for k, v in request.items() if k != "sender_signature"})
    ).hex()
    return wire(request)


def sample_for(operation):
    schema = next(
        s for s in b4.request_schemas() if s["properties"]["operation"]["const"] == operation
    )
    request = fill(schema)
    bridge, holder = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    request["nonce"] = "b" * 64 if operation == "reserve-start" else "c" * 64
    payload = request["payload"]
    if "run_identity" in payload:
        payload["run_identity"] = identity()
    if "expected_head" in payload:
        payload["expected_head"].update(
            authority_id=request["authority_id"],
            authority_epoch=request["authority_epoch"],
            revision=request["authority_revision"],
        )
    if "expected_authority_id" in payload:
        payload["expected_authority_id"] = request["authority_id"]
        payload["expected_authority_epoch"] = request["authority_epoch"]
    cap = request["capability"]
    for field in [
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
    ]:
        cap[field] = copy.deepcopy(request[field])
    cap.update(
        request_nonce=request["nonce"],
        expected_revision=request["authority_revision"],
        expected_run_id=payload.get("run_identity", {}).get("run_id"),
        reservation_id=payload.get("reservation_id"),
        reservation_digest=payload.get("reservation_digest"),
        fencing_token=payload.get("fencing_token"),
        holder_public_key_hex=holder.public_key().public_bytes_raw().hex(),
    )
    context_args = {
        field: request[field]
        for field in [
            "request_id",
            "nonce",
            "exchange_id",
            "operation",
            "session_id",
            "session_header_sha256",
            "runtime_profile_sha256",
            "authority_id",
            "authority_epoch",
            "authority_revision",
            "source_revision",
            "project",
        ]
    }
    context_args.update(
        authorization_reference=wire(request["authorization_reference"]),
        deployment_verification_sha256=cap["deployment_verification_sha256"],
        container_identity_digest=cap["container_identity_digest"],
        expected_run_id=cap["expected_run_id"],
        reservation_id=cap["reservation_id"],
        reservation_digest=cap["reservation_digest"],
        fencing_token=cap["fencing_token"],
        holder_public_key_hex=cap["holder_public_key_hex"],
        expected_payload=wire(payload),
        now=NOW,
    )
    cls = b4.RecoveryRequestContext if operation in b4.RECOVERY_OPERATIONS else b4.RequestContext
    if cls is b4.RecoveryRequestContext:
        context_args["operator_authorization_reference"] = wire(
            payload["operator_authorization_reference"]
        )
    context = cls(**context_args)
    pin = b4.PublicKeyPin(cap["bridge_key_id"], cap["bridge_key_epoch"], bridge.public_key())
    return {
        "request": request,
        "raw": signed_request(request, bridge, holder),
        "bridge": bridge,
        "holder": holder,
        "context": context,
        "pin": pin,
    }


@pytest.fixture
def sample():
    return sample_for("reserve-start")


def verify(sample, *, raw=None, context=None, pin=None, holder=None):
    kwargs = {
        "expected_context": sample["context"] if context is None else context,
        "bridge_pin": sample["pin"] if pin is None else pin,
        "holder_public_key": sample["holder"].public_key() if holder is None else holder,
    }
    return b4.verify_request(sample["raw"] if raw is None else raw, **kwargs)


@pytest.mark.parametrize("operation", [op.value for op in b4.Operation])
def test_ten_closed_request_variants_and_separate_recovery(operation):
    sample = sample_for(operation)
    if operation in b4.RECOVERY_OPERATIONS:
        with pytest.raises(b4.BridgeProtocolError, match="AUTHORIZATION_FAILED"):
            verify(sample)
        result = b4.verify_recovery_request(
            sample["raw"],
            expected_context=sample["context"],
            bridge_pin=sample["pin"],
            operator_public_key=sample["holder"].public_key(),
        )
    else:
        result = verify(sample)
    assert result.request.operation.value == operation
    assert "sender_signature" not in repr(result)
    assert sample["raw"].decode() not in repr(result)


def test_request_digest_capability_and_sender_signature_are_acyclic(sample):
    result = verify(sample)
    value = json.loads(sample["raw"])
    assert result.request.sha256 == sha(sample["raw"])
    expected = sha(
        wire(
            {
                k: v
                for k, v in value.items()
                if k not in {"capability", "request_digest", "sender_signature"}
            }
        )
    )
    assert b4.request_digest(result.request) == value["request_digest"] == expected
    altered = copy.deepcopy(value)
    altered["capability"]["signature"] = "0" * 128
    assert b4.request_digest(b4.parse_request_bytes(wire(altered))) == expected
    sample["bridge"].public_key().verify(
        bytes.fromhex(value["capability"]["signature"]),
        CAP_DOMAIN + wire({k: v for k, v in value["capability"].items() if k != "signature"}),
    )
    sample["holder"].public_key().verify(
        bytes.fromhex(value["sender_signature"]),
        REQUEST_DOMAIN + wire({k: v for k, v in value.items() if k != "sender_signature"}),
    )


@pytest.mark.parametrize(
    "field",
    [
        "request_id",
        "nonce",
        "exchange_id",
        "operation",
        "session_id",
        "session_header_sha256",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "authority_revision",
        "source_revision",
        "project",
    ],
)
def test_validly_signed_wrong_envelope_binding(sample, field):
    assert verify(sample).request.sha256 == sha(sample["raw"])
    request = copy.deepcopy(sample["request"])
    if field == "operation":
        other = sample_for("inspect-history")
        raw = other["raw"]
    else:
        old = request[field]
        request[field] = (
            old + 1
            if type(old) is int
            else UUID.replace("1000", "2000")
            if len(old) == 36
            else "9" * len(old)
        )
        raw = signed_request(request, sample["bridge"], sample["holder"])
    with pytest.raises(b4.BridgeProtocolError):
        verify(sample, raw=raw)


@pytest.mark.parametrize(
    "field",
    [
        "namespace",
        "request_nonce",
        "request_body_digest",
        "holder_public_key_hex",
        "authority_id",
        "authority_epoch",
        "session_id",
        "session_header_sha256",
        "runtime_profile_sha256",
        "source_revision",
        "expected_revision",
        "expected_run_id",
        "reservation_id",
        "reservation_digest",
        "fencing_token",
        "deployment_verification_sha256",
        "container_identity_digest",
        "exchange_id",
        "operation",
        "authorization_reference",
    ],
)
def test_validly_signed_wrong_capability_claim(sample, field):
    assert verify(sample).request.sha256 == sha(sample["raw"])
    value = json.loads(sample["raw"])
    cap = value["capability"]
    old = cap[field]
    if field == "request_body_digest":
        cap[field] = "9" * 64
    elif field == "authorization_reference":
        cap[field]["sha256"] = "9" * 64
    elif old is None:
        cap[field] = (
            1 if field == "fencing_token" else "9" * 64 if field == "reservation_digest" else UUID
        )
    elif type(old) is int:
        cap[field] = old + 1
    elif field == "expected_run_id":
        cap[field] = "s11-00000000-0000-5000-8000-000000000000"
    elif field == "operation":
        cap[field] = "inspect-history"
    elif field == "namespace":
        cap[field] = "other-namespace"
    else:
        cap[field] = (
            "9" * len(old)
            if len(old) in {40, 64}
            else UUID.replace("1000", "2000")
            if len(old) == 36
            else "different-1"
        )
    cap["signature"] = (
        sample["bridge"]
        .sign(CAP_DOMAIN + wire({k: v for k, v in cap.items() if k != "signature"}))
        .hex()
    )
    value["sender_signature"] = (
        sample["holder"]
        .sign(REQUEST_DOMAIN + wire({k: v for k, v in value.items() if k != "sender_signature"}))
        .hex()
    )
    with pytest.raises(b4.BridgeProtocolError):
        verify(sample, raw=wire(value))


@pytest.mark.parametrize("level", ["top", "capability", "payload", "reference", "run_identity"])
def test_unknown_fields_fail_closed(sample, level):
    value = copy.deepcopy(sample["request"])
    node = {
        "top": value,
        "capability": value["capability"],
        "payload": value["payload"],
        "reference": value["authorization_reference"],
        "run_identity": value["payload"]["run_identity"],
    }[level]
    node["arbitrary_command"] = "denied"
    with pytest.raises(b4.BridgeProtocolError, match="SCHEMA_INVALID"):
        verify(sample, raw=signed_request(value, sample["bridge"], sample["holder"]))


@pytest.mark.parametrize("field", ["authority_epoch", "authority_revision"])
@pytest.mark.parametrize("number", [True, 1.0, 2**53, -1])
def test_non_integer_and_overflow(sample, field, number):
    value = copy.deepcopy(sample["request"])
    value[field] = number
    with pytest.raises(b4.BridgeProtocolError, match="SCHEMA_INVALID"):
        b4.parse_request_bytes(wire(value))


@pytest.mark.parametrize("level", ["request", "capability"])
@pytest.mark.parametrize("change", ["expired", "tooLong", "future", "invalidGregorian"])
def test_freshness_and_gregorian_clock(sample, level, change):
    value = copy.deepcopy(sample["request"])
    node = value if level == "request" else value["capability"]
    if change == "expired":
        node["expires_at"] = STAMP
    elif change == "tooLong":
        node["expires_at"] = "2026-10-05T08:22:00.000001Z"
    elif change == "future":
        node["issued_at"] = "2026-10-05T08:20:05.000001Z"
    else:
        node["issued_at"] = "2026-02-30T08:20:00.000000Z"
    with pytest.raises(b4.BridgeProtocolError):
        verify(sample, raw=signed_request(value, sample["bridge"], sample["holder"]))


@pytest.mark.parametrize(
    "change", ["wrongBridge", "wrongHolder", "holderDomain", "capabilityDomain", "body"]
)
def test_signature_pin_and_domain_separation(sample, change):
    assert verify(sample).request.sha256 == sha(sample["raw"])
    value = json.loads(sample["raw"])
    if change == "wrongBridge":
        pin = replace(sample["pin"], public_key=Ed25519PrivateKey.generate().public_key())
        with pytest.raises(b4.BridgeProtocolError, match="AUTHENTICATION_FAILED"):
            verify(sample, pin=pin)
        return
    if change == "wrongHolder":
        with pytest.raises(b4.BridgeProtocolError, match="AUTHENTICATION_FAILED"):
            verify(sample, holder=Ed25519PrivateKey.generate().public_key())
        return
    if change == "holderDomain":
        value["sender_signature"] = (
            sample["holder"]
            .sign(
                b"S11-EX10-B4-PROPOSAL-v1\n"
                + wire({k: v for k, v in value.items() if k != "sender_signature"})
            )
            .hex()
        )
    elif change == "capabilityDomain":
        cap = value["capability"]
        cap["signature"] = (
            sample["bridge"]
            .sign(
                b"S11-EX10-B4-OPEN-ONE-v1\n"
                + wire({k: v for k, v in cap.items() if k != "signature"})
            )
            .hex()
        )
        value["sender_signature"] = (
            sample["holder"]
            .sign(
                REQUEST_DOMAIN + wire({k: v for k, v in value.items() if k != "sender_signature"})
            )
            .hex()
        )
    else:
        value["payload"]["acknowledgments"] = [
            {
                "run_id": identity()["run_id"],
                "authority_revision": 0,
                "terminal_evidence_reference": fill(
                    b4.schema_definitions()["reservation"]["properties"][
                        "operator_authorization_reference"
                    ]
                ),
            }
        ]
    with pytest.raises(b4.BridgeProtocolError, match="AUTHENTICATION_FAILED"):
        verify(sample, raw=wire(value))


def test_signed_body_substitution_rejected_by_full_expected_payload(sample):
    value = copy.deepcopy(sample["request"])
    value["payload"]["preflight_reference"]["sha256"] = "9" * 64
    with pytest.raises(b4.BridgeProtocolError, match="AUTHORIZATION_FAILED"):
        verify(sample, raw=signed_request(value, sample["bridge"], sample["holder"]))


def test_expected_context_cannot_be_partial(sample):
    context = replace(sample["context"], expected_payload=wire({}))
    with pytest.raises(b4.BridgeProtocolError, match="AUTHORIZATION_FAILED"):
        verify(sample, context=context)


def signed(value, key, domain):
    result = copy.deepcopy(value)
    result["signature"] = key.sign(
        domain + wire({k: v for k, v in result.items() if k != "signature"})
    ).hex()
    return wire(result)


def permit_sample(sample):
    value = fill(b4.schema_definitions()["open_one_permit"])
    value.update(
        run_identity=identity(),
        attempt_nonce="b" * 64,
        base_authority_revision=0,
        expected_open_revision=1,
        holder_public_key_hex=sample["holder"].public_key().public_bytes_raw().hex(),
    )
    value["permit_digest"] = sha(
        wire({k: v for k, v in value.items() if k not in {"permit_digest", "signature"}})
    )
    raw = signed(value, sample["bridge"], b"S11-EX10-B4-OPEN-ONE-v1\n")
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
    context = b4.PermitContext(
        wire({k: v for k, v in value.items() if k not in omitted}),
        NOW + timedelta(seconds=120),
        NOW,
    )
    pin = b4.PublicKeyPin(
        value["bridge_key_id"], value["bridge_key_epoch"], sample["bridge"].public_key()
    )
    return value, raw, context, pin


def test_signed_open_one_has_35_fields_and_two_acyclic_preimages(sample):
    value, raw, context, pin = permit_sample(sample)
    result = b4.verify_open_one(
        raw,
        expected_context=context,
        bridge_pin=pin,
        holder_public_key=sample["holder"].public_key(),
    )
    assert len(value) == 35
    assert b4.open_permit_digest(result) == value["permit_digest"]
    assert result.sha256 == sha(raw) != value["permit_digest"]


@pytest.mark.parametrize(
    "change",
    [
        "digest",
        "domain",
        "holder",
        "revision",
        "expired",
        "future",
        "deadline",
        "partialContext",
        "runIdentity",
        "nonce",
    ],
)
def test_open_one_semantic_rejection(sample, change):
    value, raw, context, pin = permit_sample(sample)
    b4.verify_open_one(
        raw,
        expected_context=context,
        bridge_pin=pin,
        holder_public_key=sample["holder"].public_key(),
    )
    if change == "digest":
        value["permit_digest"] = "0" * 64
    elif change == "domain":
        raw = signed(value, sample["bridge"], b"S11-EX10-B4-CAPABILITY-v1\n")
    elif change == "holder":
        with pytest.raises(b4.BridgeProtocolError, match="AUTHENTICATION_FAILED"):
            b4.verify_open_one(
                raw,
                expected_context=context,
                bridge_pin=pin,
                holder_public_key=Ed25519PrivateKey.generate().public_key(),
            )
        return
    elif change == "revision":
        value["expected_open_revision"] = 2
    elif change == "expired":
        context = replace(context, now=NOW + timedelta(seconds=120))
    elif change == "future":
        value["issued_at"] = "2026-10-05T08:20:05.000001Z"
    elif change == "deadline":
        context = replace(context, reservation_deadline=NOW + timedelta(seconds=119))
    elif change == "partialContext":
        context = replace(context, expected_projection=wire({}))
    elif change == "runIdentity":
        value["run_identity"]["run_id"] = "s11-00000000-0000-5000-8000-000000000000"
    else:
        value["attempt_nonce"] = "9" * 64
    if change not in {"domain", "digest"}:
        value["permit_digest"] = sha(
            wire({k: v for k, v in value.items() if k not in {"permit_digest", "signature"}})
        )
    if change != "domain":
        raw = signed(value, sample["bridge"], b"S11-EX10-B4-OPEN-ONE-v1\n")
    if change in {"revision", "runIdentity", "nonce"}:
        expected = json.loads(context.expected_projection)
        for field in expected:
            expected[field] = value[field]
        context = replace(context, expected_projection=wire(expected))
    with pytest.raises(b4.BridgeProtocolError):
        b4.verify_open_one(
            raw,
            expected_context=context,
            bridge_pin=pin,
            holder_public_key=sample["holder"].public_key(),
        )


def reservation_value(state="PENDING"):
    schema = b4.schema_definitions()["reservation"]
    value = fill(schema)
    value.update(
        run_identity=identity(),
        run_id=identity()["run_id"],
        nonce="b" * 64,
        previous_authority_revision=0,
        expected_next_revision=1,
        state=state,
    )
    if state == "UNCERTAIN":
        value["uncertainty_codes"] = ["RESPONSE_LOST"]
    if state == "COMMITTED":
        for field in [
            "reservation_receipt_reference",
            "open_witness_reference",
            "evidence_receipt_reference",
        ]:
            value[field] = fill(schema["properties"][field]["anyOf"][0])
        value["open_witness_reference"]["revision"] = 1
    if state == "CANCELLED_MANUALLY":
        value["manual_resolution_reference"] = fill(
            schema["properties"]["manual_resolution_reference"]["anyOf"][0]
        )
    value["reservation_digest"] = sha(wire({k: value[k] for k in b4.RESERVATION_INTENT_FIELDS}))
    value["record_digest"] = sha(wire({k: v for k, v in value.items() if k != "record_digest"}))
    return value


@pytest.mark.parametrize("state", ["PENDING", "COMMITTED", "UNCERTAIN", "CANCELLED_MANUALLY"])
def test_reservation39_state_shapes_and_distinct_intent_record_digests(state):
    value = reservation_value(state)
    result = b4.qualify_reservation(wire(value))
    assert len(value) == 39 and len(b4.RESERVATION_INTENT_FIELDS) == 27
    assert b4.reservation_digest(result) == value["reservation_digest"]
    assert b4.reservation_record_digest(result) == value["record_digest"]
    initial = reservation_value()
    assert value["reservation_digest"] == initial["reservation_digest"]
    if state != "PENDING":
        assert value["record_digest"] != initial["record_digest"]


@pytest.mark.parametrize(
    "change",
    [
        "intentDigest",
        "recordDigest",
        "nextRevision",
        "nonce",
        "identity",
        "sequence",
        "uncertainEmpty",
        "committedMissing",
    ],
)
def test_reservation_invariants_fail_closed(change):
    value = reservation_value()
    b4.qualify_reservation(wire(value))
    if change == "intentDigest":
        value["reservation_digest"] = "9" * 64
    elif change == "recordDigest":
        value["record_digest"] = "9" * 64
    elif change == "nextRevision":
        value["expected_next_revision"] = 0
    elif change == "nonce":
        value["nonce"] = "9" * 64
    elif change == "identity":
        value["run_identity"]["run_id"] = "s11-00000000-0000-5000-8000-000000000000"
    elif change == "sequence":
        value["record_sequence"] = 2
    elif change == "uncertainEmpty":
        value["state"] = "UNCERTAIN"
    else:
        value["state"] = "COMMITTED"
    if change not in {"intentDigest", "recordDigest"}:
        value["reservation_digest"] = sha(wire({k: value[k] for k in b4.RESERVATION_INTENT_FIELDS}))
    if change != "recordDigest":
        value["record_digest"] = sha(wire({k: v for k, v in value.items() if k != "record_digest"}))
    with pytest.raises(b4.BridgeProtocolError):
        b4.qualify_reservation(wire(value))


def witness_sample():
    key = Ed25519PrivateKey.generate()
    receipt = fill(b4.schema_definitions()["reservation_receipt"])
    receipt.update(sequence=1, transition="RESERVE", state="PENDING")
    context = b4.ReceiptContext(
        wire({k: v for k, v in receipt.items() if k not in {"signature", "issued_at"}}), NOW
    )
    pin = b4.PublicKeyPin(receipt["key_id"], receipt["key_epoch"], key.public_key())
    return receipt, key, context, pin


def test_independent_b4_reserve_receipt20_domain_and_complete_context():
    receipt, key, context, pin = witness_sample()
    raw = signed(receipt, key, b"S11-EX10-B4-RESERVATION-v1\n")
    result = b4.verify_reservation_receipt(raw, expected_context=context, witness_pin=pin)
    assert len(receipt) == 20 and result.sha256 == sha(raw)
    with pytest.raises(b4.BridgeProtocolError, match="AUTHENTICATION_FAILED"):
        b4.verify_reservation_receipt(
            signed(receipt, key, b"S11-EX10-AUTHORITY-WITNESS-v1\n"),
            expected_context=context,
            witness_pin=pin,
        )
    with pytest.raises(b4.BridgeProtocolError, match="AUTHORIZATION_FAILED"):
        b4.verify_reservation_receipt(
            raw, expected_context=replace(context, expected_projection=wire({})), witness_pin=pin
        )


@pytest.mark.parametrize(
    "field",
    [
        "sequence",
        "fencing_token",
        "reservation_id",
        "reservation_digest",
        "base_authority_witness_digest",
        "operation_id",
        "key_epoch",
        "transition",
    ],
)
def test_validly_signed_b4_receipt_wrong_bound_source(field):
    receipt, key, context, pin = witness_sample()
    original = signed(receipt, key, b"S11-EX10-B4-RESERVATION-v1\n")
    b4.verify_reservation_receipt(original, expected_context=context, witness_pin=pin)
    old = receipt[field]
    receipt[field] = (
        old + 1
        if type(old) is int
        else "COMMIT_OPEN"
        if field == "transition"
        else UUID.replace("1000", "2000")
        if len(old) == 36
        else "9" * 64
    )
    with pytest.raises(b4.BridgeProtocolError):
        b4.verify_reservation_receipt(
            signed(receipt, key, b"S11-EX10-B4-RESERVATION-v1\n"),
            expected_context=context,
            witness_pin=pin,
        )


def head_sample():
    key = Ed25519PrivateKey.generate()
    value = fill(b4.schema_definitions()["coordination_head"])
    value.update(sequence=5, fencing_token=3, expires_at="2026-10-05T08:20:30.000000Z")
    omitted = {"signature", "issued_at", "expires_at", "sequence", "fencing_token"}
    context = b4.CoordinationHeadContext(
        wire({k: v for k, v in value.items() if k not in omitted}), 5, 3, NOW
    )
    pin = b4.PublicKeyPin(value["key_id"], value["key_epoch"], key.public_key())
    return value, key, context, pin


def test_coordination_head14_fresh_challenge_and_floors():
    value, key, context, pin = head_sample()
    raw = signed(value, key, b"S11-EX10-B4-COORDINATION-HEAD-v1\n")
    result = b4.verify_coordination_head(raw, expected_context=context, witness_pin=pin)
    assert len(value) == 14 and result.sha256 == sha(raw)


@pytest.mark.parametrize(
    "change",
    [
        "challenge",
        "sequence",
        "fence",
        "expired",
        "tooLong",
        "future",
        "wrongDomain",
        "partialContext",
    ],
)
def test_coordination_head_cannot_replay_or_rollback(change):
    value, key, context, pin = head_sample()
    raw = signed(value, key, b"S11-EX10-B4-COORDINATION-HEAD-v1\n")
    b4.verify_coordination_head(raw, expected_context=context, witness_pin=pin)
    if change == "challenge":
        value["challenge"] = "9" * 64
    elif change == "sequence":
        value["sequence"] = 4
    elif change == "fence":
        value["fencing_token"] = 2
    elif change == "expired":
        context = replace(context, now=NOW + timedelta(seconds=30))
    elif change == "tooLong":
        value["expires_at"] = "2026-10-05T08:20:30.000001Z"
    elif change == "future":
        value["issued_at"] = "2026-10-05T08:20:05.000001Z"
    elif change == "partialContext":
        context = replace(context, expected_projection=wire({}))
    domain = (
        b"S11-EX10-B4-RESERVATION-v1\n"
        if change == "wrongDomain"
        else b"S11-EX10-B4-COORDINATION-HEAD-v1\n"
    )
    with pytest.raises(b4.BridgeProtocolError):
        b4.verify_coordination_head(
            signed(value, key, domain), expected_context=context, witness_pin=pin
        )


def test_expanded_schema_rules_match_frozen_authority_digests():
    values = [b4.schema_definitions(), b4.request_schemas(), b4.response_schemas()]
    assert [sha(wire(v)) for v in values] == [
        "972f418c4250a0b0aa61591c421ef357dcc57c35364ffc1ec14f0c9ef6cfc6dc",
        "289c6faa3b6f5a80da62602cd94a57deac53bbc7792f967289782daf98417eb3",
        "1738a9b9d5ee7dcfb9e11c7fa4fbc560f6163435ed6ac4138dfe005f5c590e17",
    ]


def response_sample(operation="inspect-history", status="OK"):
    sample = sample_for(operation)
    request = (
        b4.verify_recovery_request(
            sample["raw"],
            expected_context=sample["context"],
            bridge_pin=sample["pin"],
            operator_public_key=sample["holder"].public_key(),
        )
        if operation in b4.RECOVERY_OPERATIONS
        else verify(sample)
    )
    schema = next(
        s
        for s in b4.response_schemas()
        if s["properties"]["operation"]["const"] == operation
        and s["properties"]["status"]["const"] == status
    )
    value = fill(schema)
    req = json.loads(sample["raw"])
    for field in [
        "request_id",
        "nonce",
        "exchange_id",
        "operation",
        "request_digest",
        "session_id",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
    ]:
        value[field] = copy.deepcopy(req[field])
    value.update(
        authority_revision_before=req["authority_revision"],
        authority_revision_after=req["authority_revision"],
        bridge_key_id=sample["pin"].key_id,
        bridge_key_epoch=sample["pin"].key_epoch,
        run_id=req["capability"]["expected_run_id"],
        reservation_id=req["capability"]["reservation_id"],
    )
    if operation == "commit-open" and status == "OK":
        value.update(
            authority_revision_after=req["authority_revision"] + 1,
            run_id=req["payload"]["run_identity"]["run_id"],
            reservation_id=req["payload"]["reservation_id"],
        )
    raw = signed_response(value, sample["bridge"])
    return value, raw, sample, request


def signed_response(value, key):
    data = copy.deepcopy(value)
    data["result_digest"] = sha(wire(data["result"]))
    data["response_digest"] = sha(
        wire({k: v for k, v in data.items() if k not in {"response_digest", "signature"}})
    )
    return signed(data, key, b"S11-EX10-B4-RESPONSE-v1\n")


@pytest.mark.parametrize("operation", [op.value for op in b4.Operation])
@pytest.mark.parametrize("status", ["OK", "REJECTED", "UNCERTAIN"])
def test_response_operation_status_variants_and_result_digests(operation, status):
    value, raw, sample, request = response_sample(operation, status)
    result = b4.verify_response(
        raw, authenticated_request=request, bridge_pin=sample["pin"], now=NOW
    )
    data = result.to_dict()
    assert data["result_digest"] == sha(wire(data["result"]))
    assert data["response_digest"] == sha(
        wire({k: v for k, v in data.items() if k not in {"response_digest", "signature"}})
    )
    assert result.sha256 == sha(raw)
    if status != "OK":
        assert data["result"] is None


@pytest.mark.parametrize(
    "change",
    [
        "requestId",
        "nonce",
        "exchange",
        "profile",
        "authority",
        "epoch",
        "revision",
        "resultDigest",
        "responseDigest",
        "wrongKey",
        "wrongDomain",
        "future",
    ],
)
def test_signed_response_wrong_binding_rejected(change):
    value, raw, sample, request = response_sample()
    b4.verify_response(raw, authenticated_request=request, bridge_pin=sample["pin"], now=NOW)
    fields = {
        "requestId": "request_id",
        "nonce": "nonce",
        "exchange": "exchange_id",
        "profile": "runtime_profile_sha256",
        "authority": "authority_id",
        "epoch": "authority_epoch",
        "revision": "authority_revision_after",
        "resultDigest": "result_digest",
        "responseDigest": "response_digest",
    }
    pin = sample["pin"]
    if change in fields:
        field = fields[change]
        old = value[field]
        value[field] = (
            old + 1
            if type(old) is int
            else UUID.replace("1000", "2000")
            if len(old) == 36
            else "9" * 64
        )
    elif change == "wrongKey":
        pin = replace(pin, public_key=Ed25519PrivateKey.generate().public_key())
    elif change == "future":
        value["completed_at"] = "2026-10-05T08:20:05.000001Z"
    domain = (
        b"S11-EX10-B4-RESERVATION-v1\n" if change == "wrongDomain" else b"S11-EX10-B4-RESPONSE-v1\n"
    )
    if change not in {"resultDigest", "responseDigest"}:
        value = json.loads(signed_response(value, sample["bridge"]))
    raw = signed(value, sample["bridge"], domain)
    with pytest.raises(b4.BridgeProtocolError):
        b4.verify_response(raw, authenticated_request=request, bridge_pin=pin, now=NOW)


@pytest.mark.parametrize(
    "operation,status,field",
    [
        ("commit-open", "UNCERTAIN", "run_id"),
        ("commit-open", "UNCERTAIN", "reservation_id"),
        ("record-uncertain", "UNCERTAIN", "reservation_id"),
        ("inspect-reservation", "OK", "reservation_id"),
    ],
)
def test_signed_response_preserves_known_identifiers_for_every_status(operation, status, field):
    value, raw, sample, request = response_sample(operation, status)
    b4.verify_response(raw, authenticated_request=request, bridge_pin=sample["pin"], now=NOW)
    assert value[field] is not None
    value[field] = (
        "s11-00000000-0000-5000-8000-000000000000"
        if field == "run_id"
        else UUID.replace("1000", "2000")
    )
    raw = signed_response(value, sample["bridge"])
    with pytest.raises(b4.BridgeProtocolError):
        b4.verify_response(raw, authenticated_request=request, bridge_pin=sample["pin"], now=NOW)


def challenge_sample(sample):
    value = fill(b4.schema_definitions()["host_challenge"])
    value.update(
        exchange_id=sample["request"]["exchange_id"],
        bridge_key_id=sample["pin"].key_id,
        bridge_key_epoch=sample["pin"].key_epoch,
    )
    raw = signed(value, sample["bridge"], b"S11-EX10-B4-HOST-CHALLENGE-v1\n")
    context = b4.ReceiptContext(
        wire({k: v for k, v in value.items() if k not in {"signature", "issued_at", "expires_at"}}),
        NOW,
    )
    message = b4.verify_host_challenge(raw, expected_context=context, bridge_pin=sample["pin"])
    return value, message


def test_proposal_pop_and_handshake_framing(sample):
    challenge_value, challenge = challenge_sample(sample)
    value = fill(b4.schema_definitions()["open_proposal"])
    body = {
        k: v
        for k, v in json.loads(sample["raw"]).items()
        if k not in {"capability", "request_digest", "sender_signature"}
    }
    value.update(
        exchange_id=body["exchange_id"],
        host_challenge_digest=challenge.sha256,
        holder_public_key_hex=sample["holder"].public_key().public_bytes_raw().hex(),
        reserve_request_body=body,
    )
    raw = signed(value, sample["holder"], b"S11-EX10-B4-PROPOSAL-v1\n")
    proposal = b4.verify_open_proposal(
        raw, challenge=challenge, holder_public_key=sample["holder"].public_key()
    )
    assert proposal.sha256 == sha(raw)
    frame = {
        "schema_version": "s11-ex10-b4-frame-v1",
        "exchange_id": body["exchange_id"],
        "sequence": 2,
        "kind": "PROPOSE_OPEN",
        "direction": "EXECUTOR_TO_HOST",
        "body": json.loads(raw),
    }
    message = b4.parse_handshake_frame_bytes(wire(frame))
    encoded = b4.encode_frame(message)
    assert int.from_bytes(encoded[:4], "big") == len(message.canonical)
    assert b4.decode_frame(encoded, exchange="START").canonical == message.canonical
    with pytest.raises(b4.BridgeProtocolError, match="AUTHENTICATION_FAILED"):
        b4.verify_open_proposal(
            signed(value, sample["holder"], b"S11-EX10-B4-RPC-PROPOSAL-v1\n"),
            challenge=challenge,
            holder_public_key=sample["holder"].public_key(),
        )
    frame["body"]["exchange_id"] = UUID.replace("1000", "2000")
    with pytest.raises(b4.BridgeProtocolError, match="REFERENCE_MISMATCH"):
        b4.parse_handshake_frame_bytes(wire(frame))


def test_single_rpc_proposal_has_separate_closed_domain():
    sample = sample_for("inspect-coordination")
    _, challenge = challenge_sample(sample)
    value = fill(b4.schema_definitions()["single_rpc_proposal"])
    body = {
        k: v
        for k, v in json.loads(sample["raw"]).items()
        if k not in {"capability", "request_digest", "sender_signature"}
    }
    value.update(
        exchange_id=body["exchange_id"],
        host_challenge_digest=challenge.sha256,
        holder_public_key_hex=sample["holder"].public_key().public_bytes_raw().hex(),
        request_body=body,
    )
    raw = signed(value, sample["holder"], b"S11-EX10-B4-RPC-PROPOSAL-v1\n")
    result = b4.verify_single_rpc_proposal(
        raw, challenge=challenge, holder_public_key=sample["holder"].public_key()
    )
    assert result.sha256 == sha(raw)
    frame = {
        "schema_version": "s11-ex10-b4-frame-v1",
        "exchange_id": body["exchange_id"],
        "sequence": 2,
        "kind": "RPC_PROPOSAL",
        "direction": "EXECUTOR_TO_HOST",
        "body": json.loads(raw),
    }
    parsed = b4.parse_single_rpc_frame_bytes(wire(frame))
    assert b4.decode_frame(b4.encode_frame(parsed), exchange="SINGLE_RPC").canonical == wire(frame)


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"\x00\x00\x00\x00",
        b"\xff\xff\xff\xff{}\n",
        b"\x00\x00\x00\x03{}\nextra",
        b"\x00\x00\x00\x04{}\n",
    ],
)
def test_bounded_framing_rejects_truncation_extra_and_oversized_lengths(raw):
    with pytest.raises(b4.BridgeProtocolError):
        b4.decode_frame(raw, exchange="START")


def test_fixed_signing_preimage_api_has_no_caller_chosen_domain(sample):
    request = b4.parse_request_bytes(sample["raw"])
    value = request.to_dict()
    assert b4.signing_preimage(request) == REQUEST_DOMAIN + wire(
        {k: v for k, v in value.items() if k != "sender_signature"}
    )
    cap = b4.parse_capability_bytes(wire(value["capability"]))
    assert b4.signing_preimage(cap) == CAP_DOMAIN + wire(
        {k: v for k, v in value["capability"].items() if k != "signature"}
    )
    with pytest.raises(b4.BridgeProtocolError):
        b4.signing_preimage(b4.ClosedMessage("arbitrary-domain", b"{}\n"))


def test_schema_accessors_do_not_mutate_private_contract_rules():
    first = b4.schema_definitions()
    first["reservation"]["required"].clear()
    second = b4.schema_definitions()
    assert len(second["reservation"]["required"]) == 39


@pytest.mark.parametrize("field", ["session_id", "session_header_sha256", "runtime_profile_sha256"])
def test_coherent_signed_request_and_capability_substitution_rejected(sample, field):
    assert verify(sample).request.sha256 == sha(sample["raw"])
    value = copy.deepcopy(sample["request"])
    changed = UUID.replace("1000", "2000") if field == "session_id" else "9" * 64
    value[field] = changed
    value["capability"][field] = changed
    raw = signed_request(value, sample["bridge"], sample["holder"])
    with pytest.raises(b4.BridgeProtocolError, match="AUTHORIZATION_FAILED"):
        verify(sample, raw=raw)


def test_unknown_operation_is_rejected_before_admission(sample):
    assert b4.parse_request_bytes(sample["raw"]).operation is b4.Operation.RESERVE_START
    value = copy.deepcopy(sample["request"])
    value["operation"] = "execute-shell"
    value["capability"]["operation"] = "execute-shell"
    raw = signed_request(value, sample["bridge"], sample["holder"])
    with pytest.raises(b4.BridgeProtocolError, match="UNKNOWN_OPERATION"):
        b4.parse_request_bytes(raw)


@pytest.mark.parametrize(
    "change", ["none", "exchange", "bodyExchange", "permit", "bodyPermit", "domain", "holder"]
)
def test_open_evidence_frame_pop_and_request_binding(change):
    sample = sample_for("commit-open")
    request = json.loads(sample["raw"])
    body = {
        k: v
        for k, v in request.items()
        if k not in {"capability", "request_digest", "sender_signature"}
    }
    value = fill(b4.schema_definitions()["open_evidence_frame"])
    value.update(
        exchange_id=request["exchange_id"],
        open_permit_digest=body["payload"]["open_permit_digest"],
        commit_request_body=body,
    )
    domain = b"S11-EX10-B4-EVIDENCE-FRAME-v1\n"
    key = sample["holder"].public_key()
    kwargs = {
        "exchange_id": request["exchange_id"],
        "open_permit_digest": body["payload"]["open_permit_digest"],
        "holder_public_key": key,
    }
    original = signed(value, sample["holder"], domain)
    assert b4.verify_evidence_frame(original, **kwargs).sha256 == sha(original)
    if change == "none":
        return
    if change == "exchange":
        value["exchange_id"] = UUID.replace("1000", "2000")
    elif change == "bodyExchange":
        value["commit_request_body"]["exchange_id"] = UUID.replace("1000", "2000")
    elif change == "permit":
        value["open_permit_digest"] = "9" * 64
    elif change == "bodyPermit":
        value["commit_request_body"]["payload"]["open_permit_digest"] = "9" * 64
    elif change == "domain":
        domain = b"S11-EX10-B4-PROPOSAL-v1\n"
    else:
        kwargs["holder_public_key"] = Ed25519PrivateKey.generate().public_key()
    with pytest.raises(b4.BridgeProtocolError):
        b4.verify_evidence_frame(signed(value, sample["holder"], domain), **kwargs)
