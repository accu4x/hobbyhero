"""Unified NHL API ingest for Hobby Hero: fetch into a raw cache, then derive.

Two stages. Both can be re-run safely:

  fetch   Lists a season's completed games (regular season and playoffs) from
          /v1/schedule. For each game it saves the boxscore, landing and
          play-by-play responses unchanged (gzipped) under
          data/raw/nhl_api/raw/<season>/<game_id>/. Games already cached are
          skipped, so a re-run only fetches what is missing. Nothing already
          on disk is ever overwritten.
  derive  Rebuilds data/raw/nhl_api/boxscores_<season>.jsonl from that cache,
          with no network. Each record has the shape src/features/aggregate.py
          reads, plus fields the old sources lacked.

Why this exists (hobbyhero/OPEN-ITEMS.md items 13-17, 2026-09-24): the old
files mix Hockey-Reference and NHL API sources, record shootout games as ties,
and are missing whole seasons. This gives every season one source and one
schema, and keeps the raw responses so new fields never need a re-fetch.

What each record adds over the old shape:
  game_id, season, game_type (2 = regular season, 3 = playoffs),
  final_home / final_away (the official final, including the shootout
  winner's +1), winner, source.
  Per team: final_score, won, so_win, goalie_shots_against, fenwick_for /
  fenwick_against, corsi_5v5_for / corsi_5v5_against.
  sat_for / sat_against are real all-situations shot attempts from the
  play-by-play, excluding the shootout. They are NOT the skater-summed
  Hockey-Reference numbers (OPEN-ITEMS item 8).
  Per-team `goals` leaves out the shootout goal, so shooting % stays honest.

Usage, on Dan's Windows Python from the hobbyhero folder:
  python -m src.ingest.nhl_api_season fetch  --season 20252026 --limit 50
  python -m src.ingest.nhl_api_season fetch  --season 20252026
  python -m src.ingest.nhl_api_season derive --season 20252026
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import time
from collections import Counter
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

import requests

BASE = "https://api-web.nhle.com/v1"
HEADERS = {"User-Agent": "HobbyHero/0.1 (personal research; low rate)"}
ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "data" / "raw" / "nhl_api"
KINDS = ("boxscore", "landing", "play-by-play")
GAME_TYPES = {2, 3}  # regular season, playoffs (1 = preseason, skipped)
FINAL_STATES = {"OFF", "FINAL"}
SHOT_ATTEMPTS = {"shot-on-goal", "goal", "missed-shot", "blocked-shot"}
FIVE_ON_FIVE = "1551"  # situationCode: away goalie, away skaters, home skaters, home goalie
MIN_INTERVAL_S = 0.35
RETRIES = 4

log = logging.getLogger("nhl_api_season")


@dataclass(frozen=True)
class GameRef:
    game_id: int
    season: int
    game_type: int
    date: str  # YYYY-MM-DD, local game date
    home: str
    away: str


class RateLimiter:
    def __init__(self, min_interval: float = MIN_INTERVAL_S) -> None:
        self._min = min_interval
        self._last = 0.0

    def wait(self) -> None:
        gap = time.monotonic() - self._last
        if gap < self._min:
            time.sleep(self._min - gap)
        self._last = time.monotonic()


class FetchError(RuntimeError):
    """A request kept failing after all retries."""


def _get_json(session: requests.Session, url: str, limiter: RateLimiter) -> dict:
    for attempt in range(1, RETRIES + 1):
        limiter.wait()
        try:
            resp = session.get(url, headers=HEADERS, timeout=30)
        except requests.RequestException as exc:
            err = str(exc)
        else:
            if resp.status_code == 200:
                return resp.json()
            err = f"HTTP {resp.status_code}"
            if resp.status_code == 404:
                break  # not transient
        backoff = 2**attempt
        log.warning("%s -> %s (attempt %d/%d, sleeping %ds)", url, err, attempt,
                    RETRIES, backoff)
        time.sleep(backoff)
    raise FetchError(f"{url}: {err}")


# --------------------------------------------------------------------------- paths


def season_dir(season: int) -> Path:
    return OUT_DIR / "raw" / str(season)


def index_path(season: int) -> Path:
    return season_dir(season) / "_games.json"


def raw_path(season: int, game_id: int, kind: str) -> Path:
    return season_dir(season) / str(game_id) / f"{kind}.json.gz"


def _write_gz_json(path: Path, payload: dict) -> None:
    """Atomic write: a crash mid-write never leaves a half file that looks cached."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        json.dump(payload, fh, separators=(",", ":"))
    tmp.replace(path)


def _read_gz_json(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------- fetch


def list_games(season: int, session: requests.Session,
               limiter: RateLimiter) -> list[GameRef]:
    """Every completed regular-season and playoff game in `season`, by week."""
    start_year = season // 10000
    cur = date(start_year, 9, 1)
    end = date(start_year + 1, 7, 15)
    found: dict[int, GameRef] = {}
    while cur <= end:
        payload = _get_json(session, f"{BASE}/schedule/{cur.isoformat()}", limiter)
        for day in payload.get("gameWeek", []):
            for g in day.get("games", []):
                if (g.get("season") != season or g.get("gameType") not in GAME_TYPES
                        or g.get("gameState") not in FINAL_STATES):
                    continue
                found[g["id"]] = GameRef(
                    game_id=g["id"], season=season, game_type=g["gameType"],
                    date=day.get("date", ""), home=g["homeTeam"]["abbrev"],
                    away=g["awayTeam"]["abbrev"],
                )
        nxt = payload.get("nextStartDate")
        step = date.fromisoformat(nxt) if nxt else cur + timedelta(days=7)
        cur = step if step > cur else cur + timedelta(days=7)
    return sorted(found.values(), key=lambda r: (r.date, r.game_id))


def fetch(season: int, limit: int | None) -> None:
    limiter = RateLimiter()
    with requests.Session() as session:
        games = list_games(season, session, limiter)
        idx = index_path(season)
        idx.parent.mkdir(parents=True, exist_ok=True)
        idx.write_text(json.dumps([asdict(g) for g in games], indent=0),
                       encoding="utf-8")
        log.info("season %d: %d completed games listed -> %s", season, len(games), idx)

        uncached = [g for g in games
                    if not all(raw_path(season, g.game_id, k).exists() for k in KINDS)]
        todo = uncached if limit is None else uncached[:limit]
        log.info("%d games to fetch this run (%d already cached, %d uncached)",
                 len(todo), len(games) - len(uncached), len(uncached))

        failures: list[tuple[int, str]] = []
        for n, g in enumerate(todo, start=1):
            for kind in KINDS:
                path = raw_path(season, g.game_id, kind)
                if path.exists():
                    continue
                url = f"{BASE}/gamecenter/{g.game_id}/{kind}"
                try:
                    _write_gz_json(path, _get_json(session, url, limiter))
                except FetchError as exc:
                    failures.append((g.game_id, str(exc)))
                    log.error("%s", exc)
            if n % 25 == 0 or n == len(todo):
                log.info("fetched %d/%d (last %s %s@%s)", n, len(todo), g.date,
                         g.away, g.home)
    if failures:
        log.error("%d requests failed; re-run fetch to retry them", len(failures))


# --------------------------------------------------------------------------- derive


def _period_type(item: dict) -> str:
    return (item.get("periodDescriptor") or {}).get("periodType", "")


def _team_totals(block: dict) -> dict[str, int]:
    tot: Counter[str] = Counter()
    for group in ("forwards", "defense"):
        for p in block.get(group) or []:
            tot["pim"] += int(p.get("pim") or 0)
            tot["hits"] += int(p.get("hits") or 0)
            tot["blocked_shots"] += int(p.get("blockedShots") or 0)
            tot["shifts"] += int(p.get("shifts") or 0)
            tot["giveaways"] += int(p.get("giveaways") or 0)
            tot["takeaways"] += int(p.get("takeaways") or 0)
    for gk in block.get("goalies") or []:
        tot["pim"] += int(gk.get("pim") or 0)
        tot["saves"] += int(gk.get("saves") or 0)
        tot["goalie_shots_against"] += int(gk.get("shotsAgainst") or 0)
    return dict(tot)


def _goals_by_strength(landing: dict, abbrevs: tuple[str, str]) -> dict[str, Counter]:
    out = {a: Counter() for a in abbrevs}
    for period in (landing.get("summary") or {}).get("scoring") or []:
        if _period_type(period) == "SO":
            continue
        for goal in period.get("goals") or []:
            team = (goal.get("teamAbbrev") or {}).get("default", "")
            if team in out:
                out[team]["goals"] += 1
                out[team][f"{goal.get('strength', 'ev')}_goals"] += 1
    return out


def _penalty_counts(landing: dict, abbrevs: tuple[str, str]) -> Counter:
    """Minor and major penalties taken per team (same rule as enrich_nhl_boxscores)."""
    counts: Counter[str] = Counter()
    for period in (landing.get("summary") or {}).get("penalties") or []:
        for pen in period.get("penalties") or []:
            team = (pen.get("teamAbbrev") or {}).get("default", "")
            if pen.get("type") in ("MIN", "MAJ") and team in abbrevs:
                counts[team] += 1
    return counts


def _shot_attempts(pbp: dict, team_ids: dict[int, str]) -> dict[str, Counter]:
    """Shot attempts by shooting team, shootout excluded.

    For blocked shots the event owner is the blocking team, so the shooter's
    team comes from shootingPlayerId via rosterSpots.
    """
    player_team = {r["playerId"]: team_ids.get(r["teamId"], "")
                   for r in pbp.get("rosterSpots") or []}
    out = {abbr: Counter() for abbr in team_ids.values()}
    for play in pbp.get("plays") or []:
        kind = play.get("typeDescKey")
        if kind not in SHOT_ATTEMPTS or _period_type(play) == "SO":
            continue
        det = play.get("details") or {}
        shooter = player_team.get(det.get("shootingPlayerId") or det.get(
            "scoringPlayerId"), "")
        if not shooter:
            owner = team_ids.get(det.get("eventOwnerTeamId"), "")
            if kind == "blocked-shot":
                shooter = next((a for a in out if a != owner), "")
            else:
                shooter = owner
        if shooter not in out:
            continue
        out[shooter]["corsi"] += 1
        if kind != "blocked-shot":
            out[shooter]["fenwick"] += 1
        if play.get("situationCode") == FIVE_ON_FIVE:
            out[shooter]["corsi_5v5"] += 1
    return out


def derive_game(ref: GameRef) -> tuple[dict, list[str]]:
    box = _read_gz_json(raw_path(ref.season, ref.game_id, "boxscore"))
    landing = _read_gz_json(raw_path(ref.season, ref.game_id, "landing"))
    pbp = _read_gz_json(raw_path(ref.season, ref.game_id, "play-by-play"))
    warnings: list[str] = []

    home_t, away_t = box["homeTeam"], box["awayTeam"]
    home, away = home_t["abbrev"], away_t["abbrev"]
    abbrevs = (home, away)
    outcome = (box.get("gameOutcome") or {}).get("lastPeriodType") or "REG"
    final = {home: int(home_t["score"]), away: int(away_t["score"])}
    winner = home if final[home] > final[away] else away
    sog = {home: int(home_t.get("sog") or 0), away: int(away_t.get("sog") or 0)}

    players = box.get("playerByGameStats") or {}
    totals = {home: _team_totals(players.get("homeTeam") or {}),
              away: _team_totals(players.get("awayTeam") or {})}
    goals = _goals_by_strength(landing, abbrevs)
    pens = _penalty_counts(landing, abbrevs)
    attempts = _shot_attempts(pbp, {home_t["id"]: home, away_t["id"]: away})

    teams: dict[str, dict] = {}
    for team, opp in ((home, away), (away, home)):
        so_win = int(outcome == "SO" and team == winner)
        g = goals[team]
        if g["goals"] + so_win != final[team]:
            warnings.append(f"{ref.game_id} {team}: landing goals {g['goals']} + "
                            f"so {so_win} != final {final[team]}")
        cf, ca = attempts[team]["corsi"], attempts[opp]["corsi"]
        t = totals[team]
        teams[team] = {
            "team": team,
            "goals": g["goals"],
            "ev_goals": g["ev_goals"],
            "pp_goals": g["pp_goals"],
            "sh_goals": g["sh_goals"],
            "goals_against": goals[opp]["goals"],
            "shots": sog[team],
            "shots_against": sog[opp],
            "saves": t.get("saves", 0),
            # Goalie shots against leave out shots into an empty net, so save % is
            # saves over the goalies' own shots against, not over opponent SOG.
            "goalie_shots_against": t.get("goalie_shots_against", 0),
            "save_pct": (round(t["saves"] / t["goalie_shots_against"], 4)
                         if t.get("goalie_shots_against") else None),
            "pim": t.get("pim", 0),
            "hits": t.get("hits", 0),
            "blocked_shots": t.get("blocked_shots", 0),
            "takeaways": t.get("takeaways", 0),
            "giveaways": t.get("giveaways", 0),
            "shifts": t.get("shifts", 0),
            "pp_opportunities": pens[opp],
            "pk_opportunities": pens[team],
            "sat_for": cf,
            "sat_against": ca,
            "cf_pct": round(100 * cf / (cf + ca), 1) if cf + ca else None,
            "fenwick_for": attempts[team]["fenwick"],
            "fenwick_against": attempts[opp]["fenwick"],
            "corsi_5v5_for": attempts[team]["corsi_5v5"],
            "corsi_5v5_against": attempts[opp]["corsi_5v5"],
            "final_score": final[team],
            "won": int(team == winner),
            "so_win": so_win,
        }
        if t.get("goalie_shots_against", 0) > sog[opp]:
            warnings.append(f"{ref.game_id} {team}: goalie SA "
                            f"{t.get('goalie_shots_against')} > opp sog {sog[opp]}")

    record = {
        "boxscore_id": str(ref.game_id),
        "game_id": ref.game_id,
        "season": ref.season,
        "game_type": ref.game_type,
        "date": ref.date.replace("-", ""),
        "home_abbrev": home,
        "away_abbrev": away,
        "home_name": (home_t.get("commonName") or {}).get("default", home),
        "away_name": (away_t.get("commonName") or {}).get("default", away),
        "outcome": outcome,
        "final_home": final[home],
        "final_away": final[away],
        "winner": winner,
        "source": "nhl_api",
        "teams": teams,
    }
    return record, warnings


def _iter_index(season: int) -> Iterator[GameRef]:
    for row in json.loads(index_path(season).read_text(encoding="utf-8")):
        yield GameRef(**row)


def derive(season: int) -> None:
    out_path = OUT_DIR / f"boxscores_{season}.jsonl"
    missing: list[int] = []
    warnings: list[str] = []
    outcomes: Counter[str] = Counter()
    reg_gp: Counter[str] = Counter()
    n = 0
    tmp = out_path.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for ref in _iter_index(season):
            if not all(raw_path(season, ref.game_id, k).exists() for k in KINDS):
                missing.append(ref.game_id)
                continue
            record, warn = derive_game(ref)
            warnings.extend(warn)
            fh.write(json.dumps(record, separators=(",", ":")) + "\n")
            n += 1
            outcomes[record["outcome"]] += 1
            if ref.game_type == 2:
                reg_gp.update([ref.home, ref.away])
    tmp.replace(out_path)

    gp = sorted(set(reg_gp.values()))
    log.info("season %d: %d games -> %s", season, n, out_path)
    log.info("outcomes %s | regular-season GP per team %s (%d teams)",
             dict(outcomes), gp, len(reg_gp))
    if missing:
        log.warning("%d indexed games not fully cached (run fetch): %s ...",
                    len(missing), missing[:5])
    if warnings:
        log.warning("%d consistency warnings, first 5:\n  %s", len(warnings),
                    "\n  ".join(warnings[:5]))


# --------------------------------------------------------------------------- cli


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("stage", choices=["fetch", "derive"])
    parser.add_argument("--season", type=int, required=True, help="e.g. 20252026")
    parser.add_argument("--limit", type=int, default=None,
                        help="fetch at most N uncached games (for a test run)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    if args.stage == "fetch":
        fetch(args.season, args.limit)
    else:
        derive(args.season)


if __name__ == "__main__":
    main()
