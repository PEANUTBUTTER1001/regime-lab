# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["PyMySQL>=1.1.1,<2", "PyYAML>=6,<7"]
# ///
"""승인된 DART·ECOS 업무 데이터 → MariaDB. 독립 실행 인프라, 엔진/ingest import 없음."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import http.client
import io
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
import zipfile

ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = ROOT / 'engine/config/default.yaml'
SCHEMA_PATH = Path(__file__).with_name('schema.sql')
KST = timezone(timedelta(hours=9))
UTC = timezone.utc
RAW_TABLES = ('fund_corp_raw', 'fund_company_raw', 'fund_disclosure_raw', 'fund_event_raw',
              'fund_financial_raw', 'fund_ecos_meta_raw', 'fund_ecos_value_raw')
TABLES = (*RAW_TABLES, 'fund_collection_log')
EVENTS = {
    'piicDecsn': '유상증자결정', 'fricDecsn': '무상증자결정', 'pifricDecsn': '유무상증자결정',
    'crDecsn': '감자결정', 'tsstkAqDecsn': '자기주식취득결정', 'tsstkDpDecsn': '자기주식처분결정',
    'tsstkAqTrctrCnsDecsn': '자기주식취득신탁계약체결결정',
    'tsstkAqTrctrCcDecsn': '자기주식취득신탁계약해지결정',
    'cvbdIsDecsn': '전환사채권발행결정', 'cmpMgDecsn': '회사합병결정',
    'cmpDvDecsn': '회사분할결정', 'cmpDvmgDecsn': '회사분할합병결정',
}
REPORTS = {'분기보고서': ('11013', '11014'), '반기보고서': ('11012',), '사업보고서': ('11011',)}
API_TABLE = {'corpCode': 'fund_corp_raw', 'company': 'fund_company_raw', 'list': 'fund_disclosure_raw',
             'fnlttSinglAcntAll': 'fund_financial_raw', **{k: 'fund_event_raw' for k in EVENTS},
             'StatisticTableList': 'fund_ecos_meta_raw', 'StatisticItemList': 'fund_ecos_meta_raw',
             'StatisticSearch': 'fund_ecos_value_raw'}
RAW_TYPES = dict(id='bigint unsigned', source='varchar(64)', entity_key='char(64)', revision='int unsigned',
                 schema_version='smallint unsigned', corp_code='varchar(8)', stock_code='varchar(32)',
                 rcept_no='varchar(14)', identity_json='longtext', request_json='longtext', payload_json='longtext',
                 payload_hash='char(64)', quality_json='longtext', source_date='date',
                 source_published_at='datetime(6)', time_precision='varchar(16)', first_seen_at='datetime(6)',
                 observed_at='datetime(6)', available_at='datetime(6)', availability_basis='varchar(32)', batch_id='char(32)')
LOG_TYPES = dict(id='bigint unsigned', source='varchar(64)', operation='varchar(24)', work_key='char(64)',
                 batch_id='char(32)', status='varchar(24)', observed_at='datetime(6)', config_hash='char(64)',
                 request_json='longtext', report_json='longtext')
OPTIONAL = {'corp_code', 'stock_code', 'rcept_no', 'source_date', 'source_published_at', 'available_at'}


class CollectError(Exception):
    def __init__(self, code, *, retryable=False):
        super().__init__(code)
        self.code, self.retryable = code, retryable


def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(obj):
    return hashlib.sha256(canonical(obj).encode('utf-8')).hexdigest()


def utcnow():
    return datetime.now(UTC)


def sqltime(dt):
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise CollectError('timezone_required')
    return dt.astimezone(UTC).replace(tzinfo=None)


def parse_date(value):
    try:
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}|\d{8}', value):
            raise ValueError
        return date.fromisoformat(value) if '-' in value else datetime.strptime(value, '%Y%m%d').date()
    except ValueError:
        raise CollectError('invalid_date') from None


def clean_name(value):
    return re.sub(r'\s+', '', value or '')


def number(value):
    try:
        if not isinstance(value, str) or not value.strip() or value.strip() == '-':
            return None
        n = Decimal(value.replace(',', ''))
        return n if n.is_finite() else None
    except InvalidOperation:
        return None


def load_config(path=CONFIG_PATH):
    try:
        import yaml
        with open(path, encoding='utf-8') as f:
            c = yaml.safe_load(f)['fundamental_collection']
        for k in ('db_port', 'db_timeout_seconds', 'http_timeout_seconds', 'max_response_bytes', 'max_zip_bytes',
                  'max_zip_members', 'max_rows_per_unit', 'page_size', 'max_requests_per_run', 'dart_daily_requests',
                  'ecos_daily_requests', 'lookback_days', 'pending_units_per_update', 'financial_start_year',
                  'profile_refresh_days', 'delayed_retry_minutes'):
            if type(c[k]) is not int or c[k] < 1:
                raise ValueError
        for k in ('schedule_hour', 'schedule_minute', 'delayed_retries', 'http_retries'):
            if type(c[k]) is not int or c[k] < 0:
                raise ValueError
        for k in ('request_interval_seconds', 'retry_backoff_seconds'):
            if type(c[k]) not in (int, float) or not 0 < c[k] < float('inf'):
                raise ValueError
        if c['db_port'] > 65535 or c['page_size'] > 100 or c['schedule_hour'] > 23 or c['schedule_minute'] > 59:
            raise ValueError
        if c['financial_start_year'] < 2015 or not re.fullmatch(r'[A-Za-z0-9_]+', c['database']):
            raise ValueError
        parse_date(c['dart_start'])
        if not c['markets'] or not set(c['markets']) <= {'Y', 'K'}:
            raise ValueError
        if not c['events'] or len(set(c['events'])) != len(c['events']) or not set(c['events']) <= set(EVENTS):
            raise ValueError
        if not c['report_codes'] or not set(c['report_codes']) <= {'11011', '11012', '11013', '11014'}:
            raise ValueError
        ids = set()
        for s in c['ecos_series']:
            if not re.fullmatch(r'[a-z][a-z0-9_]*', s['id']) or s['id'] in ids:
                raise ValueError
            ids.add(s['id'])
            if not re.fullmatch(r'\d{3}Y\d{3}', s['stat_code']) or s['cycle'] != 'D':
                raise ValueError
            if any(not isinstance(s[k], list) or not s[k] or any(not isinstance(v, str) or not v for v in s[k])
                   for k in ('names', 'units')):
                raise ValueError
        if not ids:
            raise ValueError
        return c
    except (Exception, CollectError):
        raise CollectError('invalid_configuration') from None


@dataclass(repr=False)
class Credentials:
    host: str
    user: str
    password: str
    database: str
    port: int
    ca: str | None = None

    @classmethod
    def from_env(cls, cfg):
        keys = ('REGIME_FSC_DB_HOST', 'REGIME_FSC_DB_USER', 'REGIME_FSC_DB_PASSWORD')
        if any(not os.environ.get(k) for k in keys):
            raise CollectError('missing_db_environment')
        try:
            database = os.environ.get('REGIME_FSC_DB_NAME', cfg['database'])
            port = int(os.environ.get('REGIME_FSC_DB_PORT', cfg['db_port']))
            if not re.fullmatch(r'[A-Za-z0-9_]+', database) or not 1 <= port <= 65535:
                raise ValueError
            return cls(*(os.environ[k] for k in keys), database, port, os.environ.get('REGIME_FSC_DB_SSL_CA') or None)
        except ValueError:
            raise CollectError('invalid_db_environment') from None


@dataclass(frozen=True)
class Work:
    api: str
    params: dict
    tag: str = ''

    def __post_init__(self):
        if self.api not in API_TABLE:
            raise CollectError('unsupported_api')
        forbidden = {'crtfc_key', 'api_key', 'serviceKey', 'password', 'key', 'token'}
        if forbidden & set(self.params) or any(not isinstance(k, str) or not isinstance(v, str)
                                              for k, v in self.params.items()):
            raise CollectError('invalid_work_parameters')
        # Only allow documented business parameters; saved pending requests cannot introduce arbitrary URLs/keys.
        allowed = ({'corp_code', 'bgn_de', 'end_de', 'corp_cls', 'last_reprt_at', 'bsns_year', 'reprt_code', 'fs_div'}
                   if self.provider == 'dart' else {'stat_code', 'cycle', 'start', 'end', 'item_code'})
        if set(self.params) - allowed or any(len(v) > 200 or any(ord(ch) < 32 for ch in v) for v in self.params.values()):
            raise CollectError('invalid_work_parameters')

    @property
    def provider(self):
        return 'ecos' if self.api.startswith('Statistic') else 'dart'

    @property
    def table(self):
        return API_TABLE[self.api]

    @property
    def key(self):
        return digest(self.record())

    def record(self):
        return {'api': self.api, 'params': self.params, 'tag': self.tag}


@dataclass
class Record:
    identity: dict
    payload: object
    corp: str | None = None
    stock: str | None = None
    receipt: str | None = None
    day: date | None = None
    flags: list[str] = field(default_factory=list)

    @property
    def key(self):
        return digest(self.identity)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CollectError('redirect_refused')


class Transport:
    def __init__(self, cfg, reserve, *, opener=None, sleep=time.sleep):
        self.cfg, self.reserve, self.sleep = cfg, reserve, sleep
        self.opener = opener or urllib.request.build_opener(NoRedirect())
        self.last = None

    def get(self, url, provider):
        for attempt in range(self.cfg['http_retries'] + 1):
            if self.last is not None:
                self.sleep(max(0, self.cfg['request_interval_seconds'] - (time.monotonic() - self.last)))
            self.reserve(provider)
            self.last = time.monotonic()
            wait = self.cfg['retry_backoff_seconds'] * 2 ** attempt
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'regime-lab-fundamentals/1', 'Accept': '*/*'})
                with self.opener.open(req, timeout=self.cfg['http_timeout_seconds']) as response:
                    raw = response.read(self.cfg['max_response_bytes'] + 1)
                if len(raw) > self.cfg['max_response_bytes']:
                    raise CollectError('response_too_large')
                return raw
            except urllib.error.HTTPError as e:
                code = e.code
                retry = e.headers.get('Retry-After', '') if e.headers else ''
                e.close()
                if code in (401, 403):
                    raise CollectError('api_access_denied') from None
                if code == 429 or code >= 500:
                    if retry:
                        if not retry.isdigit() or int(retry) > self.cfg['http_timeout_seconds']:
                            raise CollectError('retry_after_deferred') from None
                        wait = max(wait, int(retry))
                else:
                    raise CollectError('http_request_failed') from None
            except (OSError, urllib.error.URLError, http.client.HTTPException):
                pass
            if attempt == self.cfg['http_retries']:
                raise CollectError('network_retries_exhausted')
            self.sleep(wait)
        raise CollectError('network_retries_exhausted')

    def json(self, url, provider):
        try:
            def bad_constant(_):
                raise ValueError
            def unique_object(pairs):
                result = {}
                for k, v in pairs:
                    if k in result:
                        raise ValueError
                    result[k] = v
                return result
            body = json.loads(self.get(url, provider).decode('utf-8-sig'), parse_int=str, parse_float=str,
                              parse_constant=bad_constant, object_pairs_hook=unique_object)
            if not isinstance(body, dict):
                raise ValueError
            return body
        except (ValueError, UnicodeError):
            raise CollectError('invalid_json') from None


def integer(value):
    if isinstance(value, bool) or not re.fullmatch(r'\d+', str(value)):
        raise CollectError('invalid_count')
    return int(value)


class Api:
    def __init__(self, cfg, transport, dart_key='', ecos_key=''):
        self.cfg, self.transport = cfg, transport
        self.keys = {'dart': dart_key, 'ecos': ecos_key}

    def require(self, provider):
        if not self.keys[provider]:
            raise CollectError('missing_' + provider + '_key')
        if not re.fullmatch(r'[A-Za-z0-9_-]+', self.keys[provider]):
            raise CollectError('invalid_' + provider + '_key')

    def dart(self, api, params):
        self.require('dart')
        url = 'https://opendart.fss.or.kr/api/' + api + '.json?' + urllib.parse.urlencode(
            {'crtfc_key': self.keys['dart'], **params})
        body = self.transport.json(url, 'dart')
        status = body.get('status')
        if status == '013':
            return None
        if status != '000':
            raise CollectError('dart_quota_exceeded' if status == '020' else
                               'dart_access_denied' if status in {'010', '011', '012', '901'} else 'dart_business_error')
        return body

    def corp_codes(self):
        self.require('dart')
        url = 'https://opendart.fss.or.kr/api/corpCode.xml?' + urllib.parse.urlencode({'crtfc_key': self.keys['dart']})
        raw = self.transport.get(url, 'dart')
        if not raw.startswith(b'PK'):
            # The binary endpoint returns an XML error instead of a ZIP when the key/quota is invalid.
            try:
                text = raw.decode('utf-8-sig')
                if '<!DOCTYPE' not in text.upper() and '<!ENTITY' not in text.upper():
                    status = ET.fromstring(text).findtext('status')
                    if status == '020':
                        raise CollectError('dart_quota_exceeded')
                    if status in {'010', '011', '012', '901'}:
                        raise CollectError('dart_access_denied')
            except (ET.ParseError, UnicodeError):
                pass
            raise CollectError('invalid_corp_zip')
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                files = archive.infolist()
                if (len(files) > self.cfg['max_zip_members'] or any(i.flag_bits & 1 for i in files)
                        or sum(i.file_size for i in files) > self.cfg['max_zip_bytes']):
                    raise CollectError('unsafe_corp_zip')
                docs = [i for i in files if i.filename.upper() == 'CORPCODE.XML']
                if len(docs) != 1:
                    raise CollectError('invalid_corp_zip')
                with archive.open(docs[0]) as f:
                    content = f.read(self.cfg['max_zip_bytes'] + 1)
                if len(content) > self.cfg['max_zip_bytes']:
                    raise CollectError('unsafe_corp_xml')
                decoded = content.decode('utf-8-sig')
                if '<!DOCTYPE' in decoded.upper() or '<!ENTITY' in decoded.upper():
                    raise CollectError('unsafe_corp_xml')
                root = ET.fromstring(decoded)
        except (zipfile.BadZipFile, KeyError, RuntimeError, ET.ParseError, UnicodeError):
            raise CollectError('invalid_corp_zip') from None
        rows = []
        for item in root.findall('list'):
            if len({child.tag for child in item}) != len(item):
                raise CollectError('duplicate_xml_fields')
            rows.append({child.tag: child.text or '' for child in item})
        if not rows or len(rows) > self.cfg['max_rows_per_unit']:
            raise CollectError('invalid_corp_rows')
        return rows

    def dart_rows(self, work):
        if work.api == 'corpCode':
            return self.corp_codes()
        if work.api == 'company':
            body = self.dart(work.api, work.params)
            return [] if body is None else [{k: v for k, v in body.items() if k not in {'status', 'message'}}]
        if work.api != 'list':
            body = self.dart(work.api, work.params)
            if body is None:
                return []
            if not isinstance(body.get('list'), list) or not body['list']:
                raise CollectError('invalid_dart_rows')
            if len(body['list']) > self.cfg['max_rows_per_unit']:
                raise CollectError('too_many_rows')
            return body['list']
        rows, seen, totals, page = [], set(), None, 1
        while True:
            body = self.dart('list', {**work.params, 'page_no': str(page), 'page_count': str(self.cfg['page_size']),
                                      'sort': 'date', 'sort_mth': 'asc'})
            if body is None:
                if page != 1:
                    raise CollectError('page_disappeared')
                return []
            count, pages = integer(body.get('total_count')), integer(body.get('total_page'))
            if integer(body.get('page_no')) != page or pages < 1 or count > self.cfg['max_rows_per_unit']:
                raise CollectError('invalid_page')
            if totals is not None and totals != (count, pages):
                raise CollectError('pages_changed')
            totals = count, pages
            part = body.get('list')
            expected = min(self.cfg['page_size'], max(0, count - (page - 1) * self.cfg['page_size']))
            if not isinstance(part, list) or len(part) != expected or pages != max(1, (count + self.cfg['page_size'] - 1) // self.cfg['page_size']):
                raise CollectError('incomplete_page')
            for row in part:
                if not isinstance(row, dict) or row.get('rcept_no') in seen:
                    raise CollectError('duplicate_receipt')
                seen.add(row.get('rcept_no'))
            rows.extend(part)
            if page == pages:
                if len(rows) != count:
                    raise CollectError('incomplete_page')
                return rows
            page += 1

    def ecos_rows(self, work):
        self.require('ecos')
        rows, first, offset = [], None, 1
        p, size = work.params, self.cfg['page_size']
        while True:
            segments = [work.api, self.keys['ecos'], 'json', 'kr', str(offset), str(offset + size - 1), p['stat_code']]
            if work.api == 'StatisticSearch':
                segments.extend([p['cycle'], p['start'], p['end'], p['item_code']])
            url = 'https://ecos.bok.or.kr/api/' + '/'.join(urllib.parse.quote(s, safe='') for s in segments)
            body = self.transport.json(url, 'ecos')
            if 'RESULT' in body:
                result = body['RESULT']
                code = result.get('CODE') if isinstance(result, dict) else None
                if code == 'INFO-200' and offset == 1:
                    return []
                raise CollectError('ecos_quota_exceeded' if code == 'ERROR-602' else
                                   'ecos_access_denied' if code == 'INFO-100' else 'ecos_business_error')
            section = body.get(work.api)
            if not isinstance(section, dict):
                raise CollectError('invalid_ecos_response')
            count, part = integer(section.get('list_total_count')), section.get('row')
            if count > self.cfg['max_rows_per_unit'] or (first is not None and first != count):
                raise CollectError('ecos_pages_changed')
            first = count
            if not isinstance(part, list) or len(part) != min(size, max(0, count - offset + 1)):
                raise CollectError('incomplete_ecos_page')
            if any(not isinstance(r, dict) for r in part):
                raise CollectError('invalid_ecos_row')
            rows.extend(part)
            if len(rows) >= count:
                return rows
            offset += size

    def fetch(self, work):
        return self.ecos_rows(work) if work.provider == 'ecos' else self.dart_rows(work)


def _code(value, length, name):
    if not isinstance(value, str) or not re.fullmatch(r'\d{' + str(length) + '}', value):
        raise CollectError('invalid_' + name)
    return value


def records(work, rows, *, receipts=None):
    """순수 정규화. 원본 행·숫자·계정 중복 보존; 이용 가능 시각은 저장소에서 실제 관측으로 부여."""
    receipts = receipts or {}
    out = []
    for r in rows:
        if not isinstance(r, dict):
            raise CollectError('invalid_row')
    if work.api == 'fnlttSinglAcntAll':
        if not rows:
            return []
        receipt_ids = set()
        for r in rows:
            if any(str(r.get(k)) != work.params[k] for k in ('corp_code', 'bsns_year', 'reprt_code')):
                raise CollectError('financial_filter_mismatch')
            receipt_ids.add(_code(r.get('rcept_no'), 14, 'receipt'))
        if len(receipt_ids) != 1:
            raise CollectError('financial_receipt_conflict')
        receipt = next(iter(receipt_ids))
        return [Record(dict(work.params), rows, corp=work.params['corp_code'], receipt=receipt,
                       day=receipts.get(receipt), flags=['historical_original_unverified'])]
    if work.api in EVENTS:
        grouped = defaultdict(list)
        for r in rows:
            if r.get('corp_code') != work.params['corp_code']:
                raise CollectError('event_filter_mismatch')
            grouped[_code(r.get('rcept_no'), 14, 'receipt')].append(r)
        return [Record({'corp_code': work.params['corp_code'], 'rcept_no': key}, value,
                       corp=work.params['corp_code'], receipt=key, day=receipts.get(key),
                       flags=['historical_original_unverified'] + ([] if key in receipts else ['receipt_date_unverified']))
                for key, value in sorted(grouped.items())]
    for r in rows:
        if work.api == 'corpCode':
            corp = _code(r.get('corp_code'), 8, 'corp_code')
            parse_date(r.get('modify_date'))
            stock = (r.get('stock_code') or '').strip() or None
            flags = ['current_mapping_not_historical']
            if stock and not re.fullmatch(r'[0-9A-Z]{6}', stock):
                flags.append('invalid_stock_code')
            out.append(Record({'corp_code': corp}, r, corp, stock, flags=flags))
        elif work.api == 'company':
            corp = _code(work.params['corp_code'], 8, 'corp_code')
            if r.get('corp_code', corp) != corp or not r.get('corp_name') or r.get('corp_cls') not in {'Y', 'K', 'N', 'E'}:
                raise CollectError('company_filter_mismatch')
            out.append(Record({'corp_code': corp}, r, corp, (r.get('stock_code') or '').strip() or None,
                              flags=['current_profile_not_historical']))
        elif work.api == 'list':
            receipt = _code(r.get('rcept_no'), 14, 'receipt')
            corp = _code(r.get('corp_code'), 8, 'corp_code')
            day = parse_date(r.get('rcept_dt'))
            if (not parse_date(work.params['bgn_de']) <= day <= parse_date(work.params['end_de'])
                    or ('corp_cls' in work.params and r.get('corp_cls') != work.params['corp_cls'])
                    or ('corp_code' in work.params and corp != work.params['corp_code'])
                    or not isinstance(r.get('report_nm'), str) or not r['report_nm'].strip()):
                raise CollectError('disclosure_filter_mismatch')
            out.append(Record({'rcept_no': receipt}, r, corp, r.get('stock_code') or None, receipt, day))
        elif work.api == 'StatisticTableList':
            if not isinstance(r.get('STAT_CODE'), str) or not r.get('STAT_NAME'):
                raise CollectError('invalid_ecos_table')
            out.append(Record({'stat_code': r['STAT_CODE'], 'cycle': r.get('CYCLE', '')}, r))
        elif work.api == 'StatisticItemList':
            if r.get('STAT_CODE') != work.params['stat_code'] or not r.get('ITEM_CODE') or not r.get('CYCLE'):
                raise CollectError('invalid_ecos_item')
            out.append(Record({k: r.get(k, '') for k in ('STAT_CODE', 'ITEM_GRP_CODE', 'ITEM_CODE', 'CYCLE')}, r))
        elif work.api == 'StatisticSearch':
            p = work.params
            if (r.get('STAT_CODE') != p['stat_code'] or r.get('ITEM_CODE1') != p['item_code']
                    or any(r.get(k) for k in ('ITEM_CODE2', 'ITEM_CODE3', 'ITEM_CODE4'))):
                raise CollectError('ecos_filter_mismatch')
            day = parse_date(r.get('TIME'))
            if not parse_date(p['start']) <= day <= parse_date(p['end']):
                raise CollectError('ecos_date_mismatch')
            flags = []
            if number(r.get('DATA_VALUE')) is None:
                flags.append('missing_or_invalid_value')
            out.append(Record({'stat_code': p['stat_code'], 'item_codes': [p['item_code']],
                               'cycle': p['cycle'], 'time': r['TIME']}, r, day=day, flags=flags))
    keys = [r.key for r in out]
    if len(set(keys)) != len(keys):
        raise CollectError('duplicate_business_key')
    return out


class Database:
    def __init__(self, conn, name, *, now=utcnow):
        self.conn, self.name, self.now = conn, name, now
        self.lock_name = 'regime.fund.' + hashlib.sha256(name.encode()).hexdigest()[:32]
        self.locked = False

    @classmethod
    def connect(cls, credentials, cfg):
        try:
            import pymysql
            args = dict(host=credentials.host, user=credentials.user, password=credentials.password,
                        database=credentials.database, port=credentials.port, charset='utf8mb4', autocommit=False,
                        connect_timeout=cfg['db_timeout_seconds'], read_timeout=cfg['db_timeout_seconds'],
                        write_timeout=cfg['db_timeout_seconds'], cursorclass=pymysql.cursors.DictCursor,
                        init_command="SET SESSION sql_mode=CONCAT(@@SESSION.sql_mode,',STRICT_ALL_TABLES')")
            if credentials.ca:
                args.update(ssl_ca=credentials.ca, ssl_verify_cert=True, ssl_verify_identity=True)
            return cls(pymysql.connect(**args), credentials.database)
        except Exception:
            raise CollectError('db_connect_failed') from None

    def query(self, sql, params=()):
        with self.conn.cursor() as c:
            c.execute(sql, params)
            return c.fetchall() if c.description else []

    @contextmanager
    def exclusive(self):
        rows = self.query('SELECT GET_LOCK(%s,0) AS acquired', (self.lock_name,))
        self.conn.commit()
        if not rows or rows[0]['acquired'] != 1:
            raise CollectError('collector_busy')
        self.locked = True
        try:
            yield
        finally:
            try:
                self.conn.rollback()
                self.query('SELECT RELEASE_LOCK(%s)', (self.lock_name,))
                self.conn.commit()
            finally:
                self.locked = False

    def require_lock(self):
        if not self.locked:
            raise CollectError('lock_required')
        rows = self.query('SELECT IS_USED_LOCK(%s)=CONNECTION_ID() AS owned', (self.lock_name,))
        if not rows or rows[0]['owned'] != 1:
            raise CollectError('lock_lost')

    def schema(self, *, missing_ok=False):
        report = {}
        for table in TABLES:
            info = self.query('SELECT ENGINE AS engine,TABLE_COLLATION AS collation_name FROM information_schema.TABLES '
                              'WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s', (self.name, table))
            if not info:
                if missing_ok:
                    report[table] = 'missing'
                    continue
                raise CollectError('missing_tables')
            cols = self.query('SELECT COLUMN_NAME AS name,COLUMN_TYPE AS type_name,IS_NULLABLE AS nullable,'
                              'COLUMN_DEFAULT AS default_value,EXTRA AS extra,COLLATION_NAME AS collation_name '
                              'FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s', (self.name, table))
            expected = LOG_TYPES if table == 'fund_collection_log' else RAW_TYPES
            types = {r['name']: re.sub(r'^(bigint|smallint|int)\(\d+\)', r'\1', r['type_name'].lower()) for r in cols}
            if types != expected or info[0]['engine'] != 'InnoDB' or info[0]['collation_name'] != 'utf8mb4_bin':
                raise CollectError('schema_mismatch')
            for c in cols:
                name = c['name']
                nullable = table != 'fund_collection_log' and name in OPTIONAL
                if (c['nullable'] == 'YES') != nullable or ('auto_increment' in c['extra']) != (name == 'id'):
                    raise CollectError('schema_mismatch')
                if name in {'entity_key', 'payload_hash', 'batch_id', 'work_key', 'config_hash'}:
                    if c['collation_name'] != 'ascii_bin':
                        raise CollectError('schema_mismatch')
                elif c['collation_name'] and c['collation_name'] != 'utf8mb4_bin':
                    raise CollectError('schema_mismatch')
                if table != 'fund_collection_log' and name in {'schema_version', 'time_precision', 'availability_basis'}:
                    required = {'schema_version': '1', 'time_precision': 'unknown', 'availability_basis': 'unverified'}[name]
                    if str(c['default_value']).strip("'") != required:
                        raise CollectError('schema_mismatch')
            indexes = self.query('SELECT INDEX_NAME AS name,COLUMN_NAME AS column_name,SEQ_IN_INDEX AS position,'
                                 'NON_UNIQUE AS non_unique FROM information_schema.STATISTICS '
                                 'WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s ORDER BY INDEX_NAME,SEQ_IN_INDEX', (self.name, table))
            actual = defaultdict(list)
            unique = set()
            for ix in indexes:
                actual[ix['name']].append(ix['column_name'])
                if ix['non_unique'] == 0:
                    unique.add(ix['name'])
            want = ({'PRIMARY': ['id'], 'ix_fund_log_work': ['source', 'operation', 'work_key', 'id'],
                     'ix_fund_log_batch': ['batch_id'], 'ix_fund_log_status': ['status', 'observed_at']}
                    if table == 'fund_collection_log' else
                    {'PRIMARY': ['id'], 'uq_fund_revision': ['source', 'entity_key', 'revision'],
                     'ix_fund_corp_observed': ['corp_code', 'observed_at'],
                     'ix_fund_receipt_observed': ['rcept_no', 'observed_at'],
                     'ix_fund_source_date': ['source', 'source_date'], 'ix_fund_batch': ['batch_id']})
            if dict(actual) != want or unique != ({'PRIMARY'} if table == 'fund_collection_log' else {'PRIMARY', 'uq_fund_revision'}):
                raise CollectError('schema_mismatch')
            report[table] = 'ready'
        return report

    def initialize(self):
        self.require_lock()
        self.schema(missing_ok=True)
        text = re.sub(r'--[^\n]*', '', SCHEMA_PATH.read_text(encoding='utf-8'))
        for statement in text.split(';'):
            if statement.strip():
                self.query(statement)
        self.conn.commit()
        return self.schema()

    def preflight(self):
        row = self.query('SELECT DATABASE() AS selected_db,VERSION() AS version')[0]
        if row['selected_db'] != self.name:
            raise CollectError('wrong_database')
        result = {'database_matches': True, 'server': 'MariaDB' if 'MariaDB' in row['version'] else 'MySQL',
                  'tables': self.schema(missing_ok=True), 'grant_scope_verified': False}
        self.conn.rollback()
        return result

    def log(self, source, operation, key, batch, status, cfg_hash, request, report):
        self.query('INSERT INTO fund_collection_log '
                   '(source,operation,work_key,batch_id,status,observed_at,config_hash,request_json,report_json) '
                   'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                   (source, operation, key, batch, status, sqltime(self.now()), cfg_hash, canonical(request), canonical(report)))

    def state(self, work, cfg_hash):
        rows = self.query('SELECT status,observed_at,report_json FROM fund_collection_log '
                          "WHERE source=%s AND operation='collect' AND work_key=%s AND config_hash=%s ORDER BY id DESC LIMIT 1",
                          (work.api, work.key, cfg_hash))
        self.conn.rollback()
        return rows[0] if rows else None

    def begin(self, work, cfg_hash, batch):
        self.require_lock()
        self.log(work.api, 'collect', work.key, batch, 'started', cfg_hash, work.record(), {})
        self.conn.commit()

    def fail(self, work, cfg_hash, batch, error, *, status='failed'):
        self.conn.rollback()
        self.require_lock()
        self.log(work.api, 'collect', work.key, batch, status, cfg_hash, work.record(), {'error': error})
        self.conn.commit()

    def save(self, work, docs, cfg_hash, batch, report=None):
        self.require_lock()
        request = canonical({'api': work.api, 'params': work.params})
        try:
            old_keys = {r['entity_key'] for r in self.query(
                f'SELECT DISTINCT entity_key FROM {work.table} WHERE source=%s AND request_json=%s', (work.api, request))}
            if old_keys - {r.key for r in docs}:
                raise CollectError('source_rows_disappeared')
            old = {}
            keys = [r.key for r in docs]
            for i in range(0, len(keys), 500):
                chunk = keys[i:i + 500]
                marks = ','.join(['%s'] * len(chunk))
                found = self.query(f'SELECT r.entity_key,r.revision,r.payload_hash,r.observed_at FROM {work.table} r JOIN '
                                   f'(SELECT entity_key,MAX(revision) AS rev FROM {work.table} '
                                   f'WHERE source=%s AND entity_key IN ({marks}) GROUP BY entity_key) v '
                                   'ON r.entity_key=v.entity_key AND r.revision=v.rev WHERE r.source=%s',
                                   (work.api, *chunk, work.api))
                old.update({r['entity_key']: r for r in found})
            inserted = unchanged = 0
            quality = Counter()
            for doc in docs:
                quality.update(doc.flags)
                prior, h = old.get(doc.key), digest(doc.payload)
                now = sqltime(self.now())
                if prior:
                    prior_time = prior['observed_at']
                    if isinstance(prior_time, str):
                        prior_time = datetime.fromisoformat(prior_time)
                    if now < prior_time:
                        raise CollectError('clock_regression')
                if prior and prior['payload_hash'] == h:
                    unchanged += 1
                    continue
                self.query(f'INSERT INTO {work.table} '
                           '(source,entity_key,revision,schema_version,corp_code,stock_code,rcept_no,identity_json,'
                           'request_json,payload_json,payload_hash,quality_json,source_date,source_published_at,'
                           'time_precision,first_seen_at,observed_at,available_at,availability_basis,batch_id) '
                           'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                           (work.api, doc.key, int(prior['revision']) + 1 if prior else 1, 1, doc.corp, doc.stock,
                            doc.receipt, canonical(doc.identity), request, canonical(doc.payload), h,
                            canonical(sorted(set(doc.flags))), doc.day, None, 'date_only' if doc.day else 'unknown',
                            now, now, now, 'observed', batch))
                inserted += 1
            result = {'status': 'complete' if docs else 'empty', 'records': len(docs), 'inserted': inserted,
                      'unchanged': unchanged, 'quality': dict(quality), **(report or {})}
            self.log(work.api, 'collect', work.key, batch, result['status'], cfg_hash, work.record(), result)
            self.require_lock()
            self.conn.commit()
            return result
        except BaseException:
            self.conn.rollback()
            raise

    def latest(self, table, source=None):
        if table not in RAW_TABLES:
            raise CollectError('invalid_table')
        clause, params = ('WHERE source=%s', (source,)) if source else ('', ())
        rows = self.query(f'SELECT r.source,r.entity_key,r.payload_json,r.payload_hash,r.identity_json,r.observed_at '
                          f'FROM {table} r JOIN (SELECT source,entity_key,MAX(revision) AS rev FROM {table} '
                          f'{clause} GROUP BY source,entity_key) v ON r.source=v.source AND r.entity_key=v.entity_key '
                          'AND r.revision=v.rev', params)
        self.conn.rollback()
        return [{**r, 'payload': json.loads(r['payload_json']), 'identity': json.loads(r['identity_json'])} for r in rows]

    def pending(self, provider, limit):
        apis = [a for a in API_TABLE if ('ecos' if a.startswith('Statistic') else 'dart') == provider]
        marks = ','.join(['%s'] * len(apis))
        rows = self.query('SELECT l.request_json,l.observed_at FROM fund_collection_log l JOIN '
                          '(SELECT source,work_key,MAX(id) AS id FROM fund_collection_log '
                          f"WHERE operation='collect' AND source IN ({marks}) GROUP BY source,work_key) v ON l.id=v.id "
                          "WHERE l.status<>'complete' ORDER BY l.observed_at,l.id LIMIT %s", (*apis, limit))
        self.conn.rollback()
        result = []
        for r in rows:
            data = json.loads(r['request_json'])
            result.append(Work(data['api'], data['params'], data.get('tag', '')))
        return result

    def status(self):
        output = {}
        for table in RAW_TABLES:
            output[table] = self.query(f'SELECT COUNT(*) AS versions,MIN(source_date) AS first_date,'
                                      f'MAX(source_date) AS last_date,MAX(observed_at) AS last_observed FROM {table}')[0]
        states = self.query('SELECT l.source,l.status,COUNT(*) AS count FROM fund_collection_log l JOIN '
                            "(SELECT source,work_key,MAX(id) AS id FROM fund_collection_log WHERE operation='collect' "
                            'GROUP BY source,work_key) v ON l.id=v.id GROUP BY l.source,l.status')
        self.conn.rollback()
        return {'tables': output, 'work_states': states, 'coverage': 'observations_not_historical_completeness'}

    def inspect(self, table, *, corp=None, receipt=None, day=None, limit=20):
        if table not in RAW_TABLES:
            raise CollectError('invalid_table')
        clauses, params = [], []
        for col, value in [('corp_code', corp), ('rcept_no', receipt), ('source_date', day)]:
            if value is not None:
                clauses.append(col + '=%s')
                params.append(value)
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        rows = self.query(f'SELECT * FROM {table}{where} ORDER BY id DESC LIMIT %s', (*params, limit))
        self.conn.rollback()
        return rows


class Budget:
    def __init__(self, db, cfg, cfg_hash, limit=None):
        self.db, self.cfg, self.cfg_hash = db, cfg, cfg_hash
        self.limit = min(limit, cfg['max_requests_per_run']) if limit else cfg['max_requests_per_run']
        self.used = Counter()

    def reserve(self, provider):
        self.db.require_lock()
        if sum(self.used.values()) >= self.limit:
            raise CollectError('run_request_budget')
        now = self.db.now()
        day = now.astimezone(KST).date()
        begin = datetime.combine(day, datetime.min.time(), KST)
        used = self.db.query("SELECT COUNT(*) AS n FROM fund_collection_log WHERE source=%s AND operation='request' "
                             'AND observed_at>=%s AND observed_at<%s',
                             (provider, sqltime(begin), sqltime(begin + timedelta(days=1))))[0]['n']
        if used >= self.cfg[provider + '_daily_requests']:
            self.db.conn.rollback()
            raise CollectError(provider + '_daily_budget')
        self.db.log(provider, 'request', digest({'provider': provider, 'day': day.isoformat()}), uuid.uuid4().hex,
                    'reserved', self.cfg_hash, {}, {'day_kst': day.isoformat()})
        self.db.conn.commit()
        self.used[provider] += 1


def month_windows(start, end):
    while start <= end:
        following = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
        last = min(end, following - timedelta(days=1))
        yield start, last
        start = last + timedelta(days=1)


def year_windows(start, end):
    while start <= end:
        last = min(end, date(start.year, 12, 31))
        yield start, last
        start = last + timedelta(days=1)


def event_api(title, enabled):
    title = clean_name(title)
    # Longer event names first: division-merger is not classified as a simple division.
    for api in sorted(enabled, key=lambda a: len(EVENTS[a]), reverse=True):
        if EVENTS[api] in title:
            return api
    return None


def resolve_series(spec, tables, items):
    found = [r for r in tables if r.get('STAT_CODE') == spec['stat_code']
             and r.get('CYCLE') == spec['cycle'] and r.get('SRCH_YN') == 'Y']
    if len(found) != 1:
        raise CollectError('ecos_table_unverified')
    scoped = [r for r in items if r.get('STAT_CODE') == spec['stat_code'] and r.get('CYCLE') == spec['cycle']]
    if len({r.get('ITEM_GRP_CODE', '') for r in scoped}) > 1:
        raise CollectError('ecos_multiple_dimensions')
    matches = [r for r in scoped if clean_name(r.get('ITEM_NAME')) in {clean_name(s) for s in spec['names']}
               and clean_name(r.get('UNIT_NAME')) in {clean_name(s) for s in spec['units']}]
    if len(matches) != 1:
        raise CollectError('ecos_item_ambiguous')
    row = matches[0]
    begin, end = parse_date(row.get('START_TIME')), parse_date(row.get('END_TIME'))
    if begin > end or not row.get('ITEM_CODE'):
        raise CollectError('ecos_invalid_period')
    return {**spec, 'item_code': row['ITEM_CODE'], 'first': begin, 'last': end,
            'unit': row['UNIT_NAME'], 'item_name': row['ITEM_NAME']}


def disclosure_scan(db, start, end, corp=None):
    """Read bounded pages of latest disclosure rows; do not materialize the historical corpus."""
    last_id = 0
    while True:
        params = [start, end, last_id]
        extra = ''
        if corp:
            extra = ' AND r.corp_code=%s'
            params.append(corp)
        rows = db.query('SELECT r.id,r.payload_json FROM fund_disclosure_raw r JOIN '
                        '(SELECT source,entity_key,MAX(revision) AS rev FROM fund_disclosure_raw GROUP BY source,entity_key) v '
                        'ON r.source=v.source AND r.entity_key=v.entity_key AND r.revision=v.rev '
                        'WHERE r.source_date>=%s AND r.source_date<=%s AND r.id>%s' + extra +
                        ' ORDER BY r.id LIMIT 2000', tuple(params))
        db.conn.rollback()
        if not rows:
            return
        for row in rows:
            last_id = row['id']
            yield json.loads(row['payload_json'])


def receipt_dates(db, rows):
    ids = sorted({r.get('rcept_no') for r in rows if isinstance(r, dict)
                  and isinstance(r.get('rcept_no'), str) and re.fullmatch(r'\d{14}', r['rcept_no'])})
    found = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        marks = ','.join(['%s'] * len(chunk))
        values = db.query(f'SELECT rcept_no,source_date FROM fund_disclosure_raw WHERE rcept_no IN ({marks})', tuple(chunk))
        for row in values:
            value = row['source_date']
            if isinstance(value, str):
                value = parse_date(value)
            if row['rcept_no'] in found and found[row['rcept_no']] != value:
                raise CollectError('receipt_date_conflict')
            found[row['rcept_no']] = value
    db.conn.rollback()
    return found


class Runner:
    def __init__(self, api, db, cfg, *, mode, force=False, max_units=None, emit=print):
        self.api, self.db, self.cfg = api, db, cfg
        self.mode, self.force, self.max_units, self.emit = mode, force, max_units, emit
        self.cfg_hash = digest({'policy': 1, 'config': cfg})
        self.totals = Counter()
        self.finished = set()
        self.expected_units = {}

    def execute(self, work, *, refresh=False):
        self.api.require(work.provider)
        state = self.db.state(work, self.cfg_hash)
        if not (self.force or refresh) and state and state['status'] in {'complete', 'empty'}:
            self.totals['cached'] += 1
            if state['status'] == 'empty':
                self.totals['empty_cached'] += 1
            return {**json.loads(state['report_json']), 'status': state['status'], 'cached': True}
        if self.max_units is not None and self.totals['attempted'] >= self.max_units:
            raise CollectError('unit_budget')
        batch = uuid.uuid4().hex
        self.totals['attempted'] += 1
        self.db.begin(work, self.cfg_hash, batch)
        try:
            rows = self.api.fetch(work)
            receipts = receipt_dates(self.db, rows) if work.api in EVENTS or work.api == 'fnlttSinglAcntAll' else {}
            docs = records(work, rows, receipts=receipts)
            report = {}
            if work.api == 'StatisticSearch':
                unit = self.expected_units.get((work.params['stat_code'], work.params['item_code']))
                if unit is None or any(clean_name(r.get('UNIT_NAME')) != clean_name(unit) for r in rows):
                    raise CollectError('ecos_unit_mismatch')
                if rows:
                    report['latest_observed_date'] = max(r.day for r in docs).isoformat()
                if any(d.flags for d in docs):
                    report['status'] = 'partial'
            result = self.db.save(work, docs, self.cfg_hash, batch, report)
        except CollectError as e:
            self.db.fail(work, self.cfg_hash, batch, e.code)
            self.totals['failed'] += 1
            self.emit(canonical({'api': work.api, 'work_key': work.key, 'status': 'failed', 'error': e.code}))
            if e.code.endswith('_budget') or e.code in {'dart_quota_exceeded', 'ecos_quota_exceeded',
                                                       'lock_lost', 'lock_required', 'dart_access_denied',
                                                       'ecos_access_denied', 'api_access_denied'}:
                raise
            return {'status': 'failed', 'error': e.code}
        except BaseException:
            self.db.conn.rollback()
            # Database/internal errors must abort; never turn a lost connection into a normal empty job.
            raise
        self.finished.add(work.key)
        self.totals[result['status']] += 1
        self.totals['inserted'] += result['inserted']
        self.totals['unchanged'] += result['unchanged']
        self.emit(canonical({'api': work.api, 'work_key': work.key, **result}))
        return result

    def retry_pending(self, provider, corp=None):
        if self.mode != 'update':
            return
        for work in self.db.pending(provider, self.cfg['pending_units_per_update']):
            if corp and work.params.get('corp_code') != corp:
                continue
            if work.api in EVENTS and work.api not in self.cfg['events']:
                continue
            if work.api == 'fnlttSinglAcntAll' and work.params['reprt_code'] not in self.cfg['report_codes']:
                continue
            if work.api == 'list' and work.params.get('corp_cls', self.cfg['markets'][0]) not in self.cfg['markets']:
                continue
            if work.key in self.finished:
                continue
            # ECOS value work is replayed only after metadata resolution for this run.
            if work.api == 'StatisticSearch' and (work.params['stat_code'], work.params['item_code']) not in self.expected_units:
                self.totals['unresolved_pending'] += 1
                continue
            result = self.execute(work, refresh=True)
            if work.api == 'fnlttSinglAcntAll' and work.params['fs_div'] == 'CFS' and result['status'] == 'empty':
                self.execute(Work(work.api, {**work.params, 'fs_div': 'OFS'}), refresh=True)


def run_ecos(runner, start, end):
    cfg, db = runner.cfg, runner.db
    specs = cfg['ecos_series']
    for stat in dict.fromkeys(s['stat_code'] for s in specs):
        for api in ('StatisticTableList', 'StatisticItemList'):
            result = runner.execute(Work(api, {'stat_code': stat}), refresh=runner.mode == 'update')
            if result['status'] not in {'complete'}:
                raise CollectError('ecos_metadata_unavailable')
    tables = [r['payload'] for r in db.latest('fund_ecos_meta_raw', 'StatisticTableList')]
    items = [r['payload'] for r in db.latest('fund_ecos_meta_raw', 'StatisticItemList')]
    resolved = []
    for spec in specs:
        try:
            s = resolve_series(spec, tables, items)
        except CollectError as e:
            runner.totals['failed'] += 1
            runner.emit(canonical({'series': spec['id'], 'status': 'failed', 'error': e.code}))
            continue
        runner.expected_units[(s['stat_code'], s['item_code'])] = s['unit']
        resolved.append(s)
        runner.emit(canonical({'series': s['id'], 'stat_code': s['stat_code'], 'item_code': s['item_code'],
                               'unit': s['unit'], 'first_date': s['first'].isoformat(), 'metadata_last': s['last'].isoformat()}))
    # Retry old failed/empty windows before new windows so a daily low budget cannot starve them.
    runner.retry_pending('ecos')
    for s in resolved:
        begin = max(s['first'], start) if start else s['first']
        finish = min(end, s['last'])
        if begin > finish:
            runner.totals['unavailable_range'] += 1
            continue
        for a, b in year_windows(begin, finish):
            runner.execute(Work('StatisticSearch', {'stat_code': s['stat_code'], 'cycle': s['cycle'],
                                                   'start': a.strftime('%Y%m%d'), 'end': b.strftime('%Y%m%d'),
                                                   'item_code': s['item_code']}, s['id']), refresh=runner.mode == 'update')


def run_dart(runner, start, end, corp=None):
    cfg, db = runner.cfg, runner.db
    if not corp:
        result = runner.execute(Work('corpCode', {}), refresh=runner.mode == 'update')
        if result['status'] != 'complete':
            raise CollectError('corp_catalog_unavailable')
    for a, b in month_windows(start, end):
        for market in ([None] if corp else cfg['markets']):
            params = {'bgn_de': a.strftime('%Y%m%d'), 'end_de': b.strftime('%Y%m%d'), 'last_reprt_at': 'N'}
            params.update({'corp_code': corp} if corp else {'corp_cls': market})
            runner.execute(Work('list', params), refresh=runner.mode == 'update')
    # Company targets include historical public disclosures, not only today's listed companies.
    markets = ','.join(['%s'] * len(cfg['markets']))
    historical = {r['corp_code'] for r in db.query(
        'SELECT DISTINCT corp_code FROM fund_disclosure_raw WHERE corp_code IS NOT NULL '
        f"AND source='list' AND JSON_VALUE(payload_json,'$.corp_cls') IN ({markets})", tuple(cfg['markets']))}
    db.conn.rollback()
    mapping = {} if corp else {r['payload']['corp_code']: r['payload'] for r in db.latest('fund_corp_raw', 'corpCode')}
    targets = ({corp} if corp else historical | {k for k, r in mapping.items()
               if isinstance(r.get('stock_code'), str) and re.fullmatch(r'[0-9A-Z]{6}', r['stock_code'].strip())})
    eligible = set()
    for code in sorted(targets):
        modified = mapping.get(code, {}).get('modify_date', '')
        work = Work('company', {'corp_code': code}, modified)
        previous = db.state(work, runner.cfg_hash)
        stale = False
        if previous and runner.mode == 'update':
            checked = previous['observed_at']
            if isinstance(checked, str):
                checked = datetime.fromisoformat(checked)
            stale = sqltime(db.now()) - checked >= timedelta(days=cfg['profile_refresh_days'])
        result = runner.execute(work, refresh=stale)
        if result['status'] != 'complete':
            continue
        # A current non-public classification does not remove a historical public company.
        if code in historical:
            eligible.add(code)
    profiles = {r['identity']['corp_code']: r['payload'] for r in db.latest('fund_company_raw', 'company')}
    eligible.update(k for k in targets if profiles.get(k, {}).get('corp_cls') in cfg['markets'])
    event_work = {}
    financial_work = set()
    for row in disclosure_scan(db, start, end, corp):
        code = row['corp_code']
        if code not in eligible:
            continue
        title = clean_name(row['report_nm'])
        selected = event_api(title, cfg['events'])
        if selected:
            d = parse_date(row['rcept_dt'])
            a = max(start, d.replace(day=1))
            b = min(end, date(d.year + (d.month == 12), d.month % 12 + 1, 1) - timedelta(days=1))
            w = Work(selected, {'corp_code': code, 'bgn_de': a.strftime('%Y%m%d'), 'end_de': b.strftime('%Y%m%d')})
            event_work[w.key] = w
        if runner.mode == 'update' and any(name in title for name in REPORTS):
            # Non-December fiscal years can straddle calendar years. Query both candidate business years,
            # and keep native response business year/period/receipt instead of assigning one from a title.
            match = re.search(r'\((\d{4})[.\-/]\d{2}\)', title)
            year = int(match[1]) if match else end.year
            for candidate in (year - 1, year):
                if cfg['financial_start_year'] <= candidate <= end.year:
                    financial_work.update((code, candidate, report) for report in cfg['report_codes'])
    runner.retry_pending('dart', corp)
    for work in event_work.values():
        runner.execute(work, refresh=runner.mode == 'update')
    if runner.mode == 'backfill':
        financial_work = ((code, y, report) for code in sorted(eligible)
                          for y in range(max(cfg['financial_start_year'], start.year), end.year + 1)
                          for report in cfg['report_codes'])
    for code, year, report in sorted(financial_work) if isinstance(financial_work, set) else financial_work:
        p = {'corp_code': code, 'bsns_year': str(year), 'reprt_code': report, 'fs_div': 'CFS'}
        result = runner.execute(Work('fnlttSinglAcntAll', p), refresh=runner.mode == 'update')
        if result['status'] == 'empty':
            runner.execute(Work('fnlttSinglAcntAll', {**p, 'fs_div': 'OFS'}), refresh=runner.mode == 'update')


def make_parser():
    p = argparse.ArgumentParser(description='DART·ECOS Python 수집기 (서버 PC 전용, fund_* 전용 표)')
    p.add_argument('--config', type=Path, default=CONFIG_PATH)
    p.add_argument('--server-pc', action='store_true')
    sub = p.add_subparsers(dest='command', required=True)
    for name in ('check', 'init-db', 'status', 'schedule-info'):
        q = sub.add_parser(name)
        if name == 'check':
            q.add_argument('--source', choices=('dart', 'ecos', 'all'), default='all')
    for name in ('backfill', 'update'):
        q = sub.add_parser(name)
        q.add_argument('--source', choices=('dart', 'ecos', 'all'), default='all')
        q.add_argument('--start')
        q.add_argument('--end')
        q.add_argument('--corp-code', help='DART 회사 고유번호8자리. 주식 종목코드 아님')
        q.add_argument('--max-units', type=int)
        q.add_argument('--max-requests', type=int)
        if name == 'backfill':
            q.add_argument('--refresh', action='store_true')
    q = sub.add_parser('inspect')
    q.add_argument('--table', choices=RAW_TABLES, required=True)
    q.add_argument('--corp-code')
    q.add_argument('--receipt')
    q.add_argument('--date')
    q.add_argument('--limit', type=int, default=20)
    return p


def api_check(api, providers, cfg, today):
    results = {}
    for source in providers:
        try:
            api.require(source)
            if source == 'dart':
                api.dart('list', {'bgn_de': today.strftime('%Y%m%d'), 'end_de': today.strftime('%Y%m%d'),
                                  'corp_cls': cfg['markets'][0], 'page_no': '1', 'page_count': '1'})
            else:
                rows = api.ecos_rows(Work('StatisticTableList', {'stat_code': cfg['ecos_series'][0]['stat_code']}))
                if not rows:
                    raise CollectError('ecos_metadata_unavailable')
            results[source] = {'status': 'ok'}
        except CollectError as e:
            results[source] = {'status': 'failed', 'error': e.code}
    return results


def output(value, *, error=False):
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, default=lambda v: v.isoformat(), allow_nan=False),
          file=sys.stderr if error else sys.stdout)


def main(argv=None):
    args, db = make_parser().parse_args(argv), None
    try:
        cfg = load_config(args.config)
        if args.command == 'schedule-info':
            output({k: cfg[k] for k in ('schedule_hour', 'schedule_minute', 'delayed_retry_minutes', 'delayed_retries')})
            return 0
        if not args.server_pc:
            raise CollectError('server_pc_required')
        today = utcnow().astimezone(KST).date()
        start = parse_date(args.start) if getattr(args, 'start', None) else None
        end = parse_date(args.end) if getattr(args, 'end', None) else today
        if end > today or (start and start > end):
            raise CollectError('invalid_range')
        for name in ('max_units', 'max_requests', 'limit'):
            if getattr(args, name, None) is not None and getattr(args, name) < 1:
                raise CollectError('invalid_limit')
        if getattr(args, 'corp_code', None):
            _code(args.corp_code, 8, 'corp_code')
            if getattr(args, 'source', 'dart') != 'dart':
                raise CollectError('corp_filter_requires_dart_source')
        if getattr(args, 'receipt', None):
            _code(args.receipt, 14, 'receipt')
        db = Database.connect(Credentials.from_env(cfg), cfg)
        if args.command == 'init-db':
            with db.exclusive():
                output({'tables': db.initialize()})
            return 0
        if args.command == 'check':
            report = db.preflight()
            if any(v != 'ready' for v in report['tables'].values()):
                output({**report, 'api': 'not_checked_until_tables_ready'})
                return 2
        else:
            db.schema()
        if args.command == 'status':
            output(db.status())
            return 0
        if args.command == 'inspect':
            output({'rows': db.inspect(args.table, corp=args.corp_code, receipt=args.receipt,
                                       day=parse_date(args.date) if args.date else None, limit=args.limit)})
            return 0
        providers = ['ecos', 'dart'] if args.source == 'all' else [args.source]
        cfg_hash = digest({'policy': 1, 'config': cfg})
        with db.exclusive():
            budget = Budget(db, cfg, cfg_hash, getattr(args, 'max_requests', None))
            api = Api(cfg, Transport(cfg, budget.reserve), os.environ.get('DART_API_KEY', '').strip(),
                      os.environ.get('ECOS_API_KEY', '').strip())
            if args.command == 'check':
                report['api'] = api_check(api, providers, cfg, today)
                report['request_log_written'] = True
                output(report)
                return 0 if all(r['status'] == 'ok' for r in report['api'].values()) else 1
            runner = Runner(api, db, cfg, mode=args.command, force=getattr(args, 'refresh', False), max_units=args.max_units)
            provider_results = {}
            for provider in providers:
                before = runner.totals.copy()
                try:
                    api.require(provider)
                    begin = start
                    if args.command == 'update' and begin is None:
                        begin = end - timedelta(days=cfg['lookback_days'] - 1)
                    if provider == 'ecos':
                        run_ecos(runner, begin, end)
                    else:
                        run_dart(runner, begin or parse_date(cfg['dart_start']), end, args.corp_code)
                    delta = runner.totals - before
                    status = ('failed' if delta['failed'] else 'partial' if any(delta[k] for k in
                              ('empty', 'empty_cached', 'partial', 'unavailable_range', 'unresolved_pending')) else 'ok')
                    provider_results[provider] = {'status': status, **dict(delta)}
                except CollectError as e:
                    if e.code in {'lock_lost', 'lock_required'}:
                        raise
                    provider_results[provider] = {'status': 'partial' if e.code.endswith('_budget') or e.code.endswith('_quota_exceeded')
                                                 else 'failed', 'error': e.code, **dict(runner.totals - before)}
            output({'sources': provider_results, 'totals': dict(runner.totals), 'requests': dict(budget.used),
                    'quota_scope': 'this_collector_only', 'historical_originals': 'unverified_unless_separately_proven'})
            return (1 if any(v['status'] == 'failed' for v in provider_results.values()) else
                    3 if any(v['status'] == 'partial' for v in provider_results.values()) else 0)
    except CollectError as e:
        output({'status': 'failed', 'error': e.code}, error=True)
        return 3 if e.code.endswith('_budget') or e.code.endswith('_quota_exceeded') else 2
    except KeyboardInterrupt:
        output({'status': 'interrupted', 'message': '같은 명령으로 재개 가능'}, error=True)
        return 130
    except Exception:
        output({'status': 'failed', 'error': 'collector_failed',
                'message': '연결·권한·표 정의 확인 필요. 외부 오류 원문은 표시하지 않음'}, error=True)
        return 2
    finally:
        if db:
            try:
                db.conn.close()
            except Exception:
                pass


if __name__ == '__main__':
    sys.exit(main())
