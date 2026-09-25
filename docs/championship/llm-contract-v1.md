# LLM 입출력 계약 v1 (`llm-contract-v1`)

기준일: 2026-09-25. 구현: `milcheck/understanding.py`. 시험: `tests/test_understanding.py`.
상위 설계: [contracts.md](contracts.md)의 Document·SourceRef·CandidateFact·EvidenceAssessment·ReviewQuestion.

> 9/19~21 작업분(원격 미반영, 소실)을 설계 문서를 근거로 **재작성**한 것이다. 과거 코드의 복구본이 아니다.
> Mock·루프백 서버 시험만 수행했으며 **실제 모델의 추출 품질은 측정하지 않았다.**

## 1. 역할과 금지사항

| 모델이 할 수 있는 것 | 모델이 할 수 없는 것 (경계에서 거부) |
|---|---|
| 허용 필드의 후보 값 + 원문 인용 제안 | `status`, `confirmed*`, `decision`, `verdict`, `presence`, `fact_revision` 키 사용 |
| 증빙 요구사항별 내용 입증 **후보**(supported/unsupported/unknown) | 파일 존재 여부 판단 — 첨부 문서 유형으로 결정론적으로 산정 |
| 추가 질문 제안 | 허용 필드 밖의 값, 출처 없는 값, 인용문에 없는 수치 |

경계를 통과한 값은 모두 `CandidateFact(status=proposed|conflicting)`다. 담당자 확인 전에는 규칙 검사 입력(ConfirmedFact)이 되지 않는다.

## 2. 요청

- 대상: OpenAI 호환 `POST {base_url}/chat/completions`, `temperature=0`, `response_format=json_object`.
- 허용 호스트: 루프백(`127.0.0.1`, `::1`, `localhost`)과 `MILCHECK_LLM_ALLOWED_HOSTS`에 명시한 내부 호스트만. URL 자격증명·비 HTTP 스킴 거부.
- 환경변수 프록시 무시, 리다이렉트 거부, 응답 256KiB 초과 거부, 문서당 60,000자 초과 시 전송하지 않음.
- 시스템 프롬프트는 문서를 **데이터**로 규정한다. 문서 본문은 사용자 메시지의 `<documents>` 안에 JSON 문자열로만 들어간다.

## 3. 응답 스키마

```json
{
  "schema_version": "llm-contract-v1",
  "facts": [{"field": "estimated_price_krw", "value": 18000000,
             "source_refs": [{"document_id": "DOC-REQ", "revision": 1,
                              "text_start": 52, "text_end": 67, "quote": "추정가격 18,000,000원"}]}],
  "evidence_claims": [{"requirement_id": "EV-SUPPLIER", "content_support": "supported",
                       "source_refs": [...]}],
  "questions": [{"affected_fields": ["quantity"], "reason": "missing", "text": "...", "source_refs": []}]
}
```

### 허용 필드 (최초 업무: 호환성·단독공급 사유 물품 구매)

| 필드 | 타입 | 비고 |
|---|---|---|
| item_name, supplier_name, existing_equipment | 문자열 ≤200자 | |
| estimated_price_krw, total_amount_krw, unit_price_krw | 0 이상 정수(원) | 서로 다른 필드. 부동소수 금지 |
| quantity, quote_count | 0 이상 정수 | |
| tax_status | vat_included / vat_excluded / unknown | |
| sole_source_basis | compatibility / patented_no_substitute / original_supplier_direct_service / single_supplier | 기존 규칙 엔진 명칭과 동일 |
| quote_status | received / planned / none | "받을 예정"은 planned — 증빙이 아님 |
| delivery_deadline | YYYY-MM-DD | |

필수 확인 필드: item_name, estimated_price_krw, tax_status, sole_source_basis, supplier_name.
증빙 요구: `EV-QUOTE`(quote), `EV-SUPPLIER`(supplier_confirmation), `EV-COMPAT`(compatibility_statement).

## 4. 경계 검증 순서

1. 봉투 해석(`choices[0].message.content`), 코드펜스 제거, JSON 객체·`schema_version` 확인. 실패 → `unavailable`.
2. 최상위 금지 키 → 전체 `unavailable`. 기타 미지정 최상위 키 → 해당 항목만 거부(`partial`).
3. 사실별: 금지 키·미허용 필드·타입 오류 → 거부.
4. 인용: 제공된 `document_id`+`revision`만 허용. `text[start:end] == quote` 확인.
   offset이 틀렸으나 인용문이 원문에 **정확히 한 번** 있으면 위치를 교정하고 `relocated=true`, `issues=["offset 교정됨"]`로 표시.
   검증 가능한 인용이 하나도 없으면 거부.
5. 수치 대조: 금액·수량 값이 인용문 안의 숫자 또는 금액 표현(한글 수사 포함: `천팔백만원`, `1억2천만원`)과 일치해야 함. 불일치 → 거부.
6. 결정론적 후처리: 같은 필드의 서로 다른 값 → 모두 `conflicting` + 충돌 질문.
   필수 필드 누락 → 누락 질문. 증빙 파일 없음 → 누락 질문. 파일은 있으나 입증 미확인·반박·충돌 → 입증 질문.
   `supported` 주장은 해당 증빙 유형 문서를 인용할 때만 인정한다.

## 4-1. 문서별 추출 (추출기 0.2.0, 2026-09-25)

첫 실제 모델 시험에서 모델이 문서 간 상충 값 중 하나만 출력해 충돌 탐지가 0/3이었다([기록](sprint-0928/p3-first-inference-2026-09-25.md)).
기본값을 `per_document=True`로 바꿔 문서마다 별도 요청하고, 같은 필드·같은 값은 출처를 병합, 다른 값은 코드가 `conflicting`으로 판정한다.
문서별 요청에서 다른 문서를 인용하면 거부된다. `quote_status=received`는 견적서(`quote`) 문서가 없으면 거부된다.
응답 스키마는 변하지 않았다. `run_record.request_sha256`/`response_sha256`은 요청별 해시 목록이다.

## 4-2. 출처 문서 제한과 부재 값 (추출기 0.3.0, 2026-09-25)

2차 측정에서 정보가 없는 문서의 '없음/모름' 응답이 가짜 충돌을 만들었다(충돌 정밀도 28.6%).

- **물품/용역 구분 제외:** `contract_category`는 모델 추출 대상이 아니다. 담당자가 검토 시작 시 업무 유형을 고르며 정해지는 사건 속성이다(사용자 결정, 2026-09-25). 모델이 내면 미허용 필드로 거부.
- **부재 값:** `tax_status=unknown`은 사실로 받지 않고 누락 질문으로 처리. `quote_status=none`은 인용문에 '없/미제출/받지 않' 등 부정 표현이 있을 때만 인정.
- **필드별 출처 문서:** `estimated_price_krw`·`quote_count`=요청서, `quote_status`=요청서·견적서, `sole_source_basis`·`existing_equipment`=요청서·호환성 사유서, `total_amount_krw`·`unit_price_krw`=견적서. 그 외 문서의 인용만 있으면 거부.

## 5. 실패 시 동작

모든 모델·전송 오류는 예외 대신 `component_status="unavailable"`, 후보 0건, 결정론적 누락 질문, `run_record.error="자동 추출 불가: … 수동 입력하십시오."`를 반환한다.
실행 기록에는 모델명·호스트·문서 해시(SHA-256)·요청/응답 해시를 남기고 원문은 남기지 않는다.

## 6. 시험 범위 (`tests/test_understanding.py`, 27건)

contracts.md 인수 사례와의 대응:

| 인수 사례 | 시험 |
|---|---|
| 세금 포함/제외 충돌 | `test_tax_included_vs_excluded_conflict_raises_question` |
| '견적 받을 예정' + 견적 파일 없음 | `test_planned_quote_without_quote_file_is_not_evidence` |
| 공급자 확인서 존재 ≠ 단독공급 입증 | `test_supplier_file_presence_is_not_proof`, `test_supported_claim_requires_quote_on_evidence_document` |
| 임의 키·출처 없는 금액 | `test_unknown_field_rejected`, `test_amount_without_source_rejected`, `test_hallucinated_amount_not_in_quote_rejected` |
| 문서 내 지시문 | `test_prompt_injection_in_document_is_data`, `test_model_cannot_set_status_or_decision` |
| 외부 통신 차단 | `test_external_hosts_blocked`, `test_external_host_never_called`, 루프백 서버의 프록시 무시·리다이렉트·HTTP 오류·크기 초과 |

미구현(후속): revision 불일치 확인 거부와 stale 처리(Confirmation 경계), 규칙 엔진 연결, 보고서 렌더링, 텍스트 PDF 추출기, 실제 모델 실행.
프롬프트 주입 시험은 "프롬프트 구조와 경계가 모델 출력을 제한한다"는 확인이며, 실제 모델이 주입에 저항하는지는 측정하지 않았다.
