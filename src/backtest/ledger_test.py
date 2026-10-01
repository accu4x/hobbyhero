"""Pre-registered ledger test (SPEC-qualitative-ledger.md, step 4; run 2026-09-25).

Baseline: the chosen engine (Poisson goals on the 19 engine features). Treatments add
home-minus-away ledger deltas from data/ledger/team_weeks_20252026.jsonl:
  facts    = health, coaching, goaltending
  feelings = morale, x_factor
  all      = all 9 dimensions
Games use the row for the week they are played in (built only from articles published
before that week's Monday). A missing value is 0 (no information), as are all games
before 2025-26, where no ledger exists.

Window, folds and games are those of pre-registered W2 (2025-26 regular season, 4
chronological folds, training on every earlier game), restricted to games with a
closing line, as in OPEN-ITEMS item 23. Pass: the 90% paired-bootstrap CI of
(treatment - baseline) log loss lies entirely below 0. Results are labelled
"historical, may be contaminated".

Two data scopes are reported:
  registered  - the data the test was written against: seasons 2023-24 to 2025-26,
                xG shot model trained on 2023-24 + 2024-25.
  all seasons - the same test with 2020-21 onward (imported 2026-09-25).

    python -m src.backtest.ledger_test
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

import run_pipeline as rp
from src.backtest.compare_models import boot_ci, log_loss_each
from src.backtest.experiments_confirm import WINDOWS
from src.backtest.experiments_market import market_frame
from src.backtest.experiments_v4 import p_poisson, window_preds
from src.export.artifact_snapshot import FEATURES, Pass, base_frame
from src.features import xg

ROOT = Path(__file__).resolve().parents[2]
TEAM_WEEKS = ROOT / "data" / "ledger" / "team_weeks_20252026.jsonl"
GROUPS = {
    "facts": ["health", "coaching", "goaltending"],
    "feelings": ["morale", "x_factor"],
    "all": ["health", "coaching", "morale", "offense", "defense", "goaltending",
            "special_teams", "physical", "x_factor"],
}
START, END, _ = WINDOWS["W2 2025-26 full season"]
log = logging.getLogger("ledger_test")


def add_ledger(df: pd.DataFrame) -> pd.DataFrame:
    rows = [json.loads(l) for l in TEAM_WEEKS.read_text(encoding="utf-8").splitlines() if l.strip()]
    tw = {(r["team"], r["week"]): r for r in rows}
    d = pd.to_datetime(df["date_key"])
    week = (d - pd.to_timedelta(d.dt.weekday, unit="D")).dt.strftime("%Y-%m-%d")
    for dim in GROUPS["all"]:
        def val(team, wk):
            v = tw.get((team, wk), {}).get(dim)
            return 0.0 if v is None else float(v)
        h = [val(t, w) for t, w in zip(df["home"], week)]
        a = [val(t, w) for t, w in zip(df["away"], week)]
        df[f"ledger_{dim}_delta"] = np.array(h) - np.array(a)
    return df


def run(records: list[dict], xg_seasons: tuple[int, ...], label: str) -> dict:
    shots = xg.shot_table(records)
    p = Pass(records, base_frame(records), shots, xg_seasons)
    df = add_ledger(p.df).merge(market_frame(), on=["date_key", "home", "away"], how="left")
    preds = {"baseline": window_preds(df, START, END, FEATURES, p_poisson)}
    for g, dims in GROUPS.items():
        preds[g] = window_preds(df, START, END, FEATURES + [f"ledger_{d}_delta" for d in dims],
                                p_poisson)
    t = df.loc[preds["baseline"].index]
    t = t[t["market_home_win"].notna()]
    y = t["home_win"].to_numpy(float)
    ll = {k: log_loss_each(np.clip(v.loc[t.index].to_numpy(float), 1e-6, 1 - 1e-6), y)
          for k, v in preds.items()}
    mk = log_loss_each(t["market_home_win"].to_numpy(float), y)
    cover = float((t[[f"ledger_{d}_delta" for d in GROUPS["all"]]] != 0).any(axis=1).mean())
    out = {"scope": label, "games": int(len(t)), "ledger_coverage": round(cover, 3),
           "market_log_loss": round(float(mk.mean()), 4), "models": {}}
    for k, v in ll.items():
        row = {"log_loss": round(float(v.mean()), 4),
               "vs_market": [round(x, 4) for x in boot_ci(v - mk)]}
        if k != "baseline":
            ci = boot_ci(v - ll["baseline"])
            row["vs_baseline"] = [round(x, 4) for x in ci]
            row["pass"] = bool(ci[2] < 0)
        out["models"][k] = row
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    rp._DATA_SOURCE = "nhl_api"
    records = rp.load_parsed_boxscores()
    reg = [r for r in records if r["season"] >= 20232024]
    results = [run(reg, (20232024, 20242025), "registered (2023-24 to 2025-26)"),
               run(records, (20202021, 20212022, 20222023, 20232024, 20242025),
                   "all seasons (2020-21 to 2025-26)")]
    out = ROOT / "reports" / f"ledger_test_{date.today().isoformat()}.json"
    out.write_text(json.dumps(results, indent=1), encoding="utf-8")
    print(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
