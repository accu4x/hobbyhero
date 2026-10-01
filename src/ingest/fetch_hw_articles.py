"""Hockey Writers 2025-26 article ingestion via their WordPress REST API.

Pulls dated, team-categorized articles for the 2025-26 NHL season so they can
be used as point-in-time qualitative context (filtered to articles published
before each game => no leakage).

API: https://thehockeywriters.com/wp-json/wp/v2/posts?categories={id}
     &after={ISO}&before={ISO}&per_page=100&page={n}
The response includes a X-WP-Total header for pagination.

Output: one JSON line per article to data/raw/hw_articles/{team}.jsonl
with fields: id, date (ISO), link, title, content (plain text), categories.
Runs as a resumable background job: for each team it fetches posts in the
season window and appends missing ones.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set

import requests

BASE = "https://thehockeywriters.com/wp-json/wp/v2"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122"}

# team folder -> (category id, abbrev). Category ids confirmed from /categories.
# Note: Utah has no team category on HW; omitted.
TEAM_CATEGORIES = {
    "anaheim-ducks": (101, "ANA"),
    "arizona-coyotes": (36, "ARI"),
    "boston-bruins": (16392, "BOS"),
    "buffalo-sabres": (16399, "BUF"),
    "calgary-flames": (16398, "CGY"),
    "carolina-hurricanes": (16401, "CAR"),
    "chicago-blackhawks": (16400, "CHI"),
    "colorado-avalanche": (16402, "COL"),
    "columbus-blue-jackets": (16405, "CBJ"),
    "dallas-stars": (16403, "DAL"),
    "detroit-red-wings": (16404, "DET"),
    "edmonton-oilers": (16406, "EDM"),
    "florida-panthers": (16407, "FLA"),
    "los-angeles-kings": (16408, "LAK"),
    "minnesota-wild": (16409, "MIN"),
    "montreal-canadiens": (16410, "MTL"),
    "nashville-predators": (16417, "NSH"),
    "new-jersey-devils": (16418, "NJD"),
    "new-york-islanders": (16411, "NYI"),
    "new-york-rangers": (16419, "NYR"),
    "ottawa-senators": (16420, "OTT"),
    "philadelphia-flyers": (16421, "PHI"),
    "pittsburgh-penguins": (16422, "PIT"),
    "san-jose-sharks": (16423, "SJS"),
    "seattle-kraken": (111092, "SEA"),
    "st-louis-blues": (250, "STL"),
    "tampa-bay-lightning": (16424, "TBL"),
    "toronto-maple-leafs": (16425, "TOR"),
    # 2024-25 name; returns nothing for 2025-26 (checked 2026-09-24).
    "utah-hockey-club": (236503, "UTA"),
    # 2025-26 name. Category found 2026-09-24 via /categories?search=mammoth (585 posts).
    "utah-mammoth": (237257, "UTA"),
    "vancouver-canucks": (16426, "VAN"),
    "vegas-golden-knights": (19266, "VGK"),
    "washington-capitals": (16427, "WSH"),
    "winnipeg-jets": (16428, "WPG"),
}

# 2025-26 season window
SEASON_START = "2025-09-01T00:00:00"
SEASON_END = "2026-07-01T00:00:00"


class RateLimiter:
    def __init__(self, min_interval: float = 0.4):
        self._min = min_interval
        self._last = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        gap = now - self._last
        if gap < self._min:
            time.sleep(self._min - gap)
        self._last = now


def _strip_html(html: str) -> str:
    txt = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt


def fetch_posts(cat_id: int, after: str, before: str, limiter: RateLimiter,
                done_ids: Set[str]) -> List[Dict]:
    """Fetch all posts in a category + date window (paginated)."""
    out: List[Dict] = []
    page = 1
    while True:
        limiter.wait()
        try:
            r = requests.get(
                f"{BASE}/posts",
                params={"categories": cat_id, "after": after, "before": before,
                        "per_page": 100, "page": page,
                        "_fields": "id,date,link,title,content,categories"},
                headers=HEADERS, timeout=25,
            )
            if r.status_code != 200:
                print(f"  [!] HTTP {r.status_code} cat={cat_id} page={page}")
                break
            posts = r.json()
            if not posts:
                break
            for p in posts:
                if str(p["id"]) in done_ids:
                    continue
                out.append({
                    "id": p["id"],
                    "date": p.get("date", ""),
                    "link": p.get("link", ""),
                    "title": (p.get("title", {}) or {}).get("rendered", "") if isinstance(p.get("title"), dict) else p.get("title", ""),
                    "content": _strip_html((p.get("content", {}) or {}).get("rendered", "") if isinstance(p.get("content"), dict) else str(p.get("content", ""))),
                    "categories": p.get("categories", []),
                })
            total_pages = int(r.headers.get("X-WP-TotalPages", "1") or 1)
            if page >= total_pages:
                break
            page += 1
        except requests.RequestException as e:
            print(f"  [!] cat={cat_id} page={page}: {e}")
            time.sleep(2)
            break
    return out


def ingest_season(outdir: Path) -> Dict[str, int]:
    limiter = RateLimiter(0.4)
    counts: Dict[str, int] = {}
    for team, (cat_id, abbr) in sorted(TEAM_CATEGORIES.items()):
        outfile = outdir / f"{team}.jsonl"
        done: Set[str] = set()
        if outfile.exists():
            for line in outfile.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    done.add(str(json.loads(line)["id"]))
                except Exception:
                    pass
        posts = fetch_posts(cat_id, SEASON_START, SEASON_END, limiter, done)
        if posts:
            with outfile.open("a", encoding="utf-8") as f:
                for p in posts:
                    f.write(json.dumps(p, ensure_ascii=False) + "\n")
        counts[team] = len(done) + len(posts)
        print(f"  {team}: +{len(posts)} (total {counts[team]})")
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest Hockey Writers 2025-26 team articles")
    parser.add_argument("--out", default="data/raw/hw_articles")
    args = parser.parse_args()
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    counts = ingest_season(outdir)
    print(f"\n[+] total articles: {sum(counts.values())} across {len(counts)} teams -> {outdir}")


if __name__ == "__main__":
    main()
