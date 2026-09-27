"""한 건 완결 흐름: 문서 이해 후보 → 담당자 확인 → 규칙 판정 → 사전검토 결과서 → 실행 기록.

권한 경계
  - 문서 이해(LLM) 결과는 모두 '후보'다. 규칙 엔진에는 담당자가 확정한 값만 들어간다.
  - 충돌 필드는 담당자가 후보 하나를 고르거나 직접 입력해야 확정된다.
  - 추정가격은 부가세 제외 금액만 규칙에 들어간다. 부가세 포함 금액을 1.1로 자동 환산하지 않는다.
    부가세 포함/미상이면 담당자가 부가세 제외 금액을 직접 입력해야 한다.
  - 계약 대상(contract_category)·검토 유형(proposed_type)은 담당자가 업무 선택으로 정한다.
  - 규칙 증빙 항목은 담당자 체크로만 인정한다. 첨부 문서 존재는 '참고 표시'일 뿐이다.
  - 실행 기록에는 문서 원문을 남기지 않고 확인값(해시)·버전·판정만 남긴다.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .understanding import EXTRACTOR_VERSION, UnderstandingResult

WORKFLOW_VERSION = "workflow-0.1.0"

# 문서 이해 필드 → 규칙 엔진 입력 필드
FIELD_MAP = {
    "item_name": "item_name",
    "sole_source_basis": "sole_source_basis",
    "quote_count": "quote_count_planned",
    "supplier_name": "supplier_name",
}
CASE_CHOICES = {
    "contract_category": {"goods", "service", "lease"},
    "proposed_type": {"sole_source", "small_amount", "urgent_security",
                      "post_tender", "designated_product", "lease"},
}
# 증빙 요구(문서 유형) → 담당자가 체크할 때 참고로 보여줄 규칙 증빙 항목
EVIDENCE_HINTS = {
    "EV-QUOTE": ["price_reasonableness"],
    "EV-SUPPLIER": ["single_supplier_evidence", "original_supplier_proof"],
    "EV-COMPAT": ["compatibility_evidence", "installed_asset_spec"],
}


EVIDENCE_KO = {
    "alternative_experts_search": "대체 전문가 탐색 기록", "at_least_one_quote": "견적서 1부 이상",
    "audit_notification_plan": "감사원 통지 계획", "certificate_validity": "인증 유효성 확인",
    "compatibility_evidence": "호환성 입증자료", "competition_review": "경쟁 가능성 검토",
    "conflict_of_interest_check": "이해충돌 확인", "designation_certificate": "지정 증명서",
    "direct_service_necessity": "직접 정비 필요성", "disclosure_risk_analysis": "공개 시 보안위험 분석",
    "electronic_quote_plan": "전자견적 계획", "installed_asset_spec": "기존 장비 규격자료",
    "lease_necessity": "임차 필요성", "no_artificial_split_review": "분할발주 여부 검토",
    "no_substitute_analysis": "대체품 부재 분석", "objective_market_search": "객관적 시장조사",
    "objective_need_definition": "객관적 소요 정의", "original_supplier_proof": "원공급자 증명",
    "price_reasonableness": "가격 적정성 검토", "registration_certificate": "등록증",
    "registration_validity": "등록 유효성 확인", "scope_limited_to_necessity": "필요 범위 한정 확인",
    "scope_necessity": "용역 범위 필요성", "security_required_evidence": "보안상 필요 입증자료",
    "single_supplier_evidence": "단독공급 입증자료", "two_or_more_quotes": "2인 이상 견적",
    "unique_qualification_evidence": "고유 자격 입증자료", "urgent_required_evidence": "긴급성 입증자료",
    "vendor_eligibility": "업체 자격 확인",
}
FIELD_KO = {
    "item_name": "품명", "estimated_price_krw": "추정가격", "estimated_price_krw_ex_vat": "추정가격(부가세 제외)",
    "tax_status": "부가세 여부", "sole_source_basis": "수의계약 사유", "supplier_name": "업체명",
    "existing_equipment": "기존 장비", "quantity": "수량", "unit_price_krw": "단가",
    "total_amount_krw": "견적 합계", "quote_status": "견적 접수 여부", "quote_count": "견적 수",
}
SEVERITY_KO = {"critical": "중대", "warning": "주의", "pass": "충족", "info": "참고"}
REQ_KO = {"EV-QUOTE": "견적서", "EV-SUPPLIER": "단독공급 확인자료", "EV-COMPAT": "호환성 사유서"}
PRESENCE_KO = {"present": "첨부됨", "absent": "없음", "unknown": "확인 불가"}
SOURCE_KO = {"candidate": "문서에서 추출 후 담당자 확인", "manual": "담당자 직접 입력"}


class ConfirmationError(ValueError):
    """담당자 확인이 규칙을 어겼을 때. 판정으로 넘어가지 않는다."""


@dataclass
class Confirmation:
    """담당자가 화면에서 확정한 내용."""
    reviewer: str
    contract_category: str
    proposed_type: str
    # 필드별 선택: {"fact_id": "..."} 또는 {"manual": 값}
    fields: dict[str, dict[str, Any]] = field(default_factory=dict)
    # 부가세 제외 추정가격(담당자 입력). 부가세 별도로 확인된 후보를 고르면 생략 가능.
    estimated_price_krw_ex_vat: int | None = None
    evidence_checked: list[str] = field(default_factory=list)
    extra_case: dict[str, Any] = field(default_factory=dict)  # alternatives_exist 등 담당자 입력
    answered_questions: dict[str, str] = field(default_factory=dict)


def review_sheet(result: UnderstandingResult) -> dict[str, Any]:
    """담당자 확인 화면에 보여줄 내용(후보·충돌·질문·증빙 참고)."""
    fields: dict[str, list[dict[str, Any]]] = {}
    for c in result.candidates:
        fields.setdefault(c.field, []).append({
            "fact_id": c.fact_id, "value": c.value, "status": c.status,
            "sources": [{"document_id": r.document_id, "quote": r.quote} for r in c.source_refs],
        })
    return {
        "component_status": result.component_status,
        "fields": fields,
        "conflicts": sorted({c.field for c in result.candidates if c.status == "conflicting"}),
        "questions": [{"question_id": q.question_id, "reason": q.reason, "text": q.text,
                       "fields": q.affected_fields} for q in result.questions],
        "evidence": [{"requirement_id": e.requirement_id, "presence": e.presence,
                      "documents": e.document_refs,
                      "rule_items_to_check": EVIDENCE_HINTS.get(e.requirement_id, [])}
                     for e in result.evidence],
        "note": "모든 값은 후보입니다. 확정한 값만 판정에 사용됩니다.",
    }


def _pick(result: UnderstandingResult, name: str, choice: dict[str, Any]) -> tuple[Any, dict]:
    if "manual" in choice:
        return choice["manual"], {"field": name, "source": "manual"}
    fid = choice.get("fact_id")
    matches = [c for c in result.candidates if c.fact_id == fid and c.field == name]
    if not matches:
        raise ConfirmationError(f"{name}: 존재하지 않는 후보 {fid!r}")
    c = matches[0]
    return c.value, {"field": name, "source": "candidate", "fact_id": fid,
                     "documents": [r.document_id for r in c.source_refs]}


def confirm(result: UnderstandingResult, conf: Confirmation) -> tuple[dict[str, Any], list[dict]]:
    """담당자 확인을 적용해 규칙 엔진 입력(case)을 만든다."""
    if not conf.reviewer:
        raise ConfirmationError("확인자 없음")
    for k, allowed in CASE_CHOICES.items():
        if getattr(conf, k) not in allowed:
            raise ConfirmationError(f"{k} 선택값이 허용 범위 밖")
    conflicts = {c.field for c in result.candidates if c.status == "conflicting"}
    for f in conflicts:
        if f not in conf.fields:
            raise ConfirmationError(f"충돌 필드 {f}는 담당자 선택이 필요합니다")

    case: dict[str, Any] = {"contract_category": conf.contract_category,
                            "proposed_type": conf.proposed_type}
    provenance: list[dict] = []
    picked: dict[str, Any] = {}
    for name, choice in conf.fields.items():
        value, prov = _pick(result, name, choice)
        picked[name] = value
        provenance.append(prov)
        if name in FIELD_MAP:
            case[FIELD_MAP[name]] = value

    # 추정가격: 부가세 제외가 확인된 경우만 후보 금액을 그대로 쓴다. 자동 환산 없음.
    if conf.estimated_price_krw_ex_vat is not None:
        if not isinstance(conf.estimated_price_krw_ex_vat, int) or conf.estimated_price_krw_ex_vat <= 0:
            raise ConfirmationError("부가세 제외 추정가격은 양의 정수")
        case["estimated_price_krw_ex_vat"] = conf.estimated_price_krw_ex_vat
        provenance.append({"field": "estimated_price_krw_ex_vat", "source": "manual"})
    elif "estimated_price_krw" in picked:
        if picked.get("tax_status") != "vat_excluded":
            raise ConfirmationError(
                "추정가격이 부가세 별도로 확인되지 않았습니다. 부가세 제외 금액을 직접 입력하십시오")
        case["estimated_price_krw_ex_vat"] = picked["estimated_price_krw"]

    case["evidence"] = sorted(set(conf.evidence_checked))
    reserved = set(case) | {"evidence", "estimated_price_krw_ex_vat", "case_id"} | set(FIELD_MAP.values())
    for k, v in conf.extra_case.items():
        if k in reserved:
            raise ConfirmationError(f"{k}는 extra_case로 덮어쓸 수 없음")
        case[k] = v
    return case, provenance


def _next_report_no(db: sqlite3.Connection) -> str:
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    n = db.execute("SELECT COUNT(*) FROM runs WHERE report_no LIKE ?", (f"MC-{today}-%",)).fetchone()[0]
    return f"MC-{today}-{n + 1:04d}"


def open_store(path: str | Path) -> sqlite3.Connection:
    db = sqlite3.connect(str(path))
    db.execute("""CREATE TABLE IF NOT EXISTS runs(
        report_no TEXT PRIMARY KEY, created_at TEXT, reviewer TEXT, decision TEXT,
        record_json TEXT, record_sha256 TEXT)""")
    return db


def run_case(result: UnderstandingResult, conf: Confirmation, agent, store: sqlite3.Connection,
             case_id: str | None = None) -> dict[str, Any]:
    case, provenance = confirm(result, conf)
    case["case_id"] = case_id
    review = agent.review(case)
    open_q = [q for q in result.questions if q.question_id not in conf.answered_questions]
    report_no = _next_report_no(store)
    report = {
        "report_no": report_no,
        "title": "사전검토 결과서",
        "review": review,
        "confirmed_case": case,
        "provenance": provenance,
        "reviewer": conf.reviewer,
        "document_findings": {
            "conflicts_resolved": sorted({c.field for c in result.candidates if c.status == "conflicting"}),
            "open_questions": [{"id": q.question_id, "text": q.text} for q in open_q],
            "answered_questions": conf.answered_questions,
            "evidence_presence": {e.requirement_id: e.presence for e in result.evidence},
        },
        "versions": {"workflow": WORKFLOW_VERSION, "extractor": EXTRACTOR_VERSION,
                     "rules_current_as_of": review.get("rules_current_as_of"),
                     "model": result.run_record.get("model")},
    }
    record = {  # 원문 없이 확인값만
        "report_no": report_no, "reviewer": conf.reviewer,
        "documents": result.run_record.get("documents"),
        "llm_request_sha256": result.run_record.get("request_sha256"),
        "llm_response_sha256": result.run_record.get("response_sha256"),
        "component_status": result.component_status,
        "confirmed_case": case, "provenance": provenance,
        "decision": review["decision"], "findings": [f["code"] for f in review["findings"]],
        "missing_evidence": review["missing_evidence"], "versions": report["versions"],
    }
    blob = json.dumps(record, ensure_ascii=False, sort_keys=True)
    sha = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    with store:
        store.execute("INSERT INTO runs VALUES(?,?,?,?,?,?)",
                      (report_no, datetime.now(timezone.utc).isoformat(), conf.reviewer,
                       review["decision"], blob, sha))
    report["record_sha256"] = sha
    return report


def lookup(store: sqlite3.Connection, report_no: str) -> dict[str, Any] | None:
    row = store.execute("SELECT record_json, record_sha256 FROM runs WHERE report_no=?",
                        (report_no,)).fetchone()
    if not row:
        return None
    ok = hashlib.sha256(row[0].encode("utf-8")).hexdigest() == row[1]
    return {"record": json.loads(row[0]), "integrity_ok": ok}


def render_markdown(report: dict[str, Any]) -> str:
    from .agent import DECISION_KO
    r = report["review"]
    df = report["document_findings"]
    ev = lambda k: EVIDENCE_KO.get(k, k)  # noqa: E731
    L = [f"# 사전검토 결과서 {report['report_no']}", "",
         f"- 품명: {r.get('item_name')}",
         f"- 판정: **{DECISION_KO.get(r['decision'], r['decision'])}**",
         f"- 근거: {r['legal_ground']}", f"- 확인자: {report['reviewer']}", "",
         "## 규칙 점검 결과"]
    L += [f"- [{SEVERITY_KO.get(f['severity'], f['severity'])}] {f['message']} ({f['legal_basis']}, 규칙 {f['code']})"
          for f in r["findings"]]
    L += ["", "## 보완 필요 증빙"] + ([f"- {ev(m)}" for m in r["missing_evidence"]] or ["- 없음"])
    L += ["", "## 통제 조건"] + ([f"- {c}" for c in r["controls"]] or ["- 없음"])
    L += ["", "## 문서 대조"]
    L.append("- 담당자가 해소한 충돌: " + (", ".join(FIELD_KO.get(f, f) for f in df["conflicts_resolved"]) or "없음"))
    L.append("- 첨부 확인: " + ", ".join(f"{REQ_KO.get(k, k)} {PRESENCE_KO.get(v, v)}"
                                       for k, v in df["evidence_presence"].items()))
    L += ["- 남은 질문:"] + ([f"  - {q['text']}" for q in df["open_questions"]] or ["  - 없음"])
    L += ["", "## 확정값 출처"]
    L += [f"- {FIELD_KO.get(p['field'], p['field'])}: {SOURCE_KO.get(p['source'], p['source'])}"
          + (f" (문서 {', '.join(p.get('documents', []))})" if p.get("documents") else "")
          for p in report["provenance"]]
    L += ["", "## 참고 신호 (판정 변경 불가)"]
    L += [f"- {s['message']}" if isinstance(s, dict) and "message" in s else f"- {s}"
          for s in r.get("advisory", {}).get("signals", [])] or ["- 없음"]
    v = report["versions"]
    L += ["", "---", f"버전: 흐름 {v['workflow']} / 문서 이해 {v['extractor']} / 규칙 기준일 {v['rules_current_as_of']} / 모델 {v['model']}",
          f"기록 확인값: {report['record_sha256'][:16]}…", "", r["disclaimer"]]
    return "\n".join(L)
