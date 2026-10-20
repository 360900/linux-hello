#!/usr/bin/env bash
# Build the bundled Linux Hello Python runtime.
#
# Downloads a pinned standalone CPython plus pinned wheels from
# tools/runtime-manifest.json, verifies every artifact's SHA256, builds dlib
# from source against the bundled interpreter, and assembles a self-contained
# runtime tree that ships inside the linux-hello package:
#
#   <out>/python/                      standalone CPython (bin/python3, stdlib)
#   <out>/python/lib/python3.12/site-packages/  bundled dependencies
#
# The resulting interpreter is referenced as `python_path` by the meson build,
# so PAM, wrappers and the GUI all use it; the target machine needs nothing
# from its own package repositories.
#
# Usage: tools/build_runtime.sh [--outdir DIR] [--arch x86_64|aarch64] [--no-dlib]
#                               [--download-dir DIR] [--offline]
# Requires: curl (or wget), tar, python3, and cmake + ninja (downloaded as
# wheels when missing). --offline never downloads: every file listed by
# `tools/manifest.py <arch> runtime` and `... build` must already be in the
# download directory, as in distro package builds.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFEST="${REPO_ROOT}/tools/runtime-manifest.json"
OUTDIR="${REPO_ROOT}/build-runtime"
ARCH="$(uname -m)"
BUILD_DLIB=1
OFFLINE=0
DLDIR=""
JOBS="$(nproc 2>/dev/null || echo 4)"

while [ $# -gt 0 ]; do
    case "$1" in
        --outdir) OUTDIR="$2"; shift 2 ;;
        --arch) ARCH="$2"; shift 2 ;;
        --no-dlib) BUILD_DLIB=0; shift ;;
        --download-dir) DLDIR="$2"; shift 2 ;;
        --offline) OFFLINE=1; shift ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

case "$ARCH" in
    x86_64 | aarch64) ;;
    *) echo "unsupported arch: $ARCH (use x86_64 or aarch64)" >&2; exit 2 ;;
esac

PYTHON_VER="$(python3 -c "import json;print(json.load(open('$MANIFEST'))['python']['version'])")"

RUNTIME="${OUTDIR}/linux-hello-runtime"
DLDIR="${DLDIR:-${OUTDIR}/dl}"
mkdir -p "$DLDIR"

# get <filename> <url> <sha256>: download unless present, then verify
get() {
    if [ ! -f "${DLDIR}/$1" ]; then
        if [ "$OFFLINE" = 1 ]; then
            echo "!! $1 is missing from ${DLDIR} (offline)" >&2
            exit 1
        fi
        echo ">> downloading $1"
        if command -v curl >/dev/null 2>&1; then
            curl -fL --retry 3 -o "${DLDIR}/$1" "$2"
        else
            wget -O "${DLDIR}/$1" "$2"
        fi
    fi
    ACTUAL="$(sha256sum "${DLDIR}/$1" | cut -d' ' -f1)"
    if [ "$ACTUAL" != "$3" ]; then
        echo "!! sha256 mismatch for $1" >&2
        echo "   expected $3" >&2
        echo "   actual   $ACTUAL" >&2
        exit 1
    fi
}

# ---------------------------------------------------------------------------
# 1. Downloads: CPython, wheels, dlib sources, build-only wheels
# ---------------------------------------------------------------------------
PY_TARBALL=""
DLIB_SDIST=""
WHEELS=()
BUILD_WHEELS=()
while read -r FILENAME URL EXPECTED; do
    get "$FILENAME" "$URL" "$EXPECTED"
    case "$FILENAME" in
        cpython-*) PY_TARBALL="$FILENAME" ;;
        dlib-*.tar.gz) DLIB_SDIST="$FILENAME" ;;
        *) WHEELS+=("${DLDIR}/${FILENAME}") ;;
    esac
done < <(python3 "${REPO_ROOT}/tools/manifest.py" "$ARCH" runtime)
while read -r FILENAME URL EXPECTED; do
    get "$FILENAME" "$URL" "$EXPECTED"
    BUILD_WHEELS+=("${DLDIR}/${FILENAME}")
done < <(python3 "${REPO_ROOT}/tools/manifest.py" "$ARCH" build)
echo ">> all hashes verified"

# ---------------------------------------------------------------------------
# 2. Assemble the runtime tree
# ---------------------------------------------------------------------------
echo ">> extracting CPython into ${RUNTIME}"
rm -rf "$RUNTIME"
mkdir -p "$RUNTIME"
tar -xzf "${DLDIR}/${PY_TARBALL}" -C "$RUNTIME"
# install_only tarballs contain ./python/...
if [ ! -x "${RUNTIME}/python/bin/python3" ]; then
    echo "!! unexpected CPython tarball layout" >&2
    exit 1
fi
PYBIN="${RUNTIME}/python/bin/python3"
SITE="${RUNTIME}/python/lib/python${PYTHON_VER%.*}/site-packages"

# Build dlib in a throwaway venv (it needs setuptools/cmake/ninja, which we
# deliberately keep OUT of the shipped runtime), then install the resulting
# wheel into the runtime together with the pinned wheels.
echo ">> preparing build venv (dlib build only)"
"${PYBIN}" -m venv "${OUTDIR}/buildvenv"
VPIP="${OUTDIR}/buildvenv/bin/pip"
"${VPIP}" install --quiet --no-index --no-deps "${BUILD_WHEELS[@]}" "${WHEELS[@]}"

if [ "$BUILD_DLIB" = 1 ]; then
    echo ">> building dlib from source (this can take a while)"
    if ! command -v cmake >/dev/null 2>&1 || ! command -v ninja >/dev/null 2>&1; then
        if [ "$OFFLINE" = 1 ]; then
            echo "!! cmake and ninja are needed to build dlib" >&2
            exit 1
        fi
        # As wheels, so no system cmake is required
        "${VPIP}" install --quiet cmake ninja
        export PATH="${OUTDIR}/buildvenv/bin:${PATH}"
    fi
    cmake --version >/dev/null || { echo "!! cmake not available" >&2; exit 1; }
    # No CUDA, no GUI layer: the face core is used headless inside PAM.
    export DLIB_USE_CUDA=0
    export DLIB_NO_GUI_SUPPORT=1
    # Nothing from the build machine's libraries: a distro update bumping
    # one of them (libjxl, libjpeg, BLAS ...) must not break face unlock.
    # Frames come from OpenCV, so dlib never reads image files; the JPEG and
    # PNG code its Python module links to comes from dlib's own copies.
    for OPTION in GIF_SUPPORT WEBP_SUPPORT JXL_SUPPORT LINK_WITH_SQLITE3 \
            USE_BLAS USE_LAPACK USE_MKL_FFT; do
        export "DLIB_${OPTION}=OFF"
    done
    # dlib skips its search for a library already marked as not found
    printf 'set(JPEG_FOUND 0)\nset(PNG_FOUND 0)\n' > "${OUTDIR}/dlib-own-libs.cmake"
    export CMAKE_TOOLCHAIN_FILE="${OUTDIR}/dlib-own-libs.cmake"
    "${VPIP}" wheel --no-cache-dir --no-index --no-deps --no-binary dlib --no-build-isolation \
        "${DLDIR}/${DLIB_SDIST}" -w "${OUTDIR}/dlib-wheel"
fi
DLIB_WHEEL="$(ls "${OUTDIR}"/dlib-wheel/dlib-*.whl 2>/dev/null || true)"

echo ">> installing wheels into the runtime"
"${PYBIN}" -m pip install --quiet --no-index --no-deps --no-compile --target "$SITE" "${WHEELS[@]}"
if [ "$BUILD_DLIB" = 1 ] && [ -n "$DLIB_WHEEL" ]; then
    "${RUNTIME}/python/bin/pip" install --quiet --no-index --no-deps --no-compile --target "$SITE" "$DLIB_WHEEL"
fi
# The runtime ships without pip: it is fully managed by this script.
rm -rf "${RUNTIME}/python/bin/pip" "${RUNTIME}/python/bin/pip3" \
       "${RUNTIME}/python/lib/python${PYTHON_VER%.*}/ensurepip"

# ---------------------------------------------------------------------------
# 3. Strip dead weight
# ---------------------------------------------------------------------------
if [ "${NO_STRIP:-0}" != "1" ]; then
    echo ">> stripping binaries (site-packages only)"
    # The CPython tree itself is already stripped (install_only_stripped);
    # stripping its binaries again breaks symbol versioning.
    find "$SITE" -type f \( -name '*.so' -o -name '*.so.*' \) |
        while read -r f; do
            if file "$f" | grep -q 'ELF.*not stripped'; then strip -s "$f" || true; fi
        done
    find "${RUNTIME}/python/lib/python${PYTHON_VER%.*}" -depth -type d \
        \( -name 'test' -o -name 'tests' -o -name '__pycache__' \) \
        -exec rm -rf {} + 2>/dev/null || true
fi

rm -rf "$SITE/pip" "$SITE"/pip-*.dist-info
# Console scripts and install records point into the build tree; unused
rm -rf "$SITE/bin" "$SITE"/*.dist-info/direct_url.json

# ---------------------------------------------------------------------------
# 4. Smoke test the assembled runtime
# ---------------------------------------------------------------------------
echo ">> smoke test"
# No bytecode: it would record build paths. Packages compile at install paths.
"${PYBIN}" -B - <<'EOF'
import sys
print("python", sys.version.split()[0])
import numpy
print("numpy", numpy.__version__)
import cv2
print("cv2", cv2.__version__)
import dlib
print("dlib", dlib.__version__)
import cryptography
print("cryptography", cryptography.__version__)
import fido2.ctap2
print("fido2 ok")
import dbus_fast.aio
print("dbus-fast ok")
EOF

# Bytecode written while building records build paths
find "$RUNTIME" -depth -type d -name '__pycache__' -exec rm -rf {} +

echo ">> runtime ready at ${RUNTIME}"
du -sh "$RUNTIME"
