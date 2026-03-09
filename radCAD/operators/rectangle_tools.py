import math
from mathutils import Vector
from .base_tool import SurfaceDrawTool

class RectangleTool_CenterCorner(SurfaceDrawTool):
    def __init__(self, core):
        super().__init__(core)
        self.mode = "RECTANGLE_CENTER_CORNER"
        self.current = None
        self.preview_pts = []

    def update(self, context, event, snap_point, snap_normal):
        # Stage 0: Set Center / Plane
        if self.stage == 0:
            self.update_initial_plane(context, event, snap_point, snap_normal)
            return

        # Stage 1: Drag to Corner
        if self.stage == 1:
            center = self.pivot
            self.current = snap_point
            
            d_vec = snap_point - center
            if self.Zp:
                d_plane = d_vec - self.Zp * d_vec.dot(self.Zp)
            else:
                d_plane = d_vec

            dx = d_plane.dot(self.Xp)
            dy = d_plane.dot(self.Yp)
            
            # P1 is the corner you are dragging to
            # We mirror it around the center
            p1 = center + (self.Xp * dx) + (self.Yp * dy)
            p2 = center - (self.Xp * dx) + (self.Yp * dy)
            p3 = center - (self.Xp * dx) - (self.Yp * dy)
            p4 = center + (self.Xp * dx) - (self.Yp * dy)
            
            # CLOSE THE LOOP: Add p1 again at the end
            self.preview_pts = [p1, p2, p3, p4, p1]

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

class RectangleTool_CornerCorner(SurfaceDrawTool):
    def __init__(self, core):
        super().__init__(core)
        self.mode = "RECTANGLE_CORNER_CORNER"
        self.current = None
        self.preview_pts = []

    def update(self, context, event, snap_point, snap_normal):
        if self.stage == 0:
            self.update_initial_plane(context, event, snap_point, snap_normal)
            return

        if self.stage == 1:
            c1 = self.pivot
            self.current = snap_point
            
            d_vec = snap_point - c1
            if self.Zp:
                d_plane = d_vec - self.Zp * d_vec.dot(self.Zp)
            else:
                d_plane = d_vec

            dx = d_plane.dot(self.Xp)
            dy = d_plane.dot(self.Yp)
            
            # Start at Pivot -> Move X -> Move X+Y -> Move Y
            p1 = c1
            p2 = c1 + (self.Xp * dx)
            p3 = c1 + (self.Xp * dx) + (self.Yp * dy)
            p4 = c1 + (self.Yp * dy)
            
            # CLOSE THE LOOP
            self.preview_pts = [p1, p2, p3, p4, p1]

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

class RectangleTool_3Point(SurfaceDrawTool):
    def __init__(self, core):
        super().__init__(core)
        self.mode = "RECTANGLE_3_POINTS"
        self.current = None
        self.preview_pts = []
        self.p2 = None

    def update(self, context, event, snap_point, snap_normal):
        # Stage 0: Set P1 (Start of Edge)
        if self.stage == 0:
            self.update_initial_plane(context, event, snap_point, snap_normal)
            return

        # Stage 1: Set P2 (End of Edge / Rotation)
        if self.stage == 1:
            p1 = self.pivot
            self.current = snap_point
            
            # Just draw the edge being defined
            self.preview_pts = [p1, snap_point]

        # Stage 2: Set Width/Height
        if self.stage == 2:
            p1 = self.pivot
            p2 = self.p2
            self.current = snap_point
            
            # 1. Edge Vector
            edge_vec = p2 - p1
            
            # 2. Perpendicular Vector on Plane
            # We use the cross product with the plane normal (Zp) to find the "sideways" direction
            if self.Zp:
                perp_vec = edge_vec.cross(self.Zp).normalized()
            else:
                perp_vec = Vector((0,0,1)).cross(edge_vec).normalized() # Fallback
            
            # 3. Vector from P2 to Mouse
            mouse_vec = snap_point - p2
            
            # 4. Project mouse_vec onto perp_vec to get the "Outward" distance
            height = mouse_vec.dot(perp_vec)
            height_vec = perp_vec * height
            
            # 5. Calculate corners
            # c1 -> c2 is the first edge
            # c3 is c2 pushed out
            # c4 is c1 pushed out
            c1 = p1
            c2 = p2
            c3 = p2 + height_vec
            c4 = p1 + height_vec
            
            # Close loop
            self.preview_pts = [c1, c2, c3, c4, c1]

    def handle_click(self, context, event, snap_point, snap_normal, button_id=None):
        if self.stage == 0:
            self.pivot = snap_point
            self.state["locked"] = True
            self.state["locked_normal"] = self.Zp
            self.stage = 1
            return 'NEXT_STAGE'

        if self.stage == 1:
            # Prevent zero-length edge
            if (snap_point - self.pivot).length < 1e-6: return None 
            self.p2 = snap_point
            self.stage = 2
            return 'NEXT_STAGE'

        if self.stage == 2:
            return 'FINISHED'
        return None