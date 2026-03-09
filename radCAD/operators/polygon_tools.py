import math
from mathutils import Vector
from .base_tool import SurfaceDrawTool
from ..plane_utils import world_to_plane, plane_to_world

def polygon_points_world(center, radius, start_angle, sides, Xp, Yp):
    """
    Generates points for a regular N-gon.
    """
    pts = []
    if sides < 3: return pts
    
    step = (2 * math.pi) / sides
    
    # We go to 'sides + 1' to close the loop (first point = last point)
    for i in range(sides + 1):
        angle = start_angle + (i * step)
        
        vx = math.cos(angle) * radius
        vy = math.sin(angle) * radius
        
        # Map 2D plane coordinates to 3D World Space
        pt = center + (Xp * vx) + (Yp * vy)
        pts.append(pt)
        
    return pts

class PolygonTool_CenterCorner(SurfaceDrawTool):
    def __init__(self, core):
        super().__init__(core)
        self.mode = "POLYGON_CENTER_CORNER"
        self.segments = max(3, self.state["segments"])
        self.radius = 0.0
        self.current = None
        self.preview_pts = []

    def update(self, context, event, snap_point, snap_normal):
        # Stage 0: Let the Prep Chef find the wall/orientation
        if self.stage == 0:
            self.update_initial_plane(context, event, snap_point, snap_normal)
            return

        # Stage 1: Dragging to Corner
        if self.stage == 1:
            pv = self.pivot
            self.current = snap_point
            
            # Calculate Vector on Plane
            d_vec = snap_point - pv
            if self.Zp:
                d_plane = d_vec - self.Zp * d_vec.dot(self.Zp)
            else:
                d_plane = d_vec
                
            # Radius is distance to corner
            self.radius = d_plane.length
            
            # Calculate Angle to align vertex with mouse cursor
            d2 = world_to_plane(d_plane, self.Xp, self.Yp)
            rot_angle = math.atan2(d2.y, d2.x)
            
            self.segments = max(3, self.state["segments"])
            
            self.preview_pts = polygon_points_world(
                self.pivot,
                self.radius,
                rot_angle,
                self.segments,
                self.Xp,
                self.Yp
            )

    def handle_click(self, context, event, snap_point, snap_normal, button_id=None):
        if self.stage == 0:
            self.pivot = snap_point
            self.state["locked"] = True
            self.state["locked_normal"] = self.Zp
            self.stage = 1
            return 'NEXT_STAGE'

        if self.stage == 1:
            if self.radius < 1e-6: return None
            return 'FINISHED'
        return None

class PolygonTool_CenterTangent(SurfaceDrawTool):
    def __init__(self, core):
        super().__init__(core)
        self.mode = "POLYGON_CENTER_TANGENT"
        self.segments = max(3, self.state["segments"])
        self.radius = 0.0
        self.current = None
        self.preview_pts = []

    def update(self, context, event, snap_point, snap_normal):
        if self.stage == 0:
            self.update_initial_plane(context, event, snap_point, snap_normal)
            return

        if self.stage == 1:
            pv = self.pivot
            self.current = snap_point
            
            d_vec = snap_point - pv
            if self.Zp: d_plane = d_vec - self.Zp * d_vec.dot(self.Zp)
            else: d_plane = d_vec
            
            apothem = d_plane.length
            self.radius = apothem 
            
            d2 = world_to_plane(d_plane, self.Xp, self.Yp)
            mouse_angle = math.atan2(d2.y, d2.x)
            
            self.segments = max(3, self.state["segments"])
            
            half_seg_angle = math.pi / self.segments
            circumradius = apothem / math.cos(half_seg_angle)
            start_angle = mouse_angle - half_seg_angle
            
            self.preview_pts = polygon_points_world(
                self.pivot,
                circumradius,
                start_angle,
                self.segments,
                self.Xp,
                self.Yp
            )

    def handle_click(self, context, event, snap_point, snap_normal, button_id=None):
        if self.stage == 0:
            self.pivot = snap_point
            self.state["locked"] = True
            self.state["locked_normal"] = self.Zp
            self.stage = 1
            return 'NEXT_STAGE'

        if self.stage == 1:
            if self.radius < 1e-6: return None
            return 'FINISHED'
        return None

class PolygonTool_CornerCorner(SurfaceDrawTool):
    def __init__(self, core):
        super().__init__(core)
        self.mode = "POLYGON_CORNER_CORNER"
        self.segments = max(3, self.state["segments"])
        self.current = None
        self.preview_pts = []

    def update(self, context, event, snap_point, snap_normal):
        # Stage 0: Set First Corner
        if self.stage == 0:
            self.update_initial_plane(context, event, snap_point, snap_normal)
            return

        # Stage 1: Dragging to Second Corner (Edge Definition)
        if self.stage == 1:
            corner1 = self.pivot
            self.current = snap_point
            
            # 1. Calculate Edge Vector on Plane
            d_vec = snap_point - corner1
            if self.Zp:
                d_plane = d_vec - self.Zp * d_vec.dot(self.Zp)
            else:
                d_plane = d_vec
            
            side_length = d_plane.length
            
            # Avoid division by zero if points are too close
            if side_length < 1e-6:
                self.preview_pts = []
                return

            self.segments = max(3, self.state["segments"])
            
            # 2. Calculate Circumradius (R) from Side Length (s)
            # s = 2 * R * sin(pi/n)  =>  R = s / (2 * sin(pi/n))
            angle_step = math.pi / self.segments
            circumradius = side_length / (2.0 * math.sin(angle_step))
            
            # 3. Calculate Apothem (distance from edge center to polygon center)
            # a = s / (2 * tan(pi/n))
            apothem = side_length / (2.0 * math.tan(angle_step))
            
            # 4. Find the Center of the Polygon
            # We need the midpoint of the edge, then move perpendicular by the Apothem
            edge_midpoint = corner1 + (d_plane * 0.5)
            
            # Perpendicular vector on the plane (Cross product with Normal)
            # Assuming Zp is Up, Xp/Yp is the plane.
            # We use d_plane x Zp to get the perpendicular direction
            edge_dir = d_plane.normalized()
            perp_dir = edge_dir.cross(self.Zp).normalized()
            
            # NOTE: This builds the polygon to the "Left" of the direction you draw.
            center = edge_midpoint + (perp_dir * apothem)
            
            # 5. Calculate Start Angle
            # We want Corner 1 to be a vertex.
            to_c1 = corner1 - center
            to_c1_2d = world_to_plane(to_c1, self.Xp, self.Yp)
            start_angle = math.atan2(to_c1_2d.y, to_c1_2d.x)
            
            self.preview_pts = polygon_points_world(
                center,
                circumradius,
                start_angle,
                self.segments,
                self.Xp,
                self.Yp
            )

    def handle_click(self, context, event, snap_point, snap_normal, button_id=None):
        if self.stage == 0:
            self.pivot = snap_point
            self.state["locked"] = True
            self.state["locked_normal"] = self.Zp
            self.stage = 1
            return 'NEXT_STAGE'

        if self.stage == 1:
            return 'FINISHED'
            
        return None

class PolygonTool_Edge(SurfaceDrawTool):
    """
    Polygon Side Size tool.
    Defined by Start of Edge -> End of Edge.
    Calculates polygon center based on edge length and side count.
    """
    def __init__(self, core):
        super().__init__(core)
        self.mode = "POLYGON_EDGE"
        self.segments = max(3, self.state["segments"])
        self.current = None
        self.preview_pts = []

    def update(self, context, event, snap_point, snap_normal):
        # Stage 0: Set First Point of Edge
        if self.stage == 0:
            self.update_initial_plane(context, event, snap_point, snap_normal)
            return

        # Stage 1: Dragging to Second Point of Edge
        if self.stage == 1:
            p1 = self.pivot
            self.current = snap_point
            
            # 1. Calculate Edge Vector on Plane
            d_vec = snap_point - p1
            if self.Zp:
                d_plane = d_vec - self.Zp * d_vec.dot(self.Zp)
            else:
                d_plane = d_vec
            
            side_length = d_plane.length
            
            if side_length < 1e-6:
                self.preview_pts = []
                return

            self.segments = max(3, self.state["segments"])
            
            # 2. Geometry Math
            # R = s / (2 * sin(pi/n))
            angle_step = math.pi / self.segments
            circumradius = side_length / (2.0 * math.sin(angle_step))
            
            # Apothem = s / (2 * tan(pi/n))
            apothem = side_length / (2.0 * math.tan(angle_step))
            
            # 3. Find Center
            edge_midpoint = p1 + (d_plane * 0.5)
            
            # Perpendicular direction
            edge_dir = d_plane.normalized()
            perp_dir = edge_dir.cross(self.Zp).normalized()
            
            # Build polygon "Outwards" (Left of edge)
            center = edge_midpoint + (perp_dir * apothem)
            
            # 4. Start Angle (Center -> p1)
            to_p1 = p1 - center
            to_p1_2d = world_to_plane(to_p1, self.Xp, self.Yp)
            start_angle = math.atan2(to_p1_2d.y, to_p1_2d.x)
            
            self.preview_pts = polygon_points_world(
                center,
                circumradius,
                start_angle,
                self.segments,
                self.Xp,
                self.Yp
            )

    def handle_click(self, context, event, snap_point, snap_normal, button_id=None):
        if self.stage == 0:
            self.pivot = snap_point
            self.state["locked"] = True
            self.state["locked_normal"] = self.Zp
            self.stage = 1
            return 'NEXT_STAGE'

        if self.stage == 1:
            return 'FINISHED'
            
        return None