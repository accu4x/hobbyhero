"""Validate a scorer result and append it to the ledger (SPEC step 2).

The scorer (Claude in Cowork) produces JSON per prompt_v1.md for one queue
packet. This module checks it and appends one row to
data/ledger/articles_<season>.jsonl. Rows are append-only; a re-score of the
same (article_id, team, prompt_version) is refused.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEDGER_DIR = ROOT / "data" / "ledger"
PROMPT = Path(__file__).with_name("prompt_v1.md")
DIMENSIONS = ("health", "coaching", "morale", "offense", "defense", "goaltending",
              "special_teams", "physical", "x_factor")
FACTS = ("health", "coaching", "goaltending")
FEELINGS = ("morale", "x_factor")


class LedgerError(ValueError):
    """A scorer result that does not meet the ledger schema."""


def prompt_version() -> str:
    digest = hashlib.sha256(PROMPT.read_bytes()).hexdigest()[:10]
    return f"v1-{digest}"


def validate(result: dict) -> dict:
    scores = result.get("scores")
    if not isinstance(scores, dict) or set(scores) != set(DIMENSIONS):
        raise LedgerError(f"scores must have exactly {DIMENSIONS}")
    evidence = result.get("evidence") or {}
    for dim, v in scores.items():
        if v is None:
            continue
        if not isinstance(v, (int, float)) or not -1.0 <= v <= 1.0:
            raise LedgerError(f"{dim}={v!r} outside [-1, 1]")
        quote = evidence.get(dim, "")
        if not quote or len(quote.split()) > 20:
            raise LedgerError(f"{dim} needs an evidence quote of 1-20 words")
    conf = result.get("confidence")
    if not isinstance(conf, (int, float)) or not 0.0 <= conf <= 1.0:
        raise LedgerError("confidence must be in [0, 1]")
    return {"scores": scores, "evidence": {k: evidence[k] for k in evidence
                                           if scores.get(k) is not None},
            "confidence": float(conf)}


def _norm(text: str) -> str:
    return " ".join(text.split())


def check_quotes(packet: dict, clean: dict) -> None:
    """Every evidence quote must be copied from the packet (no invented evidence)."""
    haystack = _norm(packet["title"] + " " + packet["text"])
    for dim, quote in clean["evidence"].items():
        if _norm(quote) not in haystack:
            raise LedgerError(f"{dim} quote not found in the article: {quote!r}")


def append(season: int, packet: dict, result: dict, scorer: str) -> dict:
    clean = validate(result)
    check_quotes(packet, clean)
    path = LEDGER_DIR / f"articles_{season}.jsonl"
    version = prompt_version()
    key = (packet["article_id"], packet["team"], version)
    if path.exists():
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                r = json.loads(line)
                if (r["article_id"], r["team"], r["prompt_version"]) == key:
                    raise LedgerError(f"already scored: {key}")
    row = {"article_id": packet["article_id"], "source": packet["source"],
           "team": packet["team"], "week": packet["week"],
           "published_at": packet["published_at"], "blinded": True, **clean,
           "scorer": scorer, "prompt_version": version,
           "scored_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row
