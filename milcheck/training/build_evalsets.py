"""평가세트 3층 구조를 생성한다.

  L1 자체 회귀세트   : 규칙 설계 의도를 검증. 라벨은 자체 작성 → 순환논증 한계 명시
  L2 실데이터 외부라벨: 방위사업청 공개계약의 실제 근거조항·계약방법이 정답
  L3 감사사례 재현세트: 공개 감사보고서의 지적사항이 정답

L1만으로 성능을 주장하지 않기 위해 세 층을 파일과 지표에서 분리한다.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

import pandas as pd

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from train_models import load, article_key  # noqa: E402
from analyze_audit import dedupe  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "eval"
EVAL.mkdir(exist_ok=True)
SEED = 20260725

FULL_EVIDENCE = {
    "small_amount": ["price_reasonableness", "vendor_eligibility",
                     "conflict_of_interest_check", "no_artificial_split_review"],
    "compatibility": ["price_reasonableness", "vendor_eligibility",
                      "conflict_of_interest_check", "installed_asset_spec",
                      "compatibility_evidence", "objective_market_search",
                      "no_substitute_analysis", "audit_notification_plan"],
    "patented_no_substitute": ["price_reasonableness", "vendor_eligibility",
                               "conflict_of_interest_check", "registration_certificate",
                               "registration_validity", "objective_market_search",
                               "no_substitute_analysis", "audit_notification_plan"],
    "original_supplier_direct_service": ["price_reasonableness", "vendor_eligibility",
                                         "conflict_of_interest_check",
                                         "original_supplier_proof",
                                         "direct_service_necessity",
                                         "objective_market_search",
                                         "audit_notification_plan"],
}


# --------------------------------------------------------------------------
# L1 자체 회귀세트
# --------------------------------------------------------------------------
def build_l1() -> list[dict]:
    cases: list[dict] = []
    n = 0

    def add(**kw):
        nonlocal n
        n += 1
        kw.setdefault("origin", "synthetic")
        kw["case_id"] = f"SYN-{n:03d}"
        cases.append(kw)

    items = ["행정용 바코드 스캐너", "부대 사무용 의자", "정수장비 소모품", "전투화 세정용품",
             "취사장 냉장고", "체력단련장 매트", "행정용 복합기 토너", "야전 텐트 보수자재"]

    # (1) 소액수의 금액 경계 — 상한 전후를 촘촘히 확인
    for i, (price, contractor, basis, expected) in enumerate([
        (19_990_000, "general", "general", "PASS_WITH_CONTROLS"),
        (20_000_000, "general", "general", "PASS_WITH_CONTROLS"),
        (20_000_001, "general", "general", "REJECT_GROUND"),
        (25_000_000, "general", "general", "REJECT_GROUND"),
        (99_000_000, "small_enterprise", "small_enterprise_or_small_business",
         "PASS_WITH_CONTROLS"),
        (100_000_000, "small_business", "small_enterprise_or_small_business",
         "PASS_WITH_CONTROLS"),
        (100_000_001, "small_enterprise", "small_enterprise_or_small_business",
         "REJECT_GROUND"),
        (49_000_000, "women_business", "supported_enterprise", "PASS_WITH_CONTROLS"),
        (100_000_000, "disabled_business", "supported_enterprise", "PASS_WITH_CONTROLS"),
        (49_000_000, "youth_startup", "general", "PASS_WITH_CONTROLS"),
        (50_000_001, "youth_startup", "general", "REJECT_GROUND"),
        (95_000_000, "general", "special_knowledge", "PASS_WITH_CONTROLS"),
    ]):
        if contractor in {"women_business", "disabled_business",
                          "qualified_social_economy"}:
            one_quote = price <= 50_000_000        # one_quote_supported_enterprise
        elif contractor == "youth_startup":
            one_quote = price <= 50_000_000        # youth_startup
        else:
            one_quote = price <= 20_000_000        # one_quote_general
        add(item_name=items[i % len(items)],
            description="금액 경계 확인용 사례", contract_category="goods",
            estimated_price_krw_ex_vat=price, proposed_type="small_amount",
            small_amount_basis=basis, contractor_category=contractor,
            quote_count_planned=1 if one_quote else 2,
            electronic_quotes_planned=not one_quote,
            split_contract_risk=False,
            evidence=FULL_EVIDENCE["small_amount"],
            expected_decision=expected)

    # (2) 견적 절차 결함
    add(item_name="사무용 파티션", description="2천만원 초과인데 전자견적 계획 없음",
        contract_category="goods", estimated_price_krw_ex_vat=45_000_000,
        proposed_type="small_amount",
        small_amount_basis="small_enterprise_or_small_business",
        contractor_category="small_enterprise", quote_count_planned=1,
        electronic_quotes_planned=False, split_contract_risk=False,
        evidence=FULL_EVIDENCE["small_amount"], expected_decision="NEEDS_EVIDENCE")
    add(item_name="사무용 파티션", description="2천만원 초과, 2인 전자견적 계획 있음",
        contract_category="goods", estimated_price_krw_ex_vat=45_000_000,
        proposed_type="small_amount",
        small_amount_basis="small_enterprise_or_small_business",
        contractor_category="small_enterprise", quote_count_planned=2,
        electronic_quotes_planned=True, split_contract_risk=False,
        evidence=FULL_EVIDENCE["small_amount"], expected_decision="PASS_WITH_CONTROLS")
    add(item_name="급식 부자재", description="소액이나 견적 계획 자체가 없음",
        contract_category="goods", estimated_price_krw_ex_vat=8_000_000,
        proposed_type="small_amount", small_amount_basis="general",
        contractor_category="general", quote_count_planned=0,
        electronic_quotes_planned=False, split_contract_risk=False,
        evidence=FULL_EVIDENCE["small_amount"], expected_decision="NEEDS_EVIDENCE")

    # (3) 분할발주
    add(item_name="병영생활관 커튼", description="한도 맞추려 분할한 정황 표시",
        contract_category="goods", estimated_price_krw_ex_vat=18_000_000,
        proposed_type="small_amount", small_amount_basis="general",
        contractor_category="general", quote_count_planned=1,
        electronic_quotes_planned=False, split_contract_risk=True,
        evidence=FULL_EVIDENCE["small_amount"], expected_decision="REJECT_GROUND")
    add(item_name="병영생활관 커튼", description="분할 검토 증빙 자체가 누락",
        contract_category="goods", estimated_price_krw_ex_vat=18_000_000,
        proposed_type="small_amount", small_amount_basis="general",
        contractor_category="general", quote_count_planned=1,
        electronic_quotes_planned=False, split_contract_risk=False,
        evidence=["price_reasonableness", "vendor_eligibility",
                  "conflict_of_interest_check"],
        expected_decision="NEEDS_EVIDENCE")

    # (4) 공통 증빙 누락
    for missing in ["price_reasonableness", "vendor_eligibility",
                    "conflict_of_interest_check"]:
        ev = [e for e in FULL_EVIDENCE["small_amount"] if e != missing]
        add(item_name="행정용 복합기 토너", description=f"{missing} 누락",
            contract_category="goods", estimated_price_krw_ex_vat=12_000_000,
            proposed_type="small_amount", small_amount_basis="general",
            contractor_category="general", quote_count_planned=1,
            electronic_quotes_planned=False, split_contract_risk=False,
            evidence=ev, expected_decision="NEEDS_EVIDENCE")

    # (5) 경쟁 불성립형 — 근거별 완비/누락
    for basis in ["compatibility", "patented_no_substitute",
                  "original_supplier_direct_service"]:
        add(item_name=f"{basis} 완비 사례", description="필수 증빙 모두 확보",
            contract_category="goods", estimated_price_krw_ex_vat=60_000_000,
            proposed_type="sole_source", sole_source_basis=basis,
            alternatives_exist=False, evidence=FULL_EVIDENCE[basis],
            expected_decision="PASS_WITH_CONTROLS")
        for drop in FULL_EVIDENCE[basis][3:]:
            ev = [e for e in FULL_EVIDENCE[basis] if e != drop]
            add(item_name=f"{basis} 증빙 부족", description=f"{drop} 누락",
                contract_category="goods", estimated_price_krw_ex_vat=60_000_000,
                proposed_type="sole_source", sole_source_basis=basis,
                alternatives_exist=False, evidence=ev,
                expected_decision="NEEDS_EVIDENCE")
        add(item_name=f"{basis} 대체품 존재", description="대체 가능 공급자가 확인됨",
            contract_category="goods", estimated_price_krw_ex_vat=60_000_000,
            proposed_type="sole_source", sole_source_basis=basis,
            alternatives_exist=True, evidence=FULL_EVIDENCE[basis],
            expected_decision="REJECT_GROUND")

    add(item_name="세부근거 미상 수의", description="호환·특허·유일 중 무엇인지 미기재",
        contract_category="goods", estimated_price_krw_ex_vat=30_000_000,
        proposed_type="sole_source", evidence=FULL_EVIDENCE["compatibility"],
        expected_decision="REJECT_GROUND")

    # (6) 긴급·보안형
    urgent_full = ["price_reasonableness", "vendor_eligibility",
                   "conflict_of_interest_check", "unforeseeable_event_record",
                   "immediate_deadline_record", "competition_time_infeasible",
                   "scope_limited_to_necessity"]
    security_full = ["price_reasonableness", "vendor_eligibility",
                     "conflict_of_interest_check", "security_basis_record",
                     "disclosure_risk_analysis", "scope_limited_to_security_need",
                     "supplier_security_safeguards"]
    add(item_name="한파 피해 급수관 긴급 복구", description="예측 곤란한 사건",
        contract_category="service", estimated_price_krw_ex_vat=70_000_000,
        proposed_type="urgent_security", urgent_security_basis="urgent",
        self_created_urgency=False, urgency_cause="natural_disaster",
        evidence=urgent_full, expected_decision="PASS_WITH_CONTROLS")
    add(item_name="발주 지연에 따른 긴급 구매", description="내부 행정지연이 원인",
        contract_category="goods", estimated_price_krw_ex_vat=70_000_000,
        proposed_type="urgent_security", urgent_security_basis="urgent",
        self_created_urgency=True, urgency_cause="routine_delay",
        evidence=urgent_full, expected_decision="REJECT_GROUND")
    add(item_name="연말 예산소진 긴급 구매", description="예산 불용 방지 목적",
        contract_category="goods", estimated_price_krw_ex_vat=40_000_000,
        proposed_type="urgent_security", urgent_security_basis="urgent",
        urgency_cause="budget_exhaustion", evidence=urgent_full,
        expected_decision="REJECT_GROUND")
    add(item_name="긴급 사유 기록 부족", description="사건일지 없음",
        contract_category="service", estimated_price_krw_ex_vat=30_000_000,
        proposed_type="urgent_security", urgent_security_basis="urgent",
        self_created_urgency=False, urgency_cause="equipment_failure",
        evidence=[e for e in urgent_full if e != "unforeseeable_event_record"],
        expected_decision="NEEDS_EVIDENCE")
    add(item_name="보안시설 관리용역", description="보안 위험분석 확보",
        contract_category="service", estimated_price_krw_ex_vat=120_000_000,
        proposed_type="urgent_security", urgent_security_basis="security",
        evidence=security_full, expected_decision="PASS_WITH_CONTROLS")
    add(item_name="보안 표지만 있는 구매", description="보안성 검토자료 없음",
        contract_category="goods", estimated_price_krw_ex_vat=50_000_000,
        proposed_type="urgent_security", urgent_security_basis="security",
        evidence=[e for e in security_full if e != "disclosure_risk_analysis"],
        expected_decision="NEEDS_EVIDENCE")
    add(item_name="긴급·보안 구분 미기재", description="어느 사유인지 미선택",
        contract_category="goods", estimated_price_krw_ex_vat=20_000_000,
        proposed_type="urgent_security", evidence=urgent_full,
        expected_decision="REJECT_GROUND")

    # (6-b) 용역 유형 및 복합 결함 사례
    add(item_name="장비 외주정비 용역", description="용역 소액수의 상한 경계",
        contract_category="service", estimated_price_krw_ex_vat=20_000_000,
        proposed_type="small_amount", small_amount_basis="general",
        contractor_category="general", quote_count_planned=1,
        electronic_quotes_planned=False, split_contract_risk=False,
        evidence=FULL_EVIDENCE["small_amount"], expected_decision="PASS_WITH_CONTROLS")
    add(item_name="장비 외주정비 용역", description="용역 소액수의 상한 초과",
        contract_category="service", estimated_price_krw_ex_vat=21_000_000,
        proposed_type="small_amount", small_amount_basis="general",
        contractor_category="general", quote_count_planned=2,
        electronic_quotes_planned=True, split_contract_risk=False,
        evidence=FULL_EVIDENCE["small_amount"], expected_decision="REJECT_GROUND")
    add(item_name="금액 초과와 증빙 누락 동시 발생", description="치명 결함 우선순위 확인",
        contract_category="goods", estimated_price_krw_ex_vat=30_000_000,
        proposed_type="small_amount", small_amount_basis="general",
        contractor_category="general", quote_count_planned=0,
        electronic_quotes_planned=False, split_contract_risk=False,
        evidence=[], expected_decision="REJECT_GROUND")
    add(item_name="대체품 존재와 증빙 누락 동시 발생", description="치명 결함 우선순위 확인",
        contract_category="goods", estimated_price_krw_ex_vat=45_000_000, proposed_type="sole_source",
        sole_source_basis="patented_no_substitute", alternatives_exist=True,
        evidence=[], expected_decision="REJECT_GROUND")
    add(item_name="긴급성 원인 미상", description="원인 필드가 비어 있음",
        contract_category="service", estimated_price_krw_ex_vat=25_000_000,
        proposed_type="urgent_security", urgent_security_basis="urgent",
        evidence=urgent_full, expected_decision="PASS_WITH_CONTROLS")
    add(item_name="보안 사유 분류근거 누락", description="classification_basis 없음",
        contract_category="service", estimated_price_krw_ex_vat=80_000_000,
        proposed_type="urgent_security", urgent_security_basis="security",
        evidence=[e for e in security_full if e != "security_basis_record"],
        expected_decision="NEEDS_EVIDENCE")

    # (7) 범위 밖
    add(item_name="관사 신축공사", description="공사 계약은 별도 규칙팩",
        contract_category="construction", estimated_price_krw_ex_vat=300_000_000,
        proposed_type="small_amount", evidence=[], expected_decision="OUT_OF_SCOPE")
    add(item_name="부대 창고 임대차", description="임대차 계약",
        contract_category="lease", estimated_price_krw_ex_vat=30_000_000,
        proposed_type="small_amount", evidence=[], expected_decision="OUT_OF_SCOPE")
    add(item_name="재공고 유찰 후 수의", description="시행령 제27조 계열",
        contract_category="goods", estimated_price_krw_ex_vat=80_000_000,
        proposed_type="failed_rebid", evidence=[], expected_decision="OUT_OF_SCOPE")
    add(item_name="우수조달물품 구매", description="시행령 제26조제1항제3호",
        contract_category="goods", estimated_price_krw_ex_vat=150_000_000,
        proposed_type="excellent_product", evidence=[],
        expected_decision="OUT_OF_SCOPE")

    # (8) 입력 오류
    add(item_name="금액 미입력 사례", description="추정가격 0",
        contract_category="goods", estimated_price_krw_ex_vat=0,
        proposed_type="small_amount", small_amount_basis="general",
        contractor_category="general", quote_count_planned=1,
        electronic_quotes_planned=False, split_contract_risk=False,
        evidence=FULL_EVIDENCE["small_amount"], expected_decision="REJECT_GROUND")
    add(item_name="음수 금액 사례", description="추정가격 음수",
        contract_category="goods", estimated_price_krw_ex_vat=-1,
        proposed_type="small_amount", small_amount_basis="general",
        contractor_category="general", quote_count_planned=1,
        electronic_quotes_planned=False, split_contract_risk=False,
        evidence=FULL_EVIDENCE["small_amount"], expected_decision="REJECT_GROUND")
    return cases


# --------------------------------------------------------------------------
# L2 실데이터 외부라벨 세트
# --------------------------------------------------------------------------
def build_l2(n_sample: int = 300) -> list[dict]:
    """학습에 사용되지 않은 홀드아웃 계약만 표본으로 삼는다.

    학습 데이터가 섞이면 성능이 부풀려지므로, train_models.py가 남긴
    테스트 인덱스에 속한 계약만 사용한다.
    """
    raw = load()   # train_models.py와 동일한 인덱스 공간
    test_index_path = Path(__file__).resolve().parent / "artifacts" / \
        "test_index_article.json"
    if not test_index_path.exists():
        raise SystemExit("training/artifacts/test_index_article.json이 없습니다. "
                         "train_models.py를 먼저 실행하십시오.")
    holdout = set(json.loads(test_index_path.read_text()))

    # 인덱스를 보존한 채 홀드아웃만 남긴 뒤 계약번호 중복을 제거한다.
    sub = raw[raw.index.isin(holdout)].copy()
    sub["article"] = sub["수의계약사유"].map(article_key)
    sub = sub[sub["article"].notna()]
    sub["차수num"] = pd.to_numeric(sub["계약차수"], errors="coerce").fillna(0)
    sub = sub.sort_values(["계약번호", "차수num"]).drop_duplicates("계약번호", keep="last")

    rng = random.Random(SEED)
    idx = rng.sample(list(sub.index), min(n_sample, len(sub)))
    rows = []
    for i, j in enumerate(idx, start=1):
        r = sub.loc[j]
        rows.append({
            "case_id": f"REAL-{i:03d}",
            "origin": "d2b_public_contract",
            "item_name": r["계약명"],
            "contract_category": "goods" if r["업무구분명"] == "물품" else "service",
            "estimated_price_krw_ex_vat": int(r["est_price"]),
            "gold_article": r["article"],
            "gold_article_text": r["수의계약사유"],
            "gold_method": r["계약체결방법명"],
            "contract_month": str(r["date"])[:7],
        })
    return rows


# --------------------------------------------------------------------------
# L3 감사사례 재현세트
# --------------------------------------------------------------------------
def build_l3() -> list[dict]:
    """공개 감사보고서의 지적 유형을 사례로 재구성한다.

    금액·품명은 보고서에 공개된 범위에서만 사용하고, 공개되지 않은 세부값은
    지적 유형이 유지되는 선에서 대표값으로 대체했다. 정답은 감사기관의 지적이다.
    """
    return [
        {"case_id": "AUD-001", "origin": "AUDIT-002 감사원 그늘막 분할 수의계약",
         "item_name": "그늘막 설치", "description": "총 8,530만원 상당을 22건으로 분할 수의계약",
         "contract_category": "goods", "estimated_price_krw_ex_vat": 3_800_000,
         "proposed_type": "small_amount", "small_amount_basis": "general",
         "contractor_category": "general", "quote_count_planned": 1,
         "electronic_quotes_planned": False, "split_contract_risk": True,
         "evidence": ["price_reasonableness", "vendor_eligibility",
                      "conflict_of_interest_check"],
         "audit_finding": "분할 수의계약", "expected_flag": "SMALL-SPLIT",
         "expected_decision": "REJECT_GROUND"},
        {"case_id": "AUD-002", "origin": "AUDIT-003 구로구 수의계약 운영실태",
         "item_name": "청사 시설 보수", "description": "통합발주 검토 없이 공종별 분할",
         "contract_category": "service", "estimated_price_krw_ex_vat": 15_000_000,
         "proposed_type": "small_amount", "small_amount_basis": "general",
         "contractor_category": "general", "quote_count_planned": 1,
         "electronic_quotes_planned": False, "split_contract_risk": True,
         "evidence": ["price_reasonableness", "vendor_eligibility",
                      "conflict_of_interest_check"],
         "audit_finding": "시기적·공종별 분할발주", "expected_flag": "SMALL-SPLIT",
         "expected_decision": "REJECT_GROUND"},
        {"case_id": "AUD-003", "origin": "AUDIT-004 부산 특정제품 규격 지정",
         "item_name": "특정 인증번호 지정 자재 구매",
         "description": "시방서에 특정 제품 인증번호를 명시해 사실상 단일업체 지정",
         "contract_category": "goods", "estimated_price_krw_ex_vat": 40_000_000,
         "proposed_type": "sole_source", "sole_source_basis": "compatibility",
         "alternatives_exist": True,
         "evidence": ["price_reasonableness", "vendor_eligibility",
                      "conflict_of_interest_check", "installed_asset_spec",
                      "compatibility_evidence"],
         "audit_finding": "경쟁제한적 규격 지정", "expected_flag": "SOLE-ALTERNATIVE",
         "expected_decision": "REJECT_GROUND"},
        {"case_id": "AUD-004", "origin": "AUDIT-004 부산 특정제품 규격 지정",
         "item_name": "업체 확인서만 있는 호환성 주장",
         "description": "독립 시장조사와 대체불가성 분석 없이 업체 확인서만 확보",
         "contract_category": "goods", "estimated_price_krw_ex_vat": 35_000_000,
         "proposed_type": "sole_source", "sole_source_basis": "compatibility",
         "alternatives_exist": False,
         "evidence": ["price_reasonableness", "vendor_eligibility",
                      "conflict_of_interest_check", "installed_asset_spec",
                      "compatibility_evidence"],
         "audit_finding": "객관적 대체불가성 미입증", "expected_flag": "SOLE-EVIDENCE",
         "expected_decision": "NEEDS_EVIDENCE"},
        {"case_id": "AUD-005", "origin": "AUDIT-001 기상청 수의계약 실태",
         "item_name": "연도말 집중 소액수의",
         "description": "연말 예산 소진을 위해 긴급성을 주장",
         "contract_category": "goods", "estimated_price_krw_ex_vat": 18_000_000,
         "proposed_type": "urgent_security", "urgent_security_basis": "urgent",
         "urgency_cause": "budget_exhaustion",
         "evidence": ["price_reasonableness", "vendor_eligibility",
                      "conflict_of_interest_check"],
         "audit_finding": "연도말 예산소진형 수의계약", "expected_flag": "URGENT-SELF",
         "expected_decision": "REJECT_GROUND"},
        {"case_id": "AUD-006", "origin": "AUDIT-001 기상청 수의계약 실태",
         "item_name": "원가 증빙 없는 수의계약",
         "description": "가격 적정성 검토자료 없이 업체 제시가로 계약",
         "contract_category": "goods", "estimated_price_krw_ex_vat": 19_000_000,
         "proposed_type": "small_amount", "small_amount_basis": "general",
         "contractor_category": "general", "quote_count_planned": 1,
         "electronic_quotes_planned": False, "split_contract_risk": False,
         "evidence": ["vendor_eligibility", "conflict_of_interest_check",
                      "no_artificial_split_review"],
         "audit_finding": "가격 적정성 미검토", "expected_flag": "MISSING:price_reasonableness",
         "expected_decision": "NEEDS_EVIDENCE"},
        {"case_id": "AUD-007", "origin": "AUDIT-005 통계청 인쇄계약 편중",
         "item_name": "반복 인쇄계약",
         "description": "동일 업체와 반복 수의계약, 경쟁·전자견적 확대 권고 대상",
         "contract_category": "service", "estimated_price_krw_ex_vat": 19_500_000,
         "proposed_type": "small_amount", "small_amount_basis": "general",
         "contractor_category": "general", "quote_count_planned": 1,
         "electronic_quotes_planned": False, "split_contract_risk": False,
         "evidence": ["price_reasonableness", "vendor_eligibility",
                      "conflict_of_interest_check"],
         "audit_finding": "특정업체 편중", "expected_flag": "MISSING:no_artificial_split_review",
         "expected_decision": "NEEDS_EVIDENCE"},
        {"case_id": "AUD-008", "origin": "CASE-003 국방부 맞춤형복지 해석",
         "item_name": "특별법 설립기관 관리대행",
         "description": "특별법 법인이라는 사실만으로 수의계약 근거를 주장",
         "contract_category": "service", "estimated_price_krw_ex_vat": 200_000_000,
         "proposed_type": "sole_source", "sole_source_basis": "single_supplier",
         "alternatives_exist": False,
         "evidence": ["price_reasonableness", "vendor_eligibility",
                      "conflict_of_interest_check"],
         "audit_finding": "열거주의 위반 위험", "expected_flag": "SOLE-EVIDENCE",
         "expected_decision": "NEEDS_EVIDENCE"},
        {"case_id": "AUD-009", "origin": "CASE-002 방산물자 대상행위 구분",
         "item_name": "제조·구매 조항으로 정비 계약",
         "description": "제조·구매 근거조항을 정비 계약까지 확대 적용",
         "contract_category": "service", "estimated_price_krw_ex_vat": 90_000_000,
         "proposed_type": "sole_source",
         "sole_source_basis": "original_supplier_direct_service",
         "alternatives_exist": False,
         "evidence": ["price_reasonableness", "vendor_eligibility",
                      "conflict_of_interest_check", "original_supplier_proof"],
         "audit_finding": "사유와 대상행위 불일치", "expected_flag": "SOLE-EVIDENCE",
         "expected_decision": "NEEDS_EVIDENCE"},
        {"case_id": "AUD-010", "origin": "REG-001 정부 입찰·계약 집행기준",
         "item_name": "2천만원 초과 소액수의 전자견적 미실시",
         "description": "2천만원을 초과했으나 전자조달 견적 절차를 계획하지 않음",
         "contract_category": "goods", "estimated_price_krw_ex_vat": 55_000_000,
         "proposed_type": "small_amount",
         "small_amount_basis": "small_enterprise_or_small_business",
         "contractor_category": "small_enterprise", "quote_count_planned": 1,
         "electronic_quotes_planned": False, "split_contract_risk": False,
         "evidence": ["price_reasonableness", "vendor_eligibility",
                      "conflict_of_interest_check", "no_artificial_split_review"],
         "audit_finding": "전자견적 절차 누락", "expected_flag": "SMALL-QUOTE",
         "expected_decision": "NEEDS_EVIDENCE"},
        {"case_id": "AUD-011", "origin": "AUDIT-002 면책 판단 구조",
         "item_name": "수요 변동에 따른 순차 발주",
         "description": "분할 정황은 있으나 불가피성 자료가 확보된 경우",
         "contract_category": "goods", "estimated_price_krw_ex_vat": 3_800_000,
         "proposed_type": "small_amount", "small_amount_basis": "general",
         "contractor_category": "general", "quote_count_planned": 1,
         "electronic_quotes_planned": False, "split_contract_risk": True,
         "evidence": ["price_reasonableness", "vendor_eligibility",
                      "conflict_of_interest_check", "no_artificial_split_review",
                      "split_unavoidability_evidence"],
         "audit_finding": "분할이나 불가피성 소명 가능",
         "expected_flag": "SMALL-SPLIT", "expected_decision": "REJECT_GROUND",
         "note": "현재 규칙은 불가피성 증빙을 반영하지 못한다. 알려진 한계로 기록한다."},
        {"case_id": "AUD-012", "origin": "LAW-006 전자견적 예외",
         "item_name": "신선 농수산물 구매",
         "description": "신선도 우선 품목으로 전자견적 예외 주장",
         "contract_category": "goods", "estimated_price_krw_ex_vat": 40_000_000,
         "proposed_type": "small_amount",
         "small_amount_basis": "small_enterprise_or_small_business",
         "contractor_category": "small_enterprise", "quote_count_planned": 1,
         "electronic_quotes_planned": False, "split_contract_risk": False,
         "evidence": ["price_reasonableness", "vendor_eligibility",
                      "conflict_of_interest_check", "no_artificial_split_review",
                      "electronic_quote_exception_basis"],
         "audit_finding": "전자견적 예외 사유 확인 필요",
         "expected_flag": "SMALL-QUOTE", "expected_decision": "NEEDS_EVIDENCE",
         "note": "현재 규칙은 시행규칙 제33조 예외를 반영하지 못한다. 알려진 한계로 기록한다."},
    ]


def write(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{path.name}: {len(rows)}건")


def main():
    write(EVAL / "cases.jsonl", build_l1())
    write(EVAL / "real_cases.jsonl", build_l2())
    write(EVAL / "audit_cases.jsonl", build_l3())


if __name__ == "__main__":
    main()
