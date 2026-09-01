"""Ejecución canónica de un lote de recuperación contra el proveedor real.

Existe para cerrar divergencias que el prerregistro por sí solo no impedía: casos
en los que el **input real** no era el **input prerregistrado** y sin embargo la
evidencia archivaba los valores prerregistrados. El número guardado describiría un
experimento que no ocurrió, y nada en la propia evidencia lo delataría.

Eran tres, y las tres se cierran aquí ligando lo declarado a lo efectivo:

- **timeout**: el protocolo declaraba `request_timeout_seconds`, pero el timeout
  que gobierna la petición se fijaba aparte al construir el cliente del SDK. Ahora
  ambos salen de la misma instancia.
- **dataset**: se archivaba `protocol.dataset_sha256` sin demostrar que el objeto
  `EvaluationDataset` recibido viniera de esos bytes. Ahora el executor lee los
  bytes, los verifica y **construye el dataset desde ellos**; no acepta un dataset
  ya construido por quien llama.
- **prompt**: `prompt_version` es una etiqueta y no impide que el archivo cambie
  debajo. Ahora se hashea el texto que `render_classification_prompt` va a usar de
  verdad y se compara con el prerregistro.

Cualquier divergencia aborta **antes** de construir el cliente, de modo que una
ejecución con inputs equivocados cuesta cero llamadas al proveedor —y cero cuota,
que es el recurso escaso que obligó a partir la corrida en lotes—.

El módulo no toca la red al importarse —el SDK de Google se importa dentro de la
función, igual que en `adapters.runtime`—, no lee `GEMINI_API_KEY` del entorno ni
de la configuración —la clave entra explícita y no se conserva— y no usa Celery.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

from ..prompts import load_prompt_template
from .dataset import DATASET_PATH, EvaluationDataset, build_dataset
from .recovery import (
    BatchEvaluation,
    RecoveryProtocol,
    RecoveryProtocolError,
    evaluate_batch,
)

#: Nombre con el que la evidencia identifica al proveedor real. No es un valor que
#: el prerregistro fije —el adaptador falso sirve para ensayar la maquinaria—, así
#: que vive aquí y no en `RecoveryProtocol`.
GEMINI_ADAPTER_NAME = "gemini"


def load_verified_dataset(
    *,
    protocol: RecoveryProtocol,
    dataset_path: Path | None = None,
) -> tuple[EvaluationDataset, str]:
    """Lee, verifica y construye el dataset **desde los mismos bytes**.

    Los bytes se leen **una sola vez**: se hashean, se comparan con el
    prerregistro y se parsean. Hashear el archivo y volver a abrirlo para parsear
    dejaría una ventana entre ambas lecturas en la que el contenido podría
    cambiar, y la evidencia acabaría certificando unos bytes distintos de los que
    se enviaron.

    `dataset_path` existe para las pruebas y para una copia legítima en otra ruta.
    No relaja nada: una ruta distinta con los **mismos** bytes es válida; un
    archivo con el mismo `dataset_version` y otro contenido no lo es.
    """
    origen = DATASET_PATH if dataset_path is None else Path(dataset_path)
    try:
        crudo = origen.read_bytes()
    except OSError as exc:
        raise RecoveryProtocolError(f"No se pudo leer el dataset en {origen}.") from exc

    digest = hashlib.sha256(crudo).hexdigest()
    if digest != protocol.dataset_sha256:
        raise RecoveryProtocolError(
            f"El dataset no es el prerregistrado por {protocol.version}: "
            f"se esperaba sha256 {protocol.dataset_sha256} y se leyó {digest}."
        )

    try:
        raw = json.loads(crudo.decode("utf-8"))
    except ValueError as exc:
        raise RecoveryProtocolError("El dataset verificado no es JSON válido.") from exc

    dataset = build_dataset(raw)
    if dataset.dataset_version != protocol.dataset_version:
        # Inalcanzable si el hash coincide, pero la comprobación es barata y deja
        # el fallo legible si algún día el prerregistro se editara a medias.
        raise RecoveryProtocolError(
            f"El dataset declara `{dataset.dataset_version}` y el protocolo exige "
            f"`{protocol.dataset_version}`."
        )
    return dataset, digest


def verify_prompt(*, protocol: RecoveryProtocol) -> str:
    """Comprueba que el prompt que se enviará es el prerregistrado.

    Se hashea el texto devuelto por `load_prompt_template`, que es exactamente el
    que `render_classification_prompt` interpola después: comprobar el archivo por
    separado dejaría fuera la caché del template y podría certificar algo distinto
    de lo que viaja en la petición.
    """
    digest = hashlib.sha256(load_prompt_template().encode("utf-8")).hexdigest()
    if digest != protocol.prompt_sha256:
        raise RecoveryProtocolError(
            f"El prompt no es el prerregistrado por {protocol.version}: "
            f"se esperaba sha256 {protocol.prompt_sha256} y se calculó {digest}."
        )
    return digest


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
    *,
    protocol: RecoveryProtocol,
    batch_number: int,
    api_key: str,
    dataset_path: Path | None = None,
    client_factory: Callable[..., object] | None = None,
    adapter_factory: Callable[..., object] | None = None,
    **evaluate_kwargs,
) -> BatchEvaluation:
    """Ejecuta un lote de `protocol` contra el proveedor real, una sola vez.

    **No recibe un `EvaluationDataset`.** Lo construye desde bytes que él mismo ha
    verificado, porque aceptar un dataset ya construido permitía ejecutar con un
    contenido cualquiera mientras la evidencia archivaba el hash prerregistrado.

    `protocol` es obligatorio y no tiene valor por omisión: quien ejecute R2 no
    puede olvidarlo y caer en R1 por defecto. La misma instancia gobierna el
    cliente, la verificación de los inputs y la validación del lote.

    `model_name`, `request_timeout_seconds`, `dataset_sha256`,
    `inter_request_delay_seconds` y `dataset` **no** se aceptan: se derivan del
    protocolo o de los bytes verificados. Pasarlos a mano reintroduciría la segunda
    fuente que este módulo elimina.

    Orden de las guardas —todas antes de que exista un cliente—: parámetros, bytes
    del dataset, construcción del dataset desde esos bytes, prompt, partición del
    lote, y solo entonces el proveedor.
    """
    prohibidos = {
        "dataset",
        "model_name",
        "request_timeout_seconds",
        "inter_request_delay_seconds",
        "dataset_sha256",
        "protocol",
    } & set(evaluate_kwargs)
    if prohibidos:
        raise TypeError(
            "Estos valores se derivan del protocolo o de los bytes verificados y no "
            f"se pasan a mano: {sorted(prohibidos)}."
        )

    dataset, dataset_digest = load_verified_dataset(protocol=protocol, dataset_path=dataset_path)
    verify_prompt(protocol=protocol)

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
        # El digest medido, no el declarado: coinciden por construcción —si no,
        # esto no se habría alcanzado—, pero lo que queda archivado es lo medido.
        dataset_sha256=dataset_digest,
        protocol=protocol,
        **evaluate_kwargs,
    )
