"""7-dimension LLM head-to-head scorer for Hobby Hero.

For each game, assembles each team's pre-game Hockey Writers article context
(point-in-time, no leakage) and asks DeepSeek to score the matchup across 7
qualitative dimensions:

    offense, defense, physical, coaching, x_factor, goaltending, special_teams

Each dimension scored -1.0..+1.0 for home and away (0 = neutral/unknown). The
result is a head-to-head summary useful BOTH as grounding/narrative content and
as potential predictive features (each dimension produces home/away/delta).

API key is read from a `.env` file at the repo root (DEEPSEEK_API_KEY) or the
process environment. Never hardcode a key.

Dry-run mode (--dry-run) emits the prompt and a stubbed result so the plumbing
can be tested without spending tokens.
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

import requests

from src.features import hw_corpus as hwc

DIMENSIONS = (
    "offense", "defense", "physical", "coaching",
    "x_factor", "goaltending", "special_teams",
)

MODEL = "deepseek-v4-flash"
DEFAULT_API_BASE = "https://api.deepseek.com/v1"
MAX_ARTICLES = 5
MAX_AGE_DAYS = 14
MAX_CHARS_PER_ARTICLE = 600


def load_api_key(root: Path) -> str:
    """Read DEEPSEEK_API_KEY from env or a .env file at root."""
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if key:
        return key
    env_path = root / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("DEEPSEEK_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError(
        "DEEPSEEK_API_KEY not found. Set it in the environment or in hobbyhero/.env"
    )


def _team_context(corpus: hwc.HWCorpus, abbr: str, game_date: date) -> str:
    arts = corpus.articles_before(abbr, game_date, MAX_AGE_DAYS, MAX_ARTICLES)
    blocks = []
    for a in arts:
        title = a.get("title", "")
        content = (a.get("content", "") or "")[:MAX_CHARS_PER_ARTICLE]
        blocks.append(f"[{a.get('date', '')[:10]}] {title}\n{content}")
    return "\n\n".join(blocks) if blocks else "(no articles in window)"


def build_prompt(home: str, away: str, game_date: date,
                 home_context: str, away_context: str) -> str:
    dims = ", ".join(DIMENSIONS)
    return (
        "You are an NHL scouting analyst. Score this game head-to-head across "
        f"these 7 dimensions: {dims}.\n"
        "For EACH dimension, score the HOME team and AWAY team on -1.0..+1.0 "
        "(0 = neutral/unknown; do not invent — only score what the articles "
        "support). Give a 1-sentence justification per team.\n\n"
        f"Game: {home} (home) vs {away} (away), {game_date}\n\n"
        f"HOME ({home}) pre-game articles:\n{home_context}\n\n"
        f"AWAY ({away}) pre-game articles:\n{away_context}\n\n"
        'Reply with ONLY a JSON object of the form:\n'
        '{"home_score": {"offense": 0.2, "defense": -0.1, "physical": 0.0, '
        '"coaching": 0.3, "x_factor": 0.0, "goaltending": 0.4, '
        '"special_teams": -0.2}, "away_score": {...same 7 keys...}, '
        '"home_reason": "...", "away_reason": "..."}'
    )


def call_deepseek(prompt: str, api_key: str, api_base: str = DEFAULT_API_BASE) -> str:
    resp = requests.post(
        f"{api_base}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": MODEL,
            "messages": [
                {"role": "system",
                 "content": "You return only valid JSON. No prose around it."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 400,
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def parse_score(raw: str) -> dict:
    """Extract the JSON object from the model reply (tolerates stray fences)."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    obj = json.loads(text)
    return obj


def score_game(corpus: hwc.HWCorpus, home: str, away: str, game_date: date,
               api_key: str, dry_run: bool = False) -> dict:
    home_ctx = _team_context(corpus, home, game_date)
    away_ctx = _team_context(corpus, away, game_date)
    prompt = build_prompt(home, away, game_date, home_ctx, away_ctx)
    if dry_run:
        return {
            "game_date": str(game_date), "home": home, "away": away,
            "prompt": prompt, "dry_run": True,
            "home_score": {d: 0.0 for d in DIMENSIONS},
            "away_score": {d: 0.0 for d in DIMENSIONS},
            "home_reason": "(dry run)", "away_reason": "(dry run)",
        }
    raw = call_deepseek(prompt, api_key)
    result = parse_score(raw)
    result.setdefault("game_date", str(game_date))
    result.setdefault("home", home)
    result.setdefault("away", away)
    result.setdefault("home_score", {})
    result.setdefault("away_score", {})
    result.setdefault("home_reason", "")
    result.setdefault("away_reason", "")
    return result


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", required=True)
    parser.add_argument("--away", required=True)
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    root = Path(args.root)
    corpus = hwc.HWCorpus(root / "data" / "raw" / "hw_articles")
    api_key = "" if args.dry_run else load_api_key(root)
    result = score_game(corpus, args.home, args.away, date.fromisoformat(args.date),
                        api_key, dry_run=args.dry_run)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
