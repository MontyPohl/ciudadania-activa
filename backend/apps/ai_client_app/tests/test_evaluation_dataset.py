"""Contrato del dataset sintético congelado (S8-E1).

El dataset define qué significa el número que se reporta para CA-4, así que su
validación es estricta y su contenido está congelado por hash: modificarlo rompe
una prueba a propósito y obliga a una decisión explícita.
"""

import hashlib
import json

import pytest

from apps.ai_client_app.evaluation.dataset import (
    DATASET_PATH,
    FORBIDDEN_FIELDS,
    MIN_CASES,
    InvalidEvaluationDataset,
    build_dataset,
    load_dataset,
)

# Freeze metodológico, no una medida de seguridad. Si esta prueba falla, alguien
# cambió el benchmark: la decisión correcta es crear una versión nueva del
# dataset, no actualizar el hash.
DATASET_S8_V1_SHA256 = "3bc54d591dc104aeabeecb2f5cc7c7a38e70ba042e7f045321ab33ae9b5efad4"


@pytest.fixture(scope="module")
def dataset():
    return load_dataset()


@pytest.fixture(scope="module")
def raw():
    return json.loads(DATASET_PATH.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Congelación
# --------------------------------------------------------------------------- #


def test_el_dataset_esta_congelado_por_hash():
    digest = hashlib.sha256(DATASET_PATH.read_bytes()).hexdigest()

    assert digest == DATASET_S8_V1_SHA256, (
        "El dataset de evaluación cambió. Si es intencional, cree una versión nueva "
        "(dataset_s8_v2) en lugar de actualizar este hash: modificar el benchmark en "
        "silencio invalida cualquier comparación con mediciones anteriores."
    )


def test_la_version_esta_declarada(dataset):
    assert dataset.dataset_version == "s8-evaluation-v1"


# --------------------------------------------------------------------------- #
# Estructura
# --------------------------------------------------------------------------- #


def test_hay_al_menos_los_casos_exigidos_por_el_contrato(dataset):
    """D19 exige un mínimo de 20."""
    assert len(dataset.cases) >= MIN_CASES
    assert len(dataset.cases) == 28


def test_los_identificadores_son_unicos_y_estables(dataset):
    ids = [c.id for c in dataset.cases]

    assert len(set(ids)) == len(ids)
    assert all(i.startswith("S8-EVAL-") for i in ids)


def test_el_orden_del_archivo_se_conserva(dataset, raw):
    assert [c.id for c in dataset.cases] == [c["id"] for c in raw["cases"]]


def test_ninguna_descripcion_esta_vacia(dataset):
    assert all(c.description.strip() for c in dataset.cases)


def test_el_catalogo_no_tiene_duplicados(dataset):
    for valores in (
        dataset.catalog.category_slugs,
        dataset.catalog.institution_slugs,
        dataset.catalog.priorities,
    ):
        assert len(set(valores)) == len(valores)


def test_la_taxonomia_coincide_con_la_del_proyecto(dataset):
    """El dataset congela la taxonomía vigente al crearlo."""
    from apps.admin_app.catalogs import CATEGORIES, INSTITUTIONS
    from apps.core.enums import ReportPriority

    assert set(dataset.catalog.category_slugs) == {slug for slug, *_ in CATEGORIES}
    assert set(dataset.catalog.institution_slugs) == {slug for slug, _ in INSTITUTIONS}
    assert set(dataset.catalog.priorities) == set(ReportPriority.values)


# --------------------------------------------------------------------------- #
# Etiquetas dentro del catálogo
# --------------------------------------------------------------------------- #


def test_todas_las_etiquetas_pertenecen_al_catalogo(dataset):
    for caso in dataset.cases:
        assert caso.current_category_slug in dataset.catalog.category_slugs
        if caso.current_institution_slug is not None:
            assert caso.current_institution_slug in dataset.catalog.institution_slugs
        if caso.expected.category_slug is not None:
            assert caso.expected.category_slug in dataset.catalog.category_slugs
        if caso.expected.institution_slug is not None:
            assert caso.expected.institution_slug in dataset.catalog.institution_slugs
        assert caso.expected.priority in dataset.catalog.priorities
        assert type(caso.expected.inconsistency) is bool


# --------------------------------------------------------------------------- #
# Cobertura del benchmark
# --------------------------------------------------------------------------- #


def test_cubre_todas_las_categorias_activas(dataset):
    esperadas = {c.expected.category_slug for c in dataset.cases}

    assert esperadas == set(dataset.catalog.category_slugs)


def test_cubre_todas_las_instituciones_activas(dataset):
    esperadas = {c.expected.institution_slug for c in dataset.cases}

    assert esperadas == set(dataset.catalog.institution_slugs)


def test_cubre_las_tres_prioridades(dataset):
    assert {c.expected.priority for c in dataset.cases} == {"BAJA", "MEDIA", "ALTA"}


def test_hay_casos_consistentes_e_inconsistentes(dataset):
    inconsistentes = [c for c in dataset.cases if c.expected.inconsistency]

    assert len(inconsistentes) >= 6
    assert len(inconsistentes) < len(dataset.cases)


def test_hay_casos_que_exigen_corregir_la_categoria(dataset):
    """Sin ellos, un sistema que se limite a repetir la entrada puntuaría alto."""
    correcciones = [c for c in dataset.cases if c.current_category_slug != c.expected.category_slug]

    assert len(correcciones) >= 6


def test_hay_casos_que_exigen_corregir_la_institucion(dataset):
    correcciones = [
        c for c in dataset.cases if c.current_institution_slug != c.expected.institution_slug
    ]

    assert len(correcciones) >= 6


def test_hay_casos_con_institucion_actual_nula(dataset):
    assert any(c.current_institution_slug is None for c in dataset.cases)


def test_hay_casos_donde_la_eleccion_del_ciudadano_ya_es_correcta(dataset):
    aciertos = [
        c
        for c in dataset.cases
        if c.current_category_slug == c.expected.category_slug
        and c.current_institution_slug == c.expected.institution_slug
    ]

    assert len(aciertos) >= 6


# --------------------------------------------------------------------------- #
# Ausencia estructural de PII
# --------------------------------------------------------------------------- #


def test_el_formato_no_admite_campos_de_datos_personales(raw):
    def recolectar(nodo):
        if isinstance(nodo, dict):
            for clave, valor in nodo.items():
                yield clave
                yield from recolectar(valor)
        elif isinstance(nodo, list):
            for item in nodo:
                yield from recolectar(item)

    claves = set(recolectar(raw))

    assert claves.isdisjoint(FORBIDDEN_FIELDS)


def test_un_campo_prohibido_invalida_el_dataset(raw):
    corrupto = json.loads(json.dumps(raw))
    corrupto["cases"][0]["citizen_id"] = "no-deberia-existir"

    with pytest.raises(InvalidEvaluationDataset, match="prohibidos|desconocidos"):
        build_dataset(corrupto)


# --------------------------------------------------------------------------- #
# Validación estricta
# --------------------------------------------------------------------------- #


def test_un_dataset_con_pocos_casos_se_rechaza(raw):
    corrupto = json.loads(json.dumps(raw))
    corrupto["cases"] = corrupto["cases"][:5]

    with pytest.raises(InvalidEvaluationDataset, match="al menos"):
        build_dataset(corrupto)


def test_los_identificadores_duplicados_se_rechazan(raw):
    corrupto = json.loads(json.dumps(raw))
    corrupto["cases"][1]["id"] = corrupto["cases"][0]["id"]

    with pytest.raises(InvalidEvaluationDataset, match="duplicado"):
        build_dataset(corrupto)


def test_un_slug_fuera_del_catalogo_se_rechaza(raw):
    corrupto = json.loads(json.dumps(raw))
    corrupto["cases"][0]["expected"]["category_slug"] = "semaforo-emocional-roto"

    with pytest.raises(InvalidEvaluationDataset, match="catálogo"):
        build_dataset(corrupto)


def test_una_prioridad_invalida_se_rechaza(raw):
    corrupto = json.loads(json.dumps(raw))
    corrupto["cases"][0]["expected"]["priority"] = "URGENTE"

    with pytest.raises(InvalidEvaluationDataset, match="[Pp]rioridad"):
        build_dataset(corrupto)


@pytest.mark.parametrize("valor", ["true", 1, None])
def test_una_inconsistencia_no_booleana_se_rechaza(raw, valor):
    corrupto = json.loads(json.dumps(raw))
    corrupto["cases"][0]["expected"]["inconsistency"] = valor

    with pytest.raises(InvalidEvaluationDataset, match="booleano"):
        build_dataset(corrupto)


def test_una_descripcion_vacia_se_rechaza(raw):
    corrupto = json.loads(json.dumps(raw))
    corrupto["cases"][0]["description"] = "   "

    with pytest.raises(InvalidEvaluationDataset, match="descripción"):
        build_dataset(corrupto)


def test_un_catalogo_con_duplicados_se_rechaza(raw):
    corrupto = json.loads(json.dumps(raw))
    corrupto["catalog"]["priorities"] = ["BAJA", "BAJA", "MEDIA", "ALTA"]

    with pytest.raises(InvalidEvaluationDataset, match="duplicados"):
        build_dataset(corrupto)


def test_una_categoria_actual_nula_se_rechaza(raw):
    """El ciudadano siempre elige categoría; la institución puede faltar."""
    corrupto = json.loads(json.dumps(raw))
    corrupto["cases"][0]["current_category_slug"] = None

    with pytest.raises(InvalidEvaluationDataset, match="no puede ser nulo"):
        build_dataset(corrupto)
