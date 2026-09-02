"""공개 계약 데이터 검색·탐지 계층 (폐쇄망, 표준 라이브러리).

방위사업청 국내조달 계약정보 3.7만 건을 반입용 인덱스로 축약해 탑재하고,
검토 중인 계약 1건에 대해 다음을 계산한다.

  1) 유사계약 검색 (문자 n-gram TF-IDF 코사인, 역색인 기반)
  2) 품목군 가격대 참조 (사분위)
  3) 분할발주 후보 탐지 (동일 부서·유사 품목·기간 내 합산이 상한 초과)
  4) 가격 이상 신호 (품목군 중앙값 대비 로버스트 z)

인덱스에는 업체명·담당자명·사업자등록번호·주소를 반입하지 않으며 부서명은
SHA-256 별칭으로 치환되어 있다.
"""
from __future__ import annotations

import gzip
import json
import math
import re
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from .linear_model import normalize_name, with_eulro

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

SMALL_AMOUNT_CAP = 20_000_000
SPLIT_WINDOW_DAYS = 60
SPLIT_SIMILARITY = 0.70

# 계약명에는 부서 필드와 별개로 부대 번호가 다시 포함되는 경우가 있다.
# 검색에는 정규화명(n)을 쓰고, 화면에는 아래 함수로 만든 표시용 이름만 내보낸다.
_UNIT_PATTERNS = (
    re.compile(
        r"(?:제\s*)?"
        r"\d+(?:(?:\s*[-~,/]\s*|\s+)\d+)*\s*"
        r"(?:군단|사단|여단|연대|대대|중대|전대|비행단|함대|전단|부대|"
        r"지원단|시설단|관리소|동지단|해감대대)"
    ),
    re.compile(r"(?<![0-9A-Za-z가-힣])[0-9A-Za-z가-힣-]+부대(?![0-9A-Za-z가-힣])"),
)
_PAREN_CONTRACT_CODE = re.compile(
    r"\(\s*\d+(?:\s*[-~]\s*\d+)*(?:차)?\s*\)"
)
_LEADING_CONTRACT_CODE = re.compile(
    r"^\s*['\"]?\d{2,4}(?:[-_/]\d+)+\(\d+\)\s*"
)


def display_contract_name(name: str) -> str:
    """계약명에서 부대 식별자와 괄호형 계약 차수를 제거한 표시용 이름."""
    masked = str(name or "")
    masked = _LEADING_CONTRACT_CODE.sub("", masked)
    for pattern in _UNIT_PATTERNS:
        masked = pattern.sub("[부대]", masked)
    masked = _PAREN_CONTRACT_CODE.sub("", masked)
    masked = re.sub(r"\s+", " ", masked).strip()
    return masked or "[계약명 비식별]"


def char_ngrams(text: str, lo: int = 3, hi: int = 4) -> list[str]:
    out: list[str] = []
    for w in text.split():
        w = f" {w} "
        for n in range(lo, hi + 1):
            if len(w) < n:
                out.append(w)
                break
            out.extend(w[i:i + n] for i in range(len(w) - n + 1))
    return out


def cluster_key(name_norm: str) -> str:
    toks = [t for t in name_norm.split() if len(t) >= 2][:2]
    return " ".join(toks)


def _month_diff(a: str, b: str) -> int:
    ya, ma = int(a[:4]), int(a[5:7])
    yb, mb = int(b[:4]), int(b[5:7])
    return abs((ya * 12 + ma) - (yb * 12 + mb))


def _parse_date(value: str | None) -> date | None:
    if not value or len(value) != 10:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


class ContractIndex:
    """역색인 기반 유사계약 검색기."""

    def __init__(self, records: list[dict], bands: dict[str, dict],
                 meta: dict | None = None):
        for rec in records:
            if "p" not in rec and rec.get("g") is not None:
                rec["p"] = int(rec["g"] / 1.1)
        self.records = records
        self.bands = bands
        self.meta = meta or {}
        self.postings: dict[str, list[int]] = defaultdict(list)
        self.norms: list[float] = []
        self.doc_grams: list[Counter] = []

        df: Counter[str] = Counter()
        grams_per_doc = []
        for rec in records:
            grams = Counter(char_ngrams(rec["n"]))
            grams_per_doc.append(grams)
            df.update(grams.keys())

        n_docs = len(records)
        self.idf = {g: math.log((n_docs + 1) / (c + 1)) + 1.0 for g, c in df.items()}
        for i, grams in enumerate(grams_per_doc):
            weights = {g: (1 + math.log(c)) * self.idf[g] for g, c in grams.items()}
            norm = math.sqrt(sum(v * v for v in weights.values())) or 1.0
            self.doc_grams.append(Counter({g: v / norm for g, v in weights.items()}))
            self.norms.append(norm)
            for g in weights:
                self.postings[g].append(i)

        self.by_dept: dict[str, list[int]] = defaultdict(list)
        for i, rec in enumerate(records):
            self.by_dept[rec["u"]].append(i)

    # ------------------------------------------------------------------
    @classmethod
    def load(cls, index_path: str | Path | None = None,
             bands_path: str | Path | None = None) -> "ContractIndex":
        """가격대는 검색 결과에서 즉석 계산하므로 사전계산 파일은 선택 사항이다."""
        index_path = Path(index_path or DATA_DIR / "contracts_index.jsonl.gz")
        records = []
        with gzip.open(index_path, "rt", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        bands: dict = {}
        bands_path = Path(bands_path) if bands_path else DATA_DIR / "price_bands.json.gz"
        if bands_path.exists():
            with gzip.open(bands_path, "rt", encoding="utf-8") as fh:
                bands = json.load(fh)
        meta_path = DATA_DIR / "index_meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        return cls(records, bands, meta)

    # ------------------------------------------------------------------
    def _query_vector(self, text: str) -> dict[str, float]:
        grams = Counter(char_ngrams(normalize_name(text)))
        weights = {g: (1 + math.log(c)) * self.idf.get(g, 0.0)
                   for g, c in grams.items() if g in self.idf}
        norm = math.sqrt(sum(v * v for v in weights.values())) or 1.0
        return {g: v / norm for g, v in weights.items()}

    def search(self, item_name: str, top_k: int = 5,
               candidates: list[int] | None = None) -> list[dict[str, Any]]:
        qvec = self._query_vector(item_name)
        if not qvec:
            return []
        scores: dict[int, float] = defaultdict(float)
        allow = set(candidates) if candidates is not None else None
        for g, qw in qvec.items():
            for i in self.postings.get(g, ()):
                if allow is None or i in allow:
                    scores[i] += qw * self.doc_grams[i].get(g, 0.0)
        ranked = sorted(scores.items(), key=lambda t: -t[1])[:top_k]
        out = []
        for i, s in ranked:
            r = self.records[i]
            out.append({
                "score": round(s, 4),
                # 새 인덱스는 x만 저장한다. 기존 인덱스도 원문을 그대로
                # 반환하지 않고 런타임에서 동일한 마스킹을 적용한다.
                "name": r.get("x") or display_contract_name(r.get("o", r["n"])),
                "est_price": r["p"],
                "method": r["m"], "article": r["a"], "category": r["c"],
                "month": r.get("d") or (r.get("t") or "")[:7],
                "department_alias": r["u"],
                "date": r.get("t"), "gross_amount": r.get("g"),
                "normalized_name": r["n"],
            })
        return out

    # ------------------------------------------------------------------
    def price_band(self, item_name: str, est_price: float | None = None,
                   min_similarity: float = 0.45, min_samples: int = 5,
                   pool: int = 60) -> dict[str, Any]:
        """검색으로 찾은 유사계약 집합에서 가격대를 계산한다.

        사전 정의된 품목 분류 체계가 공개 데이터에 없으므로, 고정 군집 대신
        유사도 임계값 이상의 검색 결과를 표본으로 삼는다.
        """
        hits = [h for h in self.search(item_name, top_k=pool)
                if h["score"] >= min_similarity]
        if len(hits) < min_samples:
            return {"available": False, "samples": len(hits),
                    "min_similarity": min_similarity,
                    "reason": f"유사도 {min_similarity} 이상 표본 {min_samples}건 미만"}

        prices = sorted(h["est_price"] for h in hits)

        def q(p: float) -> int:
            if not prices:
                return 0
            k = (len(prices) - 1) * p
            lo, hi = int(math.floor(k)), int(math.ceil(k))
            if lo == hi:
                return int(prices[lo])
            return int(prices[lo] + (prices[hi] - prices[lo]) * (k - lo))

        out = {
            "available": True, "samples": len(hits),
            "min_similarity": min_similarity,
            "q1": q(0.25), "med": q(0.5), "q3": q(0.75), "p95": q(0.95),
        }
        if est_price:
            med = out["med"] or 1
            out["ratio_to_median"] = round(est_price / med, 2)
            if est_price > out["p95"] and est_price > med * 2:
                out["signal"] = "유사계약 상위 5% 초과 — 규격·수량과 산출근거 확인 필요"
            elif est_price < out["q1"] * 0.5:
                out["signal"] = "유사계약 하위 사분위의 절반 미만 — 규격·수량 확인 필요"
            else:
                out["signal"] = "통상 가격대 이내"
        return out

    # ------------------------------------------------------------------
    def split_order_candidates(self, item_name: str, est_price: float,
                               department_alias: str | None = None,
                               contract_date: str | None = None,
                               month: str | None = None,
                               gross_amount: float | None = None) -> dict[str, Any]:
        """동일 부서 내 유사 품목의 최근 소액수의를 합산해 분할 위험을 계산한다."""
        if not department_alias or department_alias not in self.by_dept:
            return {"available": False,
                    "reason": "부서 별칭 미입력 — 실제 운영에서는 소속 부서 이력으로 계산"}
        pool = self.by_dept[department_alias]
        hits = self.search(item_name, top_k=50, candidates=pool)
        query_norm = normalize_name(item_name)
        current_date = _parse_date(contract_date)
        related = []
        for h in hits:
            if h["score"] < SPLIT_SIMILARITY:
                continue
            hit_date = _parse_date(h.get("date"))
            if current_date and hit_date:
                elapsed = (current_date - hit_date).days
                if elapsed < 0 or elapsed > SPLIT_WINDOW_DAYS:
                    continue
                # 공개 이력의 기존 계약을 다시 검토하는 재현 화면에서는
                # 현재 건 자체가 검색될 수 있으므로 정확히 같은 건은 제외한다.
                same_amount = (
                    gross_amount is not None
                    and h.get("gross_amount") is not None
                    and int(h["gross_amount"]) == int(gross_amount)
                )
                if elapsed == 0 and same_amount and h["normalized_name"] == query_norm:
                    continue
            elif month and _month_diff(h["month"], month) > 2:
                # 날짜 필드가 없는 구형 인덱스에 대한 호환 경로다.
                continue
            related.append(h)
        gross_values = [h.get("gross_amount") for h in related]
        if gross_amount is not None and all(v is not None for v in gross_values):
            total = int((int(gross_amount) + sum(int(v) for v in gross_values)) / 1.1)
        else:
            total = int(est_price + sum(h["est_price"] for h in related))
        current_row = {
            "score": 1.0, "name": display_contract_name(item_name),
            "est_price": int(est_price), "gross_amount": gross_amount,
            "method": "검토 중", "article": "", "category": "",
            "month": (contract_date or month or "")[:7],
            "date": contract_date, "department_alias": department_alias,
            "is_current": True,
        }
        contracts = sorted(
            [{**h, "is_current": False} for h in related] + [current_row],
            key=lambda h: h.get("date") or h.get("month") or "",
        )
        dated = [_parse_date(h.get("date")) for h in contracts]
        dated = [d for d in dated if d is not None]
        return {
            "available": True,
            "related_contracts": len(related),
            "contracts_including_current": len(contracts),
            "sum_with_current": int(total),
            "cap": SMALL_AMOUNT_CAP,
            "exceeds_cap": bool(total > SMALL_AMOUNT_CAP * 1.03),
            "window_days": SPLIT_WINDOW_DAYS,
            "similarity_threshold": SPLIT_SIMILARITY,
            "start_date": min(dated).isoformat() if dated else None,
            "end_date": max(dated).isoformat() if dated else None,
            "span_days": (max(dated) - min(dated)).days if dated else None,
            "examples": contracts,
        }

    # ------------------------------------------------------------------
    def analyze(self, case: dict[str, Any]) -> dict[str, Any]:
        name = case.get("item_name", "")
        price = float(case.get("estimated_price_krw_ex_vat") or 0)
        similar = self.search(name, top_k=5)
        band = self.price_band(name, price)
        split = self.split_order_candidates(
            name, price, case.get("department_alias"), case.get("contract_date"),
            case.get("contract_month"), case.get("contract_amount_krw_inc_vat"))

        signals: list[dict[str, str]] = []
        if band.get("available") and band.get("signal") and band["signal"] != "통상 가격대 이내":
            signals.append({
                "code": "DATA-PRICE-BAND", "severity": "info",
                "message": (f"유사계약 {band['samples']}건의 중앙값은 {band['med']:,}원이며 "
                            f"본 건은 중앙값의 {band.get('ratio_to_median')}배입니다. "
                            f"{band['signal']}"),
            })
        if split.get("exceeds_cap"):
            signals.append({
                "code": "DATA-SPLIT-RISK", "severity": "warning",
                "message": (f"최근 {SPLIT_WINDOW_DAYS}일 내 동일 부서의 과거 유사 계약 "
                            f"{split['related_contracts']}건을 현재 건과 합산하면 "
                            f"{split['sum_with_current']:,}원으로 소액수의 상한을 초과합니다. "
                            f"동일 사업 여부와 인위적 분할 여부를 확인하십시오."),
            })
        if similar:
            methods = Counter(h["method"] for h in similar)
            top_method, cnt = methods.most_common(1)[0]
            if top_method != "수의계약" and cnt >= 3:
                signals.append({
                    "code": "DATA-METHOD-PRECEDENT", "severity": "info",
                    "message": (f"유사계약 상위 5건 중 {cnt}건이 {with_eulro(top_method)} "
                                f"체결되었습니다. 경쟁 가능성을 검토하십시오."),
                })
        return {"similar_contracts": similar, "price_band": band,
                "split_analysis": split, "signals": signals,
                "index_meta": self.meta}
