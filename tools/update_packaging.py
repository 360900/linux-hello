#!/usr/bin/env python3
"""Write the pinned downloads of tools/runtime-manifest.json into the
packaging files, between their "pinned sources" markers.

Usage: tools/update_packaging.py [--check]
--check only reports whether a file is out of date.
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BEGIN = "# BEGIN pinned sources (tools/update_packaging.py)\n"
END = "# END pinned sources\n"
ARCHES = ("x86_64", "aarch64")


def downloads(arch):
	output = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "manifest.py"), arch],
		check=True, capture_output=True, text=True).stdout
	return [line.split() for line in output.splitlines()]


def pkgbuild():
	lines = []
	names = set()
	for arch in ARCHES:
		entries = downloads(arch)
		lines.append("source_{}=(".format(arch))
		lines += ["\t'{}::{}'".format(name, url) for name, url, _digest in entries]
		lines.append(")")
		lines.append("sha256sums_{}=(".format(arch))
		lines += ["\t'{}'".format(digest) for _name, _url, digest in entries]
		lines.append(")")
		names.update(name for name, _url, _digest in entries)
	# Only the dlib model archives get unpacked by makepkg
	lines.append("noextract=(")
	lines += ["\t'{}'".format(name) for name in sorted(names) if not name.endswith(".bz2")]
	lines.append(")")
	return "\n".join(lines) + "\n"


def spec():
	# rpm does not check these; tools/build_runtime.sh verifies every hash
	lines = []
	for base, arch in zip((100, 200), ARCHES):
		lines.append("%ifarch {}".format(arch))
		for number, (name, url, _digest) in enumerate(downloads(arch), base):
			lines.append("Source{}: {}#/{}".format(number, url.replace("%", "%%"), name))
		lines.append("%endif")
	return "\n".join(lines) + "\n"


FILES = {
	os.path.join("packaging", "arch", "PKGBUILD"): pkgbuild,
	os.path.join("packaging", "fedora", "linux-hello.spec"): spec,
}


def main():
	check = "--check" in sys.argv
	stale = []
	for relative, generate in FILES.items():
		path = os.path.join(ROOT, relative)
		with open(path) as file:
			text = file.read()
		start = text.index(BEGIN) + len(BEGIN)
		updated = text[:start] + generate() + text[text.index(END):]
		if updated == text:
			continue
		if check:
			stale.append(relative)
		else:
			with open(path, "w") as file:
				file.write(updated)
	if stale:
		sys.exit("Out of date, run tools/update_packaging.py: " + ", ".join(stale))


if __name__ == "__main__":
	main()
