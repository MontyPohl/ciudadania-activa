"""Serializadores del panel administrativo de identidades (Sprint 4).

Regla transversal del proyecto: los campos se declaran **explícitamente**. Nunca
`__all__` ni `exclude`, para que añadir una columna sensible al modelo no la publique
por descuido.

Lo que estos serializadores **jamás** exponen: `document_hmac`, la clave del objeto en
Storage, el número de cédula, cualquier secreto y cualquier token.

Lo que sí exponen, y por qué: el email y el nombre del ciudadano. Son endpoints
exclusivos de `IsAdmin` y la revisión es humana —hay que saber a quién se está
aprobando—. Eso los hace visibles ahí y en ningún otro sitio: no se registran en logs
ni entran en el payload de auditoría.
"""

from rest_framework import serializers

from apps.auth_app.enums import VerificationStatus


class RevisedUserSerializer(serializers.Serializer):
    """Identificación mínima del ciudadano bajo revisión."""

    id = serializers.UUIDField(read_only=True)
    email = serializers.EmailField(read_only=True)
    full_name = serializers.CharField(read_only=True)


class IdentityQueueItemSerializer(serializers.Serializer):
    """Fila de la cola de revisión.

    `created_at` es el del perfil: sirve para ordenar la cola de forma estable. **No**
    se presenta como «fecha del último reenvío», porque no lo es: un reenvío reutiliza
    el perfil y no cambia su `created_at`, y no existe columna `submitted_at`.

    Sin URL firmada: firmar en la lista gastaría firmas que nadie va a abrir y
    multiplicaría las ventanas de acceso al archivo. Se firma solo en el detalle.
    """

    id = serializers.UUIDField(read_only=True)
    user = RevisedUserSerializer(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    verification_status = serializers.CharField(read_only=True)


class IdentityDetailSerializer(serializers.Serializer):
    """Detalle de una verificación, con la URL firmada de la selfie.

    `selfie_signed_url` es efímera —300 segundos— y **no se persiste, no se cachea, no
    entra en auditoría y no se registra en ningún log**. Es `null` si el perfil no tiene
    archivo asociado, algo que solo puede ocurrir en una fila creada a mano.
    """

    id = serializers.UUIDField(read_only=True)
    user = RevisedUserSerializer(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    verification_status = serializers.CharField(read_only=True)
    reviewed_at = serializers.DateTimeField(read_only=True, allow_null=True)
    rejection_reason = serializers.CharField(read_only=True, allow_blank=True)
    verified_by = serializers.UUIDField(read_only=True, allow_null=True)
    selfie_signed_url = serializers.CharField(read_only=True, allow_null=True)


class IdentityReviewInputSerializer(serializers.Serializer):
    """Entrada de la revisión.

    `reason` es texto libre y **sin catálogo**: ninguna fuente del proyecto define uno
    para identidades, y `RejectionReason` de `core.enums` tipifica el rechazo de
    *reportes*. Tampoco se le impone una longitud máxima, porque ninguna fuente la
    establece y la columna es `text`.

    Al aprobar, `reason` se ignora: el servicio deja `rejection_reason` vacío.
    """

    decision = serializers.ChoiceField(
        choices=[
            (VerificationStatus.APROBADO, VerificationStatus.APROBADO.label),
            (VerificationStatus.RECHAZADO, VerificationStatus.RECHAZADO.label),
        ]
    )
    reason = serializers.CharField(required=False, allow_blank=True, trim_whitespace=True)

    def validate(self, attrs):
        """El rechazo exige un motivo con contenido; la aprobación no lo mira."""
        if attrs["decision"] != VerificationStatus.RECHAZADO:
            return attrs

        if not (attrs.get("reason") or "").strip():
            raise serializers.ValidationError({"reason": "El rechazo requiere un motivo."})

        return attrs


class IdentityReviewResultSerializer(serializers.Serializer):
    """Resultado de la revisión.

    `verified_by` es el **UUID** del administrador, no su email: identifica la decisión
    de forma estable sin publicar el dato personal de quien revisó.
    """

    id = serializers.UUIDField(read_only=True)
    verification_status = serializers.CharField(read_only=True)
    reviewed_at = serializers.DateTimeField(read_only=True, allow_null=True)
    verified_by = serializers.UUIDField(read_only=True, allow_null=True)
