"""Tarea de clasificación de reportes (Sprint 8).

Delgada a propósito, como el resto de las tareas del proyecto: la lógica vive en
`orchestration`, y que el transporte sea una cola no cambia dónde están las
reglas.

**Sin proveedor configurado no hace nada.** `get_runtime_adapter()` devuelve
`None` en S8-C, de modo que la tarea sale antes de tocar la base de datos. Esto
es lo que permite integrar la orquestación sin que la creación de reportes en
producción —que encola esta tarea desde el Sprint 6— empiece a clasificar
incidencias reales con un adaptador de pruebas.

**Sin reintentos automáticos**: ni `autoretry_for`, ni `retry_backoff`, ni
`max_retries`. La política de reintentos acotados del contrato S8-A (D9) se
cablea en S8-D, junto con el proveedor real que puede fallar de forma
transitoria. Hoy un fallo deja el reporte en `CREADO`, que es un estado
consultable y no un mensaje perdido.
"""

from celery import shared_task

from .adapters.runtime import get_runtime_adapter
from .orchestration import process_report_classification


@shared_task(name="ai.classify_report")
def classify_report(report_id):
    """Clasifica el reporte si hay un proveedor configurado.

    Es **idempotente**: `process_report_classification` bloquea la fila y relee su
    estado, de modo que dos entregas de la misma tarea —algo que Celery no
    garantiza que no ocurra— producen una sola clasificación, una sola transición
    y una sola entrada de auditoría.

    No propaga las excepciones del proveedor: el resultado queda escrito en el
    estado del reporte. Relanzarlas solo marcaría la tarea como fallida en el
    broker sin nada que reintentar.
    """
    runtime = get_runtime_adapter()
    if runtime is None:
        return None

    process_report_classification(
        report_id,
        adapter=runtime.adapter,
        model_name=runtime.model_name,
    )
    return None
