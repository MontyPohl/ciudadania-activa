#!/usr/bin/env python3
"""S15: validar por defecto; replay offline; proveedor solo con opt-in explícito."""

from __future__ import annotations

import importlib.metadata
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.evaluation.s15_common import (
    RECORD_VERSION,
    REPLAY_VERSION,
    ROOT,
    UNKNOWN_SLUG,
    PrivateArgumentParser,
    ValidationError,
    atomic_json,
    build_request,
    digest,
    exact,
    implementation_hashes,
    instrument_hashes,
    object_hash,
    read_json,
    request_hash,
    require,
    safe_output_directory,
    validate_dataset,
)
from tools.evaluation.s15_score import (
    load_records,
    persist_invalidated_run,
    score_records,
    validate_record,
)

UNKNOWN_USAGE = {"input_tokens": None, "output_tokens": None, "total_tokens": None}


def blank_record(case, dataset, status="started"):
    return {
        "schema_version": RECORD_VERSION,
        "case_id": case["case_id"],
        "attempt": 1,
        "status": status,
        "request_sha256": request_hash(case, dataset["catalog"]),
        "image_original_sha256": case["photo"]["original_sha256"],
        "image_normalized_sha256": case["photo"]["normalized_sha256"],
        "response_sha256": None,
        "prediction": None,
        "returned_model": None,
        "usage": dict(UNKNOWN_USAGE),
        "elapsed_ms": None,
        "error_code": status,
    }


def minimize_response(record, raw_response, dataset):
    # Parser único de producción, incluyendo claves exactas, duplicados y nulos.
    try:
        from apps.ai_client_app.parser import (
            ClassificationParseError,
            parse_classification_response,
        )
    except ImportError as exc:
        raise ValidationError("El replay requiere las dependencias Django del backend.") from exc
    record["response_sha256"] = digest(raw_response.encode("utf-8"))
    try:
        parsed = parse_classification_response(raw_response)
    except ClassificationParseError:
        record.update(status="parse_error", error_code="parse_error")
        return record
    categories = {row["slug"] for row in dataset["catalog"]["categories"]}
    institutions = dataset["catalog"]["institutions"]
    # Desconocidos se conservan como desconocidos, nunca como nulos ni como texto
    # arbitrario del proveedor. La justificación no es necesaria para re-puntuar.
    prediction = {
        "category_slug": parsed.category_slug
        if parsed.category_slug is None or parsed.category_slug in categories
        else UNKNOWN_SLUG,
        "institution_slug": parsed.institution_slug
        if parsed.institution_slug is None or parsed.institution_slug in institutions
        else UNKNOWN_SLUG,
        "priority": parsed.priority,
        "confidence": str(parsed.confidence),
        "inconsistency": parsed.inconsistency,
    }
    record.update(status="parsed", error_code=None, prediction=prediction)
    return record


def load_replay(path, dataset):
    payload = read_json(path)
    exact(payload, {"schema_version", "dataset_sha256", "source", "responses"})
    require(
        payload["schema_version"] == REPLAY_VERSION
        and payload["dataset_sha256"] == object_hash(dataset),
        "Replay no corresponde al congelamiento.",
    )
    require(
        payload["source"] in {"offline-fixture", "frozen-provider-response"},
        "Procedencia del replay inválida.",
    )
    require(isinstance(payload["responses"], list), "Lista de respuestas inválida.")
    known = {case["case_id"] for case in dataset["cases"]}
    result = {}
    for item in payload["responses"]:
        exact(
            item,
            {
                "case_id",
                "attempt",
                "status",
                "raw_response",
                "returned_model",
                "usage",
                "elapsed_ms",
            },
        )
        require(
            item["case_id"] in known
            and item["case_id"] not in result
            and type(item["attempt"]) is int
            and item["attempt"] == 1,
            "Caso/intento de replay desconocido o duplicado.",
        )
        require(
            item["status"] in {"response", "timeout", "provider_error", "missing", "interrupted"},
            "Estado de replay inválido.",
        )
        require(
            isinstance(item["raw_response"], str)
            if item["status"] == "response"
            else item["raw_response"] is None,
            "Respuesta de replay inválida.",
        )
        exact(item["usage"], UNKNOWN_USAGE.keys())
        require(
            all(v is None or type(v) is int and v >= 0 for v in item["usage"].values()),
            "Uso de replay inválido.",
        )
        require(
            item["elapsed_ms"] is None
            or type(item["elapsed_ms"]) is int
            and item["elapsed_ms"] >= 0,
            "Duración de replay inválida.",
        )
        result[item["case_id"]] = item
    return payload, result


def safe_model(value):
    from tools.evaluation.s15_common import MODEL

    return value if isinstance(value, str) and MODEL.fullmatch(value) else None


def replay_case(case, dataset, item):
    if item is None:
        return blank_record(case, dataset, "missing")
    record = blank_record(
        case, dataset, item["status"] if item["status"] != "response" else "started"
    )
    record.update(
        usage=item["usage"],
        returned_model=safe_model(item["returned_model"]),
        elapsed_ms=item["elapsed_ms"],
    )
    return (
        minimize_response(record, item["raw_response"], dataset)
        if item["status"] == "response"
        else record
    )


def build_live_adapter(dataset):
    # Solo se invoca desde el camino live después de validar TODAS las entradas.
    require(
        dataset["purpose"] == "reserved-evaluation",
        "Fixtures de transporte no habilitan una evaluación real.",
    )
    require(
        bool(os.environ.get("DJANGO_SETTINGS_MODULE")),
        "Configurar DJANGO_SETTINGS_MODULE para verificar catálogos activos.",
    )
    import django

    django.setup()
    from apps.admin_app.models import Category, Institution

    active = list(
        Category.objects.filter(is_active=True)
        .order_by("slug")
        .values_list("slug", "default_institution__slug")
    )
    active_institutions = list(
        Institution.objects.filter(is_active=True).order_by("slug").values_list("slug", flat=True)
    )
    expected = [(row["slug"], row["institution_slug"]) for row in dataset["catalog"]["categories"]]
    require(
        active == expected and active_institutions == dataset["catalog"]["institutions"],
        "El catálogo activo difiere del snapshot congelado.",
    )
    key = os.environ.get("OPENAI_API_KEY", "")
    require(bool(key), "Falta la credencial por variable de entorno; no se recibe por argumento.")
    from apps.ai_client_app.adapters.openai import OpenAiClassificationAdapter, build_client

    provider = dataset["provider"]
    return OpenAiClassificationAdapter(
        client=build_client(api_key=key, timeout_seconds=provider["timeout_seconds"]),
        model=provider["model"],
        reasoning_effort=provider["reasoning_effort"],
        prompt_version="v2",
    )


def live_case(case, image, dataset, adapter):
    request = build_request(case, image, dataset["catalog"])
    record = blank_record(case, dataset)
    from apps.ai_client_app.exceptions import PermanentAiProviderError, TransientAiProviderError

    started = time.monotonic()
    try:
        call = adapter.classify_with_metadata(request)
    except (PermanentAiProviderError, TransientAiProviderError) as exc:
        cause = getattr(exc, "__cause__", None)
        status_code = getattr(cause, "status_code", None)
        if (
            isinstance(status_code, int)
            and 400 <= status_code < 500
            and status_code not in {408, 429}
        ):
            raise ValidationError(
                "Fallo de configuración/contrato del proveedor; corrida inválida."
            ) from exc
        # No persistir mensajes/IDs/respuestas ni excepciones arbitrarias.
        causes = [exc, getattr(exc, "__cause__", None)]
        timeout = any(cause is not None and "Timeout" in type(cause).__name__ for cause in causes)
        status = "timeout" if timeout else "provider_error"
        record.update(status=status, error_code=status)
    else:
        record["returned_model"] = safe_model(call.model)
        if call.usage is not None:
            record["usage"] = {field: getattr(call.usage, field, None) for field in UNKNOWN_USAGE}
        record = minimize_response(record, call.text, dataset)
    record["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    return record


def environment_metadata():
    versions = {"python": platform.python_version()}
    for package in ("Django", "Pillow", "openai"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, stderr=subprocess.DEVNULL, text=True
        ).strip()
        dirty = bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"], cwd=ROOT, stderr=subprocess.DEVNULL, text=True
            )
        )
    except (OSError, subprocess.CalledProcessError):
        sha, dirty = None, None
    return {"versions": versions, "git_sha": sha, "git_dirty": dirty}


def invalidate_run(root, meta, dataset, gold, active_case, error):
    """Fallo terminal del instrumento: nunca se convierte en indisponibilidad."""
    error_code = (
        "configuration_or_integrity_error"
        if isinstance(error, ValidationError)
        else "internal_error"
    )
    persist_invalidated_run(root, meta, dataset, gold, error_code)
    if active_case is not None:
        atomic_json(
            root / "cases" / active_case["case_id"] / "attempt-001.json",
            blank_record(active_case, dataset, "instrument_error"),
        )


def execute(
    dataset,
    gold,
    prepared,
    output,
    *,
    mode,
    replay_payload=None,
    replay=None,
    adapter=None,
    resume=False,
):
    root = safe_output_directory(output)
    existing = list(root.iterdir())
    require(
        resume or not existing,
        "El destino ya contiene evidencia; usar un destino nuevo o resume explícito.",
    )
    # Un único escritor. Tras un crash, no eliminar automáticamente el lock.
    try:
        lock = os.open(root / ".lock", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ValidationError(
            "Evidencia bloqueada por otro proceso o ejecución interrumpida."
        ) from exc
    os.close(lock)
    owned_run = False
    release_lock = True
    meta = None
    active_case = None
    try:
        identity = {
            "schema_version": "s15-run-v1",
            "dataset_sha256": object_hash(dataset),
            "gold_sha256": object_hash(gold),
            "catalog_sha256": object_hash(dataset["catalog"]),
            "exclusions_sha256": dataset["exclusions_sha256"],
            "implementation_sha256": implementation_hashes(),
            "instrument_sha256": instrument_hashes(),
            "provider": dataset["provider"],
            "protocol": dataset["protocol"],
            "frozen_n": dataset["frozen_n"],
            "execution_mode": mode,
            "replay_sha256": object_hash(replay_payload) if replay_payload is not None else None,
            "replay_source": replay_payload["source"] if replay_payload else None,
        }
        if resume:
            old = read_json(root / "run.json")
            require(
                old.get("run_state") != "invalidated" and "fatal_error_code" not in old,
                "Corrida invalidada: no admite resume ni una promoción a resultado válido.",
            )
            require(
                old.get("run_state") in {"running", "complete"}, "Estado de corrida no recuperable."
            )
            require(
                all(old.get(key) == value for key, value in identity.items()),
                "Resume incompatible con la identidad de corrida congelada.",
            )
            meta = old
            owned_run = True
            records = load_records(root, dataset)
            if old["run_state"] == "complete":
                require(
                    set(records) == {case["case_id"] for case, _ in prepared},
                    "Corrida completa con evidencia ausente: fallo de integridad.",
                )
            for case, _ in prepared:
                if case["case_id"] in records:
                    record = records[case["case_id"]]
                    validate_record(record, case, dataset)
                    require(
                        record["status"] != "instrument_error",
                        "Intento invalidado: no admite recuperación operativa.",
                    )
                    require(
                        record["status"] != "started" or old["run_state"] == "running",
                        "Corrida completa con intento ambiguo: fallo de integridad.",
                    )
        else:
            records = {}
            meta = {**identity, "environment": environment_metadata()}
            owned_run = True
        meta["run_state"] = "running"
        atomic_json(root / "run.json", meta)
        for case, image in prepared:
            active_case = case
            case_id = case["case_id"]
            record_path = root / "cases" / case_id / "attempt-001.json"
            if case_id in records:
                if records[case_id]["status"] == "started":
                    # No repetir una llamada cuya respuesta pudo perderse.
                    records[case_id] = blank_record(case, dataset, "interrupted")
                    atomic_json(record_path, records[case_id])
                continue
            atomic_json(record_path, blank_record(case, dataset))
            record = (
                replay_case(case, dataset, replay.get(case_id))
                if mode == "replay"
                else live_case(case, image, dataset, adapter)
            )
            validate_record(record, case, dataset)
            atomic_json(record_path, record)
            records[case_id] = record
        active_case = None
        meta["run_state"] = "complete"
        atomic_json(root / "run.json", meta)
        score = score_records(dataset, gold, records, execution_mode=mode)
        atomic_json(root / "score.json", score)
        return score
    except Exception as exc:
        if owned_run:
            # Los fallos conocidos de ejecución son terminales; solo KeyboardInterrupt
            # o la muerte del proceso dejan un started ambiguo recuperable.
            release_lock = False
            invalidate_run(root, meta, dataset, gold, active_case, exc)
            release_lock = True
        raise
    finally:
        if release_lock:
            (root / ".lock").unlink(missing_ok=True)


def main(argv=None):
    parser = PrivateArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--gold", required=True)
    parser.add_argument("--exclusions", required=True)
    parser.add_argument("--mode", choices=("validate", "replay", "live"), default="validate")
    parser.add_argument("--responses", help="Documento de respuestas para replay sin proveedor")
    parser.add_argument(
        "--output", help="Directorio privado duradero fuera del repositorio y temporales"
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--public-api-capture", help="Captura offline de GET /api/v1/public/categories/"
    )
    parser.add_argument(
        "--allow-live-provider",
        action="store_true",
        help="Opt-in explícito de una futura llamada real",
    )
    args = parser.parse_args(argv)
    try:
        require(
            args.mode == "live" or not args.allow_live_provider,
            "El opt-in solo corresponde al modo live.",
        )
        if args.mode == "live":
            require(
                args.allow_live_provider and args.public_api_capture and not args.responses,
                "Live requiere opt-in explícito y captura vigente de API; no acepta replay.",
            )
        dataset, gold, prepared = validate_dataset(
            args.dataset, args.gold, args.exclusions, args.public_api_capture
        )
        if args.mode == "validate":
            require(
                not args.resume and not args.responses, "Validate no acepta respuestas ni resume."
            )
            print(
                f"VALIDATED_OFFLINE: N={dataset['frozen_n']}; "
                f"minimum={(85 * dataset['frozen_n'] + 99) // 100}; no provider called"
            )
            return 0
        require(bool(args.output), "La ejecución requiere directorio de evidencia privado.")
        # Preflight de salida antes de cargar un cliente o tocar la DB.
        safe_output_directory(args.output)
        replay_payload = replay = adapter = None
        if args.mode == "replay":
            require(bool(args.responses), "Replay requiere respuestas congeladas.")
            replay_payload, replay = load_replay(args.responses, dataset)
        else:
            adapter = build_live_adapter(dataset)
        score = execute(
            dataset,
            gold,
            prepared,
            args.output,
            mode=args.mode,
            replay_payload=replay_payload,
            replay=replay,
            adapter=adapter,
            resume=args.resume,
        )
        print(
            f"{score['verdict']}: {score['primary']['correct']}/{score['denominator']}; "
            f"minimum={score['required_correct']}; usage unknown preserved"
        )
        return 0 if score["run_valid"] else 2
    except ValidationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("S15 interrumpido; conservar evidencia y revisar resume.", file=sys.stderr)
        return 130
    except Exception:
        print(
            "S15 abortado: entradas, dependencias o evidencia inválidas; "
            "no se exponen rutas ni contenido privado.",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
