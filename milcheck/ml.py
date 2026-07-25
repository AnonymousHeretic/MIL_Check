"""ML 추천 계층.

공개 계약 데이터 3.7만 건으로 학습한 두 개의 선형 분류기를 폐쇄망에서 실행하여
(1) 담당자가 제시한 수의계약 근거조항과 (2) 계약체결방법이 실제 관행과 어긋나는지
확률과 함께 제시한다.

이 계층은 규칙 엔진의 판정을 바꾸지 않는다. 규칙 엔진은 법령 조건을 검사하고,
ML 계층은 "같은 성격의 계약에서 통상 어떤 근거가 쓰였는가"라는 별개의 질문에 답한다.
두 결과가 어긋날 때 담당자에게 확인을 요구하는 것이 이 계층의 목적이다.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .linear_model import LinearTextClassifier, make_text

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# MIL-Check 내부 유형 <-> 공개 데이터 근거조항 키
TYPE_TO_ARTICLES = {
    "small_amount": {"령26-1-5가(1)", "령26-1-5가(2)", "령26-1-5가(3)",
                     "령26-1-5가(4)", "령26-1-5가(5)"},
    "sole_source": {"령26-1-2바", "령26-1-2사", "령26-1-2아", "령26-1-2자", "령26-1-2차"},
    "urgent_security": {"령26-1-1다"},
}
ARTICLE_TO_TYPE = {a: t for t, arts in TYPE_TO_ARTICLES.items() for a in arts}

# 규칙팩 범위 밖이지만 실무에서 자주 쓰이는 근거 (안내용)
OUT_OF_SCOPE_HINT = {
    "령27-1-1공": "공고 후 수의계약",
    "령27-1-2재": "재공고 후 수의계약",
    "령27-3기": "1인 입찰 후 수의계약",
    "령26-1-3바": "우수조달물품",
    "령26-1-4다": "중증장애인생산품",
    "령26-1-5마": "법령상 위탁·대행",
    "령26-1-5바": "국가기관·지자체 간 계약",
    "령26-1-5사": "혁신제품 구매",
}

SOLE_SOURCE_BASIS_TO_ARTICLE = {
    "original_supplier_direct_service": "령26-1-2바",
    "compatibility": "령26-1-2사",
    "patented_no_substitute": "령26-1-2아",
    "single_supplier": "령26-1-2자",
}
SMALL_AMOUNT_BASIS_TO_ARTICLE = {
    "general": "령26-1-5가(2)",
    "small_enterprise_or_small_business": "령26-1-5가(3)",
    "special_knowledge": "령26-1-5가(4)",
    "supported_enterprise": "령26-1-5가(5)",
    "youth_startup": "령26-1-5가(3)",
}

# 판정 임계값. 낮은 확신에서 경고를 남발하면 담당자가 시스템을 무시하게 되므로
# 상충 경고는 상위 예측이 충분히 우세할 때만 낸다.
CONFLICT_MIN_PROB = 0.55
CONFLICT_MIN_MARGIN = 0.20


class MLAdvisor:
    def __init__(self, article_model: str | Path | None = None,
                 method_model: str | Path | None = None):
        self.article = LinearTextClassifier.load(
            article_model or DATA_DIR / "model_article.json.gz")
        self.method = LinearTextClassifier.load(
            method_model or DATA_DIR / "model_method.json.gz")

    # ------------------------------------------------------------------
    @staticmethod
    def _declared_article(case: dict[str, Any]) -> str | None:
        t = case.get("proposed_type")
        if t == "small_amount":
            return SMALL_AMOUNT_BASIS_TO_ARTICLE.get(
                case.get("small_amount_basis", "general"))
        if t == "sole_source":
            return SOLE_SOURCE_BASIS_TO_ARTICLE.get(case.get("sole_source_basis", ""))
        if t == "urgent_security":
            return "령26-1-1다"
        return None

    def advise(self, case: dict[str, Any], top_k: int = 3) -> dict[str, Any]:
        text = make_text(
            case.get("item_name", ""),
            "물품" if case.get("contract_category") == "goods" else "용역",
            float(case.get("estimated_price_krw_ex_vat") or 0),
        )
        art_ranked = self.article.predict_proba(text)
        met_ranked = self.method.predict_proba(text)

        declared = self._declared_article(case)
        art_map = dict(art_ranked)
        signals: list[dict[str, str]] = []

        top_art, top_p = art_ranked[0]
        second_p = art_ranked[1][1] if len(art_ranked) > 1 else 0.0
        confident = top_p >= CONFLICT_MIN_PROB and (top_p - second_p) >= CONFLICT_MIN_MARGIN

        if declared and confident and top_art != declared:
            declared_rank = [a for a, _ in art_ranked].index(declared) + 1 \
                if declared in art_map else None
            if declared_rank is None or declared_rank > 3:
                signals.append({
                    "code": "ML-ARTICLE-CONFLICT",
                    "severity": "warning",
                    "message": (
                        f"유사한 계약에서는 「{self.article.class_labels.get(top_art, top_art)}」가 "
                        f"주로 사용되었습니다(확률 {top_p:.0%}). 제시하신 근거와 다르므로 "
                        f"근거 선택 사유를 검토하십시오."
                    ),
                })
            else:
                signals.append({
                    "code": "ML-ARTICLE-ALTERNATIVE",
                    "severity": "info",
                    "message": (
                        f"제시 근거는 {declared_rank}순위 후보입니다. 1순위는 "
                        f"「{self.article.class_labels.get(top_art, top_art)}」"
                        f"(확률 {top_p:.0%})입니다."
                    ),
                })

        if top_art in OUT_OF_SCOPE_HINT and confident and declared:
            signals.append({
                "code": "ML-SCOPE-HINT",
                "severity": "info",
                "message": (
                    f"유사 계약에서는 {OUT_OF_SCOPE_HINT[top_art]} 절차가 사용되기도 합니다. "
                    f"현재 규칙팩 범위 밖이므로 해당 절차 적용 시 별도 검토가 필요합니다."
                ),
            })

        top_method, method_p = met_ranked[0]
        if top_method != "수의계약" and method_p >= CONFLICT_MIN_PROB:
            signals.append({
                "code": "ML-METHOD-CONFLICT",
                "severity": "warning",
                "message": (
                    f"유사한 품명·금액대의 계약은 「{top_method}」으로 체결된 경우가 많습니다"
                    f"(확률 {method_p:.0%}). 경쟁 가능성을 재검토하십시오."
                ),
            })

        return {
            "input_text": text,
            "article_top_k": [
                {"article": a, "label": self.article.class_labels.get(a, a),
                 "probability": round(p, 4)}
                for a, p in art_ranked[:top_k]
            ],
            "method_top_k": [
                {"method": m, "probability": round(p, 4)} for m, p in met_ranked[:top_k]
            ],
            "declared_article": declared,
            "declared_article_probability": round(art_map.get(declared, 0.0), 4)
            if declared else None,
            "confident": confident,
            "signals": signals,
            "model_meta": {
                "article": self.article.meta.get("metrics", {}),
                "method": self.method.meta.get("metrics", {}),
            },
        }
