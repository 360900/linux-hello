#!/usr/bin/env bash
# Download the dlib model files pinned in tools/runtime-manifest.json,
# verify their SHA256 and unpack them into a directory, ready for
#   meson setup build -Ddlib_data_files=<dir>
#
# Usage: tools/fetch_dlib_data.sh [--outdir DIR]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFEST="${REPO_ROOT}/tools/runtime-manifest.json"
OUTDIR="${REPO_ROOT}/build-runtime/dlib-data"

while [ $# -gt 0 ]; do
    case "$1" in
        --outdir) OUTDIR="$2"; shift 2 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

fetch() {
    if command -v curl >/dev/null 2>&1; then
        curl -fL --retry 3 -o "$2" "$1"
    else
        wget -O "$2" "$1"
    fi
}

mkdir -p "$OUTDIR"
BASE_URL="$(python3 -c "import json;print(json.load(open('$MANIFEST'))['dlib_data']['url'])")"

python3 -c "
import json
for name, digest in json.load(open('$MANIFEST'))['dlib_data']['files'].items():
    print(name, digest)
" | while read -r ARCHIVE EXPECTED; do
    TARGET="${OUTDIR}/${ARCHIVE%.bz2}"
    if [ -f "$TARGET" ]; then
        continue
    fi
    if [ ! -f "${OUTDIR}/${ARCHIVE}" ]; then
        echo ">> downloading ${ARCHIVE}"
        fetch "${BASE_URL}${ARCHIVE}" "${OUTDIR}/${ARCHIVE}"
    fi
    ACTUAL="$(sha256sum "${OUTDIR}/${ARCHIVE}" | cut -d' ' -f1)"
    if [ "$ACTUAL" != "$EXPECTED" ]; then
        echo "!! sha256 mismatch for ${ARCHIVE}" >&2
        echo "   expected $EXPECTED" >&2
        echo "   actual   $ACTUAL" >&2
        rm -f "${OUTDIR}/${ARCHIVE}"
        exit 1
    fi
    bzip2 -d "${OUTDIR}/${ARCHIVE}"
done
echo ">> dlib data in ${OUTDIR}"
