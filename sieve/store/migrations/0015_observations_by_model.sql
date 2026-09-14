-- One model's observations, without reading everybody's.
--
-- `observations_lookup` is (modality, source, field, model_id, observed_at):
-- a query that knows only the model id cannot use it, so
-- `observations_for(model_id)` -- which is what GET /v1/models/{id} returns --
-- was a full scan of the table. On the live store that is 2,095,878 rows and
-- more than twelve seconds per lookup.
--
-- Building this index reads the whole table once. Expect the first start after
-- this migration to take about half a minute on a store that size, and the
-- file to grow by roughly a tenth.
CREATE INDEX IF NOT EXISTS observations_model
    ON observations (model_id, observed_at DESC);
