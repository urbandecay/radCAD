# radCAD/hud_overlay.py
import bpy
import blf
import gpu
import math
from gpu_extras.batch import batch_for_shader
from bpy_extras.view3d_utils import location_3d_to_region_2d
from .modal_state import state, style
from .units_utils import format_length

# --- FONT SIZES ---
SIZE_KEY = 12
SIZE_LABEL = 10 

# --- SAFE SHADER LOADER (2D) ---
def get_2d_shader():
    try:
        return gpu.shader.from_builtin('2D_UNIFORM_COLOR')
    except Exception:
        return gpu.shader.from_builtin('UNIFORM_COLOR')

# --- HELPER: MEASURE MIXED TEXT ---
def get_mixed_text_metrics(font_id, text):
    """
    Splits text into Key (Size 12) and Label (Size 10).
    Returns (key_str, label_str, width_key, width_label, text_height)
    """
    if ":" in text:
        parts = text.split(":", 1)
        key_str = parts[0] + ":"
        label_str = parts[1]
    elif "\u2220" in text: # Angle Symbol
        key_str = "\u2220"
        label_str = text.replace("\u2220", "")
    else:
        key_str = text
        label_str = ""
        
    # Measure Key
    blf.size(font_id, SIZE_KEY)
    w_key = blf.dimensions(font_id, key_str)[0]
    h_key = blf.dimensions(font_id, "Hg")[1] # Use generic height
    
    # Measure Label
    blf.size(font_id, SIZE_LABEL)
    w_lbl = blf.dimensions(font_id, label_str)[0]
    h_lbl = blf.dimensions(font_id, "Hg")[1]
    
    # Use the max height of the Key font (12) to determine vertical center
    total_h = max(h_key, h_lbl)
    
    return key_str, label_str, w_key, w_lbl, total_h

# --- GENERIC BOX (Upper Overlay & Measurements) ---
def draw_ui_box_generic(x, y, text, active=False, bg_color_override=None):
    font_id = 0
    pad = style["bg_padding"]
    
    # 1. Measure
    key_str, label_str, w_key, w_lbl, txt_h = get_mixed_text_metrics(font_id, text)
    
    total_w = w_key + w_lbl
    box_w = total_w + (pad * 2)
    box_h = txt_h + (pad * 2) 
    
    x1, y1 = x, y
    x2, y2 = x + box_w, y - box_h 
    
    verts = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    
    # SAFE SHADER
    sh = get_2d_shader()
    gpu.state.blend_set("ALPHA")
    sh.bind()
    
    bg_col = bg_color_override if bg_color_override else (style["bg_color_active"] if active else style["bg_color"])
    sh.uniform_float("color", bg_col)
    
    batch_for_shader(sh, 'TRI_FAN', {"pos": verts}).draw(sh)
    
    # 2. Draw Text (Vertically Centered)
    blf.color(font_id, *style["font_color"])
    
    # Calculate Y to center text: Middle of box - 1/3 of text height
    center_y = y - (box_h / 2)
    text_y = center_y - (txt_h / 3.0)
    
    # Draw Key (Size 12)
    blf.size(font_id, SIZE_KEY)
    blf.position(font_id, x + pad, text_y, 0)
    blf.draw(font_id, key_str)
    
    # Draw Label (Size 10)
    blf.size(font_id, SIZE_LABEL)
    blf.position(font_id, x + pad + w_key, text_y, 0)
    blf.draw(font_id, label_str)
    
    gpu.state.blend_set("NONE")
    return box_h

# --- BUTTON DRAWER (Bottom Bar) ---
def draw_ui_button(x, y, text, is_active, hitbox_id):
    font_id = 0
    pad_x = 10
    pad_y = 8 # Increased slightly for easier clicking
    
    # 1. Measure
    key_str, label_str, w_key, w_lbl, txt_h = get_mixed_text_metrics(font_id, text)
    
    total_text_w = w_key + w_lbl
    
    # Consistent height based on font size + padding
    box_w = total_text_w + (pad_x * 2)
    box_h = txt_h + (pad_y * 2) + 4
    
    x1, y1 = x, y
    x2, y2 = x + box_w, y + box_h
    
    if hitbox_id:
        state["ui_hitboxes"][hitbox_id] = (x1, x2, y1, y2)
    
    # === ROUNDED RECT GENERATION ===
    rad = 5  # Corner Radius
    segs = 6 # Smoothness
    verts = []
    
    # Helper to append arc vertices
    def add_arc(cx, cy, start_ang, end_ang):
        for i in range(segs + 1):
            a = start_ang + (end_ang - start_ang) * (i / segs)
            verts.append((cx + rad * math.cos(a), cy + rad * math.sin(a)))

    # Order: Top-Right, Top-Left, Bottom-Left, Bottom-Right
    add_arc(x2 - rad, y2 - rad, 0, math.pi / 2)           # TR
    add_arc(x1 + rad, y2 - rad, math.pi / 2, math.pi)     # TL
    add_arc(x1 + rad, y1 + rad, math.pi, 1.5 * math.pi)   # BL
    add_arc(x2 - rad, y1 + rad, 1.5 * math.pi, 2 * math.pi) # BR
    
    # === DRAW BACKGROUND ===
    bg_col = style["bg_color_active"] if is_active else style["bg_color"]
    sh = get_2d_shader()
    gpu.state.blend_set("ALPHA")
    sh.bind()
    sh.uniform_float("color", bg_col)
    
    batch_for_shader(sh, 'TRI_FAN', {"pos": verts}).draw(sh)
    
    # === BUTTON STYLING (Outline Only) ===
    gpu.state.line_width_set(1.0)
    sh.uniform_float("color", (0.0, 0.0, 0.0, 0.5))
    batch_for_shader(sh, 'LINE_LOOP', {"pos": verts}).draw(sh)
    # ---------------------------------------

    # 2. Draw Text (CENTERED VERTICALLY & HORIZONTALLY)
    blf.color(font_id, *style["font_color"])
    
    # Horizontal Center
    box_center_x = x1 + (box_w / 2)
    start_tx = box_center_x - (total_text_w / 2)
    
    # Vertical Center
    box_center_y = y1 + (box_h / 2)
    text_y = box_center_y - (txt_h / 3.0) 
    
    # Draw Key (Size 12)
    blf.size(font_id, SIZE_KEY)
    blf.position(font_id, start_tx, text_y, 0)
    blf.draw(font_id, key_str)
    
    # Draw Label (Size 10)
    blf.size(font_id, SIZE_LABEL)
    blf.position(font_id, start_tx + w_key, text_y, 0)
    blf.draw(font_id, label_str)
    
    gpu.state.blend_set("NONE")
    return box_w

# --- TOP RIGHT HOTKEYS ---
def draw_hotkeys_panel():
    if not state.get("show_hotkeys", True): return

    perp_state = "ON" if state.get("is_perpendicular") else "OFF"
    perp_col = None 

    # Axis Constraint Logic
    axis_text = "X/Y/Z: Axis Constraint"
    axis_col = None
    c_axis = state.get("constraint_axis")
    
    if c_axis:
        if abs(c_axis.x) > 0.9:
            axis_text = "X-Axis Locked"
            axis_col = (0.85, 0.2, 0.2, 1.0)
        elif abs(c_axis.y) > 0.9:
            axis_text = "Y-Axis Locked"
            axis_col = (0.2, 0.8, 0.2, 1.0)
        elif abs(c_axis.z) > 0.9:
            axis_text = "Z-Axis Locked"
            axis_col = (0.2, 0.4, 1.0, 1.0)

    # --- LOCK LABEL LOGIC ---
    tool_mode = state.get("tool_mode", "1POINT")
    is_locked = state.get("locked")
    
    lock_text = "L: Lock Plane" # Default Not Locked
    lock_col = None
    
    if tool_mode == "2POINT":
         lock_text = "L: Lock Axis"
    
    if is_locked:
        if tool_mode == "2POINT":
             lock_text = "L: Axis Locked"
        else:
             lock_text = "L: Plane Locked"
        
        # --- NEW: Check if locked to X, Y, or Z Plane ---
        n = state.get("locked_normal")
        if n:
            if abs(n.x) > 0.99:
                lock_text = "X-Plane Locked"
                lock_col = (0.85, 0.2, 0.2, 1.0)
            elif abs(n.y) > 0.99:
                lock_text = "Y-Plane Locked"
                lock_col = (0.2, 0.8, 0.2, 1.0)
            elif abs(n.z) > 0.99:
                lock_text = "Z-Plane Locked"
                lock_col = (0.2, 0.4, 1.0, 1.0)

    lines = [
        (f"P: Perpendicular ({perp_state})", perp_col),
        (lock_text, lock_col),
        (axis_text, axis_col),
    ]
    
    # --- INSERT SPACER IF RADIUS/DISTANCE IS ABOUT TO BE SHOWN ---
    if state["stage"] >= 1:
        lines.append((None, None))
        if state.get("tool_mode") == "2POINT":
            lines.append(("D: Set Distance", None))
        elif state.get("tool_mode") == "LINE_POLY":
            lines.append(("L: Set Length", None)) # --- NEW: Line Length Hint ---
        else:
            lines.append(("R: Set Radius", None))
    
    if state["stage"] == 2:
        if state.get("tool_mode") != "2POINT":
            lines.append(("A: Set Angle", None))
        else:
            # === NEW: Show Alt Hint for 2-Point ===
            lines.append(("Alt: Bypass 180\u00B0", None))
            
        lines.append(("S: Set Segments", None))
        lines.append(("Scroll: +/- Segs", None))

    ctx = bpy.context
    width = ctx.region.width
    height = ctx.region.height
    
    off_x = state.get("hotkeys_offset_x", 1000)
    off_y = state.get("hotkeys_offset_y", 20)
    
    px = off_x
    current_y = height - off_y
    
    for text, col in lines:
        # Check for Spacer
        if text is None:
            current_y -= 10 # Add 10px vertical gap
            continue
            
        h = draw_ui_box_generic(px, current_y, text, bg_color_override=col)
        current_y -= (h + 2)

# --- BOTTOM SNAPPING BAR ---
def draw_bottom_bar():
    ctx = bpy.context
    width = ctx.region.width
    
    # FIXED: Increased Margin to avoid Header conflict
    margin_bottom = 60 
    button_spacing = 8
    
    buttons = [
        ("F1: Vert", state.get("snap_verts", False), "snap_verts"),
        ("F2: Edge", state.get("snap_edges", False), "snap_edges"),
        ("F3: Edge Center", state.get("snap_edge_center", False), "snap_edge_center"),
        ("F4: Face Center", state.get("snap_face_center", False), "snap_face_center"),
        ("F5: Axis", state.get("use_axis_inference", True), "toggle_axis"),
        ("C: Compass", state.get("use_angle_snap", True), "toggle_angle"),
        ("W: Weld", state.get("auto_weld", True), "weld_btn"),
    ]
    
    # === NEW: "Next Solution" Button ===
    if state.get("choosing_solution"):
         # Pink color to indicate action
         buttons.append(("Tab: Next Solution", True, "next_sol"))
    
    font_id = 0
    total_w = 0
    pad_x = 10
    
    # Calculate Total Width for Screen Centering
    for label, _, _ in buttons:
        _, _, w_k, w_l, _ = get_mixed_text_metrics(font_id, label)
        total_w += (w_k + w_l) + (pad_x * 2) + button_spacing
    total_w -= button_spacing
    
    start_x = (width - total_w) / 2
    curr_x = start_x
    curr_y = margin_bottom
    
    # --- SNAP LABEL ---
    label_size = 16 
    blf.size(font_id, label_size)
    snap_label = "Snap"
    snap_w = blf.dimensions(font_id, snap_label)[0]
    
    snap_x = start_x + (total_w / 2) - (snap_w / 2)
    
    blf.color(font_id, *style["font_color"])
    blf.position(font_id, snap_x, margin_bottom + 38, 0)
    blf.draw(font_id, snap_label)
    
    # Draw Buttons
    for label, active, hit_id in buttons:
        w = draw_ui_button(curr_x, curr_y, label, active, hit_id)
        curr_x += w + button_spacing

def get_display_str(label, raw_str, is_active):
    if not is_active: return f"{label} {raw_str}"
    idx = state["cursor_index"]
    idx = max(0, min(idx, len(raw_str)))
    s_with_cursor = raw_str[:idx] + "|" + raw_str[idx:]
    return f"{label} {s_with_cursor}"

def draw_hud_2d():
    if not state["active"]: return
    try:
        # --- NEW 2D SNAP MARKER (CUSTOMIZABLE) ---
        if state.get("snap_point"):
            import bpy
            ctx = bpy.context
            reg, rv3d = ctx.region, ctx.region_data
            
            # --- DEFAULT FALLBACKS ---
            snap_type = 'X'
            snap_sz = 6
            snap_col = (1.0, 0.8, 0.0, 1.0)
            
            pkg = __package__ if __package__ else "radCAD"
            if '.' in pkg: pkg = pkg.split('.')[0] 
            
            try:
                prefs = bpy.context.preferences.addons[pkg].preferences
                
                # --- CHECK TOOL MODE FOR CORRECT PREFS ---
                mode = state.get("tool_mode", "1POINT")
                
                if mode == "2POINT":
                    snap_sz = getattr(prefs, "snap_marker_size_2pt", 6)
                    snap_col = getattr(prefs, "snap_marker_color_2pt", (1.0, 0.8, 0.0, 1.0))
                    snap_type = getattr(prefs, "snap_marker_type_2pt", 'X')
                else:
                    snap_sz = getattr(prefs, "snap_marker_size", 6)
                    snap_col = getattr(prefs, "snap_marker_color", (1.0, 0.8, 0.0, 1.0))
                    snap_type = getattr(prefs, "snap_marker_type", 'X')
                    
            except Exception:
                pass

            p2d = location_3d_to_region_2d(reg, rv3d, state["snap_point"])
            
            if p2d:
                cx, cy = p2d
                sh = get_2d_shader()
                gpu.state.blend_set("ALPHA")
                sh.bind()
                sh.uniform_float("color", snap_col)
                gpu.state.line_width_set(2.0)
                
                # --- DRAW BASED ON TYPE ---
                if snap_type == 'CIRCLE':
                    circle_pts = []
                    steps = 16
                    for i in range(steps):
                        a = (i / steps) * 2.0 * math.pi
                        circle_pts.append((cx + snap_sz * math.cos(a), cy + snap_sz * math.sin(a)))
                    batch_for_shader(sh, 'LINE_LOOP', {"pos": circle_pts}).draw(sh)

                elif snap_type == 'DOT':
                    dot_pts = [(cx, cy)]
                    steps = 12
                    for i in range(steps + 1):
                        a = (i / steps) * 2.0 * math.pi
                        dot_pts.append((cx + snap_sz * math.cos(a), cy + snap_sz * math.sin(a)))
                    batch_for_shader(sh, 'TRI_FAN', {"pos": dot_pts}).draw(sh)
                    
                else: # Default 'X'
                    x_coords = [
                        (cx - snap_sz, cy - snap_sz), (cx + snap_sz, cy + snap_sz),
                        (cx - snap_sz, cy + snap_sz), (cx + snap_sz, cy - snap_sz)
                    ]
                    batch_for_shader(sh, 'LINES', {"pos": x_coords}).draw(sh)

                gpu.state.blend_set("NONE")

        # 1. Preview Points (Arc Fill or Phantom Circles)
        if state.get("preview_pts"):
            import bpy
            ctx = bpy.context
            reg, rv3d = ctx.region, ctx.region_data
            sh = get_2d_shader()
            gpu.state.blend_set("ALPHA")
            
            # --- Tan-Tan-Tan Special Case ---
            if state.get("tool_mode") == "CIRCLE_TAN_TAN_TAN":
                # ONLY draw the Grey Phantom Inputs (Stage 0).
                # We skip drawing if "choosing_solution" is True (Stage 1), removing the pink overlay.
                if not state.get("choosing_solution"):
                    col = (0.5, 0.5, 0.5, 0.8) # Grey for Phantom Inputs
                    gpu.state.line_width_set(1.0)
                    
                    sh.bind()
                    sh.uniform_float("color", col)
                    
                    # Convert all 3D points to 2D
                    pts_2d = []
                    for p in state["preview_pts"]:
                        p2 = location_3d_to_region_2d(reg, rv3d, p)
                        if p2: pts_2d.append(p2)
                    
                    # Draw as Line Strip (points are usually ordered in a loop)
                    if pts_2d:
                        batch_for_shader(sh, 'LINE_STRIP', {"pos": pts_2d}).draw(sh)
                    
                gpu.state.line_width_set(1.0)
                
            else:
                # --- Standard Arc Fill (TRI_FAN) ---
                if state.get("tool_mode") != "POINT_BY_ARCS":
                    base_pt = style.get("point_px", 4)
                    final_pt = max(1, base_pt - 1) if bpy.app.version >= (5, 0, 0) else base_pt
                    r = final_pt / 2.0
                    col = (0.0, 0.0, 0.0, 1.0)
                    steps = 16
                    for p in state["preview_pts"]:
                        p2d = location_3d_to_region_2d(reg, rv3d, p)
                        if not p2d: continue
                        cx, cy = p2d
                        verts = [(cx, cy)]
                        for i in range(steps + 1):
                            a = (i / steps) * 2.0 * math.pi
                            verts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
                        batch = batch_for_shader(sh, "TRI_FAN", {"pos": verts})
                        sh.bind()
                        sh.uniform_float("color", col)
                        batch.draw(sh)
                    
            gpu.state.blend_set("NONE")
        
        # 2. Draw Menus
        draw_hotkeys_panel()
        draw_bottom_bar()
        
        # 3. Measurements
        if state["show_measure"]:
            import bpy
            ctx = bpy.context
            reg, rv3d = ctx.region, ctx.region_data
            
            px, py = 0, 0
            if state["input_mode"] is not None and state["input_screen_pos"]:
                px, py = state["input_screen_pos"]
            elif state["pivot"] is not None:
                pivot_2d = location_3d_to_region_2d(reg, rv3d, state["pivot"])
                if pivot_2d:
                    px = pivot_2d.x + state.get("overlay_offset_x", 75)
                    py = pivot_2d.y + state.get("overlay_offset_y", 0)
            else:
                 return

            current_y = py
            tool_mode = state.get("tool_mode", "1POINT")

            # --- D/R/L LABEL LOGIC ---
            if state["input_mode"] == 'RADIUS': 
                label = "R:"
                if tool_mode == "2POINT": label = "D:"
                elif tool_mode == "LINE_POLY": label = "" # --- REMOVED 'L' for Line Tool
                
                r_txt = get_display_str(label, state['input_string'], True)
                h1 = draw_ui_box_generic(px, current_y, r_txt, active=True)
                current_y -= (h1 + 4)
            else:
                # Tan-Tan-Tan Radius Display
                if state.get("choosing_solution") and state.get("tan_solutions"):
                    idx = state["solution_index"] % len(state["tan_solutions"])
                    r_val = state["tan_solutions"][idx][1]
                    r_txt = "R: " + format_length(r_val)
                    h1 = draw_ui_box_generic(px, current_y, r_txt)
                    current_y -= (h1 + 4)
                elif tool_mode == "2POINT":
                    label = "D: "
                    if state["stage"] == 1:
                         # Chord Length
                         d_val = (state["current"] - state["pivot"]).length if (state["current"] and state["pivot"]) else 0.0
                         r_txt = label + format_length(d_val)
                    elif state["stage"] == 2:
                         # Height
                         h_val = (state["start"] - state["pivot"]).length if (state["start"] and state["pivot"]) else 0.0
                         r_txt = label + format_length(h_val)
                    else:
                         r_txt = "D: 0"
                    h1 = draw_ui_box_generic(px, current_y, r_txt)
                    current_y -= (h1 + 4)
                else:
                    label = "R: "
                    if tool_mode == "LINE_POLY": label = "" # --- REMOVED 'L' for Line Tool
                    
                    r_val = state["radius"] if state["stage"] == 2 else ((state["current"] - state["pivot"]).length if (state["current"] and state["pivot"]) else 0.0)
                    r_txt = label + format_length(r_val)
                    h1 = draw_ui_box_generic(px, current_y, r_txt)
                    current_y -= (h1 + 4)
            
            if state["stage"] == 2:
                # --- HIDE ANGLE IF 2POINT OR LINE_POLY ---
                if tool_mode != "2POINT" and tool_mode != "CIRCLE_TAN_TAN_TAN" and tool_mode != "LINE_POLY":
                    is_input_a = (state["input_mode"] == 'ANGLE')
                    if is_input_a: a_txt = get_display_str("\u2220", state['input_string'], True)
                    else:
                        if state.get("use_radians"): a_txt = f"\u2220 {-state['accum_angle']:.3f} rad"
                        else: a_txt = f"\u2220 {-math.degrees(state['accum_angle']):.1f}\u00B0"
                    h2 = draw_ui_box_generic(px, current_y, a_txt, active=is_input_a)
                    current_y -= (h2 + 4)
                
                # --- HIDE SEGMENTS FOR LINE_POLY ---
                if tool_mode != "LINE_POLY":
                    is_input_s = (state["input_mode"] == 'SEGMENTS')
                    if is_input_s: s_txt = get_display_str("Segments:", state['input_string'], True)
                    else: s_txt = f"Segments: {state['segments']}"
                    draw_ui_box_generic(px, current_y, s_txt, active=is_input_s)
    except Exception as e:
        print(f"HUD DRAW ERROR: {e}")