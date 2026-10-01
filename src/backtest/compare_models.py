"""Compare candidate base models on identical walk-forward folds (2026-09-24).

OPEN-ITEMS item 18: on the corrected NHL API data the 86-feature XGBoost barely
beats a coin flip. Before any qualitative feature is tested on top of a base
model, pick the base honestly. Every candidate here sees the same rows, the same
expanding-window folds as src/backtest/backtest.py (last 20%, 4 folds) and is
scored on the same games, overall and on the games matched to ESPN closing
lines. A paired bootstrap gives the log-loss gap to the market with a 90% CI.

    python -m src.backtest.compare_models --out reports/<name>.txt
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

import run_pipeline as rp
from src.backtest import market_edge

ROOT = Path(__file__).resolve().parents[2]
EPS = 1e-6
SEED = 42
N_BOOT = 2000

Model = Callable[[pd.DataFrame, pd.Series], object]


def slim_columns(cols: list[str]) -> list[str]:
    """Exponentially weighted + 20-game window per stat, plus rest days."""
    return [c for c in cols if c.endswith("_exp_delta") or c.endswith("_w20_delta")
            or c == "rest_days_delta"]


def xgb_full() -> object:
    base = xgb.XGBClassifier(n_estimators=300, max_depth=5, learning_rate=0.05,
                             subsample=0.8, colsample_bytree=0.8, random_state=SEED,
                             eval_metric="logloss", n_jobs=-1)
    return CalibratedClassifierCV(base, method="isotonic", cv=5)


def xgb_regularised() -> object:
    base = xgb.XGBClassifier(n_estimators=250, max_depth=2, learning_rate=0.03,
                             min_child_weight=20, reg_lambda=10.0, subsample=0.8,
                             colsample_bytree=0.6, random_state=SEED,
                             eval_metric="logloss", n_jobs=-1)
    return CalibratedClassifierCV(base, method="sigmoid", cv=5)


def logistic(c: float) -> Callable[[], object]:
    return lambda: make_pipeline(StandardScaler(),
                                 LogisticRegression(C=c, max_iter=2000))


class HomeRate:
    """Predicts the training-set home win rate for every game."""

    def fit(self, x: pd.DataFrame, y: pd.Series) -> "HomeRate":
        self._p = float(np.mean(y))
        return self

    def predict_proba(self, x: pd.DataFrame) -> np.ndarray:
        return np.column_stack([np.full(len(x), 1 - self._p), np.full(len(x), self._p)])


def walk_forward(df: pd.DataFrame, cols: list[str], make: Callable[[], object],
                 test_frac: float = 0.2, n_splits: int = 4) -> pd.Series:
    n = len(df)
    split = int(n * test_frac)
    chunk = max(1, split // n_splits)
    start = n - split
    proba = pd.Series(index=df.index, dtype=float)
    for fold in range(n_splits):
        train_end = start + fold * chunk
        test_end = min(n, train_end + chunk)  # same as backtest.walk_forward_moneyline
        train, test = df.iloc[:train_end], df.iloc[train_end:test_end]
        model = make()
        model.fit(train[cols].astype(float), train["home_win"])
        proba.iloc[train_end:test_end] = model.predict_proba(test[cols].astype(float))[:, 1]
    return proba


def log_loss_each(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    p = np.clip(p, EPS, 1 - EPS)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def boot_ci(diff: np.ndarray) -> tuple[float, float, float]:
    rng = np.random.default_rng(SEED)
    idx = rng.integers(0, len(diff), size=(N_BOOT, len(diff)))
    means = diff[idx].mean(axis=1)
    return float(diff.mean()), float(np.quantile(means, 0.05)), float(np.quantile(means, 0.95))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    rp._DATA_SOURCE = "nhl_api"
    feats, cols = rp._build_feature_frame(rp.load_parsed_boxscores())
    df = feats.sort_values("date").reset_index(drop=True)
    slim = slim_columns(cols)

    candidates: dict[str, tuple[list[str], Callable[[], object]]] = {
        "home-rate constant": (slim, HomeRate),
        "xgb full (current)": (cols, xgb_full),
        "xgb slim": (slim, xgb_full),
        "xgb slim regularised": (slim, xgb_regularised),
        "logistic slim C=0.05": (slim, logistic(0.05)),
        "logistic slim C=1": (slim, logistic(1.0)),
        "logistic full C=0.05": (cols, logistic(0.05)),
    }
    preds = {name: walk_forward(df, c, make) for name, (c, make) in candidates.items()}
    test = preds["xgb full (current)"].notna()
    y = df.loc[test, "home_win"].to_numpy(dtype=float)

    games = df.loc[test, ["date", "home", "away", "home_win"]].copy()
    for name, p in preds.items():
        games[name] = p[test].to_numpy()
    espn = market_edge.load_espn_archive(
        ROOT / "data/raw/odds/espn/nhl_archive_2025_26.jsonl")
    games["pred_home_win"] = games["xgb full (current)"]
    m = market_edge.join_model_to_market(games, espn).dropna(subset=["market_home_win"])
    ym = m["home_win"].to_numpy(dtype=float)
    mk = log_loss_each(m["market_home_win"].to_numpy(dtype=float), ym)

    dates = pd.to_datetime(games["date"].astype(str))
    lines = [
        "=== Base model comparison, identical walk-forward folds (data=nhl_api) ===",
        f"Rows {len(df)} | features full {len(cols)}, slim {len(slim)} | "
        f"test games {int(test.sum())} ({dates.min().date()} to "
        f"{dates.max().date()}) | matched to closing lines {len(m)}",
        "",
        f"{'model':24}{'logloss':>9}{'Brier':>8}{'acc':>7}   "
        f"{'matched: logloss':>17}{'  gap to market [90% CI]':>30}",
    ]
    for name in preds:
        p = games[name].to_numpy(dtype=float)
        ll = log_loss_each(p, y).mean()
        br = float(np.mean((p - y) ** 2))
        acc = float(np.mean((p >= 0.5) == (y == 1)))
        pm = m[name].to_numpy(dtype=float)
        mean, lo, hi = boot_ci(log_loss_each(pm, ym) - mk)
        lines.append(f"{name:24}{ll:>9.4f}{br:>8.4f}{acc:>7.1%}   "
                     f"{log_loss_each(pm, ym).mean():>17.4f}"
                     f"{f'{mean:+.4f} [{lo:+.4f}, {hi:+.4f}]':>30}")
    lines.append(f"{'closing market':24}{'':>9}{'':>8}{'':>7}   {mk.mean():>17.4f}")
    lines += ["", "Gap to market = model log loss minus market log loss on the matched "
              "games (positive = worse than the market). CI from a paired bootstrap."]
    text = "\n".join(lines)
    print(text)
    Path(args.out).write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
