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
    Generates a smooth path passing through all points with perfectly uniform spacing.
    'num_segments' is now the TOTAL number of segments for the whole curve.
    """
    if len(points) < 2:
        return points
        
    # 1. Generate a high-resolution raw spline first
    chain = [points[0]] + points + [points[-1]]
    raw_path = []
    alpha = 0.5 
    sub_samples = 10 # High-res samples per control segment
    
    def get_t(t, p0, p1):
        dist_sq = (p1 - p0).length_squared
        return t + pow(dist_sq, alpha * 0.5)

    for i in range(len(chain) - 3):
        p0, p1, p2, p3 = chain[i:i+4]
        if (p1 - p2).length_squared < 1e-8:
            raw_path.append(p1)
            continue

        t0, t1 = 0.0, 0.0
        t1 = get_t(t0, p0, p1)
        t2 = get_t(t1, p1, p2)
        t3 = get_t(t2, p2, p3)

        for j in range(sub_samples):
            t = t1 + (t2 - t1) * (j / sub_samples)
            A1 = safe_lerp(p0, p1, t, t0, t1)
            A2 = safe_lerp(p1, p2, t, t1, t2)
            A3 = safe_lerp(p2, p3, t, t2, t3)
            B1 = safe_lerp(A1, A2, t, t0, t2)
            B2 = safe_lerp(A2, A3, t, t1, t3)
            raw_path.append(safe_lerp(B1, B2, t, t1, t2))
            
    raw_path.append(points[-1])

    # 2. Resample the high-res path to be perfectly uniform
    if len(raw_path) < 2: return points
    
    # Calculate cumulative distances along the high-res path
    dists = [0.0]
    total_len = 0.0
    for i in range(len(raw_path) - 1):
        total_len += (raw_path[i+1] - raw_path[i]).length
        dists.append(total_len)
        
    if total_len < 1e-6: return [points[0], points[-1]]

    # Sample exactly 'num_segments' evenly
    uniform_path = []
    for i in range(num_segments):
        target_d = total_len * (i / num_segments)
        
        # Find segment in high-res path
        idx = 0
        while idx < len(dists) - 2 and dists[idx+1] < target_d:
            idx += 1
            
        # Lerp between the two high-res points
        d0, d1 = dists[idx], dists[idx+1]
        factor = (target_d - d0) / (d1 - d0) if (d1 - d0) > 1e-8 else 0.0
        uniform_path.append(raw_path[idx].lerp(raw_path[idx+1], factor))
        
    uniform_path.append(points[-1])
    return uniform_path

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
            
            # Use dynamic segment count from global state
            num_segs = self.state.get("segments", 12)
            
            # Calculate smooth curve using the safe chain function
            all_pts = self.control_points + [self.current]
            self.preview_pts = solve_catmull_rom_chain(all_pts, num_segments=num_segs)

    def refresh_preview(self):
        if self.stage >= 1 and self.current:
            num_segs = self.state.get("segments", 12)
            all_pts = self.control_points + [self.current]
            self.preview_pts = solve_catmull_rom_chain(all_pts, num_segments=num_segs)

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
                num_segs = self.state.get("segments", 12)
                self.preview_pts = solve_catmull_rom_chain(all_pts, num_segments=num_segs)
                return True
                
        return False