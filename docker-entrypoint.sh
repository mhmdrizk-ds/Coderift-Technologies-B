#!/bin/bash
# Coderift Technologies — Docker Entrypoint
# Starts both the MCP HTTP server and the Admin Platform API in parallel.

set -e

echo "=== Initializing database ==="
python db/init_db.py
python db/apply_migration.py

echo "=== Starting MCP HTTP Server on :8000 ==="
python -m mcp_server.server_http --host 0.0.0.0 --port 8000 &
MCP_PID=$!

echo "=== Starting Admin Platform API on :8001 ==="
python -m admin_platform.admin_tools_api --host 0.0.0.0 --port 8001 &
ADMIN_PID=$!

echo "=== Starting User Platform on :8010 ==="
python -m user_platform.backend --host 0.0.0.0 --port 8010 &
USER_PID=$!

echo "=== All services running ==="
wait $MCP_PID $ADMIN_PID $USER_PID
