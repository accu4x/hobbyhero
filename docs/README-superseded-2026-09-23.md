> **Superseded 2026-09-23** by `../README.md`. Kept as history; its "for betting, with potential
> monetization" framing is closed (see `../OPEN-ITEMS.md`, Closed).

# Hobby Hero

NHL simulation / prediction engine. Goal: maximum simulation accuracy for betting, with
potential monetization of predictions.

## Current architecture

Deterministic Python ML pipeline:

- `src/ingest/` — NHL schedule/boxscores, ESPN closing moneylines, HockeyWriters articles
- `src/features/` — feature engineering; `qualitative_llm.py` uses the LLM to turn
  qualitative input into quantitative features
- `src/models/` — XGBoost moneyline + margin models
- `src/backtest/` — walk-forward backtest + edge-vs-market analysis
- `src/predict/` — next-day predictions
- `reports/` — backtest results and milestone assessments

Status: Milestone 2 concluded no positive edge over the closing market from currently
available data sources (see `reports/milestone2_qualitative_assessment.md`).

## Past iteration (Nanobot sim engine) — what failed and why

The prior version of Hobby Hero (archived under the `Nanobot` skills) was a per-game
**LLM-authoring simulation engine**: for each game it ran ~20 independent runs, and each
run had the LLM emit full box scores + scoring events + penalties + three stars + a
broadcast narrative in one shot. A customer-facing site with Shopify credit-gating
(free nightly sims, paid custom/tweaked sims) was the monetization plan.

**Why it was abandoned:** the box-score + narrative authoring was too expensive. Output
tokens are the expensive kind and are never cached, while input tokens are (~95% cheaper
on re-runs via prompt-cache hits). Full box scores × prose × 20 runs × N games made the
unit cost untenable at scale.

**The fix (adopted):** deterministic-first. Compute results (scoreline, box score, event
log, stat priors) with math; use the LLM only for thin, on-demand narrative garnish. Full
details in `docs/simulator-design.md`; distilled lessons in `docs/LESSONS.md`.

**Reusable from the old build:** the player profile system (archetype/tier/modifier, 960
skaters + 99 goalies from HR data), the credit-gating model, and the official-vs-customized
hybrid sandbox layout. The current deterministic pipeline is the correct base to build
these on top of.

## Docs

- `docs/simulator-design.md` — deterministic-result / thin-LLM-narrative split
- `docs/LESSONS.md` — lessons from the prior iteration (cost trap, rules, reuse)
- `reports/milestone2_qualitative_assessment.md` — latest accuracy/edge findings
