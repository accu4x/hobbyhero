"""Time model for the semi-Markov chain (SPEC-markov-simulator.md section 6).

- `PenaltyClock`: the rules. Minors expire after 2:00 or on a power-play goal against;
  a double minor's first half ends on such a goal; majors run the full 5:00; coincidental
  minors at full strength give 4v4, otherwise nothing changes; in 3v3 overtime each net
  penalty adds a skater to the other side (4v3, then 5v3).
- `mark_expiries`: replays the same clock over the real event stream, so the data can be
  cut exactly where the simulator cuts: at a penalty expiry or a period end.
- `DwellTable`: time to the next event given the transition (last class, next event,
  same/opposite actor, coarse manpower). Intervals cut by an expiry or a period end are
  right-censored, and the per-transition dwell distributions are Aalen-Johansen
  cumulative incidences, so censoring doesn't make power plays look quicker than they
  are (2026-09-26 in-sample finding).
- `PullModel`: the trailing team's goalie pull in the 3rd period, as the probability that
  its goalie is off for the next interval given whether it was off for the last one, the
  deficit, the time left and whether the puck was dead. Fitted from the data.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from src.features import pbp_states as ps
from src.models.markov.transitions import ALPHA, BackoffTable

MIN_DWELL_OBS = 50
PULL_TIME_EDGES = (30, 60, 90, 120, 150, 180, 240, 300, 600)   # seconds left in P3
PULL_LEVELS = [("prev", "deficit", "tb", "pc"), ("prev", "deficit", "tb"),
               ("prev", "tb"), ("prev",)]
DEAD = {"STOPPAGE", "PENALTY", "GOAL", "PSTART"}


def mp_coarse(mp: str) -> str:
    if mp.startswith("EV"):
        return mp
    return "EN" if mp.endswith("EN") else "PP"


def relation(last_actor: str, next_actor: str) -> str:
    if last_actor == "N" or next_actor == "N":
        return "none"
    return "same" if last_actor == next_actor else "opp"


# ---------------------------------------------------------------- penalty clock
class PenaltyClock:
    """Active penalties per side as [kind, expiry], kind in m (minor), d (double minor),
    M (major), c (coincidental minor)."""

    def __init__(self) -> None:
        self.active: dict[str, list[list]] = {"H": [], "A": []}

    def add(self, actor: str, cat: str, t: float, overtime: bool) -> None:
        if cat == "COINC":
            if not overtime and not self.active["H"] and not self.active["A"]:
                self.active["H"].append(["c", t + 120])
                self.active["A"].append(["c", t + 120])
            return
        if actor not in ("H", "A"):
            return
        box = self.active[actor]
        if cat == "MINOR":
            box.append(["m", t + 120])
        elif cat == "TWO_MINOR":
            box.extend([["m", t + 120], ["m", t + 120]])
        elif cat == "DOUBLE":
            box.append(["d", t + 240])
        elif cat == "MAJOR":
            box.append(["M", t + 300])

    def skaters(self, overtime: bool) -> tuple[int, int]:
        nh, na = len(self.active["H"]), len(self.active["A"])
        if not overtime:
            return max(3, 5 - nh), max(3, 5 - na)
        net = na - nh
        return min(5, 3 + max(0, net)), min(5, 3 + max(0, -net))

    def next_expiry(self) -> float:
        times = [e[1] for box in self.active.values() for e in box]
        return min(times) if times else math.inf

    def expire(self, t: float) -> None:
        for side in self.active:
            self.active[side] = [e for e in self.active[side] if e[1] > t + 1e-9]

    def release_on_goal(self, scorer: str, t: float, h_sk: int, a_sk: int) -> None:
        """A power-play goal ends the earliest-expiring releasable minor of the other side."""
        mine, theirs = (h_sk, a_sk) if scorer == "H" else (a_sk, h_sk)
        if mine <= theirs:
            return
        box = self.active["A" if scorer == "H" else "H"]
        cands = [e for e in box if e[0] in ("m", "d")]
        if not cands:
            return
        e = min(cands, key=lambda x: x[1])
        if e[0] == "d" and e[1] - t > 120:
            e[1] = t + 120
        else:
            box.remove(e)


def manpower_category(h_sk: int, a_sk: int, h_pulled: bool, a_pulled: bool) -> str:
    if h_pulled:
        return "HEN"
    if a_pulled:
        return "AEN"
    d = h_sk - a_sk
    if d == 0:
        return "EV5" if h_sk >= 5 else ("EV4" if h_sk == 4 else "EV3")
    if d == 1:
        return "HPP"
    if d >= 2:
        return "HPP2"
    return "APP" if d == -1 else "APP2"


def mark_expiries(ev: pd.DataFrame) -> pd.DataFrame:
    """Add `cens` (seconds from the previous event to the first penalty expiry inside the
    interval ending at this row, else NaN) and `post_t` (when the uncensored part of the
    interval starts: the last expiry inside it, else the previous event's time)."""
    cens = np.full(len(ev), np.nan)
    post = ev["t"].to_numpy(dtype=float).copy()
    games = ev["game_id"].to_numpy()
    kinds = ev["kind"].to_numpy()
    ts = ev["t"].to_numpy(dtype=float)
    actors = ev["actor"].to_numpy()
    cats = ev["pen_cat"].fillna("").to_numpy()
    outs = ev["outcome"].fillna("").to_numpy()
    mps = ev["mp"].to_numpy()
    periods = ev["period"].to_numpy()
    clock = PenaltyClock()
    for i in range(len(ev)):
        if i == 0 or games[i] != games[i - 1]:
            clock = PenaltyClock()
            continue
        t_prev, t = ts[i - 1], ts[i]
        post[i] = t_prev
        e = clock.next_expiry()
        if t_prev < e < t:
            cens[i] = e - t_prev
            while clock.next_expiry() < t:
                post[i] = clock.next_expiry()
                clock.expire(post[i])
        clock.expire(t)
        ot = periods[i] >= 4
        if kinds[i] == "PENALTY":
            clock.add(actors[i], cats[i], t, ot)
        elif outs[i] == "goal" and actors[i] in ("H", "A"):
            if ps.shooter_mp(mps[i], actors[i]) in ("PP", "PP2"):
                h, a = clock.skaters(ot)
                clock.release_on_goal(actors[i], t, h, a)
    return ev.assign(cens=cens, post_t=post)


# ---------------------------------------------------------------- dwell times
def aalen_johansen(t_event: np.ndarray, cause: np.ndarray, n_causes: int,
                   t_cens: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Cumulative-incidence increments per cause at each distinct time.

    Returns (times, inc) with inc[:, c] the increment of cause c's CIF; events at a
    time are counted before censorings at that time.
    """
    all_t = np.concatenate([t_event, t_cens])
    times = np.unique(all_t)
    ie = np.searchsorted(times, t_event)
    ic = np.searchsorted(times, t_cens)
    d = np.zeros((len(times), n_causes))
    np.add.at(d, (ie, cause), 1.0)
    c = np.bincount(ic, minlength=len(times)).astype(float)
    d_tot = d.sum(axis=1)
    removed = d_tot + c
    at_risk = len(all_t) - np.concatenate([[0.0], np.cumsum(removed)[:-1]])
    haz = np.divide(d_tot, at_risk, out=np.zeros_like(d_tot), where=at_risk > 0)
    s_prev = np.concatenate([[1.0], np.cumprod(1.0 - haz)[:-1]])
    inc = s_prev[:, None] * np.divide(d, at_risk[:, None], out=np.zeros_like(d),
                                      where=at_risk[:, None] > 0)
    return times, inc


class DwellTable:
    """Dwell given (last class, next kind, same/opp actor, coarse manpower).

    Risk sets are (last class, coarse manpower), with a (last class) fallback; a cause is
    (next kind, relation). Each cause's dwell distribution is its normalised cumulative
    incidence. Below MIN_DWELL_OBS events a cause backs off to the coarser risk set.
    """

    def __init__(self) -> None:
        self.dist: dict[tuple, tuple[np.ndarray, list[float], int]] = {}

    def fit(self, tf: pd.DataFrame, cens: pd.DataFrame) -> DwellTable:
        d = tf.assign(mpc=tf["mp"].map(mp_coarse),
                      rel=[relation(a, b) for a, b in zip(tf["actor"], tf["n_actor"])])
        d["cause"] = d["kind"] + "|" + d["rel"]
        c = cens.assign(mpc=cens["mp"].map(mp_coarse))
        causes = sorted(d["cause"].unique())
        cidx = {k: i for i, k in enumerate(causes)}
        for group_cols in (["cls", "mpc"], ["cls"]):
            cgroups = {k if isinstance(k, tuple) else (k,): g["dur"].to_numpy(dtype=float)
                       for k, g in c.groupby(group_cols)}
            for key, g in d.groupby(group_cols):
                key = key if isinstance(key, tuple) else (key,)
                te = g["dwell"].to_numpy(dtype=float)
                ce = g["cause"].map(cidx).to_numpy()
                times, inc = aalen_johansen(te, ce, len(causes),
                                            cgroups.get(key, np.empty(0)))
                counts = np.bincount(ce, minlength=len(causes))
                for k, i in cidx.items():
                    if counts[i] == 0:
                        continue
                    w = inc[:, i]
                    cum = (np.cumsum(w) / w.sum()).tolist()
                    cum[-1] = 1.0
                    self.dist[key + (k,)] = (times, cum, int(counts[i]))
                pooled = inc.sum(axis=1)
                cum = (np.cumsum(pooled) / pooled.sum()).tolist()
                cum[-1] = 1.0
                self.dist[key + ("*",)] = (times, cum, len(te))
        return self

    def sample(self, cls: str, kind: str, rel: str, mp: str, u: float) -> float:
        cause = f"{kind}|{rel}"
        for key in ((cls, mp_coarse(mp), cause), (cls, cause), (cls, "*")):
            hit = self.dist.get(key)
            if hit is not None and (hit[2] >= MIN_DWELL_OBS or key[-1] == "*"
                                    or len(key) == 2):
                times, cum, _ = hit
                return float(times[min(bisect.bisect_right(cum, u), len(cum) - 1)])
        raise KeyError((cls, kind, rel, mp))


# ---------------------------------------------------------------- goalie pull
def time_bucket(secs_left: float) -> str:
    return str(bisect.bisect_left(PULL_TIME_EDGES, secs_left))


def pull_context(cls: str) -> str:
    return "dead" if cls in DEAD else ("fo" if cls == "FACEOFF" else "live")


@dataclass
class PullModel:
    table: BackoffTable = field(default_factory=lambda: BackoffTable(("0", "1"), PULL_LEVELS, ALPHA))
    n: int = 0

    def fit(self, tf: pd.DataFrame) -> PullModel:
        d = tf[(tf["p_period"] == 3) & (tf["diff"] != 0)].copy()
        trail_home = d["diff"] < 0
        code = np.where(trail_home, "HEN", "AEN")
        d["now"] = np.where(d["mp"] == code, "1", "0")
        same_trail = np.sign(d["p_diff"]) == np.sign(d["diff"])
        d["prev"] = np.where((d["p_mp"] == code) & same_trail, "1", "0")
        d["deficit"] = d["diff"].abs().clip(upper=3).astype(int).astype(str)
        d["tb"] = [time_bucket(1200 - x) for x in d["p_tin"]]
        d["pc"] = d["cls"].map(pull_context)
        self.table.fit(d, "now")
        self.n = len(d)
        return self

    def p_out(self, prev: bool, deficit: int, secs_left: float, cls: str) -> float:
        key = ("1" if prev else "0", str(min(deficit, 3)), time_bucket(secs_left),
               pull_context(cls))
        return float(self.table.probs(key)[1])
