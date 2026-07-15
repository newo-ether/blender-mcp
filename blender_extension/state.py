from __future__ import annotations

import uuid

addon_module_id = ""
instance_id = str(uuid.uuid4())
file_session_id = str(uuid.uuid4())
overlay_handle = None


def configure(package_name: str) -> None:
    global addon_module_id
    addon_module_id = package_name
