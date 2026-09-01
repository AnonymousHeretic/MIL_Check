from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Evaluation:
    decision: str
    proposed_type: str
    legal_ground: str
    findings: list[dict[str, str]] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    controls: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "proposed_type": self.proposed_type,
            "legal_ground": self.legal_ground,
            "findings": self.findings,
            "missing_evidence": sorted(set(self.missing_evidence)),
            "controls": self.controls,
            "assumptions": self.assumptions,
        }


class RuleEngine:
    def __init__(self, rules: dict[str, Any]):
        self.rules = rules

    @classmethod
    def from_file(cls, path: str | Path) -> "RuleEngine":
        with Path(path).open(encoding="utf-8") as handle:
            return cls(json.load(handle))

    @staticmethod
    def _finding(code: str, severity: str, message: str, basis: str) -> dict[str, str]:
        return {
            "code": code,
            "severity": severity,
            "message": message,
            "legal_basis": basis,
        }

    @staticmethod
    def _require(evidence: set[str], required: list[str], evaluation: Evaluation) -> None:
        evaluation.missing_evidence.extend(item for item in required if item not in evidence)

    def evaluate(self, case: dict[str, Any]) -> Evaluation:
        proposed_type = str(case.get("proposed_type", ""))
        # 정보 부족은 지원 범위 밖 계약과 구분한다. 핵심 입력이 없으면 판정을 보류한다.
        missing_core = [
            not case.get("item_name"),
            not case.get("contract_category"),
            not isinstance(case.get("estimated_price_krw_ex_vat"), (int, float)),
            not proposed_type,
        ]
        if any(missing_core):
            return Evaluation(
                decision="NEEDS_INPUT",
                proposed_type=proposed_type,
                legal_ground="계약 정보 부족 — 추가 입력 필요",
                findings=[
                    self._finding(
                        "INPUT-INCOMPLETE",
                        "warning",
                        "품명·계약 대상·추정가격·검토 사유를 입력해야 규칙 점검을 시작할 수 있습니다.",
                        "입력 정보 확인",
                    )
                ],
            )
        if case.get("contract_category") not in {"goods", "service", "lease"} or (
            case.get("contract_category") == "lease" and proposed_type != "lease"):
            return Evaluation(
                decision="OUT_OF_SCOPE",
                proposed_type=proposed_type,
                legal_ground="현재 판정 범위는 국가계약법상 물품·용역",
                findings=[
                    self._finding(
                        "SCOPE-001",
                        "info",
                        "공사·임대차·매각·방위사업법 특례 계약은 별도 규칙팩이 필요합니다.",
                        "MIL-Check 현재 판정 범위",
                    )
                ],
            )
        if proposed_type == "small_amount":
            result = self._small_amount(case)
        elif proposed_type == "sole_source":
            result = self._sole_source(case)
        elif proposed_type == "urgent_security":
            result = self._urgent_security(case)
        elif proposed_type in {"post_tender", "designated_product", "lease"}:
            result = self._additional_type(case, proposed_type)
        else:
            return Evaluation(
                decision="OUT_OF_SCOPE",
                proposed_type=proposed_type,
                legal_ground="지원하지 않는 수의계약 유형",
                findings=[
                    self._finding(
                        "SCOPE-002",
                        "info",
                        "재공고 유찰, 방산물자, 보훈·복지단체 등은 다음 규칙팩 대상으로 분리했습니다.",
                        "MIL-Check MVP 범위",
                    )
                ],
            )
        self._finalize(result)
        return result

    def _additional_type(self, case: dict[str, Any], proposed_type: str) -> Evaluation:
        """보조 유형은 등록된 필수 증빙을 모두 확인할 때만 통과시킨다."""
        spec = self.rules.get("additional_types", {}).get(proposed_type, {})
        evaluation = Evaluation(
            decision="NEEDS_EVIDENCE", proposed_type=proposed_type,
            legal_ground=spec.get("legal_basis", "추가 규칙팩"),
        )
        evidence = self._common_checks(case, evaluation)
        required = spec.get("required_evidence", [])
        self._require(evidence, required, evaluation)
        if required and all(item in evidence for item in required):
            evaluation.findings.append(self._finding(
                "ADDITIONAL-EVIDENCE", "pass",
                f"{spec.get('label', proposed_type)} 필수 증빙이 입력되었습니다.",
                evaluation.legal_ground,
            ))
        else:
            evaluation.findings.append(self._finding(
                "ADDITIONAL-EVIDENCE", "warning",
                f"{spec.get('label', proposed_type)} 적용 여부와 필수 증빙을 별도 확인해야 합니다.",
                evaluation.legal_ground,
            ))
        evaluation.controls.append("해당 유형의 별도 계약요건·예외사유와 가격 적정성 확인")
        return evaluation

    def _common_checks(self, case: dict[str, Any], evaluation: Evaluation) -> set[str]:
        evidence = set(case.get("evidence", []))
        price = case.get("estimated_price_krw_ex_vat")
        if not isinstance(price, (int, float)) or price <= 0:
            evaluation.findings.append(
                self._finding(
                    "INPUT-PRICE",
                    "critical",
                    "부가가치세를 제외한 양의 추정가격이 필요합니다.",
                    "국가계약법 시행령 제2조·제7조",
                )
            )
        self._require(
            evidence,
            ["price_reasonableness", "vendor_eligibility", "conflict_of_interest_check"],
            evaluation,
        )
        return evidence

    def _small_amount(self, case: dict[str, Any]) -> Evaluation:
        evaluation = Evaluation(
            decision="NEEDS_EVIDENCE",
            proposed_type="small_amount",
            legal_ground="국가계약법 시행령 제26조제1항제5호가목",
        )
        evidence = self._common_checks(case, evaluation)
        price = case.get("estimated_price_krw_ex_vat", 0)
        contractor = case.get("contractor_category", "general")
        basis = case.get("small_amount_basis", "general")
        thresholds = self.rules["small_amount"]["thresholds_krw_ex_vat"]

        if contractor in {"small_enterprise", "small_business"}:
            limit = thresholds["small_enterprise_or_small_business"]
        elif contractor in {"women_business", "disabled_business", "qualified_social_economy"}:
            limit = thresholds["supported_enterprise"]
        elif contractor == "youth_startup":
            limit = thresholds["youth_startup"]
        elif basis == "special_knowledge":
            limit = thresholds["special_knowledge"]
        else:
            limit = thresholds["general"]

        evaluation.assumptions.append(
            f"추정가격은 부가가치세 제외 금액이며 적용 상한은 {limit:,}원으로 계산했습니다."
        )
        if isinstance(price, (int, float)) and price > limit:
            evaluation.findings.append(
                self._finding(
                    "SMALL-THRESHOLD",
                    "critical",
                    f"추정가격 {price:,.0f}원이 입력 조건의 소액수의 상한 {limit:,}원을 초과합니다.",
                    "국가계약법 시행령 제26조제1항제5호가목",
                )
            )
        else:
            evaluation.findings.append(
                self._finding(
                    "SMALL-THRESHOLD",
                    "pass",
                    f"입력된 계약상대자 유형을 전제로 금액 기준({limit:,}원 이하)을 충족합니다.",
                    "국가계약법 시행령 제26조제1항제5호가목",
                )
            )

        if case.get("split_contract_risk") is True:
            evaluation.findings.append(
                self._finding(
                    "SMALL-SPLIT",
                    "critical",
                    "동일 사업을 수의계약 한도에 맞추기 위해 분할한 정황이 표시되었습니다.",
                    "정부 입찰·계약 집행기준 제4장 및 공개 감사사례",
                )
            )
        else:
            self._require(evidence, ["no_artificial_split_review"], evaluation)

        one_quote_limit = thresholds["one_quote_general"]
        one_quote_allowed = price <= one_quote_limit
        if contractor in {"women_business", "disabled_business", "qualified_social_economy"}:
            one_quote_allowed = price <= thresholds["one_quote_supported_enterprise"]
        if contractor == "youth_startup":
            one_quote_allowed = price <= thresholds["youth_startup"]

        planned_quotes = int(case.get("quote_count_planned", 0) or 0)
        if one_quote_allowed:
            if planned_quotes < 1:
                evaluation.missing_evidence.append("at_least_one_quote")
        else:
            if planned_quotes < 2 or not case.get("electronic_quotes_planned", False):
                evaluation.findings.append(
                    self._finding(
                        "SMALL-QUOTE",
                        "warning",
                        "2인 이상 전자견적 절차가 계획되어 있지 않습니다.",
                        "국가계약법 시행령 제30조제1항·제2항",
                    )
                )
                evaluation.missing_evidence.extend(
                    ["two_or_more_quotes", "electronic_quote_plan"]
                )
            else:
                evaluation.controls.append("전자조달시스템에서 2인 이상 견적 접수")
        evaluation.controls.extend(
            ["수의계약 배제사유·자격 확인", "동일·유사 사업 합산 및 분할발주 점검"]
        )
        return evaluation

    def _sole_source(self, case: dict[str, Any]) -> Evaluation:
        evaluation = Evaluation(
            decision="NEEDS_EVIDENCE",
            proposed_type="sole_source",
            legal_ground="국가계약법 시행령 제26조제1항제2호",
        )
        evidence = self._common_checks(case, evaluation)
        basis = case.get("sole_source_basis")
        basis_rules = self.rules["sole_source"]["bases"]
        if basis not in basis_rules:
            evaluation.findings.append(
                self._finding(
                    "SOLE-BASIS",
                    "critical",
                    "호환성·특허·유일생산자·원공급자 직접정비·특정기술용역 중 세부 근거가 필요합니다.",
                    "국가계약법 시행령 제26조제1항제2호",
                )
            )
            return evaluation

        if case.get("alternatives_exist") is True:
            evaluation.findings.append(
                self._finding(
                    "SOLE-ALTERNATIVE",
                    "critical",
                    "적절한 대용품·대체품 또는 경쟁 가능한 공급자가 존재한다고 입력되었습니다.",
                    "국가계약법 시행령 제26조제1항제2호아목·자목",
                )
            )
        required = basis_rules[basis]["required_evidence"]
        self._require(evidence, required, evaluation)
        self._require(evidence, ["audit_notification_plan"], evaluation)
        if all(item in evidence for item in required) and not case.get("alternatives_exist"):
            evaluation.findings.append(
                self._finding(
                    "SOLE-EVIDENCE",
                    "pass",
                    f"{basis_rules[basis]['label']} 근거의 핵심 증빙이 입력되었습니다.",
                    basis_rules[basis]["legal_basis"],
                )
            )
        else:
            evaluation.findings.append(
                self._finding(
                    "SOLE-EVIDENCE",
                    "warning",
                    "업체 주장만이 아니라 독립적인 시장조사와 대체불가성 입증이 필요합니다.",
                    basis_rules[basis]["legal_basis"],
                )
            )
        evaluation.controls.extend(
            [
                "1인 견적이 허용되더라도 가격 적정성 별도 검토",
                "계약 체결 후 소속 중앙관서장 보고 및 감사원 통지 절차 확인",
                "규격서가 특정 업체를 부당하게 지목하는지 역검토",
            ]
        )
        return evaluation

    def _urgent_security(self, case: dict[str, Any]) -> Evaluation:
        subtype = case.get("urgent_security_basis")
        legal = {
            "urgent": "국가계약법 시행령 제26조제1항제1호가목",
            "security": "국가계약법 시행령 제26조제1항제1호나목",
        }.get(subtype, "국가계약법 시행령 제26조제1항제1호")
        evaluation = Evaluation(
            decision="NEEDS_EVIDENCE",
            proposed_type="urgent_security",
            legal_ground=legal,
        )
        evidence = self._common_checks(case, evaluation)
        if subtype == "urgent":
            if case.get("self_created_urgency") is True or case.get("urgency_cause") in {
                "budget_exhaustion",
                "routine_delay",
            }:
                evaluation.findings.append(
                    self._finding(
                        "URGENT-SELF",
                        "critical",
                        "연말 예산소진 또는 통상적 행정지연은 긴급수의 근거로 보기 어렵습니다.",
                        legal,
                    )
                )
            required = self.rules["urgent_security"]["urgent_required_evidence"]
            self._require(evidence, required, evaluation)
        elif subtype == "security":
            required = self.rules["urgent_security"]["security_required_evidence"]
            self._require(evidence, required, evaluation)
        else:
            evaluation.findings.append(
                self._finding(
                    "URGENT-BASIS",
                    "critical",
                    "긴급 또는 보안 중 어느 사유인지 구분해야 합니다.",
                    legal,
                )
            )
            return evaluation

        if all(item in evidence for item in required):
            evaluation.findings.append(
                self._finding(
                    "URGENT-EVIDENCE",
                    "pass",
                    "긴급·보안 사유와 범위 최소화에 관한 핵심 증빙이 입력되었습니다.",
                    legal,
                )
            )
        else:
            evaluation.findings.append(
                self._finding(
                    "URGENT-EVIDENCE",
                    "warning",
                    "단순한 긴급성 주장이나 보안 표지만으로는 부족하며 객관적 기록이 필요합니다.",
                    legal,
                )
            )
        evaluation.controls.extend(
            [
                "긴급·보안에 필요한 최소 범위만 수의계약",
                "1인 견적이 허용되더라도 가격 적정성 별도 검토",
                "사후 계약서·검수·대금지급 증빙 보존",
            ]
        )
        return evaluation

    @staticmethod
    def _finalize(evaluation: Evaluation) -> None:
        if any(item["severity"] == "critical" for item in evaluation.findings):
            evaluation.decision = "REJECT_GROUND"
        elif evaluation.missing_evidence or any(
            item["severity"] == "warning" for item in evaluation.findings
        ):
            evaluation.decision = "NEEDS_EVIDENCE"
        else:
            evaluation.decision = "PASS_WITH_CONTROLS"
