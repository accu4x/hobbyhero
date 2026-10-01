# SPEC: the qualitative ledger

_Status: approved by Dan 2026-09-24 ("yes go ahead as laid out")._
_Progress 2026-09-24: steps 0–2 done; step 3 pilot reviewed. **Batch scoring is running** as
the Cowork scheduled task "Hobby Hero ledger scoring": every 6 hours, 150 packets a run
through `src/ledger/batch.py`, finishing in about 4–5 days, then the rollup runs. Step 6
layers were tested (OPEN-ITEMS item 22): none beat the base. Step 4 runs when scoring
completes. Step 5 is parked until step 4 shows viability (Dan)._

## Question

Does qualitative information about a team (articles, transcripts, news), turned into dated
scores, add anything to the model **that the model and the closing market don't already
have**? Dan's framing (2026-09-24): *"integrate articles, video transcripts, hockey news
archives … ranking teams in various dimensions maybe by weekly entries … or add bonuses,
penalties for things like coaching, chemistry, morale, health."*

Bonuses and penalties are the same experiment with hand-set weights. We fit the weights
instead. The fitted weights are readable as "win-probability points per point of health",
and if the test passes they become the artifact's qualitative levers.

## Dan's choices (2026-09-24)

- **Timeline: historical first.** Recommended alongside: a forward-only ledger from opening
  night (step 5).
- **Dimensions:** health/lineup, coaching, morale/chemistry, plus the existing seven
  (offense, defense, physical, coaching, x_factor, goaltending, special teams). Coaching is in
  both lists, so there are **9 dimensions**.
- **Sources:** YouTube transcripts, Hockey Writers (on disk), The Hockey News archive, daily
  news and injury reports.
- **Scorer: Claude, in Cowork.**

## Known limits, stated up front

1. **The scorer knows history.** When Claude scores a 2025-26 article, it may already know how
   that season ended. Team names, cities and nicknames get blinded before scoring, but player
   names stay, so the blinding is partial. **Every historical result is labelled "historical,
   may be contaminated."** Only the forward ledger (step 5) is leak-free.
2. **Historical sources are only what's on disk.** YouTube transcripts and daily news can't be
   collected for past seasons at any sensible rate (the browser method manages about 10
   transcripts a night). The historical pass uses Hockey Writers and The Hockey News; the
   other two join the forward ledger.
3. **The data can only detect large effects.** The model and the market are about 0.008 apart
   in log loss on 746 games. A null result is an answer, not a failure.
4. **The rule from `../AGENTS.md`: extract only what a box score can check.** It becomes a test
   instead of a ban. The **facts** group (health/lineup, coaching, goaltending) and the
   **feelings** group (morale/chemistry, x_factor) are scored and tested separately.

## Steps

### 0. Base model (OPEN-ITEMS item 18)
Compare the current 86-feature XGBoost, a slim feature set, logistic regressions and a
regularised XGBoost on identical walk-forward folds, with the market gap and a bootstrap CI
(`src/backtest/compare_models.py`). The best one becomes the baseline the ledger is tested
against.

### 1. Coverage audit (read-only)
Per team-week article counts. What's on disk so far (2026-09-24):
- **Hockey Writers** (`data/raw/hw_articles/`): 14,313 articles, **all 2025-26**. About
  69M characters, roughly 17M tokens. The in-season median is 11.5 articles per team-week, and
  every team has articles in at least 26 of about 28 regular-season weeks, **except Utah**:
  its articles are missing, and the `arizona-coyotes` file has one article (a category-mapping
  gap in `fetch_hw_articles.py`). Coverage is uneven: Toronto 1,356, Edmonton 1,161, Montreal
  814, Nashville 248. Title keywords include lineup (1,427), trade (1,439), takeaway (858),
  injury (362) and preview (264). So this is **not** the retrospective-only corpus Milestone 2
  audited (that was `HockeyWriters_Articles_Flat`, 976 files).
- **The Hockey News:** `C:\Users\hn2_f\source\python\TheHockeyNews_Articles`, outside the
  connected folders. Access needed before it can be audited.
- **Consequence:** the historical pass is **2025-26 only** unless older Hockey Writers seasons
  are fetched through the WordPress API that `fetch_hw_articles.py` already uses (a later
  option).

### 2. Ledger design
- **Score one article at a time, once.** One row per (article, team) in
  `data/ledger/articles_<season>.jsonl`:
  `{article_id, source, team, published_at, blinded: true, scores: {dim: -1..+1 | null},
  evidence: {dim: "short quote"}, confidence: 0..1, scorer, prompt_version, scored_at}`.
  `null` means "the article says nothing about this dimension", which is not the same as 0.
- **Blinding** (`src/ledger/blind.py`): team names, cities, nicknames and abbreviations become
  `TEAM_A` (the subject team) and `TEAM_B…` (others), done deterministically before the text
  reaches the scorer. The mapping is stored, and the scorer never sees it.
- **Filter before scoring:** regular-season window only, and titles or leads about the team's
  current state. That includes lineup, injury, preview, takeaways, recaps, coaching and trade,
  and excludes history, all-time lists, draft and prospect profiles. Keep the most relevant
  **3 per team-week**, with each body truncated to about 2,000 characters. Estimated cost:
  32 × 28 × 3 articles × about 600 tokens ≈ **1.6M input tokens a season**, with small outputs.
- **Weekly rollup** (`src/ledger/rollup.py`, plain Python): for each (team, week), a
  recency-weighted mean of article scores **published before the Monday of that week**,
  written to `data/ledger/team_weeks_<season>.jsonl`. It joins to games as `ledger_<dim>_delta`
  (home minus away), exactly like the box-score features.
- **Prompt:** `src/ledger/prompt_v1.md`, versioned. Every score must cite a quote from the
  text. The prompt forbids outside knowledge ("score only what this text says").

### 2a. What was built (2026-09-24)
- `src/ledger/blind.py`: team names, cities, nicknames, abbreviations, upper-case magazine
  layouts, and `@handles`, protecting "Wild Card". Adjacent duplicate labels are collapsed.
- `src/ledger/select_articles.py`: builds `data/ledger/queue_<season>.jsonl`. 2025-26:
  **2,177 packets, 32 teams, 28 weeks, ~1.03M input tokens.** Up to 3 per team-week, at most
  one per title stem, Morning Recaps excluded. The THN path comes from `HOBBYHERO_THN_DIR`.
- `src/ledger/prompt_v1.md`, and `src/ledger/record.py` (validates, append-only, refuses
  re-scores, version = hash of the prompt file).
- `src/ledger/rollup.py`: team-week values, recency half-life 2 weeks, 6-week window, only
  articles published before the week's Monday.

### 3. Pilot
About 20 blinded 2025-26 articles are scored in-session and shown to Dan **before any batch
run**. If they make sense, a nightly Cowork scheduled task scores batches (same pattern as the
catalog inbox drain) until 2025-26 is done, with measured cost per article.

### 3a. Pilot findings (2026-09-24, 20 articles, `pilot_review_2026-09-24.md`)
- **Blinding is cosmetic.** Player names (Bedard, Celebrini, Ovechkin), arenas (United
  Center, Enterprise Center) and owners (Madison Square Garden) identify most teams at a
  glance. The scorer could name nearly every TEAM_A. That confirms limit 1: historical
  results must carry the contamination label, and only the forward ledger is a clean test.
- **Signal is sparse and factual.** 6 of 20 came back all-null: 3 league-wide Morning
  Recaps (now excluded), 2 lineup posts where TEAM_A's section was cut by truncation, and 1 THN
  valuation feature. Health was scored most often (8 of 20); physical was never scored.
- **Fix to consider:** for "Projected Lineups", pull the subject team's `Injured:` and
  goalie lines instead of truncating at 2,000 characters.
- **The in-season THN team reports are features** (prospect rankings, franchise valuations,
  player profiles), not team-state reports. They cost budget for little signal; consider
  dropping them from the historical pass.

### 4. Pre-registered test (written before looking)
- The baseline is the step-0 winner. Treatments add the facts group, the feelings group, and
  all 9 dimensions.
- Folds, games and test window are identical to the baseline.
- Primary metric: log loss. Secondary: Brier. The comparison is a paired bootstrap of the
  per-game log-loss difference, with a 90% CI, reported against the baseline and against the
  closing market.
- **Pass:** the CI of (treatment − baseline) is entirely below 0. Anything else is reported as
  "no detectable effect".
- All numbers are labelled *historical, may be contaminated*. No peeking and re-tuning: a
  prompt change means a new `prompt_version` and a fresh pre-registration.

### 4a. Result (2026-09-25): failed
Every treatment made log loss worse, with every CI above 0 (facts +0.0030, feelings +0.0031, all
+0.0134 on the registered data). Details: `OPEN-ITEMS.md`, *2026-09-25: ledger test result*;
`reports/ledger_test_2026-09-25.json`. There is no re-tuning; a follow-up needs a fresh
pre-registration.

### 5. Forward ledger (from opening night, 2026-09-29)
It runs the same scorer and schema, adding YouTube transcripts (the catalog's browser method)
and daily news, on a weekly Cowork scheduled task. This is the leak-free version of the
test, readable around mid-season.

### 6. Box-score layers (Dan, 2026-09-24): done, see OPEN-ITEMS item 22
Player level (starting goalie, missing lineup), more stats (5v5 shot share, special teams,
back-to-backs), last season, recency weighting, plus the Elo and Poisson models. Built from
the NHL API data already on disk. Result: nothing beats the base; the starting goalie is
the only faint positive, and the 3-model average ties the market. ~~The ledger test in step 4
uses the base plus the goalie layer as the baseline.~~ **Superseded 2026-09-24:** the step 4
baseline is ~~the xG + GSAx logistic model~~ **the chosen engine: Poisson goals on xG + GSAx +
Corsi + special teams** (goalie sv% dropped: GSAx covers it; Dan, 2026-09-24; `OPEN-ITEMS.md` item 25). It still
trails the market once pooled over two windows. Step 4 compares
(a) xG vs xG + ledger (facts / feelings / all) and (b) each against the closing market, on
2025-26 with 4 chronological folds, using the same reading rule as item 23.

## Out of scope for now
Player-level scoring, anything the page would show before the test passes, and odds as an
input (a separate BACKLOG item).
