from __future__ import annotations

import uuid

addon_module_id = ""
instance_id = str(uuid.uuid4())
file_session_id = str(uuid.uuid4())
# One draw handle per SpaceType: Blender binds a draw handler to a single
# SpaceType, so covering every editor means registering (and later removing)
# one handle each. Maps SpaceType name -> handle.
overlay_handles = {}


def configure(package_name: str) -> None:
    global addon_module_id
    addon_module_id = package_name
