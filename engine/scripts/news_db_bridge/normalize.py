"""Pure spreadsheet normalization. Sheet data is never executed as code."""
import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

KINDS = {'raw', 'briefing', 'stocks'}
KST = timezone(timedelta(hours=9))


def stable(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(stable(value).encode()).hexdigest()


def text(value):
    if value is None:
        return ''
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise ValueError('invalid_cell_type')
    return str(value).strip()


def day(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not 1 <= value <= 100000 or value != int(value):
            raise ValueError('invalid_date')
        return (date(1899, 12, 30) + timedelta(days=int(value))).isoformat()
    value = text(value)
    if not value:
        return None
    try:
        match = re.fullmatch(r'(\d{4})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})\s*\.?', value)
        if not match:
            raise ValueError
        return date(*map(int, match.groups())).isoformat()
    except ValueError:
        raise ValueError('invalid_date') from None


def timestamp(value):
    value = text(value)
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
        return dt.astimezone(timezone.utc).isoformat()
    except ValueError:
        raise ValueError('invalid_timestamp') from None


def url(value):
    value = text(value)
    match = re.fullmatch(r'=HYPERLINK\(\s*"([^"]+)"\s*[,;].*\)', value, re.I | re.S)
    if match:
        value = match[1]
    try:
        p = urlsplit(value)
        if p.scheme not in ('https', 'http') or not p.hostname or p.username or p.password:
            raise ValueError
        if re.search(r'[\x00-\x20\x7f]', value):
            raise ValueError
        host = p.hostname.encode('idna').decode().lower()
        if ':' in host:
            host = '[' + host + ']'
        if p.port and (p.scheme, p.port) not in [('https', 443), ('http', 80)]:
            host += ':' + str(p.port)
        query = '&'.join(x for x in p.query.split('&') if x and not
                        (x.split('=')[0].lower().startswith('utm_') or
                         x.split('=')[0].lower() in ('fbclid', 'gclid')))
        return urlunsplit((p.scheme, host, p.path or '/', query, ''))
    except (ValueError, UnicodeError):
        raise ValueError('invalid_source_url') from None


def normalize(kind, row):
    if kind not in KINDS or not isinstance(row, dict):
        raise ValueError('invalid_row')
    original = {k: v for k, v in row.items() if k != 'row_number'}
    if not any(text(v) for v in original.values()):
        return None
    result = dict(kind=kind, title=None, summary=None, report_date=None,
                  published_at=None, reported_first_seen_at=None, source_url=None,
                  stocks=None, sector=None, impact=None, impact_reason=None,
                  uncertainty=None, stock_impacts_json=None, processing_status=None,
                  payload_json=stable(original))
    if kind == 'raw':
        result.update(title=text(row.get('원기사 제목')), summary=text(row.get('RSS 요약')),
                      source_url=url(row.get('출처')), published_at=timestamp(row.get('게시 시각')),
                      reported_first_seen_at=timestamp(row.get('최초 수집 시각')),
                      processing_status=text(row.get('처리 상태')))
        identity = result['source_url']
    elif kind == 'briefing':
        impacts = text(row.get('종목별 판정'))
        try:
            parsed = json.loads(impacts) if impacts else []
            if not isinstance(parsed, list) or any(not isinstance(v, dict) for v in parsed):
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError('invalid_stock_impacts_json') from None
        result.update(title=text(row.get('핵심 이슈')), summary=text(row.get('요약')),
                      report_date=day(row.get('날짜')), source_url=url(row.get('출처')),
                      stocks=text(row.get('관련 종목')), sector=text(row.get('업종')),
                      stock_impacts_json=stable(parsed))
        identity = result['source_url']
    else:
        result.update(report_date=day(row.get('날짜')), stocks=text(row.get('종목')))
        if not result['report_date'] or not result['stocks']:
            raise ValueError('missing_stock_identity')
        # Original aggregate key is kept in payload; identity is stable on date + stock.
        identity = result['report_date'] + '|' + result['stocks']
        source_lines = text(row.get('출처'))
        result['source_url'] = '\n'.join(url(v) for v in source_lines.splitlines() if v.strip())
    if kind != 'stocks' and not result['title']:
        raise ValueError('missing_title')
    if kind != 'raw':
        result.update(impact=text(row.get('예상 영향')), impact_reason=text(row.get('판단 근거')),
                      uncertainty=text(row.get('변수·불확실성')))
    result['entity_key'] = digest([kind, identity])
    # A reordering of rows doesn't create new versions; every actual cell change does.
    result['content_hash'] = digest({k: v for k, v in result.items() if k != 'payload_json'})
    # Include unmapped cells as well, so no sheet changes disappear silently.
    result['content_hash'] = digest([result['content_hash'], original])
    return result
