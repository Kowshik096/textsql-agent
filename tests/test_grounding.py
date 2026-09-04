"""
Layer 3. The false-positive half matters as much as the catch half: a
grounding check that fires on correct answers gets ignored, and an ignored
check is worse than no check.

The seeded-hallucination cases here are the Day 3 milestone.
"""

import pytest

import grounding


ROWS = [
    ("4869f7a5dfa277a7dca6462dcf3b52b2", 229237.63),
    ("53243585a1d6dc2643021fd1853d8905", 222776.05),
    ("4a3ca9315b744ce9f8e9374361493884", 200326.12),
]
QUESTION = "Which 3 sellers generated the most revenue in 2017?"


# --- answers that must pass -------------------------------------------------

def test_exact_numbers_pass():
    a = "The top seller earned 229237.63, followed by 222776.05."
    assert grounding.check(a, ROWS, QUESTION).ok


def test_thousands_separators_pass():
    a = "The top seller earned 229,237.63 in revenue."
    assert grounding.check(a, ROWS, QUESTION).ok


def test_rounded_numbers_pass():
    """226,988 for 226,987.93 is formatting, not invention."""
    a = "The top seller earned about 229,238."
    assert grounding.check(a, ROWS, QUESTION).ok


def test_row_count_passes():
    """3 is the LIMIT - true, useful, and absent from the rows themselves."""
    a = "There are 3 sellers in the result."
    assert grounding.check(a, ROWS, QUESTION).ok


def test_number_echoed_from_the_question_passes():
    a = "In 2017, the leading seller earned 229237.63."
    assert grounding.check(a, ROWS, QUESTION).ok


def test_small_integers_in_prose_pass():
    a = "The top 3 sellers are listed; the first 2 are close together."
    assert grounding.check(a, ROWS, QUESTION).ok


def test_answer_with_no_numbers_passes():
    assert grounding.check("No sellers matched that filter.", [], "").ok


def test_identifier_digits_are_not_treated_as_claims():
    a = "Results come from the order_items2 staging view."
    assert grounding.check(a, ROWS, QUESTION).ok


# --- hallucinations that must be caught -------------------------------------

def test_invented_total_is_caught():
    """The classic: a plausible aggregate nobody computed."""
    a = "The top 3 sellers earned 652339.80 between them."
    r = grounding.check(a, ROWS, QUESTION)
    assert not r.ok
    assert 652339.80 in r.ungrounded


def test_invented_percentage_is_caught():
    a = "The top seller accounts for 34.7% of all revenue."
    r = grounding.check(a, ROWS, QUESTION)
    assert not r.ok
    assert 34.7 in r.ungrounded


def test_invented_row_is_caught():
    a = "A fourth seller earned 198450.00."
    r = grounding.check(a, ROWS, QUESTION)
    assert not r.ok


def test_wrong_digit_is_caught():
    """229237.63 mistyped as 292237.63 - close enough to look right."""
    a = "The top seller earned 292237.63."
    r = grounding.check(a, ROWS, QUESTION)
    assert not r.ok


def test_reason_names_the_offending_numbers():
    a = "Revenue was 999999.99 across 47 sellers."
    r = grounding.check(a, ROWS, QUESTION)
    assert not r.ok
    assert "999999.99" in r.reason or "1e+06" in r.reason
    assert r.reason


# --- extraction helpers -----------------------------------------------------

@pytest.mark.parametrize(
    "text,expected",
    [
        ("1234", [1234.0]),
        ("1,234.56", [1234.56]),
        ("-3 and 4.5", [-3.0, 4.5]),
        ("no numbers here", []),
        ("order_items2", []),
    ],
)
def test_extract_numbers(text, expected):
    assert grounding.extract_numbers(text) == expected


def test_dates_in_string_cells_ground_their_parts():
    rows = [("2017-05-16", 42)]
    assert grounding.check("The earliest order was in 2017.", rows, "").ok


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_row_values_never_raise(bad):
    """
    A guardrail must never raise: inf/nan in a result cell (e.g. from
    SELECT 9e999) used to crash _matches via round()/log10(), turning a
    hostile query into a pipeline 500 instead of a grounded fallback.
    """
    r = grounding.check("The total is 500 items.", [(bad,)], "how many?")
    assert not r.ok
    assert 500.0 in r.ungrounded
