{ pkgs, lib, config, inputs, ... }:

let
  # Upstream `semantica-server` defaults to :8000. This lab host already
  # holds that port (optillm), so the peer listens on :8070.
  # Lattice slot after hermes :50651. Signals ServerQuery PEERS publishes it
  # from the lab peer-contract as <advertise-host>:50751.
  # Postgres :5588 is this peer's admin database. Heavy tables are Kudu
  # tier0 in the Signals warehouse, via the Signals-built impala_fdw.
  httpPort = 8070;
  enginePort = 50751;
  pgPort = 5588;
  signalsRoot = "${builtins.getEnv "HOME"}/local/src/wxs/signals";
in
{
  env.SEMANTICA_HOST = "0.0.0.0";
  env.SEMANTICA_PORT = toString httpPort;
  # Loopback URLs. The engine rewrites them onto SIGNALS_ADVERTISE_HOST
  # (signals-protocol surfaces.md). Binding the product on 0.0.0.0 makes
  # that rewritten URL reachable.
  env.SEMANTICA_PRIMARY_UI = "http://127.0.0.1:${toString httpPort}/";
  env.SEMANTICA_HEALTH_URL = "http://127.0.0.1:${toString httpPort}/health";
  env.SIGNALS_ENGINE_PORT = toString enginePort;
  # 0.0.0.0 leaves advertise_host empty so the library resolver publishes
  # SIGNALS_ADVERTISE_HOST. A loopback bind would force 127.0.0.1.
  env.SIGNALS_ENGINE_BIND = "0.0.0.0";
  env.SIGNALS_ENGINE_ANNOUNCE = "1";

  # This Postgres is an FDW client. KRB5_CONFIG is what the postmaster's
  # backends use for kudu_scan kinit. The .so is the Signals build.
  env.SIGNALS_ROOT = signalsRoot;
  env.KRB5_CONFIG = "${signalsRoot}/.devenv/kdc/krb5.conf";
  env.LD_LIBRARY_PATH = lib.makeLibraryPath [
    pkgs.krb5.lib
    pkgs.openssl.out
  ] + ":${signalsRoot}/.devenv/impala/lib";
  env.SEMANTICA_DATABASE_URL = "postgresql://127.0.0.1:${toString pgPort}/semantica";

  services.postgres = {
    enable = true;
    package = pkgs.postgresql_16;
    port = pgPort;
    listen_addresses = "127.0.0.1";
    initialDatabases = [ { name = "semantica"; } ];
  };

  packages = with pkgs; [
    git
    gh
    jq
    krb5
    ripgrep
    grpcurl
    curl
    uv
    postgresql_16
  ];

  languages.python = {
    enable = true;
    package = pkgs.python312;
    uv.enable = true;
    uv.sync.enable = true;
    # signals-engine is the lattice extra (Python >=3.11). explorer brings
    # fastapi and uvicorn for semantica-server. The devenv venv is
    # .devenv/state/venv, not the project .venv.
    uv.sync.extras = [ "signals" "explorer" "warehouse" ];
    venv.enable = true;
  };

  # devenv 2.1 supervises with the native process manager. Readiness is
  # `ready`, and ordering is `after`, not process-compose probes.
  # One-shot: install the Signals FDW, create admin tables, create the Kudu
  # tier0 tables with signals_kudu_create, then the foreign tables.
  processes.warehouse = {
    exec = ''
      set -euo pipefail
      export SIGNALS_ROOT="''${SIGNALS_ROOT:-$HOME/local/src/wxs/signals}"
      export KRB5_CONFIG="''${KRB5_CONFIG:-$SIGNALS_ROOT/.devenv/kdc/krb5.conf}"
      export PGHOST="''${PGHOST:-127.0.0.1}"
      export PGPORT="''${PGPORT:-${toString pgPort}}"
      export PGDATABASE="''${PGDATABASE:-semantica}"
      exec ${config.devenv.root}/scripts/warehouse/install_impala_fdw.sh
    '';
    cwd = config.devenv.root;
    after = [ "devenv:processes:postgres" ];
    restart.on = "never";
  };

  processes.semantica = {
    exec = ''
      export SEMANTICA_HOST="''${SEMANTICA_HOST:-0.0.0.0}"
      export SEMANTICA_PORT="''${SEMANTICA_PORT:-${toString httpPort}}"
      export SEMANTICA_DATABASE_URL="''${SEMANTICA_DATABASE_URL:-postgresql://''${PGHOST:-127.0.0.1}:''${PGPORT:-${toString pgPort}}/semantica}"
      psql "$SEMANTICA_DATABASE_URL" -v ON_ERROR_STOP=1 -c "SELECT 1 FROM warehouse.v_node LIMIT 0"
      exec uv run --extra explorer --extra warehouse semantica-server
    '';
    cwd = config.devenv.root;
    after = [ "devenv:processes:warehouse@completed" ];
    restart.on = "on_failure";
    ready = {
      http.get = {
        host = "127.0.0.1";
        port = httpPort;
        path = "/health";
      };
      initial_delay = 2;
      period = 5;
      probe_timeout = 4;
      failure_threshold = 36;
      timeout = 180;
    };
  };

  # Lattice peer. Starts after /health answers so the first announce names a
  # surface that is actually up. Not a directory: Announce stays on Aegir,
  # and the waffle reads this port from the peer-contract via Signals.
  processes.engine = {
    exec = ''
      export SIGNALS_ADVERTISE_HOST="''${SIGNALS_ADVERTISE_HOST:-''${SIGNALS_LATTICE_HOST:-tinybox.dev.vista.zndx.org}}"
      export SIGNALS_ENGINE_BIND="''${SIGNALS_ENGINE_BIND:-0.0.0.0}"
      export SIGNALS_ENGINE_PORT="''${SIGNALS_ENGINE_PORT:-${toString enginePort}}"
      export SEMANTICA_PRIMARY_UI="''${SEMANTICA_PRIMARY_UI:-http://127.0.0.1:${toString httpPort}/}"
      export SEMANTICA_HEALTH_URL="''${SEMANTICA_HEALTH_URL:-http://127.0.0.1:${toString httpPort}/health}"
      export SIGNALS_ENGINE_ANNOUNCE="''${SIGNALS_ENGINE_ANNOUNCE:-1}"
      unset SIGNALS_ENGINE_DIRECTORY || true
      exec uv run --extra signals semantica-signals
    '';
    cwd = config.devenv.root;
    after = [ "devenv:processes:semantica" ];
    restart.on = "on_failure";
    ready = {
      exec = "grpcurl -plaintext -max-time 2 127.0.0.1:${toString enginePort} zndx.engine.v1.Engine/Status";
      initial_delay = 1;
      period = 5;
      probe_timeout = 4;
      failure_threshold = 12;
      timeout = 90;
    };
  };

  enterShell = ''
    echo "semantica · http 0.0.0.0:${toString httpPort} · engine :${toString enginePort} · postgres :${toString pgPort} · signals-engine v0.2.0"
    echo "  warehouse: Kudu tier0 via Signals impala_fdw (Iceberg is the settle tier)"
    echo "  advertise: ''${SIGNALS_ADVERTISE_HOST:-tinybox.dev.vista.zndx.org}"
    echo "  lifecycle: devenv up -d | devenv processes down"
  '';
}
