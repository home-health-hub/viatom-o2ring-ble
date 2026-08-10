from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from viatom_o2ring_ble import cli as cli_module
from viatom_o2ring_ble.cli import _parse_args, main


def test_parse_args_oxyii_flag_defaults_false():
    args = _parse_args(["--address", "AA:BB:CC:DD:EE:FF"])
    assert args.oxyii is False


def test_parse_args_oxyii_flag_set():
    args = _parse_args(["--oxyii", "--address", "AA:BB:CC:DD:EE:FF"])
    assert args.oxyii is True


@pytest.mark.parametrize(
    "flag",
    [
        "--legacy-sensors",
        "--set-o2-alert=90",
        "--set-hr-alert-high=140",
        "--set-hr-alert-low=45",
        "--set-vibration=50",
        "--set-screen=on",
        "--set-lighting-mode=standard",
        "--set-brightness=low",
    ],
)
def test_main_rejects_legacy_only_flags_with_oxyii(flag, capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["--oxyii", "--address", "AA:BB:CC:DD:EE:FF", flag])
    assert exc_info.value.code == 2
    assert "--oxyii" in capsys.readouterr().err


def test_main_requires_address_unless_discover():
    with pytest.raises(SystemExit) as exc_info:
        main([])
    assert exc_info.value.code == 2


def test_run_discover_dispatches_to_oxyii_scan(monkeypatch, capsys):
    fake_device = type(
        "FakeDevice", (), {"address": "AA:BB:CC:DD:EE:FF", "name": "S8-AW-1"}
    )()
    mock_discover_oxyii = AsyncMock(return_value=[fake_device])
    monkeypatch.setattr(cli_module, "discover_oxyii", mock_discover_oxyii)

    asyncio.run(cli_module._run_discover(5.0, oxyii=True))

    mock_discover_oxyii.assert_awaited_once_with(timeout=5.0)
    assert "AA:BB:CC:DD:EE:FF" in capsys.readouterr().out


def test_run_discover_dispatches_to_legacy_scan(monkeypatch, capsys):
    fake_device = type(
        "FakeDevice", (), {"address": "11:22:33:44:55:66", "name": "O2Ring"}
    )()
    mock_discover = AsyncMock(return_value=[fake_device])
    monkeypatch.setattr(cli_module, "discover", mock_discover)

    asyncio.run(cli_module._run_discover(5.0, oxyii=False))

    mock_discover.assert_awaited_once_with(timeout=5.0)
    assert "11:22:33:44:55:66" in capsys.readouterr().out
