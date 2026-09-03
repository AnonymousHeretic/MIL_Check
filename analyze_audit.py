"""공개 계약 데이터에 MIL-Check 자체감사 규칙을 적용해 외부 검증 가능한 지표를 산출한다.

정제 정책
  1) 계약번호별 최종 차수만 사용 (변경계약 중복 제거)
  2) 조달청 위탁구매(대표업체=지방조달청)는 분할·편중 분석에서 제외
  3) 관급자재는 현장별 분리발주가 정상이므로 분할 분석에서 제외
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer

from train_models import load, article_key

OUT = Path(__file__).resolve().parent / "artifacts"
OUT.mkdir(exist_ok=True, parents=True)

THRESHOLDS = {
    "령26-1-5가(2)": (20_000_000, "추정가격 2천만원 이하"),
    "령26-1-5가(3)": (100_000_000, "2천만원 초과 1억원 이하(소기업·소상공인)"),
    "령26-1-5가(4)": (100_000_000, "2천만원 초과 1억원 이하(특수지식)"),
    "령26-1-5가(5)": (100_000_000, "2천만원 초과 1억원 이하(여성기업 등)"),
    "령26-1-5가(6)": (50_000_000, "5천만원 이하 임대차 등"),
}
MARGIN = 0.03


def dedupe(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["차수num"] = pd.to_numeric(d["계약차수"], errors="coerce").fillna(0)
    d = d.sort_values(["계약번호", "차수num"]).drop_duplicates("계약번호", keep="last")
    return d.reset_index(drop=True)


def exclude_agency(df: pd.DataFrame) -> pd.DataFrame:
    agency = df["대표업체명"].str.contains("조달청", na=False)
    gov_supply = df["계약명"].str.contains("관급", na=False)
    return df[~(agency | gov_supply)].copy()


def threshold_consistency(df: pd.DataFrame) -> dict:
    sub = df[df["수의계약사유"].str.strip() != ""].copy()
    sub["article"] = sub["수의계약사유"].map(article_key)
    sub = sub[sub["article"].isin(THRESHOLDS)]

    rows = []
    for art, grp in sub.groupby("article"):
        cap, desc = THRESHOLDS[art]
        est = grp["est_price"]
        over = est > cap * (1 + MARGIN)
        rows.append({
            "article": art, "description": desc, "cap_krw": cap,
            "n": int(len(grp)), "n_over_cap": int(over.sum()),
            "rate_over_cap": round(float(over.mean()), 4),
            "p99_est_price": int(est.quantile(0.99)),
            "max_est_price": int(est.max()),
        })
    total = sum(r["n"] for r in rows)
    bad = sum(r["n_over_cap"] for r in rows)
    return {
        "checked_contracts": total, "over_cap": bad,
        "over_cap_rate": round(bad / total, 4) if total else 0.0,
        "margin_applied": MARGIN,
        "method": "계약금액/1.1을 추정가격 근사로 사용, 상한 대비 3% 초과 이탈만 집계",
        "caveat": "공개 데이터에 추정가격 원본이 없어 근사치이며, 개별 건의 위법 여부를 "
                  "단정하지 않고 담당자 확인이 필요한 후보로만 제시한다.",
        "by_article": sorted(rows, key=lambda r: -r["n"]),
    }


def split_order_detection(df: pd.DataFrame, window_days: int = 60,
                          sim_threshold: float = 0.70) -> dict:
    sub = df[df["계약체결방법명"].eq("수의계약")
             & df["수의계약사유"].map(article_key).eq("령26-1-5가(2)")].copy()
    sub = exclude_agency(sub)
    sub = sub[(sub["est_price"] > 0) & (sub["est_price"] <= 20_000_000 * 1.03)]
    sub = sub.sort_values("date").reset_index(drop=True)

    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), min_df=1, sublinear_tf=True)
    flagged, groups = set(), []

    for dept, grp in sub.groupby("계약기관담당부서명"):
        if len(grp) < 2:
            continue
        try:
            M = vec.fit_transform(grp["name_norm"])
        except ValueError:
            continue
        S = (M @ M.T).toarray()
        np.fill_diagonal(S, 0.0)
        dates = grp["date"].to_numpy()
        amts = grp["est_price"].to_numpy()
        gidx = grp.index.to_numpy()
        seen: set[int] = set()
        for i in range(len(grp)):
            if i in seen:
                continue
            peers = [int(j) for j in range(len(grp))
                     if S[i, j] >= sim_threshold
                     and abs((dates[j] - dates[i]) / np.timedelta64(1, "D")) <= window_days]
            if not peers:
                continue
            members = [i] + peers
            total = float(amts[members].sum())
            if total <= 20_000_000 * 1.03:
                continue
            seen.update(members)
            flagged.update(gidx[members].tolist())
            groups.append({
                "n_contracts": len(members),
                "sum_est_price": int(total),
                "span_days": int(abs((dates[members].max() - dates[members].min())
                                     / np.timedelta64(1, "D"))),
                "same_vendor": bool(len(set(grp["대표업체명"].to_numpy()[members])) == 1),
                "names": grp["계약명"].to_numpy()[members][:3].tolist(),
            })

    groups.sort(key=lambda g: -g["sum_est_price"])
    return {
        "population": int(len(sub)),
        "flagged_contracts": len(flagged),
        "flagged_rate": round(len(flagged) / len(sub), 4) if len(sub) else 0.0,
        "groups": len(groups),
        "groups_same_vendor": sum(1 for g in groups if g["same_vendor"]),
        "params": {"window_days": window_days, "name_similarity": sim_threshold,
                   "aggregate_cap_krw": 20_000_000,
                   "excluded": "조달청 위탁구매, 관급자재, 상한 초과 개별건"},
        "caveat": "합산 시 상한을 넘는 유사 발주 후보이며, 사업의 동일성 여부는 담당자 "
                  "확인이 필요하다. 정당한 분리발주도 포함될 수 있다.",
        "top_examples": groups[:5],
    }


def price_anomaly(df: pd.DataFrame, min_cluster: int = 10, z: float = 3.5) -> dict:
    sub = exclude_agency(df[df["est_price"] > 0].copy())

    def key(n: str) -> str:
        toks = [t for t in n.split() if len(t) >= 2][:2]
        return " ".join(toks)

    sub["cluster"] = sub["name_norm"].map(key)
    counts = sub["cluster"].value_counts()
    valid = counts[counts >= min_cluster].index
    sub = sub[sub["cluster"].isin(valid)]

    flags = []
    for c, grp in sub.groupby("cluster"):
        v = np.log10(grp["est_price"].to_numpy())
        med = float(np.median(v))
        mad = float(np.median(np.abs(v - med)))
        scale = max(mad, 0.05)
        rz = 0.6745 * (v - med) / scale
        for i in np.where(np.abs(rz) > z)[0]:
            i = int(i)
            flags.append({
                "cluster": c, "name": grp["계약명"].iat[i],
                "est_price": int(grp["est_price"].iat[i]),
                "cluster_median": int(10 ** med),
                "ratio_to_median": round(float(10 ** (v[i] - med)), 2),
                "robust_z": round(float(rz[i]), 2),
                "cluster_n": int(len(grp)),
            })
    flags.sort(key=lambda f: -abs(f["robust_z"]))
    return {
        "population": int(len(sub)), "clusters": int(len(valid)),
        "flagged": len(flags),
        "flagged_rate": round(len(flags) / len(sub), 4) if len(sub) else 0.0,
        "params": {"min_cluster_size": min_cluster, "robust_z_threshold": z,
                   "mad_floor_log10": 0.05, "scale": "log10(추정가격), MAD 기반"},
        "caveat": "규격·수량이 공개 데이터에 없어 단가가 아닌 총액 비교다. 이상치는 "
                  "가격 부적정이 아니라 설명이 필요한 건을 뜻한다.",
        "top_examples": flags[:5],
    }


def vendor_concentration(df: pd.DataFrame, min_contracts: int = 10,
                         share_threshold: float = 0.6) -> dict:
    sub = exclude_agency(df[df["계약체결방법명"].eq("수의계약")].copy())
    rows = []
    for dept, grp in sub.groupby("계약기관담당부서명"):
        if len(grp) < min_contracts:
            continue
        amt = grp.groupby("대표업체명")["est_price"].sum()
        share = float(amt.max() / amt.sum()) if amt.sum() > 0 else 0.0
        top_vendor = amt.idxmax()
        n_top = int((grp["대표업체명"] == top_vendor).sum())
        if share >= share_threshold and n_top >= 3:
            rows.append({"n_sole_source": int(len(grp)),
                         "top_vendor_contracts": n_top,
                         "amount_share": round(share, 3)})
    rows.sort(key=lambda r: -r["amount_share"])
    sizes = sub.groupby("계약기관담당부서명").size()
    depts = int((sizes >= min_contracts).sum())
    return {
        "departments_evaluated": depts, "flagged_departments": len(rows),
        "flagged_rate": round(len(rows) / depts, 4) if depts else 0.0,
        "params": {"min_contracts": min_contracts, "amount_share_threshold": share_threshold},
        "caveat": "단일 공급원이 실제로 하나뿐인 품목군에서는 정상일 수 있어 편중 자체를 "
                  "위반으로 보지 않는다. 보고서에서 부서명은 익명화한다.",
        "top_examples": rows[:5],
    }


def similar_contract_bands(df: pd.DataFrame) -> dict:
    sub = df[df["est_price"] > 0].copy()

    def key(n: str) -> str:
        toks = [t for t in n.split() if len(t) >= 2][:2]
        return " ".join(toks)

    sub["cluster"] = sub["name_norm"].map(key)
    sizes = sub["cluster"].value_counts()
    usable = sizes[sizes >= 5]
    cov = float(sizes[sizes >= 5].sum() / len(sub))
    spread = []
    for c in usable.index[:1500]:
        v = sub.loc[sub["cluster"] == c, "est_price"]
        if v.median() > 0:
            spread.append(float((v.quantile(0.75) - v.quantile(0.25)) / v.median()))
    return {
        "total_contracts": int(len(sub)),
        "clusters_with_5plus": int(len(usable)),
        "coverage_rate": round(cov, 4),
        "median_iqr_over_median": round(float(np.median(spread)), 3),
        "note": "군집 5건 이상이면 사분위 가격대를 참고자료로 제시",
    }


def main():
    raw = load()
    df = dedupe(raw)
    result = {
        "dataset": {
            "source": "방위사업청 국내조달 계약정보 (공공데이터포털 data.go.kr)",
            "rows_raw": int(len(raw)), "rows_after_dedupe": int(len(df)),
            "dedupe_rule": "계약번호별 최종 차수만 사용",
            "period": f"{df['date'].min().date()} ~ {df['date'].max().date()}",
            "military_share": round(float(df["계약기관명"].str.contains("국방부").mean()), 4),
            "sole_source_share": round(float(df["계약체결방법명"].eq("수의계약").mean()), 4),
        },
        "threshold_consistency": threshold_consistency(df),
        "split_order": split_order_detection(df),
        "price_anomaly": price_anomaly(df),
        "vendor_concentration": vendor_concentration(df),
        "similar_contract_bands": similar_contract_bands(df),
    }
    (OUT / "audit_analysis.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    for k, v in result.items():
        print("==", k)
        print(json.dumps({a: b for a, b in v.items()
                          if a not in ("by_article", "top_examples")},
                         ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
