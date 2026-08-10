from ._version import __version__, __version_info__
from .client import O2RingClient, discover, supported
from .data import DeviceInfo, Reading, RtReading, VldHeader, VldRecord
from .file import parse as parse_vld_file
from .file import write_csv as write_vld_csv
from .oxyii_client import InsufficientMtuError, OxyIIClient, discover_oxyii, supported_oxyii
from .oxyii_data import (
    OxyIIDeviceInfo,
    OxyIIFileEntry,
    OxyIIFileHeader,
    OxyIIFileRecord,
    OxyIIReading,
)
from .oxyii_file import parse as parse_oxyii_file
from .oxyii_file import parse_filename_timestamp as parse_oxyii_filename_timestamp
from .oxyii_file import write_csv as write_oxyii_csv

__all__ = [
    "__version__",
    "__version_info__",
    "O2RingClient",
    "discover",
    "supported",
    "DeviceInfo",
    "Reading",
    "RtReading",
    "VldHeader",
    "VldRecord",
    "parse_vld_file",
    "write_vld_csv",
    # OxyII (O2Ring-S / T8520) -- a separate protocol; see oxyii_const.py
    # and this package's CLAUDE.md.
    "OxyIIClient",
    "InsufficientMtuError",
    "discover_oxyii",
    "supported_oxyii",
    "OxyIIDeviceInfo",
    "OxyIIReading",
    "OxyIIFileEntry",
    "OxyIIFileHeader",
    "OxyIIFileRecord",
    "parse_oxyii_file",
    "parse_oxyii_filename_timestamp",
    "write_oxyii_csv",
]
