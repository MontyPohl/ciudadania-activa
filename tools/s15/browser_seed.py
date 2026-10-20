"""Prepara y contrasta el navegador S15 sobre una copia restaurada propia.

Sin ledger directo, OAuth, IA, correo real ni cambios en los seeds históricos.
Las sesiones y el canario prospectivo se escriben solo en JSON privado.
"""

import argparse
import hashlib
import hmac
import json
import os
import secrets
import uuid
from datetime import UTC, datetime
from pathlib import Path

from tools.s15.api_flows import month_bounds, synthetic_photo
from tools.s15.browser_profile import validate_browser_environment
from tools.s15.reconcile_creation import private_path, read_private, write_private

NEARBY = {"lat": -25.2900, "lng": -57.6200}
F2_POINT = {"lat": -25.2902, "lng": -57.6202}


def query(sql, parameters=()):
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute(sql, parameters)
        return cursor.fetchall()


def balance(user_id, institution_id):
    return query(
        "SELECT COALESCE(SUM(credit_delta),0) FROM reward_transactions WHERE user_id=%s "
        "AND institution_id=%s AND status='ACREDITADO'",
        [user_id, institution_id],
    )[0][0]


def points(user_id):
    start, end = month_bounds(datetime.now(UTC))
    return query(
        "SELECT COALESCE(SUM(impact_delta),0) FROM reward_transactions WHERE user_id=%s "
        "AND status='ACREDITADO' AND credited_at >= %s AND credited_at < %s "
        "AND event_type != 'REDEMPTION'",
        [user_id, start, end],
    )[0][0]


def ensure(actual, expected, name):
    if actual != expected:
        raise ValueError("Precondición SQL divergente: " + name)


def upload(run_id, label):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile("s15.png", synthetic_photo(run_id + label), content_type="image/png")


def prepare(manifest_path, actors_path, receipt_path):
    from django.contrib.auth import get_user_model
    from django.db import transaction
    from rest_framework_simplejwt.tokens import RefreshToken

    from apps.admin_app.models import Category, Institution
    from apps.auth_app.services import profile
    from apps.core.enums import ReportStatus
    from apps.core.final_dataset.files import read_manifest
    from apps.reports_app.services import lifecycle, reports, resolution_evidence, validation
    from apps.rewards_app.models import Reward

    manifest_bytes = read_private(manifest_path)
    manifest = read_manifest(Path(manifest_path))
    private_path(actors_path, output=True)
    private_path(receipt_path, output=True)
    ensure(query("SELECT COUNT(*) FROM reports")[0][0], 36, "copia original36")
    ensure(
        query("SELECT COUNT(*) FROM users WHERE email=%s", ["s15-browser-f1@example.test"])[0][0],
        0,
        "actor F1 nuevo",
    )
    run_id = "browser-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    started = receipt_path.with_name("browser-seed-started.json")
    write_private(started, {"runId": run_id, "state": "preparing", "prospective": True})
    users = get_user_model().objects
    author = users.get(pk=manifest["actors"]["C10"]["id"])
    confirmer = users.get(pk=manifest["actors"]["C11"]["id"])
    admin = users.get(pk=manifest["actors"]["admin"]["id"])
    operator = users.get(pk=manifest["actors"]["operator-ande"]["id"])
    institution = Institution.objects.get(slug="ande", is_active=True)
    category = Category.objects.get(slug="alumbrado-publico", is_active=True)
    reward = Reward.objects.get(institution=institution, active=True)
    ensure(balance(author.pk, institution.pk), 10, "créditos históricos C10 ANDE")
    canonical_document = "99015" + f"{secrets.randbelow(100000):05}"
    formatted_document = f"{int(canonical_document):,}".replace(",", ".")
    description = f"S15 navegador {run_id}: luminaria apagada en esquina sintética."
    funded = []
    with transaction.atomic():
        applicant = users.create_user(
            email="s15-browser-f1@example.test", full_name="Ciudadana S15 pendiente de identidad"
        )
        profile.update_ranking_visibility(author, "ALIAS", "Ciudadana S15 A")
        for index in range(9):
            report = reports.create_report(
                citizen=author,
                category=category,
                description=f"S15 preestado navegador {run_id}/{index}",
                photo=upload(run_id, f"fund-{index}"),
                latitude=-25.3100 - index * 0.002,
                longitude=-57.6400,
            ).report
            validation.validate_report(
                report_id=report.pk, operator=operator, institution=institution
            )
            lifecycle.transition_report_status(
                report_id=report.pk,
                operator_id=operator.pk,
                institution_id=institution.pk,
                target_status=ReportStatus.EN_PROCESO,
                expected_cycle=1,
            )
            resolution_evidence.create_resolution_evidence(
                report_id=report.pk,
                operator_id=operator.pk,
                institution_id=institution.pk,
                expected_cycle=1,
                idempotency_key=uuid.uuid4(),
                description="Evidencia sintética S15 del preestado de navegador.",
                uploaded_file=upload(run_id, f"fund-evidence-{index}"),
            )
            lifecycle.transition_report_status(
                report_id=report.pk,
                operator_id=operator.pk,
                institution_id=institution.pk,
                target_status=ReportStatus.RESUELTO,
                expected_cycle=1,
            )
            funded.append(str(report.pk))
        neighbor = reports.create_report(
            citizen=confirmer,
            category=category,
            description=f"Vecino sintético para aviso de duplicados {run_id}.",
            photo=upload(run_id, "nearby"),
            latitude=NEARBY["lat"],
            longitude=NEARBY["lng"],
        ).report
        ensure(balance(author.pk, institution.pk), 910, "preestado financiado por servicios")
        ranking_points = points(author.pk)
        ensure(
            query("SELECT COUNT(*) FROM citizen_profiles WHERE user_id=%s", [applicant.pk])[0][0],
            0,
            "actor F1 sin perfil",
        )
    actors = {
        label: {
            "id": str(user.pk),
            "email": user.email,
            "refresh": str(RefreshToken.for_user(user)),
        }
        for label, user in [
            ("citizen_a", author),
            ("citizen_b", confirmer),
            ("citizen_c", applicant),
            ("operator", operator),
            ("admin", admin),
        ]
    }
    actors.update(
        {
            "schemaVersion": 1,
            "runId": run_id,
            "datasetSha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "referenceMonth": month_bounds(datetime.now(UTC))[0].strftime("%Y-%m"),
            "ande_id": str(institution.pk),
            "nearby": NEARBY,
            "f2_point": F2_POINT,
            "cedula_c": formatted_document,
            "reward_id": str(reward.pk),
            "reward_name": reward.name,
            "ranking_name": "Ciudadana S15 A",
            "report_description": description,
            "expected": {
                "ande_balance_before": 910,
                "after_validation": 960,
                "after_resolution": 1010,
                "after_redemption": 10,
                "ranking_points_after_resolution": ranking_points + 100,
            },
        }
    )
    write_private(actors_path, actors)
    receipt = {
        "state": "prepared",
        "runId": run_id,
        "datasetSha256": actors["datasetSha256"],
        "actorsSha256": hashlib.sha256(Path(actors_path).read_bytes()).hexdigest(),
        "instrumentSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "reportsPrepared": funded,
        "neighborReport": str(neighbor.pk),
        "reportsTotal": query("SELECT COUNT(*) FROM reports")[0][0],
        "canaryChosenBeforeRequests": True,
        "notVerified": ["navegador todavía no ejecutado", "OAuth/IA/correo reales"],
    }
    write_private(receipt_path, receipt)
    return {"state": "prepared", "reportsTotal": receipt["reportsTotal"], "actors": 5}


def check_audits(rows, expected, check, prefix):
    """Contrasta actores/tipo/payload contra fuentes SQL, sin confiar en el evento."""
    check(
        prefix + ": acciones completas y en orden",
        [row[0] for row in rows],
        [row[0] for row in expected],
    )
    actual_by_action = {row[0]: row for row in rows}
    for action, actor, payload in expected:
        row = actual_by_action.get(action)
        check(
            prefix + ": actor humano " + action,
            [str(row[1]), row[2]] if row else None,
            [actor, "human"],
        )
        actual_payload = row[3] if row else None
        # Django puede devolver JSONB como texto desde un cursor SQL crudo.
        if isinstance(actual_payload, str):
            try:
                actual_payload = json.loads(actual_payload)
            except (ValueError, RecursionError):
                actual_payload = None
        if isinstance(actual_payload, dict):
            actual_payload = dict(actual_payload)
            for field in ("credited_transaction_ids", "evidence_ids"):
                if isinstance(actual_payload.get(field), list):
                    actual_payload[field] = sorted(actual_payload[field])
        check(prefix + ": vínculos " + action, actual_payload, payload)


def check_report_trace(actors, journey, report_id, check):
    author, confirmer, operator = (
        actors[name]["id"] for name in ("citizen_a", "citizen_b", "operator")
    )
    institution = actors["ande_id"]
    report_id = str(report_id)
    history = query(
        "SELECT previous_status,new_status,resolution_cycle,changed_by_id "
        "FROM report_status_history WHERE report_id=%s ORDER BY created_at,id",
        [report_id],
    )
    check(
        "historial UI completo/ciclo/actores",
        [[a, b, c, str(d)] for a, b, c, d in history],
        [
            ["", "CREADO", 0, author],
            ["CREADO", "ASIGNADO", 1, operator],
            ["ASIGNADO", "EN_PROCESO", 1, operator],
            ["EN_PROCESO", "RESUELTO", 1, operator],
        ],
    )
    evidence = query(
        "SELECT id,resolution_cycle,uploaded_by_id,evidence_url,description,content_hash "
        "FROM resolution_evidences WHERE report_id=%s ORDER BY created_at,id",
        [report_id],
    )
    check("evidencia UI única", len(evidence), 1)
    evidence_id = None
    if len(evidence) == 1:
        eid, cycle, uploader, key, description, content_hash = evidence[0]
        evidence_id = str(eid)
        check("evidencia UI ciclo/operador", [cycle, str(uploader)], [1, operator])
        check(
            "evidencia UI referencia de objeto",
            key
            in {
                f"resolution-evidence/{report_id}/1/{evidence_id}.png",
                f"resolution-evidence/{report_id}/1/{evidence_id}.jpg",
            },
            True,
        )
        check(
            "evidencia UI descripción y huella",
            bool(description.strip())
            and len(content_hash) == 64
            and all(c in "0123456789abcdef" for c in content_hash),
            True,
        )
        if journey.get("evidenceId") is not None:
            check("evidencia UI ID=SQL", journey["evidenceId"], evidence_id)
    confirmations = query(
        "SELECT id FROM report_confirmations WHERE report_id=%s AND citizen_id=%s",
        [report_id, confirmer],
    )
    check("confirmación UI única", len(confirmations), 1)
    confirmation_id = str(confirmations[0][0]) if len(confirmations) == 1 else None
    tx = query(
        "SELECT t.id,t.event_type,t.user_id,t.resolution_cycle,t.institution_id,t.confirmation_id "
        "FROM reward_transactions t LEFT JOIN report_confirmations c ON c.id=t.confirmation_id "
        "WHERE t.report_id=%s OR c.report_id=%s ORDER BY t.event_type,t.id",
        [report_id, report_id],
    )
    check("cuatro fuentes económicas UI", len(tx), 4)
    tx_ids = {kind: str(uid) for uid, kind, *_ in tx}
    expected_kinds = [
        "CONFIRMATION_CREATED",
        "REPORT_CREATED",
        "RESOLUTION_BONUS",
        "VALIDATION_BONUS",
    ]
    check("fuentes económicas UI únicas", sorted(tx_ids), expected_kinds)
    for _, kind, user_id, cycle, inst, confirmed in tx:
        check(
            "fuente económica UI sujeto/ciclo " + kind,
            [str(user_id), cycle, str(inst), str(confirmed) if confirmed else None],
            [
                confirmer if kind == "CONFIRMATION_CREATED" else author,
                1,
                institution,
                confirmation_id if kind == "CONFIRMATION_CREATED" else None,
            ],
        )
    category_id = str(query("SELECT category_id FROM reports WHERE id=%s", [report_id])[0][0])
    base = {"report_id": report_id, "institution_id": institution, "cycle": 1}
    expected = [
        (
            "report.created",
            author,
            {"report_id": report_id, "category_id": category_id, "status": "CREADO"},
        ),
        (
            "report.confirmed",
            confirmer,
            {"report_id": report_id, "confirmation_id": confirmation_id},
        ),
        (
            "report.validated",
            operator,
            {
                **base,
                "credited_transaction_ids": sorted(
                    [
                        tx_ids.get("REPORT_CREATED", "missing"),
                        tx_ids.get("CONFIRMATION_CREATED", "missing"),
                    ]
                ),
                "validation_bonus_transaction_id": tx_ids.get("VALIDATION_BONUS"),
            },
        ),
        (
            "report.processing_started",
            operator,
            {**base, "previous_status": "ASIGNADO", "new_status": "EN_PROCESO"},
        ),
        ("report.resolution_evidence_created", operator, {**base, "evidence_id": evidence_id}),
        (
            "report.resolved",
            operator,
            {
                **base,
                "previous_status": "EN_PROCESO",
                "new_status": "RESUELTO",
                "evidence_ids": [evidence_id],
                "resolution_bonus_transaction_id": tx_ids.get("RESOLUTION_BONUS"),
            },
        ),
    ]
    audit = query(
        "SELECT action,actor_user_id,actor_type,payload FROM audit_logs "
        "WHERE payload->>'report_id'=%s ORDER BY created_at,id",
        [report_id],
    )
    check_audits(audit, expected, check, "auditoría reporte UI")


def verify(actors_path, dump_path, logs, journey_path, output):
    from django.conf import settings

    actors = json.loads(read_private(actors_path))
    journey = json.loads(read_private(journey_path))
    checks = []

    def check(name, actual, expected):
        checks.append({"name": name, "passed": actual == expected})
        # Nunca adjuntar el valor del canario ni secretos en los checks o errores.

    author, confirmer, applicant = (
        actors[name]["id"] for name in ("citizen_a", "citizen_b", "citizen_c")
    )
    institution = actors["ande_id"]
    reports = query(
        "SELECT id,status,resolution_cycle,institution_id,citizen_id FROM reports "
        "WHERE description=%s",
        [actors["report_description"]],
    )
    check("reporte UI único", len(reports), 1)
    check("UI completó F1…F8", journey.get("completedFlows"), [f"F{n}" for n in range(1, 9)])
    check("UI corresponde a la corrida", journey.get("runId"), actors["runId"])
    check("UI esquema de recibo", journey.get("schemaVersion"), 1)
    check("UI dataset congelado", journey.get("datasetSha256"), actors["datasetSha256"])
    check(
        "UI fuente real declarada",
        journey.get("source"),
        "browser-bff-django-postgis-redis-local-storage",
    )
    check("UI contextos cerrados sin errores", journey.get("contextCloseErrors"), 0)
    check("UI dashboard capturado", journey.get("dashboardCaptured"), True)
    check("UI período esperado", journey.get("referenceMonth"), actors["referenceMonth"])
    check(
        "UI sujetos de la corrida",
        journey.get("actorIds"),
        {
            name: actors[name]["id"]
            for name in ("citizen_a", "citizen_b", "citizen_c", "operator", "admin")
        },
    )
    if len(reports) == 1:
        report_id, status, cycle, inst, citizen = reports[0]
        check("ID UI=SQL", journey.get("reportId"), str(report_id))
        check(
            "reporte UI resuelto ciclo1",
            [status, cycle, str(inst), str(citizen)],
            ["RESUELTO", 1, institution, author],
        )
        ledger = query(
            "SELECT event_type,status,impact_delta,credit_delta FROM reward_transactions "
            "WHERE report_id=%s AND user_id=%s ORDER BY event_type",
            [report_id, author],
        )
        check(
            "ledger autor del reporte UI",
            [list(row) for row in ledger],
            [
                ["REPORT_CREATED", "ACREDITADO", 20, 20],
                ["RESOLUTION_BONUS", "ACREDITADO", 50, 50],
                ["VALIDATION_BONUS", "ACREDITADO", 30, 30],
            ],
        )
        confirmation = query(
            "SELECT t.status,t.impact_delta,t.credit_delta FROM reward_transactions t "
            "JOIN report_confirmations c ON c.id=t.confirmation_id "
            "WHERE c.report_id=%s AND c.citizen_id=%s",
            [report_id, confirmer],
        )
        check(
            "confirmante UI acredita10/10",
            [list(row) for row in confirmation],
            [["ACREDITADO", 10, 10]],
        )
        check_report_trace(actors, journey, report_id, check)
    check(
        "saldo institucional después del canje",
        balance(author, institution),
        actors["expected"]["after_redemption"],
    )
    check(
        "ranking mensual después del flujo",
        points(author),
        actors["expected"]["ranking_points_after_resolution"],
    )
    month = month_bounds(datetime.now(UTC))[0].date()
    redemption = query(
        "SELECT id,credits_spent FROM reward_redemptions WHERE user_id=%s "
        "AND institution_id=%s AND period_month=%s",
        [author, institution, month],
    )
    check("canje UI único", len(redemption), 1)
    if len(redemption) == 1:
        check(
            "canje UI=SQL",
            [journey.get("redemptionId"), 1000],
            [str(redemption[0][0]), redemption[0][1]],
        )
        debit = query(
            "SELECT id,impact_delta,credit_delta,status FROM reward_transactions "
            "WHERE redemption_id=%s AND user_id=%s AND institution_id=%s",
            [redemption[0][0], author, institution],
        )
        check("débito del canje UI", [list(row[1:]) for row in debit], [[0, -1000, "ACREDITADO"]])
        debit_id = str(debit[0][0]) if len(debit) == 1 else None
        audit = query(
            "SELECT action,actor_user_id,actor_type,payload FROM audit_logs "
            "WHERE payload->>'redemption_id'=%s ORDER BY created_at,id",
            [str(redemption[0][0])],
        )
        check_audits(
            audit,
            [
                (
                    "reward.redeemed",
                    author,
                    {
                        "redemption_id": str(redemption[0][0]),
                        "reward_id": actors["reward_id"],
                        "institution_id": institution,
                        "period": actors["referenceMonth"],
                        "credits_spent": 1000,
                        "balance_before": actors["expected"]["after_resolution"],
                        "balance_after": actors["expected"]["after_redemption"],
                        "ledger_transaction_id": debit_id,
                    },
                )
            ],
            check,
            "auditoría canje UI",
        )
    canonical = actors["cedula_c"].replace(".", "")
    expected_hmac = hmac.new(
        settings.DOCUMENT_HMAC_SECRET.encode(), canonical.encode(), hashlib.sha256
    ).hexdigest()
    identity = query(
        "SELECT p.id,p.document_hmac,u.verification_status,p.verified_by_id "
        "FROM citizen_profiles p JOIN users u ON u.id=p.user_id WHERE p.user_id=%s",
        [applicant],
    )
    check("perfil F1 único", len(identity), 1)
    if len(identity) == 1:
        profile_id, actual_hmac, status, reviewer = identity[0]
        check("HMAC independiente correcto", hmac.compare_digest(actual_hmac, expected_hmac), True)
        check("F1 aprobado por admin", [status, str(reviewer)], ["APROBADO", actors["admin"]["id"]])
        audit = query(
            "SELECT action,actor_user_id,actor_type,payload FROM audit_logs "
            "WHERE payload->>'profile_id'=%s "
            "ORDER BY created_at,id",
            [str(profile_id)],
        )
        check_audits(
            audit,
            [
                (
                    "identity.submitted",
                    applicant,
                    {"profile_id": str(profile_id), "user_id": applicant},
                ),
                (
                    "identity.approved",
                    actors["admin"]["id"],
                    {"profile_id": str(profile_id), "target_user_id": applicant},
                ),
            ],
            check,
            "auditoría identidad UI",
        )
        check(
            "auditoría sin número textual",
            any(
                canonical in json.dumps(payload) or actors["cedula_c"] in json.dumps(payload)
                for *_, payload in audit
            ),
            False,
        )
    patterns = [canonical.encode(), actors["cedula_c"].encode()]
    inspected = []
    for label, path in [("database-logical-dump", dump_path), *[("product-log", p) for p in logs]]:
        content = read_private(path)
        if label == "database-logical-dump":
            check(
                "dump lógico contiene perfil observado",
                b"-- PostgreSQL database dump" in content
                and expected_hmac.encode() in content
                and applicant.encode() in content,
                True,
            )
        occurrences = sum(content.count(pattern) for pattern in patterns)
        check(label + ": canario ausente", occurrences, 0)
        inspected.append(
            {
                "surface": label,
                "sha256": hashlib.sha256(content).hexdigest(),
                "bytes": len(content),
                "occurrences": occurrences,
            }
        )
    result = {
        "state": "verified",
        "status": "PASS" if all(c["passed"] for c in checks) else "FAIL",
        "runId": actors["runId"],
        "checks": checks,
        "inspected": inspected,
        "prospectiveCanary": True,
        "canaryInputsExcluded": ["actors JSON", "selfie de entrada", "vídeos/trazas privados"],
        "instrumentSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "limitations": [
            "No adjudica HT3 general",
            "Solo superficies inspeccionadas",
            "No clasificador/OAuth/correo reales",
        ],
    }
    write_private(output, result)
    return {
        "status": result["status"],
        "checks": len(checks),
        "passed": sum(c["passed"] for c in checks),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["prepare", "verify"])
    parser.add_argument("--actors", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--logical-dump", type=Path)
    parser.add_argument("--journey-result", type=Path)
    parser.add_argument("--product-log", type=Path, action="append", default=[])
    args = parser.parse_args()
    validate_browser_environment(os.environ)
    import django

    django.setup()
    from django.conf import settings

    if settings.DEBUG is not False:
        raise SystemExit("Navegador S15 requiere DEBUG=False.")
    if args.mode == "prepare":
        if not args.manifest:
            parser.error("prepare requiere --manifest")
        result = prepare(args.manifest, args.actors, args.output)
    else:
        if not args.logical_dump or not args.journey_result:
            parser.error("verify requiere --logical-dump y --journey-result")
        result = verify(
            args.actors, args.logical_dump, args.product_log, args.journey_result, args.output
        )
    print(json.dumps(result))
    return 1 if result.get("status") == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
