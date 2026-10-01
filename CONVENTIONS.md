# Conventions

The rulebook for Hobby Hero docs, data, numbers and the artifact. `../AGENTS.md` sits above
it; where the two disagree, AGENTS.md wins and this file gets fixed.

_Last updated: 2026-09-24_

## Docs

- **Date everything** that records a decision, a finding or a change of mind: `(2026-09-23)`.
- **Correct, don't overwrite.** A superseded line keeps its text, gets struck through or
  marked *Superseded <date>*, and points to what replaced it. History stays readable.
- **Stable IDs.** `OPEN-ITEMS.md` numbers are cited elsewhere, so they never get renumbered.
- **Record Dan's position in his words** when he gives one. When he hasn't, say so; never
  reconstruct it.
- **Pushback gets its own labelled line** (`**Pushback to keep (Claude, <date>):**`), so a
  later reader can tell Claude's objection from Dan's decision.
- **Private notes stay private** (2026-09-30). `OPEN-ITEMS.md`, `BACKLOG.md` and
  `PROJECT-CATALOG.md` live in `../private/hobbyhero/`, outside the public repo. Public files may
  cite them ("`OPEN-ITEMS.md` item 8") but never copy from them.
- **Where things go:** decisions and debt in `OPEN-ITEMS.md`, ideas in `BACKLOG.md`, a
  buildable plan in `SPEC-<topic>.md`. A SPEC is amended when a phase lands (what shipped,
  what was cut), not left stale.

## Python

Follow `../python-conventions.md`. Deterministic parts are Python. Every random process takes a
fixed seed. Nothing looks ahead: features use only games strictly before the target game
(`src/features/aggregate.py` compares by game sequence, not just date).

## Data

- **A snapshot is derived, never raw.** The artifact ships computed features, model trees and
  metrics, never boxscores, article text or transcripts.
- **Verified sources only.** For odds, only `data/raw/odds/espn/` is verified. The Canada-2-Way
  2025-26 file is fabricated (Milestone 2); never use it.
- **Know the quirks.** `sat_for` and `sat_against` are summed across skaters (about 5× real
  Corsi); only the CF% ratio is meaningful (`OPEN-ITEMS.md` item 8).

## Numbers

- **Calibration metrics lead:** log loss and Brier, against the closing line (as a benchmark)
  and a naive home-team baseline. Accuracy is secondary. ROI is not a metric we report.
- **Every figure cites its source file** (a report, or a named section of a doc), with the
  sample size.
- **Say the negative result plainly.** Milestone 2 found no edge over the closing market, and
  the page says so.

## Language

No "locks", "picks", "value bets", "units" or "fade". Write "the model gives MTL 54%", not "take
MTL". The market appears as numbers labelled *closing market*, never as a named book.

## The artifact

- **One self-contained page.** Inline CSS and JS, and `snapshot.json` data embedded or shipped
  alongside. No external fetches (the page cannot reach the NHL API anyway).
- ~~**Slate tokens, inlined.** Take the `:root` block from `../design/hobbyhero-tokens-slate.css`
  and derive every colour, face, size, space, radius and duration from it. Dark only, system
  fonts only. Paint the background explicitly.~~ *Superseded 2026-09-24 (v0.2, Dan: "more
  hockey themed"):*
- **Hobby Hero brand, inlined as tokens.** Navy #192168 to #0f1440, red #AF1E2D, white, and
  ice-blue rink lines. Montserrat (display) and Open Sans (body) from Google Fonts, the one
  font host the artifact allows, with system fallbacks. Dan's crossed-sticks logo is embedded
  as a data URI. Dark only; paint the background explicitly. Every colour comes from `:root`
  tokens.
- **Motion is light and optional.** CSS transitions and small SVG animations only. The page
  reads correctly with no motion, and `prefers-reduced-motion` turns it off.
- **Team colours are chips, not themes.** Each team gets background, foreground and accent,
  used on team badges and cards only. Everything else stays in the brand tokens. Check contrast.
- **No logos.** Team names, abbreviations and colours only.
- **Deterministic.** Same inputs, same output, every time. No `Math.random()`; if sampling is
  ever added, it uses a seeded PRNG. The page shows the model hash and the snapshot date.
- **Parity-tested.** The JS model output must match Python's on a held-out game set to within
  1e-6 before any number on the page is trusted.
- **No personal data, no gambling links, no secrets.** Screenshots for the showcase are taken in
  a demo state.
