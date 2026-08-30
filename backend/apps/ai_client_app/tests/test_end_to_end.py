"""Recorrido completo con Gemini doblado (S8-D).

Tarea → runtime → media → petición → adaptador → parser → mapeo → política →
persistencia → estado → auditoría. Sin red: el cliente del SDK y el
almacenamiento están doblados por completo.

El foco es lo que sale del backend: qué recibe el proveedor y qué no.
"""

import json

import pytest

from apps.admin_app.models import AuditLog
from apps.ai_client_app import tasks as ai_tasks
from apps.ai_client_app.adapters.gemini import GeminiAiClassificationAdapter
from apps.ai_client_app.adapters.runtime import RuntimeAdapter
from apps.ai_client_app.models import AiClassification
from apps.ai_client_app.orchestration import AUDIT_ACTION
from apps.ai_client_app.tasks import classify_report
from apps.core.enums import ActorType, ReportStatus
from apps.core.services import storage
from apps.reports_app.enums import MediaType
from apps.reports_app.models import ReportMedia, ReportStatusHistory

pytestmark = pytest.mark.django_db

MODELO = "gemini-modelo-de-prueba"
JPEG = b"\xff\xd8\xff\xe0 bytes normalizados"

RESPUESTA = {
    "category_slug": "semaforos",
    "institution_slug": "municipalidad-de-asuncion",
    "priority": "ALTA",
    "confidence": 0.95,
    "inconsistency": False,
    "justification": "Semáforo fuera de servicio en cruce principal.",
}


class InteractionsCapturadas:
    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return type("I", (), {"output_text": json.dumps(self._payload), "status": "completed"})()


@pytest.fixture
def gemini(monkeypatch):
    """Instala un adaptador de Gemini real sobre un cliente doblado."""

    def _instalar(payload=None):
        interactions = InteractionsCapturadas(payload or RESPUESTA)
        cliente = type("C", (), {"interactions": interactions})()
        adapter = GeminiAiClassificationAdapter(client=cliente, model=MODELO)
        monkeypatch.setattr(
            ai_tasks,
            "get_runtime_adapter",
            lambda: RuntimeAdapter(adapter=adapter, model_name=MODELO),
        )
        return interactions

    return _instalar


@pytest.fixture
def foto(report, monkeypatch):
    def _crear(clave="reports/abc/def.jpg", contenido=JPEG):
        ReportMedia.objects.create(
            report=report,
            file_url=clave,
            file_hash="1" * 64,
            media_type=MediaType.FOTO,
        )
        monkeypatch.setattr(storage, "download_private", lambda bucket, key: contenido)
        monkeypatch.setattr(storage, "reports_bucket", lambda: "reportes")

    return _crear


# --------------------------------------------------------------------------- #
# Recorrido completo
# --------------------------------------------------------------------------- #


def test_clasifica_de_extremo_a_extremo_con_imagen(report, gemini, foto, institution):
    foto()
    interactions = gemini()

    assert classify_report(str(report.pk)) is None

    report.refresh_from_db()
    assert report.status == ReportStatus.CLASIFICADO
    assert report.category.slug == "semaforos"
    assert report.institution == institution

    clasificacion = AiClassification.objects.get()
    assert clasificacion.model_name == MODELO
    assert clasificacion.justification == RESPUESTA["justification"]

    assert ReportStatusHistory.objects.filter(new_status=ReportStatus.CLASIFICADO).count() == 1

    entrada = AuditLog.objects.get(action=AUDIT_ACTION)
    assert entrada.actor_type == ActorType.AI
    assert entrada.payload["prompt_version"] == "s8-classification-v1"
    assert entrada.payload["model_name"] == MODELO

    assert len(interactions.calls) == 1


def test_clasifica_sin_imagen_cuando_el_reporte_no_tiene(report, gemini):
    interactions = gemini()

    classify_report(str(report.pk))

    bloques = interactions.calls[0]["input"]
    assert all(b["type"] != "image" for b in bloques)

    report.refresh_from_db()
    assert report.status == ReportStatus.CLASIFICADO


def test_la_imagen_llega_inline_al_proveedor(report, gemini, foto):
    import base64

    foto()
    interactions = gemini()

    classify_report(str(report.pk))

    imagen = next(b for b in interactions.calls[0]["input"] if b["type"] == "image")
    assert base64.b64decode(imagen["data"]) == JPEG
    assert imagen["mime_type"] == "image/jpeg"


# --------------------------------------------------------------------------- #
# Qué NO sale del backend
# --------------------------------------------------------------------------- #


def test_el_proveedor_no_recibe_identidad_ubicacion_ni_rutas(report, gemini, foto):
    foto()
    interactions = gemini()

    classify_report(str(report.pk))

    enviado = json.dumps(interactions.calls[0])
    # Identificadores únicos: si aparecen, es porque se filtraron. El nombre del
    # bucket no se busca por substring porque «reportes» es además una palabra del
    # propio prompt; su ausencia se comprueba sobre la estructura, más abajo.
    for prohibido in (
        str(report.pk),
        str(report.citizen_id),
        "reports/abc/def.jpg",
        "-25.2",
        "-57.6",
        "file_hash",
        "1" * 64,
    ):
        assert prohibido not in enviado, f"{prohibido} llegó al proveedor"

    # La imagen viaja como bytes y su tipo, sin nada que diga dónde vive el objeto.
    imagen = next(b for b in interactions.calls[0]["input"] if b["type"] == "image")
    assert set(imagen) == {"type", "data", "mime_type"}

    # La petición solo lleva las claves del contrato de la Interactions API.
    assert set(interactions.calls[0]) == {
        "model",
        "system_instruction",
        "input",
        "store",
        "response_format",
    }


def test_la_auditoria_no_guarda_la_imagen_ni_la_ruta(report, gemini, foto):
    foto()
    gemini()

    classify_report(str(report.pk))

    payload = AuditLog.objects.get(action=AUDIT_ACTION).payload
    texto = json.dumps(payload)
    assert "reports/abc/def.jpg" not in texto
    assert report.description not in texto
    assert set(payload).isdisjoint({"image", "prompt", "storage_path", "signed_url", "api_key"})


def test_la_credencial_no_aparece_en_la_auditoria(report, gemini, settings):
    settings.GEMINI_API_KEY = "test-api-key-not-real"
    gemini()

    classify_report(str(report.pk))

    assert "test-api-key-not-real" not in json.dumps(
        AuditLog.objects.get(action=AUDIT_ACTION).payload
    )


# --------------------------------------------------------------------------- #
# Fallos de imagen
# --------------------------------------------------------------------------- #


def test_una_imagen_ausente_no_clasifica_en_silencio(report, gemini, monkeypatch):
    """Si hay evidencia registrada, no se clasifica sin ella sin que nadie lo note."""
    ReportMedia.objects.create(
        report=report, file_url="reports/a/b.jpg", file_hash="2" * 64, media_type=MediaType.FOTO
    )
    monkeypatch.setattr(storage, "reports_bucket", lambda: "reportes")

    def ausente(bucket, key):
        raise storage.StorageObjectNotFoundError("404")

    monkeypatch.setattr(storage, "download_private", ausente)
    interactions = gemini()

    from apps.ai_client_app.exceptions import PermanentAiMediaError

    with pytest.raises(PermanentAiMediaError):
        classify_report(str(report.pk))

    assert interactions.calls == [], "no debe llamarse al proveedor sin la evidencia"
    report.refresh_from_db()
    assert report.status == ReportStatus.CREADO
    assert AiClassification.objects.count() == 0
