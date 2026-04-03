"""Tests for parent structures, case mapping, and live-context validation."""

from __future__ import annotations

from datetime import date

from context.case_mapping import CASE_MAPPINGS, get_case
from context.parent_structures import (
    PARENT_A,
    PARENT_F,
    PARENT_G,
    PARENT_TIMELINE,
    get_parent_by_name,
)
from context.live_setup_validator import (
    check_breakdown_event,
    check_parent_f_bearish_impulse,
    check_parent_g_ascending_channel,
    check_parent_g_channel_boundaries,
    check_retest_event,
    check_retest_failure_confirmed,
    validate_case10_live_setup,
)
from context.generate_reports import (
    generate_narrative_case_mapping_report,
    generate_truth_vs_strategy_comparison_report,
)


# ── Parent structure data integrity ──────────────────────────────────

def test_parent_timeline_has_seven_entries():
    assert len(PARENT_TIMELINE) == 7


def test_parent_timeline_names():
    names = [p.name for p in PARENT_TIMELINE]
    assert names == [
        "Parent_A", "Parent_B", "Parent_C", "Parent_D",
        "Parent_E", "Parent_F", "Parent_G",
    ]


def test_get_parent_by_name():
    assert get_parent_by_name("Parent_A") is PARENT_A
    assert get_parent_by_name("Parent_G") is PARENT_G
    assert get_parent_by_name("nonexistent") is None


def test_parent_g_has_breakdown_low():
    breakdown_lows = [
        kl for kl in PARENT_G.key_lows if kl.label == "breakdown_below_lower_bound"
    ]
    assert len(breakdown_lows) == 1
    assert breakdown_lows[0].price == 65000
    assert breakdown_lows[0].date == date(2026, 3, 29)


# ── Case mapping integrity ──────────────────────────────────────────

def test_case_mapping_has_ten_entries():
    assert len(CASE_MAPPINGS) == 10


def test_only_case10_is_valid_for_retest_short():
    valid = [c for c in CASE_MAPPINGS if c.valid_as_ascending_breakdown_retest_short]
    assert len(valid) == 1
    assert valid[0].case_id == "Case10"


def test_case10_inherits_parent_f_and_g():
    case10 = get_case("Case10")
    assert case10 is not None
    assert "Parent_F" in case10.parent_names
    assert "Parent_G" in case10.parent_names


# ── Live-context conditions ──────────────────────────────────────────

def test_parent_f_bearish_impulse_passes():
    result = check_parent_f_bearish_impulse()
    assert result.passed is True


def test_parent_g_ascending_channel_passes():
    result = check_parent_g_ascending_channel()
    assert result.passed is True


def test_parent_g_channel_slopes_rising():
    result, bounds = check_parent_g_channel_boundaries()
    assert result.passed is True
    assert bounds["upper_slope"] > 0
    assert bounds["lower_slope"] > 0


def test_breakdown_below_lower_bound():
    _, bounds = check_parent_g_channel_boundaries()
    result = check_breakdown_event(bounds)
    assert result.passed is True


def test_retest_near_lower_bound():
    _, bounds = check_parent_g_channel_boundaries()
    result = check_retest_event(bounds)
    assert result.passed is True


def test_retest_failure_confirmed_when_price_below():
    _, bounds = check_parent_g_channel_boundaries()
    result = check_retest_failure_confirmed(67500.0, date(2026, 4, 3), bounds)
    assert result.passed is True


def test_retest_failure_not_confirmed_when_price_above():
    _, bounds = check_parent_g_channel_boundaries()
    result = check_retest_failure_confirmed(69500.0, date(2026, 4, 3), bounds)
    assert result.passed is False


def test_retest_failure_not_confirmed_without_price():
    _, bounds = check_parent_g_channel_boundaries()
    result = check_retest_failure_confirmed(None, None, bounds)
    assert result.passed is False


# ── Full report ──────────────────────────────────────────────────────

def test_full_report_valid_at_67500():
    report = validate_case10_live_setup(
        current_price=67500.0,
        current_date=date(2026, 4, 3),
    )
    assert report["trade_valid"] is True
    assert report["all_conditions_met"] is True
    tp = report["trade_plan"]
    assert tp["side"] == "short"
    assert tp["entry"] > 0
    assert tp["stop"] > tp["entry"]
    assert tp["target_1"] < tp["entry"]
    assert tp["target_2"] < tp["target_1"]


def test_full_report_invalid_at_69500():
    report = validate_case10_live_setup(
        current_price=69500.0,
        current_date=date(2026, 4, 3),
    )
    assert report["trade_valid"] is False
    assert report["trade_plan"] is None
    assert len(report["blocking_conditions"]) > 0


def test_full_report_contains_required_sections():
    report = validate_case10_live_setup(
        current_price=67500.0,
        current_date=date(2026, 4, 3),
    )
    assert "parent_structure_summary" in report
    assert "local_structure_summary" in report
    assert "channel_boundaries" in report
    assert "conditions" in report
    assert report["case_id"] == "Case10"


# ── Report generators ────────────────────────────────────────────────

def test_narrative_case_mapping_report_structure():
    report = generate_narrative_case_mapping_report()
    assert report["report_type"] == "narrative_case_mapping_report"
    assert len(report["parent_timeline"]) == 7
    assert len(report["case_mappings"]) == 10
    assert report["summary"]["valid_for_retest_short"] == 1


def test_truth_vs_strategy_comparison_report_structure():
    report = generate_truth_vs_strategy_comparison_report()
    assert report["report_type"] == "truth_vs_strategy_comparison_report"
    assert len(report["comparisons"]) == 10
    # Case10 should agree (both True)
    case10_row = next(r for r in report["comparisons"] if r["case_id"] == "Case10")
    assert case10_row["truth_valid"] is True
    assert case10_row["agrees_with_truth"] is True
