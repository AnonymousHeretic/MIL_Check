"""명시적 메타데이터로 PDF/설명 입력. 기본은 입력 확인, --understand는 후보 추출만 수행."""
from __future__ import annotations
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from milcheck.document_input import DocumentInputError, from_pdf, from_text
from milcheck.understanding import DOC_TYPES, DocumentUnderstanding, EndpointConfig


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sources = ap.add_mutually_exclusive_group(required=True)
    sources.add_argument("--pdf")
    sources.add_argument("--text-file", help="UTF-8 설명 파일; - 는 표준 입력")
    ap.add_argument("--document-id", required=True)
    ap.add_argument("--doc-type", choices=sorted(DOC_TYPES), required=True)
    ap.add_argument("--revision", type=int, required=True)
    ap.add_argument("--origin", choices=["internal", "public", "synthetic"], default="internal")
    ap.add_argument("--understand", action="store_true", help="설정된 모델 서버로 후보 추출; 자동 확정 없음")
    a = ap.parse_args()
    metadata = dict(document_id=a.document_id, doc_type=a.doc_type,
                    revision=a.revision, origin=a.origin)
    try:
        if a.pdf:
            loaded = from_pdf(a.pdf, **metadata)
        else:
            text = sys.stdin.read() if a.text_file == "-" else Path(a.text_file).read_text(encoding="utf-8")
            loaded = from_text(text, **metadata)
    except (OSError, UnicodeError):
        print(json.dumps({"error": "text_read_error", "message": "UTF-8 텍스트를 읽을 수 없습니다. 텍스트 직접 입력으로 전환하십시오."}, ensure_ascii=False), file=sys.stderr)
        return 2
    except DocumentInputError as exc:
        print(json.dumps({"error": exc.code, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    d = loaded.document
    output = {"document_id": d.document_id, "revision": d.revision, "doc_type": d.doc_type,
              "content_hash": d.content_hash, "text_chars": len(d.text),
              "pages": [asdict(p) for p in loaded.pages],
              "position_unit": "extracted_text_unicode_characters", "pdf_coordinates": None}
    status = 0
    if a.understand:
        result = DocumentUnderstanding(EndpointConfig.from_env()).understand([d])
        output.update(component_status=result.component_status, candidate_count=len(result.candidates),
                      rejected_count=len(result.rejected), question_count=len(result.questions),
                      source_traces=[loaded.trace(ref) for c in result.candidates for ref in c.source_refs],
                      caveat="담당자 확인 전 후보. 자동 확정·규칙 판정 미수행.")
        if result.component_status == "unavailable":
            output["message"] = "자동 추출 불가. 원문을 보며 수동 입력하십시오."
            status = 3
    # 원문·인용문·모델 응답·후보 값은 stdout 및 DB에 저장하지 않는다.
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
