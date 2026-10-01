"""Hobby Hero pipeline entry point.

Runs the full pipeline:
  1. Ingest (schedule + CSV)
  2. Build features
  3. Backtest (moneyline + margin, walk-forward)
  4. (Optional) Predict next day
  5. Write report

Usage:
    python -m src.main --backtest
    python -m src.main --predict --next-day
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from src.backtest import backtest
from src.features import build_features, dataset
from src.models import models

RAW_DIR = Path("data/raw")
REPORT_DIR = Path("reports")


def run_backtest() -> str:
    games = dataset.load_games(RAW_DIR)
    feats = build_features.build_feature_frame(games)
    feature_cols = models.select_features(feats)
    feats = feats.dropna(subset=feature_cols)
    print(f"[i] Feature frame: {len(feats)} rows, {len(feature_cols)} features")

    ml = backtest.walk_forward_moneyline(feats, feature_cols)
    margin = backtest.walk_forward_margin(feats, feature_cols)
    return "\n\n".join([backtest.report_moneyline(ml), backtest.report_margin(margin)])


def main() -> None:
    parser = argparse.ArgumentParser(description="Hobby Hero pipeline")
    parser.add_argument("--backtest", action="store_true", help="Run walk-forward backtest")
    parser.add_argument("--predict", action="store_true", help="Predict a day")
    parser.add_argument("--date", help="Predict this date YYYY-MM-DD")
    parser.add_argument("--next-day", action="store_true")
    parser.add_argument("--out", default=str(REPORT_DIR / "pipeline_report.txt"))
    args = parser.parse_args()

    if args.backtest:
        text = run_backtest()
        print(text)
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"[+] report -> {args.out}")
    elif args.predict:
        from src.predict.run import predict_day, format_report
        games = dataset.load_games(RAW_DIR)
        if args.next_day:
            target = games["date"].max().date()
            # find next day with games
            from datetime import timedelta
            t = target
            for _ in range(14):
                t += timedelta(days=1)
                if (games["date"].dt.date == t).any():
                    break
            target = t
        else:
            target = date.fromisoformat(args.date)
        pred = predict_day(games, target)
        print(format_report(pred))
    else:
        parser.error("need --backtest or --predict")


if __name__ == "__main__":
    main()
