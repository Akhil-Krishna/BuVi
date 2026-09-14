-- Development bootstrap for the shared Postgres cluster.
--
-- Section 19 requires that application traffic never runs under a role that can
-- bypass Row-Level Security. Two roles exist from day one:
--
--   buvi_migrator  owns the per-service schemas and runs Alembic. Because it
--                  owns the tables it is exempt from their RLS policies, so it
--                  is never used by request-handling code.
--   buvi_app       the role every service uses at request time. It owns
--                  nothing, so every RLS policy applies to it.
--
-- Production provisions the equivalent pair through Terraform (Section 28); the
-- role names and grants below are the contract those modules implement.

CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()

CREATE ROLE buvi_migrator LOGIN PASSWORD 'devmigrator';
CREATE ROLE buvi_app LOGIN PASSWORD 'devapp';

GRANT CONNECT ON DATABASE agentic_bi TO buvi_migrator, buvi_app;
-- buvi_migrator owns the per-service schemas, so it may create them.
GRANT CREATE ON DATABASE agentic_bi TO buvi_migrator;

-- One schema per owning service (Section 8). Each is created up front so a
-- service's first migration never needs cluster-level privileges.
CREATE SCHEMA IF NOT EXISTS identity      AUTHORIZATION buvi_migrator;
CREATE SCHEMA IF NOT EXISTS metadata      AUTHORIZATION buvi_migrator;
CREATE SCHEMA IF NOT EXISTS semantic      AUTHORIZATION buvi_migrator;
CREATE SCHEMA IF NOT EXISTS analytics     AUTHORIZATION buvi_migrator;
CREATE SCHEMA IF NOT EXISTS query_gateway AUTHORIZATION buvi_migrator;
CREATE SCHEMA IF NOT EXISTS dashboard     AUTHORIZATION buvi_migrator;
CREATE SCHEMA IF NOT EXISTS mcp           AUTHORIZATION buvi_migrator;
CREATE SCHEMA IF NOT EXISTS notification  AUTHORIZATION buvi_migrator;

DO $$
DECLARE
    service_schema TEXT;
BEGIN
    FOREACH service_schema IN ARRAY ARRAY[
        'identity', 'metadata', 'semantic', 'analytics',
        'query_gateway', 'dashboard', 'mcp', 'notification'
    ] LOOP
        EXECUTE format('GRANT USAGE ON SCHEMA %I TO buvi_app', service_schema);

        -- buvi_app reads and writes rows but never alters structure.
        EXECUTE format(
            'ALTER DEFAULT PRIVILEGES FOR ROLE buvi_migrator IN SCHEMA %I '
            'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO buvi_app',
            service_schema
        );
        EXECUTE format(
            'ALTER DEFAULT PRIVILEGES FOR ROLE buvi_migrator IN SCHEMA %I '
            'GRANT USAGE, SELECT ON SEQUENCES TO buvi_app',
            service_schema
        );
    END LOOP;
END
$$;
