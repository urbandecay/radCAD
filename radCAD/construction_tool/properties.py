"""Scene-backed properties for persistent construction guides."""

import bpy

from ..registration_utils import safe_delete_property, safe_unregister_class


def tag_redraw_all_view3d():
    window_manager = getattr(bpy.context, "window_manager", None)
    if window_manager is None:
        return
    for window in window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


def _display_property_updated(_self, _context):
    tag_redraw_all_view3d()


def _visibility_property_updated(_self, context):
    try:
        from .native_snap import sync_scene_snap_proxy

        sync_scene_snap_proxy(context.scene)
    except (AttributeError, ImportError, RuntimeError):
        pass
    tag_redraw_all_view3d()


class RADCAD_PG_ConstructionLine(bpy.types.PropertyGroup):
    selected: bpy.props.BoolProperty(default=False, options={"HIDDEN"})
    schema_version: bpy.props.IntProperty(default=0)
    anchor: bpy.props.FloatVectorProperty(
        name="Anchor",
        size=3,
        subtype="XYZ",
    )
    direction: bpy.props.FloatVectorProperty(
        name="Direction",
        size=3,
        subtype="DIRECTION",
        default=(1.0, 0.0, 0.0),
    )
    plane_normal: bpy.props.FloatVectorProperty(
        name="Plane Normal",
        size=3,
        subtype="DIRECTION",
        default=(0.0, 0.0, 1.0),
    )


_CLASSES = (RADCAD_PG_ConstructionLine,)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)

    bpy.types.Scene.radcad_construction_lines = bpy.props.CollectionProperty(
        type=RADCAD_PG_ConstructionLine,
    )
    bpy.types.Scene.radcad_construction_lines_visible = bpy.props.BoolProperty(
        name="Show Construction Lines",
        description="Draw construction lines and make them available to radCAD and Blender snapping",
        default=True,
        update=_visibility_property_updated,
    )
    bpy.types.Scene.radcad_active_construction_line = bpy.props.IntProperty(
        name="Active Construction Line",
        description="Index of the construction line selected in the viewport",
        default=-1,
        options={"HIDDEN"},
    )
    bpy.types.Scene.radcad_construction_line_color = bpy.props.FloatVectorProperty(
        name="Construction Line Color",
        description="Viewport color used for persistent construction lines",
        subtype="COLOR",
        size=4,
        min=0.0,
        max=1.0,
        default=(1.0, 1.0, 1.0, 1.0),
        update=_display_property_updated,
    )
    bpy.types.Scene.radcad_construction_line_width = bpy.props.FloatProperty(
        name="Construction Line Width",
        description="Viewport width of construction line dashes",
        default=1.0,
        min=1.0,
        max=6.0,
        update=_display_property_updated,
    )
    bpy.types.Scene.radcad_construction_dash_length = bpy.props.FloatProperty(
        name="Dash Length (px)",
        description="Screen-space length of each construction line dash",
        default=9.0,
        min=1.0,
        max=100.0,
        update=_display_property_updated,
    )
    bpy.types.Scene.radcad_construction_dash_gap = bpy.props.FloatProperty(
        name="Dash Gap (px)",
        description="Screen-space space between construction line dashes; zero draws a solid line",
        default=6.0,
        min=0.0,
        max=100.0,
        update=_display_property_updated,
    )


def unregister():
    for property_name in (
        "radcad_construction_dash_gap",
        "radcad_construction_dash_length",
        "radcad_construction_line_width",
        "radcad_construction_line_color",
        "radcad_construction_lines_visible",
        "radcad_active_construction_line",
        "radcad_construction_lines",
    ):
        safe_delete_property(bpy.types.Scene, property_name)

    for cls in reversed(_CLASSES):
        safe_unregister_class(cls)
