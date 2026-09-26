"""문서 이해 개발세트 평가기.

사용:
    # 채점기 자체 점검: 정답을 모델 응답처럼 되돌려 100%가 나오는지 확인(모델 성능 아님)
    python scripts/eval_understanding.py --mode gold-echo --output out.json
    # 실제 로컬 모델(OpenAI 호환 서버, 루프백 또는 승인 호스트)
    MILCHECK_LLM_BASE_URL=http://127.0.0.1:8000/v1 MILCHECK_LLM_MODEL=<모델명> \
        python scripts/eval_understanding.py --mode live --output out.json

데이터: eval/understanding_dev.jsonl (split=dev, 합성). 최종 성능 주장에 쓰지 않는다.
"""
from __future__ import annotations

import argparse
import json
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from milcheck.understanding import (  # noqa: E402
    REQUIRED_FIELDS, SCHEMA_VERSION, Document, DocumentUnderstanding, EndpointConfig)


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def gold_echo_transport(bundle: dict):
    texts = {d["document_id"]: d["text"] for d in bundle["documents"]}

    def transport(url, body, headers, timeout):
        user = body["messages"][1]["content"]
        present = {d for d in texts if f'"document_id": "{d}"' in user}
        facts = []
        for f in bundle["gold"]["facts"]:
            if f["document_id"] not in present:
                continue
            start = texts[f["document_id"]].index(f["quote"])
            facts.append({"field": f["field"], "value": f["value"], "source_refs": [{
                "document_id": f["document_id"], "revision": 1, "text_start": start,
                "text_end": start + len(f["quote"]), "quote": f["quote"]}]})
        payload = {"schema_version": SCHEMA_VERSION, "facts": facts,
                   "evidence_claims": [], "questions": []}
        return json.dumps({"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]}).encode()
    return transport


def score_bundle(bundle: dict, result) -> dict:
    gold = bundle["gold"]
    skip = set(gold.get("ambiguous_fields", []))
    gold_pairs = {(f["field"], json.dumps(f["value"], ensure_ascii=False))
                  for f in gold["facts"] if f["field"] not in skip}
    pred_pairs = {(c.field, json.dumps(c.value, ensure_ascii=False))
                  for c in result.candidates if c.field not in skip}
    tp = len(gold_pairs & pred_pairs)
    conflicts_pred = {c.field for c in result.candidates if c.status == "conflicting"}
    conflicts_gold = set(gold["conflict_fields"])
    missing_q = {f for q in result.questions if q.reason == "missing" for f in q.affected_fields}
    missing_gold = set(gold["missing_fields"])
    absent_pred = {e.requirement_id for e in result.evidence if e.presence == "absent"}
    wrong_required = [f for f in REQUIRED_FIELDS if f in missing_gold and f in {c.field for c in result.candidates}]
    return {
        "bundle_id": bundle["bundle_id"], "component_status": result.component_status,
        "fact_tp": tp, "fact_pred": len(pred_pairs), "fact_gold": len(gold_pairs),
        "false_facts": sorted(pred_pairs - gold_pairs), "missed_facts": sorted(gold_pairs - pred_pairs),
        "conflict_tp": len(conflicts_pred & conflicts_gold), "conflict_pred": len(conflicts_pred),
        "conflict_gold": len(conflicts_gold),
        "missing_q_tp": len(missing_q & missing_gold), "missing_gold": len(missing_gold),
        "fabricated_required": wrong_required,
        "evidence_presence_correct": absent_pred == set(gold["evidence_absent"]),
        "rejected": len(result.rejected),
        "rejected_detail": [{k: r.get(k) for k in ("kind", "field", "reason", "document_id")}
                            for r in result.rejected],
        "relocated_refs": sum(1 for c in result.candidates for r in c.source_refs if r.relocated),
    }


def ratio(n, d):
    return {"num": n, "den": d, "value": round(n / d, 4) if d else None}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["gold-echo", "live"], required=True)
    ap.add_argument("--data", default=str(ROOT / "eval/understanding_dev.jsonl"))
    ap.add_argument("--output", required=True)
    ap.add_argument("--exclude", default="", help="쉼표로 구분한 제외 bundle_id(사전 등록된 것만)")
    args = ap.parse_args()

    paths = [Path(x) for x in args.data.split(",")]
    excluded = {x.strip() for x in args.exclude.split(",") if x.strip()}
    bundles = [b for p in paths for b in load(p) if b["bundle_id"] not in excluded]
    config = EndpointConfig.from_env()
    rows, latencies = [], []
    for b in bundles:
        docs = [Document(d["document_id"], d["revision"], d["text"], doc_type=d["doc_type"],
                         origin=b["origin"]) for d in b["documents"]]
        transport = gold_echo_transport(b) if args.mode == "gold-echo" else None
        du = DocumentUnderstanding(config, transport=transport)
        t0 = time.perf_counter()
        result = du.understand(docs)
        latencies.append(time.perf_counter() - t0)
        row = score_bundle(b, result)
        row["error"] = result.run_record.get("error")
        rows.append(row)

    s = lambda k: sum(r[k] for r in rows)  # noqa: E731
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                                text=True, check=False).stdout.strip() or None
    except OSError:
        commit = None
    report = {
        "mode": args.mode, "dataset": [p.name for p in paths], "excluded": sorted(excluded), "split": sorted({b.get("split", "dev") for b in bundles}),
        "n_bundles": len(rows), "code_commit": commit, "model": config.model if args.mode == "live" else None,
        "per_document": config.per_document,
        "python": platform.python_version(), "machine": platform.machine(),
        "metrics": {
            "fact_precision": ratio(s("fact_tp"), s("fact_pred")),
            "fact_recall": ratio(s("fact_tp"), s("fact_gold")),
            "conflict_recall": ratio(s("conflict_tp"), s("conflict_gold")),
            "conflict_precision": ratio(s("conflict_tp"), s("conflict_pred")),
            "missing_question_recall": ratio(s("missing_q_tp"), s("missing_gold")),
            "fabricated_required_fields": sum(len(r["fabricated_required"]) for r in rows),
            "evidence_presence_accuracy": ratio(sum(r["evidence_presence_correct"] for r in rows), len(rows)),
            "unavailable_bundles": sum(r["component_status"] == "unavailable" for r in rows),
            "rejected_items": s("rejected"),
            "latency_s_p50": round(statistics.median(latencies), 4),
            "latency_s_max": round(max(latencies), 4),
        },
        "caveat": ("gold-echo는 채점기·경계 점검이며 모델 성능이 아니다." if args.mode == "gold-echo"
                   else "합성 개발세트 결과. 최종 성능·군 현장 성능으로 인용하지 않는다."),
        "bundles": rows,
    }
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
