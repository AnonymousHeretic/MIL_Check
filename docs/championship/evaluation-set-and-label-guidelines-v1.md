# 평가셋 구성·라벨 지침 v1

## 2026-10-05 정정: 핵심 3개 주 지표 (평가기 0.3.0)

최신 적용 기준은 노션 29·30절에 대해 이번 작업 요청에 제공된 기준이다.
이 환경에는 노션 접근 도구가 없어 최신 원문을 직접 확인하지 못했다.
아래 10/03의 5개 범위 정의는 과거 기록으로 보존하며, 98% 목표 대상 정의는 이 절로 정정한다.

- **주 지표** `core_decision_input_precision/recall`: `estimated_price_krw`,
  `tax_status`, `sole_source_basis`의 (필드, 값) 일치 합산. 정밀도 목표 98%의 대상이다.
- **보조 지표** `auxiliary_decision_input_5_precision/recall`: 위 3개에 `item_name`,
  `quote_count`를 더한 기존 5개 범위. 품명·견적 수의 정오답은 주 지표를 바꾸지 않는다.
- 두 범위의 `*_source_precision/recall` 및 `input_field_metrics` 내 `*_source`는
  (필드, 값, 문서 ID) 일치로 별도 집계한다. 값 일치가 출처 일치를 보장하지 않는다.
- `input_field_metrics.per_field`에는 전체 필드의 값 지표를 유지한다.
  `core_per_field_source`에는 핵심 각 항목의 출처 지표를 제공한다.
- 각 집계·필드에 `tp`, `fp`, `fn`, `pred`(예측 수), `gold`(정답 수)를 제공한다.
  정밀도는 TP/예측 수, 재현율은 TP/정답 수이며 `num`, `den`, `value`,
  기존 `wilson_95_ci`를 기록한다. 묶음별 집합의 건수를 합산하는 micro 방식이다.
- 동일 값/출처는 묶음 안에서 중복 제거하고 상충 값은 각각 유지한다. 여러 묶음에
  같은 값이 있어도 각 묶음별로 센다. 모호 항목은 양쪽에서 제외하며 제외 횟수를 남긴다.
  출처 없는 후보는 출처 None으로 예측 분모에 포함하여 FP로 센다. 분모 0은 값·구간 null.
- 이는 **담당자 확인 전 AI 추출 후보 정확도**다. 담당자 확인 후 정확도나 법적 판정
  정확도가 아니다. 담당자 확인값만 규칙에 전달하고 부가세를 임의로 1.1로 나누지 않는 원칙은 유지한다.

### 출력 호환성과 기록 해석

평가기 버전은 `understanding-eval-0.2.0` → `understanding-eval-0.3.0`이다.
`input_metric_profiles`에 두 범위의 역할·대상 필드·버전·평가 단계·집계 단위를 기록한다.
`--summary-only`에도 이 메타데이터와 두 범위 및 필드별 집계가 포함된다.
기존 `decision_input`, `decision_input_source`, `decision_input_precision/recall`와
`decision_input_profile`은 **계속 5개 보조 범위**를 뜻하는 호환 키다. 3개로 재해석하지 않는다.
저장소 실행 코드의 소비처는 평가기와 테스트이며 별도 앱 소비 코드는 발견되지 않았다.
과거 JSON과 모델 성능 기록은 수정하지 않았다. 기존 전체 정밀도 87.6%로 핵심 점수를 역산할 수 없다.

```bash
python scripts/eval_understanding.py --mode gold-echo --data eval/understanding_dev.jsonl,eval/external/XT_01-10_dev.jsonl --summary-only --output /tmp/core-input-check.json
```

이번 개발자료 20묶음 점검: 핵심 값 TP/예측/정답 58/58/58, 핵심 출처 73/73/73,
보조 값 86/86/86, 보조 출처 116/116/116. 모두 FP/FN 0, 정밀도·재현율 1.0.
이는 gold-echo 채점기 점검이며 모델 성능·98% 목표 달성 증거가 아니다.
[집계 기록](verification/2026-10-05/core-input-gold-echo.json).
XT-11~60 개별 자료·정답·오답은 열람·실행하지 않았다.
다음 단계는 **수정된 평가기로 Kanana 개발자료 20건의 기준값 확보**이며,
GPU 또는 충분한 원시 결과(후보 값·출처와 묶음 대응)가 필요하다.

## 2026-10-03 추가: 판정 입력 후보 평가

최신 작업 기준은 [노션 개발 일지](https://www.notion.so/3e69d292c30f8102a3d7fdd52e017993) 15~27절이다.
아래 2026-09-25 기록의 '최종 시험세트 미생성·목표 미정·실제 LLM 미측정'은 당시 상태다.
노션에는 개발 20건, 별도 작성 합성 평가 49건(XT-57 사전 제외), Kanana 1.5 8B 선정과
9/27 비교 결과가 기록되어 있다. 이번 작업에서는 그 모델 결과를 재측정하지 않았다.

### 집계 범위와 해석

`scripts/eval_understanding.py`는 전체 추출 지표를 유지하며 다음 지표를 추가한다.

| 필드 | 실제 코드에서의 용도 |
|---|---|
| item_name | 담당자 확인 후 필수 입력 검사·근거 검색 |
| estimated_price_krw | 담당자 확인 후 부가세 제외 추정가격 입력 후보 |
| tax_status | 후보 금액을 부가세 제외로 사용할 수 있는지 확인하는 조건 |
| sole_source_basis | 담당자 확인 후 수의계약 사유 분기 |
| quote_count | 담당자 확인 후 quote_count_planned로 전달(견적 수 검사) |

범위는 `workflow.confirm` 및 `RuleEngine`을 기준으로 정했다. 업체명은 규칙 판정에 직접
사용되지 않아 별도 필드 지표로 확인한다. 계약 대상·검토 유형·증빙 충족 여부는 담당자
입력이므로 AI 추출 지표에 포함하지 않는다. 견적 수는 현재 호환성 경로에서 판정을
바꾸지 않아도 연결된 규칙의 입력이므로 포함한다. 필드 범위는 결과를 보고 바꾸지 않는다.

- `decision_input_precision/recall`: 위 5개 필드의 **담당자 확인 전 검수 통과 후보**에 대해
  (필드, 값) 일치 건수를 합산한다. 건별 비율의 평균이 아닌 전체 분자/분모 합산이다.
- `input_field_metrics.per_field`: 모든 추출 필드별 TP/FP/FN, 예측·정답 수, 정밀도·재현율.
- `decision_input_source`: (필드, 값, 문서 ID) 일치. 같은 값이라도 인용 문서가 다르면 오답.
  값 지표와 단위·분모가 다르며, 인용문이 법적 의미까지 입증한다는 지표가 아니다.
- 같은 필드·값/출처 중복은 1개로 센다. 상충하는 두 값은 각각 센다. `ambiguous_fields`는
  기존 사실 지표처럼 제외하고 제외된 필드별 사례 수를 남긴다. 분모 0은 100% 대신 null.
- 비율에 분자·분모와 Wilson 95% 구간을 기록한다. 사실은 같은 문서 안에서 상관될 수 있어
  이 구간을 독립 표본에 대한 일반화 보장으로 해석하지 않는다.

노션의 '판정 입력 항목 정밀도 98%' 목표와 비교할 **운영 정의 초안**이다. 최종 보고 시
반드시 위 5개 필드와 확인 전 후보 단계임을 병기한다. 담당자 확정값 정확도·금액과 부가세의
올바른 대응·최종 규칙 판정 정확도는 이 점수로 대체하지 않는다. 목표 달성은 미측정이다.

### 실행 및 기록

저장소 루트에서 CPU만으로 채점기를 점검한다. 100%는 정답 대입 시험으로 모델 성능이 아니다.

```bash
python scripts/eval_understanding.py --mode gold-echo --summary-only --output /tmp/input-field-check.json
```

실제 모델 서버가 준비된 뒤 같은 평가 명령에 `--mode live`와 사전 확정된 데이터·모델 설정을
사용한다. 9/27 비교 조건은 문서별 처리와 출력 형식 고정이었다. 모델 버전·생성 설정은 별도로
고정하고, `MILCHECK_LLM_PER_DOCUMENT=1`, `MILCHECK_LLM_STRUCTURED=1`을 확인한다.
현재 사용 가능한 GPU·서버가 확인되지 않았으므로 이번에 외부 실행을 시작하지 않았다.

`--summary-only`는 사례별 오답·정답·거부 상세를 결과 파일에서 제외한다. 독립 시험에는 이를
사용하되 집계에 기반해 모델을 선택·수정한 이력도 별도로 관리한다. 상세 오류 분석은 개발세트에서
진행한다. 이번에는 XT-11~60의 내용·정답·오답을 열람하거나 실행하지 않았다.
출력에는 평가기 버전·파일 해시, 입력 파일별 SHA-256, 출력 형식 고정 여부도 남는다.
9/27 Kanana 개별 결과 파일은 현재 저장소에서 찾지 못했으므로 기존 87.6%에서 새 점수를
추산하지 않는다. 이 지표의 실제 기준값은 원시 결과 확보 또는 새 실측 후 보고한다.

---

## 2026-09-25 초기 기록

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
