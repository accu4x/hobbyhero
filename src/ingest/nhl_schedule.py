"""Fetch a season's regular-season schedule from the NHL API (2026-09-29).

For the artifact's upcoming games (Dan, 2026-09-29: "update the scheduled games to include
future ones for this season"). Played games still come from `nhl_api_season.py`; this only
lists what is scheduled.

  fetch   Walks /v1/schedule/<date> week by week and saves each response unchanged
          (gzipped) under data/raw/nhl_api/schedule/<season>/<fetch date>/<week>.json.gz.
          A schedule changes (postponements), so each fetch day gets its own folder and
          nothing already on disk is overwritten.
  derive  Rebuilds data/raw/nhl_api/schedule_<season>.jsonl from the newest fetch folder,
          with no network: one record per regular-season game.

    python -m src.ingest.nhl_schedule fetch  --season 20262027
    python -m src.ingest.nhl_schedule derive --season 20262027
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import date, timedelta
from pathlib import Path

import requests

from src.ingest.nhl_api_season import (BASE, OUT_DIR, RateLimiter, _get_json, _read_gz_json,
                                       _write_gz_json)

REGULAR_SEASON = 2
log = logging.getLogger("nhl_schedule")


def fetch_dir(season: int, day: str) -> Path:
    return OUT_DIR / "schedule" / str(season) / day


def out_path(season: int) -> Path:
    return OUT_DIR / f"schedule_{season}.jsonl"


def fetch(season: int) -> Path:
    start_year = season // 10000
    cur, end = date(start_year, 9, 1), date(start_year + 1, 4, 30)
    folder = fetch_dir(season, date.today().strftime("%Y%m%d"))
    limiter = RateLimiter()
    n = 0
    with requests.Session() as session:
        while cur <= end:
            path = folder / f"{cur.isoformat()}.json.gz"
            if path.exists():
                payload = _read_gz_json(path)
            else:
                payload = _get_json(session, f"{BASE}/schedule/{cur.isoformat()}", limiter)
                _write_gz_json(path, payload)
                n += 1
            nxt = payload.get("nextStartDate")
            step = date.fromisoformat(nxt) if nxt else cur + timedelta(days=7)
            cur = step if step > cur else cur + timedelta(days=7)
    log.info("season %d: %d schedule weeks fetched -> %s", season, n, folder)
    return folder


def _name(team: dict, key: str) -> str:
    return (team.get(key) or {}).get("default", "")


def derive(season: int) -> Path:
    folders = sorted(p for p in (OUT_DIR / "schedule" / str(season)).iterdir() if p.is_dir())
    if not folders:
        raise SystemExit(f"no schedule fetched for {season}; run fetch first")
    folder = folders[-1]
    games: dict[int, dict] = {}
    for path in sorted(folder.glob("*.json.gz")):
        for day in _read_gz_json(path).get("gameWeek", []):
            for g in day.get("games", []):
                if g.get("season") != season or g.get("gameType") != REGULAR_SEASON:
                    continue
                h, a = g["homeTeam"], g["awayTeam"]
                games[g["id"]] = {
                    "game_id": g["id"], "season": season, "game_type": g["gameType"],
                    "date": day["date"].replace("-", ""), "start_utc": g.get("startTimeUTC"),
                    "home": h["abbrev"], "away": a["abbrev"],
                    "home_place": _name(h, "placeName"), "home_name": _name(h, "commonName"),
                    "away_place": _name(a, "placeName"), "away_name": _name(a, "commonName"),
                    "venue": _name(g, "venue"), "neutral_site": bool(g.get("neutralSite")),
                    "game_state": g.get("gameState"), "schedule_state": g.get("gameScheduleState"),
                    "fetched": folder.name,
                }
    rows = sorted(games.values(), key=lambda r: (r["date"], r["start_utc"] or "", r["game_id"]))
    out = out_path(season)
    out.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    log.info("season %d: %d regular-season games (%s to %s) from %s -> %s", season, len(rows),
             rows[0]["date"] if rows else "-", rows[-1]["date"] if rows else "-", folder.name, out)
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("stage", choices=["fetch", "derive"])
    ap.add_argument("--season", type=int, required=True)
    args = ap.parse_args()
    if args.stage == "fetch":
        fetch(args.season)
    derive(args.season)


if __name__ == "__main__":
    main()
