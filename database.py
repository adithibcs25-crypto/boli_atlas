import sqlite3
from pathlib import Path
from flask import current_app, g

SCHEMA = '''CREATE TABLE IF NOT EXISTS phrases (
 id INTEGER PRIMARY KEY AUTOINCREMENT, phrase TEXT NOT NULL, transliteration TEXT,
 language TEXT NOT NULL, region TEXT NOT NULL, literal_meaning TEXT, contextual_meaning TEXT NOT NULL,
 example_usage TEXT, cultural_note TEXT, tags TEXT, audio_path TEXT, contributor TEXT,
 raw_transcript TEXT, corrected_transcript TEXT NOT NULL, model_used TEXT,
 verification_status TEXT NOT NULL DEFAULT 'Pending review', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 detected_language_code TEXT, transcription_request_id TEXT, transcription_timestamp TEXT, transcription_mode TEXT
);'''

def get_db():
    if 'db' not in g:
        path = Path(current_app.config['DATABASE']); path.parent.mkdir(parents=True, exist_ok=True)
        g.db = sqlite3.connect(path); g.db.row_factory = sqlite3.Row
    return g.db

def close_db(_=None):
    db = g.pop('db', None)
    if db is not None: db.close()

def init_db():
    db = get_db()
    db.executescript(SCHEMA)
    existing = {row['name'] for row in db.execute('PRAGMA table_info(phrases)')}
    migrations = {
        'detected_language_code': 'TEXT',
        'transcription_request_id': 'TEXT',
        'transcription_timestamp': 'TEXT',
        'transcription_mode': 'TEXT',
    }
    for column, column_type in migrations.items():
        if column not in existing:
            db.execute(f'ALTER TABLE phrases ADD COLUMN {column} {column_type}')
    db.commit()
    close_db()

def list_phrases(search='', language='', region='', limit=None):
    db=get_db(); sql='SELECT * FROM phrases WHERE 1=1'; args=[]
    if search:
        needle=f'%{search}%'; sql += ' AND (phrase LIKE ? OR transliteration LIKE ? OR literal_meaning LIKE ? OR contextual_meaning LIKE ? OR tags LIKE ? OR corrected_transcript LIKE ?)'; args += [needle]*6
    if language: sql += ' AND language=?'; args.append(language)
    if region: sql += ' AND region=?'; args.append(region)
    sql += ' ORDER BY created_at DESC, id DESC'
    if limit is not None: sql += ' LIMIT ?'; args.append(limit)
    return db.execute(sql,args).fetchall()

def get_phrase(phrase_id): return get_db().execute('SELECT * FROM phrases WHERE id=?',(phrase_id,)).fetchone()

