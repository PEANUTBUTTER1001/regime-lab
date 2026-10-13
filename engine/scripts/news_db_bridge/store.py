"""SQLite authoritative history and recoverable, atomic Parquet exports."""
import json
import os
import sqlite3
from pathlib import Path
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq

from normalize import KINDS, digest, normalize, stable

FIELDS = ['kind', 'title', 'summary', 'report_date', 'published_at',
          'reported_first_seen_at', 'source_url', 'stocks', 'sector', 'impact',
          'impact_reason', 'uncertainty', 'stock_impacts_json', 'processing_status',
          'payload_json', 'entity_key', 'content_hash']
EXPORT_SCHEMA = pa.schema([(k, pa.string()) for k in FIELDS] +
                         [('version', pa.int64()), ('imported_at', pa.string()),
                          ('available_at', pa.string())])


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / 'sheets.sqlite', timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        columns = ','.join('"' + k + '" TEXT' for k in FIELDS)
        self.db.executescript(f'''
          CREATE TABLE IF NOT EXISTS versions ({columns}, version INTEGER NOT NULL,
            imported_at TEXT NOT NULL, available_at TEXT NOT NULL,
            PRIMARY KEY(entity_key, version));
          CREATE TABLE IF NOT EXISTS current (entity_key TEXT PRIMARY KEY, version INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS quarantine (id TEXT PRIMARY KEY, kind TEXT NOT NULL,
            row_number TEXT, code TEXT NOT NULL, payload_json TEXT NOT NULL, observed_at TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS imports (id TEXT PRIMARY KEY, kind TEXT NOT NULL,
            observed_at TEXT NOT NULL, result_json TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS export_state (kind TEXT PRIMARY KEY, dirty INTEGER NOT NULL);
          CREATE VIEW IF NOT EXISTS current_records AS SELECT v.* FROM versions v
            JOIN current c ON v.entity_key=c.entity_key AND v.version=c.version;
        ''')
        if 'resolved_at' not in [r[1] for r in self.db.execute('PRAGMA table_info(quarantine)')]:
            self.db.execute('ALTER TABLE quarantine ADD COLUMN resolved_at TEXT')
            self.db.commit()

    def close(self):
        self.db.close()

    def sync(self, kind, rows, now):
        if kind not in KINDS:
            raise ValueError('invalid_kind')
        result = dict(kind=kind, received=len(rows), inserted=0, updated=0, unchanged=0,
                      rejected=0, empty=0, error_codes={})
        normalized, bad = [], []
        # Conflicting duplicate keys in the same batch have no trustworthy ordering.
        hashes = {}
        for row in rows:
            try:
                item = normalize(kind, row)
                if item is None:
                    result['empty'] += 1
                    continue
                normalized.append((row, item))
                hashes.setdefault(item['entity_key'], set()).add(item['content_hash'])
            except (ValueError, TypeError, OverflowError) as error:
                code = str(error) if str(error) in {'invalid_cell_type', 'invalid_date',
                    'invalid_timestamp', 'invalid_source_url', 'invalid_stock_impacts_json',
                    'missing_stock_identity', 'missing_title', 'invalid_row'} else 'invalid_row'
                bad.append((row, code))
        with self.db:
            for row, item in normalized:
                if len(hashes[item['entity_key']]) != 1:
                    bad.append((row, 'conflicting_duplicate'))
                    continue
                old = self.db.execute('SELECT * FROM current_records WHERE entity_key=?',
                                      (item['entity_key'],)).fetchone()
                self.db.execute('UPDATE quarantine SET resolved_at=? WHERE id=?', (now, digest([kind, row])))
                if old and old['content_hash'] == item['content_hash']:
                    result['unchanged'] += 1
                    continue
                version = old['version'] + 1 if old else 1
                # Sheet publisher/date fields cannot establish historical availability.
                available = max(now, old['available_at']) if old else now
                values = [item[k] for k in FIELDS] + [version, now, available]
                self.db.execute('INSERT INTO versions VALUES (' + ','.join('?' for _ in values) + ')', values)
                self.db.execute('INSERT INTO current VALUES (?,?) ON CONFLICT(entity_key) DO UPDATE SET version=excluded.version',
                                (item['entity_key'], version))
                result['updated' if old else 'inserted'] += 1
            for row, code in bad:
                result['rejected'] += 1
                result['error_codes'][code] = result['error_codes'].get(code, 0) + 1
                self.db.execute('INSERT OR REPLACE INTO quarantine (id,kind,row_number,code,payload_json,observed_at) VALUES (?,?,?,?,?,?)',
                                (digest([kind, row]), kind, str(row.get('row_number', '')) if isinstance(row, dict) else '',
                                 code, stable(row), now))
            result['status'] = 'partial' if bad else 'ok'
            self.db.execute('INSERT INTO imports VALUES (?,?,?,?)', (uuid4().hex, kind, now, stable(result)))
            if result['inserted'] + result['updated']:
                self.db.execute('INSERT INTO export_state VALUES (?,1) ON CONFLICT(kind) DO UPDATE SET dirty=1', (kind,))
        # A failed export leaves dirty=1; retry safely repairs it without duplicate versions.
        self.recover_exports()
        return result

    def recover_exports(self):
        for entry in self.db.execute('SELECT kind FROM export_state WHERE dirty=1').fetchall():
            kind = entry['kind']
            folder = self.root / 'parquet' / ('kind=' + kind)
            folder.mkdir(parents=True, exist_ok=True)
            for view, filename in [('versions', 'history.parquet'), ('current_records', 'current.parquet')]:
                rows = [dict(r) for r in self.db.execute(f'SELECT * FROM {view} WHERE kind=? ORDER BY entity_key,version', (kind,))]
                temp = folder / (filename + '.tmp')
                pq.write_table(pa.Table.from_pylist(rows, schema=EXPORT_SCHEMA), temp)
                os.replace(temp, folder / filename)
            with self.db:
                self.db.execute('UPDATE export_state SET dirty=0 WHERE kind=?', (kind,))

    def status(self):
        return dict(current={r['kind']: r['n'] for r in self.db.execute('SELECT kind,count(*) n FROM current_records GROUP BY kind')},
                    versions=self.db.execute('SELECT count(*) FROM versions').fetchone()[0],
                    quarantine=self.db.execute('SELECT count(*) FROM quarantine WHERE resolved_at IS NULL').fetchone()[0],
                    resolved_quarantine=self.db.execute('SELECT count(*) FROM quarantine WHERE resolved_at IS NOT NULL').fetchone()[0],
                    pending_exports=self.db.execute('SELECT count(*) FROM export_state WHERE dirty=1').fetchone()[0],
                    last_imports=[dict(r) for r in self.db.execute('SELECT kind,observed_at,result_json FROM imports ORDER BY rowid DESC LIMIT 3')])
