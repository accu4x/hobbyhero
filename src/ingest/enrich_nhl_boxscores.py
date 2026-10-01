"""Enrich 2025-26 Hockey-Reference boxscores with NHL API team stats.

The existing `boxscores_20252026.jsonl` (parsed from HR HTML) has hits, blocks,
PIM, pp/sh goals, and Corsi. The NHL API `gamecenter/{id}/boxscore` adds per-player
takeaways, giveaways, faceoffs, shifts; the `landing` endpoint's penalties let us
derive REAL power-play / penalty-kill opportunities (vs the PIM/2 approximation).

boxscore_id encodes date+home (e.g. "202510070FLA"). We map to the NHL game id via
the schedule endpoint keyed by (date, home_abbrev), then merge new team aggregates
back into each record's `teams` dict. Existing HR-parsed fields are kept.

New per-team keys added:
  takeaways, giveaways, blocked_shots, shifts
  pp_opportunities, pk_opportunities   (derived from penalties; real, not PIM/2)

faceoff_pct is intentionally NOT added: the API only gives faceoffWinningPctg
(0.0 for players who took zero draws), so a team mean is biased low and would feed
the model bad signal. Deferred per product decision.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import date, timedelta
from pathlib import Path

import requests

BASE = "https://api-web.nhle.com/v1"
HEADERS = {"User-Agent": "Mozilla/5.0"}

ABBREV = {"VEG": "VGK"}  # NHL uses VGK; HR boxscore uses VEG


def norm(a: str) -> str:
    return ABBREV.get(a, a)


class RateLimiter:
    def __init__(self, min_interval: float = 0.3):
        self._min = min_interval
        self._last = 0.0

    def wait(self) -> None:
        gap = time.monotonic() - self._last
        if gap < self._min:
            time.sleep(self._min - gap)
        self._last = time.monotonic()


def _get(url: str, limiter: RateLimiter) -> dict:
    limiter.wait()
    r = requests.get(url, headers=HEADERS, timeout=25)
    r.raise_for_status()
    return r.json()


def game_ids_by_date(limiter: RateLimiter, out: Path) -> dict[str, dict[str, int]]:
    """Map {date: {home_abbrev: game_id}} for the season via the ~7-day schedule window."""
    if out.exists():
        return {str(k): {str(k2): int(v) for k2, v in v.items()}
                for k, v in json.loads(out.read_text(encoding="utf-8")).items()}

    result: dict[str, dict[str, int]] = {}
    cur = date(2025, 10, 7)
    end = date(2026, 6, 24)
    while cur <= end:
        try:
            payload = _get(f"{BASE}/schedule/{cur.isoformat()}", limiter)
        except requests.RequestException as e:
            print(f"  [!] {cur}: {e}")
            cur += timedelta(days=7)
            continue
        for gw in payload.get("gameWeek", []):
            dt = gw.get("date", "")
            for g in gw.get("games", []):
                gid = g.get("id")
                home = (g.get("homeTeam") or {}).get("abbrev")
                if gid is None or not home or g.get("gameState") != "OFF":
                    continue
                result.setdefault(dt, {})[home] = gid
        cur += timedelta(days=6)  # window overlaps; step 6

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result), encoding="utf-8")
    print(f"[+] game-id map ({len(result)} dates) -> {out}")
    return result


def _team_stats_from_players(team_block: dict) -> dict:
    agg: dict = {
        "takeaways": 0, "giveaways": 0, "blocked_shots": 0, "hits": 0,
        "shifts": 0,
    }
    for group in ("forwards", "defense", "goalies"):
        for p in team_block.get(group) or []:
            agg["takeaways"] += int(p.get("takeaways") or 0)
            agg["giveaways"] += int(p.get("giveaways") or 0)
            agg["blocked_shots"] += int(p.get("blockedShots") or 0)
            agg["hits"] += int(p.get("hits") or 0)
            agg["shifts"] += int(p.get("shifts") or 0)
    return agg


def _ppk_from_penalties(landing: dict, home: str, away: str) -> dict[str, int]:
    """Return {pp_opportunities, pk_opportunities} per team keyed by abbrev.

    PP opps for a team = count of opponent MIN/MAJ penalties.
    PK opps for a team = count of own MIN/MAJ penalties.
    """
    counts = {home: 0, away: 0}
    pens = (landing.get("summary") or {}).get("penalties") or []
    for period in pens:
        for p in period.get("penalties") or []:
            if p.get("type") not in ("MIN", "MAJ"):
                continue
            team = norm((p.get("teamAbbrev") or {}).get("default") or "")
            if team in counts:
                counts[team] += 1
    return {
        home: {"pp_opportunities": counts.get(away, 0),
               "pk_opportunities": counts.get(home, 0)},
        away: {"pp_opportunities": counts.get(home, 0),
               "pk_opportunities": counts.get(away, 0)},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--boxscores", default="data/raw/boxscores_20252026.jsonl")
    parser.add_argument("--out", default="data/raw/boxscores_20252026_enriched.jsonl")
    parser.add_argument("--map", default="data/raw/gameid_map_20252026.json")
    parser.add_argument("--limit", type=int, default=0, help="Only enrich first N games (0=all)")
    parser.add_argument("--rate", type=float, default=0.3)
    args = parser.parse_args()

    ROOT = Path(__file__).resolve().parents[2]
    boxpath = ROOT / args.boxscores
    outpath = ROOT / args.out
    mappath = ROOT / args.map

    limiter = RateLimiter(args.rate)
    mapping = game_ids_by_date(limiter, mappath)

    records = [json.loads(line) for line in boxpath.read_text(encoding="utf-8").splitlines()
               if line.strip()]
    if args.limit:
        records = records[:args.limit]

    out_lines: list[str] = []
    fetched = 0
    for rec in records:
        bid = rec.get("boxscore_id", "")
        if len(bid) < 11:
            out_lines.append(json.dumps(rec, ensure_ascii=False))
            continue
        dt = bid[:8]
        home_abbr = norm(bid[-3:])
        dt_iso = f"{dt[:4]}-{dt[4:6]}-{dt[6:]}"
        game_id = mapping.get(dt_iso, {}).get(home_abbr)
        if game_id is None:
            out_lines.append(json.dumps(rec, ensure_ascii=False))
            continue
        try:
            box = _get(f"{BASE}/gamecenter/{game_id}/boxscore", limiter)
            land = _get(f"{BASE}/gamecenter/{game_id}/landing", limiter)
        except requests.RequestException as e:
            print(f"  [!] {bid}: {e}")
            out_lines.append(json.dumps(rec, ensure_ascii=False))
            continue
        fetched += 1

        home_nhl = norm(box["homeTeam"]["abbrev"])
        away_nhl = norm(box["awayTeam"]["abbrev"])
        pbgs = box.get("playerByGameStats") or {}
        ppk = _ppk_from_penalties(land, home=home_nhl, away=away_nhl)

        for key, nhl_side in ((home_nhl, "homeTeam"), (away_nhl, "awayTeam")):
            if key not in rec.get("teams", {}):
                continue
            stats = _team_stats_from_players(pbgs.get(nhl_side) or {})
            stats["pp_opportunities"] = ppk[key]["pp_opportunities"]
            stats["pk_opportunities"] = ppk[key]["pk_opportunities"]
            rec["teams"][key].update(stats)

        out_lines.append(json.dumps(rec, ensure_ascii=False))
        if fetched % 25 == 0:
            print(f"  [enriched {fetched}/{len(records)}]")

    outpath.parent.mkdir(parents=True, exist_ok=True)
    outpath.write_text("\n".join(out_lines) + ("\n" if out_lines else ""), encoding="utf-8")
    print(f"[+] wrote {len(out_lines)} enriched records -> {outpath} (fetched {fetched})")


if __name__ == "__main__":
    main()
