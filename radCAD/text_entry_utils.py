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
        # Allow negative height/distance if desired, but usually radius is pos.
        # For 2-Point Height, negative is valid (flips arc), so we take raw value for 2-Point?
        # Let's keep it simple: radius is abs for 1-Point, but 2-Point height might need sign.
        # Actually standard radius is usually positive.
        
        state["radius"] = max(0.0001, abs(val_meters)) # Stores the value
        
        # === 1-POINT LOGIC ===
        if tool_mode == "1POINT":
            if state["stage"] == 1:
                # Setting Start Point via Radius
                d = state["current"] - state["pivot"]
                if d.length > 1e-9: d.normalize()
                else: d = Vector((1,0,0))
                    
                state["start"] = state["pivot"] + (d * state["radius"])
                state["current"] = state["start"]
                
                # Initialize Angle
                rvec2 = world_to_plane(state["start"] - state["pivot"], state["Xp"], state["Yp"])
                a0 = math.atan2(rvec2.y, rvec2.x)
                state["a0"] = a0; state["a1"] = a0; state["a_prev_raw"] = a0
                state["accum_angle"] = 0.0
                state["stage"] = 2
                
            elif state["stage"] == 2:
                # Update Radius of existing arc
                d = state["start"] - state["pivot"]
                if d.length > 0: 
                    d.normalize()
                    state["start"] = state["pivot"] + d * state["radius"]

        # === POINTS BY ARCS LOGIC ===
        elif tool_mode == "POINT_BY_ARCS":
            if state["stage"] in [1, 4]:
                # Setting Radius for Arc 1 or Arc 2
                pv = state["pivot"]
                d = state["current"] - pv
                if d.length > 1e-9: d.normalize()
                else: d = Vector((1,0,0))
                
                state["start"] = pv + (d * state["radius"])
                state["current"] = state["start"]
                
                # Init Angle
                rvec2 = world_to_plane(state["start"] - pv, state["Xp"], state["Yp"])
                a0 = math.atan2(rvec2.y, rvec2.x)
                state["a0"] = a0; state["a1"] = a0; state["a_prev_raw"] = a0
                state["accum_angle"] = 0.0
                
                # Advance stage (1->2, 4->5)
                state["stage"] += 1

        # === 2-POINT LOGIC ===
        elif tool_mode == "2POINT":
            # Stage 1: Setting Chord Length (P2 distance from P1)
            if state["stage"] == 1:
                d = state["current"] - state["pivot"]
                if d.length > 1e-9: d.normalize()
                else: d = Vector((1,0,0))
                
                # Apply entered distance
                state["p2"] = state["pivot"] + (d * abs(val_meters))
                state["current"] = state["p2"]
                # We do NOT advance stage automatically on distance entry usually?
                # Actually standard CAD behavior is enter -> commit -> next stage.
                state["stage"] = 2
                
                # Setup Stage 2 defaults
                state["p1"] = state["pivot"]
                state["midpoint"] = (state["p1"] + state["p2"]) * 0.5
                
            # Stage 2: Setting Height (Sagitta)
            elif state["stage"] == 2:
                # Use the entered value as height
                height = val_meters # Can be negative? 
                # Let's trust the sign entered or just magnitude.
                # Usually height input is magnitude in direction of mouse.
                
                # We need the direction from Mid to Start (Peak)
                # If start isn't valid yet, we might fail.
                # But ArcTool_2Point updates "start" constantly.
                mid = state["midpoint"]
                curr_peak = state["start"] 
                
                if curr_peak and mid:
                    d = curr_peak - mid
                    if d.length > 1e-9: d.normalize()
                    else: d = state["Zp"] if state["Zp"] else Vector((0,0,1))
                    
                    # Apply Height
                    state["start"] = mid + (d * abs(height))
                    
                    # NOTE: ArcTool_2Point.update() logic calculates radius/pts from this new "start"
                    # We rely on the next update loop to fix the geometry based on this new peak.

    # --- ANGLE INPUT (1-POINT ONLY) ---
    elif state["input_mode"] == 'ANGLE':
        try:
            val = float(val_str)
            
            # --- FIX: Respect Current Drawing Direction ---
            # If current sweep is zero (just started drag), we use mouse position vs start angle.
            # Otherwise use current sweep sign.
            accum = state.get("accum_angle", 0.0)
            if abs(accum) < 1e-6:
                direction = 1.0 # Default to CCW if unsure
                # Potentially use a_prev_raw vs a0 here...
            else:
                direction = -1.0 if accum < 0 else 1.0
                
            rad = math.radians(val) * direction
                
            state["accum_angle"] = rad
            state["a1"] = state["a0"] + rad
            
            # Update mouse memory so next move is relative to this new angle
            state["a_prev_raw"] = math.atan2(math.sin(state["a1"]), math.cos(state["a1"]))
            
            # --- COMMIT LOGIC REMOVED ---
            # Hitting enter now just updates the angle but keeps the tool active
                
        except ValueError:
            pass
            
    # --- SEGMENTS INPUT ---
    elif state["input_mode"] == 'SEGMENTS':
        try:
            val = int(val_str)
            state["segments"] = max(3, min(1000, val))
        except ValueError:
            pass

    # Update Preview Points immediately (Common)
    if tool_mode in ["1POINT", "2POINT", "POINT_BY_ARCS"]:
        state["skip_mouse_update"] = True # Use the stamp logic we added
        
    # Reset Input Mode
    state["input_mode"] = None
    state["input_screen_pos"] = None


def handle_text_input(ctx, ev):
    """
    Main event handler for text entry.
    Returns True if the event was consumed (don't pass to navigation).
    """
    if state["input_mode"] is None:
        return False

    curr_str = state["input_string"]
    idx = state["cursor_index"]

    # ENTER -> Apply
    if ev.type == 'RET' or ev.type == 'NUMPAD_ENTER':
        apply_input_value(ctx)
        ctx.area.tag_redraw()
        return True
        
    # ESC -> Cancel Input
    elif ev.type == 'ESC':
        state["input_mode"] = None
        state["input_screen_pos"] = None
        ctx.area.tag_redraw()
        return True
        
    # ARROWS -> Move Cursor
    elif ev.type == 'LEFT_ARROW' and ev.value == 'PRESS':
        state["cursor_index"] = max(0, idx - 1)
        ctx.area.tag_redraw()
        return True

    elif ev.type == 'RIGHT_ARROW' and ev.value == 'PRESS':
        state["cursor_index"] = min(len(curr_str), idx + 1)
        ctx.area.tag_redraw()
        return True

    # BACKSPACE -> Delete Left
    elif ev.type == 'BACK_SPACE' and ev.value == 'PRESS':
        if idx > 0:
            state["input_string"] = curr_str[:idx-1] + curr_str[idx:]
            state["cursor_index"] = idx - 1
            ctx.area.tag_redraw()
        return True
        
    # DELETE -> Delete Current
    elif ev.type == 'DEL' and ev.value == 'PRESS':
        if idx < len(curr_str):
            state["input_string"] = curr_str[:idx] + curr_str[idx+1:]
            ctx.area.tag_redraw()
        return True

    # TYPING
    elif ev.value == 'PRESS':
        char = ev.unicode
        
        # Handle Negative Sign logic
        if char == "-":
            if state["input_mode"] == 'ANGLE':
                # Toggle negative at start
                if state["input_string"].startswith("-"):
                    state["input_string"] = state["input_string"][1:]
                    state["cursor_index"] = max(0, idx - 1)
                else:
                    state["input_string"] = "-" + state["input_string"]
                    state["cursor_index"] = idx + 1
            else:
                # Normal hyphen insertion (e.g. 10-1/2)
                state["input_string"] = curr_str[:idx] + "-" + curr_str[idx:]
                state["cursor_index"] = idx + 1
            ctx.area.tag_redraw()
            return True
            
        # Allow digits, decimals, quotes, spaces, units
        if char and (char.isdigit() or char in ".,'\"cmftinµ/ "):
            state["input_string"] = curr_str[:idx] + char + curr_str[idx:]
            state["cursor_index"] = idx + 1
            ctx.area.tag_redraw()
            return True

    return True # Consumed but no action