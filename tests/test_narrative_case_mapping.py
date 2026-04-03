from research.narrative_case_mapping import (
    build_case_mapping,
    build_parent_structure_timeline,
    build_report_payload,
    render_report_markdown,
)


def _case_lookup():
    return {case.case_id: case for case in build_case_mapping()}


def test_case1_to_case3_are_one_cluster_not_independent_short_near_miss() -> None:
    cases = _case_lookup()
    for case_id in ("Case1", "Case2", "Case3"):
        case = cases[case_id]
        assert case.cluster_members == ["Case1", "Case2", "Case3"]
        assert case.correct_classification == "major_descending_channel_lower_boundary_false_breakdown_reclaim_cluster"
        assert case.invalid_local_rule_if_any == "ascending_channel_breakdown_retest_short"
        assert case.whether_it_should_be_short_candidate is False


def test_case4_is_not_ascending_channel_breakdown_retest_short_candidate() -> None:
    case = _case_lookup()["Case4"]
    assert case.correct_classification == "major_descending_channel_lower_boundary_support_context"
    assert case.invalid_local_rule_if_any == "ascending_channel_breakdown_retest_short"
    assert case.whether_it_should_be_short_candidate is False


def test_case5_to_case7_are_not_short_near_miss() -> None:
    cases = _case_lookup()
    for case_id in ("Case5", "Case6", "Case7"):
        case = cases[case_id]
        assert case.cluster_members == ["Case5", "Case6", "Case7"]
        assert case.correct_classification == "major_ascending_channel_lower_boundary_support_reaction_context"
        assert case.whether_it_should_be_short_candidate is False


def test_case8_to_case9_are_shock_reclaim_context() -> None:
    cases = _case_lookup()
    assert cases["Case8"].correct_classification == "shock_break_reclaim_context"
    assert cases["Case9"].correct_classification == "post_shock_stabilization_context"
    assert cases["Case8"].cluster_members == ["Case8", "Case9"]
    assert cases["Case9"].cluster_members == ["Case8", "Case9"]
    assert cases["Case8"].whether_it_should_be_short_candidate is False
    assert cases["Case9"].whether_it_should_be_short_candidate is False


def test_case10_must_link_parent_f_plus_g_not_isolated() -> None:
    case = _case_lookup()["Case10"]
    assert case.parent_structure_id == "F+G"
    assert case.period_start == "2025-11-21"
    assert case.period_end == "2026-04-01"
    assert "2026-03-29 breakdown" in case.reason
    assert "2026-04-01" in case.reason
    assert case.whether_it_should_be_short_candidate is True


def test_parent_timeline_includes_a_to_g() -> None:
    timeline = build_parent_structure_timeline()
    assert [item.structure_id for item in timeline] == ["A", "B", "C", "D", "E", "F", "G"]


def test_report_payload_and_markdown_have_required_sections() -> None:
    payload = build_report_payload()
    assert "parent_structure_timeline" in payload
    assert "case_mapping_report" in payload
    assert len(payload["case_mapping_report"]) == 10
    markdown = render_report_markdown(payload)
    assert "Parent Structure Timeline (A~G)" in markdown
    assert "Case Mapping (Case1~Case10)" in markdown
