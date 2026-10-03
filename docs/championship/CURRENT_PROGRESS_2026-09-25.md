# 현재 진행 상황 (2026-09-25)

## 최신 후속 확인: 2026-10-03

최신 기준은 [노션 챔피언십 개발 일지](https://www.notion.so/3e69d292c30f8102a3d7fdd52e017993)
15~27절이며, 아래의 9/25 미완료 목록은 과거 기록이다. 새 현황 문서를 중복 생성하지 않고
이 파일에 후속 작업을 추가한다.

### 확인된 완료 범위

- 작업 브랜치 `work/championship-rebuild`, 작업 시작 원격 head `fc99418`.
- 노션 9/27 기록: Kanana 1.5 8B 선정, 같은 조건 모델 비교, 개발계획서 최종 제출본 정리.
  비교 점수는 기존 실측 기록이며 이번 실행 결과가 아니다.
- 저장소 직접 확인: 문서 이해·출력 형식 고정, 담당자 확인→규칙 판정→결과서→SQLite 기록.
  `run_case_demo.py`의 확인값은 시연용 자동 선택이다. 실제 담당자 확인 화면 완료로 보지 않는다.
- 실제 LLM을 통한 한 건 전체 흐름, 웹 화면, 새 독립 평가, 폐쇄망 인수시험은 미완료.

### 이번 완료: 판정 입력 후보 평가기

- `scripts/eval_understanding.py`: 품명·추정가격·부가세·수의계약 사유·견적 수의
  정밀도/재현율, 전체 필드별 TP/FP/FN, 출처 문서까지 일치하는 별도 지표 추가.
- 담당자 확인 전 후보 평가이며, 최종 판정 정확도나 담당자 확정값 정확도로 주장하지 않는다.
  범위·분모·한계는 [라벨 지침](evaluation-set-and-label-guidelines-v1.md)에 기록.
- 분자·분모·Wilson 구간, 입력/평가기 해시, 출력 형식 고정 설정 기록.
- `--summary-only`로 사례별 오답 노출 없이 집계 저장. 빈 입력·중복 ID는 실패 처리.
- 신규 회귀 8건: 오답 금액, 누락, 출처 혼동, 인용 중복, 상충 값, 애매 라벨 제외,
  미측정 분모, 집계 전용 출력, 빈 입력·중복 입력 등을 검증.

### 이번 직접 실행

환경: Python 3.12.14, CPU, 실제 LLM 미호출. 첫 전체 테스트는 샌드박스의 소켓 생성 제한으로
루프백 서버 시험이 막혔고, 같은 명령을 소켓 허용 환경에서 재실행했다.

| 명령 | 결과 |
|---|---|
| `python -m unittest discover -s tests -v` | 110건: 109 통과, jsonschema 미설치로 1건 건너뜀. 실패 0 |
| `python scripts/verify_championship_baseline.py` | 예선 48개 파일 바이트 동일 |
| `python scripts/eval_understanding.py --mode gold-echo --summary-only --output docs/championship/verification/2026-10-03/input-field-gold-echo.json` | 개발 10건, 판정 입력 후보 39/39. 정답 대입으로 채점기만 점검(모델 성능 아님) |

원시 로그: [테스트](verification/2026-10-03/unittest.log),
[채점기 점검](verification/2026-10-03/input-field-gold-echo.json).
JSON의 `code_commit`은 실행 당시 부모 head이며, 수정한 평가기는 `evaluator_sha256`으로 식별한다.
이번에 XT-11~60 내용·정답·오답을 열람하거나 재평가하지 않았다. 모델·프롬프트·검수 로직도 변경하지 않았다.

### 다음 한 단계와 선행조건

Kanana를 같은 문서별·출력 형식 고정 조건으로 **개발세트 20건**에 실행해 새 지표의 기준값과
필드별 오류를 확보한다. GPU 서버 접근 또는 해당 실행의 원시 결과가 필요하다.
현재 저장소에 9/27 Kanana 개별 결과가 없으므로 과거 전체 정밀도로 새 점수를 추산하지 않는다.
실제 추론 후 금액·부가세 등 우선 오류를 개선하고, 조정에 쓰지 않은 새 세트로 독립 재평가한다.

---

## 2026-09-25 재작업 당시 기록

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
