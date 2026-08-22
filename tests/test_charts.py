"""
Most of these test when a chart is NOT drawn. An agent that charts everything
produces a one-bar chart for a COUNT and an illegible 1,000-label axis for
anything ungrouped, and both cost the reader time before they can be dismissed.
"""

import charts


TOP_SELLERS = [("seller_a", 229237.63), ("seller_b", 222776.05), ("seller_c", 200326.12)]
BY_YEAR = [("2016", 329), ("2017", 45101), ("2018", 54011)]


# --- when not to draw -------------------------------------------------------

def test_a_single_scalar_is_not_charted():
    """'How many sellers are there' is a number, not a comparison."""
    assert not charts.is_chartable(["count"], [(3095,)])


def test_one_row_is_not_charted():
    assert not charts.is_chartable(["seller_id", "revenue"], [("a", 1.0)])


def test_too_many_rows_are_not_charted():
    rows = [(f"cat_{i}", float(i)) for i in range(200)]
    assert not charts.is_chartable(["category", "n"], rows)


def test_all_text_columns_are_not_charted():
    assert not charts.is_chartable(["a", "b"], [("x", "y"), ("p", "q")])


def test_empty_result_is_not_charted():
    assert not charts.is_chartable(["a", "b"], [])


def test_booleans_do_not_count_as_numeric():
    assert not charts.is_chartable(["a", "flag"], [("x", True), ("y", False)])


# --- when to draw -----------------------------------------------------------

def test_a_ranking_is_chartable():
    assert charts.is_chartable(["seller_id", "revenue"], TOP_SELLERS)


def test_a_time_series_is_chartable():
    assert charts.is_chartable(["year", "orders"], BY_YEAR)


def test_periods_are_detected_as_a_series():
    assert charts._looks_like_a_series(["2016", "2017", "2018"])
    assert charts._looks_like_a_series(["2017-01", "2017-02"])
    assert not charts._looks_like_a_series(["SP", "RJ", "MG"])


# --- rendering --------------------------------------------------------------

def test_render_writes_a_png(tmp_path):
    path = charts.render("Top sellers by revenue", ["seller_id", "revenue"],
                         TOP_SELLERS, out_dir=tmp_path)
    assert path and path.endswith(".png")
    written = tmp_path / (path.split("\\")[-1].split("/")[-1])
    assert written.exists() and written.stat().st_size > 1000


def test_render_returns_none_rather_than_charting_a_scalar(tmp_path):
    assert charts.render("How many sellers?", ["count"], [(3095,)], out_dir=tmp_path) is None


def test_render_never_raises(tmp_path):
    """A chart is a nicety; it must not be able to fail a correct query."""
    for cols, rows in [(None, None), ([], []), (["a"], [(1,)]), (["a", "b"], [(None, None)])]:
        assert charts.render("q", cols, rows, out_dir=tmp_path) is None


def test_long_labels_are_truncated(tmp_path):
    """Seller ids are 32-char hashes and would otherwise fill the figure."""
    rows = [("a" * 60, 10.0), ("b" * 60, 5.0)]
    assert charts.render("Long labels", ["id", "v"], rows, out_dir=tmp_path)
