"""핵심 패턴 5종 (FR-P1~P4, A7). 파라미터는 config 의 patterns 절에서만 읽는다 (UI 변경 불가)."""

from regime_lab.patterns.base import Pattern, combine_signals, compute_signals
from regime_lab.patterns.core import CORE_PATTERNS, get_pattern

__all__ = ["Pattern", "CORE_PATTERNS", "get_pattern", "combine_signals", "compute_signals"]
