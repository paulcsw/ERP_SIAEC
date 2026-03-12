#!/bin/bash
set -e

# Detect ODBC driver dynamically and patch DATABASE_URL
ODBC_DRIVER=$(odbcinst -q -d 2>/dev/null | head -1 | tr -d '[]' || echo "ODBC Driver 18 for SQL Server")
echo "==> Detected ODBC driver: ${ODBC_DRIVER}"
export ODBC_DRIVER

# Replace driver= in DATABASE_URL with the detected driver (URL-encoded)
ODBC_DRIVER_ENCODED=$(echo "${ODBC_DRIVER}" | sed 's/ /+/g')
export DATABASE_URL=$(echo "${DATABASE_URL}" | sed "s/driver=[^&]*/driver=${ODBC_DRIVER_ENCODED}/")
echo "==> DATABASE_URL driver set to: ${ODBC_DRIVER}"

echo "==> Waiting for SQL Server to be ready..."
MAX_RETRIES=30
RETRY=0
until python -c "
import os, pyodbc
from urllib.parse import unquote, urlsplit

url = os.environ.get('DATABASE_URL', '')
parsed = urlsplit(url)
host = parsed.hostname or 'db'
port = parsed.port or 1433
username = unquote(parsed.username or 'sa')
password = unquote(parsed.password or '')
driver = '${ODBC_DRIVER}'
conn = pyodbc.connect(
    f'DRIVER={{{driver}}};SERVER={host},{port};UID={username};PWD={password};TrustServerCertificate=yes',
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
import os, pyodbc
from urllib.parse import unquote, urlsplit

url = os.environ.get('DATABASE_URL', '')
parsed = urlsplit(url)
host = parsed.hostname or 'db'
port = parsed.port or 1433
username = unquote(parsed.username or 'sa')
password = unquote(parsed.password or '')
driver = '${ODBC_DRIVER}'
conn = pyodbc.connect(
    f'DRIVER={{{driver}}};SERVER={host},{port};UID={username};PWD={password};TrustServerCertificate=yes',
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
alembic -c alembic/alembic.ini upgrade head || echo "==> WARNING: Alembic migration failed (may need manual intervention)"

# Seed dev data if script exists and has content
if [ -f "scripts/seed_data.py" ] && [ -s "scripts/seed_data.py" ]; then
    echo "==> Seeding development data..."
    python scripts/seed_data.py || echo "==> WARNING: Seed script failed"
fi

echo "==> Starting uvicorn..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
