"""Market odds ingestion and edge/EV analysis for Hobby Hero.

Consumes game-level betting odds (from the sportsbookreview-scraper
nhl_archive_10Y.json) and computes, for each game:
  - market implied home-win probability from closing moneyline odds
    (de-vigged so probabilities sum to ~1)
  - model predicted home-win probability
  - edge = model_prob - market_prob (positive = value)
  - expected value at given odds
  - simulated ROI of betting every game where edge > threshold

Odds format: American moneyline (e.g. +150 / -120). We convert to
implied probability then de-vig the two-way moneyline.

This is the honest "does the model beat the market?" check the objective
requires. No edge vs the market => no bettable value, regardless of
raw accuracy.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from src.features.teams import team_to_abbrev


def american_to_implied(american: float) -> float:
    """American moneyline -> implied probability (no vig removed)."""
    if american == 0:
        return 0.5
    if american > 0:
        return 100.0 / (american + 100.0)
    return (-american) / (-american + 100.0)


def de_vig(home_p: float, away_p: float) -> tuple[float, float]:
    """Remove vig from a two-way moneyline so probs sum to 1.0."""
    total = home_p + away_p
    if total == 0:
        return 0.5, 0.5
    return home_p / total, away_p / total


def decimal_to_implied(decimal: float) -> float:
    """Decimal odds -> implied probability (with vig)."""
    if decimal <= 1:
        return 0.5
    return 1.0 / decimal


def load_2way_odds_dir(directory: str | Path) -> pd.DataFrame:
    """Load the checkbestodds 2-way decimal moneyline CSVs (one per month).

    Columns: Game_ID, Date, Time, Visitor, Home, Actual_*_Score,
    Home_Odds_Decimal, Away_Odds_Decimal, Actual_Outcome.
    Team names are full nicknames ('Vegas Golden Knights'), which we
    normalize to abbrevs via team_to_abbrev.
    """
    import glob
    rows: List[Dict] = []
    for f in sorted(glob.glob(str(Path(directory) / "*.csv"))):
        with open(f, encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for r in reader:
                try:
                    h = float(r["Home_Odds_Decimal"])
                    a = float(r["Away_Odds_Decimal"])
                except (ValueError, TypeError):
                    continue
                home = team_to_abbrev(r["Home"])
                away = team_to_abbrev(r["Visitor"])
                if not home or not away:
                    continue
                hp = decimal_to_implied(h)
                ap = decimal_to_implied(a)
                dh, da = de_vig(hp, ap)
                try:
                    hf = int(float(r["Actual_Home_Score"]))
                    af = int(float(r["Actual_Visitor_Score"]))
                except (ValueError, TypeError):
                    hf = af = None
                rows.append({
                    "date": r["Date"],
                    "home": home,
                    "away": away,
                    "home_close_ml": h,   # stored as decimal; flagged by source
                    "away_close_ml": a,
                    "market_home_win": dh,
                    "market_away_win": da,
                    "home_final": hf,
                    "away_final": af,
                    "home_win": (1 if (hf is not None and af is not None and hf > af) else None),
                    "source": "checkbestodds_2way",
                })
    df = pd.DataFrame(rows)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df


def load_espn_archive(path: str | Path) -> pd.DataFrame:
    """Load the ESPN 2025-26 closing-ML archive (jsonl) into a clean DataFrame.

    The ESPN archive is written in the same record schema as nhl_archive_10Y.json
    (season, date as yyyymmdd float, home_team/away_team nicknames,
    home/away_open_ml, home/away_close_ml) so this normalizes identically.
    ESPN odds do NOT carry final scores, so home_final/away_final/home_win are
    left NaN here — the actual outcome comes from the boxscore (model) side.
    """
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    df = pd.DataFrame(rows)
    df["season_start"] = df["season"].astype(int)
    df["season_label"] = (df["season_start"].astype(str) + "-" +
                          (df["season_start"] + 1).astype(str))
    def _parse_date(v):
        try:
            s = str(float(v)).split(".")[0]
        except (ValueError, TypeError):
            return pd.NaT
        if len(s) == 8 and s.isdigit():
            return pd.to_datetime(f"{s[:4]}-{s[4:6]}-{s[6:]}")
        return pd.NaT
    df["date"] = df["date"].apply(_parse_date)
    df = df.dropna(subset=["date"])
    df["home"] = df["home_team"].apply(team_to_abbrev)
    df["away"] = df["away_team"].apply(team_to_abbrev)
    df = df[(df["home"] != "") & (df["away"] != "")]  # drop malformed rows
    for c in ["home_open_ml", "away_open_ml", "home_close_ml", "away_close_ml"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    hp = df["home_close_ml"].apply(american_to_implied)
    ap = df["away_close_ml"].apply(american_to_implied)
    dh, da = zip(*[de_vig(h, a) if (h > 0 and a > 0) else (0.5, 0.5)
                   for h, a in zip(hp, ap)])
    df["market_home_win"] = dh
    df["market_away_win"] = da
    df["home_final"] = np.nan
    df["away_final"] = np.nan
    df["home_win"] = np.nan
    return df


def load_odds_archive(path: str | Path) -> pd.DataFrame:
    """Load the nhl_archive_10Y.json into a clean DataFrame."""
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    df = pd.DataFrame(raw)
    # season is stored as starting year (int); normalize to 'YYYY-YYYY'
    df["season_start"] = df["season"].astype(int)
    df["season_label"] = (df["season_start"].astype(str) + "-" +
                          (df["season_start"] + 1).astype(str))
    # parse date: raw value is yyyymmdd stored as a float (e.g. 20200113.0)
    def _parse_date(v):
        try:
            s = str(float(v)).split(".")[0]  # '20200113'
        except (ValueError, TypeError):
            return pd.NaT
        if len(s) == 8 and s.isdigit():
            return pd.to_datetime(f"{s[:4]}-{s[4:6]}-{s[6:]}")
        return pd.NaT
    df["date"] = df["date"].apply(_parse_date)
    df = df.dropna(subset=["date"])
    # build a game key for joining (date + home/away team)
    df["home"] = df["home_team"].apply(team_to_abbrev)
    df["away"] = df["away_team"].apply(team_to_abbrev)
    df = df[(df["home"] != "") & (df["away"] != "")]  # drop malformed rows
    df["home_close_ml"] = pd.to_numeric(df["home_close_ml"], errors="coerce")
    df["away_close_ml"] = pd.to_numeric(df["away_close_ml"], errors="coerce")
    df["home_final"] = pd.to_numeric(df["home_final"], errors="coerce")
    df["away_final"] = pd.to_numeric(df["away_final"], errors="coerce")
    df["home_open_ml"] = pd.to_numeric(df["home_open_ml"], errors="coerce")
    df["away_open_ml"] = pd.to_numeric(df["away_open_ml"], errors="coerce")
    # market implied + de-vigged home win prob
    hp = df["home_close_ml"].apply(american_to_implied)
    ap = df["away_close_ml"].apply(american_to_implied)
    dh, da = zip(*[de_vig(h, a) if (h > 0 and a > 0) else (0.5, 0.5)
                   for h, a in zip(hp, ap)])
    df["market_home_win"] = dh
    df["market_away_win"] = da
    # actual home win
    df["home_win"] = (df["home_final"] > df["away_final"]).astype(int)
    return df


def join_model_to_market(model_preds: pd.DataFrame, odds: pd.DataFrame) -> pd.DataFrame:
    """Join model predictions to market odds on date + home/away team."""
    model_preds = model_preds.copy()
    model_preds["home"] = model_preds["home"].str.upper()
    model_preds["away"] = model_preds["away"].str.upper()
    model_preds["date"] = pd.to_datetime(model_preds["date"]).dt.date

    odds = odds.copy()
    odds["date_d"] = odds["date"].dt.date

    merged = model_preds.merge(
        odds[["date_d", "home", "away", "home_close_ml", "away_close_ml",
              "market_home_win", "market_away_win", "home_win",
              "season_label", "home_final", "away_final"]].rename(
                  columns={"home_win": "market_home_win_actual"}),
        left_on=["date", "home", "away"],
        right_on=["date_d", "home", "away"],
        how="inner",
    )
    # unify the actual outcome: prefer model's home_win (from boxscores),
    # fall back to odds' actual when model's is missing
    if "market_home_win_actual" in merged.columns and "home_win" in merged.columns:
        merged["home_win"] = merged["home_win"].fillna(merged["market_home_win_actual"])
    return merged


def compute_edge(m: pd.DataFrame, prob_col: str = "pred_home_win") -> pd.DataFrame:
    """Compute edge, EV, and whether each game is a bet (edge>threshold)."""
    m = m.copy()
    m["edge"] = m[prob_col] - m["market_home_win"]
    # EV: expected value of betting home at the closing moneyline odds
    # For +odds: profit = odds/100 * stake on win; for -odds stake = 100 to win |odds|
    def ev(prob, american):
        if american > 0:
            return prob * (american / 100.0) - (1 - prob)
        return prob - (1 - prob) * (100.0 / -american)
    m["ev_home"] = [ev(p, a) for p, a in zip(m[prob_col], m["home_close_ml"])]
    m["is_bet"] = m["edge"] > 0.0
    return m


def simulate_flat_bets(m: pd.DataFrame, stake: float = 100.0,
                       min_edge: float = 0.0) -> Dict:
    """Simulate flat betting every game where model edge exceeds min_edge."""
    bets = m[m["edge"] > min_edge].copy()
    if bets.empty:
        return {"n_bets": 0, "roi": 0.0, "profit": 0.0, "wins": 0, "losses": 0,
                "avg_edge": 0.0, "hit_rate": 0.0}
    profits = []
    for _, r in bets.iterrows():
        if r["home_win"] == 1:
            if r["home_close_ml"] > 0:
                profit = stake * (r["home_close_ml"] / 100.0)
            else:
                profit = stake * (100.0 / -r["home_close_ml"])
        else:
            profit = -stake
        profits.append(profit)
    profits = np.array(profits)
    total_staked = stake * len(profits)
    return {
        "n_bets": len(bets),
        "profit": float(profits.sum()),
        "total_staked": float(total_staked),
        "roi": float(profits.sum() / total_staked),
        "wins": int((bets["home_win"] == 1).sum()),
        "losses": int((bets["home_win"] == 0).sum()),
        "hit_rate": float(bets["home_win"].mean()),
        "avg_edge": float(bets["edge"].mean()),
    }


def report_edge(m: pd.DataFrame) -> str:
    if m.empty:
        return ("=== Edge vs market (closing moneyline) ===\n"
                "Games matched to market odds: 0\n"
                "No overlap between model predictions and odds data.")
    # ensure edge/EV columns exist
    if "edge" not in m.columns:
        m = compute_edge(m)
    lines = []
    lines.append("=== Edge vs market (closing moneyline) ===")
    lines.append(f"Games matched to market odds: {len(m)}")
    lines.append(f"Mean model prob:   {m['pred_home_win'].mean():.3f}")
    lines.append(f"Mean market prob:  {m['market_home_win'].mean():.3f}")
    lines.append(f"Mean edge:         {m['edge'].mean():+.4f}")
    lines.append(f"Games with edge>0: {int((m['edge']>0).sum())} / {len(m)}")

    # Threshold sweep
    lines.append("")
    lines.append("Flat-bet simulation (stake=100) by edge threshold:")
    for th in [0.0, 0.02, 0.05, 0.08, 0.10]:
        sim = simulate_flat_bets(m, stake=100.0, min_edge=th)
        lines.append(
            f"  edge>{th:+.2f}: n={sim['n_bets']:4d} "
            f"roi={sim['roi']*100:+6.1f}%  hit={sim['hit_rate']*100:5.1f}%  "
            f"profit=${sim['profit']:+9.0f}"
        )
    return "\n".join(lines)
