"""Viewport preview drawing for the three-click dimension workflow."""

import blf
import bpy
import gpu
import math
from bpy_extras.view3d_utils import location_3d_to_region_2d
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

from .geometry import dimension_basis


DIMENSION_HIT_RADIUS = 9.0


def _shader():
    try:
        return gpu.shader.from_builtin("UNIFORM_COLOR")
    except ValueError:
        return gpu.shader.from_builtin("3D_UNIFORM_COLOR")


def _draw_segments(segments, color, width=2.0):
    coords = []
    for start, end in segments:
        coords.extend((start, end))
    if not coords:
        return
    shader = _shader()
    gpu.state.blend_set("ALPHA")
    gpu.state.depth_test_set("NONE")
    gpu.state.line_width_set(width)
    shader.bind()
    shader.uniform_float("color", color)
    batch_for_shader(shader, "LINES", {"pos": coords}).draw(shader)
    gpu.state.line_width_set(1.0)
    gpu.state.depth_test_set("NONE")
    gpu.state.blend_set("NONE")


def _draw_points(points, color, size=7.0):
    if not points:
        return
    shader = _shader()
    gpu.state.blend_set("ALPHA")
    gpu.state.depth_test_set("NONE")
    gpu.state.point_size_set(size)
    shader.bind()
    shader.uniform_float("color", color)
    batch_for_shader(shader, "POINTS", {"pos": points}).draw(shader)
    gpu.state.point_size_set(1.0)
    gpu.state.blend_set("NONE")


def _draw_segments_2d(segments, color, width=1.5):
    coords = []
    for start, end in segments:
        coords.extend((start, end))
    if not coords:
        return
    shader = _shader()
    gpu.state.blend_set("ALPHA")
    gpu.state.line_width_set(max(1.0, float(width)))
    shader.bind()
    shader.uniform_float("color", color)
    batch_for_shader(shader, "LINES", {"pos": coords}).draw(shader)
    gpu.state.line_width_set(1.0)
    gpu.state.blend_set("NONE")


def _screen_dimension_geometry(
    context,
    p1,
    p2,
    plane_normal,
    offset_distance,
    label,
    text_size,
    arrow_size,
    extension_gap,
    extension_overshoot,
):
    """Build the exact screen-space geometry used for drawing and picking."""
    basis = dimension_basis(p1, p2, plane_normal)
    if basis is None:
        return None
    _line_world, offset_world, _normal = basis
    p1 = Vector(p1)
    p2 = Vector(p2)
    distance = float(offset_distance)
    side = 1.0 if distance >= 0.0 else -1.0
    d1 = p1 + offset_world * distance
    d2 = p2 + offset_world * distance
    gap = max(0.0, float(extension_gap))
    overshoot = max(0.0, float(extension_overshoot))
    world_points = (
        p1 + offset_world * side * gap,
        d1 + offset_world * side * overshoot,
        p2 + offset_world * side * gap,
        d2 + offset_world * side * overshoot,
        d1,
        d2,
    )
    projected = [location_3d_to_region_2d(context.region, context.region_data, point) for point in world_points]
    if any(point is None for point in projected):
        return None
    ext_1_start, ext_1_end, ext_2_start, ext_2_end, d1_2d, d2_2d = projected
    screen_line = d2_2d - d1_2d
    if screen_line.length_squared <= 1.0e-8:
        return None
    screen_line.normalize()
    screen_perp = Vector((-screen_line.y, screen_line.x))
    midpoint = (d1_2d + d2_2d) * 0.5

    font_size = max(8, int(round(float(text_size))))
    arrow = max(4.0, float(arrow_size))
    blf.size(0, font_size)
    text_width, text_height = blf.dimensions(0, label)
    text_half = text_width * 0.5 + 6.0

    segments = [(ext_1_start, ext_1_end), (ext_2_start, ext_2_end)]
    if (d2_2d - d1_2d).length > text_width + arrow * 3.0:
        segments.extend(
            (
                (d1_2d, midpoint - screen_line * text_half),
                (midpoint + screen_line * text_half, d2_2d),
            )
        )
    else:
        segments.append((d1_2d, d2_2d))

    if (d2_2d - d1_2d).length >= arrow * 2.5:
        left_back = d1_2d + screen_line * arrow
        right_back = d2_2d - screen_line * arrow
    else:
        left_back = d1_2d - screen_line * arrow
        right_back = d2_2d + screen_line * arrow
    wing = arrow * 0.38
    segments.extend(
        (
            (d1_2d, left_back + screen_perp * wing),
            (d1_2d, left_back - screen_perp * wing),
            (d2_2d, right_back + screen_perp * wing),
            (d2_2d, right_back - screen_perp * wing),
        )
    )

    text_direction = screen_line.copy()
    if text_direction.x < 0.0:
        text_direction.negate()
    text_up = Vector((-text_direction.y, text_direction.x))
    text_position = midpoint - text_direction * (text_width * 0.5) + text_up * (4.0 - text_height * 0.2)
    return {
        "segments": segments,
        "midpoint": midpoint,
        "text_direction": text_direction,
        "text_up": text_up,
        "text_position": text_position,
        "text_width": text_width,
        "text_height": text_height,
        "font_size": font_size,
    }


def _distance_to_segment_2d(point, start, end):
    segment = end - start
    if segment.length_squared <= 1.0e-8:
        return (point - start).length
    factor = max(0.0, min(1.0, (point - start).dot(segment) / segment.length_squared))
    return (point - (start + segment * factor)).length


def dimension_hit_distance(
    context,
    mouse,
    p1,
    p2,
    plane_normal,
    offset_distance,
    label,
    text_size,
    arrow_size,
    line_width,
    extension_gap,
    extension_overshoot,
):
    """Return a pixel hit distance, or None when the dimension was not clicked."""
    geometry = _screen_dimension_geometry(
        context,
        p1,
        p2,
        plane_normal,
        offset_distance,
        label,
        text_size,
        arrow_size,
        extension_gap,
        extension_overshoot,
    )
    if geometry is None:
        return None

    mouse = Vector(mouse)
    line_distance = min(
        (_distance_to_segment_2d(mouse, start, end) for start, end in geometry["segments"]),
        default=float("inf"),
    )
    line_radius = max(DIMENSION_HIT_RADIUS, float(line_width) * 0.5 + 5.0)
    best = line_distance if line_distance <= line_radius else None

    # The text is rotated around its lower-left draw position. Use the same
    # local axes and a small pad so labels remain comfortable click targets.
    delta = mouse - geometry["text_position"]
    text_x = delta.dot(geometry["text_direction"])
    text_y = delta.dot(geometry["text_up"])
    pad = DIMENSION_HIT_RADIUS
    if (
        -pad <= text_x <= geometry["text_width"] + pad
        and -pad <= text_y <= geometry["text_height"] + pad
    ):
        text_distance = 0.0
        best = text_distance if best is None else min(best, text_distance)
    return best


def draw_screen_dimension(
    context,
    p1,
    p2,
    plane_normal,
    offset_distance,
    label,
    color,
    text_size,
    arrow_size,
    line_width,
    extension_gap,
    extension_overshoot,
):
    """Draw one complete dimension in POST_PIXEL; no scene geometry is used."""
    geometry = _screen_dimension_geometry(
        context,
        p1,
        p2,
        plane_normal,
        offset_distance,
        label,
        text_size,
        arrow_size,
        extension_gap,
        extension_overshoot,
    )
    if geometry is None:
        return

    _draw_segments_2d(geometry["segments"], tuple(color), line_width)
    font_size = geometry["font_size"]
    text_position = geometry["text_position"]
    text_direction = geometry["text_direction"]
    blf.size(0, font_size)
    blf.color(0, *tuple(color))
    blf.position(0, text_position.x, text_position.y, 0)
    try:
        blf.enable(0, blf.ROTATION)
        blf.rotation(0, math.atan2(text_direction.y, text_direction.x))
        blf.draw(0, label)
    except (AttributeError, RuntimeError):
        blf.draw(0, label)
    finally:
        try:
            blf.disable(0, blf.ROTATION)
        except (AttributeError, RuntimeError):
            pass


def draw_preview_3d(operator):
    if not operator.running:
        return
    if operator.stage == 0 and operator.current is not None:
        _draw_points([operator.current], (1.0, 0.75, 0.0, 1.0))
    elif operator.stage == 1 and operator.p1 is not None and operator.current is not None:
        _draw_segments([(operator.p1, operator.current)], (0.02, 0.02, 0.02, 1.0), 2.0)
        _draw_points([operator.p1, operator.current], (1.0, 0.75, 0.0, 1.0))
    else:
        _draw_points([operator.p1, operator.p2], (1.0, 0.75, 0.0, 1.0))


def _draw_box(x, y, text):
    font_id = 0
    blf.size(font_id, 13)
    width, height = blf.dimensions(font_id, text)
    pad = 7.0
    verts = ((x, y), (x + width + pad * 2, y), (x + width + pad * 2, y + height + pad * 2), (x, y + height + pad * 2))
    shader = _shader()
    gpu.state.blend_set("ALPHA")
    shader.bind()
    shader.uniform_float("color", (0.08, 0.08, 0.08, 0.82))
    batch_for_shader(shader, "TRI_FAN", {"pos": verts}).draw(shader)
    blf.color(font_id, 0.92, 0.92, 0.92, 1.0)
    blf.position(font_id, x + pad, y + pad, 0)
    blf.draw(font_id, text)
    gpu.state.blend_set("NONE")


def draw_preview_2d(operator):
    if not operator.running:
        return
    prompts = (
        "Dimension — click first point",
        "Dimension — click second point",
        "Dimension — place dimension and click",
    )
    prompt = prompts[min(operator.stage, 2)]
    _draw_box(20, operator.context.region.height - 48, prompt)
    _draw_box(20, 20, "F1–F5 snapping   •   Backspace previous   •   Esc cancel")

    if operator.stage >= 2 and operator.p1 is not None and operator.p2 is not None:
        scene = operator.context.scene
        draw_screen_dimension(
            operator.context,
            operator.p1,
            operator.p2,
            operator.plane_normal,
            operator.offset_distance,
            operator.preview_label,
            scene.radcad_dimension_color,
            scene.radcad_dimension_text_size,
            scene.radcad_dimension_arrow_size,
            scene.radcad_dimension_line_width,
            scene.radcad_dimension_extension_gap,
            scene.radcad_dimension_extension_overshoot,
        )


def draw_persistent_dimensions_2d():
    context = bpy.context
    if (
        context.area is None
        or context.area.type != "VIEW_3D"
        or context.region is None
        or context.region_data is None
        or not getattr(context.scene, "radcad_dimensions_visible", True)
    ):
        return
    from .model import dimension_layout, iter_dimensions

    for root in iter_dimensions(context.scene):
        layout, label = dimension_layout(root)
        if layout is None:
            continue
        data = root.radcad_dimension
        draw_screen_dimension(
            context,
            layout.p1,
            layout.p2,
            layout.plane_normal,
            data.offset_distance,
            label,
            data.color,
            data.text_size if data.text_size >= 4.0 else 14.0,
            data.arrow_size if data.arrow_size >= 2.0 else 10.0,
            data.line_width if data.line_width >= 0.5 else 1.5,
            data.extension_gap,
            data.extension_overshoot,
        )
