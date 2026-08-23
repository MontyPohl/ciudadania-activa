"""Panel administrativo de verificación de identidad (Sprint 4).

Tres endpoints, todos exclusivos de `IsAdmin` —el rol de dominio, no `is_staff` ni
`is_superuser` (ADR-0005)—: la cola de revisión, el detalle con la selfie firmada y la
decisión.

El reparto sigue el §8 del plan: `auth_app` es dueño del lado ciudadano de la identidad
y `admin_app` de su revisión, junto con los catálogos y la auditoría.

Las vistas no contienen reglas de negocio: componen la consulta, delegan en el servicio
y traducen sus excepciones a códigos HTTP.
"""

import logging

from django.conf import settings
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.admin_app.serializers import (
    IdentityDetailSerializer,
    IdentityQueueItemSerializer,
    IdentityReviewInputSerializer,
    IdentityReviewResultSerializer,
)
from apps.admin_app.services import identity_review
from apps.auth_app.enums import VerificationStatus
from apps.auth_app.models import CitizenProfile
from apps.auth_app.services import identity as identity_service
from apps.core.permissions import IsAdmin
from apps.core.services import storage

logger = logging.getLogger(__name__)

#: Respuesta única cuando Storage no está configurado o no responde. No distingue entre
#: ambos casos ni nombra ninguna variable.
SELFIE_NO_DISPONIBLE = "No se pudo obtener la imagen de verificación. Intentá más tarde."
VERIFICACION_INEXISTENTE = "No existe esa verificación."

#: Mismo texto y misma opacidad que el 503 del envío ciudadano: sin `DOCUMENT_HMAC_SECRET`
#: no se puede comprobar la cédula, así que no se aprueba. No se nombra la variable que
#: falta, para no confirmarle nada a quien sondee el endpoint.
REVISION_NO_DISPONIBLE = (
    "La verificación de identidad no está disponible en este momento. Intentá más tarde."
)


def _cola_base():
    """Perfiles con su usuario, en orden estable.

    `created_at` desempatado por `id`: dos envíos en el mismo instante no pueden
    alternar de posición entre páginas, que es como se pierde o se repite una fila al
    paginar.
    """
    return CitizenProfile.objects.select_related("user").order_by("created_at", "id")


def _a_fila(perfil) -> dict:
    return {
        "id": perfil.pk,
        "user": {
            "id": perfil.user_id,
            "email": perfil.user.email,
            "full_name": perfil.user.full_name,
        },
        "created_at": perfil.created_at,
        "verification_status": perfil.user.verification_status,
    }


class IdentityQueueView(ListAPIView):
    """Cola de verificaciones. Por defecto, las pendientes de revisar.

    La cola se deriva del estado del **usuario** y no necesita ninguna columna propia:
    `citizen_profiles` solo tiene fila si hubo envío, así que el filtro por
    `verification_status` es exactamente la bandeja que el administrador espera.
    """

    permission_classes = [IsAdmin]
    serializer_class = IdentityQueueItemSerializer

    def get_queryset(self):
        return _cola_base().filter(user__verification_status=self._estado())

    def _estado(self) -> str:
        estado = self.request.query_params.get("status", VerificationStatus.PENDIENTE)
        if estado not in VerificationStatus.values:
            raise ValidationError({"status": "El estado solicitado no existe."})
        return estado

    def list(self, request, *args, **kwargs):
        pagina = self.paginate_queryset(self.get_queryset())
        filas = [_a_fila(perfil) for perfil in pagina]
        return self.get_paginated_response(self.get_serializer(filas, many=True).data)


class IdentityDetailView(APIView):
    """Detalle de una verificación, con la URL firmada de la selfie.

    Es el **único** punto del sistema que firma el objeto privado, y lo hace en el
    momento de abrir el detalle: firmar antes, o en bloque, multiplicaría sin motivo las
    ventanas de acceso a un archivo que muestra a una persona con su documento.
    """

    permission_classes = [IsAdmin]

    def get(self, request, profile_id):
        perfil = _cola_base().filter(pk=profile_id).first()
        if perfil is None:
            return Response({"detail": VERIFICACION_INEXISTENTE}, status=status.HTTP_404_NOT_FOUND)

        try:
            firmada = _firmar_selfie(perfil)
        except storage.StorageError:
            # El cuerpo de la respuesta de Supabase no se propaga: podría traer rutas
            # internas o el nombre del proyecto. El cliente ya lo tradujo y registró.
            return Response(
                {"detail": SELFIE_NO_DISPONIBLE}, status=status.HTTP_503_SERVICE_UNAVAILABLE
            )

        detalle = _a_fila(perfil) | {
            "reviewed_at": perfil.verified_at,
            "rejection_reason": perfil.rejection_reason,
            "verified_by": perfil.verified_by_id,
            "selfie_signed_url": firmada,
        }
        return Response(IdentityDetailSerializer(detalle).data, status=status.HTTP_200_OK)


class IdentityReviewView(APIView):
    """Aprobación o rechazo manual de una verificación (RF-03, RF-30).

    Aprobar exige que el administrador transcriba el número de cédula que ve en la
    selfie: el servicio lo compara contra el `document_hmac` del envío y solo entonces
    aplica la decisión. Ese número **no vuelve en la respuesta, no se persiste y no se
    registra en ningún log**; la vista se limita a pasárselo al servicio.
    """

    permission_classes = [IsAdmin]

    def patch(self, request, profile_id):
        entrada = IdentityReviewInputSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)

        try:
            perfil = identity_review.review(
                profile_id,
                decision=entrada.validated_data["decision"],
                reason=entrada.validated_data.get("reason", ""),
                document_number=entrada.validated_data.get("document_number", ""),
                actor=request.user,
            )
        except CitizenProfile.DoesNotExist:
            return Response({"detail": VERIFICACION_INEXISTENTE}, status=status.HTTP_404_NOT_FOUND)
        except identity_review.AlreadyReviewedError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except identity_review.InvalidReviewError as exc:
            # Cubre también `DocumentMismatchError`, que es una subclase: una cédula que
            # no coincide es un 400, nunca el 409 reservado al estado de la verificación.
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except identity_service.IdentityConfigurationError:
            # Fallo cerrado, el mismo del envío ciudadano: sin clave no se compara y no
            # se aprueba. No se distingue de otras faltas de configuración.
            return Response(
                {"detail": REVISION_NO_DISPONIBLE}, status=status.HTTP_503_SERVICE_UNAVAILABLE
            )

        resultado = {
            "id": perfil.pk,
            "verification_status": perfil.user.verification_status,
            "reviewed_at": perfil.verified_at,
            "verified_by": perfil.verified_by_id,
        }
        return Response(IdentityReviewResultSerializer(resultado).data, status=status.HTTP_200_OK)


def _firmar_selfie(perfil) -> str | None:
    """URL firmada de vida corta, o `None` si el perfil no tiene archivo asociado.

    Un perfil sin clave solo puede venir de una fila creada a mano: el envío siempre la
    escribe. Se devuelve `null` en lugar de pedirle a Storage que firme una clave vacía.
    """
    if not perfil.verification_selfie_url:
        return None

    return storage.create_signed_url(
        storage.private_bucket(),
        perfil.verification_selfie_url,
        expires_in=settings.IDENTITY_SIGNED_URL_TTL,
    )
