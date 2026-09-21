from __future__ import annotations

from enum import StrEnum

METHODOLOGY_VERSION = "S11A-v1.0"
GENERATOR_VERSION = "s11b-generator-v1"
DEFAULT_SEED = 20260921

RUN_ID_HEADER = "HTTP_X_S11_RUN_ID"
REQUEST_ID_HEADER = "HTTP_X_S11_REQUEST_ID"
RUN_ID_WIRE_HEADER = "X-S11-Run-ID"
REQUEST_ID_WIRE_HEADER = "X-S11-Request-ID"

SUPPORTED_SCENARIOS = tuple(f"EX-{number:02d}" for number in range(2, 11))
FULLSTACK_SCENARIOS = tuple(f"EX-{number:02d}" for number in range(2, 9))


class Classification(StrEnum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    EXPERIMENTAL = "EXPERIMENTAL"
    SENSITIVE = "SENSITIVE"
    SECRET = "SECRET"


class ArtifactKind(StrEnum):
    RAW = "raw"
    PROCESSED = "processed"
    DERIVED = "derived"
    LOG = "logs"
    METADATA = "metadata"


class RunStatus(StrEnum):
    PREPARED = "PREPARED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    INVALID = "INVALID"


class Decision(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_DEMONSTRATED = "NOT_DEMONSTRATED"
