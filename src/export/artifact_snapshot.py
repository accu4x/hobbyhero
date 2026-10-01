"""Export the Hobby Hero artifact snapshot (SPEC-artifact-v0; v0.1 and v0.2, 2026-09-24).

Engine (Dan, 2026-09-24): two Poisson regressions (home goals, away goals; shootout
excluded) on 19 home-minus-away features: 10 xG form values, starting-goalie GSAx,
and shot attempts for/against plus PP%/PK% at 10- and 40-game half-lives. The market
is never an input; it is only the benchmark shown on the page.

v0.2 adds a date control and a look-back (OPEN-ITEMS, "2026-09-24 (late)"):
  * pass A: xG shot model trained on 2023-24 -> features and team states for 2024-25 dates
  * pass B: xG shot model trained on 2023-24 + 2024-25 -> 2025-26 dates
  * pass C: xG shot model trained on all three seasons -> the off-season state (default)
  * a Poisson refit at the start of every month with games, trained only on earlier games
    of the same pass, so every past game is predicted walk-forward.

Writes artifact/snapshot.json:
  meta, features, models (monthly + off-season), dates (game days -> model), states
  (team form each game-day morning, changes only), end state, goalies, teams, games
  (look-back log with closing market), levers, metrics (pre-registered windows),
  lookback summary, parity set.

    python -m src.export.artifact_snapshot
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn
from scipy.stats import poisson
from sklearn.linear_model import PoissonRegressor
from sklearn.preprocessing import StandardScaler

import run_pipeline as rp
from src.backtest.compare_models import boot_ci, log_loss_each
from src.backtest.experiments_confirm import WINDOWS
from src.backtest.experiments_market import ESPN_DIR, market_frame
from src.backtest.experiments_v4 import p_poisson, window_preds
from src.backtest.market_edge import load_espn_archive
from src.export.team_colors import chip
from src.features import extra, xg
from src.features.extra import _boxscore

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifact" / "snapshot.json"
# Seasons come from the NHL API files on disk (2026-09-24: 2020-21 to 2025-26). Every season
# after the first is a look-back season; its xG shot model trains only on earlier seasons.
ALL_SEASONS = tuple(sorted(int(p.stem.split("_")[1]) for p in
                           (ROOT / "data" / "raw" / "nhl_api").glob("boxscores_*.jsonl")))
LOOKBACK = {s: ALL_SEASONS[:i] for i, s in enumerate(ALL_SEASONS) if i > 0}
MAX_GOALS = 12
XG_KEYS = [f"xg_{s}_h{h}" for s in ("xg_share", "xgf", "xga", "xg5_share", "finish")
           for h in (10, 40)]
REC_KEYS = [f"rec_{s}_h{h}" for s in ("cfor", "cagainst", "pp", "pk") for h in (10, 40)]
TEAM_KEYS = XG_KEYS + ["g_gsax"] + REC_KEYS
STATE_KEYS = XG_KEYS + REC_KEYS          # g_gsax comes from the goalie, stored separately
FEATURES = [f"{k}_delta" for k in TEAM_KEYS]
GAME_COLS = ["date", "season", "type", "home", "away", "home_final", "away_final", "outcome",
             "p_home", "lam_home", "lam_away", "model", "market_home", "ml_home", "ml_away",
             "g_home", "g_away", "gsax_home", "gsax_away"]

log = logging.getLogger("artifact_snapshot")


def _r(v, nd: int = 5):
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else round(float(v), nd)


# --------------------------------------------------------------------------- passes

class Pass:
    """One feature pass: an xG shot model trained on `xg_seasons`, the per-game frame,
    and the morning-of team states that frame was built from."""

    def __init__(self, records: list[dict], base: pd.DataFrame, shots: pd.DataFrame,
                 xg_seasons: tuple[int, ...]) -> None:
        self.xg_seasons = xg_seasons
        goals = {str(r["game_id"]): (r["teams"][r["home_abbrev"]]["goals"],
                                     r["teams"][r["away_abbrev"]]["goals"], r["outcome"])
                 for r in records}
        self.daily_xg: dict = {}
        self.daily_rec: dict = {}
        self.team_rows: list = []
        self.rec_state: dict = {}
        scored = xg.score_shots(shots, xg.train_xg(shots, xg_seasons))
        xgl = xg.xg_layers(records, scored, daily_out=self.daily_xg,
                           team_rows_out=self.team_rows)
        layers = extra.build_layers(records, state_out=self.rec_state, daily_out=self.daily_rec)
        df = base.merge(xgl, on="game", how="left").merge(layers, on="game", how="left")
        df["date_key"] = df["date"].astype(str)
        g = df["game"].map(goals)
        df["home_goals"], df["away_goals"] = g.map(lambda v: v[0]), g.map(lambda v: v[1])
        df["outcome"] = g.map(lambda v: v[2])
        df[FEATURES] = df[FEATURES].astype(float).fillna(0.0)
        self.df = df.sort_values(["date_key", "game"]).reset_index(drop=True)

    def state(self, key: str, season_teams: list[str]) -> dict:
        """Team state on the morning of game date `key` (or "end"): 18 form values, the
        primary goalie (most starts in the last 20) and that goalie's GSAx."""
        xs, rs = self.daily_xg[key], self.daily_rec[key]
        out = {}
        for t in season_teams:
            vals = [xs["teams"].get(t, {}).get(k) for k in XG_KEYS]
            vals += [rs["teams"].get(t, {}).get(k) for k in REC_KEYS]
            starts = Counter(rs["starts"].get(t, []))
            gid, n = starts.most_common(1)[0] if starts else (None, 0)
            gsax = xs["gsax"].get(gid) if gid else None
            out[t] = {"v": [(_r(v, 6) if v is not None else 0.0) for v in vals],
                      "goalie": gid, "starts": n, "gsax": _r(gsax if gsax is not None else 0.0, 6)}
        return out


def base_frame(records: list[dict]) -> pd.DataFrame:
    feats, _ = rp._build_feature_frame(records)
    return feats[["game", "date", "home", "away", "home_win", "margin"]].copy()


# --------------------------------------------------------------------------- models

def fit(df: pd.DataFrame) -> dict:
    scaler = StandardScaler().fit(df[FEATURES])
    x = scaler.transform(df[FEATURES])
    model = {}
    for side in ("home_goals", "away_goals"):
        m = PoissonRegressor(alpha=0.1, max_iter=1000).fit(x, df[side])
        model[side] = {"coef": m.coef_.tolist(), "intercept": float(m.intercept_)}
    et = df[df["outcome"] != "REG"]
    return {"scaler": {"mean": scaler.mean_.tolist(), "scale": scaler.scale_.tolist()},
            "home": model["home_goals"], "away": model["away_goals"],
            "tie_home_share": float(et["home_win"].mean())}


def predict(m: dict, x: np.ndarray) -> tuple[float, float, float]:
    z = (x - np.array(m["scaler"]["mean"])) / np.array(m["scaler"]["scale"])
    lh = float(np.exp(z @ np.array(m["home"]["coef"]) + m["home"]["intercept"]))
    la = float(np.exp(z @ np.array(m["away"]["coef"]) + m["away"]["intercept"]))
    k = np.arange(MAX_GOALS)
    joint = np.outer(poisson.pmf(k, lh), poisson.pmf(k, la))
    p = float(np.tril(joint, -1).sum() + np.trace(joint) * m["tie_home_share"])
    return p, lh, la


# --------------------------------------------------------------------------- metrics

def evaluate(passes: dict[tuple, Pass]) -> dict:
    """Pre-registered windows (OPEN-ITEMS item 23), re-scored for this exact spec."""
    market = market_frame()
    out: dict = {"windows": {}}
    pooled_m, pooled_mk = [], []
    for wname, (start, end, _orig_xg) in WINDOWS.items():
        season = next(s for s in LOOKBACK if str(s)[:4] <= start[:4] and start <= f"{str(s)[4:]}0701")
        xg_seasons = LOOKBACK[season]      # every season before the window (2026-09-24)
        df = passes[xg_seasons].df.merge(market, on=["date_key", "home", "away"], how="left")
        pred = window_preds(df, start, end, FEATURES, p_poisson)
        t = df.loc[pred.index]
        t = t[t["market_home_win"].notna()]
        y = t["home_win"].to_numpy(float)
        pm = np.clip(pred.loc[t.index].to_numpy(float), 1e-6, 1 - 1e-6)
        mk_p = t["market_home_win"].to_numpy(float)
        ll, mk = log_loss_each(pm, y), log_loss_each(mk_p, y)
        pooled_m.append(ll)
        pooled_mk.append(mk)
        g = boot_ci(ll - mk)
        out["windows"][wname] = {
            "games": int(len(t)), "from": t["date_key"].min(), "to": t["date_key"].max(),
            "model": {"log_loss": round(ll.mean(), 4), "brier": round(float(np.mean((pm - y) ** 2)), 4),
                      "accuracy": round(float(np.mean((pm >= .5) == (y == 1))), 3)},
            "market": {"log_loss": round(mk.mean(), 4), "brier": round(float(np.mean((mk_p - y) ** 2)), 4),
                       "accuracy": round(float(np.mean((mk_p >= .5) == (y == 1))), 3)},
            "gap_vs_market": [round(v, 4) for v in g],
        }
    g = boot_ci(np.concatenate(pooled_m) - np.concatenate(pooled_mk))
    out["pooled_gap_vs_market"] = [round(v, 4) for v in g]
    out["pooled_games"] = int(sum(len(x) for x in pooled_m))
    out["source"] = "src/export/artifact_snapshot.py (pre-registered windows, OPEN-ITEMS item 23)"
    return out


def closing_market() -> pd.DataFrame:
    frames = [load_espn_archive(p) for p in sorted(ESPN_DIR.glob("nhl_archive_*.jsonl"))]
    m = pd.concat(frames, ignore_index=True)
    m["date_key"] = m["date"].dt.strftime("%Y%m%d")
    m = m.drop_duplicates(subset=["date_key", "home", "away"], keep="last")
    ok = (m["home_close_ml"].abs() >= 100) & (m["away_close_ml"].abs() >= 100)
    m.loc[~ok, "market_home_win"] = np.nan
    return m[["date_key", "home", "away", "market_home_win", "home_close_ml", "away_close_ml"]]


def lookback_summary(games: list[list]) -> dict:
    ix = {c: i for i, c in enumerate(GAME_COLS)}
    out = {}
    labels = [(f"{str(s)[:4]}-{str(s)[6:]}", {s}) for s in LOOKBACK] + [("all", set(LOOKBACK))]
    for label, seasons in labels:
        rows = [g for g in games if g[ix["season"]] in seasons and g[ix["market_home"]] is not None]
        if not rows:                      # a season with no verified closing lines (2021-22)
            continue
        y = np.array([1.0 if g[ix["home_final"]] > g[ix["away_final"]] else 0.0 for g in rows])
        pm = np.clip(np.array([g[ix["p_home"]] for g in rows]), 1e-6, 1 - 1e-6)
        mk = np.clip(np.array([g[ix["market_home"]] for g in rows]), 1e-6, 1 - 1e-6)
        ll, mll = log_loss_each(pm, y), log_loss_each(mk, y)
        out[label] = {"games": len(rows), "model_log_loss": round(ll.mean(), 4),
                      "market_log_loss": round(mll.mean(), 4),
                      "gap_vs_market": [round(v, 4) for v in boot_ci(ll - mll)],
                      "model_accuracy": round(float(np.mean((pm >= .5) == (y == 1))), 3),
                      "market_accuracy": round(float(np.mean((mk >= .5) == (y == 1))), 3)}
    return out


# --------------------------------------------------------------------------- schedule

SCHEDULE_COLS = ["date", "start_utc", "home", "away", "neutral", "p_home", "lam_home", "lam_away",
                 "g_home", "g_away", "gsax_home", "gsax_away", "game_id"]


def main_starters(p: Pass, by_gid: dict, season: int) -> dict[str, tuple]:
    """Each team's goalie with the most regular-season starts in `season`, and that goalie's
    GSAx after the last game (Dan, 2026-09-29: "last season's main starter")."""
    starts: dict[str, Counter] = {}
    for row in p.team_rows:
        r = by_gid.get(str(row["game"]))
        if r is None or r["season"] != season or r["game_type"] != 2 or row.get("starter") is None:
            continue
        starts.setdefault(row["team"], Counter())[row["starter"]] += 1
    gsax = p.daily_xg["end"]["gsax"]
    out = {}
    for t, c in starts.items():
        gid, n = c.most_common(1)[0]
        g = gsax.get(gid)
        out[t] = (gid, n, _r(g if g is not None else 0.0, 6))
    return out


def upcoming(p: Pass, model: dict, end_state: dict, by_gid: dict, played: set[str],
             gix) -> dict | None:
    """The next season's regular-season schedule, predicted pre-season (2026-09-29): the
    off-season model on end-of-season form, with last season's main starters. Neutral-site
    games average both orientations, as the page's neutral venue does."""
    last = ALL_SEASONS[-1]
    season = last + 10001
    path = ROOT / "data" / "raw" / "nhl_api" / f"schedule_{season}.jsonl"
    if not path.exists():
        return None
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    rows = [r for r in rows if str(r["game_id"]) not in played]
    starters = main_starters(p, by_gid, last)
    k = {key: i for i, key in enumerate(STATE_KEYS)}

    def vals(t: str) -> dict | None:
        if t not in end_state or t not in starters:
            return None
        v = {key: end_state[t]["v"][i] for key, i in k.items()}
        v["g_gsax"] = starters[t][2]
        return v

    def x(hv: dict, av: dict) -> np.ndarray:
        return np.array([hv[t] - av[t] for t in TEAM_KEYS], dtype=float)

    games, names = [], {}
    for r in rows:
        h, a = r["home"], r["away"]
        names[h] = {"place": r["home_place"], "name": r["home_name"]}
        names[a] = {"place": r["away_place"], "name": r["away_name"]}
        hv, av = vals(h), vals(a)
        p_home = lh = la = None
        if hv and av:
            p_home, lh, la = predict(model, x(hv, av))
            if r["neutral_site"]:
                pb, lbh, lba = predict(model, x(av, hv))
                p_home, lh, la = (p_home + 1 - pb) / 2, (lh + lba) / 2, (la + lbh) / 2
        sh, sa = starters.get(h), starters.get(a)
        games.append([r["date"], r["start_utc"], h, a, int(r["neutral_site"]),
                      None if p_home is None else round(p_home, 5),
                      None if lh is None else round(lh, 4), None if la is None else round(la, 4),
                      gix(sh[0]) if sh else None, gix(sa[0]) if sa else None,
                      sh[2] if sh else None, sa[2] if sa else None, r["game_id"]])
    return {"season": season, "fetched": rows[0]["fetched"] if rows else None,
            "starters_from": last, "cols": SCHEDULE_COLS, "games": games, "names": names,
            "starter_starts": {t: s[1] for t, s in starters.items()}}


# --------------------------------------------------------------------------- main

def levers(all_states: list[dict]) -> list[dict]:
    def rng(idx: list[int] | None, gsax: bool = False, pad: float = 0.25) -> tuple[float, float]:
        vals = [s["gsax"] for st in all_states for s in st.values()] if gsax else \
            [s["v"][i] for st in all_states for s in st.values() for i in idx]
        lo, hi = np.percentile(vals, 1), np.percentile(vals, 99)
        return lo - pad * (hi - lo), hi + pad * (hi - lo)

    k = {key: i for i, key in enumerate(STATE_KEYS)}
    spec = [
        ("xgf", "Expected goals for / game", ["xg_xgf_h10", "xg_xgf_h40"], 0.01, "{:.2f}"),
        ("xga", "Expected goals against / game", ["xg_xga_h10", "xg_xga_h40"], 0.01, "{:.2f}"),
        ("finish", "Finishing (goals minus xG / game)", ["xg_finish_h10", "xg_finish_h40"], 0.01, "{:+.2f}"),
        ("gsax", "Starting goalie GSAx (per expected goal)", ["g_gsax"], 0.005, "{:+.3f}"),
        ("cfor", "Shot attempts for / game", ["rec_cfor_h10", "rec_cfor_h40"], 0.5, "{:.1f}"),
        ("cagainst", "Shot attempts against / game", ["rec_cagainst_h10", "rec_cagainst_h40"], 0.5, "{:.1f}"),
        ("pp", "Power play %", ["rec_pp_h10", "rec_pp_h40"], 0.005, "pct"),
        ("pk", "Penalty kill %", ["rec_pk_h10", "rec_pk_h40"], 0.005, "pct"),
    ]
    out = []
    for lid, label, keys, step, fmt in spec:
        lo, hi = rng(None, gsax=True) if lid == "gsax" else rng([k[x] for x in keys])
        out.append({"id": lid, "label": label, "keys": keys, "min": round(lo, 4),
                    "max": round(hi, 4), "step": step, "format": fmt})
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    rp._DATA_SOURCE = "nhl_api"
    records = rp.load_parsed_boxscores()
    by_gid = {str(r["game_id"]): r for r in records}
    base = base_frame(records)
    shots = xg.shot_table(records)
    passes = {s: Pass(records, base, shots, s) for s in list(LOOKBACK.values()) + [ALL_SEASONS]}
    metrics = evaluate(passes)
    market = closing_market().set_index(["date_key", "home", "away"])

    # names, per season (Utah changed its name between seasons)
    names: dict[int, dict] = {}
    for r in sorted(records, key=lambda r: r["date"]):
        box = _boxscore(r)
        if not box:
            continue
        for side in ("homeTeam", "awayTeam"):
            t = box[side]
            names.setdefault(r["season"], {})[t["abbrev"]] = {
                "place": (t.get("placeName") or {}).get("default", ""),
                "name": (t.get("commonName") or {}).get("default", "")}
    goalie_names = passes[ALL_SEASONS].rec_state["goalie_names"]
    goalie_ix: dict = {}

    def gix(pid) -> int | None:
        if pid is None:
            return None
        if pid not in goalie_ix:
            goalie_ix[pid] = len(goalie_ix)
        return goalie_ix[pid]

    def pack(st: dict) -> dict:
        return {t: s["v"] + [gix(s["goalie"]), s["gsax"], s["starts"]] for t, s in st.items()}

    models, dates, states, games, parity, all_states = [], [], {}, [], [], []
    rng = np.random.default_rng(42)
    for season, xg_seasons in LOOKBACK.items():
        p = passes[xg_seasons]
        df = p.df
        season_teams = sorted({r["home_abbrev"] for r in records if r["season"] == season})
        season_dates = sorted({str(r["date"]) for r in records if r["season"] == season})
        rows_by_game = {(row["game"], row["team"]): row for row in p.team_rows}
        prev = None
        month_model: dict[str, int] = {}
        for d in season_dates:
            month = d[:6]
            if month not in month_model:
                train = df[df["date_key"] < month + "01"]
                m = fit(train)
                label = pd.Timestamp(month + "01").strftime("%B %Y")
                models.append({"id": len(models), "label": label, "applies_from": month + "01",
                               "trained_through": train["date_key"].max(),
                               "train_games": int(len(train)), "xg_trained_on": list(xg_seasons),
                               **m})
                month_model[month] = len(models) - 1
                # parity sample: games this model predicts
                test = df[(df["date_key"] >= month + "01") & (df["date_key"] <= month + "31")]
                for i in rng.choice(test.index.to_numpy(), size=min(8, len(test)), replace=False):
                    x = test.loc[i, FEATURES].to_numpy(float)
                    pp, lh, la = predict(models[-1], x)
                    parity.append({"model": len(models) - 1, "x": [float(v) for v in x],
                                   "p_home": pp, "lam_home": lh, "lam_away": la})
            mi = month_model[month]
            st = p.state(d, season_teams)
            all_states.append(st)
            packed = pack(st)
            if prev is None:
                states[d] = packed
            else:
                states[d] = {t: v for t, v in packed.items() if prev.get(t) != v}
            prev = packed
            dates.append({"d": d, "season": season, "model": mi})
            # the day's games, predicted by this month's model
            for _, row in df[df["date_key"] == d].iterrows():
                r = by_gid[row["game"]]
                x = row[FEATURES].to_numpy(float)
                pp, lh, la = predict(models[mi], x)
                h, a = r["home_abbrev"], r["away_abbrev"]
                rh, ra = rows_by_game.get((row["game"], h), {}), rows_by_game.get((row["game"], a), {})
                try:
                    mk = market.loc[(d, h, a)]
                    mkt = _r(mk["market_home_win"], 4)
                    ml_h = int(mk["home_close_ml"]) if mkt is not None else None
                    ml_a = int(mk["away_close_ml"]) if mkt is not None else None
                except KeyError:
                    mkt = ml_h = ml_a = None
                games.append([d, season, r["game_type"], h, a, r["final_home"], r["final_away"],
                              r["outcome"], round(pp, 5), round(lh, 4), round(la, 4), mi, mkt,
                              ml_h, ml_a, gix(rh.get("starter")), gix(ra.get("starter")),
                              _r(rh.get("g_gsax"), 6), _r(ra.get("g_gsax"), 6)])

    # off-season: everything, xG trained on all seasons
    pc = passes[ALL_SEASONS]
    end_model = fit(pc.df)
    last_day = max(str(r["date"]) for r in records)
    models.append({"id": len(models), "label": "Off-season", "applies_from": "end",
                   "trained_through": last_day, "train_games": int(len(pc.df)),
                   "xg_trained_on": list(ALL_SEASONS), **end_model})
    end_teams = sorted({r["home_abbrev"] for r in records if r["season"] == ALL_SEASONS[-1]})
    end_state = pc.state("end", end_teams)
    all_states.append(end_state)
    recent = pc.df[pc.df["date_key"] >= min(str(r["date"]) for r in records
                                            if r["season"] == ALL_SEASONS[-1])]
    for i in rng.choice(recent.index.to_numpy(), size=40, replace=False):
        x = recent.loc[i, FEATURES].to_numpy(float)
        pp, lh, la = predict(end_model, x)
        parity.append({"model": len(models) - 1, "x": [float(v) for v in x],
                       "p_home": pp, "lam_home": lh, "lam_away": la})

    # 2026-09-29: this season's schedule, predicted pre-season from the off-season state
    schedule = upcoming(pc, models[-1], end_state, by_gid, set(by_gid), gix)

    teams = {}
    for season in sorted(names):
        if season not in LOOKBACK:
            continue                      # 2023-24 (and Arizona) is training data only
        for t, nm in names[season].items():
            teams.setdefault(t, {"colors": chip(t), "names": {}})["names"][str(season)] = nm
    if schedule:
        for t, nm in schedule.pop("names").items():
            teams.setdefault(t, {"colors": chip(t), "names": {}})["names"][str(schedule["season"])] = nm
    goalies = [None] * len(goalie_ix)
    for pid, i in goalie_ix.items():
        goalies[i] = goalie_names.get(pid, "")

    engine = {"engine": "poisson", "max_goals": MAX_GOALS,
              "features": [{"name": f, "team_key": k} for f, k in zip(FEATURES, TEAM_KEYS)],
              "state_keys": STATE_KEYS, "models": models}
    model_hash = hashlib.sha256(json.dumps(engine, sort_keys=True).encode()).hexdigest()[:12]
    summary = lookback_summary(games)
    body = {**engine, "dates": dates, "states": states,
            "end": {"after": last_day, "model": len(models) - 1, "teams": pack(end_state)},
            "goalies": goalies, "teams": teams, "game_cols": GAME_COLS, "games": games,
            "levers": levers(all_states), "metrics": metrics, "lookback": summary,
            "parity": parity, "schedule": schedule}
    body["meta"] = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "last_game": last_day, "lookback_seasons": list(LOOKBACK),
        "trained_on": list(ALL_SEASONS), "model_hash": model_hash,
        "sklearn": sklearn.__version__,
        "benchmark": "ESPN closing moneyline, de-vigged (comparison only, never an input)",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(body, separators=(",", ":")), encoding="utf-8")
    log.info("snapshot -> %s (%d models, %d dates, %d games, %d scheduled, %d KB, hash %s)", OUT,
             len(models), len(dates), len(games), len(schedule["games"]) if schedule else 0,
             OUT.stat().st_size // 1024, model_hash)
    print(json.dumps({"metrics": metrics, "lookback": summary}, indent=1))


if __name__ == "__main__":
    main()
