import math
from mathutils import Vector, geometry
from ..inference_utils import get_axis_snapped_location
from .base_tool import SurfaceDrawTool 

def safe_lerp(p_a, p_b, t, t_a, t_b):
    """
    Safely interpolates between p_a and p_b.
    Prevents ZeroDivisionError if t_a and t_b are identical (distance is 0).
    """
    if abs(t_b - t_a) < 1e-6:
        return p_a
    return (t_b - t) / (t_b - t_a) * p_a + (t - t_a) / (t_b - t_a) * p_b

def solve_catmull_rom_chain(points, num_segments=16):
    """
    Generates a smooth path passing through all given points.
    Includes protections against stacked points.
    """
    if len(points) < 2:
        return points
        
    # Duplicate ends to ensure the curve goes through start and end control points
    chain = [points[0]] + points + [points[-1]]
    
    smooth_path = []
    alpha = 0.5 
    
    def get_t(t, p0, p1):
        # Calculate 'time' based on distance
        a = pow((p1 - p0).length_squared, alpha * 0.5)
        return t + a

    for i in range(len(chain) - 3):
        p0 = chain[i]
        p1 = chain[i+1]
        p2 = chain[i+2]
        p3 = chain[i+3]
        
        # [CRITICAL FIX] If points are too close, skip calculation to avoid crash
        if (p1 - p2).length_squared < 1e-6:
            smooth_path.append(p1)
            continue

        t0 = 0.0
        t1 = get_t(t0, p0, p1)
        t2 = get_t(t1, p1, p2)
        t3 = get_t(t2, p2, p3)

        for j in range(num_segments):
            t = t1 + (t2 - t1) * (j / num_segments)
            
            # Use safe_lerp instead of raw math
            A1 = safe_lerp(p0, p1, t, t0, t1)
            A2 = safe_lerp(p1, p2, t, t1, t2)
            A3 = safe_lerp(p2, p3, t, t2, t3)
            
            B1 = safe_lerp(A1, A2, t, t0, t2)
            B2 = safe_lerp(A2, A3, t, t1, t3)
            
            C = safe_lerp(B1, B2, t, t1, t2)
            smooth_path.append(C)
            
    smooth_path.append(points[-1])
    return smooth_path

class CurveTool_Interpolate(SurfaceDrawTool):
    def __init__(self, core):
        super().__init__(core)
        self.mode = "CURVE_INTERPOLATE"
        self.control_points = [] 
        self.current = None
        self.constraint_axis = None

    def update(self, context, event, snap_point, snap_normal):
        # Stage 0: Just finding the start point
        if self.stage == 0:
            self.update_initial_plane(context, event, snap_point, snap_normal)
            self.current = snap_point
            self.preview_pts = []
            return

        # Stage 1+: Drawing Spline
        if self.stage >= 1:
            ref_point = self.control_points[-1] if self.control_points else self.pivot
            target = snap_point
            
            # --- AXIS CONSTRAINT LOGIC ---
            if self.constraint_axis:
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
            
            elif not self.state.get("geometry_snap", False):
                strength_deg = self.state.get("snap_strength", 6.0)
                strength_deg = max(0.1, min(89.0, strength_deg))
                axis_thresh = math.cos(math.radians(strength_deg))
                inf_loc, _, _ = get_axis_snapped_location(
                    ref_point, (event.mouse_region_x, event.mouse_region_y), context, snap_threshold=axis_thresh
                )
                if inf_loc: target = inf_loc

            self.current = target
            
            # Calculate smooth curve using the safe chain function
            all_pts = self.control_points + [self.current]
            self.preview_pts = solve_catmull_rom_chain(all_pts, num_segments=12)

    def handle_click(self, context, event, snap_point, snap_normal, button_id=None):
        if self.stage == 0:
            self.pivot = snap_point
            self.control_points.append(snap_point)
            
            self.state["locked"] = True
            self.state["locked_normal"] = self.Zp
            
            self.stage = 1
            return 'NEXT_STAGE'

        if self.stage >= 1:
            # Don't add point if it's too close to the last one (Double Click protection)
            if (self.current - self.control_points[-1]).length < 1e-5:
                return None
                
            self.control_points.append(self.current)
            self.constraint_axis = None
            self.state["constraint_axis"] = None
            self.pivot = self.current 
            
            return 'NEXT_STAGE'
            
        return None

    def handle_input(self, context, event):
        if super().handle_plane_lock_input(context, event):
            return True

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
            if len(self.control_points) > 1:
                self.control_points.pop()
                self.pivot = self.control_points[-1]
                all_pts = self.control_points + [self.current] if self.current else self.control_points
                self.preview_pts = solve_catmull_rom_chain(all_pts, num_segments=12)
                return True
                
        return False