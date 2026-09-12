# Set a config value

# Import required modules
import sys
import builtins
import fileinput
import paths_factory

from i18n import _

# Get the absolute filepath
config_path = paths_factory.config_file_path()

# Check if enough arguments have been passed
if len(builtins.linux_hello_args.arguments) < 2:
	print(_("Please add a setting you would like to change and the value to set it to"))
	print(_("For example:"))
	print("\n\tlinux-hello-cli set certainty 3\n")
	print("\tlinux-hello-cli set --section webauthn enabled true\n")
	sys.exit(1)

# Get the name and value from the cli
set_name = builtins.linux_hello_args.arguments[0]
set_value = builtins.linux_hello_args.arguments[1]

# Optional section to disambiguate duplicate option names
set_section = getattr(builtins.linux_hello_args, "section", None)

# Will be filled with the exact config line to update
found_line = None

# Track the current INI section while scanning
current_section = ""

# Loop through all lines in the config file
for line in fileinput.input([config_path]):
	stripped = line.strip()
	# Track which section we are in
	if stripped.startswith("[") and stripped.endswith("]"):
		current_section = stripped[1:-1]
		continue
	# Save the line if it starts with the requested config option
	# and (when a section was given) is inside that section
	if line.startswith(set_name + " ") and (not set_section or current_section == set_section):
		found_line = line

# If we don't have the line it is not in the config file
if found_line is None:
	if set_section:
		print(_('Could not find a "{}" config option to set in section "{}"').format(set_name, set_section))
	else:
		print(_('Could not find a "{}" config option to set').format(set_name))
	sys.exit(1)

# Go through the file again and update only the first exact match
replaced = False
for line in fileinput.input([config_path], inplace=1):
	if not replaced and line == found_line:
		print(set_name + " = " + set_value)
		replaced = True
	else:
		print(line, end="")

if not replaced:
	# Should not happen since we found the line above
	print(_("Config option updated"))
else:
	print(_("Config option updated"))
