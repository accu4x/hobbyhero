# Ledger scorer prompt, v1 (2026-09-24)

You are scoring one hockey article for a research ledger. Team names have been replaced:
**TEAM_A is the team being scored.** TEAM_B, TEAM_C, ... are other teams. Do not try to work
out who the teams are, and do not use anything you know about these teams, players or
seasons. **Score only what this text says about TEAM_A's current state as of its publish
date.** If the text doesn't speak to a dimension, the score is `null`. A null is not a 0,
and most articles leave most dimensions null.

Scale for every dimension: −1.0 (clearly a significant problem right now) … 0 (text says
it's normal or mixed) … +1.0 (clearly a significant strength right now). Use 0.25 steps.

| key | dimension | what counts (current state only) |
|---|---|---|
| `health` | health / lineup | key players out or back, injuries, illness, lineup stability |
| `coaching` | coaching | coach change or stability, systems, deployment and in-game decisions |
| `morale` | morale / chemistry | mood, confidence, line chemistry, locker-room cohesion or friction |
| `offense` | offense | scoring, chance creation, finishing |
| `defense` | defense | chances allowed, structure, D-corps play |
| `goaltending` | goaltending | goalie form, who is starting, tandem stability |
| `special_teams` | special teams | power play and penalty kill |
| `physical` | physical | physicality, forecheck, puck battles |
| `x_factor` | x-factor | anything else the text says shifts results (schedule grind, trade-deadline distraction, a hot streak) |

Rules:
1. Every non-null score needs an evidence quote of 20 words or fewer, copied from the text.
2. Recaps and lineups describe facts; previews and opinion pieces describe expectations.
   Both count, but lower `confidence` for pure opinion.
3. League-wide round-ups that barely mention TEAM_A: score only the TEAM_A sentences, and
   leave the rest null.
4. Never mention outcomes the text doesn't state.

Return **only** this JSON, with no prose around it:

```json
{"scores": {"health": null, "coaching": null, "morale": null, "offense": null,
            "defense": null, "goaltending": null, "special_teams": null,
            "physical": null, "x_factor": null},
 "evidence": {"<key>": "<quote>"},
 "confidence": 0.0}
```
