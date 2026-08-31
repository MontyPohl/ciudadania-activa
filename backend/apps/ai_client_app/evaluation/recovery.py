"""Protocolo prerregistrado de la corrida de recuperación S8-E2-R1.

La corrida canónica S8-E2 quedó `NOT_DEMONSTRATED`: 28 casos intentados, 22
`TransientAiProviderError` y 6 clasificaciones válidas. AI Studio confirmó
después la causa técnica —nivel gratuito, RPM 5 y RPD 20— y con ella que el
protocolo original era **inejecutable por construcción**: 28 solicitudes seguidas
no caben en una cuota diaria de 20, y lanzarlas sin pausa excede 5 por minuto.

Lo que se enmienda es exclusivamente el transporte del experimento. Modelo,
dataset, etiquetas de oro, prompt, timeout, scoring y umbral quedan intactos:
tocarlos convertiría una corrida repetida en un experimento distinto.

Dos decisiones que este módulo hace estructurales en lugar de dejarlas al
cuidado de quien ejecute:

- **Un lote no es una medición.** `BatchEvaluation` no guarda ningún porcentaje.
  No se puede leer un PASS del lote 1 porque el dato no existe hasta combinar.
- **Los lotes no son datasets.** `select_batch` recorta por índice sobre el
  dataset congelado; no hay un segundo archivo que pueda divergir del original.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass

from ..prompts import PROMPT_VERSION
from .dataset import EvaluationCase, EvaluationDataset
from .reporting import report_to_dict
from .runner import CaseDiagnostic, EvaluationReport, build_report, run_evaluation
from .scoring import CA4_MIN_CONCORDANCE_PCT, CaseResult, percentage

PROTOCOL_VERSION = "s8-e2-r1"

#: Los 28 casos congelados del dataset `s8-evaluation-v1`.
TOTAL_CASES = 28

#: Dos lotes iguales. 14 cabe con holgura en la cuota diaria de 20 observada y
#: deja margen para que una ventana parcialmente consumida siga sirviendo.
BATCH_SIZE = 14
BATCH_NUMBERS = (1, 2)

#: Pacing prerregistrado. 20 s entre solicitudes son 3 por minuto, por debajo del
#: RPM 5 observado. **No es el timeout**: el timeout acota cuánto se espera una
#: respuesta; esto acota cada cuánto se pide la siguiente.
INTER_REQUEST_DELAY_SECONDS = 20

#: Igual que en la corrida canónica. Se repite aquí para que la evidencia lo
#: registre, no para redefinirlo.
REQUEST_TIMEOUT_SECONDS = 20

#: Estado obligatorio mientras falte algún lote. No es un resultado.
STATUS_IN_PROGRESS = "R1_IN_PROGRESS"

RESULT_PASS = "PASS"
RESULT_FAIL = "FAIL"
RESULT_NOT_DEMONSTRATED = "NOT_DEMONSTRATED"


class RecoveryProtocolError(Exception):
    """El protocolo R1 se violó. La corrida no puede continuar ni combinarse."""


@dataclass(frozen=True, slots=True)
class BatchEvaluation:
    """Evidencia de un lote. Deliberadamente **sin** métricas agregadas.

    Un porcentaje sobre 14 casos invitaría a leer un resultado a mitad de
    experimento, que es exactamente lo que el prerregistro prohíbe. Las métricas
    aparecen una sola vez, en `CombinedEvaluation`, y sobre los 28.
    """

    protocol_version: str
    status: str
    model_name: str
    adapter_name: str
    dataset_version: str
    dataset_sha256: str
    prompt_version: str
    batch_number: int
    batch_case_ids: tuple[str, ...]
    inter_request_delay_seconds: float
    request_timeout_seconds: int
    results: tuple[CaseResult, ...]
    diagnostics: tuple[CaseDiagnostic, ...]


@dataclass(frozen=True, slots=True)
class CombinedEvaluation:
    """Los dos lotes reunidos: la única forma válida de leer CA-4 en R1."""

    protocol_version: str
    report: EvaluationReport
    diagnostics: tuple[CaseDiagnostic, ...]
    batch_numbers: tuple[int, ...]
    minimum_concordant_cases: int
    run_valid_for_ca4: bool
    ca4_result: str


def minimum_concordant_cases(total: int = TOTAL_CASES) -> int:
    """Casos concordantes mínimos para alcanzar el umbral, con `total` casos.

    Se deriva de `CA4_MIN_CONCORDANCE_PCT` en lugar de escribir 24: si alguien
    cambiara el umbral, un número copiado a mano quedaría mintiendo en silencio.
    Con 28 casos da 24, porque 23/28 = 82.14 % y 24/28 = 85.71 %.
    """
    for concordantes in range(total + 1):
        if percentage(concordantes, total) >= CA4_MIN_CONCORDANCE_PCT:
            return concordantes
    return total + 1


def batch_bounds(batch_number: int) -> tuple[int, int]:
    """Índices `[inicio, fin)` del lote, sobre el orden original del dataset."""
    if batch_number not in BATCH_NUMBERS:
        raise RecoveryProtocolError(f"Lote inexistente en el protocolo R1: {batch_number}.")
    inicio = (batch_number - 1) * BATCH_SIZE
    return inicio, inicio + BATCH_SIZE


def select_batch(dataset: EvaluationDataset, batch_number: int) -> tuple[EvaluationCase, ...]:
    """Recorta el lote del dataset congelado, sin copiarlo ni reordenarlo.

    Si el dataset no tuviera exactamente 28 casos, los límites del protocolo no
    se corresponderían con lo prerregistrado y la selección se rechaza en vez de
    adaptarse: adaptarse sería medir otra cosa con el mismo nombre.
    """
    if len(dataset.cases) != TOTAL_CASES:
        raise RecoveryProtocolError(
            f"El protocolo R1 exige {TOTAL_CASES} casos; el dataset tiene {len(dataset.cases)}."
        )
    inicio, fin = batch_bounds(batch_number)
    return dataset.cases[inicio:fin]


def evaluate_batch(
    dataset: EvaluationDataset,
    adapter,
    *,
    batch_number: int,
    adapter_name: str,
    model_name: str,
    dataset_sha256: str,
    inter_request_delay_seconds: float = INTER_REQUEST_DELAY_SECONDS,
    request_timeout_seconds: int = REQUEST_TIMEOUT_SECONDS,
    sleep: Callable[[float], object] = time.sleep,
    monotonic_ns: Callable[[], int] = time.perf_counter_ns,
) -> BatchEvaluation:
    """Ejecuta un lote con el pacing prerregistrado y devuelve su evidencia.

    No reintenta, no sustituye casos y no decide nada: un fallo se registra y el
    lote continúa, igual que en la corrida canónica.
    """
    casos = select_batch(dataset, batch_number)
    run = run_evaluation(
        dataset,
        adapter,
        adapter_name=adapter_name,
        model_name=model_name,
        cases=casos,
        inter_request_delay_seconds=inter_request_delay_seconds,
        sleep=sleep,
        monotonic_ns=monotonic_ns,
    )
    return BatchEvaluation(
        protocol_version=PROTOCOL_VERSION,
        status=STATUS_IN_PROGRESS,
        model_name=model_name,
        adapter_name=adapter_name,
        dataset_version=dataset.dataset_version,
        dataset_sha256=dataset_sha256,
        prompt_version=PROMPT_VERSION,
        batch_number=batch_number,
        batch_case_ids=tuple(case.id for case in casos),
        inter_request_delay_seconds=inter_request_delay_seconds,
        request_timeout_seconds=request_timeout_seconds,
        results=run.report.cases,
        diagnostics=run.diagnostics,
    )


def combine_batches(
    dataset: EvaluationDataset,
    batches: Iterable[BatchEvaluation],
) -> CombinedEvaluation:
    """Reúne los lotes en la única medición válida de R1.

    Comprueba antes que los lotes describan el mismo experimento y cubran el
    dataset exactamente una vez. Cualquier discrepancia aborta: combinar dos
    mitades que no pertenecen a la misma corrida produciría un número con
    apariencia de medición y sin experimento detrás.
    """
    lotes = tuple(batches)
    _validar_lotes(dataset, lotes)

    por_id = {resultado.id: resultado for lote in lotes for resultado in lote.results}
    diagnostico_por_id = {
        diagnostico.id: diagnostico for lote in lotes for diagnostico in lote.diagnostics
    }

    # El orden vuelve a ser el del dataset, no el de llegada de los lotes: así el
    # informe combinado es indistinguible del de una corrida única de 28 casos.
    resultados = tuple(por_id[case.id] for case in dataset.cases)
    diagnosticos = tuple(diagnostico_por_id[case.id] for case in dataset.cases)

    primero = lotes[0]
    report = build_report(
        dataset,
        resultados,
        adapter_name=primero.adapter_name,
        model_name=primero.model_name,
    )
    minimo = minimum_concordant_cases(TOTAL_CASES)
    valida, resultado = classify_run(report, minimum=minimo)

    return CombinedEvaluation(
        protocol_version=PROTOCOL_VERSION,
        report=report,
        diagnostics=diagnosticos,
        batch_numbers=tuple(lote.batch_number for lote in lotes),
        minimum_concordant_cases=minimo,
        run_valid_for_ca4=valida,
        ca4_result=resultado,
    )


def classify_run(report: EvaluationReport, *, minimum: int | None = None) -> tuple[bool, str]:
    """Aplica la regla de validez congelada. Devuelve `(válida, resultado)`.

    Un solo error basta para invalidar la corrida entera. No se excluyen casos ni
    se recalcula el porcentaje sobre los que sí respondieron: eso convertiría una
    medición fallida en una medición favorable sobre una muestra elegida por el
    propio fallo.
    """
    umbral = minimum_concordant_cases(TOTAL_CASES) if minimum is None else minimum

    if len(report.cases) != TOTAL_CASES:
        return False, RESULT_NOT_DEMONSTRATED
    if report.errors_count > 0:
        return False, RESULT_NOT_DEMONSTRATED
    if report.ca4_concordant_cases >= umbral:
        return True, RESULT_PASS
    return True, RESULT_FAIL


def batch_to_json(batch: BatchEvaluation) -> str:
    """Evidencia del lote, con las mismas garantías de formato que el informe.

    `BatchEvaluation` no contiene `Decimal` —los porcentajes no existen aquí a
    propósito—, así que basta con volcar el dataclass tal cual.
    """
    return _dump(asdict(batch))


def combined_to_json(combined: CombinedEvaluation) -> str:
    """Evidencia combinada: el informe de los 28 más el veredicto de validez."""
    datos = {
        "protocol_version": combined.protocol_version,
        "batch_numbers": list(combined.batch_numbers),
        "minimum_concordant_cases": combined.minimum_concordant_cases,
        "run_valid_for_ca4": combined.run_valid_for_ca4,
        "ca4_result": combined.ca4_result,
        "report": report_to_dict(combined.report),
        "diagnostics": [asdict(d) for d in combined.diagnostics],
    }
    return _dump(datos)


def _dump(datos: dict) -> str:
    return json.dumps(datos, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _validar_lotes(dataset: EvaluationDataset, lotes: tuple[BatchEvaluation, ...]) -> None:
    numeros = tuple(lote.batch_number for lote in lotes)
    if sorted(numeros) != sorted(BATCH_NUMBERS):
        raise RecoveryProtocolError(
            f"R1 exige exactamente los lotes {BATCH_NUMBERS}; se recibieron {numeros}."
        )

    for campo in (
        "protocol_version",
        "model_name",
        "dataset_version",
        "prompt_version",
        "dataset_sha256",
        "request_timeout_seconds",
        "inter_request_delay_seconds",
    ):
        valores = {getattr(lote, campo) for lote in lotes}
        if len(valores) != 1:
            raise RecoveryProtocolError(
                f"Los lotes no comparten `{campo}`: la corrida R1 queda invalidada."
            )

    ids_por_lote = [{resultado.id for resultado in lote.results} for lote in lotes]
    if ids_por_lote[0] & ids_por_lote[1]:
        raise RecoveryProtocolError("Hay casos repetidos entre lotes: no se puede combinar.")

    union = ids_por_lote[0] | ids_por_lote[1]
    esperados = {case.id for case in dataset.cases}
    if union != esperados:
        raise RecoveryProtocolError(
            "Los lotes no cubren el dataset exactamente una vez: la corrida R1 no es válida."
        )
