"""Cliente de Supabase Storage (§7 del plan: «core … cliente de Supabase Storage»).

Responsabilidad única: hablar HTTP con la API de Storage. No conoce el dominio, no
sabe qué es una selfie ni una verificación, no decide rutas y no valida contenidos.
Quien lo llama compone la clave del objeto y decide el bucket.

Se usa la API REST directamente con `requests`, sin el SDK `supabase`: las tres
operaciones que el proyecto necesita no justifican incorporar su árbol de
dependencias, y así el comportamiento ante errores queda bajo control del proyecto.

Tres reglas de higiene que este módulo cumple sin excepción:

- **La service-role nunca sale de aquí.** No se registra, no se devuelve y no aparece
  en ningún mensaje de error. Es una credencial con privilegios totales sobre el
  almacenamiento y solo se configura en el backend web, nunca en el worker de Celery
  ni en el frontend.
- **El cuerpo de las respuestas de Supabase no se propaga.** Puede traer rutas
  internas, nombres de proyecto o detalles del almacenamiento. Los errores externos se
  traducen a excepciones propias que llevan, como mucho, el código de estado.
- **Las claves de objeto no se registran completas.** En los logs se reduce cada clave
  a su primer segmento: basta para localizar el problema y no deja rastro de qué
  ciudadano es.
"""

from __future__ import annotations

import logging
from urllib.parse import quote

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

#: Timeouts explícitos `(conexión, lectura)`. Ninguna llamada puede quedarse colgada:
#: este cliente se invoca dentro del ciclo HTTP y una espera indefinida convertiría un
#: problema de Supabase en una petición bloqueada del ciudadano.
_TIMEOUT_CORTO = (5, 10)
_TIMEOUT_SUBIDA = (5, 30)


class StorageError(Exception):
    """Fallo de almacenamiento. Base de las demás; se traduce a 503 genérico."""


class StorageConfigurationError(StorageError):
    """Falta configuración de Storage en este servidor.

    Es el fallo cerrado: sin `SUPABASE_URL`, sin service-role o sin bucket, no se
    intenta ninguna llamada. Se traduce a 503 y no revela qué ajuste falta.
    """


class StorageUnavailableError(StorageError):
    """Supabase no respondió, o respondió con un error. Se traduce a 503."""


def private_bucket() -> str:
    """Nombre del bucket privado de verificación, o fallo cerrado si no está configurado."""
    bucket = getattr(settings, "SUPABASE_STORAGE_BUCKET_PRIVATE", "")
    if not bucket:
        raise StorageConfigurationError("El almacenamiento no está configurado en este servidor.")
    return bucket


def upload(bucket: str, key: str, content: bytes, content_type: str) -> None:
    """Sube un objeto al bucket indicado.

    `x-upsert: false`: subir dos veces la misma clave es un error, no una
    sobrescritura silenciosa. Las claves las genera el servidor con un UUID, de modo
    que una colisión indicaría un defecto y conviene que se note.
    """
    base, cabeceras = _configuracion()
    respuesta = _peticion(
        "POST",
        f"{base}/object/{_ruta(bucket, key)}",
        headers={**cabeceras, "Content-Type": content_type, "x-upsert": "false"},
        data=content,
        timeout=_TIMEOUT_SUBIDA,
        operacion="upload",
        key=key,
    )
    respuesta.close()


def create_signed_url(bucket: str, key: str, expires_in: int) -> str:
    """Devuelve una URL firmada absoluta, válida durante `expires_in` segundos.

    La URL contiene un token de acceso: no se persiste, no se cachea y no se registra
    en ningún log. Quien la reciba puede leer el objeto hasta que expire.
    """
    base, cabeceras = _configuracion()
    respuesta = _peticion(
        "POST",
        f"{base}/object/sign/{_ruta(bucket, key)}",
        headers={**cabeceras, "Content-Type": "application/json"},
        json={"expiresIn": expires_in},
        timeout=_TIMEOUT_CORTO,
        operacion="sign",
        key=key,
    )

    try:
        cuerpo = respuesta.json()
    except ValueError as exc:
        raise StorageUnavailableError("El almacenamiento devolvió una respuesta ilegible.") from exc
    finally:
        respuesta.close()

    # Supabase ha usado ambas grafías según la versión de la API.
    firmada = cuerpo.get("signedURL") or cuerpo.get("signedUrl")
    if not firmada:
        raise StorageUnavailableError("El almacenamiento no devolvió una URL firmada.")

    return f"{base}{firmada}" if firmada.startswith("/") else f"{base}/{firmada}"


def delete(bucket: str, key: str) -> None:
    """Borra un objeto. Operación de compensación: el llamador decide cuándo aplicarla."""
    base, cabeceras = _configuracion()
    respuesta = _peticion(
        "DELETE",
        f"{base}/object/{_ruta(bucket, key)}",
        headers=cabeceras,
        timeout=_TIMEOUT_CORTO,
        operacion="delete",
        key=key,
    )
    respuesta.close()


# --------------------------------------------------------------------------- #
# Internos
# --------------------------------------------------------------------------- #


def _configuracion() -> tuple[str, dict[str, str]]:
    """URL base de la API de Storage y cabeceras de autorización.

    Falla cerrado antes de cualquier llamada si falta configuración.
    """
    url = getattr(settings, "SUPABASE_URL", "")
    service_role = getattr(settings, "SUPABASE_SERVICE_ROLE_KEY", "")

    if not url or not service_role:
        raise StorageConfigurationError("El almacenamiento no está configurado en este servidor.")

    cabeceras = {
        "Authorization": f"Bearer {service_role}",
        "apikey": service_role,
    }
    return f"{url.rstrip('/')}/storage/v1", cabeceras


def _ruta(bucket: str, key: str) -> str:
    """Segmento `{bucket}/{key}` con los caracteres especiales escapados.

    `safe="/"` conserva la jerarquía de la clave y escapa todo lo demás.
    """
    return f"{quote(bucket, safe='')}/{quote(key, safe='/')}"


def redact_key(key: str) -> str:
    """Clave reducida a su primer segmento, para poder registrarla sin identificar a nadie.

    Es pública porque el servicio de identidad registra sus propios avisos sobre claves
    de objeto y debe aplicar exactamente la misma regla de redacción; duplicarla en dos
    módulos garantizaría que con el tiempo dejen de coincidir.
    """
    primero = key.split("/", 1)[0]
    return f"{primero}/…" if "/" in key else "…"


def _peticion(method: str, url: str, *, operacion: str, key: str, **kwargs):
    """Ejecuta la llamada y traduce cualquier fallo a una excepción propia.

    Ni el cuerpo de la respuesta de Supabase ni la clave completa llegan al log o al
    mensaje de la excepción: solo la operación, el código de estado y la clave
    redactada.
    """
    try:
        respuesta = requests.request(method, url, **kwargs)
    except requests.RequestException as exc:
        logger.error(
            "Storage inalcanzable en la operación %s sobre %s.", operacion, redact_key(key)
        )
        raise StorageUnavailableError("El almacenamiento no está disponible.") from exc

    if not respuesta.ok:
        estado = respuesta.status_code
        respuesta.close()
        logger.error(
            "Storage respondió %s en la operación %s sobre %s.",
            estado,
            operacion,
            redact_key(key),
        )
        raise StorageUnavailableError(f"El almacenamiento respondió con el estado {estado}.")

    return respuesta
