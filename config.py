import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _supabase_project_url():
    url = os.environ.get('SUPABASE_URL', '').strip().rstrip('/')
    for api_path in ('/rest/v1', '/storage/v1'):
        if url.endswith(api_path):
            return url[:-len(api_path)]
    return url


class Config:
    SECRET_KEY = os.environ.get('FLASK_SECRET_KEY') or os.environ.get('SECRET_KEY') or 'local-development-change-me'
    DATABASE_URL = os.environ.get('DATABASE_URL', '').strip()
    DATABASE = os.environ.get('BOLI_DATABASE', str(BASE_DIR / 'instance' / 'boli.db'))
    UPLOAD_FOLDER = os.environ.get('BOLI_UPLOAD_FOLDER', str(BASE_DIR / 'uploads' / 'recordings'))
    SUPABASE_URL = _supabase_project_url()
    SUPABASE_SECRET_KEY = os.environ.get('SUPABASE_SECRET_KEY', '').strip()
    SUPABASE_AUDIO_BUCKET = 'boli-audio'
    MAX_CONTENT_LENGTH = 15 * 1024 * 1024
    GNANI_API_KEY = os.environ.get('GNANI_API_KEY', '').strip()
    DEBUG = False
    TESTING = False
