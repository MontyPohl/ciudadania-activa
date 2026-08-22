"""Normalización del número de cédula y HMAC-SHA-256 (RF-02, RF-04).

Ninguna prueba toca la base de datos: son funciones puras.

Deliberadamente **no** existe una prueba de «irreversibilidad»: una prueba unitaria no
demuestra una propiedad criptográfica. Lo que sí se verifica es lo que el proyecto
necesita y puede comprobarse: un vector conocido, determinismo, dependencia de la
clave, la forma del resultado, la equivalencia de las escrituras admitidas, el rechazo
de todo lo demás, el fallo cerrado sin clave y que el número no se filtra a los logs.
"""

import logging

import pytest

from apps.auth_app.services import identity

#: Clave ficticia, exclusiva de la suite. Cada entorno tiene la suya: la de producción
#: nunca se comparte con desarrollo ni con CI (ADR-0006).
SECRETO = "secreto-de-prueba-para-el-hmac-del-sprint-4"
OTRO_SECRETO = "otro-secreto-de-prueba-distinto-del-anterior"

#: Vector conocido: HMAC-SHA-256(SECRETO, "1234567") en hexadecimal minúscula.
VECTOR_1234567 = "e128527b97d3efee543c195d80cbd7e275624ff8d4bf0cc45941d8113109a812"
VECTOR_0001234 = "1bb37d715479c1103496bb0d7e0951aa4f9b45468ffbd042013b4b9e1e9a169b"
VECTOR_1234 = "dfdd3e077aabc5d5150c70ec920518638328e4a02da15f16e4a993582f99f910"


@pytest.fixture
def hmac_secret(settings):
    settings.DOCUMENT_HMAC_SECRET = SECRETO
    return SECRETO


# --------------------------------------------------------------------------- #
# Normalización
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "entrada",
    ["1.234.567", "1234567", "1 234 567", "1-234-567", "  1234567  ", "1.234-567"],
)
def test_las_escrituras_admitidas_son_equivalentes(entrada):
    assert identity.normalize_document(entrada) == "1234567"


def test_los_ceros_iniciales_se_preservan():
    """Convertir a entero colapsaría dos identidades distintas en una."""
    assert identity.normalize_document("0001234") == "0001234"
    assert identity.normalize_document("0.001.234") == "0001234"
    assert identity.normalize_document("0001234") != identity.normalize_document("1234")


@pytest.mark.parametrize(
    "entrada",
    [
        "abc123",
        "123abc",
        "12/34",
        "12+34",
        "12,34",
        "12_34",
        "1234;DROP",
        "١٢٣٤",  # dígitos árabo-índicos
        "１２３４",  # dígitos de ancho completo
        "١٢٣٤٥٦٧",
        "²³",
        "12\t34",  # el tabulador no es el espacio admitido
        "12\u00a034",  # espacio duro: tampoco
        "12\u200b34",  # espacio de ancho cero
    ],
)
def test_los_caracteres_no_admitidos_se_rechazan(entrada):
    with pytest.raises(identity.InvalidDocumentError):
        identity.normalize_document(entrada)


def test_nunca_rescata_los_digitos_de_una_cadena_invalida():
    """`abc123` es un error, no `123`: normalizar en silencio crearía otra identidad."""
    with pytest.raises(identity.InvalidDocumentError):
        identity.normalize_document("abc123")


@pytest.mark.parametrize("entrada", ["", "   ", ".", "-", "...", "- . -", "  .-  "])
def test_vacio_o_solo_separadores_se_rechaza(entrada):
    with pytest.raises(identity.InvalidDocumentError):
        identity.normalize_document(entrada)


def test_una_entrada_que_no_es_cadena_se_rechaza():
    with pytest.raises(identity.InvalidDocumentError):
        identity.normalize_document(1234567)


def test_el_mensaje_de_error_no_repite_el_valor_recibido():
    with pytest.raises(identity.InvalidDocumentError) as excinfo:
        identity.normalize_document("abc987654")

    assert "987654" not in str(excinfo.value)
    assert "abc" not in str(excinfo.value)


# --------------------------------------------------------------------------- #
# HMAC
# --------------------------------------------------------------------------- #


def test_vector_conocido(hmac_secret):
    assert identity.compute_document_hmac("1234567") == VECTOR_1234567
    assert identity.compute_document_hmac("0001234") == VECTOR_0001234
    assert identity.compute_document_hmac("1234") == VECTOR_1234


def test_es_determinista(hmac_secret):
    assert identity.compute_document_hmac("1234567") == identity.compute_document_hmac("1234567")


def test_las_escrituras_equivalentes_producen_el_mismo_hmac(hmac_secret):
    esperado = identity.compute_document_hmac("1234567")

    for escritura in ("1.234.567", "1 234 567", "1-234-567", " 1234567 "):
        assert identity.compute_document_hmac(escritura) == esperado


def test_una_clave_distinta_produce_un_hmac_distinto(settings):
    settings.DOCUMENT_HMAC_SECRET = SECRETO
    con_secreto = identity.compute_document_hmac("1234567")

    settings.DOCUMENT_HMAC_SECRET = OTRO_SECRETO
    con_otro = identity.compute_document_hmac("1234567")

    assert con_secreto != con_otro
    assert con_otro == "5968fd53ed7795c455462323afc1df3f20dbb1ce0156dd085d6aeae2bfc25a77"


def test_la_forma_del_resultado_encaja_en_la_columna(hmac_secret):
    """`citizen_profiles.document_hmac` es `varchar(64)`: hex minúscula de 64."""
    resultado = identity.compute_document_hmac("1234567")

    assert len(resultado) == 64
    assert resultado == resultado.lower()
    assert set(resultado) <= set("0123456789abcdef")


def test_dos_cedulas_distintas_producen_hmac_distintos(hmac_secret):
    assert identity.compute_document_hmac("1234567") != identity.compute_document_hmac("7654321")


# --------------------------------------------------------------------------- #
# Fallo cerrado y no filtración
# --------------------------------------------------------------------------- #


def test_sin_secreto_falla_cerrado(settings):
    settings.DOCUMENT_HMAC_SECRET = ""

    with pytest.raises(identity.IdentityConfigurationError):
        identity.compute_document_hmac("1234567")


def test_sin_secreto_no_llega_a_normalizar(settings, monkeypatch):
    """No se procesa nada: la configuración se comprueba antes de tocar la entrada."""
    settings.DOCUMENT_HMAC_SECRET = ""
    llamadas = []
    monkeypatch.setattr(
        identity, "normalize_document", lambda raw: llamadas.append(raw) or "1234567"
    )

    with pytest.raises(identity.IdentityConfigurationError):
        identity.compute_document_hmac("1234567")

    assert llamadas == []


def test_el_error_de_configuracion_no_menciona_el_secreto(settings):
    settings.DOCUMENT_HMAC_SECRET = ""

    with pytest.raises(identity.IdentityConfigurationError) as excinfo:
        identity.compute_document_hmac("1234567")

    assert "DOCUMENT_HMAC_SECRET" not in str(excinfo.value)


def test_la_cedula_no_aparece_en_los_logs(hmac_secret, caplog):
    """Ni en la rama de éxito ni en la de error (RF-02)."""
    cedula = "4987654"

    with caplog.at_level(logging.DEBUG):
        identity.compute_document_hmac(cedula)
        with pytest.raises(identity.InvalidDocumentError):
            identity.compute_document_hmac("abc" + cedula)

    registrado = caplog.text
    assert cedula not in registrado
    assert SECRETO not in registrado


def test_el_hmac_tampoco_se_registra(hmac_secret, caplog):
    with caplog.at_level(logging.DEBUG):
        resultado = identity.compute_document_hmac("1234567")

    assert resultado not in caplog.text


# --------------------------------------------------------------------------- #
# Comparación
# --------------------------------------------------------------------------- #


def test_documents_match_reconoce_el_mismo_numero(hmac_secret):
    almacenado = identity.compute_document_hmac("1.234.567")

    assert identity.documents_match("1234567", almacenado) is True
    assert identity.documents_match("7654321", almacenado) is False
