# text_entry_utils.py
# The Bouncer: Handles keyboard input events and parsing application.

import bpy
import math
from mathutils import Vector
from .modal_state import state
from .units_utils import parse_length_input
from .plane_utils import world_to_plane 
from .geometry_utils import arc_points_world

def apply_input_value(ctx):
    """
    Taking the user's string, parsing it, and updating the Arc state.
    """
    val_str = state["input_string"]
    tool_mode = state.get("tool_mode", "1POINT")
    
    # --- RADIUS (OR DISTANCE) INPUT ---
    if state["input_mode"] == 'RADIUS':
        val_meters = parse_length_input(val_str)
        state["radius"] = max(0.0001, abs(val_meters)) # Stores the value
        
        # === 1-POINT LOGIC ===
        if tool_mode == "1POINT":
            if state["stage"] == 1:
                d = state["current"] - state["pivot"]
                if d.length > 1e-9: d.normalize()
                else: d = Vector((1,0,0))
                state["start"] = state["pivot"] + (d * state["radius"])
                state["current"] = state["start"]
                rvec2 = world_to_plane(state["start"] - state["pivot"], state["Xp"], state["Yp"])
                a0 = math.atan2(rvec2.y, rvec2.x)
                state["a0"] = a0; state["a1"] = a0; state["a_prev_raw"] = a0
                state["accum_angle"] = 0.0
                state["stage"] = 2
            elif state["stage"] == 2:
                d = state["start"] - state["pivot"]
                if d.length > 0: 
                    d.normalize()
                    state["start"] = state["pivot"] + d * state["radius"]

        # === POINTS BY ARCS LOGIC ===
        elif tool_mode == "POINT_BY_ARCS":
            if state["stage"] in [1, 4]:
                pv = state["pivot"]
                d = state["current"] - pv
                if d.length > 1e-9: d.normalize()
                else: d = Vector((1,0,0))
                state["start"] = pv + (d * state["radius"])
                state["current"] = state["start"]
                rvec2 = world_to_plane(state["start"] - pv, state["Xp"], state["Yp"])
                a0 = math.atan2(rvec2.y, rvec2.x)
                state["a0"] = a0; state["a1"] = a0; state["a_prev_raw"] = a0
                state["accum_angle"] = 0.0
                state["stage"] += 1

        # === 2-POINT LOGIC ===
        elif tool_mode == "2POINT":
            if state["stage"] == 1:
                d = state["current"] - state["pivot"]
                if d.length > 1e-9: d.normalize()
                else: d = Vector((1,0,0))
                state["p2"] = state["pivot"] + (d * abs(val_meters))
                state["current"] = state["p2"]
                state["stage"] = 2
                state["p1"] = state["pivot"]
                state["midpoint"] = (state["p1"] + state["p2"]) * 0.5
            elif state["stage"] == 2:
                mid = state["midpoint"]
                curr_peak = state["start"] 
                if curr_peak and mid:
                    d = curr_peak - mid
                    if d.length > 1e-9: d.normalize()
                    else: d = state["Zp"] if state["Zp"] else Vector((0,0,1))
                    state["start"] = mid + (d * abs(val_meters))

        # === ELLIPSE TOOLS LOGIC ===
        elif tool_mode == "ELLIPSE_RADIUS":
            if state["stage"] == 1:
                diam = abs(val_meters)
                state["rx"] = diam * 0.5
                # Update current to reflect the diameter endpoint
                p1 = state["pivot"]
                d_vec = state["current"] - p1
                if d_vec.length > 1e-9: d_vec.normalize()
                else: d_vec = state.get("Xp") or Vector((1,0,0))
                state["current"] = p1 + d_vec * diam
                state["stage"] = 2
            elif state["stage"] == 2:
                ry = abs(val_meters)
                state["ry"] = ry
                # Update current to reflect the minor radius endpoint
                p1 = state["pivot"]
                Xp = state.get("Xp") or Vector((1,0,0))
                Yp = state.get("Yp") or Vector((0,1,0))
                rx = state.get("rx", 0.0)
                center = p1 + (Xp * rx)
                state["current"] = center + (Yp * ry)

        elif tool_mode == "ELLIPSE_FOCI":
            if state["stage"] == 1:
                pv = state["pivot"]
                maj = state.get("Xp") or Vector((1,0,0))
                # Set F2 and IMMEDIATELY move to Stage 2
                state["f1"] = pv
                state["f2"] = pv + (maj * abs(val_meters))
                state["stage"] = 2
            elif state["stage"] == 2:
                ry = abs(val_meters)
                state["ry"] = ry
                # Update current to reflect the minor radius endpoint
                f1, f2 = state.get("f1"), state.get("f2")
                if f1 and f2:
                    center = (f1 + f2) * 0.5
                    Xp = (f2 - f1).normalized()
                    Zp = state.get("Zp") or Vector((0,0,1))
                    Yp = Zp.cross(Xp).normalized()
                    # For Foci mode, current should be on the ellipse itself
                    # dist_sum = 2*a. For current on minor axis, it's at 'b' distance from center.
                    state["current"] = center + (Yp * ry)

        elif tool_mode == "ELLIPSE_ENDPOINTS":
            if state["stage"] == 1:
                p1 = state["pivot"]
                d = state["current"] - p1
                if d.length > 1e-9: d.normalize()
                else: d = Vector((1,0,0))
                
                diam = abs(val_meters)
                p2 = p1 + d * diam
                state["p1"] = p1
                state["p2"] = p2
                state["rx"] = diam * 0.5
                state["current"] = p2
                state["stage"] = 2
            elif state["stage"] == 2:
                ry = abs(val_meters)
                state["ry"] = ry
                # Update current to reflect the minor radius endpoint
                p1, p2 = state.get("p1"), state.get("p2")
                if p1 and p2:
                    center = (p1 + p2) * 0.5
                    Xp = (p2 - p1).normalized()
                    Zp = state.get("Zp") or Vector((0,0,1))
                    Yp = Zp.cross(Xp).normalized()
                    state["current"] = center + (Yp * ry)

    # --- ANGLE INPUT (1-POINT ONLY) ---
    elif state["input_mode"] == 'ANGLE':
        try:
            val = float(val_str)
            accum = state.get("accum_angle", 0.0)
            direction = 1.0 if abs(accum) < 1e-6 else (-1.0 if accum < 0 else 1.0)
            rad = math.radians(val) * direction
            state["accum_angle"] = rad
            state["a1"] = state["a0"] + rad
            state["a_prev_raw"] = math.atan2(math.sin(state["a1"]), math.cos(state["a1"]))
        except ValueError:
            pass
            
    # --- SEGMENTS INPUT ---
    elif state["input_mode"] == 'SEGMENTS':
        try:
            val = int(val_str)
            state["segments"] = max(3, min(1000, val))
        except ValueError:
            pass

    # Update Preview Points immediately
    state["skip_mouse_update"] = True 
    state["input_mode"] = None
    state["input_screen_pos"] = None


def handle_text_input(ctx, ev):
    if state["input_mode"] is None:
        return False

    curr_str = state["input_string"]
    idx = state["cursor_index"]

    if ev.type == 'RET' or ev.type == 'NUMPAD_ENTER':
        apply_input_value(ctx)
        ctx.area.tag_redraw()
        return True
    elif ev.type == 'ESC':
        state["input_mode"] = None
        state["input_screen_pos"] = None
        ctx.area.tag_redraw()
        return True
    elif ev.type == 'LEFT_ARROW' and ev.value == 'PRESS':
        state["cursor_index"] = max(0, idx - 1)
        ctx.area.tag_redraw()
        return True
    elif ev.type == 'RIGHT_ARROW' and ev.value == 'PRESS':
        state["cursor_index"] = min(len(curr_str), idx + 1)
        ctx.area.tag_redraw()
        return True
    elif ev.type == 'BACK_SPACE' and ev.value == 'PRESS':
        if idx > 0:
            state["input_string"] = curr_str[:idx-1] + curr_str[idx:]
            state["cursor_index"] = idx - 1
            ctx.area.tag_redraw()
        return True
    elif ev.type == 'DEL' and ev.value == 'PRESS':
        if idx < len(curr_str):
            state["input_string"] = curr_str[:idx] + curr_str[idx+1:]
            ctx.area.tag_redraw()
        return True
    elif ev.value == 'PRESS':
        char = ev.unicode
        if char == "-":
            if state["input_mode"] == 'ANGLE':
                if state["input_string"].startswith("-"):
                    state["input_string"] = state["input_string"][1:]
                    state["cursor_index"] = max(0, idx - 1)
                else:
                    state["input_string"] = "-" + state["input_string"]
                    state["cursor_index"] = idx + 1
            else:
                state["input_string"] = curr_str[:idx] + "-" + curr_str[idx:]
                state["cursor_index"] = idx + 1
            ctx.area.tag_redraw()
            return True
        if char and (char.isdigit() or char in ".,'\"cmftinµ/ "):
            state["input_string"] = curr_str[:idx] + char + curr_str[idx:]
            state["cursor_index"] = idx + 1
            ctx.area.tag_redraw()
            return True

    return True
