"""Storage en memoria solo para el E2E local del S14; nunca apuntar a producción."""

from __future__ import annotations

import argparse
import json
import sys
from hmac import compare_digest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock
from urllib.parse import parse_qs, quote, unquote, urlsplit


class FakeStorageServer(ThreadingHTTPServer):
    """Objetos efímeros compartidos por los hilos del servidor local."""

    def __init__(self, address: tuple[str, int], service_role: str):
        self.service_role = service_role
        self.objects: dict[tuple[str, str], tuple[bytes, str]] = {}
        self.lock = Lock()
        super().__init__(address, FakeStorageHandler)

    def handle_error(self, request, client_address):
        # El traceback por defecto podría exponer datos de la petición.
        print("ERROR 500", file=sys.stderr, flush=True)


class FakeStorageHandler(BaseHTTPRequestHandler):
    """Las cinco rutas REST mínimas que consume el cliente real de Storage."""

    server: FakeStorageServer

    def log_message(self, format, *args):
        # BaseHTTPRequestHandler incluye la URL en sus mensajes habituales.
        pass

    def log_request(self, code="-", size="-"):
        method = self.command if self.command in {"GET", "POST", "DELETE"} else "OTHER"
        print(f"{method} {code}", file=sys.stderr, flush=True)

    def send_error(self, code, message=None, explain=None):
        # También las rutas y métodos no implementados responden sin reflejar la URL.
        self._reply(404 if code == 501 else code)

    def do_GET(self):
        self._dispatch()

    def do_POST(self):
        self._dispatch()

    def do_DELETE(self):
        self._dispatch()

    def _reply(self, status: int, body: bytes = b"", content_type="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: dict):
        self._reply(status, json.dumps(payload).encode("utf-8"))

    def _read_body(self) -> bytes | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._reply(400)
            return None
        if length < 0:
            self._reply(400)
            return None
        return self.rfile.read(length)

    def _dispatch(self):
        try:
            url = urlsplit(self.path)
        except ValueError:
            self._reply(404)
            return
        prefix = "/storage/v1/object/"
        if not url.path.startswith(prefix):
            self._reply(404)
            return
        path = url.path[len(prefix) :]
        signed = path.startswith("sign/")
        if signed:
            path = path[len("sign/") :]
        bucket, separator, key = path.partition("/")
        if not separator or not bucket or not key:
            self._reply(404)
            return
        # Separar primero preserva los '/' escapados que pertenecen al bucket.
        bucket, key = unquote(bucket), unquote(key)
        object_id = (bucket, key)
        if signed and self.command == "GET":
            if parse_qs(url.query).get("token") != ["e2e-local"]:
                self._reply(403)
                return
        else:
            if signed and self.command != "POST":
                self._reply(404)
                return
            authorization = self.headers.get("Authorization", "").encode("utf-8")
            expected = f"Bearer {self.server.service_role}".encode()
            if not compare_digest(authorization, expected):
                self._reply(401)
                return

        if self.command == "GET":
            with self.server.lock:
                stored = self.server.objects.get(object_id)
            if stored is None:
                self._reply(404)
            else:
                content, content_type = stored
                self._reply(200, content, content_type)
        elif self.command == "DELETE":
            with self.server.lock:
                self.server.objects.pop(object_id, None)
            self._json(200, {})
        elif signed:
            self._sign(object_id)
        else:
            self._upload(object_id)

    def _upload(self, object_id: tuple[str, str]):
        content = self._read_body()
        if content is None:
            return
        content_type = self.headers.get("Content-Type", "application/octet-stream")
        with self.server.lock:
            conflict = object_id in self.server.objects and self.headers.get("x-upsert") == "false"
            if not conflict:
                self.server.objects[object_id] = (content, content_type)
        if conflict:
            self._reply(400)
        else:
            self._json(200, {"Key": "/".join(object_id)})

    def _sign(self, object_id: tuple[str, str]):
        body = self._read_body()
        if body is None:
            return
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            self._reply(400)
            return
        expires_in = payload.get("expiresIn") if isinstance(payload, dict) else None
        if type(expires_in) is not int or expires_in <= 0:
            self._reply(400)
            return
        with self.server.lock:
            exists = object_id in self.server.objects
        if not exists:
            self._reply(404)
            return
        bucket, key = object_id
        path = f"{quote(bucket, safe='')}/{quote(key, safe='/')}"
        self._json(200, {"signedURL": f"/object/sign/{path}?token=e2e-local"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=59014)
    parser.add_argument("--service-role", required=True)
    args = parser.parse_args()
    if not args.service_role:
        parser.error("La service-role local no puede estar vacía.")
    with FakeStorageServer((args.host, args.port), args.service_role) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
