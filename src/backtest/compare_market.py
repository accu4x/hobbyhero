"""Score the model against the closing market on the same games (2026-09-24).

Milestone 2 compared model and market with log loss and Brier on the matched
games. The --espn-edge report prints only mean probabilities and a flat-bet
table, so this reproduces the calibration comparison for any data source:

    python -m src.backtest.compare_market --data nhl_api --out reports/<name>.txt

It prints log loss, Brier and accuracy for the model, the de-vigged closing
market and a naive "home team wins" baseline, all on the same matched games.
The market is a benchmark only (hobbyhero/CLAUDE.md, no betting).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

import run_pipeline as rp
from src.backtest import backtest, market_edge

ROOT = Path(__file__).resolve().parents[2]
EPS = 1e-6


def _scores(p: np.ndarray, y: np.ndarray) -> dict[str, float]:
    p = np.clip(p, EPS, 1 - EPS)
    return {
        "log_loss": float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))),
        "brier": float(np.mean((p - y) ** 2)),
        "accuracy": float(np.mean((p >= 0.5) == (y == 1))),
        "mean_p": float(np.mean(p)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data", choices=["legacy", "nhl_api"], default="nhl_api")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    rp._DATA_SOURCE = args.data
    feats, cols = rp._build_feature_frame(rp.load_parsed_boxscores())
    oof = backtest.walk_forward_moneyline(feats, cols)["oof"]
    for col in ("home", "away"):
        oof[col] = oof[col].map(lambda a: "VGK" if a == "VEG" else a)
    espn = market_edge.load_espn_archive(
        ROOT / "data/raw/odds/espn/nhl_archive_2025_26.jsonl")
    m = market_edge.join_model_to_market(oof, espn).dropna(
        subset=["pred_home_win", "market_home_win", "home_win"])
    y = m["home_win"].to_numpy(dtype=float)

    rows = {
        "model": _scores(m["pred_home_win"].to_numpy(dtype=float), y),
        "closing market": _scores(m["market_home_win"].to_numpy(dtype=float), y),
        "home team always": _scores(np.full(len(y), 0.5 + EPS * 10), y),
    }
    lines = [
        f"=== Model vs closing market, same games (data={args.data}) ===",
        f"Out-of-fold test games: {len(oof)} | matched to ESPN closing lines: {len(m)}",
        f"Date range: {m['date'].min()} to {m['date'].max()}",
        f"Observed home win rate: {y.mean():.3f}",
        "",
        f"{'':18}{'log loss':>10}{'Brier':>9}{'accuracy':>10}{'mean p':>9}",
    ]
    for name, s in rows.items():
        lines.append(f"{name:18}{s['log_loss']:>10.4f}{s['brier']:>9.4f}"
                     f"{s['accuracy']:>10.1%}{s['mean_p']:>9.3f}")
    lines += ["", "Lower log loss / Brier is better. 'home team always' predicts 0.5 "
              "(ties go to home) for log loss and Brier, and home for accuracy."]
    text = "\n".join(lines)
    print(text)
    Path(args.out).write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
