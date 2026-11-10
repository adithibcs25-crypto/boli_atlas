# BOLI ATLAS

An audio-first Phrase Passport archive for expressions in Kannada, Tamil, Telugu, and Malayalam. Existing homepage, Explore, phrase details, recording, Prisma transcription, and contribution workflows are preserved. New contributions remain **Pending review**.

## Run locally on Windows

```powershell
cd D:\bholi-atlas
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
# Put your private GNANI_API_KEY in .env. FLASK_SECRET_KEY is recommended locally.
python app.py
```

Open http://127.0.0.1:5000. With `DATABASE_URL` unset, the app uses `instance/boli.db` and `uploads/recordings`, as before. Gnani transcription requires `GNANI_API_KEY`; the key is used only by the Flask server. Browser-recorded WebM is converted to WAV for Prisma, while the original recording is kept with the saved Phrase Passport.

## Deploy on Render with Supabase

### 1. Prepare Supabase

1. Create a Supabase project and choose a region close to the Render service.
2. Open **SQL Editor**, run [`supabase/storage_setup.sql`](supabase/storage_setup.sql), and confirm `boli-audio` is marked private. The script restricts uploads to 15 MB and the audio MIME types accepted by the app.
3. Do not add Storage object policies for `anon` or `authenticated`; RLS denies object access in their absence. Flask accesses the private bucket with the server-only secret key, which bypasses RLS. The app serves a recording through its own route only when the contributor checks the explicit public playback consent box. Historical recordings default to no consent.
4. In **Project Settings → API Keys**, create and copy a server-side **Secret API key** with the new `sb_secret_…` format. Do not use the deprecated `service_role` key. Also copy the root Project URL (without `/rest/v1`). In **Connect**, copy the PostgreSQL connection string and replace `[YOUR-PASSWORD]` with your actual database password. For Render without an IPv4 add-on, use Supabase's **Session pooler** string (port 5432). URL-encode reserved characters in the password.

### 2. Configure Render

1. Push this project to a Git repository you control.
2. In Render, create a Web Service from that repository and select `render.yaml` as its Blueprint, or configure the equivalent commands manually:
   - Build: `pip install -r requirements.txt`
   - Start: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120`
   - Health check path: `/healthz`
3. Set these environment variables in Render's dashboard (or fill the `sync: false` prompts when applying the Blueprint):

   | Variable | Value |
   | --- | --- |
   | `FLASK_SECRET_KEY` | A unique, long random secret; do not reuse the local development value. |
   | `GNANI_API_KEY` | Your Gnani API key. |
   | `SUPABASE_URL` | The Supabase Project URL. |
   | `SUPABASE_SECRET_KEY` | A Supabase Secret API key beginning `sb_secret_`. Server only; do not use the deprecated `service_role` key. |
   | `DATABASE_URL` | Supabase PostgreSQL Session pooler URI (or a supported direct URI). |

4. Deploy. `/healthz` checks PostgreSQL and returns HTTP 200 only when the database is reachable. App startup in Render fails with the names of missing required variables, never their values.

Render uses Gunicorn and binds to its injected `PORT`. Local SQLite and local audio remain available for development; `DATABASE_URL` selects PostgreSQL and configured Supabase Storage credentials select remote audio storage.

## Import existing SQLite data

Back up `instance/boli.db` and `uploads/recordings` before importing. Configure `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_SECRET_KEY` in a private local environment (do not commit `.env`). Start the app once against the Supabase settings to create the PostgreSQL schema, then run:

```powershell
python import_sqlite.py --sqlite instance/boli.db --audio-dir uploads/recordings
```

The importer preserves phrase IDs and fields, uploads available local audio to the private bucket, and stores each Supabase object path in PostgreSQL. It is safe to rerun: IDs already present in PostgreSQL are skipped. Missing local audio files are reported and the phrase row is still imported without a broken audio reference. Historical audio is imported with playback consent off. Review the report and resolve any missing recordings before switching traffic to the deployed app. Do not run this against a database containing unrelated records with colliding IDs; the importer deliberately leaves existing IDs untouched.

For a dry import of text-only phrase rows, add `--skip-audio` (audio paths will be blank in PostgreSQL).

## Data and privacy notes

- PostgreSQL stores phrase data, both raw and corrected transcripts, metadata, and the Supabase Storage object path. Supabase Storage stores the recording itself.
- The bucket remains private. The UI does not offer a public player unless the contributor explicitly opts in. Existing records receive a false consent value automatically.
- Gnani requests and credentials stay server-side. The frontend calls only the local `/api/transcribe` route.
- `.env` is ignored by Git. Never commit secret values or the Supabase secret key.
- `/healthz` is intended for Render's health checks; it reveals only a generic status.

## Gnani Prisma v3

The backend sends `POST https://api.vachana.ai/stt/v3` with `X-API-Key-ID`, multipart `audio_file`, explicit `model=prisma-v3.0`, a supported language or `indic-auto`, and the selected output format (`verbatim`, `transcribe`, `codemix`, or `transliterate`). The server keeps Prisma's raw transcript separate from the contributor's editable correction and stores model, detected language, request ID, mode, and timestamp.
