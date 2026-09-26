"""외부 작성 평가 세트의 형식만 점검하고 동결 해시를 기록한다(내용은 출력하지 않음).

사용: python scripts/check_external_set.py eval/external/XT_11-20.jsonl ... --manifest eval/external/manifest.json
출력은 묶음 수·오류 개수·SHA-256뿐이다. 문서 본문·정답 값을 화면에 내지 않아
측정 전 개발자가 내용을 보는 일을 막는다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from milcheck.understanding import DOC_TYPES, EVIDENCE_REQUIREMENTS, FIELD_SPECS, REQUIRED_FIELDS  # noqa: E402


def check(path: Path) -> dict:
    errors: Counter = Counter()
    ids = []
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    for line in lines:
        try:
            b = json.loads(line)
        except ValueError:
            errors["json_parse"] += 1
            continue
        ids.append(b.get("bundle_id"))
        docs = {d.get("document_id"): d for d in b.get("documents", [])}
        if not docs:
            errors["no_documents"] += 1
        for d in docs.values():
            if d.get("doc_type") not in DOC_TYPES:
                errors["bad_doc_type"] += 1
        g = b.get("gold", {})
        vals: dict = {}
        for f in g.get("facts", []):
            if f.get("field") not in FIELD_SPECS:
                errors["unknown_field"] += 1
                continue
            doc = docs.get(f.get("document_id"))
            if doc is None:
                errors["unknown_document"] += 1
            elif f.get("quote") not in doc.get("text", ""):
                errors["quote_not_in_text"] += 1
            vals.setdefault(f["field"], set()).add(json.dumps(f.get("value"), ensure_ascii=False))
        multi = {k for k, v in vals.items() if len(v) > 1}
        if multi != set(g.get("conflict_fields", [])):
            errors["conflict_fields_mismatch"] += 1
        if set(g.get("missing_fields", [])) - set(REQUIRED_FIELDS):
            errors["missing_not_required"] += 1
        if set(g.get("evidence_absent", [])) - set(EVIDENCE_REQUIREMENTS):
            errors["bad_evidence_id"] += 1
    return {"file": path.name, "bundles": len(lines), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "duplicate_ids": len(ids) - len(set(ids)), "errors": dict(errors)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--manifest")
    a = ap.parse_args()
    results = [check(Path(f)) for f in a.files]
    for r in results:
        print(json.dumps({k: r[k] for k in ("file", "bundles", "duplicate_ids", "errors")}, ensure_ascii=False))
    if a.manifest:
        Path(a.manifest).write_text(json.dumps({"frozen_files": results}, ensure_ascii=False, indent=2), encoding="utf-8")
        print("manifest:", a.manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
