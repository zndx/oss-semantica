"""Heavy Semantica tables are Kudu tier0. Admin tables stay local."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SQL = ROOT / "scripts" / "warehouse"


def _text(name: str) -> str:
    return (SQL / name).read_text(encoding="utf-8")


def test_admin_schema_is_local_postgres():
    admin = _text("admin.sql")
    assert "CREATE SCHEMA IF NOT EXISTS admin" in admin
    assert "CREATE TABLE IF NOT EXISTS admin.graph" in admin
    assert "SERVER impala_kudu_srv" not in admin
    assert "STORED AS ICEBERG" not in admin


def test_heavy_tables_are_kudu_tier0_not_iceberg_only():
    kudu = _text("kudu.sql")
    for table in (
        "semantica_node_tier0",
        "semantica_edge_tier0",
        "semantica_provenance_tier0",
        "semantica_chunk_tier0",
    ):
        assert f"signals_dataproducts.{table}" in kudu
    assert "STORED AS KUDU" in kudu
    assert "STORED AS ICEBERG" not in kudu
    assert "not Iceberg-only" in kudu


def test_foreign_tables_use_kudu_scan_and_hide_tombstones():
    foreign = _text("foreign.sql")
    assert foreign.count("access 'kudu_scan'") == 4
    assert "access 'impala_sql'" not in foreign
    assert "CREATE OR REPLACE VIEW warehouse.v_node" in foreign
    assert "WHERE removed = 0" in foreign
    assert "NOT removed" not in foreign
    assert "removed boolean" not in foreign


def test_install_uses_the_signals_fdw_and_does_not_vendor_it():
    script = (SQL / "install_impala_fdw.sh").read_text(encoding="utf-8")
    assert "components/impala_fdw/impala_fdw.so" in script
    assert "SIGNALS_ROOT" in script
    assert "signals_kudu_create" in script
    assert "kinit -kt" in script
    assert "semantica.keytab" in script
    assert "semantica@DEV.VISTA.ZNDX.ORG" in script
    assert "signals.keytab" not in script
    assert "SELECT impala_fdw_exec" not in script
    assert "signals_catalog" in script
    catalog = _text("catalog.sql")
    for table in (
        "semantica_node_tier0",
        "semantica_edge_tier0",
        "semantica_provenance_tier0",
        "semantica_chunk_tier0",
    ):
        assert table in catalog
    assert "STORED AS ICEBERG" not in catalog
    assert not (ROOT / "components" / "impala_fdw").exists()
