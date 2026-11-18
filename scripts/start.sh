#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."

if ! command -v npm >/dev/null 2>&1; then
    printf '%s\n' 'Install Node.js with npm before starting RideCast.' >&2
    exit 1
fi

if [ ! -x .venv/bin/python ]; then
    if command -v python3.12 >/dev/null 2>&1; then
        python=python3.12
    elif command -v python3.11 >/dev/null 2>&1; then
        python=python3.11
    else
        python=python3
    fi
    if ! "$python" -c 'import sys; raise SystemExit(sys.version_info[:2] not in ((3, 11), (3, 12)))'; then
        printf '%s\n' 'Install Python 3.11 or 3.12 before starting RideCast.' >&2
        exit 1
    fi
    "$python" -m venv .venv
fi

if [ ! -x .venv/bin/ridecast ] || ! cmp -s pyproject.toml .venv/ridecast-pyproject.toml; then
    .venv/bin/python -m pip install -e '.[research]'
    cp pyproject.toml .venv/ridecast-pyproject.toml
fi

if [ ! -x web/node_modules/.bin/vite ] || ! cmp -s web/package-lock.json web/node_modules/.ridecast-package-lock.json; then
    npm --prefix web ci
    cp web/package-lock.json web/node_modules/.ridecast-package-lock.json
fi

npm --prefix web run build
exec .venv/bin/ridecast present "$@"
