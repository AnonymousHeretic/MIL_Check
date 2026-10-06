# Kanana 개발자료 20건 기준값 확보

상태: **준비 완료·GPU 또는 원시 결과 확보 대기**. 실측 미실행(2026-10-06 유지).

2026-10-05 준비 상태: 평가기 `understanding-eval-0.3.0`, 작업 브랜치
`core-input-metrics`, 기준 커밋 `b4060cd` + 이 작업공간의 평가기 수정.
현재 변경 파일을 포함한 코드로 실행해야 한다. 원격 기준 브랜치를 새로 clone하는 것만으로는
수정된 평가기가 포함되지 않는다.

## 현재 확인과 필요한 입력

현재 작업공간에는 GPU 장치·설정된 모델 endpoint·Kanana 원시 결과가 없고,
루프백 8000 및 11434 포트도 연결 거부 상태다. 실제 모델 측정은 미실행이다.
GPU 서버 또는 충분한 원시 결과가 필요하다. 서버 사용 시 정확한 Kanana 모델 ID와 revision,
양자화·서버 버전·생성 설정을 먼저 기록한다. 모델 선정은 기존 Kanana 1.5 8B를 유지한다.
원시 결과 재채점은 묶음별 검수 통과 후보 값·출처가 있어야 하며, 전체 비율이나
false/missed 목록만으로 출처 점수를 복원하지 않는다.

## 실측 명령

GPU 서버와 모델이 준비된 환경에서 아래 placeholder를 실제 값으로 설정한다.
기존 추출 경계는 루프백 또는 명시적으로 승인된 호스트만 허용한다.
원격 주소를 사용할 경우 해당 호스트를 `MILCHECK_LLM_ALLOWED_HOSTS`에 설정한다.

```bash
export MILCHECK_LLM_BASE_URL='<OpenAI 호환 서버의 /v1 주소>'
export MILCHECK_LLM_MODEL='<서버가 제공하는 정확한 Kanana 모델 ID>'
export MILCHECK_LLM_PER_DOCUMENT=1
export MILCHECK_LLM_STRUCTURED=1
mkdir -p out/kanana-dev20
python scripts/eval_understanding.py --mode live --data eval/understanding_dev.jsonl,eval/external/XT_01-10_dev.jsonl --output out/kanana-dev20/baseline.json
```

측정 자료는 위 두 개발 파일의 20묶음으로 고정한다. XT-11~60을 추가하거나 열람하지 않는다.
개발 오류 분석용이므로 상세 출력은 보존한다. 집계만 공유할 때는 별도 복사본에서
`bundles`를 제외하고 메타데이터·전체 집계를 유지한다. 재추론을 단순히 상세 제거 목적으로 실행하지 않는다.

## 결과 확인과 보존

- `mode=live`, `n_bundles=20`, 두 입력 파일명·SHA-256, 평가기 버전·해시와 코드 커밋 확인.
- `per_document=true`, `structured=true`, 정확한 모델 ID 확인.
- `unavailable_bundles` 및 오류를 확인한다. 서버 장애를 모델 기준값으로 확정하지 않는다.
- 주 지표 `core_decision_input`과 그 출처 지표, 핵심 각 필드의 TP/FP/FN·예측/정답 수·
  정밀도/재현율의 분자/분모·Wilson 구간을 기록한다.
- 보조 `auxiliary_decision_input_5`와 전체 필드 지표도 함께 보존한다.
  기존 `decision_input` 키는 계속 5개 보조 범위다.
- 실행 환경의 GPU, 모델 revision, 양자화, 서버 버전, 생성 설정과 원본 JSON을 보존한다.
  현재 평가 JSON의 `bundles`는 검수 통과 후보 전체와 원래 모델 응답을 보존하는 형식이
  아니므로 후속 재채점이 필요하면 서버 원시 응답 또는 후보 전체·출처도 따로 보존한다.

이 결과는 담당자 확인 전 AI 추출 후보의 **개발자료 정확도**다.
담당자 확인 후 정확도·법적 판정 정확도·독립 시험 성능으로 표현하지 않는다.
기존 전체 87.6%나 gold-echo에서 Kanana 핵심 3개 점수를 계산하지 않는다.
