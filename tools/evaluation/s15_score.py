#!/usr/bin/env python3
"""Puntuación S15 exacta antes de policy, sin llamadas a proveedores."""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.evaluation.s15_common import (
    RECORD_VERSION,
    UNKNOWN_SLUG,
    PrivateArgumentParser,
    ValidationError,
    atomic_json,
    exact,
    instrument_hashes,
    object_hash,
    read_json,
    request_hash,
    require,
    safe_output_directory,
    valid_sha,
    validate_dataset,
)

ERROR_STATUSES = {"missing", "interrupted", "timeout", "provider_error", "parse_error"}
FATAL_STATUSES = {"instrument_error"}


def validate_record(record, case, dataset):
    exact(
        record,
        {
            "schema_version",
            "case_id",
            "attempt",
            "status",
            "request_sha256",
            "image_original_sha256",
            "image_normalized_sha256",
            "response_sha256",
            "prediction",
            "returned_model",
            "usage",
            "elapsed_ms",
            "error_code",
        },
        "Registro de intento inválido.",
    )
    require(
        record["schema_version"] == RECORD_VERSION
        and record["case_id"] == case["case_id"]
        and type(record["attempt"]) is int
        and record["attempt"] == 1,
        "Registro de intento fuera del denominador.",
    )
    require(
        record["request_sha256"] == request_hash(case, dataset["catalog"])
        and record["image_original_sha256"] == case["photo"]["original_sha256"]
        and record["image_normalized_sha256"] == case["photo"]["normalized_sha256"],
        "Registro no corresponde a los bytes y solicitud congelados.",
    )
    require(
        record["status"] in ERROR_STATUSES | FATAL_STATUSES | {"parsed", "started"},
        "Estado de intento inválido.",
    )
    require(
        record["response_sha256"] is None or valid_sha(record["response_sha256"]),
        "Hash de respuesta inválido.",
    )
    usage = record["usage"]
    exact(usage, {"input_tokens", "output_tokens", "total_tokens"})
    require(
        all(value is None or type(value) is int and value >= 0 for value in usage.values()),
        "Contadores de uso inválidos.",
    )
    require(
        record["elapsed_ms"] is None
        or type(record["elapsed_ms"]) is int
        and record["elapsed_ms"] >= 0,
        "Duración inválida.",
    )
    if record["status"] == "parsed":
        require(
            valid_sha(record["response_sha256"]) and record["error_code"] is None,
            "Respuesta parseada sin hash o con error.",
        )
        parsed = record["prediction"]
        exact(
            parsed, {"category_slug", "institution_slug", "priority", "confidence", "inconsistency"}
        )
        allowed_categories = {row["slug"] for row in dataset["catalog"]["categories"]}
        for field, allowed in (
            ("category_slug", allowed_categories),
            ("institution_slug", set(dataset["catalog"]["institutions"])),
        ):
            require(
                parsed[field] is None or parsed[field] == UNKNOWN_SLUG or parsed[field] in allowed,
                "Predicción no minimizada.",
            )
        from decimal import Decimal, InvalidOperation

        try:
            confidence = Decimal(parsed["confidence"])
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValidationError("Confianza inválida.") from exc
        require(
            isinstance(parsed["confidence"], str)
            and confidence.is_finite()
            and 0 <= confidence <= 1
            and type(parsed["inconsistency"]) is bool
            and parsed["priority"] in {"BAJA", "MEDIA", "ALTA"},
            "Predicción inválida.",
        )
    else:
        require(
            record["prediction"] is None and record["error_code"] == record["status"],
            "Registro de error inválido.",
        )


def persist_invalidated_run(root, meta, dataset, gold, error_code):
    """Estado terminal compartido por recolección y nueva puntuación offline."""
    meta.update(run_state="invalidated", fatal_error_code=error_code)
    # El estado terminal se escribe primero, antes de sustituir cualquier score previo.
    atomic_json(root / "run.json", meta)
    atomic_json(
        root / "score.json",
        {
            "schema_version": "s15-score-v1",
            "dataset_sha256": object_hash(dataset),
            "gold_sha256": object_hash(gold),
            "execution_mode": meta["execution_mode"],
            "complete": False,
            "run_valid": False,
            "verdict": "INVALID_RUN",
            "fatal_error_code": error_code,
            "denominator": dataset["frozen_n"],
            "primary": {
                "metric": dataset["protocol"]["primary_metric"],
                "correct": None,
                "total": dataset["frozen_n"],
                "threshold_met": None,
            },
        },
    )


def score_records(dataset, gold, records, *, execution_mode="replay", complete=True):
    """El universo es SIEMPRE N; cada caso ausente tiene un desacierto explícito."""
    n = dataset["frozen_n"]
    cases = {case["case_id"]: case for case in dataset["cases"]}
    labels = {label["case_id"]: label for label in gold["labels"]}
    require(set(records) <= set(cases), "La corrida contiene casos ajenos al denominador.")
    primary = secondary = 0
    errors = {}
    failures = []
    observed = {field: 0 for field in ("input_tokens", "output_tokens", "total_tokens")}
    known = {field: 0 for field in observed}
    for case_id, case in cases.items():
        record = records.get(case_id)
        status = "missing"
        if record is not None:
            validate_record(record, case, dataset)
            status = record["status"]
            for field in observed:
                value = record["usage"][field]
                if value is not None:
                    observed[field] += value
                    known[field] += 1
        if status == "started":
            status = "interrupted"
        if status != "parsed":
            errors[status] = errors.get(status, 0) + 1
            failures.append({"case_id": case_id, "reason": status})
            continue
        parsed, expected = record["prediction"], labels[case_id]
        # Nunca resolver slugs desconocidos a None. null solo acierta con null.
        cat_prio = (
            parsed["category_slug"] == expected["category_slug"]
            and parsed["priority"] == expected["priority"]
        )
        triple = cat_prio and parsed["institution_slug"] == expected["institution_slug"]
        primary += int(triple)
        secondary += int(cat_prio)
        if not triple:
            failures.append({"case_id": case_id, "reason": "exact_triple_mismatch"})
    run_valid = (
        complete
        and not (FATAL_STATUSES & set(errors))
        and (not errors or dataset["protocol"]["error_policy"] == "count_as_miss")
    )
    primary_met = primary * 100 >= 85 * n
    verdict = (
        "INVALID_RUN"
        if not run_valid
        else "REPLAY_ONLY"
        if execution_mode != "live"
        else "PASS"
        if primary_met
        else "FAIL"
    )
    return {
        "schema_version": "s15-score-v1",
        "dataset_sha256": object_hash(dataset),
        "gold_sha256": object_hash(gold),
        "protocol_id": dataset["protocol"]["protocol_id"],
        "execution_mode": execution_mode,
        "complete": complete,
        "run_valid": run_valid,
        "error_policy": dataset["protocol"]["error_policy"],
        "interpretation": "operational-including-unavailability"
        if dataset["protocol"]["error_policy"] == "count_as_miss"
        else "classification-capacity-only-if-run-valid",
        "denominator": n,
        "required_correct": (85 * n + 99) // 100,
        "primary": {
            "metric": dataset["protocol"]["primary_metric"],
            "correct": primary,
            "total": n,
            "threshold_met": primary_met if run_valid else None,
        },
        "secondary": {
            "metric": dataset["protocol"]["secondary_metric"],
            "correct": secondary,
            "total": n,
            "decision_metric": False,
        },
        "verdict": verdict,
        "errors": errors,
        "failed_cases": failures,
        "usage": {
            field: {
                "observed_sum": observed[field] if known[field] else None,
                "known_attempts": known[field],
                "unknown_attempts": n - known[field],
            }
            for field in observed
        },
        "cost": {
            "observed": None,
            "estimated": None,
            "provider_reconciled": False,
            "reason": "No price schedule or provider reconciliation supplied.",
        },
        "scope": "direct-provider-before-policy; no Celery, persistence or policy coverage",
    }


def load_records(run_dir, dataset):
    root = Path(run_dir).resolve()
    directory = root / "cases"
    known = {case["case_id"] for case in dataset["cases"]}
    records = {}
    if directory.exists():
        require(not directory.is_symlink(), "Carpeta de intentos inválida.")
        for path in directory.iterdir():
            require(
                path.name in known and path.is_dir() and not path.is_symlink(),
                "Hay evidencia fuera del denominador.",
            )
            children = list(path.iterdir())
            require(
                len(children) <= 1
                and all(
                    child.name == "attempt-001.json" and child.is_file() and not child.is_symlink()
                    for child in children
                ),
                "La corrida contiene intentos no autorizados.",
            )
            if children:
                records[path.name] = read_json(children[0])
    return records


def rescore_run(dataset, gold, run_dir):
    root = safe_output_directory(run_dir)
    require(not (root / ".lock").exists(), "La corrida está bloqueada por un escritor.")
    meta = read_json(root / "run.json")
    require(
        meta.get("run_state") != "invalidated" and "fatal_error_code" not in meta,
        "Corrida invalidada: no admite una nueva puntuación como resultado válido.",
    )
    require(
        meta.get("dataset_sha256") == object_hash(dataset)
        and meta.get("gold_sha256") == object_hash(gold),
        "La corrida no corresponde al dataset/oro congelados.",
    )
    require(meta.get("execution_mode") in {"replay", "live"}, "Origen de corrida inválido.")
    require(
        meta.get("instrument_sha256") == instrument_hashes(),
        "El instrumento difiere del registrado en la corrida.",
    )
    try:
        records = load_records(root, dataset)
        if meta.get("run_state") == "complete":
            require(
                set(records) == {case["case_id"] for case in dataset["cases"]},
                "Corrida completa con evidencia ausente: fallo de integridad.",
            )
            require(
                all(record["status"] != "started" for record in records.values()),
                "Corrida completa con intento ambiguo: fallo de integridad.",
            )
        score = score_records(
            dataset,
            gold,
            records,
            execution_mode=meta["execution_mode"],
            complete=meta.get("run_state") == "complete",
        )
        atomic_json(root / "score.json", score)
        return score
    except Exception as exc:
        code = (
            "configuration_or_integrity_error"
            if isinstance(exc, ValidationError)
            else "internal_error"
        )
        persist_invalidated_run(root, meta, dataset, gold, code)
        raise


def main(argv=None):
    parser = PrivateArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--gold", required=True)
    parser.add_argument("--exclusions", required=True)
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args(argv)
    try:
        dataset, gold, _ = validate_dataset(args.dataset, args.gold, args.exclusions)
        score = rescore_run(dataset, gold, args.run_dir)
        print(
            f"{score['verdict']}: {score['primary']['correct']}/{score['denominator']}; "
            f"minimum={score['required_correct']}"
        )
        return 0 if score["run_valid"] else 2
    except ValidationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception:
        print(
            "No se pudo puntuar: evidencia o entradas inválidas (sin exponer contenido privado).",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
