#!/usr/bin/env bash
# Sets up or updates the bot's Python environment in .venv. Safe to run again after pulling.
# Piper voices are installed when PIPER_MODEL_EN or PIPER_MODEL_JP is set in .env.
set -euo pipefail
cd "$(dirname "$0")"

PYTHON_VERSION=3.13
VENV=.venv

# Use the installed python$PYTHON_VERSION if there is one, otherwise let uv download it
create_venv() {
    if command -v "python$PYTHON_VERSION" >/dev/null; then
        echo "Creating $VENV with $(command -v "python$PYTHON_VERSION")"
        "python$PYTHON_VERSION" -m venv "$VENV"
        return
    fi
    if ! command -v uv >/dev/null && [ ! -x "$HOME/.local/bin/uv" ]; then
        echo "python$PYTHON_VERSION isn't installed. Installing uv to download it (no sudo needed)."
        curl -LsSf https://astral.sh/uv/install.sh | sh
    fi
    local uv
    uv=$(command -v uv || echo "$HOME/.local/bin/uv")
    echo "Creating $VENV with Python $PYTHON_VERSION from uv"
    # --seed includes pip, so .venv/bin/python -m pip works like a regular venv
    "$uv" venv --seed -p "$PYTHON_VERSION" "$VENV"
}

# Recreate the venv if it's missing, broken, or on a different Python version
if [ ! -x "$VENV/bin/python" ] || ! "$VENV/bin/python" -c "import sys; sys.exit(sys.version_info[:2] != tuple(map(int, '$PYTHON_VERSION'.split('.'))))" 2>/dev/null; then
    rm -rf "$VENV"
    create_venv
fi

PY="$VENV/bin/python"
echo "Using $("$PY" --version)"

git submodule update --init
"$PY" -m pip install -q --upgrade pip
"$PY" -m pip install -q -r requirements.txt

env_set() {
    [ -f .env ] && grep -Eq "^\s*$1\s*=\s*['\"]?[^'\"[:space:]]" .env
}

missing=()
if env_set PIPER_MODEL_EN || env_set PIPER_MODEL_JP; then
    echo "Installing piper-tts"
    "$PY" -m pip install -q piper-tts
fi
if env_set PIPER_MODEL_JP; then
    if command -v cmake >/dev/null && command -v g++ >/dev/null; then
        echo "Installing pyopenjtalk for Japanese voices (compiles from source, this takes a while)"
        "$PY" -m pip install -q pyopenjtalk
    else
        missing+=(cmake build-essential)
        echo "Skipping pyopenjtalk: building it needs cmake and a C++ compiler."
    fi
fi

# System packages for voice announcements, which this script doesn't install since they need sudo
command -v ffmpeg >/dev/null || missing+=(ffmpeg)
# grep -c reads all of ldconfig's output; grep -q can stop early and fail the pipeline under pipefail
[ "$(ldconfig -p 2>/dev/null | grep -c libopus)" -gt 0 ] || missing+=(libopus0)
command -v espeak-ng >/dev/null || missing+=(espeak-ng)

if [ ${#missing[@]} -gt 0 ]; then
    echo
    echo "Voice announcements need system packages that aren't installed. To install them, run:"
    echo "    sudo apt install ${missing[*]}"
    if [[ " ${missing[*]} " == *" cmake "* ]]; then
        echo "and then run this script again for pyopenjtalk."
    fi
fi

echo
echo "Done. Start the bot with: $PY scheduler.py"
