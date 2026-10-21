"""Prueba el contrato REST local sin proveedores externos."""

import importlib.util
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

SPEC = importlib.util.spec_from_file_location(
    "s15_storage", Path(__file__).with_name("storage_server.py")
)
storage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(storage)
PNG = b"\x89PNG\r\n\x1a\nsynthetic-test-bytes"


class StorageContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.server = storage.LocalStorageServer(("127.0.0.1", 0), self.root, "s15-synthetic-tests")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}/storage/v1"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, method, path, body=None, *, authorize=True, mime="image/png"):
        headers = {"Content-Type": mime}
        if authorize:
            headers["Authorization"] = "Bearer s15-synthetic-tests"
        req = Request(self.base + path, data=body, method=method, headers=headers)
        try:
            with urlopen(req, timeout=3) as response:
                return response.status, response.read()
        except HTTPError as error:
            with error:
                return error.code, error.read()

    def test_persistent_upload_sign_download_and_delete(self):
        path = "/object/s15-reports/reports/report/photo.png"
        self.assertEqual(self.request("POST", path, PNG)[0], 200)
        self.assertEqual(
            (self.root / "objects/s15-reports/reports/report/photo.png").read_bytes(), PNG
        )
        self.assertEqual(self.request("POST", path, PNG)[0], 409)
        status, body = self.request(
            "POST",
            path.replace("/object/", "/object/sign/"),
            json.dumps({"expiresIn": 300}).encode(),
            mime="application/json",
        )
        self.assertEqual(status, 200)
        signed = json.loads(body)["signedURL"]
        self.assertEqual(self.request("GET", signed, authorize=False), (200, PNG))
        self.assertEqual(self.request("GET", path, authorize=False)[0], 401)
        self.assertEqual(self.request("DELETE", path)[0], 200)
        self.assertEqual(self.request("GET", path)[0], 404)

    def test_tampered_and_expired_capabilities_are_rejected(self):
        path = "/object/s15-private/verifications/photo.png"
        self.request("POST", path, PNG)
        expires = int(time.time()) - 5
        token = self.server.signature("s15-private", "verifications/photo.png", expires)
        signed = f"/object/sign/s15-private/verifications/photo.png?expires={expires}&token={token}"
        self.assertEqual(self.request("GET", signed, authorize=False)[0], 403)
        self.assertEqual(
            self.request("GET", signed.replace(token, "broken"), authorize=False)[0], 403
        )

    def test_traversal_buckets_symlinks_and_mime_fail_closed(self):
        for key in ["../escape.png", "/escape.png", "a\\b.png"]:
            with self.assertRaises(ValueError):
                storage.object_path(self.root, "s15-reports", key)
        with self.assertRaises(ValueError):
            storage.object_path(self.root, "production", "photo.png")
        (self.root / "objects").symlink_to(self.root.parent, target_is_directory=True)
        with self.assertRaises(ValueError):
            storage.object_path(self.root, "s15-reports", "photo.png")
        self.assertEqual(self.request("POST", "/object/s15-reports/a.png", b"not-a-photo")[0], 400)


if __name__ == "__main__":
    unittest.main()
