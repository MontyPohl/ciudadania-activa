"""Cliente HTTPS del correo transaccional de Brevo.

**Ninguna prueba contacta con Brevo.** El doble `brevo_api` —`autouse` en el `conftest`
raíz— sustituye el cliente y registra lo que se le pide enviar. Los ajustes de prueba
apuntan además `BREVO_API_URL` a un host inexistente, de modo que un doble mal puesto se
manifestaría como error de red y no como un envío real.

El doble se instala **solo sobre el módulo `brevo`**, no sobre el `requests` compartido:
la última prueba del archivo fija esa garantía para que no se pierda por descuido.

Lo que se comprueba aquí es la **forma exacta de la petición** —endpoint, cabeceras,
remitente, destinatario, asunto y cuerpo— y que la clave de API no se escapa por ningún
lado: ni al log, ni al mensaje de una excepción, ni al valor de retorno.
"""

import logging

import pytest
import requests

from apps.notifications_app.services import brevo

CLAVE = "clave-brevo-de-prueba-no-real"


def _enviar():
    brevo.send_email(
        to="destinataria@example.test",
        subject="Tu identidad fue verificada",
        text_content="Hola Ana:\n\nTu verificación fue aprobada.\n",
    )


# --------------------------------------------------------------------------- #
# Forma de la petición
# --------------------------------------------------------------------------- #


def test_envia_por_post_al_endpoint_configurado(brevo_api, settings):
    _enviar()

    assert brevo_api.envios == 1
    assert brevo_api.ultimo.url == settings.BREVO_API_URL


def test_el_endpoint_por_defecto_es_el_oficial_de_brevo():
    """El default del ajuste, no el valor que fija la suite."""
    from config.settings import base

    assert base.BREVO_API_URL == "https://api.brevo.com/v3/smtp/email"


def test_la_cabecera_api_key_lleva_la_clave(brevo_api):
    _enviar()

    assert brevo_api.ultimo.headers["api-key"] == CLAVE


def test_la_clave_no_viaja_como_authorization_ni_en_la_url(brevo_api):
    """Brevo autentica con `api-key`. Duplicarla en otro sitio solo amplía la exposición."""
    _enviar()

    assert "Authorization" not in brevo_api.ultimo.headers
    assert "authorization" not in brevo_api.ultimo.headers
    assert CLAVE not in brevo_api.ultimo.url


def test_el_content_type_es_json(brevo_api):
    _enviar()

    assert brevo_api.ultimo.headers["content-type"] == "application/json"


def test_declara_que_acepta_json(brevo_api):
    _enviar()

    assert brevo_api.ultimo.headers["accept"] == "application/json"


def test_el_cuerpo_va_como_json_y_no_como_formulario(brevo_api):
    """`json=` y no `data=`: la API rechaza cualquier otra codificación."""
    _enviar()

    assert isinstance(brevo_api.ultimo.payload, dict)


def test_el_remitente_separa_nombre_y_direccion(brevo_api):
    _enviar()

    assert brevo_api.ultimo.remitente == {
        "name": "Ciudadanía Activa",
        "email": "remitente-de-prueba@example.test",
    }


def test_un_remitente_sin_nombre_omite_la_clave(brevo_api, settings):
    """`{"name": ""}` no es lo mismo que no mandar `name`."""
    settings.DEFAULT_FROM_EMAIL = "solo-direccion@example.test"

    _enviar()

    assert brevo_api.ultimo.remitente == {"email": "solo-direccion@example.test"}


def test_el_destinatario_va_en_la_lista_to(brevo_api):
    _enviar()

    assert brevo_api.ultimo.payload["to"] == [{"email": "destinataria@example.test"}]


def test_el_asunto_y_el_cuerpo_van_tal_cual(brevo_api):
    _enviar()

    assert brevo_api.ultimo.asunto == "Tu identidad fue verificada"
    assert brevo_api.ultimo.cuerpo == "Hola Ana:\n\nTu verificación fue aprobada.\n"


def test_el_cuerpo_es_texto_plano_sin_html(brevo_api):
    """El Sprint 4 manda texto plano; el HTML es alcance de los Sprints 12-13."""
    _enviar()

    assert "htmlContent" not in brevo_api.ultimo.payload
    assert "textContent" in brevo_api.ultimo.payload


def test_no_se_manda_nada_de_mas(brevo_api):
    """Cuatro claves: cuantos menos datos viajen a un tercero, mejor."""
    _enviar()

    assert set(brevo_api.ultimo.payload) == {"sender", "to", "subject", "textContent"}


def test_la_llamada_lleva_timeout_explicito(brevo_api):
    """Sin timeout, `requests` espera indefinidamente y el lock de la fila con él."""
    _enviar()

    conexion, lectura = brevo_api.ultimo.timeout
    assert 0 < conexion <= 10
    assert 0 < lectura <= 30


# --------------------------------------------------------------------------- #
# Éxito y fallo
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("estado", [200, 201, 202])
def test_una_respuesta_2xx_es_exito(brevo_api, estado):
    brevo_api.status_code = estado

    assert _enviar() is None


@pytest.mark.parametrize("estado", [301, 302, 400, 401, 402, 403, 429, 500, 502, 503])
def test_cualquier_otro_estado_levanta(brevo_api, estado):
    brevo_api.status_code = estado

    with pytest.raises(brevo.BrevoUnavailableError):
        _enviar()


@pytest.mark.parametrize(
    "fallo",
    [
        requests.exceptions.ConnectTimeout("timeout"),
        requests.exceptions.ReadTimeout("timeout"),
        requests.exceptions.ConnectionError("connection refused"),
        requests.exceptions.SSLError("bad handshake"),
    ],
)
def test_un_fallo_de_red_levanta_el_error_propio(brevo_api, fallo):
    brevo_api.excepcion = fallo

    with pytest.raises(brevo.BrevoUnavailableError):
        _enviar()


def test_la_respuesta_se_cierra_siempre(brevo_api, monkeypatch):
    """Dejar la conexión abierta filtraría sockets en un worker de larga vida."""
    respuestas = []
    original = brevo_api.__call__

    def registrar(*args, **kwargs):
        respuesta = original(*args, **kwargs)
        respuestas.append(respuesta)
        return respuesta

    monkeypatch.setattr("apps.notifications_app.services.brevo.requests.post", registrar)

    _enviar()

    assert respuestas[0].cerrada is True


# --------------------------------------------------------------------------- #
# Fallo cerrado
# --------------------------------------------------------------------------- #


def test_sin_clave_de_api_no_se_intenta_ninguna_llamada(brevo_api, settings):
    settings.BREVO_API_KEY = ""

    with pytest.raises(brevo.BrevoConfigurationError):
        _enviar()

    assert brevo_api.envios == 0


def test_sin_endpoint_no_se_intenta_ninguna_llamada(brevo_api, settings):
    settings.BREVO_API_URL = ""

    with pytest.raises(brevo.BrevoConfigurationError):
        _enviar()

    assert brevo_api.envios == 0


def test_sin_remitente_no_se_intenta_ninguna_llamada(brevo_api, settings):
    settings.DEFAULT_FROM_EMAIL = ""

    with pytest.raises(brevo.BrevoConfigurationError):
        _enviar()

    assert brevo_api.envios == 0


def test_el_error_de_configuracion_no_dice_que_ajuste_falta(settings):
    settings.BREVO_API_KEY = ""

    with pytest.raises(brevo.BrevoConfigurationError) as exc:
        _enviar()

    assert "BREVO_API_KEY" not in str(exc.value)
    assert "clave-brevo" not in str(exc.value)


def test_los_errores_de_configuracion_son_de_la_familia_brevo():
    assert issubclass(brevo.BrevoConfigurationError, brevo.BrevoError)
    assert issubclass(brevo.BrevoUnavailableError, brevo.BrevoError)


# --------------------------------------------------------------------------- #
# La clave no se escapa
# --------------------------------------------------------------------------- #


def test_la_clave_no_aparece_en_el_log_de_un_fallo_de_red(brevo_api, caplog):
    brevo_api.excepcion = requests.exceptions.ReadTimeout("timeout contra api.brevo.com")

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(brevo.BrevoUnavailableError):
            _enviar()

    assert CLAVE not in caplog.text
    assert "ReadTimeout" in caplog.text


def test_la_clave_no_aparece_en_el_log_de_un_rechazo(brevo_api, caplog):
    brevo_api.status_code = 401

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(brevo.BrevoUnavailableError):
            _enviar()

    assert CLAVE not in caplog.text
    assert "401" in caplog.text


def test_la_clave_no_aparece_en_el_mensaje_de_las_excepciones(brevo_api):
    brevo_api.status_code = 401

    with pytest.raises(brevo.BrevoUnavailableError) as exc:
        _enviar()

    assert CLAVE not in str(exc.value)


def test_el_log_del_fallo_no_lleva_el_destinatario(brevo_api, caplog):
    """Quien llama registra el id de la notificación; el correo no hace falta."""
    brevo_api.excepcion = requests.exceptions.ConnectionError("connection refused")

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(brevo.BrevoUnavailableError):
            _enviar()

    assert "destinataria@example.test" not in caplog.text


def test_el_cuerpo_de_la_respuesta_de_brevo_no_se_propaga(brevo_api, caplog):
    """Puede repetir la dirección del destinatario o detalles de la cuenta."""
    brevo_api.status_code = 400

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(brevo.BrevoUnavailableError) as exc:
            _enviar()

    assert "messageId" not in caplog.text
    assert "messageId" not in str(exc.value)


def test_send_email_no_devuelve_nada(brevo_api):
    """No hay valor de retorno del que pueda colgarse un dato del proveedor."""
    assert brevo.send_email(to="a@example.test", subject="x", text_content="y") is None


# --------------------------------------------------------------------------- #
# Aislamiento del doble
# --------------------------------------------------------------------------- #


def test_el_doble_no_alcanza_al_requests_compartido(brevo_api):
    """El doble sustituye el cliente de Brevo, **no** `requests.post` de todo el proceso.

    `brevo.py` hace `import requests`, así que `brevo.requests` sería el módulo global y
    parchear `brevo.requests.post` lo sustituiría en todo el proceso, para toda la
    suite. Que hoy no rompa nada es una casualidad —`core.services.storage` usa
    `requests.request`, que es otra función—, no una garantía: cualquier código o
    dependencia que llamara a `requests.post` dentro de una prueba recibiría en silencio
    este doble y un `201` inventado.

    Esta prueba existe para que ese aislamiento no se pierda en una refactorización.
    """
    assert requests.post is not brevo_api
    assert brevo.requests is not requests
    assert brevo.requests.post is brevo_api


def test_el_doble_delega_el_resto_del_modulo_en_el_requests_real(brevo_api):
    """Todo lo que no sea `post` sigue siendo el módulo de verdad.

    `brevo.py` captura `requests.RequestException`; si el envoltorio no la delegara, el
    `except` del cliente dejaría de coincidir y un fallo de red escaparía sin traducirse.
    """
    assert brevo.requests.RequestException is requests.RequestException
    assert brevo.requests.Timeout is requests.Timeout
    assert brevo.requests.ConnectionError is requests.ConnectionError
