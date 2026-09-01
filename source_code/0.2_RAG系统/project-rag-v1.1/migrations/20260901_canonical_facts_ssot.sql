CREATE TABLE IF NOT EXISTS canonical_facts (
    id BIGSERIAL PRIMARY KEY,
    source_document_id BIGINT NOT NULL REFERENCES documents(id) ON DELETE RESTRICT,
    project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    fact_type VARCHAR(24) NOT NULL,
    business_key VARCHAR(180) NOT NULL,
    schema_version VARCHAR(24) NOT NULL DEFAULT 'v1',
    fact_version INTEGER NOT NULL DEFAULT 1 CHECK (fact_version > 0),
    source_hash CHAR(64) NOT NULL,
    payload JSONB NOT NULL,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    validation_errors JSONB NOT NULL DEFAULT '[]'::jsonb,
    confidence NUMERIC(6,5) NOT NULL DEFAULT 0 CHECK (confidence >= 0 AND confidence <= 1),
    status VARCHAR(24) NOT NULL CHECK (status IN ('accepted','needs_review','superseded','rejected')),
    is_current BOOLEAN NOT NULL DEFAULT FALSE,
    producer VARCHAR(80) NOT NULL DEFAULT 'rag',
    accepted_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_canonical_fact_source_hash UNIQUE (source_document_id, fact_type, source_hash)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_canonical_fact_current_business
    ON canonical_facts(project_id, fact_type, business_key)
    WHERE status = 'accepted' AND is_current;
CREATE INDEX IF NOT EXISTS ix_canonical_facts_project_type_status
    ON canonical_facts(project_id, fact_type, status);
CREATE INDEX IF NOT EXISTS ix_canonical_facts_document
    ON canonical_facts(source_document_id);

CREATE TABLE IF NOT EXISTS canonical_fact_outbox (
    id BIGSERIAL PRIMARY KEY,
    fact_id BIGINT NOT NULL REFERENCES canonical_facts(id) ON DELETE CASCADE,
    event_type VARCHAR(64) NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at TIMESTAMPTZ NULL,
    CONSTRAINT uq_canonical_fact_outbox_event UNIQUE (fact_id, event_type)
);
CREATE INDEX IF NOT EXISTS ix_canonical_fact_outbox_unpublished
    ON canonical_fact_outbox(id) WHERE published_at IS NULL;

CREATE OR REPLACE VIEW analytics_canonical_facts_current AS
SELECT
    id AS fact_id,
    source_document_id,
    project_id,
    fact_type,
    business_key,
    schema_version,
    fact_version,
    source_hash,
    payload,
    evidence,
    confidence,
    producer,
    accepted_at,
    created_at,
    updated_at
FROM canonical_facts
WHERE status = 'accepted' AND is_current = TRUE;

CREATE OR REPLACE VIEW analytics_canonical_fact_review_queue AS
SELECT
    id AS fact_id,
    source_document_id,
    project_id,
    fact_type,
    business_key,
    fact_version,
    source_hash,
    payload,
    evidence,
    validation_errors,
    confidence,
    producer,
    created_at,
    updated_at
FROM canonical_facts
WHERE status = 'needs_review';
