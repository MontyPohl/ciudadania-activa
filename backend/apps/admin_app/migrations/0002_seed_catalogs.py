# Migración de datos: catálogos semilla del MVP (Anexo A.3).
#
# COPIA CONGELADA. Estas tuplas son el estado inicial del catálogo tal como se cargó
# en el Sprint 2 y NO deben reescribirse cuando el catálogo evolucione: una migración
# histórica describe el pasado. Los cambios posteriores se hacen en migraciones nuevas.
# La copia viva que usa el management command `seed_catalogs` vive en
# `apps/admin_app/catalogs.py`.
#
# Idempotente: `update_or_create` por `slug`, que es la clave natural. Reversible:
# borra exclusivamente los slugs que sembró.

from django.db import migrations

MUNICIPALIDAD = "municipalidad-de-asuncion"
ANDE = "ande"
ESSAP = "essap"

INSTITUTIONS = (
    (MUNICIPALIDAD, "Municipalidad de Asunción"),
    (ANDE, "ANDE"),
    (ESSAP, "ESSAP"),
)

CATEGORIES = (
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


def seed_catalogs(apps, schema_editor):
    Institution = apps.get_model("admin_app", "Institution")
    Category = apps.get_model("admin_app", "Category")

    institutions = {}
    for slug, name in INSTITUTIONS:
        institution, _ = Institution.objects.update_or_create(slug=slug, defaults={"name": name})
        institutions[slug] = institution

    for slug, name, institution_slug, priority in CATEGORIES:
        Category.objects.update_or_create(
            slug=slug,
            defaults={
                "name": name,
                "default_institution": institutions[institution_slug],
                "default_priority": priority,
            },
        )


def unseed_catalogs(apps, schema_editor):
    Institution = apps.get_model("admin_app", "Institution")
    Category = apps.get_model("admin_app", "Category")

    Category.objects.filter(slug__in=[slug for slug, *_ in CATEGORIES]).delete()
    Institution.objects.filter(slug__in=[slug for slug, _ in INSTITUTIONS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('admin_app', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed_catalogs, unseed_catalogs),
    ]
