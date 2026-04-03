"""Fixed narrative case mapping (Case1–Case10).

Each case maps to a specific parent structure context and records whether
the ``rising_channel_breakdown_retest_short`` rule would be a valid
classification.  These mappings are treated as immutable truth — they
must not be changed by backtest output or automated re-classification.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CaseMapping:
    case_id: str
    parent_names: list[str]
    narrative_context: str
    valid_as_ascending_breakdown_retest_short: bool
    invalidation_reason: str
    notes: str = ""


CASE_MAPPINGS: list[CaseMapping] = [
    # ── Case 1–3: merged cluster ─────────────────────────────────────
    CaseMapping(
        case_id="Case1",
        parent_names=["Parent_A"],
        narrative_context="major_descending_channel_lower_boundary_false_breakdown_reclaim_cluster",
        valid_as_ascending_breakdown_retest_short=False,
        invalidation_reason="false_breakdown_reclaim_cluster_not_ascending_breakdown",
    ),
    CaseMapping(
        case_id="Case2",
        parent_names=["Parent_A"],
        narrative_context="major_descending_channel_lower_boundary_false_breakdown_reclaim_cluster",
        valid_as_ascending_breakdown_retest_short=False,
        invalidation_reason="false_breakdown_reclaim_cluster_not_ascending_breakdown",
    ),
    CaseMapping(
        case_id="Case3",
        parent_names=["Parent_A"],
        narrative_context="major_descending_channel_lower_boundary_false_breakdown_reclaim_cluster",
        valid_as_ascending_breakdown_retest_short=False,
        invalidation_reason="false_breakdown_reclaim_cluster_not_ascending_breakdown",
    ),
    # ── Case 4 ───────────────────────────────────────────────────────
    CaseMapping(
        case_id="Case4",
        parent_names=["Parent_A"],
        narrative_context="major_descending_channel_lower_boundary_support_context",
        valid_as_ascending_breakdown_retest_short=False,
        invalidation_reason="descending_channel_support_context_not_ascending_breakdown",
    ),
    # ── Case 5–7 ─────────────────────────────────────────────────────
    CaseMapping(
        case_id="Case5",
        parent_names=["Parent_D"],
        narrative_context="major_ascending_channel_lower_boundary_support_reaction_context",
        valid_as_ascending_breakdown_retest_short=False,
        invalidation_reason="support_reaction_inside_intact_ascending_channel",
    ),
    CaseMapping(
        case_id="Case6",
        parent_names=["Parent_D"],
        narrative_context="major_ascending_channel_lower_boundary_support_reaction_context",
        valid_as_ascending_breakdown_retest_short=False,
        invalidation_reason="support_reaction_inside_intact_ascending_channel",
    ),
    CaseMapping(
        case_id="Case7",
        parent_names=["Parent_D"],
        narrative_context="major_ascending_channel_lower_boundary_support_reaction_context",
        valid_as_ascending_breakdown_retest_short=False,
        invalidation_reason="support_reaction_inside_intact_ascending_channel",
    ),
    # ── Case 8–9 ─────────────────────────────────────────────────────
    CaseMapping(
        case_id="Case8",
        parent_names=["Parent_E"],
        narrative_context="shock_break_reclaim_context",
        valid_as_ascending_breakdown_retest_short=False,
        invalidation_reason="black_swan_shock_context_not_standard_breakdown",
    ),
    CaseMapping(
        case_id="Case9",
        parent_names=["Parent_E"],
        narrative_context="post_shock_stabilization_context",
        valid_as_ascending_breakdown_retest_short=False,
        invalidation_reason="post_shock_stabilization_not_standard_breakdown",
    ),
    # ── Case 10 ──────────────────────────────────────────────────────
    CaseMapping(
        case_id="Case10",
        parent_names=["Parent_F", "Parent_G"],
        narrative_context="ascending_rebound_channel_breakdown_retest_short",
        valid_as_ascending_breakdown_retest_short=True,
        invalidation_reason="",
        notes=(
            "Current closest valid short live context: "
            "2026-03-29 breakdown + 2026-04-01 retest near 69310 as resistance. "
            "Inherits Parent F bearish impulse into Parent G ascending rebound channel."
        ),
    ),
]


def get_case(case_id: str) -> CaseMapping | None:
    for case in CASE_MAPPINGS:
        if case.case_id == case_id:
            return case
    return None
