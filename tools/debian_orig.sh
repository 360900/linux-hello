#!/usr/bin/env bash
# Create the orig tarballs for the Debian source package: the source tree,
# and a "runtime" component with every download pinned in
# tools/runtime-manifest.json (for amd64 and arm64), so the package builds
# without network access.
#
# Usage: tools/debian_orig.sh [OUTDIR]   (default: the parent directory)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTDIR="$(cd "${1:-${REPO_ROOT}/..}" && pwd)"
VERSION="$(cat "${REPO_ROOT}/VERSION")"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

git -C "$REPO_ROOT" archive --prefix="linux-hello-${VERSION}/" HEAD |
    gzip -n > "${OUTDIR}/linux-hello_${VERSION}.orig.tar.gz"

mkdir "${WORK}/runtime"
for ARCH in x86_64 aarch64; do
    python3 "${REPO_ROOT}/tools/manifest.py" "$ARCH" | while read -r FILENAME URL EXPECTED; do
        TARGET="${WORK}/runtime/${FILENAME}"
        [ -f "$TARGET" ] || curl -fL --retry 3 -o "$TARGET" "$URL"
        echo "${EXPECTED}  ${TARGET}" | sha256sum -c --quiet -
    done
done
tar -C "$WORK" --sort=name --owner=0 --group=0 --mtime=@0 -cf - runtime |
    gzip -n > "${OUTDIR}/linux-hello_${VERSION}.orig-runtime.tar.gz"
echo "${OUTDIR}/linux-hello_${VERSION}.orig.tar.gz"
echo "${OUTDIR}/linux-hello_${VERSION}.orig-runtime.tar.gz"
