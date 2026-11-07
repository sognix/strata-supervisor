"""Placeholder on the API port while no model serves (IMPLEMENTATION.md, "Goals and scope").

Every request gets HTTP 503 with "No model loaded in Strata." in the OpenAI error format, except
GET /v1/models, which lists no models. It is not a proxy and never loads anything.

The port is released before `strata launch` starts, because the launch treats a busy port as a
conflict. It is taken back as soon as nothing serves on it any more: after an unload, a failed
launch or a crash. Binding is the test: while Strata's server listens, the bind fails.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MESSAGE = "No model loaded in Strata."
HOLD_RETRIES = 10
HOLD_RETRY_DELAY_S = 0.5
MAX_BODY = 1 << 20


class _Handler(BaseHTTPRequestHandler):
    server_version = "strata-supervisor-placeholder"
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - quiet, the UI has its own log
        pass

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _answer(self) -> None:
        self._drain_body()
        if self.command == "GET" and self.path.split("?", 1)[0].rstrip("/") == "/v1/models":
            self._send(200, {"object": "list", "data": []})
            return
        self._send(503, {"error": {"message": MESSAGE, "type": "service_unavailable", "code": "no_model_loaded"}})

    def _drain_body(self) -> None:
        """Read the request body so keep-alive stays in sync; close the connection when that is not possible.

        Keep-alive is kept for normal requests on purpose: closing every connection on the server side
        leaves TIME_WAIT sockets on the port, which could block the engine's bind right after a release.
        """
        if self.headers.get("Transfer-Encoding"):
            self.close_connection = True  # chunked bodies are not parsed here
            return
        raw = (self.headers.get("Content-Length") or "0").strip()
        if not raw.isdigit():
            self.close_connection = True
            return
        length = int(raw)
        if length > MAX_BODY:
            self.close_connection = True
            return
        if length:
            self.rfile.read(length)

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _answer


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True  # SO_REUSEADDR: allows a bind over TIME_WAIT, never over a live listener


class Placeholder:
    def __init__(self, port: int, host: str = "") -> None:
        self.port = port
        self._host = host
        self._lock = threading.Lock()
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None
        self.error: str | None = None

    @property
    def held(self) -> bool:
        return self._server is not None

    def try_hold(self) -> bool:
        """One bind attempt. True if the placeholder holds the port afterwards."""
        with self._lock:
            if self._server is not None:
                return True
            try:
                server = _Server((self._host, self.port), _Handler)
            except OSError as exc:
                self.error = f"Port {self.port} is busy ({exc.strerror or exc})."
                return False
            self._server = server
            self.error = None
            self._thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)
            self._thread.start()
            return True

    def hold(self, retries: int = HOLD_RETRIES, delay_s: float = HOLD_RETRY_DELAY_S) -> bool:
        """Bind with retries, for a port that may still be closing. Sets `error` if it never works."""
        for attempt in range(retries):
            if self.try_hold():
                return True
            if attempt + 1 < retries:
                time.sleep(delay_s)
        return False

    def release(self) -> None:
        with self._lock:
            server, self._server = self._server, None
            thread, self._thread = self._thread, None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if thread is not None:
            thread.join(timeout=2)


def port_is_free(port: int, host: str = "") -> bool:
    """Test helper and diagnosis: can this process bind the port right now?"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True
