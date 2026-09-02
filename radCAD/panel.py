bl_info = {
    "name": "rCAD",
    "blender": (4, 2, 0),
    "category": "3D View",
}

import bpy
import bpy.utils.previews
import importlib
import os
import sys
import traceback
from .modal_state import state 
from .modal_core import DrawManager


_reload_pending = False


def _reload_radCAD_timer():
    """Reload the complete rCAD package after the button operation returns."""
    global _reload_pending
    _reload_pending = False

    package_name = __package__.split('.')[0]
    old_package = sys.modules.get(package_name)
    if old_package is None:
        print(f"rCAD reload failed: {package_name!r} is not loaded")
        return None

    addon_enabled = getattr(old_package, "__addon_enabled__", False)
    addon_persistent = getattr(old_package, "__addon_persistent__", False)

    try:
        old_package.unregister()
        package_modules = [
            name for name in tuple(sys.modules)
            if name == package_name or name.startswith(package_name + ".")
        ]
        for name in sorted(
            package_modules,
            key=lambda item: (item.count('.'), item),
            reverse=True,
        ):
            sys.modules.pop(name, None)

        importlib.invalidate_caches()
        new_package = importlib.import_module(package_name)
        new_package.register()
        new_package.__addon_enabled__ = addon_enabled
        new_package.__addon_persistent__ = addon_persistent
        print("rCAD reloaded")
    except Exception:
        traceback.print_exc()

    return None


class RADCAD_OT_ReloadAddon(bpy.types.Operator):
    bl_idname = "wm.radcad_reload_addon"
    bl_label = "Reload rCAD"
    bl_description = "Reload rCAD without restarting Blender or saving the file"
    bl_options = {'REGISTER'}

    def execute(self, context):
        global _reload_pending
        if _reload_pending:
            self.report({'WARNING'}, "rCAD reload is already pending.")
            return {'CANCELLED'}

        _reload_pending = True
        bpy.app.timers.register(_reload_radCAD_timer, first_interval=0.1)
        self.report({'INFO'}, "rCAD will reload after this operation finishes.")
        return {'FINISHED'}


CURRENT_DIR = os.path.dirname(__file__)
POSSIBLE_PATHS = [
    os.path.join(CURRENT_DIR, "icons"),
    os.path.join(CURRENT_DIR, "Toolbar Icons"),
    "/home/molotovgirl/Desktop/ArcTools/Toolbar Icons/"
]

ICON_FOLDERS = tuple(p for p in POSSIBLE_PATHS if os.path.isdir(p))

# Header-only icons are kept separate from SVG_FILES so they do not become
# selectable tool buttons in the panels.
DEFAULT_ICON_FILES = {
    "arc_default": "arc.svg",
    "circle": "circle.svg",
    "ellipse": "ellipse.svg",
    "line_default": "line_default.svg",
    "point_default": "point.svg",
    "polygon_default": "polygon.svg",
    "rectangle_default": "rectangle.svg",
}

HEADER_HEIGHT = 1.5 

IMPLEMENTED_TOOLS = {
    "arc_1_point",
    "arc_2_point",
    "arc_3_point",
    "circle_center_radius",
    "circle_2_points",
    "circle_3_points",    "circle_tangent_to_three_curves", 
    "circle_tangent_to_two_curves", 
    "ellipse_from_radius",  
    "ellipse_foci_point",   
    "ellipse_from_endpoints",
    "ellipse_from_corners",
    "polygon_cen_cor",
    "polygon_cen_tan",
    "polygon_cor_cor",
    "polygon_size_size", 
    "line", 
    "line_perpendicular_from_curve", 
    "line_tangent_to_two_curves", 
    "line_perpendicular_to_two_curves",
    "line_tangent_from_curve", 
    "curve_interpolate_points",
    "curve_freehand",
    "point_by_arcs",
    "point_center",
    "point_edge_center",
    "rectangle_from_center",
    "rectangle_from_corners",
    "rectangle_3_points", 
    "dimension_linear",
}

TOOL_OPERATORS = {
    "point_by_arcs": "view3d.point_by_arcs",
    "point_center": "view3d.point_center",
    "point_edge_center": "view3d.point_edge_center",
    "line": "view3d.line_polyline",
    "line_perpendicular_from_curve": "view3d.line_perp_from_curve",
    "line_tangent_to_two_curves": "view3d.line_tan_tan",
    "line_perpendicular_to_two_curves": "view3d.line_perp_to_two_curves",
    "line_tangent_from_curve": "view3d.line_tangent_from_curve",
    "curve_interpolate_points": "view3d.curve_interpolate",
    "curve_freehand": "view3d.curve_freehand",
    "arc_1_point": "view3d.arc_overlay_preview",
    "arc_2_point": "view3d.arc_2pt",
    "arc_3_point": "view3d.arc_3pt",
    "circle_center_radius": "view3d.circle_1pt",
    "circle_2_points": "view3d.circle_2pt",
    "circle_3_points": "view3d.circle_3pt",
    "circle_tangent_to_three_curves": "view3d.radcad_circle_tan_tan_tan",
    "circle_tangent_to_two_curves": "view3d.radcad_circle_tan_tan",
    "ellipse_from_radius": "view3d.ellipse_radius",
    "ellipse_foci_point": "view3d.ellipse_foci",
    "ellipse_from_endpoints": "view3d.ellipse_endpoints",
    "ellipse_from_corners": "view3d.ellipse_corners",
    "polygon_cen_cor": "view3d.polygon_cen_cor",
    "polygon_cen_tan": "view3d.polygon_cen_tan",
    "polygon_cor_cor": "view3d.polygon_cor_cor",
    "polygon_size_size": "view3d.polygon_edge",
    "rectangle_from_center": "view3d.rectangle_cen_cor",
    "rectangle_from_corners": "view3d.rectangle_cor_cor",
    "rectangle_3_points": "view3d.rectangle_3_points",
    "dimension_linear": "view3d.radcad_dimension_linear",
}

SVG_FILES = {
    "arc_1_point": "1_point_arc.svg",
    "arc_2_point": "2_point_arc.svg",
    "arc_3_point": "3_point_arc.svg",
    "line": "line.svg",
    "curve_freehand": "line_freehand.svg",
    "line_tangent_from_curve": "line_tangent_from_curve.svg",
    "line_tangent_to_two_curves": "line_tangent_to_two_curves.svg",
    "line_perpendicular_from_curve": "line_perpendicular_from_curve.svg",
    "line_perpendicular_to_two_curves": "line_perpendicular_to_two_curves.svg",
    "point_by_arcs": "point_by_arcs.svg",
    "point_center": "point_center.svg",
    "point_edge_center": "point_edge_center.svg",
    "circle_center_radius": "circle_center_radius.svg",
    "circle_2_points": "circle_2_points.svg",
    "circle_3_points": "circle_3_points.svg",
    "circle_tangent_to_three_curves": "circle_tangent_to_three_curves.svg",
    "circle_tangent_to_two_curves": "circle_tangent_to_two_curves.svg",
    "curve_interpolate_points": "curve_interpolate_points.svg",
    "ellipse_foci_point": "ellipse_foci_point.svg",
    "ellipse_from_corners": "ellipse_from_corners.svg",
    "ellipse_from_endpoints": "ellipse_from_endpoints.svg",
    "ellipse_from_radius": "ellipse_from_radius.svg",
    "polygon_cen_cor": "polygon_cen_cor.svg",
    "polygon_cen_tan": "polygon_cen_tan.svg",
    "polygon_cor_cor": "polygon_cor_cor.svg",
    "polygon_size_size": "polygon_size_size.svg",
    "rectangle_from_center": "rectangle_from_center.svg",
    "rectangle_from_corners": "rectangle_from_corners.svg",
    "rectangle_3_points": "rectangle_3_points.svg",
    "erase": "erase.svg",
}

TOOL_LABELS = {
    "point_by_arcs": "Point by Arcs",
    "point_center": "Point Center",
    "point_edge_center": "Edge Center",
    "line_perpendicular_from_curve": "Line Perpendicular from Curve",
    "line_perpendicular_to_two_curves": "Line Perpendicular to Two Curves",
    "line_tangent_from_curve": "Line Tangent from Curve",
    "line_tangent_to_two_curves": "Line Tangent to Two Curves",
    "arc_1_point": "1 Point Arc",
    "arc_2_point": "2 Point Arc",
    "arc_3_point": "3 Point Arc",
    "circle_center_radius": "1 Point Circle",
    "circle_2_points": "2 Point Circle",
    "circle_3_points": "3 Point Circle",
    "circle_tangent_to_three_curves": "Circle Tangent to Three Curves",
    "circle_tangent_to_two_curves": "Circle Tangent to Two Curves",
    "polygon_cen_cor": "Polygon Center Corner",
    "polygon_cen_tan": "Polygon Center Tangent",
    "polygon_cor_cor": "Polygon Corner Corner",
    "polygon_size_size": "Polygon Side Size",
}

preview_collection = None

def _has_icon(key: str) -> bool:
    return (preview_collection is not None) and (key in preview_collection)

class RADCAD_OT_reset_overlays(bpy.types.Operator):
    bl_idname = "radcad.reset_overlays"
    bl_label = "Clear Stuck Overlays"
    
    def execute(self, context):
        state["active"] = False
        DrawManager.clear_all()
        
        # Clear legacy/driver based handles if any persist
        if "radcad_handles" in bpy.app.driver_namespace:
            for h, region_type in bpy.app.driver_namespace["radcad_handles"]:
                try: bpy.types.SpaceView3D.draw_handler_remove(h, region_type)
                except Exception: pass
            bpy.app.driver_namespace["radcad_handles"] = []
            
        context.area.tag_redraw()
        return {'FINISHED'}

class RADCAD_OT_generic(bpy.types.Operator):
    bl_idname = "radcad.generic"
    bl_label = "CAD Tool"

    name: bpy.props.StringProperty()
    panel: bpy.props.StringProperty()

    def execute(self, context):
        setattr(context.scene, f"radcad_{self.panel}_icon", self.name)
        
        if self.panel == "line":
            if self.name == "line":
                bpy.ops.view3d.line_polyline('INVOKE_DEFAULT')
            elif self.name == "line_perpendicular_from_curve": 
                bpy.ops.view3d.line_perp_from_curve('INVOKE_DEFAULT')
            elif self.name == "line_tangent_to_two_curves": 
                bpy.ops.view3d.line_tan_tan('INVOKE_DEFAULT')
            elif self.name == "line_perpendicular_to_two_curves":
                bpy.ops.view3d.line_perp_to_two_curves('INVOKE_DEFAULT')
            elif self.name == "line_tangent_from_curve":
                bpy.ops.view3d.line_tangent_from_curve('INVOKE_DEFAULT')

        elif self.panel == "curve":
            if self.name == "curve_interpolate_points":
                bpy.ops.view3d.curve_interpolate('INVOKE_DEFAULT')
            elif self.name == "curve_freehand":
                bpy.ops.view3d.curve_freehand('INVOKE_DEFAULT')

        elif self.panel == "arc" and self.name == "arc_1_point":
            bpy.ops.view3d.arc_overlay_preview('INVOKE_DEFAULT')
        elif self.panel == "arc" and self.name == "arc_2_point":
            bpy.ops.view3d.arc_2pt('INVOKE_DEFAULT')
        elif self.panel == "arc" and self.name == "arc_3_point":
            bpy.ops.view3d.arc_3pt('INVOKE_DEFAULT')
            
        elif self.panel == "circle" and self.name == "circle_center_radius":
            bpy.ops.view3d.circle_1pt('INVOKE_DEFAULT')
        elif self.panel == "circle" and self.name == "circle_2_points":
            bpy.ops.view3d.circle_2pt('INVOKE_DEFAULT')
        elif self.panel == "circle" and self.name == "circle_3_points": 
            bpy.ops.view3d.circle_3pt('INVOKE_DEFAULT')
        elif self.panel == "circle" and self.name == "circle_tangent_to_three_curves":
            bpy.ops.view3d.radcad_circle_tan_tan_tan('INVOKE_DEFAULT')
        elif self.panel == "circle" and self.name == "circle_tangent_to_two_curves":
            bpy.ops.view3d.radcad_circle_tan_tan('INVOKE_DEFAULT')

        elif self.panel == "ellipse" and self.name == "ellipse_from_radius":
            bpy.ops.view3d.ellipse_radius('INVOKE_DEFAULT')
        elif self.panel == "ellipse" and self.name == "ellipse_foci_point":
            bpy.ops.view3d.ellipse_foci('INVOKE_DEFAULT')
        elif self.panel == "ellipse" and self.name == "ellipse_from_endpoints":
            bpy.ops.view3d.ellipse_endpoints('INVOKE_DEFAULT')
        elif self.panel == "ellipse" and self.name == "ellipse_from_corners":
            bpy.ops.view3d.ellipse_corners('INVOKE_DEFAULT')

        elif self.panel == "polygon" and self.name == "polygon_cen_cor":
            bpy.ops.view3d.polygon_cen_cor('INVOKE_DEFAULT')
        elif self.panel == "polygon" and self.name == "polygon_cen_tan":
            bpy.ops.view3d.polygon_cen_tan('INVOKE_DEFAULT')
        elif self.panel == "polygon" and self.name == "polygon_cor_cor":
            bpy.ops.view3d.polygon_cor_cor('INVOKE_DEFAULT')
        elif self.panel == "polygon" and self.name == "polygon_size_size": 
            bpy.ops.view3d.polygon_edge('INVOKE_DEFAULT')
            
        elif self.panel == "point":
            if self.name == "point_by_arcs":
                bpy.ops.view3d.point_by_arcs('INVOKE_DEFAULT')
            elif self.name == "point_center":
                bpy.ops.view3d.point_center('INVOKE_DEFAULT')
            elif self.name == "point_edge_center":
                bpy.ops.view3d.point_edge_center('INVOKE_DEFAULT')

        elif self.panel == "rectangle":
            if self.name == "rectangle_from_center":
                bpy.ops.view3d.rectangle_cen_cor('INVOKE_DEFAULT')
            elif self.name == "rectangle_from_corners":
                bpy.ops.view3d.rectangle_cor_cor('INVOKE_DEFAULT')
            elif self.name == "rectangle_3_points":
                bpy.ops.view3d.rectangle_3_points('INVOKE_DEFAULT')
            
        return {'FINISHED'}

def draw_header(layout, icon_key):
    col = layout.column(align=True)
    col.alignment = 'CENTER'
    col.ui_units_y = HEADER_HEIGHT
    if _has_icon(icon_key):
        col.template_icon(icon_value=preview_collection[icon_key].icon_id, scale=2)
    else:
        col.label(text=" ")
    layout.separator()

def draw_tool_button(layout, key):
    row = layout.row()
    operator_id = TOOL_OPERATORS.get(key)
    label = TOOL_LABELS.get(key, key.replace("_", " ").title())
    if key not in IMPLEMENTED_TOOLS or operator_id is None:
        row.enabled = False 

    row.operator_context = 'INVOKE_REGION_WIN'
    if _has_icon(key):
        row.operator(operator_id or "radcad.generic", text=label, icon_value=preview_collection[key].icon_id)
    else:
        row.operator(operator_id or "radcad.generic", text=label)

class RADCAD_PT_Main(bpy.types.Panel):
    bl_label = "rCAD"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "rCAD"

    def draw(self, context):
        layout = self.layout
        row = layout.row()
        row.scale_y = 1.2
        row.operator("radcad.reset_overlays", icon='TRASH') 
        row = layout.row()
        row.scale_y = 1.2
        preferences_button = row.operator(
            "preferences.addon_show",
            text="Add-on Preferences",
            icon='PREFERENCES',
        )
        preferences_button.module = __package__
        layout.separator()


class RADCAD_PT_AddonDevelopment(bpy.types.Panel):
    bl_label = "Addon Development"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "rCAD"
    bl_parent_id = "RADCAD_PT_Main"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        box = self.layout.box()
        box.label(text="Reload the complete rCAD addon")
        box.operator("wm.radcad_reload_addon", text="Reload rCAD", icon='FILE_REFRESH')

class RADCAD_PT_Point(bpy.types.Panel):
    bl_label = "Point"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "rCAD"
    bl_parent_id = "RADCAD_PT_Main"

    def draw(self, context):
        draw_header(self.layout, context.scene.radcad_point_icon)
        draw_tool_button(self.layout, "point_by_arcs")
        draw_tool_button(self.layout, "point_center")
        draw_tool_button(self.layout, "point_edge_center")

class RADCAD_PT_Line(bpy.types.Panel):
    bl_label = "Line"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "rCAD"
    bl_parent_id = "RADCAD_PT_Main"

    def draw(self, context):
        draw_header(self.layout, context.scene.radcad_line_icon)
        for key in sorted(k for k in SVG_FILES if k.startswith("line")):
            draw_tool_button(self.layout, key)

class RADCAD_PT_Arc(bpy.types.Panel):
    bl_label = "Arc"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "rCAD"
    bl_parent_id = "RADCAD_PT_Main"

    def draw(self, context):
        draw_header(self.layout, context.scene.radcad_arc_icon)
        keys = [k for k in SVG_FILES if k.startswith("arc")]
        for key in sorted(keys):
            draw_tool_button(self.layout, key)

class RADCAD_PT_Circle(bpy.types.Panel):
    bl_label = "Circle"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "rCAD"
    bl_parent_id = "RADCAD_PT_Main"

    def draw(self, context):
        draw_header(self.layout, context.scene.radcad_circle_icon)
        circle_order = (
            "circle_center_radius",
            "circle_2_points",
            "circle_3_points",
            "circle_tangent_to_three_curves",
            "circle_tangent_to_two_curves",
        )
        for key in circle_order:
            if key in SVG_FILES:
                draw_tool_button(self.layout, key)
        for key in sorted(k for k in SVG_FILES if k.startswith("circle") and k not in circle_order):
            draw_tool_button(self.layout, key)

class RADCAD_PT_Ellipse(bpy.types.Panel):
    bl_label = "Ellipse"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "rCAD"
    bl_parent_id = "RADCAD_PT_Main"

    def draw(self, context):
        draw_header(self.layout, context.scene.radcad_ellipse_icon)
        for key in sorted(k for k in SVG_FILES if k.startswith("ellipse")):
            draw_tool_button(self.layout, key)

class RADCAD_PT_Polygon(bpy.types.Panel):
    bl_label = "Polygon"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "rCAD"
    bl_parent_id = "RADCAD_PT_Main"

    def draw(self, context):
        draw_header(self.layout, context.scene.radcad_polygon_icon)
        for key in sorted(k for k in SVG_FILES if k.startswith("polygon")):
            draw_tool_button(self.layout, key)

class RADCAD_PT_Curve(bpy.types.Panel):
    bl_label = "Curve"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "rCAD"
    bl_parent_id = "RADCAD_PT_Main"

    def draw(self, context):
        draw_header(self.layout, context.scene.radcad_curve_icon)
        for key in sorted(k for k in SVG_FILES if k.startswith("curve")):
            draw_tool_button(self.layout, key)

class RADCAD_PT_Rectangle(bpy.types.Panel):
    bl_label = "Rectangle"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "rCAD"
    bl_parent_id = "RADCAD_PT_Main"

    def draw(self, context):
        draw_header(self.layout, context.scene.radcad_rectangle_icon)
        for key in sorted(k for k in SVG_FILES if k.startswith("rectangle")):
            draw_tool_button(self.layout, key)

class RADCAD_PT_Dimension(bpy.types.Panel):
    bl_label = "Dimension"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "rCAD"
    bl_parent_id = "RADCAD_PT_Main"

    def draw(self, context):
        layout = self.layout
        row = layout.row(align=True)
        row.scale_y = 1.2
        row.operator("view3d.radcad_dimension_linear", text="Linear Dimension", icon="DRIVER_DISTANCE")
        row.operator(
            "view3d.radcad_dimension_parameters",
            text="",
            icon="PREFERENCES",
        )


class RADCAD_PT_Erase(bpy.types.Panel):
    bl_label = "Erase"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "rCAD"
    bl_parent_id = "RADCAD_PT_Main"

    def draw(self, context):
        row = self.layout.row()
        row.scale_y = 1.6
        row.operator_context = "INVOKE_REGION_WIN"
        if _has_icon("erase"):
            row.operator(
                "view3d.radcad_erase",
                text="Erase",
                icon_value=preview_collection["erase"].icon_id,
            )
        else:
            row.operator("view3d.radcad_erase", text="Erase", icon="BRUSH_DATA")


class RADCAD_PT_Rotate(bpy.types.Panel):
    bl_label = "Rotate"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "rCAD"
    bl_parent_id = "RADCAD_PT_Main"

    def draw(self, context):
        row = self.layout.row()
        row.scale_y = 1.6
        row.operator_context = "INVOKE_REGION_WIN"
        row.operator(
            "view3d.radcad_rotate",
            text="Rotate",
            icon="ORIENTATION_GIMBAL",
        )


class RADCAD_PT_ConstructionLine(bpy.types.Panel):
    bl_label = "Construction Lines"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "rCAD"
    bl_parent_id = "RADCAD_PT_Main"

    def draw(self, context):
        layout = self.layout
        row = layout.row(align=True)
        row.scale_y = 1.6
        row.operator_context = "INVOKE_REGION_WIN"
        row.operator(
            "view3d.radcad_construction_line",
            text="Construction Line",
            icon="TRACKING",
        )
        row.operator(
            "view3d.radcad_construction_parameters",
            text="",
            icon="PREFERENCES",
        )

classes = (
    RADCAD_OT_reset_overlays, 
    RADCAD_OT_ReloadAddon,
    RADCAD_OT_generic,
    RADCAD_PT_Main,
    RADCAD_PT_AddonDevelopment,
    RADCAD_PT_Point,
    RADCAD_PT_Line,
    RADCAD_PT_Arc,
    RADCAD_PT_Circle,
    RADCAD_PT_Ellipse,
    RADCAD_PT_Polygon,
    RADCAD_PT_Curve,
    RADCAD_PT_Rectangle,
    RADCAD_PT_Dimension,
    RADCAD_PT_Erase,
    RADCAD_PT_Rotate,
    RADCAD_PT_ConstructionLine,
)

def register():
    global preview_collection
    preview_collection = bpy.utils.previews.new()

    for key, filename in SVG_FILES.items():
        for folder in ICON_FOLDERS:
            path = os.path.join(folder, filename)
            if os.path.isfile(path):
                preview_collection.load(key, path, "IMAGE")
                break

    for key, filename in DEFAULT_ICON_FILES.items():
        for folder in ICON_FOLDERS:
            path = os.path.join(folder, filename)
            if os.path.isfile(path):
                preview_collection.load(key, path, "IMAGE")
                break

    bpy.types.Scene.radcad_line_icon = bpy.props.StringProperty(default="line_default")
    bpy.types.Scene.radcad_arc_icon = bpy.props.StringProperty(default="arc_default")
    bpy.types.Scene.radcad_circle_icon = bpy.props.StringProperty(default="circle")
    bpy.types.Scene.radcad_ellipse_icon = bpy.props.StringProperty(default="ellipse")
    bpy.types.Scene.radcad_polygon_icon = bpy.props.StringProperty(default="polygon_default")
    bpy.types.Scene.radcad_curve_icon = bpy.props.StringProperty(default="curve_interpolate_points")
    bpy.types.Scene.radcad_rectangle_icon = bpy.props.StringProperty(default="rectangle_default")
    bpy.types.Scene.radcad_point_icon = bpy.props.StringProperty(default="point_default")
    bpy.types.Scene.radcad_dimension_icon = bpy.props.StringProperty(default="dimension_linear")

    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.radcad_line_icon
    del bpy.types.Scene.radcad_arc_icon
    del bpy.types.Scene.radcad_circle_icon
    del bpy.types.Scene.radcad_ellipse_icon
    del bpy.types.Scene.radcad_polygon_icon
    del bpy.types.Scene.radcad_curve_icon
    del bpy.types.Scene.radcad_rectangle_icon
    del bpy.types.Scene.radcad_point_icon
    del bpy.types.Scene.radcad_dimension_icon

    if preview_collection:
        bpy.utils.previews.remove(preview_collection)

if __name__ == "__main__":
    register()
