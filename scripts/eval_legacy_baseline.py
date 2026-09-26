"""예선 규칙 기반 추출기(extract.py) vs LLM 문서 이해 — 공통 필드 비교.

사용:
    python scripts/eval_legacy_baseline.py --data <jsonl[,jsonl...]> [--exclude XT-57] \
        [--llm-result live-perdoc.json] --output out.json

비교 대상 필드(두 추출기가 모두 낼 수 있는 것, 구매요구서 문서 기준 정답):
  item_name / estimated_price_krw / sole_source_basis / quote_count
규칙 추출기는 요청서 1장의 텍스트만 입력받는다(원래 설계). 추정가격은 부가세 포함 금액을
부가세 제외로 환산해 내므로, 원문 금액 기준(raw)과 환산 허용(vat_adjusted) 두 방식으로 채점한다.
규칙 추출기가 구조적으로 낼 수 없는 기능(업체명·부가세 여부·충돌·누락 질문·증빙 대조)은
'미지원'으로 따로 표시한다. 출력은 집계만(문서 본문·값 미출력).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from milcheck.extract import extract  # noqa: E402

FIELDS = ("item_name", "estimated_price_krw", "sole_source_basis", "quote_count")
UNSUPPORTED = ("supplier_name", "tax_status", "total_amount_krw", "unit_price_krw", "quantity",
               "conflict_detection", "missing_questions", "evidence_presence")


def wilson(k, n, z=1.96):
    if not n:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(c - h, 4), round(c + h, 4)]


def ratio(k, n):
    return {"num": k, "den": n, "value": round(k / n, 4) if n else None, "wilson95": wilson(k, n)}


def gold_request(bundle):
    req_ids = {d["document_id"] for d in bundle["documents"] if d["doc_type"] == "request"}
    out = {}
    for f in bundle["gold"]["facts"]:
        if f["field"] in FIELDS and f["document_id"] in req_ids \
                and f["field"] not in bundle["gold"].get("ambiguous_fields", []):
            out.setdefault(f["field"], set()).add(json.dumps(f["value"], ensure_ascii=False))
    tax = {f["value"] for f in bundle["gold"]["facts"]
           if f["field"] == "tax_status" and f["document_id"] in req_ids}
    return out, tax


def legacy_predict(bundle):
    text = "\n".join(d["text"] for d in bundle["documents"] if d["doc_type"] == "request")
    if not text:
        return {}
    got = extract(text)["fields"]
    pred = {}
    if got.get("item_name"):
        pred["item_name"] = got["item_name"]
    if got.get("estimated_price_krw_ex_vat"):
        pred["estimated_price_krw"] = got["estimated_price_krw_ex_vat"]
    if got.get("sole_source_basis"):
        pred["sole_source_basis"] = got["sole_source_basis"]
    if got.get("quote_count_planned"):
        pred["quote_count"] = got["quote_count_planned"]
    return pred


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--exclude", default="")
    ap.add_argument("--llm-result")
    ap.add_argument("--output", required=True)
    a = ap.parse_args()
    excluded = {x.strip() for x in a.exclude.split(",") if x.strip()}
    bundles = [json.loads(l) for p in a.data.split(",") for l in Path(p).read_text(encoding="utf-8").splitlines()
               if l.strip()]
    bundles = [b for b in bundles if b["bundle_id"] not in excluded]

    llm = None
    if a.llm_result:
        llm = {b["bundle_id"]: b for b in json.loads(Path(a.llm_result).read_text(encoding="utf-8"))["bundles"]}

    stats = {f: {"gold": 0, "legacy_tp": 0, "legacy_pred": 0, "legacy_tp_vat_adj": 0, "llm_tp": 0} for f in FIELDS}
    for b in bundles:
        gold, tax = gold_request(b)
        pred = legacy_predict(b)
        for f in FIELDS:
            g = gold.get(f, set())
            stats[f]["gold"] += len(g)
            if f in pred:
                stats[f]["legacy_pred"] += 1
                v = json.dumps(pred[f], ensure_ascii=False)
                hit = v in g
                stats[f]["legacy_tp"] += hit
                adj = hit
                if f == "estimated_price_krw" and not hit and "vat_included" in tax:
                    adj = any(abs(round(int(x) / 1.1) - pred[f]) <= 1 for x in g)
                stats[f]["legacy_tp_vat_adj"] += adj
            if llm and b["bundle_id"] in llm:
                missed = {(m[0], m[1]) for m in llm[b["bundle_id"]]["missed_facts"]}
                stats[f]["llm_tp"] += sum((f, v) not in missed for v in g)

    report = {"bundles": len(bundles), "excluded": sorted(excluded), "per_field": {}, "legacy_unsupported": UNSUPPORTED}
    tot = {"gold": 0, "legacy_tp": 0, "legacy_tp_vat_adj": 0, "llm_tp": 0}
    for f, s in stats.items():
        row = {"legacy_recall": ratio(s["legacy_tp"], s["gold"]),
               "legacy_recall_vat_adjusted": ratio(s["legacy_tp_vat_adj"], s["gold"]),
               "legacy_precision": ratio(s["legacy_tp"], s["legacy_pred"])}
        if llm:
            row["llm_recall"] = ratio(s["llm_tp"], s["gold"])
        report["per_field"][f] = row
        for k in tot:
            tot[k] += s[k]
    report["overall"] = {"legacy_recall": ratio(tot["legacy_tp"], tot["gold"]),
                         "legacy_recall_vat_adjusted": ratio(tot["legacy_tp_vat_adj"], tot["gold"])}
    if llm:
        report["overall"]["llm_recall"] = ratio(tot["llm_tp"], tot["gold"])
    report["note"] = ("LLM 재현율은 같은 정답(요청서 기준) 중 LLM 결과에서 놓치지 않은 비율. "
                      "LLM 정밀도는 필드 전체 평가(eval_understanding)를 따른다.")
    Path(a.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    brief = {f: {k: v["value"] for k, v in r.items() if isinstance(v, dict)} for f, r in report["per_field"].items()}
    print(json.dumps({"bundles": len(bundles), "per_field": brief,
                      "overall": {k: v["value"] for k, v in report["overall"].items()}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
