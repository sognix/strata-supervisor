"""The API on the web UI port: requests under /v1 go on to Strata's own port inside the container.

One host port is then enough for the web UI and the API (USER-GUIDE, "Ports"). A host port mapped to the API
port itself (PORT) keeps working as before; such a client talks to Strata directly and never comes through here.

The request goes on as the client sent it, Host, Origin and Authorization included, so Strata makes the same
checks as for a direct call (its API key, its Host check against DNS rebinding, its Origin rules). Only the
headers of this one connection (Connection, Keep-Alive, Transfer-Encoding, ...) are not passed on. Answers are
passed on piece by piece as Strata sends them, so streamed answers (SSE) arrive at once. When the client goes
away, the connection to Strata is closed, so Strata stops the request.

While no model serves, the placeholder on PORT answers (503, empty /v1/models). In the short moments when
nothing listens there (while a model starts), the answer is a 503 of the same shape.
"""

from __future__ import annotations

import http.client
import json
from http.server import BaseHTTPRequestHandler

CONNECT_TIMEOUT_S = 5
# The largest request body passed on. Requests with pictures can be big; Strata has its own limits beyond this.
MAX_BODY = 256 * 1024 * 1024
CHUNK = 64 * 1024
# Headers of one connection only (RFC 9110, section 7.6.1); they are never passed on.
HOP_BY_HOP = frozenset({"connection", "keep-alive", "proxy-connection", "proxy-authenticate", "proxy-authorization",
                        "te", "trailer", "transfer-encoding", "upgrade"})
NOT_ANSWERING = "Strata does not answer on its port right now (a model may be starting). Try again in a moment."


def is_api_path(path: str) -> bool:
    return path == "/v1" or path.startswith("/v1/")


def _drop(headers) -> set[str]:
    """Hop-by-hop headers plus every header the Connection header names."""
    names = set(HOP_BY_HOP)
    for value in headers.get_all("Connection") or []:
        names.update(part.strip().lower() for part in value.split(",") if part.strip())
    return names


def _read_body(handler: BaseHTTPRequestHandler) -> bytes | None:
    """The request body, also a chunked one. None (and an answer sent) when it is too big or malformed."""
    rfile = handler.rfile
    if "chunked" in (handler.headers.get("Transfer-Encoding") or "").lower():
        parts: list[bytes] = []
        size = 0
        while True:
            line = rfile.readline(1024)
            try:
                length = int(line.split(b";", 1)[0].strip(), 16)
            except ValueError:
                _answer(handler, 400, "The chunked request body is malformed.", close=True)
                return None
            if length == 0:
                while rfile.readline(1024) not in (b"\r\n", b"\n", b""):
                    pass  # trailer fields: dropped
                return b"".join(parts)
            size += length
            if size > MAX_BODY:
                _answer(handler, 413, "The request body is too big.", close=True)
                return None
            parts.append(rfile.read(length))
            rfile.readline(16)  # the CRLF after each chunk
    raw = (handler.headers.get("Content-Length") or "0").strip()
    if not raw.isdigit():
        _answer(handler, 400, "The request has no valid Content-Length.", close=True)
        return None
    if int(raw) > MAX_BODY:
        _answer(handler, 413, "The request body is too big.", close=True)
        return None
    return rfile.read(int(raw)) if int(raw) else b""


def _answer(handler: BaseHTTPRequestHandler, status: int, message: str, close: bool = False) -> None:
    """An error of our own, in the OpenAI error format the placeholder uses as well."""
    kind = "service_unavailable" if status == 503 else "invalid_request_error"
    body = json.dumps({"error": {"message": message, "type": kind}}).encode()
    if close:
        handler.close_connection = True
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    if handler.close_connection:
        handler.send_header("Connection", "close")
    handler.end_headers()
    if handler.command != "HEAD":
        handler.wfile.write(body)


def forward(handler: BaseHTTPRequestHandler, port: int, host: str = "127.0.0.1") -> None:
    """Pass this request on to Strata on `port` and its answer back to the client."""
    body = _read_body(handler)
    if body is None:
        return
    drop = _drop(handler.headers) | {"content-length", "host"}
    upstream = http.client.HTTPConnection(host, port, timeout=CONNECT_TIMEOUT_S)
    try:
        try:
            upstream.connect()
        except OSError:
            _answer(handler, 503, NOT_ANSWERING)
            return
        upstream.sock.settimeout(None)  # a long prompt or a big picture can take minutes before the first byte
        upstream.putrequest(handler.command, handler.path, skip_host=True, skip_accept_encoding=True)
        if handler.headers.get("Host") is not None:
            upstream.putheader("Host", handler.headers["Host"])
        for name, value in handler.headers.items():
            if name.lower() not in drop:
                upstream.putheader(name, value)
        if body or handler.command in ("POST", "PUT", "PATCH"):
            upstream.putheader("Content-Length", str(len(body)))
        upstream.endheaders(body or None)
        try:
            response = upstream.getresponse()
        except (OSError, http.client.HTTPException):
            _answer(handler, 503, NOT_ANSWERING)
            return
        _pass_back(handler, response)
    finally:
        upstream.close()


def _pass_back(handler: BaseHTTPRequestHandler, response: http.client.HTTPResponse) -> None:
    no_body = handler.command == "HEAD" or response.status in (204, 304) or 100 <= response.status < 200
    length = response.getheader("Content-Length")
    chunked = not no_body and length is None  # a stream (SSE) or an answer that ends when Strata closes
    drop = _drop(response.headers)
    handler.send_response(response.status, response.reason)
    for name, value in response.headers.items():
        if name.lower() not in drop:
            handler.send_header(name, value)
    if chunked:
        handler.send_header("Transfer-Encoding", "chunked")
    if handler.close_connection:
        handler.send_header("Connection", "close")
    handler.end_headers()
    if no_body:
        handler.wfile.flush()
        return
    try:
        while True:
            piece = response.read1(CHUNK)
            if not piece:
                break
            if chunked:
                handler.wfile.write(b"%x\r\n%s\r\n" % (len(piece), piece))
            else:
                handler.wfile.write(piece)
            handler.wfile.flush()
        if chunked:
            handler.wfile.write(b"0\r\n\r\n")
            handler.wfile.flush()
    except (OSError, http.client.HTTPException):
        # The client went away, or Strata ended early (IncompleteRead): the caller closes the connection to Strata,
        # which ends the request there; the client's connection is closed too, so it sees the answer is cut off.
        handler.close_connection = True
