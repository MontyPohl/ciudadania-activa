"""Pruebas de la orquestación de clasificación (S8-C).

El adaptador entra siempre por parámetro y nunca sale de la máquina. Lo que se
verifica es que cada desenlace del contrato acabe en el estado correcto, que nada
quede a medias y que una segunda entrega no duplique nada.
"""

import json
from decimal import Decimal

import pytest

from apps.admin_app.models import AuditLog, Category, Institution
from apps.ai_client_app import orchestration
from apps.ai_client_app.adapters.openai import ProviderCall
from apps.ai_client_app.contracts import ProviderUsage
from apps.ai_client_app.exceptions import PermanentAiProviderError, TransientAiProviderError
from apps.ai_client_app.models import AiClassification
from apps.ai_client_app.orchestration import (
    AUDIT_ACTION,
    MAX_RAW_RESPONSE_CHARS,
    PROMPT_VERSION,
    process_report_classification,
)
from apps.core.enums import ActorType, ReportPriority, ReportStatus
from apps.reports_app.models import Report, ReportStatusHistory
from apps.rewards_app.models import RewardTransaction

pytestmark = pytest.mark.django_db

MODEL_NAME = "fake-s8-c"


class ScriptedAdapter:
    """Adaptador de guion: devuelve lo que la prueba necesita y cuenta llamadas."""

    def __init__(self, payload=None, raw=None, error=None):
        self._payload = payload
        self._raw = raw
        self._error = error
        self.calls = 0
        self.requests = []

    def classify(self, request) -> str:
        self.calls += 1
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        if self._raw is not None:
            return self._raw
        return json.dumps(self._payload)


def payload(**overrides) -> dict:
    datos = {
        "category_slug": "semaforos",
        "institution_slug": "municipalidad-de-asuncion",
        "priority": "ALTA",
        "confidence": 0.95,
        "inconsistency": False,
        "justification": "La imagen muestra un semáforo fuera de servicio.",
    }
    datos.update(overrides)
    return datos


def run(report, adapter, model_name=MODEL_NAME) -> bool:
    return process_report_classification(report.pk, adapter=adapter, model_name=model_name)


def audit_entries():
    return AuditLog.objects.filter(action=AUDIT_ACTION)


# --------------------------------------------------------------------------- #
# Aplicación automática asistida
# --------------------------------------------------------------------------- #


def test_auto_aplica_cuando_la_policy_lo_permite(report, institution):
    adapter = ScriptedAdapter(payload())

    assert run(report, adapter) is True

    report.refresh_from_db()
    assert report.category.slug == "semaforos"
    assert report.institution == institution
    assert report.priority == "ALTA"
    assert report.status == ReportStatus.CLASIFICADO


def test_la_frontera_exacta_del_umbral_se_aplica(report):
    """0.75 alcanza el umbral: la comparación es `>=`, no `>`."""
    adapter = ScriptedAdapter(payload(confidence=0.75))

    assert run(report, adapter) is True

    report.refresh_from_db()
    assert report.category.slug == "semaforos"
    assert audit_entries().get().payload["applied"] is True


def test_la_clasificacion_aplicada_queda_persistida(report, institution):
    run(report, ScriptedAdapter(payload()))

    clasificacion = AiClassification.objects.get()
    assert clasificacion.report_id == report.pk
    assert clasificacion.suggested_category.slug == "semaforos"
    assert clasificacion.suggested_institution == institution
    assert clasificacion.suggested_priority == "ALTA"
    assert clasificacion.confidence == Decimal("0.95")
    assert clasificacion.inconsistency_flag is False
    assert clasificacion.justification == payload()["justification"]
    assert clasificacion.model_name == MODEL_NAME


def test_el_model_name_lo_fija_la_orquestacion_y_no_el_proveedor(report):
    """No se infiere de la respuesta ni se le pide al modelo en el JSON."""
    run(report, ScriptedAdapter(payload()), model_name="otro-modelo-v9")

    assert AiClassification.objects.get().model_name == "otro-modelo-v9"


def test_solo_se_tocan_categoria_institucion_y_prioridad(report):
    original = (report.description, report.citizen_id, report.location.wkt, report.source)

    run(report, ScriptedAdapter(payload()))

    report.refresh_from_db()
    assert (report.description, report.citizen_id, report.location.wkt, report.source) == original


# --------------------------------------------------------------------------- #
# Revisión humana: se persiste, no se aplica
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "overrides",
    [
        {"confidence": 0.74},
        {"inconsistency": True},
        {"category_slug": "categoria-que-no-existe"},
        {"institution_slug": "institucion-que-no-existe"},
        {"category_slug": None},
        {"institution_slug": None},
    ],
    ids=[
        "confianza-baja",
        "inconsistencia",
        "categoria-desconocida",
        "institucion-desconocida",
        "categoria-nula",
        "institucion-nula",
    ],
)
def test_los_casos_de_revision_persisten_pero_no_aplican(report, overrides):
    previo = (report.category_id, report.institution_id, report.priority)

    assert run(report, ScriptedAdapter(payload(**overrides))) is True

    report.refresh_from_db()
    assert (report.category_id, report.institution_id, report.priority) == previo
    assert report.status == ReportStatus.CLASIFICADO
    assert AiClassification.objects.count() == 1
    assert ReportStatusHistory.objects.filter(new_status=ReportStatus.CLASIFICADO).count() == 1

    entrada = audit_entries().get()
    assert entrada.payload["applied"] is False
    assert entrada.payload["requires_human_review"] is True
    assert entrada.payload["decision"] == "human_review"


def test_la_confianza_baja_conserva_la_sugerencia_para_el_operador(report):
    run(report, ScriptedAdapter(payload(confidence=0.74)))

    clasificacion = AiClassification.objects.get()
    assert clasificacion.suggested_category.slug == "semaforos"
    assert clasificacion.confidence == Decimal("0.74")
    assert clasificacion.inconsistency_flag is False


@pytest.mark.parametrize("campo", ["category_slug", "institution_slug"])
def test_un_slug_desconocido_deja_la_fk_nula_y_marca_inconsistencia(report, campo):
    run(report, ScriptedAdapter(payload(**{campo: "no-existe-en-el-catalogo"})))

    clasificacion = AiClassification.objects.get()
    fk = "suggested_category" if campo == "category_slug" else "suggested_institution"
    assert getattr(clasificacion, fk) is None
    assert clasificacion.inconsistency_flag is True


def test_una_categoria_inventada_no_se_da_de_alta(report):
    categorias = Category.objects.count()
    instituciones = Institution.objects.count()

    run(report, ScriptedAdapter(payload(category_slug="semaforo-emocional-roto")))

    assert Category.objects.count() == categorias
    assert Institution.objects.count() == instituciones
    assert not Category.objects.filter(slug="semaforo-emocional-roto").exists()


def test_una_categoria_inactiva_no_se_aplica(report, category):
    Category.objects.filter(slug="semaforos").update(is_active=False)
    previo = report.category_id

    run(report, ScriptedAdapter(payload()))

    report.refresh_from_db()
    assert report.category_id == previo
    assert AiClassification.objects.get().suggested_category is None


# --------------------------------------------------------------------------- #
# Fallos: nada se escribe y el reporte sigue CREADO
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw",
    [
        "no soy json",
        "{",
        "[]",
        "null",
        '{"category_slug": "semaforos"}',
        json.dumps({**payload(), "hubo_cambio": True}),
        json.dumps({**payload(), "confidence": "0.9"}),
        f"```json\n{json.dumps(payload())}\n```",
    ],
    ids=[
        "texto",
        "truncado",
        "array",
        "null",
        "campos-faltantes",
        "campo-extra",
        "confianza-string",
        "valla-markdown",
    ],
)
def test_una_respuesta_invalida_no_escribe_nada(report, raw):
    assert run(report, ScriptedAdapter(raw=raw)) is False

    report.refresh_from_db()
    assert report.status == ReportStatus.CREADO
    assert AiClassification.objects.count() == 0
    assert ReportStatusHistory.objects.filter(new_status=ReportStatus.CLASIFICADO).count() == 0
    assert audit_entries().count() == 0


def test_un_fallo_del_proveedor_deja_el_reporte_creado(report):
    adapter = ScriptedAdapter(error=RuntimeError("proveedor caído"))

    assert run(report, adapter) is False

    report.refresh_from_db()
    assert report.status == ReportStatus.CREADO
    assert AiClassification.objects.count() == 0
    assert audit_entries().count() == 0


def test_un_reporte_inexistente_no_llama_al_proveedor():
    adapter = ScriptedAdapter(payload())

    assert (
        process_report_classification(
            "11111111-1111-4111-8111-111111111111", adapter=adapter, model_name=MODEL_NAME
        )
        is False
    )
    assert adapter.calls == 0
    assert AiClassification.objects.count() == 0


def test_un_identificador_que_no_es_uuid_no_revienta():
    adapter = ScriptedAdapter(payload())

    resultado = process_report_classification(
        "no-soy-un-uuid", adapter=adapter, model_name=MODEL_NAME
    )

    assert resultado is False
    assert adapter.calls == 0


@pytest.mark.parametrize(
    "estado",
    [
        ReportStatus.CLASIFICADO,
        ReportStatus.ASIGNADO,
        ReportStatus.EN_PROCESO,
        ReportStatus.RESUELTO,
        ReportStatus.CERRADO,
        ReportStatus.RECHAZADO,
    ],
)
def test_un_reporte_fuera_de_creado_no_se_toca(report, estado):
    """La IA no sobrescribe una decisión humana posterior."""
    Report.objects.filter(pk=report.pk).update(status=estado)
    adapter = ScriptedAdapter(payload())

    assert run(report, adapter) is False
    assert adapter.calls == 0

    report.refresh_from_db()
    assert report.status == estado
    assert AiClassification.objects.count() == 0
    assert audit_entries().count() == 0


# --------------------------------------------------------------------------- #
# Idempotencia
# --------------------------------------------------------------------------- #


def test_una_segunda_entrega_no_duplica_nada(report):
    adapter = ScriptedAdapter(payload())

    assert run(report, adapter) is True
    assert run(report, adapter) is False

    assert adapter.calls == 1, "la segunda entrega no debe volver a gastar cuota"
    assert AiClassification.objects.count() == 1
    assert ReportStatusHistory.objects.filter(new_status=ReportStatus.CLASIFICADO).count() == 1
    assert audit_entries().count() == 1

    report.refresh_from_db()
    assert report.status == ReportStatus.CLASIFICADO


def test_la_guarda_dentro_del_lock_es_la_autoridad(report, monkeypatch):
    """Fuerza la ventana entre la comprobación previa y el lock, sin hilos.

    La primera lectura es una optimización para no gastar cuota; si otra entrega
    clasifica el reporte mientras el proveedor responde, la segunda comprobación
    —ya dentro de `select_for_update`— debe descartar el resultado.
    """
    original = orchestration.parse_classification_response

    def clasificar_por_detras(raw):
        Report.objects.filter(pk=report.pk).update(status=ReportStatus.CLASIFICADO)
        return original(raw)

    monkeypatch.setattr(orchestration, "parse_classification_response", clasificar_por_detras)

    assert run(report, ScriptedAdapter(payload())) is False
    assert AiClassification.objects.count() == 0
    assert audit_entries().count() == 0


# --------------------------------------------------------------------------- #
# Historial, auditoría y atomicidad
# --------------------------------------------------------------------------- #


def test_el_historial_registra_la_transicion_sin_actor_humano(report):
    run(report, ScriptedAdapter(payload()))

    historial = ReportStatusHistory.objects.get(new_status=ReportStatus.CLASIFICADO)
    assert historial.previous_status == ReportStatus.CREADO
    assert historial.report_id == report.pk
    assert historial.changed_by is None


def test_la_auditoria_usa_actor_type_ai(report):
    run(report, ScriptedAdapter(payload()))

    entrada = audit_entries().get()
    assert entrada.actor_type == ActorType.AI
    assert entrada.actor_user is None


def test_el_payload_de_auditoria_tiene_las_claves_del_contrato(report):
    run(report, ScriptedAdapter(payload()))

    entrada = audit_entries().get()
    clasificacion = AiClassification.objects.get()

    assert set(entrada.payload) == {
        "report_id",
        "classification_id",
        "prompt_version",
        "model_name",
        "raw_response",
        "decision",
        "applied",
        "requires_human_review",
        "has_changes",
    }
    assert entrada.payload["report_id"] == str(report.pk)
    assert entrada.payload["classification_id"] == str(clasificacion.pk)
    assert entrada.payload["prompt_version"] == PROMPT_VERSION == "s8-classification-v1"
    assert entrada.payload["model_name"] == MODEL_NAME
    assert entrada.payload["decision"] == "auto_apply"
    assert entrada.payload["applied"] is True
    assert entrada.payload["requires_human_review"] is False


def test_la_auditoria_no_filtra_datos_del_ciudadano(report):
    run(report, ScriptedAdapter(payload()))

    texto = json.dumps(audit_entries().get().payload)
    assert report.description not in texto
    assert str(report.citizen_id) not in texto
    assert "-25.2" not in texto and "-57.6" not in texto
    for prohibida in ("description", "citizen", "user_id", "email", "location", "prompt", "image"):
        assert prohibida not in audit_entries().get().payload


def test_la_respuesta_cruda_se_trunca(report):
    largo = json.dumps(payload(justification="x" * 990)) + " " * 20000
    adapter = ScriptedAdapter(raw=largo)

    run(report, adapter)

    guardada = audit_entries().get().payload["raw_response"]
    assert len(guardada) <= MAX_RAW_RESPONSE_CHARS == 8000


def test_has_changes_lo_deriva_el_backend(report):
    """El reporte nace en `baches`; la sugerencia es `semaforos`."""
    run(report, ScriptedAdapter(payload()))

    assert audit_entries().get().payload["has_changes"] is True


def test_sin_cambios_cuando_la_sugerencia_confirma_lo_actual(report, institution):
    report.institution = institution
    report.priority = ReportPriority.MEDIA
    report.save(update_fields=["institution", "priority"])

    run(report, ScriptedAdapter(payload(category_slug="baches", priority="MEDIA")))

    assert audit_entries().get().payload["has_changes"] is False


def test_todo_se_revierte_si_falla_una_escritura(report, monkeypatch):
    """Sin estados a medias: o está todo, o no está nada."""
    monkeypatch.setattr(
        orchestration.audit,
        "log",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("auditoría caída")),
    )

    with pytest.raises(RuntimeError, match="auditoría caída"):
        run(report, ScriptedAdapter(payload()))

    report.refresh_from_db()
    assert report.status == ReportStatus.CREADO
    assert report.category.slug == "baches"
    assert AiClassification.objects.count() == 0
    assert ReportStatusHistory.objects.filter(new_status=ReportStatus.CLASIFICADO).count() == 0


# --------------------------------------------------------------------------- #
# Invariantes del contrato
# --------------------------------------------------------------------------- #


def test_la_ia_nunca_produce_un_rechazo(report):
    """Ni con la peor respuesta posible que aún valide (D6)."""
    run(report, ScriptedAdapter(payload(confidence=0.0, inconsistency=True, category_slug=None)))

    report.refresh_from_db()
    assert report.status == ReportStatus.CLASIFICADO
    assert not ReportStatusHistory.objects.filter(new_status=ReportStatus.RECHAZADO).exists()
    assert report.rejection_reason == ""


def test_la_clasificacion_no_genera_ni_altera_puntos(report):
    antes = list(
        RewardTransaction.objects.filter(report=report).values_list("id", "status", "impact_delta")
    )

    run(report, ScriptedAdapter(payload()))

    assert (
        list(
            RewardTransaction.objects.filter(report=report).values_list(
                "id", "status", "impact_delta"
            )
        )
        == antes
    )


def test_la_peticion_al_proveedor_solo_lleva_lo_permitido(report):
    adapter = ScriptedAdapter(payload())

    run(report, adapter)

    peticion = adapter.requests[0]
    assert peticion.description == report.description
    assert peticion.current_category_slug == "baches"
    assert peticion.image_bytes == b""
    assert "semaforos" in peticion.allowed_category_slugs
    assert set(peticion.allowed_priorities) == set(ReportPriority.values)

    campos = set(type(peticion).__dataclass_fields__)
    assert campos.isdisjoint(
        {"report_id", "citizen", "location", "latitude", "longitude", "created_at", "file_hash"}
    )


class MetadataAdapter(ScriptedAdapter):
    def classify_with_metadata(self, request):
        return ProviderCall(
            text=super().classify(request),
            response_id=None,
            request_id=None,
            model=MODEL_NAME,
            usage=ProviderUsage(120, 30, 150),
        )

    def classify(self, request):
        raise AssertionError("Se debe usar classify_with_metadata cuando está disponible.")


def test_persiste_tokens_del_adaptador_con_metadata(report):
    adapter = MetadataAdapter(payload())

    assert run(report, adapter) is True

    clasificacion = AiClassification.objects.get()
    assert clasificacion.input_tokens == 120
    assert clasificacion.output_tokens == 30
    assert clasificacion.total_tokens == 150
    assert adapter.calls == 1


def test_adaptador_solo_con_classify_deja_tokens_nulos(report):
    assert run(report, ScriptedAdapter(payload())) is True

    clasificacion = AiClassification.objects.get()
    assert clasificacion.input_tokens is None
    assert clasificacion.output_tokens is None
    assert clasificacion.total_tokens is None


@pytest.mark.parametrize("tipo", [ScriptedAdapter, MetadataAdapter])
@pytest.mark.parametrize("error", [TransientAiProviderError, PermanentAiProviderError])
def test_propaga_errores_de_integracion_en_ambos_caminos(report, tipo, error):
    with pytest.raises(error):
        run(report, tipo(error=error("Proveedor no disponible.")))

    assert AiClassification.objects.count() == 0


@pytest.mark.parametrize("tipo", [ScriptedAdapter, MetadataAdapter])
def test_error_inesperado_en_ambos_caminos_devuelve_false_y_warning(report, tipo, caplog):
    assert run(report, tipo(error=RuntimeError("Proveedor no disponible."))) is False

    assert AiClassification.objects.count() == 0
    assert "El proveedor de IA falló al clasificar el reporte" in caplog.text


def test_auditoria_recibe_version_explicita(report):
    assert (
        process_report_classification(
            report.pk,
            adapter=ScriptedAdapter(payload()),
            model_name=MODEL_NAME,
            prompt_version="classification-v2",
        )
        is True
    )
    assert audit_entries().get().payload["prompt_version"] == "classification-v2"
