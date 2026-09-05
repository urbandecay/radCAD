"""Persistent dependency-graph updates for associative dimensions."""

import bpy
from bpy.app.handlers import persistent

from . import debug
from .model import update_all_dimensions


_UPDATING = False
_OVERLAY_KEY = "radcad_dimension_overlay_handles"
_LEGACY_OVERLAY_KEYS = ("radcad_handles",)


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


def _remove_legacy_overlay_handlers():
    """Remove handles left by pre-registry versions of the add-on."""
    namespace = bpy.app.driver_namespace
    for key in _LEGACY_OVERLAY_KEYS:
        handles = namespace.get(key)
        if not isinstance(handles, (list, tuple)) or not handles:
            continue
        removed = []
        for entry in list(handles):
            if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                continue
            handle, region_type = entry[0], entry[1]
            try:
                bpy.types.SpaceView3D.draw_handler_remove(handle, region_type)
                removed.append(repr(handle))
            except (ReferenceError, RuntimeError, ValueError):
                pass
        namespace[key] = []
        debug.log(
            "legacy_handler_cleanup",
            key=key,
            handles=removed,
        )


def _remove_overlay_handlers():
    handles = bpy.app.driver_namespace.get(_OVERLAY_KEY, [])
    debug.invalidate_persistent_renderer("remove_overlay_handlers")
    _remove_legacy_overlay_handlers()
    debug.log(
        "persistent_handler_remove_begin",
        handles=[repr(value) for value in handles] if isinstance(handles, list) else repr(handles),
    )
    for handle, region_type in handles:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(handle, region_type)
        except (ReferenceError, RuntimeError, ValueError):
            pass
    bpy.app.driver_namespace[_OVERLAY_KEY] = []
    debug.log("persistent_handler_remove_end", handlers=debug.handler_snapshot())


def _register_overlay_handlers():
    from .drawing import draw_persistent_dimensions_2d

    debug.retire_stale_dimension_draw_callbacks(draw_persistent_dimensions_2d)
    _remove_overlay_handlers()
    try:
        handle = bpy.types.SpaceView3D.draw_handler_add(
            draw_persistent_dimensions_2d,
            (),
            "WINDOW",
            "POST_PIXEL",
        )
    except (ReferenceError, RuntimeError, ValueError) as error:
        debug.log("persistent_handler_register_error", error=error)
        return
    bpy.app.driver_namespace[_OVERLAY_KEY] = [(handle, "WINDOW")]
    debug.activate_persistent_renderer(draw_persistent_dimensions_2d)
    debug.log(
        "persistent_handler_register",
        handle=repr(handle),
        callback=f"{draw_persistent_dimensions_2d.__module__}.{draw_persistent_dimensions_2d.__name__}",
        handlers=debug.handler_snapshot(),
    )


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
    debug.log("dimension_updater_register_begin", handlers=debug.handler_snapshot())
    _remove_named_handler(bpy.app.handlers.depsgraph_update_post, radcad_dimension_depsgraph_update.__name__)
    _remove_named_handler(bpy.app.handlers.load_post, radcad_dimension_load_post.__name__)
    bpy.app.handlers.depsgraph_update_post.append(radcad_dimension_depsgraph_update)
    bpy.app.handlers.load_post.append(radcad_dimension_load_post)
    _register_overlay_handlers()
    if not bpy.app.timers.is_registered(radcad_dimension_deferred_update):
        bpy.app.timers.register(radcad_dimension_deferred_update, first_interval=0.0)
    debug.log("dimension_updater_register_end", handlers=debug.handler_snapshot())


def unregister():
    debug.log("dimension_updater_unregister_begin", handlers=debug.handler_snapshot())
    if bpy.app.timers.is_registered(radcad_dimension_deferred_update):
        bpy.app.timers.unregister(radcad_dimension_deferred_update)
    _remove_overlay_handlers()
    _remove_named_handler(bpy.app.handlers.depsgraph_update_post, radcad_dimension_depsgraph_update.__name__)
    _remove_named_handler(bpy.app.handlers.load_post, radcad_dimension_load_post.__name__)
    debug.log("dimension_updater_unregister_end", handlers=debug.handler_snapshot())
