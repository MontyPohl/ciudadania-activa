"""Recorridos HTTP reales S15 con oráculo SQL independiente, exclusivamente local.

Ejecutar dentro del contenedor web S15 contra su loopback. La preparación de
saldo usa servicios públicos de dominio, nunca inserta/edita el ledger.
No consume OAuth, IA, correo ni otros proveedores reales.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Asuncion")
TABLES = (
    "reports",
    "report_media",
    "report_status_history",
    "report_confirmations",
    "resolution_evidences",
    "reward_transactions",
    "reward_redemptions",
    "audit_logs",
    "notifications",
)


class FlowError(ValueError):
    pass


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise FlowError("Una respuesta de ensayo intentó redirigir la solicitud.")


def validate_target(base_url):
    parsed = urlsplit(base_url)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.port != 8000
        or parsed.path not in ("", "/")
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise FlowError("Solo se admite http://127.0.0.1:8000 dentro del web S15 aislado.")


def private_path(value, *, output=False):
    path = Path(value)
    root = Path("/out/s15")
    if not path.is_absolute() or not path.is_relative_to(root) or path.resolve() != path:
        raise FlowError("Las rutas deben ser canónicas bajo /out/s15.")
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise FlowError("No se admiten symlinks.")
        if parent == root:
            break
    if output:
        if path.exists() or not path.parent.is_dir() or path.parent.stat().st_mode & 0o077:
            raise FlowError("La salida debe ser nueva y su directorio privado (0700).")
    elif not path.is_file() or path.stat().st_mode & 0o077:
        raise FlowError("La entrada debe ser un archivo privado (0600).")
    return path


def month_bounds(now):
    local = now.astimezone(TZ)
    start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = (
        start.replace(year=start.year + 1, month=1)
        if start.month == 12
        else start.replace(month=start.month + 1)
    )
    return start, end


def multipart(fields, uploads):
    boundary = "s15-" + uuid.uuid4().hex
    parts = []
    for key, value in fields.items():
        parts.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n'
                f"\r\n{value}\r\n"
            ).encode()
        )
    for key, content in uploads.items():
        parts.extend(
            [
                (
                    f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"; '
                    'filename="s15.png"\r\nContent-Type: image/png\r\n\r\n'
                ).encode(),
                content,
                b"\r\n",
            ]
        )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def synthetic_photo(label):
    from PIL import Image

    data = hashlib.sha256(label.encode()).digest()
    image = Image.new("RGB", (64, 64))
    image.putdata([tuple(data[(n + c) % 32] for c in range(3)) for n in range(64 * 64)])
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def visible_name(name, visibility, alias):
    words = name.strip().split()
    if visibility == "ALIAS":
        return (alias.strip(), False) if alias.strip() else ("Usuario privado", True)
    if visibility == "FULL_NAME" and words:
        return name.strip(), False
    if visibility == "FIRST_INITIAL" and words:
        return (words[0] if len(words) == 1 else f"{words[0]} {words[-1][0]}."), False
    return "Usuario privado", True


class Runner:
    def __init__(self, base_url, actors, output):
        validate_target(base_url)
        self.base_url = base_url.rstrip("/")
        self.actors = {actor["id"]: actor for actor in actors}
        self.output = output
        self.run_id = "api-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        self.instrument_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        self.checks = []
        self.requests = []
        self.created = []
        self.opener = build_opener(ProxyHandler({}), NoRedirects())

    def check(self, name, actual, expected):
        passed = actual == expected
        self.checks.append({"name": name, "passed": passed, "actual": actual, "expected": expected})
        if not passed:
            raise FlowError(f"Check divergente: {name}")

    def sql(self, statement, parameters=()):
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute(statement, parameters)
            return cursor.fetchall()

    def counts(self):
        return {table: self.sql(f'SELECT COUNT(*) FROM "{table}"')[0][0] for table in TABLES}

    def states(self):
        """Detecta inserciones, borrados y actualizaciones, incluidos timestamps."""
        result = {}
        for table in TABLES:
            rows = self.sql(f'SELECT row_to_json(t)::text FROM "{table}" t ORDER BY id')
            result[table] = {
                "rows": len(rows),
                "sha256": hashlib.sha256("\n".join(row[0] for row in rows).encode()).hexdigest(),
            }
        return result

    def request(self, name, method, path, actor=None, data=None, uploads=None, headers=None):
        payload, content_type = None, "application/json"
        if uploads:
            payload, content_type = multipart(data or {}, uploads)
        elif data is not None:
            payload = json.dumps(data).encode()
        request_headers = {"Accept": "application/json", "Content-Type": content_type}
        if actor:
            request_headers["Authorization"] = "Bearer " + self.actors[actor]["accessToken"]
        request_headers.update(headers or {})
        request = Request(
            self.base_url + path, data=payload, headers=request_headers, method=method
        )
        try:
            response = self.opener.open(request, timeout=45)
        except HTTPError as exc:
            response = exc
        with response:
            raw = response.read()
            code, cache = response.status, response.headers.get("Cache-Control", "")
        try:
            body = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            body = {"bodySha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
        # No bodies, JWT, cookies, signed capabilities ni domicilios en el informe.
        self.requests.append(
            {
                "name": name,
                "method": method,
                "path": (
                    "/api/v1/reports/evidence-access/[redacted]/"
                    if "/evidence-access/" in path
                    else path
                ),
                "status": code,
                "responseSha256": hashlib.sha256(raw).hexdigest(),
                "cacheControl": cache,
            }
        )
        return code, body, cache

    def http(self, name, method, path, expected=200, **kwargs):
        code, body, cache = self.request(name, method, path, **kwargs)
        self.check(name + ": HTTP", code, expected)
        return body

    def negative(
        self,
        name,
        path,
        *,
        actor=None,
        data=None,
        expected=403,
        code=None,
        method="POST",
    ):
        before = self.states()
        body = self.http(name, method, path, expected, actor=actor, data=data or {})
        if code is not None:
            self.check(name + ": código", body.get("code"), code)
        self.check(name + ": sin cambios SQL", self.states(), before)

    def report_ledger(self, report_id):
        return [
            list(row)
            for row in self.sql(
                "SELECT event_type,status,impact_delta,credit_delta,COALESCE(i.slug,'') "
                "FROM reward_transactions t LEFT JOIN institutions i ON i.id=t.institution_id "
                "WHERE report_id=%s OR confirmation_id IN "
                "(SELECT id FROM report_confirmations WHERE report_id=%s) ORDER BY event_type",
                [report_id, report_id],
            )
        ]

    def summary(self, label):
        start, end = month_bounds(datetime.now(UTC))
        uid = self.actors[label]["userId"]
        expected = self.sql(
            "SELECT COALESCE(SUM(impact_delta) FILTER(WHERE status='ACREDITADO'),0),"
            "COALESCE(SUM(impact_delta) FILTER(WHERE status='ACREDITADO' AND credited_at >= %s "
            "AND credited_at < %s),0),"
            "COALESCE(SUM(impact_delta) FILTER(WHERE status='PENDIENTE'),0) "
            "FROM reward_transactions WHERE user_id=%s",
            [start, end, uid],
        )[0]
        balances = self.sql(
            "SELECT i.id,SUM(t.credit_delta) FROM reward_transactions t JOIN institutions i "
            "ON i.id=t.institution_id WHERE t.user_id=%s AND t.status='ACREDITADO' "
            "GROUP BY i.id ORDER BY i.name",
            [uid],
        )
        body = self.http(f"{label} resumen", "GET", "/api/v1/rewards/summary/", actor=label)
        self.check(
            label + ": impacto API=SQL",
            body["impact"],
            dict(zip(["historical", "current_month", "pending"], expected, strict=True)),
        )
        self.check(
            label + ": créditos API=SQL",
            [[r["institution"]["id"], r["balance"]] for r in body["credits_by_institution"]],
            [[str(i), n] for i, n in balances],
        )
        self.check(label + ": período", body["period"], start.strftime("%Y-%m"))
        expected_movements = [
            list(row)
            for row in self.sql(
                "SELECT t.id,t.event_type,t.status,t.impact_delta,t.credit_delta,t.institution_id,"
                "COALESCE(t.report_id,c.report_id),t.redemption_id FROM reward_transactions t "
                "LEFT JOIN report_confirmations c ON c.id=t.confirmation_id WHERE t.user_id=%s "
                "ORDER BY t.created_at DESC,t.id DESC",
                [uid],
            )
        ]
        expected_movements = [
            [str(value) if isinstance(value, uuid.UUID) else value for value in row]
            for row in expected_movements
        ]
        actual_movements = []
        path = "/api/v1/rewards/transactions/"
        pages = 0
        while path:
            page = self.http(
                label + ": movimientos página " + str(pages + 1),
                "GET",
                path,
                actor=label,
            )
            actual_movements.extend(
                [
                    [
                        row["id"],
                        row["event_type"],
                        row["status"],
                        row["impact_delta"],
                        row["credit_delta"],
                        row["institution"]["id"] if row["institution"] else None,
                        row["report_id"],
                        row["redemption_id"],
                    ]
                    for row in page["results"]
                ]
            )
            pages += 1
            if pages > 100:
                raise FlowError("Paginación no termina.")
            next_url = page["next"]
            if next_url:
                parsed = urlsplit(next_url)
                validate_target(f"{parsed.scheme}://{parsed.netloc}")
                path = parsed.path + ("?" + parsed.query if parsed.query else "")
            else:
                path = None
        self.check(
            label + ": movimientos completos API=SQL",
            actual_movements,
            expected_movements,
        )
        return body

    def ranking(self):
        start, end = month_bounds(datetime.now(UTC))
        rows = self.sql(
            "SELECT u.id,SUM(t.impact_delta),u.full_name,p.ranking_visibility,p.ranking_alias "
            "FROM reward_transactions t JOIN users u ON u.id=t.user_id "
            "LEFT JOIN citizen_profiles p ON p.user_id=u.id WHERE t.status='ACREDITADO' "
            "AND t.credited_at >= %s AND t.credited_at < %s AND u.role='citizen' AND u.is_active "
            "AND t.event_type != 'REDEMPTION' GROUP BY u.id,p.id HAVING SUM(t.impact_delta)>0 "
            "ORDER BY SUM(t.impact_delta) DESC,MAX(t.credited_at),u.id LIMIT 10",
            [start, end],
        )
        for viewer in [None, "C01"]:
            expected = []
            for n, (uid, points, name, visibility, alias) in enumerate(rows, 1):
                display, private = visible_name(name, visibility, alias or "")
                expected.append(
                    {
                        "position": n,
                        "display_name": display,
                        "points": points,
                        "is_private": private,
                        "is_me": bool(viewer and self.actors[viewer]["userId"] == str(uid)),
                    }
                )
            body = self.http(
                "ranking " + str(viewer),
                "GET",
                "/api/v1/ranking/monthly/",
                actor=viewer,
            )
            self.check("ranking API=SQL " + str(viewer), body["entries"], expected)
            self.check(
                "ranking sin identificadores",
                sorted(body["entries"][0]),
                ["display_name", "is_me", "is_private", "points", "position"],
            )

    def create_report(self, label, category, suffix):
        body = self.http(
            "crear " + suffix,
            "POST",
            "/api/v1/reports/",
            201,
            actor=label,
            data={
                "category": category,
                "description": f"S15 API {self.run_id}: {suffix}",
                "lat": "-25.2637",
                "lng": "-57.6359",
            },
            uploads={"photo": synthetic_photo(self.run_id + suffix)},
        )
        self.created.append(body["id"])
        self.check(
            "premio creación " + suffix,
            body["reward"],
            {"impact_points": 20, "credits": 20, "status": "PENDIENTE"},
        )
        self.check(
            "ledger creación " + suffix,
            self.report_ledger(body["id"]),
            [["REPORT_CREATED", "PENDIENTE", 20, 20, ""]],
        )
        self.check(
            "creación SQL autor/categoría/estado " + suffix,
            [
                [str(uid), str(cat), status]
                for uid, cat, status in self.sql(
                    "SELECT citizen_id,category_id,status FROM reports WHERE id=%s",
                    [body["id"]],
                )
            ],
            [[self.actors[label]["userId"], category, "CREADO"]],
        )
        self.check(
            "creación SQL media original " + suffix,
            [
                [file_hash, str(uploaded_by)]
                for file_hash, uploaded_by in self.sql(
                    "SELECT file_hash,uploaded_by_id FROM report_media WHERE report_id=%s",
                    [body["id"]],
                )
            ],
            [
                [
                    hashlib.sha256(synthetic_photo(self.run_id + suffix)).hexdigest(),
                    self.actors[label]["userId"],
                ]
            ],
        )
        self.check(
            "creación SQL historial único " + suffix,
            [
                list(row)
                for row in self.sql(
                    "SELECT previous_status,new_status,resolution_cycle FROM report_status_history "
                    "WHERE report_id=%s",
                    [body["id"]],
                )
            ],
            [["", "CREADO", 0]],
        )
        return body["id"]

    def lifecycle(self, report_id, institution, operator):
        base = f"/api/v1/operator/reports/{report_id}"
        query = "?institution_id=" + institution
        body = self.http("validar", "POST", base + "/validate/" + query, actor=operator, data={})
        self.check("validación estado", body["status"], "ASIGNADO")
        self.http(
            "atender",
            "PATCH",
            base + "/status/" + query,
            actor=operator,
            data={"status": "EN_PROCESO", "expected_cycle": 1},
        )
        self.negative(
            "resolver sin evidencia",
            base + "/status/" + query,
            actor=operator,
            data={"status": "RESUELTO", "expected_cycle": 1},
            expected=409,
            code="RESOLUTION_EVIDENCE_REQUIRED",
            method="PATCH",
        )
        evidence = self.http(
            "adjuntar evidencia",
            "POST",
            base + "/evidence/" + query,
            201,
            actor=operator,
            data={"description": "Evidencia sintética S15 de resolución", "cycle": 1},
            uploads={"file": synthetic_photo(self.run_id + "resolution")},
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
        self.check(
            "evidencia SQL",
            self.sql(
                "SELECT COUNT(*) FROM resolution_evidences WHERE id=%s AND report_id=%s "
                "AND resolution_cycle=1",
                [evidence["id"], report_id],
            )[0][0],
            1,
        )
        self.http(
            "resolver",
            "PATCH",
            base + "/status/" + query,
            actor=operator,
            data={"status": "RESUELTO", "expected_cycle": 1},
        )
        self.check(
            "SQL resuelto",
            self.sql("SELECT status FROM reports WHERE id=%s", [report_id])[0][0],
            "RESUELTO",
        )
        self.check(
            "historial SQL ciclo completo",
            [
                list(row)
                for row in self.sql(
                    "SELECT new_status,resolution_cycle FROM report_status_history "
                    "WHERE report_id=%s "
                    "ORDER BY created_at,id",
                    [report_id],
                )
            ],
            [["CREADO", 0], ["ASIGNADO", 1], ["EN_PROCESO", 1], ["RESUELTO", 1]],
        )
        signed = urlsplit(evidence["signed_url"])
        validate_target(f"{signed.scheme}://{signed.netloc}")
        listing = f"/api/v1/reports/{report_id}/evidence/"
        self.http("autor lista evidencia", "GET", listing, actor="C03")
        self.negative("lista evidencia exige sesión", listing, expected=401, method="GET")
        self.negative(
            "otro ciudadano no lista evidencia",
            listing,
            actor="C07",
            expected=404,
            method="GET",
        )
        # La capacidad firmada es intencionalmente descargable sin sesión hasta expirar.
        downloaded = self.http("descarga con capacidad efímera", "GET", signed.path)
        object_key = self.sql(
            "SELECT evidence_url FROM resolution_evidences WHERE id=%s",
            [evidence["id"]],
        )[0][0]
        objects = list(Path("/out/s15").glob("s15-functional-*/objects/s15-reports"))
        self.check("Storage único", len(objects), 1)
        self.check(
            "evidencia HTTP=bytes persistidos",
            downloaded["bodySha256"],
            hashlib.sha256((objects[0] / object_key).read_bytes()).hexdigest(),
        )
        return evidence

    def prepare_funding(self, category_id, institution_id, operator_label):
        """Preestado documentado por servicios, fuera del tráfico HTTP y de sus cuotas."""
        from django.contrib.auth import get_user_model
        from django.core.files.uploadedfile import SimpleUploadedFile

        from apps.admin_app.models import Category, Institution
        from apps.core.enums import ReportStatus
        from apps.reports_app.services import (
            lifecycle,
            reports,
            resolution_evidence,
            validation,
        )

        user = get_user_model().objects.get(pk=self.actors["C10"]["userId"])
        category = Category.objects.get(pk=category_id)
        institution = Institution.objects.get(pk=institution_id)
        operator_id = self.actors[operator_label]["userId"]
        operator = get_user_model().objects.get(pk=operator_id)
        existing = self.sql(
            "SELECT COALESCE(SUM(credit_delta),0) FROM reward_transactions "
            "WHERE user_id=%s AND institution_id=%s AND status='ACREDITADO'",
            [user.pk, institution_id],
        )[0][0]
        self.check("preestado C10 créditos (confirmación histórica)", existing, 10)
        funded = []
        for n in range(11):
            photo = SimpleUploadedFile(
                "s15.png",
                synthetic_photo(self.run_id + f"fund-{n}"),
                content_type="image/png",
            )
            report = reports.create_report(
                citizen=user,
                category=category,
                description=f"S15 API preestado {self.run_id}/{n}",
                photo=photo,
                latitude=-25.2637,
                longitude=-57.6359,
            ).report
            validation.validate_report(
                report_id=report.pk, operator=operator, institution=institution
            )
            if n < 10:
                lifecycle.transition_report_status(
                    report_id=report.pk,
                    operator_id=operator_id,
                    institution_id=institution_id,
                    target_status=ReportStatus.EN_PROCESO,
                    expected_cycle=1,
                )
                resolution_evidence.create_resolution_evidence(
                    report_id=report.pk,
                    operator_id=operator_id,
                    institution_id=institution_id,
                    expected_cycle=1,
                    idempotency_key=uuid.uuid4(),
                    description="Evidencia sintética preestado",
                    uploaded_file=SimpleUploadedFile(
                        "s15.png",
                        synthetic_photo(self.run_id + f"fund-evidence-{n}"),
                        content_type="image/png",
                    ),
                )
                lifecycle.transition_report_status(
                    report_id=report.pk,
                    operator_id=operator_id,
                    institution_id=institution_id,
                    target_status=ReportStatus.RESUELTO,
                    expected_cycle=1,
                )
            funded.append(str(report.pk))
        self.check(
            "preestado C10 financiación servicios",
            self.sql(
                "SELECT SUM(credit_delta) FROM reward_transactions WHERE user_id=%s "
                "AND institution_id=%s AND status='ACREDITADO'",
                [user.pk, institution_id],
            )[0][0],
            1060,
        )
        return funded

    def canje(self, category_id, institution_id, operator_label, reward_id):
        funded = self.prepare_funding(category_id, institution_id, operator_label)
        path = f"/api/v1/rewards/{reward_id}/redeem/"
        self.negative("operador no canjea", path, actor=operator_label)
        self.negative("admin no canjea", path, actor="admin")
        body = self.http("canjear1060", "POST", path, 201, actor="C10", data={})
        self.check("remanente60", body["balance_after"], 60)
        redemption = body["redemption"]
        self.check(
            "débito SQL",
            [
                list(r)
                for r in self.sql(
                    "SELECT impact_delta,credit_delta,status FROM reward_transactions "
                    "WHERE redemption_id=%s",
                    [redemption["id"]],
                )
            ],
            [[0, -1000, "ACREDITADO"]],
        )
        self.negative(
            "límite mensual",
            path,
            actor="C10",
            expected=409,
            code="MONTHLY_LIMIT_REACHED",
        )
        self.negative(
            "saldo insuficiente",
            path,
            actor="C11",
            expected=409,
            code="INSUFFICIENT_BALANCE",
        )
        for report_id in funded[:2]:
            self.http(
                "revertir después del canje",
                "POST",
                f"/api/v1/admin/reports/{report_id}/revert-validation/",
                actor="admin",
                data={
                    "cycle": 1,
                    "reason": "Ensayo sintético S15 de reversión posterior al canje",
                },
            )
        self.check(
            "saldo negativo permitido",
            self.sql(
                "SELECT SUM(credit_delta) FROM reward_transactions WHERE user_id=%s "
                "AND institution_id=%s AND status='ACREDITADO'",
                [self.actors["C10"]["userId"], institution_id],
            )[0][0],
            -140,
        )
        self.check(
            "vale conserva código",
            self.sql("SELECT code FROM reward_redemptions WHERE id=%s", [redemption["id"]])[0][0],
            redemption["code"],
        )
        catalog = self.http(
            "catálogo después de reversión",
            "GET",
            "/api/v1/rewards/catalog/",
            actor="C10",
        )
        entry = next(
            row for row in catalog["results"] if row["institution"]["id"] == institution_id
        )
        self.check("catálogo saldo negativo", entry["balance"], -140)
        self.check(
            "catálogo límite mensual precede saldo",
            entry["blocked_reason"],
            "MONTHLY_LIMIT_REACHED",
        )
        self.summary("C10")

    def dashboards(self):
        period = datetime.now(TZ).strftime("%Y-%m")
        body = self.http(
            "dashboard admin",
            "GET",
            "/api/v1/admin/metrics/?period=" + period,
            actor="admin",
        )
        start, end = month_bounds(datetime.now(UTC))
        expected = dict(
            self.sql(
                "SELECT status,COUNT(*) FROM reports WHERE created_at >= %s AND created_at < %s "
                "GROUP BY status",
                [start, end],
            )
        )
        self.check(
            "dashboard recibidos API=SQL",
            body["reports"]["received"],
            sum(expected.values()),
        )
        self.check(
            "dashboard estados API=SQL",
            body["reports"]["by_status"],
            {status: expected.get(status, 0) for status in body["reports"]["by_status"]},
        )
        for label, actor in self.actors.items():
            if not label.startswith("operator-"):
                continue
            institution = actor["institutionId"]
            detail = self.http(
                "dashboard " + label,
                "GET",
                f"/api/v1/operator/metrics/?institution_id={institution}&period={period}",
                actor=label,
            )
            expected_count = self.sql(
                "SELECT COUNT(*) FROM reports r JOIN categories c ON c.id=r.category_id "
                "WHERE COALESCE(r.institution_id,c.default_institution_id)=%s "
                "AND r.created_at >= %s AND r.created_at < %s",
                [institution, start, end],
            )[0][0]
            self.check(
                label + ": dashboard scope SQL",
                detail["reports"]["received"],
                expected_count,
            )
            overdue = self.sql(
                "SELECT COUNT(*) FROM reports r JOIN categories c ON c.id=r.category_id "
                "JOIN institutions i ON i.id=COALESCE(r.institution_id,c.default_institution_id) "
                "WHERE i.id=%s AND i.sla_hours IS NOT NULL AND r.status IN "
                "('CREADO','CLASIFICADO','ASIGNADO','EN_PROCESO') "
                "AND r.created_at < %s - i.sla_hours * INTERVAL '1 hour'",
                [institution, datetime.fromisoformat(detail["generated_at"])],
            )[0][0]
            if detail["sla"]["configured"]:
                self.check(
                    label + ": SLA abiertos SQL",
                    detail["sla"]["open_over_sla"],
                    overdue,
                )
            else:
                self.check(label + ": SLA no configurado", detail["sla"]["open_over_sla"], None)
        public = self.http("transparencia pública", "GET", "/api/v1/public/metrics/")
        self.check("supresión k=5", public["min_cell_size"], 5)
        # Los canjes solo corresponden a cuatro sujetos en esta copia: no pueden publicarse.
        self.check("canjes públicos suprimidos", public["totals"]["redemptions"], None)
        self.check(
            "motivo supresión registrado",
            "redemptions" in public["totals"]["suppressed"],
            True,
        )
        encoded = json.dumps(public)
        self.check("dashboard público sin correo", "@example.test" in encoded, False)

    def run(self):
        self.check("copia inicial de 36 reportes", self.counts()["reports"], 36)
        institutions = {
            slug: str(uid) for uid, slug in self.sql("SELECT id,slug FROM institutions")
        }
        categories = {slug: str(uid) for uid, slug in self.sql("SELECT id,slug FROM categories")}
        muni = institutions["municipalidad-de-asuncion"]
        operator = "operator-municipalidad-de-asuncion"
        for label, actor in self.actors.items():
            session = self.http("sesión " + label, "GET", "/api/v1/auth/me/", actor=label)
            self.check(
                label + ": identidad sesión",
                [session["id"], session["role"]],
                [actor["userId"], actor["role"]],
            )
        # Comprobar snapshot original por HTTP antes de modificar la copia.
        for label in ["C00", "C01", "C02", "C03"]:
            self.summary(label)
        self.ranking()
        report = self.create_report("C03", categories["baches"], "ciclo-completo")
        before = self.states()
        self.http(
            "foto duplicada no crea reporte",
            "POST",
            "/api/v1/reports/",
            409,
            actor="C07",
            data={
                "category": categories["baches"],
                "description": "Reintento foto original S15",
                "lat": "-25.2637",
                "lng": "-57.6359",
            },
            uploads={"photo": synthetic_photo(self.run_id + "ciclo-completo")},
        )
        self.check("foto duplicada sin cambios SQL", self.states(), before)
        confirmation = f"/api/v1/reports/{report}/confirm/"
        self.negative(
            "autoconfirmación",
            confirmation,
            actor="C03",
            expected=409,
            code="SELF_CONFIRMATION_NOT_ALLOWED",
        )
        confirmed = self.http("confirmar tercero", "POST", confirmation, 201, actor="C04", data={})
        self.check(
            "confirmación recompensa",
            confirmed["reward"],
            {"impact_points": 10, "status": "PENDIENTE"},
        )
        self.negative(
            "confirmación duplicada",
            confirmation,
            actor="C04",
            expected=409,
            code="ALREADY_CONFIRMED",
        )
        action = f"/api/v1/operator/reports/{report}/validate/?institution_id={muni}"
        self.negative("anónimo no valida", action, expected=401)
        self.negative("ciudadano no valida", action, actor="C03")
        self.negative("otra institución no valida", action, actor="operator-ande")
        self.negative(
            "ciudadano no ve bandeja operador",
            f"/api/v1/operator/reports/{report}/",
            actor="C03",
            method="GET",
        )
        self.negative(
            "institución ajena no ve reporte",
            f"/api/v1/operator/reports/{report}/",
            actor="operator-ande",
            expected=404,
            method="GET",
        )
        self.lifecycle(report, muni, operator)
        self.check(
            "ciclo ledger exacto",
            self.report_ledger(report),
            [
                [
                    "CONFIRMATION_CREATED",
                    "ACREDITADO",
                    10,
                    10,
                    "municipalidad-de-asuncion",
                ],
                ["REPORT_CREATED", "ACREDITADO", 20, 20, "municipalidad-de-asuncion"],
                ["RESOLUTION_BONUS", "ACREDITADO", 50, 50, "municipalidad-de-asuncion"],
                ["VALIDATION_BONUS", "ACREDITADO", 30, 30, "municipalidad-de-asuncion"],
            ],
        )
        self.summary("C03")
        self.summary("C04")
        rejected = self.create_report("C05", categories["baches"], "rechazo")
        self.http(
            "confirmar antes de rechazo",
            "POST",
            f"/api/v1/reports/{rejected}/confirm/",
            201,
            actor="C06",
            data={},
        )
        self.http(
            "rechazar",
            "POST",
            f"/api/v1/operator/reports/{rejected}/reject/?institution_id={muni}",
            actor=operator,
            data={"reason": "duplicado"},
        )
        self.check(
            "rechazo anula pendientes",
            self.report_ledger(rejected),
            [
                ["CONFIRMATION_CREATED", "ANULADO", 10, 10, ""],
                ["REPORT_CREATED", "ANULADO", 20, 20, ""],
            ],
        )
        reward = str(self.sql("SELECT id FROM rewards WHERE institution_id=%s", [muni])[0][0])
        self.canje(categories["baches"], muni, operator, reward)
        self.dashboards()


def guard(settings):
    db = settings.DATABASES["default"]
    if db.get("NAME") != "ciudadania_s15" or db.get("HOST") != "ca-s15-db":
        raise FlowError("Base S15 API dedicada requerida.")
    if os.environ.get("S15_API_WORLD") != "ca-s15-api-local":
        raise FlowError("Falta la marca operativa de la copia exclusiva API S15.")
    if settings.AI_CLASSIFICATION_ENABLED or settings.S11_EXPERIMENTS_ENABLED:
        raise FlowError("Proveedores IA/S11 deben estar desactivados.")
    if settings.SUPABASE_URL != "http://127.0.0.1:18515" or not str(
        settings.SUPABASE_SERVICE_ROLE_KEY
    ).startswith("s15-synthetic-"):
        raise FlowError("Se requiere Storage sintético local.")
    if settings.REDIS_URL != "redis://ca-s15-redis:6379/0":
        raise FlowError("Redis dedicado requerido.")
    if (
        settings.DEBUG is not False
        or os.environ.get("DJANGO_SETTINGS_MODULE") != "tools.s15.measurement_settings"
    ):
        raise FlowError("Se requiere el perfil de medición DEBUG=False.")
    if b"gunicorn" not in Path("/proc/1/cmdline").read_bytes():
        raise FlowError("Ejecutar dentro del contenedor Gunicorn S15.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actors", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--manifest",
        default="/out/s15/s15-functional-v1-2026-10-20-15-36/manifest.json",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args(argv)
    actor_path = private_path(args.actors)
    manifest_path = private_path(args.manifest)
    output = private_path(args.output, output=True)
    import django

    django.setup()
    from django.conf import settings

    guard(settings)
    actors = json.loads(actor_path.read_bytes())
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    if actors.get("schemaVersion") != 1 or actors.get("datasetPlanSha256") != manifest.get(
        "plan_sha256"
    ):
        raise FlowError("Actores no corresponden al plan del dataset.")
    runner = Runner(args.base_url, actors["actors"], output)
    failure = None
    try:
        runner.run()
    except Exception as exc:
        failure = {"type": type(exc).__name__, "message": str(exc)[:300]}
    result = {
        "schemaVersion": 1,
        "runId": runner.run_id,
        "status": "FAIL" if failure else "PASS",
        "observedAt": datetime.now(UTC).isoformat(),
        "world": "ca-s15-api-local",
        "debug": settings.DEBUG,
        "applicationServer": "Gunicorn",
        "actorIds": sorted(runner.actors),
        "checks": runner.checks,
        "requests": runner.requests,
        "createdReports": runner.created,
        "failure": failure,
        "instrumentSha256": runner.instrument_sha256,
        "datasetManifestSha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "datasetPlanSha256": manifest["plan_sha256"],
        "notVerified": [
            "OAuth Google real",
            "IA proveedor real",
            "entrega de correo",
            "renderizado navegador",
            "SLA latencia de producción",
            "todo k=5 público",
            "todos los agregados de dashboards",
            "movimientos de cuentas no recorridas",
        ],
    }
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(
        json.dumps(
            {
                "status": result["status"],
                "checks": len(runner.checks),
                "passed": sum(c["passed"] for c in runner.checks),
                "failure": failure,
            }
        )
    )
    return 1 if failure else 0


if __name__ == "__main__":
    sys.exit(main())
