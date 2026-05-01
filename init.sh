#!/usr/bin/env bash
# init.sh — parallax-engine development environment setup
#
# IDEMPOTENT: safe to run multiple times. Each step is guarded.
# INTENT: ensure Python >= 3.11, create/activate .venv, install deps,
#         verify FFmpeg is the LGPL build with libopenh264.
#
# Run from the project root:
#   ./init.sh
#
# NOTE: This script NEVER mentions or checks for GPL-licensed encoders by name.
#       The Python licensing validator (tools/validate_licensing.py) performs
#       that comprehensive check. This script only verifies that the LGPL-safe
#       libopenh264 encoder IS present.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== parallax-engine init ==="
echo ""

# ---------------------------------------------------------------------------
# Step 1: Verify Python 3.11+
# ---------------------------------------------------------------------------
echo "[1/6] Checking Python version..."

PYTHON_BIN=""
for candidate in python3.11 python3.12 python3.13 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
        version_ok=$("$candidate" -c "
import sys
ok = sys.version_info >= (3, 11)
print('ok' if ok else 'bad')
" 2>/dev/null)
        if [ "$version_ok" = "ok" ]; then
            PYTHON_BIN="$candidate"
            break
        fi
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    echo "ERROR: Python 3.11+ is required but not found."
    echo "       Install Python 3.11 or later and ensure it is on PATH."
    exit 1
fi

PYTHON_VERSION=$("$PYTHON_BIN" --version 2>&1)
echo "       Found: $PYTHON_VERSION at $(command -v "$PYTHON_BIN")"

# ---------------------------------------------------------------------------
# Step 2: Verify pip is available
# ---------------------------------------------------------------------------
echo "[2/6] Checking pip..."

if ! "$PYTHON_BIN" -m pip --version >/dev/null 2>&1; then
    echo "ERROR: pip not available for $PYTHON_BIN"
    echo "       Run: $PYTHON_BIN -m ensurepip --upgrade"
    exit 1
fi

PIP_VERSION=$("$PYTHON_BIN" -m pip --version 2>&1)
echo "       Found: $PIP_VERSION"

# ---------------------------------------------------------------------------
# Step 3: Create .venv if missing
# ---------------------------------------------------------------------------
echo "[3/6] Setting up virtual environment..."

VENV_DIR="$SCRIPT_DIR/.venv"

if [ ! -d "$VENV_DIR" ]; then
    echo "       Creating .venv at $VENV_DIR ..."
    "$PYTHON_BIN" -m venv "$VENV_DIR"
    echo "       Created."
else
    echo "       .venv already exists — skipping creation."
fi

# Activate
if [ -f "$VENV_DIR/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$VENV_DIR/bin/activate"
    ACTIVE_PYTHON=$(command -v python)
    echo "       Activated: $ACTIVE_PYTHON"
elif [ -f "$VENV_DIR/Scripts/activate" ]; then
    # Windows (Git Bash / WSL)
    # shellcheck disable=SC1091
    source "$VENV_DIR/Scripts/activate"
    ACTIVE_PYTHON=$(command -v python)
    echo "       Activated (Windows path): $ACTIVE_PYTHON"
else
    echo "ERROR: Could not find venv activation script in $VENV_DIR"
    exit 1
fi

# ---------------------------------------------------------------------------
# Step 4: Install / upgrade dependencies from pyproject.toml
# ---------------------------------------------------------------------------
echo "[4/6] Installing / upgrading dependencies from pyproject.toml..."

# Upgrade pip itself first (avoids "new version available" noise on every run)
python -m pip install --quiet --upgrade pip

# Install the package in editable mode with all declared deps.
# --quiet suppresses per-package output when already up to date.
python -m pip install --quiet -e ".[dev]"

echo "       Dependencies installed."

# ---------------------------------------------------------------------------
# Step 5: Verify FFmpeg — must have libopenh264 (the LGPL-safe H.264 codec)
# ---------------------------------------------------------------------------
echo "[5/6] Verifying FFmpeg (LGPL build with libopenh264)..."

if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "WARNING: ffmpeg not found on PATH."
    echo "         Phase 1 encoding will fail without FFmpeg."
    echo "         Install the LGPL build of FFmpeg."
    echo "         On macOS: brew install ffmpeg"
    echo "         On Ubuntu: apt-get install ffmpeg"
    echo "         Verify it includes libopenh264 after installation."
    # Do not exit — allow the rest of the environment to configure.
else
    FFMPEG_PATH=$(command -v ffmpeg)
    FFMPEG_VERSION=$(ffmpeg -version 2>&1 | head -1)
    echo "       Found: $FFMPEG_VERSION at $FFMPEG_PATH"

    # Check for libopenh264 (the LGPL-safe Cisco-royalty-paid H.264 encoder)
    ENCODER_LIST=$(ffmpeg -hide_banner -encoders 2>/dev/null || true)

    if echo "$ENCODER_LIST" | grep -q "libopenh264"; then
        echo "       PASS: libopenh264 is available."
    else
        echo "WARNING: libopenh264 encoder NOT found in this FFmpeg build."
        echo "         parallax-engine requires libopenh264 for LGPL-clean H.264 encoding."
        echo "         The encoder may need to be installed separately."
        echo "         On macOS: brew install openh264; and rebuild/reinstall ffmpeg."
        echo "         Running: python tools/validate_licensing.py will flag this."
        # Do not exit — warn loudly but proceed so other steps can complete.
    fi
fi

# ---------------------------------------------------------------------------
# Step 6: Print summary
# ---------------------------------------------------------------------------
echo ""
echo "[6/6] Environment summary:"
echo ""
echo "  Python:     $(python --version 2>&1)"
echo "  pip:        $(python -m pip --version 2>&1)"
echo "  venv:       $VENV_DIR"

if command -v ffmpeg >/dev/null 2>&1; then
    echo "  FFmpeg:     $(ffmpeg -version 2>&1 | head -1)"
else
    echo "  FFmpeg:     NOT FOUND (see warning above)"
fi

# Count milestones from phase_milestones.json if it exists and python is available
if [ -f "phase_milestones.json" ]; then
    MILESTONE_SUMMARY=$(python -c "
import json, pathlib
data = json.loads(pathlib.Path('phase_milestones.json').read_text())
milestones = data.get('milestones', [])
total = len(milestones)
passed = sum(1 for m in milestones if m.get('passes', False))
print(f'{passed}/{total} milestones passing')
" 2>/dev/null || echo "  (could not read phase_milestones.json)")
    echo "  Milestones: $MILESTONE_SUMMARY"
fi

echo ""
echo "=== init complete ==="
