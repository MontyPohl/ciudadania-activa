"""Entradas congelables de S15. No contiene escrituras ni selectores del producto."""

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from PIL import Image

VERSION = "s15-functional-v1"
ZONE = ZoneInfo("America/Asuncion")
INSTITUTIONS = ("municipalidad-de-asuncion", "ande", "essap")
CATEGORIES = (
    ("baches", INSTITUTIONS[0]),
    ("basura-acumulada", INSTITUTIONS[0]),
    ("alumbrado-publico", INSTITUTIONS[1]),
    ("agua-potable-perdidas", INSTITUTIONS[2]),
    ("drenaje-desague-cloaca", INSTITUTIONS[2]),
    ("semaforos", INSTITUTIONS[0]),
    ("escombros", INSTITUTIONS[0]),
    ("arbol-caido", INSTITUTIONS[0]),
    ("cables-expuestos-riesgo-electrico", INSTITUTIONS[1]),
    ("otros", INSTITUTIONS[0]),
)
PRIVACY = ("FULL_NAME", "FIRST_INITIAL", "ALIAS", "PRIVATE")


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def synthetic_image(seed: int, logical_id: str, image_format="JPEG") -> bytes:
    """Mosaico sin personas, texto, EXIF ni localización, distinto por entrada."""
    palette = hashlib.sha256(f"{VERSION}:{seed}:{logical_id}".encode()).digest()
    image = Image.new("RGB", (96, 96))
    pixels = image.load()
    for y in range(96):
        for x in range(96):
            i = ((x // 12) + (y // 12) * 8) % 32
            pixels[x, y] = (palette[i], palette[(i + 7) % 32], palette[(i + 19) % 32])
    output = io.BytesIO()
    image.save(output, format=image_format)
    image.close()
    return output.getvalue()


@dataclass(frozen=True)
class Recipe:
    reference_date: date
    seed: int = 15
    report_count: int = 36

    def __post_init__(self):
        if not 36 <= self.report_count <= 200:
            raise ValueError("Se requieren entre 36 y 200 reportes; 36 financia los casos mínimos.")
        if isinstance(self.seed, bool) or not 0 <= self.seed <= 2**31 - 1:
            raise ValueError("La semilla debe estar entre 0 y 2147483647.")

    @property
    def end(self):
        return datetime.combine(self.reference_date, time(23, 59, 59), ZONE)

    @property
    def start(self):
        return self.end - timedelta(days=60)

    @property
    def month(self):
        return self.end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    @property
    def next_month(self):
        return (self.month + timedelta(days=32)).replace(day=1)

    @property
    def operation(self):
        # Todos los canjes/reversiones ocurren dentro del mes del ancla, aun el día 1.
        return self.end - timedelta(minutes=30)

    def cases(self) -> list[dict]:
        cases = []
        for n in range(1, self.report_count + 1):
            if n <= 30:
                institution_index = (n - 1) // 10
                choices = [c for c, i in CATEGORIES if i == INSTITUTIONS[institution_index]]
                category = choices[(n - 1) % len(choices)]
                author, kind = institution_index, "funding"
                # R001 acredita justo antes del corte; R002 justo después.
                at = self.month - timedelta(seconds=10) if n == 1 else self.month
                at += timedelta(seconds=n * 10)
                if n == 1:
                    at = self.month - timedelta(seconds=10)
            else:
                category = CATEGORIES[(n - 31) % len(CATEGORIES)][0]
                author = {31: 3, 32: 4, 33: None, 34: 1, 35: 6, 36: 7}.get(n, (n - 1) % 12)
                kind = {
                    31: "pending",
                    32: "rejected",
                    33: "x_mock",
                    34: "surplus",
                    35: "revalidated",
                    36: "closed",
                }.get(n, "pending")
                if n == 34:
                    category = "alumbrado-publico"
                at = self.start if n == 31 else self.month + timedelta(minutes=10)
                if n == 36:
                    at = self.month - timedelta(hours=80)
            cases.append(
                {
                    "id": f"R{n:03}",
                    "number": n,
                    "kind": kind,
                    "author": author,
                    "category": category,
                    "institution": dict(CATEGORIES)[category],
                    "priority": "MEDIA"
                    if kind == "x_mock"
                    else ("BAJA", "MEDIA", "ALTA")[(n - 1) % 3],
                    "at": at.isoformat(),
                    "latitude": round(-25.20 - (n % 40) * 0.004, 6),
                    "longitude": round(-57.45 - (n // 40) * 0.025, 6),
                    "confirmants": [3 + (n - 1) % 9]
                    if n <= 30
                    else ([5] if n == 32 else [11] if n == 33 else []),
                }
            )
        return cases

    def plan(self) -> dict:
        return {
            "version": VERSION,
            "seed": self.seed,
            "reference_date": self.reference_date.isoformat(),
            "timezone": str(ZONE),
            "window_start": self.start.isoformat(),
            "window_end": self.end.isoformat(),
            "window_seconds": 60 * 86400,
            "report_count": self.report_count,
            "citizens": 12,
            "cases": self.cases(),
            "bootstrap": "Usuarios técnicos creados localmente; roles por promote_user auditado.",
            "providers": {
                "classification": "s15-synthetic-adapter",
                "mail": "captured-not-delivered",
                "storage": "private-local-disk",
                "queue": "captured-not-executed",
            },
            "funding": {"first_complete_cycles": 30, "beneficiaries": 3, "credits_each": 1000},
            "checkpoints": ["before_redemption", "after_redemption", "after_reversal"],
        }

    @property
    def identifier(self):
        return f"{VERSION}-{self.reference_date}-{self.seed}-{self.report_count}"
