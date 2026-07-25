"""MIL-Check 로컬 시연 서버 — 외부 통신 없이 표준 라이브러리만으로 동작한다.

  python demo_app.py   →   http://127.0.0.1:8765
"""
from __future__ import annotations

import argparse
import html
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from milcheck.agent import DECISION_KO, MilCheckAgent

ROOT = Path(__file__).resolve().parent
AGENT = MilCheckAgent(llm_mode="none")

BASE_EVIDENCE = ["price_reasonableness", "vendor_eligibility",
                 "conflict_of_interest_check"]

PRESETS = {
    "small_ok": {
        "label": "A. 정상 소액수의",
        "hint": "규칙 통과. ML 추천 조항이 담당자 제시 근거와 일치하는지 확인한다.",
        "case": {
            "case_id": "DEMO-A", "item_name": "교육훈련용 보호장구 구매",
            "description": "부대 교육훈련용 보호장구, 동일 사업 추가 구매 없음",
            "contract_category": "goods", "proposed_type": "small_amount",
            "estimated_price_krw_ex_vat": 18_000_000,
            "contractor_category": "general", "small_amount_basis": "general",
            "quote_count_planned": 1, "electronic_quotes_planned": False,
            "split_contract_risk": False,
            "evidence": BASE_EVIDENCE + ["no_artificial_split_review"],
        },
    },
    "compatibility_missing": {
        "label": "B. 호환성 증빙 부족",
        "hint": "업체 확인서만으로는 통과하지 않는다. 독립 시장조사와 대체불가성 분석을 요구한다.",
        "case": {
            "case_id": "DEMO-B", "item_name": "기존 정수장비 교체 모듈",
            "description": "기존 장비와 특정 업체 모듈만 호환된다고 주장",
            "contract_category": "goods", "proposed_type": "sole_source",
            "sole_source_basis": "compatibility",
            "estimated_price_krw_ex_vat": 46_000_000, "alternatives_exist": False,
            "evidence": BASE_EVIDENCE + ["installed_asset_spec",
                                         "compatibility_evidence"],
        },
    },
    "urgent_bad": {
        "label": "C. 자체 지연을 긴급사유로 제시",
        "hint": "납기 촉박이라는 결과가 아니라 원인을 본다. 내부 행정지연은 긴급수의 근거가 아니다.",
        "case": {
            "case_id": "DEMO-C", "item_name": "발주 지연에 따른 긴급 구매",
            "description": "통상적인 발주 준비가 늦어져 납기가 촉박해짐",
            "contract_category": "goods", "proposed_type": "urgent_security",
            "urgent_security_basis": "urgent", "self_created_urgency": True,
            "urgency_cause": "routine_delay",
            "estimated_price_krw_ex_vat": 70_000_000,
            "evidence": BASE_EVIDENCE + ["unforeseeable_event_record",
                                         "immediate_deadline_record",
                                         "competition_time_infeasible",
                                         "scope_limited_to_necessity"],
        },
    },
    "method_conflict": {
        "label": "D. 계약방법 재검토 신호",
        "hint": "규칙은 통과시키지만 유사한 공개계약은 경쟁으로 체결된 경우가 많다는 신호를 낸다.",
        "case": {
            "case_id": "DEMO-D", "item_name": "감시장비 외주정비 용역",
            "description": "장비 외주정비 용역을 수의계약으로 검토 중",
            "contract_category": "service", "proposed_type": "small_amount",
            "estimated_price_krw_ex_vat": 19_000_000,
            "contractor_category": "general", "small_amount_basis": "general",
            "quote_count_planned": 1, "electronic_quotes_planned": False,
            "split_contract_risk": False,
            "evidence": BASE_EVIDENCE + ["no_artificial_split_review"],
        },
    },
}

SAMPLE_TEXT = ("기존 정수장비에 들어가는 부품인데 다른 회사 제품은 호환이 안 됩니다. "
               "부가세 제외 4,600만원이고 가격조사와 자격 확인은 마쳤습니다. "
               "업체 확인서는 받았지만 시장조사는 아직 못 했습니다.")

STYLE = """
:root{--bg:#0f1115;--panel:#171a21;--line:#252a34;--fg:#e8eaed;--mut:#9aa3b2;
--ok:#3fb950;--warn:#d29922;--bad:#f85149;--info:#58a6ff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font-family:-apple-system,'Segoe UI','Noto Sans KR',sans-serif;line-height:1.6}
.wrap{max-width:1080px;margin:0 auto;padding:28px 20px 64px}
h1{font-size:22px;margin:0 0 4px}
.sub{color:var(--mut);font-size:13px;margin-bottom:20px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;
padding:18px;margin-bottom:16px}
.btns{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px}
a.btn,button.btn{display:inline-block;padding:8px 14px;border-radius:7px;
border:1px solid var(--line);background:#1e222b;color:var(--fg);
text-decoration:none;font-size:13px;cursor:pointer}
a.btn.on{border-color:var(--info);color:var(--info)}
.badge{display:inline-block;padding:5px 12px;border-radius:6px;font-weight:600;font-size:14px}
.PASS_WITH_CONTROLS{background:rgba(63,185,80,.15);color:var(--ok)}
.NEEDS_EVIDENCE{background:rgba(210,153,34,.15);color:var(--warn)}
.REJECT_GROUND{background:rgba(248,81,73,.15);color:var(--bad)}
.OUT_OF_SCOPE{background:rgba(154,163,178,.15);color:var(--mut)}
h2{font-size:14px;text-transform:none;color:var(--mut);margin:0 0 10px;
border-bottom:1px solid var(--line);padding-bottom:7px;font-weight:600}
ul{margin:0;padding-left:19px}li{margin-bottom:5px;font-size:14px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line)}
th{color:var(--mut);font-weight:600}td.num{text-align:right}
.sig{padding:9px 12px;border-radius:7px;margin-bottom:7px;font-size:13.5px;
border-left:3px solid var(--line);background:#1c202a}
.sig.warning{border-left-color:var(--warn)}
.sig.info{border-left-color:var(--info)}
.code{color:var(--mut);font-size:11.5px;letter-spacing:.02em}
.hint{color:var(--mut);font-size:13px;margin-bottom:14px;font-style:italic}
textarea{width:100%;min-height:96px;background:#0d1017;color:var(--fg);
border:1px solid var(--line);border-radius:8px;padding:11px;font-size:13.5px;
font-family:inherit;resize:vertical}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:760px){.grid{grid-template-columns:1fr}}
.foot{color:var(--mut);font-size:12px;margin-top:22px;border-top:1px solid var(--line);
padding-top:14px}
.kv{font-size:13px;color:var(--mut);margin-bottom:3px}
"""


def esc(x) -> str:
    return html.escape(str(x))


def render_findings(report: dict) -> str:
    out = ["<div class='card'><h2>1. 규칙 점검 — 이 결과만이 판정 근거</h2><ul>"]
    for f in report["findings"]:
        color = {"critical": "var(--bad)", "warning": "var(--warn)",
                 "pass": "var(--ok)"}.get(f["severity"], "var(--mut)")
        out.append(f"<li><span style='color:{color};font-weight:600'>"
                   f"[{esc(f['severity']).upper()}]</span> {esc(f['message'])}"
                   f"<br><span class='code'>{esc(f['code'])} · {esc(f['legal_basis'])}</span></li>")
    out.append("</ul></div>")

    out.append("<div class='grid'>")
    out.append("<div class='card'><h2>2. 누락 증빙</h2><ul>")
    out += [f"<li>{esc(m)}</li>" for m in report["missing_evidence"]] or ["<li>없음</li>"]
    out.append("</ul></div>")
    out.append("<div class='card'><h2>3. 필수 통제</h2><ul>")
    out += [f"<li>{esc(c)}</li>" for c in report["controls"]] or ["<li>없음</li>"]
    out.append("</ul></div></div>")
    return "".join(out)


def render_advisory(report: dict) -> str:
    adv = report.get("advisory") or {}
    out = []
    if adv.get("signals"):
        out.append("<div class='card'><h2>4. 참고 신호 — 판정에 반영되지 않음</h2>")
        for s in adv["signals"]:
            out.append(f"<div class='sig {esc(s['severity'])}'>{esc(s['message'])}"
                       f"<br><span class='code'>{esc(s['code'])}</span></div>")
        out.append("</div>")

    ml = adv.get("ml")
    if ml:
        rows = "".join(
            f"<tr><td>{i}</td><td>{esc(a['label'][:64])}</td>"
            f"<td class='num'>{a['probability']:.1%}</td></tr>"
            for i, a in enumerate(ml["article_top_k"], 1))
        methods = ", ".join(f"{esc(m['method'])} {m['probability']:.0%}"
                            for m in ml["method_top_k"][:3])
        declared = ""
        if ml.get("declared_article"):
            declared = (f"<div class='kv'>담당자 제시 근거 <b>{esc(ml['declared_article'])}</b>"
                        f"의 모형 확률: "
                        f"{(ml.get('declared_article_probability') or 0):.1%}</div>")
        out.append(
            "<div class='card'><h2>4-1. 근거조항 추천 — 공개계약 3.7만 건 학습</h2>"
            f"{declared}"
            "<table><tr><th>순위</th><th>조항</th><th>확률</th></tr>"
            f"{rows}</table>"
            f"<div class='kv' style='margin-top:9px'>계약방법 추천: {methods}</div></div>")

    cd = adv.get("contract_data")
    if cd and cd.get("similar_contracts"):
        rows = "".join(
            f"<tr><td>{esc(h['name'][:40])}</td><td class='num'>{h['est_price']:,}</td>"
            f"<td>{esc(h['method'])}</td><td>{esc(h['month'])}</td></tr>"
            for h in cd["similar_contracts"])
        band = cd.get("price_band") or {}
        band_html = ""
        if band.get("available"):
            band_html = (f"<div class='kv' style='margin-top:9px'>유사계약 {band['samples']}건 "
                         f"가격대 — 1사분위 {band['q1']:,}원 / 중앙값 {band['med']:,}원 / "
                         f"3사분위 {band['q3']:,}원</div>")
        out.append(
            "<div class='card'><h2>4-2. 유사 공개계약</h2>"
            "<table><tr><th>계약명</th><th>추정가격</th><th>계약방법</th><th>체결월</th></tr>"
            f"{rows}</table>{band_html}</div>")
    return "".join(out)


def render_sources(report: dict) -> str:
    rows = "".join(
        f"<li><a href='{esc(s['source_url'])}' style='color:var(--info)' "
        f"target='_blank' rel='noopener'>{esc(s['title'])}</a> "
        f"<span class='code'>{esc(s.get('source_date') or '일자 미상')} · "
        f"{esc(s.get('status'))}</span></li>"
        for s in report["retrieved_sources"])
    assum = "".join(f"<li>{esc(a)}</li>" for a in report["assumptions"]) or "<li>없음</li>"
    return (f"<div class='card'><h2>5. 적용 가정</h2><ul>{assum}</ul></div>"
            f"<div class='card'><h2>6. 검색된 공개 근거 — 문자 n-gram 로컬 검색</h2>"
            f"<ul>{rows}</ul></div>")


def render_page(key: str, report: dict, intake: dict | None = None,
                text_value: str = "") -> str:
    buttons = "".join(
        f"<a class='btn {"on" if k == key else ""}' href='/?preset={k}'>"
        f"{esc(v['label'])}</a>" for k, v in PRESETS.items())
    hint = PRESETS.get(key, {}).get("hint", "")
    intake_html = ""
    if intake:
        fields = "".join(f"<tr><td>{esc(k)}</td><td>{esc(v)}</td></tr>"
                         for k, v in intake["fields"].items() if k != "description")
        notes = "".join(f"<li>{esc(n)}</li>" for n in intake["notes"]) or "<li>없음</li>"
        intake_html = (
            "<div class='card'><h2>0. 자유서술에서 추출된 필드 — 담당자 확인 필요</h2>"
            f"<table><tr><th>필드</th><th>값</th></tr>{fields}</table>"
            f"<div class='kv' style='margin-top:9px'>추출 방식: {esc(intake['method'])}</div>"
            f"<ul style='margin-top:7px'>{notes}</ul></div>")

    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MIL-Check 시연</title><style>{STYLE}</style></head><body><div class="wrap">
<h1>MIL-Check — 근거 기반 군 계약 사전점검</h1>
<div class="sub">외부 통신 없음 · 규정 기준일 {esc(report['rules_current_as_of'])} ·
판정은 규칙 엔진, 추천·검색은 참고 신호</div>
<div class="btns">{buttons}</div>
<div class="hint">{esc(hint)}</div>

<div class="card"><h2>자유서술로 입력하기</h2>
<form method="get" action="/">
<textarea name="text" placeholder="상황을 문장으로 적으십시오">{esc(text_value or SAMPLE_TEXT)}</textarea>
<div style="margin-top:9px"><button class="btn" type="submit">추출 후 검토</button></div>
</form></div>

{intake_html}

<div class="card">
<div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px">
<div><b>{esc(report.get('item_name') or '-')}</b>
<div class="kv">{esc(report.get('legal_ground'))}</div></div>
<span class="badge {esc(report['decision'])}">
{esc(DECISION_KO.get(report['decision'], report['decision']))}</span></div></div>

{render_findings(report)}
{render_advisory(report)}
{render_sources(report)}

<div class="foot">
판정은 규칙 엔진이 결정하며 ML 추천·유사계약·LLM은 판정을 바꾸지 못합니다.
본 화면은 공개 규정과 공개 계약데이터에 기반한 사전검토이며, 적법성 확정 또는
계약담당공무원의 최종 판단을 대체하지 않습니다.
</div></div></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path not in ("/", "/index.html"):
            self.send_error(404)
            return
        params = {}
        for pair in parsed.query.split("&"):
            if "=" in pair:
                k, _, v = pair.partition("=")
                from urllib.parse import unquote_plus
                params[k] = unquote_plus(v)

        text = params.get("text", "").strip()
        intake = None
        if text:
            intake = AGENT.intake(text)
            case = dict(intake["fields"])
            case["case_id"] = "DEMO-TEXT"
            key = ""
        else:
            key = params.get("preset", "small_ok")
            if key not in PRESETS:
                key = "small_ok"
            case = PRESETS[key]["case"]

        report = AGENT.review(case)
        body = render_page(key, report, intake, text).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # 조용한 시연을 위해 접속 로그를 끈다
        return


def main() -> int:
    ap = argparse.ArgumentParser(description="MIL-Check 로컬 시연 서버")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"MIL-Check 시연: http://{args.host}:{args.port}  (Ctrl+C 종료)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n종료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
