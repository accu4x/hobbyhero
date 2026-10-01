# SPEC: two market tests (pre-registration)

_Status: written 2026-09-25, before any look at the data these tests use. **Locked 2026-09-25**
(Dan: "Run as written"). Results: see *Result* at the end._

Dan asked whether any creative metric could find an edge (2026-09-25). These are the two cheap
tests he picked ("yes let's do 1 and 2"). Both use data already on disk. The market stays a
**yardstick, never a model input**, and nothing here is betting work: no staking, no ROI, no
picks (`CONVENTIONS.md`, `../AGENTS.md`). Every number is labelled *historical*.

## Shared inputs

- **Model probabilities:** the walk-forward look-back in `artifact/snapshot.json` (monthly
  refits on earlier games only, model set `9ed12b50a8ae`, built 2026-09-25).
- **Market:** ESPN archives in `data/raw/odds/espn/` (the only verified odds source). Both
  sides are de-vigged the same way as the closing line, with the multiplicative method in
  `market_edge.de_vig`. A line is valid when both sides are American odds with |ml| ≥ 100.
- **Games:** regular season and playoffs. Games missing the needed line are dropped and
  counted in the report.
- **Seed** 42. **Bootstrap** 2,000 resamples of games. **CI** 90%.
- Code goes in `src/backtest/market_tests.py`, and the report in
  `reports/market_tests_<date>.json` plus a short `.md`.

## Test 1: does the model anticipate how the line moves?

**Question.** Lines move between open and close as information arrives. If the model's
disagreement with the *opening* line predicts the direction of that move, the model holds
real information early, even though it doesn't beat the close.

**Sample.** 2023-24 to 2025-26, games with a valid opening and closing line. 2022-23 is
excluded because its archive has no opening lines. That's about 4,200 games.

**Leak to remove first.** The look-back uses the goalie who actually started. Starters are
often confirmed after the line opens, and that news moves the line. So the **primary** model
here swaps in the team's *usual* starter as of that morning: the most starts in the last 20
games, with that goalie's GSAx from the stored morning state. That is information available
at the open. The actual-starter version is reported as secondary, and the difference between
the two shows how much of any signal is goalie news.

**Measures** (home-team probabilities):
- disagreement `d = p_model − p_open`
- move `m = p_close − p_open`

**Primary statistic.** The OLS slope β of `m` on `d` (no intercept term is tested), with a
bootstrap CI over games.

**Pass.** The 90% CI for β lies entirely above 0 in the primary (usual-starter) version.

**Secondary** (reported, not a pass condition):
- Sign agreement: among games where |m| ≥ 0.01 and |d| ≥ 0.02, the share where sign(d) ==
  sign(m), against 50%.
- β by season.
- β for the actual-starter version.
- The share of line moves the model "explained": R² of m on d.

**Reading rule.**
- A pass means the model carries information the opening line lacked. It says nothing about
  profit, and the report will say so.
- A fail means the model's disagreements with the open are noise, as far as the market is
  concerned.

## Test 2: does the market misprice particular teams ("fan tax")?

**Question.** Is the closing market's error one-sided for some teams, for example popular
teams priced too high? All teams are tested together, and none is picked in advance, the
Habs included.

**Sample.** 2022-23 to 2025-26, games with a valid closing line (about 5,490). Arizona
(2022-24) and Utah (2024-26) count as one franchise, so there are 32 franchises.

**Measure.** For each game and each of its two teams, the residual `r = won − p_close(team)`.
A team's bias is its mean residual. Negative means the market expected more wins than it
got, so the team was priced too high.

**Primary test: is team bias bigger than chance?**
- Statistic: `D = Σ_teams z_t²`, where `z_t` is the team's summed residual divided by
  `sqrt(Σ p(1−p))` over its games.
- Null: simulate every game's outcome from the closing probabilities 10,000 times and
  recompute D.
- p-value: the share of simulated D ≥ observed D. Each simulated game has exactly one winner,
  so a team's residuals and its opponents' stay linked, as in the real data.

**Secondary test: is it persistent?** Split the sample into 2022-23 + 2023-24 and 2024-25 +
2025-26. Correlate each team's bias across the two halves (Pearson, bootstrap CI over games
within each half).

**Pass.** p < 0.05 on the primary test **and** the split-half correlation's 90% CI is
entirely above 0. Bias that doesn't persist is noise, even if it shows up once.

**Also reported:**
- All 32 teams' bias with CIs, sorted, with Bonferroni-adjusted flags (family-wise 10%).
- The same table for the Hobby Hero model's residuals, to show whether it shares any bias the
  market shows.
- No team's result is highlighted unless the flags earn it.

**Known confound.** Multiplicative de-vigging spreads the margin evenly. A favourite–longshot
bias would show up as favourites looking over- or under-priced. So the report also shows
mean residual by closing-probability bin (0.30–0.40, …, 0.60–0.70). A team effect that is
really a favourite effect is labelled as such.

## What doesn't change after we look

The samples, measures, thresholds, seed and pass rules above. Anything new is a separate,
dated pre-registration.

## Result (2026-09-25)

Source: `reports/market_tests_2026-09-25.json` (`src/backtest/market_tests.py`, model set
`9ed12b50a8ae`). Label: *historical*.

### Test 1: PASS

The line moves toward the model.

- **Sample:** 4,170 games with an opening and a closing line (2023-24 to 2025-26).
- **Primary, usual starter:** β = **0.119** (90% CI **+0.095 to +0.149**), R² 0.045.
  - Among the 2,108 games with a real move (≥1 point) and a real disagreement (≥2 points),
    the line moved the model's way **61.2%** of the time.
  - Every season is positive: 0.105, 0.096, 0.162.
- **Secondary, actual starter:** β 0.157 (+0.134 to +0.186), R² 0.078, 64.9%. So about a
  quarter of the signal is goalie news.
- **Scale:** the model disagrees with the open by 4.1 points on average, and the line moves
  2.2 points on average. On average about 12% of the model's disagreement closes by puck
  drop.
- **Reading:**
  - The model holds information the opening line didn't, and the market takes it in by
    the close.
  - That is **not** an edge on the close. The closing line still beats the model (Test 1
    doesn't change that), and nothing here is about profit.
  - The most likely source is ordinary public news arriving after the open: the previous
    night's results, the updated xG form, and which goalie is expected. So the model updates
    the way the market does, just from box-score data. It's not a secret insight.
  - A cleaner follow-up would timestamp each opening line and rebuild the model's inputs as
    of that moment. The archive has no open timestamps, so that would be a new
    pre-registration.

### Test 2: FAIL

There's no team "fan tax".

- **Sample:** 5,492 games with a closing line (2022-23 to 2025-26), 32 franchises.
- **Dispersion:** D = 22.4 against a chance average of 31.9, p = **0.88**. Team-level market
  errors are, if anything, *smaller* than chance would produce.
- **Persistence:** r = **−0.22** (90% CI −0.36 to +0.17) between 2022-24 and 2024-26.
- **Bonferroni flags** (|z| > 2.955): **none**. Per the rule written in advance, no team is
  singled out. The full table is in the report, with the model's own residuals beside the
  market's.
- **Observation, not a pass condition:** the de-vigged close shows a favourite–longshot
  pattern.
  - Teams priced under 30% won 3.9 points less often than priced, and teams over 70% won
    3.9 points more often (545 team-games each, about 2 standard errors).
  - Part of this is known to come from multiplicative de-vigging.
  - Testing it properly would need its own pre-registration.
