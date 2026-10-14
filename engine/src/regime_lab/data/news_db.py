"""Read approved immutable news snapshots through the F1 HTTP API only."""
import base64
from collections import Counter
from datetime import datetime, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request
from uuid import uuid4
import pyarrow as pa
import pyarrow.parquet as pq


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


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


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('News API redirect refused')


class Client:
    """GET-only transport; injectable opener keeps CI independent of the server."""
    def __init__(self, base, token, policy, opener=None):
        try:
            url = urllib.parse.urlsplit(base)
            valid = (url.scheme in ('http', 'https') and url.hostname
                     and not url.username and not url.password and not url.query
                     and not url.fragment and url.port != 0)
        except ValueError:
            valid = False
        if not valid:
            raise ValueError('Invalid REGIME_DATA_API_URL')
        self._base, self._token = base.rstrip('/'), token
        self._policy = policy
        self._opener = opener or urllib.request.build_opener(_NoRedirect())

    def get(self, path, **params):
        query = urllib.parse.urlencode(params)
        headers = {'Accept': 'application/json'}
        if self._token:
            headers['X-API-Key'] = self._token
        try:
            request = urllib.request.Request(self._base + path + ('?' + query if query else ''), headers=headers)
            with self._opener.open(request, timeout=self._policy['http_timeout_seconds']) as response:
                body = response.read(self._policy['max_response_bytes'] + 1)
        except urllib.error.HTTPError as error:
            code = error.code
            error.close()
            raise ValueError('News API request failed (HTTP %d)' % code) from None
        except (ValueError, OSError, http.client.HTTPException, urllib.error.URLError):
            raise ValueError('News API unavailable') from None
        if len(body) > self._policy['max_response_bytes']:
            raise ValueError('News API response exceeds limit')
        try:
            result = json.loads(body.decode('utf-8'))
        except (UnicodeDecodeError, ValueError):
            raise ValueError('Invalid news API JSON') from None
        if not isinstance(result, dict):
            raise ValueError('Invalid news API response')
        return result


def read_latest(client, root, sources, current_id=None, *, page_size, max_versions):
    """Validate manifest/membership/document counts before publishing any files.

    HTTP requests are not a transaction. A manifest visible before its members is
    rejected, and the caller retains the old index until the next polling cycle.
    Response envelopes are documented in P3_DB_검색_자동갱신.md pending F1 finalization.
    """
    if sorted(sources) != ['n8n_disclosure', 'n8n_news']:
        raise ValueError('Invalid source scope')
    latest = client.get('/v1/ext/snapshots/latest', sources=','.join(sources))
    sid = latest.get('snapshot_id')
    if not isinstance(sid, str) or not re.fullmatch('[a-f0-9]{64}', sid):
        raise ValueError('No complete approved snapshot')
    if sid == current_id:
        return None
    endpoint = '/v1/ext/snapshots/' + sid
    metadata = client.get(endpoint)
    if metadata.get('snapshot_id') != sid or metadata.get('coverage_rows') != 0:
        raise ValueError('Snapshot or coverage contract mismatch')
    manifests, schemas, counts = {}, {}, {}
    for row in metadata['manifests']:
        source = row['source']
        count = row['document_versions']
        if source not in sources or source in manifests or row['coverage_state'] != 'unknown' or row['coverage_rows'] != 0:
            raise ValueError('Unsupported source or coverage contract')
        if type(count) is not int or count <= 0:
            raise ValueError('Invalid manifest count')
        manifest = json.loads(row['manifest_json'])
        if manifest.get('source') != source or manifest.get('schema_version') != 1 or manifest.get('license_scope') != 'metadata_only' or manifest.get('coverage') != 'unknown':
            raise ValueError('Invalid manifest')
        manifests[source] = manifest
        schemas[source] = pa.ipc.read_schema(pa.BufferReader(base64.b64decode(json.loads(row['arrow_schema_json'])['ipc_base64'], validate=True)))
        counts[source] = count
    members = metadata['member_counts']
    if (set(manifests) != set(sources) or members != counts
            or any(type(v) is not int for v in members.values())
            or sum(counts.values()) > max_versions):
        raise ValueError('Incomplete snapshot manifests/membership')
    docs = {}
    for source in sources:
        cursor = None
        fetched = 0
        while True:
            params = {'source': source, 'limit': page_size}
            if cursor is not None:
                params.update(after_doc_id=cursor[0], after_version=cursor[1])
            page = client.get(endpoint + '/documents', **params)
            rows = page['rows']
            if page.get('snapshot_id') != sid or page.get('source') != source or not isinstance(rows, list) or len(rows) > page_size:
                raise ValueError('Invalid snapshot page')
            for row in rows:
                doc = json.loads(row['document_json'])
                expected = projection(doc)
                columns = ['doc_id', 'version', 'source', 'status', 'license_scope',
                           'first_seen_at_utc', 'available_at_utc', 'record_sha256']
                if [row[k] for k in columns] != expected[:-1]:
                    raise ValueError('Document projection/hash mismatch')
                key = (doc['doc_id'], doc['version'])
                if doc['source'] != source or key in docs or (cursor is not None and key <= cursor):
                    raise ValueError('Invalid snapshot membership/order')
                if doc.get('status', 'active') != 'active':
                    raise ValueError('Deletion policy not supported by this search runtime')
                docs[key] = doc
                cursor = key
                fetched += 1
            if fetched > counts[source]:
                raise ValueError('Snapshot document count mismatch')
            next_cursor = page['next_cursor']
            if next_cursor is None:
                break
            if (not rows or not isinstance(next_cursor, dict)
                    or (next_cursor.get('doc_id'), next_cursor.get('version')) != cursor
                    or fetched >= counts[source]):
                raise ValueError('Invalid next page cursor')
        if fetched != counts[source]:
            raise ValueError('Incomplete snapshot document count')
    if Counter(d['source'] for d in docs.values()) != counts:
        raise ValueError('Snapshot count/membership mismatch')
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
        with pq.ParquetFile(path) as parquet:
            check = parquet.read()
        if not check.schema.equals(schemas[source]) or canonical(check.to_pylist()) != canonical(values):
            raise ValueError('Snapshot roundtrip mismatch')
        (folder/'_manifest.json').write_text(canonical(manifests[source]), encoding='utf-8')
    (stage/'report.json').write_text(canonical(report),encoding='utf-8')
    # Each process gets a unique immutable directory; never replace an in-use snapshot.
    final = root / (sid + '-' + uuid4().hex)
    os.replace(stage, final)
    return final, report
