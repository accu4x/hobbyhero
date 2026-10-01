"""Feature engineering for Hobby Hero.

Produces one row per game with team-strength features computed from
STRICTLY PRIOR games only (no look-ahead). All team statistics are
rolling windows over games that finished before the current game's
start time.

Features are computed per team (home and away) and then the away
features are subtracted from the home features to get matchup deltas
that are meaningful for a head-to-head model.

Leakage guard: for each game, a team's feature window only includes
games with date < this game's date. Equal-date games are excluded too
(for robustness we compare by a game-sequence index, not just date).
"""

from __future__ import annotations

from typing import List

import numpy as np
import pandas as pd

# Rolling window sizes (in games) for team form.
WINDOWS = [5, 10, 20]


def _team_event_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Explode the game frame into one row per team appearance.

    Each game yields two rows: one for the away team and one for the home
    team, tagged with whether it was home, the result, goals for/against,
    and a global game sequence index (for correct temporal ordering).
    """
    events: List[dict] = []
    seq = {gid: i for i, gid in enumerate(df["game_id"])}

    for _, r in df.iterrows():
        gid = r["game_id"]
        # away team row
        events.append({
            "game_id": gid,
            "seq": seq[gid],
            "date": r["date"],
            "season": r["season"],
            "team": r["away"],
            "is_home": 0,
            "opp": r["home"],
            "gf": int(r["away_score"]),
            "ga": int(r["home_score"]),
            "won": 1 if r["away_score"] > r["home_score"] else 0,
            "is_reg": 1 if (r["outcome"] == "REG" and r["away_score"] > r["home_score"]) else 0,
            "is_ot_loss": 1 if (r["outcome"] != "REG" and r["away_score"] < r["home_score"]) else 0,
            "is_so_win": 1 if (r["outcome"] == "SO" and r["away_score"] > r["home_score"]) else 0,
        })
        # home team row
        events.append({
            "game_id": gid,
            "seq": seq[gid],
            "date": r["date"],
            "season": r["season"],
            "team": r["home"],
            "is_home": 1,
            "opp": r["away"],
            "gf": int(r["home_score"]),
            "ga": int(r["away_score"]),
            "won": 1 if r["home_score"] > r["away_score"] else 0,
            "is_reg": 1 if (r["outcome"] == "REG" and r["home_score"] > r["away_score"]) else 0,
            "is_ot_loss": 1 if (r["outcome"] != "REG" and r["home_score"] < r["away_score"]) else 0,
            "is_so_win": 1 if (r["outcome"] == "SO" and r["home_score"] > r["away_score"]) else 0,
        })
    return pd.DataFrame(events)


def _rolling_windows(events: pd.DataFrame, windows: List[int]) -> pd.DataFrame:
    """Compute trailing-window features per team appearance (no leakage).

    Returns a DataFrame keyed by game_id x team with columns like
    win_rate_w5, gf_per_game_w5, ga_per_game_w5, plus rest days.
    """
    events = events.sort_values(["team", "seq"]).reset_index(drop=True)

    # per-team numeric event matrix, aligned by seq
    teams = sorted(events["team"].unique())
    rows_out: List[dict] = []

    # Precompute a mapping team -> sorted list of prior appearances with metrics
    team_map: dict[str, List[dict]] = {}
    for team in teams:
        sub = events[events["team"] == team].sort_values("seq")
        team_map[team] = sub.to_dict("records")

    for _, r in events.iterrows():
        team = r["team"]
        seq = r["seq"]
        # prior appearances for this team (strictly before this game)
        prior = [p for p in team_map[team] if p["seq"] < seq]
        row = {"game_id": r["game_id"], "team": team}

        # rest days: gap since the most recent prior game
        if prior:
            last_date = max(p["date"] for p in prior)
            row["rest_days"] = max(0.0, (r["date"] - last_date).total_seconds() / 86400.0)
            row["has_prior"] = 1
        else:
            row["rest_days"] = np.nan
            row["has_prior"] = 0

        for w in windows:
            win = [p for p in prior if p["seq"] >= seq - w]
            win = sorted(win, key=lambda p: p["seq"])
            if not win:
                row[f"win_rate_w{w}"] = np.nan
                row[f"gf_per_game_w{w}"] = np.nan
                row[f"ga_per_game_w{w}"] = np.nan
                row[f"reg_win_rate_w{w}"] = np.nan
                row[f"ot_loss_rate_w{w}"] = np.nan
                row[f"so_win_rate_w{w}"] = np.nan
                row[f"games_w{w}"] = 0
                continue
            row[f"win_rate_w{w}"] = np.mean([p["won"] for p in win])
            row[f"gf_per_game_w{w}"] = np.mean([p["gf"] for p in win])
            row[f"ga_per_game_w{w}"] = np.mean([p["ga"] for p in win])
            row[f"reg_win_rate_w{w}"] = np.mean([p["is_reg"] for p in win])
            row[f"ot_loss_rate_w{w}"] = np.mean([p["is_ot_loss"] for p in win])
            row[f"so_win_rate_w{w}"] = np.mean([p["is_so_win"] for p in win])
            row[f"games_w{w}"] = len(win)
        rows_out.append(row)

    return pd.DataFrame(rows_out)


def build_feature_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Build the full feature frame, one row per game.

    Returns a DataFrame with game identity columns plus matchup features
    (home minus away deltas) and the target variable.
    """
    events = _team_event_frame(df)
    team_feats = _rolling_windows(events, WINDOWS)

    # split into home and away feature sets, then merge onto game rows
    feats = df[["game_id", "season", "date", "away", "home",
                "away_score", "home_score", "outcome", "game_type"]].copy()

    home_feats = team_feats[team_feats["team"].isin(feats["home"])].copy()
    away_feats = team_feats[team_feats["team"].isin(feats["away"])].copy()

    home_merge = home_feats.merge(
        feats[["game_id", "home"]].rename(columns={"home": "team"}), on=["game_id", "team"])
    away_merge = away_feats.merge(
        feats[["game_id", "away"]].rename(columns={"away": "team"}), on=["game_id", "team"])

    feat_cols = [c for c in team_feats.columns if c not in ("game_id", "team")]

    # delta = home - away
    delta = pd.DataFrame({"game_id": home_merge["game_id"]})
    for c in feat_cols:
        delta[f"{c}_delta"] = home_merge[c].values - away_merge[c].values

    feats = feats.merge(delta, on="game_id", how="left")

    # target: home team won the game (binary, moneyline)
    feats["home_win"] = (feats["home_score"] > feats["away_score"]).astype(int)
    # target: home win margin (puck-line)
    feats["margin"] = feats["home_score"] - feats["away_score"]
    # moneyline excludes push? In hockey there is no tie in a final game.
    feats = feats.dropna(subset=["home_win"])
    return feats
