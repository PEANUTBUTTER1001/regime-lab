"""데이터 서버 API → store 형식 로컬 Parquet 동기화 (F1, 결정 F1a, 로드맵 S1). 인프라 스크립트.

팀 데이터 서버(조회 전용 API, docs/F1_데이터_서버_API.md)에서 일봉 원본을 받아 **지금 엔진이 읽는 store 형식 그대로**
새 폴더에 저장한다. 엔진의 데이터 읽기(data/loader.py)는 바꾸지 않는다. 받은 뒤 paths.local.yaml 의 store 를 새 폴더로
바꾸면 백테스트·예측·근거 검색이 그대로 돈다.

    <out>/store/   prices·marketcap·master·index·delisted·sample30.parquet, raw/krx_daily/YYYYMMDD.parquet,
                   raw/sector/YYYYMMDD_ALL.parquet
    <out>/minutes/ stock_minutes_YYYYMM.parquet + minutes_manifest.json   (--minutes 일 때만)

- 접속값은 환경변수로만: REGIME_DATA_API_URL (예: http://<서버>:<포트>), REGIME_DATA_API_TOKEN (서버가 인증을 켰을 때만,
  X-API-Key 헤더). 주소·토큰은 출력·오류 문구에 남기지 않는다.
- 표준 라이브러리 urllib 만 쓴다: 리디렉션 거부, 시간 제한, 응답 크기 상한. 서버 한도(결과 20만 행)를 넘으면 기간·종목 묶음을
  반으로 나눠 다시 받는다.
- 원본 store 를 덮어쓰지 않는다. <out> 옆 .tmp_ 폴더에 받고, 검증을 통과해야 한 번에 이름을 바꾼다. 실패하면 임시 폴더를 지운다.
- 검증: 응답 count 와 받은 행 수, 열 이름·자료형, (--compare 의 기존 store 가 있으면) 겹치는 기간·종목의 값이 모두 같은지.
  하나라도 다르면 저장하지 않는다.
- 서버 형식과 store 형식의 차이(2026-10-13 승인 변환 규칙): store_index 의 market 열 → index, store_krx_daily·store_sector 는
  날짜별 파일로 나눔(sector 는 서버에 시장 열이 없어 YYYYMMDD_ALL — loader 는 date·ticker·sector 만 읽음), 날짜 문자열 →
  timestamp, is_delisted 0/1 → bool. sample30 은 기존 store 의 30종목을 따르고 없으면 --sample-tickers 로 받는다.

사용:
    uv run --project engine python engine/scripts/sync_data_server.py --out ../regime-data-sync
    uv run --project engine python engine/scripts/sync_data_server.py --out ../regime-data-sync --minutes
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from regime_lab.config import load_paths  # noqa: E402

ENV_URL, ENV_TOKEN = "REGIME_DATA_API_URL", "REGIME_DATA_API_TOKEN"
TIMEOUT_SEC = 120
MAX_RESPONSE_BYTES = 512 * 2**20       # 20만 행 JSON 이 넉넉히 들어가는 크기
TRADING_DAYS_PER_CALL = 60             # 일봉 표를 처음 나눠 받는 거래일 수 (한도에 걸리면 반으로)
MINUTE_CODES_PER_CALL = 20             # 1분봉을 처음 나눠 받는 종목 수 (한 달·종목당 최대 약 8천 행)

# store 파일 형식 (열 순서·자료형). kind: ts=timestamp[ns], str, f64, i64, bool
PRICE_COLS = [("date", "ts"), ("ticker", "str"), ("open", "f64"), ("high", "f64"), ("low", "f64"),
              ("close", "i64"), ("volume", "i64"), ("value", "i64")]
STORE = {
    "prices": ("store_prices", PRICE_COLS),
    "marketcap": ("store_marketcap", [("date", "ts"), ("ticker", "str"), ("marketcap", "i64"), ("shares", "i64"),
                                      ("market", "str")]),
    "master": ("store_master", [("ticker", "str"), ("name", "str"), ("market", "str"), ("sector", "str"),
                                ("listing_date", "ts"), ("kind", "str"), ("price_source", "str"),
                                ("is_delisted", "bool")]),
    "index": ("store_index", [("date", "ts"), ("index", "str"), ("open", "f64"), ("high", "f64"), ("low", "f64"),
                              ("close", "f64"), ("volume", "i64")]),
    "delisted": ("store_delisted", [("ticker", "str"), ("name", "str"), ("delisting_date", "ts"), ("market", "str")]),
    "krx_daily": ("store_krx_daily", [("date", "ts"), ("ticker", "str"), ("name", "str"), ("market", "str"),
                                      ("dept", "str"), ("open_raw", "i64"), ("high_raw", "i64"), ("low_raw", "i64"),
                                      ("close_raw", "i64"), ("volume_raw", "i64"), ("value", "i64"),
                                      ("marketcap", "i64"), ("shares", "i64")]),
    "sector": ("store_sector", [("date", "ts"), ("ticker", "str"), ("sector", "str")]),
}
RENAME = {"store_index": {"market": "index"}}   # 서버 열 이름 → store 열 이름
KEYS = {"prices": ["ticker", "date"], "marketcap": ["ticker", "date"], "master": ["ticker"],
        "index": ["index", "date"], "delisted": ["ticker"], "krx_daily": ["ticker", "date"],
        "sector": ["ticker", "date"], "sample30": ["ticker", "date"]}
MINUTE_SCHEMA = [("code", "str"), ("dt", "ts_s"), ("open_p", "i32"), ("high_p", "i32"), ("low_p", "i32"),
                 ("close_p", "i32"), ("volume", "i64"), ("value", "i64")]


class SyncError(Exception):
    """동기화 중단 (주소·토큰을 담지 않는다)."""


class TooManyRows(SyncError):
    """서버 결과 한도(20만 행) 초과 — 범위를 나눠 다시 받는다."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SyncError(f"서버가 다른 주소로 보내려 해 중단했습니다 (HTTP {code})")


class Client:
    """데이터 서버 조회 API (GET 만). base 는 REGIME_DATA_API_URL 값."""

    def __init__(self, base: str, token: str | None = None, opener=None):
        if not base.startswith(("http://", "https://")):
            raise SyncError(f"{ENV_URL} 는 http:// 또는 https:// 로 시작해야 합니다")
        self._base, self._token = base.rstrip("/"), token or None
        self._opener = opener or urllib.request.build_opener(_NoRedirect())

    def _get(self, path: str, params: dict) -> dict:
        q = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
        headers = {"Accept": "application/json", **({"X-API-Key": self._token} if self._token else {})}
        req = urllib.request.Request(f"{self._base}{path}{'?' + q if q else ''}", headers=headers)
        try:
            with self._opener.open(req, timeout=TIMEOUT_SEC) as resp:
                body = resp.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as e:
            detail = _detail(e)
            if e.code == 400 and "200,000" in detail:
                raise TooManyRows(detail) from None
            raise SyncError(f"{path} 요청 실패: HTTP {e.code} {detail}") from None
        except urllib.error.URLError as e:
            raise SyncError(f"{path} 서버 연결 실패: {type(e.reason).__name__}") from None
        except TimeoutError:
            raise SyncError(f"{path} 응답 시간 초과({TIMEOUT_SEC}초)") from None
        if len(body) > MAX_RESPONSE_BYTES:
            raise SyncError(f"{path} 응답이 {MAX_RESPONSE_BYTES // 2**20}MB 를 넘어 중단했습니다")
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise SyncError(f"{path} 응답이 JSON 이 아닙니다") from None

    def health(self) -> bool:
        return bool(self._get("/health", {}).get("ok"))

    def table(self, name: str, **params) -> pd.DataFrame:
        j = self._get(f"/v1/{name}", params)
        if j.get("table") != name or not isinstance(j.get("rows"), list) or j.get("count") != len(j["rows"]):
            raise SyncError(f"{name} 응답의 행 수가 count 와 다릅니다")
        return pd.DataFrame(j["rows"], columns=j["columns"])


def _detail(e: urllib.error.HTTPError) -> str:
    try:
        d = json.loads(e.read(64 * 1024).decode("utf-8")).get("detail")
    except Exception:  # noqa: BLE001 — 오류 본문을 못 읽어도 상태 코드로 보고한다
        return ""
    return d if isinstance(d, str) else json.dumps(d, ensure_ascii=False)[:300]


def convert(df: pd.DataFrame, spec: list[tuple[str, str]], what: str) -> pd.DataFrame:
    """서버 응답(문자열 날짜·0/1·정수/실수) → store 파일과 같은 열 순서·자료형."""
    missing = [c for c, _ in spec if c not in df]
    if missing:
        raise SyncError(f"{what}: 서버 응답에 열이 없습니다 {missing}")
    out = {}
    for col, kind in spec:
        s = df[col]
        if kind in ("ts", "ts_s"):
            v = pd.to_datetime(s, errors="coerce")
            if (v.isna() & s.notna()).any():
                raise SyncError(f"{what}.{col}: 날짜로 읽을 수 없는 값이 있습니다")
            out[col] = v.astype("datetime64[s]" if kind == "ts_s" else "datetime64[ns]")
        elif kind in ("i64", "i32", "bool"):
            if s.isna().any():
                raise SyncError(f"{what}.{col}: 정수 열에 결측이 있습니다 ({int(s.isna().sum())}행)")
            out[col] = s.astype(bool) if kind == "bool" else s.astype("int32" if kind == "i32" else "int64")
        elif kind == "f64":
            out[col] = s.astype("float64")
        else:  # Parquet 를 다시 읽을 때와 같은 문자열 자료형 (결측 = NaN)
            out[col] = s.astype(object).where(s.notna(), None).astype(pd.StringDtype(na_value=np.nan))
    return pd.DataFrame(out)


def fetch_by_dates(client: Client, name: str, dates: list[pd.Timestamp], step: int, log) -> pd.DataFrame:
    """거래일 창으로 나눠 받는다. 한도에 걸리면 그 창만 반으로."""
    parts, i = [], 0
    while i < len(dates):
        n = min(step, len(dates) - i)
        while True:
            a, b = dates[i], dates[i + n - 1]
            try:
                parts.append(client.table(name, start=a.date().isoformat(), end=b.date().isoformat()))
                break
            except TooManyRows:
                if n == 1:
                    raise SyncError(f"{name}: 하루치도 서버 한도를 넘습니다 ({a.date()})") from None
                n = max(1, n // 2)
        i += n
    log(f"  {name}: {sum(len(p) for p in parts):,}행 ({len(parts)}번 요청)")
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def _sorted(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    return df.sort_values(keys, kind="stable").reset_index(drop=True)


def compare(new: pd.DataFrame, old: pd.DataFrame, keys: list[str], what: str) -> None:
    """기존 store 와 겹치는 범위(기존 최신일까지·기존 종목)의 값이 모두 같아야 한다. 새 날짜·새 종목 추가는 허용."""
    if "date" in old and len(old):
        new = new[new["date"] <= old["date"].max()]
    if "date" not in old:  # 마스터류: 기존 종목만 대조
        new = new[new[keys[0]].isin(old[keys[0]])]
    a, b = _sorted(new, keys), _sorted(old[list(new.columns)], keys)
    if len(a) != len(b) or not a[keys].equals(b[keys]):
        raise SyncError(f"{what}: 기존 store 와 겹치는 행이 다릅니다 (서버 {len(a):,}행 / 기존 {len(b):,}행)")
    for c in new.columns:
        if not a[c].equals(b[c]):
            bad = int((a[c].ne(b[c]) & ~(a[c].isna() & b[c].isna())).sum())
            raise SyncError(f"{what}.{c}: 기존 store 와 값이 다른 행 {bad}개 — 저장하지 않습니다")


def read_store(store: Path) -> dict[str, pd.DataFrame]:
    """비교용 기존 store (파일 그대로)."""
    d = {k: pd.read_parquet(store / f"{k}.parquet") for k in ("prices", "marketcap", "master", "index", "delisted",
                                                              "sample30")}
    for sub in ("krx_daily", "sector"):
        files = sorted((store / "raw" / sub).glob("*.parquet"))
        d[sub] = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True) if files else pd.DataFrame()
    return d


def sync_store(client: Client, dest: Path, old: dict | None, sample_tickers: list[str] | None, log) -> dict:
    """일봉 store 를 dest 에 쓴다. 돌려주는 값: 파일별 행 수."""
    got = {}
    for key in ("master", "delisted", "index"):
        name, spec = STORE[key]
        got[key] = convert(client.table(name).rename(columns=RENAME.get(name, {})), spec, name)
        log(f"  {name}: {len(got[key]):,}행")
    dates = sorted(got["index"]["date"].unique())
    if not dates:
        raise SyncError("store_index 가 비어 있어 거래일을 알 수 없습니다")
    for key in ("prices", "marketcap", "krx_daily", "sector"):
        name, spec = STORE[key]
        got[key] = convert(fetch_by_dates(client, name, [pd.Timestamp(x) for x in dates], TRADING_DAYS_PER_CALL, log),
                           spec, name)
    tickers = sorted(old["sample30"]["ticker"].unique()) if old is not None and "sample30" in old else sample_tickers
    if not tickers:
        raise SyncError("sample30 종목을 알 수 없습니다 — --compare 로 기존 store 를 주거나 --sample-tickers 로 지정하세요")
    got["sample30"] = got["prices"][got["prices"]["ticker"].isin(tickers)].reset_index(drop=True)
    for key, df in got.items():
        dup = df.duplicated(KEYS[key]).sum()
        if dup:
            raise SyncError(f"{key}: 키 {KEYS[key]} 중복 {int(dup)}행")
        if old is not None and key in old and len(old[key]):
            compare(df, old[key], KEYS[key], key)
    (dest / "raw" / "krx_daily").mkdir(parents=True)
    (dest / "raw" / "sector").mkdir(parents=True)
    for key in ("prices", "marketcap", "master", "index", "delisted", "sample30"):
        _sorted(got[key], KEYS[key]).to_parquet(dest / f"{key}.parquet", index=False)
    for d, part in got["krx_daily"].groupby("date", sort=True):
        _sorted(part, ["ticker"]).to_parquet(dest / "raw" / "krx_daily" / f"{d:%Y%m%d}.parquet", index=False)
    for d, part in got["sector"].groupby("date", sort=True):
        _sorted(part, ["ticker"]).to_parquet(dest / "raw" / "sector" / f"{d:%Y%m%d}_ALL.parquet", index=False)
    return {k: len(v) for k, v in got.items()}


def sync_minutes(client: Client, dest: Path, codes: list[str], months: list[str], log) -> dict:
    """1분봉 사본 (export_minutes.py 와 같은 파일·매니페스트 형식). months: YYYY-MM 목록."""
    started, t0 = datetime.now(), time.time()
    dest.mkdir(parents=True)
    stats, total = {}, 0
    for ym in months:
        start = pd.Timestamp(f"{ym}-01")
        end = (start + pd.offsets.MonthEnd(0)).date().isoformat()
        parts, i = [], 0
        while i < len(codes):
            n = min(MINUTE_CODES_PER_CALL, len(codes) - i)
            while True:
                try:
                    parts.append(client.table("stock_minutes", ticker=",".join(codes[i:i + n]),
                                              start=start.date().isoformat(), end=end))
                    break
                except TooManyRows:
                    if n == 1:
                        raise SyncError(f"stock_minutes: 한 종목 한 달도 한도를 넘습니다 ({codes[i]} {ym})") from None
                    n = max(1, n // 2)
            i += n
        df = convert(pd.concat(parts, ignore_index=True), MINUTE_SCHEMA, f"stock_minutes {ym}")
        if df.duplicated(["code", "dt"]).any():
            raise SyncError(f"stock_minutes {ym}: (code, dt) 중복")
        if len(df):
            key = start.strftime("%Y%m")
            _sorted(df, ["code", "dt"]).to_parquet(dest / f"stock_minutes_{key}.parquet", index=False,
                                                    compression="zstd")
            stats[key] = {"rows": len(df), "min_dt": str(df["dt"].min()), "max_dt": str(df["dt"].max())}
            total += len(df)
        log(f"  stock_minutes {ym}: {len(df):,}행")
    if not total:
        raise SyncError("stock_minutes: 받은 행이 없습니다 — 기간·종목을 확인하세요")
    manifest = {"source": "regime_lab data server API /v1/stock_minutes (sync_data_server.py)",
                "columns": [c for c, _ in MINUTE_SCHEMA], "started_at": started.isoformat(timespec="seconds"),
                "finished_at": datetime.now().isoformat(timespec="seconds"),
                "elapsed_sec": round(time.time() - t0, 1), "total_rows": total, "months": dict(sorted(stats.items()))}
    (dest / "minutes_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"stock_minutes": total}


def run(client: Client, out: Path, compare_store: Path | None = None, sample_tickers: list[str] | None = None,
        minutes: bool = False, minutes_start: str | None = None, log=print) -> dict:
    """동기화 본체 (테스트는 가짜 Client 로 부른다). 성공해야만 out 이 생긴다."""
    out = Path(out)
    tmp = out.parent / f".tmp_{out.name}"
    if out.exists():
        raise SyncError(f"{out.name} 폴더가 이미 있습니다 — 새 폴더 이름을 주세요 (기존 폴더는 덮어쓰지 않습니다)")
    if tmp.exists():
        raise SyncError(f"이전 실행의 임시 폴더 {tmp.name} 가 남아 있습니다 — 확인 뒤 직접 지우고 다시 실행하세요")
    if not client.health():
        raise SyncError("서버 /health 가 정상이 아닙니다")
    old = read_store(compare_store) if compare_store is not None else None
    tmp.mkdir(parents=True)
    try:
        log("[store] 일봉 받는 중")
        counts = sync_store(client, tmp / "store", old, sample_tickers, log)
        if minutes:
            months = _minute_months(compare_store, minutes_start, counts, tmp / "store")
            codes = sorted(pd.read_parquet(tmp / "store" / "master.parquet")["ticker"])
            log(f"[minutes] 1분봉 받는 중 ({months[0]} ~ {months[-1]}, {len(codes)}종목)")
            counts |= sync_minutes(client, tmp / "minutes", codes, months, log)
        tmp.rename(out)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)  # 이번 실행이 만든 임시 폴더만 지운다
        raise
    return counts


def _minute_months(compare_store: Path | None, minutes_start: str | None, counts: dict, store: Path) -> list[str]:
    """1분봉 받을 달: --minutes-start 부터 일봉 마지막 달까지. 없으면 기존 1분봉 매니페스트의 첫 달부터."""
    if not minutes_start and compare_store is not None:
        m = compare_store.parent / "minutes" / "minutes_manifest.json"
        if m.exists():
            first = min(json.loads(m.read_text(encoding="utf-8"))["months"])
            minutes_start = f"{first[:4]}-{first[4:]}"
    if not minutes_start:
        raise SyncError("1분봉 시작 달을 알 수 없습니다 — --minutes-start YYYY-MM 을 주세요")
    last = pd.read_parquet(store / "index.parquet")["date"].max()
    return [p.strftime("%Y-%m") for p in pd.period_range(minutes_start, last.strftime("%Y-%m"), freq="M")]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="데이터 서버 API → store 형식 로컬 Parquet 동기화 (F1)")
    ap.add_argument("--out", type=Path, required=True, help="새로 만들 폴더 (안에 store/, minutes/ 생성). 있으면 중단")
    ap.add_argument("--compare", type=Path, help="겹치는 기간을 대조할 기존 store 폴더 (기본: paths.local.yaml 의 store)")
    ap.add_argument("--no-compare", action="store_true", help="기존 store 대조를 건너뜀 (기존 store 가 없는 PC)")
    ap.add_argument("--sample-tickers", help="sample30 종목 (쉼표 구분, 기존 store 가 없을 때)")
    ap.add_argument("--minutes", action="store_true", help="1분봉도 받음 (수천 번 요청·수 GB, 봉 예측에만 필요)")
    ap.add_argument("--minutes-start", help="1분봉 시작 달 YYYY-MM (기존 1분봉 매니페스트가 없을 때)")
    args = ap.parse_args(argv)
    base = os.environ.get(ENV_URL, "").strip()
    if not base:
        print(f"[stop] 환경변수 {ENV_URL} 가 없습니다 (예: http://<서버>:<포트>). README '데이터 서버에서 받기' 참고",
              file=sys.stderr)
        return 2
    compare_store = None if args.no_compare else (args.compare or load_paths().store)
    if compare_store is not None and not (compare_store / "prices.parquet").exists():
        print(f"[stop] 대조할 기존 store 가 없습니다 ({compare_store.name}). 없는 PC 면 --no-compare 와 --sample-tickers 를 주세요",
              file=sys.stderr)
        return 2
    t0 = time.time()
    try:
        counts = run(Client(base, os.environ.get(ENV_TOKEN, "").strip() or None), args.out, compare_store,
                     [t.strip() for t in args.sample_tickers.split(",")] if args.sample_tickers else None,
                     args.minutes, args.minutes_start)
    except SyncError as e:
        print(f"[stop] {e}", file=sys.stderr)
        return 1
    print(f"[done] {args.out} ({time.time() - t0:,.0f}초) " + ", ".join(f"{k} {v:,}" for k, v in counts.items()))
    print(f"다음: engine/config/paths.local.yaml 의 store 를 {(args.out / 'store').as_posix()} 로 바꾸고 "
          "`uv run --project engine regime-lab prepare` 를 실행하세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
