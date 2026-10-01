"""Team colour chips for the artifact (2026-09-24). Colours only, never logos.

(primary, secondary) hex, from each club's published palette. The chip foreground
is chosen for contrast, not taken from the club.
"""

from __future__ import annotations

TEAM_COLORS: dict[str, tuple[str, str]] = {
    "ANA": ("#F47A38", "#B9975B"), "BOS": ("#FFB81C", "#000000"),
    "BUF": ("#002654", "#FCB514"), "CGY": ("#C8102E", "#F1BE48"),
    "CAR": ("#CC0000", "#000000"), "CHI": ("#CF0A2C", "#000000"),
    "COL": ("#6F263D", "#236192"), "CBJ": ("#002654", "#CE1126"),
    "DAL": ("#006847", "#8F8F8C"), "DET": ("#CE1126", "#FFFFFF"),
    "EDM": ("#041E42", "#FF4C00"), "FLA": ("#041E42", "#C8102E"),
    "LAK": ("#111111", "#A2AAAD"), "MIN": ("#154734", "#A6192E"),
    "MTL": ("#AF1E2D", "#192168"), "NSH": ("#FFB81C", "#041E42"),
    "NJD": ("#CE1126", "#000000"), "NYI": ("#00539B", "#F47D30"),
    "NYR": ("#0038A8", "#CE1126"), "OTT": ("#DA1A32", "#B79257"),
    "PHI": ("#F74902", "#000000"), "PIT": ("#000000", "#FCB514"),
    "SJS": ("#006D75", "#EA7200"), "SEA": ("#001628", "#99D9D9"),
    "STL": ("#002F87", "#FCB514"), "TBL": ("#002868", "#FFFFFF"),
    "TOR": ("#00205B", "#FFFFFF"), "UTA": ("#6CACE4", "#0B0B0B"),
    "ARI": ("#8C2633", "#E2D6B5"),  # Arizona, look-back seasons to 2023-24 (added 2026-09-24)
    "VAN": ("#00205B", "#00843D"), "VGK": ("#B4975A", "#333F42"),
    "WSH": ("#041E42", "#C8102E"), "WPG": ("#041E42", "#AC162C"),
}


def _lum(hex_: str) -> float:
    rgb = [int(hex_[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def chip(abbrev: str) -> dict[str, str]:
    bg, accent = TEAM_COLORS[abbrev]
    light, dark = "#FFFFFF", "#0B0B0B"
    contrast = lambda a, b: (max(_lum(a), _lum(b)) + .05) / (min(_lum(a), _lum(b)) + .05)
    fg = light if contrast(bg, light) >= contrast(bg, dark) else dark
    return {"bg": bg, "fg": fg, "accent": accent}
