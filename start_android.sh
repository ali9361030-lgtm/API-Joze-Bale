#!/data/data/com.termux/files/usr/bin/bash
# Termux: bash start_android.sh [port] [--no-browser]
set -eu
cd -- "$(dirname -- "$0")"
if ! command -v python >/dev/null 2>&1; then
    if ! command -v pkg >/dev/null 2>&1; then
        echo "Python 3.10+ is required. Install it, then rerun this script." >&2
        exit 1
    fi
    pkg install -y python
fi
if ! python -c 'import sys; assert sys.version_info >= (3, 10)' 2>/dev/null; then
    echo "Python 3.10+ is required." >&2
    exit 1
fi
if ! python -c 'import requests, docx' 2>/dev/null; then
    # lxml (python-docx dependency) needs native libraries when no Android wheel exists.
    if command -v pkg >/dev/null 2>&1; then
        pkg install -y clang libxml2 libxslt
    fi
    python -m pip install -r requirements.txt
fi
exec python run.py "$@"
