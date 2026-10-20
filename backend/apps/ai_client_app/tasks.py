"""Tarea de clasificación de reportes (Sprint 8).

Delgada a propósito: la lógica vive en `orchestration` y la decisión de qué
proveedor usar en `adapters.runtime`. Lo único que esta capa aporta es la
política de reintentos, y solo porque el reintento es una decisión de transporte.

**Sin proveedor configurado no hace nada**, ni una consulta. Es lo que permite
que la creación de reportes siga encolando esta tarea en producción sin que se
clasifique nada hasta que `AI_CLASSIFICATION_ENABLED` se encienda de forma
explícita.

**Los reintentos viven aquí y en ningún otro sitio.** El SDK de Google se
construye con `attempts=1`, es decir, cero reintentos internos: si ambas capas
reintentaran, una sola ejecución podría multiplicar las llamadas al proveedor de
forma invisible y agotar cuota sin que ningún contador lo refleje.
"""

import logging
import random

from celery import shared_task
from django.conf import settings

from .adapters.runtime import get_runtime_adapter
from .exceptions import TransientAiError
from .orchestration import process_report_classification

logger = logging.getLogger(__name__)

# Base del retroceso exponencial, en segundos.
BACKOFF_BASE = 2


def compute_retry_countdown(retries: int) -> float:
    """Retroceso exponencial con *jitter* para el reintento número `retries`.

    El jitter reparte en el tiempo los reintentos de varias tareas que fallaron a
    la vez: sin él, una caída momentánea del proveedor produce una segunda oleada
    igual de simultánea que la primera.

    Con base 2, el reintento 0 espera entre 1 y 2 segundos; el 1, entre 2 y 4; el
    2, entre 4 y 8.
    """
    base = BACKOFF_BASE**retries
    return base + random.uniform(0, base)


@shared_task(bind=True, name="ai.classify_report")
def classify_report(self, report_id):
    """Clasifica el reporte si hay un proveedor configurado.

    Es **idempotente**: `process_report_classification` bloquea la fila y relee su
    estado, de modo que dos entregas de la misma tarea producen una sola
    clasificación, una sola transición y una sola entrada de auditoría.

    Solo reintenta ante fallos explícitamente transitorios. Un error de
    credencial, de permisos o una respuesta que no valida no mejoran por
    repetirse: reintentarlos consumiría cuota y retrasaría el momento en que
    alguien se entera del problema.
    """
    runtime = get_runtime_adapter()
    if runtime is None:
        return None

    try:
        process_report_classification(
            report_id,
            adapter=runtime.adapter,
            model_name=runtime.model_name,
            prompt_version=runtime.prompt_version,
        )
    except TransientAiError as exc:
        max_retries = getattr(settings, "AI_MAX_RETRIES", 3)
        if self.request.retries >= max_retries:
            # Agotados los reintentos, el reporte permanece `CREADO`. No se
            # registra la excepción completa: podría arrastrar el cuerpo de la
            # petición y con él la descripción del ciudadano.
            logger.warning(
                "Clasificación de IA agotada tras %s reintentos para el reporte %s.",
                max_retries,
                report_id,
            )
            return None
        raise self.retry(exc=exc, countdown=compute_retry_countdown(self.request.retries)) from exc

    return None
