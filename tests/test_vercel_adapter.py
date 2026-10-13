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

from milcheck import understanding, web  # noqa: E402

_before = os.environ.get("MILCHECK_DEPLOY")
_spec = importlib.util.spec_from_file_location("vercel_api_index", ROOT / "api" / "index.py")
api_index = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(api_index)
if _before is None:  # 어댑터 import가 켠 배포 플래그를 다른 시험에 남기지 않는다
    os.environ.pop("MILCHECK_DEPLOY", None)

SECRET = "합성원문표식-7f3a9c"  # 붙여넣기 원문에만 들어가는 고유 문자열


def call(method, path, form=None):
    """BaseHTTPRequestHandler를 소켓 없이 실행하고 (status, headers, body)를 돌려준다."""
    body = urlencode(form or {}, doseq=True).encode()
    h = api_index.handler.__new__(api_index.handler)
    h.rfile, h.wfile = io.BytesIO(body), io.BytesIO()
    h.path, h.command = path, method
    h.request_version, h.requestline = "HTTP/1.1", f"{method} {path} HTTP/1.1"
    h.client_address, h.server, h.close_connection = ("127.0.0.1", 0), None, True
    msg = Message()
    msg["Content-Length"] = str(len(body))
    h.headers = msg
    getattr(h, f"do_{method}")()
    raw = h.wfile.getvalue()
    head, _, payload = raw.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    status = int(lines[0].split()[1])
    headers = {}
    for line in lines[1:]:
        k, _, v = line.partition(": ")
        headers[k.lower()] = v
    return status, headers, payload.decode("utf-8")


def confirm_form(page_html, base):
    """확인 화면에서 기본 선택된 후보를 그대로 담고, 충돌 필드는 첫 후보를 고른다."""
    form = dict(base, contract_category="goods", proposed_type="sole_source")
    for name, value in re.findall(r'name="f:(\w+)" value="([^"]*)" checked', page_html):
        form[f"f:{name}"] = value
    for name in set(re.findall(r'name="f:(\w+)" value="F', page_html)):
        if not form.get(f"f:{name}"):
            form[f"f:{name}"] = re.findall(rf'name="f:{name}" value="(F\w+)"', page_html)[0]
    return form


class FakeAgent:
    def __init__(self):
        from milcheck.rules import RuleEngine
        self.engine = RuleEngine.from_file(ROOT / "data/rules.json")

    def review(self, case):
        ev = self.engine.evaluate(case).as_dict()
        return {**ev, "item_name": case.get("item_name"), "rules_current_as_of": "t",
                "advisory": {"signals": []}, "retrieved_sources": [], "disclaimer": "d"}


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
        status, _, body = call("GET", "/")
        self.assertNotIn('value="live"', body)
        for path in ("/understand", "/review"):
            status, _, body = call("POST", path, {"mode": "live", "source": "bundle", "bundle_id": "UD-01"})
            self.assertEqual(status, 403)
            self.assertIn("live", body)
        self.assertEqual(web.allowed_modes(), ("gold-echo", "manual"))

    def test_2_banner_on_every_html_page(self):
        pages = [call("GET", "/"), call("GET", "/runs"), call("GET", "/nope"),
                 call("POST", "/understand", {"mode": "gold-echo", "source": "bundle", "bundle_id": "UD-01"}),
                 call("POST", "/understand", {"mode": "live"})]
        for status, headers, body in pages:
            self.assertTrue(headers["content-type"].startswith("text/html"))
            self.assertIn(web.BANNER, body)
            self.assertIn('<meta name="robots" content="noindex">', body)
        self.assertIn(web.INPUT_WARNING, pages[0][2])
        self.assertIn(web.VOLATILE_NOTICE, pages[1][2])
        self.assertIn(web.SAME_DB_LIMIT, pages[1][2])

    def test_3_noindex_header_on_every_response(self):
        for method, path in [("GET", "/"), ("GET", "/static/style.css"), ("GET", "/healthz"),
                             ("GET", "/robots.txt"), ("GET", "/runs"), ("GET", "/x"), ("POST", "/x")]:
            _, headers, _ = call(method, path)
            self.assertEqual(headers.get("x-robots-tag"), "noindex, nofollow", path)

    def test_4_db_in_temp_path(self):
        self.assertEqual(web.db_path().parent, Path(self.tmp.name))
        self.assertTrue(str(web.db_path()).startswith(tempfile.gettempdir()))

    def test_5_record_has_no_source_text(self):
        base = {"mode": "manual", "source": "paste",
                "doc_R1": f"구매요구서. 품명: 시험용 부품. {SECRET}. 추정가격 9,000,000원(부가세 별도)."}
        status, _, page_html = call("POST", "/understand", base)
        self.assertEqual(status, 200)
        form = dict(base, contract_category="goods", proposed_type="small_amount",
                    estimated_price_krw_ex_vat="9,000,000", **{"f:item_name": "__manual__",
                                                                "m:item_name": "시험용 부품"})
        status, _, body = call("POST", "/review", form)
        self.assertEqual(status, 200, body[:500])
        db = sqlite3.connect(web.db_path())
        rows = db.execute("SELECT * FROM runs").fetchall()
        db.close()
        self.assertEqual(len(rows), 1)
        self.assertNotIn(SECRET, repr(rows))
        self.assertIn(web.DEMO_REVIEWER, repr(rows))  # 시연 미러는 확인자 이름을 받지 않는다
        # gold-echo 묶음 원문도 기록에 남지 않는다
        bundle = web.load_bundles()["UD-01"]
        base = {"mode": "gold-echo", "source": "bundle", "bundle_id": "UD-01"}
        _, _, page_html = call("POST", "/understand", base)
        call("POST", "/review", confirm_form(page_html, base))
        db = sqlite3.connect(web.db_path())
        dump = repr(db.execute("SELECT * FROM runs").fetchall())
        db.close()
        for d in bundle["documents"]:
            self.assertNotIn(d["text"], dump)

    def test_6_gold_echo_flow_to_report_with_badge(self):
        web.set_agent(None)  # 실제 MilCheckAgent로 결과서까지
        base = {"mode": "gold-echo", "source": "bundle", "bundle_id": "UD-08"}  # 업체명 충돌 묶음
        status, _, page_html = call("POST", "/understand", base)
        self.assertEqual(status, 200)
        self.assertIn(web.GOLD_BADGE, page_html)
        self.assertIn("충돌 — 선택 필요", page_html)
        # 충돌을 고르지 않으면 판정으로 넘어가지 않는다
        no_choice = confirm_form(page_html, base)
        no_choice["f:supplier_name"] = ""
        status, _, body = call("POST", "/review", no_choice)
        self.assertEqual(status, 400)
        status, _, report = call("POST", "/review", confirm_form(page_html, base))
        self.assertEqual(status, 200, report[:500])
        self.assertIn("사전검토 결과서", report)
        self.assertIn(web.GOLD_BADGE, report)
        self.assertIn(f"&gt; {web.GOLD_BADGE}", report)  # Markdown 결과서 본문에도 표시
        self.assertRegex(report, r"MC-\d{8}-\d{4}")
        report_no = re.search(r"MC-\d{8}-\d{4}", report).group(0)
        status, _, detail = call("GET", f"/runs/{report_no}")
        self.assertEqual(status, 200)
        self.assertIn("일치", detail)

    def test_gold_echo_matches_dev_script(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        from eval_understanding import gold_echo_transport as script_transport
        b = web.load_bundles()["UD-02"]
        docs = [understanding.Document(d["document_id"], d["revision"], d["text"], doc_type=d["doc_type"])
                for d in b["documents"]]
        cfg = understanding.EndpointConfig(model="m")
        a = understanding.DocumentUnderstanding(cfg, transport=web.gold_echo_transport(b)).understand(docs)
        s = understanding.DocumentUnderstanding(cfg, transport=script_transport(b)).understand(docs)
        self.assertEqual(a.to_dict(), s.to_dict())


class LocalModeTest(unittest.TestCase):
    def test_7_local_unchanged_and_loopback_only(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MILCHECK_DEPLOY", None)
            os.environ.pop("MILCHECK_WEB_DB", None)
            self.assertFalse(web.is_deploy())
            self.assertIn("live", web.allowed_modes())
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
