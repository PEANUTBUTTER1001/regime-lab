"""요청 스키마 (FR-X5 서버 측 입력 검증의 1차 관문). OpenAPI 계약의 기준이다.

타입·키 검증은 pydantic(엄격 모드, 알 수 없는 키 금지)이 하고, 값 범위 검증은 엔진 Strategy.validate 가 한다.
단위: 날짜 YYYY-MM-DD, 거래대금 원 정수, 보유 기간 거래일 정수, 퍼센트 단일 단위(-8 = -8%), trailing stop 은 null.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_STRATEGIES = 5  # 한 요청의 최대 전략 수 (비교 실행, 핵심 5종 기준)

PatternName = Literal["ma_cross_5_20", "breakout_20d", "breakout_vol", "rsi_rebound", "bb_lower_recover"]
Market = Literal["KOSPI", "KOSDAQ"]
CapGroup = Literal["large", "mid", "small"]
DATE = r"^\d{4}-\d{2}-\d{2}$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ExitIn(_Strict):
    stop_loss_pct: float | None = Field(None, description="손절 %, -50 ~ -1 또는 null(미사용)")
    take_profit_pct: float | None = Field(None, description="익절 %, +1 ~ +200 또는 null(미사용)")
    max_hold_days: int = Field(20, description="최대 보유 거래일, 1 ~ 250 (필수)")
    trailing_stop_pct: None = Field(None, description="이번 릴리스는 null 만 허용")


class PeriodIn(_Strict):
    start: str = Field(pattern=DATE, description="진입 신호 시작일 YYYY-MM-DD (≥ 2020-09-01)")
    end: str = Field(pattern=DATE, description="진입 신호 종료일 YYYY-MM-DD (≤ 데이터 기준일)")


class FilterIn(_Strict):
    markets: list[Market] | None = Field(None, description="대상 시장. 생략 시 KOSPI·KOSDAQ")
    period: PeriodIn | None = Field(None, description="진입 신호 기간. 생략 시 전체")
    min_avg_value_krw: int | None = Field(None, description="20일 평균 거래대금 하한(원), 5억 원 이상")
    cap_groups: list[CapGroup] | None = Field(None, description="시총 그룹. 생략 시 전체")


class StrategyIn(FilterIn):
    name: str = Field(pattern=r"^[A-Za-z0-9_\-]{1,64}$", description="전략 이름 (영문·숫자·_·-)")
    patterns: list[PatternName] = Field(min_length=1, description="핵심 패턴 1개 이상")
    combine: Literal["and", "or"] = "or"
    exit: ExitIn | None = Field(None, description="청산 규칙. 생략 시 기본값(-8 / +20 / 20거래일)")


class RunRequest(_Strict):
    strategies: list[StrategyIn] = Field(min_length=1, max_length=MAX_STRATEGIES,
                                         description="전략 1개 이상. 전략 수가 FDR 가족 크기 m")


class PreviewRequest(FilterIn):
    pass


def to_engine_dict(model: BaseModel) -> dict:
    """생략한 필드는 빼고(엔진 기본값 적용), 명시한 null 은 유지한다."""
    return model.model_dump(exclude_unset=True)
