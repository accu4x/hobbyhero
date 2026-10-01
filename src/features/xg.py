"""Expected goals (xG) and goalie GSAx from the raw NHL API play-by-play (2026-09-24).

1. `shot_table`: every unblocked shot attempt (shot on goal, missed shot, goal)
   outside the shootout, with distance and angle to the attacked net, shot type,
   strength state, empty net, rebound and time since the previous event.
2. `train_xg`: a gradient-boosted classifier P(goal | shot), trained **only on
   seasons before the test window** (2023-24 and 2024-25) so the 2025-26 test
   games never inform their own shot values.
3. `game_xg`: per game and team, xG for and against (all situations and 5v5), and
   per goalie the xG he faced and the goals he allowed (empty-net shots excluded).
4. `xg_layers`: leak-free features read before each game: recency-weighted xG share,
   xG for/against per game, 5v5 xG share, finishing (goals minus xG), and the
   starting goalie's goals saved above expected (GSAx) per expected goal, shrunk.

Rink geometry: nets at x = +/-89 ft. `homeTeamDefendingSide` says which net the
home team defends in that period; each team attacks the other one.
"""

from __future__ import annotations

import gzip
import json
import logging
import math
import pickle
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

from src.features.extra import EW, DecaySum, _boxscore, _ordered

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw" / "nhl_api" / "raw"
PROCESSED = ROOT / "data" / "processed"
MODELS = ROOT / "data" / "models"
NET_X = 89.0
UNBLOCKED = {"shot-on-goal", "missed-shot", "goal"}
SHOT_EVENTS = UNBLOCKED | {"blocked-shot"}
SHOT_TYPES = ("wrist", "snap", "slap", "backhand", "tip-in", "deflected", "wrap-around",
              "poke", "bat", "between-legs", "cradle")
FEATURES = ["dist", "angle", "strength", "empty_net", "rebound", "since_prev",
            "prev_faceoff", "prev_other_zone", "period"] + [f"st_{s}" for s in SHOT_TYPES]
XG_TRAIN_SEASONS = (20232024, 20242025)
GSAX_PRIOR_XG = 20.0

log = logging.getLogger("xg")


def _secs(t: str) -> int:
    m, s = (t or "0:0").split(":")
    return int(m) * 60 + int(s)


def _pbp(rec: dict) -> dict | None:
    path = RAW / str(rec["season"]) / str(rec["game_id"]) / "play-by-play.json.gz"
    if not path.exists():
        return None
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def shots_for_game(rec: dict) -> list[dict]:
    pbp = _pbp(rec)
    if pbp is None:
        return []
    home_id = pbp["homeTeam"]["id"]
    ids = {pbp["homeTeam"]["id"]: rec["home_abbrev"], pbp["awayTeam"]["id"]: rec["away_abbrev"]}
    out, prev = [], None
    for play in pbp.get("plays") or []:
        pd_ = play.get("periodDescriptor") or {}
        if pd_.get("periodType") == "SO":
            break
        kind = play.get("typeDescKey")
        t = (pd_.get("number", 0), _secs(play.get("timeInPeriod")))
        det = play.get("details") or {}
        if kind in UNBLOCKED and det.get("xCoord") is not None:
            owner = det.get("eventOwnerTeamId")
            is_home = owner == home_id
            home_def = play.get("homeTeamDefendingSide", "left")
            attack_right = (home_def == "left") == is_home
            net_x = NET_X if attack_right else -NET_X
            x, y = float(det["xCoord"]), float(det.get("yCoord") or 0)
            dx = abs(net_x - x)
            sc = play.get("situationCode") or "1551"
            away_g, away_s, home_s, home_g = (int(c) for c in sc) if len(sc) == 4 else (1, 5, 5, 1)
            own_s, opp_s = (home_s, away_s) if is_home else (away_s, home_s)
            opp_g = away_g if is_home else home_g
            since = (t[1] - prev["t"][1]) if prev and prev["t"][0] == t[0] else 99
            row = {
                "game_id": rec["game_id"], "season": rec["season"], "team": ids.get(owner, ""),
                "is_home": int(is_home), "goal": int(kind == "goal"),
                "goalie": det.get("goalieInNetId"),
                "dist": math.hypot(dx, y), "angle": math.degrees(math.atan2(abs(y), dx)),
                "strength": own_s - opp_s, "empty_net": int(opp_g == 0),
                "five_v_five": int(sc == "1551"),
                "rebound": int(bool(prev) and prev["kind"] in SHOT_EVENTS
                               and prev["team"] == owner and since <= 3),
                "since_prev": min(since, 99),
                "prev_faceoff": int(bool(prev) and prev["kind"] == "faceoff" and since <= 5),
                "prev_other_zone": int(bool(prev) and prev["zone"] in ("N", "D")
                                       and prev["team"] == owner and since <= 4),
                "period": min(pd_.get("number", 1), 4),
            }
            st = det.get("shotType", "")
            for s in SHOT_TYPES:
                row[f"st_{s}"] = int(st == s)
            out.append(row)
        if kind not in ("stoppage", "period-start", "period-end", "game-end"):
            prev = {"kind": kind, "team": det.get("eventOwnerTeamId"), "t": t,
                    "zone": det.get("zoneCode")}
    return out


def shot_table(records: list[dict]) -> pd.DataFrame:
    PROCESSED.mkdir(parents=True, exist_ok=True)
    cache = PROCESSED / "shots_nhl_api.csv.gz"
    ids = sorted(r["game_id"] for r in records)
    if cache.exists():
        df = pd.read_csv(cache)
        if set(ids) <= set(df["game_id"].unique()):
            return df[df["game_id"].isin(ids)]
    rows = [s for rec in _ordered(records) for s in shots_for_game(rec)]
    df = pd.DataFrame(rows)
    df.to_csv(cache, index=False)
    log.info("shot table: %d unblocked shots from %d games -> %s", len(df), len(ids), cache)
    return df


def train_xg(shots: pd.DataFrame,
             seasons: tuple[int, ...] = XG_TRAIN_SEASONS) -> HistGradientBoostingClassifier:
    train = shots[shots["season"].isin(seasons)]
    model = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_depth=5,
                                           min_samples_leaf=100, l2_regularization=1.0,
                                           random_state=42)
    model.fit(train[FEATURES], train["goal"])
    MODELS.mkdir(parents=True, exist_ok=True)
    name = "xg_v1.pkl" if seasons == XG_TRAIN_SEASONS else f"xg_v1_{'_'.join(map(str, seasons))}.pkl"
    with (MODELS / name).open("wb") as fh:
        pickle.dump(model, fh)
    p = model.predict_proba(train[FEATURES])[:, 1]
    log.info("xG trained on %d shots (%s): goals %d, xG %.0f", len(train), seasons,
             train["goal"].sum(), p.sum())
    return model


def score_shots(shots: pd.DataFrame, model: HistGradientBoostingClassifier) -> pd.DataFrame:
    shots = shots.copy()
    shots["xg"] = model.predict_proba(shots[FEATURES])[:, 1]
    return shots


def game_xg(shots: pd.DataFrame) -> tuple[dict, dict]:
    """(game_id, team) -> xG totals, and (game_id, goalie) -> faced xG / goals."""
    team: dict = defaultdict(lambda: defaultdict(float))
    goalie: dict = defaultdict(lambda: defaultdict(float))
    for r in shots.itertuples(index=False):
        t = team[(r.game_id, r.team)]
        t["xgf"] += r.xg
        t["gf_shots"] += r.goal
        if r.five_v_five:
            t["xgf5"] += r.xg
        if not r.empty_net and r.goalie == r.goalie and r.goalie:
            g = goalie[(r.game_id, int(r.goalie))]
            g["xga"] += r.xg
            g["ga"] += r.goal
    return team, goalie


def xg_layers(records: list[dict], shots: pd.DataFrame,
              state_out: dict | None = None, daily_out: dict | None = None,
              team_rows_out: list | None = None) -> pd.DataFrame:
    """Per-game deltas. If `state_out` is given, it receives the end state: per-team xG
    values, per-goalie GSAx, and every pre-game team value (for lever ranges).

    Optional hooks (artifact v0.2, 2026-09-24; default behaviour unchanged):
      daily_out[date] = state on the morning of each game date (before its games), plus
        daily_out["end"] after the last game: {"teams": {team: values}, "gsax": {pid: v}}.
      team_rows_out gets one row per team per game with its pre-game values and starter."""
    team_x, goalie_x = game_xg(shots)
    goalies_by_game: dict[int, list] = defaultdict(list)
    for (g_gid, pid), g in goalie_x.items():
        goalies_by_game[g_gid].append((pid, g))
    ew = defaultdict(lambda: {(s, h): EW(h) for s in ("xg_share", "xgf", "xga", "xg5_share",
                                                        "finish") for h in (10, 40)})
    g_xga: dict[int, DecaySum] = defaultdict(lambda: DecaySum(20))
    g_ga: dict[int, DecaySum] = defaultdict(lambda: DecaySum(20))
    rows = []

    def _snap() -> dict:
        return {"teams": {t: {f"xg_{s}_h{hl}": e.mean() for (s, hl), e in st.items()}
                          for t, st in ew.items()},
                "gsax": {pid: (g_xga[pid].total - g_ga[pid].total)
                         / (g_xga[pid].total + GSAX_PRIOR_XG) for pid in g_xga}}

    cur_date = None
    for rec in _ordered(records):
        gid, h, a = rec["game_id"], rec["home_abbrev"], rec["away_abbrev"]
        if daily_out is not None and str(rec["date"]) != cur_date:
            cur_date = str(rec["date"])
            daily_out[cur_date] = _snap()
        box = _boxscore(rec)
        starters = {}
        if box:
            for side, t in (("homeTeam", h), ("awayTeam", a)):
                st = next((g for g in box["playerByGameStats"][side]["goalies"]
                           if g.get("starter")), None)
                starters[t] = st["playerId"] if st else None
        f = {}
        for t in (h, a):
            v = {f"xg_{s}_h{hl}": e.mean() for (s, hl), e in ew[t].items()}
            if state_out is not None:
                state_out.setdefault("history", []).append(dict(v))
            sid = starters.get(t)
            v["g_gsax"] = ((g_xga[sid].total - g_ga[sid].total) / (g_xga[sid].total + GSAX_PRIOR_XG)
                           if sid else None)
            f[t] = v
            if team_rows_out is not None:
                team_rows_out.append({"game": str(gid), "team": t, "starter": sid, **v})
        row = {"game": str(gid)}
        for k in f[h]:
            hv, av = f[h][k], f[a][k]
            row[f"{k}_delta"] = (hv - av) if hv is not None and av is not None else None
        rows.append(row)
        # update after reading
        for t, o in ((h, a), (a, h)):
            tx, ox = team_x.get((gid, t), {}), team_x.get((gid, o), {})
            xgf, xga = tx.get("xgf", 0.0), ox.get("xgf", 0.0)
            xgf5, xga5 = tx.get("xgf5", 0.0), ox.get("xgf5", 0.0)
            stats = {"xg_share": xgf / (xgf + xga) if xgf + xga else None, "xgf": xgf,
                     "xga": xga, "xg5_share": xgf5 / (xgf5 + xga5) if xgf5 + xga5 else None,
                     "finish": rec["teams"][t]["goals"] - xgf}
            for (s, hl), e in ew[t].items():
                e.add(stats[s])
        for pid, g in goalies_by_game.get(gid, []):
            g_xga[pid].add(g["xga"])
            g_ga[pid].add(g["ga"])
    if daily_out is not None:
        daily_out["end"] = _snap()
    if state_out is not None:
        state_out["teams"] = {t: {f"xg_{s}_h{hl}": e.mean() for (s, hl), e in st.items()}
                              for t, st in ew.items()}
        state_out["gsax"] = {pid: (g_xga[pid].total - g_ga[pid].total)
                             / (g_xga[pid].total + GSAX_PRIOR_XG) for pid in g_xga}
    return pd.DataFrame(rows)
