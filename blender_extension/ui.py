from __future__ import annotations

from contextlib import suppress

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    StringProperty,
)

from . import state
from .bridge.constants import RODIN_FREE_TRIAL_KEY
from .bridge.server import BlenderMCPServer
from .lifecycle import (
    _auto_connect_if_enabled,
    _load_post_handler,
    _make_scene_update,
    sync_prefs_to_scene,
)
from .preferences import get_blendermcp_addon_preferences
from .runtime import (
    OVERLAY_SPACE_TYPES,
    draw_occupancy_border,
    on_allow_ai_control_changed,
)


class BLENDERMCP_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = state.addon_module_id

    # ── General ──
    telemetry_consent: BoolProperty(
        name="Allow Telemetry",
        description="Allow collection of prompts, code, and screenshots",
        default=True
    )
    auto_connect: BoolProperty(
        name="Auto-connect on startup",
        description="Automatically start MCP server when Blender opens or loads a file",
        default=True
    )
    allow_ai_control: BoolProperty(
        name="Allow AI control",
        description="Allow an MCP client to claim and modify this Blender instance",
        default=True,
        update=on_allow_ai_control_changed,
    )
    # ── Poly Haven ──
    use_polyhaven: BoolProperty(
        name="Use Poly Haven",
        description="Enable Poly Haven asset integration",
        default=False
    )
    # ── Hyper3D Rodin ──
    use_hyper3d: BoolProperty(name="Use Hyper3D Rodin", default=False)
    hyper3d_mode: EnumProperty(name="Rodin Mode", items=[
        ("MAIN_SITE", "hyper3d.ai", ""), ("FAL_AI", "fal.ai", ""),
    ], default="MAIN_SITE")
    hyper3d_api_key: StringProperty(name="API Key", subtype="PASSWORD", default="")
    # ── Sketchfab ──
    use_sketchfab: BoolProperty(name="Use Sketchfab", default=False)
    sketchfab_api_key: StringProperty(name="API Key", subtype="PASSWORD", default="")
    # ── Hunyuan3D ──
    use_hunyuan3d: BoolProperty(name="Use Hunyuan3D", default=False)
    hunyuan3d_mode: EnumProperty(name="Mode", items=[
        ("LOCAL_API", "Local API", ""), ("OFFICIAL_API", "Official API", ""),
    ], default="LOCAL_API")
    hunyuan3d_secret_id: StringProperty(name="SecretId", default="")
    hunyuan3d_secret_key: StringProperty(name="SecretKey", subtype="PASSWORD", default="")
    hunyuan3d_api_url: StringProperty(name="API URL", default="http://localhost:8081")
    hunyuan3d_octree_resolution: IntProperty(name="Octree Resolution", default=256, min=128, max=512)
    hunyuan3d_num_inference_steps: IntProperty(name="Inference Steps", default=30, min=20, max=50)
    hunyuan3d_guidance_scale: FloatProperty(name="Guidance Scale", default=5.5, min=1.0, max=10.0)
    hunyuan3d_texture: BoolProperty(name="Generate Texture", default=True)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "telemetry_consent")
        layout.prop(self, "auto_connect")
        layout.prop(self, "allow_ai_control")
        layout.separator()
        layout.prop(self, "use_polyhaven")
        layout.prop(self, "use_hyper3d")
        if self.use_hyper3d:
            box = layout.box()
            box.prop(self, "hyper3d_mode")
            box.prop(self, "hyper3d_api_key")
        layout.prop(self, "use_sketchfab")
        if self.use_sketchfab:
            layout.prop(self, "sketchfab_api_key")
        layout.separator()
        layout.prop(self, "use_hunyuan3d")
        if self.use_hunyuan3d:
            box = layout.box()
            box.prop(self, "hunyuan3d_mode")
            if self.hunyuan3d_mode == 'OFFICIAL_API':
                box.prop(self, "hunyuan3d_secret_id")
                box.prop(self, "hunyuan3d_secret_key")
            if self.hunyuan3d_mode == 'LOCAL_API':
                box.prop(self, "hunyuan3d_api_url")
                box.prop(self, "hunyuan3d_octree_resolution")
                box.prop(self, "hunyuan3d_num_inference_steps")
                box.prop(self, "hunyuan3d_guidance_scale")
                box.prop(self, "hunyuan3d_texture")
        layout.separator()
        layout.operator("blendermcp.open_terms", text="View Terms and Conditions", icon='TEXT')

        layout.separator()
        layout.label(text="Persistent API Credentials:", icon='LOCKED')
        cred_box = layout.box()
        cred_box.prop(self, "sketchfab_api_key", text="Sketchfab API Key")
        cred_box.prop(self, "hyper3d_api_key", text="Hyper3D API Key")
        cred_box.prop(self, "hunyuan3d_secret_id", text="Hunyuan3D SecretId")
        cred_box.prop(self, "hunyuan3d_secret_key", text="Hunyuan3D SecretKey")
        cred_box.prop(self, "hunyuan3d_api_url", text="Hunyuan3D API URL")

class BLENDERMCP_PT_Panel(bpy.types.Panel):
    bl_label = "Blender MCP"
    bl_idname = "BLENDERMCP_PT_Panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'BlenderMCP'

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        prefs = get_blendermcp_addon_preferences(context)

        layout.prop(scene, "blendermcp_allow_ai_control", text="Allow AI control")
        layout.prop(scene, "blendermcp_use_polyhaven", text="Use assets from Poly Haven")

        layout.prop(scene, "blendermcp_use_hyper3d", text="Use Hyper3D Rodin 3D model generation")
        if scene.blendermcp_use_hyper3d:
            layout.prop(scene, "blendermcp_hyper3d_mode", text="Rodin Mode")
            if prefs:
                layout.prop(prefs, "hyper3d_api_key", text="API Key")
            else:
                layout.prop(scene, "blendermcp_hyper3d_api_key", text="API Key")
            layout.operator("blendermcp.set_hyper3d_free_trial_api_key", text="Set Free Trial API Key")

        layout.prop(scene, "blendermcp_use_sketchfab", text="Use assets from Sketchfab")
        if scene.blendermcp_use_sketchfab:
            if prefs:
                layout.prop(prefs, "sketchfab_api_key", text="API Key")
            else:
                layout.prop(scene, "blendermcp_sketchfab_api_key", text="API Key")

        layout.prop(scene, "blendermcp_use_hunyuan3d", text="Use Tencent Hunyuan 3D model generation")
        if scene.blendermcp_use_hunyuan3d:
            layout.prop(scene, "blendermcp_hunyuan3d_mode", text="Hunyuan3D Mode")
            if scene.blendermcp_hunyuan3d_mode == 'OFFICIAL_API':
                if prefs:
                    layout.prop(prefs, "hunyuan3d_secret_id", text="SecretId")
                    layout.prop(prefs, "hunyuan3d_secret_key", text="SecretKey")
                else:
                    layout.prop(scene, "blendermcp_hunyuan3d_secret_id", text="SecretId")
                    layout.prop(scene, "blendermcp_hunyuan3d_secret_key", text="SecretKey")
            if scene.blendermcp_hunyuan3d_mode == 'LOCAL_API':
                if prefs:
                    layout.prop(prefs, "hunyuan3d_api_url", text="API URL")
                else:
                    layout.prop(scene, "blendermcp_hunyuan3d_api_url", text="API URL")
                layout.prop(scene, "blendermcp_hunyuan3d_octree_resolution", text="Octree Resolution")
                layout.prop(scene, "blendermcp_hunyuan3d_num_inference_steps", text="Number of Inference Steps")
                layout.prop(scene, "blendermcp_hunyuan3d_guidance_scale", text="Guidance Scale")
                layout.prop(scene, "blendermcp_hunyuan3d_texture", text="Generate Texture")

        if not scene.blendermcp_server_running:
            layout.operator("blendermcp.start_server", text="Start MCP connection")
        else:
            server = getattr(bpy.types, "blendermcp_server", None)
            if server and server.has_live_claim():
                layout.operator("blendermcp.release_ai_control", text="Release AI control", icon='CANCEL')
            layout.operator("blendermcp.stop_server", text="Stop MCP connection")

class BLENDERMCP_OT_SetFreeTrialHyper3DAPIKey(bpy.types.Operator):
    bl_idname = "blendermcp.set_hyper3d_free_trial_api_key"
    bl_label = "Set Free Trial API Key"

    def execute(self, context):
        prefs = get_blendermcp_addon_preferences(context)
        if prefs:
            if not prefs.hyper3d_api_key or prefs.hyper3d_api_key == RODIN_FREE_TRIAL_KEY:
                prefs.hyper3d_api_key = RODIN_FREE_TRIAL_KEY
            else:
                self.report(
                    {'INFO'},
                    "Using free trial for this session only; saved private key was kept."
                )
        context.scene.blendermcp_hyper3d_api_key = RODIN_FREE_TRIAL_KEY
        context.scene.blendermcp_hyper3d_mode = 'MAIN_SITE'
        self.report({'INFO'}, "API Key set successfully!")
        return {'FINISHED'}

class BLENDERMCP_OT_StartServer(bpy.types.Operator):
    bl_idname = "blendermcp.start_server"
    bl_label = "Start MCP connection"
    bl_description = "Start automatic local Blender MCP registration"

    def execute(self, context):
        scene = context.scene

        # Create a new server instance
        if not hasattr(bpy.types, "blendermcp_server") or not bpy.types.blendermcp_server:
            bpy.types.blendermcp_server = BlenderMCPServer()

        # Start the server
        bpy.types.blendermcp_server.start()
        scene.blendermcp_server_running = bpy.types.blendermcp_server.running

        return {'FINISHED'}

class BLENDERMCP_OT_StopServer(bpy.types.Operator):
    bl_idname = "blendermcp.stop_server"
    bl_label = "Stop MCP connection"
    bl_description = "Stop the local Blender MCP registration"

    def execute(self, context):
        scene = context.scene

        # Stop the server if it exists
        if hasattr(bpy.types, "blendermcp_server") and bpy.types.blendermcp_server:
            bpy.types.blendermcp_server.stop()
            del bpy.types.blendermcp_server

        scene.blendermcp_server_running = False

        return {'FINISHED'}

class BLENDERMCP_OT_ReleaseAIControl(bpy.types.Operator):
    bl_idname = "blendermcp.release_ai_control"
    bl_label = "Release AI control"
    bl_description = "Revoke the current AI claim without stopping the MCP connection"

    def execute(self, _context):
        server = getattr(bpy.types, "blendermcp_server", None)
        if server:
            server.revoke_claim("claim_revoked_by_user")
        self.report({'INFO'}, "AI control released")
        return {'FINISHED'}

class BLENDERMCP_OT_OpenTerms(bpy.types.Operator):
    bl_idname = "blendermcp.open_terms"
    bl_label = "View Terms and Conditions"
    bl_description = "Open the Terms and Conditions document"

    def execute(self, context):
        # Open the Terms and Conditions on GitHub
        terms_url = "https://github.com/ahujasid/blender-mcp/blob/main/TERMS_AND_CONDITIONS.md"
        try:
            import webbrowser
            webbrowser.open(terms_url)
            self.report({'INFO'}, "Terms and Conditions opened in browser")
        except Exception as e:
            self.report({'ERROR'}, f"Could not open Terms and Conditions: {str(e)}")

        return {'FINISHED'}

def _register_overlay_handlers():
    """Draw the occupancy overlay in every editor the add-on can reach.

    Blender binds a draw handler to one SpaceType and clips its callback to that
    region, so there is no way to draw a single window-wide frame; each editor
    must register its own. A SpaceType absent from this Blender build is skipped
    rather than allowed to break registration.
    """
    for space_name in OVERLAY_SPACE_TYPES:
        if space_name in state.overlay_handles:
            continue
        space_type = getattr(bpy.types, space_name, None)
        if space_type is None or not hasattr(space_type, "draw_handler_add"):
            continue
        try:
            state.overlay_handles[space_name] = space_type.draw_handler_add(
                draw_occupancy_border,
                (),
                'WINDOW',
                'POST_PIXEL',
            )
        except Exception:
            continue


def _unregister_overlay_handlers():
    for space_name, handle in list(state.overlay_handles.items()):
        space_type = getattr(bpy.types, space_name, None)
        if space_type is not None:
            with suppress(Exception):
                space_type.draw_handler_remove(handle, 'WINDOW')
        state.overlay_handles.pop(space_name, None)


def register():
    # Scene properties with update callbacks that sync to persistent AddonPreferences
    U = lambda name: _make_scene_update(name)  # shorthand

    bpy.types.Scene.blendermcp_server_running = bpy.props.BoolProperty(
        name="Server Running", default=False
    )

    bpy.types.Scene.blendermcp_allow_ai_control = bpy.props.BoolProperty(
        name="Allow AI control",
        description="Allow an MCP client to claim and modify this Blender instance",
        default=True,
        update=U('allow_ai_control'),
    )

    bpy.types.Scene.blendermcp_use_polyhaven = bpy.props.BoolProperty(
        name="Use Poly Haven", description="Enable Poly Haven asset integration",
        default=False, update=U('use_polyhaven')
    )

    bpy.types.Scene.blendermcp_use_hyper3d = bpy.props.BoolProperty(
        name="Use Hyper3D Rodin", description="Enable Hyper3D Rodin generation integration",
        default=False, update=U('use_hyper3d')
    )

    bpy.types.Scene.blendermcp_hyper3d_mode = bpy.props.EnumProperty(
        name="Rodin Mode", description="Choose the platform used to call Rodin APIs",
        items=[("MAIN_SITE", "hyper3d.ai", "hyper3d.ai"), ("FAL_AI", "fal.ai", "fal.ai")],
        default="MAIN_SITE", update=U('hyper3d_mode')
    )

    bpy.types.Scene.blendermcp_hyper3d_api_key = bpy.props.StringProperty(
        name="Hyper3D API Key", subtype="PASSWORD",
        description="API Key provided by Hyper3D",
        default="", update=U('hyper3d_api_key')
    )

    bpy.types.Scene.blendermcp_use_hunyuan3d = bpy.props.BoolProperty(
        name="Use Hunyuan 3D", description="Enable Hunyuan asset integration",
        default=False, update=U('use_hunyuan3d')
    )

    bpy.types.Scene.blendermcp_hunyuan3d_mode = bpy.props.EnumProperty(
        name="Hunyuan3D Mode", description="Choose local or official API",
        items=[("LOCAL_API", "local api", "local api"), ("OFFICIAL_API", "official api", "official api")],
        default="LOCAL_API", update=U('hunyuan3d_mode')
    )

    bpy.types.Scene.blendermcp_hunyuan3d_secret_id = bpy.props.StringProperty(
        name="Hunyuan 3D SecretId", description="SecretId provided by Hunyuan 3D",
        default="", update=U('hunyuan3d_secret_id')
    )

    bpy.types.Scene.blendermcp_hunyuan3d_secret_key = bpy.props.StringProperty(
        name="Hunyuan 3D SecretKey", subtype="PASSWORD",
        description="SecretKey provided by Hunyuan 3D",
        default="", update=U('hunyuan3d_secret_key')
    )

    bpy.types.Scene.blendermcp_hunyuan3d_api_url = bpy.props.StringProperty(
        name="API URL", description="URL of the Hunyuan 3D API service",
        default="http://localhost:8081", update=U('hunyuan3d_api_url')
    )

    bpy.types.Scene.blendermcp_hunyuan3d_octree_resolution = bpy.props.IntProperty(
        name="Octree Resolution", description="Octree resolution for 3D generation",
        default=256, min=128, max=512, update=U('hunyuan3d_octree_resolution')
    )

    bpy.types.Scene.blendermcp_hunyuan3d_num_inference_steps = bpy.props.IntProperty(
        name="Number of Inference Steps", description="Number of inference steps for 3D generation",
        default=30, min=20, max=50, update=U('hunyuan3d_num_inference_steps')
    )

    bpy.types.Scene.blendermcp_hunyuan3d_guidance_scale = bpy.props.FloatProperty(
        name="Guidance Scale", description="Guidance scale for 3D generation",
        default=5.5, min=1.0, max=10.0, update=U('hunyuan3d_guidance_scale')
    )

    bpy.types.Scene.blendermcp_hunyuan3d_texture = bpy.props.BoolProperty(
        name="Generate Texture", description="Whether to generate texture for the 3D model",
        default=True, update=U('hunyuan3d_texture')
    )

    bpy.types.Scene.blendermcp_use_sketchfab = bpy.props.BoolProperty(
        name="Use Sketchfab", description="Enable Sketchfab asset integration",
        default=False, update=U('use_sketchfab')
    )

    bpy.types.Scene.blendermcp_sketchfab_api_key = bpy.props.StringProperty(
        name="Sketchfab API Key", subtype="PASSWORD",
        description="API Key provided by Sketchfab",
        default="", update=U('sketchfab_api_key')
    )

    # Register all classes
    bpy.utils.register_class(BLENDERMCP_AddonPreferences)
    bpy.utils.register_class(BLENDERMCP_PT_Panel)
    bpy.utils.register_class(BLENDERMCP_OT_SetFreeTrialHyper3DAPIKey)
    bpy.utils.register_class(BLENDERMCP_OT_StartServer)
    bpy.utils.register_class(BLENDERMCP_OT_StopServer)
    bpy.utils.register_class(BLENDERMCP_OT_ReleaseAIControl)
    bpy.utils.register_class(BLENDERMCP_OT_OpenTerms)

    _register_overlay_handlers()

    # Register load_post handler for persistence + auto-connect
    bpy.app.handlers.load_post.append(_load_post_handler)

    # One-shot timer: sync prefs→scene and auto-connect on initial Blender startup
    def _startup_sync():
        sync_prefs_to_scene()
        _auto_connect_if_enabled()
    bpy.app.timers.register(_startup_sync, first_interval=0.5)

    preferences = get_blendermcp_addon_preferences()
    auto_connect = bool(preferences and preferences.auto_connect)
    print(
        "BlenderMCP addon registered (auto-connect: "
        + ("on" if auto_connect else "off")
        + ")"
    )

def unregister():
    # Remove load_post handler
    if _load_post_handler in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_load_post_handler)

    # Stop the server if it's running
    if hasattr(bpy.types, "blendermcp_server") and bpy.types.blendermcp_server:
        bpy.types.blendermcp_server.stop()
        del bpy.types.blendermcp_server

    _unregister_overlay_handlers()

    bpy.utils.unregister_class(BLENDERMCP_PT_Panel)
    bpy.utils.unregister_class(BLENDERMCP_OT_SetFreeTrialHyper3DAPIKey)
    bpy.utils.unregister_class(BLENDERMCP_OT_StartServer)
    bpy.utils.unregister_class(BLENDERMCP_OT_StopServer)
    bpy.utils.unregister_class(BLENDERMCP_OT_ReleaseAIControl)
    bpy.utils.unregister_class(BLENDERMCP_OT_OpenTerms)
    bpy.utils.unregister_class(BLENDERMCP_AddonPreferences)

    del bpy.types.Scene.blendermcp_server_running
    del bpy.types.Scene.blendermcp_allow_ai_control
    del bpy.types.Scene.blendermcp_use_polyhaven
    del bpy.types.Scene.blendermcp_use_hyper3d
    del bpy.types.Scene.blendermcp_hyper3d_mode
    del bpy.types.Scene.blendermcp_hyper3d_api_key
    del bpy.types.Scene.blendermcp_use_sketchfab
    del bpy.types.Scene.blendermcp_sketchfab_api_key
    del bpy.types.Scene.blendermcp_use_hunyuan3d
    del bpy.types.Scene.blendermcp_hunyuan3d_mode
    del bpy.types.Scene.blendermcp_hunyuan3d_secret_id
    del bpy.types.Scene.blendermcp_hunyuan3d_secret_key
    del bpy.types.Scene.blendermcp_hunyuan3d_api_url
    del bpy.types.Scene.blendermcp_hunyuan3d_octree_resolution
    del bpy.types.Scene.blendermcp_hunyuan3d_num_inference_steps
    del bpy.types.Scene.blendermcp_hunyuan3d_guidance_scale
    del bpy.types.Scene.blendermcp_hunyuan3d_texture

    print("BlenderMCP addon unregistered")
