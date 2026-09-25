"""근거 검색 기준선 재측정 (기존 30질의, 회귀용).

사용: python scripts/eval_retrieval_report.py --output <결과.json>

기존 evaluation.eval_retrieval와 같은 자료·검색기를 쓰되 다음을 추가한다.
- Recall@1/3/5, MRR@5와 Wilson 95% 신뢰구간
- 무작위 순위 기댓값(말뭉치 크기 대비 기준)
- 모드별 실패 질의 목록, 정답 문서 분포
표준 라이브러리만 사용한다.
"""
from __future__ import annotations

import argparse
import json
import math
import platform
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from milcheck.retriever import HybridRetriever  # noqa: E402


def wilson(k: int, n: int, z: float = 1.96) -> list[float] | None:
    if not n:
        return None
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


def random_recall_at_k(n_docs: int, n_gold: int, k: int) -> float:
    """정답 n_gold개 중 하나 이상이 무작위 top-k에 들어갈 확률."""
    if k >= n_docs:
        return 1.0
    miss = math.comb(n_docs - n_gold, k) / math.comb(n_docs, k)
    return 1 - miss


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    retriever = HybridRetriever.from_jsonl(ROOT / "data/corpus.jsonl")
    cases = [json.loads(line) for line in (ROOT / "eval/retrieval_cases.jsonl").read_text(
        encoding="utf-8").splitlines() if line.strip()]
    n_docs = len(retriever.documents)
    n = len(cases)

    rand = {k: round(sum(random_recall_at_k(n_docs, len(c["gold"]), k) for c in cases) / n, 4)
            for k in (1, 3, 5)}
    by_mode = {}
    for mode in ("ngram", "bm25", "hybrid"):
        hits_at = {1: 0, 3: 0, 5: 0}
        mrr = 0.0
        failures = []
        for c in cases:
            ids = [h.document["id"] for h in retriever.search(c["query"], top_k=5, mode=mode)]
            gold = set(c["gold"])
            rank = next((i + 1 for i, d in enumerate(ids) if d in gold), 0)
            for k in hits_at:
                hits_at[k] += bool(rank and rank <= k)
            mrr += 1 / rank if rank else 0.0
            if rank != 1:
                failures.append({"qid": c["qid"], "gold": sorted(gold), "top5": ids, "rank": rank or None})
        by_mode[mode] = {
            **{f"recall_at_{k}": {"hits": v, "n": n, "value": round(v / n, 4), "wilson95": wilson(v, n)}
               for k, v in hits_at.items()},
            "mrr_at_5": round(mrr / n, 4),
            "not_rank1": failures,
        }

    report = {
        "run_date": "2026-09-25",
        "python": platform.python_version(),
        "corpus_documents": n_docs,
        "queries": n,
        "gold_per_query": dict(Counter(len(c["gold"]) for c in cases)),
        "gold_document_distribution": dict(Counter(g for c in cases for g in c["gold"]).most_common()),
        "random_baseline_recall": {f"at_{k}": v for k, v in rand.items()},
        "production_default_mode": "ngram (agent.py retrieval_mode)",
        "by_mode": by_mode,
        "caveats": [
            "말뭉치가 17문서로 작아 Recall@3의 무작위 기댓값이 이미 20% 안팎이다.",
            "30질의는 반복 사용된 회귀 자료다. 일반화 성능·신규 LLM 성능으로 인용하지 않는다.",
            "법령 원문 버전·조항 단위 정확성은 측정하지 않는다(문서 단위 적중만).",
        ],
    }
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {m: {k: (v["value"] if isinstance(v, dict) else v) for k, v in d.items() if k != "not_rank1"}
               for m, d in by_mode.items()}
    print(json.dumps({"random": rand, "by_mode": summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
