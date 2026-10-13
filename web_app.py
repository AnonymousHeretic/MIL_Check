"""MIL-Check 웹 MVP 로컬 실행(폐쇄망).

실행:
    python web_app.py            # http://127.0.0.1:8766
    python web_app.py --port 9000

127.0.0.1에만 바인딩한다(외부 접속 불가). 실제 처리는 milcheck.web.handle()이 한다.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from milcheck import web

HOST = "127.0.0.1"
DEFAULT_PORT = 8766


class Handler(BaseHTTPRequestHandler):
    def _serve(self, method: str) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length > web.MAX_BODY_BYTES:
            resp = web.error_page(413, "요청이 너무 큽니다")
        else:
            body = self.rfile.read(length) if length else b""
            resp = web.handle(method, self.path, body, self.headers.get("Content-Type") or "")
        self.send_response(resp.status)
        for k, v in resp.headers:
            self.send_header(k, v)
        self.end_headers()
        if method != "HEAD":
            self.wfile.write(resp.body)

    def do_GET(self):  # noqa: N802
        self._serve("GET")

    def do_HEAD(self):  # noqa: N802
        self._serve("HEAD")

    def do_POST(self):  # noqa: N802
        self._serve("POST")

    def log_message(self, fmt, *args):  # 요청 본문·원문을 로그에 남기지 않는다
        pass


def build_server(port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((HOST, port), Handler)


def main() -> None:
    ap = argparse.ArgumentParser(description="MIL-Check 웹 MVP (127.0.0.1 전용)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    port = ap.parse_args().port
    server = build_server(port)
    print(f"MIL-Check 웹 MVP: http://{HOST}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
