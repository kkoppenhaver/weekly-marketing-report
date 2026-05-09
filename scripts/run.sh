#!/usr/bin/env bash
# Routine entrypoint. Bootstraps Python dependencies in the fresh container,
# then runs the weekly pipeline.
#
# Local dev shouldn't need this — running scripts/run_pipeline.py directly in
# an already-set-up venv works fine. This script exists for the Routine, where
# every run starts in a clean Python environment.

set -euo pipefail

cd "$(dirname "$0")/.."

echo "━━━ bootstrap: installing dependencies ━━━"
python -m pip install --quiet --upgrade pip
# Refresh certifi explicitly. Stale CA bundles cause
# "self-signed certificate in certificate chain" against Google APIs.
python -m pip install --quiet --upgrade certifi
python -m pip install --quiet -e .

# Point Python's SSL stack at certifi's bundle. Google's auth libraries
# don't always pick this up automatically.
CERT_PATH="$(python -c 'import certifi; print(certifi.where())')"
export SSL_CERT_FILE="$CERT_PATH"
export REQUESTS_CA_BUNDLE="$CERT_PATH"
echo "  using CA bundle: $CERT_PATH"

echo "━━━ bootstrap: complete ━━━"

echo
echo "━━━ env check ━━━"
python scripts/check_env.py
echo "━━━ env check: complete ━━━"

echo
echo "━━━ pipeline: starting ━━━"
exec python scripts/run_pipeline.py "$@"
