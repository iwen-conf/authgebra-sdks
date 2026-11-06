"""Local fake Authgebra server (stdlib only) used by every test."""

from __future__ import annotations

import socket
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterator


def user_json() -> dict[str, object]:
    """A complete UserDto for tests that exercise the named admin response decoder."""
    return {
        "id": "550e8400-e29b-41d4-a716-446655440000", "username": "example-user",
        "display_name": "Example", "status": "active", "auth_version": 1,
        "version": 1, "etag": 'W/"user:v1"', "created_at_rfc3339": "2026-11-06T00:00:00Z",
        "mfa_enabled": False, "roles_count": 0, "sessions_count": 0,
    }

import pytest


@dataclass
class Recorded:
    method: str
    path: str
    headers: dict[str, str]
    body: bytes


@dataclass
class Script:
    """One scripted answer: an HTTP response or a network fault."""

    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    fault: str | None = None  # "timeout" | "reset"


class FakeServer:
    def __init__(self) -> None:
        self.requests: list[Recorded] = []
        self.script: list[Script] = [Script()]
        self.release = threading.Event()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                return

            def _handle(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                headers = {k.lower(): v for k, v in self.headers.items()}
                outer.requests.append(Recorded(self.command, self.path, headers, body))
                index = min(len(outer.requests) - 1, len(outer.script) - 1)
                step = outer.script[index]
                if step.fault == "timeout":
                    outer.release.wait(3.0)
                    self.close_connection = True
                    return
                if step.fault == "reset":
                    self.close_connection = True
                    try:
                        self.connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    return
                self.send_response(step.status)
                sent = {k.lower() for k in step.headers}
                for name, value in step.headers.items():
                    self.send_header(name, value)
                if "content-type" not in sent:
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(step.body)))
                self.end_headers()
                self.wfile.write(step.body)

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _handle  # noqa: N815

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.release.set()
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server() -> Iterator[FakeServer]:
    fake = FakeServer()
    try:
        yield fake
    finally:
        fake.close()
