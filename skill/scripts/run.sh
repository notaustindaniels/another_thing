#!/usr/bin/env bash
set -euo pipefail
WORKSPACE="${1:-./workspace}"
shift || true
python -m parallax_engine.cli --workspace "$WORKSPACE" "$@"
