"""Reconcilia creaciones S15 por SQL antes/después; no consume respuestas HTTP.

Ejecutar con DJANGO_SETTINGS_MODULE=config.settings.dev dentro del entorno local:
    python /work/tools/s15/reconcile_creation.py before --manifest ... --output ...
    python /work/tools/s15/reconcile_creation.py after --manifest ... --baseline ... --output ...

El SHA del manifiesto corresponde a sus bytes originales, incluso al usar
--actors-file para resolver una ruta host dentro del contenedor. Cada consulta
ocurre en una única transacción PostgreSQL REPEATABLE READ READ ONLY. El caseId
es una etiqueta de herramienta: la identidad SQL previa de caso es autor+descripción,
además del hash original global de la foto; no se deduce nada por timestamps.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

ALLOWED_ROOT = Path("/out/s15")
DATABASE_NAMES = frozenset({"ciudadania_s15", "ciudadania_s15_load", "ciudadania_s15_restore"})
METHOD = "postgresql-before-after"
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
LOGICAL_ID = re.compile(r"[A-Za-z0-9_.-]{1,128}\Z")


class ReconcileError(ValueError):
    """Divergencia explícita que impide publicar evidencia SQL válida."""


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _uuid(value):
    try:
        parsed = uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ReconcileError("UUID inválido en manifiesto o SQL.") from exc
    if str(parsed) != str(value).lower():
        raise ReconcileError("UUID no canónico.")
    return str(parsed)


def private_path(value, *, output=False):
    path = Path(value)
    if not path.is_absolute() or not path.is_relative_to(ALLOWED_ROOT):
        raise ReconcileError("Las rutas deben pertenecer a /out/s15.")
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ReconcileError("No se admiten enlaces simbólicos.")
        if parent == ALLOWED_ROOT:
            break
    if path.resolve() != path.absolute():
        raise ReconcileError("La ruta no es canónica.")
    for parent in (path.parent, *path.parent.parents):
        if parent == ALLOWED_ROOT.parent:
            break
        if not parent.is_dir() or parent.stat().st_mode & 0o077:
            raise ReconcileError("Los directorios de evidencia deben ser privados (0700).")
    if output:
        if path.exists():
            raise ReconcileError("No se sobrescribe evidencia existente.")
    elif not path.is_file() or path.stat().st_mode & 0o077:
        raise ReconcileError("La entrada debe ser un archivo privado (0600).")
    return path


def read_private(path):
    path = private_path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as source:
        return source.read()


def write_private(path, value):
    path = private_path(path, output=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=".s15-sql-", delete=False
        ) as output:
            temporary = Path(output.name)
            os.fchmod(output.fileno(), 0o600)
            output.write(_canonical(value) + b"\n")
            output.flush()
            os.fsync(output.fileno())
        # Publicación atómica exclusiva: un concurrente no puede reemplazar un baseline.
        os.link(temporary, path, follow_symlinks=False)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def parse_inputs(manifest_bytes, actor_bytes):
    manifest, actors_document = json.loads(manifest_bytes), json.loads(actor_bytes)
    if manifest.get("schemaVersion") != 1 or actors_document.get("schemaVersion") != 1:
        raise ReconcileError("schemaVersion debe ser 1.")
    if not isinstance(manifest.get("runId"), str) or not LOGICAL_ID.fullmatch(manifest["runId"]):
        raise ReconcileError("runId inválido.")
    if not isinstance(manifest.get("datasetSha256"), str) or not SHA256.fullmatch(
        manifest["datasetSha256"]
    ):
        raise ReconcileError("datasetSha256 inválido.")
    actors = {}
    for actor in actors_document.get("actors", []):
        label = actor.get("id")
        if not isinstance(label, str) or not LOGICAL_ID.fullmatch(label) or label in actors:
            raise ReconcileError("Actor inválido o duplicado.")
        actors[label] = actor
    payloads = manifest.get("create", {}).get("payloads")
    if not isinstance(payloads, list) or len(payloads) != 50:
        raise ReconcileError("La ventana de creación requiere exactamente 50 payloads.")
    cases, hashes, mappings, seen_descriptions = set(), set(), {}, set()
    result = []
    for payload in payloads:
        case_id, actor_id = payload.get("caseId"), payload.get("actorId")
        if not isinstance(case_id, str) or not LOGICAL_ID.fullmatch(case_id) or case_id in cases:
            raise ReconcileError("caseId inválido o duplicado.")
        actor = actors.get(actor_id, {})
        if actor.get("role") != "citizen" or actor.get("verified") is not True:
            raise ReconcileError(f"{case_id}: actor no es ciudadano verificado.")
        user_id = _uuid(actor.get("userId"))
        photo_sha = payload.get("photo", {}).get("sha256")
        if not isinstance(photo_sha, str) or not SHA256.fullmatch(photo_sha) or photo_sha in hashes:
            raise ReconcileError(f"{case_id}: hash inválido o duplicado.")
        description = payload.get("description")
        if not isinstance(description, str) or not description.strip():
            raise ReconcileError(f"{case_id}: falta la descripción que identifica el caso SQL.")
        case_key = (user_id, description)
        if case_key in seen_descriptions:
            raise ReconcileError("Casos SQL ambiguos: autor+descripción repetidos.")
        cases.add(case_id)
        hashes.add(photo_sha)
        seen_descriptions.add(case_key)
        mappings[actor_id] = user_id
        result.append(
            {
                "caseId": case_id,
                "actorId": actor_id,
                "userId": user_id,
                "photoSha256": photo_sha,
                "description": description,
            }
        )
    distribution = Counter(row["actorId"] for row in result)
    if (
        len(distribution) != 10
        or set(distribution.values()) != {5}
        or len(set(mappings.values())) != 10
    ):
        raise ReconcileError("Se requieren diez ciudadanos distintos y cinco casos por ciudadano.")
    return {
        "manifest": manifest,
        "manifestSha256": _sha(manifest_bytes),
        "cases": result,
        "actorsMappingSha256": _sha(_canonical(mappings)),
    }


def _binding(inputs, database):
    manifest = inputs["manifest"]
    return {
        "schemaVersion": 1,
        "method": METHOD,
        "runId": manifest["runId"],
        "datasetSha256": manifest["datasetSha256"],
        "manifestSha256": inputs["manifestSha256"],
        "actorsMappingSha256": inputs["actorsMappingSha256"],
        "database": database,
    }


def _rows(cursor, sql, parameters):
    cursor.execute(sql, parameters)
    return cursor.fetchall()


def _matching(cursor, case):
    by_hash = _rows(
        cursor,
        "SELECT id,report_id,media_type FROM report_media WHERE file_hash=%s",
        [case["photoSha256"]],
    )
    by_case = _rows(
        cursor,
        "SELECT id FROM reports WHERE citizen_id=%s AND description=%s",
        [case["userId"], case["description"]],
    )
    return by_hash, by_case


def collect_before(cursor, inputs, database):
    observations = []
    for case in inputs["cases"]:
        by_hash, by_case = _matching(cursor, case)
        if by_hash or by_case:
            raise ReconcileError(
                f"{case['caseId']}: hash o caso SQL preexistente; ventana inválida."
            )
        users = _rows(
            cursor,
            "SELECT role,verification_status,is_active FROM users WHERE id=%s",
            [case["userId"]],
        )
        if users != [("citizen", "APROBADO", True)]:
            raise ReconcileError(f"{case['caseId']}: actor SQL ausente, inactivo o no verificado.")
        observations.append(
            {
                "caseId": case["caseId"],
                "actorId": case["actorId"],
                "photoSha256": case["photoSha256"],
                "hashRows": 0,
                "caseRows": 0,
                "reportIds": [],
            }
        )
    document = {
        **_binding(inputs, database),
        "phase": "before",
        "rows": observations,
        "observedAt": datetime.now(UTC).isoformat(),
    }
    return {**document, "baselineSha256": _sha(_canonical(document))}


def validate_baseline(baseline, inputs, database):
    document = {key: value for key, value in baseline.items() if key != "baselineSha256"}
    if baseline.get("baselineSha256") != _sha(_canonical(document)):
        raise ReconcileError("Hash de baseline divergente.")
    if any(baseline.get(key) != value for key, value in _binding(inputs, database).items()):
        raise ReconcileError("Baseline no corresponde a manifiesto, actores, corrida o DB.")
    expected = [
        {
            "caseId": case["caseId"],
            "actorId": case["actorId"],
            "photoSha256": case["photoSha256"],
            "hashRows": 0,
            "caseRows": 0,
            "reportIds": [],
        }
        for case in inputs["cases"]
    ]
    if baseline.get("phase") != "before" or baseline.get("rows") != expected:
        raise ReconcileError("Baseline no demuestra ausencia de los 50 hashes/casos.")


def collect_after(cursor, inputs, baseline, database):
    validate_baseline(baseline, inputs, database)
    result, missing, seen_reports = [], [], set()
    for case in inputs["cases"]:
        case_id = case["caseId"]
        by_hash, by_case = _matching(cursor, case)
        if not by_hash and not by_case:
            missing.append(case_id)
            continue
        if len(by_hash) != 1 or len(by_case) != 1:
            raise ReconcileError(f"{case_id}: hash/caso duplicado, incompleto o divergente.")
        media_id, report_id, media_type = by_hash[0]
        report_id = _uuid(report_id)
        if report_id != _uuid(by_case[0][0]) or media_type != "FOTO" or report_id in seen_reports:
            raise ReconcileError(f"{case_id}: enlace foto/caso/reporte divergente.")
        seen_reports.add(report_id)
        reports = _rows(
            cursor, "SELECT citizen_id,source,description FROM reports WHERE id=%s", [report_id]
        )
        if len(reports) != 1 or (_uuid(reports[0][0]), reports[0][1], reports[0][2]) != (
            case["userId"],
            "platform",
            case["description"],
        ):
            raise ReconcileError(f"{case_id}: autor u origen del reporte divergente.")
        media = _rows(
            cursor,
            "SELECT id,file_hash,media_type,uploaded_by_id FROM report_media WHERE report_id=%s",
            [report_id],
        )
        if len(media) != 1 or (
            _uuid(media[0][0]),
            media[0][1],
            media[0][2],
            _uuid(media[0][3]),
        ) != (
            _uuid(media_id),
            case["photoSha256"],
            "FOTO",
            case["userId"],
        ):
            raise ReconcileError(f"{case_id}: cantidad o autoría de media divergente.")
        history = _rows(
            cursor,
            "SELECT changed_by_id,resolution_cycle FROM report_status_history "
            "WHERE report_id=%s AND previous_status='' AND new_status='CREADO'",
            [report_id],
        )
        if len(history) != 1 or (_uuid(history[0][0]), history[0][1]) != (case["userId"], 0):
            raise ReconcileError(f"{case_id}: historial inicial ausente, duplicado o divergente.")
        credit = _rows(
            cursor,
            "SELECT user_id,status,impact_delta,credit_delta,institution_id,"
            "credited_at,voided_at,reversed_at,resolution_cycle,confirmation_id,redemption_id,"
            "related_transaction_id FROM reward_transactions WHERE report_id=%s "
            "AND event_type='REPORT_CREATED'",
            [report_id],
        )
        if len(credit) != 1 or (_uuid(credit[0][0]), *credit[0][1:]) != (
            case["userId"],
            "PENDIENTE",
            20,
            20,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        ):
            raise ReconcileError(f"{case_id}: ledger inicial pendiente divergente.")
        result.append(
            {
                "caseId": case_id,
                "actorId": case["actorId"],
                "reportId": report_id,
                "photoSha256": case["photoSha256"],
                "reportRows": len(reports),
                "mediaRows": len(media),
                "initialHistoryRows": len(history),
                "pendingCreditRows": len(credit),
                "impactDelta": credit[0][2],
                "creditDelta": credit[0][3],
            }
        )
    return {
        **_binding(inputs, database),
        "phase": "after",
        "rows": result,
        "baselineSha256": baseline["baselineSha256"],
        "observedAt": datetime.now(UTC).isoformat(),
        "missingCases": missing,
        "persistedCases": len(result),
        "scope": (
            "SQL existence and before/after difference; client timing is reconciled separately"
        ),
    }


def guard_database(settings, connection):
    database = settings.DATABASES["default"]
    if (
        settings.DEBUG is not True
        or database.get("HOST") != "ca-s15-db"
        or database.get("NAME") not in DATABASE_NAMES
    ):
        raise ReconcileError("Se requiere DEBUG=True y una DB sintética S15 en ca-s15-db.")
    if connection.vendor != "postgresql" or "postgis" not in database.get("ENGINE", ""):
        raise ReconcileError("La reconciliación requiere PostgreSQL/PostGIS local.")
    return {"name": database["NAME"], "host": database["HOST"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("before", "after"))
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument(
        "--actors-file", type=Path, help="Alias local; conserva el hash del manifiesto original."
    )
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        private_path(args.output, output=True)
        manifest_bytes = read_private(args.manifest)
        manifest = json.loads(manifest_bytes)
        actors_path = args.actors_file or manifest.get("actorsFile", "")
        inputs = parse_inputs(manifest_bytes, read_private(actors_path))
        if (args.phase == "after") != bool(args.baseline):
            raise ReconcileError("--baseline es obligatorio solo para after.")
        baseline = json.loads(read_private(args.baseline)) if args.baseline else None
        import django

        django.setup()
        from django.conf import settings
        from django.db import connection, transaction

        database = guard_database(settings, connection)
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                cursor.execute(
                    "SELECT current_database(), current_setting('transaction_read_only')"
                )
                if cursor.fetchone() != (database["name"], "on"):
                    raise ReconcileError("La conexión SQL efectiva no cumple DB/readonly.")
                result = (
                    collect_before(cursor, inputs, database)
                    if args.phase == "before"
                    else collect_after(cursor, inputs, baseline, database)
                )
        write_private(args.output, result)
        print(json.dumps({"status": "EXPORTED", "phase": args.phase, "rows": len(result["rows"])}))
        return 0
    except (ReconcileError, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "FAIL", "reason": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
