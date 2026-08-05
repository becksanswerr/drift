"""The data plane: an HTTP surface for payloads and results.

Control messages are datagrams because they are small, frequent and
best-effort. Task payloads are none of those things — a prompt or an image
does not fit in a datagram and losing one silently is not acceptable. So the
payload leaves over HTTP, and the result comes back over HTTP as a callback
rather than as a held-open response: a task may take minutes, and a socket
held open for minutes across a home network will be closed by something.

Deliberately stdlib-only. A framework whose whole point is running on the
machines you already own should not drag a web stack in with it.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BODY = 32 * 1024 * 1024  # 32 MB; a refusal is better than an OOM


class _Handler(BaseHTTPRequestHandler):
    node = None  # injected by make_server
    protocol_version = "HTTP/1.1"
    server_version = "drift"
    sys_version = ""

    # ---------------- plumbing ----------------
    def log_message(self, fmt, *args):  # noqa: A003 - stdlib signature
        # BaseHTTPRequestHandler writes to stderr, which would scribble over
        # the TUI. Route it through the node's log at debug level instead.
        if self.node is not None:
            self.node.log(fmt % args, level="debug", source="http")

    def _reply(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorised(self) -> bool:
        return self.node.mesh.security.check_token(self.headers.get("X-Drift-Auth"))

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return None
        if length <= 0 or length > MAX_BODY:
            return None
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None

    # ---------------- routes ----------------
    def do_GET(self):  # noqa: N802 - stdlib signature
        if not self._authorised():
            return self._reply(401, {"error": "unauthorised"})
        if self.path == "/drift/info":
            return self._reply(200, self.node.info())
        if self.path == "/drift/state":
            return self._reply(200, self.node.state())
        if self.path == "/drift/health":
            return self._reply(200, {"ok": True, "node": self.node.name})
        return self._reply(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802 - stdlib signature
        if not self._authorised():
            return self._reply(401, {"error": "unauthorised"})
        body = self._read_json()
        if body is None:
            return self._reply(400, {"error": "invalid json body"})

        if self.path == "/drift/execute":
            task_id = body.get("task_id")
            cap = body.get("cap")
            if not task_id or not cap:
                return self._reply(400, {"error": "task_id and cap are required"})
            # Accept immediately and run in the pool. Holding the request open
            # for the duration of the task is what the result callback exists
            # to avoid.
            self.node._accept_remote_task(task_id, cap, body.get("payload", {}), body.get("reply_to", {}))
            return self._reply(202, {"accepted": True})

        if self.path == "/drift/result":
            self.node._deliver_result(body.get("task_id"), body.get("result"))
            return self._reply(200, {"ok": True})

        if self.path == "/drift/call":
            # Lets a CLI (or any local script) borrow this node's membership in
            # the Hive instead of joining it. Two processes on one machine
            # cannot share the discovery port cleanly, so the right move is not
            # to try: the running node holds the socket, everyone else asks it.
            cap = body.get("cap")
            if not cap:
                return self._reply(400, {"error": "cap is required"})
            result = self.node.call(
                cap,
                body.get("payload", {}),
                timeout=float(body.get("timeout", 60)),
                prefer_remote=bool(body.get("prefer_remote", False)),
                hint=body.get("hint"),
            )
            return self._reply(200, {"result": result})

        return self._reply(404, {"error": "not found"})


class _Server(ThreadingHTTPServer):
    # HTTPServer defaults this to True, which on Windows lets a second process
    # bind a port that is already serving. The clash then shows up as requests
    # landing on whichever server the OS feels like — far worse than a refusal
    # at startup.
    allow_reuse_address = False
    daemon_threads = True


def make_server(node, port: int, host: str = "0.0.0.0") -> ThreadingHTTPServer:
    handler = type("_BoundHandler", (_Handler,), {"node": node})
    server = _Server((host, port), handler)
    server.daemon_threads = True
    return server


def serve_in_thread(node, port: int, host: str = "0.0.0.0"):
    server = make_server(node, port, host)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def post_json(url: str, payload: dict, headers: dict | None = None, timeout: float = 10.0) -> dict:
    """POST JSON and return the decoded response. Raises on transport failure."""
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def get_json(url: str, headers: dict | None = None, timeout: float = 5.0) -> dict:
    request = urllib.request.Request(url, method="GET")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))
