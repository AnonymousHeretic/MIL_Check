"""MIL-Check 오케스트레이터.

계층별 권한을 분리한다.

  입력   자유서술 → 구조화 필드 추출 (규칙 + 선택적 내부 LLM)
  판정   규칙 엔진만이 상태(PASS/NEEDS/REJECT/OUT)를 결정한다
  자문   ML 추천·공개계약 검색은 신호와 확률만 제시하고 판정을 바꾸지 못한다
  근거   문자 n-gram 검색이 법령·감사사례 원문을 연결한다
  문서   내부 LLM은 확정된 결과를 문장으로 다듬기만 한다

이 분리가 MIL-Check의 핵심 설계다. 생성형 모델이 법적 판정을 하지 않으므로
동일 입력에 동일 판정이 나오고, 모든 판정에 규칙 ID와 조문이 남는다.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from .contracts import ContractIndex
from .extract import extract, extract_with_llm
from .llm import LocalLLMConfig, LocalOpenAICompatibleLLM
from .ml import MLAdvisor
from .retriever import HybridRetriever
from .rules import RuleEngine

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DECISION_KO = {
    "PASS_WITH_CONTROLS": "근거 성립 가능 — 통제조건 이행 필요",
    "NEEDS_EVIDENCE": "보완 필요 — 증빙 또는 절차 누락",
    "REJECT_GROUND": "제안 사유 부적합 — 경쟁절차 또는 다른 근거 검토",
    "OUT_OF_SCOPE": "현재 판정 범위 밖 — 전문 검토 필요",
    "NEEDS_INPUT": "입력 부족 — 추가 정보 필요",
}


class MilCheckAgent:
    def __init__(
        self,
        rules_path: str | Path | None = None,
        corpus_path: str | Path | None = None,
        llm_mode: str = "none",
        use_ml: bool = True,
        use_contract_index: bool = True,
        retrieval_mode: str = "ngram",
    ):
        self.rule_engine = RuleEngine.from_file(
            rules_path or PROJECT_ROOT / "data" / "rules.json")
        self.retriever = HybridRetriever.from_jsonl(
            corpus_path or PROJECT_ROOT / "data" / "corpus.jsonl")
        self.retrieval_mode = retrieval_mode
        self.llm_mode = llm_mode
        self.llm = (LocalOpenAICompatibleLLM(LocalLLMConfig.from_env())
                    if llm_mode == "local" else None)

        self.advisor: MLAdvisor | None = None
        self.contract_index: ContractIndex | None = None
        self._load_errors: list[str] = []
        if use_ml:
            try:
                self.advisor = MLAdvisor()
            except Exception as exc:
                self._load_errors.append(f"ML 추천 계층 미탑재: {exc}")
        if use_contract_index:
            try:
                self.contract_index = ContractIndex.load()
            except Exception as exc:
                self._load_errors.append(f"공개계약 인덱스 미탑재: {exc}")

    # ------------------------------------------------------------------
    def intake(self, text: str, item_name: str | None = None) -> dict[str, Any]:
        """자유서술을 구조화 필드로 변환한다. 판정 전에 담당자 확인이 필요하다."""
        if self.llm is not None:
            return extract_with_llm(text, self.llm, item_name)
        return extract(text, item_name)

    # ------------------------------------------------------------------
    def review(self, case: dict[str, Any], top_k: int = 5) -> dict[str, Any]:
        evaluation = self.rule_engine.evaluate(case).as_dict()

        query = " ".join(str(case.get(k, "")) for k in (
            "item_name", "description", "proposed_type", "sole_source_basis",
            "urgent_security_basis")) + " " + " ".join(case.get("evidence", [])) \
            + " " + evaluation["legal_ground"]
        hits = self.retriever.search(query, top_k=top_k, mode=self.retrieval_mode)
        sources = [{
            "id": h.document.get("id"), "title": h.document.get("title"),
            "source_url": h.document.get("source_url"),
            "source_date": h.document.get("source_date"),
            "status": h.document.get("status"), "score": h.score,
            "excerpt": h.document.get("text"),
        } for h in hits]

        advisory: dict[str, Any] = {"signals": []}
        if self.advisor is not None:
            similarity_max = None
            if self.contract_index is not None:
                nearest = self.contract_index.search(case.get("item_name", ""), top_k=1)
                similarity_max = nearest[0]["score"] if nearest else 0.0
            advisory["ml"] = self.advisor.advise(case, similarity_max=similarity_max)
            advisory["signals"].extend(advisory["ml"]["signals"])
        if self.contract_index is not None:
            advisory["contract_data"] = self.contract_index.analyze(case)
            advisory["signals"].extend(advisory["contract_data"]["signals"])

        report = {
            "case_id": case.get("case_id"),
            "item_name": case.get("item_name"),
            "reviewed_at": date.today().isoformat(),
            "rules_current_as_of": self.rule_engine.rules["current_as_of"],
            **evaluation,
            "advisory": advisory,
            "retrieved_sources": sources,
            "layer_authority": {
                "decision_by": "규칙 엔진 (결정론적)",
                "advisory_by": "ML 추천 + 공개계약 검색 (판정 변경 불가)",
                "narrative_by": "내부망 LLM (선택, 판정·근거 변경 불가)",
            },
            "load_warnings": self._load_errors,
            "disclaimer": ("본 결과는 공개 규정 기반 사전검토이며 적법성 확정 또는 "
                           "계약담당공무원의 최종 판단을 대체하지 않습니다."),
        }
        if self.llm:
            try:
                report["llm_summary"] = self.llm.summarize({
                    "decision": report["decision"],
                    "legal_ground": report["legal_ground"],
                    "findings": report["findings"],
                    "missing_evidence": report["missing_evidence"],
                    "controls": report["controls"],
                    "advisory_signals": advisory["signals"],
                    "sources": sources[:3],
                })
            except Exception as exc:
                report["llm_summary"] = None
                report["load_warnings"].append(f"내부 LLM 요약 실패: {exc}")
        return report

    # ------------------------------------------------------------------
    @staticmethod
    def to_markdown(report: dict[str, Any]) -> str:
        L = [
            "# MIL-Check 사전검토 보고서", "",
            f"- 사례 ID: {report.get('case_id') or '-'}",
            f"- 품명/과업: {report.get('item_name') or '-'}",
            f"- 검토일: {report['reviewed_at']}",
            f"- 규정 기준일: {report['rules_current_as_of']}",
            f"- 결과: **{DECISION_KO.get(report['decision'], report['decision'])}**",
            f"- 제안 법적 근거: {report['legal_ground']}",
            "",
        ]
        if report.get("llm_summary"):
            L += ["## 요약(내부 LLM 작성, 판정 변경 없음)", "", report["llm_summary"], ""]

        L += ["## 1. 규칙 점검 — 판정 근거", ""]
        for f in report["findings"]:
            L.append(f"- [{f['severity'].upper()}] {f['message']} ({f['legal_basis']})")

        L += ["", "## 2. 누락 증빙", ""]
        L += [f"- {i}" for i in report["missing_evidence"]] or ["- 없음"]

        L += ["", "## 3. 필수 통제", ""]
        L += [f"- {i}" for i in report["controls"]] or ["- 없음"]

        adv = report.get("advisory", {})
        if adv.get("signals"):
            L += ["", "## 4. 참고 신호 — 판정에 반영되지 않음", ""]
            for s in adv["signals"]:
                L.append(f"- [{s['severity'].upper()}·{s['code']}] {s['message']}")

        ml = adv.get("ml")
        if ml:
            L += ["", "### 4-1. 근거조항 추천 (공개계약 3.7만 건 학습)", "",
                  "| 순위 | 조항 | 확률 |", "|---:|---|---:|"]
            for i, a in enumerate(ml["article_top_k"], 1):
                L.append(f"| {i} | {a['label'][:60]} | {a['probability']:.1%} |")
            if ml.get("declared_article"):
                p = ml.get("declared_article_probability") or 0.0
                L.append("")
                L.append(f"제시 근거 `{ml['declared_article']}`의 모형 확률: {p:.1%}")

        cd = adv.get("contract_data")
        if cd and cd.get("similar_contracts"):
            L += ["", "### 4-2. 유사 공개계약", "",
                  "| 계약명 | 추정가격 | 계약방법 | 체결월 |", "|---|---:|---|---|"]
            for h in cd["similar_contracts"]:
                L.append(f"| {h['name'][:34]} | {h['est_price']:,} | {h['method']} | {h['month']} |")
            band = cd.get("price_band", {})
            if band.get("available"):
                L += ["", f"유사계약 {band['samples']}건 가격대: "
                          f"1사분위 {band['q1']:,}원 / 중앙값 {band['med']:,}원 / "
                          f"3사분위 {band['q3']:,}원"]

        L += ["", "## 5. 적용 가정", ""]
        L += [f"- {i}" for i in report["assumptions"]] or ["- 없음"]

        L += ["", "## 6. 검색된 공개 근거", ""]
        for s in report["retrieved_sources"]:
            L.append(f"- [{s['title']}]({s['source_url']}) — "
                     f"{s.get('source_date') or '일자 미상'}, {s.get('status')}")

        la = report.get("layer_authority", {})
        if la:
            L += ["", "## 7. 계층별 권한", ""]
            L += [f"- {k}: {v}" for k, v in la.items()]
        if report.get("load_warnings"):
            L += ["", "## 알림", ""] + [f"- {w}" for w in report["load_warnings"]]
        L += ["", f"> {report['disclaimer']}", ""]
        return "\n".join(L)

    @staticmethod
    def load_case(path: str | Path) -> dict[str, Any]:
        with Path(path).open(encoding="utf-8") as fh:
            return json.load(fh)
