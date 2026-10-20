"""Contrato HTTP del doble de correo; sus recibos no conservan datos del mensaje."""

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tools.s15.mail_server import MailServer


class MailTests(unittest.TestCase):
    def test_accepts_auth_only_and_records_digest_without_contents(self):
        with tempfile.TemporaryDirectory(prefix="s15-mail-") as temporary:
            root = Path(temporary)
            # Solo la prueba sustituye el prefijo de salida; el CLI exige /out/s15.
            with patch("tools.s15.mail_server.Path.is_relative_to", return_value=True):
                server = MailServer(
                    ("127.0.0.1", 0), "s15-synthetic-mail-test-only", root / "receipts.jsonl"
                )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/v3/smtp/email"
                payload = {
                    "sender": {"email": "synthetic@example.invalid"},
                    "to": [{"email": "recipient@example.invalid"}],
                    "subject": "PRIVATE-SUBJECT-CANARY",
                    "textContent": "PRIVATE-BODY-CANARY",
                }
                request = Request(
                    url,
                    data=json.dumps(payload).encode(),
                    headers={"api-key": server.key, "Content-Type": "application/json"},
                )
                with urlopen(request, timeout=2) as result:
                    self.assertEqual(result.status, 201)
                request.remove_header("Api-key")
                with self.assertRaises(HTTPError) as result:
                    urlopen(request, timeout=2)
                result.exception.close()
                self.assertEqual(result.exception.code, 401)
                content = (root / "receipts.jsonl").read_text()
                receipt = json.loads(content)
                self.assertEqual(receipt["ordinal"], 1)
                self.assertNotIn("CANARY", content)
                self.assertNotIn("recipient", content)
                self.assertEqual((root / "receipts.jsonl").stat().st_mode & 0o077, 0)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

    def test_rejects_external_binding_and_real_key_before_creating_files(self):
        for host, key in [("0.0.0.0", "s15-synthetic-test"), ("127.0.0.1", "real-key")]:
            with self.assertRaises(ValueError):
                MailServer((host, 0), key, Path("/out/s15/unused.jsonl"))


if __name__ == "__main__":
    unittest.main()
