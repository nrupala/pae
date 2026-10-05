"""Tests for decision journal entry validation and calibration."""

import math

import pytest

from pae.decision.journal import (
    CalibrationMetric,
    DecisionEntry,
    EmotionalState,
    compute_calibration,
    validate_entry,
)


def _entry(**kwargs):
    """Build a DecisionEntry with sensible defaults for testing."""
    kwargs.setdefault("confidence", 7)
    kwargs.setdefault("outcome_90d", 5.0)
    return DecisionEntry(**kwargs)


# --- Entry creation ---


def test_entry_defaults():
    """Test that a bare entry gets sane defaults."""
    entry = DecisionEntry()

    assert len(entry.entry_id) == 12
    assert entry.confidence == 5
    assert entry.emotional_state == EmotionalState.NEUTRAL.value
    assert entry.outcome_30d is None
    assert entry.outcome_90d is None
    assert entry.outcome_180d is None
    assert entry.was_thesis_correct is None
    assert entry.symbols_affected == []
    assert entry.alternatives_considered == []


def test_entry_ids_unique():
    """Test that two default entries get different ids."""
    assert DecisionEntry().entry_id != DecisionEntry().entry_id


def test_entry_timestamp_is_iso8601():
    """Test that the default timestamp parses as ISO 8601."""
    from datetime import datetime

    ts = DecisionEntry().timestamp
    parsed = datetime.fromisoformat(ts)
    assert parsed is not None


def test_entry_stores_fields():
    """Test that constructor kwargs land on the entry."""
    entry = DecisionEntry(
        action="Buy 100 AAPL",
        symbols_affected=["AAPL"],
        rationale="Earnings beat",
        alternatives_considered=["Hold cash"],
        thesis="iPhone cycle",
        confidence=9,
        time_horizon="6 months",
        what_could_go_wrong="Guidance cut",
        max_acceptable_loss_pct=8.0,
        emotional_state=EmotionalState.CALM.value,
        market_context="Bull market",
        trigger="Breakout",
    )

    assert entry.action == "Buy 100 AAPL"
    assert entry.symbols_affected == ["AAPL"]
    assert entry.confidence == 9
    assert entry.max_acceptable_loss_pct == 8.0
    assert entry.emotional_state == "calm"


def test_all_emotional_states_valid():
    """Test that every EmotionalState enum value passes validation."""
    for state in EmotionalState:
        entry = _entry(emotional_state=state.value)
        assert validate_entry(entry) == [], f"state {state.value} should be valid"


# --- Validation ---


def test_valid_entry_no_errors():
    """Test that a well-formed entry validates clean."""
    entry = _entry(
        confidence=7,
        emotional_state=EmotionalState.CONFIDENT.value,
        max_acceptable_loss_pct=10.0,
        outcome_30d=1.5,
        outcome_90d=4.2,
        outcome_180d=-2.0,
    )

    assert validate_entry(entry) == []


def test_confidence_bounds():
    """Test confidence rejects values outside 1-10."""
    for bad in (0, 11, -3, 100):
        entry = _entry(confidence=bad)
        errors = validate_entry(entry)
        assert len(errors) == 1, f"confidence={bad} should produce one error"
        assert "confidence" in errors[0]

    for good in (1, 5, 10):
        assert validate_entry(_entry(confidence=good)) == []


def test_confidence_must_be_int():
    """Test confidence rejects non-integer types."""
    for bad in (7.5, "7", None):
        errors = validate_entry(_entry(confidence=bad))
        assert any("confidence" in e for e in errors), f"confidence={bad!r} accepted"


def test_bad_emotional_state():
    """Test that an unrecognized emotional state is rejected."""
    errors = validate_entry(_entry(emotional_state="spicy"))

    assert len(errors) == 1
    assert "spicy" in errors[0]
    assert "emotional_state" in errors[0]


def test_negative_loss_pct_rejected():
    """Test that a negative max acceptable loss is rejected."""
    errors = validate_entry(_entry(max_acceptable_loss_pct=-5.0))

    assert len(errors) == 1
    assert "max_acceptable_loss_pct" in errors[0]


def test_non_finite_loss_pct_rejected():
    """Test that NaN/inf loss percentages are rejected."""
    for bad in (math.nan, math.inf, -math.inf):
        errors = validate_entry(_entry(max_acceptable_loss_pct=bad))
        assert any("max_acceptable_loss_pct" in e for e in errors)


def test_non_numeric_loss_pct_rejected():
    """Test that a non-numeric loss percentage is rejected."""
    errors = validate_entry(_entry(max_acceptable_loss_pct="ten"))
    assert any("max_acceptable_loss_pct" in e for e in errors)


def test_outcome_values_must_be_numeric_or_none():
    """Test that outcome fields reject non-numeric values."""
    for field_name in ("outcome_30d", "outcome_90d", "outcome_180d"):
        errors = validate_entry(_entry(**{field_name: "good"}))
        assert any(field_name in e for e in errors)


def test_outcome_values_must_be_finite():
    """Test that NaN/inf outcomes are rejected."""
    for field_name in ("outcome_30d", "outcome_90d", "outcome_180d"):
        for bad in (math.nan, math.inf):
            errors = validate_entry(_entry(**{field_name: bad}))
            assert any(field_name in e for e in errors)


def test_outcome_none_is_valid():
    """Test that unmeasured (None) outcomes validate clean."""
    entry = DecisionEntry(outcome_30d=None, outcome_90d=None, outcome_180d=None)
    assert validate_entry(entry) == []


def test_multiple_errors_accumulate():
    """Test that validation reports every problem, not just the first."""
    entry = _entry(confidence=99, emotional_state="bogus", max_acceptable_loss_pct=-1.0)
    errors = validate_entry(entry)

    assert len(errors) == 3
    assert any("confidence" in e for e in errors)
    assert any("emotional_state" in e for e in errors)
    assert any("max_acceptable_loss_pct" in e for e in errors)


# --- Calibration ---


def test_calibration_requires_list():
    """Test that compute_calibration raises TypeError on non-list input."""
    with pytest.raises(TypeError):
        compute_calibration("not a list")
    with pytest.raises(TypeError):
        compute_calibration(None)


def test_calibration_empty_journal():
    """Test empty journal returns three buckets with zero decisions."""
    result = compute_calibration([])

    assert len(result) == 3
    assert [m.confidence_bucket for m in result] == ["8-10", "5-7", "1-4"]
    for metric in result:
        assert metric.total_decisions == 0
        assert metric.positive_outcomes == 0
        assert metric.accuracy_pct == 0.0


def test_calibration_skips_unmeasured_entries():
    """Test entries without outcome_90d are excluded."""
    entries = [_entry(confidence=9, outcome_90d=None)]

    result = compute_calibration(entries)

    assert all(m.total_decisions == 0 for m in result)


def test_calibration_single_entry():
    """Test calibration with a single measured entry."""
    entries = [_entry(confidence=9, outcome_90d=12.5)]

    result = compute_calibration(entries)
    high = next(m for m in result if m.confidence_bucket == "8-10")

    assert high.total_decisions == 1
    assert high.positive_outcomes == 1
    assert high.accuracy_pct == 100.0


def test_calibration_bucket_assignment():
    """Test confidence levels land in the right buckets."""
    entries = [
        _entry(confidence=10, outcome_90d=1.0),
        _entry(confidence=8, outcome_90d=1.0),
        _entry(confidence=7, outcome_90d=1.0),
        _entry(confidence=5, outcome_90d=1.0),
        _entry(confidence=4, outcome_90d=1.0),
        _entry(confidence=1, outcome_90d=1.0),
    ]

    result = compute_calibration(entries)
    buckets = {m.confidence_bucket: m for m in result}

    assert buckets["8-10"].total_decisions == 2
    assert buckets["5-7"].total_decisions == 2
    assert buckets["1-4"].total_decisions == 2


def test_calibration_accuracy_math():
    """Test accuracy percentage is computed and rounded correctly."""
    entries = [
        _entry(confidence=9, outcome_90d=5.0),
        _entry(confidence=9, outcome_90d=-3.0),
        _entry(confidence=9, outcome_90d=2.0),
    ]

    result = compute_calibration(entries)
    high = next(m for m in result if m.confidence_bucket == "8-10")

    assert high.total_decisions == 3
    assert high.positive_outcomes == 2
    assert high.accuracy_pct == 66.7


def test_calibration_zero_return_not_positive():
    """Test a 0.0 outcome_90d counts as non-positive."""
    entries = [_entry(confidence=6, outcome_90d=0.0)]

    result = compute_calibration(entries)
    mid = next(m for m in result if m.confidence_bucket == "5-7")

    assert mid.total_decisions == 1
    assert mid.positive_outcomes == 0
    assert mid.accuracy_pct == 0.0


def test_calibration_overconfidence_pattern():
    """Test high-confidence bucket can show low accuracy (miscalibration)."""
    entries = [
        _entry(confidence=9, outcome_90d=-8.0),
        _entry(confidence=10, outcome_90d=-4.0),
        _entry(confidence=3, outcome_90d=15.0),
        _entry(confidence=2, outcome_90d=9.0),
    ]

    result = compute_calibration(entries)
    buckets = {m.confidence_bucket: m for m in result}

    assert buckets["8-10"].accuracy_pct == 0.0
    assert buckets["1-4"].accuracy_pct == 100.0


def test_calibration_skips_invalid_confidence():
    """Test entries with out-of-range confidence are excluded."""
    entries = [
        _entry(confidence=9, outcome_90d=5.0),
        _entry(confidence=99, outcome_90d=5.0),
        _entry(confidence=0, outcome_90d=5.0),
        _entry(confidence=7.5, outcome_90d=5.0),  # non-int confidence
    ]

    result = compute_calibration(entries)
    high = next(m for m in result if m.confidence_bucket == "8-10")

    assert high.total_decisions == 1


def test_calibration_skips_non_finite_outcomes():
    """Test entries with NaN/inf outcomes are excluded."""
    entries = [
        _entry(confidence=9, outcome_90d=5.0),
        _entry(confidence=9, outcome_90d=math.nan),
        _entry(confidence=9, outcome_90d=math.inf),
    ]

    result = compute_calibration(entries)
    high = next(m for m in result if m.confidence_bucket == "8-10")

    assert high.total_decisions == 1


def test_calibration_returns_metric_dataclasses():
    """Test results are CalibrationMetric instances with the right fields."""
    result = compute_calibration([])

    assert all(isinstance(m, CalibrationMetric) for m in result)


def test_calibration_only_uses_90d_outcome():
    """Test that 30d/180d outcomes alone do not include an entry."""
    entries = [_entry(confidence=9, outcome_90d=None)]
    entries[0].outcome_30d = 20.0
    entries[0].outcome_180d = 25.0

    result = compute_calibration(entries)

    assert all(m.total_decisions == 0 for m in result)
