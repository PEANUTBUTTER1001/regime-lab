"""Raw metadata mirror for the existing loopback bridge; no credentials in state."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from uuid import uuid4

from remote_transfer import Client, load_snapshot, transfer
from search_snapshot import export


class RemoteSyncError(Exception):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


class RemoteSync:
    def __init__(self, store_root, config):
        self.root = Path(store_root)
        self.work = self.root / 'remote_sync'
        self.state_path = self.work / 'status.json'
        self.env_path = Path(config['env_file'])
        self.client_path = config['client_executable']
        self.state_write_attempts = config['state_write_attempts']
        self.state_write_delay = config['state_write_delay_seconds']
        if type(self.state_write_attempts) is not int or self.state_write_attempts < 1 or self.state_write_delay < 0:
            raise ValueError('remote_sync_configuration_invalid')
        if not self.env_path.is_file() or not Path(self.client_path).is_file():
            raise ValueError('remote_sync_configuration_unavailable')
        self.work.mkdir(parents=True, exist_ok=True)

    def status(self):
        if not self.state_path.exists():
            return {'enabled': True, 'status': 'not_attempted'}
        return json.loads(self.state_path.read_text(encoding='utf-8'))

    def save(self, state):
        target = self.state_path.with_name('status.' + uuid4().hex + '.tmp')
        target.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
        for attempt in range(self.state_write_attempts):
            try:
                os.replace(target, self.state_path)
                return
            except PermissionError:
                if attempt + 1 == self.state_write_attempts:
                    raise
                time.sleep(self.state_write_delay)

    def sync(self):
        previous = self.status()
        state = {'enabled': True, 'status': 'pending', 'attempted_at': now(),
                 'last_success': previous.get('last_success')}
        self.save(state)
        client = None
        try:
            history = self.root / 'parquet/kind=raw/history.parquet'
            if not history.is_file():
                # No raw collection yet is not evidence of a successful DB mirror.
                result = {'status': 'no_raw_data', 'documents': 0, 'versions': 0}
                self.save({**state, 'status': 'no_raw_data'})
                return result
            snapshot_path, _ = export(history, self.work / 'snapshots')
            snapshot = load_snapshot(snapshot_path)
            client = Client(self.env_path, self.client_path)
            if client.query("SELECT GET_LOCK('regime_ext_document_copy',0);") != ['1']:
                raise RemoteSyncError('remote_sync_busy')
            report = transfer(client, snapshot, commit=True, export_root=None)
            result = {key: report[key] for key in
                      ('snapshot_id','documents','versions','new_versions','reused_versions','coverage')}
            result['status'] = 'ok'
            result['confirmed_at'] = now()
            self.save({**state, 'status': 'ok', 'last_success': result})
            return result
        except Exception:
            # SQL and upstream exceptions may contain metadata or credentials.
            # Only a fixed code is persisted and returned.
            try:
                self.save({**state, 'status': 'pending', 'error': 'remote_sync_unavailable'})
            except OSError:
                pass
            raise RemoteSyncError('remote_sync_unavailable') from None
        finally:
            if client is not None:
                client.close()  # also releases the advisory lock
