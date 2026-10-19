# Clear all models of a user

# Import required modules
import sys
import builtins
import models as model_store

from i18n import _

user = builtins.linux_hello_user

if not model_store.load(user):
	print(_("{} has no models or they have been cleared already").format(user))
	sys.exit(1)

# Only ask the user if there's no -y flag
if not builtins.linux_hello_args.y:
	print(_("This will clear all models for ") + user)
	ans = input(_("Do you want to continue [y/N]: "))
	if ans.lower() != "y":
		print(_('\nInterpreting as a "NO", aborting'))
		sys.exit(1)

model_store.delete(user)
print(_("\nModels cleared"))
