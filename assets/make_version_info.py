"""Writes version_info.txt, the Windows "Details" tab data (product name, version, description) for the exe.

build.bat and the GitHub release build run this before PyInstaller and pass the result with --version-file.
Without it the exe shows blank properties, which some antivirus heuristics treat as a bad sign.

Usage (from the project folder):  python assets\\make_version_info.py
"""
from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from version import APP_VERSION  # noqa: E402

PRODUCT = "Stream Timestamper"
DESCRIPTION = "Stream Timestamper - makes YouTube timestamp lists from stream captions (open source)"
COMPANY = "Stream Timestamper contributors (open source)"
COPYRIGHT = "MIT License. Source: github.com/ardiberen/YT-Stream-Timestamper"


def version_tuple(version: str) -> tuple[int, int, int, int]:
    nums = [int(n) for n in re.findall(r"\d+", version)][:4]
    nums += [0] * (4 - len(nums))
    return nums[0], nums[1], nums[2], nums[3]


def build_text(version: str = APP_VERSION) -> str:
    vt = version_tuple(version)
    shown = ".".join(str(n) for n in vt)
    strings = [
        ("CompanyName", COMPANY),
        ("FileDescription", DESCRIPTION),
        ("FileVersion", shown),
        ("InternalName", "StreamTimestamper"),
        ("LegalCopyright", COPYRIGHT),
        ("OriginalFilename", "StreamTimestamper.exe"),
        ("ProductName", PRODUCT),
        ("ProductVersion", shown),
    ]
    rows = ",\n          ".join(f"StringStruct({k!r}, {v!r})" for k, v in strings)
    return f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={vt!r},
    prodvers={vt!r},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [
          {rows}
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def main() -> None:
    out = os.path.join(ROOT, "version_info.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write(build_text())
    print(f"wrote {out} for version {APP_VERSION}")


if __name__ == "__main__":
    main()
