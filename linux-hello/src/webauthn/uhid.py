# Virtual FIDO2 HID device via the Linux /dev/uhid interface
# Creates a kernel HID device advertising the FIDO usage page, so an
# unmodified browser discovers it exactly like a plugged-in security key.

import fcntl
import os
import struct

# uhid event types from linux/uhid.h
UHID_CREATE2 = 11
UHID_DESTROY = 1
UHID_START = 2
UHID_STOP = 3
UHID_OPEN = 4
UHID_CLOSE = 5
UHID_OUTPUT = 6
UHID_INPUT2 = 12

UHID_DEV = "/dev/uhid"
HID_REPORT_SIZE = 64

# Canonical FIDO CTAPHID report descriptor (usage page 0xF1D0, usage 0x01),
# a 64-byte input report and a 64-byte output report. Taken verbatim from the
# CTAP HID specification, as used by other virtual FIDO authenticators.
FIDO_REPORT_DESCRIPTOR = bytes([
	0x06, 0xD0, 0xF1,  # Usage Page (FIDO Alliance 0xF1D0)
	0x09, 0x01,        # Usage (CTAPHID)
	0xA1, 0x01,        # Collection (Application)
	0x09, 0x20,        #   Usage (Input Report Data)
	0x15, 0x00,        #   Logical Minimum (0)
	0x26, 0xFF, 0x00,  #   Logical Maximum (255)
	0x75, 0x08,        #   Report Size (8)
	0x95, 0x40,        #   Report Count (64)
	0x81, 0x02,        #   Input (Data, Var, Abs)
	0x09, 0x21,        #   Usage (Output Report Data)
	0x15, 0x00,        #   Logical Minimum (0)
	0x26, 0xFF, 0x00,  #   Logical Maximum (255)
	0x75, 0x08,        #   Report Size (8)
	0x95, 0x40,        #   Report Count (64)
	0x91, 0x02,        #   Output (Data, Var, Abs)
	0xC0,              # End Collection
])


class UHidDevice:
	"""A /dev/uhid backed virtual FIDO HID device"""

	def __init__(self, name="Linux Hello Virtual FIDO2", vendor=0x1209, product=0xF1D0):
		self.name = name
		self.vendor = vendor
		self.product = product
		self._fd = os.open(UHID_DEV, os.O_RDWR)
		self._create()

	def _create(self):
		name = self.name.encode()[:127]
		name = name.ljust(128, b"\x00")
		phys = b"\x00" * 64
		uniq = b"\x00" * 64
		rd_size = len(FIDO_REPORT_DESCRIPTOR)
		rd_data = FIDO_REPORT_DESCRIPTOR.ljust(4096, b"\x00")
		# struct uhid_create2_req: name[128], phys[64], uniq[64], rd_size u16,
		# bus u16, vendor u32, product u32, version u32, country u32, rd_data[4096]
		payload = (
			name + phys + uniq
			+ struct.pack("<HHIIII", rd_size, 0x03, self.vendor, self.product, 0, 0)
			+ rd_data
		)
		self._write_event(UHID_CREATE2, payload)

	def _write_event(self, event_type, payload=b""):
		# struct uhid_event starts with a u32 type; the kernel reads a fixed
		# size buffer, so pad to the largest event body we send
		data = struct.pack("<I", event_type) + payload
		os.write(self._fd, data)

	def send_input(self, report):
		"""Send a 64-byte report to the host (UHID_INPUT2)"""
		report = bytes(report)[:HID_REPORT_SIZE].ljust(HID_REPORT_SIZE, b"\x00")
		payload = struct.pack("<H", len(report)) + report
		self._write_event(UHID_INPUT2, payload)

	def read_event(self):
		"""Read one uhid event, returns (event_type, output_report or None)"""
		data = os.read(self._fd, 4096 + 256)
		if len(data) < 4:
			return None, None
		event_type = struct.unpack("<I", data[:4])[0]
		if event_type == UHID_OUTPUT:
			# struct uhid_output_req: u8 data[4096], u16 size, u8 rtype.
			# Validate the body actually holds the full fixed-size struct
			# before reading the size field at its offset
			body = data[4:]
			if len(body) < 4098:
				return event_type, None
			size = struct.unpack("<H", body[4096:4098])[0]
			if size > 4096:
				return event_type, None
			report = body[:size]
			# The kernel prepends the report id byte (0 for our single report)
			if report and report[0] == 0x00:
				report = report[1:]
			return event_type, report
		return event_type, None

	def fileno(self):
		return self._fd

	def close(self):
		# May be called re-entrantly (signal handler interrupting the read
		# loop) and from more than one place, so capture the fd and null it
		# before touching it, and never raise
		fd = self._fd
		if fd is None:
			return
		self._fd = None
		try:
			os.write(fd, struct.pack("<I", UHID_DESTROY))
		except OSError:
			pass
		try:
			os.close(fd)
		except OSError:
			pass
