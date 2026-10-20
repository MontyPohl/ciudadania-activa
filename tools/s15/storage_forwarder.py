"""Expone solo GET de capacidades de Storage al navegador local S15.

Escucha 0.0.0.0:18517 únicamente dentro de la red Docker interna; el contenedor
debe publicar 127.0.0.1:18515:18517. El cliente de Django sigue usando su
loopback:18515, y ninguna credencial de servicio llega a este forwarder.
"""

import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

MAX_BYTES = 5 * 1024 * 1024
UPSTREAM = "http://127.0.0.1:18515"


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Storage no puede redirigir solicitudes.")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def reply(self, status, body=b"", mime="application/octet-stream"):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path)
        if (
            path.scheme
            or path.netloc
            or path.fragment
            or not path.path.startswith("/storage/v1/object/sign/")
        ):
            self.reply(404)
            return
        try:
            opener = build_opener(ProxyHandler({}), NoRedirects())
            request = Request(UPSTREAM + self.path, method="GET", headers={"Accept": "image/*"})
            try:
                response = opener.open(request, timeout=10)
            except HTTPError as exc:
                response = exc
            with response:
                body = response.read(MAX_BYTES + 1)
                status = response.status
                mime = response.headers.get("Content-Type", "application/octet-stream")
            if len(body) > MAX_BYTES:
                self.reply(502)
                return
            if status == 200 and mime not in {"image/jpeg", "image/png"}:
                self.reply(502)
                return
            self.reply(status, body, mime)
        except (ValueError, OSError):
            self.reply(502)

    def do_POST(self):
        self.reply(405)

    do_DELETE = do_POST
    do_PUT = do_POST
    do_PATCH = do_POST


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        # No registrar capacidades ni contenido ante una conexión cancelada.
        pass


def main():
    if os.environ.get("S15_BROWSER_WORLD") != "ca-s15-browser-local":
        raise SystemExit("Forwarder permitido solo en el mundo de navegador S15.")
    with Server(("0.0.0.0", 18517), Handler) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
