"""새 합성 PDF·설명으로 입력 경로 검증. 독립 성능 평가나 실제 Kanana 검증이 아니다."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from milcheck.document_input import DocumentInputError, from_pdf, from_text
from milcheck.understanding import DocumentUnderstanding, EndpointConfig, SCHEMA_VERSION, SourceRef

ROOT = Path(__file__).resolve().parents[1]
PDF_READY = all(importlib.util.find_spec(m) for m in ('pypdf', 'reportlab'))


def mock_transport(document, field, value, quote, *, revision=None, wrong_offset=False):
    start = document.text.index(quote)
    def transport(url, body, headers, timeout):
        assert quote in body['messages'][1]['content']
        payload = {'schema_version': SCHEMA_VERSION, 'facts': [{
            'field': field, 'value': value, 'source_refs': [{
                'document_id': document.document_id,
                'revision': document.revision if revision is None else revision,
                'text_start': 0 if wrong_offset else start,
                'text_end': len(quote) if wrong_offset else start + len(quote), 'quote': quote}]}],
            'evidence_claims': [], 'questions': []}
        return json.dumps({'choices': [{'message': {'content': json.dumps(payload, ensure_ascii=False)}}]}).encode()
    return transport


class TextInputTests(unittest.TestCase):
    def test_korean_newlines_and_mock_validation(self):
        text = '구매 설명\n추정가격 100원\n부가세 별도\n'
        loaded = from_text(text, document_id='DESC', doc_type='request', revision=2)
        self.assertEqual(loaded.document.text, text)
        self.assertEqual(loaded.document.origin, 'internal')
        du = DocumentUnderstanding(EndpointConfig(), transport=mock_transport(
            loaded.document, 'estimated_price_krw', 100, '추정가격 100원'))
        result = du.understand([loaded.document])
        self.assertEqual(len(result.candidates), 1)
        trace = loaded.trace(result.candidates[0].source_refs[0])
        self.assertEqual(trace['revision'], 2)
        self.assertEqual(trace['pages'], [])
        self.assertIsNone(trace['pdf_coordinates'])
        self.assertNotIn(text, json.dumps(result.run_record, ensure_ascii=False))
        self.assertNotIn('추정가격 100원', json.dumps(result.run_record, ensure_ascii=False))
        self.assertTrue(all(e.content_support == 'unknown' for e in result.evidence))

    def test_types_and_revision_rejection(self):
        for kind, revision, expected in [('request', 2, 1), ('quote', 2, 0), ('request', 1, 0)]:
            loaded = from_text('추정가격 100원', document_id='SAME', doc_type=kind, revision=revision)
            result = DocumentUnderstanding(EndpointConfig(), transport=mock_transport(
                loaded.document, 'estimated_price_krw', 100, '추정가격 100원', revision=2)).understand([loaded.document])
            self.assertEqual(len(result.candidates), expected)

    def test_invalid_metadata_empty_and_limit(self):
        good = dict(document_id='D', doc_type='request', revision=1)
        for patch in ({'document_id': ''}, {'doc_type': 'filename.pdf'}, {'revision': 0}, {'revision': True}):
            with self.assertRaises(DocumentInputError):
                from_text('설명', **(good | patch))
        for text in (' \n', '가' * 60001):
            with self.assertRaises(DocumentInputError):
                from_text(text, **good)

    def test_trace_rejects_wrong_document_revision_and_offsets(self):
        loaded = from_text('설명 문장', document_id='D', doc_type='other', revision=1)
        for ref in (SourceRef('X', 1, 0, 2, '설명'), SourceRef('D', 2, 0, 2, '설명'),
                    SourceRef('D', 1, -1, 2, '설명'), SourceRef('D', 1, 0, 2, '다름')):
            with self.assertRaises(ValueError): loaded.trace(ref)

    def test_cli_stdin_metadata_only_and_empty_error(self):
        command = [sys.executable, str(ROOT / 'scripts/understand_input.py'), '--text-file', '-',
                   '--document-id', 'D', '--doc-type', 'request', '--revision', '3']
        r = subprocess.run(command, input='비공개 합성 설명\n부가세 별도', text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)['revision'], 3)
        self.assertNotIn('비공개', r.stdout + r.stderr)
        r = subprocess.run(command, input='', text=True, capture_output=True)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(json.loads(r.stderr)['error'], 'no_text')


@unittest.skipUnless(PDF_READY, 'pypdf/reportlab 미설치: requirements-input.txt 필요')
class PdfInputTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / '견적서라는파일명.pdf'

    def make_pdf(self, pages):
        from reportlab.pdfgen import canvas
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        pdfmetrics.registerFont(UnicodeCIDFont('HYSMyeongJo-Medium'))
        c = canvas.Canvas(str(self.path))
        for lines in pages:
            c.setFont('HYSMyeongJo-Medium', 12)
            for n, line in enumerate(lines): c.drawString(40, 760 - n * 20, line)
            c.showPage()
        c.save()

    def load(self, **patch):
        return from_pdf(self.path, **(dict(document_id='PDF', doc_type='request', revision=4) | patch))

    def test_multipage_korean_newlines_mock_and_relocated_quote(self):
        self.make_pdf([['구매 설명', '첫 페이지'], ['추정가격 100원', '부가세 별도']])
        loaded = self.load()
        self.assertEqual(loaded.document.doc_type, 'request')  # 파일명에서 유형 추론 안 함
        self.assertEqual(loaded.document.media_type, 'application/pdf')
        self.assertIn('구매 설명\n첫 페이지', loaded.document.text)
        self.assertEqual(len(loaded.pages), 2)
        for page, expected in zip(loaded.pages, ['구매 설명', '추정가격 100원']):
            self.assertIn(expected, loaded.document.text[page.text_start:page.text_end])
        result = DocumentUnderstanding(EndpointConfig(), transport=mock_transport(
            loaded.document, 'estimated_price_krw', 100, '추정가격 100원', wrong_offset=True)).understand([loaded.document])
        self.assertEqual(len(result.candidates), 1)
        ref = result.candidates[0].source_refs[0]
        self.assertTrue(ref.relocated)
        trace = loaded.trace(ref)
        self.assertEqual(trace['pages'][0]['page'], 2)
        self.assertEqual(trace['pages'][0]['page_text_start'], 0)
        self.assertIsNone(trace['pdf_coordinates'])
        self.assertEqual(ref.revision, 4)
        self.assertNotIn(loaded.document.text, json.dumps(result.run_record, ensure_ascii=False))
        # 페이지 경계를 넘는 인용도 두 페이지에 대응한다.
        start, end = loaded.pages[0].text_end - 2, loaded.pages[1].text_start + 3
        ref = SourceRef('PDF', 4, start, end, loaded.document.text[start:end])
        self.assertEqual([p['page'] for p in loaded.trace(ref)['pages']], [1, 2])

    def test_blank_page_ranges_preserved(self):
        self.make_pdf([[], ['설명 문장']])
        loaded = self.load()
        self.assertEqual(loaded.pages[0].text_start, loaded.pages[0].text_end)
        self.assertEqual(loaded.pages[1].page, 2)

    def test_damaged_encrypted_and_no_text_are_explicit_errors(self):
        from pypdf import PdfWriter
        self.path.write_bytes(b'not a pdf')
        with self.assertRaises(DocumentInputError) as error: self.load()
        self.assertEqual(error.exception.code, 'damaged_pdf')
        writer = PdfWriter(); writer.add_blank_page(width=100, height=100)
        writer.write(self.path)
        with self.assertRaises(DocumentInputError) as error: self.load()
        self.assertEqual(error.exception.code, 'no_text')
        writer.encrypt('secret'); writer.write(self.path)
        with self.assertRaises(DocumentInputError) as error: self.load()
        self.assertEqual(error.exception.code, 'encrypted_pdf')
        self.assertIn('텍스트 직접 입력', str(error.exception))
        # 암호화 실패 후 동일 메타데이터의 직접 입력 경로가 사용 가능하다.
        loaded = from_text('직접 설명', document_id='PDF', doc_type='request', revision=4)
        self.assertEqual(loaded.document.revision, 4)

    def test_presence_does_not_prove_content(self):
        self.make_pdf([['합성 첨부 설명']])
        loaded = self.load(doc_type='quote')
        def transport(*args):
            return json.dumps({'choices': [{'message': {'content': json.dumps({
                'schema_version': SCHEMA_VERSION, 'facts': [], 'evidence_claims': [], 'questions': []})}}]}).encode()
        r = DocumentUnderstanding(EndpointConfig(), transport=transport).understand([loaded.document])
        e = next(e for e in r.evidence if e.requirement_id == 'EV-QUOTE')
        self.assertEqual(e.presence, 'present')
        self.assertEqual(e.content_support, 'unknown')
        self.assertIsNone(e.confirmed_by)


if __name__ == '__main__': unittest.main()
