#!/usr/bin/env python3
"""meson install script: copy the bundled python runtime into the package.

Invoked by meson.add_install_script() when -Dbundled_python_dir is set.
Copies <runtime> to $MESON_INSTALL_DESTDIR_PREFIX/$libdir/linux-hello-runtime,
preserving executable bits and symlinks.
"""
import os
import shutil
import sys

src, libdir = sys.argv[1:3]
if not libdir:
    sys.exit("install_runtime.py: usage: install_runtime.py <runtime-src> <libdir>")
destdir = os.environ.get("MESON_INSTALL_DESTDIR_PREFIX", "")
if not destdir:
    sys.exit("install_runtime.py: MESON_INSTALL_DESTDIR_PREFIX not set")

dest = os.path.join(destdir, libdir.lstrip("/"), "linux-hello-runtime")
os.makedirs(os.path.dirname(dest), exist_ok=True)

if os.path.exists(dest):
    shutil.rmtree(dest)

shutil.copytree(src, dest, symlinks=True)
print(f"install_runtime.py: bundled python -> {dest}")
