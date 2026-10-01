"""Extra feature layers and rating models on the NHL API data (2026-09-24).

Everything here walks the games in time order and emits, for each game, values
computed from **earlier games only** (state is read before the game updates it).
The one deliberate exception is the lineup: the starting goalie and the skaters
who dressed come from the game's own boxscore, on the assumption that lineups
and starters are known at puck drop (OPEN-ITEMS, 2026-09-24).

Layers (all home-minus-away deltas unless noted):
  rec_*    recency-weighted team form across seasons: exponential half-lives of
           10, 40 and 120 games for goal diff, wins, all-situation and 5v5 shot
           share, shooting %, save %, PP % and PK %. Last season counts, with
           less weight the further back it is (Dan, 2026-09-24).
  prev_*   the previous regular season's points % and goal diff per game.
  g_*      starting goalie: shrunk, recency-weighted save % and his share of the
           team's last 20 starts (a low share means a backup is in).
  miss_*   share of the team's usual ice time / points missing from the lineup.
  b2b_*    second night of a back-to-back (per team, and the delta).
Rating models (probabilities, not deltas): Elo and a Poisson goal model.
"""

from __future__ import annotations

import gzip
import json
import math
from collections import defaultdict, deque
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw" / "nhl_api" / "raw"
HALF_LIVES = (10, 40, 120)
# cfor / cagainst: shot attempts for and against per game (added 2026-09-24, Dan).
REC_STATS = ("gd", "win", "cf", "cf5", "sh", "sv", "pp", "pk", "cfor", "cagainst")
LEAGUE_SV = 0.900
SV_PRIOR_SHOTS = 200.0


def _ordered(records: list[dict]) -> list[dict]:
    return sorted(records, key=lambda r: (r["date"], r["game_id"]))


def _toi_minutes(toi: str) -> float:
    m, s = (toi or "0:0").split(":")
    return int(m) + int(s) / 60


def _ratio(a: float, b: float) -> float | None:
    return a / b if b else None


class DecaySum:
    """Exponentially decayed running sum (per appearance)."""

    def __init__(self, half_life: float) -> None:
        self._d = 0.5 ** (1 / half_life)
        self.total = 0.0

    def add(self, x: float) -> None:
        self.total = self.total * self._d + x


class EW:
    """Exponentially weighted mean with a half-life measured in games."""

    def __init__(self, half_life: float) -> None:
        self._d = 0.5 ** (1 / half_life)
        self._s = 0.0
        self._w = 0.0

    def add(self, x: float | None) -> None:
        self._s *= self._d
        self._w *= self._d
        if x is not None:
            self._s += x
            self._w += 1

    def mean(self) -> float | None:
        return self._s / self._w if self._w > 1e-9 else None


def _team_game_stats(t: dict, o: dict) -> dict[str, float | None]:
    return {
        "gd": t["goals"] - o["goals"],
        "win": t["won"],
        "cf": _ratio(t["sat_for"], t["sat_for"] + t["sat_against"]),
        "cf5": _ratio(t["corsi_5v5_for"], t["corsi_5v5_for"] + t["corsi_5v5_against"]),
        "sh": _ratio(t["goals"], t["shots"]),
        "sv": _ratio(t["saves"], t.get("goalie_shots_against") or t["shots_against"]),
        "pp": _ratio(t["pp_goals"], t["pp_opportunities"]),
        "pk": (1 - o["pp_goals"] / o["pp_opportunities"]) if o["pp_opportunities"] else None,
        "cfor": float(t["sat_for"]),
        "cagainst": float(t["sat_against"]),
    }


def _boxscore(rec: dict) -> dict | None:
    path = RAW / str(rec["season"]) / str(rec["game_id"]) / "boxscore.json.gz"
    if not path.exists():
        return None
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def build_layers(records: list[dict], state_out: dict | None = None,
                 daily_out: dict | None = None) -> pd.DataFrame:
    """Per-game deltas. If `state_out` is given, it receives the end state (after the last
    record): per-team recency values, recent starting goalies and goalie names.

    Optional hook (artifact v0.2, 2026-09-24; default behaviour unchanged):
      daily_out[date] = {"teams": {team: rec values}, "starts": {team: [goalie ids]}} on the
        morning of each game date, plus daily_out["end"] after the last game."""
    rec_state = defaultdict(lambda: {(s, h): EW(h) for s in REC_STATS for h in HALF_LIVES})
    season_tot: dict[tuple[int, str], list[float]] = defaultdict(lambda: [0, 0, 0, 0.0])
    last_date: dict[str, pd.Timestamp] = {}
    toi_hist: dict[str, deque] = defaultdict(lambda: deque(maxlen=10))
    pts_hist: dict[str, deque] = defaultdict(lambda: deque(maxlen=20))
    starts: dict[str, deque] = defaultdict(lambda: deque(maxlen=20))
    g_saves: dict[int, DecaySum] = defaultdict(lambda: DecaySum(10))
    g_sa: dict[int, DecaySum] = defaultdict(lambda: DecaySum(10))

    goalie_names: dict[int, str] = {}
    rows = []

    def _snap() -> dict:
        return {"teams": {t: {f"rec_{s}_h{h}": ew.mean() for (s, h), ew in st.items()}
                          for t, st in rec_state.items()},
                "starts": {t: list(q) for t, q in starts.items()}}

    cur_date = None
    for rec in _ordered(records):
        if daily_out is not None and str(rec["date"]) != cur_date:
            cur_date = str(rec["date"])
            daily_out[cur_date] = _snap()
        season, date = rec["season"], pd.Timestamp(rec["date"])
        home, away = rec["home_abbrev"], rec["away_abbrev"]
        box = _boxscore(rec)
        lineup = {}
        if box:
            pbg = box["playerByGameStats"]
            for side, team in (("homeTeam", home), ("awayTeam", away)):
                sk = pbg[side]["forwards"] + pbg[side]["defense"]
                starter = next((g for g in pbg[side]["goalies"] if g.get("starter")), None)
                lineup[team] = {
                    "toi": {p["playerId"]: _toi_minutes(p.get("toi")) for p in sk},
                    "pts": {p["playerId"]: int(p.get("points") or 0) for p in sk},
                    "starter": starter["playerId"] if starter else None,
                    "goalies": pbg[side]["goalies"],
                }
        row: dict = {"game": str(rec["game_id"])}
        feats = {}
        for team in (home, away):
            f: dict[str, float] = {}
            st = rec_state[team]
            for (s, h), ew in st.items():
                f[f"rec_{s}_h{h}"] = ew.mean()
            prev = season_tot.get((season - 10001, team))
            f["prev_pts"] = (prev[3] / (2 * prev[0])) if prev and prev[0] else None
            f["prev_gd"] = (prev[2] / prev[0]) if prev and prev[0] else None
            ld = last_date.get(team)
            f["b2b"] = 1.0 if ld is not None and (date - ld).days == 1 else 0.0
            lu = lineup.get(team)
            if lu:
                avg = defaultdict(float)
                for g in toi_hist[team]:
                    for pid, m in g.items():
                        avg[pid] += m / max(1, len(toi_hist[team]))
                top = sorted(avg.items(), key=lambda kv: kv[1], reverse=True)[:18]
                exp_mass = sum(m for _, m in top)
                f["miss_toi"] = (1 - sum(m for pid, m in top if pid in lu["toi"]) / exp_mass
                                 if exp_mass else None)
                tot = defaultdict(int)
                for g in pts_hist[team]:
                    for pid, p in g.items():
                        tot[pid] += p
                allp = sum(tot.values())
                f["miss_pts"] = (sum(p for pid, p in tot.items() if pid not in lu["toi"]) / allp
                                 if allp else None)
                sid = lu["starter"]
                if sid is not None:
                    # Recency-weighted save %, shrunk toward league average by 200 shots.
                    f["g_sv"] = ((g_saves[sid].total + LEAGUE_SV * SV_PRIOR_SHOTS)
                                 / (g_sa[sid].total + SV_PRIOR_SHOTS))
                    f["g_share"] = (sum(1 for x in starts[team] if x == sid) / len(starts[team])
                                    if starts[team] else None)
            feats[team] = f
        for k in feats[home].keys() | feats[away].keys():
            hv, av = feats[home].get(k), feats[away].get(k)
            row[f"{k}_delta"] = (hv - av) if hv is not None and av is not None else None
        row["b2b_home"], row["b2b_away"] = feats[home]["b2b"], feats[away]["b2b"]
        rows.append(row)

        # ---- update state with this game (after the features were read) ----
        t_h, t_a = rec["teams"][home], rec["teams"][away]
        for team, t, o in ((home, t_h, t_a), (away, t_a, t_h)):
            stats = _team_game_stats(t, o)
            for (s, h), ew in rec_state[team].items():
                ew.add(stats[s])
            last_date[team] = date
            if rec["game_type"] == 2:
                tot = season_tot[(season, team)]
                tot[0] += 1
                tot[2] += t["goals"] - o["goals"]
                tot[3] += 2 if t["won"] else (1 if rec["outcome"] != "REG" else 0)
            lu = lineup.get(team)
            if lu:
                toi_hist[team].append(lu["toi"])
                pts_hist[team].append(lu["pts"])
                if lu["starter"] is not None:
                    starts[team].append(lu["starter"])
                for g in lu["goalies"]:
                    goalie_names[g["playerId"]] = (g.get("name") or {}).get("default", "")
                    if g.get("shotsAgainst"):
                        g_saves[g["playerId"]].add(float(g.get("saves") or 0))
                        g_sa[g["playerId"]].add(float(g["shotsAgainst"]))
    if daily_out is not None:
        daily_out["end"] = _snap()
    if state_out is not None:
        state_out["teams"] = {t: {f"rec_{s}_h{h}": ew.mean() for (s, h), ew in st.items()}
                              for t, st in rec_state.items()}
        state_out["starts"] = {t: list(q) for t, q in starts.items()}
        state_out["goalie_names"] = goalie_names
    return pd.DataFrame(rows)


# --------------------------------------------------------------------- ratings


def elo(records: list[dict], k: float = 6.0, hfa: float = 35.0, revert: float = 0.7,
        mov: bool = True) -> pd.DataFrame:
    """Pre-game Elo home win probability per game (FiveThirtyEight-style)."""
    r: dict[str, float] = defaultdict(lambda: 1500.0)
    season_of: dict[str, int] = {}
    out = []
    for rec in _ordered(records):
        h, a, season = rec["home_abbrev"], rec["away_abbrev"], rec["season"]
        for t in (h, a):
            if season_of.get(t, season) != season:
                r[t] = revert * r[t] + (1 - revert) * 1500.0
            season_of[t] = season
        diff = r[h] + hfa - r[a]
        p = 1 / (1 + 10 ** (-diff / 400))
        out.append({"game": str(rec["game_id"]), "elo_p": p, "elo_diff": r[h] - r[a]})
        won = 1.0 if rec["winner"] == h else 0.0
        mult = 1.0
        if mov:
            margin = abs(rec["final_home"] - rec["final_away"])
            wdiff = diff if won else -diff
            mult = math.log(margin + 1) * 2.2 / (wdiff * 0.001 + 2.2)
        delta = k * mult * (won - p)
        r[h] += delta
        r[a] -= delta
    return pd.DataFrame(out)


def _pois(k: int, lam: float) -> float:
    return math.exp(-lam) * lam ** k / math.factorial(k)


def poisson(records: list[dict], half_life: float = 30.0, shrink: float = 10.0) -> pd.DataFrame:
    """Pre-game home win probability from team attack/defence goal rates."""
    gf: dict[str, EW] = defaultdict(lambda: EW(half_life))
    ga: dict[str, EW] = defaultdict(lambda: EW(half_life))
    n: dict[str, int] = defaultdict(int)
    lg_h, lg_a, tie_home = EW(400), EW(400), EW(400)
    out = []
    for rec in _ordered(records):
        h, a = rec["home_abbrev"], rec["away_abbrev"]
        mh, ma = lg_h.mean() or 3.1, lg_a.mean() or 2.9
        avg = (mh + ma) / 2

        def rate(ew: EW, team: str) -> float:
            v = ew.mean()
            x = (v / avg) if v is not None else 1.0
            return (n[team] * x + shrink) / (n[team] + shrink)

        lam_h = mh * rate(gf[h], h) * rate(ga[a], a)
        lam_a = ma * rate(gf[a], a) * rate(ga[h], h)
        ph = [_pois(i, lam_h) for i in range(12)]
        pa = [_pois(i, lam_a) for i in range(12)]
        p_win = sum(ph[i] * pa[j] for i in range(12) for j in range(12) if i > j)
        p_tie = sum(ph[i] * pa[i] for i in range(12))
        q = tie_home.mean() or 0.52
        out.append({"game": str(rec["game_id"]), "pois_p": p_win + p_tie * q,
                    "pois_lam_h": lam_h, "pois_lam_a": lam_a})
        th, ta = rec["teams"][h], rec["teams"][a]
        # Regulation goals: OT/SO winners' extra goal is not a scoring-rate signal.
        reg_h = th["goals"] - (1 if rec["outcome"] == "OT" and rec["winner"] == h else 0)
        reg_a = ta["goals"] - (1 if rec["outcome"] == "OT" and rec["winner"] == a else 0)
        gf[h].add(reg_h); ga[h].add(reg_a); gf[a].add(reg_a); ga[a].add(reg_h)
        n[h] += 1; n[a] += 1
        lg_h.add(reg_h); lg_a.add(reg_a)
        if rec["outcome"] != "REG":
            tie_home.add(1.0 if rec["winner"] == h else 0.0)
    return pd.DataFrame(out)
