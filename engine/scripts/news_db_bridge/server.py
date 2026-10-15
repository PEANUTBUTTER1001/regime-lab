"""Loopback-only authenticated import service; no article text or secrets in logs."""
import argparse
import hmac
import json
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from normalize import KINDS
from store import Store
from remote_sync import RemoteSync, RemoteSyncError


def make_handler(store, token, config, remote_sync=None):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(config['request_timeout_seconds'])

        def log_message(self, *args):
            pass

        def respond(self, code, data):
            payload = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(payload)

        def allowed(self):
            # No browser cross-origin access and no DNS rebinding Host values.
            if self.headers.get('Origin') or self.headers.get('Host') not in {
                f"127.0.0.1:{config['port']}", f"localhost:{config['port']}"}:
                self.respond(403, {'error': 'forbidden_origin'})
                return False
            if not hmac.compare_digest(self.headers.get('X-Import-Key', ''), token):
                self.respond(401, {'error': 'unauthorized'})
                return False
            return True

        def do_GET(self):
            if self.path == '/healthz':
                self.respond(200, {'status': 'ok', 'service': 'sheets-db-bridge', 'schema_version': 1})
            elif self.path == '/v1/status' and self.allowed():
                result = store.status()
                if remote_sync is not None:
                    result['remote_db'] = remote_sync.status()
                self.respond(200, result)
            elif self.path != '/v1/status':
                self.respond(404, {'error': 'not_found'})

        def do_POST(self):
            if not self.allowed():
                return
            if self.path != '/v1/import':
                self.respond(404, {'error': 'not_found'})
                return
            try:
                if self.headers.get_content_type() != 'application/json':
                    raise ValueError
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= config['max_body_bytes']:
                    self.respond(413, {'error': 'body_limit'})
                    return
                body = json.loads(self.rfile.read(length), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                if body.get('spreadsheet_id') != config['spreadsheet_id'] or body.get('kind') not in KINDS:
                    raise ValueError
                rows = body['rows']
                if not isinstance(rows, list) or len(rows) > config['max_rows'] or any(not isinstance(r, dict) for r in rows):
                    raise ValueError
            except (ValueError, KeyError, TypeError, AttributeError):
                self.respond(400, {'error': 'invalid_request'})
                return
            try:
                result = store.sync(body['kind'], rows, datetime.now(timezone.utc).isoformat())
                if remote_sync is not None and body['kind'] == 'raw':
                    try:
                        result['remote_db'] = remote_sync.sync()
                    except RemoteSyncError:
                        self.respond(503, {'error': 'remote_sync_unavailable',
                                           'retry_safe': True, 'local_result': result})
                        return
                self.respond(200, result)
            except Exception:
                self.respond(503, {'error': 'storage_unavailable', 'retry_safe': True})
    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding='utf-8-sig'))
    token = Path(config['token_file']).read_text(encoding='utf-8').strip()
    if len(token) < 32:
        raise ValueError('invalid_service_key')
    store = Store(config['store_root'])
    remote_config = config.get('remote_db', {})
    remote = RemoteSync(config['store_root'], remote_config) if remote_config.get('enabled') else None
    # Bind before recovery so a second instance never touches an active export.
    server = HTTPServer(('127.0.0.1', config['port']), make_handler(store, token, config, remote))
    server.socket.settimeout(config['request_timeout_seconds'])
    store.recover_exports()
    try:
        server.serve_forever()
    finally:
        server.server_close()
        store.close()


if __name__ == '__main__':
    main()
