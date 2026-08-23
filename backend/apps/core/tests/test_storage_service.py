"""Cliente de Supabase Storage.

**Ninguna prueba sale a la red.** `requests.request` se sustituye por un doble en cada
caso, y los que ejercitan el fallo cerrado comprueban además que no llega a llamarse.

Se verifica sobre todo lo que no debe salir: la service-role, el cuerpo de la respuesta
de Supabase y las claves de objeto completas no pueden aparecer ni en los mensajes de
error ni en los logs.
"""

import logging

import pytest
import requests

from apps.core.services import storage

USUARIO = "2f1c9b7e-4a3d-4c2b-9f11-8d6a0c5e7b42"
OBJETO = "9b7c1d2e-3f40-4a5b-8c6d-7e8f90a1b2c3"
CLAVE = f"verifications/{USUARIO}/{OBJETO}.png"
CUERPO_SENSIBLE = {"error": "s3 bucket ciudadania-prod ruta interna /var/lib/storage"}


class RespuestaFalsa:
    """Mínimo de la interfaz de `requests.Response` que usa el cliente."""

    def __init__(self, status_code=200, payload=None, texto=""):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._payload = payload
        self.text = texto
        self.cerrada = False

    def json(self):
        if self._payload is None:
            raise ValueError("Sin cuerpo JSON.")
        return self._payload

    def close(self):
        self.cerrada = True


@pytest.fixture
def espia(monkeypatch):
    """Sustituye `requests.request` y registra cada llamada."""
    llamadas = []

    def instalar(respuesta=None, error=None):
        def falso(method, url, **kwargs):
            llamadas.append({"method": method, "url": url, **kwargs})
            if error is not None:
                raise error
            return respuesta if respuesta is not None else RespuestaFalsa()

        monkeypatch.setattr(storage.requests, "request", falso)
        return llamadas

    instalar.llamadas = llamadas
    return instalar


# --------------------------------------------------------------------------- #
# Fallo cerrado por configuración ausente
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("ausente", ["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"])
def test_sin_configuracion_falla_cerrado_y_no_llama_a_nadie(
    settings, storage_config, espia, ausente
):
    llamadas = espia()
    setattr(settings, ausente, "")

    with pytest.raises(storage.StorageConfigurationError):
        storage.upload("verificaciones", CLAVE, b"x", "image/png")

    assert llamadas == []


def test_sin_bucket_configurado_falla_cerrado(settings, storage_config):
    settings.SUPABASE_STORAGE_BUCKET_PRIVATE = ""

    with pytest.raises(storage.StorageConfigurationError):
        storage.private_bucket()


def test_el_bucket_privado_se_lee_de_los_ajustes(storage_config):
    assert storage.private_bucket() == "verificaciones"


def test_el_error_de_configuracion_no_revela_que_ajuste_falta(settings, storage_config):
    settings.SUPABASE_SERVICE_ROLE_KEY = ""

    with pytest.raises(storage.StorageConfigurationError) as excinfo:
        storage.create_signed_url("verificaciones", CLAVE, 300)

    mensaje = str(excinfo.value)
    assert "SUPABASE" not in mensaje
    assert "service" not in mensaje.lower()


def test_la_configuracion_es_error_de_almacenamiento(storage_config):
    assert issubclass(storage.StorageConfigurationError, storage.StorageError)
    assert issubclass(storage.StorageUnavailableError, storage.StorageError)


# --------------------------------------------------------------------------- #
# upload
# --------------------------------------------------------------------------- #


def test_upload_envia_los_bytes_al_bucket(storage_config, espia):
    llamadas = espia()

    storage.upload("verificaciones", CLAVE, b"bytes-de-la-imagen", "image/png")

    assert len(llamadas) == 1
    llamada = llamadas[0]
    assert llamada["method"] == "POST"
    assert llamada["url"] == (
        "https://proyecto-de-prueba.supabase.co/storage/v1/object/verificaciones/" + CLAVE
    )
    assert llamada["data"] == b"bytes-de-la-imagen"
    assert llamada["headers"]["Content-Type"] == "image/png"


def test_upload_no_sobrescribe_en_silencio(storage_config, espia):
    llamadas = espia()

    storage.upload("verificaciones", CLAVE, b"x", "image/png")

    assert llamadas[0]["headers"]["x-upsert"] == "false"


def test_upload_autoriza_con_la_service_role(storage_config, espia):
    llamadas = espia()

    storage.upload("verificaciones", CLAVE, b"x", "image/png")

    assert llamadas[0]["headers"]["Authorization"] == "Bearer service-role-de-prueba"


# --------------------------------------------------------------------------- #
# create_signed_url
# --------------------------------------------------------------------------- #


def test_create_signed_url_devuelve_una_url_absoluta(storage_config, espia):
    firmada = f"/object/sign/verificaciones/{CLAVE}?token=jwt-de-prueba"
    espia(respuesta=RespuestaFalsa(payload={"signedURL": firmada}))

    resultado = storage.create_signed_url("verificaciones", CLAVE, 300)

    assert resultado == f"https://proyecto-de-prueba.supabase.co/storage/v1{firmada}"


def test_create_signed_url_acepta_la_grafia_alternativa(storage_config, espia):
    espia(respuesta=RespuestaFalsa(payload={"signedUrl": "/object/sign/x?token=t"}))

    resultado = storage.create_signed_url("verificaciones", CLAVE, 300)

    assert resultado.endswith("/object/sign/x?token=t")


def test_create_signed_url_envia_la_expiracion_pedida(storage_config, espia):
    llamadas = espia(respuesta=RespuestaFalsa(payload={"signedURL": "/object/sign/x?token=t"}))

    storage.create_signed_url("verificaciones", CLAVE, 300)

    assert llamadas[0]["json"] == {"expiresIn": 300}


def test_una_respuesta_sin_url_firmada_es_un_fallo(storage_config, espia):
    espia(respuesta=RespuestaFalsa(payload={"otra": "cosa"}))

    with pytest.raises(storage.StorageUnavailableError):
        storage.create_signed_url("verificaciones", CLAVE, 300)


def test_una_respuesta_ilegible_es_un_fallo(storage_config, espia):
    espia(respuesta=RespuestaFalsa(payload=None))

    with pytest.raises(storage.StorageUnavailableError):
        storage.create_signed_url("verificaciones", CLAVE, 300)


# --------------------------------------------------------------------------- #
# delete
# --------------------------------------------------------------------------- #


def test_delete_borra_el_objeto(storage_config, espia):
    llamadas = espia()

    storage.delete("verificaciones", CLAVE)

    assert llamadas[0]["method"] == "DELETE"
    assert llamadas[0]["url"].endswith(f"/object/verificaciones/{CLAVE}")


# --------------------------------------------------------------------------- #
# Timeouts
# --------------------------------------------------------------------------- #


def test_todas_las_operaciones_declaran_timeout_explicito(storage_config, espia):
    llamadas = espia(respuesta=RespuestaFalsa(payload={"signedURL": "/object/sign/x?token=t"}))

    storage.upload("verificaciones", CLAVE, b"x", "image/png")
    storage.create_signed_url("verificaciones", CLAVE, 300)
    storage.delete("verificaciones", CLAVE)

    assert len(llamadas) == 3
    for llamada in llamadas:
        conexion, lectura = llamada["timeout"]
        assert conexion > 0
        assert lectura > 0


def test_la_subida_tolera_mas_tiempo_de_lectura_que_las_demas(storage_config, espia):
    llamadas = espia(respuesta=RespuestaFalsa(payload={"signedURL": "/object/sign/x?token=t"}))

    storage.upload("verificaciones", CLAVE, b"x", "image/png")
    storage.delete("verificaciones", CLAVE)

    assert llamadas[0]["timeout"][1] > llamadas[1]["timeout"][1]


# --------------------------------------------------------------------------- #
# Errores externos
# --------------------------------------------------------------------------- #


def test_la_red_caida_se_traduce_a_error_propio(storage_config, espia):
    espia(error=requests.ConnectionError("connection refused"))

    with pytest.raises(storage.StorageUnavailableError):
        storage.upload("verificaciones", CLAVE, b"x", "image/png")


def test_el_timeout_se_traduce_a_error_propio(storage_config, espia):
    espia(error=requests.Timeout("read timed out"))

    with pytest.raises(storage.StorageUnavailableError):
        storage.delete("verificaciones", CLAVE)


@pytest.mark.parametrize("estado", [400, 401, 403, 404, 409, 500, 503])
def test_un_estado_http_de_error_se_traduce_a_error_propio(storage_config, espia, estado):
    espia(respuesta=RespuestaFalsa(status_code=estado, payload=CUERPO_SENSIBLE))

    with pytest.raises(storage.StorageUnavailableError):
        storage.upload("verificaciones", CLAVE, b"x", "image/png")


# --------------------------------------------------------------------------- #
# No filtración
# --------------------------------------------------------------------------- #


def test_el_mensaje_de_error_no_incluye_el_cuerpo_de_supabase(storage_config, espia):
    espia(
        respuesta=RespuestaFalsa(
            status_code=500, payload=CUERPO_SENSIBLE, texto=str(CUERPO_SENSIBLE)
        )
    )

    with pytest.raises(storage.StorageUnavailableError) as excinfo:
        storage.upload("verificaciones", CLAVE, b"x", "image/png")

    mensaje = str(excinfo.value)
    assert "ciudadania-prod" not in mensaje
    assert "/var/lib/storage" not in mensaje


def test_los_logs_no_llevan_la_clave_completa_ni_la_service_role(storage_config, espia, caplog):
    espia(respuesta=RespuestaFalsa(status_code=500, payload=CUERPO_SENSIBLE))

    with caplog.at_level(logging.DEBUG), pytest.raises(storage.StorageUnavailableError):
        storage.upload("verificaciones", CLAVE, b"x", "image/png")

    registrado = caplog.text
    assert CLAVE not in registrado
    assert OBJETO not in registrado
    assert USUARIO not in registrado
    assert "service-role-de-prueba" not in registrado
    assert "ciudadania-prod" not in registrado
    # Queda lo justo para localizar el problema.
    assert "verifications/…" in registrado


def test_los_logs_de_red_caida_tampoco_filtran(storage_config, espia, caplog):
    espia(error=requests.ConnectionError("connection refused"))

    with caplog.at_level(logging.DEBUG), pytest.raises(storage.StorageUnavailableError):
        storage.create_signed_url("verificaciones", CLAVE, 300)

    assert CLAVE not in caplog.text
    assert "service-role-de-prueba" not in caplog.text


def test_la_url_firmada_no_se_registra(storage_config, espia, caplog):
    firmada = "/object/sign/verificaciones/x?token=jwt-secreto-de-prueba"
    espia(respuesta=RespuestaFalsa(payload={"signedURL": firmada}))

    with caplog.at_level(logging.DEBUG):
        storage.create_signed_url("verificaciones", CLAVE, 300)

    assert "jwt-secreto-de-prueba" not in caplog.text
