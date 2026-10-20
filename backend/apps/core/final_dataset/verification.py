"""Verificador SQL independiente: no llama al generador, servicios ni selectores."""

from __future__ import annotations

import hashlib
import io
import json
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.db import connection
from PIL import Image

from .files import LocalObjects, read_manifest
from .isolation import DatasetError, guard_environment
from .oracle import expected_history, expected_ledger
from .recipe import CATEGORIES, INSTITUTIONS, PRIVACY, Recipe, digest, synthetic_image


def _rows(table):
    # Identificadores llamados únicamente desde constantes de este módulo.
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT * FROM "{table}"')
        names = [column[0] for column in cursor.description]
        return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def _identity(row):
    return (
        row["case"],
        row["event"],
        row["user"],
        row["cycle"],
        row["confirmation"],
        row["credit"],
    )


def verify(manifest_path: Path, *, artifact_root: Path):
    root = guard_environment(settings, artifact_root)
    path = Path(manifest_path).resolve()
    if root not in path.parents:
        raise DatasetError("El manifiesto debe pertenecer al directorio privado autorizado.")
    manifest = read_manifest(path)
    plan = manifest["plan"]
    recipe = Recipe(date.fromisoformat(plan["reference_date"]), plan["seed"], plan["report_count"])
    if plan != recipe.plan() or manifest["plan_sha256"] != digest(recipe.plan()):
        raise DatasetError("La receta versionada y el manifiesto no coinciden.")
    checks, failures = [], []

    def check(label, actual, expected):
        passed = actual == expected
        checks.append({"id": label, "pass": passed})
        if not passed:
            # El informe es público: solo nombres de comprobación; no IDs ni objetos privados.
            failures.append(label)

    actors = {label: data["id"] for label, data in manifest["actors"].items()}
    actor_labels = {value: label for label, value in actors.items()}
    user_rows, profile_rows = _rows("users"), _rows("citizen_profiles")
    users = {str(row["id"]): row for row in user_rows}
    expected_labels = [
        "admin",
        *[f"operator-{i}" for i in INSTITUTIONS],
        *[f"C{i:02}" for i in range(12)],
    ]
    check("actors.exact-set", set(users), {actors.get(label) for label in expected_labels})
    for label in expected_labels:
        expected_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{recipe.identifier}/{label}"))
        check(f"actors.{label}.id", actors.get(label), expected_id)
        row = users.get(expected_id, {})
        expected_role = (
            "admin"
            if label == "admin"
            else "operator"
            if label.startswith("operator")
            else "citizen"
        )
        check(
            f"actors.{label}.role-active",
            (row.get("role"), row.get("is_active")),
            (expected_role, True),
        )
        if label.startswith("C"):
            check(f"actors.{label}.verified", row.get("verification_status"), "APROBADO")
    profiles = {str(row["user_id"]): row for row in profile_rows}
    check("profiles.count", len(profiles), 12)
    for n in range(12):
        profile = profiles.get(actors[f"C{n:02}"], {})
        check(
            f"profiles.C{n:02}.review-privacy",
            (
                str(profile.get("verified_by_id")),
                profile.get("verified_at"),
                profile.get("ranking_visibility"),
            ),
            (actors["admin"], recipe.start, PRIVACY[n % 4]),
        )

    institutions = {str(row["id"]): row["slug"] for row in _rows("institutions")}
    categories = {str(row["id"]): row["slug"] for row in _rows("categories")}
    check("catalogs.institutions", set(institutions.values()), set(INSTITUTIONS))
    check("catalogs.categories", set(categories.values()), {c for c, _ in CATEGORIES})
    check(
        "catalogs.demo-sla",
        {row["slug"]: row["sla_hours"] for row in _rows("institutions")},
        dict(zip(INSTITUTIONS, (48, 72, None), strict=True)),
    )
    links = {
        (
            actor_labels.get(str(row["user_id"])),
            institutions.get(str(row["institution_id"])),
            row["active"],
        )
        for row in _rows("institution_operators")
    }
    check(
        "operators.memberships", links, {(f"operator-{slug}", slug, True) for slug in INSTITUTIONS}
    )
    reports = {str(row["id"]): row for row in _rows("reports")}
    with connection.cursor() as cursor:
        cursor.execute("SELECT id,ST_X(location),ST_Y(location),ST_SRID(location) FROM reports")
        locations = {str(row[0]): tuple(row[1:]) for row in cursor.fetchall()}
    report_labels = {value: label for label, value in manifest["reports"].items()}
    check("reports.exact-set", set(reports), set(report_labels))
    check("reports.logical-cases", set(report_labels.values()), {c["id"] for c in recipe.cases()})
    histories = _rows("report_status_history")
    confirmations = _rows("report_confirmations")
    classifications = _rows("ai_classifications")
    confirmation_ids = {str(r["id"]): r for r in confirmations}
    check("confirmations.count", len(confirmations), 32)
    check(
        "confirmations.no-duplicates",
        len({(r["report_id"], r["citizen_id"]) for r in confirmations}),
        len(confirmations),
    )

    for case in recipe.cases():
        label, kind = case["id"], case["kind"]
        report_id = manifest["reports"][label]
        row = reports.get(report_id, {})
        expected = expected_history(case)
        check(
            f"reports.{label}.location-projection",
            locations.get(report_id),
            (case["longitude"], case["latitude"], 4326),
        )
        check(
            f"reports.{label}.institution-projection",
            institutions.get(str(row.get("institution_id"))),
            None if kind == "pending" else case["institution"],
        )
        check(
            f"reports.{label}.state-cycle",
            (row.get("status"), row.get("resolution_cycle")),
            expected[-1][:2],
        )
        check(
            f"reports.{label}.category-source-author",
            (
                categories.get(str(row.get("category_id"))),
                row.get("source"),
                actor_labels.get(str(row.get("citizen_id"))),
                row.get("created_at"),
            ),
            (
                case["category"],
                "x_mock" if kind == "x_mock" else "platform",
                f"C{case['author']:02}" if case["author"] is not None else None,
                datetime.fromisoformat(case["at"]),
            ),
        )
        at = datetime.fromisoformat(case["at"])
        expected_priority = "MEDIA" if kind == "pending" else case["priority"]
        check(f"reports.{label}.priority", row.get("priority"), expected_priority)
        validated_at = (
            at + timedelta(seconds=5)
            if kind == "revalidated"
            else at + timedelta(seconds=3)
            if kind not in {"pending", "rejected"} and label != "R001"
            else None
        )
        check(
            f"reports.{label}.validation-projection",
            (row.get("validated_at"), actor_labels.get(str(row.get("validated_by_id")))),
            (validated_at, f"operator-{case['institution']}" if validated_at else None),
        )
        resolved_at = (
            at + timedelta(seconds=6) if kind in {"funding", "closed"} and label != "R001" else None
        )
        check(
            f"reports.{label}.resolution-projection",
            (row.get("resolved_at"), row.get("closed_at")),
            (resolved_at, recipe.operation if kind == "closed" else None),
        )
        expected_classifications = (
            []
            if kind in {"pending", "x_mock"}
            else [
                (
                    case["category"],
                    case["institution"],
                    case["priority"],
                    "s15-synthetic-adapter",
                    at + timedelta(seconds=1),
                )
            ]
        )
        check(
            f"classification.{label}.synthetic-provenance",
            [
                (
                    categories.get(str(r["suggested_category_id"])),
                    institutions.get(str(r["suggested_institution_id"])),
                    r["suggested_priority"],
                    r["model_name"],
                    r["created_at"],
                )
                for r in classifications
                if str(r["report_id"]) == report_id
            ],
            expected_classifications,
        )
        actual = sorted(
            (r for r in histories if str(r["report_id"]) == report_id),
            key=lambda r: r["created_at"],
        )
        expected_history_rows = []
        previous = ""
        for state, cycle, seconds, actor in expected:
            at = (
                recipe.operation + timedelta(seconds=2)
                if seconds == "operation+2"
                else recipe.operation
                if seconds == "operation"
                else datetime.fromisoformat(case["at"]) + timedelta(seconds=seconds)
            )
            expected_history_rows.append((previous, state, cycle, at, actor))
            previous = state
        check(
            f"history.{label}.exact",
            [
                (
                    r["previous_status"],
                    r["new_status"],
                    r["resolution_cycle"],
                    r["created_at"],
                    actor_labels.get(str(r["changed_by_id"])),
                )
                for r in actual
            ],
            expected_history_rows,
        )
        check(
            f"confirmations.{label}.actors",
            sorted(
                actor_labels.get(str(r["citizen_id"]))
                for r in confirmations
                if str(r["report_id"]) == report_id
            ),
            [f"C{n:02}" for n in case["confirmants"]],
        )

    ledger = _rows("reward_transactions")
    oracle = expected_ledger(recipe)
    mapped = []
    actual_by_key = {}
    for row in ledger:
        conf = confirmation_ids.get(str(row["confirmation_id"]))
        logical = {
            "case": report_labels.get(str(conf["report_id"] if conf else row["report_id"])),
            "event": row["event_type"],
            "user": actor_labels.get(str(row["user_id"])),
            "impact": row["impact_delta"],
            "credit": row["credit_delta"],
            "status": row["status"],
            "institution": institutions.get(str(row["institution_id"])),
            "cycle": row["resolution_cycle"],
            "confirmation": conf is not None,
            "credited_at": row["credited_at"],
            "created_at": row["created_at"],
            "reversed_at": row["reversed_at"],
            "voided_at": row["voided_at"],
        }
        mapped.append(logical)
        actual_by_key[_identity(logical)] = row
    # Counter retiene multiplicidades: ni conjuntos ni totales pueden esconder duplicados.
    fields = tuple(mapped[0]) if mapped else ()
    check(
        "ledger.exact-values-timestamps-links",
        Counter(tuple(r[f] for f in fields) for r in mapped),
        Counter(tuple(r[f] for f in fields) for r in oracle),
    )
    check(
        "ledger.four-states",
        {r["status"] for r in mapped},
        {"PENDIENTE", "ACREDITADO", "ANULADO", "REVERTIDO"},
    )
    for expected in oracle:
        row = actual_by_key.get(_identity(expected), {})
        related = expected["related"]
        if related:
            event, user, cycle = related
            original = actual_by_key.get(
                (
                    expected["case"],
                    event,
                    user,
                    cycle,
                    event == "CONFIRMATION_CREATED",
                    -expected["credit"],
                ),
                {},
            )
            check(
                f"ledger.reversal.{expected['case']}.{event}.{user}",
                row.get("related_transaction_id"),
                original.get("id"),
            )
        elif row:
            check(
                f"ledger.no-related.{expected['case']}.{expected['event']}.{expected['user']}.{expected['cycle']}",
                row["related_transaction_id"],
                None,
            )

    _verify_balances_ranking(recipe, oracle, actors, check)
    _verify_redemptions(ledger, actors, institutions, check, recipe)
    event_denominator = _verify_events(recipe, manifest, actors, report_labels, ledger, check)
    _verify_media(recipe, manifest, path.parent, profiles, reports, check)
    check(
        "checkpoints.pre-balances",
        manifest.get("checkpoints", {}).get("before_redemption", {}).get("balances"),
        {"C00": 1000, "C01": 1050, "C02": 1000},
    )
    check(
        "checkpoints.post-balances",
        manifest.get("checkpoints", {}).get("after_redemption", {}).get("balances"),
        {"C00": 0, "C01": 50, "C02": 0},
    )
    check(
        "checkpoints.reversal-balances",
        manifest.get("checkpoints", {}).get("after_reversal", {}).get("balances"),
        {"C00": -100, "C01": 50, "C02": 0},
    )
    controls = manifest.get("negative_controls", [])
    check(
        "negative-controls.codes",
        Counter(c.get("code") for c in controls),
        Counter(
            {
                "MONTHLY_LIMIT_REACHED": 3,
                "INSUFFICIENT_BALANCE": 1,
                "SELF_CONFIRMATION_NOT_ALLOWED": 1,
                "ALREADY_CONFIRMED": 1,
            }
        ),
    )
    check(
        "negative-controls.no-writes",
        all(c.get("before") == c.get("after") for c in controls),
        True,
    )
    check(
        "callbacks.no-delivery",
        all(c.get("delivered") is False for c in manifest.get("callbacks", [])),
        True,
    )
    _verify_observations(manifest, check)
    return {
        "status": "FAIL" if failures else "PASS",
        "scope": "local SQL + bytes + declarative oracle",
        "checks_passed": len(checks) - len(failures),
        "checks_required": len(checks),
        "failures": failures,
        "checks": checks,
        "dataset_version": plan["version"],
        "plan_sha256": manifest["plan_sha256"],
        "mandatory_events": event_denominator,
        "not_verified": [
            "HTTP permissions and API=SQL",
            "live Google/OpenAI/Brevo/X",
            "DB+Storage restoration",
            "as-of snapshot restoration",
            "browser demo",
            "academic hypotheses S11",
            "provider delivery",
            "public dashboard k=5 suppression",
            "six-month dashboard and SLA API metrics",
        ],
        "state_semantics": (
            "Current ledger only; checkpoint observations do not reconstruct prior states."
        ),
    }


def _verify_balances_ranking(recipe, oracle, actors, check):
    historical, pending, month, balances = (
        defaultdict(int),
        defaultdict(int),
        defaultdict(int),
        defaultdict(int),
    )
    reached = {}
    for row in oracle:
        if row["status"] == "PENDIENTE":
            pending[row["user"]] += row["impact"]
        if row["status"] != "ACREDITADO":
            continue
        historical[row["user"]] += row["impact"]
        balances[row["user"], row["institution"]] += row["credit"]
        if recipe.month <= row["credited_at"] < recipe.next_month and row["event"] != "REDEMPTION":
            month[row["user"]] += row["impact"]
            reached[row["user"]] = max(
                reached.get(row["user"], row["credited_at"]), row["credited_at"]
            )
    with connection.cursor() as cursor:
        for user in (f"C{n:02}" for n in range(12)):
            cursor.execute(
                "SELECT COALESCE(SUM(impact_delta) FILTER (WHERE status='ACREDITADO'),0), "
                "COALESCE(SUM(impact_delta) FILTER (WHERE status='PENDIENTE'),0) "
                "FROM reward_transactions WHERE user_id=%s",
                [actors[user]],
            )
            check(
                f"balances.{user}.impact-pending",
                cursor.fetchone(),
                (historical[user], pending[user]),
            )
            for slug in INSTITUTIONS:
                cursor.execute(
                    "SELECT COALESCE(SUM(t.credit_delta),0) FROM reward_transactions t "
                    "JOIN institutions i ON i.id=t.institution_id WHERE t.user_id=%s "
                    "AND i.slug=%s AND t.status='ACREDITADO'",
                    [actors[user], slug],
                )
                check(f"balances.{user}.{slug}", cursor.fetchone()[0], balances[user, slug])
        cursor.execute(
            "SELECT user_id,SUM(impact_delta),MAX(credited_at) FROM reward_transactions "
            "WHERE status='ACREDITADO' AND event_type<>'REDEMPTION' "
            "AND credited_at >= %s AND credited_at < %s GROUP BY user_id "
            "HAVING SUM(impact_delta)>0 ORDER BY SUM(impact_delta) DESC,MAX(credited_at),user_id",
            [recipe.month, recipe.next_month],
        )
        actual = [(str(r[0]), r[1], r[2]) for r in cursor.fetchall()]
    expected = sorted(
        ((actors[u], points, reached[u]) for u, points in month.items() if points > 0),
        key=lambda r: (-r[1], r[2], r[0]),
    )
    check("ranking.monthly-all-candidates", actual, expected)
    check("ranking.more-than-ten", len(actual) > 10, True)
    check(
        "ranking.tie-positions-10-11",
        actual[9][1] == actual[10][1] if len(actual) > 10 else False,
        True,
    )


def _verify_redemptions(ledger, actors, institutions, check, recipe):
    redemptions = _rows("reward_redemptions")
    rewards = {r["id"]: r for r in _rows("rewards")}
    check("redemptions.count", len(redemptions), 3)
    check("rewards.count", len(rewards), 3)
    for n, slug in enumerate(INSTITUTIONS):
        matching = [r for r in redemptions if str(r["user_id"]) == actors[f"C{n:02}"]]
        check(f"redemptions.C{n:02}.one", len(matching), 1)
        if not matching:
            continue
        redemption = matching[0]
        reward = rewards.get(redemption["reward_id"], {})
        check(
            f"redemptions.C{n:02}.cost-month-institution",
            (
                redemption["credits_spent"],
                redemption["period_month"],
                institutions.get(str(redemption["institution_id"])),
                redemption["created_at"],
                reward.get("credit_cost"),
                reward.get("simulated"),
                reward.get("active"),
            ),
            (1000, recipe.month.date(), slug, recipe.operation, 1000, True, True),
        )
        debits = [r for r in ledger if r["redemption_id"] == redemption["id"]]
        check(
            f"redemptions.C{n:02}.debit-link",
            [(r["event_type"], str(r["user_id"]), r["institution_id"]) for r in debits],
            [("REDEMPTION", actors[f"C{n:02}"], redemption["institution_id"])],
        )


def _verify_events(recipe, manifest, actors, report_labels, ledger, check):
    audit = _rows("audit_logs")
    for row in audit:
        if isinstance(row["payload"], str):
            row["payload"] = json.loads(row["payload"])
    notifications = _rows("notifications")
    profiles = _rows("citizen_profiles")
    confirmations = _rows("report_confirmations")
    expected_audit, expected_notifications = Counter(), Counter()

    def event(action, actor, report=None, cycle=None):
        expected_audit[action, actor, report, cycle] += 1

    for label, data in manifest["actors"].items():
        if label.startswith("C"):
            profile = next((r for r in profiles if str(r["user_id"]) == data["id"]), {})
            for action, actor in (
                ("identity.submitted", data["id"]),
                ("identity.approved", actors["admin"]),
            ):
                matching = [
                    r
                    for r in audit
                    if r["action"] == action
                    and r["payload"].get("profile_id") == str(profile.get("id"))
                ]
                check(
                    f"audit.{label}.{action}.profile-actor",
                    [str(r["actor_user_id"]) for r in matching],
                    [actor],
                )
            event("identity.submitted", data["id"])
            event("identity.approved", actors["admin"])
            expected_notifications[data["id"], "VERIFICACION_APROBADA", None, None] += 1
        elif label == "admin" or label.startswith("operator-"):
            event("user.role_promoted", None)
    for slug in INSTITUTIONS:
        event("reward.created", actors[f"operator-{slug}"])
    for case in recipe.cases():
        report_id, kind = manifest["reports"][case["id"]], case["kind"]
        citizen = actors[f"C{case['author']:02}"] if case["author"] is not None else None
        operator = actors[f"operator-{case['institution']}"]
        event("report.ingested" if kind == "x_mock" else "report.created", citizen, report_id)
        if citizen:
            expected_notifications[
                citizen, "REPORTE_CREADO", report_id, f"REPORTE_CREADO:{report_id}"
            ] += 1
        for confirmant in case["confirmants"]:
            event("report.confirmed", actors[f"C{confirmant:02}"], report_id)
        if kind == "pending":
            continue
        if kind != "x_mock":
            event("ai.report.classified", None, report_id)
        if kind == "rejected":
            event("report.rejected", operator, report_id)
            expected_notifications[
                citizen, "REPORTE_RECHAZADO", report_id, f"REPORTE_RECHAZADO:{report_id}"
            ] += 1
            continue
        for cycle in range(1, 3 if kind == "revalidated" else 2):
            event("report.validated", operator, report_id, cycle)
            if citizen:
                expected_notifications[
                    citizen,
                    "VALIDACION_INSTITUCIONAL",
                    report_id,
                    f"VALIDACION_INSTITUCIONAL:{report_id}:{cycle}",
                ] += 1
        if kind in {"funding", "closed"}:
            event("report.processing_started", operator, report_id, 1)
            event("report.resolution_evidence_created", operator, report_id, 1)
            event("report.resolved", operator, report_id, 1)
            expected_notifications[
                citizen, "REPORTE_RESUELTO", report_id, f"REPORTE_RESUELTO:{report_id}:1"
            ] += 1
        if kind == "closed":
            event("report.closed", None, report_id, 1)
        if kind == "revalidated" or case["id"] == "R001":
            event("report.validation_reverted", actors["admin"], report_id, 1)
    for n in range(3):
        event("reward.redeemed", actors[f"C{n:02}"])
        expected_notifications[actors[f"C{n:02}"], "CANJE_REALIZADO", None, None] += 1
    actual_audit = Counter(
        (
            r["action"],
            str(r["actor_user_id"]) if r["actor_user_id"] else None,
            r["payload"].get("report_id"),
            r["payload"].get("cycle"),
        )
        for r in audit
    )
    check("audit.mandatory-events-exact-denominator", actual_audit, expected_audit)
    check(
        "notifications.exact-events-dedup",
        Counter(
            (
                str(r["user_id"]),
                r["notification_type"],
                str(r["report_id"]) if r["report_id"] else None,
                r["dedup_key"],
            )
            for r in notifications
        ),
        expected_notifications,
    )
    check(
        "notifications.pending-not-delivered",
        {(r["status"], r["attempts"]) for r in notifications},
        {("PENDIENTE", 0)},
    )
    check(
        "callbacks.exact-count",
        len(manifest.get("callbacks", [])),
        len(notifications) + recipe.report_count - 1,
    )
    by_id = {str(r["id"]): r for r in ledger}
    evidence = _rows("resolution_evidences")
    for index, row in enumerate(audit):
        payload, action = row["payload"], row["action"]
        report_id = payload.get("report_id")
        if action == "report.confirmed":
            matches = [r for r in confirmations if str(r["id"]) == payload.get("confirmation_id")]
            check(
                f"audit.links.confirmation.{index}",
                [(str(r["report_id"]), r["citizen_id"]) for r in matches],
                [(report_id, row["actor_user_id"])],
            )
        if action == "report.resolution_evidence_created":
            linked = [
                evidence_row
                for evidence_row in evidence
                if str(evidence_row["id"]) == payload.get("evidence_id")
            ]
            check(
                f"audit.links.evidence-created.{index}",
                [(str(e["report_id"]), e["resolution_cycle"], e["uploaded_by_id"]) for e in linked],
                [(report_id, payload.get("cycle"), row["actor_user_id"])],
            )
        if action == "report.rejected":
            expected_voided = [
                str(tx["id"])
                for tx in ledger
                if tx["status"] == "ANULADO"
                and tx["event_type"] in {"REPORT_CREATED", "CONFIRMATION_CREATED"}
                and (
                    str(tx["report_id"]) == report_id
                    or any(
                        tx["confirmation_id"] == c["id"] and str(c["report_id"]) == report_id
                        for c in confirmations
                    )
                )
            ]
            check(
                f"audit.links.rejected-voided.{index}",
                Counter(payload.get("voided_transaction_ids", [])),
                Counter(expected_voided),
            )
        if action == "report.validated":
            cycle = payload["cycle"]
            ids = payload.get("credited_transaction_ids", [])
            bonus = payload.get("validation_bonus_transaction_id")
            expected_ids = [
                str(r["id"])
                for r in ledger
                if r["resolution_cycle"] == cycle
                and r["event_type"] in {"REPORT_CREATED", "CONFIRMATION_CREATED"}
                and (
                    str(r["report_id"]) == report_id
                    or (
                        r["confirmation_id"]
                        and any(
                            str(c["id"]) == str(r["confirmation_id"])
                            and str(c["report_id"]) == report_id
                            for c in confirmations
                        )
                    )
                )
            ]
            check(f"audit.links.validated.{index}", Counter(ids), Counter(expected_ids))
            tx = by_id.get(bonus, {}) if bonus else {}
            author = reports_author(report_id)
            check(
                f"audit.links.bonus.{index}",
                (
                    str(tx.get("report_id")),
                    tx.get("resolution_cycle"),
                    tx.get("event_type"),
                    tx.get("user_id"),
                )
                if bonus
                else None,
                (report_id, cycle, "VALIDATION_BONUS", author) if author else None,
            )
        if action == "report.resolved":
            expected_ids = [
                str(r["id"])
                for r in evidence
                if str(r["report_id"]) == report_id and r["resolution_cycle"] == payload["cycle"]
            ]
            check(
                f"audit.links.evidence.{index}",
                Counter(payload.get("evidence_ids", [])),
                Counter(expected_ids),
            )
            tx = by_id.get(payload.get("resolution_bonus_transaction_id"), {})
            check(
                f"audit.links.resolution-bonus.{index}",
                (
                    str(tx.get("report_id")),
                    tx.get("resolution_cycle"),
                    tx.get("event_type"),
                    tx.get("user_id"),
                ),
                (report_id, payload["cycle"], "RESOLUTION_BONUS", reports_author(report_id)),
            )
        if action == "reward.redeemed":
            tx = by_id.get(payload.get("ledger_transaction_id"), {})
            check(
                f"audit.links.redemption.{index}",
                (
                    str(tx.get("redemption_id")),
                    tx.get("user_id"),
                    tx.get("event_type"),
                    tx.get("impact_delta"),
                    tx.get("credit_delta"),
                    str(tx.get("institution_id")),
                ),
                (
                    payload.get("redemption_id"),
                    row["actor_user_id"],
                    "REDEMPTION",
                    0,
                    -1000,
                    payload.get("institution_id"),
                ),
            )
        if action == "report.validation_reverted":
            originals = {
                str(r["id"])
                for r in ledger
                if r["event_type"] != "REVERSAL"
                and r["resolution_cycle"] == payload["cycle"]
                and r["status"] == "REVERTIDO"
                and (
                    str(r["report_id"]) == report_id
                    or any(
                        r["confirmation_id"] == c["id"] and str(c["report_id"]) == report_id
                        for c in confirmations
                    )
                )
            }
            compensations = {
                str(r["id"])
                for r in ledger
                if r["event_type"] == "REVERSAL"
                and str(r["report_id"]) == report_id
                and r["resolution_cycle"] == payload["cycle"]
            }
            check(
                f"audit.links.reversal-originals.{index}",
                Counter(payload.get("reverted_transaction_ids", [])),
                Counter(originals),
            )
            check(
                f"audit.links.reversal-compensations.{index}",
                Counter(payload.get("reversal_transaction_ids", [])),
                Counter(compensations),
            )
    return {
        "audit_required": sum(expected_audit.values()),
        "audit_observed": len(audit),
        "audit_matched": sum((actual_audit & expected_audit).values()),
        "notifications_required": sum(expected_notifications.values()),
        "notifications_observed": len(notifications),
    }


def _verify_observations(manifest, check):
    institution = INSTITUTIONS[0]
    created = [["REPORT_CREATED", "PENDIENTE", 20, 20, "", None]]
    confirmed = [["CONFIRMATION_CREATED", "PENDIENTE", 10, 10, "", None], *created]
    validated = [
        ["CONFIRMATION_CREATED", "ACREDITADO", 10, 10, institution, 1],
        ["REPORT_CREATED", "ACREDITADO", 20, 20, institution, 1],
        ["VALIDATION_BONUS", "ACREDITADO", 30, 30, institution, 1],
    ]
    resolved = [
        *validated[:2],
        ["RESOLUTION_BONUS", "ACREDITADO", 50, 50, institution, 1],
        validated[2],
    ]
    expected = {
        "S01-created": created,
        "S03-confirmed-pending": confirmed,
        "S02-S03-validated": validated,
        "S04-resolved": resolved,
    }
    check("observations.S01-S04.transitions", manifest.get("observations"), expected)


def reports_author(report_id):
    with connection.cursor() as cursor:
        cursor.execute("SELECT citizen_id FROM reports WHERE id=%s", [report_id])
        row = cursor.fetchone()
    return row[0] if row else None


def _verify_media(recipe, manifest, directory, profiles, reports, check):
    objects = LocalObjects(directory)
    object_index = {(r["bucket"], r["key"]): r for r in manifest["objects"]}
    check("media.manifest-unique-keys", len(object_index), len(manifest["objects"]))
    input_index = {r["logical_id"]: r for r in manifest["inputs"]}
    check(
        "media.input-unique-hashes",
        len({r["original_sha256"] for r in manifest["inputs"]}),
        len(manifest["inputs"]),
    )
    expected_inputs = {f"C{n:02}-selfie" for n in range(12)}
    expected_inputs.update(c["id"] for c in recipe.cases() if c["kind"] != "x_mock")
    expected_inputs.update(
        f"{c['id']}-evidence" for c in recipe.cases() if c["kind"] in {"funding", "closed"}
    )
    check("media.inputs-exact", set(input_index), expected_inputs)
    for logical_id, entry in input_index.items():
        relative = Path(entry["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise DatasetError("Entrada multimedia fuera del manifiesto.")
        data = (directory / relative).read_bytes()
        image_format = "PNG" if logical_id.endswith(("-selfie", "-evidence")) else "JPEG"
        expected = synthetic_image(recipe.seed, logical_id, image_format)
        check(
            f"media.input.{logical_id}.bytes-sha256",
            (data, hashlib.sha256(data).hexdigest()),
            (expected, entry["original_sha256"]),
        )
    references = set()
    report_labels = {value: label for label, value in manifest["reports"].items()}
    for row in _rows("report_media"):
        logical_id = report_labels[str(row["report_id"])]
        check(
            f"media.report.{logical_id}.original-hash",
            row["file_hash"],
            input_index[logical_id]["original_sha256"],
        )
        check(
            f"media.report.{logical_id}.normalized-input",
            objects.download("s15-reports", row["file_url"]),
            _normalized_input((directory / input_index[logical_id]["path"]).read_bytes(), "JPEG"),
        )
        check(
            f"media.report.{logical_id}.key-link",
            row["file_url"],
            f"reports/{row['report_id']}/{row['id']}.jpg",
        )
        references.add(("s15-reports", row["file_url"]))
    for row in profiles.values():
        references.add(("s15-private", row["verification_selfie_url"]))
        label = next(
            label
            for label, actor in manifest["actors"].items()
            if actor["id"] == str(row["user_id"])
        )
        check(
            f"media.selfie.{label}.input-link",
            objects.download("s15-private", row["verification_selfie_url"]),
            (directory / input_index[f"{label}-selfie"]["path"]).read_bytes(),
        )
        check(
            f"media.selfie.{label}.key-link",
            row["verification_selfie_url"].startswith(f"verifications/{row['user_id']}/"),
            True,
        )
    for row in _rows("resolution_evidences"):
        references.add(("s15-reports", row["evidence_url"]))
        data = objects.download("s15-reports", row["evidence_url"])
        composite = hashlib.sha256(
            data
            + b"\x00"
            + row["description"].encode()
            + b"\x00"
            + str(row["resolution_cycle"]).encode()
        ).hexdigest()
        check(
            f"media.evidence.{report_labels[str(row['report_id'])]}.composite-hash",
            row["content_hash"],
            composite,
        )
        logical_id = f"{report_labels[str(row['report_id'])]}-evidence"
        check(
            f"media.evidence.{logical_id}.normalized-input",
            data,
            _normalized_input((directory / input_index[logical_id]["path"]).read_bytes(), "PNG"),
        )
        check(
            f"media.evidence.{logical_id}.key-link",
            row["evidence_url"],
            f"resolution-evidence/{row['report_id']}/{row['resolution_cycle']}/{row['id']}.png",
        )
    check("media.references-exact", references, set(object_index))
    for n, (key, entry) in enumerate(object_index.items()):
        data = objects.download(*key)
        check(
            f"media.object.{n}.stored-hash-length",
            (hashlib.sha256(data).hexdigest(), len(data)),
            (entry["stored_sha256"], entry["bytes"]),
        )
        with Image.open(io.BytesIO(data)) as image:
            check(
                f"media.object.{n}.mime-no-exif",
                (Image.MIME.get(image.format), bool(image.getexif())),
                (entry["mime"], False),
            )


def _normalized_input(data, image_format):
    """Oráculo de bytes para los mosaicos RGB sin EXIF de esta receta, sin helper productivo."""
    with Image.open(io.BytesIO(data)) as image:
        pixels = image.convert("RGB")
        clean = Image.frombytes("RGB", pixels.size, pixels.tobytes())
        pixels.close()
    output = io.BytesIO()
    if image_format == "JPEG":
        clean.save(
            output, format="JPEG", quality=90, optimize=True, progressive=False, subsampling=2
        )
    else:
        clean.save(output, format="PNG", optimize=True, compress_level=9)
    clean.close()
    return output.getvalue()
