"""Shared Blender Extension namespace for integration fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import blender_extension as _extension

for _name in dir(_extension):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_extension, _name)
