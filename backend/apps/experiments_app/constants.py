from __future__ import annotations

from enum import StrEnum

#: Versión de S11-A que registra todo run nuevo: la enmienda `S11A-v1.1` del 26/09/2026
#: (`docs/tesis/S11A_DATOS_EVIDENCIA_Y_REBASELINE.md` §13).
METHODOLOGY_VERSION = "S11A-v1.1"
#: Versión con la que se ejecutaron los runs anteriores a la enmienda. Sólo es válida
#: para ellos: un run nuevo que la declarase quedaría registrado bajo un texto que ya
#: no rige.
HISTORICAL_METHODOLOGY_VERSION = "S11A-v1.0"
#: Los 16 runs ejecutados bajo `S11A-v1.0`, enumerados uno a uno a partir de sus
#: manifests custodiados en `var/s11-runs/runs/`: C1 (dos), EX-02…EX-08 y sus intentos
#: `NOT_DEMONSTRATED`. Ninguno es de EX-09. La lista es cerrada y no se amplía: nada
#: puede ejecutarse ya bajo `S11A-v1.0`.
HISTORICAL_METHODOLOGY_RUN_IDS = frozenset(
    {
        "s11-99e928d6-f4b5-52bf-8f33-c69c5e5d50c1",  # C1
        "s11-8448491d-3347-5c00-9f44-d8ae110e9095",  # C1
        "s11-3b48870b-7979-542b-8a46-1c6dfce7bed8",  # EX-02
        "s11-d407a461-ca75-5809-97bd-bcfea7593f3e",  # EX-02
        "s11-41eaa7d0-67c8-53b1-aa9a-38068859dd1f",  # EX-02
        "s11-95954a43-507b-5093-824d-8f0c83c1a1a7",  # EX-02
        "s11-6efb78d7-ca14-523f-b7e5-7c5d6ee14fad",  # EX-02
        "s11-4df882ed-9dfe-59a2-888b-ca1efc98d6af",  # EX-02
        "s11-6dc3ea66-08c9-51ef-9fe3-70e7dcc67959",  # EX-03
        "s11-03adb9bc-c9e4-5f8f-b64c-c4360d0c2085",  # EX-04
        "s11-1961b2d4-2c48-5cc0-ac84-8847dda2de39",  # EX-05
        "s11-0e2feac0-6583-513c-a6aa-33430394d14a",  # EX-05
        "s11-989218f3-4808-5c6f-ac23-6e8870fec153",  # EX-06
        "s11-176a55e0-1c27-5b2f-a0f2-ef50dbe6d959",  # EX-07
        "s11-545491b8-01fe-55e6-8f08-1a0ebbdca8d3",  # EX-07
        "s11-b567289c-9fe2-5349-a384-e9284bdde863",  # EX-08
    }
)
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
