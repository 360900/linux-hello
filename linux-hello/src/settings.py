# Layered configuration.
#
# The package ships the defaults read-only at paths.vendor_config_path.
# /etc/linux-hello/config.ini only holds what the admin changed, so package
# upgrades never have to merge or migrate a config file. Older installs
# have a full copy in /etc, which keeps working: it is read as overrides.

import configparser
import os
import tempfile

import paths

OVERRIDE_HEADER = """\
# Linux Hello local settings.
# Only changed values live here; the defaults and their documentation are in
# {vendor}
# Change values with "linux-hello-cli set <option> <value>" or the settings app.

"""


class SettingsError(Exception):
	pass


def _parser():
	# No interpolation: values are plain strings, "%" has no meaning
	return configparser.ConfigParser(interpolation=None)


def vendor_path():
	return str(paths.vendor_config_path)


def override_path():
	return str(paths.config_dir / "config.ini")


def load():
	"""Return a ConfigParser with the defaults and local overrides merged"""
	config = _parser()
	config.read([vendor_path(), override_path()])
	return config


def defaults():
	config = _parser()
	config.read(vendor_path())
	return config


def overrides():
	config = _parser()
	config.read(override_path())
	return config


def find_section(key):
	"""Section of a known option, or None. Raises on ambiguous names."""
	known = defaults()
	found = [section for section in known.sections() if known.has_option(section, key)]
	if len(found) > 1:
		raise SettingsError('"{}" exists in several sections ({}), pass the section'.format(key, ", ".join(found)))
	return found[0] if found else None


def set_value(key, value, section=None):
	"""
	Store one option in the override file. Only options that exist in the
	defaults are accepted, so typos cannot silently do nothing.
	Returns the section the value was written to.
	"""
	known = defaults()
	if section is None:
		section = find_section(key)
	if section is None or not known.has_option(section, key):
		raise SettingsError('Unknown option "{}"'.format(key if section is None else section + "." + key))

	local = overrides()
	if not local.has_section(section):
		local.add_section(section)
	local.set(section, key, str(value))
	_write(local)
	return section


def reset_value(key, section=None):
	"""Drop a local override so the default applies again"""
	if section is None:
		section = find_section(key)
	local = overrides()
	if section and local.has_option(section, key):
		local.remove_option(section, key)
		if not local.options(section):
			local.remove_section(section)
		_write(local)


def ensure_override_file():
	"""Create the override file with its header if it does not exist yet"""
	if not os.path.exists(override_path()):
		_write(overrides())


def _write(local):
	path = override_path()
	directory = os.path.dirname(path)
	os.makedirs(directory, mode=0o755, exist_ok=True)

	descriptor, temporary = tempfile.mkstemp(prefix=".config-", dir=directory)
	try:
		with os.fdopen(descriptor, "w") as file:
			file.write(OVERRIDE_HEADER.format(vendor=vendor_path()))
			local.write(file)
			file.flush()
			os.fsync(file.fileno())
		os.chmod(temporary, 0o644)
		os.replace(temporary, path)
	except BaseException:
		if os.path.exists(temporary):
			os.unlink(temporary)
		raise
