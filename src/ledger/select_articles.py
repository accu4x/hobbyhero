"""Build the ledger work queue: blinded, filtered article packets per team-week.

SPEC-qualitative-ledger.md, step 2. For each (team, week) in the regular
season it keeps the K most relevant articles about the team's current state,
truncates each body, blinds team identity, and writes one packet per line to
data/ledger/queue_<season>.jsonl. The scorer (Claude in Cowork) reads packets
from there; nothing here calls a model.

    python -m src.ledger.select_articles --season 20252026 [--per-week 3]
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import os
import re
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

from src.features.teams import NICK_TO_ABBREV, team_to_abbrev
from src.ledger.blind import blind_article

ROOT = Path(__file__).resolve().parents[2]
HW_DIR = ROOT / "data" / "raw" / "hw_articles"
# Outside the repo; config from env (python-conventions.md), default = its Windows home.
THN_DIR = Path(os.environ.get("HOBBYHERO_THN_DIR",
                            r"C:\Users\hn2_f\source\python\TheHockeyNews_Articles"))
OUT_DIR = ROOT / "data" / "ledger"
# THN team reports are long, dense and (Dan, 2026-09-24) "very good content": keep more.
BODY_CHARS = {"hockeywriters": 2000, "thehockeynews": 6000}
# (first article date kept, Monday of last regular-season week). Pre-season articles
# (training camp, THN season previews) feed the first weeks through the rollup window.
SEASONS = {20252026: (date(2025, 8, 1), date(2026, 4, 13))}
# The Arizona Coyotes relocated to Utah for 2024-25; sources still use Arizona slugs.
UTAH_FROM = date(2024, 7, 1)

INCLUDE = ("lineup", "line", "injur", "preview", "takeaway", "recap", "game", "coach",
           "goalie", "trade", "streak", "slump", "power play", "penalty kill", "chemistry",
           "morale", "locker", "struggl", "surg", "return", "report", "grade", "rank",
           "defeat", "beat", "win", "loss", "lose")
EXCLUDE = ("history", "all-time", "draft", "prospect", "years ago", "throwback",
           "retro", "junior", "chl", "ohl", "whl", "qmjhl", "ncaa", "ahl", "memories",
           "free agency", "rumour", "rumor", "mock",
           # League-wide round-ups filed under one team's category: mostly other teams.
           "morning recap")

log = logging.getLogger("select_articles")


@dataclass(frozen=True)
class Packet:
    article_id: str
    source: str
    team: str
    week: str  # Monday (ISO) of the week the article was published in
    published_at: str
    title: str  # blinded
    text: str  # blinded, truncated
    relevance: int


VALID = set(NICK_TO_ABBREV.values())


def _abbrev(name: str) -> str:
    """Folder name ("Toronto Maple Leafs") -> "TOR"; '' when unrecognised."""
    words = name.replace("_", " ").replace("-", " ").replace(".", "").title().split()
    for cand in (" ".join(words), " ".join(words[-2:]), words[-1], words[0]):
        a = team_to_abbrev(cand)
        if a in VALID:
            return a
    return ""


def _team(abbrev: str, published: date) -> str:
    return "UTA" if abbrev == "ARI" and published >= UTAH_FROM else abbrev


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _relevance(title: str) -> int:
    t = title.lower()
    if any(x in t for x in EXCLUDE):
        return -1
    return sum(1 for x in INCLUDE if x in t)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def hw_articles() -> Iterator[tuple[str, str, str, date, str, str]]:
    for path in sorted(HW_DIR.glob("*.jsonl")):
        abbrev = _abbrev(path.stem)
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                a = json.loads(line)
                yield (f"hw:{a['id']}", "hockeywriters", abbrev,
                       date.fromisoformat(a["date"][:10]), _clean(a.get("title", "")),
                       _clean(a.get("content", "")))


def thn_articles() -> Iterator[tuple[str, str, str, date, str, str]]:
    if not THN_DIR.exists():
        log.warning("THN archive not found at %s, skipping", THN_DIR)
        return
    for page in sorted(THN_DIR.glob("*/*/article.html")):
        team_dir, issue_dir = page.parts[-3], page.parts[-2]
        abbrev = _abbrev(team_dir)
        raw = page.read_text(encoding="utf-8", errors="ignore")
        raw = re.sub(r"<script.*?</script>|<style.*?</style>", " ", raw, flags=re.S)
        text = _clean(re.sub(r"<[^>]+>", " ", raw))
        # Drop the site chrome before the byline: the report starts at " BY ".
        start = text.find(" BY ")
        text = text[start + 1:] if start > 0 else text
        yield (f"thn:{issue_dir}", "thehockeynews", abbrev,
               date.fromisoformat(issue_dir[:10]), f"THN team report {issue_dir[:10]}", text)


def build(season: int, per_week: int) -> list[Packet]:
    first, last = SEASONS[season]
    buckets: dict[tuple[str, date], list[Packet]] = defaultdict(list)
    for gen in (hw_articles(), thn_articles()):
        for aid, source, team, published, title, body in gen:
            team = _team(team, published)
            week = _monday(published)
            if not team or published < first or week > last:
                continue
            rel = 5 if source == "thehockeynews" else _relevance(title)
            if rel < 1:
                continue
            t, b, _ = blind_article(team, title, body[:BODY_CHARS[source]])
            buckets[(team, week)].append(Packet(aid, source, team, week.isoformat(),
                                                published.isoformat(), t, b, rel))
    packets: list[Packet] = []
    for key in sorted(buckets):
        ranked = sorted(buckets[key], key=lambda p: (p.relevance, p.published_at),
                        reverse=True)
        # Every THN report is kept (dense, monthly); the per-week cap applies to the blog.
        packets.extend(p for p in ranked if p.source == "thehockeynews")
        # Variety: at most one article per title stem ("Projected Lineups for ...").
        seen: set[str] = set()
        for p in ranked:
            if p.source == "thehockeynews":
                continue
            stem = " ".join(p.title.split()[:2]).lower()
            if stem in seen:
                continue
            seen.add(stem)
            packets.append(p)
            if len(seen) == per_week:
                break
    return packets


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--season", type=int, default=20252026)
    parser.add_argument("--per-week", type=int, default=3)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    packets = build(args.season, args.per_week)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"queue_{args.season}.jsonl"
    with out.open("w", encoding="utf-8") as fh:
        for p in packets:
            fh.write(json.dumps(asdict(p), ensure_ascii=False) + "\n")
    teams = {p.team for p in packets}
    weeks = {p.week for p in packets}
    chars = sum(len(p.text) for p in packets)
    log.info("%d packets -> %s | %d teams, %d weeks | ~%.2fM input tokens", len(packets),
             out, len(teams), len(weeks), chars / 4e6)


if __name__ == "__main__":
    main()
