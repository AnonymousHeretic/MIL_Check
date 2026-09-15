# 단계 1: 문서 기반 검토의 공통 데이터 계약 v1

상태: 설계 계약. 아래 이름은 후속 구현의 공통 언어이며 현재 Python API가 아니다.
필수 의미·상태·책임은 먼저 고정하고 모델별 출력은 어댑터가 이 구조로 변환한다.
변경 시 schema_version을 올리고 구버전 읽기/변환 시험을 추가한다.

## 공통 레코드

| 레코드 | 필수 정보 | 제약 |
|---|---|---|
| Document | document_id, revision, content_hash, origin(public/internal/synthetic), media_type, access_scope, available_at | 불변 원문. 새 파일은 새 revision. 텍스트 위치는 해당 revision에 종속 |
| SourceRef | document_id, revision, page 또는 text_start/text_end, quote | offset은 추출 텍스트의 Unicode 문자 기준 [start,end). OCR은 페이지·좌표·OCR 버전도 기록 |
| CandidateFact | fact_id, field, value, unit, source_refs, extractor_version, status | status=proposed/conflicting/missing/rejected. 값 없음은 null; 출처 없는 모델값은 확인 대기, 자동 채택 금지 |
| ConfirmedFact | fact_id, value, unit, source_refs, confirmed_by, confirmed_at, fact_revision | 사용자 직접 입력은 수동 입력 출처와 확인 이력. 모델은 생성 권한 없음 |
| EvidenceAssessment | document_ref, requirement_id, presence, content_support, valid_at, confirmed_by | 파일 존재(present/absent/unknown)와 내용 입증(supported/unsupported/unknown/conflicting)을 독립 기록 |
| ReviewQuestion | question_id, affected_fields, requirement_id, reason, source_refs, status | reason=missing/conflict/unsupported. status=open/answered/dismissed; 답변만으로 자동 확인 완료 금지 |
| SearchRequest | query, purpose, as_of, access_scope, filters | purpose=reference/similar_contract. 조문 직접 조회는 source_id+version 별도 API |
| SearchHit | source_ref, score, score_kind, retriever_version, index_version | 유사도는 법적 확률이 아님. 접근권한/available_at/as_of 조건을 후보 단계에서 적용 |
| ReviewResult | review_id, case_revision, fact_revision, rule_version, source_versions, model_versions, index_versions, findings, questions, component_status | 확정 사실 스냅샷에 종속. component_status=ok/unavailable/partial. 분석 불가를 위험 없음으로 변환 금지 |
| EvaluationRecord | run_id, code_commit, package_hash, dataset_version, split, hardware, command, metrics, errors | 지표별 분자·분모·기권·95% 신뢰구간과 대상 유형 기록. 원문을 공개 로그에 쓰지 않음 |

금액은 정수 원 단위(또는 명시적인 소수 문자열+통화)로 저장하고 부동소수 금액 계산을 피한다.
estimated_price, total_amount, unit_price, quantity, tax_status는 다른 필드다.
날짜 미상과 현재 날짜를 구분한다. 계약일·검토 기준일·문서 가용일·규정 시행일은 합치지 않는다.
모든 자동 출력은 타입·허용 필드·범위·출처 연결을 검증하며 모델 신뢰점수로 사실 확인을 대신하지 않는다.

## 상태 전이와 무효화

1. 문서 수신→추출 후보 및 증빙 평가→누락/충돌 질문.
2. 담당자 확인 행위→확정 사실의 새 revision. 필요한 사실이 미확인이면 해당 검사는 보류한다.
3. 확정 사실+고정 규칙/근거 버전→검토 결과→출처가 연결된 보고서 초안.
4. 원문·금액·사유 수정→영향받는 확인과 결과를 stale로 표시하고 재확인한다.
5. 동일 revision에 대한 중복 확인/재시도는 idempotency key로 중복 기록을 막는다.
6. 보고서는 검토 상태와 component_status를 보존한다. 초안/부분 결과를 최종 승인처럼 표시하지 않는다.

문서 내용은 데이터다. 문서 안의 명령으로 확인 상태·규칙·도구 권한을 변경할 수 없다.

## 모듈 연결 규약

- extract(Document[]) -> CandidateFact[]: 추출기 교체는 이 경계 내부에서 수행.
- assess_evidence(Document[], CandidateFact[], requirements) -> EvidenceAssessment[], ReviewQuestion[].
- confirm(candidates, explicit_user_action, expected_revision) -> ConfirmedFact[]: 오래된 revision의 확인 요청 거부.
- evaluate(confirmed_snapshot, rule_version) -> findings: UI/LLM/검색에 의존하지 않음.
- get_legal_source(source_id, effective_at) -> 원문 버전 또는 명시적 missing.
- search(SearchRequest) -> SearchHit[]: 검색 점수별 보정은 해당 backend 소유.
- render(ReviewResult) -> 보고서: 새로운 사실·판정을 추가하지 않음.

실제 구현 순서는 legacy dict 어댑터→확인 경계→문서 추출 어댑터→대조/질문→보고서다.
기존 MilCheckAgent/CLI는 보존 경로로 유지한다. 새 경로에서 확인을 우회하는 legacy 호출은 허용하지 않는다.

## 후속 구현의 인수 사례

- 동일 금액의 세금 포함/제외 문서 충돌: 질문 표시, 임의 선택 및 통과 금지.
- '견적을 받을 예정'과 견적 파일 없음: 증빙 완료 금지.
- 공급자 확인서만 존재: 파일 존재와 단독공급 입증을 동일시하지 않음.
- 금액 수정 후 이전 확인 요청: revision 불일치로 거부, 이전 보고서 stale.
- LLM 임의 evidence 키/출처 없는 금액: 경계에서 거부 또는 확인 대기.
- 외부/권한 없는/미래 문서: 검색·평가 근거에서 제외.
- Advisor 비활성화 또는 실패: 확정 사실 기반 핵심 검토는 계속 가능.

이 사례들은 후속 제품 PR에서 실행 시험으로 구현한다. 본 설계 PR의 단위시험 통과는 이 기능의 구현 증거가 아니다.
