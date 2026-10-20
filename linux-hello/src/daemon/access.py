# Who is calling, and may they do this?
#
# The caller's uid comes from the bus driver, never from the message. Admin
# and enrolment actions go through polkit with interaction allowed, so the
# desktop's polkit agent shows a password (or face) prompt when needed.

import logging
import pwd

from dbus_fast import Message, MessageType, Variant

from daemon.dispatch import Caller, Failed

log = logging.getLogger("linux-hello.access")

ACTION_ENROLL_SELF = "io.github.linuxhello.enroll-self"
ACTION_MANAGE_USERS = "io.github.linuxhello.manage-users"
ACTION_CONFIGURE = "io.github.linuxhello.configure"
ACTION_PREVIEW = "io.github.linuxhello.preview"

# CheckAuthorization flag AllowUserInteraction
ALLOW_INTERACTION = 1


class Access:
	def __init__(self, bus):
		self.bus = bus

	async def _call(self, destination, path, interface, member, signature, body):
		reply = await self.bus.call(Message(
			destination=destination,
			path=path,
			interface=interface,
			member=member,
			signature=signature,
			body=body,
		))
		if reply.message_type == MessageType.ERROR:
			raise Failed("Internal", "{}: {}".format(reply.error_name, reply.body[0] if reply.body else ""))
		return reply.body

	async def resolve(self, sender):
		body = await self._call(
			"org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
			"GetConnectionUnixUser", "s", [sender],
		)
		return Caller(sender, int(body[0]))

	async def authorized(self, caller, action):
		"""Ask polkit; root is always allowed"""
		if caller.uid == 0:
			return True
		subject = ("system-bus-name", {"name": Variant("s", caller.sender)})
		body = await self._call(
			"org.freedesktop.PolicyKit1", "/org/freedesktop/PolicyKit1/Authority",
			"org.freedesktop.PolicyKit1.Authority", "CheckAuthorization", "(sa{sv})sa{ss}us",
			[subject, action, {}, ALLOW_INTERACTION, ""],
		)
		is_authorized, _is_challenge, _details = body[0]
		return bool(is_authorized)

	async def require(self, caller, action):
		if not await self.authorized(caller, action):
			log.warning("uid %d was not authorized for %s", caller.uid, action)
			raise Failed("PermissionDenied", "Not authorized for {}".format(action))

	# -- composite rules ---------------------------------------------------
	@staticmethod
	def user_name(uid):
		try:
			return pwd.getpwuid(uid).pw_name
		except KeyError:
			return None

	async def has_local_session(self, uid, pid):
		"""
		Whether someone can be at the keyboard for uid: the asking process
		(pid, 0 when unknown) is not in a remote or inactive session, and
		the user has an active session at a seat. Apps on Plasma run in the
		user's systemd instance, outside any session, so a process without
		one only needs the second.
		"""
		login1 = "org.freedesktop.login1"
		if pid:
			path = await self._call_or_none(login1, "/org/freedesktop/login1", login1 + ".Manager",
				"GetSessionByPID", "u", [pid])
			if path is not None and not self._local_and_active(await self._session(path[0])):
				return False
		sessions = await self._call_or_none(login1, "/org/freedesktop/login1", login1 + ".Manager",
			"ListSessions", "", [])
		for _id, session_uid, _user, seat, path in (sessions[0] if sessions else []):
			if session_uid == uid and seat and self._local_and_active(await self._session(path)):
				return True
		return False

	async def _call_or_none(self, *args):
		try:
			return await self._call(*args)
		except Failed:
			return None

	async def _session(self, path):
		reply = await self._call_or_none("org.freedesktop.login1", path, "org.freedesktop.DBus.Properties",
			"GetAll", "s", ["org.freedesktop.login1.Session"])
		return {key: value.value for key, value in reply[0].items()} if reply else {}

	@staticmethod
	def _local_and_active(properties):
		return properties.get("Active") is True and properties.get("Remote") is False

	def is_self(self, caller, user):
		return caller.uid != 0 and self.user_name(caller.uid) == user

	async def require_self_or_admin(self, caller, user, action_self):
		"""
		Acting on your own face needs action_self (or nothing when None),
		acting on someone else's needs the admin action
		"""
		if caller.uid == 0:
			return
		if self.is_self(caller, user):
			if action_self is not None:
				await self.require(caller, action_self)
			return
		await self.require(caller, ACTION_MANAGE_USERS)
