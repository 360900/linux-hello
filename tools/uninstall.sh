#!/usr/bin/env bash
# Remove a source install of Linux Hello. Undoes every PAM change first,
# while the CLI still exists, then deletes what the builds installed.
#
# Usage: sudo tools/uninstall.sh [--purge] [MESON_BUILD_DIR] [KCM_BUILD_DIR]
#   --purge          also delete face models, settings and logs
#   MESON_BUILD_DIR  default: build
#   KCM_BUILD_DIR    default: build-kcm
set -euo pipefail

PURGE=()
DIRS=()
for ARG in "$@"; do
    case "$ARG" in
        --purge) PURGE=(--purge) ;;
        -*) echo "unknown option: $ARG" >&2; exit 2 ;;
        *) DIRS+=("$ARG") ;;
    esac
done
MESON_DIR="${DIRS[0]:-build}"
KCM_DIR="${DIRS[1]:-build-kcm}"

if [ "$(id -u)" != 0 ]; then
    echo "Run as root: sudo $0 $*" >&2
    exit 1
fi
if [ ! -f "${MESON_DIR}/meson-private/install.dat" ]; then
    echo "${MESON_DIR} is not an installed meson build directory" >&2
    exit 1
fi

if command -v linux-hello-cli >/dev/null 2>&1; then
    linux-hello-cli reset-system "${PURGE[@]}"
fi
systemctl disable --now linux-hello-webauthn.service 2>/dev/null || true
systemctl stop linux-hello-daemon.service 2>/dev/null || true

ninja -C "$MESON_DIR" uninstall
if [ -f "${KCM_DIR}/CMakeCache.txt" ]; then
    cmake --build "$KCM_DIR" --target uninstall
fi
systemctl daemon-reload
echo "Linux Hello is uninstalled."
