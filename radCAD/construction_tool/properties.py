"""Scene-backed properties for persistent construction guides."""

import bpy


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


class RADCAD_PG_ConstructionLine(bpy.types.PropertyGroup):
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
        description="Draw construction lines and make them available to radCAD snapping",
        default=True,
        update=_display_property_updated,
    )
    bpy.types.Scene.radcad_construction_line_color = bpy.props.FloatVectorProperty(
        name="Construction Line Color",
        description="Viewport color used for persistent construction lines",
        subtype="COLOR",
        size=4,
        min=0.0,
        max=1.0,
        default=(0.12, 0.62, 1.0, 0.9),
        update=_display_property_updated,
    )
    bpy.types.Scene.radcad_construction_line_width = bpy.props.FloatProperty(
        name="Construction Line Width",
        description="Viewport width of construction line dashes",
        default=1.5,
        min=1.0,
        max=6.0,
        update=_display_property_updated,
    )


def unregister():
    for property_name in (
        "radcad_construction_line_width",
        "radcad_construction_line_color",
        "radcad_construction_lines_visible",
        "radcad_construction_lines",
    ):
        if hasattr(bpy.types.Scene, property_name):
            delattr(bpy.types.Scene, property_name)

    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
