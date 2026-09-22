"""Fixtures compartidas de la suite del backend.

Se limitan a construir instancias mínimas válidas de los modelos del Sprint 2 y a los
insumos deterministas que necesitan las pruebas del Sprint 4: ninguna reproduce reglas
de negocio de sprints futuros.

Ningún dato sensible real aparece aquí: las cédulas son ficticias, la clave del HMAC es
de prueba y las imágenes se generan en memoria.

La suite **no sale a la red**: `brevo_api` es `autouse` y dobla el cliente de correo,
de modo que ninguna prueba puede llegar a la API de Brevo. El parche se aplica sobre el
módulo `brevo` y **no** sobre el `requests` compartido, para no alterar el
comportamiento de los demás clientes HTTP del proyecto.
"""

import io
import shutil
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.admin_app.models import Category, Institution
from apps.notifications_app.services import brevo
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
    settings.SUPABASE_STORAGE_BUCKET_REPORTS = "reportes"
    return settings


@pytest.fixture
def s11_local_storage(settings, request):
    """Overlay experimental S11: adaptador local aislado y Supabase en blanco.

    Reproduce `infra/docker-compose.s11.yml`, que deja `SUPABASE_URL` y la
    service-role vacías **a propósito**, para que ningún run experimental pueda
    alcanzar almacenamiento productivo. La raíz de objetos vive bajo
    `backend/.s11-test-artifacts/`, que está en `.gitignore`.
    """
    settings.S11_LOCAL_STORAGE_ENABLED = True
    settings.S11_EXPERIMENTS_ENABLED = True
    settings.S11_ENVIRONMENT = "isolated-experiment"
    settings.S11_DATABASE_URL = "postgis://ciudadania@db:5432/ciudadania_s11_experiment"
    settings.REDIS_URL = "redis://redis:6379/11"
    # `local_storage.upload` crea en exclusiva: una raíz heredada de otra corrida
    # haría fallar la subida con «el objeto sintético ya existe».
    root = Path.cwd() / ".s11-test-artifacts" / request.node.name
    shutil.rmtree(root, ignore_errors=True)
    settings.S11_OBJECT_ROOT = root
    settings.SUPABASE_URL = ""
    settings.SUPABASE_SERVICE_ROLE_KEY = ""
    settings.SUPABASE_STORAGE_BUCKET_PRIVATE = "s11-verificaciones"
    yield settings
    shutil.rmtree(root, ignore_errors=True)


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


# --------------------------------------------------------------------------- #
# Correo transaccional (Brevo, Sprint 4)
# --------------------------------------------------------------------------- #


class _RespuestaBrevo:
    """Respuesta mínima de `requests`: lo único que el cliente lee es el estado."""

    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"messageId": "<mensaje-de-prueba>"}
        self.cerrada = False

    def json(self) -> dict:
        return self._payload

    def close(self) -> None:
        self.cerrada = True


class DobleBrevo:
    """Doble de `requests.post` que registra cada envío en lugar de hacerlo.

    Por defecto responde `201`, que es lo que Brevo devuelve cuando acepta un mensaje.
    Una prueba puede cambiar `status_code` para simular un rechazo, o fijar `excepcion`
    para simular un fallo de red —un timeout, por ejemplo—.

    El registro está protegido con un `Lock` porque la prueba de concurrencia ejecuta la
    tarea desde dos hilos y cuenta los envíos: sin él, la propia cuenta sería la que
    tendría la condición de carrera.
    """

    def __init__(self):
        self.llamadas: list[SimpleNamespace] = []
        self.status_code = 201
        self.excepcion: Exception | None = None
        self._lock = threading.Lock()

    def __call__(self, url, headers=None, json=None, timeout=None, **kwargs):
        with self._lock:
            self.llamadas.append(
                SimpleNamespace(
                    url=url,
                    headers=dict(headers or {}),
                    payload=json,
                    timeout=timeout,
                    remitente=(json or {}).get("sender"),
                    destinatarios=[d["email"] for d in (json or {}).get("to", [])],
                    asunto=(json or {}).get("subject"),
                    cuerpo=(json or {}).get("textContent"),
                )
            )
        if self.excepcion is not None:
            raise self.excepcion
        return _RespuestaBrevo(self.status_code)

    @property
    def envios(self) -> int:
        return len(self.llamadas)

    @property
    def ultimo(self) -> SimpleNamespace:
        return self.llamadas[-1]

    @property
    def destinatario(self) -> str:
        return self.ultimo.destinatarios[0]


class _RequestsSoloParaBrevo:
    """Sustituto del módulo `requests` **dentro de `brevo`**, con `post` doblado.

    Existe para acotar el parche. `brevo.py` hace `import requests`, de modo que
    `brevo.requests` **es** el módulo global: hacer
    `setattr("…services.brevo.requests.post", doble)` parece acotado por la ruta, pero
    resuelve el atributo sobre el módulo compartido y sustituye `requests.post` en todo
    el proceso, para toda la suite. Hoy no rompe nada —el único otro cliente HTTP del
    proyecto, `core.services.storage`, usa `requests.request`, que es otra función—,
    pero ese aislamiento sería una casualidad, no una garantía: el día que cualquier
    código o dependencia llamara a `requests.post` dentro de una prueba, recibiría en
    silencio el doble de Brevo y un `201` inventado.

    Sustituyendo el **nombre `requests` en el espacio de nombres de `brevo`** el parche
    alcanza exactamente a un módulo. Todo lo que no sea `post` —`RequestException` y
    cualquier atributo que el cliente use en el futuro— se delega al módulo real, así
    que el envoltorio no puede quedarse desactualizado respecto de `brevo.py`.
    """

    def __init__(self, post):
        self.post = post

    def __getattr__(self, nombre):
        # Solo se consulta para lo que no esté en la instancia, es decir, todo menos
        # `post`. `requests` aquí es el módulo de verdad, importado arriba.
        return getattr(requests, nombre)


@pytest.fixture(autouse=True)
def brevo_api(monkeypatch):
    """Dobla el cliente de Brevo. **Ninguna prueba contacta con la API de correo.**

    Es `autouse` a propósito: el envío se dispara desde `on_commit` en pruebas de tres
    apps distintas, y depender de que cada una recuerde pedir el doble convertiría un
    olvido en una llamada real a la API de correo.

    El parche se aplica **solo sobre `brevo`** y no sobre el módulo `requests`
    compartido: ver `_RequestsSoloParaBrevo`. `core.services.storage` y el transporte de
    `google-auth` quedan intactos y siguen doblándose con sus propios mecanismos.
    """
    doble = DobleBrevo()
    monkeypatch.setattr(brevo, "requests", _RequestsSoloParaBrevo(doble))
    return doble
