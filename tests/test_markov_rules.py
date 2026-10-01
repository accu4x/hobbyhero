"""Penalty-clock rules and table smoothing for the Markov simulator."""

import pandas as pd
import pytest

from src.models.markov.timing import PenaltyClock, manpower_category
from src.models.markov.transitions import BackoffTable, xg_bin


def test_minor_expires_after_two_minutes():
    c = PenaltyClock()
    c.add("A", "MINOR", 100, overtime=False)
    assert c.skaters(False) == (5, 4)
    assert c.next_expiry() == 220
    c.expire(220)
    assert c.skaters(False) == (5, 5)


def test_power_play_goal_releases_minor_not_major():
    c = PenaltyClock()
    c.add("A", "MINOR", 0, False)
    c.release_on_goal("H", 30, 5, 4)
    assert c.skaters(False) == (5, 5)
    c.add("A", "MAJOR", 100, False)
    c.release_on_goal("H", 130, 5, 4)
    assert c.skaters(False) == (5, 4)


def test_short_handed_goal_releases_nothing():
    c = PenaltyClock()
    c.add("H", "MINOR", 0, False)
    c.release_on_goal("H", 30, 4, 5)
    assert c.skaters(False) == (4, 5)


def test_double_minor_first_half_ends_on_goal():
    c = PenaltyClock()
    c.add("H", "DOUBLE", 0, False)
    c.release_on_goal("A", 60, 4, 5)       # during the first two minutes
    assert c.next_expiry() == 180 and c.skaters(False) == (4, 5)
    c.release_on_goal("A", 150, 4, 5)      # during the second
    assert c.skaters(False) == (5, 5)


def test_coincidental_minors_give_4v4_only_at_full_strength():
    c = PenaltyClock()
    c.add("N", "COINC", 0, False)
    assert c.skaters(False) == (4, 4)
    c2 = PenaltyClock()
    c2.add("H", "MINOR", 0, False)
    c2.add("N", "COINC", 10, False)
    assert c2.skaters(False) == (4, 5)


def test_overtime_penalties_add_a_skater():
    c = PenaltyClock()
    c.add("H", "MINOR", 3700, overtime=True)
    assert c.skaters(True) == (3, 4)
    c.add("H", "MINOR", 3710, overtime=True)
    assert c.skaters(True) == (3, 5)


def test_regulation_floor_is_three_skaters():
    c = PenaltyClock()
    for t in (0, 5, 10):
        c.add("A", "MINOR", t, False)
    assert c.skaters(False) == (5, 3)


@pytest.mark.parametrize("h, a, hp, ap, expected", [
    (5, 5, False, False, "EV5"), (5, 4, False, False, "HPP"), (3, 5, False, False, "APP2"),
    (5, 5, True, False, "HEN"), (4, 4, False, False, "EV4"),
])
def test_manpower_category(h, a, hp, ap, expected):
    assert manpower_category(h, a, hp, ap) == expected


def test_backoff_table_falls_back_and_smooths():
    df = pd.DataFrame({"a": ["x"] * 90 + ["y"] * 10, "b": ["p"] * 100,
                       "o": ["1"] * 90 + ["0"] * 10})
    t = BackoffTable(("0", "1"), [("a", "b"), ("a",)], alpha=10).fit(df, "o")
    assert t.probs(("x", "p"))[1] > 0.9
    # unseen finest key -> coarser level; unseen everywhere -> global rate
    assert t.probs(("z", "q"))[1] == pytest.approx(90.5 / 101)
    assert t.sample(("x", "p"), 0.999) == "1"


def test_xg_bins():
    assert [xg_bin(x) for x in (0.01, 0.03, 0.05, 0.1, 0.5)] == ["0", "1", "1", "2", "3"]


def test_aalen_johansen_without_censoring_is_empirical():
    import numpy as np
    from src.models.markov.timing import aalen_johansen
    t = np.array([1.0, 2.0, 2.0, 5.0])
    times, inc = aalen_johansen(t, np.array([0, 0, 1, 1]), 2, np.empty(0))
    assert list(times) == [1.0, 2.0, 5.0]
    assert inc.sum() == pytest.approx(1.0)
    assert inc[:, 0].sum() == pytest.approx(0.5)


def test_aalen_johansen_censoring_shifts_mass_later():
    import numpy as np
    from src.models.markov.timing import aalen_johansen
    # two quick events, and two intervals cut at 3s that would have lasted longer
    times, inc = aalen_johansen(np.array([1.0, 2.0, 10.0]), np.zeros(3, dtype=int), 1,
                                np.array([3.0, 3.0]))
    w = inc[:, 0] / inc[:, 0].sum()
    assert w[list(times).index(10.0)] > 1 / 3      # naive share of the 10s event is 1/3


def test_mark_expiries_cuts_the_interval_at_the_expiry():
    from src.models.markov.timing import mark_expiries
    ev = pd.DataFrame({
        "game_id": [1] * 4, "kind": ["PSTART", "PENALTY", "FACEOFF", "ATTEMPT"],
        "t": [0.0, 10.0, 10.0, 140.0], "actor": ["N", "A", "H", "H"],
        "pen_cat": ["", "MINOR", "", ""], "outcome": ["", "", "", "saved"],
        "mp": ["EV5", "EV5", "HPP", "EV5"], "period": [1] * 4,
    })
    m = mark_expiries(ev)
    assert m["cens"].iloc[3] == pytest.approx(120.0)     # 10 -> 130 expiry
    assert m["post_t"].iloc[3] == pytest.approx(130.0)
    assert pd.isna(m["cens"].iloc[2])
