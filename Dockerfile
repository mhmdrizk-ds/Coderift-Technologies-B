
# What this builds:
#   - Python 3.11 slim image
#   - Installs all dependencies from requirements.txt
#   - Copies the entire repo
#   - Initializes the database (schema + seed)
#   - Exposes ports for MCP HTTP server (8000) and Admin API (8001)
#   - Default CMD starts both services via entrypoint script

FROM python:3.11-slim

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends     gcc     sqlite3     curl     && rm -rf /var/lib/apt/lists/*

# Copy requirements first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy entire project
COPY . .

# Build-time DB init (schema + seed only, NOT the state-graph/admin
# migrations). This gives `docker run` on this image alone a working DB
# out of the box. It does NOT cover docker-compose usage: docker-compose.yml
# mounts db_data as a volume over /app/db, which shadows this build-time
# file at container start — that's why compose has its own one-shot
# db-init service that runs init_db.py + apply_migration.py against the
# actual shared volume before any other service starts. See
# docker-compose.yml's comments for why that had to be a separate step
# rather than baked in here.
RUN python db/init_db.py

# Expose all three service ports (MCP HTTP, Admin Platform, User Platform)
EXPOSE 8000 8001 8010

# Default CMD for standalone `docker run` (not docker-compose, which
# overrides this per-service — see docker-compose.yml).
COPY docker-entrypoint.sh .
RUN chmod +x docker-entrypoint.sh

CMD ["./docker-entrypoint.sh"]
