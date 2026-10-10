-- Local control tables. Heavy graph, provenance, and chunk rows are not
-- stored here. They are signals_dataproducts.semantica_*_tier0 (Kudu),
-- reached through impala_fdw. See kudu.sql.

CREATE SCHEMA IF NOT EXISTS admin;

CREATE TABLE IF NOT EXISTS admin.graph (
  graph_id text PRIMARY KEY,
  name text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now()
);

COMMENT ON SCHEMA admin IS
  'Semantica control plane. Not the warehouse.';
