-- Semantica Postgres is an impala_fdw client of the Signals warehouse.
-- Placeholders: __FDW_SO__ __KEYTAB__ __HS2_HOST__ __KUDU_MASTERS__ __PRINCIPAL__
-- __PRINCIPAL__ is the semantica service principal. signals@ stays with Signals.
-- Do not DROP objects in the Signals database on :5455.

DROP FOREIGN TABLE IF EXISTS semantica_chunk_tier0 CASCADE;
DROP FOREIGN TABLE IF EXISTS semantica_provenance_tier0 CASCADE;
DROP FOREIGN TABLE IF EXISTS semantica_edge_tier0 CASCADE;
DROP FOREIGN TABLE IF EXISTS semantica_node_tier0 CASCADE;
DROP USER MAPPING IF EXISTS FOR CURRENT_USER SERVER impala_kudu_srv;
DROP SERVER IF EXISTS impala_kudu_srv CASCADE;
DROP FOREIGN DATA WRAPPER IF EXISTS impala_fdw CASCADE;
DROP FUNCTION IF EXISTS impala_fdw_exec(text, text) CASCADE;
DROP FUNCTION IF EXISTS impala_fdw_validator(text[], oid) CASCADE;
DROP FUNCTION IF EXISTS impala_fdw_handler() CASCADE;

CREATE FUNCTION impala_fdw_handler()
RETURNS fdw_handler
AS '__FDW_SO__'
LANGUAGE C STRICT;

CREATE FUNCTION impala_fdw_validator(text[], oid)
RETURNS void
AS '__FDW_SO__'
LANGUAGE C STRICT;

CREATE FOREIGN DATA WRAPPER impala_fdw
  HANDLER impala_fdw_handler
  VALIDATOR impala_fdw_validator;

CREATE FUNCTION impala_fdw_exec(server_name text, sql text)
RETURNS text
AS '__FDW_SO__'
LANGUAGE C STRICT;

CREATE SERVER impala_kudu_srv
  FOREIGN DATA WRAPPER impala_fdw
  OPTIONS (
    host '__HS2_HOST__',
    port '21050',
    auth 'kerberos',
    kudu_masters '__KUDU_MASTERS__',
    default_access 'auto'
  );

CREATE USER MAPPING IF NOT EXISTS FOR CURRENT_USER
  SERVER impala_kudu_srv
  OPTIONS (
    principal '__PRINCIPAL__',
    keytab '__KEYTAB__'
  );
