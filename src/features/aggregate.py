"""Point-in-time weighted feature aggregator for Hobby Hero.

Consumes per-team per-game stats (from boxscores and/or API) and builds
one row per game with team features computed from STRICTLY PRIOR games.

Two aggregation modes:
  - rolling window: mean over the last N games (windows configurable)
  - exponential recency weight: recent games weighted more (half-life in
    games)

Leakage guarantee: for a game at index i, only games with index < i for
that team are included. Each game gets a global sequence index so ordering
is exact (date tie-break is handled by the ingestion layer).

Derived per-game team metrics (the user's requested feature set):
  S/G   = shots for per game        (team shots)
  SA/G  = shots against per game    (opponent's shots = goalie shots_against)
  PIM/G = penalty minutes per game
  S%    = shooting % = goals / shots
  SAV%  = save % = saves / shots_against (from goalie)
  PDO   = S% + (1 - opponent shooting%) -- team PDO proxy
  CF%   = corsi for % 5v5 = sat_for / (sat_for + sat_against)
  PP%   = power play efficiency (pp_goals / pp_opportunities) [needs opp]
  PK%   = penalty kill efficiency
  xG    = not available from boxscores (skipped by design)

Note on PP%/PK%: boxscores give pp_goals and sh_goals but NOT PP
opportunities directly. We approximate opportunities from penalties:
  PP opportunities for a team ~= opponent PIM / 2 (a 2-min minor)
  PK opportunities for a team ~= team PIM / 2
These are approximations; we compute and flag them as such.
"""

from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

# Rolling window sizes (in games) for team form.
WINDOWS = [5, 10, 20, 40]

# Exponential decay half-life in games (recency weight).
HALF_LIFE_GAMES = 10


def _to_pct(x: float) -> float:
    return 100.0 * x


def build_team_event_frame(boxscore_records: List[Dict]) -> pd.DataFrame:
    """Explode parsed boxscore records into one row per team appearance.

    Each game record has 'teams': {TEAM: {...stats}, OPP: {...stats}},
    'date', 'home_abbrev', 'outcome'. Produces rows keyed by game sequence.
    """
    rows: List[Dict] = []
    # assign a global sequence per game based on date order
    recs = sorted(boxscore_records, key=lambda r: r["date"])
    seq = 0
    for r in recs:
        teams = r.get("teams", {})
        home = r.get("home_abbrev")
        date = r.get("date", "")
        outcome = r.get("outcome", "REG")
        for team, s in teams.items():
            opp = [o for o in teams if o != team]
            opp_stats = teams[opp[0]] if opp else {}
            is_home = 1 if team == home else 0

            # opponent shooting % (for PDO / save context)
            opp_shots = opp_stats.get("shots", 0) or 0
            opp_goals = opp_stats.get("goals", 0) or 0
            opp_s_percent = (opp_goals / opp_shots) if opp_shots else 0.0

            team_shots = s.get("shots", 0) or 0
            team_goals = s.get("goals", 0) or 0
            saves = s.get("saves", 0) or 0
            shots_against = s.get("shots_against", 0) or 0
            sat_for = s.get("sat_for", 0) or 0
            sat_against = s.get("sat_against", 0) or 0

            pp_goals = s.get("pp_goals", 0) or 0
            sh_goals = s.get("sh_goals", 0) or 0
            pim = s.get("pim", 0) or 0
            opp_pim = opp_stats.get("pim", 0) or 0

            # New enriched fields from NHL API (takeaways/giveaways/blocks/hits/shifts)
            takeaways = s.get("takeaways", 0) or 0
            giveaways = s.get("giveaways", 0) or 0
            blocked_shots = s.get("blocked_shots", 0) or 0
            hits = s.get("hits", 0) or 0
            shifts = s.get("shifts", 0) or 0

            # shooting % and save %
            s_pct = (team_goals / team_shots) if team_shots else 0.0
            # Save % over the goalies' own shots against when the record carries them
            # (NHL API files), so empty-net shots don't count against the goalie.
            goalie_sa = s.get("goalie_shots_against") or shots_against
            sv_pct = (saves / goalie_sa) if goalie_sa else 0.0
            pdo = s_pct + (1.0 - opp_s_percent)
            cf_pct = (sat_for / (sat_for + sat_against)) if (sat_for + sat_against) else 0.5

            # PP%/PK% : prefer REAL opportunities from NHL API; fall back to PIM/2 approx.
            pp_opps = s.get("pp_opportunities")
            pk_opps = s.get("pk_opportunities")
            if not pp_opps:
                pp_opps = max(1, round(opp_pim / 2))
            if not pk_opps:
                pk_opps = max(1, round(pim / 2))
            pp_pct = (pp_goals / pp_opps) if pp_opps else 0.0
            pk_pct = 1.0 - (sh_goals / pk_opps) if pk_opps else 1.0

            # Prefer the official result when the record carries it (NHL API files
            # include the shootout winner). Older files fall back to goals, which
            # counts every shootout as a loss for both teams (OPEN-ITEMS item 13).
            won = int(s["won"]) if "won" in s else int(team_goals > opp_goals)
            final_score = s.get("final_score", team_goals)

            rows.append({
                "seq": seq,
                "game": r.get("boxscore_id"),
                "date": date,
                "team": team,
                "is_home": is_home,
                "opp": opp[0] if opp else None,
                "won": won,
                "goals": team_goals,
                "final_score": final_score,
                "shots": team_shots,
                "shots_against": shots_against,
                "pim": pim,
                "s_pct": s_pct,
                "sv_pct": sv_pct,
                "pdo": pdo,
                "cf_pct": cf_pct,
                "pp_pct": pp_pct,
                "pk_pct": pk_pct,
                "sat_for": sat_for,
                "sat_against": sat_against,
                "takeaways": takeaways,
                "giveaways": giveaways,
                "blocked_shots": blocked_shots,
                "hits": hits,
                "shifts": shifts,
                "outcome": outcome,
            })
        seq += 1
    return pd.DataFrame(rows)


def _exp_weight(games_ago: int, half_life: int = HALF_LIFE_GAMES) -> float:
    return 0.5 ** (games_ago / half_life)


def _weighted_agg(prior: List[Dict], key: str) -> Optional[float]:
    """Exponentially-weighted mean of a metric over prior games."""
    if not prior:
        return None
    total_w = 0.0
    acc = 0.0
    for i, p in enumerate(prior):
        w = _exp_weight(len(prior) - 1 - i)
        v = p.get(key)
        if v is None or np.isnan(v):
            continue
        acc += v * w
        total_w += w
    return (acc / total_w) if total_w else None


def _rolling_mean(prior: List[Dict], key: str, window: int) -> Optional[float]:
    if not prior:
        return None
    vals = [p.get(key) for p in prior[-window:]]
    vals = [v for v in vals if v is not None and not np.isnan(v)]
    if not vals:
        return None
    return float(np.mean(vals))


def build_feature_frame(events: pd.DataFrame) -> pd.DataFrame:
    """Build per-game feature rows (one row per game, home-away delta).

    For each game appearance, compute team features from prior games
    (both rolling windows and exponential weighting), then produce a
    matchup row of home minus away deltas plus the target.
    """
    # map team -> list of prior appearances (sorted by seq)
    team_prior: Dict[str, List[Dict]] = {}
    for _, r in events.iterrows():
        team_prior.setdefault(r["team"], []).append(r.to_dict())

    game_feats: Dict[str, Dict] = {}  # boxscore_id -> row
    game_rows: Dict[str, Dict] = {}

    for _, r in events.iterrows():
        team = r["team"]
        game = r["game"]
        # prior appearances strictly before this game (exclude current seq)
        prior = [p for p in team_prior[team] if p["seq"] < r["seq"]]
        # ensure sorted by seq (already are)

        feats: Dict[str, Optional[float]] = {}
        base = {
            "won": "win_rate",
            "s_pct": "s_pct",
            "sv_pct": "sv_pct",
            "pdo": "pdo",
            "cf_pct": "cf_pct",
            "pp_pct": "pp_pct",
            "pk_pct": "pk_pct",
            "shots": "shots_per_game",
            "shots_against": "shots_against_per_game",
            "pim": "pim_per_game",
            "sat_for": "sat_for_per_game",
            "sat_against": "sat_against_per_game",
            "takeaways": "takeaways_per_game",
            "giveaways": "giveaways_per_game",
            "blocked_shots": "blocked_shots_per_game",
            "hits": "hits_per_game",
            "shifts": "shifts_per_game",
        }
        for src_key, base_name in base.items():
            for w in WINDOWS:
                feats[f"{base_name}_w{w}"] = _rolling_mean(prior, src_key, w)
            feats[f"{base_name}_exp"] = _weighted_agg(prior, src_key)

        # rest days
        if prior:
            last_date = pd.to_datetime(str(prior[-1]["date"]))
            cur_date = pd.to_datetime(str(r["date"]))
            feats["rest_days"] = max(0.0, (cur_date - last_date).total_seconds() / 86400.0)
        else:
            feats["rest_days"] = None

        # store per-team features indexed by (game, team)
        game_rows.setdefault(game, {})[team] = feats
        if game not in game_feats:
            game_feats[game] = {
                "date": r["date"],
                "home": r["team"] if r["is_home"] else r["opp"],
                "away": r["opp"] if r["is_home"] else r["team"],
                "home_score": None,
                "away_score": None,
            }
        if r["is_home"]:
            game_feats[game]["home_score"] = r["final_score"]
        else:
            game_feats[game]["away_score"] = r["final_score"]

    # build delta rows
    out = []
    for game, meta in game_feats.items():
        home = meta["home"]
        away = meta["away"]
        hf = game_rows[game].get(home, {})
        af = game_rows[game].get(away, {})
        row = {
            "game": game,
            "date": meta["date"],
            "home": home,
            "away": away,
            "home_score": meta["home_score"],
            "away_score": meta["away_score"],
        }
        all_keys = set(hf) | set(af)
        for k in all_keys:
            hv = hf.get(k)
            av = af.get(k)
            if hv is None or av is None:
                row[f"{k}_delta"] = None
            else:
                row[f"{k}_delta"] = hv - av
            row[f"{k}_home"] = hv
            row[f"{k}_away"] = av
        if meta["home_score"] is not None and meta["away_score"] is not None:
            row["home_win"] = 1 if meta["home_score"] > meta["away_score"] else 0
            row["margin"] = meta["home_score"] - meta["away_score"]
        else:
            row["home_win"] = None
            row["margin"] = None
        out.append(row)

    return pd.DataFrame(out)


def select_feature_columns(df: pd.DataFrame) -> List[str]:
    """Return delta feature columns (home minus away) to feed the model."""
    feats = [c for c in df.columns if c.endswith("_delta")]
    return sorted(feats)
