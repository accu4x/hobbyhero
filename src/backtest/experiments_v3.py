"""Experiments v3 (Dan, 2026-09-24): does adding Corsi, special teams and goalie save %
back to the xG model help; does the market as an input help; gradient boosting vs
logistic regression.

Grid: 5 feature sets x (without / with the market's closing probability as an input)
x (logistic regression / gradient boosting) = 20 models, each scored on the two
windows from the pre-registered confirmation (OPEN-ITEMS item 23):
  W1  2024-25 from 2025-01-15 (xG trained on 2023-24 only)
  W2  2025-26 full regular season (xG trained on 2023-24 + 2024-25)
4 chronological folds per window; each fold trains on every earlier game (market
models: every earlier game with a closing line). Scored on test games with a line.
Pooled over both windows: model minus market, and model minus the current best
(xG + GSAx, logistic, no market), with paired-bootstrap 90% CIs.

    python -m src.backtest.experiments_v3 --out reports/<name>.txt
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

import run_pipeline as rp
from src.backtest.compare_models import boot_ci, log_loss_each
from src.backtest.experiments_confirm import WINDOWS
from src.backtest.experiments_market import market_frame
from src.features import extra, xg

N_FOLDS = 4
SEED = 42


def logistic() -> object:
    return make_pipeline(StandardScaler(), LogisticRegression(C=0.05, max_iter=3000))


def boosting() -> object:
    # Deliberately conservative: shallow trees, slow learning, big leaves, L2.
    return HistGradientBoostingClassifier(max_depth=3, learning_rate=0.03, max_iter=300,
                                          min_samples_leaf=50, l2_regularization=1.0,
                                          random_state=SEED)


ALGOS: dict[str, Callable[[], object]] = {"logistic": logistic, "gradient boosting": boosting}


def window_preds(df: pd.DataFrame, start: str, end: str, cols: list[str],
                 make: Callable[[], object]) -> pd.Series:
    test = df[(df["date_key"] >= start) & (df["date_key"] <= end)].sort_values("date_key")
    out = pd.Series(index=test.index, dtype=float)
    for chunk in np.array_split(test.index.to_numpy(), N_FOLDS):
        first = df.loc[chunk, "date_key"].min()
        train = df[df["date_key"] < first].dropna(subset=cols)
        rows = df.loc[chunk].dropna(subset=cols)
        model = make()
        model.fit(train[cols].astype(float), train["home_win"])
        out.loc[rows.index] = model.predict_proba(rows[cols].astype(float))[:, 1]
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    rp._DATA_SOURCE = "nhl_api"
    records = rp.load_parsed_boxscores()
    feats, _ = rp._build_feature_frame(records)
    shots = xg.shot_table(records)
    layers = extra.build_layers(records)
    market = market_frame()

    corsi = [f"rec_{s}_h{h}_delta" for s in ("cfor", "cagainst") for h in (10, 40)]
    special = [f"rec_{s}_h{h}_delta" for s in ("pp", "pk") for h in (10, 40)]
    goalie_sv = ["g_sv_delta"]

    results: dict[str, list[tuple[np.ndarray, np.ndarray]]] = {}
    lines = ["=== Experiments v3: xG + Corsi / special teams / goalie sv%, market in vs out, "
             "gradient boosting vs logistic ===", ""]
    for wname, (start, end, xg_seasons) in WINDOWS.items():
        xgl = xg.xg_layers(records, xg.score_shots(shots, xg.train_xg(shots, xg_seasons)))
        df = feats[["game", "date", "home", "away", "home_win"]].merge(xgl, on="game", how="left")
        df = df.merge(layers, on="game", how="left")
        df["date_key"] = df["date"].astype(str)
        df = df.merge(market, on=["date_key", "home", "away"], how="left")
        p = df["market_home_win"].clip(1e-4, 1 - 1e-4)
        df["market_logit"] = np.log(p / (1 - p))
        xcols = [c for c in xgl.columns if c.startswith("xg_")] + ["g_gsax_delta"]
        feat_cols = xcols + corsi + special + goalie_sv
        df[feat_cols] = df[feat_cols].astype(float).fillna(0.0)
        sets = {
            "xG": xcols,
            "xG + Corsi": xcols + corsi,
            "xG + special teams": xcols + special,
            "xG + goalie sv%": xcols + goalie_sv,
            "xG + all three": xcols + corsi + special + goalie_sv,
        }
        preds = {}
        for sname, cols in sets.items():
            for mkt in (False, True):
                use = (["market_logit"] + cols) if mkt else cols
                for aname, make in ALGOS.items():
                    key = f"{sname} | {'market in' if mkt else 'no market'} | {aname}"
                    preds[key] = window_preds(df, start, end, use, make)
        idx = next(iter(preds.values())).index
        t = df.loc[idx]
        t = t[t["market_home_win"].notna()]
        for s in preds.values():
            t = t[s.loc[t.index].notna()]
        y = t["home_win"].to_numpy(float)
        mk = log_loss_each(t["market_home_win"].to_numpy(float), y)
        dates = pd.to_datetime(t["date_key"])
        lines += [f"--- {wname}: {len(t)} games ({dates.min().date()} to {dates.max().date()}), "
                  f"xG trained on {xg_seasons}; market log loss {mk.mean():.4f}",
                  f"{'model':62}{'logloss':>8}{'acc':>7}{'  vs market':>11}"]
        for key, s in preds.items():
            pv = s.loc[t.index].to_numpy(float)
            ll = log_loss_each(pv, y)
            results.setdefault(key, []).append((ll, mk))
            lines.append(f"{key:62}{ll.mean():>8.4f}{np.mean((pv >= .5) == (y == 1)):>7.1%}"
                         f"{ll.mean() - mk.mean():>+11.4f}")
        lines.append("")

    ref_key = "xG | no market | logistic"
    ref = np.concatenate([ll for ll, _ in results[ref_key]])
    lines += ["--- Pooled W1 + W2 (paired bootstrap 90% CIs; negative = better)",
              f"{'model':62}{'logloss':>8}{'  vs market [90% CI]':>28}"
              f"{'  vs xG logistic [90% CI]':>28}"]
    for key, parts in sorted(results.items(),
                             key=lambda kv: np.concatenate([a for a, _ in kv[1]]).mean()):
        ll = np.concatenate([a for a, _ in parts])
        mk = np.concatenate([b for _, b in parts])
        g, r = boot_ci(ll - mk), boot_ci(ll - ref)
        lines.append(f"{key:62}{ll.mean():>8.4f}"
                     f"{f'{g[0]:+.4f} [{g[1]:+.4f}, {g[2]:+.4f}]':>28}"
                     f"{f'{r[0]:+.4f} [{r[1]:+.4f}, {r[2]:+.4f}]':>28}")
    lines.append(f"{'closing market':62}{np.concatenate([b for _, b in results[ref_key]]).mean():>8.4f}")
    lines += ["", "20 models were compared, so expect one or two to look good by chance. Trust "
              "only a pooled CI that sits entirely on one side of 0."]
    text = "\n".join(lines)
    print(text)
    Path(args.out).write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
