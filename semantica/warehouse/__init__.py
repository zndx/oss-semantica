"""Lab warehouse client.

Admin rows stay in this peer's Postgres. Heavy graph, provenance, and chunk
rows are Kudu tier0 tables in the Signals warehouse, read and written through
``impala_fdw`` (``kudu_scan``). Iceberg is the settle tier. It is not the
write path: Impala Iceberg DML is still pending.
"""

from semantica.warehouse.graph import attach_warehouse

__all__ = ["attach_warehouse"]
