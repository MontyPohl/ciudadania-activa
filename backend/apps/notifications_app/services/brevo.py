"""Cliente del correo transaccional de Brevo por API HTTPS (Sprint 4, ADR-0015).

Responsabilidad única: entregar **un** mensaje de texto plano al endpoint
`POST /v3/smtp/email`. No conoce el dominio: no sabe qué es una verificación de
identidad, no elige plantillas y no toca la base de datos. Quien lo llama compone el
asunto y el cuerpo.

**Por qué HTTPS y no SMTP.** El plan Free de Railway no ofrece SMTP saliente. La
conexión no se rechaza: se queda colgada hasta agotar el timeout, que es lo que produjo
el `TimeoutError` tras 135 s del E2E productivo del 23/08/2026. Esta API sale por el
puerto 443, igual que las llamadas a Supabase Storage o a Google, y por tanto funciona
en el mismo plan sin cambiar de proveedor de correo ni de alojamiento.

Se usa `requests` directamente, sin el SDK de Brevo: una sola llamada HTTP no justifica
incorporar su árbol de dependencias, y así el tratamiento de los errores queda bajo
control del proyecto. Es el mismo criterio ya aplicado en `core.services.storage`.

Tres reglas de higiene que este módulo cumple sin excepción:

- **La `BREVO_API_KEY` nunca sale de aquí.** No se registra, no se devuelve y no aparece
  en ningún mensaje de error. Vive únicamente en la cabecera de la petición.
- **El cuerpo de las respuestas de Brevo no se propaga.** Puede repetir la dirección del
  destinatario o detalles de la cuenta. De un fallo se conserva, como mucho, el código
  de estado.
- **No se registra el destinatario.** El identificador de la notificación, que sí lleva
  quien llama a este módulo, basta para investigar el caso en el admin.
"""

from __future__ import annotations

import logging
from email.utils import parseaddr

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

#: Timeout explícito `(conexión, lectura)`. Corre en el worker de Celery **con la fila
#: de la notificación bloqueada**, así que una espera indefinida bloquearía la fila
#: indefinidamente. Los 15 s de lectura son holgados para un endpoint que solo encola.
_TIMEOUT = (5, 15)


class BrevoError(Exception):
    """Fallo del correo transaccional. Base de las demás."""


class BrevoConfigurationError(BrevoError):
    """Falta configuración de correo en este servidor.

    Es el fallo cerrado: sin clave de API, sin endpoint o sin remitente no se intenta
    ninguna llamada. El mensaje no revela qué ajuste falta ni ningún valor.
    """


class BrevoUnavailableError(BrevoError):
    """Brevo no respondió, o respondió con un error.

    `status_code` es el código HTTP de la respuesta, o `None` si no hubo respuesta (red o
    timeout). `dispatch` lo usa para distinguir un fallo transitorio de uno permanente.
    """

    def __init__(self, message: str = "", *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def send_email(*, to: str, subject: str, text_content: str) -> None:
    """Entrega un correo de texto plano. Devuelve `None` en éxito; levanta en fallo.

    Éxito es **solo** una respuesta 2xx: Brevo acepta el envío con `201` y un
    `messageId`. Cualquier otro código —incluido un `3xx`— se trata como fallo, porque
    esta API no redirige y una redirección indicaría que la petición no llegó donde
    debía.

    No hay reintentos aquí: los decide `dispatch` (Sprint 13), que reprograma los fallos
    transitorios mientras la notificación sigue PENDIENTE.
    """
    url, cabeceras = _configuracion()
    cuerpo = {
        "sender": _remitente(),
        "to": [{"email": to}],
        "subject": subject,
        "textContent": text_content,
    }

    try:
        respuesta = requests.post(url, headers=cabeceras, json=cuerpo, timeout=_TIMEOUT)
    except requests.RequestException as exc:
        # Ni la URL ni el destinatario: solo el tipo de fallo de red.
        logger.error("Brevo inalcanzable al entregar un correo (%s).", type(exc).__name__)
        raise BrevoUnavailableError("El servicio de correo no está disponible.") from exc

    estado = respuesta.status_code
    respuesta.close()

    if not 200 <= estado < 300:
        logger.error("Brevo respondió %s al entregar un correo.", estado)
        raise BrevoUnavailableError(
            f"El servicio de correo respondió con el estado {estado}.", status_code=estado
        )


# --------------------------------------------------------------------------- #
# Internos
# --------------------------------------------------------------------------- #


def _configuracion() -> tuple[str, dict[str, str]]:
    """Endpoint y cabeceras de la llamada, o fallo cerrado si falta configuración.

    `content-type` se fija de forma explícita aunque `requests` lo deduciría de `json=`:
    la cabecera forma parte del contrato de la API y conviene leerla en el código, no
    deducirla de un efecto de la librería. Al ser las cabeceras insensibles a
    mayúsculas, `requests` no la duplica.
    """
    api_key = getattr(settings, "BREVO_API_KEY", "")
    url = getattr(settings, "BREVO_API_URL", "")

    if not api_key or not url:
        raise BrevoConfigurationError("El correo no está configurado en este servidor.")

    cabeceras = {
        "accept": "application/json",
        "api-key": api_key,
        "content-type": "application/json",
    }
    return url, cabeceras


def _remitente() -> dict[str, str]:
    """Remitente derivado de `DEFAULT_FROM_EMAIL`, en el formato que espera Brevo.

    `parseaddr` es la única separación de `Nombre <direccion>` del proyecto: escribir un
    `split("<")` a mano fallaría con un nombre entrecomillado. Sin nombre se envía solo
    la dirección, porque `{"name": ""}` no es lo mismo que omitir la clave.
    """
    nombre, direccion = parseaddr(getattr(settings, "DEFAULT_FROM_EMAIL", ""))

    if not direccion:
        raise BrevoConfigurationError("El correo no tiene remitente configurado.")

    return {"name": nombre, "email": direccion} if nombre else {"email": direccion}
