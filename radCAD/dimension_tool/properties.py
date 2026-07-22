"""RNA data stored on persistent dimension objects."""

import bpy


def _update_dimension(self, _context):
    root = getattr(self, "id_data", None)
    if not isinstance(root, bpy.types.Object):
        return
    data = getattr(root, "radcad_dimension", None)
    if data is None or not data.is_dimension:
        return
    from .model import update_dimension

    update_dimension(root)


class RADCAD_PG_dimension_anchor(bpy.types.PropertyGroup):
    target: bpy.props.PointerProperty(type=bpy.types.Object)
    kind: bpy.props.StringProperty(default="FREE")
    indices: bpy.props.StringProperty(default="[]")
    weights: bpy.props.StringProperty(default="[]")
    fallback: bpy.props.FloatVectorProperty(size=3, subtype="XYZ")


class RADCAD_PG_dimension_data(bpy.types.PropertyGroup):
    is_dimension: bpy.props.BoolProperty(default=False)
    anchor_1: bpy.props.PointerProperty(type=RADCAD_PG_dimension_anchor)
    anchor_2: bpy.props.PointerProperty(type=RADCAD_PG_dimension_anchor)
    plane_normal: bpy.props.FloatVectorProperty(size=3, subtype="XYZ", default=(0.0, 0.0, 1.0))
    offset_distance: bpy.props.FloatProperty(name="Offset", subtype="DISTANCE", default=1.0, update=_update_dimension)
    text_override: bpy.props.StringProperty(
        name="Text Override",
        description="Leave empty to display the live measured distance",
        default="",
        update=_update_dimension,
    )
    text_size: bpy.props.FloatProperty(name="Text Size (px)", min=8.0, max=72.0, default=14.0, update=_update_dimension)
    arrow_size: bpy.props.FloatProperty(name="Arrow Size (px)", min=4.0, max=40.0, default=10.0, update=_update_dimension)
    extension_gap: bpy.props.FloatProperty(name="Extension Gap", subtype="DISTANCE", min=0.0, default=0.05, update=_update_dimension)
    extension_overshoot: bpy.props.FloatProperty(name="Extension Overshoot", subtype="DISTANCE", min=0.0, default=0.10, update=_update_dimension)
    line_width: bpy.props.FloatProperty(name="Line Width (px)", min=1.0, max=10.0, default=1.0, update=_update_dimension)
    color: bpy.props.FloatVectorProperty(
        name="Color",
        subtype="COLOR",
        size=4,
        min=0.0,
        max=1.0,
        default=(0.02, 0.02, 0.02, 1.0),
        update=_update_dimension,
    )
    measured_length: bpy.props.FloatProperty(options={"HIDDEN"})


CLASSES = (RADCAD_PG_dimension_anchor, RADCAD_PG_dimension_data)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Object.radcad_dimension = bpy.props.PointerProperty(type=RADCAD_PG_dimension_data)

    scene_props = {
        "radcad_dimension_text_size": bpy.props.FloatProperty(name="Text Size (px)", min=8.0, max=72.0, default=14.0),
        "radcad_dimension_arrow_size": bpy.props.FloatProperty(name="Arrow Size (px)", min=4.0, max=40.0, default=10.0),
        "radcad_dimension_extension_gap": bpy.props.FloatProperty(name="Extension Gap", subtype="DISTANCE", min=0.0, default=0.05),
        "radcad_dimension_extension_overshoot": bpy.props.FloatProperty(name="Extension Overshoot", subtype="DISTANCE", min=0.0, default=0.10),
        "radcad_dimension_line_width": bpy.props.FloatProperty(name="Line Width (px)", min=1.0, max=10.0, default=1.0),
        "radcad_dimension_color": bpy.props.FloatVectorProperty(name="Color", subtype="COLOR", size=4, min=0.0, max=1.0, default=(0.02, 0.02, 0.02, 1.0)),
        "radcad_dimensions_visible": bpy.props.BoolProperty(name="Show Dimensions", default=True),
        "radcad_active_dimension": bpy.props.PointerProperty(name="Active Dimension", type=bpy.types.Object),
    }
    for name, prop in scene_props.items():
        setattr(bpy.types.Scene, name, prop)


def unregister():
    for name in (
        "radcad_dimension_color",
        "radcad_active_dimension",
        "radcad_dimensions_visible",
        "radcad_dimension_line_width",
        "radcad_dimension_extension_overshoot",
        "radcad_dimension_extension_gap",
        "radcad_dimension_arrow_size",
        "radcad_dimension_text_size",
    ):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    if hasattr(bpy.types.Object, "radcad_dimension"):
        del bpy.types.Object.radcad_dimension
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
