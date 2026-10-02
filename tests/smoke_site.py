"""Headless smoke test of the site edition: python tests/smoke_site.py [screenshot-dir]

Build both editions first (python artifact/build.py, then python artifact/build.py --site).
Needs Python Playwright with Chromium (requirements-dev.txt). Serves artifact/dist/site locally
with the _headers rules applied, so the real policy is enforced, then checks: the page loads
with no console error or policy violation and no request to another origin; the self-hosted
fonts are in use; styles set from script survive the policy; a fixed matchup shows the same
numbers as the artifact build; the manifest and its icons are served; and, with the server
stopped and the network off, a reload still starts the page from the service worker.

Modelled on kestrel-nine/artifact/test/smoke_site.py. Not a pytest module (the name doesn't
match test_*.py): it needs a build and a browser, so it is run on purpose.
"""
from __future__ import annotations

import fnmatch
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

DIST = Path(__file__).resolve().parents[1] / "artifact" / "dist"
ROOT = DIST / "site"
SLUG = "hobby-hero"
SHOTS = Path(sys.argv[1]) if len(sys.argv) > 1 else None

# The matchup both editions are asked for: a regular-season day, its first game, every season
# on the scoreboard. The ids are the page's own.
MATCHUP_DATE = "2024-01-27"
FACT_IDS = ("pctA", "pctB", "nameA", "nameB", "xgLine", "soLine", "topLine", "toplist", "grid",
            "games", "dayNote", "boardNote", "board", "boardVerdict", "metrics", "verdict",
            "engineCheck", "modelHash", "builtAt", "dateNote")
SET_DATE = """(iso) => { const d = document.getElementById('date'); d.value = iso;
  d.dispatchEvent(new Event('change', { bubbles: true })); }"""
READ_FACTS = "(ids) => Object.fromEntries(ids.map((id) => [id, document.getElementById(id).innerText]))"
LOADED = "() => document.getElementById('modelHash').textContent !== ''"


def load_headers() -> list[tuple[str, list[tuple[str, str]]]]:
    rules: list[tuple[str, list[tuple[str, str]]]] = []
    for line in (ROOT / "_headers").read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line[0].isspace():
            rules.append((line.strip(), []))
        else:
            name, _, value = line.strip().partition(":")
            rules[-1][1].append((name.strip(), value.strip()))
    return rules


class Handler(SimpleHTTPRequestHandler):
    rules = load_headers()
    # Windows can map .js to text/plain in the registry, which nosniff would then refuse.
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".js": "text/javascript",
                      ".css": "text/css", ".json": "application/json", ".woff2": "font/woff2",
                      ".webp": "image/webp", ".webmanifest": "application/manifest+json"}

    def end_headers(self) -> None:
        path = self.path.split("?")[0]
        for pattern, headers in self.rules:
            if fnmatch.fnmatchcase(path, pattern):
                for name, value in headers:
                    self.send_header(name, value)
        super().end_headers()

    def log_message(self, *args: object) -> None:
        pass


def check(cond: bool, msg: str) -> None:
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        check.failed = True  # type: ignore[attr-defined]


check.failed = False  # type: ignore[attr-defined]


def matchup_facts(page: Page) -> dict[str, str]:
    """Ask the page for the fixed matchup and read back every number it shows."""
    page.evaluate(SET_DATE, MATCHUP_DATE)
    page.click("#games button.load >> nth=0")
    page.click("#rangeSeg button[data-range=both]")
    return page.evaluate(READ_FACTS, list(FACT_IDS))


def main() -> int:
    artifact = DIST / "hobby-hero.html"
    if not artifact.exists() or not (ROOT / SLUG / "index.html").exists():
        print("Build both editions first: python artifact/build.py, then with --site.")
        return 1
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    base = f"{origin}/{SLUG}/"
    errors: list[str] = []
    requests: list[str] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        options = {"viewport": {"width": 1280, "height": 900}, "locale": "en-CA",
                   "timezone_id": "America/Toronto"}

        # The artifact build is the reference: same source, same snapshot, one file.
        ref_ctx = browser.new_context(**options)
        ref = ref_ctx.new_page()
        ref.goto(artifact.as_uri())
        ref.wait_for_function(LOADED)
        expected = matchup_facts(ref)
        ref_ctx.close()

        ctx = browser.new_context(**options)
        page = ctx.new_page()
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("request", lambda r: requests.append(r.url))
        page.goto(base)
        page.wait_for_function(LOADED)
        policy = page.evaluate("fetch(location.href).then(r => r.headers.get('content-security-policy'))")
        check("default-src 'self'" in (policy or "") and "'unsafe-inline'" not in (policy or ""),
              "policy header served, with no inline allowance")
        check("Engine check passed" in page.inner_text("#engineCheck"), "the engine check passes on the page")

        got = matchup_facts(page)
        different = [k for k in FACT_IDS if got[k] != expected[k]]
        check(not different, f"the fixed matchup shows the same numbers as the artifact build ({len(FACT_IDS)} fields)")
        for k in different:
            print(f"    {k}: site {got[k][:80]!r} vs artifact {expected[k][:80]!r}")

        # Styles set from script (team chips, the score grid, the win bar) must survive the policy.
        painted = page.evaluate("""() => {
          const bg = (el) => getComputedStyle(el).backgroundColor, clear = 'rgba(0, 0, 0, 0)';
          return { chip: bg(document.getElementById('chipA')) !== clear,
                   listChip: bg(document.querySelector('#games .chip')) !== clear,
                   grid: new Set([...document.querySelectorAll('#grid td')].map(bg)).size > 3,
                   bar: bg(document.getElementById('barA')) !== clear }; }""")
        check(all(painted.values()), f"team chips, score grid and win bar are coloured ({painted})")

        page.evaluate("document.fonts.ready")
        fonts = page.evaluate("""() => ({ display: document.fonts.check('700 16px Montserrat'),
          body: document.fonts.check('400 16px "Open Sans"'),
          used: getComputedStyle(document.querySelector('h1')).fontFamily })""")
        font_files = [u for u in requests if u.endswith(".woff2")]
        check(fonts["display"] and fonts["body"] and "Montserrat" in fonts["used"]
              and font_files and all(u.startswith(base + "fonts/") for u in font_files),
              f"self-hosted fonts are loaded ({len(font_files)} files)")

        ready = page.evaluate("Promise.race([navigator.serviceWorker.ready.then(() => true),"
                              " new Promise((r) => setTimeout(() => r(false), 10000))])")
        check(ready, "service worker installs within 10 s")
        if not ready:
            browser.close()
            return 1
        page.reload()
        page.wait_for_function(LOADED)
        check(page.evaluate("!!navigator.serviceWorker.controller"), "service worker controls the page")
        manifest = page.evaluate("fetch('manifest.webmanifest').then(r => r.json())")
        check(manifest["start_url"] == f"/{SLUG}/" and manifest["scope"] == f"/{SLUG}/"
              and manifest["display"] == "standalone", "manifest start_url, scope and display")
        icons = page.evaluate("""(icons) => Promise.all(icons.map(async (i) => {
          const r = await fetch(i.src); if (!r.ok) return false;
          const img = await createImageBitmap(await r.blob());
          return `${img.width}x${img.height}` === i.sizes; }))""", manifest["icons"])
        check(len(icons) == 3 and all(icons), "manifest icons are served at their stated sizes")
        if SHOTS:
            page.screenshot(path=str(SHOTS / "site.png"), full_page=True)

        foreign = sorted({u.split("/")[2] for u in requests if not u.startswith(origin)})
        check(not foreign, f"no request left the origin ({', '.join(foreign) or 'none'})")

        # Offline start: server gone, network off.
        server.shutdown()
        ctx.set_offline(True)
        page.reload()
        page.wait_for_function(LOADED, timeout=10000)
        check(matchup_facts(page) == expected, "offline reload starts the page and shows the same numbers")
        check(page.evaluate("document.fonts.check('700 16px Montserrat')"), "offline: the fonts still load")
        if SHOTS:
            page.screenshot(path=str(SHOTS / "site-offline.png"), full_page=True)
        browser.close()

    check(not errors, f"no console errors or policy violations ({len(errors)})")
    for e in errors:
        print("   ", e[:200])
    return 1 if check.failed else 0  # type: ignore[attr-defined]


if __name__ == "__main__":
    sys.exit(main())
