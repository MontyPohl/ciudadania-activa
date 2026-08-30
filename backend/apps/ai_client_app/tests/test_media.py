"""Carga de la imagen del reporte para clasificar (S8-D).

Ninguna prueba toca Supabase: el cliente HTTP está doblado. Lo que se verifica es
que la imagen llega como bytes, que su ruta nunca sale del backend y que un fallo
de almacenamiento se clasifica como transitorio o permanente según corresponda.
"""

import pytest

from apps.ai_client_app import media as media_module
from apps.ai_client_app.exceptions import PermanentAiMediaError, TransientAiMediaError
from apps.ai_client_app.media import load_report_image
from apps.core.services import storage
from apps.reports_app.enums import MediaType
from apps.reports_app.models import ReportMedia

pytestmark = pytest.mark.django_db

JPEG = b"\xff\xd8\xff\xe0 bytes normalizados de prueba"
PNG = b"\x89PNG\r\n\x1a\n bytes normalizados de prueba"


@pytest.fixture
def con_foto(report):
    def _crear(nombre="reports/abc/def.jpg"):
        return ReportMedia.objects.create(
            report=report,
            file_url=nombre,
            file_hash="0" * 64,
            media_type=MediaType.FOTO,
        )

    return _crear


@pytest.fixture
def descarga(monkeypatch):
    """Doble de la descarga privada que registra sus argumentos."""
    llamadas = []

    def _instalar(resultado=None, error=None):
        def fake_download(bucket, key):
            llamadas.append({"bucket": bucket, "key": key})
            if error is not None:
                raise error
            return resultado

        monkeypatch.setattr(storage, "download_private", fake_download)
        monkeypatch.setattr(storage, "reports_bucket", lambda: "reportes")
        return llamadas

    return _instalar


# --------------------------------------------------------------------------- #
# Camino feliz
# --------------------------------------------------------------------------- #


def test_descarga_los_bytes_de_la_foto(report, con_foto, descarga):
    con_foto()
    llamadas = descarga(resultado=JPEG)

    imagen = load_report_image(report)

    assert imagen.content == JPEG
    assert imagen.mime_type == "image/jpeg"
    assert llamadas == [{"bucket": "reportes", "key": "reports/abc/def.jpg"}]


@pytest.mark.parametrize(
    ("clave", "mime"),
    [
        ("reports/a/b.jpg", "image/jpeg"),
        ("reports/a/b.jpeg", "image/jpeg"),
        ("reports/a/b.png", "image/png"),
        ("reports/a/B.PNG", "image/png"),
    ],
)
def test_el_mime_se_deriva_de_la_extension_que_fijo_la_pipeline(
    report, con_foto, descarga, clave, mime
):
    """S6 garantiza que la extensión coincide con el formato real detectado."""
    con_foto(clave)
    descarga(resultado=PNG)

    assert load_report_image(report).mime_type == mime


def test_sin_foto_devuelve_none(report, descarga):
    llamadas = descarga(resultado=JPEG)

    assert load_report_image(report) is None
    assert llamadas == [], "no debe consultarse el almacenamiento sin evidencia"


# --------------------------------------------------------------------------- #
# Nada de URLs
# --------------------------------------------------------------------------- #


def test_no_se_genera_ninguna_url_firmada(report, con_foto, descarga, monkeypatch):
    con_foto()
    descarga(resultado=JPEG)

    def prohibido(*args, **kwargs):
        raise AssertionError("se intentó crear una URL firmada")

    monkeypatch.setattr(storage, "create_signed_url", prohibido)

    load_report_image(report)


def test_el_modulo_no_conoce_urls_ni_publicas_ni_firmadas():
    import inspect

    fuente = inspect.getsource(media_module)

    assert "create_signed_url" not in fuente
    assert "public_url" not in fuente
    assert "http://" not in fuente
    assert "https://" not in fuente


# --------------------------------------------------------------------------- #
# Fallos: transitorio frente a permanente
# --------------------------------------------------------------------------- #


def test_el_almacenamiento_caido_es_transitorio(report, con_foto, descarga):
    con_foto()
    descarga(error=storage.StorageUnavailableError("503"))

    with pytest.raises(TransientAiMediaError):
        load_report_image(report)


def test_un_objeto_inexistente_es_permanente(report, con_foto, descarga):
    """Insistir no lo va a crear."""
    con_foto()
    descarga(error=storage.StorageObjectNotFoundError("404"))

    with pytest.raises(PermanentAiMediaError):
        load_report_image(report)


def test_la_falta_de_configuracion_es_permanente(report, con_foto, descarga):
    con_foto()
    descarga(error=storage.StorageConfigurationError("sin configurar"))

    with pytest.raises(PermanentAiMediaError):
        load_report_image(report)


def test_una_extension_irreconocible_es_permanente(report, con_foto, descarga):
    con_foto("reports/abc/def.gif")
    llamadas = descarga(resultado=JPEG)

    with pytest.raises(PermanentAiMediaError):
        load_report_image(report)

    assert llamadas == [], "no debe descargarse lo que no se puede identificar"


def test_un_objeto_vacio_es_permanente(report, con_foto, descarga):
    con_foto()
    descarga(resultado=b"")

    with pytest.raises(PermanentAiMediaError):
        load_report_image(report)


def test_el_error_no_revela_la_clave_completa(report, con_foto, descarga):
    con_foto("reports/11111111-2222-3333-4444-555555555555/foto.jpg")
    descarga(error=storage.StorageObjectNotFoundError("404"))

    with pytest.raises(PermanentAiMediaError) as exc:
        load_report_image(report)

    assert "11111111-2222-3333-4444-555555555555" not in str(exc.value)


def test_no_persiste_nada(report, con_foto, descarga):
    con_foto()
    descarga(resultado=JPEG)
    antes = ReportMedia.objects.count()

    load_report_image(report)

    assert ReportMedia.objects.count() == antes
