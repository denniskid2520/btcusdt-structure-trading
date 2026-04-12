"""Tests for the report consistency guard.

These tests cover:
  1. Block parsing — correct extraction of metric claims from the
     `<!-- canonical-metrics ... -->` block
  2. Canonical validation — claims cross-checked against CANONICAL_CELLS
  3. CSV validation — claims cross-checked against CSV row filters
  4. Body scanning — percentages in the body must match declared claims
  5. Error reporting — malformed blocks / mismatched values fail cleanly
  6. The real Phase 8 canonical baseline report — must pass when
     properly annotated with the machine-readable block

The last test is the critical "self-check" — the report we just
wrote in Gate 1 must be clean under the guard. If it's not, either
the guard has a bug or the report has a fabricated number.
"""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from research.strategy_c_v2_report_consistency import (
    MetricClaim,
    check_report,
    parse_metric_blocks,
    validate_canonical_claim,
    validate_claim,
    validate_csv_claim,
)


# ── block parsing ───────────────────────────────────────────────────


def test_parse_empty_report_returns_no_claims() -> None:
    assert parse_metric_blocks("# Hello world\n\nNothing to see.") == []


def test_parse_one_canonical_block() -> None:
    md = """
# My report

<!-- canonical-metrics
cell: D1_long_primary
source: canonical
oos_return: 1.4345
max_dd: 0.1297
num_trades: 73
-->

Some prose follows.
"""
    claims = parse_metric_blocks(md)
    assert len(claims) == 3
    cell_ids = {c.cell_id for c in claims}
    assert cell_ids == {"D1_long_primary"}
    metrics = {c.metric: c.value for c in claims}
    assert metrics["oos_return"] == 1.4345
    assert metrics["max_dd"] == 0.1297
    assert metrics["num_trades"] == 73.0


def test_parse_two_blocks() -> None:
    md = """
<!-- canonical-metrics
cell: D1_long_primary
source: canonical
oos_return: 1.4345
-->

Text.

<!-- canonical-metrics
cell: C_long_backup
source: canonical
oos_return: 1.0626
num_trades: 178
-->
"""
    claims = parse_metric_blocks(md)
    by_cell: dict[str, list[MetricClaim]] = {}
    for c in claims:
        by_cell.setdefault(c.cell_id, []).append(c)
    assert "D1_long_primary" in by_cell
    assert "C_long_backup" in by_cell
    assert len(by_cell["C_long_backup"]) == 2


def test_parse_metric_aliases() -> None:
    md = """
<!-- canonical-metrics
cell: D1_long_primary
source: canonical
return: 1.4345
dd: 0.1297
pf: 2.23
trades: 73
-->
"""
    claims = parse_metric_blocks(md)
    metrics = {c.metric for c in claims}
    assert metrics == {
        "oos_return",
        "max_dd",
        "profit_factor",
        "num_trades",
    }


def test_parse_rejects_unclosed_block() -> None:
    md = """
<!-- canonical-metrics
cell: D1_long_primary
source: canonical
oos_return: 1.4345
"""
    with pytest.raises(ValueError, match="Unclosed"):
        parse_metric_blocks(md)


def test_parse_rejects_missing_cell_field() -> None:
    md = """
<!-- canonical-metrics
source: canonical
oos_return: 1.4345
-->
"""
    with pytest.raises(ValueError, match="missing required 'cell'"):
        parse_metric_blocks(md)


def test_parse_rejects_unknown_metric_key() -> None:
    md = """
<!-- canonical-metrics
cell: D1_long_primary
source: canonical
bogus_metric: 123
-->
"""
    with pytest.raises(ValueError, match="unknown metric"):
        parse_metric_blocks(md)


def test_parse_rejects_non_numeric_value() -> None:
    md = """
<!-- canonical-metrics
cell: D1_long_primary
source: canonical
oos_return: not_a_number
-->
"""
    with pytest.raises(ValueError, match="non-numeric"):
        parse_metric_blocks(md)


def test_parse_rejects_unknown_source() -> None:
    md = """
<!-- canonical-metrics
cell: D1_long_primary
source: mystery
oos_return: 1.4345
-->
"""
    with pytest.raises(ValueError, match="unknown source"):
        parse_metric_blocks(md)


def test_parse_csv_source_requires_csv_path() -> None:
    md = """
<!-- canonical-metrics
cell: research_cell
source: csv
oos_return: 1.15
-->
"""
    with pytest.raises(ValueError, match="no csv_path"):
        parse_metric_blocks(md)


def test_parse_csv_filter_entries() -> None:
    md = """
<!-- canonical-metrics
cell: research_cell
source: csv
csv_path: some/path.csv
csv_filter: signal=rsi_only_20,sl_pct=0.02
oos_return: 1.15
-->
"""
    claims = parse_metric_blocks(md)
    assert len(claims) == 1
    c = claims[0]
    assert c.source == "csv"
    assert c.csv_filter == {"signal": "rsi_only_20", "sl_pct": "0.02"}


def test_parse_tolerance_override() -> None:
    md = """
<!-- canonical-metrics
cell: D1_long_primary
source: canonical
oos_return: 1.4345
tolerance_oos_return: 0.01
-->
"""
    claims = parse_metric_blocks(md)
    assert len(claims) == 1
    assert claims[0].tolerance == 0.01


# ── canonical validation ───────────────────────────────────────────


def test_canonical_claim_matches_ground_truth() -> None:
    claim = MetricClaim(
        cell_id="D1_long_primary",
        metric="oos_return",
        value=1.4345,
        source="canonical",
    )
    errors = validate_canonical_claim(claim)
    assert errors == []


def test_canonical_claim_detects_wrong_value() -> None:
    """Simulates the Phase 6 fabrication: claim +173.06% on D1_long_primary."""
    claim = MetricClaim(
        cell_id="D1_long_primary",
        metric="oos_return",
        value=1.7306,
        source="canonical",
    )
    errors = validate_canonical_claim(claim)
    assert len(errors) == 1
    assert "does not match canonical" in errors[0]
    assert "1.7306" in errors[0]
    assert "1.4345" in errors[0]


def test_canonical_claim_detects_wrong_dd() -> None:
    claim = MetricClaim(
        cell_id="D1_long_primary",
        metric="max_dd",
        value=0.0927,
        source="canonical",
    )
    errors = validate_canonical_claim(claim)
    assert len(errors) == 1
    assert "does not match" in errors[0]


def test_canonical_claim_unknown_cell_id() -> None:
    claim = MetricClaim(
        cell_id="bogus",
        metric="oos_return",
        value=1.0,
        source="canonical",
    )
    errors = validate_canonical_claim(claim)
    assert len(errors) == 1
    assert "Unknown canonical cell" in errors[0]


def test_canonical_claim_within_default_tolerance() -> None:
    """A claim 1 bp off canonical still passes with default tolerance."""
    claim = MetricClaim(
        cell_id="D1_long_primary",
        metric="oos_return",
        value=1.4345 + 9e-5,   # inside 1e-4 tolerance
        source="canonical",
    )
    errors = validate_canonical_claim(claim)
    assert errors == []


def test_canonical_claim_outside_default_tolerance() -> None:
    claim = MetricClaim(
        cell_id="D1_long_primary",
        metric="oos_return",
        value=1.4345 + 2e-4,   # outside 1e-4 tolerance
        source="canonical",
    )
    errors = validate_canonical_claim(claim)
    assert len(errors) == 1


def test_canonical_trade_count_exact_match_required() -> None:
    claim = MetricClaim(
        cell_id="D1_long_primary",
        metric="num_trades",
        value=74.0,     # 1 off — unacceptable for trade count
        source="canonical",
    )
    errors = validate_canonical_claim(claim)
    assert len(errors) == 1


# ── CSV validation ─────────────────────────────────────────────────


@pytest.fixture
def tmp_csv(tmp_path: Path) -> Path:
    csv_path = tmp_path / "sweep.csv"
    with csv_path.open("w", newline="") as fh:
        w = csv.DictWriter(
            fh,
            fieldnames=[
                "signal",
                "sl_pct",
                "stop_trigger",
                "leverage",
                "agg_compounded_return",
                "combined_max_dd",
                "total_oos_trades",
            ],
        )
        w.writeheader()
        w.writerow({
            "signal": "rsi_only_20",
            "sl_pct": "0.02",
            "stop_trigger": "close",
            "leverage": "2",
            "agg_compounded_return": "1.15",
            "combined_max_dd": "0.102",
            "total_oos_trades": "70",
        })
        w.writerow({
            "signal": "rsi_only_20",
            "sl_pct": "0.015",
            "stop_trigger": "close",
            "leverage": "2",
            "agg_compounded_return": "1.43",
            "combined_max_dd": "0.129",
            "total_oos_trades": "73",
        })
    return csv_path


def test_csv_claim_matches_row(tmp_csv: Path) -> None:
    claim = MetricClaim(
        cell_id="research_cell",
        metric="oos_return",
        value=1.15,
        source="csv",
        csv_path=str(tmp_csv),
        csv_filter={"sl_pct": "0.02", "stop_trigger": "close"},
    )
    errors = validate_csv_claim(claim)
    assert errors == []


def test_csv_claim_detects_wrong_value(tmp_csv: Path) -> None:
    claim = MetricClaim(
        cell_id="research_cell",
        metric="oos_return",
        value=2.00,  # wrong
        source="csv",
        csv_path=str(tmp_csv),
        csv_filter={"sl_pct": "0.02", "stop_trigger": "close"},
    )
    errors = validate_csv_claim(claim)
    assert len(errors) == 1


def test_csv_claim_filter_matches_zero_rows(tmp_csv: Path) -> None:
    claim = MetricClaim(
        cell_id="research_cell",
        metric="oos_return",
        value=1.15,
        source="csv",
        csv_path=str(tmp_csv),
        csv_filter={"sl_pct": "0.999"},
    )
    errors = validate_csv_claim(claim)
    assert len(errors) == 1
    assert "matched 0 rows" in errors[0]


def test_csv_claim_filter_matches_multiple_rows_errors(tmp_csv: Path) -> None:
    claim = MetricClaim(
        cell_id="research_cell",
        metric="oos_return",
        value=1.15,
        source="csv",
        csv_path=str(tmp_csv),
        csv_filter={"signal": "rsi_only_20"},  # matches 2 rows
    )
    errors = validate_csv_claim(claim)
    assert len(errors) == 1
    assert "matched 2 rows" in errors[0]


def test_csv_claim_missing_file() -> None:
    claim = MetricClaim(
        cell_id="research_cell",
        metric="oos_return",
        value=1.0,
        source="csv",
        csv_path="does_not_exist.csv",
    )
    errors = validate_csv_claim(claim)
    assert len(errors) == 1
    assert "not found" in errors[0]


# ── end-to-end report check ────────────────────────────────────────


def test_check_report_all_canonical_clean(tmp_path: Path) -> None:
    md = """
# My Phase 8 report

<!-- canonical-metrics
cell: D1_long_primary
source: canonical
oos_return: 1.4345
max_dd: 0.1297
num_trades: 73
profit_factor: 2.23
worst_trade_pnl: -0.0568
-->

D1_long_primary achieved +143.45% OOS with 12.97% max drawdown
on 73 trades, profit factor 2.23, worst trade -5.68%.
"""
    path = tmp_path / "report.md"
    path.write_text(md, encoding="utf-8")
    result = check_report(path, scan_body=True)
    assert result.ok, f"Errors: {result.errors}"
    assert len(result.claims) == 5


def test_check_report_fabricated_number_fails(tmp_path: Path) -> None:
    """Simulates the Phase 6 fabrication — recover that error."""
    md = """
<!-- canonical-metrics
cell: D1_long_primary
source: canonical
oos_return: 1.7306
max_dd: 0.0927
-->

D1_long_primary: +173.06% / 9.27% DD
"""
    path = tmp_path / "fabricated.md"
    path.write_text(md, encoding="utf-8")
    result = check_report(path)
    assert not result.ok
    assert any("1.7306" in e for e in result.errors)
    assert any("0.0927" in e for e in result.errors)


def test_check_report_body_drift_fails_in_strict_mode(tmp_path: Path) -> None:
    """Block is correct but body narrative has a drifted number.

    In strict mode (scan_body=True), the drift is caught. This is
    the mode recommendation reports use to prevent fabrications.
    """
    md = """
<!-- canonical-metrics
cell: D1_long_primary
source: canonical
oos_return: 1.4345
max_dd: 0.1297
-->

The cell achieved +165.50% OOS.  # ← drift!
"""
    path = tmp_path / "drift.md"
    path.write_text(md, encoding="utf-8")
    result = check_report(path, scan_body=True)
    assert not result.ok
    assert any("165.50%" in e or "1.655" in e for e in result.errors)


def test_check_report_body_drift_ignored_in_default_mode(tmp_path: Path) -> None:
    """In the default (narrative-safe) mode, body drift is allowed
    so the report can cite historical numbers (like the Phase 6
    fabrication) without the guard over-firing.

    Block validation still runs — this test verifies the default
    mode catches block errors but NOT body drift.
    """
    md = """
<!-- canonical-metrics
cell: D1_long_primary
source: canonical
oos_return: 1.4345
-->

Historical note: Phase 6 claimed +173.06% but that was fabricated.
"""
    path = tmp_path / "narrative.md"
    path.write_text(md, encoding="utf-8")
    result = check_report(path)  # default scan_body=False
    assert result.ok


def test_check_report_body_scan_strict_matches_declared_values(
    tmp_path: Path,
) -> None:
    """In strict mode, every declared value must appear in the body."""
    md = """
<!-- canonical-metrics
cell: D1_long_primary
source: canonical
oos_return: 1.4345
max_dd: 0.1297
worst_trade_pnl: -0.0568
-->

D1_long_primary: +143.45% OOS, 12.97% max DD, -5.68% worst trade.
"""
    path = tmp_path / "clean.md"
    path.write_text(md, encoding="utf-8")
    result = check_report(path, scan_body=True)
    assert result.ok, f"Errors: {result.errors}"


def test_check_report_default_skips_body_scan(tmp_path: Path) -> None:
    """Default mode only checks the block."""
    md = """
<!-- canonical-metrics
cell: D1_long_primary
source: canonical
oos_return: 1.4345
-->

The cell achieved some unrelated 5% return on a different day.
"""
    path = tmp_path / "skip_body.md"
    path.write_text(md, encoding="utf-8")
    result = check_report(path)  # default scan_body=False
    assert result.ok


def test_check_report_missing_file() -> None:
    result = check_report("does_not_exist.md")
    assert not result.ok
    assert "not found" in result.errors[0]


def test_check_report_result_raise_if_failed() -> None:
    md = """
<!-- canonical-metrics
cell: D1_long_primary
source: canonical
oos_return: 1.7306
-->
"""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "bad.md"
        path.write_text(md, encoding="utf-8")
        result = check_report(path, scan_body=False)
        assert not result.ok
        with pytest.raises(AssertionError, match="FAILED"):
            result.raise_if_failed()


# ── end-to-end: real Phase 8 baseline report ───────────────────────


def test_real_phase8_baseline_report_passes_consistency_check() -> None:
    """CRITICAL: the Phase 8 canonical baseline report we just wrote
    in Gate 1 must pass the guard.

    This is the self-check that the guard machinery actually works
    on the real document. If this test ever fails, either:
      (a) the guard has a regression
      (b) the baseline report drifted from CANONICAL_CELLS
      (c) a new number was added to the body without updating the
          declared block
    """
    report = Path("strategy_c_v2_phase8_canonical_baseline.md")
    if not report.exists():
        pytest.skip(f"{report} not present in this worktree")
    result = check_report(report)
    # Format errors nicely so failure is easy to debug
    msg = "Phase 8 baseline report failed consistency check:\n" + "\n".join(
        f"  - {e}" for e in result.errors
    )
    assert result.ok, msg
