-- Heavy Semantica tables on the Signals warehouse.
--
-- Shape contract only. not Iceberg-only. The apply path is
-- signals_kudu_create (libkudu_client) plus catalog_tables on
-- signals_catalog (:5455). impala_fdw_exec refuses CREATE TABLE
-- (#SL.00000029.RANGEDDL). Do not schema-apply this file through Python
-- impyla, and do not send it to impala_fdw_exec.
--
-- Kudu tier0 is the live store. impala_fdw INSERT is kudu_scan (Kudu upsert).
-- Impala Iceberg DML is still pending (#SL.00000027.SCHEMA2), so tier1 is the
-- settle target and is not created here. These rows are current state, hashed
-- on graph_id, with no range partition. Removals are tombstones (removed = 1).
-- The flag is INT, not BOOLEAN: kudu_scan cannot push a boolean NOT, and the
-- live view filters with removed = 0.

CREATE DATABASE IF NOT EXISTS signals_dataproducts;

CREATE TABLE IF NOT EXISTS signals_dataproducts.semantica_node_tier0 (
  graph_id STRING,
  node_id STRING,
  node_type STRING,
  label STRING,
  properties STRING,
  updated_ns BIGINT,
  removed INT,
  PRIMARY KEY (graph_id, node_id)
)
PARTITION BY HASH (graph_id) PARTITIONS 4
STORED AS KUDU
TBLPROPERTIES (
  'kudu.num_tablet_replicas' = '1',
  'signals.tier' = '0',
  'signals.product' = 'semantica.graph',
  'signals.settle' = 'iceberg'
);

CREATE TABLE IF NOT EXISTS signals_dataproducts.semantica_edge_tier0 (
  graph_id STRING,
  edge_id STRING,
  source_id STRING,
  target_id STRING,
  edge_type STRING,
  weight DOUBLE,
  properties STRING,
  updated_ns BIGINT,
  removed INT,
  PRIMARY KEY (graph_id, edge_id)
)
PARTITION BY HASH (graph_id) PARTITIONS 4
STORED AS KUDU
TBLPROPERTIES (
  'kudu.num_tablet_replicas' = '1',
  'signals.tier' = '0',
  'signals.product' = 'semantica.graph',
  'signals.settle' = 'iceberg'
);

CREATE TABLE IF NOT EXISTS signals_dataproducts.semantica_provenance_tier0 (
  graph_id STRING,
  entity_id STRING,
  entity_type STRING,
  activity_id STRING,
  agent_id STRING,
  source_document STRING,
  payload STRING,
  recorded_ns BIGINT,
  PRIMARY KEY (graph_id, entity_id)
)
PARTITION BY HASH (graph_id) PARTITIONS 4
STORED AS KUDU
TBLPROPERTIES (
  'kudu.num_tablet_replicas' = '1',
  'signals.tier' = '0',
  'signals.product' = 'semantica.provenance',
  'signals.settle' = 'iceberg'
);

CREATE TABLE IF NOT EXISTS signals_dataproducts.semantica_chunk_tier0 (
  graph_id STRING,
  chunk_id STRING,
  document_id STRING,
  body STRING,
  updated_ns BIGINT,
  removed INT,
  PRIMARY KEY (graph_id, chunk_id)
)
PARTITION BY HASH (graph_id) PARTITIONS 4
STORED AS KUDU
TBLPROPERTIES (
  'kudu.num_tablet_replicas' = '1',
  'signals.tier' = '0',
  'signals.product' = 'semantica.chunk',
  'signals.settle' = 'iceberg'
);
