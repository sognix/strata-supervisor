"""Tests for Strata's API on the web UI port (web/proxy.py). Run: python3 -m unittest discover -s test/python"""

from __future__ import annotations

import http.client
import json
import socket
import sys
import threading
import time
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app" / "backend"))

from strata_webui.strata.placeholder import Placeholder  # noqa: E402
from strata_webui.web import server  # noqa: E402


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class FakeStrata(BaseHTTPRequestHandler):
    """Stands in for Strata's server: echoes what it got, or streams server-sent events."""

    protocol_version = "HTTP/1.1"
    seen: list[dict] = []
    release_second = threading.Event()
    stream_cut = threading.Event()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def do_POST(self) -> None:  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        FakeStrata.seen.append({"path": self.path, "headers": dict(self.headers.items()), "body": body})
        if self.path == "/v1/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")  # SSE without a length: the answer ends when the server closes
            self.end_headers()
            self.wfile.write(b"data: one\n\n")
            self.wfile.flush()
            FakeStrata.release_second.wait(5)
            self.wfile.write(b"data: two\n\n")
            self.wfile.flush()
            self.close_connection = True
            return
        if self.path == "/v1/endless":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                for _ in range(500):
                    self.wfile.write(b"data: x\n\n")
                    self.wfile.flush()
                    time.sleep(0.01)
            except OSError:
                FakeStrata.stream_cut.set()  # the proxy closed its side: Strata would stop generating here
            self.close_connection = True
            return
        payload = json.dumps({"ok": True, "got": body.decode()}).encode()
        self.send_response(201)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Strata", "yes")
        self.end_headers()
        self.wfile.write(payload)


class ProxyTest(unittest.TestCase):
    def setUp(self) -> None:
        FakeStrata.seen = []
        FakeStrata.release_second = threading.Event()
        FakeStrata.stream_cut = threading.Event()
        self.api_port = free_port()
        self.ui_port = free_port()
        app = types.SimpleNamespace(allowed_hosts=frozenset({"tower"}))
        self.ui = server.make_server(app, self.ui_port, host="127.0.0.1", api_port=self.api_port)
        threading.Thread(target=self.ui.serve_forever, daemon=True).start()
        self.addCleanup(self.ui.server_close)
        self.addCleanup(self.ui.shutdown)

    def start_strata(self) -> None:
        upstream = ThreadingHTTPServer(("127.0.0.1", self.api_port), FakeStrata)
        upstream.daemon_threads = True
        threading.Thread(target=upstream.serve_forever, daemon=True).start()
        self.addCleanup(upstream.server_close)
        self.addCleanup(upstream.shutdown)

    def conn(self) -> http.client.HTTPConnection:
        return http.client.HTTPConnection("127.0.0.1", self.ui_port, timeout=10)

    def test_request_goes_on_unchanged(self) -> None:
        self.start_strata()
        c = self.conn()
        c.request("POST", "/v1/chat/completions?x=1", body=b'{"a": 1}', headers={
            "Host": "evil.example", "Origin": "http://other.page", "Authorization": "Bearer key",
            "Content-Type": "application/json", "Keep-Alive": "timeout=5", "Connection": "keep-alive, X-Drop", "X-Drop": "1"})
        r = c.getresponse()
        self.assertEqual((r.status, r.getheader("X-Strata")), (201, "yes"))
        self.assertEqual(json.loads(r.read()), {"ok": True, "got": '{"a": 1}'})
        got = FakeStrata.seen[0]
        self.assertEqual(got["path"], "/v1/chat/completions?x=1")
        headers = {k.lower(): v for k, v in got["headers"].items()}
        # Host and Origin as the client sent them: Strata makes its own checks; our ALLOWED_HOSTS does not apply
        self.assertEqual((headers["host"], headers["origin"], headers["authorization"]),
                         ("evil.example", "http://other.page", "Bearer key"))
        self.assertNotIn("keep-alive", headers)
        self.assertNotIn("x-drop", headers)
        # the same client connection serves a second request (keep-alive kept on our side)
        c.request("POST", "/v1/x", body=b"2", headers={"Content-Type": "application/json"})
        self.assertEqual(c.getresponse().read(), b'{"ok": true, "got": "2"}')
        c.close()

    def test_chunked_request_body(self) -> None:
        self.start_strata()
        c = self.conn()
        c.request("POST", "/v1/x", body=iter([b"ab", b"cd"]), headers={"Content-Type": "application/json"}, encode_chunked=True)
        self.assertEqual(json.loads(c.getresponse().read())["got"], "abcd")
        c.close()

    def test_stream_arrives_piece_by_piece(self) -> None:
        self.start_strata()
        c = self.conn()
        c.request("POST", "/v1/stream", body=b"{}", headers={"Content-Type": "application/json"})
        r = c.getresponse()
        self.assertEqual((r.status, r.getheader("Content-Type")), (200, "text/event-stream"))
        self.assertEqual(r.read1(100), b"data: one\n\n")  # before Strata sends the second event
        FakeStrata.release_second.set()
        self.assertEqual(r.read(), b"data: two\n\n")
        c.close()

    def test_client_going_away_ends_the_request_at_strata(self) -> None:
        self.start_strata()
        c = self.conn()
        c.request("POST", "/v1/endless", body=b"{}", headers={"Content-Type": "application/json"})
        r = c.getresponse()
        r.read1(100)
        c.sock.shutdown(socket.SHUT_RDWR)
        c.close()
        self.assertTrue(FakeStrata.stream_cut.wait(8))

    def test_nothing_listening(self) -> None:
        c = self.conn()
        c.request("GET", "/v1/models")
        r = c.getresponse()
        self.assertEqual(r.status, 503)
        self.assertEqual(json.loads(r.read())["error"]["type"], "service_unavailable")
        c.close()

    def test_placeholder_answers_through_the_proxy(self) -> None:
        placeholder = Placeholder(self.api_port, host="127.0.0.1")
        self.assertTrue(placeholder.hold())
        self.addCleanup(placeholder.release)
        c = self.conn()
        c.request("GET", "/v1/models")
        r = c.getresponse()
        self.assertEqual((r.status, json.loads(r.read())), (200, {"object": "list", "data": []}))
        c.request("POST", "/v1/chat/completions", body=b"{}", headers={"Content-Type": "application/json"})
        r = c.getresponse()
        self.assertEqual((r.status, json.loads(r.read())["error"]["code"]), (503, "no_model_loaded"))
        c.close()

    def test_rest_of_the_ui_keeps_its_checks(self) -> None:
        c = self.conn()
        c.request("GET", "/api/status", headers={"Host": "evil.example"})
        self.assertEqual(c.getresponse().status, 403)  # ALLOWED_HOSTS still applies outside /v1
        c.close()
        c = self.conn()
        c.request("PUT", "/api/status", body=b"", headers={"Host": "tower"})
        self.assertEqual(c.getresponse().status, 405)
        c.close()
        c = self.conn()
        c.request("GET", "/v1beta", headers={"Host": "tower"})
        self.assertEqual(c.getresponse().status, 404)  # only /v1 and below
        c.close()


if __name__ == "__main__":
    unittest.main()
