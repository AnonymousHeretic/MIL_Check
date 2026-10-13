"""웹 MVP(로컬) 흐름 시험: milcheck.web.handle을 HTTP 없이 호출한다."""
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

from milcheck import web  # noqa: E402
from milcheck.workflow import ConfirmationError  # noqa: E402


class FakeAgent:
    def __init__(self):
        from milcheck.rules import RuleEngine
        self.engine = RuleEngine.from_file(ROOT / "data/rules.json")
        self.calls = []

    def review(self, case):
        self.calls.append(dict(case))
        ev = self.engine.evaluate(case).as_dict()
        return {**ev, "item_name": case.get("item_name"), "rules_current_as_of": "t",
                "advisory": {"signals": []}, "retrieved_sources": [], "disclaimer": "d"}


def post(path, form):
    r = web.handle("POST", path, urlencode(form, doseq=True).encode())
    return r.status, r.body.decode()


class WebMvpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"MILCHECK_WEB_DB": str(Path(self.tmp.name) / "w.sqlite")})
        self.env.start()
        os.environ.pop("MILCHECK_DEPLOY", None)
        self.agent = FakeAgent()
        web.set_agent(self.agent)

    def tearDown(self):
        web.set_agent(None)
        self.env.stop()
        self.tmp.cleanup()

    def test_index_lists_synthetic_bundles(self):
        body = web.handle("GET", "/").body.decode()
        for bid in web.load_bundles():
            self.assertIn(bid, body)
        self.assertIn(web.INPUT_WARNING, body)

    def test_manual_mode_has_no_candidates(self):
        status, body = post("/understand", {"mode": "manual", "source": "bundle", "bundle_id": "UD-01"})
        self.assertEqual(status, 200)
        self.assertNotRegex(body, r'name="f:\w+" value="F')
        self.assertIn("자동 추출 불가", body)

    def test_vat_included_requires_manual_price(self):
        base = {"mode": "gold-echo", "source": "bundle", "bundle_id": "UD-07"}
        _, page = post("/understand", base)
        form = dict(base, contract_category="goods", proposed_type="sole_source", reviewer="r")
        inc = re.search(r'name="f:tax_status" value="(F\w+)"> <b>부가세 포함', page).group(1)
        price = re.search(r'name="f:estimated_price_krw" value="(F\w+)"', page).group(1)
        form.update({"f:tax_status": inc, "f:estimated_price_krw": price})
        status, body = post("/review", form)
        self.assertEqual(status, 400)
        self.assertIn("부가세", body)
        form["estimated_price_krw_ex_vat"] = "10,000,000"
        status, body = post("/review", form)
        self.assertEqual(status, 200)
        self.assertEqual(self.agent.calls[-1]["estimated_price_krw_ex_vat"], 10_000_000)

    def test_only_confirmed_values_reach_rules(self):
        base = {"mode": "gold-echo", "source": "bundle", "bundle_id": "UD-01"}
        status, body = post("/review", dict(base, contract_category="goods", proposed_type="sole_source",
                                             reviewer="r", estimated_price_krw_ex_vat="5"))
        self.assertEqual(status, 200)
        case = self.agent.calls[-1]
        self.assertNotIn("item_name", case)
        self.assertEqual(case["evidence"], [])

    def test_bad_choices_rejected(self):
        base = {"mode": "gold-echo", "source": "bundle", "bundle_id": "UD-01", "reviewer": "r",
                "proposed_type": "sole_source"}
        self.assertEqual(post("/review", dict(base, contract_category="construction"))[0], 400)
        self.assertEqual(post("/review", dict(base, contract_category="goods",
                                              **{"f:item_name": "nope"}))[0], 400)
        self.assertEqual(post("/review", dict(base, contract_category="goods",
                                              **{"x:item_name": "x", "evidence": ["bogus"]}))[0], 200)
        self.assertNotIn("bogus", self.agent.calls[-1]["evidence"])
        self.assertEqual(post("/understand", {"mode": "gold-echo", "source": "paste",
                                              "doc_R1": "x"})[0], 400)
        self.assertEqual(post("/understand", {"mode": "x"})[0], 400)

    def test_html_is_escaped(self):
        status, body = post("/understand", {"mode": "manual", "source": "paste",
                                            "doc_R1": "<script>alert(1)</script>"})
        self.assertEqual(status, 200)
        self.assertNotIn("<script>alert(1)</script>", body)

    def test_records_and_integrity(self):
        base = {"mode": "gold-echo", "source": "bundle", "bundle_id": "UD-01"}
        status, body = post("/review", dict(base, contract_category="goods", proposed_type="sole_source",
                                             reviewer="r", **{"f:item_name": "__manual__", "m:item_name": "충전기"},
                                             estimated_price_krw_ex_vat="12000000"))
        self.assertEqual(status, 200)
        report_no = re.search(r"MC-\d{8}-\d{4}", body).group(0)
        runs = web.handle("GET", "/runs").body.decode()
        self.assertIn(report_no, runs)
        self.assertIn(web.SAME_DB_LIMIT, runs)
        self.assertNotIn(web.VOLATILE_NOTICE, runs)
        db = sqlite3.connect(os.environ["MILCHECK_WEB_DB"])
        db.execute("UPDATE runs SET record_json=replace(record_json,'충전기','변조')")
        db.commit()
        db.close()
        self.assertIn("불일치", web.handle("GET", f"/runs/{report_no}").body.decode())

    def test_manual_int_parse(self):
        with self.assertRaises(ConfirmationError):
            web._parse_int("12만", "x")
        self.assertEqual(web._parse_int("1,200원", "x"), 1200)


if __name__ == "__main__":
    unittest.main()
