"""Persistent and modal viewport drawing for construction guides."""

import blf
import bpy
import gpu
from bpy_extras.view3d_utils import location_3d_to_region_2d
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

from .model import iter_construction_lines
from .projection import guide_vectors, projected_visible_guide_segment


def _shader():
    try:
        return gpu.shader.from_builtin("UNIFORM_COLOR")
    except ValueError:
        return gpu.shader.from_builtin("3D_UNIFORM_COLOR")


def _draw_segments(segments, color, width):
    coordinates = []
    for start, end in segments:
        coordinates.extend((start, end))
    if not coordinates:
        return
    shader = _shader()
    gpu.state.blend_set("ALPHA")
    gpu.state.line_width_set(max(1.0, float(width)))
    shader.bind()
    shader.uniform_float("color", tuple(color))
    batch_for_shader(shader, "LINES", {"pos": coordinates}).draw(shader)
    gpu.state.line_width_set(1.0)
    gpu.state.blend_set("NONE")


def _draw_points(points, color, size):
    if not points:
        return
    shader = _shader()
    gpu.state.blend_set("ALPHA")
    gpu.state.point_size_set(max(1.0, float(size)))
    shader.bind()
    shader.uniform_float("color", tuple(color))
    batch_for_shader(shader, "POINTS", {"pos": points}).draw(shader)
    gpu.state.point_size_set(1.0)
    gpu.state.blend_set("NONE")


def _dashed_line(start, end, dash_length=9.0, gap_length=6.0):
    delta = Vector(end) - Vector(start)
    length = delta.length
    if length <= 1.0e-8:
        return []
    direction = delta / length
    segments = []
    cursor = 0.0
    while cursor < length:
        segment_end = min(length, cursor + dash_length)
        segments.append((Vector(start) + direction * cursor, Vector(start) + direction * segment_end))
        cursor += dash_length + gap_length
    return segments


def _screen_line_for_vectors(context, anchor, direction):
    return projected_visible_guide_segment(context, anchor, direction)


def _draw_guide_vectors(context, guides, color, width, dashed=True):
    segments = []
    anchors = []
    for anchor, direction, _normal in guides:
        clipped = _screen_line_for_vectors(context, anchor, direction)
        if clipped is not None:
            if dashed:
                segments.extend(_dashed_line(*clipped))
            else:
                segments.append(clipped)
        anchor_screen = location_3d_to_region_2d(
            context.region,
            context.region_data,
            anchor,
            default=None,
        )
        if anchor_screen is not None:
            anchors.append(Vector(anchor_screen))
    _draw_segments(segments, color, width)
    _draw_points(anchors, color, max(4.0, width * 2.5))


def draw_persistent_construction_lines():
    context = bpy.context
    scene = getattr(context, "scene", None)
    if (
        scene is None
        or context.area is None
        or context.area.type != "VIEW_3D"
        or context.region is None
        or context.region_data is None
        or not getattr(scene, "radcad_construction_lines_visible", True)
    ):
        return

    guides = []
    for line in iter_construction_lines(scene):
        vectors = guide_vectors(line)
        if vectors is not None:
            guides.append(vectors)
    _draw_guide_vectors(
        context,
        guides,
        getattr(scene, "radcad_construction_line_color", (0.12, 0.62, 1.0, 0.9)),
        getattr(scene, "radcad_construction_line_width", 1.5),
        dashed=True,
    )


def _draw_prompt(region, text):
    font_id = 0
    blf.size(font_id, 13)
    text_width, text_height = blf.dimensions(font_id, text)
    padding = 7.0
    x = 20.0
    y = float(region.height) - text_height - padding * 2.0 - 20.0
    vertices = (
        (x, y),
        (x + text_width + padding * 2.0, y),
        (x + text_width + padding * 2.0, y + text_height + padding * 2.0),
        (x, y + text_height + padding * 2.0),
    )
    shader = _shader()
    gpu.state.blend_set("ALPHA")
    shader.bind()
    shader.uniform_float("color", (0.08, 0.08, 0.08, 0.82))
    batch_for_shader(shader, "TRI_FAN", {"pos": vertices}).draw(shader)
    blf.color(font_id, 0.92, 0.92, 0.92, 1.0)
    blf.position(font_id, x + padding, y + padding, 0)
    blf.draw(font_id, text)
    gpu.state.blend_set("NONE")


def draw_construction_preview(operator):
    if not operator.running:
        return
    context = operator.context
    if context.region is None or context.region_data is None:
        return

    color = (0.05, 0.8, 1.0, 1.0)
    if operator.stage == 0:
        if operator.current is not None:
            point = location_3d_to_region_2d(
                context.region,
                context.region_data,
                operator.current,
                default=None,
            )
            if point is not None:
                _draw_points([Vector(point)], color, 7.0)
        _draw_prompt(context.region, "Construction Line — click anchor point")
        return

    direction = operator.current - operator.anchor
    if direction.length_squared > 1.0e-12:
        normal = (
            operator.plane_normal
            if operator.plane_normal is not None
            else Vector((0.0, 0.0, 1.0))
        )
        _draw_guide_vectors(
            context,
            [(operator.anchor, direction.normalized(), normal)],
            color,
            2.0,
            dashed=False,
        )
    _draw_prompt(context.region, "Construction Line — click to set direction   •   Backspace: previous")
