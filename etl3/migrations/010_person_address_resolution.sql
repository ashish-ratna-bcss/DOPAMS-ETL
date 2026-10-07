-- Confirmed geography from the address knowledge-base lookup.
-- Null means the knowledge base did not confirm a field. Raw person
-- address columns on persons_unified are not changed.

ALTER TABLE person_enrichment
    ADD COLUMN IF NOT EXISTS address_resolution JSONB;
