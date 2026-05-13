"""Decision Journal - Structured decision logging with outcome tracking.

Records the user's rationale, alternatives, confidence, and emotional state
before portfolio changes. Tracks outcomes at 30/90/180 days. Surfaces
patterns over time.

This is a data model and storage layer. No recommendations are generated.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import uuid


class EmotionalState(Enum):
    """Emotional state at time of decision."""

    CALM = "calm"
    ANXIOUS = "anxious"
    EXCITED = "excited"
    FEARFUL = "fearful"
    CONFIDENT = "confident"
    UNCERTAIN = "uncertain"
    NEUTRAL = "neutral"


@dataclass
class DecisionEntry:
    """A single decision journal entry."""

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
    outcome_30d: float | None = None
    outcome_90d: float | None = None
    outcome_180d: float | None = None
    outcome_notes: str = ""
    was_thesis_correct: bool | None = None


@dataclass
class CalibrationMetric:
    """Confidence calibration for a bucket of decisions."""

    confidence_bucket: str
    total_decisions: int
    positive_outcomes: int
    accuracy_pct: float


def compute_calibration(entries: list[DecisionEntry]) -> list[CalibrationMetric]:
    """Compute confidence calibration from completed journal entries.

    Groups decisions by confidence level and compares stated confidence
    against actual outcomes. Pure behavioral observation, not advice.
    """
    buckets: dict[str, dict[str, int]] = {
        "8-10": {"total": 0, "positive": 0},
        "5-7": {"total": 0, "positive": 0},
        "1-4": {"total": 0, "positive": 0},
    }

    for entry in entries:
        if entry.outcome_90d is None:
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

    results = []
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
