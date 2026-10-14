"""Read-only access to approved C3 metadata snapshots; no database writes."""
import base64
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import uuid
from uuid import uuid4
import pyarrow as pa
import pyarrow.parquet as pq


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
                                     env=child_env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
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


def read_latest(client, root, sources, current_id=None):
    """Read only. Publish files only after all metadata and counts validate."""
    if not sources or len(set(sources)) != len(sources):
        raise ValueError('Invalid source scope')
    source_sql = ','.join(sql_value(s) for s in sources)
    client.query('START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY;')
    try:
        found = client.query(
            'SELECT snapshot_id FROM ext_document_manifests GROUP BY snapshot_id '
            'HAVING COUNT(*)=' + str(len(sources)) +
            ' AND SUM(source IN (' + source_sql + '))=' + str(len(sources)) +
            ' ORDER BY MAX(imported_at_utc) DESC,snapshot_id DESC LIMIT 1;')
        if len(found) != 1 or not re.fullmatch('[a-f0-9]{64}', found[0]):
            raise ValueError('No complete approved snapshot')
        sid = found[0]
        if sid == current_id:
            return None
        where = ' WHERE snapshot_id=' + sql_value(sid)
        raw = client.query('SELECT source,HEX(manifest_json),HEX(arrow_schema_json),'
                           'document_versions,coverage_state,coverage_rows '
                           'FROM ext_document_manifests' + where + ';')
        manifests, schemas, counts = {}, {}, {}
        for line in raw:
            source, manifest_hex, schema_hex, count, coverage, coverage_rows = line.split('\t')
            if source not in sources or source in manifests or coverage != 'unknown' or coverage_rows != '0':
                raise ValueError('Unsupported source or coverage contract')
            manifest = json_from_hex(manifest_hex)
            if manifest.get('source') != source or manifest.get('schema_version') != 1 or manifest.get('license_scope') != 'metadata_only' or manifest.get('coverage') != 'unknown':
                raise ValueError('Invalid manifest')
            manifests[source] = manifest
            schemas[source] = pa.ipc.read_schema(pa.BufferReader(base64.b64decode(json_from_hex(schema_hex)['ipc_base64'], validate=True)))
            counts[source] = int(count)
        if set(manifests) != set(sources) or any(c <= 0 for c in counts.values()):
            raise ValueError('Incomplete manifests')
        members = client.query('SELECT doc_id,version,source FROM ext_document_snapshot_members' + where + ';')
        expected = {}
        for row in members:
            did, version, source = row.split('\t')
            key = (did, int(version))
            if key in expected or source not in sources:
                raise ValueError('Invalid membership')
            expected[key] = source
        docs = verify_docs(client, {}, 'WHERE (doc_id,version) IN (SELECT doc_id,version FROM ext_document_snapshot_members' + where + ')')
        if set(docs) != set(expected) or any(d['source'] != expected[k] for k,d in docs.items()) or Counter(d['source'] for d in docs.values()) != counts:
            raise ValueError('Snapshot count/membership mismatch')
        if any(d.get('status','active') != 'active' for d in docs.values()):
            raise ValueError('Deletion policy not supported by this search runtime')
        if client.query('SELECT COUNT(*) FROM ext_document_coverage' + where + ';') != ['0']:
            raise ValueError('Coverage contract mismatch')
    finally:
        client.query('ROLLBACK;')
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    stage = root / ('pending-' + uuid4().hex)
    stage.mkdir()
    report = {'snapshot_id':sid, 'documents':len({d['doc_id'] for d in docs.values()}), 'versions':len(docs), 'coverage':'unknown'}
    for source in sorted(sources):
        folder = stage/'docs'/('source='+source)
        folder.mkdir(parents=True)
        values = [d for _,d in sorted(docs.items()) if d['source']==source]
        table = pa.Table.from_pylist(values, schema=schemas[source])
        path = folder/'part-00000.parquet'
        pq.write_table(table, path)
        check = pq.ParquetFile(path).read()
        if not check.schema.equals(schemas[source]) or canonical(check.to_pylist()) != canonical(values):
            raise ValueError('Snapshot roundtrip mismatch')
        (folder/'_manifest.json').write_text(canonical(manifests[source]), encoding='utf-8')
    (stage/'report.json').write_text(canonical(report),encoding='utf-8')
    # Each process gets a unique immutable directory; never replace an in-use snapshot.
    final = root / (sid + '-' + uuid4().hex)
    os.replace(stage, final)
    return final, report
