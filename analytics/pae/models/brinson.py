# Copyright (C) 2026 Nrupal Akolkar
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Brinson-Hood-Beebower performance attribution engine.

Classic arithmetic attribution of a portfolio's ACTIVE return (portfolio
return minus benchmark return) against a benchmark, broken down by segment:

    Allocation  = (w_p - w_b) * (R_b_segment - R_b_total)
    Selection   = w_b * (R_p_segment - R_b_segment)
    Interaction = (w_p - w_b) * (R_p_segment - R_b_segment)

Per segment the three effects sum to that segment's active-return
contribution; across segments the totals sum to the portfolio active return
(verified in the test suite). All math is backward-looking.

Treatment of one-sided segments: a segment present on only one side is
treated as weight 0.0 on the missing side, with the missing segment return
imputed as that side's TOTAL return (i.e. the segment is assumed to have
earned what the side as a whole earned). This keeps allocation honest —
the over/under-weight relative to the benchmark total is still measured —
while selection and interaction collapse to zero on the missing side.
This treatment is a convention, not a market truth.

Returns are decimal fractions (0.05 = 5%). Weights are decimal fractions
and must sum to 1.0 on each side.

Boundary: this is performance-EXPLANATION analytics — it decomposes what
drove the difference versus the benchmark. It never recommends actions.
No output constitutes investment advice.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

# Weights must sum to 1.0 within this tolerance (each side separately).
WEIGHT_SUM_TOLERANCE = 1e-6


@dataclass
class SegmentAttribution:
    """Attribution effects for one segment."""

    segment: str
    portfolio_weight: float
    benchmark_weight: float
    portfolio_return: float
    benchmark_return: float
    allocation_effect: float
    selection_effect: float
    interaction_effect: float
    active_contribution: float


@dataclass
class BrinsonAttribution:
    """Complete attribution of the portfolio vs. its benchmark."""

    portfolio_return: float
    benchmark_return: float
    active_return: float
    total_allocation: float
    total_selection: float
    total_interaction: float
    segments: list[SegmentAttribution] = field(default_factory=list)


class BrinsonError(Exception):
    """Raised when attribution cannot be computed from the inputs."""


def _validate_segments(segments: list[dict[str, Any]], side: str) -> None:
    """Validate one side's segment list.

    Args:
        segments: List of dicts with 'segment', 'weight', 'return' keys.
        side: Label used in error messages ('portfolio' or 'benchmark').

    Raises:
        ValueError: If the list is empty, has duplicate/missing names,
            non-finite or out-of-range weights/returns, or weights that do
            not sum to 1.0.
    """
    if not segments:
        msg = f"{side} segments must not be empty"
        raise ValueError(msg)

    names: set[str] = set()
    weight_sum = 0.0
    for i, seg in enumerate(segments):
        if not isinstance(seg, dict):
            msg = f"{side} segment at index {i} must be a dict"
            raise ValueError(msg)

        name = seg.get("segment")
        if not isinstance(name, str) or not name.strip():
            msg = f"{side} segment at index {i} is missing a valid 'segment' name"
            raise ValueError(msg)
        if name in names:
            msg = f"{side} segment '{name}' is duplicated"
            raise ValueError(msg)
        names.add(name)

        for key in ("weight", "return"):
            if key not in seg:
                msg = f"{side} segment '{name}' is missing '{key}'"
                raise ValueError(msg)
            value = seg[key]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                msg = f"{side} segment '{name}' has a non-numeric '{key}': {value!r}"
                raise ValueError(msg)

        weight = float(seg["weight"])
        if weight < 0.0 or weight > 1.0:
            msg = (
                f"{side} segment '{name}' weight {weight} is outside [0, 1] "
                "(weights must be decimal fractions)"
            )
            raise ValueError(msg)
        weight_sum += weight

    if abs(weight_sum - 1.0) > WEIGHT_SUM_TOLERANCE:
        msg = (
            f"{side} weights sum to {weight_sum:.6f}, expected 1.0 "
            "(weights must be decimal fractions summing to 1.0)"
        )
        raise ValueError(msg)


def attribute(
    portfolio_segments: list[dict[str, Any]],
    benchmark_segments: list[dict[str, Any]],
) -> BrinsonAttribution:
    """Run Brinson-Hood-Beebower attribution of portfolio vs. benchmark.

    Args:
        portfolio_segments: List of dicts with 'segment' (str), 'weight'
            (decimal, sum to 1.0), 'return' (decimal) keys.
        benchmark_segments: Same shape for the benchmark.

    Returns:
        BrinsonAttribution with per-segment allocation, selection, and
        interaction effects plus the totals, which sum to the active return.

    Raises:
        ValueError: If either side fails validation.
    """
    _validate_segments(portfolio_segments, "portfolio")
    _validate_segments(benchmark_segments, "benchmark")

    p_by_name = {s["segment"]: s for s in portfolio_segments}
    b_by_name = {s["segment"]: s for s in benchmark_segments}

    portfolio_total = sum(float(s["weight"]) * float(s["return"]) for s in portfolio_segments)
    benchmark_total = sum(float(s["weight"]) * float(s["return"]) for s in benchmark_segments)

    segment_attributions: list[SegmentAttribution] = []
    for name in list(p_by_name) + [n for n in b_by_name if n not in p_by_name]:
        p_seg = p_by_name.get(name)
        b_seg = b_by_name.get(name)

        # One-sided segments: weight 0.0 and return imputed as the side's
        # total (see module docstring for the documented treatment).
        w_p = float(p_seg["weight"]) if p_seg is not None else 0.0
        r_p = float(p_seg["return"]) if p_seg is not None else portfolio_total
        w_b = float(b_seg["weight"]) if b_seg is not None else 0.0
        r_b = float(b_seg["return"]) if b_seg is not None else benchmark_total

        allocation = (w_p - w_b) * (r_b - benchmark_total)
        selection = w_b * (r_p - r_b)
        interaction = (w_p - w_b) * (r_p - r_b)

        segment_attributions.append(
            SegmentAttribution(
                segment=name,
                portfolio_weight=w_p,
                benchmark_weight=w_b,
                portfolio_return=r_p,
                benchmark_return=r_b,
                allocation_effect=allocation,
                selection_effect=selection,
                interaction_effect=interaction,
                active_contribution=allocation + selection + interaction,
            )
        )

    total_allocation = sum(s.allocation_effect for s in segment_attributions)
    total_selection = sum(s.selection_effect for s in segment_attributions)
    total_interaction = sum(s.interaction_effect for s in segment_attributions)

    return BrinsonAttribution(
        portfolio_return=portfolio_total,
        benchmark_return=benchmark_total,
        active_return=portfolio_total - benchmark_total,
        total_allocation=total_allocation,
        total_selection=total_selection,
        total_interaction=total_interaction,
        segments=segment_attributions,
    )
