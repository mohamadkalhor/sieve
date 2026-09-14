-- A revoked token's name is free again (AMS-28).
--
-- `tokens_owner_name` covered every row, revoked ones included, so minting a
-- second `cron` after revoking the first raised an IntegrityError -- a 500 on
-- the one page where a person is told to name things themselves. The row of a
-- revoked token is kept forever so its hash can never be honoured again, but
-- its *name* is only a label, and holding a label hostage is not security.

DROP INDEX IF EXISTS tokens_owner_name;
CREATE UNIQUE INDEX IF NOT EXISTS tokens_owner_live_name
    ON tokens (owner_id, name) WHERE revoked = 0;
