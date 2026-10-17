# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["PyMySQL>=1.1.1,<2", "PyYAML>=6,<7"]
# ///
"""금융위 API 3종 → 별도 MariaDB 원본 표. uv run --script collect_fsc.py --help.

독립 수집 인프라: 엔진/웹 import 없음. 키·접속값은 환경변수, 수집은 --server-pc 명시.
외부 예외/응답 메시지는 키·접속값을 포함할 수 있어 출력하지 않는다.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = ROOT / "engine/config/default.yaml"
SCHEMA_PATH = Path(__file__).with_name("schema.sql")
KST = timezone(timedelta(hours=9))
SOURCES = {
    "stock": ("GetStockSecuritiesInfoService_V2/getStockPriceInfo_V2", "fsc_stock_daily_raw"),
    "index": ("GetMarketIndexInfoService_V2/getStockMarketIndex_V2", "fsc_index_daily_raw"),
    "listed": ("GetKrxListedInfoService_V2/getItemInfo_V2", "fsc_listed_daily_raw"),
}
RAW_COLUMNS = {"id", "bas_dt", "entity_key", "revision", "code", "isin", "label", "market",
               "payload_hash", "payload_json", "quality_json", "observed_at", "batch_id"}
LOG_COLUMNS = {"id", "source", "operation", "bas_dt", "batch_id", "status", "observed_at",
               "config_hash", "report_json"}


class CollectError(Exception):
    """code/message는 고정 문구만 사용. 외부 예외 원문을 넘기지 않는다."""
    def __init__(self, code, message, *, retryable=False, fatal=False):
        super().__init__(message)
        self.code, self.retryable, self.fatal = code, retryable, fatal


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def parse_date(value):
    try:
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}|\d{8}", value):
            raise ValueError
        return date.fromisoformat(value) if "-" in value else datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        raise CollectError("invalid_date", "실제 존재하는 YYYY-MM-DD 또는 YYYYMMDD 날짜 필요") from None


def utcnow():
    return datetime.now(timezone.utc)


def load_config(path=CONFIG_PATH):
    try:
        import yaml
        with open(path, encoding="utf-8") as stream:
            cfg = yaml.safe_load(stream)["fsc_collection"]
        positives = ("db_port", "db_timeout_seconds", "page_size", "max_page_size", "max_rows_per_day",
                     "max_response_bytes", "http_timeout_seconds", "max_requests_per_run", "lookback_days",
                     "pending_days_per_update", "delayed_retry_minutes")
        nonnegative = ("http_retries", "delayed_retries", "schedule_hour", "schedule_minute")
        if any(type(cfg[k]) is not int or cfg[k] <= 0 for k in positives):
            raise ValueError
        if any(type(cfg[k]) is not int or cfg[k] < 0 for k in nonnegative):
            raise ValueError
        for k in ("request_interval_seconds", "retry_backoff_seconds"):
            if type(cfg[k]) not in (int, float) or not (0 < cfg[k] < float("inf")):
                raise ValueError
        if not 1 <= cfg["page_size"] <= cfg["max_page_size"] <= 10000:
            raise ValueError
        if cfg["db_port"] > 65535 or cfg["schedule_hour"] > 23 or cfg["schedule_minute"] > 59:
            raise ValueError
        if not cfg["markets"] or not set(cfg["markets"]) <= {"KOSPI", "KOSDAQ"}:
            raise ValueError
        if not cfg["index_names"] or len(set(cfg["index_names"])) != len(cfg["index_names"]):
            raise ValueError
        if any(not isinstance(s, str) or not s.strip() for s in cfg["index_names"]):
            raise ValueError
        if not re.fullmatch(r"[A-Za-z0-9_]+", cfg["database"]):
            raise ValueError
        parse_date(cfg["earliest_probe_date"])
        return cfg
    except CollectError:
        raise
    except Exception:
        raise CollectError("invalid_config", "fsc_collection 설정을 확인하세요") from None


@dataclass(repr=False)
class Credentials:
    api_key: str = field(repr=False)
    host: str = field(repr=False)
    user: str = field(repr=False)
    password: str = field(repr=False)
    database: str
    port: int
    ssl_ca: str | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls, cfg):
        names = ("REGIME_FSC_SERVICE_KEY", "REGIME_FSC_DB_HOST", "REGIME_FSC_DB_USER", "REGIME_FSC_DB_PASSWORD")
        missing = [n for n in names if not os.environ.get(n)]
        if missing:
            raise CollectError("missing_environment", "환경변수 필요: " + ", ".join(missing), fatal=True)
        try:
            database = os.environ.get("REGIME_FSC_DB_NAME", cfg["database"])
            port = int(os.environ.get("REGIME_FSC_DB_PORT", cfg["db_port"]))
            if not re.fullmatch(r"[A-Za-z0-9_]+", database) or not 1 <= port <= 65535:
                raise ValueError
            # '+' 원문은 보존. %xx가 있는 인코딩 키만 한 번 디코딩한 뒤 urlencode 한다.
            key = urllib.parse.unquote(os.environ[names[0]].strip())
            if not key or any(c.isspace() for c in key):
                raise ValueError
            return cls(key, os.environ[names[1]], os.environ[names[2]], os.environ[names[3]],
                       database, port, os.environ.get("REGIME_FSC_DB_SSL_CA") or None)
        except ValueError:
            raise CollectError("invalid_environment", "DB 이름·포트·API 키 형식을 확인하세요", fatal=True) from None


def result_error(code):
    code = str(code)
    if code in {"22", "API token quota exceeded"}:
        return CollectError("quota_exceeded", "API 일일 한도 초과: 이후 재개 필요", fatal=True)
    if code in {"23", "API rate limit exceeded"}:
        return CollectError("rate_limited", "API 요청 속도 제한", retryable=True)
    if code in {"20", "30", "31", "Unauthorized", "Forbidden"}:
        return CollectError("api_access_denied", "API 키·서비스별 승인·만료 상태 확인 필요", fatal=True)
    if code in {"01", "04", "05", "99"}:
        return CollectError("api_transient", "API 일시 오류", retryable=True)
    return CollectError("api_business_error", "API 업무 오류: 키·승인·요청 조건 확인 필요", fatal=True)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CollectError("redirect_rejected", "API 리디렉션 거부", fatal=True)


class ApiClient:
    def __init__(self, key, cfg, *, opener=None, sleep=time.sleep, monotonic=time.monotonic):
        self._key, self.cfg = key, cfg
        self.opener = opener or urllib.request.build_opener(NoRedirect())
        self.sleep, self.monotonic = sleep, monotonic
        self.requests, self.last_request = 0, None

    def page(self, source, params, page=1, size=None):
        if source not in SOURCES:
            raise CollectError("invalid_source", "지원하지 않는 출처")
        size = size or self.cfg["page_size"]
        query = {**params, "serviceKey": self._key, "resultType": "json", "pageNo": page, "numOfRows": size}
        url = "https://apis.data.go.kr/1160100/" + SOURCES[source][0] + "?" + urllib.parse.urlencode(query)
        for attempt in range(self.cfg["http_retries"] + 1):
            if self.requests >= self.cfg["max_requests_per_run"]:
                raise CollectError("request_budget", "이번 실행 요청 예산 도달: 같은 명령으로 재개", fatal=True)
            if self.last_request is not None:
                wait = self.cfg["request_interval_seconds"] - (self.monotonic() - self.last_request)
                if wait > 0:
                    self.sleep(wait)
            self.requests += 1
            self.last_request = self.monotonic()
            try:
                with self.opener.open(urllib.request.Request(url, headers={"Accept": "application/json"}),
                                      timeout=self.cfg["http_timeout_seconds"]) as response:
                    body = response.read(self.cfg["max_response_bytes"] + 1)
                if len(body) > self.cfg["max_response_bytes"]:
                    raise CollectError("response_too_large", "API 응답 크기 한도 초과")
                return decode_page(body, page, size)
            except urllib.error.HTTPError as e:
                status = e.code
                e.close()
                error = (CollectError("http_access_denied", "API 키·승인 상태 확인 필요", fatal=True)
                         if status in (401, 403) else
                         CollectError("http_error", "API HTTP 오류", retryable=status == 429 or status >= 500))
            except (OSError, urllib.error.URLError, TimeoutError):
                error = CollectError("network_error", "API 연결·응답 시간 초과", retryable=True)
            except CollectError as e:
                error = e
            if not error.retryable or attempt == self.cfg["http_retries"]:
                raise error from None
            self.sleep(self.cfg["retry_backoff_seconds"] * (2 ** attempt))
        raise AssertionError("unreachable")


def integer(value):
    if not re.fullmatch(r"\d+", str(value)):
        raise CollectError("invalid_page", "API 페이지 수치 형식 오류")
    return int(value)


def decode_page(body, expected_page, requested_size):
    try:
        # JSON 숫자도 문자열로 받아 큰 정수·소수 자릿수를 보존한다.
        def reject_constant(_):
            raise ValueError("nonfinite")
        data = json.loads(body, parse_int=str, parse_float=str, parse_constant=reject_constant)
    except (ValueError, UnicodeError):
        try:
            root = ET.fromstring(body)
            code = root.findtext(".//returnReasonCode") or root.findtext(".//resultCode")
        except ET.ParseError:
            code = None
        if code:
            raise result_error(code)
        raise CollectError("invalid_response", "JSON 응답 아님: 키·요청·API 상태 확인 필요") from None
    try:
        response = data["response"]
        code = str(response["header"]["resultCode"])
        if code != "00":
            raise result_error(code)
        b = response["body"]
        total, size, number = integer(b["totalCount"]), integer(b["numOfRows"]), integer(b["pageNo"])
        if size != requested_size or number != expected_page:
            raise CollectError("invalid_page", "API 페이지 번호·크기 불일치")
        items = b.get("items")
        raw = items.get("item") if isinstance(items, dict) else None
        rows = [] if raw is None or raw == "" else raw if isinstance(raw, list) else [raw]
        if any(not isinstance(r, dict) for r in rows):
            raise CollectError("invalid_page", "API 행 구조 오류")
        if len(rows) != max(0, min(size, total - (number - 1) * size)):
            raise CollectError("missing_page_rows", "API 페이지 행 누락")
        return {"total": total, "size": size, "rows": rows}
    except (KeyError, TypeError, AttributeError):
        raise CollectError("invalid_response", "API 응답 필수 구조 누락") from None


def filters(source, cfg):
    if source == "stock":
        return [{"mrktCls": market} for market in cfg["markets"]]
    if source == "index":
        return [{"idxNm": name} for name in cfg["index_names"]]
    return [{}]


def entity_key(source, row):
    if source == "index":
        if not row.get("idxNm") or not row.get("idxCsf"):
            raise CollectError("invalid_identity", "지수명·분류 누락")
        return digest([row["idxCsf"], row["idxNm"]])
    code = row.get("srtnCd")
    if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9]{6,9}", code):
        raise CollectError("invalid_identity", "종목코드 형식 오류")
    # A 접두어·외국회사·문자 포함 코드 원형 보존. 두 API의 코드를 임의 합치지 않는다.
    return code


def quality(source, row):
    flags = []
    if source == "listed":
        return ["missing_corporate_number"] if not row.get("crno") else []
    values = {}
    fields = ("mkp", "hipr", "lopr", "clpr", "trqu", "trPrc")
    if source == "stock":
        fields += ("lstgStCnt", "mrktTotAmt")
    for name in fields:
        value = row.get(name)
        if value is None or value == "":
            flags.append("missing_" + name)
            continue
        try:
            number = Decimal(str(value))
            if not number.is_finite():
                raise InvalidOperation
            values[name] = number
            if number < 0:
                flags.append("negative_" + name)
            if name in {"trqu", "trPrc", "lstgStCnt", "mrktTotAmt"} and number != number.to_integral_value():
                flags.append("noninteger_" + name)
        except InvalidOperation:
            flags.append("invalid_" + name)
    prices = [values.get(n) for n in ("mkp", "hipr", "lopr", "clpr")]
    if any(p == 0 for p in prices):
        flags.append("zero_price")
    if all(p == 0 for p in prices):
        flags.append("all_ohlc_zero")
    if values.get("trqu") == 0:
        flags.append("zero_volume")
    if all(p is not None for p in prices):
        o, h, lo, c = prices
        if not (lo <= o <= h and lo <= c <= h):
            flags.append("ohlc_order")
    return flags


def fetch_day(api, source, day, cfg):
    rows, seen, api_rows, missing_conditions = [], set(), 0, []
    for condition in filters(source, cfg):
        params = {**condition, "basDt": day.strftime("%Y%m%d")}
        first = api.page(source, params)
        total = first["total"]
        if not total:
            missing_conditions.append(condition)
        api_rows += total
        if api_rows > cfg["max_rows_per_day"]:
            raise CollectError("day_too_large", "하루 응답 건수 한도 초과")
        pages = max(1, (total + first["size"] - 1) // first["size"])
        for number in range(1, pages + 1):
            page = first if number == 1 else api.page(source, params, number)
            if page["total"] != total or page["size"] != first["size"]:
                raise CollectError("changing_pages", "조회 도중 전체 건수·페이지 크기 변경: 재조회 필요")
            for row in page["rows"]:
                if parse_date(row.get("basDt")) != day:
                    raise CollectError("wrong_date", "요청과 다른 기준일 반환")
                if source == "stock" and row.get("mrktCtg") != condition["mrktCls"]:
                    raise CollectError("ignored_filter", "API 시장 필터 불일치")
                if source == "index" and row.get("idxNm") != condition["idxNm"]:
                    raise CollectError("ignored_filter", "API 대표 지수 필터 불일치")
                key = entity_key(source, row)
                if key in seen:
                    raise CollectError("duplicate_identity", "같은 날짜의 원본 키 중복")
                seen.add(key)
                if source == "listed" and row.get("mrktCtg") not in cfg["markets"]:
                    continue
                rows.append({"entity": key, "payload": row, "hash": digest(row), "flags": quality(source, row)})
    rows.sort(key=lambda r: r["entity"])
    counts = Counter(flag for row in rows for flag in row["flags"])
    return rows, {"api_rows": api_rows, "selected_rows": len(rows), "quality": dict(counts),
                  "missing_conditions": missing_conditions,
                  "content_hash": digest([[r["entity"], r["hash"]] for r in rows])}


def discover_first(api, source, end, cfg):
    """누적 건수 이분 탐색. endBasDt 경계 차이는 정확한 일자 조회로 검증."""
    floor = parse_date(cfg["earliest_probe_date"])
    if end < floor:
        raise CollectError("invalid_range", "종료일은 탐색 하한 이후여야 합니다")

    def exists(cutoff):
        return any(api.page(source, {**cond, "endBasDt": cutoff.strftime("%Y%m%d")}, size=1)["total"] > 0
                   for cond in filters(source, cfg))

    if exists(floor):
        raise CollectError("probe_floor_has_data", "탐색 하한 이전 자료 존재: earliest_probe_date 조정 필요")
    upper = end + timedelta(days=1)
    if not exists(upper):
        raise CollectError("source_empty", "종료일까지 자료 없음: 승인·필터·기간 확인 필요")
    lo, hi = floor.toordinal(), upper.toordinal()
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if exists(date.fromordinal(mid)):
            hi = mid
        else:
            lo = mid
    # 상장종목 API는 시장 필터가 없어 다른 시장만 존재했던 초기 날짜도 통과한다.
    # 선택 시장의 실제 첫 행이 나올 때까지 정확 일자로 확인한다.
    for ordinal in range(hi - 1, end.toordinal() + 1):
        day = date.fromordinal(ordinal)
        if day > end:
            continue
        rows, _ = fetch_day(api, source, day, cfg)
        if rows:
            return day
    raise CollectError("discovery_unverified", "최초일 후보의 정확 일자 확인 실패: --start 지정 또는 필터 확인 필요")


class Database:
    def __init__(self, connection, name, cfg):
        self.conn, self.name, self.cfg = connection, name, cfg
        self.lock_name = "regime.fsc." + hashlib.sha256(name.encode()).hexdigest()[:32]
        self.locked = False

    @classmethod
    def connect(cls, credentials, cfg):
        try:
            import pymysql
            options = dict(host=credentials.host, port=credentials.port, user=credentials.user,
                           password=credentials.password, database=credentials.database, charset="utf8mb4",
                           autocommit=False, connect_timeout=cfg["db_timeout_seconds"],
                           read_timeout=cfg["db_timeout_seconds"], write_timeout=cfg["db_timeout_seconds"],
                           init_command="SET SESSION sql_mode=CONCAT(@@SESSION.sql_mode,',STRICT_ALL_TABLES')",
                           cursorclass=pymysql.cursors.DictCursor)
            if credentials.ssl_ca:
                options.update(ssl_ca=credentials.ssl_ca, ssl_verify_cert=True, ssl_verify_identity=True)
            return cls(pymysql.connect(**options), credentials.database, cfg)
        except ImportError:
            raise CollectError("missing_driver", "uv run --script로 실행해 PyMySQL을 설치하세요", fatal=True) from None
        except Exception:
            raise CollectError("db_connect_failed", "DB 연결 실패: 서버 실행환경의 접속값·권한 확인 필요", fatal=True) from None

    def query(self, sql, params=()):
        with self.conn.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall() if cursor.description else []

    @contextmanager
    def exclusive(self):
        acquired = self.query("SELECT GET_LOCK(%s, 0) AS acquired", (self.lock_name,))
        self.conn.commit()
        if not acquired or acquired[0]["acquired"] != 1:
            raise CollectError("collector_busy", "같은 DB의 수집 작업 실행 중", fatal=True)
        self.locked = True
        try:
            yield
        finally:
            try:
                self.conn.rollback()
                self.query("SELECT RELEASE_LOCK(%s)", (self.lock_name,))
                self.conn.commit()
            finally:
                self.locked = False

    def require_lock(self):
        if not self.locked:
            raise CollectError("lock_required", "수집 잠금 필요", fatal=True)
        rows = self.query("SELECT IS_USED_LOCK(%s) = CONNECTION_ID() AS owned", (self.lock_name,))
        if not rows or rows[0]["owned"] != 1:
            raise CollectError("lock_lost", "DB 수집 잠금 손실", fatal=True)

    def schema(self, *, allow_missing=False):
        report = {}
        for table, required in [(v[1], RAW_COLUMNS) for v in SOURCES.values()] + [("fsc_collection_log", LOG_COLUMNS)]:
            info = self.query("SELECT ENGINE AS engine, TABLE_COLLATION AS collation_name FROM information_schema.TABLES "
                              "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s", (self.name, table))
            if not info:
                if allow_missing:
                    report[table] = "missing"
                    continue
                raise CollectError("missing_tables", "init-db 또는 관리자용 schema.sql 실행 필요", fatal=True)
            columns = self.query("SELECT COLUMN_NAME AS name,COLUMN_TYPE AS type_name,EXTRA AS extra "
                                 "FROM information_schema.COLUMNS "
                                 "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s", (self.name, table))
            if {r["name"] for r in columns} != required or info[0]["engine"] != "InnoDB" or info[0]["collation_name"] != "utf8mb4_bin":
                raise CollectError("schema_mismatch", "기존 fsc 표 정의 불일치: 덮어쓰지 않았습니다", fatal=True)
            types = {r["name"]: re.sub(r"^(bigint|int)\(\d+\)", r"\1", r["type_name"].lower()) for r in columns}
            expected = {"id": "bigint unsigned", "bas_dt": "date", "batch_id": "char(32)", "observed_at": "datetime(6)"}
            if table == "fsc_collection_log":
                expected.update(source="varchar(16)", operation="varchar(16)", status="varchar(24)",
                                config_hash="char(64)", report_json="longtext")
            else:
                expected.update(entity_key="varchar(191)", revision="int unsigned", code="varchar(32)",
                                isin="varchar(12)", label="varchar(240)", market="varchar(32)",
                                payload_hash="char(64)", payload_json="longtext", quality_json="longtext")
            if (types != expected or
                    "auto_increment" not in next(r["extra"] for r in columns if r["name"] == "id")):
                raise CollectError("schema_mismatch", "fsc 표 자료형·자동번호 정의 불일치", fatal=True)
            indexes = self.query("SELECT INDEX_NAME AS name, COLUMN_NAME AS column_name, SEQ_IN_INDEX AS position, "
                                 "NON_UNIQUE AS non_unique FROM information_schema.STATISTICS "
                                 "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s ORDER BY INDEX_NAME, SEQ_IN_INDEX", (self.name, table))
            unique = {}
            for ix in indexes:
                if ix["non_unique"] == 0:
                    unique.setdefault(ix["name"], []).append(ix["column_name"])
            needed = ["id"] if table == "fsc_collection_log" else ["bas_dt", "entity_key", "revision"]
            if needed not in unique.values():
                raise CollectError("schema_mismatch", "fsc 표 고유키 불일치", fatal=True)
            report[table] = "ready"
        return report

    def initialize(self):
        self.schema(allow_missing=True)  # 이름이 같은 호환되지 않는 표는 DDL 전에 거부.
        self.require_lock()
        text = re.sub(r"--[^\n]*", "", SCHEMA_PATH.read_text(encoding="utf-8"))
        for statement in text.split(";"):
            if statement.strip():
                self.query(statement)
        self.conn.commit()
        return self.schema()

    def preflight(self):
        selected = self.query("SELECT DATABASE() AS selected_db, VERSION() AS version")[0]
        if selected["selected_db"] != self.name:
            raise CollectError("wrong_database", "선택 DB 불일치", fatal=True)
        # SHOW GRANTS 원문에는 계정/해시가 있을 수 있다. 고정 요약만 반환.
        grants = " ".join(str(value) for row in self.query("SHOW GRANTS") for value in row.values())
        report = {"database_matches": True, "server": "MariaDB" if "MariaDB" in selected["version"] else "MySQL",
                  "create_grant_hint": bool(re.search(r"\bCREATE\b|ALL PRIVILEGES", grants, re.I)),
                  "insert_grant_hint": bool(re.search(r"\bINSERT\b|ALL PRIVILEGES", grants, re.I)),
                  "tables": self.schema(allow_missing=True), "grant_scope_verified": False}
        self.conn.rollback()
        return report

    def log(self, source, operation, day, batch, status, cfg_hash, report):
        self.query("INSERT INTO fsc_collection_log "
                   "(source,operation,bas_dt,batch_id,status,observed_at,config_hash,report_json) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                   (source, operation, day, batch, status, utcnow().replace(tzinfo=None), cfg_hash, canonical(report)))

    def begin_day(self, source, day, batch, cfg_hash):
        self.require_lock()
        self.log(source, "day", day, batch, "started", cfg_hash, {})
        self.conn.commit()

    def fail_day(self, source, day, batch, cfg_hash, code):
        self.conn.rollback()
        self.require_lock()
        self.log(source, "day", day, batch, "failed", cfg_hash, {"error": code})
        self.conn.commit()

    def store_day(self, source, day, batch, cfg_hash, rows, report):
        table = SOURCES[source][1]
        self.require_lock()
        try:
            previous = self.query(f"SELECT r.entity_key, r.revision, r.payload_hash FROM {table} r JOIN "
                                  f"(SELECT entity_key,MAX(revision) AS revision FROM {table} WHERE bas_dt=%s "
                                  "GROUP BY entity_key) v ON r.entity_key=v.entity_key AND r.revision=v.revision WHERE r.bas_dt=%s", (day, day))
            latest = {r["entity_key"]: r for r in previous}
            # 원본 키 소실은 정정 버전만으로 표현할 수 없어 정상 완료로 처리하지 않는다.
            if set(latest) - {r["entity"] for r in rows}:
                raise CollectError("source_rows_disappeared", "재조회에서 기존 키 소실: 원인 확인까지 당일 저장 보류")
            inserted = unchanged = 0
            for row in rows:
                prior = latest.get(row["entity"])
                if prior and prior["payload_hash"] == row["hash"]:
                    unchanged += 1
                    continue
                payload = row["payload"]
                self.query(f"INSERT INTO {table} (bas_dt,entity_key,revision,code,isin,label,market,payload_hash,"
                           "payload_json,quality_json,observed_at,batch_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                           (day, row["entity"], prior["revision"] + 1 if prior else 1, payload.get("srtnCd"),
                            payload.get("isinCd"), payload.get("idxNm") if source == "index" else payload.get("itmsNm"),
                            payload.get("idxCsf") if source == "index" else payload.get("mrktCtg"), row["hash"],
                            canonical(payload), canonical(row["flags"]), utcnow().replace(tzinfo=None), batch))
                inserted += 1
            status = "empty" if not rows else "partial" if report.get("missing_conditions") else "complete"
            result = {**report, "inserted": inserted, "unchanged": unchanged, "status": status}
            self.log(source, "day", day, batch, status, cfg_hash, result)
            self.conn.commit()
            return result
        except BaseException:
            self.conn.rollback()
            raise

    def day_states(self, source):
        rows = self.query("SELECT l.bas_dt,l.status,l.observed_at,l.config_hash FROM fsc_collection_log l JOIN "
                          "(SELECT bas_dt,MAX(id) AS id FROM fsc_collection_log WHERE source=%s AND operation='day' "
                          "GROUP BY bas_dt) v ON l.id=v.id", (source,))
        return {r["bas_dt"]: r for r in rows}

    def discovered(self, source, cfg_hash):
        rows = self.query("SELECT bas_dt FROM fsc_collection_log WHERE source=%s AND operation='discovery' "
                          "AND status='verified' AND config_hash=%s ORDER BY id DESC LIMIT 1", (source, cfg_hash))
        return rows[0]["bas_dt"] if rows else None

    def save_discovery(self, source, day, cfg_hash, end):
        self.require_lock()
        self.log(source, "discovery", day, uuid.uuid4().hex, "verified", cfg_hash,
                 {"probe_floor": self.cfg["earliest_probe_date"], "probe_end": end.isoformat()})
        self.conn.commit()

    def status(self):
        report = {}
        for source, (_, table) in SOURCES.items():
            states = self.day_states(source)
            summary = self.query(f"SELECT COUNT(*) AS versions,MIN(bas_dt) AS first_date,MAX(bas_dt) AS last_date FROM {table}")[0]
            summary = {k: v.isoformat() if isinstance(v, date) else v for k, v in summary.items()}
            observations = self.query("SELECT l.report_json FROM fsc_collection_log l JOIN "
                                      "(SELECT bas_dt,MAX(id) AS id FROM fsc_collection_log WHERE source=%s "
                                      "AND operation='day' AND status IN ('complete','partial','empty') "
                                      "GROUP BY bas_dt) v ON l.id=v.id", (source,))
            flags = Counter()
            for r in observations:
                flags.update(json.loads(r["report_json"]).get("quality", {}))
            pending = [d.isoformat() for d, s in sorted(states.items()) if s["status"] != "complete"]
            report[source] = {**summary, "day_states": dict(Counter(s["status"] for s in states.values())),
                              "quality_counts_latest_saved_days": dict(flags), "pending_dates": pending,
                              "zero_meaning": "unverified; not a delisting determination"}
        self.conn.rollback()
        return report

    def inspect(self, source, day):
        table = SOURCES[source][1]
        rows = self.query(f"SELECT entity_key,revision,payload_json,quality_json,observed_at FROM {table} "
                          "WHERE bas_dt=%s ORDER BY entity_key,revision", (day,))
        for row in rows:
            row["payload"] = json.loads(row.pop("payload_json"))
            row["quality"] = json.loads(row.pop("quality_json"))
            row["observed_at"] = row["observed_at"].isoformat() + "Z"
        self.conn.rollback()
        return {"source": source, "date": day.isoformat(), "records": rows}


def run_collection(api, db, cfg, sources, *, mode, end, start=None, refresh=False, max_days=None, emit=print):
    cfg_hash = digest(cfg)
    totals = Counter({k: 0 for k in ("attempted", "complete", "empty", "partial", "failed", "inserted", "unchanged")})
    for source in sources:
        states = db.day_states(source)
        if mode == "backfill":
            first = start
            if first is None:
                first = None if refresh else db.discovered(source, cfg_hash)
                if first is None:
                    first = discover_first(api, source, end, cfg)
                    db.save_discovery(source, first, cfg_hash, end)
                emit(canonical({"source": source, "verified_first": first.isoformat()}))
            days = [first + timedelta(days=i) for i in range(max(0, (end - first).days + 1))]
            if not refresh:
                days = [d for d in days if d not in states or states[d]["status"] not in {"complete", "empty"}
                        or states[d]["config_hash"] != cfg_hash]
        else:
            recent_start = end - timedelta(days=cfg["lookback_days"] - 1)
            recent = {recent_start + timedelta(days=i) for i in range(cfg["lookback_days"])}
            # 과거 빈 응답도 순환 재검사. 휴장으로 단정하지 않고 7일 초과 게시 지연 복구.
            pending = sorted((s for d, s in states.items() if d <= end and d not in recent and
                              (s["status"] != "complete" or s["config_hash"] != cfg_hash)),
                             key=lambda s: (s["status"] == "empty", s["observed_at"], s["bas_dt"]))
            days = sorted(recent | {s["bas_dt"] for s in pending[:cfg["pending_days_per_update"]]})
        for day in days:
            if max_days is not None and totals["attempted"] >= max_days:
                return {"status": "partial", **dict(totals), "requests": api.requests}
            totals["attempted"] += 1
            batch = uuid.uuid4().hex
            db.begin_day(source, day, batch, cfg_hash)
            try:
                rows, report = fetch_day(api, source, day, cfg)
                stored = db.store_day(source, day, batch, cfg_hash, rows, report)
                totals[stored["status"]] += 1
                totals["inserted"] += stored["inserted"]
                totals["unchanged"] += stored["unchanged"]
                emit(canonical({"source": source, "date": day.isoformat(), **stored}))
            except CollectError as e:
                db.fail_day(source, day, batch, cfg_hash, e.code)
                totals["failed"] += 1
                emit(canonical({"source": source, "date": day.isoformat(), "status": "failed", "error": e.code}))
                if e.fatal:
                    raise
            except BaseException:
                # 커밋 결과 불명도 다음 실행의 버전 대조로 재개. 원래 DB 오류 문자열은 출력하지 않음.
                db.fail_day(source, day, batch, cfg_hash, "interrupted_or_db_failed")
                raise
    return {"status": "failed" if totals["failed"] else "partial" if totals["partial"] else "finished",
            **dict(totals), "requests": api.requests}


def make_parser():
    p = argparse.ArgumentParser(description="금융위 API 3종 Python 수집기 (서버 PC 전용)")
    p.add_argument("--config", type=Path, default=CONFIG_PATH, help="fsc_collection 절을 가진 YAML")
    p.add_argument("--server-pc", action="store_true", help="원본 수집·보관이 허용된 서버 PC임을 확인")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="API 3종·DB·권한 힌트 확인 (DB 쓰기 없음)")
    sub.add_parser("init-db", help="전용 신규 테이블 준비 (기존 불일치 표 변경 없음)")
    for command in ("backfill", "update"):
        q = sub.add_parser(command)
        q.add_argument("--source", nargs="+", choices=list(SOURCES), default=list(SOURCES))
        q.add_argument("--end", help="어제까지의 종료일 (기본 KST 어제)")
        q.add_argument("--max-days", type=int, help="이번 실행에서 시도할 출처×날짜 수 상한")
        if command == "backfill":
            q.add_argument("--start", help="생략 시 최초 제공일 탐색")
            q.add_argument("--refresh", action="store_true", help="완료/빈 날짜도 재조회·최초일 재탐색")
    sub.add_parser("status", help="저장 기간·수집 상태·0값 품질 집계")
    q = sub.add_parser("inspect", help="서버에서 특정 날짜의 실제 원본·품질 확인")
    q.add_argument("--source", choices=list(SOURCES), required=True)
    q.add_argument("--date", required=True)
    sub.add_parser("schedule-info", help="비밀값 없이 예약 실행 수치 출력")
    return p


def main(argv=None):
    args = make_parser().parse_args(argv)
    db = None
    try:
        cfg = load_config(args.config)
        if args.command == "schedule-info":
            print(canonical({k: cfg[k] for k in ("schedule_hour", "schedule_minute", "delayed_retry_minutes", "delayed_retries")}))
            return 0
        if not args.server_pc:
            raise CollectError("server_pc_required", "데이터 서버 PC에서 --server-pc를 명시하세요")
        today = datetime.now(KST).date()
        end = parse_date(args.end) if getattr(args, "end", None) else today - timedelta(days=1)
        start = parse_date(args.start) if getattr(args, "start", None) else None
        if args.command in ("backfill", "update") and (end >= today or (start and start > end)):
            raise CollectError("invalid_range", "수집 범위는 어제까지이며 시작일은 종료일 이하여야 합니다")
        if getattr(args, "max_days", None) is not None and args.max_days < 1:
            raise CollectError("invalid_limit", "max-days는 양수여야 합니다")
        credentials = Credentials.from_env(cfg)
        db = Database.connect(credentials, cfg)
        if args.command == "check":
            report = db.preflight()
            api = ApiClient(credentials.api_key, cfg)
            report["api"] = {}
            for source in SOURCES:
                try:
                    report["api"][source] = {"access": "ok", "rows_available": api.page(source, {}, size=1)["total"]}
                except CollectError as e:
                    report["api"][source] = {"access": "failed", "error": e.code}
            print(canonical(report))
            return 0 if all(r["access"] == "ok" for r in report["api"].values()) else 1
        if args.command == "init-db":
            with db.exclusive():
                print(canonical({"tables": db.initialize()}))
            return 0
        db.schema()
        if args.command == "status":
            print(canonical(db.status()))
            return 0
        if args.command == "inspect":
            print(canonical(db.inspect(args.source, parse_date(args.date))))
            return 0
        with db.exclusive():
            result = run_collection(ApiClient(credentials.api_key, cfg), db, cfg, list(dict.fromkeys(args.source)),
                                    mode=args.command, end=end, start=start,
                                    refresh=getattr(args, "refresh", False), max_days=args.max_days)
        print(canonical(result))
        return 1 if result["status"] == "failed" else 3 if result["status"] == "partial" else 0
    except CollectError as e:
        print(canonical({"status": "failed", "error": e.code, "message": str(e)}), file=sys.stderr)
        return 3 if e.code in {"request_budget", "quota_exceeded"} else 2
    except KeyboardInterrupt:
        print(canonical({"status": "interrupted", "message": "같은 명령으로 재개 가능"}), file=sys.stderr)
        return 130
    except Exception:
        print(canonical({"status": "failed", "error": "collector_failed", "message": "연결·표 정의·권한 확인 필요; 외부 오류 원문은 숨김"}), file=sys.stderr)
        return 2
    finally:
        if db is not None:
            try:
                db.conn.close()
            except Exception:
                pass  # 연결 종료 오류에도 외부 메시지/자격증명을 출력하지 않는다.


if __name__ == "__main__":
    sys.exit(main())
