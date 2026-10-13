import json
from http.server import HTTPServer
import sys
import threading
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from remote_sync import RemoteSync, RemoteSyncError
from remote_transfer import canonical, load_snapshot, projection
from server import make_handler
from store import Store

NOW = '2026-10-08T03:00:00+00:00'
ROW = {'원기사 제목': "한글 ' 테스트 \\ 😀", 'RSS 요약': 'DO_NOT_SEND_PRIVATE_SUMMARY',
       '출처': 'https://example.com/a', '게시 시각': '2026-10-01T09:00:00+09:00',
       '최초 수집 시각': '2026-09-01T09:00:00+09:00'}


@pytest.fixture
def local(tmp_path):
    store = Store(tmp_path / 'store')
    store.sync('raw', [ROW], NOW)
    env = tmp_path / 'fake.env'
    env.write_text('test-only')
    sync = RemoteSync(store.root, {'env_file':str(env), 'client_executable':sys.executable,
                                 'state_write_attempts':5, 'state_write_delay_seconds':0.1})
    yield store, sync
    store.close()


def report(snapshot):
    sid, docs, *_ = snapshot
    return dict(snapshot_id=sid,documents=len(docs),versions=len(docs),
                new_versions=len(docs),reused_versions=0,coverage='unknown')


def test_failure_preserves_raw_and_success_replays(local):
    store, sync = local
    client = Mock()
    client.query.return_value = ['1']
    with patch('remote_sync.Client', return_value=client), patch('remote_sync.transfer', side_effect=RuntimeError('PASSWORD-secret')):
        with pytest.raises(RemoteSyncError, match='^remote_sync_unavailable$'):
            sync.sync()
    assert store.status()['current']['raw'] == 1
    assert sync.status()['status'] == 'pending'
    assert 'PASSWORD' not in sync.state_path.read_text()
    client.close.assert_called_once()
    def successful(client, snapshot, **kwargs):
        assert kwargs == {'commit': True, 'export_root': None}
        docs = snapshot[1]
        assert all(d['summary'] is None and d['body_ref'] is None for d in docs.values())
        assert 'DO_NOT_SEND' not in canonical(list(docs.values()))
        assert all(d['first_seen_at'] == NOW for d in docs.values())
        return report(snapshot)
    with patch('remote_sync.Client', return_value=Mock(query=Mock(return_value=['1']))), patch('remote_sync.transfer', side_effect=successful):
        first = sync.sync()
        repeated = sync.sync()
    assert first['snapshot_id'] == repeated['snapshot_id']
    assert sync.status()['status'] == 'ok'


def test_busy_lock_does_not_upload(local):
    _, sync = local
    client = Mock(query=Mock(return_value=['0']))
    with patch('remote_sync.Client', return_value=client), patch('remote_sync.transfer') as send:
        with pytest.raises(RemoteSyncError):
            sync.sync()
    send.assert_not_called()
    client.close.assert_called_once()


def test_connect_failure_fixed_error(local):
    _, sync = local
    with patch('remote_sync.Client', side_effect=RuntimeError('secret')):
        with pytest.raises(RemoteSyncError, match='^remote_sync_unavailable$'):
            sync.sync()
    assert sync.status()['status'] == 'pending'
    assert 'secret' not in sync.state_path.read_text()


def test_commit_then_state_write_failure_retry_safe(local):
    _, sync = local
    save = sync.save
    seen = []
    def write(state):
        if state['status'] == 'ok' and not seen:
            seen.append(1)
            raise OSError('disk')
        save(state)
    def send(client, snapshot, **kwargs):
        return report(snapshot)
    with patch('remote_sync.Client', return_value=Mock(query=Mock(return_value=['1']))), patch('remote_sync.transfer', side_effect=send), patch.object(sync, 'save', side_effect=write):
        with pytest.raises(RemoteSyncError):
            sync.sync()
        assert sync.status()['status'] == 'pending'
        assert sync.sync()['status'] == 'ok'


def test_no_raw_is_not_remote_success(tmp_path):
    env = tmp_path / 'fake.env'
    env.touch()
    sync = RemoteSync(tmp_path / 'empty', {'env_file':str(env), 'client_executable':sys.executable,
                                         'state_write_attempts':5, 'state_write_delay_seconds':0.1})
    with patch('remote_sync.Client') as client:
        assert sync.sync()['status'] == 'no_raw_data'
        client.assert_not_called()
    assert sync.status()['status'] == 'no_raw_data'


def test_version_changes_and_original_ids_stay(local):
    store, sync = local
    snapshots = []
    def send(client, snapshot, **kwargs):
        snapshots.append(snapshot)
        return report(snapshot)
    with patch('remote_sync.Client', return_value=Mock(query=Mock(return_value=['1']))), patch('remote_sync.transfer', side_effect=send):
        sync.sync()
        store.sync('raw', [{**ROW, '원기사 제목':'변경 제목'}], '2026-10-09T03:00:00+00:00')
        sync.sync()
    old, new = snapshots
    assert old[0] != new[0]
    assert len(old[1]) == 1 and len(new[1]) == 2
    key = next(iter(old[1]))
    assert old[1][key] == new[1][key]
    assert {d['version'] for d in new[1].values()} == {1, 2}


@pytest.fixture
def service():
    store = Mock()
    store.sync.return_value = {'kind':'raw','status':'ok','rejected':0,'inserted':1}
    store.status.return_value = {'versions':1}
    remote = Mock()
    remote.status.return_value = {'status':'pending'}
    config = {'port':0,'request_timeout_seconds':5,'spreadsheet_id':'synthetic',
              'max_body_bytes':10000,'max_rows':100}
    http = HTTPServer(('127.0.0.1',0), make_handler(store,'fake-import-key',config,remote))
    config['port'] = http.server_port
    worker = threading.Thread(target=http.serve_forever, daemon=True)
    worker.start()
    yield f'http://127.0.0.1:{http.server_port}',store,remote
    http.shutdown()
    http.server_close()
    worker.join()


def request(base, kind='raw', key='fake-import-key'):
    payload = json.dumps({'spreadsheet_id':'synthetic','kind':kind,'rows':[ROW]}).encode()
    req = Request(base+'/v1/import',data=payload,headers={'Content-Type':'application/json','X-Import-Key':key})
    try:
        with urlopen(req,timeout=5) as response:
            return response.status,json.load(response)
    except HTTPError as error:
        return error.code,json.load(error)


def test_http_remote_failure_503_then_retry_success(service):
    base, store, remote = service
    remote.sync.side_effect = [RemoteSyncError('safe'), {'status':'ok','new_versions':0}]
    status, body = request(base)
    assert status == 503 and body['retry_safe']
    assert body['local_result']['inserted'] == 1
    assert request(base)[1]['remote_db']['status'] == 'ok'
    assert store.sync.call_count == 2


@pytest.mark.parametrize('kind',['briefing','stocks'])
def test_generated_results_never_sent_to_remote(service, kind):
    base, store, remote = service
    assert request(base,kind)[0] == 200
    remote.sync.assert_not_called()


def test_unauthorized_request_never_stores_or_uploads(service):
    base, store, remote = service
    assert request(base,key='bad')[0] == 401
    store.sync.assert_not_called()
    remote.sync.assert_not_called()


def test_local_failure_never_uploads(service):
    base, store, remote = service
    store.sync.side_effect = OSError('secret')
    status, body = request(base)
    assert status == 503 and body['error'] == 'storage_unavailable'
    remote.sync.assert_not_called()


def test_state_replace_retries_transient_windows_lock(local):
    _, sync = local
    import os
    original = os.replace
    calls = []
    def replace(src, dst):
        calls.append(1)
        if len(calls) == 1:
            raise PermissionError('temporary file lock')
        original(src, dst)
    with patch('remote_sync.os.replace', side_effect=replace), patch('remote_sync.time.sleep'):
        sync.save({'status':'pending'})
    assert len(calls) == 2
    assert sync.status()['status'] == 'pending'
