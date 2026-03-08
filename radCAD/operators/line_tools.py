import math
from mathutils import Vector, geometry
from ..inference_utils import get_axis_snapped_location
from ..plane_utils import world_to_plane, plane_to_world # Incorporating standard modules
from ..geometry_utils import snap_angle_soft # Ready for angle snapping if needed
from .base_tool import SurfaceDrawTool 

class LineTool_Poly(SurfaceDrawTool):
    def __init__(self, core):
        super().__init__(core)
        self.mode = "LINE_POLY"
        self.points = [] 
        self.current = None
        self.constraint_axis = None
        self.shift_lock_vec = None

    def update(self, context, event, snap_point, snap_normal):
        # Reset axis vec default for the frame
        self.state["current_axis_vector"] = None

        # Stage 0: Just finding the start point
        if self.stage == 0:
            self.update_initial_plane(context, event, snap_point, snap_normal)
            self.current = snap_point
            self.preview_pts = []
            return

        # Stage 1+: Drawing segments
        if self.stage >= 1:
            ref_point = self.points[-1] if self.points else self.pivot
            target = snap_point
            
            # --- SHIFT LOCK LOGIC ---
            if event.shift:
                if self.shift_lock_vec is None:
                    # First frame of press: Check if we are already snapped/inferred
                    strength = max(0.1, min(89.0, self.state.get("snap_strength", 6.0)))
                    inf_loc, inf_axis, _ = get_axis_snapped_location(
                        ref_point, (event.mouse_region_x, event.mouse_region_y), 
                        context, 
                        snap_threshold=math.cos(math.radians(strength))
                    )
                    
                    if inf_axis:
                        # Lock to inferred axis
                        self.shift_lock_vec = inf_axis
                    else:
                        # Lock to raw mouse direction
                        diff = target - ref_point
                        if diff.length_squared > 1e-6:
                            self.shift_lock_vec = diff.normalized()
                
                if self.shift_lock_vec:
                    # Project mouse onto the locked vector line
                    v = target - ref_point
                    dist = v.dot(self.shift_lock_vec)
                    target = ref_point + self.shift_lock_vec * dist
                    self.state["current_axis_vector"] = self.shift_lock_vec
            else:
                self.shift_lock_vec = None
            
            # --- NUMERIC INPUT OVERRIDE ---
            if self.state.get("input_mode") in {'RADIUS', 'LENGTH'} and self.state.get("radius") is not None:
                user_len = self.state["radius"]
                
                # Priority: Axis Key > Shift Lock > Mouse Direction
                if self.constraint_axis:
                    direction = self.constraint_axis
                    self.state["current_axis_vector"] = self.constraint_axis
                elif self.shift_lock_vec:
                    direction = self.shift_lock_vec
                    self.state["current_axis_vector"] = self.shift_lock_vec
                else:
                    direction = target - ref_point

                target = ref_point + (direction.normalized() if direction.length_squared > 1e-6 else self.Xp) * user_len
            
            # --- AXIS CONSTRAINT LOGIC (X/Y/Z KEYS) ---
            elif self.constraint_axis:
                self.state["current_axis_vector"] = self.constraint_axis
                if self.state.get("geometry_snap", False):
                    diff = target - ref_point
                    proj = self.constraint_axis * diff.dot(self.constraint_axis)
                    target = ref_point + proj
                else:
                    from bpy_extras import view3d_utils
                    region = context.region
                    rv3d = context.region_data
                    coord = (event.mouse_region_x, event.mouse_region_y)
                    ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
                    ray_vector = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
                    res = geometry.intersect_line_line(
                        ray_origin, ray_origin + ray_vector, 
                        ref_point, ref_point + self.constraint_axis
                    )
                    if res: target = res[1]
            
            # --- AXIS INFERENCE (SNAPPING) ---
            # Disable inference if Shift is held (prevent fighting with lock)
            elif not self.state.get("geometry_snap", False) and not self.shift_lock_vec:
                strength_deg = self.state.get("snap_strength", 6.0)
                strength_deg = max(0.1, min(89.0, strength_deg))
                axis_thresh = math.cos(math.radians(strength_deg))
                inf_loc, inf_axis, _ = get_axis_snapped_location(
                    ref_point, (event.mouse_region_x, event.mouse_region_y), context, snap_threshold=axis_thresh
                )
                if inf_loc: 
                    target = inf_loc
                    self.state["current_axis_vector"] = inf_axis

            self.current = target
            self.preview_pts = self.points + [self.current]
            self.radius = (self.current - ref_point).length

    def handle_click(self, context, event, snap_point, snap_normal, button_id=None):
        if self.stage == 0:
            self.pivot = snap_point
            self.points.append(snap_point)
            self.state["locked"] = True
            self.state["locked_normal"] = self.Zp
            self.stage = 1
            return 'NEXT_STAGE'

        if self.stage >= 1:
            if (self.current - self.points[-1]).length < 1e-5:
                return None
            self.points.append(self.current)
            self.constraint_axis = None 
            self.state["constraint_axis"] = None
            self.shift_lock_vec = None # Reset shift lock for next segment
            self.pivot = self.current 
            
            if self.state.get("input_mode"):
                self.state["input_mode"] = None
                self.state["input_string"] = ""
                
            return 'NEXT_STAGE'
            
        return None

    def handle_input(self, context, event):
        if super().handle_plane_lock_input(context, event): return True

        # Standard Axis Locking
        if event.type in {'X', 'Y', 'Z'} and event.value == 'PRESS':
            axes = {'X': Vector((1, 0, 0)), 'Y': Vector((0, 1, 0)), 'Z': Vector((0, 0, 1))}
            new_axis = axes[event.type]
            if self.constraint_axis == new_axis:
                self.constraint_axis = None
                self.state["constraint_axis"] = None
            else:
                self.constraint_axis = new_axis
                self.state["constraint_axis"] = new_axis
            return True
            
        if event.type == 'BACK_SPACE' and event.value == 'PRESS':
            if len(self.points) > 1:
                self.points.pop()
                self.pivot = self.points[-1]
                self.preview_pts = self.points + [self.current] if self.current else self.points
                return True
                
        if event.type in {'RET', 'NUMPAD_ENTER', 'SPACE'} and event.value == 'PRESS':
            if len(self.points) >= 2:
                return True 
            
        return False