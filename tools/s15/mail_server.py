"""Sustituto local de Brevo: acusa recepción sin enviar correo ni guardar contenido."""

import argparse
import hashlib
import hmac
import json
import os
import threading
import uuid
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class MailServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, key, output):
        if address[0] != "127.0.0.1" or not key.startswith("s15-synthetic-"):
            raise ValueError("Solo se admite correo sintético en loopback.")
        output = Path(output)
        if (
            not output.is_absolute()
            or not output.is_relative_to(Path("/out/s15"))
            or any(p.is_symlink() for p in (output, *output.parents))
            or output.resolve() != output
            or not output.parent.is_dir()
            or output.parent.stat().st_mode & 0o077
        ):
            raise ValueError("Se requiere una salida privada nueva bajo /out/s15.")
        self.descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self.key = key
        self.lock = threading.Lock()
        self.count = 0
        super().__init__(address, MailHandler)

    def record(self, payload):
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        with self.lock:
            self.count += 1
            receipt = {
                "ordinal": self.count,
                "receivedAt": datetime.now(UTC).isoformat(),
                "payloadSha256": hashlib.sha256(raw).hexdigest(),
                "scope": "local acceptance only; no external mail delivery",
            }
            os.write(self.descriptor, (json.dumps(receipt) + "\n").encode())
            os.fsync(self.descriptor)

    def server_close(self):
        try:
            super().server_close()
        finally:
            os.close(self.descriptor)


class MailHandler(BaseHTTPRequestHandler):
    server: MailServer

    def log_message(self, format, *args):
        pass

    def do_POST(self):
        try:
            if self.path != "/v3/smtp/email":
                self.reply(404)
                return
            if not hmac.compare_digest(self.headers.get("api-key", ""), self.server.key):
                self.reply(401)
                return
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= 1024 * 1024:
                raise ValueError("Tamaño inválido.")
            payload = json.loads(self.rfile.read(length))
            if (
                not isinstance(payload, dict)
                or not isinstance(payload.get("to"), list)
                or len(payload["to"]) != 1
                or not isinstance(payload["to"][0], dict)
                or not isinstance(payload["to"][0].get("email"), str)
                or not isinstance(payload.get("subject"), str)
                or not isinstance(payload.get("textContent"), str)
            ):
                raise ValueError("Correo sintético inválido.")
            self.server.record(payload)
            self.reply(201, {"messageId": f"<s15-{uuid.uuid4()}@local.invalid>"})
        except (ValueError, TypeError, OSError):
            self.reply(400)

    def reply(self, status, payload=None):
        raw = json.dumps(payload or {}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18516)
    args = parser.parse_args()
    key = os.environ.get("BREVO_API_KEY", "")
    with MailServer(("127.0.0.1", args.port), key, args.output) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
