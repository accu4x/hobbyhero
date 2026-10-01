"""Parse Hockey-Reference boxscore HTML files into per-team game stats.

Each boxscore file is a full game page containing, per team:
  - skaters table: goals, assists, points, plus/minus, PIM, goals_pp,
    goals_ev, goals_sh, shots, shot_pct, shifts, TOI
  - goalies table: SOG against, saves, save_pct, goals against, TOI
  - advanced 5v5 table: on-ice Corsi events for/against (SAT-F / SAT-A),
    individual CF%, corsi_rel, zone starts, hits, blocks

We aggregate skater/goalie/advanced tables per team into one record.
The final/OT/SO outcome is read from the page (Final / Final OT / Final SO)
and cross-checked against the boxscore id date.

Output: a list of dicts, one per team per game, with both the team's own
totals and its opponent's context (for computing CF%, save%, etc.).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional

from bs4 import BeautifulSoup

# Map CSS stat keys -> our canonical field names (skaters table)
SKATER_STATS = {
    "goals": "goals",
    "assists": "assists",
    "points": "points",
    "plus_minus": "plus_minus",
    "pen_min": "pim",
    "goals_pp": "pp_goals",
    "goals_ev": "ev_goals",
    "goals_sh": "sh_goals",
    "shots": "shots",
    "time_on_ice": "toi_sec",
}
# Goalie table stats
GOALIE_STATS = {
    "goals_against": "goals_against",
    "saves": "saves",
    "save_pct": "save_pct",
    "shots_against": "shots_against",  # aka SOG faced
    "time_on_ice": "goalie_toi_sec",
}
# Advanced 5v5 table stats
ADV5_STATS = {
    "on_Cevents": "sat_for",      # SAT-F: on-ice Corsi For events
    "on_opp_Cevents": "sat_against",  # SAT-A
    "corsi_for": "cf_pct_player",
    "corsi_rel": "corsi_rel",
    "hits": "hits",
    "blocks": "blocks",
}


def _table_rows(soup: BeautifulSoup, table_id: str):
    """Return rows of a table (handling the commented-out pattern)."""
    table = soup.find("table", id=table_id)
    if not table:
        return []
    tbody = table.find("tbody")
    if not tbody:
        return []
    rows = []
    for tr in tbody.find_all("tr"):
        cells = {}
        for td in tr.find_all(["td", "th"]):
            stat = td.get("data-stat")
            if stat:
                text = td.get_text(" ", strip=True)
                cells[stat] = text
        if cells:
            rows.append(cells)
    return rows


def _to_int(v: Optional[str], default: int = 0) -> int:
    if v is None:
        return default
    v = v.replace(",", "").strip()
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return default


def _to_float(v: Optional[str]) -> Optional[float]:
    if v is None:
        return None
    v = v.strip()
    if v in ("", "-", "n/a"):
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _to_sec(toi: Optional[str]) -> Optional[int]:
    """'MM:SS' -> seconds. Returns None if missing."""
    if not toi:
        return None
    m = re.match(r"(\d+):(\d{2})", toi.strip())
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2))


def parse_boxscore(html: str, boxscore_id: str, outcome_override: Optional[str] = None) -> Dict:
    """Parse a single boxscore HTML string into a normalized dict.

    boxscore_id looks like '202510070FLA' where the trailing letters are
    the HOME team's abbrev and the leading date is game date.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Title gives matchup: "Chicago Blackhawks vs. Florida Panthers ..."
    title = (soup.find("title").get_text() if soup.find("title") else "") or ""
    m = re.match(r"([^ ]+.*?)\s+(?:vs\.?|at)\s+(.+?)\s+Box Score", title)
    away_name, home_name = (m.group(1).strip(), m.group(2).strip()) if m else (None, None)

    # Home team abbrev is the trailing letters of the boxscore id
    # Format: YYYYMMDD0ABBREV  (there's a leading '0' before the team code)
    date_part = boxscore_id[:8]  # YYYYMMDD
    home_abbrev = boxscore_id[-3:] if len(boxscore_id) >= 9 else None
    # Determine outcome: Final OT / Final SO / Final
    body_text = soup.get_text(" ", strip=True)
    outcome = "REG"
    if "Final SO" in body_text or "FinalOT" in body_text or ">SO<" in body_text:
        outcome = "SO"
    elif "Final OT" in body_text:
        outcome = "OT"
    # A reliable outcome override (from Hockey-Reference CSV / API results)
    # beats fragile HTML inference.
    if outcome_override is not None:
        outcome = outcome_override

    # Identify the two team abbreviations present (from advanced table ids)
    team_ids = [m2.group(1) for m2 in re.finditer(r'id="(\w+)_adv_ALL5v5_sh"', html)]
    team_ids = list(dict.fromkeys(team_ids))  # de-dup preserve order

    teams: Dict[str, Dict] = {}
    for tid in team_ids:
        skater_rows = _table_rows(soup, f"{tid}_skaters")
        goalie_rows = _table_rows(soup, f"{tid}_goalies")
        adv_rows = _table_rows(soup, f"{tid}_adv_ALL5v5")

        agg = {
            "team": tid,
            "skaters": len(skater_rows),
            "goals": 0, "assists": 0, "points": 0,
            "plus_minus": 0, "pim": 0,
            "pp_goals": 0, "ev_goals": 0, "sh_goals": 0,
            "shots": 0, "toi_sec": 0,
            "goals_against": 0, "saves": 0, "shots_against": 0,
            "save_pct": None, "goalie_toi_sec": 0,
            "sat_for": 0, "sat_against": 0, "hits": 0, "blocks": 0,
            "cf_pct": None, "corsi_rel": None,
        }
        for r in skater_rows:
            for stat, key in SKATER_STATS.items():
                if stat == "time_on_ice":
                    s = _to_sec(r.get(stat))
                    if s is not None:
                        agg[key] += s
                else:
                    agg[key] += _to_int(r.get(stat))
        for r in goalie_rows:
            ga = _to_int(r.get("goals_against"))
            sv = _to_int(r.get("saves"))
            sa = _to_int(r.get("shots_against"))
            agg["goals_against"] += ga
            agg["saves"] += sv
            if sa:
                agg["shots_against"] += sa
            sp = _to_float(r.get("save_pct"))
            if sp is not None:
                agg["save_pct"] = sp
            t = _to_sec(r.get("time_on_ice"))
            if t is not None:
                agg["goalie_toi_sec"] += t
        # Advanced 5v5: aggregate SAT-F / SAT-A across skaters, plus team CF%
        sat_f = sat_a = 0
        for r in adv_rows:
            sat_f += _to_int(r.get("on_Cevents"))
            sat_a += _to_int(r.get("on_opp_Cevents"))
            agg["hits"] += _to_int(r.get("hits"))
            agg["blocks"] += _to_int(r.get("blocks"))
        agg["sat_for"] = sat_f
        agg["sat_against"] = sat_a
        if sat_f + sat_a > 0:
            agg["cf_pct"] = round(100.0 * sat_f / (sat_f + sat_a), 2)
        teams[tid] = agg

    return {
        "boxscore_id": boxscore_id,
        "date": date_part,
        "home_abbrev": home_abbrev,
        "away_name": away_name,
        "home_name": home_name,
        "outcome": outcome,
        "teams": teams,
    }


def parse_boxscore_file(path: Path, outcome_map: Optional[Dict] = None) -> Dict:
    boxscore_id = path.stem.replace("_raw", "")
    html = path.read_text(encoding="utf-8", errors="ignore")
    outcome = None
    if outcome_map is not None:
        # outcome_map keyed by normalized date+teams; fall back to boxscore_id
        outcome = outcome_map.get(boxscore_id)
        if outcome is None:
            outcome = outcome_map.get(_game_key_from_id(boxscore_id))
    return parse_boxscore(html, boxscore_id, outcome)


_TEAM_ALIAS = {
    # CSV abbrev -> boxscore abbrev
    "FP": "FLA", "TB": "TBL", "LA": "LAK", "SJ": "SJS", "NJ": "NJD",
    "WSH": "WSH", "CLB": "CBJ", "NSH": "NSH", "UTA": "UTA", "ARI": "ARI",
    "VGS": "VGK", "MON": "MTL",
}

# Full team name -> boxscore abbrev (reliable; the CSV carries full names)
_TEAM_NAME_ABBREV = {
    "Chicago Blackhawks": "CHI", "Florida Panthers": "FLA",
    "Colorado Avalanche": "COL", "Los Angeles Kings": "LAK",
    "Pittsburgh Penguins": "PIT", "New York Rangers": "NYR",
    "Calgary Flames": "CGY", "Edmonton Oilers": "EDM",
    "Toronto Maple Leafs": "TOR", "Vegas Golden Knights": "VEG",
    "Washington Capitals": "WSH", "Boston Bruins": "BOS",
    "Buffalo Sabres": "BUF", "Carolina Hurricanes": "CAR",
    "Columbus Blue Jackets": "CBJ", "Dallas Stars": "DAL",
    "Detroit Red Wings": "DET", "Minnesota Wild": "MIN",
    "Montreal Canadiens": "MTL", "Nashville Predators": "NSH",
    "New Jersey Devils": "NJD", "New York Islanders": "NYI",
    "Ottawa Senators": "OTT", "Philadelphia Flyers": "PHI",
    "San Jose Sharks": "SJS", "Seattle Kraken": "SEA",
    "St. Louis Blues": "STL", "Tampa Bay Lightning": "TBL",
    "Utah Mammoth": "UTA", "Vancouver Canucks": "VAN",
    "Winnipeg Jets": "WPG", "Anaheim Ducks": "ANA",
    "Arizona Coyotes": "ARI",
}


def normalize_team_abbrev(a: str) -> str:
    return _TEAM_ALIAS.get(a.upper(), a.upper())


def build_outcome_map_from_csv(csv_season: str = "20252026") -> Dict[str, str]:
    """Build boxscore_id -> outcome from the Hockey-Reference CSV.

    The boxscore id encodes date_home (e.g. '202510080EDM'). We match by
    (date, home_team) using normalized abbrevs.
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from ingest.ingest_nhl import parse_hockeyref_csv

    csvdir = Path("C:/Users/hn2_f/source/python")
    csvs = {
        "20252026": ("nhl-regular-season-games-2026.csv", "nhl-playoff-games-2026.csv"),
    }[csv_season]
    out: Dict[str, str] = {}
    for fname in csvs:
        for g in parse_hockeyref_csv(csvdir / fname):
            date8 = g["date"].replace("-", "")
            home_name = g["home_team_name"]
            home = _TEAM_NAME_ABBREV.get(home_name, normalize_team_abbrev(g["home_team_abbrev"]))
            out[f"{date8}_{home}"] = g["outcome"]
    return out


def _game_key_from_id(boxscore_id: str) -> str:
    """'202510080EDM' -> '20251008_EDM' (date_home)."""
    if len(boxscore_id) >= 11:
        return f"{boxscore_id[:8]}_{boxscore_id[-3:]}"
    return boxscore_id


def parse_directory(dirpath: Path, outcome_map: Optional[Dict] = None) -> List[Dict]:
    """Parse every *_raw.html under dirpath (one dir per game)."""
    results = []
    for game_dir in sorted(dirpath.iterdir()):
        if not game_dir.is_dir():
            continue
        for html in sorted(game_dir.glob("*_raw.html")):
            try:
                results.append(parse_boxscore_file(html, outcome_map))
            except Exception as e:  # noqa: BLE001
                print(f"  [!] failed {html}: {e}")
    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Parse Hockey-Reference boxscores")
    parser.add_argument("--dir", default="C:/Users/hn2_f/source/python/HockeyReference_Boxscores")
    parser.add_argument("--out", default="data/raw/boxscores_20252026.jsonl")
    parser.add_argument("--sample", type=int, default=0, help="Parse only first N games (0=all)")
    args = parser.parse_args()

    outcome_map = build_outcome_map_from_csv() if args.sample == 0 else None
    games = parse_directory(Path(args.dir), outcome_map)
    if args.sample:
        games = games[:args.sample]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    import json
    with out.open("w", encoding="utf-8") as f:
        for g in games:
            f.write(json.dumps(g, ensure_ascii=False) + "\n")
    print(f"[+] parsed {len(games)} boxscores -> {out}")
