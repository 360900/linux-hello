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
# Requires: curl (or wget), tar, python3. Network access needed.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFEST="${REPO_ROOT}/tools/runtime-manifest.json"
OUTDIR="${REPO_ROOT}/build-runtime"
ARCH="$(uname -m)"
BUILD_DLIB=1
JOBS="$(nproc 2>/dev/null || echo 4)"

while [ $# -gt 0 ]; do
    case "$1" in
        --outdir) OUTDIR="$2"; shift 2 ;;
        --arch) ARCH="$2"; shift 2 ;;
        --no-dlib) BUILD_DLIB=0; shift ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

case "$ARCH" in
    x86_64 | aarch64) ;;
    *) echo "unsupported arch: $ARCH (use x86_64 or aarch64)" >&2; exit 2 ;;
esac

PYTHON_VER="$(python3 -c "import json;print(json.load(open('$MANIFEST'))['python']['version'])")"
PBS_TAG="$(python3 -c "import json;print(json.load(open('$MANIFEST'))['python']['pbs_tag'])")"
PY_VARIANT="$(python3 -c "import json;print(json.load(open('$MANIFEST'))['python'].get('variant', 'install_only'))")"

RUNTIME="${OUTDIR}/linux-hello-runtime"
DLDIR="${OUTDIR}/dl"
mkdir -p "$DLDIR"

fetch() {
    # fetch <url> <dest>
    if command -v curl >/dev/null 2>&1; then
        curl -fL --retry 3 -o "$2" "$1"
    else
        wget -O "$2" "$1"
    fi
}

# ---------------------------------------------------------------------------
# 1. CPython (python-build-standalone, install_only)
# ---------------------------------------------------------------------------
PY_TARBALL="cpython-${PYTHON_VER}+${PBS_TAG}-${ARCH}-unknown-linux-gnu-${PY_VARIANT}.tar.gz"
PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_TAG}/$(python3 -c "import urllib.parse;print(urllib.parse.quote('${PY_TARBALL}'))")"

if [ ! -f "${DLDIR}/${PY_TARBALL}" ]; then
    echo ">> downloading CPython ${PYTHON_VER} (${ARCH})"
    fetch "$PY_URL" "${DLDIR}/${PY_TARBALL}"
fi
PY_EXPECTED="$(python3 -c "import json;print(json.load(open('$MANIFEST'))['python']['sha256']['$ARCH'])")"
PY_ACTUAL="$(sha256sum "${DLDIR}/${PY_TARBALL}" | cut -d' ' -f1)"
if [ "$PY_ACTUAL" != "$PY_EXPECTED" ]; then
    echo "!! sha256 mismatch for ${PY_TARBALL}" >&2
    echo "   expected $PY_EXPECTED" >&2
    echo "   actual   $PY_ACTUAL" >&2
    exit 1
fi
echo ">> CPython tarball hash verified"

# ---------------------------------------------------------------------------
# 2. Wheels: download + verify sha256 from the manifest, then install
# ---------------------------------------------------------------------------
WHEELS=()
for PKG in numpy opencv-python-headless shiboken6 PySide6-Essentials PySide6 cffi pycparser cryptography fido2; do
    read -r FILENAME URL EXPECTED <<EOF
$(python3 - "$PKG" "$ARCH" "$MANIFEST" <<'PYEOF'
import json, sys
m = json.load(open(sys.argv[3]))
e = m["packages"][sys.argv[1]]
arch = sys.argv[2]
if arch not in e:
    sys.exit(f"no pinned artifact for {sys.argv[1]} on {arch}")
print(e[arch]["filename"], e[arch]["url"], e[arch]["sha256"])
PYEOF
)
EOF
    if [ ! -f "${DLDIR}/${FILENAME}" ]; then
        echo ">> downloading $FILENAME"
        fetch "$URL" "${DLDIR}/${FILENAME}"
    fi
    ACTUAL="$(sha256sum "${DLDIR}/${FILENAME}" | cut -d' ' -f1)"
    if [ "$ACTUAL" != "$EXPECTED" ]; then
        echo "!! sha256 mismatch for $FILENAME" >&2
        echo "   expected $EXPECTED" >&2
        echo "   actual   $ACTUAL" >&2
        exit 1
    fi
    WHEELS+=("${DLDIR}/${FILENAME}")
done
echo ">> all wheel hashes verified"

# dlib sdist
DLIB_SDIST="$(python3 -c "import json;print(json.load(open('$MANIFEST'))['packages']['dlib']['sdist']['filename'])")"
DLIB_URL="$(python3 -c "import json;print(json.load(open('$MANIFEST'))['packages']['dlib']['sdist']['url'])")"
DLIB_HASH="$(python3 -c "import json;print(json.load(open('$MANIFEST'))['packages']['dlib']['sdist']['sha256'])")"
if [ ! -f "${DLDIR}/${DLIB_SDIST}" ]; then
    echo ">> downloading $DLIB_SDIST"
    fetch "$DLIB_URL" "${DLDIR}/${DLIB_SDIST}"
fi
ACTUAL="$(sha256sum "${DLDIR}/${DLIB_SDIST}" | cut -d' ' -f1)"
if [ "$ACTUAL" != "$DLIB_HASH" ]; then
    echo "!! sha256 mismatch for $DLIB_SDIST" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# 3. Assemble the runtime tree
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
"${VPIP}" install --quiet --upgrade pip setuptools wheel
"${VPIP}" install --quiet --no-deps "${WHEELS[@]}"

if [ "$BUILD_DLIB" = 1 ]; then
    echo ">> building dlib from source (this can take a while)"
    # cmake/ninja as wheels so no system cmake is required
    "${VPIP}" install --quiet cmake ninja
    export PATH="${OUTDIR}/buildvenv/bin:${PATH}"
    cmake --version >/dev/null || { echo "!! cmake not available" >&2; exit 1; }
    # No CUDA, no GUI layer: the face core is used headless inside PAM.
    export DLIB_USE_CUDA=0
    export DLIB_NO_GUI_SUPPORT=1
    "${VPIP}" wheel --no-deps --no-binary dlib --no-build-isolation \
        "${DLDIR}/${DLIB_SDIST}" -w "${OUTDIR}/dlib-wheel"
fi
DLIB_WHEEL="$(ls "${OUTDIR}"/dlib-wheel/dlib-*.whl 2>/dev/null || true)"

echo ">> installing wheels into the runtime"
"${PYBIN}" -m pip install --quiet --no-deps --target "$SITE" "${WHEELS[@]}"
if [ "$BUILD_DLIB" = 1 ] && [ -n "$DLIB_WHEEL" ]; then
    "${RUNTIME}/python/bin/pip" install --quiet --no-deps --target "$SITE" "$DLIB_WHEEL"
fi
# The runtime ships without pip: it is fully managed by this script.
rm -rf "${RUNTIME}/python/bin/pip" "${RUNTIME}/python/bin/pip3" \
       "${RUNTIME}/python/lib/python${PYTHON_VER%.*}/ensurepip"

# ---------------------------------------------------------------------------
# 4. Strip dead weight
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

# ---------------------------------------------------------------------------
# 5. Prune PySide6 down to what the app uses (QtCore/QtGui/QtWidgets + deps)
# ---------------------------------------------------------------------------
QT_KEEP=" Core Gui Widgets Network DBus Svg Xml "
rm -rf "$SITE/pip" "$SITE"/pip-*.dist-info
rm -rf "$SITE/PySide6/Qt/qml" "$SITE/PySide6/Qt/translations"
rm -rf "$SITE/PySide6"/{assistant,designer,doc,include,linguist,lrelease,lupdate,qmlformat,qmllint,qmlls,svgtoqml}
rm -f "$SITE/PySide6"/libpyside6qml.abi3.so.*
for so in "$SITE"/PySide6/*.abi3.so; do
    MOD="$(basename "$so" | sed 's/^Qt//; s/\.abi3\.so$//')"
    case "$QT_KEEP" in *" $MOD "*) continue ;; esac
    rm -f "$so" "${so%.abi3.so}.pyi"
done
for lib in "$SITE/PySide6/Qt/lib"/libQt6*; do
    [ -e "$lib" ] || continue
    MOD="$(basename "$lib" | sed 's/^libQt6//; s/\.so.*$//')"
    case "$QT_KEEP" in *" $MOD "*) continue ;; esac
    rm -f "$lib"
done

# ---------------------------------------------------------------------------
# 4. Smoke test the assembled runtime
# ---------------------------------------------------------------------------
echo ">> smoke test"
"${PYBIN}" - <<'EOF'
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
from PySide6 import QtWidgets, QtGui, QtCore
print("PySide6", QtCore.__version__)
EOF

echo ">> runtime ready at ${RUNTIME}"
du -sh "$RUNTIME"
