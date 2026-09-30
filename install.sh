#!/usr/bin/env sh
# تثبيت نطاق للمتاجر | Nitaaq Store — wrapper around tools/install.py
set -e
cd "$(dirname "$0")"
command -v python3 >/dev/null 2>&1 || { echo "يلزم Python 3.9 أو أحدث"; exit 1; }
exec python3 tools/install.py "$@"
