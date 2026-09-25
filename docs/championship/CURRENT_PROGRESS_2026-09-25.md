# 현재 진행 상황 (2026-09-25)

기준 코드: main `324c0e3`(2026-09-15 PR #6) + 이 브랜치 `work/championship-rebuild`의 추가 파일.
9/19~21 작업(로컬 커밋 `f783ad6`, `understanding.py`, 9/21 문서 5종)은 원격에 반영되지 않았고 **소실**됐다. 아래는 설계 문서를 근거로 한 **재작업**이며 복구본이 아니다.

## 이번에 한 일

| 작업 | 산출물 | 직접 확인한 결과 |
|---|---|---|
| LLM 입출력 계약 | `docs/championship/llm-contract-v1.md` | — |
| 문서 이해 경계 구현 | `milcheck/understanding.py` | 루프백 전용·프록시 무시·리다이렉트 거부·응답 크기 제한, 스키마·인용 위치·수치 대조(한글 수사 포함), 후보만 생성 |
| 경계 시험 | `tests/test_understanding.py` (27) | Mock·루프백 서버로 통과 |
| 계약 데이터 품질 감사 | `scripts/audit_contract_data.py`, `sprint-0928/data-quality-audit-2026-09-25.{md,json}` | CSV 5종 실행. 차수 표기 불일치 837행, 공고·계약 직접 연결 키 0, 과거 30,059 재현 불가 등 |
| 평가셋·라벨 지침 | `evaluation-set-and-label-guidelines-v1.md`, `eval/understanding_dev.jsonl`(합성 10묶음), `scripts/eval_understanding.py` | gold-echo 채점기 점검 100%(모델 성능 아님) |
| 검색 기준선 재측정 | `scripts/eval_retrieval_report.py`, `sprint-0928/retrieval-evaluation-2026-09-25.{md,json}` | ngram R@1 96.7%(95% CI 83.3–99.4), 말뭉치 17문서 |
| 평가 도구 시험 | `tests/test_eval_tools.py` (2) | 통과 |

## 전체 검증 (이 작업공간, Python 3.11.15, GPU 없음)

- `python -m unittest discover -s tests` → **79/79 통과** (기존 50 + 신규 29).
- `python scripts/verify_championship_baseline.py` → 기존 48개 파일 바이트 동일.
- `python -m milcheck.cli evaluate` → 9/15 `candidate-evaluation.json`과 **완전히 동일**.
- 기존 파일은 하나도 수정하지 않았다(신규 파일만 추가).

## 하지 않은 것 / 차단

- 실제 LLM 다운로드·추론·품질 측정 없음(GPU·런타임 미확보). `eval_understanding.py --mode live`는 서버가 없으면 전 묶음 `unavailable`로 끝난다.
- 확인(Confirmation) 경계, 규칙 엔진 연결, 보고서 렌더링, PDF 텍스트 추출은 미구현.
- 최종 시험세트 미생성. 정량 목표치 미정.
- 9/19에 보고된 "57 tests", 9/21 "61 tests"는 이번 79개와 다른 코드 기준이므로 비교하지 않는다.

## 다음 한 단계

이 파일들을 GitHub `work/championship-rebuild` 브랜치로 올려 보존한다(이 세션은 저장소 쓰기가 차단돼 사용자가 웹으로 업로드).
