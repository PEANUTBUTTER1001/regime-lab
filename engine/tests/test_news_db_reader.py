import base64
import io
import urllib.error
from urllib.parse import urlsplit, parse_qs
import pytest
import pyarrow as pa

from regime_lab import retrieval
from regime_lab.config import load_config
from regime_lab.data.loader import DocsStore
from regime_lab.data.news_db import Client, read_latest as _read_latest, canonical, projection, _NoRedirect
SID='a'*64
SOURCES=['n8n_news','n8n_disclosure']


def read_latest(*args, **kwargs):
    policy = load_config()['news_refresh']
    options = {key: policy[key] for key in ('page_size', 'max_versions')}
    return _read_latest(*args, **(options | kwargs))


def doc(source,i=0,seen='2026-10-13T00:00:00+00:00'):
    return dict(doc_id=source+':'+str(i),version=1,source=source,source_type='news' if source=='n8n_news' else 'disclosure',
        title='반도체 합성자료 '+str(i),corp_name=None,url='https://example.com/'+str(i),
        published_at=seen,time_precision='datetime',published_at_basis='upstream_metadata',first_seen_at=seen,available_at=seen,
        backfilled=False,tickers=[],ticker_status='none',content_hash='test',license_scope='metadata_only',summary=None,body_ref=None,status='active')


class FakeHTTP:
    def __init__(self, bad=None):
        self.bad = bad
        self.calls = []
        self.docs = [doc(s, i) for i, s in enumerate(SOURCES)]

    def open(self, request, timeout):
        self.calls.append(request)
        parsed = urlsplit(request.full_url)
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        if parsed.path.endswith('/latest'):
            if self.bad == 'missing':
                raise urllib.error.HTTPError(request.full_url, 404, 'private failure', {}, io.BytesIO(b'private'))
            value = {'snapshot_id': SID}
        elif parsed.path.endswith('/documents'):
            values = sorted([d for d in self.docs if d['source'] == query['source']], key=lambda d: (d['doc_id'], d['version']))
            if 'after_doc_id' in query:
                values = [d for d in values if (d['doc_id'], d['version']) > (query['after_doc_id'], int(query['after_version']))]
            limit = int(query['limit'])
            rows = []
            for d in values[:limit]:
                values_for_row = projection(d)
                row = dict(zip(['doc_id','version','source','status','license_scope','first_seen_at_utc','available_at_utc','record_sha256','document_json'], values_for_row))
                if self.bad == 'hash': row['record_sha256'] = 'bad'
                if self.bad == 'membership': row['source'] = 'outside'
                rows.append(row)
            if self.bad == 'partial': rows = []
            nxt = {'doc_id': rows[-1]['doc_id'], 'version': rows[-1]['version']} if len(values)>limit else None
            if self.bad == 'cursor': nxt = {'doc_id': 'bad', 'version': 1}
            value = {'snapshot_id': SID, 'source': query['source'], 'rows': rows, 'next_cursor': nxt}
        else:
            manifests = []
            counts = {}
            for source in SOURCES:
                values = [d for d in self.docs if d['source'] == source]
                counts[source] = len(values)
                schema = pa.Table.from_pylist(values).schema
                manifest = dict(source=source, schema_version=1, coverage='unknown', license_scope='metadata_only')
                manifests.append(dict(source=source, manifest_json=canonical(manifest),
                    arrow_schema_json=canonical({'ipc_base64': base64.b64encode(schema.serialize()).decode()}),
                    document_versions=len(values), coverage_state='unknown', coverage_rows=0))
            if self.bad == 'members': counts[SOURCES[0]] -= 1
            if self.bad == 'count': manifests[0]['document_versions'] += 1
            if self.bad == 'source': manifests[0]['source'] = '../outside'
            value = {'snapshot_id': SID, 'manifests': manifests, 'member_counts': counts,
                     'coverage_rows': 1 if self.bad == 'coverage' else 0}
        return io.BytesIO(canonical(value).encode())


def client(fake):
    return Client('https://example.invalid', 'test-token', load_config()['news_refresh'], opener=fake)


def test_verified_roundtrip_and_get_only(tmp_path):
    fake = FakeHTTP()
    fake.docs.append(doc(SOURCES[0], 5))
    path, report = read_latest(client(fake), tmp_path, SOURCES, page_size=1)
    index = retrieval.open_index(DocsStore(path, tmp_path/'cache'), load_config(), use_cache=False)
    assert report['documents'] == 3 and len(index.docs) == 3
    assert all(r.get_method() == 'GET' for r in fake.calls)
    assert all(r.get_header('X-api-key') == 'test-token' for r in fake.calls)
    assert any('after_doc_id=' in r.full_url for r in fake.calls)


@pytest.mark.parametrize('bad', ['missing','members','count','hash','coverage','source','partial','membership','cursor'])
def test_invalid_snapshot_never_published(tmp_path, bad):
    with pytest.raises(ValueError):
        read_latest(client(FakeHTTP(bad)), tmp_path/'out', SOURCES)
    assert not (tmp_path/'out').exists()


def test_roundtrip_mismatch_never_published(tmp_path, monkeypatch):
    from regime_lab.data import news_db
    original = news_db.pq.write_table
    def corrupt(table, path):
        original(table.slice(0, 0), path)
    monkeypatch.setattr(news_db.pq, 'write_table', corrupt)
    with pytest.raises(ValueError, match='roundtrip'):
        read_latest(client(FakeHTTP()), tmp_path, SOURCES)
    assert not list(tmp_path.glob(SID + '*'))


def test_unchanged_snapshot_skips_download(tmp_path):
    fake = FakeHTTP()
    assert read_latest(client(fake), tmp_path, SOURCES, current_id=SID) is None
    assert len(fake.calls) == 1


def test_partial_ingest_keeps_last_snapshot_and_retries(tmp_path):
    fake = FakeHTTP()
    old, _ = read_latest(client(fake), tmp_path, SOURCES)
    before = {str(p.relative_to(old)): p.read_bytes() for p in old.rglob('*') if p.is_file()}
    fake.bad = 'partial'
    with pytest.raises(ValueError, match='Incomplete'):
        read_latest(client(fake), tmp_path, SOURCES)
    assert before == {str(p.relative_to(old)): p.read_bytes() for p in old.rglob('*') if p.is_file()}
    fake.bad = None
    new, _ = read_latest(client(fake), tmp_path, SOURCES)
    assert new != old


@pytest.mark.parametrize('failure', ['http','connection','oversize','json','redirect'])
def test_transport_limits_and_no_secret_in_errors(failure):
    class Broken:
        def open(self, request, timeout):
            assert timeout == 30
            if failure == 'http':
                raise urllib.error.HTTPError(request.full_url, 503, 'secret', {}, io.BytesIO(b'secret'))
            if failure == 'connection': raise urllib.error.URLError('secret')
            if failure == 'redirect':
                return _NoRedirect().redirect_request(request, None, 302, 'secret', {}, 'https://other.invalid')
            return io.BytesIO(b'x' * 65 if failure == 'oversize' else b'not-json')
    policy = {**load_config()['news_refresh'], 'max_response_bytes': 64}
    with pytest.raises(ValueError) as error:
        Client('https://secret.invalid', 'secret', policy, Broken()).get('/v1/ext/snapshots/latest')
    assert 'secret' not in str(error.value)


@pytest.mark.parametrize('url', ['file:///private', 'https://user:secret@example.invalid', 'https://example.invalid?token=secret'])
def test_invalid_connection_value_not_echoed(url):
    with pytest.raises(ValueError) as error:
        Client(url, None, load_config()['news_refresh'])
    assert 'secret' not in str(error.value)


def test_http_snapshot_preserves_available_time_cutoff(tmp_path):
    fake = FakeHTTP()
    fake.docs[1]['published_at'] = '2026-10-01T00:00:00+00:00'
    fake.docs[1]['first_seen_at'] = fake.docs[1]['available_at'] = '2026-10-14T00:00:00+00:00'
    path, _ = read_latest(client(fake), tmp_path, SOURCES)
    cfg = load_config()
    index = retrieval.open_index(DocsStore(path, tmp_path/'cache'), cfg, use_cache=False)
    before = retrieval.search(index, cfg, '반도체', '2026-10-13T12:00:00+00:00')
    after = retrieval.search(index, cfg, '반도체', '2026-10-14T12:00:00+00:00')
    assert before['index']['visible_docs'] == 1
    assert after['index']['visible_docs'] == 2
