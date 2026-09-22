"""Morning Briefing 자리 (FR-G6, C-7). 야간 시세·뉴스 입력이 없으므로(§8 T-2) 브리핑을 만들지 않는다."""

from __future__ import annotations

from fastapi import APIRouter

from regime_lab.runs import DISCLAIMER

router = APIRouter(tags=["briefing"])


@router.get("/briefing")
def briefing():
    return {
        "status": "data_unavailable",
        "reason_code": "external_inputs_missing",
        "reason": "Night-market series and news headlines are not available, so no briefing is produced (planned task T-2).",
        "missing_inputs": ["night_market_series", "news_headlines"],
        "disclaimer": DISCLAIMER,
    }
