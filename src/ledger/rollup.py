"""Roll scored articles up to team-week ledger values (SPEC step 2).

For each team and each game week (Monday W), every dimension is the weighted
mean of that team's article scores published strictly before W, within the
last WINDOW_WEEKS. Weight = confidence x 0.5 ** (age_weeks / HALF_LIFE_WEEKS).
Nulls are skipped, and `n_<dim>` records how many articles contributed, so a
missing value stays missing. Output: data/ledger/team_weeks_<season>.jsonl,
one row per (team, week). Games in week W use the row for W (zero leakage:
nothing published on or after W's Monday).

    python -m src.ledger.rollup --season 20252026
"""

from __future__ import annotations

import argparse
import json
import logging
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from src.ledger.record import DIMENSIONS, LEDGER_DIR

HALF_LIFE_WEEKS = 2.0
# THN is a monthly magazine: its reports stay relevant longer than a daily blog post.
WINDOW_WEEKS = {"thehockeynews": 16, "hockeywriters": 6}
HALF_LIFE_BY_SOURCE = {"thehockeynews": 6.0, "hockeywriters": HALF_LIFE_WEEKS}

GAME_WEEKS = {20252026: (date(2025, 10, 6), date(2026, 4, 13))}

log = logging.getLogger("ledger_rollup")


def rollup(season: int) -> list[dict]:
    rows = [json.loads(l) for l in
            (LEDGER_DIR / f"articles_{season}.jsonl").read_text(encoding="utf-8").splitlines()
            if l.strip()]
    by_team: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_team[r["team"]].append(r)
    if not rows:
        return []
    # Game weeks: every Monday of the regular season (first game week 2025-10-06).
    first_game_week, last_game_week = GAME_WEEKS[season]
    game_weeks = [first_game_week + timedelta(weeks=i)
                  for i in range((last_game_week - first_game_week).days // 7 + 1)]
    out: list[dict] = []
    for team, arts in sorted(by_team.items()):
        for w in game_weeks:
            row: dict = {"team": team, "week": w.isoformat()}
            for dim in DIMENSIONS:
                num = den = 0.0
                n = 0
                for a in arts:
                    pub = date.fromisoformat(a["published_at"][:10])
                    age = (w - pub).days / 7
                    s = a["scores"].get(dim)
                    src = a.get("source", "hockeywriters")
                    if s is None or pub >= w or age > WINDOW_WEEKS.get(src, 6):
                        continue
                    wt = a["confidence"] * 0.5 ** (age / HALF_LIFE_BY_SOURCE.get(src, 2.0))
                    num += wt * s
                    den += wt
                    n += 1
                row[dim] = round(num / den, 4) if den else None
                row[f"n_{dim}"] = n
            out.append(row)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--season", type=int, default=20252026)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    rows = rollup(args.season)
    path = LEDGER_DIR / f"team_weeks_{args.season}.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    log.info("%d team-week rows -> %s", len(rows), path)


if __name__ == "__main__":
    main()
