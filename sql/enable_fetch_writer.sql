-- Run once in the Supabase SQL editor, after `uv run tr-banking db migrate` applied
-- 0003_fetch_writer.sql. Replace CHANGE_ME with a long random password in the editor only.
-- Never save the real password in this file or commit it, and delete the query from the
-- editor's history after running it.
--
-- Then set the GitHub secret DATABASE_URL (Session pooler, this role as the user):
--   postgresql://fetch_writer.<project-ref>:<password>@<pooler-host>:5432/postgres

ALTER ROLE fetch_writer WITH LOGIN PASSWORD 'CHANGE_ME';
