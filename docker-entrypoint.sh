#!/bin/bash
set -e

echo "==> Waiting for SQL Server to be ready..."
MAX_RETRIES=30
RETRY=0
until python -c "
import pyodbc, os, urllib.parse
url = os.environ.get('DATABASE_URL', '')
# Parse host:port from DATABASE_URL
parts = url.split('@')[1].split('/')[0] if '@' in url else 'db:1433'
host = parts.split(':')[0]
port = parts.split(':')[1] if ':' in parts else '1433'
conn = pyodbc.connect(
    f'DRIVER={{ODBC Driver 18 for SQL Server}};SERVER={host},{port};UID=sa;PWD=DevPass@12345;TrustServerCertificate=yes',
    timeout=3
)
conn.close()
" 2>/dev/null; do
    RETRY=$((RETRY + 1))
    if [ "$RETRY" -ge "$MAX_RETRIES" ]; then
        echo "==> ERROR: SQL Server not ready after ${MAX_RETRIES}s, starting anyway..."
        break
    fi
    echo "==> SQL Server not ready (${RETRY}/${MAX_RETRIES}), retrying..."
    sleep 1
done
echo "==> SQL Server is ready!"

# Create database if it doesn't exist
echo "==> Ensuring database 'cis_erp' exists..."
python -c "
import pyodbc, os
url = os.environ.get('DATABASE_URL', '')
parts = url.split('@')[1].split('/')[0] if '@' in url else 'db:1433'
host = parts.split(':')[0]
port = parts.split(':')[1] if ':' in parts else '1433'
conn = pyodbc.connect(
    f'DRIVER={{ODBC Driver 18 for SQL Server}};SERVER={host},{port};UID=sa;PWD=DevPass@12345;TrustServerCertificate=yes',
    autocommit=True
)
cursor = conn.cursor()
cursor.execute(\"IF DB_ID('cis_erp') IS NULL CREATE DATABASE cis_erp\")
conn.close()
print('==> Database ready')
" || echo "==> WARNING: Could not create database (may already exist)"

# Run Alembic migrations
echo "==> Running Alembic migrations..."
cd /app
alembic upgrade head || echo "==> WARNING: Alembic migration failed (may need manual intervention)"

# Seed dev data if script exists and has content
if [ -f "scripts/seed_data.py" ] && [ -s "scripts/seed_data.py" ]; then
    echo "==> Seeding development data..."
    python scripts/seed_data.py || echo "==> WARNING: Seed script failed"
fi

echo "==> Starting uvicorn..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
