"""독립 수집기의 합성 API·실제 저장 SQL/트랜잭션·메타 계약 검사. 실제 서버/키 사용 없음."""
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
import urllib.error
import zipfile

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/fundamental_collection/collect_fundamentals.py'
spec = importlib.util.spec_from_file_location('fundamental_collector_tests', SCRIPT)
f = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = f
spec.loader.exec_module(f)
NOW = datetime(2026, 10, 17, 5, 30, tzinfo=timezone.utc)
CORP = '00126380'
RECEIPT = '20251013000001'


@pytest.fixture
def cfg():
    return f.load_config()


class Cursor:
    def __init__(self, owner):
        self.owner, self.rows, self.description = owner, None, None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, sql, params=()):
        if 'GET_LOCK' in sql:
            self.owner.owned = True
            self.rows = [{'acquired': 1}]
        elif 'IS_USED_LOCK' in sql:
            self.rows = [{'owned': int(self.owner.owned)}]
        elif 'RELEASE_LOCK' in sql:
            self.owner.owned = False
            self.rows = []
        else:
            if self.owner.fail and self.owner.fail(sql, params):
                raise sqlite3.OperationalError('synthetic storage failure')
            values = tuple(v.isoformat(sep=' ', timespec='microseconds') if isinstance(v, datetime)
                           else v.isoformat() if isinstance(v, date) else v for v in params)
            cur = self.owner.raw.execute(sql.replace('%s', '?'), values)
            self.description = cur.description
            self.rows = [dict(r) for r in cur.fetchall()] if cur.description else []
            return
        self.description = ('synthetic',)

    def fetchall(self):
        return self.rows


class Connection:
    def __init__(self):
        self.raw = sqlite3.connect(':memory:')
        self.raw.row_factory = sqlite3.Row
        self.raw.create_function('JSON_VALUE', 2, lambda raw, path: json.loads(raw).get(path[2:]))
        self.owned, self.fail = False, None
        for table in f.RAW_TABLES:
            columns = ','.join(k + (' INTEGER PRIMARY KEY AUTOINCREMENT' if k == 'id' else
                                   ' INTEGER' if 'int' in v else ' TEXT') for k, v in f.RAW_TYPES.items())
            self.raw.execute(f'CREATE TABLE {table} ({columns}, UNIQUE(source,entity_key,revision))')
        columns = ','.join(k + (' INTEGER PRIMARY KEY AUTOINCREMENT' if k == 'id' else ' TEXT') for k in f.LOG_TYPES)
        self.raw.execute(f'CREATE TABLE fund_collection_log ({columns})')
        self.raw.commit()

    def cursor(self):
        return Cursor(self)

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        self.raw.close()


@pytest.fixture
def db():
    connection = Connection()
    d = f.Database(connection, 'synthetic', now=lambda: NOW)
    with d.exclusive():
        yield d
    connection.close()


def disclosure(**kw):
    return dict(corp_code=CORP, stock_code='005930', corp_cls='Y', rcept_no=RECEIPT, rcept_dt='20251013',
                report_nm='주요사항보고서(자기주식취득결정)', corp_name='합성회사', **kw)


def list_work():
    return f.Work('list', {'bgn_de': '20251001', 'end_de': '20251031', 'corp_cls': 'Y', 'last_reprt_at': 'N'})


def save(db, work, docs, cfg_hash='c'):
    batch = 'a' * 32
    db.begin(work, cfg_hash, batch)
    return db.save(work, docs, cfg_hash, batch)


def test_storage_exact_latest_versions_and_times(db):
    work = list_work()
    original = f.records(work, [disclosure()])
    assert save(db, work, original)['inserted'] == 1
    assert save(db, work, original)['inserted'] == 0
    changed = [dict(disclosure(), report_nm='[기재정정] 새 제목')]
    assert save(db, work, f.records(work, changed))['inserted'] == 1
    assert save(db, work, original)['inserted'] == 1
    rows = db.query('SELECT revision,available_at,source_published_at,payload_json FROM fund_disclosure_raw ORDER BY id')
    assert [int(r['revision']) for r in rows] == [1, 2, 3]
    assert all(r['available_at'].startswith('2026-10-17') and r['source_published_at'] is None for r in rows)
    assert json.loads(rows[-1]['payload_json']) == disclosure()


def test_raw_and_complete_log_rollback_together(db):
    work = list_work()
    db.begin(work, 'c', 'b' * 32)
    db.conn.fail = lambda sql, p: 'INSERT INTO fund_collection_log' in sql and p[4] == 'complete'
    with pytest.raises(sqlite3.OperationalError):
        db.save(work, f.records(work, [disclosure()]), 'c', 'b' * 32)
    assert db.query('SELECT COUNT(*) AS n FROM fund_disclosure_raw')[0]['n'] == 0
    assert db.query('SELECT status FROM fund_collection_log') == [{'status': 'started'}]
    db.conn.fail = None
    assert save(db, work, f.records(work, [disclosure()]))['inserted'] == 1


def test_disappeared_rows_are_not_a_normal_empty(db):
    w = list_work()
    save(db, w, f.records(w, [disclosure()]))
    with pytest.raises(f.CollectError, match='source_rows_disappeared'):
        save(db, w, [])
    assert db.query('SELECT COUNT(*) AS n FROM fund_disclosure_raw')[0]['n'] == 1


def test_config_hash_and_pending_resume(db, cfg):
    w = list_work()
    save(db, w, f.records(w, [disclosure()]))
    assert db.state(w, 'c')['status'] == 'complete'
    assert db.state(w, 'different') is None
    db.begin(w, 'c', 'd' * 32)
    assert db.pending('dart', 10) == [w]
    db.fail(w, 'c', 'd' * 32, 'synthetic')
    assert db.pending('dart', 10) == [w]


def test_lock_loss_stops_before_write(db):
    db.conn.owned = False
    with pytest.raises(f.CollectError, match='lock_lost'):
        save(db, list_work(), [])
    assert db.query('SELECT COUNT(*) AS n FROM fund_disclosure_raw')[0]['n'] == 0


def test_budget_is_persistent_and_counts_before_dispatch(db, cfg):
    cfg['dart_daily_requests'] = 2
    b = f.Budget(db, cfg, 'c', 5)
    b.reserve('dart')
    b.reserve('dart')
    with pytest.raises(f.CollectError, match='dart_daily_budget'):
        f.Budget(db, cfg, 'c').reserve('dart')
    assert db.query("SELECT COUNT(*) AS n FROM fund_collection_log WHERE operation='request'")[0]['n'] == 2


def test_budget_kst_day_boundary(db, cfg):
    cfg['dart_daily_requests'] = 1
    db.now = lambda: datetime(2026, 10, 16, 14, 59, tzinfo=timezone.utc)
    f.Budget(db, cfg, 'c').reserve('dart')
    db.now = lambda: datetime(2026, 10, 16, 15, 0, tzinfo=timezone.utc)
    f.Budget(db, cfg, 'c').reserve('dart')


class Opener:
    def __init__(self, outputs):
        self.outputs, self.urls = list(outputs), []

    def open(self, request, timeout):
        self.urls.append(request.full_url)
        result = self.outputs.pop(0)
        if isinstance(result, Exception):
            raise result
        return io.BytesIO(result if isinstance(result, bytes) else json.dumps(result).encode())


def api(cfg, outputs):
    opener = Opener(outputs)
    calls = []
    transport = f.Transport(cfg, calls.append, opener=opener, sleep=lambda _: None)
    return f.Api(cfg, transport, 'd' * 40, 'e' * 20), opener, calls


def test_numbers_keep_original_digits(cfg):
    a, _, _ = api(cfg, [b'{"status":"000","x":900719925474099312345,"y":0.1234567890123456789}'])
    result = a.dart('company', {'corp_code': CORP})
    assert result['x'] == '900719925474099312345'
    assert result['y'] == '0.1234567890123456789'


@pytest.mark.parametrize('body,code', [({'status': '020', 'message': 'SECRET'}, 'dart_quota_exceeded'),
                                     ({'status': '010', 'message': 'SECRET'}, 'dart_access_denied'),
                                     ({'status': '000'}, 'invalid_dart_rows')])
def test_dart_error_is_not_empty_or_secret(cfg, body, code):
    a, _, _ = api(cfg, [body])
    with pytest.raises(f.CollectError, match=code) as error:
        a.dart_rows(f.Work('cvbdIsDecsn', {'corp_code': CORP, 'bgn_de': '20250101', 'end_de': '20251231'}))
    assert 'SECRET' not in str(error.value)


def test_dart_pagination_and_duplicates(cfg):
    cfg['page_size'] = 1
    row = disclosure()
    body = lambda n, r: dict(status='000', total_count=2, total_page=2, page_no=n, list=[r])
    a, _, calls = api(cfg, [body(1, row), body(2, {**row, 'rcept_no': '20251013000002'})])
    assert len(a.dart_rows(list_work())) == 2 and len(calls) == 2
    a, _, _ = api(cfg, [body(1, row), body(2, row)])
    with pytest.raises(f.CollectError, match='duplicate_receipt'):
        a.dart_rows(list_work())


@pytest.mark.parametrize('second', [dict(status='013'), dict(status='000', total_count=3, total_page=3, page_no=2, list=[]),
                                    dict(status='000', total_count=2, total_page=2, page_no=2, list=[])])
def test_changed_or_missing_page_fails(cfg, second):
    cfg['page_size'] = 1
    a, _, _ = api(cfg, [dict(status='000', total_count=2, total_page=2, page_no=1, list=[disclosure()]), second])
    with pytest.raises(f.CollectError):
        a.dart_rows(list_work())


def test_transport_retries_reserve_each_attempt_and_redacts(cfg):
    a, _, calls = api(cfg, [urllib.error.URLError('SECRET'), dict(status='013')])
    assert a.dart('list', {}) is None
    assert calls == ['dart', 'dart']
    a, _, _ = api(cfg, [urllib.error.HTTPError('SECRET', 403, 'SECRET', {}, None)])
    with pytest.raises(f.CollectError, match='api_access_denied') as error:
        a.dart('list', {})
    assert 'SECRET' not in str(error.value)


def test_response_size_and_redirect(cfg):
    cfg['max_response_bytes'] = 2
    a, _, _ = api(cfg, [b'big'])
    with pytest.raises(f.CollectError, match='response_too_large'):
        a.dart('list', {})
    with pytest.raises(f.CollectError, match='redirect_refused'):
        f.NoRedirect().redirect_request(None, None, 302, 'SECRET', {}, 'https://example.invalid')


def zipped(xml, filename='CORPCODE.xml'):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr(filename, xml)
    return buf.getvalue()


def test_corp_zip_keeps_empty_stock_and_extra_fields(cfg):
    data = zipped('<result><list><corp_code>00126380</corp_code><stock_code></stock_code>'
                  '<modify_date>20251013</modify_date><extra>유지</extra></list></result>')
    a, _, _ = api(cfg, [data])
    result = f.records(f.Work('corpCode', {}), a.corp_codes())
    assert result[0].stock is None and result[0].payload['extra'] == '유지'


@pytest.mark.parametrize('raw', [b'not a zip', zipped('<!DOCTYPE x><result/>'), zipped('<result/>', '../../CORPCODE.xml'),
                               zipped('<result>')])
def test_unsafe_zip_and_xml_are_rejected(cfg, raw):
    a, _, _ = api(cfg, [raw])
    with pytest.raises(f.CollectError):
        a.corp_codes()


def test_financial_preserves_duplicate_accounts_and_fiscal_basis():
    p = dict(corp_code=CORP, bsns_year='2025', reprt_code='11012', fs_div='CFS')
    rows = [dict(corp_code=CORP, bsns_year='2025', reprt_code='11012', rcept_no=RECEIPT,
                 account_nm='이름같음', thstrm_amount='0', thstrm_add_amount='900719925474099312345'),
            dict(corp_code=CORP, bsns_year='2025', reprt_code='11012', rcept_no=RECEIPT,
                 account_nm='이름같음', thstrm_amount='-42', thstrm_add_amount='')]
    result = f.records(f.Work('fnlttSinglAcntAll', p), rows)
    assert len(result) == 1 and result[0].payload == rows and result[0].identity['fs_div'] == 'CFS'
    assert 'historical_original_unverified' in result[0].flags


def test_financial_filter_and_receipt_conflicts():
    w = f.Work('fnlttSinglAcntAll', dict(corp_code=CORP, bsns_year='2025', reprt_code='11011', fs_div='CFS'))
    with pytest.raises(f.CollectError, match='financial_filter_mismatch'):
        f.records(w, [dict(corp_code='99999999')])


def test_event_groups_multiple_rows_of_receipt():
    w = f.Work('tsstkAqDecsn', dict(corp_code=CORP, bgn_de='20251001', end_de='20251031'))
    rows = [dict(corp_code=CORP, rcept_no=RECEIPT, amount='1'), dict(corp_code=CORP, rcept_no=RECEIPT, amount='2')]
    assert f.records(w, rows)[0].payload == rows


@pytest.mark.parametrize('field,value', [('rcept_dt', '20251101'), ('corp_cls', 'K'), ('corp_code', 'short')])
def test_disclosure_scope_validation(field, value):
    with pytest.raises(f.CollectError):
        f.records(list_work(), [{**disclosure(), field: value}])


def test_longer_event_names_win(cfg):
    assert f.event_api('主要事項 회사분할합병결정', cfg['events']) == 'cmpDvmgDecsn'
    assert f.event_api('주요사항보고서(자기주식취득신탁계약체결결정)', cfg['events']) == 'tsstkAqTrctrCnsDecsn'


def metadata(cfg):
    s = cfg['ecos_series'][0]
    table = dict(STAT_CODE=s['stat_code'], CYCLE='D', SRCH_YN='Y', STAT_NAME='기준금리')
    item = dict(STAT_CODE=s['stat_code'], CYCLE='D', ITEM_CODE='0101000', ITEM_NAME=s['names'][0],
                UNIT_NAME='연%', START_TIME='20100101', END_TIME='20261016', ITEM_GRP_CODE='Group1')
    return s, table, item


def test_ecos_resolve_names_units_period_and_dimensions(cfg):
    s, t, i = metadata(cfg)
    assert f.resolve_series(s, [t], [i])['first'] == date(2010, 1, 1)
    for invalid in ([i, {**i, 'ITEM_CODE': 'different'}], [{**i, 'UNIT_NAME': '원'}],
                    [i, {**i, 'ITEM_GRP_CODE': 'Group2'}], [{**i, 'START_TIME': '99999999'}]):
        with pytest.raises(f.CollectError):
            f.resolve_series(s, [t], invalid)


def test_ecos_pages_and_error_are_validated(cfg):
    w = f.Work('StatisticItemList', {'stat_code': '722Y001'})
    a, _, _ = api(cfg, [{'RESULT': {'CODE': 'INFO-100', 'MESSAGE': 'SECRET'}}])
    with pytest.raises(f.CollectError, match='ecos_access_denied'):
        a.ecos_rows(w)
    a, _, _ = api(cfg, [{'StatisticItemList': {'list_total_count': '2', 'row': [{}]}}])
    with pytest.raises(f.CollectError, match='incomplete_ecos_page'):
        a.ecos_rows(w)


def test_ecos_keeps_zero_missing_and_refuses_wrong_item():
    w = f.Work('StatisticSearch', dict(stat_code='722Y001', cycle='D', start='20251001', end='20251031', item_code='0101000'))
    row = dict(STAT_CODE='722Y001', ITEM_CODE1='0101000', TIME='20251013', DATA_VALUE='0', UNIT_NAME='연%')
    assert f.records(w, [row])[0].flags == []
    assert f.records(w, [{**row, 'DATA_VALUE': ''}])[0].flags == ['missing_or_invalid_value']
    with pytest.raises(f.CollectError, match='ecos_filter_mismatch'):
        f.records(w, [{**row, 'ITEM_CODE2': 'extra'}])


def test_future_observation_does_not_rewrite_old_version(db):
    w = list_work()
    save(db, w, f.records(w, [disclosure()]))
    before = db.query('SELECT * FROM fund_disclosure_raw WHERE revision=1')
    db.now = lambda: NOW + timedelta(days=1)
    save(db, w, f.records(w, [{**disclosure(), 'report_nm': '미래 정정'}]))
    assert db.query('SELECT * FROM fund_disclosure_raw WHERE revision=1') == before


class FakeApi:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def require(self, provider):
        pass

    def fetch(self, work):
        self.calls.append(work)
        value = self.answers(work)
        if isinstance(value, Exception):
            raise value
        return value


def test_runner_restart_skips_completed_and_max_units(db, cfg):
    a = FakeApi(lambda w: [disclosure()])
    r = f.Runner(a, db, cfg, mode='backfill', max_units=1, emit=lambda _: None)
    r.execute(list_work())
    r.execute(list_work())
    assert len(a.calls) == 1
    with pytest.raises(f.CollectError, match='unit_budget'):
        r.execute(f.Work('company', {'corp_code': CORP}))


@pytest.mark.parametrize('cfs_result,expect_ofs', [([], True), (f.CollectError('dart_access_denied'), False)])
def test_fallback_only_on_normal_cfs_absence(db, cfg, cfs_result, expect_ofs):
    cfg['report_codes'] = ['11011']
    def answer(w):
        if w.api == 'company':
            return [dict(corp_code=CORP, corp_cls='Y', corp_name='합성')]
        if w.api == 'list':
            return []
        if w.api == 'fnlttSinglAcntAll':
            return cfs_result if w.params['fs_div'] == 'CFS' else []
        return []
    a = FakeApi(answer)
    r = f.Runner(a, db, cfg, mode='backfill', emit=lambda _: None)
    if isinstance(cfs_result, f.CollectError):
        with pytest.raises(f.CollectError, match='dart_access_denied'):
            f.run_dart(r, date(2025, 1, 1), date(2025, 12, 31), CORP)
    else:
        f.run_dart(r, date(2025, 1, 1), date(2025, 12, 31), CORP)
    assert any(w.params.get('fs_div') == 'OFS' for w in a.calls) == expect_ofs


def test_private_current_profile_keeps_historical_public_company(db, cfg):
    cfg['report_codes'] = ['11011']
    def answer(w):
        if w.api == 'company':
            return [dict(corp_code=CORP, corp_cls='E', corp_name='과거상장')]
        if w.api == 'list':
            return [dict(disclosure(), report_nm='사업보고서(2024.12)')]
        if w.api == 'fnlttSinglAcntAll':
            return []
        return []
    a = FakeApi(answer)
    r = f.Runner(a, db, cfg, mode='backfill', emit=lambda _: None)
    f.run_dart(r, date(2025, 10, 1), date(2025, 10, 31), CORP)
    assert any(w.api == 'fnlttSinglAcntAll' for w in a.calls)


def test_work_rejects_credentials_and_non_business_fields():
    for p in ({'crtfc_key': 'SECRET'}, {'url': 'https://example.invalid'}, {'corp_code': None}):
        with pytest.raises(f.CollectError):
            f.Work('company', p)


def test_check_uses_no_api_without_server_pc(capsys):
    assert f.main(['check']) == 2
    assert 'server_pc_required' in capsys.readouterr().err


def test_schedule_info_has_no_credentials(capsys):
    assert f.main(['schedule-info']) == 0
    out = json.loads(capsys.readouterr().out)
    assert out['schedule_hour'] == 13 and out['schedule_minute'] == 30


def test_invalid_configuration_and_range_rejected_before_db(cfg, tmp_path, capsys):
    assert f.main(['--server-pc', 'backfill', '--source', 'ecos', '--corp-code', CORP]) == 2
    assert 'corp_filter_requires_dart_source' in capsys.readouterr().err
    assert f.main(['--server-pc', 'backfill', '--start', '2025-12-31', '--end', '2025-01-01']) == 2
    assert 'invalid_range' in capsys.readouterr().err


def test_schema_matches_server_message_and_existing_task_is_independent():
    schema = SCRIPT.with_name('schema.sql').read_text(encoding='utf-8')
    assert schema.count('CREATE TABLE IF NOT EXISTS') == 8
    assert 'ALTER TABLE' not in schema and 'DROP TABLE' not in schema
    assert 'UNIQUE KEY uq_fund_revision (source, entity_key, revision)' in schema
    install = SCRIPT.with_name('install_task.ps1').read_text(encoding='utf-8')
    assert 'regime-lab-fundamentals-daily' in install
    assert 'regime-lab-fsc-daily' not in install
    assert 'collect_fundamentals.py' in install
    assert 'Get-ScheduledTask' in install and '덮어쓰지' in install


class MetaDatabase(f.Database):
    """MariaDB information_schema contract only; does not claim actual DDL execution."""
    def __init__(self, mutation=None):
        self.mutation = mutation or (lambda table, cols: None)
        self.name = 'synthetic'

    def query(self, sql, params=()):
        table = params[1]
        if 'information_schema.TABLES' in sql:
            return [{'engine': 'InnoDB', 'collation_name': 'utf8mb4_bin'}]
        if 'information_schema.COLUMNS' in sql:
            types = f.LOG_TYPES if table == 'fund_collection_log' else f.RAW_TYPES
            cols = []
            for name, kind in types.items():
                default = {'schema_version': '1', 'time_precision': "'unknown'", 'availability_basis': "'unverified'"}.get(name)
                cols.append(dict(name=name, type_name=kind, nullable='YES' if name in f.OPTIONAL else 'NO',
                                 default_value=default, extra='auto_increment' if name == 'id' else '',
                                 collation_name=('ascii_bin' if name in {'entity_key', 'payload_hash', 'batch_id', 'work_key', 'config_hash'}
                                                 else 'utf8mb4_bin' if 'char' in kind or kind == 'longtext' else None)))
            self.mutation(table, cols)
            return cols
        indexes = ({'PRIMARY': ['id'], 'ix_fund_log_work': ['source', 'operation', 'work_key', 'id'],
                    'ix_fund_log_batch': ['batch_id'], 'ix_fund_log_status': ['status', 'observed_at']}
                   if table == 'fund_collection_log' else
                   {'PRIMARY': ['id'], 'uq_fund_revision': ['source', 'entity_key', 'revision'],
                    'ix_fund_corp_observed': ['corp_code', 'observed_at'],
                    'ix_fund_receipt_observed': ['rcept_no', 'observed_at'],
                    'ix_fund_source_date': ['source', 'source_date'], 'ix_fund_batch': ['batch_id']})
        return [dict(name=k, column_name=c, position=i + 1, non_unique=0 if k in {'PRIMARY', 'uq_fund_revision'} else 1)
                for k in sorted(indexes) for i, c in enumerate(indexes[k])]


def test_schema_checks_all_eight_tables():
    assert set(MetaDatabase().schema().values()) == {'ready'}


@pytest.mark.parametrize('column,change', [('schema_version', {'default_value': '2'}),
                                         ('payload_json', {'type_name': 'varchar(10)'}),
                                         ('entity_key', {'collation_name': 'utf8mb4_general_ci'}),
                                         ('source', {'nullable': 'YES'})])
def test_schema_rejects_incompatible_existing_table(column, change):
    def mutate(table, cols):
        if table == 'fund_corp_raw':
            next(c for c in cols if c['name'] == column).update(change)
    with pytest.raises(f.CollectError, match='schema_mismatch'):
        MetaDatabase(mutate).schema()


def test_ecos_full_five_series_collection(db, cfg):
    codes = {s['id']: str(i + 1) for i, s in enumerate(cfg['ecos_series'])}
    def answer(w):
        stat = w.params['stat_code']
        if w.api == 'StatisticTableList':
            return [dict(STAT_CODE=stat, STAT_NAME='합성통계', CYCLE='D', SRCH_YN='Y')]
        if w.api == 'StatisticItemList':
            return [dict(STAT_CODE=stat, CYCLE='D', ITEM_GRP_CODE='Group1', ITEM_CODE=codes[s['id']],
                         ITEM_NAME=s['names'][0], UNIT_NAME=s['units'][0], START_TIME='20251001', END_TIME='20251031')
                    for s in cfg['ecos_series'] if s['stat_code'] == stat]
        s = next(s for s in cfg['ecos_series'] if codes[s['id']] == w.params['item_code'])
        return [dict(STAT_CODE=stat, ITEM_CODE1=w.params['item_code'], TIME='20251013',
                     UNIT_NAME=s['units'][0], DATA_VALUE='1.234567890123456789')]
    r = f.Runner(FakeApi(answer), db, cfg, mode='backfill', emit=lambda _: None)
    f.run_ecos(r, date(2025, 10, 1), date(2025, 10, 31))
    assert db.query('SELECT COUNT(*) AS n FROM fund_ecos_value_raw')[0]['n'] == 5
    again = f.Runner(FakeApi(answer), db, cfg, mode='backfill', emit=lambda _: None)
    f.run_ecos(again, date(2025, 10, 1), date(2025, 10, 31))
    assert not again.api.calls


def test_pending_retry_respects_explicit_company_scope(db, cfg):
    other = f.Work('company', {'corp_code': '99999999'})
    db.begin(other, 'c', 'b' * 32)
    a = FakeApi(lambda w: [])
    r = f.Runner(a, db, cfg, mode='update', emit=lambda _: None)
    r.retry_pending('dart', CORP)
    assert not a.calls


def test_clock_regression_rejected_without_backdating(db):
    w = list_work()
    save(db, w, f.records(w, [disclosure()]))
    db.now = lambda: NOW - timedelta(days=1)
    with pytest.raises(f.CollectError, match='clock_regression'):
        save(db, w, f.records(w, [{**disclosure(), 'report_nm': '정정'}]))
    assert db.query('SELECT COUNT(*) AS n FROM fund_disclosure_raw')[0]['n'] == 1


def test_user_request_limit_cannot_increase_configured_limit(db, cfg):
    cfg['max_requests_per_run'] = 1
    b = f.Budget(db, cfg, 'c', 999)
    b.reserve('dart')
    with pytest.raises(f.CollectError, match='run_request_budget'):
        b.reserve('ecos')


def test_duplicate_json_fields_are_not_silently_overwritten(cfg):
    a, _, _ = api(cfg, [b'{"status":"000","status":"013"}'])
    with pytest.raises(f.CollectError, match='invalid_json'):
        a.dart('list', {})


def test_utf16_xml_not_accepted_as_utf8_corp_document(cfg):
    a, _, _ = api(cfg, [zipped('<!DOCTYPE x><result/>'.encode('utf-16'))])
    with pytest.raises(f.CollectError):
        a.corp_codes()


def test_no_normal_empty_fallback_after_other_cfs_failure(db, cfg):
    cfg['report_codes'] = ['11011']
    def answer(w):
        if w.api == 'company':
            return [dict(corp_code=CORP, corp_cls='Y', corp_name='합성')]
        if w.api == 'fnlttSinglAcntAll':
            return f.CollectError('dart_business_error')
        return []
    a = FakeApi(answer)
    f.run_dart(f.Runner(a, db, cfg, mode='backfill', emit=lambda _: None), date(2025, 10, 1), date(2025, 10, 31), CORP)
    assert any(w.api == 'fnlttSinglAcntAll' for w in a.calls)
    assert not any(w.params.get('fs_div') == 'OFS' for w in a.calls)


def test_blank_space_stock_code_does_not_expand_to_all_private_companies(db, cfg):
    cfg['report_codes'] = ['11011']
    def answer(w):
        if w.api == 'corpCode':
            return [dict(corp_code=CORP, stock_code='005930', modify_date='20251001'),
                    dict(corp_code='99999999', stock_code=' ', modify_date='20251001')]
        if w.api == 'company':
            return [dict(corp_code=w.params['corp_code'], corp_cls='Y', corp_name='합성')]
        return []
    a = FakeApi(answer)
    f.run_dart(f.Runner(a, db, cfg, mode='backfill', emit=lambda _: None), date(2025, 10, 1), date(2025, 10, 31))
    assert {w.params['corp_code'] for w in a.calls if w.api == 'company'} == {CORP}
    stored = db.query("SELECT stock_code,payload_json FROM fund_corp_raw WHERE corp_code='99999999'")[0]
    assert stored['stock_code'] is None and json.loads(stored['payload_json'])['stock_code'] == ' '


def test_recent_profile_is_not_refetched_for_every_daily_retry(db, cfg):
    def answer(w):
        if w.api == 'company':
            return [dict(corp_code=CORP, corp_cls='Y', corp_name='합성')]
        return []
    initial = FakeApi(answer)
    f.run_dart(f.Runner(initial, db, cfg, mode='update', emit=lambda _: None), date(2026, 10, 10), date(2026, 10, 17), CORP)
    again = FakeApi(answer)
    f.run_dart(f.Runner(again, db, cfg, mode='update', emit=lambda _: None), date(2026, 10, 10), date(2026, 10, 17), CORP)
    assert not any(w.api == 'company' for w in again.calls)


@pytest.mark.parametrize('status,expected', [('020', 'dart_quota_exceeded'), ('010', 'dart_access_denied')])
def test_binary_endpoint_xml_errors_remain_actionable_and_redacted(cfg, status, expected):
    a, _, _ = api(cfg, [f'<result><status>{status}</status><message>SECRET</message></result>'.encode()])
    with pytest.raises(f.CollectError, match=expected) as error:
        a.corp_codes()
    assert 'SECRET' not in str(error.value)


def test_discovered_private_disclosure_is_not_a_historical_public_company(db, cfg):
    cfg['report_codes'] = ['11011']
    def answer(w):
        if w.api == 'list':
            return [dict(disclosure(), corp_cls='E')]
        if w.api == 'company':
            return [dict(corp_code=CORP, corp_cls='E', corp_name='비상장')]
        return []
    a = FakeApi(answer)
    f.run_dart(f.Runner(a, db, cfg, mode='backfill', emit=lambda _: None), date(2025, 10, 1), date(2025, 10, 31), CORP)
    assert not any(w.api == 'fnlttSinglAcntAll' for w in a.calls)


def test_pending_does_not_enable_a_disabled_event_api(db, cfg):
    old = f.Work('crDecsn', dict(corp_code=CORP, bgn_de='20251001', end_de='20251031'))
    db.begin(old, 'c', 'b' * 32)
    cfg['events'] = ['tsstkAqDecsn']
    a = FakeApi(lambda w: [])
    f.Runner(a, db, cfg, mode='update', emit=lambda _: None).retry_pending('dart')
    assert not a.calls
