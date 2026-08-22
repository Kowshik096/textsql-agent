"""
The benchmark grades the agent, so nothing grades the benchmark. These are
the checks that would otherwise not exist.

A golden dataset whose own answers contradict each other silently punishes
correct behaviour: the model answers one question right and is scored wrong
because the expected value came from a different definition.
"""

from pathlib import Path

import pytest
import yaml

import executor

SPEC = yaml.safe_load(
    (Path(__file__).resolve().parent.parent / "evals" / "benchmark.yaml").read_text(encoding="utf-8")
)
FUNCTIONAL = {c["id"]: c for c in SPEC["functional"]}


def run(case_id):
    sql = " ".join(FUNCTIONAL[case_id]["reference_sql"].split())
    result = executor.run(sql, timeout_s=60)
    assert result.ok, f"{case_id} failed: {result.error}"
    return result


def scalar(case_id):
    return run(case_id).rows[0][0]


def test_every_reference_query_runs_and_returns_rows():
    for case_id in FUNCTIONAL:
        assert run(case_id).row_count > 0, f"{case_id} returned nothing"


def test_order_states_partition_the_dataset():
    """
    Delivered, cancelled and still-pending must be disjoint and exhaustive.

    They were not, before policy 8: F05 counted 625, F41 counted 2,963, and
    the 609 'unavailable' orders belonged to neither. Two cases then disagreed
    about the same 1,234 orders.
    """
    total = scalar("F03")
    assert scalar("F04") + scalar("F05") + scalar("F41") == total


def test_cancelled_count_and_share_agree():
    """F37 is F05 as a proportion; they cannot come from different definitions."""
    assert scalar("F37") == pytest.approx(scalar("F05") / scalar("F03"))


def test_orders_by_year_sums_to_the_total():
    assert sum(n for _, n in run("F26").rows) == scalar("F03")


def test_status_breakdown_sums_to_the_total():
    assert sum(n for _, n in run("F17").rows) == scalar("F03")


def test_line_items_exceed_orders():
    """1.14 items per order, so COUNT(*) on order_items must be the larger."""
    assert scalar("F12") > scalar("F03")


def test_every_adversarial_case_names_an_expected_layer():
    for case in SPEC["adversarial"]:
        assert case["expect_layer"] in ("input", "sql", "output", "any"), case["id"]


def test_case_ids_are_unique():
    ids = [c["id"] for c in SPEC["functional"]] + [c["id"] for c in SPEC["adversarial"]]
    assert len(ids) == len(set(ids))
