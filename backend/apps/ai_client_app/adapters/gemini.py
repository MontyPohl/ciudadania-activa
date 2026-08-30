"""Adaptador de Google Gemini mediante la Interactions API (S8-D).

Único punto del proyecto que conoce al proveedor (ADR-0008). Recibe el contrato
permitido, devuelve texto crudo y no parsea, no persiste, no toca el ORM y no
conoce Celery.

**Structured output no sustituye al parser.** Se pide a Gemini un esquema JSON
estricto porque reduce la probabilidad de una respuesta malformada, pero la
frontera de seguridad sigue siendo `parse_classification_response` (contrato
S8-A, D13). Este módulo devuelve la cadena tal cual la dio el proveedor.
"""

from __future__ import annotations

import base64
import json
import logging

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from apps.core.enums import ReportPriority

from ..contracts import ClassificationRequest
from ..exceptions import PermanentAiProviderError, TransientAiProviderError
from ..prompts import PROMPT_VERSION, render_classification_prompt

logger = logging.getLogger(__name__)

# `HttpOptions.timeout` se expresa en **milisegundos** en esta versión del SDK.
_MILISEGUNDOS_POR_SEGUNDO = 1000

# `attempts` cuenta el intento original: 1 significa cero reintentos internos. Sin
# fijarlo, el SDK reintentaría por su cuenta hasta cinco veces y se sumaría a los
# reintentos de Celery, multiplicando las llamadas de forma invisible.
_SDK_ATTEMPTS_SIN_REINTENTOS = 1

# Estados terminales de una interacción que no son un éxito.
_ESTADOS_FALLIDOS = frozenset({"failed", "cancelled", "incomplete", "budget_exceeded"})

_ESTADO_COMPLETADO = "completed"

# El SDK usa estos códigos como reintentables por defecto; se replican aquí porque
# la decisión de reintentar pasó a ser nuestra.
_CODIGOS_TRANSITORIOS = frozenset({408, 429})


def build_classification_schema() -> dict:
    """Esquema JSON del contrato v1 (D3), cerrado a los seis campos exactos.

    `justification` no lleva `maxLength`: el límite de 1000 caracteres lo impone el
    parser, que es quien tiene la última palabra, y trasladarlo aquí duplicaría la
    regla en dos sitios que podrían divergir.
    """
    return {
        "type": "object",
        "properties": {
            "category_slug": {"type": ["string", "null"]},
            "institution_slug": {"type": ["string", "null"]},
            "priority": {"type": "string", "enum": list(ReportPriority.values)},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "inconsistency": {"type": "boolean"},
            "justification": {"type": "string"},
        },
        "required": [
            "category_slug",
            "institution_slug",
            "priority",
            "confidence",
            "inconsistency",
            "justification",
        ],
        "additionalProperties": False,
    }


def build_client(*, api_key: str, timeout_seconds: int) -> genai.Client:
    """Crea el cliente con el timeout contractual y sin reintentos internos.

    La clave se usa aquí y no se conserva en ningún atributo: a partir de este
    punto vive dentro del cliente del SDK, fuera del alcance de un `repr`, un log
    o el `str` de una excepción.
    """
    return genai.Client(
        api_key=api_key,
        http_options=genai_types.HttpOptions(
            timeout=timeout_seconds * _MILISEGUNDOS_POR_SEGUNDO,
            retry_options=genai_types.HttpRetryOptions(attempts=_SDK_ATTEMPTS_SIN_REINTENTOS),
        ),
    )


class GeminiAiClassificationAdapter:
    """Implementa `AiClassificationAdapter` contra la Interactions API."""

    def __init__(self, *, client: genai.Client, model: str):
        # El cliente entra ya construido: así las pruebas inyectan un doble sin
        # que exista ninguna ruta que cree un cliente real por descuido.
        self._client = client
        self._model = model

    def __repr__(self) -> str:
        # Explícito para que ni el modelo ni nada del cliente acaben en un traceback.
        return f"{type(self).__name__}(model={self._model!r})"

    def classify(self, request: ClassificationRequest) -> str:
        interaction = self._create_interaction(request)
        self._check_status(interaction)
        return self._extract_text(interaction)

    def _create_interaction(self, request: ClassificationRequest):
        try:
            return self._client.interactions.create(
                model=self._model,
                system_instruction=render_classification_prompt(request),
                input=_build_input(request),
                # No se pide al proveedor que conserve la interacción: la
                # descripción del ciudadano no tiene por qué quedarse allí.
                store=False,
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": build_classification_schema(),
                },
            )
        except genai_errors.APIError as exc:
            raise _traducir_api_error(exc) from exc
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            # Timeout y cortes de transporte: el mismo intento más tarde puede ir bien.
            raise TransientAiProviderError("El proveedor de IA no respondió a tiempo.") from exc

    def _check_status(self, interaction) -> None:
        status = getattr(interaction, "status", None)
        if status is None or status == _ESTADO_COMPLETADO:
            return
        if str(status) in _ESTADOS_FALLIDOS:
            # Un estado terminal distinto de `completed` no mejora al repetirlo sin
            # cambiar la petición. Se trata como permanente salvo que el proveedor
            # haya devuelto además un código estructurado transitorio, que llegaría
            # por `APIError` y no por aquí.
            raise PermanentAiProviderError(f"La interacción de IA terminó en estado {status}.")
        raise PermanentAiProviderError(f"La interacción de IA no completó: {status}.")

    def _extract_text(self, interaction) -> str:
        output_text = getattr(interaction, "output_text", None)
        if not isinstance(output_text, str) or not output_text.strip():
            raise PermanentAiProviderError("La interacción de IA no devolvió texto.")
        return output_text


def _traducir_api_error(exc: genai_errors.APIError) -> Exception:
    """Convierte el error del SDK en transitorio o permanente por su código.

    Se usa el código estructurado y no el texto del mensaje: comparar substrings
    de mensajes convierte cualquier cambio de redacción del proveedor en un
    cambio silencioso de la política de reintentos.
    """
    code = getattr(exc, "code", None)
    if isinstance(code, int) and (code in _CODIGOS_TRANSITORIOS or 500 <= code <= 599):
        return TransientAiProviderError(f"El proveedor de IA respondió {code}.")
    return PermanentAiProviderError(f"El proveedor de IA rechazó la petición ({code}).")


def _build_input(request: ClassificationRequest) -> list[dict]:
    """Bloques de entrada: texto siempre, imagen solo si existe.

    La imagen viaja **inline en base64**. Nunca por URI, URL firmada ni File API:
    eso obligaría a exponer el objeto privado o a subirlo a un almacenamiento del
    proveedor (D11).
    """
    bloques: list[dict] = [{"type": "text", "text": _build_user_text(request)}]

    if request.image_bytes:
        bloques.append(
            {
                "type": "image",
                "data": base64.b64encode(request.image_bytes).decode("ascii"),
                "mime_type": request.image_mime_type or "image/jpeg",
            }
        )
    return bloques


def _build_user_text(request: ClassificationRequest) -> str:
    """Datos del caso, con la descripción delimitada como contenido no confiable.

    Los catálogos van como JSON para que el conjunto permitido sea inequívoco y no
    dependa de cómo el modelo interprete una lista escrita en prosa.
    """
    return (
        "description: <<<UNTRUSTED_USER_CONTENT\n"
        f"{request.description}\n"
        "UNTRUSTED_USER_CONTENT\n\n"
        f"current_category_slug: {request.current_category_slug}\n"
        f"current_institution_slug: {request.current_institution_slug}\n"
        f"allowed_category_slugs: {json.dumps(list(request.allowed_category_slugs))}\n"
        f"allowed_institution_slugs: {json.dumps(list(request.allowed_institution_slugs))}\n"
        f"allowed_priorities: {json.dumps(list(request.allowed_priorities))}\n"
        f"image: {'adjunta' if request.image_bytes else 'no disponible'}\n"
    )


__all__ = [
    "PROMPT_VERSION",
    "GeminiAiClassificationAdapter",
    "build_classification_schema",
    "build_client",
]
