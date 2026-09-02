"""Viewport preview drawing for the three-click dimension workflow."""

import blf
import bpy
import gpu
import math
from bpy_extras.view3d_utils import location_3d_to_region_2d
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

from ..modal_state import state
from .geometry import build_angle_layout, dimension_basis


DIMENSION_HIT_RADIUS = 9.0
SELECTION_COLOR = (1.0, 121.0 / 255.0, 0.0, 1.0)  # #FF7900


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


def _draw_segments_2d(segments, color, width=1.0):
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
    text_thickness,
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
    thickness_radius = max(0.0, (float(text_thickness) - 1.0) * 0.5)
    text_half = text_width * 0.5 + 6.0 + thickness_radius

    segments = [(ext_1_start, ext_1_end), (ext_2_start, ext_2_end)]
    if (d2_2d - d1_2d).length > text_width + thickness_radius * 2.0 + arrow * 3.0:
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
        "thickness_radius": thickness_radius,
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
    text_thickness,
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
        text_thickness,
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
    text_thickness,
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
        text_thickness,
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
    text_up = geometry["text_up"]
    radius = geometry["thickness_radius"]
    blf.size(0, font_size)
    blf.color(0, *tuple(color))
    try:
        blf.enable(0, blf.ROTATION)
        blf.rotation(0, math.atan2(text_direction.y, text_direction.x))
        offsets = ((0.0, 0.0),)
        if radius > 1.0e-6:
            diagonal = radius * math.sqrt(0.5)
            offsets += (
                (radius, 0.0),
                (-radius, 0.0),
                (0.0, radius),
                (0.0, -radius),
                (diagonal, diagonal),
                (-diagonal, diagonal),
                (diagonal, -diagonal),
                (-diagonal, -diagonal),
            )
        for along, upward in offsets:
            draw_position = text_position + text_direction * along + text_up * upward
            blf.position(0, draw_position.x, draw_position.y, 0)
            blf.draw(0, label)
    except (AttributeError, RuntimeError):
        blf.position(0, text_position.x, text_position.y, 0)
        blf.draw(0, label)
    finally:
        try:
            blf.disable(0, blf.ROTATION)
        except (AttributeError, RuntimeError):
            pass


def _screen_angle_geometry(
    context,
    vertex,
    ray_1,
    ray_2,
    plane_normal,
    radius,
    label,
    text_size,
    text_thickness,
    arrow_size,
    extension_gap,
    extension_overshoot,
):
    """Build the exact screen-space geometry used for angle drawing/picking."""
    layout = build_angle_layout(
        vertex,
        ray_1,
        ray_2,
        plane_normal,
        radius,
        text_size,
        arrow_size,
        extension_gap,
        extension_overshoot,
        label,
    )
    if layout is None:
        return None

    vertex_2d = location_3d_to_region_2d(context.region, context.region_data, layout.vertex)
    arc_2d = [
        location_3d_to_region_2d(context.region, context.region_data, point)
        for point in layout.arc_points
    ]
    witness_2d = [
        (
            location_3d_to_region_2d(context.region, context.region_data, start),
            location_3d_to_region_2d(context.region, context.region_data, end),
        )
        for start, end in layout.segments[:2]
    ]
    if (
        vertex_2d is None
        or any(point is None for point in arc_2d)
        or any(start is None or end is None for start, end in witness_2d)
    ):
        return None

    font_size = max(8, int(round(float(text_size))))
    arrow = max(4.0, float(arrow_size))
    blf.size(0, font_size)
    text_width, text_height = blf.dimensions(0, label)
    thickness_radius = max(0.0, (float(text_thickness) - 1.0) * 0.5)

    segments = list(witness_2d)
    segments.extend(zip(arc_2d[:-1], arc_2d[1:]))
    wing = arrow * 0.38

    def add_arrow(tip, toward_arc):
        direction = Vector(toward_arc)
        if direction.length_squared <= 1.0e-8:
            return
        direction.normalize()
        back = tip + direction * arrow
        perpendicular = Vector((-direction.y, direction.x)) * wing
        segments.extend(((tip, back + perpendicular), (tip, back - perpendicular)))

    add_arrow(arc_2d[0], arc_2d[1] - arc_2d[0])
    add_arrow(arc_2d[-1], arc_2d[-2] - arc_2d[-1])

    midpoint = location_3d_to_region_2d(context.region, context.region_data, layout.midpoint)
    if midpoint is None:
        return None
    radial = midpoint - vertex_2d
    if radial.length_squared <= 1.0e-8:
        radial = Vector((0.0, 1.0))
    else:
        radial.normalize()
    text_center = midpoint + radial * max(4.0, text_height * 0.30)
    text_direction = Vector((1.0, 0.0))
    text_up = Vector((0.0, 1.0))
    text_position = (
        text_center
        - text_direction * (text_width * 0.5)
        - text_up * (text_height * 0.5)
    )
    return {
        "segments": segments,
        "midpoint": midpoint,
        "text_direction": text_direction,
        "text_up": text_up,
        "text_position": text_position,
        "text_width": text_width,
        "text_height": text_height,
        "font_size": font_size,
        "thickness_radius": thickness_radius,
    }


def angle_dimension_hit_distance(
    context,
    mouse,
    vertex,
    ray_1,
    ray_2,
    plane_normal,
    radius,
    label,
    text_size,
    text_thickness,
    arrow_size,
    line_width,
    extension_gap,
    extension_overshoot,
):
    """Return a pixel hit distance for an angle annotation, or None."""
    geometry = _screen_angle_geometry(
        context,
        vertex,
        ray_1,
        ray_2,
        plane_normal,
        radius,
        label,
        text_size,
        text_thickness,
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

    delta = mouse - geometry["text_position"]
    text_x = delta.dot(geometry["text_direction"])
    text_y = delta.dot(geometry["text_up"])
    pad = DIMENSION_HIT_RADIUS
    if (
        -pad <= text_x <= geometry["text_width"] + pad
        and -pad <= text_y <= geometry["text_height"] + pad
    ):
        best = 0.0 if best is None else min(best, 0.0)
    return best


def draw_screen_angle_dimension(
    context,
    vertex,
    ray_1,
    ray_2,
    plane_normal,
    radius,
    label,
    color,
    text_size,
    text_thickness,
    arrow_size,
    line_width,
    extension_gap,
    extension_overshoot,
):
    """Draw one angle dimension in POST_PIXEL; no scene geometry is used."""
    geometry = _screen_angle_geometry(
        context,
        vertex,
        ray_1,
        ray_2,
        plane_normal,
        radius,
        label,
        text_size,
        text_thickness,
        arrow_size,
        extension_gap,
        extension_overshoot,
    )
    if geometry is None:
        return

    _draw_segments_2d(geometry["segments"], tuple(color), line_width)
    font_size = geometry["font_size"]
    text_position = geometry["text_position"]
    radius = geometry["thickness_radius"]
    blf.size(0, font_size)
    blf.color(0, *tuple(color))
    try:
        offsets = ((0.0, 0.0),)
        if radius > 1.0e-6:
            diagonal = radius * math.sqrt(0.5)
            offsets += (
                (radius, 0.0),
                (-radius, 0.0),
                (0.0, radius),
                (0.0, -radius),
                (diagonal, diagonal),
                (-diagonal, diagonal),
                (diagonal, -diagonal),
                (-diagonal, -diagonal),
            )
        for along, upward in offsets:
            draw_position = text_position + Vector((along, upward))
            blf.position(0, draw_position.x, draw_position.y, 0)
            blf.draw(0, label)
    except (AttributeError, RuntimeError):
        blf.position(0, text_position.x, text_position.y, 0)
        blf.draw(0, label)


def _draw_angle_compass(operator):
    """Reuse the Rotate/Arc protractor for the angle plane preview."""
    center = getattr(operator, "compass_center", None)
    plane_normal = getattr(operator, "compass_plane_normal", None)
    compass_x = getattr(operator, "compass_x", None)
    compass_y = getattr(operator, "compass_y", None)
    if center is None or compass_x is None or compass_y is None:
        return

    try:
        from ..tool_previews import (
            draw_compass_geometry,
            get_axis_aligned_color,
            get_render_settings,
            get_shaders,
        )

        context = operator.context
        settings = get_render_settings(context)
        shaders = get_shaders()
        color_normal = plane_normal if plane_normal is not None else compass_x.cross(compass_y)
        color = get_axis_aligned_color(
            color_normal,
            (0.0, 0.0, 0.0, 1.0),
            settings,
        )
        draw_compass_geometry(
            context,
            shaders,
            center,
            compass_x,
            compass_y,
            getattr(operator, "compass_rotation", 0.0),
            state.get("compass_size", 125),
            state.get("angle_increment", 15.0),
            color,
            settings,
        )
    except Exception:
        # The dimension overlay must remain usable if Blender has no active
        # GPU viewport during a redraw or a preference is unavailable.
        return


def _draw_angle_preview_3d(operator):
    _draw_angle_compass(operator)
    vertex = getattr(operator, "vertex", None)
    current = getattr(operator, "current", None)
    if operator.stage == 0 and current is not None:
        _draw_points([current], (1.0, 0.75, 0.0, 1.0))
        return
    if vertex is None:
        return

    ray_1 = getattr(operator, "ray_1", None)
    ray_2 = getattr(operator, "ray_2", None)
    if operator.stage == 1 and current is not None:
        _draw_segments([(vertex, current)], (0.02, 0.02, 0.02, 1.0), 2.0)
        _draw_points([vertex, current], (1.0, 0.75, 0.0, 1.0))
        return
    if ray_1 is None:
        return
    ray_2 = ray_2 if ray_2 is not None else current
    if ray_2 is None:
        return
    layout = build_angle_layout(
        vertex,
        ray_1,
        ray_2,
        getattr(operator, "plane_normal", None),
        getattr(operator, "offset_distance", 0.0),
        0.001,
        0.001,
        0.0,
        0.0,
    )
    if layout is None:
        _draw_segments(((vertex, ray_1), (vertex, ray_2)), (0.02, 0.02, 0.02, 1.0), 2.0)
    else:
        _draw_segments(layout.segments, (0.02, 0.02, 0.02, 1.0), 2.0)
    _draw_points([vertex, ray_1, ray_2], (1.0, 0.75, 0.0, 1.0))


def draw_preview_3d(operator):
    if not operator.running:
        return
    if getattr(operator, "dimension_type", "LINEAR") == "ANGLE":
        _draw_angle_preview_3d(operator)
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
    if getattr(operator, "dimension_type", "LINEAR") == "ANGLE":
        prompts = (
            "Angle Dimension — click vertex",
            "Angle Dimension — click first ray",
            "Angle Dimension — click second ray",
        )
        prompt = prompts[min(operator.stage, 2)]
        _draw_box(20, operator.context.region.height - 48, prompt)
        _draw_box(20, 20, "F1–F5 snapping   •   Backspace previous   •   Esc cancel")
        ray_1 = getattr(operator, "ray_1", None)
        ray_2 = getattr(operator, "ray_2", None)
        if operator.stage >= 2 and operator.vertex is not None and ray_1 is not None:
            ray_2 = ray_2 if ray_2 is not None else operator.current
            if ray_2 is not None:
                scene = operator.context.scene
                draw_screen_angle_dimension(
                    operator.context,
                    operator.vertex,
                    ray_1,
                    ray_2,
                    operator.plane_normal,
                    operator.offset_distance,
                    operator.preview_label,
                    scene.radcad_dimension_color,
                    scene.radcad_dimension_text_size,
                    scene.radcad_dimension_text_thickness,
                    scene.radcad_dimension_arrow_size,
                    scene.radcad_dimension_line_width,
                    scene.radcad_dimension_extension_gap,
                    scene.radcad_dimension_extension_overshoot,
                )
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
            scene.radcad_dimension_text_thickness,
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

    active = getattr(context.scene, "radcad_active_dimension", None)
    for root in iter_dimensions(context.scene):
        layout, label = dimension_layout(root)
        if layout is None:
            continue
        data = root.radcad_dimension
        selected = root == active
        color = SELECTION_COLOR if selected else tuple(data.color)
        line_width = data.line_width if data.line_width >= 0.5 else 1.0
        if getattr(data, "dimension_type", "LINEAR") == "ANGLE":
            draw_screen_angle_dimension(
                context,
                layout.vertex,
                layout.ray_1,
                layout.ray_2,
                layout.plane_normal,
                data.offset_distance,
                label,
                color,
                data.text_size if data.text_size >= 4.0 else 14.0,
                max(1.0, float(data.text_thickness)),
                data.arrow_size if data.arrow_size >= 2.0 else 10.0,
                line_width,
                data.extension_gap,
                data.extension_overshoot,
            )
        else:
            draw_screen_dimension(
                context,
                layout.p1,
                layout.p2,
                layout.plane_normal,
                data.offset_distance,
                label,
                color,
                data.text_size if data.text_size >= 4.0 else 14.0,
                max(1.0, float(data.text_thickness)),
                data.arrow_size if data.arrow_size >= 2.0 else 10.0,
                line_width,
                data.extension_gap,
                data.extension_overshoot,
            )
