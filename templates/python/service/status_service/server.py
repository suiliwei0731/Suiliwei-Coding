"""健康状态查询 HTTP 服务器。

仅使用标准库 ``http.server``，暴露两个端点：

- ``GET /healthz``：200 "OK" 表示存活探针
- ``GET /status``：200 JSON，包含版本、进程启动时长与服务标识
"""

from __future__ import annotations

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_STARTED = time.monotonic()
_SERVICE_NAME = "status-service"


def build_status() -> dict[str, object]:
    return {
        "service": _SERVICE_NAME,
        "version": __import__("status_service").__version__,
        "uptime_seconds": round(time.monotonic() - _STARTED, 3),
        "ok": True,
    }


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - http.server 约定
        if self.path == "/healthz":
            self._send(200, b"OK", content_type="text/plain")
            return
        if self.path == "/status":
            body = json.dumps(build_status()).encode("utf-8")
            self._send(200, body)
            return
        self._send(404, b"Not Found", content_type="text/plain")

    def _send(self, code: int, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    port = int(argv[0]) if argv else 8080
    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    print(f"{_SERVICE_NAME} listening on 127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
    return 0
