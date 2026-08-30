"""Pruebas del parser estricto (S8-B).

Todas son puras: ni base de datos, ni red, ni fixtures de Django. Lo que se
verifica es que el parser rechaza, porque rechazar es su única función de
seguridad.
"""

import json
from decimal import Decimal

import pytest

from apps.ai_client_app.contracts import OUTPUT_FIELDS
from apps.ai_client_app.parser import ClassificationParseError, parse_classification_response

VALIDO = {
    "category_slug": "baches",
    "institution_slug": "municipalidad-de-asuncion",
    "priority": "ALTA",
    "confidence": 0.87,
    "inconsistency": False,
    "justification": "La imagen muestra un bache profundo en vía principal.",
}


def render(**overrides) -> str:
    payload = {**VALIDO, **overrides}
    return json.dumps(payload)


# --------------------------------------------------------------------------- #
# Camino feliz
# --------------------------------------------------------------------------- #


def test_respuesta_valida_completa():
    parsed = parse_classification_response(render())

    assert parsed.category_slug == "baches"
    assert parsed.institution_slug == "municipalidad-de-asuncion"
    assert parsed.priority == "ALTA"
    assert parsed.confidence == Decimal("0.87")
    assert parsed.inconsistency is False
    assert parsed.justification == VALIDO["justification"]


def test_confidence_se_parsea_como_decimal_exacto():
    """Decimal y no float: 0.87 debe seguir siendo 0.87 al auditarse."""
    parsed = parse_classification_response(render(confidence=0.87))

    assert isinstance(parsed.confidence, Decimal)
    assert parsed.confidence == Decimal("0.87")


@pytest.mark.parametrize("campo", ["category_slug", "institution_slug"])
def test_slug_nulo_es_valido(campo):
    parsed = parse_classification_response(render(**{campo: None}))

    assert getattr(parsed, campo) is None


@pytest.mark.parametrize("valor", [0, 1, 0.0, 1.0, 0.75])
def test_confidence_en_los_limites_del_rango(valor):
    parsed = parse_classification_response(render(confidence=valor))

    assert Decimal("0") <= parsed.confidence <= Decimal("1")


@pytest.mark.parametrize("prioridad", ["BAJA", "MEDIA", "ALTA"])
def test_las_tres_prioridades_del_enum(prioridad):
    assert parse_classification_response(render(priority=prioridad)).priority == prioridad


def test_inconsistency_true_es_valido():
    """Marcar inconsistencia es una respuesta legítima, no un error."""
    assert parse_classification_response(render(inconsistency=True)).inconsistency is True


# --------------------------------------------------------------------------- #
# JSON inválido, top-level y peculiaridades permisivas
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "{",
        "{'category_slug': 'baches'}",
        '{"category_slug": "baches",}',
        "no soy json",
    ],
    ids=["vacio", "truncado", "comillas-simples", "coma-final", "texto-libre"],
)
def test_json_malformado(raw):
    with pytest.raises(ClassificationParseError, match="invalid json"):
        parse_classification_response(raw)


@pytest.mark.parametrize(
    "raw",
    ["[]", '["a"]', "null", '"texto"', "42", "true"],
    ids=["array-vacio", "array", "null", "string", "number", "boolean"],
)
def test_top_level_no_objeto(raw):
    with pytest.raises(ClassificationParseError, match="invalid top-level type"):
        parse_classification_response(raw)


@pytest.mark.parametrize("constante", ["NaN", "Infinity", "-Infinity"])
def test_constantes_no_representables(constante):
    """`json` las acepta por defecto; el contrato no."""
    raw = render().replace("0.87", constante)

    with pytest.raises(ClassificationParseError, match="invalid json constant"):
        parse_classification_response(raw)


def test_clave_duplicada():
    """Sin el hook, `json` conservaría en silencio la última aparición."""
    raw = '{"confidence": 0.2, "confidence": 0.9}'

    with pytest.raises(ClassificationParseError, match="duplicate field: confidence"):
        parse_classification_response(raw)


def test_clave_duplicada_en_respuesta_por_lo_demas_valida():
    raw = render()[:-1] + ', "confidence": 0.1}'

    with pytest.raises(ClassificationParseError, match="duplicate field: confidence"):
        parse_classification_response(raw)


def test_valla_markdown():
    """El prompt exige JSON puro; una valla de código no lo es."""
    with pytest.raises(ClassificationParseError, match="invalid json"):
        parse_classification_response(f"```json\n{render()}\n```")


def test_texto_antes_del_json():
    with pytest.raises(ClassificationParseError, match="invalid json"):
        parse_classification_response(f"Aquí tienes el resultado:\n{render()}")


def test_texto_despues_del_json():
    with pytest.raises(ClassificationParseError, match="invalid json"):
        parse_classification_response(f"{render()}\nEspero que sea útil.")


def test_raw_no_es_texto():
    with pytest.raises(ClassificationParseError, match="invalid raw type"):
        parse_classification_response({"category_slug": "baches"})


# --------------------------------------------------------------------------- #
# Conjunto exacto de claves
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("campo", sorted(OUTPUT_FIELDS))
def test_cada_campo_obligatorio_falta(campo):
    payload = {k: v for k, v in VALIDO.items() if k != campo}

    with pytest.raises(ClassificationParseError, match="missing field"):
        parse_classification_response(json.dumps(payload))


def test_campo_extra():
    """Un proveedor que añade campos cambió el contrato: debe verse."""
    with pytest.raises(ClassificationParseError, match="unknown field: foo"):
        parse_classification_response(render(foo="bar"))


def test_hubo_cambio_del_anexo_es_un_campo_desconocido():
    """D4: lo deriva el backend, no se acepta del modelo."""
    with pytest.raises(ClassificationParseError, match="unknown field: hubo_cambio"):
        parse_classification_response(render(hubo_cambio=True))


def test_objeto_vacio():
    with pytest.raises(ClassificationParseError, match="missing field"):
        parse_classification_response("{}")


# --------------------------------------------------------------------------- #
# Validación por campo: sin coerción
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("campo", ["category_slug", "institution_slug"])
@pytest.mark.parametrize(
    "valor",
    [1, True, False, [], {}, 1.5, ""],
    ids=["int", "true", "false", "list", "dict", "float", "vacio"],
)
def test_slug_de_tipo_incorrecto(campo, valor):
    with pytest.raises(ClassificationParseError, match=f"invalid field: {campo}"):
        parse_classification_response(render(**{campo: valor}))


@pytest.mark.parametrize(
    "valor",
    ["alta", "Alta", "HIGH", "URGENTE", 1, None, True, ""],
    ids=["minusculas", "capitalizada", "ingles", "inventada", "int", "null", "bool", "vacia"],
)
def test_prioridad_invalida(valor):
    with pytest.raises(ClassificationParseError, match="invalid field: priority"):
        parse_classification_response(render(priority=valor))


@pytest.mark.parametrize(
    "valor",
    ["0.87", True, False, None, [], {}, -0.001, 1.001, -1, 2],
    ids=["string", "true", "false", "null", "list", "dict", "bajo", "alto", "negativo", "dos"],
)
def test_confidence_invalida(valor):
    with pytest.raises(ClassificationParseError, match="invalid field: confidence"):
        parse_classification_response(render(confidence=valor))


@pytest.mark.parametrize(
    "valor",
    [0, 1, "true", "false", None, [], {}],
    ids=["cero", "uno", "string-true", "string-false", "null", "list", "dict"],
)
def test_inconsistency_invalida(valor):
    with pytest.raises(ClassificationParseError, match="invalid field: inconsistency"):
        parse_classification_response(render(inconsistency=valor))


@pytest.mark.parametrize(
    "valor",
    ["", "   ", "\n\t ", None, 1, True, [], {}, "x" * 1001],
    ids=["vacia", "espacios", "whitespace", "null", "int", "bool", "list", "dict", "larga"],
)
def test_justification_invalida(valor):
    with pytest.raises(ClassificationParseError, match="invalid field: justification"):
        parse_classification_response(render(justification=valor))


def test_justification_de_exactamente_mil_caracteres_es_valida():
    parsed = parse_classification_response(render(justification="x" * 1000))

    assert len(parsed.justification) == 1000


def test_justification_se_recorta_por_fuera():
    """Único ajuste admitido; el interior se conserva intacto."""
    parsed = parse_classification_response(render(justification="  Bache   profundo.  "))

    assert parsed.justification == "Bache   profundo."


def test_justification_larga_solo_por_espacios_exteriores_es_valida():
    """El límite se mide sobre el texto normalizado, que es el que se persiste."""
    parsed = parse_classification_response(render(justification="  " + "x" * 1000 + "  "))

    assert len(parsed.justification) == 1000


# --------------------------------------------------------------------------- #
# Higiene de los mensajes de error
# --------------------------------------------------------------------------- #


def test_el_error_no_filtra_el_contenido_de_la_respuesta():
    """Los mensajes terminan en logs y auditoría: nombran el campo, no el dato."""
    secreto = "El ciudadano Juan Pérez vive en Avenida Siempreviva 742"

    with pytest.raises(ClassificationParseError) as exc:
        parse_classification_response(render(justification=secreto, confidence="0.9"))

    assert secreto not in str(exc.value)
    assert "0.9" not in str(exc.value)
    assert str(exc.value) == "invalid field: confidence"


def test_el_error_de_json_invalido_no_incluye_el_payload():
    crudo = '{"description": "datos sensibles del ciudadano"'

    with pytest.raises(ClassificationParseError) as exc:
        parse_classification_response(crudo)

    assert "sensibles" not in str(exc.value)


def test_la_excepcion_publica_no_es_la_de_json():
    """Quien invoca captura ClassificationParseError, no JSONDecodeError."""
    with pytest.raises(ClassificationParseError):
        parse_classification_response("{")

    assert not isinstance(json.JSONDecodeError("m", "d", 0), ClassificationParseError)
