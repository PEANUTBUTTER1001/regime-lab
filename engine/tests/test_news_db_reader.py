import base64
import pytest
import pyarrow as pa

from regime_lab import retrieval
from regime_lab.config import load_config
from regime_lab.data.loader import DocsStore
from regime_lab.data.news_db import read_latest, canonical, projection
SID='a'*64
SOURCES=['n8n_news','n8n_disclosure']


def doc(source,i=0,seen='2026-10-13T00:00:00+00:00'):
    return dict(doc_id=source+':'+str(i),version=1,source=source,source_type='news' if source=='n8n_news' else 'disclosure',
        title='반도체 합성자료 '+str(i),corp_name=None,url='https://example.com/'+str(i),
        published_at=seen,time_precision='datetime',published_at_basis='upstream_metadata',first_seen_at=seen,available_at=seen,
        backfilled=False,tickers=[],ticker_status='none',content_hash='test',license_scope='metadata_only',summary=None,body_ref=None,status='active')


def hexjson(v):return canonical(v).encode().hex()


class Fake:
    def __init__(self,bad=None):
        self.bad=bad;self.calls=[];self.docs=[doc(s,i) for i,s in enumerate(SOURCES)]
    def query(self,q):
        self.calls.append(q)
        if q.startswith(('START','ROLLBACK')):return []
        if q.startswith('SELECT snapshot_id'):return [] if self.bad=='missing' else [SID]
        if q.startswith('SELECT source,HEX'):
            out=[]
            for d in self.docs:
                source=d['source'];schema=pa.Table.from_pylist([d]).schema
                manifest=dict(source=source,schema_version=1,coverage='unknown',license_scope='metadata_only')
                out.append('\t'.join([source,hexjson(manifest),hexjson({'ipc_base64':base64.b64encode(schema.serialize()).decode()}),'1','unknown','0']))
            return out
        if q.startswith('SELECT doc_id'):
            values=[d['doc_id']+'\t1\t'+d['source'] for d in self.docs]
            return values[:-1] if self.bad=='members' else values
        if q.startswith('SELECT HEX(document_json)'):
            rows=[]
            for d in self.docs:
                p=projection(d)
                if self.bad=='hash':p[-2]='bad'
                rows.append('\t'.join([hexjson(d)]+[str(v) for v in p[:-1]]))
            return rows
        if q.startswith('SELECT COUNT(*)'):return ['1' if self.bad=='coverage' else '0']
        raise AssertionError(q)


def test_verified_roundtrip_and_read_only(tmp_path):
    client=Fake(); path,report=read_latest(client,tmp_path,SOURCES)
    index=retrieval.open_index(DocsStore(path,tmp_path/'cache'),load_config(),use_cache=False)
    assert report['documents']==2 and len(index.docs)==2
    assert all(q.startswith(('START TRANSACTION','SELECT','ROLLBACK')) for q in client.calls)
    assert client.calls[-1]=='ROLLBACK;'


@pytest.mark.parametrize('bad',['missing','members','hash','coverage'])
def test_invalid_snapshot_never_published(tmp_path,bad):
    client=Fake(bad)
    with pytest.raises(ValueError):read_latest(client,tmp_path/'out',SOURCES)
    assert not (tmp_path/'out').exists()
    assert client.calls[-1]=='ROLLBACK;'


def test_unchanged_snapshot_skips_download(tmp_path):
    client=Fake()
    assert read_latest(client,tmp_path,SOURCES,current_id=SID) is None
    assert len(client.calls)==3
