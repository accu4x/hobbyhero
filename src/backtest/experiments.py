"""Model and feature-layer experiments on identical walk-forward folds (2026-09-24).

Base: logistic regression on the slim rolling-form features (OPEN-ITEMS item 18).
Adds, one layer at a time and then together: recency-weighted form across
seasons, last season, starting goalie, missing lineup, back-to-backs, and Elo.
Also scores Elo and a Poisson goal model on their own, recency sample weights,
and simple averages. Rating-model parameters are tuned only on games before the
test window.

Because many candidates are compared on one test window, the test games are split
in time into a **selection** half and a **confirmation** half. A candidate should
beat the base in both halves before it is believed.

    python -m src.backtest.experiments --out reports/<name>.txt
"""

from __future__ import annotations

import argparse
import itertools
import json
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

import run_pipeline as rp
from src.backtest import market_edge
from src.backtest.compare_models import boot_ci, log_loss_each, slim_columns
from src.features import extra

ROOT = Path(__file__).resolve().parents[2]
TEST_FRAC, N_SPLITS = 0.2, 4


def folds(n: int) -> list[tuple[int, int]]:
    split = int(n * TEST_FRAC)
    chunk = max(1, split // N_SPLITS)
    start = n - split
    return [(start + i * chunk, min(n, start + (i + 1) * chunk)) for i in range(N_SPLITS)]


def logistic_wf(df: pd.DataFrame, cols: list[str], weight_half_life: float | None = None,
                c: float = 0.05) -> pd.Series:
    proba = pd.Series(index=df.index, dtype=float)
    for tr_end, te_end in folds(len(df)):
        train, test = df.iloc[:tr_end], df.iloc[tr_end:te_end]
        model = make_pipeline(StandardScaler(), LogisticRegression(C=c, max_iter=3000))
        kw = {}
        if weight_half_life:
            age = np.arange(len(train))[::-1]  # 0 = most recent training game
            kw["logisticregression__sample_weight"] = 0.5 ** (age / weight_half_life)
        model.fit(train[cols].astype(float), train["home_win"], **kw)
        proba.iloc[tr_end:te_end] = model.predict_proba(test[cols].astype(float))[:, 1]
    return proba


def tune(records: list[dict], df: pd.DataFrame, make: Callable[..., pd.DataFrame], col: str,
         grid: dict[str, list]) -> tuple[dict, pd.DataFrame]:
    """Pick rating-model parameters by log loss on games before the test window."""
    pre = set(df["game"].iloc[:folds(len(df))[0][0]])
    y = df.set_index("game")["home_win"]
    best = None
    for combo in itertools.product(*grid.values()):
        params = dict(zip(grid, combo))
        out = make(records, **params)
        m = out[out["game"].isin(pre)]
        ll = log_loss_each(m[col].to_numpy(), y.loc[m["game"]].to_numpy(float)).mean()
        if best is None or ll < best[0]:
            best = (ll, params, out)
    return best[1], best[2]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    rp._DATA_SOURCE = "nhl_api"
    records = rp.load_parsed_boxscores()
    feats, cols = rp._build_feature_frame(records)
    feats = feats.sort_values("date").reset_index(drop=True)
    slim = slim_columns(cols)
    layers = extra.build_layers(records)
    elo_params, elo_df = tune(records, feats, extra.elo, "elo_p",
                              {"k": [4, 6, 8, 12], "hfa": [20, 35, 50], "revert": [0.6, 0.75]})
    pois_params, pois_df = tune(records, feats, extra.poisson, "pois_p",
                                {"half_life": [20, 40, 80]})

    df = (feats.merge(layers, on="game", how="left").merge(elo_df, on="game", how="left")
          .merge(pois_df, on="game", how="left"))
    df = df.sort_values("date").reset_index(drop=True)
    groups = {
        "recency": [c for c in layers.columns if c.startswith("rec_")],
        "last season": ["prev_pts_delta", "prev_gd_delta"],
        "goalie": ["g_sv_delta", "g_share_delta"],
        "missing lineup": ["miss_toi_delta", "miss_pts_delta"],
        "back-to-back": ["b2b_home", "b2b_away", "b2b_delta"],
        "elo": ["elo_diff"],
    }
    for g in groups.values():
        df[g] = df[g].astype(float).fillna(0.0)
    all_layers = [c for g in groups.values() for c in g]

    preds: dict[str, pd.Series] = {"base: logistic slim": logistic_wf(df, slim)}
    for name, g in groups.items():
        preds[f"base + {name}"] = logistic_wf(df, slim + g)
    preds["base + all layers"] = logistic_wf(df, slim + all_layers)
    preds["all layers, no slim"] = logistic_wf(df, all_layers)
    preds["base + all, recency weights (1 season)"] = logistic_wf(df, slim + all_layers, 1312)
    preds["base + all, recency weights (1/2 season)"] = logistic_wf(df, slim + all_layers, 656)
    test_mask = preds["base: logistic slim"].notna()
    preds["elo alone"] = df["elo_p"].where(test_mask)
    preds["poisson alone"] = df["pois_p"].where(test_mask)
    preds["avg(base, elo, poisson)"] = (preds["base: logistic slim"] + preds["elo alone"]
                                        + preds["poisson alone"]) / 3
    preds["avg(base + all, elo)"] = (preds["base + all layers"] + preds["elo alone"]) / 2

    t = df[test_mask].copy()
    y = t["home_win"].to_numpy(float)
    half = len(t) // 2
    base_ll = log_loss_each(preds["base: logistic slim"][test_mask].to_numpy(), y)

    espn = market_edge.load_espn_archive(ROOT / "data/raw/odds/espn/nhl_archive_2025_26.jsonl")
    games = t[["date", "home", "away", "home_win"]].copy()
    for name, p in preds.items():
        games[name] = p[test_mask].to_numpy()
    games["pred_home_win"] = games["base: logistic slim"]
    m = market_edge.join_model_to_market(games, espn).dropna(subset=["market_home_win"])
    ym = m["home_win"].to_numpy(float)
    mk = log_loss_each(m["market_home_win"].to_numpy(float), ym)

    dates = pd.to_datetime(t["date"].astype(str))
    lines = [
        "=== Model and layer experiments, identical folds (data=nhl_api) ===",
        f"Test games {len(t)} ({dates.min().date()} to {dates.max().date()}); selection half "
        f"to {dates.iloc[half - 1].date()}, confirmation half after. Matched to closing "
        f"lines: {len(m)}. Elo params {elo_params}; Poisson {pois_params}.",
        "",
        f"{'model':42}{'logloss':>8}{'Brier':>7}{'acc':>7}{'sel LL':>8}{'conf LL':>8}"
        f"{'  vs base [90% CI]':>28}{'  vs market [90% CI]':>28}",
    ]
    for name, p in preds.items():
        pv = p[test_mask].to_numpy()
        ll = log_loss_each(pv, y)
        d_mean, d_lo, d_hi = boot_ci(ll - base_ll)
        g_mean, g_lo, g_hi = boot_ci(log_loss_each(m[name].to_numpy(float), ym) - mk)
        lines.append(
            f"{name:42}{ll.mean():>8.4f}{np.mean((pv - y) ** 2):>7.4f}"
            f"{np.mean((pv >= 0.5) == (y == 1)):>7.1%}{ll[:half].mean():>8.4f}"
            f"{ll[half:].mean():>8.4f}"
            f"{f'{d_mean:+.4f} [{d_lo:+.4f}, {d_hi:+.4f}]':>28}"
            f"{f'{g_mean:+.4f} [{g_lo:+.4f}, {g_hi:+.4f}]':>28}")
    lines.append(f"{'closing market (matched games)':42}{mk.mean():>8.4f}")
    lines += ["", "vs base / vs market: difference in log loss (negative = better), paired "
              "bootstrap 90% CI. sel/conf LL: log loss on the first/second half of the test "
              "games. Layers use earlier games only; lineups and starting goalies are "
              "assumed known at puck drop."]
    text = "\n".join(lines)
    print(text)
    Path(args.out).write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
