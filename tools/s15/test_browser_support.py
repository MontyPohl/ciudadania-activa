"""Contratos del perfil, frontera de Storage y detector prospectivo, sin servicios."""

import hashlib
import hmac
import http.client
import io
import json
import secrets
import threading
import types
import unittest
import uuid
from http.server import HTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

from tools.s15 import browser_seed
from tools.s15.browser_profile import validate_browser_environment
from tools.s15.browser_relay import validate_target
from tools.s15.local_profile import ProfileError
from tools.s15.storage_forwarder import Handler, NoRedirects
from tools.s15.test_local_profile import environment


class ImageResponse(io.BytesIO):
    status = 200
    headers = {"Content-Type": "image/png"}


class BrowserProfileTests(unittest.TestCase):
    def test_profile_uses_copy_and_exact_browser_world(self):
        values = environment()
        values.update(
            DJANGO_SETTINGS_MODULE="tools.s15.browser_settings",
            S15_BROWSER_WORLD="ca-s15-browser-local",
        )
        original = dict(values)
        self.assertEqual(validate_browser_environment(values)["frontend"], "http://127.0.0.1:3115")
        self.assertEqual(values, original)
        for key, value in [
            ("S15_BROWSER_WORLD", "another-world"),
            ("DJANGO_SETTINGS_MODULE", "config.settings.prod"),
            ("SUPABASE_URL", "https://real.supabase.co"),
        ]:
            changed = {**values, key: value}
            with self.subTest(key=key), self.assertRaises(ProfileError):
                validate_browser_environment(changed)


class RelayTests(unittest.TestCase):
    def test_target_requires_labeled_internal_network_and_browser_profile(self):
        container = {
            "Config": {
                "Labels": {"ciudadania.sprint": "15"},
                "Env": [
                    "S15_BROWSER_WORLD=ca-s15-browser-local",
                    "DJANGO_SETTINGS_MODULE=tools.s15.browser_settings",
                ],
            },
            "NetworkSettings": {
                "Networks": {"ca-s15-browser-local": {"IPAddress": "192.168.64.4"}}
            },
        }
        network = {
            "Internal": True,
            "Labels": {"ciudadania.sprint": "15"},
            "IPAM": {"Config": [{"Subnet": "192.168.64.0/20"}]},
        }
        self.assertEqual(validate_target(container, network), "192.168.64.4")
        network["Internal"] = False
        with self.assertRaises(ValueError):
            validate_target(container, network)

    def test_public_target_is_rejected_even_if_labels_match(self):
        container = {
            "Config": {
                "Labels": {"ciudadania.sprint": "15"},
                "Env": [
                    "S15_BROWSER_WORLD=ca-s15-browser-local",
                    "DJANGO_SETTINGS_MODULE=tools.s15.browser_settings",
                ],
            },
            "NetworkSettings": {"Networks": {"ca-s15-browser-local": {"IPAddress": "8.8.8.8"}}},
        }
        network = {
            "Internal": True,
            "Labels": {"ciudadania.sprint": "15"},
            "IPAM": {"Config": [{"Subnet": "0.0.0.0/0"}]},
        }
        with self.assertRaises(ValueError):
            validate_target(container, network)


class ForwarderTests(unittest.TestCase):
    def setUp(self):
        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method, path, headers=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        try:
            connection.request(method, path, headers=headers or {})
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_only_signed_get_is_forwarded_without_client_credentials(self):
        opener = Mock()
        opener.open.return_value = ImageResponse(b"synthetic-png")
        with patch("tools.s15.storage_forwarder.build_opener", return_value=opener):
            status, body = self.request(
                "GET",
                "/storage/v1/object/sign/s15-private/test?token=fake",
                {"Authorization": "Bearer must-not-forward", "Cookie": "session=must-not-forward"},
            )
        self.assertEqual((status, body), (200, b"synthetic-png"))
        forwarded = opener.open.call_args.args[0]
        self.assertEqual(
            forwarded.full_url,
            "http://127.0.0.1:18515/storage/v1/object/sign/s15-private/test?token=fake",
        )
        self.assertIsNone(forwarded.get_header("Authorization"))
        self.assertIsNone(forwarded.get_header("Cookie"))

    def test_private_paths_and_mutations_never_reach_upstream(self):
        with patch("tools.s15.storage_forwarder.build_opener") as build:
            for method, path, code in [
                ("GET", "/storage/v1/object/s15-private/test", 404),
                ("POST", "/storage/v1/object/sign/s15-private/test", 405),
                ("DELETE", "/storage/v1/object/sign/s15-private/test", 405),
            ]:
                with self.subTest(method=method):
                    self.assertEqual(self.request(method, path)[0], code)
            build.assert_not_called()

    def test_redirect_is_rejected(self):
        with self.assertRaises(ValueError):
            NoRedirects().redirect_request(None, None, 302, "redirect", {}, "https://remote.test")


class CanaryTests(unittest.TestCase):
    def fixture(self):
        names = [
            "author",
            "confirmer",
            "applicant",
            "operator",
            "admin",
            "institution",
            "report",
            "redemption",
            "profile",
            "evidence",
            "confirmation",
            "category",
            "creation_tx",
            "confirmation_tx",
            "validation_tx",
            "resolution_tx",
            "debit",
        ]
        ids = {name: uuid.uuid4() for name in names}
        canonical = "99015" + f"{secrets.randbelow(100000):05}"
        formatted = f"{int(canonical):,}".replace(",", ".")
        secret = "s15-synthetic-test-key-not-production"
        digest = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
        actor_names = {
            "citizen_a": "author",
            "citizen_b": "confirmer",
            "citizen_c": "applicant",
            "operator": "operator",
            "admin": "admin",
        }
        actors = {name: {"id": str(ids[role])} for name, role in actor_names.items()}
        actors.update(
            {
                "runId": "browser-unit",
                "ande_id": str(ids["institution"]),
                "cedula_c": formatted,
                "report_description": "synthetic",
                "datasetSha256": "a" * 64,
                "referenceMonth": "2026-10",
                "reward_id": str(uuid.uuid4()),
                "expected": {
                    "after_redemption": 10,
                    "after_resolution": 1010,
                    "ranking_points_after_resolution": 1030,
                },
            }
        )
        journey = {
            "runId": "browser-unit",
            "reportId": str(ids["report"]),
            "redemptionId": str(ids["redemption"]),
            "evidenceId": str(ids["evidence"]),
            "completedFlows": [f"F{n}" for n in range(1, 9)],
            "schemaVersion": 1,
            "datasetSha256": actors["datasetSha256"],
            "referenceMonth": actors["referenceMonth"],
            "source": "browser-bff-django-postgis-redis-local-storage",
            "contextCloseErrors": 0,
            "dashboardCaptured": True,
            "actorIds": {
                name: data["id"]
                for name, data in actors.items()
                if isinstance(data, dict) and "id" in data
            },
        }
        rid, inst = str(ids["report"]), str(ids["institution"])
        base = {"report_id": rid, "institution_id": inst, "cycle": 1}
        audit = [
            (
                "report.created",
                ids["author"],
                "human",
                {"report_id": rid, "category_id": str(ids["category"]), "status": "CREADO"},
            ),
            (
                "report.confirmed",
                ids["confirmer"],
                "human",
                {"report_id": rid, "confirmation_id": str(ids["confirmation"])},
            ),
            (
                "report.validated",
                ids["operator"],
                "human",
                {
                    **base,
                    "credited_transaction_ids": [
                        str(ids["creation_tx"]),
                        str(ids["confirmation_tx"]),
                    ],
                    "validation_bonus_transaction_id": str(ids["validation_tx"]),
                },
            ),
            (
                "report.processing_started",
                ids["operator"],
                "human",
                {**base, "previous_status": "ASIGNADO", "new_status": "EN_PROCESO"},
            ),
            (
                "report.resolution_evidence_created",
                ids["operator"],
                "human",
                {**base, "evidence_id": str(ids["evidence"])},
            ),
            (
                "report.resolved",
                ids["operator"],
                "human",
                {
                    **base,
                    "previous_status": "EN_PROCESO",
                    "new_status": "RESUELTO",
                    "evidence_ids": [str(ids["evidence"])],
                    "resolution_bonus_transaction_id": str(ids["resolution_tx"]),
                },
            ),
        ]
        redemption_audit = [
            (
                "reward.redeemed",
                ids["author"],
                "human",
                {
                    "redemption_id": str(ids["redemption"]),
                    "reward_id": actors["reward_id"],
                    "institution_id": inst,
                    "period": actors["referenceMonth"],
                    "credits_spent": 1000,
                    "balance_before": 1010,
                    "balance_after": 10,
                    "ledger_transaction_id": str(ids["debit"]),
                },
            )
        ]
        identity_audit = [
            (
                "identity.submitted",
                ids["applicant"],
                "human",
                {"profile_id": str(ids["profile"]), "user_id": str(ids["applicant"])},
            ),
            (
                "identity.approved",
                ids["admin"],
                "human",
                {"profile_id": str(ids["profile"]), "target_user_id": str(ids["applicant"])},
            ),
        ]
        state = {
            "actors": actors,
            "journey": journey,
            "ids": ids,
            "canonical": canonical,
            "formatted": formatted,
            "secret": secret,
            "digest": digest,
            "report_audit": audit,
            "redemption_audit": redemption_audit,
            "identity_audit": identity_audit,
            "history": [
                ("", "CREADO", 0, ids["author"]),
                ("CREADO", "ASIGNADO", 1, ids["operator"]),
                ("ASIGNADO", "EN_PROCESO", 1, ids["operator"]),
                ("EN_PROCESO", "RESUELTO", 1, ids["operator"]),
            ],
            "evidence": [
                (
                    ids["evidence"],
                    1,
                    ids["operator"],
                    f"resolution-evidence/{rid}/1/{ids['evidence']}.png",
                    "Reparación sintética",
                    "b" * 64,
                )
            ],
            "dump": b"-- PostgreSQL database dump\n"
            + digest.encode()
            + b"\n"
            + str(ids["applicant"]).encode(),
        }
        return state

    def run_fixture(self, state):
        ids, actors, journey = state["ids"], state["actors"], state["journey"]

        def sql(statement, parameters=()):
            if statement.startswith("SELECT id,status"):
                return [(ids["report"], "RESUELTO", 1, ids["institution"], ids["author"])]
            if statement.startswith("SELECT category_id"):
                return [(ids["category"],)]
            if "FROM report_status_history" in statement:
                return state["history"]
            if "FROM resolution_evidences" in statement:
                return state["evidence"]
            if statement.startswith("SELECT id FROM report_confirmations"):
                return [(ids["confirmation"],)]
            if statement.startswith("SELECT t.id,t.event_type"):
                return [
                    (
                        ids[tx],
                        kind,
                        ids[user],
                        1,
                        ids["institution"],
                        ids["confirmation"] if user == "confirmer" else None,
                    )
                    for tx, kind, user in [
                        ("confirmation_tx", "CONFIRMATION_CREATED", "confirmer"),
                        ("creation_tx", "REPORT_CREATED", "author"),
                        ("resolution_tx", "RESOLUTION_BONUS", "author"),
                        ("validation_tx", "VALIDATION_BONUS", "author"),
                    ]
                ]
            if "WHERE report_id=" in statement:
                return [
                    ("REPORT_CREATED", "ACREDITADO", 20, 20),
                    ("RESOLUTION_BONUS", "ACREDITADO", 50, 50),
                    ("VALIDATION_BONUS", "ACREDITADO", 30, 30),
                ]
            if statement.startswith("SELECT t.status"):
                return [("ACREDITADO", 10, 10)]
            if "SUM(credit_delta)" in statement:
                return [(10,)]
            if "SUM(impact_delta)" in statement:
                return [(1030,)]
            if "FROM reward_redemptions" in statement:
                return [(ids["redemption"], 1000)]
            if "WHERE redemption_id=" in statement:
                return (
                    [(ids["debit"], 0, -1000, "ACREDITADO")]
                    if statement.startswith("SELECT id,")
                    else [(0, -1000, "ACREDITADO")]
                )
            if "FROM citizen_profiles" in statement:
                return [(ids["profile"], state["digest"], "APROBADO", ids["admin"])]
            if "FROM audit_logs" in statement:
                if "'report_id'" in statement:
                    rows = state["report_audit"]
                elif "'redemption_id'" in statement:
                    rows = state["redemption_audit"]
                else:
                    rows = state["identity_audit"]
                return rows if "actor_user_id" in statement else [(row[0], row[3]) for row in rows]
            raise AssertionError("unexpected SQL")

        django = types.ModuleType("django")
        conf = types.ModuleType("django.conf")
        conf.settings = types.SimpleNamespace(DOCUMENT_HMAC_SECRET=state["secret"])
        paths = {
            Path("actors"): json.dumps(actors).encode(),
            Path("journey"): json.dumps(journey).encode(),
            Path("dump"): state["dump"],
        }
        with (
            patch.dict("sys.modules", {"django": django, "django.conf": conf}),
            patch.object(browser_seed, "query", side_effect=sql),
            patch.object(browser_seed, "read_private", side_effect=lambda path: paths[path]),
            patch.object(browser_seed, "write_private") as write,
        ):
            result = browser_seed.verify(
                Path("actors"), Path("dump"), [], Path("journey"), Path("output")
            )
        return result, write.call_args.args[1]

    def test_coherent_trace_and_canary_absence_pass(self):
        result, receipt = self.run_fixture(self.fixture())
        self.assertEqual(
            result["status"], "PASS", [c for c in receipt["checks"] if not c["passed"]]
        )

    def test_raw_cursor_jsonb_text_matches_decoded_audit_payloads(self):
        state = self.fixture()
        for name in ("report_audit", "redemption_audit", "identity_audit"):
            state[name] = [(*row[:3], json.dumps(row[3])) for row in state[name]]
        result, receipt = self.run_fixture(state)
        self.assertEqual(
            result["status"], "PASS", [c for c in receipt["checks"] if not c["passed"]]
        )

    def test_invalid_or_nonobject_audit_payload_cannot_pass(self):
        for value in ("{", "[]", "null", '"private-sentinel"', None, b"{}"):
            with self.subTest(payload_type=type(value).__name__):
                state = self.fixture()
                first = state["report_audit"][0]
                state["report_audit"][0] = (*first[:3], value)
                result, receipt = self.run_fixture(state)
                self.assertEqual(result["status"], "FAIL")
                self.assertNotIn("private-sentinel", json.dumps(receipt))

    def test_detector_rejects_plaintext_and_minimizes_receipt(self):
        state = self.fixture()
        state["dump"] += b"\ndeliberate leak: " + state["formatted"].encode()
        result, receipt = self.run_fixture(state)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(
            next(
                c["passed"] for c in receipt["checks"] if c["name"] == "HMAC independiente correcto"
            )
        )
        self.assertFalse(
            next(
                c["passed"]
                for c in receipt["checks"]
                if c["name"] == "database-logical-dump: canario ausente"
            )
        )
        self.assertNotIn(state["canonical"], json.dumps(receipt))
        self.assertNotIn(state["formatted"], json.dumps(receipt))

    def test_missing_history_cannot_pass_despite_correct_ledger(self):
        state = self.fixture()
        state["history"] = []
        self.assertEqual(self.run_fixture(state)[0]["status"], "FAIL")

    def test_missing_evidence_cannot_pass_despite_resolved_projection(self):
        state = self.fixture()
        state["evidence"] = []
        self.assertEqual(self.run_fixture(state)[0]["status"], "FAIL")

    def test_missing_audit_cannot_pass_despite_correct_ledger(self):
        for key in ["report_audit", "redemption_audit"]:
            with self.subTest(surface=key):
                state = self.fixture()
                state[key] = []
                self.assertEqual(self.run_fixture(state)[0]["status"], "FAIL")

    def test_mislinked_audit_bonus_is_rejected(self):
        state = self.fixture()
        state["report_audit"][2][3]["validation_bonus_transaction_id"] = str(
            state["ids"]["creation_tx"]
        )
        self.assertEqual(self.run_fixture(state)[0]["status"], "FAIL")

    def test_journey_integrity_is_required(self):
        for key, value in [
            ("datasetSha256", "c" * 64),
            ("contextCloseErrors", 1),
            ("dashboardCaptured", False),
            ("source", "intercepted-api"),
        ]:
            with self.subTest(field=key):
                state = self.fixture()
                state["journey"][key] = value
                self.assertEqual(self.run_fixture(state)[0]["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
