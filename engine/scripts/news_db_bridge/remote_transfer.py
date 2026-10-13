"""One-off C3 metadata snapshot transfer. Default: insert, verify, ROLLBACK.

Uses existing MariaDB CLI and pyarrow; no project runtime changes. SQL values
are UTF-8 hex literals. Credentials only enter the child process environment.
"""
import argparse
import base64
from collections import Counter
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import uuid

import pyarrow as pa
import pyarrow.parquet as pq

POLICY = 'c3-metadata-copy-v3.1'
TABLES = ('ext_document_manifests', 'ext_document_versions',
          'ext_document_snapshot_members', 'ext_document_coverage')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def sql_value(value):
    if value is None:
        return 'NULL'
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return "CONVERT(X'" + str(value).encode('utf-8').hex() + "' USING utf8mb4)"


def utc(value):
    dt = datetime.fromisoformat(value)
    if dt.utcoffset() is None:
        raise ValueError('Timezone required')
    return dt.astimezone(timezone.utc).replace(tzinfo=None).isoformat(' ', timespec='microseconds')


def projection(doc):
    source = doc['source']
    if not doc['doc_id'].startswith(source + ':') or len(doc['doc_id']) > 191:
        raise ValueError('Document namespace/length invalid')
    if type(doc['version']) is not int or not 0 < doc['version'] < 2**64:
        raise ValueError('Invalid version')
    status = doc.get('status', 'active')
    if status not in ('active', 'deleted'):
        raise ValueError('Unsupported status')
    if doc['license_scope'] != 'metadata_only' or doc.get('summary') is not None or doc.get('body_ref') is not None:
        raise ValueError('Outside metadata-only transfer scope')
    if len(source) > 64 or not doc.get('title') or not isinstance(doc.get('tickers'), list):
        raise ValueError('Invalid metadata fields')
    published = doc.get('published_at')
    if published:
        if doc['time_precision'] == 'date_only':
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', published):
                raise ValueError('Invalid date-only publication')
            datetime.strptime(published, '%Y-%m-%d')
        elif doc['time_precision'] == 'datetime':
            utc(published)
        else:
            raise ValueError('Unsupported time precision')
    return [doc['doc_id'], doc['version'], source, status, doc['license_scope'],
            utc(doc['first_seen_at']), utc(doc['available_at']), digest(doc), canonical(doc)]


def load_snapshot(root):
    files = sorted(list((root / 'docs').rglob('*.parquet')) +
                   list((root / 'docs').rglob('_manifest.json')))
    if (root / 'coverage').exists():
        raise ValueError('This one-off importer only accepts the approved coverage-unknown snapshot')
    data = {p.relative_to(root).as_posix(): p.read_bytes() for p in files}
    if not data:
        raise ValueError('Empty snapshot')
    identities = {k: hashlib.sha256(v).hexdigest() for k, v in data.items()}
    sid = digest({'policy': POLICY, 'files': identities})
    manifests, schemas, docs = {}, {}, {}
    for name, blob in data.items():
        if name.endswith('_manifest.json'):
            manifest = json.loads(blob)
            source = manifest['source']
            if manifest.get('schema_version') != 1 or manifest.get('coverage') != 'unknown':
                raise ValueError('Unexpected manifest scope')
            manifests[source] = manifest
            continue
        table = pq.ParquetFile(io.BytesIO(blob)).read()
        for doc in table.to_pylist():
            projection(doc)
            source = doc['source']
            schema = table.schema
            if source in schemas and not schemas[source].equals(schema):
                raise ValueError('Mixed source schemas')
            schemas[source] = schema
            key = (doc['doc_id'], doc['version'])
            if key in docs:
                raise ValueError('Duplicate source key')
            docs[key] = doc
    if not docs or set(manifests) != set(schemas):
        raise ValueError('Missing source manifest/schema')
    return sid, docs, manifests, schemas, identities


class Client:
    def __init__(self, env_path, executable):
        conf = {}
        for line in env_path.read_text(encoding='utf-8-sig').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                conf[key.strip()] = value.strip()
        if conf['DB_NAME'] != 'regime_lab':
            raise ValueError('Unexpected database')
        child_env = os.environ.copy()
        child_env['MYSQL_PWD'] = conf['DB_PASSWORD']
        args = [executable, '--no-defaults', '--protocol=TCP', '--connect-timeout=8',
                '--host=' + conf['DB_HOST'], '--port=' + conf['DB_PORT'],
                '--user=' + conf['DB_USER'], '--database=' + conf['DB_NAME'],
                '--default-character-set=utf8mb4', '--batch', '--raw',
                '--skip-column-names', '--unbuffered', '--skip-reconnect']
        self.proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, encoding='utf-8',
                                     env=child_env, creationflags=subprocess.CREATE_NO_WINDOW)
        self.lines, self.errors = queue.Queue(), []
        def read_stdout():
            for line in self.proc.stdout:
                self.lines.put(line.rstrip('\r\n'))
            self.lines.put(None)
        def read_stderr():
            for line in self.proc.stderr:
                match = re.search(r'ERROR\s+(\d+)', line)
                if match:
                    self.errors.append(match[1])
        threading.Thread(target=read_stdout, daemon=True).start()
        threading.Thread(target=read_stderr, daemon=True).start()
        try:
            self.query("SET SESSION sql_mode='STRICT_ALL_TABLES,NO_ENGINE_SUBSTITUTION'; SET SESSION time_zone='+00:00';")
        except Exception:
            self.close()
            raise

    def query(self, statement):
        marker = 'c3_end_' + uuid.uuid4().hex
        try:
            self.proc.stdin.write(statement + "\nSELECT '" + marker + "';\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            raise RuntimeError('Database client stopped') from None
        rows = []
        while True:
            try:
                line = self.lines.get(timeout=45)
            except queue.Empty:
                raise RuntimeError('Database response timeout') from None
            if line is None:
                raise RuntimeError('Database query failed; error codes=' + ','.join(self.errors))
            if line == marker:
                return rows
            rows.append(line)

    def close(self):
        if self.proc.poll() is None:
            self.proc.stdin.close()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.terminate()
                self.proc.wait(timeout=5)


def insert(client, table, columns, rows):
    if table not in TABLES:
        raise ValueError('Unexpected target table')
    for start in range(0, len(rows), 40):
        values = ','.join('(' + ','.join(sql_value(v) for v in row) + ')' for row in rows[start:start+40])
        client.query('INSERT INTO ' + table + ' (' + ','.join(columns) + ') VALUES ' + values + ';')


def json_from_hex(value):
    return json.loads(bytes.fromhex(value).decode('utf-8'))


def verify_docs(client, expected, where=''):
    rows = client.query('SELECT HEX(document_json),doc_id,version,source,status,license_scope,'
                        "DATE_FORMAT(first_seen_at_utc,'%Y-%m-%d %H:%i:%s.%f'),"
                        "DATE_FORMAT(available_at_utc,'%Y-%m-%d %H:%i:%s.%f'),record_sha256 "
                        'FROM ext_document_versions ' + where + ';')
    found = {}
    for row in rows:
        cells = row.split('\t')
        doc = json_from_hex(cells[0])
        projected = projection(doc)
        if cells[1:] != [str(v) for v in projected[:-1]]:
            raise ValueError('DB projection/hash mismatch')
        key = (doc['doc_id'], doc['version'])
        if key in expected and canonical(doc) != canonical(expected[key]):
            raise ValueError('Existing document key has different content; refusing overwrite')
        found[key] = doc
    return found


def transfer(client, snapshot, commit, export_root):
    sid, docs, manifests, schemas, identities = snapshot
    counts = Counter(d['source'] for d in docs.values())
    mapping = {'mapping_version': POLICY, 'status_source': 'absent_means_active(engine rule)',
               'scope': 'metadata_only_local_search_snapshot', 'timestamps': 'preserve_original_offset; derived_utc'}
    client.query('START TRANSACTION;')
    try:
        # Read all keys to detect cross-source collisions, not just the current sources.
        existing = verify_docs(client, docs)
        fresh = [projection(doc) for key, doc in sorted(docs.items()) if key not in existing]
        insert(client, TABLES[1], ['doc_id','version','source','status','license_scope',
               'first_seen_at_utc','available_at_utc','record_sha256','document_json'], fresh)
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(' ', timespec='microseconds')
        expected_manifests = {}
        prior = client.query('SELECT source FROM ext_document_manifests WHERE snapshot_id=' + sql_value(sid) + ';')
        for source in sorted(manifests):
            original = manifests[source]
            schema_record = {'ipc_base64': base64.b64encode(schemas[source].serialize().to_pybytes()).decode('ascii'),
                             'fields': [{'name': f.name, 'type': str(f.type), 'nullable': f.nullable} for f in schemas[source]]}
            norm = original.get('normalization_policy_version')
            time_policy = original.get('time_policy_version') or original.get('time_policy')
            expected = [source, original['schema_version'], norm, time_policy, canonical(original),
                        canonical(mapping), canonical(schema_record), 'unknown', counts[source], 0]
            expected_manifests[source] = expected
            if source not in prior:
                insert(client, TABLES[0], ['snapshot_id','source','schema_version','normalization_policy_version',
                       'time_policy_version','manifest_json','mapping_policy_json','arrow_schema_json',
                       'coverage_state','document_versions','coverage_rows','imported_at_utc'], [[sid] + expected + [now]])
        old_members = client.query('SELECT doc_id,version,source FROM ext_document_snapshot_members WHERE snapshot_id=' + sql_value(sid) + ';')
        expected_members = {d['doc_id']+'\t'+str(d['version'])+'\t'+d['source'] for d in docs.values()}
        if prior:
            if set(prior) != set(manifests) or set(old_members) != expected_members:
                raise ValueError('Existing immutable snapshot differs')
        else:
            insert(client, TABLES[2], ['snapshot_id','source','doc_id','version'],
                   [[sid,d['source'],d['doc_id'],d['version']] for _,d in sorted(docs.items())])
        found = verify_docs(client, docs, 'WHERE (doc_id,version) IN (SELECT doc_id,version FROM ext_document_snapshot_members WHERE snapshot_id=' + sql_value(sid) + ')')
        if set(found) != set(docs):
            raise ValueError('Snapshot document set mismatch')
        for source, expected in expected_manifests.items():
            fields = client.query("SELECT source,schema_version,COALESCE(normalization_policy_version,'NULL'),COALESCE(time_policy_version,'NULL'),HEX(manifest_json),HEX(mapping_policy_json),HEX(arrow_schema_json),coverage_state,document_versions,coverage_rows FROM ext_document_manifests WHERE snapshot_id=" + sql_value(sid) + ' AND source=' + sql_value(source) + ';')
            if len(fields) != 1:
                raise ValueError('Manifest missing')
            cells = fields[0].split('\t')
            actual = cells[:4] + [canonical(json_from_hex(v)) for v in cells[4:7]] + cells[7:]
            wanted = ['NULL' if v is None else str(v) for v in expected]
            if actual != wanted:
                raise ValueError('Manifest/provenance mismatch')
        if client.query('SELECT COUNT(*) FROM ext_document_coverage WHERE snapshot_id=' + sql_value(sid) + ';') != ['0']:
            raise ValueError('Unexpected fabricated coverage')
        # Round trip only to a new output directory, before committing.
        if export_root is not None and export_root.exists():
            raise ValueError('Roundtrip output already exists')
        for source in sorted(manifests) if export_root is not None else []:
            target = export_root / 'docs' / ('source=' + source)
            target.mkdir(parents=True)
            selected = [doc for _,doc in sorted(found.items()) if doc['source'] == source]
            table = pa.Table.from_pylist(selected, schema=schemas[source])
            pq.write_table(table, target / 'part-roundtrip.parquet')
            readback = pq.ParquetFile(target / 'part-roundtrip.parquet').read()
            if not readback.schema.equals(schemas[source]) or canonical(readback.to_pylist()) != canonical(selected):
                raise ValueError('Parquet roundtrip mismatch')
            (target / '_manifest.json').write_text(canonical(manifests[source]), encoding='utf-8')
        client.query('COMMIT;' if commit else 'ROLLBACK;')
        return {'snapshot_id':sid, 'result':'committed' if commit else 'rolled_back',
                'versions':len(docs), 'documents':len({k[0] for k in docs}),
                'new_versions':len(fresh), 'reused_versions':len(docs)-len(fresh),
                'versions_by_source':dict(counts), 'coverage':'unknown', 'coverage_rows':0,
                'verified':'all fields, hashes, UTC projections, manifests, membership, Arrow schema' + (' and Parquet roundtrip' if export_root is not None else ''),
                'input_files':identities, 'automatic_updates':False}
    except Exception:
        if client.proc.poll() is None:
            client.query('ROLLBACK;')
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--env', type=Path, required=True)
    parser.add_argument('--client', required=True)
    parser.add_argument('--export', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--commit', action='store_true')
    args = parser.parse_args()
    snapshot = load_snapshot(args.snapshot)
    client = Client(args.env, args.client)
    try:
        report = transfer(client, snapshot, args.commit, args.export)
    finally:
        client.close()
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k != 'input_files'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
