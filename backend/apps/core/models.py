import uuid

from django.db import models


class BaseModel(models.Model):
    """Modelo base de todas las entidades de dominio creadas en el Sprint 2.

    Fija las convenciones del proyecto: clave primaria `UUID` —coherente con
    `auth_app.User`, que la declara desde `0001_initial`— y marcas temporales de
    creación y actualización. Es abstracto: no genera tabla ni migración propia.

    `auth_app.User` **no** hereda de aquí: conserva su `date_joined` y su clave
    primaria ya migrada en el Sprint 1 (ADR-0014), y reescribirlo obligaría a
    tocar una migración aplicada.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
