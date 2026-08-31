"""Runner determinista de evaluación (S8-E1).

Adapter-agnostic: el adaptador entra por parámetro. Este módulo no resuelve el
proveedor de runtime, no lee configuración de Gemini, no toca el ORM, no usa
Celery y no persiste nada. La misma maquinaria sirve para el adaptador falso en
CI y para el proveedor real en un gate separado.

Determinista por construcción: el informe no lleva marcas de tiempo,
identificadores aleatorios, rutas locales ni duraciones. Dos ejecuciones con el
mismo dataset, el mismo adaptador y el mismo modelo producen el mismo informe
byte a byte, que es lo que hace comparables dos mediciones.

La instrumentación de S8-E2-R1 —duración y mensaje de error por caso— es
inherentemente no determinista, así que viaja **fuera** del informe, en
`EvaluationRun.diagnostics`. Mezclarla dentro habría convertido cada corrida en un
informe distinto y habría destruido justo la propiedad que hace archivable la
evidencia.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from decimal import Decimal

from ..contracts import ClassificationRequest
from ..parser import ClassificationParseError, parse_classification_response
from .dataset import EvaluationCase, EvaluationDataset
from .sanitization import sanitize_error_message
from .scoring import CaseResult, compare_case, failed_case, percentage

_NANOSEGUNDOS_POR_MILISEGUNDO = 1_000_000


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


@dataclass(frozen=True, slots=True)
class CaseDiagnostic:
    """Evidencia diagnóstica de un caso. No participa en ninguna métrica.

    Existe por lo aprendido en la corrida canónica S8-E2: 22 casos fallaron y solo
    se guardó el nombre de la clase, así que fue imposible saber después si habían
    sido tiempos agotados, 429 o 5xx. `error_message` conserva esa distinción ya
    sanitizada y `duration_ms` permite reconstruir el ritmo real de la corrida sin
    depender del reloj de pared del sistema de ficheros.
    """

    id: str
    error_type: str | None
    error_message: str | None
    duration_ms: int


@dataclass(frozen=True, slots=True)
class EvaluationRun:
    """Informe canónico y evidencia diagnóstica, deliberadamente separados.

    `report` conserva su determinismo byte a byte; `diagnostics` es la parte que
    cambia entre corridas. Quien archive una medición archiva el primero; quien
    investigue un fallo lee el segundo.
    """

    report: EvaluationReport
    diagnostics: tuple[CaseDiagnostic, ...]


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
    return run_evaluation(
        dataset,
        adapter,
        adapter_name=adapter_name,
        model_name=model_name,
    ).report


def run_evaluation(
    dataset: EvaluationDataset,
    adapter,
    *,
    adapter_name: str,
    model_name: str,
    cases: Iterable[EvaluationCase] | None = None,
    inter_request_delay_seconds: float = 0.0,
    sleep: Callable[[float], object] = time.sleep,
    monotonic_ns: Callable[[], int] = time.perf_counter_ns,
) -> EvaluationRun:
    """Evalúa `cases` (o el dataset entero) y devuelve informe + diagnóstico.

    `inter_request_delay_seconds` espacia las solicitudes **entre** casos y por
    defecto vale 0, de modo que ningún uso previo cambia de comportamiento. No es
    un timeout: el timeout acota cuánto se espera *una* respuesta, mientras que
    este retardo acota cada cuánto se pide la siguiente. Se duerme antes de cada
    caso salvo el primero, así que N casos producen N-1 esperas y la corrida no
    empieza ni termina durmiendo.

    `sleep` y `monotonic_ns` entran por parámetro para que las pruebas midan el
    pacing sin esperar en tiempo real. El reloj es monotónico a propósito: un
    ajuste de la hora del sistema no debe poder producir una duración negativa.
    """
    seleccion = tuple(dataset.cases if cases is None else cases)

    resultados: list[CaseResult] = []
    diagnosticos: list[CaseDiagnostic] = []
    for indice, case in enumerate(seleccion):
        if indice > 0 and inter_request_delay_seconds > 0:
            sleep(inter_request_delay_seconds)

        inicio = monotonic_ns()
        resultado, mensaje = _evaluate_case(case, dataset, adapter)
        transcurrido = monotonic_ns() - inicio

        resultados.append(resultado)
        diagnosticos.append(
            CaseDiagnostic(
                id=case.id,
                error_type=resultado.error_type,
                error_message=mensaje,
                # `max` protege de un reloj que retroceda: la duración es una
                # magnitud física y un número negativo en la evidencia sería ruido.
                duration_ms=max(0, transcurrido // _NANOSEGUNDOS_POR_MILISEGUNDO),
            )
        )

    report = build_report(
        dataset,
        tuple(resultados),
        adapter_name=adapter_name,
        model_name=model_name,
    )
    return EvaluationRun(report=report, diagnostics=tuple(diagnosticos))


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


def _evaluate_case(
    case: EvaluationCase, dataset: EvaluationDataset, adapter
) -> tuple[CaseResult, str | None]:
    """Evalúa un caso. Devuelve el resultado y el mensaje del fallo, si lo hubo.

    El mensaje sale por separado y ya sanitizado: `CaseResult` alimenta las
    métricas y debe seguir siendo determinista, así que no es su sitio.
    """
    request = build_request(case, dataset)

    try:
        raw_response = adapter.classify(request)
    except Exception as exc:
        return failed_case(case, type(exc).__name__), sanitize_error_message(exc)

    try:
        # El mismo parser que protege el runtime protege la evaluación: medir con
        # una validación más laxa daría un número que producción no reproduce.
        parsed = parse_classification_response(raw_response)
    except ClassificationParseError as exc:
        return (
            failed_case(case, ClassificationParseError.__name__),
            sanitize_error_message(exc),
        )

    return compare_case(case, parsed), None


def build_report(
    dataset: EvaluationDataset,
    resultados: tuple[CaseResult, ...],
    *,
    adapter_name: str,
    model_name: str,
) -> EvaluationReport:
    """Agrega las métricas sobre `resultados`, en el orden en que se reciben.

    Público porque la recuperación por lotes necesita construir el informe una
    sola vez sobre los 28 resultados ya reunidos: agregarlos por lote y sumar
    después daría porcentajes de 14 casos que se leerían como si fueran la
    medición.
    """
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
