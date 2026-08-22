"""Fixtures compartidas de la suite del backend.

Se limitan a construir instancias mínimas válidas de los modelos del Sprint 2 y a los
insumos deterministas que necesitan las pruebas del Sprint 4: ninguna reproduce reglas
de negocio de sprints futuros.

Ningún dato sensible real aparece aquí: las cédulas son ficticias, la clave del HMAC es
de prueba y las imágenes se generan en memoria.
"""

import io
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.admin_app.models import Category, Institution
from apps.reports_app.models import Report

# Microcentro de Asunción, en el orden (lon, lat) que espera PostGIS.
ASUNCION = (-57.6359, -25.2637)


@pytest.fixture
def user_factory(db):
    def make_user(email="ciudadano@example.com", **extra):
        return get_user_model().objects.create_user(
            email=email, password="clave-segura-123", **extra
        )

    return make_user


@pytest.fixture
def citizen(user_factory):
    return user_factory(email="autor@example.com")


@pytest.fixture
def other_citizen(user_factory):
    return user_factory(email="confirmador@example.com")


@pytest.fixture
def institution(db):
    return Institution.objects.get(slug="municipalidad-de-asuncion")


@pytest.fixture
def category(db):
    return Category.objects.get(slug="baches")


@pytest.fixture
def operator_user(user_factory, institution):
    """Operador con vínculo institucional activo."""
    from apps.admin_app.models import InstitutionOperator
    from apps.auth_app.enums import UserRole

    user = user_factory(email="operador@example.com", role=UserRole.OPERATOR)
    InstitutionOperator.objects.create(user=user, institution=institution, active=True)
    return user


@pytest.fixture
def admin_user(user_factory):
    """Administrador de dominio. Deliberadamente sin `is_staff` ni `is_superuser`."""
    from apps.auth_app.enums import UserRole

    return user_factory(email="admin@example.com", role=UserRole.ADMIN)


@pytest.fixture
def google_client_id(settings):
    """Configura el `audience` que el verificador debe exigir."""
    settings.GOOGLE_OAUTH_CLIENT_ID = "cliente-de-prueba.apps.googleusercontent.com"
    return settings.GOOGLE_OAUTH_CLIENT_ID


@pytest.fixture
def google_claims(google_client_id):
    """Fábrica de claims verificados, tal como los devolvería `google-auth`."""

    def make(**overrides):
        claims = {
            "iss": "https://accounts.google.com",
            "aud": google_client_id,
            "sub": "115000000000000000001",
            "email": "nuevo@example.com",
            "email_verified": True,
            "name": "Nombre Apellido",
        }
        claims.update(overrides)
        return claims

    return make


@pytest.fixture
def google_verifier(monkeypatch):
    """Sustituye la verificación real por un doble controlado.

    Ninguna prueba sale a la red: se reemplaza `verify_oauth2_token`, de modo que la
    validación de claims propia del servicio sí se ejercita.
    """

    def install(claims=None, error=None):
        def fake_verify(token, request, audience=None, **kwargs):
            if error is not None:
                raise error
            return claims

        monkeypatch.setattr(
            "apps.auth_app.services.google.google_id_token.verify_oauth2_token",
            fake_verify,
        )

    return install


# --------------------------------------------------------------------------- #
# Verificación de identidad (Sprint 4)
# --------------------------------------------------------------------------- #

#: Clave ficticia del HMAC, exclusiva de la suite. La de producción es otra y no se
#: comparte con desarrollo ni con CI (ADR-0006).
DOCUMENT_HMAC_SECRET_DE_PRUEBA = "secreto-de-prueba-para-el-hmac-del-sprint-4"


@pytest.fixture
def document_hmac_secret(settings):
    """Configura una clave de HMAC ficticia para las pruebas que la necesiten."""
    settings.DOCUMENT_HMAC_SECRET = DOCUMENT_HMAC_SECRET_DE_PRUEBA
    return settings.DOCUMENT_HMAC_SECRET


@pytest.fixture
def storage_config(settings):
    """Configuración de Storage con valores ficticios. Ninguna prueba sale a la red."""
    settings.SUPABASE_URL = "https://proyecto-de-prueba.supabase.co"
    settings.SUPABASE_SERVICE_ROLE_KEY = "service-role-de-prueba"  # noqa: S105
    settings.SUPABASE_STORAGE_BUCKET_PRIVATE = "verificaciones"
    return settings


@pytest.fixture
def fake_storage(monkeypatch):
    """Sustituye las operaciones de Storage y registra lo que se les pide.

    **Ninguna prueba sale a la red.** El registro permite afirmar cuántas subidas y
    borrados hubo y sobre qué claves, y los dos atributos de error permiten inyectar
    un fallo de Storage sin fabricar respuestas HTTP.
    """
    from apps.core.services import storage

    registro = SimpleNamespace(uploads=[], deletes=[], upload_error=None, delete_error=None)

    def fake_upload(bucket, key, content, content_type):
        if registro.upload_error is not None:
            raise registro.upload_error
        registro.uploads.append(
            {"bucket": bucket, "key": key, "content": content, "content_type": content_type}
        )

    def fake_delete(bucket, key):
        # Se registra el intento antes de fallar: una prueba necesita comprobar que la
        # compensación se intentó, aunque el borrado no prosperase.
        registro.deletes.append({"bucket": bucket, "key": key})
        if registro.delete_error is not None:
            raise registro.delete_error

    monkeypatch.setattr(storage, "upload", fake_upload)
    monkeypatch.setattr(storage, "delete", fake_delete)
    return registro


@pytest.fixture
def imagen_factory():
    """Genera en memoria una imagen mínima válida del formato pedido."""

    def make(formato: str, size: tuple[int, int] = (64, 64)) -> bytes:
        buffer = io.BytesIO()
        Image.new("RGB", size, color=(120, 80, 160)).save(buffer, format=formato)
        return buffer.getvalue()

    return make


@pytest.fixture
def jpeg_bytes(imagen_factory):
    return imagen_factory("JPEG")


@pytest.fixture
def png_bytes(imagen_factory):
    return imagen_factory("PNG")


@pytest.fixture
def selfie_factory():
    """Fábrica de archivos subidos, para las pruebas de validación y de API."""

    def make(content: bytes, name: str = "selfie.jpg", content_type: str = "image/jpeg"):
        return SimpleUploadedFile(name, content, content_type=content_type)

    return make


@pytest.fixture
def report(citizen, category):
    return Report.objects.create(
        citizen=citizen,
        category=category,
        description="Bache profundo en la calzada.",
        location=Point(*ASUNCION, srid=4326),
    )
