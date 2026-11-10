import os
import uuid
import wave
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, send_from_directory, url_for
from werkzeug.exceptions import BadRequest
from mutagen import File as MutagenFile, MutagenError
from dotenv import load_dotenv
from werkzeug.utils import secure_filename

load_dotenv()

from config import Config
from database import close_db, get_db, init_db, list_phrases, get_phrase
from gnani_api import GnaniError, transcribe

app = Flask(__name__)
app.config.from_object(Config)
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
        ext = Path(secure_filename(audio.filename)).suffix.lower()
        filename = f'{uuid.uuid4().hex}{ext}'
        audio.save(Path(app.config['UPLOAD_FOLDER']) / filename)
        audio_path = filename
    with get_db() as db:
        db.execute('''INSERT INTO phrases (phrase,transliteration,language,region,literal_meaning,contextual_meaning,example_usage,cultural_note,tags,audio_path,contributor,raw_transcript,corrected_transcript,model_used,verification_status,detected_language_code,transcription_request_id,transcription_timestamp,transcription_mode)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (data['phrase'].strip(),data.get('transliteration','').strip(),data['language'],data['region'].strip(),data.get('literal_meaning','').strip(),data['contextual_meaning'].strip(),data.get('example_usage','').strip(),data.get('cultural_note','').strip(),data.get('tags','').strip(),audio_path,data.get('contributor','').strip() or 'Anonymous',data.get('raw_transcript','').strip(),data['corrected_transcript'].strip(),data.get('model_used','').strip() or None,'Pending review',data.get('detected_language_code','').strip() or None,data.get('transcription_request_id','').strip() or None,data.get('transcription_timestamp','').strip() or None,data.get('transcription_mode','').strip() or None))
    flash('Your Phrase Passport was saved and is pending community review.', 'success')
    return redirect(url_for('explore'))

@app.get('/uploads/<path:filename>')
def uploaded_audio(filename):
    if Path(filename).name != filename: abort(404)
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename, as_attachment=False)

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
    app.run(debug=os.environ.get('FLASK_DEBUG') == '1')
