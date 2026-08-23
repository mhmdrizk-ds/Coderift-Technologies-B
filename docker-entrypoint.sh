#!/bin/bash
# Coderift Technologies — Docker Entrypoint
# Starts the MCP HTTP server, Admin Platform API, and User Platform in parallel.
# Used as the image's default CMD for standalone `docker run`.
# Under docker-compose, each service overrides this with its own `command:`
# (see docker-compose.yml) — this script is not what runs in that path.

set -e

echo "=== Initializing database ==="
python db/init_db.py
python db/apply_migration.py

echo "=== Starting MCP HTTP Server on :8000 ==="
python -m mcp_server.server_http --host 0.0.0.0 --port 8000 &
MCP_PID=$!

echo "=== Starting Admin Platform API on :8001 ==="
python -m admin_platform.admin_tools_api &
ADMIN_PID=$!

echo "=== Starting User Platform on :8010 ==="
python -m user_platform.backend --host 0.0.0.0 --port 8010 &
USER_PID=$!

echo "=== All services running ==="
wait $MCP_PID $ADMIN_PID $USER_PID