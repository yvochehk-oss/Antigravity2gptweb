ALTER TABLE canonical_facts
DROP CONSTRAINT IF EXISTS ck_canonical_facts_current_requires_accepted;
ALTER TABLE canonical_facts
ADD CONSTRAINT ck_canonical_facts_current_requires_accepted
CHECK (NOT is_current OR status = 'accepted');

ALTER TABLE canonical_facts
DROP CONSTRAINT IF EXISTS ck_canonical_facts_accepted_requires_timestamp;
ALTER TABLE canonical_facts
ADD CONSTRAINT ck_canonical_facts_accepted_requires_timestamp
CHECK (status <> 'accepted' OR accepted_at IS NOT NULL);

CREATE UNIQUE INDEX IF NOT EXISTS uq_canonical_fact_business_version
    ON canonical_facts(project_id, fact_type, business_key, fact_version);
