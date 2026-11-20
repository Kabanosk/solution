#!/bin/sh
# Dispatcher for the backend image: `cfo <command> [args]`.
set -e

usage() {
    cat <<USAGE
usage: cfo <command> [args...]
  api                     FastAPI on 0.0.0.0:\${API_PORT:-8000}   (default)
  mcp <company-db|workstations|hr> [port]
                          one MCP server over streamable HTTP (ports 8001/8002/8003)
  worker                  agent worker: simulator + agent + notifier (python -m cfo.run)
  smoke                   cluster smoke check: LLM tool calling, MCP servers, DB seed, MLflow (python -m cfo.smoke)
  migrate [args]          apply the DB schema (python -m cfo.db.migrate)
  generate [args]         generate synthetic data (python -m cfo.data.generate)
  load [args]             load generated data into Postgres (python -m cfo.data.load)
  <anything else>         executed as is (e.g. \`cfo python -m ...\`, \`cfo sh\`)
USAGE
}

cmd="${1:-api}"
[ "$#" -gt 0 ] && shift

case "$cmd" in
    api)
        exec python -m uvicorn cfo.api.app:app --host 0.0.0.0 --port "${API_PORT:-8000}" "$@"
        ;;
    mcp)
        server="${1:?usage: cfo mcp <company-db|workstations|hr> [port]}"
        shift
        case "$server" in
            company-db) port=8001 ;;
            workstations) port=8002 ;;
            hr) port=8003 ;;
            *) echo "unknown MCP server: $server" >&2; exit 2 ;;
        esac
        [ "$#" -gt 0 ] && { port="$1"; shift; }
        exec python -m cfo.mcp_servers "$server" --transport streamable-http --host 0.0.0.0 --port "$port" "$@"
        ;;
    worker)
        exec python -m cfo.run "$@"
        ;;
    smoke)
        exec python -m cfo.smoke "$@"
        ;;
    migrate)
        exec python -m cfo.db.migrate "$@"
        ;;
    generate)
        exec python -m cfo.data.generate "$@"
        ;;
    load)
        exec python -m cfo.data.load "$@"
        ;;
    help|-h|--help)
        usage
        ;;
    *)
        exec "$cmd" "$@"
        ;;
esac
