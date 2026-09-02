"""공개 계약 데이터를 폐쇄망 반입용 압축 인덱스로 변환한다.

개인정보(담당자명·사업자등록번호·대표자명·주소)와 업체명은 반입하지 않는다.
부서명은 일방향 해시 별칭으로 치환한다.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from analyze_audit import dedupe
from milcheck.contracts import display_contract_name
from train_models import load, article_key

OUT = Path("/home/claude/work/artifacts")
SALT = "milcheck-2026"


def dept_alias(name: str) -> str:
    return "D" + hashlib.sha256((SALT + str(name)).encode()).hexdigest()[:6].upper()


def cluster_key(name_norm: str) -> str:
    toks = [t for t in name_norm.split() if len(t) >= 2][:2]
    return " ".join(toks)


def main(raw_path: str | Path | None = None, out_dir: str | Path | None = None):
    out = Path(out_dir or OUT)
    out.mkdir(parents=True, exist_ok=True)
    df = dedupe(load(raw_path))
    df["article"] = df["수의계약사유"].map(article_key)
    df["cluster"] = df["name_norm"].map(cluster_key)

    records = []
    for r in df.itertuples(index=False):
        records.append(
            {
                "n": r.name_norm,                       # 정규화 계약명
                "x": display_contract_name(r.계약명),     # 비식별 표시용 계약명
                # 원계약금액 하나만 저장하고 추정가격은 적재 시 계산한다.
                # 일자 정밀도를 추가해도 인덱스 크기를 유지하기 위한 중복 제거다.
                "g": int(r.amount),                      # 계약금액(부가세 포함)
                "m": r.계약체결방법명,                     # 계약체결방법
                "a": r.article or "",                    # 수의계약 근거조항 키
                "c": r.업무구분명,                        # 물품/용역
                "t": r.date.date().isoformat(),            # 체결일자(60일 창 계산용)
                "u": dept_alias(r.계약기관담당부서명),      # 부서 별칭
            }
        )

    with gzip.open(out / "contracts_index.jsonl.gz", "wt", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # 품목군별 가격대(사분위) 사전 계산 — 참고용 산출물이며 런타임은 사용하지 않는다.
    # (운영에서는 검색 결과에서 즉석 계산하는 적응형 가격대를 쓴다)
    bands = {}
    for c, grp in df.groupby("cluster"):
        if len(grp) < 5 or not c:
            continue
        v = grp["est_price"]
        bands[c] = {
            "n": int(len(grp)),
            "q1": int(v.quantile(0.25)),
            "med": int(v.median()),
            "q3": int(v.quantile(0.75)),
            "p95": int(v.quantile(0.95)),
        }
    with gzip.open(out / "price_bands.json.gz", "wt", encoding="utf-8") as fh:
        json.dump(bands, fh, ensure_ascii=False)

    meta = {
        "source": "방위사업청 국내조달 계약정보 (data.go.kr)",
        "records": len(records),
        "period": f"{df['date'].min().date()} ~ {df['date'].max().date()}",
        "clusters_with_bands": len(bands),
        "excluded_fields": ["대표업체명", "대표업체대표자명", "사업자등록번호",
                            "대표업체주소", "담당자명", "원문 계약명"],
        "pseudonymized": ["계약기관담당부서명 → SHA-256 별칭",
                           "계약명 내 부대 식별자 → [부대]"],
    }
    (out / "index_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    for f in ["contracts_index.jsonl.gz", "price_bands.json.gz"]:
        print(f, f"{(out / f).stat().st_size / 1e6:.2f} MB")
    print(json.dumps(meta, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", default=None, help="공개 계약정보 CSV 경로")
    parser.add_argument("--out", default=None, help="인덱스 출력 디렉터리")
    args = parser.parse_args()
    main(args.raw, args.out)
