"""웹 MVP: 문서 입력 → 문서 이해(후보) → 담당자 확인 → 규칙 판정 → 판정·근거 → 사전검토 결과서.

HTTP 서버와 분리된 순수 함수 `handle()`만 둔다. 로컬 서버(web_app.py)와 Vercel 어댑터
(api/index.py)는 모두 이 함수를 호출하는 얇은 껍데기다. 판정 로직은 새로 만들지 않고
milcheck.workflow / milcheck.understanding / MilCheckAgent를 그대로 쓴다.

실행 모드
  gold-echo  합성 묶음의 정답을 모델 응답처럼 되돌린다(흐름 시연용, 모델 성능 아님).
  manual     모델을 호출하지 않는다. 담당자가 원문을 보며 값을 직접 입력한다.
  live       승인된 로컬(루프백) OpenAI 호환 서버 호출. 로컬 실행에서만 허용.

배포 구분: 환경변수 MILCHECK_DEPLOY=vercel 일 때만 공개 시연 미러 제한을 적용한다
  (live 거부, 확인자 이름 고정, 실행 기록은 임시 경로). 그 외에는 로컬 동작 그대로다.

원문 보관 금지: 입력 문서 원문은 요청 처리 중 메모리에만 있고, 다음 화면으로는 브라우저
  양식(hidden)으로만 되돌아간다. 실행 기록 DB에는 workflow.run_case가 남기는 해시·확인값만 저장된다.
"""
from __future__ import annotations

import html
import json
import os
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from .agent import DECISION_KO
from .understanding import (FIELD_SPECS, MAX_DOCUMENT_CHARS, SCHEMA_VERSION, Document,
                            DocumentUnderstanding, EndpointConfig, UnderstandingError)
from .workflow import (EVIDENCE_KO, FIELD_KO, PRESENCE_KO, REQ_KO, SEVERITY_KO, SOURCE_KO,
                       Confirmation, ConfirmationError, lookup, open_store, render_markdown,
                       review_sheet, run_case)

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "web_static"
BUNDLES_PATH = ROOT / "eval" / "understanding_dev.jsonl"
MAX_BODY_BYTES = 512 * 1024

BANNER = "공개 시연용 미러 · 합성 데이터만 사용 · 외부 LLM 미사용 · 실제 운용은 폐쇄망 로컬 실행"
GOLD_BADGE = "정답 대입 모드 — 모델 성능 아님"
INPUT_WARNING = "실제 계약 문서·개인정보·군 내부 자료 입력 금지"
VOLATILE_NOTICE = "시연용 미러에서는 기록이 서버 재시작 시 사라집니다"
SAME_DB_LIMIT = ("한계: 실행 기록과 그 확인값(SHA-256)이 같은 DB에 저장됩니다. "
                 "기록 한 건만 바뀐 경우는 탐지하지만, DB 전체를 다시 쓰는 변조는 탐지하지 못합니다.")
DEMO_REVIEWER = "시연 담당자"
ROBOTS = "noindex, nofollow"

MODE_KO = {"gold-echo": "정답 대입(합성 묶음)", "manual": "수동 입력(모델 미사용)",
           "live": "로컬 모델 호출(폐쇄망)"}
DOC_SLOTS = [("R1", "request", "구매요구서"), ("Q1", "quote", "견적서"),
             ("S1", "supplier_confirmation", "공급확인서"),
             ("C1", "compatibility_statement", "호환성 사유서")]
CATEGORY_KO = {"goods": "물품", "service": "용역", "lease": "임차"}
PROPOSED_KO = {"sole_source": "수의계약(단독·호환 사유)", "small_amount": "소액 수의계약",
               "urgent_security": "긴급·보안 수의계약", "post_tender": "유찰 후 수의계약",
               "designated_product": "지정 물품", "lease": "임차"}
ENUM_KO = {
    "tax_status": {"vat_excluded": "부가세 별도(제외)", "vat_included": "부가세 포함",
                   "unknown": "미상"},
    "sole_source_basis": {"compatibility": "기존 장비 호환", "patented_no_substitute": "특허·대체품 없음",
                          "original_supplier_direct_service": "제조·공급자 직접 설치·정비",
                          "single_supplier": "단일 업체만 공급"},
    "quote_status": {"received": "견적 접수", "planned": "견적 예정", "none": "견적 없음"},
}
# 담당자가 화면에서 직접 고르는 추가 사실(규칙 엔진이 읽는 키). 문서 이해 후보가 아니다.
EXTRA_BOOL = {"alternatives_exist": "대체 가능한 다른 제품·업체가 있음",
              "split_contract_risk": "동일 사업을 나눠 발주했을 가능성 있음",
              "self_created_urgency": "긴급성이 기관 내부 사정(예산소진·행정지연)으로 생김"}
EXTRA_SELECT = {
    "small_amount_basis": ("소액수의 기준", {"general": "일반", "special_knowledge": "특수 지식·기술",
                                         "youth_startup": "청년창업기업", "supported_enterprise": "지원 대상 기업"}),
    "urgent_security_basis": ("긴급·보안 구분", {"urgent": "긴급", "security": "보안"}),
}


# --------------------------------------------------------------------------
# 환경
# --------------------------------------------------------------------------
def is_deploy() -> bool:
    return os.environ.get("MILCHECK_DEPLOY", "").strip().lower() == "vercel"


def allowed_modes() -> tuple[str, ...]:
    return ("gold-echo", "manual") if is_deploy() else ("gold-echo", "manual", "live")


def db_path() -> Path:
    """시연 미러는 쓰기 가능한 임시 경로(휘발성), 로컬은 runs/ 아래."""
    if is_deploy():
        return Path(tempfile.gettempdir()) / "milcheck_demo_runs.sqlite"
    env = os.environ.get("MILCHECK_WEB_DB")
    return Path(env) if env else ROOT / "runs" / "web_runs.sqlite"


_agent = None
_agent_lock = threading.Lock()


def get_agent():
    global _agent
    with _agent_lock:
        if _agent is None:
            from .agent import MilCheckAgent
            _agent = MilCheckAgent(llm_mode="none")
        return _agent


def set_agent(agent) -> None:
    """시험용 대역 주입."""
    global _agent
    _agent = agent


def load_bundles() -> dict[str, dict[str, Any]]:
    out = {}
    for line in BUNDLES_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip():
            b = json.loads(line)
            if b.get("origin") == "synthetic":
                out[b["bundle_id"]] = b
    return out


# --------------------------------------------------------------------------
# 문서 이해 전송 계층(모두 네트워크 미사용, live 제외)
# --------------------------------------------------------------------------
def gold_echo_transport(bundle: dict[str, Any]):
    """scripts/eval_understanding.py의 gold-echo와 같은 동작. 정답을 모델 응답 형식으로 되돌린다."""
    texts = {d["document_id"]: d["text"] for d in bundle["documents"]}

    def transport(url, body, headers, timeout):
        user = body["messages"][1]["content"]
        present = {d for d in texts if f'"document_id": "{d}"' in user}
        facts = []
        for f in bundle["gold"]["facts"]:
            if f["document_id"] not in present:
                continue
            start = texts[f["document_id"]].index(f["quote"])
            facts.append({"field": f["field"], "value": f["value"], "source_refs": [{
                "document_id": f["document_id"], "revision": 1, "text_start": start,
                "text_end": start + len(f["quote"]), "quote": f["quote"]}]})
        payload = {"schema_version": SCHEMA_VERSION, "facts": facts,
                   "evidence_claims": [], "questions": []}
        return json.dumps({"choices": [{"message": {
            "content": json.dumps(payload, ensure_ascii=False)}}]}).encode()
    return transport


def manual_transport(url, body, headers, timeout):
    raise UnderstandingError("수동 입력 모드(모델 미호출)")


class RequestError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass
class Inputs:
    mode: str
    bundle_id: str | None
    documents: list[Document]
    pasted: dict[str, str] = field(default_factory=dict)  # 문서 ID → 원문(양식 왕복용)


def parse_inputs(form: dict[str, list[str]], bundles: dict[str, dict]) -> Inputs:
    mode = _one(form, "mode") or "gold-echo"
    if mode not in MODE_KO:
        raise RequestError(400, "알 수 없는 실행 모드")
    if mode not in allowed_modes():
        raise RequestError(403, "시연 미러에서는 live 모드를 사용할 수 없습니다")
    source = _one(form, "source") or "bundle"
    if source == "bundle":
        bid = _one(form, "bundle_id")
        if bid not in bundles:
            raise RequestError(400, "합성 묶음을 선택하십시오")
        docs = [Document(d["document_id"], d["revision"], d["text"], doc_type=d["doc_type"])
                for d in bundles[bid]["documents"]]
        return Inputs(mode, bid, docs)
    if source != "paste":
        raise RequestError(400, "알 수 없는 입력 방식")
    if mode == "gold-echo":
        raise RequestError(400, "정답 대입 모드는 합성 묶음에서만 쓸 수 있습니다. 직접 입력은 수동 입력 모드로 진행하십시오")
    docs, pasted = [], {}
    for doc_id, doc_type, _ in DOC_SLOTS:
        text = (_one(form, f"doc_{doc_id}") or "").replace("\r\n", "\n")
        if text.strip():
            if len(text) > MAX_DOCUMENT_CHARS:
                raise RequestError(400, "문서 길이 제한 초과")
            pasted[doc_id] = text
            docs.append(Document(doc_id, 1, text, doc_type=doc_type,
                                 origin="synthetic" if is_deploy() else "internal"))
    if not docs:
        raise RequestError(400, "문서 원문을 하나 이상 입력하십시오")
    return Inputs(mode, None, docs, pasted)


def understand(inputs: Inputs, bundles: dict[str, dict]):
    if inputs.mode == "gold-echo":
        transport = gold_echo_transport(bundles[inputs.bundle_id])
        cfg = EndpointConfig(model="gold-echo", per_document=True)
    elif inputs.mode == "manual":
        transport, cfg = manual_transport, EndpointConfig(model="manual", per_document=True)
    else:
        if is_deploy():  # parse_inputs가 이미 막지만 이중으로 막는다
            raise RequestError(403, "시연 미러에서는 live 모드를 사용할 수 없습니다")
        transport, cfg = None, EndpointConfig.from_env()
    return DocumentUnderstanding(cfg, transport=transport).understand(inputs.documents)


# --------------------------------------------------------------------------
# 응답
# --------------------------------------------------------------------------
@dataclass
class Response:
    status: int
    headers: list[tuple[str, str]]
    body: bytes


def _resp(status: int, body: str | bytes, ctype: str = "text/html; charset=utf-8") -> Response:
    data = body.encode("utf-8") if isinstance(body, str) else body
    headers = [("Content-Type", ctype), ("Content-Length", str(len(data))),
               ("X-Robots-Tag", ROBOTS), ("X-Content-Type-Options", "nosniff"),
               ("Referrer-Policy", "no-referrer"), ("Cache-Control", "no-store"),
               ("Content-Security-Policy",
                "default-src 'self'; style-src 'self'; img-src 'self' data:; "
                "form-action 'self'; frame-ancestors 'none'; base-uri 'none'")]
    return Response(status, headers, data)


e = html.escape


def _one(form: dict[str, list[str]], key: str) -> str | None:
    v = form.get(key)
    return v[0] if v else None


def _ko_field(name: str) -> str:
    return FIELD_KO.get(name, name)


def _ko_value(name: str, value: Any) -> str:
    if name in ENUM_KO and value in ENUM_KO[name]:
        return ENUM_KO[name][value]
    if isinstance(value, int) and name.endswith("_krw"):
        return f"{value:,}원"
    return str(value)


def _ko_text(text: str) -> str:
    for k, v in FIELD_KO.items():
        text = text.replace(f"'{k}'", f"'{v}'")
    return text


def page(title: str, body: str, *, gold: bool = False) -> str:
    deploy = is_deploy()
    nav = ('<nav><a href="/">새 검토</a><a href="/runs">실행 기록</a></nav>')
    gold_html = f'<p class="badge gold">{e(GOLD_BADGE)}</p>' if gold else ""
    banner = (f'<div class="banner" role="note">{e(BANNER)}</div>' if deploy else
              '<div class="banner local" role="note">폐쇄망 로컬 실행 · 127.0.0.1 전용</div>')
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{e(title)} · MIL-Check</title>
<link rel="stylesheet" href="/static/style.css"></head>
<body>{banner}
<header><span class="brand">MIL-Check</span> <span class="sub">계약 사전검토 웹 MVP</span>{nav}</header>
<main>{gold_html}{body}</main>
<footer>사전검토 지원 도구입니다. 적법성을 확정하거나 계약담당공무원의 최종 판단을 대체하지 않습니다.</footer>
</body></html>"""


STEPS = ["문서 입력", "문서 이해", "담당자 확인", "규칙 판정", "판정·근거", "사전검토 결과서"]


def steps(active: int) -> str:
    items = "".join(f'<li class="{"on" if i <= active else ""}">{e(s)}</li>' for i, s in enumerate(STEPS))
    return f'<ol class="steps">{items}</ol>'


def error_page(status: int, message: str) -> Response:
    body = f'<h1>요청을 처리할 수 없습니다</h1><p class="err">{e(message)}</p><p><a href="/">처음으로</a></p>'
    return _resp(status, page("오류", body))


# --------------------------------------------------------------------------
# 화면 1: 입력
# --------------------------------------------------------------------------
def index_page(bundles: dict[str, dict]) -> str:
    modes = "".join(
        f'<label class="opt"><input type="radio" name="mode" value="{m}"{" checked" if m == "gold-echo" else ""}> '
        f'{e(MODE_KO[m])}</label>' for m in allowed_modes())
    rows = []
    for i, (bid, b) in enumerate(bundles.items()):
        types = ", ".join(REQ_KO.get(d["doc_type"], dict((t, k) for _, t, k in DOC_SLOTS).get(d["doc_type"], d["doc_type"]))
                          for d in b["documents"])
        rows.append(f'<label class="bundle"><input type="radio" name="bundle_id" value="{e(bid)}"'
                    f'{" checked" if i == 0 else ""}> <b>{e(bid)}</b> {e(b.get("note", ""))}'
                    f' <span class="muted">({e(types)})</span></label>')
    paste = "".join(
        f'<label class="doc">{e(label)} <span class="muted">({e(doc_id)})</span>'
        f'<textarea name="doc_{doc_id}" rows="3" maxlength="{MAX_DOCUMENT_CHARS}"></textarea></label>'
        for doc_id, _, label in DOC_SLOTS)
    deploy_note = ('<p class="muted">시연 미러: 정답 대입·수동 입력만 제공합니다. 외부 LLM·외부 API를 호출하지 않으며 '
                   'PDF 입력은 꺼져 있습니다.</p>' if is_deploy() else "")
    return page("문서 입력", f"""{steps(0)}
<h1>1. 문서 입력</h1>{deploy_note}
<form method="post" action="/understand">
<fieldset><legend>실행 모드</legend>{modes}
<p class="muted">정답 대입 모드는 합성 묶음의 정답을 모델 응답 자리에 넣어 화면 흐름만 보여 줍니다. 모델 성능이 아닙니다.</p></fieldset>
<fieldset><legend>합성 예시 묶음 (eval/understanding_dev.jsonl)</legend>
<input type="hidden" name="source" value="bundle">{"".join(rows)}
<button type="submit">문서 이해 실행</button></fieldset>
</form>
<form method="post" action="/understand">
<fieldset><legend>직접 붙여넣기 (수동 입력 모드)</legend>
<p class="warn">{e(INPUT_WARNING)}</p>
<p class="muted">붙여넣은 원문은 저장하지 않습니다. 다음 화면으로 넘어갈 때 브라우저 양식으로만 되돌아갑니다.</p>
<input type="hidden" name="source" value="paste"><input type="hidden" name="mode" value="manual">
{paste}<button type="submit">수동 입력으로 진행</button></fieldset>
</form>""")


# --------------------------------------------------------------------------
# 화면 2: 문서 이해 결과 + 담당자 확인
# --------------------------------------------------------------------------
def _hidden_inputs(inputs: Inputs) -> str:
    h = [f'<input type="hidden" name="mode" value="{e(inputs.mode)}">']
    if inputs.bundle_id:
        h += ['<input type="hidden" name="source" value="bundle">',
              f'<input type="hidden" name="bundle_id" value="{e(inputs.bundle_id)}">']
    else:
        h.append('<input type="hidden" name="source" value="paste">')
        h += [f'<input type="hidden" name="doc_{e(k)}" value="{e(v)}">' for k, v in inputs.pasted.items()]
    return "".join(h)


def _manual_input(name: str) -> str:
    spec = FIELD_SPECS[name]
    if spec["type"] == "enum":
        opts = "".join(f'<option value="{e(v)}">{e(ENUM_KO.get(name, {}).get(v, v))}</option>'
                       for v in spec["values"] if v != "unknown")
        return f'<select name="m:{name}"><option value=""></option>{opts}</select>'
    kind = "text" if spec["type"] in {"str", "date"} else "text\" inputmode=\"numeric"
    return f'<input type="{kind}" name="m:{name}" maxlength="200">'


def confirm_page(inputs: Inputs, result, bundles: dict[str, dict]) -> str:
    sheet = review_sheet(result)
    gold = inputs.mode == "gold-echo"
    docs = "".join(
        f'<details><summary>{e(d.document_id)} · {e(dict((t, k) for _, t, k in DOC_SLOTS).get(d.doc_type, d.doc_type))}</summary>'
        f'<pre>{e(d.text)}</pre></details>' for d in inputs.documents)
    status_ko = {"ok": "정상", "partial": "일부 후보 거부", "unavailable": "자동 추출 불가 — 수동 입력"}
    err = result.run_record.get("error")

    rows = []
    shown = list(dict.fromkeys(list(sheet["fields"]) + [f for f in FIELD_SPECS if f not in sheet["fields"]]))
    for name in shown:
        cands = sheet["fields"].get(name, [])
        conflict = name in sheet["conflicts"]
        opts = []
        for i, c in enumerate(cands):
            src = "; ".join(f'{s["document_id"]}: “{s["quote"]}”' for s in c["sources"])
            checked = " checked" if (i == 0 and not conflict) else ""
            opts.append(f'<label class="opt"><input type="radio" name="f:{name}" value="{e(c["fact_id"])}"{checked}> '
                        f'<b>{e(_ko_value(name, c["value"]))}</b> <span class="muted">{e(src)}</span></label>')
        none_checked = " checked" if (not cands or conflict) else ""
        opts.append(f'<label class="opt"><input type="radio" name="f:{name}" value="__manual__"> 직접 입력 '
                    f'{_manual_input(name)}</label>')
        opts.append(f'<label class="opt"><input type="radio" name="f:{name}" value=""{none_checked}> '
                    f'<span class="muted">확정하지 않음</span></label>')
        cls = "field conflict" if conflict else "field"
        tag = '<span class="badge warn">충돌 — 선택 필요</span>' if conflict else ""
        rows.append(f'<div class="{cls}"><div class="fname">{e(_ko_field(name))} {tag}</div>{"".join(opts)}</div>')

    qs = "".join(
        f'<li><span class="badge">{e({"missing": "누락", "conflict": "충돌", "unsupported": "입증 미확인"}.get(q["reason"], q["reason"]))}</span> '
        f'{e(_ko_text(q["text"]))}<br><input type="text" name="q:{e(q["question_id"])}" maxlength="300" '
        f'placeholder="담당자 답변(선택). 답하면 결과서에서 남은 질문에서 빠집니다"></li>'
        for q in sheet["questions"]) or "<li>없음</li>"
    ev_hint = {}
    for ev in sheet["evidence"]:
        for item in ev["rule_items_to_check"]:
            ev_hint[item] = f'{REQ_KO.get(ev["requirement_id"], ev["requirement_id"])} {PRESENCE_KO.get(ev["presence"], ev["presence"])}'
    evid = "".join(
        f'<label class="opt"><input type="checkbox" name="evidence" value="{e(k)}"> {e(EVIDENCE_KO.get(k, k))}'
        + (f' <span class="muted">(참고: {e(ev_hint[k])})</span>' if k in ev_hint else "") + "</label>"
        for k in evidence_keys())
    presence = ", ".join(f'{REQ_KO.get(x["requirement_id"], x["requirement_id"])} {PRESENCE_KO.get(x["presence"], x["presence"])}'
                         for x in sheet["evidence"])
    cat = "".join(f'<option value="{k}"{" selected" if k == "goods" else ""}>{e(v)}</option>' for k, v in CATEGORY_KO.items())
    prop = "".join(f'<option value="{k}"{" selected" if k == "sole_source" else ""}>{e(v)}</option>' for k, v in PROPOSED_KO.items())
    extra = "".join(
        f'<label class="opt">{e(label)} <select name="x:{k}"><option value="">미입력</option>'
        f'<option value="yes">예</option><option value="no">아니오</option></select></label>'
        for k, label in EXTRA_BOOL.items())
    extra += "".join(
        f'<label class="opt">{e(label)} <select name="x:{k}"><option value="">미입력</option>'
        + "".join(f'<option value="{e(v)}">{e(t)}</option>' for v, t in values.items()) + "</select></label>"
        for k, (label, values) in EXTRA_SELECT.items())
    reviewer = (f'<p>확인자: <b>{e(DEMO_REVIEWER)}</b> <span class="muted">(시연 미러에서는 이름을 받지 않습니다)</span></p>'
                if is_deploy() else
                f'<label class="opt">확인자 <input type="text" name="reviewer" value="담당자" maxlength="40" required></label>')

    body = f"""{steps(2)}
<h1>2. 문서 이해 결과 · 3. 담당자 확인</h1>
<p>실행 모드: <b>{e(MODE_KO[inputs.mode])}</b>{f" · 묶음 {e(inputs.bundle_id)}" if inputs.bundle_id else ""}
 · 문서 이해 상태: <b>{e(status_ko.get(result.component_status, result.component_status))}</b></p>
{f'<p class="muted">{e(err)}</p>' if err else ""}
<p class="note">{e(sheet["note"])} 충돌 필드는 반드시 하나를 고르거나 직접 입력해야 판정으로 넘어갑니다.</p>
<section><h2>입력 문서</h2>{docs}<p class="muted">첨부 확인(참고 표시): {e(presence) or "없음"}</p></section>
<form method="post" action="/review">{_hidden_inputs(inputs)}
<section><h2>사실 후보 확정</h2>{"".join(rows)}</section>
<section><h2>검토 유형 (담당자 선택)</h2>
<label class="opt">계약 대상 <select name="contract_category">{cat}</select></label>
<label class="opt">검토 중인 계약 방식 <select name="proposed_type">{prop}</select></label>
<label class="opt">부가세 제외 추정가격(원) <input type="text" inputmode="numeric" name="estimated_price_krw_ex_vat" maxlength="20"
 placeholder="부가세 포함·미상이면 직접 입력. 자동 환산하지 않습니다"></label>
{extra}</section>
<section><h2>증빙 확인 (담당자 체크로만 인정)</h2><p class="muted">첨부 여부는 참고 표시일 뿐입니다. 내용을 확인한 항목만 체크하십시오.</p>{evid}</section>
<section><h2>남은 질문</h2><ul class="qs">{qs}</ul></section>
<section>{reviewer}<button type="submit">규칙 판정 실행</button></section>
</form>"""
    return page("담당자 확인", body, gold=gold)


def evidence_keys() -> list[str]:
    """규칙팩이 요구하는 증빙 항목 전체(화면 체크 목록)."""
    rules = json.loads((ROOT / "data" / "rules.json").read_text(encoding="utf-8"))
    keys: set[str] = set(EVIDENCE_KO)

    def walk(node, parent=""):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, k)
        elif isinstance(node, list) and "evidence" in parent:
            keys.update(x for x in node if isinstance(x, str) and x.isidentifier())
    walk(rules)
    return sorted(keys, key=lambda k: EVIDENCE_KO.get(k, "힣" + k))


# --------------------------------------------------------------------------
# 화면 3: 판정 결과서
# --------------------------------------------------------------------------
def _parse_int(raw: str, label: str) -> int:
    s = raw.replace(",", "").replace("원", "").strip()
    if not s.isdigit():
        raise ConfirmationError(f"{label}: 숫자만 입력하십시오")
    return int(s)


def build_confirmation(form: dict[str, list[str]], result) -> Confirmation:
    fields: dict[str, dict[str, Any]] = {}
    for name in FIELD_SPECS:
        choice = _one(form, f"f:{name}")
        if not choice:
            continue
        if choice == "__manual__":
            raw = (_one(form, f"m:{name}") or "").strip()
            if not raw:
                raise ConfirmationError(f"{_ko_field(name)}: 직접 입력 값이 비어 있습니다")
            spec = FIELD_SPECS[name]
            if spec["type"] in {"int", "int_krw"}:
                value: Any = _parse_int(raw, _ko_field(name))
            elif spec["type"] == "enum":
                if raw not in spec["values"]:
                    raise ConfirmationError(f"{_ko_field(name)}: 허용되지 않은 값")
                value = raw
            else:
                value = raw[:200]
            fields[name] = {"manual": value}
        else:
            fields[name] = {"fact_id": choice}
    price_raw = (_one(form, "estimated_price_krw_ex_vat") or "").strip()
    price = _parse_int(price_raw, "부가세 제외 추정가격") if price_raw else None
    extra: dict[str, Any] = {}
    for k in EXTRA_BOOL:
        v = _one(form, f"x:{k}")
        if v in {"yes", "no"}:
            extra[k] = v == "yes"
    for k, (_, values) in EXTRA_SELECT.items():
        v = _one(form, f"x:{k}")
        if v in values:
            extra[k] = v
    answered = {}
    valid_q = {q.question_id for q in result.questions}
    for key, vals in form.items():
        if key.startswith("q:") and key[2:] in valid_q and vals and vals[0].strip():
            answered[key[2:]] = vals[0].strip()[:300]
    evidence = [x for x in form.get("evidence", []) if x in set(evidence_keys())]
    reviewer = DEMO_REVIEWER if is_deploy() else (_one(form, "reviewer") or "").strip()[:40]
    return Confirmation(reviewer=reviewer, contract_category=_one(form, "contract_category") or "",
                        proposed_type=_one(form, "proposed_type") or "", fields=fields,
                        estimated_price_krw_ex_vat=price, evidence_checked=evidence,
                        extra_case=extra, answered_questions=answered)


DECISION_CLASS = {"PASS_WITH_CONTROLS": "pass", "NEEDS_EVIDENCE": "warn", "REJECT_GROUND": "bad",
                  "OUT_OF_SCOPE": "info", "NEEDS_INPUT": "info"}


def report_page(report: dict[str, Any], mode: str) -> str:
    r = report["review"]
    df = report["document_findings"]
    gold = mode == "gold-echo"
    md = render_markdown(report)
    if gold:
        md = f"> {GOLD_BADGE}\n\n" + md
    findings = "".join(
        f'<tr><td><span class="sev {e(f["severity"])}">{e(SEVERITY_KO.get(f["severity"], f["severity"]))}</span></td>'
        f'<td>{e(f["message"])}</td><td>{e(f["legal_basis"])}</td><td><code>{e(f["code"])}</code></td></tr>'
        for f in r["findings"])
    missing = "".join(f"<li>{e(EVIDENCE_KO.get(m, m))}</li>" for m in r["missing_evidence"]) or "<li>없음</li>"
    controls = "".join(f"<li>{e(c)}</li>" for c in r["controls"]) or "<li>없음</li>"
    prov = "".join(
        f'<li>{e(_ko_field(p["field"]))}: {e(SOURCE_KO.get(p["source"], p["source"]))}'
        + (f' (문서 {e(", ".join(p.get("documents", [])))})' if p.get("documents") else "") + "</li>"
        for p in report["provenance"]) or "<li>없음</li>"
    signals = "".join(
        f'<li>{e(s["message"]) if isinstance(s, dict) and "message" in s else e(str(s))}</li>'
        for s in r.get("advisory", {}).get("signals", [])) or "<li>없음</li>"
    sources = "".join(
        f'<li>{e(s.get("title") or "")} <span class="muted">{e(s.get("source_date") or "일자 미상")}</span></li>'
        for s in r.get("retrieved_sources", [])[:5]) or "<li>없음</li>"
    openq = "".join(f"<li>{e(_ko_text(q['text']))}</li>" for q in df["open_questions"]) or "<li>없음</li>"
    v = report["versions"]
    body = f"""{steps(5)}
<h1>사전검토 결과서 {e(report["report_no"])}</h1>
<section class="decision {DECISION_CLASS.get(r["decision"], "info")}">
<div class="dlabel">4. 규칙 판정</div>
<div class="dvalue">{e(DECISION_KO.get(r["decision"], r["decision"]))}</div>
<div>품명: {e(str(r.get("item_name") or "-"))} · 근거: {e(r["legal_ground"])} · 확인자: {e(report["reviewer"])}</div></section>
<section><h2>5. 판정·근거</h2>
<table><thead><tr><th>구분</th><th>점검 내용</th><th>근거</th><th>규칙</th></tr></thead><tbody>{findings}</tbody></table>
<h3>보완 필요 증빙</h3><ul>{missing}</ul>
<h3>통제 조건</h3><ul>{controls}</ul>
<h3>문서 대조</h3><ul>
<li>담당자가 해소한 충돌: {e(", ".join(_ko_field(f) for f in df["conflicts_resolved"]) or "없음")}</li>
<li>첨부 확인: {e(", ".join(f"{REQ_KO.get(k, k)} {PRESENCE_KO.get(x, x)}" for k, x in df["evidence_presence"].items()) or "없음")}</li>
<li>남은 질문<ul>{openq}</ul></li></ul>
<h3>확정값 출처</h3><ul>{prov}</ul>
<h3>참고 신호 (판정 변경 불가)</h3><ul>{signals}</ul>
<h3>검색된 공개 근거</h3><ul>{sources}</ul></section>
<section><h2>6. 사전검토 결과서 (Markdown)</h2><pre class="md">{e(md)}</pre>
<p class="muted">버전: 흐름 {e(v["workflow"])} / 문서 이해 {e(v["extractor"])} / 규칙 기준일 {e(str(v["rules_current_as_of"]))} / 모델 {e(str(v["model"]))}
 · 기록 확인값 <code>{e(report["record_sha256"][:16])}…</code></p>
<p>{e(r["disclaimer"])}</p>
<p><a href="/runs/{e(report["report_no"])}">실행 기록 보기</a> · <a href="/">새 검토</a></p></section>"""
    return page(f"결과서 {report['report_no']}", body, gold=gold)


# --------------------------------------------------------------------------
# 실행 기록
# --------------------------------------------------------------------------
def _store():
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not is_deploy() and path.parent == ROOT / "runs":
        ignore = path.parent / ".gitignore"  # 로컬 실행 기록이 커밋되지 않게(루트 .gitignore는 기준선 파일)
        if not ignore.exists():
            ignore.write_text("*\n", encoding="utf-8")
    return open_store(path)


def runs_notice() -> str:
    vol = f'<p class="warn">{e(VOLATILE_NOTICE)}</p>' if is_deploy() else ""
    return f'{vol}<p class="muted">{e(SAME_DB_LIMIT)}</p><p class="muted">기록에는 문서 원문이 없고 해시·확정값·판정만 남습니다.</p>'


def runs_page() -> str:
    store = _store()
    try:
        rows = store.execute("SELECT report_no, created_at, reviewer, decision FROM runs "
                             "ORDER BY created_at DESC LIMIT 100").fetchall()
    finally:
        store.close()
    trs = "".join(f'<tr><td><a href="/runs/{e(n)}">{e(n)}</a></td><td>{e(t[:19].replace("T", " "))}</td>'
                  f'<td>{e(rv)}</td><td>{e(DECISION_KO.get(d, d))}</td></tr>' for n, t, rv, d in rows)
    table = (f'<table><thead><tr><th>결과서 번호</th><th>시각(UTC)</th><th>확인자</th><th>판정</th></tr></thead>'
             f'<tbody>{trs}</tbody></table>' if rows else "<p>기록이 없습니다.</p>")
    return page("실행 기록", f"<h1>실행 기록</h1>{runs_notice()}{table}")


def run_detail_page(report_no: str) -> Response:
    store = _store()
    try:
        got = lookup(store, report_no)
    finally:
        store.close()
    if not got:
        return error_page(404, "해당 기록이 없습니다")
    ok = got["integrity_ok"]
    body = (f'<h1>실행 기록 {e(report_no)}</h1>{runs_notice()}'
            f'<p>무결성 확인: <b class="{"okc" if ok else "badc"}">{"일치" if ok else "불일치 — 기록이 바뀌었습니다"}</b></p>'
            f'<pre>{e(json.dumps(got["record"], ensure_ascii=False, indent=2))}</pre>'
            f'<p><a href="/runs">목록</a></p>')
    return _resp(200, page(f"기록 {report_no}", body))


# --------------------------------------------------------------------------
# 진입점
# --------------------------------------------------------------------------
def handle(method: str, path: str, body: bytes = b"") -> Response:
    """HTTP와 무관한 요청 처리. 모든 응답에 noindex 헤더가 붙는다."""
    path = path.split("?", 1)[0] or "/"
    try:
        if method in {"GET", "HEAD"}:
            if path == "/":
                return _resp(200, index_page(load_bundles()))
            if path == "/healthz":
                return _resp(200, "ok", "text/plain; charset=utf-8")
            if path == "/robots.txt":
                return _resp(200, "User-agent: *\nDisallow: /\n", "text/plain; charset=utf-8")
            if path == "/static/style.css":
                return _resp(200, (STATIC_DIR / "style.css").read_bytes(), "text/css; charset=utf-8")
            if path == "/runs":
                return _resp(200, runs_page())
            if path.startswith("/runs/"):
                return run_detail_page(path[len("/runs/"):])
            return error_page(404, "없는 페이지입니다")
        if method == "POST":
            if len(body) > MAX_BODY_BYTES:
                return error_page(413, "요청이 너무 큽니다")
            form = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
            bundles = load_bundles()
            if path == "/understand":
                inputs = parse_inputs(form, bundles)
                result = understand(inputs, bundles)
                return _resp(200, confirm_page(inputs, result, bundles))
            if path == "/review":
                inputs = parse_inputs(form, bundles)
                # 원문은 저장하지 않으므로 같은 입력으로 문서 이해를 다시 수행한다(gold-echo·manual은 결정론적).
                result = understand(inputs, bundles)
                conf = build_confirmation(form, result)
                store = _store()
                try:
                    report = run_case(result, conf, get_agent(), store,
                                      case_id=inputs.bundle_id or "PASTE")
                finally:
                    store.close()
                return _resp(200, report_page(report, inputs.mode))
            return error_page(404, "없는 페이지입니다")
        return error_page(405, "지원하지 않는 요청 방식입니다")
    except RequestError as exc:
        return error_page(exc.status, str(exc))
    except ConfirmationError as exc:
        return error_page(400, f"담당자 확인이 규칙에 맞지 않습니다: {exc}. 뒤로 가서 수정하십시오.")
