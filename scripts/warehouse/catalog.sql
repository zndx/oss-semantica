-- HMS-free visibility for tables created by signals_kudu_create.
-- Apply on signals_catalog (:5455), not on the Semantica admin database.
-- ICEBERG rows would be inert here; these are KUDU. Iceberg stays the settle
-- tier and is not registered until that write path exists.
-- Keep the parameters in sync with config/platform/signal-registry.sql.

INSERT INTO catalog_databases (name, location)
VALUES ('signals_dataproducts', 's3a://signals-dataproducts/iceberg')
ON CONFLICT (name) DO NOTHING;

INSERT INTO catalog_tables (db_name, table_name, table_type, parameters) VALUES
  ('signals_dataproducts', 'semantica_node_tier0', 'KUDU',
   '{"kudu.table_name": "impala::signals_dataproducts.semantica_node_tier0", "kudu.master_addresses": "tinybox.dev.vista.zndx.org:7051", "signals.tier": "0", "signals.product": "semantica.graph", "signals.settle": "iceberg"}'),
  ('signals_dataproducts', 'semantica_edge_tier0', 'KUDU',
   '{"kudu.table_name": "impala::signals_dataproducts.semantica_edge_tier0", "kudu.master_addresses": "tinybox.dev.vista.zndx.org:7051", "signals.tier": "0", "signals.product": "semantica.graph", "signals.settle": "iceberg"}'),
  ('signals_dataproducts', 'semantica_provenance_tier0', 'KUDU',
   '{"kudu.table_name": "impala::signals_dataproducts.semantica_provenance_tier0", "kudu.master_addresses": "tinybox.dev.vista.zndx.org:7051", "signals.tier": "0", "signals.product": "semantica.provenance", "signals.settle": "iceberg"}'),
  ('signals_dataproducts', 'semantica_chunk_tier0', 'KUDU',
   '{"kudu.table_name": "impala::signals_dataproducts.semantica_chunk_tier0", "kudu.master_addresses": "tinybox.dev.vista.zndx.org:7051", "signals.tier": "0", "signals.product": "semantica.chunk", "signals.settle": "iceberg"}')
ON CONFLICT (db_name, table_name) DO UPDATE SET
  table_type = EXCLUDED.table_type, parameters = EXCLUDED.parameters;
