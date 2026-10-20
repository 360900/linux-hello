#!/usr/bin/env python3
"""Downloads pinned in tools/runtime-manifest.json, one per line:

    <filename> <url> <sha256>

Usage: tools/manifest.py <x86_64|aarch64> [runtime|build|dlib-data|all]

runtime: CPython, the wheels shipped in the runtime and the dlib sources
build: wheels only needed to compile dlib
dlib-data: the dlib model archives (.bz2)
"""
import json
import os
import sys
import urllib.parse

MANIFEST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runtime-manifest.json")


def runtime(manifest, arch):
	python = manifest["python"]
	tarball = "cpython-{}+{}-{}-unknown-linux-gnu-{}.tar.gz".format(
		python["version"], python["pbs_tag"], arch, python.get("variant", "install_only"))
	url = "https://github.com/astral-sh/python-build-standalone/releases/download/{}/{}".format(
		python["pbs_tag"], urllib.parse.quote(tarball))
	yield tarball, url, python["sha256"][arch]
	for name, entry in manifest["packages"].items():
		artifact = entry.get(arch) or entry.get("sdist")
		if artifact is None:
			sys.exit("no pinned artifact for {} on {}".format(name, arch))
		yield artifact["filename"], artifact["url"], artifact["sha256"]


def build(manifest, arch):
	for entry in manifest["build_packages"].values():
		artifact = entry.get(arch) or entry["any"]
		yield artifact["filename"], artifact["url"], artifact["sha256"]


def dlib_data(manifest, arch):
	data = manifest["dlib_data"]
	for name, digest in data["files"].items():
		yield name, data["url"] + name, digest


def main():
	if len(sys.argv) not in (2, 3) or sys.argv[1] not in ("x86_64", "aarch64"):
		sys.exit(__doc__)
	arch = sys.argv[1]
	kind = sys.argv[2] if len(sys.argv) == 3 else "all"
	groups = {"runtime": [runtime], "build": [build], "dlib-data": [dlib_data], "all": [runtime, build, dlib_data]}
	if kind not in groups:
		sys.exit(__doc__)
	with open(MANIFEST) as file:
		manifest = json.load(file)
	for group in groups[kind]:
		for line in group(manifest, arch):
			print(*line)


if __name__ == "__main__":
	main()
