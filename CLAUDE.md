# Hobby Hero

NHL game simulation. **A hobby** (decided 2026-09-23): no hard deadline and no monetization
driver, and never a betting tool. ~~The delivery mechanism is a **Claude artifact**: a
self-contained page on claude.ai.~~ *Amended 2026-09-30 (Dan: "Go with C"):* two editions from
one dataset. The **Claude artifact** stays as the model lab; a **game** (set lines, replay a
real season against the real team's result, forecast) will ship as an installable app at
`play.latentmirror.com/hobby-hero`, as Kestrel Nine does. See `SPEC-repo-and-site.md`.

**This is a public repo** (<https://github.com/accu4x/hobbyhero>, since 2026-09-30). Run
`python tests/leak_check.py` before every push. The `pre-push` hook does it for you once a clone
has run `git config core.hooksPath .githooks`.

## Read first, in this order

1. `../AGENTS.md`: workspace rules, hard constraints, Python conventions pointer, Slate design
   system. It is the system of record above this file.
2. This file.
3. `CONVENTIONS.md`: how docs, data, numbers and the artifact are written.
4. `../private/hobbyhero/OPEN-ITEMS.md`: every decision so far, dated, with the open questions.
5. `../private/hobbyhero/BACKLOG.md`: ideas and the long-game direction.
6. The current SPECs: `SPEC-artifact-v0.md` (the lab page) and `SPEC-site-edition.md` (its
   site edition, proposed 2026-10-01).

## Private notes

`OPEN-ITEMS.md`, `BACKLOG.md`, `PROJECT-CATALOG.md`, Dan's `markov-simulator-design.md` and any
handovers live outside this repo in
`../private/hobbyhero/` (Dan, 2026-09-30). Read them when present. Never copy them, or a
paraphrase of them, into the repo: not into code, docs, commit messages or PRs. Where this
repo cites "`OPEN-ITEMS.md` item N", that is the private file. `reports/` stays in this folder
because the pipeline writes there, but Git ignores it.

## The rules that matter most

- **Ask, then plan, then build.** Ask Dan whatever is ambiguous, show him the plan, and wait
  for his explicit OK before writing code or files.
- **No betting.** The closing line is an accuracy benchmark. Never link to a sportsbook, never
  name one in the artifact, and do no staking, bankroll, EV or "value bet" work.
- **Deterministic first.** Math computes every number. The LLM only turns qualitative input into
  quantities and writes thin, cached garnish. Why: `docs/LESSONS.md` (the output-token trap).
- **Honest numbers only.** Every accuracy figure cites the report it came from. The "93.8%"
  claim in `hobbyhero-app/README.md` is known-bad (`OPEN-ITEMS.md` item 10). Never repeat it.
- **Out-of-sample or it didn't happen.** No hand-picked evaluation sets. Dan is a lifelong Habs
  fan, and `hw_articles/montreal-canadiens.jsonl` is one of the largest corpus files. Check
  Montreal isn't over-represented.
- **Secrets stay out.** Nothing from any archived `config.json` goes near an artifact, a doc or
  a commit.
- **Git:** ~~this folder is not a repo yet (2026-09-23).~~ The public repo `accu4x/hobbyhero`
  since 2026-09-30. Every change goes through a branch and a pull request, and merging is
  Dan's call. A Cowork session never runs git (`../AGENTS.md`). Repo work belongs to Claude
  Code.

## Two source folders

| Folder | Role |
|---|---|
| `workspace/hobbyhero/` (this one) | **System of record.** The Python pipeline, docs, decisions and the artifact source. |
| `source/repos/hobbyhero-app/` | The older .NET product (Blazor, Azure Functions, C# Monte Carlo). **Reference only** for artifact work: team colours, the Monte Carlo, UI ideas. Changes there go through Claude Code and that repo's PR flow. |

Latent Mirror (`workspace/latent-mirror/`) holds only pointers to this project and the
*posts* about it. Hobby Hero decisions are recorded **here**, not there.

## Layout

- `src/ingest/`: NHL schedule and boxscores, ESPN closing moneylines, Hockey Writers articles.
- `src/features/`: `aggregate.py` (the enriched feature set the current model uses),
  `build_features.py` (the older form-only set), `qualitative_*.py` (LLM and keyword scorers).
- `src/models/`: XGBoost moneyline and margin models. `src/backtest/`: walk-forward and edge
  vs market. `src/predict/`: next-day runner.
- `data/raw/nhl_api/`: **the source of truth for game data from 2026-09-24.**
  `raw/<season>/<game_id>/` holds the raw API responses (boxscore, landing, play-by-play,
  gzipped, never overwritten), and `boxscores_<season>.jsonl` is derived from them by
  `src/ingest/nhl_api_season.py`. The fetch log is in `logs/`.
- `data/raw/` (older files, kept as history; see `OPEN-ITEMS.md` items 13–17 before using
  any of them): boxscores (2020-21 and 2021-22 plain, 2025-26 Hockey-Reference plus
  enrichment), score-only game files, odds (only `odds/espn/` is verified), article corpora.
- `docs/showcase/`: the Latent Mirror screenshot (`hobby-hero.png`) and `preview.html`, the
  wrapped page it was captured from.
- `../private/hobbyhero/PROJECT-CATALOG.md` (moved out of `docs/` 2026-09-30): the whole
  project indexed by decision point, experiment and content hook. Start here for history or
  Latent Mirror content.
- `docs/`: `LESSONS.md`, `simulator-design.md`, and as history `2026-season-plan.md` and
  `README-superseded-2026-09-23.md`.
- `reports/`: backtest results and milestone assessments. Private: Git ignores it.
- `src/export/`: `artifact_snapshot.py` (trains the Poisson engine and writes `artifact/snapshot.json`)
  and `team_colors.py`.
- `artifact/`: `snapshot.json`, `src/` (`engine.js`, `parity_test.cjs`, `template.html`, `logo.datauri`), `build.py`,
  and the built page `dist/hobby-hero.html`. Rebuild steps: `SPEC-artifact-v0.md`, *Build log*.

## Recording

Decisions, tech debt and open questions go in `../private/hobbyhero/OPEN-ITEMS.md`, dated,
with stable numbers. Ideas go in `../private/hobbyhero/BACKLOG.md`. When a SPEC phase lands or
a decision reverses, amend the SPEC and `OPEN-ITEMS.md` together.

## Environment

**Run everything from Claude, on Dan's Windows Python.** (2026-09-24) Dan manages Hobby Hero
from one place, Claude. Claude kicks off the deterministic Python, and the Python does the work.
From a Cowork session (interactive or scheduled), that means Desktop Commander's
`start_process` with `powershell.exe` on Dan's machine, the same pattern as the Latent Mirror
weekly reading task. Verified 2026-09-24: Python 3.14.7, xgboost 3.4.1, pandas 3.0.0,
scikit-learn 1.8.0, and `api-web.nhle.com` returns 200.

- **Don't use** the Cowork VM (`device_bash`) or the cloud sandbox for training or fetching.
  Neither has network access to the NHL API (the VM has no outbound network at all), and the VM
  has no xgboost. `device_bash` is fine for reading and editing files.
- Scheduled runs need the computer on and the Claude desktop app running. A missed night gets
  backfilled.
