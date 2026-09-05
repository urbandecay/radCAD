"""Persistent dependency-graph updates for associative dimensions."""

import bpy
from bpy.app.handlers import persistent

from .model import update_all_dimensions


_UPDATING = False
_OVERLAY_KEY = "radcad_dimension_overlay_handles"


@persistent
def radcad_dimension_depsgraph_update(_scene, _depsgraph):
    global _UPDATING
    if _UPDATING:
        return
    try:
        _UPDATING = True
        update_all_dimensions()
    finally:
        _UPDATING = False


@persistent
def radcad_dimension_load_post(_filepath):
    update_all_dimensions()


def _remove_named_handler(handlers, name):
    for handler in list(handlers):
        if getattr(handler, "__name__", "") == name:
            handlers.remove(handler)


def _remove_overlay_handlers():
    handles = bpy.app.driver_namespace.get(_OVERLAY_KEY, [])
    for handle, region_type in handles:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(handle, region_type)
        except (ReferenceError, RuntimeError, ValueError):
            pass
    bpy.app.driver_namespace[_OVERLAY_KEY] = []


def _register_overlay_handlers():
    from .drawing import draw_persistent_dimensions_2d

    _remove_overlay_handlers()
    handle = bpy.types.SpaceView3D.draw_handler_add(
        draw_persistent_dimensions_2d,
        (),
        "WINDOW",
        "POST_PIXEL",
    )
    bpy.app.driver_namespace[_OVERLAY_KEY] = [(handle, "WINDOW")]


def radcad_dimension_deferred_update():
    """Run migration after Blender releases its restricted registration data."""
    if not hasattr(bpy.data, "objects"):
        return 0.1
    update_all_dimensions()
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()
    return None


def register():
    _remove_named_handler(bpy.app.handlers.depsgraph_update_post, radcad_dimension_depsgraph_update.__name__)
    _remove_named_handler(bpy.app.handlers.load_post, radcad_dimension_load_post.__name__)
    bpy.app.handlers.depsgraph_update_post.append(radcad_dimension_depsgraph_update)
    bpy.app.handlers.load_post.append(radcad_dimension_load_post)
    _register_overlay_handlers()
    if not bpy.app.timers.is_registered(radcad_dimension_deferred_update):
        bpy.app.timers.register(radcad_dimension_deferred_update, first_interval=0.0)


def unregister():
    if bpy.app.timers.is_registered(radcad_dimension_deferred_update):
        bpy.app.timers.unregister(radcad_dimension_deferred_update)
    _remove_overlay_handlers()
    _remove_named_handler(bpy.app.handlers.depsgraph_update_post, radcad_dimension_depsgraph_update.__name__)
    _remove_named_handler(bpy.app.handlers.load_post, radcad_dimension_load_post.__name__)
