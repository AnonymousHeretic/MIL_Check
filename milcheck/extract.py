"""입력 단계 AI: 담당자의 자유서술을 검사 가능한 구조화 필드로 변환한다.

계약 담당자는 실무에서 JSON이 아니라 문장으로 상황을 설명한다. 이 모듈은 그 문장을
규칙 엔진이 검사할 수 있는 필드(추정가격·유형·근거·증빙·견적 수)로 바꾼다.

기본 경로는 폐쇄망에서 외부 통신 없이 동작하는 결정론적 추출기다.
내부망에 승인된 LLM이 있으면 추출 결과를 보강할 수 있으나, 추출된 값은
반드시 담당자 확인 단계를 거치며 LLM이 규칙 판정을 바꾸지 못한다.
"""
from __future__ import annotations

import re
from typing import Any

# --------------------------------------------------------------------------
# 금액 파싱
# --------------------------------------------------------------------------
_BIG = {"조": 10**12, "억": 10**8, "만": 10**4}
_SMALL = {"천": 10**3, "백": 10**2, "십": 10}
_AMOUNT_RE = re.compile(r"(\d[\d,\.]*(?:\s*[조억만천백십]|\s*\d[\d,\.]*)*)\s*원")
_TOKEN_RE = re.compile(r"(\d[\d,\.]*)|([조억만천백십])")


def parse_amount(text: str) -> int | None:
    """'1,800만 원', '6천만원', '1억 2천만원', '18,000,000원'을 정수 원 단위로 변환.

    한국어 수사는 억·만이 자릿수를 끊고 천·백·십이 그 안에서 배수로 작동한다.
    ('3천 5백만원' = (3000+500) x 10^4)
    """
    m = _AMOUNT_RE.search((text or "").replace(" ", ""))
    if not m:
        return None
    total = section = current = 0
    for num, unit in _TOKEN_RE.findall(m.group(1)):
        if num:
            try:
                current = float(num.replace(",", ""))
            except ValueError:
                current = 0
        elif unit in _SMALL:
            section += (current or 1) * _SMALL[unit]
            current = 0
        elif unit in _BIG:
            section += current
            total += (section or 1) * _BIG[unit]
            section = current = 0
    result = int(total + section + current)
    return result or None


def has_vat_exclusion(text: str) -> bool | None:
    if re.search(r"(부가세|부가가치세)\s*(제외|별도|미포함)", text):
        return True
    if re.search(r"(부가세|부가가치세)\s*(포함|포함가)", text):
        return False
    return None


# --------------------------------------------------------------------------
# 유형·근거 추론
# --------------------------------------------------------------------------
TYPE_PATTERNS = [
    ("sole_source", "compatibility", r"호환|연동|기존\s*장비.*맞|규격이?\s*맞"),
    ("sole_source", "patented_no_substitute", r"특허|실용신안|디자인등록|의장등록"),
    ("sole_source", "original_supplier_direct_service",
     r"제조사가?\s*직접|공급자가?\s*직접|납품업체가?\s*직접\s*(설치|조립|정비)"),
    ("sole_source", "single_supplier", r"유일|단일\s*업체|독점|국내\s*유일"),
    ("urgent_security", "urgent", r"긴급|촉박|시급|급히|급하|즉시\s*필요|재해|재난|복구|한파|태풍"),
    ("urgent_security", "security", r"보안상|비밀|기밀|대외비|보안\s*시설"),
    ("small_amount", "general", r"소액|이하\s*수의|간이"),
]

EVIDENCE_PATTERNS = {
    "price_reasonableness": r"가격\s*(적정|비교|산출)|시중가|단가\s*비교|가격조사",
    "vendor_eligibility": r"자격\s*(확인|증명)|사업자\s*등록|면허|소기업\s*확인|소상공인\s*확인",
    "conflict_of_interest_check": r"이해\s*충돌|이해관계|배제\s*사유\s*확인|청렴",
    "no_artificial_split_review": r"분할\s*(검토|여부)|합산\s*검토|동일\s*사업\s*확인",
    "objective_market_search": r"시장\s*조사|市場|공개\s*조사|조달\s*시장\s*확인|대체품\s*조사",
    "no_substitute_analysis": r"대체\s*(불가|불능)|대체품\s*(없|부재)|대체\s*가능성\s*분석",
    "installed_asset_spec": r"설치\s*자산|기존\s*설비\s*규격|현\s*장비\s*규격",
    "compatibility_evidence": r"호환성\s*(자료|확인서|시험)|연동\s*시험",
    "registration_certificate": r"특허증|등록증|권리\s*증명",
    "registration_validity": r"권리\s*(유효|존속)|등록\s*유효기간",
    "original_supplier_proof": r"제조\s*증명|공급\s*실적\s*증명|납품\s*확인서",
    "direct_service_necessity": r"직접\s*시공\s*필요|직접\s*정비\s*필요",
    "incident_log": r"사건\s*(일지|경위)|고장\s*(일지|보고)|발생\s*경위",
    "security_risk_analysis": r"보안\s*(위험|영향)\s*분석|보안성\s*검토",
    "urgency_causation": r"긴급\s*사유\S*\s*(입증|소명)|예측\S*\s*곤란|불가피\S*\s*사정",
}

# 담당자가 스스로 밝힌 위험 신호
SELF_DELAY_RE = (r"(행정|결재|기안|검토|예산|집행|발주|착수)\S{0,4}\s*(지연|늦|지체)"
                 r"|늦게\s*착수|준비가\s*늦")
SPLIT_HINT_RE = r"나눠\s*(발주|계약)|분할\s*발주|쪼개|여러\s*건으로"


def extract(text: str, item_name: str | None = None) -> dict[str, Any]:
    """자유서술에서 구조화 필드를 추출한다. 확신하지 못한 값은 넣지 않는다."""
    raw = text or ""
    flat = re.sub(r"\s+", " ", raw)

    fields: dict[str, Any] = {}
    notes: list[str] = []
    confidence: dict[str, str] = {}

    amount = parse_amount(flat)
    if amount:
        fields["estimated_price_krw_ex_vat"] = amount
        vat = has_vat_exclusion(flat)
        if vat is False:
            fields["estimated_price_krw_ex_vat"] = int(round(amount / 1.1))
            notes.append("부가가치세 포함 금액으로 보고 1.1로 나눈 추정가격을 사용했습니다. "
                         "실제 추정가격을 확인하십시오.")
            confidence["estimated_price_krw_ex_vat"] = "low"
        elif vat is True:
            confidence["estimated_price_krw_ex_vat"] = "high"
        else:
            notes.append("부가가치세 포함 여부가 문장에 없어 제외 금액으로 가정했습니다.")
            confidence["estimated_price_krw_ex_vat"] = "medium"

    if re.search(r"용역|정비|유지보수|위탁|서비스", flat):
        fields["contract_category"] = "service"
    elif re.search(r"구매|구입|납품|물품|장비|자재|비품", flat):
        fields["contract_category"] = "goods"

    for ptype, basis, pattern in TYPE_PATTERNS:
        if re.search(pattern, flat):
            fields["proposed_type"] = ptype
            if ptype == "sole_source":
                fields["sole_source_basis"] = basis
            elif ptype == "urgent_security":
                fields["urgent_security_basis"] = basis
            confidence["proposed_type"] = "medium"
            break
    if "proposed_type" not in fields and amount and amount <= 20_000_000:
        fields["proposed_type"] = "small_amount"
        fields["small_amount_basis"] = "general"
        confidence["proposed_type"] = "low"
        notes.append("수의계약 사유가 문장에 명시되지 않아 금액만으로 소액수의로 가정했습니다.")

    if re.search(r"소기업|소상공인", flat):
        fields["contractor_category"] = "small_enterprise_or_small_business"
        fields["small_amount_basis"] = "small_enterprise_or_small_business"
    elif re.search(r"여성기업|장애인기업|사회적기업|사회적협동조합", flat):
        fields["contractor_category"] = "supported_enterprise"
        fields["small_amount_basis"] = "supported_enterprise"

    evidence = [k for k, pat in EVIDENCE_PATTERNS.items() if re.search(pat, flat)]
    if evidence:
        fields["evidence"] = sorted(evidence)

    if "견적" in flat:
        m = re.search(r"(\d+)\s*(?:개|건|인|곳|군데|업체)", flat)
        if m:
            fields["quote_count_planned"] = int(m.group(1))
        elif re.search(r"1인\s*견적|단독\s*견적|한\s*곳", flat):
            fields["quote_count_planned"] = 1
    if re.search(r"전자\s*견적|나라장터|국방전자조달|전자조달", flat):
        fields["electronic_quotes_planned"] = True

    if re.search(SELF_DELAY_RE, flat):
        fields["urgency_cause_internal_delay"] = True
        notes.append("내부 행정지연이 긴급사유로 제시되었을 가능성을 표시했습니다.")
    if re.search(SPLIT_HINT_RE, flat):
        fields["split_contract_risk"] = True

    if item_name:
        fields["item_name"] = item_name
    else:
        head = re.split(r"[,\.\n]", flat.strip())[0]
        fields["item_name"] = head[:40] if head else ""
        confidence["item_name"] = "low"

    fields["description"] = flat[:300]
    return {"fields": fields, "notes": notes, "confidence": confidence,
            "method": "deterministic"}


def extract_with_llm(text: str, llm, item_name: str | None = None) -> dict[str, Any]:
    """승인된 내부망 LLM으로 추출 결과를 보강한다. 실패하면 규칙 결과를 그대로 쓴다."""
    base = extract(text, item_name)
    if llm is None:
        return base
    try:
        enriched = llm.extract_fields(text, base["fields"])
    except Exception as exc:  # 폐쇄망에서 LLM 미가용은 정상 상황이다
        base["notes"].append(f"내부 LLM 보강 실패로 규칙 추출 결과만 사용했습니다: {exc}")
        return base
    if not isinstance(enriched, dict):
        return base
    merged = dict(base["fields"])
    for k, v in enriched.items():
        # 규칙이 이미 확정한 값은 LLM이 덮어쓰지 못한다.
        if k not in merged and v not in (None, "", []):
            merged[k] = v
    base["fields"] = merged
    base["method"] = "deterministic+llm"
    base["notes"].append("내부 LLM이 보강한 필드는 담당자 확인이 필요합니다.")
    return base
