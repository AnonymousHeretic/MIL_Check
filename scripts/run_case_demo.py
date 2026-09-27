"""한 건 완결 흐름 시연: 문서 → 문서 이해(후보) → 담당자 확인 → 규칙 판정 → 결과서 → 기록 DB.

기본은 GPU 없이 gold-echo(정답 되돌림) 전송으로 흐름만 확인한다. 실제 모델은 --live.
    python scripts/run_case_demo.py --bundle UD-01 --out out_dir [--live]
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
from milcheck.understanding import Document, DocumentUnderstanding, EndpointConfig  # noqa
from milcheck.workflow import Confirmation, open_store, run_case, review_sheet, render_markdown, lookup  # noqa
from milcheck.agent import MilCheckAgent  # noqa
from eval_understanding import gold_echo_transport  # noqa

ap = argparse.ArgumentParser()
ap.add_argument("--bundle", default="UD-01"); ap.add_argument("--out", required=True)
ap.add_argument("--live", action="store_true"); a = ap.parse_args()
b = next(json.loads(l) for l in (ROOT / "eval/understanding_dev.jsonl").read_text(encoding="utf-8").splitlines()
         if l.strip() and json.loads(l)["bundle_id"] == a.bundle)
docs = [Document(d["document_id"], d["revision"], d["text"], doc_type=d["doc_type"]) for d in b["documents"]]
cfg = EndpointConfig.from_env()
du = DocumentUnderstanding(cfg, transport=None if a.live else gold_echo_transport(b))
res = du.understand(docs)
out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
sheet = review_sheet(res)
(out / "1_review_sheet.json").write_text(json.dumps(sheet, ensure_ascii=False, indent=2), encoding="utf-8")

# 담당자 확인(시연용 입력): 각 필드의 첫 후보를 선택, 증빙은 첨부된 문서 기준으로 체크
fields = {f: {"fact_id": c[0]["fact_id"]} for f, c in sheet["fields"].items()}
checked = ["price_reasonableness", "vendor_eligibility", "conflict_of_interest_check"]
for e in sheet["evidence"]:
    if e["presence"] == "present":
        checked += e["rule_items_to_check"]
conf = Confirmation(reviewer="담당자(시연)", contract_category="goods", proposed_type="sole_source",
                    fields=fields, evidence_checked=checked, extra_case={"alternatives_exist": False})
agent = MilCheckAgent()
store = open_store(out / "milcheck_runs.sqlite")
rep = run_case(res, conf, agent, store, case_id=a.bundle)
(out / "2_report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
(out / "3_report.md").write_text(render_markdown(rep), encoding="utf-8")
chk = lookup(store, rep["report_no"])
print(json.dumps({"report_no": rep["report_no"], "decision": rep["review"]["decision"],
                  "missing": rep["review"]["missing_evidence"], "integrity_ok": chk["integrity_ok"]},
                 ensure_ascii=False))
