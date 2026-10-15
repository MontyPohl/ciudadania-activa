"""EX-10 B3 offline lifecycle authority. No operational integration.

Public API: exactly the eight read functions in __all__. The six underscore
mutators are INTERNAL ONLY and require an injected trusted future B4 verifier.
No production verifier/coordinator, START, reservation, bridge, key provisioning,
historical scan, real genesis, automatic retry or recovery is provided.
"""

from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from . import ex10_authority_sqlite as sql
from . import ex10_authority_witness as witness
from . import ex10_governance_certificate as governance

_APPROVED_BOOTSTRAP_SCHEMAS_JSON = r"""{"candidate_core":{"additionalProperties":false,"properties":{"active_source_ids":{"items":{"minLength":1,"type":"string"},"type":"array"},"authority_epoch":{"maximum":9007199254740991,"minimum":1,"type":"integer"},"authority_id":{"pattern":"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$","type":"string"},"baseline_head":{"pattern":"^[0-9a-f]{40}$","type":"string"},"baseline_tree":{"pattern":"^[0-9a-f]{40}$","type":"string"},"completeness":{"enum":["COMPLETE","INCOMPLETE","CONTRADICTORY","UNREADABLE"],"type":"string"},"coverage_certificate_reference":{"additionalProperties":false,"properties":{"path":{"minLength":1,"type":"string"},"schema_version":{"minLength":1,"type":"string"},"sha256":{"pattern":"^[0-9a-f]{64}$","type":"string"}},"required":["path","sha256","schema_version"],"type":"object"},"namespace":{"const":"S11C3-EX10"},"observed_at":{"pattern":"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\\.[0-9]{6}Z$","type":"string"},"run_count":{"maximum":9007199254740991,"minimum":0,"type":"integer"},"run_findings":{"items":{"additionalProperties":false,"properties":{"contradiction_reference":{"anyOf":[{"additionalProperties":false,"properties":{"path":{"minLength":1,"type":"string"},"schema_version":{"minLength":1,"type":"string"},"sha256":{"pattern":"^[0-9a-f]{64}$","type":"string"}},"required":["path","sha256","schema_version"],"type":"object"},{"type":"null"}]},"normalized_facts_reference":{"additionalProperties":false,"properties":{"path":{"minLength":1,"type":"string"},"schema_version":{"minLength":1,"type":"string"},"sha256":{"pattern":"^[0-9a-f]{64}$","type":"string"}},"required":["path","sha256","schema_version"],"type":"object"},"run_id":{"minLength":1,"pattern":"^s11-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$","type":"string"},"source_ids":{"items":{"minLength":1,"type":"string"},"type":"array"}},"required":["run_id","source_ids","normalized_facts_reference","contradiction_reference"],"type":"object"},"type":"array"},"schema_version":{"const":"s11-ex10-authority-bootstrap-core-v1"},"scope_reference":{"additionalProperties":false,"properties":{"path":{"minLength":1,"type":"string"},"schema_version":{"minLength":1,"type":"string"},"sha256":{"pattern":"^[0-9a-f]{64}$","type":"string"}},"required":["path","sha256","schema_version"],"type":"object"},"source_count":{"maximum":9007199254740991,"minimum":1,"type":"integer"},"sources":{"items":{"additionalProperties":false,"properties":{"active_source":{"type":"boolean"},"custody_references":{"items":{"additionalProperties":false,"properties":{"path":{"minLength":1,"type":"string"},"schema_version":{"minLength":1,"type":"string"},"sha256":{"pattern":"^[0-9a-f]{64}$","type":"string"}},"required":["path","sha256","schema_version"],"type":"object"},"type":"array"},"diagnostic_codes":{"items":{"minLength":1,"type":"string"},"type":"array"},"location":{"minLength":1,"type":"string"},"observation_reference":{"additionalProperties":false,"properties":{"path":{"minLength":1,"type":"string"},"schema_version":{"minLength":1,"type":"string"},"sha256":{"pattern":"^[0-9a-f]{64}$","type":"string"}},"required":["path","sha256","schema_version"],"type":"object"},"required":{"type":"boolean"},"run_ids":{"items":{"pattern":"^s11-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$","type":"string"},"type":"array"},"snapshot_reference":{"anyOf":[{"additionalProperties":false,"properties":{"path":{"minLength":1,"type":"string"},"schema_version":{"minLength":1,"type":"string"},"sha256":{"pattern":"^[0-9a-f]{64}$","type":"string"}},"required":["path","sha256","schema_version"],"type":"object"},{"type":"null"}]},"source_digest":{"anyOf":[{"pattern":"^[0-9a-f]{64}$","type":"string"},{"type":"null"}]},"source_id":{"minLength":1,"type":"string"},"source_kind":{"enum":["EVIDENCE_ROOT","SESSION_ROOT","SOURCE_ANCHOR","CUSTODY","GOVERNANCE","WRITER_REGISTRY"],"type":"string"},"status":{"enum":["SCANNED","ABSENT_CERTIFIED","UNREADABLE","BLOCKED"],"type":"string"}},"required":["source_id","source_kind","location","required","status","observation_reference","snapshot_reference","source_digest","run_ids","active_source","custody_references","diagnostic_codes"],"type":"object"},"type":"array"},"unreadable_source_ids":{"items":{"minLength":1,"type":"string"},"type":"array"},"writer_closure_reference":{"additionalProperties":false,"properties":{"path":{"minLength":1,"type":"string"},"schema_version":{"minLength":1,"type":"string"},"sha256":{"pattern":"^[0-9a-f]{64}$","type":"string"}},"required":["path","sha256","schema_version"],"type":"object"}},"required":["schema_version","authority_id","authority_epoch","namespace","scope_reference","coverage_certificate_reference","baseline_head","baseline_tree","observed_at","completeness","sources","run_findings","unreadable_source_ids","active_source_ids","writer_closure_reference","run_count","source_count"],"type":"object"},"sealed_manifest":{"additionalProperties":false,"properties":{"approved_at":{"pattern":"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\\.[0-9]{6}Z$","type":"string"},"completeness":{"const":"COMPLETE"},"core_manifest_digest":{"pattern":"^[0-9a-f]{64}$","type":"string"},"core_manifest_reference":{"additionalProperties":false,"properties":{"path":{"minLength":1,"type":"string"},"schema_version":{"minLength":1,"type":"string"},"sha256":{"pattern":"^[0-9a-f]{64}$","type":"string"}},"required":["path","sha256","schema_version"],"type":"object"},"operator_approval_reference":{"additionalProperties":false,"properties":{"path":{"minLength":1,"type":"string"},"schema_version":{"minLength":1,"type":"string"},"sha256":{"pattern":"^[0-9a-f]{64}$","type":"string"}},"required":["path","sha256","schema_version"],"type":"object"},"schema_version":{"const":"s11-ex10-authority-bootstrap-manifest-v1"}},"required":["schema_version","core_manifest_reference","core_manifest_digest","operator_approval_reference","approved_at","completeness"],"type":"object"}}"""  # noqa: E501 -- verbatim frozen contract

BOOTSTRAP_SCHEMAS = sql.json.loads(_APPROVED_BOOTSTRAP_SCHEMAS_JSON)
__all__ = (
    "load_authority",
    "verify_authority",
    "current_revision",
    "list_official_runs",
    "find_run",
    "first_valid_status",
    "active_or_uncertain_runs",
    "verify_ack_target",
)
_VERIFIED_TOKEN = object()


class ExternalVerifier(Protocol):
    """Pinned trusted external adapter. This core does not implement B4."""

    def verify(
        self, *, authority_id, epoch, operation_id, revision, action, proof, candidate_state
    ): ...


class PublicationAdapter(Protocol):
    """Injected durable-intent / independent witness / atomic local publication.

    This interface is not an implementation of B4 or a witness service.
    Synthetic test adapters use only ephemeral keys and disposable SQLite.
    """

    def durable_intent(self, intent, candidate_payload): ...

    def publish_witness(self, payload, expected_previous): ...

    def atomic_commit(self, additions): ...

    def observe_head(self, receipt): ...


@dataclass(frozen=True, slots=True)
class AuthoritySnapshot:
    """Immutable unverified material; constructing it grants no authority."""

    canonical_material: bytes

    @property
    def material(self):
        return sql.parse_canonical(self.canonical_material)


@dataclass(frozen=True, slots=True)
class _VerifiedSnapshot:
    canonical_material: bytes
    observed_at: str
    canonical_current_head: bytes
    canonical_trust_pins: tuple[bytes, ...]
    expires_at: str
    authority_uncertainty: bool
    write_closed: bool
    token: object

    @property
    def material(self):
        return sql.parse_canonical(self.canonical_material)


def _now():
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _reference(reference):
    witness._validate_reference(reference)
    return copy.deepcopy(reference)


def _reference_row(state, ref_id):
    rows = [row for row in state["artifact_references"] if row["reference_id"] == ref_id]
    sql.require(len(rows) == 1, "HISTORY_INVALID")
    return {k: rows[0][k] for k in ("path", "sha256", "schema_version")}


def _checkpoint(record):
    genesis = record["genesis"]
    return {
        "revision": record["revision"],
        "operation_id": record["operation_id"],
        "authority_id": record["authority_id"],
        "authority_epoch": record["authority_epoch"],
        "ledger_digest": record["ledger_digest"],
        "previous_witness_digest": record["previous_witness_digest"],
        "witness_digest": sql.digest(record),
        "created_at": record["created_at"],
        "key_id": record["key_id"],
        "key_epoch": record["key_epoch"],
        "key_fingerprint": record["key_fingerprint"],
        "signature": record["signature"],
        "receipt_schema_version": record["witness_schema_version"],
        "genesis_bootstrap_sha256": None
        if genesis is None
        else genesis["bootstrap_manifest_sha256"],
        "genesis_g0_a3_approval_path": None
        if genesis is None
        else genesis["g0_a3_approval_reference"]["path"],
        "genesis_g0_a3_approval_sha256": None
        if genesis is None
        else genesis["g0_a3_approval_reference"]["sha256"],
        "genesis_g0_a3_approval_schema": None
        if genesis is None
        else genesis["g0_a3_approval_reference"]["schema_version"],
        "genesis_baseline_head": None if genesis is None else genesis["source_baseline_head"],
        "genesis_baseline_tree": None if genesis is None else genesis["source_baseline_tree"],
    }


def _receipts(state):
    result = []
    for cp in sorted(state["witness_checkpoints"], key=lambda r: r["revision"]):
        genesis = None
        if cp["revision"] == 0:
            genesis = {
                "bootstrap_manifest_sha256": cp["genesis_bootstrap_sha256"],
                "g0_a3_approval_reference": {
                    "path": cp["genesis_g0_a3_approval_path"],
                    "sha256": cp["genesis_g0_a3_approval_sha256"],
                    "schema_version": cp["genesis_g0_a3_approval_schema"],
                },
                "source_baseline_head": cp["genesis_baseline_head"],
                "source_baseline_tree": cp["genesis_baseline_tree"],
            }
        result.append(
            {
                "witness_schema_version": cp["receipt_schema_version"],
                "authority_id": cp["authority_id"],
                "authority_epoch": cp["authority_epoch"],
                "namespace": "S11C3-EX10",
                "operation_id": cp["operation_id"],
                "revision": cp["revision"],
                "ledger_digest": cp["ledger_digest"],
                "previous_witness_digest": cp["previous_witness_digest"],
                "created_at": cp["created_at"],
                "key_id": cp["key_id"],
                "key_epoch": cp["key_epoch"],
                "key_fingerprint": cp["key_fingerprint"],
                "genesis": genesis,
                "signature": cp["signature"],
            }
        )
    return result


def _validate_candidate(core, *, certified_source_ids):
    """Offline proof reconciliation; input observations are never a real scan."""
    sql.validate_schema(core, BOOTSTRAP_SCHEMAS["candidate_core"])
    sql.utc_time(core["observed_at"])
    for key in ("scope_reference", "coverage_certificate_reference", "writer_closure_reference"):
        _reference(core[key])
    sources = core["sources"]
    ids = [s["source_id"] for s in sources]
    findings = core["run_findings"]
    run_ids = [r["run_id"] for r in findings]
    contradictory = len(set(ids)) != len(ids) or len(set(run_ids)) != len(run_ids)
    contradictory |= core["source_count"] != len(sources) or core["run_count"] != len(set(run_ids))
    incomplete = set(ids) != set(certified_source_ids) or not certified_source_ids
    unreadable = False
    active = []
    unreadable_ids = []
    discovered = {}
    for source in sources:
        _reference(source["observation_reference"])
        if source["active_source"]:
            active.append(source["source_id"])
        if source["status"] == "UNREADABLE":
            unreadable = True
            unreadable_ids.append(source["source_id"])
        incomplete |= source["status"] == "BLOCKED"
        if source["status"] == "SCANNED":
            incomplete |= source["snapshot_reference"] is None or source["source_digest"] is None
            if source["snapshot_reference"] is not None:
                _reference(source["snapshot_reference"])
                contradictory |= source["source_digest"] != source["snapshot_reference"]["sha256"]
        if source["status"] == "ABSENT_CERTIFIED":
            contradictory |= (
                source["snapshot_reference"] is not None
                or source["source_digest"] is not None
                or bool(source["run_ids"])
            )
        contradictory |= source["run_ids"] != sorted(set(source["run_ids"]))
        for run_id in source["run_ids"]:
            discovered.setdefault(run_id, set()).add(source["source_id"])
        for ref in source["custody_references"]:
            _reference(ref)
    for finding in findings:
        _reference(finding["normalized_facts_reference"])
        contradictory |= finding["contradiction_reference"] is not None
        contradictory |= finding["source_ids"] != sorted(set(finding["source_ids"]))
        incomplete |= set(finding["source_ids"]) != discovered.get(finding["run_id"], set())
    incomplete |= set(discovered) != set(run_ids)
    contradictory |= core["unreadable_source_ids"] != sorted(unreadable_ids)
    contradictory |= core["active_source_ids"] != sorted(active)
    contradictory |= ids != sorted(ids) or run_ids != sorted(run_ids)
    return (
        "CONTRADICTORY"
        if contradictory
        else "UNREADABLE"
        if unreadable
        else "INCOMPLETE"
        if incomplete
        else "COMPLETE"
    )


def _build_bootstrap_candidate(fields, *, certified_source_ids):
    result = copy.deepcopy(fields)
    result["completeness"] = _validate_candidate(result, certified_source_ids=certified_source_ids)
    return result


def _validate_sealed_bootstrap(core, sealed, *, certified_source_ids, approval_verifier):
    sql.validate_schema(sealed, BOOTSTRAP_SCHEMAS["sealed_manifest"])
    _reference(sealed["core_manifest_reference"])
    _reference(sealed["operator_approval_reference"])
    sql.utc_time(sealed["approved_at"])
    sql.require(
        _validate_candidate(core, certified_source_ids=certified_source_ids)
        == core["completeness"]
        == "COMPLETE",
        "INCOMPLETE_BOOTSTRAP",
    )
    sql.require(
        sealed["core_manifest_digest"]
        == sealed["core_manifest_reference"]["sha256"]
        == sql.digest(core),
        "DIGEST_MISMATCH",
    )
    sql.require(approval_verifier is not None, "TRUST_UNAPPROVED")
    sql.require(approval_verifier(core, sealed) is None, "TRUST_UNAPPROVED")


def _verify_trust_state(trust, descriptor):
    sql.require(trust.get("trust_verifier") is not None and trust["pins"], "TRUST_UNAPPROVED")
    sql.require(
        trust["trust_verifier"](trust["pins"], trust["trust_root_reference"]) is None,
        "TRUST_UNAPPROVED",
    )
    root = min(trust["pins"], key=lambda p: p.record["key_epoch"])
    sql.require(
        root.record["activation_revision"] == 0 and root.record["key_epoch"] == 1,
        "TRUST_UNAPPROVED",
    )
    sql.require(
        sql.digest(root.record) == descriptor["trust_root_reference"]["sha256"]
        and descriptor["trust_root_reference"]["schema_version"] == root.record["schema_version"],
        "TRUST_UNAPPROVED",
    )
    for pin in trust["pins"]:
        record = pin.record
        sql.require(
            record["authority_id"] == descriptor["authority_id"]
            and record["authority_epoch"] == descriptor["authority_epoch"]
            and record["namespace"] == descriptor["namespace"],
            "TRUST_UNAPPROVED",
        )


def _reconcile_bootstrap(state, core, sealed, descriptor):
    """Bind the sealed approved core to exact immutable genesis facts."""
    metadata = state["metadata"][0]
    bootstrap = state["bootstrap_manifest"][0]
    sql.require(
        metadata["authority_id"] == core["authority_id"] == descriptor["authority_id"]
        and metadata["epoch"] == core["authority_epoch"] == descriptor["authority_epoch"],
        "REFERENCE_MISMATCH",
    )
    sql.require(
        metadata["namespace"] == core["namespace"] == descriptor["namespace"]
        and metadata["schema_version"]
        == descriptor["authority_schema_version"]
        == sql.SCHEMA_VERSION
        and metadata["descriptor_sha256"] == sql.digest(descriptor),
        "REFERENCE_MISMATCH",
    )
    for name in ("head", "tree"):
        sql.require(
            metadata["source_baseline_" + name]
            == core["baseline_" + name]
            == bootstrap["baseline_" + name]
            == descriptor["source_baseline_" + name],
            "REFERENCE_MISMATCH",
        )
    sql.require(
        _reference_row(state, metadata["g0_a3_approval_reference_id"])
        == descriptor["g0_a3_approval_reference"],
        "REFERENCE_MISMATCH",
    )
    sql.require(
        bootstrap["core_digest"] == sql.digest(core)
        and bootstrap["source_count"] == core["source_count"]
        and bootstrap["run_count"] == core["run_count"]
        and bootstrap["certified_at"] == sealed["approved_at"],
        "INCOMPLETE_BOOTSTRAP",
    )
    for field, reference in (
        ("scope_reference_id", core["scope_reference"]),
        ("coverage_certificate_reference_id", core["coverage_certificate_reference"]),
        ("operator_approval_reference_id", sealed["operator_approval_reference"]),
    ):
        sql.require(_reference_row(state, bootstrap[field]) == reference, "INCOMPLETE_BOOTSTRAP")
    sql.require(
        _reference_row(state, bootstrap["manifest_reference_id"])
        == descriptor["bootstrap_manifest_reference"]
        and sql.digest(sealed) == descriptor["bootstrap_manifest_reference"]["sha256"],
        "INCOMPLETE_BOOTSTRAP",
    )
    sources = {s["source_id"]: s for s in state["bootstrap_sources"]}
    sql.require(set(sources) == {s["source_id"] for s in core["sources"]}, "INCOMPLETE_BOOTSTRAP")
    for observed in core["sources"]:
        row = sources[observed["source_id"]]
        sql.require(
            row["location"] == observed["location"]
            and row["source_kind"] == observed["source_kind"]
            and row["status"] == observed["status"]
            and row["has_active_source"] == observed["active_source"]
            and row["observed_at"] == core["observed_at"],
            "INCOMPLETE_BOOTSTRAP",
        )
        sql.require(
            _reference_row(state, row["observation_reference_id"])
            == observed["observation_reference"],
            "INCOMPLETE_BOOTSTRAP",
        )
        if observed["snapshot_reference"] is None:
            sql.require(row["snapshot_reference_id"] is None, "INCOMPLETE_BOOTSTRAP")
        else:
            sql.require(
                _reference_row(state, row["snapshot_reference_id"])
                == observed["snapshot_reference"],
                "INCOMPLETE_BOOTSTRAP",
            )
        actual_ids = sorted(
            f["run_id"] for f in state["bootstrap_findings"] if f["source_id"] == row["source_id"]
        )
        sql.require(
            actual_ids == observed["run_ids"] and row["discovered_run_count"] == len(actual_ids),
            "INCOMPLETE_BOOTSTRAP",
        )
    imported = {r["run_id"]: r for r in state["official_runs"] if r["introduced_revision"] == 0}
    sql.require(
        set(imported) == {r["run_id"] for r in core["run_findings"]}, "INCOMPLETE_BOOTSTRAP"
    )
    for finding in core["run_findings"]:
        proof = finding["normalized_facts_reference"]
        sql.require(sql.digest(imported[finding["run_id"]]) == proof["sha256"], "CONTRADICTORY_RUN")
        matches = [f for f in state["bootstrap_findings"] if f["run_id"] == finding["run_id"]]
        sql.require(
            sorted(f["source_id"] for f in matches) == finding["source_ids"], "INCOMPLETE_BOOTSTRAP"
        )
        sql.require(
            matches and any(f["is_primary_evidence"] for f in matches), "INCOMPLETE_BOOTSTRAP"
        )
        for row in matches:
            sql.require(
                _reference_row(state, row["finding_reference_id"]) == proof, "INCOMPLETE_BOOTSTRAP"
            )


def _verify_intent_payloads(state, reader):
    """Read canonical candidate bytes through an injected confined read-only adapter."""
    sql.require(reader is not None and callable(reader), "UNCERTAIN_PUBLICATION")
    head = len(state["history_revisions"]) - 1
    for intent in state["publication_intents"]:
        payload = reader(intent["candidate_payload_path"])
        sql.require(
            type(payload) is bytes
            and hashlib.sha256(payload).hexdigest() == intent["candidate_payload_sha256"],
            "DIGEST_MISMATCH",
        )
        projection = sql.parse_canonical(payload)
        sql.require(
            type(projection) is dict
            and set(projection) == set(sql.APPROVED_SCHEMA["logical_digest"]["fields"]),
            "SCHEMA",
        )
        revision = intent["candidate_revision"]
        sql.require(
            projection["schema_version"] == "s11-ex10-authority-logical-state-v1"
            and projection["revision"] == revision,
            "SCHEMA",
        )
        candidate = copy.deepcopy(state)
        for name in sql.APPROVED_SCHEMA["logical_digest"]["fields"]:
            if name in ("schema_version", "revision"):
                continue
            candidate[name] = (
                [projection[name]]
                if name in ("metadata", "bootstrap_manifest")
                else copy.deepcopy(projection[name])
            )
        sql.require(type(candidate["history_revisions"]) is list, "SCHEMA")
        for record in candidate["history_revisions"]:
            sql.require(
                type(record) is dict
                and set(record)
                == set(sql.TABLES["history_revisions"]["columns"]) - {"ledger_digest"},
                "SCHEMA",
            )
            number = record["revision"]
            matches = [r for r in state["history_revisions"] if r["revision"] == number]
            record["ledger_digest"] = (
                matches[0]["ledger_digest"] if matches else intent["candidate_ledger_digest"]
            )
        last = next((r for r in candidate["history_revisions"] if r["revision"] == revision), None)
        sql.require(
            last is not None
            and last["operation_id"] == intent["operation_id"]
            and last["previous_ledger_digest"] == intent["base_ledger_digest"],
            "FORK_DETECTED",
        )
        if revision <= head:
            sql.require(projection == sql.logical_projection(state, revision), "DIGEST_MISMATCH")
        else:
            sql.require(
                revision == head + 1
                and intent["base_revision"] == head
                and sql.logical_projection(candidate, head) == sql.logical_projection(state, head),
                "FORK_DETECTED",
            )
            dummy = copy.deepcopy(max(state["witness_checkpoints"], key=lambda r: r["revision"]))
            dummy.update(
                revision=revision,
                operation_id=intent["operation_id"],
                ledger_digest=intent["candidate_ledger_digest"],
                previous_witness_digest=dummy["witness_digest"],
                witness_digest="0" * 64,
                signature="0" * 128,
            )
            for field in dummy:
                if field.startswith("genesis_"):
                    dummy[field] = None
            candidate["witness_checkpoints"].append(dummy)
            sql.validate_state(candidate, check_publication=False)
        sql.require(
            sql.logical_digest(candidate, revision) == intent["candidate_ledger_digest"],
            "DIGEST_MISMATCH",
        )


def load_authority(
    descriptor_reference, external_trust_bundle, read_adapter, fresh_witness_observation
):
    """Read-only adapter supplies bytes/state; this function does not scan roots."""
    _reference(descriptor_reference)
    sql.require(
        external_trust_bundle is not None and fresh_witness_observation is not None,
        "TRUST_UNAPPROVED",
    )
    material = read_adapter(descriptor_reference)
    sql.require(
        type(material) is dict
        and set(material)
        == {"descriptor", "state", "bootstrap_core", "bootstrap_manifest", "descriptor_reference"},
        "SCHEMA",
    )
    sql.require(material["descriptor_reference"] == descriptor_reference, "REFERENCE_MISMATCH")
    return AuthoritySnapshot(sql.canonical_bytes(material))


def verify_authority(
    unverified_snapshot,
    external_approved_trust_state,
    fresh_witness_observation,
    profile_reference,
    session_reference,
    independently_authenticated_B4_publication_barrier_observation,
):
    """Offline full verification. External trust/freshness/barrier proofs are injected.

    No callback is supplied by a ledger. No B4 implementation is imported.
    The returned snapshot cannot authorize START and contains blocking facts.
    """
    sql.require(type(unverified_snapshot) is AuthoritySnapshot, "HISTORY_INVALID")
    material = unverified_snapshot.material
    sql.require(
        type(material) is dict
        and set(material)
        == {"descriptor", "state", "bootstrap_core", "bootstrap_manifest", "descriptor_reference"},
        "SCHEMA",
    )
    descriptor, state = material["descriptor"], material["state"]
    sql.validate_schema(descriptor, witness.SCHEMAS["authority_descriptor"])
    for key in ("bootstrap_manifest_reference", "g0_a3_approval_reference", "trust_root_reference"):
        _reference(descriptor[key])
    loaded_ref = material["descriptor_reference"]
    sql.require(
        profile_reference == session_reference == loaded_ref
        and loaded_ref["sha256"] == sql.digest(descriptor)
        and loaded_ref["schema_version"] == descriptor["schema_version"],
        "REFERENCE_MISMATCH",
    )
    latest = sql.validate_state(state)
    metadata = state["metadata"][0]
    sql.require(
        descriptor["authority_id"] == metadata["authority_id"]
        and descriptor["authority_epoch"] == metadata["epoch"]
        and descriptor["authority_schema_version"] == metadata["schema_version"]
        and descriptor["namespace"] == metadata["namespace"]
        and metadata["descriptor_sha256"] == loaded_ref["sha256"],
        "REFERENCE_MISMATCH",
    )
    trust = external_approved_trust_state
    sql.require(
        type(trust) is dict
        and set(trust)
        == {
            "pins",
            "floor",
            "genesis_digest",
            "certified_source_ids",
            "approval_verifier",
            "trust_root_reference",
            "trust_verifier",
            "intent_payload_reader",
        },
        "TRUST_UNAPPROVED",
    )
    sql.require(
        descriptor["trust_root_reference"] == trust["trust_root_reference"], "TRUST_UNAPPROVED"
    )
    _verify_trust_state(trust, descriptor)
    _verify_intent_payloads(state, trust["intent_payload_reader"])
    _reconcile_bootstrap(
        state, material["bootstrap_core"], material["bootstrap_manifest"], descriptor
    )
    _validate_sealed_bootstrap(
        material["bootstrap_core"],
        material["bootstrap_manifest"],
        certified_source_ids=trust["certified_source_ids"],
        approval_verifier=trust["approval_verifier"],
    )
    sql.require(
        sql.digest(material["bootstrap_manifest"])
        == descriptor["bootstrap_manifest_reference"]["sha256"]
        and _reference_row(state, metadata["bootstrap_reference_id"])
        == descriptor["bootstrap_manifest_reference"]
        and _reference_row(state, metadata["g0_a3_approval_reference_id"])
        == descriptor["g0_a3_approval_reference"],
        "DIGEST_MISMATCH",
    )
    sql.require(
        descriptor["source_baseline_head"]
        == metadata["source_baseline_head"]
        == material["bootstrap_core"]["baseline_head"]
        and descriptor["source_baseline_tree"]
        == metadata["source_baseline_tree"]
        == material["bootstrap_core"]["baseline_tree"],
        "REFERENCE_MISMATCH",
    )
    sql.require(
        material["bootstrap_core"]["authority_id"] == metadata["authority_id"]
        and material["bootstrap_core"]["authority_epoch"] == metadata["epoch"],
        "REFERENCE_MISMATCH",
    )
    receipts = _receipts(state)
    genesis_binding = {
        "bootstrap_manifest_sha256": descriptor["bootstrap_manifest_reference"]["sha256"],
        "g0_a3_approval_reference": descriptor["g0_a3_approval_reference"],
        "source_baseline_head": descriptor["source_baseline_head"],
        "source_baseline_tree": descriptor["source_baseline_tree"],
    }
    tip = witness.verify_chain(
        receipts,
        trust["pins"],
        expected_genesis_digest=trust["genesis_digest"],
        expected_genesis_binding=genesis_binding,
    )
    for cp, receipt in zip(
        sorted(state["witness_checkpoints"], key=lambda r: r["revision"]), receipts, strict=True
    ):
        sql.require(cp["witness_digest"] == sql.digest(receipt), "DIGEST_MISMATCH")
    observation = fresh_witness_observation
    sql.require(
        type(observation) is dict
        and set(observation)
        == {"head", "challenge", "now", "request_elapsed_seconds", "clock_healthy"},
        "EXPIRED_OBSERVATION",
    )
    head_record = observation["head"]
    witness._validate_message(head_record)
    pins = [
        p
        for p in trust["pins"]
        if p.record["key_id"] == head_record["key_id"]
        and p.record["key_epoch"] == head_record["key_epoch"]
    ]
    sql.require(len(pins) == 1, "TRUST_UNAPPROVED")
    sql.require(
        head_record["key_id"] == receipts[-1]["key_id"]
        and head_record["key_epoch"] == receipts[-1]["key_epoch"],
        "TRUST_UNAPPROVED",
    )
    witness._verify_current_head(
        head_record,
        pins[0],
        challenge=observation["challenge"],
        now=observation["now"],
        request_elapsed_seconds=observation["request_elapsed_seconds"],
        clock_healthy=observation["clock_healthy"],
    )
    revision = len(state["history_revisions"]) - 1
    witness._verify_floor(
        revision, sql.logical_digest(state, revision), tip, receipts, head_record, trust["floor"]
    )
    sql.require(revision >= descriptor["minimum_revision"], "ROLLBACK_DETECTED")
    endpoint = descriptor["witness_head_reference"]
    sql.require(
        endpoint["authority_id"] == metadata["authority_id"]
        and endpoint["authority_epoch"] == metadata["epoch"]
        and endpoint["namespace"] == metadata["namespace"],
        "REFERENCE_MISMATCH",
    )
    sql.require(
        any(
            p.record["transport_identity_reference"] == endpoint["transport_identity_reference"]
            for p in trust["pins"]
        ),
        "TRUST_UNAPPROVED",
    )
    barrier = independently_authenticated_B4_publication_barrier_observation
    sql.require(barrier is not None, "B4_BARRIER_UNVERIFIED")
    sql.require(
        barrier(metadata["authority_id"], metadata["epoch"], revision, tip) is None,
        "B4_BARRIER_UNVERIFIED",
    )
    resolutions = {r["operation_id"]: r for r in state["publication_resolutions"]}
    pending = any(i["operation_id"] not in resolutions for i in state["publication_intents"])
    closed = any(r["disposition"] == "MANUAL_CANCELLED" for r in resolutions.values())
    resolved_events = {
        e["resolves_event_sequence"]
        for e in state["authority_events"]
        if e["event_kind"] == "MANUAL_RESOLUTION"
    }
    pending |= any(
        e["event_kind"] == "AUTHORITY_UNCERTAIN" and e["event_sequence"] not in resolved_events
        for e in state["authority_events"]
    )
    del latest
    return _VerifiedSnapshot(
        sql.canonical_bytes(material),
        observation["now"],
        sql.canonical_bytes(head_record),
        tuple(p.canonical_record for p in trust["pins"]),
        head_record["expires_at"],
        pending,
        closed,
        _VERIFIED_TOKEN,
    )


def _verified(snapshot):
    sql.require(
        type(snapshot) is _VerifiedSnapshot and snapshot.token is _VERIFIED_TOKEN, "HISTORY_INVALID"
    )
    sql.require(sql.utc_time(_now()) < sql.utc_time(snapshot.expires_at), "EXPIRED_OBSERVATION")
    return snapshot.material


def current_revision(verified_snapshot):
    material = _verified(verified_snapshot)
    state = material["state"]
    revision = len(state["history_revisions"]) - 1
    current = next(r for r in state["history_revisions"] if r["revision"] == revision)
    metadata = state["metadata"][0]
    return {
        "authority_id": metadata["authority_id"],
        "epoch": metadata["epoch"],
        "revision": revision,
        "ledger_digest": current["ledger_digest"],
        "witness_digest": sql.digest(_receipts(state)[-1]),
        "verified_at": verified_snapshot.observed_at,
        "expires_at": verified_snapshot.expires_at,
    }


def list_official_runs(verified_snapshot):
    state = _verified(verified_snapshot)["state"]
    latest = {}
    for run in sorted(state["official_runs"], key=lambda row: row["run_version"]):
        latest[run["run_id"]] = run
    return tuple(
        sorted(latest.values(), key=lambda row: (row["introduced_revision"], row["run_id"]))
    )


def find_run(verified_snapshot, exact_run_id):
    sql.validate_scalar(exact_run_id, {"type": "run_id", "not_null": True})
    return next(
        (r for r in list_official_runs(verified_snapshot) if r["run_id"] == exact_run_id), None
    )


def active_or_uncertain_runs(verified_snapshot):
    runs = list_official_runs(verified_snapshot)
    return {
        "runs": tuple(r for r in runs if r["lifecycle_state"] in ("OPEN", "ACTIVE", "UNCERTAIN")),
        "authority_uncertainty": verified_snapshot.authority_uncertainty,
        "write_closed": verified_snapshot.write_closed,
    }


def first_valid_status(verified_snapshot):
    try:
        runs = list_official_runs(verified_snapshot)
        binding = [r for r in runs if r["binding"]]
        sql.require(len(binding) <= 1, "CONTRADICTORY_RUN")
        uncertain = [r["run_id"] for r in runs if r["lifecycle_state"] == "UNCERTAIN"]
        active = [r["run_id"] for r in runs if r["lifecycle_state"] in ("OPEN", "ACTIVE")]
        status = (
            "UNCERTAIN_BLOCKER"
            if uncertain
            or verified_snapshot.authority_uncertainty
            or verified_snapshot.write_closed
            else "ACTIVE_BLOCKER"
            if active
            else binding[0]["result"] + "_BINDING"
            if binding
            else "NONE"
        )
        current = current_revision(verified_snapshot)
        return {
            "status": status,
            "consumed": bool(binding),
            "first_binding_run_id": binding[0]["run_id"] if binding else None,
            "blocking_run_ids": sorted(uncertain + active),
            "reason_codes": ["WRITE_CLOSED"] if verified_snapshot.write_closed else [],
            "revision": current["revision"],
            "head_digest": current["witness_digest"],
        }
    except sql.AuthorityError as exc:
        return {
            "status": "HISTORY_INVALID",
            "consumed": None,
            "first_binding_run_id": None,
            "blocking_run_ids": [],
            "reason_codes": [exc.code],
            "revision": None,
            "head_digest": None,
        }


def verify_ack_target(verified_snapshot, exact_run_id):
    run = find_run(verified_snapshot, exact_run_id)
    recognizable = (
        run is not None
        and run["lifecycle_state"] == "TERMINAL"
        and run["result"] == "NOT_DEMONSTRATED"
        and run["binding"] is False
        and run["ack_required"] is True
    )
    return {
        "recognizable": recognizable,
        "run_id": exact_run_id,
        "result": None if run is None else run["result"],
        "binding": None if run is None else run["binding"],
        "ack_required": None if run is None else run["ack_required"],
        "evidence_reference": None
        if run is None
        else _reference_row(
            _verified(verified_snapshot)["state"], run["evidence_authority_reference_id"]
        ),
        "revision": current_revision(verified_snapshot)["revision"],
        "reason_codes": [] if recognizable else ["NOT_ACK_TARGET"],
    }


def _authorize(verifier, state, operation_id, revision, action, proof):
    sql.require(
        verifier is not None and hasattr(verifier, "verify") and proof is not None,
        "B4_CAPABILITY_REQUIRED",
    )
    metadata = state["metadata"][0]
    sql.require(
        verifier.verify(
            authority_id=metadata["authority_id"],
            epoch=metadata["epoch"],
            operation_id=operation_id,
            revision=revision,
            action=action,
            proof=proof,
            candidate_state=sql.canonical_bytes(state),
        )
        is None,
        "B4_CAPABILITY_REQUIRED",
    )


def _updated_snapshot(material, state, observation, approved_signer_pins):
    material = copy.deepcopy(material)
    material["state"] = state
    resolved = {
        e["resolves_event_sequence"]
        for e in state["authority_events"]
        if e["event_kind"] == "MANUAL_RESOLUTION"
    }
    uncertain = any(
        e["event_kind"] == "AUTHORITY_UNCERTAIN" and e["event_sequence"] not in resolved
        for e in state["authority_events"]
    )
    return _VerifiedSnapshot(
        sql.canonical_bytes(material),
        observation["now"],
        sql.canonical_bytes(observation["head"]),
        tuple(approved_signer_pins),
        observation["head"]["expires_at"],
        uncertain,
        False,
        _VERIFIED_TOKEN,
    )


def _publication(
    state,
    additions,
    *,
    operation_id,
    occurred_at,
    evidence_reference_id,
    action,
    verifier,
    capability,
    adapter,
    signer_identity,
    candidate_payload_path,
    genesis_material=None,
    approved_signer_pins=None,
):
    """Internal offline primitive. Durability is delegated to a trusted adapter.

    No default adapter exists. No locks/retries/coordination are implemented.
    The caller cannot qualify production use until future B4 is implemented.
    """
    if type(state) is _VerifiedSnapshot:
        sql.require(not state.write_closed, "UNCERTAIN_PUBLICATION")
        base_material = _verified(state)
        approved_signer_pins = state.canonical_trust_pins
        state = base_material["state"]
    else:
        base_material = genesis_material
        sql.require(state == sql.empty_state() and action == "GENESIS", "HISTORY_INVALID")
    sql.require(
        base_material is not None
        and approved_signer_pins is not None
        and signer_identity.canonical_record in approved_signer_pins,
        "TRUST_UNAPPROVED",
    )
    previous = (
        None
        if not state["history_revisions"]
        else max(state["history_revisions"], key=lambda r: r["revision"])
    )
    revision = 0 if previous is None else previous["revision"] + 1
    sql.require(revision <= sql.MAX_COUNTER, "REVISION_OVERFLOW")
    pin = signer_identity.record
    sql.require(
        pin["activation_revision"] <= revision
        and (pin["retirement_revision"] is None or revision <= pin["retirement_revision"]),
        "TRUST_UNAPPROVED",
    )
    if previous is not None:
        old_epoch = _receipts(state)[-1]["key_epoch"]
        sql.require(
            pin["key_epoch"] == old_epoch
            or (
                pin["key_epoch"] == old_epoch + 1
                and pin["activation_revision"] == revision
                and pin["key_transition_reference"] is not None
            ),
            "TRUST_UNAPPROVED",
        )
    candidate = copy.deepcopy(state)
    for name, rows in additions.items():
        sql.require(
            name in sql.TABLES
            and name
            not in (
                "history_revisions",
                "witness_checkpoints",
                "publication_intents",
                "publication_resolutions",
            ),
            "SCHEMA",
        )
        candidate[name].extend(copy.deepcopy(rows))
    _authorize(verifier, candidate, operation_id, revision, action, capability)
    sql.require(adapter is not None, "PUBLICATION_ADAPTER_REQUIRED")
    existing_resolutions = {r["operation_id"] for r in state["publication_resolutions"]}
    sql.require(
        not any(i["operation_id"] not in existing_resolutions for i in state["publication_intents"])
        and not any(
            r["disposition"] == "MANUAL_CANCELLED" for r in state["publication_resolutions"]
        ),
        "UNCERTAIN_PUBLICATION",
    )
    events = [e for e in candidate["authority_events"] if e["revision"] == revision]
    sql.require(events, "SCHEMA")
    record = {
        "revision": revision,
        "operation_id": operation_id,
        "previous_ledger_digest": None if previous is None else previous["ledger_digest"],
        "ledger_digest": "0" * 64,
        "recorded_at": occurred_at,
        "first_event_sequence": min(e["event_sequence"] for e in events),
        "last_event_sequence": max(e["event_sequence"] for e in events),
        "event_count": len(events),
    }
    candidate["history_revisions"].append(record)
    record["ledger_digest"] = sql.logical_digest(candidate, revision)
    metadata = candidate["metadata"][0]
    prev_receipt = None if previous is None else sql.digest(_receipts(state)[-1])
    genesis = (
        None
        if previous
        else {
            "bootstrap_manifest_sha256": _reference_row(
                candidate, metadata["bootstrap_reference_id"]
            )["sha256"],
            "g0_a3_approval_reference": _reference_row(
                candidate, metadata["g0_a3_approval_reference_id"]
            ),
            "source_baseline_head": metadata["source_baseline_head"],
            "source_baseline_tree": metadata["source_baseline_tree"],
        }
    )
    payload = witness.build_witness_payload(
        {
            "witness_schema_version": witness.RECEIPT_SCHEMA,
            "authority_id": metadata["authority_id"],
            "authority_epoch": metadata["epoch"],
            "namespace": metadata["namespace"],
            "operation_id": operation_id,
            "revision": revision,
            "ledger_digest": record["ledger_digest"],
            "previous_witness_digest": prev_receipt,
            "created_at": occurred_at,
            "key_id": signer_identity.record["key_id"],
            "key_epoch": signer_identity.record["key_epoch"],
            "key_fingerprint": signer_identity.record["fingerprint"],
            "genesis": genesis,
        }
    )
    intent = {
        "operation_id": operation_id,
        "base_revision": None if previous is None else previous["revision"],
        "candidate_revision": revision,
        "base_ledger_digest": None if previous is None else previous["ledger_digest"],
        "candidate_ledger_digest": record["ledger_digest"],
        "candidate_payload_path": candidate_payload_path,
        "candidate_payload_sha256": sql.digest(sql.logical_projection(candidate, revision)),
        "serialization_capability_digest": sql.digest(capability),
        "created_at": occurred_at,
    }
    sql.validate_row("publication_intents", intent)
    sql.require(intent["candidate_payload_sha256"] == record["ledger_digest"], "DIGEST_MISMATCH")
    # Validate the complete proposed state before any adapter can persist an intent.
    dummy = {**payload, "signature": "0" * 128}
    candidate["witness_checkpoints"].append(_checkpoint(dummy))
    sql.validate_state(candidate, check_publication=False)
    candidate["witness_checkpoints"].pop()
    adapter.durable_intent(intent, sql.canonical_bytes(sql.logical_projection(candidate, revision)))
    receipt = adapter.publish_witness(payload, prev_receipt)
    witness.verify_witness(receipt, signer_identity)
    sql.require({k: v for k, v in receipt.items() if k != "signature"} == payload, "FORK_DETECTED")
    candidate["witness_checkpoints"].append(_checkpoint(receipt))
    chain = _receipts(candidate)
    witness.verify_chain(
        chain,
        [witness.TrustPin(p) for p in approved_signer_pins],
        expected_genesis_digest=sql.digest(chain[0]),
        expected_genesis_binding=chain[0]["genesis"],
    )
    candidate["publication_intents"].append(intent)
    observation = adapter.observe_head(receipt)
    witness._verify_current_head(
        observation["head"],
        signer_identity,
        challenge=observation["challenge"],
        now=observation["now"],
        request_elapsed_seconds=observation["request_elapsed_seconds"],
        clock_healthy=observation["clock_healthy"],
    )
    observed_head = observation["head"]
    sql.require(
        observed_head["revision"] == revision
        and observed_head["ledger_digest"] == record["ledger_digest"]
        and observed_head["witness_digest"] == sql.digest(receipt),
        "FORK_DETECTED",
    )
    resolution = {
        "operation_id": operation_id,
        "disposition": "COMMITTED",
        "resolved_at": occurred_at,
        "committed_revision": revision,
        "receipt_digest": sql.digest(receipt),
        "operator_approval_path": None,
        "operator_approval_sha256": None,
        "operator_approval_schema": None,
        "fresh_witness_observation_digest": sql.digest(observation["head"]),
    }
    candidate["publication_resolutions"].append(resolution)
    sql.validate_state(candidate)
    _authorize(verifier, candidate, operation_id, revision, action, capability)
    changes = {name: candidate[name][len(state[name]) :] for name in sql.TABLES}
    adapter.atomic_commit(changes)
    return _updated_snapshot(base_material, candidate, observation, approved_signer_pins)


def _bootstrap_authority(
    state,
    additions,
    *,
    core,
    sealed,
    descriptor,
    descriptor_reference,
    trust,
    certified_source_ids,
    approval_verifier,
    **publication,
):
    """INTERNAL ONLY: approved offline genesis; future B4 capability mandatory."""
    sql.require(state == sql.empty_state(), "ILLEGAL_TRANSITION")
    _validate_sealed_bootstrap(
        core, sealed, certified_source_ids=certified_source_ids, approval_verifier=approval_verifier
    )
    proposed = copy.deepcopy(state)
    for name, rows in additions.items():
        proposed[name].extend(copy.deepcopy(rows))
    sql.validate_schema(descriptor, witness.SCHEMAS["authority_descriptor"])
    sql.require(
        descriptor_reference["sha256"] == sql.digest(descriptor)
        and descriptor_reference["schema_version"] == descriptor["schema_version"],
        "REFERENCE_MISMATCH",
    )
    _verify_trust_state(trust, descriptor)
    _reconcile_bootstrap(proposed, core, sealed, descriptor)
    sql.require(
        proposed["metadata"][0]["descriptor_sha256"] == descriptor_reference["sha256"],
        "REFERENCE_MISMATCH",
    )
    material = {
        "descriptor": descriptor,
        "descriptor_reference": descriptor_reference,
        "bootstrap_core": core,
        "bootstrap_manifest": sealed,
        "state": proposed,
    }
    return _publication(
        state,
        additions,
        action="GENESIS",
        genesis_material=material,
        approved_signer_pins=tuple(p.canonical_record for p in trust["pins"]),
        **publication,
    )


def _governance_open_bindings(material, body, run, event, publication, base_head):
    state = material["state"]
    meta = state["metadata"][0]
    before = body["authority_revision_before"]
    receipts = _receipts(state)
    sql.require(0 <= before < len(receipts), "GOV_REVISION")
    receipt = receipts[before]
    revision = next(r for r in state["history_revisions"] if r["revision"] == before)
    base = body["b3_base_witness"]
    sql.require(
        (body["authority_id"], body["authority_epoch"], body["namespace"])
        == (meta["authority_id"], meta["epoch"], meta["namespace"])
        and body["authority_descriptor_reference"] == material["descriptor_reference"]
        and base["ledger_digest"] == revision["ledger_digest"]
        and base["witness_digest"] == sql.digest(receipt),
        "GOV_AUTHORITY",
    )
    if base_head is not None:
        sql.require(
            body["b3_base_current_head_reference"]["sha256"]
            == hashlib.sha256(base_head).hexdigest(),
            "GOV_WITNESS",
        )
    sql.require(
        all(body[k] == run[k] for k in ("run_id", "source_revision", "protocol", "methodology"))
        and run["mode"] == "OFFICIAL"
        and run["introduced_revision"] == body["authority_revision_after"]
        and event["revision"] == body["authority_revision_after"]
        and event["run_id"] == run["run_id"]
        and event["run_version"] == 0
        and event["occurred_at"] == publication["occurred_at"] == body["issued_at"]
        and run["opened_at"] <= body["issued_at"]
        and publication["operation_id"] == body["commit_request_id"],
        "GOV_BINDING",
    )
    latest = {}
    for r in sorted(state["official_runs"], key=lambda r: r["run_version"]):
        if r["introduced_revision"] <= before:
            latest[r["run_id"]] = r
    expected = [
        {
            "run_id": r["run_id"],
            "authority_revision": before,
            "terminal_evidence_reference": _reference_row(
                state, r["evidence_authority_reference_id"]
            ),
        }
        for r in sorted(latest.values(), key=lambda r: r["run_id"])
        if r["lifecycle_state"] == "TERMINAL"
        and r["result"] == "NOT_DEMONSTRATED"
        and r["binding"] is False
        and r["ack_required"] is True
    ]
    sql.require(body["acknowledgments"] == expected, "GOV_ACK")


def _append_run_opened(
    state,
    run,
    event,
    *,
    governance_certificate,
    governance_certificate_reference,
    reference_additions,
    **publication,
):
    """INTERNAL ONLY: explicit closed binding, existing trusted publication only.

    No external artifact reads. Global reservation uniqueness/namespace custody
    remains the approved B4 caller's responsibility, not inferred from refs.
    """
    material = _verified(state)
    sql.require(type(governance_certificate) is governance.GovernanceCertificateV1, "GOV_SCHEMA")
    cert = governance.validate_governance_certificate(governance_certificate)
    body = cert.value
    ref = governance.validate_governance_reference(governance_certificate_reference, cert)
    sql.validate_row("official_runs", run)
    sql.validate_row("authority_events", event)
    run, event = copy.deepcopy(run), copy.deepcopy(event)
    sql.require(
        run["run_version"] == 0
        and run["lifecycle_state"] == "OPEN"
        and run["provenance_model"] == "PROFILE_V2"
        and event["event_kind"] == "RUN_OPENED",
        "ILLEGAL_TRANSITION",
    )
    sql.require(
        run["evidence_authority_reference_id"]
        == event["evidence_reference_id"]
        == publication["evidence_reference_id"]
        == ref["reference_id"],
        "GOV_REFERENCE",
    )
    sql.require(
        type(reference_additions) is tuple and len(reference_additions) <= 2, "GOV_REFERENCE"
    )
    additions = copy.deepcopy(reference_additions)
    stored = material["state"]
    rows = {r["reference_id"]: r for r in stored["artifact_references"]}
    bindings = {
        run["profile_reference_id"]: (body["runtime_profile"]["reference"], "PROFILE"),
        run["session_reference_id"]: (body["session"]["header_reference"], "SESSION"),
    }
    sql.require(
        len(bindings) == 2 and None not in bindings and ref["reference_id"] not in bindings,
        "GOV_REFERENCE",
    )
    supplied = {}
    for row in additions:
        sql.validate_row("artifact_references", row)
        rid = row["reference_id"]
        sql.require(
            rid in bindings
            and rid not in supplied
            and row["introduced_revision"] == body["authority_revision_after"],
            "GOV_REFERENCE",
        )
        supplied[rid] = row
    duplicate = any(r["run_id"] == run["run_id"] for r in stored["official_runs"])
    for rid, (claim, kind) in bindings.items():
        effective = supplied.get(rid, rows.get(rid))
        sql.require(effective is not None, "GOV_REFERENCE")
        sql.require(
            {k: effective[k] for k in ("path", "sha256", "schema_version")} == claim
            and effective["kind"] == kind,
            "GOV_REFERENCE",
        )
        if rid in rows and rid in supplied:
            sql.require(duplicate and rows[rid] == supplied[rid], "GOV_REFERENCE")
    for old in stored["artifact_references"]:
        if old["reference_id"] == ref["reference_id"] or (
            old["kind"] == "GOVERNANCE" and old["path"] == ref["path"]
        ):
            sql.require(duplicate and old == ref, "GOV_REWRITE")
    _governance_open_bindings(
        material, body, run, event, publication, None if duplicate else state.canonical_current_head
    )
    if duplicate:
        sql.require(
            run in stored["official_runs"]
            and event in stored["authority_events"]
            and rows.get(ref["reference_id"]) == ref,
            "GOV_REWRITE",
        )
        committed = next(
            (
                r
                for r in stored["history_revisions"]
                if r["revision"] == body["authority_revision_after"]
            ),
            None,
        )
        sql.require(
            committed is not None and committed["operation_id"] == body["commit_request_id"],
            "GOV_REWRITE",
        )
        introduced = {
            r["reference_id"]
            for r in stored["artifact_references"]
            if r["introduced_revision"] == body["authority_revision_after"]
            and r["reference_id"] in bindings
        }
        sql.require(introduced == supplied.keys(), "GOV_REWRITE")
        return state
    sql.require(not state.write_closed and not state.authority_uncertainty, "UNCERTAIN_PUBLICATION")
    sql.require(
        body["authority_revision_before"] == current_revision(state)["revision"], "GOV_REVISION"
    )
    sql.require(first_valid_status(state)["status"] == "NONE", "UNCERTAIN_PUBLICATION")
    return _publication(
        state,
        {
            "artifact_references": [ref, *additions],
            "official_runs": [run],
            "authority_events": [event],
        },
        action="RUN_OPENED",
        **publication,
    )


def _append_run_state(state, run, event, **publication):
    """INTERNAL ONLY; no coordination or safety claim."""
    sql.require(
        run["lifecycle_state"] == "ACTIVE" and event["event_kind"] == "RUN_STATE",
        "ILLEGAL_TRANSITION",
    )
    return _publication(
        state,
        {"official_runs": [run], "authority_events": [event]},
        action="RUN_STATE",
        **publication,
    )


def _append_run_terminal(state, run, event, **publication):
    """INTERNAL ONLY; one evidence-backed terminal version, no experiment finalize."""
    sql.require(
        run["lifecycle_state"] == "TERMINAL" and event["event_kind"] == "RUN_TERMINAL",
        "ILLEGAL_TRANSITION",
    )
    return _publication(
        state,
        {"official_runs": [run], "authority_events": [event]},
        action="RUN_TERMINAL",
        **publication,
    )


def _append_uncertain_state(state, run, event, **publication):
    """INTERNAL ONLY; uncertainty persists and never expires automatically."""
    sql.require(
        event["event_kind"] in ("RUN_UNCERTAIN", "AUTHORITY_UNCERTAIN"), "ILLEGAL_TRANSITION"
    )
    sql.require(run is None or run["lifecycle_state"] == "UNCERTAIN", "ILLEGAL_TRANSITION")
    changes = {"authority_events": [event], "official_runs": [] if run is None else [run]}
    return _publication(state, changes, action="UNCERTAINTY", **publication)


def _append_manual_resolution(
    state, additions, *, operator_approval_reference, resolution_verifier, **publication
):
    """INTERNAL ONLY; no automatic repair, retry or terminal rewrite."""
    _reference(operator_approval_reference)
    sql.require(resolution_verifier is not None, "TRUST_UNAPPROVED")
    sql.require(type(state) is _VerifiedSnapshot, "HISTORY_INVALID")
    sql.require(
        resolution_verifier(state, additions, operator_approval_reference) is None,
        "TRUST_UNAPPROVED",
    )
    cancellation = additions.get("publication_resolutions", [])
    if cancellation:
        material = _verified(state)
        base = material["state"]
        sql.require(
            set(additions) == {"publication_resolutions"} and len(cancellation) == 1,
            "ILLEGAL_TRANSITION",
        )
        resolution = cancellation[0]
        sql.require(resolution["disposition"] == "MANUAL_CANCELLED", "ILLEGAL_TRANSITION")
        approval = {
            "path": resolution["operator_approval_path"],
            "sha256": resolution["operator_approval_sha256"],
            "schema_version": resolution["operator_approval_schema"],
        }
        sql.require(approval == operator_approval_reference, "TRUST_UNAPPROVED")
        _authorize(
            publication["verifier"],
            base,
            publication["operation_id"],
            len(base["history_revisions"]) - 1,
            "MANUAL_CANCELLED",
            publication["capability"],
        )
        candidate = copy.deepcopy(base)
        candidate["publication_resolutions"].append(copy.deepcopy(resolution))
        sql.validate_state(candidate)
        sql.require(
            resolution["fresh_witness_observation_digest"]
            == sql.digest(sql.parse_canonical(state.canonical_current_head)),
            "TRUST_UNAPPROVED",
        )
        publication["adapter"].atomic_commit(additions)
        material["state"] = candidate
        resolved = {
            e["resolves_event_sequence"]
            for e in candidate["authority_events"]
            if e["event_kind"] == "MANUAL_RESOLUTION"
        }
        authority_uncertainty = any(
            e["event_kind"] == "AUTHORITY_UNCERTAIN" and e["event_sequence"] not in resolved
            for e in candidate["authority_events"]
        )
        return _VerifiedSnapshot(
            sql.canonical_bytes(material),
            state.observed_at,
            state.canonical_current_head,
            state.canonical_trust_pins,
            state.expires_at,
            authority_uncertainty,
            True,
            _VERIFIED_TOKEN,
        )
    sql.require(
        any(e["event_kind"] == "MANUAL_RESOLUTION" for e in additions.get("authority_events", [])),
        "ILLEGAL_TRANSITION",
    )
    return _publication(state, additions, action="MANUAL_RESOLUTION", **publication)


def _validate_restore_candidate(
    snapshot,
    trust,
    observation,
    profile_reference,
    session_reference,
    barrier,
    operator_authorization_reference,
    authorization_verifier,
):
    """Offline validation only; never replace, repair or restore a DB."""
    _reference(operator_authorization_reference)
    sql.require(authorization_verifier is not None, "TRUST_UNAPPROVED")
    sql.require(
        authorization_verifier(snapshot, operator_authorization_reference) is None,
        "TRUST_UNAPPROVED",
    )
    return verify_authority(
        snapshot, trust, observation, profile_reference, session_reference, barrier
    )
