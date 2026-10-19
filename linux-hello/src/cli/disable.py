# Set the disable flag

# Import required modules
import sys
import builtins
import settings

from i18n import _

# Check if enough arguments have been passed
if not builtins.linux_hello_args.arguments:
	print(_("Please add a 0 (enable) or a 1 (disable) as an argument"))
	sys.exit(1)

argument = builtins.linux_hello_args.arguments[0].lower()

if argument in ("1", "true"):
	out_value = "true"
elif argument in ("0", "false"):
	out_value = "false"
else:
	print(_("Please only use 0 (enable) or 1 (disable) as an argument"))
	sys.exit(1)

if out_value == settings.load().get("core", "disabled", fallback="false"):
	print(_("The disable option has already been set to ") + out_value)
	sys.exit(1)

settings.set_value("disabled", out_value, section="core")

if out_value == "true":
	print(_("Linux Hello has been disabled"))
else:
	print(_("Linux Hello has been enabled"))
