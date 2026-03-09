# radCAD/tool_previews.py
import math
import gpu
from gpu_extras.batch import batch_for_shader
from mathutils import Vector
import bpy

from .modal_state import state, style
from .plane_utils import world_radius_for_pixel_size
from .orientation_utils import orthonormal_basis_from_normal

# =========================================================================
#  SHADER MANAGEMENT
# =========================================================================

def get_shaders():
    """
    Returns a dict containing the two necessary shaders.
    """
    shaders = {}
    try:
        shaders["POLYLINE"] = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    except Exception:
        try:
            shaders["POLYLINE"] = gpu.shader.from_builtin('3D_UNIFORM_COLOR')
        except:
            shaders["POLYLINE"] = gpu.shader.from_builtin('UNIFORM_COLOR')

    try:
        shaders["UNIFORM"] = gpu.shader.from_builtin('3D_UNIFORM_COLOR')
    except Exception:
        shaders["UNIFORM"] = gpu.shader.from_builtin('UNIFORM_COLOR')
        
    return shaders

def apply_view_bias(points, ctx, amount=0.002, lift_mult=10.0, persp_percent=0.2):
    """Pulls geometry towards the camera to prevent Z-fighting."""
    rv3d = ctx.region_data
    if not rv3d: return points

    is_ortho = (rv3d.view_perspective == 'ORTHO')
    
    if is_ortho:
        view_inv = rv3d.view_matrix.inverted()
        view_z_axis = Vector((view_inv[0][2], view_inv[1][2], view_inv[2][2]))
        current_scale = getattr(rv3d, "ortho_scale", 1.0)
        bias_dist = current_scale * amount * lift_mult 
        bias_vec = view_z_axis * bias_dist
        return [p + bias_vec for p in points]
    else:
        real_amount = persp_percent / 100.0
        view_inv = rv3d.view_matrix.inverted()
        cam_pos = view_inv.translation
        factor = 1.0 - real_amount
        cam_part = cam_pos * real_amount
        return [p * factor + cam_part for p in points]

def get_axis_aligned_color(vec, default_col):
    if vec.length_squared < 1e-6: return default_col
    v_norm = vec.normalized()
    tol = 0.999999 
    if abs(v_norm.dot(Vector((1, 0, 0)))) > tol: return (0.85, 0.00, 0.00, 1.00) # Red
    if abs(v_norm.dot(Vector((0, 1, 0)))) > tol: return (0.00, 0.60, 0.00, 1.00) # Green
    if abs(v_norm.dot(Vector((0, 0, 1)))) > tol: return (0.149, 0.376, 1.0, 1.0) # Blue
    return default_col

def get_render_settings(ctx):
    """Fetches colors, sizes, and crucial 4K scaling factors."""
    prefs = {
        "LIFT_COMPASS": 4.0, "LIFT_ARC": 20.0, "LIFT_PERSP": 0.2,
        "LINE_COL": (1.0, 1.0, 0.0, 0.7),
        "COL_START": (0.8, 0.8, 0.2, 1.0), "COL_END": (0.2, 0.8, 0.2, 1.0),
        "COL_CHORD": (0.2, 0.8, 0.2, 1.0), "COL_HEIGHT": (0.2, 0.8, 0.2, 1.0),
        "PREVIEW_VERTEX_SIZE": 5,
        "UI_SCALE": 1.0,
        "VIEWPORT_SIZE": (100.0, 100.0)
    }
    
    system_prefs = ctx.preferences.system
    prefs["UI_SCALE"] = system_prefs.ui_scale
    
    vp = gpu.state.viewport_get()
    prefs["VIEWPORT_SIZE"] = (float(vp[2]), float(vp[3]))

    pkg = __package__ if __package__ else "radCAD"
    if '.' in pkg: pkg = pkg.split('.')[0] 
    
    try:
        addon_prefs = ctx.preferences.addons[pkg].preferences
        prefs["LIFT_COMPASS"] = addon_prefs.lift_compass
        prefs["LIFT_ARC"] = addon_prefs.lift_arc
        prefs["LIFT_PERSP"] = addon_prefs.lift_perspective
        prefs["LINE_COL"] = addon_prefs.snap_line_color
        prefs["COL_START"] = addon_prefs.color_arc_start
        prefs["COL_END"] = addon_prefs.color_arc_end
        prefs["COL_CHORD"] = addon_prefs.color_arc_2pt_chord
        prefs["COL_HEIGHT"] = addon_prefs.color_arc_2pt_height
        prefs["PREVIEW_VERTEX_SIZE"] = addon_prefs.preview_vertex_size
    except (KeyError, AttributeError):
        pass
        
    return prefs

# =========================================================================
#  PRIMITIVE DRAWERS
# =========================================================================

def setup_polyline_shader(sh, color, width, settings):
    """Configures the complex uniforms required by the Polyline shader."""
    sh.bind()
    sh.uniform_float("color", color)
    try:
        sh.uniform_float("viewportSize", settings["VIEWPORT_SIZE"])
        # FORCE 1.0 MINIMUM
        final_width = max(1.0, width * settings["UI_SCALE"])
        sh.uniform_float("lineWidth", final_width)
        sh.uniform_float("miterLimit", 1.0) 
    except Exception:
        pass

def draw_compass_geometry(ctx, shaders, center, Xp, Yp, rotation_radians, size_px, angle_inc, color, settings):
    """Draws the compass using the Polyline shader."""
    if center is None or Xp is None or Yp is None: return
    
    radius = world_radius_for_pixel_size(ctx, center, Xp, Yp, size_px)
    if radius <= 0: radius = 0.2
    
    outerR = radius; innerR = outerR * (80.0 / 120.0)
    tickLen = outerR * (10.0 / 120.0); crossLen = outerR * (10.0 / 120.0)
    safe_inc = max(angle_inc, 0.1); tickCount = int(360.0 / safe_inc)
    segs_circle = 72
    cosr, sinr = math.cos(rotation_radians), math.sin(rotation_radians)
    def R(ax, ay): return (ax * cosr - ay * sinr, ax * sinr + ay * cosr)
    
    # 1. Circle
    circle_segs = []
    for i in range(segs_circle):
        a1 = (i / segs_circle) * 2.0 * math.pi
        a2 = ((i + 1) / segs_circle) * 2.0 * math.pi
        ax1, ay1 = math.cos(a1) * outerR, math.sin(a1) * outerR
        ax2, ay2 = math.cos(a2) * outerR, math.sin(a2) * outerR
        rx1, ry1 = R(ax1, ay1); rx2, ry2 = R(ax2, ay2)
        p1 = center + Xp * rx1 + Yp * ry1
        p2 = center + Xp * rx2 + Yp * ry2
        circle_segs.extend([p1, p2])
        
    # 2. Ticks
    tick_segs = []
    for i in range(tickCount):
        a = (i / tickCount) * 2.0 * math.pi
        ax1, ay1 = math.cos(a) * outerR, math.sin(a) * outerR
        ax2, ay2 = math.cos(a) * (outerR - tickLen), math.sin(a) * (outerR - tickLen)
        rx1, ry1 = R(ax1, ay1); rx2, ry2 = R(ax2, ay2)
        tick_segs.extend([center + Xp * rx1 + Yp * ry1, center + Xp * rx2 + Yp * ry2])
        
    # 3. Arcs (RESTORED FROM STEAL THIS)
    arc_segs = []
    def add_arc_segs(a_start, a_end):
        steps = 48
        ax_start, ay_start = math.cos(a_start) * innerR, math.sin(a_start) * innerR
        rx_start, ry_start = R(ax_start, ay_start)
        p_start = center + Xp * rx_start + Yp * ry_start
        ax_end, ay_end = math.cos(a_end) * innerR, math.sin(a_end) * innerR
        rx_end, ry_end = R(ax_end, ay_end)
        p_end = center + Xp * rx_end + Yp * ry_end
        
        for i in range(steps):
            t1 = i / steps
            t2 = (i + 1) / steps
            ang1 = a_start + (a_end - a_start) * t1
            ang2 = a_start + (a_end - a_start) * t2
            ax1, ay1 = math.cos(ang1) * innerR, math.sin(ang1) * innerR
            ax2, ay2 = math.cos(ang2) * innerR, math.sin(ang2) * innerR
            rx1, ry1 = R(ax1, ay1); rx2, ry2 = R(ax2, ay2)
            p1 = center + Xp * rx1 + Yp * ry1
            p2 = center + Xp * rx2 + Yp * ry2
            arc_segs.extend([p1, p2])
        arc_segs.extend([p_start, p_end])
            
    add_arc_segs(math.radians(200), math.radians(340))
    add_arc_segs(math.radians(20), math.radians(160))
    
    # 4. Cross
    cross_segs = [
        center + Xp * (-crossLen), center + Xp * (crossLen), 
        center + Yp * (-crossLen), center + Yp * (crossLen)
    ]

    # Bias
    lift = settings["LIFT_COMPASS"]
    persp = settings["LIFT_PERSP"]
    circle_segs = apply_view_bias(circle_segs, ctx, lift_mult=lift, persp_percent=persp)
    tick_segs = apply_view_bias(tick_segs, ctx, lift_mult=lift, persp_percent=persp)
    arc_segs = apply_view_bias(arc_segs, ctx, lift_mult=lift, persp_percent=persp)
    cross_segs = apply_view_bias(cross_segs, ctx, lift_mult=lift, persp_percent=persp)

    # Draw
    sh = shaders["POLYLINE"]
    setup_polyline_shader(sh, color, 1.0, settings)
    
    if circle_segs: batch_for_shader(sh, 'LINES', {"pos": circle_segs}).draw(sh)
    if tick_segs: batch_for_shader(sh, 'LINES', {"pos": tick_segs}).draw(sh)
    if arc_segs: batch_for_shader(sh, 'LINES', {"pos": arc_segs}).draw(sh)
    if cross_segs: batch_for_shader(sh, 'LINES', {"pos": cross_segs}).draw(sh)

def draw_line(ctx, shaders, p1, p2, color, settings):
    """Draws a single high-quality line."""
    if p1 is None or p2 is None: return
    pts = apply_view_bias([p1, p2], ctx, lift_mult=settings["LIFT_ARC"], persp_percent=settings["LIFT_PERSP"])
    sh = shaders["POLYLINE"]
    
    # --- TEAL KILLER (Fixes the Perpendicular Light) ---
    # Catches Cyan/Teal (0, 1, 1) and forces it to Gold (1.0, 0.8, 0.0)
    final_color = color
    if len(color) >= 3 and color[0] < 0.1 and color[1] > 0.9 and color[2] > 0.9:
        final_color = (1.0, 0.8, 0.0, 1.0) # Gold
        
    setup_polyline_shader(sh, final_color, 1.0, settings)
    batch_for_shader(sh, 'LINES', {"pos": pts}).draw(sh)

def draw_polyline(ctx, shaders, points, color, settings, custom_lift=None, custom_width=1.0):
    if not points or len(points) < 2: return
    segments = []
    for i in range(len(points) - 1):
        segments.append(points[i])
        segments.append(points[i+1])
    
    lift = custom_lift if custom_lift is not None else settings["LIFT_ARC"]
    pts = apply_view_bias(segments, ctx, lift_mult=lift, persp_percent=settings["LIFT_PERSP"])
    sh = shaders["POLYLINE"]
    setup_polyline_shader(sh, color, custom_width, settings)
    batch_for_shader(sh, 'LINES', {"pos": pts}).draw(sh)

def get_round_point_shader():
    # Force fallback to avoid Blender 4.2+ crash
    return None

def draw_points(ctx, shaders, points, color, size, settings):
    """Draws perfect round dots using a custom shader."""
    if not points: return
    pts = apply_view_bias(points, ctx, lift_mult=settings["LIFT_ARC"], persp_percent=settings["LIFT_PERSP"])
    
    sh = get_round_point_shader()
    
    if sh:
        sh.bind()
        sh.uniform_float("color", color)
        final_size = max(1.0, size * settings["UI_SCALE"])
        sh.uniform_float("size", final_size)
        batch_for_shader(sh, 'POINTS', {"pos": pts}).draw(sh)
    else:
        # Fallback
        sh = shaders["UNIFORM"]
        sh.bind()
        sh.uniform_float("color", color)
        final_size = max(1.0, size * settings["UI_SCALE"])
        gpu.state.point_size_set(int(final_size))
        batch_for_shader(sh, 'POINTS', {"pos": pts}).draw(sh)

# =========================================================================
#  TOOL SPECIALISTS
# =========================================================================

def draw_preview_1point(ctx, shaders, prefs):
    center = state["pivot"] if state["pivot"] is not None else state["last_surface_hit"]
    Xc, Yc, Zc = state["Xp"], state["Yp"], state["Zp"]
    orient_normal = Zc if Zc else (state["locked_normal"] if (state["locked"] and state["locked_normal"]) else state["last_surface_normal"])
    
    if Xc is None or Yc is None:
        if orient_normal: Xc, Yc, Zc = orthonormal_basis_from_normal(orient_normal)
        
    # --- FIX: Default to BLACK if not aligned ---
    compass_col = (0.0, 0.0, 0.0, 1.0) # Black default
    
    if orient_normal:
        compass_col = get_axis_aligned_color(orient_normal, compass_col)
    
    c_size = state.get("compass_size", 125)
    a_inc = state.get("angle_increment", 15.0)
    
    if center and Xc and Yc:
        draw_compass_geometry(ctx, shaders, center, Xc, Yc, state["compass_rot"], c_size, a_inc, compass_col, prefs)

    pv = state["pivot"]
    if not pv: return

    # USE UNIFORM SIZE FROM PREFS (Default 5)
    pt_size = prefs.get("PREVIEW_VERTEX_SIZE", 5)

    if state["stage"] == 1 and state["current"] is not None:
        draw_line(ctx, shaders, pv, state["current"], prefs["COL_START"], prefs)
        
        # If in Circle Mode, draw preview IMMEDIATELY
        if state.get("tool_mode") == "CIRCLE_1POINT":
            pts = state.get("preview_pts", [])
            if pts:
                draw_polyline(ctx, shaders, pts, (0,0,0,1), prefs)
                draw_points(ctx, shaders, pts, (0,0,0,1), pt_size, prefs)
    
    elif state["stage"] == 2:
        if state["start"]:
            draw_line(ctx, shaders, pv, state["start"], prefs["COL_START"], prefs)
        
        pts = state.get("preview_pts", [])
        if pts:
            draw_polyline(ctx, shaders, pts, (0,0,0,1), prefs)
            draw_points(ctx, shaders, pts, (0,0,0,1), pt_size, prefs)
            draw_line(ctx, shaders, pv, pts[-1], prefs["COL_END"], prefs)


def draw_preview_2point(ctx, shaders, prefs):
    pt_size = prefs.get("PREVIEW_VERTEX_SIZE", 5)

    # --- STAGE 0: Initial Cursor Dot ---
    if state["stage"] == 0:
        target = state.get("snap_point") if state.get("snap_point") else state.get("current")
        if target:
            draw_points(ctx, shaders, [target], (0,0,0,1), pt_size, prefs)
    
    pv = state["pivot"]
    if not pv: return

    # --- STAGE 1: Dragging Chord (or Diameter) ---
    if state["stage"] == 1 and state["current"] is not None:
        draw_points(ctx, shaders, [pv], (0,0,0,1), pt_size, prefs)
        draw_points(ctx, shaders, [state["current"]], (0,0,0,1), pt_size, prefs)
        
        diff = state["current"] - pv
        col = get_axis_aligned_color(diff, (0.5, 0.5, 0.5, 0.5))
        draw_line(ctx, shaders, pv, state["current"], col, prefs)

        if state.get("tool_mode") == "CIRCLE_2POINT":
            pts = state.get("preview_pts", [])
            if pts:
                draw_polyline(ctx, shaders, pts, (0,0,0,1), prefs)
                draw_points(ctx, shaders, pts, (0,0,0,1), pt_size, prefs)

    # --- STAGE 2: Dragging Height (Arcs Only) ---
    elif state["stage"] == 2:
        p1 = state.get("p1")
        p2 = state.get("p2")
        
        if p1: draw_points(ctx, shaders, [p1], (0,0,0,1), pt_size, prefs)
        if p2: draw_points(ctx, shaders, [p2], (0,0,0,1), pt_size, prefs)
        
        if p1 and p2:
            chord_vec = p2 - p1
            col_c = get_axis_aligned_color(chord_vec, (0.5, 0.5, 0.5, 0.5))
            draw_line(ctx, shaders, p1, p2, col_c, prefs)
            
        if state["start"] is not None:
            peak = state["start"]
            height_vec = peak - pv
            col_h = get_axis_aligned_color(height_vec, prefs["COL_HEIGHT"])
            draw_line(ctx, shaders, pv, peak, col_h, prefs)
            
        pts = state.get("preview_pts", [])
        if pts:
            draw_polyline(ctx, shaders, pts, (0,0,0,1), prefs)
            draw_points(ctx, shaders, pts, (0,0,0,1), pt_size, prefs)


def draw_preview_3point(ctx, shaders, prefs):
    pt_size = prefs.get("PREVIEW_VERTEX_SIZE", 5)
    if state["stage"] == 0:
        target = state.get("snap_point") if state.get("snap_point") else state.get("current")
        if target:
            draw_points(ctx, shaders, [target], (0,0,0,1), pt_size, prefs)

    pv = state["pivot"] 
    if not pv: return

    if state["stage"] == 1 and state["current"] is not None:
        draw_points(ctx, shaders, [pv], (0,0,0,1), pt_size, prefs)
        diff = state["current"] - pv
        col = get_axis_aligned_color(diff, (0.5, 0.5, 0.5, 0.5))
        draw_line(ctx, shaders, pv, state["current"], col, prefs)
        
    elif state["stage"] == 2:
        p1 = state.get("p1")
        p2 = state.get("p2")
        p3 = state.get("current")
        
        if p1: draw_points(ctx, shaders, [p1], (0,0,0,1), pt_size, prefs)
        if p2: draw_points(ctx, shaders, [p2], (0,0,0,1), pt_size, prefs)
        
        if p1 and p2:
            # Color chord by axis
            chord_vec = p2 - p1
            col_c = get_axis_aligned_color(chord_vec, (0.5, 0.5, 0.5, 0.5))
            draw_line(ctx, shaders, p1, p2, col_c, prefs)
            
        if p2 and p3:
            # Color curvature segment by axis
            curv_vec = p3 - p2
            col_v = get_axis_aligned_color(curv_vec, (0.5, 0.5, 0.5, 0.5))
            draw_line(ctx, shaders, p2, p3, col_v, prefs)
            
        pts = state.get("preview_pts", [])
        if pts:
            draw_polyline(ctx, shaders, pts, (0,0,0,1), prefs)
            draw_points(ctx, shaders, pts, (0,0,0,1), pt_size, prefs)

def draw_preview_ellipse(ctx, shaders, prefs):
    # Reuse standard compass drawing
    center = state["pivot"]
    
    mode = state.get("tool_mode")
    
    # Special Handling for Corners mode: Center is computed, not pivot
    if mode == "ELLIPSE_CORNERS" and state["stage"] == 1 and state["current"] is not None:
        # Re-calc center for display
        d_vec = state["current"] - state["pivot"]
        if state["Xp"] and state["Yp"]:
            width = d_vec.dot(state["Xp"])
            height = d_vec.dot(state["Yp"])
            center = state["pivot"] + (state["Xp"] * (width * 0.5)) + (state["Yp"] * (height * 0.5))
    
    if not center:
        # If in Stage 0, draw compass at surface hit
        center = state.get("last_surface_hit")
        
    if center and state["Xp"] and state["Yp"]:
         draw_compass_geometry(ctx, shaders, center, state["Xp"], state["Yp"], 0.0, 125, 15.0, (0,0,0,1), prefs)

    pv = state["pivot"]
    if not pv: return
    pt_size = prefs.get("PREVIEW_VERTEX_SIZE", 5)

    # --- ELLIPSE FOCI DRAWING ---
    if mode == "ELLIPSE_FOCI":
        # Draw F1
        if "f1" in state and state["f1"]:
            draw_points(ctx, shaders, [state["f1"]], (0,0,0,1), pt_size, prefs)
            
        # Draw F2 (if set or dragging)
        if state["stage"] >= 1:
            f2 = state.get("f2") if state.get("f2") else state.get("current")
            if f2:
                draw_points(ctx, shaders, [f2], (0,0,0,1), pt_size, prefs)
                if state["f1"]:
                    draw_line(ctx, shaders, state["f1"], f2, prefs["COL_CHORD"], prefs)

    # --- ELLIPSE CORNERS DRAWING ---
    if mode == "ELLIPSE_CORNERS" and state["stage"] == 1:
        # Draw Bounding Box logic
        cur = state["current"]
        if cur and state["Xp"] and state["Yp"]:
            d_vec = cur - pv
            w = d_vec.dot(state["Xp"])
            h = d_vec.dot(state["Yp"])
            
            p1 = pv
            p2 = pv + state["Xp"] * w
            p3 = pv + state["Xp"] * w + state["Yp"] * h
            p4 = pv + state["Yp"] * h
            
            # Draw Box
            draw_line(ctx, shaders, p1, p2, prefs["COL_CHORD"], prefs)
            draw_line(ctx, shaders, p2, p3, prefs["COL_CHORD"], prefs)
            draw_line(ctx, shaders, p3, p4, prefs["COL_CHORD"], prefs)
            draw_line(ctx, shaders, p4, p1, prefs["COL_CHORD"], prefs)
            
            # Draw Ellipse
            pts = state.get("preview_pts", [])
            if pts:
                 draw_polyline(ctx, shaders, pts, (0,0,0,1), prefs)
                 draw_points(ctx, shaders, pts, (0,0,0,1), pt_size, prefs)
        return

    # --- STANDARD ELLIPSE DRAWING (Radius/Endpoints) ---
    if state["stage"] == 1 and state["current"] is not None:
        draw_points(ctx, shaders, [pv], (0,0,0,1), pt_size, prefs)
        draw_line(ctx, shaders, pv, state["current"], prefs["COL_START"], prefs)
        
    elif state["stage"] == 2:
        draw_points(ctx, shaders, [pv], (0,0,0,1), pt_size, prefs)
        
        # Draw Major Axis (Ghosted/Reference)
        if mode == "ELLIPSE_RADIUS" and "Xp" in state and "rx" in state:
            end_major = pv + (state["Xp"] * state["rx"])
            draw_line(ctx, shaders, pv, end_major, prefs["COL_START"], prefs)
            
        # Draw Full Diameter for Endpoint Mode
        if mode == "ELLIPSE_ENDPOINTS" and "p1" in state and "p2" in state:
            if state["p1"] and state["p2"]:
                draw_line(ctx, shaders, state["p1"], state["p2"], prefs["COL_START"], prefs)
            
        # Draw Minor Axis line (to cursor)
        if state["current"] is not None:
             # Just draw a line from pivot (or center) to cursor to visualize the height pull
             draw_line(ctx, shaders, pv, state["current"], prefs["COL_HEIGHT"], prefs)

        # Draw Ellipse Polyline
        pts = state.get("preview_pts", [])
        if pts:
             draw_polyline(ctx, shaders, pts, (0,0,0,1), prefs)
             draw_points(ctx, shaders, pts, (0,0,0,1), pt_size, prefs)

def draw_preview_polygon(ctx, shaders, prefs):
    pv = state["pivot"]
    if not pv: return
    pt_size = prefs.get("PREVIEW_VERTEX_SIZE", 5)
    if state["stage"] == 1 and state["current"] is not None:
        draw_points(ctx, shaders, [pv], (0,0,0,1), pt_size, prefs)
        draw_line(ctx, shaders, pv, state["current"], prefs["COL_START"], prefs)
        draw_points(ctx, shaders, [state["current"]], (0,0,0,1), pt_size, prefs)
        pts = state.get("preview_pts", [])
        if pts:
            draw_polyline(ctx, shaders, pts, (0,0,0,1), prefs)
            draw_points(ctx, shaders, pts, (0,0,0,1), pt_size, prefs)
    
    # RECTANGLE 3 POINT SUPPORT
    if state["tool_mode"] == "RECTANGLE_3_POINTS" and state["stage"] == 2:
        pts = state.get("preview_pts", [])
        if pts:
            draw_polyline(ctx, shaders, pts, (0,0,0,1), prefs)
            draw_points(ctx, shaders, pts, (0,0,0,1), pt_size, prefs)


def draw_preview_circle_3point(ctx, shaders, prefs):
    pt_size = prefs.get("PREVIEW_VERTEX_SIZE", 5)
    pts = state.get("preview_pts", [])
    if pts:
        draw_polyline(ctx, shaders, pts, (0,0,0,1), prefs)
        draw_points(ctx, shaders, pts, (0,0,0,1), pt_size, prefs)

def draw_preview_tan_tan(ctx, shaders, prefs):
    pt_size = prefs.get("PREVIEW_VERTEX_SIZE", 5)
    
    # 1. Background Math Circle (Grey)
    v_pts = state.get("visual_pts", [])
    if v_pts:
        draw_polyline(ctx, shaders, v_pts, (0.5, 0.5, 0.5, 0.5), prefs)
        
    # 2. Foreground Mesh Geometry (Black) - Added +2.0 lift to pop over grey
    p_pts = state.get("preview_pts", [])
    if p_pts:
        draw_polyline(ctx, shaders, p_pts, (0,0,0,1), prefs, custom_lift=prefs["LIFT_ARC"] + 2.0)
        draw_points(ctx, shaders, p_pts, (0,0,0,1), pt_size, prefs)
        
    # 3. Tangency Viz
    viz_tan = state.get("viz_tangent_line")
    if viz_tan and len(viz_tan) == 2:
        draw_line(ctx, shaders, viz_tan[0], viz_tan[1], (1, 0.8, 0, 1), prefs)
        
    viz_diam = state.get("viz_diameter_line")
    if viz_diam and len(viz_diam) == 2:
        draw_line(ctx, shaders, viz_diam[0], viz_diam[1], (1, 0.8, 0, 1), prefs)

def draw_preview_tan_tan_tan(ctx, shaders, prefs):
    pt_size = prefs.get("PREVIEW_VERTEX_SIZE", 5)
    
    # 1. Background Math Circle (Grey)
    v_pts = state.get("visual_pts", [])
    if v_pts:
        draw_polyline(ctx, shaders, v_pts, (0.5, 0.5, 0.5, 1.0), prefs)
        
    # 2. Foreground Mesh Geometry (Grey Circle)
    p_pts = state.get("preview_pts", [])
    if p_pts:
        draw_polyline(ctx, shaders, p_pts, (0.5, 0.5, 0.5, 1.0), prefs, custom_lift=prefs["LIFT_ARC"] + 2.0)
        # NO DOTS ON CIRCLE FOR CLEAN PREVIEW

    # 3. Inscribed Polygon (Black Triangle or N-gon)
    tan_poly_pts = state.get("tan_points_poly", [])
    if tan_poly_pts and len(tan_poly_pts) >= 3:
        # Create poly segments (closed loop)
        poly_draw_pts = []
        for i in range(len(tan_poly_pts)):
            poly_draw_pts.append(tan_poly_pts[i])
            poly_draw_pts.append(tan_poly_pts[(i + 1) % len(tan_poly_pts)])
            
        draw_polyline(ctx, shaders, poly_draw_pts, (0, 0, 0, 1), prefs, custom_lift=prefs["LIFT_ARC"] + 5.0)
        # Orange Dots at vertices
        draw_points(ctx, shaders, tan_poly_pts, (1.0, 0.5, 0.0, 1.0), pt_size + 2, prefs)


def draw_cb_3d():
    if not state["active"]: return
    try:
        ctx = bpy.context
        is_xray = False
        if ctx.space_data and ctx.space_data.type == 'VIEW_3D':
            shading = ctx.space_data.shading
            if shading.type == 'WIREFRAME' or shading.show_xray:
                is_xray = True
        
        gpu.state.blend_set("ALPHA")
        gpu.state.depth_test_set('NONE' if is_xray else 'LESS_EQUAL')
        
        settings = get_render_settings(ctx)
        shaders = get_shaders()
        
        # --- NEW: DRAW GREY CATMULL OUTLINES FOR INPUT SELECTION ---
        catmull_outlines = state.get("catmull_spline_previews", [])
        if catmull_outlines:
            for c_pts in catmull_outlines:
                # Grey color (R=0.5, G=0.5, B=0.5, A=0.7)
                draw_polyline(ctx, shaders, c_pts, (0.5, 0.5, 0.5, 0.7), settings, custom_lift=settings["LIFT_ARC"] - 5.0, custom_width=2.0)
        
        # --- REMOVED SPLINE OVERLAYS TO PRESERVE ORANGE SELECTION ---

        mode = state.get("tool_mode", "1POINT")
        
        if mode == "POINT_BY_ARCS":
            center = state["pivot"] if state["pivot"] else state["last_surface_hit"]
            Xc, Yc = state["Xp"], state["Yp"]
            if center and Xc and Yc:
                draw_compass_geometry(ctx, shaders, center, Xc, Yc, state["compass_rot"], 125, 15.0, (0,0,0,1), settings)
            if state.get("arc1_pts"):
                draw_polyline(ctx, shaders, state["arc1_pts"], (0.2, 0.5, 1.0, 0.8), settings)
            if state.get("preview_pts"):
                draw_polyline(ctx, shaders, state["preview_pts"], (0.2, 0.8, 0.2, 1.0), settings)
            if state.get("intersection_pts"):
                draw_points(ctx, shaders, state["intersection_pts"], (1.0, 0.0, 0.0, 1.0), 10, settings)
            if state["stage"] in [1, 4] and state["pivot"] and state["current"]:
                draw_line(ctx, shaders, state["pivot"], state["current"], settings["COL_START"], settings)
                
        # UPDATED: Added all Line Curve Tools
        elif mode in ["LINE_POLY", "CURVE_INTERPOLATE", "LINE_PERP_FROM_CURVE", "LINE_PERP_TO_TWO_CURVES", "LINE_TANGENT_FROM_CURVE", "LINE_TAN_TAN"]:
            
            pts = state.get("preview_pts", [])
            if pts:
                # DEFAULT COLOR
                base_color = (0, 0, 0, 1) # Black
                
                # If LINE_POLY, handle segment coloring for axis snaps
                if mode == "LINE_POLY" and len(pts) >= 2:
                    # Draw fixed segments (all except last segment)
                    if len(pts) > 2:
                        draw_polyline(ctx, shaders, pts[:-1], base_color, settings)
                    
                    # Draw active segment (last 2 points)
                    active_seg = pts[-2:]
                    active_col = base_color
                    
                    axis_vec = state.get("current_axis_vector")
                    if axis_vec:
                        active_col = get_axis_aligned_color(axis_vec, base_color)
                    
                    draw_polyline(ctx, shaders, active_seg, active_col, settings)
                else:
                    # Fallback for other tools or single point
                    draw_polyline(ctx, shaders, pts, base_color, settings)

                # Dots
                draw_points(ctx, shaders, pts, base_color, settings.get("PREVIEW_VERTEX_SIZE", 5), settings) 

            # RESTORED: Hover Dot (But now Black Size 4, not Yellow Size 8)
            if state.get("stage", 0) == 0:
                target = state.get("snap_point") if state.get("snap_point") else state.get("current")
                if target: draw_points(ctx, shaders, [target], (0.0, 0.0, 0.0, 1.0), settings.get("PREVIEW_VERTEX_SIZE", 5), settings)
            if state.get("current"):
                draw_points(ctx, shaders, [state["current"]], (0.0, 0.0, 0.0, 1.0), settings.get("PREVIEW_VERTEX_SIZE", 5), settings)

        elif mode == "CIRCLE_TAN_TAN_TAN":
            from .tool_previews import draw_preview_tan_tan_tan # Just in case
            draw_preview_tan_tan_tan(ctx, shaders, settings)
            
        elif mode == "CIRCLE_TAN_TAN":
            from .tool_previews import draw_preview_tan_tan
            draw_preview_tan_tan(ctx, shaders, settings)

        elif mode == "1POINT" or mode == "CIRCLE_1POINT":
            draw_preview_1point(ctx, shaders, settings)
            
        elif mode == "2POINT" or mode == "CIRCLE_2POINT":
            draw_preview_2point(ctx, shaders, settings)
            
        elif mode == "3POINT":
            draw_preview_3point(ctx, shaders, settings)
            
        elif mode == "CIRCLE_3POINT": 
            from .tool_previews import draw_preview_circle_3point
            draw_preview_circle_3point(ctx, shaders, settings)
            
        elif mode in ["ELLIPSE_RADIUS", "ELLIPSE_FOCI", "ELLIPSE_ENDPOINTS", "ELLIPSE_CORNERS"]:
            draw_preview_ellipse(ctx, shaders, settings)
            
        elif mode in ["POLYGON_CENTER_CORNER", "POLYGON_CENTER_TANGENT", "POLYGON_CORNER_CORNER", "POLYGON_EDGE", "RECTANGLE_CENTER_CORNER", "RECTANGLE_CORNER_CORNER", "RECTANGLE_3_POINTS"]: 
            draw_preview_polygon(ctx, shaders, settings)

        gpu.state.depth_test_set('NONE')
        gpu.state.blend_set("NONE")
    except Exception as e:
        print(f"DRAW ERROR: {e}")