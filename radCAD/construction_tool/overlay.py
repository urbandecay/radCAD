"""Lifecycle for the persistent construction-line viewport handler."""

import bpy


_OVERLAY_KEY = "radcad_construction_overlay_handles"


def _remove_overlay_handlers():
    handles = bpy.app.driver_namespace.get(_OVERLAY_KEY, [])
    for handle, region_type in handles:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(handle, region_type)
        except (ReferenceError, ValueError):
            pass
    bpy.app.driver_namespace[_OVERLAY_KEY] = []


def register():
    from .drawing import draw_persistent_construction_lines

    _remove_overlay_handlers()
    try:
        handle = bpy.types.SpaceView3D.draw_handler_add(
            draw_persistent_construction_lines,
            (),
            "WINDOW",
            "POST_PIXEL",
        )
    except (ReferenceError, RuntimeError):
        return
    bpy.app.driver_namespace[_OVERLAY_KEY] = [(handle, "WINDOW")]


def unregister():
    _remove_overlay_handlers()
