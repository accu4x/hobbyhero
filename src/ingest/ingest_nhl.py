"""NHL API ingestion for Hobby Hero.

Pulls schedule + final results from the free NHL public API
(api-web.nhle.com) into a normalized, deduplicated per-game dataset.

Key design decisions:
  - Completed games appear in the schedule payload with gameState 'OFF',
    final scores, and periodDescriptor.periodType (REG/OT/SO). So one
    schedule call per day is sufficient -- NO per-game enrichment calls.
  - The 2025-26 season is read from the user's existing Hockey-Reference
    CSV (nhl-regular-season-games-2026.csv + nhl-playoff-games-2026.csv)
    to reuse already-downloaded data and avoid re-fetching.
  - Output is written incrementally (per day) so a long run is never a
    silent black box.

Sources:
  - /v1/schedule/{date}  -> games on/around a date (paginated by date)
  - CSV files (2025-26)  -> already-downloaded results

Output: data/raw/games_<season>.jsonl  (one game record per line)
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List

import requests

BASE = "https://api-web.nhle.com/v1"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}

# gameType codes: 2 = regular season, 3 = playoffs
KEEP_GAME_TYPES = {2, 3}

# Prior seasons to backfill via API. (2025-26 comes from the CSV instead.)
SEASON_SPANS: Dict[str, tuple[str, str]] = {
    "20232024": ("2023-10-10", "2024-06-24"),
    "20242025": ("2024-10-04", "2025-06-21"),
}

# Season -> the user's existing CSVs (regular, playoff).
CSV_SEASONS: Dict[str, tuple[str, str]] = {
    "20252026": (
        "nhl-regular-season-games-2026.csv",
        "nhl-playoff-games-2026.csv",
    )
}


class RateLimiter:
    """Minimal politeness throttle."""

    def __init__(self, min_interval: float = 0.3):
        self._min = min_interval
        self._last = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        gap = now - self._last
        if gap < self._min:
            time.sleep(self._min - gap)
        self._last = now


def _get(url: str, limiter: RateLimiter) -> Dict[str, Any]:
    limiter.wait()
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.json()


def _is_final(game: Dict[str, Any]) -> bool:
    """A completed game has OFF gameState and a real score."""
    state = game.get("gameState")
    if state != "OFF":
        return False
    away = game.get("awayTeam") or {}
    home = game.get("homeTeam") or {}
    return away.get("score") is not None and home.get("score") is not None


def _record_from_api_game(game: Dict[str, Any], season: int) -> Dict[str, Any]:
    away = game.get("awayTeam") or {}
    home = game.get("homeTeam") or {}
    pd = game.get("periodDescriptor") or {}
    return {
        "game_id": game.get("id"),
        "season": season,
        "game_type": game.get("gameType"),
        "date": game.get("startTimeUTC", ""),
        "away_team_abbrev": away.get("abbrev"),
        "away_team_name": (away.get("placeName") or {}).get("default"),
        "home_team_abbrev": home.get("abbrev"),
        "home_team_name": (home.get("placeName") or {}).get("default"),
        "away_score": away.get("score"),
        "home_score": home.get("score"),
        "outcome": pd.get("periodType") if _is_final(game) else None,
        "game_state": game.get("gameState"),
    }


def collect_games_for_season(season: str, limiter: RateLimiter, outfile: Path) -> int:
    """Iterate days, collect games, append incrementally. Returns total written."""
    start_s, end_s = SEASON_SPANS[season]
    start = date.fromisoformat(start_s)
    end = date.fromisoformat(end_s)
    seen: set[int] = set()

    # Resume: already-seen game ids from an existing file.
    if outfile.exists():
        for line in outfile.read_text(encoding="utf-8").splitlines():
            try:
                seen.add(json.loads(line)["game_id"])
            except Exception:
                pass

    total_days = (end - start).days + 1
    d = start
    day_idx = 0
    while d <= end:
        day_idx += 1
        day = d.isoformat()
        try:
            payload = _get(f"{BASE}/schedule/{day}", limiter)
        except requests.RequestException as e:
            print(f"  [{day_idx}/{total_days}] {day}: ! {e}")
            d += timedelta(days=1)
            continue

        new_rows: List[str] = []
        week = payload.get("gameWeek", [])
        for gw in week:
            for game in gw.get("games", []):
                gid = game.get("id")
                gtype = game.get("gameType")
                if gid is None or gtype not in KEEP_GAME_TYPES:
                    continue
                if gid in seen:
                    continue
                seen.add(gid)
                new_rows.append(
                    json.dumps(
                        _record_from_api_game(game, int(season)), ensure_ascii=False
                    )
                )

        if new_rows:
            with outfile.open("a", encoding="utf-8") as f:
                f.write("\n".join(new_rows) + "\n")

        if day_idx % 20 == 0 or day_idx == total_days:
            print(f"  [{day_idx}/{total_days}] {day}: +{len(new_rows)} new "
                  f"(running total {len(seen)})")
        d += timedelta(days=1)

    return len(seen)


def parse_hockeyref_csv(path: Path) -> List[Dict[str, Any]]:
    """Parse the Hockey-Reference '..._games_2026.csv' format.

    Columns: Date,Time,Visitor,G,Home,G,,Att.,LOG,Notes
    Notes column carries 'SO'/'OT' for games decided beyond regulation.
    """
    out: List[Dict[str, Any]] = []
    if not path.exists():
        return out
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if not header:
            return out
        idx = {name: i for i, name in enumerate(header)}
        # Home score is the G column that comes AFTER the Home column.
        home_score_idx = idx["Home"] + 1
        # OT/SO marker lives in the empty column between Home's G and Att.
        outcome_idx = home_score_idx + 1
        for row in reader:
            if len(row) <= home_score_idx:
                continue
            date_s = row[idx["Date"]].strip()
            away = row[idx["Visitor"]].strip()
            away_g = row[idx["G"]].strip()  # first G = visitor score
            home = row[idx["Home"]].strip()
            home_g = row[home_score_idx].strip()
            marker = row[outcome_idx].strip().upper() if len(row) > outcome_idx else ""
            try:
                away_g_i = int(away_g)
                home_g_i = int(home_g)
            except (ValueError, TypeError):
                continue
            outcome = "REG"
            if "SO" in marker:
                outcome = "SO"
            elif "OT" in marker:
                outcome = "OT"
            outcome = "REG"
            if "SO" in marker:
                outcome = "SO"
            elif "OT" in marker:
                outcome = "OT"
            # game_id: stable pseudo id from date+teams
            abbrev = lambda name: "".join(w[0] for w in name.split() if w)[:3].upper() or "XXX"  # noqa
            gid = f"{date_s.replace('-','')}_{abbrev(away)}@{abbrev(home)}"
            out.append({
                "game_id": gid,
                "season": None,  # set by caller
                "game_type": 2,
                "date": date_s,
                "away_team_abbrev": abbrev(away),
                "away_team_name": away,
                "home_team_abbrev": abbrev(home),
                "home_team_name": home,
                "away_score": away_g_i,
                "home_score": home_g_i,
                "outcome": outcome,
                "game_state": "OFF",
                "_source": "csv",
            })
    return out


def load_csv_season(season: str, csvdir: Path) -> int:
    reg_name, play_name = CSV_SEASONS[season]
    reg = parse_hockeyref_csv(csvdir / reg_name)
    play = parse_hockeyref_csv(csvdir / play_name)
    for g in reg + play:
        g["season"] = int(season)
        g["game_type"] = 3 if g in play else 2

    outfile = Path("data/raw") / f"games_{season}.jsonl"
    outfile.parent.mkdir(parents=True, exist_ok=True)
    with outfile.open("w", encoding="utf-8") as f:
        for g in reg + play:
            f.write(json.dumps(g, ensure_ascii=False) + "\n")
    print(f"[+] CSV season {season}: wrote {len(reg) + len(play)} games to {outfile}")
    return len(reg) + len(play)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest NHL schedule/results.")
    parser.add_argument(
        "--api-seasons", nargs="*", default=sorted(SEASON_SPANS),
        help="Seasons to backfill via API (default: all configured prior seasons)",
    )
    parser.add_argument(
        "--csv-seasons", nargs="*", default=sorted(CSV_SEASONS),
        help="Seasons to load from existing CSVs",
    )
    parser.add_argument("--csvdir", default="C:/Users/hn2_f/source/python",
                        help="Directory holding the Hockey-Reference CSVs")
    parser.add_argument("--outdir", default="data/raw", help="Output directory")
    parser.add_argument("--rate", type=float, default=0.3, help="Min seconds between requests")
    args = parser.parse_args()

    limiter = RateLimiter(args.rate)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    for season in args.csv_seasons:
        load_csv_season(season, Path(args.csvdir))

    for season in args.api_seasons:
        print(f"[-->] Ingesting season {season} via API ...")
        outfile = outdir / f"games_{season}.jsonl"
        n = collect_games_for_season(season, limiter, outfile)
        finals = sum(1 for line in outfile.read_text(encoding="utf-8").splitlines()
                     if line and '"outcome": "REG"'.replace('"outcome": ', '') in line or '"outcome":"' in line)
        # simpler: count non-null outcomes
        finals = 0
        for line in outfile.read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            g = json.loads(line)
            if g.get("outcome"):
                finals += 1
        print(f"[+] API season {season}: {n} games, {finals} finalized -> {outfile}")


if __name__ == "__main__":
    main()
