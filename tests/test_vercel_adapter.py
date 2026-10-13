"""Vercel 시연 미러 어댑터 시험. HTTP 서버 없이 api/index.py의 handler를 직접 호출한다."""
import importlib.util
import io
import os
import re
import sqlite3
import sys
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest import mock
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from milcheck import understanding, web  # noqa: E402
from test_web_mvp import PDF_READY, FakeAgent, make_pdf, multipart  # noqa: E402

_before = os.environ.get("MILCHECK_DEPLOY")
_spec = importlib.util.spec_from_file_location("vercel_api_index", ROOT / "api" / "index.py")
api_index = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(api_index)
if _before is None:  # 어댑터 import가 켠 배포 플래그를 다른 시험에 남기지 않는다
    os.environ.pop("MILCHECK_DEPLOY", None)

SECRET = "합성원문표식-7f3a9c"  # 입력 원문에만 들어가는 고유 문자열


def call(method, path, body=b"", ctype="application/x-www-form-urlencoded"):
    """BaseHTTPRequestHandler를 소켓 없이 실행하고 (status, headers, body)를 돌려준다."""
    if isinstance(body, dict):
        body = urlencode(body, doseq=True).encode()
    h = api_index.handler.__new__(api_index.handler)
    h.rfile, h.wfile = io.BytesIO(body), io.BytesIO()
    h.path, h.command = path, method
    h.request_version, h.requestline = "HTTP/1.1", f"{method} {path} HTTP/1.1"
    h.client_address, h.server, h.close_connection = ("127.0.0.1", 0), None, True
    msg = Message()
    msg["Content-Length"] = str(len(body))
    msg["Content-Type"] = ctype
    h.headers = msg
    getattr(h, f"do_{method}")()
    raw = h.wfile.getvalue()
    head, _, payload = raw.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    headers = {}
    for line in lines[1:]:
        k, _, v = line.partition(": ")
        headers[k.lower()] = v
    return int(lines[0].split()[1]), headers, payload.decode("utf-8")


def upload(fields, files=()):
    body, ctype = multipart(fields, files)
    return call("POST", "/extract", body, ctype)


def full_manual_form(page):
    docs = re.search(r'name="docs" value="([^"]*)"', page).group(1)
    return {"docs": docs, "review_type": "goods_sole", "mode": "manual", "ack": "1",
            "m:item_name": "시험용 부품", "m:sole_source_basis": "compatibility",
            "estimated_price_krw_ex_vat": "9,000,000", "x:alternatives_exist": "no",
            "evidence": ["req:EV-QUOTE", "req:EV-COMPAT", "vendor_eligibility", "conflict_of_interest_check",
                         "objective_market_search", "no_substitute_analysis", "audit_notification_plan"]}


class DeployModeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"MILCHECK_DEPLOY": "vercel"})
        self.env.start()
        self.tmpdir = mock.patch.object(tempfile, "tempdir", self.tmp.name)
        self.tmpdir.start()
        # 외부 호출 경로가 쓰이면 즉시 실패
        self.net = mock.patch.object(understanding, "http_transport",
                                     side_effect=AssertionError("네트워크 호출 금지"))
        self.net.start()
        web.set_agent(FakeAgent())

    def tearDown(self):
        web.set_agent(None)
        self.net.stop()
        self.tmpdir.stop()
        self.env.stop()
        self.tmp.cleanup()

    def test_1_live_mode_rejected(self):
        _, _, body = call("GET", "/")
        self.assertNotIn('value="live"', body)
        self.assertEqual(web.allowed_modes(), ("manual",))
        status, _, body = upload([("review_type", "goods_sole"), ("mode", "live"), ("direct_text", "a")])
        self.assertEqual(status, 403)
        status, _, _ = call("POST", "/review", {"mode": "live", "review_type": "goods_sole", "docs": "x"})
        self.assertEqual(status, 403)

    def test_2_banner_on_every_html_page(self):
        pages = [call("GET", "/"), call("GET", "/runs"), call("GET", "/nope"),
                 upload([("review_type", "goods_sole"), ("mode", "manual"), ("direct_text", "구매요구서")]),
                 upload([("review_type", "goods_sole"), ("mode", "live")])]
        for status, headers, body in pages:
            self.assertTrue(headers["content-type"].startswith("text/html"), status)
            self.assertIn(web.BANNER, body)
            self.assertIn('<meta name="robots" content="noindex">', body)
        self.assertIn(web.INPUT_WARNING, pages[0][2])
        self.assertNotIn("UD-0", pages[0][2])  # 합성 묶음 선택 없음
        self.assertIn(web.VOLATILE_NOTICE, pages[1][2])
        self.assertIn(web.SAME_DB_LIMIT, pages[1][2])

    def test_3_noindex_header_on_every_response(self):
        for method, path in [("GET", "/"), ("GET", "/static/style.css"), ("GET", "/static/app.js"),
                             ("GET", "/healthz"), ("GET", "/robots.txt"), ("GET", "/favicon.ico"),
                             ("GET", "/runs"), ("GET", "/x"), ("POST", "/x")]:
            _, headers, _ = call(method, path)
            self.assertEqual(headers.get("x-robots-tag"), "noindex, nofollow", path)

    def test_4_db_in_temp_path(self):
        self.assertEqual(web.db_path().parent, Path(self.tmp.name))
        self.assertTrue(str(web.db_path()).startswith(tempfile.gettempdir()))

    def test_5_record_has_no_source_text(self):
        status, _, page = upload([("review_type", "goods_sole"), ("mode", "manual"),
                                  ("direct_text", f"구매요구서. 품명: 시험용 부품. {SECRET}. 추정가격 9,000,000원.")])
        self.assertEqual(status, 200)
        status, _, body = call("POST", "/review", full_manual_form(page))
        self.assertEqual(status, 200, body[:500])
        db = sqlite3.connect(web.db_path())
        rows = db.execute("SELECT * FROM runs").fetchall()
        db.close()
        self.assertEqual(len(rows), 1)
        self.assertNotIn(SECRET, repr(rows))
        self.assertIn(web.DEMO_REVIEWER, repr(rows))  # 시연 미러는 확인자 이름을 받지 않는다
        self.assertNotIn('name="reviewer"', page)

    @unittest.skipUnless(PDF_READY, "pypdf/reportlab 미설치: requirements-input.txt 필요")
    def test_6_pdf_manual_flow_to_report(self):
        pdf = make_pdf([["구매요구서", f"품명: 시험용 부품 {SECRET}"], ["추정가격 9,000,000원(부가세 별도)"]])
        status, _, page = upload([("review_type", "goods_sole"), ("mode", "manual"), ("doctype_0", "request")],
                                 [("구매요구서.pdf", pdf), ("견적서.txt", "견적서. 합계 9,900,000원".encode())])
        self.assertEqual(status, 200, page[:500])
        self.assertIn("수동 입력 모드", page)
        self.assertIn("입력 필요", page)
        status, _, report = call("POST", "/review", full_manual_form(page))
        self.assertEqual(status, 200, report[:500])
        self.assertIn("요건 충족", report)
        self.assertIn("수의계약 사전검토 결과서", report)
        self.assertIn("LLM 미사용(수동 입력)", report)
        self.assertIn("시연 미러에서 생성된 문서", report)
        report_no = re.search(r"MC-\d{8}-\d{4}", report).group(0)
        status, _, detail = call("GET", f"/runs/{report_no}")
        self.assertEqual(status, 200)
        self.assertIn("일치", detail)
        db = sqlite3.connect(web.db_path())
        self.assertNotIn(SECRET, repr(db.execute("SELECT * FROM runs").fetchall()))
        db.close()


class LocalModeTest(unittest.TestCase):
    def test_7_local_unchanged_and_loopback_only(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MILCHECK_DEPLOY", None)
            os.environ.pop("MILCHECK_WEB_DB", None)
            self.assertFalse(web.is_deploy())
            self.assertEqual(web.allowed_modes(), ("live", "manual"))
            self.assertEqual(web.db_path(), ROOT / "runs" / "web_runs.sqlite")
            resp = web.handle("GET", "/")
            body = resp.body.decode()
            self.assertNotIn(web.BANNER, body)
            self.assertIn('value="live"', body)
            self.assertIn(("X-Robots-Tag", "noindex, nofollow"), resp.headers)
        import web_app
        self.assertEqual(web_app.HOST, "127.0.0.1")
        server = web_app.build_server(0)
        try:
            self.assertEqual(server.server_address[0], "127.0.0.1")
        finally:
            server.server_close()


if __name__ == "__main__":
    unittest.main()
