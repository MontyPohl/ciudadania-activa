"""Carga y validación del dataset sintético congelado (S8-E1).

El dataset es un artefacto **versionado y congelado**: su contenido define la
medición de CA-4, así que cambiarlo cambia el significado del número que se
reporta. Por eso la validación es estricta y hay una prueba que fija su hash: una
modificación futura rompe esa prueba a propósito y obliga a una decisión
explícita, en vez de mover el listón en silencio.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DATASET_PATH = Path(__file__).resolve().parent / "dataset_s8_v1.json"

MIN_CASES = 20

_CASE_FIELDS = frozenset(
    {"id", "description", "current_category_slug", "current_institution_slug", "expected", "notes"}
)
_EXPECTED_FIELDS = frozenset({"category_slug", "institution_slug", "priority", "inconsistency"})
_TOP_LEVEL_FIELDS = frozenset({"dataset_version", "description", "catalog", "cases"})
_CATALOG_FIELDS = frozenset({"category_slugs", "institution_slugs", "priorities"})

# Nombres que no deben existir como campos del dataset. No es un detector de PII:
# es una barrera estructural para que el formato no invite a guardarla.
FORBIDDEN_FIELDS = frozenset(
    {
        "citizen",
        "citizen_id",
        "user",
        "user_id",
        "email",
        "phone",
        "latitude",
        "longitude",
        "coordinates",
        "address",
        "report_id",
        "created_at",
        "updated_at",
        "storage_path",
        "bucket",
        "file_hash",
    }
)


class InvalidEvaluationDataset(ValueError):
    """El dataset no cumple su contrato. Aborta antes de evaluar nada."""


@dataclass(frozen=True, slots=True)
class ExpectedClassification:
    """Etiqueta de oro de un caso."""

    category_slug: str | None
    institution_slug: str | None
    priority: str
    inconsistency: bool


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    """Un caso del benchmark. `notes` explica por qué existe; no se envía a nadie."""

    id: str
    description: str
    current_category_slug: str
    current_institution_slug: str | None
    expected: ExpectedClassification
    notes: str = ""


@dataclass(frozen=True, slots=True)
class EvaluationCatalog:
    """Taxonomía congelada en el momento de crear el dataset.

    Se guarda dentro del propio archivo y no se lee de la base de datos: si el
    catálogo de la aplicación cambia en un sprint futuro, el benchmark v1 debe
    seguir midiendo lo mismo que medía, o dejar de usarse.
    """

    category_slugs: tuple[str, ...]
    institution_slugs: tuple[str, ...]
    priorities: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EvaluationDataset:
    dataset_version: str
    description: str
    catalog: EvaluationCatalog
    cases: tuple[EvaluationCase, ...]


def load_dataset(path: Path | None = None) -> EvaluationDataset:
    """Lee y valida el dataset. Los casos conservan el orden del archivo."""
    source = path or DATASET_PATH
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise InvalidEvaluationDataset("No se encontró el dataset de evaluación.") from exc
    except ValueError as exc:
        raise InvalidEvaluationDataset("El dataset no es JSON válido.") from exc

    return build_dataset(raw)


def build_dataset(raw: object) -> EvaluationDataset:
    """Valida la estructura completa y devuelve el dataset congelado."""
    if not isinstance(raw, dict):
        raise InvalidEvaluationDataset("El dataset debe ser un objeto JSON.")

    _reject_unknown(set(raw), _TOP_LEVEL_FIELDS, "raíz")

    version = raw.get("dataset_version")
    if not isinstance(version, str) or not version.strip():
        raise InvalidEvaluationDataset("Falta `dataset_version`.")

    catalog = _build_catalog(raw.get("catalog"))
    cases = _build_cases(raw.get("cases"), catalog)

    return EvaluationDataset(
        dataset_version=version,
        description=str(raw.get("description", "")),
        catalog=catalog,
        cases=cases,
    )


def _build_catalog(raw: object) -> EvaluationCatalog:
    if not isinstance(raw, dict):
        raise InvalidEvaluationDataset("Falta el catálogo congelado.")
    _reject_unknown(set(raw), _CATALOG_FIELDS, "catalog")

    valores = {}
    for campo in ("category_slugs", "institution_slugs", "priorities"):
        lista = raw.get(campo)
        if not isinstance(lista, list) or not lista:
            raise InvalidEvaluationDataset(f"El catálogo requiere `{campo}` no vacío.")
        if not all(isinstance(v, str) and v for v in lista):
            raise InvalidEvaluationDataset(f"`{campo}` debe contener cadenas no vacías.")
        if len(set(lista)) != len(lista):
            raise InvalidEvaluationDataset(f"`{campo}` contiene duplicados.")
        valores[campo] = tuple(lista)

    return EvaluationCatalog(**valores)


def _build_cases(raw: object, catalog: EvaluationCatalog) -> tuple[EvaluationCase, ...]:
    if not isinstance(raw, list):
        raise InvalidEvaluationDataset("`cases` debe ser una lista.")
    if len(raw) < MIN_CASES:
        raise InvalidEvaluationDataset(
            f"El dataset requiere al menos {MIN_CASES} casos; tiene {len(raw)}."
        )

    vistos: set[str] = set()
    casos: list[EvaluationCase] = []

    for entrada in raw:
        if not isinstance(entrada, dict):
            raise InvalidEvaluationDataset("Cada caso debe ser un objeto JSON.")
        _reject_unknown(set(entrada), _CASE_FIELDS, "caso")

        case_id = entrada.get("id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise InvalidEvaluationDataset("Cada caso necesita un `id` estable.")
        if case_id in vistos:
            raise InvalidEvaluationDataset(f"`id` duplicado: {case_id}.")
        vistos.add(case_id)

        description = entrada.get("description")
        if not isinstance(description, str) or not description.strip():
            raise InvalidEvaluationDataset(f"{case_id}: la descripción no puede estar vacía.")

        current_category = _slug(
            entrada.get("current_category_slug"),
            catalog.category_slugs,
            case_id,
            "current_category_slug",
            allow_none=False,
        )
        current_institution = _slug(
            entrada.get("current_institution_slug"),
            catalog.institution_slugs,
            case_id,
            "current_institution_slug",
            allow_none=True,
        )

        casos.append(
            EvaluationCase(
                id=case_id,
                description=description,
                current_category_slug=current_category,
                current_institution_slug=current_institution,
                expected=_build_expected(entrada.get("expected"), catalog, case_id),
                notes=str(entrada.get("notes", "")),
            )
        )

    return tuple(casos)


def _build_expected(
    raw: object, catalog: EvaluationCatalog, case_id: str
) -> ExpectedClassification:
    if not isinstance(raw, dict):
        raise InvalidEvaluationDataset(f"{case_id}: falta `expected`.")
    _reject_unknown(set(raw), _EXPECTED_FIELDS, f"{case_id}.expected")

    priority = raw.get("priority")
    if priority not in catalog.priorities:
        raise InvalidEvaluationDataset(f"{case_id}: prioridad esperada inválida.")

    inconsistency = raw.get("inconsistency")
    if type(inconsistency) is not bool:
        raise InvalidEvaluationDataset(f"{case_id}: `inconsistency` debe ser booleano.")

    return ExpectedClassification(
        category_slug=_slug(
            raw.get("category_slug"),
            catalog.category_slugs,
            case_id,
            "expected.category_slug",
            allow_none=True,
        ),
        institution_slug=_slug(
            raw.get("institution_slug"),
            catalog.institution_slugs,
            case_id,
            "expected.institution_slug",
            allow_none=True,
        ),
        priority=priority,
        inconsistency=inconsistency,
    )


def _slug(
    value: object, permitidos: tuple[str, ...], case_id: str, campo: str, *, allow_none: bool
) -> str | None:
    if value is None:
        if allow_none:
            return None
        raise InvalidEvaluationDataset(f"{case_id}: `{campo}` no puede ser nulo.")
    if not isinstance(value, str) or value not in permitidos:
        raise InvalidEvaluationDataset(f"{case_id}: `{campo}` no pertenece al catálogo congelado.")
    return value


def _reject_unknown(presentes: set[str], permitidos: frozenset[str], contexto: str) -> None:
    desconocidos = presentes - permitidos
    if desconocidos:
        raise InvalidEvaluationDataset(
            f"Campos desconocidos en {contexto}: {', '.join(sorted(desconocidos))}."
        )
    prohibidos = presentes & FORBIDDEN_FIELDS
    if prohibidos:
        raise InvalidEvaluationDataset(
            f"Campos prohibidos en {contexto}: {', '.join(sorted(prohibidos))}."
        )
