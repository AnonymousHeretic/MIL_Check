"""Vercel 진입점: 공개 시연용 미러.

Vercel Python 런타임 규칙(api/*.py의 최상위 `handler` = BaseHTTPRequestHandler 하위 클래스)을 따른다.
모든 경로는 vercel.json rewrite로 이 함수에 들어오며, 처리는 milcheck.web.handle()에 위임한다.
이 파일은 Vercel에서만 쓰이므로 시연 미러 제한(MILCHECK_DEPLOY=vercel)을 여기서 켠다.
"""
from __future__ import annotations

import os
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

os.environ.setdefault("MILCHECK_DEPLOY", "vercel")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from milcheck import web  # noqa: E402


class handler(BaseHTTPRequestHandler):  # noqa: N801 - Vercel이 요구하는 이름
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
