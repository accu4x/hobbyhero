"""Pre-registered confirmation of the xG-only model (OPEN-ITEMS item 23, 2026-09-24).

Frozen spec: standardised logistic regression, C=0.05, features = the 10
xg_*_h10/h40 deltas + g_gsax_delta. Compared with the closing market, the base
(logistic slim) and Elo on two windows:
  W1  2024-25 from 2025-01-15 (never seen), xG trained on 2023-24 only.
  W2  2025-26 full regular season, xG trained on 2023-24 + 2024-25.
Each window is split into 4 chronological folds; a fold trains on every game
before its first date. Scoring uses the test games that have a closing line.

    python -m src.backtest.experiments_confirm --out reports/<name>.txt
"""

from __future__ import annotations

import argparse
import itertools
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

import run_pipeline as rp
from src.backtest.compare_models import boot_ci, log_loss_each, slim_columns
from src.backtest.experiments_market import market_frame
from src.features import extra, xg

ROOT = Path(__file__).resolve().parents[2]
WINDOWS = {
    "W1 2024-25 from Jan 15 (unseen)": ("20250115", "20250418", (20232024,)),
    "W2 2025-26 full season": ("20251007", "20260416", (20232024, 20242025)),
}
N_FOLDS = 4


def fit_predict(train: pd.DataFrame, test: pd.DataFrame, cols: list[str]) -> np.ndarray:
    model = make_pipeline(StandardScaler(), LogisticRegression(C=0.05, max_iter=3000))
    model.fit(train[cols].astype(float), train["home_win"])
    return model.predict_proba(test[cols].astype(float))[:, 1]


def window_preds(df: pd.DataFrame, start: str, end: str, cols: list[str]) -> pd.Series:
    test = df[(df["date_key"] >= start) & (df["date_key"] <= end)].sort_values("date_key")
    out = pd.Series(index=test.index, dtype=float)
    for chunk in np.array_split(test.index.to_numpy(), N_FOLDS):
        first = df.loc[chunk, "date_key"].min()
        train = df[df["date_key"] < first].dropna(subset=cols)
        rows = df.loc[chunk].dropna(subset=cols)
        out.loc[rows.index] = fit_predict(train, rows, cols)
    return out


def tuned_elo(records: list[dict], df: pd.DataFrame, before: str) -> pd.DataFrame:
    y = df.set_index("game")["home_win"]
    pre = set(df.loc[df["date_key"] < before, "game"])
    best = None
    for k, hfa, rev in itertools.product([4, 6, 8, 12], [20, 35, 50], [0.6, 0.75]):
        e = extra.elo(records, k=k, hfa=hfa, revert=rev)
        m = e[e["game"].isin(pre)]
        ll = log_loss_each(m["elo_p"].to_numpy(), y.loc[m["game"]].to_numpy(float)).mean()
        if best is None or ll < best[0]:
            best = (ll, (k, hfa, rev), e)
    return best[2], best[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    rp._DATA_SOURCE = "nhl_api"
    records = rp.load_parsed_boxscores()
    feats, cols = rp._build_feature_frame(records)
    slim = slim_columns(cols)
    shots = xg.shot_table(records)
    market = market_frame()

    lines = ["=== Pre-registered confirmation: xG + GSAx only (frozen spec) ===", ""]
    pooled: dict[str, list[np.ndarray]] = {}
    for wname, (start, end, xg_seasons) in WINDOWS.items():
        model = xg.train_xg(shots, xg_seasons)
        layers = xg.xg_layers(records, xg.score_shots(shots, model))
        df = feats.merge(layers, on="game", how="left")
        df["date_key"] = df["date"].astype(str)
        df = df.merge(market, on=["date_key", "home", "away"], how="left")
        elo_df, elo_params = tuned_elo(records, df, start)
        df = df.merge(elo_df, on="game", how="left").sort_values("date_key")
        xcols = [c for c in layers.columns if c.startswith("xg_")] + ["g_gsax_delta"]
        df[xcols] = df[xcols].astype(float).fillna(0.0)

        preds = {"xG + GSAx only (frozen)": window_preds(df, start, end, xcols),
                 "base: logistic slim": window_preds(df, start, end, slim)}
        idx = preds["xG + GSAx only (frozen)"].index
        preds["elo alone"] = df.loc[idx, "elo_p"]
        t = df.loc[idx]
        t = t[t["market_home_win"].notna()]
        for p in preds.values():
            t = t[p.loc[t.index].notna()]
        y = t["home_win"].to_numpy(float)
        mk = log_loss_each(t["market_home_win"].to_numpy(float), y)
        dates = pd.to_datetime(t["date_key"])
        lines += [f"--- {wname}: {len(t)} games with a closing line "
                  f"({dates.min().date()} to {dates.max().date()}); xG trained on {xg_seasons}; "
                  f"Elo (k, hfa, revert) = {elo_params}",
                  f"{'model':30}{'logloss':>8}{'Brier':>7}{'acc':>7}{'  vs market [90% CI]':>28}"]
        for name, p in preds.items():
            pv = p.loc[t.index].to_numpy(float)
            ll = log_loss_each(pv, y)
            g = boot_ci(ll - mk)
            pooled.setdefault(name, []).append(ll - mk)
            lines.append(f"{name:30}{ll.mean():>8.4f}{np.mean((pv - y) ** 2):>7.4f}"
                         f"{np.mean((pv >= .5) == (y == 1)):>7.1%}"
                         f"{f'{g[0]:+.4f} [{g[1]:+.4f}, {g[2]:+.4f}]':>28}")
        pm = t["market_home_win"].to_numpy(float)
        lines += [f"{'closing market':30}{mk.mean():>8.4f}{np.mean((pm - y) ** 2):>7.4f}"
                  f"{np.mean((pm >= .5) == (y == 1)):>7.1%}", ""]
    lines.append("--- Pooled W1 + W2 (model minus market log loss, paired bootstrap 90% CI)")
    for name, parts in pooled.items():
        g = boot_ci(np.concatenate(parts))
        lines.append(f"{name:30}{f'{g[0]:+.4f} [{g[1]:+.4f}, {g[2]:+.4f}]':>28}")
    lines += ["", "Reading rule (pre-registered): confirmed lead if the xG-only model beats the "
              "market in both windows; significant only if the pooled CI is entirely below 0."]
    text = "\n".join(lines)
    print(text)
    Path(args.out).write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
