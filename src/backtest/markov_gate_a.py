"""Gate A for the Markov simulator: does the league-only chain reproduce hockey?

`SPEC-markov-simulator.md` section 8; pre-registration in `OPEN-ITEMS.md` item 26.

Modes:
- `insample`: fit on the training seasons 2020-21 and 2021-22, simulate, and compare with
  those same seasons. A mechanics check on training data only; no held-out season is
  read. Also gives the sampling error of each metric for a 1,312-game season (bootstrap
  over 2021-22 games), the basis for the proposed tolerances.
- `run`: the gate. For each held-out regular season S (2022-23 to 2025-26), fit the chain
  and the xG shot model on every season before S, simulate N_SIM games and compare with
  S as observed. Refuses to run until TOLERANCES is filled in from the pre-registration.

The market is not used anywhere in this file.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import random
import time
from collections import Counter

import numpy as np
import pandas as pd

from src.features import pbp_states as ps
from src.models.markov.simulate import fit_chain, simulate_league

REPORTS = ps.REPORTS
HELD_OUT = (20222023, 20232024, 20242025, 20252026)
INSAMPLE = (20202021, 20212022)
N_SIM = 20_000
SEED = 42
BOOT = 2_000
METRICS = ("goals_per_game", "attempts_per_game", "pp_opps_per_team_game", "pp_pct",
           "ot_share", "so_share", "home_win_rate", "trailing_share_5v5_down1",
           "trailing_share_5v5_down2plus")

# Pre-registered 2026-09-26 (OPEN-ITEMS.md item 26; Dan: "Approve as proposed",
# "Pooled + 1.5x cap"). |simulated - observed|, proportions as fractions.
TOLERANCES: dict[str, float] | None = {
    "goals_per_game": 0.20,
    "attempts_per_game": 3.4,
    "pp_opps_per_team_game": 0.15,
    "pp_pct": 0.014,
    "ot_share": 0.033,
    "so_share": 0.022,
    "home_win_rate": 0.042,
    "trailing_share_5v5_down1": 0.010,
    "trailing_share_5v5_down2plus": 0.012,
}
SEASON_CAP = 1.5
PASS_RULE = ("every metric within tolerance pooled over the held-out seasons "
             "(games-weighted), and no single season beyond 1.5x tolerance")

log = logging.getLogger("markov_gate_a")


def observed(season: int) -> tuple[dict, pd.DataFrame]:
    _, sm = ps.build_events(season)
    return ps.aggregate(sm.drop(columns=["game_id", "season"]).to_dict("records")), sm


def bootstrap_se(sm: pd.DataFrame, n_games: int, seed: int = SEED) -> dict:
    """SE of each metric for a season of `n_games`, resampling games."""
    rng = np.random.default_rng(seed)
    recs = sm.drop(columns=["game_id", "season"]).to_dict("records")
    draws = {k: [] for k in METRICS}
    for _ in range(BOOT):
        idx = rng.integers(0, len(recs), n_games)
        agg = ps.aggregate([recs[i] for i in idx])
        for k in METRICS:
            draws[k].append(agg[k])
    return {k: float(np.std(v, ddof=1)) for k, v in draws.items()}


def mc_se(sims: list[dict]) -> dict:
    """Monte Carlo SE of each simulated metric (batch means over 20 batches)."""
    batches = np.array_split(np.arange(len(sims)), 20)
    vals = {k: [] for k in METRICS}
    for b in batches:
        agg = ps.aggregate([sims[i] for i in b])
        for k in METRICS:
            vals[k].append(agg[k])
    return {k: float(np.std(v, ddof=1) / math.sqrt(len(v))) for k, v in vals.items()}


def insample(n_sim: int) -> dict:
    t0 = time.time()
    model = fit_chain(INSAMPLE, INSAMPLE)
    diag: Counter = Counter()
    sims = simulate_league(model, n_sim, SEED, diag)
    n_train = model.info["train_games"]
    keys = sorted(set(diag) | set(model.train_diag))
    diag_rows = {k: {"sim_per_game": diag[k] / n_sim,
                     "obs_per_game": model.train_diag[k] / n_train} for k in keys}
    obs_all, sms = {}, []
    for s in INSAMPLE:
        o, sm = observed(s)
        obs_all[str(s)] = o
        sms.append(sm)
    pooled = ps.aggregate(pd.concat(sms).drop(columns=["game_id", "season"]).to_dict("records"))
    sim = ps.aggregate(sims)
    return {"mode": "insample (training seasons only; not the gate)",
            "train_seasons": list(INSAMPLE), "n_sim": n_sim, "seed": SEED,
            "model": model.info, "simulated": sim, "simulated_mc_se": mc_se(sims),
            "observed_pooled": pooled, "observed_by_season": obs_all,
            "sampling_se_1312_games": bootstrap_se(sms[-1], 1312),
            "diagnostics_per_game": diag_rows,
            "seconds": round(time.time() - t0, 1)}


def run(n_sim: int) -> dict:
    if TOLERANCES is None:
        raise RuntimeError("Gate A tolerances are not pre-registered yet (OPEN-ITEMS item 26)")
    t0 = time.time()
    out = {"gate": "A", "spec": "SPEC-markov-simulator.md",
           "preregistration": "OPEN-ITEMS.md item 26", "pass_rule": PASS_RULE,
           "tolerances": TOLERANCES, "season_cap": SEASON_CAP, "n_sim": n_sim, "seed": SEED,
           "market_used": False, "seasons": {}}
    all_sims, all_obs = [], []
    cap_ok = True
    for i, season in enumerate(HELD_OUT):
        train = tuple(s for s in ps.SEASONS if s < season)
        model = fit_chain(train, train)
        diag: Counter = Counter()
        sims = simulate_league(model, n_sim, SEED + i, diag)
        sim, se = ps.aggregate(sims), mc_se(sims)
        obs, sm = observed(season)
        all_sims.extend(sims)
        all_obs.extend(sm.drop(columns=["game_id", "season"]).to_dict("records"))
        rows = {}
        for k in METRICS:
            gap = sim[k] - obs[k]
            within = abs(gap) <= TOLERANCES[k]
            capped = abs(gap) <= SEASON_CAP * TOLERANCES[k]
            cap_ok &= capped
            rows[k] = {"simulated": sim[k], "observed": obs[k], "gap": gap,
                       "tolerance": TOLERANCES[k], "mc_se": se[k],
                       "within_tolerance": bool(within), "within_1_5x": bool(capped)}
        out["seasons"][str(season)] = {
            "train_seasons": list(train), "model": model.info, "metrics": rows,
            "diagnostics_per_game": {k: v / n_sim for k, v in sorted(diag.items())}}
        log.info("%s: %s", season, {k: round(v["gap"], 4) for k, v in rows.items()})
    sim_p, obs_p = ps.aggregate(all_sims), ps.aggregate(all_obs)
    pooled = {}
    pooled_ok = True
    for k in METRICS:
        gap = sim_p[k] - obs_p[k]
        ok = abs(gap) <= TOLERANCES[k]
        pooled_ok &= ok
        pooled[k] = {"simulated": sim_p[k], "observed": obs_p[k], "gap": gap,
                     "tolerance": TOLERANCES[k], "pass": bool(ok)}
    out["pooled"] = pooled
    out["pooled_all_within"] = bool(pooled_ok)
    out["no_season_beyond_cap"] = bool(cap_ok)
    out["pass"] = bool(pooled_ok and cap_ok)
    out["seconds"] = round(time.time() - t0, 1)
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("insample", "run"))
    ap.add_argument("--n-sim", type=int, default=N_SIM)
    args = ap.parse_args()
    random.seed(SEED)
    res = insample(args.n_sim) if args.mode == "insample" else run(args.n_sim)
    day = pd.Timestamp.now().strftime("%Y-%m-%d")
    name = (f"markov_gate_a_insample_{day}.json" if args.mode == "insample"
            else f"markov_gate_a_{day}.json")
    path = REPORTS / name
    with path.open("w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1, default=float)
    log.info("-> %s", path)


if __name__ == "__main__":
    main()
