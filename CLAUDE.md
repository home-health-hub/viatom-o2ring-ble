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

## Verification status

**Work in progress -- not yet verified against real hardware** as of this
writing; see the README's warning banner. Protocol-correct-on-paper only,
built from/cross-checked against the five sources above, not from testing
against an actual O2Ring-family device.
