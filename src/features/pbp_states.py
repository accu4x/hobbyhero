"""Play-by-play -> normalised events for the Markov simulator (2026-09-26).

`SPEC-markov-simulator.md`, steps 2-3. Reads the raw NHL API cache
(`data/raw/nhl_api/raw/<season>/<game_id>/play-by-play.json.gz`) and never writes to it.

- `parse_game`: one game's plays -> chain events (dicts), shootout excluded.
- `summarise`: observed per-game metrics, with the same definitions the simulator
  uses (`src/models/markov/simulate.py`), so Gate A compares like with like.
- `build_events`: every regular-season game of a season -> DataFrame, cached in
  `data/processed/markov_events_<season>.csv.gz`.
- `audit`: the Step 0 field audit (`python -m src.features.pbp_states audit`).

Everything is encoded from the home team's perspective:

- zone of an event: O / N / D relative to home, from the x coordinate and
  `homeTeamDefendingSide` (blue lines at x = +/-25 ft). Events without coordinates
  (stoppages) keep the previous zone.
- manpower of the interval that ends at an event, from that event's `situationCode`
  (away goalie, away skaters, home skaters, home goalie). A goalie off the ice counts as
  a tactical pull (HEN / AEN) only for the trailing team in the 3rd period and outside a
  delayed penalty; otherwise the extra attacker is taken back off.
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
NHL = ROOT / "data" / "raw" / "nhl_api"
RAW = NHL / "raw"
ODDS = ROOT / "data" / "raw" / "odds" / "espn"
PROCESSED = ROOT / "data" / "processed"
REPORTS = ROOT / "reports"
SEASONS = (20202021, 20212022, 20222023, 20232024, 20242025, 20252026)
REGULAR = 2

REG_LEN = 1200
BLUE_LINE = 25.0
NET_X = 89.0

KINDS = ("FACEOFF", "ATTEMPT", "PENALTY", "STOPPAGE", "OTHER", "PSTART", "END")
CLASSES = ("PSTART", "FACEOFF", "SHOT_SAVED", "SHOT_MISSED", "SHOT_BLOCKED", "GOAL",
           "PENALTY", "STOPPAGE", "OTHER")
ACTORS = ("H", "A", "N")
ZONES = ("O", "N", "D")
MANPOWER = ("EV5", "EV4", "EV3", "HPP", "HPP2", "APP", "APP2", "HEN", "AEN")
SHOOTER_MP = ("EV5", "EV4", "EV3", "PP", "PP2", "SH", "SH2", "ENF", "ENA")
OUTCOMES = ("blocked", "missed", "saved", "goal")
PEN_CATS = ("MINOR", "DOUBLE", "TWO_MINOR", "MAJOR", "COINC", "NOEFF")
PP_CATS = {"MINOR", "DOUBLE", "TWO_MINOR", "MAJOR"}
PHASES = ("P12", "P3", "P3L", "OT")

ATTEMPT_PLAYS = {"shot-on-goal": "saved", "missed-shot": "missed",
                 "blocked-shot": "blocked", "goal": "goal"}
UNBLOCKED = {"shot-on-goal", "missed-shot", "goal"}
OTHER_PLAYS = {"hit", "giveaway", "takeaway"}
CLASS_OF_OUTCOME = {"saved": "SHOT_SAVED", "missed": "SHOT_MISSED",
                    "blocked": "SHOT_BLOCKED", "goal": "GOAL"}
_TO_SHOOTER = {
    "H": {"HPP": "PP", "HPP2": "PP2", "APP": "SH", "APP2": "SH2", "HEN": "ENF", "AEN": "ENA"},
    "A": {"HPP": "SH", "HPP2": "SH2", "APP": "PP", "APP2": "PP2", "HEN": "ENA", "AEN": "ENF"},
}

log = logging.getLogger("pbp_states")


# ---------------------------------------------------------------- small helpers
def secs(t: str | None) -> int:
    m, s = (t or "0:0").split(":")
    return int(m) * 60 + int(s)


def phase(period: int, t_in_period: float) -> str:
    if period >= 4:
        return "OT"
    if period == 3:
        return "P3L" if REG_LEN - t_in_period <= 300 else "P3"
    return "P12"


def score_bucket(diff: int) -> int:
    """Home minus away, clipped to -2..+2, as an index 0..4."""
    return max(-2, min(2, diff)) + 2


def shooter_mp(mp: str, side: str) -> str:
    return _TO_SHOOTER[side].get(mp, mp)


def manpower(sc: str | None, period: int, home_diff: int, delayed: bool) -> str:
    """Interval manpower (home perspective) from a situationCode."""
    if not sc or len(sc) != 4 or not sc.isdigit():
        return "EV5"
    ag, a_s, h_s, hg = (int(c) for c in sc)
    if hg == 0 and period == 3 and home_diff < 0 and not delayed:
        return "HEN"
    if ag == 0 and period == 3 and home_diff > 0 and not delayed:
        return "AEN"
    if hg == 0:
        h_s -= 1
    if ag == 0:
        a_s -= 1
    d = h_s - a_s
    if d == 0:
        return "EV5" if h_s >= 5 else ("EV4" if h_s == 4 else "EV3")
    if d == 1:
        return "HPP"
    if d >= 2:
        return "HPP2"
    return "APP" if d == -1 else "APP2"


def is_penalty_shot(sc: str | None) -> bool:
    """Penalty shots carry one skater at most on the ice (e.g. '0101', '1010')."""
    if not sc or len(sc) != 4 or not sc.isdigit():
        return False
    return int(sc[1]) + int(sc[2]) <= 2


def zone_home(det: dict, home_defending: str | None) -> str | None:
    x = det.get("xCoord")
    if x is None or home_defending not in ("left", "right"):
        return None
    hx = float(x) if home_defending == "left" else -float(x)
    return "O" if hx > BLUE_LINE else ("D" if hx < -BLUE_LINE else "N")


def penalty_category(pens: list[tuple[str, str, int]]) -> tuple[str, str]:
    """(side, typeCode, duration) for one timestamp's penalties -> (actor, category).

    Equal penalties on both sides cancel type by type (majors, double minors, minors).
    Whatever is left decides the power play: MAJOR > DOUBLE > TWO_MINOR > MINOR for the
    side that owns it. Cancelled minors with nothing left are COINC (4v4 at full
    strength); anything else (misconducts, offsetting majors, penalty shots) is NOEFF.
    """
    cnt = {"H": Counter(), "A": Counter()}
    for side, code, dur in pens:
        if side not in cnt:
            continue
        if code in ("MIN", "BEN") and dur == 2:
            cnt[side]["m"] += 1
        elif code in ("MIN", "BEN") and dur == 4:
            cnt[side]["d"] += 1
        elif code in ("MAJ", "MAT"):
            cnt[side]["M"] += 1
    cancelled = Counter()
    for t in ("M", "d", "m"):
        c = min(cnt["H"][t], cnt["A"][t])
        cnt["H"][t] -= c
        cnt["A"][t] -= c
        cancelled[t] += c
    weight = {s: (cnt[s]["M"], cnt[s]["d"], cnt[s]["m"]) for s in "HA"}
    left = [s for s in "HA" if sum(weight[s])]
    if len(left) == 2:
        left = ["H"] if weight["H"] >= weight["A"] else ["A"]
    if left:
        c = cnt[left[0]]
        cat = ("MAJOR" if c["M"] else "DOUBLE" if c["d"]
               else "TWO_MINOR" if c["m"] >= 2 else "MINOR")
        return left[0], cat
    if cancelled["m"]:
        return "N", "COINC"
    return "N", "NOEFF"


def load_pbp(season: int, game_id: int) -> dict | None:
    path = RAW / str(season) / str(game_id) / "play-by-play.json.gz"
    if not path.exists():
        return None
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def load_records(season: int) -> list[dict]:
    path = NHL / f"boxscores_{season}.jsonl"
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


# ---------------------------------------------------------------- parsing
def parse_game(rec: dict, pbp: dict) -> list[dict]:
    """Chain events for one game, in play order. Shootout plays are not included.

    Row fields: period, tin (seconds into period), t (game seconds), kind, actor, zone,
    cls (the class the event leaves behind), mp (manpower of the interval ending at this
    event), hs/as_ (score before the event), outcome, shot_idx (ordinal among unblocked
    shots with coordinates, the order `src/features/xg.shots_for_game` uses, else -1),
    pen_cat, delayed.
    """
    home_id, away_id = pbp["homeTeam"]["id"], pbp["awayTeam"]["id"]
    side_of = {home_id: "H", away_id: "A"}
    rows: list[dict] = []
    hs = as_ = 0
    delayed = False
    zone = "N"
    shot_counter = 0
    plays = pbp.get("plays") or []
    i = 0

    def row(period: int, tin: int, **kw) -> dict:
        base = {"game_id": rec["game_id"], "season": rec["season"], "period": period,
                "tin": tin, "t": (period - 1) * REG_LEN + tin, "kind": "", "actor": "N",
                "zone": zone, "cls": "", "mp": "EV5", "hs": hs, "as_": as_,
                "outcome": "", "shot_idx": -1, "pen_cat": "", "delayed": int(delayed)}
        base.update(kw)
        return base

    while i < len(plays):
        pl = plays[i]
        pdsc = pl.get("periodDescriptor") or {}
        if pdsc.get("periodType") == "SO":
            break
        period = int(pdsc.get("number", 1))
        kind = pl.get("typeDescKey")
        tin = secs(pl.get("timeInPeriod"))
        det = pl.get("details") or {}
        sc = pl.get("situationCode")
        if kind == "period-start":
            delayed, zone = False, "N"
            rows.append(row(period, tin, kind="PSTART", cls="PSTART", zone="N"))
        elif kind == "period-end":
            rows.append(row(period, tin, kind="END", cls="END"))
            delayed = False
        elif kind == "delayed-penalty":
            delayed = True
        elif kind in ATTEMPT_PLAYS or kind in OTHER_PLAYS or kind in ("faceoff", "stoppage",
                                                                       "penalty"):
            mp = manpower(sc, period, hs - as_, delayed)
            z = zone_home(det, pl.get("homeTeamDefendingSide")) or zone
            side = side_of.get(det.get("eventOwnerTeamId"), "N")
            if kind == "penalty":
                pens, j = [], i
                while (j < len(plays) and plays[j].get("typeDescKey") == "penalty"
                       and (plays[j].get("periodDescriptor") or {}).get("number") == period
                       and plays[j].get("timeInPeriod") == pl.get("timeInPeriod")):
                    d = plays[j].get("details") or {}
                    pens.append((side_of.get(d.get("eventOwnerTeamId"), "N"),
                                 d.get("typeCode", ""), int(d.get("duration") or 0)))
                    j += 1
                actor, cat = penalty_category(pens)
                rows.append(row(period, tin, kind="PENALTY", actor=actor, zone=z,
                                cls="PENALTY", mp=mp, pen_cat=cat))
                i = j - 1
                delayed = False
            elif kind in ATTEMPT_PLAYS:
                idx = -1
                if kind in UNBLOCKED and det.get("xCoord") is not None:
                    idx = shot_counter
                    shot_counter += 1
                if not is_penalty_shot(sc):
                    out = ATTEMPT_PLAYS[kind]
                    rows.append(row(period, tin, kind="ATTEMPT", actor=side, zone=z,
                                    cls=CLASS_OF_OUTCOME[out], mp=mp, outcome=out,
                                    shot_idx=idx))
                if kind == "goal":
                    if side == "H":
                        hs += 1
                    elif side == "A":
                        as_ += 1
                    delayed = False
            elif kind == "faceoff":
                rows.append(row(period, tin, kind="FACEOFF", actor=side, zone=z,
                                cls="FACEOFF", mp=mp))
                delayed = False
            elif kind == "stoppage":
                rows.append(row(period, tin, kind="STOPPAGE", cls="STOPPAGE", mp=mp))
                delayed = False
            else:
                rows.append(row(period, tin, kind="OTHER", actor=side, zone=z, cls="OTHER",
                                mp=mp))
            zone = z
        i += 1
    return rows


def empty_summary() -> dict:
    return {"goals_h": 0, "goals_a": 0, "att_h": 0, "att_a": 0, "ppo_h": 0, "ppo_a": 0,
            "ppg_h": 0, "ppg_a": 0, "trail_d1": 0, "lead_d1": 0, "trail_d2": 0,
            "lead_d2": 0, "ot": 0, "so": 0, "home_win": 0}


def tally_attempt(s: dict, side: str, mp: str, diff: int, outcome: str) -> None:
    """Shared by the observed summary and the simulator."""
    s["att_" + side.lower()] += 1
    if outcome == "goal":
        s["goals_" + side.lower()] += 1
        if shooter_mp(mp, side) in ("PP", "PP2"):
            s["ppg_" + side.lower()] += 1
    if mp == "EV5" and diff != 0:
        trailing = "H" if diff < 0 else "A"
        bucket = "d1" if abs(diff) == 1 else "d2"
        s[("trail_" if side == trailing else "lead_") + bucket] += 1


def tally_penalty(s: dict, actor: str, cat: str) -> None:
    if actor in ("H", "A") and cat in PP_CATS:
        s["ppo_" + ("a" if actor == "H" else "h")] += 1


def summarise(rows: list[dict], rec: dict) -> dict:
    s = empty_summary()
    for r in rows:
        if r["kind"] == "ATTEMPT" and r["actor"] in ("H", "A"):
            tally_attempt(s, r["actor"], r["mp"], r["hs"] - r["as_"], r["outcome"])
        elif r["kind"] == "PENALTY":
            tally_penalty(s, r["actor"], r["pen_cat"])
    s["ot"] = int(rec.get("outcome") in ("OT", "SO"))
    s["so"] = int(rec.get("outcome") == "SO")
    s["home_win"] = int(rec.get("winner") == rec.get("home_abbrev"))
    return s


def aggregate(summaries: list[dict]) -> dict:
    """League metrics over a list of per-game summaries (the Gate A quantities)."""
    n = len(summaries)
    tot = Counter()
    for s in summaries:
        tot.update(s)
    ppo = tot["ppo_h"] + tot["ppo_a"]
    d1 = tot["trail_d1"] + tot["lead_d1"]
    d2 = tot["trail_d2"] + tot["lead_d2"]
    return {
        "games": n,
        "goals_per_game": (tot["goals_h"] + tot["goals_a"]) / n,
        "attempts_per_game": (tot["att_h"] + tot["att_a"]) / n,
        "pp_opps_per_team_game": ppo / (2 * n),
        "pp_pct": (tot["ppg_h"] + tot["ppg_a"]) / ppo if ppo else float("nan"),
        "ot_share": tot["ot"] / n,
        "so_share": tot["so"] / n,
        "home_win_rate": tot["home_win"] / n,
        "trailing_share_5v5_down1": tot["trail_d1"] / d1 if d1 else float("nan"),
        "trailing_share_5v5_down2plus": tot["trail_d2"] / d2 if d2 else float("nan"),
    }


# ---------------------------------------------------------------- season tables
def build_events(season: int, refresh: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(events, per-game observed summaries) for a season's regular-season games."""
    PROCESSED.mkdir(parents=True, exist_ok=True)
    ev_path = PROCESSED / f"markov_events_{season}.csv.gz"
    sm_path = PROCESSED / f"markov_games_{season}.csv.gz"
    if ev_path.exists() and sm_path.exists() and not refresh:
        return pd.read_csv(ev_path), pd.read_csv(sm_path)
    rows, sums = [], []
    for rec in load_records(season):
        if rec.get("game_type") != REGULAR:
            continue
        pbp = load_pbp(season, rec["game_id"])
        if pbp is None:
            log.warning("no play-by-play for %s", rec["game_id"])
            continue
        g = parse_game(rec, pbp)
        rows.extend(g)
        sums.append({"game_id": rec["game_id"], "season": season, **summarise(g, rec)})
    ev, sm = pd.DataFrame(rows), pd.DataFrame(sums)
    ev.to_csv(ev_path, index=False)
    sm.to_csv(sm_path, index=False)
    log.info("%s: %d games, %d events -> %s", season, len(sm), len(ev), ev_path.name)
    return ev, sm


# ---------------------------------------------------------------- Step 0 audit
def _frac(num: int, den: int) -> float | None:
    return round(num / den, 5) if den else None


def audit_season(season: int) -> dict:
    """Field coverage for one season's cached play-by-play (all game types)."""
    recs = {r["game_id"]: r for r in load_records(season)}
    dirs = sorted(p for p in (RAW / str(season)).iterdir() if p.is_dir())
    c: Counter = Counter()
    types: Counter = Counter()
    pen_codes: Counter = Counter()
    blocked_zone: Counter = Counter()
    dist: dict[str, list[float]] = defaultdict(list)
    so_mismatch = []
    for d in dirs:
        gid = int(d.name)
        pbp = load_pbp(season, gid)
        if pbp is None:
            c["missing_pbp"] += 1
            continue
        c["games_with_pbp"] += 1
        rec = recs.get(gid, {})
        c[f"game_type_{rec.get('game_type', 'unknown')}"] += 1
        roster = {r.get("playerId"): r.get("teamId") for r in pbp.get("rosterSpots") or []}
        home_id = pbp["homeTeam"]["id"]
        plays = pbp.get("plays") or []
        has_so = any((p.get("periodDescriptor") or {}).get("periodType") == "SO" for p in plays)
        if has_so != (rec.get("outcome") == "SO"):
            so_mismatch.append(gid)
        last = (0, -1, -1)
        pending_delayed = None
        for p in plays:
            kind = p.get("typeDescKey")
            types[kind] += 1
            pdsc = p.get("periodDescriptor") or {}
            if pdsc.get("periodType") == "SO":
                continue
            det = p.get("details") or {}
            c["plays"] += 1
            sc = p.get("situationCode")
            c["sc_valid"] += bool(sc and len(sc) == 4 and sc.isdigit())
            tip = p.get("timeInPeriod")
            c["time_valid"] += bool(tip and ":" in tip)
            c["hds_valid"] += p.get("homeTeamDefendingSide") in ("left", "right")
            key = (pdsc.get("number", 0), secs(tip), p.get("sortOrder", -1))
            c["order_ok"] += key[:2] >= last[:2]
            last = key
            if kind in ATTEMPT_PLAYS or kind in OTHER_PLAYS or kind in ("faceoff", "penalty"):
                c[f"n_{kind}"] += 1
                c[f"xy_{kind}"] += det.get("xCoord") is not None and det.get("yCoord") is not None
                c[f"owner_{kind}"] += det.get("eventOwnerTeamId") in (home_id, pbp["awayTeam"]["id"])
            if kind in ATTEMPT_PLAYS:
                c["penalty_shot_like"] += is_penalty_shot(sc)
                shooter = det.get("shootingPlayerId") or det.get("scoringPlayerId")
                if kind == "blocked-shot":
                    c["blocked_owner_is_shooter_team"] += roster.get(shooter) == det.get("eventOwnerTeamId")
                    c["blocked_has_blocker"] += det.get("blockingPlayerId") is not None
                    blocked_zone[det.get("zoneCode")] += 1
                if kind in UNBLOCKED and sc and len(sc) == 4 and sc.isdigit():
                    is_home = det.get("eventOwnerTeamId") == home_id
                    opp_g = int(sc[0]) if is_home else int(sc[3])
                    if opp_g == 1:
                        c["n_unblocked_goalie_in"] += 1
                        c["goalie_id_present"] += det.get("goalieInNetId") is not None
                x, y = det.get("xCoord"), det.get("yCoord")
                hds = p.get("homeTeamDefendingSide")
                if x is not None and hds in ("left", "right"):
                    is_home = det.get("eventOwnerTeamId") == home_id
                    attack_right = (hds == "left") == is_home
                    net = NET_X if attack_right else -NET_X
                    dist[kind].append(math.hypot(net - float(x), float(y or 0)))
            if kind == "penalty":
                pen_codes[f"{det.get('typeCode')}:{det.get('duration')}"] += 1
                who = roster.get(det.get("committedByPlayerId"))
                if who is not None:
                    c["pen_with_committer"] += 1
                    c["pen_owner_is_committer_team"] += who == det.get("eventOwnerTeamId")
                if pending_delayed is not None:
                    c["delayed_then_penalty"] += 1
                    c["delayed_owner_is_penalised"] += pending_delayed == det.get("eventOwnerTeamId")
                    pending_delayed = None
            if kind == "delayed-penalty":
                pending_delayed = det.get("eventOwnerTeamId")
            if kind == "goal":
                c["goal_has_score"] += det.get("homeScore") is not None
    out = {"game_dirs": len(dirs), "boxscore_records": len(recs),
           "so_label_mismatches": so_mismatch[:20], "n_so_label_mismatches": len(so_mismatch)}
    out.update({k: v for k, v in sorted(c.items())})
    n_play = c["plays"]
    out["coverage"] = {
        "situation_code": _frac(c["sc_valid"], n_play),
        "time_in_period": _frac(c["time_valid"], n_play),
        "home_defending_side": _frac(c["hds_valid"], n_play),
        "time_order_non_decreasing": _frac(c["order_ok"], n_play),
        "goalie_in_net_id_on_unblocked_vs_goalie": _frac(c["goalie_id_present"],
                                                         c["n_unblocked_goalie_in"]),
        **{f"xy_{k}": _frac(c[f"xy_{k}"], c[f"n_{k}"])
           for k in list(ATTEMPT_PLAYS) + sorted(OTHER_PLAYS) + ["faceoff", "penalty"]},
        "blocked_owner_is_shooter_team": _frac(c["blocked_owner_is_shooter_team"],
                                               c["n_blocked-shot"]),
        "penalty_owner_is_committer_team": _frac(c["pen_owner_is_committer_team"],
                                                 c["pen_with_committer"]),
        "delayed_penalty_owner_is_penalised_team": _frac(c["delayed_owner_is_penalised"],
                                                         c["delayed_then_penalty"]),
    }
    out["event_types"] = dict(types.most_common())
    out["penalty_type_duration"] = dict(pen_codes.most_common())
    out["blocked_zone_code_owner_relative"] = dict(blocked_zone.most_common())
    out["distance_to_attacked_net_ft"] = {
        k: {"n": len(v), "median": round(statistics.median(v), 1),
            "p10": round(sorted(v)[len(v) // 10], 1),
            "p90": round(sorted(v)[9 * len(v) // 10], 1),
            "share_within_20ft": round(sum(x <= 20 for x in v) / len(v), 3)}
        for k, v in sorted(dist.items()) if v}
    return out


def audit_odds() -> dict:
    out = {}
    for path in sorted(ODDS.glob("*.jsonl")):
        with path.open(encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
        keys = sorted({k for r in rows for k in r})

        def nz(k: str) -> int:
            return sum(1 for r in rows if r.get(k) not in (None, 0, 0.0, ""))

        out[path.name] = {
            "rows": len(rows), "fields": keys,
            "providers": dict(Counter(r.get("provider") for r in rows)),
            "nonzero": {k: nz(k) for k in keys if "over_under" in k or "spread" in k
                        or k.endswith("_ml")},
        }
    return out


def audit(seasons: tuple[int, ...] = SEASONS) -> dict:
    return {"written": pd.Timestamp.now().strftime("%Y-%m-%d"),
            "seasons": {str(s): audit_season(s) for s in seasons},
            "odds_espn": audit_odds()}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("audit", "build"))
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    if args.cmd == "audit":
        REPORTS.mkdir(exist_ok=True)
        path = REPORTS / f"markov_step0_audit_{pd.Timestamp.now():%Y-%m-%d}.json"
        with path.open("w", encoding="utf-8") as fh:
            json.dump(audit(), fh, indent=1)
        log.info("audit -> %s", path)
    else:
        for s in SEASONS:
            build_events(s, refresh=args.refresh)


if __name__ == "__main__":
    main()
