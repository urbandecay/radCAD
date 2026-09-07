"""Persistent and modal viewport drawing for construction guides."""

import blf
import bpy
import gpu
from bpy_extras.view3d_utils import location_3d_to_region_2d
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

from ..hud_overlay import draw_ui_box_generic
from ..modal_state import state
from ..units_utils import format_length
from .model import iter_construction_lines
from .projection import guide_vectors, projected_visible_guide_segment


_MOVE_PREVIEW = None
_MOVE_DISTANCE_INPUT = None


def set_construction_move_preview(start, end):
    """Set the runtime travel indicator used while moving a guide."""
    global _MOVE_PREVIEW
    _MOVE_PREVIEW = (Vector(start), Vector(end))


def clear_construction_move_preview():
    """Remove the runtime travel indicator after a guide move ends."""
    global _MOVE_PREVIEW
    _MOVE_PREVIEW = None


def set_construction_move_distance_input(text, cursor):
    """Set the temporary typed-distance text shown during a guide move."""
    global _MOVE_DISTANCE_INPUT
    _MOVE_DISTANCE_INPUT = (str(text), int(cursor))


def clear_construction_move_distance_input():
    """Remove the temporary typed-distance text after a guide move ends."""
    global _MOVE_DISTANCE_INPUT
    _MOVE_DISTANCE_INPUT = None


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
    dash_length = max(1.0, float(dash_length))
    gap_length = max(0.0, float(gap_length))
    if gap_length <= 1.0e-8:
        return [(Vector(start), Vector(end))]
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


def _draw_guide_vectors(
    context,
    guides,
    color,
    width,
    dashed=True,
    draw_anchors=True,
    dash_length=9.0,
    gap_length=6.0,
):
    segments = []
    anchors = []
    for anchor, direction, _normal in guides:
        clipped = _screen_line_for_vectors(context, anchor, direction)
        if clipped is not None:
            if dashed:
                segments.extend(
                    _dashed_line(
                        *clipped,
                        dash_length=dash_length,
                        gap_length=gap_length,
                    )
                )
            else:
                segments.append(clipped)
        if draw_anchors:
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


def _draw_move_preview(context):
    if _MOVE_PREVIEW is None:
        return

    start, end = _MOVE_PREVIEW
    distance = (end - start).length
    if distance <= 1.0e-8 and _MOVE_DISTANCE_INPUT is None:
        return

    projected = []
    for point in (start, end):
        screen = location_3d_to_region_2d(
            context.region,
            context.region_data,
            point,
            default=None,
        )
        if screen is not None:
            projected.append(Vector(screen))
    if not projected:
        return

    color = (0.05, 0.8, 1.0, 1.0)
    if len(projected) == 2 and distance > 1.0e-8:
        # Match the creation preview: the connector shows the direction and
        # distance traveled from the original guide position to the live one.
        _draw_segments(
            _dashed_line(projected[0], projected[1], dash_length=5.0, gap_length=4.0),
            color,
            1.0,
        )
        _draw_points(projected, color, 5.0)

    scale = getattr(context.scene.unit_settings, "scale_length", 1.0) or 1.0
    if _MOVE_DISTANCE_INPUT is None:
        label = format_length(distance * scale)
        input_active = False
    else:
        typed, cursor = _MOVE_DISTANCE_INPUT
        label = f"{typed[:cursor]}|{typed[cursor:]}"
        input_active = True
    draw_ui_box_generic(
        projected[-1].x + state.get("overlay_offset_x", 25),
        projected[-1].y + state.get("overlay_offset_y", 10),
        label,
        active=input_active,
    )


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
    selected_guides = []
    selected_index = getattr(scene, "radcad_active_construction_line", -1)
    for index, line in enumerate(iter_construction_lines(scene)):
        vectors = guide_vectors(line)
        if vectors is not None:
            if line.selected or index == selected_index:
                selected_guides.append(vectors)
            else:
                guides.append(vectors)
    color = getattr(scene, "radcad_construction_line_color", (1.0, 1.0, 1.0, 1.0))
    width = getattr(scene, "radcad_construction_line_width", 1.0)
    dash_length = getattr(scene, "radcad_construction_dash_length", 9.0)
    gap_length = getattr(scene, "radcad_construction_dash_gap", 6.0)
    _draw_guide_vectors(
        context,
        guides,
        color,
        width,
        dashed=True,
        draw_anchors=False,
        dash_length=dash_length,
        gap_length=gap_length,
    )
    _draw_guide_vectors(
        context,
        selected_guides,
        (1.0, 0.48, 0.0, 1.0),
        max(1.0, float(width) + 1.0),
        dashed=True,
        draw_anchors=False,
        dash_length=dash_length,
        gap_length=gap_length,
    )
    _draw_move_preview(context)


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


def _draw_live_distance(context, operator):
    input_active = getattr(operator, "distance_input_active", False)
    if (not operator.preview_label and not input_active) or operator.current is None:
        return

    label_point = operator.source_point if input_active else operator.current
    current_screen = location_3d_to_region_2d(
        context.region,
        context.region_data,
        label_point,
        default=None,
    )
    if current_screen is None:
        return

    label = operator.preview_label
    if input_active:
        typed = getattr(operator, "distance_input", "")
        cursor = getattr(operator, "distance_input_cursor", len(typed))
        label = f"{typed[:cursor]}|{typed[cursor:]}"

    draw_ui_box_generic(
        current_screen.x + state.get("overlay_offset_x", 25),
        current_screen.y + state.get("overlay_offset_y", 10),
        label,
        active=input_active,
    )


def draw_construction_preview(operator):
    if not operator.running:
        return
    context = operator.context
    if context.region is None or context.region_data is None:
        return

    color = (0.05, 0.8, 1.0, 1.0)
    if operator.stage == 0:
        if operator.hover_edge is not None:
            edge_points = []
            for point in (operator.hover_edge.start, operator.hover_edge.end):
                projected = location_3d_to_region_2d(
                    context.region,
                    context.region_data,
                    point,
                    default=None,
                )
                if projected is not None:
                    edge_points.append(Vector(projected))
            if len(edge_points) == 2:
                _draw_segments([(edge_points[0], edge_points[1])], color, 3.0)
        if operator.current is not None:
            point = location_3d_to_region_2d(
                context.region,
                context.region_data,
                operator.current,
                default=None,
            )
            if point is not None:
                _draw_points([Vector(point)], color, 7.0)
        _draw_prompt(context.region, "Construction Line — click an edge and drag across a connected face")
        return

    source_points = []
    for point in (operator.source_edge.start, operator.source_edge.end):
        projected = location_3d_to_region_2d(
            context.region,
            context.region_data,
            point,
            default=None,
        )
        if projected is not None:
            source_points.append(Vector(projected))
    if len(source_points) == 2:
        _draw_segments([(source_points[0], source_points[1])], (0.2, 0.55, 0.72, 0.9), 2.0)

    if operator.offset_distance > 1.0e-8:
        _draw_guide_vectors(
            context,
            [(operator.anchor, operator.edge_direction, operator.plane_normal)],
            color,
            2.0,
            dashed=False,
        )

        connector = []
        for point in (operator.source_point, operator.current):
            projected = location_3d_to_region_2d(
                context.region,
                context.region_data,
                point,
                default=None,
            )
            if projected is not None:
                connector.append(Vector(projected))
        if len(connector) == 2:
            _draw_segments(_dashed_line(*connector, dash_length=5.0, gap_length=4.0), color, 1.0)

    _draw_live_distance(context, operator)
    if getattr(operator, "distance_input_active", False):
        prompt = (
            "Construction Line — type an exact distance; Enter or click: place"
            "   •   Esc: cancel input"
        )
    else:
        prompt = (
            "Construction Line — drag on a connected face; release or click to place"
            "   •   Type distance or L   •   Backspace: previous"
        )
    _draw_prompt(
        context.region,
        prompt,
    )
