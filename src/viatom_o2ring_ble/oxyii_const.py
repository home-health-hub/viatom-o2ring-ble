"""Constants for the "OxyII" BLE protocol used by the O2Ring-S (T8520).

This is a completely separate protocol from the rest of this package (see
const.py's module docstring) -- different GATT service, different frame
format, different auth handshake, different stored-file format. Nothing
here is shared with const.py/protocol.py/commands.py/data.py/file.py on
purpose: those target the legacy "Oxy" family (plain O2Ring, KidsO2,
RingO2, O2 Max, etc.), this targets the O2Ring-S specifically, and mixing
the two up is exactly the mistake every existing open-source O2Ring tool
made against a T8520 before this was reverse-engineered.

Sourced from nglessner/o2ring-s-protocol
(https://github.com/nglessner/o2ring-s-protocol), a reverse-engineered,
MIT-licensed protocol reference with a working Python implementation,
verified end-to-end (byte-exact against vendor-app file exports via
SHA-256) against a real T8520. See this package's CLAUDE.md.
"""

#: Advertised name prefix in "OxyII / sync mode" (idle, or briefly after a
#: recording finalizes) -- the mode that exposes the full OxyII GATT
#: service and supports file transfer. The device's Random Static address
#: rotates on every factory reset, so identification must go by name
#: prefix / manufacturer ID / service UUID, never a hardcoded MAC.
LOCAL_NAME_PREFIX = "S8-AW"

#: Manufacturer ID (Viatom's OxyII-specific ID, distinct from the legacy
#: family's manufacturer data) seen in OxyII-mode advertisements.
MANUFACTURER_ID = 0xF34E

#: In "recording mode" (worn, actively recording) the device instead
#: advertises as local name "T8520_<last4>" with manufacturer ID 0x036F
#: (the same Viatom ID the legacy family uses) and a stripped GATT layout
#: that does not reliably expose the OxyII service. supported() below
#: recognizes this name prefix too, but a device only found this way
#: cannot be connected to for file transfer until it re-advertises in
#: OxyII mode (worn or pressing the button is enough to trigger that).
RECORDING_MODE_NAME_PREFIX = "T8520_"

#: GATT service/characteristic UUIDs for the OxyII protocol. Entirely
#: distinct from const.SERVICE_UUID -- do not mix the two families up.
SERVICE_UUID = "e8fb0001-a14b-98f9-831b-4e2941d01248"
WRITE_CHARACTERISTIC_UUID = "e8fb0002-a14b-98f9-831b-4e2941d01248"
NOTIFY_CHARACTERISTIC_UUID = "e8fb0003-a14b-98f9-831b-4e2941d01248"

#: Every reply fits in a single ATT notification once this MTU is
#: negotiated -- see client.py's connection handshake. Below this, larger
#: replies (in particular the 512-byte READ_FILE_DATA chunks) may arrive
#: fragmented across more than one notification; OxyIIFrameAssembler
#: handles that either way, but requesting the larger MTU up front is what
#: makes cmd=0xF2 (READ_FILE_START) work at all -- see the module
#: docstring in client.py for the "MTU gotcha".
REQUESTED_MTU = 517
#: Minimum negotiated MTU file transfer has been confirmed to work at
#: ("Vendor app requests 517 / accepts 247; either is sufficient").
MINIMUM_MTU_FOR_FILE_TRANSFER = 247

#: Frame lead byte for both requests and replies (OxyII does not use
#: separate request/response sync bytes the way the legacy protocol does).
FRAME_LEAD = 0xA5
#: 0xA5, cmd, ~cmd, flag, seq, len_lo, len_hi.
FRAME_HEADER_SIZE = 7
#: app -> device request.
FLAG_REQUEST = 0x00
#: device -> app reply.
FLAG_REPLY = 0x01

# Opcodes confirmed end-to-end against a real T8520 (see the upstream
# repo's Status table). The vendor SDK exposes further opcodes
# (GET_RT_PARAM, GET_RT_WAVE, GET_RT_PPG, RESET) that haven't been
# captured/verified there; not implemented here either.
OP_GET_CONFIG = 0x00
OP_LIVE_SAMPLES_A = 0x03
OP_LIVE_SAMPLES_B = 0x04
OP_SETUP = 0x10  # required post-auth handshake step; exact purpose unknown
OP_SET_UTC_TIME = 0xC0
OP_GET_INFO = 0xE1
OP_FACTORY_RESET = 0xE3  # wipes settings AND every recording; see client.py
OP_GET_BATTERY = 0xE4
OP_GET_FILE_LIST = 0xF1
OP_READ_FILE_START = 0xF2
OP_READ_FILE_DATA = 0xF3
OP_READ_FILE_END = 0xF4
OP_AUTH = 0xFF

#: FACTORY_RESET_ALL (0xEE): powers the ring off and refuses to
#: re-advertise until woken by USB power. The vendor app doesn't expose
#: it; deliberately not implemented here -- there is no legitimate reason
#: for this library to send it, and the failure mode (bricked-until-USB)
#: is severe enough not to wrap it "for completeness."

#: MD5("lepucloud"), the salt used to derive the (unused-in-practice) AES
#: session key and the cmd=0xFF auth XOR payload. A protocol constant, not
#: a secret.
LEPUCLOUD_SALT = b"lepucloud"

#: Recommended portable default for auth key derivation when the device's
#: real serial number isn't known yet (auth happens immediately after
#: connect, before GET_INFO can be called to learn the real one).
DEFAULT_AUTH_SERIAL = "0000"

#: Format A stored-file header, ten fixed bytes at the start of every file
#: this device produces (see file.py). The `04 00` at offset 8-9 appears
#: to be the sample interval in some unit; no value other than this has
#: been observed.
FILE_HEADER = bytes.fromhex("01030000000000000400")
FILE_HEADER_SIZE = len(FILE_HEADER)
#: Bytes per sample record in the body (spo2, heart_rate, status flags),
#: one record per second.
FILE_RECORD_SIZE = 3
#: Trailer appended to every finalized recording; see file.py.
FILE_TRAILER_SIZE = 48
#: Sub-magic bytes confirming a file's trailer has actually flushed (at
#: trailer offset 4-7, i.e. file_size - 44) -- full byte count alone is
#: not a reliable "this file is complete" check; see file.py.
FILE_TRAILER_SUB_MAGIC = bytes.fromhex("48125ada")

#: The firmware emits this as a no-finger-contact sentinel for heart
#: rate in Format A records; clamp/flag rather than plot verbatim.
FILE_NO_FINGER_HR_SENTINEL = 0xFF
