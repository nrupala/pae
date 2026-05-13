"""Decision Journal - Structured decision logging with outcome tracking.

Records the user's rationale, alternatives, confidence, and emotional state
before portfolio changes. Tracks outcomes at 30/90/180 days. Surfaces
patterns over time.

This is a data model and storage layer. No recommendations are generated.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
import uuid

logger = logging.getLogger(__name__)


class EmotionalState(Enum):
    """Emotional state at time of decision.

    Values:
        CALM: Relaxed, clear-headed state.
        ANXIOUS: Worried or stressed state.
        EXCITED: Enthusiastic, high-energy state.
        FEARFUL: Afraid of loss or negative outcome.
        CONFIDENT: Strong conviction in the decision.
        UNCERTAIN: Unsure or ambivalent state.
        NEUTRAL: No strong emotional signal.
    """

    CALM = "calm"
    ANXIOUS = "anxious"
    EXCITED = "excited"
    FEARFUL = "fearful"
    CONFIDENT = "confident"
    UNCERTAIN = "uncertain"
    NEUTRAL = "neutral"


@dataclass
class DecisionEntry:
    """A single decision journal entry.

    Attributes:
        entry_id: Unique identifier (auto-generated 12-char UUID prefix).
        timestamp: ISO 8601 timestamp in UTC (auto-generated).
        action: Description of the action taken (e.g., "Buy 100 shares of SPY").
        symbols_affected: List of ticker symbols involved.
        rationale: Why the decision was made.
        alternatives_considered: Other options that were evaluated.
        thesis: Investment thesis supporting the decision.
        confidence: Confidence level from 1 (low) to 10 (high).
        time_horizon: Expected holding period (e.g., "6 months", "2 years").
        what_could_go_wrong: Pre-mortem analysis.
        max_acceptable_loss_pct: Maximum loss percentage before reconsidering.
        emotional_state: Self-reported emotional state at decision time.
        market_context: Description of market conditions.
        trigger: What prompted the decision.
        outcome_30d: Return after 30 days (None if not yet measured).
        outcome_90d: Return after 90 days (None if not yet measured).
        outcome_180d: Return after 180 days (None if not yet measured).
        outcome_notes: Qualitative notes on the outcome.
        was_thesis_correct: Whether the original thesis played out.
    """

    entry_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
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
    outcome_30d: Optional[float] = None
    outcome_90d: Optional[float] = None
    outcome_180d: Optional[float] = None
    outcome_notes: str = ""
    was_thesis_correct: Optional[bool] = None

    def __post_init__(self) -> None:
        """Validate fields after initialization."""
        if not isinstance(self.confidence, int):
            try:
                self.confidence = int(self.confidence)
            except (ValueError, TypeError):
                self.confidence = 5

        self.confidence = max(1, min(10, self.confidence))

        if self.max_acceptable_loss_pct < 0:
            self.max_acceptable_loss_pct = abs(self.max_acceptable_loss_pct)

        # Validate emotional state
        valid_states = {e.value for e in EmotionalState}
        if self.emotional_state not in valid_states:
            logger.warning(
                "Invalid emotional_state '%s', defaulting to 'neutral'",
                self.emotional_state,
            )
            self.emotional_state = EmotionalState.NEUTRAL.value


@dataclass
class CalibrationMetric:
    """Confidence calibration for a bucket of decisions.

    Attributes:
        confidence_bucket: Label for the confidence range (e.g., "8-10").
        total_decisions: Number of decisions in this bucket.
        positive_outcomes: Number of decisions with positive 90-day outcome.
        accuracy_pct: Percentage of positive outcomes (0.0 to 100.0).
    """

    confidence_bucket: str
    total_decisions: int
    positive_outcomes: int
    accuracy_pct: float


def compute_calibration(entries: list[DecisionEntry]) -> list[CalibrationMetric]:
    """Compute confidence calibration from completed journal entries.

    Groups decisions by confidence level and compares stated confidence
    against actual outcomes. Pure behavioral observation, not advice.

    Args:
        entries: List of DecisionEntry objects. Only entries with a
            non-None outcome_90d value are included in the analysis.

    Returns:
        List of CalibrationMetric for each confidence bucket:
        - "8-10": High confidence decisions
        - "5-7": Medium confidence decisions
        - "1-4": Low confidence decisions

        Empty list if entries is empty.

    Note:
        Uses 90-day outcomes as the default evaluation period.
        A positive outcome is defined as outcome_90d > 0.
    """
    if not entries:
        return []

    buckets: dict[str, dict[str, int]] = {
        "8-10": {"total": 0, "positive": 0},
        "5-7": {"total": 0, "positive": 0},
        "1-4": {"total": 0, "positive": 0},
    }

    evaluated_count = 0

    for entry in entries:
        if entry.outcome_90d is None:
            continue

        evaluated_count += 1

        # Clamp confidence to valid range before bucketing
        conf = max(1, min(10, entry.confidence))

        if conf >= 8:
            bucket = "8-10"
        elif conf >= 5:
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

    logger.info(
        "Calibration computed: %d entries evaluated out of %d total",
        evaluated_count, len(entries),
    )

    return results
