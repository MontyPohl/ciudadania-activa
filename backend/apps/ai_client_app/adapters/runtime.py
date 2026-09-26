"""Resolución del proveedor en tiempo de ejecución (S8-D).

Devuelve un adaptador **solo** si se cumplen a la vez todas las condiciones: el
interruptor `AI_CLASSIFICATION_ENABLED` está encendido, `AI_PROVIDER` nombra un
proveedor conocido y ese proveedor tiene clave y modelo. Si falta cualquiera,
`None`, y `ai.classify_report` sale sin tocar la base de datos ni la red.

`AI_PROVIDER` no tiene valor por defecto (ADR-0016): tener claves de dos
proveedores en el mismo entorno no puede decidir por sí solo cuál clasifica.

El interruptor es deliberado y está separado de la credencial. La creación de
reportes encola `ai.classify_report` en producción desde el Sprint 6; sin él, una
clave de proveedor olvidada en las variables de un entorno bastaría para que un
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

#: Valores admitidos en `AI_PROVIDER`.
PROVIDER_OPENAI = "openai"
PROVIDER_GEMINI = "gemini"


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

    provider = str(getattr(settings, "AI_PROVIDER", "")).strip().lower()
    if provider == PROVIDER_OPENAI:
        return _openai_adapter()
    if provider == PROVIDER_GEMINI:
        return _gemini_adapter()
    # Proveedor vacío o desconocido: se falla cerrado, igual que sin clave.
    return None


def _openai_adapter() -> RuntimeAdapter | None:
    api_key = getattr(settings, "OPENAI_API_KEY", "")
    model = getattr(settings, "OPENAI_MODEL", "")
    if not api_key or not model:
        # Con el interruptor encendido pero la configuración incompleta se falla
        # cerrado y en silencio: clasificar a medias sería peor que no clasificar.
        return None

    # Importación diferida: sin este proveedor activo, su SDK ni siquiera se importa.
    from .openai import OpenAiClassificationAdapter, build_client

    client = build_client(
        api_key=api_key,
        timeout_seconds=getattr(settings, "AI_REQUEST_TIMEOUT_SECONDS", 20),
    )
    return RuntimeAdapter(
        adapter=OpenAiClassificationAdapter(
            client=client,
            model=model,
            reasoning_effort=getattr(settings, "OPENAI_REASONING_EFFORT", ""),
        ),
        model_name=model,
    )


def _gemini_adapter() -> RuntimeAdapter | None:
    api_key = getattr(settings, "GEMINI_API_KEY", "")
    model = getattr(settings, "GEMINI_MODEL", "")
    if not api_key or not model:
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
