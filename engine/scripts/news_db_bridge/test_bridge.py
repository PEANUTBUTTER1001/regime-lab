import json
import sqlite3
from unittest.mock import patch

import pyarrow.parquet as pq
import pytest

from normalize import normalize
from store import Store

NOW = '2026-10-08T03:00:00+00:00'
LATER = '2026-10-08T04:00:00+00:00'


def article(**extra):
    return {'row_number': 2, '날짜': '2026.10.08', '핵심 이슈': '테스트', '요약': '요약',
            '출처': 'https://example.com/news?id=1', '종목별 판정': '[]', **extra}


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path)
    yield s
    s.close()


def test_first_import_and_repeat(store):
    assert store.sync('briefing', [article()], NOW)['inserted'] == 1
    assert store.sync('briefing', [article(row_number=100)], LATER)['unchanged'] == 1
    assert store.status()['versions'] == 1
    rows = pq.read_table(store.root / 'parquet/kind=briefing/current.parquet').to_pylist()
    assert rows[0]['report_date'] == '2026-10-08'
    assert rows[0]['available_at'] == NOW


def test_versions_and_observation_time(store):
    store.sync('briefing', [article()], NOW)
    store.sync('briefing', [article(**{'요약': '수정'})], LATER)
    assert store.status()['versions'] == 2
    versions = store.db.execute('SELECT available_at FROM versions ORDER BY version').fetchall()
    assert [r[0] for r in versions] == [NOW, LATER]
    assert store.db.execute('SELECT version FROM current_records').fetchone()[0] == 2


def test_invalid_rows_quarantined_without_fabrication(store):
    result = store.sync('briefing', [article(**{'출처': 'javascript:evil'}), article()], NOW)
    assert result['rejected'] == 1 and result['inserted'] == 1
    assert result['status'] == 'partial'
    assert store.status()['quarantine'] == 1


def test_duplicate_conflict(store):
    r = store.sync('briefing', [article(), article(**{'요약': 'conflict'})], NOW)
    assert r['rejected'] == 2 and r['inserted'] == 0


def test_export_failure_replay_recovers(store):
    with patch('store.os.replace', side_effect=OSError('disk failure')):
        with pytest.raises(OSError):
            store.sync('briefing', [article()], NOW)
    assert store.status()['pending_exports'] == 1
    result = store.sync('briefing', [article()], LATER)
    assert result['unchanged'] == 1 and store.status()['versions'] == 1
    assert store.status()['pending_exports'] == 0


def test_sql_failure_rolls_back_entire_batch(store):
    with store.db:
        store.db.execute("CREATE TRIGGER fail_write BEFORE INSERT ON versions WHEN NEW.title='fail' BEGIN SELECT RAISE(ABORT,'test'); END")
    with pytest.raises(sqlite3.IntegrityError):
        store.sync('briefing', [article(), article(**{'출처': 'https://example.com/2', '핵심 이슈': 'fail'})], NOW)
    assert store.status()['versions'] == 0


def test_raw_separate_from_generated_summary(store):
    row = {'원기사 제목': '원자료', 'RSS 요약': '원본 제공 요약', '출처': article()['출처'],
           '게시 시각': '2026-09-01T12:00:00+09:00', '최초 수집 시각': '2026-09-02T12:00:00+09:00'}
    store.sync('raw', [row], NOW)
    store.sync('briefing', [article()], NOW)
    assert store.status()['current'] == {'briefing': 1, 'raw': 1}
    raw = store.db.execute("SELECT * FROM current_records WHERE kind='raw'").fetchone()
    assert raw['available_at'] == NOW
    assert raw['reported_first_seen_at'] == '2026-09-02T03:00:00+00:00'


def test_stocks_daily_identity():
    row = {'날짜': '2026-10-08', '종목': '005930', '출처': 'https://example.com/1\nhttps://example.com/2'}
    n = normalize('stocks', row)
    assert n['stocks'] == '005930'
    assert normalize('stocks', {**row, 'row_number': 999})['entity_key'] == n['entity_key']


@pytest.mark.parametrize('change', [{'날짜': '2026-99-01'}, {'종목별 판정': '{}'}, {'출처': 'https://u:p@example.com/a'}, {'핵심 이슈': ''}])
def test_invalid_fields(change):
    with pytest.raises(ValueError):
        normalize('briefing', article(**change))


def test_empty_row_and_formula_not_executed():
    assert normalize('raw', {'row_number': 5, '원기사 제목': ''}) is None
    n = normalize('briefing', article(**{'요약': '=IMPORTXML("x")', '출처': '=HYPERLINK("https://example.com/a", "원문")'}))
    assert n['summary'].startswith('=IMPORTXML') and n['source_url'] == 'https://example.com/a'


def test_unknown_column_preserved_and_versioned(store):
    store.sync('briefing', [article()], NOW)
    assert store.sync('briefing', [article(추가열='보존')], LATER)['updated'] == 1
    row = store.db.execute('SELECT payload_json FROM current_records').fetchone()
    assert json.loads(row[0])['추가열'] == '보존'


@pytest.mark.parametrize('value', ['2026. 10. 8', '2026.10.08.', '2026/10/8', '2026-10-08', 46303])
def test_google_sheet_dates(value):
    from normalize import day
    assert day(value) == '2026-10-08'


def test_fixed_parser_resolves_quarantine(store):
    row = article()
    with patch('store.normalize', side_effect=ValueError('invalid_date')):
        assert store.sync('briefing', [row], NOW)['rejected'] == 1
    assert store.sync('briefing', [row], LATER)['inserted'] == 1
    assert store.status()['quarantine'] == 0
    assert store.status()['resolved_quarantine'] == 1
