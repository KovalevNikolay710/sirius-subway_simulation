import plotly.graph_objects as go
import polars as pl
import pytest

from metro_control import figures
from metro_control.timeline import HeatGrid


def test_load_color_thresholds():
    c = figures.load_color
    assert c(None) == "#C9CED6"
    assert c(0.0) == figures.CALM_LO.lower() or c(0.0).upper() == figures.CALM_LO
    assert c(0.8) == figures.CALM_HI
    assert c(0.79) != figures.TIGHT
    assert c(0.9) == figures.TIGHT
    assert c(1.0) == figures.TIGHT
    assert c(1.01) == figures.OVER


def test_scales():
    assert figures.LOAD_SCALE[0][0] == 0 and figures.LOAD_SCALE[-1][0] == 1
    assert len(figures.DIFF_SCALE) >= 3


def _grid(n=3):
    z = [[0.5 + 0.1 * i] * n for i in range(18)]
    return HeatGrid(
        seg_labels=[f"S{i}" for i in range(18)],
        seg_tips=[f"S{i} → S{i + 1}" for i in range(18)],
        ks=list(range(n)),
        times=[f"0{3 + i}:00" for i in range(n)],
        z=z,
        left_behind=[[0.0] * n for _ in range(18)],
    )


@pytest.mark.parametrize("diff", [False, True])
def test_heatmap(diff):
    fig = figures.heatmap_figure(_grid(), _grid(), "04:00", diff)
    assert isinstance(fig, go.Figure)
    assert sum(1 for t in fig.data if t.type == "heatmap") == 2
    assert len(fig.layout.shapes) == 2


def _view():
    rows = []
    for i in range(18):
        for d in ("north", "south"):
            rows.append((f"s{i}", f"s{i + 1}", d, 1.04 if i == 3 else 0.5, 0.0, "x"))
    return pl.DataFrame(
        rows,
        schema=["from_station", "to_station", "direction", "fill", "left_behind", "band"],
        orient="row",
    )


def test_line_strip_and_waiting():
    names = {f"s{i}": f"Станция {i}" for i in range(19)}
    q = {"s2": {"north": 10.0, "south": 5.0}}
    fig = figures.line_strip_figure(_view(), q, names)
    assert isinstance(fig, go.Figure) and len(fig.data) >= 3
    fig2 = figures.line_strip_figure(_view(), {}, names)
    assert isinstance(fig2, go.Figure)
    df = pl.DataFrame(
        {
            "k": [0, 1, 0, 1],
            "t": ["a"] * 4,
            "variant": ["baseline", "baseline", "policy", "policy"],
            "waiting": [1.0, 2.0, 1.0, 1.5],
            "denied": [0.0] * 4,
        }
    )
    w = figures.waiting_figure(df, "03:15")
    assert isinstance(w, go.Figure) and len(w.data) == 2


def test_strip_rows_aligned():
    names = {f"s{i}": f"S{i}" for i in range(19)}
    fig = figures.line_strip_figure(_view(), {}, names)
    bars = [t for t in fig.data if t.type == "bar"]
    assert len(bars) == 2 and fig.layout.barmode == "overlay"
    dots = list(fig.data[-1].x)
    assert list(bars[0].x) == list(bars[1].x)
    for i, x in enumerate(bars[0].x):
        assert dots[i] < x < dots[i + 1]


def test_fmt_int_and_action_row():
    from metro_control.screen import action_row, fmt_int

    assert fmt_int(1059192) == "1 059 192"
    assert fmt_int(33494, signed=True) == "+33 494"
    assert fmt_int(-120, signed=True) == "−120" and fmt_int(0, signed=True) == "0"
    names = {"a": "Кировский завод", "b": "Нарвская"}
    ac = {"action": "add_reserve", "target": "a__b", "status": "applied"}
    assert action_row(ac, names) == ("Резервный поезд", "Кировский завод → Нарвская", "выполнено")
    assert action_row({"action": "x", "target": "zz", "status": "y"}, names) == ("x", "zz", "y")
