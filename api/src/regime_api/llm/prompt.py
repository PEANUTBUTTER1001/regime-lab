"""LLM 입력(근거 수치)과 프롬프트 (FR-L1·L2). 수치는 화면 표시와 같은 반올림 값으로 미리 만들어 전달한다."""

from __future__ import annotations

PROMPT_VERSION = "report-v1"

SYSTEM = """당신은 과거 백테스트 결과를 한국어로 해설하는 분석 보조자입니다.
규칙:
1. 입력 JSON에 있는 숫자만 그대로 인용하세요. 새 숫자를 계산·추정·반올림하지 마세요.
2. 순위를 매기거나 바꾸지 말고, 국면·신호·종목을 새로 판정하지 마세요.
3. 매수·매도 권유, 종목 추천, 목표가, 투자 비중, 미래 수익 전망을 쓰지 마세요.
4. 표본 부족(거래 수 기준 미달) 셀과 데이터 한계(limitations)를 반드시 언급하세요.
5. 검증(기간 분할·FDR·무작위 벤치마크) 결과를 있는 그대로 설명하세요. 분석 대상(analysis_target)이 false면 그 사실을 분명히 쓰세요.
6. 형식: 세 개의 소제목 '## 요약', '## 검증 결과', '## 한계' 아래에 짧은 문단으로 쓰세요. 전체 12문장 이내.
7. 투자 권유가 아니라는 고지 문구는 화면이 따로 표시하므로 쓰지 않아도 됩니다."""


def _pct(x, nd=2):
    return None if x is None else round(float(x) * 100, nd)


def _r(x, nd):
    return None if x is None else round(float(x), nd)


LIMITATIONS = [
    "시장 국면은 지수 이력 한계로 2021-07-21 이후 진입 거래만 산출된다.",
    "KOSPI 종목은 소속부 데이터가 없어 관리종목 제외가 KOSDAQ에만 적용된다.",
    "과거 데이터 분석 결과이며 미래 성과를 보장하지 않는다.",
]


def build_facts(result: dict, strategy: str) -> dict:
    sr = next(s for s in result["strategies"] if s["strategy"] == strategy)
    m, v, inp = sr["summary"], sr["validation"], sr.get("strategy_input", {})
    min_n = result.get("min_cell_trades", 300)
    cells = sr.get("cells_market", [])
    ok_cells = [c for c in cells if not c.get("sample_insufficient")]
    return {
        "strategy": {
            "name": strategy, "patterns": inp.get("patterns"), "combine": inp.get("combine"),
            "exit": inp.get("exit"), "markets": inp.get("markets"), "period": inp.get("period"),
            "min_avg_value_krw": inp.get("min_avg_value_krw"), "cap_groups": inp.get("cap_groups"),
        },
        "data_as_of": result.get("data_as_of"),
        "units": "수익률·승률·초과수익·MDD는 % 단위, 손익비·샤프는 배수",
        "metrics": {
            "trades": m.get("trades"),
            "win_rate_pct": _pct(m.get("win_rate"), 1),
            "mean_ret_pct": _pct(m.get("mean_ret")),
            "median_ret_pct": _pct(m.get("median_ret")),
            "mean_excess_pct": _pct(m.get("mean_excess")),
            "payoff_ratio": _r(m.get("payoff_ratio"), 2),
            "sharpe_per_trade": _r(m.get("sharpe"), 3),
            "mdd_pct": _pct(m.get("mdd"), 1),
            "excluded_trades": m.get("excluded_trades"),
            "skipped_entries": m.get("skipped_entries"),
            "period_start": m.get("period_start"), "period_end": m.get("period_end"),
        },
        "validation": {
            "fdr_family_size": result.get("fdr_family_size"),
            "p_value": _r(v.get("p_value"), 3),
            "fdr_pass": v.get("fdr_pass"),
            "split_judgement": v.get("split_judgement"),
            "first_half_trades": v.get("h1_trades"), "second_half_trades": v.get("h2_trades"),
            "first_half_mean_excess_pct": _pct(v.get("h1_mean_excess")),
            "second_half_mean_excess_pct": _pct(v.get("h2_mean_excess")),
            "random_percentile": _r(v.get("random_percentile") * 100, 1) if v.get("random_percentile") is not None else None,
            "random_pass": v.get("random_pass"),
            "analysis_target": v.get("analysis_target"),
        },
        "cells": {
            "min_cell_trades": min_n,
            "total_cells": len(cells),
            "insufficient_cells": len(cells) - len(ok_cells),
            "sufficient": [{"regime": c["regime"], "market": c["market"], "cap_group": c["cap_group"],
                            "trades": c["trades"], "mean_excess_pct": _pct(c.get("mean_excess"))}
                           for c in ok_cells],
        },
        "limitations": LIMITATIONS,
    }


def build_user(facts: dict) -> str:
    import json

    return "다음 JSON은 한 번의 백테스트 결과입니다. 규칙에 따라 해설하세요.\n\n" + json.dumps(
        facts, ensure_ascii=False, indent=1)
