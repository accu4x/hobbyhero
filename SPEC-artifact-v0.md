# SPEC: Hobby Hero artifact v0.1, team A vs team B

_Status: proposed 2026-09-23. **v0.1 built and published privately 2026-09-24** (see *Build log*)._
_**Amended 2026-09-24 (Dan): the engine is logistic regression, not XGBoost trees.** It's the
best base on the corrected data (`OPEN-ITEMS.md` item 18). In JS it is one dot product plus
a sigmoid, so no tree walker is needed, and its levers move smoothly instead of in steps.
Wherever this spec says "trees", read "coefficients"._
_**Amended again 2026-09-24 (Dan): the engine is Poisson goals.** Two Poisson regressions
(home goals, away goals) on xG + GSAx + Corsi for/against + PP%/PK% (goalie sv% dropped the same day, since GSAx covers it). The market
is never an input. In JS: two dot products, `exp`, then a 12×12 Poisson grid. That gives
the win % (ties split by the historical home share in OT/SO) and a **score distribution**
("most likely 3–2"). Levers move the xG, Corsi, special-teams and goalie (GSAx) inputs. Evidence:
`OPEN-ITEMS.md` items 23–25. Phase 1 exports coefficients, scaler means/SDs and the tie
share instead of trees. Phase 2 parity tests the win % and the score grid._

## Goal

A self-contained Claude artifact where anyone picks two NHL teams and gets a deterministic
win probability from the Hobby Hero XGBoost model, built on last season's (2025-26) team stats,
with levers to move each team's key stats and watch the probability respond.

It's a hobby. There is no deadline; we play and see how far we get.

## Non-goals (v0.1)

The scorecard and centaur tracks, LLM calls, stored data (no `db`), live or per-game odds,
the 2026-27 schedule, player-level sims, score distributions, the C# Monte Carlo. All of these
are in `BACKLOG.md`.

## Phase 1: export (Python, new files only)

A new script, `src/export/artifact_snapshot.py`, that does not edit existing modules:

1. Load `data/raw/nhl_api/boxscores_*.jsonl` (2023-24 to 2025-26; `run_pipeline.py --data
   nhl_api`), and build events and features with `src/features/aggregate.py`. Updated
   2026-09-24: previously the Hockey-Reference 2025-26 file, which carries the shootout label
   bug (`OPEN-ITEMS.md` item 13).
2. Train the moneyline model (and the margin model, for expected margin) with the settings in
   `src/models/models.py`. Decide raw vs calibrated (`OPEN-ITEMS.md` item 4) and the training
   seasons (item 1).
3. Write `artifact/snapshot.json`:
   - `meta`: snapshot date, source files, training games, model hash, xgboost version, and the
     feature list in model order.
   - `model`: the moneyline trees (and margin trees) in a compact array form, plus base score.
   - `teams`: for each of the 32 teams, 2025-26 full-season values for every base stat in
     `aggregate.py` (win_rate, s_pct, sv_pct, pdo, cf_pct, pp_pct, pk_pct, shots, shots
     against, pim, sat_*, takeaways, giveaways, blocked shots, hits, shifts), with name,
     abbreviation and colour set.
   - `levers`: for each lever, the stat(s) it drives, and a min/max taken from the 1st–99th
     percentile of training values.
   - `metrics`: the walk-forward backtest numbers the page quotes (accuracy, log loss, Brier,
     and the same metrics for the ESPN closing market on the matched games), with source and
     sample size.
   - `parity`: about 200 held-out feature rows plus the Python probabilities, for the JS test.
4. **Runs on Dan's Windows Python** (`OPEN-ITEMS.md` item 7).

## Phase 2: JS model evaluator + parity

- A plain-JS tree walker, about 40 lines: sum the leaf values, add the base score, apply a
  sigmoid.
- A parity check over `snapshot.parity`: the maximum absolute difference must be ≤ 1e-6. It
  runs once at build time and also as a hidden self-check on the page that disables the result
  panel if it fails.

## Phase 3: UI

- **Header:** Hobby Hero, snapshot date, model hash.
- **Matchup picker:** away team @ home team, plus a Home / Away / Neutral toggle.
  - Neutral = mean of p(A beats B with A at home) and 1 − p(B beats A with B at home). The model
    has no home-ice feature, because home advantage is baked into how it's trained.
- **Result:** win probability for each team and expected margin. Say plainly that these are
  model outputs, not predictions of certainty.
- **Team cards**, in team colours: the 2025-26 values for the lever stats.
- **Levers, per team:** CF%, Sh%, Sv%, PP%, PK% and penalties (PIM per game). Each shows the
  base value, the adjusted value and the change, limited to the training range, with a
  reset-all button.
  - Lever mechanics (`OPEN-ITEMS.md` items 2–3): the baseline is the full-season average placed
    in every window (w5, w10, w20, w40, exp). A lever shifts its stat across all windows and
    recomputes the features derived from it (`pdo` from Sh% and Sv%). `win_rate` and the other
    non-lever stats stay at baseline.
  - The trees respond in steps. Say so in one line near the levers.
- **"What this is and isn't" panel:**
  - Model: XGBoost on rolling team form, 2025-26, walk-forward.
  - ~~Quoted numbers: accuracy 56.2% (enriched) vs 53.3% (boxscore-only model); Brier 0.2475;~~
    **Superseded 2026-09-24:** quote only numbers from the corrected NHL API data
    (`reports/market_compare_nhl_api_2026-09-24.txt`, or whatever replaces it after
    `OPEN-ITEMS.md` item 18). The line below is kept as history.
  - Old quoted numbers: accuracy 56.2% (enriched) vs 53.3% (boxscore-only model); Brier 0.2475;
    log loss 0.6886 (`docs/2026-season-plan.md`, backtest table, 186 games).
  - Against the closing market: model log loss 0.690 vs market 0.673, Brier 0.2485 vs 0.2400
    (`reports/milestone2_qualitative_assessment.md`, section 6, 186 games). **These figures
    describe the boxscore-only model, not the enriched one.** Phase 1 recomputes the market
    comparison for the model actually shipped, and the page quotes that number.
  - **"No edge over the closing market."**
  - Opening-night caveat: it uses last season's stats and knows nothing about offseason moves.
  - No sportsbook names or links. The market is labelled *closing market*.
- **Style:** `CONVENTIONS.md`, *The artifact*.

## Build log (v0.1, 2026-09-24)

All three phases landed. Where this log and the phase text above disagree, this log wins; the
phase text is kept as the plan it was.

- **Phase 1, export.** `src/export/artifact_snapshot.py` writes `artifact/snapshot.json`
  (157 KB, model hash `41d07f1bcca9`).
  - Features: 19 home-minus-away deltas. xG layers (10: xg_share, xgf, xga, xg5_share, finish
    at half-lives 10 and 40), goalie GSAx, and Corsi for/against plus PP%/PK% at half-lives
    10 and 40. (The module docstring says 18; it is 19.)
  - Model: StandardScaler + `PoissonRegressor(alpha=0.1)` for home goals and for away goals,
    trained on 2023-24 to 2025-26 (4,172 games, NHL API data). Ties after regulation plus
    overtime are split by the historical home OT/SO share, 0.526.
  - Team state: end of the 2025-26 regular season (2026-04-16). Starting goalie = most starts
    in the team's last 20 games.
  - Lever ranges: the league range across teams, widened by 25%.
  - Team colours: `src/export/team_colors.py`, 32 teams, foreground picked by contrast.
  - Quoted numbers (this exact spec, re-scored by the export step):

    | window | games | model log loss | acc | closing market log loss | gap [90% CI] |
    |---|---|---|---|---|---|
    | W1, 2024-25 from Jan 15 | 609 | 0.6612 | 59.9% | 0.6491 | +0.0121 [+0.0029, +0.0211] |
    | W2, 2025-26 full | 1,306 | 0.6835 | 53.4% | 0.6818 | +0.0017 [−0.0034, +0.0071] |
    | pooled | 1,915 | | | | +0.0050 [+0.0005, +0.0097] |

- **Phase 2, JS engine + parity.** `artifact/src/engine.js` (`HHEngine`): two dot products,
  `exp`, a 12×12 Poisson grid. `artifact/src/parity_test.cjs` over 200 test games:
  max difference 2.2e-15. The same check runs on the page and is reported in the footer.
  Neutral venue = the mean of both orientations (a team against itself at neutral = 0.500).
- **Phase 3, UI.** `artifact/src/template.html` + `artifact/build.py` →
  `artifact/dist/hobby-hero.html` (144 KB, no external requests, system fonts, Slate dark).
  - Picker: away @ home, Home ice / Neutral, Swap sides. Default CAR at FLA.
  - Result: win % for each side, expected goals, chance level after overtime, most likely
    score, the five likeliest scores and a 7×7 score heatmap.
  - Levers per team (8): xG for, xG against, finishing, starting-goalie GSAx, shot attempts
    for and against, PP%, PK%. A lever moves both half-lives by the same amount and
    recomputes the xG shares. Per-team and global reset.
  - "What this is, and what it isn't": the table above and **"No edge over the market."**
- **Published** 2026-09-24 as a **private** claude.ai artifact, "Hobby Hero Matchup Lab". Not
  shared; share only when Dan says.
- **Acceptance status:** same numbers across reloads (deterministic, verified); parity passes;
  no external requests; banned-word scan clean. "Opens for a stranger with a share link" is
  untested until Dan shares it.
- **Rebuild:** run `python -m src.export.artifact_snapshot`, then `node artifact/src/parity_test.cjs`,
  then `python artifact/build.py`, and republish from the same Claude conversation (or pass
  the artifact URL) so the link stays the same.

## v0.2: date control, look-back, hockey restyle (planned and built 2026-09-24)

Decisions: `OPEN-ITEMS.md`, *2026-09-24 (late): artifact v0.2 scope*.

- **Export.**
  - Two feature passes: xG shot model on 2023-24 for 2024-25 dates, and on 2023-24 + 2024-25
    for 2025-26 dates.
  - A Poisson refit at the start of every month that has games, trained on games before
    that month.
  - Team state each game-day morning, stored as changes only.
  - A game log (2024-25 and 2025-26, playoffs included) with each game's model %, expected
    goals, starting goalies, closing market % and moneylines, and the final score (with OT/SO).
  - An off-season state after the last game, using a model trained on everything. This is the
    default date.
  - Core feature modules get optional `daily_out` / `team_rows_out` hooks only. Their default
    behaviour doesn't change.
- **Engine.** The model and team state are picked by date. Parity is checked on sampled games
  from several monthly models (≤ 1e-6).
- **UI.**
  - Named "Hobby Hero"; brand tokens (`CONVENTIONS.md`); SVG rink behind the result.
  - A date control with previous/next game-day steps.
  - The day's games: click one to load it into the lab with the actual starters.
  - A model vs closing market scoreboard over a chosen range (log loss, Brier, accuracy,
    games), over every game in the range.
- **Unchanged:** the pre-registered W1/W2 numbers stay in the honesty panel, cited as such.
  The monthly look-back is a separate, labelled measure.

### v0.2 build log (2026-09-24)

- **Snapshot:** 1.5 MB, 19 models (18 monthly plus off-season), 435 game days, 2,791 look-back
  games, 113 goalies. Model-set hash `faf2b5dcc775`. The page is 1.56 MB.
- **Pre-registered numbers are unchanged** from v0.1 (W1 +0.0121, W2 +0.0017, pooled +0.0050
  [+0.0005, +0.0097]), so the export reproduces them.
- **Monthly walk-forward look-back** (the page's scoreboard; 174 games with no closing line are
  left out):

  | range | games | model log loss | closing market | gap [90% CI] | model acc | market acc |
  |---|---|---|---|---|---|---|
  | 2024-25 | 1,311 | 0.6655 | 0.6584 | +0.0071 [+0.0013, +0.0133] | 59.6% | 61.3% |
  | 2025-26 | 1,306 | 0.6831 | 0.6818 | +0.0012 [−0.0037, +0.0066] | 53.7% | 55.3% |
  | both | 2,617 | 0.6743 | 0.6701 | +0.0042 [+0.0003, +0.0081] | 56.7% | 58.3% |

  Same verdict as the pre-registered test: no edge over the market; 2025-26 is within noise.
  **Amended 2026-09-24 (later):** playoff closing lines were fetched, so all 2,791 games now
  have a line. The look-back reads +0.0045 [+0.0007, +0.0084] for both seasons, and W2 and the
  pooled pre-registered numbers moved by 0.0001 (`OPEN-ITEMS.md`).
- **Parity:** 180 sampled games across all 19 models, max difference 3.1e-15. Consistency:
  all 2,791 games recomputed from the stored morning form plus the actual starters match
  Python to 5.9e-6.
- **UI:**
  - Brand bar with Dan's logo (WebP data URI, `artifact/src/logo.datauri`); center-ice SVG
    rink behind the result; a puck that rides the win bar.
  - Date input plus previous/next game day and an Off-season button.
  - "That day's games" shows the model, the closing market % plus both moneylines, and the
    final score. **Load** puts a game into the lab with its real starters.
  - The scoreboard (season to date / full season / both) has a running log-loss gap chart.
  - Very dark team colours are lifted on the win bar so they read on navy.
- **Checked:** no page errors; no horizontal scroll at 400 px; banned-word scan clean; the only
  external requests are Google Fonts (allowed by `CONVENTIONS.md` as amended).
- **Republished** to the same private artifact (version 2):
  https://claude.ai/artifact/47HzJJXs1KM7zZapvtgoGE. Not shared.
- **Known quirks:**
  - Early-season points on the running chart are noisy, so the first 25 games are
    hidden.
  - The date input can't grey out non-game days; the page moves to the next game day and
    says so.

## 2026-27 schedule (built 2026-09-29)

- Adds the season's 1,344 regular-season games from `src/ingest/nhl_schedule.py`, as the
  snapshot's `schedule` block.
- Each game gets a pre-season estimate: the off-season model on end-of-2025-26 form, with
  last season's main starters in goal.
- The date control runs to the last scheduled day, and the page opens on today's game day.
- Scheduled games stay out of the scoreboard.
- The model hash is unchanged. Details and checks: `OPEN-ITEMS.md`, "2026-09-29: 2026-27
  schedule on the page".
- Published as artifact version 7.

## Acceptance

- Opens on claude.ai for a stranger with a share link, with no login or capability prompts.
- Same inputs give the same numbers across reloads and machines.
- Parity check passes.
- No external requests (checked in the network panel).
- Nothing on the page matches: sportsbook names, "pick"/"lock"/"value", the 93.8% claim,
  personal data.

## Open questions (tracked in OPEN-ITEMS)

Items 1 (training seasons), 2 (lever coherence), 3 (lever baseline), 4 (calibration),
5 (team colours).
