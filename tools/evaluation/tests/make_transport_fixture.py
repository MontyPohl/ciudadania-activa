#!/usr/bin/env python3
"""Crea fixtures explícitamente sintéticas para probar el CLI; NO evalúa visión."""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.evaluation.s15_common import (
    ROOT,
    PrivateArgumentParser,
    atomic_json,
    digest,
    implementation_hashes,
    normalize_photo,
    object_hash,
    prompt_catalog,
    read_json,
    require,
    safe_output_directory,
)


def create(directory):
    from PIL import Image

    root = safe_output_directory(directory)
    require(not list(root.iterdir()), "Las fixtures requieren un directorio vacío.")
    templates = ROOT / "tools/evaluation/templates"
    dataset = read_json(templates / "dataset.template.json")
    gold = read_json(templates / "gold.template.json")
    now = datetime.now(UTC).isoformat()
    dataset.update(
        dataset_id="s15-synthetic-transport-v1",
        state="frozen",
        purpose="offline-test",
        frozen_n=4,
        implementation_sha256=implementation_hashes(),
    )
    dataset["protocol"].update(
        protocol_id="s15-synthetic-transport-only", frozen_at=now, error_policy="count_as_miss"
    )
    dataset["provider"].update(model="gpt-test-offline", timeout_seconds=30)
    mapping = prompt_catalog()
    dataset["catalog"].update(
        captured_at=now,
        environment="synthetic-only",
        categories=[
            {"slug": slug, "institution_slug": institution}
            for slug, institution in sorted(mapping.items())
        ],
        institutions=sorted(set(mapping.values())),
        public_category_slugs=sorted(mapping),
    )
    gold.update(
        dataset_id=dataset["dataset_id"],
        review={
            "reviewer_ids": ["synthetic-a", "synthetic-b"],
            "rubric": "classification-v2",
            "adjudicated_before_prediction": True,
        },
    )
    exclusions = {
        "schema_version": "s15-exclusions-v1",
        "scene_ids": [],
        "origin_ids": [],
        "original_sha256": [],
        "normalized_sha256": [],
    }
    for index in range(4):
        case_id = f"transport-{index}"
        path = root / f"{case_id}.png"
        image = Image.new("RGB", (16 + index, 16), (index * 50, 30, 100))
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as target:
            image.save(target, format="PNG")
        image.close()
        original, normalized, mime = normalize_photo(path, "image/png")
        case = read_json(templates / "case.template.json")
        case.update(case_id=case_id, scene_id=case_id, origin_id=case_id)
        case["request"].update(
            description="Transporte sintético: ninguna foto urbana ni evaluación visual.",
            current_category_slug="baches",
            current_institution_slug="municipalidad-de-asuncion",
        )
        case["photo"].update(
            original_path=path.name,
            original_sha256=digest(original),
            normalized_sha256=digest(normalized),
            normalized_bytes=len(normalized),
            mime_type=mime,
        )
        case["review"] = {key: True for key in case["review"]}
        dataset["cases"].append(case)
        gold["labels"].append(
            {
                "case_id": case_id,
                "category_slug": "baches",
                "institution_slug": "municipalidad-de-asuncion",
                "priority": "MEDIA",
            }
        )
    dataset.update(gold_sha256=object_hash(gold), exclusions_sha256=object_hash(exclusions))
    raw = json.dumps(
        {
            "category_slug": "baches",
            "institution_slug": "municipalidad-de-asuncion",
            "priority": "MEDIA",
            "confidence": 0.9,
            "inconsistency": False,
            "justification": "Respuesta artificial de transporte, sin evaluación visual.",
        }
    )
    base = {
        "attempt": 1,
        "returned_model": None,
        "usage": {"input_tokens": None, "output_tokens": None, "total_tokens": None},
        "elapsed_ms": None,
    }
    responses = {
        "schema_version": "s15-responses-v1",
        "dataset_sha256": object_hash(dataset),
        "source": "offline-fixture",
        "responses": [
            {**base, "case_id": "transport-0", "status": "response", "raw_response": raw},
            {
                **base,
                "case_id": "transport-1",
                "status": "response",
                "raw_response": "invalid synthetic response",
            },
            {**base, "case_id": "transport-2", "status": "timeout", "raw_response": None},
            # transport-3 ausente: debe permanecer en N.
        ],
    }
    for filename, value in (
        ("dataset.json", dataset),
        ("gold.json", gold),
        ("exclusions.json", exclusions),
        ("responses.json", responses),
    ):
        atomic_json(root / filename, value)


def main(argv=None):
    parser = PrivateArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        create(args.output)
        print("SYNTHETIC_TRANSPORT_ONLY: 4 casos; ninguna llamada al proveedor; no es RNF-05.")
        return 0
    except Exception:
        print("No se pudieron preparar fixtures privadas de transporte.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
