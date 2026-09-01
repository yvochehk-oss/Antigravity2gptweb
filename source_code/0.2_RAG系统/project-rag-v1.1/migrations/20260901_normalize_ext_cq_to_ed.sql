-- One-time data self-heal: EXT-CQ is an alias of canonical external party ED.
-- PostgreSQL only. Idempotent after a successful first run.
-- Run after taking a DB backup and after deploying the code-side normalization fixes.

BEGIN;

LOCK TABLE documents IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE external_parties IN SHARE ROW EXCLUSIVE MODE;

DO $$
DECLARE
    ed_count integer;
BEGIN
    SELECT count(*)
      INTO ed_count
      FROM external_parties
     WHERE upper(trim(code)) = 'ED';

    IF ed_count <> 1 THEN
        RAISE EXCEPTION 'Expected exactly one canonical external_parties row for ED, found %', ed_count;
    END IF;
END
$$;

-- Move every durable document reference from the alias to the canonical code.
UPDATE documents
   SET counterparty_code = 'ED'
 WHERE upper(trim(counterparty_code)) = 'EXT-CQ';

-- The alias row is an ingestion artifact, not a separate business entity.
-- Delete it only after no document still refers to it.
DELETE FROM external_parties
 WHERE upper(trim(code)) = 'EXT-CQ'
   AND NOT EXISTS (
       SELECT 1
         FROM documents
        WHERE upper(trim(counterparty_code)) = 'EXT-CQ'
   );

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM documents
         WHERE upper(trim(counterparty_code)) = 'EXT-CQ'
    ) THEN
        RAISE EXCEPTION 'Self-heal failed: documents still reference EXT-CQ';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM external_parties
         WHERE upper(trim(code)) = 'EXT-CQ'
    ) THEN
        RAISE EXCEPTION 'Self-heal failed: redundant external_parties row EXT-CQ still exists';
    END IF;
END
$$;

COMMIT;

-- Expected verification result for the reported dataset:
--   documents(counterparty_code='ED')     = 13
--   documents(counterparty_code='EXT-CQ') = 0
--   external_parties(code='ED')           = 1
--   external_parties(code='EXT-CQ')       = 0
SELECT counterparty_code, count(*) AS document_count
  FROM documents
 WHERE upper(trim(counterparty_code)) IN ('ED', 'EXT-CQ')
 GROUP BY counterparty_code
 ORDER BY counterparty_code;

SELECT id, code, name, kind, active
  FROM external_parties
 WHERE upper(trim(code)) IN ('ED', 'EXT-CQ')
 ORDER BY code, id;
