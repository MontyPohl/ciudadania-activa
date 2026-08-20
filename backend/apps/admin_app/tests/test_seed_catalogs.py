"""Verifica el resultado inicial del seed, su mapeo y su idempotencia.

No se comprueba ninguna igualdad entre las constantes vivas de
`apps/admin_app/catalogs.py` y la copia congelada de la migración de datos: una
migración histórica describe el pasado y no debe reescribirse cuando el catálogo
evolucione.
"""

import pytest
from django.core.management import call_command
from django.utils.text import slugify

from apps.admin_app.models import Category, Institution
from apps.core.enums import ReportPriority

pytestmark = pytest.mark.django_db

# Mapeo del Anexo A.3 de la tesis.
MAPEO_ANEXO_A3 = {
    "Baches": "Municipalidad de Asunción",
    "Basura acumulada": "Municipalidad de Asunción",
    "Alumbrado público": "ANDE",
    "Agua potable / pérdidas": "ESSAP",
    "Drenaje / desagüe / cloaca": "ESSAP",
    "Semáforos": "Municipalidad de Asunción",
    "Escombros": "Municipalidad de Asunción",
    "Árbol caído": "Municipalidad de Asunción",
    "Cables expuestos / riesgo eléctrico": "ANDE",
    "Otros": "Municipalidad de Asunción",
}


def test_la_migracion_de_datos_deja_el_catalogo_cargado():
    assert Institution.objects.count() == 3
    assert Category.objects.count() == 10


def test_instituciones_sembradas_sin_datos_inventados():
    assert sorted(Institution.objects.values_list("slug", flat=True)) == [
        "ande",
        "essap",
        "municipalidad-de-asuncion",
    ]
    assert not Institution.objects.exclude(contact_email="").exists()
    assert not Institution.objects.filter(sla_hours__isnull=False).exists()


def test_mapeo_categoria_institucion_del_anexo_a3():
    real = {
        categoria.name: categoria.default_institution.name
        for categoria in Category.objects.select_related("default_institution")
    }

    assert real == MAPEO_ANEXO_A3


def test_todas_las_categorias_nacen_con_prioridad_media():
    assert (
        list(Category.objects.values_list("default_priority", flat=True))
        == [ReportPriority.MEDIA] * 10
    )


def test_los_slugs_son_la_slugificacion_determinista_del_nombre():
    for categoria in Category.objects.all():
        assert categoria.slug == slugify(categoria.name)
    for institucion in Institution.objects.all():
        assert institucion.slug == slugify(institucion.name)


def test_el_command_es_idempotente():
    ids_previos = set(Institution.objects.values_list("id", flat=True))

    call_command("seed_catalogs", verbosity=0)
    call_command("seed_catalogs", verbosity=0)

    assert Institution.objects.count() == 3
    assert Category.objects.count() == 10
    # No recrea filas: conserva las claves primarias existentes.
    assert set(Institution.objects.values_list("id", flat=True)) == ids_previos


def test_el_command_repone_un_catalogo_vaciado():
    Category.objects.all().delete()
    Institution.objects.all().delete()

    call_command("seed_catalogs", verbosity=0)

    assert Institution.objects.count() == 3
    assert Category.objects.count() == 10
