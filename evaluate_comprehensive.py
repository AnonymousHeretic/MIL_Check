"""MIL-Check 종합 평가 스위트.

원칙:
- 데이터 일반화(1~3): 기존 evaluate_robustness.py 결과와 동일한 철학.
- 모델 비교(4~6): 2025-12 temporal test를 고정하고 모델/threshold만 변경.
- 입력 견고성(7): 학습 모델과 test label을 고정하고 표현만 변경.
- End-to-end 안전성(8): 외부 감사사례에서 최종 규칙 판정의 위험 누락률 측정.
"""
from __future__ import annotations

import gzip
import json
import re
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, confusion_matrix, f1_score, accuracy_score
from sklearn.pipeline import FeatureUnion

from milcheck.agent import MilCheckAgent
from milcheck.evaluation import eval_audit, eval_extraction, eval_retrieval

DATA = Path("data/contracts_index.jsonl.gz")
OUT = Path("docs/evaluation_results_v2.json")
CUTOFF = pd.Timestamp("2025-12-01")


def load_index() -> pd.DataFrame:
    rows = []
    with gzip.open(DATA, "rt", encoding="utf-8") as fh:
        for line in fh:
            rows.append(json.loads(line))
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["d"], errors="coerce")
    df["est_price"] = pd.to_numeric(df["p"], errors="coerce")
    return df


def prepare_task(df: pd.DataFrame, task: str, min_count: int = 25) -> tuple[pd.DataFrame, str]:
    label = "a" if task == "article" else "m"
    sub = df[df[label].fillna("").str.strip() != ""].copy()
    counts = Counter(sub[label])
    keep = {k for k, v in counts.items() if v >= min_count}
    return sub[sub[label].isin(keep)].reset_index(drop=True), label


def bucket_token(df: pd.DataFrame) -> pd.Series:
    return pd.cut(df["est_price"], bins=[-1, 2e6, 1e7, 2e7, 5e7, 1e8, 5e8, 1e9, 1e13],
                  labels=["b0", "b1", "b2", "b3", "b4", "b5", "b6", "b7"]).astype(str)


def make_text(df: pd.DataFrame, features: str = "full", name_col: str = "n") -> pd.Series:
    n = df[name_col].fillna("")
    if features == "name":
        return n
    if features == "name_work":
        return n + " ⟪" + df["c"].fillna("") + "⟫"
    return n + " ⟪" + df["c"].fillna("") + "⟫ ⟪" + bucket_token(df) + "⟫"


def build_vectorizer(kind: str = "char_word"):
    char = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), min_df=3,
                           sublinear_tf=True, max_features=120_000)
    word = TfidfVectorizer(analyzer="word", ngram_range=(1, 2), min_df=3,
                           sublinear_tf=True, max_features=60_000)
    if kind == "char":
        return char
    if kind == "word":
        return word
    return FeatureUnion([("char", char), ("word", word)])


def train_model(train: pd.DataFrame, label: str, features="full", kind="char_word"):
    vec = build_vectorizer(kind)
    X = vec.fit_transform(make_text(train, features))
    clf = LogisticRegression(C=4.0, max_iter=2000, class_weight="balanced")
    clf.fit(X, train[label])
    return vec, clf


def predict(vec, clf, test: pd.DataFrame, features="full", name_col="n"):
    X = vec.transform(make_text(test, features, name_col=name_col))
    return clf.predict(X), clf.predict_proba(X)


def metric_block(y, pred, proba, classes) -> dict:
    true_idx = np.array([list(classes).index(v) if v in classes else -1 for v in y])
    top3 = np.argsort(-proba, axis=1)[:, :3]
    known = true_idx >= 0
    top3_acc = float(np.mean([a in b for a, b in zip(true_idx[known], top3[known])])) if known.any() else 0.0
    return {
        "accuracy": float(accuracy_score(y, pred)),
        "macro_f1": float(f1_score(y, pred, average="macro", zero_division=0)),
        "weighted_f1": float(f1_score(y, pred, average="weighted", zero_division=0)),
        "top3_accuracy": top3_acc,
    }


def class_error_analysis(train, test, label) -> dict:
    vec, clf = train_model(train, label)
    pred, proba = predict(vec, clf, test)
    report = classification_report(test[label], pred, output_dict=True, zero_division=0)
    labels = list(clf.classes_)
    cm = confusion_matrix(test[label], pred, labels=labels)
    confusions = []
    for i, gold in enumerate(labels):
        for j, guessed in enumerate(labels):
            if i != j and cm[i, j]:
                confusions.append({"gold": str(gold), "predicted": str(guessed), "count": int(cm[i, j])})
    confusions.sort(key=lambda x: x["count"], reverse=True)
    per_class = {}
    counts = Counter(test[label])
    for c in labels:
        r = report.get(str(c), {})
        per_class[str(c)] = {"support": int(counts.get(c, 0)),
                             "precision": float(r.get("precision", 0)),
                             "recall": float(r.get("recall", 0)),
                             "f1": float(r.get("f1-score", 0))}
    return {"overall": metric_block(test[label].to_numpy(), pred, proba, clf.classes_),
            "per_class": per_class, "top_confusions": confusions[:20]}


def baseline_ablation(train, test, label) -> dict:
    y = test[label].to_numpy()
    majority = Counter(train[label]).most_common(1)[0][0]
    variants = {
        "majority_baseline": {"accuracy": float(np.mean(y == majority))},
    }
    specs = [
        ("name_word", "name", "word"),
        ("name_char", "name", "char"),
        ("name_char_word", "name", "char_word"),
        ("name_plus_work", "name_work", "char_word"),
        ("full_current", "full", "char_word"),
    ]
    for name, features, kind in specs:
        vec, clf = train_model(train, label, features, kind)
        pred, proba = predict(vec, clf, test, features)
        variants[name] = metric_block(y, pred, proba, clf.classes_)
    return variants


def confidence_reject(train, test, label) -> dict:
    vec, clf = train_model(train, label)
    pred, proba = predict(vec, clf, test)
    conf = proba.max(axis=1)
    correct = pred == test[label].to_numpy()
    rows = []
    for t in [0.0, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]:
        keep = conf >= t
        rows.append({"threshold": t,
                     "coverage": float(keep.mean()),
                     "accepted": int(keep.sum()),
                     "accuracy_when_accepted": float(correct[keep].mean()) if keep.any() else None,
                     "error_rate_when_accepted": float(1-correct[keep].mean()) if keep.any() else None})
    return {"curve": rows,
            "interpretation": "threshold 이상만 자동추천하고 나머지는 담당자 검토로 넘기는 선택적 예측 평가"}


def perturb_names(test: pd.DataFrame) -> pd.DataFrame:
    base = test.copy()
    variants = []
    transforms = {
        "remove_spaces": lambda s: str(s).replace(" ", ""),
        "year_prefix": lambda s: "2026년 " + str(s),
        "round_suffix": lambda s: str(s) + " 제2차",
        "minor_typo": lambda s: (str(s)[:-1] if len(str(s)) > 3 else str(s)),
    }
    for name, fn in transforms.items():
        v = base.copy()
        v["n_perturbed"] = v["n"].map(fn)
        v["perturbation"] = name
        variants.append(v)
    return pd.concat(variants, ignore_index=True)


def robustness(train, test, label) -> dict:
    vec, clf = train_model(train, label)
    orig_pred, orig_proba = predict(vec, clf, test)
    original = metric_block(test[label].to_numpy(), orig_pred, orig_proba, clf.classes_)
    pert = perturb_names(test)
    out = {"original": original, "perturbations": {}}
    for name, g in pert.groupby("perturbation"):
        pred, proba = predict(vec, clf, g, name_col="n_perturbed")
        m = metric_block(g[label].to_numpy(), pred, proba, clf.classes_)
        # 각 변형은 원 test와 동일 순서이므로 원래 예측과의 일관성도 계산 가능
        m["prediction_consistency"] = float(np.mean(pred == orig_pred))
        out["perturbations"][name] = m
    return out


def evaluate_ml_task(df: pd.DataFrame, task: str) -> dict:
    sub, label = prepare_task(df, task)
    train = sub[sub["date"] < CUTOFF].copy()
    test = sub[sub["date"] >= CUTOFF].copy()
    return {
        "task": task, "label": label,
        "train_rows": int(len(train)), "test_rows": int(len(test)),
        "train_period": f"{train['d'].min()}..{train['d'].max()}",
        "test_period": f"{test['d'].min()}..{test['d'].max()}",
        "class_error_analysis": class_error_analysis(train, test, label),
        "baseline_ablation": baseline_ablation(train, test, label),
        "confidence_reject": confidence_reject(train, test, label),
        "robustness": robustness(train, test, label),
    }


def end_to_end_safety() -> dict:
    agent = MilCheckAgent(llm_mode="none", use_ml=True, use_contract_index=True)
    audit = eval_audit(agent)
    details = audit["details"]
    risky = [r for r in details if r["expected_decision"] in {"REJECT_GROUND", "NEEDS_EVIDENCE"}]
    false_pass = [r for r in risky if r["decision"] == "PASS_WITH_CONTROLS"]
    extraction = eval_extraction(Path("eval/extraction_holdout.jsonl"), "홀드아웃")
    retrieval = eval_retrieval(agent.retriever)
    return {
        "scope": "자유서술 추출은 별도 홀드아웃으로, 최종 위험판정은 외부 감사사례로 평가. 감사사례 자체는 구조화 입력이라 완전한 자연어 end-to-end와는 구분함.",
        "external_audit_cases": int(len(details)),
        "risk_cases": int(len(risky)),
        "risk_detection_rate": float(audit["detection_rate"]),
        "decision_match_rate": float(audit["decision_match_rate"]),
        "false_pass": int(len(false_pass)),
        "false_pass_rate": float(len(false_pass)/len(risky)) if risky else 0.0,
        "false_pass_cases": false_pass,
        "extraction_holdout": extraction,
        "retrieval": retrieval,
    }


def main():
    df = load_index()
    robustness_path = Path("docs/robustness_results.json")
    prior = json.loads(robustness_path.read_text(encoding="utf-8")) if robustness_path.exists() else None
    results = {
        "evaluation_plan": {
            "1_stratified_5fold": "분할 우연성 — docs/robustness_results.json 참조",
            "2_name_group_5fold": "유사계약 누수 — docs/robustness_results.json 참조",
            "3_temporal_holdout": "미래 일반화 — 2025-12 고정 시험셋",
            "4_class_error": "클래스별 precision/recall/F1 및 confusion",
            "5_baseline_ablation": "시험셋 고정, 입력/모델 구성만 변경",
            "6_confidence_reject": "시험셋/모델 고정, confidence threshold만 변경",
            "7_robustness": "시험셋/모델 고정, 계약명 표현만 변경",
            "8_end_to_end_safety": "외부 감사사례의 최종 위험 누락(false pass) + 추출/검색 홀드아웃",
        },
        "prior_generalization": prior,
        "article": evaluate_ml_task(df, "article"),
        "method": evaluate_ml_task(df, "method"),
        "end_to_end_safety": end_to_end_safety(),
        "limitations": [
            "비식별 인덱스에 계약번호가 없어 contract-id Group CV는 원본 CSV에서 별도 수행 필요",
            "robustness 변형은 자동 생성된 표현 교란이므로 실제 사용자 입력 분포 전체를 대표하지 않음",
            "외부 감사사례는 구조화 입력으로 재구성되어 자연어 입력부터 최종 판정까지의 완전한 외부 end-to-end 세트는 아직 없음",
        ],
    }
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
