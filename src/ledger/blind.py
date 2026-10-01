"""Blind team identity in article text before the ledger scorer sees it.

SPEC-qualitative-ledger.md, step 2. The scorer (Claude) may know how a past
season ended, so team names, cities, nicknames and abbreviations are replaced
deterministically: the article's subject team becomes TEAM_A and every other
team gets TEAM_B, TEAM_C, ... (labels carry no meaning beyond "not TEAM_A"). Player names, coaches
and arenas are left in place, so the blinding is partial by design (the SPEC
labels historical results "may be contaminated" for this reason).
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass, field

from src.features.teams import ABBREV_ALIAS, NICK_TO_ABBREV

EXTRA_NAMES: dict[str, str] = {
    "Utah Hockey Club": "UTA", "Utah Mammoth": "UTA", "Habs": "MTL", "Leafs": "TOR",
    "Bolts": "TBL", "Caps": "WSH", "Pens": "PIT", "Isles": "NYI", "Nucks": "VAN",
    "Sens": "OTT", "Canes": "CAR", "Avs": "COL", "Preds": "NSH", "Hawks": "CHI",
    "Knights": "VGK", "Coyotes": "ARI",
}
ABBREVS = sorted({*NICK_TO_ABBREV.values(), *ABBREV_ALIAS.values(), "UTA"})


def _patterns() -> list[tuple[re.Pattern[str], str]]:
    names: dict[str, str] = {**NICK_TO_ABBREV, **EXTRA_NAMES}
    for a in ABBREVS:
        names.setdefault(a, a)
    for alias, a in ABBREV_ALIAS.items():
        names.setdefault(alias, a)
    # Longest first, so "Toronto Maple Leafs" wins over "Toronto" and "Leafs".
    ordered = sorted(names.items(), key=lambda kv: len(kv[0]), reverse=True)
    # Case-sensitive on purpose: "Wild", "Stars", "Kings" as teams are capitalised.
    # Upper-case variants too: magazine layouts print "ANAHEIM DUCKS".
    variants = [(n, a) for n, a in ordered if len(n) >= 2]
    variants += [(n.upper(), a) for n, a in ordered if len(n) >= 4 and n.upper() != n]
    variants.sort(key=lambda kv: len(kv[0]), reverse=True)
    return [(re.compile(rf"(?<![\w'])(?:[Tt]he\s+|THE\s+)?{re.escape(n)}(?:'s)?(?![\w])"), a)
            for n, a in variants]


WILD_CARD = re.compile(r"\b[Ww]ild[\s-]+[Cc]ard")
HANDLES = re.compile(r"@\w+")
REPEATS = re.compile(r"\b(TEAM_[A-Z])(?:\s+\1\b)+")


PATTERNS = _patterns()


@dataclass
class Blinder:
    subject: str
    mapping: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.mapping[self.subject] = "TEAM_A"

    def _label(self, abbrev: str) -> str:
        if abbrev not in self.mapping:
            self.mapping[abbrev] = f"TEAM_{string.ascii_uppercase[len(self.mapping)]}"
        return self.mapping[abbrev]

    def blind(self, text: str) -> str:
        # Phrases that contain a team word but aren't the team (pilot, 2026-09-24).
        text = WILD_CARD.sub(lambda m: m.group(0).replace("ild", "ILDCARD_"), text)
        text = HANDLES.sub("@HANDLE", text)  # "@NJDevils" names the team outright
        for pattern, abbrev in PATTERNS:
            text = pattern.sub(lambda m, a=abbrev: self._label(a), text)
        text = text.replace("ILDCARD_", "ild")
        return REPEATS.sub(r"\1", text)  # "Chicago Blackhawks" -> one label, not two


def blind_article(subject: str, title: str, body: str) -> tuple[str, str, dict[str, str]]:
    """Return (blinded title, blinded body, abbrev -> label mapping)."""
    b = Blinder(subject)
    return b.blind(title), b.blind(body), dict(b.mapping)
