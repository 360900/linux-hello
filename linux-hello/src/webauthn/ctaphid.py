# CTAPHID transport layer
# Reassembles 64-byte HID reports into CTAPHID messages, dispatches CBOR
# commands to the authenticator, and frames responses back. Long running
# operations (face verification) run in a worker thread while keepalive
# frames are emitted so the browser does not time out.

import os
import struct
import threading

from fido2.ctap import CtapError

# CTAPHID command bytes (high bit set on the wire)
CTAPHID_PING = 0x01
CTAPHID_MSG = 0x03
CTAPHID_LOCK = 0x04
CTAPHID_INIT = 0x06
CTAPHID_WINK = 0x08
CTAPHID_CBOR = 0x10
CTAPHID_CANCEL = 0x11
CTAPHID_KEEPALIVE = 0x3B
CTAPHID_ERROR = 0x3F

# CTAPHID error codes (payload of a CTAPHID_ERROR frame)
ERR_INVALID_CMD = 0x01
ERR_INVALID_PAR = 0x02
ERR_INVALID_LEN = 0x03
ERR_INVALID_SEQ = 0x04
ERR_MSG_TIMEOUT = 0x05
ERR_CHANNEL_BUSY = 0x06
ERR_INVALID_CHANNEL = 0x0B
ERR_OTHER = 0x7F

# Keepalive status bytes
STATUS_PROCESSING = 0x01
STATUS_UPNEEDED = 0x02

# Capability flags in the INIT response
CAPABILITY_WINK = 0x01
CAPABILITY_CBOR = 0x04
CAPABILITY_NMSG = 0x08  # CTAPHID_MSG (U2F) not supported

HID_REPORT_SIZE = 64
CID_BROADCAST = 0xFFFFFFFF
INIT_PAYLOAD = HID_REPORT_SIZE - 7
CONT_PAYLOAD = HID_REPORT_SIZE - 5
CTAPHID_PROTOCOL_VERSION = 2


class Channel:
	"""Reassembly state for one CTAPHID channel"""

	def __init__(self, cid):
		self.cid = cid
		self.command = None
		self.expected_length = 0
		self.buffer = bytearray()
		self.next_seq = 0

	def reset(self):
		self.command = None
		self.expected_length = 0
		self.buffer = bytearray()
		self.next_seq = 0


class CtapHidDevice:
	"""Drives one virtual FIDO HID device against an Authenticator

	The transport is abstracted: `send_report` writes a 64-byte report to the
	host, `feed_report` is called with each 64-byte report from the host.
	"""

	def __init__(self, authenticator, send_report):
		self.authenticator = authenticator
		self._raw_send_report = send_report
		self.channels = {}
		self._next_cid = 1
		self._lock = threading.Lock()
		# Serialises outbound reports: keepalive, worker-response and
		# feed-thread frames run concurrently, and a multi-frame message must
		# never have a foreign frame spliced into it or the host's reassembler
		# corrupts (crashing the browser on overlapping ceremonies)
		self._send_lock = threading.Lock()
		self._worker = None
		self._active_cid = None
		self._cancelled = False
		# Whether a cancelled operation should still send its response frame.
		# True for CTAPHID_CANCEL (the host waits for KEEPALIVE_CANCEL), False
		# for a CTAPHID_INIT re-sync (the channel was reset, INIT is the reply)
		self._cancel_reply = True

	# --- inbound ---

	def feed_report(self, report):
		"""Handle one 64-byte report from the host"""
		if len(report) < 5:
			return
		cid = struct.unpack(">I", report[:4])[0]
		is_init_frame = report[4] & 0x80

		if is_init_frame:
			self._handle_init_frame(cid, report)
		else:
			self._handle_cont_frame(cid, report)

	def _handle_init_frame(self, cid, report):
		command = report[4] & 0x7F
		length = struct.unpack(">H", report[5:7])[0]

		if command == CTAPHID_INIT:
			self._handle_ctaphid_init(cid, report[7:7 + length])
			return

		if cid == CID_BROADCAST:
			self._send_error(cid, ERR_INVALID_CHANNEL)
			return
		if cid not in self.channels:
			self._send_error(cid, ERR_INVALID_CHANNEL)
			return

		if command == CTAPHID_CANCEL:
			self._cancel_active(cid)
			return

		# Reject a new transaction while another channel is mid-message
		if self._busy_with_other(cid):
			self._send_error(cid, ERR_CHANNEL_BUSY)
			return

		channel = self.channels[cid]
		channel.reset()
		channel.command = command
		channel.expected_length = length
		channel.buffer = bytearray(report[7:7 + min(length, INIT_PAYLOAD)])
		channel.next_seq = 0

		if len(channel.buffer) >= length:
			self._dispatch(channel)

	def _handle_cont_frame(self, cid, report):
		channel = self.channels.get(cid)
		if channel is None or channel.command is None:
			# Continuation for an idle channel: ignore per spec
			return
		seq = report[4] & 0x7F
		if seq != channel.next_seq:
			channel.reset()
			self._send_error(cid, ERR_INVALID_SEQ)
			return
		channel.next_seq += 1
		remaining = channel.expected_length - len(channel.buffer)
		channel.buffer += report[5:5 + min(remaining, CONT_PAYLOAD)]
		if len(channel.buffer) >= channel.expected_length:
			self._dispatch(channel)

	def _busy_with_other(self, cid):
		with self._lock:
			return self._active_cid is not None and self._active_cid != cid

	# --- command handling ---

	def _handle_ctaphid_init(self, cid, nonce):
		if len(nonce) != 8:
			self._send_error(cid, ERR_INVALID_LEN)
			return

		if cid == CID_BROADCAST:
			with self._lock:
				new_cid = self._next_cid
				self._next_cid += 1
			self.channels[new_cid] = Channel(new_cid)
		else:
			# Re-sync of an existing channel: abort its in-flight transaction,
			# including any running face verification, so a browser restarting
			# the ceremony (e.g. the user clicking twice) doesn't leave the old
			# operation and its keepalives running
			new_cid = cid
			if cid in self.channels:
				self.channels[cid].reset()
			self._cancel_active(cid, reply=False)

		response = nonce + struct.pack(
			">IBBBBB", new_cid, CTAPHID_PROTOCOL_VERSION, 0, 0, 0,
			CAPABILITY_CBOR | CAPABILITY_NMSG)
		self._send_message(cid, CTAPHID_INIT, response)

	def _dispatch(self, channel):
		command = channel.command
		payload = bytes(channel.buffer)
		cid = channel.cid
		channel.reset()

		if command == CTAPHID_PING:
			self._send_message(cid, CTAPHID_PING, payload)
		elif command == CTAPHID_WINK:
			self._send_message(cid, CTAPHID_WINK, b"")
		elif command == CTAPHID_MSG:
			# U2F/CTAP1 is not offered (NMSG capability advertised)
			self._send_error(cid, ERR_INVALID_CMD)
		elif command == CTAPHID_CBOR:
			self._start_cbor(cid, payload)
		elif command == CTAPHID_LOCK:
			self._send_message(cid, CTAPHID_LOCK, b"")
		else:
			self._send_error(cid, ERR_INVALID_CMD)

	def _start_cbor(self, cid, payload):
		if not payload:
			self._send_error(cid, ERR_INVALID_LEN)
			return

		with self._lock:
			if self._active_cid is not None:
				self._send_error(cid, ERR_CHANNEL_BUSY)
				return
			self._active_cid = cid
			self._cancelled = False
			self._worker = threading.Thread(
				target=self._run_cbor, args=(cid, payload), daemon=True)
			self._worker.start()

	def _run_cbor(self, cid, payload):
		# Emit keepalives from a helper thread while the (possibly slow, camera
		# bound) command runs, so the client shows its "verify" prompt
		stop = threading.Event()
		keepalive = threading.Thread(
			target=self._keepalive_loop, args=(cid, stop), daemon=True)
		keepalive.start()
		try:
			response = self.authenticator.handle_cbor(payload)
		except Exception:
			response = bytes([CtapError.ERR.OTHER])
		finally:
			stop.set()
			keepalive.join()

		with self._lock:
			cancelled = self._cancelled
			cancel_reply = self._cancel_reply
			self._active_cid = None
			self._worker = None

		if cancelled and not cancel_reply:
			# A CTAPHID_INIT re-sync reset the channel; the INIT response was
			# already sent, so drop this transaction's stale reply
			return
		# On CTAPHID_CANCEL `response` is CTAP2_ERR_KEEPALIVE_CANCEL, which the
		# host is waiting for; on success it's the real answer. Either way the
		# host expects exactly one response frame for its request
		self._send_message(cid, CTAPHID_CBOR, response)

	def _keepalive_loop(self, cid, stop):
		# ~100ms cadence, UPNEEDED so the browser prompts the user
		while not stop.wait(0.1):
			self._send_message(cid, CTAPHID_KEEPALIVE, bytes([STATUS_UPNEEDED]))

	def _cancel_active(self, cid, reply=True):
		with self._lock:
			if self._active_cid != cid:
				return
			self._cancelled = True
			self._cancel_reply = reply
		# Abort the in-flight face verification
		self.authenticator.cancel()

	# --- outbound framing ---

	def _send_message(self, cid, command, data):
		"""Fragment data into an init frame plus continuation frames

		Held under _send_lock so the whole message reaches the host as one
		contiguous run of frames, even when other threads are also sending.
		"""
		length = len(data)
		with self._send_lock:
			header = struct.pack(">IBH", cid, 0x80 | command, length)
			chunk = data[:INIT_PAYLOAD]
			frame = header + chunk
			self._raw_send_report(frame.ljust(HID_REPORT_SIZE, b"\x00"))

			offset = len(chunk)
			seq = 0
			while offset < length:
				chunk = data[offset:offset + CONT_PAYLOAD]
				frame = struct.pack(">IB", cid, seq & 0x7F) + chunk
				self._raw_send_report(frame.ljust(HID_REPORT_SIZE, b"\x00"))
				offset += len(chunk)
				seq += 1

	def _send_error(self, cid, code):
		self._send_message(cid, CTAPHID_ERROR, bytes([code]))
