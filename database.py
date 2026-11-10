from pathlib import Path
import socket
import ssl
from urllib.parse import unquote

from flask import current_app, g


class Database:
    """Small adapter so existing parameterized queries work on SQLite and PostgreSQL."""
    def __init__(self, connection, postgres=False):
        self.connection = connection
        self.postgres = postgres

    def execute(self, sql, params=()):
        return self.connection.execute(sql.replace('?', '%s') if self.postgres else sql, params)

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def __enter__(self):
        if not self.postgres:
            self.connection.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.postgres:
            if exc_type is None:
                self.connection.commit()
            else:
                self.connection.rollback()
            return False
        return self.connection.__exit__(exc_type, exc, tb)


class DictCursor:
    """Expose pg8000's DB-API tuples as the mapping rows the Flask views use."""
    def __init__(self, cursor):
        self.cursor = cursor

    @property
    def rowcount(self):
        return self.cursor.rowcount

    def _mapping(self, row):
        if row is None or not self.cursor.description:
            return row
        return {column[0]: value for column, value in zip(self.cursor.description, row)}

    def fetchone(self):
        return self._mapping(self.cursor.fetchone())

    def fetchall(self):
        return [self._mapping(row) for row in self.cursor.fetchall()]


class Pg8000Connection:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, params=()):
        cursor = self.connection.cursor()
        cursor.execute(sql, params)
        return DictCursor(cursor)

    def __getattr__(self, name):
        return getattr(self.connection, name)


def connect_postgres(database_url):
    """Open an encrypted pg8000 connection from a postgres:// URI."""
    if any(value in database_url for value in ('[YOUR-PASSWORD]', '[PASSWORD]', 'YOUR_PASSWORD')):
        raise RuntimeError('DATABASE_URL still contains a password placeholder; replace it with the Supabase database password.')
    scheme, marker, remainder = database_url.partition('://')
    if not marker or scheme not in {'postgres', 'postgresql'}:
        raise RuntimeError('DATABASE_URL must be a PostgreSQL connection URI.')
    authority, slash, path = remainder.partition('/')
    if not slash or '@' not in authority or ':' not in authority.rsplit('@', 1)[0]:
        raise RuntimeError('DATABASE_URL is missing its username, password, host, or database name.')
    credentials, endpoint = authority.rsplit('@', 1)
    username, password = credentials.split(':', 1)
    if endpoint.startswith('['):
        host_end = endpoint.find(']')
        if host_end < 0 or endpoint[host_end + 1:host_end + 2] != ':':
            raise RuntimeError('DATABASE_URL has an invalid IPv6 endpoint.')
        host, port = endpoint[1:host_end], endpoint[host_end + 2:]
    else:
        if ':' not in endpoint:
            raise RuntimeError('DATABASE_URL is missing its PostgreSQL port.')
        host, port = endpoint.rsplit(':', 1)
    database, _, _query = path.partition('?')
    if not host or not database:
        raise RuntimeError('DATABASE_URL is missing its host or database name.')

    import pg8000.dbapi
    port_number = int(port)
    addresses = socket.getaddrinfo(host, port_number, type=socket.SOCK_STREAM)
    if not addresses:
        raise RuntimeError('DATABASE_URL host did not resolve to an address.')
    address = addresses[0][4][0]
    tls = ssl.create_default_context()
    # Matches libpq sslmode=require: encrypted transport without CA/hostname verification.
    tls.check_hostname = False
    tls.verify_mode = ssl.CERT_NONE
    try:
        connection = pg8000.dbapi.connect(
            user=unquote(username), password=unquote(password), host=address,
            port=port_number, database=unquote(database), ssl_context=tls, timeout=10,
        )
    except pg8000.exceptions.DatabaseError as exc:
        error = exc.args[0] if exc.args else None
        if isinstance(error, dict) and error.get('C') == '28P01':
            raise RuntimeError(
                'Supabase rejected DATABASE_URL credentials (SQLSTATE 28P01). '
                'Check the PostgreSQL database password and pooler username; '
                'the database password is separate from SUPABASE_SECRET_KEY.'
            ) from exc
        raise
    return Database(Pg8000Connection(connection), postgres=True)


def get_db():
    if 'db' not in g:
        database_url = current_app.config.get('DATABASE_URL', '')
        if database_url:
            g.db = connect_postgres(database_url)
        else:
            import sqlite3
            path = Path(current_app.config['DATABASE'])
            path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            g.db = Database(connection)
    return g.db


def close_db(_=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    if db.postgres:
        db.execute('''CREATE TABLE IF NOT EXISTS phrases (
            id BIGSERIAL PRIMARY KEY, phrase TEXT NOT NULL, transliteration TEXT,
            language TEXT NOT NULL, region TEXT NOT NULL, literal_meaning TEXT, contextual_meaning TEXT NOT NULL,
            example_usage TEXT, cultural_note TEXT, tags TEXT, audio_path TEXT, contributor TEXT,
            raw_transcript TEXT, corrected_transcript TEXT NOT NULL, model_used TEXT,
            verification_status TEXT NOT NULL DEFAULT 'Pending review', created_at TEXT NOT NULL DEFAULT (to_char(CURRENT_TIMESTAMP, 'YYYY-MM-DD HH24:MI:SS')),
            detected_language_code TEXT, transcription_request_id TEXT, transcription_timestamp TEXT, transcription_mode TEXT,
            audio_public_consent BOOLEAN NOT NULL DEFAULT FALSE
        )''')
        columns = {r['column_name'] for r in db.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = 'phrases'").fetchall()}
        migrations = {
            'detected_language_code': 'TEXT', 'transcription_request_id': 'TEXT',
            'transcription_timestamp': 'TEXT', 'transcription_mode': 'TEXT',
            'audio_public_consent': 'BOOLEAN NOT NULL DEFAULT FALSE',
        }
        for column, coltype in migrations.items():
            if column not in columns:
                db.execute(f'ALTER TABLE phrases ADD COLUMN {column} {coltype}')
        db.commit()
    else:
        db.execute('''CREATE TABLE IF NOT EXISTS phrases (
            id INTEGER PRIMARY KEY AUTOINCREMENT, phrase TEXT NOT NULL, transliteration TEXT,
            language TEXT NOT NULL, region TEXT NOT NULL, literal_meaning TEXT, contextual_meaning TEXT NOT NULL,
            example_usage TEXT, cultural_note TEXT, tags TEXT, audio_path TEXT, contributor TEXT,
            raw_transcript TEXT, corrected_transcript TEXT NOT NULL, model_used TEXT,
            verification_status TEXT NOT NULL DEFAULT 'Pending review', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            detected_language_code TEXT, transcription_request_id TEXT, transcription_timestamp TEXT, transcription_mode TEXT,
            audio_public_consent INTEGER NOT NULL DEFAULT 0
        )''')
        existing = {row['name'] for row in db.execute('PRAGMA table_info(phrases)').fetchall()}
        migrations = {
            'detected_language_code': 'TEXT', 'transcription_request_id': 'TEXT',
            'transcription_timestamp': 'TEXT', 'transcription_mode': 'TEXT',
            'audio_public_consent': 'INTEGER NOT NULL DEFAULT 0',
        }
        for column, column_type in migrations.items():
            if column not in existing:
                db.execute(f'ALTER TABLE phrases ADD COLUMN {column} {column_type}')
        db.commit()
    close_db()


def list_phrases(search='', language='', region='', limit=None):
    db = get_db()
    sql = 'SELECT * FROM phrases WHERE 1=1'
    args = []
    if search:
        needle = f'%{search}%'
        operator = 'ILIKE' if db.postgres else 'LIKE'
        sql += f' AND (phrase {operator} ? OR transliteration {operator} ? OR literal_meaning {operator} ? OR contextual_meaning {operator} ? OR tags {operator} ? OR corrected_transcript {operator} ?)'
        args += [needle] * 6
    if language:
        sql += ' AND language=?'
        args.append(language)
    if region:
        sql += ' AND region=?'
        args.append(region)
    sql += ' ORDER BY created_at DESC, id DESC'
    if limit is not None:
        sql += ' LIMIT ?'
        args.append(limit)
    return db.execute(sql, args).fetchall()


def get_phrase(phrase_id):
    return get_db().execute('SELECT * FROM phrases WHERE id=?', (phrase_id,)).fetchone()
