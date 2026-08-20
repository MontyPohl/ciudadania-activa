"""Petición mínima para ejercitar `ModelAdmin` sin levantar el ciclo HTTP.

El usuario simulado concede **todos** los permisos: así, cuando un admin de solo
lectura devuelve `False`, queda claro que la negativa viene de la clase y no de la
falta de permisos.
"""


class AllowAllUser:
    is_active = True
    is_staff = True
    is_superuser = True

    def has_perm(self, perm, obj=None) -> bool:
        return True

    def has_module_perms(self, app_label) -> bool:
        return True


class FakeRequest:
    def __init__(self):
        self.user = AllowAllUser()
        self.method = "GET"
        self.GET = {}
        self.POST = {}
