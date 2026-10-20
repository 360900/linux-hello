#!/usr/bin/env python3
"""Write the pinned downloads of tools/runtime-manifest.json into
packaging/arch/PKGBUILD, between its "pinned sources" markers."""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKGBUILD = os.path.join(ROOT, "packaging", "arch", "PKGBUILD")
BEGIN = "# BEGIN pinned sources (tools/update_pkgbuild.py)\n"
END = "# END pinned sources\n"


def downloads(arch):
	output = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "manifest.py"), arch],
		check=True, capture_output=True, text=True).stdout
	return [line.split() for line in output.splitlines()]


def block():
	lines = []
	names = set()
	for arch in ("x86_64", "aarch64"):
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


def main():
	with open(PKGBUILD) as file:
		text = file.read()
	start = text.index(BEGIN) + len(BEGIN)
	end = text.index(END)
	updated = text[:start] + block() + text[end:]
	if "--check" in sys.argv:
		if updated != text:
			sys.exit("packaging/arch/PKGBUILD is out of date, run tools/update_pkgbuild.py")
		return
	with open(PKGBUILD, "w") as file:
		file.write(updated)


if __name__ == "__main__":
	main()
