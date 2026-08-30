"""Pruebas del mapeo contra los catálogos reales (S8-B).

El eje de todas ellas es el mismo: el emparejamiento es exacto o no es. Un
modelo que se queda cerca no acierta, y el catálogo nunca se modifica.
"""

import pytest

from apps.admin_app.models import Category, Institution
from apps.ai_client_app.catalog_mapping import resolve_category, resolve_institution

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------- #
# Resolución correcta
# --------------------------------------------------------------------------- #


def test_categoria_valida_y_activa(category):
    assert resolve_category("baches") == category


def test_institucion_valida_y_activa(institution):
    assert resolve_institution("municipalidad-de-asuncion") == institution


@pytest.mark.parametrize(
    "slug", ["baches", "semaforos", "otros", "alumbrado-publico", "agua-potable-perdidas"]
)
def test_los_slugs_del_catalogo_semilla_resuelven(slug):
    assert resolve_category(slug) is not None


@pytest.mark.parametrize("slug", ["municipalidad-de-asuncion", "ande", "essap"])
def test_las_instituciones_semilla_resuelven(slug):
    assert resolve_institution(slug) is not None


# --------------------------------------------------------------------------- #
# Desconocidos y nulos
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "slug",
    ["semaforo-emocional-roto", "inexistente", "bache", "baches-2", "categoria-inventada"],
)
def test_categoria_desconocida_no_resuelve(slug):
    """Una categoría inventada por el modelo no entra al dominio."""
    assert resolve_category(slug) is None


@pytest.mark.parametrize("slug", ["copaco", "petropar", "municipalidad", "inexistente"])
def test_institucion_desconocida_no_resuelve(slug):
    assert resolve_institution(slug) is None


def test_categoria_nula_no_resuelve():
    assert resolve_category(None) is None


def test_institucion_nula_no_resuelve():
    assert resolve_institution(None) is None


# --------------------------------------------------------------------------- #
# Baja lógica
# --------------------------------------------------------------------------- #


def test_categoria_inactiva_se_trata_como_inexistente(category):
    Category.objects.filter(pk=category.pk).update(is_active=False)

    assert resolve_category("baches") is None


def test_institucion_inactiva_se_trata_como_inexistente(institution):
    Institution.objects.filter(pk=institution.pk).update(is_active=False)

    assert resolve_institution("municipalidad-de-asuncion") is None


# --------------------------------------------------------------------------- #
# Nada de aproximaciones
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("slug", ["Baches", "BACHES", "bAcHeS"])
def test_no_hay_normalizacion_de_mayusculas(slug):
    assert resolve_category(slug) is None


@pytest.mark.parametrize("slug", ["baches ", " baches", "  baches  ", "\tbaches"])
def test_el_espacio_sobrante_no_se_recorta(slug):
    """El parser deja pasar la cadena; el catálogo la rechaza, que es aquí."""
    assert resolve_category(slug) is None


@pytest.mark.parametrize(
    "valor",
    ["Baches", "Municipalidad de Asunción", "Municipalidad de Asuncion", "Semáforos"],
)
def test_el_nombre_humano_no_sustituye_al_slug(valor):
    """El Anexo H.3 usaba nombres libres; el contrato v1 exige slugs (D2)."""
    assert resolve_category(valor) is None
    assert resolve_institution(valor) is None


def test_no_se_quitan_acentos():
    """`semaforos` existe; `semáforos` no, y no se normaliza para acertar."""
    assert resolve_category("semaforos") is not None
    assert resolve_category("semáforos") is None


# --------------------------------------------------------------------------- #
# Solo lectura
# --------------------------------------------------------------------------- #


def test_resolver_no_crea_ni_modifica_catalogo():
    categorias = Category.objects.count()
    instituciones = Institution.objects.count()

    resolve_category("baches")
    resolve_category("inexistente")
    resolve_category(None)
    resolve_institution("ande")
    resolve_institution("inexistente")
    resolve_institution(None)

    assert Category.objects.count() == categorias
    assert Institution.objects.count() == instituciones


def test_un_slug_desconocido_no_da_de_alta_la_categoria():
    resolve_category("semaforo-emocional-roto")

    assert not Category.objects.filter(slug="semaforo-emocional-roto").exists()


def test_no_usa_default_institution_como_respaldo(category):
    """Ese mapeo es del catálogo, no una sugerencia de la IA (D16)."""
    assert category.default_institution is not None
    assert resolve_institution(None) is None
