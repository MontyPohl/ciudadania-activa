"""Utilidades de administración compartidas.

El admin del Sprint 2 es una herramienta de **inspección interna** (§12 del plan):
no es el panel institucional y no debe permitir escribir a mano en tablas cuya
única vía legítima de escritura es un servicio de dominio.
"""


class ReadOnlyAdminMixin:
    """Deja un `ModelAdmin` en modo lectura: sin alta, cambio ni borrado.

    Django concede el permiso `view` de forma independiente desde la versión 2.1,
    de modo que la lista y el detalle siguen siendo navegables en modo lectura.
    """

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        # Segundo cinturón: si alguien concediera el permiso de cambio por error, el
        # formulario seguiría sin campos editables, incluidos los que se agreguen luego.
        return [field.name for field in self.model._meta.fields]
