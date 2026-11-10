# BOLI ATLAS

An audio-first, local Phrase Passport archive for expressions in Kannada, Tamil, Telugu, and Malayalam. New contributions are marked **Pending review**. Audio stays in the local `uploads/recordings` folder; this MVP does not publish verified community claims.

## Run on Windows PowerShell

```powershell
cd D:\bholi-atlas
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
# Add GNANI_API_KEY to .env using your Gnani dashboard key.
python app.py
```

Open http://127.0.0.1:5000. Gnani's key is optional: the rest of the app works and transcripts can be entered manually. For persistence of the key, set it in your PowerShell profile or load it from your private environment manager; never commit `.env`. To stop the server press Ctrl+C.

## Gnani Prisma v3

The backend sends `POST https://api.vachana.ai/stt/v3` with `X-API-Key-ID`, multipart `audio_file`, explicit `model=prisma-v3.0`, a supported language or `indic-auto`, and the selected output format (`verbatim`, `transcribe`, `codemix`, or `transliterate`). The API key remains on the Flask server. Browser-recorded WebM is converted to WAV in the browser for transcription; the original recording remains attached to the saved Phrase Passport. The server validates audio size and duration, and records Prisma's raw transcript separately from the human-corrected transcript, along with detected language, model, request ID, mode, and timestamp.

## Notes

The project initializes its SQLite database at `instance/boli.db` on first run and applies additive schema migrations without deleting existing phrases. New contributions are pending review by default. Gnani's documented request and response fields are described in the [REST transcription docs](https://docs.gnani.ai/api/STT/speech-to-text).
