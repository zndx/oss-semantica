#!/usr/bin/env bash
# Register the Signals-built impala_fdw on this peer's Postgres.
# The .so stays in weathership/signals. Semantica does not vendor it.
# Impala, Kudu, and RustFS stay Signals services. This Postgres is a client.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SIGNALS_ROOT="${SIGNALS_ROOT:-$HOME/local/src/wxs/signals}"
SO="${IMPALA_FDW_SO:-$SIGNALS_ROOT/components/impala_fdw/impala_fdw.so}"
if [[ ! -f "$SO" ]]; then
  SO="$SIGNALS_ROOT/.devenv/pg-ext/lib/impala_fdw.so"
fi
if [[ ! -f "$SO" ]]; then
  echo "impala_fdw.so not found under $SIGNALS_ROOT."
  echo "Build it in the Signals tree: devenv tasks run impala-fdw:build"
  exit 1
fi

EXT_DIR="${SEMANTICA_PG_EXT_DIR:-$ROOT/.devenv/pg-ext}"
mkdir -p "$EXT_DIR/lib"
tmp="$EXT_DIR/lib/impala_fdw.so.new"
cp -f "$SO" "$tmp"
mv -f "$tmp" "$EXT_DIR/lib/impala_fdw.so"
FDW_SO="$EXT_DIR/lib/impala_fdw.so"

KEYTAB="${IMPALA_FDW_KEYTAB:-$SIGNALS_ROOT/.devenv/kdc/signals.keytab}"
if [[ ! -f "$KEYTAB" ]]; then
  echo "Kerberos keytab missing: $KEYTAB"
  exit 1
fi
HS2_HOST="${IMPALA_HS2_HOST:-tinybox.dev.vista.zndx.org}"
KUDU_MASTERS="${KUDU_MASTERS:-tinybox.dev.vista.zndx.org:7051}"
PGHOST="${PGHOST:-127.0.0.1}"
PGPORT="${PGPORT:-5588}"
PGDATABASE="${PGDATABASE:-semantica}"
export KRB5_CONFIG="${KRB5_CONFIG:-$SIGNALS_ROOT/.devenv/kdc/krb5.conf}"

for _ in $(seq 1 30); do
  if pg_isready -h "$PGHOST" -p "$PGPORT" -d "$PGDATABASE" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
pg_isready -h "$PGHOST" -p "$PGPORT" -d "$PGDATABASE"

PSQL=(psql -h "$PGHOST" -p "$PGPORT" -d "$PGDATABASE" -v ON_ERROR_STOP=1)

"${PSQL[@]}" -f "$ROOT/scripts/warehouse/admin.sql"

sed \
  -e "s|__FDW_SO__|$FDW_SO|g" \
  -e "s|__KEYTAB__|$KEYTAB|g" \
  -e "s|__HS2_HOST__|$HS2_HOST|g" \
  -e "s|__KUDU_MASTERS__|$KUDU_MASTERS|g" \
  "$ROOT/scripts/warehouse/fdw.sql" | "${PSQL[@]}"

# impala_fdw_exec refuses CREATE TABLE (#SL.00000029.RANGEDDL). Kudu tables
# are created by the Signals C++ client. kudu.sql is the shape contract.
CREATE="${SIGNALS_KUDU_CREATE:-$SIGNALS_ROOT/.devenv/bin/signals_kudu_create}"
if [[ ! -x "$CREATE" ]]; then
  echo "signals_kudu_create is not built: $CREATE"
  echo "  From the Signals tree: devenv shell -- scripts/build-kudu-tools.sh signals_kudu_create"
  echo "  Guru: #SL.00000027.SCHEMA2"
  exit 1
fi
export KUDU_MASTERS="${KUDU_MASTERS:-tinybox.dev.vista.zndx.org:7051}"
export KRB5CCNAME="${KRB5CCNAME:-$ROOT/.devenv/krb5cc}"
if ! kinit -kt "$KEYTAB" signals@DEV.VISTA.ZNDX.ORG; then
  echo "kinit failed for signals@DEV.VISTA.ZNDX.ORG"
  echo "  Guru: #SL.00000028.HS2GSSAPI"
  exit 1
fi
for table in semantica_node_tier0 semantica_edge_tier0 semantica_provenance_tier0 semantica_chunk_tier0; do
  "$CREATE" "$table"
done

# kudu_scan talks to the master directly. This row is what makes the table
# visible to Impala's HMS-free catalog on the Signals database.
CATALOG_PORT="${SIGNALS_CATALOG_PORT:-5455}"
psql -h 127.0.0.1 -p "$CATALOG_PORT" -d signals_catalog -v ON_ERROR_STOP=1 \
  -f "$ROOT/scripts/warehouse/catalog.sql"

"${PSQL[@]}" -f "$ROOT/scripts/warehouse/foreign.sql"
echo "impala_fdw on ${PGDATABASE}:${PGPORT} → HS2 ${HS2_HOST}:21050 (Signals FDW, not vendored)"
echo "Smoke: psql -h $PGHOST -p $PGPORT -d $PGDATABASE -c 'SELECT count(*) FROM warehouse.v_node'"
