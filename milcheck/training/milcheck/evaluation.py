"""3층 평가 스위트.

L1 규칙 회귀 (자체 라벨)      : 규칙이 설계 의도대로 동작하는가
L2 실데이터 (외부 라벨)        : ML 계층이 실제 계약의 근거조항·계약방법을 맞추는가
L3 감사사례 재현 (외부 라벨)   : 공개 감사에서 지적된 유형을 잡아내는가
R  근거검색 (외부 라벨)        : 실무 표현으로 물었을 때 맞는 조문을 찾는가
E  필드추출 (외부 라벨)        : 자유서술에서 필드를 정확히 뽑는가

L1 결과만으로 성능을 주장하지 않는다.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = ROOT / "eval"


def _read(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


# --------------------------------------------------------------------------
def eval_rules(agent, path: Path | None = None) -> dict[str, Any]:
    cases = _read(path or EVAL_DIR / "cases.jsonl")
    confusion: Counter = Counter()
    failures = []
    for case in cases:
        case = dict(case)
        expected = case.pop("expected_decision")
        actual = agent.rule_engine.evaluate(case).decision
        confusion[(expected, actual)] += 1
        if actual != expected:
            failures.append({"case_id": case.get("case_id"),
                             "expected": expected, "actual": actual})
    total = len(cases)
    correct = sum(c for (e, a), c in confusion.items() if e == a)

    # 잘못된 통과: 부적합해야 하는데 통과시킨 경우 (가장 위험한 오류)
    false_pass = sum(
        c for (e, a), c in confusion.items()
        if e in {"REJECT_GROUND", "NEEDS_EVIDENCE"} and a == "PASS_WITH_CONTROLS")
    n_should_flag = sum(c for (e, _), c in confusion.items()
                        if e in {"REJECT_GROUND", "NEEDS_EVIDENCE"})
    return {
        "layer": "L1 규칙 회귀 (자체 라벨)",
        "total": total, "correct": correct,
        "accuracy": round(correct / total, 4) if total else 0.0,
        "false_pass": false_pass,
        "false_pass_rate": round(false_pass / n_should_flag, 4) if n_should_flag else 0.0,
        "failures": failures,
        "caveat": "라벨을 규칙 설계자가 작성했으므로 실제 정확도가 아니라 회귀 무결성 지표다.",
    }


# --------------------------------------------------------------------------
def eval_real(advisor, path: Path | None = None) -> dict[str, Any]:
    cases = _read(path or EVAL_DIR / "real_cases.jsonl")
    from .linear_model import make_text

    a1 = a2 = a3 = 0
    m1 = m2 = 0
    for c in cases:
        text = make_text(c["item_name"],
                         "물품" if c["contract_category"] == "goods" else "용역",
                         c["estimated_price_krw_ex_vat"])
        arts = [a for a, _ in advisor.article.predict_proba(text)[:3]]
        gold = c["gold_article"]
        a1 += arts[0] == gold
        a2 += gold in arts[:2]
        a3 += gold in arts

        mets = [m for m, _ in advisor.method.predict_proba(text)[:2]]
        gm = {"협상에의한계약(전자)": "협상에의한계약",
              "협상에의한계약(서류)": "협상에의한계약",
              "2단계경쟁(동시)": "2단계경쟁",
              "2단계경쟁(분리)": "2단계경쟁"}.get(c["gold_method"], c["gold_method"])
        m1 += mets[0] == gm
        m2 += gm in mets
    n = len(cases)
    return {
        "layer": "L2 실데이터 (외부 라벨: 방위사업청 공개계약)",
        "total": n,
        "article_top1": round(a1 / n, 4), "article_top2": round(a2 / n, 4),
        "article_top3": round(a3 / n, 4),
        "method_top1": round(m1 / n, 4), "method_top2": round(m2 / n, 4),
        "note": "정답은 계약서에 기재된 실제 근거조항·계약방법이며 MIL-Check가 만든 라벨이 아니다.",
    }


# --------------------------------------------------------------------------
def eval_audit(agent, path: Path | None = None) -> dict[str, Any]:
    cases = _read(path or EVAL_DIR / "audit_cases.jsonl")
    detected, rows = 0, []
    for c in cases:
        case = {k: v for k, v in c.items()
                if k not in {"expected_flag", "expected_decision", "audit_finding",
                             "origin", "note"}}
        result = agent.rule_engine.evaluate(case)
        codes = {f["code"] for f in result.findings
                 if f["severity"] in {"critical", "warning"}}
        missing = {f"MISSING:{m}" for m in result.missing_evidence}
        want = c["expected_flag"]
        hit = want in codes or want in missing
        detected += hit
        rows.append({"case_id": c["case_id"], "audit_finding": c["audit_finding"],
                     "expected_flag": want, "detected": bool(hit),
                     "decision": result.decision,
                     "expected_decision": c["expected_decision"],
                     "decision_match": result.decision == c["expected_decision"],
                     "note": c.get("note", "")})
    n = len(cases)
    return {
        "layer": "L3 감사사례 재현 (외부 라벨: 공개 감사보고서)",
        "total": n, "detected": detected,
        "detection_rate": round(detected / n, 4) if n else 0.0,
        "decision_match_rate": round(
            sum(r["decision_match"] for r in rows) / n, 4) if n else 0.0,
        "details": rows,
        "caveat": "공개 보고서에 없는 세부값은 지적 유형이 유지되는 범위에서 대표값으로 "
                  "대체했다. 재현율은 지적 유형 탐지 여부를 뜻한다.",
    }


# --------------------------------------------------------------------------
def eval_retrieval(retriever, path: Path | None = None,
                   modes: tuple[str, ...] = ("bm25", "ngram", "hybrid")) -> dict[str, Any]:
    cases = _read(path or EVAL_DIR / "retrieval_cases.jsonl")
    out = {}
    for mode in modes:
        r1 = r3 = mrr = 0.0
        for c in cases:
            hits = [h.document["id"] for h in retriever.search(c["query"], top_k=3,
                                                               mode=mode)]
            gold = set(c["gold"])
            if hits and hits[0] in gold:
                r1 += 1
            if any(h in gold for h in hits):
                r3 += 1
            rank = next((i + 1 for i, h in enumerate(hits) if h in gold), 0)
            mrr += 1 / rank if rank else 0
        n = len(cases)
        out[mode] = {"recall_at_1": round(r1 / n, 4), "recall_at_3": round(r3 / n, 4),
                     "mrr": round(mrr / n, 4)}
    return {"layer": "R 근거검색 (외부 라벨: 실무 표현 질의)",
            "total": len(cases), "by_mode": out}


# --------------------------------------------------------------------------
def eval_extraction(path: Path | None = None, label: str = "개발세트") -> dict[str, Any]:
    from .extract import extract
    cases = _read(path or EVAL_DIR / "extraction_cases.jsonl")
    per_field: dict[str, list[int]] = {}
    rows = []
    for c in cases:
        got = extract(c["text"])["fields"]
        detail = {}
        for field, gold in c["expected"].items():
            ok = got.get(field) == gold
            if field == "evidence":
                ok = set(got.get("evidence", [])) >= set(gold)
            per_field.setdefault(field, []).append(int(ok))
            detail[field] = {"expected": gold, "got": got.get(field), "ok": bool(ok)}
        rows.append({"case_id": c["case_id"],
                     "all_ok": all(d["ok"] for d in detail.values()),
                     "fields": detail})
    field_acc = {k: round(sum(v) / len(v), 4) for k, v in per_field.items()}
    n = len(cases)
    hit = sum(sum(v) for v in per_field.values())
    cnt = sum(len(v) for v in per_field.values())
    return {
        "layer": f"E 필드추출 ({label})",
        "total": n,
        "field_level_accuracy": round(hit / cnt, 4) if cnt else 0.0,
        "field_level_counts": {"correct": hit, "total": cnt},
        "exact_match_cases": sum(r["all_ok"] for r in rows),
        "exact_match_rate": round(sum(r["all_ok"] for r in rows) / n, 4) if n else 0.0,
        "field_accuracy": field_acc,
        "failures": [r for r in rows if not r["all_ok"]],
    }


def run_all(agent) -> dict[str, Any]:
    result = {
        "L1_rules": eval_rules(agent),
        "L3_audit": eval_audit(agent),
        "R_retrieval": eval_retrieval(agent.retriever),
        "E_extraction_dev": eval_extraction(EVAL_DIR / "extraction_cases.jsonl",
                                            "개발세트 — 규칙을 이 세트로 작성했으므로 상한값"),
        "E_extraction_holdout": eval_extraction(EVAL_DIR / "extraction_holdout.jsonl",
                                                "홀드아웃 — 튜닝하지 않음, 실제 성능 추정치"),
    }
    if getattr(agent, "advisor", None) is not None:
        result["L2_real_data"] = eval_real(agent.advisor)
    return result
