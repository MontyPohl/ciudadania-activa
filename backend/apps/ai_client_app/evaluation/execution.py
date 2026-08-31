"""Ejecución canónica de un lote de recuperación contra el proveedor real.

Existe para cerrar una divergencia que el prerregistro por sí solo no impedía. El
protocolo declara `request_timeout_seconds`, y la evidencia lo registra, pero el
timeout que de verdad gobierna la petición se fijaba aparte, al construir el
cliente del SDK:

    client = build_client(api_key=..., timeout_seconds=20)   # ← una fuente
    evaluate_batch(..., protocol=R2)                         # ← otra fuente

Con dos fuentes independientes, una corrida podía dejar escrito
`request_timeout_seconds = 60` en la evidencia mientras el cliente esperaba 20 s.
El número archivado describiría un experimento que no ocurrió, y el fallo sería
invisible en la propia evidencia.

Aquí el protocolo es la **única** fuente: `run_recovery_batch` deriva el modelo y
el timeout de la misma instancia que después valida el lote, así que no hay dos
valores que puedan discrepar. Tampoco acepta `model_name` ni
`request_timeout_seconds` como parámetros: lo que se puede derivar no se pasa a
mano.

El módulo no toca la red al importarse —el SDK de Google se importa dentro de la
función, igual que en `adapters.runtime`—, no lee `GEMINI_API_KEY` del entorno ni
de `settings` —la clave entra explícita y no se conserva— y no usa Celery.
"""

from __future__ import annotations

from collections.abc import Callable

from .dataset import EvaluationDataset
from .recovery import BatchEvaluation, RecoveryProtocol, evaluate_batch

#: Nombre con el que la evidencia identifica al proveedor real. No es un valor que
#: el prerregistro fije —el adaptador falso sirve para ensayar la maquinaria—, así
#: que vive aquí y no en `RecoveryProtocol`.
GEMINI_ADAPTER_NAME = "gemini"


def build_provider_adapter(
    *,
    protocol: RecoveryProtocol,
    api_key: str,
    client_factory: Callable[..., object] | None = None,
    adapter_factory: Callable[..., object] | None = None,
):
    """Construye el adaptador real con el modelo y el timeout **del protocolo**.

    Las dos factorías entran por parámetro para que las pruebas comprueben con qué
    se llamó a cada una sin tocar el SDK ni la red. Cuando no se pasan, se importan
    de forma diferida: sin ejecutar una recuperación, el SDK de Google no se
    importa y un fallo suyo no puede afectar al arranque.

    La clave se usa aquí y no se guarda en ningún atributo: a partir de este punto
    vive dentro del cliente del SDK, fuera del alcance de un `repr` o de un log.
    """
    if not api_key:
        raise ValueError("La clave del proveedor no puede estar vacía.")

    if client_factory is None or adapter_factory is None:
        from ..adapters.gemini import GeminiAiClassificationAdapter, build_client

        client_factory = client_factory or build_client
        adapter_factory = adapter_factory or GeminiAiClassificationAdapter

    client = client_factory(
        api_key=api_key,
        # Del protocolo, no de un parámetro: es lo que impide que la evidencia
        # declare 60 s y el cliente espere 20.
        timeout_seconds=protocol.request_timeout_seconds,
    )
    return adapter_factory(client=client, model=protocol.model_name)


def run_recovery_batch(
    dataset: EvaluationDataset,
    *,
    protocol: RecoveryProtocol,
    batch_number: int,
    api_key: str,
    client_factory: Callable[..., object] | None = None,
    adapter_factory: Callable[..., object] | None = None,
    **evaluate_kwargs,
) -> BatchEvaluation:
    """Ejecuta un lote de `protocol` contra el proveedor real, una sola vez.

    `protocol` es obligatorio y no tiene valor por omisión: quien ejecute R2 no
    puede olvidarlo y caer en R1 por defecto. La misma instancia gobierna el
    cliente y la validación del lote, de modo que el timeout de la evidencia y el
    de la petición son necesariamente el mismo número.

    `model_name`, `request_timeout_seconds`, `dataset_sha256` e
    `inter_request_delay_seconds` **no** se aceptan: se derivan del protocolo.
    Pasarlos a mano reintroduciría la segunda fuente que este módulo elimina.
    """
    prohibidos = {
        "model_name",
        "request_timeout_seconds",
        "inter_request_delay_seconds",
        "dataset_sha256",
        "protocol",
    } & set(evaluate_kwargs)
    if prohibidos:
        raise TypeError(
            "Estos valores se derivan del protocolo y no se pasan a mano: " f"{sorted(prohibidos)}."
        )

    adapter = build_provider_adapter(
        protocol=protocol,
        api_key=api_key,
        client_factory=client_factory,
        adapter_factory=adapter_factory,
    )

    return evaluate_batch(
        dataset,
        adapter,
        batch_number=batch_number,
        adapter_name=evaluate_kwargs.pop("adapter_name", GEMINI_ADAPTER_NAME),
        model_name=protocol.model_name,
        dataset_sha256=protocol.dataset_sha256,
        protocol=protocol,
        **evaluate_kwargs,
    )
