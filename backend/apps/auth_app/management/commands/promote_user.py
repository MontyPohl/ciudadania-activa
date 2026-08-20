"""Promoción de un usuario existente a `operator` o `admin` (Sprint 3).

Es la **única** vía de asignación de roles: no existe ni existirá un endpoint público
de promoción, y el frontend nunca puede elegir el rol. El comando no crea usuarios.

Uso:
    python manage.py promote_user <email> --role operator --institution <slug>
    python manage.py promote_user <email> --role admin

Promover a `admin` **no** toca `is_staff` ni `is_superuser`: el acceso al admin de
Django se administra por separado con los mecanismos propios de Django (ADR-0005).
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.admin_app.models import Institution, InstitutionOperator
from apps.auth_app.enums import UserRole
from apps.core.services import audit

User = get_user_model()


class Command(BaseCommand):
    help = "Promueve un usuario existente a operator (con su institución) o a admin."

    def add_arguments(self, parser):
        parser.add_argument("email", help="Email del usuario, que debe existir.")
        parser.add_argument(
            "--role",
            required=True,
            choices=[UserRole.OPERATOR, UserRole.ADMIN],
            help="Rol de destino.",
        )
        parser.add_argument(
            "--institution",
            help="Slug de la institución. Obligatorio para operator, inválido para admin.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        email = options["email"]
        role = options["role"]
        institution_slug = options["institution"]

        if role == UserRole.OPERATOR and not institution_slug:
            raise CommandError("--institution es obligatorio al promover a operator.")
        if role == UserRole.ADMIN and institution_slug:
            raise CommandError("--institution no es válido al promover a admin.")

        user = User.objects.filter(email__iexact=email).first()
        if user is None:
            raise CommandError(
                f"No existe ningún usuario con el email «{email}». "
                "Este comando promueve usuarios existentes; no los crea."
            )

        institution = None
        if institution_slug:
            institution = Institution.objects.filter(slug=institution_slug).first()
            if institution is None:
                raise CommandError(
                    f"No existe ninguna institución con el slug «{institution_slug}»."
                )

        # `verification_status` no se toca: la verificación de identidad es del Sprint 4.
        if user.role != role:
            user.role = role
            user.save(update_fields=["role"])

        payload = {
            "target_user_id": str(user.pk),
            "new_role": role,
            "source": "management_command",
        }

        if institution is not None:
            vinculo, creado = InstitutionOperator.objects.get_or_create(
                user=user, institution=institution, defaults={"active": True}
            )
            if not creado and not vinculo.active:
                vinculo.active = True
                vinculo.save(update_fields=["active"])
            payload["institution_id"] = str(institution.pk)
            payload["institution_slug"] = institution.slug

        # El actor es `None`: la acción la ejecuta el operador del sistema por consola,
        # así que el servicio la registra con `actor_type = system`. En la misma
        # transacción que el cambio de rol y el vínculo.
        audit.log(None, "user.role_promoted", payload)

        detalle = f" en «{institution.name}»" if institution is not None else ""
        self.stdout.write(self.style.SUCCESS(f"Usuario {user.pk} promovido a {role}{detalle}."))
