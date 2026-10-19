# List all models for a user

# Import required modules
import sys
import time
import builtins
import models as model_store

from i18n import _

user = builtins.linux_hello_user
encodings = model_store.load(user)

if not encodings:
	if not builtins.linux_hello_args.plain:
		print(_("No face model known for the user {}, please run:").format(user))
		print("\n\tsudo linux-hello-cli -U " + user + " add\n")
	sys.exit(1)

# Print a header if we're not in plain mode
if not builtins.linux_hello_args.plain:
	print(_("Known face models for {}:").format(user))
	print("\n\033[1;29m" + _("ID  Date                 Label\033[0m"))

for enc in encodings:
	date = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(enc["time"]))
	if builtins.linux_hello_args.plain:
		print("{},{},{}".format(enc["id"], date, enc["label"]))
	else:
		print("{:<4}{}  {}".format(enc["id"], date, enc["label"]))

print()
