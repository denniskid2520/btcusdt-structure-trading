# Truth vs Strategy Comparison Report

- Source of Truth: `data\reports\narrative_case_mapping_report.json`
- Validation Mode: `context_aware_truth_vs_strategy`
- Overall Truth Consistent: `True`

## Per-Case Comparison

### Case1
- strategy_detected_structure: `parent_descending_channel_lower_bound_false_breakdown_reclaim_cluster`
- planner_decision: `reject_short_candidate`
- whether_short_candidate: `False`
- rejection_or_gating_reason: `parent_context_conflict`
- narrative_truth_short_candidate: `False`
- truth_consistent: `True`

### Case2
- strategy_detected_structure: `parent_descending_channel_lower_bound_false_breakdown_reclaim_cluster`
- planner_decision: `reject_short_candidate`
- whether_short_candidate: `False`
- rejection_or_gating_reason: `parent_context_conflict`
- narrative_truth_short_candidate: `False`
- truth_consistent: `True`

### Case3
- strategy_detected_structure: `parent_descending_channel_lower_bound_false_breakdown_reclaim_cluster`
- planner_decision: `reject_short_candidate`
- whether_short_candidate: `False`
- rejection_or_gating_reason: `parent_context_conflict`
- narrative_truth_short_candidate: `False`
- truth_consistent: `True`

### Case4
- strategy_detected_structure: `parent_descending_channel_lower_bound_support_context`
- planner_decision: `reject_short_candidate`
- whether_short_candidate: `False`
- rejection_or_gating_reason: `parent_context_conflict`
- narrative_truth_short_candidate: `False`
- truth_consistent: `True`

### Case5
- strategy_detected_structure: `parent_ascending_channel_lower_bound_support_reaction_context`
- planner_decision: `reject_short_candidate`
- whether_short_candidate: `False`
- rejection_or_gating_reason: `parent_context_conflict`
- narrative_truth_short_candidate: `False`
- truth_consistent: `True`

### Case6
- strategy_detected_structure: `parent_ascending_channel_lower_bound_support_reaction_context`
- planner_decision: `reject_short_candidate`
- whether_short_candidate: `False`
- rejection_or_gating_reason: `parent_context_conflict`
- narrative_truth_short_candidate: `False`
- truth_consistent: `True`

### Case7
- strategy_detected_structure: `parent_ascending_channel_lower_bound_support_reaction_context`
- planner_decision: `reject_short_candidate`
- whether_short_candidate: `False`
- rejection_or_gating_reason: `parent_context_conflict`
- narrative_truth_short_candidate: `False`
- truth_consistent: `True`

### Case8
- strategy_detected_structure: `black_swan_shock_reclaim_and_post_shock_stabilization`
- planner_decision: `reject_short_candidate`
- whether_short_candidate: `False`
- rejection_or_gating_reason: `shock_override_active`
- narrative_truth_short_candidate: `False`
- truth_consistent: `True`

### Case9
- strategy_detected_structure: `black_swan_shock_reclaim_and_post_shock_stabilization`
- planner_decision: `reject_short_candidate`
- whether_short_candidate: `False`
- rejection_or_gating_reason: `shock_override_active`
- narrative_truth_short_candidate: `False`
- truth_consistent: `True`

### Case10
- strategy_detected_structure: `parent_F_plus_G_breakdown_then_retest_live_context`
- planner_decision: `short_candidate_watchlist_active`
- whether_short_candidate: `True`
- rejection_or_gating_reason: `requires_retest_failure_confirmation`
- narrative_truth_short_candidate: `True`
- truth_consistent: `True`

## Required Checks

- case1_to_case3_not_in_short_candidate_pool: `True`
- case4_not_in_short_candidate_pool: `True`
- case5_to_case7_not_in_short_candidate_pool: `True`
- case8_to_case9_blocked_by_shock_override: `True`
- case10_identified_as_current_live_short_context: `True`
