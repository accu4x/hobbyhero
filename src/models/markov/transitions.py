"""Transition tables for the semi-Markov chain (SPEC-markov-simulator.md section 5).

Gate A is league-only, so every table is a count table smoothed by hierarchical backoff
rather than the penalised regression the spec plans for team effects (Gate B): a cell's
distribution is (counts + alpha * parent) / (n + alpha), where the parent is the same
distribution one level coarser, down to the global rate.

- Stage A: next event (FACEOFF, ATTEMPT, PENALTY, STOPPAGE, OTHER), its actor (H/A/N)
  and zone (relative to home), given the state (last class, last actor, manpower, score,
  zone, phase). Period ends are not sampled: the clock censors them (timing.py).
- Stage B (reordered 2026-09-26, because blocked-shot coordinates mark the block, not
  the shot): B1 blocked or not; B2 xG bin of an unblocked attempt; B3 missed, saved or
  goal given the bin. All from the shooter's perspective.
- Penalty category given the penalised side and manpower.

xG bins come from the existing walk-forward shot model (`src/features/xg.py`), trained
only on seasons before the one being simulated.
"""

from __future__ import annotations

import bisect
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from src.features import pbp_states as ps
from src.features import xg

XG_BIN_EDGES = (0.03, 0.08, 0.20)          # 4 bins: <3%, 3-8%, 8-20%, >=20%
XG_BINS = tuple(str(i) for i in range(len(XG_BIN_EDGES) + 1))
ALPHA = 10.0
STAGE_A_OUTCOMES = tuple(f"{k}|{a}|{z}" for k in ps.KINDS[:5] for a in ps.ACTORS
                         for z in ps.ZONES)
STAGE_A_LEVELS = [("cls", "actor", "mp", "sb", "zone", "ph"),
                  ("cls", "actor", "mp", "sb", "zone"),
                  ("cls", "actor", "mp", "zone"),
                  ("cls", "actor", "mp"),
                  ("cls", "mp"),
                  ("cls",)]
BLOCK_LEVELS = [("smp", "sbs", "ph"), ("smp",)]
BIN_LEVELS = [("smp", "reb", "cls"), ("smp", "reb"), ("smp",)]
RESULT_LEVELS = [("bin", "smp"), ("bin",)]
PEN_LEVELS = [("actor", "mp"), ("actor",)]
REBOUND_SECS = 3

log = logging.getLogger("markov.transitions")


class BackoffTable:
    """Categorical distribution over `outcomes`, smoothed along nested `levels`.

    `levels` run finest to coarsest; a global level is always added last. `sample`
    takes a key tuple ordered like the finest level.
    """

    def __init__(self, outcomes: Sequence[str], levels: list[tuple[str, ...]],
                 alpha: float = ALPHA) -> None:
        self.outcomes = tuple(outcomes)
        self.levels = [tuple(lv) for lv in levels] + [()]
        self.alpha = alpha
        finest = self.levels[0]
        self._proj = [tuple(finest.index(c) for c in lv) for lv in self.levels]
        self._tables: list[dict[tuple, np.ndarray]] = []
        self._cache: dict[tuple, list[float]] = {}
        self.counts: list[int] = []

    def fit(self, df: pd.DataFrame, outcome_col: str) -> BackoffTable:
        idx = {o: i for i, o in enumerate(self.outcomes)}
        y = df[outcome_col].map(idx)
        if y.isna().any():
            bad = df.loc[y.isna(), outcome_col].unique()[:5]
            raise ValueError(f"unknown outcomes {bad!r} for {outcome_col}")
        df = df.assign(_y=y.astype(int))
        k = len(self.outcomes)
        glob = np.bincount(df["_y"], minlength=k).astype(float) + 0.5
        tables: list[dict[tuple, np.ndarray]] = [None] * len(self.levels)  # type: ignore
        tables[-1] = {(): glob / glob.sum()}
        for li in range(len(self.levels) - 2, -1, -1):
            cols = list(self.levels[li])
            parent_cols = self.levels[li + 1]
            pos = [cols.index(c) for c in parent_cols]
            ct = (df.groupby(cols + ["_y"]).size().unstack(fill_value=0)
                  .reindex(columns=range(k), fill_value=0))
            table = {}
            for key, row in zip(ct.index, ct.to_numpy(dtype=float)):
                key = key if isinstance(key, tuple) else (key,)
                parent = tables[li + 1][tuple(key[p] for p in pos)]
                table[key] = (row + self.alpha * parent) / (row.sum() + self.alpha)
            tables[li] = table
        self._tables = tables
        self.counts = [len(t) for t in tables]
        self._cache.clear()
        return self

    def probs(self, key: tuple) -> np.ndarray:
        for proj, table in zip(self._proj, self._tables):
            sub = tuple(key[i] for i in proj)
            if sub in table:
                return table[sub]
        raise KeyError(key)

    def _cum(self, key: tuple) -> list[float]:
        cum = self._cache.get(key)
        if cum is None:
            cum = np.cumsum(self.probs(key)).tolist()
            cum[-1] = 1.0
            self._cache[key] = cum
        return cum

    def sample(self, key: tuple, u: float) -> str:
        cum = self._cum(key)
        return self.outcomes[min(bisect.bisect_right(cum, u), len(cum) - 1)]


def xg_bin(x: float) -> str:
    return str(bisect.bisect_right(XG_BIN_EDGES, x))


def attach_xg(ev: pd.DataFrame, train_seasons: tuple[int, ...],
              xg_seasons: tuple[int, ...]) -> tuple[pd.DataFrame, dict]:
    """Add `xg` and `bin` to unblocked attempts with coordinates.

    The shot model is `xg.train_xg` on `xg_seasons` only (every season before the one
    being simulated). Joined on (game_id, shot_idx): both sides count unblocked shots with
    coordinates in play order, and the goal flags must agree.
    """
    records = [r for s in ps.SEASONS for r in ps.load_records(s)]
    shots = xg.shot_table(records)
    model = xg.train_xg(shots, xg_seasons)
    sub = shots[shots["season"].isin(train_seasons)].copy()
    sub["shot_idx"] = sub.groupby("game_id").cumcount()
    sub = xg.score_shots(sub, model)[["game_id", "shot_idx", "xg", "goal"]]
    ev = ev.merge(sub, on=["game_id", "shot_idx"], how="left")
    has = ev["shot_idx"] >= 0
    matched = has & ev["xg"].notna()
    agree = (ev.loc[matched, "outcome"] == "goal").astype(int) == ev.loc[matched, "goal"]
    info = {"unblocked_with_xy": int(has.sum()), "matched": int(matched.sum()),
            "goal_flag_agree": float(agree.mean()) if len(agree) else None,
            "xg_trained_on": list(xg_seasons)}
    if info["matched"] < 0.999 * info["unblocked_with_xy"] or (agree.mean() < 0.999):
        raise ValueError(f"xG join failed: {info}")
    ev["bin"] = np.where(matched, ev["xg"].fillna(0).map(xg_bin), "")
    return ev.drop(columns=["goal"]), info


def start_manpower(ev: pd.DataFrame) -> pd.Series:
    """Manpower at the *start* of the interval that follows each event.

    An event's own situationCode describes the ice while it happened, which is also the
    ice right after it, except for events that change manpower (a penalty, a goal, a
    period start); for those, the next event's code (usually a faceoff at the same second)
    is used. The chain is keyed on start-of-interval manpower (2026-09-26: keying on the
    code at the *end* of the interval relabelled long power-play intervals as even
    strength, which made power-play events too quick in the in-sample check).
    """
    nxt = ev.groupby("game_id", sort=False)["mp"].shift(-1)
    change = ev["kind"].isin(["PENALTY", "PSTART"]) | (ev["outcome"] == "goal")
    return pd.Series(np.where(change & nxt.notna(), nxt, ev["mp"]), index=ev.index)


def transition_frame(ev: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(transitions, censored intervals) from an event frame marked by
    `timing.mark_expiries`.

    A transition p -> n inside a period is observed from its uncensored start: p's time,
    or the last penalty expiry inside the interval, in which case its manpower is the
    post-expiry one (n's own code). `mp` is that start-of-interval manpower; `p_mp` is the
    previous interval's. The censored frame holds (cls, mp, dur) for intervals cut by a
    penalty expiry or a period end, which is exactly where the simulator cuts them.
    """
    ev = ev.reset_index(drop=True)
    ev["smp"] = start_manpower(ev)
    grp = ev.groupby("game_id", sort=False)
    prev = grp.shift(1)
    prev2_smp = grp["smp"].shift(2)
    same = prev["kind"].notna() & (prev["kind"] != "END") & (prev["period"] == ev["period"])
    keep = same & ~ev["kind"].isin(["PSTART", "END"])
    straddle = ev["cens"].notna()
    to_end = same & (ev["kind"] == "END")
    cut = (keep & straddle) | to_end
    cens = pd.DataFrame({
        "cls": prev.loc[cut, "cls"].to_numpy(),
        "mp": prev.loc[cut, "smp"].to_numpy(),
        "dur": np.where(straddle[cut], ev.loc[cut, "cens"],
                        ev.loc[cut, "t"] - prev.loc[cut, "t"]),
    })
    p, n = prev[keep], ev[keep]
    start_mp = np.where(straddle[keep], n["mp"], p["smp"])
    diff = (n["hs"] - n["as_"]).to_numpy()
    p_tin = p["tin"].to_numpy()
    p_per = p["period"].to_numpy().astype(int)
    ph = np.where(p_per >= 4, "OT", np.where(p_per == 3, np.where(1200 - p_tin <= 300, "P3L", "P3"), "P12"))
    out = pd.DataFrame({
        "game_id": n["game_id"].to_numpy(), "season": n["season"].to_numpy(),
        "cls": p["cls"].to_numpy(), "actor": p["actor"].to_numpy(),
        "zone": p["zone"].to_numpy(), "p_kind": p["kind"].to_numpy(),
        "p_mp": prev2_smp[keep].fillna("EV5").to_numpy(),
        "p_diff": (p["hs"] - p["as_"]).to_numpy(),
        "p_tin": p_tin, "p_period": p_per,
        "mp": start_mp, "n_mp": n["mp"].to_numpy(), "diff": diff,
        "sb": [str(ps.score_bucket(int(d))) for d in diff], "ph": ph,
        "dwell": (n["t"] - n["post_t"]).to_numpy(),
        "straddle": straddle[keep].to_numpy(),
        "kind": n["kind"].to_numpy(), "n_actor": n["actor"].to_numpy(),
        "n_zone": n["zone"].to_numpy(), "outcome": n["outcome"].fillna("").to_numpy(),
        "bin": n["bin"].fillna("").to_numpy() if "bin" in n else "",
        "pen_cat": n["pen_cat"].fillna("").to_numpy(),
    })
    out["out_a"] = out["kind"] + "|" + out["n_actor"] + "|" + out["n_zone"]
    return out, cens


def shooter_frame(tf: pd.DataFrame) -> pd.DataFrame:
    a = tf[(tf["kind"] == "ATTEMPT") & tf["n_actor"].isin(["H", "A"])].copy()
    a["smp"] = [ps.shooter_mp(m, s) for m, s in zip(a["mp"], a["n_actor"])]
    sign = np.where(a["n_actor"] == "H", 1, -1)
    a["sbs"] = [str(ps.score_bucket(int(d))) for d in a["diff"].to_numpy() * sign]
    a["reb"] = np.where(a["cls"].isin(["SHOT_SAVED", "SHOT_MISSED", "SHOT_BLOCKED"])
                        & (a["actor"] == a["n_actor"]) & (a["dwell"] <= REBOUND_SECS), "1", "0")
    a["blk"] = np.where(a["outcome"] == "blocked", "B", "U")
    return a


@dataclass
class Transitions:
    stage_a: BackoffTable
    block: BackoffTable
    xbin: BackoffTable
    result: BackoffTable
    pen: BackoffTable
    info: dict = field(default_factory=dict)


def fit_transitions(tf: pd.DataFrame, alpha: float = ALPHA) -> Transitions:
    for c in ("cls", "actor", "mp", "sb", "zone", "ph"):
        tf[c] = tf[c].astype(str)
    stage_a = BackoffTable(STAGE_A_OUTCOMES, STAGE_A_LEVELS, alpha).fit(tf, "out_a")
    sh = shooter_frame(tf)
    block = BackoffTable(("B", "U"), BLOCK_LEVELS, alpha).fit(sh, "blk")
    unb = sh[(sh["blk"] == "U") & (sh["bin"] != "")]
    xbin = BackoffTable(XG_BINS, BIN_LEVELS, alpha).fit(unb, "bin")
    result = BackoffTable(("missed", "saved", "goal"), RESULT_LEVELS, alpha).fit(unb, "outcome")
    pens = tf[tf["kind"] == "PENALTY"].rename(columns={"actor": "p_actor"})
    pens = pens.assign(actor=pens["n_actor"])
    pen = BackoffTable(ps.PEN_CATS, PEN_LEVELS, alpha).fit(pens, "pen_cat")
    info = {"transitions": len(tf), "attempts": len(sh), "unblocked_binned": len(unb),
            "penalties": len(pens), "stage_a_cells": stage_a.counts,
            "bin_shares": unb["bin"].value_counts(normalize=True).sort_index().round(4).to_dict(),
            "goal_rate_by_bin": unb.groupby("bin")["outcome"].apply(
                lambda s: round(float((s == "goal").mean()), 4)).to_dict()}
    log.info("transitions fitted: %s", info)
    return Transitions(stage_a, block, xbin, result, pen, info)
