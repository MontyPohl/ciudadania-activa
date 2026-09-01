"""Protocolos prerregistrados de recuperación de S8-E2: R1 (histórico) y R2.

La corrida canónica S8-E2 quedó `NOT_DEMONSTRATED`: 28 casos intentados, 22
`TransientAiProviderError` y 6 clasificaciones válidas. AI Studio confirmó
después la causa técnica —nivel gratuito, RPM 5 y RPD 20— y con ella que el
protocolo original era **inejecutable por construcción**: 28 solicitudes seguidas
no caben en una cuota diaria de 20, y lanzarlas sin pausa excede 5 por minuto.

**R1** añadió pacing de 20 s y partición en dos lotes, y quedó `ABORTED`. Su lote
1 se ejecutó una vez: 14 intentos, 4 respuestas y 10 fallos por la ruta
transporte/timeout. Su evidencia JSON se perdió de forma irrecuperable —se escribió
dentro de un contenedor efímero—, de modo que no puede combinarse ni auditarse por
caso.

**R2** es una corrida nueva y completa de los 28, no una continuación de R1 ni un
rerun selectivo, y no reutiliza ninguno de sus resultados. Su único cambio técnico
es el timeout: 60 s en lugar de 20, porque la duración máxima observada en R1
—20 270 ms frente a un deadline de 20 000— es compatible con presión del timeout.

Lo que se enmienda en ambas es exclusivamente el transporte del experimento.
Modelo, dataset, etiquetas de oro, prompt, scoring y umbral quedan intactos:
tocarlos convertiría una corrida repetida en un experimento distinto.

Tres decisiones que este módulo hace estructurales en lugar de dejarlas al
cuidado de quien ejecute:

- **Un lote no es una medición.** `BatchEvaluation` no guarda ningún porcentaje.
  No se puede leer un PASS del lote 1 porque el dato no existe hasta combinar.
- **Los lotes no son datasets.** `select_batch` recorta por índice sobre el
  dataset congelado; no hay un segundo archivo que pueda divergir del original.
- **Un protocolo no se autovalida.** `RecoveryProtocol` lleva los valores
  absolutos y se pasa explícitamente; deducirlo de lo que el lote declara sería
  volver al agujero que el endurecimiento de R1 cerró.
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

#: Los 28 casos congelados del dataset `s8-evaluation-v1`. Común a todas las
#: recuperaciones: cambiarlo sería medir otra cosa.
TOTAL_CASES = 28

#: Dos lotes iguales. 14 cabe con holgura en la cuota diaria de 20 observada y
#: deja margen para que una ventana parcialmente consumida siga sirviendo.
BATCH_SIZE = 14
BATCH_NUMBERS = (1, 2)

RESULT_PASS = "PASS"
RESULT_FAIL = "FAIL"
RESULT_NOT_DEMONSTRATED = "NOT_DEMONSTRATED"


class RecoveryProtocolError(Exception):
    """El protocolo se violó. La corrida no puede continuar ni combinarse."""


@dataclass(frozen=True, slots=True)
class RecoveryProtocol:
    """Los valores absolutos que fija un prerregistro de recuperación.

    Existe para que R2 no obligue a duplicar el módulo ni a reinterpretar R1. Cada
    recuperación es una instancia congelada, y la validación compara contra la que
    se le pasa explícitamente: nunca contra la que el propio lote declara, que era
    justamente el agujero que el endurecimiento de R1 cerró.

    `status_in_progress` lleva el número de la recuperación a propósito. Un lote de
    R1 no puede colarse en una combinación de R2 ni al revés, porque su estado no
    coincide.
    """

    version: str
    model_name: str
    dataset_version: str
    dataset_sha256: str
    prompt_version: str
    prompt_sha256: str
    request_timeout_seconds: int
    inter_request_delay_seconds: float
    status_in_progress: str

    def expected_fields(self) -> tuple[tuple[str, object], ...]:
        """Campos de `BatchEvaluation` cuyo valor fija este prerregistro.

        `adapter_name` queda fuera a propósito: el prerregistro no fija qué
        adaptador se usa —el falso sirve para ensayar la maquinaria—, solo exige
        que los dos lotes usen el mismo.
        """
        return (
            ("protocol_version", self.version),
            ("status", self.status_in_progress),
            ("model_name", self.model_name),
            ("dataset_version", self.dataset_version),
            ("dataset_sha256", self.dataset_sha256),
            ("prompt_version", self.prompt_version),
            ("prompt_sha256", self.prompt_sha256),
            ("request_timeout_seconds", self.request_timeout_seconds),
            ("inter_request_delay_seconds", self.inter_request_delay_seconds),
        )


#: `PROMPT_VERSION` no se copia como literal: su fuente canónica es
#: `apps.ai_client_app.prompts`, y duplicarlo crearía dos verdades.
_DATASET_VERSION = "s8-evaluation-v1"
_DATASET_SHA256 = "3bc54d591dc104aeabeecb2f5cc7c7a38e70ba042e7f045321ab33ae9b5efad4"
_MODEL_NAME = "gemini-3.6-flash"

#: Hash del contenido de `classification_v1.txt`. El prompt ya estaba congelado por
#: `prompt_version`, pero una versión es una etiqueta: no impide que el archivo
#: cambie debajo. Este hash es lo que la ejecución canónica comprueba contra el
#: texto que de verdad se envía.
_PROMPT_SHA256 = "f3c803984806a3b934db0417f6cc3ce59a49cdde1e30b4c86b862c2e11c665ca"

#: Pacing prerregistrado, idéntico en R1 y R2. 20 s entre solicitudes son 3 por
#: minuto, por debajo del RPM 5 observado. **No es el timeout**: el timeout acota
#: cuánto se espera una respuesta; esto acota cada cuánto se pide la siguiente.
_PACING_SECONDS = 20

#: R1: protocolo **histórico**. Quedó `ABORTED`. Su lote 1 se ejecutó una vez —14
#: intentos, 10 fallos de transporte/timeout— y su evidencia JSON se perdió de
#: forma irrecuperable, así que no puede combinarse ni auditarse por caso. Se
#: conserva intacto para que el registro de lo que se prerregistró siga legible.
R1 = RecoveryProtocol(
    version="s8-e2-r1",
    model_name=_MODEL_NAME,
    dataset_version=_DATASET_VERSION,
    dataset_sha256=_DATASET_SHA256,
    prompt_version=PROMPT_VERSION,
    prompt_sha256=_PROMPT_SHA256,
    request_timeout_seconds=20,
    inter_request_delay_seconds=_PACING_SECONDS,
    status_in_progress="R1_IN_PROGRESS",
)

#: R2: corrida **nueva y completa** de los 28 casos. No es continuación de R1, no
#: reutiliza ninguno de sus resultados y no es un rerun selectivo.
#:
#: Único cambio técnico frente a R1: el timeout pasa de 20 s a 60 s. R1 devolvió 10
#: de 14 casos por la ruta transporte/timeout, con una duración máxima de 20 270 ms
#: frente a un deadline de 20 000, de modo que 20 s quedó demostrado como
#: operacionalmente inadecuado para completar la corrida. El mensaje del adaptador
#: no separa timeout de otros fallos de transporte, así que **no** se afirma que
#: los 10 fueran deadlines agotados ni que el modelo necesite siempre más de 20 s:
#: el deadline es el único parámetro que el patrón señala. Los 60 s se fijan aquí,
#: antes de R2, y no podrán ajustarse después de ver resultados.
R2 = RecoveryProtocol(
    version="s8-e2-r2",
    model_name=_MODEL_NAME,
    dataset_version=_DATASET_VERSION,
    dataset_sha256=_DATASET_SHA256,
    prompt_version=PROMPT_VERSION,
    prompt_sha256=_PROMPT_SHA256,
    request_timeout_seconds=60,
    inter_request_delay_seconds=_PACING_SECONDS,
    status_in_progress="R2_IN_PROGRESS",
)

PROTOCOLS = {R1.version: R1, R2.version: R2}

# Alias históricos de R1. Se conservan para que ningún uso previo cambie de
# significado al aparecer R2; el prerregistro vigente para ejecutar es `R2`.
PROTOCOL_VERSION = R1.version
MODEL_NAME = R1.model_name
DATASET_VERSION = R1.dataset_version
DATASET_SHA256 = R1.dataset_sha256
INTER_REQUEST_DELAY_SECONDS = R1.inter_request_delay_seconds
REQUEST_TIMEOUT_SECONDS = R1.request_timeout_seconds
STATUS_IN_PROGRESS = R1.status_in_progress


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
    prompt_sha256: str
    batch_number: int
    batch_case_ids: tuple[str, ...]
    inter_request_delay_seconds: float
    request_timeout_seconds: int
    results: tuple[CaseResult, ...]
    diagnostics: tuple[CaseDiagnostic, ...]


@dataclass(frozen=True, slots=True)
class CombinedEvaluation:
    """Los dos lotes reunidos: la única forma válida de leer CA-4 en una recuperación."""

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
    protocol: RecoveryProtocol = R1,
    inter_request_delay_seconds: float | None = None,
    request_timeout_seconds: int | None = None,
    sleep: Callable[[float], object] = time.sleep,
    monotonic_ns: Callable[[], int] = time.perf_counter_ns,
) -> BatchEvaluation:
    """Ejecuta un lote con el pacing prerregistrado y devuelve su evidencia.

    No reintenta, no sustituye casos y no decide nada: un fallo se registra y el
    lote continúa, igual que en la corrida canónica.

    `protocol` decide contra qué prerregistro se valida y por defecto es `R1`, de
    modo que ningún uso previo cambia de significado al aparecer `R2`. El pacing y
    el timeout se toman del protocolo si no se pasan, y si se pasan tienen que
    coincidir con él: el parámetro sirve para que la evidencia registre lo que se
    usó, no para poder desviarse.

    Los parámetros se validan **antes** de la primera llamada. `combine_batches`
    volvería a rechazarlos, pero lo haría después de haber gastado 14 unidades de
    una cuota diaria de 20 —el recurso escaso que obligó a partir la corrida—, y
    esa ventana ya no se recupera.
    """
    if inter_request_delay_seconds is None:
        inter_request_delay_seconds = protocol.inter_request_delay_seconds
    if request_timeout_seconds is None:
        request_timeout_seconds = protocol.request_timeout_seconds

    _exigir("model_name", model_name, protocol.model_name)
    _exigir("dataset_version", dataset.dataset_version, protocol.dataset_version)
    _exigir("dataset_sha256", dataset_sha256, protocol.dataset_sha256)
    _exigir(
        "request_timeout_seconds",
        request_timeout_seconds,
        protocol.request_timeout_seconds,
    )
    _exigir(
        "inter_request_delay_seconds",
        inter_request_delay_seconds,
        protocol.inter_request_delay_seconds,
    )

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
        protocol_version=protocol.version,
        status=protocol.status_in_progress,
        model_name=model_name,
        adapter_name=adapter_name,
        dataset_version=dataset.dataset_version,
        dataset_sha256=dataset_sha256,
        prompt_version=protocol.prompt_version,
        prompt_sha256=protocol.prompt_sha256,
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
    *,
    protocol: RecoveryProtocol = R1,
) -> CombinedEvaluation:
    """Reúne los lotes en la única medición válida de la recuperación.

    Comprueba antes que los lotes sean **el** experimento que `protocol` describe y
    que cubran el dataset exactamente una vez. Cualquier discrepancia aborta:
    combinar dos mitades que no pertenecen a la misma corrida produciría un número
    con apariencia de medición y sin experimento detrás.

    `protocol` es explícito y por defecto `R1`. Deducirlo de lo que declaran los
    lotes los validaría contra sí mismos, que es exactamente el agujero que el
    endurecimiento de R1 cerró; olvidarlo, en cambio, falla ruidosamente.
    """
    lotes = tuple(batches)
    _validar_lotes(dataset, lotes, protocol)

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
        protocol_version=protocol.version,
        report=report,
        diagnostics=diagnosticos,
        # Ordenados, no en orden de llegada: la evidencia combinada debe ser
        # idéntica se entreguen los lotes como se entreguen.
        batch_numbers=tuple(sorted(lote.batch_number for lote in lotes)),
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


def _exigir(campo: str, obtenido: object, esperado: object) -> None:
    """Impone un valor absoluto del prerregistro, no un acuerdo entre lotes."""
    if obtenido != esperado:
        raise RecoveryProtocolError(
            f"R1 prerregistró `{campo}` = {esperado!r}; se recibió {obtenido!r}."
        )


def expected_case_ids(dataset: EvaluationDataset, batch_number: int) -> tuple[str, ...]:
    """IDs que le corresponden al lote, en el orden exacto del dataset."""
    return tuple(case.id for case in select_batch(dataset, batch_number))


def _validar_lotes(
    dataset: EvaluationDataset,
    lotes: tuple[BatchEvaluation, ...],
    protocol: RecoveryProtocol,
) -> None:
    """Comprueba que los lotes sean **el** experimento, no un experimento coherente.

    Cada campo se compara contra el valor absoluto del prerregistro. La versión
    anterior solo exigía que los dos lotes coincidieran entre sí, y eso admitía una
    corrida completa hecha con otro modelo, sin pacing o contra otro dataset: basta
    con equivocarse igual dos veces.

    La partición se valida por `batch_number` contra los IDs esperados en orden
    exacto, así que da igual en qué orden lleguen los lotes al combinador. Esa
    igualdad de tuplas subsume por sí sola la ausencia de duplicados, de faltantes
    y de reordenaciones, y también descarta cualquier partición arbitraria cuya
    unión sumase 28 por casualidad: no hay que comprobarlas por separado.
    """
    numeros = tuple(lote.batch_number for lote in lotes)
    if sorted(numeros) != sorted(BATCH_NUMBERS):
        raise RecoveryProtocolError(
            f"R1 exige exactamente los lotes {BATCH_NUMBERS}; se recibieron {numeros}."
        )

    for lote in lotes:
        for campo, esperado in protocol.expected_fields():
            _exigir(f"lote {lote.batch_number}.{campo}", getattr(lote, campo), esperado)

    adaptadores = {lote.adapter_name for lote in lotes}
    if len(adaptadores) != 1:
        raise RecoveryProtocolError(
            f"Los lotes usan adaptadores distintos {sorted(adaptadores)}: no se pueden combinar."
        )

    for lote in lotes:
        esperados = expected_case_ids(dataset, lote.batch_number)
        if len(esperados) != BATCH_SIZE:
            raise RecoveryProtocolError(
                f"El lote {lote.batch_number} debería abarcar {BATCH_SIZE} casos."
            )
        _exigir(f"lote {lote.batch_number}.batch_case_ids", lote.batch_case_ids, esperados)
        _exigir(
            f"lote {lote.batch_number}.results",
            tuple(resultado.id for resultado in lote.results),
            esperados,
        )
        _exigir(
            f"lote {lote.batch_number}.diagnostics",
            tuple(diagnostico.id for diagnostico in lote.diagnostics),
            esperados,
        )
