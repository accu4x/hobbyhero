"""LLM team-strength scorer for Hobby Hero.

Reads each team's pre-game Hockey Writers articles (assembled point-in-time
by hw_corpus.HWCorpus) and produces a team-strength score in -1.0..+1.0 plus
a short justification. The score captures qualitative betting-relevant state
that boxscore stats miss: injuries/lineup, form/momentum, motivation/stakes,
goaltending/roster quality, system/fatigue.

Scoring rubric (score each dimension -1..+1, 0 = neutral/unknown):
  -1.0          -0.5           0.0          +0.5          +1.0
  badly broken  below-average  neutral     above-average  elite
  e.g. star out, key injuries, slump, low stakes; vs. full/healthy lineup,
  hot streak, must-win stakes, strong goaltending.

The LLM (the scorer) reads the assembled context and returns a JSON record:
  {"home_team": "MTL", "away_team": "TOR", "game_date": "2026-01-15",
   "home_score": 0.4, "away_score": -0.2,
   "home_reason": "...", "away_reason": "...", "confidence": 0.8}

A positive home_score - away_score => qualitative edge favors the home team.

This module provides the harness: assembling context per game and combining
the LLM score with the boxscore model + market into an adjusted probability.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from src.features import hw_corpus as hwc


def assemble_context(corpus: hwc.HWCorpus, home_abbr: str, away_abbr: str,
                     game_date, max_age_days: int = 14, max_articles: int = 5,
                     max_chars: int = 600) -> Dict:
    """Build the LLM prompt context for a game from pre-game articles."""
    def team_ctx(abbr):
        arts = corpus.articles_before(abbr, game_date, max_age_days, max_articles)
        blocks = []
        for a in arts:
            title = a.get("title", "")
            content = (a.get("content", "") or "")
            blocks.append(f"[{a.get('date','')[:10]}] {title}\n{content[:max_chars]}")
        return "\n\n".join(blocks) if blocks else "(no articles in window)"

    return {
        "game_date": str(game_date),
        "home_team": home_abbr,
        "away_team": away_abbr,
        "home_context": team_ctx(home_abbr),
        "away_context": team_ctx(away_abbr),
    }


# --- combining LLM score with model + market ---

def adjust_probability(model_prob: float, market_prob: float,
                       home_score: float, away_score: float,
                       blend: float = 0.5) -> float:
    """Combine model + market with an LLM qualitative adjustment.

    The LLM delta (home_score - away_score) in [-2,2] is mapped to a
    probability shift. We blend toward the LLM view:
        qual_delta_prob = 0.5 + 0.25 * (home_score - away_score)   # in (0,1)
        adjusted = (1-blend)*market_prob + blend*qual_delta_prob
    `blend` controls how much weight the qualitative signal gets.
    """
    import numpy as np
    qual_prob = 0.5 + 0.25 * (home_score - away_score)
    qual_prob = float(np.clip(qual_prob, 0.02, 0.98))
    return (1 - blend) * market_prob + blend * qual_prob


def edge_of(adjusted_prob: float, market_prob: float) -> float:
    return adjusted_prob - market_prob
