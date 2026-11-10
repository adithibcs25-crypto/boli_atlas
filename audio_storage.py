import os
import uuid
from pathlib import Path

from flask import current_app
from werkzeug.utils import secure_filename


def _supabase():
    url = current_app.config.get('SUPABASE_URL')
    key = current_app.config.get('SUPABASE_SECRET_KEY')
    if not url and not key:
        return None
    if not url or not key:
        raise RuntimeError('Both SUPABASE_URL and SUPABASE_SECRET_KEY must be configured together.')
    if not key.startswith('sb_secret_'):
        raise RuntimeError('SUPABASE_SECRET_KEY must use Supabase’s sb_secret_ format.')
    from supabase import create_client
    return create_client(url, key)


def store_audio(upload):
    """Persist audio and return a storage key (never a public URL)."""
    ext = Path(secure_filename(upload.filename or '')).suffix.lower()
    key = f'contributions/{uuid.uuid4().hex}{ext}'
    client = _supabase()
    if client:
        content = upload.read()
        fallback_mime = {'.wav': 'audio/wav', '.mp3': 'audio/mpeg', '.m4a': 'audio/mp4', '.webm': 'audio/webm', '.ogg': 'audio/ogg', '.flac': 'audio/flac', '.aac': 'audio/aac'}.get(ext, 'application/octet-stream')
        mime = upload.mimetype if upload.mimetype and upload.mimetype != 'application/octet-stream' else fallback_mime
        client.storage.from_(current_app.config['SUPABASE_AUDIO_BUCKET']).upload(
            path=key, file=content,
            file_options={'content-type': mime, 'upsert': 'false'},
        )
        return key
    folder = Path(current_app.config['UPLOAD_FOLDER'])
    folder.mkdir(parents=True, exist_ok=True)
    filename = f'{uuid.uuid4().hex}{ext}'
    upload.save(folder / filename)
    return filename


def read_audio(key):
    client = _supabase()
    if client:
        return client.storage.from_(current_app.config['SUPABASE_AUDIO_BUCKET']).download(key)
    filename = Path(key).name
    if filename != key:
        return None
    path = Path(current_app.config['UPLOAD_FOLDER']) / filename
    return path.read_bytes() if path.is_file() else None


def copy_local_audio_to_supabase(local_path, object_path):
    client = _supabase()
    if not client:
        raise RuntimeError('Supabase Storage credentials are not configured.')
    content = Path(local_path).read_bytes()
    ext = Path(local_path).suffix.lower()
    mime = {'.wav': 'audio/wav', '.mp3': 'audio/mpeg', '.m4a': 'audio/mp4', '.webm': 'audio/webm', '.ogg': 'audio/ogg', '.flac': 'audio/flac', '.aac': 'audio/aac'}.get(ext, 'application/octet-stream')
    client.storage.from_(current_app.config['SUPABASE_AUDIO_BUCKET']).upload(
        path=object_path, file=content,
        file_options={'content-type': mime, 'upsert': 'true'},
    )


def delete_audio(key):
    client = _supabase()
    if client and key.startswith('contributions/'):
        client.storage.from_(current_app.config['SUPABASE_AUDIO_BUCKET']).remove([key])
    elif not client and key and Path(key).name == key:
        try:
            os.remove(Path(current_app.config['UPLOAD_FOLDER']) / key)
        except FileNotFoundError:
            pass
