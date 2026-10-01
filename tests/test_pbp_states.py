"""State normalisation for the Markov simulator (src/features/pbp_states.py)."""

import pytest

from src.features import pbp_states as ps


@pytest.mark.parametrize("sc, period, diff, delayed, expected", [
    ("1551", 1, 0, False, "EV5"),
    ("1541", 1, 0, False, "APP"),       # home 4 skaters, away 5
    ("1451", 2, 0, False, "HPP"),
    ("1351", 2, 0, False, "HPP2"),
    ("1441", 2, 0, False, "EV4"),
    ("1331", 4, 0, False, "EV3"),
    ("1341", 4, 0, False, "HPP"),       # 4v3 in overtime
    ("1560", 3, -1, False, "HEN"),      # home trailing, goalie pulled
    ("0651", 3, 2, False, "AEN"),       # away trailing, goalie pulled
    ("1560", 3, -1, True, "EV5"),       # pulled for a delayed penalty
    ("1560", 2, -1, False, "EV5"),      # not the 3rd period: delayed-penalty pull
    ("1560", 3, 1, False, "EV5"),       # leading team's goalie off: not tactical
    (None, 1, 0, False, "EV5"),
])
def test_manpower(sc, period, diff, delayed, expected):
    assert ps.manpower(sc, period, diff, delayed) == expected


@pytest.mark.parametrize("pens, expected", [
    ([("H", "MIN", 2)], ("H", "MINOR")),
    ([("A", "MIN", 4)], ("A", "DOUBLE")),
    ([("H", "MAJ", 5), ("H", "GAM", 10)], ("H", "MAJOR")),
    ([("H", "MIN", 2), ("A", "MIN", 2)], ("N", "COINC")),
    ([("H", "MAJ", 5), ("A", "MAJ", 5)], ("N", "NOEFF")),         # fighting majors
    ([("H", "MAJ", 5), ("A", "MAJ", 5), ("H", "MIN", 2)], ("H", "MINOR")),
    ([("A", "MIN", 2), ("A", "MIN", 2)], ("A", "TWO_MINOR")),
    ([("H", "MIS", 10)], ("N", "NOEFF")),
    ([("A", "PS", 0)], ("N", "NOEFF")),
    ([("H", "BEN", 2)], ("H", "MINOR")),
])
def test_penalty_category(pens, expected):
    assert ps.penalty_category(pens) == expected


def test_zone_home_follows_defending_side():
    assert ps.zone_home({"xCoord": 60}, "left") == "O"
    assert ps.zone_home({"xCoord": 60}, "right") == "D"
    assert ps.zone_home({"xCoord": 10}, "left") == "N"
    assert ps.zone_home({}, "left") is None


def test_phase():
    assert ps.phase(1, 100) == "P12"
    assert ps.phase(3, 800) == "P3"
    assert ps.phase(3, 900) == "P3L"
    assert ps.phase(4, 10) == "OT"


def test_shooter_perspective():
    assert ps.shooter_mp("HPP", "H") == "PP"
    assert ps.shooter_mp("HPP", "A") == "SH"
    assert ps.shooter_mp("AEN", "H") == "ENA"
    assert ps.shooter_mp("EV5", "A") == "EV5"


def _play(kind, t, owner=None, sc="1551", **det):
    d = dict(det)
    if owner is not None:
        d["eventOwnerTeamId"] = owner
    return {"typeDescKey": kind, "timeInPeriod": t, "situationCode": sc,
            "homeTeamDefendingSide": "left", "periodDescriptor": {"number": 1, "periodType": "REG"},
            "details": d}


def test_parse_and_summarise_small_game():
    home, away = 1, 2
    pbp = {"homeTeam": {"id": home}, "awayTeam": {"id": away}, "plays": [
        _play("period-start", "00:00"),
        _play("faceoff", "00:00", home, xCoord=0, yCoord=0),
        _play("shot-on-goal", "00:10", home, xCoord=60, yCoord=5),
        _play("blocked-shot", "00:20", away, xCoord=-70, yCoord=5),
        _play("penalty", "00:30", away, typeCode="MIN", duration=2, xCoord=0, yCoord=0),
        _play("faceoff", "00:30", home, sc="1451", xCoord=69, yCoord=22),
        _play("goal", "00:40", home, sc="1451", xCoord=80, yCoord=0),
        _play("stoppage", "00:50", sc="1551"),
        _play("period-end", "20:00"),
    ]}
    rec = {"game_id": 1, "season": 20232024, "outcome": "REG", "winner": "AAA",
           "home_abbrev": "AAA"}
    rows = ps.parse_game(rec, pbp)
    kinds = [r["kind"] for r in rows]
    assert kinds == ["PSTART", "FACEOFF", "ATTEMPT", "ATTEMPT", "PENALTY", "FACEOFF",
                     "ATTEMPT", "STOPPAGE", "END"]
    assert [r["shot_idx"] for r in rows if r["kind"] == "ATTEMPT"] == [0, -1, 1]
    blocked = rows[3]
    assert blocked["actor"] == "A" and blocked["zone"] == "D"     # block in home's zone
    goal = rows[6]
    assert goal["mp"] == "HPP" and goal["hs"] == 0
    assert rows[7]["hs"] == 1 and rows[7]["zone"] == "O"            # stoppage keeps zone
    s = ps.summarise(rows, rec)
    assert s["goals_h"] == 1 and s["att_h"] == 2 and s["att_a"] == 1
    assert s["ppo_h"] == 1 and s["ppg_h"] == 1 and s["home_win"] == 1


def test_penalty_shot_is_not_an_attempt_but_keeps_shot_order():
    pbp = {"homeTeam": {"id": 1}, "awayTeam": {"id": 2}, "plays": [
        _play("shot-on-goal", "05:00", 1, sc="0101", xCoord=70, yCoord=0),
        _play("shot-on-goal", "06:00", 1, xCoord=70, yCoord=0),
    ]}
    rows = ps.parse_game({"game_id": 1, "season": 1}, pbp)
    assert len(rows) == 1 and rows[0]["shot_idx"] == 1
