"""Resolución del contexto institucional del operador."""

from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.admin_app.models import InstitutionOperator

INSTITUTION_CONTEXT_FORBIDDEN = "No tenés acceso al contexto institucional solicitado."
INSTITUTION_CONTEXT_REQUIRED = (
    "Este campo es obligatorio cuando el operador tiene varias instituciones activas."
)


def resolve_operator_institution(*, operator, requested_id):
    memberships = list(
        InstitutionOperator.objects.filter(user=operator, active=True)
        .select_related("institution")
        .order_by("institution__name", "institution_id")
    )

    if requested_id is not None:
        for membership in memberships:
            if membership.institution_id == requested_id:
                return membership.institution
        raise PermissionDenied(INSTITUTION_CONTEXT_FORBIDDEN)

    if len(memberships) == 1:
        return memberships[0].institution
    if len(memberships) > 1:
        raise ValidationError({"institution_id": [INSTITUTION_CONTEXT_REQUIRED]})
    raise PermissionDenied(INSTITUTION_CONTEXT_FORBIDDEN)
