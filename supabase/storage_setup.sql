-- Run in Supabase SQL Editor before deployment.
-- No anon/authenticated object policies are added: RLS denies them by default.
-- The trusted Flask server uses SUPABASE_SECRET_KEY and enforces consent itself.
INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES (
  'boli-audio',
  'boli-audio',
  false,
  15728640,
  ARRAY['audio/wav', 'audio/x-wav', 'audio/mpeg', 'audio/mp4', 'audio/x-m4a', 'audio/ogg', 'application/ogg', 'audio/webm', 'audio/flac', 'audio/x-flac', 'audio/aac', 'audio/x-aac']
)
ON CONFLICT (id) DO UPDATE SET
  name = EXCLUDED.name,
  public = false,
  file_size_limit = EXCLUDED.file_size_limit,
  allowed_mime_types = EXCLUDED.allowed_mime_types;
