"""Hockey Writers article loader + point-in-time game alignment.

Reads the ingested HW article JSONL files (data/raw/hw_articles/{team}.jsonl)
and provides, for any game, each team's articles published strictly before
the game date (no leakage). This is the corpus layer that feeds the LLM
team-strength scorer.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# team folder -> abbrev (must match fetch_hw_articles.TEAM_CATEGORIES abbrevs)
TEAM_ABBREV = {
    "anaheim-ducks": "ANA", "arizona-coyotes": "ARI", "boston-bruins": "BOS",
    "buffalo-sabres": "BUF", "calgary-flames": "CGY", "carolina-hurricanes": "CAR",
    "chicago-blackhawks": "CHI", "colorado-avalanche": "COL",
    "columbus-blue-jackets": "CBJ", "dallas-stars": "DAL", "detroit-red-wings": "DET",
    "edmonton-oilers": "EDM", "florida-panthers": "FLA", "los-angeles-kings": "LAK",
    "minnesota-wild": "MIN", "montreal-canadiens": "MTL", "nashville-predators": "NSH",
    "new-jersey-devils": "NJD", "new-york-islanders": "NYI", "new-york-rangers": "NYR",
    "ottawa-senators": "OTT", "philadelphia-flyers": "PHI", "pittsburgh-penguins": "PIT",
    "san-jose-sharks": "SJS", "seattle-kraken": "SEA", "st-louis-blues": "STL",
    "tampa-bay-lightning": "TBL", "toronto-maple-leafs": "TOR", "utah-hockey-club": "UTA",
    "vancouver-canucks": "VAN", "vegas-golden-knights": "VGK", "washington-capitals": "WSH",
    "winnipeg-jets": "WPG",
}
ABBREV_TO_TEAM = {v: k for k, v in TEAM_ABBREV.items()}


class HWCorpus:
    """Loads and serves HW articles for point-in-time game context."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        # abbrev -> sorted list of (ISO_date, article_dict)
        self._articles: Dict[str, List[Tuple[str, dict]]] = {}
        self._loaded: Dict[str, bool] = {}

    def _load_team(self, abbr: str) -> None:
        if abbr in self._loaded:
            return
        self._articles.setdefault(abbr, [])
        team = ABBREV_TO_TEAM.get(abbr)
        if team:
            p = self.root / f"{team}.jsonl"
            if p.exists():
                arts = []
                for line in p.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        arts.append(json.loads(line))
                    except Exception:
                        continue
                arts.sort(key=lambda a: a.get("date", ""))
                self._articles[abbr] = [(a["date"][:10], a) for a in arts]
        self._loaded[abbr] = True

    def articles_before(self, abbr: str, game_date: date,
                        max_age_days: int = 30, max_articles: int = 6) -> List[dict]:
        """Most recent articles for a team published strictly before game_date.

        Returns up to `max_articles` recent articles, each within max_age_days.
        Sorted newest-first.
        """
        self._load_team(abbr)
        gd = game_date.isoformat()
        out = []
        for adate, art in reversed(self._articles.get(abbr, [])):
            if adate >= gd:
                continue  # strictly before game
            age = (game_date - date.fromisoformat(adate)).days
            if age > max_age_days:
                break  # list is date-ascending; older than window, stop
            out.append(art)
            if len(out) >= max_articles:
                break
        return out

    def has_any(self, abbr: str, game_date: date) -> bool:
        return len(self.articles_before(abbr, game_date, max_age_days=365)) > 0
