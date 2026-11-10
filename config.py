import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'local-development-change-me')
    DATABASE = os.environ.get('BOLI_DATABASE', str(BASE_DIR / 'instance' / 'boli.db'))
    UPLOAD_FOLDER = os.environ.get('BOLI_UPLOAD_FOLDER', str(BASE_DIR / 'uploads' / 'recordings'))
    MAX_CONTENT_LENGTH = 15 * 1024 * 1024
    GNANI_API_KEY = os.environ.get('GNANI_API_KEY', '').strip()
