"""Blender operator entry point for the angle dimension tool."""

from ..dimension_tool.operator import (
    VIEW3D_OT_radcad_dimension_angle as _AngleDimensionImplementation,
)


class VIEW3D_OT_radcad_dimension_angle(_AngleDimensionImplementation):
    """Expose the angle dimension operator with the standard op_* layout."""

    bl_idname = _AngleDimensionImplementation.bl_idname
