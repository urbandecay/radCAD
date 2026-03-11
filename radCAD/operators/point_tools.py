import math
from mathutils import Vector
from ..geometry_utils import snap_angle_soft, unwrap, arc_points_world
from ..plane_utils import world_to_plane, plane_to_world
from ..orientation_utils import orthonormal_basis_from_normal
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

def is_angle_in_arc(pt, center, Xp, Yp, a0, a1):
    """Checks if a point lies within the directed angular sweep from a0 to a1, with very high tolerance."""
    d = a1 - a0
    # 1. Full Circle Check
    if abs(d) >= 2 * math.pi - 0.05:
        return True
        
    # 2. Project point to local 2D angle
    v = pt - center
    lx, ly = v.dot(Xp), v.dot(Yp)
    ang = math.atan2(ly, lx)
    
    # 3. Get positive forward distance from a0 to ang
    diff = ang - a0
    diff_norm = diff % (2 * math.pi)
    
    # 4. HIGH TOLERANCE (approx 10 degrees)
    tol = 0.17 
    
    # 5. Check against sweep direction
    if d >= 0:
        # Forward sweep: valid if diff_norm is in [0, d] with tolerance
        return diff_norm <= (d + tol) or diff_norm >= (2 * math.pi - tol)
    else:
        # Backward sweep
        back_dist = (2 * math.pi - diff_norm) % (2 * math.pi)
        return back_dist <= (abs(d) + tol) or back_dist >= (2 * math.pi - tol)

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
        self.endpoints_1 = [] # To store Arc 1 Start/End for snapping
        
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
        self.segments = 128 # Fixed High res for smooth curves
        self.preview_pts = []
        self.intersection_pts = [] 
        self.state["arc1_pts"] = []
        self.state["intersection_pts"] = []
        self.constraint_axis = None

    def handle_input(self, context, event):
        # 1. Check Parent Input first (L Key)
        if self.handle_plane_lock_input(context, event):
            return True

        # 2. Perpendicular Lock (P Key)
        if event.type == 'P' and event.value == 'PRESS':
            if self.stage == 0: return False
            
            bridge = None
            if self.stage in [1, 2] and self.pivot:
                if self.stage == 1 and self.current: bridge = self.current - self.pivot
                elif self.stage == 2 and self.start: bridge = self.start - self.pivot
            elif self.stage in [4, 5] and self.pivot:
                if self.stage == 4 and self.current: bridge = self.current - self.pivot
                elif self.stage == 5 and self.start: bridge = self.start - self.pivot
            
            if bridge and bridge.length_squared > 1e-6:
                self.state["is_perpendicular"] = not self.state.get("is_perpendicular", False)
                
                if self.state["is_perpendicular"]:
                    b_vec = bridge.normalized()
                    floor_n = self.state.get("locked_normal") or Vector((0,0,1))
                    new_Zp = b_vec.cross(floor_n).normalized()
                    new_Yp = floor_n 
                    new_Xp = new_Yp.cross(new_Zp).normalized() 
                    
                    self.Xp, self.Yp, self.Zp = new_Xp, new_Yp, new_Zp
                    self.state["locked"] = True
                    self.state["locked_normal"] = new_Zp
                else:
                    self.state["locked"] = False
                    self.state["locked_normal"] = None
                return True

        # 3. Axis Locking (X/Y/Z) - Locks Plane Normal (like Arc 1-Point)
        if event.type in {'X', 'Y', 'Z'} and event.value == 'PRESS':
            axes = {'X': Vector((1, 0, 0)), 'Y': Vector((0, 1, 0)), 'Z': Vector((0, 0, 1))}
            new_n = axes[event.type]
            
            current_locked = self.state.get("locked")
            current_normal = self.state.get("locked_normal")
            
            is_same_axis = False
            if current_locked and current_normal:
                if abs(current_normal.dot(new_n)) > 0.99:
                    is_same_axis = True

            if is_same_axis:
                self.state["locked"] = False
                self.state["locked_normal"] = None
                self.state["locked_plane_point"] = None
                self.core.report({'INFO'}, f"Unlocked {event.type}-Plane")
            else:
                self.Zp = new_n
                self.Xp, self.Yp, _ = orthonormal_basis_from_normal(self.Zp)
                self.state["locked"] = True
                self.state["locked_normal"] = self.Zp
                
                # --- FIX: Set plane point to where the compass is RIGHT NOW ---
                target_point = self.current if self.current else Vector((0,0,0))
                self.state["locked_plane_point"] = target_point
                
                self.core.report({'INFO'}, f"Locked to {event.type}-Plane")
            return True

        return False

    def update(self, context, event, snap_point, snap_normal):
        self.current = snap_point
        
        # --- PROJECT ONTO LOCKED PLANE IF APPLICABLE ---
        if self.state.get("locked") and self.Zp and self.pivot:
            d = snap_point - self.pivot
            d_plane = d - self.Zp * d.dot(self.Zp)
            self.current = self.pivot + d_plane
        
        # --- STAGE 0: PIVOT 1 (First Arc) ---
        if self.stage == 0:
            self.update_initial_plane(context, event, snap_point, snap_normal)
            return

        # --- STAGE 1: RADIUS 1 ---
        if self.stage == 1:
            pv = self.pivot
            d_vec = self.current - pv
            d2 = world_to_plane(d_vec, self.Xp, self.Yp)
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
            d_vec = self.current - pv
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
            # self.current is already projected onto locked plane at top of update
            return

        # --- STAGE 4: RADIUS 2 ---
        if self.stage == 4:
            pv = self.pivot # Now pivot is C2
            d_vec = self.current - pv
            d2 = world_to_plane(d_vec, self.Xp, self.Yp)
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
            
            # --- CALCULATE INTERSECTION FOR PREVIEW (FULL CIRCLE FOR ARC 2) ---
            self.intersection_pts = []
            if self.c1 is not None and self.r1 > 1e-6 and self.radius > 1e-6:
                candidates = intersect_circles_3d(self.c1, self.r1, pv, self.radius, self.Xp, self.Yp)
                valid_ints = []
                for pt in candidates:
                    # Check if point is on Arc 1
                    if is_angle_in_arc(pt, self.c1, self.Xp, self.Yp, self.a0_1, self.a1_1):
                        valid_ints.append(pt)
                self.intersection_pts = valid_ints
                self.state["intersection_pts"] = valid_ints
                
            return

        # --- STAGE 5: ANGLE 2 & SOLVE ---
        if self.stage == 5:
            pv = self.pivot # C2
            d_vec = self.current - pv
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
            self.intersection_pts = []
            if self.c1 is not None and self.r1 > 1e-6:
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
                
                self.intersection_pts = valid_ints
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
            target = snap_point
            if self.Xp is not None and self.Zp is not None:
                d = target - self.pivot
                d_plane = d - self.Zp * d.dot(self.Zp)
                target = self.pivot + d_plane
            self.start = target
            
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
            
            # Store endpoints for snapping
            if self.preview_pts and len(self.preview_pts) >= 2:
                self.endpoints_1 = [self.preview_pts[0], self.preview_pts[-1]]
            
            self.preview_pts = [] # Clear for next
            
            self.stage = 3 # Ready for Pivot 2
            return 'NEXT_STAGE'

        # Stage 3: Commit Pivot 2
        if self.stage == 3:
            target = snap_point
            if self.state.get("locked") and self.Zp and self.c1:
                d = target - self.c1
                d_plane = d - self.Zp * d.dot(self.Zp)
                target = self.c1 + d_plane
            
            self.pivot = target
            # Keep plane locked from Arc 1
            self.stage = 4
            return 'NEXT_STAGE'

        # Stage 4: Commit Radius 2 / Start 2
        if self.stage == 4:
            target = snap_point
            if self.Xp is not None and self.Zp is not None:
                d = target - self.pivot
                d_plane = d - self.Zp * d.dot(self.Zp)
                target = self.pivot + d_plane
            self.start = target
            
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