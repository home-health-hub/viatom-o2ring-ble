# viatom-o2ring-ble

![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white) ![Bluetooth LE](https://img.shields.io/badge/Bluetooth-LE-0082FC?logo=bluetooth&logoColor=white)

[![License: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-blue)](https://github.com/home-health-hub/viatom-o2ring-ble/blob/main/LICENSE) [![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/home-health-hub/viatom-o2ring-ble#contributing) [![Discussions](https://img.shields.io/badge/discussions-welcome-blue)](https://github.com/home-health-hub/viatom-o2ring-ble/discussions)

A standalone Python client for Viatom/Wellue pulse oximeters that share
Viatom's "oxy" BLE protocol family: O2Ring, Checkme O2 (including the
Sanei-branded "CMRing" variant), KidsO2, RingO2, SleepO2, and the O2 Max.
The O2Ring S (T8520), S8-AW, Band-WU, and SHQO2Pro, which speak a
completely different protocol, are supported separately via `OxyIIClient`
-- see [OxyII (O2Ring-S / T8520)](https://github.com/home-health-hub/viatom-o2ring-ble/wiki/OxyII-Protocol). It connects
over Bluetooth Low Energy, and supports live SpO2/heart-rate streaming,
downloading and decoding stored measurement sessions, and writing device
settings such as vibration alert thresholds.

> [!WARNING]
> **Work in progress -- not yet verified against real hardware.** Every
> protocol detail here is built from, and cross-checked against, five
> independent third-party/official sources (see
> [Acknowledgments](#acknowledgments)), not from testing against an
> actual device. Treat this as protocol-correct-on-paper rather than
> field-verified until that's happened. This notice will be removed once
> it has been confirmed against real hardware.

> [!IMPORTANT]
> **"O2Ring S" (T8520) is a different protocol, now separately supported.**
> O2Ring S, S8-AW, Band-WU, and SHQO2Pro speak a newer protocol generation
> ("OxyII") over a completely different BLE service UUID -- `O2RingClient`
> still does not, and cannot, work against them. Use `OxyIIClient` instead;
> see [OxyII (O2Ring-S / T8520)](https://github.com/home-health-hub/viatom-o2ring-ble/wiki/OxyII-Protocol). This support is
> newer and less battle-tested than the rest of the package -- see that
> page's own status notes.

## Disclaimer

This is an unofficial, reverse-engineered client. The author and
contributors are not affiliated with Viatom Technology Co., Ltd. or
Wellue. **This is a personal-use tool for reading data from and
configuring your own device, not a medical product.** The device's
vibration alerts are a safety feature (desaturation/heart-rate alarms);
double-check any threshold you write with `set_o2_alert`/`set_hr_alert`
before relying on it.

## Features

- Discovers and connects to the device over BLE (via [bleak](https://github.com/hbldh/bleak)
  and [bleak-retry-connector](https://github.com/Bluetooth-Devices/bleak-retry-connector)).
- Streams live SpO2, pulse rate, battery/charging state, perfusion index,
  worn/calibrating state, and raw PPG waveform samples, via the same
  CMD_RT_DATA command the current official app polls. An older, simpler
  command is available as an explicit fallback (`legacy_sensors=True`).
- Lists and downloads stored `.vld` measurement files, and decodes them
  to structured records or CSV -- including the per-session SpO2
  average/min, time/percent under 90%, O2 score, and step count that
  most third-party implementations leave undecoded.
- Reads back current device configuration (firmware/hardware versions,
  current alert thresholds, vibration strength, mode) via CMD_INFO, and
  writes configuration: clock sync, SpO2/heart-rate vibration alert
  thresholds, vibration strength, and screen always-on/brightness.
- Ships a `viatom-o2ring` CLI for one-off use without writing any code,
  covering both device families (`--oxyii` for the O2Ring-S; see
  [OxyII (O2Ring-S / T8520)](https://github.com/home-health-hub/viatom-o2ring-ble/wiki/OxyII-Protocol) for what that mode
  does and doesn't cover).
- Separately, supports the O2Ring-S (T8520) via `OxyIIClient`: live SpO2/
  heart-rate streaming, and listing/downloading/decoding its stored
  recordings -- a completely different protocol from everything above;
  see [OxyII (O2Ring-S / T8520)](https://github.com/home-health-hub/viatom-o2ring-ble/wiki/OxyII-Protocol).

## Requirements

- A Viatom/Wellue pulse oximeter in the "oxy" family (see above and the
  O2Ring S warning). Not physically tested against real hardware yet; the
  protocol is built from, and cross-checked against, five independent
  sources -- including two of Viatom's own official BLE SDKs -- rather
  than from a live device.
- A Bluetooth Low Energy adapter reachable by [bleak](https://github.com/hbldh/bleak).

## Installation

```bash
pip install git+https://github.com/home-health-hub/viatom-o2ring-ble.git
```

## Library usage

### Live streaming

```python
import asyncio

from viatom_o2ring_ble import O2RingClient, RtReading


def on_reading(reading: RtReading) -> None:
    if reading.worn and not reading.calibrating:
        print(f"SpO2 {reading.spo2}%, pulse {reading.pulse_bpm} bpm, battery {reading.battery}%")


async def main() -> None:
    client = O2RingClient("AA:BB:CC:DD:EE:FF", on_reading=on_reading)
    await client.async_start()
    try:
        await asyncio.Event().wait()  # run until interrupted
    finally:
        await client.async_stop()


asyncio.run(main())
```

Pass `legacy_sensors=True` to `O2RingClient(...)` to use the older
CMD_READ_SENSORS command instead (delivers a `Reading`, not `RtReading`)
-- see [Protocol notes](https://github.com/home-health-hub/viatom-o2ring-ble/wiki/Protocol#two-live-reading-commands).

See the [Usage Examples](https://github.com/home-health-hub/viatom-o2ring-ble/wiki/Usage-Examples)
wiki page for one-off info/file-download/config, discovering a device's
address, and O2Ring-S (T8520) streaming/file-download examples.

## CLI usage

```bash
# Find nearby devices
viatom-o2ring --discover

# Stream live readings from a known address until Ctrl+C
viatom-o2ring --address AA:BB:CC:DD:EE:FF

# Print device info and exit
viatom-o2ring --address AA:BB:CC:DD:EE:FF --info
```

Run `viatom-o2ring --help` for all options. See the
[CLI Reference](https://github.com/home-health-hub/viatom-o2ring-ble/wiki/CLI-Reference)
wiki page for the full set of examples (file listing/download, config
writes, OxyII/`--oxyii` equivalents) and flag-compatibility notes.

## Protocol notes

The device speaks a request/response protocol over one GATT service,
framed as `sync | cmd | cmd^0xFF | block(2, LE) | length(2, LE) | data |
crc8` with a CRC-8-CCITT checksum. See the
[Protocol reference](https://github.com/home-health-hub/viatom-o2ring-ble/wiki/Protocol)
wiki page for the GATT UUIDs, full packet/command-code layout, the two
live-reading commands, the `.vld` stored-file format, config write value
ranges, file-download robustness notes, and device discovery matching
rules.

The O2Ring-S (T8520), S8-AW, Band-WU, and SHQO2Pro speak a completely
different, newer protocol generation ("OxyII") over a different GATT
service. See the
[OxyII (O2Ring-S / T8520)](https://github.com/home-health-hub/viatom-o2ring-ble/wiki/OxyII-Protocol)
wiki page for its frame format, connection handshake, discovery quirks,
stored-file format, and what's deliberately not implemented.

## Contributing

Contributions are welcome!

- **Bug reports**: [Open an issue](https://github.com/home-health-hub/viatom-o2ring-ble/issues).
- **Everything else** (questions, feature requests, ideas, general discussion): [Use Discussions](https://github.com/home-health-hub/viatom-o2ring-ble/discussions).
- Pull requests are welcome for bug fixes or discussed features.

## Acknowledgments

- Built by combining and cross-checking five independent sources against
  the same device family:
  [farolone/wellue-o2ring-protocol](https://github.com/farolone/wellue-o2ring-protocol)
  (packet framing and file-transfer protocol docs),
  [ecostech/viatom-ble](https://github.com/ecostech/viatom-ble) (client
  architecture: async/sync dual API, scanner lifecycle, live-reading byte
  offsets), [MackeyStingray/o2r](https://github.com/MackeyStingray/o2r)
  (the original downloader/configurator, and the only one of the first
  three that documents the CMD_CONFIG write commands and the full `.vld`
  header layout used here),
  [viatom-develop/LepuBle](https://github.com/viatom-develop/LepuBle),
  Viatom's own official low-level BLE SDK, which confirms the GATT UUIDs
  and the full set of devices sharing this protocol (including the O2
  Max), supplied the real advertised-name matching rules used in
  `client.supported()`, and -- on closer reading of its response parsers
  (`OxyBleResponse.kt`, `OxyDataFile.kt`) and command builders
  (`OxyBleCmd.java`) -- turned out to define the live-reading command and
  the `.vld` header/record layouts differently than the first three
  sources, correcting real bugs in this package's first cut, and
  [viatom-develop/LepuDemo](https://github.com/viatom-develop/LepuDemo),
  Viatom's current higher-level SDK, whose device compatibility table is
  where the "O2Ring S is a different, incompatible protocol" finding
  comes from, and whose documented value ranges corrected two more bugs
  in this package's CMD_CONFIG validation (`commands.py`) that had been
  sourced only from o2r. See the
  [Protocol reference](https://github.com/home-health-hub/viatom-o2ring-ble/wiki/Protocol)
  for the specifics.
- OxyII (O2Ring-S / T8520) support is built on
  [nglessner/o2ring-s-protocol](https://github.com/nglessner/o2ring-s-protocol),
  a reverse-engineered, MIT-licensed protocol reference with a working
  Python reference implementation (frame codec, CRC-8, auth-key
  derivation, GET_INFO/GET_FILE_LIST parsing), verified end-to-end
  against a real T8520 (byte-exact against vendor-app file exports via
  SHA-256, live-reading and file-transfer round-trips). `oxyii_*.py`'s
  frame codec, CRC-8, and session-key derivation are adapted directly
  from it; the client/command/data/file-parsing layers around them are
  this package's own, patterned after `O2RingClient` for consistency with
  the rest of the package. See
  [OxyII (O2Ring-S / T8520)](https://github.com/home-health-hub/viatom-o2ring-ble/wiki/OxyII-Protocol)
  for what's ported vs. original, and what's deliberately not
  implemented.
- Code review, ported implementation, and documentation assisted by [Claude](https://www.anthropic.com/claude).

## License

This project is licensed under the **GNU General Public License v3.0**.

See [LICENSE](LICENSE) for more information.
