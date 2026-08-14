"""Storage helpers for construction guides."""

from mathutils import Vector


_EPSILON = 1.0e-12


def iter_construction_lines(scene):
    lines = getattr(scene, "radcad_construction_lines", None)
    return lines if lines is not None else ()


def has_visible_construction_lines(scene):
    return bool(
        getattr(scene, "radcad_construction_lines_visible", True)
        and len(iter_construction_lines(scene))
    )


def add_construction_line(scene, anchor, direction, plane_normal):
    direction = Vector(direction)
    if direction.length_squared <= _EPSILON:
        return None
    direction.normalize()

    normal = Vector(plane_normal) if plane_normal is not None else Vector((0.0, 0.0, 1.0))
    if normal.length_squared <= _EPSILON:
        normal = Vector((0.0, 0.0, 1.0))
    normal -= direction * normal.dot(direction)
    if normal.length_squared <= _EPSILON:
        reference = min(
            (Vector((1.0, 0.0, 0.0)), Vector((0.0, 1.0, 0.0)), Vector((0.0, 0.0, 1.0))),
            key=lambda axis: abs(axis.dot(direction)),
        )
        normal = direction.cross(reference)
    normal.normalize()

    lines = scene.radcad_construction_lines
    line = lines.add()
    line.name = f"Construction Line {len(lines)}"
    line.anchor = Vector(anchor)
    line.direction = direction
    line.plane_normal = normal
    return line
