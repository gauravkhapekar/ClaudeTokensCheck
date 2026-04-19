#!/usr/bin/env bash
# refresh_usage.sh — Mac/Linux
# Regenerates live_usage.js from your ~/.claude/projects/ session logs.
# Usage:  ./refresh_usage.sh [--range week|month|last30|quarter|year|alltime]
#
# Make executable once:  chmod +x refresh_usage.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RANGE="${1:---range}"
if [ "$RANGE" = "--range" ]; then
  RANGE_ARG=""
else
  RANGE_ARG="$1"
fi

# Use the first Python 3 found on PATH
PYTHON=$(command -v python3 || command -v python)
if [ -z "$PYTHON" ]; then
  echo "Error: python3 not found. Install Python 3.10+ and try again." >&2
  exit 1
fi

echo "Using: $PYTHON"
cd "$SCRIPT_DIR"

if [ -n "$RANGE_ARG" ]; then
  "$PYTHON" generate_usage_data.py "$RANGE_ARG"
else
  "$PYTHON" generate_usage_data.py
fi

echo ""
echo "Done. Open Token Usage.html in your browser and reload."
