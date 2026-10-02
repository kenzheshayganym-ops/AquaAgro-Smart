"""Frozen snapshot, persistent session memory and request accounting."""
from contextlib import contextmanager
from pathlib import Path
import hashlib
import json
import sqlite3

KINDS = ['fields', 'readings', 'water_daily', 'irrigation_events']

def full_hash(value):
    text = json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(text.encode()).hexdigest()

class Store:
    def __init__(self, db_path: Path, data_dir: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.snapshot = json.loads((data_dir / 'snapshot.json').read_text(encoding='utf-8'))
        self.manifest = json.loads((data_dir / 'manifest.json').read_text(encoding='utf-8'))
        self.data_hash = full_hash(self.snapshot)
        if self.data_hash != self.manifest['data_sha256']:
            raise RuntimeError('Snapshot fingerprint mismatch')
        self.eval_hash = self.manifest['eval_input_hash']
        with self.connect() as conn:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.executescript('''
            CREATE TABLE IF NOT EXISTS source_records (
              kind TEXT NOT NULL, position INTEGER NOT NULL, payload TEXT NOT NULL,
              PRIMARY KEY(kind,position));
            CREATE TABLE IF NOT EXISTS source_meta (name TEXT PRIMARY KEY,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS session_messages (
              id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
              role TEXT NOT NULL,content TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS messages_session ON session_messages(session_id,id);
            CREATE TABLE IF NOT EXISTS request_usage (
              request_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            ''')
            stored = conn.execute("SELECT payload FROM source_meta WHERE name='data_hash'").fetchone()
            if stored is None:
                if conn.execute('SELECT COUNT(*) FROM source_records').fetchone()[0]:
                    raise RuntimeError('Database has records without a fingerprint')
                for kind in KINDS:
                    conn.executemany('INSERT INTO source_records VALUES(?,?,?)',
                        [(kind, i, json.dumps(r, ensure_ascii=False))
                         for i, r in enumerate(self.snapshot[kind])])
                for name, value in {'info': self.snapshot['info'], 'policy': self.snapshot['policy'],
                                    'data_hash': self.data_hash}.items():
                    conn.execute('INSERT INTO source_meta VALUES(?,?)',
                                 (name, json.dumps(value, ensure_ascii=False)))
            elif json.loads(stored[0]) != self.data_hash:
                raise RuntimeError('Database contains a different snapshot')
        self.check_database()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def db_records(self, kind):
        if kind not in KINDS:
            raise ValueError('Unknown data kind')
        with self.connect() as conn:
            rows = conn.execute('SELECT payload FROM source_records WHERE kind=? ORDER BY position',
                                (kind,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def db_meta(self, name):
        with self.connect() as conn:
            row = conn.execute('SELECT payload FROM source_meta WHERE name=?', (name,)).fetchone()
        if row is None:
            raise RuntimeError('Missing snapshot metadata')
        return json.loads(row[0])

    def check_database(self):
        restored = {'info': self.db_meta('info'), 'policy': self.db_meta('policy'),
                    **{k: self.db_records(k) for k in KINDS}}
        if full_hash(restored) != self.data_hash:
            raise RuntimeError('SQLite snapshot has changed')

    @staticmethod
    def memory_key(user_id, session_id):
        # The same session name for different users never shares messages.
        return json.dumps([user_id, session_id], ensure_ascii=False, separators=(',', ':'))

    def session_history(self, user_id, session_id):
        with self.connect() as conn:
            rows = conn.execute('SELECT role,content FROM session_messages WHERE session_id=? '
                                'ORDER BY id DESC LIMIT 12',
                                (self.memory_key(user_id, session_id),)).fetchall()
        return [{'role': r, 'content': c} for r, c in reversed(rows)]

    def save_exchange(self, user_id, session_id, question, answer, record):
        with self.connect() as conn:
            key = self.memory_key(user_id, session_id)
            conn.executemany('INSERT INTO session_messages(session_id,role,content) VALUES(?,?,?)',
                             [(key, 'user', question), (key, 'assistant', answer)])
            conn.execute('INSERT INTO request_usage VALUES(?,?)',
                         (record['request_id'], json.dumps(record, ensure_ascii=False)))

    def record_failure(self, record):
        with self.connect() as conn:
            conn.execute('INSERT OR REPLACE INTO request_usage VALUES(?,?)',
                         (record['request_id'], json.dumps(record, ensure_ascii=False)))

    def usage(self, request_id):
        with self.connect() as conn:
            row = conn.execute('SELECT payload FROM request_usage WHERE request_id=?', (request_id,)).fetchone()
        return json.loads(row[0]) if row else None
