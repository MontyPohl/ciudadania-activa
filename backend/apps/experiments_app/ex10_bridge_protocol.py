"""Pure, closed B4 wire codecs, acyclic digests and externally pinned signatures.

Schema rules below are factored copies of the approved designs. No API accepts
code, SQL, environment settings, filesystem dispatch or self-declared trust.
Operational origin/replay/locking qualification belongs to the bounded host
caller; these functions authenticate exact typed bytes without doing I/O.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .ex10_open_evidence import (
    SAFE_INTEGER_MAX,
    OpenEvidenceError,
    canonical_bytes,
    parse_canonical_bytes,
    utc_instant,
)
from .manifest import make_run_id

_JSON_VALUE_TYPES = MappingProxyType(
    {
        "object": dict,
        "array": list,
        "string": str,
        "integer": int,
        "boolean": bool,
        "null": type(None),
    }
)

# Frozen B4 schema rules generated from the three approved JSON designs.
# Shared schema nodes remove duplication only; expanded rules are identical.
# Public schema APIs return detached copies. No external path is read at import.
CONTRACT_SOURCE_SHA256 = {
    "reservation-schema.json": "bc431fe7b242f1a21f2f635123a33776d080b185ed24079de13a1ae313ff826b",
    "bridge-request-schema.json": (
        "628bbaed89c4ed164719dbf700b20db5" "980716973a3df7e204f1489a0e038d6f"
    ),
    "bridge-response-schema.json": (
        "c1939709c2a82ae1e1508a2078c4e019" "7c9f904966a211b48fb4bf82d79d9cb1"
    ),
}
_SCHEMA_000 = {
    "maxLength": 36,
    "minLength": 1,
    "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    "type": "string",
}
_SCHEMA_001 = {"type": "null"}
_SCHEMA_002 = {"anyOf": [_SCHEMA_000, _SCHEMA_001]}
_SCHEMA_003 = {"maximum": 9007199254740991, "minimum": 1, "type": "integer"}
_SCHEMA_004 = {"maxLength": 64, "minLength": 1, "pattern": "^[0-9a-f]{64}$", "type": "string"}
_SCHEMA_005 = {
    "maxLength": 27,
    "minLength": 1,
    "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\\.[0-9]{6}Z$",
    "type": "string",
}
_SCHEMA_006 = {"maximum": 9007199254740991, "minimum": 0, "type": "integer"}
_SCHEMA_007 = {
    "maxLength": 128,
    "minLength": 1,
    "pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$",
    "type": "string",
}
_SCHEMA_008 = {"const": "S11C3-EX10"}
_SCHEMA_009 = {"anyOf": [_SCHEMA_004, _SCHEMA_001]}
_SCHEMA_010 = {"const": "s11-ex10-b4-coordination-head-v1"}
_SCHEMA_011 = {"maxLength": 128, "minLength": 1, "pattern": "^[0-9a-f]{128}$", "type": "string"}
_SCHEMA_012 = {
    "additionalProperties": False,
    "properties": {
        "active_reservation_id": _SCHEMA_002,
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "challenge": _SCHEMA_004,
        "expires_at": _SCHEMA_005,
        "fencing_token": _SCHEMA_006,
        "issued_at": _SCHEMA_005,
        "key_epoch": _SCHEMA_003,
        "key_id": _SCHEMA_007,
        "namespace": _SCHEMA_008,
        "receipt_digest": _SCHEMA_009,
        "schema_version": _SCHEMA_010,
        "sequence": _SCHEMA_006,
        "signature": _SCHEMA_011,
    },
    "required": [
        "schema_version",
        "namespace",
        "authority_id",
        "authority_epoch",
        "sequence",
        "receipt_digest",
        "active_reservation_id",
        "fencing_token",
        "challenge",
        "issued_at",
        "expires_at",
        "key_id",
        "key_epoch",
        "signature",
    ],
    "type": "object",
}
_SCHEMA_013 = {"maxLength": 4096, "minLength": 1, "type": "string"}
_SCHEMA_014 = {
    "maxLength": 128,
    "minLength": 1,
    "pattern": "^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$",
    "type": "string",
}
_SCHEMA_015 = {
    "additionalProperties": False,
    "properties": {"path": _SCHEMA_013, "schema_version": _SCHEMA_014, "sha256": _SCHEMA_004},
    "required": ["path", "sha256", "schema_version"],
    "type": "object",
}
_SCHEMA_016 = {
    "maxLength": 63,
    "minLength": 1,
    "pattern": "^[a-z0-9][a-z0-9_-]{0,62}$",
    "type": "string",
}
_SCHEMA_017 = {"const": "s11-ex10-b4-host-challenge-v1"}
_SCHEMA_018 = {"maxLength": 40, "minLength": 1, "pattern": "^[0-9a-f]{40}$", "type": "string"}
_SCHEMA_019 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authorization_reference": _SCHEMA_015,
        "bridge_key_epoch": _SCHEMA_003,
        "bridge_key_id": _SCHEMA_007,
        "challenge": _SCHEMA_004,
        "container_identity_digest": _SCHEMA_004,
        "deployment_verification_sha256": _SCHEMA_004,
        "exchange_id": _SCHEMA_000,
        "expires_at": _SCHEMA_005,
        "issued_at": _SCHEMA_005,
        "project": _SCHEMA_016,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_017,
        "session_header_sha256": _SCHEMA_004,
        "session_id": _SCHEMA_007,
        "signature": _SCHEMA_011,
        "source_revision": _SCHEMA_018,
    },
    "required": [
        "schema_version",
        "exchange_id",
        "challenge",
        "session_id",
        "session_header_sha256",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "source_revision",
        "project",
        "deployment_verification_sha256",
        "container_identity_digest",
        "authorization_reference",
        "issued_at",
        "expires_at",
        "bridge_key_id",
        "bridge_key_epoch",
        "signature",
    ],
    "type": "object",
}
_SCHEMA_020 = {"const": "HOST_TO_EXECUTOR"}
_SCHEMA_021 = {"const": "HOST_CHALLENGE"}
_SCHEMA_022 = {"const": "s11-ex10-b4-frame-v1"}
_SCHEMA_023 = {"const": 1}
_SCHEMA_024 = {
    "additionalProperties": False,
    "properties": {
        "body": _SCHEMA_019,
        "direction": _SCHEMA_020,
        "exchange_id": _SCHEMA_000,
        "kind": _SCHEMA_021,
        "schema_version": _SCHEMA_022,
        "sequence": _SCHEMA_023,
    },
    "required": ["schema_version", "exchange_id", "sequence", "kind", "direction", "body"],
    "type": "object",
}
_SCHEMA_025 = {"const": "reserve-start"}
_SCHEMA_026 = {
    "maxLength": 40,
    "minLength": 1,
    "pattern": "^s11-[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    "type": "string",
}
_SCHEMA_027 = {
    "additionalProperties": False,
    "properties": {
        "authority_revision": _SCHEMA_006,
        "run_id": _SCHEMA_026,
        "terminal_evidence_reference": _SCHEMA_015,
    },
    "required": ["run_id", "authority_revision", "terminal_evidence_reference"],
    "type": "object",
}
_SCHEMA_028 = {"items": _SCHEMA_027, "maxItems": 10000, "type": "array"}
_SCHEMA_029 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "ledger_digest": _SCHEMA_004,
        "receipt_reference": _SCHEMA_015,
        "revision": _SCHEMA_006,
        "witness_digest": _SCHEMA_004,
    },
    "required": [
        "authority_id",
        "authority_epoch",
        "revision",
        "ledger_digest",
        "witness_digest",
        "receipt_reference",
    ],
    "type": "object",
}
_SCHEMA_030 = {"const": "s11-load-v1"}
_SCHEMA_031 = {"const": "EX-10"}
_SCHEMA_032 = {"const": 20260921}
_SCHEMA_033 = {
    "additionalProperties": False,
    "properties": {
        "dataset_id": _SCHEMA_030,
        "execution_nonce": _SCHEMA_004,
        "git_sha": _SCHEMA_018,
        "identity_digest": _SCHEMA_004,
        "run_id": _SCHEMA_026,
        "scenario_id": _SCHEMA_031,
        "seed": _SCHEMA_032,
        "started_at": _SCHEMA_005,
    },
    "required": [
        "started_at",
        "git_sha",
        "dataset_id",
        "scenario_id",
        "seed",
        "execution_nonce",
        "run_id",
        "identity_digest",
    ],
    "type": "object",
}
_SCHEMA_034 = {
    "additionalProperties": False,
    "properties": {
        "acknowledgments": _SCHEMA_028,
        "deployment_verification_reference": _SCHEMA_015,
        "descriptor_reference": _SCHEMA_015,
        "expected_head": _SCHEMA_029,
        "operational_envelope_reference": _SCHEMA_015,
        "preflight_reference": _SCHEMA_015,
        "profile_reference": _SCHEMA_015,
        "run_identity": _SCHEMA_033,
        "session_reference": _SCHEMA_015,
    },
    "required": [
        "session_reference",
        "profile_reference",
        "descriptor_reference",
        "deployment_verification_reference",
        "operational_envelope_reference",
        "preflight_reference",
        "run_identity",
        "acknowledgments",
        "expected_head",
    ],
    "type": "object",
}
_SCHEMA_035 = {"const": "s11-ex10-b4-bridge-request-v1"}
_SCHEMA_036 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision": _SCHEMA_006,
        "authorization_reference": _SCHEMA_015,
        "exchange_id": _SCHEMA_000,
        "expires_at": _SCHEMA_005,
        "issued_at": _SCHEMA_005,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_025,
        "payload": _SCHEMA_034,
        "project": _SCHEMA_016,
        "request_id": _SCHEMA_000,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_035,
        "session_header_sha256": _SCHEMA_004,
        "session_id": _SCHEMA_007,
        "source_revision": _SCHEMA_018,
    },
    "required": [
        "schema_version",
        "request_id",
        "nonce",
        "session_id",
        "session_header_sha256",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "authority_revision",
        "source_revision",
        "project",
        "issued_at",
        "expires_at",
        "authorization_reference",
        "exchange_id",
        "operation",
        "payload",
    ],
    "type": "object",
}
_SCHEMA_037 = {"const": "s11-ex10-b4-proposal-v1"}
_SCHEMA_038 = {
    "additionalProperties": False,
    "properties": {
        "exchange_id": _SCHEMA_000,
        "holder_public_key_hex": _SCHEMA_004,
        "host_challenge_digest": _SCHEMA_004,
        "reserve_request_body": _SCHEMA_036,
        "schema_version": _SCHEMA_037,
        "signature": _SCHEMA_011,
    },
    "required": [
        "schema_version",
        "exchange_id",
        "host_challenge_digest",
        "holder_public_key_hex",
        "reserve_request_body",
        "signature",
    ],
    "type": "object",
}
_SCHEMA_039 = {"const": "EXECUTOR_TO_HOST"}
_SCHEMA_040 = {"const": "PROPOSE_OPEN"}
_SCHEMA_041 = {"const": 2}
_SCHEMA_042 = {
    "additionalProperties": False,
    "properties": {
        "body": _SCHEMA_038,
        "direction": _SCHEMA_039,
        "exchange_id": _SCHEMA_000,
        "kind": _SCHEMA_040,
        "schema_version": _SCHEMA_022,
        "sequence": _SCHEMA_041,
    },
    "required": ["schema_version", "exchange_id", "sequence", "kind", "direction", "body"],
    "type": "object",
}
_SCHEMA_043 = {"anyOf": [_SCHEMA_026, _SCHEMA_001]}
_SCHEMA_044 = {"anyOf": [_SCHEMA_003, _SCHEMA_001]}
_SCHEMA_045 = {
    "enum": [
        "reserve-start",
        "commit-open",
        "record-uncertain",
        "inspect-history",
        "verify-history-authority",
        "inspect-reservation",
        "inspect-coordination",
    ],
    "type": "string",
}
_SCHEMA_046 = {"const": "SESSION_EXECUTOR"}
_SCHEMA_047 = {"const": "s11-ex10-b4-capability-v1"}
_SCHEMA_048 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authorization_reference": _SCHEMA_015,
        "bridge_key_epoch": _SCHEMA_003,
        "bridge_key_id": _SCHEMA_007,
        "capability_id": _SCHEMA_000,
        "container_identity_digest": _SCHEMA_004,
        "deployment_verification_sha256": _SCHEMA_004,
        "exchange_id": _SCHEMA_000,
        "expected_revision": _SCHEMA_006,
        "expected_run_id": _SCHEMA_043,
        "expires_at": _SCHEMA_005,
        "fencing_token": _SCHEMA_044,
        "holder_public_key_hex": _SCHEMA_004,
        "issued_at": _SCHEMA_005,
        "namespace": _SCHEMA_008,
        "operation": _SCHEMA_045,
        "request_body_digest": _SCHEMA_004,
        "request_id": _SCHEMA_000,
        "request_nonce": _SCHEMA_004,
        "reservation_digest": _SCHEMA_009,
        "reservation_id": _SCHEMA_002,
        "role": _SCHEMA_046,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_047,
        "session_header_sha256": _SCHEMA_004,
        "session_id": _SCHEMA_007,
        "signature": _SCHEMA_011,
        "source_revision": _SCHEMA_018,
    },
    "required": [
        "schema_version",
        "capability_id",
        "session_id",
        "session_header_sha256",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "namespace",
        "source_revision",
        "deployment_verification_sha256",
        "container_identity_digest",
        "operation",
        "reservation_id",
        "reservation_digest",
        "fencing_token",
        "request_id",
        "request_nonce",
        "request_body_digest",
        "expected_revision",
        "expected_run_id",
        "holder_public_key_hex",
        "issued_at",
        "expires_at",
        "authorization_reference",
        "bridge_key_id",
        "bridge_key_epoch",
        "signature",
        "role",
        "exchange_id",
    ],
    "type": "object",
}
_SCHEMA_049 = {"const": "AUTHORIZE_REQUEST"}
_SCHEMA_050 = {"const": 3}
_SCHEMA_051 = {
    "additionalProperties": False,
    "properties": {
        "body": _SCHEMA_048,
        "direction": _SCHEMA_020,
        "exchange_id": _SCHEMA_000,
        "kind": _SCHEMA_049,
        "schema_version": _SCHEMA_022,
        "sequence": _SCHEMA_050,
    },
    "required": ["schema_version", "exchange_id", "sequence", "kind", "direction", "body"],
    "type": "object",
}
_SCHEMA_052 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision": _SCHEMA_006,
        "authorization_reference": _SCHEMA_015,
        "capability": _SCHEMA_048,
        "exchange_id": _SCHEMA_000,
        "expires_at": _SCHEMA_005,
        "issued_at": _SCHEMA_005,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_025,
        "payload": _SCHEMA_034,
        "project": _SCHEMA_016,
        "request_digest": _SCHEMA_004,
        "request_id": _SCHEMA_000,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_035,
        "sender_signature": _SCHEMA_011,
        "session_header_sha256": _SCHEMA_004,
        "session_id": _SCHEMA_007,
        "source_revision": _SCHEMA_018,
    },
    "required": [
        "schema_version",
        "request_id",
        "nonce",
        "session_id",
        "session_header_sha256",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "authority_revision",
        "source_revision",
        "project",
        "issued_at",
        "expires_at",
        "authorization_reference",
        "capability",
        "request_digest",
        "sender_signature",
        "exchange_id",
        "operation",
        "payload",
    ],
    "type": "object",
}
_SCHEMA_053 = {"oneOf": [_SCHEMA_052]}
_SCHEMA_054 = {"const": "RPC_REQUEST"}
_SCHEMA_055 = {"const": 4}
_SCHEMA_056 = {
    "additionalProperties": False,
    "properties": {
        "body": _SCHEMA_053,
        "direction": _SCHEMA_039,
        "exchange_id": _SCHEMA_000,
        "kind": _SCHEMA_054,
        "schema_version": _SCHEMA_022,
        "sequence": _SCHEMA_055,
    },
    "required": ["schema_version", "exchange_id", "sequence", "kind", "direction", "body"],
    "type": "object",
}
_SCHEMA_057 = {"anyOf": [_SCHEMA_012, _SCHEMA_001]}
_SCHEMA_058 = {"const": None}
_SCHEMA_059 = {"const": "OPEN_ONE"}
_SCHEMA_060 = {"const": "s11-ex10-b4-open-one-permit-v1"}
_SCHEMA_061 = {
    "additionalProperties": False,
    "properties": {
        "acknowledgment_digest": _SCHEMA_004,
        "attempt_nonce": _SCHEMA_004,
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authorization_reference": _SCHEMA_015,
        "base_authority_revision": _SCHEMA_006,
        "bridge_key_epoch": _SCHEMA_003,
        "bridge_key_id": _SCHEMA_007,
        "container_identity_digest": _SCHEMA_004,
        "deployment_verification_sha256": _SCHEMA_004,
        "exchange_id": _SCHEMA_000,
        "expected_open_revision": _SCHEMA_003,
        "expires_at": _SCHEMA_005,
        "fencing_token": _SCHEMA_003,
        "holder_public_key_hex": _SCHEMA_004,
        "issued_at": _SCHEMA_005,
        "kind": _SCHEMA_059,
        "namespace": _SCHEMA_008,
        "permit_digest": _SCHEMA_004,
        "permit_id": _SCHEMA_000,
        "permit_nonce": _SCHEMA_004,
        "preflight_reference": _SCHEMA_015,
        "project": _SCHEMA_016,
        "reservation_digest": _SCHEMA_004,
        "reservation_id": _SCHEMA_000,
        "reservation_receipt_reference": _SCHEMA_015,
        "reserve_request_digest": _SCHEMA_004,
        "reserve_request_id": _SCHEMA_000,
        "run_identity": _SCHEMA_033,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_060,
        "session_header_sha256": _SCHEMA_004,
        "session_id": _SCHEMA_007,
        "signature": _SCHEMA_011,
        "source_revision": _SCHEMA_018,
    },
    "required": [
        "schema_version",
        "kind",
        "permit_id",
        "exchange_id",
        "namespace",
        "reservation_id",
        "reservation_digest",
        "reservation_receipt_reference",
        "fencing_token",
        "reserve_request_id",
        "reserve_request_digest",
        "attempt_nonce",
        "permit_nonce",
        "authority_id",
        "authority_epoch",
        "base_authority_revision",
        "expected_open_revision",
        "session_id",
        "session_header_sha256",
        "runtime_profile_sha256",
        "source_revision",
        "project",
        "deployment_verification_sha256",
        "container_identity_digest",
        "run_identity",
        "acknowledgment_digest",
        "preflight_reference",
        "issued_at",
        "expires_at",
        "holder_public_key_hex",
        "authorization_reference",
        "permit_digest",
        "bridge_key_id",
        "bridge_key_epoch",
        "signature",
    ],
    "type": "object",
}
_SCHEMA_062 = {"anyOf": [_SCHEMA_015, _SCHEMA_001]}
_SCHEMA_063 = {"anyOf": [_SCHEMA_029, _SCHEMA_001]}
_SCHEMA_064 = {"const": "s11-ex10-b4-reservation-v1"}
_SCHEMA_065 = {
    "enum": ["PENDING", "COMMITTED", "UNCERTAIN", "CANCELLED_MANUALLY"],
    "type": "string",
}
_SCHEMA_066 = {
    "enum": [
        "OWNER_DIED",
        "WITNESS_RESULT_UNKNOWN",
        "EVIDENCE_RESULT_UNKNOWN",
        "LOCAL_COMMIT_UNKNOWN",
        "RESPONSE_LOST",
        "IDENTITY_REPLACED",
        "CLOCK_UNHEALTHY",
        "OBSERVATION_STALE",
    ],
    "type": "string",
}
_SCHEMA_067 = {"items": _SCHEMA_066, "maxItems": 8, "type": "array"}
_SCHEMA_068 = {
    "additionalProperties": False,
    "properties": {
        "acknowledgment_digest": _SCHEMA_004,
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "coordination_policy_reference": _SCHEMA_015,
        "deployment_verification_reference": _SCHEMA_015,
        "evidence_receipt_reference": _SCHEMA_062,
        "expected_next_revision": _SCHEMA_003,
        "fencing_token": _SCHEMA_003,
        "freshness_deadline": _SCHEMA_005,
        "host_root_binding_sha256": _SCHEMA_004,
        "manual_resolution_reference": _SCHEMA_062,
        "namespace": _SCHEMA_008,
        "nonce": _SCHEMA_004,
        "open_witness_reference": _SCHEMA_063,
        "operator_authorization_reference": _SCHEMA_015,
        "previous_authority_revision": _SCHEMA_006,
        "previous_ledger_digest": _SCHEMA_004,
        "previous_reservation_record_digest": _SCHEMA_009,
        "previous_witness_digest": _SCHEMA_004,
        "project": _SCHEMA_016,
        "record_digest": _SCHEMA_004,
        "record_sequence": _SCHEMA_003,
        "request_digest": _SCHEMA_004,
        "request_id": _SCHEMA_000,
        "requested_at": _SCHEMA_005,
        "reservation_digest": _SCHEMA_004,
        "reservation_id": _SCHEMA_000,
        "reservation_receipt_reference": _SCHEMA_062,
        "run_id": _SCHEMA_026,
        "run_identity": _SCHEMA_033,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_064,
        "session_header_sha256": _SCHEMA_004,
        "session_id": _SCHEMA_007,
        "source_revision": _SCHEMA_018,
        "state": _SCHEMA_065,
        "terminal_witness_reference": _SCHEMA_063,
        "uncertainty_codes": _SCHEMA_067,
        "updated_at": _SCHEMA_005,
    },
    "required": [
        "schema_version",
        "namespace",
        "reservation_id",
        "authority_id",
        "authority_epoch",
        "session_id",
        "session_header_sha256",
        "runtime_profile_sha256",
        "source_revision",
        "project",
        "deployment_verification_reference",
        "coordination_policy_reference",
        "host_root_binding_sha256",
        "requested_at",
        "freshness_deadline",
        "state",
        "nonce",
        "request_id",
        "request_digest",
        "acknowledgment_digest",
        "previous_authority_revision",
        "previous_ledger_digest",
        "previous_witness_digest",
        "expected_next_revision",
        "run_id",
        "run_identity",
        "fencing_token",
        "record_sequence",
        "previous_reservation_record_digest",
        "reservation_receipt_reference",
        "open_witness_reference",
        "evidence_receipt_reference",
        "terminal_witness_reference",
        "operator_authorization_reference",
        "manual_resolution_reference",
        "updated_at",
        "uncertainty_codes",
        "reservation_digest",
        "record_digest",
    ],
    "type": "object",
}
_SCHEMA_069 = {"const": "s11-ex10-b4-reservation-receipt-v1"}
_SCHEMA_070 = {
    "enum": [
        "RESERVE",
        "COMMIT_OPEN",
        "RECORD_UNCERTAIN",
        "CANCEL_MANUAL",
        "RETIRE_TERMINAL",
        "ADOPT_OPEN_MANUAL",
    ],
    "type": "string",
}
_SCHEMA_071 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "base_authority_revision": _SCHEMA_006,
        "base_authority_witness_digest": _SCHEMA_004,
        "fencing_token": _SCHEMA_003,
        "issued_at": _SCHEMA_005,
        "key_epoch": _SCHEMA_003,
        "key_id": _SCHEMA_007,
        "namespace": _SCHEMA_008,
        "open_witness_digest": _SCHEMA_009,
        "operation_id": _SCHEMA_000,
        "previous_receipt_digest": _SCHEMA_009,
        "reservation_digest": _SCHEMA_004,
        "reservation_id": _SCHEMA_000,
        "schema_version": _SCHEMA_069,
        "sequence": _SCHEMA_003,
        "signature": _SCHEMA_011,
        "state": _SCHEMA_065,
        "terminal_witness_digest": _SCHEMA_009,
        "transition": _SCHEMA_070,
    },
    "required": [
        "schema_version",
        "namespace",
        "authority_id",
        "authority_epoch",
        "reservation_id",
        "reservation_digest",
        "state",
        "fencing_token",
        "sequence",
        "previous_receipt_digest",
        "base_authority_revision",
        "base_authority_witness_digest",
        "open_witness_digest",
        "issued_at",
        "key_id",
        "key_epoch",
        "signature",
        "transition",
        "terminal_witness_digest",
        "operation_id",
    ],
    "type": "object",
}
_SCHEMA_072 = {
    "additionalProperties": False,
    "properties": {
        "open_permit": _SCHEMA_061,
        "reservation": _SCHEMA_068,
        "reservation_receipt": _SCHEMA_071,
    },
    "required": ["reservation", "reservation_receipt", "open_permit"],
    "type": "object",
}
_SCHEMA_073 = {"const": "s11-ex10-b4-bridge-response-v1"}
_SCHEMA_074 = {"const": "OK"}
_SCHEMA_075 = {"const": False}
_SCHEMA_076 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision_after": _SCHEMA_006,
        "authority_revision_before": _SCHEMA_006,
        "bridge_key_epoch": _SCHEMA_003,
        "bridge_key_id": _SCHEMA_007,
        "completed_at": _SCHEMA_005,
        "coordination_head": _SCHEMA_057,
        "error_class": _SCHEMA_058,
        "exchange_id": _SCHEMA_000,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_025,
        "request_digest": _SCHEMA_004,
        "request_id": _SCHEMA_000,
        "reservation_id": _SCHEMA_002,
        "response_digest": _SCHEMA_004,
        "result": _SCHEMA_072,
        "result_digest": _SCHEMA_004,
        "run_id": _SCHEMA_043,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_073,
        "session_id": _SCHEMA_007,
        "signature": _SCHEMA_011,
        "status": _SCHEMA_074,
        "uncertain": _SCHEMA_075,
        "witness_head": _SCHEMA_063,
    },
    "required": [
        "schema_version",
        "request_id",
        "operation",
        "request_digest",
        "nonce",
        "session_id",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "status",
        "authority_revision_before",
        "authority_revision_after",
        "reservation_id",
        "run_id",
        "result_digest",
        "witness_head",
        "coordination_head",
        "completed_at",
        "error_class",
        "uncertain",
        "response_digest",
        "bridge_key_id",
        "bridge_key_epoch",
        "signature",
        "exchange_id",
        "result",
    ],
    "type": "object",
}
_SCHEMA_077 = {
    "enum": [
        "AUTHENTICATION_FAILED",
        "AUTHORIZATION_FAILED",
        "SCHEMA_INVALID",
        "UNKNOWN_OPERATION",
        "REFERENCE_MISMATCH",
        "SESSION_STALE",
        "PROFILE_STALE",
        "DEPLOYMENT_CHANGED",
        "ACK_MISSING",
        "ACK_WRONG",
        "ACK_UNKNOWN",
        "FIRST_VALID_CONSUMED",
        "ACTIVE_BLOCKER",
        "UNCERTAIN_BLOCKER",
        "HISTORY_INVALID",
        "LOCK_BUSY",
        "REVISION_STALE",
        "REPLAY_CONFLICT",
        "REQUEST_EXPIRED",
        "CAPABILITY_EXPIRED",
        "WITNESS_UNAVAILABLE",
        "WITNESS_RESULT_UNKNOWN",
        "EVIDENCE_RESULT_UNKNOWN",
        "RUN_ID_MISMATCH",
        "LOCAL_COMMIT_UNKNOWN",
        "IDENTITY_REPLACED",
        "RECOVERY_PRECONDITION_FAILED",
        "REAL_INFRA_NOT_QUALIFIED",
        "B3_API_GAP",
    ],
    "type": "string",
}
_SCHEMA_078 = {"const": "REJECTED"}
_SCHEMA_079 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision_after": _SCHEMA_006,
        "authority_revision_before": _SCHEMA_006,
        "bridge_key_epoch": _SCHEMA_003,
        "bridge_key_id": _SCHEMA_007,
        "completed_at": _SCHEMA_005,
        "coordination_head": _SCHEMA_057,
        "error_class": _SCHEMA_077,
        "exchange_id": _SCHEMA_000,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_025,
        "request_digest": _SCHEMA_004,
        "request_id": _SCHEMA_000,
        "reservation_id": _SCHEMA_002,
        "response_digest": _SCHEMA_004,
        "result": _SCHEMA_058,
        "result_digest": _SCHEMA_004,
        "run_id": _SCHEMA_043,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_073,
        "session_id": _SCHEMA_007,
        "signature": _SCHEMA_011,
        "status": _SCHEMA_078,
        "uncertain": _SCHEMA_075,
        "witness_head": _SCHEMA_063,
    },
    "required": [
        "schema_version",
        "request_id",
        "operation",
        "request_digest",
        "nonce",
        "session_id",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "status",
        "authority_revision_before",
        "authority_revision_after",
        "reservation_id",
        "run_id",
        "result_digest",
        "witness_head",
        "coordination_head",
        "completed_at",
        "error_class",
        "uncertain",
        "response_digest",
        "bridge_key_id",
        "bridge_key_epoch",
        "signature",
        "exchange_id",
        "result",
    ],
    "type": "object",
}
_SCHEMA_080 = {"const": "UNCERTAIN"}
_SCHEMA_081 = {"const": True}
_SCHEMA_082 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision_after": _SCHEMA_006,
        "authority_revision_before": _SCHEMA_006,
        "bridge_key_epoch": _SCHEMA_003,
        "bridge_key_id": _SCHEMA_007,
        "completed_at": _SCHEMA_005,
        "coordination_head": _SCHEMA_057,
        "error_class": _SCHEMA_077,
        "exchange_id": _SCHEMA_000,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_025,
        "request_digest": _SCHEMA_004,
        "request_id": _SCHEMA_000,
        "reservation_id": _SCHEMA_002,
        "response_digest": _SCHEMA_004,
        "result": _SCHEMA_058,
        "result_digest": _SCHEMA_004,
        "run_id": _SCHEMA_043,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_073,
        "session_id": _SCHEMA_007,
        "signature": _SCHEMA_011,
        "status": _SCHEMA_080,
        "uncertain": _SCHEMA_081,
        "witness_head": _SCHEMA_063,
    },
    "required": [
        "schema_version",
        "request_id",
        "operation",
        "request_digest",
        "nonce",
        "session_id",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "status",
        "authority_revision_before",
        "authority_revision_after",
        "reservation_id",
        "run_id",
        "result_digest",
        "witness_head",
        "coordination_head",
        "completed_at",
        "error_class",
        "uncertain",
        "response_digest",
        "bridge_key_id",
        "bridge_key_epoch",
        "signature",
        "exchange_id",
        "result",
    ],
    "type": "object",
}
_SCHEMA_083 = {"oneOf": [_SCHEMA_076, _SCHEMA_079, _SCHEMA_082]}
_SCHEMA_084 = {"const": "RPC_RESPONSE"}
_SCHEMA_085 = {"const": 5}
_SCHEMA_086 = {
    "additionalProperties": False,
    "properties": {
        "body": _SCHEMA_083,
        "direction": _SCHEMA_020,
        "exchange_id": _SCHEMA_000,
        "kind": _SCHEMA_084,
        "schema_version": _SCHEMA_022,
        "sequence": _SCHEMA_085,
    },
    "required": ["schema_version", "exchange_id", "sequence", "kind", "direction", "body"],
    "type": "object",
}
_SCHEMA_087 = {"const": "commit-open"}
_SCHEMA_088 = {"const": "s11-ex10-b4-open-evidence-v1"}
_SCHEMA_089 = {
    "additionalProperties": False,
    "properties": {
        "executor_signature": _SCHEMA_011,
        "fencing_token": _SCHEMA_003,
        "identity_digest": _SCHEMA_004,
        "manifest_initial_reference": _SCHEMA_015,
        "open_permit_digest": _SCHEMA_004,
        "opened_at": _SCHEMA_005,
        "plan_reference": _SCHEMA_015,
        "preflight_reference": _SCHEMA_015,
        "receipt_digest": _SCHEMA_004,
        "reservation_id": _SCHEMA_000,
        "reserve_request_id": _SCHEMA_000,
        "run_id": _SCHEMA_026,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_088,
        "session_header_sha256": _SCHEMA_004,
        "start_reference": _SCHEMA_015,
    },
    "required": [
        "schema_version",
        "reserve_request_id",
        "open_permit_digest",
        "reservation_id",
        "fencing_token",
        "run_id",
        "identity_digest",
        "runtime_profile_sha256",
        "session_header_sha256",
        "manifest_initial_reference",
        "start_reference",
        "preflight_reference",
        "plan_reference",
        "opened_at",
        "receipt_digest",
        "executor_signature",
    ],
    "type": "object",
}
_SCHEMA_090 = {
    "additionalProperties": False,
    "properties": {
        "expected_head": _SCHEMA_029,
        "fencing_token": _SCHEMA_003,
        "open_evidence": _SCHEMA_089,
        "open_permit_digest": _SCHEMA_004,
        "reservation_digest": _SCHEMA_004,
        "reservation_id": _SCHEMA_000,
        "reservation_receipt_reference": _SCHEMA_015,
        "reserve_request_id": _SCHEMA_000,
        "run_identity": _SCHEMA_033,
    },
    "required": [
        "reservation_id",
        "reserve_request_id",
        "open_permit_digest",
        "reservation_digest",
        "reservation_receipt_reference",
        "fencing_token",
        "run_identity",
        "open_evidence",
        "expected_head",
    ],
    "type": "object",
}
_SCHEMA_091 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision": _SCHEMA_006,
        "authorization_reference": _SCHEMA_015,
        "exchange_id": _SCHEMA_000,
        "expires_at": _SCHEMA_005,
        "issued_at": _SCHEMA_005,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_087,
        "payload": _SCHEMA_090,
        "project": _SCHEMA_016,
        "request_id": _SCHEMA_000,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_035,
        "session_header_sha256": _SCHEMA_004,
        "session_id": _SCHEMA_007,
        "source_revision": _SCHEMA_018,
    },
    "required": [
        "schema_version",
        "request_id",
        "nonce",
        "session_id",
        "session_header_sha256",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "authority_revision",
        "source_revision",
        "project",
        "issued_at",
        "expires_at",
        "authorization_reference",
        "exchange_id",
        "operation",
        "payload",
    ],
    "type": "object",
}
_SCHEMA_092 = {"const": "s11-ex10-b4-evidence-frame-v1"}
_SCHEMA_093 = {
    "additionalProperties": False,
    "properties": {
        "commit_request_body": _SCHEMA_091,
        "exchange_id": _SCHEMA_000,
        "open_permit_digest": _SCHEMA_004,
        "schema_version": _SCHEMA_092,
        "signature": _SCHEMA_011,
    },
    "required": [
        "schema_version",
        "exchange_id",
        "open_permit_digest",
        "commit_request_body",
        "signature",
    ],
    "type": "object",
}
_SCHEMA_094 = {"const": "OPEN_EVIDENCE"}
_SCHEMA_095 = {"const": 6}
_SCHEMA_096 = {
    "additionalProperties": False,
    "properties": {
        "body": _SCHEMA_093,
        "direction": _SCHEMA_039,
        "exchange_id": _SCHEMA_000,
        "kind": _SCHEMA_094,
        "schema_version": _SCHEMA_022,
        "sequence": _SCHEMA_095,
    },
    "required": ["schema_version", "exchange_id", "sequence", "kind", "direction", "body"],
    "type": "object",
}
_SCHEMA_097 = {"const": 7}
_SCHEMA_098 = {
    "additionalProperties": False,
    "properties": {
        "body": _SCHEMA_048,
        "direction": _SCHEMA_020,
        "exchange_id": _SCHEMA_000,
        "kind": _SCHEMA_049,
        "schema_version": _SCHEMA_022,
        "sequence": _SCHEMA_097,
    },
    "required": ["schema_version", "exchange_id", "sequence", "kind", "direction", "body"],
    "type": "object",
}
_SCHEMA_099 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision": _SCHEMA_006,
        "authorization_reference": _SCHEMA_015,
        "capability": _SCHEMA_048,
        "exchange_id": _SCHEMA_000,
        "expires_at": _SCHEMA_005,
        "issued_at": _SCHEMA_005,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_087,
        "payload": _SCHEMA_090,
        "project": _SCHEMA_016,
        "request_digest": _SCHEMA_004,
        "request_id": _SCHEMA_000,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_035,
        "sender_signature": _SCHEMA_011,
        "session_header_sha256": _SCHEMA_004,
        "session_id": _SCHEMA_007,
        "source_revision": _SCHEMA_018,
    },
    "required": [
        "schema_version",
        "request_id",
        "nonce",
        "session_id",
        "session_header_sha256",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "authority_revision",
        "source_revision",
        "project",
        "issued_at",
        "expires_at",
        "authorization_reference",
        "capability",
        "request_digest",
        "sender_signature",
        "exchange_id",
        "operation",
        "payload",
    ],
    "type": "object",
}
_SCHEMA_100 = {"oneOf": [_SCHEMA_099]}
_SCHEMA_101 = {"const": 8}
_SCHEMA_102 = {
    "additionalProperties": False,
    "properties": {
        "body": _SCHEMA_100,
        "direction": _SCHEMA_039,
        "exchange_id": _SCHEMA_000,
        "kind": _SCHEMA_054,
        "schema_version": _SCHEMA_022,
        "sequence": _SCHEMA_101,
    },
    "required": ["schema_version", "exchange_id", "sequence", "kind", "direction", "body"],
    "type": "object",
}
_SCHEMA_103 = {
    "additionalProperties": False,
    "properties": {
        "open_evidence": _SCHEMA_089,
        "open_witness": _SCHEMA_029,
        "opened": _SCHEMA_081,
        "reservation": _SCHEMA_068,
    },
    "required": ["reservation", "open_evidence", "open_witness", "opened"],
    "type": "object",
}
_SCHEMA_104 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision_after": _SCHEMA_006,
        "authority_revision_before": _SCHEMA_006,
        "bridge_key_epoch": _SCHEMA_003,
        "bridge_key_id": _SCHEMA_007,
        "completed_at": _SCHEMA_005,
        "coordination_head": _SCHEMA_057,
        "error_class": _SCHEMA_058,
        "exchange_id": _SCHEMA_000,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_087,
        "request_digest": _SCHEMA_004,
        "request_id": _SCHEMA_000,
        "reservation_id": _SCHEMA_002,
        "response_digest": _SCHEMA_004,
        "result": _SCHEMA_103,
        "result_digest": _SCHEMA_004,
        "run_id": _SCHEMA_043,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_073,
        "session_id": _SCHEMA_007,
        "signature": _SCHEMA_011,
        "status": _SCHEMA_074,
        "uncertain": _SCHEMA_075,
        "witness_head": _SCHEMA_063,
    },
    "required": [
        "schema_version",
        "request_id",
        "operation",
        "request_digest",
        "nonce",
        "session_id",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "status",
        "authority_revision_before",
        "authority_revision_after",
        "reservation_id",
        "run_id",
        "result_digest",
        "witness_head",
        "coordination_head",
        "completed_at",
        "error_class",
        "uncertain",
        "response_digest",
        "bridge_key_id",
        "bridge_key_epoch",
        "signature",
        "exchange_id",
        "result",
    ],
    "type": "object",
}
_SCHEMA_105 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision_after": _SCHEMA_006,
        "authority_revision_before": _SCHEMA_006,
        "bridge_key_epoch": _SCHEMA_003,
        "bridge_key_id": _SCHEMA_007,
        "completed_at": _SCHEMA_005,
        "coordination_head": _SCHEMA_057,
        "error_class": _SCHEMA_077,
        "exchange_id": _SCHEMA_000,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_087,
        "request_digest": _SCHEMA_004,
        "request_id": _SCHEMA_000,
        "reservation_id": _SCHEMA_002,
        "response_digest": _SCHEMA_004,
        "result": _SCHEMA_058,
        "result_digest": _SCHEMA_004,
        "run_id": _SCHEMA_043,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_073,
        "session_id": _SCHEMA_007,
        "signature": _SCHEMA_011,
        "status": _SCHEMA_078,
        "uncertain": _SCHEMA_075,
        "witness_head": _SCHEMA_063,
    },
    "required": [
        "schema_version",
        "request_id",
        "operation",
        "request_digest",
        "nonce",
        "session_id",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "status",
        "authority_revision_before",
        "authority_revision_after",
        "reservation_id",
        "run_id",
        "result_digest",
        "witness_head",
        "coordination_head",
        "completed_at",
        "error_class",
        "uncertain",
        "response_digest",
        "bridge_key_id",
        "bridge_key_epoch",
        "signature",
        "exchange_id",
        "result",
    ],
    "type": "object",
}
_SCHEMA_106 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision_after": _SCHEMA_006,
        "authority_revision_before": _SCHEMA_006,
        "bridge_key_epoch": _SCHEMA_003,
        "bridge_key_id": _SCHEMA_007,
        "completed_at": _SCHEMA_005,
        "coordination_head": _SCHEMA_057,
        "error_class": _SCHEMA_077,
        "exchange_id": _SCHEMA_000,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_087,
        "request_digest": _SCHEMA_004,
        "request_id": _SCHEMA_000,
        "reservation_id": _SCHEMA_002,
        "response_digest": _SCHEMA_004,
        "result": _SCHEMA_058,
        "result_digest": _SCHEMA_004,
        "run_id": _SCHEMA_043,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_073,
        "session_id": _SCHEMA_007,
        "signature": _SCHEMA_011,
        "status": _SCHEMA_080,
        "uncertain": _SCHEMA_081,
        "witness_head": _SCHEMA_063,
    },
    "required": [
        "schema_version",
        "request_id",
        "operation",
        "request_digest",
        "nonce",
        "session_id",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "status",
        "authority_revision_before",
        "authority_revision_after",
        "reservation_id",
        "run_id",
        "result_digest",
        "witness_head",
        "coordination_head",
        "completed_at",
        "error_class",
        "uncertain",
        "response_digest",
        "bridge_key_id",
        "bridge_key_epoch",
        "signature",
        "exchange_id",
        "result",
    ],
    "type": "object",
}
_SCHEMA_107 = {"oneOf": [_SCHEMA_104, _SCHEMA_105, _SCHEMA_106]}
_SCHEMA_108 = {"const": 9}
_SCHEMA_109 = {
    "additionalProperties": False,
    "properties": {
        "body": _SCHEMA_107,
        "direction": _SCHEMA_020,
        "exchange_id": _SCHEMA_000,
        "kind": _SCHEMA_084,
        "schema_version": _SCHEMA_022,
        "sequence": _SCHEMA_108,
    },
    "required": ["schema_version", "exchange_id", "sequence", "kind", "direction", "body"],
    "type": "object",
}
_SCHEMA_110 = {
    "oneOf": [
        _SCHEMA_024,
        _SCHEMA_042,
        _SCHEMA_051,
        _SCHEMA_056,
        _SCHEMA_086,
        _SCHEMA_096,
        _SCHEMA_098,
        _SCHEMA_102,
        _SCHEMA_109,
    ]
}
_SCHEMA_111 = {"const": "cancel-reservation-manual"}
_SCHEMA_112 = {
    "items": _SCHEMA_015,
    "maxItems": 64,
    "minItems": 1,
    "type": "array",
    "uniqueItems": True,
}
_SCHEMA_113 = {
    "enum": ["OPERATOR_CANCELLED_BEFORE_OPEN", "NO_COMMIT_PROVEN_AUTHORITY_CLOSED"],
    "type": "string",
}
_SCHEMA_114 = {
    "additionalProperties": False,
    "properties": {
        "evidence_references": _SCHEMA_112,
        "expected_record_digest": _SCHEMA_004,
        "expected_record_sequence": _SCHEMA_003,
        "expected_state": _SCHEMA_080,
        "fresh_authority_head": _SCHEMA_029,
        "fresh_coordination_head": _SCHEMA_012,
        "idempotency_token": _SCHEMA_004,
        "no_open_certificate_reference": _SCHEMA_015,
        "operator_authorization_reference": _SCHEMA_015,
        "reason_code": _SCHEMA_113,
        "reservation_id": _SCHEMA_000,
        "writer_closure_reference": _SCHEMA_015,
    },
    "required": [
        "reservation_id",
        "expected_state",
        "expected_record_digest",
        "expected_record_sequence",
        "evidence_references",
        "fresh_authority_head",
        "fresh_coordination_head",
        "operator_authorization_reference",
        "idempotency_token",
        "no_open_certificate_reference",
        "writer_closure_reference",
        "reason_code",
    ],
    "type": "object",
}
_SCHEMA_115 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision": _SCHEMA_006,
        "authorization_reference": _SCHEMA_015,
        "exchange_id": _SCHEMA_000,
        "expires_at": _SCHEMA_005,
        "issued_at": _SCHEMA_005,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_111,
        "payload": _SCHEMA_114,
        "project": _SCHEMA_016,
        "request_id": _SCHEMA_000,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_035,
        "session_header_sha256": _SCHEMA_004,
        "session_id": _SCHEMA_007,
        "source_revision": _SCHEMA_018,
    },
    "required": [
        "schema_version",
        "request_id",
        "nonce",
        "session_id",
        "session_header_sha256",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "authority_revision",
        "source_revision",
        "project",
        "issued_at",
        "expires_at",
        "authorization_reference",
        "exchange_id",
        "operation",
        "payload",
    ],
    "type": "object",
}
_SCHEMA_116 = {"const": "inspect-coordination"}
_SCHEMA_117 = {
    "additionalProperties": False,
    "properties": {"expected_authority_epoch": _SCHEMA_003, "expected_authority_id": _SCHEMA_000},
    "required": ["expected_authority_id", "expected_authority_epoch"],
    "type": "object",
}
_SCHEMA_118 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision": _SCHEMA_006,
        "authorization_reference": _SCHEMA_015,
        "exchange_id": _SCHEMA_000,
        "expires_at": _SCHEMA_005,
        "issued_at": _SCHEMA_005,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_116,
        "payload": _SCHEMA_117,
        "project": _SCHEMA_016,
        "request_id": _SCHEMA_000,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_035,
        "session_header_sha256": _SCHEMA_004,
        "session_id": _SCHEMA_007,
        "source_revision": _SCHEMA_018,
    },
    "required": [
        "schema_version",
        "request_id",
        "nonce",
        "session_id",
        "session_header_sha256",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "authority_revision",
        "source_revision",
        "project",
        "issued_at",
        "expires_at",
        "authorization_reference",
        "exchange_id",
        "operation",
        "payload",
    ],
    "type": "object",
}
_SCHEMA_119 = {"const": "inspect-history"}
_SCHEMA_120 = {"maximum": 1000, "minimum": 1, "type": "integer"}
_SCHEMA_121 = {
    "additionalProperties": False,
    "properties": {
        "cursor_after_run_id": _SCHEMA_043,
        "expected_head": _SCHEMA_029,
        "limit": _SCHEMA_120,
    },
    "required": ["expected_head", "cursor_after_run_id", "limit"],
    "type": "object",
}
_SCHEMA_122 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision": _SCHEMA_006,
        "authorization_reference": _SCHEMA_015,
        "exchange_id": _SCHEMA_000,
        "expires_at": _SCHEMA_005,
        "issued_at": _SCHEMA_005,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_119,
        "payload": _SCHEMA_121,
        "project": _SCHEMA_016,
        "request_id": _SCHEMA_000,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_035,
        "session_header_sha256": _SCHEMA_004,
        "session_id": _SCHEMA_007,
        "source_revision": _SCHEMA_018,
    },
    "required": [
        "schema_version",
        "request_id",
        "nonce",
        "session_id",
        "session_header_sha256",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "authority_revision",
        "source_revision",
        "project",
        "issued_at",
        "expires_at",
        "authorization_reference",
        "exchange_id",
        "operation",
        "payload",
    ],
    "type": "object",
}
_SCHEMA_123 = {"const": "inspect-reservation"}
_SCHEMA_124 = {
    "additionalProperties": False,
    "properties": {"original_request_id": _SCHEMA_000, "reservation_id": _SCHEMA_000},
    "required": ["reservation_id", "original_request_id"],
    "type": "object",
}
_SCHEMA_125 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision": _SCHEMA_006,
        "authorization_reference": _SCHEMA_015,
        "exchange_id": _SCHEMA_000,
        "expires_at": _SCHEMA_005,
        "issued_at": _SCHEMA_005,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_123,
        "payload": _SCHEMA_124,
        "project": _SCHEMA_016,
        "request_id": _SCHEMA_000,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_035,
        "session_header_sha256": _SCHEMA_004,
        "session_id": _SCHEMA_007,
        "source_revision": _SCHEMA_018,
    },
    "required": [
        "schema_version",
        "request_id",
        "nonce",
        "session_id",
        "session_header_sha256",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "authority_revision",
        "source_revision",
        "project",
        "issued_at",
        "expires_at",
        "authorization_reference",
        "exchange_id",
        "operation",
        "payload",
    ],
    "type": "object",
}
_SCHEMA_126 = {"const": "record-uncertain"}
_SCHEMA_127 = {
    "additionalProperties": False,
    "properties": {
        "fencing_token": _SCHEMA_003,
        "observation_reference": _SCHEMA_015,
        "reason_code": _SCHEMA_066,
        "reservation_id": _SCHEMA_000,
    },
    "required": ["reservation_id", "fencing_token", "reason_code", "observation_reference"],
    "type": "object",
}
_SCHEMA_128 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision": _SCHEMA_006,
        "authorization_reference": _SCHEMA_015,
        "exchange_id": _SCHEMA_000,
        "expires_at": _SCHEMA_005,
        "issued_at": _SCHEMA_005,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_126,
        "payload": _SCHEMA_127,
        "project": _SCHEMA_016,
        "request_id": _SCHEMA_000,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_035,
        "session_header_sha256": _SCHEMA_004,
        "session_id": _SCHEMA_007,
        "source_revision": _SCHEMA_018,
    },
    "required": [
        "schema_version",
        "request_id",
        "nonce",
        "session_id",
        "session_header_sha256",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "authority_revision",
        "source_revision",
        "project",
        "issued_at",
        "expires_at",
        "authorization_reference",
        "exchange_id",
        "operation",
        "payload",
    ],
    "type": "object",
}
_SCHEMA_129 = {"const": "resolve-uncertain-as-committed"}
_SCHEMA_130 = {
    "additionalProperties": False,
    "properties": {
        "evidence_references": _SCHEMA_112,
        "existing_open_witness_reference": _SCHEMA_029,
        "expected_record_digest": _SCHEMA_004,
        "expected_record_sequence": _SCHEMA_003,
        "expected_state": _SCHEMA_080,
        "fresh_authority_head": _SCHEMA_029,
        "fresh_coordination_head": _SCHEMA_012,
        "idempotency_token": _SCHEMA_004,
        "open_evidence": _SCHEMA_089,
        "operator_authorization_reference": _SCHEMA_015,
        "reservation_id": _SCHEMA_000,
        "run_identity": _SCHEMA_033,
    },
    "required": [
        "reservation_id",
        "expected_state",
        "expected_record_digest",
        "expected_record_sequence",
        "evidence_references",
        "fresh_authority_head",
        "fresh_coordination_head",
        "operator_authorization_reference",
        "idempotency_token",
        "run_identity",
        "open_evidence",
        "existing_open_witness_reference",
    ],
    "type": "object",
}
_SCHEMA_131 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision": _SCHEMA_006,
        "authorization_reference": _SCHEMA_015,
        "exchange_id": _SCHEMA_000,
        "expires_at": _SCHEMA_005,
        "issued_at": _SCHEMA_005,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_129,
        "payload": _SCHEMA_130,
        "project": _SCHEMA_016,
        "request_id": _SCHEMA_000,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_035,
        "session_header_sha256": _SCHEMA_004,
        "session_id": _SCHEMA_007,
        "source_revision": _SCHEMA_018,
    },
    "required": [
        "schema_version",
        "request_id",
        "nonce",
        "session_id",
        "session_header_sha256",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "authority_revision",
        "source_revision",
        "project",
        "issued_at",
        "expires_at",
        "authorization_reference",
        "exchange_id",
        "operation",
        "payload",
    ],
    "type": "object",
}
_SCHEMA_132 = {"const": "resolve-uncertain-as-not-committed"}
_SCHEMA_133 = {
    "additionalProperties": False,
    "properties": {
        "evidence_references": _SCHEMA_112,
        "expected_record_digest": _SCHEMA_004,
        "expected_record_sequence": _SCHEMA_003,
        "expected_state": _SCHEMA_080,
        "fresh_authority_head": _SCHEMA_029,
        "fresh_coordination_head": _SCHEMA_012,
        "idempotency_token": _SCHEMA_004,
        "no_open_certificate_reference": _SCHEMA_015,
        "operator_authorization_reference": _SCHEMA_015,
        "reservation_id": _SCHEMA_000,
        "writer_closure_reference": _SCHEMA_015,
    },
    "required": [
        "reservation_id",
        "expected_state",
        "expected_record_digest",
        "expected_record_sequence",
        "evidence_references",
        "fresh_authority_head",
        "fresh_coordination_head",
        "operator_authorization_reference",
        "idempotency_token",
        "no_open_certificate_reference",
        "writer_closure_reference",
    ],
    "type": "object",
}
_SCHEMA_134 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision": _SCHEMA_006,
        "authorization_reference": _SCHEMA_015,
        "exchange_id": _SCHEMA_000,
        "expires_at": _SCHEMA_005,
        "issued_at": _SCHEMA_005,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_132,
        "payload": _SCHEMA_133,
        "project": _SCHEMA_016,
        "request_id": _SCHEMA_000,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_035,
        "session_header_sha256": _SCHEMA_004,
        "session_id": _SCHEMA_007,
        "source_revision": _SCHEMA_018,
    },
    "required": [
        "schema_version",
        "request_id",
        "nonce",
        "session_id",
        "session_header_sha256",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "authority_revision",
        "source_revision",
        "project",
        "issued_at",
        "expires_at",
        "authorization_reference",
        "exchange_id",
        "operation",
        "payload",
    ],
    "type": "object",
}
_SCHEMA_135 = {"const": "verify-history-authority"}
_SCHEMA_136 = {
    "additionalProperties": False,
    "properties": {
        "deployment_verification_reference": _SCHEMA_015,
        "descriptor_reference": _SCHEMA_015,
        "expected_head": _SCHEMA_029,
    },
    "required": ["descriptor_reference", "deployment_verification_reference", "expected_head"],
    "type": "object",
}
_SCHEMA_137 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision": _SCHEMA_006,
        "authorization_reference": _SCHEMA_015,
        "exchange_id": _SCHEMA_000,
        "expires_at": _SCHEMA_005,
        "issued_at": _SCHEMA_005,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_135,
        "payload": _SCHEMA_136,
        "project": _SCHEMA_016,
        "request_id": _SCHEMA_000,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_035,
        "session_header_sha256": _SCHEMA_004,
        "session_id": _SCHEMA_007,
        "source_revision": _SCHEMA_018,
    },
    "required": [
        "schema_version",
        "request_id",
        "nonce",
        "session_id",
        "session_header_sha256",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "authority_revision",
        "source_revision",
        "project",
        "issued_at",
        "expires_at",
        "authorization_reference",
        "exchange_id",
        "operation",
        "payload",
    ],
    "type": "object",
}
_SCHEMA_138 = {
    "oneOf": [
        _SCHEMA_115,
        _SCHEMA_118,
        _SCHEMA_122,
        _SCHEMA_125,
        _SCHEMA_128,
        _SCHEMA_131,
        _SCHEMA_134,
        _SCHEMA_137,
    ]
}
_SCHEMA_139 = {"const": "s11-ex10-b4-rpc-proposal-v1"}
_SCHEMA_140 = {
    "additionalProperties": False,
    "properties": {
        "exchange_id": _SCHEMA_000,
        "holder_public_key_hex": _SCHEMA_004,
        "host_challenge_digest": _SCHEMA_004,
        "request_body": _SCHEMA_138,
        "schema_version": _SCHEMA_139,
        "signature": _SCHEMA_011,
    },
    "required": [
        "schema_version",
        "exchange_id",
        "host_challenge_digest",
        "holder_public_key_hex",
        "request_body",
        "signature",
    ],
    "type": "object",
}
_SCHEMA_141 = {"const": "RPC_PROPOSAL"}
_SCHEMA_142 = {
    "additionalProperties": False,
    "properties": {
        "body": _SCHEMA_140,
        "direction": _SCHEMA_039,
        "exchange_id": _SCHEMA_000,
        "kind": _SCHEMA_141,
        "schema_version": _SCHEMA_022,
        "sequence": _SCHEMA_041,
    },
    "required": ["schema_version", "exchange_id", "sequence", "kind", "direction", "body"],
    "type": "object",
}
_SCHEMA_143 = {
    "enum": [
        "resolve-uncertain-as-committed",
        "resolve-uncertain-as-not-committed",
        "cancel-reservation-manual",
    ],
    "type": "string",
}
_SCHEMA_144 = {"const": "RECOVERY_OPERATOR"}
_SCHEMA_145 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authorization_reference": _SCHEMA_015,
        "bridge_key_epoch": _SCHEMA_003,
        "bridge_key_id": _SCHEMA_007,
        "capability_id": _SCHEMA_000,
        "container_identity_digest": _SCHEMA_004,
        "deployment_verification_sha256": _SCHEMA_004,
        "exchange_id": _SCHEMA_000,
        "expected_revision": _SCHEMA_006,
        "expected_run_id": _SCHEMA_043,
        "expires_at": _SCHEMA_005,
        "fencing_token": _SCHEMA_044,
        "holder_public_key_hex": _SCHEMA_004,
        "issued_at": _SCHEMA_005,
        "namespace": _SCHEMA_008,
        "operation": _SCHEMA_143,
        "operator_authorization_reference": _SCHEMA_015,
        "request_body_digest": _SCHEMA_004,
        "request_id": _SCHEMA_000,
        "request_nonce": _SCHEMA_004,
        "reservation_digest": _SCHEMA_009,
        "reservation_id": _SCHEMA_000,
        "role": _SCHEMA_144,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_047,
        "session_header_sha256": _SCHEMA_004,
        "session_id": _SCHEMA_007,
        "signature": _SCHEMA_011,
        "source_revision": _SCHEMA_018,
    },
    "required": [
        "schema_version",
        "capability_id",
        "session_id",
        "session_header_sha256",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "namespace",
        "source_revision",
        "deployment_verification_sha256",
        "container_identity_digest",
        "operation",
        "reservation_id",
        "reservation_digest",
        "fencing_token",
        "request_id",
        "request_nonce",
        "request_body_digest",
        "expected_revision",
        "expected_run_id",
        "holder_public_key_hex",
        "issued_at",
        "expires_at",
        "authorization_reference",
        "bridge_key_id",
        "bridge_key_epoch",
        "signature",
        "role",
        "exchange_id",
        "operator_authorization_reference",
    ],
    "type": "object",
}
_SCHEMA_146 = {"oneOf": [_SCHEMA_048, _SCHEMA_145]}
_SCHEMA_147 = {
    "additionalProperties": False,
    "properties": {
        "body": _SCHEMA_146,
        "direction": _SCHEMA_020,
        "exchange_id": _SCHEMA_000,
        "kind": _SCHEMA_049,
        "schema_version": _SCHEMA_022,
        "sequence": _SCHEMA_050,
    },
    "required": ["schema_version", "exchange_id", "sequence", "kind", "direction", "body"],
    "type": "object",
}
_SCHEMA_148 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision": _SCHEMA_006,
        "authorization_reference": _SCHEMA_015,
        "capability": _SCHEMA_048,
        "exchange_id": _SCHEMA_000,
        "expires_at": _SCHEMA_005,
        "issued_at": _SCHEMA_005,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_119,
        "payload": _SCHEMA_121,
        "project": _SCHEMA_016,
        "request_digest": _SCHEMA_004,
        "request_id": _SCHEMA_000,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_035,
        "sender_signature": _SCHEMA_011,
        "session_header_sha256": _SCHEMA_004,
        "session_id": _SCHEMA_007,
        "source_revision": _SCHEMA_018,
    },
    "required": [
        "schema_version",
        "request_id",
        "nonce",
        "session_id",
        "session_header_sha256",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "authority_revision",
        "source_revision",
        "project",
        "issued_at",
        "expires_at",
        "authorization_reference",
        "capability",
        "request_digest",
        "sender_signature",
        "exchange_id",
        "operation",
        "payload",
    ],
    "type": "object",
}
_SCHEMA_149 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision": _SCHEMA_006,
        "authorization_reference": _SCHEMA_015,
        "capability": _SCHEMA_048,
        "exchange_id": _SCHEMA_000,
        "expires_at": _SCHEMA_005,
        "issued_at": _SCHEMA_005,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_135,
        "payload": _SCHEMA_136,
        "project": _SCHEMA_016,
        "request_digest": _SCHEMA_004,
        "request_id": _SCHEMA_000,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_035,
        "sender_signature": _SCHEMA_011,
        "session_header_sha256": _SCHEMA_004,
        "session_id": _SCHEMA_007,
        "source_revision": _SCHEMA_018,
    },
    "required": [
        "schema_version",
        "request_id",
        "nonce",
        "session_id",
        "session_header_sha256",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "authority_revision",
        "source_revision",
        "project",
        "issued_at",
        "expires_at",
        "authorization_reference",
        "capability",
        "request_digest",
        "sender_signature",
        "exchange_id",
        "operation",
        "payload",
    ],
    "type": "object",
}
_SCHEMA_150 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision": _SCHEMA_006,
        "authorization_reference": _SCHEMA_015,
        "capability": _SCHEMA_048,
        "exchange_id": _SCHEMA_000,
        "expires_at": _SCHEMA_005,
        "issued_at": _SCHEMA_005,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_123,
        "payload": _SCHEMA_124,
        "project": _SCHEMA_016,
        "request_digest": _SCHEMA_004,
        "request_id": _SCHEMA_000,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_035,
        "sender_signature": _SCHEMA_011,
        "session_header_sha256": _SCHEMA_004,
        "session_id": _SCHEMA_007,
        "source_revision": _SCHEMA_018,
    },
    "required": [
        "schema_version",
        "request_id",
        "nonce",
        "session_id",
        "session_header_sha256",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "authority_revision",
        "source_revision",
        "project",
        "issued_at",
        "expires_at",
        "authorization_reference",
        "capability",
        "request_digest",
        "sender_signature",
        "exchange_id",
        "operation",
        "payload",
    ],
    "type": "object",
}
_SCHEMA_151 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision": _SCHEMA_006,
        "authorization_reference": _SCHEMA_015,
        "capability": _SCHEMA_048,
        "exchange_id": _SCHEMA_000,
        "expires_at": _SCHEMA_005,
        "issued_at": _SCHEMA_005,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_126,
        "payload": _SCHEMA_127,
        "project": _SCHEMA_016,
        "request_digest": _SCHEMA_004,
        "request_id": _SCHEMA_000,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_035,
        "sender_signature": _SCHEMA_011,
        "session_header_sha256": _SCHEMA_004,
        "session_id": _SCHEMA_007,
        "source_revision": _SCHEMA_018,
    },
    "required": [
        "schema_version",
        "request_id",
        "nonce",
        "session_id",
        "session_header_sha256",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "authority_revision",
        "source_revision",
        "project",
        "issued_at",
        "expires_at",
        "authorization_reference",
        "capability",
        "request_digest",
        "sender_signature",
        "exchange_id",
        "operation",
        "payload",
    ],
    "type": "object",
}
_SCHEMA_152 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision": _SCHEMA_006,
        "authorization_reference": _SCHEMA_015,
        "capability": _SCHEMA_048,
        "exchange_id": _SCHEMA_000,
        "expires_at": _SCHEMA_005,
        "issued_at": _SCHEMA_005,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_116,
        "payload": _SCHEMA_117,
        "project": _SCHEMA_016,
        "request_digest": _SCHEMA_004,
        "request_id": _SCHEMA_000,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_035,
        "sender_signature": _SCHEMA_011,
        "session_header_sha256": _SCHEMA_004,
        "session_id": _SCHEMA_007,
        "source_revision": _SCHEMA_018,
    },
    "required": [
        "schema_version",
        "request_id",
        "nonce",
        "session_id",
        "session_header_sha256",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "authority_revision",
        "source_revision",
        "project",
        "issued_at",
        "expires_at",
        "authorization_reference",
        "capability",
        "request_digest",
        "sender_signature",
        "exchange_id",
        "operation",
        "payload",
    ],
    "type": "object",
}
_SCHEMA_153 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision": _SCHEMA_006,
        "authorization_reference": _SCHEMA_015,
        "capability": _SCHEMA_145,
        "exchange_id": _SCHEMA_000,
        "expires_at": _SCHEMA_005,
        "issued_at": _SCHEMA_005,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_129,
        "payload": _SCHEMA_130,
        "project": _SCHEMA_016,
        "request_digest": _SCHEMA_004,
        "request_id": _SCHEMA_000,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_035,
        "sender_signature": _SCHEMA_011,
        "session_header_sha256": _SCHEMA_004,
        "session_id": _SCHEMA_007,
        "source_revision": _SCHEMA_018,
    },
    "required": [
        "schema_version",
        "request_id",
        "nonce",
        "session_id",
        "session_header_sha256",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "authority_revision",
        "source_revision",
        "project",
        "issued_at",
        "expires_at",
        "authorization_reference",
        "capability",
        "request_digest",
        "sender_signature",
        "exchange_id",
        "operation",
        "payload",
    ],
    "type": "object",
}
_SCHEMA_154 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision": _SCHEMA_006,
        "authorization_reference": _SCHEMA_015,
        "capability": _SCHEMA_145,
        "exchange_id": _SCHEMA_000,
        "expires_at": _SCHEMA_005,
        "issued_at": _SCHEMA_005,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_132,
        "payload": _SCHEMA_133,
        "project": _SCHEMA_016,
        "request_digest": _SCHEMA_004,
        "request_id": _SCHEMA_000,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_035,
        "sender_signature": _SCHEMA_011,
        "session_header_sha256": _SCHEMA_004,
        "session_id": _SCHEMA_007,
        "source_revision": _SCHEMA_018,
    },
    "required": [
        "schema_version",
        "request_id",
        "nonce",
        "session_id",
        "session_header_sha256",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "authority_revision",
        "source_revision",
        "project",
        "issued_at",
        "expires_at",
        "authorization_reference",
        "capability",
        "request_digest",
        "sender_signature",
        "exchange_id",
        "operation",
        "payload",
    ],
    "type": "object",
}
_SCHEMA_155 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision": _SCHEMA_006,
        "authorization_reference": _SCHEMA_015,
        "capability": _SCHEMA_145,
        "exchange_id": _SCHEMA_000,
        "expires_at": _SCHEMA_005,
        "issued_at": _SCHEMA_005,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_111,
        "payload": _SCHEMA_114,
        "project": _SCHEMA_016,
        "request_digest": _SCHEMA_004,
        "request_id": _SCHEMA_000,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_035,
        "sender_signature": _SCHEMA_011,
        "session_header_sha256": _SCHEMA_004,
        "session_id": _SCHEMA_007,
        "source_revision": _SCHEMA_018,
    },
    "required": [
        "schema_version",
        "request_id",
        "nonce",
        "session_id",
        "session_header_sha256",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "authority_revision",
        "source_revision",
        "project",
        "issued_at",
        "expires_at",
        "authorization_reference",
        "capability",
        "request_digest",
        "sender_signature",
        "exchange_id",
        "operation",
        "payload",
    ],
    "type": "object",
}
_SCHEMA_156 = {
    "oneOf": [
        _SCHEMA_148,
        _SCHEMA_149,
        _SCHEMA_150,
        _SCHEMA_151,
        _SCHEMA_152,
        _SCHEMA_153,
        _SCHEMA_154,
        _SCHEMA_155,
    ]
}
_SCHEMA_157 = {
    "additionalProperties": False,
    "properties": {
        "body": _SCHEMA_156,
        "direction": _SCHEMA_039,
        "exchange_id": _SCHEMA_000,
        "kind": _SCHEMA_054,
        "schema_version": _SCHEMA_022,
        "sequence": _SCHEMA_055,
    },
    "required": ["schema_version", "exchange_id", "sequence", "kind", "direction", "body"],
    "type": "object",
}
_SCHEMA_158 = {"items": _SCHEMA_026, "maxItems": 10000, "type": "array"}
_SCHEMA_159 = {"type": "boolean"}
_SCHEMA_160 = {"anyOf": [_SCHEMA_159, _SCHEMA_001]}
_SCHEMA_161 = {
    "enum": [
        "NONE",
        "PASS_BINDING",
        "FAIL_BINDING",
        "ACTIVE_BLOCKER",
        "UNCERTAIN_BLOCKER",
        "HISTORY_INVALID",
    ],
    "type": "string",
}
_SCHEMA_162 = {
    "additionalProperties": False,
    "properties": {
        "blocking_run_ids": _SCHEMA_158,
        "complete": _SCHEMA_159,
        "consumed": _SCHEMA_160,
        "first_valid_status": _SCHEMA_161,
        "nd_ack_targets": _SCHEMA_158,
        "observation_expires_at": _SCHEMA_005,
        "observed_revision": _SCHEMA_006,
        "run_ids": _SCHEMA_158,
    },
    "required": [
        "first_valid_status",
        "consumed",
        "blocking_run_ids",
        "nd_ack_targets",
        "run_ids",
        "complete",
        "observed_revision",
        "observation_expires_at",
    ],
    "type": "object",
}
_SCHEMA_163 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision_after": _SCHEMA_006,
        "authority_revision_before": _SCHEMA_006,
        "bridge_key_epoch": _SCHEMA_003,
        "bridge_key_id": _SCHEMA_007,
        "completed_at": _SCHEMA_005,
        "coordination_head": _SCHEMA_057,
        "error_class": _SCHEMA_058,
        "exchange_id": _SCHEMA_000,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_119,
        "request_digest": _SCHEMA_004,
        "request_id": _SCHEMA_000,
        "reservation_id": _SCHEMA_002,
        "response_digest": _SCHEMA_004,
        "result": _SCHEMA_162,
        "result_digest": _SCHEMA_004,
        "run_id": _SCHEMA_043,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_073,
        "session_id": _SCHEMA_007,
        "signature": _SCHEMA_011,
        "status": _SCHEMA_074,
        "uncertain": _SCHEMA_075,
        "witness_head": _SCHEMA_063,
    },
    "required": [
        "schema_version",
        "request_id",
        "operation",
        "request_digest",
        "nonce",
        "session_id",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "status",
        "authority_revision_before",
        "authority_revision_after",
        "reservation_id",
        "run_id",
        "result_digest",
        "witness_head",
        "coordination_head",
        "completed_at",
        "error_class",
        "uncertain",
        "response_digest",
        "bridge_key_id",
        "bridge_key_epoch",
        "signature",
        "exchange_id",
        "result",
    ],
    "type": "object",
}
_SCHEMA_164 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision_after": _SCHEMA_006,
        "authority_revision_before": _SCHEMA_006,
        "bridge_key_epoch": _SCHEMA_003,
        "bridge_key_id": _SCHEMA_007,
        "completed_at": _SCHEMA_005,
        "coordination_head": _SCHEMA_057,
        "error_class": _SCHEMA_077,
        "exchange_id": _SCHEMA_000,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_119,
        "request_digest": _SCHEMA_004,
        "request_id": _SCHEMA_000,
        "reservation_id": _SCHEMA_002,
        "response_digest": _SCHEMA_004,
        "result": _SCHEMA_058,
        "result_digest": _SCHEMA_004,
        "run_id": _SCHEMA_043,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_073,
        "session_id": _SCHEMA_007,
        "signature": _SCHEMA_011,
        "status": _SCHEMA_078,
        "uncertain": _SCHEMA_075,
        "witness_head": _SCHEMA_063,
    },
    "required": [
        "schema_version",
        "request_id",
        "operation",
        "request_digest",
        "nonce",
        "session_id",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "status",
        "authority_revision_before",
        "authority_revision_after",
        "reservation_id",
        "run_id",
        "result_digest",
        "witness_head",
        "coordination_head",
        "completed_at",
        "error_class",
        "uncertain",
        "response_digest",
        "bridge_key_id",
        "bridge_key_epoch",
        "signature",
        "exchange_id",
        "result",
    ],
    "type": "object",
}
_SCHEMA_165 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision_after": _SCHEMA_006,
        "authority_revision_before": _SCHEMA_006,
        "bridge_key_epoch": _SCHEMA_003,
        "bridge_key_id": _SCHEMA_007,
        "completed_at": _SCHEMA_005,
        "coordination_head": _SCHEMA_057,
        "error_class": _SCHEMA_077,
        "exchange_id": _SCHEMA_000,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_119,
        "request_digest": _SCHEMA_004,
        "request_id": _SCHEMA_000,
        "reservation_id": _SCHEMA_002,
        "response_digest": _SCHEMA_004,
        "result": _SCHEMA_058,
        "result_digest": _SCHEMA_004,
        "run_id": _SCHEMA_043,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_073,
        "session_id": _SCHEMA_007,
        "signature": _SCHEMA_011,
        "status": _SCHEMA_080,
        "uncertain": _SCHEMA_081,
        "witness_head": _SCHEMA_063,
    },
    "required": [
        "schema_version",
        "request_id",
        "operation",
        "request_digest",
        "nonce",
        "session_id",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "status",
        "authority_revision_before",
        "authority_revision_after",
        "reservation_id",
        "run_id",
        "result_digest",
        "witness_head",
        "coordination_head",
        "completed_at",
        "error_class",
        "uncertain",
        "response_digest",
        "bridge_key_id",
        "bridge_key_epoch",
        "signature",
        "exchange_id",
        "result",
    ],
    "type": "object",
}
_SCHEMA_166 = {
    "additionalProperties": False,
    "properties": {
        "bootstrap_complete": _SCHEMA_159,
        "descriptor_sha256": _SCHEMA_004,
        "observation_expires_at": _SCHEMA_005,
        "observed_head": _SCHEMA_029,
        "verified": _SCHEMA_159,
    },
    "required": [
        "verified",
        "descriptor_sha256",
        "bootstrap_complete",
        "observed_head",
        "observation_expires_at",
    ],
    "type": "object",
}
_SCHEMA_167 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision_after": _SCHEMA_006,
        "authority_revision_before": _SCHEMA_006,
        "bridge_key_epoch": _SCHEMA_003,
        "bridge_key_id": _SCHEMA_007,
        "completed_at": _SCHEMA_005,
        "coordination_head": _SCHEMA_057,
        "error_class": _SCHEMA_058,
        "exchange_id": _SCHEMA_000,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_135,
        "request_digest": _SCHEMA_004,
        "request_id": _SCHEMA_000,
        "reservation_id": _SCHEMA_002,
        "response_digest": _SCHEMA_004,
        "result": _SCHEMA_166,
        "result_digest": _SCHEMA_004,
        "run_id": _SCHEMA_043,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_073,
        "session_id": _SCHEMA_007,
        "signature": _SCHEMA_011,
        "status": _SCHEMA_074,
        "uncertain": _SCHEMA_075,
        "witness_head": _SCHEMA_063,
    },
    "required": [
        "schema_version",
        "request_id",
        "operation",
        "request_digest",
        "nonce",
        "session_id",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "status",
        "authority_revision_before",
        "authority_revision_after",
        "reservation_id",
        "run_id",
        "result_digest",
        "witness_head",
        "coordination_head",
        "completed_at",
        "error_class",
        "uncertain",
        "response_digest",
        "bridge_key_id",
        "bridge_key_epoch",
        "signature",
        "exchange_id",
        "result",
    ],
    "type": "object",
}
_SCHEMA_168 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision_after": _SCHEMA_006,
        "authority_revision_before": _SCHEMA_006,
        "bridge_key_epoch": _SCHEMA_003,
        "bridge_key_id": _SCHEMA_007,
        "completed_at": _SCHEMA_005,
        "coordination_head": _SCHEMA_057,
        "error_class": _SCHEMA_077,
        "exchange_id": _SCHEMA_000,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_135,
        "request_digest": _SCHEMA_004,
        "request_id": _SCHEMA_000,
        "reservation_id": _SCHEMA_002,
        "response_digest": _SCHEMA_004,
        "result": _SCHEMA_058,
        "result_digest": _SCHEMA_004,
        "run_id": _SCHEMA_043,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_073,
        "session_id": _SCHEMA_007,
        "signature": _SCHEMA_011,
        "status": _SCHEMA_078,
        "uncertain": _SCHEMA_075,
        "witness_head": _SCHEMA_063,
    },
    "required": [
        "schema_version",
        "request_id",
        "operation",
        "request_digest",
        "nonce",
        "session_id",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "status",
        "authority_revision_before",
        "authority_revision_after",
        "reservation_id",
        "run_id",
        "result_digest",
        "witness_head",
        "coordination_head",
        "completed_at",
        "error_class",
        "uncertain",
        "response_digest",
        "bridge_key_id",
        "bridge_key_epoch",
        "signature",
        "exchange_id",
        "result",
    ],
    "type": "object",
}
_SCHEMA_169 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision_after": _SCHEMA_006,
        "authority_revision_before": _SCHEMA_006,
        "bridge_key_epoch": _SCHEMA_003,
        "bridge_key_id": _SCHEMA_007,
        "completed_at": _SCHEMA_005,
        "coordination_head": _SCHEMA_057,
        "error_class": _SCHEMA_077,
        "exchange_id": _SCHEMA_000,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_135,
        "request_digest": _SCHEMA_004,
        "request_id": _SCHEMA_000,
        "reservation_id": _SCHEMA_002,
        "response_digest": _SCHEMA_004,
        "result": _SCHEMA_058,
        "result_digest": _SCHEMA_004,
        "run_id": _SCHEMA_043,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_073,
        "session_id": _SCHEMA_007,
        "signature": _SCHEMA_011,
        "status": _SCHEMA_080,
        "uncertain": _SCHEMA_081,
        "witness_head": _SCHEMA_063,
    },
    "required": [
        "schema_version",
        "request_id",
        "operation",
        "request_digest",
        "nonce",
        "session_id",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "status",
        "authority_revision_before",
        "authority_revision_after",
        "reservation_id",
        "run_id",
        "result_digest",
        "witness_head",
        "coordination_head",
        "completed_at",
        "error_class",
        "uncertain",
        "response_digest",
        "bridge_key_id",
        "bridge_key_epoch",
        "signature",
        "exchange_id",
        "result",
    ],
    "type": "object",
}
_SCHEMA_170 = {
    "additionalProperties": False,
    "properties": {"original_response_reference": _SCHEMA_062, "reservation": _SCHEMA_068},
    "required": ["reservation", "original_response_reference"],
    "type": "object",
}
_SCHEMA_171 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision_after": _SCHEMA_006,
        "authority_revision_before": _SCHEMA_006,
        "bridge_key_epoch": _SCHEMA_003,
        "bridge_key_id": _SCHEMA_007,
        "completed_at": _SCHEMA_005,
        "coordination_head": _SCHEMA_057,
        "error_class": _SCHEMA_058,
        "exchange_id": _SCHEMA_000,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_123,
        "request_digest": _SCHEMA_004,
        "request_id": _SCHEMA_000,
        "reservation_id": _SCHEMA_002,
        "response_digest": _SCHEMA_004,
        "result": _SCHEMA_170,
        "result_digest": _SCHEMA_004,
        "run_id": _SCHEMA_043,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_073,
        "session_id": _SCHEMA_007,
        "signature": _SCHEMA_011,
        "status": _SCHEMA_074,
        "uncertain": _SCHEMA_075,
        "witness_head": _SCHEMA_063,
    },
    "required": [
        "schema_version",
        "request_id",
        "operation",
        "request_digest",
        "nonce",
        "session_id",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "status",
        "authority_revision_before",
        "authority_revision_after",
        "reservation_id",
        "run_id",
        "result_digest",
        "witness_head",
        "coordination_head",
        "completed_at",
        "error_class",
        "uncertain",
        "response_digest",
        "bridge_key_id",
        "bridge_key_epoch",
        "signature",
        "exchange_id",
        "result",
    ],
    "type": "object",
}
_SCHEMA_172 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision_after": _SCHEMA_006,
        "authority_revision_before": _SCHEMA_006,
        "bridge_key_epoch": _SCHEMA_003,
        "bridge_key_id": _SCHEMA_007,
        "completed_at": _SCHEMA_005,
        "coordination_head": _SCHEMA_057,
        "error_class": _SCHEMA_077,
        "exchange_id": _SCHEMA_000,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_123,
        "request_digest": _SCHEMA_004,
        "request_id": _SCHEMA_000,
        "reservation_id": _SCHEMA_002,
        "response_digest": _SCHEMA_004,
        "result": _SCHEMA_058,
        "result_digest": _SCHEMA_004,
        "run_id": _SCHEMA_043,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_073,
        "session_id": _SCHEMA_007,
        "signature": _SCHEMA_011,
        "status": _SCHEMA_078,
        "uncertain": _SCHEMA_075,
        "witness_head": _SCHEMA_063,
    },
    "required": [
        "schema_version",
        "request_id",
        "operation",
        "request_digest",
        "nonce",
        "session_id",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "status",
        "authority_revision_before",
        "authority_revision_after",
        "reservation_id",
        "run_id",
        "result_digest",
        "witness_head",
        "coordination_head",
        "completed_at",
        "error_class",
        "uncertain",
        "response_digest",
        "bridge_key_id",
        "bridge_key_epoch",
        "signature",
        "exchange_id",
        "result",
    ],
    "type": "object",
}
_SCHEMA_173 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision_after": _SCHEMA_006,
        "authority_revision_before": _SCHEMA_006,
        "bridge_key_epoch": _SCHEMA_003,
        "bridge_key_id": _SCHEMA_007,
        "completed_at": _SCHEMA_005,
        "coordination_head": _SCHEMA_057,
        "error_class": _SCHEMA_077,
        "exchange_id": _SCHEMA_000,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_123,
        "request_digest": _SCHEMA_004,
        "request_id": _SCHEMA_000,
        "reservation_id": _SCHEMA_002,
        "response_digest": _SCHEMA_004,
        "result": _SCHEMA_058,
        "result_digest": _SCHEMA_004,
        "run_id": _SCHEMA_043,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_073,
        "session_id": _SCHEMA_007,
        "signature": _SCHEMA_011,
        "status": _SCHEMA_080,
        "uncertain": _SCHEMA_081,
        "witness_head": _SCHEMA_063,
    },
    "required": [
        "schema_version",
        "request_id",
        "operation",
        "request_digest",
        "nonce",
        "session_id",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "status",
        "authority_revision_before",
        "authority_revision_after",
        "reservation_id",
        "run_id",
        "result_digest",
        "witness_head",
        "coordination_head",
        "completed_at",
        "error_class",
        "uncertain",
        "response_digest",
        "bridge_key_id",
        "bridge_key_epoch",
        "signature",
        "exchange_id",
        "result",
    ],
    "type": "object",
}
_SCHEMA_174 = {
    "additionalProperties": False,
    "properties": {"global_start_blocked": _SCHEMA_081, "reservation": _SCHEMA_068},
    "required": ["reservation", "global_start_blocked"],
    "type": "object",
}
_SCHEMA_175 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision_after": _SCHEMA_006,
        "authority_revision_before": _SCHEMA_006,
        "bridge_key_epoch": _SCHEMA_003,
        "bridge_key_id": _SCHEMA_007,
        "completed_at": _SCHEMA_005,
        "coordination_head": _SCHEMA_057,
        "error_class": _SCHEMA_058,
        "exchange_id": _SCHEMA_000,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_126,
        "request_digest": _SCHEMA_004,
        "request_id": _SCHEMA_000,
        "reservation_id": _SCHEMA_002,
        "response_digest": _SCHEMA_004,
        "result": _SCHEMA_174,
        "result_digest": _SCHEMA_004,
        "run_id": _SCHEMA_043,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_073,
        "session_id": _SCHEMA_007,
        "signature": _SCHEMA_011,
        "status": _SCHEMA_074,
        "uncertain": _SCHEMA_075,
        "witness_head": _SCHEMA_063,
    },
    "required": [
        "schema_version",
        "request_id",
        "operation",
        "request_digest",
        "nonce",
        "session_id",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "status",
        "authority_revision_before",
        "authority_revision_after",
        "reservation_id",
        "run_id",
        "result_digest",
        "witness_head",
        "coordination_head",
        "completed_at",
        "error_class",
        "uncertain",
        "response_digest",
        "bridge_key_id",
        "bridge_key_epoch",
        "signature",
        "exchange_id",
        "result",
    ],
    "type": "object",
}
_SCHEMA_176 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision_after": _SCHEMA_006,
        "authority_revision_before": _SCHEMA_006,
        "bridge_key_epoch": _SCHEMA_003,
        "bridge_key_id": _SCHEMA_007,
        "completed_at": _SCHEMA_005,
        "coordination_head": _SCHEMA_057,
        "error_class": _SCHEMA_077,
        "exchange_id": _SCHEMA_000,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_126,
        "request_digest": _SCHEMA_004,
        "request_id": _SCHEMA_000,
        "reservation_id": _SCHEMA_002,
        "response_digest": _SCHEMA_004,
        "result": _SCHEMA_058,
        "result_digest": _SCHEMA_004,
        "run_id": _SCHEMA_043,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_073,
        "session_id": _SCHEMA_007,
        "signature": _SCHEMA_011,
        "status": _SCHEMA_078,
        "uncertain": _SCHEMA_075,
        "witness_head": _SCHEMA_063,
    },
    "required": [
        "schema_version",
        "request_id",
        "operation",
        "request_digest",
        "nonce",
        "session_id",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "status",
        "authority_revision_before",
        "authority_revision_after",
        "reservation_id",
        "run_id",
        "result_digest",
        "witness_head",
        "coordination_head",
        "completed_at",
        "error_class",
        "uncertain",
        "response_digest",
        "bridge_key_id",
        "bridge_key_epoch",
        "signature",
        "exchange_id",
        "result",
    ],
    "type": "object",
}
_SCHEMA_177 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision_after": _SCHEMA_006,
        "authority_revision_before": _SCHEMA_006,
        "bridge_key_epoch": _SCHEMA_003,
        "bridge_key_id": _SCHEMA_007,
        "completed_at": _SCHEMA_005,
        "coordination_head": _SCHEMA_057,
        "error_class": _SCHEMA_077,
        "exchange_id": _SCHEMA_000,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_126,
        "request_digest": _SCHEMA_004,
        "request_id": _SCHEMA_000,
        "reservation_id": _SCHEMA_002,
        "response_digest": _SCHEMA_004,
        "result": _SCHEMA_058,
        "result_digest": _SCHEMA_004,
        "run_id": _SCHEMA_043,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_073,
        "session_id": _SCHEMA_007,
        "signature": _SCHEMA_011,
        "status": _SCHEMA_080,
        "uncertain": _SCHEMA_081,
        "witness_head": _SCHEMA_063,
    },
    "required": [
        "schema_version",
        "request_id",
        "operation",
        "request_digest",
        "nonce",
        "session_id",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "status",
        "authority_revision_before",
        "authority_revision_after",
        "reservation_id",
        "run_id",
        "result_digest",
        "witness_head",
        "coordination_head",
        "completed_at",
        "error_class",
        "uncertain",
        "response_digest",
        "bridge_key_id",
        "bridge_key_epoch",
        "signature",
        "exchange_id",
        "result",
    ],
    "type": "object",
}
_SCHEMA_178 = {"enum": ["BUSY", "NOT_PROBED", "HELD_BY_THIS_RECOVERY"], "type": "string"}
_SCHEMA_179 = {"anyOf": [_SCHEMA_068, _SCHEMA_001]}
_SCHEMA_180 = {
    "additionalProperties": False,
    "properties": {
        "authority_revision": _SCHEMA_006,
        "consistent": _SCHEMA_159,
        "coordination_sequence": _SCHEMA_006,
        "global_start_blocked": _SCHEMA_159,
        "lock_state": _SCHEMA_178,
        "reservation": _SCHEMA_179,
    },
    "required": [
        "global_start_blocked",
        "reservation",
        "lock_state",
        "coordination_sequence",
        "authority_revision",
        "consistent",
    ],
    "type": "object",
}
_SCHEMA_181 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision_after": _SCHEMA_006,
        "authority_revision_before": _SCHEMA_006,
        "bridge_key_epoch": _SCHEMA_003,
        "bridge_key_id": _SCHEMA_007,
        "completed_at": _SCHEMA_005,
        "coordination_head": _SCHEMA_057,
        "error_class": _SCHEMA_058,
        "exchange_id": _SCHEMA_000,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_116,
        "request_digest": _SCHEMA_004,
        "request_id": _SCHEMA_000,
        "reservation_id": _SCHEMA_002,
        "response_digest": _SCHEMA_004,
        "result": _SCHEMA_180,
        "result_digest": _SCHEMA_004,
        "run_id": _SCHEMA_043,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_073,
        "session_id": _SCHEMA_007,
        "signature": _SCHEMA_011,
        "status": _SCHEMA_074,
        "uncertain": _SCHEMA_075,
        "witness_head": _SCHEMA_063,
    },
    "required": [
        "schema_version",
        "request_id",
        "operation",
        "request_digest",
        "nonce",
        "session_id",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "status",
        "authority_revision_before",
        "authority_revision_after",
        "reservation_id",
        "run_id",
        "result_digest",
        "witness_head",
        "coordination_head",
        "completed_at",
        "error_class",
        "uncertain",
        "response_digest",
        "bridge_key_id",
        "bridge_key_epoch",
        "signature",
        "exchange_id",
        "result",
    ],
    "type": "object",
}
_SCHEMA_182 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision_after": _SCHEMA_006,
        "authority_revision_before": _SCHEMA_006,
        "bridge_key_epoch": _SCHEMA_003,
        "bridge_key_id": _SCHEMA_007,
        "completed_at": _SCHEMA_005,
        "coordination_head": _SCHEMA_057,
        "error_class": _SCHEMA_077,
        "exchange_id": _SCHEMA_000,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_116,
        "request_digest": _SCHEMA_004,
        "request_id": _SCHEMA_000,
        "reservation_id": _SCHEMA_002,
        "response_digest": _SCHEMA_004,
        "result": _SCHEMA_058,
        "result_digest": _SCHEMA_004,
        "run_id": _SCHEMA_043,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_073,
        "session_id": _SCHEMA_007,
        "signature": _SCHEMA_011,
        "status": _SCHEMA_078,
        "uncertain": _SCHEMA_075,
        "witness_head": _SCHEMA_063,
    },
    "required": [
        "schema_version",
        "request_id",
        "operation",
        "request_digest",
        "nonce",
        "session_id",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "status",
        "authority_revision_before",
        "authority_revision_after",
        "reservation_id",
        "run_id",
        "result_digest",
        "witness_head",
        "coordination_head",
        "completed_at",
        "error_class",
        "uncertain",
        "response_digest",
        "bridge_key_id",
        "bridge_key_epoch",
        "signature",
        "exchange_id",
        "result",
    ],
    "type": "object",
}
_SCHEMA_183 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision_after": _SCHEMA_006,
        "authority_revision_before": _SCHEMA_006,
        "bridge_key_epoch": _SCHEMA_003,
        "bridge_key_id": _SCHEMA_007,
        "completed_at": _SCHEMA_005,
        "coordination_head": _SCHEMA_057,
        "error_class": _SCHEMA_077,
        "exchange_id": _SCHEMA_000,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_116,
        "request_digest": _SCHEMA_004,
        "request_id": _SCHEMA_000,
        "reservation_id": _SCHEMA_002,
        "response_digest": _SCHEMA_004,
        "result": _SCHEMA_058,
        "result_digest": _SCHEMA_004,
        "run_id": _SCHEMA_043,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_073,
        "session_id": _SCHEMA_007,
        "signature": _SCHEMA_011,
        "status": _SCHEMA_080,
        "uncertain": _SCHEMA_081,
        "witness_head": _SCHEMA_063,
    },
    "required": [
        "schema_version",
        "request_id",
        "operation",
        "request_digest",
        "nonce",
        "session_id",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "status",
        "authority_revision_before",
        "authority_revision_after",
        "reservation_id",
        "run_id",
        "result_digest",
        "witness_head",
        "coordination_head",
        "completed_at",
        "error_class",
        "uncertain",
        "response_digest",
        "bridge_key_id",
        "bridge_key_epoch",
        "signature",
        "exchange_id",
        "result",
    ],
    "type": "object",
}
_SCHEMA_184 = {
    "additionalProperties": False,
    "properties": {
        "committed": _SCHEMA_081,
        "new_open_executed": _SCHEMA_075,
        "reservation": _SCHEMA_068,
        "run_id": _SCHEMA_026,
    },
    "required": ["reservation", "run_id", "committed", "new_open_executed"],
    "type": "object",
}
_SCHEMA_185 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision_after": _SCHEMA_006,
        "authority_revision_before": _SCHEMA_006,
        "bridge_key_epoch": _SCHEMA_003,
        "bridge_key_id": _SCHEMA_007,
        "completed_at": _SCHEMA_005,
        "coordination_head": _SCHEMA_057,
        "error_class": _SCHEMA_058,
        "exchange_id": _SCHEMA_000,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_129,
        "request_digest": _SCHEMA_004,
        "request_id": _SCHEMA_000,
        "reservation_id": _SCHEMA_002,
        "response_digest": _SCHEMA_004,
        "result": _SCHEMA_184,
        "result_digest": _SCHEMA_004,
        "run_id": _SCHEMA_043,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_073,
        "session_id": _SCHEMA_007,
        "signature": _SCHEMA_011,
        "status": _SCHEMA_074,
        "uncertain": _SCHEMA_075,
        "witness_head": _SCHEMA_063,
    },
    "required": [
        "schema_version",
        "request_id",
        "operation",
        "request_digest",
        "nonce",
        "session_id",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "status",
        "authority_revision_before",
        "authority_revision_after",
        "reservation_id",
        "run_id",
        "result_digest",
        "witness_head",
        "coordination_head",
        "completed_at",
        "error_class",
        "uncertain",
        "response_digest",
        "bridge_key_id",
        "bridge_key_epoch",
        "signature",
        "exchange_id",
        "result",
    ],
    "type": "object",
}
_SCHEMA_186 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision_after": _SCHEMA_006,
        "authority_revision_before": _SCHEMA_006,
        "bridge_key_epoch": _SCHEMA_003,
        "bridge_key_id": _SCHEMA_007,
        "completed_at": _SCHEMA_005,
        "coordination_head": _SCHEMA_057,
        "error_class": _SCHEMA_077,
        "exchange_id": _SCHEMA_000,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_129,
        "request_digest": _SCHEMA_004,
        "request_id": _SCHEMA_000,
        "reservation_id": _SCHEMA_002,
        "response_digest": _SCHEMA_004,
        "result": _SCHEMA_058,
        "result_digest": _SCHEMA_004,
        "run_id": _SCHEMA_043,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_073,
        "session_id": _SCHEMA_007,
        "signature": _SCHEMA_011,
        "status": _SCHEMA_078,
        "uncertain": _SCHEMA_075,
        "witness_head": _SCHEMA_063,
    },
    "required": [
        "schema_version",
        "request_id",
        "operation",
        "request_digest",
        "nonce",
        "session_id",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "status",
        "authority_revision_before",
        "authority_revision_after",
        "reservation_id",
        "run_id",
        "result_digest",
        "witness_head",
        "coordination_head",
        "completed_at",
        "error_class",
        "uncertain",
        "response_digest",
        "bridge_key_id",
        "bridge_key_epoch",
        "signature",
        "exchange_id",
        "result",
    ],
    "type": "object",
}
_SCHEMA_187 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision_after": _SCHEMA_006,
        "authority_revision_before": _SCHEMA_006,
        "bridge_key_epoch": _SCHEMA_003,
        "bridge_key_id": _SCHEMA_007,
        "completed_at": _SCHEMA_005,
        "coordination_head": _SCHEMA_057,
        "error_class": _SCHEMA_077,
        "exchange_id": _SCHEMA_000,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_129,
        "request_digest": _SCHEMA_004,
        "request_id": _SCHEMA_000,
        "reservation_id": _SCHEMA_002,
        "response_digest": _SCHEMA_004,
        "result": _SCHEMA_058,
        "result_digest": _SCHEMA_004,
        "run_id": _SCHEMA_043,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_073,
        "session_id": _SCHEMA_007,
        "signature": _SCHEMA_011,
        "status": _SCHEMA_080,
        "uncertain": _SCHEMA_081,
        "witness_head": _SCHEMA_063,
    },
    "required": [
        "schema_version",
        "request_id",
        "operation",
        "request_digest",
        "nonce",
        "session_id",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "status",
        "authority_revision_before",
        "authority_revision_after",
        "reservation_id",
        "run_id",
        "result_digest",
        "witness_head",
        "coordination_head",
        "completed_at",
        "error_class",
        "uncertain",
        "response_digest",
        "bridge_key_id",
        "bridge_key_epoch",
        "signature",
        "exchange_id",
        "result",
    ],
    "type": "object",
}
_SCHEMA_188 = {
    "additionalProperties": False,
    "properties": {
        "authority_write_closed": _SCHEMA_159,
        "no_open_proven": _SCHEMA_081,
        "reservation": _SCHEMA_068,
    },
    "required": ["reservation", "no_open_proven", "authority_write_closed"],
    "type": "object",
}
_SCHEMA_189 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision_after": _SCHEMA_006,
        "authority_revision_before": _SCHEMA_006,
        "bridge_key_epoch": _SCHEMA_003,
        "bridge_key_id": _SCHEMA_007,
        "completed_at": _SCHEMA_005,
        "coordination_head": _SCHEMA_057,
        "error_class": _SCHEMA_058,
        "exchange_id": _SCHEMA_000,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_132,
        "request_digest": _SCHEMA_004,
        "request_id": _SCHEMA_000,
        "reservation_id": _SCHEMA_002,
        "response_digest": _SCHEMA_004,
        "result": _SCHEMA_188,
        "result_digest": _SCHEMA_004,
        "run_id": _SCHEMA_043,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_073,
        "session_id": _SCHEMA_007,
        "signature": _SCHEMA_011,
        "status": _SCHEMA_074,
        "uncertain": _SCHEMA_075,
        "witness_head": _SCHEMA_063,
    },
    "required": [
        "schema_version",
        "request_id",
        "operation",
        "request_digest",
        "nonce",
        "session_id",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "status",
        "authority_revision_before",
        "authority_revision_after",
        "reservation_id",
        "run_id",
        "result_digest",
        "witness_head",
        "coordination_head",
        "completed_at",
        "error_class",
        "uncertain",
        "response_digest",
        "bridge_key_id",
        "bridge_key_epoch",
        "signature",
        "exchange_id",
        "result",
    ],
    "type": "object",
}
_SCHEMA_190 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision_after": _SCHEMA_006,
        "authority_revision_before": _SCHEMA_006,
        "bridge_key_epoch": _SCHEMA_003,
        "bridge_key_id": _SCHEMA_007,
        "completed_at": _SCHEMA_005,
        "coordination_head": _SCHEMA_057,
        "error_class": _SCHEMA_077,
        "exchange_id": _SCHEMA_000,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_132,
        "request_digest": _SCHEMA_004,
        "request_id": _SCHEMA_000,
        "reservation_id": _SCHEMA_002,
        "response_digest": _SCHEMA_004,
        "result": _SCHEMA_058,
        "result_digest": _SCHEMA_004,
        "run_id": _SCHEMA_043,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_073,
        "session_id": _SCHEMA_007,
        "signature": _SCHEMA_011,
        "status": _SCHEMA_078,
        "uncertain": _SCHEMA_075,
        "witness_head": _SCHEMA_063,
    },
    "required": [
        "schema_version",
        "request_id",
        "operation",
        "request_digest",
        "nonce",
        "session_id",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "status",
        "authority_revision_before",
        "authority_revision_after",
        "reservation_id",
        "run_id",
        "result_digest",
        "witness_head",
        "coordination_head",
        "completed_at",
        "error_class",
        "uncertain",
        "response_digest",
        "bridge_key_id",
        "bridge_key_epoch",
        "signature",
        "exchange_id",
        "result",
    ],
    "type": "object",
}
_SCHEMA_191 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision_after": _SCHEMA_006,
        "authority_revision_before": _SCHEMA_006,
        "bridge_key_epoch": _SCHEMA_003,
        "bridge_key_id": _SCHEMA_007,
        "completed_at": _SCHEMA_005,
        "coordination_head": _SCHEMA_057,
        "error_class": _SCHEMA_077,
        "exchange_id": _SCHEMA_000,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_132,
        "request_digest": _SCHEMA_004,
        "request_id": _SCHEMA_000,
        "reservation_id": _SCHEMA_002,
        "response_digest": _SCHEMA_004,
        "result": _SCHEMA_058,
        "result_digest": _SCHEMA_004,
        "run_id": _SCHEMA_043,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_073,
        "session_id": _SCHEMA_007,
        "signature": _SCHEMA_011,
        "status": _SCHEMA_080,
        "uncertain": _SCHEMA_081,
        "witness_head": _SCHEMA_063,
    },
    "required": [
        "schema_version",
        "request_id",
        "operation",
        "request_digest",
        "nonce",
        "session_id",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "status",
        "authority_revision_before",
        "authority_revision_after",
        "reservation_id",
        "run_id",
        "result_digest",
        "witness_head",
        "coordination_head",
        "completed_at",
        "error_class",
        "uncertain",
        "response_digest",
        "bridge_key_id",
        "bridge_key_epoch",
        "signature",
        "exchange_id",
        "result",
    ],
    "type": "object",
}
_SCHEMA_192 = {
    "additionalProperties": False,
    "properties": {
        "authority_write_closed": _SCHEMA_159,
        "new_start_permitted_by_this_response": _SCHEMA_075,
        "reservation": _SCHEMA_068,
    },
    "required": ["reservation", "authority_write_closed", "new_start_permitted_by_this_response"],
    "type": "object",
}
_SCHEMA_193 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision_after": _SCHEMA_006,
        "authority_revision_before": _SCHEMA_006,
        "bridge_key_epoch": _SCHEMA_003,
        "bridge_key_id": _SCHEMA_007,
        "completed_at": _SCHEMA_005,
        "coordination_head": _SCHEMA_057,
        "error_class": _SCHEMA_058,
        "exchange_id": _SCHEMA_000,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_111,
        "request_digest": _SCHEMA_004,
        "request_id": _SCHEMA_000,
        "reservation_id": _SCHEMA_002,
        "response_digest": _SCHEMA_004,
        "result": _SCHEMA_192,
        "result_digest": _SCHEMA_004,
        "run_id": _SCHEMA_043,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_073,
        "session_id": _SCHEMA_007,
        "signature": _SCHEMA_011,
        "status": _SCHEMA_074,
        "uncertain": _SCHEMA_075,
        "witness_head": _SCHEMA_063,
    },
    "required": [
        "schema_version",
        "request_id",
        "operation",
        "request_digest",
        "nonce",
        "session_id",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "status",
        "authority_revision_before",
        "authority_revision_after",
        "reservation_id",
        "run_id",
        "result_digest",
        "witness_head",
        "coordination_head",
        "completed_at",
        "error_class",
        "uncertain",
        "response_digest",
        "bridge_key_id",
        "bridge_key_epoch",
        "signature",
        "exchange_id",
        "result",
    ],
    "type": "object",
}
_SCHEMA_194 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision_after": _SCHEMA_006,
        "authority_revision_before": _SCHEMA_006,
        "bridge_key_epoch": _SCHEMA_003,
        "bridge_key_id": _SCHEMA_007,
        "completed_at": _SCHEMA_005,
        "coordination_head": _SCHEMA_057,
        "error_class": _SCHEMA_077,
        "exchange_id": _SCHEMA_000,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_111,
        "request_digest": _SCHEMA_004,
        "request_id": _SCHEMA_000,
        "reservation_id": _SCHEMA_002,
        "response_digest": _SCHEMA_004,
        "result": _SCHEMA_058,
        "result_digest": _SCHEMA_004,
        "run_id": _SCHEMA_043,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_073,
        "session_id": _SCHEMA_007,
        "signature": _SCHEMA_011,
        "status": _SCHEMA_078,
        "uncertain": _SCHEMA_075,
        "witness_head": _SCHEMA_063,
    },
    "required": [
        "schema_version",
        "request_id",
        "operation",
        "request_digest",
        "nonce",
        "session_id",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "status",
        "authority_revision_before",
        "authority_revision_after",
        "reservation_id",
        "run_id",
        "result_digest",
        "witness_head",
        "coordination_head",
        "completed_at",
        "error_class",
        "uncertain",
        "response_digest",
        "bridge_key_id",
        "bridge_key_epoch",
        "signature",
        "exchange_id",
        "result",
    ],
    "type": "object",
}
_SCHEMA_195 = {
    "additionalProperties": False,
    "properties": {
        "authority_epoch": _SCHEMA_003,
        "authority_id": _SCHEMA_000,
        "authority_revision_after": _SCHEMA_006,
        "authority_revision_before": _SCHEMA_006,
        "bridge_key_epoch": _SCHEMA_003,
        "bridge_key_id": _SCHEMA_007,
        "completed_at": _SCHEMA_005,
        "coordination_head": _SCHEMA_057,
        "error_class": _SCHEMA_077,
        "exchange_id": _SCHEMA_000,
        "nonce": _SCHEMA_004,
        "operation": _SCHEMA_111,
        "request_digest": _SCHEMA_004,
        "request_id": _SCHEMA_000,
        "reservation_id": _SCHEMA_002,
        "response_digest": _SCHEMA_004,
        "result": _SCHEMA_058,
        "result_digest": _SCHEMA_004,
        "run_id": _SCHEMA_043,
        "runtime_profile_sha256": _SCHEMA_004,
        "schema_version": _SCHEMA_073,
        "session_id": _SCHEMA_007,
        "signature": _SCHEMA_011,
        "status": _SCHEMA_080,
        "uncertain": _SCHEMA_081,
        "witness_head": _SCHEMA_063,
    },
    "required": [
        "schema_version",
        "request_id",
        "operation",
        "request_digest",
        "nonce",
        "session_id",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "status",
        "authority_revision_before",
        "authority_revision_after",
        "reservation_id",
        "run_id",
        "result_digest",
        "witness_head",
        "coordination_head",
        "completed_at",
        "error_class",
        "uncertain",
        "response_digest",
        "bridge_key_id",
        "bridge_key_epoch",
        "signature",
        "exchange_id",
        "result",
    ],
    "type": "object",
}
_SCHEMA_196 = {
    "oneOf": [
        _SCHEMA_163,
        _SCHEMA_164,
        _SCHEMA_165,
        _SCHEMA_167,
        _SCHEMA_168,
        _SCHEMA_169,
        _SCHEMA_171,
        _SCHEMA_172,
        _SCHEMA_173,
        _SCHEMA_175,
        _SCHEMA_176,
        _SCHEMA_177,
        _SCHEMA_181,
        _SCHEMA_182,
        _SCHEMA_183,
        _SCHEMA_185,
        _SCHEMA_186,
        _SCHEMA_187,
        _SCHEMA_189,
        _SCHEMA_190,
        _SCHEMA_191,
        _SCHEMA_193,
        _SCHEMA_194,
        _SCHEMA_195,
    ]
}
_SCHEMA_197 = {
    "additionalProperties": False,
    "properties": {
        "body": _SCHEMA_196,
        "direction": _SCHEMA_020,
        "exchange_id": _SCHEMA_000,
        "kind": _SCHEMA_084,
        "schema_version": _SCHEMA_022,
        "sequence": _SCHEMA_085,
    },
    "required": ["schema_version", "exchange_id", "sequence", "kind", "direction", "body"],
    "type": "object",
}
_SCHEMA_198 = {"oneOf": [_SCHEMA_024, _SCHEMA_142, _SCHEMA_147, _SCHEMA_157, _SCHEMA_197]}
_DEFINITIONS = {
    "coordination_head": _SCHEMA_012,
    "handshake_frame": _SCHEMA_110,
    "host_challenge": _SCHEMA_019,
    "open_evidence": _SCHEMA_089,
    "open_evidence_frame": _SCHEMA_093,
    "open_one_permit": _SCHEMA_061,
    "open_proposal": _SCHEMA_038,
    "reservation": _SCHEMA_068,
    "reservation_receipt": _SCHEMA_071,
    "single_rpc_frame": _SCHEMA_198,
    "single_rpc_proposal": _SCHEMA_140,
}
_REQUEST_SCHEMAS = [
    _SCHEMA_148,
    _SCHEMA_149,
    _SCHEMA_052,
    _SCHEMA_150,
    _SCHEMA_099,
    _SCHEMA_151,
    _SCHEMA_152,
    _SCHEMA_153,
    _SCHEMA_154,
    _SCHEMA_155,
]
_RESPONSE_SCHEMAS = [
    _SCHEMA_163,
    _SCHEMA_164,
    _SCHEMA_165,
    _SCHEMA_167,
    _SCHEMA_168,
    _SCHEMA_169,
    _SCHEMA_076,
    _SCHEMA_079,
    _SCHEMA_082,
    _SCHEMA_171,
    _SCHEMA_172,
    _SCHEMA_173,
    _SCHEMA_104,
    _SCHEMA_105,
    _SCHEMA_106,
    _SCHEMA_175,
    _SCHEMA_176,
    _SCHEMA_177,
    _SCHEMA_181,
    _SCHEMA_182,
    _SCHEMA_183,
    _SCHEMA_185,
    _SCHEMA_186,
    _SCHEMA_187,
    _SCHEMA_189,
    _SCHEMA_190,
    _SCHEMA_191,
    _SCHEMA_193,
    _SCHEMA_194,
    _SCHEMA_195,
]

MAX_REQUEST_BYTES = 262144
MAX_RESPONSE_BYTES = 1048576
NAMESPACE = "S11C3-EX10"


class Operation(StrEnum):
    INSPECT_HISTORY = "inspect-history"
    VERIFY_HISTORY = "verify-history-authority"
    RESERVE_START = "reserve-start"
    INSPECT_RESERVATION = "inspect-reservation"
    COMMIT_OPEN = "commit-open"
    RECORD_UNCERTAIN = "record-uncertain"
    INSPECT_COORDINATION = "inspect-coordination"
    RESOLVE_COMMITTED = "resolve-uncertain-as-committed"
    RESOLVE_NOT_COMMITTED = "resolve-uncertain-as-not-committed"
    CANCEL_MANUAL = "cancel-reservation-manual"


RECOVERY_OPERATIONS = frozenset(
    {Operation.RESOLVE_COMMITTED, Operation.RESOLVE_NOT_COMMITTED, Operation.CANCEL_MANUAL}
)
DOMAINS = {
    "capability": b"S11-EX10-B4-CAPABILITY-v1\n",
    "request": b"S11-EX10-B4-REQUEST-v1\n",
    "response": b"S11-EX10-B4-RESPONSE-v1\n",
    "open_one_permit": b"S11-EX10-B4-OPEN-ONE-v1\n",
    "reservation_receipt": b"S11-EX10-B4-RESERVATION-v1\n",
    "coordination_head": b"S11-EX10-B4-COORDINATION-HEAD-v1\n",
    "host_challenge": b"S11-EX10-B4-HOST-CHALLENGE-v1\n",
    "open_proposal": b"S11-EX10-B4-PROPOSAL-v1\n",
    "single_rpc_proposal": b"S11-EX10-B4-RPC-PROPOSAL-v1\n",
    "open_evidence_frame": b"S11-EX10-B4-EVIDENCE-FRAME-v1\n",
}
RESERVATION_INTENT_FIELDS = (
    "schema_version",
    "namespace",
    "reservation_id",
    "authority_id",
    "authority_epoch",
    "session_id",
    "session_header_sha256",
    "runtime_profile_sha256",
    "source_revision",
    "project",
    "deployment_verification_reference",
    "coordination_policy_reference",
    "host_root_binding_sha256",
    "requested_at",
    "freshness_deadline",
    "nonce",
    "request_id",
    "request_digest",
    "acknowledgment_digest",
    "previous_authority_revision",
    "previous_ledger_digest",
    "previous_witness_digest",
    "expected_next_revision",
    "run_id",
    "run_identity",
    "fencing_token",
    "operator_authorization_reference",
)


class BridgeProtocolError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _require(condition: bool, code: str = "SCHEMA_INVALID") -> None:
    if not condition:
        raise BridgeProtocolError(code)


def _canonical(value: Any) -> bytes:
    try:
        return canonical_bytes(value)
    except OpenEvidenceError:
        raise BridgeProtocolError("SCHEMA_INVALID") from None


def _same(actual: Any, expected: Any, code: str = "REFERENCE_MISMATCH") -> None:
    _require(type(actual) is type(expected) and _canonical(actual) == _canonical(expected), code)


def _time(value: str) -> datetime:
    try:
        return utc_instant(value)
    except OpenEvidenceError:
        raise BridgeProtocolError("SCHEMA_INVALID") from None


def schema_definitions() -> dict[str, Any]:
    return copy.deepcopy(_DEFINITIONS)


def request_schemas() -> list[dict[str, Any]]:
    return copy.deepcopy(_REQUEST_SCHEMAS)


def response_schemas() -> list[dict[str, Any]]:
    return copy.deepcopy(_RESPONSE_SCHEMAS)


def _validates(value: Any, schema: Mapping[str, Any]) -> bool:
    try:
        _validate(value, schema)
        return True
    except BridgeProtocolError:
        return False


def _validate(value: Any, schema: Mapping[str, Any]) -> None:
    if "anyOf" in schema:
        _require(any(_validates(value, part) for part in schema["anyOf"]))
    if "oneOf" in schema:
        _require(sum(_validates(value, part) for part in schema["oneOf"]) == 1)
    if "const" in schema:
        _same(value, schema["const"], "SCHEMA_INVALID")
    if "enum" in schema:
        _require(
            any(
                type(value) is type(part) and _canonical(value) == _canonical(part)
                for part in schema["enum"]
            )
        )
    kind = schema.get("type")
    if kind is None:
        return
    types = _JSON_VALUE_TYPES
    _require(type(value) is types[kind])
    if kind == "object":
        props = schema.get("properties", {})
        _require(set(schema.get("required", ())) <= value.keys())
        if schema.get("additionalProperties") is False:
            _require(value.keys() <= props.keys())
        for field in value.keys() & props.keys():
            _validate(value[field], props[field])
    elif kind == "array":
        _require(
            schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", MAX_RESPONSE_BYTES)
        )
        if schema.get("uniqueItems"):
            _require(len({_canonical(v) for v in value}) == len(value))
        for part in value:
            _validate(part, schema["items"])
    elif kind == "string":
        _require(
            schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", MAX_RESPONSE_BYTES)
        )
        pattern = schema.get("pattern")
        if pattern:
            if pattern.startswith("^[0-9]{4}-[0-9]{2}-[0-9]{2}T"):
                _time(value)  # Full Gregorian validation, not a timestamp regex alone.
            else:
                _require(re.fullmatch(pattern, value) is not None)
    elif kind == "integer":
        _require(
            schema.get("minimum", -SAFE_INTEGER_MAX)
            <= value
            <= schema.get("maximum", SAFE_INTEGER_MAX)
        )


@dataclass(frozen=True, slots=True)
class ClosedMessage:
    kind: str
    canonical: bytes = dataclass_field(repr=False)

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self.canonical)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical).hexdigest()


@dataclass(frozen=True, slots=True)
class Request(ClosedMessage):
    @property
    def operation(self) -> Operation:
        return Operation(self.to_dict()["operation"])


@dataclass(frozen=True, slots=True)
class Response(ClosedMessage):
    @property
    def operation(self) -> Operation:
        return Operation(self.to_dict()["operation"])


def _parse(kind: str, raw: bytes, *, maximum: int) -> ClosedMessage:
    try:
        value = parse_canonical_bytes(raw, maximum=maximum)
    except OpenEvidenceError:
        raise BridgeProtocolError("SCHEMA_INVALID") from None
    if kind in {"request", "response"}:
        _require(type(value.get("operation")) is str, "UNKNOWN_OPERATION")
        _require(value["operation"] in {op.value for op in Operation}, "UNKNOWN_OPERATION")
        candidates = _REQUEST_SCHEMAS if kind == "request" else _RESPONSE_SCHEMAS
        candidates = [
            candidate
            for candidate in candidates
            if candidate["properties"]["operation"]["const"] == value["operation"]
        ]
        schema = {"oneOf": candidates}
    elif kind == "capability":
        candidates = [s["properties"]["capability"] for s in _REQUEST_SCHEMAS]
        # Normal/admin variants repeat schemas; dispatch by role before validation.
        schema = (
            next(s for s in candidates if s["properties"]["role"]["const"] == value.get("role"))
            if any(s["properties"]["role"]["const"] == value.get("role") for s in candidates)
            else None
        )
        _require(schema is not None)
    else:
        _require(kind in _DEFINITIONS)
        schema = _DEFINITIONS[kind]
    _validate(value, schema)
    if kind == "request":
        return Request(kind, raw)
    if kind == "response":
        return Response(kind, raw)
    return ClosedMessage(kind, raw)


def parse_request_bytes(raw: bytes) -> Request:
    return _parse("request", raw, maximum=MAX_REQUEST_BYTES)


def parse_response_bytes(raw: bytes) -> Response:
    return _parse("response", raw, maximum=MAX_RESPONSE_BYTES)


def parse_open_one_bytes(raw: bytes) -> ClosedMessage:
    return _parse("open_one_permit", raw, maximum=MAX_REQUEST_BYTES)


def parse_reservation_bytes(raw: bytes) -> ClosedMessage:
    return _parse("reservation", raw, maximum=MAX_RESPONSE_BYTES)


def parse_reservation_receipt_bytes(raw: bytes) -> ClosedMessage:
    return _parse("reservation_receipt", raw, maximum=MAX_REQUEST_BYTES)


def parse_coordination_head_bytes(raw: bytes) -> ClosedMessage:
    return _parse("coordination_head", raw, maximum=MAX_REQUEST_BYTES)


def _digest_projection(value: Mapping[str, Any], excluded: frozenset[str]) -> str:
    return hashlib.sha256(
        _canonical({k: v for k, v in value.items() if k not in excluded})
    ).hexdigest()


def request_digest(value: Request) -> str:
    data = parse_request_bytes(value.canonical).to_dict()
    return _digest_projection(data, frozenset({"request_digest", "sender_signature", "capability"}))


def open_permit_digest(value: ClosedMessage) -> str:
    data = parse_open_one_bytes(value.canonical).to_dict()
    return _digest_projection(data, frozenset({"permit_digest", "signature"}))


def reservation_digest(value: ClosedMessage) -> str:
    data = parse_reservation_bytes(value.canonical).to_dict()
    return hashlib.sha256(_canonical({k: data[k] for k in RESERVATION_INTENT_FIELDS})).hexdigest()


def reservation_record_digest(value: ClosedMessage) -> str:
    data = parse_reservation_bytes(value.canonical).to_dict()
    return _digest_projection(data, frozenset({"record_digest"}))


def result_digest(value: Response) -> str:
    data = parse_response_bytes(value.canonical).to_dict()
    return hashlib.sha256(_canonical(data["result"])).hexdigest()


def response_digest(value: Response) -> str:
    data = parse_response_bytes(value.canonical).to_dict()
    return _digest_projection(data, frozenset({"response_digest", "signature"}))


@dataclass(frozen=True, slots=True)
class PublicKeyPin:
    """Externally approved pin. Loading/OS ownership qualification is a host concern."""

    key_id: str
    key_epoch: int
    public_key: Ed25519PublicKey


def _verify_signature(
    kind: str, value: dict[str, Any], key: Ed25519PublicKey, signature_field: str = "signature"
) -> None:
    _require(isinstance(key, Ed25519PublicKey), "AUTHENTICATION_FAILED")
    preimage = {k: v for k, v in value.items() if k != signature_field}
    try:
        key.verify(bytes.fromhex(value[signature_field]), DOMAINS[kind] + _canonical(preimage))
    except (ValueError, InvalidSignature):
        raise BridgeProtocolError("AUTHENTICATION_FAILED") from None


def _verify_pin(value: dict[str, Any], pin: PublicKeyPin, *, bridge: bool) -> None:
    _require(type(pin) is PublicKeyPin, "AUTHENTICATION_FAILED")
    prefix = "bridge_" if bridge else ""
    _same(value[prefix + "key_id"], pin.key_id, "AUTHENTICATION_FAILED")
    _same(value[prefix + "key_epoch"], pin.key_epoch, "AUTHENTICATION_FAILED")


def _fresh(value: Mapping[str, Any], now: datetime, *, maximum: int, code: str) -> None:
    _require(
        type(now) is datetime and now.tzinfo is not None and now.utcoffset() == UTC.utcoffset(now),
        "AUTHORIZATION_FAILED",
    )
    issued, expires = _time(value["issued_at"]), _time(value["expires_at"])
    _require(
        issued <= now + timedelta(seconds=5)
        and now < expires
        and timedelta(0) < expires - issued <= timedelta(seconds=maximum),
        code,
    )


@dataclass(frozen=True, slots=True)
class RequestContext:
    request_id: str
    nonce: str
    exchange_id: str
    operation: str
    session_id: str
    session_header_sha256: str
    runtime_profile_sha256: str
    authority_id: str
    authority_epoch: int
    authority_revision: int
    source_revision: str
    project: str
    authorization_reference: bytes
    deployment_verification_sha256: str
    container_identity_digest: str
    expected_run_id: str | None
    reservation_id: str | None
    reservation_digest: str | None
    fencing_token: int | None
    holder_public_key_hex: str
    expected_payload: bytes = dataclass_field(repr=False)
    now: datetime


@dataclass(frozen=True, slots=True)
class RecoveryRequestContext(RequestContext):
    operator_authorization_reference: bytes


@dataclass(frozen=True, slots=True)
class AuthenticatedRequest:
    request: Request
    capability: ClosedMessage
    holder_public_key_hex: str


def _reference(raw: bytes) -> dict[str, Any]:
    try:
        value = parse_canonical_bytes(raw)
    except OpenEvidenceError:
        raise BridgeProtocolError("AUTHORIZATION_FAILED") from None
    schema = _REQUEST_SCHEMAS[0]["properties"]["authorization_reference"]
    _validate(value, schema)
    return value


def _verify_request(
    raw: bytes,
    context: RequestContext,
    bridge_pin: PublicKeyPin,
    holder_public_key: Ed25519PublicKey,
) -> AuthenticatedRequest:
    request = parse_request_bytes(raw)
    value = request.to_dict()
    _require(type(context) in {RequestContext, RecoveryRequestContext}, "AUTHORIZATION_FAILED")
    recovery = request.operation in RECOVERY_OPERATIONS
    _require(recovery == (type(context) is RecoveryRequestContext), "AUTHORIZATION_FAILED")
    for field in (
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
    ):
        _same(value[field], getattr(context, field), "AUTHORIZATION_FAILED")
    _same(
        value["authorization_reference"],
        _reference(context.authorization_reference),
        "AUTHORIZATION_FAILED",
    )
    _same(value["request_digest"], request_digest(request), "AUTHENTICATION_FAILED")
    cap = _parse("capability", _canonical(value["capability"]), maximum=MAX_REQUEST_BYTES)
    claims = cap.to_dict()
    _verify_pin(claims, bridge_pin, bridge=True)
    _verify_signature("capability", claims, bridge_pin.public_key)
    _same(
        claims["role"],
        "RECOVERY_OPERATOR" if recovery else "SESSION_EXECUTOR",
        "AUTHORIZATION_FAILED",
    )
    for field in (
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
    ):
        _same(claims[field], value[field], "AUTHORIZATION_FAILED")
    for field, expected in {
        "namespace": NAMESPACE,
        "request_nonce": value["nonce"],
        "request_body_digest": value["request_digest"],
        "expected_revision": value["authority_revision"],
        "deployment_verification_sha256": context.deployment_verification_sha256,
        "container_identity_digest": context.container_identity_digest,
        "expected_run_id": context.expected_run_id,
        "reservation_id": context.reservation_id,
        "reservation_digest": context.reservation_digest,
        "fencing_token": context.fencing_token,
        "holder_public_key_hex": context.holder_public_key_hex,
    }.items():
        _same(claims[field], expected, "AUTHORIZATION_FAILED")
    _require(isinstance(holder_public_key, Ed25519PublicKey), "AUTHENTICATION_FAILED")
    _same(
        holder_public_key.public_bytes_raw().hex(),
        context.holder_public_key_hex,
        "AUTHENTICATION_FAILED",
    )
    _verify_signature("request", value, holder_public_key, "sender_signature")
    _fresh(value, context.now, maximum=120, code="REQUEST_EXPIRED")
    _fresh(claims, context.now, maximum=120, code="CAPABILITY_EXPIRED")
    _require(
        _time(value["expires_at"]) <= _time(claims["expires_at"])
        and _time(value["issued_at"]) >= _time(claims["issued_at"]) - timedelta(seconds=5),
        "CAPABILITY_EXPIRED",
    )
    payload = value["payload"]
    expected_payload = _expected(context.expected_payload, set(payload))
    _same(payload, expected_payload, "AUTHORIZATION_FAILED")
    if "expected_head" in payload:
        for field, target in (
            ("authority_id", "authority_id"),
            ("authority_epoch", "authority_epoch"),
            ("revision", "authority_revision"),
        ):
            _same(payload["expected_head"][field], value[target], "REVISION_STALE")
    if "expected_authority_id" in payload:
        _same(payload["expected_authority_id"], value["authority_id"], "AUTHORIZATION_FAILED")
        _same(payload["expected_authority_epoch"], value["authority_epoch"], "AUTHORIZATION_FAILED")
    if "run_identity" in payload:
        identity = qualify_run_identity(payload["run_identity"])
        _same(identity["run_id"], context.expected_run_id, "RUN_ID_MISMATCH")
        _same(identity["git_sha"], value["source_revision"], "RUN_ID_MISMATCH")
        if request.operation is Operation.RESERVE_START:
            _same(identity["execution_nonce"], value["nonce"], "RUN_ID_MISMATCH")
    for field in ("reservation_id", "reservation_digest", "fencing_token"):
        if field in payload:
            _same(payload[field], getattr(context, field), "AUTHORIZATION_FAILED")
    if recovery:
        _same(
            payload["operator_authorization_reference"],
            _reference(context.operator_authorization_reference),
            "AUTHORIZATION_FAILED",
        )
    return AuthenticatedRequest(request, cap, context.holder_public_key_hex)


def verify_request(
    raw: bytes,
    *,
    expected_context: RequestContext,
    bridge_pin: PublicKeyPin,
    holder_public_key: Ed25519PublicKey,
) -> AuthenticatedRequest:
    """Normal transport admits only the seven session executor operations."""
    _require(type(expected_context) is RequestContext, "AUTHORIZATION_FAILED")
    return _verify_request(raw, expected_context, bridge_pin, holder_public_key)


def verify_recovery_request(
    raw: bytes,
    *,
    expected_context: RecoveryRequestContext,
    bridge_pin: PublicKeyPin,
    operator_public_key: Ed25519PublicKey,
) -> AuthenticatedRequest:
    """For the host-only recovery endpoint, after external operator qualification."""
    _require(type(expected_context) is RecoveryRequestContext, "AUTHORIZATION_FAILED")
    return _verify_request(raw, expected_context, bridge_pin, operator_public_key)


def qualify_run_identity(value: dict[str, Any]) -> dict[str, Any]:
    schema = _DEFINITIONS["reservation"]["properties"]["run_identity"]
    _validate(value, schema)
    _same(
        value["identity_digest"],
        _digest_projection(value, frozenset({"identity_digest"})),
        "RUN_ID_MISMATCH",
    )
    calculated = make_run_id(
        started_at=_time(value["started_at"]),
        git_sha=value["git_sha"],
        dataset_id=value["dataset_id"],
        scenario_id=value["scenario_id"],
        seed=value["seed"],
        execution_nonce=value["execution_nonce"],
    )
    _same(value["run_id"], calculated, "RUN_ID_MISMATCH")
    return copy.deepcopy(value)


@dataclass(frozen=True, slots=True)
class PermitContext:
    """All original reserve/grant bindings from qualified sources, without optional expectations."""

    expected_projection: bytes
    reservation_deadline: datetime
    now: datetime


@dataclass(frozen=True, slots=True)
class ReceiptContext:
    expected_projection: bytes
    now: datetime


@dataclass(frozen=True, slots=True)
class CoordinationHeadContext:
    expected_projection: bytes
    minimum_sequence: int
    minimum_fencing_token: int
    now: datetime


def _expected(raw: bytes, fields: set[str]) -> dict[str, Any]:
    try:
        value = parse_canonical_bytes(raw, maximum=MAX_RESPONSE_BYTES)
    except OpenEvidenceError:
        raise BridgeProtocolError("AUTHORIZATION_FAILED") from None
    _require(set(value) == fields, "AUTHORIZATION_FAILED")
    return value


def verify_open_one(
    raw: bytes,
    *,
    expected_context: PermitContext,
    bridge_pin: PublicKeyPin,
    holder_public_key: Ed25519PublicKey,
) -> ClosedMessage:
    message = parse_open_one_bytes(raw)
    value = message.to_dict()
    _require(type(expected_context) is PermitContext, "AUTHORIZATION_FAILED")
    excluded = {
        "signature",
        "permit_digest",
        "issued_at",
        "expires_at",
        "permit_id",
        "permit_nonce",
        "kind",
        "schema_version",
    }
    expected = _expected(expected_context.expected_projection, set(value) - excluded)
    for field in expected:
        _same(value[field], expected[field], "AUTHORIZATION_FAILED")
    _verify_pin(value, bridge_pin, bridge=True)
    _same(value["permit_digest"], open_permit_digest(message), "AUTHENTICATION_FAILED")
    _verify_signature("open_one_permit", value, bridge_pin.public_key)
    _require(isinstance(holder_public_key, Ed25519PublicKey), "AUTHENTICATION_FAILED")
    _same(
        value["holder_public_key_hex"],
        holder_public_key.public_bytes_raw().hex(),
        "AUTHENTICATION_FAILED",
    )
    _fresh(value, expected_context.now, maximum=120, code="CAPABILITY_EXPIRED")
    deadline = expected_context.reservation_deadline
    _require(
        type(deadline) is datetime
        and deadline.tzinfo is not None
        and deadline.utcoffset() == UTC.utcoffset(deadline),
        "AUTHORIZATION_FAILED",
    )
    _require(_time(value["expires_at"]) <= deadline, "CAPABILITY_EXPIRED")
    _require(
        value["base_authority_revision"] < SAFE_INTEGER_MAX
        and value["expected_open_revision"] == value["base_authority_revision"] + 1,
        "REVISION_STALE",
    )
    identity = qualify_run_identity(value["run_identity"])
    _same(identity["git_sha"], value["source_revision"], "RUN_ID_MISMATCH")
    _same(identity["execution_nonce"], value["attempt_nonce"], "RUN_ID_MISMATCH")
    return message


def qualify_reservation(raw: bytes) -> ClosedMessage:
    """Validate the two different reservation digests and immutable record invariants."""
    message = parse_reservation_bytes(raw)
    value = message.to_dict()
    _same(value["reservation_digest"], reservation_digest(message), "REFERENCE_MISMATCH")
    _same(value["record_digest"], reservation_record_digest(message), "REFERENCE_MISMATCH")
    _require(
        value["previous_authority_revision"] < SAFE_INTEGER_MAX
        and value["expected_next_revision"] == value["previous_authority_revision"] + 1,
        "REVISION_STALE",
    )
    identity = qualify_run_identity(value["run_identity"])
    _same(identity["run_id"], value["run_id"], "RUN_ID_MISMATCH")
    _same(identity["git_sha"], value["source_revision"], "RUN_ID_MISMATCH")
    _same(identity["execution_nonce"], value["nonce"], "RUN_ID_MISMATCH")
    _require(_time(value["requested_at"]) <= _time(value["updated_at"]), "SCHEMA_INVALID")
    _require(_time(value["requested_at"]) < _time(value["freshness_deadline"]), "SCHEMA_INVALID")
    _require(
        (value["record_sequence"] == 1) == (value["previous_reservation_record_digest"] is None)
    )
    state = value["state"]
    if state == "COMMITTED":
        for field in (
            "reservation_receipt_reference",
            "open_witness_reference",
            "evidence_receipt_reference",
        ):
            _require(value[field] is not None)
        _same(
            value["open_witness_reference"]["revision"],
            value["expected_next_revision"],
            "REVISION_STALE",
        )
        _same(value["open_witness_reference"]["authority_id"], value["authority_id"])
        _same(value["open_witness_reference"]["authority_epoch"], value["authority_epoch"])
    elif state == "UNCERTAIN":
        _require(bool(value["uncertainty_codes"]))
    elif state == "CANCELLED_MANUALLY":
        _require(value["manual_resolution_reference"] is not None)
    return message


def verify_reservation_receipt(
    raw: bytes, *, expected_context: ReceiptContext, witness_pin: PublicKeyPin
) -> ClosedMessage:
    message = parse_reservation_receipt_bytes(raw)
    value = message.to_dict()
    _require(type(expected_context) is ReceiptContext, "AUTHORIZATION_FAILED")
    expected = _expected(
        expected_context.expected_projection, set(value) - {"signature", "issued_at"}
    )
    for field in expected:
        _same(value[field], expected[field], "REFERENCE_MISMATCH")
    _verify_pin(value, witness_pin, bridge=False)
    _verify_signature("reservation_receipt", value, witness_pin.public_key)
    _require(
        type(expected_context.now) is datetime
        and expected_context.now.tzinfo is not None
        and expected_context.now.utcoffset() == UTC.utcoffset(expected_context.now),
        "AUTHORIZATION_FAILED",
    )
    _require(
        _time(value["issued_at"]) <= expected_context.now + timedelta(seconds=5), "REQUEST_EXPIRED"
    )
    transition, state = value["transition"], value["state"]
    required_state = {
        "RESERVE": "PENDING",
        "COMMIT_OPEN": "COMMITTED",
        "RECORD_UNCERTAIN": "UNCERTAIN",
        "CANCEL_MANUAL": "CANCELLED_MANUALLY",
        "ADOPT_OPEN_MANUAL": "COMMITTED",
    }
    if transition in required_state:
        _same(state, required_state[transition], "SCHEMA_INVALID")
    if transition in {"COMMIT_OPEN", "ADOPT_OPEN_MANUAL"}:
        _require(value["open_witness_digest"] is not None)
    if transition == "RETIRE_TERMINAL":
        _same(state, "COMMITTED", "SCHEMA_INVALID")
        _require(value["terminal_witness_digest"] is not None)
    if transition == "RESERVE":
        _require(value["open_witness_digest"] is None and value["terminal_witness_digest"] is None)
    return message


def verify_coordination_head(
    raw: bytes, *, expected_context: CoordinationHeadContext, witness_pin: PublicKeyPin
) -> ClosedMessage:
    message = parse_coordination_head_bytes(raw)
    value = message.to_dict()
    _require(type(expected_context) is CoordinationHeadContext, "AUTHORIZATION_FAILED")
    expected = _expected(
        expected_context.expected_projection,
        set(value) - {"signature", "issued_at", "expires_at", "sequence", "fencing_token"},
    )
    for field in expected:
        _same(value[field], expected[field], "REFERENCE_MISMATCH")
    _verify_pin(value, witness_pin, bridge=False)
    _verify_signature("coordination_head", value, witness_pin.public_key)
    _fresh(value, expected_context.now, maximum=240, code="REQUEST_EXPIRED")
    _require(
        type(expected_context.minimum_sequence) is int
        and expected_context.minimum_sequence >= 0
        and type(expected_context.minimum_fencing_token) is int
        and expected_context.minimum_fencing_token >= 0,
        "AUTHORIZATION_FAILED",
    )
    _require(value["sequence"] >= expected_context.minimum_sequence, "REVISION_STALE")
    if value["fencing_token"] is not None:
        _require(value["fencing_token"] >= expected_context.minimum_fencing_token, "REVISION_STALE")
    return message


def verify_response(
    raw: bytes,
    *,
    authenticated_request: AuthenticatedRequest,
    bridge_pin: PublicKeyPin,
    now: datetime,
) -> Response:
    """Authenticate response/result bytes; embedded history heads still need their own verifier."""
    response = parse_response_bytes(raw)
    value = response.to_dict()
    _require(type(authenticated_request) is AuthenticatedRequest, "AUTHORIZATION_FAILED")
    request = parse_request_bytes(authenticated_request.request.canonical).to_dict()
    _verify_pin(value, bridge_pin, bridge=True)
    _verify_signature("response", value, bridge_pin.public_key)
    for field in (
        "request_id",
        "nonce",
        "exchange_id",
        "operation",
        "request_digest",
        "session_id",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
    ):
        _same(value[field], request[field], "REFERENCE_MISMATCH")
    _same(value["result_digest"], result_digest(response), "AUTHENTICATION_FAILED")
    _same(value["response_digest"], response_digest(response), "AUTHENTICATION_FAILED")
    _same(value["authority_revision_before"], request["authority_revision"], "REVISION_STALE")
    _require(
        type(now) is datetime and now.tzinfo is not None and now.utcoffset() == UTC.utcoffset(now),
        "AUTHORIZATION_FAILED",
    )
    _require(_time(value["completed_at"]) <= now + timedelta(seconds=5), "REQUEST_EXPIRED")
    claims = request["capability"]
    for field, claim, code in (
        ("run_id", "expected_run_id", "RUN_ID_MISMATCH"),
        ("reservation_id", "reservation_id", "REFERENCE_MISMATCH"),
    ):
        if claims[claim] is not None:
            _same(value[field], claims[claim], code)
    if value["status"] == "OK":
        if response.operation is Operation.COMMIT_OPEN:
            _require(
                value["authority_revision_before"] < SAFE_INTEGER_MAX
                and value["authority_revision_after"] == value["authority_revision_before"] + 1,
                "REVISION_STALE",
            )
            _same(value["run_id"], request["payload"]["run_identity"]["run_id"], "RUN_ID_MISMATCH")
            _same(
                value["reservation_id"], request["payload"]["reservation_id"], "REFERENCE_MISMATCH"
            )
        elif response.operation not in RECOVERY_OPERATIONS:
            _same(
                value["authority_revision_after"],
                value["authority_revision_before"],
                "REVISION_STALE",
            )
    return response


def parse_host_challenge_bytes(raw: bytes) -> ClosedMessage:
    return _parse("host_challenge", raw, maximum=MAX_REQUEST_BYTES)


def verify_host_challenge(
    raw: bytes, *, expected_context: ReceiptContext, bridge_pin: PublicKeyPin
) -> ClosedMessage:
    message = parse_host_challenge_bytes(raw)
    value = message.to_dict()
    _require(type(expected_context) is ReceiptContext, "AUTHORIZATION_FAILED")
    expected = _expected(
        expected_context.expected_projection, set(value) - {"signature", "issued_at", "expires_at"}
    )
    for field in expected:
        _same(value[field], expected[field], "REFERENCE_MISMATCH")
    _verify_pin(value, bridge_pin, bridge=True)
    _verify_signature("host_challenge", value, bridge_pin.public_key)
    _fresh(value, expected_context.now, maximum=120, code="REQUEST_EXPIRED")
    return message


def verify_open_proposal(
    raw: bytes, *, challenge: ClosedMessage, holder_public_key: Ed25519PublicKey
) -> ClosedMessage:
    """The holder must already be admitted from the observed process/channel."""
    message = _parse("open_proposal", raw, maximum=MAX_REQUEST_BYTES)
    value = message.to_dict()
    host = parse_host_challenge_bytes(challenge.canonical).to_dict()
    _require(isinstance(holder_public_key, Ed25519PublicKey), "AUTHENTICATION_FAILED")
    _same(
        value["holder_public_key_hex"],
        holder_public_key.public_bytes_raw().hex(),
        "AUTHENTICATION_FAILED",
    )
    _same(value["exchange_id"], host["exchange_id"], "REFERENCE_MISMATCH")
    _same(value["host_challenge_digest"], challenge.sha256, "REFERENCE_MISMATCH")
    _verify_signature("open_proposal", value, holder_public_key)
    body = value["reserve_request_body"]
    _same(body["exchange_id"], host["exchange_id"], "REFERENCE_MISMATCH")
    _same(body["operation"], Operation.RESERVE_START.value, "UNKNOWN_OPERATION")
    return message


def verify_single_rpc_proposal(
    raw: bytes, *, challenge: ClosedMessage, holder_public_key: Ed25519PublicKey
) -> ClosedMessage:
    message = _parse("single_rpc_proposal", raw, maximum=MAX_REQUEST_BYTES)
    value = message.to_dict()
    host = parse_host_challenge_bytes(challenge.canonical).to_dict()
    _require(isinstance(holder_public_key, Ed25519PublicKey), "AUTHENTICATION_FAILED")
    _same(
        value["holder_public_key_hex"],
        holder_public_key.public_bytes_raw().hex(),
        "AUTHENTICATION_FAILED",
    )
    _same(value["exchange_id"], host["exchange_id"], "REFERENCE_MISMATCH")
    _same(value["host_challenge_digest"], challenge.sha256, "REFERENCE_MISMATCH")
    _verify_signature("single_rpc_proposal", value, holder_public_key)
    body = value["request_body"]
    _same(body["exchange_id"], host["exchange_id"], "REFERENCE_MISMATCH")
    _require(
        body["operation"] not in {Operation.RESERVE_START.value, Operation.COMMIT_OPEN.value},
        "UNKNOWN_OPERATION",
    )
    return message


def verify_evidence_frame(
    raw: bytes, *, exchange_id: str, open_permit_digest: str, holder_public_key: Ed25519PublicKey
) -> ClosedMessage:
    message = _parse("open_evidence_frame", raw, maximum=MAX_REQUEST_BYTES)
    value = message.to_dict()
    _same(value["exchange_id"], exchange_id, "REFERENCE_MISMATCH")
    _same(value["open_permit_digest"], open_permit_digest, "REFERENCE_MISMATCH")
    _verify_signature("open_evidence_frame", value, holder_public_key)
    body = value["commit_request_body"]
    _same(body["exchange_id"], exchange_id, "REFERENCE_MISMATCH")
    _same(body["operation"], Operation.COMMIT_OPEN.value, "UNKNOWN_OPERATION")
    _same(body["payload"]["open_permit_digest"], open_permit_digest, "REFERENCE_MISMATCH")
    return message


def parse_handshake_frame_bytes(raw: bytes) -> ClosedMessage:
    message = _parse("handshake_frame", raw, maximum=MAX_RESPONSE_BYTES)
    value = message.to_dict()
    if value["direction"] == "EXECUTOR_TO_HOST":
        _require(len(raw) <= MAX_REQUEST_BYTES)
    if "exchange_id" in value["body"]:
        _same(value["exchange_id"], value["body"]["exchange_id"])
    return message


def parse_single_rpc_frame_bytes(raw: bytes) -> ClosedMessage:
    message = _parse("single_rpc_frame", raw, maximum=MAX_RESPONSE_BYTES)
    value = message.to_dict()
    if value["direction"] == "EXECUTOR_TO_HOST":
        _require(len(raw) <= MAX_REQUEST_BYTES)
    if "exchange_id" in value["body"]:
        _same(value["exchange_id"], value["body"]["exchange_id"])
    return message


def encode_frame(message: ClosedMessage) -> bytes:
    _require(message.kind in {"handshake_frame", "single_rpc_frame"})
    parsed = (
        parse_handshake_frame_bytes(message.canonical)
        if message.kind == "handshake_frame"
        else parse_single_rpc_frame_bytes(message.canonical)
    )
    return len(parsed.canonical).to_bytes(4, "big") + parsed.canonical


def decode_frame(raw: bytes, *, exchange: str) -> ClosedMessage:
    """Decode one complete bounded frame; streaming/deadline handling belongs to W06."""
    _require(type(raw) is bytes and 4 < len(raw) <= MAX_RESPONSE_BYTES + 4)
    length = int.from_bytes(raw[:4], "big")
    _require(0 < length <= MAX_RESPONSE_BYTES and length == len(raw) - 4)
    _require(exchange in {"START", "SINGLE_RPC"})
    return (
        parse_handshake_frame_bytes(raw[4:])
        if exchange == "START"
        else parse_single_rpc_frame_bytes(raw[4:])
    )


def parse_capability_bytes(raw: bytes) -> ClosedMessage:
    return _parse("capability", raw, maximum=MAX_REQUEST_BYTES)


def parse_open_proposal_bytes(raw: bytes) -> ClosedMessage:
    return _parse("open_proposal", raw, maximum=MAX_REQUEST_BYTES)


def parse_single_rpc_proposal_bytes(raw: bytes) -> ClosedMessage:
    return _parse("single_rpc_proposal", raw, maximum=MAX_REQUEST_BYTES)


def parse_evidence_frame_bytes(raw: bytes) -> ClosedMessage:
    return _parse("open_evidence_frame", raw, maximum=MAX_REQUEST_BYTES)


def signing_preimage(message: ClosedMessage) -> bytes:
    """Fixed type selects the domain and exact signature field; no caller-supplied domain."""
    _require(message.kind in DOMAINS, "SCHEMA_INVALID")
    maximum = MAX_RESPONSE_BYTES if message.kind == "response" else MAX_REQUEST_BYTES
    checked = _parse(message.kind, message.canonical, maximum=maximum).to_dict()
    signature_field = "sender_signature" if message.kind == "request" else "signature"
    return DOMAINS[message.kind] + _canonical(
        {k: v for k, v in checked.items() if k != signature_field}
    )
