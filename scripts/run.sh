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
python -m pip install --quiet -e .
echo "━━━ bootstrap: complete ━━━"

echo
echo "━━━ pipeline: starting ━━━"
exec python scripts/run_pipeline.py "$@"
