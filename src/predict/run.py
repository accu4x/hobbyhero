"""Daily prediction runner for Hobby Hero.

Trains on all historical games (strictly prior), then predicts the next
day's scheduled games. Outputs a human-readable report and (optionally)
a CSV of predictions.

Usage:
    python -m src.predict.run --date 2025-11-01
    python -m src.predict.run --next-day   # predict the nearest future games
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from src.features import build_features, dataset
from src.models import models

RAW_DIR = Path("data/raw")
MODEL_DIR = Path("data/models")
REPORT_DIR = Path("reports")


def _future_games(from_date: date, games: pd.DataFrame) -> pd.DataFrame:
    """Return games strictly after from_date that are not yet finalized."""
    # In this milestone we use historical finalized games for testing, so we
    # just filter to the requested date window from the raw set.
    d = pd.to_datetime(from_date)
    return games[games["date"].dt.date == from_date]


def predict_day(df: pd.DataFrame, target_day: date) -> pd.DataFrame:
    """Build features for the full frame, train on games before target_day,
    and predict games on target_day. Returns a frame of predictions."""
    # Build features over the entire frame (needed for trailing stats of
    # teams playing on target_day).
    feats = build_features.build_feature_frame(df)
    feature_cols = models.select_features(feats)
    feats = feats.dropna(subset=feature_cols)

    train = feats[feats["date"].dt.date < target_day]
    target = feats[feats["date"].dt.date == target_day]
    if target.empty:
        raise ValueError(f"No games found on {target_day}")

    ml = models.train_moneyline(train, feature_cols)
    margin_m = models.train_margin(train, feature_cols)
    p_ml = models.predict_moneyline(ml, target, feature_cols)
    p_margin = models.predict_margin(margin_m, target, feature_cols)

    target = target.copy()
    target["pred_home_win_prob"] = p_ml
    target["pred_margin"] = p_margin
    return target


def format_report(pred: pd.DataFrame) -> str:
    lines = []
    lines.append("=== Hobby Hero Predictions ===")
    lines.append(f"Date: {pred['date'].dt.date.iloc[0]}")
    for _, r in pred.sort_values("date").iterrows():
        hw = r["pred_home_win_prob"]
        line = (f"{r['away']:>3} @ {r['home']:<3}  "
                f"home win {hw*100:5.1f}%  (margin {r['pred_margin']:+.2f})")
        lines.append(line)
    lines.append("")
    lines.append("Note: probabilities are from features strictly before game time.")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Predict a day of NHL games.")
    parser.add_argument("--date", help="Target date YYYY-MM-DD")
    parser.add_argument("--next-day", action="store_true",
                        help="Predict the nearest game day after the last finalized game")
    parser.add_argument("--outdir", default="reports")
    args = parser.parse_args()

    games = dataset.load_games(RAW_DIR)
    if args.next_day:
        last = games["date"].max().date()
        target = last + timedelta(days=1)
        # find a day with scheduled (non-final) games
        while target < date.today() + timedelta(days=14):
            cand = dataset.load_games(RAW_DIR)
            if (cand["date"].dt.date == target).any():
                break
            target += timedelta(days=1)
    elif args.date:
        target = date.fromisoformat(args.date)
    else:
        parser.error("provide --date or --next-day")

    pred = predict_day(games, target)
    text = format_report(pred)
    print(text)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / f"predictions_{target.isoformat()}.txt").write_text(text, encoding="utf-8")
    pred.to_csv(outdir / f"predictions_{target.isoformat()}.csv", index=False)
    print(f"[+] saved to {outdir / 'predictions_' + target.isoformat()}.csv")


if __name__ == "__main__":
    main()
