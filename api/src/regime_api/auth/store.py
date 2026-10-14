"""메모리 세션과 지속적인 소유자 인덱스. 토큰은 SQLite에 쓰지 않는다."""
import hashlib
import sqlite3
import threading
from pathlib import Path
from regime_api.auth.contracts import Session, OAuthFlow

class AuthStore:
    def __init__(self, root: Path, max_sessions: int):
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "ownership.sqlite3"
        self.max_sessions = max_sessions
        self._sessions: dict[str, Session] = {}
        self._flows: dict[str, OAuthFlow] = {}
        self._locks = {}
        self._lock = threading.RLock()
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS owners (kind TEXT NOT NULL, resource_id TEXT NOT NULL, user_id TEXT NOT NULL, PRIMARY KEY(kind, resource_id))")

    def _db(self):
        return sqlite3.connect(self.path)

    @staticmethod
    def _key(value):
        return hashlib.sha256(value.encode()).hexdigest()

    def put(self, sid, session):
        with self._lock:
            if len(self._sessions) >= self.max_sessions:
                raise RuntimeError("session capacity reached")
            key = self._key(sid)
            self._sessions[key] = session
            self._locks[key] = threading.RLock()

    def put_flow(self, fid, flow, before, old_id=None):
        with self._lock:
            self._flows = {key:value for key,value in self._flows.items() if value.created > before}
            if old_id:
                self._flows.pop(self._key(old_id), None)
            if len(self._flows) >= self.max_sessions:
                raise RuntimeError("OAuth capacity reached")
            self._flows[self._key(fid)] = flow

    def take_flow(self, fid):
        with self._lock:
            return self._flows.pop(self._key(fid), None)

    def locked(self, sid):
        with self._lock:
            return self._locks.get(self._key(sid), self._lock)

    def get(self, sid):
        with self._lock:
            return self._sessions.get(self._key(sid))

    def drop(self, sid):
        with self._lock:
            key = self._key(sid)
            self._sessions.pop(key, None)
            self._locks.pop(key, None)

    def drop_user(self, uid):
        with self._lock:
            for key, session in list(self._sessions.items()):
                if session.tokens.user.id == uid:
                    self._sessions.pop(key, None)
                    self._locks.pop(key, None)

    def prune(self, before):
        with self._lock:
            for key, session in list(self._sessions.items()):
                if session.seen < before:
                    self._sessions.pop(key, None)
                    self._locks.pop(key, None)

    def claim(self, kind, rid, uid):
        with self._db() as db:
            db.execute("INSERT OR IGNORE INTO owners VALUES (?, ?, ?)", (kind, rid, uid))
            owner = db.execute("SELECT user_id FROM owners WHERE kind=? AND resource_id=?", (kind, rid)).fetchone()
            if owner is None or owner[0] != uid:
                raise RuntimeError("resource ownership conflict")

    def owner(self, kind, rid):
        with self._db() as db:
            row = db.execute("SELECT user_id FROM owners WHERE kind=? AND resource_id=?", (kind, rid)).fetchone()
            return row[0] if row else None
