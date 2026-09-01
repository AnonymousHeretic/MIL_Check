"""폐쇄망 추론 엔진: 표준 라이브러리만으로 TF-IDF + 다항 로지스틱 회귀를 실행한다.

학습은 공개망(scikit-learn)에서 수행하고 결과를 JSON 가중치로 내보낸 뒤,
운영 환경에서는 외부 패키지 없이 이 모듈만으로 추론한다.
"""
from __future__ import annotations

import gzip
import json
import math
import re
import unicodedata
from collections import Counter
from pathlib import Path


def normalize_name(s: str) -> str:
    """학습 시점과 동일한 정규화."""
    s = unicodedata.normalize("NFKC", str(s or ""))
    s = s.lower()
    s = re.sub(r"['\"`’‘“”]", "", s)
    s = re.sub(r"\b(19|20)?\d{2}\s*[-년]", " ", s)
    s = re.sub(r"제?\s*\d+\s*(차|회|호|분기)", " ", s)
    s = re.sub(r"\d+", "0", s)
    s = re.sub(r"[^\w가-힣]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


PRICE_BINS = [2e6, 1e7, 2e7, 5e7, 1e8, 5e8, 1e9]


def price_bucket(est_price: float) -> str:
    for i, edge in enumerate(PRICE_BINS):
        if (est_price or 0) <= edge:
            return f"b{i}"
    return f"b{len(PRICE_BINS)}"


def make_text(item_name: str, category: str, est_price: float) -> str:
    return f"{normalize_name(item_name)} ⟪{category or ''}⟫ ⟪{price_bucket(est_price)}⟫"


_WORD_RE = re.compile(r"(?u)\b\w\w+\b")


def _word_ngrams(text: str, lo: int, hi: int) -> list[str]:
    tokens = _WORD_RE.findall(text)
    out = list(tokens) if lo <= 1 else []
    n = len(tokens)
    for size in range(max(lo, 2), hi + 1):
        for i in range(n - size + 1):
            out.append(" ".join(tokens[i:i + size]))
    return out


def _char_wb_ngrams(text: str, lo: int, hi: int) -> list[str]:
    """scikit-learn char_wb 재현: 단어 경계 안에서만, 양끝 공백 패딩."""
    text = re.sub(r"\s\s+", " ", text)
    out: list[str] = []
    for w in text.split(" "):
        w = " " + w + " "
        wl = len(w)
        for size in range(lo, hi + 1):
            if wl < size:
                out.append(w)
                break
            for i in range(wl - size + 1):
                out.append(w[i:i + size])
    return out


class _Block:
    __slots__ = ("name", "analyzer", "lo", "hi", "vocab", "idf", "offset")

    def __init__(self, spec: dict):
        self.name = spec["name"]
        self.analyzer = spec["analyzer"]
        self.lo, self.hi = spec["ngram_range"]
        self.vocab: dict[str, int] = spec["vocab"]
        self.idf: list[float] = spec["idf"]
        self.offset: int = spec["offset"]

    def features(self, text: str) -> dict[int, float]:
        grams = (_char_wb_ngrams(text, self.lo, self.hi) if self.analyzer == "char_wb"
                 else _word_ngrams(text, self.lo, self.hi))
        counts = Counter(g for g in grams if g in self.vocab)
        vec: dict[int, float] = {}
        for g, c in counts.items():
            gi = self.vocab[g]
            vec[gi] = (1.0 + math.log(c)) * self.idf[gi - self.offset]
        norm = math.sqrt(sum(v * v for v in vec.values()))
        if norm > 0:
            for k in vec:
                vec[k] /= norm
        return vec


class LinearTextClassifier:
    """TF-IDF(FeatureUnion) + 다항 로지스틱 회귀의 폐쇄망 추론기."""

    def __init__(self, payload: dict):
        self.classes: list[str] = payload["classes"]
        self.class_labels: dict[str, str] = payload.get("class_labels", {})
        self.intercept: list[float] = payload["intercept"]
        self.meta: dict = payload.get("meta", {})
        self.blocks = [_Block(b) for b in payload["blocks"]]
        self.by_feature: dict[int, list[tuple[int, float]]] = {}
        for ci, row in enumerate(payload["weights"]):
            for fi, w in zip(row["i"], row["w"]):
                self.by_feature.setdefault(fi, []).append((ci, w))

    @classmethod
    def load(cls, path: str | Path) -> "LinearTextClassifier":
        p = Path(path)
        opener = gzip.open if p.suffix == ".gz" else open
        with opener(p, "rt", encoding="utf-8") as fh:
            return cls(json.load(fh))

    def predict_proba(self, text: str) -> list[tuple[str, float]]:
        vec: dict[int, float] = {}
        for blk in self.blocks:
            vec.update(blk.features(text))
        scores = list(self.intercept)
        for fi, val in vec.items():
            for ci, w in self.by_feature.get(fi, ()):
                scores[ci] += w * val
        m = max(scores)
        exps = [math.exp(s - m) for s in scores]
        total = sum(exps)
        pairs = list(zip(self.classes, (e / total for e in exps)))
        pairs.sort(key=lambda t: -t[1])
        return pairs

    def top_k(self, text: str, k: int = 3) -> list[dict]:
        return [
            {"class": c, "probability": round(p, 4), "label": self.class_labels.get(c, c)}
            for c, p in self.predict_proba(text)[:k]
        ]


def with_eulro(word: str) -> str:
    """받침 유무에 맞는 '으로/로' 조사를 붙인다. ('제한경쟁로' 같은 표시 오류 방지)"""
    if not word:
        return word
    last = word[-1]
    if "가" <= last <= "힣":
        jong = (ord(last) - 0xAC00) % 28
        return word + ("로" if jong in (0, 8) else "으로")
    return word + "으로"
