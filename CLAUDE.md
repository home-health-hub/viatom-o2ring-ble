# Project notes for viatom-o2ring-ble

## Upstream sources to watch

Unlike some sibling libraries in this family (which have only ever had
one source to go on), this one was built by combining and cross-checking
five independent sources -- and doing so caught real bugs (see the
README's Protocol notes section) where the first three sources disagreed
with Viatom's own official SDKs. Any of the five could still surface
something new:

- **farolone/wellue-o2ring-protocol** --
  https://github.com/farolone/wellue-o2ring-protocol -- packet framing
  and file-transfer protocol docs.
- **ecostech/viatom-ble** -- https://github.com/ecostech/viatom-ble --
  client architecture (async/sync dual API, scanner lifecycle,
  live-reading byte offsets).
- **MackeyStingray/o2r** -- https://github.com/MackeyStingray/o2r -- the
  original downloader/configurator; the only one of the first three that
  documents the CMD_CONFIG write commands and the full `.vld` header
  layout this library uses.
- **viatom-develop/LepuBle** -- https://github.com/viatom-develop/LepuBle
  -- Viatom's own official low-level BLE SDK. Confirms GATT UUIDs and the
  full device family list; corrected real bugs in this library's first
  cut around the live-reading command and `.vld` header/record layouts.
- **viatom-develop/LepuDemo** --
  https://github.com/viatom-develop/LepuDemo -- Viatom's current
  higher-level SDK docs; source of the "O2Ring S is a different,
  incompatible protocol" finding, and corrected two more `CMD_CONFIG`
  value-range bugs sourced only from o2r.

## Upstream source for OxyII (O2Ring-S / T8520) support

A sixth source, added later and covering a separate protocol from the
five above:

- **nglessner/o2ring-s-protocol** --
  https://github.com/nglessner/o2ring-s-protocol -- reverse-engineered,
  MIT-licensed protocol reference (with a working Python reference
  implementation, `oxyii_protocol.py`) for the O2Ring-S (T8520)'s "OxyII"
  protocol -- a completely different GATT service/frame format/auth
  handshake/file format from the "oxy" family the five sources above
  cover (see `oxyii_const.py`'s module docstring). Verified end-to-end
  there against a real T8520: byte-exact against vendor-app file exports
  via SHA-256, live SpO2/HR streaming, and file listing/download/
  decoding. This package's `oxyii_*.py` modules were built by porting
  that repo's frame codec/CRC-8/auth-key derivation directly and writing
  a new client/command/data/file-parsing layer around them patterned
  after `client.py`'s `O2RingClient` -- see the README's
  "OxyII (O2Ring-S / T8520)" section for exactly what's ported vs.
  original, and what's deliberately not implemented (SET_CONFIG, AES,
  PPG waveform decoding, CLI integration). Worth checking for updates if
  the upstream repo resolves any of its own "Open questions" (cmd=0x10
  semantics, several GET_INFO byte offsets) or confirms behavior on
  firmware other than 2D010001/2D010002/2D010003.

## Verification status

**Work in progress -- not yet verified against real hardware** as of this
writing; see the README's warning banner. Protocol-correct-on-paper only,
built from/cross-checked against the five sources above, not from testing
against an actual O2Ring-family device.

OxyII (O2Ring-S / T8520) support is a partial exception: it's ported from
a source (nglessner/o2ring-s-protocol) that *has* verified its own
implementation against real hardware, which is a meaningfully stronger
starting point than the rest of this package had -- but the port itself,
as adapted into this package's own client/module structure, has not been
independently re-verified against a real T8520 here.
