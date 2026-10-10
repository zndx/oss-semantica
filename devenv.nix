{ pkgs, lib, config, inputs, ... }:

let
  # Upstream `semantica-server` defaults to :8000. This lab host already
  # holds that port (optillm), so the peer listens on :8070.
  # Lattice slot after hermes :50651. Signals ServerQuery PEERS publishes it
  # from the lab peer-contract as <advertise-host>:50751.
  httpPort = 8070;
  enginePort = 50751;
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

  packages = with pkgs; [
    git
    gh
    jq
    ripgrep
    grpcurl
    curl
    uv
  ];

  languages.python = {
    enable = true;
    package = pkgs.python312;
    uv.enable = true;
    uv.sync.enable = true;
    # signals-engine is the lattice extra (Python >=3.11). explorer brings
    # fastapi and uvicorn for semantica-server. The devenv venv is
    # .devenv/state/venv, not the project .venv.
    uv.sync.extras = [ "signals" "explorer" ];
    venv.enable = true;
  };

  # devenv 2.1 supervises with the native process manager. Readiness is
  # `ready`, and ordering is `after`, not process-compose probes.
  processes.semantica = {
    exec = ''
      export SEMANTICA_HOST="''${SEMANTICA_HOST:-0.0.0.0}"
      export SEMANTICA_PORT="''${SEMANTICA_PORT:-${toString httpPort}}"
      exec uv run --extra explorer semantica-server
    '';
    cwd = config.devenv.root;
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
    echo "semantica · http 0.0.0.0:${toString httpPort} · engine :${toString enginePort} · signals-engine v0.2.0"
    echo "  advertise: ''${SIGNALS_ADVERTISE_HOST:-tinybox.dev.vista.zndx.org}"
    echo "  lifecycle: devenv up -d | devenv processes down"
  '';
}
