"""요청 스키마 (FR-X5 서버 측 입력 검증의 1차 관문). OpenAPI 계약의 기준이다.

타입·키 검증은 pydantic(엄격 모드, 알 수 없는 키 금지)이 하고, 값 범위 검증은 엔진 Strategy.validate 가 한다.
단위: 날짜 YYYY-MM-DD, 거래대금 원 정수, 보유 기간 거래일 정수, 퍼센트 단일 단위(-8 = -8%), trailing stop 은 null.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_STRATEGIES = 5  # 한 요청의 최대 전략 수 (비교 실행, 핵심 5종 기준)

PatternName = Literal["ma_cross_5_20", "breakout_20d", "breakout_vol", "rsi_rebound", "bb_lower_recover",
                      "three_down_up", "bb_squeeze_break", "pullback_ma20", "granville_buy1", "engulfing",  # X6 후순위 5종
                      "macd_cross", "high_52w", "disparity_rebound", "stochastic_rebound"]  # 추가 4종
Market = Literal["KOSPI", "KOSDAQ"]
CapGroup = Literal["large", "mid", "small"]
DATE = r"^\d{4}-\d{2}-\d{2}$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ExitIn(_Strict):
    stop_loss_pct: float | None = Field(None, description="손절 %, -50 ~ -1 또는 null(미사용)")
    take_profit_pct: float | None = Field(None, description="익절 %, +1 ~ +200 또는 null(미사용)")
    max_hold_days: int = Field(20, description="최대 보유 거래일, 1 ~ 250 (필수)")
    trailing_stop_pct: float | None = Field(None, description="트레일링 스톱 %, 보유 중 최고 기준가 대비 -50 ~ -1 또는 null(미사용)")
    breakeven_trigger_pct: float | None = Field(None, description="본전 스톱 발동 수익률 %, +1 ~ +100 또는 null(미사용)")
    ma_exit_window: int | None = Field(None, description="이동평균 이탈 청산 기간(거래일), 5 ~ 250 또는 null(미사용)")


class PeriodIn(_Strict):
    start: str = Field(pattern=DATE, description="진입 신호 시작일 YYYY-MM-DD (≥ 2020-09-01)")
    end: str = Field(pattern=DATE, description="진입 신호 종료일 YYYY-MM-DD (≤ 데이터 기준일)")


class FilterIn(_Strict):
    markets: list[Market] | None = Field(None, description="대상 시장. 생략 시 KOSPI·KOSDAQ")
    period: PeriodIn | None = Field(None, description="진입 신호 기간. 생략 시 전체")
    min_avg_value_krw: int | None = Field(None, description="20일 평균 거래대금 하한(원), 5억 원 이상")
    cap_groups: list[CapGroup] | None = Field(None, description="시총 그룹. 생략 시 전체")


PatternParams = dict[PatternName, dict[str, int | float]]  # 정수는 정수 그대로 (일수 같은 수치는 엔진이 정수만 받음)


class StrategyIn(FilterIn):
    name: str = Field(pattern=r"^[A-Za-z0-9_\-]{1,64}$", description="전략 이름 (영문·숫자·_·-)")
    patterns: list[PatternName] = Field(min_length=1, description="핵심 패턴 1개 이상")
    combine: Literal["and", "or"] = "or"
    exit: ExitIn | None = Field(None, description="청산 규칙. 생략 시 기본값(-8 / +20 / 20거래일)")
    pattern_params: PatternParams | None = Field(
        None, description="P1-4 조합별 패턴 수치 {패턴: {수치: 값}}. 바꿀 수 있는 수치·범위는 GET /meta 의 pattern_params")


class RunRequest(_Strict):
    strategies: list[StrategyIn] = Field(min_length=1, max_length=MAX_STRATEGIES,
                                         description="전략 1개 이상. 전략 수가 FDR 가족 크기 m")


class PreviewRequest(FilterIn):
    pass


def to_engine_dict(model: BaseModel) -> dict:
    """생략한 필드는 빼고(엔진 기본값 적용), 명시한 null 은 유지한다."""
    return model.model_dump(exclude_unset=True)


# ---------------------------------------------------------------- 역방향 탐색 (P1-7, 설계 §4.1)
CombineName = Literal["and", "or"]


class SearchAxesIn(_Strict):
    patterns: list[PatternName] | None = Field(None, description="탐색할 패턴. 생략 시 핵심 5종 전체")
    combine: list[CombineName] | None = Field(None, description="결합 방식. 생략 시 and·or")
    stop_loss_pct: list[float | None] | None = Field(None, description="손절 % 목록 (허용 값은 GET /searches/options)")
    take_profit_pct: list[float | None] | None = Field(None, description="익절 % 목록")
    max_hold_days: list[int] | None = Field(None, description="최대 보유 거래일 목록")
    pattern_params: dict[PatternName, dict[str, list[int | float]]] | None = Field(
        None, description="P1-4 패턴 수치 축 {패턴: {수치: [값...]}}. 허용 값은 GET /searches/options 의 pattern_axes")


class SearchRequestIn(_Strict):
    name: str = Field(pattern=r"^[A-Za-z0-9_\-]{1,64}$", description="탐색 이름 (영문·숫자·_·-)")
    target_win_rate: float = Field(description="목표 승률 0~1 (0.55 = 55% 이상)")
    min_trades: int | None = Field(None, description="최소 거래 수. 생략 시 300")
    axes: SearchAxesIn | None = None
    filters: FilterIn | None = Field(None, description="시장·기간·거래대금 하한·시총 그룹 (기간은 분할일 포함)")


# ---------------------------------------------------------------- 찾은 조합 저장 (P1-9·P1-7, 설계 §5)
class PresetStrategyIn(FilterIn):
    name: str | None = Field(None, description="무시됨. 저장 시 조합 id 로 바뀐다")
    patterns: list[PatternName] = Field(min_length=1)
    combine: Literal["and", "or"] = "or"
    exit: ExitIn | None = None
    pattern_params: PatternParams | None = None


class FromSearchIn(_Strict):
    search_id: str = Field(pattern=r"^[A-Za-z0-9_\-]{1,128}$")
    candidate_id: str = Field(pattern=r"^c\d{3}$")


class PresetCreateIn(_Strict):
    name: str = Field(description="조합 이름 1~60자 (한글 가능)")
    strategy: PresetStrategyIn | None = Field(None, description="직접 작성한 조합. from_search 와 둘 중 하나")
    from_search: FromSearchIn | None = Field(None, description="탐색 기록의 후보를 저장")


class PresetUpdateIn(_Strict):
    revision: int = Field(description="불러온 조합의 revision. 다르면 409 version_conflict")
    name: str | None = None
    strategy: PresetStrategyIn | None = None
