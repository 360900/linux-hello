import os
import re

SRC = os.path.join(os.path.dirname(__file__), "..", "src")


def test_every_python_source_is_installed():
	"""meson lists sources by hand; a forgotten file only fails once installed"""
	with open(os.path.join(SRC, "meson.build")) as file:
		text = file.read()
	start = text.index("py_sources = [")
	listed = set(re.findall(r"'([^']+\.py)'", text[start:text.index("]", start)]))

	present = set()
	for directory, _dirs, names in os.walk(SRC):
		for name in names:
			if name.endswith(".py"):
				present.add(os.path.relpath(os.path.join(directory, name), SRC))

	assert present - listed == set(), "add these to py_sources in src/meson.build"
	assert listed - present == set(), "listed in meson.build but missing"
