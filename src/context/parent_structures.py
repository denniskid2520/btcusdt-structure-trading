"""Parent structure timeline definitions for BTCUSDT.

Each parent structure represents a major market phase identified via
narrative truth analysis on the 1D timeframe.  These are fixed reference
data — not auto-detected — and serve as the source of truth for
context-aware validation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class KeyLevel:
    date: date
    price: float
    label: str = ""


@dataclass(frozen=True)
class Transition:
    date: date
    description: str


@dataclass(frozen=True)
class ParentStructure:
    name: str
    period_start: date
    period_end: date
    structure_type: str
    key_highs: list[KeyLevel] = field(default_factory=list)
    key_lows: list[KeyLevel] = field(default_factory=list)
    transitions: list[Transition] = field(default_factory=list)
    notes: str = ""


# ── Parent A ─────────────────────────────────────────────────────────
PARENT_A = ParentStructure(
    name="Parent_A",
    period_start=date(2024, 3, 13),
    period_end=date(2024, 10, 21),
    structure_type="major_descending_channel",
    key_highs=[
        KeyLevel(date(2024, 3, 13), 73650),
        KeyLevel(date(2024, 5, 21), 71979),
        KeyLevel(date(2024, 7, 29), 70079),
    ],
    key_lows=[
        KeyLevel(date(2024, 5, 1), 56552),
        KeyLevel(date(2024, 7, 5), 53485),
        KeyLevel(date(2024, 8, 5), 49000, "liquidity_sweep_false_breakdown"),
        KeyLevel(date(2024, 9, 6), 52550),
    ],
    transitions=[
        Transition(date(2024, 8, 5), "lower-bound liquidity sweep / false breakdown reclaim"),
        Transition(date(2024, 10, 21), "upside breakout"),
        Transition(date(2024, 11, 4), "retest success"),
    ],
)

# ── Parent B ─────────────────────────────────────────────────────────
PARENT_B = ParentStructure(
    name="Parent_B",
    period_start=date(2024, 11, 14),
    period_end=date(2025, 5, 2),
    structure_type="major_descending_channel",
    key_highs=[
        KeyLevel(date(2024, 12, 17), 108353),
        KeyLevel(date(2025, 1, 22), 106394),
    ],
    key_lows=[
        KeyLevel(date(2025, 2, 28), 78258),
        KeyLevel(date(2025, 3, 11), 78595),
        KeyLevel(date(2025, 4, 8), 76239),
    ],
    transitions=[
        Transition(date(2025, 5, 2), "breakout above major descending channel"),
    ],
    notes="breakout followed by consolidation / retest / support hold before next leg up",
)

# ── Parent C ─────────────────────────────────────────────────────────
PARENT_C = ParentStructure(
    name="Parent_C",
    period_start=date(2025, 5, 8),
    period_end=date(2025, 7, 4),
    structure_type="local_descending_channel_inside_bullish_transition",
    key_highs=[
        KeyLevel(date(2025, 5, 22), 111980),
        KeyLevel(date(2025, 6, 10), 110400),
        KeyLevel(date(2025, 6, 30), 0, "upper_boundary_retest"),
    ],
    key_lows=[
        KeyLevel(date(2025, 6, 5), 100372),
        KeyLevel(date(2025, 6, 22), 98200),
    ],
    transitions=[
        Transition(date(2025, 7, 4), "bull-flag style breakout, retest success"),
    ],
)

# ── Parent D ─────────────────────────────────────────────────────────
PARENT_D = ParentStructure(
    name="Parent_D",
    period_start=date(2025, 7, 5),
    period_end=date(2025, 10, 10),
    structure_type="major_ascending_channel",
    key_highs=[
        KeyLevel(date(2025, 7, 14), 123218),
        KeyLevel(date(2025, 8, 14), 124474),
        KeyLevel(date(2025, 10, 6), 126199),
    ],
    key_lows=[
        KeyLevel(date(2025, 8, 31), 108076),
        KeyLevel(date(2025, 9, 27), 109064),
    ],
    transitions=[
        Transition(date(2025, 10, 10), "black swan shock starts after this structure"),
    ],
)

# ── Parent E ─────────────────────────────────────────────────────────
PARENT_E = ParentStructure(
    name="Parent_E",
    period_start=date(2025, 10, 10),
    period_end=date(2025, 11, 20),
    structure_type="black_swan_shock_liquidity_sweep_reclaim",
    key_highs=[
        KeyLevel(date(2025, 10, 10), 122550),
    ],
    key_lows=[
        KeyLevel(date(2025, 10, 10), 102000, "shock_low"),
    ],
    transitions=[
        Transition(date(2025, 10, 10), "pierce below major ascending-channel lower bound then reclaim"),
        Transition(date(2025, 11, 20), "stabilization around ~106k then clean bearish breakdown"),
    ],
)

# ── Parent F ─────────────────────────────────────────────────────────
PARENT_F = ParentStructure(
    name="Parent_F",
    period_start=date(2025, 11, 21),
    period_end=date(2026, 1, 29),
    structure_type="bearish_impulse_then_ascending_rebound_channel",
    key_highs=[
        KeyLevel(date(2025, 11, 28), 93092),
        KeyLevel(date(2025, 12, 3), 94150),
        KeyLevel(date(2026, 1, 14), 97924),
    ],
    key_lows=[
        KeyLevel(date(2025, 11, 21), 80600),
        KeyLevel(date(2025, 12, 1), 83822),
        KeyLevel(date(2026, 1, 20), 87263),
    ],
    transitions=[
        Transition(date(2026, 1, 29), "downside breakdown"),
        Transition(date(2026, 2, 6), "selloff into low near 60000"),
    ],
)

# ── Parent G ─────────────────────────────────────────────────────────
PARENT_G = ParentStructure(
    name="Parent_G",
    period_start=date(2026, 2, 6),
    period_end=date(2026, 4, 1),
    structure_type="current_ascending_rebound_channel_after_crash",
    key_highs=[
        KeyLevel(date(2026, 2, 9), 71453),
        KeyLevel(date(2026, 3, 4), 74050),
        KeyLevel(date(2026, 3, 17), 76000),
    ],
    key_lows=[
        KeyLevel(date(2026, 2, 6), 60000),
        KeyLevel(date(2026, 2, 25), 63913),
        KeyLevel(date(2026, 3, 8), 65618),
        KeyLevel(date(2026, 3, 23), 67445),
        KeyLevel(date(2026, 3, 29), 65000, "breakdown_below_lower_bound"),
    ],
    transitions=[
        Transition(date(2026, 3, 29), "breakdown below lower bound"),
        Transition(date(2026, 4, 1), "retest near 69310 as resistance"),
    ],
    notes="midline retest 2026-03-25 at 72026",
)


# ── Registry ─────────────────────────────────────────────────────────
PARENT_TIMELINE: list[ParentStructure] = [
    PARENT_A,
    PARENT_B,
    PARENT_C,
    PARENT_D,
    PARENT_E,
    PARENT_F,
    PARENT_G,
]


def get_parent_by_name(name: str) -> ParentStructure | None:
    for parent in PARENT_TIMELINE:
        if parent.name == name:
            return parent
    return None
