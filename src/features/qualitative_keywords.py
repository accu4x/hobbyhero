"""Deterministic keyword-based qualitative scorer for the Stage-0 pilot.

Transparent, reproducible proxy for the LLM scoring rubric. Detects whether
each dimension is "supported" by the article text and assigns a directional
score. This is a STAGE-0 feasibility probe: if these cheap heuristic scores
show no incremental predictive signal, the (much more expensive) LLM scorer
very likely won't either, given the same corpus. Conversely it also reveals
how thin the corpus's real team-state signal is.

Each dimension returns a score in -1.0..+1.0 and a "supported" flag (False =
article had no usable info -> 0 / neutral).
"""

from __future__ import annotations

import re

# Dimension -> (positive evidence, negative evidence)
RULES = {
    "injury_lineup": (
        ["healthy", "back in the lineup", "return from injury", "reinforcement", "full lineup"],
        ["injur", "out of the lineup", "sidelined", "day-to-day", "missing key", "dented", "banged up", "short-handed"],
    ),
    "rest_travel": (
        ["well-rested", "rest advantage", "home stand", "fresh", "two days rest"],
        ["back-to-back", "second half of back-to-back", "long road trip", "travel", "fatigue", "worn down", "exhaust"],
    ),
    "momentum": (
        ["winning streak", "on a roll", "surg", "hot", "winners of", "streak", "momentum", "confidence high"],
        ["losing streak", "slump", "skid", "cold", "struggl", "descent", "reeling", "lost confidence"],
    ),
    "motivation": (
        ["playoff push", "must-win", "desperate", "revenge", "rivalry", "race", "clinched spot", "stakes", "fighting for"],
        ["out of playoff", "eliminated", "nothing to play for", "tank", "tanking", "no stakes", "already clinched", "coast"],
    ),
}


def score_article(text: str) -> dict:
    """Return {dim: {'score': float, 'supported': bool}} for one article."""
    t = text.lower()
    out = {}
    for dim, (pos, neg) in RULES.items():
        p = sum(1 for k in pos if k in t)
        n = sum(1 for k in neg if k in t)
        if p == 0 and n == 0:
            out[dim] = {"score": 0.0, "supported": False}
        else:
            # clip to [-1,1]
            val = (p - n) / max(1, (p + n))
            out[dim] = {"score": round(val, 3), "supported": True}
    return out


# a callable matching the QualitativeScorer scorer signature:
# scorer(text, team_abbrev, article_date) -> dict of dim->float
def scorer(text: str, team: str = "", date: str = "") -> dict:
    return {k: v["score"] for k, v in score_article(text).items()}


def supported(text: str) -> dict:
    """Return {dim: bool} whether the article supports the dimension."""
    return {k: v["supported"] for k, v in score_article(text).items()}
