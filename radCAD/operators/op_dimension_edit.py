"""Blender entry points for shared dimension editing actions."""

from ..dimension_tool.operator import (
    VIEW3D_OT_radcad_dimension_delete as _DeleteDimensionImplementation,
    VIEW3D_OT_radcad_dimension_parameters as _DimensionParametersImplementation,
    VIEW3D_OT_radcad_dimension_pick as _PickDimensionImplementation,
    VIEW3D_OT_radcad_dimension_refresh as _RefreshDimensionImplementation,
    VIEW3D_OT_radcad_dimension_reposition as _RepositionDimensionImplementation,
)


class VIEW3D_OT_radcad_dimension_reposition(_RepositionDimensionImplementation):
    """Expose dimension repositioning through the shared operator folder."""

    bl_idname = _RepositionDimensionImplementation.bl_idname


class VIEW3D_OT_radcad_dimension_refresh(_RefreshDimensionImplementation):
    """Expose dimension refresh through the shared operator folder."""

    bl_idname = _RefreshDimensionImplementation.bl_idname


class VIEW3D_OT_radcad_dimension_parameters(_DimensionParametersImplementation):
    """Expose dimension parameters through the shared operator folder."""

    bl_idname = _DimensionParametersImplementation.bl_idname


class VIEW3D_OT_radcad_dimension_pick(_PickDimensionImplementation):
    """Expose dimension picking through the shared operator folder."""

    bl_idname = _PickDimensionImplementation.bl_idname


class VIEW3D_OT_radcad_dimension_delete(_DeleteDimensionImplementation):
    """Expose dimension deletion through the shared operator folder."""

    bl_idname = _DeleteDimensionImplementation.bl_idname


CLASSES = (
    VIEW3D_OT_radcad_dimension_reposition,
    VIEW3D_OT_radcad_dimension_refresh,
    VIEW3D_OT_radcad_dimension_parameters,
    VIEW3D_OT_radcad_dimension_pick,
    VIEW3D_OT_radcad_dimension_delete,
)
