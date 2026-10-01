"""Fetch 2025-26 NHL closing moneyline odds from ESPN's free public APIs.

Two-step process:
  1. Enumerate game event ids by querying the ESPN scoreboard for each date
     in the season range:
       GET site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard?dates=YYYYMMDD&limit=100
     NOTE: the `limit=100` param is REQUIRED; without it ESPN returns HTTP 403.
     The scoreboard also gives us the human-readable home/away team names.
  2. For each unique event id, fetch closing moneylines:
       GET sports.core.api.espn.com/v2/sports/hockey/leagues/nhl/events/{eid}/competitions/{eid}/odds
     pick provider.name == "ESPN BET"; read homeTeamOdds/awayTeamOdds
     .close.moneyLine.american (also captures open + current).

Writes records in the SAME schema as nhl_archive_10Y.json (sportsbookreview
archive) so market_edge.load_odds_archive() can consume them unchanged:
    season (int starting year), date (yyyymmdd stored as a float),
    home_team/away_team as nicknames, home/away_open_ml, home/away_close_ml,
    and zeroed spread/OU fields.

Runs as a resumable background job: completed (date,home,away) records are
tracked in the output file so a re-run only fetches missing games.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import requests

SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard"
ODDS = "https://sports.core.api.espn.com/v2/sports/hockey/leagues/nhl/events/{eid}/competitions/{eid}/odds"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# 2025-26 NHL regular season (regular season only; playoffs start mid-April).
SEASON = 2025
REG_START = date(2025, 10, 1)
REG_END = date(2026, 4, 15)


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


def _get(url: str, limiter: RateLimiter, params: Optional[Dict] = None,
         retries: int = 4) -> Optional[Dict]:
    """GET with rate-limit + retry-on-transient-failure (rate limiting is common)."""
    for attempt in range(retries):
        limiter.wait()
        try:
            resp = requests.get(url, headers=HEADERS, params=params, timeout=20)
            if resp.status_code == 200:
                return resp.json()
            # 403/429/5xx -> transient (rate limit / scrape protection)
            print(f"  [!] HTTP {resp.status_code} {url.split('/v2/')[-1]} "
                  f"(attempt {attempt+1}/{retries})")
            time.sleep(1.5 * (attempt + 1))
        except requests.RequestException as e:
            print(f"  [!] {url.split('/v2/')[-1]}: {e} (attempt {attempt+1}/{retries})")
            time.sleep(1.5 * (attempt + 1))
    return None


def _american_ml(team_odds: Dict, which: str = "close") -> Optional[float]:
    """Return an American moneyline from a team-odds dict, e.g. close.moneyLine.american."""
    if not isinstance(team_odds, dict):
        return None
    block = team_odds.get(which, {}) if isinstance(team_odds.get(which), dict) else {}
    ml = block.get("moneyLine", {}) if isinstance(block, dict) else {}
    am = ml.get("american")
    if am is None:
        return None
    try:
        return float(am)
    except (ValueError, TypeError):
        return None


# team-id -> nickname cache (resolved lazily from the odds team $ref)
_TEAM_CACHE: Dict[str, str] = {}


def _team_name(team_odds: Dict, limiter: RateLimiter) -> str:
    """Resolve a team's nickname from its $ref id (or fall back to 'Team{id}')."""
    ref = team_odds.get("team", {}).get("$ref", "")
    tid = ""
    if ref:
        try:
            tid = ref.rstrip("/").split("/")[-1]
        except Exception:
            tid = ""
    if not tid:
        return "Unknown"
    if tid in _TEAM_CACHE:
        return _TEAM_CACHE[tid]
    # resolve the team resource
    url = f"https://sports.core.api.espn.com/v2/sports/hockey/leagues/nhl/teams/{tid}"
    j = _get(url, limiter)
    if j:
        name = (j.get("displayName") or j.get("name") or f"Team{tid}").strip()
    else:
        name = f"Team{tid}"
    _TEAM_CACHE[tid] = name
    return name


# Providers, in preference order. ESPN BET covers early-season games,
# DraftKings covers the whole season; neither is universal alone.
PREFERRED_PROVIDERS = ["ESPN BET", "DraftKings"]


def fetch_game_odds(eid: str, limiter: RateLimiter) -> Optional[Dict]:
    """Fetch home/away closing ML + team names for one game.

    Iterates PREFERRED_PROVIDERS in order and uses the first that has a
    complete closing moneyline. Returns None if none do.
    """
    j = _get(ODDS.format(eid=eid), limiter)
    if not j:
        return None
    items = j.get("items", [])
    for provider in PREFERRED_PROVIDERS:
        item = next((i for i in items
                     if i.get("provider", {}).get("name") == provider), None)
        if not item:
            continue
        home = item.get("homeTeamOdds", {})
        away = item.get("awayTeamOdds", {})
        home_close = _american_ml(home, "close")
        away_close = _american_ml(away, "close")
        if home_close is None or away_close is None:
            continue
        return {
            "home_team": _team_name(home, limiter),
            "away_team": _team_name(away, limiter),
            "home_open_ml": _american_ml(home, "open"),
            "away_open_ml": _american_ml(away, "open"),
            "home_close_ml": home_close,
            "away_close_ml": away_close,
            "provider": provider,
        }
    return None


def enumerate_event_ids(start: date, end: date, limiter: RateLimiter,
                        done: Set[str]) -> Tuple[List[Tuple[str, date]], Set[str]]:
    """Enumerate all game event ids in [start, end] via the date-keyed scoreboard."""
    events: List[Tuple[str, date]] = []
    seen: Set[str] = set()
    d = start
    while d <= end:
        ds = d.strftime("%Y%m%d")
        j = _get(SCOREBOARD, limiter, params={"dates": ds, "limit": 100})
        if j:
            for e in j.get("events", []):
                eid = e.get("id")
                if not eid:
                    continue
                if eid in seen or eid in done:
                    continue
                seen.add(eid)
                events.append((eid, d))
        d += timedelta(days=1)
    return events, seen


def collect_season(outfile: Path, start: date, end: date, season: int) -> int:
    outfile.parent.mkdir(parents=True, exist_ok=True)
    limiter = RateLimiter(0.35)
    # resumable: track already-written (date,home,away) keys and espn ids
    done_ids: Set[str] = set()
    done_keys: Set[Tuple[str, str, str]] = set()
    if outfile.exists():
        for line in outfile.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            done_keys.add((str(rec["date"]), rec["home_team"], rec["away_team"]))
            eid = rec.get("espn_id")
            if eid:
                done_ids.add(str(eid))

    print(f"[-->] Enumerating events {start} .. {end}")
    events, seen = enumerate_event_ids(start, end, limiter, done_ids)
    print(f"[i] {len(events)} new event ids to fetch (skipping {len(done_ids)} done)")

    written = 0
    missing = 0
    for eid, d in events:
        odds = fetch_game_odds(eid, limiter)
        if not odds:
            missing += 1
            print(f"  [!] no ESPN BET closing ML for {eid} ({d})")
            continue
        rec = {
            "espn_id": eid,
            "provider": odds.get("provider", ""),
            "season": season,
            "date": float(d.strftime("%Y%m%d")),
            "home_team": odds["home_team"],
            "away_team": odds["away_team"],
            "home_open_ml": odds["home_open_ml"],
            "away_open_ml": odds["away_open_ml"],
            "home_close_ml": odds["home_close_ml"],
            "away_close_ml": odds["away_close_ml"],
            # schema-compatible placeholders (not populated from this source)
            "home_close_spread": 0.0,
            "away_close_spread": 0.0,
            "home_close_spread_odds": 0.0,
            "away_close_spread_odds": 0.0,
            "open_over_under": 0.0,
            "open_over_under_odds": 0.0,
            "close_over_under": 0.0,
            "close_over_under_odds": 0.0,
        }
        with outfile.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        written += 1
        if written % 25 == 0:
            print(f"  [{written}/{len(events)}] +{written} games, {missing} missing")
    print(f"[+] done: {written} written, {missing} missing odds")
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch 2025-26 NHL closing ML odds from ESPN")
    parser.add_argument("--out", default="data/raw/odds/espn/nhl_archive_2025_26.jsonl")
    parser.add_argument("--season", type=int, default=SEASON)
    parser.add_argument("--start", default=REG_START.isoformat())
    parser.add_argument("--end", default=REG_END.isoformat())
    args = parser.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    n = collect_season(out, start, end, args.season)
    print(f"[+] {n} odds records -> {out}")


if __name__ == "__main__":
    main()
