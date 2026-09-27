import json, sys, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from milcheck.understanding import (Document, DocumentUnderstanding, EndpointConfig, SCHEMA_VERSION,
                                    FIELD_SPECS, response_schema)

try:
    import jsonschema
except ImportError:  # pragma: no cover
    jsonschema = None

REF = {"document_id": "R1", "revision": 1, "text_start": 0, "text_end": 3, "quote": "구매요"}


def payload(facts, claims=(), qs=()):
    return {"schema_version": SCHEMA_VERSION, "facts": list(facts),
            "evidence_claims": list(claims), "questions": list(qs)}


class StructuredOutputTest(unittest.TestCase):
    def test_body_uses_schema_only_when_enabled(self):
        seen = {}
        def transport(url, body, headers, timeout):
            seen.update(body)
            return json.dumps({"choices": [{"message": {"content": json.dumps(payload([]))}}]}).encode()
        doc = [Document("R1", 1, "구매요구서", doc_type="request")]
        cfg = EndpointConfig(model="m", structured=True)
        r = DocumentUnderstanding(cfg, transport=transport).understand(doc)
        self.assertEqual(seen["response_format"]["type"], "json_schema")
        self.assertTrue(r.run_record["structured_output"])
        DocumentUnderstanding(EndpointConfig(model="m"), transport=transport).understand(doc)
        self.assertEqual(seen["response_format"], {"type": "json_object"})

    def test_extra_body_cannot_override_schema(self):
        seen = {}
        def transport(url, body, headers, timeout):
            seen.update(body)
            return json.dumps({"choices": [{"message": {"content": json.dumps(payload([]))}}]}).encode()
        cfg = EndpointConfig(model="m", structured=True, extra_body={"response_format": {"type": "text"}})
        DocumentUnderstanding(cfg, transport=transport).understand([Document("R1", 1, "x", doc_type="request")])
        self.assertEqual(seen["response_format"]["type"], "json_schema")

    def test_schema_covers_all_fields(self):
        variants = response_schema()["properties"]["facts"]["items"]["anyOf"]
        self.assertEqual({v["properties"]["field"]["enum"][0] for v in variants}, set(FIELD_SPECS))

    @unittest.skipIf(jsonschema is None, "jsonschema 미설치")
    def test_schema_accepts_valid_and_rejects_observed_mistakes(self):
        s = response_schema()
        ok = payload([{"field": "estimated_price_krw", "value": 1000, "source_refs": [REF]},
                      {"field": "tax_status", "value": "vat_excluded", "source_refs": [REF]}],
                     [{"requirement_id": "EV-QUOTE", "content_support": "unknown", "source_refs": []}],
                     [{"affected_fields": ["supplier_name"], "reason": "missing", "text": "업체?", "source_refs": []}])
        jsonschema.validate(ok, s)
        bad = [
            {"field": "item_name", "value": "x", "source_refs": [REF], "note": "설명"},       # 덧붙인 키
            {"field": "quantity", "value": 3, "unit": "대", "source_refs": [REF]},
            {"field": "item_name", "value": "x", "source_refs": [dict(REF, offset=1)]},
            {"field": "item_name", "value": "x", "source_refs": [REF], "evidence_claims": []},  # 잘못된 중첩
            {"field": "tax_status", "value": "maybe", "source_refs": [REF]},                  # enum 밖
            {"field": "estimated_price_krw", "value": "1,000원", "source_refs": [REF]},       # 타입
            {"field": "contract_category", "value": "goods", "source_refs": [REF]},           # 없는 필드
        ]
        for f in bad:
            with self.assertRaises(jsonschema.ValidationError, msg=str(f)):
                jsonschema.validate(payload([f]), s)


if __name__ == "__main__":
    unittest.main()
