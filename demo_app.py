"""MIL-Check 발표용 로컬 데모 UI.

실행:
    python demo_app.py
    http://127.0.0.1:8765
"""
from __future__ import annotations

import argparse
import html
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from milcheck.agent import DECISION_KO, MilCheckAgent

AGENT = MilCheckAgent(llm_mode="none")

COMMON = ["price_reasonableness", "vendor_eligibility",
          "conflict_of_interest_check"]

PRESETS = {
    "small_ok": {
        "label": "일반 물품 구매",
        "hint": "금액 기준과 기본 증빙을 충족하는 정상 사례",
        "case": {
            "case_id": "DEMO-A", "item_name": "교육훈련용 보호장구 구매",
            "description": "동일 사업 추가 구매 없음", "contract_category": "goods",
            "proposed_type": "small_amount", "small_amount_basis": "general",
            "estimated_price_krw_ex_vat": 18_000_000,
            "contractor_category": "general", "quote_count_planned": 1,
            "electronic_quotes_planned": False, "split_contract_risk": False,
            "evidence": COMMON + ["no_artificial_split_review"],
        },
    },
    "compatibility_missing": {
        "label": "기존 장비 호환 부품",
        "hint": "호환성 주장만으로는 부족하고 대체불가성·시장조사가 필요합니다",
        "case": {
            "case_id": "DEMO-B", "item_name": "기존 정수장비 교체 모듈",
            "description": "기존 장비와 특정 업체 모듈만 호환된다고 주장",
            "contract_category": "goods", "proposed_type": "sole_source",
            "sole_source_basis": "compatibility", "estimated_price_krw_ex_vat": 46_000_000,
            "alternatives_exist": False,
            "evidence": COMMON + ["installed_asset_spec", "compatibility_evidence"],
        },
    },
    "urgent_bad": {
        "label": "긴급 구매 요청",
        "hint": "납기보다 긴급성이 발생한 원인을 검사합니다",
        "case": {
            "case_id": "DEMO-C", "item_name": "발주 지연에 따른 긴급 구매",
            "description": "통상적인 발주 준비가 늦어져 납기가 촉박해짐",
            "contract_category": "goods", "proposed_type": "urgent_security",
            "urgent_security_basis": "urgent", "self_created_urgency": True,
            "urgency_cause": "routine_delay", "estimated_price_krw_ex_vat": 70_000_000,
            "evidence": COMMON + ["unforeseeable_event_record",
                "immediate_deadline_record", "competition_time_infeasible",
                "scope_limited_to_necessity"],
        },
    },
    "method_review": {
        "label": "계약방법 재검토",
        "hint": "규칙은 통과하지만 유사계약 데이터가 경쟁 가능성을 시사합니다",
        "case": {
            "case_id": "DEMO-D", "item_name": "감시장비 외주정비 용역",
            "description": "규칙상 소액수의 요건 충족, 그러나 유사 계약은 대부분 제한경쟁",
            "contract_category": "service", "proposed_type": "small_amount",
            "small_amount_basis": "general", "contractor_category": "general",
            "estimated_price_krw_ex_vat": 19_000_000, "quote_count_planned": 1,
            "electronic_quotes_planned": False, "split_contract_risk": False,
            "evidence": COMMON + ["no_artificial_split_review"],
        },
    },
    "out_scope": {
        "label": "공사 계약",
        "hint": "현재 MVP가 최종 판정하지 않는 계약 유형입니다",
        "case": {
            "case_id": "DEMO-E", "item_name": "부대 배관 공사",
            "description": "부대 내 배관 공사", "contract_category": "construction",
            "proposed_type": "small_amount", "estimated_price_krw_ex_vat": 15_000_000,
            "evidence": [],
        },
    },
}

SAMPLE_TEXT = (
    "기존 정수장비에 들어가는 부품인데 다른 회사 제품은 호환이 안 됩니다. "
    "부가세 제외 4,600만원이고 가격조사와 자격 확인은 마쳤습니다. "
    "업체 확인서는 받았지만 대체품 시장조사는 아직 못 했습니다."
)

STYLE = """
:root{--bg:#f4f7fb;--panel:#fff;--ink:#172033;--muted:#64748b;--line:#dbe3ef;
--blue:#2563eb;--green:#15803d;--amber:#b45309;--red:#b91c1c;--gray:#64748b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans KR",sans-serif;line-height:1.55}
.wrap{max-width:1180px;margin:auto;padding:28px 20px 60px}
.header{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;margin-bottom:20px}
h1{font-size:25px;line-height:1.2;margin:0 0 6px}.sub{color:var(--muted);font-size:13px}
.badges{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}
.pill{background:#e8f0ff;color:#1d4ed8;border-radius:999px;padding:5px 10px;font-size:12px;font-weight:700}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px;margin:14px 0;box-shadow:0 2px 8px #1720330a}
h2{font-size:15px;margin:0 0 12px;border-bottom:1px solid var(--line);padding-bottom:9px}
.btns{display:flex;gap:8px;flex-wrap:wrap}.btn{display:inline-block;border:1px solid var(--line);
border-radius:9px;background:#fff;color:var(--ink);padding:9px 13px;text-decoration:none;
font-size:13px;cursor:pointer}.btn.on{border-color:var(--blue);color:var(--blue);background:#eff6ff}
.primary{background:var(--blue);color:white;border-color:var(--blue);font-weight:700}
.hint{color:var(--muted);font-size:13px;margin:10px 0}.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:800px){.grid{grid-template-columns:1fr}.header{display:block}.badges{justify-content:flex-start;margin-top:12px}}
textarea{width:100%;min-height:115px;border:1px solid var(--line);border-radius:10px;padding:12px;
font:inherit;resize:vertical;background:#fbfdff}.workflow{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}
.step{padding:7px 10px;border-radius:8px;background:#eef2f7;color:var(--muted);font-size:12px}
.step.active{background:#dbeafe;color:#1d4ed8;font-weight:700}.step.done{background:#dcfce7;color:var(--green)}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;padding:8px;
border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--muted);font-weight:600}.num{text-align:right}
.status{display:flex;justify-content:space-between;gap:10px;align-items:center;flex-wrap:wrap}
.badge{padding:8px 12px;border-radius:9px;font-weight:800;font-size:14px}
.PASS_WITH_CONTROLS{background:#dcfce7;color:var(--green)}.NEEDS_EVIDENCE{background:#fef3c7;color:var(--amber)}
.REJECT_GROUND{background:#fee2e2;color:var(--red)}.OUT_OF_SCOPE{background:#e2e8f0;color:var(--gray)}
.finding{padding:10px 12px;border-left:4px solid var(--line);background:#f8fafc;margin:7px 0;border-radius:7px;font-size:13px}
.finding.critical{border-color:var(--red)}.finding.warning{border-color:var(--amber)}.finding.pass{border-color:var(--green)}
.kv{color:var(--muted);font-size:12px}.code{color:var(--muted);font-size:11px}
.checklist{display:grid;grid-template-columns:1fr 1fr;gap:0 24px}.check-row{display:flex;gap:9px;align-items:flex-start;padding:10px 4px;border-bottom:1px solid var(--line);font-size:13px}.check-mark{font-size:17px;font-weight:800;line-height:1}.check-ok .check-mark{color:var(--green)}.check-missing{background:#fffaf0}.check-missing .check-mark{color:var(--amber)}.check-summary{margin-top:13px;font-weight:800;color:var(--amber)}
ul{margin:0;padding-left:20px}li{margin:5px 0;font-size:13px}.callout{background:#eff6ff;border:1px solid #bfdbfe;
padding:12px;border-radius:9px;color:#1e40af;font-size:13px}.footer{font-size:12px;color:var(--muted);margin-top:24px}
"""

def esc(value) -> str:
    return html.escape(str(value))

FIELD_LABELS = {
    "estimated_price_krw_ex_vat": "예상 계약금액",
    "contract_category": "계약 대상",
    "proposed_type": "검토 중인 계약 방식",
    "small_amount_basis": "소액수의 적용 사유",
    "sole_source_basis": "수의계약을 고려하는 이유",
    "urgent_security_basis": "긴급·보안 사유",
    "contractor_category": "계약상대자 유형",
    "quote_count_planned": "예정된 견적 수",
    "electronic_quotes_planned": "전자견적 진행 여부",
    "split_contract_risk": "분할발주 가능성",
    "evidence": "현재 확인된 자료",
    "alternatives_exist": "대체 가능한 업체·제품 존재 여부",
    "urgency_cause_internal_delay": "긴급성이 내부 지연에서 비롯되었는지",
}

VALUE_LABELS = {
    "goods": "물품",
    "service": "용역",
    "lease": "임대차",
    "small_amount": "소액수의계약",
    "sole_source": "수의계약",
    "urgent_security": "긴급·보안 수의계약",
    "post_tender": "공고 후 수의계약",
    "compatibility": "기존 장비와의 호환성",
    "general": "일반 소액수의",
}

EVIDENCE_LABELS = {
    "price_reasonableness": "가격 적정성 조사",
    "vendor_eligibility": "계약상대자 자격 확인",
    "conflict_of_interest_check": "이해충돌 확인",
    "no_artificial_split_review": "분할발주 검토",
    "objective_market_search": "객관적 시장조사",
    "no_substitute_analysis": "대체불가성 분석",
    "installed_asset_spec": "기설치 장비 규격서",
    "compatibility_evidence": "호환성 입증자료",
    "audit_notification_plan": "감사원 통보 계획",
    "at_least_one_quote": "1인 견적 자료",
    "two_or_more_quotes": "2인 이상 견적 자료",
    "electronic_quote_plan": "전자견적 계획",
}

FINDING_LABELS = {
    "critical": "확인 필요",
    "warning": "보완 필요",
    "pass": "확인됨",
    "info": "참고",
}


def display_value(value):
    if isinstance(value, list):
        return " · ".join(EVIDENCE_LABELS.get(x, x) for x in value)
    if isinstance(value, bool):
        return "예" if value else "아니오"
    if isinstance(value, (int, float)) and value >= 100000:
        return f"{value:,.0f}원 (부가세 제외)"
    return VALUE_LABELS.get(value, value)


def evidence_label(key: str) -> str:
    return EVIDENCE_LABELS.get(key, key)

def render_intake(intake: dict | None) -> str:
    if not intake:
        return ""
    rows = "".join(
        f"<tr><td>{esc(FIELD_LABELS.get(k, k))}</td><td>{esc(display_value(v))}</td>"
        f"<td>{'[확인]' if intake.get('confidence',{}).get(k) else '확인 필요'}</td></tr>"
        for k, v in intake["fields"].items() if k != "description"
    )
    notes = "".join(f"<li>{esc(n)}</li>" for n in intake["notes"]) or "<li>추가 주의사항 없음</li>"
    return f"""<div class="card"><h2>1. 시스템이 이해한 내용 <span class="kv">담당자 확인 후 검토 실행</span></h2>
<table><tr><th>확인할 내용</th><th>시스템이 파악한 내용</th><th>확인 상태</th></tr>{rows}</table>
<ul style="margin-top:10px">{notes}</ul>
<div class="callout">이 값은 AI가 추출한 초안입니다. 실제 증빙과 일치하는지 확인한 뒤 규칙 점검 결과를 해석하십시오.</div></div>"""

def render_findings(report: dict, case: dict | None = None, raw_text: str = "") -> str:
    findings = "".join(
        f"<div class='finding {esc(f['severity'])}'><b>{esc(FINDING_LABELS.get(f['severity'], '확인'))}</b> "
        f"{esc(f['message'])}<br><span class='code'>{esc(f['legal_basis'])}</span></div>"
        for f in report["findings"]
    )
    case = case or {}
    present = set(case.get("evidence", []))
    missing_keys = set(report.get("missing_evidence", []))

    if case.get("sole_source_basis") == "compatibility":
        checklist_keys = [
            "price_reasonableness", "vendor_eligibility", "installed_asset_spec",
            "compatibility_evidence", "objective_market_search", "no_substitute_analysis",
        ]
    else:
        checklist_keys = list(dict.fromkeys(sorted(present | missing_keys)))

    checklist_rows = []
    for key in checklist_keys:
        checked = key in present and key not in missing_keys
        label = evidence_label(key)
        note = ""
        if key == "compatibility_evidence" and re.search(r"업체\s*확인서", raw_text):
            checked = False
            note = " · 업체 확인서만 있음, 독립 자료 필요"
        mark = "✓" if checked else "✗"
        cls = "check-ok" if checked else "check-missing"
        checklist_rows.append(
            f"<div class='check-row {cls}'><span class='check-mark'>{mark}</span>"
            f"<span>{esc(label)}{esc(note)}</span></div>"
        )
    checklist = "".join(checklist_rows)
    checked_count = sum(
        1 for key in checklist_keys
        if key in present and key not in missing_keys
        and not (key == "compatibility_evidence" and re.search(r"업체\s*확인서", raw_text))
    )
    missing_count = len(checklist_keys) - checked_count
    procedures = "".join(
        f"<li>{esc(x)}</li>" for x in report.get("controls", [])
    ) or "<li>추가 절차 확인 없음</li>"
    summary = (
        f"<div class='check-summary'>{checked_count} / {len(checklist_keys)}종 확인 · "
        f"{missing_count}종 보완 필요</div>"
    ) if checklist_keys else ""
    return f"""<div class="card"><h2>3. 사전검토 결과 <span class="kv">규정 기준으로 확인한 결과</span></h2>
{findings}</div><div class="card"><h2>4. 요구 증빙 체크리스트 <span class="kv">근거: {esc(report.get('legal_ground','-'))}</span></h2>
<div class="checklist">{checklist}</div>{summary}</div>
<div class="card"><h2>5. 증빙 외 확인 절차</h2><ul>{procedures}</ul></div>"""

def render_advisory(report: dict) -> str:
    adv = report.get("advisory") or {}
    signals = "".join(
        f"<div class='finding {esc(s['severity'])}'><b>참고</b> {esc(s['message'])}</div>"
        for s in adv.get("signals", [])
    )
    ml = adv.get("ml") or {}
    methods = ", ".join(f"{esc(x['method'])} {x['probability']:.0%}" for x in ml.get("method_top_k",[])[:3])
    cd = adv.get("contract_data") or {}
    similar = cd.get("similar_contracts") or []
    similar_rows = "".join(
        f"<tr><td>{esc(x.get('name','-'))}</td><td>{esc(x.get('method','-'))}</td></tr>"
        for x in similar[:5]
    )
    table = (
        f"<table><tr><th>유사 계약</th><th>계약방법</th></tr>{similar_rows}</table>"
        if similar else ""
    )
    if not signals and not table and not methods:
        return ""
    extra = f"<div class='kv'>계약방법 후보: {esc(methods)}</div>" if methods else ""
    return f"""<div class="card"><h2>6. 유사계약 참고 신호 <span class="kv">최종 판정과 구분되는 참고 정보</span></h2>
{signals}{table}{extra}</div>"""

def render_page(key: str, report: dict, intake: dict | None, text: str,
                case: dict | None = None) -> str:
    buttons = "".join(
        f"<a class='btn {'on' if key == k else ''}' href='/?preset={k}'>{esc(v['label'])}</a>"
        for k, v in PRESETS.items()
    )
    active = "done"
    input_text = esc(text or "")
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>MIL-Check 데모</title>
<style>{STYLE}</style></head><body><main class="wrap">
<header class="header"><div><h1>MIL-Check</h1><div class="sub">근거 기반 군 계약 사전점검 보조 시스템 · 로컬 데모</div></div>
<div class="badges"><span class="pill">외부 전송 없음</span><span class="pill">규칙 기반 판정</span><span class="pill">담당자 최종 확인</span></div></header>
<div class="workflow"><span class="step {active}">① 입력</span><span class="step {'done' if intake else ''}">② 내용 확인</span><span class="step {'done' if intake or key else ''}">③ 사전검토</span><span class="step">④ 근거와 확인사항</span></div>
<div class="card"><h2>검토할 계약 상황 선택</h2><div class="btns">{buttons}</div>
<div class="hint">{esc(PRESETS.get(key,{}).get('hint','계약 상황을 선택하거나 아래에 직접 입력하십시오.'))}</div></div>
<div class="card"><h2>0. 계약 상황을 문장으로 입력</h2><form method="get" action="/">
<textarea name="text" placeholder="계약 상황을 문장으로 입력하십시오">{input_text or esc(SAMPLE_TEXT)}</textarea>
<div style="margin-top:10px"><button class="btn primary" type="submit">내용 이해하기</button>
<a class="btn" href="/?preset={esc(key or 'small_ok')}">선택한 상황 다시 보기</a></div></form></div>
<div class="card status"><div><b>{esc(report.get('item_name') or '검토 결과')}</b><div class="kv">{esc(report.get('legal_ground'))}</div></div>
<span class="badge {esc(report['decision'])}">{esc(DECISION_KO.get(report['decision'], report['decision']))}</span></div>
{render_intake(intake)}
{render_findings(report, case, text)}{render_advisory(report)}
<div class="card"><h2>7. 시스템 역할 경계</h2><div class="callout">MIL-Check는 적법성이나 계약을 자동 승인하지 않습니다. 규칙 엔진이 확인 결과를 만들고,
ML·검색·LLM은 참고 신호와 문서화를 제공합니다. 최종 판단과 결재는 담당자에게 있습니다.</div></div>
<div class="footer">규정 기준일 {esc(report.get('rules_current_as_of','-'))} · 본 화면은 공개 규정·공개 계약데이터 기반 시제품입니다.</div>
</main></body></html>"""

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path not in ("/", "/index.html"):
            self.send_error(404); return
        params = parse_qs(parsed.query)
        text = params.get("text", [""])[0].strip()
        intake = AGENT.intake(text) if text else None
        if text:
            case = dict(intake["fields"])
            case["case_id"] = "DEMO-TEXT"
            key = ""
        else:
            key = params.get("preset", ["small_ok"])[0]
            if key not in PRESETS: key = "small_ok"
            case = PRESETS[key]["case"]
        report = AGENT.review(case)
        body = render_page(key, report, intake, text, case).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args): return

def main() -> int:
    parser = argparse.ArgumentParser(description="MIL-Check 발표용 로컬 데모")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"MIL-Check 데모: http://{args.host}:{args.port}")
    try: server.serve_forever()
    except KeyboardInterrupt: print("\n종료")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
