-- Foreign tables over Kudu tier0. Run after signals_kudu_create.
-- Reads of the current graph use warehouse.v_*. Writers INSERT the tier0
-- foreign table (Kudu upsert). removed is a tombstone: the FDW has no DELETE.
-- kudu_scan rejects residual quals, and a boolean NOT is not a pushable
-- predicate, so `removed` is an integer (0 live, 1 tombstone).

CREATE FOREIGN TABLE semantica_node_tier0 (
  graph_id text,
  node_id text,
  node_type text,
  label text,
  properties text,
  updated_ns bigint,
  removed integer
) SERVER impala_kudu_srv
OPTIONS (
  database 'signals_dataproducts',
  "table" 'semantica_node_tier0',
  kudu_table 'impala::signals_dataproducts.semantica_node_tier0',
  access 'kudu_scan'
);

CREATE FOREIGN TABLE semantica_edge_tier0 (
  graph_id text,
  edge_id text,
  source_id text,
  target_id text,
  edge_type text,
  weight double precision,
  properties text,
  updated_ns bigint,
  removed integer
) SERVER impala_kudu_srv
OPTIONS (
  database 'signals_dataproducts',
  "table" 'semantica_edge_tier0',
  kudu_table 'impala::signals_dataproducts.semantica_edge_tier0',
  access 'kudu_scan'
);

CREATE FOREIGN TABLE semantica_provenance_tier0 (
  graph_id text,
  entity_id text,
  entity_type text,
  activity_id text,
  agent_id text,
  source_document text,
  payload text,
  recorded_ns bigint
) SERVER impala_kudu_srv
OPTIONS (
  database 'signals_dataproducts',
  "table" 'semantica_provenance_tier0',
  kudu_table 'impala::signals_dataproducts.semantica_provenance_tier0',
  access 'kudu_scan'
);

CREATE FOREIGN TABLE semantica_chunk_tier0 (
  graph_id text,
  chunk_id text,
  document_id text,
  body text,
  updated_ns bigint,
  removed integer
) SERVER impala_kudu_srv
OPTIONS (
  database 'signals_dataproducts',
  "table" 'semantica_chunk_tier0',
  kudu_table 'impala::signals_dataproducts.semantica_chunk_tier0',
  access 'kudu_scan'
);

CREATE SCHEMA IF NOT EXISTS warehouse;

CREATE OR REPLACE VIEW warehouse.v_node AS
SELECT graph_id, node_id, node_type, label, properties, updated_ns
FROM semantica_node_tier0
WHERE removed = 0;

CREATE OR REPLACE VIEW warehouse.v_edge AS
SELECT graph_id, edge_id, source_id, target_id, edge_type, weight, properties, updated_ns
FROM semantica_edge_tier0
WHERE removed = 0;

CREATE OR REPLACE VIEW warehouse.v_provenance AS
SELECT graph_id, entity_id, entity_type, activity_id, agent_id, source_document, payload, recorded_ns
FROM semantica_provenance_tier0;

CREATE OR REPLACE VIEW warehouse.v_chunk AS
SELECT graph_id, chunk_id, document_id, body, updated_ns
FROM semantica_chunk_tier0
WHERE removed = 0;
