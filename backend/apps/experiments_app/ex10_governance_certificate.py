"""Closed offline Governance Certificate codec. No B4 or artifact I/O.

Values here are immutable binding claims, never execution authority. External
signatures, custody, reservation uniqueness and path qualification belong to
an approved caller; this module only checks supplied bytes/claims/metadata.
"""

from __future__ import annotations

import copy
import hashlib
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from . import ex10_authority_sqlite as sql

SCHEMA_VERSION = "s11-ex10-governance-certificate-v1"
ID_DOMAIN = b"S11-EX10-GOVERNANCE-ID-v1\n"
MAX_BYTES = 262144
_CERTIFICATE_SCHEMA_JSON = r"""{"$id":"urn:s11:ex10:governance-certificate:v1","$schema":"https://json-schema.org/draft/2020-12/schema","additionalProperties":false,"design_status":"DESIGN_ONLY_NO_INSTANCES","properties":{"acknowledgment_digest":{"maxLength":64,"minLength":1,"pattern":"^[0-9a-f]{64}$","type":"string"},"acknowledgments":{"items":{"additionalProperties":false,"properties":{"authority_revision":{"maximum":9007199254740991,"minimum":0,"type":"integer"},"run_id":{"maxLength":40,"minLength":1,"pattern":"^s11-[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$","type":"string"},"terminal_evidence_reference":{"additionalProperties":false,"properties":{"path":{"maxLength":4096,"minLength":1,"type":"string"},"schema_version":{"maxLength":128,"minLength":1,"pattern":"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$","type":"string"},"sha256":{"maxLength":64,"minLength":1,"pattern":"^[0-9a-f]{64}$","type":"string"}},"required":["path","sha256","schema_version"],"type":"object"}},"required":["run_id","authority_revision","terminal_evidence_reference"],"type":"object"},"maxItems":10000,"type":"array","uniqueItems":true},"authority_descriptor_reference":{"additionalProperties":false,"properties":{"path":{"maxLength":4096,"minLength":1,"type":"string"},"schema_version":{"const":"s11-ex10-historical-authority-descriptor-v1","type":"string"},"sha256":{"maxLength":64,"minLength":1,"pattern":"^[0-9a-f]{64}$","type":"string"}},"required":["path","sha256","schema_version"],"type":"object"},"authority_epoch":{"maximum":9007199254740991,"minimum":1,"type":"integer"},"authority_id":{"maxLength":36,"minLength":1,"pattern":"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$","type":"string"},"authority_revision_after":{"maximum":9007199254740991,"minimum":1,"type":"integer"},"authority_revision_before":{"maximum":9007199254740991,"minimum":0,"type":"integer"},"b3_base_current_head_reference":{"additionalProperties":false,"properties":{"path":{"maxLength":4096,"minLength":1,"type":"string"},"schema_version":{"const":"s11-ex10-authority-witness-head-v1","type":"string"},"sha256":{"maxLength":64,"minLength":1,"pattern":"^[0-9a-f]{64}$","type":"string"}},"required":["path","sha256","schema_version"],"type":"object"},"b3_base_witness":{"additionalProperties":false,"properties":{"authority_epoch":{"maximum":9007199254740991,"minimum":1,"type":"integer"},"authority_id":{"maxLength":36,"minLength":1,"pattern":"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$","type":"string"},"ledger_digest":{"maxLength":64,"minLength":1,"pattern":"^[0-9a-f]{64}$","type":"string"},"receipt_reference":{"additionalProperties":false,"properties":{"path":{"maxLength":4096,"minLength":1,"type":"string"},"schema_version":{"const":"s11-ex10-authority-witness-receipt-v1","type":"string"},"sha256":{"maxLength":64,"minLength":1,"pattern":"^[0-9a-f]{64}$","type":"string"}},"required":["path","sha256","schema_version"],"type":"object"},"revision":{"maximum":9007199254740991,"minimum":0,"type":"integer"},"witness_digest":{"maxLength":64,"minLength":1,"pattern":"^[0-9a-f]{64}$","type":"string"}},"required":["authority_id","authority_epoch","revision","ledger_digest","witness_digest","receipt_reference"],"type":"object"},"certificate_id":{"maxLength":64,"minLength":1,"pattern":"^[0-9a-f]{64}$","type":"string"},"commit_request_digest":{"maxLength":64,"minLength":1,"pattern":"^[0-9a-f]{64}$","type":"string"},"commit_request_id":{"maxLength":36,"minLength":1,"pattern":"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$","type":"string"},"first_valid_status":{"const":"NONE","type":"string"},"issued_at":{"maxLength":27,"minLength":1,"pattern":"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\\.[0-9]{6}Z$","type":"string"},"issuer":{"const":"HOST_GOVERNANCE_AUTHORITY","type":"string"},"kind":{"const":"GOVERNANCE_CERTIFICATE","type":"string"},"methodology":{"maxLength":128,"minLength":1,"pattern":"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$","type":"string"},"namespace":{"const":"S11C3-EX10","type":"string"},"open_evidence_reference":{"additionalProperties":false,"properties":{"path":{"maxLength":4096,"minLength":1,"type":"string"},"schema_version":{"const":"s11-ex10-b4-open-evidence-v1","type":"string"},"sha256":{"maxLength":64,"minLength":1,"pattern":"^[0-9a-f]{64}$","type":"string"}},"required":["path","sha256","schema_version"],"type":"object"},"open_one_permit":{"additionalProperties":false,"properties":{"permit_digest":{"maxLength":64,"minLength":1,"pattern":"^[0-9a-f]{64}$","type":"string"},"permit_id":{"maxLength":36,"minLength":1,"pattern":"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$","type":"string"},"reference":{"additionalProperties":false,"properties":{"path":{"maxLength":4096,"minLength":1,"type":"string"},"schema_version":{"const":"s11-ex10-b4-open-one-permit-v1","type":"string"},"sha256":{"maxLength":64,"minLength":1,"pattern":"^[0-9a-f]{64}$","type":"string"}},"required":["path","sha256","schema_version"],"type":"object"}},"required":["permit_id","permit_digest","reference"],"type":"object"},"project":{"maxLength":63,"minLength":1,"pattern":"^[a-z0-9][a-z0-9_-]{0,62}$","type":"string"},"protocol":{"maxLength":128,"minLength":1,"pattern":"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$","type":"string"},"reservation":{"additionalProperties":false,"properties":{"fencing_token":{"maximum":9007199254740991,"minimum":1,"type":"integer"},"record_reference":{"additionalProperties":false,"properties":{"path":{"maxLength":4096,"minLength":1,"type":"string"},"schema_version":{"const":"s11-ex10-b4-reservation-v1","type":"string"},"sha256":{"maxLength":64,"minLength":1,"pattern":"^[0-9a-f]{64}$","type":"string"}},"required":["path","sha256","schema_version"],"type":"object"},"record_sequence":{"maximum":9007199254740991,"minimum":1,"type":"integer"},"reservation_digest":{"maxLength":64,"minLength":1,"pattern":"^[0-9a-f]{64}$","type":"string"},"reservation_id":{"maxLength":36,"minLength":1,"pattern":"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$","type":"string"},"state_at_open":{"const":"PENDING","type":"string"},"witness_receipt_reference":{"additionalProperties":false,"properties":{"path":{"maxLength":4096,"minLength":1,"type":"string"},"schema_version":{"const":"s11-ex10-b4-reservation-receipt-v1","type":"string"},"sha256":{"maxLength":64,"minLength":1,"pattern":"^[0-9a-f]{64}$","type":"string"}},"required":["path","sha256","schema_version"],"type":"object"},"witness_sequence":{"maximum":9007199254740991,"minimum":1,"type":"integer"}},"required":["reservation_id","reservation_digest","record_reference","record_sequence","fencing_token","state_at_open","witness_receipt_reference","witness_sequence"],"type":"object"},"reserve_request_digest":{"maxLength":64,"minLength":1,"pattern":"^[0-9a-f]{64}$","type":"string"},"reserve_request_id":{"maxLength":36,"minLength":1,"pattern":"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$","type":"string"},"run_id":{"maxLength":40,"minLength":1,"pattern":"^s11-[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$","type":"string"},"run_identity_digest":{"maxLength":64,"minLength":1,"pattern":"^[0-9a-f]{64}$","type":"string"},"runtime_profile":{"additionalProperties":false,"properties":{"profile_id":{"maxLength":128,"minLength":1,"pattern":"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$","type":"string"},"reference":{"additionalProperties":false,"properties":{"path":{"maxLength":4096,"minLength":1,"type":"string"},"schema_version":{"const":"s11-ex10-runtime-profile-v1","type":"string"},"sha256":{"maxLength":64,"minLength":1,"pattern":"^[0-9a-f]{64}$","type":"string"}},"required":["path","sha256","schema_version"],"type":"object"},"schema_version":{"const":"s11-ex10-runtime-profile-v1","type":"string"},"sha256":{"maxLength":64,"minLength":1,"pattern":"^[0-9a-f]{64}$","type":"string"}},"required":["profile_id","schema_version","sha256","reference"],"type":"object"},"schema_version":{"const":"s11-ex10-governance-certificate-v1","type":"string"},"session":{"additionalProperties":false,"properties":{"header_reference":{"additionalProperties":false,"properties":{"path":{"maxLength":4096,"minLength":1,"type":"string"},"schema_version":{"const":"s11-ex10-session-v2-header-projection-v1","type":"string"},"sha256":{"maxLength":64,"minLength":1,"pattern":"^[0-9a-f]{64}$","type":"string"}},"required":["path","sha256","schema_version"],"type":"object"},"header_sha256":{"maxLength":64,"minLength":1,"pattern":"^[0-9a-f]{64}$","type":"string"},"schema_version":{"const":"s11c3-ex10-official-launcher-session-v2","type":"string"},"session_id":{"maxLength":128,"minLength":1,"pattern":"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$","type":"string"}},"required":["session_id","schema_version","header_sha256","header_reference"],"type":"object"},"source_revision":{"maxLength":40,"minLength":1,"pattern":"^[0-9a-f]{40}$","type":"string"}},"required":["schema_version","kind","certificate_id","issued_at","issuer","namespace","authority_id","authority_epoch","authority_descriptor_reference","authority_revision_before","authority_revision_after","b3_base_witness","b3_base_current_head_reference","reservation","session","runtime_profile","project","source_revision","protocol","methodology","run_id","run_identity_digest","open_evidence_reference","acknowledgments","acknowledgment_digest","first_valid_status","reserve_request_id","reserve_request_digest","commit_request_id","commit_request_digest","open_one_permit"],"title":"s11-ex10-governance-certificate-v1","type":"object","x-canonicalization":{"acknowledgment_digest":"SHA256 of canonical exact sorted acknowledgments array with trailing LF; equals reservation and OPEN_ONE acknowledgment_digest","arrays":"acknowledgments sorted lexicographically by run_id; unique run_id; exact complete accepted ND set at base revision. Empty set encoded [], never null or omitted.","certificate_sha256":"SHA256 of ALL canonical certificate bytes including certificate_id and every field; hash kept exclusively in reference row/metadata, not inside certificate","encoding":"UTF-8 without BOM; strings already NFC; reject surrogate codepoints and non-NFC input, never silently normalize","identity_preimage":{"canonical_object_fields":["namespace","authority_id","authority_epoch","reservation_id"],"domain_prefix_utf8":"S11-EX10-GOVERNANCE-ID-v1\n","id":"Lowercase SHA256 hex of domain prefix concatenated with canonical identity object including trailing LF; no own ID or other certificate field in identity preimage","source":"namespace/authority_id/epoch from certificate; reservation_id from certificate.reservation"},"max_certificate_bytes":262144,"no_future_self_reference":"No new B3 OPEN receipt/head/digest, B4 COMMITTED receipt, certificate reference or own SHA inside body. Base B3 head and B4 RESERVE receipts already exist. Current commit-open body contains evidence, not certificate.","serialization":"JSON sort_keys=true, ensure_ascii=false, allow_nan=false, separators comma/colon without spaces; exactly one trailing LF","types":"Closed objects; all required; no defaults; reject duplicate keys, unknown fields, floats, NaN/Infinity and bool-as-integer; integers <=9007199254740991; validate Gregorian UTC six decimal places/Z beyond regex"},"x-semantic-validation":[{"category":"schema","id":"V01","rule":"All 31 root fields and every closed nested field required; no missing/default/null/unknown/duplicate-key acceptance; max262144bytes; raw input canonical exactly"},{"category":"identifier","id":"V02","rule":"ID/UUID/run_id/hash/project patterns exact. New run UUID5 RFC variant; no retroactive tightening on imported B3 history. Existing references use approved normalized POSIX or approved HTTPS object locators per B3, no traversal/NUL/userinfo/query/fragment. Certificate row is host-local absolute POSIX, qualified no-symlink/registry namespace; refs never confer read/network authority"},{"category":"sha256","id":"V03","rule":"Lowercase hashes64; recompute certificate identity, complete file SHA and row reference_id separately; never hash parsed normalization of noncanonical bytes"},{"category":"revision","id":"V04","rule":"after=before+1 within safe counters; B3 metadata authority_id/epoch/namespace and fresh verified base revision/digest/tip match; publication operation_id=commit_request_id and revision=after"},{"category":"session-binding","id":"V05","rule":"B2 immutable HEADER projection verified using all27 existing HEADER_KEYS; ref SHA=header_sha256; embedded header schema is SESSION_V2_SCHEMA, reference schema is explicit header-projection type. Compare session_id/profile/source/project/authority reference to live approved session; dynamic history/footer separate B2 validation"},{"category":"profile-binding","id":"V06","rule":"Whole canonical B1 profile SHA equals profile.sha256/reference.sha256 and B2 runtime_profile_sha256; IDs/schema match. Profile project/source/authority/deployment/roots match approved session/reserve/permit"},{"category":"reservation-binding","id":"V07","rule":"Immutable record snapshot PENDING at seal; exact record_sequence/full canonical ref SHA verified, recompute intent reservation_digest; record ID/token/run/identity/request/base/profile/header/project/ACK equal certificate, signed RESERVE receipt state=PENDING/transition=RESERVE/intent/base/seq/token match"},{"category":"authority-binding","id":"V08","rule":"Descriptor approval and external pins/floors already approved; base_witness ID/epoch/revision exact, receipt file full SHA=witness_digest and signed ledger digest verified; current-head reference same base and receipt, challenge freshness and clock checked at issue/publication, never now-expiry invalidation of historical proof"},{"category":"evidence-binding","id":"V09","rule":"Verify OPEN_EVIDENCE whole canonical file SHA and executor PoP against approved session process; receipt ID/reservation/token/profile/header/run/identity/permit/reserve request exact; verify only referenced immutable initial manifest/start/preflight/plan in confined run root, no recursive store hash"},{"category":"ack-binding","id":"V10","rule":"Accepted ACK set precisely B3 list+verify_ack_target at base; each ND/binding-false/existing target and terminal evidence exact, every ACK authority_revision=before; canonical array digest equals reserve/permit; no global consumed ACK field"},{"category":"request-binding","id":"V11","rule":"reserve_request_id/digest equal original accepted reserve request/claim; commit_request_id/digest equal exact already received authenticated commit-open request with evidence; IDs and nonce claims distinct, session/profile/source/authority/base same; SHA preimage exactly existing B4 request rule, no certificate in either body"},{"category":"timestamp","id":"V12","rule":"Certificate issued after existing evidence opened_at, within healthy B4 critical context and current-head/request/permit acceptance deadlines. B3 event/publication occurred_at=issued_at; run.created_at=frozen identity started_at, run.opened_at=evidence opened_at, nondecreasing timestamps"},{"category":"canonicalization","id":"V13","rule":"Reject all equivalent noncanonical encodings; arrays canonical order; fullSHA includes every field. Evidence receipt_digest/permit_digest intentionally have their own B4 preimages; ref.sha256 hashes complete stored bytes, never confuse these hashes"},{"category":"permit-binding","id":"V14","rule":"Verify approved host OPEN_ONE signature/holder/live exchange externally in B4; certificate stores only permit ID/digest/ref, no token/key/signature. Permit run/identity/reservation/receipt/token/request/ACK/profile/header/source/project/authority base exact"},{"category":"first-valid-binding","id":"V15","rule":"first_valid_status constant NONE commits host decision at referenced complete authenticated base history. Certificate alone does not prove query; B4 performs B3 query+blocker+ACK checks under coordinator. Future B3 same verified first_valid gate remains"},{"category":"reference-binding","id":"V16","rule":"B3 row kind exactly GOVERNANCE/schema certificateV1/SHA whole canonical cert; row reference_id existing SHA of canonical path,sha256,schema_version,kind; introduced_revision=after. Run v0 and RUN_OPENED event both point to this same row"},{"category":"immutability","id":"V17","rule":"One approved canonical host artifact path per authority/epoch/certificate_id; create-exclusive, no alias/rebinding/overwrite. B4 registry/index and independent permit reject reservation reuse. B3 rejects same run changed cert/ref and same stored path changed row; B3 without prior cert bodies cannot alone prove global reservation uniqueness"},{"category":"authorization","id":"V18","rule":"Issuer literal is descriptive, not self-approval. Only host with existing policy/session/source/writer/witness qualifications may construct and authorize publication. Cert has no own signing key, grants no START/execute and cannot bypass B3 verifier/capability/publication adapter"}]}"""  # noqa: E501
_REFERENCE_SCHEMA_JSON = r"""{"$id":"urn:s11:ex10:governance-certificate-reference:v1","$schema":"https://json-schema.org/draft/2020-12/schema","additionalProperties":false,"design_status":"DESIGN_ONLY","properties":{"introduced_revision":{"maximum":9007199254740991,"minimum":1,"type":"integer"},"kind":{"const":"GOVERNANCE","type":"string"},"path":{"maxLength":4096,"minLength":1,"type":"string"},"reference_id":{"maxLength":64,"minLength":1,"pattern":"^[0-9a-f]{64}$","type":"string"},"schema_version":{"const":"s11-ex10-governance-certificate-v1","type":"string"},"sha256":{"maxLength":64,"minLength":1,"pattern":"^[0-9a-f]{64}$","type":"string"}},"required":["reference_id","path","sha256","schema_version","kind","introduced_revision"],"title":"Existing B3 GOVERNANCE artifact reference row","type":"object","x-semantic-validation":["reference_id = existing B3 SHA256(canonical {path,sha256,schema_version,kind}); exact whole cert canonical SHA, including certificate_id","introduced_revision equals certificate.authority_revision_after; one same row bound by runv0.evidence_authority_reference_id and ROOTevent.evidence_reference_id","Absolute normalized approved POSIX host path, no NUL/relative/duplicate separators/dotdot/traversal/userinfo/query; registered authority/epoch artifact namespace, stable no-symlink/inode/owner/nlink identity; path basename certificate_id + .json","Different path aliases or same certificate_id bytes substitutions forbidden by B4 trusted namespace registry; B3 same run/ref and same stored path changed row rejected; row alone not proof of permission","No complete certificate blob in row; explicit closed certificate body supplied to B3 validator and external file read/hash done by qualified B4 host"]}"""  # noqa: E501


def certificate_schema():
    """Return a fresh exact copy of the frozen schema, never shared mutable rules."""
    return sql.json.loads(_CERTIFICATE_SCHEMA_JSON)


def reference_schema():
    return sql.json.loads(_REFERENCE_SCHEMA_JSON)


def _shape(value, schema):
    types = {"object": dict, "array": list, "string": str, "integer": int}
    kind = schema["type"]
    sql.require(type(value) is types[kind], "GOV_SCHEMA")
    if "const" in schema:
        sql.require(type(value) is type(schema["const"]) and value == schema["const"], "GOV_SCHEMA")
    if kind == "object":
        sql.require(value.keys() == schema["properties"].keys(), "GOV_SCHEMA")
        for key, child in value.items():
            _shape(child, schema["properties"][key])
        if value.keys() == {"path", "sha256", "schema_version"}:
            sql.validate_scalar(value["path"], {"type": "locator", "not_null": True})
            sql.require(not any(ord(c) < 32 for c in value["path"]), "GOV_PATH")
    elif kind == "array":
        sql.require(len(value) <= schema.get("maxItems", sql.MAX_COUNTER), "GOV_SCHEMA")
        for child in value:
            _shape(child, schema["items"])
        if schema.get("uniqueItems"):
            sql.require(len({sql.canonical_bytes(x) for x in value}) == len(value), "GOV_SCHEMA")
    elif kind == "string":
        sql.require(
            schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", MAX_BYTES),
            "GOV_SCHEMA",
        )
        if "pattern" in schema:
            sql.require(re.fullmatch(schema["pattern"], value) is not None, "GOV_SCHEMA")
    else:
        sql.require(
            schema.get("minimum", 0) <= value <= schema.get("maximum", sql.MAX_COUNTER),
            "GOV_SCHEMA",
        )


def certificate_identity(value):
    identity = {
        "namespace": value["namespace"],
        "authority_id": value["authority_id"],
        "authority_epoch": value["authority_epoch"],
        "reservation_id": value["reservation"]["reservation_id"],
    }
    return hashlib.sha256(ID_DOMAIN + sql.canonical_bytes(identity)).hexdigest()


@dataclass(frozen=True, slots=True)
class GovernanceCertificateV1:
    """Immutable canonical claims; construction does not grant trust."""

    canonical_bytes: bytes

    @property
    def value(self):
        return sql.parse_canonical(self.canonical_bytes)


def validate_governance_certificate(value, *, context=None):
    if type(value) is GovernanceCertificateV1:
        sql.require(
            type(value.canonical_bytes) is bytes and len(value.canonical_bytes) <= MAX_BYTES,
            "GOV_SCHEMA",
        )
        value = sql.parse_canonical(value.canonical_bytes)
    sql.require(type(value) is dict, "GOV_SCHEMA")
    value = copy.deepcopy(value)
    encoded = sql.canonical_bytes(value)
    sql.require(0 < len(encoded) <= MAX_BYTES, "GOV_SCHEMA")
    _shape(value, certificate_schema())
    sql.utc_time(value["issued_at"])
    sql.require(value["certificate_id"] == certificate_identity(value), "GOV_ID")
    before = value["authority_revision_before"]
    sql.require(value["authority_revision_after"] == before + 1, "GOV_REVISION")
    base = value["b3_base_witness"]
    sql.require(
        (base["authority_id"], base["authority_epoch"], base["revision"])
        == (value["authority_id"], value["authority_epoch"], before)
        and base["receipt_reference"]["sha256"] == base["witness_digest"],
        "GOV_WITNESS",
    )
    sql.require(
        value["session"]["header_sha256"] == value["session"]["header_reference"]["sha256"],
        "GOV_SESSION",
    )
    sql.require(
        value["runtime_profile"]["sha256"] == value["runtime_profile"]["reference"]["sha256"],
        "GOV_PROFILE",
    )
    acks = value["acknowledgments"]
    ids = [ack["run_id"] for ack in acks]
    sql.require(
        ids == sorted(set(ids)) and all(a["authority_revision"] == before for a in acks), "GOV_ACK"
    )
    sql.require(value["acknowledgment_digest"] == sql.digest(acks), "GOV_ACK")
    if context is not None:
        # Only exact already available certificate claims; no callbacks, file
        # reads, crypto verification, queries or capability execution here.
        sql.require(type(context) is dict and context.keys() <= value.keys(), "GOV_CONTEXT")
        sql.canonical_bytes(context)
        for key, expected in context.items():
            sql.require(
                type(expected) is type(value[key]) and expected == value[key], "GOV_BINDING"
            )
    return GovernanceCertificateV1(encoded)


def parse_governance_certificate_bytes(data, *, context=None):
    sql.require(type(data) is bytes and 0 < len(data) <= MAX_BYTES, "GOV_SCHEMA")
    return validate_governance_certificate(sql.parse_canonical(data), context=context)


def canonical_governance_certificate_bytes(value):
    return validate_governance_certificate(value).canonical_bytes


def _canonical_sha256(data):
    """Hash the entire preimage; schema-invalid mutations remain hash-bound."""
    sql.require(type(data) is bytes and 0 < len(data) <= MAX_BYTES, "GOV_SCHEMA")
    return hashlib.sha256(data).hexdigest()


def governance_certificate_sha256(value):
    return _canonical_sha256(canonical_governance_certificate_bytes(value))


def validate_governance_reference(row, certificate, *, artifact_namespace=None):
    cert = validate_governance_certificate(certificate)
    sql.require(type(row) is dict, "GOV_REFERENCE")
    row = copy.deepcopy(row)
    sql.canonical_bytes(row)
    _shape(row, reference_schema())
    sql.validate_row("artifact_references", row)
    body = cert.value
    path = row["path"]
    sql.require(path.startswith("/") and str(PurePosixPath(path)) == path, "GOV_PATH")
    sql.require(not any(ord(c) < 32 for c in path), "GOV_PATH")
    sql.require(PurePosixPath(path).name == body["certificate_id"] + ".json", "GOV_PATH")
    if artifact_namespace is not None:
        sql.require(
            type(artifact_namespace) is str
            and artifact_namespace.startswith("/")
            and str(PurePosixPath(artifact_namespace)) == artifact_namespace,
            "GOV_PATH",
        )
        sql.validate_scalar(artifact_namespace, {"type": "locator", "not_null": True})
        sql.require(path == artifact_namespace + "/" + body["certificate_id"] + ".json", "GOV_PATH")
    sql.require(row["sha256"] == _canonical_sha256(cert.canonical_bytes), "GOV_SHA")
    sql.require(row["introduced_revision"] == body["authority_revision_after"], "GOV_REVISION")
    return row


def parse_governance_reference_bytes(data, certificate, *, artifact_namespace=None):
    return validate_governance_reference(
        sql.parse_canonical(data), certificate, artifact_namespace=artifact_namespace
    )


def canonical_governance_reference_bytes(row, certificate, *, artifact_namespace=None):
    return sql.canonical_bytes(
        validate_governance_reference(row, certificate, artifact_namespace=artifact_namespace)
    )
