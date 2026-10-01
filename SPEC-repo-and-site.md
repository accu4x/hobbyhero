# SPEC: own repo, then a site (option C)

_Drafted 2026-09-30 (Cowork). Status: **approved by Dan 2026-09-30; §4 steps 1–2 done in Cowork,
git setup handed to Claude Code.**_

## 1. Decision

Dan, 2026-09-30, choosing between three hosting options: **"Go with C."**

- **C = Latent Mirror's play host now, hobbyhero.app later.** The game ships as an installable
  app at `play.latentmirror.com/hobby-hero`, the same way Kestrel Nine ships at
  `play.latentmirror.com/kestrel-nine`. hobbyhero.app becomes the home only if the
  "LLM-formatted hockey source" idea (llms.txt, `.md` and `.json` per entity) becomes real,
  because that is the one part that needs a domain root.
- Rejected for now: **A** (hobbyhero.app straight away: needs the v3 site retired and DNS moved
  first) and **B** (Latent Mirror only, permanently: `llms.txt` would sit in a subfolder).
- Why C is cheap to change: every edition is a static build, so the domain is one route in
  `wrangler.jsonc` plus a redirect.

Earlier in the same session (2026-09-30), Dan's words: the repo should hold the artifact too
(*"I would eventually port the artifact as part of this repo"*), and he agreed the repo lives
in `workspace/`.

**This amends the 2026-09-23 decision** "delivered as a Claude artifact". The artifact stays as
one edition (the model lab); the game becomes a second edition. See `OPEN-ITEMS.md`,
*2026-09-30*.

## 2. The repo

- **`workspace/hobbyhero/` becomes the public repo.** No new folder, no parallel structure.
- **`source/repos/hobbyhero-app/` becomes legacy.** It stays as it is, reference only (team
  colours, the C# Monte Carlo, UI ideas). It is never made public: its history holds the v3
  login and Shopify code, and auditing every commit isn't worth it.
- **Git is Claude Code's job** (`../AGENTS.md`). Cowork writes the files below and hands off;
  Claude Code runs `git init`, creates the GitHub repo and pushes.
- **Name:** `accu4x/hobbyhero` (Dan: "my typical github"; the name itself is assumed).
- **Pattern: Kestrel Nine.** Private notes live outside the repo in `../private/hobbyhero/` and
  are never copied in. A leak check fails closed and runs before every push.

### Public and private

| Path | Public? | Why |
|---|---|---|
| `src/`, `tests/`, `run_pipeline.py`, `requirements.txt` | Yes | The pipeline is the point. |
| `artifact/src/`, `artifact/build.py` | Yes | Engine and page source. |
| `artifact/snapshot.json`, `artifact/dist/` | No (built) | Reproducible from source, as in Kestrel Nine. |
| `SPEC-*.md`, `CONVENTIONS.md`, `CLAUDE.md`, `README.md`, `docs/` | Yes, after a read-through | Kestrel Nine publishes its equivalents. |
| `OPEN-ITEMS.md`, `BACKLOG.md`, `docs/PROJECT-CATALOG.md` | **No: moved to `../private/hobbyhero/`** | Dan, 2026-09-30: "keep the open-items and backlog and reports in private area outside of repo"; the catalog too. |
| `docs/markov-simulator-design.md` | **No: moved to `../private/hobbyhero/`** | Dan's original design doc, kept as written; it states betting goals the spec later replaced (Dan, 2026-09-30: "let's move to private"). |
| `reports/` | **No: stays in place, Git ignores it** | The pipeline writes there from ~15 files; moving it means changing them all. Dan approved ignoring it in place. |
| `data/` (all of it) | **No** | Raw NHL API responses, odds files and Hockey Writers article corpora. The pipeline rebuilds them locally. |
| `archive/` | **No** | Old snapshots, and `CLAUDE.md` warns about archived `config.json` secrets. |
| `.env`, `.pytest_cache/`, `__pycache__/` | No | Already partly ignored. |

**Leak check** (`tests/leak_check.py`, modelled on `kestrel-nine/artifact/test/leak.test.cjs`):
scans every file Git would publish (or a build, with `--dir`) for secret patterns (including
Shopify, Azure and JWT from v3), the phrases in `../private/hobbyhero-denylist.txt` (required)
and `../private/employer-denylist.txt`, forbidden paths (`data/`, `archive/`, `reports/`, the
private notes, handovers, `config.json`) and files over 2 MB. Fails closed.

**Closing lines are public** (Dan, 2026-09-30): "per-game closing lines can go into public
builds. People might want that data as a data point." Labelled *closing market*, never a named
book.

**Licences:** `LICENSE` (MIT, the code) and `LICENSE-WRITING` (CC BY-NC-SA 4.0, the Markdown
documents and page prose), the same split as Kestrel Nine (Dan: "yes licenses are good").

## 3. Editions, one dataset

| Edition | Where | What |
|---|---|---|
| Model lab | Claude artifact (as today) | The matchup lab and honest scoreboard. |
| Game (later) | `play.latentmirror.com/hobby-hero` | Installable, offline app: set lines, replay a season, forecast. |
| Data pages (if ever) | hobbyhero.app | `llms.txt`, `.md` and `.json` per team and player. |

All three are built from one snapshot folder written by the Python pipeline. The site build
follows Kestrel Nine: `build.py --site` writes `artifact/dist/site/` with a manifest, a service
worker and a `_headers` CSP; `wrangler.jsonc` is an assets-only Worker on the path route
`play.latentmirror.com/hobby-hero*` (the play host was built for more apps on their own paths).

**Before hobbyhero.app is used:** check for unspent Shopify credits on the v3 site, find where
v3 is hosted, and move the domain's DNS to Cloudflare. None of this blocks C.

## 4. Order of work

1. **This spec** approved (Dan).
2. **Repo setup** (Cowork writes, Claude Code commits): `.gitignore` per §2, `LICENSE`, the
   leak test, a README without the known-bad "93.8%" claim (item 10). No pipeline changes.
3. **First site edition:** today's lab page as an installable app on the play host. This
   proves the build and deploy path before the game exists. Plan: `SPEC-site-edition.md`
   (proposed 2026-10-01).
4. **Player ratings** — its own SPEC with a pre-registered test: ratings count as real only if
   lineups built from them improve the Poisson engine out of sample.
5. **The game**, in phases, each playable on its own: single game with lines → season replay
   against the real team's result → forecast mode. Its own SPEC.

## 5. Open questions

Tracked as `OPEN-ITEMS.md` item 27.
