-- Inventory is the last pull, not the union of every pull ever made.
--
-- `reachable` used to be emptied per inventory and refilled, which lost the
-- history, and the row a dead provider left behind was indistinguishable from
-- one the router served this morning. A row is now kept and marked `stale`
-- when the pull that follows it no longer lists it: the history survives, and
-- every reader that has to answer "can I route here right now" asks for
-- `stale = 0`, which is exactly the most recent successful pull.
ALTER TABLE reachable ADD COLUMN stale INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS reachable_stale ON reachable (stale);
