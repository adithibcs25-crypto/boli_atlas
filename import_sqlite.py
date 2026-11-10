"""Idempotently import BOLI ATLAS phrases and optional local audio to Supabase."""
import argparse
import sqlite3
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
from config import Config
from database import connect_postgres


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sqlite', default=str(Path('instance') / 'boli.db'), help='Source SQLite database')
    parser.add_argument('--audio-dir', default=Config.UPLOAD_FOLDER, help='Folder containing local recordings')
    parser.add_argument('--skip-audio', action='store_true', help='Import phrase rows without uploading audio files')
    args = parser.parse_args()
    if not Config.DATABASE_URL:
        parser.error('Set DATABASE_URL to the Supabase PostgreSQL connection string first.')
    if not Config.SUPABASE_URL or not Config.SUPABASE_SECRET_KEY:
        parser.error('Set SUPABASE_URL and SUPABASE_SECRET_KEY first.')
    if not Config.SUPABASE_SECRET_KEY.startswith('sb_secret_'):
        parser.error('SUPABASE_SECRET_KEY must use Supabase’s sb_secret_ format.')
    if any(value in Config.DATABASE_URL for value in ('[YOUR-PASSWORD]', '[PASSWORD]', 'YOUR_PASSWORD')):
        parser.error('DATABASE_URL still contains a password placeholder. Replace it with the Supabase database password.')

    source_path = Path(args.sqlite)
    if not source_path.is_file():
        parser.error(f'SQLite database not found: {source_path}')
    source = sqlite3.connect(source_path)
    source.row_factory = sqlite3.Row
    rows = source.execute('SELECT * FROM phrases ORDER BY id').fetchall()
    source.close()

    from supabase import create_client
    storage = create_client(Config.SUPABASE_URL, Config.SUPABASE_SECRET_KEY).storage.from_(Config.SUPABASE_AUDIO_BUCKET)
    audio_dir = Path(args.audio_dir)
    inserted = skipped = missing_audio = uploaded = 0
    target = connect_postgres(Config.DATABASE_URL)
    try:
        with target:
            columns = {r['column_name'] for r in target.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='phrases'").fetchall()}
            if not columns:
                parser.error('PostgreSQL schema is missing. Start the Flask app once to initialize it before importing.')
            for row in rows:
                values = dict(row)
                old_audio = values.get('audio_path')
                if old_audio:
                    if args.skip_audio:
                        values['audio_path'] = None
                        values['audio_public_consent'] = False
                    else:
                        local = audio_dir / Path(old_audio).name
                        if local.is_file():
                            object_path = f"legacy/{values['id']}/{local.name}"
                            ext = local.suffix.lower()
                            mime = {'.wav':'audio/wav','.mp3':'audio/mpeg','.m4a':'audio/mp4','.webm':'audio/webm','.ogg':'audio/ogg','.flac':'audio/flac','.aac':'audio/aac'}.get(ext, 'application/octet-stream')
                            storage.upload(path=object_path, file=local.read_bytes(), file_options={'content-type': mime, 'upsert': 'true'})
                            values['audio_path'] = object_path
                            uploaded += 1
                        else:
                            values['audio_path'] = None
                            values['audio_public_consent'] = False
                            missing_audio += 1
                # Existing SQLite schemas without the explicit flag default to private playback.
                values.setdefault('audio_public_consent', False)
                names = [name for name in values if name in columns]
                placeholders = ','.join(['%s'] * len(names))
                sql = f"INSERT INTO phrases ({','.join(names)}) VALUES ({placeholders}) ON CONFLICT (id) DO NOTHING"
                result = target.execute(sql, [values[name] for name in names])
                if result.rowcount:
                    inserted += 1
                else:
                    skipped += 1
            target.execute("SELECT setval(pg_get_serial_sequence('phrases','id'), COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM phrases")
    finally:
        target.close()
    print(f'Imported {inserted} phrase(s); skipped existing IDs: {skipped}; uploaded audio: {uploaded}; missing local audio: {missing_audio}.')
    if missing_audio:
        print('Phrase rows remain imported, but their missing local audio was not copied. Restore those files and rerun the command.')


if __name__ == '__main__':
    main()
