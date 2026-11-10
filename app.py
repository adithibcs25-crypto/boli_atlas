import os
import wave
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_wtf.csrf import CSRFProtect, CSRFError
from io import BytesIO
from werkzeug.exceptions import BadRequest
from mutagen import File as MutagenFile, MutagenError
from dotenv import load_dotenv

load_dotenv()

from config import Config
from database import close_db, get_db, init_db, list_phrases, get_phrase
from gnani_api import GnaniError, transcribe
from audio_storage import delete_audio, read_audio, store_audio

app = Flask(__name__)
app.config.from_object(Config)
IS_PRODUCTION = bool(os.environ.get('RENDER')) or os.environ.get('FLASK_ENV', '').lower() == 'production'
if IS_PRODUCTION:
    missing = [name for name, configured in (
        ('GNANI_API_KEY', app.config['GNANI_API_KEY']),
        ('SUPABASE_URL', app.config['SUPABASE_URL']),
        ('SUPABASE_SECRET_KEY', app.config['SUPABASE_SECRET_KEY']),
        ('DATABASE_URL', app.config['DATABASE_URL']),
        ('FLASK_SECRET_KEY', os.environ.get('FLASK_SECRET_KEY', '').strip()),
    ) if not configured]
    if missing:
        raise RuntimeError('Missing required production environment variables: ' + ', '.join(missing))
    if app.config['SECRET_KEY'] == 'local-development-change-me':
        raise RuntimeError('FLASK_SECRET_KEY must be set to a secure random value in production.')
    if not app.config['SUPABASE_SECRET_KEY'].startswith('sb_secret_'):
        raise RuntimeError('SUPABASE_SECRET_KEY must use Supabase’s sb_secret_ format.')
    if any(value in app.config['DATABASE_URL'] for value in ('[YOUR-PASSWORD]', '[PASSWORD]', 'YOUR_PASSWORD')):
        raise RuntimeError('DATABASE_URL still contains a password placeholder; replace it with the Supabase database password.')
    app.config.update(SESSION_COOKIE_SECURE=True, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax')
csrf = CSRFProtect(app)
app.teardown_appcontext(close_db)
Path(app.config['UPLOAD_FOLDER']).mkdir(parents=True, exist_ok=True)
with app.app_context():
    init_db()

LANGUAGES = {'Kannada': 'ಕನ್ನಡ', 'Tamil': 'தமிழ்', 'Telugu': 'తెలుగు', 'Malayalam': 'മലയാളം'}
LOCALES = {'Kannada': 'kn-IN', 'Tamil': 'ta-IN', 'Telugu': 'te-IN', 'Malayalam': 'ml-IN'}
ALLOWED = {'wav','mp3','m4a','ogg','webm','flac','aac'}
GNANI_FORMATS = {'wav','mp3','m4a','ogg','flac','aac'}
TRANSCRIPTION_FORMATS = {'verbatim','transcribe','codemix','transliterate'}

@app.context_processor
def globals_for_templates():
    return {'languages': LANGUAGES, 'api_configured': bool(app.config['GNANI_API_KEY'])}

@app.get('/')
def index():
    phrases = list_phrases(limit=8)
    return render_template('index.html', phrases=phrases)

@app.get('/healthz')
def healthz():
    try:
        get_db().execute('SELECT 1').fetchone()
        return jsonify(status='ok'), 200
    except Exception as exc:
        app.logger.error('Health check failed while querying the database (%s).', type(exc).__name__)
        return jsonify(status='unavailable'), 503

@app.get('/explore')
def explore():
    phrases = list_phrases(search=request.args.get('q','').strip(), language=request.args.get('language',''), region=request.args.get('region',''))
    regions = sorted({p['region'] for p in list_phrases() if p['region']})
    return render_template('explore.html', phrases=phrases, regions=regions)

@app.get('/phrase/<int:phrase_id>')
def phrase(phrase_id):
    item = get_phrase(phrase_id)
    if not item: abort(404)
    return render_template('phrase.html', item=item)

@app.get('/contribute')
def contribute():
    return render_template('contribute.html')

def validate_audio(file, duration_file=None):
    if not file or not file.filename: return 'Choose an audio file first.'
    ext = Path(file.filename or '').suffix.lower().lstrip('.')
    if ext not in ALLOWED: return 'Use WAV, MP3, M4A, OGG, FLAC, AAC, or WebM audio.'
    mime = (file.mimetype or '').lower().split(';', 1)[0].strip()
    accepted_mimes = {'wav': {'audio/wav','audio/x-wav'}, 'mp3': {'audio/mpeg'}, 'm4a': {'audio/mp4','audio/x-m4a'}, 'ogg': {'audio/ogg','application/ogg'}, 'webm': {'audio/webm'}, 'flac': {'audio/flac','audio/x-flac'}, 'aac': {'audio/aac','audio/x-aac'}}
    if mime and mime != 'application/octet-stream' and mime not in accepted_mimes[ext]:
        return 'The file type does not match its audio extension. Choose a valid audio file.'
    file.stream.seek(0, os.SEEK_END); size = file.stream.tell(); file.stream.seek(0)
    if not size: return 'The selected audio file is empty.'
    if size > app.config['MAX_CONTENT_LENGTH']: return 'Audio must be no larger than 15 MB.'
    measured = duration_file or file
    try:
        measured.stream.seek(0)
        try:
            with wave.open(measured.stream, 'rb') as wav:
                duration = wav.getnframes() / wav.getframerate()
        except (wave.Error, EOFError):
            measured.stream.seek(0)
            audio = MutagenFile(measured.stream)
            duration = audio.info.length if audio and audio.info else None
    except (MutagenError, OSError, ValueError, AttributeError, ZeroDivisionError):
        duration = None
    finally:
        measured.stream.seek(0)
        file.stream.seek(0)
    if duration is None:
        if ext == 'webm': return 'Could not verify this WebM recording duration. Record or upload it again in the browser.'
        return 'Could not read the audio duration. Re-export the clip as WAV, MP3, M4A, OGG, FLAC, or AAC.'
    if duration > 30.1: return 'Audio must be 30 seconds or shorter.'
    return None

@app.post('/api/transcribe')
def api_transcribe():
    audio = request.files.get('audio')
    language_code = request.form.get('language_code','')
    output_format = request.form.get('format','verbatim')
    if language_code not in set(LOCALES.values()) | {'indic-auto'}: return jsonify(error='Choose a supported language or automatic identification.'), 400
    if output_format not in TRANSCRIPTION_FORMATS: return jsonify(error='Choose a supported transcription format.'), 400
    if not audio or not audio.filename: return jsonify(error='Choose or record an audio clip first.'), 400
    if Path(audio.filename).suffix.lower().lstrip('.') not in GNANI_FORMATS:
        return jsonify(error='Gnani does not accept WebM. The browser should convert the recording to WAV before sending it.'), 415
    error = validate_audio(audio)
    if error: return jsonify(error=error), 400
    if not app.config['GNANI_API_KEY']:
        return jsonify(error='Gnani transcription is not configured. Add GNANI_API_KEY to .env, then restart Flask.'), 503
    try:
        result = transcribe(audio, language_code, output_format, app.config['GNANI_API_KEY'])
        return jsonify(
            transcript=result['transcript'], model=result['model'],
            detected_language_code=result.get('detected_language_code'),
            request_id=result['request_id'],
            transcription_timestamp=datetime.now(timezone.utc).isoformat(),
            format=output_format,
        )
    except GnaniError as exc:
        return jsonify(error=str(exc)), exc.status

@app.post('/contribute')
def save_contribution():
    try:
        data = request.form
    except BadRequest:
        app.logger.warning('Contribution rejected during multipart form parsing.')
        flash('The form upload could not be read. Please reload the page and submit again.', 'error')
        return redirect(url_for('contribute'))
    required = ['phrase','language','region','corrected_transcript','contextual_meaning']
    if any(not data.get(k,'').strip() for k in required):
        app.logger.warning('Contribution rejected: required form fields are missing.')
        flash('Please complete the phrase, language, region, transcript, and contextual meaning.', 'error')
        return redirect(url_for('contribute'))
    if data['language'] not in LANGUAGES:
        app.logger.warning('Contribution rejected: language value failed allowlist validation.')
        flash('Choose Kannada, Tamil, Telugu, or Malayalam as the phrase language. Automatic detection must finish first.', 'error')
        return redirect(url_for('contribute'))
    audio_path = None
    audio = request.files.get('audio')
    if not audio or not audio.filename:
        app.logger.warning('Contribution rejected: audio upload is missing.')
        flash('Add a recording or audio file to create a Phrase Passport.', 'error')
        return redirect(url_for('contribute'))
    if audio and audio.filename:
        error = validate_audio(audio, request.files.get('duration_audio'))
        if error:
            app.logger.warning('Contribution rejected during audio validation: %s', error)
            flash(error, 'error'); return redirect(url_for('contribute'))
        try:
            audio_path = store_audio(audio)
        except Exception as exc:
            app.logger.error('Contribution failed during audio storage upload (%s).', type(exc).__name__)
            flash('We could not store your recording. Please try again in a moment.', 'error')
            return redirect(url_for('contribute'))
    consent = data.get('audio_public_consent') == 'yes'
    try:
        with get_db() as db:
            db.execute('''INSERT INTO phrases (phrase,transliteration,language,region,literal_meaning,contextual_meaning,example_usage,cultural_note,tags,audio_path,contributor,raw_transcript,corrected_transcript,model_used,verification_status,detected_language_code,transcription_request_id,transcription_timestamp,transcription_mode,audio_public_consent)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (data['phrase'].strip(),data.get('transliteration','').strip(),data['language'],data['region'].strip(),data.get('literal_meaning','').strip(),data['contextual_meaning'].strip(),data.get('example_usage','').strip(),data.get('cultural_note','').strip(),data.get('tags','').strip(),audio_path,data.get('contributor','').strip() or 'Anonymous',data.get('raw_transcript','').strip(),data['corrected_transcript'].strip(),data.get('model_used','').strip() or None,'Pending review',data.get('detected_language_code','').strip() or None,data.get('transcription_request_id','').strip() or None,data.get('transcription_timestamp','').strip() or None,data.get('transcription_mode','').strip() or None,consent))
    except Exception as exc:
        app.logger.error('Contribution failed while saving the phrase record (%s).', type(exc).__name__)
        if audio_path:
            try:
                delete_audio(audio_path)
            except Exception as exc:
                app.logger.error('Contribution cleanup could not remove an unlinked audio object (%s).', type(exc).__name__)
        flash('We could not save your Phrase Passport. Your recording was not attached; please try again.', 'error')
        return redirect(url_for('contribute'))
    flash('Your Phrase Passport was saved and is pending community review.', 'success')
    return redirect(url_for('explore'))

@app.get('/uploads/<path:filename>')
def uploaded_audio(filename):
    # The public phrase page only exposes audio when a contributor explicitly consented.
    row = get_db().execute('SELECT audio_path,audio_public_consent FROM phrases WHERE audio_path=?', (filename,)).fetchone()
    if not row or not row['audio_public_consent']:
        abort(404)
    try:
        content = read_audio(row['audio_path'])
    except Exception as exc:
        app.logger.error('Audio retrieval failed for an opted-in recording (%s).', type(exc).__name__)
        abort(503)
    if content is None:
        abort(404)
    ext = Path(filename).suffix.lower()
    mimetype = {'.wav':'audio/wav','.mp3':'audio/mpeg','.m4a':'audio/mp4','.webm':'audio/webm','.ogg':'audio/ogg','.flac':'audio/flac','.aac':'audio/aac'}.get(ext, 'application/octet-stream')
    return send_file(BytesIO(content), mimetype=mimetype, as_attachment=False, download_name=Path(filename).name, max_age=300)

@app.get('/about')
def about(): return render_template('about.html')

@app.errorhandler(413)
def too_large(_):
    app.logger.warning('Request rejected: total upload exceeded the 15 MB request limit.')
    if request.path.startswith('/api/'):
        return jsonify(error='Audio must be no larger than 15 MB.'), 413
    if request.path == '/contribute':
        flash('The upload is larger than 15 MB. Choose a smaller audio clip and try again.', 'error')
        return redirect(url_for('contribute'))
    return 'Audio must be no larger than 15 MB.', 413

@app.errorhandler(CSRFError)
def csrf_error(_error):
    app.logger.warning('Request rejected by CSRF validation at route %s.', request.path)
    message = 'This form expired or could not be verified. Reload the page and try again.'
    if request.path.startswith('/api/'):
        return jsonify(error=message), 400
    if request.path == '/contribute':
        flash(message, 'error')
        return redirect(url_for('contribute'))
    return message, 400


@app.errorhandler(BadRequest)
def bad_request(error):
    app.logger.warning('Bad request rejected at route %s.', request.path)
    message = 'The form upload could not be read. Please reload the page and submit again.'
    if request.path.startswith('/api/'):
        return jsonify(error=message), 400
    if request.path == '/contribute':
        flash(message, 'error')
        return redirect(url_for('contribute'))
    return error

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', '5000')), debug=not IS_PRODUCTION and os.environ.get('FLASK_DEBUG') == '1')
