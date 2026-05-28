-- Least-privilege application role (security audit F7).
--
-- WHY: the stock Postgres entrypoint creates POSTGRES_USER as a SUPERUSER, and
-- a superuser BYPASSES row-level security unconditionally (even FORCE ROW
-- LEVEL SECURITY). If the app connects as that superuser, every RLS policy in
-- the schema is inert and tenant isolation rests entirely on the app-layer
-- WHERE filters. This provisions a NOSUPERUSER NOBYPASSRLS role for the app to
-- connect as, so RLS is actually enforced at runtime.
--
-- WHEN IT RUNS: Postgres executes /docker-entrypoint-initdb.d/*.sql ONLY on
-- first cluster init (empty data dir). It runs BEFORE migrations, so the
-- ALTER DEFAULT PRIVILEGES below is what grants DML on the tables alembic
-- later creates (as the bootstrap superuser). For an EXISTING database the
-- initdb hook will NOT fire — run these statements once by hand as the
-- superuser (see SECURITY.md / the F7 runbook).
--
-- AFTER provisioning: point DATABASE_URL / DATABASE_URL_SYNC at neuthek_app
-- and keep the bootstrap superuser ONLY for `alembic upgrade head` (DDL needs
-- it). The password comes from NEUTHEK_APP_DB_PASSWORD passed to the postgres
-- container; psql reads it from the environment via the backtick below.

\set app_pw `echo "$NEUTHEK_APP_DB_PASSWORD"`

-- First-init only, so a plain CREATE ROLE is safe (the role can't pre-exist).
CREATE ROLE neuthek_app LOGIN PASSWORD :'app_pw'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;

-- CONNECT is granted to PUBLIC by default, so no explicit GRANT CONNECT.
GRANT USAGE ON SCHEMA public TO neuthek_app;

-- No tables exist yet at init time; these ALL-TABLES grants are no-ops now but
-- harmless. The ALTER DEFAULT PRIVILEGES lines are the important part: they
-- grant DML on every table/sequence the superuser creates LATER (migrations).
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO neuthek_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO neuthek_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO neuthek_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO neuthek_app;
