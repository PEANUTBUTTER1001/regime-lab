"""OpenDART list.json 을 흉내 내는 가짜 서버 (네트워크 없음). 합성 공시로 응답 형식·페이지·상태 코드를 재현한다."""

from __future__ import annotations

from datetime import date, timedelta


def make_items(start: date, end: date, per_day: int = 3) -> list[dict]:
    """하루 per_day 건(유가·코스닥 번갈아). 접수번호 = YYYYMMDD + 일련번호 6자리 (OpenDART 형식)."""
    out, d = [], start
    while d <= end:
        ymd = d.strftime("%Y%m%d")
        for i in range(per_day):
            cls = "Y" if i % 2 == 0 else "K"
            corp = f"{(i % 4) + 1:08d}"
            out.append({"corp_code": corp, "corp_name": f"합성회사{corp[-1]}", "stock_code": f"{int(corp):06d}",
                        "corp_cls": cls, "report_nm": f"주요사항보고서(자기주식취득결정)", "rcept_no": f"{ymd}{i + 1:06d}",
                        "flr_nm": f"합성회사{corp[-1]}", "rcept_dt": ymd, "rm": ""})
        d += timedelta(days=1)
    return out


class FakeDart:
    def __init__(self, items: list[dict], *, fail: dict | None = None):
        self.items = items
        self.calls: list[dict] = []
        self.fail = fail or {}  # {호출 순번(1부터): 응답 body}

    def __call__(self, params: dict) -> dict:
        self.calls.append(dict(params))
        n = len(self.calls)
        if n in self.fail:
            f = self.fail[n]
            if isinstance(f, Exception):
                raise f
            return f
        b, e = params["bgn_de"], params["end_de"]
        rows = sorted((it for it in self.items if b <= it["rcept_dt"] <= e and it["corp_cls"] == params["corp_cls"]),
                      key=lambda it: it["rcept_no"], reverse=True)  # 최신순 (OpenDART 기본)
        if not rows:
            return {"status": "013", "message": "조회된 데이타가 없습니다."}
        size, page = int(params["page_count"]), int(params["page_no"])
        total_page = (len(rows) + size - 1) // size
        return {"status": "000", "message": "정상", "page_no": page, "page_count": size, "total_count": len(rows),
                "total_page": total_page, "list": rows[(page - 1) * size: page * size]}
