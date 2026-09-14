# 단계 0: 현재 구조와 기준선

## 고정 버전

- 저장소: AnonymousHeretic/MIL_Check
- 기준 커밋: `68410c3ef17418341b278f8be5c37773106359f7`
- 기준 트리: `aa5373ced0bedd7cd03d582b2e868b66de64880c`
- 커밋 제목: Show public-data metadata for split demo
- 파일 식별자는 [baseline-manifest.json](baseline-manifest.json)에 기록했다.
- Git blob SHA는 Git 객체 식별자이며 일반 파일 SHA-256과 다르다.
- 코드뿐 아니라 data/, eval/, 기존 결과 문서도 동일 커밋으로 보존한다.
- 이 PR의 부모 커밋이 기준 버전이다. 과거 제출본과의 일치는 별도 조사 대상이다.

## 실제 코드의 의존관계

| 진입점·모듈 | 직접 의존 | 역할과 결합 지점 |
|---|---|---|
| demo_app.py | agent.py | HTTP 처리·프리셋·입력 확인 화면·보고서 렌더가 한 파일에 있음 |
| milcheck/cli.py | agent.py, evaluation.py | JSON 검토·자연어 추출·평가. intake --review는 추출 필드를 바로 review에 전달 |
| agent.py / MilCheckAgent | extract, rules, retriever, ml, contracts, llm | 객체 생성·실행 순서·결과 조립·Markdown 출력까지 담당 |
| extract.py | 선택적 LLM 객체 | 자유서술을 dict로 변환. extract_with_llm이 비어 있는 키를 보강 |
| rules.py | data/rules.json | 입력 dict와 규칙팩으로 조건 검사, 마지막에 전체 상태 결정 |
| retriever.py | data/corpus.jsonl | BM25, 문자 2~4-gram TF-IDF, 코사인, RRF. 적재 시 검색 구조 생성 |
| ml.py | linear_model.py, 모델 gzip 2종 | 조항·계약방법 추천, 유형 매핑, 확률·유사도 기반 기권 |
| contracts.py | linear_model.normalize_name, 계약 gzip | 문자 3~4-gram 역색인·코사인; 유사계약·가격 분포·분할 후보 분석 |
| llm.py | urllib.request, 환경변수 | OpenAI 호환 서버에 추출·요약 요청 |
| evaluation.py | 규칙·추천·추출·검색, eval/ | 기존 사례 평가. 전체 학습·평가를 모두 재현하는 단일 명령은 아님 |
| train_models.py | pandas, numpy, sklearn | 공개망 학습·모델 내보내기 |
| build_index.py | train_models, analyze_audit, contracts | 공개 원본 정제 후 압축 자료 생성. 기본 출력은 artifacts/ |
| evaluate_robustness.py, evaluate_comprehensive.py | 학습 라이브러리·계약 색인 | 재학습 평가. 자료 형식 호환성을 먼저 확인해야 함 |

경로는 모두 저장소 루트 기준이다. 실제 소스 확인은 manifest의 커밋과 blob으로 추적한다.

### 검색에 연결된 숨은 의존관계

1. agent.review는 규칙 결과의 legal_ground를 검색 질의에 덧붙인다. 적용 조문 직접 조회와 관련 자료 검색이 분리되지 않았다.
2. agent.review는 계약 검색 최고 유사도를 MLAdvisor.advise의 similarity_max로 전달한다. 검색 교체는 ML 기권에도 영향을 준다.
3. ContractIndex의 검색은 화면의 상위 유사계약, 가격 분포, 분할 합산 후보 확보에 재사용된다.
4. 분할 후보는 상위 50건 검색 후 날짜 등을 검사한다. 순위 검색의 절단이 분석 후보 누락으로 이어질 수 있다.
5. HybridRetriever.search의 기본값은 hybrid지만 MilCheckAgent의 기본 retrieval_mode는 ngram이다. 클래스명·주석만 보고 실제 선택 방식을 판단하지 않는다.
6. 문자 벡터·IDF는 자료 적재 시 재계산된다. 색인 갱신은 유사도와 임계값 판단에 영향을 줄 수 있다.
7. n-gram 분류 특징, 법령 검색 특징, 계약 검색 특징은 서로 다른 용도다. 하나의 모델 교체로 묶지 않는다.

## 알려진 위험과 후속 조치

아래는 고정 소스에서 확인한 구조적 위험이다. 이전 대화에서 재현한 현상도 포함하지만 이번 PR에서 재실행한 결과로 표기하지 않는다.

| ID | 근거 위치 | 문제 | 후속 검증·수정 |
|---|---|---|---|
| B01 | extract.extract / rules._small_amount | 업체 유형 명칭 불일치 | 공통 enum·변환과 같은 의미 입력의 동등성 시험 |
| B02 | extract_with_llm / llm.extract_fields | 보강 키의 코드 수준 허용 목록·타입 검증 부족 | 임의 키·근거 없는 증빙을 거부하는 시험 |
| B03 | extract의 금액·견적·증빙 처리 | 첫 금액·수량 혼동, 예정과 완료 구분 부족 | 다중 금액·부정·예정·대조 문장 회귀시험 |
| B04 | agent.review / cli.intake_command | 백엔드의 확인된 사실 계약 부재 | UI·CLI 공통 확인 경계; 자동 입력도 검증 출처 명시 |
| B05 | rules._additional_type | 유형별 검사 깊이 차이 | 지원/부분/안내 전용 표, 요건·예외 검증 |
| B06 | contracts.split_order_candidates | top-k 절단, 날짜 누락, 동일 건 식별, 고정 상한 | 조건 우선 후보 확보·고유번호·규정 기준 연결 |
| B07 | contracts.price_band / analyze_audit.py | 런타임 총액 분포와 오프라인 MAD 분석은 다름 | 기능별 실제 계산·데이터 요구·표시 의미 분리 |
| B08 | llm.LocalLLMConfig / _chat | 주소 설정만으로 내부망 제한이 보장되지 않음 | 승인 대상 제한·시간 제한·연결 실패 시험 |
| B09 | evaluation.eval_extraction | 증빙은 정답의 상위집합이면 성공 | 오추출 정밀도·거짓 보유 판정도 측정 |
| B10 | build_index / 평가 스크립트 | 생성기는 t/g, robustness는 d/p 참조 | 실제 압축 자료 키 확인 후 공통 reader·형식 버전 도입 |
| B11 | ml.advise | 기권 표시와 signals 생성 조건이 완전히 같지 않음 | 기권 상황에서 충돌 신호의 정책·출력 일관성 검증 |
| B12 | README·metrics·평가자료 | 과거 수치·사례 수·테스트 수의 버전 관계 불명 | 실행 결과마다 코드·자료·분모·명령 연결 |

## 재현 절차: 후속 실행 환경에서 수행

별도 체크아웃을 사용해 기존 결과 파일을 덮어쓰지 않는다.

```bash
git clone https://github.com/AnonymousHeretic/MIL_Check.git milcheck-baseline
cd milcheck-baseline
git checkout --detach 68410c3ef17418341b278f8be5c37773106359f7
python --version
python -m unittest discover -s tests -v
python -m milcheck.cli evaluate --output /tmp/milcheck-baseline-evaluation.json
```

단위시험 실패도 기준선에 보존한다. 원인을 고치지 않고 과거 기대값을 바꿔 통과시키지 않는다.
CLI evaluate의 종료코드만으로 모든 계층이 목표를 충족했다고 판단하지 않는다.
--no-ml은 일부 계층 평가이며 모델 성능 재현을 의미하지 않는다.

전체 분류·강건성 평가는 자료 키·원본 가용성 확인 후 requirements-training.txt 환경에서 별도로 실행한다.
build_index.py의 기본 출력 artifacts/와 운영 data/ 사이의 반입 과정도 기록한다.
기존 workflow는 수동 실행 또는 제한된 브랜치 push 조건이며 pull_request 트리거가 없다.
따라서 이 문서 PR에서 평가 CI가 자동 통과했다고 주장하지 않는다.

각 실행 기록: 코드 commit, 입력 blob/해시, 규칙·모델·색인 버전, Python·패키지·OS·CPU·RAM,
실행 명령, 시작/종료, 종료코드, 로그, 지표의 분자·분모, 실패 및 누락 계층.
모든 시험 결과를 확보하기 전 상태는 not_run 또는 partial로 기록한다.

## 완료 범위

현재 커밋 및 파일 정체성 보존, 코드 의존관계와 위험 목록은 작성 완료.
실행 기준선 측정, 제출 당시 버전 확인, 원본 데이터 기반 분류 재현은 미완료이며 후속 작업으로 남긴다.
