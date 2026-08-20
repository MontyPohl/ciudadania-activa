"""Permisos reutilizables del proyecto (§Sprint 3 del plan).

Dos ideas separadas y no intercambiables:

- **Autenticación**: ¿quién es el usuario? La resuelve `JWTAuthentication`.
- **Autorización**: ¿qué puede hacer? La resuelven estas clases, que leen el rol
  **de la base de datos** en cada petición, nunca de un claim del token. Así,
  retirar un rol surte efecto de inmediato y un JWT manipulado no eleva privilegios.

`DenyAll` es el permiso por defecto del proyecto: una vista que olvide declarar el
suyo queda cerrada. Los cinco nombres restantes son los que fija el plan.

Ninguno de estos permisos usa `is_staff` ni `is_superuser`: esos flags gobiernan el
admin de Django, no la autorización de dominio (ADR-0005).
"""

from rest_framework.permissions import BasePermission
from rest_framework.permissions import IsAuthenticated as IsAuthenticated  # re-export

from apps.auth_app.enums import UserRole, VerificationStatus

__all__ = [
    "DenyAll",
    "IsAdmin",
    "IsAuthenticated",
    "IsInstitutionOperator",
    "IsOperatorOfReportInstitution",
    "IsVerifiedCitizen",
]


def _authenticated_with_role(request, role: str) -> bool:
    user = getattr(request, "user", None)
    return bool(user and user.is_authenticated and user.role == role)


def _has_active_membership(user, institution_id=None) -> bool:
    """¿El usuario tiene un vínculo institucional activo?

    La importación es diferida a propósito: `core` no puede depender de `admin_app`
    en tiempo de importación sin invertir el grafo de dependencias del §9 del plan.
    """
    from apps.admin_app.models import InstitutionOperator

    vinculos = InstitutionOperator.objects.filter(user=user, active=True)
    if institution_id is not None:
        vinculos = vinculos.filter(institution_id=institution_id)
    return vinculos.exists()


class DenyAll(BasePermission):
    """Deniega siempre. Permiso por defecto de la API.

    No concede acceso en ningún caso: ni a anónimos, ni a autenticados, ni a
    superusuarios. Una vista solo responde si declara su propio permiso.
    """

    message = "Esta operación no está disponible."

    def has_permission(self, request, view) -> bool:
        return False

    def has_object_permission(self, request, view, obj) -> bool:
        return False


class IsVerifiedCitizen(BasePermission):
    """Ciudadano con identidad verificada por revisión administrativa (RF-06).

    La identidad de Google **no** equivale a identidad ciudadana verificada: eso lo
    decide el flujo de cédula y selfie del Sprint 4. Este permiso protegerá la
    creación de reportes (S6), las confirmaciones (S7) y los canjes (S12).
    """

    message = "Se requiere una identidad ciudadana verificada."

    def has_permission(self, request, view) -> bool:
        if not _authenticated_with_role(request, UserRole.CITIZEN):
            return False
        return request.user.verification_status == VerificationStatus.APROBADO


class IsInstitutionOperator(BasePermission):
    """Operador con al menos un vínculo institucional activo.

    El rol por sí solo no basta: un `operator` sin `InstitutionOperator` activo no
    opera nada.
    """

    message = "Se requiere ser operador de una institución."

    def has_permission(self, request, view) -> bool:
        if not _authenticated_with_role(request, UserRole.OPERATOR):
            return False
        return _has_active_membership(request.user)


class IsAdmin(BasePermission):
    """Administrador global del dominio (RF-30).

    Es el rol de dominio, no `is_staff` ni `is_superuser`.
    """

    message = "Se requiere el rol de administrador."

    def has_permission(self, request, view) -> bool:
        return _authenticated_with_role(request, UserRole.ADMIN)


class IsOperatorOfReportInstitution(BasePermission):
    """Operador **de la institución del objeto** (RF-27).

    Se declara en el Sprint 3 y se conecta a las vistas de reportes en el Sprint 9.
    Implementación defensiva: si el objeto no expone una institución, deniega. No
    asume una única institución por operador: consulta el vínculo concreto.
    """

    message = "Se requiere ser operador de la institución responsable."

    def has_permission(self, request, view) -> bool:
        return _authenticated_with_role(request, UserRole.OPERATOR)

    def has_object_permission(self, request, view, obj) -> bool:
        if not self.has_permission(request, view):
            return False

        institution_id = getattr(obj, "institution_id", None)
        if institution_id is None:
            return False

        return _has_active_membership(request.user, institution_id=institution_id)
