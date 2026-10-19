"""Orquestación de la clasificación de un reporte (S8-C).

Une las piezas puras de S8-B —parser, mapeo, política— con la persistencia, la
transición de estado y la auditoría. Vive fuera de `tasks.py` para poder
ejecutarse y probarse sin Celery.

El adaptador entra por parámetro: esta función nunca resuelve el proveedor. Quién
es el proveedor lo decide `adapters.runtime`, y en S8-C no hay ninguno.

**Dos garantías estructurales.** La llamada al proveedor ocurre *fuera* de la
transacción: hoy el único adaptador es local, pero en S8-D será una petición de
red con veinte segundos de espera, y sostener un `select_for_update` durante ese
tiempo convertiría una API lenta en un bloqueo de fila prolongado. Y el estado
del reporte se comprueba dos veces —antes de llamar al proveedor y otra vez ya
dentro del lock—, porque la primera lectura es una optimización y solo la segunda
es autoridad.
"""

from __future__ import annotations

import logging

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.admin_app.models import Category, Institution
from apps.core.enums import ActorType, ReportPriority, ReportStatus
from apps.core.services import audit
from apps.reports_app.models import Report, ReportStatusHistory

from .contracts import ClassificationRequest
from .exceptions import AiIntegrationError
from .media import load_report_image
from .models import AiClassification
from .parser import ClassificationParseError, parse_classification_response
from .policy import derive_has_changes, resolve_classification
from .prompts import PROMPT_VERSION

logger = logging.getLogger(__name__)

# Tope defensivo del contrato (D16).
MAX_RAW_RESPONSE_CHARS = 8000

AUDIT_ACTION = "ai.report.classified"

DECISION_AUTO_APPLY = "auto_apply"
DECISION_HUMAN_REVIEW = "human_review"


def process_report_classification(report_id, *, adapter, model_name: str) -> bool:
    """Clasifica el reporte `report_id` con `adapter`. Devuelve si persistió algo.

    Un `False` nunca es una excepción propagada: es una salida controlada por
    reporte inexistente, estado incompatible, fallo del proveedor o respuesta que
    no valida. En todos esos casos el reporte permanece como estaba —`CREADO` si
    aún no se había clasificado— y no se escribe nada.

    S8-C no reintenta. El cableado de reintentos acotados corresponde a S8-D,
    cuando exista un proveedor real que pueda fallar de forma transitoria.
    """
    report = _load_pending_report(report_id)
    if report is None:
        return False

    # La descarga y la llamada al proveedor ocurren aquí, antes de abrir la
    # transacción: sostener un `select_for_update` durante una lectura de Storage y
    # una petición de red de veinte segundos convertiría un servicio lento en un
    # bloqueo de fila prolongado.
    image = load_report_image(report)
    request = _build_request(report, image)

    try:
        usage = None
        classify_with_metadata = getattr(adapter, "classify_with_metadata", None)
        if callable(classify_with_metadata):
            call = classify_with_metadata(request)
            raw_response = call.text
            usage = call.usage
        else:
            raw_response = adapter.classify(request)
    except AiIntegrationError:
        # Transitorio o permanente: lo decide la tarea, que es quien reintenta.
        raise
    except Exception:
        # El proveedor no forma parte de la atomicidad del reporte. No se registra
        # la excepción con su contenido: podría arrastrar el cuerpo de la petición.
        logger.warning("El proveedor de IA falló al clasificar el reporte %s.", report_id)
        return False

    try:
        parsed = parse_classification_response(raw_response)
    except ClassificationParseError as exc:
        # Una respuesta que no valida no se persiste: `ai_classifications` guarda
        # clasificaciones, no intentos fallidos del proveedor. El reporte sigue
        # `CREADO`, que es exactamente «sin clasificar».
        logger.warning("Respuesta de IA inválida para el reporte %s: %s.", report_id, exc)
        return False

    with transaction.atomic():
        report = Report.objects.select_for_update().filter(pk=report.pk).first()
        # Segunda guarda: entre la comprobación previa y el lock pudo entrar otra
        # entrega de la misma tarea, o un operador pudo mover el reporte. La
        # autoridad es este estado, no el que se leyó antes de llamar al proveedor.
        if report is None or report.status != ReportStatus.CREADO:
            return False

        resolved = resolve_classification(parsed)
        has_changes = derive_has_changes(
            report.category.slug,
            report.institution.slug if report.institution_id else None,
            report.priority,
            parsed,
        )

        classification = AiClassification.objects.create(
            report=report,
            suggested_category=resolved.category,
            suggested_institution=resolved.institution,
            suggested_priority=parsed.priority,
            confidence=parsed.confidence,
            inconsistency_flag=resolved.effective_inconsistency,
            justification=parsed.justification,
            model_name=model_name,
            input_tokens=usage.input_tokens if usage is not None else None,
            output_tokens=usage.output_tokens if usage is not None else None,
            total_tokens=usage.total_tokens if usage is not None else None,
        )

        applied = resolved.eligible_for_auto_apply
        campos = ["status", "updated_at"]
        if applied:
            # Solo estos tres. La descripción, la ubicación, la evidencia y todo lo
            # relativo a puntos son del ciudadano y de los operadores, no de la IA.
            report.category = resolved.category
            report.institution = resolved.institution
            report.priority = parsed.priority
            campos = ["category", "institution", "priority", *campos]

        # Se transiciona en los cuatro desenlaces con resultado, no solo cuando la
        # sugerencia se aplica: `CLASIFICADO` significa que la IA se pronunció y
        # quedó registrada (contrato S8-A, D7). Nunca `RECHAZADO`, que es humano.
        previous_status = report.status
        report.status = ReportStatus.CLASIFICADO
        report.save(update_fields=campos)

        ReportStatusHistory.objects.create(
            report=report,
            previous_status=previous_status,
            new_status=ReportStatus.CLASIFICADO,
            # Nullable por diseño para los actores no humanos (RF-16). La autoría de
            # la IA queda en `audit_logs` con `actor_type='ai'`.
            changed_by=None,
        )

        audit.log(
            None,
            AUDIT_ACTION,
            {
                "report_id": str(report.pk),
                "classification_id": str(classification.pk),
                "prompt_version": PROMPT_VERSION,
                "model_name": model_name,
                "raw_response": raw_response[:MAX_RAW_RESPONSE_CHARS],
                "decision": DECISION_AUTO_APPLY if applied else DECISION_HUMAN_REVIEW,
                "applied": applied,
                "requires_human_review": resolved.requires_human_review,
                "has_changes": has_changes,
            },
            actor_type=ActorType.AI,
        )

    return True


def _load_pending_report(report_id) -> Report | None:
    """Lee el reporte solo si sigue pendiente de clasificar.

    Filtrar por estado aquí evita llamar al proveedor —y gastar cuota— cuando una
    reentrega llega después de que el reporte ya se clasificó.
    """
    try:
        return (
            Report.objects.select_related("category", "institution")
            .filter(pk=report_id, status=ReportStatus.CREADO)
            .first()
        )
    except (ValidationError, ValueError, TypeError):
        # Un identificador que ni siquiera es un UUID no merece una excepción que
        # marque la tarea como fallida en el broker: no hay nada que reintentar.
        logger.warning("Identificador de reporte inválido en la clasificación de IA.")
        return None


def _build_request(report: Report, image=None) -> ClassificationRequest:
    """Construye la petición con la lista blanca del contrato (D1).

    Se enumeran los campos uno a uno a propósito: nada del ciudadano, ninguna
    coordenada y ningún identificador interno pueden colarse por serializar el
    reporte entero.

    La fotografía llega ya descargada del bucket privado y viaja como bytes. No se
    incluye su clave, su bucket ni ninguna URL: el proveedor recibe la imagen, no
    la forma de conseguirla (D11).
    """
    return ClassificationRequest(
        description=report.description,
        current_category_slug=report.category.slug,
        current_institution_slug=report.institution.slug if report.institution_id else None,
        allowed_category_slugs=tuple(
            Category.objects.filter(is_active=True).order_by("slug").values_list("slug", flat=True)
        ),
        allowed_institution_slugs=tuple(
            Institution.objects.filter(is_active=True)
            .order_by("slug")
            .values_list("slug", flat=True)
        ),
        allowed_priorities=tuple(ReportPriority.values),
        image_bytes=image.content if image is not None else b"",
        image_mime_type=image.mime_type if image is not None else None,
    )
