"""방위사업청 공개 계약데이터 품질 감사 (재현 가능, 집계만 출력).

사용:
    python scripts/audit_contract_data.py --contracts <계약정보.csv> \
        [--bids <경쟁 입찰공고.csv>] [--open-sole <공개수의 입찰결과.csv>] \
        [--plan-domestic <국내 조달계획.csv>] [--plan-foreign <국외 조달계획.csv>] \
        --output <결과.json>

원본 CSV는 담당자명·대표자명·사업자등록번호·주소·연락처를 포함하므로 저장소에 넣지 않는다.
출력에는 행 단위 원문·개인정보를 쓰지 않고 집계와 원본 파일 SHA-256만 남긴다.
표준 라이브러리만 사용한다.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path

PII_COLUMNS = {"계약기관담당자명", "수요기관담당자명", "대표업체대표자명", "대표업체사업자등록번호",
               "대표업체주소", "담당자명", "연락처", "공고기관담당자명"}
ARTICLE_RE = re.compile(r"제(\d+)조\s*(?:제(\d+)항)?\s*(?:제(\d+)호)?\s*(?:([가나다라마바사아자차카타파하])목)?")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def read_csv(path: Path) -> tuple[list[dict[str, str]], str, str]:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp949"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise SystemExit(f"인코딩 판별 실패: {path}")
    rows = list(csv.DictReader(text.splitlines()))
    return rows, enc, hashlib.sha256(raw).hexdigest()


def to_int(value: str) -> int | None:
    value = (value or "").strip().replace(",", "")
    if not value:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def valid_date(value: str) -> bool:
    if not DATE_RE.match(value or ""):
        return False
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def pct(n: int, d: int) -> float | None:
    return round(100 * n / d, 2) if d else None


def file_meta(path: Path, rows, enc, sha) -> dict:
    cols = list(rows[0].keys()) if rows else []
    return {"file": path.name, "sha256": sha, "encoding": enc, "rows": len(rows),
            "columns": len(cols), "pii_columns_present": sorted(PII_COLUMNS & set(cols))}


def audit_contracts(path: Path) -> dict:
    rows, enc, sha = read_csv(path)
    out = {"meta": file_meta(path, rows, enc, sha)}
    n = len(rows)

    # 식별자·차수
    key_counts = Counter((r["계약번호"], r["계약차수"]) for r in rows)
    dup_extra = sum(v - 1 for v in key_counts.values() if v > 1)
    revisions = Counter(r["계약차수"] for r in rows)
    nonstandard_rev = {k: v for k, v in revisions.items() if not re.fullmatch(r"\d{2}", k)}
    by_contract = Counter(r["계약번호"] for r in rows)
    out["identifiers"] = {
        "unique_contract_numbers": len(by_contract),
        "duplicate_number_revision_extra_rows": dup_extra,
        "contracts_with_multiple_revisions": sum(1 for v in by_contract.values() if v > 1),
        "revision_value_counts_top": revisions.most_common(8),
        "nonstandard_revision_format": nonstandard_rev,
        "nonstandard_revision_by_agency": Counter(
            r["계약기관명"] for r in rows if r["계약차수"] in nonstandard_rev).most_common(5),
    }

    # 날짜
    dates = [r["계약체결일자"] for r in rows]
    good = sorted(d for d in dates if valid_date(d))
    out["dates"] = {"invalid_contract_date": n - len(good),
                    "min": good[0] if good else None, "max": good[-1] if good else None,
                    "missing_period": sum(1 for r in rows if not r["계약기간"].strip())}

    # 금액
    amt = [to_int(r["계약금액"]) for r in rows]
    tot = [to_int(r["총계약금액"]) for r in rows]
    est = [to_int(r["예정가격"]) for r in rows]
    out["amounts"] = {
        "contract_amount_missing_or_non_numeric": sum(1 for a in amt if a is None),
        "contract_amount_zero": sum(1 for a in amt if a == 0),
        "contract_amount_negative": sum(1 for a in amt if a is not None and a < 0),
        "total_less_than_contract_amount": sum(
            1 for a, t in zip(amt, tot) if a is not None and t is not None and t < a),
        "planned_price_missing_or_zero": sum(1 for e in est if not e),
        "contract_amount_above_planned_price": sum(
            1 for a, e in zip(amt, est) if a and e and a > e),
        "note": "공개 데이터의 '계약금액'은 부가세 포함 여부가 표기되지 않는다. 추정가격(부가세 제외)과 직접 비교하지 않는다.",
    }

    # 분류
    methods = Counter(r["계약체결방법명"] for r in rows)
    out["categories"] = {
        "work_type": dict(Counter(r["업무구분명"] for r in rows)),
        "method": methods.most_common(),
        "contracting_agency": Counter(r["계약기관명"] for r in rows).most_common(5),
        "demand_agency_type": dict(Counter(r["수요기관구분명"] for r in rows)),
    }

    # 수의계약 사유
    sole = [r for r in rows if r["계약체결방법명"] == "수의계약"]
    reasons = [r["수의계약사유"].strip() for r in sole]
    with_reason = [x for x in reasons if x]
    parsed = [ARTICLE_RE.search(x) for x in with_reason]
    article_keys = Counter(
        "제{}조 제{}항 제{}호 {}목".format(m.group(1), m.group(2) or "?", m.group(3) or "?", m.group(4) or "-")
        for m in parsed if m)
    out["sole_source"] = {
        "rows": len(sole), "share_of_rows_pct": pct(len(sole), n),
        "reason_present": len(with_reason), "reason_missing": len(sole) - len(with_reason),
        "reason_article_parsed": sum(1 for m in parsed if m),
        "reason_article_unparsed": sum(1 for m in parsed if not m),
        "top_articles": article_keys.most_common(20),
        "reason_in_non_sole_rows": sum(1 for r in rows
                                       if r["계약체결방법명"] != "수의계약" and r["수의계약사유"].strip()),
        "caveat": "기재된 사유는 계약기관이 입력한 값이며 적법성 정답이 아니다.",
    }

    # 최초 완결 업무(호환성·단독공급) 관련 표본 규모
    def has(r, *words):
        return any(w in r["수의계약사유"] for w in words)
    out["first_use_case_pool"] = {
        "compatibility_2_sa": sum(1 for r in sole if "제2호 사목" in r["수의계약사유"] or has(r, "호환성")),
        "single_supplier_2_ja": sum(1 for r in sole if "제2호 자목" in r["수의계약사유"]),
        "direct_supply_2_ba": sum(1 for r in sole if "제2호 바목" in r["수의계약사유"]),
        "patent_2_a": sum(1 for r in sole if "제2호 아목" in r["수의계약사유"]),
        "goods_only_compatibility": sum(1 for r in sole if r["업무구분명"] == "물품"
                                        and ("제2호 사목" in r["수의계약사유"] or has(r, "호환성"))),
        "note": "공개 CSV에는 요청서·견적서·공급자 확인서 원문이 없다. 이 표는 문서 묶음 확보 대상의 모집단 크기일 뿐이다.",
    }

    # 과거 기록(37,595 / 30,059 / 71.5%) 재현 시도
    latest: dict[str, dict] = {}
    for r in rows:
        k = r["계약번호"]
        if k not in latest or int(r["계약차수"] or 0) >= int(latest[k]["계약차수"] or 0):
            latest[k] = r
    lat = list(latest.values())
    lat_sole = [r for r in lat if r["계약체결방법명"] == "수의계약"]
    out["reproduce_prior_figures"] = {
        "latest_revision_per_contract": len(lat),
        "latest_sole_source": len(lat_sole),
        "latest_sole_source_pct": pct(len(lat_sole), len(lat)),
        "latest_sole_with_reason": sum(1 for r in lat_sole if r["수의계약사유"].strip()),
        "prior_record": {"cleaned": 37595, "sole_pct": 71.5, "with_article": 30059},
        "note": "과거 정제 규칙의 원문을 확보하지 못해 동일 규칙 재현이 아니다. 차이는 정제 규칙 차이로만 설명하며 원인을 단정하지 않는다.",
    }
    return out


def audit_generic(path: Path, key_cols: list[str], date_col: str | None,
                  amount_col: str | None, cat_cols: list[str]) -> dict:
    rows, enc, sha = read_csv(path)
    out = {"meta": file_meta(path, rows, enc, sha)}
    keys = Counter(tuple(r.get(c, "") for c in key_cols) for r in rows)
    out["duplicate_key_extra_rows"] = sum(v - 1 for v in keys.values() if v > 1)
    out["key_columns"] = key_cols
    if date_col:
        vals = sorted(v[:10].replace("/", "-") for v in (r.get(date_col, "") for r in rows) if v)
        out["date_range"] = [vals[0], vals[-1]] if vals else None
    if amount_col:
        amts = [to_int(r.get(amount_col, "")) for r in rows]
        out["amount_missing_or_zero"] = sum(1 for a in amts if not a)
    out["categories"] = {c: Counter(r.get(c, "") for r in rows).most_common(8) for c in cat_cols}
    out["rows_with_extra_or_missing_fields"] = sum(
        1 for r in rows if None in r or any(v is None for v in r.values()))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--contracts", required=True)
    ap.add_argument("--bids")
    ap.add_argument("--open-sole")
    ap.add_argument("--plan-domestic")
    ap.add_argument("--plan-foreign")
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    result = {"audit_version": "2026-09-25", "contracts": audit_contracts(Path(args.contracts))}
    if args.bids:
        result["bids"] = audit_generic(Path(args.bids), ["입찰공고번호", "입찰공고차수"], "입찰공고일자",
                                       "배정예산금액(설계금액)", ["업무구분명", "계약체결방법명", "입찰공고상태명"])
    if args.open_sole:
        result["open_sole_results"] = audit_generic(Path(args.open_sole), ["공고번호"], "견적서제출마감일시",
                                                    "예산금액", ["계약방법", "입찰결과", "집행유형"])
    if args.plan_domestic:
        result["plan_domestic"] = audit_generic(Path(args.plan_domestic), ["판단번호"], "집행예정월",
                                                "예산금액", ["계약방법", "진행상태", "집행유형"])
    if args.plan_foreign:
        result["plan_foreign"] = audit_generic(Path(args.plan_foreign), ["판단번호"], "집행예정월",
                                               "예산금액", ["계약방법", "진행상태", "집행유형"])

    # 파일 간 연결 가능성: 입찰공고번호/공고번호 ↔ 계약번호 직접 일치 여부
    if args.bids or args.open_sole:
        contract_rows, _, _ = read_csv(Path(args.contracts))
        cnos = {r["계약번호"] for r in contract_rows}
        link = {}
        if args.bids:
            b, _, _ = read_csv(Path(args.bids))
            link["bids_notice_no_equals_contract_no"] = sum(1 for r in b if r["입찰공고번호"] in cnos)
        if args.open_sole:
            o, _, _ = read_csv(Path(args.open_sole))
            link["open_sole_notice_no_equals_contract_no"] = sum(1 for r in o if r["공고번호"] in cnos)
        link["note"] = "공개 파일 사이에 공통 식별자가 없으면 공고→계약 연결은 명칭·기관·일자 기반 추정이 되며 검증 라벨이 필요하다."
        result["cross_file_linkage"] = link

    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(Path(args.output).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
