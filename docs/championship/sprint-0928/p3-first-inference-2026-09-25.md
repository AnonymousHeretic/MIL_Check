# P3 첫 실제 LLM 추론 기록 (2026-09-25)

출처: 사용자가 RunPod 웹 터미널 출력을 대화에 붙여 넣은 값. 원본 `live-qwen3-4b.json`은 Pod 종료로 보존되지 않았다.
코드: `work/championship-rebuild` `8d00f6b`(추출기 0.1.0, 묶음 전체를 한 번에 요청). 1회 실행.

| 항목 | 값 |
|---|---|
| 장비 | RunPod RTX A5000 24GB ×1 (24,564 MiB), Container Disk 40GB |
| 소프트웨어 | Runpod Pytorch 2.8.0 템플릿, Python 3.12.3, vLLM 0.30.0 |
| 모델 | `Qwen/Qwen3-4B` BF16, revision 미고정, max-model-len 16384, enable_thinking=false, temperature 0 |
| 자료 | `eval/understanding_dev.jsonl` 합성 개발세트 10묶음(정답 사실 64) |
| 비용 | 약 USD 0.44 (충전 20.00 → 잔액 19.56) |

## 지표

| 지표 | 값 |
|---|---|
| 형식 실패(unavailable) | 0/10 |
| 사실 정밀도 | 50/69 = 72.5% (contract_category 9건 제외 시 50/60 = 83.3%) |
| 사실 재현율 | 50/64 = 78.1% |
| 충돌 재현율 | 0/3 |
| 누락 질문 재현율 | 3/3 |
| 조작된 필수값 | 0 |
| 증빙 유무 정확도 | 10/10 (결정론적) |
| 경계 거부 항목 | 35 (사유 미수집) |
| 묶음당 지연 | p50 15.13초, 최대 22.18초 |
| GPU 메모리 표시 | 20,047 MiB — vLLM `gpu-memory-utilization 0.85` 사전 할당값, 모델 필요량 아님 |

## 묶음별

| 묶음 | 상태 | 맞음/정답 | 예측 | 거부 | 놓침 | 틀림 |
|---|---|---|---|---|---|---|
| UD-01 | partial | 10/10 | 11 | 2 | — | contract_category |
| UD-02 | partial | 6/8 | 9 | 5 | sole_source_basis, tax_status(별도) | contract_category, existing_equipment(문장), unit_price←합계 |
| UD-03 | partial | 0/6 | 0 | 7 | 전부 | — |
| UD-04 | partial | 4/5 | 8 | 5 | sole_source_basis=single_supplier | contract_category, quote_status=received(견적서 없음), basis=compatibility, total←추정가격 |
| UD-05 | ok | 5/5 | 6 | 0 | — | contract_category |
| UD-06 | partial | 4/4 | 5 | 2 | — | contract_category |
| UD-07 | partial | 8/10 | 10 | 4 | sole_source_basis, tax_status(별도) | contract_category, existing_equipment |
| UD-08 | partial | 7/8 | 10 | 5 | supplier_name(누리통신) | contract_category, existing_equipment, unit_price←합계 |
| UD-09 | partial | 4/5 | 7 | 3 | estimated_price(1억 2천만원) | contract_category, existing_equipment, quote_status=none |
| UD-10 | partial | 2/3 | 3 | 2 | sole_source_basis | quote_status=planned(정답 라벨 없음) |

## 해석과 조치 (추출기 0.2.0에 반영, 재측정 전)

1. 충돌 0/3 → 문서별로 따로 추출하고 충돌은 코드가 판정(`per_document=True` 기본값).
2. 견적서 없이 `quote_status=received` → 경계에서 거부.
3. 필드 혼동(합계↔단가, 추정가격↔합계, 장비명에 문장) → 프롬프트에 필드별 정의 추가.
4. 거부 사유 미수집 → 평가 결과에 `rejected_detail` 기록.
5. `contract_category` 라벨 기준은 미결정(명시만 인정 vs 문서 유형 추론). 0.2.0 프롬프트는 "명시된 경우에만"으로 지시.

합성 개발세트 결과이며 최종 성능·군 현장 성능으로 인용하지 않는다.
