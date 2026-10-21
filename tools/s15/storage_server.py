"""Storage REST persistente y sintético para el ensayo local S15."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, quote, unquote, urlsplit

BUCKETS = frozenset({"s15-private", "s15-reports"})
MAX_BYTES = 5 * 1024 * 1024


def object_path(root: Path, bucket: str, key: str) -> Path:
    parts = PurePosixPath(key).parts
    if (
        bucket not in BUCKETS
        or not parts
        or key.startswith("/")
        or "\\" in key
        or "\x00" in key
        or any(part in {".", ".."} for part in key.split("/"))
    ):
        raise ValueError("Objeto local inválido.")
    base = root / "objects"
    result = base / bucket / key
    for parent in (base, *result.parents):
        if parent.is_symlink():
            raise ValueError("No se permiten enlaces simbólicos.")
        if parent == root:
            break
    if result.is_symlink() or not result.resolve().is_relative_to(base.resolve()):
        raise ValueError("Objeto fuera del almacén local.")
    return result


def content_type(content: bytes) -> str:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    raise ValueError("Solo se permiten objetos sintéticos PNG/JPEG.")


class LocalStorageServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, root: Path, service_role: str):
        self.root = root.resolve()
        self.service_role = service_role
        super().__init__(address, LocalStorageHandler)

    def signature(self, bucket: str, key: str, expires: int) -> str:
        message = f"{bucket}\n{key}\n{expires}".encode()
        return hmac.new(self.service_role.encode(), message, hashlib.sha256).hexdigest()

    def handle_error(self, request, client_address):
        # No imprimir payloads, firmas, claves ni rutas de objetos.
        pass


class LocalStorageHandler(BaseHTTPRequestHandler):
    server: LocalStorageServer

    def log_message(self, format, *args):
        pass

    def _reply(self, status: int, payload: bytes = b"", mime="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, status: int, payload: dict):
        self._reply(status, json.dumps(payload).encode())

    def _body(self, maximum=MAX_BYTES) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 <= length <= maximum:
            raise ValueError("Tamaño no permitido.")
        return self.rfile.read(length)

    def do_GET(self):
        self._dispatch()

    def do_POST(self):
        self._dispatch()

    def do_DELETE(self):
        self._dispatch()

    def _dispatch(self):
        try:
            self._object_request()
        except (ValueError, OSError, UnicodeError):
            self._reply(400)

    def _object_request(self):
        url = urlsplit(self.path)
        prefix = "/storage/v1/object/"
        if not url.path.startswith(prefix):
            self._reply(404)
            return
        path = url.path[len(prefix) :]
        signed = path.startswith("sign/")
        if signed:
            path = path[len("sign/") :]
        raw_bucket, separator, raw_key = path.partition("/")
        if not separator:
            raise ValueError("Ruta inválida.")
        bucket, key = unquote(raw_bucket), unquote(raw_key)
        target = object_path(self.server.root, bucket, key)
        if signed and self.command == "GET":
            query = parse_qs(url.query)
            expires = int(query.get("expires", ["0"])[0])
            signatures = query.get("token", [])
            if (
                expires < int(time.time())
                or len(signatures) != 1
                or not hmac.compare_digest(
                    signatures[0], self.server.signature(bucket, key, expires)
                )
            ):
                self._reply(403)
                return
        elif not hmac.compare_digest(
            self.headers.get("Authorization", "").encode(),
            f"Bearer {self.server.service_role}".encode(),
        ):
            self._reply(401)
            return

        if self.command == "GET":
            if not target.is_file():
                self._reply(404)
                return
            data = target.read_bytes()
            self._reply(200, data, content_type(data))
        elif self.command == "DELETE" and not signed:
            target.unlink(missing_ok=True)
            self._json(200, {})
        elif self.command == "POST" and signed:
            payload = json.loads(self._body(4096))
            ttl = payload.get("expiresIn") if isinstance(payload, dict) else None
            if type(ttl) is not int or not 1 <= ttl <= 300:
                raise ValueError("TTL inválido.")
            if not target.is_file():
                self._reply(404)
                return
            expires = int(time.time()) + ttl
            token = self.server.signature(bucket, key, expires)
            suffix = f"{quote(bucket, safe='')}/{quote(key, safe='/')}"
            self._json(200, {"signedURL": f"/object/sign/{suffix}?expires={expires}&token={token}"})
        elif self.command == "POST" and not signed:
            data = self._body()
            mime = content_type(data)
            if self.headers.get("Content-Type") != mime:
                raise ValueError("MIME inválido.")
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            try:
                descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                self._reply(409)
                return
            with os.fdopen(descriptor, "wb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            self._json(200, {})
        else:
            self._reply(404)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18515)
    args = parser.parse_args()
    service_role = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not service_role.startswith("s15-synthetic-"):
        parser.error("Se requiere una credencial sintética exclusiva de S15.")
    root = args.root.resolve()
    allowed = Path("/out/s15")
    if not root.is_relative_to(allowed) or root == allowed:
        parser.error("El almacén debe estar en un subdirectorio privado de /out/s15.")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with LocalStorageServer(("127.0.0.1", args.port), root, service_role) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
