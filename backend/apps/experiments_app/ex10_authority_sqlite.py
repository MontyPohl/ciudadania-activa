"""Offline EX-10 B3 SQLite codec. No operational database factory or recovery.

The schema is embedded from the approved design. Only explicit test harnesses
create databases. Production connections are read-only. There is no B4 lock,
reservation, executor bridge, service, key store, retry or repair here.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import sqlite3
import stat
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

_SURROGATE_CODEPOINT_RE = re.compile(r"[\ud800-\udfff]")

_APPROVED_SCHEMA_JSON = r"""{"authority_schema_version":"s11-ex10-historical-authority-ledger-v1","counter_overflow":"At 2^53-1 stop publication; never wrap, truncate, use floating point or reset. Separately certified successor required.","logical_digest":{"algorithm":"SHA-256","canonicalization":"UTF-8, JSON sort_keys=true, separators comma/colon without spaces, ensure_ascii=false, allow_nan=false, exactly one trailing LF; no BOM, duplicate keys, floats, non-NFC strings, surrogate codepoints or extra properties; arrays ordered explicitly.","excluded":["witness_checkpoints","publication_intents","publication_resolutions","all history_revisions.ledger_digest","SQLite physical bytes, page layout, indexes, caches, file metadata"],"fields":["schema_version","metadata","revision","artifact_references","official_runs","history_revisions","authority_events","bootstrap_manifest","bootstrap_sources","bootstrap_findings"],"ordering":{"artifact_references":["reference_id"],"authority_events":["event_sequence"],"bootstrap_findings":["source_id","run_id"],"bootstrap_sources":["source_id"],"history_revisions":["revision"],"official_runs":["run_id","run_version"]},"prefix_verification":"Reconstruct each prefix revision 0..head, recompute digest, verify stored digest and successor previous_ledger_digest; full history required, no unauthenticated incremental cache shortcuts.","preimage":"canonical full closed logical projection including all run versions, not merely latest run rows","projection_schema_version":"s11-ex10-authority-logical-state-v1","scope":"All committed rows introduced at revision <= selected revision. Bootstrap static rows included since zero. history_revisions omit derived ledger_digest but retain previous_ledger_digest. revision header and events are known before computing new digest. Include NULL explicitly and convert bool01 fields to wire booleans.","stability":"VACUUM or equivalent page reorganization with identical projected values yields identical digest; deleting a run/version/event/source/ref changes projection or invalidates constraints. SQLite byte SHA may be custody evidence only."},"projection_row_encoding":"Each projected row is a closed object with exactly the declared column names and values (integer/bool conversion as specified), except history_revisions omits ledger_digest; table arrays present even if empty. metadata/bootstrap_manifest serialized as singleton row objects, not omitted/arrays. Schema field fixed projection_schema_version.","scalar_types":{"bool01":{"check":"value IN (0,1); wire type is boolean, not number","sqlite_type":"INTEGER"},"enum":{"check":"exact closed per-column enum","sqlite_type":"TEXT"},"git_sha1":{"check":"exactly 40 lowercase ASCII hexadecimal characters","sqlite_type":"TEXT"},"identifier":{"check":"ASCII [A-Za-z0-9][A-Za-z0-9_.:-]{0,127}","sqlite_type":"TEXT"},"int64_nonnegative":{"check":"exact integer; 0 <= value <= 9007199254740991; stored as SQLite INTEGER, restricted to interoperable JSON safe integer domain; JSON bool forbidden","sqlite_type":"INTEGER"},"locator":{"check":"absolute POSIX path or approved HTTPS object locator; no NUL, userinfo, query credentials, fragment or traversal; locator alone grants no trust","sqlite_type":"TEXT"},"positive_int64":{"check":"exact integer; 1 <= value <= 9007199254740991; stored as SQLite INTEGER with safe JSON bound","sqlite_type":"INTEGER"},"run_id":{"check":"s11- followed by canonical lowercase UUID; reject whitespace, separators and NUL","sqlite_type":"TEXT"},"sha256":{"check":"exactly 64 lowercase ASCII hexadecimal characters","sqlite_type":"TEXT"},"signature_ed25519":{"check":"128 lowercase ASCII hex characters, exactly 64 decoded bytes","sqlite_type":"TEXT"},"utc":{"check":"YYYY-MM-DDTHH:MM:SS.ffffffZ; valid Gregorian UTC instant; no leap seconds, alternate offsets or inferred values","sqlite_type":"TEXT"},"uuid":{"check":"lowercase canonical RFC 9562 UUID, 36 ASCII characters","sqlite_type":"TEXT"}},"schema_version":"s11-ex10-phase-b3-relational-design-v1","sqlite_requirements":{"busy_policy":"Fail closed, bounded one attempt, no automatic mutating retry","expected_database_files":["ledger.sqlite3","ledger.sqlite3-journal (transient private rollback journal, preserved if crash/hot)"],"filesystem":"Qualified local POSIX filesystem; no NFS/network/shared mounted authority","forbidden_files":"Unexpected -wal/-shm means configuration mismatch; preserve, fail closed, never auto-delete or auto-convert.","foreign_keys":true,"journal_mode":"DELETE","minimum_version":"3.37.0","read_mode":"Read-only connections, no immutable=1 on live DB; hot journal/recovery required => stop read qualification and request separate manual recovery","synchronous":"EXTRA","tables":"STRICT","trusted_schema":false},"state_transitions":{"ACTIVE":["ACTIVE","UNCERTAIN","TERMINAL"],"OPEN":["ACTIVE","UNCERTAIN","TERMINAL"],"TERMINAL":[],"UNCERTAIN":["UNCERTAIN","ACTIVE","TERMINAL"],"manual_requirements":"Every exit from UNCERTAIN requires separate operator approval, external witness freshness and evidence establishing the next state; OPEN->TERMINAL also requires complete authoritative terminal evidence. Terminal immutable even by manual resolution."},"status":"DESIGN_ONLY_NOT_DATABASE_DDL","tables":[{"append_only_enforcement":"Future UPDATE and DELETE rejection guards named reject_update_artifact_references and reject_delete_artifact_references; verifier checks their declared presence and behavior. INSERT transitions require internal verifier. No guard implementation provided.","append_only_policy":"No UPDATE, DELETE, REPLACE, UPSERT or cascading deletion. INSERT only via validated internal publication; verifier detects trigger/schema tampering. DDL migration requires separate authorization.","checks":["reference_id = SHA256(canonical reference {path,sha256,schema_version,kind})"],"columns":{"introduced_revision":{"not_null":true,"type":"int64_nonnegative"},"kind":{"not_null":true,"type":"enum","values":["EVIDENCE","SESSION","PROFILE","SOURCE_ANCHOR","APPROVAL","COVERAGE","BOOTSTRAP","GOVERNANCE","SNAPSHOT"]},"path":{"not_null":true,"type":"locator"},"reference_id":{"not_null":true,"type":"sha256"},"schema_version":{"not_null":true,"type":"identifier"},"sha256":{"not_null":true,"type":"sha256"}},"constraint_names":{"checks":["ck_artifact_references_1"],"foreign_keys":["fk_artifact_references_1"],"primary_key":"pk_artifact_references","unique":["uq_artifact_references_1"]},"foreign_keys":[{"columns":["introduced_revision"],"deferrable":"INITIALLY_DEFERRED","on_delete":"RESTRICT","on_update":"RESTRICT","references_columns":["revision"],"references_table":"history_revisions"}],"indexes":[{"columns":["introduced_revision"],"name":"idx_artifact_references_introduced_revision","predicate":null,"unique":false}],"mutability":"IMMUTABLE_ROWS","name":"artifact_references","primary_key":["reference_id"],"purpose":"Normalize typed custody/evidence/approval references; never copy experimental payloads.","strict":true,"unique":[["path","sha256","schema_version","kind"]]},{"append_only_enforcement":"Future UPDATE and DELETE rejection guards named reject_update_metadata and reject_delete_metadata; verifier checks their declared presence and behavior. INSERT transitions require internal verifier. No guard implementation provided.","append_only_policy":"No UPDATE, DELETE, REPLACE, UPSERT or cascading deletion. INSERT only via validated internal publication; verifier detects trigger/schema tampering. DDL migration requires separate authorization.","checks":["singleton = 1; exactly one row after genesis"],"columns":{"authority_id":{"not_null":true,"type":"uuid"},"bootstrap_reference_id":{"not_null":true,"type":"sha256"},"descriptor_sha256":{"not_null":true,"type":"sha256"},"epoch":{"not_null":true,"type":"positive_int64"},"g0_a3_approval_reference_id":{"not_null":true,"type":"sha256"},"namespace":{"not_null":true,"type":"enum","values":["S11C3-EX10"]},"schema_version":{"not_null":true,"type":"enum","values":["s11-ex10-historical-authority-ledger-v1"]},"singleton":{"not_null":true,"type":"positive_int64"},"source_baseline_head":{"not_null":true,"type":"git_sha1"},"source_baseline_tree":{"not_null":true,"type":"git_sha1"}},"constraint_names":{"checks":["ck_metadata_1"],"foreign_keys":["fk_metadata_1","fk_metadata_2"],"primary_key":"pk_metadata","unique":["uq_metadata_1"]},"foreign_keys":[{"columns":["bootstrap_reference_id"],"deferrable":"INITIALLY_DEFERRED","on_delete":"RESTRICT","on_update":"RESTRICT","references_columns":["reference_id"],"references_table":"artifact_references"},{"columns":["g0_a3_approval_reference_id"],"deferrable":"INITIALLY_DEFERRED","on_delete":"RESTRICT","on_update":"RESTRICT","references_columns":["reference_id"],"references_table":"artifact_references"}],"indexes":[],"mutability":"IMMUTABLE_ROWS","name":"metadata","primary_key":["singleton"],"purpose":"Singleton immutable authority identity; no mutable revision counter.","strict":true,"unique":[["authority_id","epoch"]]},{"append_only_enforcement":"Future UPDATE and DELETE rejection guards named reject_update_history_revisions and reject_delete_history_revisions; verifier checks their declared presence and behavior. INSERT transitions require internal verifier. No guard implementation provided.","append_only_policy":"No UPDATE, DELETE, REPLACE, UPSERT or cascading deletion. INSERT only via validated internal publication; verifier detects trigger/schema tampering. DDL migration requires separate authorization.","checks":["revision = 0 iff previous_ledger_digest IS NULL","last_event_sequence >= first_event_sequence","event_count = last_event_sequence - first_event_sequence + 1","Cross-row: revision set exactly 0..max; revision n+1 links recomputed digest of n; all event ranges contiguous, nonoverlapping and exhaustive"],"columns":{"event_count":{"not_null":true,"type":"positive_int64"},"first_event_sequence":{"not_null":true,"type":"positive_int64"},"last_event_sequence":{"not_null":true,"type":"positive_int64"},"ledger_digest":{"not_null":true,"type":"sha256"},"operation_id":{"not_null":true,"type":"uuid"},"previous_ledger_digest":{"not_null":false,"type":"sha256"},"recorded_at":{"not_null":true,"type":"utc"},"revision":{"not_null":true,"type":"int64_nonnegative"}},"constraint_names":{"checks":["ck_history_revisions_1","ck_history_revisions_2","ck_history_revisions_3","ck_history_revisions_4"],"foreign_keys":[],"primary_key":"pk_history_revisions","unique":["uq_history_revisions_1"]},"foreign_keys":[],"indexes":[{"columns":["recorded_at"],"name":"idx_history_revisions_recorded_at","predicate":null,"unique":false}],"mutability":"IMMUTABLE_ROWS","name":"history_revisions","primary_key":["revision"],"purpose":"Committed contiguous global publication chain; digest is derived, not input authority.","strict":true,"unique":[["operation_id"]]},{"append_only_enforcement":"Future UPDATE and DELETE rejection guards named reject_update_official_runs and reject_delete_official_runs; verifier checks their declared presence and behavior. INSERT transitions require internal verifier. No guard implementation provided.","append_only_policy":"No UPDATE, DELETE, REPLACE, UPSERT or cascading deletion. INSERT only via validated internal publication; verifier detects trigger/schema tampering. DDL migration requires separate authorization.","checks":["run_version = 0 iff previous_run_version IS NULL; otherwise previous_run_version = run_version - 1","created_at <= opened_at; finalized_at IS NULL OR finalized_at >= opened_at","TERMINAL iff phase=FINALIZED, result IS NOT NULL and finalized_at IS NOT NULL","Nonterminal: result IS NULL, finalized_at IS NULL, binding=0, ack_required=0","TERMINAL PASS/FAIL: binding=1, validity_classification=VALID_BINDING, ack_required=0","TERMINAL NOT_DEMONSTRATED: binding=0, validity_classification=NON_BINDING_ND, ack_required=1","OPEN: phase=OPENED, validity=PENDING; ACTIVE: phase IN(EXECUTING,DRAINED,RECONCILED), validity=PENDING; UNCERTAIN: phase=UNCERTAIN, validity=UNCERTAIN","PROFILE_V2 requires nonnull profile and session refs; HISTORICAL_V1 profile ref IS NULL (session ref optional only if historically absent and certified)","Cross-version: run identity, source_revision, protocol, methodology, mode, provenance_model, created_at, opened_at, profile/session refs immutable; terminal may not acquire a successor; each new version strictly later introduced_revision","Cross-run: at most one first-valid terminal PASS/FAIL across official history; additional binding outcomes are contradictory, never silently selected"],"columns":{"ack_required":{"not_null":true,"type":"bool01"},"binding":{"not_null":true,"type":"bool01"},"created_at":{"not_null":true,"type":"utc"},"evidence_authority_reference_id":{"not_null":true,"type":"sha256"},"finalized_at":{"not_null":false,"type":"utc"},"introduced_revision":{"not_null":true,"type":"int64_nonnegative"},"lifecycle_state":{"not_null":true,"type":"enum","values":["OPEN","ACTIVE","UNCERTAIN","TERMINAL"]},"methodology":{"not_null":true,"type":"identifier"},"mode":{"not_null":true,"type":"enum","values":["OFFICIAL"]},"opened_at":{"not_null":true,"type":"utc"},"phase":{"not_null":true,"type":"enum","values":["OPENED","EXECUTING","DRAINED","RECONCILED","UNCERTAIN","FINALIZED"]},"previous_run_version":{"not_null":false,"type":"int64_nonnegative"},"profile_reference_id":{"not_null":false,"type":"sha256"},"protocol":{"not_null":true,"type":"identifier"},"provenance_model":{"not_null":true,"type":"enum","values":["HISTORICAL_V1","PROFILE_V2"]},"result":{"not_null":false,"type":"enum","values":["PASS","FAIL","NOT_DEMONSTRATED"]},"run_id":{"not_null":true,"type":"run_id"},"run_version":{"not_null":true,"type":"int64_nonnegative"},"session_reference_id":{"not_null":false,"type":"sha256"},"source_revision":{"not_null":true,"type":"git_sha1"},"validity_classification":{"not_null":true,"type":"enum","values":["PENDING","UNCERTAIN","VALID_BINDING","NON_BINDING_ND"]}},"constraint_names":{"checks":["ck_official_runs_1","ck_official_runs_2","ck_official_runs_3","ck_official_runs_4","ck_official_runs_5","ck_official_runs_6","ck_official_runs_7","ck_official_runs_8","ck_official_runs_9","ck_official_runs_10"],"foreign_keys":["fk_official_runs_1","fk_official_runs_2","fk_official_runs_3","fk_official_runs_4","fk_official_runs_5"],"primary_key":"pk_official_runs","unique":["uq_official_runs_1"]},"foreign_keys":[{"columns":["introduced_revision"],"deferrable":"INITIALLY_DEFERRED","on_delete":"RESTRICT","on_update":"RESTRICT","references_columns":["revision"],"references_table":"history_revisions"},{"columns":["evidence_authority_reference_id"],"deferrable":"INITIALLY_DEFERRED","on_delete":"RESTRICT","on_update":"RESTRICT","references_columns":["reference_id"],"references_table":"artifact_references"},{"columns":["profile_reference_id"],"deferrable":"INITIALLY_DEFERRED","on_delete":"RESTRICT","on_update":"RESTRICT","references_columns":["reference_id"],"references_table":"artifact_references"},{"columns":["session_reference_id"],"deferrable":"INITIALLY_DEFERRED","on_delete":"RESTRICT","on_update":"RESTRICT","references_columns":["reference_id"],"references_table":"artifact_references"},{"columns":["run_id","previous_run_version"],"deferrable":"INITIALLY_DEFERRED","on_delete":"RESTRICT","on_update":"RESTRICT","references_columns":["run_id","run_version"],"references_table":"official_runs"}],"indexes":[{"columns":["introduced_revision"],"name":"idx_official_runs_introduced_revision","predicate":null,"unique":false},{"columns":["lifecycle_state","run_id"],"name":"idx_official_runs_lifecycle_state_run_id","predicate":null,"unique":false},{"columns":["result","binding","introduced_revision"],"name":"idx_official_runs_result_binding_introduced_revision","predicate":null,"unique":false}],"mutability":"IMMUTABLE_ROWS","name":"official_runs","primary_key":["run_id","run_version"],"purpose":"Append-only lifecycle versions, preserving every previous state. Current run = greatest run_version at selected revision.","strict":true,"unique":[["run_id","introduced_revision"]]},{"append_only_enforcement":"Future UPDATE and DELETE rejection guards named reject_update_authority_events and reject_delete_authority_events; verifier checks their declared presence and behavior. INSERT transitions require internal verifier. No guard implementation provided.","append_only_policy":"No UPDATE, DELETE, REPLACE, UPSERT or cascading deletion. INSERT only via validated internal publication; verifier detects trigger/schema tampering. DDL migration requires separate authorization.","checks":["run_id and run_version both NULL or both nonnull","RUN_* / IMPORT_RUN require run tuple; GENESIS / AUTHORITY_UNCERTAIN / COVERAGE_EXTENSION require no run tuple","MANUAL_RESOLUTION requires resolves_event_sequence + operator approval proof; other kinds resolve NULL","Cross-row: event_sequence contiguous from 1, event revision agrees with associated run version introduced_revision; GENESIS only revision 0 and exactly one; timestamps are event evidence times, not substituted historical run times"],"columns":{"event_kind":{"not_null":true,"type":"enum","values":["GENESIS","IMPORT_RUN","RUN_OPENED","RUN_STATE","RUN_TERMINAL","RUN_UNCERTAIN","AUTHORITY_UNCERTAIN","MANUAL_RESOLUTION","COVERAGE_EXTENSION"]},"event_sequence":{"not_null":true,"type":"positive_int64"},"evidence_reference_id":{"not_null":true,"type":"sha256"},"occurred_at":{"not_null":true,"type":"utc"},"reason_code":{"not_null":false,"type":"identifier"},"resolves_event_sequence":{"not_null":false,"type":"positive_int64"},"revision":{"not_null":true,"type":"int64_nonnegative"},"run_id":{"not_null":false,"type":"run_id"},"run_version":{"not_null":false,"type":"int64_nonnegative"}},"constraint_names":{"checks":["ck_authority_events_1","ck_authority_events_2","ck_authority_events_3","ck_authority_events_4"],"foreign_keys":["fk_authority_events_1","fk_authority_events_2","fk_authority_events_3","fk_authority_events_4"],"primary_key":"pk_authority_events","unique":[]},"foreign_keys":[{"columns":["revision"],"deferrable":"INITIALLY_DEFERRED","on_delete":"RESTRICT","on_update":"RESTRICT","references_columns":["revision"],"references_table":"history_revisions"},{"columns":["run_id","run_version"],"deferrable":"INITIALLY_DEFERRED","on_delete":"RESTRICT","on_update":"RESTRICT","references_columns":["run_id","run_version"],"references_table":"official_runs"},{"columns":["evidence_reference_id"],"deferrable":"INITIALLY_DEFERRED","on_delete":"RESTRICT","on_update":"RESTRICT","references_columns":["reference_id"],"references_table":"artifact_references"},{"columns":["resolves_event_sequence"],"deferrable":"INITIALLY_DEFERRED","on_delete":"RESTRICT","on_update":"RESTRICT","references_columns":["event_sequence"],"references_table":"authority_events"}],"indexes":[{"columns":["revision","event_sequence"],"name":"idx_authority_events_revision_event_sequence","predicate":null,"unique":false},{"columns":["run_id","event_sequence"],"name":"idx_authority_events_run_id_event_sequence","predicate":null,"unique":false},{"columns":["event_kind","event_sequence"],"name":"idx_authority_events_event_kind_event_sequence","predicate":null,"unique":false}],"mutability":"IMMUTABLE_ROWS","name":"authority_events","primary_key":["event_sequence"],"purpose":"Typed append-only audit history, no arbitrary JSON payload.","strict":true,"unique":[]},{"append_only_enforcement":"Future UPDATE and DELETE rejection guards named reject_update_witness_checkpoints and reject_delete_witness_checkpoints; verifier checks their declared presence and behavior. INSERT transitions require internal verifier. No guard implementation provided.","append_only_policy":"No UPDATE, DELETE, REPLACE, UPSERT or cascading deletion. INSERT only via validated internal publication; verifier detects trigger/schema tampering. DDL migration requires separate authorization.","checks":["revision=0 iff previous_witness_digest IS NULL","Cross-table: authority_id/epoch match singleton metadata; operation_id + ledger_digest exactly match revision row","Cross-row: previous_witness_digest matches preceding canonical full receipt digest, accepted external key epoch and genesis bindings; one checkpoint per committed revision, no gaps","Revision zero requires all six genesis_* fields; all later revisions require all NULL; values match singleton metadata and independently sealed bootstrap. namespace constant S11C3-EX10 is reconstructed; schema/key names map exactly to witness receipt schema."],"columns":{"authority_epoch":{"not_null":true,"type":"positive_int64"},"authority_id":{"not_null":true,"type":"uuid"},"created_at":{"not_null":true,"type":"utc"},"genesis_baseline_head":{"not_null":false,"type":"git_sha1"},"genesis_baseline_tree":{"not_null":false,"type":"git_sha1"},"genesis_bootstrap_sha256":{"not_null":false,"type":"sha256"},"genesis_g0_a3_approval_path":{"not_null":false,"type":"locator"},"genesis_g0_a3_approval_schema":{"not_null":false,"type":"identifier"},"genesis_g0_a3_approval_sha256":{"not_null":false,"type":"sha256"},"key_epoch":{"not_null":true,"type":"positive_int64"},"key_fingerprint":{"not_null":true,"type":"sha256"},"key_id":{"not_null":true,"type":"identifier"},"ledger_digest":{"not_null":true,"type":"sha256"},"operation_id":{"not_null":true,"type":"uuid"},"previous_witness_digest":{"not_null":false,"type":"sha256"},"receipt_schema_version":{"not_null":true,"type":"enum","values":["s11-ex10-authority-witness-receipt-v1"]},"revision":{"not_null":true,"type":"int64_nonnegative"},"signature":{"not_null":true,"type":"signature_ed25519"},"witness_digest":{"not_null":true,"type":"sha256"}},"constraint_names":{"checks":["ck_witness_checkpoints_1","ck_witness_checkpoints_2","ck_witness_checkpoints_3","ck_witness_checkpoints_4"],"foreign_keys":["fk_witness_checkpoints_1"],"primary_key":"pk_witness_checkpoints","unique":["uq_witness_checkpoints_1","uq_witness_checkpoints_2"]},"foreign_keys":[{"columns":["revision"],"deferrable":"INITIALLY_DEFERRED","on_delete":"RESTRICT","on_update":"RESTRICT","references_columns":["revision"],"references_table":"history_revisions"}],"indexes":[{"columns":["key_epoch","revision"],"name":"idx_witness_checkpoints_key_epoch_revision","predicate":null,"unique":false}],"mutability":"IMMUTABLE_ROWS","name":"witness_checkpoints","primary_key":["revision"],"purpose":"Locally cached independently signed receipts; excluded from logical digest to avoid recursive self-hashing.","strict":true,"unique":[["operation_id"],["witness_digest"]]},{"append_only_enforcement":"Future UPDATE and DELETE rejection guards named reject_update_bootstrap_manifest and reject_delete_bootstrap_manifest; verifier checks their declared presence and behavior. INSERT transitions require internal verifier. No guard implementation provided.","append_only_policy":"No UPDATE, DELETE, REPLACE, UPSERT or cascading deletion. INSERT only via validated internal publication; verifier detects trigger/schema tampering. DDL migration requires separate authorization.","checks":["singleton=1; exactly one row; metadata/bootstrap baseline and references agree","source_count and run_count equal verified sealed manifest counts"],"columns":{"baseline_head":{"not_null":true,"type":"git_sha1"},"baseline_tree":{"not_null":true,"type":"git_sha1"},"certified_at":{"not_null":true,"type":"utc"},"completeness":{"not_null":true,"type":"enum","values":["COMPLETE"]},"core_digest":{"not_null":true,"type":"sha256"},"coverage_certificate_reference_id":{"not_null":true,"type":"sha256"},"manifest_reference_id":{"not_null":true,"type":"sha256"},"operator_approval_reference_id":{"not_null":true,"type":"sha256"},"run_count":{"not_null":true,"type":"int64_nonnegative"},"scope_reference_id":{"not_null":true,"type":"sha256"},"singleton":{"not_null":true,"type":"positive_int64"},"source_count":{"not_null":true,"type":"positive_int64"}},"constraint_names":{"checks":["ck_bootstrap_manifest_1","ck_bootstrap_manifest_2"],"foreign_keys":["fk_bootstrap_manifest_1","fk_bootstrap_manifest_2","fk_bootstrap_manifest_3","fk_bootstrap_manifest_4"],"primary_key":"pk_bootstrap_manifest","unique":["uq_bootstrap_manifest_1"]},"foreign_keys":[{"columns":["manifest_reference_id"],"deferrable":"INITIALLY_DEFERRED","on_delete":"RESTRICT","on_update":"RESTRICT","references_columns":["reference_id"],"references_table":"artifact_references"},{"columns":["scope_reference_id"],"deferrable":"INITIALLY_DEFERRED","on_delete":"RESTRICT","on_update":"RESTRICT","references_columns":["reference_id"],"references_table":"artifact_references"},{"columns":["coverage_certificate_reference_id"],"deferrable":"INITIALLY_DEFERRED","on_delete":"RESTRICT","on_update":"RESTRICT","references_columns":["reference_id"],"references_table":"artifact_references"},{"columns":["operator_approval_reference_id"],"deferrable":"INITIALLY_DEFERRED","on_delete":"RESTRICT","on_update":"RESTRICT","references_columns":["reference_id"],"references_table":"artifact_references"}],"indexes":[],"mutability":"IMMUTABLE_ROWS","name":"bootstrap_manifest","primary_key":["singleton"],"purpose":"One sealed COMPLETE genesis manifest; malformed candidates never qualify as authority.","strict":true,"unique":[["manifest_reference_id"]]},{"append_only_enforcement":"Future UPDATE and DELETE rejection guards named reject_update_bootstrap_sources and reject_delete_bootstrap_sources; verifier checks their declared presence and behavior. INSERT transitions require internal verifier. No guard implementation provided.","append_only_policy":"No UPDATE, DELETE, REPLACE, UPSERT or cascading deletion. INSERT only via validated internal publication; verifier detects trigger/schema tampering. DDL migration requires separate authorization.","checks":["SCANNED requires snapshot reference; ABSENT_CERTIFIED requires observation proof, no snapshot and zero discoveries","UNREADABLE or BLOCKED rows permitted in candidate representation only; committed COMPLETE genesis rejects them","All mandatory scope locations and writers accounted exactly once; source set immutable after genesis (extensions need independent approved supplemental coverage event)"],"columns":{"discovered_run_count":{"not_null":true,"type":"int64_nonnegative"},"has_active_source":{"not_null":true,"type":"bool01"},"location":{"not_null":true,"type":"locator"},"observation_reference_id":{"not_null":true,"type":"sha256"},"observed_at":{"not_null":true,"type":"utc"},"scope_reference_id":{"not_null":true,"type":"sha256"},"snapshot_reference_id":{"not_null":false,"type":"sha256"},"source_id":{"not_null":true,"type":"identifier"},"source_kind":{"not_null":true,"type":"enum","values":["EVIDENCE_ROOT","SESSION_ROOT","SOURCE_ANCHOR","CUSTODY","GOVERNANCE","WRITER_REGISTRY"]},"status":{"not_null":true,"type":"enum","values":["SCANNED","ABSENT_CERTIFIED","UNREADABLE","BLOCKED"]}},"constraint_names":{"checks":["ck_bootstrap_sources_1","ck_bootstrap_sources_2","ck_bootstrap_sources_3"],"foreign_keys":["fk_bootstrap_sources_1","fk_bootstrap_sources_2","fk_bootstrap_sources_3"],"primary_key":"pk_bootstrap_sources","unique":["uq_bootstrap_sources_1"]},"foreign_keys":[{"columns":["scope_reference_id"],"deferrable":"INITIALLY_DEFERRED","on_delete":"RESTRICT","on_update":"RESTRICT","references_columns":["reference_id"],"references_table":"artifact_references"},{"columns":["snapshot_reference_id"],"deferrable":"INITIALLY_DEFERRED","on_delete":"RESTRICT","on_update":"RESTRICT","references_columns":["reference_id"],"references_table":"artifact_references"},{"columns":["observation_reference_id"],"deferrable":"INITIALLY_DEFERRED","on_delete":"RESTRICT","on_update":"RESTRICT","references_columns":["reference_id"],"references_table":"artifact_references"}],"indexes":[{"columns":["status"],"name":"idx_bootstrap_sources_status","predicate":null,"unique":false},{"columns":["has_active_source"],"name":"idx_bootstrap_sources_has_active_source","predicate":null,"unique":false}],"mutability":"IMMUTABLE_ROWS","name":"bootstrap_sources","primary_key":["source_id"],"purpose":"Closed genesis source inventory, including formally certified absence; no skipped inaccessible source.","strict":true,"unique":[["source_kind","location"]]},{"append_only_enforcement":"Future UPDATE and DELETE rejection guards named reject_update_bootstrap_findings and reject_delete_bootstrap_findings; verifier checks their declared presence and behavior. INSERT transitions require internal verifier. No guard implementation provided.","append_only_policy":"No UPDATE, DELETE, REPLACE, UPSERT or cascading deletion. INSERT only via validated internal publication; verifier detects trigger/schema tampering. DDL migration requires separate authorization.","checks":["run_version=0","Every imported run has one or more findings and at least one primary evidence finding; identical copies do not create extra runs; incompatible facts abort before genesis"],"columns":{"finding_reference_id":{"not_null":true,"type":"sha256"},"is_primary_evidence":{"not_null":true,"type":"bool01"},"run_id":{"not_null":true,"type":"run_id"},"run_version":{"not_null":true,"type":"int64_nonnegative"},"source_id":{"not_null":true,"type":"identifier"}},"constraint_names":{"checks":["ck_bootstrap_findings_1","ck_bootstrap_findings_2"],"foreign_keys":["fk_bootstrap_findings_1","fk_bootstrap_findings_2","fk_bootstrap_findings_3"],"primary_key":"pk_bootstrap_findings","unique":["uq_bootstrap_findings_1"]},"foreign_keys":[{"columns":["source_id"],"deferrable":"INITIALLY_DEFERRED","on_delete":"RESTRICT","on_update":"RESTRICT","references_columns":["source_id"],"references_table":"bootstrap_sources"},{"columns":["run_id","run_version"],"deferrable":"INITIALLY_DEFERRED","on_delete":"RESTRICT","on_update":"RESTRICT","references_columns":["run_id","run_version"],"references_table":"official_runs"},{"columns":["finding_reference_id"],"deferrable":"INITIALLY_DEFERRED","on_delete":"RESTRICT","on_update":"RESTRICT","references_columns":["reference_id"],"references_table":"artifact_references"}],"indexes":[{"columns":["run_id"],"name":"idx_bootstrap_findings_run_id","predicate":null,"unique":false}],"mutability":"IMMUTABLE_ROWS","name":"bootstrap_findings","primary_key":["source_id","run_id"],"purpose":"Deduplicate physical copies without losing independent evidence or hiding conflicting facts.","strict":true,"unique":[["source_id","finding_reference_id"]]},{"append_only_enforcement":"Future UPDATE and DELETE rejection guards named reject_update_publication_intents and reject_delete_publication_intents; verifier checks their declared presence and behavior. INSERT transitions require internal verifier. No guard implementation provided.","append_only_policy":"No UPDATE, DELETE, REPLACE, UPSERT or cascading deletion. INSERT only via validated internal publication; verifier detects trigger/schema tampering. DDL migration requires separate authorization.","checks":["candidate_revision=0 iff base_revision/base_ledger_digest both NULL; otherwise candidate_revision=base_revision+1","Candidate payload is closed-schema exact proposed logical projection, durably stored and hashed; capability digest is audit only, not authentication","No accepted qualification while any intent lacks a valid resolution; intent insertion does not change committed revision; local control table excluded from logical digest but checked before every read"],"columns":{"base_ledger_digest":{"not_null":false,"type":"sha256"},"base_revision":{"not_null":false,"type":"int64_nonnegative"},"candidate_ledger_digest":{"not_null":true,"type":"sha256"},"candidate_payload_path":{"not_null":true,"type":"locator"},"candidate_payload_sha256":{"not_null":true,"type":"sha256"},"candidate_revision":{"not_null":true,"type":"int64_nonnegative"},"created_at":{"not_null":true,"type":"utc"},"operation_id":{"not_null":true,"type":"uuid"},"serialization_capability_digest":{"not_null":true,"type":"sha256"}},"constraint_names":{"checks":["ck_publication_intents_1","ck_publication_intents_2","ck_publication_intents_3"],"foreign_keys":[],"primary_key":"pk_publication_intents","unique":["uq_publication_intents_1"]},"foreign_keys":[],"indexes":[{"columns":["candidate_revision"],"name":"idx_publication_intents_candidate_revision","predicate":null,"unique":false}],"mutability":"IMMUTABLE_ROWS","name":"publication_intents","primary_key":["operation_id"],"purpose":"Durable local pending publication barrier before independent witness CAS; not a B4 START reservation.","strict":true,"unique":[["candidate_revision"]]},{"append_only_enforcement":"Future UPDATE and DELETE rejection guards named reject_update_publication_resolutions and reject_delete_publication_resolutions; verifier checks their declared presence and behavior. INSERT transitions require internal verifier. No guard implementation provided.","append_only_policy":"No UPDATE, DELETE, REPLACE, UPSERT or cascading deletion. INSERT only via validated internal publication; verifier detects trigger/schema tampering. DDL migration requires separate authorization.","checks":["COMMITTED requires committed_revision + receipt_digest; revision equals intent candidate_revision","MANUAL_CANCELLED requires committed_revision/receipt_digest NULL and all three operator approval fields nonnull; proves witness never advanced for this operation; no automatic timeout, retry or cleanup","Cancellation permanently burns candidate revision: no subsequent mutation in this authority namespace; read facts can qualify only after manual verified cancellation, but further writes require separately approved successor authority. This preserves UNIQUE candidate_revision and prohibition on revision reuse.","Control records excluded from committed logical digest; their deletion still cannot permit B4 START: external B4 fencing/receipts plus witness current-head checks must corroborate clean publication state"],"columns":{"committed_revision":{"not_null":false,"type":"int64_nonnegative"},"disposition":{"not_null":true,"type":"enum","values":["COMMITTED","MANUAL_CANCELLED"]},"fresh_witness_observation_digest":{"not_null":true,"type":"sha256"},"operation_id":{"not_null":true,"type":"uuid"},"operator_approval_path":{"not_null":false,"type":"locator"},"operator_approval_schema":{"not_null":false,"type":"identifier"},"operator_approval_sha256":{"not_null":false,"type":"sha256"},"receipt_digest":{"not_null":false,"type":"sha256"},"resolved_at":{"not_null":true,"type":"utc"}},"constraint_names":{"checks":["ck_publication_resolutions_1","ck_publication_resolutions_2","ck_publication_resolutions_3","ck_publication_resolutions_4"],"foreign_keys":["fk_publication_resolutions_1","fk_publication_resolutions_2"],"primary_key":"pk_publication_resolutions","unique":[]},"foreign_keys":[{"columns":["operation_id"],"deferrable":"INITIALLY_DEFERRED","on_delete":"RESTRICT","on_update":"RESTRICT","references_columns":["operation_id"],"references_table":"publication_intents"},{"columns":["committed_revision"],"deferrable":"INITIALLY_DEFERRED","on_delete":"RESTRICT","on_update":"RESTRICT","references_columns":["revision"],"references_table":"history_revisions"}],"indexes":[],"mutability":"IMMUTABLE_ROWS","name":"publication_resolutions","primary_key":["operation_id"],"purpose":"Append-only manual or successful publication disposition; retain uncertainty history.","strict":true,"unique":[]}],"validation_layers":{"cross_row_verifier":"Recompute complete projection and prefixes, validate continuity, transitions, exact table/index/trigger schema, all counters/refs/timestamps/enums and no extra state. Constraints alone cannot authenticate history.","external":"Fresh independently signed current head + approved trust-root lineage + scope certification. Local writable files never self-authenticate.","sqlite":"Type affinity STRICT, local CHECK/PK/UNIQUE/FK; FK enforcement verified per connection. Logical rules above not expressible in CHECK must be validated explicitly; future INSERT-only triggers may add defense, not replace verification."}}"""  # noqa: E501 -- verbatim frozen contract

SCHEMA_VERSION = "s11-ex10-historical-authority-ledger-v1"
MAX_COUNTER = 9007199254740991
APPROVED_SCHEMA = json.loads(_APPROVED_SCHEMA_JSON)
TABLES = {table["name"]: table for table in APPROVED_SCHEMA["tables"]}
BOOL_COLUMNS = {
    name: {key for key, field in table["columns"].items() if field["type"] == "bool01"}
    for name, table in TABLES.items()
}
ORDERS = APPROVED_SCHEMA["logical_digest"]["ordering"]


class AuthorityError(ValueError):
    """A fail-closed offline contract error, with a sanitized reason code."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require(condition, code="HISTORY_INVALID"):
    if not condition:
        raise AuthorityError(code)


def _canonical_value(value):
    if type(value) is str:
        if not value.isascii() and not (
            unicodedata.normalize("NFC", value) == value
            and _SURROGATE_CODEPOINT_RE.search(value) is None
        ):
            raise AuthorityError("NONCANONICAL")
    elif type(value) is dict:
        require(all(type(key) is str for key in value), "SCHEMA")
        for key, item in value.items():
            _canonical_value(key)
            _canonical_value(item)
    elif type(value) in (list, tuple):
        for item in value:
            _canonical_value(item)
    elif type(value) is int:
        require(abs(value) <= MAX_COUNTER, "SCHEMA")
    else:
        require(value is None or type(value) is bool, "SCHEMA")


def canonical_bytes(value):
    _canonical_value(value)
    return (
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )
        + "\n"
    ).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def parse_canonical(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "DUPLICATE_KEY")
            result[key] = value
        return result

    try:
        require(type(data) is bytes and 0 < len(data) <= 16 * 1024 * 1024, "SCHEMA")
        parsed = json.loads(data.decode("utf-8"), object_pairs_hook=unique)
        require(canonical_bytes(parsed) == data, "NONCANONICAL")
        return parsed
    except (UnicodeError, TypeError, json.JSONDecodeError) as exc:
        raise AuthorityError("SCHEMA") from exc


def validate_schema(value, schema):
    """Validate the closed, bounded subset used by the approved B3 schemas."""
    _canonical_value(value)
    if "anyOf" in schema:
        for alternative in schema["anyOf"]:
            try:
                validate_schema(value, alternative)
                return
            except AuthorityError:
                pass
        raise AuthorityError("SCHEMA")
    if "const" in schema:
        require(type(value) is type(schema["const"]) and value == schema["const"], "SCHEMA")
    if "enum" in schema:
        require(value in schema["enum"] and type(value) is str, "SCHEMA")
    kind = schema.get("type")
    expected = {"object": dict, "array": list, "string": str, "integer": int, "boolean": bool}
    if kind == "null":
        require(value is None, "SCHEMA")
    elif kind:
        require(type(value) is expected[kind], "SCHEMA")
    if kind == "object" or (kind is None and "properties" in schema):
        props = schema["properties"]
        require(set(schema.get("required", [])) <= value.keys(), "SCHEMA")
        if schema.get("additionalProperties") is False:
            require(value.keys() <= props.keys(), "SCHEMA")
        for key, item in value.items():
            if key in props:
                validate_schema(item, props[key])
    elif kind == "array":
        for item in value:
            validate_schema(item, schema["items"])
    elif kind == "string":
        require(len(value) >= schema.get("minLength", 0), "SCHEMA")
        if "pattern" in schema:
            if schema["pattern"] == "^https://":
                # The approved witness endpoint rule is a prefix, not a full regex.
                require(
                    value.startswith("https://")
                    and not any(unicodedata.category(c) in ("Cc", "Zl", "Zp") for c in value),
                    "SCHEMA",
                )
            else:
                require(re.fullmatch(schema["pattern"], value) is not None, "SCHEMA")
    elif kind == "integer":
        require(
            schema.get("minimum", -MAX_COUNTER) <= value <= schema.get("maximum", MAX_COUNTER),
            "SCHEMA",
        )
    for rule in schema.get("allOf", []):
        try:
            validate_schema(value, rule["if"])
            branch = "then"
        except AuthorityError:
            branch = "else"
        validate_schema(value, rule[branch])


def utc_time(value):
    require(
        type(value) is str and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z", value),
        "SCHEMA",
    )
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError as exc:
        raise AuthorityError("SCHEMA") from exc


def validate_scalar(value, field):
    if value is None:
        require(not field["not_null"], "SCHEMA")
        return
    kind = field["type"]
    if kind in ("int64_nonnegative", "positive_int64"):
        require(
            type(value) is int and (1 if kind == "positive_int64" else 0) <= value <= MAX_COUNTER,
            "SCHEMA",
        )
    elif kind == "bool01":
        require(type(value) is bool, "SCHEMA")
    else:
        require(type(value) is str and "\x00" not in value, "SCHEMA")
        patterns = {
            "sha256": r"[0-9a-f]{64}",
            "git_sha1": r"[0-9a-f]{40}",
            "uuid": r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            "run_id": r"s11-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            "identifier": r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}",
            "signature_ed25519": r"[0-9a-f]{128}",
        }
        if kind in patterns:
            require(re.fullmatch(patterns[kind], value) is not None, "SCHEMA")
        elif kind == "utc":
            utc_time(value)
        elif kind == "enum":
            require(value in field["values"], "SCHEMA")
        elif kind == "locator":
            parsed = urlsplit(value)
            if value.startswith("/"):
                require(
                    "//" not in value and all(x not in (".", "..") for x in value.split("/")),
                    "SCHEMA",
                )
            else:
                require(
                    parsed.scheme == "https"
                    and parsed.hostname
                    and not parsed.username
                    and not parsed.password
                    and not parsed.query
                    and not parsed.fragment,
                    "SCHEMA",
                )
                require(all(x not in (".", "..") for x in parsed.path.split("/")), "SCHEMA")
    _canonical_value(value)


def validate_row(name, row):
    require(
        name in TABLES and type(row) is dict and row.keys() == TABLES[name]["columns"].keys(),
        "SCHEMA",
    )
    for key, field in TABLES[name]["columns"].items():
        validate_scalar(row[key], field)
    if name in ("metadata", "bootstrap_manifest"):
        require(row["singleton"] == 1, "SCHEMA")
    if name == "artifact_references":
        require(
            row["reference_id"]
            == digest({k: row[k] for k in ("path", "sha256", "schema_version", "kind")}),
            "SCHEMA",
        )
    elif name == "history_revisions":
        require((row["revision"] == 0) == (row["previous_ledger_digest"] is None), "SCHEMA")
        require(
            row["last_event_sequence"] >= row["first_event_sequence"]
            and row["event_count"] == row["last_event_sequence"] - row["first_event_sequence"] + 1,
            "SCHEMA",
        )
    elif name == "official_runs":
        version, prev = row["run_version"], row["previous_run_version"]
        require(
            (version == 0 and prev is None) or (version > 0 and prev == version - 1),
            "ILLEGAL_TRANSITION",
        )
        require(
            row["created_at"] <= row["opened_at"]
            and (row["finalized_at"] is None or row["finalized_at"] >= row["opened_at"]),
            "SCHEMA",
        )
        state, result = row["lifecycle_state"], row["result"]
        terminal = state == "TERMINAL"
        require(
            terminal
            == (
                row["phase"] == "FINALIZED"
                and result is not None
                and row["finalized_at"] is not None
            ),
            "ILLEGAL_TRANSITION",
        )
        if terminal:
            nd = result == "NOT_DEMONSTRATED"
            require(
                row["binding"] is (not nd)
                and row["ack_required"] is nd
                and row["validity_classification"] == ("NON_BINDING_ND" if nd else "VALID_BINDING"),
                "CONTRADICTORY_RUN",
            )
        else:
            require(
                result is None
                and row["finalized_at"] is None
                and not row["binding"]
                and not row["ack_required"],
                "ILLEGAL_TRANSITION",
            )
            expected = {
                "OPEN": ("OPENED",),
                "ACTIVE": ("EXECUTING", "DRAINED", "RECONCILED"),
                "UNCERTAIN": ("UNCERTAIN",),
            }
            require(
                row["phase"] in expected[state]
                and row["validity_classification"]
                == ("UNCERTAIN" if state == "UNCERTAIN" else "PENDING"),
                "ILLEGAL_TRANSITION",
            )
        if row["provenance_model"] == "PROFILE_V2":
            require(
                row["profile_reference_id"] is not None and row["session_reference_id"] is not None,
                "SCHEMA",
            )
        else:
            require(row["profile_reference_id"] is None, "SCHEMA")
    elif name == "authority_events":
        has_run = row["run_id"] is not None
        require(has_run == (row["run_version"] is not None), "SCHEMA")
        if row["event_kind"].startswith("RUN_") or row["event_kind"] == "IMPORT_RUN":
            require(has_run, "SCHEMA")
        elif row["event_kind"] != "MANUAL_RESOLUTION":
            require(not has_run, "SCHEMA")
        require(
            (row["event_kind"] == "MANUAL_RESOLUTION")
            == (row["resolves_event_sequence"] is not None),
            "SCHEMA",
        )
    elif name == "witness_checkpoints":
        is_genesis = row["revision"] == 0
        require(is_genesis == (row["previous_witness_digest"] is None), "SCHEMA")
        require(
            all((row[k] is not None) == is_genesis for k in row if k.startswith("genesis_")),
            "SCHEMA",
        )
    elif name == "bootstrap_sources":
        require(row["status"] in ("SCANNED", "ABSENT_CERTIFIED"), "INCOMPLETE_BOOTSTRAP")
        require(
            (row["status"] == "SCANNED") == (row["snapshot_reference_id"] is not None), "SCHEMA"
        )
        if row["status"] == "ABSENT_CERTIFIED":
            require(row["discovered_run_count"] == 0, "SCHEMA")
    elif name == "bootstrap_findings":
        require(row["run_version"] == 0, "SCHEMA")
    elif name == "publication_intents":
        zero = row["candidate_revision"] == 0
        require(
            zero == (row["base_revision"] is None) and zero == (row["base_ledger_digest"] is None),
            "SCHEMA",
        )
        require(zero or row["candidate_revision"] == row["base_revision"] + 1, "SCHEMA")
    elif name == "publication_resolutions":
        committed = row["disposition"] == "COMMITTED"
        require(
            committed == (row["committed_revision"] is not None)
            and committed == (row["receipt_digest"] is not None),
            "SCHEMA",
        )
        approval = [
            row[k]
            for k in (
                "operator_approval_path",
                "operator_approval_sha256",
                "operator_approval_schema",
            )
        ]
        require(all(x is None for x in approval) or all(x is not None for x in approval), "SCHEMA")
        require(committed or all(x is not None for x in approval), "SCHEMA")


def empty_state():
    return {name: [] for name in TABLES}


def validate_state(state, *, check_digests=True, check_publication=True):
    require(type(state) is dict and state.keys() == TABLES.keys(), "SCHEMA")
    indexes = {}
    for name, rows in state.items():
        require(type(rows) is list, "SCHEMA")
        table = TABLES[name]
        for row in rows:
            validate_row(name, row)
        for keys in [table["primary_key"], *table["unique"]]:
            values = [
                tuple(row[k] for k in keys) for row in rows if all(row[k] is not None for k in keys)
            ]
            require(
                len(values) == len(set(values)),
                "CONTRADICTORY_RUN" if name == "official_runs" else "HISTORY_INVALID",
            )
        indexes[name] = {tuple(row[k] for k in table["primary_key"]): row for row in rows}
    for name, rows in state.items():
        for foreign in TABLES[name]["foreign_keys"]:
            targets = {
                tuple(row[k] for k in foreign["references_columns"])
                for row in state[foreign["references_table"]]
            }
            for row in rows:
                value = tuple(row[k] for k in foreign["columns"])
                require(any(x is None for x in value) or value in targets, "HISTORY_INVALID")
    require(len(state["metadata"]) == len(state["bootstrap_manifest"]) == 1, "INCOMPLETE_BOOTSTRAP")
    metadata, bootstrap = state["metadata"][0], state["bootstrap_manifest"][0]
    revisions = sorted(state["history_revisions"], key=lambda row: row["revision"])
    require(
        revisions and [r["revision"] for r in revisions] == list(range(len(revisions))),
        "REVISION_GAP",
    )
    head = len(revisions) - 1
    require(
        all(
            r["introduced_revision"] <= head
            for t in ("official_runs", "artifact_references")
            for r in state[t]
        ),
        "REVISION_GAP",
    )
    events = sorted(state["authority_events"], key=lambda row: row["event_sequence"])
    require(
        [e["event_sequence"] for e in events] == list(range(1, len(events) + 1)), "REVISION_GAP"
    )
    require(sum(e["event_kind"] == "GENESIS" for e in events) == 1, "INCOMPLETE_BOOTSTRAP")
    next_event = 1
    for revision in revisions:
        selected = [e for e in events if e["revision"] == revision["revision"]]
        require(
            selected
            and revision["first_event_sequence"] == next_event
            and len(selected) == revision["event_count"]
            and selected[-1]["event_sequence"] == revision["last_event_sequence"],
            "REVISION_GAP",
        )
        next_event = revision["last_event_sequence"] + 1
        require(all(e["revision"] == 0 for e in selected if e["event_kind"] == "GENESIS"), "SCHEMA")
    require(all(e["revision"] <= head for e in events), "REVISION_GAP")
    resolved_targets = [
        e["resolves_event_sequence"] for e in events if e["resolves_event_sequence"] is not None
    ]
    require(len(resolved_targets) == len(set(resolved_targets)), "ILLEGAL_TRANSITION")
    for event in events:
        if event["run_id"] is not None:
            run = indexes["official_runs"][(event["run_id"], event["run_version"])]
            require(run["introduced_revision"] == event["revision"], "CONTRADICTORY_RUN")
        if event["resolves_event_sequence"] is not None:
            previous = indexes["authority_events"][(event["resolves_event_sequence"],)]
            require(
                previous["event_sequence"] < event["event_sequence"]
                and (
                    previous["event_kind"] in ("RUN_UNCERTAIN", "AUTHORITY_UNCERTAIN")
                    or (
                        previous["event_kind"] == "IMPORT_RUN"
                        and previous["run_id"] is not None
                        and indexes["official_runs"][(previous["run_id"], previous["run_version"])][
                            "lifecycle_state"
                        ]
                        == "UNCERTAIN"
                    )
                ),
                "ILLEGAL_TRANSITION",
            )
            require(previous["run_id"] == event["run_id"], "ILLEGAL_TRANSITION")
            require(
                indexes["artifact_references"][(event["evidence_reference_id"],)]["kind"]
                == "APPROVAL",
                "TRUST_UNAPPROVED",
            )
    latest = {}
    immutable = (
        "source_revision",
        "protocol",
        "methodology",
        "mode",
        "provenance_model",
        "created_at",
        "opened_at",
        "profile_reference_id",
        "session_reference_id",
    )
    for run in sorted(state["official_runs"], key=lambda r: (r["run_id"], r["run_version"])):
        previous = latest.get(run["run_id"])
        require(
            run["run_version"] == (0 if previous is None else previous["run_version"] + 1),
            "ILLEGAL_TRANSITION",
        )
        if previous:
            require(all(previous[k] == run[k] for k in immutable), "CONTRADICTORY_RUN")
            require(
                run["introduced_revision"] > previous["introduced_revision"]
                and run["lifecycle_state"]
                in APPROVED_SCHEMA["state_transitions"][previous["lifecycle_state"]],
                "ILLEGAL_TRANSITION",
            )
            if previous["lifecycle_state"] == "UNCERTAIN" and run["lifecycle_state"] != "UNCERTAIN":
                require(
                    any(
                        e["event_kind"] == "MANUAL_RESOLUTION"
                        and e["run_id"] == run["run_id"]
                        and e["revision"] == run["introduced_revision"]
                        and indexes["authority_events"][(e["resolves_event_sequence"],)][
                            "run_version"
                        ]
                        == previous["run_version"]
                        for e in events
                    ),
                    "ILLEGAL_TRANSITION",
                )
        latest[run["run_id"]] = run
    require(sum(r["binding"] for r in latest.values()) <= 1, "CONTRADICTORY_RUN")
    require(
        metadata["bootstrap_reference_id"] == bootstrap["manifest_reference_id"]
        and metadata["source_baseline_head"] == bootstrap["baseline_head"]
        and metadata["source_baseline_tree"] == bootstrap["baseline_tree"],
        "INCOMPLETE_BOOTSTRAP",
    )
    require(bootstrap["source_count"] == len(state["bootstrap_sources"]), "INCOMPLETE_BOOTSTRAP")
    imported = [r for r in state["official_runs"] if r["introduced_revision"] == 0]
    require(bootstrap["run_count"] == len(imported), "INCOMPLETE_BOOTSTRAP")
    for run in imported:
        findings = [f for f in state["bootstrap_findings"] if f["run_id"] == run["run_id"]]
        require(
            findings and any(f["is_primary_evidence"] for f in findings), "INCOMPLETE_BOOTSTRAP"
        )
    for source in state["bootstrap_sources"]:
        require(
            source["scope_reference_id"] == bootstrap["scope_reference_id"], "INCOMPLETE_BOOTSTRAP"
        )
        require(
            source["discovered_run_count"]
            == sum(f["source_id"] == source["source_id"] for f in state["bootstrap_findings"]),
            "INCOMPLETE_BOOTSTRAP",
        )
    checkpoints = sorted(state["witness_checkpoints"], key=lambda r: r["revision"])
    require([r["revision"] for r in checkpoints] == list(range(head + 1)), "HISTORY_INVALID")
    for cp, revision in zip(checkpoints, revisions, strict=True):
        require(
            cp["authority_id"] == metadata["authority_id"]
            and cp["authority_epoch"] == metadata["epoch"]
            and cp["operation_id"] == revision["operation_id"]
            and cp["ledger_digest"] == revision["ledger_digest"],
            "FORK_DETECTED",
        )
    for resolution in state["publication_resolutions"]:
        intent = indexes["publication_intents"][(resolution["operation_id"],)]
        if resolution["disposition"] == "COMMITTED":
            require(
                resolution["committed_revision"] == intent["candidate_revision"], "FORK_DETECTED"
            )
            cp = checkpoints[resolution["committed_revision"]]
            require(
                cp["operation_id"] == intent["operation_id"]
                and cp["ledger_digest"] == intent["candidate_ledger_digest"]
                and cp["witness_digest"] == resolution["receipt_digest"],
                "FORK_DETECTED",
            )
    for intent in state["publication_intents"]:
        require(
            intent["candidate_payload_sha256"] == intent["candidate_ledger_digest"]
            and intent["candidate_revision"] <= head + 1,
            "DIGEST_MISMATCH",
        )
        if intent["base_revision"] is not None:
            require(
                intent["base_revision"] <= head
                and revisions[intent["base_revision"]]["ledger_digest"]
                == intent["base_ledger_digest"],
                "FORK_DETECTED",
            )
    if check_publication:
        committed = [
            r["committed_revision"]
            for r in state["publication_resolutions"]
            if r["disposition"] == "COMMITTED"
        ]
        require(sorted(committed) == list(range(head + 1)), "UNCERTAIN_PUBLICATION")
        cancelled = [
            r for r in state["publication_resolutions"] if r["disposition"] == "MANUAL_CANCELLED"
        ]
        require(
            not cancelled
            or all(
                indexes["publication_intents"][(r["operation_id"],)]["candidate_revision"]
                == head + 1
                for r in cancelled
            ),
            "ILLEGAL_TRANSITION",
        )
    if check_digests:
        previous = None
        for revision in revisions:
            require(revision["previous_ledger_digest"] == previous, "FORK_DETECTED")
            previous = logical_digest(state, revision["revision"])
            require(previous == revision["ledger_digest"], "DIGEST_MISMATCH")
    return latest


def logical_projection(state, revision):
    require(type(revision) is int and 0 <= revision <= MAX_COUNTER, "SCHEMA")
    require(state["metadata"] and state["bootstrap_manifest"], "INCOMPLETE_BOOTSTRAP")
    result = {"schema_version": "s11-ex10-authority-logical-state-v1", "revision": revision}
    for name in APPROVED_SCHEMA["logical_digest"]["fields"]:
        if name in ("schema_version", "revision"):
            continue
        if name in ("metadata", "bootstrap_manifest"):
            require(len(state[name]) == 1, "SCHEMA")
            result[name] = copy.deepcopy(state[name][0])
            continue
        rows = state[name]
        if name in ("official_runs", "artifact_references"):
            rows = [r for r in rows if r["introduced_revision"] <= revision]
        elif name in ("authority_events", "history_revisions"):
            rows = [r for r in rows if r["revision"] <= revision]
        rows = sorted(rows, key=lambda row: tuple(row[k] for k in ORDERS[name]))
        result[name] = [
            {
                k: v
                for k, v in row.items()
                if not (name == "history_revisions" and k == "ledger_digest")
            }
            for row in rows
        ]
    return result


def logical_digest(state, revision):
    return digest(logical_projection(state, revision))


def _sql_scalar(key, field):
    q = '"' + key + '"'
    kind = field["type"]
    if kind in ("int64_nonnegative", "positive_int64"):
        expr = f"{q} BETWEEN {1 if kind == 'positive_int64' else 0} AND {MAX_COUNTER}"
    elif kind == "bool01":
        expr = f"{q} IN (0,1)"
    elif kind == "enum":
        expr = q + " IN (" + ",".join("'" + v + "'" for v in field["values"]) + ")"
    elif kind in ("sha256", "git_sha1", "signature_ed25519"):
        size = {"sha256": 64, "git_sha1": 40, "signature_ed25519": 128}[kind]
        expr = f"length({q})={size} AND {q} NOT GLOB '*[^0-9a-f]*'"
    elif kind in ("uuid", "run_id"):
        value = q if kind == "uuid" else f"substr({q},5)"
        expr = (
            f"length({value})=36 AND substr({value},9,1)='-' AND substr({value}"
            + f",14,1)='-' AND substr({value},19,1)='-' AND substr({value},24"
            + f",1)='-' AND length(replace({value},'-',''))=32 AND replace"
            + f"({value},'-','') NOT GLOB '*[^0-9a-f]*'"
        )
        if kind == "run_id":
            expr = f"substr({q},1,4)='s11-' AND " + expr
    elif kind == "identifier":
        expr = (
            f"length({q}) BETWEEN 1 AND 128 AND substr({q},1,1) GLO"
            + f"B '[A-Za-z0-9]' AND {q} NOT GLOB '*[^A-Za-z0-9_.:-]*'"
        )
    elif kind == "utc":
        expr = (
            f"length({q})=27 AND {q} GLOB '[0-9][0-9][0-9][0-9]-[0-"
            + "9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-"
            + "9][0-9][0-9][0-9][0-9][0-9]Z'"
        )
    else:
        expr = f"(substr({q},1,1)='/' OR substr({q},1,8)='https://') AND instr({q},char(0))=0"
    return f"({q} IS NULL OR ({expr}))" if not field["not_null"] else "(" + expr + ")"


def _local_checks(name):
    """Approved rule numbers; non-SQL rules are explicitly mapped below."""
    return {
        "metadata": {1: "singleton=1"},
        "artifact_references": {},
        "bootstrap_manifest": {1: "singleton=1"},
        "history_revisions": {
            1: "(revision=0)=(previous_ledger_digest IS NULL)",
            2: "last_event_sequence>=first_event_sequence",
            3: "event_count=last_event_sequence-first_event_sequence+1",
        },
        "official_runs": {
            1: (
                "(run_version=0 AND previous_run_version IS NULL) OR "
                "(run_version>0 AND previous_run_version=run_version-1)"
            ),
            2: "created_at<=opened_at AND (finalized_at IS NULL OR finalized_at>=opened_at)",
            3: (
                "(lifecycle_state='TERMINAL')=(phase='FINALIZED' AND result "
                "IS NOT NULL AND finalized_at IS NOT NULL)"
            ),
            4: (
                "lifecycle_state='TERMINAL' OR (result IS NULL AND "
                "finalized_at IS NULL AND binding=0 AND ack_required=0)"
            ),
            5: (
                "NOT(lifecycle_state='TERMINAL' AND result IN "
                "('PASS','FAIL')) OR (binding=1 AND "
                "validity_classification='VALID_BINDING' AND "
                "ack_required=0)"
            ),
            6: (
                "NOT(lifecycle_state='TERMINAL' AND "
                "result='NOT_DEMONSTRATED') OR (binding=0 AND "
                "validity_classification='NON_BINDING_ND' AND "
                "ack_required=1)"
            ),
            7: (
                "lifecycle_state='TERMINAL' OR (lifecycle_state='OPEN' AND "
                "phase='OPENED' AND validity_classification='PENDING') OR "
                "(lifecycle_state='ACTIVE' AND phase IN "
                "('EXECUTING','DRAINED','RECONCILED') AND "
                "validity_classification='PENDING') OR "
                "(lifecycle_state='UNCERTAIN' AND phase='UNCERTAIN' AND "
                "validity_classification='UNCERTAIN')"
            ),
            8: (
                "(provenance_model='PROFILE_V2' AND profile_reference_id IS "
                "NOT NULL AND session_reference_id IS NOT NULL) OR "
                "(provenance_model='HISTORICAL_V1' AND profile_reference_id "
                "IS NULL)"
            ),
        },
        "authority_events": {
            1: "(run_id IS NULL)=(run_version IS NULL)",
            2: (
                "(event_kind IN "
                "('IMPORT_RUN','RUN_OPENED','RUN_STATE','RUN_TERMINAL','RUN_"
                "UNCERTAIN') AND run_id IS NOT NULL) OR (event_kind IN "
                "('GENESIS','AUTHORITY_UNCERTAIN','COVERAGE_EXTENSION') AND "
                "run_id IS NULL) OR event_kind='MANUAL_RESOLUTION'"
            ),
            3: "(event_kind='MANUAL_RESOLUTION')=(resolves_event_sequence IS NOT NULL)",
        },
        "witness_checkpoints": {
            1: "(revision=0)=(previous_witness_digest IS NULL)",
            4: " AND ".join(
                f"(revision=0)=({k} IS NOT NULL)"
                for k in TABLES["witness_checkpoints"]["columns"]
                if k.startswith("genesis_")
            ),
        },
        "bootstrap_sources": {
            1: (
                "(status='SCANNED' AND snapshot_reference_id IS NOT NULL) "
                "OR (status='ABSENT_CERTIFIED' AND snapshot_reference_id IS "
                "NULL AND discovered_run_count=0)"
            ),
            2: "status IN ('SCANNED','ABSENT_CERTIFIED')",
        },
        "bootstrap_findings": {1: "run_version=0"},
        "publication_intents": {
            1: (
                "(candidate_revision=0 AND base_revision IS NULL AND "
                "base_ledger_digest IS NULL) OR (candidate_revision>0 AND "
                "base_revision IS NOT NULL AND base_ledger_digest IS NOT "
                "NULL AND candidate_revision=base_revision+1)"
            )
        },
        "publication_resolutions": {
            1: (
                "disposition!='COMMITTED' OR (committed_revision IS NOT "
                "NULL AND receipt_digest IS NOT NULL)"
            ),
            2: (
                "disposition!='MANUAL_CANCELLED' OR (committed_revision IS "
                "NULL AND receipt_digest IS NULL AND operator_approval_path "
                "IS NOT NULL AND operator_approval_sha256 IS NOT NULL AND "
                "operator_approval_schema IS NOT NULL)"
            ),
        },
    }[name]


def _check_coverage():
    """Trace each frozen rule to SQL plus mandatory complete verification."""
    return {
        name: [
            {
                "name": f"ck_{name}_{number}",
                "approved_rule": rule,
                "sql_expression": _local_checks(name).get(number),
                "verifier": (
                    "validate_row + validate_state; cryptographic/custody rules "
                    "additionally require verify_authority"
                ),
            }
            for number, rule in enumerate(table["checks"], 1)
        ]
        for name, table in TABLES.items()
    }


def schema_statements():
    statements = []
    for name, table in TABLES.items():
        parts = []
        for key, field in table["columns"].items():
            kind = (
                "INTEGER"
                if field["type"] in ("int64_nonnegative", "positive_int64", "bool01")
                else "TEXT"
            )
            parts.append(
                f'"{key}" {kind}'
                + (" NOT NULL" if field["not_null"] else "")
                + " CHECK "
                + _sql_scalar(key, field)
            )
        parts.append(
            "CONSTRAINT pk_" + name + " PRIMARY KEY (" + ",".join(table["primary_key"]) + ")"
        )
        for i, keys in enumerate(table["unique"], 1):
            parts.append(f"CONSTRAINT uq_{name}_{i} UNIQUE (" + ",".join(keys) + ")")
        for i, foreign in enumerate(table["foreign_keys"], 1):
            parts.append(
                f"CONSTRAINT fk_{name}_{i} FOREIGN KEY ("
                + ",".join(foreign["columns"])
                + ") REFERENCES "
                + foreign["references_table"]
                + "("
                + ",".join(foreign["references_columns"])
                + ") ON DELETE RESTRICT ON UPDATE RESTRICT DEFERRABLE INITIALLY DEFERRED"
            )
        parts += [
            f"CONSTRAINT ck_{name}_{i} CHECK ({check})" for i, check in _local_checks(name).items()
        ]
        statements.append("CREATE TABLE " + name + " (" + ",".join(parts) + ") STRICT")
        for index in table["indexes"]:
            statements.append(
                "CREATE INDEX "
                + index["name"]
                + " ON "
                + name
                + "("
                + ",".join(index["columns"])
                + ")"
            )
        conflicts = []
        for keys in [table["primary_key"], *table["unique"]]:
            conflicts.append("(" + " AND ".join(f"{k}=NEW.{k}" for k in keys) + ")")
        statements.append(
            (
                f"CREATE TRIGGER reject_replace_{name} BEFORE INSERT ON {name}"
                + f" WHEN EXISTS (SELECT 1 FROM {name} WHERE "
            )
            + " OR ".join(conflicts)
            + ") BEGIN SELECT RAISE(ABORT, 'APPEND_ONLY'); END"
        )
        for action in ("UPDATE", "DELETE"):
            statements.append(
                f"CREATE TRIGGER reject_{action.lower()}_{name} BEFORE {action} ON {name} BEG"
                + "IN SELECT RAISE(ABORT, 'APPEND_ONLY'); END"
            )
    return tuple(statements)


def _path_readonly(path):
    path = Path(path)
    require(path.is_absolute(), "UNSAFE_PATH")
    for parent in [*reversed(path.parents), path]:
        info = parent.lstat()
        require(not stat.S_ISLNK(info.st_mode), "UNSAFE_PATH")
    info = path.lstat()
    require(
        stat.S_ISREG(info.st_mode)
        and info.st_nlink == 1
        and info.st_uid == os.getuid()
        and stat.S_IMODE(info.st_mode) == 0o600,
        "UNSAFE_PATH",
    )
    require(stat.S_IMODE(path.parent.lstat().st_mode) == 0o700, "UNSAFE_PATH")
    require(
        not any(Path(str(path) + suffix).exists() for suffix in ("-journal", "-wal", "-shm")),
        "RECOVERY_REQUIRED",
    )
    return path


def _connect_readonly(path):
    path = _path_readonly(path)
    con = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0)
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA trusted_schema=OFF")
    con.execute("PRAGMA recursive_triggers=ON")
    require(con.execute("PRAGMA recursive_triggers").fetchone()[0] == 1, "CONFIGURATION_MISMATCH")
    con.execute("PRAGMA synchronous=EXTRA")
    require(
        con.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        and con.execute("PRAGMA synchronous").fetchone()[0] == 3,
        "CONFIGURATION_MISMATCH",
    )
    con.execute("PRAGMA query_only=ON")
    return con


def _create_test_database(path):
    """Internal test harness only; no operational creation entrypoint."""
    path = Path(path)
    require("s11-b3-test-" in path.parent.name and not path.exists(), "TEST_DOMAIN_REQUIRED")
    require(path.parent.is_dir() and not path.parent.is_symlink(), "UNSAFE_PATH")
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    con = sqlite3.connect(path, timeout=0)
    con.execute("PRAGMA journal_mode=DELETE")
    con.execute("PRAGMA synchronous=EXTRA")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA trusted_schema=OFF")
    con.execute("PRAGMA recursive_triggers=ON")
    require(con.execute("PRAGMA recursive_triggers").fetchone()[0] == 1, "CONFIGURATION_MISMATCH")
    require(
        con.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        and con.execute("PRAGMA synchronous").fetchone()[0] == 3,
        "CONFIGURATION_MISMATCH",
    )
    for statement in schema_statements():
        con.execute(statement)
    con.commit()
    return con


def _verify_sql_schema(con):
    expected = {}
    for statement in schema_statements():
        words = statement.split()
        expected[(words[1].lower(), words[2])] = statement
    actual = {
        (kind, name): sql
        for kind, name, sql in con.execute(
            "SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL"
        )
    }
    require(actual == expected, "SCHEMA_TAMPERED")
    require(
        con.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        and not con.execute("PRAGMA foreign_key_check").fetchall(),
        "HISTORY_INVALID",
    )


def _read_state(con):
    _verify_sql_schema(con)
    result = {}
    for name, table in TABLES.items():
        columns = list(table["columns"])
        rows = con.execute("SELECT " + ",".join(columns) + " FROM " + name).fetchall()
        result[name] = [
            {
                k: bool(v) if k in BOOL_COLUMNS[name] else v
                for k, v in zip(columns, row, strict=True)
            }
            for row in rows
        ]
    return result


def _load_database(path):
    con = _connect_readonly(path)
    try:
        con.execute("BEGIN")
        state = _read_state(con)
        con.rollback()
        return state
    finally:
        con.close()


def _insert_rows(con, additions):
    """Internal primitive; caller must hold externally verified future B4 serialization."""
    for name in TABLES:
        for row in additions.get(name, []):
            validate_row(name, row)
            cols = list(TABLES[name]["columns"])
            values = [int(row[k]) if k in BOOL_COLUMNS[name] else row[k] for k in cols]
            con.execute(
                "INSERT INTO "
                + name
                + " ("
                + ",".join(cols)
                + ") VALUES ("
                + ",".join("?" for _ in cols)
                + ")",
                values,
            )


def _ordered_state(state):
    return {
        name: sorted(rows, key=lambda r: tuple(r[k] for k in TABLES[name]["primary_key"]))
        for name, rows in state.items()
    }


def _backup_test_database(source, target, *, verified_source):
    """Offline consistent snapshot bound to an unexpired verified authority."""
    from .ex10_history_authority import _verified

    material = _verified(verified_source)
    require(
        not verified_source.authority_uncertainty and not verified_source.write_closed,
        "UNCERTAIN_PUBLICATION",
    )
    target = Path(target)
    require("s11-b3-test-" in target.parent.name and not target.exists(), "TEST_DOMAIN_REQUIRED")
    con = _connect_readonly(source)
    destination = None
    try:
        con.execute("BEGIN")
        original = _read_state(con)
        validate_state(original)
        require(_ordered_state(original) == _ordered_state(material["state"]), "DIGEST_MISMATCH")
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        destination = sqlite3.connect(target, timeout=0)
        destination.execute("PRAGMA journal_mode=DELETE")
        destination.execute("PRAGMA synchronous=EXTRA")
        destination.execute("PRAGMA foreign_keys=ON")
        destination.execute("PRAGMA trusted_schema=OFF")
        destination.execute("PRAGMA recursive_triggers=ON")
        require(
            destination.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
            and destination.execute("PRAGMA synchronous").fetchone()[0] == 3,
            "CONFIGURATION_MISMATCH",
        )
        con.backup(destination)
        copied = _read_state(destination)
        validate_state(copied)
        require(_ordered_state(original) == _ordered_state(copied), "DIGEST_MISMATCH")
        destination.close()
        destination = None
        for path in (target, target.parent):
            fd = os.open(path, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        head = max(copied["history_revisions"], key=lambda r: r["revision"])
        return {
            "authority_id": copied["metadata"][0]["authority_id"],
            "epoch": copied["metadata"][0]["epoch"],
            "revision": head["revision"],
            "ledger_digest": head["ledger_digest"],
            "witness_digest": max(copied["witness_checkpoints"], key=lambda r: r["revision"])[
                "witness_digest"
            ],
            "snapshot_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        }
    finally:
        if destination is not None:
            destination.close()
        con.close()
