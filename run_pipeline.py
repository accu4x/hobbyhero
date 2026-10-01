"""Hobby Hero pipeline runner.

Handles the src path automatically so you can run from the project root:

    python run_pipeline.py --backtest
    python run_pipeline.py --predict --next-day
    python run_pipeline.py --parse-boxscores
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

# External dependency: the HockeyReference boxscore HTML lives OUTSIDE this repo.
# Config from env, not hardcoded (python-conventions.md). The default preserves the
# previous hardcoded location so existing runs are unaffected.
DEFAULT_BOXSCORE_DIR = Path("C:/Users/hn2_f/source/python/HockeyReference_Boxscores")
BOXSCORE_DIR = Path(os.environ.get("HOBBYHERO_BOXSCORE_DIR", DEFAULT_BOXSCORE_DIR))

from src.backtest import backtest  # noqa: E402
from src.backtest import market_edge  # noqa: E402
from src.features import aggregate, dataset  # noqa: E402
from src.models import models  # noqa: E402


# Which game data the runs read (--data). "legacy" is the pre-2026-09-24 behaviour.
# "nhl_api" reads every data/raw/nhl_api/boxscores_<season>.jsonl written by
# src/ingest/nhl_api_season.py (OPEN-ITEMS items 13-17).
_DATA_SOURCE = "legacy"


def load_nhl_api_boxscores() -> "list[dict]":
    import json
    recs = []
    for path in sorted((ROOT / "data" / "raw" / "nhl_api").glob("boxscores_*.jsonl")):
        with path.open(encoding="utf-8") as fh:
            recs.extend(json.loads(line) for line in fh if line.strip())
    return recs


def load_parsed_boxscores() -> "list[dict]":
    import json
    if _DATA_SOURCE == "nhl_api":
        return load_nhl_api_boxscores()
    # Prefer the NHL-enriched file when present (adds TAK/GIV/BLK/HIT/SHIFTS + real PP/PK).
    enriched = ROOT / "data" / "raw" / "boxscores_20252026_enriched.jsonl"
    path = enriched if enriched.exists() else ROOT / "data" / "raw" / "boxscores_20252026.jsonl"
    recs = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                recs.append(json.loads(line))
    return recs


def load_historical_boxscores() -> "list[dict]":
    """Load historical (odds-season) boxscore records from data/raw/historical."""
    import json
    recs = []
    for path in sorted((ROOT / "data" / "raw" / "historical").glob("boxscores_*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                recs.append(json.loads(line))
    return recs


def _build_feature_frame(recs):
    events = aggregate.build_team_event_frame(recs)
    feats = aggregate.build_feature_frame(events)
    feature_cols = aggregate.select_feature_columns(feats)
    feats = feats.dropna(subset=feature_cols + ["home_win", "margin"])
    return feats, feature_cols


def run_backtest() -> str:
    recs = load_parsed_boxscores()
    events = aggregate.build_team_event_frame(recs)
    feats = aggregate.build_feature_frame(events)
    feature_cols = aggregate.select_feature_columns(feats)
    feats = feats.dropna(subset=feature_cols + ["home_win", "margin"])
    print(f"[i] boxscores={len(recs)} -> feature rows={len(feats)}, "
          f"features={len(feature_cols)}")

    ml = backtest.walk_forward_moneyline(feats, feature_cols)
    margin = backtest.walk_forward_margin(feats, feature_cols)
    return "\n\n".join([backtest.report_moneyline(ml), backtest.report_margin(margin)])


def run_edge() -> str:
    """Run walk-forward predictions, join to market odds, report edge."""
    recs = load_parsed_boxscores()
    events = aggregate.build_team_event_frame(recs)
    feats = aggregate.build_feature_frame(events)
    feature_cols = aggregate.select_feature_columns(feats)
    feats = feats.dropna(subset=feature_cols + ["home_win", "margin"])
    ml = backtest.walk_forward_moneyline(feats, feature_cols)
    oof = ml.get("oof")
    if oof is None or oof.empty:
        return "No out-of-fold predictions available."

    odds = market_edge.load_odds_archive(ROOT / "data" / "raw" / "nhl_archive_10Y.json")
    merged = market_edge.join_model_to_market(oof, odds)
    text = backtest.report_moneyline(ml)
    text += "\n\n" + market_edge.report_edge(merged)
    return text


def run_espn_edge() -> str:
    """Edge test on 2025-26 boxscores vs ESPN BET closing moneylines."""
    recs = load_parsed_boxscores()
    if not recs:
        return "No 2025-26 boxscores found."
    feats, feature_cols = _build_feature_frame(recs)
    feats = feats.dropna(subset=feature_cols + ["home_win", "margin"])
    print(f"[i] 2025-26 boxscores={len(recs)} -> feature rows={len(feats)}, "
          f"features={len(feature_cols)}")

    ml = backtest.walk_forward_moneyline(feats, feature_cols)
    oof = ml.get("oof")
    if oof is None or oof.empty:
        return "No out-of-fold predictions available."

    # normalize boxscore team labels (VEG -> VGK) so they join to ESPN odds
    for col in ("home", "away"):
        oof[col] = oof[col].map(lambda a: "VGK" if a == "VEG" else a)

    espn = market_edge.load_espn_archive(ROOT / "data/raw/odds/espn/nhl_archive_2025_26.jsonl")
    merged = market_edge.join_model_to_market(oof, espn)
    text = backtest.report_moneyline(ml)
    text += "\n\n" + market_edge.report_edge(merged)
    return text


def run_historical_edge() -> str:
    """Edge test on the historical (odds-season) boxscores vs closing odds."""
    recs = load_historical_boxscores()
    if not recs:
        return "No historical boxscores found — run ingest_historical first."
    feats, feature_cols = _build_feature_frame(recs)
    feats = feats.dropna(subset=feature_cols + ["home_win", "margin"])
    print(f"[i] historical boxscores={len(recs)} -> feature rows={len(feats)}, "
          f"features={len(feature_cols)}")
    ml = backtest.walk_forward_moneyline(feats, feature_cols)
    oof = ml.get("oof")
    if oof is None or oof.empty:
        return "No out-of-fold predictions available."
    odds = market_edge.load_odds_archive(ROOT / "data" / "raw" / "nhl_archive_10Y.json")
    merged = market_edge.join_model_to_market(oof, odds)
    text = backtest.report_moneyline(ml)
    text += "\n\n" + market_edge.report_edge(merged)
    return text


def main() -> None:
    parser = argparse.ArgumentParser(description="Hobby Hero pipeline")
    parser.add_argument("--backtest", action="store_true")
    parser.add_argument("--edge", action="store_true", help="Walk-forward + market edge report")
    parser.add_argument("--historical-edge", action="store_true",
                        help="Edge test on historical odds-season boxscores vs closing odds")
    parser.add_argument("--espn-edge", action="store_true",
                        help="Edge test on 2025-26 boxscores vs ESPN BET closing moneylines")
    parser.add_argument("--parse-boxscores", action="store_true")
    parser.add_argument("--boxscore-dir", default=str(BOXSCORE_DIR),
                        help="HockeyReference boxscore HTML directory "
                             "(env: HOBBYHERO_BOXSCORE_DIR)")
    parser.add_argument("--data", choices=["legacy", "nhl_api"], default="legacy",
                        help="game data to read: legacy files, or data/raw/nhl_api "
                             "(all seasons present)")
    parser.add_argument("--out", default=None,
                        help="report path (default: reports/pipeline_report.txt, or "
                             "reports/pipeline_report_nhl_api.txt with --data nhl_api)")
    args = parser.parse_args()

    global _DATA_SOURCE
    _DATA_SOURCE = args.data
    if args.out is None:
        name = "pipeline_report_nhl_api.txt" if args.data == "nhl_api" else "pipeline_report.txt"
        args.out = str(ROOT / "reports" / name)

    if args.parse_boxscores:
        from ingest.parse_boxscores import parse_directory, build_outcome_map_from_csv
        box_dir = Path(args.boxscore_dir)
        if not box_dir.is_dir():
            raise FileNotFoundError(
                f"Boxscore source directory not found: {box_dir}. "
                "Set HOBBYHERO_BOXSCORE_DIR or pass --boxscore-dir."
            )
        out = ROOT / "data" / "raw" / "boxscores_20252026.jsonl"
        import json
        outcome_map = build_outcome_map_from_csv()
        games = parse_directory(box_dir, outcome_map)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as f:
            for g in games:
                f.write(json.dumps(g, ensure_ascii=False) + "\n")
        print(f"[+] parsed {len(games)} boxscores -> {out}")
    elif args.backtest:
        text = run_backtest()
        print(text)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"[+] report -> {args.out}")
    elif args.edge:
        text = run_edge()
        print(text)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"[+] edge report -> {args.out}")
    elif args.historical_edge:
        text = run_historical_edge()
        print(text)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"[+] historical edge report -> {args.out}")
    elif args.espn_edge:
        text = run_espn_edge()
        print(text)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"[+] espn edge report -> {args.out}")
    else:
        parser.error("need --backtest, --edge, --historical-edge, or --parse-boxscores")


if __name__ == "__main__":
    main()
