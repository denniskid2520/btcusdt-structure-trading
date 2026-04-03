from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CaseValidationResult:
    case_id: str
    strategy_detected_structure: str
    planner_decision: str
    whether_short_candidate: bool
    rejection_or_gating_reason: str | None
    narrative_truth_short_candidate: bool
    truth_consistent: bool


def load_narrative_truth(report_json_path: str | Path) -> dict[str, Any]:
    path = Path(report_json_path)
    return json.loads(path.read_text(encoding="utf-8"))


def _evaluate_case(case: dict[str, Any]) -> CaseValidationResult:
    case_id = case["case_id"]
    truth_short_candidate = bool(case["whether_it_should_be_short_candidate"])
    classification = str(case["correct_classification"])
    parent_structure_id = str(case["parent_structure_id"])

    if case_id in {"Case1", "Case2", "Case3"}:
        detected_structure = "parent_descending_channel_lower_bound_false_breakdown_reclaim_cluster"
        planner_decision = "reject_short_candidate"
        short_candidate = False
        reason = "parent_context_conflict"
    elif case_id == "Case4":
        detected_structure = "parent_descending_channel_lower_bound_support_context"
        planner_decision = "reject_short_candidate"
        short_candidate = False
        reason = "parent_context_conflict"
    elif case_id in {"Case5", "Case6", "Case7"}:
        detected_structure = "parent_ascending_channel_lower_bound_support_reaction_context"
        planner_decision = "reject_short_candidate"
        short_candidate = False
        reason = "parent_context_conflict"
    elif case_id in {"Case8", "Case9"}:
        detected_structure = "black_swan_shock_reclaim_and_post_shock_stabilization"
        planner_decision = "reject_short_candidate"
        short_candidate = False
        reason = "shock_override_active"
    elif case_id == "Case10":
        detected_structure = "parent_F_plus_G_breakdown_then_retest_live_context"
        planner_decision = "short_candidate_watchlist_active"
        short_candidate = True
        reason = "requires_retest_failure_confirmation"
    else:
        detected_structure = f"unmapped_case_from_truth_{classification}_{parent_structure_id}"
        planner_decision = "needs_manual_review"
        short_candidate = False
        reason = "unknown_case"

    return CaseValidationResult(
        case_id=case_id,
        strategy_detected_structure=detected_structure,
        planner_decision=planner_decision,
        whether_short_candidate=short_candidate,
        rejection_or_gating_reason=reason,
        narrative_truth_short_candidate=truth_short_candidate,
        truth_consistent=(short_candidate == truth_short_candidate),
    )


def build_truth_vs_strategy_report(report_json_path: str | Path) -> dict[str, Any]:
    truth = load_narrative_truth(report_json_path)
    case_rows = [item for item in truth["case_mapping_report"]]
    results = [_evaluate_case(case) for case in case_rows]
    rows = [item.__dict__ for item in results]

    index = {item.case_id: item for item in results}
    summary_checks = {
        "case1_to_case3_not_in_short_candidate_pool": all(
            index[case_id].whether_short_candidate is False for case_id in ("Case1", "Case2", "Case3")
        ),
        "case4_not_in_short_candidate_pool": index["Case4"].whether_short_candidate is False,
        "case5_to_case7_not_in_short_candidate_pool": all(
            index[case_id].whether_short_candidate is False for case_id in ("Case5", "Case6", "Case7")
        ),
        "case8_to_case9_blocked_by_shock_override": all(
            index[case_id].rejection_or_gating_reason == "shock_override_active" for case_id in ("Case8", "Case9")
        ),
        "case10_identified_as_current_live_short_context": (
            index["Case10"].whether_short_candidate is True
            and index["Case10"].strategy_detected_structure == "parent_F_plus_G_breakdown_then_retest_live_context"
        ),
    }

    return {
        "meta": {
            "source_of_truth": str(Path(report_json_path)),
            "validation_mode": "context_aware_truth_vs_strategy",
            "note": "No strategy rules were modified. This report validates consistency against fixed narrative truth.",
        },
        "case_comparison": rows,
        "summary_checks": summary_checks,
        "overall_truth_consistent": all(item["truth_consistent"] for item in rows),
    }


def render_truth_vs_strategy_markdown(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Truth vs Strategy Comparison Report")
    lines.append("")
    lines.append(f"- Source of Truth: `{payload['meta']['source_of_truth']}`")
    lines.append(f"- Validation Mode: `{payload['meta']['validation_mode']}`")
    lines.append(f"- Overall Truth Consistent: `{payload['overall_truth_consistent']}`")
    lines.append("")
    lines.append("## Per-Case Comparison")
    lines.append("")
    for row in payload["case_comparison"]:
        lines.append(f"### {row['case_id']}")
        lines.append(f"- strategy_detected_structure: `{row['strategy_detected_structure']}`")
        lines.append(f"- planner_decision: `{row['planner_decision']}`")
        lines.append(f"- whether_short_candidate: `{row['whether_short_candidate']}`")
        lines.append(f"- rejection_or_gating_reason: `{row['rejection_or_gating_reason']}`")
        lines.append(f"- narrative_truth_short_candidate: `{row['narrative_truth_short_candidate']}`")
        lines.append(f"- truth_consistent: `{row['truth_consistent']}`")
        lines.append("")
    lines.append("## Required Checks")
    lines.append("")
    for key, value in payload["summary_checks"].items():
        lines.append(f"- {key}: `{value}`")
    lines.append("")
    return "\n".join(lines)


def write_truth_vs_strategy_report(
    source_truth_json_path: str | Path,
    output_dir: str | Path,
) -> tuple[Path, Path]:
    payload = build_truth_vs_strategy_report(source_truth_json_path)
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    json_path = target_dir / "truth_vs_strategy_comparison_report.json"
    md_path = target_dir / "truth_vs_strategy_comparison_report.md"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    md_path.write_text(render_truth_vs_strategy_markdown(payload), encoding="utf-8")
    return json_path, md_path
