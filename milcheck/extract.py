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
    ("sole_source", "specific_technical_service",
     r"특정\s*기술|특수\s*자격|전문\s*경험|자격\s*보유.*용역|특정\s*품질"),
    ("urgent_security", "urgent", r"긴급|촉박|시급|급히|급하|즉시\s*필요|재해|재난|복구|한파|태풍"),
    ("urgent_security", "security", r"보안상|비밀|기밀|대외비|보안\s*시설"),
    ("post_tender", "failed_tender", r"재공고|유찰|입찰이\s*성립하지|응찰자\s*없"),
    ("designated_product", "designated", r"우수조달|혁신제품|성능인증|중증장애인생산품|지정제품"),
    ("lease", "lease", r"임대차|임대\s*계약|장비를\s*임차|렌탈"),
    ("small_amount", "general", r"소액|이하\s*수의|간이"),
]

EVIDENCE_PATTERNS = {
    "price_reasonableness": r"가격\s*(적정|비교|산출)|시중가|단가\s*비교|가격조사",
    # 실무 문장은 '자격은 확인했다'처럼 조사가 끼어들므로 조사를 허용한다.
    "vendor_eligibility": r"자격[은는이가을를도]?\s*(확인|증명)|사업자\s*등록|면허|소기업\s*확인|소상공인\s*확인",
    "conflict_of_interest_check": r"이해\s*충돌|이해관계|배제\s*사유\s*확인|청렴",
    "no_artificial_split_review": r"분할\s*(?:발주\s*)?(검토|여부)|합산\s*검토|동일\s*사업\s*확인|동일\s*사업\s*추가\s*(구매|발주)\s*(?:는\s*)?없",
    "objective_market_search": r"시장\s*조사|市場|공개\s*조사|조달\s*시장\s*확인|대체품\s*조사",
    "no_substitute_analysis": r"대체\s*(불가|불능)|대체품\s*(없|부재)|대체\s*가능성\s*분석",
    "installed_asset_spec": r"설치\s*자산|기존\s*설비\s*규격|현\s*장비\s*규격",
    "compatibility_evidence": r"호환성\s*(자료|확인서|시험)|연동\s*시험",
    "registration_certificate": r"특허증|등록증|권리\s*증명",
    "registration_validity": r"권리\s*(유효|존속)|등록\s*유효기간",
    "original_supplier_proof": r"제조\s*증명|공급\s*실적\s*증명|납품\s*확인서",
    "direct_service_necessity": r"직접\s*시공\s*필요|직접\s*정비\s*필요",
    "single_supplier_evidence": r"유일\s*(업체|생산|공급)\s*(증명|확인)|독점\s*공급\s*확인|단독\s*생산\s*증명",
    "unique_qualification_evidence": r"특수\s*자격\s*(증명|확인)|전문\s*경험\s*증명|기술\s*자격\s*증명",
    "scope_necessity": r"업무\s*범위\s*필요성|용역\s*범위\s*소명",
    "alternative_experts_search": r"대체\s*전문가\s*조사|다른\s*전문\s*업체\s*조사|대안\s*인력\s*조사",
    "tender_failure_record": r"유찰|입찰\s*(실패|무응찰)|응찰자\s*없|입찰결과",
    "re_tender_record": r"재공고|재입찰|재공고\s*결과",
    "competition_review": r"경쟁\s*(가능성|검토)|경쟁절차\s*검토",
    "designation_certificate": r"우수조달|혁신제품|성능인증|중증장애인생산품|지정\s*(증서|확인서|제품)",
    "certificate_validity": r"인증\s*(유효|기간)|지정\s*(유효|기간)|유효기간",
    "lease_necessity": r"임대\s*(필요|사유)|임차\s*(필요|사유)|렌탈\s*필요",
    "incident_log": r"사건\s*(일지|경위)|고장\s*(일지|보고)|발생\s*경위",
    "security_risk_analysis": r"보안\s*(위험|영향)\s*분석|보안성\s*검토",
    "urgency_causation": r"긴급\s*사유\S*\s*(입증|소명)|예측\S*\s*곤란|불가피\S*\s*사정",
}

NEGATED_EVIDENCE_RE = re.compile(
    r"(?:아직|현재)?\s*(?:못|안|않|미실시|미완료)|없(?:습니다|음|다)"
)


def _evidence_is_present(text: str, key: str, pattern: str) -> bool:
    """증빙 표현이 있어도 '아직 못 했다'는 부정 문장은 보유 자료로 세지 않는다."""
    match = re.search(pattern, text)
    if not match:
        return False

    # '동일 사업 추가 구매 없음'은 분할발주 검토가 끝났다는 의미의
    # 긍정적 확인이므로 일반적인 '없음' 부정 처리에서 제외한다.
    if key == "no_artificial_split_review" and re.search(
        r"동일\s*사업\s*추가\s*(구매|발주)\s*(?:는\s*)?없", text
    ):
        return True

    # 같은 문장 안에 다른 증빙의 긍정·부정 표현이 함께 있을 수 있으므로
    # 접속어·구두점 기준으로 현재 절만 확인한다.
    clause_start = max(
        text.rfind(".", 0, match.start()),
        text.rfind(",", 0, match.start()),
        text.rfind("지만", 0, match.start()),
        text.rfind("그러나", 0, match.start()),
    ) + 1
    before = text[max(clause_start, match.start() - 12):match.start()]
    # 뒤에 이어진 다른 증빙의 '없음'이 앞 항목을 부정하지 않도록
    # 부정 표현은 증빙 표현 바로 뒤의 짧은 구간에서만 확인한다.
    after = text[match.end():match.end() + 18]
    return not NEGATED_EVIDENCE_RE.search(before + after)

# 담당자가 스스로 밝힌 위험 신호
SELF_DELAY_RE = (r"(행정|결재|기안|검토|예산|집행|발주|착수)\S{0,4}\s*(지연|늦|지체)"
                 r"|늦게\s*착수|준비가\s*늦")
SPLIT_HINT_RE = r"나눠\s*(발주|계약)|분할\s*발주|쪼개|여러\s*건으로"

# 업체가 스스로 써 준 확인서는 독립적인 입증자료가 아니므로 따로 표시한다.
VENDOR_ATTESTATION_RE = r"(업체|제조사|공급사|납품업체|해당\s*회사)\s*(자체\s*)?(확인서|확인\s*공문|의견서)"


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

    if re.search(r"임대차|임대\s*계약|임차|렌탈", flat):
        fields["contract_category"] = "lease"
    elif re.search(r"용역|정비|유지보수|위탁|서비스", flat):
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

    evidence = [
        k for k, pat in EVIDENCE_PATTERNS.items()
        if _evidence_is_present(flat, k, pat)
    ]
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
        fields["self_created_urgency"] = True
        fields["urgency_cause"] = "routine_delay"
        notes.append("내부 행정지연이 긴급사유로 제시되었을 가능성을 표시했습니다.")
    if re.search(SPLIT_HINT_RE, flat):
        fields["split_contract_risk"] = True

    if re.search(VENDOR_ATTESTATION_RE, flat) and "compatibility_evidence" not in evidence:
        # 업체 확인서만 있는 상태를 보유 증빙(✓)으로도, 완전한 미보유(✗)로도 보지 않는다.
        fields["vendor_attestation_only"] = True
        notes.append("업체가 발급한 확인서만 확인되었습니다. 독립적인 입증자료가 아니므로 "
                     "보유 증빙으로 계산하지 않았습니다.")

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
