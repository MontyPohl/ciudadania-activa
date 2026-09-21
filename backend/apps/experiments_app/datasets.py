from __future__ import annotations

import base64
import binascii
import hashlib
import struct
import uuid
import zlib
from dataclasses import dataclass
from typing import Any

from apps.admin_app.catalogs import CATEGORIES, INSTITUTIONS

from .canonical import canonical_json_bytes, sha256_bytes
from .constants import DEFAULT_SEED, GENERATOR_VERSION

_DATASET_NAMESPACE = uuid.UUID("41f45d43-37e3-514e-9b44-92f468b8a02a")
FUNCTIONAL_DATASET_ID = "s11-functional-v1"
LOAD_DATASET_ID = "s11-load-v1"
AI_DATASET_ID = "s8-evaluation-v1"
AI_DATASET_SHA256 = "3bc54d591dc104aeabeecb2f5cc7c7a38e70ba042e7f045321ab33ae9b5efad4"


@dataclass(frozen=True, slots=True)
class GeneratedDataset:
    dataset_id: str
    seed: int
    payload: dict[str, Any]

    @property
    def bytes(self) -> bytes:
        return canonical_json_bytes(self.payload)

    @property
    def sha256(self) -> str:
        return sha256_bytes(self.bytes)


def generate_dataset(dataset_id: str, *, seed: int = DEFAULT_SEED) -> GeneratedDataset:
    if dataset_id == FUNCTIONAL_DATASET_ID:
        return _functional(seed)
    if dataset_id == LOAD_DATASET_ID:
        return _load(seed)
    raise ValueError(f"Dataset S11 desconocido: {dataset_id}")


def dataset_registry(seed: int = DEFAULT_SEED) -> dict[str, dict[str, Any]]:
    functional = generate_dataset(FUNCTIONAL_DATASET_ID, seed=seed)
    load = generate_dataset(LOAD_DATASET_ID, seed=seed)
    return {
        FUNCTIONAL_DATASET_ID: {
            "seed": seed,
            "sha256": functional.sha256,
            "scenarios": [f"EX-{number:02d}" for number in range(2, 9)],
        },
        LOAD_DATASET_ID: {
            "seed": seed,
            "sha256": load.sha256,
            "scenarios": ["EX-10"],
        },
        AI_DATASET_ID: {
            "seed": None,
            "sha256": AI_DATASET_SHA256,
            "scenarios": ["EX-09"],
            "reference_only": True,
        },
    }


def _functional(seed: int) -> GeneratedDataset:
    namespace = "functional"
    actors = [
        _actor(seed, namespace, "citizen-author", "citizen", verified=False),
        _actor(seed, namespace, "citizen-confirmer", "citizen", verified=True),
        _actor(seed, namespace, "citizen-secondary", "citizen", verified=True),
        _actor(seed, namespace, "operator-a", "operator", verified=False),
        _actor(seed, namespace, "operator-b", "operator", verified=False),
        _actor(seed, namespace, "admin", "admin", verified=False),
    ]
    reports: list[dict[str, Any]] = []
    scenarios = [f"EX-{number:02d}" for number in range(2, 9)]
    for category_index, (slug, name, institution_slug, priority) in enumerate(CATEGORIES):
        for occurrence in range(2):
            index = category_index * 2 + occurrence
            latitude, longitude = _coordinates(seed, namespace, index)
            image = _png(seed, namespace, index)
            reports.append(
                {
                    "report_key": f"functional-report-{index + 1:02d}",
                    "id": _stable_uuid(seed, namespace, f"report-{index + 1}"),
                    "scenario_id": scenarios[index % len(scenarios)],
                    "category": {"slug": slug, "name": name},
                    "institution_slug": institution_slug,
                    "priority": priority,
                    "description": (
                        f"SYNTHETIC S11 {slug} caso {occurrence + 1}; "
                        "no representa una incidencia real."
                    ),
                    "location": {"latitude": latitude, "longitude": longitude, "srid": 4326},
                    "image": {
                        "filename": f"s11-functional-{index + 1:02d}.png",
                        "mime_type": "image/png",
                        "sha256": sha256_bytes(image),
                        "base64": base64.b64encode(image).decode("ascii"),
                    },
                    "expected_initial_cycle": 0,
                }
            )
    payload = _envelope(
        dataset_id=FUNCTIONAL_DATASET_ID,
        namespace=namespace,
        seed=seed,
        body={
            "synthetic_notice": "SYNTHETIC ONLY — NO PRODUCTION OR PERSONAL DATA",
            "institutions": [{"slug": slug, "name": name} for slug, name in INSTITUTIONS],
            "actors": actors,
            "reports": reports,
            "scenario_coverage": scenarios,
        },
    )
    return GeneratedDataset(FUNCTIONAL_DATASET_ID, seed, payload)


def _load(seed: int) -> GeneratedDataset:
    namespace = "load"
    users = [
        _actor(seed, namespace, f"load-citizen-{index + 1:02d}", "citizen", verified=True)
        for index in range(10)
    ]
    requests: list[dict[str, Any]] = []
    for user_index, user in enumerate(users):
        for sequence in range(5):
            index = user_index * 5 + sequence
            slug, _, institution_slug, priority = CATEGORIES[index % len(CATEGORIES)]
            latitude, longitude = _coordinates(seed, namespace, index)
            image = _png(seed, namespace, index)
            requests.append(
                {
                    "request_key": f"load-request-{index + 1:02d}",
                    "user_alias": user["alias"],
                    "sequence_for_user": sequence + 1,
                    "category_slug": slug,
                    "institution_slug": institution_slug,
                    "priority": priority,
                    "description": (
                        f"SYNTHETIC LOAD S11 request {index + 1:02d}; " "no es un reporte real."
                    ),
                    "latitude": latitude,
                    "longitude": longitude,
                    "image": {
                        "filename": f"s11-load-{index + 1:02d}.png",
                        "mime_type": "image/png",
                        "sha256": sha256_bytes(image),
                        "base64": base64.b64encode(image).decode("ascii"),
                    },
                }
            )
    payload = _envelope(
        dataset_id=LOAD_DATASET_ID,
        namespace=namespace,
        seed=seed,
        body={
            "synthetic_notice": "SYNTHETIC ONLY — NO PRODUCTION OR PERSONAL DATA",
            "users": users,
            "requests": requests,
            "load_shape": {
                "virtual_users": 10,
                "requests_per_user": 5,
                "total_requests": 50,
                "window_seconds": 60,
                "worker_concurrency": 2,
            },
            "scenario_coverage": ["EX-10"],
        },
    )
    return GeneratedDataset(LOAD_DATASET_ID, seed, payload)


def _envelope(
    *, dataset_id: str, namespace: str, seed: int, body: dict[str, Any]
) -> dict[str, Any]:
    return {
        "schema_version": "s11-synthetic-dataset-v1",
        "generator_version": GENERATOR_VERSION,
        "dataset_id": dataset_id,
        "seed": seed,
        "namespace": namespace,
        "locale": "es-PY",
        "timezone": "America/Asuncion",
        "synthetic": True,
        **body,
    }


def _actor(seed: int, namespace: str, alias: str, role: str, *, verified: bool) -> dict[str, Any]:
    return {
        "id": _stable_uuid(seed, namespace, f"actor-{alias}"),
        "alias": alias,
        "role": role,
        "verified": verified,
    }


def _stable_uuid(seed: int, namespace: str, label: str) -> str:
    return str(uuid.uuid5(_DATASET_NAMESPACE, f"{seed}:{namespace}:{label}"))


def _coordinates(seed: int, namespace: str, index: int) -> tuple[float, float]:
    # Malla sintética alrededor de un punto urbano general de Asunción. No procede
    # de hogares, reportes ni ubicaciones productivas.
    digest = hashlib.sha256(f"{seed}:{namespace}:geo:{index}".encode()).digest()
    lat_offset = int.from_bytes(digest[:2], "big") % 4000
    lon_offset = int.from_bytes(digest[2:4], "big") % 5000
    return round(-25.30 + lat_offset / 1_000_000, 6), round(-57.63 + lon_offset / 1_000_000, 6)


def _png(seed: int, namespace: str, index: int) -> bytes:
    digest = hashlib.sha256(f"{seed}:{namespace}:image:{index}".encode()).digest()
    width = height = 2
    rows = b"".join(
        b"\x00" + bytes(digest[(row + col) % len(digest)] for col in range(6)) for row in range(2)
    )
    compressed = zlib.compress(rows, level=9)
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        signature
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", compressed)
        + _png_chunk(b"IEND", b"")
    )


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    checksum = binascii.crc32(kind + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", checksum)
