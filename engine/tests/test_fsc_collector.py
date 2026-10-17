"""Python 수집기: 합성 API와 SQLite에서 동일 저장 SQL의 원자성/재개 검증. 외부 접속 없음."""
from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timezone
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/daily_stock_collection/collect_fsc.py"
spec = importlib.util.spec_from_file_location("fsc_collector_test_module", SCRIPT)
fsc = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = fsc
spec.loader.exec_module(fsc)
DAY = date(2026, 10, 13)
STOCK = {"basDt": "20261013", "srtnCd": "0001A0", "isinCd": "KR7000000001", "itmsNm": "합성 ' 종목",
         "mrktCtg": "KOSPI", "mkp": "100", "hipr": "120", "lopr": "90", "clpr": "110",
         "trqu": "1000", "trPrc": "900719925474099312345", "mrktTotAmt": "999999999999999999999", "lstgStCnt": "10000"}


@pytest.fixture
def cfg():
    return fsc.load_config()


def page(rows, *, total=None, number=1, size=10000):
    return {"response": {"header": {"resultCode": "00"}, "body": {
        "totalCount": len(rows) if total is None else total, "pageNo": number, "numOfRows": size,
        "items": {"item": rows}}}}


class Opener:
    def __init__(self, *outputs):
        self.outputs, self.requests = list(outputs), []

    def open(self, req, timeout):
        self.requests.append((req, timeout))
        output = self.outputs.pop(0)
        if isinstance(output, BaseException):
            raise output
        return io.BytesIO(output if isinstance(output, bytes) else json.dumps(output).encode())


class SyntheticApi:
    """문서상의 필터/날짜/페이지를 구현. 테스트가 전체 반환 건수와 실재 키를 통제한다."""
    def __init__(self, cfg, records=None, inclusive=False):
        self.cfg, self.records, self.inclusive = cfg, records or {}, inclusive
        self.requests, self.calls, self.failures = 0, [], {}

    def page(self, source, params, page=1, size=None):
        self.requests += 1
        self.calls.append((source, params.copy(), page))
        target = (source, params.get("basDt"))
        if target in self.failures:
            raise self.failures.pop(target)
        rows = self.records.get(source, [])
        for key, field in (("mrktCls", "mrktCtg"), ("idxNm", "idxNm")):
            if key in params:
                rows = [r for r in rows if r.get(field) == params[key]]
        if "basDt" in params:
            rows = [r for r in rows if r["basDt"] == params["basDt"]]
        if "endBasDt" in params:
            rows = [r for r in rows if r["basDt"] <= params["endBasDt"]] if self.inclusive else [
                r for r in rows if r["basDt"] < params["endBasDt"]]
        size = size or self.cfg["page_size"]
        return {"total": len(rows), "size": size, "rows": rows[(page - 1) * size:page * size]}


class SqliteDb(fsc.Database):
    """행 저장/조회 SQL을 실제 SQLite 트랜잭션에서 실행. MariaDB 잠금은 대역."""
    def __init__(self, cfg):
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        super().__init__(con, "regime_lab", cfg)
        self.locked = True
        ddl = re.sub(r"--[^\n]*", "", fsc.SCHEMA_PATH.read_text(encoding="utf-8"))
        for statement in ddl.split(";"):
            if not statement.strip():
                continue
            statement = statement.split(" ENGINE=", 1)[0]
            statement = statement.replace("BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")
            statement = re.sub(r",\s*KEY \w+ \([^)]*\)", "", statement)
            statement = re.sub(r"UNIQUE KEY \w+", "UNIQUE", statement)
            self.conn.execute(statement)
        self.conn.commit()
        self.fail_on_insert = None

    def query(self, sql, params=()):
        if "IS_USED_LOCK" in sql:
            return [{"owned": int(self.locked)}]
        if "GET_LOCK" in sql:
            return [{"acquired": int(not self.locked)}]
        if "RELEASE_LOCK" in sql:
            return []
        if self.fail_on_insert and sql.startswith("INSERT INTO " + self.fail_on_insert):
            raise RuntimeError("upstream-password-must-not-leak")
        params = tuple(v.isoformat() if isinstance(v, (date, datetime)) else v for v in params)
        cursor = self.conn.execute(sql.replace("%s", "?"), params)
        rows = [dict(r) for r in cursor.fetchall()] if cursor.description else []
        for row in rows:
            for key in ("bas_dt", "first_date", "last_date"):
                if isinstance(row.get(key), str):
                    row[key] = date.fromisoformat(row[key])
            if isinstance(row.get("observed_at"), str):
                row["observed_at"] = datetime.fromisoformat(row["observed_at"])
        return rows


@pytest.fixture
def db(cfg):
    instance = SqliteDb(cfg)
    yield instance
    instance.conn.close()


def selected_day(cfg, rows=None):
    api = SyntheticApi(cfg, {"stock": rows if rows is not None else [STOCK, {**STOCK, "srtnCd": "000002", "mrktCtg": "KOSDAQ"}]})
    return fsc.fetch_day(api, "stock", DAY, cfg)


def test_config_is_independent_of_analysis(cfg):
    assert cfg["page_size"] == 10000
    assert (cfg["schedule_hour"], cfg["schedule_minute"]) == (13, 30)
    assert cfg["markets"] == ["KOSPI", "KOSDAQ"]


@pytest.mark.parametrize("value", ["20260230", "2026-13-01", "2026/01/01", "x", None])
def test_invalid_dates(value):
    with pytest.raises(fsc.CollectError, match="날짜"):
        fsc.parse_date(value)


def test_key_decode_once_and_no_plus_conversion(cfg, monkeypatch):
    for key, value in {"REGIME_FSC_SERVICE_KEY": "abc%2Bdef%3D", "REGIME_FSC_DB_HOST": "localhost",
                       "REGIME_FSC_DB_USER": "test", "REGIME_FSC_DB_PASSWORD": "secret"}.items():
        monkeypatch.setenv(key, value)
    creds = fsc.Credentials.from_env(cfg)
    assert creds.api_key == "abc+def="
    assert "secret" not in repr(creds)
    opener = Opener(page([], size=1))
    api = fsc.ApiClient(creds.api_key, cfg, opener=opener)
    api.page("stock", {}, size=1)
    parsed = urllib.parse.parse_qs(urllib.parse.urlsplit(opener.requests[0][0].full_url).query)
    assert parsed["serviceKey"] == ["abc+def="]


def test_missing_environment_only_names(cfg, monkeypatch):
    for name in ("REGIME_FSC_SERVICE_KEY", "REGIME_FSC_DB_HOST", "REGIME_FSC_DB_USER", "REGIME_FSC_DB_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(fsc.CollectError, match="REGIME_FSC_SERVICE_KEY"):
        fsc.Credentials.from_env(cfg)


def test_json_numeric_precision_and_single_item():
    response = page([{**STOCK, "trPrc": 900719925474099312345}], size=1)
    response["response"]["body"]["items"]["item"] = response["response"]["body"]["items"]["item"][0]
    result = fsc.decode_page(json.dumps(response).encode(), 1, 1)
    assert result["rows"][0]["trPrc"] == "900719925474099312345"
    raw = b'{"response":{"header":{"resultCode":"00"},"body":{"pageNo":1,"numOfRows":1,"totalCount":1,"items":{"item":{"clpr":1244.990000000001}}}}}'
    assert fsc.decode_page(raw, 1, 1)["rows"][0]["clpr"] == "1244.990000000001"


@pytest.mark.parametrize("mutate", [
    lambda b: b.update(pageNo=2),
    lambda b: b.update(numOfRows=1),
    lambda b: b.update(totalCount=2),
    lambda b: b.update(items={"item": ["invalid"]}),
    lambda b: b.update(totalCount="NaN"),
])
def test_broken_pages_rejected(mutate):
    data = page([STOCK])
    mutate(data["response"]["body"])
    with pytest.raises(fsc.CollectError):
        fsc.decode_page(json.dumps(data).encode(), 1, 10000)


@pytest.mark.parametrize("raw", [b"not-json", b"[]", b'{"response":{"header":{"resultCode":"00"},"body":null}}', b'{"value":NaN}'])
def test_invalid_response(raw):
    with pytest.raises(fsc.CollectError):
        fsc.decode_page(raw, 1, 10000)


def test_gateway_xml_and_message_secret_never_propagated():
    with pytest.raises(fsc.CollectError) as caught:
        fsc.decode_page(b"<OpenAPI_ServiceResponse><cmmMsgHeader><returnReasonCode>30</returnReasonCode><errMsg>secret</errMsg></cmmMsgHeader></OpenAPI_ServiceResponse>", 1, 10000)
    assert caught.value.fatal
    assert "secret" not in str(caught.value)


def test_retry_then_success_and_budget(cfg):
    cfg = {**cfg, "max_requests_per_run": 2}
    waits = []
    opener = Opener(urllib.error.URLError("key=secret"), page([], size=1))
    api = fsc.ApiClient("secret", cfg, opener=opener, sleep=waits.append, monotonic=lambda: 0)
    assert api.page("stock", {}, size=1)["total"] == 0
    assert api.requests == 2 and waits
    with pytest.raises(fsc.CollectError) as caught:
        api.page("stock", {}, size=1)
    assert caught.value.code == "request_budget" and "secret" not in str(caught.value)


def test_quota_does_not_loop(cfg):
    opener = Opener({"response": {"header": {"resultCode": "22", "resultMsg": "secret"}}})
    api = fsc.ApiClient("secret", cfg, opener=opener)
    with pytest.raises(fsc.CollectError) as caught:
        api.page("stock", {})
    assert caught.value.code == "quota_exceeded" and len(opener.requests) == 1


def test_redirect_and_response_size(cfg):
    with pytest.raises(fsc.CollectError):
        fsc.NoRedirect().redirect_request(None, None, 302, "secret", {}, "https://other.invalid?key=secret")
    api = fsc.ApiClient("secret", {**cfg, "max_response_bytes": 3}, opener=Opener(b"1234"))
    with pytest.raises(fsc.CollectError) as caught:
        api.page("stock", {})
    assert caught.value.code == "response_too_large"


def test_all_markets_pages_and_zero_preserved(cfg):
    cfg = {**cfg, "page_size": 1}
    zero = {**STOCK, "srtnCd": "000003", "mkp": "0", "hipr": "0", "lopr": "0", "clpr": "0", "trqu": "0"}
    rows, report = selected_day(cfg, [STOCK, zero, {**STOCK, "srtnCd": "000002", "mrktCtg": "KOSDAQ"}])
    assert report["selected_rows"] == 3
    assert report["quality"]["all_ohlc_zero"] == 1
    assert rows[1]["payload"]["clpr"] == "0"
    assert STOCK["trPrc"] in fsc.canonical(rows)
    assert not report["missing_conditions"]


def test_quality_does_not_repair_values():
    row = {**STOCK, "hipr": "50", "trPrc": "-1", "lstgStCnt": "1.5", "clpr": None, "trqu": "bad"}
    flags = fsc.quality("stock", row)
    assert {"missing_clpr", "negative_trPrc", "noninteger_lstgStCnt", "invalid_trqu"} <= set(flags)
    assert row["hipr"] == "50" and row["clpr"] is None
    assert "ohlc_order" in fsc.quality("stock", {**STOCK, "hipr": "50"})


def test_listed_prefix_foreign_and_konex(cfg):
    row = {"basDt": "20261013", "srtnCd": "A000040", "isinCd": "KR7000040006", "mrktCtg": "KOSPI", "itmsNm": "합성", "crno": ""}
    api = SyntheticApi(cfg, {"listed": [row, {**row, "srtnCd": "A000041", "mrktCtg": "KONEX"}]})
    rows, report = fsc.fetch_day(api, "listed", DAY, cfg)
    assert len(rows) == 1 and rows[0]["entity"] == "A000040"
    assert report["api_rows"] == 2 and report["quality"]["missing_corporate_number"] == 1


def test_only_exact_representative_indexes(cfg):
    records = [{"basDt": "20261013", "idxNm": name, "idxCsf": name + "시리즈", "mkp": "1233.15",
                "hipr": "1265.85", "lopr": "1225.89", "clpr": "1244.99", "trqu": "1", "trPrc": "999999999999999999999"}
               for name in ["코스피", "코스닥", "코스피 200"]]
    rows, report = fsc.fetch_day(SyntheticApi(cfg, {"index": records}), "index", DAY, cfg)
    assert len(rows) == 2 and {r["payload"]["idxNm"] for r in rows} == {"코스피", "코스닥"}
    assert not report["quality"]


@pytest.mark.parametrize("problem", ["date", "duplicate", "filter", "count"])
def test_daily_integrity(cfg, problem):
    api = SyntheticApi(cfg, {"stock": [STOCK]})
    original = api.page
    def broken(source, params, page=1, size=None):
        result = original(source, params, page, size)
        if result["rows"]:
            if problem == "date":
                result["rows"] = [{**STOCK, "basDt": "20261012"}]
            elif problem == "duplicate":
                result.update(total=2, rows=[STOCK, STOCK])
            elif problem == "filter":
                result["rows"] = [{**STOCK, "mrktCtg": "KONEX"}]
            else:
                result["total"] = cfg["max_rows_per_day"] + 1
        return result
    api.page = broken
    with pytest.raises(fsc.CollectError):
        fsc.fetch_day(api, "stock", DAY, cfg)


@pytest.mark.parametrize("inclusive", [False, True])
def test_discovery_does_not_assume_end_boundary(cfg, inclusive):
    cfg = {**cfg, "earliest_probe_date": "2020-01-01"}
    first = {**STOCK, "basDt": "20200102"}
    api = SyntheticApi(cfg, {"stock": [first, STOCK]}, inclusive=inclusive)
    assert fsc.discover_first(api, "stock", DAY, cfg) == date(2020, 1, 2)


def test_discovery_other_market_first_and_no_data(cfg):
    cfg = {**cfg, "earliest_probe_date": "2020-01-01"}
    records = [{**STOCK, "basDt": "20200102", "mrktCtg": "KONEX"}, {**STOCK, "basDt": "20200103"}]
    assert fsc.discover_first(SyntheticApi(cfg, {"listed": records}), "listed", DAY, cfg) == date(2020, 1, 3)
    with pytest.raises(fsc.CollectError) as caught:
        fsc.discover_first(SyntheticApi(cfg), "stock", DAY, cfg)
    assert caught.value.code == "source_empty"


def test_append_duplicate_and_a_b_a_correction(cfg, db):
    cfg_hash = fsc.digest(cfg)
    for rev, close in [(1, "110"), (1, "110"), (2, "111"), (3, "110")]:
        rows, report = selected_day(cfg, [{**STOCK, "clpr": close}])
        batch = fsc.uuid.uuid4().hex
        db.begin_day("stock", DAY, batch, cfg_hash)
        result = db.store_day("stock", DAY, batch, cfg_hash, rows, report)
        stored = db.query("SELECT revision,payload_json FROM fsc_stock_daily_raw ORDER BY revision")
        assert len(stored) == rev
    assert result["inserted"] == 1
    assert [json.loads(r["payload_json"])["clpr"] for r in stored] == ["110", "111", "110"]
    assert db.inspect("stock", DAY)["records"][-1]["payload"]["trPrc"] == STOCK["trPrc"]


def test_data_and_complete_log_roll_back_together(cfg, db):
    rows, report = selected_day(cfg)
    batch, ch = "a" * 32, fsc.digest(cfg)
    db.begin_day("stock", DAY, batch, ch)
    db.fail_on_insert = "fsc_collection_log"
    with pytest.raises(RuntimeError):
        db.store_day("stock", DAY, batch, ch, rows, report)
    assert db.query("SELECT * FROM fsc_stock_daily_raw") == []
    assert db.day_states("stock")[DAY]["status"] == "started"
    db.fail_on_insert = None
    db.fail_day("stock", DAY, batch, ch, "db_failed")
    assert db.day_states("stock")[DAY]["status"] == "failed"


def test_source_key_disappearance_keeps_last_good_version(cfg, db):
    rows, report = selected_day(cfg)
    db.store_day("stock", DAY, "a" * 32, fsc.digest(cfg), rows, report)
    one, one_report = selected_day(cfg, [STOCK])
    with pytest.raises(fsc.CollectError) as caught:
        db.store_day("stock", DAY, "b" * 32, fsc.digest(cfg), one, one_report)
    assert caught.value.code == "source_rows_disappeared"
    assert len(db.query("SELECT * FROM fsc_stock_daily_raw")) == 2


def test_lost_db_lock_blocks_all_writes(cfg, db):
    rows, report = selected_day(cfg)
    db.locked = False
    with pytest.raises(fsc.CollectError):
        db.store_day("stock", DAY, "a" * 32, fsc.digest(cfg), rows, report)
    assert db.query("SELECT * FROM fsc_stock_daily_raw") == []


def test_resume_and_partial_market_state(cfg, db):
    records = [STOCK, {**STOCK, "srtnCd": "000002", "mrktCtg": "KOSDAQ"}]
    api = SyntheticApi(cfg, {"stock": records})
    options = dict(mode="backfill", start=DAY - fsc.timedelta(days=1), end=DAY, emit=lambda _: None)
    first = fsc.run_collection(api, db, cfg, ["stock"], max_days=1, **options)
    assert first["status"] == "partial"
    second = fsc.run_collection(api, db, cfg, ["stock"], **options)
    assert second["attempted"] == 1 and second["inserted"] == 2
    third = fsc.run_collection(api, db, cfg, ["stock"], **options)
    assert third["attempted"] == 0
    assert db.status()["stock"]["pending_dates"] == [(DAY - fsc.timedelta(days=1)).isoformat()]


def test_old_empty_and_failed_dates_revisited_by_update(cfg, db):
    old = DAY - fsc.timedelta(days=20)
    record = {**STOCK, "basDt": old.strftime("%Y%m%d")}
    db.log("stock", "day", old, "a" * 32, "empty", fsc.digest(cfg), {})
    db.conn.commit()
    api = SyntheticApi(cfg, {"stock": [record]})
    result = fsc.run_collection(api, db, cfg, ["stock"], mode="update", end=DAY, emit=lambda _: None)
    assert result["attempted"] == cfg["lookback_days"] + 1
    assert db.day_states("stock")[old]["status"] == "partial"  # KOSDAQ 미게시
    assert db.status()["stock"]["first_date"] == old.isoformat()


def test_date_error_does_not_fake_success_and_can_resume(cfg, db):
    api = SyntheticApi(cfg, {"stock": [STOCK]})
    api.failures[("stock", "20261013")] = fsc.CollectError("network_error", "고정 오류")
    result = fsc.run_collection(api, db, cfg, ["stock"], mode="backfill", start=DAY, end=DAY, emit=lambda _: None)
    assert result["status"] == "failed" and db.day_states("stock")[DAY]["status"] == "failed"
    result = fsc.run_collection(api, db, cfg, ["stock"], mode="backfill", start=DAY, end=DAY, emit=lambda _: None)
    assert result["inserted"] == 1


def test_partial_market_is_saved_without_claiming_complete(cfg, db):
    rows, report = selected_day(cfg, [STOCK])
    result = db.store_day("stock", DAY, "a" * 32, fsc.digest(cfg), rows, report)
    assert result["status"] == "partial"
    assert db.day_states("stock")[DAY]["status"] == "partial"
    assert result["missing_conditions"] == [{"mrktCls": "KOSDAQ"}]


def test_changed_configuration_rechecks_prior_complete_dates(cfg, db):
    records = [STOCK, {**STOCK, "srtnCd": "000002", "mrktCtg": "KOSDAQ"}]
    api = SyntheticApi(cfg, {"stock": records})
    options = dict(mode="backfill", start=DAY, end=DAY, emit=lambda _: None)
    assert fsc.run_collection(api, db, cfg, ["stock"], **options)["inserted"] == 2
    changed = {**cfg, "page_size": 1}
    result = fsc.run_collection(SyntheticApi(changed, {"stock": records}), db, changed, ["stock"], **options)
    assert result["attempted"] == 1 and result["unchanged"] == 2


def test_failed_recheck_does_not_hide_saved_zero_quality(cfg, db):
    zero = {**STOCK, "mkp": "0", "hipr": "0", "lopr": "0", "clpr": "0", "trqu": "0"}
    rows, report = selected_day(cfg, [zero])
    db.store_day("stock", DAY, "a" * 32, fsc.digest(cfg), rows, report)
    db.fail_day("stock", DAY, "b" * 32, fsc.digest(cfg), "network_error")
    status = db.status()["stock"]
    assert status["quality_counts_latest_saved_days"]["all_ohlc_zero"] == 1
    assert status["day_states"] == {"failed": 1}


def test_future_rows_do_not_change_prior_day_payloads(cfg):
    before = fsc.fetch_day(SyntheticApi(cfg, {"stock": [STOCK]}), "stock", DAY, cfg)
    after = fsc.fetch_day(SyntheticApi(cfg, {"stock": [STOCK, {**STOCK, "basDt": "20261014", "clpr": "999"}]}), "stock", DAY, cfg)
    assert before == after


def test_changing_total_on_next_page_rejected(cfg):
    cfg = {**cfg, "page_size": 1}
    records = [STOCK, {**STOCK, "srtnCd": "000002"}]
    api = SyntheticApi(cfg, {"stock": records})
    original = api.page
    def changing(source, params, page=1, size=None):
        result = original(source, params, page, size)
        if page == 2:
            result["total"] += 1
        return result
    api.page = changing
    with pytest.raises(fsc.CollectError) as caught:
        fsc.fetch_day(api, "stock", DAY, cfg)
    assert caught.value.code == "changing_pages"


@pytest.mark.parametrize("command", ["backfill", "update"])
def test_future_date_rejected_before_any_connection(command, capsys):
    assert fsc.main(["--server-pc", command, "--end", "9999-12-31"]) == 2
    assert "invalid_range" in capsys.readouterr().err


def test_preflight_does_not_print_grants_or_credentials(cfg):
    db = fsc.Database(None, "regime_lab", cfg)
    class Connection:
        def rollback(self):
            pass
    db.conn = Connection()
    db.schema = lambda **kwargs: {"fsc_stock_daily_raw": "missing"}
    db.query = lambda sql: ([{"selected_db": "regime_lab", "version": "10.11.19-MariaDB"}]
                            if "DATABASE()" in sql else [{"Grants": "GRANT SELECT, INSERT ON other_db.* TO user@host IDENTIFIED BY PASSWORD 'secret'"}])
    report = db.preflight()
    assert report["insert_grant_hint"] and not report["grant_scope_verified"]
    assert "secret" not in fsc.canonical(report) and "user@host" not in fsc.canonical(report)


def test_schema_rejects_wrong_columns_before_any_ddl(cfg):
    db = fsc.Database(None, "regime_lab", cfg)
    calls = []
    def query(sql, params=()):
        calls.append(sql)
        if "information_schema.TABLES" in sql:
            return [{"engine": "InnoDB", "collation_name": "utf8mb4_bin"}]
        return [{"name": "not_the_right_column", "type_name": "bigint", "extra": ""}]
    db.query = query
    with pytest.raises(fsc.CollectError) as caught:
        db.initialize()
    assert caught.value.code == "schema_mismatch"
    assert not any("CREATE TABLE" in sql for sql in calls)


def test_session_lock_contention_rejected(cfg):
    db = fsc.Database(None, "regime_lab", cfg)
    class Connection:
        def commit(self):
            pass
    db.conn = Connection()
    db.query = lambda *args: [{"acquired": 0}]
    with pytest.raises(fsc.CollectError) as caught:
        with db.exclusive():
            pytest.fail("다른 수집기 실행 중에는 진입 불가")
    assert caught.value.code == "collector_busy"


def test_actual_driver_options_and_strict_mode(cfg, monkeypatch):
    options = {}
    class Driver:
        class cursors:
            DictCursor = object
        @staticmethod
        def connect(**kwargs):
            options.update(kwargs)
            return object()
    monkeypatch.setitem(sys.modules, "pymysql", Driver)
    creds = fsc.Credentials("secret-key", "localhost", "test", "secret-password", "regime_lab", 3306, "/ca.pem")
    fsc.Database.connect(creds, cfg)
    assert options["autocommit"] is False
    assert options["ssl_verify_cert"] and options["ssl_verify_identity"]
    assert "STRICT_ALL_TABLES" in options["init_command"]
    assert "secret" not in options["init_command"]


@pytest.mark.parametrize("bad_type", [False, True])
def test_schema_requires_exact_types_and_unique_keys(cfg, bad_type):
    db = fsc.Database(None, "regime_lab", cfg)
    raw = {"id": "bigint(20) unsigned", "bas_dt": "date", "entity_key": "varchar(191)", "revision": "int(10) unsigned",
           "code": "varchar(32)", "isin": "varchar(12)", "label": "varchar(240)", "market": "varchar(32)",
           "payload_hash": "char(64)", "payload_json": "longtext", "quality_json": "longtext",
           "observed_at": "datetime(6)", "batch_id": "char(32)"}
    logs = {"id": "bigint(20) unsigned", "bas_dt": "date", "batch_id": "char(32)", "source": "varchar(16)",
            "operation": "varchar(16)", "status": "varchar(24)", "observed_at": "datetime(6)",
            "config_hash": "char(64)", "report_json": "longtext"}
    def query(sql, params=()):
        table = params[-1]
        if "information_schema.TABLES" in sql:
            return [{"engine": "InnoDB", "collation_name": "utf8mb4_bin"}]
        if "information_schema.COLUMNS" in sql:
            types = logs if table == "fsc_collection_log" else raw
            return [{"name": key, "type_name": "varchar(4)" if bad_type and key == "payload_hash" else kind,
                     "extra": "auto_increment" if key == "id" else ""} for key, kind in types.items()]
        return [{"name": "required", "column_name": c, "position": i, "non_unique": 0}
                for i, c in enumerate(["id"] if table == "fsc_collection_log" else ["bas_dt", "entity_key", "revision"], 1)]
    db.query = query
    if bad_type:
        with pytest.raises(fsc.CollectError, match="자료형"):
            db.schema()
    else:
        assert set(db.schema().values()) == {"ready"}


def test_cli_without_credentials_and_server_flag_is_safe(monkeypatch, capsys):
    assert fsc.main(["schedule-info"]) == 0
    assert fsc.main(["status"]) == 2
    assert "server_pc_required" in capsys.readouterr().err
    monkeypatch.delenv("REGIME_FSC_SERVICE_KEY", raising=False)
    assert fsc.main(["--server-pc", "check"]) == 2
    assert "missing_environment" in capsys.readouterr().err


def test_raw_external_exception_is_hidden(monkeypatch, capsys):
    for name in ("REGIME_FSC_SERVICE_KEY", "REGIME_FSC_DB_HOST", "REGIME_FSC_DB_USER", "REGIME_FSC_DB_PASSWORD"):
        monkeypatch.setenv(name, "NEVER_PRINT_THIS")
    def crash(*args):
        raise RuntimeError("password=NEVER_PRINT_THIS")
    monkeypatch.setattr(fsc.Database, "connect", crash)
    assert fsc.main(["--server-pc", "check"]) == 2
    captured = capsys.readouterr()
    assert "NEVER_PRINT_THIS" not in captured.out + captured.err


@pytest.mark.skipif(sys.platform != "win32" or not shutil.which("powershell"), reason="Windows PowerShell 필요")
def test_scheduler_print_only_xml_and_script_syntax():
    script = SCRIPT.with_name("install_task.ps1")
    completed = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                                "-PrintOnly", "-PythonPath", sys.executable], capture_output=True, timeout=30)
    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
    output = completed.stdout.decode("utf-8-sig", errors="replace")
    xml_start = output.index("<?xml")
    root = ET.fromstring(output[xml_start:])
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    boundary = root.findtext("t:Triggers/t:CalendarTrigger/t:StartBoundary", namespaces=ns)
    assert boundary.endswith("T13:30:00+09:00")
    assert root.findtext("t:Triggers/t:CalendarTrigger/t:Repetition/t:Interval", namespaces=ns) == "PT30M"
    assert root.findtext("t:Settings/t:MultipleInstancesPolicy", namespaces=ns) == "IgnoreNew"
    assert "--server-pc update" in root.findtext("t:Actions/t:Exec/t:Arguments", namespaces=ns)
    assert "SERVICE_KEY" not in output and "DB_PASSWORD" not in output


@pytest.mark.skipif(sys.platform != "win32" or not shutil.which("powershell"), reason="Windows PowerShell 필요")
def test_custom_config_is_used_for_schedule_and_action(cfg, tmp_path):
    custom_path = tmp_path / "config & alternate.yaml"
    custom_path.write_text(json.dumps({"fsc_collection": {**cfg, "schedule_hour": 9, "schedule_minute": 10}}), encoding="utf-8")
    completed = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT.with_name("install_task.ps1")),
                                "-PrintOnly", "-PythonPath", sys.executable, "-ConfigPath", str(custom_path)],
                               capture_output=True, timeout=30)
    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
    output = completed.stdout.decode("utf-8-sig", errors="replace")
    root = ET.fromstring(output[output.index("<?xml"):])
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    assert root.findtext("t:Triggers/t:CalendarTrigger/t:StartBoundary", namespaces=ns).endswith("T09:10:00+09:00")
    assert '--config "' + str(custom_path) + '"' in root.findtext("t:Actions/t:Exec/t:Arguments", namespaces=ns)

