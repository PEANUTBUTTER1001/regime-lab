"""템플릿 보고서 (FR-L4). LLM 을 쓸 수 없거나 검증에 실패했을 때 근거 수치만으로 문장을 만든다.

모든 숫자는 facts 값을 그대로 쓰므로 verify() 를 항상 통과한다 (테스트로 확인).
"""

from __future__ import annotations

JUDGEMENT_KO = {
    "maintained": "전반·후반 모두 평균 초과수익이 양수이고 순위가 유지되었습니다",
    "weakened": "전반·후반 모두 평균 초과수익이 양수이지만 순위가 낮아졌습니다",
    "reversed": "전반과 후반의 평균 초과수익 부호가 바뀌었습니다",
    "negative_both": "전반·후반 모두 평균 초과수익이 0 이하입니다",
    "sample_insufficient": "반기별 거래 수가 기준에 못 미쳐 판정하지 않았습니다",
    "not_applicable": "입력 기간이 분할일을 포함하지 않아 판정하지 않았습니다",
}


def _n(v) -> str:
    if v is None:
        return "산출 불가"
    if isinstance(v, bool):
        return "예" if v else "아니오"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def _regime_sentence(c: dict) -> str:
    """국면별 성과: 표본 충분 셀을 입력 순서대로 인용한다 (순위를 만들지 않음)."""
    cells = c.get("sufficient") or []
    if not cells:
        return f"거래 {_n(c['min_cell_trades'])}건 이상인 국면 × 시장 × 시총 셀이 없어 국면별 성과는 해설하지 않습니다."
    parts = ", ".join(f"{x['regime_ko']}·{x['market_ko']}·{x['cap_group_ko']} {_n(x['mean_excess_pct'])}%" for x in cells)
    return (f"거래 {_n(c['min_cell_trades'])}건 이상인 셀 {_n(c['sufficient_cells'])}개 중 평균 초과수익이 양수인 셀은 "
            f"{_n(c['sufficient_positive_cells'])}개, 음수인 셀은 {_n(c['sufficient_negative_cells'])}개입니다 ({parts}).")


def render(facts: dict) -> str:
    m, v, c = facts["metrics"], facts["validation"], facts["cells"]
    target = "분석 대상으로 분류되었습니다" if v["analysis_target"] else "분석 대상으로 분류되지 않았습니다"
    lines = [
        "## 요약",
        f"{m['period_start']}부터 {m['period_end']}까지 집계 거래 {_n(m['trades'])}건의 비용 차감 후 평균 수익률은 "
        f"{_n(m['mean_ret_pct'])}%, 중앙값은 {_n(m['median_ret_pct'])}%, 승률은 {_n(m['win_rate_pct'])}%입니다. "
        f"같은 기간 소속 시장 지수 대비 평균 초과수익은 {_n(m['mean_excess_pct'])}%이고, 손익비는 {_n(m['payoff_ratio'])}배, "
        f"동일가중 자산곡선의 최대낙폭은 {_n(m['mdd_pct'])}%입니다. " + _regime_sentence(c),
        "",
        "## 검증 결과",
        f"이 실행의 FDR 가족 크기는 {_n(v['fdr_family_size'])}이며, 단측 t-검정 p-value는 {_n(v['p_value'])}로 "
        f"FDR 통과 여부는 '{_n(v['fdr_pass'])}'입니다. 기간 분할은 {JUDGEMENT_KO.get(v['split_judgement'], v['split_judgement'])} "
        f"(전반 {_n(v['first_half_trades'])}건 {_n(v['first_half_mean_excess_pct'])}%, "
        f"후반 {_n(v['second_half_trades'])}건 {_n(v['second_half_mean_excess_pct'])}%). "
        f"무작위 벤치마크 대비 백분위는 {_n(v['random_percentile'])}입니다. 세 검증 결과에 따라 이 전략은 {target}.",
        "",
        "## 한계",
        f"국면 × 시장 × 시총 셀 {_n(c['total_cells'])}개 중 {_n(c['insufficient_cells'])}개는 거래 {_n(c['min_cell_trades'])}건 "
        f"미만이라 결론에 쓰지 않습니다. 기준일 보유 중인 거래 {_n(m['excluded_trades'])}건은 집계에서 제외했습니다. "
        + " ".join(facts["limitations"]),
    ]
    return "\n".join(lines)
