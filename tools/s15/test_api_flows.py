"""Pruebas del instrumento: sin Django, DB, Docker ni proveedores."""

import io
import json
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.request import ProxyHandler

from tools.s15.api_flows import (
    FlowError,
    NoRedirects,
    Runner,
    guard,
    month_bounds,
    multipart,
    synthetic_photo,
    validate_target,
    visible_name,
)


class Response(io.BytesIO):
    status = 200
    headers = {"Cache-Control": "private, no-store"}


class InstrumentTests(unittest.TestCase):
    def test_only_exact_local_target_is_accepted(self):
        validate_target("http://127.0.0.1:8000")
        for value in [
            "https://127.0.0.1:8000",
            "http://localhost:8000",
            "http://remote:8000",
            "http://127.0.0.1:8001",
            "http://key@127.0.0.1:8000",
            "http://127.0.0.1:8000/?token=key",
        ]:
            with self.subTest(value=value), self.assertRaises(FlowError):
                validate_target(value)

    def test_redirect_cannot_forward_bearer(self):
        with self.assertRaises(FlowError):
            NoRedirects().redirect_request(None, None, 302, "redirect", {}, "https://remote.test")

    def test_local_client_ignores_environment_proxy_without_network(self):
        with patch.dict(
            "os.environ",
            {
                "http_proxy": "http://proxy.invalid:8888",
                "HTTP_PROXY": "http://proxy.invalid:8888",
                "no_proxy": "",
                "NO_PROXY": "",
            },
            clear=True,
        ):
            runner = Runner("http://127.0.0.1:8000", [], None)
        self.assertFalse(
            any(isinstance(handler, ProxyHandler) for handler in runner.opener.handlers)
        )

    def test_month_boundary_is_asuncion_and_rolls_year(self):
        start, end = month_bounds(datetime(2027, 1, 1, 1, tzinfo=UTC))
        self.assertEqual((start.year, start.month, start.day), (2026, 12, 1))
        self.assertEqual((end.year, end.month, end.day), (2027, 1, 1))
        self.assertEqual(start.utcoffset().total_seconds(), -10800)

    def test_private_alias_and_name_preferences_are_independent_of_product(self):
        self.assertEqual(visible_name("Ana Pérez", "PRIVATE", ""), ("Usuario privado", True))
        self.assertEqual(visible_name("Ana Pérez", "ALIAS", " Vecina "), ("Vecina", False))
        self.assertEqual(visible_name("Ana Pérez", "FIRST_INITIAL", ""), ("Ana P.", False))
        self.assertEqual(visible_name("Ana Pérez", None, ""), ("Usuario privado", True))

    def test_multipart_preserves_photo_bytes_and_fields(self):
        photo = synthetic_photo("case1")
        data, content_type = multipart({"cycle": 1}, {"file": photo})
        self.assertIn(photo, data)
        self.assertIn(b'name="cycle"\r\n\r\n1\r\n', data)
        self.assertTrue(content_type.startswith("multipart/form-data; boundary=s15-"))
        self.assertNotEqual(photo, synthetic_photo("case2"))

    def test_receipt_hashes_body_and_never_records_capabilities(self):
        runner = Runner("http://127.0.0.1:8000", [{"id": "C00", "accessToken": "jwt-secret"}], None)
        runner.opener = Mock()
        runner.opener.open.return_value = Response(
            json.dumps({"signed_url": "http://private?token=capability"}).encode()
        )
        code, body, cache = runner.request("receipt", "GET", "/api/v1/example/", actor="C00")
        self.assertEqual(code, 200)
        self.assertEqual(body["signed_url"], "http://private?token=capability")
        receipt = json.dumps(runner.requests)
        self.assertNotIn("capability", receipt)
        self.assertNotIn("jwt-secret", receipt)
        self.assertEqual(len(runner.requests[0]["responseSha256"]), 64)

    def test_negative_detects_effect_despite_correct_http_error(self):
        runner = Runner("http://127.0.0.1:8000", [], None)
        runner.states = Mock(side_effect=[{"reward_transactions": 3}, {"reward_transactions": 4}])
        runner.http = Mock(return_value={"code": "ALREADY_CONFIRMED"})
        with self.assertRaises(FlowError):
            runner.negative("duplicate", "/example", expected=409, code="ALREADY_CONFIRMED")
        self.assertFalse(runner.checks[-1]["passed"])

    def test_snapshot_detects_in_place_update_without_new_row(self):
        runner = Runner("http://127.0.0.1:8000", [], None)
        runner.sql = Mock(
            side_effect=[
                [('{"id":1,"status":"CREADO"}',)],
                [('{"id":1,"status":"ASIGNADO"}',)],
            ]
        )
        from unittest.mock import patch

        with patch("tools.s15.api_flows.TABLES", ("reports",)):
            before, after = runner.states(), runner.states()
        self.assertEqual(before["reports"]["rows"], after["reports"]["rows"])
        self.assertNotEqual(before["reports"]["sha256"], after["reports"]["sha256"])

    def test_environment_guard_rejects_real_storage_before_network(self):
        settings = SimpleNamespace(
            DATABASES={"default": {"NAME": "ciudadania_s15", "HOST": "ca-s15-db"}},
            AI_CLASSIFICATION_ENABLED=False,
            S11_EXPERIMENTS_ENABLED=False,
            SUPABASE_URL="https://remote.supabase.co",
            SUPABASE_SERVICE_ROLE_KEY="secret",
        )
        from unittest.mock import patch

        with patch.dict("os.environ", {"S15_API_WORLD": "ca-s15-api-local"}):
            with self.assertRaises(FlowError):
                guard(settings)


if __name__ == "__main__":
    unittest.main()
