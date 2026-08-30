"""Resolución de slugs sugeridos contra los catálogos reales (S8-B).

Lectura pura: consulta, nunca escribe. La IA no puede crear ni modificar
categorías ni instituciones (D2); la autoridad sigue siendo `admin_app`.

El emparejamiento es **exacto**. No hay `lower()`, ni normalización de acentos,
ni coincidencia aproximada, ni sinónimos, ni distancia de edición. Un modelo que
devuelve «Baches», «Municipalidad de Asunción» o `"baches "` no acierta por poco:
no acierta, y el reporte va a revisión humana. Aproximar aquí sería adivinar en
nombre de un operador que todavía no ha visto el caso.
"""

from __future__ import annotations

from apps.admin_app.models import Category, Institution


def resolve_category(slug: str | None) -> Category | None:
    """Devuelve la categoría activa con ese slug exacto, o `None`.

    `None` en la entrada significa que el modelo no propuso categoría, y `None`
    en la salida que no hay sugerencia utilizable: no se sustituye por la
    categoría actual, ni por «otros», ni por ninguna otra cosa.
    """
    if slug is None:
        return None
    return Category.objects.filter(slug=slug, is_active=True).first()


def resolve_institution(slug: str | None) -> Institution | None:
    """Devuelve la institución activa con ese slug exacto, o `None`.

    Una institución dada de baja (`is_active=False`) se trata como inexistente:
    sigue en la tabla porque las claves foráneas son `PROTECT`, pero ya no es un
    destino válido para una sugerencia automática.

    No se usa `Category.default_institution` como respaldo: ese mapeo es una
    decisión del catálogo, no una sugerencia de la IA, y confundirlos haría pasar
    por clasificación automática algo que nunca se clasificó (D13, D16).
    """
    if slug is None:
        return None
    return Institution.objects.filter(slug=slug, is_active=True).first()
