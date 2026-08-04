#!/bin/bash
# App Service Linux startup - Python 3.12 / FastAPI / gunicorn
# Oryx extracts the compressed output to /tmp/<hash>/ and runs this script from there.
# We resolve APP_ROOT from BASH_SOURCE so paths work regardless of cwd.
set -e

APP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== AI Watchtower startup ==="
echo "APP_ROOT: $APP_ROOT"
echo "PWD: $(pwd)"
echo "PYTHON: $(which python) ($(python --version 2>&1))"
echo "APP_ROOT contents:"
ls -la "$APP_ROOT" 2>&1 | head -30 || true
echo "APP_ROOT/src contents:"
ls -la "$APP_ROOT/src" 2>&1 | head -20 || true
echo "APP_ROOT/src/watchtower contents:"
ls -la "$APP_ROOT/src/watchtower" 2>&1 | head -30 || true

# Persistent SQLite dir (bind-mounted on Linux App Service)
mkdir -p /home/data || true

export PYTHONPATH="$APP_ROOT/src:${PYTHONPATH:-}"
echo "PYTHONPATH: $PYTHONPATH"

# Sanity-check the import first - a failure here lands the traceback in stderr immediately
# instead of the opaque gunicorn "worker failed to boot" message.
echo "Test import..."
python -c "import sys; sys.path.insert(0, '$APP_ROOT/src'); from watchtower.main import app; print('  OK: app =', app)" 2>&1

echo "Starting gunicorn..."
exec gunicorn \
  --chdir "$APP_ROOT/src" \
  -w 2 -k uvicorn.workers.UvicornWorker \
  --bind=0.0.0.0:8000 \
  --timeout 180 \
  --access-logfile - \
  --error-logfile - \
  watchtower.main:app
