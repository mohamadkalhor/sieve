-- A connector names a secret id, not the name of an environment variable.
--
-- `token_env` was a pointer into this box's environment that the API let anybody
-- with `profiles:write` set, so a viewer could aim the owner's gateway token at
-- their own host and press test. A connector now carries `secret`, an id that
-- exists only in `[secrets.<id>]` in `sieve.toml` (`env = "GATEWAY_TOKEN",
-- kinds = ["ninerouter"]`), which is edited on the box and never over the wire,
-- and which is bound to the connector kinds it may be used with.
--
-- The old columns stay: a row this migration has not moved yet still says what
-- it used to name, and `tools/migrate_connector_secrets.py` reports on it. They
-- are cleared by that script, and nothing reads them any more.
ALTER TABLE connectors ADD COLUMN secret TEXT;
ALTER TABLE connectors ADD COLUMN admin_secret TEXT;
