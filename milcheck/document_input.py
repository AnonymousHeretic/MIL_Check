"""텍스트 PDF·직접 입력 어댑터. 원문은 메모리에만 유지하며 OCR은 수행하지 않는다."""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from .understanding import DOC_TYPES, MAX_DOCUMENT_CHARS, Document, SourceRef


class DocumentInputError(ValueError):
    """빈 정상 문서 대신 직접 텍스트 입력을 안내하는 오류."""

    def __init__(self, code: str, reason: str):
        self.code = code
        super().__init__(f"{reason}. 텍스트 직접 입력으로 전환하십시오.")


@dataclass(frozen=True)
class PageRange:
    page: int  # 1부터 시작하는 PDF 페이지 번호
    text_start: int
    text_end: int  # 반열린 Python 문자 범위


@dataclass(frozen=True)
class InputDocument:
    document: Document
    pages: tuple[PageRange, ...] = ()

    def trace(self, ref: SourceRef) -> dict:
        """검증된 인용을 추출 문자열 및 페이지 내부 문자 범위로 연결한다."""
        d = self.document
        if (ref.document_id, ref.revision) != (d.document_id, d.revision):
            raise ValueError("인용 문서 ID·개정 번호 불일치")
        if (type(ref.text_start) is not int or type(ref.text_end) is not int
                or not 0 <= ref.text_start < ref.text_end <= len(d.text)
                or d.text[ref.text_start:ref.text_end] != ref.quote):
            raise ValueError("인용 문자 범위·원문 불일치")
        matches = []
        for page in self.pages:
            start, end = max(ref.text_start, page.text_start), min(ref.text_end, page.text_end)
            if start < end:
                matches.append({"page": page.page, "text_start": start, "text_end": end,
                                "page_text_start": start - page.text_start,
                                "page_text_end": end - page.text_start})
        return {"document_id": d.document_id, "revision": d.revision,
                "doc_type": d.doc_type, "text_start": ref.text_start, "text_end": ref.text_end,
                "position_unit": "extracted_text_unicode_characters",
                "pdf_coordinates": None, "pages": matches}


def _metadata(document_id, doc_type, revision, origin):
    if not isinstance(document_id, str) or not document_id.strip():
        raise DocumentInputError("invalid_metadata", "문서 ID가 필요합니다")
    if doc_type not in DOC_TYPES:
        raise DocumentInputError("invalid_metadata", "지원하지 않는 문서 유형")
    if type(revision) is not int or revision < 1:
        raise DocumentInputError("invalid_metadata", "개정 번호는 1 이상의 정수여야 합니다")
    if origin not in {"public", "internal", "synthetic"}:
        raise DocumentInputError("invalid_metadata", "지원하지 않는 원문 출처")


def _document(text, document_id, doc_type, revision, origin, media_type):
    if not isinstance(text, str) or not text.strip():
        raise DocumentInputError("no_text", "읽을 수 있는 텍스트가 없습니다(OCR 미지원)")
    if len(text) > MAX_DOCUMENT_CHARS:
        raise DocumentInputError("too_long", "문서 이해 입력 문자 제한 초과")
    return Document(document_id, revision, text, doc_type=doc_type, origin=origin,
                    media_type=media_type)


def from_text(text: str, *, document_id: str, doc_type: str, revision: int,
              origin: str = "internal") -> InputDocument:
    _metadata(document_id, doc_type, revision, origin)
    return InputDocument(_document(text, document_id, doc_type, revision, origin, "text/plain"))


def from_pdf(path: str | Path | BinaryIO, *, document_id: str, doc_type: str, revision: int,
             origin: str = "internal") -> InputDocument:
    """path는 파일 경로 또는 읽기 가능한 바이너리 스트림(업로드를 디스크에 쓰지 않을 때)."""
    _metadata(document_id, doc_type, revision, origin)
    try:
        from pypdf import PdfReader
    except ImportError:
        raise DocumentInputError("pdf_dependency_missing", "PDF 입력용 pypdf가 설치되지 않았습니다") from None
    chunks, ranges, offset = [], [], 0
    try:
        # strict=True로 손상 파일을 조용히 복구하지 않는다. 파서 로그에 원문을 쓰지 않는다.
        opened = nullcontext(path) if hasattr(path, "read") else Path(path).open("rb")
        with opened as stream:
            reader = PdfReader(stream, strict=True)
            if reader.is_encrypted:
                raise DocumentInputError("encrypted_pdf", "암호화된 PDF는 지원하지 않습니다")
            for number, page in enumerate(reader.pages, 1):
                text = page.extract_text() or ""
                if number > 1:
                    chunks.append("\n\f\n")
                    offset += 3
                start = offset
                chunks.append(text)
                offset += len(text)
                ranges.append(PageRange(number, start, offset))
                if offset > MAX_DOCUMENT_CHARS:
                    raise DocumentInputError("too_long", "문서 이해 입력 문자 제한 초과")
    except DocumentInputError:
        raise
    except OSError:
        raise DocumentInputError("pdf_read_error", "PDF 파일을 읽을 수 없습니다") from None
    except Exception:
        # 라이브러리 예외 메시지에는 파일·원문 일부가 포함될 수 있어 노출하지 않는다.
        raise DocumentInputError("damaged_pdf", "손상되었거나 지원하지 않는 PDF") from None
    return InputDocument(_document("".join(chunks), document_id, doc_type, revision,
                                   origin, "application/pdf"), tuple(ranges))
