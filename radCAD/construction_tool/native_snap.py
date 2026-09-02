"""Hidden helper geometry that exposes construction guides to Blender snapping.

Blender's transform tools cannot snap to radCAD's POST_PIXEL overlay.  This
module mirrors the scene-backed infinite guides into a hidden loose-edge mesh.
The overlay remains the source of truth; the mesh only exists so Blender's
native vertex and edge snapping can discover the guides.
"""

import bpy
from bpy.app.handlers import persistent
from mathutils import Vector, geometry

from .model import has_visible_construction_lines, iter_construction_lines
from .projection import guide_vectors


PROXY_TAG = "radcad_construction_snap_proxy"
_PROXY_NAME = ".radCAD Construction Snap"
_MINIMUM_HALF_SPAN = 1000.0
_EPSILON = 1.0e-10


def is_construction_snap_proxy(obj):
    return bool(obj is not None and obj.get(PROXY_TAG, False))


def _scene_proxy_objects(scene):
    return [obj for obj in scene.objects if is_construction_snap_proxy(obj)]


def _remove_object(obj):
    mesh = obj.data if obj.type == "MESH" else None
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh is not None and mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def remove_scene_snap_proxy(scene):
    for obj in _scene_proxy_objects(scene):
        _remove_object(obj)


def _clear_scene_snap_proxy(scene):
    """Empty derived geometry while keeping its evaluated object alive."""
    for obj in _scene_proxy_objects(scene):
        if obj.type == "MESH":
            obj.data.clear_geometry()
            obj.data.update()
        # An empty visible object draws nothing, but keeping its Base in the
        # active view layer means the first guide does not introduce a brand
        # new snap target halfway through Edit Mode.
        obj.hide_viewport = False


def remove_all_snap_proxies():
    for obj in list(bpy.data.objects):
        if is_construction_snap_proxy(obj):
            _remove_object(obj)


def _ensure_proxy_object(scene):
    proxies = _scene_proxy_objects(scene)
    if proxies:
        obj = proxies[0]
        for duplicate in proxies[1:]:
            _remove_object(duplicate)
        if obj.type != "MESH":
            _remove_object(obj)
            obj = None
    else:
        obj = None

    if obj is None:
        mesh = bpy.data.meshes.new(f"{_PROXY_NAME} Mesh")
        obj = bpy.data.objects.new(_PROXY_NAME, mesh)
        scene.collection.objects.link(obj)

    obj[PROXY_TAG] = True
    # Keep the proxy object available for native snapping, but hide its
    # redundant wire display. The persistent construction line is rendered by
    # the POST_PIXEL overlay and radCAD snapping uses the stored guide data.
    obj.hide_select = False
    # The proxy is a loose-edge helper and has no faces to contribute to a
    # render. Leave the datablock viewport-enabled so the Outliner eye toggle
    # is the only visibility state applied here.
    obj.hide_render = False
    obj.hide_viewport = False
    obj.display_type = "WIRE"
    # Construction guides are screen overlays and remain visible across the
    # model. Put their native snap proxy in Blender's matching in-front depth
    # group so faces cannot occlude only part of an otherwise visible guide.
    obj.show_in_front = True
    try:
        obj.hide_set(True)
    except RuntimeError:
        pass
    obj.lock_location = (True, True, True)
    obj.lock_rotation = (True, True, True)
    obj.lock_scale = (True, True, True)
    try:
        obj.select_set(False)
    except RuntimeError:
        pass
    obj.matrix_world.identity()
    # Blender 5 exposes this explicit snapping/depth-picking switch.  It is
    # absent in Blender 4.2, where visible geometry is pickable by default.
    if hasattr(obj, "hide_surface_pick"):
        obj.hide_surface_pick = False
    return obj


def _view_clip_span():
    span = _MINIMUM_HALF_SPAN
    window_manager = getattr(bpy.context, "window_manager", None)
    if window_manager is None:
        return span
    for window in window_manager.windows:
        for area in window.screen.areas:
            if area.type != "VIEW_3D":
                continue
            for space in area.spaces:
                if space.type == "VIEW_3D":
                    span = max(span, float(space.clip_end) * 1.25)
    return span


def _scene_half_span(scene, records):
    """Cover the scene and normal viewport ranges without huge float errors."""
    points = [anchor for anchor, _direction, _normal in records]
    for obj in scene.objects:
        if is_construction_snap_proxy(obj):
            continue
        try:
            points.extend(obj.matrix_world @ Vector(corner) for corner in obj.bound_box)
        except (AttributeError, TypeError):
            continue

    extent = 0.0
    if len(points) > 1:
        minimum = Vector((
            min(point.x for point in points),
            min(point.y for point in points),
            min(point.z for point in points),
        ))
        maximum = Vector((
            max(point.x for point in points),
            max(point.y for point in points),
            max(point.z for point in points),
        ))
        extent = (maximum - minimum).length
    return max(_view_clip_span(), extent * 4.0, _MINIMUM_HALF_SPAN)


def _guide_intersection_parameters(records):
    parameters = [[0.0] for _record in records]
    for first_index, (anchor_a, direction_a, _normal_a) in enumerate(records):
        for second_index in range(first_index + 1, len(records)):
            anchor_b, direction_b, _normal_b = records[second_index]
            pair = geometry.intersect_line_line(
                anchor_a,
                anchor_a + direction_a,
                anchor_b,
                anchor_b + direction_b,
            )
            if pair is None:
                continue
            point_a, point_b = pair
            scale = max(1.0, anchor_a.length, anchor_b.length)
            if (point_a - point_b).length > scale * 1.0e-7:
                continue
            point = (point_a + point_b) * 0.5
            parameters[first_index].append((point - anchor_a).dot(direction_a))
            parameters[second_index].append((point - anchor_b).dot(direction_b))
    return parameters


def _unique_sorted(values, tolerance):
    result = []
    for value in sorted(values):
        if not result or abs(value - result[-1]) > tolerance:
            result.append(value)
    return result


def _proxy_geometry(records, half_span):
    vertices = []
    edges = []
    intersection_parameters = _guide_intersection_parameters(records)
    tolerance = max(_EPSILON, half_span * 1.0e-9)

    for (anchor, direction, _normal), parameters in zip(records, intersection_parameters):
        parameters.extend((-half_span, half_span))
        parameters = _unique_sorted(
            (value for value in parameters if -half_span <= value <= half_span),
            tolerance,
        )
        first_vertex = len(vertices)
        vertices.extend(anchor + direction * value for value in parameters)
        edges.extend(
            (first_vertex + index, first_vertex + index + 1)
            for index in range(len(parameters) - 1)
        )
    return vertices, edges


def sync_scene_snap_proxy(scene):
    """Rebuild the native snap mesh from the guides stored on *scene*."""
    if scene is None or not hasattr(scene, "radcad_construction_lines"):
        return None

    # Create the empty proxy as soon as radCAD registers, before the user
    # normally enters Edit Mode. Later guide changes only rebuild mesh data on
    # this already-evaluated object, which Blender's transform snapper sees
    # without a magnet/camera visibility toggle.
    obj = _ensure_proxy_object(scene)

    records = []
    for line in iter_construction_lines(scene):
        vectors = guide_vectors(line)
        if vectors is not None:
            records.append(vectors)

    if not records:
        _clear_scene_snap_proxy(scene)
        return obj

    enabled = has_visible_construction_lines(scene)
    obj.hide_viewport = not enabled
    if not enabled:
        return obj

    half_span = _scene_half_span(scene, records)
    vertices, edges = _proxy_geometry(records, half_span)
    mesh = obj.data
    mesh.clear_geometry()
    mesh.from_pydata(vertices, edges, [])
    mesh.update(calc_edges=True)
    obj.update_tag()
    color = tuple(getattr(scene, "radcad_construction_line_color", (1.0, 1.0, 1.0, 1.0)))
    obj.color = color
    obj["radcad_construction_snap_half_span"] = half_span
    return obj


def sync_all_snap_proxies():
    if not hasattr(bpy.data, "scenes"):
        return
    for scene in bpy.data.scenes:
        sync_scene_snap_proxy(scene)


@persistent
def radcad_construction_load_post(_filepath):
    sync_all_snap_proxies()


@persistent
def radcad_construction_undo_post(_scene):
    sync_all_snap_proxies()


@persistent
def radcad_construction_redo_post(_scene):
    sync_all_snap_proxies()


def _remove_named_handler(handlers, name):
    for handler in list(handlers):
        if getattr(handler, "__name__", "") == name:
            handlers.remove(handler)


def radcad_construction_deferred_sync():
    if not hasattr(bpy.data, "objects"):
        return 0.1
    sync_all_snap_proxies()
    return None


def _unregister_legacy_snap_guard():
    """Stop the old timer that forced Blender's magnet back on."""
    # importlib.reload keeps names that disappeared from a module's source.
    # Looking it up dynamically lets this version remove the already-running
    # function object without defining or restarting that guard.
    legacy_guard = globals().get("radcad_construction_snap_guard")
    if legacy_guard is None:
        return
    if bpy.app.timers.is_registered(legacy_guard):
        bpy.app.timers.unregister(legacy_guard)


def register():
    _unregister_legacy_snap_guard()
    handler_specs = (
        (bpy.app.handlers.load_post, radcad_construction_load_post),
        (bpy.app.handlers.undo_post, radcad_construction_undo_post),
        (bpy.app.handlers.redo_post, radcad_construction_redo_post),
    )
    for handlers, handler in handler_specs:
        _remove_named_handler(handlers, handler.__name__)
        handlers.append(handler)
    # Repair existing proxies immediately during a normal add-on reload. The
    # timer remains as the fallback for Blender's restricted startup phase.
    try:
        sync_all_snap_proxies()
    except (AttributeError, RuntimeError):
        pass
    if not bpy.app.timers.is_registered(radcad_construction_deferred_sync):
        bpy.app.timers.register(radcad_construction_deferred_sync, first_interval=0.0)


def unregister():
    _unregister_legacy_snap_guard()
    if bpy.app.timers.is_registered(radcad_construction_deferred_sync):
        bpy.app.timers.unregister(radcad_construction_deferred_sync)
    handler_specs = (
        (bpy.app.handlers.load_post, radcad_construction_load_post),
        (bpy.app.handlers.undo_post, radcad_construction_undo_post),
        (bpy.app.handlers.redo_post, radcad_construction_redo_post),
    )
    for handlers, handler in handler_specs:
        _remove_named_handler(handlers, handler.__name__)
    remove_all_snap_proxies()
