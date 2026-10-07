from datetime import datetime

import pytest

from metro_control.line import load_line, station_ids
from metro_control.timeutil import MSK, to_msk, to_utc


def test_line_structure():
    line = load_line()
    assert len(line.stations) == 19 and len(station_ids()) == 19
    assert len(line.segments) == 36
    order = {s.id: s.order for s in line.stations}
    pairs = {(g.from_station, g.to_station) for g in line.segments}
    for g in line.segments:
        assert abs(order[g.from_station] - order[g.to_station]) == 1
        assert (g.to_station, g.from_station) in pairs
        assert g.id == f"{g.from_station}__{g.to_station}"
    assert sum(len(s.vestibules) for s in line.stations) == 24


def test_params_sourced():
    line = load_line()
    assert line.params["train_capacity"].value == 1458
    assert all(p.source.strip() for p in line.params.values())


def test_time_helpers():
    assert to_utc(datetime(2026, 9, 30, 12, 0, tzinfo=MSK)) == datetime(
        2026, 9, 30, 9, 0, tzinfo=to_utc(datetime(2026, 1, 1, tzinfo=MSK)).tzinfo
    )
    assert to_msk(to_utc(datetime(2026, 9, 30, 12, 0, tzinfo=MSK))).hour == 12
    with pytest.raises(ValueError):
        to_utc(datetime(2026, 9, 30, 12, 0))
    with pytest.raises(ValueError):
        to_msk(datetime(2026, 9, 30, 12, 0))
