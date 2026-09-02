"""Storage helpers for construction guides."""

from mathutils import Vector


_EPSILON = 1.0e-12


def constrain_direction_to_plane(direction, plane_normal):
    """Return a unit direction lying in the guide's drawing plane."""
    direction = Vector(direction)
    normal = Vector(plane_normal)
    if direction.length_squared <= _EPSILON:
        return None
    if normal.length_squared <= _EPSILON:
        normal = Vector((0.0, 0.0, 1.0))
    normal.normalize()
    direction -= normal * direction.dot(normal)
    if direction.length_squared <= _EPSILON:
        return None
    direction.normalize()
    return direction


def recover_legacy_plane_normal(direction, stored_normal):
    """Best-effort recovery for guides from the first implementation.

    That version stored a plane normal after projecting it perpendicular to the
    line, so the original plane was not retained. Pick the world-axis plane
    whose projected normal best matches the stored value.
    """
    direction = Vector(direction)
    stored_normal = Vector(stored_normal)
    if direction.length_squared <= _EPSILON:
        return Vector((0.0, 0.0, 1.0))
    direction.normalize()
    if stored_normal.length_squared <= _EPSILON:
        return Vector((0.0, 0.0, 1.0))
    stored_normal.normalize()

    best_axis = Vector((0.0, 0.0, 1.0))
    best_score = -1.0
    # Prefer the common ground plane when two world-axis reconstructions are
    # equally plausible (a frequent tie for diagonal legacy directions).
    for axis in (
        Vector((0.0, 0.0, 1.0)),
        Vector((1.0, 0.0, 0.0)),
        Vector((0.0, 1.0, 0.0)),
    ):
        projected = axis - direction * axis.dot(direction)
        if projected.length_squared <= _EPSILON:
            continue
        projected.normalize()
        score = abs(projected.dot(stored_normal))
        if score > best_score:
            best_score = score
            best_axis = axis
    return best_axis


def iter_construction_lines(scene):
    lines = getattr(scene, "radcad_construction_lines", None)
    return lines if lines is not None else ()


def has_visible_construction_lines(scene):
    return bool(
        getattr(scene, "radcad_construction_lines_visible", True)
        and len(iter_construction_lines(scene))
    )


def add_construction_line(scene, anchor, direction, plane_normal):
    normal = Vector(plane_normal) if plane_normal is not None else Vector((0.0, 0.0, 1.0))
    if normal.length_squared <= _EPSILON:
        normal = Vector((0.0, 0.0, 1.0))
    normal.normalize()

    direction = constrain_direction_to_plane(direction, normal)
    if direction is None:
        return None

    lines = scene.radcad_construction_lines
    line = lines.add()
    line.name = f"Construction Line {len(lines)}"
    line.anchor = Vector(anchor)
    line.direction = direction
    line.plane_normal = normal
    line.schema_version = 2
    # The GPU overlay is enough for radCAD tools, but Blender's own transform
    # snapping requires discoverable scene geometry.
    from .native_snap import sync_scene_snap_proxy

    sync_scene_snap_proxy(scene)
    if hasattr(scene, "radcad_active_construction_line"):
        scene.radcad_active_construction_line = len(lines) - 1
    return line
