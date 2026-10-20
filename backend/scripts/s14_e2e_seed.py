"""Siembra exclusivamente el E2E local del S14; nunca se apunta a producción.

Se ejecuta mediante ``manage.py shell`` con una base nueva y el Storage falso
levantado. Las sesiones se escriben únicamente en ``/out/actors.json``.

Se usa el servicio existente ``reports.create_report``, cuyo ``on_commit`` ya
encola IA. En este E2E, ``AI_CLASSIFICATION_ENABLED`` queda sin definir y no hay
worker: las tareas permanecen en Redis local (base lógica 4), sin ejecutarse.
La prohibición de añadir caminos nuevos de encolado aplica al código de producto,
no a este seed que reutiliza el servicio.
"""

import io
import json
import os
import tempfile
import uuid
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from PIL import Image
from rest_framework_simplejwt.tokens import RefreshToken

from apps.admin_app.models import Category, Institution, InstitutionOperator
from apps.admin_app.services import identity_review
from apps.auth_app.enums import UserRole, VerificationStatus
from apps.auth_app.services import identity
from apps.core.enums import ReportStatus
from apps.reports_app.services import lifecycle, reports, resolution_evidence, validation
from apps.rewards_app import selectors
from apps.rewards_app.services import rewards

NEARBY = {"lat": -25.2900, "lng": -57.6200}
F2_POINT = {"lat": -25.2902, "lng": -57.6202}
CEDULA_C = "9900014"
ACTORS_PATH = Path("/out/actors.json")


def _guard_environment() -> None:
    """Rechaza entornos ajenos al arnés antes de toda escritura."""
    if settings.DEBUG is not True:
        raise SystemExit("Seed S14 rechazado: DEBUG debe estar habilitado.")
    database = settings.DATABASES["default"]
    if database.get("NAME") != "ciudadania_s14e2e":
        raise SystemExit("Seed S14 rechazado: la base no es la exclusiva del E2E.")
    if database.get("HOST") not in {"s12-dev-db", "s14-dev-db", "localhost", "127.0.0.1"}:
        raise SystemExit("Seed S14 rechazado: el host de base no es local autorizado.")
    storage_url = getattr(settings, "SUPABASE_URL", "")
    if not isinstance(storage_url, str) or not storage_url.startswith("http://127.0.0.1:"):
        raise SystemExit("Seed S14 rechazado: Storage no apunta al servidor falso local.")
    if get_user_model().objects.exists():
        raise SystemExit("Seed S14 rechazado: la base ya contiene usuarios.")
    if not ACTORS_PATH.parent.is_dir():
        raise SystemExit("Seed S14 rechazado: falta el directorio externo de salida.")


def _photo(index: int, *, image_format: str) -> SimpleUploadedFile:
    """Genera contenido sintético distinto para no repetir ningún file_hash."""
    output = io.BytesIO()
    color = (index, (index * 37) % 256, (index * 73) % 256)
    with Image.new("RGB", (96, 96), color=color) as image:
        image.save(output, format=image_format)
    extension, content_type = (
        ("jpg", "image/jpeg") if image_format == "JPEG" else ("png", "image/png")
    )
    return SimpleUploadedFile(
        f"s14-sintetica-{index}.{extension}", output.getvalue(), content_type=content_type
    )


def _approve_citizen(user, *, admin, document_number: str, photo_index: int) -> None:
    """Crea el perfil por servicios: sin él el ranking muestra Usuario privado."""
    profile = identity.submit_verification(
        user,
        document_number=document_number,
        selfie=_photo(photo_index, image_format="PNG"),
    )
    identity_review.review(
        profile.pk,
        decision=VerificationStatus.APROBADO,
        document_number=document_number,
        actor=admin,
    )
    user.refresh_from_db()


def _seed_resolved_report(*, index: int, citizen, operator, institution, category) -> None:
    """Recorre creación, validación, atención, evidencia y resolución reales."""
    # Hay más de 200 m entre estos puntos y más de 1 km hasta el reporte vecino.
    result = reports.create_report(
        citizen=citizen,
        category=category,
        description=f"Alumbrado sintético resuelto para el saldo E2E, caso {index}.",
        photo=_photo(index, image_format="JPEG"),
        latitude=-25.3000 - (index - 1) * 0.002,
        longitude=-57.6400,
    )
    report_id = result.report.pk
    validation.validate_report(report_id=report_id, operator=operator, institution=institution)
    lifecycle.transition_report_status(
        report_id=report_id,
        operator_id=operator.pk,
        institution_id=institution.pk,
        target_status=ReportStatus.EN_PROCESO,
        expected_cycle=1,
    )
    resolution_evidence.create_resolution_evidence(
        report_id=report_id,
        operator_id=operator.pk,
        institution_id=institution.pk,
        expected_cycle=1,
        idempotency_key=uuid.uuid4(),
        description=f"Reparación sintética finalizada en el caso E2E {index}.",
        uploaded_file=_photo(index + 10, image_format="PNG"),
    )
    lifecycle.transition_report_status(
        report_id=report_id,
        operator_id=operator.pk,
        institution_id=institution.pk,
        target_status=ReportStatus.RESUELTO,
        expected_cycle=1,
    )


def _write_actors(actors: dict) -> None:
    """Publica el archivo atómicamente, sin un instante con permisos amplios."""
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=ACTORS_PATH.parent, prefix=".actors-", delete=False
        ) as output:
            temporary_path = Path(output.name)
            os.fchmod(output.fileno(), 0o600)
            json.dump(actors, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, ACTORS_PATH)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def main() -> None:
    _guard_environment()
    identity.normalize_document(CEDULA_C)
    call_command("seed_catalogs", stdout=io.StringIO())
    institution = Institution.objects.get(slug="ande", is_active=True)
    category = Category.objects.get(slug="alumbrado-publico", is_active=True)
    users = get_user_model().objects
    citizen_a = users.create_user(email="s14-a@example.test", full_name="Ciudadana E2E A")
    citizen_b = users.create_user(email="s14-b@example.test", full_name="Ciudadano E2E B")
    citizen_c = users.create_user(email="s14-c@example.test", full_name="Ciudadana E2E C")
    operator = users.create_user(
        email="s14-operador@example.test", full_name="Operador E2E", role=UserRole.OPERATOR
    )
    admin = users.create_user(
        email="s14-admin@example.test", full_name="Admin E2E", role=UserRole.ADMIN, is_staff=False
    )
    InstitutionOperator.objects.create(user=operator, institution=institution, active=True)
    _approve_citizen(citizen_a, admin=admin, document_number="9900012", photo_index=21)
    _approve_citizen(citizen_b, admin=admin, document_number="9900013", photo_index=22)

    for index in range(1, 10):
        _seed_resolved_report(
            index=index,
            citizen=citizen_a,
            operator=operator,
            institution=institution,
            category=category,
        )
    if selectors.institution_balance(user_id=citizen_a.pk, institution_id=institution.pk) != 900:
        raise SystemExit("Seed S14 abortado: los servicios no produjeron el saldo previsto.")
    reports.create_report(
        citizen=citizen_b,
        category=category,
        description="Alumbrado sintético vecino para el aviso de posible duplicado E2E.",
        photo=_photo(10, image_format="JPEG"),
        latitude=NEARBY["lat"],
        longitude=NEARBY["lng"],
    )
    rewards.create_reward(
        operator=operator,
        institution=institution,
        name="Vale simulado E2E",
        description="Vale sintético del E2E local del Sprint 14, sin valor económico real.",
    )

    actors = {
        name: {"id": str(user.pk), "email": user.email, "refresh": str(RefreshToken.for_user(user))}
        for name, user in (
            ("citizen_a", citizen_a),
            ("citizen_b", citizen_b),
            ("citizen_c", citizen_c),
            ("operator", operator),
            ("admin", admin),
        )
    }
    actors.update(ande_id=str(institution.pk), nearby=NEARBY, f2_point=F2_POINT, cedula_c=CEDULA_C)
    _write_actors(actors)


if __name__ == "__main__":
    main()
