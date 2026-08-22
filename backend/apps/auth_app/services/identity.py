"""Verificación de identidad ciudadana: normalización, HMAC y validación de la selfie.

Contiene las piezas puras —normalización, HMAC y validación del archivo— y el servicio
de dominio del lado ciudadano, `submit_verification`, que las orquesta junto con Storage
y la base de datos. No habla HTTP: las vistas traducen sus excepciones a códigos.

La revisión administrativa llega en la fase siguiente; su contrato de concurrencia queda
anotado al final del archivo para que la implementación no lo reinvente.

Privacidad, con la precisión que corresponde (RF-02, §29 del plan):

- El **número de cédula recibido como dato textual** nunca se persiste en claro.
  Existe como variable local durante la petición y se guarda únicamente como
  HMAC-SHA-256. No entra en la base, ni en logs, ni en `audit_logs`, ni en ninguna
  respuesta, ni en los mensajes de error que levanta este módulo.
- La **selfie es otra cosa**. Muestra al ciudadano *sosteniendo su cédula*, así que
  puede contener visualmente ese mismo número. Que el número textual no se guarde en
  claro no la vuelve un archivo inocuo: es información sensible, vivirá solo en un
  bucket privado y solo un administrador podrá verla, mediante URL firmada efímera.

Sobre la clave del HMAC: **cada entorno tiene la suya**. Desarrollo usa la suya, la
suite de pruebas una ficticia y producción la real, que no se comparte con desarrollo
ni con CI. Lo que debe permanecer estable es la clave *de producción*, una vez exista
la primera identidad verificada real: cambiarla invalida todos los `document_hmac` ya
calculados y no hay manera de recalcularlos, porque el número en claro no se guarda en
ninguna parte. Ver ADR-0006.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import uuid
import warnings
from dataclasses import dataclass
from functools import partial

from django.conf import settings
from django.db import IntegrityError, transaction
from PIL import Image, UnidentifiedImageError

from apps.auth_app.enums import UserRole, VerificationStatus
from apps.auth_app.models import CitizenProfile
from apps.core.services import audit, storage

logger = logging.getLogger(__name__)

#: Acciones de auditoría del envío ciudadano (§30 del plan).
ACCION_ENVIO = "identity.submitted"
ACCION_REENVIO = "identity.resubmitted"

#: Prefijo de las claves de objeto en el bucket privado.
PREFIJO_OBJETO = "verifications"

# --------------------------------------------------------------------------- #
# Excepciones de dominio
# --------------------------------------------------------------------------- #


class IdentityConfigurationError(Exception):
    """Falta configuración del servidor para operar. Se traduce a 503 genérico.

    Nunca lleva el valor de ningún secreto ni el nombre de su contenido: solo indica
    qué ajuste falta.
    """


class InvalidDocumentError(Exception):
    """El número de cédula recibido no es utilizable. Se traduce a 400.

    El mensaje **jamás** incluye el valor recibido: repetirlo en un error acabaría en
    los logs de la aplicación o en la respuesta HTTP, que es exactamente lo que RF-02
    prohíbe.
    """


class InvalidSelfieError(Exception):
    """El archivo recibido no es una selfie utilizable. Se traduce a 400."""


class NotACitizenError(Exception):
    """Quien envía la verificación no tiene rol de ciudadano. Se traduce a 403.

    El permiso HTTP ya lo garantiza, pero el servicio no delega en la vista: es una
    precondición del dominio y debe sostenerse aunque se le llame desde una consola,
    un comando de gestión o una prueba.
    """


class VerificationConflictError(Exception):
    """El estado actual no admite un envío. Se traduce a 409.

    Cubre los tres casos: ya hay una verificación pendiente, la identidad ya está
    aprobada, y la carrera en la que otra petición se adelantó.
    """


class DuplicateDocumentError(VerificationConflictError):
    """Esa cédula ya está registrada por alguien. Se traduce a 409 **genérico**.

    El mensaje no revela de quién es: confirmar la existencia de una cédula concreta
    convertiría el endpoint en un oráculo (§14 del plan).
    """


#: Mensaje único del conflicto de cédula. No menciona al titular ni sugiere que exista.
MENSAJE_DUPLICADO = "No se pudo registrar la verificación con los datos enviados."


# --------------------------------------------------------------------------- #
# Normalización del número de cédula
# --------------------------------------------------------------------------- #

#: Alfabeto admitido, deliberadamente estrecho. Se escribe como clase explícita y no
#: con `\d` ni `str.isdigit()`: ambos aceptan dígitos Unicode —`١٢٣`, `１２３`— que
#: normalizarían a un número distinto del que la persona cree haber escrito.
_CARACTERES_ADMITIDOS = re.compile(r"^[0-9.\- ]+$")

#: Separadores que se eliminan. Son los tres únicos: no se acepta ninguna otra forma
#: de puntuación ni de espacio (tabulador, espacio duro, guion largo…).
_SEPARADORES = re.compile(r"[.\- ]")


def normalize_document(raw: str) -> str:
    """Devuelve la forma canónica del número de cédula.

    Acepta únicamente dígitos ASCII y los separadores `.`, `-` y espacio, y elimina
    esos separadores. `1.234.567`, `1234567`, `1 234 567` y `1-234-567` producen el
    mismo resultado, y por tanto el mismo HMAC.

    Los **ceros iniciales se preservan**: el resultado es una cadena, nunca un entero.
    Convertirlo a `int` haría que `0001234` y `1234` colapsaran en la misma identidad.

    Levanta `InvalidDocumentError` si la entrada trae letras, barras, signos, dígitos
    no ASCII, o si queda vacía tras quitar los separadores. Nunca «rescata» los
    dígitos de una cadena inválida: `abc123` es un error, no `123`.
    """
    if not isinstance(raw, str):
        raise InvalidDocumentError("El número de cédula debe ser una cadena de texto.")

    candidato = raw.strip()
    if not candidato:
        raise InvalidDocumentError("El número de cédula es obligatorio.")

    if _CARACTERES_ADMITIDOS.match(candidato) is None:
        raise InvalidDocumentError(
            "El número de cédula solo puede contener dígitos, puntos, guiones y espacios."
        )

    normalizado = _SEPARADORES.sub("", candidato)
    if not normalizado:
        raise InvalidDocumentError("El número de cédula no contiene ningún dígito.")

    return normalizado


# --------------------------------------------------------------------------- #
# HMAC-SHA-256
# --------------------------------------------------------------------------- #


def compute_document_hmac(raw: str) -> str:
    """HMAC-SHA-256 del número de cédula, en hexadecimal minúscula de 64 caracteres.

    Normaliza internamente: quien llame no puede olvidarse de hacerlo y obtener dos
    huellas distintas para la misma persona.

    **Fallo cerrado**: sin `DOCUMENT_HMAC_SECRET` configurado no se normaliza ni se
    calcula nada. Se levanta `IdentityConfigurationError` antes de tocar la entrada,
    igual que `services.google` se niega a verificar tokens sin `audience`.
    """
    secreto = getattr(settings, "DOCUMENT_HMAC_SECRET", "")
    if not secreto:
        raise IdentityConfigurationError(
            "La verificación de identidad no está configurada en este servidor."
        )

    normalizado = normalize_document(raw)

    return hmac.new(
        key=secreto.encode("utf-8"),
        msg=normalizado.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()


def documents_match(raw: str, stored_hmac: str) -> bool:
    """¿El número recibido corresponde al HMAC almacenado?

    Comparación en tiempo constante con `hmac.compare_digest`: comparar con `==`
    filtraría información por el tiempo de respuesta.
    """
    return hmac.compare_digest(compute_document_hmac(raw), stored_hmac)


# --------------------------------------------------------------------------- #
# Validación de la selfie
# --------------------------------------------------------------------------- #

#: Extensiones admitidas en el **pre-filtro** barato sobre el nombre recibido.
EXTENSIONES_ADMITIDAS = frozenset({".jpg", ".jpeg", ".png"})

#: Formatos reales admitidos, tal como los reporta Pillow.
_FORMATOS_ADMITIDOS = {
    "JPEG": (".jpg", "image/jpeg"),
    "PNG": (".png", "image/png"),
}


@dataclass(frozen=True)
class SelfieValidada:
    """Resultado de la validación. Todo se deriva del **contenido real**.

    Ni `extension` ni `content_type` provienen del nombre ni del `Content-Type` que
    envía el cliente: los determina el formato que Pillow detecta en los bytes.
    """

    #: Formato reportado por Pillow: `JPEG` o `PNG`.
    format: str
    #: Extensión canónica del formato detectado, para construir la clave del objeto.
    extension: str
    #: MIME canónico del formato detectado, para subir a Storage.
    content_type: str


def validate_selfie(archivo) -> SelfieValidada:
    """Valida la selfie y devuelve su formato real, extensión y MIME.

    Orden de las comprobaciones, de la más barata a la más cara:

    1. tamaño, sobre `UploadedFile.size`;
    2. extensión declarada, como pre-filtro;
    3. `Image.open` y lectura del formato;
    4. `Image.verify()`, que recorre la estructura del archivo;
    5. el formato detectado debe ser JPEG o PNG;
    6. la extensión y el MIME se derivan de ese formato.

    Los pasos 2 y 3-5 no son redundantes: el paso 2 descarta basura evidente sin
    trabajo, pero **la autoridad es el contenido**. Un `.jpg` cuyos bytes son PNG se
    acepta como PNG, y su extensión canónica pasa a ser `.png`. No se exige que la
    extensión declarada coincida con el formato detectado: solo rechazaría archivos
    legítimos mal nombrados, sin ganar nada, porque la clave del objeto no hereda
    jamás el nombre recibido.

    El nombre del cliente se usa **exclusivamente** para mirar su extensión. Nunca
    para construir rutas, nombres de objeto ni ninguna otra cosa.

    Al terminar, el archivo queda **reposicionado en el origen** y listo para leerse.
    """
    _validar_tamano(archivo)
    _validar_extension_declarada(archivo)
    formato = _detectar_formato(archivo)

    if formato not in _FORMATOS_ADMITIDOS:
        raise InvalidSelfieError("La imagen debe estar en formato JPEG o PNG.")

    extension, content_type = _FORMATOS_ADMITIDOS[formato]
    return SelfieValidada(format=formato, extension=extension, content_type=content_type)


def _validar_tamano(archivo) -> None:
    maximo = settings.IDENTITY_SELFIE_MAX_BYTES
    tamano = getattr(archivo, "size", None)

    if tamano is None:
        raise InvalidSelfieError("No se pudo determinar el tamaño del archivo.")

    if tamano > maximo:
        megas = maximo / (1024 * 1024)
        raise InvalidSelfieError(f"La imagen supera el tamaño máximo de {megas:.0f} MB.")


def _validar_extension_declarada(archivo) -> None:
    """Pre-filtro sobre el nombre recibido. Único uso legítimo de ese nombre.

    Se toma solo el sufijo posterior al último punto del **último segmento** del
    nombre, de modo que un nombre con separadores de ruta no puede colar nada: lo que
    interesa es la extensión, y el nombre no se conserva en ninguna parte.
    """
    nombre = (getattr(archivo, "name", "") or "").strip()
    ultimo_segmento = nombre.replace("\\", "/").rsplit("/", 1)[-1]
    punto = ultimo_segmento.rfind(".")
    extension = ultimo_segmento[punto:].lower() if punto > 0 else ""

    if extension not in EXTENSIONES_ADMITIDAS:
        raise InvalidSelfieError("La imagen debe tener extensión .jpg, .jpeg o .png.")


def _detectar_formato(archivo) -> str:
    """Abre la imagen, lee su formato y verifica su estructura.

    `Image.verify()` invalida la instancia sobre la que se ejecuta, así que el formato
    se lee **antes**. Después, el archivo se reposiciona en el origen para que el
    llamador pueda leer sus bytes íntegros.

    Una bomba de descompresión se trata como archivo inválido en los dos casos en que
    Pillow la señala: cuando levanta `DecompressionBombError` y cuando se limita a
    emitir `DecompressionBombWarning`, que aquí se escala a error. No se añade ningún
    límite de dimensiones propio: el criterio es el que Pillow considera inseguro.
    """
    archivo.seek(0)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            imagen = Image.open(archivo)
            formato = imagen.format
            imagen.verify()
    except Image.DecompressionBombWarning as exc:
        raise InvalidSelfieError("La imagen no es válida.") from exc
    except Image.DecompressionBombError as exc:
        raise InvalidSelfieError("La imagen no es válida.") from exc
    except UnidentifiedImageError as exc:
        raise InvalidSelfieError("El archivo no es una imagen válida.") from exc
    except (OSError, ValueError, SyntaxError) as exc:
        # Pillow señala los archivos truncados o corruptos con OSError, y algunos
        # decodificadores con SyntaxError o ValueError.
        raise InvalidSelfieError("El archivo no es una imagen válida.") from exc
    finally:
        archivo.seek(0)

    if not formato:
        raise InvalidSelfieError("No se pudo determinar el formato de la imagen.")

    return formato


# --------------------------------------------------------------------------- #
# Servicio de envío ciudadano
# --------------------------------------------------------------------------- #


def build_object_key(user_id, extension: str) -> str:
    """Clave del objeto en el bucket privado: `verifications/{user_id}/{uuid4}.{ext}`.

    La genera **íntegramente el servidor**. El nombre que envía el navegador no
    interviene: ya se usó para el pre-filtro de extensión y ahí termina su papel. El
    UUID evita colisiones entre reenvíos del mismo ciudadano y hace que la clave no sea
    adivinable.
    """
    return f"{PREFIJO_OBJETO}/{user_id}/{uuid.uuid4()}{extension}"


def submit_verification(user, document_number: str, selfie):
    """Registra el envío de verificación de un ciudadano y devuelve su perfil.

    Cubre los dos caminos: el **primer envío**, que crea el `CitizenProfile`, y el
    **reenvío tras un rechazo**, que reutiliza el existente. Cualquier otro estado es
    un conflicto.

    Orden deliberado de las operaciones:

    1. precondiciones de dominio —rol y configuración completa— **antes de tocar nada**;
    2. HMAC y validación del archivo, que son puras y baratas de deshacer;
    3. subida a Storage;
    4. escritura en base de datos, dentro de una transacción.

    La subida va antes de la escritura porque el fallo de Storage es el más probable de
    los dos y así no deja filas a medias. El precio es que un fallo de base de datos
    deja un objeto recién subido: se borra por compensación, y si ese borrado también
    falla, el error original sigue siendo el que se propaga.
    """
    _asegurar_ciudadano(user)
    _asegurar_configuracion()

    perfil = CitizenProfile.objects.filter(user=user).first()
    _asegurar_estado_admite_envio(user, perfil)

    document_hmac = compute_document_hmac(document_number)
    validada = validate_selfie(selfie)

    bucket = storage.private_bucket()
    clave_nueva = build_object_key(user.pk, validada.extension)
    storage.upload(bucket, clave_nueva, selfie.read(), validada.content_type)

    try:
        if perfil is None:
            return _registrar_primer_envio(user, document_hmac, clave_nueva)
        return _registrar_reenvio(perfil, document_hmac, clave_nueva, bucket)
    except Exception:
        # La escritura no prosperó: el objeto recién subido queda huérfano y se borra.
        # El borrado no puede tapar la causa real, así que nunca sustituye la excepción.
        _borrar_sin_propagar(bucket, clave_nueva, "compensación de un envío fallido")
        raise


def _asegurar_ciudadano(user) -> None:
    if getattr(user, "role", None) != UserRole.CITIZEN:
        raise NotACitizenError("Solo un ciudadano puede enviar su verificación de identidad.")


def _asegurar_configuracion() -> None:
    """Exige la configuración completa **antes** de normalizar, leer o subir nada.

    Se comprueban las cuatro variables juntas para que un servidor a medio configurar
    falle en seco y no después de haber subido un archivo. El mensaje es único y no
    dice cuál falta: esa información solo le sirve a quien esté sondeando.
    """
    completa = all(
        (
            getattr(settings, "DOCUMENT_HMAC_SECRET", ""),
            getattr(settings, "SUPABASE_URL", ""),
            getattr(settings, "SUPABASE_SERVICE_ROLE_KEY", ""),
            getattr(settings, "SUPABASE_STORAGE_BUCKET_PRIVATE", ""),
        )
    )
    if not completa:
        raise IdentityConfigurationError(
            "La verificación de identidad no está disponible en este servidor."
        )


def _asegurar_estado_admite_envio(user, perfil) -> None:
    """Comprobación temprana, para no subir un archivo que se va a descartar.

    **No es la comprobación autoritativa** en el reenvío: entre esta lectura y la
    escritura cabe otra transacción, así que `_registrar_reenvio` vuelve a leer el
    estado dentro del lock. Esta solo evita el trabajo inútil en el caso normal.
    """
    estado = user.verification_status

    if perfil is None:
        if estado != VerificationStatus.PENDIENTE:
            # Estado inalcanzable por la API —revisar exige perfil—, así que solo puede
            # venir de una edición manual. No se inventa una recuperación.
            raise VerificationConflictError("El estado de la cuenta no admite un envío.")
        return

    if estado == VerificationStatus.PENDIENTE:
        raise VerificationConflictError("Ya hay una verificación pendiente de revisión.")
    if estado == VerificationStatus.APROBADO:
        raise VerificationConflictError("La identidad ya está verificada.")


def _registrar_primer_envio(user, document_hmac: str, clave: str):
    """Crea el perfil. `verification_status` ya es PENDIENTE y no se reescribe."""
    try:
        with transaction.atomic():
            perfil = CitizenProfile.objects.create(
                user=user,
                document_hmac=document_hmac,
                verification_selfie_url=clave,
                rejection_reason="",
            )
            audit.log(user, ACCION_ENVIO, _payload_auditoria(perfil))
    except IntegrityError as exc:
        raise DuplicateDocumentError(MENSAJE_DUPLICADO) from exc

    return perfil


def _registrar_reenvio(perfil, document_hmac: str, clave_nueva: str, bucket: str):
    """Reutiliza el perfil rechazado y lo devuelve al circuito de revisión.

    El bloqueo se declara de forma **explícita** sobre las dos filas que se van a
    escribir —el perfil y el usuario— en lugar de confiar en lo que `select_related`
    arrastre a la cláusula `FOR UPDATE`. El estado se relee ya con el lock tomado: sin
    eso, dos reenvíos simultáneos podrían pasar los dos la comprobación previa.
    """
    try:
        with transaction.atomic():
            bloqueado = (
                CitizenProfile.objects.select_for_update(of=("self", "user"))
                .select_related("user")
                .get(pk=perfil.pk)
            )

            if bloqueado.user.verification_status != VerificationStatus.RECHAZADO:
                # Otra petición se adelantó, o un administrador acaba de revisar.
                raise VerificationConflictError("El estado de la cuenta no admite un envío.")

            clave_anterior = bloqueado.verification_selfie_url

            bloqueado.document_hmac = document_hmac
            bloqueado.verification_selfie_url = clave_nueva
            bloqueado.verified_at = None
            bloqueado.verified_by = None
            bloqueado.rejection_reason = ""
            bloqueado.save(
                update_fields=[
                    "document_hmac",
                    "verification_selfie_url",
                    "verified_at",
                    "verified_by",
                    "rejection_reason",
                    "updated_at",
                ]
            )

            bloqueado.user.verification_status = VerificationStatus.PENDIENTE
            bloqueado.user.save(update_fields=["verification_status"])

            audit.log(bloqueado.user, ACCION_REENVIO, _payload_auditoria(bloqueado))

            if clave_anterior and clave_anterior != clave_nueva:
                # Solo tras un commit exitoso. Si la transacción se deshace, el objeto
                # anterior sigue siendo el vigente y no debe desaparecer.
                transaction.on_commit(
                    partial(
                        _borrar_sin_propagar,
                        bucket,
                        clave_anterior,
                        "reemplazo tras un reenvío",
                    )
                )
    except IntegrityError as exc:
        raise DuplicateDocumentError(MENSAJE_DUPLICADO) from exc

    return bloqueado


def _payload_auditoria(perfil) -> dict[str, str]:
    """Solo identificadores. Nunca la cédula, el HMAC ni la clave del objeto (§30)."""
    return {"profile_id": str(perfil.pk), "user_id": str(perfil.user_id)}


def _borrar_sin_propagar(bucket: str, clave: str, motivo: str) -> None:
    """Borra un objeto y **se traga el fallo**, dejando un aviso redactado.

    Se usa en los dos borrados que no son la operación principal: la compensación de un
    envío fallido y el reemplazo del archivo anterior tras un reenvío ya confirmado. En
    ninguno de los dos puede el borrado cambiar el resultado de la petición: en el
    primero taparía la causa real del fallo, y en el segundo desharía una verificación
    que ya está registrada. Un objeto huérfano en un bucket privado no es una fuga.
    """
    try:
        storage.delete(bucket, clave)
    except storage.StorageError:
        logger.warning(
            "No se pudo borrar el objeto de %s en %s sobre %s.",
            motivo,
            bucket,
            storage.redact_key(clave),
        )


# --------------------------------------------------------------------------- #
# Contrato pendiente de la fase siguiente
# --------------------------------------------------------------------------- #
#
# REVISIÓN ADMINISTRATIVA
#   Mismo requisito que el reenvío, ya implementado en `_registrar_reenvio`:
#   `transaction.atomic()` + bloqueo explícito de `CitizenProfile` y `User` con
#   `select_for_update(of=("self", "user"))`, relectura del estado dentro del lock y
#   exigencia de PENDIENTE. Con dos administradores simultáneos, el primero obtiene 200
#   y el segundo 409; nunca se escriben dos filas de auditoría de revisión ni dos
#   notificaciones.
