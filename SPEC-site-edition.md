# SPEC: the site edition (the lab page as an installable app)

_Status: **done. Live at <https://play.latentmirror.com/hobby-hero/> since 2026-10-01** (see
*Build log*). Approved by Dan 2026-10-01 (he merged it and said "start phase 1"); all three
phases landed the same day. This is step 3 of
`SPEC-repo-and-site.md` §4. Dan answered the five open choices on 2026-10-01 (§2)._

## 1. Goal

Today's lab page, unchanged in what it does, served as an installable, offline-capable app at
`play.latentmirror.com/hobby-hero`. It proves the build and deploy path before the game exists.

**Non-goals:** any game mode, player ratings, a nightly data refresh, an install button or other
site-only features, hobbyhero.app. Each has its own step in `SPEC-repo-and-site.md` or an entry
in `BACKLOG.md`.

## 2. Decisions (Dan, 2026-10-01)

His answers, in his words: *"self-host, yes one source 2 outputs, closing market, split by
season, yes in public repo."*

| Choice | Decision |
|---|---|
| Fonts on the site | **Self-host** Montserrat and Open Sans, so the site matches the artifact. |
| Does the Claude artifact keep shipping from this build? | **Yes: one source, two outputs.** Closes `OPEN-ITEMS.md` item 27's last open question. |
| The benchmark label in the data, which names its source | **"Closing market"**, as `CONVENTIONS.md` already requires. |
| Data files over the leak check's 2 MB limit | **Split by season.** The limit stays as it is. |
| Where this plan lives | **This file, in the public repo.** |

## 3. What has to change, and why

Measured on the snapshot built 2026-09-29 (`artifact/snapshot.json`, 3,865 KB minified):

- **Everything is inline.** `template.html` holds one style block, three script blocks and ten
  `style=` attributes: five in the static markup and five that the app script writes into
  generated markup. The play host's policy (Kestrel Nine's `site-headers.txt`) allows no inline
  script or style.
- **Fonts come from Google Fonts.** A third-party host fails the same policy and fails offline.
- **The data is one 3.9 MB blob.** By section: team states 2,621 KB, games 754 KB, parity
  fixtures 188 KB, everything else 302 KB. By season the states are about 525 KB each and the
  games about 150 KB each, for five seasons (2021-22 to 2025-26).
- **`meta.benchmark` names the odds source.** The page never displays it, but it ships in the
  data.
- **Only Dan's machine can build it.** The snapshot is trained from `data/`, which is private
  and local, so there is no CI build or deploy.

## 4. Design

### One source, two outputs

`artifact/src/` becomes: `template.html` (the page shell), `app.css`, `app.js`, `engine.js`,
`logo.datauri`, `sw.js`, `site-headers.txt` and `fonts/`.

| Command | Output | How it differs |
|---|---|---|
| `python artifact/build.py` | `dist/hobby-hero.html` | Single file, everything inlined, snapshot embedded, fonts from Google Fonts. The artifact, as today. |
| `python artifact/build.py --site` | `dist/site/hobby-hero/` and `dist/site/_headers` | Separate files, data split, fonts self-hosted, manifest, icons, service worker. |

`app.js` reads the snapshot through one loader. In the artifact it parses the embedded JSON; on
the site it fetches the data files and merges them into the same object. Nothing below the
loader knows which edition it is in.

No `style=` attribute survives in either edition. Static ones become classes. Generated ones
(team colour chips, the score grid's shading) become build-generated team classes or CSSOM
property sets. The
site build fails if the shell contains an inline script, an inline style or a `style=`.

### Site files

```
dist/site/_headers
dist/site/hobby-hero/
  index.html  app.css  fonts.css  engine.js  app.js  site.js  sw.js  manifest.webmanifest
  logo.webp  icon-192.png  icon-512.png  icon-maskable-512.png  apple-touch-icon.png
  fonts/      Montserrat and Open Sans, woff2, with their licence texts
  data/core.json            models, teams, dates, schedule, metrics, levers, parity (~490 KB)
  data/<season>.json        that season's team states and games (~675 KB each)
```

- **Data.** All files load in parallel at start, because the scoreboard spans every season.
  Loading a season on demand is a later optimisation and not needed here. ~~The parity fixtures
  stay out of the site build; `parity_test.cjs` reads them from `snapshot.json`.~~
  *Corrected 2026-10-01 (Phase 1):* the page runs the engine check on load and reports the
  result in its footer, so the fixtures (188 KB) ship on the site too, inside `core.json`.
- **Fonts.** The two families' woff2 files (Latin subset) and their SIL Open Font License texts
  are committed under `artifact/src/fonts/`. They are downloaded once, from the fonts' official
  releases, with Dan's OK at that point.
- **Icons.** Drawn at build time from `logo.datauri` with Pillow. No hand-made image files.
- **Service worker.** Kestrel Nine's `sw.js` pattern: the cache name is a content hash of every
  file plus `_headers`, the whole build is precached, old caches are deleted on activate, and
  `sw.js` itself is served `no-cache`. Cache prefix `hh-`.
- **Headers.** Kestrel Nine's policy plus `font-src 'self'`. The only outside script allowed is
  the Cloudflare Web Analytics beacon the zone injects, as on Kestrel Nine. *Phase 2:* `data:`
  images are not allowed either, since nothing on the page needs one.
- **Updates.** A new deploy is fetched in the background and shown on the next visit. An open
  page is not reloaded under the reader.
- **Storage.** The page's `localStorage` key (`hh-v4`) works on the site as it is.

### The benchmark label

`src/export/artifact_snapshot.py` writes `meta.benchmark` as "Closing market moneyline,
de-vigged (comparison only, never an input)". The site build fails if any file in it names an
odds source or a sportsbook; ~~the list of names lives in the test~~ the list of names is
`BANNED_NAMES` in `build.py` (*corrected in Phase 2*: the build is what refuses, so the list
sits with it). Per-game closing lines stay
in the data (Dan, 2026-09-30, `SPEC-repo-and-site.md` §2).

### Deploy

`wrangler.jsonc` at the repo root: an assets-only Worker named `hobby-hero` on the route
`play.latentmirror.com/hobby-hero*`, serving `./artifact/dist/site`, with `workers_dev` and
`preview_urls` off. The play host's DNS record already exists. Deploying is
`npx wrangler deploy` from Dan's machine under his Cloudflare sign-in, and Claude asks before
each one. Undoing it is `npx wrangler rollback`, or deleting the Worker to take the path down.

## 5. Tests

| Check | What it proves |
|---|---|
| `node artifact/src/parity_test.cjs` | The engine still matches Python to 1e-6 after the split. |
| `python tests/leak_check.py --dir artifact/dist/site` | No secret, private phrase or file over 2 MB in the build. |
| Build-time checks in `build.py --site` | No inline script or style, no `style=`, no named odds source. |
| `python tests/smoke_site.py` (Playwright, headless) | Served under its real `_headers`: the page loads with no policy violation or console error, a fixed matchup gives the same numbers as the artifact build, and it starts again with the network off. |

Pillow and Playwright are build and test tools only; they go in a `requirements-dev.txt`, not
in the pipeline's `requirements.txt`.

## 6. Phases

Each is its own pull request; Dan merges.

1. **Split the source.** `app.css` and `app.js` come out of `template.html`, `style=` goes, the
   loader goes in, the export's label changes. `build.py` still writes the same single-file
   artifact. Parity passes. No visible change.
2. **`build.py --site` and its tests.** Fonts, data split, manifest, icons, service worker,
   `_headers`, `smoke_site.py`, `wrangler.jsonc`. Amend `CONVENTIONS.md`, *The artifact*, for
   the site edition (separate files, self-hosted fonts).
3. **Deploy and record.** Dan deploys. Check the live URL against §7. Amend this spec's status,
   `SPEC-repo-and-site.md`, `README.md` and `OPEN-ITEMS.md` together. A link from the Latent
   Mirror showcase page is a change in that repo.

## 7. Acceptance

- `play.latentmirror.com/hobby-hero/` gives the same win %, score grid and scoreboard figures as
  the artifact for a fixed matchup and date.
- After one visit it starts with the network off.
- A browser offers to install it.
- No policy violation in the console, and no request leaves the origin except the analytics
  beacon.
- `leak_check.py --dir` is clean on the build, and no file names an odds source or a sportsbook.
- The artifact build is still one self-contained file and still passes parity.

## Build log

### Phase 1 (2026-10-01)

- **Shipped.** `template.html` is the page shell only; the style is `src/app.css` and the app
  script is `src/app.js`. `build.py` puts them back, so the artifact is still one file. The
  pure move was committed on its own: that build matched the previous one line for line,
  blank lines aside.
- **No `style=` left.** The five in the markup became classes, listed at the end of
  `app.css`. Of the five the script wrote: three became classes, team chips are coloured
  after insertion by the existing `styleChip`, and the score grid's shading is set through
  the element's style object. Both are allowed under the site policy, which forbids the
  attribute in markup and not styles set from script.
- **The loader** is `loadSnapshot()` at the top of `app.js`. It has the artifact's path only;
  the site path arrives with its test in Phase 2.
- **The label.** The export writes "Closing market moneyline". Re-running it on unchanged
  data gave the same snapshot apart from `meta.built_at` and that label (same model hash).
- **Checked.** Parity passes (393 rows, 46 models). The old and new builds were rendered
  headless at 1280 px and 390 px through six views each (today's schedule, a playoff day, a
  regular day, the off-season, a loaded game, all seasons with sides swapped on neutral
  ice): every element's position, size and computed style, the page text and the pixels
  matched. Removing one of the new rules on purpose made the same check fail. After the
  re-export the only difference is the build date in the footer.
- **Cut from this phase:** nothing. **Found:** the parity fixtures correction in §4.

### Phase 2 (2026-10-01)

- **Shipped.** `python artifact/build.py --site` writes `artifact/dist/site/`: 26 files,
  about 4.3 MB before compression. `tests/smoke_site.py`, `wrangler.jsonc` and
  `requirements-dev.txt` are new. `CONVENTIONS.md`, *The artifact*, is amended.
- **Fonts.** Dan approved the download on 2026-10-01 ("Yes, download all six"). The four
  `.woff2` files are the ones the artifact page fetches from Google Fonts today (Montserrat
  Latin, and Open Sans Latin, symbols and math: 165 KB together), with the two licence texts.
  `fonts.css` keeps Google's character ranges and rule order.
- **Data.** `core.json` (502 KB) and five season files (about 690 KB each), every one under the
  2 MB limit. `core.json` lists the season files, and `loadSnapshot()` fetches them together.
  The build puts the parts back together itself and refuses to write anything if the result
  differs from the snapshot in content or order.
- **Icons** are drawn from the logo at build time. The logo is 192 px, so the 512 px icons are
  enlarged from it and are slightly soft. A larger logo file would fix that.
- **Checked.** `tests/smoke_site.py` passes its 13 checks under the real headers: the policy is
  served with no inline allowance, the engine check passes on the page, a fixed matchup shows
  the same 20 fields as the artifact build, script-set styles survive, the four fonts load from
  this origin, the service worker installs and takes control, the manifest and icons are
  served, no request leaves the origin, and the page restarts with the server stopped and the
  network off. `tests/leak_check.py --dir artifact/dist/site` is clean. Parity passes.
- **Checked once, by hand-run script:** the site and the artifact (wrapped in a standards-mode
  document) rendered pixel for pixel the same at 1280 px and 390 px in three views each.
- **The checks can fail.** An inline style, an inline script and an outside image planted in
  the built page failed the smoke test; a named source in the data and a file over 2 MB each
  made the build refuse.
- **Not checked:** the browser's install prompt itself (headless Chromium does not show one),
  and anything on Cloudflare: the route, the real headers and the analytics beacon are Phase 3.

### Phase 3 (2026-10-01)

- **Deployed** by Dan's word ("deploy hobby hero"), from his machine with `npx wrangler deploy`,
  after he merged Phase 2. Worker `hobby-hero`, route `play.latentmirror.com/hobby-hero*`,
  25 assets, built from `main` with the same cache name as the Phase 2 build.
- **Before the deploy,** on the merged `main`: parity, `leak_check.py --dir`, and all 13 smoke
  checks passed. The path returned 522 until then.
- **§7 against the live URL**, headless: the fixed matchup shows the same 20 fields as the
  artifact build; the engine check passes; the fonts load; the service worker installs and takes
  control; the page starts with the network off after one visit; no console error or policy
  violation; the only request off the origin is the Cloudflare analytics beacon. The headers
  are served as written, `sw.js` is `no-cache`, and `/hobby-hero` redirects to `/hobby-hero/`.
  Over the wire the data is about 1.6 MB compressed. Kestrel Nine, on the same host, still
  answers.
- **Not checked:** the browser's install prompt (headless Chromium shows none). Try it once in
  a real browser.

## 8. Open

- **Staleness. Pushback to keep (Claude, 2026-10-01):** the snapshot's last game is 2026-06-14
  and it was built 2026-09-29, the 2026-27 opening night. With no refresh in scope, the site
  shows last season's states against this season's schedule, and falls further behind each
  day. The artifact has the same gap; an installable app at a public address makes it more
  visible. Decide before linking the site from anywhere whether a manual rebuild-and-deploy
  routine is enough, or whether the refresh in `BACKLOG.md` comes first. ~~Dan has not given a
  position on this.~~ **Dan, 2026-10-01:** ship now and "add a season refresh in the
  backlog". It is there (`BACKLOG.md`, *Season refresh for the site edition*); its cadence is
  not decided. Until it runs, the site shows last season's form against this season's
  schedule, and the page says so in its own words.
