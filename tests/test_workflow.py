import json, sys, tempfile, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
from milcheck.understanding import Document, DocumentUnderstanding, EndpointConfig
from milcheck.workflow import (Confirmation, ConfirmationError, confirm, open_store, run_case,
                               review_sheet, lookup, render_markdown)
from eval_understanding import gold_echo_transport

BUNDLES = {json.loads(l)["bundle_id"]: json.loads(l)
           for l in (ROOT / "eval/understanding_dev.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()}


def understand(bid):
    b = BUNDLES[bid]
    docs = [Document(d["document_id"], d["revision"], d["text"], doc_type=d["doc_type"]) for d in b["documents"]]
    cfg = EndpointConfig(base_url="http://127.0.0.1:8000/v1", model="m", per_document=True)
    return DocumentUnderstanding(cfg, transport=gold_echo_transport(b)).understand(docs)


def first_choices(res, skip=()):
    out = {}
    for c in res.candidates:
        if c.field not in out and c.field not in skip:
            out[c.field] = {"fact_id": c.fact_id}
    return out


class FakeAgent:
    """규칙 엔진만 쓰는 대역(ML·인덱스 로드 없이 빠르게)."""
    def __init__(self):
        from milcheck.rules import RuleEngine
        self.engine = RuleEngine.from_file(ROOT / "data/rules.json")
        self.calls = []
    def review(self, case):
        self.calls.append(dict(case))
        ev = self.engine.evaluate(case).as_dict()
        return {**ev, "item_name": case.get("item_name"), "rules_current_as_of": "t",
                "advisory": {"signals": []}, "disclaimer": "d"}


def conf(res, **kw):
    base = dict(reviewer="r", contract_category="goods", proposed_type="sole_source",
                fields=first_choices(res))
    base.update(kw)
    return Confirmation(**base)


class WorkflowTest(unittest.TestCase):
    def test_conflict_requires_choice(self):
        res = understand("UD-08")
        self.assertIn("supplier_name", review_sheet(res)["conflicts"])
        with self.assertRaises(ConfirmationError):
            confirm(res, conf(res, fields=first_choices(res, skip={"supplier_name"})))
        confirm(res, conf(res))  # 선택하면 통과

    def test_vat_included_never_auto_converted(self):
        res = understand("UD-07")
        fields = first_choices(res)
        inc = [c for c in res.candidates if c.field == "tax_status" and c.value == "vat_included"]
        fields["tax_status"] = {"fact_id": inc[0].fact_id}
        with self.assertRaises(ConfirmationError):
            confirm(res, conf(res, fields=fields))
        case, _ = confirm(res, conf(res, fields=fields, estimated_price_krw_ex_vat=10_000_000))
        self.assertEqual(case["estimated_price_krw_ex_vat"], 10_000_000)

    def test_unconfirmed_candidates_not_in_case(self):
        res = understand("UD-01")
        case, _ = confirm(res, conf(res, fields={}, estimated_price_krw_ex_vat=5))
        self.assertNotIn("item_name", case)
        self.assertEqual(case["evidence"], [])

    def test_extra_case_cannot_override(self):
        res = understand("UD-01")
        with self.assertRaises(ConfirmationError):
            confirm(res, conf(res, extra_case={"proposed_type": "small_amount"}))
        with self.assertRaises(ConfirmationError):
            confirm(res, conf(res, extra_case={"evidence": ["x"]}))
        with self.assertRaises(ConfirmationError):
            confirm(res, conf(res, extra_case={"item_name": "x"}))

    def test_bad_choices_rejected(self):
        res = understand("UD-01")
        with self.assertRaises(ConfirmationError):
            confirm(res, conf(res, contract_category="construction"))
        with self.assertRaises(ConfirmationError):
            confirm(res, conf(res, fields={"item_name": {"fact_id": "nope"}}))
        with self.assertRaises(ConfirmationError):
            confirm(res, conf(res, reviewer=""))

    def test_end_to_end_record_and_no_text(self):
        res = understand("UD-01")
        with tempfile.TemporaryDirectory() as d:
            store = open_store(Path(d) / "r.sqlite")
            agent = FakeAgent()
            rep = run_case(res, conf(res), agent, store, case_id="UD-01")
            rep2 = run_case(res, conf(res), agent, store, case_id="UD-01")
            self.assertNotEqual(rep["report_no"], rep2["report_no"])
            self.assertEqual(rep["review"]["decision"], rep2["review"]["decision"])
            got = lookup(store, rep["report_no"])
            self.assertTrue(got["integrity_ok"])
            raw = store.execute("SELECT record_json FROM runs").fetchone()[0]
            for doc in BUNDLES["UD-01"]["documents"]:
                self.assertNotIn(doc["text"], raw)
            store.execute("UPDATE runs SET record_json=replace(record_json,'NEEDS','PASS_')")
            self.assertFalse(lookup(store, rep["report_no"])["integrity_ok"])
            self.assertIn("사전검토 결과서", render_markdown(rep))

    def test_questions_do_not_change_decision(self):
        # 문서 이해 결과(질문·증빙 존재)가 달라도 같은 확정값이면 판정은 같다
        a, b = understand("UD-01"), understand("UD-06")
        agent = FakeAgent()
        with tempfile.TemporaryDirectory() as d:
            store = open_store(Path(d) / "r.sqlite")
            c = dict(reviewer="r", contract_category="goods", proposed_type="sole_source",
                     fields={"item_name": {"manual": "충전기"}, "sole_source_basis": {"manual": "compatibility"}},
                     estimated_price_krw_ex_vat=12_000_000)
            ra = run_case(a, Confirmation(**c), agent, store)
            rb = run_case(b, Confirmation(**c), agent, store)
            self.assertEqual(ra["review"]["decision"], rb["review"]["decision"])
            self.assertEqual(agent.calls[0], agent.calls[1])


if __name__ == "__main__":
    unittest.main()
