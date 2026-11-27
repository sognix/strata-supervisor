"""The web UI: a small JSON API plus one static page on UI_PORT, and Strata's API under /v1 (web/proxy.py).

No login for now, LAN use only (IMPLEMENTATION.md, section 5, point 8). Protection against other web
pages in the browser instead: every change is a POST with a JSON body, and a browser's Origin must
match the Host. ALLOWED_HOSTS (comma separated) optionally limits the Host header against DNS
rebinding. Model control and the HTTP layer are kept apart, so a login can be added later.
Requests under /v1 skip these checks: they go on to Strata unchanged, and Strata checks them as it checks a
direct call (API key, Host, Origin), so a client that works on the API port works here as well.
"""

from __future__ import annotations

import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from ..strata import model_config as mc
from ..strata.launcher import LaunchError, check_model_name
from . import proxy
from .api import AppState


# The page: app/frontend, next to app/backend, in the repo and in the image alike.
FRONTEND = Path(__file__).resolve().parents[3] / "frontend"

MAX_REQUEST_BODY = 256 * 1024

NAME_ROUTE = re.compile(r"/api/models/([^/]+)(?:/(load|plan|delete))?")

LOG_ROUTE = re.compile(r"/api/logs/([^/]+)")


class _Handler(BaseHTTPRequestHandler):
    server_version = "strata-supervisor"
    protocol_version = "HTTP/1.1"
    app: AppState  # set on the subclass made by make_server
    api_port: int | None = None  # Strata's port inside the container (PORT); None: no /v1 on this port

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    # ---- plumbing

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self._send(status, body, "application/json")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        if content_type.startswith("text/html"):
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _error(self, status: int, message: str) -> None:
        self._send_json(status, {"error": message})

    def _host_allowed(self) -> bool:
        allowed = self.app.allowed_hosts
        if not allowed:
            return True
        try:
            host = urlsplit("//" + (self.headers.get("Host") or "")).hostname or ""
        except ValueError:
            return False
        return host.lower() in allowed

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is None:
            return True  # not a browser request from another page (curl, scripts)
        parts = urlsplit(origin)
        return parts.scheme in ("http", "https") and parts.netloc.lower() == (self.headers.get("Host") or "").lower()

    def _read_json(self) -> dict | None:
        if self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            self._error(411, "Send a Content-Length.")
            return None
        raw = (self.headers.get("Content-Length") or "0").strip()
        if not raw.isdigit() or int(raw) > MAX_REQUEST_BODY:
            self.close_connection = True
            self._error(413, "The request body is missing a valid length or is too big.")
            return None
        body = self.rfile.read(int(raw)) if int(raw) else b""
        ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            self._error(415, "Changes need Content-Type: application/json.")
            return None
        try:
            data = json.loads(body or b"{}")
        except ValueError:
            self._error(400, "The body is not valid JSON.")
            return None
        if not isinstance(data, dict):
            self._error(400, "The body must be a JSON object.")
            return None
        return data

    # ---- routes

    def _api(self) -> bool:
        """Pass a /v1 request on to Strata. True when it was one."""
        if self.api_port is None or not proxy.is_api_path(urlsplit(self.path).path):
            return False
        proxy.forward(self, self.api_port)
        return True

    def do_GET(self) -> None:  # noqa: N802
        if self._api():
            return
        path = urlsplit(self.path).path
        if path == "/api/health":  # the container health check calls 127.0.0.1, whatever ALLOWED_HOSTS says
            self._send_json(200, {"ok": True})
            return
        if not self._host_allowed():
            self._error(403, "This host name is not allowed (ALLOWED_HOSTS).")
            return
        try:
            if path in ("/", "/index.html"):
                self._send(200, (FRONTEND / "index.html").read_bytes(), "text/html; charset=utf-8")
            elif path == "/app.js":
                self._send(200, (FRONTEND / "app.js").read_bytes(), "text/javascript; charset=utf-8")
            elif path == "/style.css":
                self._send(200, (FRONTEND / "style.css").read_bytes(), "text/css; charset=utf-8")
            elif path == "/logo.svg":
                self._send(200, (FRONTEND / "logo.svg").read_bytes(), "image/svg+xml")
            elif path == "/api/status":
                self._send_json(200, self.app.status())
            elif path == "/api/models":
                self._send_json(200, self.app.models())
            elif path == "/api/model-files":
                self._send_json(200, self.app.model_files())
            elif path == "/api/own-models":
                self._send_json(200, self.app.own_models())
            elif path == "/api/changelog":
                self._send_json(200, self.app.changelog())
            elif path == "/api/logs":
                self._send_json(200, self.app.logs())
            elif LOG_ROUTE.fullmatch(path):
                name = LOG_ROUTE.fullmatch(path).group(1)
                try:
                    check_model_name(name)
                except LaunchError as exc:
                    self._error(400, str(exc))
                    return
                result = self.app.model_logs(name)
                if result is None:
                    self._error(404, f"Nothing is known about {name}.")
                    return
                self._send_json(200, result)
            else:
                self._error(404, "Not found.")
        except OSError as exc:
            self._error(500, f"Could not read a file: {exc}")

    do_HEAD = do_GET

    def do_OTHER(self) -> None:  # noqa: N802 - PUT, DELETE, PATCH, OPTIONS: only Strata's API takes them
        if self._api():
            return
        self.close_connection = True  # the body, if any, is not read
        self._error(405, "Method not allowed.")

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_OTHER

    def do_POST(self) -> None:  # noqa: N802
        if self._api():
            return
        if not self._host_allowed():
            self._error(403, "This host name is not allowed (ALLOWED_HOSTS).")
            return
        if not self._origin_ok():
            self.close_connection = True
            self._error(403, "Changes are only accepted from this page (Origin does not match Host).")
            return
        body = self._read_json()
        if body is None:
            return
        path = urlsplit(self.path).path
        app = self.app
        try:
            if path == "/api/unload":
                self._send_json(202, app.unload())
                return
            if path == "/api/key":
                self._send_json(200, app.api_key())
                return
            if path == "/api/changelog/seen":
                self._send_json(200, app.changelog_seen())
                return
            if path == "/api/model-files/delete":
                self._send_json(200, app.delete_model_file(body))
                return
            match = NAME_ROUTE.fullmatch(path)
            if not match:
                self._error(404, "Not found.")
                return
            name, action = match.group(1), match.group(2)
            try:
                check_model_name(name)
            except LaunchError as exc:
                raise mc.ConfigError(str(exc)) from None
            if action is None:
                self._send_json(200, app.save(name, body))
            elif action == "delete":
                self._send_json(200, app.delete(name))
            elif action == "plan":
                self._send_json(200, app.plan(name, body.get("config")))
            elif action == "load":
                self._send_json(202, app.load(name))
        except (mc.ConfigError, LaunchError) as exc:
            self._error(400 if isinstance(exc, mc.ConfigError) else 409, str(exc))
        except OSError as exc:
            self._error(500, f"File error: {exc}")


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address) -> None:
        """A client that closes its connection (often mid-stream under /v1) is normal, not a traceback."""
        if isinstance(sys.exc_info()[1], ConnectionError):
            return
        super().handle_error(request, client_address)


def make_server(app: AppState, port: int, host: str = "", api_port: int | None = None) -> ThreadingHTTPServer:
    """`api_port`: Strata's port, for the API under /v1 on this port; None leaves /v1 out (404)."""
    handler = type("Handler", (_Handler,), {"app": app, "api_port": api_port})
    return _Server((host, port), handler)


def parse_allowed_hosts(raw: str) -> frozenset[str]:
    return frozenset(h.strip().lower() for h in raw.split(",") if h.strip())
