"""웹 MVP: 문서 입력 → 추출 항목 확인(담당자) → 규칙 판정·근거 → 사전검토 결과서.

HTTP 서버와 분리된 순수 함수 `handle()`만 둔다. 로컬 서버(web_app.py)와 Vercel 어댑터
(api/index.py)는 모두 이 함수를 호출하는 얇은 껍데기다. 판정 로직은 새로 만들지 않고
milcheck.workflow / milcheck.understanding / milcheck.document_input / MilCheckAgent를 그대로 쓴다.

입력: 텍스트 PDF 업로드(document_input.from_pdf, 디스크에 쓰지 않음) 또는 내용 직접 입력.

추출 모드
  live    승인된 로컬(루프백) OpenAI 호환 서버로 후보 추출. 로컬 실행 기본값.
          실패하면 '자동 추출 실패' 화면에서 수동 입력으로 전환할 수 있다.
  manual  모델을 호출하지 않는다. 담당자가 원문을 보며 값을 직접 입력한다.

배포 구분: 환경변수 MILCHECK_DEPLOY=vercel 일 때만 공개 시연 미러 제한을 적용한다
  (manual만 허용·live 거부, 확인자 이름 고정, 실행 기록은 임시 경로, 고정 배너). 그 외에는 로컬 동작.

원문 보관 금지: 문서 원문은 요청 처리 중 메모리에만 있고, 다음 화면으로는 브라우저 양식(hidden)으로만
  되돌아간다. 실행 기록 DB에는 workflow.run_case가 남기는 해시·확인값만 저장된다.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import io
import json
import os
import re
import secrets
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field, replace
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from .agent import DECISION_KO
from .document_input import DocumentInputError, from_pdf, from_text
from .understanding import (EVIDENCE_REQUIREMENTS, MAX_DOCUMENT_CHARS, CandidateFact, Document,
                            DocumentUnderstanding, EndpointConfig, EvidenceAssessment,
                            ReviewQuestion, SourceRef, UnderstandingError, UnderstandingResult)
from .workflow import (EVIDENCE_HINTS, EVIDENCE_KO, Confirmation, ConfirmationError, lookup,
                       open_store, run_case)

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "web_static"
MAX_BODY_BYTES = 4 * 1024 * 1024       # Vercel 요청 본문 한도(4.5MB) 안쪽
MAX_FILES = 6

BANNER = "공개 시연용 미러 · 합성 데이터만 사용 · 외부 LLM 미사용 · 실제 운용은 폐쇄망 로컬 실행"
INPUT_WARNING = "실제 계약 문서·개인정보·군 내부 자료 입력 금지"
VOLATILE_NOTICE = "시연용 미러에서는 기록이 서버 재시작 시 사라집니다"
SAME_DB_LIMIT = ("한계: 실행 기록과 그 확인값(SHA-256)이 같은 DB에 저장됩니다. "
                 "기록 한 건만 바뀐 경우는 탐지하지만, DB 전체를 다시 쓰는 변조는 탐지하지 못합니다.")
FINAL_NOTICE = "이 결과는 담당자가 확정한 입력에 대한 규칙 검사입니다. 계약 체결 여부의 최종 결정은 담당자에게 있습니다."
DEMO_REVIEWER = "시연 담당자"
ROBOTS = "noindex, nofollow"

DOC_TYPE_KO = {"request": "구매요구서", "quote": "견적서", "supplier_confirmation": "공급자 확인서",
               "compatibility_statement": "호환성 사유서", "other": "기타 문서"}
REVIEW_TYPES = {  # 검토 유형(담당자 선택) → (계약 대상, 검토 방식)
    "goods_sole": ("물품 구매 · 수의계약 (호환성 · 단독공급 사유)", "goods", "sole_source"),
    "goods_small": ("물품 구매 · 소액 수의계약", "goods", "small_amount"),
    "goods_urgent": ("물품 구매 · 긴급·보안 수의계약", "goods", "urgent_security"),
    "service_sole": ("용역 · 수의계약 (특정 기술·자격 사유)", "service", "sole_source"),
}
# 추출 항목 확인 표: (필드, 표시 이름, 용도)
FIELD_ROWS = [("item_name", "품명", "판정 입력"),
              ("sole_source_basis", "수의계약 근거", "판정 입력 · 핵심"),
              ("estimated_price_krw", "추정가격", "판정 입력 · 핵심"),
              ("tax_status", "부가세 여부", "판정 입력 · 핵심"),
              ("total_amount_krw", "견적 합계", "참고"),
              ("supplier_name", "업체명", "참고"),
              ("quote_count", "견적 수", "판정 입력"),
              ("existing_equipment", "기존 장비", "참고")]
# 기본 표에 없더라도 후보가 나오면 행을 추가해 확인·충돌 선택을 받는다(FIELD_SPECS 전체 대응).
EXTRA_FIELD_ROWS = [("quantity", "수량", "참고"), ("unit_price_krw", "단가", "참고"),
                    ("quote_status", "견적 접수 여부", "참고"), ("delivery_deadline", "납기", "참고")]
FIELD_LABEL = {f: label for f, label, _ in FIELD_ROWS + EXTRA_FIELD_ROWS}
INT_FIELDS = {"estimated_price_krw", "total_amount_krw", "quote_count", "quantity", "unit_price_krw"}
ENUM_KO = {
    "tax_status": {"vat_excluded": "부가세 별도(제외)", "vat_included": "부가세 포함"},
    "quote_status": {"received": "견적 접수", "planned": "견적 예정", "none": "견적 없음"},
    "sole_source_basis": {"compatibility": "호환성", "patented_no_substitute": "특허·대체품 없음",
                          "original_supplier_direct_service": "제조·공급자 직접 설치·정비",
                          "single_supplier": "단일 업체만 공급",
                          "specific_technical_service": "특정 기술·자격 용역"},
}
EXTRA_EVIDENCE_KO = {
    "unforeseeable_event_record": "예측 불가 사건 기록", "immediate_deadline_record": "즉시 이행 기한 기록",
    "competition_time_infeasible": "경쟁 절차 시간 부족 근거", "security_basis_record": "보안 근거 기록",
    "scope_limited_to_security_need": "보안 필요 범위 한정 확인",
    "supplier_security_safeguards": "업체 보안 조치 확인",
}
EXTRA_FACTS = {  # 검토 방식별 담당자 판단 사실(규칙 엔진 입력, 문서 이해 후보 아님)
    "sole_source": [("alternatives_exist", "대체 가능한 제품·업체가 있음", {"yes": "예", "no": "아니오"})],
    "small_amount": [("small_amount_basis", "소액수의 기준", {"general": "일반", "special_knowledge": "특수 지식·기술",
                                                          "youth_startup": "청년창업기업",
                                                          "supported_enterprise": "지원 대상 기업"}),
                     ("split_contract_risk", "동일 사업 분할 발주 가능성", {"yes": "예", "no": "아니오"}),
                     ("electronic_quotes_planned", "전자견적 계획", {"yes": "예", "no": "아니오"})],
    "urgent_security": [("urgent_security_basis", "긴급·보안 구분", {"urgent": "긴급", "security": "보안"}),
                        ("self_created_urgency", "긴급성이 내부 사정(예산소진·행정지연)으로 생김",
                         {"yes": "예", "no": "아니오"})],
}
BOOL_EXTRAS = {"alternatives_exist", "split_contract_risk", "electronic_quotes_planned", "self_created_urgency"}
REQ_KEYWORDS = {"EV-QUOTE": ("견적",), "EV-SUPPLIER": ("공급", "단독", "유일"), "EV-COMPAT": ("호환",)}
SUPPORT_KO = {"supported": "내용 확인", "unsupported": "입증 부족", "conflicting": "상충", "unknown": "판단 불가"}
FINDING_KO = {"SOLE-EVIDENCE": "사유 입증 서류", "SOLE-BASIS": "수의계약 세부 근거",
              "SOLE-ALTERNATIVE": "대체품·경쟁 가능성", "INPUT-PRICE": "추정가격 입력",
              "INPUT-INCOMPLETE": "필수 입력", "SCOPE-001": "판정 범위", "SCOPE-002": "판정 범위",
              "ADDITIONAL-EVIDENCE": "유형별 필수 증빙"}
SEVERITY_BADGE = {"pass": ("충족", "ok"), "warning": ("보완 필요", "warn"),
                  "critical": ("부적합", "bad"), "info": ("참고", "muted")}
DECISION_VIEW = {  # 배너 제목, 색, 한 줄 설명
    "PASS_WITH_CONTROLS": ("요건 충족", "ok", "모든 규칙 검사를 통과했습니다. 통제 조건을 이행하고 담당자 최종 확인 후 진행하세요."),
    "NEEDS_EVIDENCE": ("보완 필요", "warn", "필수 증빙 또는 절차가 부족합니다. 보완 요청서를 생성할 수 있습니다."),
    "REJECT_GROUND": ("사유 부적합", "bad", "제안한 수의계약 사유와 충돌하는 사실이 있습니다. 경쟁 절차 또는 다른 근거를 검토하세요."),
    "OUT_OF_SCOPE": ("판단 보류", "muted", "현재 규칙팩 판정 범위 밖입니다. 전문 검토가 필요합니다."),
    "NEEDS_INPUT": ("판단 보류", "muted", "판정에 필요한 입력이 부족해 판정을 확정하지 않았습니다."),
}


# --------------------------------------------------------------------------
# 환경
# --------------------------------------------------------------------------
def is_deploy() -> bool:
    return os.environ.get("MILCHECK_DEPLOY", "").strip().lower() == "vercel"


def allowed_modes() -> tuple[str, ...]:
    return ("manual",) if is_deploy() else ("live", "manual")


def db_path() -> Path:
    """시연 미러는 쓰기 가능한 임시 경로(휘발성), 로컬은 runs/ 아래."""
    if is_deploy():
        return Path(tempfile.gettempdir()) / "milcheck_demo_runs.sqlite"
    env = os.environ.get("MILCHECK_WEB_DB")
    return Path(env) if env else ROOT / "runs" / "web_runs.sqlite"


_agent = None
_agent_lock = threading.Lock()
_STATE_KEY = (os.environ.get("MILCHECK_WEB_SECRET") or secrets.token_hex(32)).encode()


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


def live_config() -> EndpointConfig:
    """MILCHECK_LLM_* 환경변수 → EndpointConfig. 값이 잘못되면 LLMConfigError(예외 대신 안내·수동 전환)."""
    try:
        return EndpointConfig.from_env()
    except (ValueError, TypeError) as exc:  # int('oops'), json 해석 실패 등
        raise LLMConfigError(f"LLM 설정 오류({type(exc).__name__}): MILCHECK_LLM_* 환경변수 값을 확인하십시오") from None


def model_label(mode: str) -> str:
    if mode == "manual":
        return "LLM 미사용(수동 입력)"
    try:
        return live_config().model
    except LLMConfigError:
        return "설정 오류"


def rules_meta() -> dict[str, Any]:
    return json.loads((ROOT / "data" / "rules.json").read_text(encoding="utf-8"))


class LLMConfigError(Exception):
    """LLM 연결 환경변수가 잘못됨. 웹 요청은 500이 아니라 '자동 추출 실패' 화면으로 안내한다."""


class RequestError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------
# 입력 해석
# --------------------------------------------------------------------------
@dataclass
class DocInfo:
    document_id: str
    name: str
    doc_type: str
    text: str
    pages: list[list[int]] = field(default_factory=list)  # [쪽, 시작, 끝]

    def document(self) -> Document:
        return Document(self.document_id, 1, self.text, doc_type=self.doc_type,
                        origin="synthetic" if is_deploy() else "internal",
                        media_type="application/pdf" if self.pages else "text/plain")


def guess_doc_type(name: str) -> str:
    for key, kind in (("견적", "quote"), ("호환", "compatibility_statement"), ("공급", "supplier_confirmation"),
                      ("확인서", "supplier_confirmation"), ("요구", "request"), ("요청", "request")):
        if key in name:
            return kind
    return "request"


def parse_form(body: bytes, content_type: str) -> tuple[dict[str, list[str]], list[tuple[str, bytes]]]:
    """urlencoded 또는 multipart 본문 → (필드, 업로드 파일[(이름, 바이트)])."""
    if content_type.lower().startswith("multipart/form-data"):
        msg = BytesParser(policy=policy.HTTP).parsebytes(
            b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body)
        if not msg.is_multipart():
            raise RequestError(400, "양식 형식 오류")
        fields: dict[str, list[str]] = {}
        files: list[tuple[str, bytes]] = []
        for part in msg.iter_parts():
            name = part.get_param("name", header="content-disposition")
            data = part.get_payload(decode=True) or b""
            filename = part.get_filename()
            if filename is not None:
                if data:
                    files.append((Path(filename).name[:120], data))
            elif name:
                fields.setdefault(name, []).append(data.decode("utf-8", errors="replace"))
        return fields, files
    return parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True), []


def _one(form: dict[str, list[str]], key: str) -> str | None:
    v = form.get(key)
    return v[0] if v else None


def docs_from_upload(form, files) -> list[DocInfo]:
    if len(files) > MAX_FILES:
        raise RequestError(400, f"문서는 {MAX_FILES}건까지 올릴 수 있습니다")
    docs: list[DocInfo] = []
    for i, (name, data) in enumerate(files):
        doc_type = _one(form, f"doctype_{i}") or guess_doc_type(name)
        if doc_type not in DOC_TYPE_KO:
            raise RequestError(400, "알 수 없는 문서 유형")
        doc_id = f"D{i + 1}"
        try:
            if name.lower().endswith(".pdf") or data[:5] == b"%PDF-":
                got = from_pdf(io.BytesIO(data), document_id=doc_id, doc_type=doc_type, revision=1,
                               origin="synthetic" if is_deploy() else "internal")
                pages = [[p.page, p.text_start, p.text_end] for p in got.pages]
            elif name.lower().endswith(".txt"):
                got = from_text(data.decode("utf-8", errors="replace"), document_id=doc_id,
                                doc_type=doc_type, revision=1)
                pages = []
            else:
                raise RequestError(400, f"{name}: 텍스트 PDF 또는 .txt만 지원합니다(스캔본 OCR·HWP 미지원)")
        except DocumentInputError as exc:
            raise RequestError(400, f"{name}: {exc}") from None
        docs.append(DocInfo(doc_id, name, doc_type, got.document.text, pages))
    text = (_one(form, "direct_text") or "").replace("\r\n", "\n")
    if text.strip():
        if len(text) > MAX_DOCUMENT_CHARS:
            raise RequestError(400, "직접 입력 문자 제한 초과")
        kind = "other" if any(d.doc_type == "request" for d in docs) else "request"
        docs.append(DocInfo("T1", "직접 입력", kind, text))
    if not docs:
        raise RequestError(400, "문서를 올리거나 내용을 직접 입력하십시오")
    return docs


def encode_docs(docs: list[DocInfo]) -> str:
    raw = json.dumps([asdict(d) for d in docs], ensure_ascii=False).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def decode_docs(value: str | None) -> list[DocInfo]:
    try:
        items = json.loads(base64.b64decode(value or "", validate=True).decode("utf-8"))
        docs = [DocInfo(str(d["document_id"]), str(d["name"])[:120], str(d["doc_type"]), str(d["text"]),
                        [[int(x) for x in p][:3] for p in d.get("pages", [])]) for d in items]
    except (ValueError, KeyError, TypeError):
        raise RequestError(400, "문서 상태가 손상되었습니다. 처음부터 다시 진행하십시오") from None
    if not docs or len(docs) > MAX_FILES + 1 or any(d.doc_type not in DOC_TYPE_KO for d in docs) \
            or any(len(d.text) > MAX_DOCUMENT_CHARS for d in docs):
        raise RequestError(400, "문서 상태가 올바르지 않습니다")
    return docs


def parse_review_type(form) -> str:
    key = _one(form, "review_type") or "goods_sole"
    if key not in REVIEW_TYPES:
        raise RequestError(400, "알 수 없는 검토 유형")
    return key


def parse_mode(form) -> str:
    mode = _one(form, "mode") or allowed_modes()[0]
    if mode not in {"live", "manual"}:
        raise RequestError(400, "알 수 없는 실행 모드")
    if mode not in allowed_modes():
        raise RequestError(403, "시연 미러에서는 모델 호출(live) 모드를 사용할 수 없습니다")
    return mode


# --------------------------------------------------------------------------
# 문서 이해 (기존 DocumentUnderstanding 그대로)
# --------------------------------------------------------------------------
def manual_transport(url, body, headers, timeout):
    raise UnderstandingError("수동 입력 모드(모델 미호출)")


def understand(docs: list[DocInfo], mode: str) -> UnderstandingResult:
    documents = [d.document() for d in docs]
    if mode == "manual":
        return DocumentUnderstanding(EndpointConfig(model="manual"), transport=manual_transport).understand(documents)
    if is_deploy():  # parse_mode가 이미 막지만 이중으로 막는다
        raise RequestError(403, "시연 미러에서는 모델 호출(live) 모드를 사용할 수 없습니다")
    try:
        cfg = live_config()
    except LLMConfigError as exc:
        reason = str(exc)

        def config_error(url, body, headers, timeout):
            raise UnderstandingError(reason)
        # 기존 경로 그대로 '자동 추출 불가' 결과를 만든다 → 실패 화면에서 수동 입력 전환
        return DocumentUnderstanding(EndpointConfig(model="설정 오류"), transport=config_error).understand(documents)
    return DocumentUnderstanding(budgeted(cfg, len(documents))).understand(documents)


def budgeted(cfg: EndpointConfig, n_docs: int) -> EndpointConfig:
    """웹 요청 1건의 LLM 총 대기 시간 상한. 문서별 순차 호출이므로 호출당 제한 = 예산 / 호출 수.
    MILCHECK_WEB_LLM_BUDGET(초)로 정한다. Vercel(VERCEL 환경변수 존재)에서는 함수 실행 제한
    (vercel.json maxDuration 30초)보다 짧은 25초가 기본이다. 로컬 기본은 제한 없음(기존 동작)."""
    raw = os.environ.get("MILCHECK_WEB_LLM_BUDGET") or ("25" if os.environ.get("VERCEL") else "")
    try:
        budget = int(raw) if raw else 0
    except ValueError:
        budget = 0
    if budget <= 0:
        return cfg
    calls = max(1, n_docs if cfg.per_document else 1)
    return replace(cfg, timeout_seconds=max(1, min(cfg.timeout_seconds, budget // calls)))


def _sign(data: bytes) -> str:
    return hmac.new(_STATE_KEY, data, hashlib.sha256).hexdigest()


def encode_result(result: UnderstandingResult) -> str:
    raw = json.dumps(result.to_dict(), ensure_ascii=False).encode("utf-8")
    return base64.b64encode(raw).decode("ascii") + "." + _sign(raw)


def decode_result(value: str | None) -> UnderstandingResult:
    """live 결과는 다시 호출하지 않고 서명된 양식 값에서 복원한다(후보 변조 방지)."""
    try:
        b64, sig = (value or "").rsplit(".", 1)
        raw = base64.b64decode(b64, validate=True)
    except ValueError:
        raise RequestError(400, "추출 결과 상태가 없습니다. 처음부터 다시 진행하십시오") from None
    if not hmac.compare_digest(_sign(raw), sig):
        raise RequestError(400, "추출 결과 상태가 만료되었거나 바뀌었습니다. 처음부터 다시 진행하십시오")
    d = json.loads(raw.decode("utf-8"))
    refs = lambda xs: [SourceRef(**x) for x in xs]  # noqa: E731
    return UnderstandingResult(
        d["schema_version"], d["extractor_version"], d["component_status"],
        [CandidateFact(**{**c, "source_refs": refs(c["source_refs"])}) for c in d["candidates"]],
        [EvidenceAssessment(**{**e, "source_refs": refs(e["source_refs"])}) for e in d["evidence"]],
        [ReviewQuestion(**{**q, "source_refs": refs(q["source_refs"])}) for q in d["questions"]],
        d["rejected"], d["run_record"])


# --------------------------------------------------------------------------
# 표시 도우미
# --------------------------------------------------------------------------
e = html.escape


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
                "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                "form-action 'self'; frame-ancestors 'none'; base-uri 'none'")]
    return Response(status, headers, data)


def won(v: Any) -> str:
    return f"{v:,}원" if isinstance(v, int) else str(v)


def basis_text(value: str) -> str:
    bases = rules_meta()["sole_source"]["bases"]
    if value in bases:
        return f"{bases[value]['legal_basis']} ({ENUM_KO['sole_source_basis'].get(value, bases[value]['label'])})"
    return ENUM_KO["sole_source_basis"].get(value, value)


def show_value(name: str, value: Any) -> str:
    if name == "sole_source_basis":
        return basis_text(value)
    if name in ENUM_KO:
        return ENUM_KO[name].get(value, str(value))
    if name in {"estimated_price_krw", "total_amount_krw", "unit_price_krw"}:
        return won(value)
    if name == "quote_count" and isinstance(value, int):
        return f"{value}개사"
    return str(value)


def evidence_label(key: str) -> str:
    return EVIDENCE_KO.get(key) or EXTRA_EVIDENCE_KO.get(key, key)


def ref_place(ref, docs: dict[str, DocInfo]) -> str:
    d = docs.get(ref.document_id)
    if not d:
        return ref.document_id
    label = d.name if d.document_id == "T1" else DOC_TYPE_KO.get(d.doc_type, d.doc_type)
    for page, start, end in d.pages:
        if start <= ref.text_start < max(end, start + 1):
            return f"{label} p.{page}"
    return label


def page(title: str, body: str, *, step: int, foot: str = "") -> str:
    tabs = "".join(
        f'<span class="tab {"on" if i == step else "done" if i < step else ""}">{n:02d} {e(t)}</span>'
        for i, (n, t) in enumerate([(1, "문서 입력"), (2, "추출 확인"), (3, "판정 · 근거"), (4, "결과서")]))
    banner = (f'<div class="banner" role="note">{e(BANNER)}</div>' if is_deploy() else "")
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{e(title)} · MIL-Check</title>
<link rel="stylesheet" href="/static/style.css">
<script src="/static/app.js" defer></script></head>
<body>{banner}
<header class="top"><div class="wrap topin">
<a class="brand" href="/">MIL-Check <small>수의계약 사전검토 · 시제품</small></a>
<nav class="tabs" aria-label="진행 단계">{tabs}</nav>
<div class="topright"><a class="btn ghost" href="/runs">실행 기록</a><a class="btn ghost" href="/">처음부터</a></div>
</div></header>
<main class="wrap">{body}</main>
<footer class="wrap meta">{foot}</footer>
</body></html>"""


def error_page(status: int, message: str) -> Response:
    body = (f'<section class="card narrow"><h1>요청을 처리할 수 없습니다</h1><p class="err">{e(message)}</p>'
            f'<p><a class="btn" href="/">처음부터</a></p></section>')
    return _resp(status, page("오류", body, step=0))


def chip(text: str, kind: str = "") -> str:
    return f'<span class="chip {kind}">{e(text)}</span>'


# --------------------------------------------------------------------------
# 01 문서 입력
# --------------------------------------------------------------------------
def input_page(message: str = "") -> str:
    types = "".join(f'<option value="{k}">{e(v[0])}</option>' for k, v in REVIEW_TYPES.items())
    doc_opts = "".join(f'<option value="{k}">{e(v)}</option>' for k, v in DOC_TYPE_KO.items())
    modes = allowed_modes()
    mode_html = (
        '<input type="hidden" name="mode" value="manual">' if modes == ("manual",) else
        '<label class="inline"><input type="checkbox" name="mode" value="manual"> AI 추출 없이 수동 입력으로 진행</label>'
        '<input type="hidden" name="mode" value="live">')
    deploy_note = ('<p class="note">시연 미러: 외부 LLM을 호출하지 않습니다. 문서를 올리면 원문을 보며 항목을 '
                   '직접 입력하는 수동 입력 모드로 진행합니다.</p>' if is_deploy() else "")
    err = f'<p class="alert bad">{e(message)}</p>' if message else ""
    body = f"""
<h1>문서 입력</h1>
<p class="lead">계약 관련 문서(텍스트 PDF)를 올리거나 내용을 직접 입력합니다. 문서 원문은 실행 기록에 저장되지 않습니다.</p>
{err}{deploy_note}
<form method="post" action="/extract" enctype="multipart/form-data" id="input-form" data-loading="extract">
<section class="card row2">
<div><h2 class="h3">검토 유형 (담당자 선택)</h2><select name="review_type" class="wide">{types}</select></div>
<p class="muted">물품/용역 구분은 AI가 추출하지 않고 담당자가 정합니다. 현재 범위는 호환성·단독공급 사유 물품 구매입니다.</p>
</section>
<div class="grid2">
<section class="card">
<h2>문서 업로드</h2>
<div class="drop" id="drop">
<div class="dropicon" aria-hidden="true">⇪</div>
<p class="strong">여기로 파일을 끌어 놓거나 선택</p>
<p class="muted small">텍스트 PDF 지원 · 스캔본(OCR)·HWP는 현재 미지원</p>
<label class="btn outline">파일 선택<input type="file" name="files" id="files" accept=".pdf,.txt,application/pdf,text/plain" multiple hidden></label>
</div>
<h3 class="h4">올린 문서 · 문서 유형 확인</h3>
<div id="filelist" class="filelist"><p class="muted small" id="nofile">올린 문서가 없습니다.</p></div>
<template id="filerow"><div class="filerow"><span class="fname mono"></span><select>{doc_opts}</select></div></template>
<noscript><p class="muted small">문서 유형은 파일 이름으로 추정합니다(견적·호환·공급·요구).</p></noscript>
</section>
<section class="card">
<h2>내용 직접 입력</h2>
<p class="muted">문서가 없거나 추가 설명이 있으면 적습니다. 예: 구매 품목, 기존 장비, 단독공급 사유.</p>
<p class="warn">{e(INPUT_WARNING)}</p>
<textarea name="direct_text" rows="12" maxlength="{MAX_DOCUMENT_CHARS}"></textarea>
</section>
</div>
<div class="actionbar">
<span class="muted" id="summary">문서 0건 · 처리 시간은 문서 수에 따라 달라집니다</span>
{mode_html}
<button type="submit" class="btn primary lg">사전검토 시작</button>
</div>
</form>
{loading_overlay("extract")}{loading_overlay("judge")}"""
    return page("문서 입력", body, step=0)


def loading_overlay(kind: str) -> str:
    if kind == "extract":
        title, sub = "문서를 읽고 항목을 추출하는 중", "문서마다 따로 읽은 뒤 서로 대조합니다"
        if is_deploy():
            steps = [("문서 텍스트 추출", "텍스트 PDF · 문서 유형별 분리"),
                     ("수동 입력 준비", "시연 미러 · 외부 LLM 미사용"),
                     ("증빙 첨부 여부 대조", "규칙 · 문서 유형 기준")]
        else:
            steps = [("문서 텍스트 추출", "텍스트 PDF · 문서 유형별 분리"),
                     ("문서별 사실 후보 추출", f"로컬 LLM ({model_label('live')}) · 출력 형식 고정"),
                     ("인용 · 수치 · 형식 검수", "규칙 · 원문에 없는 값은 거부"),
                     ("문서 간 충돌 판정", "규칙 · 정규화 후 비교"),
                     ("누락 항목 질문 생성", "규칙 · 모델")]
    else:
        title, sub = "규칙 판정과 근거를 정리하는 중", "판정은 규칙 엔진이 합니다"
        steps = [("담당자 확정값 고정", "확정되지 않은 AI 값은 사용하지 않음"),
                 ("수의계약 규칙 판정", "규칙 엔진 · 국가계약법 시행령 제26조 기반"),
                 ("법령 · 유사 계약 근거 검색", "검색 · 현재 계약의 사실을 채우지 않음"),
                 ("판정 기록 저장", "원문 없이 확인값 · 버전 · 판정만")]
    items = "".join(f'<li><span class="dot">{i + 1}</span><div><b>{e(a)}</b><small>{e(b)}</small></div></li>'
                    for i, (a, b) in enumerate(steps))
    return (f'<div class="overlay" id="loading-{kind}" hidden><section class="card narrow">'
            f'<div class="lhead"><span class="spinner" aria-hidden="true"></span><div><h2>{e(title)}</h2>'
            f'<p class="muted">{e(sub)}</p></div></div><ol class="lsteps">{items}</ol></section></div>')


# --------------------------------------------------------------------------
# 자동 추출 실패 (B1)
# --------------------------------------------------------------------------
def failure_page(docs: list[DocInfo], review_type: str, reason: str) -> str:
    headline = ("모델 응답 시간 초과 — 자동 추출을 중단했습니다" if re.search(r"timed out|시간|timeout", reason, re.I)
                else "모델 응답 실패 — 자동 추출을 중단했습니다")
    hidden = (f'<input type="hidden" name="docs" value="{e(encode_docs(docs))}">'
              f'<input type="hidden" name="review_type" value="{e(review_type)}">')
    steps = [("ok", "문서 텍스트 추출", "텍스트 PDF · 문서 유형별 분리"),
             ("fail", "문서별 사실 후보 추출", f"로컬 LLM ({model_label('live')})"),
             ("", "인용 · 수치 · 형식 검수", "규칙 · 원문에 없는 값은 거부"),
             ("", "문서 간 충돌 판정", "규칙 · 정규화 후 비교"),
             ("", "누락 항목 질문 생성", "규칙 · 모델")]
    items = "".join(f'<li class="{s}"><span class="dot">{"✓" if s == "ok" else "!" if s == "fail" else i + 1}</span>'
                    f'<div><b>{e(a)}</b><small>{e(b)}</small></div></li>' for i, (s, a, b) in enumerate(steps))
    body = f"""<section class="card narrow"><h1>자동 추출 실패</h1>
<p class="muted">모델 장애 시에도 수동 경로로 검토를 계속할 수 있습니다</p><ol class="lsteps">{items}</ol>
<div class="alert bad"><b>{e(headline)}</b><p class="small mono">{e(reason)}</p>
<p>AI 없이도 검토를 이어갈 수 있습니다. 항목을 직접 입력하면 같은 규칙 판정과 결과서 경로를 사용합니다.</p>
<form method="post" action="/extract" class="btnrow" data-loading="extract">{hidden}
<button class="btn primary" name="mode" value="manual">수동 입력으로 전환</button>
<button class="btn outline" name="mode" value="live">다시 시도</button></form></div></section>
{loading_overlay("extract")}"""
    return page("자동 추출 실패", body, step=0)


# --------------------------------------------------------------------------
# 02 추출 항목 확인
# --------------------------------------------------------------------------
def required_evidence(proposed_type: str, category: str) -> list[str]:
    rules = rules_meta()
    items = ["price_reasonableness", "vendor_eligibility", "conflict_of_interest_check"]
    if proposed_type == "sole_source":
        for name, b in rules["sole_source"]["bases"].items():
            if (category == "service") == (name == "specific_technical_service"):
                items += b["required_evidence"]
        items.append("audit_notification_plan")
    elif proposed_type == "small_amount":
        items += ["no_artificial_split_review", "at_least_one_quote", "two_or_more_quotes", "electronic_quote_plan"]
    elif proposed_type == "urgent_security":
        items += rules["urgent_security"]["urgent_required_evidence"] + rules["urgent_security"]["security_required_evidence"]
    return list(dict.fromkeys(items))


def field_rows(result: UnderstandingResult) -> list[tuple[str, str, str]]:
    """표에 보일 행: 기본 8개 + 후보가 있는 추가 필드 + (방어) 그 밖에 후보가 있는 모든 필드.
    충돌 후보가 있는 필드는 반드시 행이 생기므로 담당자가 선택할 수 있다."""
    present = {c.field for c in result.candidates}
    rows = list(FIELD_ROWS) + [r for r in EXTRA_FIELD_ROWS if r[0] in present]
    shown = {r[0] for r in rows}
    rows += [(f, f, "참고") for f in sorted(present - shown)]
    return rows


def _total_hint(result) -> str:
    est = [c.value for c in result.candidates if c.field == "estimated_price_krw"]
    tot = [c.value for c in result.candidates if c.field == "total_amount_krw"]
    if est and tot and any(abs(t - round(p * 1.1)) <= 10 for t in tot for p in est):
        return "추정가격과 1.1배 관계일 수 있음 — 자동 환산하지 않음"
    return ""


def _field_input(name: str, value: str = "") -> str:
    if name in ENUM_KO:
        opts = "".join(f'<option value="{k}"{" selected" if k == value else ""}>{e(show_value(name, k))}</option>'
                       for k in ENUM_KO[name])
        return f'<select name="m:{name}" data-field="{name}"><option value="">선택</option>{opts}</select>'
    mode = ' inputmode="numeric"' if name in INT_FIELDS else ""
    return (f'<input type="text" name="m:{name}" data-field="{name}" value="{e(value)}" maxlength="200"{mode} '
            f'placeholder="{"숫자만" if name in INT_FIELDS else "원문을 보고 입력"}">')


def confirm_page(docs: list[DocInfo], review_type: str, mode: str, result: UnderstandingResult,
                 form: dict[str, list[str]] | None = None, message: str = "") -> str:
    form = form or {}
    _, category, proposed = REVIEW_TYPES[review_type]
    dmap = {d.document_id: d for d in docs}
    manual = mode == "manual" or result.component_status == "unavailable"
    conflicts = {c.field for c in result.candidates if c.status == "conflicting"}
    hint = _total_hint(result)
    rows = []
    for name, label, use in field_rows(result):
        cands = [c for c in result.candidates if c.field == name]
        mval = _one(form, f"m:{name}") or ""
        if not cands:
            value_html = _field_input(name, mval)
            cite = '<span class="muted small mono">수동 입력</span><br>—'
            status = chip("입력 필요", "warn")
        elif name in conflicts:
            chosen = _one(form, f"f:{name}")
            opts = "".join(
                f'<label class="pick"><input type="radio" name="f:{name}" value="{e(c.fact_id)}"'
                f'{" checked" if chosen == c.fact_id else ""} data-conflict="{name}" data-value="{e(str(c.value))}"> <b>{e(show_value(name, c.value))}</b>'
                f'<small>{e(" · ".join(ref_place(r, dmap) + " “" + r.quote + "”" for r in c.source_refs))}</small></label>'
                for c in cands)
            value_html = (f'<p class="err small">문서마다 값이 다릅니다 — 하나를 선택하세요</p>{opts}'
                          f'<div class="edit">{_field_input(name, mval)}</div>')
            r0 = cands[0].source_refs[0] if cands[0].source_refs else None
            cite = (f'<span class="muted small mono">{e(ref_place(r0, dmap))}</span><br>“{e(r0.quote)}”' if r0 else "—")
            status = chip("충돌", "bad")
        else:
            c = cands[0]
            note = f'<p class="warn small">{e(hint)}</p>' if name == "total_amount_krw" and hint else ""
            value_html = (f'<input type="hidden" name="f:{name}" value="{e(c.fact_id)}" data-value="{e(str(c.value))}">'
                          f'<span class="val">{e(show_value(name, c.value))}</span>{note}'
                          f'<div class="edit">{_field_input(name, mval)}</div>')
            cite = "<br>".join(f'<span class="muted small mono">{e(ref_place(r, dmap))}</span><br>“{e(r.quote)}”'
                               for r in c.source_refs[:2]) or "—"
            status = chip("확인 권장", "warn") if (name == "total_amount_krw" and hint) else chip("AI 후보")
        rows.append(f'<tr><th scope="row">{e(label)}<small>{e(use)}</small></th><td>{value_html}</td>'
                    f'<td class="cite">{cite}</td><td>{status}</td></tr>')

    # 규칙 검사용 추정가격
    est = [c for c in result.candidates if c.field == "estimated_price_krw"
           and dmap.get(c.source_refs[0].document_id if c.source_refs else "", None)
           and dmap[c.source_refs[0].document_id].doc_type == "request"]
    # 적용 버튼은 화면에서 현재 확정한 '부가세 여부'가 부가세 별도(제외)일 때만 값을 복사한다(app.js).
    # 부가세 포함·미상·충돌 미선택이면 복사하지 않고 직접 입력을 안내한다. 서버는 이 입력란 값만 쓴다.
    apply_btn = (f'<button type="button" class="btn outline" data-apply-price="{est[0].value}">구매요구서 값 적용</button>'
                 if len(est) == 1 else
                 '<button type="button" class="btn outline" data-apply-price="from-manual">추정가격 값 적용</button>')
    price_val = _one(form, "estimated_price_krw_ex_vat") or ""

    # 질문(수동 모드에서는 항목 누락 질문은 표의 '입력 필요'와 같으므로 뺀다)
    qs = [q for q in result.questions if not (manual and q.reason == "missing" and q.affected_fields)]
    q_html = "".join(
        f'<div class="q"><p>{e(_ko_question(q.text))}</p><input type="text" name="q:{e(q.question_id)}" '
        f'value="{e(_one(form, "q:" + q.question_id) or "")}" maxlength="300" placeholder="답변 또는 \'해당 없음\'"></div>'
        for q in qs) or '<p class="muted">추가 질문이 없습니다.</p>'

    # 증빙 확인
    checked = set(form.get("evidence", []))
    all_text = "\n".join(d.text for d in docs)
    ev_rows, covered = [], set()
    for ev in result.evidence:
        rid = ev.requirement_id
        items = EVIDENCE_HINTS.get(rid, [])
        covered.update(items)
        mentioned = any(k in all_text for k in REQ_KEYWORDS.get(rid, ()))
        present = {"present": chip("있음", "ok"), "absent": chip("없음", "bad")}.get(ev.presence, chip("확인 불가"))
        support = chip("판단 불가") if manual else chip(SUPPORT_KO.get(ev.content_support, ev.content_support),
                                                     "ok" if ev.content_support == "supported" else "")
        ev_rows.append(
            f'<tr><td>{e(EVIDENCE_REQUIREMENTS[rid]["label"])}<small>{e(" · ".join(evidence_label(i) for i in items))}</small></td>'
            f'<td>{chip("있음", "ok") if mentioned else chip("없음")}</td><td>{present}</td><td>{support}</td>'
            f'<td><label class="inline"><input type="checkbox" name="evidence" value="req:{rid}"'
            f'{" checked" if "req:" + rid in checked else ""}> 확인</label></td></tr>')
    bases_of: dict[str, list[str]] = {}
    if proposed == "sole_source":
        for bname, spec in rules_meta()["sole_source"]["bases"].items():
            for it in spec["required_evidence"]:
                bases_of.setdefault(it, []).append(bname)
    for item in required_evidence(proposed, category):
        if item in covered:
            continue
        tag = f' data-bases="{" ".join(bases_of[item])}"' if item in bases_of and len(bases_of[item]) < 4 else ""
        ev_rows.append(
            f'<tr{tag}><td>{e(evidence_label(item))}<small>규칙 증빙 항목</small></td><td>—</td><td>—</td><td>—</td>'
            f'<td><label class="inline"><input type="checkbox" name="evidence" value="{e(item)}"'
            f'{" checked" if item in checked else ""}> 확인</label></td></tr>')
    extras = "".join(
        f'<label class="inline">{e(label)} <select name="x:{k}"><option value="">미확인</option>'
        + "".join(f'<option value="{v}"{" selected" if _one(form, "x:" + k) == v else ""}>{e(t)}</option>'
                  for v, t in opts.items()) + "</select></label>"
        for k, label, opts in EXTRA_FACTS.get(proposed, []))

    reviewer = ("" if is_deploy() else
                f'<label class="inline">확인자 <input type="text" name="reviewer" value="{e(_one(form, "reviewer") or "담당자")}" '
                f'maxlength="40" required></label>')
    lead = ("AI 추출 없이 수동 입력 모드입니다. 같은 규칙 판정 경로를 사용합니다." if manual else
            "AI가 찾은 값과 원문 위치입니다. 수정하거나 확인한 값만 판정에 사용됩니다.")
    if manual and mode == "live":
        lead = "자동 추출을 사용할 수 없어 수동 입력으로 전환했습니다. 같은 규칙 판정 경로를 사용합니다."
    doc_list = "".join(
        f'<details><summary>{e(DOC_TYPE_KO.get(d.doc_type, d.doc_type))} · <span class="mono">{e(d.name)}</span>'
        f'{f" · {len(d.pages)}쪽" if d.pages else ""}</summary><pre>{e(d.text)}</pre></details>' for d in docs)
    state = (f'<input type="hidden" name="docs" value="{e(encode_docs(docs))}">'
             f'<input type="hidden" name="review_type" value="{e(review_type)}">'
             f'<input type="hidden" name="mode" value="{e("manual" if manual else "live")}">'
             + ("" if manual else f'<input type="hidden" name="result" value="{e(encode_result(result))}">'))
    err = f'<p class="alert bad">{e(message)}</p>' if message else ""
    body = f"""
<div class="titlerow"><div><h1>추출 항목 확인</h1><p class="lead">{e(lead)}</p></div>
<div class="legend">{chip("AI 후보")}{chip("충돌 · 선택 필요", "bad")}{chip("확인 권장", "warn")}{chip("담당자 확정", "ok")}</div></div>
{err}
<form method="post" action="/review" id="confirm-form" data-loading="judge">{state}
<section class="card flush"><table class="grid fields"><thead><tr><th>항목</th><th>값</th><th>원문 인용</th><th>상태</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></section>
<section class="card"><h2 class="h3">원문 보기</h2><p class="muted small">원문은 저장하지 않습니다. 화면 안에서만 대조용으로 표시합니다.</p>{doc_list}</section>
<div class="grid2">
<section class="card"><h2>규칙 검사용 추정가격 <small>(부가세 제외 · 담당자 입력)</small></h2>
<p class="muted">부가세 포함 금액을 1.1로 자동 환산하지 않습니다. 규칙 판정에는 아래에 확정한 값만 사용합니다.</p>
<div class="inputrow"><input type="text" inputmode="numeric" name="estimated_price_krw_ex_vat" id="price" value="{e(price_val)}" placeholder="숫자만 입력 (원)" maxlength="20">{apply_btn}</div><p class="err small" id="price-msg" role="status"></p></section>
<section class="card"><h2>{"누락 질문" if manual else "AI 누락 질문"}</h2>{q_html}</section>
</div>
<section class="card flush"><div class="pad"><h2>증빙 확인</h2>
<p class="muted">"문서에 적혀 있음", "파일이 있음", "내용이 입증함", "담당자가 확인함"을 구분합니다. 판정에는 담당자 확인만 반영됩니다.</p></div>
<table class="grid"><thead><tr><th>증빙</th><th>문서에 언급</th><th>파일 있음</th><th>내용 입증 (AI 참고)</th><th>담당자 확인</th></tr></thead>
<tbody>{"".join(ev_rows)}</tbody></table>
{f'<div class="pad extras"><h3 class="h4">담당자 판단 사실</h3>{extras}</div>' if extras else ""}</section>
<div class="actionbar sticky">
<label class="inline"><input type="checkbox" name="ack" value="1" id="ack"{" checked" if _one(form, "ack") else ""}> 표시된 값을 원문과 대조해 확인했습니다. 확인한 값만 규칙 판정에 사용됩니다.</label>
{reviewer}<span class="err small" id="remaining"></span>
<button type="button" class="btn outline" id="toggle-edit">수정</button>
<button type="submit" class="btn primary lg" id="judge">확인 · 규칙 판정</button>
</div>
</form>
{loading_overlay("judge")}"""
    foot = f'모드: {"수동 입력" if manual else "AI 추출 + 담당자 확인"} · 모델 {e(model_label("manual" if manual else "live"))}'
    return page("추출 항목 확인", body, step=1, foot=foot)


def _ko_question(text: str) -> str:
    for k, v in FIELD_LABEL.items():
        text = text.replace(f"'{k}'", f"'{v}'")
    for name, values in ENUM_KO.items():  # 충돌 질문의 원시 값("vat_included")을 한국어로
        for k, v in values.items():
            text = text.replace(f'"{k}"', f'"{v}"')
    return text


# --------------------------------------------------------------------------
# 확인값 → Confirmation
# --------------------------------------------------------------------------
def _parse_int(raw: str, label: str) -> int:
    s = raw.replace(",", "").replace("원", "").replace("개사", "").replace("개", "").strip()
    if not s.isdigit():
        raise ConfirmationError(f"{label}: 숫자만 입력하십시오")
    return int(s)


def build_confirmation(form: dict[str, list[str]], result: UnderstandingResult, review_type: str) -> Confirmation:
    if not _one(form, "ack"):
        raise ConfirmationError("표시된 값을 원문과 대조해 확인했다는 체크가 필요합니다")
    _, category, proposed = REVIEW_TYPES[review_type]
    fields: dict[str, dict[str, Any]] = {}
    for name, label, _ in field_rows(result):
        raw = (_one(form, f"m:{name}") or "").strip()
        if raw:  # 담당자가 직접 입력(수정)한 값이 후보보다 우선
            if name in INT_FIELDS:
                value: Any = _parse_int(raw, label)
            elif name in ENUM_KO:
                if raw not in ENUM_KO[name]:
                    raise ConfirmationError(f"{label}: 허용되지 않은 값")
                value = raw
            else:
                value = raw[:200]
            fields[name] = {"manual": value}
        elif _one(form, f"f:{name}"):
            fields[name] = {"fact_id": _one(form, f"f:{name}")}
    price_raw = (_one(form, "estimated_price_krw_ex_vat") or "").strip()
    if not price_raw:
        raise ConfirmationError("규칙 검사용 추정가격(부가세 제외)을 입력하십시오")
    price = _parse_int(price_raw, "부가세 제외 추정가격")
    allowed = set(required_evidence(proposed, category))
    evidence: list[str] = []
    for x in form.get("evidence", []):
        if x.startswith("req:") and x[4:] in EVIDENCE_HINTS:
            evidence += EVIDENCE_HINTS[x[4:]]
        elif x in allowed:
            evidence.append(x)
    extra: dict[str, Any] = {}
    for k, _, opts in EXTRA_FACTS.get(proposed, []):
        v = _one(form, f"x:{k}")
        if v in opts:
            extra[k] = (v == "yes") if k in BOOL_EXTRAS else v
    valid_q = {q.question_id for q in result.questions}
    answered = {k[2:]: v[0].strip()[:300] for k, v in form.items()
                if k.startswith("q:") and k[2:] in valid_q and v and v[0].strip()}
    reviewer = DEMO_REVIEWER if is_deploy() else (_one(form, "reviewer") or "").strip()[:40]
    return Confirmation(reviewer=reviewer, contract_category=category, proposed_type=proposed,
                        fields=fields, estimated_price_krw_ex_vat=price, evidence_checked=evidence,
                        extra_case=extra, answered_questions=answered)


# --------------------------------------------------------------------------
# 03 판정 · 근거 / 04 결과서
# --------------------------------------------------------------------------
_ARTICLE_RE = re.compile(r"제\d+조(?:제\d+항)?(?:제\d+호)?(?:[가-힣]목)?")


def _nospace(text: Any) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def grounding_source(review: dict[str, Any]) -> dict[str, Any] | None:
    """판정에 적용된 근거 조항(legal_ground)의 현행 원문을 검색 결과에서 찾는다.

    통과 조건(모두): 규칙 기준일이 있음 · 적용 근거에서 법령명과 조항(제n조제n항제n호[n목])을 읽을 수 있음 ·
    검색 결과 중 상태 '현행' · 원문 발췌 있음 · 시행일이 규칙 기준일 이전 · 제목에 같은 법령명과 같은 조항이 있음.
    목 단위 조항은 호 단위 원문(예: 제26조제1항제5호가목 → 제26조제1항제5호)까지 인정한다.
    """
    asof = str(review.get("rules_current_as_of") or "")
    ground = _nospace(review.get("legal_ground"))
    m = _ARTICLE_RE.search(ground)
    if not asof or not m:
        return None
    law, article = ground[:m.start()], m.group(0)
    articles = [article] + ([article[:-2]] if article.endswith("목") else [])
    for s in review.get("retrieved_sources", []):
        title, date = _nospace(s.get("title")), str(s.get("source_date") or "")
        if s.get("status") != "현행" or not s.get("excerpt") or not date or date > asof:
            continue
        if law and law not in title:
            continue
        if any(a in title for a in articles):
            return s
    return None


def grounding_ok(review: dict[str, Any]) -> bool:
    """근거 원문·적용 버전 확인. 적용 조항의 현행 원문이 확인될 때만 확정본 발행 가능."""
    return grounding_source(review) is not None


def rule_rows(report: dict[str, Any]) -> list[tuple[str, str, str, str, str]]:
    """(검사 항목, 결과, 결과 색, 근거 첫 줄, 근거 둘째 줄) — 모두 규칙 엔진 출력에서 그대로 옮긴다."""
    r = report["review"]
    case = report["confirmed_case"]
    src = grounding_source(r)
    rows = []
    for f in r["findings"]:
        text, kind = SEVERITY_BADGE.get(f["severity"], (f["severity"], ""))
        rows.append((FINDING_KO.get(f["code"], f["code"]), text, kind, f["legal_basis"], f["message"]))
    missing = r["missing_evidence"]
    rows.append(("필수 증빙 (담당자 확인)", "보완 필요" if missing else "충족", "warn" if missing else "ok",
                 "규칙 세트의 유형별 필요 증빙 기준",
                 ("미확인: " + ", ".join(evidence_label(m) for m in missing)) if missing else
                 "필요 증빙 모두 담당자 확인"))
    price = case.get("estimated_price_krw_ex_vat")
    rows.append(("금액 입력 확인", "충족" if isinstance(price, int) else "확인 불가",
                 "ok" if isinstance(price, int) else "bad",
                 "담당자 확정 추정가격(부가세 제외) 사용 · 자동 환산 없음", f"확정값 {won(price)}" if price else "확정값 없음"))
    ok = grounding_ok(r)
    rows.append(("근거 원문 · 적용 버전", "충족" if ok else "확인 불가", "ok" if ok else "bad",
                 f"규칙 기준일 {r.get('rules_current_as_of') or '미상'} 대조",
                 (f"{src['title']} ({src.get('source_date') or '일자 미상'}, {src['status']})" if src
                  else f"적용 조항({r.get('legal_ground') or '미상'})의 현행 원문 또는 시행일을 확인하지 못함")))
    return rows


def _table(rows) -> str:
    return "".join(f'<tr><th scope="row">{e(a)}</th><td>{chip(b, k)}</td><td>{e(c)}<small>{e(d)}</small></td></tr>'
                   for a, b, k, c, d in rows)


def a4_document(report: dict[str, Any], docs: list[DocInfo], review_type: str, result: UnderstandingResult,
                kind: str, created: str) -> str:
    """07 출력 결과서(A4) 또는 보완 요청서. 화면 미리보기와 인쇄가 같은 마크업을 쓴다."""
    r = report["review"]
    case = report["confirmed_case"]
    title_txt, color, _ = DECISION_VIEW.get(r["decision"], (r["decision"], "muted", ""))
    dmap = {d.document_id: d for d in docs}
    cand = {c.fact_id: c for c in result.candidates}
    type_label = REVIEW_TYPES[review_type][0]
    doc_count = f'{len(docs)}건 ({"·".join(DOC_TYPE_KO.get(d.doc_type, d.doc_type) for d in docs)})'
    ok = grounding_ok(r)
    badge = "초안 · 확정 불가" if not ok else "초안 · 담당자 서명 전"
    head = (f'<div class="a4head"><div class="mono small">문서번호 {e(report["report_no"])}<br>생성일시 {e(created)}</div>'
            f'<span class="stamp" data-stamp>{e(badge)}</span></div>')
    info = (f'<table class="kv"><tr><th>검토 유형</th><td>{e(type_label)}</td><th>입력 문서</th><td>{e(doc_count)}</td></tr>'
            f'<tr><th>품명</th><td colspan="3">{e(str(case.get("item_name") or "-"))}</td></tr>'
            f'<tr><th>추정가격</th><td colspan="3">{e(won(case.get("estimated_price_krw_ex_vat")))} (부가세 제외, 담당자 입력)</td></tr></table>')
    if kind == "supplement":
        req = [f"- {e(evidence_label(m))} 제출 또는 확인 자료" for m in r["missing_evidence"]]
        req += [f"- {e(q['text'])}" for q in report["document_findings"]["open_questions"]]
        req += [f"- {e(f['message'])}" for f in r["findings"] if f["severity"] in {"warning", "critical"}]
        body = (f'<h1 class="a4title">수의계약 사전검토 보완 요청서</h1><p class="a4sub">MIL-Check 사전검토 보조 결과 · 최종 결정 문서 아님</p>'
                f'{info}<div class="result {color}"><span>규칙 검사 결과</span><b>{e(title_txt)}</b></div>'
                f'<ol class="plain"><li>보완 요청 사항<br>{"<br>".join(req) or "- 없음"}</li>'
                f'<li>근거<br>- {e(r["legal_ground"])} · 규칙 기준일 {e(str(r.get("rules_current_as_of")))}</li>'
                f'<li>회신 기한: [담당자 지정]</li></ol>')
    else:
        prov_rows = []
        for p in report["provenance"]:
            name = p["field"]
            if name == "estimated_price_krw_ex_vat":
                prov_rows.append(("추정가격 (부가세 제외)", won(case.get(name)), "담당자 입력", "담당자 입력"))
                continue
            c = cand.get(p.get("fact_id")) if p["source"] == "candidate" else None
            value = c.value if c else report.get("confirmed_values", {}).get(name)
            place = " · ".join(ref_place(x, dmap) for x in c.source_refs) if c else "원문 대조 후 직접 입력"
            prov_rows.append((FIELD_LABEL.get(name, name), show_value(name, value) if value is not None else "-",
                              place, "AI 후보 확인" if c else "담당자 입력"))
        prov = "".join(f"<tr><td>{e(a)}</td><td>{e(b)}</td><td>{e(c)}</td><td>{e(d)}</td></tr>" for a, b, c, d in prov_rows)
        rules_tbl = "".join(f'<tr><td>{e(a)}</td><td class="{k}">{e(b)}</td><td>{e(c)} · {e(d)}</td></tr>'
                            for a, b, k, c, d in rule_rows(report))
        ev_presence = report["document_findings"]["evidence_presence"]
        checked = set(case.get("evidence", []))
        ev_tbl = "".join(
            f'<tr><td>{e(EVIDENCE_REQUIREMENTS[rid]["label"])}</td>'
            f'<td>{"○" if ev_presence.get(rid) == "present" else "×"}</td>'
            f'<td>{"○" if all(i in checked for i in EVIDENCE_HINTS.get(rid, [])) else "미확인"}</td></tr>'
            for rid in EVIDENCE_REQUIREMENTS)
        signals = " · ".join(s.get("message", str(s)) if isinstance(s, dict) else str(s)
                             for s in r.get("advisory", {}).get("signals", [])) or "없음"
        answers = report["document_findings"]["answered_questions"]
        qtext = {q.question_id: q.text for q in result.questions}
        ans = " / ".join(f"{_ko_question(qtext.get(k, k))} — {v}" for k, v in answers.items()) or "없음"
        body = (f'<h1 class="a4title">수의계약 사전검토 결과서</h1><p class="a4sub">MIL-Check 사전검토 보조 결과 · 최종 결정 문서 아님</p>'
                f'<h2>1. 검토 대상</h2>{info}'
                f'<div class="result {color}"><span>규칙 검사 결과</span><b>{e(title_txt)}</b>'
                f'<p>{e(r["legal_ground"])}. 담당자가 확정한 입력값에 대한 규칙 검사 결과이며, 계약 체결 여부는 검토 담당자가 결정합니다.</p></div>'
                f'<h2>2. 담당자 확정 입력값</h2><table class="lined"><tr><th>항목</th><th>확정값</th><th>출처 (문서 · 쪽)</th><th>확정 방식</th></tr>{prov}</table>'
                f'<h2>3. 규칙별 검사 결과와 근거</h2><table class="lined"><tr><th>검사 항목</th><th>결과</th><th>근거 (법령 · 문서 인용)</th></tr>{rules_tbl}</table>'
                f'<h2>4. 증빙 확인</h2><table class="lined"><tr><th>증빙</th><th>파일 있음</th><th>담당자 확인</th></tr>{ev_tbl}</table>'
                + (f'<p class="small">통제 조건: {e(" · ".join(r["controls"]))}</p>' if r["controls"] else "")
                + f'<h2>5. 참고 사항 <small>(판정에 반영되지 않음)</small></h2><div class="box">'
                f'<p><b>AI 검토 의견(초안):</b> {e(r.get("llm_summary") or "생성하지 않음 (LLM 미사용)")}</p>'
                f'<p><b>AI 참고 신호:</b> {e(signals)}</p><p><b>누락 질문 답변:</b> {e(ans)}</p></div>')
    v = report["versions"]
    sign = ('<div class="a4foot"><div class="mono small">모델 ' + e(report.get("model_label") or str(v["model"])) + " · 규칙 기준일 "
            + e(str(v["rules_current_as_of"])) + "<br>흐름 " + e(v["workflow"]) + " · 실행 ID " + e(report["report_no"])
            + "<br>문서 원문은 실행 기록에 저장하지 않음</div>"
            '<table class="sign"><tr><th>검토 담당자</th><th>확인일</th></tr><tr><td></td><td></td></tr></table></div>'
            '<p class="small muted">본 결과서는 담당자의 검토를 돕는 보조 자료입니다.'
            + (" 시연 미러에서 생성된 문서이며 실제 계약 정보가 아닙니다." if is_deploy() else "") + "</p>")
    return f'<article class="a4" id="doc-{kind}">{head}{body}{sign}</article>'


def result_page(report: dict[str, Any], docs: list[DocInfo], review_type: str, mode: str,
                result: UnderstandingResult, elapsed_ms: int) -> str:
    r = report["review"]
    title_txt, color, line = DECISION_VIEW.get(r["decision"], (r["decision"], "muted", ""))
    ok = grounding_ok(r)
    signals = "".join(f'<li>{e(s.get("message", str(s)) if isinstance(s, dict) else str(s))}</li>'
                      for s in r.get("advisory", {}).get("signals", [])) or "<li>없음</li>"
    sources = "".join(f'<li><b>{e(s.get("title") or "")}</b> <span class="muted small">{e(s.get("source_date") or "일자 미상")} · {e(s.get("status") or "")}</span>'
                      f'<small>{e((s.get("excerpt") or "")[:160])}</small></li>' for s in r.get("retrieved_sources", [])[:3])
    created = time.strftime("%Y-%m-%d %H:%M", time.gmtime()) + " UTC"
    needs = r["decision"] == "NEEDS_EVIDENCE"
    report_doc = a4_document(report, docs, review_type, result, "report", created)
    supp_doc = a4_document(report, docs, review_type, result, "supplement", created) if needs else ""
    block = ('' if ok else '<p class="err small">근거 원문 또는 적용 법령 버전을 확인하지 못해 확정본 발행이 차단됩니다. 초안만 생성할 수 있습니다.</p>')
    body = f"""
<section class="decision {color}"><div><span class="dlabel">규칙 검사 결과</span><div class="dvalue">{e(title_txt)}</div>
<p>{e(line)}</p><p class="small">{e(DECISION_KO.get(r["decision"], ""))} · {e(r["legal_ground"])}</p></div>
<p class="dnote">{e(FINAL_NOTICE)}</p></section>
<section class="card flush"><div class="pad"><h2>규칙별 판정과 근거</h2></div>
<table class="grid"><thead><tr><th>검사 항목</th><th>결과</th><th>근거 (법령 · 문서 인용)</th></tr></thead><tbody>{_table(rule_rows(report))}</tbody></table>
{f'<div class="pad"><h3 class="h4">통제 조건</h3><ul>{"".join(f"<li>{e(c)}</li>" for c in r["controls"])}</ul></div>' if r["controls"] else ""}
<div class="pad"><h3 class="h4">검색된 공개 근거 원문</h3><ul class="srcs">{sources or "<li>없음</li>"}</ul></div></section>
<div class="grid2">
<section class="card"><div class="cardhead"><h2>AI 검토 의견 (초안)</h2><span class="muted small">인용 검증을 통과한 문장만 표시</span></div>
<p class="muted">{e(r.get("llm_summary") or ("시연 미러는 외부 LLM을 사용하지 않아 검토 의견을 생성하지 않습니다." if is_deploy() else "로컬 LLM 요약이 설정되지 않아 생성하지 않았습니다."))}</p></section>
<section class="card"><div class="cardhead"><h2>AI 참고 신호</h2>{chip("판정에 반영되지 않음")}</div><ul>{signals}</ul></section>
</div>
<section class="card" id="docgen"><h2>문서 생성 <small>(선택)</small></h2>
<div class="btnrow"><button type="button" class="btn primary" data-show="report">사전검토 결과서 생성</button>
{'<button type="button" class="btn outline" data-show="supplement">보완 요청서 생성</button>' if needs else ""}
<a class="btn outline" href="/runs/{e(report["report_no"])}">실행 기록 보기</a></div>{block}
<div class="preview" data-doc="report">{report_doc}
<div class="btnrow"><button type="button" class="btn outline" data-print="report">PDF로 저장</button>
<button type="button" class="btn primary" data-final="report"{"" if ok else " disabled"}>확정본 발행</button></div></div>
{f'<div class="preview" data-doc="supplement">{supp_doc}<div class="btnrow"><button type="button" class="btn outline" data-print="supplement">PDF로 저장</button></div></div>' if needs else ""}
</section>"""
    v = report["versions"]
    foot = (f'실행 ID {e(report["report_no"])} · 모델 {e(report.get("model_label") or str(v["model"]))} · 규칙 세트 {e(str(rules_meta().get("schema_version")))}'
            f' · 법령 기준일 {e(str(v["rules_current_as_of"]))} · 처리 시간 {elapsed_ms / 1000:.1f}초'
            f' · 모드: {"수동 입력" if mode == "manual" else "AI 추출 + 담당자 확인"}')
    return page(f"판정 {report['report_no']}", body, step=2, foot=foot)


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
                  f'<td>{e(rv)}</td><td>{e(DECISION_VIEW.get(d, (d,))[0])}</td></tr>' for n, t, rv, d in rows)
    table = (f'<table class="grid"><thead><tr><th>결과서 번호</th><th>시각(UTC)</th><th>확인자</th><th>판정</th></tr></thead>'
             f'<tbody>{trs}</tbody></table>' if rows else '<p class="pad">기록이 없습니다.</p>')
    return page("실행 기록", f'<h1>실행 기록</h1><section class="card">{runs_notice()}</section>'
                             f'<section class="card flush">{table}</section>', step=3)


def run_detail_page(report_no: str) -> Response:
    store = _store()
    try:
        got = lookup(store, report_no)
    finally:
        store.close()
    if not got:
        return error_page(404, "해당 기록이 없습니다" + (f" ({VOLATILE_NOTICE})" if is_deploy() else ""))
    ok = got["integrity_ok"]
    body = (f'<h1>실행 기록 {e(report_no)}</h1><section class="card">{runs_notice()}'
            f'<p>무결성 확인: {chip("일치", "ok") if ok else chip("불일치 — 기록이 바뀌었습니다", "bad")}</p></section>'
            f'<section class="card"><pre>{e(json.dumps(got["record"], ensure_ascii=False, indent=2))}</pre></section>')
    return _resp(200, page(f"기록 {report_no}", body, step=3))


# --------------------------------------------------------------------------
# 진입점
# --------------------------------------------------------------------------
STATIC = {"/static/style.css": ("style.css", "text/css; charset=utf-8"),
          "/static/app.js": ("app.js", "text/javascript; charset=utf-8")}


def handle(method: str, path: str, body: bytes = b"", content_type: str = "") -> Response:
    """HTTP와 무관한 요청 처리. 모든 응답에 noindex 헤더가 붙는다."""
    path = path.split("?", 1)[0] or "/"
    try:
        if method in {"GET", "HEAD"}:
            if path == "/":
                return _resp(200, input_page())
            if path == "/favicon.ico":
                return _resp(204, b"", "image/x-icon")
            if path == "/healthz":
                return _resp(200, "ok", "text/plain; charset=utf-8")
            if path == "/robots.txt":
                return _resp(200, "User-agent: *\nDisallow: /\n", "text/plain; charset=utf-8")
            if path in STATIC:
                name, ctype = STATIC[path]
                return _resp(200, (STATIC_DIR / name).read_bytes(), ctype)
            if path == "/runs":
                return _resp(200, runs_page())
            if path.startswith("/runs/"):
                return run_detail_page(path[len("/runs/"):])
            return error_page(404, "없는 페이지입니다")
        if method == "POST":
            if len(body) > MAX_BODY_BYTES:
                return error_page(413, "요청이 너무 큽니다(문서 합계 4MB 이하)")
            form, files = parse_form(body, content_type)
            if path == "/extract":
                review_type = parse_review_type(form)
                modes = form.get("mode", [])  # 입력 화면: hidden live + '수동 입력' 체크박스
                mode = parse_mode({"mode": ["manual" if "manual" in modes else (modes[-1] if modes else "")]})
                try:
                    docs = decode_docs(_one(form, "docs")) if _one(form, "docs") else docs_from_upload(form, files)
                except RequestError as exc:
                    if exc.status == 400 and not _one(form, "docs"):
                        return _resp(400, input_page(str(exc)))
                    raise
                result = understand(docs, mode)
                if mode == "live" and result.component_status == "unavailable":
                    reason = result.run_record.get("error") or "모델을 사용할 수 없습니다"
                    return _resp(200, failure_page(docs, review_type, reason))
                return _resp(200, confirm_page(docs, review_type, mode, result))
            if path == "/review":
                started = time.monotonic()
                review_type = parse_review_type(form)
                mode = parse_mode(form)
                docs = decode_docs(_one(form, "docs"))
                # 수동 모드는 모델 호출이 없어 같은 입력으로 다시 계산한다. live는 서명된 추출 결과를 복원한다.
                result = understand(docs, "manual") if mode == "manual" else decode_result(_one(form, "result"))
                try:
                    conf = build_confirmation(form, result, review_type)
                    store = _store()
                    try:
                        report = run_case(result, conf, get_agent(), store, case_id=None)
                    finally:
                        store.close()
                    # 결과서 표시용(기록 DB에는 들어가지 않음): 담당자가 직접 입력한 값
                    report["confirmed_values"] = {k: v["manual"] for k, v in conf.fields.items() if "manual" in v}
                    report["model_label"] = model_label(mode)
                except ConfirmationError as exc:
                    return _resp(400, confirm_page(docs, review_type, mode, result, form,
                                                   f"담당자 확인이 규칙에 맞지 않습니다: {exc}"))
                elapsed = int((time.monotonic() - started) * 1000)
                return _resp(200, result_page(report, docs, review_type, mode, result, elapsed))
            return error_page(404, "없는 페이지입니다")
        return error_page(405, "지원하지 않는 요청 방식입니다")
    except RequestError as exc:
        return error_page(exc.status, str(exc))
