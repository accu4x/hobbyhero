# Simulator Design — Deterministic Result, Thin LLM Narrative

Target architecture for the Hobby Hero simulation engine, replacing the prior
LLM-authoring approach that was abandoned for cost.

## Principle

**Compute the result; write the story.** The box score, event log, and player stat
priors are produced deterministically by math. The LLM is used only to (a) turn
qualitative input into quantitative variables (existing `qualitative_llm.py` pattern)
and (b) generate thin narrative garnish on demand.

This inverts the old design, where the LLM authored the entire box score + narrative
per run — the output-token trap (see `LESSONS.md`).

## Two-layer output contract

### Layer 1 — Deterministic sim result (free / core)

Produced by code. Cheap, cacheable, reproducible.

- Final scoreline + win probability (home/away)
- Full box score (per-player G/A/PTS/TOI/S%/CF%/PIM, goalie SV/SV%)
- Event log (goals, assists, penalties, PP/PK/EV context)
- Three stars (selected by a deterministic rule: game-deciding goal, points, SV%)

Sources for the distributions:

- Team/goal models from the existing ML pipeline (goal lambdas, home-ice, rest,
  etc.) — reuse `hobbyhero/` model outputs.
- Player stat priors from HR data (G/GP, A/GP, PTS/GP) and the **player profile
  system**: archetype + tier + modifiers shift how offense is distributed
  (T1 core carries offense; Sniper shoots more; Grinder rarely scores).
- These are probabilities + weighted sampling, not LLM prose.

### Layer 2 — LLM narrative garnish (paid / premium, optional)

Produced by the LLM. **Thin**, **on-demand**, **one per game (not per run)**.

- Broadcast-style recap / headlines
- "Why it happened" narrative wrapping the deterministic events
- Character beats keyed to player archetypes (Sniper highlight, etc.)

Rules:

- The LLM receives the *already-computed* deterministic result as ground truth and
  writes prose around it. It does not invent the score or stats.
- Generate once per game and cache the prose; reuse across views.
- Output contract stays small and bounded (a few hundred tokens max).

## Why this is affordable

- Deterministic Layer 1 costs ~$0 (code + SQL/CSV).
- LLM cost is limited to Layer 2 output tokens, which are the expensive, uncacheable
  kind. Keeping them thin, one-shot, and cached keeps the unit cost measurable and low.
- Input-side prompt-cache hits (~95% cheaper on re-runs) still apply to any repeated
  qualitative calls.

## Pricing guardrail

Before committing to any credit price, measure the **actual per-sim output-token cost**
of Layer 2. The prior iteration assumed `$0.06/sim / 85% margin / 1 credit` without
verifying — that unverified number is what sank it. Price from a real measurement, and
keep the Layer-2 contract small enough that margin survives at scale.

## Mapping to product modes

| Mode (from prior roadmap) | Deterministic (Layer 1) | LLM (Layer 2, paid) |
|---|---|---|
| Coach mode / What If | Recompute result from modified lineup/profiles | One recap |
| Redraft / Fantasy league | Batch-sim whole season deterministically | Recaps per marquee game |
| Historical matchup | Same engine, cross-era player profiles | One recap |
| Standard nightly sim | Free result | Free short recap; longer recap = premium |

## Open questions to resolve before build

1. Does the deterministic box-score generator already exist, or must it be written from
   the ML model outputs + HR priors?
2. How are player profiles (archetype/tier/modifier) merged into the sampling weights?
3. What is the exact Layer-2 token budget, and what is the measured $/game at that budget?

## References

- Lessons: `LESSONS.md`
- Player profiles concept: `docs/player-profiles-ccg-concept.md` (prior iteration)
- Prior milestone finding: `reports/milestone2_qualitative_assessment.md`
