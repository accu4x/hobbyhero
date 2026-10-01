# Hobby Hero

NHL game simulation, built as a hobby. The question it keeps asking: **how close can public
data get to simulating hockey accurately?** Every estimate is scored out of sample against the
closing betting market, used as a benchmark only. Hobby Hero is not a betting tool and never
names or links a sportsbook.

**The honest result so far:** public data gets close to the closing market but not past it.

## Editions

All editions are built from one dataset written by the Python pipeline in this repo.

| Edition | Where | Status |
|---|---|---|
| Model lab | A Claude artifact: matchups, a look-back over past games, the 2026-27 schedule | Live |
| Game | An installable app at `play.latentmirror.com/hobby-hero`: set your lines, replay a real season against the real team's result, forecast the future | Planned |

Plan and rationale: `SPEC-repo-and-site.md`.

## Run the pipeline

```
pip install -r requirements.txt
python run_pipeline.py --backtest        # walk-forward backtest
```

The raw data is not in this repo. The pipeline rebuilds it from the public NHL API
(`api-web.nhle.com`) into `data/`, which Git ignores. Odds files and article corpora are kept
private and are not redistributed.

## Layout

- `src/`: ingest, features, models (the Poisson engine and the Markov simulator), backtests,
  the snapshot export.
- `artifact/`: the page engine (`engine.js`, parity-tested against Python) and template.
- `docs/`: design notes and lessons, including `LESSONS.md` (why the earlier LLM-written
  simulator was abandoned).
- `SPEC-*.md`: buildable plans. `CONVENTIONS.md`: how docs, data and numbers are written.
- `CLAUDE.md`: orientation for agents working in this repo.

Before every push: `python tests/leak_check.py`.

## Licence

- **Code:** MIT (`LICENSE`).
- **Writing** (the Markdown documents and the prose on the pages): CC BY-NC-SA 4.0
  (`LICENSE-WRITING`).

Made by Dan Peterson, [latentmirror.com](https://latentmirror.com).
