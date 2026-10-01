"""Game loop for the semi-Markov chain (SPEC-markov-simulator.md sections 2 and 7).

Gate A version: league-only, regular season. Each step:

1. Set the trailing team's goalie (3rd period only) from the pull model.
2. Sample the next event, actor and zone (Stage A), then its dwell time given that
   transition. If a penalty expires or the period ends first, jump there, apply it and
   draw again from the same last event. The data is cut at the same points
   (`timing.mark_expiries`), and the dwell tables treat those cuts as censoring.
3. An attempt goes through Stage B (blocked? -> xG bin -> missed / saved / goal).
4. Update score, penalty clock and state.

Ties after regulation get 5:00 of 3v3 sudden death, then a shootout won by the home team
at the training seasons' base rate. Every run takes a fixed seed.
"""

from __future__ import annotations

import logging
import random
from collections import Counter
from dataclasses import dataclass, field

import pandas as pd

from src.features import pbp_states as ps
from src.models.markov import timing
from src.models.markov.transitions import (REBOUND_SECS, Transitions, attach_xg,
                                           fit_transitions, shooter_frame, transition_frame)

REG_OT_LEN = 300
log = logging.getLogger("markov.simulate")


@dataclass
class ChainModel:
    trans: Transitions
    dwell: timing.DwellTable
    pull: timing.PullModel
    p_so_home: float
    info: dict = field(default_factory=dict)
    train_diag: Counter = field(default_factory=Counter)


def fit_chain(train_seasons: tuple[int, ...], xg_seasons: tuple[int, ...]) -> ChainModel:
    """League chain from the regular-season events of `train_seasons`.

    `xg_seasons` trains the shot model used for the bins; for walk-forward use it must
    hold only seasons before the season being simulated.
    """
    ev = pd.concat([ps.build_events(s)[0] for s in train_seasons], ignore_index=True)
    ev, xg_info = attach_xg(ev, train_seasons, xg_seasons)
    tf, cens = transition_frame(timing.mark_expiries(ev))
    trans = fit_transitions(tf)
    dwell = timing.DwellTable().fit(tf, cens)
    pull = timing.PullModel().fit(tf)
    so = [r for s in train_seasons for r in ps.load_records(s)
          if r.get("game_type") == ps.REGULAR and r.get("outcome") == "SO"]
    p_so = sum(r["winner"] == r["home_abbrev"] for r in so) / len(so)
    info = {"train_seasons": list(train_seasons), "xg": xg_info, "transitions": trans.info,
            "pull_rows": pull.n, "shootouts": len(so), "p_so_home": round(p_so, 4),
            "train_games": int(ev["game_id"].nunique()),
            "censored_intervals": len(cens), "straddling_expiry": int(tf["straddle"].sum())}
    return ChainModel(trans, dwell, pull, p_so, info, observed_diag(tf, cens))


def simulate_game(m: ChainModel, rng: random.Random, diag: Counter | None = None) -> dict:
    """One regular-season game; returns the same summary fields as `ps.summarise`.

    `diag`, if given, accumulates seconds by manpower and attempts / outcomes by the
    shooter's manpower (a diagnostic, not a gated metric).
    """
    s = ps.empty_summary()
    tr = m.trans
    rand = rng.random
    hs = as_ = 0
    clock = timing.PenaltyClock()
    period, start, length = 1, 0.0, float(ps.REG_LEN)
    t = 0.0
    cls, actor, zone = "PSTART", "N", "N"
    pulled = {"H": False, "A": False}
    home_so = False
    while True:
        tin = t - start
        ot = period >= 4
        ph = "OT" if ot else ("P12" if period < 3 else ("P3L" if length - tin <= 300 else "P3"))
        diff = hs - as_
        if period == 3 and diff != 0:
            trail = "H" if diff < 0 else "A"
            lead = "A" if trail == "H" else "H"
            pulled[lead] = False
            pulled[trail] = rand() < m.pull.p_out(pulled[trail], abs(diff), length - tin, cls)
        else:
            pulled["H"] = pulled["A"] = False
        h_sk, a_sk = clock.skaters(ot)
        mp = timing.manpower_category(h_sk, a_sk, pulled["H"], pulled["A"])
        sb = str(ps.score_bucket(diff))
        kind, act, z = tr.stage_a.sample((cls, actor, mp, sb, zone, ph), rand()).split("|")
        dwell = m.dwell.sample(cls, kind, timing.relation(actor, act), mp, rand())
        period_end = start + length
        boundary = min(period_end, clock.next_expiry())
        if t + dwell >= boundary:
            if diag is not None:
                diag["secs_" + mp] += boundary - t
            t = boundary
            clock.expire(t)
            if t < period_end - 1e-9:
                continue          # a penalty ran out: redraw from the same last event
            if period >= 3 and hs != as_:
                break
            if ot:
                s["so"] = 1
                home_so = rand() < m.p_so_home
                break
            if period == 3:
                s["ot"] = 1
            period += 1
            start, length = t, float(REG_OT_LEN if period >= 4 else ps.REG_LEN)
            cls, actor, zone = "PSTART", "N", "N"
            pulled["H"] = pulled["A"] = False
            continue
        t += dwell
        h_now, a_now, mp_now = h_sk, a_sk, mp
        if diag is not None:
            diag["secs_" + mp] += dwell
            diag["n_" + kind] += 1
        if kind == "ATTEMPT":
            if act not in ("H", "A"):
                continue
            smp = ps.shooter_mp(mp, act)
            sbs = str(ps.score_bucket(diff if act == "H" else -diff))
            reb = "1" if (cls in ("SHOT_SAVED", "SHOT_MISSED", "SHOT_BLOCKED")
                          and actor == act and dwell <= REBOUND_SECS) else "0"
            if diag is not None and reb == "1":
                diag[f"reb_{smp}"] += 1
            if tr.block.sample((smp, sbs, ph), rand()) == "B":
                out = "blocked"
            else:
                b = tr.xbin.sample((smp, reb, cls), rand())
                out = tr.result.sample((b, smp), rand())
            ps.tally_attempt(s, act, mp_now, diff, out)
            if diag is not None:
                diag[f"att_{smp}"] += 1
                diag[f"{out}_{smp}"] += 1
                if out == "goal":
                    diag[f"goal_{smp}>{ps.shooter_mp(mp_now, act)}"] += 1
            cls, actor, zone = ps.CLASS_OF_OUTCOME[out], act, z
            if out == "goal":
                if not (pulled["H"] or pulled["A"]):
                    clock.release_on_goal(act, t, h_now, a_now)
                if act == "H":
                    hs += 1
                else:
                    as_ += 1
                if ot:
                    break
                pulled["H"] = pulled["A"] = False
        elif kind == "PENALTY":
            cat = tr.pen.sample((act, mp), rand())
            ps.tally_penalty(s, act, cat)
            clock.add(act, cat, t, ot)
            cls, actor, zone = "PENALTY", act, z
        else:
            cls, actor, zone = kind, act, z
    s["home_win"] = int(hs > as_ or (hs == as_ and home_so))
    return s


def simulate_league(m: ChainModel, n_games: int, seed: int,
                    diag: Counter | None = None) -> list[dict]:
    rng = random.Random(seed)
    return [simulate_game(m, rng, diag) for _ in range(n_games)]


def observed_diag(tf: pd.DataFrame, cens: pd.DataFrame) -> Counter:
    """The same diagnostic counters as `simulate_game(diag=...)`, from real transitions."""
    d: Counter = Counter()
    for frame, col in ((tf, "dwell"), (cens, "dur")):
        for mp, secs in frame.groupby("mp")[col].sum().items():
            d["secs_" + mp] += float(secs)
    for kind, n in tf["kind"].value_counts().items():
        d["n_" + kind] += int(n)
    att = tf[(tf["kind"] == "ATTEMPT") & tf["n_actor"].isin(["H", "A"])]
    smp = [ps.shooter_mp(m, s) for m, s in zip(att["mp"], att["n_actor"])]
    sh = shooter_frame(tf)
    for s_, n in sh[sh["reb"] == "1"]["smp"].value_counts().items():
        d[f"reb_{s_}"] += int(n)
    now = [ps.shooter_mp(m, s) for m, s in zip(att["n_mp"], att["n_actor"])]
    for s_, o, w in zip(smp, att["outcome"], now):
        d[f"att_{s_}"] += 1
        d[f"{o}_{s_}"] += 1
        if o == "goal":
            d[f"goal_{s_}>{w}"] += 1
    return d
