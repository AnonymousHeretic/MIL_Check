"""웹 MVP(로컬) 흐름 시험: milcheck.web.handle을 HTTP 없이 호출한다.

live 경로는 실제 서버 대신 understanding.http_transport를 가짜 로컬 LLM 응답으로 바꿔 시험한다.
"""
import importlib.util
import io
import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from milcheck import understanding, web  # noqa: E402
from milcheck.understanding import SCHEMA_VERSION  # noqa: E402

PDF_READY = all(importlib.util.find_spec(m) for m in ("pypdf", "reportlab"))

REQUEST = "구매요구서. 품명: NVX-8 거치대 호환 고정 브래킷. 추정가격 12,000,000원(부가세 별도). 공급업체: 한빛정밀."
QUOTE = "견적서. 상호: 한빛정밀산업. 합계 13,200,000원(VAT 포함)."
FACTS = {  # 가짜 로컬 LLM이 문서별로 돌려줄 사실(필드, 값, 원문 인용)
    "request": [("item_name", "NVX-8 거치대 호환 고정 브래킷", "NVX-8 거치대 호환 고정 브래킷"),
                ("estimated_price_krw", 12000000, "추정가격 12,000,000원"),
                ("tax_status", "vat_excluded", "부가세 별도"), ("supplier_name", "한빛정밀", "한빛정밀")],
    "quote": [("supplier_name", "한빛정밀산업", "한빛정밀산업"), ("total_amount_krw", 13200000, "합계 13,200,000원"),
              ("tax_status", "vat_included", "VAT 포함")],
}


def fake_llm(url, body, headers, timeout):
    user = body["messages"][1]["content"]
    text = REQUEST if '"document_id": "T1"' in user or REQUEST in user else QUOTE
    kind = "request" if text == REQUEST else "quote"
    did = re.search(r'"document_id": "(\w+)"', user).group(1)
    facts = []
    for f, v, q in FACTS[kind]:
        st = text.index(q)
        facts.append({"field": f, "value": v, "source_refs": [
            {"document_id": did, "revision": 1, "text_start": st, "text_end": st + len(q), "quote": q}]})
    payload = {"schema_version": SCHEMA_VERSION, "facts": facts, "evidence_claims": [], "questions": []}
    return json.dumps({"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]}).encode()


def multipart(fields, files=()):
    b = "MILCHECKTEST"
    out = b""
    for k, v in fields:
        out += f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    for name, data in files:
        out += (f'--{b}\r\nContent-Disposition: form-data; name="files"; filename="{name}"\r\n'
                f'Content-Type: application/pdf\r\n\r\n').encode() + data + b"\r\n"
    return out + f"--{b}--\r\n".encode(), f"multipart/form-data; boundary={b}"


def make_pdf(pages):
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfgen import canvas
    pdfmetrics.registerFont(UnicodeCIDFont("HYSMyeongJo-Medium"))
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for lines in pages:
        c.setFont("HYSMyeongJo-Medium", 12)
        for i, line in enumerate(lines):
            c.drawString(60, 780 - 20 * i, line)
        c.showPage()
    c.save()
    return buf.getvalue()


class FakeAgent:
    def __init__(self):
        from milcheck.rules import RuleEngine
        self.engine = RuleEngine.from_file(ROOT / "data/rules.json")
        self.calls = []

    def review(self, case):
        self.calls.append(dict(case))
        ev = self.engine.evaluate(case).as_dict()
        return {**ev, "item_name": case.get("item_name"), "rules_current_as_of": "2026-07-23",
                "advisory": {"signals": []}, "disclaimer": "d",
                "retrieved_sources": [{"title": "시행령", "status": "현행", "excerpt": "조문", "source_date": "2026-06-03"}]}


def extract(fields, files=()):
    body, ctype = multipart(fields, files)
    r = web.handle("POST", "/extract", body, ctype)
    return r.status, r.body.decode()


def review(form):
    r = web.handle("POST", "/review", urlencode(form, doseq=True).encode(), "application/x-www-form-urlencoded")
    return r.status, r.body.decode()


def hidden(page, name):
    return re.search(rf'name="{name}" value="([^"]*)"', page).group(1)


class WebMvpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"MILCHECK_WEB_DB": str(Path(self.tmp.name) / "w.sqlite")})
        self.env.start()
        os.environ.pop("MILCHECK_DEPLOY", None)
        self.llm = mock.patch.object(understanding, "http_transport", side_effect=fake_llm)
        self.llm.start()
        self.agent = FakeAgent()
        web.set_agent(self.agent)

    def tearDown(self):
        web.set_agent(None)
        self.llm.stop()
        self.env.stop()
        self.tmp.cleanup()

    def live_page(self):
        status, page = extract([("review_type", "goods_sole"), ("mode", "live"), ("direct_text", REQUEST)],
                               [])
        self.assertEqual(status, 200, page[:300])
        return page

    def test_input_page_has_no_synthetic_bundles(self):
        body = web.handle("GET", "/").body.decode()
        self.assertNotIn("UD-0", body)
        self.assertNotIn("합성 예시", body)
        self.assertIn("문서 업로드", body)
        self.assertIn("내용 직접 입력", body)
        self.assertIn(web.INPUT_WARNING, body)
        self.assertIn('value="manual"', body)  # 로컬: 수동 입력 선택 가능

    def test_live_candidates_show_citations(self):
        page = self.live_page()
        self.assertIn("AI 후보", page)
        self.assertIn("“추정가격 12,000,000원”", page)
        self.assertIn("구매요구서", page)

    def test_conflict_requires_choice_and_signed_state(self):
        body, ctype = multipart([("review_type", "goods_sole"), ("mode", "live"), ("doctype_0", "quote")],
                                [("견적서.txt", QUOTE.encode())])
        # .txt 업로드 + 직접 입력(구매요구서 역할)
        body2, _ = multipart([("review_type", "goods_sole"), ("mode", "live"), ("doctype_0", "quote"),
                              ("direct_text", REQUEST)], [("견적서.txt", QUOTE.encode())])
        page = web.handle("POST", "/extract", body2, ctype).body.decode()
        self.assertIn("문서마다 값이 다릅니다", page)
        form = {"docs": hidden(page, "docs"), "result": hidden(page, "result"), "review_type": "goods_sole",
                "mode": "live", "reviewer": "r", "ack": "1", "estimated_price_krw_ex_vat": "12000000"}
        for name, fid in re.findall(r'name="f:(\w+)" value="(F\w+)" data-value', page):
            form[f"f:{name}"] = fid
        status, out = review(form)
        self.assertEqual(status, 400)  # 충돌 필드 미선택
        self.assertIn("충돌 필드", out)
        for name in ("supplier_name", "tax_status"):
            form[f"f:{name}"] = re.findall(rf'name="f:{name}" value="(F\w+)"', page)[0]
        status, out = review(form)
        self.assertEqual(status, 200, out[:400])
        # 서명된 추출 결과를 바꾸면 거부
        bad = dict(form, result=form["result"][:-4] + "0000")
        self.assertEqual(review(bad)[0], 400)

    def test_manual_price_always_required_and_no_auto_conversion(self):
        page = self.live_page()
        form = {"docs": hidden(page, "docs"), "result": hidden(page, "result"), "review_type": "goods_sole",
                "mode": "live", "reviewer": "r", "ack": "1"}
        status, out = review(form)
        self.assertEqual(status, 400)
        self.assertIn("부가세 제외", out)
        form["estimated_price_krw_ex_vat"] = "10,000,000"
        self.assertEqual(review(form)[0], 200)
        self.assertEqual(self.agent.calls[-1]["estimated_price_krw_ex_vat"], 10_000_000)

    def test_ack_required(self):
        page = self.live_page()
        form = {"docs": hidden(page, "docs"), "result": hidden(page, "result"), "review_type": "goods_sole",
                "mode": "live", "reviewer": "r", "estimated_price_krw_ex_vat": "1"}
        status, out = review(form)
        self.assertEqual(status, 400)
        self.assertIn("대조", out)

    def test_only_confirmed_values_reach_rules(self):
        status, page = extract([("review_type", "goods_sole"), ("mode", "live"), ("mode", "manual"),
                                ("direct_text", REQUEST)])
        self.assertEqual(status, 200)
        self.assertIn("수동 입력 모드", page)
        self.assertNotIn('name="result"', page)
        status, _ = review({"docs": hidden(page, "docs"), "review_type": "goods_sole", "mode": "manual",
                            "reviewer": "r", "ack": "1", "estimated_price_krw_ex_vat": "5",
                            "evidence": ["bogus", "req:EV-QUOTE"]})
        self.assertEqual(status, 200)
        case = self.agent.calls[-1]
        self.assertNotIn("item_name", case)
        self.assertEqual(case["evidence"], ["price_reasonableness"])

    def test_model_failure_offers_manual_switch(self):
        self.llm.stop()
        with mock.patch.object(understanding, "http_transport",
                               side_effect=understanding.UnderstandingError("LLM 서버 연결 실패: timed out")):
            status, page = extract([("review_type", "goods_sole"), ("mode", "live"), ("direct_text", REQUEST)])
        self.llm.start()
        self.assertEqual(status, 200)
        self.assertIn("자동 추출 실패", page)
        self.assertIn("시간 초과", page)
        self.assertIn("수동 입력으로 전환", page)
        status, page2 = extract([("review_type", "goods_sole"), ("mode", "manual"), ("docs", hidden(page, "docs"))])
        self.assertEqual(status, 200)
        self.assertIn("수동 입력 모드", page2)

    def test_html_is_escaped(self):
        status, body = extract([("review_type", "goods_sole"), ("mode", "manual"),
                                ("direct_text", "<script>alert(1)</script>")])
        self.assertEqual(status, 200)
        self.assertNotIn("<script>alert(1)</script>", body)

    def test_bad_inputs(self):
        self.assertEqual(extract([("review_type", "goods_sole"), ("mode", "manual")])[0], 400)  # 문서 없음
        self.assertEqual(extract([("review_type", "x"), ("direct_text", "a")])[0], 400)
        self.assertEqual(extract([("review_type", "goods_sole"), ("mode", "manual"), ("doctype_0", "quote")],
                                 [("a.hwp", b"xx")])[0], 400)
        self.assertEqual(review({"docs": "!!", "review_type": "goods_sole", "mode": "manual"})[0], 400)

    def test_report_and_records(self):
        status, page = extract([("review_type", "goods_sole"), ("mode", "manual"), ("direct_text", REQUEST)])
        form = {"docs": hidden(page, "docs"), "review_type": "goods_sole", "mode": "manual", "reviewer": "r",
                "ack": "1", "m:item_name": "충전기", "m:sole_source_basis": "compatibility",
                "estimated_price_krw_ex_vat": "12000000"}
        status, body = review(form)
        self.assertEqual(status, 200)
        for text in ("규칙별 판정과 근거", "수의계약 사전검토 결과서", "담당자 확정 입력값", "확정본 발행",
                     web.FINAL_NOTICE, "원문 대조 후 직접 입력"):
            self.assertIn(text, body)
        self.assertIn("보완 요청서 생성", body)  # 증빙 미확인 → 보완 필요
        report_no = re.search(r"MC-\d{8}-\d{4}", body).group(0)
        runs = web.handle("GET", "/runs").body.decode()
        self.assertIn(report_no, runs)
        self.assertIn(web.SAME_DB_LIMIT, runs)
        self.assertNotIn(web.VOLATILE_NOTICE, runs)
        db = sqlite3.connect(os.environ["MILCHECK_WEB_DB"])
        raw = repr(db.execute("SELECT * FROM runs").fetchall())
        self.assertNotIn(REQUEST, raw)
        db.execute("UPDATE runs SET record_json=replace(record_json,'충전기','변조')")
        db.commit()
        db.close()
        self.assertIn("불일치", web.handle("GET", f"/runs/{report_no}").body.decode())

    def test_grounding_missing_blocks_final(self):
        class NoSource(FakeAgent):
            def review(self, case):
                return {**super().review(case), "retrieved_sources": []}
        web.set_agent(NoSource())
        status, page = extract([("review_type", "goods_sole"), ("mode", "manual"), ("direct_text", REQUEST)])
        status, body = review({"docs": hidden(page, "docs"), "review_type": "goods_sole", "mode": "manual",
                               "reviewer": "r", "ack": "1", "estimated_price_krw_ex_vat": "1"})
        self.assertEqual(status, 200)
        self.assertIn("확정본 발행이 차단됩니다", body)
        self.assertIn('data-final="report" disabled', body)

    @unittest.skipUnless(PDF_READY, "pypdf/reportlab 미설치: requirements-input.txt 필요")
    def test_pdf_upload_pages_in_citations(self):
        pdf = make_pdf([["구매요구서"], ["추정가격 12,000,000원(부가세 별도)"]])
        status, page = extract([("review_type", "goods_sole"), ("mode", "manual"), ("doctype_0", "request")],
                               [("구매요구서.pdf", pdf)])
        self.assertEqual(status, 200, page[:300])
        self.assertIn("2쪽", page)
        docs = web.decode_docs(hidden(page, "docs"))
        self.assertEqual([p[0] for p in docs[0].pages], [1, 2])
        ref = understanding.SourceRef("D1", 1, docs[0].pages[1][1], docs[0].pages[1][1] + 4, "추정가격")
        self.assertEqual(web.ref_place(ref, {"D1": docs[0]}), "구매요구서 p.2")

    def test_manual_int_parse(self):
        with self.assertRaises(web.ConfirmationError):
            web._parse_int("12만", "x")
        self.assertEqual(web._parse_int("1,200원", "x"), 1200)


class ReviewFindingsTest(unittest.TestCase):
    """코드 검사에서 지적된 5가지 문제와 LLM 시간 예산의 회귀 시험."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"MILCHECK_WEB_DB": str(Path(self.tmp.name) / "w.sqlite")})
        self.env.start()
        os.environ.pop("MILCHECK_DEPLOY", None)
        web.set_agent(FakeAgent())

    def tearDown(self):
        web.set_agent(None)
        self.env.stop()
        self.tmp.cleanup()

    def test_conflict_on_field_outside_default_rows_can_be_resolved(self):
        req, quo = "구매요구서. 품명: 브래킷. 수량 20개.", "견적서. 수량 30개."

        def llm(url, body, headers, timeout):
            user = body["messages"][1]["content"]
            did = re.search(r'"document_id": "(\w+)"', user).group(1)
            text, q, v = (req, "20개", 20) if did == "T1" else (quo, "30개", 30)
            st = text.index(q)
            payload = {"schema_version": SCHEMA_VERSION, "evidence_claims": [], "questions": [], "facts": [
                {"field": "quantity", "value": v, "source_refs": [
                    {"document_id": did, "revision": 1, "text_start": st, "text_end": st + len(q), "quote": q}]}]}
            return json.dumps({"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]}).encode()
        with mock.patch.object(understanding, "http_transport", side_effect=llm):
            body, ctype = multipart([("review_type", "goods_sole"), ("mode", "live"), ("doctype_0", "quote"),
                                     ("direct_text", req)], [("견적서.txt", quo.encode())])
            page = web.handle("POST", "/extract", body, ctype).body.decode()
        self.assertIn("수량", page)
        picks = re.findall(r'name="f:quantity" value="(F\w+)"', page)
        self.assertEqual(len(picks), 2)  # 충돌 후보 2개 모두 선택란이 있다
        form = {"docs": hidden(page, "docs"), "result": hidden(page, "result"), "review_type": "goods_sole",
                "mode": "live", "reviewer": "r", "ack": "1", "estimated_price_krw_ex_vat": "1000"}
        self.assertEqual(review(form)[0], 400)  # 선택 전에는 판정 불가
        self.assertEqual(review(dict(form, **{"f:quantity": picks[1]}))[0], 200)
        self.assertEqual(review(dict(form, **{"m:quantity": "25"}))[0], 200)  # 직접 입력으로도 해소

    def test_field_rows_cover_every_conflicting_field(self):
        res = understanding.UnderstandingResult("v", "v", "ok", [
            understanding.CandidateFact(f"F{i}", "unknown_field", i, None, [], "v", "conflicting")
            for i in range(2)], [], [], [], {})
        self.assertIn("unknown_field", [r[0] for r in web.field_rows(res)])

    def test_bad_llm_env_does_not_raise(self):
        for env in ({"MILCHECK_LLM_TIMEOUT": "oops"}, {"MILCHECK_LLM_EXTRA_BODY": "{bad"},
                    {"MILCHECK_LLM_MAX_TOKENS": "x"}):
            with mock.patch.dict(os.environ, env):
                self.assertEqual(web.handle("GET", "/").status, 200)
                status, page = extract([("review_type", "goods_sole"), ("mode", "live"), ("direct_text", REQUEST)])
                self.assertEqual(status, 200)
                self.assertIn("자동 추출 실패", page)
                self.assertIn("LLM 설정 오류", page)
                self.assertIn("수동 입력으로 전환", page)

    def test_apply_price_button_checks_vat_in_script(self):
        js = (ROOT / "milcheck/web_static/app.js").read_text(encoding="utf-8")
        gate = js[js.index("applyBtn.addEventListener"):]
        gate = gate[:gate.index("});")]
        self.assertIn('currentValue("tax_status") !== "vat_excluded"', gate)
        self.assertLess(gate.index("vat_excluded"), gate.index("price.value = v"))

    def test_report_numbers_unique_under_concurrency(self):
        import threading
        from milcheck.workflow import Confirmation, open_store, run_case
        res = web.understand([web.DocInfo("T1", "직접 입력", "request", REQUEST)], "manual")
        path = Path(self.tmp.name) / "c.sqlite"
        open_store(path).close()
        numbers, errors = [], []
        barrier = threading.Barrier(8)

        def worker():
            store = open_store(path)
            try:
                barrier.wait()
                rep = run_case(res, Confirmation("r", "goods", "sole_source", estimated_price_krw_ex_vat=1),
                               FakeAgent(), store)
                numbers.append(rep["report_no"])
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                store.close()
        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(set(numbers)), 8)
        store = open_store(path)
        self.assertEqual(store.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 8)
        # 기록이 지워져도 남은 최대 번호 다음을 발급(COUNT 방식이면 겹침)
        last = max(numbers)
        store.execute("DELETE FROM runs WHERE report_no != ?", (last,))
        store.commit()
        from milcheck.workflow import _next_report_no
        self.assertEqual(int(_next_report_no(store)[-4:]), int(last[-4:]) + 1)
        store.close()

    def test_grounding_requires_matching_current_article(self):
        src = lambda title, date="2026-06-03", status="현행": {  # noqa: E731
            "title": title, "status": status, "excerpt": "조문", "source_date": date}
        base = {"rules_current_as_of": "2026-07-23", "legal_ground": "국가계약법 시행령 제26조제1항제2호"}
        ok = lambda *s: web.grounding_ok({**base, "retrieved_sources": list(s)})  # noqa: E731
        self.assertTrue(ok(src("국가계약법 시행령 제26조제1항제2호 경쟁 불성립")))
        self.assertFalse(ok(src("국가계약법 시행령 제26조제1항제5호가목 소액수의")))  # 무관한 조항
        self.assertFalse(ok(src("국가계약법 제7조 계약의 방법")))                     # 다른 법령
        self.assertFalse(ok(src("국가계약법 시행령 제26조제1항제2호", status="역사적")))
        self.assertFalse(ok(src("국가계약법 시행령 제26조제1항제2호", date="2026-09-01")))  # 기준일 이후
        self.assertFalse(ok({**src("국가계약법 시행령 제26조제1항제2호"), "excerpt": ""}))
        self.assertFalse(web.grounding_ok({**base, "legal_ground": "계약 정보 부족 — 추가 입력 필요",
                                           "retrieved_sources": [src("국가계약법 시행령 제26조제1항제2호")]}))
        # 목 단위 근거는 호 단위 원문까지 인정
        self.assertTrue(web.grounding_ok({**base, "legal_ground": "국가계약법 시행령 제26조제1항제5호가목",
                                          "retrieved_sources": [src("국가계약법 시행령 제26조제1항제5호가목 소액수의")]}))
        self.assertTrue(web.grounding_ok({**base, "legal_ground": "국가계약법 시행령 제26조제1항제1호가목",
                                          "retrieved_sources": [src("국가계약법 시행령 제26조제1항제1호 긴급·보안")]}))

    def test_grounding_with_real_agent_sources(self):
        from milcheck.agent import MilCheckAgent
        agent = MilCheckAgent(use_ml=False, use_contract_index=False)
        for proposed, extra in (("sole_source", {"sole_source_basis": "compatibility"}),
                                ("small_amount", {}), ("urgent_security", {"urgent_security_basis": "urgent"})):
            rev = agent.review({"item_name": "부품", "contract_category": "goods", "proposed_type": proposed,
                                "estimated_price_krw_ex_vat": 1_000_000, "evidence": [], **extra})
            self.assertTrue(web.grounding_ok(rev), (proposed, rev["legal_ground"]))

    def test_llm_time_budget(self):
        cfg = understanding.EndpointConfig(timeout_seconds=120, per_document=True)
        with mock.patch.dict(os.environ, {"VERCEL": "1"}):
            os.environ.pop("MILCHECK_WEB_LLM_BUDGET", None)
            self.assertEqual(web.budgeted(cfg, 4).timeout_seconds, 6)   # 25초 / 4건
        with mock.patch.dict(os.environ, {"MILCHECK_WEB_LLM_BUDGET": "60"}):
            self.assertEqual(web.budgeted(cfg, 2).timeout_seconds, 30)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VERCEL", None)
            os.environ.pop("MILCHECK_WEB_LLM_BUDGET", None)
            self.assertEqual(web.budgeted(cfg, 4).timeout_seconds, 120)  # 로컬 기본: 기존 동작


if __name__ == "__main__":
    unittest.main()
