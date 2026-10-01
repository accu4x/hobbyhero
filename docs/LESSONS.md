# Hobby Hero — Lessons from the Prior (Nanobot) Sim Engine

Distilled from the archived `Nanobot` skill/script dump (retrieved 2026-08-29). This
records what the previous simulation engine was, why it failed, and the concrete rules
the current rebuild should follow so we don't repeat the mistake.

## What the prior iteration was

A per-game, **LLM-authoring** simulation pipeline (DeepSeek `deepseek-v4-flash`, temp 0.5):

- Context builder pulled ~10 THW articles + ~10 YT transcripts + ~20 HR games per team
  into a JSON/CSV `recent_context` file, with per-file provenance and a `chars/3.5`
  token estimator.
- For each game it ran **~20 independent runs**, and each run asked the LLM to emit, in a
  single shot: **full box scores + scoring_events + penalty_events + three_stars + a
  broadcast narrative summary**.
- Outputs were uploaded to an Azure Functions/SWA API and tracked by version
  (accuracy-by-version, v0.1 → v0.3).
- A customer-facing site + Shopify credit-gating (free nightly sims; paid custom/tweaked
  sims) was the monetization plan. It was **abandoned because the box-score + narrative
  authoring was too expensive** to run at that scale.

## Root cause — the output-token trap

The expensive thing was **output tokens, and output tokens are never cached.**

- Input (context) tokens ARE cacheable: re-running the same game was ~95% cheaper on the
  second run due to full prompt-cache hits.
- Output tokens are NOT cached and were huge: a full box score + events + 3-stars +
  prose, × 20 runs × N games. That cost multiplied and was never discounted.
- The narrative/summary generation was the most token-dense, least monetizable part —
  precisely what killed the economics.

## Reusable assets from the old build (do carry these forward)

1. **Player profile system** — archetype + tier + modifiers injected into the sim prompt
   (960 skaters + 99 goalies already computed from Hockey-Reference data via a rules
   engine). This is the most productizable asset and the hook that makes "tweaked sims"
   feel meaningful (swap a player → tier/archetype shift → different outcome distribution).
2. **Credit-gating model** — credits (not tokens), 5 free on signup, Shopify purchase
   idempotency, optimistic concurrency on balance. The paid-product mechanics were solved;
   that was not what failed.
3. **Hybrid sandbox** — official vs. customized simulation (`SimulationType`, per-user
   `custom/{userId}/{runId}` blob layout, Azure Queue async daemon). This is the skeleton
   for "customer tweaks a sim."
4. **Pipeline hygiene** — compact CSV context (35–40% input-token reduction), per-file
   provenance, point-in-time loading, deterministic 320-game backtest sampler,
   accuracy-by-version tracking. All deterministic-Python-first and sound.

## Concrete rules going forward

1. **Deterministic-first.** Compute the result (scoreline, box score, event log, player
   stat priors) with deterministic probability / archetype logic. Only use the LLM for
   what math can't do: qualitative → quantitative scoring and thin narrative garnish.
2. **Never ask the LLM to author the box score.** That was the failure. Keep the LLM
   output contract tiny.
3. **One narrative per game, not per run.** If prose is the product, generate it once and
   reuse it. Bill the cheap deterministic sim; upsell the expensive prose as a separate
   premium.
4. **Cache output too**, not just prompt. Store the computed box score; only regenerate
   prose on demand.
5. **Price against measured unit cost before building the public site.** The old
   `$0.06/sim / 85% margin / 1 credit` figures were asserted, not verified against actual
   per-run output-token cost — and that unverified number is what sank the economics.

## Security note from the archive

The retrieved folder contains a plaintext `config.json` dump with a **live Telegram bot
token, a Gmail app password, and a LAN-bridge secret**. If that folder (or any copy of it)
is ever shared, rotate all three immediately. Prefer quarantining the file over keeping
it in-place.

## What the current rebuild already gets right

The current `hobbyhero/` workspace is a **deterministic Python ML pipeline**
(ingest → features → models → backtest → predict) targeting the closing moneyline, with
the LLM used only as a qualitative scorer (`qualitative_llm.py`). That is the correct
shape — keep it that way. Milestone 2 found no incremental edge from available
qualitative/market sources (see `reports/milestone2_qualitative_assessment.md`); the
deterministic-first lesson and the player-profile injection belong on top of this,
not in place of it.
