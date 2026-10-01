"""Two pre-registered market tests (SPEC-market-tests.md, locked 2026-09-25).

Test 1: does the model's disagreement with the opening line predict the open->close move?
Test 2: is the closing market's error one-sided by team ("fan tax"), and does it persist?

The market is a yardstick only. No staking, ROI or picks.

    python -m src.backtest.market_tests
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm, poisson

from src.backtest.market_edge import american_to_implied, de_vig, load_espn_archive

ROOT = Path(__file__).resolve().parents[2]
SNAP = ROOT / "artifact" / "snapshot.json"
ESPN = ROOT / "data" / "raw" / "odds" / "espn"
SEED, N_BOOT, N_SIM = 42, 2000, 10000
FRANCHISE = {"ARI": "UTA"}


# ------------------------------------------------------------------ snapshot helpers

def predict(snap: dict, m: dict, x: np.ndarray) -> float:
    z = (x - np.array(m["scaler"]["mean"])) / np.array(m["scaler"]["scale"])
    lh = float(np.exp(z @ np.array(m["home"]["coef"]) + m["home"]["intercept"]))
    la = float(np.exp(z @ np.array(m["away"]["coef"]) + m["away"]["intercept"]))
    k = np.arange(snap["max_goals"])
    joint = np.outer(poisson.pmf(k, lh), poisson.pmf(k, la))
    return float(np.tril(joint, -1).sum() + np.trace(joint) * m["tie_home_share"])


def morning_states(snap: dict) -> dict:
    """Full packed team state on each game-day morning (states are stored as changes)."""
    out, cur, season = {}, {}, None
    for d in snap["dates"]:
        if d["season"] != season:
            cur, season = {}, d["season"]
        cur = {**cur, **snap["states"][d["d"]]}
        out[d["d"]] = cur
    return out


def games_frame(snap: dict) -> pd.DataFrame:
    g = pd.DataFrame(snap["games"], columns=snap["game_cols"])
    g["y"] = (g["home_final"] > g["away_final"]).astype(float)
    return g


def usual_starter_probs(snap: dict, g: pd.DataFrame) -> np.ndarray:
    """Model win % with each team's usual starter (most starts in the last 20, as of that
    morning) instead of the goalie who actually started."""
    keys = snap["state_keys"]
    k = len(keys)
    states = morning_states(snap)
    out = np.empty(len(g))
    for i, row in enumerate(g.itertuples(index=False)):
        st = states[row.date]
        h, a = st[row.home], st[row.away]
        hv = dict(zip(keys, h[:k])); hv["g_gsax"] = h[k + 1]
        av = dict(zip(keys, a[:k])); av["g_gsax"] = a[k + 1]
        x = np.array([hv[f["team_key"]] - av[f["team_key"]] for f in snap["features"]])
        out[i] = predict(snap, snap["models"][row.model], x)
    return out


def ols(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    xc = x - x.mean()
    beta = float((xc * (y - y.mean())).sum() / (xc ** 2).sum())
    resid = y - y.mean() - beta * xc
    r2 = 1 - float((resid ** 2).sum() / ((y - y.mean()) ** 2).sum())
    return beta, r2


def boot_beta(x: np.ndarray, y: np.ndarray, rng) -> list[float]:
    n = len(x)
    bs = [ols(x[idx], y[idx])[0] for idx in rng.integers(0, n, size=(N_BOOT, n))]
    return [float(np.quantile(bs, 0.05)), float(np.quantile(bs, 0.95))]


# ------------------------------------------------------------------ test 1

def opening_lines() -> pd.DataFrame:
    frames = [load_espn_archive(p) for p in sorted(ESPN.glob("nhl_archive_*.jsonl"))]
    m = pd.concat(frames, ignore_index=True)
    m["date"] = m["date"].dt.strftime("%Y%m%d")
    ok = ((m["home_open_ml"].abs() >= 100) & (m["away_open_ml"].abs() >= 100)
          & (m["home_close_ml"].abs() >= 100) & (m["away_close_ml"].abs() >= 100))
    m = m[ok].drop_duplicates(subset=["date", "home", "away"], keep="last").copy()
    m["p_open"] = [de_vig(american_to_implied(h), american_to_implied(a))[0]
                   for h, a in zip(m["home_open_ml"], m["away_open_ml"])]
    m["p_close"] = [de_vig(american_to_implied(h), american_to_implied(a))[0]
                    for h, a in zip(m["home_close_ml"], m["away_close_ml"])]
    return m[["date", "home", "away", "p_open", "p_close"]]


def test1(snap: dict, g: pd.DataFrame, rng) -> dict:
    t = g.merge(opening_lines(), on=["date", "home", "away"], how="inner")
    t = t[t["season"] >= 20232024].reset_index(drop=True)
    t["p_usual"] = usual_starter_probs(snap, t)
    m = (t["p_close"] - t["p_open"]).to_numpy()
    out = {"games": int(len(t)), "seasons": sorted(int(s) for s in t["season"].unique())}
    for name, col in (("primary_usual_starter", "p_usual"), ("secondary_actual_starter", "p_home")):
        d = (t[col] - t["p_open"]).to_numpy()
        beta, r2 = ols(d, m)
        ci = boot_beta(d, m, rng)
        big = (np.abs(m) >= 0.01) & (np.abs(d) >= 0.02)
        agree = float((np.sign(d[big]) == np.sign(m[big])).mean()) if big.any() else None
        by_season = {}
        for s in out["seasons"]:
            k = (t["season"] == s).to_numpy()
            by_season[str(s)] = round(ols(d[k], m[k])[0], 4)
        out[name] = {"beta": round(beta, 4), "ci90": [round(v, 4) for v in ci], "r2": round(r2, 4),
                     "sign_agree": None if agree is None else round(agree, 3),
                     "sign_agree_games": int(big.sum()), "beta_by_season": by_season,
                     "mean_abs_disagreement": round(float(np.abs(d).mean()), 4)}
    out["mean_abs_move"] = round(float(np.abs(m).mean()), 4)
    out["pass"] = bool(out["primary_usual_starter"]["ci90"][0] > 0)
    return out


# ------------------------------------------------------------------ test 2

def team_rows(g: pd.DataFrame, pcol: str) -> pd.DataFrame:
    h = pd.DataFrame({"team": g["home"], "p": g[pcol], "won": g["y"], "season": g["season"],
                      "game": g.index})
    a = pd.DataFrame({"team": g["away"], "p": 1 - g[pcol], "won": 1 - g["y"],
                      "season": g["season"], "game": g.index})
    r = pd.concat([h, a], ignore_index=True)
    r["team"] = r["team"].replace(FRANCHISE)
    r["resid"] = r["won"] - r["p"]
    return r


def dispersion(resid_sum: np.ndarray, var_sum: np.ndarray) -> float:
    return float(((resid_sum / np.sqrt(var_sum)) ** 2).sum())


def test2(g: pd.DataFrame, rng) -> dict:
    g = g[g["market_home"].notna()].reset_index(drop=True)
    g["market_home"] = g["market_home"].astype(float)
    rows = team_rows(g, "market_home")
    teams = sorted(rows["team"].unique())
    tix = {t: i for i, t in enumerate(teams)}
    hi = g["home"].replace(FRANCHISE).map(tix).to_numpy()
    ai = g["away"].replace(FRANCHISE).map(tix).to_numpy()
    p = g["market_home"].to_numpy()
    var = np.bincount(hi, p * (1 - p), len(teams)) + np.bincount(ai, p * (1 - p), len(teams))
    y = g["y"].to_numpy()

    def team_sums(yy: np.ndarray) -> np.ndarray:
        rh = yy - p
        return np.bincount(hi, rh, len(teams)) + np.bincount(ai, -rh, len(teams))

    d_obs = dispersion(team_sums(y), var)
    sims = []
    for start in range(0, N_SIM, 500):
        ys = (rng.random((min(500, N_SIM - start), len(p))) < p).astype(float)
        rh = ys - p
        s = np.stack([np.bincount(hi, r, len(teams)) + np.bincount(ai, -r, len(teams)) for r in rh])
        sims.extend(((s / np.sqrt(var)) ** 2).sum(axis=1))
    p_value = float((np.array(sims) >= d_obs).mean())

    # persistence: team bias in 2022-24 vs 2024-26
    first = g["season"].isin([20222023, 20232024]).to_numpy()

    def half_bias(mask: np.ndarray, idx: np.ndarray | None = None) -> np.ndarray:
        sel = np.where(mask)[0] if idx is None else idx
        rh = y[sel] - p[sel]
        n = np.bincount(hi[sel], None, len(teams)) + np.bincount(ai[sel], None, len(teams))
        s = np.bincount(hi[sel], rh, len(teams)) + np.bincount(ai[sel], -rh, len(teams))
        return s / np.maximum(n, 1)

    b1, b2 = half_bias(first), half_bias(~first)
    r_obs = float(np.corrcoef(b1, b2)[0, 1])
    i1, i2 = np.where(first)[0], np.where(~first)[0]
    rs = [np.corrcoef(half_bias(first, rng.choice(i1, len(i1))),
                      half_bias(~first, rng.choice(i2, len(i2))))[0, 1] for _ in range(N_BOOT)]
    r_ci = [float(np.quantile(rs, 0.05)), float(np.quantile(rs, 0.95))]

    # per-team table with Bonferroni flags (family-wise 10%, two-sided)
    zcrit = float(norm.ppf(1 - 0.10 / (2 * len(teams))))
    sums = team_sums(y)
    n_games = np.bincount(hi, None, len(teams)) + np.bincount(ai, None, len(teams))
    model = team_rows(g, "p_home").groupby("team")["resid"].mean()
    table = []
    for t, i in tix.items():
        z = sums[i] / np.sqrt(var[i])
        table.append({"team": t, "games": int(n_games[i]), "market_bias": round(sums[i] / n_games[i], 4),
                      "z": round(float(z), 2), "flag": bool(abs(z) > zcrit),
                      "model_bias": round(float(model[t]), 4)})
    table.sort(key=lambda r: r["market_bias"])

    # favourite-longshot check by closing-probability bin
    bins = [0, .3, .4, .5, .6, .7, 1.0]
    rows["bin"] = pd.cut(rows["p"], bins, right=False)
    fav = rows.groupby("bin", observed=True).agg(n=("resid", "size"), mean_resid=("resid", "mean"))
    fav_out = [{"bin": str(b), "n": int(r.n), "mean_resid": round(float(r.mean_resid), 4)}
               for b, r in fav.iterrows()]

    return {"games": int(len(g)), "teams": len(teams),
            "dispersion": {"observed": round(d_obs, 2), "null_mean": round(float(np.mean(sims)), 2),
                           "p_value": round(p_value, 4)},
            "persistence": {"r": round(r_obs, 3), "ci90": [round(v, 3) for v in r_ci]},
            "bonferroni_z": round(zcrit, 3), "flagged": [r["team"] for r in table if r["flag"]],
            "teams_table": table, "favourite_longshot": fav_out,
            "pass": bool(p_value < 0.05 and r_ci[0] > 0)}


def main() -> None:
    rng = np.random.default_rng(SEED)
    snap = json.loads(SNAP.read_text(encoding="utf-8"))
    g = games_frame(snap)
    result = {"model_set": snap["meta"]["model_hash"], "label": "historical",
              "test1_line_move": test1(snap, g, rng), "test2_team_bias": test2(g, rng)}
    out = ROOT / "reports" / f"market_tests_{date.today().isoformat()}.json"
    out.write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
