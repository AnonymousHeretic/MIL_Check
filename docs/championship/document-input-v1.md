# 텍스트 PDF・설명 문장 입력 v1

2026-10-06. 새 합성 PDF와 모의 모델 응답으로 검증한 **입력 경로 구현**이다.
실제 Kanana 통합 또는 독립 성능 검증은 아니다. 노션 29절의 병행 작업 기준은 이번
요청에 제공된 내용을 사용했다. 노션 최신 원문 직접 확인은 수행하지 못했다.

## 입력 계약

`milcheck.document_input.from_pdf` / `from_text`는 `InputDocument`를 반환한다.
`document`는 기존 문서 이해 경로의 `Document`이며 문서 ID·유형·개정 번호를 명시적으로
받는다. 개정 번호는 1 이상의 정수다. 파일명으로 유형을 추론하지 않는다.
기본 원문 출처는 `internal`이며 `public`·`synthetic`을 명시할 수 있다.

PDF의 각 페이지 추출 문자열은 수정하지 않고 페이지 사이에 `\n\f\n`을 삽입한다.
`pages`는 빈 페이지도 포함한 1부터 시작하는 페이지 번호와 반열린 `text_start/text_end`
범위를 제공한다. 삽입한 구분자는 특정 페이지 범위에 속하지 않는다.
위치는 UTF-8 바이트가 아닌 **Python Unicode 문자 인덱스**다.
이는 PDF 화면의 글자 위치·좌표가 아니다. `pdf_coordinates`는 null이며 화면 좌표를 추정하지 않는다.
PDF의 추출 순서는 화면 읽기 순서와 다를 수 있다.

기존 인용 검증과 유일 위치 교정 후 `InputDocument.trace(SourceRef)`를 호출하면 문서
ID·개정 번호·원문 일치를 다시 확인하고, 해당 페이지 및 페이지 내부 문자 범위를 반환한다.
여러 페이지에 걸친 인용은 각 페이지의 겹치는 범위를 반환한다. 설명 문장에는 PDF 페이지가 없다.
원문과 페이지 지도는 호출자의 메모리에 유지한다. 모델 계약에 페이지 좌표를 새로 요구하지 않는다.

## 최소 CLI

PDF 입력의 선택 의존성은 `pypdf`이며 `reportlab`은 합성 PDF 테스트 생성용이다.
기존 예선 기준선의 pyproject.toml은 변경하지 않았다.

```bash
python -m pip install -r requirements-input.txt
python scripts/understand_input.py --pdf /path/request.pdf --document-id REQ-01 --doc-type request --revision 2
python scripts/understand_input.py --text-file /path/description.txt --document-id DESC-01 --doc-type other --revision 1
python scripts/understand_input.py --text-file - --document-id DESC-01 --doc-type request --revision 1
```

마지막 명령은 설명 문장을 표준 입력으로 받는다. 파일은 UTF-8이어야 한다.
기본 동작은 입력 확인만 수행한다. 서버가 준비되면 동일 명령에 `--understand`를 추가하여
기존 `MILCHECK_LLM_*` 설정으로 **후보 추출만** 실행한다.
CLI는 문서 메타데이터·해시·문자 수·페이지 범위 및 후보 수·인용 위치만 출력한다.
원문·인용문·후보 값·모델 응답을 출력하거나 실행 기록 DB에 추가 저장하지 않는다.
후보 검토용 값·인용문은 Python API의 문서 이해 결과에서 메모리로 접근할 수 있다.

```python
from milcheck.document_input import from_pdf
from milcheck.understanding import DocumentUnderstanding

loaded = from_pdf("request.pdf", document_id="REQ-01", doc_type="request", revision=2)
result = DocumentUnderstanding().understand([loaded.document])
for candidate in result.candidates:
    for ref in candidate.source_refs:
        location = loaded.trace(ref)
```

이 경로는 담당자 확인·규칙 판정·실행 DB를 호출하지 않는다.
여러 입력은 API에서 `[loaded.document, ...]`로 기존 경로에 전달할 수 있다.
같은 ID의 개정본은 서로 다른 revision으로 구분한다.

## 오류와 경계

손상 PDF·암호화 PDF(빈 암호 포함)·텍스트 없는 PDF·읽기 실패·의존성 누락·문자 제한
초과는 `DocumentInputError`와 구분 가능한 `code`로 실패한다. 빈 정상 문서를 반환하지 않는다.
암호 입력·복호화·OCR은 제공하지 않는다. 직접 입력은 동일한 ID·유형·개정 번호를 사용해
`from_text` 또는 CLI의 `--text-file -`로 다시 제출한다.
CLI 입력 실패 종료 코드는 2, 모델 사용 불가 종료 코드는 3이다. 모델 사용 불가 시 자동
추출 불가와 수동 입력 안내를 제공한다. 라이브러리 예외 원문은 사용자 출력에 포함하지 않는다.

PDF 존재는 증빙 문서의 존재와 구별되는 내용 입증을 보장하지 않는다. 기존 후보·증빙
검수 경계를 유지하며 담당자 확인값만 규칙에 사용한다. 부가세 자동 1.1 환산은 하지 않는다.
OCR·HWP·전체 웹 UI·GPU 추론·모델 다운로드·충돌 판정 변경은 포함하지 않았다.
XT-11~60은 열람하거나 테스트에 사용하지 않았다.
