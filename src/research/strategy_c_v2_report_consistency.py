"""Strategy C v2 report consistency guard.

Enforces the rule that every return / DD / trades / PF / worst-trade
number cited in a Strategy C v2 report must be traceable to either:

1. A `CanonicalCell` record in
   `strategy_c_v2_canonical_baseline.CANONICAL_CELLS`, or
2. A specific row in a source CSV file.

How it works
============

Reports declare their numeric claims in a machine-readable block at
the top of the markdown file:

    <!-- canonical-metrics
    cell: D1_long_primary
    source: canonical
    oos_return: 1.4345
    max_dd: 0.1297
    num_trades: 73
    profit_factor: 2.23
    worst_trade_pnl: -0.0568
    -->

Or for research cells that are outside the Phase 8 deployment stack,
a CSV-rooted claim:

    <!-- canonical-metrics
    cell: d1_long_sl2_r2_L2
    source: csv
    csv_path: strategy_c_v2_phase5a_stop_loss_leverage.csv
    csv_filter: signal=rsi_only_20,sl_pct=0.02,stop_trigger=close,leverage=2
    oos_return: 1.15
    max_dd: 0.102
    num_trades: 70
    -->

The guard parses the block and cross-references every claim:
- For `source: canonical`, look up the cell in CANONICAL_CELLS and
  assert the value matches within float tolerance.
- For `source: csv`, load the CSV, filter rows by the filter spec,
  assert exactly one row matches, and assert the claim equals that
  row's value within tolerance.

In STRICT MODE (opt-in via `scan_body=True`), the guard also scans
the report BODY for any percentage number that resembles a cited
metric (patterns like `+143.45%` or `12.97%`) and verifies each
appears in the declared block. This catches narrative drift in
report formats that make formal claims in prose. In the default
mode (`scan_body=False`), only the declared blocks are validated,
which is appropriate for narrative-heavy documents (like the
baseline reconciliation report) that discuss historical numbers
such as the Phase 6 fabrication for context.

Tolerances
==========

Default tolerance is ±0.0001 for fractions, ±0.01 for percent
strings, ±0.01 for profit factors, 0 (exact) for trade counts.
Report authors can override per-metric via `tolerance:` fields in
the block.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from strategies.strategy_c_v2_canonical_baseline import (
    CANONICAL_CELLS,
    CanonicalCell,
    get_canonical_cell,
)


# ── metric block parsing ────────────────────────────────────────────


# Block delimiters — match the HTML-comment wrapper used in markdown
# to keep the block invisible when rendered.
_BLOCK_START_RE = re.compile(r"<!--\s*canonical-metrics\s*$", re.MULTILINE)
_BLOCK_END_RE = re.compile(r"^\s*-->\s*$", re.MULTILINE)

# Line format: `key: value` inside the block
_LINE_RE = re.compile(r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*(.*?)\s*$")

# Percentage number in the body — matches things like "+143.45%" or "12.97%"
_PERCENT_RE = re.compile(r"([+-]?\d+(?:\.\d+)?)\s*%")


# Metric name → which CanonicalMetrics field holds the ground truth
_CANONICAL_METRIC_FIELDS = {
    "oos_return": "oos_return",
    "compounded_return": "oos_return",   # alias
    "return": "oos_return",               # alias
    "max_dd": "max_dd",
    "dd": "max_dd",                        # alias
    "drawdown": "max_dd",                  # alias
    "num_trades": "num_trades",
    "trades": "num_trades",
    "profit_factor": "profit_factor",
    "pf": "profit_factor",
    "worst_trade_pnl": "worst_trade_pnl",
    "worst_trade": "worst_trade_pnl",
    "worst_adverse_move": "worst_adverse_move",
    "worst_adverse": "worst_adverse_move",
    "positive_windows": "positive_windows",
    "stops_fired": "stops_fired",
}


# Default tolerances by metric type
DEFAULT_TOLERANCES = {
    "oos_return": 1e-4,
    "max_dd": 1e-4,
    "num_trades": 0.0,          # exact match for integer counts
    "profit_factor": 1e-2,
    "worst_trade_pnl": 1e-4,
    "worst_adverse_move": 1e-4,
    "positive_windows": 0.0,    # exact
    "stops_fired": 0.0,
}


@dataclass(frozen=True)
class MetricClaim:
    """One numeric claim made by a report, parsed from the block."""
    cell_id: str
    metric: str                     # canonical metric name
    value: float
    source: str                     # "canonical" or "csv"
    csv_path: str | None = None
    csv_filter: dict[str, str] = field(default_factory=dict)
    tolerance: float | None = None


@dataclass(frozen=True)
class ConsistencyCheckResult:
    """Outcome of checking one report."""
    ok: bool
    report_path: str
    claims: tuple[MetricClaim, ...]
    errors: tuple[str, ...]

    def raise_if_failed(self) -> None:
        if not self.ok:
            joined = "\n".join(f"  - {e}" for e in self.errors)
            raise AssertionError(
                f"Report consistency check FAILED for {self.report_path}:\n{joined}"
            )


# ── parsing ─────────────────────────────────────────────────────────


def parse_metric_blocks(markdown_text: str) -> list[MetricClaim]:
    """Extract all `<!-- canonical-metrics ... -->` blocks.

    A report can have multiple blocks (one per cell it cites).

    Returns:
        A list of MetricClaim records, one per metric within each
        block. An empty list is valid — a report with no numeric
        claims doesn't need blocks.

    Raises:
        ValueError: if a block is malformed (unclosed, unknown
        metric key, non-numeric value, etc.).
    """
    claims: list[MetricClaim] = []
    pos = 0
    while True:
        start = _BLOCK_START_RE.search(markdown_text, pos)
        if not start:
            break
        end = _BLOCK_END_RE.search(markdown_text, start.end())
        if not end:
            raise ValueError(
                f"Unclosed canonical-metrics block at char {start.start()}"
            )

        block_body = markdown_text[start.end(): end.start()]
        block_entries: dict[str, str] = {}
        for line in block_body.splitlines():
            line = line.strip()
            if not line:
                continue
            m = _LINE_RE.match(line)
            if not m:
                raise ValueError(
                    f"Malformed metrics line in block starting at "
                    f"char {start.start()}: {line!r}"
                )
            key, val = m.group(1), m.group(2)
            block_entries[key] = val

        cell_id = block_entries.pop("cell", None)
        if cell_id is None:
            raise ValueError(
                f"canonical-metrics block at char {start.start()} "
                f"is missing required 'cell' field"
            )

        source = block_entries.pop("source", "canonical")
        csv_path = block_entries.pop("csv_path", None)
        csv_filter_raw = block_entries.pop("csv_filter", None)

        if source == "csv":
            if not csv_path:
                raise ValueError(
                    f"Block for cell {cell_id!r} has source=csv but no csv_path"
                )
            csv_filter: dict[str, str] = {}
            if csv_filter_raw:
                for pair in csv_filter_raw.split(","):
                    if "=" not in pair:
                        raise ValueError(
                            f"Malformed csv_filter entry {pair!r} "
                            f"(expected 'key=value')"
                        )
                    k, v = pair.split("=", 1)
                    csv_filter[k.strip()] = v.strip()
        elif source == "canonical":
            csv_filter = {}
            csv_path = None
        else:
            raise ValueError(
                f"Block for cell {cell_id!r} has unknown source {source!r} "
                f"(expected 'canonical' or 'csv')"
            )

        # Parse per-metric tolerance overrides (keys like "tolerance_oos_return")
        tol_overrides: dict[str, float] = {}
        for key in list(block_entries.keys()):
            if key.startswith("tolerance_"):
                metric_key = key[len("tolerance_"):]
                try:
                    tol_overrides[metric_key] = float(block_entries[key])
                except ValueError:
                    raise ValueError(
                        f"Block for cell {cell_id!r} has non-numeric "
                        f"tolerance value for {key!r}: {block_entries[key]!r}"
                    )
                del block_entries[key]

        # The remaining entries are metric claims.
        for key, val in block_entries.items():
            canonical_key = _CANONICAL_METRIC_FIELDS.get(key)
            if canonical_key is None:
                raise ValueError(
                    f"Block for cell {cell_id!r} has unknown metric {key!r}. "
                    f"Known metrics: {sorted(_CANONICAL_METRIC_FIELDS.keys())}"
                )
            try:
                value = float(val)
            except ValueError:
                raise ValueError(
                    f"Block for cell {cell_id!r} metric {key!r} has "
                    f"non-numeric value {val!r}"
                )
            tol = tol_overrides.get(key)
            claims.append(
                MetricClaim(
                    cell_id=cell_id,
                    metric=canonical_key,
                    value=value,
                    source=source,
                    csv_path=csv_path,
                    csv_filter=dict(csv_filter),
                    tolerance=tol,
                )
            )

        pos = end.end()

    return claims


# ── validation ──────────────────────────────────────────────────────


def _resolve_tolerance(metric: str, override: float | None) -> float:
    if override is not None:
        return override
    return DEFAULT_TOLERANCES.get(metric, 1e-4)


def validate_canonical_claim(claim: MetricClaim) -> list[str]:
    """Validate a single claim against CANONICAL_CELLS.

    Returns a list of error strings; empty list means the claim
    matches the canonical record.
    """
    errors: list[str] = []
    try:
        cell = get_canonical_cell(claim.cell_id)
    except KeyError as e:
        return [str(e)]

    canonical_value = getattr(cell.metrics, claim.metric, None)
    if canonical_value is None:
        errors.append(
            f"cell {claim.cell_id!r} canonical metric {claim.metric!r} "
            f"not found on CanonicalMetrics"
        )
        return errors

    tol = _resolve_tolerance(claim.metric, claim.tolerance)
    if abs(float(canonical_value) - claim.value) > tol:
        errors.append(
            f"cell {claim.cell_id!r} metric {claim.metric!r} claim "
            f"{claim.value} does not match canonical "
            f"{canonical_value} (tolerance {tol})"
        )
    return errors


def _load_csv_rows(csv_path: str) -> list[dict[str, str]]:
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")
    with path.open("r", newline="") as fh:
        reader = csv.DictReader(fh)
        return list(reader)


def _match_csv_row(
    rows: list[dict[str, str]],
    filter_spec: dict[str, str],
) -> list[dict[str, str]]:
    """Filter CSV rows by the supplied key=value spec."""
    if not filter_spec:
        return list(rows)
    matched: list[dict[str, str]] = []
    for row in rows:
        ok = True
        for k, v in filter_spec.items():
            cell = row.get(k)
            if cell is None:
                ok = False
                break
            # Try numeric comparison first (more robust), then string fallback
            try:
                if float(cell) != float(v):
                    ok = False
                    break
            except (ValueError, TypeError):
                if str(cell).strip() != str(v).strip():
                    ok = False
                    break
        if ok:
            matched.append(row)
    return matched


def validate_csv_claim(claim: MetricClaim) -> list[str]:
    """Validate a single claim against a CSV source row.

    Returns a list of error strings; empty means the claim matches.
    """
    errors: list[str] = []
    if claim.csv_path is None:
        return [f"cell {claim.cell_id!r} source=csv but csv_path is None"]
    try:
        rows = _load_csv_rows(claim.csv_path)
    except FileNotFoundError as e:
        return [str(e)]
    matched = _match_csv_row(rows, claim.csv_filter)
    if not matched:
        return [
            f"cell {claim.cell_id!r} csv filter {claim.csv_filter} "
            f"matched 0 rows in {claim.csv_path}"
        ]
    if len(matched) > 1:
        return [
            f"cell {claim.cell_id!r} csv filter {claim.csv_filter} "
            f"matched {len(matched)} rows in {claim.csv_path} "
            f"(expected exactly 1)"
        ]
    row = matched[0]
    # CSV column name mapping — accept both the canonical metric name
    # and common aliases used in the sweep CSVs.
    column_candidates = [
        claim.metric,
        {
            "oos_return": ["agg_compounded_return", "compounded_return"],
            "max_dd": ["combined_max_dd", "max_dd"],
            "num_trades": ["total_oos_trades", "num_trades"],
            "profit_factor": ["combined_profit_factor", "profit_factor"],
            "worst_trade_pnl": ["worst_trade_pnl"],
            "worst_adverse_move": ["worst_adverse_move"],
        }.get(claim.metric, []),
    ]
    # Flatten
    candidates: list[str] = []
    for c in column_candidates:
        if isinstance(c, str):
            candidates.append(c)
        elif isinstance(c, list):
            candidates.extend(c)

    row_value = None
    for col in candidates:
        if col in row:
            row_value = row[col]
            break
    if row_value is None:
        return [
            f"cell {claim.cell_id!r} csv row has no column for "
            f"metric {claim.metric!r} (tried {candidates})"
        ]
    try:
        csv_value = float(row_value)
    except ValueError:
        return [
            f"cell {claim.cell_id!r} csv row metric {claim.metric!r} "
            f"value {row_value!r} is not numeric"
        ]

    tol = _resolve_tolerance(claim.metric, claim.tolerance)
    if abs(csv_value - claim.value) > tol:
        errors.append(
            f"cell {claim.cell_id!r} metric {claim.metric!r} claim "
            f"{claim.value} does not match csv row {csv_value} "
            f"(tolerance {tol}, source {claim.csv_path})"
        )
    return errors


def validate_claim(claim: MetricClaim) -> list[str]:
    """Validate a single claim against its declared source."""
    if claim.source == "canonical":
        return validate_canonical_claim(claim)
    if claim.source == "csv":
        return validate_csv_claim(claim)
    return [f"cell {claim.cell_id!r} unknown source {claim.source!r}"]


# ── body scanning ───────────────────────────────────────────────────


def _extract_percentage_numbers(body_text: str) -> set[float]:
    """Extract every signed percentage in the body as a fraction.

    "+143.45%" → 1.4345
    "12.97%"   → 0.1297
    "-5.68%"   → -0.0568
    """
    out: set[float] = set()
    for m in _PERCENT_RE.finditer(body_text):
        try:
            pct = float(m.group(1))
        except ValueError:
            continue
        out.add(round(pct / 100.0, 6))
    return out


def _extract_block_values_as_fractions(claims: list[MetricClaim]) -> set[float]:
    """Build the set of all numeric values (as fractions) declared
    in the metrics blocks. The guard uses this to verify that every
    percentage in the body corresponds to a declared claim."""
    out: set[float] = set()
    for c in claims:
        if c.metric in ("oos_return", "max_dd", "worst_trade_pnl",
                        "worst_adverse_move"):
            out.add(round(c.value, 6))
    return out


def _strip_blocks(markdown_text: str) -> str:
    """Return the markdown with all canonical-metrics blocks removed."""
    out = []
    pos = 0
    while True:
        start = _BLOCK_START_RE.search(markdown_text, pos)
        if not start:
            out.append(markdown_text[pos:])
            break
        end = _BLOCK_END_RE.search(markdown_text, start.end())
        if not end:
            out.append(markdown_text[pos:])
            break
        out.append(markdown_text[pos:start.start()])
        pos = end.end()
    return "".join(out)


# ── public entrypoint ──────────────────────────────────────────────


def check_report(
    report_path: str | Path,
    *,
    scan_body: bool = False,
) -> ConsistencyCheckResult:
    """Run the full consistency check on one report.

    Args:
        report_path: Path to the markdown report.
        scan_body: If True (strict mode), scan the body text for
            percentages that look like metric claims and verify each
            appears in the declared blocks. Default False — only the
            declared blocks are validated, which is appropriate for
            narrative-heavy reports that discuss historical numbers
            (like the Phase 6 fabrication) for context.

    Returns:
        ConsistencyCheckResult.ok is True iff every declared claim
        validates (and in strict mode, every body percentage is
        traceable).
    """
    path = Path(report_path)
    if not path.exists():
        return ConsistencyCheckResult(
            ok=False,
            report_path=str(path),
            claims=(),
            errors=(f"Report file not found: {path}",),
        )
    text = path.read_text(encoding="utf-8")

    try:
        claims = parse_metric_blocks(text)
    except ValueError as e:
        return ConsistencyCheckResult(
            ok=False,
            report_path=str(path),
            claims=(),
            errors=(f"Malformed metrics block: {e}",),
        )

    errors: list[str] = []

    # 1. Validate each declared claim against its source
    for claim in claims:
        errs = validate_claim(claim)
        errors.extend(errs)

    # 2. Body scan: every percentage in the body must match some
    # declared claim (within tolerance). This catches narrative
    # numbers that drift from the declared block.
    if scan_body:
        declared = _extract_block_values_as_fractions(claims)
        body = _strip_blocks(text)
        body_values = _extract_percentage_numbers(body)
        body_errors = _body_tolerance_scan(body_values, declared)
        errors.extend(body_errors)

    return ConsistencyCheckResult(
        ok=(len(errors) == 0),
        report_path=str(path),
        claims=tuple(claims),
        errors=tuple(errors),
    )


def _body_tolerance_scan(
    body_values: set[float],
    declared: set[float],
    *,
    tolerance: float = 1e-4,
) -> list[str]:
    """For each body value, check it's within tolerance of a declared value."""
    errors: list[str] = []
    if not declared:
        # If the report declares nothing, body scanning is meaningless.
        return errors
    for bv in sorted(body_values):
        matched = any(abs(bv - dv) <= tolerance for dv in declared)
        if not matched:
            errors.append(
                f"body percentage {bv * 100:+.2f}% (fraction {bv}) "
                f"is not traceable to any declared canonical-metrics "
                f"claim"
            )
    return errors
