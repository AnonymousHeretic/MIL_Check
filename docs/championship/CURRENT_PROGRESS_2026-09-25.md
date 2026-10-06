# 현재 진행 상황 (2026-09-25)

## 최신 후속 확인: 2026-10-06 — 텍스트 PDF·설명 문장 입력

Kanana 개발자료 20건 실측은 **준비 완료·GPU 또는 원시 결과 확보 대기**로 유지한다.
실측은 미실행이며 기존 기준값 확보 문서를 보존했다. 이번에는 노션 29절 병행 작업에 대해
사용자가 제공한 입력 경로 범위를 구현했다. 실제 Kanana 전체 흐름 검증과 구분한다.

- 기존 `core-input-metrics` 브랜치와 앞선 미커밋 변경을 유지했다. main에 합치지 않았다.
  AGENTS.md 없음. 핵심 3개 주 지표·5개 보조 지표 분리를 재확인했고 관련 10개 테스트 통과.
- `milcheck/document_input.py`: PDF·직접 설명을 기존 `Document`로 변환.
  문서 ID·유형·1 이상의 개정 번호를 명시적으로 요구하며 파일명으로 유형을 결정하지 않는다.
- PDF 추출 원문·빈 페이지 포함 페이지별 문자 범위를 메모리에 유지한다.
  검증된 `SourceRef`를 문서·개정·페이지 문자 범위에 연결한다. PDF 화면 좌표는 제공하지 않는다.
- 손상·암호화·텍스트 없음·읽기 실패 등은 이유 코드와 직접 텍스트 입력 안내로 실패한다.
  실패를 빈 정상 문서로 반환하지 않는다. OCR·복호화·HWP는 미지원.
- `scripts/understand_input.py`: 입력 확인 및 선택적인 후보 추출 CLI.
  원문·인용문·후보 값을 출력하거나 DB에 저장하지 않는다. 자동 확정·규칙 호출 없음.
- PDF 존재와 내용 입증을 구분하고, 기존 담당자 확인·부가세 처리·충돌 판정은 변경하지 않았다.
  예선 기준선의 pyproject.toml은 유지하고 선택 의존성은 `requirements-input.txt`로 분리했다.

| 검증 | 통과 | 실패 | 건너뜀 |
|---|---:|---:|---:|
| 핵심/보조 입력 지표 관련 테스트 | 10 | 0 | 0 |
| 새 합성 PDF·설명 입력 테스트 | 9 | 0 | 0 |
| `python -m unittest discover -s tests -v` | 121 | 0 | 0 |
| `python scripts/verify_championship_baseline.py` | 48개 파일 동일 | 0 | 0 |

환경: pypdf 6.10.0, reportlab 4.4.9. 테스트는 작은 새 합성 PDF와 설명 문장만 사용했다.
여러 페이지 인용·한글·줄바꿈·빈 페이지·문서 유형·개정 번호·인용 위치 교정·PDF 실패 및
모의 모델 입력→문서 이해→인용 검증을 확인했다. 독립 성능 평가자료가 아니다.
PDF 첨부만으로 content_support를 확정하지 않는 기존 경계도 검증했다.
[전체 테스트 로그](verification/2026-10-06/unittest.log),
[실행 방법·입력 계약·제한사항](document-input-v1.md).

이번 완료는 **입력 경로 구현**이다. **실제 모델 전체 흐름 검증은 미실행**이다.
GPU 추론·모델 다운로드·유료 서버 생성은 없었고 XT-11~60은 열람·실행하지 않았다.
PDF 읽기 순서와 화면 순서는 다를 수 있고 화면 좌표·OCR은 지원하지 않는다.
입력 원문은 메모리에서만 유지하므로 페이지 인용 추적에는 해당 InputDocument가 필요하다.

## 2026-10-05 후속: Kanana 개발자료 20건 기준값 확보 준비

다음 단계 실행을 위해 현재 환경을 확인했다. GPU 장치 없음, 모델 endpoint 설정 없음,
루프백 8000·11434 연결 거부, 업로드 영역과 저장소에서 Kanana 원시 결과 미발견.
따라서 실제 모델 측정은 **선행조건 미충족으로 미실행**이며 기준값을 만들지 않았다.
[실측 절차와 결과 보존 항목](kanana-dev20-baseline.md)을 정리했다.
GPU 모델 서버의 주소·정확한 모델 ID/revision 또는 묶음별 후보 값·출처 원시 결과가
확보되면 지정된 두 개발 파일의 20건만 평가한다. XT-11~60은 열람하지 않았다.
이 후속 준비에서는 평가 코드 변경이나 모델 다운로드·추론·유료 GPU 생성이 없었다.

## 최신 후속 확인: 2026-10-05 — 핵심 3개 평가 지표 정정

- 기준: `work/championship-rebuild` 최신 원격 커밋 `b4060cd`. 별도 작업 브랜치
  `core-input-metrics`에서 구현했으며 main으로 합치지 않았다. 시작 시 사용자 변경 없음,
  AGENTS.md 없음. 노션 접근 도구가 없어 요청에 제공된 29·30절 기준으로 진행했다.
- 평가기 0.3.0: 추정가격·부가세 여부·수의계약 근거 3개를 주 지표로 추가.
  기존 5개는 명시적인 보조 이름으로 제공하며 기존 키도 계속 5개를 의미한다.
  전체 필드 값 지표, 핵심 필드 출처 지표, TP/FP/FN·예측/정답 수·분자/분모·Wilson 구간 제공.
  정의와 호환 정책은 [평가 지침](evaluation-set-and-label-guidelines-v1.md) 최신 절 참조.
- 담당자 확인 전 AI 후보 정확도이며 담당자 확인 후 정확도·법적 판정 정확도가 아니다.
  모델·규칙·확인 흐름·부가세 처리 로직은 변경하지 않았다.

| 검증 | 통과 | 실패 | 건너뜀 |
|---|---:|---:|---:|
| `python -m unittest discover -s tests -v` | 112 | 0 | 0 |
| `python scripts/verify_championship_baseline.py` | 48개 파일 동일 | 0 | 0 |
| 지정 개발자료 20건 gold-echo / summary-only | 집계 점검 통과 | 0 | 0 |

핵심 값 58/58, 출처 73/73, 보조 5개 값 86/86, 출처 116/116.
각 분모는 예측 수와 정답 수 모두 동일하며 FP/FN 0. **gold-echo는 모델 성능이 아니다.**
핵심 오답·누락·출처 혼동·출처 없음·중복·모호 항목 제외·분모 0·묶음 합산과,
품명·견적 수가 보조 점수만 바꾸는지 및 summary-only 메타데이터를 검증했다.
기존 출력 소비 코드 검색 결과 별도 앱 소비처는 없었고 평가기·테스트를 갱신했다.

원시 기록: [테스트](verification/2026-10-05/unittest.log),
[개발자료 집계](verification/2026-10-05/core-input-gold-echo.json).
평가기 해시로 수정 코드 식별 가능하며 code_commit은 실행 당시 기준 head다.
기존 모델 성능·과거 측정 기록과 예선 기준선은 보존했다.
XT-11~60은 열람·수정·평가하지 않았고 GPU 추론·모델 다운로드도 실행하지 않았다.

다음 단계는 **수정된 평가기로 Kanana 개발자료 20건의 기준값 확보**다.
GPU 또는 충분한 원시 결과(묶음별 후보 값·출처)가 필요하다. 실제 모델 기준값과 98% 달성 여부는
아직 미측정이며 기존 전체 정밀도 87.6%에서 역산하지 않는다.

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
