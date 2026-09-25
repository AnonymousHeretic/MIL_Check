# 평가셋 구성·라벨 지침 v1

기준일: 2026-09-25. 상위: [evaluation-plan.md](evaluation-plan.md), [contracts.md](contracts.md), [llm-contract-v1.md](llm-contract-v1.md).

> 9/21 지침(소실)을 대체하는 새 문서다. 이 문서의 **개발세트는 만들었고**, 최종 시험세트는 **아직 만들지 않았다**.

## 1. 세트 현황과 용도

| 세트 | 파일 | 규모 | 성격 | 허용 용도 | 금지 |
|---|---|---|---|---|---|
| 기존 추출 holdout | `eval/extraction_holdout.jsonl` | 20건 | 9/15 결과까지 기록됨(노출) | 개발·회귀 | "홀드아웃"·최종 성능으로 인용 |
| 기존 검색 | `eval/retrieval_cases.jsonl` | 30질의 | 반복 사용됨 | 회귀 | 일반화 성능 주장 |
| 기존 감사사례·규칙 | `eval/audit_cases.jsonl`, `eval/cases.jsonl` | 12 / 60 | 자체·공개 라벨 | 회귀 | 정확도 주장(규칙 회귀) |
| 문서 이해 개발세트 | `eval/understanding_dev.jsonl` | 10묶음, 정답 사실 64개 | **합성**, 작성자=개발자 | 프롬프트·모델·임계값 선택 | 최종 성능·군 현장 성능 인용 |
| 문서 이해 최종 시험세트 | 미생성 | — | 개발자 비공개 예정 | 1회 최종 측정 | 튜닝에 사용(사용 시 개발세트로 강등) |

## 2. 문서 묶음 형식

```json
{"bundle_id": "UD-01", "split": "dev", "origin": "synthetic|public|internal", "note": "...",
 "documents": [{"document_id": "R1", "revision": 1, "doc_type": "request", "text": "..."}],
 "gold": {"facts": [{"field": "...", "value": ..., "document_id": "R1", "quote": "원문 그대로"}],
          "conflict_fields": [], "evidence_absent": ["EV-QUOTE"], "missing_fields": [],
          "ambiguous_fields": []}}
```

- `doc_type`: request / quote / supplier_confirmation / compatibility_statement / other.
- 필드·값 체계는 [llm-contract-v1.md](llm-contract-v1.md) §3을 그대로 쓴다.

## 3. 라벨 규칙

1. **문서에 적힌 것만** 정답이다. 상식·계산으로 채우지 않는다(단가×수량이 맞아도 `total_amount_krw`는 적힌 경우에만).
2. `quote`는 원문 문자열 그대로. 같은 필드 값이 문서마다 다르면 **모두** 정답에 넣고 `conflict_fields`에 필드명을 쓴다.
3. 금액: `estimated_price_krw`(요청서의 추정가격), `total_amount_krw`(견적 합계), `unit_price_krw`는 서로 다른 필드. 원 단위 정수.
4. `tax_status`: "부가세 별도/제외" → vat_excluded, "포함" → vat_included, 표기 없음 → 라벨 없음(모델이 unknown을 내도 정답으로 치지 않음).
5. `quote_status`: 견적서 파일이 있으면 received, "받을 예정"은 planned, "견적 없음"이 명시되면 none.
6. `evidence_absent`: 해당 유형 파일이 **묶음에 없을 때만**. 파일이 있으나 내용이 부족한 경우는 absent가 아니다(내용 입증은 별도 라벨, v2 예정).
7. `missing_fields`: 필수 필드(item_name, estimated_price_krw, tax_status, sole_source_basis, supplier_name) 중 문서에 근거가 없는 것.
8. 법적 해석이 필요한 분류(예: 제26조①2호 사목과 바목 중 어느 쪽인가)는 `ambiguous_fields`로 채점에서 제외하고 실무자 라벨을 기다린다.
9. 문서 안의 지시문(프롬프트 주입)은 사실이 아니다. 라벨에 반영하지 않는다.

## 4. 분할 규칙

- 묶음 단위(사업·계약·변경 차수·파생 문서 전체)로 한 세트에만 둔다. 같은 원본에서 만든 변형 문서는 같은 세트.
- 합성·공개·내부 출처를 섞어 보고하지 않는다. 지표는 출처별로 따로.
- 개발세트 결과를 보고 규칙·프롬프트를 고친 뒤 같은 세트로 재측정한 값은 "개발 성능"으로만 표기.

## 5. 최종 시험세트 만드는 절차 (미수행)

1. 작성자: 개발자가 아닌 사람(실무 경험자 우선). 불가능하면 그 사실을 명시한다.
2. 구성 목표: 호환성·단독공급 물품 묶음 최소 30개. 충돌·누락·주입·한글 수사·다중 문서를 개발세트와 **다른 문장·품목·업체**로.
3. 2인 독립 라벨 → 불일치 조정 기록. 일치율을 함께 보고.
4. 파일 SHA-256을 먼저 기록해 동결(예: `eval/final/manifest.json`)하고, 모델·프롬프트·임계값을 고정한 뒤 **1회** 실행.
5. 실행 후 오류 분석 결과로 무언가를 고치면 그 세트는 개발세트로 강등하고 새 최종 세트를 만든다.
6. 정량 목표(예: 필드 정밀도·충돌 재현율·조작 필수값 0건)는 실행 **전에** 이 문서에 기록한다. 현재 목표치는 **미정**이다.

## 6. 채점 (`scripts/eval_understanding.py`)

| 지표 | 정의 |
|---|---|
| fact_precision / recall | (필드, 값) 쌍 일치. ambiguous 제외 |
| conflict_recall / precision | `conflicting` 표시 필드 대 `conflict_fields` |
| missing_question_recall | 누락 질문의 필드 대 `missing_fields` |
| fabricated_required_fields | 정답상 누락인 필수 필드를 모델이 채운 수 — **0이어야 함** |
| evidence_presence_accuracy | 결정론적 산정. 100%가 아니면 코드 결함 |
| unavailable_bundles, rejected_items | 형식 실패·경계 거부 |
| latency | 묶음별 전체 소요(모델 포함) |

`--mode gold-echo`는 정답을 모델 응답처럼 넣어 채점기·경계를 점검한다. 2026-09-25 실행: 모든 지표 100%, 거부 0 ([기록](sprint-0928/understanding-gold-echo-2026-09-25.json)). **모델 성능이 아니다.**
실제 모델 측정(`--mode live`)은 GPU·런타임 확보 후 수행한다.
