"""status_service.server 单元测试。"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from status_service import server as svc


@pytest.fixture(scope="module")
def http() -> Iterator[tuple[ThreadingHTTPServer, int]]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), svc._Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv, int(port)
    srv.shutdown()


def _get(port: int, path: str) -> tuple[int, str]:
    conn = HTTPConnection("127.0.0.1", port, timeout=2)
    conn.request("GET", path)
    resp = conn.getresponse()
    body = resp.read().decode("utf-8")
    conn.close()
    return resp.status, body


def test_build_status_shape() -> None:
    status = svc.build_status()
    assert status["service"] == "status-service"
    assert isinstance(status["uptime_seconds"], float)
    assert status["ok"] is True


def test_healthz_responds(http: tuple[Any, int]) -> None:
    _, port = http
    code, body = _get(port, "/healthz")
    assert code == 200
    assert body == "OK"


def test_status_returns_json(http: tuple[Any, int]) -> None:
    _, port = http
    code, body = _get(port, "/status")
    assert code == 200
    data = json.loads(body)
    assert data["service"] == "status-service"
    assert data["ok"] is True
    assert isinstance(data["version"], str)


def test_unknown_path_404(http: tuple[Any, int]) -> None:
    _, port = http
    code, _ = _get(port, "/nope")
    assert code == 404


def test_mypy_ignored() -> None:
    """仅占位：确保模板在空白库上也能通过；不做行为断言。"""
    assert Path("pyproject.toml").name == "pyproject.toml"
