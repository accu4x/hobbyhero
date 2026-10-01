"""Experiments v2: expected goals, goalie GSAx, and the market as an input (2026-09-24).

Same data, folds and test window as src/backtest/experiments.py. Two questions:
  1. Do xG and GSAx improve the box-score base model?
  2. Does anything add information **on top of the closing market**? Models that
     take the de-vigged closing probability as an input are trained only on
     earlier games that have a closing line (ESPN archives for every season on
     disk), and scored on the test games that have one.
Dan's framing (2026-09-24): the market may act as a proxy for the qualitative
measures (injuries, news, mood) the ledger is trying to capture.

    python -m src.backtest.experiments_market --out reports/<name>.txt
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

import run_pipeline as rp
from src.backtest import market_edge
from src.backtest.compare_models import boot_ci, log_loss_each, slim_columns
from src.backtest.experiments import folds
from src.features import extra, xg

ROOT = Path(__file__).resolve().parents[2]
ESPN_DIR = ROOT / "data" / "raw" / "odds" / "espn"


def market_frame() -> pd.DataFrame:
    frames = [market_edge.load_espn_archive(p) for p in sorted(ESPN_DIR.glob("nhl_archive_*.jsonl"))]
    m = pd.concat(frames, ignore_index=True)
    m["date_key"] = m["date"].dt.strftime("%Y%m%d")
    m = m.drop_duplicates(subset=["date_key", "home", "away"], keep="last")
    return m[["date_key", "home", "away", "market_home_win"]]


def wf(df: pd.DataFrame, cols: list[str], c: float = 0.05) -> pd.Series:
    """Walk-forward logistic; trains only on earlier rows where every column is present."""
    proba = pd.Series(index=df.index, dtype=float)
    for tr_end, te_end in folds(len(df)):
        train = df.iloc[:tr_end].dropna(subset=cols)
        test = df.iloc[tr_end:te_end].dropna(subset=cols)
        if len(train) < 100 or test.empty:
            continue
        model = make_pipeline(StandardScaler(), LogisticRegression(C=c, max_iter=3000))
        model.fit(train[cols].astype(float), train["home_win"])
        proba.loc[test.index] = model.predict_proba(test[cols].astype(float))[:, 1]
    return proba


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    rp._DATA_SOURCE = "nhl_api"
    records = rp.load_parsed_boxscores()
    feats, cols = rp._build_feature_frame(records)
    slim = slim_columns(cols)
    layers = extra.build_layers(records)
    shots = xg.score_shots(xg.shot_table(records), xg.train_xg(xg.shot_table(records)))
    xgl = xg.xg_layers(records, shots)

    df = feats.merge(layers, on="game", how="left").merge(xgl, on="game", how="left")
    df["date_key"] = df["date"].astype(str)
    df = df.merge(market_frame(), on=["date_key", "home", "away"], how="left")
    df = df.sort_values("date").reset_index(drop=True)
    p = df["market_home_win"].clip(1e-4, 1 - 1e-4)
    df["market_logit"] = np.log(p / (1 - p))

    xg_cols = [c for c in xgl.columns if c.startswith("xg_")]
    gsax = ["g_gsax_delta"]
    goalie = ["g_sv_delta", "g_share_delta"]
    for c_ in xg_cols + gsax + goalie:
        df[c_] = df[c_].astype(float).fillna(0.0)

    preds: dict[str, pd.Series] = {
        "base: logistic slim": wf(df, slim),
        "base + xG": wf(df, slim + xg_cols),
        "base + GSAx": wf(df, slim + gsax),
        "base + xG + GSAx": wf(df, slim + xg_cols + gsax),
        "xG + GSAx only": wf(df, xg_cols + gsax),
        "market + base": wf(df, ["market_logit"] + slim),
        "market + xG + GSAx": wf(df, ["market_logit"] + xg_cols + gsax),
        "market + GSAx": wf(df, ["market_logit"] + gsax),
        "market + goalie (sv%, share)": wf(df, ["market_logit"] + goalie),
        "market recalibrated (logit only)": wf(df, ["market_logit"]),
    }
    n = len(df)
    start = folds(n)[0][0]
    test = df.iloc[start:folds(n)[-1][1]]
    test = test[test["market_home_win"].notna()]
    for name, s in preds.items():
        test = test[s.loc[test.index].notna()]
    y = test["home_win"].to_numpy(float)
    mk = log_loss_each(test["market_home_win"].to_numpy(float), y)
    base = log_loss_each(preds["base: logistic slim"].loc[test.index].to_numpy(), y)
    half = len(test) // 2
    n_train_mkt = int(df.iloc[:start]["market_home_win"].notna().sum())
    dates = pd.to_datetime(test["date"].astype(str))
    lines = [
        "=== Experiments v2: xG, GSAx, market as input (data=nhl_api) ===",
        f"Scored on test games that have a closing line: {len(test)} "
        f"({dates.min().date()} to {dates.max().date()}). Games with a closing line available "
        f"for training before the test window: {n_train_mkt}. xG trained on "
        f"{xg.XG_TRAIN_SEASONS} only.",
        "",
        f"{'model':36}{'logloss':>8}{'Brier':>7}{'acc':>7}{'sel LL':>8}{'conf LL':>8}"
        f"{'  vs market [90% CI]':>28}{'  vs base [90% CI]':>28}",
    ]
    for name, s in preds.items():
        pv = s.loc[test.index].to_numpy()
        ll = log_loss_each(pv, y)
        g = boot_ci(ll - mk)
        b = boot_ci(ll - base)
        lines.append(f"{name:36}{ll.mean():>8.4f}{np.mean((pv - y) ** 2):>7.4f}"
                     f"{np.mean((pv >= .5) == (y == 1)):>7.1%}{ll[:half].mean():>8.4f}"
                     f"{ll[half:].mean():>8.4f}"
                     f"{f'{g[0]:+.4f} [{g[1]:+.4f}, {g[2]:+.4f}]':>28}"
                     f"{f'{b[0]:+.4f} [{b[1]:+.4f}, {b[2]:+.4f}]':>28}")
    pm = test["market_home_win"].to_numpy(float)
    lines.append(f"{'closing market':36}{mk.mean():>8.4f}{np.mean((pm - y) ** 2):>7.4f}"
                 f"{np.mean((pm >= .5) == (y == 1)):>7.1%}{mk[:half].mean():>8.4f}"
                 f"{mk[half:].mean():>8.4f}")
    lines += ["", "Negative = better. Paired bootstrap 90% CIs. sel/conf LL = first/second half "
              "of the test games in time. Market models train only on earlier games with a "
              "closing line."]
    text = "\n".join(lines)
    print(text)
    Path(args.out).write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
