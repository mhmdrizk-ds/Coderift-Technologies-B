
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

# Initialize the database (original schema + seed)
RUN python db/init_db.py
RUN python db/apply_migration.py

# Expose both service ports
EXPOSE 8000 8001

# Start both services
COPY docker-entrypoint.sh .
RUN chmod +x docker-entrypoint.sh

CMD ["./docker-entrypoint.sh"]
