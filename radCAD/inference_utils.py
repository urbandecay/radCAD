# radCAD/inference_utils.py

import bpy
from mathutils import Vector, geometry
from bpy_extras.view3d_utils import (
    region_2d_to_origin_3d,
    region_2d_to_vector_3d,
    location_3d_to_region_2d,
)


def get_direction_snapped_location(start, mouse_xy, context, directions, snap_threshold=0.9):
    """Snap from *start* along the best screen-aligned candidate direction.

    ``directions`` maps display names to 3D vectors.  This is kept separate
    from global-axis inference so constrained tools can omit directions that
    they cannot actually use.
    """
    region = context.region
    rv3d = context.space_data.region_3d

    if start is None or region is None or rv3d is None:
        return None, None, None

    start = Vector(start)
    start_screen = location_3d_to_region_2d(region, rv3d, start)
    if start_screen is None:
        return None, None, None

    d_screen = Vector(mouse_xy) - start_screen
    if d_screen.length < 1.0:
        return None, None, None
    d_screen.normalize()

    best_name = None
    best_direction = None
    best_dot = -1.0
    for direction_name, direction in directions.items():
        direction = Vector(direction)
        if direction.length_squared <= 1.0e-12:
            continue
        direction.normalize()

        target_screen = location_3d_to_region_2d(region, rv3d, start + direction)
        if target_screen is None:
            continue
        screen_direction = target_screen - start_screen
        if screen_direction.length_squared <= 1.0e-12:
            continue
        screen_direction.normalize()

        dot_value = abs(d_screen.dot(screen_direction))
        if dot_value > best_dot:
            best_dot = dot_value
            best_name = direction_name
            best_direction = direction

    if best_direction is None or best_dot < snap_threshold:
        return None, None, None

    ray_origin = region_2d_to_origin_3d(region, rv3d, mouse_xy)
    ray_vector = region_2d_to_vector_3d(region, rv3d, mouse_xy)
    intersection = geometry.intersect_line_line(
        ray_origin,
        ray_origin + ray_vector * 10000.0,
        start,
        start + best_direction * 10000.0,
    )
    if intersection is None:
        return None, None, None

    snapped_location = intersection[1]
    if (snapped_location - start).dot(best_direction) < 0.0:
        best_direction.negate()
    return snapped_location, best_direction, best_name


def get_axis_snapped_location(start, mouse_xy, context, snap_threshold=0.9):
    """
    Compute a snapped location based on how the line from the
    start point to the mouse appears in the viewport.

    The method works by:
      1. Projecting the start point to screen space.
      2. Calculating the 2D direction from the start to the mouse.
      3. For each global axis (X, Y, Z) offset from the start,
         project the resulting point to screen space.
      4. Compare the normalized screen-space displacement with the unit
         screen-space direction of the global axis.
      5. If the best matching axis has a dot product above snap_threshold,
         perform a 3D Ray-Line intersection to find the exact point on the axis
         that lies under the mouse cursor.
    """
    axes = {
        "X": Vector((1, 0, 0)),
        "Y": Vector((0, 1, 0)),
        "Z": Vector((0, 0, 1)),
    }
    return get_direction_snapped_location(
        start,
        mouse_xy,
        context,
        axes,
        snap_threshold=snap_threshold,
    )
