import os

import pytest


@pytest.fixture
def settings(fake_paths):
	import settings
	return settings


def test_defaults_without_override_file(settings):
	config = settings.load()
	assert config.get("video", "certainty") == "3.5"
	assert not os.path.exists(settings.override_path())


def test_set_writes_only_the_override(settings):
	section = settings.set_value("certainty", "2.5")
	assert section == "video"
	assert settings.load().get("video", "certainty") == "2.5"

	local = settings.overrides()
	assert local.sections() == ["video"]
	assert local.options("video") == ["certainty"]
	with open(settings.override_path()) as file:
		text = file.read()
	assert text.startswith("# Linux Hello local settings.")
	assert os.stat(settings.override_path()).st_mode & 0o777 == 0o644


def test_unknown_option_is_rejected(settings):
	with pytest.raises(settings.SettingsError):
		settings.set_value("certanity", "2")
	with pytest.raises(settings.SettingsError):
		settings.set_value("certainty", "2", section="core")
	assert not os.path.exists(settings.override_path())


def test_ambiguous_option_needs_section(settings):
	# "enabled" exists in [rubberstamps] and [webauthn]
	with pytest.raises(settings.SettingsError):
		settings.set_value("enabled", "true")
	settings.set_value("enabled", "true", section="webauthn")
	config = settings.load()
	assert config.getboolean("webauthn", "enabled")
	assert not config.getboolean("rubberstamps", "enabled")


def test_reset_value_restores_default(settings):
	settings.set_value("timeout", "8")
	settings.reset_value("timeout")
	assert settings.load().get("video", "timeout") == "4"
	assert settings.overrides().sections() == []


def test_full_legacy_config_still_applies(settings, fake_paths):
	# Older installs copied the whole config to /etc
	os.makedirs(str(fake_paths.config_dir))
	with open(str(fake_paths.vendor_config_path)) as source:
		text = source.read().replace("certainty = 3.5", "certainty = 4.2")
	with open(settings.override_path(), "w") as file:
		file.write(text)
	assert settings.load().get("video", "certainty") == "4.2"
	settings.set_value("timeout", "6")
	config = settings.load()
	assert config.get("video", "certainty") == "4.2"
	assert config.get("video", "timeout") == "6"


def test_percent_values_are_literal(settings):
	settings.set_value("device_path", "/dev/v4l/by-id/usb-Cam_100%-video-index0")
	assert settings.load().get("video", "device_path").endswith("100%-video-index0")
