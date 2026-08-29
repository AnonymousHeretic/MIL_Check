"""MIL-Check 회귀 테스트.

핵심 불변식은 "ML·검색 계층이 규칙 엔진의 판정을 바꾸지 못한다"이다.
성능 지표는 milcheck.evaluation이 담당하고, 여기서는 구조적 계약을 검증한다.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from milcheck.agent import MilCheckAgent
from milcheck.contracts import ContractIndex
from milcheck.evaluation import (eval_audit, eval_extraction, eval_retrieval,
                                 eval_rules)
from milcheck.extract import extract, parse_amount
from demo_app import PRESETS
from milcheck.linear_model import LinearTextClassifier, make_text
from milcheck.ml import MLAdvisor
from milcheck.rules import RuleEngine

ROOT = Path(__file__).resolve().parent.parent


class TestRules(unittest.TestCase):
    def setUp(self):
        self.engine = RuleEngine.from_file(ROOT / "data" / "rules.json")

    def test_small_amount_threshold_boundary(self):
        base = {"item_name": "테스트", "contract_category": "goods",
                "proposed_type": "small_amount", "small_amount_basis": "general",
                "contractor_category": "general", "quote_count_planned": 1,
                "electronic_quotes_planned": False, "split_contract_risk": False,
                "evidence": ["price_reasonableness", "vendor_eligibility",
                             "conflict_of_interest_check",
                             "no_artificial_split_review"]}
        self.assertEqual(
            self.engine.evaluate({**base, "estimated_price_krw_ex_vat": 20_000_000}).decision,
            "PASS_WITH_CONTROLS")
        self.assertEqual(
            self.engine.evaluate({**base, "estimated_price_krw_ex_vat": 20_000_001}).decision,
            "REJECT_GROUND")

    def test_self_created_urgency_is_rejected(self):
        case = {"item_name": "테스트", "contract_category": "goods",
                "proposed_type": "urgent_security", "urgent_security_basis": "urgent",
                "urgency_cause": "routine_delay",
                "estimated_price_krw_ex_vat": 30_000_000, "evidence": []}
        self.assertEqual(self.engine.evaluate(case).decision, "REJECT_GROUND")

    def test_single_supplier_uses_consistent_rule_key(self):
        self.assertIn("single_supplier", self.engine.rules["sole_source"]["bases"])
        self.assertNotIn("single_producer", self.engine.rules["sole_source"]["bases"])

    def test_documented_additional_types_are_conservative(self):
        for ptype, category in (("post_tender", "goods"), ("designated_product", "goods"), ("lease", "lease")):
            result = self.engine.evaluate({
                "item_name": "추가 유형 테스트", "contract_category": category,
                "proposed_type": ptype, "estimated_price_krw_ex_vat": 30_000_000,
                "evidence": [],
            })
            self.assertEqual(result.decision, "NEEDS_EVIDENCE")
            self.assertTrue(result.missing_evidence)

    def test_out_of_scope_categories(self):
        case = {"item_name": "공사", "contract_category": "construction",
                "proposed_type": "small_amount",
                "estimated_price_krw_ex_vat": 10_000_000, "evidence": []}
        self.assertEqual(self.engine.evaluate(case).decision, "OUT_OF_SCOPE")

    def test_determinism(self):
        case = {"item_name": "동일 입력", "contract_category": "goods",
                "proposed_type": "sole_source", "sole_source_basis": "compatibility",
                "estimated_price_krw_ex_vat": 40_000_000, "evidence": []}
        first = self.engine.evaluate(case).as_dict()
        for _ in range(5):
            self.assertEqual(self.engine.evaluate(case).as_dict(), first)


class TestLayerAuthority(unittest.TestCase):
    """ML·계약데이터 계층이 판정을 바꾸지 않는지 확인한다."""

    @classmethod
    def setUpClass(cls):
        cls.plain = MilCheckAgent(use_ml=False, use_contract_index=False)
        cls.full = MilCheckAgent(use_ml=True, use_contract_index=True)

    def test_decision_unchanged_by_advisory_layers(self):
        cases = []
        with (ROOT / "eval" / "cases.jsonl").open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    cases.append(json.loads(line))
        for case in cases:
            case = {k: v for k, v in case.items() if k != "expected_decision"}
            a = self.plain.review(case)
            b = self.full.review(case)
            self.assertEqual(a["decision"], b["decision"], case.get("case_id"))
            self.assertEqual(a["findings"], b["findings"], case.get("case_id"))
            self.assertEqual(a["missing_evidence"], b["missing_evidence"],
                             case.get("case_id"))

    def test_advisory_signals_are_labelled(self):
        case = {"case_id": "T", "item_name": "감시장비 외주정비 용역",
                "contract_category": "service", "proposed_type": "small_amount",
                "small_amount_basis": "general", "contractor_category": "general",
                "estimated_price_krw_ex_vat": 19_000_000, "quote_count_planned": 1,
                "electronic_quotes_planned": False, "split_contract_risk": False,
                "evidence": ["price_reasonableness", "vendor_eligibility",
                             "conflict_of_interest_check",
                             "no_artificial_split_review"]}
        report = self.full.review(case)
        for signal in report["advisory"]["signals"]:
            self.assertIn(signal["severity"], {"info", "warning"})
            self.assertTrue(signal["code"].startswith(("ML-", "DATA-")))

    def test_method_review_case_has_competition_signal(self):
        case = {**PRESETS["method_review"]["case"]}
        report = self.full.review(case)
        self.assertEqual(report["decision"], "PASS_WITH_CONTROLS")
        messages = [s["message"] for s in report["advisory"]["signals"]]
        self.assertTrue(any("상위 5건 중 4건" in m and "제한경쟁" in m for m in messages))


class TestOfflineInference(unittest.TestCase):
    def test_models_load_and_predict(self):
        advisor = MLAdvisor()
        text = make_text("행정용 바코드 스캐너", "물품", 18_000_000)
        probs = advisor.article.predict_proba(text)
        self.assertAlmostEqual(sum(p for _, p in probs), 1.0, places=5)
        self.assertEqual(probs, sorted(probs, key=lambda t: -t[1]))

    def test_classifier_is_deterministic(self):
        clf = LinearTextClassifier.load(ROOT / "data" / "model_article.json.gz")
        text = make_text("전투식량 구매", "물품", 14_000_000)
        self.assertEqual(clf.predict_proba(text), clf.predict_proba(text))


class TestContractIndex(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = ContractIndex.load()

    def test_no_personal_fields_in_index(self):
        forbidden = {"대표업체명", "사업자등록번호", "대표업체주소", "담당자명"}
        for record in self.index.records[:200]:
            self.assertFalse(forbidden & set(record.keys()))

    def test_similar_search_returns_ranked_hits(self):
        hits = self.index.search("전투식량 구매", top_k=5)
        self.assertTrue(hits)
        self.assertEqual([h["score"] for h in hits],
                         sorted((h["score"] for h in hits), reverse=True))


class TestExtraction(unittest.TestCase):
    def test_korean_compound_numerals(self):
        self.assertEqual(parse_amount("1,800만 원"), 18_000_000)
        self.assertEqual(parse_amount("6천만원"), 60_000_000)
        self.assertEqual(parse_amount("1억 2천만원"), 120_000_000)
        self.assertEqual(parse_amount("3천 5백만원"), 35_000_000)

    def test_vat_inclusive_is_converted(self):
        fields = extract("부가세 포함해서 2,200만 원입니다")["fields"]
        self.assertEqual(fields["estimated_price_krw_ex_vat"], 20_000_000)

    def test_internal_delay_is_flagged(self):
        fields = extract("내부 결재가 늦어져서 긴급하게 사야 합니다. 3천만원")["fields"]
        self.assertTrue(fields.get("urgency_cause_internal_delay"))
        self.assertTrue(fields.get("self_created_urgency"))
        self.assertEqual(fields.get("urgency_cause"), "routine_delay")

    def test_specific_technical_service_is_extractable(self):
        fields = extract("특정 기술과 전문 경험이 필요한 용역을 수의계약으로 진행합니다")["fields"]
        self.assertEqual(fields.get("proposed_type"), "sole_source")
        self.assertEqual(fields.get("sole_source_basis"), "specific_technical_service")

    def test_documented_additional_types_are_extractable(self):
        self.assertEqual(extract("재공고했지만 유찰되어 수의계약을 검토합니다 8천만원")["fields"]["proposed_type"], "post_tender")
        self.assertEqual(extract("우수조달제품 인증서가 있는 장비를 구매합니다 3천만원")["fields"]["proposed_type"], "designated_product")
        self.assertEqual(extract("장비를 임대차 계약으로 임차합니다 3천만원")["fields"]["contract_category"], "lease")


class TestEvaluationSuite(unittest.TestCase):
    """평가 스위트가 회귀 없이 기준선을 유지하는지 확인한다."""

    @classmethod
    def setUpClass(cls):
        cls.agent = MilCheckAgent(use_ml=False, use_contract_index=False)

    def test_rule_regression_has_no_false_pass(self):
        result = eval_rules(self.agent)
        self.assertEqual(result["false_pass"], 0)
        self.assertEqual(result["failures"], [])

    def test_audit_reproduction_baseline(self):
        self.assertGreaterEqual(eval_audit(self.agent)["detection_rate"], 0.9)

    def test_retrieval_baseline(self):
        result = eval_retrieval(self.agent.retriever, modes=("ngram",))
        self.assertGreaterEqual(result["by_mode"]["ngram"]["recall_at_3"], 0.9)

    def test_extraction_holdout_baseline(self):
        result = eval_extraction(ROOT / "eval" / "extraction_holdout.jsonl")
        self.assertGreaterEqual(result["field_level_accuracy"], 0.85)

    def test_method_review_demo_preset_is_distinct_from_out_of_scope(self):
        self.assertEqual(PRESETS["method_review"]["case"]["case_id"], "DEMO-D")
        self.assertEqual(PRESETS["out_scope"]["case"]["case_id"], "DEMO-E")

    def test_article_abstention_is_explicit(self):
        result = MLAdvisor().advise({
            "item_name": "감시장비 외주정비 용역",
            "contract_category": "service",
            "proposed_type": "small_amount",
            "small_amount_basis": "general",
            "estimated_price_krw_ex_vat": 19_000_000,
        })
        self.assertIn("article_abstained", result)

    def test_low_information_input_withholds_recommendations(self):
        result = MLAdvisor().advise({})
        self.assertTrue(result["article_abstained"])
        self.assertTrue(result["method_abstained"])
        self.assertEqual(result["article_top_k"], [])
        self.assertEqual(result["method_top_k"], [])


if __name__ == "__main__":
    unittest.main()
