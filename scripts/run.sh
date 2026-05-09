#!/usr/bin/env bash
# Routine entrypoint. Bootstraps Python dependencies in the fresh container,
# then runs the weekly pipeline.
#
# Local dev shouldn't need this — running scripts/run_pipeline.py directly in
# an already-set-up venv works fine. This script exists for the Routine, where
# every run starts in a clean Python environment.

set -euo pipefail

cd "$(dirname "$0")/.."

echo "━━━ bootstrap: creating venv + installing dependencies ━━━"
# Routine container ships with a Debian-managed system pip that refuses
# self-upgrades (PEP 668). Avoid that whole class of problem by creating
# a project-local venv on every run. The container is fresh each time
# so no caching is lost.
python3 -m venv .venv
source .venv/bin/activate

python -m pip install --quiet --upgrade pip
# Refresh certifi explicitly. Stale CA bundles cause
# "self-signed certificate in certificate chain" against Google APIs.
python -m pip install --quiet --upgrade certifi
python -m pip install --quiet -e .

# Build a combined CA bundle that trusts both:
#   1. Public CAs (from certifi)
#   2. The Routine container's system CAs (which include any proxy CA the
#      container's TLS-inspecting proxy has installed via update-ca-certificates)
#
# We append the system bundle to certifi's bundle in place. Every Python
# library that calls certifi.where() — httpx, requests, google-auth — picks
# up the combined trust set automatically. The venv is fresh each run, so
# this isn't persistent pollution.
CERT_PATH="$(python -c 'import certifi; print(certifi.where())')"
SYSTEM_BUNDLE=/etc/ssl/certs/ca-certificates.crt
if [ -f "$SYSTEM_BUNDLE" ]; then
  echo "  appending system CAs ($SYSTEM_BUNDLE) to certifi bundle"
  cat "$SYSTEM_BUNDLE" >> "$CERT_PATH"
else
  echo "  no system CA bundle found at $SYSTEM_BUNDLE; using certifi alone"
fi
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
