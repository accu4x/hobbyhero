"""Historical NHL boxscore ingestion for odds-season feature building.

Fetches schedule + boxscore stats from the NHL public API for the
sportsbookreview odds seasons (e.g. 2020-21, 2021-22) so we can build
the same features used for 2025-26 (minus Corsi, which the API lacks).

Per game we extract per-team aggregates: shots (SOG), goals, PIM,
power-play goals, shots-against, saves, save%, and the shootout/OT
outcome. These are written as per-team-per-game records compatible with
aggregate.build_team_event_frame.

Cost: one schedule call per day + one boxscore call per game. Runs as a
background job with incremental, resumable output.

Note: season dates here are the API's 'season' codes (20202021, 20212022).
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

BASE = "https://api-web.nhle.com/v1"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}
KEEP_GAME_TYPES = {2, 3}

# api season code -> (start_date, end_date) for daily paging
SEASON_SPANS: Dict[str, tuple[str, str]] = {
    "20202021": ("2021-01-13", "2021-07-08"),  # COVID-shortened
    "20212022": ("2021-10-12", "2022-06-26"),
}


class RateLimiter:
    def __init__(self, min_interval: float = 0.35):
        self._min = min_interval
        self._last = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        gap = now - self._last
        if gap < self._min:
            time.sleep(self._min - gap)
        self._last = now


def _get(url: str, limiter: RateLimiter) -> Optional[Dict[str, Any]]:
    limiter.wait()
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        print(f"  [!] {url}: {e}")
        return None


def _team_stats_from_boxscore(box: Dict[str, Any], side: str) -> Dict[str, Any]:
    """Aggregate player stats from a boxscore for one side (home/away)."""
    pgs = box.get("playerByGameStats", {})
    team = pgs.get(side, {})
    forwards = team.get("forwards", []) or []
    defense = team.get("defense", []) or []
    goalies = team.get("goalies", []) or []
    skaters = forwards + defense

    agg = {
        "shots": sum(p.get("sog", 0) or 0 for p in skaters),
        "pim": sum(p.get("pim", 0) or 0 for p in skaters),
        "goals": sum(p.get("goals", 0) or 0 for p in skaters),
        "pp_goals": sum(p.get("powerPlayGoals", 0) or 0 for p in skaters),
        "ev_goals": sum(p.get("evenStrengthGoals", 0) or 0 for p in skaters)
        if any("evenStrengthGoals" in p for p in skaters) else 0,
        "sh_goals": sum(p.get("shorthandedGoals", 0) or 0 for p in skaters)
        if any("shorthandedGoals" in p for p in skaters) else 0,
        "saves": sum(p.get("saves", 0) or 0 for p in goalies),
        "shots_against": sum(p.get("shotsAgainst", 0) or 0 for p in goalies),
        "goals_against": sum(p.get("goalsAgainst", 0) or 0 for p in goalies),
    }
    return agg


def _record_from_boxscore(box: Dict[str, Any], pbp: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    if not box:
        return None
    gid = box.get("id")
    away = box.get("awayTeam", {})
    home = box.get("homeTeam", {})
    pd = box.get("periodDescriptor", {}) or {}
    away_abbr = away.get("abbrev")
    home_abbr = home.get("abbrev")
    if not away_abbr or not home_abbr:
        return None
    home_stats = _team_stats_from_boxscore(box, "homeTeam")
    away_stats = _team_stats_from_boxscore(box, "awayTeam")
    outcome = pd.get("periodType", "REG") if box.get("gameState") == "OFF" else "REG"

    # Compute Corsi (SAT) from play-by-play shot-attempt events.
    home_sat = away_sat = 0
    if pbp:
        team_id_to_abbr = {}
        if home.get("id") is not None:
            team_id_to_abbr[home["id"]] = home_abbr
        if away.get("id") is not None:
            team_id_to_abbr[away["id"]] = away_abbr
        for play in pbp.get("plays", []) or []:
            t = play.get("typeDescKey")
            if t not in ("shot-on-goal", "missed-shot", "blocked-shot", "goal"):
                continue
            details = play.get("details") or {}
            owner = details.get("eventOwnerTeamId")
            abbr = team_id_to_abbr.get(owner)
            if abbr == home_abbr:
                home_sat += 1
            elif abbr == away_abbr:
                away_sat += 1

    home_stats["sat_for"] = home_sat
    home_stats["sat_against"] = away_sat
    away_stats["sat_for"] = away_sat
    away_stats["sat_against"] = home_sat
    if home_sat + away_sat > 0:
        home_stats["cf_pct"] = round(100.0 * home_sat / (home_sat + away_sat), 2)
        away_stats["cf_pct"] = round(100.0 * away_sat / (home_sat + away_sat), 2)
    else:
        home_stats["cf_pct"] = None
        away_stats["cf_pct"] = None

    return {
        "boxscore_id": str(gid),
        "date": box.get("gameDate", "").replace("-", "")[:8],
        "home_abbrev": home_abbr,
        "away_name": away.get("commonName", {}).get("default"),
        "home_name": home.get("commonName", {}).get("default"),
        "outcome": outcome,
        "teams": {
            home_abbr: dict(home_stats, team=home_abbr),
            away_abbr: dict(away_stats, team=away_abbr),
        },
    }


def collect_season(season_code: str, limiter: RateLimiter, outfile: Path) -> int:
    start_s, end_s = SEASON_SPANS[season_code]
    start = date.fromisoformat(start_s)
    end = date.fromisoformat(end_s)

    # resume: known game ids
    done: set = set()
    if outfile.exists():
        for line in outfile.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                done.add(json.loads(line)["boxscore_id"])
            except Exception:
                pass

    total_days = (end - start).days + 1
    d = start
    day_idx = 0
    written = 0
    while d <= end:
        day_idx += 1
        day = d.isoformat()
        sched = _get(f"{BASE}/schedule/{day}", limiter)
        if sched:
            games = [x for w in sched.get("gameWeek", []) for x in w.get("games", [])
                     if x.get("gameType") in KEEP_GAME_TYPES]
            for game in games:
                gid = game.get("id")
                if gid is None or str(gid) in done:
                    continue
                # only final games have a boxscore worth fetching
                if game.get("gameState") != "OFF":
                    continue
                box = _get(f"{BASE}/gamecenter/{gid}/boxscore", limiter)
                # play-by-play supplies shot-attempt events for Corsi
                pbp = _get(f"{BASE}/gamecenter/{gid}/play-by-play", limiter)
                rec = _record_from_boxscore(box, pbp)
                if rec:
                    with outfile.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    done.add(str(gid))
                    written += 1
        if day_idx % 10 == 0 or day_idx == total_days:
            print(f"  [{day_idx}/{total_days}] {day}: +{written} boxscores "
                  f"(total {len(done)})")
        d += timedelta(days=1)
    return len(done)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest historical NHL boxscores")
    parser.add_argument("--seasons", nargs="*", default=sorted(SEASON_SPANS),
                        help="API season codes, e.g. 20202021 20212022")
    parser.add_argument("--outdir", default="data/raw/historical")
    parser.add_argument("--rate", type=float, default=0.35)
    args = parser.parse_args()

    limiter = RateLimiter(args.rate)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    for season in args.seasons:
        outfile = outdir / f"boxscores_{season}.jsonl"
        print(f"[-->] Ingesting {season} -> {outfile}")
        n = collect_season(season, limiter, outfile)
        print(f"[+] {season}: {n} boxscores -> {outfile}")


if __name__ == "__main__":
    main()
