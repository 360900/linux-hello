# Set a config value

# Import required modules
import sys
import builtins
import settings

from i18n import _

# Check if enough arguments have been passed
if len(builtins.linux_hello_args.arguments) < 2:
	print(_("Please add a setting you would like to change and the value to set it to"))
	print(_("For example:"))
	print("\n\tlinux-hello-cli set certainty 3\n")
	print("\tlinux-hello-cli set --section webauthn enabled true\n")
	sys.exit(1)

set_name = builtins.linux_hello_args.arguments[0]
set_value = builtins.linux_hello_args.arguments[1]
set_section = getattr(builtins.linux_hello_args, "section", None)

try:
	settings.set_value(set_name, set_value, section=set_section)
except settings.SettingsError as error:
	print(error)
	sys.exit(1)

print(_("Config option updated"))
