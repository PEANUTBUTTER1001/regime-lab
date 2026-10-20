"""Current-name text queries do not create security links or bypass as_of/query eligibility."""
from copy import deepcopy
from datetime import timedelta

import pytest

from rag_synth import T0, make_doc, write_store
from regime_lab import retrieval
from regime_lab.config import load_config
from regime_lab.data.loader import DocsStore
from regime_lab.rag.select import ticker_matches

NAMES = {'005930': '삼성전자', '000660': 'SK하이닉스', '005935': '삼성전자우'}


@pytest.mark.parametrize('text,expected', [
    ('삼성전자 투자', True), ('삼성 전자가 발표', True), ('삼성전자의 발표', True),
    ('삼성전자에서 발표', True), ('삼성전자서비스 발표', False), ('삼성전자우 상승', False),
    ('가상삼성전자 발표', False), ('삼성 전자 서비스', True), ('삼성 발표', False),
    ('삼성전자가짜 발표', False),
])
def test_complete_current_name_mention(text, expected):
    docs = [make_doc(1, '', text, '', T0)]
    before = deepcopy(docs)
    result = ticker_matches(docs, [0], ['005930'], NAMES, load_config()['rag']['query_particles'])
    assert bool(result) is expected
    assert docs == before
    if expected:
        assert result[0] == {'codes': [], 'name_mentions': [{'code': '005930', 'name': '삼성전자', 'field': 'title'}]}


def test_code_or_mention_and_mixed_identifiers():
    docs = [make_doc(1, '', '별도 제목', '005930', T0),
            make_doc(2, '삼성전자', '실적', '', T0),
            make_doc(3, '', 'sk하이닉스 실적', '', T0),
            make_doc(4, '', '삼성전자 실적', '005930', T0)]
    result = ticker_matches(docs, list(range(4)), ['005930', '000660'], NAMES, load_config()['rag']['query_particles'])
    assert set(result) == {0, 1, 2, 3}
    assert result[0] == {'codes': ['005930'], 'name_mentions': []}
    assert result[1]['name_mentions'][0]['field'] == 'corp_name'
    assert result[2]['name_mentions'][0]['code'] == '000660'
    assert result[3]['codes'] and result[3]['name_mentions']
    assert ticker_matches(docs, [0, 1], ['999999'], {}, []) == {}


def make_index(tmp_path, docs, cfg, tag='store'):
    return retrieval.open_index(DocsStore(write_store(tmp_path / tag, docs), tmp_path / 'cache'), cfg, use_cache=False)


@pytest.mark.parametrize('mode', ['observed', 'historical_assumed'])
def test_search_browse_cutoff_and_original_contract(tmp_path, mode):
    cfg = load_config()
    seen = T0 + timedelta(hours=1)
    docs = [make_doc(0, '', '삼성전자 자기주식 계획 발표', '', T0),
            make_doc(1, '', '삼성전자 자기주식 결과 발표', '005930', T0),
            make_doc(2, '', '대양중공업 계약 발표', '', T0),
            make_doc(3, '', '삼성전자서비스 자기주식', '', T0)]
    future = [make_doc(4, '', '삼성전자 자기주식 미래 문서', '', T0 + timedelta(days=2)),
              {**make_doc(0, '', '다른 회사로 제목 변경', '', T0 + timedelta(days=2)), 'version': 2},
              {**make_doc(2, '', '삼성전자 자기주식 정정', '', T0 + timedelta(days=2)), 'version': 2}]
    full = make_index(tmp_path, docs + future, cfg)
    cut = make_index(tmp_path, docs, cfg, 'cut')
    before = deepcopy(full.docs)
    opts = dict(mode=mode, tickers=['005930'], ticker_names=NAMES)
    a = retrieval.search(full, cfg, '삼성전자 자기주식', seen, **opts)
    b = retrieval.search(cut, cfg, '삼성전자 자기주식', seen, **opts)
    assert a['items'] == b['items']
    returned = a['items'] + [s for item in a['items'] for s in item['similar']]
    assert {item['doc_id'] for item in returned} == {docs[0]['doc_id'], docs[1]['doc_id']}
    assert all('ticker_match' in item for item in returned)
    assert a['index']['ticker_filter'] == 'code_or_name_mention'
    assert retrieval.search(full, cfg, '삼성전자 화성기지', seen, **opts)['items'] == []
    listing = retrieval.browse(full, cfg, seen, **opts)
    cut_listing = retrieval.browse(cut, cfg, seen, **opts)
    assert listing['items'] == cut_listing['items'] and listing['total'] == 2
    original = retrieval.search(full, cfg, '삼성전자 자기주식', seen, mode=mode, tickers=['005930'])
    assert len(original['items']) == 1 and original['items'][0]['doc_id'] == docs[1]['doc_id']
    assert 'ticker_match' not in original['items'][0] and 'ticker_filter' not in original['index']
    assert full.docs == before


def test_mention_filter_does_not_promote_links_or_missing_words(tmp_path):
    cfg = load_config()
    doc = make_doc(0, '', '삼성전자 발표', '', T0)
    ei = make_index(tmp_path, [doc], cfg)
    options = dict(tickers=['005930'], ticker_names=NAMES)
    res = retrieval.browse(ei, cfg, T0, **options)
    item = res['items'][0]
    assert item['tickers'] == [] and item['ticker_status'] is None
    assert item['statuses'] == ['unlinked']
    assert retrieval.document(ei, cfg, doc['doc_id'], T0)['doc']['tickers'] == []
    assert retrieval.search(ei, cfg, '자기주식', T0, **options)['items'] == []
