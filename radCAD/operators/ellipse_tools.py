import math
from mathutils import Vector
from .base_tool import SurfaceDrawTool
from ..plane_utils import plane_to_world

def ellipse_points_world(center, rx, ry, segments, Xp, Yp):
    """
    Generates points for a full ellipse in 3D space.
    """
    pts = []
    if segments < 3: return pts
    
    # Pre-calculate angle step
    step = (2 * math.pi) / segments
    
    for i in range(segments + 1):
        angle = i * step
        # Ellipse parametric equation
        vx = math.cos(angle) * rx
        vy = math.sin(angle) * ry
        
        # Map to 3D World Space
        pt = center + (Xp * vx) + (Yp * vy)
        pts.append(pt)
        
    return pts

class EllipseTool_FromRadius(SurfaceDrawTool):
    def __init__(self, core):
        super().__init__(core)
        self.mode = "ELLIPSE_RADIUS"
        self.segments = self.state["segments"]
        self.rx = 0.0 
        self.ry = 0.0 
        self.current = None
        self.preview_pts = []

    def update(self, context, event, snap_point, snap_normal):
        # Stage 0: Let the Prep Chef find the wall/orientation
        if self.stage == 0:
            self.update_initial_plane(context, event, snap_point, snap_normal)
            return

        # Stage 1: Dragging Major Axis (Radius X + Rotation)
        if self.stage == 1:
            pv = self.pivot
            d = snap_point - pv
            
            if self.Zp: d_plane = d - self.Zp * d.dot(self.Zp)
            else: d_plane = d
            
            length = d_plane.length
            self.rx = length
            
            # Orientation
            if length > 1e-6:
                self.Xp = d_plane.normalized()
                self.Yp = self.Zp.cross(self.Xp).normalized()
            
            self.current = snap_point
            self.preview_pts = [self.pivot, self.pivot + (self.Xp * self.rx)]

        # Stage 2: Dragging Minor Axis (Radius Y)
        if self.stage == 2:
            pv = self.pivot
            d = snap_point - pv
            dist_y = d.dot(self.Yp)
            self.ry = abs(dist_y)
            
            # Constrain visual cursor to the perpendicular axis
            self.current = pv + (self.Yp * dist_y)
            
            self.segments = self.state["segments"]
            self.preview_pts = ellipse_points_world(
                self.pivot, self.rx, self.ry, self.segments, self.Xp, self.Yp
            )

    def handle_click(self, context, event, snap_point, snap_normal, button_id=None):
        if self.stage == 0:
            self.pivot = snap_point
            self.state["locked"] = True
            self.state["locked_normal"] = self.Zp
            self.stage = 1
            return 'NEXT_STAGE'

        if self.stage == 1:
            if self.rx < 1e-6: return None
            self.stage = 2
            return 'NEXT_STAGE'

        if self.stage == 2:
            return 'FINISHED'
        return None

class EllipseTool_FromEndpoints(SurfaceDrawTool):
    def __init__(self, core):
        super().__init__(core)
        self.mode = "ELLIPSE_ENDPOINTS"
        self.segments = self.state["segments"]
        
        self.p1 = None 
        self.p2 = None
        self.rx = 0.0 # Semi-Major
        self.ry = 0.0 # Semi-Minor
        self.current = None
        self.preview_pts = []

    def update(self, context, event, snap_point, snap_normal):
        # Stage 0: Set P1 (Start of Diameter)
        if self.stage == 0:
            self.update_initial_plane(context, event, snap_point, snap_normal)
            return

        # Stage 1: Drag to P2 (End of Diameter)
        if self.stage == 1:
            self.p1 = self.pivot
            d = snap_point - self.p1
            
            if self.Zp: d_plane = d - self.Zp * d.dot(self.Zp)
            else: d_plane = d
            
            diameter = d_plane.length
            self.rx = diameter * 0.5
            
            if diameter > 1e-6:
                self.Xp = d_plane.normalized()
                self.Yp = self.Zp.cross(self.Xp).normalized()
            
            self.current = self.p1 + d_plane
            self.preview_pts = [self.p1, self.current]

        # Stage 2: Drag Height (Minor Axis)
        if self.stage == 2:
            center = (self.p1 + self.p2) * 0.5
            d_raw = snap_point - center
            d_plane = d_raw - self.Zp * d_raw.dot(self.Zp)
            P = center + d_plane
            
            # Distance from center to P along Yp axis (Minor Radius)
            dist_y = abs(d_plane.dot(self.Yp))
            self.ry = dist_y
            
            # Snap current point to the actual circumference at the mouse's angular position
            if d_plane.length > 1e-6:
                d2 = Vector((d_plane.dot(self.Xp), d_plane.dot(self.Yp)))
                angle = math.atan2(d2.y, d2.x)
                # Ellipse Parametric: x = rx*cos(t), y = ry*sin(t)
                local_snap = Vector((self.rx * math.cos(angle), self.ry * math.sin(angle)))
                self.current = center + (self.Xp * local_snap.x) + (self.Yp * local_snap.y)
            else:
                self.current = P
            
            self.segments = self.state["segments"]
            
            self.preview_pts = ellipse_points_world(
                center, self.rx, self.ry, self.segments, self.Xp, self.Yp
            )

    def handle_click(self, context, event, snap_point, snap_normal, button_id=None):
        if self.stage == 0:
            self.pivot = snap_point
            self.state["locked"] = True
            self.state["locked_normal"] = self.Zp
            self.stage = 1
            return 'NEXT_STAGE'

        if self.stage == 1:
            if self.current: self.p2 = self.current
            else: self.p2 = snap_point
            
            if (self.p2 - self.pivot).length < 1e-6: return None
            self.stage = 2
            return 'NEXT_STAGE'

        if self.stage == 2:
            return 'FINISHED'
            
        return None

class EllipseTool_FromCorners(SurfaceDrawTool):
    def __init__(self, core):
        super().__init__(core)
        self.mode = "ELLIPSE_CORNERS"
        self.segments = self.state["segments"]
        
        self.rx = 0.0
        self.ry = 0.0
        self.current = None
        self.preview_pts = []

    def update(self, context, event, snap_point, snap_normal):
        # Stage 0: Set Corner 1
        if self.stage == 0:
            self.update_initial_plane(context, event, snap_point, snap_normal)
            return

        # Stage 1: Drag to Corner 2
        if self.stage == 1:
            self.current = snap_point
            
            # Project mouse vector onto our established X/Y plane basis
            d_vec = self.current - self.pivot
            
            # Distance along Xp axis
            width = d_vec.dot(self.Xp)
            # Distance along Yp axis
            height = d_vec.dot(self.Yp)
            
            self.rx = abs(width) * 0.5
            self.ry = abs(height) * 0.5
            
            # Center is offset from pivot by half width/height
            center = self.pivot + (self.Xp * (width * 0.5)) + (self.Yp * (height * 0.5))
            
            self.segments = self.state["segments"]
            self.preview_pts = ellipse_points_world(
                center, self.rx, self.ry, self.segments, self.Xp, self.Yp
            )

    def handle_click(self, context, event, snap_point, snap_normal, button_id=None):
        # Stage 0: Set Pivot (Corner 1)
        if self.stage == 0:
            self.pivot = snap_point
            self.state["locked"] = True
            self.state["locked_normal"] = self.Zp
            self.stage = 1
            return 'NEXT_STAGE'

        # Stage 1: Set Corner 2 -> FINISH
        if self.stage == 1:
            return 'FINISHED'
            
        return None