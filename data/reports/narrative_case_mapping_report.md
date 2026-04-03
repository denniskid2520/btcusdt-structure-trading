# Narrative Case Mapping Report (Fixed Schema)

- Version: `narrative-fixed-v1`
- Instrument: `BTCUSDT`
- Contract Type: `USDT-margined perpetual futures`
- Note: This report is explicitly fixed to the user-provided narrative and is not inferred from strategy rules.

## Parent Structure Timeline (A~G)

### Parent A
- Period: `2024-03-13` -> `2024-10-21`
- Type: `major_descending_channel`
- Key Highs: 2024-03-13:73650.0, 2024-05-21:71979.0, 2024-07-29:70079.0
- Key Lows: 2024-05-01:56552.0, 2024-07-05:53485.0, 2024-08-05:49000.0, 2024-09-06:52550.0
- Transition: 2024-08-05 lower-bound liquidity sweep / false breakdown reclaim, then 2024-10-21 upside breakout and 2024-11-04 retest success.

### Parent B
- Period: `2024-11-14` -> `2025-05-02`
- Type: `major_descending_channel`
- Key Highs: 2024-12-17:108353.0, 2025-01-22:106394.0
- Key Lows: 2025-02-28:78258.0, 2025-03-11:78595.0, 2025-04-08:76239.0
- Transition: 2025-05-02 upside breakout of major descending channel, followed by consolidation / retest / support hold before the next impulsive advance.

### Parent C
- Period: `2025-05-08` -> `2025-07-04`
- Type: `local_descending_channel_inside_bullish_transition`
- Key Highs: 2025-05-22:111980.0, 2025-06-10:110400.0, 2025-06-30:110000.0
- Key Lows: 2025-06-05:100372.0, 2025-06-22:98200.0
- Transition: After another upper-bound test on 2025-06-30, price broke out and then confirmed with 2025-07-04 retest success.

### Parent D
- Period: `2025-07-05` -> `2025-10-10`
- Type: `major_ascending_channel`
- Key Highs: 2025-07-14:123218.0, 2025-08-14:124474.0, 2025-10-06:126199.0
- Key Lows: 2025-08-31:108076.0, 2025-09-27:109064.0
- Transition: 2025-10-10 black swan shock starts after this structure.

### Parent E
- Period: `2025-10-10` -> `2025-11-20`
- Type: `black_swan_shock_liquidity_sweep_reclaim`
- Key Highs: 2025-10-10:122550.0
- Key Lows: 2025-10-10:102000.0
- Transition: Pierce below major ascending-channel lower bound then reclaim; stabilize around the major lower-bound zone (~106k area) for a period, then later print a clean bearish breakdown.

### Parent F
- Period: `2025-11-21` -> `2026-01-29`
- Type: `bearish_impulse_then_ascending_rebound_channel`
- Key Highs: 2025-11-28:93092.0, 2025-12-03:94150.0, 2026-01-14:97924.0
- Key Lows: 2025-11-21:80600.0, 2025-12-01:83822.0, 2026-01-20:87263.0
- Transition: 2026-01-29 downside breakdown followed by selloff into 2026-02-06 low near 60000.

### Parent G
- Period: `2026-02-06` -> `2026-04-01`
- Type: `current_ascending_rebound_channel_after_crash`
- Key Highs: 2026-02-09:71453.0, 2026-03-04:74050.0, 2026-03-17:76000.0
- Key Lows: 2026-02-06:60000.0, 2026-02-25:63913.0, 2026-03-08:65618.0, 2026-03-23:67445.0, 2026-03-29:65000.0
- Transition: 2026-03-25 midline retest near 72026, then 2026-03-29 breakdown below lower bound, followed by 2026-04-01 retest near 69310 as resistance.

## Case Mapping (Case1~Case10)

### Case1
- parent_structure_id: `A`
- parent_structure_type: `major_descending_channel`
- period_start / period_end: `2024-08-05` -> `2024-09-06`
- narrative_summary: Part of the same lower-bound false-breakdown reclaim sequence in Parent A.
- correct_classification: `major_descending_channel_lower_boundary_false_breakdown_reclaim_cluster`
- invalid_local_rule_if_any: `ascending_channel_breakdown_retest_short`
- cluster_members: `Case1, Case2, Case3`
- whether_it_should_be_short_candidate: `False`
- reason: Liquidity sweep + reclaim at parent lower boundary, not valid bearish continuation breakdown.

### Case2
- parent_structure_id: `A`
- parent_structure_type: `major_descending_channel`
- period_start / period_end: `2024-08-05` -> `2024-09-06`
- narrative_summary: Merged into the same Parent A false-breakdown reclaim event as Case1/Case3.
- correct_classification: `major_descending_channel_lower_boundary_false_breakdown_reclaim_cluster`
- invalid_local_rule_if_any: `ascending_channel_breakdown_retest_short`
- cluster_members: `Case1, Case2, Case3`
- whether_it_should_be_short_candidate: `False`
- reason: Not an independent short near-miss; belongs to one merged reclaim cluster.

### Case3
- parent_structure_id: `A`
- parent_structure_type: `major_descending_channel`
- period_start / period_end: `2024-08-05` -> `2024-09-06`
- narrative_summary: Merged with Case1/Case2 as one event cluster within Parent A.
- correct_classification: `major_descending_channel_lower_boundary_false_breakdown_reclaim_cluster`
- invalid_local_rule_if_any: `ascending_channel_breakdown_retest_short`
- cluster_members: `Case1, Case2, Case3`
- whether_it_should_be_short_candidate: `False`
- reason: Parent lower-bound reclaim context invalidates short-breakdown interpretation.

### Case4
- parent_structure_id: `B`
- parent_structure_type: `major_descending_channel`
- period_start / period_end: `2025-02-28` -> `2025-04-08`
- narrative_summary: Price action occurred near major descending-channel lower boundary support.
- correct_classification: `major_descending_channel_lower_boundary_support_context`
- invalid_local_rule_if_any: `ascending_channel_breakdown_retest_short`
- cluster_members: `Case4`
- whether_it_should_be_short_candidate: `False`
- reason: Parent context conflict: lower-bound support zone is not a valid local bear-flag short trigger.

### Case5
- parent_structure_id: `D`
- parent_structure_type: `major_ascending_channel`
- period_start / period_end: `2025-08-31` -> `2025-09-27`
- narrative_summary: Part of the same major ascending-channel lower-bound support reaction sequence.
- correct_classification: `major_ascending_channel_lower_boundary_support_reaction_context`
- invalid_local_rule_if_any: `ascending_channel_breakdown_retest_short`
- cluster_members: `Case5, Case6, Case7`
- whether_it_should_be_short_candidate: `False`
- reason: Parent D pullback/support behavior, not valid breakdown-retest short.

### Case6
- parent_structure_id: `D`
- parent_structure_type: `major_ascending_channel`
- period_start / period_end: `2025-08-31` -> `2025-09-27`
- narrative_summary: Merged in the same Parent D lower-bound support reaction cluster.
- correct_classification: `major_ascending_channel_lower_boundary_support_reaction_context`
- invalid_local_rule_if_any: `ascending_channel_breakdown_retest_short`
- cluster_members: `Case5, Case6, Case7`
- whether_it_should_be_short_candidate: `False`
- reason: Not an independent short near-miss; same parent support reaction event.

### Case7
- parent_structure_id: `D`
- parent_structure_type: `major_ascending_channel`
- period_start / period_end: `2025-08-31` -> `2025-09-27`
- narrative_summary: Merged in the same Parent D lower-bound support reaction cluster.
- correct_classification: `major_ascending_channel_lower_boundary_support_reaction_context`
- invalid_local_rule_if_any: `ascending_channel_breakdown_retest_short`
- cluster_members: `Case5, Case6, Case7`
- whether_it_should_be_short_candidate: `False`
- reason: Parent major ascending channel context overrides local short interpretation.

### Case8
- parent_structure_id: `E`
- parent_structure_type: `black_swan_shock_liquidity_sweep_reclaim`
- period_start / period_end: `2025-10-10` -> `2025-11-20`
- narrative_summary: Black swan break below boundary followed by reclaim and stabilization.
- correct_classification: `shock_break_reclaim_context`
- invalid_local_rule_if_any: `ascending_channel_breakdown_retest_short`
- cluster_members: `Case8, Case9`
- whether_it_should_be_short_candidate: `False`
- reason: Shock reclaim override: not a standard confirmed breakdown window.

### Case9
- parent_structure_id: `E`
- parent_structure_type: `black_swan_shock_liquidity_sweep_reclaim`
- period_start / period_end: `2025-10-10` -> `2025-11-20`
- narrative_summary: Post-shock stabilization phase within the same shock/reclaim event cluster.
- correct_classification: `post_shock_stabilization_context`
- invalid_local_rule_if_any: `ascending_channel_breakdown_retest_short`
- cluster_members: `Case8, Case9`
- whether_it_should_be_short_candidate: `False`
- reason: Post-shock zone remains invalid for standard breakdown-retest short labeling.

### Case10
- parent_structure_id: `F+G`
- parent_structure_type: `bearish_impulse_then_current_ascending_rebound_channel_context`
- period_start / period_end: `2025-11-21` -> `2026-04-01`
- narrative_summary: Must inherit Parent F then Parent G; key live setup is 2026-03-29 breakdown then 2026-04-01 retest.
- correct_classification: `parent_FG_context_with_live_breakdown_retest_watch`
- invalid_local_rule_if_any: `None`
- cluster_members: `Case10`
- whether_it_should_be_short_candidate: `True`
- reason: Current closest valid short context is the 2026-03-29 breakdown and 2026-04-01 lower-bound retest as resistance; avoid isolated 2026-02-10-only framing.
