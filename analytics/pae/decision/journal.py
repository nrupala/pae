"""Decision Journal - Structured decision logging with outcome tracking.

Records the user's rationale, alternatives, confidence, and emotional state
before portfolio changes. Tracks outcomes at 30/90/180 days. Surfaces
patterns over time.

This is a data model and storage layer. No recommendations are generated.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum


class EmotionalState(Enum):
    """Emotional state at time of decision."""

    CALM = "calm"
    ANXIOUS = "anxious"
    EXCITED = "excited"
    FEARFUL = "fearful"
    CONFIDENT = "confident"
    UNCERTAIN = "uncertain"
    NEUTRAL = "neutral"


# Valid emotional state values for input validation
_VALID_EMOTIONAL_STATES: frozenset[str] = frozenset(e.value for e in EmotionalState)

# Confidence score valid range (inclusive)
CONFIDENCE_MIN = 1
CONFIDENCE_MAX = 10


@dataclass
class DecisionEntry:
    """A single decision journal entry.

    Attributes:
        entry_id: Unique identifier (first 12 chars of UUID4).
        timestamp: ISO 8601 UTC timestamp of entry creation.
        action: Description of the portfolio action taken.
        symbols_affected: List of ticker symbols involved.
        rationale: Why the decision was made.
        alternatives_considered: Other options that were evaluated.
        thesis: The investment thesis behind the decision.
        confidence: Self-assessed confidence (1-10 scale).
        time_horizon: Expected holding period (e.g. "6 months").
        what_could_go_wrong: Pre-mortem analysis of risks.
        max_acceptable_loss_pct: Maximum tolerable loss as a percentage.
        emotional_state: Emotional state at decision time.
        market_context: Description of current market conditions.
        trigger: What triggered the decision.
        outcome_30d: 30-day return outcome (None if not yet measured).
        outcome_90d: 90-day return outcome (None if not yet measured).
        outcome_180d: 180-day return outcome (None if not yet measured).
        outcome_notes: Free-text notes on the outcome.
        was_thesis_correct: Whether the original thesis played out.
    """

    entry_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    action: str = ""
    symbols_affected: list[str] = field(default_factory=list)
    rationale: str = ""
    alternatives_considered: list[str] = field(default_factory=list)
    thesis: str = ""
    confidence: int = 5
    time_horizon: str = ""
    what_could_go_wrong: str = ""
    max_acceptable_loss_pct: float = 0.0
    emotional_state: str = EmotionalState.NEUTRAL.value
    market_context: str = ""
    trigger: str = ""
    outcome_30d: float | None = None
    outcome_90d: float | None = None
    outcome_180d: float | None = None
    outcome_notes: str = ""
    was_thesis_correct: bool | None = None


def validate_entry(entry: DecisionEntry) -> list[str]:
    """Validate a DecisionEntry for data integrity.

    Checks confidence range, emotional state validity, loss percentage bounds,
    and outcome value sanity. Returns a list of validation error messages.
    An empty list means the entry is valid.

    Args:
        entry: The DecisionEntry to validate.

    Returns:
        List of validation error strings. Empty if valid.
    """
    errors: list[str] = []

    # Confidence must be in [1, 10]
    if not isinstance(entry.confidence, int):
        errors.append(
            f"confidence must be an integer, got {type(entry.confidence).__name__}"
        )
    elif entry.confidence < CONFIDENCE_MIN or entry.confidence > CONFIDENCE_MAX:
        errors.append(
            f"confidence must be between {CONFIDENCE_MIN} and {CONFIDENCE_MAX}, "
            f"got {entry.confidence}"
        )

    # Emotional state must be a recognized value
    if entry.emotional_state not in _VALID_EMOTIONAL_STATES:
        errors.append(
            f"emotional_state '{entry.emotional_state}' is not valid. "
            f"Must be one of: {', '.join(sorted(_VALID_EMOTIONAL_STATES))}"
        )

    # max_acceptable_loss_pct should be non-negative and finite
    if not isinstance(entry.max_acceptable_loss_pct, (int, float)):
        errors.append(
            f"max_acceptable_loss_pct must be numeric, "
            f"got {type(entry.max_acceptable_loss_pct).__name__}"
        )
    elif math.isnan(entry.max_acceptable_loss_pct) or math.isinf(entry.max_acceptable_loss_pct):
        errors.append("max_acceptable_loss_pct must be a finite number")
    elif entry.max_acceptable_loss_pct < 0:
        errors.append(
            f"max_acceptable_loss_pct must be non-negative, "
            f"got {entry.max_acceptable_loss_pct}"
        )

    # Validate outcome values if present
    for field_name in ("outcome_30d", "outcome_90d", "outcome_180d"):
        value = getattr(entry, field_name)
        if value is not None:
            if not isinstance(value, (int, float)):
                errors.append(f"{field_name} must be numeric or None")
            elif math.isnan(value) or math.isinf(value):
                errors.append(f"{field_name} must be a finite number, got {value}")

    return errors


@dataclass
class CalibrationMetric:
    """Confidence calibration for a bucket of decisions.

    Attributes:
        confidence_bucket: Label for the confidence range (e.g. "8-10").
        total_decisions: Number of decisions in this bucket.
        positive_outcomes: Number of decisions with positive 90-day returns.
        accuracy_pct: Percentage of positive outcomes.
    """

    confidence_bucket: str
    total_decisions: int
    positive_outcomes: int
    accuracy_pct: float


def compute_calibration(entries: list[DecisionEntry]) -> list[CalibrationMetric]:
    """Compute confidence calibration from completed journal entries.

    Groups decisions by confidence level and compares stated confidence
    against actual 90-day outcomes. Pure behavioral observation, not advice.

    Only entries with a non-None outcome_90d are included. Entries with
    invalid confidence values (outside 1-10) are skipped with a warning.

    Args:
        entries: List of DecisionEntry objects. Entries without outcome_90d
            are silently skipped.

    Returns:
        List of CalibrationMetric for each confidence bucket (high/medium/low).
        Buckets with zero decisions show 0.0% accuracy.

    Raises:
        TypeError: If entries is not a list.
    """
    if not isinstance(entries, list):
        msg = f"entries must be a list, got {type(entries).__name__}"
        raise TypeError(msg)

    buckets: dict[str, dict[str, int]] = {
        "8-10": {"total": 0, "positive": 0},
        "5-7": {"total": 0, "positive": 0},
        "1-4": {"total": 0, "positive": 0},
    }

    for entry in entries:
        if entry.outcome_90d is None:
            continue

        # Skip entries with invalid confidence
        if not isinstance(entry.confidence, int):
            continue
        if entry.confidence < CONFIDENCE_MIN or entry.confidence > CONFIDENCE_MAX:
            continue

        # Skip entries with non-finite outcomes
        if not isinstance(entry.outcome_90d, (int, float)):
            continue
        if math.isnan(entry.outcome_90d) or math.isinf(entry.outcome_90d):
            continue

        if entry.confidence >= 8:
            bucket = "8-10"
        elif entry.confidence >= 5:
            bucket = "5-7"
        else:
            bucket = "1-4"

        buckets[bucket]["total"] += 1
        if entry.outcome_90d > 0:
            buckets[bucket]["positive"] += 1

    results: list[CalibrationMetric] = []
    for bucket_name, data in buckets.items():
        total = data["total"]
        positive = data["positive"]
        accuracy = (positive / total * 100) if total > 0 else 0.0
        results.append(CalibrationMetric(
            confidence_bucket=bucket_name,
            total_decisions=total,
            positive_outcomes=positive,
            accuracy_pct=round(accuracy, 1),
        ))

    return results
