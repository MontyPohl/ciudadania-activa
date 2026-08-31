"""Runner determinista de evaluación (S8-E1).

Adapter-agnostic: el adaptador entra por parámetro. Este módulo no resuelve el
proveedor de runtime, no lee configuración de Gemini, no toca el ORM, no usa
Celery y no persiste nada. La misma maquinaria sirve para el adaptador falso en
CI y para el proveedor real en un gate separado.

Determinista por construcción: el informe no lleva marcas de tiempo,
identificadores aleatorios, rutas locales ni duraciones. Dos ejecuciones con el
mismo dataset, el mismo adaptador y el mismo modelo producen el mismo informe
byte a byte, que es lo que hace comparables dos mediciones.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..contracts import ClassificationRequest
from ..parser import ClassificationParseError, parse_classification_response
from .dataset import EvaluationCase, EvaluationDataset
from .scoring import CaseResult, compare_case, failed_case, percentage


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    """Resultado agregado de una evaluación.

    `ca4_concordance_pct` es la **métrica oficial**: categoría, institución y
    prioridad, según el Anexo H de la tesis. Es la única a la que se aplica el
    umbral del 85 %.

    `strict_concordance_pct` añade `inconsistency` y es **solo diagnóstico**. No
    tiene umbral: no es un criterio de aceptación.

    No expone `meets_threshold` a propósito. Con el adaptador falso ese campo
    invitaría a leer un número como si dijera algo sobre la calidad del modelo, y
    no dice nada: la comparación con el 85 % solo tiene sentido tras medir contra
    el proveedor real (contrato S8-A, D19).
    """

    dataset_version: str
    adapter_name: str
    model_name: str
    total_cases: int
    ca4_concordant_cases: int
    ca4_non_concordant_cases: int
    ca4_concordance_pct: Decimal
    strict_concordant_cases: int
    strict_non_concordant_cases: int
    strict_concordance_pct: Decimal
    category_correct: int
    category_accuracy_pct: Decimal
    institution_correct: int
    institution_accuracy_pct: Decimal
    priority_correct: int
    priority_accuracy_pct: Decimal
    inconsistency_correct: int
    inconsistency_accuracy_pct: Decimal
    errors_count: int
    cases: tuple[CaseResult, ...]


def evaluate_dataset(
    dataset: EvaluationDataset,
    adapter,
    *,
    adapter_name: str,
    model_name: str,
) -> EvaluationReport:
    """Ejecuta el dataset completo contra `adapter` y agrega las métricas.

    Un caso que falla no interrumpe la corrida: se registra como no concordante y
    la evaluación continúa. Abortar por un caso daría una medición parcial
    disfrazada de completa.
    """
    resultados = tuple(_evaluate_case(case, dataset, adapter) for case in dataset.cases)
    return _build_report(dataset, resultados, adapter_name=adapter_name, model_name=model_name)


def build_request(case: EvaluationCase, dataset: EvaluationDataset) -> ClassificationRequest:
    """Construye la petición con la taxonomía congelada del dataset.

    Deliberadamente **no** viajan el identificador del caso, las etiquetas de oro
    ni las notas: preguntarle al modelo con la respuesta delante no mediría nada.

    `image_bytes` va vacío. S8-E1 mide clasificación semántica sobre texto y la
    maquinaria de evaluación; incorporar imágenes exigiría leer Storage, que está
    fuera de este gate.
    """
    return ClassificationRequest(
        description=case.description,
        current_category_slug=case.current_category_slug,
        current_institution_slug=case.current_institution_slug,
        allowed_category_slugs=dataset.catalog.category_slugs,
        allowed_institution_slugs=dataset.catalog.institution_slugs,
        allowed_priorities=dataset.catalog.priorities,
        image_bytes=b"",
        image_mime_type=None,
    )


def _evaluate_case(case: EvaluationCase, dataset: EvaluationDataset, adapter) -> CaseResult:
    request = build_request(case, dataset)

    try:
        raw_response = adapter.classify(request)
    except Exception as exc:
        return failed_case(case, type(exc).__name__)

    try:
        # El mismo parser que protege el runtime protege la evaluación: medir con
        # una validación más laxa daría un número que producción no reproduce.
        parsed = parse_classification_response(raw_response)
    except ClassificationParseError:
        return failed_case(case, ClassificationParseError.__name__)

    return compare_case(case, parsed)


def _build_report(
    dataset: EvaluationDataset,
    resultados: tuple[CaseResult, ...],
    *,
    adapter_name: str,
    model_name: str,
) -> EvaluationReport:
    total = len(resultados)
    # Oficial: categoría, institución y prioridad (Anexo H).
    ca4 = sum(1 for r in resultados if r.ca4_concordant)
    # Diagnóstico: lo anterior más haber reconocido la inconsistencia.
    estricta = sum(1 for r in resultados if r.strict_concordant)
    categoria = sum(1 for r in resultados if r.category_match)
    institucion = sum(1 for r in resultados if r.institution_match)
    prioridad = sum(1 for r in resultados if r.priority_match)
    inconsistencia = sum(1 for r in resultados if r.inconsistency_match)
    errores = sum(1 for r in resultados if r.error_type is not None)

    return EvaluationReport(
        dataset_version=dataset.dataset_version,
        adapter_name=adapter_name,
        model_name=model_name,
        total_cases=total,
        ca4_concordant_cases=ca4,
        ca4_non_concordant_cases=total - ca4,
        ca4_concordance_pct=percentage(ca4, total),
        strict_concordant_cases=estricta,
        strict_non_concordant_cases=total - estricta,
        strict_concordance_pct=percentage(estricta, total),
        category_correct=categoria,
        category_accuracy_pct=percentage(categoria, total),
        institution_correct=institucion,
        institution_accuracy_pct=percentage(institucion, total),
        priority_correct=prioridad,
        priority_accuracy_pct=percentage(prioridad, total),
        inconsistency_correct=inconsistencia,
        inconsistency_accuracy_pct=percentage(inconsistencia, total),
        errors_count=errores,
        cases=resultados,
    )
