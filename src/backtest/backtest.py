"""Walk-forward backtesting with honest scoring.

Walks through the data in time order, training on everything strictly
before a cut point and predicting the next chunk. Reports:
  - Brier score (moneyline)
  - Log loss (moneyline)
  - Accuracy
  - Calibration (predicted prob vs observed freq by bin)
  - Edge vs market implied odds (when provided)
"""

from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import xgboost as xgb

from src.models import models


def _calibration_bins(y_true: np.ndarray, p: np.ndarray, n_bins: int = 10):
    """Return per-bin mean predicted prob, observed rate, and count."""
    bins = np.linspace(0.0, 1.0, n_bins + 1)
    idx = np.clip(np.digitize(p, bins) - 1, 0, n_bins - 1)
    out = []
    for b in range(n_bins):
        mask = idx == b
        n = int(mask.sum())
        if n == 0:
            out.append({"bin": f"{bins[b]:.1f}-{bins[b+1]:.1f}", "n": 0,
                        "pred": np.nan, "obs": np.nan})
        else:
            out.append({"bin": f"{bins[b]:.1f}-{bins[b+1]:.1f}", "n": n,
                        "pred": float(np.mean(p[mask])),
                        "obs": float(np.mean(y_true[mask]))})
    return out


def walk_forward_moneyline(
    df: pd.DataFrame,
    feature_cols: List[str],
    test_frac: float = 0.2,
    n_splits: int = 4,
) -> Dict:
    """Walk-forward CV for the moneyline classifier.

    Returns metrics plus an 'oof' DataFrame of out-of-fold test predictions
    (game identity + pred_home_win + true label) for market-edge joins.
    """
    df = df.sort_values("date").reset_index(drop=True)
    n = len(df)
    split = int(n * test_frac)
    # create n_splits expanding-window folds over the last test_frac
    # each fold trains on [0, start) and tests on [start, start+chunk)
    chunk = max(1, split // n_splits)
    preds = pd.Series(index=df.index, dtype=float)
    proba = pd.Series(index=df.index, dtype=float)

    start = n - split
    for fold in range(n_splits):
        train_end = start + fold * chunk
        test_start = train_end
        test_end = min(n, test_start + chunk)
        if test_end <= test_start:
            break
        if train_end < 100:  # need enough training data
            continue
        train = df.iloc[:train_end]
        test = df.iloc[test_start:test_end]
        model = models.train_moneyline_calibrated(train, feature_cols)
        p = models.predict_moneyline(model, test, feature_cols)
        proba.iloc[test.index] = p
        preds.iloc[test.index] = (p >= 0.5).astype(int)

    valid = proba.notna()
    y = df.loc[valid, "home_win"].to_numpy()
    p = proba.loc[valid].to_numpy()
    if len(p) == 0:
        return {"error": "not enough data"}

    brier = np.mean((p - y) ** 2)
    eps = 1e-9
    logloss = -np.mean(y * np.log(np.clip(p, eps, 1)) + (1 - y) * np.log(np.clip(1 - p, eps, 1)))
    acc = np.mean((p >= 0.5).astype(int) == y)
    calib = _calibration_bins(y, p)

    # Feature importance (train final model on all data for reporting)
    final = models.train_moneyline(df, feature_cols)
    importance = dict(zip(feature_cols, final.feature_importances_))

    # out-of-fold predictions for market-edge join
    oof = df.loc[valid, ["date", "home", "away", "home_score", "away_score",
                         "home_win"]].copy()
    oof["pred_home_win"] = proba.loc[valid].to_numpy()

    return {
        "n_test": int(len(p)),
        "brier": float(brier),
        "logloss": float(logloss),
        "accuracy": float(acc),
        "calibration": calib,
        "feature_importance": importance,
        "oof": oof,
    }


def walk_forward_margin(
    df: pd.DataFrame,
    feature_cols: List[str],
    test_frac: float = 0.2,
    n_splits: int = 4,
) -> Dict:
    """Walk-forward CV for the margin regression."""
    df = df.sort_values("date").reset_index(drop=True)
    n = len(df)
    split = int(n * test_frac)
    chunk = max(1, split // n_splits)
    start = n - split
    pred_margin = pd.Series(index=df.index, dtype=float)

    for fold in range(n_splits):
        train_end = start + fold * chunk
        test_start = train_end
        test_end = min(n, test_start + chunk)
        if test_end <= test_start or train_end < 100:
            continue
        model = models.train_margin(df.iloc[:train_end], feature_cols)
        pred_margin.iloc[test_start:test_end] = models.predict_margin(
            model, df.iloc[test_start:test_end], feature_cols)

    valid = pred_margin.notna()
    y = df.loc[valid, "margin"].to_numpy()
    pred = pred_margin.loc[valid].to_numpy()
    if len(pred) == 0:
        return {"error": "not enough data"}
    mae = np.mean(np.abs(pred - y))
    rmse = np.sqrt(np.mean((pred - y) ** 2))
    # puck-line ~ +1.5 for home: prob home covers +1.5
    cover = np.mean(pred + 1.5 > 0)
    return {"n_test": int(len(pred)), "mae": float(mae), "rmse": float(rmse),
            "pct_cover_plus1_5": float(cover)}


def report_moneyline(result: Dict) -> str:
    lines = []
    lines.append("=== Moneyline walk-forward backtest ===")
    lines.append(f"Test games: {result.get('n_test')}")
    lines.append(f"Brier score:   {result.get('brier'):.4f}  (lower is better; 0.25 = coin flip)")
    lines.append(f"Log loss:      {result.get('logloss'):.4f}")
    lines.append(f"Accuracy:      {result.get('accuracy')*100:.1f}%")
    lines.append("Calibration (predicted -> observed):")
    for c in result.get("calibration", []):
        if c["n"] == 0:
            continue
        lines.append(f"  {c['bin']:>12}  n={c['n']:4d}  pred={c['pred']:.2f}  obs={c['obs']:.2f}")
    imp = result.get("feature_importance", {})
    if imp:
        lines.append("Top features:")
        for k, v in sorted(imp.items(), key=lambda kv: -kv[1])[:8]:
            lines.append(f"  {k}: {v:.3f}")
    return "\n".join(lines)


def report_margin(result: Dict) -> str:
    lines = []
    lines.append("=== Puck-line (margin) walk-forward backtest ===")
    lines.append(f"Test games: {result.get('n_test')}")
    lines.append(f"MAE (goal margin):  {result.get('mae'):.2f}")
    lines.append(f"RMSE:               {result.get('rmse'):.2f}")
    lines.append(f"Pct predicted home covers +1.5: {result.get('pct_cover_plus1_5')*100:.1f}%")
    return "\n".join(lines)
