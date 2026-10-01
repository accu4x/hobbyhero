"""Leak check for the public Hobby Hero repo. Fails closed; run it before every push.

    python tests/leak_check.py              every file Git would publish
    python tests/leak_check.py --dir <d>    a build output, such as artifact/dist/site

Modelled on kestrel-nine/artifact/test/leak.test.cjs, with the same matching rules. The private
phrases live outside the repo in ../private/*-denylist.txt and never enter it. Findings name
the file and line only and never print the matched text, so a report can be pasted anywhere.

Not a pytest module (the name doesn't match test_*.py or *_test.py): it is a gate, run on purpose.
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PRIVATE = REPO.parent / "private"
LISTS = ["hobbyhero-denylist.txt", "employer-denylist.txt"]
REQUIRED = LISTS[0]
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".avif",
                 ".woff", ".woff2", ".ttf", ".zip", ".pdf"}
MAX_BYTES = 2_000_000  # a data dump is the likeliest accident; showcase/preview.html is ~1.6 MB

# Credentials of the kinds this workspace and the old hobbyhero-app touch.
SECRETS = [re.compile(p) for p in (
    r"gh[pousr]_[A-Za-z0-9]{30,}",
    r"github_pat_[A-Za-z0-9_]{40,}",
    r"sk-ant-[A-Za-z0-9_-]{20,}",
    r"sk-[A-Za-z0-9]{32,}",                      # DeepSeek and OpenAI-style keys
    r"AKIA[0-9A-Z]{16}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"xox[abprs]-[A-Za-z0-9-]{10,}",
    r"shp(?:at|ss|ca|pa)_[a-fA-F0-9]{32}",       # Shopify (v3 credits)
    r"AccountKey=[A-Za-z0-9+/=]{40,}",           # Azure storage (v3 hosting)
    r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.",  # a signed JWT
    r"(?i)(?:CLOUDFLARE|CF)_API_TOKEN\s*[=:]\s*['\"]?[A-Za-z0-9_-]{30,}",
)]

# Paths that must never be published, whatever .gitignore says (SPEC-repo-and-site.md §2).
FORBIDDEN = [re.compile(p) for p in (
    r"^data/", r"^archive/", r"^reports/", r"^logs/",
    r"(^|/)\.env$",
    r"(^|/)(OPEN-ITEMS|BACKLOG|PROJECT-CATALOG|markov-simulator-design)\.md$",
    r"(^|/)HANDOVER[^/]*\.md$",
    r"(^|/)config\.json$",
)]


def to_matcher(term: str) -> re.Pattern[str]:
    """A single word is case-sensitive whole-word; a phrase is case-insensitive, any spacing."""
    body = r"\s+".join(re.escape(part) for part in term.split())
    flags = re.IGNORECASE if " " in term.strip() else 0
    return re.compile(rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9])", flags)


def load_denylist() -> list[re.Pattern[str]]:
    patterns: list[re.Pattern[str]] = []
    for name in LISTS:
        path = PRIVATE / name
        if not path.exists():
            if name == REQUIRED:
                sys.exit(f"leak: ../private/{name} is missing, so nothing is vouched for. Failing closed.")
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                patterns.append(to_matcher(line))
    return patterns


def walk(root: Path) -> Iterator[Path]:
    yield from (p for p in root.rglob("*") if p.is_file())


def ignore_rules() -> list[tuple[str, bool, bool]]:
    """(pattern, anchored, dir_only) from .gitignore; enough for the rules this repo uses."""
    rules = []
    for line in (REPO / ".gitignore").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("!"):
            sys.exit("leak: .gitignore uses '!' negation, which the fallback can't read. Run inside the git repo.")
        rules.append((line.strip("/"), line.startswith("/"), line.endswith("/")))
    return rules


def ignored(rel: str, rules: list[tuple[str, bool, bool]]) -> bool:
    parts = rel.split("/")
    for pattern, anchored, dir_only in rules:
        if "/" in pattern or anchored:
            depth = pattern.count("/") + 1
            prefix = "/".join(parts[:depth])
            if fnmatch.fnmatch(prefix, pattern) and (not dir_only or len(parts) > depth):
                return True
            continue
        candidates = parts[:-1] if dir_only else parts
        if any(fnmatch.fnmatch(part, pattern) for part in candidates):
            return True
    return False


def publishable() -> tuple[list[Path], str]:
    """What `git add -A` would publish. Uses git when this is a repo, else reads .gitignore."""
    if (REPO / ".git").exists():
        out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                             cwd=REPO, capture_output=True, text=True, check=True).stdout
        return [REPO / f for f in out.splitlines() if f], "git"
    rules = ignore_rules()
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(REPO):
        base = Path(dirpath)
        # Prune ignored folders before descending: data/ alone is far too big to walk.
        dirnames[:] = [d for d in dirnames
                       if not ignored((base / d / "x").relative_to(REPO).as_posix(), rules)]
        files += [base / f for f in filenames
                  if not ignored((base / f).relative_to(REPO).as_posix(), rules)]
    return files, "fallback (.gitignore read directly; no .git yet)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", type=Path, help="scan a build directory instead of the repo")
    args = parser.parse_args()

    denylist = load_denylist()
    if args.dir:
        root = args.dir.resolve()
        files, mode = list(walk(root)), f"dir {args.dir}"
    else:
        root = REPO
        files, mode = publishable()

    bad = unreadable = scanned = 0
    for path in files:
        rel = path.relative_to(root).as_posix()
        if not args.dir and any(f.search(rel) for f in FORBIDDEN):
            print(f"FAIL forbidden path: {rel}", file=sys.stderr)
            bad += 1
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        try:
            if path.stat().st_size > MAX_BYTES:
                print(f"FAIL too large to publish unreviewed: {rel}", file=sys.stderr)
                bad += 1
                continue
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            continue  # deleted in the working tree: nothing to publish
        except (OSError, UnicodeDecodeError):
            print(f"UNREADABLE {rel}", file=sys.stderr)
            unreadable += 1
            continue
        scanned += 1
        lines = text.splitlines()
        for number, line in enumerate(lines, 1):
            for kind, patterns in (("private phrase", denylist), ("secret", SECRETS)):
                if any(p.search(line) for p in patterns):
                    print(f"FAIL {kind}: {rel}:{number}", file=sys.stderr)
                    bad += 1
        # Phrases wrapped across lines: check the whole file too, reporting the file only.
        if any(p.search(text) for p in denylist) and not any(
                p.search(line) for line in lines for p in denylist):
            print(f"FAIL private phrase (across lines): {rel}", file=sys.stderr)
            bad += 1

    if bad or unreadable:
        print(f"leak: {bad} finding(s), {unreadable} unreadable. Matched text is never printed; "
              "open the line.", file=sys.stderr)
        return 1
    print(f"leak: clean. {scanned} files via {mode}, {len(denylist)} private phrases, "
          f"{len(SECRETS)} secret patterns.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
