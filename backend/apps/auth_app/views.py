"""Endpoints de autenticación (Sprint 3).

Exactamente tres, los que fija el plan. **No existe `/auth/logout/`**: el Sprint 3 no
implementa revocación server-side, así que el cierre de sesión consiste en que el BFF
de Next.js borre la cookie del refresh y el cliente olvide el access de memoria.

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
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from apps.auth_app.serializers import (
    GoogleLoginSerializer,
    TokenRefreshInputSerializer,
    UserSessionSerializer,
)
from apps.auth_app.services import accounts as accounts_service
from apps.auth_app.services import google as google_service
from apps.core.permissions import IsAuthenticated

logger = logging.getLogger(__name__)

User = get_user_model()

CREDENCIALES_INVALIDAS = "No se pudo iniciar sesión con esas credenciales."
CONFLICTO_DE_CUENTA = (
    "No es posible vincular esta cuenta de Google automáticamente. " "Contactá al administrador."
)
GOOGLE_NO_DISPONIBLE = "No se pudo verificar la identidad con Google. Intentá de nuevo."


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
