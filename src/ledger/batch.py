"""Batch helper for scoring the ledger queue from a Cowork session (SPEC step 3).

    python3 -m src.ledger.batch status --season 20252026
    python3 -m src.ledger.batch start  --season 20252026            -> prints {"run": "<id>"}
    python3 -m src.ledger.batch next   --season 20252026 --n 25 --run <id>
    python3 -m src.ledger.batch commit --season 20252026 --scorer "<model> (Cowork nightly)" --run <id>
    python3 -m src.ledger.batch finish --run <id>

Only one run at a time (added 2026-09-24, after two runs overlapped): `start` takes the lock in
data/ledger/_run.lock, or prints {"busy": ...} and exits with code 3 while another run holds it.
`next` and `commit` refuse a run id that doesn't hold the lock. A lock untouched for 40 minutes
counts as abandoned. `finish` releases it by marking it released (nothing is deleted).

`next` prints the next N unscored packets (for the current prompt version) as
JSON lines. The scorer writes one line per packet to data/ledger/_incoming.jsonl:
    {"article_id": ..., "team": ..., "result": {<prompt_v1 JSON>}}
`commit` validates each line (schema, range, quotes copied from the article),
appends good rows to the ledger, writes bad ones to data/ledger/_rejected.jsonl
with the reason, and moves the incoming file to data/ledger/incoming_done/.
Nothing is ever deleted.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.ledger.record import LEDGER_DIR, LedgerError, append, prompt_version

INCOMING = LEDGER_DIR / "_incoming.jsonl"
LOCK = LEDGER_DIR / "_run.lock"
STALE_SECONDS = 40 * 60


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _read_lock() -> dict | None:
    if not LOCK.exists():
        return None
    try:
        lock = json.loads(LOCK.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if lock.get("released"):
        return None
    age = (_now() - datetime.fromisoformat(lock["touched"])).total_seconds()
    return None if age > STALE_SECONDS else lock


def _write_lock(run: str, started: str, released: bool = False) -> None:
    LOCK.write_text(json.dumps({"run": run, "started": started, "touched": _now().isoformat(),
                                "released": released}), encoding="utf-8")


def start() -> None:
    held = _read_lock()
    if held:
        print(json.dumps({"busy": held["run"], "since": held["started"]}))
        sys.exit(3)
    run = _now().strftime("%Y%m%dT%H%M%SZ")
    _write_lock(run, _now().isoformat())
    print(json.dumps({"run": run}))


def _check(run: str | None) -> None:
    held = _read_lock()
    if run is None and not held:
        return  # legacy call with no lock in play (runs started before the lock existed)
    if not held or held["run"] != run:
        print(json.dumps({"error": "this run does not hold the lock; run `start` first",
                          "held_by": held["run"] if held else None}))
        sys.exit(3)
    _write_lock(run, held["started"])


def finish(run: str | None) -> None:
    held = _read_lock()
    if held and held["run"] == run:
        _write_lock(run, held["started"], released=True)
    print(json.dumps({"released": bool(held and held["run"] == run)}))


def _queue(season: int) -> list[dict]:
    path = LEDGER_DIR / f"queue_{season}.jsonl"
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _done(season: int) -> set[tuple[str, str]]:
    path = LEDGER_DIR / f"articles_{season}.jsonl"
    version = prompt_version()
    if not path.exists():
        return set()
    with path.open(encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    return {(r["article_id"], r["team"]) for r in rows if r["prompt_version"] == version}


def status(season: int) -> None:
    q, done = _queue(season), _done(season)
    left = [p for p in q if (p["article_id"], p["team"]) not in done]
    print(json.dumps({"season": season, "prompt_version": prompt_version(),
                      "queue": len(q), "scored": len(q) - len(left), "remaining": len(left)}))


def next_packets(season: int, n: int) -> None:
    done = _done(season)
    left = [p for p in _queue(season) if (p["article_id"], p["team"]) not in done]
    for p in left[:n]:
        print(json.dumps({k: p[k] for k in ("article_id", "team", "published_at", "title",
                                             "text")}, ensure_ascii=False))


def commit(season: int, scorer: str) -> None:
    if not INCOMING.exists():
        print("nothing to commit")
        return
    packets = {(p["article_id"], p["team"]): p for p in _queue(season)}
    ok = bad = 0
    rejected = LEDGER_DIR / "_rejected.jsonl"
    with INCOMING.open(encoding="utf-8") as fh:
        lines = [line for line in fh if line.strip()]
    for line in lines:
        try:
            item = json.loads(line)
            packet = packets[(item["article_id"], item["team"])]
            append(season, packet, item["result"], scorer)
            ok += 1
        except (LedgerError, KeyError, json.JSONDecodeError) as exc:
            bad += 1
            with rejected.open("a", encoding="utf-8") as out:
                out.write(json.dumps({"line": line.strip(), "error": str(exc)},
                                     ensure_ascii=False) + "\n")
    done_dir = LEDGER_DIR / "incoming_done"
    done_dir.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    INCOMING.replace(done_dir / f"incoming_{stamp}.jsonl")
    print(json.dumps({"committed": ok, "rejected": bad}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("cmd", choices=["status", "start", "next", "commit", "finish"])
    parser.add_argument("--season", type=int, default=20252026)
    parser.add_argument("--n", type=int, default=25)
    parser.add_argument("--scorer", default="unknown")
    parser.add_argument("--run", default=None, help="run id from `start`")
    args = parser.parse_args()
    if args.cmd == "status":
        status(args.season)
    elif args.cmd == "start":
        start()
    elif args.cmd == "finish":
        finish(args.run)
    elif args.cmd == "next":
        _check(args.run)
        next_packets(args.season, args.n)
    else:
        _check(args.run)
        commit(args.season, args.scorer)
    sys.stdout.flush()


if __name__ == "__main__":
    main()
