-- Run once in the Supabase SQL editor, after `uv run tr-banking db migrate`.
-- Replace CHANGE_ME with a long random password in the editor only.
-- Never save the real password in this file or commit it.
--
-- Dashboard connection (Supabase > Connect > Session pooler), with this role as the user:
--   postgresql://dashboard_reader.<project-ref>:<password>@<pooler-host>:5432/postgres

ALTER ROLE dashboard_reader WITH LOGIN PASSWORD 'CHANGE_ME';
