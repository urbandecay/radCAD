"""Blender operator entry point for the linear dimension tool."""

from ..dimension_tool.operator import (
    VIEW3D_OT_radcad_dimension_linear as _LinearDimensionImplementation,
)


class VIEW3D_OT_radcad_dimension_linear(_LinearDimensionImplementation):
    """Expose the linear dimension operator with the standard op_* layout."""

    bl_idname = _LinearDimensionImplementation.bl_idname
