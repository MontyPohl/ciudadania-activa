"""Endpoints de autenticación (Sprint 3) e identidad ciudadana (Sprint 4).

De autenticación son exactamente tres, los que fija el plan. **No existe
`/auth/logout/`**: el Sprint 3 no implementa revocación server-side, así que el cierre
de sesión consiste en que el BFF de Next.js borre la cookie del refresh y el cliente
olvide el access de memoria.

Del Sprint 4 se añaden los tres del lado ciudadano —envío de la verificación, consulta
del estado propio y preferencia de visibilidad del ranking—. Los de administración
viven en `admin_app`, según el reparto de responsabilidades del §8 del plan.

Cada vista declara su permiso de forma explícita, porque el permiso por defecto del
proyecto es `DenyAll`.

Las vistas no contienen reglas de negocio: verifican la entrada, delegan en los
servicios de dominio y traducen sus excepciones a códigos HTTP. Los errores de
autenticación son **genéricos** a propósito: distinguir «audience incorrecta» de
«firma inválida» o «cuenta desactivada» solo ayudaría a quien esté probando suerte.
"""

import logging

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from apps.auth_app.models import CitizenProfile
from apps.auth_app.serializers import (
    GoogleLoginSerializer,
    IdentityStatusSerializer,
    IdentitySubmitSerializer,
    RankingVisibilitySerializer,
    TokenRefreshInputSerializer,
    UserSessionSerializer,
)
from apps.auth_app.services import accounts as accounts_service
from apps.auth_app.services import google as google_service
from apps.auth_app.services import identity as identity_service
from apps.auth_app.services import profile as profile_service
from apps.core.permissions import IsAuthenticated, IsCitizen
from apps.core.services import storage

logger = logging.getLogger(__name__)

User = get_user_model()

CREDENCIALES_INVALIDAS = "No se pudo iniciar sesión con esas credenciales."
CONFLICTO_DE_CUENTA = (
    "No es posible vincular esta cuenta de Google automáticamente. " "Contactá al administrador."
)
GOOGLE_NO_DISPONIBLE = "No se pudo verificar la identidad con Google. Intentá de nuevo."

#: Respuesta única cuando falta configuración del servidor o Storage no responde. No
#: distingue entre ambos casos ni nombra ninguna variable: quien sondea el endpoint no
#: debe poder deducir cómo está configurado.
VERIFICACION_NO_DISPONIBLE = (
    "La verificación de identidad no está disponible en este momento. Intentá más tarde."
)
SIN_PERFIL_CIUDADANO = "Todavía no enviaste tu verificación de identidad."


def _issue_tokens(user) -> tuple[str, str]:
    """Emite el par de tokens propios del sistema."""
    refresh = RefreshToken.for_user(user)
    return str(refresh.access_token), str(refresh)


class GoogleLoginView(APIView):
    """Canjea un ID token de Google por la sesión propia del sistema.

    El `refresh` se devuelve en el cuerpo porque el consumidor directo es el Route
    Handler server-side del BFF, que lo guarda en una cookie `HttpOnly`. Nunca llega
    al JavaScript del navegador.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]

    def post(self, request):
        entrada = GoogleLoginSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)

        try:
            identidad = google_service.verify_id_token(entrada.validated_data["token_google"])
        except google_service.GoogleIdentityError:
            return Response({"detail": CREDENCIALES_INVALIDAS}, status=status.HTTP_401_UNAUTHORIZED)
        except google_service.GoogleUnavailableError:
            return Response(
                {"detail": GOOGLE_NO_DISPONIBLE}, status=status.HTTP_503_SERVICE_UNAVAILABLE
            )

        try:
            user = accounts_service.login_with_google(identidad)
        except accounts_service.AccountConflictError:
            return Response({"detail": CONFLICTO_DE_CUENTA}, status=status.HTTP_409_CONFLICT)
        except accounts_service.InactiveAccountError:
            return Response({"detail": CREDENCIALES_INVALIDAS}, status=status.HTTP_401_UNAUTHORIZED)

        access, refresh = _issue_tokens(user)
        return Response(
            {
                "access": access,
                "refresh": refresh,
                "user": UserSessionSerializer(user).data,
            },
            status=status.HTTP_200_OK,
        )


class TokenRefreshView(APIView):
    """Renueva el access token.

    No se usa la vista de SimpleJWT: su serializador valida el refresh sin cargar al
    usuario, de modo que una cuenta desactivada podría seguir renovando su sesión
    indefinidamente. Aquí se carga el usuario y se comprueba `is_active`.

    No se rota el refresh: sin `token_blacklist` la rotación no revocaría el anterior.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]

    def post(self, request):
        entrada = TokenRefreshInputSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)

        try:
            refresh = RefreshToken(entrada.validated_data["refresh"])
        except TokenError:
            return Response({"detail": CREDENCIALES_INVALIDAS}, status=status.HTTP_401_UNAUTHORIZED)

        user = self._usuario_activo(refresh)
        if user is None:
            return Response({"detail": CREDENCIALES_INVALIDAS}, status=status.HTTP_401_UNAUTHORIZED)

        return Response({"access": str(refresh.access_token)}, status=status.HTTP_200_OK)

    @staticmethod
    def _usuario_activo(refresh) -> User | None:
        user_id = refresh.payload.get("user_id")
        if not user_id:
            return None
        try:
            user = User.objects.filter(pk=user_id).first()
        except (ValueError, TypeError, ValidationError):
            # `user_id` con un formato que no es UUID.
            return None
        if user is None or not user.is_active:
            return None
        return user


class MeView(APIView):
    """Perfil del usuario autenticado."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UserSessionSerializer(request.user).data, status=status.HTTP_200_OK)


# --------------------------------------------------------------------------- #
# Verificación de identidad — lado ciudadano (Sprint 4)
# --------------------------------------------------------------------------- #


class IdentitySubmitView(APIView):
    """Envío de la verificación: número de cédula y selfie sosteniéndola (RF-02).

    `IsCitizen` y no `IsVerifiedCitizen`: quien envía su verificación es, por
    definición, alguien que todavía no está verificado.

    El throttle es **el único del proyecto** y está acotado a esta vista. Como el
    endpoint exige autenticación, `ScopedRateThrottle` cuenta por usuario y no por IP.

    La vista no contiene reglas: valida la forma de la entrada, delega en el servicio y
    traduce sus excepciones. Los errores que podrían revelar información —configuración
    ausente, Storage caído, cédula ya registrada— son deliberadamente genéricos.
    """

    permission_classes = [IsCitizen]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "identity_submit"
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        entrada = IdentitySubmitSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)

        try:
            perfil = identity_service.submit_verification(
                user=request.user,
                document_number=entrada.validated_data["document_number"],
                selfie=entrada.validated_data["selfie"],
            )
        except identity_service.NotACitizenError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        except (identity_service.InvalidDocumentError, identity_service.InvalidSelfieError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except identity_service.VerificationConflictError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except (identity_service.IdentityConfigurationError, storage.StorageError):
            return Response(
                {"detail": VERIFICACION_NO_DISPONIBLE},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        # Se lee del perfil devuelto y no de `request.user`: en un reenvío el servicio
        # actualiza la instancia que bloqueó, que no es la que trae la petición.
        return Response(
            {"verification_status": perfil.user.verification_status},
            status=status.HTTP_201_CREATED,
        )


class IdentityStatusView(APIView):
    """Estado de la verificación **propia**.

    No acepta ningún identificador: el sujeto es siempre `request.user`, de modo que no
    hay IDOR posible. Un ciudadano que aún no envió nada recibe 200 con
    `has_submission: false`, no un 404: no haber empezado es un estado legítimo.
    """

    permission_classes = [IsCitizen]

    def get(self, request):
        perfil = CitizenProfile.objects.filter(user=request.user).first()

        estado = {
            "has_submission": perfil is not None,
            "verification_status": request.user.verification_status,
            "reviewed_at": perfil.verified_at if perfil else None,
            "rejection_reason": perfil.rejection_reason if perfil else "",
        }
        return Response(IdentityStatusSerializer(estado).data, status=status.HTTP_200_OK)


class RankingVisibilityView(APIView):
    """Preferencia de visibilidad en el ranking (RF-07): consulta y actualización.

    Requiere un `CitizenProfile`, que nace con el envío de la verificación. Sin él
    ambos métodos responden 404: no hay dónde guardar ni qué leer (ADR-0011).

    El `GET` existe porque el criterio de aceptación del sprint pide que las cuatro
    modalidades «se guarden **y se recuperen** correctamente». Sin lectura, la
    preferencia se pierde de vista en cuanto se recarga la página.

    Los dos métodos devuelven la misma forma y con el mismo serializador: que la
    respuesta de guardar y la de consultar puedan divergir sería una fuente de errores
    gratuita. El sujeto es siempre `request.user`; no hay parámetro por el que pedir la
    preferencia de otra persona, así que no hay IDOR posible.
    """

    permission_classes = [IsCitizen]

    def get(self, request):
        try:
            perfil = profile_service.get_ranking_preferences(request.user)
        except profile_service.ProfileNotFoundError:
            return Response({"detail": SIN_PERFIL_CIUDADANO}, status=status.HTTP_404_NOT_FOUND)

        return Response(RankingVisibilitySerializer(perfil).data, status=status.HTTP_200_OK)

    def patch(self, request):
        entrada = RankingVisibilitySerializer(data=request.data)
        entrada.is_valid(raise_exception=True)

        try:
            perfil = profile_service.update_ranking_visibility(
                user=request.user,
                ranking_visibility=entrada.validated_data["ranking_visibility"],
                ranking_alias=entrada.validated_data.get("ranking_alias"),
            )
        except profile_service.ProfileNotFoundError:
            return Response({"detail": SIN_PERFIL_CIUDADANO}, status=status.HTTP_404_NOT_FOUND)
        except profile_service.InvalidRankingPreferenceError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(RankingVisibilitySerializer(perfil).data, status=status.HTTP_200_OK)
