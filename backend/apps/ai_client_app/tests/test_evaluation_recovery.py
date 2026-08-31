"""Instrumentación y protocolo de la recuperación S8-E2-R1.

Ninguna prueba de este módulo toca la red ni el proveedor: los adaptadores son
dobles locales y el reloj y el `sleep` entran por parámetro, de modo que probar
un pacing de 20 segundos no cuesta 20 segundos.
"""

import json
from dataclasses import replace

import pytest

from apps.ai_client_app.evaluation import recovery as recovery_module
from apps.ai_client_app.evaluation import runner as runner_module
from apps.ai_client_app.evaluation.dataset import load_dataset
from apps.ai_client_app.evaluation.recovery import (
    BATCH_NUMBERS,
    BATCH_SIZE,
    DATASET_SHA256,
    DATASET_VERSION,
    INTER_REQUEST_DELAY_SECONDS,
    MODEL_NAME,
    PROTOCOL_VERSION,
    REQUEST_TIMEOUT_SECONDS,
    STATUS_IN_PROGRESS,
    TOTAL_CASES,
    RecoveryProtocolError,
    batch_to_json,
    combine_batches,
    combined_to_json,
    evaluate_batch,
    expected_case_ids,
    minimum_concordant_cases,
    select_batch,
)
from apps.ai_client_app.evaluation.reporting import report_to_json
from apps.ai_client_app.evaluation.runner import evaluate_dataset, run_evaluation
from apps.ai_client_app.evaluation.sanitization import (
    MAX_MESSAGE_LENGTH,
    REDACTED,
    sanitize_error_message,
    sanitize_text,
)

from ._ast_checks import code_identifiers

# El SHA no se copia aquí: su fuente canónica es `recovery.DATASET_SHA256`, y un
# literal duplicado en la prueba dejaría de detectar que el módulo lo cambió.

# Con la forma de una clave de Google pero inventada: la suite nunca ve una real.
CLAVE_FALSA = "AIzaSyD9fakeKEYfakeKEYfakeKEY0123456789x"


@pytest.fixture(scope="module")
def dataset():
    return load_dataset()


class AdapterCorrecto:
    """Devuelve siempre la etiqueta de oro del caso que le toca, en orden."""

    def __init__(self, dataset, *, casos=None):
        self._casos = list(casos if casos is not None else dataset.cases)
        self.llamadas = 0

    def classify(self, request) -> str:
        case = self._casos[self.llamadas]
        self.llamadas += 1
        return json.dumps(
            {
                "category_slug": case.expected.category_slug,
                "institution_slug": case.expected.institution_slug,
                "priority": case.expected.priority,
                "confidence": 0.9,
                "inconsistency": case.expected.inconsistency,
                "justification": "Justificación sintética del benchmark.",
            }
        )


class AdapterQueFalla:
    """Lanza la excepción indicada en todas las llamadas."""

    def __init__(self, error):
        self._error = error
        self.llamadas = 0

    def classify(self, request) -> str:
        self.llamadas += 1
        raise self._error


class RelojFalso:
    """Reloj monotónico controlado: cada lectura avanza `paso_ns`."""

    def __init__(self, paso_ns=5_000_000):
        self._ahora = 0
        self._paso = paso_ns

    def __call__(self) -> int:
        valor = self._ahora
        self._ahora += self._paso
        return valor


def _sleep_espia():
    registradas = []

    def sleep(segundos):
        registradas.append(segundos)

    return sleep, registradas


# --------------------------------------------------------------------------- #
# 1-3. Duración y tipo de error se registran siempre
# --------------------------------------------------------------------------- #


def test_la_duracion_se_registra_en_los_casos_correctos(dataset):
    run = run_evaluation(
        dataset,
        AdapterCorrecto(dataset),
        adapter_name="doble",
        model_name="modelo-de-prueba",
        monotonic_ns=RelojFalso(),
    )

    assert len(run.diagnostics) == len(dataset.cases)
    for diagnostico in run.diagnostics:
        assert diagnostico.duration_ms >= 0
        assert isinstance(diagnostico.duration_ms, int)
        assert diagnostico.error_type is None
        assert diagnostico.error_message is None


def test_la_duracion_se_registra_tambien_cuando_el_caso_falla(dataset):
    run = run_evaluation(
        dataset,
        AdapterQueFalla(RuntimeError("El proveedor de IA respondió 429.")),
        adapter_name="doble",
        model_name="modelo-de-prueba",
        monotonic_ns=RelojFalso(),
    )

    for diagnostico in run.diagnostics:
        assert diagnostico.duration_ms >= 0


def test_el_tipo_de_error_se_conserva(dataset):
    run = run_evaluation(
        dataset,
        AdapterQueFalla(TimeoutError("El proveedor de IA no respondió a tiempo.")),
        adapter_name="doble",
        model_name="modelo-de-prueba",
        monotonic_ns=RelojFalso(),
    )

    assert {d.error_type for d in run.diagnostics} == {"TimeoutError"}
    assert {r.error_type for r in run.report.cases} == {"TimeoutError"}


# --------------------------------------------------------------------------- #
# 4-6. Sanitización: conserva el diagnóstico, elimina el secreto
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "mensaje",
    [
        "El proveedor de IA respondió 429.",
        "El proveedor de IA no respondió a tiempo.",
        "El proveedor de IA rechazó la petición (404).",
    ],
)
def test_la_sanitizacion_conserva_el_mensaje_diagnostico(mensaje):
    assert sanitize_text(mensaje) == mensaje


@pytest.mark.parametrize(
    "peligroso",
    [
        f"api_key={CLAVE_FALSA}",
        f"Authorization: Bearer {CLAVE_FALSA}",
        f"x-goog-api-key: {CLAVE_FALSA}",
        "password=hunter2secretisimo",
        "postgis://usuario:clave@db:5432/ciudadania_activa",
        "redis://:claveredis@redis:6379/0",
        "https://usuario:clave@generativelanguage.googleapis.com/v1/models",
        f"https://generativelanguage.googleapis.com/v1/models?key={CLAVE_FALSA}",
        f"token {CLAVE_FALSA}",
    ],
)
def test_la_sanitizacion_elimina_datos_sensibles(peligroso):
    limpio = sanitize_text(peligroso)

    assert CLAVE_FALSA not in limpio
    assert "clave@" not in limpio
    assert "hunter2secretisimo" not in limpio
    assert "claveredis" not in limpio
    assert REDACTED in limpio


def test_la_clave_no_aparece_en_la_evidencia_del_lote(dataset):
    adapter = AdapterQueFalla(RuntimeError(f"fallo con api_key={CLAVE_FALSA} incrustada"))
    sleep, _ = _sleep_espia()

    lote = evaluate_batch(
        dataset,
        adapter,
        batch_number=1,
        adapter_name="doble",
        model_name="gemini-3.6-flash",
        dataset_sha256=DATASET_SHA256,
        sleep=sleep,
        monotonic_ns=RelojFalso(),
    )
    serializado = batch_to_json(lote)

    assert CLAVE_FALSA not in serializado
    assert REDACTED in serializado
    # El dato diagnóstico sobrevive a la redacción.
    assert "fallo con" in serializado


def test_la_sanitizacion_borra_tambien_un_bearer_corto():
    """Un token corto no lo salva la red de seguridad de los tokens opacos.

    `Authorization: Bearer abc123` es el caso que obliga a redactar el `Bearer`
    antes que el par clave/valor: si se hiciera al revés, `Authorization` se
    quedaría con la palabra `Bearer` como valor y el token seguiría a la vista.
    """
    limpio = sanitize_text("Authorization: Bearer abc123")

    assert "abc123" not in limpio


def test_la_sanitizacion_acota_la_longitud():
    limpio = sanitize_text("x" * (MAX_MESSAGE_LENGTH * 3))

    assert len(limpio) <= MAX_MESSAGE_LENGTH + 1


def test_sanitize_error_message_trabaja_sobre_la_excepcion():
    mensaje = sanitize_error_message(RuntimeError(f"api_key={CLAVE_FALSA}"))

    assert CLAVE_FALSA not in mensaje


# --------------------------------------------------------------------------- #
# 7-11. Pacing
# --------------------------------------------------------------------------- #


def test_el_pacing_duerme_solo_entre_casos(dataset):
    sleep, esperas = _sleep_espia()

    evaluate_batch(
        dataset,
        AdapterCorrecto(dataset, casos=select_batch(dataset, 1)),
        batch_number=1,
        adapter_name="doble",
        model_name="gemini-3.6-flash",
        dataset_sha256=DATASET_SHA256,
        sleep=sleep,
        monotonic_ns=RelojFalso(),
    )

    assert esperas == [INTER_REQUEST_DELAY_SECONDS] * (BATCH_SIZE - 1)


def test_catorce_casos_producen_trece_esperas(dataset):
    sleep, esperas = _sleep_espia()
    casos = select_batch(dataset, 1)

    run_evaluation(
        dataset,
        AdapterCorrecto(dataset, casos=casos),
        adapter_name="doble",
        model_name="modelo-de-prueba",
        cases=casos,
        inter_request_delay_seconds=20,
        sleep=sleep,
        monotonic_ns=RelojFalso(),
    )

    assert len(casos) == 14
    assert len(esperas) == 13


def test_el_primer_caso_no_espera_antes_ni_el_ultimo_despues(dataset):
    """Un solo caso no debe dormir nunca: ni antes de pedir ni después de recibir."""
    sleep, esperas = _sleep_espia()
    casos = dataset.cases[:1]

    run_evaluation(
        dataset,
        AdapterCorrecto(dataset, casos=casos),
        adapter_name="doble",
        model_name="modelo-de-prueba",
        cases=casos,
        inter_request_delay_seconds=20,
        sleep=sleep,
        monotonic_ns=RelojFalso(),
    )

    assert esperas == []


def test_el_retardo_por_defecto_es_cero_y_no_llama_a_sleep(dataset):
    sleep, esperas = _sleep_espia()

    run_evaluation(
        dataset,
        AdapterCorrecto(dataset),
        adapter_name="doble",
        model_name="modelo-de-prueba",
        sleep=sleep,
        monotonic_ns=RelojFalso(),
    )

    assert esperas == []


def test_el_pacing_no_es_el_timeout():
    """Son dos magnitudes distintas y el protocolo las registra por separado."""
    assert INTER_REQUEST_DELAY_SECONDS == 20
    assert REQUEST_TIMEOUT_SECONDS == 20
    firma = runner_module.run_evaluation.__doc__ or ""
    assert "timeout" in firma.lower()


# --------------------------------------------------------------------------- #
# 12-15. Lotes
# --------------------------------------------------------------------------- #


def test_el_lote_1_selecciona_exactamente_los_casos_1_a_14(dataset):
    casos = select_batch(dataset, 1)

    assert len(casos) == 14
    assert [c.id for c in casos] == [c.id for c in dataset.cases[:14]]


def test_el_lote_2_selecciona_exactamente_los_casos_15_a_28(dataset):
    casos = select_batch(dataset, 2)

    assert len(casos) == 14
    assert [c.id for c in casos] == [c.id for c in dataset.cases[14:]]


def test_los_lotes_no_se_solapan_y_su_union_es_el_dataset(dataset):
    primero = {c.id for c in select_batch(dataset, 1)}
    segundo = {c.id for c in select_batch(dataset, 2)}

    assert primero & segundo == set()
    assert primero | segundo == {c.id for c in dataset.cases}
    assert len(primero | segundo) == TOTAL_CASES


def test_un_lote_inexistente_se_rechaza(dataset):
    with pytest.raises(RecoveryProtocolError):
        select_batch(dataset, 3)


def test_el_lote_no_publica_ningun_porcentaje(dataset):
    """Leer un PASS a mitad de corrida debe ser imposible, no solo estar prohibido."""
    sleep, _ = _sleep_espia()
    lote = evaluate_batch(
        dataset,
        AdapterCorrecto(dataset, casos=select_batch(dataset, 1)),
        batch_number=1,
        adapter_name="doble",
        model_name="gemini-3.6-flash",
        dataset_sha256=DATASET_SHA256,
        sleep=sleep,
        monotonic_ns=RelojFalso(),
    )
    serializado = batch_to_json(lote)

    assert lote.status == STATUS_IN_PROGRESS
    assert "concordance_pct" not in serializado
    assert "ca4_concordant_cases" not in serializado
    assert lote.batch_case_ids == tuple(c.id for c in select_batch(dataset, 1))
    assert lote.protocol_version == PROTOCOL_VERSION
    assert lote.dataset_sha256 == DATASET_SHA256


# --------------------------------------------------------------------------- #
# 16-17. El batching no altera el scoring
# --------------------------------------------------------------------------- #


def _dos_lotes(dataset, sleep=None):
    sleep = sleep or _sleep_espia()[0]
    return [
        evaluate_batch(
            dataset,
            AdapterCorrecto(dataset, casos=select_batch(dataset, numero)),
            batch_number=numero,
            adapter_name="doble",
            model_name="gemini-3.6-flash",
            dataset_sha256=DATASET_SHA256,
            sleep=sleep,
            monotonic_ns=RelojFalso(),
        )
        for numero in BATCH_NUMBERS
    ]


def test_dos_lotes_producen_el_mismo_scoring_que_una_corrida_completa(dataset):
    combinado = combine_batches(dataset, _dos_lotes(dataset))
    completo = evaluate_dataset(
        dataset,
        AdapterCorrecto(dataset),
        adapter_name="doble",
        model_name="gemini-3.6-flash",
    )

    assert report_to_json(combinado.report) == report_to_json(completo)


def test_el_informe_combinado_conserva_el_orden_del_dataset(dataset):
    combinado = combine_batches(dataset, reversed(_dos_lotes(dataset)))

    assert [r.id for r in combinado.report.cases] == [c.id for c in dataset.cases]
    assert [d.id for d in combinado.diagnostics] == [c.id for c in dataset.cases]


def test_la_corrida_combinada_perfecta_es_valida_y_pasa(dataset):
    combinado = combine_batches(dataset, _dos_lotes(dataset))

    assert combinado.report.total_cases == TOTAL_CASES
    assert combinado.report.errors_count == 0
    assert combinado.run_valid_for_ca4 is True
    assert combinado.ca4_result == "PASS"
    assert combinado.minimum_concordant_cases == 24


def test_el_minimo_se_deriva_del_umbral_y_no_de_un_numero_a_mano():
    assert minimum_concordant_cases(28) == 24


# --------------------------------------------------------------------------- #
# 18-19. Validez y ausencia de reruns
# --------------------------------------------------------------------------- #


def test_un_error_en_cualquier_lote_invalida_la_corrida_combinada(dataset):
    sleep, _ = _sleep_espia()
    bueno = evaluate_batch(
        dataset,
        AdapterCorrecto(dataset, casos=select_batch(dataset, 1)),
        batch_number=1,
        adapter_name="doble",
        model_name="gemini-3.6-flash",
        dataset_sha256=DATASET_SHA256,
        sleep=sleep,
        monotonic_ns=RelojFalso(),
    )
    malo = evaluate_batch(
        dataset,
        AdapterQueFalla(RuntimeError("El proveedor de IA respondió 429.")),
        batch_number=2,
        adapter_name="doble",
        model_name="gemini-3.6-flash",
        dataset_sha256=DATASET_SHA256,
        sleep=sleep,
        monotonic_ns=RelojFalso(),
    )

    combinado = combine_batches(dataset, [bueno, malo])

    assert combinado.report.errors_count == 14
    assert combinado.run_valid_for_ca4 is False
    assert combinado.ca4_result == "NOT_DEMONSTRATED"


def test_combinar_lotes_de_experimentos_distintos_se_rechaza(dataset):
    primero, segundo = _dos_lotes(dataset)
    # `replace` y no `__dict__`: los dataclasses del protocolo usan `slots`.
    impostor = replace(segundo, model_name="otro-modelo")

    with pytest.raises(RecoveryProtocolError):
        combine_batches(dataset, [primero, impostor])


def test_combinar_el_mismo_lote_dos_veces_se_rechaza(dataset):
    primero, _ = _dos_lotes(dataset)

    with pytest.raises(RecoveryProtocolError):
        combine_batches(dataset, [primero, primero])


# --------------------------------------------------------------------------- #
# Hardening: coincidir entre lotes no basta; hay que coincidir con el protocolo
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("model_name", "gemini-2.5-flash"),
        ("protocol_version", "s8-e2-r2"),
        ("prompt_version", "s8-classification-v2"),
        ("dataset_version", "s8-evaluation-v2"),
        ("dataset_sha256", "0" * 64),
        ("request_timeout_seconds", 60),
        ("inter_request_delay_seconds", 0),
        ("status", "COMPLETED"),
    ],
)
def test_los_dos_lotes_equivocados_igual_no_pasan(dataset, campo, valor):
    """Equivocarse igual dos veces no convierte el error en protocolo.

    Es el agujero exacto que este hardening cierra: antes bastaba con que los dos
    lotes coincidieran entre sí, así que una corrida completa con otro modelo, sin
    pacing o contra otro dataset se combinaba sin protestar.
    """
    lotes = [replace(lote, **{campo: valor}) for lote in _dos_lotes(dataset)]

    with pytest.raises(RecoveryProtocolError):
        combine_batches(dataset, lotes)


def test_se_rechazan_adaptadores_distintos(dataset):
    primero, segundo = _dos_lotes(dataset)

    with pytest.raises(RecoveryProtocolError):
        combine_batches(dataset, [primero, replace(segundo, adapter_name="otro-adaptador")])


def test_se_rechazan_los_batch_case_ids_alterados(dataset):
    primero, segundo = _dos_lotes(dataset)
    revueltos = tuple(reversed(primero.batch_case_ids))

    with pytest.raises(RecoveryProtocolError):
        combine_batches(dataset, [replace(primero, batch_case_ids=revueltos), segundo])


def test_se_rechazan_los_resultados_intercambiados_entre_lotes(dataset):
    """Cada `batch_number` tiene una partición fija: no se pueden permutar."""
    primero, segundo = _dos_lotes(dataset)
    cruzados = [
        replace(primero, results=segundo.results, diagnostics=segundo.diagnostics),
        replace(segundo, results=primero.results, diagnostics=primero.diagnostics),
    ]

    with pytest.raises(RecoveryProtocolError):
        combine_batches(dataset, cruzados)


def test_se_rechaza_una_particion_arbitraria_que_cubra_los_28(dataset):
    """7+21, 14+14 desplazado, cualquier reparto: la unión no basta."""
    primero, segundo = _dos_lotes(dataset)
    todos_resultados = primero.results + segundo.results
    todos_diagnosticos = primero.diagnostics + segundo.diagnostics
    corte = 7

    arbitrarios = [
        replace(
            primero,
            batch_case_ids=tuple(r.id for r in todos_resultados[:corte]),
            results=todos_resultados[:corte],
            diagnostics=todos_diagnosticos[:corte],
        ),
        replace(
            segundo,
            batch_case_ids=tuple(r.id for r in todos_resultados[corte:]),
            results=todos_resultados[corte:],
            diagnostics=todos_diagnosticos[corte:],
        ),
    ]

    assert {r.id for r in todos_resultados} == {c.id for c in dataset.cases}
    with pytest.raises(RecoveryProtocolError):
        combine_batches(dataset, arbitrarios)


def test_se_rechaza_un_resultado_duplicado(dataset):
    primero, segundo = _dos_lotes(dataset)
    duplicado = primero.results[:-1] + (primero.results[0],)

    with pytest.raises(RecoveryProtocolError):
        combine_batches(dataset, [replace(primero, results=duplicado), segundo])


def test_se_rechaza_un_resultado_faltante(dataset):
    primero, segundo = _dos_lotes(dataset)

    with pytest.raises(RecoveryProtocolError):
        combine_batches(dataset, [replace(primero, results=primero.results[:-1]), segundo])


def test_se_rechaza_un_diagnostico_faltante(dataset):
    primero, segundo = _dos_lotes(dataset)

    with pytest.raises(RecoveryProtocolError):
        combine_batches(dataset, [replace(primero, diagnostics=primero.diagnostics[:-1]), segundo])


def test_se_rechaza_un_diagnostico_con_id_equivocado(dataset):
    primero, segundo = _dos_lotes(dataset)
    torcido = (replace(primero.diagnostics[0], id="S8-EVAL-999"),) + primero.diagnostics[1:]

    with pytest.raises(RecoveryProtocolError):
        combine_batches(dataset, [replace(primero, diagnostics=torcido), segundo])


def test_el_orden_de_entrega_al_combinador_sigue_siendo_indiferente(dataset):
    """El hardening fija el contenido de cada lote, no el orden en que llegan."""
    primero, segundo = _dos_lotes(dataset)

    directo = combine_batches(dataset, [primero, segundo])
    invertido = combine_batches(dataset, [segundo, primero])

    assert combined_to_json(directo) == combined_to_json(invertido)


def test_los_ids_esperados_son_la_particion_prerregistrada(dataset):
    assert expected_case_ids(dataset, 1) == tuple(c.id for c in dataset.cases[:14])
    assert expected_case_ids(dataset, 2) == tuple(c.id for c in dataset.cases[14:])


# --------------------------------------------------------------------------- #
# Hardening: el lote se rechaza ANTES de gastar cuota
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("kwargs", "descripcion"),
    [
        ({"model_name": "gemini-2.5-flash"}, "modelo no prerregistrado"),
        ({"dataset_sha256": "0" * 64}, "dataset distinto del congelado"),
        ({"request_timeout_seconds": 60}, "timeout alterado"),
        ({"inter_request_delay_seconds": 0}, "sin pacing"),
    ],
)
def test_un_lote_mal_parametrizado_no_llega_a_llamar_al_adaptador(dataset, kwargs, descripcion):
    """Rechazar después de las 14 llamadas gastaría una ventana de cuota irrecuperable."""
    adapter = AdapterCorrecto(dataset, casos=select_batch(dataset, 1))
    base = {
        "batch_number": 1,
        "adapter_name": "doble",
        "model_name": MODEL_NAME,
        "dataset_sha256": DATASET_SHA256,
        "sleep": _sleep_espia()[0],
        "monotonic_ns": RelojFalso(),
    }

    with pytest.raises(RecoveryProtocolError):
        evaluate_batch(dataset, adapter, **{**base, **kwargs})

    assert adapter.llamadas == 0, descripcion


def test_las_constantes_del_protocolo_son_las_prerregistradas():
    assert PROTOCOL_VERSION == "s8-e2-r1"
    assert MODEL_NAME == "gemini-3.6-flash"
    assert DATASET_VERSION == "s8-evaluation-v1"
    assert DATASET_SHA256 == ("3bc54d591dc104aeabeecb2f5cc7c7a38e70ba042e7f045321ab33ae9b5efad4")
    assert REQUEST_TIMEOUT_SECONDS == 20
    assert INTER_REQUEST_DELAY_SECONDS == 20
    assert STATUS_IN_PROGRESS == "R1_IN_PROGRESS"
    assert TOTAL_CASES == 28
    assert BATCH_SIZE == 14
    assert BATCH_NUMBERS == (1, 2)


def test_cada_caso_se_pide_exactamente_una_vez(dataset):
    """Sin reruns: 14 casos son 14 llamadas, fallen o no."""
    sleep, _ = _sleep_espia()
    adapter = AdapterQueFalla(RuntimeError("El proveedor de IA respondió 429."))

    evaluate_batch(
        dataset,
        adapter,
        batch_number=1,
        adapter_name="doble",
        model_name="gemini-3.6-flash",
        dataset_sha256=DATASET_SHA256,
        sleep=sleep,
        monotonic_ns=RelojFalso(),
    )

    assert adapter.llamadas == BATCH_SIZE


def test_el_protocolo_no_contiene_ningun_mecanismo_de_reintento():
    identificadores = code_identifiers(recovery_module) | code_identifiers(runner_module)

    assert "retry" not in identificadores
    assert "reintentar" not in identificadores
    assert "backoff" not in identificadores


# --------------------------------------------------------------------------- #
# 20. Nada de red, nada de proveedor
# --------------------------------------------------------------------------- #


def test_la_recuperacion_no_importa_red_ni_proveedor():
    for modulo in (recovery_module, runner_module):
        identificadores = code_identifiers(modulo)
        assert "requests" not in identificadores
        assert "httpx" not in identificadores
        assert "socket" not in identificadores
        assert "genai" not in identificadores


# --------------------------------------------------------------------------- #
# La instrumentación no rompe el determinismo del informe canónico
# --------------------------------------------------------------------------- #


def test_el_informe_canonico_sigue_sin_duraciones(dataset):
    serializado = report_to_json(
        evaluate_dataset(
            dataset,
            AdapterCorrecto(dataset),
            adapter_name="doble",
            model_name="gemini-3.6-flash",
        )
    )

    assert "duration_ms" not in serializado
    assert "error_message" not in serializado


def test_la_evidencia_combinada_si_lleva_duraciones(dataset):
    serializado = combined_to_json(combine_batches(dataset, _dos_lotes(dataset)))
    datos = json.loads(serializado)

    assert len(datos["diagnostics"]) == TOTAL_CASES
    assert all("duration_ms" in d for d in datos["diagnostics"])
    assert datos["ca4_result"] == "PASS"
    assert datos["protocol_version"] == PROTOCOL_VERSION
