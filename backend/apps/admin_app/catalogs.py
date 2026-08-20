"""Catálogos semilla del MVP (Anexo A.3 de la tesis).

Estas constantes son la copia **viva** que usa el management command
`seed_catalogs`. La migración `0002_seed_catalogs` lleva su propia copia
**congelada**: una migración histórica no se reescribe cuando el catálogo
evoluciona.

Los `slug` se obtienen con `django.utils.text.slugify` sobre el nombre
(decisión D2-12) y son la clave natural de la carga idempotente.
"""

MUNICIPALIDAD = "municipalidad-de-asuncion"
ANDE = "ande"
ESSAP = "essap"

# (slug, name)
INSTITUTIONS: tuple[tuple[str, str], ...] = (
    (MUNICIPALIDAD, "Municipalidad de Asunción"),
    (ANDE, "ANDE"),
    (ESSAP, "ESSAP"),
)

# (slug, name, slug de la institución por defecto, prioridad por defecto)
CATEGORIES: tuple[tuple[str, str, str, str], ...] = (
    ("baches", "Baches", MUNICIPALIDAD, "MEDIA"),
    ("basura-acumulada", "Basura acumulada", MUNICIPALIDAD, "MEDIA"),
    ("alumbrado-publico", "Alumbrado público", ANDE, "MEDIA"),
    ("agua-potable-perdidas", "Agua potable / pérdidas", ESSAP, "MEDIA"),
    ("drenaje-desague-cloaca", "Drenaje / desagüe / cloaca", ESSAP, "MEDIA"),
    ("semaforos", "Semáforos", MUNICIPALIDAD, "MEDIA"),
    ("escombros", "Escombros", MUNICIPALIDAD, "MEDIA"),
    ("arbol-caido", "Árbol caído", MUNICIPALIDAD, "MEDIA"),
    (
        "cables-expuestos-riesgo-electrico",
        "Cables expuestos / riesgo eléctrico",
        ANDE,
        "MEDIA",
    ),
    ("otros", "Otros", MUNICIPALIDAD, "MEDIA"),
)
