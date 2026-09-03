"""D2B 공개계약 데이터로 MIL-Check ML 계층을 학습하고 폐쇄망 추론용 JSON으로 내보낸다.

학습(공개망) -> JSON 가중치 -> 추론(폐쇄망, 표준 라이브러리)
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, f1_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import FeatureUnion

RAW = Path("/mnt/project/방위사업청_국내조달_계약정보_20251231.csv")
OUT = Path(__file__).resolve().parent / "artifacts"
OUT.mkdir(exist_ok=True, parents=True)
SEED = 20260725


# --------------------------------------------------------------------------
# 1. 로드 및 정규화
# --------------------------------------------------------------------------
def normalize_name(s: str) -> str:
    s = unicodedata.normalize("NFKC", str(s or ""))
    s = s.lower()
    s = re.sub(r"['\"`’‘“”]", "", s)
    s = re.sub(r"\b(19|20)?\d{2}\s*[-년]", " ", s)          # 연도 표기 제거
    s = re.sub(r"제?\s*\d+\s*(차|회|호|분기)", " ", s)        # 차수 제거
    s = re.sub(r"\d+", "0", s)                               # 숫자 일반화
    s = re.sub(r"[^\w가-힣]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def load(raw_path: str | Path | None = None) -> pd.DataFrame:
    df = pd.read_csv(Path(raw_path or RAW), encoding="cp949", dtype=str).fillna("")
    df["amount"] = pd.to_numeric(df["계약금액"], errors="coerce")
    df["planned"] = pd.to_numeric(df["예정가격"], errors="coerce")
    df["date"] = pd.to_datetime(df["계약체결일자"], errors="coerce")
    df["name_norm"] = df["계약명"].map(normalize_name)
    df["est_price"] = df["amount"] / 1.1          # 추정가격 근사(부가세 제외)
    df = df[(df["amount"] > 0) & (df["name_norm"].str.len() >= 2)]
    return df.reset_index(drop=True)


# --------------------------------------------------------------------------
# 2. 수의계약 근거조항 라벨 정리
# --------------------------------------------------------------------------
ARTICLE_RE = re.compile(
    r"(국계법시행령|방위사업법시행령|특정조달\s*특례규정)\s*"
    r"제(\d+)조(?:제(\d+)항)?(?:제(\d+)호)?\s*([가-힣])?목?"
)


def article_key(reason: str) -> str | None:
    m = ARTICLE_RE.search(reason or "")
    if not m:
        return None
    law, art, para, ho, mok = m.groups()
    short = {"국계법시행령": "령", "방위사업법시행령": "방령", "특정조달 특례규정": "특례"}
    law_s = short.get(law.replace("  ", " "), "령")
    key = f"{law_s}{art}"
    if para:
        key += f"-{para}"
    if ho:
        key += f"-{ho}"
    if mok:
        key += mok
    # 소액수의 세부호(1~6)까지 구분
    sub = re.search(r"목\s*(\d)\)", reason or "")
    if sub:
        key += f"({sub.group(1)})"
    return key


def build_article_labels(df: pd.DataFrame, min_count: int = 25):
    sub = df[df["수의계약사유"].str.strip() != ""].copy()
    sub["article"] = sub["수의계약사유"].map(article_key)
    sub = sub[sub["article"].notna()]
    counts = Counter(sub["article"])
    keep = {k for k, v in counts.items() if v >= min_count}
    sub = sub[sub["article"].isin(keep)].copy()
    label_text = (
        sub.groupby("article")["수의계약사유"].agg(lambda s: s.mode().iat[0]).to_dict()
    )
    return sub, label_text


# --------------------------------------------------------------------------
# 3. 특징 추출 : 문자 n-gram TF-IDF + 금액 구간 + 업무구분
# --------------------------------------------------------------------------
def make_text(df: pd.DataFrame) -> pd.Series:
    """계약명 + 업무구분 + 금액 구간 토큰을 하나의 문자열로."""
    bucket = pd.cut(
        df["est_price"],
        bins=[-1, 2e6, 1e7, 2e7, 5e7, 1e8, 5e8, 1e9, 1e13],
        labels=["b0", "b1", "b2", "b3", "b4", "b5", "b6", "b7"],
    ).astype(str)
    return df["name_norm"] + " ⟪" + df["업무구분명"] + "⟫ ⟪" + bucket + "⟫"


def build_vectorizer() -> FeatureUnion:
    return FeatureUnion(
        [
            (
                "char",
                TfidfVectorizer(
                    analyzer="char_wb", ngram_range=(2, 4), min_df=3,
                    sublinear_tf=True, max_features=120_000,
                ),
            ),
            (
                "word",
                TfidfVectorizer(
                    analyzer="word", ngram_range=(1, 2), min_df=3,
                    sublinear_tf=True, max_features=60_000,
                ),
            ),
        ]
    )


def topk_accuracy(proba: np.ndarray, y_true_idx: np.ndarray, k: int) -> float:
    top = np.argsort(-proba, axis=1)[:, :k]
    return float(np.mean([y in row for y, row in zip(y_true_idx, top)]))


def train_classifier(X_text, y, name: str, report_path: Path):
    Xtr, Xte, ytr, yte = train_test_split(
        X_text, y, test_size=0.2, random_state=SEED, stratify=y
    )
    vec = build_vectorizer()
    Xtr_v = vec.fit_transform(Xtr)
    Xte_v = vec.transform(Xte)
    clf = LogisticRegression(
        C=4.0, max_iter=2000, class_weight="balanced", )
    clf.fit(Xtr_v, ytr)

    proba = clf.predict_proba(Xte_v)
    classes = list(clf.classes_)
    y_idx = np.array([classes.index(v) for v in yte])
    pred = clf.predict(Xte_v)

    # 다수결 베이스라인
    major = Counter(ytr).most_common(1)[0][0]
    baseline = float(np.mean(yte == major))

    metrics = {
        "model": name,
        "n_train": int(len(ytr)),
        "n_test": int(len(yte)),
        "n_classes": len(classes),
        "top1_accuracy": round(topk_accuracy(proba, y_idx, 1), 4),
        "top2_accuracy": round(topk_accuracy(proba, y_idx, 2), 4),
        "top3_accuracy": round(topk_accuracy(proba, y_idx, 3), 4),
        "macro_f1": round(float(f1_score(yte, pred, average="macro")), 4),
        "weighted_f1": round(float(f1_score(yte, pred, average="weighted")), 4),
        "majority_baseline_accuracy": round(baseline, 4),
    }
    report_path.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2)
        + "\n\n"
        + classification_report(yte, pred, zero_division=0),
        encoding="utf-8",
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2))

    # 전체 데이터로 재학습하지 않고 '평가한 모델 그 자체'를 배포한다.
    # 보고 지표가 실제 배포 모델의 성능과 정확히 일치하도록 하기 위함이다.
    test_index = list(map(int, np.asarray(Xte.index))) if hasattr(Xte, "index") else []
    return vec, clf, metrics, test_index


# --------------------------------------------------------------------------
# 4. 폐쇄망 추론용 내보내기 (희소 가중치 프루닝)
# --------------------------------------------------------------------------
def export_model(vec: FeatureUnion, clf, path: Path, keep_ratio: float = 0.30,
                 label_text: dict | None = None, meta: dict | None = None):
    blocks = []
    offset = 0
    for bname, tv in vec.transformer_list:
        vocab = {t: int(i) + offset for t, i in tv.vocabulary_.items()}
        idf = tv.idf_.tolist()
        blocks.append(
            {
                "name": bname,
                "analyzer": tv.analyzer,
                "ngram_range": list(tv.ngram_range),
                "offset": offset,
                "vocab": vocab,
                "idf": [round(v, 5) for v in idf],
            }
        )
        offset += len(tv.vocabulary_)

    W = clf.coef_
    thresh = np.quantile(np.abs(W), 1 - keep_ratio)
    sparse_rows = []
    for row in W:
        idx = np.where(np.abs(row) >= thresh)[0]
        sparse_rows.append({"i": idx.tolist(), "w": [round(float(row[j]), 4) for j in idx]})

    payload = {
        "schema": "milcheck-linear-tfidf/1",
        "classes": [str(c) for c in clf.classes_],
        "class_labels": label_text or {},
        "intercept": [round(float(b), 4) for b in clf.intercept_],
        "n_features": int(offset),
        "blocks": blocks,
        "weights": sparse_rows,
        "meta": meta or {},
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    size = path.stat().st_size / 1e6
    print(f"exported {path.name}: {size:.1f} MB, kept {keep_ratio:.0%} weights")
    return payload


def main():
    df = load()
    print("loaded", df.shape)

    # (A) 수의계약 근거조항 분류
    sub, label_text = build_article_labels(df)
    print("article classes:", sub["article"].nunique(), "rows:", len(sub))
    Xa = make_text(sub)
    vec_a, clf_a, m_a, test_a = train_classifier(
        Xa, sub["article"].values, "sole_source_article", OUT / "report_article.txt"
    )
    (OUT / "test_index_article.json").write_text(json.dumps(test_a), encoding="utf-8")
    export_model(
        vec_a, clf_a, OUT / "model_article.json", keep_ratio=0.25,
        label_text=label_text,
        meta={"task": "수의계약 근거조항 추천/검증", "metrics": m_a,
              "source": "방위사업청 국내조달 계약정보(공공데이터포털)"},
    )

    # (B) 계약체결방법 분류
    dfm = df[df["계약체결방법명"] != ""].copy()
    coll = {
        "협상에의한계약(전자)": "협상에의한계약",
        "협상에의한계약(서류)": "협상에의한계약",
        "2단계경쟁(동시)": "2단계경쟁",
        "2단계경쟁(분리)": "2단계경쟁",
    }
    dfm["method"] = dfm["계약체결방법명"].replace(coll)
    keep = {k for k, v in Counter(dfm["method"]).items() if v >= 25}
    dfm = dfm[dfm["method"].isin(keep)]
    Xm = make_text(dfm)
    vec_m, clf_m, m_m, test_m = train_classifier(
        Xm, dfm["method"].values, "contract_method", OUT / "report_method.txt"
    )
    (OUT / "test_index_method.json").write_text(json.dumps(test_m), encoding="utf-8")
    export_model(
        vec_m, clf_m, OUT / "model_method.json", keep_ratio=0.35,
        meta={"task": "계약체결방법 추천/검증", "metrics": m_m,
              "source": "방위사업청 국내조달 계약정보(공공데이터포털)"},
    )

    summary = {"article": m_a, "method": m_m,
               "dataset": {"file": RAW.name, "rows_used": int(len(df)),
                           "date_min": str(df['date'].min().date()),
                           "date_max": str(df['date'].max().date())}}
    (OUT / "training_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
