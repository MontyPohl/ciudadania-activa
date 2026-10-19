"""Adaptador de OpenAI mediante la Responses API (ADR-0016).

Igual que el de Gemini, es un punto que conoce al proveedor (ADR-0008): recibe el
contrato permitido, devuelve texto crudo y no parsea, no persiste, no toca el ORM
y no conoce Celery.

**Structured output no sustituye al parser.** Se pide un JSON Schema en modo
`strict`, que reduce la probabilidad de una respuesta malformada, pero la frontera
de seguridad sigue siendo `parse_classification_response` (contrato S8-A, D13).

**No reutiliza código de `gemini.py`.** Ese módulo forma parte del código que
archivan las corridas prerregistradas de S8-E2 y EX-09, y no se toca. El esquema y
el texto de entrada se replican aquí, y una prueba de paridad exige que sigan
siendo idénticos a los de Gemini: el cambio de proveedor no puede cambiar de paso
lo que se le pregunta al modelo.

**Los errores se clasifican con la jerarquía del propio SDK.** A diferencia de la
Interactions API, el SDK de OpenAI tiene una sola jerarquía: `APIConnectionError`
(del que hereda `APITimeoutError`) para el transporte y `APIStatusError`, con el
código HTTP en `status_code`, para las respuestas de error.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass

from openai import APIConnectionError, APIStatusError, OpenAI

from apps.core.enums import ReportPriority

from ..contracts import ClassificationRequest
from ..exceptions import PermanentAiProviderError, TransientAiProviderError
from ..prompts import PROMPT_VERSION, render_classification_prompt

# Sin reintentos internos: el SDK reintentaría dos veces por defecto y se sumaría a
# los reintentos de Celery, multiplicando las llamadas de forma invisible.
_SDK_MAX_RETRIES = 0

# Nombre del formato de salida en la petición: `[a-zA-Z0-9_-]`, máximo 64.
_NOMBRE_DEL_ESQUEMA = "classification_v1"

_ESTADO_COMPLETADO = "completed"

# Mismo criterio que el adaptador de Gemini: 408 y 429 se reintentan, igual que
# cualquier 5xx.
_CODIGOS_TRANSITORIOS = frozenset({408, 429})

# Un 429 con este código no es un límite de ritmo sino la cuenta sin crédito:
# esperar no lo arregla y reintentarlo solo retrasa que alguien lo vea.
_CODIGO_SIN_CUOTA = "insufficient_quota"

# Resolución con la que el modelo analiza la imagen. `auto` deja al proveedor
# elegir según el tamaño; la pipeline ya normaliza la imagen antes de enviarla.
_DETALLE_DE_IMAGEN = "auto"


def build_classification_schema() -> dict:
    """Esquema JSON del contrato v1 (D3), cerrado a los seis campos exactos.

    Idéntico al de `gemini.build_classification_schema`, y la prueba de paridad lo
    exige. Cumple las condiciones del modo `strict` de OpenAI: todos los campos en
    `required` y `additionalProperties: false`. `justification` no lleva
    `maxLength`: el límite lo impone el parser, que tiene la última palabra.
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


def build_client(*, api_key: str, timeout_seconds: int, http_client=None) -> OpenAI:
    """Crea el cliente con el timeout contractual y sin reintentos internos.

    El SDK expresa `timeout` en **segundos**, a diferencia del de Gemini, que usa
    milisegundos. Un `float` se aplica por fase (conexión, escritura, lectura y
    pool), no como plazo total de la petición. La clave se usa aquí y no se
    conserva en ningún atributo propio.

    `http_client` existe sólo para que las pruebas ejerciten el SDK real contra un
    transporte en memoria, sin red.
    """
    return OpenAI(
        api_key=api_key,
        timeout=float(timeout_seconds),
        max_retries=_SDK_MAX_RETRIES,
        http_client=http_client,
    )


@dataclass(frozen=True, slots=True)
class ProviderCall:
    """Texto devuelto y metadatos de correlación de una respuesta completada.

    Ninguno es secreto: `response_id` es el `id` de la respuesta, `request_id` la
    cabecera `x-request-id` y `model` el modelo que el proveedor declara haber usado.
    """

    text: str
    response_id: str | None
    request_id: str | None
    model: str | None


class OpenAiClassificationAdapter:
    """Implementa `AiClassificationAdapter` contra la Responses API."""

    def __init__(self, *, client: OpenAI, model: str, reasoning_effort: str = ""):
        # El cliente entra ya construido: así las pruebas inyectan un doble sin
        # que exista ninguna ruta que cree un cliente real por descuido.
        self._client = client
        self._model = model
        self._reasoning_effort = reasoning_effort

    def __repr__(self) -> str:
        # Explícito para que ni el modelo ni nada del cliente acaben en un traceback.
        return f"{type(self).__name__}(model={self._model!r})"

    def classify(self, request: ClassificationRequest) -> str:
        return self.classify_with_metadata(request).text

    def classify_with_metadata(self, request: ClassificationRequest) -> ProviderCall:
        """Como `classify`, pero conserva la correlación que exige la evidencia de EX-09."""
        response = self._create_response(request)
        self._check_status(response)
        return ProviderCall(
            text=self._extract_text(response),
            response_id=_texto_o_none(getattr(response, "id", None)),
            request_id=_texto_o_none(getattr(response, "_request_id", None)),
            model=_texto_o_none(getattr(response, "model", None)),
        )

    def _create_response(self, request: ClassificationRequest):
        parametros = {
            "model": self._model,
            "instructions": render_classification_prompt(request),
            "input": [{"role": "user", "content": _build_input(request)}],
            # No se pide al proveedor que conserve la respuesta: la descripción del
            # ciudadano no tiene por qué quedarse allí.
            "store": False,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": _NOMBRE_DEL_ESQUEMA,
                    "schema": build_classification_schema(),
                    "strict": True,
                }
            },
        }
        if self._reasoning_effort:
            # Vacío significa «lo que el modelo use por defecto»: se omite el
            # parámetro en lugar de enviar un valor inventado.
            parametros["reasoning"] = {"effort": self._reasoning_effort}

        try:
            return self._client.responses.create(**parametros)
        except Exception as exc:
            raise _traducir_error(exc) from exc

    def _check_status(self, response) -> None:
        status = getattr(response, "status", None)
        if status is None or status == _ESTADO_COMPLETADO:
            return
        # `incomplete` (tope de tokens o filtro de contenido), `failed` o
        # `cancelled`: repetir la misma petición no cambia el resultado.
        raise PermanentAiProviderError(f"La respuesta de IA terminó en estado {status}.")

    def _extract_text(self, response) -> str:
        # `output_text` concatena los bloques de texto de la salida. Una negativa del
        # modelo llega como bloque `refusal`, no como texto, y cae aquí como vacío.
        output_text = getattr(response, "output_text", None)
        if not isinstance(output_text, str) or not output_text.strip():
            raise PermanentAiProviderError("La respuesta de IA no devolvió texto.")
        return output_text


def _traducir_error(exc: BaseException) -> BaseException:
    """Clasifica el fallo del proveedor en transitorio, permanente o sin clasificar.

    Se decide por la clase del SDK y el código HTTP estructurado, nunca por el texto
    del mensaje.
    """
    if isinstance(exc, APIConnectionError):
        # Incluye `APITimeoutError`: el mismo intento más tarde puede ir bien.
        return TransientAiProviderError("El proveedor de IA no respondió a tiempo.")

    if not isinstance(exc, APIStatusError):
        # Un error ajeno al SDK no se convierte en transitorio ni en permanente:
        # inventarle una categoría llevaría a reintentar a ciegas o a descartar algo
        # recuperable. Se devuelve tal cual y la orquestación lo trata como fallo no
        # reintentable, dejando el reporte en `CREADO`.
        return exc

    status = exc.status_code
    if status == 429 and getattr(exc, "code", None) == _CODIGO_SIN_CUOTA:
        return PermanentAiProviderError(
            "El proveedor de IA rechazó la petición (429: la cuenta no tiene crédito)."
        )
    if status in _CODIGOS_TRANSITORIOS or 500 <= status <= 599:
        return TransientAiProviderError(f"El proveedor de IA respondió {status}.")
    return PermanentAiProviderError(f"El proveedor de IA rechazó la petición ({status}).")


def _texto_o_none(value) -> str | None:
    return value if isinstance(value, str) and value else None


def _build_input(request: ClassificationRequest) -> list[dict]:
    """Bloques del turno del usuario: texto siempre, imagen solo si existe.

    La imagen viaja **inline** como data URL en base64. Nunca por URL pública, URL
    firmada ni Files API: eso obligaría a exponer el objeto privado o a subirlo a un
    almacenamiento del proveedor (D11).
    """
    bloques: list[dict] = [{"type": "input_text", "text": _build_user_text(request)}]

    if request.image_bytes:
        mime_type = request.image_mime_type or "image/jpeg"
        datos = base64.b64encode(request.image_bytes).decode("ascii")
        bloques.append(
            {
                "type": "input_image",
                "image_url": f"data:{mime_type};base64,{datos}",
                "detail": _DETALLE_DE_IMAGEN,
            }
        )
    return bloques


def _build_user_text(request: ClassificationRequest) -> str:
    """Datos del caso, con la descripción delimitada como contenido no confiable.

    Idéntico al texto que envía el adaptador de Gemini (prueba de paridad).
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
    "OpenAiClassificationAdapter",
    "ProviderCall",
    "build_classification_schema",
    "build_client",
]
