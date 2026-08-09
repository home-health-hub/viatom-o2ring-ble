"""Constants for the Viatom/Wellue ring pulse-oximeter BLE protocol.

Confirmed against four independent sources: the O2Ring protocol docs
(farolone/wellue-o2ring-protocol), the actively maintained viatom-ble
client (ecostech/viatom-ble), the original o2r downloader/configurator
(MackeyStingray/o2r) -- the only one of the first three that documents the
config/write command set used here -- and Viatom's own official Android
BLE SDK (viatom-develop/LepuBle), which hardcodes these same three UUIDs
in its OxyBleManager and confirms the device family: O2Ring, CheckO2,
SleepO2, KidsO2, RingO2, and the O2 Max all share one BLE manager class.

The LepuBle source (OxyBleCmd.java, OxyBleResponse.kt, OxyDataFile.kt) is
also what current firmware/the current official app actually speaks,
which turned out to disagree with the other three sources in two
important ways: live readings are polled with CMD_RT_DATA (0x1B), not the
older CMD_READ_SENSORS (0x17) documented elsewhere, and the .vld header
and record layouts both decode differently than o2r's o2file.py assumed.
See protocol.py, file.py, and data.py for the specifics.

A fifth source, Viatom's higher-level current SDK docs
(viatom-develop/LepuDemo's README and OxyActivity.kt), confirmed the
CMD_CONFIG value ranges used in commands.py (some of which, sourced only
from o2r, were wrong) and is also where the incompatibility below comes
from.

IMPORTANT: "O2Ring S" and a few other similarly-named devices
(Bluetooth.MODEL_O2RING_S, MODEL_S8_AW, MODEL_BAND_WU, MODEL_SHQO2_PRO)
are NOT part of this "Oxy" protocol family despite the name resemblance.
They speak a different, newer protocol ("OxyII") over an entirely
different GATT service: E8FB0001-A14B-98F9-831B-4E2941D01248 (vs.
14839ac4-... here). Nothing in this package will work against one --
BLE service discovery will simply fail to find SERVICE_UUID. If your
device's box/app says "O2Ring S" rather than plain "O2Ring", this
library does not support it.
"""

MANUFACTURER = "Viatom"

#: Advertised-name prefix that means a device is in the O2 family but
#: doesn't literally contain "O2" (Viatom's own app dispatches on this
#: exact prefix as a CheckO2-model device, a Sanei-branded variant).
LOCAL_NAME_EXACT_MATCHES = ("CMRing",)

#: GATT service/characteristic UUIDs for the "Oxy" protocol family this
#: package implements. Devices on the newer, incompatible "OxyII" protocol
#: (O2Ring S and others -- see the module docstring) use a different
#: service entirely (E8FB0001-A14B-98F9-831B-4E2941D01248) and are not,
#: and cannot be made, reachable through these UUIDs.
SERVICE_UUID = "14839ac4-7d7e-415c-9a42-167340cf2339"
NOTIFY_CHARACTERISTIC_UUID = "0734594a-a8e7-4b1a-a6b1-cd5243059a57"
WRITE_CHARACTERISTIC_UUID = "8b00ace7-eb0b-49b0-bbe9-9aee0a26e1a3"

# Command codes for the CMD field of a request packet.
CMD_FILE_OPEN = 3
CMD_FILE_READ = 4
CMD_FILE_CLOSE = 5
CMD_INFO = 20
CMD_PING = 21
CMD_CONFIG = 22
#: Legacy live-reading command (viatom-ble, o2r). Confirmed working against
#: O2Ring-era firmware; unconfirmed against current firmware/devices, which
#: use CMD_RT_DATA instead. Kept as an opt-in fallback -- see client.py.
CMD_READ_SENSORS = 23
CMD_FACTORY_DEFAULT = 24
#: Current live-reading command (LepuBle's OXY_CMD_RT_DATA), used by the
#: official app for every device in this family. Returns SpO2, a 2-byte
#: pulse rate, battery, battery charge state, perfusion index, lead-on
#: state, and a chunk of raw PPG waveform samples.
CMD_RT_DATA = 27

#: Sync byte leading every packet the client sends to the device.
REQUEST_SYNC_BYTE = 0xAA

#: Sync byte leading every packet the device sends back. Distinct from the
#: request sync byte -- a detail present in o2r's framing but absent from
#: the O2Ring protocol docs, which show only 0xAA in both directions.
RESPONSE_SYNC_BYTE = 0x55

#: Maximum payload size per BLE write; larger packets must be chunked.
BLE_WRITE_CHUNK_SIZE = 20

#: Byte offsets within a legacy CMD_READ_SENSORS (0x17) response *payload*
#: (the data section after ResponseAssembler strips the 7-byte header --
#: NOT offsets into the full packet). viatom-ble documents these same
#: fields numbered from the start of the full packet (7 higher than here,
#: e.g. its SpO2 offset is 7, not 0); o2r's o2state.py decodes the payload
#: directly and confirms these payload-relative values, plus one field --
#: charging -- that viatom-ble doesn't expose.
LIVE_IDX_SPO2 = 0
LIVE_IDX_HR = 1
LIVE_IDX_BATTERY = 7
LIVE_IDX_CHARGING = 8
LIVE_IDX_MOVEMENT = 9
LIVE_IDX_PI = 10
LIVE_IDX_WORN = 11
LIVE_PACKET_MIN_LEN = LIVE_IDX_WORN + 1

#: Byte offsets within a CMD_RT_DATA (0x1B) response payload, from
#: LepuBle's OxyBleResponse.RtWave. Pulse rate is 2 bytes here (unlike the
#: legacy command's 1 byte); battery charge state and lead-on state are
#: each their own byte rather than packed fields.
RT_IDX_SPO2 = 0
RT_IDX_PULSE = 1  # 2 bytes, little-endian
RT_IDX_BATTERY = 3
RT_IDX_BATTERY_STATE = 4  # 0 = not charging, 1 = charging, 2 = fully charged
RT_IDX_PI = 5
RT_IDX_LEAD_STATE = 6  # 1 = lead on (worn); 0 = lead off; other values undefined
RT_IDX_WAVE_LEN = 10  # 2 bytes, little-endian
RT_IDX_WAVE_DATA = 12
RT_PACKET_MIN_LEN = RT_IDX_WAVE_LEN + 2

#: Record size, in bytes, for VLD version-3 measurement records.
VLD3_RECORD_SIZE = 5

#: Byte length of the parsed portion of a VLD3 header, per LepuBle's
#: OxyDataFile.kt (version and mode as separate bytes, size as one 4-byte
#: field, plus percent-below-90% and step count that o2r's o2file.py never
#: decoded). The file's actual reserved header block is 40 bytes; 10 bytes
#: of that (30-40) are genuinely unused padding. Records begin at 40.
VLD3_HEADER_PARSED_SIZE = 30
VLD3_HEADER_TOTAL_SIZE = 40

CSV_TIME_FORMAT = "%I:%M:%S%p %b %d, %Y"

#: Column layout for exporting a decoded .vld file. "Acceleration" and
#: "Reserved" follow OxyDataFile.kt's O2Sample field names/meaning; o2r's
#: "Motion"/"Vibration" labels for these same two bytes are unconfirmed by
#: Viatom's own code (which explicitly marks the second one reserved).
CSV_FILE_FIELDNAMES = ("Time", "SpO2(%)", "Pulse Rate(bpm)", "Acceleration", "Reserved")

#: Column layout for logging legacy CMD_READ_SENSORS (0x17) readings,
#: matching viatom-ble's --csv-file output (each row also ends with one
#: blank trailer field). Distinct from CSV_FILE_FIELDNAMES: live readings
#: expose separate SpO2/PR alert-reminder flags that file records don't.
CSV_LIVE_FIELDNAMES = (
    "Time", "SpO2(%)", "Pulse Rate(bpm)", "Motion", "SpO2 Reminder", "PR Reminder",
)

#: Sentinel values the device (and its own PC software) use for "no finger".
NO_FINGER_SPO2 = 255
NO_FINGER_PULSE = 65535
