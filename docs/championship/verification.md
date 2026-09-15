# 단계 0~1 검증 기록

2026-09-15 로컬 실행 및 이 PR 최종 커밋의 GitHub Actions를 구분해 기록한다.

## 기존 버전 보존

- 기준: 68410c3ef17418341b278f8be5c37773106359f7.
- 원격 보존: archive/pre-championship-2026-09-14. 모델·데이터·코드 전체를 같은 커밋으로 복구 가능.
- 후보 부모: af3b545c241d5bdfbf2641ad928e00f26bde9d47.
- README 외 기존 48개 파일의 Git blob/크기 동일. 제품 소스·가중치·규칙·평가 자료 변경 없음.
- 별도 임시 복사본에서 모델 변조 및 삭제를 보존 검사가 거부함을 확인.

## 로컬 실행 결과

환경: Python 3.12.14, Linux 6.18.44 x86_64. 군 PC 성능 측정이 아니다.

| 검사 | 기준 버전 | 후보 작업 트리 |
|---|---|---|
| python -m unittest discover -s tests -v | 50/50 통과 | 50/50 통과 |
| python -m milcheck.cli evaluate --output <별도 경로> | 정상 종료 | 정상 종료, 전체 JSON 동일 |
| 기존 파일 보존 | 기준 manifest | README 외 48개 동일 |

원본 기록: [요약](verification/2026-09-15/summary.json), [기준 시험 로그](verification/2026-09-15/baseline-tests.log), [후보 시험 로그](verification/2026-09-15/candidate-tests.log), [동일한 CLI 평가 결과](verification/2026-09-15/candidate-evaluation.json).

CLI 평가가 완벽하다는 의미는 아니다. 기존 추출 홀드아웃은 57/66 필드(86.36%), 16/20 사례 일치이며 동일 오류가 남아 있다.
기존 실제 공개 라벨 300건 조항 Top-1은 78%, Top-3는 95.67%다. 업무 적정성·신규 문서 AI 성능으로 해석하지 않는다.
기존 B01~B12는 후속 수정 대상이며 이 문서/CI 변경으로 해결되지 않았다.

## 최종 커밋과 병합 조건

championship-tests.yml은 PR 후보 SHA와 고정 기준 SHA 각각에 대해 시험·CLI 평가·로그 업로드를 수행한다.
후보 SHA의 보존 검사 및 두 버전의 시험 성공, diff 검토, main 기준 변경 여부를 확인한 후 병합한다.
최종 SHA와 Actions 링크는 PR 설명에서 기록한다. 아래 파일은 로컬 실행 기록이며 최종 CI 완료를 미리 주장하지 않는다.

전체 재학습·실제 군 설치·독립 내부 평가·업무시간 비교·새 문서 흐름의 실행은 수행하지 않았다.
