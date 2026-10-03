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
import hashlib
import json
import math
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from milcheck.understanding import (  # noqa: E402
    FIELD_SPECS, REQUIRED_FIELDS, SCHEMA_VERSION, Document, DocumentUnderstanding, EndpointConfig)


EVALUATOR_VERSION = "understanding-eval-0.2.0"
# workflow.confirm -> RuleEngine에서 읽는 AI 후보 및 금액 사용을 허용하는 tax_status.
# supplier_name은 결과서 맥락, contract_category/proposed_type/증빙은 담당자 입력이다.
DECISION_INPUT_FIELDS = ("item_name", "estimated_price_krw", "tax_status",
                         "sole_source_basis", "quote_count")


def pair_counts(gold: set, pred: set) -> dict:
    return {"tp": len(gold & pred), "pred": len(pred), "gold": len(gold),
            "fp": len(pred - gold), "fn": len(gold - pred)}


def score_input_fields(bundle: dict, result) -> dict:
    """확인 전 후보 평가. 법적 판정·담당자 확인·금액/세금 관계 검증이 아니다."""
    skip = set(bundle["gold"].get("ambiguous_fields", []))
    gold = {(f["field"], json.dumps(f["value"], ensure_ascii=False))
            for f in bundle["gold"]["facts"] if f["field"] not in skip}
    pred = {(c.field, json.dumps(c.value, ensure_ascii=False))
            for c in result.candidates if c.field not in skip}
    fields = sorted(set(FIELD_SPECS) | {f for f, _ in gold | pred})
    per_field = {f: pair_counts({p for p in gold if p[0] == f},
                                {p for p in pred if p[0] == f}) for f in fields}
    selected_gold = {p for p in gold if p[0] in DECISION_INPUT_FIELDS}
    selected_pred = {p for p in pred if p[0] in DECISION_INPUT_FIELDS}

    # 같은 값이 다른 문서에 등장한 경우 값 일치만으로 출처까지 맞다고 하지 않는다.
    # source metric 단위는 (필드, 값, 문서 ID), 중복 인용은 1개로 센다.
    source_gold = {(f["field"], json.dumps(f["value"], ensure_ascii=False), f["document_id"])
                   for f in bundle["gold"]["facts"]
                   if f["field"] in DECISION_INPUT_FIELDS and f["field"] not in skip}
    source_pred = {(c.field, json.dumps(c.value, ensure_ascii=False), ref.document_id)
                   for c in result.candidates
                   if c.field in DECISION_INPUT_FIELDS and c.field not in skip
                   for ref in c.source_refs}
    # 출처가 없는 후보도 분모에서 사라지지 않게 한다(정상 검수 결과에는 없어야 함).
    source_pred |= {(c.field, json.dumps(c.value, ensure_ascii=False), None)
                    for c in result.candidates if not c.source_refs
                    and c.field in DECISION_INPUT_FIELDS and c.field not in skip}
    return {"per_field": per_field,
            "decision_input": pair_counts(selected_gold, selected_pred),
            "decision_input_source": pair_counts(source_gold, source_pred),
            "excluded_ambiguous_fields": sorted(skip)}


def aggregate_input_fields(rows: list[dict]) -> dict:
    def summarize(counts):
        totals = {k: sum(c[k] for c in counts) for k in ("tp", "pred", "gold", "fp", "fn")}
        return {**totals, "precision": ratio(totals["tp"], totals["pred"]),
                "recall": ratio(totals["tp"], totals["gold"])}

    fields = sorted({f for r in rows for f in r["input_fields"]["per_field"]})
    empty = dict.fromkeys(("tp", "pred", "gold", "fp", "fn"), 0)
    return {
        "decision_input": summarize([r["input_fields"]["decision_input"] for r in rows]),
        "decision_input_source": summarize([r["input_fields"]["decision_input_source"] for r in rows]),
        "per_field": {f: summarize([r["input_fields"]["per_field"].get(f, empty) for r in rows])
                      for f in fields},
        "ambiguous_exclusions": {f: sum(f in r["input_fields"]["excluded_ambiguous_fields"] for r in rows)
                                 for f in fields},
    }


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
        "input_fields": score_input_fields(bundle, result),
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
    ci = None
    if d:
        p, z = n / d, 1.959963984540054
        scale = 1 + z * z / d
        center = (p + z * z / (2 * d)) / scale
        width = z * math.sqrt(p * (1 - p) / d + z * z / (4 * d * d)) / scale
        ci = [round(max(0, center - width), 4), round(min(1, center + width), 4)]
    return {"num": n, "den": d, "value": round(n / d, 4) if d else None,
            "wilson_95_ci": ci}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["gold-echo", "live"], required=True)
    ap.add_argument("--data", default=str(ROOT / "eval/understanding_dev.jsonl"))
    ap.add_argument("--output", required=True)
    ap.add_argument("--exclude", default="", help="쉼표로 구분한 제외 bundle_id(사전 등록된 것만)")
    ap.add_argument("--summary-only", action="store_true",
                    help="사례별 정답·오답·거부 상세를 저장하지 않고 집계만 저장")
    args = ap.parse_args()

    paths = [Path(x) for x in args.data.split(",")]
    excluded = {x.strip() for x in args.exclude.split(",") if x.strip()}
    bundles = [b for p in paths for b in load(p) if b["bundle_id"] not in excluded]
    if not bundles:
        ap.error("평가할 문서 묶음이 없습니다")
    ids = [b["bundle_id"] for b in bundles]
    if len(ids) != len(set(ids)):
        ap.error("bundle_id 중복: 같은 사례를 두 번 집계할 수 없습니다")
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
    input_metrics = aggregate_input_fields(rows)
    report = {
        "evaluator_version": EVALUATOR_VERSION,
        "evaluator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "decision_input_profile": {
            "fields": list(DECISION_INPUT_FIELDS),
            "stage": "validated_candidates_before_human_confirmation",
            "source_unit": "field_value_document_id",
            "caveat": "판정 입력 후보의 추출 정확도. 담당자 확정값·금액과 세금의 대응 관계·법적 판정 정확도는 별도 검증 대상.",
        },
        "dataset_sha256": [{"name": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                           for p in paths],
        "mode": args.mode, "dataset": [p.name for p in paths], "excluded": sorted(excluded), "split": sorted({b.get("split", "dev") for b in bundles}),
        "n_bundles": len(rows), "code_commit": commit, "model": config.model if args.mode == "live" else None,
        "per_document": config.per_document,
        "structured": config.structured,
        "python": platform.python_version(), "machine": platform.machine(),
        "metrics": {
            "fact_precision": ratio(s("fact_tp"), s("fact_pred")),
            "fact_recall": ratio(s("fact_tp"), s("fact_gold")),
            "decision_input_precision": input_metrics["decision_input"]["precision"],
            "decision_input_recall": input_metrics["decision_input"]["recall"],
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
                   else "개발세트는 개발 성능만 보고한다. 시험세트의 독립성은 사용 이력으로 별도 확인하며 합성 자료 결과를 군 현장 성능으로 일반화하지 않는다."),
        "input_field_metrics": input_metrics,
        "detail_policy": "aggregate_only" if args.summary_only else "per_bundle_errors",
    }
    if not args.summary_only:
        report["bundles"] = rows
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
