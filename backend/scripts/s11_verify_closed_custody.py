#!/usr/bin/env python3
"""Verificador read-only de la custodia S11 ya cerrada (`S11-CUSTODY-v1.0`).

Comprueba, sin escribir un solo byte, la evidencia finalizada de las tres raíces de custodia:

- primary: `runs/<run_id>/`, `custody-verifications/<verification_id>/` y los directorios
  transitorios `custody-transactions/` y `finalization-transactions/`, que deben estar vacíos;
- backup y restore: `runs/<run_id>/`, que deben ser copias exactas de primary y de backup.

Por cada run verifica:

- el marcador y el índice `metadata/artifacts.jsonl`, canónico y con HMAC;
- cada registro: esquema, tipos, ruta, SHA-256, tamaño, firma HMAC y aprobación PUBLIC;
- que ningún archivo quede fuera del índice;
- el sello `.s11-finalized` y su vínculo con el manifest final;
- las referencias y la procedencia del manifest;
- la retención anclada a `finished_at`;
- `SHA256SUMS` contra el árbol completo.

Por cada verificación custodial comprueba cada bundle `.json` + `.sha256` + `.hmac` + `.commit`,
el conjunto mínimo de registros y los hashes cruzados entre ellos.

La completitud, es decir que no falten unidades enteras, se contrasta con los identificadores
citados en los documentos versionados (`--baseline-git-commit`) o con un reporte anterior
(`--compare-report`). Sin ninguna de esas bases el resultado es `INCOMPLETE`.

Las unidades declaradas `HISTORICAL_UNVERIFIABLE` (`--historical`) sólo se verifican en sus
bytes y exigen línea base. Si su HMAC resulta verificable con la clave dada, la declaración es
errónea y se falla. Si ningún documento previo las ancla, el resultado global es
`PASS_WITH_UNANCHORED_HISTORICAL` (salida 4), nunca `PASS`.

La clave custodial se recibe **exclusivamente** por la variable de entorno
`S11_CUSTODY_HMAC_KEY`. Nunca llega por argumento, archivo, stdout, log, traceback ni JSON de
salida. Sólo se informa su huella no reversible: 16 hexadecimales del SHA-256. El proceso la
retira de `os.environ` al leerla, aunque `/proc/<pid>/environ` conserva el entorno inicial del
proceso.

Garantías read-only:

- nunca instancia `EvidenceStore` ni `CustodyManager`: sus APIs crean directorios, cambian
  permisos o toman locks aunque sólo se quiera leer;
- sólo usa las funciones puras de `custody_policy`;
- lee con `os.open(O_RDONLY | O_NOFOLLOW | O_NONBLOCK)` y sólo archivos regulares, y lista con
  `os.scandir` sin seguir enlaces;
- como proceso instala un audit hook que aborta ante aperturas en escritura, cambios de permisos,
  borrados, renombrados, directorios nuevos, locks, xattr, sockets y procesos hijos que no sean
  las tres lecturas de `git` exactas de la línea base. Además desactiva el bytecode.

El hook no alcanza a procesos hijos ni a escrituras hechas desde C. La garantía fuerte es
ejecutarlo con un usuario sin permiso de escritura o sobre un montaje de sólo lectura.

Ejecutar siempre con `python3 -B` (o `PYTHONDONTWRITEBYTECODE=1`): el intérprete compila módulos
de arranque antes de que este archivo pueda impedirlo, así que la CLI se niega a ejecutarse sin
esa opción.

Un archivo o directorio ilegible nunca se ignora: el resultado queda `INCOMPLETE`.

Códigos de salida:

- 0: `PASS`;
- 1: `FAIL`;
- 2: `INCOMPLETE`;
- 3: error de uso o de configuración;
- 4: `PASS_WITH_UNANCHORED_HISTORICAL`.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse  # noqa: E402
import errno  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import stat  # noqa: E402
import subprocess  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from datetime import UTC, datetime  # noqa: E402
from pathlib import Path, PurePosixPath  # noqa: E402
from typing import Any  # noqa: E402

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from apps.experiments_app.canonical import canonical_json_bytes  # noqa: E402
from apps.experiments_app.constants import ArtifactKind, Classification  # noqa: E402
from apps.experiments_app.custody_policy import (  # noqa: E402
    PUBLICATION_POLICY_VERSION,
    CustodyPolicyError,
    PublicationApproval,
    expiration_for,
    validate_custody_index_signature,
    validate_custody_record_signature,
    validate_custody_seal_signature,
    validate_custody_verification_signature,
    validate_public_approval,
)

SCHEMA_VERSION = "s11-closed-custody-verification-v1"
KEY_ENV = "S11_CUSTODY_HMAC_KEY"
PUBLICATION_KEY_ENV = "S11_PUBLICATION_APPROVAL_PUBLIC_KEY"

PASS = "PASS"
FAIL = "FAIL"
INCOMPLETE = "INCOMPLETE"
PASS_WITH_UNANCHORED_HISTORICAL = "PASS_WITH_UNANCHORED_HISTORICAL"
HISTORICAL_ANCHORED = "HISTORICAL_ANCHORED"
HISTORICAL_UNANCHORED = "HISTORICAL_UNANCHORED"
UNIT_STATUSES = (PASS, FAIL, INCOMPLETE, HISTORICAL_ANCHORED, HISTORICAL_UNANCHORED)
EXIT_CODES = {PASS: 0, FAIL: 1, INCOMPLETE: 2, PASS_WITH_UNANCHORED_HISTORICAL: 4}
EXIT_ERROR = 3

SEVERITY_FAIL = "fail"
SEVERITY_INCOMPLETE = "incomplete"
SEVERITY_WARNING = "warning"

MARKER = ".s11-evidence-root"
MARKER_CONTENT = b"S11-B persistent evidence root\n"
SEAL = ".s11-finalized"
INDEX = "metadata/artifacts.jsonl"
INDEX_SIGNATURE = "metadata/artifacts.jsonl.hmac"
CHECKSUMS = "SHA256SUMS"
RUN_CONTROL_FILES = frozenset({MARKER, SEAL, CHECKSUMS, INDEX, INDEX_SIGNATURE})
#: Canario sintético de P02/P03 que nunca debe persistirse (política §11).
CANARY_SECRET = b"S11-CANARY-MUST-NOT-PERSIST"
#: Campos exactos de `evidence.ArtifactRecord` tal como se persisten en el índice.
ARTIFACT_RECORD_FIELDS = frozenset(
    {
        "artifact_id",
        "run_id",
        "scenario_id",
        "artifact_type",
        "kind",
        "classification",
        "relative_path",
        "created_at",
        "expires_at",
        "retention_anchor",
        "retention_started_at",
        "size_bytes",
        "mime_type",
        "sha256",
        "source",
        "transformation",
        "parent_artifact_id",
        "publication_approval",
        "custody_signature",
    }
)
_REQUIRED_TEXT_FIELDS = (
    "artifact_id",
    "run_id",
    "scenario_id",
    "artifact_type",
    "kind",
    "classification",
    "relative_path",
    "created_at",
    "retention_anchor",
    "retention_started_at",
    "mime_type",
    "sha256",
    "source",
)
_OPTIONAL_TEXT_FIELDS = ("expires_at", "transformation", "parent_artifact_id", "custody_signature")
#: Campos que el manifest final puede cambiar respecto del inicial (`evidence.py`).
MANIFEST_MUTABLE_FIELDS = frozenset(
    {"artifact_references", "finished_at", "invalidity_reason", "status"}
)
FINAL_MANIFEST_STATUSES = frozenset({"COMPLETED", "FAILED", "INVALID"})
PRIMARY_ROOT_ENTRIES = frozenset(
    {
        "runs",
        "custody-verifications",
        "custody-transactions",
        "finalization-transactions",
        "control",
        ".s11-custody.lock",
    }
)
#: Fuera de `S11-CUSTODY-v1.0`: se informa, pero ninguna firma lo autentica.
UNCOVERED_PRIMARY_ENTRIES = frozenset({"control"})
COPY_ROOT_ENTRIES = frozenset({"runs"})
PENDING_ENTRY_CODES = {
    "custody-transactions": "pending-custody-transaction",
    "finalization-transactions": "pending-finalization-journal",
}
BUNDLE_SIDECARS = (".sha256", ".hmac", ".commit")

_SHA256 = re.compile(r"[0-9a-f]{64}")
_SHA256_IN_TEXT = re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")
_SIGNATURE_LINE = re.compile(rb"[0-9a-f]{64}\n")
_CHECKSUM_LINE = re.compile(r"([0-9a-f]{64})  (.+)")
_SAFE_SUBJECT = re.compile(r"[A-Za-z0-9._:/@+-]{1,512}")
_SAFE_NAME = re.compile(r"[A-Za-z0-9._@+-]{1,255}")
_FINGERPRINT = re.compile(r"[0-9a-f]{16}")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_DOC_PATH = re.compile(r"docs/[A-Za-z0-9._/-]+\.md")
_DOCUMENTED_RUN_ID = re.compile(
    r"\bs11-[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b"
)
_DOCUMENTED_VERIFICATION_ID = re.compile(r"\bs11-(?:custody|c1)-\d{8}-[a-z0-9-]*?-v\d+\b")
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
_READ_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NONBLOCK", 0)
)
_GIT_EXECUTABLE = shutil.which("git")


class VerifierConfigurationError(Exception):
    """Uso o configuración inválidos. `code` es un identificador sin datos."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class WriteAttemptError(RuntimeError):
    pass


def _subject(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if _SAFE_SUBJECT.fullmatch(text) else "[unprintable]"


@dataclass(frozen=True, slots=True)
class Finding:
    severity: str
    code: str
    subject: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {"severity": self.severity, "code": self.code, "subject": self.subject}


@dataclass(slots=True)
class Unit:
    """Una unidad de evidencia: un run o una verificación custodial dentro de una raíz."""

    root_label: str
    relative: str
    path: Path
    kind: str
    historical: bool = False
    findings: list[Finding] = field(default_factory=list)
    inventory: dict[str, str] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)
    unreadable: list[str] = field(default_factory=list)
    records: list[dict[str, Any]] = field(default_factory=list)
    payloads: dict[str, Any] = field(default_factory=dict)
    anchored: bool | None = None

    @property
    def unit_id(self) -> str:
        return f"{self.root_label}:{self.relative}"

    @property
    def name(self) -> str:
        return self.path.name

    def fail(self, code: str, subject: Any = None) -> None:
        self.findings.append(Finding(SEVERITY_FAIL, code, _subject(subject)))

    def incomplete(self, code: str, subject: Any = None) -> None:
        self.findings.append(Finding(SEVERITY_INCOMPLETE, code, _subject(subject)))

    def warn(self, code: str, subject: Any = None) -> None:
        self.findings.append(Finding(SEVERITY_WARNING, code, _subject(subject)))

    def fail_or_incomplete(self, code: str, relative: str) -> None:
        """Lo que falta bajo una ruta ilegible no prueba un fallo: queda incompleto."""
        if self.under_unreadable(relative):
            self.incomplete(code, relative)
        else:
            self.fail(code, relative)

    def under_unreadable(self, relative: str) -> bool:
        return any(
            prefix == "." or relative == prefix or relative.startswith(prefix + "/")
            for prefix in self.unreadable
        )

    @property
    def has_incomplete(self) -> bool:
        return any(finding.severity == SEVERITY_INCOMPLETE for finding in self.findings)

    @property
    def status(self) -> str:
        severities = {finding.severity for finding in self.findings}
        if SEVERITY_FAIL in severities:
            return FAIL
        if SEVERITY_INCOMPLETE in severities:
            return INCOMPLETE
        if self.historical:
            return HISTORICAL_ANCHORED if self.anchored else HISTORICAL_UNANCHORED
        return PASS

    def to_json(self) -> dict[str, Any]:
        return {
            "unit": self.unit_id,
            "kind": self.kind,
            "historical": self.historical,
            "status": self.status,
            "findings": [f.to_json() for f in self.findings if f.severity != SEVERITY_WARNING],
            "warnings": [f.to_json() for f in self.findings if f.severity == SEVERITY_WARNING],
            "stats": dict(sorted(self.stats.items())),
            "inventory": dict(sorted(self.inventory.items())),
        }


@dataclass(frozen=True, slots=True)
class VerificationContext:
    custody_key: bytes
    publication_key: bytes | None


@dataclass(frozen=True, slots=True)
class Baseline:
    """Línea base previa: hashes e identificadores citados en documentos versionados."""

    commit: str | None
    anchors: dict[str, list[str]]
    documented_units: frozenset[str]


# --- Lectura estrictamente read-only ---------------------------------------------------


def _open_regular(path: Path) -> int:
    descriptor = os.open(path, _READ_FLAGS)
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise OSError(errno.EINVAL, "not a regular file")
    return descriptor


def _read_bytes(path: Path) -> bytes:
    descriptor = _open_regular(path)
    try:
        chunks = []
        while chunk := os.read(descriptor, 1 << 20):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _hash_and_scan(path: Path) -> tuple[str, bool]:
    """SHA-256 del archivo y si contiene el canario, leyendo en bloques."""
    digest = hashlib.sha256()
    found = False
    tail = b""
    descriptor = _open_regular(path)
    try:
        while chunk := os.read(descriptor, 1 << 20):
            digest.update(chunk)
            window = tail + chunk
            found = found or CANARY_SECRET in window
            tail = window[-(len(CANARY_SECRET) - 1) :]
    finally:
        os.close(descriptor)
    return digest.hexdigest(), found


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


@dataclass(slots=True)
class Tree:
    files: dict[str, os.stat_result] = field(default_factory=dict)
    dirs: dict[str, os.stat_result] = field(default_factory=dict)


def _walk(unit: Unit) -> Tree:
    """Lista el árbol sin seguir enlaces. Lo ilegible queda como `incomplete`."""
    tree = Tree()

    def visit(directory: Path, prefix: str) -> None:
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda entry: entry.name)
        except OSError:
            unit.unreadable.append(prefix or ".")
            unit.incomplete("unreadable-directory", prefix or ".")
            return
        for entry in entries:
            if not _SAFE_NAME.fullmatch(entry.name):
                unit.fail("unsafe-filename", prefix or ".")
                continue
            relative = f"{prefix}/{entry.name}" if prefix else entry.name
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError:
                unit.unreadable.append(relative)
                unit.incomplete("unstatable-entry", relative)
                continue
            if stat.S_ISLNK(info.st_mode):
                unit.fail("symlink", relative)
            elif stat.S_ISDIR(info.st_mode):
                tree.dirs[relative] = info
                if info.st_mode & 0o077:
                    unit.warn("mode-not-private", relative)
                visit(Path(entry.path), relative)
            elif stat.S_ISREG(info.st_mode):
                tree.files[relative] = info
                if info.st_mode & 0o077:
                    unit.warn("mode-not-private", relative)
            else:
                unit.fail("non-regular-entry", relative)

    visit(unit.path, "")
    return tree


def _hash_tree(unit: Unit, tree: Tree) -> None:
    for relative in sorted(tree.files):
        try:
            digest, canary = _hash_and_scan(unit.path / relative)
        except OSError:
            unit.unreadable.append(relative)
            unit.incomplete("unreadable-file", relative)
            continue
        unit.inventory[relative] = digest
        if canary:
            unit.fail("canary-secret-persisted", relative)


def _read_unit_file(unit: Unit, relative: str) -> bytes | None:
    try:
        content = _read_bytes(unit.path / relative)
    except OSError:
        unit.incomplete("unreadable-file", relative)
        return None
    expected = unit.inventory.get(relative)
    if expected is not None and _sha256_bytes(content) != expected:
        unit.fail("changed-during-verification", relative)
    return content


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_utc(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp")
    return parsed.astimezone(UTC)


def _hmac_matches(check, *arguments, **kwargs) -> bool:
    try:
        check(*arguments, **kwargs)
    except (CustodyPolicyError, TypeError, ValueError):
        return False
    return True


# --- Runs ------------------------------------------------------------------------------


def _safe_relative(value: Any) -> str | None:
    if not isinstance(value, str) or not value or value.startswith("/"):
        return None
    candidate = PurePosixPath(value)
    if candidate.as_posix() != value or ".." in candidate.parts or "." in candidate.parts:
        return None
    return value


def _verify_run(unit: Unit, context: VerificationContext, *, require_finalized: bool) -> None:
    tree = _walk(unit)
    _hash_tree(unit, tree)
    if MARKER not in tree.files:
        unit.fail_or_incomplete("marker-missing", MARKER)
    elif (content := _read_unit_file(unit, MARKER)) is not None and content != MARKER_CONTENT:
        unit.fail("marker-content")

    records = _verify_index(unit, tree, context)
    unit.stats["records"] = len(records or [])
    unit.records = _verify_records(unit, tree, context, records or [])

    if records is not None:
        # Sin índice legible no se puede afirmar que un archivo esté fuera de él.
        indexed = {record["relative_path"] for record in unit.records}
        for relative in sorted(set(tree.files) - RUN_CONTROL_FILES - indexed):
            # SHA256SUMS no está autenticado: un archivo fuera del índice no está custodiado.
            unit.fail("unindexed-file", relative)

    finals = [r for r in unit.records if r["artifact_type"] == "run-manifest-final"]
    if require_finalized and not finals and SEAL not in tree.files:
        unit.fail_or_incomplete("not-finalized", SEAL)
    if finals or SEAL in tree.files:
        _verify_seal_and_manifest(unit, tree, context, finals)
    _verify_checksums(unit, tree)


def _verify_index(
    unit: Unit, tree: Tree, context: VerificationContext
) -> list[dict[str, Any]] | None:
    """Registros del índice; `None` si no se pudieron leer sus bytes."""
    if INDEX not in tree.files:
        unit.fail_or_incomplete("index-missing", INDEX)
        return None
    index_bytes = _read_unit_file(unit, INDEX)
    if index_bytes is None:
        return None
    if INDEX_SIGNATURE not in tree.files:
        unit.fail_or_incomplete("index-signature-missing", INDEX_SIGNATURE)
    elif (signature := _read_unit_file(unit, INDEX_SIGNATURE)) is not None:
        if not _SIGNATURE_LINE.fullmatch(signature):
            unit.fail("index-signature-format")
        else:
            valid = _hmac_matches(
                validate_custody_index_signature,
                index_bytes,
                signature.decode("ascii").strip(),
                custody_hmac_key=context.custody_key,
            )
            if unit.historical and valid:
                unit.fail("historical-declaration-invalid", INDEX_SIGNATURE)
            elif not unit.historical and not valid:
                unit.fail("index-signature")
    records: list[dict[str, Any]] = []
    try:
        lines = index_bytes.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        unit.fail("index-encoding")
        return []
    for number, line in enumerate(lines, start=1):
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            unit.fail("index-json", number)
            continue
        if not isinstance(record, dict):
            unit.fail("index-json", number)
            continue
        records.append(record)
    try:
        canonical = b"".join(canonical_json_bytes(record) for record in records)
    except (TypeError, ValueError):
        canonical = None
    if canonical != index_bytes and len(records) == len(lines):
        unit.fail("index-not-canonical")
    if not records:
        unit.fail("index-empty")
    return records


def _record_types_ok(record: dict[str, Any]) -> bool:
    if any(not isinstance(record[name], str) for name in _REQUIRED_TEXT_FIELDS):
        return False
    if any(
        record[name] is not None and not isinstance(record[name], str)
        for name in _OPTIONAL_TEXT_FIELDS
    ):
        return False
    if type(record["size_bytes"]) is not int or record["size_bytes"] < 0:
        return False
    if record["publication_approval"] is not None and not isinstance(
        record["publication_approval"], dict
    ):
        return False
    return bool(_SHA256.fullmatch(record["sha256"]))


def _verify_records(
    unit: Unit,
    tree: Tree,
    context: VerificationContext,
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    valid = []
    kinds = {kind.value for kind in ArtifactKind}
    for number, record in enumerate(records, start=1):
        if set(record) != ARTIFACT_RECORD_FIELDS:
            unit.fail("record-fields", number)
            continue
        if not _record_types_ok(record):
            unit.fail("record-types", number)
            continue
        subject = record["artifact_id"]
        if record["run_id"] != unit.name:
            unit.fail("record-run-id", subject)
        if subject in seen_ids:
            unit.fail("record-duplicate-id", subject)
        seen_ids.add(subject)
        relative = _safe_relative(record["relative_path"])
        parts = relative.split("/") if relative else []
        if (
            len(parts) != 3
            or parts[0] != record["kind"]
            or parts[1] != record["scenario_id"]
            or record["kind"] not in kinds
        ):
            unit.fail("record-path-layout", subject)
        if relative is None:
            unit.fail("record-path", subject)
        elif relative not in tree.files:
            unit.fail_or_incomplete("record-path", relative)
        else:
            if relative in seen_paths:
                unit.fail("record-duplicate-path", subject)
            seen_paths.add(relative)
            if relative in unit.inventory and unit.inventory[relative] != record["sha256"]:
                unit.fail("record-sha256", subject)
            if tree.files[relative].st_size != record["size_bytes"]:
                unit.fail("record-size", subject)
        try:
            classification = Classification(record["classification"])
        except ValueError:
            unit.fail("record-classification", subject)
            continue
        if classification in {Classification.SECRET, Classification.SENSITIVE}:
            unit.fail("record-classification", subject)
        signed = _hmac_matches(
            validate_custody_record_signature, record, custody_hmac_key=context.custody_key
        )
        if unit.historical and signed:
            unit.fail("historical-declaration-invalid", subject)
        elif not unit.historical and not signed:
            unit.fail("record-signature", subject)
        try:
            approval_payload = record["publication_approval"]
            approval = (
                PublicationApproval(**approval_payload) if approval_payload is not None else None
            )
            validate_public_approval(
                classification,
                approval,
                expected_artifact_id=record["artifact_id"],
                expected_content_sha256=record["sha256"],
                verification_key=context.publication_key,
            )
        except (CustodyPolicyError, TypeError, ValueError):
            unit.fail("record-approval", subject)
        valid.append(record)
    return valid


def _load_json_file(unit: Unit, tree: Tree, relative: Any, code: str) -> dict[str, Any] | None:
    safe = _safe_relative(relative)
    if safe is None or safe not in tree.files:
        unit.fail(code, relative if isinstance(relative, str) else None)
        return None
    raw = _read_unit_file(unit, safe)
    if raw is None:
        return None
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        payload = None
    if not isinstance(payload, dict):
        unit.fail(code, safe)
        return None
    return payload


def _verify_seal_and_manifest(
    unit: Unit,
    tree: Tree,
    context: VerificationContext,
    finals: list[dict[str, Any]],
) -> None:
    """Mismo contrato que `evidence.validate_finalized_state`, informando cada fallo."""
    records = unit.records
    if SEAL not in tree.files:
        unit.fail_or_incomplete("seal-missing", SEAL)
        return
    raw = _read_unit_file(unit, SEAL)
    if raw is None:
        return
    try:
        envelope = json.loads(raw)
        if not isinstance(envelope, dict) or set(envelope) != {"payload", "signature"}:
            raise TypeError
        payload = envelope["payload"]
        signature = envelope["signature"]
        if not isinstance(payload, dict) or not isinstance(signature, str):
            raise TypeError
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError, ValueError):
        unit.fail("seal-format")
        return
    try:
        sealed = _hmac_matches(
            validate_custody_seal_signature,
            canonical_json_bytes(payload),
            signature,
            custody_hmac_key=context.custody_key,
        )
    except (TypeError, ValueError):
        sealed = False
    if unit.historical and sealed:
        unit.fail("historical-declaration-invalid", SEAL)
    elif not unit.historical and not sealed:
        unit.fail("seal-signature")
    if len(finals) > 1:
        unit.fail("final-manifest-count")
        return
    if not finals:
        _verify_seal_without_manifest(unit, payload)
        return

    final = finals[0]
    manifest = _load_json_file(unit, tree, final["relative_path"], "final-manifest-format")
    if manifest is None:
        return
    try:
        finished_at = _parse_utc(manifest.get("finished_at"))
    except ValueError:
        unit.fail("final-manifest-format")
        return
    expected_seal = {
        "artifact_id": final["artifact_id"],
        "finished_at": manifest["finished_at"],
        "manifest_sha256": final["sha256"],
        "run_id": final["run_id"],
    }
    if payload != expected_seal:
        unit.fail("seal-linkage")
    expected_references = [
        {
            "artifact_id": record["artifact_id"],
            "relative_path": record["relative_path"],
            "sha256": record["sha256"],
        }
        for record in records
        if record is not final
    ]
    if manifest.get("artifact_references") != expected_references:
        unit.fail("manifest-references")
    if manifest.get("run_id") != unit.name or manifest.get("scenario_id") != final["scenario_id"]:
        unit.fail("manifest-identity")
    status = manifest.get("status")
    if status not in FINAL_MANIFEST_STATUSES:
        unit.fail("manifest-status")
    reason = manifest.get("invalidity_reason")
    if (status == "INVALID") != (isinstance(reason, str) and bool(reason)) or (
        status != "INVALID" and reason is not None
    ):
        unit.fail("manifest-invalidity-reason")
    initials = [r for r in records if r["artifact_type"] == "run-manifest-initial"]
    if len(initials) != 1 or initials[0]["scenario_id"] != final["scenario_id"]:
        unit.fail("initial-manifest-count")
    else:
        initial = _load_json_file(
            unit, tree, initials[0]["relative_path"], "initial-manifest-format"
        )
        if initial is not None and (
            set(initial) != set(manifest)
            or any(
                initial[name] != manifest[name] for name in set(manifest) - MANIFEST_MUTABLE_FIELDS
            )
        ):
            unit.fail("manifest-provenance")
    for record in records:
        _verify_retention(unit, record, finished_at)


def _verify_retention(unit: Unit, record: dict[str, Any], finished_at: datetime) -> None:
    subject = record["artifact_id"]
    try:
        classification = Classification(record["classification"])
        started_at = _parse_utc(record["retention_started_at"])
        if classification is Classification.PUBLIC:
            approval = record["publication_approval"]
            ok = (
                isinstance(approval, dict)
                and record["retention_anchor"] == "publication_approval"
                and started_at == _parse_utc(approval["approved_at"])
                and record["expires_at"] is None
            )
        else:
            ok = (
                record["retention_anchor"] == "finished_at"
                and started_at == finished_at
                and record["expires_at"] == _format_utc(expiration_for(classification, started_at))
            )
    except (CustodyPolicyError, KeyError, TypeError, ValueError):
        ok = False
    if not ok:
        unit.fail("retention", subject)


def _verify_seal_without_manifest(unit: Unit, payload: dict[str, Any]) -> None:
    # Sello histórico tras destruir el manifest INTERNAL vencido: el payload ancla la
    # retención EXPERIMENTAL y PUBLIC que sigue vigente.
    if set(payload) != {"artifact_id", "finished_at", "manifest_sha256", "run_id"}:
        unit.fail("seal-format")
        return
    if payload["run_id"] != unit.name or not (
        isinstance(payload["manifest_sha256"], str)
        and _SHA256.fullmatch(payload["manifest_sha256"])
    ):
        unit.fail("seal-linkage")
    if any(
        record["classification"]
        not in {Classification.PUBLIC.value, Classification.EXPERIMENTAL.value}
        for record in unit.records
    ):
        unit.fail("seal-without-final-manifest")
        return
    try:
        finished_at = _parse_utc(payload["finished_at"])
    except ValueError:
        unit.fail("seal-format")
        return
    for record in unit.records:
        _verify_retention(unit, record, finished_at)


def _verify_checksums(unit: Unit, tree: Tree) -> None:
    if CHECKSUMS not in tree.files:
        unit.fail_or_incomplete("checksums-missing", CHECKSUMS)
        return
    raw = _read_unit_file(unit, CHECKSUMS)
    if raw is None:
        return
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        unit.fail("checksums-format")
        return
    listed: dict[str, str] = {}
    for number, line in enumerate(lines, start=1):
        match = _CHECKSUM_LINE.fullmatch(line)
        if match is None:
            unit.fail("checksums-format", number)
            continue
        digest, relative = match.groups()
        if relative in listed:
            unit.fail("checksums-duplicate", relative)
        listed[relative] = digest
    if raw and not raw.endswith(b"\n"):
        unit.fail("checksums-format", "trailing-newline")
    covered = set(tree.files) - {CHECKSUMS}
    for relative in sorted(covered - set(listed)):
        unit.fail("checksums-missing-entry", relative)
    for relative in sorted(set(listed) - covered):
        unit.fail_or_incomplete("checksums-extra-entry", relative)
    for relative in sorted(set(listed) & set(unit.inventory)):
        if listed[relative] != unit.inventory[relative]:
            unit.fail("checksums-mismatch", relative)


# --- Verificaciones custodiales ------------------------------------------------------


def _verify_verification(unit: Unit, context: VerificationContext) -> None:
    tree = _walk(unit)
    _hash_tree(unit, tree)
    data_files = sorted(rel for rel in tree.files if rel.endswith(".json") and "/" not in rel)
    expected = {f"{name}{suffix}" for name in data_files for suffix in ("", *BUNDLE_SIDECARS)}
    for relative in sorted(tree.files):
        if relative not in expected:
            unit.fail("unexpected-file", relative)
    for relative in sorted(tree.dirs):
        unit.fail("unexpected-directory", relative)
    for name in data_files:
        _verify_bundle(unit, tree, context, name)
    _verify_bundle_set(unit)
    _verify_cross_references(unit)
    unit.stats["records"] = len(data_files)


def _verify_bundle(unit: Unit, tree: Tree, context: VerificationContext, name: str) -> None:
    content = _read_unit_file(unit, name)
    if content is None:
        return
    digest = _sha256_bytes(content)
    checksum_name, signature_name, commit_name = (name + suffix for suffix in BUNDLE_SIDECARS)
    checksum = _read_unit_file(unit, checksum_name) if checksum_name in tree.files else None
    if checksum_name not in tree.files:
        unit.fail("sha256-sidecar-missing", name)
    elif checksum is not None and checksum != f"{digest}  {name}\n".encode():
        match = re.fullmatch(rb"([0-9a-f]{64})  (.+)\n", checksum)
        if match is None:
            unit.fail("sha256-sidecar-format", name)
        elif match.group(1).decode() != digest:
            unit.fail("sha256-mismatch", name)
        else:
            unit.fail("sha256-sidecar-name", name)
    has_signature = signature_name in tree.files
    has_commit = commit_name in tree.files
    if not unit.historical and not (has_signature and has_commit):
        unit.fail("bundle-incomplete", name)
    signature = _read_unit_file(unit, signature_name) if has_signature else None
    if signature is not None:
        if not _SIGNATURE_LINE.fullmatch(signature):
            unit.fail("signature-format", name)
        else:
            valid = _hmac_matches(
                validate_custody_verification_signature,
                content,
                signature.decode("ascii").strip(),
                custody_hmac_key=context.custody_key,
            )
            if unit.historical and valid:
                unit.fail("historical-declaration-invalid", name)
            elif not unit.historical and not valid:
                unit.fail("record-signature", name)
    if has_commit and (commit := _read_unit_file(unit, commit_name)) is not None:
        try:
            commit_payload = json.loads(commit)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            commit_payload = None
        expected_commit = {
            "checksum_sha256": _sha256_bytes(checksum) if checksum is not None else None,
            "content_sha256": digest,
            "signature_sha256": _sha256_bytes(signature) if signature is not None else None,
        }
        if commit_payload != expected_commit:
            unit.fail("commit-marker", name)
    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        unit.fail("json", name)
        return
    if not isinstance(payload, dict):
        unit.fail("json", name)
        return
    if payload.get("verification_id") != unit.name:
        unit.fail("verification-id", name)
    unit.payloads[name] = payload


def _verify_bundle_set(unit: Unit) -> None:
    """Registros que otro registro presente exige: un borrado parcial falla cerrado."""
    present = set(unit.payloads)
    complete = unit.payloads.get("complete.json")
    required = []
    if "prepare.json" in present or "attestation.json" in present:
        required.append("complete.json")
    if "destruction.intent.json" in present:
        required.append("destruction.json")
    if isinstance(complete, dict):
        destruction = complete.get("destruction")
        if isinstance(destruction, dict):
            required.append("destruction.json")
            if isinstance(destruction.get("intent_sha256"), str):
                required.append("destruction.intent.json")
        if "source_snapshot" in complete and not unit.historical:
            required.append("attestation.json")
    for name in sorted(set(required) - present):
        if not unit.under_unreadable(name):
            unit.fail("bundle-set", name)


def _verify_cross_references(unit: Unit) -> None:
    """Hashes que un registro de verificación declara sobre otro del mismo bundle."""
    payloads = unit.payloads
    links: list[tuple[str, Any, str]] = []
    attestation = payloads.get("attestation.json")
    complete = payloads.get("complete.json")
    receipt = payloads.get("destruction.json")
    if attestation is not None:
        links.append(
            (
                "attestation.complete_evidence_sha256",
                attestation.get("complete_evidence_sha256"),
                "complete.json",
            )
        )
    if complete is not None and "prepare_evidence_sha256" in complete:
        links.append(
            (
                "complete.prepare_evidence_sha256",
                complete.get("prepare_evidence_sha256"),
                "prepare.json",
            )
        )
    if complete is not None and isinstance(complete.get("destruction"), dict):
        destruction = complete["destruction"]
        links.append(
            (
                "complete.destruction.receipt_sha256",
                destruction.get("receipt_sha256"),
                "destruction.json",
            )
        )
        if "destruction.intent.json" in payloads:
            links.append(
                (
                    "complete.destruction.intent_sha256",
                    destruction.get("intent_sha256"),
                    "destruction.intent.json",
                )
            )
    if receipt is not None and "destruction.intent.json" in payloads:
        links.append(
            (
                "destruction.intent_sha256",
                receipt.get("intent_sha256"),
                "destruction.intent.json",
            )
        )
    for label, declared, target in links:
        actual = unit.inventory.get(target)
        if actual is None and unit.under_unreadable(target):
            unit.incomplete("cross-reference-unverifiable", label)
        elif actual is None or declared != actual:
            unit.fail("cross-reference", label)


# --- Raíces ----------------------------------------------------------------------------


@dataclass(slots=True)
class RootScan:
    label: str
    path: Path
    findings: list[Finding] = field(default_factory=list)
    uncovered: dict[str, Any] = field(default_factory=dict)

    def fail(self, code: str, subject: Any = None) -> None:
        self.findings.append(Finding(SEVERITY_FAIL, code, _subject(subject)))

    def incomplete(self, code: str, subject: Any = None) -> None:
        self.findings.append(Finding(SEVERITY_INCOMPLETE, code, _subject(subject)))


def _list_directory(scan: RootScan, directory: Path, label: str) -> list[os.DirEntry] | None:
    try:
        with os.scandir(directory) as iterator:
            return sorted(iterator, key=lambda entry: entry.name)
    except FileNotFoundError:
        return None
    except OSError:
        scan.incomplete("unreadable-directory", label)
    return []


def _entry_stat(scan: RootScan, entry: os.DirEntry, label: str) -> os.stat_result | None:
    try:
        return entry.stat(follow_symlinks=False)
    except OSError:
        scan.incomplete("unstatable-entry", label)
        return None


def _discover(scan: RootScan, allowed: frozenset[str]) -> dict[str, list[Path]]:
    entries = _list_directory(scan, scan.path, ".")
    if entries is None:
        scan.fail("root-missing")
        return {}
    found: dict[str, list[Path]] = {}
    for entry in entries:
        if not _SAFE_NAME.fullmatch(entry.name):
            scan.fail("unsafe-filename", ".")
            continue
        info = _entry_stat(scan, entry, entry.name)
        if info is None:
            continue
        if entry.name not in allowed:
            scan.fail("unexpected-root-entry", entry.name)
            continue
        if stat.S_ISLNK(info.st_mode):
            scan.fail("symlink", entry.name)
            continue
        if entry.name == ".s11-custody.lock":
            if not stat.S_ISREG(info.st_mode):
                scan.fail("lock-not-regular")
            continue
        if not stat.S_ISDIR(info.st_mode):
            scan.fail("root-entry-not-directory", entry.name)
            continue
        if entry.name in UNCOVERED_PRIMARY_ENTRIES:
            scan.uncovered[entry.name] = _count_uncovered(scan, Path(entry.path), entry.name)
            continue
        children = _list_directory(scan, Path(entry.path), entry.name) or []
        if entry.name in PENDING_ENTRY_CODES:
            # Transitorios: en reposo deben estar vacíos. Si no, algo quedó a medias.
            for child in children:
                scan.fail(PENDING_ENTRY_CODES[entry.name], f"{entry.name}/{child.name}")
            continue
        paths = []
        for child in children:
            label = f"{entry.name}/{child.name}"
            if not _SAFE_NAME.fullmatch(child.name):
                scan.fail("unsafe-filename", entry.name)
                continue
            child_info = _entry_stat(scan, child, label)
            if child_info is None:
                continue
            if stat.S_ISLNK(child_info.st_mode) or not stat.S_ISDIR(child_info.st_mode):
                scan.fail("unexpected-entry", label)
                continue
            paths.append(Path(child.path))
        found[entry.name] = paths
    return found


def _count_uncovered(scan: RootScan, directory: Path, label: str) -> dict[str, int]:
    files = 0
    pending = [(directory, label)]
    while pending:
        current, current_label = pending.pop()
        for entry in _list_directory(scan, current, current_label) or []:
            info = _entry_stat(scan, entry, f"{current_label}/{entry.name}")
            if info is None:
                continue
            if stat.S_ISDIR(info.st_mode):
                pending.append((Path(entry.path), f"{current_label}/{entry.name}"))
            else:
                files += 1
    return {"files": files}


# --- Línea base ------------------------------------------------------------------------


def git_document_baseline(repo_root: Path, commit: str) -> Baseline:
    """Hashes e identificadores citados en `docs/**/*.md` de un commit, sólo con `git`."""
    if not _COMMIT.fullmatch(commit):
        raise VerifierConfigurationError("baseline-commit-format")
    _git(repo_root, ["cat-file", "-e", f"{commit}^{{commit}}"])
    listing = _git(repo_root, ["ls-tree", "-r", "-z", "--name-only", commit, "--", "docs"])
    anchors: dict[str, set[str]] = {}
    units: set[str] = set()
    for raw_name in listing.split(b"\0"):
        name = raw_name.decode("utf-8", errors="replace")
        if not name.endswith(".md"):
            continue
        if not _DOC_PATH.fullmatch(name):
            raise VerifierConfigurationError("baseline-document-name")
        text = _git(repo_root, ["show", f"{commit}:{name}"]).decode("utf-8", errors="replace")
        for value in _SHA256_IN_TEXT.findall(text):
            anchors.setdefault(value, set()).add(name)
        units |= set(_DOCUMENTED_RUN_ID.findall(text))
        units |= set(_DOCUMENTED_VERIFICATION_ID.findall(text))
    return Baseline(
        commit=commit,
        anchors={value: sorted(paths) for value, paths in sorted(anchors.items())},
        documented_units=frozenset(units),
    )


def _git_environment(repo_root: str) -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        # Ni `git replace`, ni configuración del sistema o global: sólo los objetos del commit.
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        # Leer con otro uid (root en el contenedor) exige `safe.directory`, sólo para este
        # repositorio. Esa excepción desactiva la defensa contra comandos de la configuración
        # del repositorio; `core.fsmonitor=false`, con prioridad de línea de comando, la repone.
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "safe.directory",
        "GIT_CONFIG_VALUE_0": repo_root,
        "GIT_CONFIG_KEY_1": "core.fsmonitor",
        "GIT_CONFIG_VALUE_1": "false",
        "LC_ALL": "C",
    }


def _allowed_git_argv(command: Any) -> bool:
    """Las tres formas exactas que construye `_git`, con el ejecutable resuelto."""
    if not isinstance(command, list | tuple) or _GIT_EXECUTABLE is None:
        return False
    command = list(command)
    if len(command) < 5 or command[0] != _GIT_EXECUTABLE or command[1] != "-C":
        return False
    rest = command[3:]
    if rest[:2] == ["cat-file", "-e"] and len(rest) == 3:
        return bool(re.fullmatch(r"[0-9a-f]{40}\^\{commit\}", rest[2]))
    if rest[:4] == ["ls-tree", "-r", "-z", "--name-only"] and len(rest) == 7:
        return bool(_COMMIT.fullmatch(rest[4])) and rest[5:] == ["--", "docs"]
    if rest[0] == "show" and len(rest) == 2:
        commit, _, path = rest[1].partition(":")
        return bool(_COMMIT.fullmatch(commit)) and bool(_DOC_PATH.fullmatch(path))
    return False


def _git(repo_root: Path, arguments: list[str]) -> bytes:
    if _GIT_EXECUTABLE is None:
        raise VerifierConfigurationError("baseline-git-unavailable")
    root = os.path.realpath(repo_root)
    command = [_GIT_EXECUTABLE, "-C", root, *arguments]
    if not _allowed_git_argv(command):
        raise VerifierConfigurationError("git-command-not-allowed")
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            env=_git_environment(root),
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise VerifierConfigurationError("baseline-git-unavailable") from exc
    return result.stdout


def _apply_anchors(units: list[Unit], baseline: Baseline) -> dict[str, Any]:
    """Ancla cada unidad a la línea base: directo por hash o transitivo por un registro."""
    documented = set(baseline.anchors)
    effective = set(documented)
    # Un registro anclado ancla a su vez todo hash que cita (attestation → complete →
    # prepare, destrucción…). Se itera hasta un punto fijo.
    growing = True
    while growing:
        growing = False
        for unit in units:
            for name, payload in unit.payloads.items():
                if unit.inventory.get(name) not in effective:
                    continue
                cited = set(_SHA256_IN_TEXT.findall(json.dumps(payload, sort_keys=True)))
                if not cited <= effective:
                    effective |= cited
                    growing = True
    per_unit = {}
    matched = set()
    for unit in units:
        anchored_files: set[str] = set()
        direct: set[str] = set()
        for relative, digest in unit.inventory.items():
            if digest in effective and relative != MARKER:
                anchored_files.add(relative)
                matched.add(digest)
                if digest in documented:
                    direct |= set(baseline.anchors[digest])
        if unit.kind == "run":
            final = [r for r in unit.records if r["artifact_type"] == "run-manifest-final"]
            unit.anchored = bool(final) and final[0]["sha256"] in effective
            if unit.anchored:
                anchored_files |= {record["relative_path"] for record in unit.records}
        else:
            data_files = [name for name in unit.inventory if name.endswith(".json")]
            unit.anchored = bool(data_files) and all(
                unit.inventory[name] in effective for name in data_files
            )
        per_unit[unit.unit_id] = {
            "anchored": unit.anchored,
            "anchored_files": len(anchored_files & set(unit.inventory)),
            "files": len(unit.inventory),
            "anchor_documents": sorted(direct),
        }
    return {
        "commit": baseline.commit,
        "anchors": len(documented),
        "anchors_matching_current_files": len(matched & documented),
        "documented_units": len(baseline.documented_units),
        "units": per_unit,
    }


def _check_documented_units(
    units: list[Unit], baseline: Baseline, scan: RootScan
) -> dict[str, Any]:
    present_runs = {u.name for u in units if u.root_label == "primary" and u.kind == "run"}
    present_verifications = {u.name for u in units if u.kind == "verification"}
    # Un run destruido por retención queda justificado por su constancia `DESTROYED`.
    destroyed = {
        receipt["run_id"]
        for unit in units
        if isinstance(receipt := unit.payloads.get("destruction.json"), dict)
        and receipt.get("result") == "DESTROYED"
        and receipt.get("remaining_artifact_ids") == []
        and isinstance(receipt.get("run_id"), str)
    }
    for unit_name in sorted(baseline.documented_units):
        if unit_name not in present_runs | present_verifications | destroyed:
            scan.fail("documented-unit-missing", unit_name)
    return {
        "units_not_documented": sorted(
            (present_runs | present_verifications) - baseline.documented_units
        )
    }


def _compare_with_report(units: list[Unit], baseline: dict[str, Any], scan: RootScan) -> dict:
    if baseline.get("schema_version") != SCHEMA_VERSION or not isinstance(
        baseline.get("units"), list
    ):
        raise VerifierConfigurationError("compare-report-schema")
    current = {unit.unit_id: unit for unit in units}
    previous: dict[str, dict[str, Any]] = {}
    for entry in baseline["units"]:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("unit"), str)
            or not isinstance(entry.get("inventory"), dict)
        ):
            raise VerifierConfigurationError("compare-report-schema")
        previous[entry["unit"]] = entry["inventory"]
    for unit_id, inventory in sorted(previous.items()):
        unit = current.get(unit_id)
        if unit is None:
            scan.fail("baseline-unit-missing", unit_id)
            continue
        for relative, digest in sorted(inventory.items()):
            if relative not in unit.inventory:
                unit.fail_or_incomplete("baseline-file-missing", relative)
            elif unit.inventory[relative] != digest:
                unit.fail("baseline-mismatch", relative)
        for relative in sorted(set(unit.inventory) - set(inventory)):
            unit.fail("baseline-new-file", relative)
    return {
        "units_compared": len(previous),
        "units_not_in_baseline": sorted(set(current) - set(previous)),
    }


# --- Orquestación ----------------------------------------------------------------------


def verify_custody(
    *,
    primary_root: Path,
    backup_root: Path,
    restore_root: Path,
    custody_key: bytes,
    publication_key: bytes | None = None,
    historical: frozenset[str] = frozenset(),
    baseline: Baseline | None = None,
    compare_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Verifica las tres raíces sin escribir. Devuelve el reporte sin secretos."""
    if len(custody_key) < 32:
        raise VerifierConfigurationError("custody-key-too-short")
    if historical and baseline is None:
        raise VerifierConfigurationError("historical-requires-baseline")
    roots = {"primary": primary_root, "backup": backup_root, "restore": restore_root}
    resolved = [path.resolve() for path in roots.values()]
    if len(set(resolved)) != 3 or any(
        left in right.parents for left in resolved for right in resolved
    ):
        raise VerifierConfigurationError("roots-overlap")
    context = VerificationContext(custody_key, publication_key)

    scans = {label: RootScan(label, path) for label, path in roots.items()}
    discovered = {
        "primary": _discover(scans["primary"], PRIMARY_ROOT_ENTRIES),
        "backup": _discover(scans["backup"], COPY_ROOT_ENTRIES),
        "restore": _discover(scans["restore"], COPY_ROOT_ENTRIES),
    }
    units: list[Unit] = []
    for label in ("primary", "backup", "restore"):
        for path in discovered[label].get("runs", []):
            units.append(Unit(label, f"runs/{path.name}", path, "run"))
    for path in discovered["primary"].get("custody-verifications", []):
        units.append(Unit("primary", f"custody-verifications/{path.name}", path, "verification"))
    if sorted(historical - {unit.unit_id for unit in units}):
        raise VerifierConfigurationError("historical-unit-unknown")

    for unit in units:
        unit.historical = unit.unit_id in historical
        try:
            if unit.kind == "run":
                _verify_run(unit, context, require_finalized=unit.root_label == "primary")
            else:
                _verify_verification(unit, context)
        except WriteAttemptError:
            raise
        except Exception as exc:  # noqa: BLE001 - la unidad falla cerrada, sin mensaje
            unit.fail("unexpected-structure", type(exc).__name__)

    by_id = {unit.unit_id: unit for unit in units}
    for unit in units:
        if unit.kind != "run" or unit.root_label == "primary":
            continue
        source_label = "primary" if unit.root_label == "backup" else "backup"
        code = "copy" if unit.root_label == "backup" else "restore"
        source = by_id.get(f"{source_label}:{unit.relative}")
        if source is None:
            unit.fail(f"{code}-without-{source_label}")
        elif source.has_incomplete or unit.has_incomplete:
            unit.incomplete(f"{code}-comparison-incomplete")
        elif source.inventory != unit.inventory:
            unit.fail(f"{code}-diverges-from-{source_label}")

    for unit in units:
        receipt = unit.payloads.get("destruction.json")
        if (
            isinstance(receipt, dict)
            and receipt.get("result") == "DESTROYED"
            and receipt.get("remaining_artifact_ids") == []
            and isinstance(receipt.get("run_id"), str)
        ):
            for label in ("primary", "backup", "restore"):
                if f"{label}:runs/{receipt['run_id']}" in by_id:
                    unit.fail("canary-residual", label)

    completeness = []
    documented = None
    anchoring = None
    if baseline is not None:
        anchoring = _apply_anchors(units, baseline)
        documented = _check_documented_units(units, baseline, scans["primary"])
        if baseline.documented_units:
            completeness.append("git-documents")
    comparison = None
    compare_sha256 = None
    if compare_report is not None:
        comparison = _compare_with_report(units, compare_report, scans["primary"])
        compare_sha256 = _sha256_bytes(canonical_json_bytes(compare_report))
        completeness.append("compare-report")
    if not completeness:
        scans["primary"].incomplete("unit-completeness-unverified")

    root_findings = [
        {"root": label, **finding.to_json()}
        for label, scan in scans.items()
        for finding in scan.findings
    ]
    statuses = [unit.status for unit in units]
    severities = {finding["severity"] for finding in root_findings}
    if FAIL in statuses or SEVERITY_FAIL in severities:
        overall = FAIL
    elif INCOMPLETE in statuses or SEVERITY_INCOMPLETE in severities:
        overall = INCOMPLETE
    elif HISTORICAL_UNANCHORED in statuses:
        overall = PASS_WITH_UNANCHORED_HISTORICAL
    else:
        overall = PASS
    return {
        "schema_version": SCHEMA_VERSION,
        "policy_version": PUBLICATION_POLICY_VERSION,
        "verifier_sha256": _sha256_bytes(_read_bytes(Path(__file__))),
        "overall": overall,
        "custody_key_fingerprint": key_fingerprint(custody_key),
        "publication_key_id": (
            hashlib.sha256(publication_key).hexdigest()[:16] if publication_key else None
        ),
        "historical_units": sorted(historical),
        "completeness_basis": completeness,
        "summary": {
            "units": len(units),
            **{status.lower(): statuses.count(status) for status in UNIT_STATUSES},
        },
        "root_findings": root_findings,
        "uncovered": {
            f"{label}:{name}": value
            for label, scan in scans.items()
            for name, value in scan.uncovered.items()
        },
        "baseline": anchoring,
        "documentation": documented,
        "comparison": comparison,
        "compare_report_sha256": compare_sha256,
        "units": [unit.to_json() for unit in units],
    }


def key_fingerprint(key: bytes) -> str:
    return hashlib.sha256(key).hexdigest()[:16]


# --- CLI -------------------------------------------------------------------------------


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):
        # Nunca repetir argumentos: uno de ellos podría ser un secreto pegado por error.
        raise VerifierConfigurationError("usage")

    def exit(self, status: int = 0, message: str | None = None):
        raise VerifierConfigurationError("usage")


def _parser() -> argparse.ArgumentParser:
    # Sin `--help`: una salida 0 sin verificar nada se confundiría con `PASS`.
    parser = _Parser(prog="s11_verify_closed_custody.py", add_help=False, allow_abbrev=False)
    parser.add_argument("--primary-root", type=Path, required=True)
    parser.add_argument("--backup-root", type=Path, required=True)
    parser.add_argument("--restore-root", type=Path, required=True)
    parser.add_argument("--historical", action="append", default=[], metavar="ROOT:RELATIVE")
    parser.add_argument("--expect-fingerprint", metavar="HEX16")
    parser.add_argument("--baseline-git-commit", metavar="SHA")
    parser.add_argument("--repo-root", type=Path)
    parser.add_argument("--compare-report", type=Path, metavar="REPORT_JSON")
    return parser


_BLOCKED_AUDIT_EVENTS = frozenset(
    {
        "ctypes.cdata",
        "ctypes.dlopen",
        "ctypes.dlsym",
        "fcntl.fcntl",
        "fcntl.flock",
        "fcntl.ioctl",
        "fcntl.lockf",
        "mmap.__new__",
        "os.chflags",
        "os.chmod",
        "os.chown",
        "os.exec",
        "os.fork",
        "os.forkpty",
        "os.kill",
        "os.lchflags",
        "os.lchmod",
        "os.lchown",
        "os.link",
        "os.mkdir",
        "os.posix_spawn",
        "os.putenv",
        "os.remove",
        "os.removexattr",
        "os.rename",
        "os.rmdir",
        "os.setxattr",
        "os.spawn",
        "os.symlink",
        "os.system",
        "os.truncate",
        "os.unsetenv",
        "os.utime",
        "shutil.chown",
        "shutil.copyfile",
        "shutil.copymode",
        "shutil.copystat",
        "shutil.copytree",
        "shutil.make_archive",
        "shutil.move",
        "shutil.rmtree",
        "shutil.unpack_archive",
        "socket.bind",
        "socket.connect",
        "socket.sendmsg",
        "socket.sendto",
        "sqlite3.connect",
        "tempfile.mkdtemp",
        "tempfile.mkstemp",
    }
)


def _write_guard(event: str, arguments: tuple) -> None:
    if event in _BLOCKED_AUDIT_EVENTS:
        raise WriteAttemptError(event)
    if event == "open":
        _path, mode, flags = (tuple(arguments) + (None, None, None))[:3]
        if isinstance(flags, int) and flags & _WRITE_FLAGS:
            raise WriteAttemptError(event)
        if isinstance(mode, str) and set(mode) & set("wax+"):
            raise WriteAttemptError(event)
    elif event == "subprocess.Popen":
        command = arguments[1] if len(arguments) > 1 else None
        if not _allowed_git_argv(command):
            raise WriteAttemptError(event)


def install_write_guard() -> None:
    """Audit hook irreversible para el proceso: cualquier escritura aborta."""
    sys.addaudithook(_write_guard)


def _secret_variants(secrets: tuple[str, ...]) -> set[str]:
    variants = set()
    for secret in secrets:
        if secret:
            variants |= {
                secret,
                json.dumps(secret)[1:-1],
                json.dumps(secret, ensure_ascii=False)[1:-1],
            }
    return variants


def _emit(stream, report: dict[str, Any], secrets: tuple[str, ...]) -> bool:
    """Escribe el reporte. Devuelve `False` si tuvo que sustituirlo por contener un secreto."""
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if any(variant in text for variant in _secret_variants(secrets)):
        stream.write(json.dumps({"overall": "ERROR", "error": "report-would-contain-secret"}))
        stream.write("\n")
        return False
    stream.write(text + "\n")
    return True


def run(argv: list[str], environ: dict[str, str], stdout, *, guarded: bool = False) -> int:
    raw_key = environ.get(KEY_ENV, "")
    secrets: tuple[str, ...] = ()
    try:
        try:
            custody_key = raw_key.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise VerifierConfigurationError("custody-key-encoding") from exc
        secrets = (raw_key, custody_key.hex()) if raw_key else ()
        if raw_key and any(raw_key in argument for argument in argv):
            raise VerifierConfigurationError("key-in-arguments")
        arguments = _parser().parse_args(argv)
        if not raw_key:
            raise VerifierConfigurationError("custody-key-missing")
        fingerprint = key_fingerprint(custody_key)
        if arguments.expect_fingerprint is not None:
            if not _FINGERPRINT.fullmatch(arguments.expect_fingerprint):
                raise VerifierConfigurationError("expected-fingerprint-format")
            if arguments.expect_fingerprint != fingerprint:
                raise VerifierConfigurationError("custody-key-fingerprint-mismatch")
        publication_key = None
        if environ.get(PUBLICATION_KEY_ENV):
            try:
                publication_key = bytes.fromhex(environ[PUBLICATION_KEY_ENV])
            except ValueError as exc:
                raise VerifierConfigurationError("publication-key-format") from exc
        baseline = None
        if arguments.baseline_git_commit is not None:
            if arguments.repo_root is None:
                raise VerifierConfigurationError("baseline-requires-repo-root")
            baseline = git_document_baseline(arguments.repo_root, arguments.baseline_git_commit)
        compare_report = None
        if arguments.compare_report is not None:
            try:
                compare_report = json.loads(_read_bytes(arguments.compare_report))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
                raise VerifierConfigurationError("compare-report-unreadable") from exc
            if not isinstance(compare_report, dict):
                raise VerifierConfigurationError("compare-report-schema")
        report = verify_custody(
            primary_root=arguments.primary_root,
            backup_root=arguments.backup_root,
            restore_root=arguments.restore_root,
            custody_key=custody_key,
            publication_key=publication_key,
            historical=frozenset(arguments.historical),
            baseline=baseline,
            compare_report=compare_report,
        )
    except VerifierConfigurationError as exc:
        _emit(stdout, {"overall": "ERROR", "error": exc.code}, secrets)
        return EXIT_ERROR
    except WriteAttemptError as exc:
        _emit(
            stdout,
            {"overall": "ERROR", "error": "write-attempt-blocked", "event": str(exc)},
            secrets,
        )
        return EXIT_ERROR
    except Exception as exc:  # noqa: BLE001 - nunca se imprime el mensaje
        _emit(
            stdout, {"overall": "ERROR", "error": "internal", "type": type(exc).__name__}, secrets
        )
        return EXIT_ERROR
    report["read_only_guard"] = "audit-hook" if guarded else "none"
    if not _emit(stdout, report, secrets):
        return EXIT_ERROR
    return EXIT_CODES[report["overall"]]


def main() -> int:
    # La clave sale de `os.environ` antes de la guarda (que prohíbe `unsetenv`) y vive sólo
    # en memoria; ningún proceso hijo la recibe.
    raw_key = os.environ.pop(KEY_ENV, "")
    install_write_guard()
    if not sys.flags.dont_write_bytecode:
        # Sin `-B` el intérprete ya pudo escribir bytecode de arranque: se rechaza la ejecución.
        _emit(sys.stdout, {"overall": "ERROR", "error": "requires-python-B"}, ())
        return EXIT_ERROR
    environ = dict(os.environ)
    if raw_key:
        environ[KEY_ENV] = raw_key
    return run(sys.argv[1:], environ, sys.stdout, guarded=True)


if __name__ == "__main__":
    raise SystemExit(main())
