"""Fronteras offline y custodia de la evaluación multimodal S15.

No carga configuración de Django ni crea clientes de proveedores al importar.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

VERSION = "s15-multimodal-v1"
GOLD_VERSION = "s15-gold-v1"
REPLAY_VERSION = "s15-responses-v1"
RECORD_VERSION = "s15-attempt-v1"
MAX_PHOTO_BYTES = 5 * 1024 * 1024
PRIORITIES = ["BAJA", "MEDIA", "ALTA"]
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
IDENTIFIER = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}\Z")
SHA = re.compile(r"[0-9a-f]{64}\Z")
MODEL = re.compile(r"(?:gpt-|chatgpt-|o[1-9]|ft:)[a-zA-Z0-9_.:-]{0,120}\Z")
UNKNOWN_SLUG = "<UNKNOWN_SLUG>"
FIELDS = {
    "category_slug",
    "institution_slug",
    "priority",
    "confidence",
    "inconsistency",
    "justification",
}


class ValidationError(ValueError):
    """Mensajes estáticos: nunca interpolar rutas, texto ni salida del proveedor."""


class PrivateArgumentParser(argparse.ArgumentParser):
    """No repetir argumentos erróneos que puedan contener rutas o credenciales."""

    def error(self, _message):
        self.print_usage(sys.stderr)
        self.exit(2, "Argumentos inválidos; consultar --help (contenido privado omitido).\n")


def instrument_hashes():
    directory = Path(__file__).resolve().parent
    files = ["s15_common.py", "s15_run.py", "s15_score.py"]
    files.extend(f"{name}.schema.json" for name in ("dataset", "gold", "exclusions", "responses"))
    return {name: digest((directory / name).read_bytes()) for name in files}


def require(condition, message):
    if not condition:
        raise ValidationError(message)


def exact(obj, fields, message="Campos de documento inválidos."):
    require(isinstance(obj, dict) and set(obj) == set(fields), message)


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def digest(value):
    return hashlib.sha256(value).hexdigest()


def object_hash(value):
    return digest(canonical(value))


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "JSON con campos duplicados.")
        result[key] = value
    return result


def _constant(_value):
    raise ValidationError("JSON con constantes no finitas.")


def read_json(path):
    try:
        with Path(path).open(encoding="utf-8") as source:
            return json.load(source, object_pairs_hook=_pairs, parse_constant=_constant)
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        raise ValidationError("No se pudo leer un documento JSON válido.") from exc


def read_bytes(path, maximum=MAX_PHOTO_BYTES):
    try:
        with Path(path).open("rb") as source:
            result = source.read(maximum + 1)
        require(0 < len(result) <= maximum, "Archivo vacío o por encima del tamaño permitido.")
        return result
    except OSError as exc:
        raise ValidationError("No se pudo leer una entrada privada.") from exc


def safe_id(value):
    return isinstance(value, str) and IDENTIFIER.fullmatch(value) is not None


def valid_sha(value):
    return isinstance(value, str) and SHA.fullmatch(value) is not None


def timestamp(value):
    require(isinstance(value, str), "Fecha de congelamiento ausente.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError("Fecha de congelamiento inválida.") from exc
    require(
        parsed.tzinfo is not None and parsed <= datetime.now(UTC),
        "Fecha de congelamiento inválida.",
    )
    return parsed


def implementation_hashes():
    paths = {
        "prompt_v2": "apps/ai_client_app/prompts/classification_v2.txt",
        "parser": "apps/ai_client_app/parser.py",
        "normalization": "apps/reports_app/services/photos.py",
        "adapter_openai": "apps/ai_client_app/adapters/openai.py",
        "contracts": "apps/ai_client_app/contracts.py",
        "prompt_renderer": "apps/ai_client_app/prompts/__init__.py",
    }
    return {name: digest((BACKEND / path).read_bytes()) for name, path in paths.items()}


def prompt_catalog():
    text = (BACKEND / "apps/ai_client_app/prompts/classification_v2.txt").read_text(
        encoding="utf-8"
    )
    matches = re.findall(r"^- ([a-z0-9-]+)\s+-> ([a-z0-9-]+)$", text, re.MULTILINE)
    require(bool(matches), "La tabla del prompt v2 no se pudo verificar.")
    require(len(dict(matches)) == len(matches), "Tabla del prompt duplicada.")
    return dict(matches)


def validate_catalog(catalog):
    exact(
        catalog,
        {
            "captured_at",
            "environment",
            "source",
            "categories",
            "institutions",
            "public_category_slugs",
        },
    )
    timestamp(catalog["captured_at"])
    require(safe_id(catalog["environment"]), "Identificador de entorno inválido.")
    require(
        catalog["source"] == "active-orm+GET /api/v1/public/categories/",
        "Procedencia de catálogo inválida.",
    )
    institutions = catalog["institutions"]
    require(
        isinstance(institutions, list)
        and institutions == sorted(set(institutions))
        and all(isinstance(s, str) and SLUG.fullmatch(s) for s in institutions),
        "Instituciones inválidas.",
    )
    categories = catalog["categories"]
    require(isinstance(categories, list) and bool(categories), "Categorías ausentes.")
    mapping = {}
    for entry in categories:
        exact(entry, {"slug", "institution_slug"})
        slug, institution = entry["slug"], entry["institution_slug"]
        require(
            isinstance(slug, str) and SLUG.fullmatch(slug) and slug not in mapping,
            "Categoría inválida o duplicada.",
        )
        require(institution in institutions, "Relación de catálogo inválida.")
        mapping[slug] = institution
    require(list(mapping) == sorted(mapping), "Orden de catálogo inválido.")
    require(mapping == prompt_catalog(), "El catálogo no coincide con la tabla del prompt v2.")
    public_slugs = catalog["public_category_slugs"]
    require(
        public_slugs == sorted(mapping), "El catálogo no coincide con la captura de API pública."
    )
    return mapping


def check_public_api(catalog, path):
    """La API vigente solo publica id/name/slug; NO publica asociaciones."""
    payload = read_json(path)
    require(isinstance(payload, list), "Captura de API inválida.")
    slugs = []
    for row in payload:
        exact(row, {"id", "name", "slug"}, "Contrato de API de categorías distinto del vigente.")
        require(
            all(isinstance(row[key], str) and row[key] for key in row), "Captura de API inválida."
        )
        slugs.append(row["slug"])
    require(
        len(set(slugs)) == len(slugs) and sorted(slugs) == catalog["public_category_slugs"],
        "El catálogo no coincide con la API capturada.",
    )


def normalize_photo(path, declared_mime):
    # Reutiliza exactamente el normalizador del producto; sin ORM ni settings.
    try:
        from apps.reports_app.services.photos import prepare_report_photo
    except ImportError as exc:
        raise ValidationError(
            "Falta Pillow, dependencia del backend para validar imágenes."
        ) from exc
    original = read_bytes(path)
    photo = io.BytesIO(original)
    photo.name = Path(path).name
    photo.content_type = declared_mime
    try:
        prepared = prepare_report_photo(photo)
    except ValueError as exc:
        raise ValidationError("Imagen incompatible con la normalización vigente.") from exc
    return original, prepared.content, prepared.content_type


def validate_dataset(dataset_path, gold_path, exclusions_path, public_api_path=None):
    dataset, gold, exclusions = (
        read_json(path) for path in (dataset_path, gold_path, exclusions_path)
    )
    exact(
        dataset,
        {
            "schema_version",
            "dataset_id",
            "state",
            "purpose",
            "frozen_n",
            "protocol",
            "provider",
            "implementation_sha256",
            "catalog",
            "gold_sha256",
            "exclusions_sha256",
            "cases",
        },
    )
    require(
        dataset["schema_version"] == VERSION and dataset["state"] == "frozen",
        "Dataset pendiente de congelamiento; no constituye evaluación.",
    )
    require(safe_id(dataset["dataset_id"]), "Identificador de dataset inválido.")
    require(
        dataset["purpose"] in {"reserved-evaluation", "offline-test"},
        "Propósito de dataset inválido.",
    )
    n = dataset["frozen_n"]
    require(type(n) is int and n > 0, "N debe congelarse como entero positivo.")
    require(
        dataset["implementation_sha256"] == implementation_hashes(),
        "El código vigente difiere de los hashes congelados.",
    )
    protocol = dataset["protocol"]
    exact(
        protocol,
        {
            "protocol_id",
            "frozen_at",
            "error_policy",
            "attempt_limit",
            "primary_metric",
            "secondary_metric",
            "threshold_percent",
            "sdk_max_retries",
            "script_max_retries",
            "celery_used",
        },
    )
    require(safe_id(protocol["protocol_id"]), "Identificador de protocolo inválido.")
    timestamp(protocol["frozen_at"])
    require(
        protocol["error_policy"] in {"invalidate", "count_as_miss"}, "Política de errores inválida."
    )
    require(
        type(protocol["attempt_limit"]) is int
        and protocol["attempt_limit"] == 1
        and type(protocol["sdk_max_retries"]) is int
        and protocol["sdk_max_retries"] == 0
        and type(protocol["script_max_retries"]) is int
        and protocol["script_max_retries"] == 0
        and protocol["celery_used"] is False,
        "El protocolo exige una pasada sin reintentos.",
    )
    require(
        protocol["primary_metric"] == "exact-category-institution-priority-before-policy"
        and protocol["secondary_metric"] == "exact-category-priority-before-policy"
        and type(protocol["threshold_percent"]) is int
        and protocol["threshold_percent"] == 85,
        "Métricas o umbral distintos del protocolo S15.",
    )
    provider = dataset["provider"]
    exact(provider, {"name", "model", "prompt_version", "reasoning_effort", "timeout_seconds"})
    require(
        provider["name"] == "openai" and provider["prompt_version"] == "v2",
        "Proveedor o prompt no admitido.",
    )
    require(
        isinstance(provider["model"], str) and MODEL.fullmatch(provider["model"]),
        "Modelo explícito requerido.",
    )
    require(
        provider["reasoning_effort"] in {"", "none", "minimal", "low", "medium", "high", "xhigh"},
        "Esfuerzo de razonamiento inválido.",
    )
    require(
        type(provider["timeout_seconds"]) is int and 1 <= provider["timeout_seconds"] <= 300,
        "Timeout inválido.",
    )
    mapping = validate_catalog(dataset["catalog"])
    if public_api_path:
        check_public_api(dataset["catalog"], public_api_path)
    exact(
        exclusions,
        {"schema_version", "scene_ids", "origin_ids", "original_sha256", "normalized_sha256"},
    )
    require(
        exclusions["schema_version"] == "s15-exclusions-v1"
        and object_hash(exclusions) == dataset["exclusions_sha256"],
        "Registro histórico de exclusiones no coincide.",
    )
    for field in ("scene_ids", "origin_ids", "original_sha256", "normalized_sha256"):
        values = exclusions[field]
        validator = safe_id if field.endswith("ids") else valid_sha
        require(
            isinstance(values, list)
            and all(validator(value) for value in values)
            and len(set(values)) == len(values),
            "Registro histórico de exclusiones inválido.",
        )
    exact(gold, {"schema_version", "dataset_id", "review", "labels"})
    require(
        gold["schema_version"] == GOLD_VERSION
        and gold["dataset_id"] == dataset["dataset_id"]
        and object_hash(gold) == dataset["gold_sha256"],
        "Oro no coincide con el conjunto congelado.",
    )
    exact(gold["review"], {"reviewer_ids", "rubric", "adjudicated_before_prediction"})
    reviewers = gold["review"]["reviewer_ids"]
    require(
        isinstance(reviewers, list)
        and len(reviewers) >= 2
        and all(safe_id(r) for r in reviewers)
        and len(set(reviewers)) == len(reviewers)
        and gold["review"]["rubric"] == "classification-v2"
        and gold["review"]["adjudicated_before_prediction"] is True,
        "Falta doble revisión humana prospectiva del oro v2.",
    )
    labels = {}
    require(isinstance(gold["labels"], list), "Etiquetas de oro inválidas.")
    for label in gold["labels"]:
        exact(label, {"case_id", "category_slug", "institution_slug", "priority"})
        require(
            safe_id(label["case_id"]) and label["case_id"] not in labels,
            "ID de oro inválido o duplicado.",
        )
        require(
            label["category_slug"] is None or label["category_slug"] in mapping,
            "Categoría de oro inválida.",
        )
        require(
            label["institution_slug"] is None
            or label["institution_slug"] in dataset["catalog"]["institutions"],
            "Institución de oro inválida.",
        )
        require(label["priority"] in PRIORITIES, "Prioridad de oro inválida.")
        if label["category_slug"] is not None:
            require(
                label["institution_slug"] == mapping[label["category_slug"]],
                "Relación categoría/institución de oro incompatible con v2.",
            )
        labels[label["case_id"]] = label
    cases = dataset["cases"]
    require(
        isinstance(cases, list) and len(cases) == n and len(labels) == n,
        "N congelado no coincide con todos los casos y etiquetas.",
    )
    seen = {key: set() for key in ("case_id", "scene_id", "original_sha256", "normalized_sha256")}
    prepared_cases = []
    for case in cases:
        exact(case, {"case_id", "scene_id", "origin_id", "request", "photo", "review"})
        require(
            all(safe_id(case[key]) for key in ("case_id", "scene_id", "origin_id")),
            "Identificador de caso/escena/origen inválido.",
        )
        require(case["case_id"] in labels, "Caso sin oro congelado.")
        exact(
            case["request"],
            {"description", "current_category_slug", "current_institution_slug"},
            "La entrada permite únicamente la lista blanca; oro o identidad no están permitidos.",
        )
        request = case["request"]
        require(
            isinstance(request["description"], str)
            and bool(request["description"].strip())
            and len(request["description"]) <= 10000,
            "Descripción inválida.",
        )
        require(
            request["current_category_slug"] in mapping
            and request["current_institution_slug"] == mapping[request["current_category_slug"]],
            "Categoría inicial incompatible con catálogo vigente.",
        )
        exact(
            case["review"],
            {"rights_confirmed", "pii_reviewed", "scene_reserved", "not_used_for_prompt_tuning"},
        )
        require(
            all(value is True for value in case["review"].values()),
            "Faltan autorización, revisión de privacidad o independencia por escena.",
        )
        photo = case["photo"]
        exact(
            photo,
            {
                "original_path",
                "original_sha256",
                "normalized_sha256",
                "normalized_bytes",
                "mime_type",
            },
        )
        require(
            isinstance(photo["original_path"], str) and bool(photo["original_path"]),
            "Ruta privada de foto requerida.",
        )
        require(
            valid_sha(photo["original_sha256"]) and valid_sha(photo["normalized_sha256"]),
            "Hashes de fotografía inválidos.",
        )
        require(
            photo["mime_type"] in {"image/jpeg", "image/png"}
            and type(photo["normalized_bytes"]) is int
            and 0 < photo["normalized_bytes"] <= MAX_PHOTO_BYTES,
            "MIME o tamaño normalizado inválido.",
        )
        path = Path(dataset_path).resolve().parent / photo["original_path"]
        original, normalized, mime = normalize_photo(path, photo["mime_type"])
        require(
            digest(original) == photo["original_sha256"]
            and digest(normalized) == photo["normalized_sha256"]
            and len(normalized) == photo["normalized_bytes"]
            and mime == photo["mime_type"],
            "Los bytes originales o enviados no coinciden con el congelamiento.",
        )
        for key in seen:
            value = photo[key] if key in photo else case[key]
            require(
                value not in seen[key], "Caso, escena o fotografía duplicada en el denominador."
            )
            seen[key].add(value)
        for key in ("scene_id", "origin_id", "original_sha256", "normalized_sha256"):
            values = exclusions[key + "s"] if key in {"scene_id", "origin_id"} else exclusions[key]
            value = photo[key] if key in photo else case[key]
            require(
                value not in values, "Escena/origen/foto conocida: no es evaluación independiente."
            )
        prepared_cases.append((case, normalized))
    require(set(labels) == seen["case_id"], "Hay etiquetas o casos omitidos.")
    return dataset, gold, prepared_cases


def build_request(case, image_bytes, catalog):
    from apps.ai_client_app.contracts import ClassificationRequest

    # Esta es la única frontera hacia el adaptador. Ningún metadato del caso/oro.
    request = case["request"]
    return ClassificationRequest(
        description=request["description"],
        current_category_slug=request["current_category_slug"],
        current_institution_slug=request["current_institution_slug"],
        allowed_category_slugs=tuple(row["slug"] for row in catalog["categories"]),
        allowed_institution_slugs=tuple(catalog["institutions"]),
        allowed_priorities=tuple(PRIORITIES),
        image_bytes=image_bytes,
        image_mime_type=case["photo"]["mime_type"],
    )


def request_hash(case, catalog):
    return object_hash(
        {
            **case["request"],
            "allowed_category_slugs": [row["slug"] for row in catalog["categories"]],
            "allowed_institution_slugs": catalog["institutions"],
            "allowed_priorities": PRIORITIES,
            "image_sha256": case["photo"]["normalized_sha256"],
            "image_mime_type": case["photo"]["mime_type"],
        }
    )


def safe_output_directory(path):
    resolved = Path(path).expanduser().resolve()
    forbidden = [ROOT, Path(tempfile.gettempdir()).resolve(), Path("/tmp"), Path("/var/tmp")]
    require(
        not any(resolved == parent or parent in resolved.parents for parent in forbidden),
        "La evidencia debe estar fuera del repositorio y de directorios temporales.",
    )
    require(not resolved.is_symlink(), "Destino de evidencia inválido.")
    resolved.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(resolved, 0o700)
    return resolved


def atomic_json(path, value):
    path = Path(path)
    missing = []
    parent = path.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for directory_path in reversed(missing):
        directory_path.mkdir(mode=0o700)
    require(
        not path.is_symlink() and not path.parent.is_symlink(), "Artefacto de evidencia inválido."
    )
    descriptor, temporary = tempfile.mkstemp(prefix=".s15-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as target:
            os.fchmod(target.fileno(), 0o600)
            target.write(canonical(value) + b"\n")
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
