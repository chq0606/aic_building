-- Additive migration: preserve all original readings and application tables.
BEGIN;
CREATE SCHEMA IF NOT EXISTS optimization;
CREATE TABLE IF NOT EXISTS optimization.diagnosis_run (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES core.tenant(id),
    site_id uuid NOT NULL REFERENCES core.site(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    algorithm_version text NOT NULL,
    source_signature text NOT NULL,
    payload jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS diagnosis_run_scope ON optimization.diagnosis_run(tenant_id,site_id,created_at DESC);
CREATE TABLE IF NOT EXISTS optimization.hvac_trial (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES core.tenant(id),
    user_id uuid NOT NULL REFERENCES core."user"(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    inputs jsonb NOT NULL,
    result jsonb NOT NULL
);
COMMIT;
