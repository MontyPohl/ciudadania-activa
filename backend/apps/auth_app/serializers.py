"""Serializadores de autenticación (Sprint 3).

Regla transversal: los campos se declaran **explícitamente**. Nunca `__all__` ni
`exclude`, para que añadir una columna sensible al modelo no la publique por
descuido. `CitizenProfile` no se serializa en ningún caso.
"""

from rest_framework import serializers

from apps.auth_app.enums import UserRole, VerificationStatus


class GoogleLoginSerializer(serializers.Serializer):
    """Entrada del login. Lo único aceptable es el ID token de Google.

    Cualquier otro campo que llegue en el cuerpo se ignora: `email`, `full_name`,
    `role` y `verification_status` los decide el servidor.
    """

    token_google = serializers.CharField(write_only=True, allow_blank=False, trim_whitespace=True)


class TokenRefreshInputSerializer(serializers.Serializer):
    """Entrada de la renovación. El consumidor es el BFF de Next.js, no el navegador."""

    refresh = serializers.CharField(write_only=True, allow_blank=False, trim_whitespace=True)


class InstitutionMembershipSerializer(serializers.Serializer):
    """Institución en la que el usuario es operador activo."""

    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    slug = serializers.SlugField(read_only=True)


class UserSessionSerializer(serializers.Serializer):
    """Representación del usuario autenticado para `/auth/me/` y para el login.

    Deliberadamente ausentes: `password`, `google_oauth_id`, `is_staff`,
    `is_superuser`, `groups`, `user_permissions`, cualquier token y todo dato de
    `CitizenProfile` (`document_hmac`, `verification_selfie_url`).
    """

    id = serializers.UUIDField(read_only=True)
    email = serializers.EmailField(read_only=True)
    full_name = serializers.CharField(read_only=True)
    role = serializers.CharField(read_only=True)
    verification_status = serializers.CharField(read_only=True)
    is_verified_citizen = serializers.SerializerMethodField()
    institutions = serializers.SerializerMethodField()

    def get_is_verified_citizen(self, user) -> bool:
        """Derivado, para que el cliente no reimplemente la regla."""
        return (
            user.role == UserRole.CITIZEN
            and user.verification_status == VerificationStatus.APROBADO
        )

    def get_institutions(self, user) -> list[dict]:
        """Instituciones con vínculo **activo**; lista vacía para roles no operadores.

        Importación diferida: `auth_app` no importa `admin_app` en tiempo de módulo
        para no invertir el grafo de dependencias del §9 del plan.
        """
        if user.role != UserRole.OPERATOR:
            return []

        from apps.admin_app.models import InstitutionOperator

        vinculos = (
            InstitutionOperator.objects.filter(user=user, active=True)
            .select_related("institution")
            .order_by("institution__name")
        )
        return InstitutionMembershipSerializer(
            [vinculo.institution for vinculo in vinculos], many=True
        ).data
