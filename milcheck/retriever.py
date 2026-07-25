"""법령·예규·감사사례 근거검색 (폐쇄망, 표준 라이브러리).

한국어 법령 질의는 어절 단위 BM25만으로는 곤란하다. "호환성이 없는" 과 "호환되지
않아" 처럼 어미가 달라지면 어휘가 어긋나기 때문이다. 그래서 두 검색기를 함께 쓴다.

  · BM25        : 어절 단위, 정확히 일치하는 법령 용어에 강하다
  · 문자 n-gram : 어미 변화와 띄어쓰기 차이에 강하다
  · RRF 융합    : 두 순위를 상호순위융합으로 합쳐 한쪽의 실패를 보완한다

임베딩 모델을 쓰지 않는 이유는 폐쇄망 반입 대상이 줄고 결과가 결정론적이며
동일 입력에 동일 근거를 재현할 수 있어 감사 추적에 유리하기 때문이다.
승인된 온프레미스 임베딩 모델이 있으면 세 번째 검색기로 추가할 수 있다.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

TOKEN_RE = re.compile(r"[가-힣A-Za-z0-9]+")


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in TOKEN_RE.findall(text or "")]


def char_ngrams(text: str, lo: int = 2, hi: int = 4) -> list[str]:
    text = re.sub(r"\s+", " ", (text or "").lower())
    out: list[str] = []
    for w in text.split(" "):
        if not w:
            continue
        w = f" {w} "
        for n in range(lo, hi + 1):
            if len(w) < n:
                out.append(w)
                break
            out.extend(w[i:i + n] for i in range(len(w) - n + 1))
    return out


@dataclass(frozen=True)
class SearchHit:
    score: float
    document: dict
    components: tuple = ()


def _doc_text(doc: dict) -> str:
    return " ".join([
        str(doc.get("title", "")), str(doc.get("text", "")),
        " ".join(doc.get("tags", [])),
    ])


class HybridRetriever:
    """BM25 + 문자 n-gram 코사인의 RRF 융합 검색기."""

    def __init__(self, documents: Iterable[dict], k1: float = 1.5, b: float = 0.75,
                 rrf_k: int = 60):
        self.documents = list(documents)
        self.k1, self.b, self.rrf_k = k1, b, rrf_k
        texts = [_doc_text(d) for d in self.documents]

        # --- BM25 ---
        self.tokens = [tokenize(t) for t in texts]
        self.term_freqs = [Counter(t) for t in self.tokens]
        self.lengths = [len(t) for t in self.tokens]
        self.avgdl = sum(self.lengths) / max(len(self.lengths), 1)
        self.document_frequency: Counter[str] = Counter()
        for toks in self.tokens:
            self.document_frequency.update(set(toks))

        # --- 문자 n-gram TF-IDF ---
        gram_docs = [Counter(char_ngrams(t)) for t in texts]
        gdf: Counter[str] = Counter()
        for g in gram_docs:
            gdf.update(g.keys())
        n = len(self.documents)
        self.gram_idf = {g: math.log((n + 1) / (c + 1)) + 1.0 for g, c in gdf.items()}
        self.gram_vectors: list[dict[str, float]] = []
        self.gram_postings: dict[str, list[int]] = defaultdict(list)
        for i, grams in enumerate(gram_docs):
            w = {g: (1 + math.log(c)) * self.gram_idf[g] for g, c in grams.items()}
            norm = math.sqrt(sum(v * v for v in w.values())) or 1.0
            vec = {g: v / norm for g, v in w.items()}
            self.gram_vectors.append(vec)
            for g in vec:
                self.gram_postings[g].append(i)

    @classmethod
    def from_jsonl(cls, path: str | Path) -> "HybridRetriever":
        docs = []
        with Path(path).open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    docs.append(json.loads(line))
        return cls(docs)

    # ------------------------------------------------------------------
    def _bm25_scores(self, query: str) -> dict[int, float]:
        terms = tokenize(query)
        total = len(self.documents)
        scores: dict[int, float] = {}
        for i in range(total):
            s = 0.0
            dl = self.lengths[i]
            freqs = self.term_freqs[i]
            for term in terms:
                tf = freqs.get(term, 0)
                if not tf:
                    continue
                df = self.document_frequency.get(term, 0)
                idf = math.log(1 + (total - df + 0.5) / (df + 0.5))
                denom = tf + self.k1 * (1 - self.b + self.b * dl / max(self.avgdl, 1))
                s += idf * (tf * (self.k1 + 1)) / denom
            if s > 0:
                scores[i] = s
        return scores

    def _gram_scores(self, query: str) -> dict[int, float]:
        grams = Counter(char_ngrams(query))
        w = {g: (1 + math.log(c)) * self.gram_idf.get(g, 0.0)
             for g, c in grams.items() if g in self.gram_idf}
        norm = math.sqrt(sum(v * v for v in w.values())) or 1.0
        qvec = {g: v / norm for g, v in w.items()}
        scores: dict[int, float] = defaultdict(float)
        for g, qw in qvec.items():
            for i in self.gram_postings.get(g, ()):
                scores[i] += qw * self.gram_vectors[i].get(g, 0.0)
        return dict(scores)

    @staticmethod
    def _ranks(scores: dict[int, float]) -> dict[int, int]:
        return {i: r for r, (i, _) in enumerate(
            sorted(scores.items(), key=lambda t: -t[1]), start=1)}

    def search(self, query: str, top_k: int = 5,
               mode: str = "hybrid") -> list[SearchHit]:
        if not (query or "").strip():
            return []
        bm = self._bm25_scores(query)
        gr = self._gram_scores(query)
        if mode == "bm25":
            fused = bm
        elif mode == "ngram":
            fused = gr
        else:
            rb, rg = self._ranks(bm), self._ranks(gr)
            fused = {}
            for i in set(rb) | set(rg):
                fused[i] = (1.0 / (self.rrf_k + rb.get(i, 10**6))
                            + 1.0 / (self.rrf_k + rg.get(i, 10**6)))
        ranked = sorted(fused.items(), key=lambda t: -t[1])[:top_k]
        return [
            SearchHit(score=round(s, 6), document=self.documents[i],
                      components=(round(bm.get(i, 0.0), 4), round(gr.get(i, 0.0), 4)))
            for i, s in ranked
        ]


# 하위 호환 별칭
BM25Retriever = HybridRetriever
