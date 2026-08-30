"""Resolución del proveedor en tiempo de ejecución (S8-D).

Devuelve un adaptador **solo** si tres condiciones se cumplen a la vez: el
interruptor `AI_CLASSIFICATION_ENABLED` está encendido, hay clave y hay modelo.
Si falta cualquiera, `None`, y `ai.classify_report` sale sin tocar la base de
datos ni la red.

El interruptor es deliberado y está separado de la credencial. La creación de
reportes encola `ai.classify_report` en producción desde el Sprint 6; sin él, una
`GEMINI_API_KEY` olvidada en las variables de un entorno bastaría para que un
despliegue empezara a clasificar incidencias ciudadanas reales sin que nadie lo
hubiese decidido. Activar la clasificación tiene que ser un acto explícito, no
una consecuencia de que exista una clave.

Aquí no hay adaptador falso: el doble de pruebas se inyecta sustituyendo esta
función, nunca devolviéndolo desde producción.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings

from .base import AiClassificationAdapter


@dataclass(frozen=True, slots=True)
class RuntimeAdapter:
    """Adaptador resuelto junto al modelo con el que responde.

    El `model_name` viaja aquí y no dentro de la respuesta del proveedor: es un
    dato de configuración, no algo que el modelo deba declarar sobre sí mismo.
    Mantenerlo fuera conserva intacta la interfaz `classify(request) -> str`.
    """

    adapter: AiClassificationAdapter
    model_name: str


def get_runtime_adapter() -> RuntimeAdapter | None:
    """Devuelve el proveedor configurado, o `None` si no hay ninguno activo."""
    if not getattr(settings, "AI_CLASSIFICATION_ENABLED", False):
        return None

    api_key = getattr(settings, "GEMINI_API_KEY", "")
    model = getattr(settings, "GEMINI_MODEL", "")
    if not api_key or not model:
        # Con el interruptor encendido pero la configuración incompleta se falla
        # cerrado y en silencio: clasificar a medias sería peor que no clasificar.
        return None

    # La importación es diferida a propósito: sin proveedor activo, el SDK de Google
    # ni siquiera se importa, y un fallo suyo no puede afectar al arranque.
    from .gemini import GeminiAiClassificationAdapter, build_client

    client = build_client(
        api_key=api_key,
        timeout_seconds=getattr(settings, "AI_REQUEST_TIMEOUT_SECONDS", 20),
    )
    return RuntimeAdapter(
        adapter=GeminiAiClassificationAdapter(client=client, model=model),
        model_name=model,
    )
