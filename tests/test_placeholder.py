"""Tests for the API-port placeholder. Run: python3 -m unittest discover -s tests"""

from __future__ import annotations

import http.client
import json
import socket
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app" / "backend"))

from strata_webui.strata import placeholder  # noqa: E402


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def request(port: int, method: str, path: str, body: bytes | None = None) -> tuple[int, dict]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request(method, path, body=body, headers={"Content-Type": "application/json"} if body else {})
        response = conn.getresponse()
        return response.status, json.loads(response.read() or b"{}")
    finally:
        conn.close()


class PlaceholderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.port = free_port()
        self.ph = placeholder.Placeholder(self.port, host="127.0.0.1")
        self.addCleanup(self.ph.release)

    def test_answers(self) -> None:
        self.assertTrue(self.ph.hold())
        status, body = request(self.port, "GET", "/v1/models")
        self.assertEqual((status, body), (200, {"object": "list", "data": []}))
        status, body = request(self.port, "POST", "/v1/chat/completions", b'{"model": "x", "messages": []}')
        self.assertEqual(status, 503)
        self.assertEqual(body["error"]["message"], "No model loaded in Strata.")
        status, _ = request(self.port, "GET", "/health")
        self.assertEqual(status, 503)

    def raw(self, data: bytes) -> bytes:
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
            sock.sendall(data)
            chunks = []
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    return b"".join(chunks)
                chunks.append(chunk)

    def test_bodies_it_cannot_read_close_the_connection(self) -> None:
        self.assertTrue(self.ph.hold())
        cases = [
            b"POST /v1/chat/completions HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n0\r\n\r\n",
            b"POST /v1/chat/completions HTTP/1.1\r\nHost: x\r\nContent-Length: abc\r\n\r\n",
            b"POST /v1/chat/completions HTTP/1.1\r\nHost: x\r\nContent-Length: 99999999\r\n\r\npartial",
        ]
        for request_bytes in cases:
            answer = self.raw(request_bytes)  # returns only because the server closes
            self.assertTrue(answer.startswith(b"HTTP/1.1 503"), answer[:80])
            self.assertIn(b"Connection: close", answer)
            self.assertEqual(answer.count(b"HTTP/1.1"), 1, answer)

    def test_keep_alive_for_normal_requests(self) -> None:
        self.assertTrue(self.ph.hold())
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            for _ in range(3):
                conn.request("POST", "/v1/completions", body=b'{"prompt": "x"}', headers={"Content-Type": "application/json"})
                response = conn.getresponse()
                response.read()
                self.assertEqual(response.status, 503)
                self.assertIsNone(response.getheader("Connection"))
        finally:
            conn.close()

    def test_release_frees_port_and_hold_again(self) -> None:
        self.assertTrue(self.ph.hold())
        self.assertFalse(placeholder.port_is_free(self.port, "127.0.0.1"))
        self.ph.release()
        self.assertFalse(self.ph.held)
        self.assertTrue(placeholder.port_is_free(self.port, "127.0.0.1"))
        self.assertTrue(self.ph.try_hold())

    def test_busy_port(self) -> None:
        other = socket.socket()
        other.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        other.bind(("127.0.0.1", self.port))
        other.listen()
        self.addCleanup(other.close)
        self.assertFalse(self.ph.hold(retries=2, delay_s=0.05))
        self.assertIn(str(self.port), self.ph.error)
        other.close()
        self.assertTrue(self.ph.try_hold())
        self.assertIsNone(self.ph.error)


if __name__ == "__main__":
    unittest.main()
