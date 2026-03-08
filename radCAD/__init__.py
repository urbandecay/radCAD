bl_info = {
    "name": "radCAD",
    "author": "You",
    "version": (2, 7, 8),
    "blender": (4, 2, 0),
    "location": "View3D > N-panel > radCAD",
    "category": "3D View",
    "description": "radCAD-style drawing tools including Arc Tool."
}

import bpy
from importlib import import_module

def _import(name):
    return import_module(f".{name}", package=__name__)

# 1. Import The Engine Parts
prefs = _import("preferences")
panel = _import("panel")

# 2. Import The Operators
op_1pt = _import("operators.op_arc_1pt")
op_2pt = _import("operators.op_arc_2pt")
op_3pt = _import("operators.op_arc_3pt")
op_circle_1pt = _import("operators.op_circle_1pt")
op_circle_2pt = _import("operators.op_circle_2pt")
op_circle_3pt = _import("operators.op_circle_3pt") 
op_circle_tan_tan_tan = _import("operators.op_circle_tan_tan_tan")
op_circle_tan_tan_tan_circles = _import("operators.op_circle_tan_tan_tan_circles") # <--- IMPORT
op_circle_tan_tan = _import("operators.op_circle_tan_tan") 
op_ellipse_radius = _import("operators.op_ellipse_radius")
op_ellipse_foci = _import("operators.op_ellipse_foci")
op_ellipse_endpoints = _import("operators.op_ellipse_endpoints")
op_ellipse_corners = _import("operators.op_ellipse_corners")
op_polygon_cen_cor = _import("operators.op_polygon_cen_cor")
op_polygon_cen_tan = _import("operators.op_polygon_cen_tan")
op_polygon_cor_cor = _import("operators.op_polygon_cor_cor")
op_line_polyline = _import("operators.op_line_polyline")
op_curve_interpolate = _import("operators.op_curve_interpolate")
op_point_by_arcs = _import("operators.op_point_by_arcs")

def register():
    bpy.utils.register_class(prefs.RADCAD_Preferences)

    if not hasattr(bpy.types.Scene, "active_radCAD_tool_id"):
        bpy.types.Scene.active_radCAD_tool_id = bpy.props.StringProperty(
            name="Active radCAD Tool ID",
            default="",
            description="ID of the currently active RadradCAD modal operator"
        )

    bpy.utils.register_class(op_1pt.VIEW3D_OT_arc_overlay_preview)
    bpy.utils.register_class(op_2pt.VIEW3D_OT_arc_2pt)
    bpy.utils.register_class(op_3pt.VIEW3D_OT_arc_3pt)
    bpy.utils.register_class(op_circle_1pt.VIEW3D_OT_circle_1pt)
    bpy.utils.register_class(op_circle_2pt.VIEW3D_OT_circle_2pt)
    bpy.utils.register_class(op_circle_3pt.VIEW3D_OT_circle_3pt) 
    bpy.utils.register_class(op_circle_tan_tan_tan.VIEW3D_OT_circle_tan_tan_tan)
    bpy.utils.register_class(op_circle_tan_tan_tan_circles.VIEW3D_OT_circle_tan_tan_tan_circles) # <--- REGISTER 
    bpy.utils.register_class(op_circle_tan_tan.VIEW3D_OT_circle_tan_tan)
    bpy.utils.register_class(op_ellipse_radius.VIEW3D_OT_ellipse_radius)
    bpy.utils.register_class(op_ellipse_foci.VIEW3D_OT_ellipse_foci)
    bpy.utils.register_class(op_ellipse_endpoints.VIEW3D_OT_ellipse_endpoints)
    bpy.utils.register_class(op_ellipse_corners.VIEW3D_OT_ellipse_corners)
    bpy.utils.register_class(op_polygon_cen_cor.VIEW3D_OT_polygon_cen_cor)
    bpy.utils.register_class(op_polygon_cen_tan.VIEW3D_OT_polygon_cen_tan)
    bpy.utils.register_class(op_polygon_cor_cor.VIEW3D_OT_polygon_cor_cor)
    bpy.utils.register_class(op_line_polyline.VIEW3D_OT_line_polyline)
    bpy.utils.register_class(op_curve_interpolate.VIEW3D_OT_curve_interpolate)
    bpy.utils.register_class(op_point_by_arcs.VIEW3D_OT_point_by_arcs)

    if hasattr(panel, "register"):
        panel.register()

def unregister():
    if hasattr(panel, "unregister"):
        panel.unregister()

    bpy.utils.unregister_class(op_point_by_arcs.VIEW3D_OT_point_by_arcs)
    bpy.utils.unregister_class(op_curve_interpolate.VIEW3D_OT_curve_interpolate)
    bpy.utils.unregister_class(op_line_polyline.VIEW3D_OT_line_polyline)
    bpy.utils.unregister_class(op_polygon_cor_cor.VIEW3D_OT_polygon_cor_cor)
    bpy.utils.unregister_class(op_polygon_cen_tan.VIEW3D_OT_polygon_cen_tan)
    bpy.utils.unregister_class(op_polygon_cen_cor.VIEW3D_OT_polygon_cen_cor)
    bpy.utils.unregister_class(op_ellipse_corners.VIEW3D_OT_ellipse_corners)
    bpy.utils.unregister_class(op_ellipse_endpoints.VIEW3D_OT_ellipse_endpoints)
    bpy.utils.unregister_class(op_ellipse_foci.VIEW3D_OT_ellipse_foci)
    bpy.utils.unregister_class(op_ellipse_radius.VIEW3D_OT_ellipse_radius)
    bpy.utils.unregister_class(op_circle_tan_tan.VIEW3D_OT_circle_tan_tan)
    bpy.utils.unregister_class(op_circle_tan_tan_tan_circles.VIEW3D_OT_circle_tan_tan_tan_circles) # <--- UNREGISTER
    bpy.utils.unregister_class(op_circle_tan_tan_tan.VIEW3D_OT_circle_tan_tan_tan) 
    bpy.utils.unregister_class(op_circle_3pt.VIEW3D_OT_circle_3pt) 
    bpy.utils.unregister_class(op_circle_2pt.VIEW3D_OT_circle_2pt)
    bpy.utils.unregister_class(op_circle_1pt.VIEW3D_OT_circle_1pt)
    bpy.utils.unregister_class(op_3pt.VIEW3D_OT_arc_3pt)
    bpy.utils.unregister_class(op_2pt.VIEW3D_OT_arc_2pt)
    bpy.utils.unregister_class(op_1pt.VIEW3D_OT_arc_overlay_preview)
    
    if hasattr(bpy.types.Scene, "active_radCAD_tool_id"):
        del bpy.types.Scene.active_radCAD_tool_id

    bpy.utils.unregister_class(prefs.RADCAD_Preferences)