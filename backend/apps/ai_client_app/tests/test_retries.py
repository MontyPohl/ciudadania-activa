"""Política de reintentos de la tarea de clasificación (S8-D).

Dos invariantes gobiernan estas pruebas. La primera: **solo se reintenta lo
transitorio**; una credencial inválida o una respuesta que no valida no mejoran
por repetirse y reintentarlas gastaría cuota. La segunda: **como máximo cuatro
llamadas al proveedor** por ejecución lógica —la original más tres reintentos—,
porque los reintentos internos del SDK están apagados y esta es la única capa
que reintenta.
"""

import contextlib

import pytest
from celery.exceptions import Retry

from apps.admin_app.models import AuditLog
from apps.ai_client_app import tasks as ai_tasks
from apps.ai_client_app.adapters.runtime import RuntimeAdapter
from apps.ai_client_app.exceptions import (
    PermanentAiMediaError,
    PermanentAiProviderError,
    TransientAiMediaError,
    TransientAiProviderError,
)
from apps.ai_client_app.models import AiClassification
from apps.ai_client_app.orchestration import AUDIT_ACTION
from apps.ai_client_app.tasks import classify_report, compute_retry_countdown
from apps.core.enums import ReportStatus
from apps.reports_app.models import ReportStatusHistory

MODEL_NAME = "gemini-modelo-de-prueba"


@contextlib.contextmanager
def contexto_de_worker(monkeypatch, retries: int = 0):
    """Ejecuta el cuerpo de la tarea como lo haría un worker y espía el reintento.

    Las pruebas invocan `classify_report.run(...)` dentro de este contexto: llamar
    al proxy de la tarea haría que Celery empujase su propio request con
    `retries=0` y descartase el que aquí se simula.

    `self.retry()` se sustituye por un espía que registra el `countdown` y levanta
    `Retry`. Lo que aquí se prueba es **cuándo decide reintentar la tarea y con qué
    espera**, no si Celery sabe encolar: con `task_always_eager`, el `apply_async`
    interno de `retry()` reejecutaría la tarea en el mismo proceso y enmascararía
    justamente la decisión que interesa observar.
    """
    programados = []

    def espia_retry(*, exc=None, countdown=None, **kwargs):
        programados.append({"exc": exc, "countdown": countdown})
        raise Retry(exc=exc, when=countdown)

    classify_report.push_request(retries=retries, called_directly=False)
    monkeypatch.setattr(classify_report, "retry", espia_retry)
    try:
        yield programados
    finally:
        classify_report.pop_request()


RESPUESTA_VALIDA = (
    '{"category_slug": "semaforos", "institution_slug": "municipalidad-de-asuncion",'
    ' "priority": "ALTA", "confidence": 0.95, "inconsistency": false,'
    ' "justification": "Semáforo fuera de servicio."}'
)


class AdapterGuionado:
    """Devuelve o lanza según un guion, contando las llamadas al proveedor."""

    def __init__(self, *efectos):
        self._efectos = list(efectos)
        self.calls = 0

    def classify(self, request):
        self.calls += 1
        efecto = self._efectos[min(self.calls - 1, len(self._efectos) - 1)]
        if isinstance(efecto, Exception):
            raise efecto
        return efecto


@pytest.fixture
def runtime(monkeypatch):
    """Inyecta un adaptador guionado como proveedor de runtime."""

    def _instalar(adapter):
        monkeypatch.setattr(
            ai_tasks,
            "get_runtime_adapter",
            lambda: RuntimeAdapter(adapter=adapter, model_name=MODEL_NAME),
        )
        return adapter

    return _instalar


@pytest.fixture(autouse=True)
def jitter_determinista(monkeypatch):
    """Fija el jitter para que el countdown sea comprobable."""
    monkeypatch.setattr(ai_tasks.random, "uniform", lambda a, b: b)


# --------------------------------------------------------------------------- #
# Retroceso exponencial y jitter
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("intento", "minimo", "maximo"),
    [(0, 1, 2), (1, 2, 4), (2, 4, 8)],
)
def test_el_retroceso_crece_de_forma_exponencial(intento, minimo, maximo, monkeypatch):
    monkeypatch.setattr(ai_tasks.random, "uniform", lambda a, b: 0)
    assert compute_retry_countdown(intento) == minimo

    monkeypatch.setattr(ai_tasks.random, "uniform", lambda a, b: b)
    assert compute_retry_countdown(intento) == maximo


@pytest.mark.parametrize("intento", [0, 1, 2, 3])
def test_el_jitter_se_mantiene_dentro_del_rango(intento, monkeypatch):
    """Sin jitter, una caída del proveedor produce una segunda oleada simultánea."""
    monkeypatch.undo()
    base = 2**intento

    for _ in range(50):
        countdown = compute_retry_countdown(intento)
        assert base <= countdown <= 2 * base


def test_la_tarea_no_duerme_el_worker():
    """Celery programa el reintento; bloquear el proceso sería otra cosa."""
    import inspect

    fuente = inspect.getsource(ai_tasks)

    assert "time.sleep" not in fuente
    assert "sleep(" not in fuente


# --------------------------------------------------------------------------- #
# Qué se reintenta y qué no
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
@pytest.mark.parametrize(
    "error",
    [
        TransientAiProviderError("429"),
        TransientAiProviderError("503"),
        TransientAiMediaError("storage caído"),
    ],
    ids=["proveedor-429", "proveedor-5xx", "storage-transitorio"],
)
def test_los_fallos_transitorios_se_reintentan(report, runtime, error, monkeypatch):
    runtime(AdapterGuionado(error))

    with contexto_de_worker(monkeypatch), pytest.raises(Retry):
        classify_report.run(str(report.pk))

    report.refresh_from_db()
    assert report.status == ReportStatus.CREADO
    assert AiClassification.objects.count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize(
    "error",
    [
        PermanentAiProviderError("401"),
        PermanentAiProviderError("404"),
        PermanentAiMediaError("objeto inexistente"),
    ],
    ids=["credencial", "modelo-inexistente", "imagen-ausente"],
)
def test_los_fallos_permanentes_no_se_reintentan(report, runtime, error, monkeypatch):
    adapter = runtime(AdapterGuionado(error))

    with contexto_de_worker(monkeypatch), pytest.raises(type(error)):
        classify_report.run(str(report.pk))

    assert adapter.calls <= 1
    report.refresh_from_db()
    assert report.status == ReportStatus.CREADO
    assert AiClassification.objects.count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize(
    "raw",
    ['{"category_slug": "x"}', "no soy json", '{"a": 1}'],
    ids=["campos-faltantes", "texto", "campo-desconocido"],
)
def test_una_respuesta_invalida_no_se_reintenta(report, runtime, raw, monkeypatch):
    """El proveedor respondió; insistir daría lo mismo."""
    adapter = runtime(AdapterGuionado(raw))

    with contexto_de_worker(monkeypatch):
        assert classify_report.run(str(report.pk)) is None

    assert adapter.calls == 1
    report.refresh_from_db()
    assert report.status == ReportStatus.CREADO
    assert AiClassification.objects.count() == 0


# --------------------------------------------------------------------------- #
# Errores reales de la Interactions API, extremo a extremo
#
# El adaptador los traduce y la tarea decide sobre esa traducción. Antes de la
# corrección, un 429 real llegaba sin clasificar y no disparaba reintento.
# --------------------------------------------------------------------------- #


def error_de_interactions(clase, status):
    import httpx
    from google.genai._gaos.lib import compat_errors  # noqa: F401

    peticion = httpx.Request("POST", "https://ejemplo.invalido/v1/interactions")
    respuesta = httpx.Response(status_code=status, request=peticion)
    return clase("error del proveedor", response=respuesta, body=None)


class AdapterGemini:
    """Adaptador real de Gemini sobre un cliente que solo sabe fallar."""

    def __init__(self, error):
        from apps.ai_client_app.adapters.gemini import GeminiAiClassificationAdapter

        class Interactions:
            def create(self, **kwargs):
                raise error

        cliente = type("C", (), {"interactions": Interactions()})()
        self._adapter = GeminiAiClassificationAdapter(client=cliente, model=MODEL_NAME)
        self.calls = 0

    def classify(self, request):
        self.calls += 1
        return self._adapter.classify(request)


@pytest.mark.django_db
@pytest.mark.parametrize("status", [429, 500, 503])
def test_un_error_transitorio_real_dispara_reintento(report, runtime, monkeypatch, status):
    from google.genai._gaos.lib import compat_errors

    clase = compat_errors.RateLimitError if status == 429 else compat_errors.InternalServerError
    runtime(AdapterGemini(error_de_interactions(clase, status)))

    with contexto_de_worker(monkeypatch) as programados, pytest.raises(Retry):
        classify_report.run(str(report.pk))

    assert len(programados) == 1
    report.refresh_from_db()
    assert report.status == ReportStatus.CREADO


@pytest.mark.django_db
@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_un_error_permanente_real_no_dispara_reintento(report, runtime, monkeypatch, status):
    from google.genai._gaos.lib import compat_errors

    clases = {
        400: compat_errors.BadRequestError,
        401: compat_errors.AuthenticationError,
        403: compat_errors.PermissionDeniedError,
        404: compat_errors.NotFoundError,
        422: compat_errors.UnprocessableEntityError,
    }
    adapter = AdapterGemini(error_de_interactions(clases[status], status))
    runtime(adapter)

    from apps.ai_client_app.exceptions import PermanentAiProviderError

    with contexto_de_worker(monkeypatch) as programados, pytest.raises(PermanentAiProviderError):
        classify_report.run(str(report.pk))

    assert programados == [], "un error permanente no debe programar reintento"
    assert adapter.calls == 1
    report.refresh_from_db()
    assert report.status == ReportStatus.CREADO
    assert AiClassification.objects.count() == 0


@pytest.mark.django_db
def test_el_404_de_la_corrida_canonica_no_se_reintenta(report, runtime, monkeypatch):
    """Escenario exacto de S8-E2: reintentarlo habría multiplicado el gasto por cuatro."""
    from google.genai._gaos.lib import compat_errors

    from apps.ai_client_app.exceptions import PermanentAiProviderError

    adapter = AdapterGemini(error_de_interactions(compat_errors.NotFoundError, 404))
    runtime(adapter)

    with contexto_de_worker(monkeypatch) as programados, pytest.raises(PermanentAiProviderError):
        classify_report.run(str(report.pk))

    assert programados == []
    assert adapter.calls == 1


# --------------------------------------------------------------------------- #
# Tope de intentos
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_agotados_los_reintentos_no_se_programa_otro(report, runtime, monkeypatch):
    runtime(AdapterGuionado(TransientAiProviderError("503")))

    with contexto_de_worker(monkeypatch, retries=3):
        assert classify_report.run(str(report.pk)) is None

    report.refresh_from_db()
    assert report.status == ReportStatus.CREADO
    assert AiClassification.objects.count() == 0
    assert AuditLog.objects.filter(action=AUDIT_ACTION).count() == 0


@pytest.mark.django_db
def test_como_maximo_cuatro_llamadas_al_proveedor(report, runtime, settings, monkeypatch):
    """El intento original más tres reintentos. Ni una más."""
    adapter = runtime(AdapterGuionado(TransientAiProviderError("503")))
    reintentos_programados = 0

    for numero in range(settings.AI_MAX_RETRIES + 5):
        with contexto_de_worker(monkeypatch, retries=numero):
            try:
                classify_report.run(str(report.pk))
            except Retry:
                reintentos_programados += 1
                continue
            break

    assert adapter.calls == 4, f"el proveedor recibió {adapter.calls} llamadas"
    assert reintentos_programados == 3, "se programaron más de tres reintentos"


# --------------------------------------------------------------------------- #
# Éxito tras reintento e idempotencia
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_exito_despues_de_un_reintento(report, runtime, monkeypatch):
    adapter = runtime(AdapterGuionado(TransientAiProviderError("429"), RESPUESTA_VALIDA))

    with contexto_de_worker(monkeypatch), pytest.raises(Retry):
        classify_report.run(str(report.pk))

    with contexto_de_worker(monkeypatch, retries=1):
        assert classify_report.run(str(report.pk)) is None

    assert adapter.calls == 2
    assert AiClassification.objects.count() == 1
    assert ReportStatusHistory.objects.filter(new_status=ReportStatus.CLASIFICADO).count() == 1
    assert AuditLog.objects.filter(action=AUDIT_ACTION).count() == 1

    report.refresh_from_db()
    assert report.status == ReportStatus.CLASIFICADO


@pytest.mark.django_db
def test_una_reentrega_posterior_al_exito_no_llama_al_proveedor(report, runtime, monkeypatch):
    """La guarda previa evita gastar cuota en un reporte ya clasificado."""
    adapter = runtime(AdapterGuionado(RESPUESTA_VALIDA))

    with contexto_de_worker(monkeypatch):
        classify_report.run(str(report.pk))
        classify_report.run(str(report.pk))

    assert adapter.calls == 1
    assert AiClassification.objects.count() == 1
    assert ReportStatusHistory.objects.filter(new_status=ReportStatus.CLASIFICADO).count() == 1
    assert AuditLog.objects.filter(action=AUDIT_ACTION).count() == 1


@pytest.mark.django_db
def test_sin_proveedor_no_se_consulta_la_base_de_datos(django_assert_num_queries):
    with django_assert_num_queries(0):
        assert classify_report("id-inexistente") is None
