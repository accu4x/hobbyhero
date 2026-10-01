"""Experiments v4 (Dan, 2026-09-24): regression targets instead of win/loss.

The market is used for comparison only, never as an input at prediction time.
Standalone features ("full") = xG + GSAx + Corsi for/against + special teams +
goalie sv% (Dan: include the optional stats). The xG + GSAx set is a reference.
Targets:
  win/loss      logistic regression on home win (current approach)
  goal margin   ridge regression on final goal margin -> P(win) = Phi(mu / sigma),
                sigma = training residual std
  market %      ridge regression on the logit of the closing-line probability
                (training target only: odds are never an input at prediction time)
  Poisson       Poisson regressions for home and away goals (shootout excluded) ->
                P(home win) = P(H > A) + P(tie) x share of home wins in OT/SO
Windows W1/W2 and folds as in OPEN-ITEMS item 23; pooled paired-bootstrap CIs.

    python -m src.backtest.experiments_v4 --out reports/<name>.txt
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm, poisson
from sklearn.linear_model import LogisticRegression, PoissonRegressor, Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

import run_pipeline as rp
from src.backtest.compare_models import boot_ci, log_loss_each
from src.backtest.experiments_confirm import WINDOWS
from src.backtest.experiments_market import market_frame
from src.features import extra, xg

N_FOLDS = 4
MAX_GOALS = 12


def p_winloss(train: pd.DataFrame, test: pd.DataFrame, cols: list[str]) -> np.ndarray:
    m = make_pipeline(StandardScaler(), LogisticRegression(C=0.05, max_iter=3000))
    m.fit(train[cols], train["home_win"])
    return m.predict_proba(test[cols])[:, 1]


def p_margin(train: pd.DataFrame, test: pd.DataFrame, cols: list[str]) -> np.ndarray:
    m = make_pipeline(StandardScaler(), Ridge(alpha=10.0))
    m.fit(train[cols], train["margin"])
    sigma = float(np.std(train["margin"] - m.predict(train[cols])))
    return norm.cdf(m.predict(test[cols]) / sigma)


def p_market_target(train: pd.DataFrame, test: pd.DataFrame, cols: list[str]) -> np.ndarray:
    tr = train.dropna(subset=["market_logit"])
    m = make_pipeline(StandardScaler(), Ridge(alpha=10.0))
    m.fit(tr[cols], tr["market_logit"])
    return 1 / (1 + np.exp(-m.predict(test[cols])))


def p_poisson(train: pd.DataFrame, test: pd.DataFrame, cols: list[str]) -> np.ndarray:
    lam = {}
    for side in ("home_goals", "away_goals"):
        m = make_pipeline(StandardScaler(), PoissonRegressor(alpha=0.1, max_iter=1000))
        m.fit(train[cols], train[side])
        lam[side] = m.predict(test[cols])
    extra_time = train[train["outcome"] != "REG"]
    q = float(extra_time["home_win"].mean()) if len(extra_time) else 0.5
    k = np.arange(MAX_GOALS)
    out = []
    for lh, la in zip(lam["home_goals"], lam["away_goals"]):
        ph, pa = poisson.pmf(k, lh), poisson.pmf(k, la)
        joint = np.outer(ph, pa)
        out.append(np.tril(joint, -1).sum() + np.trace(joint) * q)
    return np.array(out)


TARGETS = {"win/loss (logistic)": p_winloss, "goal margin (ridge)": p_margin,
           "market % as target (ridge)": p_market_target, "Poisson goals": p_poisson}


def window_preds(df: pd.DataFrame, start: str, end: str, cols: list[str], fn) -> pd.Series:
    test = df[(df["date_key"] >= start) & (df["date_key"] <= end)].sort_values("date_key")
    out = pd.Series(index=test.index, dtype=float)
    for chunk in np.array_split(test.index.to_numpy(), N_FOLDS):
        first = df.loc[chunk, "date_key"].min()
        out.loc[chunk] = fn(df[df["date_key"] < first], df.loc[chunk], cols)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    rp._DATA_SOURCE = "nhl_api"
    records = rp.load_parsed_boxscores()
    goals = {str(r["game_id"]): (r["teams"][r["home_abbrev"]]["goals"],
                                 r["teams"][r["away_abbrev"]]["goals"], r["outcome"])
             for r in records}
    feats, _ = rp._build_feature_frame(records)
    shots = xg.shot_table(records)
    layers = extra.build_layers(records)
    market = market_frame()
    extra_cols = ([f"rec_{s}_h{h}_delta" for s in ("cfor", "cagainst", "pp", "pk")
                   for h in (10, 40)] + ["g_sv_delta"])

    results: dict[str, list[tuple[np.ndarray, np.ndarray]]] = {}
    lines = ["=== Experiments v4: regression targets; market for comparison only ===", ""]
    for wname, (start, end, xg_seasons) in WINDOWS.items():
        xgl = xg.xg_layers(records, xg.score_shots(shots, xg.train_xg(shots, xg_seasons)))
        df = feats[["game", "date", "home", "away", "home_win", "margin"]].merge(
            xgl, on="game", how="left").merge(layers, on="game", how="left")
        df["date_key"] = df["date"].astype(str)
        df = df.merge(market, on=["date_key", "home", "away"], how="left")
        p = df["market_home_win"].clip(1e-4, 1 - 1e-4)
        df["market_logit"] = np.log(p / (1 - p))
        g = df["game"].map(goals)
        df["home_goals"] = g.map(lambda v: v[0])
        df["away_goals"] = g.map(lambda v: v[1])
        df["outcome"] = g.map(lambda v: v[2])
        xcols = [c for c in xgl.columns if c.startswith("xg_")] + ["g_gsax_delta"]
        full = xcols + extra_cols
        df[full] = df[full].astype(float).fillna(0.0)
        df = df.sort_values("date_key")

        preds = {}
        for sname, cols in (("full", full), ("xG only", xcols)):
            for tname, fn in TARGETS.items():
                preds[f"{sname} | {tname}"] = window_preds(df, start, end, cols, fn)
        t = df.loc[next(iter(preds.values())).index]
        t = t[t["market_home_win"].notna()]
        y = t["home_win"].to_numpy(float)
        mk = log_loss_each(t["market_home_win"].to_numpy(float), y)
        lines += [f"--- {wname}: {len(t)} games, market log loss {mk.mean():.4f}",
                  f"{'model':44}{'logloss':>8}{'acc':>7}{'  vs market':>11}"]
        for key, s in preds.items():
            pv = np.clip(s.loc[t.index].to_numpy(float), 1e-6, 1 - 1e-6)
            ll = log_loss_each(pv, y)
            results.setdefault(key, []).append((ll, mk))
            lines.append(f"{key:44}{ll.mean():>8.4f}{np.mean((pv >= .5) == (y == 1)):>7.1%}"
                         f"{ll.mean() - mk.mean():>+11.4f}")
        lines.append("")

    ref = np.concatenate([a for a, _ in results["full | win/loss (logistic)"]])
    lines += ["--- Pooled W1 + W2 (paired bootstrap 90% CIs; negative = better)",
              f"{'model':44}{'logloss':>8}{'  vs market [90% CI]':>28}"
              f"{'  vs full win/loss [90% CI]':>30}"]
    for key, parts in sorted(results.items(),
                             key=lambda kv: np.concatenate([a for a, _ in kv[1]]).mean()):
        ll = np.concatenate([a for a, _ in parts])
        mk = np.concatenate([b for _, b in parts])
        gm, rr = boot_ci(ll - mk), boot_ci(ll - ref)
        lines.append(f"{key:44}{ll.mean():>8.4f}"
                     f"{f'{gm[0]:+.4f} [{gm[1]:+.4f}, {gm[2]:+.4f}]':>28}"
                     f"{f'{rr[0]:+.4f} [{rr[1]:+.4f}, {rr[2]:+.4f}]':>30}")
    lines.append(f"{'closing market':44}{mk.mean():>8.4f}")
    text = "\n".join(lines)
    print(text)
    Path(args.out).write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
