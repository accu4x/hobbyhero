# SPEC: Markov-Chain Game Simulator

**Status:** Proposed (revised 2026-09-26). Steps 1–3 done 2026-09-26. **Gate A ran and FAILED**
on attempts/game (pooled) and league-level drift (`OPEN-ITEMS.md` item 26,
`reports/markov_gate_a_2026-09-26.json`). Gate B doesn't start until Dan decides. See §13.
**Builds on:** Dan's `markov-simulator-design.md` (2026-09-25; private since 2026-09-30, in `../private/hobbyhero/`). This spec turns that design into
testable steps. Where the two disagree, reconcile them before building.
**Related:** `docs/simulator-design.md`, `docs/LESSONS.md`, `SPEC-artifact-v0.md`,
`SPEC-market-tests.md`, `OPEN-ITEMS.md` items 22–25

> Drafted outside the repo. File locations and module paths are proposals. Check them against
> `workspace/AGENTS.md` and this repo's `CLAUDE.md` / conventions first.

## 1. Goal

1. **Accuracy.** Build an event-level simulator and find out whether it gets closer to the
   closing line than the current engine does. The market is a benchmark only. It is never an
   input and never a wager (decision 2026-09-12).
2. **Engagement and narrative.** Each simulated game is a real event sequence, so the page can
   show a game that plays out event by event instead of only a probability.

**Baseline:** the current engine, Poisson goals on xG + GSAx + Corsi for/against + PP%/PK%.

### What the chain could add over the Poisson engine

The Poisson engine already produces score distributions, so the chain has to earn its place
through **game-state dynamics**:
- score effects (trailing teams press)
- special-teams sequences and penalty timing
- empty-net pulls
- 3v3 OT and shootouts

These come out of the simulation directly. The Poisson engine can only average over them.
Honest prior: the gap to the close has been within noise for 2025-26, so a large improvement
is unlikely. A tie with the Poisson engine plus a working game-flow simulator still counts as
a useful outcome for goal 2.

## 2. Model overview

The model is a **semi-Markov chain over play-by-play events**:

1. A state `s` summarizes the current game context (§4).
2. From `s`, sample the **dwell time** until the next event (§6).
3. Sample the **next event and the acting team** from `P(e | s, home, away)` (§5).
4. Update the clock, score, manpower and penalty timers, which gives the new state.
5. Repeat until the game ends, including OT and the shootout (§7).

Skaters are modeled at team level. Goalies are modeled individually.

## 3. Data

- **Source:** the NHL API play-by-play, cached as raw responses (D5). Seasons 2020-21 →
  2025-26. Closing lines are verified from 2022-23 onward.
- **Step 0:** the xG shot model is built from play-by-play shot locations, so the play-by-play
  is probably cached already. Confirm this, and confirm that situation codes, goalie-on-ice
  and event times are present. If the odds files include totals, note that; they could serve
  as a second benchmark (§8), never as an input.

| Tier | Events | Treatment |
|---|---|---|
| Reliable | faceoff, shot-on-goal, missed-shot, blocked-shot, goal, penalty, stoppage, period start/end | Modeled directly |
| Noisy (rink scorer bias) | hit, giveaway, takeaway | v1: merged into one `OTHER` class; tested as an ablation |
| Unobserved | passes, zone entries, possession | Not modeled (§10) |

Coordinate caveat: blocked-shot coordinates may mark where the shot was blocked, not where it
was taken. Verify this before blocked shots are given a shot-quality value.

## 4. State

States are encoded from the home team's perspective, with an actor flag.

| Variable | Values (v1) |
|---|---|
| Last event class | FACEOFF, SHOT_SAVED, SHOT_MISSED, SHOT_BLOCKED, GOAL, PENALTY, STOPPAGE, OTHER |
| Last actor | home / away |
| Manpower | 5v5, home PP, away PP, 4v4, 3v3, home EN, away EN |
| Score diff (home − away) | ≤−2, −1, 0, +1, ≥+2 |
| Zone of last event | O / N / D relative to home |
| Game phase | P1–P2, P3 (>5:00 left), P3 late (≤5:00), OT |

That gives several thousand cells in total, far too many to estimate per team as count tables.
§5 handles this with regression and shrinkage.

## 5. Transitions

**Stage A: the next event.** A multinomial model over
`{shot attempt (side, quality bin), stoppage→faceoff, penalty (side), OTHER (side), period end}`,
conditioned on the state plus team effects.

**Stage B: the shot outcome.** One of `{blocked, missed, saved, goal}`, conditioned on shot
quality, manpower, shooting-team offense, defending-team suppression, and **the goalie**.

**Shot quality** reuses the existing walk-forward xG shot model: attempts are binned by xG
(for example, 3–4 bins). This keeps shot quality consistent with the engine it's being
compared against.

**Estimation:**
- Multinomial logistic regression (or GLMM). The state variables are fixed effects. Team
  offense and defense are penalized effects, shrunk toward league rates.
- Team parameters cover: attempt generation and suppression, quality mix for and against,
  block and miss rates for and against, penalties drawn and taken, and faceoff win rate.
  Finishing talent is heavily shrunk.
- Goalie save probability is estimated per xG bin, with strong shrinkage (this is the GSAx
  analogue).
- **Walk-forward monthly refits**, matching the engine and the page. Recency weighting uses a
  half-life. Tune it only inside the selection windows so tuning doesn't leak.

## 6. Time

- Dwell time is sampled from an empirical or gamma-fitted distribution by (last event class,
  manpower). If that fits poorly, move to competing risks.
- Rules: 20:00 periods; minors expire after 2:00 or on a goal against; majors run the full
  5:00; coincidental minors produce 4v4.
- The trailing team's goalie pull is modeled as a hazard fitted from the data, based on time
  left and the deficit.

## 7. Simulation and outputs

- N = 10,000 simulations per game (Monte Carlo SE ≈ 0.005 on a win probability), vectorized
  with NumPy.
- **Regular season:** 5:00 of 3v3 sudden death, then a shootout at the league base rate in v1.
  **Playoffs:** 20:00 periods of 5v5 until a goal.
- **Goalies:** the actual starter in the look-back. A future live mode would need projected
  starters.
- **Outputs per game:** P(home win), a regulation / OT / SO split, the score and total-goals
  distribution, and expected shots and PP opportunities for each side.

## 8. Validation (pre-registered)

Write the tolerances and pass criteria into `OPEN-ITEMS.md` **before** running each gate, the
same way the ledger and market tests were handled.

**Gate A: the league chain reproduces hockey** (no team effects). On held-out seasons, check
goals/game, attempts/game, PP opportunities and PP%, the share of games reaching OT and
shootouts, the home win rate, and score effects (attempt share when trailing vs. leading).
All must fall within tolerances set in advance.

**Gate B: team calibration.** Simulated per-team attempt and goal rates should be calibrated
against actual results in held-out games.

**Gate C: accuracy against the benchmarks.** Use the same pre-registered windows (W1/W2) and
walk-forward setup as items 22–25:
- Compare the Markov model, the Poisson engine, and the closing line (vig removed) on log loss
  and Brier score, with 95% CIs. Also report calibration.
- **Primary test:** the Markov log loss minus the Poisson log loss. It passes if the upper CI
  bound is below 0.
- **Secondary tests:** the gap to the close, and a Markov + Poisson blend.
- If totals lines exist, compare the simulated total-goals distribution with the Poisson
  engine's as a secondary benchmark.

**Ablations:** OTHER events in vs. out; shot-quality bins vs. none; score state vs. none.

**Report what happens.** Results go to `reports/markov_gate_{a,b,c}_*.json` and
`OPEN-ITEMS.md`, and a failure is written up plainly, like the ledger result.

## 9. Engagement hook (keep the data; no page work yet)

For each look-back game, save one representative simulated event log (for example, the sim
whose final score matches the modal score) as JSON. This is enough for the page to replay a
game on the SVG rink later, or for the thin narrative layer in `simulator-design.md` to
describe it, with no LLM authoring cost (`LESSONS.md`).

## 10. Limitations

- **Markov memory:** rushes and sustained pressure depend on more than the last event. Add
  rebound/rush flags, and consider a second-order state only if Gate A misses these patterns.
- **Possession isn't observed.**
- **Rink scorer bias** affects the noisy events.
- **Roster moves and injuries aren't modeled**, so team effects lag behind them.
- **Special-teams effects stay noisy early in the season.**

## 11. Proposed layout (confirm with AGENTS.md / CLAUDE.md)

```
src/markov/states.py      # play-by-play -> state sequences, xG bins
src/markov/transitions.py
src/markov/timing.py
src/markov/simulate.py
src/backtest/             # Markov adapter in the existing harness
reports/markov_gate_*.json
```

## 12. Steps

1. [x] Reconcile with `markov-simulator-design.md`; confirm the layout (2026-09-26, §13)
2. [x] Step 0 audit: play-by-play cache, fields, totals lines (2026-09-26, §13)
3. [x] States and the league chain → pre-register and run Gate A (2026-09-26: built,
   registered and run. **Result: FAIL**, `OPEN-ITEMS.md` item 26)
4. [ ] Team and goalie effects → pre-register and run Gate B
5. [ ] Full simulator (OT, SO, empty net) with saved event logs
6. [ ] Pre-register and run Gate C, plus ablations
7. [ ] Write up the result in `OPEN-ITEMS.md`; update the catalogue

## 13. Reconciliation and build log (2026-09-26)

### Reconciled with `markov-simulator-design.md` (Dan, 2026-09-25)

The design doc is kept as Dan wrote it. Where the two disagree, this is what stands and why.

| Topic | Design doc | Resolution |
|---|---|---|
| Goal | "Improve betting strategy"; ROI, CLV, a bet-selection threshold | **This spec:** accuracy and engagement. The market is a benchmark only (`../AGENTS.md` hard constraint, 2026-09-12). |
| Baseline | XGBoost moneyline and margin models | **This spec:** the Poisson engine (items 24–25). |
| Shot quality | Location danger buckets | **This spec:** bins from the walk-forward xG model (Dan, 2026-09-26). |
| Play-by-play ingest (`src/ingest/pbp.py`) | New ingest if the cache lacks play-by-play | **Not needed:** `src/ingest/nhl_api_season.py` already caches it for every game (Step 0 below). |
| Layout | `src/features/pbp_states.py`, `src/models/markov/…` | **Design doc's layout** (Dan, 2026-09-26), replacing §11 above. |
| Reports | `reports/markov_gate_*.md` | **JSON** (`reports/markov_gate_a_<date>.json`) plus the `OPEN-ITEMS.md` entry (Dan). |
| Tests | "with tests" | **pytest in `tests/`**, the project's first tests folder (Dan, 2026-09-26). |
| Odds scope | Totals and puck lines | Both checked in Step 0. Neither exists. |
| Six-dimension mapping | §11 of the design doc | Not carried over. Still open as item 9. |

### Amendments to this spec (approved by Dan, 2026-09-26)

1. **OT, the shootout, goalie pulls and penalty timers are built in step 3**, because Gate A
   measures them. Step 5 keeps only saved event logs and the team and goalie layering.
2. **Stage B is reordered:** attempt (side) → blocked or not → xG bin (unblocked only) →
   missed, saved or goal. Blocked-shot coordinates mark the block (Step 0), so a blocked
   attempt has no reliable shot quality.
3. **Period end is not sampled.** The clock censors the interval at 20:00.
4. **Gate A uses count tables with hierarchical backoff** (a cell's distribution is shrunk
   toward the next coarser cell, alpha = 10). The penalised regression comes with team
   effects in Gate B.
5. **5v3 is its own manpower state** (HPP2 / APP2). The manpower states are EV5, EV4, EV3,
   HPP, HPP2, APP, APP2, HEN, AEN.
6. **2020-21 is a full training season** (Dan's choice over Claude's recommendation to use it
   for the xG model only). **Held-out seasons:** 2022-23, 2023-24, 2024-25 and 2025-26,
   regular season only. Each season's chain and xG model are fit on every season before it.

### Changes found while building (in-sample, training seasons only)

The in-sample check fits on 2020-21 and 2021-22 and simulates the same seasons. It reads no
held-out season. It turned up three mechanics problems, each fixed before any tolerance was
proposed:

- **Dwell drawn after the next event.** The dwell time depends on the transition (last
  class, next event, same or opposite actor, manpower), not only on the state. With a
  state-only dwell, rebounds came out too often and goals/game was +0.23.
- **Start-of-interval manpower.** The chain is keyed on the manpower at the start of each
  interval. Keying on the next event's code moved long power-play intervals into
  even-strength, so power-play time and PP% were off.
- **Censoring.** `timing.mark_expiries` replays the penalty clock over the real events. An
  interval cut by a penalty expiry or a period end is right-censored, and the dwell tables
  are Aalen–Johansen cumulative incidences. The simulator cuts at the same points.
  - With this change, time by manpower matches the data (seconds per game, simulated vs
    observed): 5v5 2,937 vs 2,934; home PP 289 vs 291; away PP 269 vs 273; 4v4 47.9 vs
    47.6; 3v3 37.8 vs 38.0.
  - The empty-net states run about 5% long, and 5v3 about 0.6 s a game long.
  - Source: `reports/markov_gate_a_insample_2026-09-26.json`, `diagnostics_per_game`.
- **The xG bin also depends on the previous event class** (as well as manpower and the
  rebound flag).

### Step 0 audit (`reports/markov_step0_audit_2026-09-26.json`)

- **Play-by-play is cached for every game** in all six seasons: 952, 1,401, 1,400, 1,400,
  1,398 and 1,394 games, regular season and playoffs. Nothing is missing, and every shootout
  label matches a shootout period.
- **Fields:**
  - Coverage is 99.7–100%: situation codes (2020-21 to 2022-23 ≈ 99.7%), times, defending
    side, and x/y on every modelled event type (takeaways 95.5% in 2020-21).
  - `goalieInNetId` is on every unblocked shot with a goalie in net.
  - Blocked shots are owned by the shooting team in 100% of cases.
- **Blocked-shot coordinates mark the block, not the shot.**
  - The median distance to the attacked net is 22–25 ft for blocks, against 32–35 ft for
    missed shots and shots on goal. About 40% of blocks are within 20 ft, against 24–31%.
  - Point shots are the ones most often blocked, so shot origins would sit further out, not
    closer.
  - A blocked shot's `zoneCode` is relative to the blocking team. From 2023-24 about 8% read
    "O", so the coding changed.
- **The odds files have no totals or puck lines.** Every `over_under` and `spread` field is 0
  in every ESPN file, so there's no secondary totals benchmark for Gate C unless new data is
  fetched. 2022-23 has no opening lines (already known).
- **Scorer drift:** from 2024-25, giveaways roughly doubled (≈24k to ≈42k a season) and
  takeaways fell (≈20k to ≈13k). This is further evidence for `OTHER` as one merged class.
- **Penalty shots** (≈30–57 a season) are dropped from the chain, but their order is kept for
  the xG join.

### Layout as built

```
src/features/pbp_states.py        # play-by-play -> events, observed summaries, Step 0 audit
src/models/markov/transitions.py  # Stage A / B tables, penalty categories, xG bins
src/models/markov/timing.py       # penalty clock, censoring, dwell times, goalie pull
src/models/markov/simulate.py     # fit_chain, the game loop
src/backtest/markov_gate_a.py     # in-sample check and Gate A
tests/test_pbp_states.py, tests/test_markov_rules.py
data/processed/markov_events_<season>.csv.gz, markov_games_<season>.csv.gz
```
`goalies.py` from the design doc comes with Gate B.
