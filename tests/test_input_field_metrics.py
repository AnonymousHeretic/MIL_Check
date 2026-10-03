"""판정 입력 후보 채점: 오답·누락·출처 혼동을 정답으로 세지 않는다."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from eval_understanding import (aggregate_input_fields, ratio, score_input_fields)  # noqa: E402


def candidate(field, value, *documents):
    return SimpleNamespace(field=field, value=value,
                           source_refs=[SimpleNamespace(document_id=d) for d in documents])


def score(gold, pred, ambiguous=()):
    bundle = {"gold": {"facts": [{"field": f, "value": v, "document_id": d}
                                 for f, v, d in gold], "ambiguous_fields": ambiguous}}
    return score_input_fields(bundle, SimpleNamespace(candidates=pred))


class InputFieldMetricsTests(unittest.TestCase):
    def test_wrong_amount_and_missing_basis_are_counted(self):
        r = score([("item_name", "장비", "R1"), ("estimated_price_krw", 100, "R1"),
                   ("sole_source_basis", "compatibility", "R1")],
                  [candidate("item_name", "장비", "R1"),
                   candidate("estimated_price_krw", 110, "R1")])
        self.assertEqual(r["decision_input"], {"tp": 1, "pred": 2, "gold": 3, "fp": 1, "fn": 2})
        self.assertEqual(r["per_field"]["estimated_price_krw"]["fp"], 1)

    def test_correct_value_wrong_document_is_not_source_correct(self):
        r = score([("tax_status", "vat_excluded", "R1")],
                  [candidate("tax_status", "vat_excluded", "Q1")])
        self.assertEqual(r["decision_input"]["tp"], 1)
        self.assertEqual(r["decision_input_source"], {"tp": 0, "pred": 1, "gold": 1, "fp": 1, "fn": 1})

    def test_missing_source_counts_as_false_positive(self):
        r = score([("item_name", "장비", "R1")], [candidate("item_name", "장비")])
        self.assertEqual(r["decision_input_source"]["pred"], 1)
        self.assertEqual(r["decision_input_source"]["tp"], 0)

    def test_deduplication_keeps_both_conflicting_values(self):
        r = score([("estimated_price_krw", 100, "R1"), ("estimated_price_krw", 200, "R2")],
                  [candidate("estimated_price_krw", 100, "R1", "R1"),
                   candidate("estimated_price_krw", 100, "R1"),
                   candidate("estimated_price_krw", 200, "R2")])
        self.assertEqual(r["decision_input"]["tp"], 2)
        self.assertEqual(r["decision_input"]["pred"], 2)
        self.assertEqual(r["decision_input_source"]["pred"], 2)

    def test_ambiguous_and_non_rule_fields_do_not_inflate_profile(self):
        r = score([("sole_source_basis", "compatibility", "R1"),
                   ("supplier_name", "업체", "R1")],
                  [candidate("sole_source_basis", "single_supplier", "R1"),
                   candidate("supplier_name", "업체", "R1")], ["sole_source_basis"])
        self.assertEqual(r["decision_input"]["pred"], 0)
        self.assertEqual(r["decision_input_source"]["gold"], 0)
        self.assertEqual(r["per_field"]["supplier_name"]["tp"], 1)
        self.assertEqual(r["excluded_ambiguous_fields"], ["sole_source_basis"])

    def test_micro_aggregation_and_empty_denominator(self):
        a = score([("item_name", "A", "R1")], [candidate("item_name", "A", "R1")])
        b = score([("item_name", "B", "R1"), ("quote_count", 2, "R1")],
                  [candidate("item_name", "X", "R1"), candidate("quote_count", 3, "R1")])
        m = aggregate_input_fields([{"input_fields": a}, {"input_fields": b}])
        self.assertEqual(m["decision_input"]["precision"]["value"], 0.3333)
        self.assertIsNone(m["per_field"]["quantity"]["precision"]["value"])
        self.assertEqual(ratio(1, 2)["wilson_95_ci"], [0.0945, 0.9055])
        self.assertIsNone(ratio(0, 0)["wilson_95_ci"])

    def test_summary_only_contains_no_case_details(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "report.json"
            subprocess.run([sys.executable, str(ROOT / "scripts/eval_understanding.py"),
                            "--mode", "gold-echo", "--summary-only", "--output", str(output)],
                           check=True, capture_output=True)
            r = json.loads(output.read_text())
        self.assertNotIn("bundles", r)
        self.assertNotIn("false_facts", json.dumps(r))
        self.assertEqual(r["metrics"]["decision_input_precision"]["value"], 1.0)
        self.assertEqual(r["input_field_metrics"]["decision_input_source"]["precision"]["value"], 1.0)
        self.assertEqual(len(r["dataset_sha256"][0]["sha256"]), 64)
        self.assertIn("structured", r)

    def test_empty_and_duplicate_datasets_fail_before_inference(self):
        source = ROOT / "eval/understanding_dev.jsonl"
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "empty.jsonl"
            empty.write_text("")
            for data in (str(empty), f"{source},{source}"):
                output = Path(tmp) / "report.json"
                result = subprocess.run([sys.executable, str(ROOT / "scripts/eval_understanding.py"),
                                         "--mode", "gold-echo", "--data", data, "--output", str(output)],
                                        capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
