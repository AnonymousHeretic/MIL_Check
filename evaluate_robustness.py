"""MIL-Check ML 평가의 분할 민감도·유사계약 누수·시간 일반화를 검증한다.

레포에 포함된 비식별 contracts_index.jsonl.gz만 사용한다.
평가:
  1) Stratified 5-fold CV
  2) normalized contract name GroupKFold
  3) temporal holdout (2025-12 test; 이전 기간 train)

주의: 계약번호는 비식별 인덱스에 포함되지 않아 contract-id GroupKFold는
원본 CSV 환경에서 별도로 수행해야 한다.
"""
from __future__ import annotations

import gzip
import json
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score
from sklearn.model_selection import GroupKFold, StratifiedKFold
from sklearn.pipeline import FeatureUnion

DATA = Path("data/contracts_index.jsonl.gz")
OUT = Path("docs/robustness_results.json")
SEED = 20260725


def load_index() -> pd.DataFrame:
    rows = []
    with gzip.open(DATA, "rt", encoding="utf-8") as fh:
        for line in fh:
            rows.append(json.loads(line))
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["d"], errors="coerce")
    df["est_price"] = pd.to_numeric(df["p"], errors="coerce")
    return df


def make_text(df: pd.DataFrame) -> pd.Series:
    bucket = pd.cut(
        df["est_price"],
        bins=[-1, 2e6, 1e7, 2e7, 5e7, 1e8, 5e8, 1e9, 1e13],
        labels=["b0", "b1", "b2", "b3", "b4", "b5", "b6", "b7"],
    ).astype(str)
    return df["n"].fillna("") + " ⟪" + df["c"].fillna("") + "⟫ ⟪" + bucket + "⟫"


def build_vectorizer() -> FeatureUnion:
    return FeatureUnion([
        ("char", TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), min_df=3,
                                 sublinear_tf=True, max_features=120_000)),
        ("word", TfidfVectorizer(analyzer="word", ngram_range=(1, 2), min_df=3,
                                 sublinear_tf=True, max_features=60_000)),
    ])


def prepare_task(df: pd.DataFrame, task: str, min_count: int = 25) -> pd.DataFrame:
    label = "a" if task == "article" else "m"
    sub = df[df[label].fillna("").str.strip() != ""].copy()
    counts = Counter(sub[label])
    keep = {k for k, v in counts.items() if v >= min_count}
    return sub[sub[label].isin(keep)].reset_index(drop=True)


def score_split(df: pd.DataFrame, label: str, train_idx, test_idx) -> dict:
    train = df.iloc[train_idx]
    test = df.iloc[test_idx]
    vec = build_vectorizer()
    Xtr = vec.fit_transform(make_text(train))
    Xte = vec.transform(make_text(test))
    clf = LogisticRegression(C=4.0, max_iter=2000, class_weight="balanced")
    clf.fit(Xtr, train[label])
    pred = clf.predict(Xte)
    proba = clf.predict_proba(Xte)
    classes = list(clf.classes_)
    true_idx = np.array([classes.index(v) if v in classes else -1 for v in test[label]])
    top3 = np.argsort(-proba, axis=1)[:, :3]
    known = true_idx >= 0
    top3_acc = float(np.mean([y in row for y, row in zip(true_idx[known], top3[known])])) if known.any() else 0.0
    return {
        "n_train": int(len(train)), "n_test": int(len(test)),
        "accuracy": float(accuracy_score(test[label], pred)),
        "macro_f1": float(f1_score(test[label], pred, average="macro", zero_division=0)),
        "weighted_f1": float(f1_score(test[label], pred, average="weighted", zero_division=0)),
        "top3_accuracy": top3_acc,
    }


def summarize(folds: list[dict]) -> dict:
    out = {"folds": folds}
    for metric in ["accuracy", "macro_f1", "weighted_f1", "top3_accuracy"]:
        vals = np.array([f[metric] for f in folds])
        out[metric] = {"mean": float(vals.mean()), "std": float(vals.std(ddof=1))}
    return out


def evaluate_task(df: pd.DataFrame, task: str) -> dict:
    label = "a" if task == "article" else "m"
    sub = prepare_task(df, task)
    y = sub[label].to_numpy()

    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=SEED)
    random_folds = [score_split(sub, label, tr, te) for tr, te in skf.split(sub, y)]

    # 가장 강한 유사계약 누수 테스트: 동일 normalized name은 절대 양쪽에 배치하지 않는다.
    gkf = GroupKFold(n_splits=5)
    group_folds = [score_split(sub, label, tr, te)
                   for tr, te in gkf.split(sub, y, groups=sub["n"].fillna(""))]

    cutoff = pd.Timestamp("2025-12-01")
    tr = np.where(sub["date"] < cutoff)[0]
    te = np.where(sub["date"] >= cutoff)[0]
    temporal = score_split(sub, label, tr, te)
    temporal["train_period"] = f"{sub.iloc[tr]['d'].min()}..{sub.iloc[tr]['d'].max()}"
    temporal["test_period"] = f"{sub.iloc[te]['d'].min()}..{sub.iloc[te]['d'].max()}"

    return {
        "rows": int(len(sub)), "classes": int(sub[label].nunique()),
        "stratified_5fold": summarize(random_folds),
        "name_group_5fold": summarize(group_folds),
        "temporal_holdout": temporal,
    }


def main() -> None:
    df = load_index()
    results = {
        "dataset": {"rows": int(len(df)), "period": f"{df['d'].min()}..{df['d'].max()}"},
        "design": {
            "random": "StratifiedKFold(5), shuffle=True, seed=20260725",
            "group": "GroupKFold(5), group=normalized contract name",
            "temporal": "train before 2025-12; test 2025-12",
            "limitation": "contract-id grouping requires original CSV because contract id was excluded from the offline index",
        },
        "article": evaluate_task(df, "article"),
        "method": evaluate_task(df, "method"),
    }
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
