import uuid

import requests

API_URL = 'https://api.vachana.ai/stt/v3'
MODEL = 'prisma-v3.0'
FORMATS = {'verbatim', 'transcribe', 'codemix', 'transliterate'}


class GnaniError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def transcribe(file_storage, language_code, output_format, api_key):
    if not api_key:
        raise GnaniError('Gnani transcription is not configured. Add GNANI_API_KEY to .env.', 503)
    if output_format not in FORMATS:
        raise GnaniError('Choose a supported transcription format.', 400)
    if language_code != 'indic-auto' and language_code not in {'kn-IN', 'ta-IN', 'te-IN', 'ml-IN'}:
        raise GnaniError('Choose Kannada, Tamil, Telugu, Malayalam, or automatic language identification.', 400)

    request_id = str(uuid.uuid4())
    try:
        response = requests.post(
            API_URL,
            headers={'X-API-Key-ID': api_key, 'X-API-Request-ID': request_id},
            files={'audio_file': (file_storage.filename, file_storage.stream, file_storage.mimetype or 'application/octet-stream')},
            data={'model': MODEL, 'language_code': language_code, 'format': output_format},
            timeout=(10, 60),
        )
    except requests.Timeout as exc:
        raise GnaniError('Gnani transcription timed out. Retry the request.', 504) from exc
    except requests.RequestException as exc:
        raise GnaniError('Could not reach Gnani. Check the connection and retry.', 503) from exc

    if response.status_code in (401, 403):
        raise GnaniError('Gnani rejected the API key or STT access. Check the key and account permissions.', 502)
    if response.status_code == 429:
        raise GnaniError('Gnani is rate limiting requests. Wait briefly, then retry.', 429)
    if response.status_code == 413:
        raise GnaniError('Gnani rejected the audio because it is too large.', 413)
    if response.status_code == 415:
        raise GnaniError('Gnani does not support this audio format. Use WAV, MP3, OGG, FLAC, AAC, or M4A.', 415)
    if response.status_code >= 500:
        raise GnaniError('Gnani is temporarily unavailable. Retry shortly.', 502)

    try:
        body = response.json()
    except ValueError:
        body = {}
    if not response.ok or body.get('success') is False:
        # Do not return provider response text: it is untrusted and may echo request data.
        raise GnaniError('Gnani could not process this audio or request. Check the format and try again.', 502)
    if not isinstance(body.get('transcript'), str):
        raise GnaniError('Gnani returned an unexpected response. Retry or enter the transcript manually.', 502)
    body['request_id'] = body.get('request_id') or request_id
    body['model'] = body.get('model') or MODEL
    return body
