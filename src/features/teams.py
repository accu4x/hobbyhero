"""Team nickname/abbreviation normalization for odds data join.

The sportsbookreview odds archive stores teams as loose nicknames
('Maple Leafs', 'NY Islanders', 'SeattleKraken', 'St.Louis', 'Tampa Bay').
Our feature output uses 3-letter abbrevs. This module maps every variant
to the canonical 3-letter abbrev used by Hockey-Reference/NHL.
"""

from __future__ import annotations

from typing import Dict

# Every known nickname variant -> canonical 3-letter abbrev
NICK_TO_ABBREV: Dict[str, str] = {
    "Anaheim": "ANA", "Ducks": "ANA",
    "Arizona": "ARI", "Arizonas": "ARI", "Coyotes": "ARI", "Phoenix": "ARI",
    "Boston": "BOS", "Bruins": "BOS",
    "Buffalo": "BUF", "Sabres": "BUF",
    "Calgary": "CGY", "Flames": "CGY",
    "Carolina": "CAR", "Hurricanes": "CAR",
    "Chicago": "CHI", "Blackhawks": "CHI",
    "Colorado": "COL", "Avalanche": "COL",
    "Columbus": "CBJ", "Blue Jackets": "CBJ", "Columbus Blue Jackets": "CBJ",
    "Dallas": "DAL", "Stars": "DAL",
    "Detroit": "DET", "Red Wings": "DET", "Detroit Red Wings": "DET",
    "Edmonton": "EDM", "Oilers": "EDM",
    "Florida": "FLA", "Panthers": "FLA",
    "Los Angeles": "LAK", "Kings": "LAK", "LA": "LAK",
    "Minnesota": "MIN", "Wild": "MIN",
    "Montreal": "MTL", "Canadiens": "MTL", "Montreal Canadiens": "MTL",
    "Nashville": "NSH", "Predators": "NSH",
    "New Jersey": "NJD", "Devils": "NJD", "Jersey": "NJD",
    "New York Islanders": "NYI", "NY Islanders": "NYI", "Islanders": "NYI",
    "New York Rangers": "NYR", "NY Rangers": "NYR", "NYR": "NYR", "Rangers": "NYR",
    "Ottawa": "OTT", "Senators": "OTT",
    "Philadelphia": "PHI", "Flyers": "PHI",
    "Pittsburgh": "PIT", "Penguins": "PIT",
    "San Jose": "SJS", "Sharks": "SJS", "San Jose Sharks": "SJS",
    "Seattle": "SEA", "Kraken": "SEA", "SeattleKraken": "SEA",
    "St. Louis": "STL", "St Louis": "STL", "St.Louis": "STL", "Blues": "STL",
    "Tampa Bay": "TBL", "Tampa": "TBL", "Lightning": "TBL",
    "Toronto": "TOR", "Maple Leafs": "TOR", "Toronto Maple Leafs": "TOR",
    "Utah": "UTA", "Mammoth": "UTA",
    "Vancouver": "VAN", "Canucks": "VAN",
    "Vegas": "VGK", "Golden Knights": "VGK", "Vegas Golden Knights": "VGK",
    "Washington": "WSH", "Capitals": "WSH",
    "Winnipeg": "WPG", "Jets": "WPG", "WinnipegJets": "WPG",
}

# Abbrev aliases
ABBREV_ALIAS: Dict[str, str] = {
    "FP": "FLA", "TB": "TBL", "LA": "LAK", "SJ": "SJS", "NJ": "NJD",
    "MON": "MTL", "VGS": "VGK", "VEG": "VGK", "CLB": "CBJ",
}


def team_to_abbrev(name: object) -> str:
    """Normalize any team label to the canonical 3-letter abbrev.

    Handles nicknames, full names, abbrevs, and numeric outliers (-> '').
    """
    if not isinstance(name, str):
        return ""
    s = " ".join(name.strip().split())
    # direct abbrev match (e.g. 'FLA', 'TOR')
    if len(s) == 3 and s.upper() in ABBREV_ALIAS:
        return ABBREV_ALIAS[s.upper()]
    if len(s) == 3 and s.isupper():
        return s.upper()
    if s in NICK_TO_ABBREV:
        return NICK_TO_ABBREV[s]
    if s.title() in NICK_TO_ABBREV:
        return NICK_TO_ABBREV[s.title()]
    # token-wise fallback: match on last word or whole phrase
    for key, val in NICK_TO_ABBREV.items():
        if s.lower() == key.lower():
            return val
        # multiword exact
        if " " in key and s.lower() == key.lower():
            return val
    # try last-word match
    last = s.split()[-1] if s.split() else ""
    for key, val in NICK_TO_ABBREV.items():
        if key.lower() == last.lower():
            return val
    return s.upper() if len(s) == 3 else s
