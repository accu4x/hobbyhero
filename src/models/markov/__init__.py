"""Semi-Markov event simulator (SPEC-markov-simulator.md, 2026-09-26).

- `transitions`: Stage A (next event, actor, zone) and Stage B (block, xG bin, result)
  tables, fitted with hierarchical backoff; penalty categories.
- `timing`: dwell times, the penalty clock (the rules in spec section 6) and the
  goalie-pull model.
- `simulate`: the game loop and per-game summaries.

Gate A (2026-09-26) is league-only: no team or goalie effects. `goalies.py` from the
design doc arrives with Gate B.
"""
