"""Serializadores de autenticación (Sprint 3).

Regla transversal: los campos se declaran **explícitamente**. Nunca `__all__` ni
`exclude`, para que añadir una columna sensible al modelo no la publique por
descuido. `CitizenProfile` no se serializa en ningún caso.
"""

from rest_framework import serializers

from apps.auth_app.enums import RankingVisibility, UserRole, VerificationStatus
from apps.auth_app.services.profile import ALIAS_MAX_LENGTH


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


# --------------------------------------------------------------------------- #
# Verificación de identidad (Sprint 4)
# --------------------------------------------------------------------------- #


class IdentitySubmitSerializer(serializers.Serializer):
    """Entrada del envío de verificación. Ambos campos son de **solo escritura**.

    Aquí solo se comprueba la *forma* de la entrada: que los dos campos lleguen. Las
    reglas de dominio —normalización de la cédula, HMAC, y validación real de la imagen
    con Pillow— viven en `services.identity`, porque las vistas no contienen reglas de
    negocio y porque deben sostenerse aunque se llame al servicio sin pasar por HTTP.

    `selfie` es un `FileField` y **no** un `ImageField` a propósito: `ImageField`
    validaría la imagen por su cuenta y la autoridad sobre el formato tiene que ser
    una sola, la del servicio.

    Ninguno de los dos campos puede aparecer en una respuesta: `write_only` lo impide
    estructuralmente, además de que este serializador nunca se usa para serializar.
    """

    document_number = serializers.CharField(
        write_only=True, allow_blank=False, trim_whitespace=True
    )
    selfie = serializers.FileField(write_only=True)


class IdentityStatusSerializer(serializers.Serializer):
    """Estado de la verificación propia. Cuatro campos, ni uno más.

    Deliberadamente ausentes: `document_hmac`, `verification_selfie_url`, cualquier URL
    firmada, y también `submitted_at` —que no existe como columna— y `updated_at`, que
    cambia al guardar la preferencia de ranking y no significaría lo que aparenta.

    `reviewed_at` es `citizen_profiles.verified_at`: el instante de la **revisión**
    administrativa, se haya aprobado o rechazado.
    """

    has_submission = serializers.BooleanField(read_only=True)
    verification_status = serializers.CharField(read_only=True)
    reviewed_at = serializers.DateTimeField(read_only=True, allow_null=True)
    rejection_reason = serializers.CharField(read_only=True, allow_blank=True)


class RankingVisibilitySerializer(serializers.Serializer):
    """Preferencia de visibilidad en el ranking (RF-07). Entrada **y** salida.

    Se usa para las dos direcciones a propósito: la respuesta de consultar la
    preferencia y la de guardarla tienen exactamente la misma forma, y declararla dos
    veces solo abriría la puerta a que se separen con el tiempo.

    `ranking_alias` es opcional y se recorta. Su ausencia significa «no lo cambies»,
    que es lo que hace que abandonar el modo `ALIAS` conserve el alias guardado.

    No se declara ninguna unicidad: el alias **no** es único a nivel global (ADR-0011).
    """

    ranking_visibility = serializers.ChoiceField(choices=RankingVisibility.choices)
    ranking_alias = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=True,
        max_length=ALIAS_MAX_LENGTH,
    )

    def validate(self, attrs):
        """El modo `ALIAS` exige un alias con contenido **en la propia petición**.

        No se acepta apoyarse en el alias ya guardado: quien declara que quiere
        mostrarse con alias debe decir con cuál, aunque coincida con el anterior. El
        contrato queda así explícito y no depende del estado previo de la fila.
        """
        if attrs["ranking_visibility"] != RankingVisibility.ALIAS:
            return attrs

        if not (attrs.get("ranking_alias") or "").strip():
            raise serializers.ValidationError(
                {"ranking_alias": "El alias es obligatorio en la modalidad Alias."}
            )

        return attrs
