"""핵심 패턴 (FR-P1~P4, A7). 기본 수치는 config 의 patterns 절, 조합별 수치는 pattern_limits 범위 안에서만 (P1-4)."""

from regime_lab.patterns.base import Pattern, combine_signals, compute_signals, make_pattern
from regime_lab.patterns.core import CORE_PATTERNS, get_pattern

__all__ = ["Pattern", "CORE_PATTERNS", "get_pattern", "make_pattern", "combine_signals", "compute_signals"]
