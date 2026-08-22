import uuid

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils import timezone

from apps.auth_app.enums import RankingVisibility, UserRole, VerificationStatus
from apps.core.models import BaseModel


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email: str, password: str | None, **extra_fields):
        if not email:
            raise ValueError("El email es obligatorio.")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email: str, password: str | None = None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email: str, password: str | None = None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("El superusuario debe tener is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("El superusuario debe tener is_superuser=True.")

        return self._create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    # Esqueleto del Sprint 1 ampliado en el Sprint 2 con los campos de dominio del Anexo A.1.
    # El modelo no se redefine ni se recrea: AUTH_USER_MODEL y la migración 0001 quedan
    # intactos (ADR-0014, C-06); los campos nuevos llegan por migración incremental.
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=255, blank=True)
    # Nullable porque el superusuario y las cuentas semilla no tienen identidad Google;
    # PostgreSQL admite varios NULL bajo un índice único, de modo que RF-01 sigue garantizado
    # para los valores reales. El flujo de OAuth llega en el Sprint 3.
    google_oauth_id = models.CharField(max_length=255, unique=True, null=True, blank=True)
    role = models.CharField(max_length=20, choices=UserRole.choices, default=UserRole.CITIZEN)
    verification_status = models.CharField(
        max_length=20,
        choices=VerificationStatus.choices,
        default=VerificationStatus.PENDIENTE,
    )
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        db_table = "users"

    def __str__(self) -> str:
        return self.email


class CitizenProfile(BaseModel):
    """Identidad verificada y preferencias de privacidad del ciudadano (Anexo A.1).

    El perfil **no** se crea automáticamente al registrar o autenticar un usuario:
    nace cuando el ciudadano envía su verificación de identidad en el Sprint 4,
    momento en el que `document_hmac` ya tiene valor. Eso permite mantener la
    restricción `unique, not null` que declara el Anexo A.4 (decisión D2-06).
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="citizen_profile",
    )
    # HMAC-SHA-256 del número de cédula, con clave secreta del servidor (RF-02).
    # El **número recibido como dato textual** nunca se persiste en claro: solo
    # existe como esta huella. El índice único materializa RF-04.
    document_hmac = models.CharField(max_length=64, unique=True)
    # Clave del objeto en el bucket **privado** de Supabase Storage
    # (`verifications/{user_id}/{uuid4}.{ext}`), no una URL: una URL firmada expira
    # y una pública no existe para este bucket.
    #
    # Precisión de privacidad: la selfie muestra al ciudadano **sosteniendo su
    # cédula**, de modo que puede contener visualmente el número. Que el número
    # textual no se guarde en claro no convierte a este archivo en dato inocuo: es
    # información sensible, vive solo en el bucket privado y únicamente un
    # administrador la ve, mediante URL firmada efímera.
    verification_selfie_url = models.TextField(blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    # Administrador que aprobó o rechazó la verificación. SET_NULL para no bloquear
    # la baja de una cuenta administrativa; el detalle de la revisión queda en audit_logs.
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="verified_profiles",
    )
    # Motivo del rechazo, en texto libre y **sin catálogo**: ninguna fuente del
    # proyecto define uno para identidades. `RejectionReason` de `core.enums`
    # tipifica el rechazo de *reportes* y no se reutiliza aquí. El mismo motivo se
    # duplica en `audit_logs.payload` para trazabilidad administrativa; esta columna
    # existe para que el ciudadano pueda leerlo sin consultar la auditoría, que
    # §29 del plan reserva a los administradores. Se limpia al reenviar.
    rejection_reason = models.TextField(blank=True, default="")
    ranking_visibility = models.CharField(
        max_length=20,
        choices=RankingVisibility.choices,
        default=RankingVisibility.FULL_NAME,
    )
    ranking_alias = models.CharField(max_length=60, blank=True)

    class Meta:
        db_table = "citizen_profiles"

    def __str__(self) -> str:
        return f"Perfil de {self.user_id}"
