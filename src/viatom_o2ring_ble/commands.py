"""Request builders for the Viatom/Wellue ring BLE protocol.

CMD_INFO, CMD_READ_SENSORS, and the CMD_FILE_* flow are confirmed by all
four source projects. The CMD_CONFIG (write) key names were first sourced
from o2r's o2state.py (check_settings/SetConfig) -- neither the O2Ring
protocol docs nor viatom-ble document a config/write path at all -- and
the exact valid ranges below are since confirmed against Viatom's current
official high-level SDK docs (viatom-develop/LepuDemo's OxyActivity.kt
comment block and README device table), which disagree with o2r's ranges
in two places: the O2 alert threshold is 80-95, not 1-100, and heart-rate
thresholds are 30-250, not 0-200. Current values are read back via
CMD_INFO under the unprefixed name (e.g. "CurOxiThr"); writing uses the
same name with "Set" in place of "Cur" (e.g. "SetOxiThr"), or a bare
"Set" prefix for switches that have no "Cur" counterpart (e.g.
"OxiSwitch" -> "SetOxiSwitch").

Note: vibration motor range is device-dependent (0-100 for O2Ring-class
devices per the official docs, but only 0-35 for KidsO2/Oxylink) --
set_vibration_strength validates against the wider O2Ring range since
that's this package's primary target; a KidsO2-class device is expected
to clamp an out-of-its-range value rather than error.

Safety note: set_o2_alert/set_hr_alert configure the device's own
desaturation/heart-rate vibration alarm. This is a medical-alerting
feature, not a cosmetic setting -- confirm values before writing them.
"""

from __future__ import annotations

import json
import time

from .const import (
    CMD_CONFIG,
    CMD_FACTORY_DEFAULT,
    CMD_FILE_CLOSE,
    CMD_FILE_OPEN,
    CMD_FILE_READ,
    CMD_INFO,
    CMD_PING,
    CMD_READ_SENSORS,
    CMD_RT_DATA,
)
from .protocol import encode_request

TIME_FORMAT = "%Y-%m-%d,%H:%M:%S"


def info() -> bytes:
    """Request device info: model, serial, battery, current config, file list."""
    return encode_request(CMD_INFO)


def ping() -> bytes:
    return encode_request(CMD_PING)


def read_sensors() -> bytes:
    """Request one live reading via the legacy CMD_READ_SENSORS (0x17).

    Confirmed against O2Ring-era firmware; not what the current official
    app uses (see read_rt_data). Kept as an explicit fallback.
    """
    return encode_request(CMD_READ_SENSORS)


def read_rt_data(waveform_rate: int = 0) -> bytes:
    """Request one live reading via CMD_RT_DATA (0x1B), the command the
    current official app actually polls for live monitoring.

    waveform_rate: 0 for 125Hz PPG waveform samples, 1 for 62.5Hz.
    """
    if waveform_rate not in (0, 1):
        raise ValueError("waveform_rate must be 0 (125Hz) or 1 (62.5Hz)")
    return encode_request(CMD_RT_DATA, bytes([waveform_rate]))


def file_open(filename: str) -> bytes:
    """Open a stored file for download.

    The filename must be null-terminated, or the device returns error
    code 9.
    """
    return encode_request(CMD_FILE_OPEN, filename.encode("ascii") + b"\x00")


def file_read(block: int) -> bytes:
    """Read the next block of the currently open file."""
    return encode_request(CMD_FILE_READ, block=block)


def file_close() -> bytes:
    return encode_request(CMD_FILE_CLOSE)


def factory_default() -> bytes:
    """Reset the device to factory defaults. Destructive; use deliberately."""
    return encode_request(CMD_FACTORY_DEFAULT)


def _config(payload: dict[str, int | str]) -> bytes:
    return encode_request(CMD_CONFIG, json.dumps(payload, separators=(",", ":")).encode("ascii"))


def set_time(when: time.struct_time | None = None) -> bytes:
    """Sync the device clock. Defaults to the current local system time."""
    return _config({"SetTIME": time.strftime(TIME_FORMAT, when or time.localtime())})


def set_o2_alert(threshold_percent: int | None) -> bytes:
    """Configure the SpO2 desaturation vibration alarm.

    threshold_percent: alert when SpO2 drops to or below this value.
        Must be 80-95, the device's confirmed valid range. Pass 0 or None
        to disable the alert entirely.
    """
    if threshold_percent is None or threshold_percent == 0:
        return _config({"SetOxiSwitch": 0})
    if not 80 <= threshold_percent <= 95:
        raise ValueError("threshold_percent must be 0 (disable) or between 80 and 95")
    return _config({"SetOxiThr": threshold_percent, "SetOxiSwitch": 1})


def set_hr_alert(*, high_bpm: int | None = None, low_bpm: int | None = None) -> bytes:
    """Configure the heart-rate vibration alarm.

    high_bpm/low_bpm: alert above/below this rate. Each must be 0
    (disables that bound) or 30-250, the device's confirmed valid range.
    The alarm as a whole is only fully disabled if both bounds are 0 or
    omitted-as-0 in the same call. At least one of high_bpm/low_bpm must
    be given.
    """
    if high_bpm is None and low_bpm is None:
        raise ValueError("set_hr_alert() requires at least one of high_bpm/low_bpm")

    cfg: dict[str, int | str] = {}
    for bpm, thr_key in ((high_bpm, "SetHRHighThr"), (low_bpm, "SetHRLowThr")):
        if bpm is None:
            continue
        if bpm == 0:
            cfg["SetHRSwitch"] = 0
        elif 30 <= bpm <= 250:
            cfg["SetHRSwitch"] = 1
            cfg[thr_key] = bpm
        else:
            raise ValueError("high_bpm/low_bpm must be 0 (disable) or between 30 and 250")
    return _config(cfg)


def set_vibration_strength(strength: int) -> bytes:
    """Set overall vibration motor strength. 0 is off; up to 100 for
    O2Ring-class devices (KidsO2/Oxylink top out lower -- see module note).
    """
    if not 0 <= strength <= 100:
        raise ValueError("strength must be between 0 and 100")
    return _config({"SetMotor": strength})


def set_lighting_mode(mode: int) -> bytes:
    """Set the screen lighting mode: 0 (standard, auto-timeout), 1 (always
    off), or 2 (always on).
    """
    if mode not in (0, 1, 2):
        raise ValueError("mode must be 0 (standard), 1 (always off), or 2 (always on)")
    return _config({"SetLightingMode": mode})


def set_screen_always_on(enabled: bool) -> bytes:
    """Convenience wrapper for the common on/standard case.

    Maps to set_lighting_mode(2) if enabled, else set_lighting_mode(0).
    This can't select mode 1 (always off); use set_lighting_mode directly
    for that.
    """
    return set_lighting_mode(2 if enabled else 0)


def set_brightness(level: int) -> bytes:
    """Set screen brightness: 0 (low), 1 (medium), or 2 (high)."""
    if level not in (0, 1, 2):
        raise ValueError("level must be 0 (low), 1 (medium), or 2 (high)")
    return _config({"SetLightStr": level})
