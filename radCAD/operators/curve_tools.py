import math
from mathutils import Vector, geometry
from ..inference_utils import get_axis_snapped_location
from .base_tool import SurfaceDrawTool 

def safe_lerp(p_a, p_b, t, t_a, t_b):
    """Safely interpolates between p_a and p_b."""
    if abs(t_b - t_a) < 1e-6:
        return p_a
    return (t_b - t) / (t_b - t_a) * p_a + (t - t_a) / (t_b - t_a) * p_b

def evaluate_catmull_rom(p0, p1, p2, p3, t_normalized):
    """Directly evaluates a Catmull-Rom point for a single segment p1-p2."""
    alpha = 0.5
    def get_t(t, pa, pb): return t + pow((pb - pa).length_squared, alpha * 0.5)
    
    t0 = 0.0
    t1 = get_t(t0, p0, p1)
    t2 = get_t(t1, p1, p2)
    t3 = get_t(t2, p2, p3)
    
    t = t1 + (t2 - t1) * t_normalized
    A1 = safe_lerp(p0, p1, t, t0, t1)
    A2 = safe_lerp(p1, p2, t, t1, t2)
    A3 = safe_lerp(p2, p3, t, t2, t3)
    B1 = safe_lerp(A1, A2, t, t0, t2)
    B2 = safe_lerp(A2, A3, t, t1, t3)
    return safe_lerp(B1, B2, t, t1, t2)

def solve_catmull_rom_chain(points, num_segments=16, cached_dists=None):
    """
    Highly efficient uniform curve solver.
    Directly evaluates requested segments using chord-length distances.
    """
    if len(points) < 2: return points
    if len(points) == 2:
        return [points[0].lerp(points[1], i/num_segments) for i in range(num_segments + 1)]

    # 1. Calculate cumulative straight-line distances between control points
    # Use cached version if available (O(1) vs O(N))
    if cached_dists and len(cached_dists) == len(points):
        dists = cached_dists
        total_len = dists[-1]
    else:
        dists = [0.0]
        total_len = 0.0
        for i in range(len(points) - 1):
            total_len += (points[i+1] - points[i]).length
            dists.append(total_len)
    
    if total_len < 1e-6: return [points[0], points[-1]]

    # 2. Add boundary points for Catmull-Rom
    chain = [points[0]] + points + [points[-1]]
    
    # 3. Sample exactly 'num_segments' points along the curve
    final_pts = []
    # Optimization: pre-calculate step
    step = total_len / num_segments
    
    idx = 0
    for i in range(num_segments):
        target_d = step * i
        
        # Find which segment this distance falls into
        # Optimization: start search from previous index (O(1) average case)
        while idx < len(dists) - 2 and dists[idx+1] < target_d:
            idx += 1
            
        # Local 't' within the segment [idx, idx+1]
        d0, d1 = dists[idx], dists[idx+1]
        local_t = (target_d - d0) / (d1 - d0) if (d1 - d0) > 1e-8 else 0.0
        
        # Evaluate spline using P[idx-1], P[idx], P[idx+1], P[idx+2]
        # (Offset by 1 because of the boundary points added to 'chain')
        p0, p1, p2, p3 = chain[idx:idx+4]
        final_pts.append(evaluate_catmull_rom(p0, p1, p2, p3, local_t))
        
    final_pts.append(points[-1])
    return final_pts

class CurveTool_Interpolate(SurfaceDrawTool):
    def __init__(self, core):
        super().__init__(core)
        self.mode = "CURVE_INTERPOLATE"
        self.control_points = [] 
        self.current = None
        self.constraint_axis = None
        self.dists = [0.0] # Cached for speed

    def update(self, context, event, snap_point, snap_normal):
        if self.stage == 0:
            self.update_initial_plane(context, event, snap_point, snap_normal)
            self.current = snap_point
            self.preview_pts = []
            return

        if self.stage >= 1:
            ref_point = self.control_points[-1] if self.control_points else self.pivot
            target = snap_point
            
            if self.constraint_axis:
                if self.state.get("geometry_snap", False):
                    diff = target - ref_point
                    proj = self.constraint_axis * diff.dot(self.constraint_axis)
                    target = ref_point + proj
                else:
                    from bpy_extras import view3d_utils
                    ray_origin = view3d_utils.region_2d_to_origin_3d(context.region, context.region_data, (event.mouse_region_x, event.mouse_region_y))
                    ray_vector = view3d_utils.region_2d_to_vector_3d(context.region, context.region_data, (event.mouse_region_x, event.mouse_region_y))
                    res = geometry.intersect_line_line(ray_origin, ray_origin + ray_vector, ref_point, ref_point + self.constraint_axis)
                    if res: target = res[1]
            elif not self.state.get("geometry_snap", False):
                axis_thresh = math.cos(math.radians(self.state.get("snap_strength", 6.0)))
                inf_loc, _, _ = get_axis_snapped_location(ref_point, (event.mouse_region_x, event.mouse_region_y), context, snap_threshold=axis_thresh)
                if inf_loc: target = inf_loc

            self.current = target
            num_segs = self.state.get("segments", 12)
            # Temporary distances for the point under mouse
            temp_pts = self.control_points + [self.current]
            temp_len = self.dists[-1] + (self.current - self.control_points[-1]).length
            temp_dists = self.dists + [temp_len]
            self.preview_pts = solve_catmull_rom_chain(temp_pts, num_segments=num_segs, cached_dists=temp_dists)

    def refresh_preview(self):
        if self.stage >= 1 and self.current:
            num_segs = self.state.get("segments", 12)
            temp_pts = self.control_points + [self.current]
            temp_len = self.dists[-1] + (self.current - self.control_points[-1]).length
            temp_dists = self.dists + [temp_len]
            self.preview_pts = solve_catmull_rom_chain(temp_pts, num_segments=num_segs, cached_dists=temp_dists)

    def handle_click(self, context, event, snap_point, snap_normal, button_id=None):
        if self.stage == 0:
            self.pivot = snap_point
            self.control_points.append(snap_point)
            self.dists = [0.0]
            self.state["locked"], self.state["locked_normal"] = True, self.Zp
            self.stage = 1
            return 'NEXT_STAGE'
        if self.stage >= 1:
            if (self.current - self.control_points[-1]).length < 1e-5: return None
            # Update distance cache
            seg_len = (self.current - self.control_points[-1]).length
            self.dists.append(self.dists[-1] + seg_len)
            self.control_points.append(self.current)
            self.constraint_axis = self.state["constraint_axis"] = None
            self.pivot = self.current 
            return 'NEXT_STAGE'
        return None

    def handle_input(self, context, event):
        if super().handle_plane_lock_input(context, event): return True
        if event.type in {'X', 'Y', 'Z'} and event.value == 'PRESS':
            axes = {'X': Vector((1, 0, 0)), 'Y': Vector((0, 1, 0)), 'Z': Vector((0, 0, 1))}
            self.constraint_axis = self.state["constraint_axis"] = None if self.constraint_axis == axes[event.type] else axes[event.type]
            return True
        if event.type == 'BACK_SPACE' and event.value == 'PRESS' and len(self.control_points) > 1:
            self.control_points.pop()
            self.dists.pop()
            self.pivot = self.control_points[-1]
            self.refresh_preview()
            return True
        return False

class CurveTool_Freehand(SurfaceDrawTool):
    def __init__(self, core):
        super().__init__(core)
        self.mode = "CURVE_FREEHAND"
        self.points = []
        self.current = None
        self.last_collect_pos = None
        self.is_drawing = False
        self.min_dist = 0.05 # Optimal for direct solving
        self.dists = [0.0]

    def update(self, context, event, snap_point, snap_normal):
        if self.stage == 0:
            self.update_initial_plane(context, event, snap_point, snap_normal)
            self.current = snap_point
            self.preview_pts = []
            return

        if self.stage == 1 and self.is_drawing:
            target = snap_point
            if self.Zp and self.pivot:
                from bpy_extras import view3d_utils
                coord = (event.mouse_region_x, event.mouse_region_y)
                ray_origin = view3d_utils.region_2d_to_origin_3d(context.region, context.region_data, coord)
                ray_vector = view3d_utils.region_2d_to_vector_3d(context.region, context.region_data, coord)
                hit = geometry.intersect_line_plane(ray_origin, ray_origin + ray_vector, self.pivot, self.Zp)
                if hit: target = hit
            
            self.current = target
            
            if self.last_collect_pos is None or (target - self.last_collect_pos).length > self.min_dist:
                # Update distance cache
                if self.points:
                    seg_len = (target - self.points[-1]).length
                    self.dists.append(self.dists[-1] + seg_len)
                
                self.points.append(target.copy())
                self.last_collect_pos = target.copy()
            
            # DIRECT SOLVE: Perfectly smooth, perfectly fast
            num_segs = self.state.get("segments", 12)
            self.preview_pts = solve_catmull_rom_chain(self.points, num_segments=num_segs, cached_dists=self.dists)
            self.state["preview_pts"] = self.preview_pts

        if self.stage == 2:
            self.current = snap_point

    def refresh_preview(self):
        if len(self.points) > 1:
            num_segs = self.state.get("segments", 12)
            self.preview_pts = solve_catmull_rom_chain(self.points, num_segments=num_segs, cached_dists=self.dists)
            self.state["preview_pts"] = self.preview_pts

    def handle_click(self, context, event, snap_point, snap_normal, button_id=None):
        if event.value != 'PRESS': return None
        
        # Safety: If snap_point is None (skipped for performance), use the calculated current position
        target = snap_point if snap_point is not None else self.current
        if target is None: return None

        if self.stage == 0:
            self.pivot = target
            self.points = [target.copy()]
            self.dists = [0.0]
            self.last_collect_pos = target.copy()
            self.is_drawing = True
            self.state["locked"], self.state["locked_normal"] = True, self.Zp
            self.stage = 1
            return 'NEXT_STAGE'
        if self.stage == 1:
            self.is_drawing = False
            if (target - self.points[-1]).length > 1e-5:
                seg_len = (target - self.points[-1]).length
                self.dists.append(self.dists[-1] + seg_len)
                self.points.append(target.copy())
            self.refresh_preview()
            self.stage = 2
            return 'NEXT_STAGE'
        if self.stage == 2: return 'FINISHED'
        return None

    def handle_input(self, context, event):
        return super().handle_plane_lock_input(context, event)
