# Remove a model from a user

# Import required modules
import sys
import builtins
import models as model_store

from i18n import _

user = builtins.linux_hello_user

# Check if enough arguments have been passed
if not builtins.linux_hello_args.arguments:
	print(_("Please add the ID of the model you want to remove as an argument"))
	print(_("For example:"))
	print("\n\tlinux-hello-cli remove 0\n")
	print(_("You can find the IDs by running:"))
	print("\n\tlinux-hello-cli list\n")
	sys.exit(1)

encodings = model_store.load(user)
if not encodings:
	print(_("No face model known for the user {}, please run:").format(user))
	print("\n\tlinux-hello-cli add\n")
	sys.exit(1)

id = builtins.linux_hello_args.arguments[0]
match = next((enc for enc in encodings if str(enc["id"]) == id), None)

if match is None:
	print(_("No model with ID {id} exists for {user}").format(id=id, user=user))
	sys.exit(1)

# Only ask the user if there's no -y flag
if not builtins.linux_hello_args.y:
	print(_('This will remove the model called "{label}" for {user}').format(label=match["label"], user=user))
	ans = input(_("Do you want to continue [y/N]: "))
	if ans.lower() != "y":
		print(_('\nInterpreting as a "NO", aborting'))
		sys.exit(1)
	print()

model_store.remove(user, match["id"])

if len(encodings) == 1:
	print(_("Removed last model, Linux Hello disabled for user"))
else:
	print(_("Removed model {}").format(id))
