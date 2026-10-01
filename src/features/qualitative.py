"""Qualitative article scoring for Hobby Hero — Stage-0 pilot.

Defines a scoring rubric for converting hockey-news articles into
betting-relevant dimension scores, and helpers to align articles to games
point-in-time (no leakage) and build qualitative delta features.

Dimensions (each scored on -1.0..+1.0, 0 = neutral/unknown):
  injury_lineup  : star-player injuries / lineup changes. -1 = key players
                   out or depth depleted; +1 = healthy, reinforcements back.
  rest_travel    : rest/fatigue/travel. -1 = tired (back-to-back, long road,
                   travel); +1 = well-rested (rest advantage, home stand).
  momentum       : recent form / streak / confidence. -1 = cold (losing
                   streak, slump); +1 = hot (winning streak, surging).
  motivation     : stakes / desperation / narrative. -1 = low motivation
                   (tanking, no stakes, distractions); +1 = high stakes
                   (playoff race, rivalry, revenge, must-win).

When an article contains no information about a dimension, score that
dimension 0 (neutral) and note it as "unsupported" in the source field.
Never invent facts — only score what the article states.

Usage:
    from src.features import qualitative as q
    qual = q.QualitativeScorer(article_map)
    feats = qual.add_qualitative_features(feature_frame)
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple


class QualitativeScorer:
    """Aligns per-team articles to games point-in-time and scores them."""

    # team folder name -> abbrev (mirrors teams.py + VEG->VGK fix)
    TEAM_FOLDER = {
        "Anaheim_Ducks": "ANA", "Arizona_Coyotes": "ARI", "Boston_Bruins": "BOS",
        "Buffalo_Sabres": "BUF", "Calgary_Flames": "CGY", "Carolina_Hurricanes": "CAR",
        "Chicago_Blackhawks": "CHI", "Colorado_Avalanche": "COL",
        "Columbus_Blue_Jackets": "CBJ", "Dallas_Stars": "DAL",
        "Detroit_Red_Wings": "DET", "Edmonton_Oilers": "EDM",
        "Florida_Panthers": "FLA", "Los_Angeles_Kings": "LAK",
        "Minnesota_Wild": "MIN", "Montreal_Canadiens": "MTL",
        "Nashville_Predators": "NSH", "New_Jersey_Devils": "NJD",
        "New_York_Islanders": "NYI", "New_York_Rangers": "NYR",
        "Ottawa_Senators": "OTT", "Philadelphia_Flyers": "PHI",
        "Pittsburgh_Penguins": "PIT", "San_Jose_Sharks": "SJS",
        "Seattle_Kraken": "SEA", "St._Louis_Blues": "STL",
        "Tampa_Bay_Lightning": "TBL", "Toronto_Maple_Leafs": "TOR",
        "Utah_Mammoth": "UTA", "Vancouver_Canucks": "VAN",
        "Vegas_Golden_Knights": "VGK", "Washington_Capitals": "WSH",
        "Winnipeg_Jets": "WPG",
    }
    DIMENSIONS = ("injury_lineup", "rest_travel", "momentum", "motivation")

    def __init__(self, article_map: Dict[str, List[Tuple[str, str]]]):
        """article_map: abbrev -> sorted list of (ISO_date, article_path)."""
        self.article_map = article_map
        # cache of (abbrev, path) -> parsed plain-text
        self._text_cache: Dict[Tuple[str, str], str] = {}

    @staticmethod
    def _to_plain(path: str) -> str:
        import re
        raw = open(path, encoding="utf-8", errors="replace").read()
        txt = re.sub(r"<script.*?</script>|<style.*?</style>", " ", raw, flags=re.S)
        txt = re.sub(r"<[^>]+>", " ", txt)
        txt = re.sub(r"\s+", " ", txt).strip()
        return txt

    def article_text(self, abbr: str, path: str) -> str:
        key = (abbr, path)
        if key not in self._text_cache:
            self._text_cache[key] = self._to_plain(path)
        return self._text_cache[key]

    def last_article_before(self, abbr: str, game_date: date,
                            max_age_days: int = 30) -> Optional[Tuple[str, str]]:
        """Most recent article published strictly before game_date within max_age_days."""
        arts = self.article_map.get(abbr, [])
        best = None
        for adate, path in arts:
            if adate >= game_date.isoformat():
                break  # sorted ascending
            age = (game_date - date.fromisoformat(adate)).days
            if 0 <= age <= max_age_days:
                best = (adate, path)
        return best

    def scores_for_team(self, abbr: str, game_date: date,
                        scorer) -> Optional[Dict[str, float]]:
        """Return {dimension: score} for a team from its most recent article, or None."""
        art = self.last_article_before(abbr, game_date)
        if not art:
            return None
        adate, path = art
        text = self.article_text(abbr, path)
        return scorer(text, abbr, adate)

    def add_qualitative_features(self, df, scorer) -> "pd.DataFrame":
        """Add home/away qualitative scores + deltas to a feature frame.

        scorer(text, team_abbrev, article_date) -> dict of dimension->float.
        Rows without a usable article for either team get NaN qualitative
        features (they must be dropped before training).
        """
        import pandas as pd
        df = df.copy().reset_index(drop=True)
        df["date_dt"] = pd.to_datetime(df["date"]).dt.date
        df["home_a"] = df["home"].map(lambda a: "VGK" if a == "VEG" else a)
        df["away_a"] = df["away"].map(lambda a: "VGK" if a == "VEG" else a)

        rows = []
        for dim in self.DIMENSIONS:
            rows.append((f"qual_{dim}_home", [None] * len(df)))
            rows.append((f"qual_{dim}_away", [None] * len(df)))
            rows.append((f"qual_{dim}_delta", [None] * len(df)))
        cols = {name: vals for name, vals in rows}

        for i, r in df.iterrows():
            hs = self.scores_for_team(r["home_a"], r["date_dt"], scorer)
            as_ = self.scores_for_team(r["away_a"], r["date_dt"], scorer)
            for dim in self.DIMENSIONS:
                hv = hs.get(dim) if hs else None
                av = as_.get(dim) if as_ else None
                cols[f"qual_{dim}_home"][i] = hv
                cols[f"qual_{dim}_away"][i] = av
                if hv is not None and av is not None:
                    cols[f"qual_{dim}_delta"][i] = hv - av
        for name, vals in cols.items():
            df[name] = vals
        return df
