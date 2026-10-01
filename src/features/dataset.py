"""Shared dataset loading and normalization for Hobby Hero."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, List

import pandas as pd

TEAM_ALIASES: Dict[str, str] = {
    # normalize a few non-standard names to 3-letter abbrev
    "Utah Mammoth": "UTA",
    "Utah": "UTA",
    "Mammoth": "UTA",
    "Los Angeles": "LAK",
    "St. Louis": "STL",
    "San Jose": "SJS",
    "Tampa Bay": "TBL",
    "New York Rangers": "NYR",
    "New York Islanders": "NYI",
    "New Jersey": "NJD",
    "Washington": "WSH",
    "Montreal": "MTL",
    "Toronto": "TOR",
    "Buffalo": "BUF",
    "Boston": "BOS",
    "Detroit": "DET",
    "Florida": "FLA",
    "Ottawa": "OTT",
    "Pittsburgh": "PIT",
    "Philadelphia": "PHI",
    "Columbus": "CBJ",
    "Carolina": "CAR",
    "Chicago": "CHI",
    "Nashville": "NSH",
    "Minnesota": "MIN",
    "Colorado": "COL",
    "Anaheim": "ANA",
    "Dallas": "DAL",
    "St Louis": "STL",
    "Winnipeg": "WPG",
    "Calgary": "CGY",
    "Edmonton": "EDM",
    "Vancouver": "VAN",
    "Seattle": "SEA",
    "Vegas": "VGK",
    "Arizona": "ARI",
}


def load_games(data_dir: str | Path) -> pd.DataFrame:
    """Load all games_*.jsonl into a single tidy DataFrame.

    Columns normalized: game_id, date(datetime), season, game_type, away,
    home, away_score, home_score, outcome.
    """
    rows: List[dict] = []
    data_dir = Path(data_dir)
    for path in sorted(data_dir.glob("games_*.jsonl")):
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    g = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rows.append(g)
    df = pd.DataFrame(rows)
    if df.empty:
        return df

    # ensure numeric scores
    for col in ("away_score", "home_score"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["away_score", "home_score"])

    # normalize team identifiers to 3-letter abbrev
    for col, other in (("away_team_abbrev", "away_team_name"),
                       ("home_team_abbrev", "home_team_name")):
        df[col] = df[col].fillna("")
        df[col] = df[col].apply(lambda v: TEAM_ALIASES.get(str(v).strip(), str(v).strip()))
        # fall back to name when abbrev missing
        df[col] = df[col].where(df[col] != "", df[other].map(lambda n: TEAM_ALIASES.get(str(n), str(n))))

    # only regular season + playoffs with known teams
    df = df[df["game_type"].isin([2, 3])]
    df = df[(df["away_team_abbrev"] != "") & (df["home_team_abbrev"] != "")]

    # parse date: CSV gives 'YYYY-MM-DD'; API gives ISO UTC datetime
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.tz_localize(None)
    df = df.dropna(subset=["date"])

    # standardize column names
    df = df.rename(columns={
        "away_team_abbrev": "away",
        "home_team_abbrev": "home",
        "away_score": "away_score",
        "home_score": "home_score",
    })
    df = df.sort_values(["season", "date", "game_id"]).reset_index(drop=True)
    return df
