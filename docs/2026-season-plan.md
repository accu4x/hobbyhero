# Hobby Hero — 2026 Season Plan (August → Opening Night 9/29)

## Product decision (locked)

Hobby Hero is a **read-only repository**: browsable results, season stats, predictions, and the daily take.
**No user-run simulations for now.** Removes user accounts / credits / Shopify / custom-sim surface built for
the LLM era.

Distribution: X + Substack point to the repo. Monetization only if it gains traction (newsletter).

## Architecture principle

Split **prediction** from **narrative**. The LLM computes nothing predictive; it only writes the garnish.

| Step | Method | Notes |
|---|---|---|
| Ingest | deterministic | daily schedule + box scores; nightly data pull (NEAR DONE) |
| Predict | deterministic Monte Carlo | team-level features; backtested; fixed RNG seeds |
| Content (box score/3 stars) | deterministic | player-level output from existing player-profile engine; NEVER LLM-authored |
| Narrative (the take) | DeepSeek only | reads articles/transcripts/news; writes ONE short "the sim says X, why" per game |
| Publish | push blob → repo → auto-post X + Substack |

"Deterministic where we can": everything except the narrative. If the narrative fails, the prediction still
publishes.

## The anti-slop rule (do not skip)

1. **Numbers first, prose last.** Prediction table + box score carry the post; summary is ONE dry paragraph.
2. **Fixed, flat, recognizable voice.** Not a hype broadcaster. e.g. "MTL 58% to win. Expected 3-2. Caufield
   over 0.5 goals — 61%."
3. **Transparent failures.** Log and publish misses ("went 5-7 last night, here's where"). Self-reported
   failure is the anti-slop moat.

## Scope of the month (before 9/29)

1. **Finish nightly data pull** — schedule + box scores (CLOSE).
2. **Backtest engine against last season** — get the honest track record number to post on opening night.
3. **Post offseason sim content daily** — projections, division standings sims, what-ifs, award picks. Builds
   audience before opening night.
4. **Player-level content (deterministic)** — for content richness; NOT to chase betting accuracy.

## Player-level audit (2026-08)

### Data already exists
- HockeyReferenceIngestionService already ingests per-game SKATER stats (G-A-PTS +/- PIM S TOI + advanced: Corsi, hits, blocks) and GOALIE stats (GA SA saves SV% SO) into GamePlayerStat / GameGoalieStat. The stat set we want to simulate is already grounded.
- PlayerProfile MODEL exists (ratings, Tier 1-5, Archetype, Modifiers) but has NO data. Old "960 skaters + 99 goalies" rating file NOT found in repo or nanobot skill data — assume lost unless found in a backup.

### Plan: derive, don't hand-build
- Tier = derived from PPG + TOI + league percentile (deterministic math, no hand tuning).
- Archetype = derived from stat SHAPE via rules engine (Sniper=high S%/shots/low assists; Playmaker=high A/low G; Power Forward=high hits+PIM; etc.).
- Modifiers = tags layered on derived tier/archetype (Iron-Man=high GP; Disciplined=low PIM; Shutdown=high blocks; Leader=roles).
- Simulation: player per-game rates weighted by derived profile, then GROUNDED into team MC goal total so player outcomes sum to the team score.
- Rule: never LLM-author the box score (LESSONS). LLM only writes the one-paragraph narrative.
- Yearbook/prospects: grounding for NARRATIVE + prospect tiers only, NOT a dependency to run a sim.

### Build order for the month
1. Derivation layer (tiers/archetypes/modifiers from ingested stats) — working prototype.
2. Backtest team-level FIRST — the honest opening-night track record. (Player richness is content, not edge.)
3. Player stat simulation grounded to team total — the content feature.

## Backtest / ML pipeline audit (2026-08, confirmed from code)

- Data source: NHL API (api-web.nhle.com) for prior seasons; 2025-26 read from pre-downloaded Hockey-Reference CSVs; market odds from ESPN (fetch_espn_odds.py). NOT Hockey-Reference-driven for games.
- XGBoost features (build_features.py), all team-level rolling form, prior-only, no leakage, home-minus-away deltas, windows 5/10/20:
  win_rate, gf_per_game, ga_per_game, reg_win_rate, ot_loss_rate, so_win_rate, rest_days.
- Targets: home_win (moneyline) + margin (puck-line). NO player-level or qualitative features in the model.
- Decision: KEEP as a skill (data/ML research tool), do NOT port into the C# app (which is the publishing storefront). App consumes pipeline outputs (blob → repo → X/Substack).
- Releasable artifact: a leakage-guarded walk-forward NHL prediction starter. Side quest after the daily take.

## Feature enrichment — DONE (2026-08)

- Added `src/ingest/enrich_nhl_boxscores.py`: pulls per-game team stats from NHL API
  `gamecenter/{id}/boxscore` (takeaways, giveaways, blocked_shots, hits, shifts) and
  derives REAL PP/PK opportunities from `summary.penalties` (count opponent MIN/MAJ
  penalties) — replacing the old PIM/2 approximation in aggregate.py.
- Output: `data/raw/boxscores_20252026_enriched.jsonl` (1,394 games; 1,393 fetched, 1 timeout).
  `run_pipeline.py` auto-prefers the enriched file.
- faceoff_pct NOT added: API only gives faceoffWinningPctg (0.0 for zero-draw players),
  so a team mean is biased low — deferred per product decision.

### Backtest results (2025-26, walk-forward, one season)
| Metric | Baseline | With enrichment |
|---|---|---|
| Moneyline accuracy | 53.3% | 56.2% (+2.9pp) |
| Brier | 0.2500 | 0.2475 |
| Log loss | 0.6934 | 0.6886 |
| Mean edge vs closing market | −0.0528 | −0.0605 |
| Games with edge>0 | 53/186 | 48/186 |

- New feature `blocked_shots` now appears in top-8 importance.
- Honest conclusion: richer team features IMPROVE raw accuracy but NOT edge vs the closing
  market. Market remains efficient. Consistent with Milestone 2. Use accuracy as the
  credibility/content number, NOT a betting claim.

## Deliberately deferred (north star, not this month)

- CCG-style player cards, award ceremonies, iOS/PWA/SQLite.
- Trading fantasy league (trade, edit lines, name team, compete).
- User-run simulations / storefront.
- Chasing player-level to beat the betting market (proven dead end; Milestone 2).

## Rule reminders

- Keep toolchain lean; quarantine before delete.
- Token discipline: LLM only for the one-paragraph narrative.
- MVP > perfect. Post something every day.
