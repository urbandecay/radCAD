import math
from mathutils import Vector
from ..geometry_utils import snap_angle_soft, unwrap, arc_points_world
from ..plane_utils import world_to_plane, plane_to_world
from .base_tool import SurfaceDrawTool 

def intersect_circles_3d(c1, r1, c2, r2, Xp, Yp):
    """
    Finds intersection points of two circles on the same plane (defined by Xp, Yp).
    Returns a list of 3D Vectors.
    """
    # Project c2 onto c1's local plane basis
    d_vec = c2 - c1
    dx = d_vec.dot(Xp)
    dy = d_vec.dot(Yp)
    d = math.sqrt(dx*dx + dy*dy)
    
    # Check for no intersection or containment
    if d > r1 + r2 or d < abs(r1 - r2) or d == 0:
        return []
    
    a = (r1**2 - r2**2 + d**2) / (2*d)
    h_sq = r1**2 - a**2
    h = math.sqrt(max(0, h_sq))
    
    # P2 is the point on the line connecting centers
    p2_x = dx * a / d
    p2_y = dy * a / d
    
    # Offsets for intersection points
    ox = -dy * h / d
    oy = dx * h / d
    
    pts = []
    
    # Intersection 1
    i1_local_x = p2_x + ox
    i1_local_y = p2_y + oy
    pts.append(c1 + Xp * i1_local_x + Yp * i1_local_y)
    
    # Intersection 2 (if distinct)
    if h > 1e-6:
        i2_local_x = p2_x - ox
        i2_local_y = p2_y - oy
        pts.append(c1 + Xp * i2_local_x + Yp * i2_local_y)
        
    return pts

def is_angle_in_arc(pt, center, Xp, Yp, start_ang, end_ang):
    """Checks if a point lies within the angular sweep of an arc."""
    # Convert point to local 2D relative to center
    v = pt - center
    lx = v.dot(Xp)
    ly = v.dot(Yp)
    ang = math.atan2(ly, lx)
    
    # Normalize angles to be continuous or check range
    # Easiest way: Normalize everything to [0, 2pi) relative to start_ang?
    # Or just use the unwrapped logic.
    
    # Let's normalize ang to be close to start_ang
    # We assume start_ang < end_ang or vice versa based on direction
    # Arc tools usually store a0 and a1 where a1 can be > 2pi or < -2pi
    
    if start_ang > end_ang:
        start_ang, end_ang = end_ang, start_ang
        
    # Unwrap 'ang' to be near start_ang
    diff = ang - start_ang
    # Wrap to [-pi, pi]
    while diff <= -math.pi: diff += 2*math.pi
    while diff > math.pi: diff -= 2*math.pi
    
    # If the unwrap placed it "before" start, maybe it belongs "after" if it's a full circle?
    # But for simple arc checks:
    
    # Robust check:
    # Check if ang is between [a0, a1] modulo 2pi is tricky.
    # Alternative: Cross product check if sweep < 180?
    
    # Let's try simple range check by normalizing all to [0, 2pi)
    def n(a): return a % (2 * math.pi)
    
    na = n(ang)
    n0 = n(start_ang)
    n1 = n(end_ang)
    
    # Handle wrap-around case
    if n0 < n1:
        return n0 <= na <= n1
    else:
        return na >= n0 or na <= n1

class PointTool_ByArcs(SurfaceDrawTool):
    def __init__(self, core):
        super().__init__(core)
        self.mode = "POINT_BY_ARCS"
        
        # Arc 1 Data
        self.arc1_done = False
        self.c1 = None
        self.r1 = 0.0
        self.a0_1 = 0.0
        self.a1_1 = 0.0
        
        # Current Arc (Shared)
        self.start = None
        self.current = None
        self.radius = 0.0
        self.a0 = 0.0
        self.a1 = 0.0
        self.a_prev_raw = 0.0
        self.accum_angle = 0.0
        self.compass_rot = 0.0
        
        # Display
        self.segments = 64 # High res for smooth curves
        self.preview_pts = []
        self.state["arc1_pts"] = []
        self.state["intersection_pts"] = []

    def update(self, context, event, snap_point, snap_normal):
        self.current = snap_point
        
        # --- STAGE 0: PIVOT 1 (First Arc) ---
        if self.stage == 0:
            self.update_initial_plane(context, event, snap_point, snap_normal)
            return

        # --- STAGE 1: RADIUS 1 ---
        if self.stage == 1:
            pv = self.pivot
            d = snap_point - pv
            d_plane = d - self.Zp * d.dot(self.Zp)
            d2 = world_to_plane(d_plane, self.Xp, self.Yp)
            self.radius = d2.length
            
            raw_angle = math.atan2(d2.y, d2.x)
            
            if self.state.get("geometry_snap", False): final_angle = raw_angle
            else:
                strength = self.state.get("snap_strength", 6.0)
                if self.state.get("use_angle_snap", True):
                    final_angle = snap_angle_soft(raw_angle, self.state.get("angle_increment", 15.0), strength)
                else: final_angle = raw_angle
            
            self.compass_rot = final_angle
            snapped_vec2 = Vector((math.cos(final_angle), math.sin(final_angle))) * self.radius
            self.current = pv + plane_to_world(snapped_vec2, self.Xp, self.Yp)
            return

        # --- STAGE 2: ANGLE 1 ---
        if self.stage == 2:
            pv = self.pivot
            d_vec = snap_point - pv
            d2 = world_to_plane(d_vec, self.Xp, self.Yp)
            raw_angle = math.atan2(d2.y, d2.x)

            strength = self.state.get("snap_strength", 6.0)
            if self.state.get("use_angle_snap", True) and not self.state.get("geometry_snap", False):
                raw_angle = snap_angle_soft(raw_angle, self.state.get("angle_increment", 15.0), strength)

            accum, a_prev = unwrap(self.a_prev_raw, raw_angle, self.accum_angle)
            self.a_prev_raw = a_prev
            self.accum_angle = accum
            self.a1 = self.a0 + accum

            self.preview_pts = arc_points_world(
                pv, self.radius, self.a0, self.a1, self.segments, self.Xp, self.Yp
            )
            return

        # --- STAGE 3: PIVOT 2 (Wait for click) ---
        if self.stage == 3:
            # Just showing cursor
            return

        # --- STAGE 4: RADIUS 2 ---
        if self.stage == 4:
            pv = self.pivot # Now pivot is C2
            d = snap_point - pv
            d_plane = d - self.Zp * d.dot(self.Zp)
            d2 = world_to_plane(d_plane, self.Xp, self.Yp)
            self.radius = d2.length
            
            raw_angle = math.atan2(d2.y, d2.x)
            if self.state.get("geometry_snap", False): final_angle = raw_angle
            else:
                strength = self.state.get("snap_strength", 6.0)
                if self.state.get("use_angle_snap", True):
                    final_angle = snap_angle_soft(raw_angle, self.state.get("angle_increment", 15.0), strength)
                else: final_angle = raw_angle
                
            self.compass_rot = final_angle
            snapped_vec2 = Vector((math.cos(final_angle), math.sin(final_angle))) * self.radius
            self.current = pv + plane_to_world(snapped_vec2, self.Xp, self.Yp)
            return

        # --- STAGE 5: ANGLE 2 & SOLVE ---
        if self.stage == 5:
            pv = self.pivot # C2
            d_vec = snap_point - pv
            d2 = world_to_plane(d_vec, self.Xp, self.Yp)
            raw_angle = math.atan2(d2.y, d2.x)

            strength = self.state.get("snap_strength", 6.0)
            if self.state.get("use_angle_snap", True) and not self.state.get("geometry_snap", False):
                raw_angle = snap_angle_soft(raw_angle, self.state.get("angle_increment", 15.0), strength)

            accum, a_prev = unwrap(self.a_prev_raw, raw_angle, self.accum_angle)
            self.a_prev_raw = a_prev
            self.accum_angle = accum
            self.a1 = self.a0 + accum

            # Update Arc 2 Preview
            self.preview_pts = arc_points_world(
                pv, self.radius, self.a0, self.a1, self.segments, self.Xp, self.Yp
            )
            
            # --- CALCULATE INTERSECTION ---
            if self.c1 and self.r1:
                # Find circle-circle intersections
                candidates = intersect_circles_3d(self.c1, self.r1, pv, self.radius, self.Xp, self.Yp)
                
                valid_ints = []
                for pt in candidates:
                    # Check if point is on Arc 1
                    in_arc1 = is_angle_in_arc(pt, self.c1, self.Xp, self.Yp, self.a0_1, self.a1_1)
                    # Check if point is on Arc 2
                    in_arc2 = is_angle_in_arc(pt, pv, self.Xp, self.Yp, self.a0, self.a1)
                    
                    if in_arc1 and in_arc2:
                        valid_ints.append(pt)
                
                self.state["intersection_pts"] = valid_ints

    def handle_click(self, context, event, snap_point, snap_normal, button_id=None):
        # Stage 0: Commit Pivot 1
        if self.stage == 0:
            self.pivot = snap_point
            self.state["locked"] = True
            self.state["locked_normal"] = self.Zp
            self.stage = 1
            return 'NEXT_STAGE'

        # Stage 1: Commit Radius 1 / Start 1
        if self.stage == 1:
            src = snap_point
            if self.Xp is not None and self.Zp is not None:
                d = src - self.pivot
                d_plane = d - self.Zp * d.dot(self.Zp)
                self.start = self.pivot + d_plane
            else: self.start = src
            
            # Init Angle
            rvec2 = world_to_plane(self.start - self.pivot, self.Xp, self.Yp)
            self.radius = rvec2.length
            if rvec2.length > 1e-9: rvec2.normalize()
            a0 = math.atan2(rvec2.y, rvec2.x)
            
            # Snap start angle
            if self.state.get("use_angle_snap", True) and not self.state.get("geometry_snap", False):
                strength = self.state.get("snap_strength", 6.0)
                a0 = snap_angle_soft(a0, 15.0, strength)
                snapped_vec2 = Vector((math.cos(a0), math.sin(a0))) * self.radius
                self.start = self.pivot + plane_to_world(snapped_vec2, self.Xp, self.Yp)

            self.stage = 2
            self.a0 = a0; self.a1 = a0; self.a_prev_raw = a0; self.accum_angle = 0.0
            return 'NEXT_STAGE'

        # Stage 2: Finish Arc 1
        if self.stage == 2:
            # Save Arc 1 Data
            self.c1 = self.pivot
            self.r1 = self.radius
            self.a0_1 = self.a0
            self.a1_1 = self.a1
            self.state["arc1_pts"] = self.preview_pts # Persist for display
            self.preview_pts = [] # Clear for next
            
            self.stage = 3 # Ready for Pivot 2
            return 'NEXT_STAGE'

        # Stage 3: Commit Pivot 2
        if self.stage == 3:
            self.pivot = snap_point
            # Keep plane locked from Arc 1
            self.stage = 4
            return 'NEXT_STAGE'

        # Stage 4: Commit Radius 2 / Start 2
        if self.stage == 4:
            src = snap_point
            d = src - self.pivot
            d_plane = d - self.Zp * d.dot(self.Zp)
            self.start = self.pivot + d_plane
            
            rvec2 = world_to_plane(self.start - self.pivot, self.Xp, self.Yp)
            self.radius = rvec2.length
            a0 = math.atan2(rvec2.y, rvec2.x)
            
            if self.state.get("use_angle_snap", True) and not self.state.get("geometry_snap", False):
                strength = self.state.get("snap_strength", 6.0)
                a0 = snap_angle_soft(a0, 15.0, strength)
                snapped_vec2 = Vector((math.cos(a0), math.sin(a0))) * self.radius
                self.start = self.pivot + plane_to_world(snapped_vec2, self.Xp, self.Yp)

            self.stage = 5
            self.a0 = a0; self.a1 = a0; self.a_prev_raw = a0; self.accum_angle = 0.0
            return 'NEXT_STAGE'

        # Stage 5: Finish
        if self.stage == 5:
            return 'FINISHED'
            
        return None