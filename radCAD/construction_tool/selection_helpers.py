"""Shared cursor projection helpers for construction-line operators."""

from bpy_extras.view3d_utils import (
    region_2d_to_origin_3d,
    region_2d_to_vector_3d,
)
from mathutils.geometry import intersect_line_plane


def _project_cursor_to_plane(context, x, y, plane_point, plane_normal):
    origin = region_2d_to_origin_3d(context.region, context.region_data, (x, y))
    direction = region_2d_to_vector_3d(context.region, context.region_data, (x, y))
    return intersect_line_plane(
        origin,
        origin + direction * 100000.0,
        plane_point,
        plane_normal,
    )
