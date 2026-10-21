"""Generación local por servicios reales; ninguna fixture escribe el ledger."""

from __future__ import annotations

import hashlib
import io
import json
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection, transaction

from .files import LocalObjects, read_manifest, seal, write_json
from .isolation import DatasetError, guard_environment
from .recipe import CATEGORIES, INSTITUTIONS, PRIVACY, Recipe, digest, synthetic_image
from .runtime import Clock, local_runtime


class SyntheticAdapter:
    """Inyección explícita en la orquestación; nunca registrado como proveedor runtime."""

    def __init__(self, case):
        self.case = case

    def classify(self, request):
        return json.dumps(
            {
                "category_slug": self.case["category"],
                "institution_slug": self.case["institution"],
                "priority": self.case["priority"],
                "confidence": 0.99,
                "inconsistency": False,
                "justification": (
                    "S15: clasificación sintética inyectada localmente; no evalúa IA real."
                ),
            }
        )


def _empty_database():
    # Los catálogos migrados y django_migrations son las únicas filas preexistentes admitidas.
    for table in (
        "users",
        "reports",
        "reward_transactions",
        "reward_redemptions",
        "rewards",
        "audit_logs",
        "notifications",
        "x_mock_messages",
        "citizen_profiles",
        "institution_operators",
    ):
        with connection.cursor() as cursor:
            cursor.execute(f'SELECT COUNT(*) FROM "{table}"')
            if cursor.fetchone()[0]:
                raise DatasetError(
                    "Base parcial o ajena: reconstruya un entorno S15 vacío separado."
                )


def _catalogs():
    from apps.admin_app.models import Category, Institution

    institutions = {i.slug: i for i in Institution.objects.all()}
    categories = {c.slug: c for c in Category.objects.select_related("default_institution")}
    if set(institutions) != set(INSTITUTIONS) or set(categories) != {c for c, _ in CATEGORIES}:
        raise DatasetError(
            "El catálogo debe contener exactamente las 3 instituciones y 10 categorías."
        )
    if any(not i.is_active for i in institutions.values()) or any(
        not categories[c].is_active or categories[c].default_institution.slug != i
        for c, i in CATEGORIES
    ):
        raise DatasetError("Catálogos inactivos o asignaciones divergentes.")
    return institutions, categories


def _counts():
    counts = {}
    for table in (
        "reward_redemptions",
        "reward_transactions",
        "report_confirmations",
        "audit_logs",
        "notifications",
    ):
        with connection.cursor() as cursor:
            cursor.execute(f'SELECT COUNT(*) FROM "{table}"')
            counts[table] = cursor.fetchone()[0]
    return counts


def _report_ledger_checkpoint(report_id):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT t.event_type,t.status,t.impact_delta,t.credit_delta,COALESCE(i.slug,''),"
            "t.resolution_cycle FROM reward_transactions t LEFT JOIN institutions i "
            "ON i.id=t.institution_id WHERE t.report_id=%s OR t.confirmation_id IN "
            "(SELECT id FROM report_confirmations WHERE report_id=%s) "
            "ORDER BY t.event_type,t.user_id",
            [report_id, report_id],
        )
        return [list(row) for row in cursor.fetchall()]


def _sql_checkpoint(users, institutions):
    """Observación del estado actual, no un snapshot restaurable ni consulta as-of."""
    balances = {}
    with connection.cursor() as cursor:
        for n, user in enumerate(users[:3]):
            cursor.execute(
                "SELECT COALESCE(SUM(credit_delta),0) FROM reward_transactions "
                "WHERE user_id=%s AND institution_id=%s AND status='ACREDITADO'",
                [user.pk, institutions[INSTITUTIONS[n]].pk],
            )
            balances[f"C{n:02}"] = cursor.fetchone()[0]
    return {"balances": balances, "counts": _counts(), "restorable": False}


def generate(recipe: Recipe, artifact_root: Path, *, dry_run=False, processes_stopped=False):
    root = guard_environment(settings, artifact_root)
    plan = recipe.plan()
    directory = root / recipe.identifier
    manifest_path = directory / "manifest.json"
    # Inspección pura: ni SQL, mkdir, uploads, transacciones, tareas o creación de JWT.
    if dry_run:
        return {
            "state": "dry-run",
            "plan_sha256": digest(plan),
            "plan": plan,
            "artifact_directory": str(directory),
            "effects": [],
        }
    if not processes_stopped:
        raise DatasetError(
            "Confirme --processes-stopped: web, Beat y workers S15 deben estar detenidos."
        )
    if directory.exists():
        if not manifest_path.exists():
            raise DatasetError(
                "Journal/objetos parciales presentes: no se reanuda ni repara el dataset."
            )
        journal_path = directory / "journal.json"
        if (
            not journal_path.exists()
            or json.loads(journal_path.read_text()).get("state") != "complete"
        ):
            raise DatasetError("Journal incompleto; reconstruya un entorno S15 separado.")
        manifest = read_manifest(manifest_path)
        if manifest.get("plan_sha256") != digest(plan):
            raise DatasetError("El manifiesto existente no coincide con las entradas.")
        from .verification import verify

        result = verify(manifest_path, artifact_root=root)
        if result["status"] != "PASS":
            raise DatasetError(
                "Replay divergente: consulte verify_final_dataset y reconstruya aparte."
            )
        return {
            "state": "verified-existing",
            "manifest": str(manifest_path),
            "verification": result,
        }

    _empty_database()
    institutions, categories = _catalogs()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.mkdir(mode=0o700)
    journal_path = directory / "journal.json"
    journal_state = {"state": "incomplete", "version": plan["version"], "plan_sha256": digest(plan)}

    def journal(stage, objects=None):
        journal_state["stage"] = stage
        if objects is not None:
            journal_state["objects"] = list(objects)
        write_json(journal_path, journal_state)

    journal("preflight")
    objects = LocalObjects(directory, journal)
    clock = Clock(recipe.start)
    inputs, actors, report_ids, negative_controls, checkpoints = [], {}, {}, [], {}
    observations = {}

    def photo(logical_id, image_format="JPEG"):
        content = synthetic_image(recipe.seed, logical_id, image_format)
        extension, mime = ("jpg", "image/jpeg") if image_format == "JPEG" else ("png", "image/png")
        filename = f"{logical_id}.{extension}"
        input_directory = directory / "inputs"
        input_directory.mkdir(mode=0o700, exist_ok=True)
        path = input_directory / filename
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(content)
        inputs.append(
            {
                "logical_id": logical_id,
                "path": f"inputs/{filename}",
                "mime": mime,
                "original_sha256": hashlib.sha256(content).hexdigest(),
            }
        )
        return SimpleUploadedFile(filename, content, content_type=mime)

    def action_at(instant, function, **kwargs):
        clock.instant = instant
        return function(**kwargs)

    try:
        with local_runtime(objects, clock) as captured:
            # Transacción DB global. Los objetos externos quedan en journal si algo falla.
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_xact_lock(7315, 1)")
                _empty_database()
                users, operators, admin = _create_actors(recipe, clock, photo, actors, institutions)
                from apps.ai_client_app.orchestration import process_report_classification
                from apps.core.enums import RejectionReason, ReportStatus
                from apps.reports_app.models import Report
                from apps.reports_app.services import (
                    auto_close,
                    confirmations,
                    lifecycle,
                    reports,
                    resolution_evidence,
                    reversal,
                    validation,
                )
                from apps.rewards_app.services import redemptions, rewards
                from apps.social_ingestion_app.services import x_mock

                reward_objects = {}
                for i, slug in enumerate(INSTITUTIONS):
                    # SLAs de demostración, no metas institucionales reales.
                    institutions[slug].sla_hours = (48, 72, None)[i]
                    institutions[slug].save(update_fields=["sla_hours", "updated_at"])
                    reward_objects[slug] = rewards.create_reward(
                        operator=operators[slug],
                        institution=institutions[slug],
                        name=f"Vale simulado S15 {slug}",
                        description="Recompensa sintética S15 sin valor económico real.",
                    )

                for case in recipe.cases():
                    journal(case["id"])
                    instant = datetime.fromisoformat(case["at"])
                    clock.instant = instant
                    slug = case["institution"]
                    operator, institution = operators[slug], institutions[slug]
                    if case["kind"] == "x_mock":
                        items = x_mock.parse_dataset(
                            {
                                "dataset": x_mock.DATASET_NAME,
                                "notice": x_mock.DATASET_NOTICE,
                                "items": [
                                    {
                                        "source_handle": "@ficticio_s15",
                                        "simulated_at": case["at"],
                                        "raw_text": (
                                            "S15 R033: incidente urbano ficticio "
                                            "sin persona de origen."
                                        ),
                                        "category": case["category"],
                                        "latitude": case["latitude"],
                                        "longitude": case["longitude"],
                                    }
                                ],
                            }
                        )
                        x_mock.ingest(items)
                        report = Report.objects.get(x_mock_messages__source_handle="@ficticio_s15")
                    else:
                        report = reports.create_report(
                            citizen=users[case["author"]],
                            category=categories[case["category"]],
                            description=(
                                f"S15 {case['id']}: incidencia urbana sintética "
                                "para validación local."
                            ),
                            photo=photo(case["id"]),
                            latitude=case["latitude"],
                            longitude=case["longitude"],
                        ).report
                        if case["kind"] != "pending":
                            classified = action_at(
                                instant + timedelta(seconds=1),
                                process_report_classification,
                                report_id=report.pk,
                                adapter=SyntheticAdapter(case),
                                model_name="s15-synthetic-adapter",
                                prompt_version="s15-synthetic-v1",
                            )
                            if not classified:
                                raise DatasetError(
                                    "La orquestación sintética no persistió la clasificación."
                                )
                    report_ids[case["id"]] = str(report.pk)
                    if case["id"] == "R002":
                        observations["S01-created"] = _report_ledger_checkpoint(report.pk)
                    for confirmant in case["confirmants"]:
                        action_at(
                            instant + timedelta(seconds=2),
                            confirmations.confirm_report,
                            report_id=report.pk,
                            citizen=users[confirmant],
                        )
                    if case["id"] == "R002":
                        observations["S03-confirmed-pending"] = _report_ledger_checkpoint(report.pk)
                        for name, citizen, code in (
                            (
                                "self-R002",
                                users[case["author"]],
                                confirmations.SELF_CONFIRMATION_NOT_ALLOWED,
                            ),
                            (
                                "duplicate-R002",
                                users[case["confirmants"][0]],
                                confirmations.ALREADY_CONFIRMED,
                            ),
                        ):
                            before = _counts()
                            try:
                                confirmations.confirm_report(report_id=report.pk, citizen=citizen)
                            except confirmations.ConfirmationConflict as exc:
                                if exc.code != code or _counts() != before:
                                    raise DatasetError(
                                        "Control de confirmación divergente o con escrituras."
                                    ) from exc
                                negative_controls.append(
                                    {
                                        "case": name,
                                        "code": exc.code,
                                        "before": before,
                                        "after": _counts(),
                                    }
                                )
                            else:
                                raise DatasetError("Una confirmación no permitida fue aceptada.")
                    if case["kind"] == "pending":
                        continue
                    if case["kind"] == "rejected":
                        action_at(
                            instant + timedelta(seconds=3),
                            validation.reject_report,
                            report_id=report.pk,
                            operator=operator,
                            institution=institution,
                            reason=RejectionReason.DUPLICADO,
                        )
                        continue
                    action_at(
                        instant + timedelta(seconds=3),
                        validation.validate_report,
                        report_id=report.pk,
                        operator=operator,
                        institution=institution,
                    )
                    if case["id"] == "R002":
                        observations["S02-S03-validated"] = _report_ledger_checkpoint(report.pk)
                    if case["kind"] in {"funding", "closed"}:
                        action_at(
                            instant + timedelta(seconds=4),
                            lifecycle.transition_report_status,
                            report_id=report.pk,
                            operator_id=operator.pk,
                            institution_id=institution.pk,
                            target_status=ReportStatus.EN_PROCESO,
                            expected_cycle=1,
                        )
                        action_at(
                            instant + timedelta(seconds=5),
                            resolution_evidence.create_resolution_evidence,
                            report_id=report.pk,
                            operator_id=operator.pk,
                            institution_id=institution.pk,
                            expected_cycle=1,
                            idempotency_key=uuid.uuid5(
                                uuid.NAMESPACE_URL, f"{recipe.identifier}/{case['id']}/evidence/1"
                            ),
                            description=f"Reparación sintética terminada {case['id']}.",
                            uploaded_file=photo(f"{case['id']}-evidence", "PNG"),
                        )
                        action_at(
                            instant + timedelta(seconds=6),
                            lifecycle.transition_report_status,
                            report_id=report.pk,
                            operator_id=operator.pk,
                            institution_id=institution.pk,
                            target_status=ReportStatus.RESUELTO,
                            expected_cycle=1,
                        )
                        if case["id"] == "R002":
                            observations["S04-resolved"] = _report_ledger_checkpoint(report.pk)
                    if case["kind"] == "closed":
                        result = action_at(
                            recipe.operation, auto_close.close_report, report_id=report.pk
                        )
                        if not result.changed:
                            raise DatasetError(
                                "El autocierre no satisfizo las 72 horas del producto."
                            )
                    if case["kind"] == "revalidated":
                        action_at(
                            instant + timedelta(seconds=4),
                            reversal.revert_report_validation,
                            report_id=report.pk,
                            admin_id=admin.pk,
                            expected_cycle=1,
                            reason="Revisión sintética S15 de la primera validación.",
                        )
                        action_at(
                            instant + timedelta(seconds=5),
                            validation.validate_report,
                            report_id=report.pk,
                            operator=operator,
                            institution=institution,
                        )

                checkpoints["before_redemption"] = _sql_checkpoint(users, institutions)
                if checkpoints["before_redemption"]["balances"] != {
                    "C00": 1000,
                    "C01": 1050,
                    "C02": 1000,
                }:
                    raise DatasetError(
                        "La historia real no produjo los preestados económicos exactos."
                    )
                for i, slug in enumerate(INSTITUTIONS):
                    redeemed = action_at(
                        recipe.operation,
                        redemptions.redeem,
                        user_id=users[i].pk,
                        reward_id=reward_objects[slug].pk,
                    )
                    actors[f"redemption_{i}"] = {"id": str(redeemed.redemption.pk)}
                    before = _counts()
                    try:
                        action_at(
                            recipe.operation + timedelta(seconds=1),
                            redemptions.redeem,
                            user_id=users[i].pk,
                            reward_id=reward_objects[slug].pk,
                        )
                    except redemptions.RedemptionConflict as exc:
                        if exc.code != "MONTHLY_LIMIT_REACHED" or _counts() != before:
                            raise DatasetError(
                                "Control mensual divergente o con escrituras."
                            ) from exc
                        negative_controls.append(
                            {
                                "case": f"monthly-{i}",
                                "code": exc.code,
                                "before": before,
                                "after": _counts(),
                            }
                        )
                    else:
                        raise DatasetError("El segundo canje mensual fue aceptado.")
                checkpoints["after_redemption"] = _sql_checkpoint(users, institutions)
                action_at(
                    recipe.operation + timedelta(seconds=2),
                    reversal.revert_report_validation,
                    report_id=report_ids["R001"],
                    admin_id=admin.pk,
                    expected_cycle=1,
                    reason="Reversión sintética S15 posterior al canje; el vale sigue válido.",
                )
                checkpoints["after_reversal"] = _sql_checkpoint(users, institutions)
                before = _counts()
                try:
                    action_at(
                        recipe.next_month,
                        redemptions.redeem,
                        user_id=users[0].pk,
                        reward_id=reward_objects[INSTITUTIONS[0]].pk,
                    )
                except redemptions.RedemptionConflict as exc:
                    if exc.code != "INSUFFICIENT_BALANCE" or _counts() != before:
                        raise DatasetError("Control de saldo divergente o con escrituras.") from exc
                    negative_controls.append(
                        {
                            "case": "next-month-negative-balance",
                            "code": exc.code,
                            "operation_at": recipe.next_month.isoformat(),
                            "before": before,
                            "after": _counts(),
                        }
                    )
                else:
                    raise DatasetError("El saldo negativo permitió un canje en otro mes.")
                clock.instant = recipe.end
                journal("committing", objects.objects)
            # Los callbacks se ejecutan antes de salir del runtime acotado y se capturan.
        document = {
            "state": "complete",
            "plan": plan,
            "plan_sha256": digest(plan),
            "actors": actors,
            "reports": report_ids,
            "inputs": inputs,
            "objects": objects.objects,
            "negative_controls": negative_controls,
            "checkpoints": checkpoints,
            "observations": observations,
            "callbacks": captured,
            "objects_root": "objects",
            "identity": "synthetic-only",
            "uuid_policy": (
                "UUID persistidos pueden variar; casos lógicos, entradas y semántica se verifican."
            ),
        }
        write_json(manifest_path, seal(document))
        journal_state["state"] = "complete"
        journal("complete", objects.objects)
        from .verification import verify

        verification = verify(manifest_path, artifact_root=root)
        if verification["status"] != "PASS":
            raise DatasetError(
                "Dataset creado pero verificación divergente; no se modifica ni repara."
            )
        return {"state": "generated", "manifest": str(manifest_path), "verification": verification}
    except Exception as exc:
        journal_state.update(state="incomplete", error_type=type(exc).__name__)
        journal("failed", objects.objects)
        # No se elimina ningún objeto: el journal conserva diagnóstico para reconstrucción separada.
        raise


def _create_actors(recipe, clock, photo, actors, institutions):
    from apps.admin_app.services import identity_review
    from apps.auth_app.enums import VerificationStatus
    from apps.auth_app.services import identity, profile

    manager = get_user_model().objects

    def create(label, full_name):
        user = manager.create_user(
            id=uuid.uuid5(uuid.NAMESPACE_URL, f"{recipe.identifier}/{label}"),
            email=f"s15-{label.lower()}@example.test",
            full_name=full_name,
            date_joined=clock.now(),
        )
        actors[label] = {"id": str(user.pk), "email": user.email}
        return user

    admin = create("admin", "Administrador Sintético S15")
    call_command("promote_user", admin.email, role="admin", stdout=io.StringIO())
    admin.refresh_from_db()
    operators = {}
    for slug in INSTITUTIONS:
        operator = create(f"operator-{slug}", f"Operador Sintético {slug}")
        call_command(
            "promote_user", operator.email, role="operator", institution=slug, stdout=io.StringIO()
        )
        operator.refresh_from_db()
        operators[slug] = operator
    users = []
    for n in range(12):
        user = create(f"C{n:02}", f"Ciudadano Sintético {n:02}")
        # Canarios ficticios; jamás se exportan en manifiesto, SQL ni logs.
        document = str(99015000 + n)
        submitted = identity.submit_verification(user, document, photo(f"C{n:02}-selfie", "PNG"))
        identity_review.review(
            submitted.pk,
            decision=VerificationStatus.APROBADO,
            document_number=document,
            actor=admin,
        )
        user.refresh_from_db()
        profile.update_ranking_visibility(user, PRIVACY[n % 4], ranking_alias=f"Alias S15 {n:02}")
        users.append(user)
    return users, operators, admin
