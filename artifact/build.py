"""Build the Hobby Hero page from artifact/src/ and artifact/snapshot.json.

    python artifact/build.py           the Claude artifact: one file, dist/hobby-hero.html
    python artifact/build.py --site    the installable site: dist/site/hobby-hero/ and _headers

One source, two outputs (SPEC-site-edition.md). The artifact inlines everything and takes its
fonts from Google Fonts. The site edition ships separate files under a policy that allows no
inline script or style: self-hosted fonts, the snapshot split by season, a manifest, icons and
a service worker. History: SPEC-artifact-v0 Phase 3 (2026-09-24), SPEC-site-edition Phases 1
and 2 (2026-10-01).
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import re
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "src"
DIST = HERE / "dist"

SLUG = "hobby-hero"  # the path on play.latentmirror.com; a route does not strip it
GROUND = "#0a0d26"  # --hh-ground in app.css
MAX_BYTES = 2_000_000  # the same limit tests/leak_check.py holds a build to
# The market is "closing market", never a named source or book (CONVENTIONS.md, Language).
BANNED_NAMES = re.compile(
    r"\b(ESPN|DraftKings|FanDuel|BetMGM|Caesars|bet365|PointsBet|Pinnacle|Betway|BetRivers"
    r"|theScore|Polymarket|Kalshi)\b",
    re.IGNORECASE,
)
BINARY = (".png", ".webp", ".woff2")
ICONS = (  # file, size, share of the square the logo fills (maskable keeps to the safe zone)
    ("icon-192.png", 192, 0.78),
    ("icon-512.png", 512, 0.78),
    ("icon-maskable-512.png", 512, 0.56),
    ("apple-touch-icon.png", 180, 0.78),
)


def source(name: str) -> str:
    return (SRC / name).read_text(encoding="utf-8").strip("\n")


def compact(data: object) -> str:
    return json.dumps(data, separators=(",", ":"))


def build_artifact(snap: dict) -> None:
    # The snapshot goes in last, so nothing inside the data is ever read as a placeholder.
    parts = {
        "/*__CSS__*/": source("app.css"),
        "/*__ENGINE__*/": source("engine.js"),
        "/*__APP__*/": source("app.js"),
        "/*__LOGO__*/": source("logo.datauri").strip(),
        "/*__SNAPSHOT__*/": compact(snap).replace("</", "<\\/"),
    }
    page = (SRC / "template.html").read_text(encoding="utf-8")
    for placeholder, value in parts.items():
        if page.count(placeholder) != 1:
            raise SystemExit(f"template.html must hold {placeholder} exactly once")
        page = page.replace(placeholder, value)
    DIST.mkdir(exist_ok=True)
    out = DIST / "hobby-hero.html"
    out.write_text(page, encoding="utf-8")
    print(out, out.stat().st_size // 1024, "KB")


def split_snapshot(snap: dict) -> dict[str, dict]:
    """core plus one part per season, each small enough to pass MAX_BYTES. app.js puts them
    back together, so the split is checked here by doing the same and comparing."""

    def label(season: int) -> str:
        return f"{str(season)[:4]}-{str(season)[6:]}"

    def part(name: str) -> dict:
        return seasons.setdefault(name, {"states": {}, "games": []})

    season_of = {d["d"]: label(d["season"]) for d in snap["dates"]}
    season_col = snap["game_cols"].index("season")
    seasons: dict[str, dict] = {}
    for key, state in snap["states"].items():
        part(season_of[key])["states"][key] = state
    for game in snap["games"]:
        part(label(game[season_col]))["games"].append(game)

    states = {key: state for p in seasons.values() for key, state in p["states"].items()}
    games = [game for p in seasons.values() for game in p["games"]]
    same_order = list(states) == list(snap["states"])
    if not same_order or states != snap["states"] or games != snap["games"]:
        raise SystemExit("splitting by season changed the order or content of states or games")

    core = {key: value for key, value in snap.items() if key not in ("states", "games")}
    core["season_files"] = list(seasons)
    return {"core": core, **seasons}


def read_logo() -> tuple[bytes, object]:
    from PIL import Image  # build-only dependency: requirements-dev.txt

    head, _, payload = source("logo.datauri").strip().partition(",")
    if head != "data:image/webp;base64":
        raise SystemExit("logo.datauri is expected to be a base64 WebP data URI")
    raw = base64.b64decode(payload)
    return raw, Image.open(io.BytesIO(raw)).convert("RGBA")


def draw_icon(logo: object, size: int, fill: float) -> bytes:
    from PIL import Image

    icon = Image.new("RGBA", (size, size), GROUND)
    mark = logo.resize((round(size * fill),) * 2, Image.LANCZOS)
    offset = (size - mark.width) // 2
    icon.alpha_composite(mark, (offset, offset))
    out = io.BytesIO()
    icon.convert("RGB").save(out, format="PNG", optimize=True)
    return out.getvalue()


def site_shell() -> str:
    """index.html: the page body from template.html inside a document of its own."""
    template = (SRC / "template.html").read_text(encoding="utf-8")
    title = re.search(r"<title>(.*?)</title>", template).group(1)
    description = re.search(r'<meta name="description" content="(.*?)">', template).group(1)
    body = template[template.index('<div class="page">'):template.index("<script")].strip()
    if body.count("/*__LOGO__*/") != 1:
        raise SystemExit("template.html must hold /*__LOGO__*/ exactly once in the page body")
    body = body.replace("/*__LOGO__*/", "logo.webp")
    if re.search(r"\sstyle=|<style|<script", body, re.IGNORECASE):
        raise SystemExit("the page shell carries inline style or script")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{title}</title>
<meta name="description" content="{description}">
<meta name="theme-color" content="{GROUND}">
<link rel="manifest" href="manifest.webmanifest">
<link rel="icon" href="icon-192.png" type="image/png">
<link rel="apple-touch-icon" href="apple-touch-icon.png">
<link rel="stylesheet" href="fonts.css">
<link rel="stylesheet" href="app.css">
</head>
<body>
{body}
<script src="engine.js" defer></script>
<script src="app.js" defer></script>
<script src="site.js" defer></script>
</body>
</html>
"""


def site_files(snap: dict) -> dict[str, bytes]:
    """Every file of the site edition except sw.js, which is derived from the rest."""
    manifest = {
        "name": "Hobby Hero",
        "short_name": "Hobby Hero",
        "description": "An NHL goal model: win chances, likely scores and an honest scoreboard.",
        "id": f"/{SLUG}/",
        "start_url": f"/{SLUG}/",
        "scope": f"/{SLUG}/",
        "display": "standalone",
        "background_color": GROUND,
        "theme_color": GROUND,
        "icons": [
            {"src": name, "sizes": f"{size}x{size}", "type": "image/png",
             "purpose": "maskable" if "maskable" in name else "any"}
            for name, size, _ in ICONS if name.startswith("icon-")
        ],
    }
    logo_bytes, logo = read_logo()
    files: dict[str, bytes] = {
        "index.html": site_shell().encode(),
        "app.css": (source("app.css") + "\n").encode(),
        "engine.js": (source("engine.js") + "\n").encode(),
        "app.js": (source("app.js") + "\n").encode(),
        "site.js": (source("site.js") + "\n").encode(),
        "fonts.css": (SRC / "fonts" / "fonts.css").read_bytes(),
        "manifest.webmanifest": (json.dumps(manifest, indent=2) + "\n").encode(),
        "logo.webp": logo_bytes,
    }
    for name, size, fill in ICONS:
        files[name] = draw_icon(logo, size, fill)
    for font in sorted((SRC / "fonts").iterdir()):
        if font.suffix in (".woff2", ".txt"):
            files[f"fonts/{font.name}"] = font.read_bytes()
    for name, data in split_snapshot(snap).items():
        files[f"data/{name}.json"] = compact(data).encode()
    return files


def check_site_files(files: dict[str, bytes]) -> None:
    for name, data in files.items():
        if len(data) > MAX_BYTES:
            raise SystemExit(f"{name} is {len(data):,} bytes, over the {MAX_BYTES:,} limit")
        if name.endswith(BINARY):
            continue
        hit = BANNED_NAMES.search(data.decode("utf-8"))
        if hit:
            raise SystemExit(f"{name} names an odds source or a sportsbook: {hit.group(0)}")


def build_site(snap: dict) -> None:
    files = site_files(snap)
    check_site_files(files)

    # The headers are hashed too: the worker caches the page with its headers, so a policy-only
    # change must still change sw.js, or installed clients keep the old policy.
    headers = (SRC / "site-headers.txt").read_text(encoding="utf-8").replace("{SLUG}", SLUG)
    digest = hashlib.sha256(b"_headers" + headers.encode())
    for name in sorted(files):
        digest.update(name.encode())
        digest.update(files[name])
    cache = "hh-" + digest.hexdigest()[:12]
    # './' is the start URL; index.html is not listed, since the host redirects it to './'.
    # The licence texts ship with the fonts but need no place in the offline cache.
    precache = ["./"] + [n for n in files if n != "index.html" and not n.endswith(".txt")]
    worker = source("sw.js") + "\n"
    worker = worker.replace("'/*CACHE*/'", json.dumps(cache))
    worker = worker.replace("/*FILES*/[]", json.dumps(precache))
    if "/*CACHE*/" in worker or "/*FILES*/" in worker:
        raise SystemExit("sw.js placeholders were not filled")
    files["sw.js"] = worker.encode()

    root = DIST / "site"
    # Empty rather than remove the folder: Windows refuses to delete a directory a shell is in.
    root.mkdir(parents=True, exist_ok=True)
    for child in root.iterdir():
        shutil.rmtree(child) if child.is_dir() else child.unlink()
    for name, data in files.items():
        path = root / SLUG / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (root / "_headers").write_bytes(headers.encode())
    total = sum(len(data) for data in files.values())
    print(f"built dist/site/{SLUG}/ ({len(files)} files, {total / 1024:.0f} KB, cache {cache})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--site", action="store_true", help="build the installable site edition")
    args = parser.parse_args()
    snap = json.loads((HERE / "snapshot.json").read_text(encoding="utf-8"))
    if args.site:
        build_site(snap)
    else:
        build_artifact(snap)


if __name__ == "__main__":
    main()
