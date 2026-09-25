"""평가 도구 무결성 시험: 개발세트 정답이 경계를 통과하는지(채점기 점검)."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class EvalToolTests(unittest.TestCase):
    def test_dev_set_quotes_exist_in_documents(self):
        for line in (ROOT / "eval/understanding_dev.jsonl").read_text(encoding="utf-8").splitlines():
            b = json.loads(line)
            texts = {d["document_id"]: d["text"] for d in b["documents"]}
            self.assertEqual(b["split"], "dev")
            for f in b["gold"]["facts"]:
                self.assertIn(f["quote"], texts[f["document_id"]], b["bundle_id"])

    def test_gold_echo_scores_perfect(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "r.json"
            subprocess.run([sys.executable, str(ROOT / "scripts/eval_understanding.py"),
                            "--mode", "gold-echo", "--output", str(out)],
                           check=True, capture_output=True)
            m = json.loads(out.read_text(encoding="utf-8"))["metrics"]
        for key in ("fact_precision", "fact_recall", "conflict_recall", "missing_question_recall",
                    "evidence_presence_accuracy"):
            self.assertEqual(m[key]["value"], 1.0, key)
        self.assertEqual(m["rejected_items"], 0)
        self.assertEqual(m["fabricated_required_fields"], 0)


if __name__ == "__main__":
    unittest.main()
