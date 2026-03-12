import math
import bpy
from mathutils import Vector, geometry
from .base_tool import SurfaceDrawTool
from ..plane_utils import world_to_plane, plane_to_world
from ..inference_utils import get_axis_snapped_location
from ..orientation_utils import orthonormal_basis_from_normal
from bpy_extras import view3d_utils

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
        self.ref_normal = Vector((0,0,1))
        self.vertical_override_axis = None
        self.major_axis = Vector((1,0,0))

    def update(self, context, event, snap_point, snap_normal):
        # Stage 0: Let the Prep Chef find the wall/orientation
        if self.stage == 0:
            self.update_initial_plane(context, event, snap_point, snap_normal)
            if self.Zp: self.ref_normal = self.Zp.copy()
            return

        # Stage 1: Dragging to Corner
        if self.stage == 1:
            pv = self.pivot
            coord = (event.mouse_region_x, event.mouse_region_y)
            target = snap_point

            # Snapping
            strength_deg = self.state.get("snap_strength", 6.0)
            axis_thresh = math.cos(math.radians(strength_deg))
            inf_loc, _, _ = get_axis_snapped_location(pv, coord, context, snap_threshold=axis_thresh)
            if inf_loc and not event.alt: target = inf_loc

            # Perpendicular Logic
            bridge = target - pv
            if bridge.length_squared > 1e-8:
                b_vec = bridge.normalized()
                self.major_axis = b_vec
                up = self.ref_normal
                is_perp = self.state.get("is_perpendicular", False)
                is_vertical = abs(b_vec.dot(up)) > (0.98 if getattr(self, "_is_vert_last", False) else 0.995)
                self._is_vert_last = is_vertical

                if is_perp or is_vertical:
                    if is_vertical:
                        ax_x, ax_y, _ = orthonormal_basis_from_normal(up)
                        if self.vertical_override_axis is None:
                            rv3d = context.region_data
                            view_fwd = rv3d.view_matrix.inverted().to_3x3() @ Vector((0,0,-1))
                            new_Zp = ax_x if abs(view_fwd.dot(ax_x)) > abs(view_fwd.dot(ax_y)) else ax_y
                        else:
                            new_Zp = ax_x if self.vertical_override_axis == 'X' else ax_y
                        
                        if new_Zp.length > 1e-4:
                            view_fwd = context.region_data.view_matrix.inverted().to_3x3() @ Vector((0,0,-1))
                            if new_Zp.dot(view_fwd) > 0: new_Zp = -new_Zp
                            self.Zp = new_Zp.normalized()
                            self.Yp = up.normalized()
                            self.Xp = self.Yp.cross(self.Zp).normalized()
                    else:
                        new_Zp = b_vec.cross(up).normalized()
                        if new_Zp.length > 1e-4:
                            view_fwd = context.region_data.view_matrix.inverted().to_3x3() @ Vector((0,0,-1))
                            if new_Zp.dot(view_fwd) > 0: new_Zp = -new_Zp
                            self.Zp = new_Zp.normalized()
                            self.Xp = b_vec
                            self.Yp = self.Zp.cross(self.Xp).normalized()
                else:
                    self.Zp = up.copy()
                    self.Xp = b_vec
                    self.Yp = self.Zp.cross(self.Xp).normalized()

            # Final project to current plane (only if NOT axis snapped)
            if not inf_loc:
                ray_origin = view3d_utils.region_2d_to_origin_3d(context.region, context.region_data, coord)
                ray_vector = view3d_utils.region_2d_to_vector_3d(context.region, context.region_data, coord)
                hit = geometry.intersect_line_plane(ray_origin, ray_origin + ray_vector, pv, self.Zp)
                if hit: target = hit
            
            self.current = target
            
            # Radius is distance to corner
            d_vec = target - pv
            self.radius = d_vec.length
            
            # Calculate Angle to align vertex with mouse cursor
            d2 = world_to_plane(d_vec, self.Xp, self.Yp)
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

    def handle_input(self, context, event):
        if super().handle_plane_lock_input(context, event):
            if self.Zp: self.ref_normal = self.Zp.copy()
            return True
        if event.type == 'P' and event.value == 'PRESS':
            if self.stage == 0: return False
            self.state["is_perpendicular"] = not self.state.get("is_perpendicular", False)
            self.vertical_override_axis = None 
            self.state["locked"], self.state["locked_normal"] = True, self.ref_normal
            return True
        if event.type in {'X', 'Y'} and event.value == 'PRESS' and self.stage > 0:
            self.vertical_override_axis = event.type
            return True
        return False

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
        self.ref_normal = Vector((0,0,1))
        self.vertical_override_axis = None
        self.major_axis = Vector((1,0,0))

    def update(self, context, event, snap_point, snap_normal):
        if self.stage == 0:
            self.update_initial_plane(context, event, snap_point, snap_normal)
            if self.Zp: self.ref_normal = self.Zp.copy()
            return

        if self.stage == 1:
            pv = self.pivot
            coord = (event.mouse_region_x, event.mouse_region_y)
            target = snap_point

            # Snapping
            strength_deg = self.state.get("snap_strength", 6.0)
            axis_thresh = math.cos(math.radians(strength_deg))
            inf_loc, _, _ = get_axis_snapped_location(pv, coord, context, snap_threshold=axis_thresh)
            if inf_loc and not event.alt: target = inf_loc

            # Perpendicular Logic
            bridge = target - pv
            if bridge.length_squared > 1e-8:
                b_vec = bridge.normalized()
                self.major_axis = b_vec
                up = self.ref_normal
                is_perp = self.state.get("is_perpendicular", False)
                is_vertical = abs(b_vec.dot(up)) > (0.98 if getattr(self, "_is_vert_last", False) else 0.995)
                self._is_vert_last = is_vertical

                if is_perp or is_vertical:
                    if is_vertical:
                        ax_x, ax_y, _ = orthonormal_basis_from_normal(up)
                        if self.vertical_override_axis is None:
                            rv3d = context.region_data
                            view_fwd = rv3d.view_matrix.inverted().to_3x3() @ Vector((0,0,-1))
                            new_Zp = ax_x if abs(view_fwd.dot(ax_x)) > abs(view_fwd.dot(ax_y)) else ax_y
                        else:
                            new_Zp = ax_x if self.vertical_override_axis == 'X' else ax_y
                        
                        if new_Zp.length > 1e-4:
                            view_fwd = context.region_data.view_matrix.inverted().to_3x3() @ Vector((0,0,-1))
                            if new_Zp.dot(view_fwd) > 0: new_Zp = -new_Zp
                            self.Zp = new_Zp.normalized()
                            self.Yp = up.normalized()
                            self.Xp = self.Yp.cross(self.Zp).normalized()
                    else:
                        new_Zp = b_vec.cross(up).normalized()
                        if new_Zp.length > 1e-4:
                            view_fwd = context.region_data.view_matrix.inverted().to_3x3() @ Vector((0,0,-1))
                            if new_Zp.dot(view_fwd) > 0: new_Zp = -new_Zp
                            self.Zp = new_Zp.normalized()
                            self.Xp = b_vec
                            self.Yp = self.Zp.cross(self.Xp).normalized()
                else:
                    self.Zp = up.copy()
                    self.Xp = b_vec
                    self.Yp = self.Zp.cross(self.Xp).normalized()

            # Final project to current plane (only if NOT axis snapped)
            if not inf_loc:
                ray_origin = view3d_utils.region_2d_to_origin_3d(context.region, context.region_data, coord)
                ray_vector = view3d_utils.region_2d_to_vector_3d(context.region, context.region_data, coord)
                hit = geometry.intersect_line_plane(ray_origin, ray_origin + ray_vector, pv, self.Zp)
                if hit: target = hit
            
            self.current = target
            
            d_vec = target - pv
            apothem = d_vec.length
            self.radius = apothem 
            
            d2 = world_to_plane(d_vec, self.Xp, self.Yp)
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

    def handle_input(self, context, event):
        if super().handle_plane_lock_input(context, event):
            if self.Zp: self.ref_normal = self.Zp.copy()
            return True
        if event.type == 'P' and event.value == 'PRESS':
            if self.stage == 0: return False
            self.state["is_perpendicular"] = not self.state.get("is_perpendicular", False)
            self.vertical_override_axis = None 
            self.state["locked"], self.state["locked_normal"] = True, self.ref_normal
            return True
        if event.type in {'X', 'Y'} and event.value == 'PRESS' and self.stage > 0:
            self.vertical_override_axis = event.type
            return True
        return False

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
        self.ref_normal = Vector((0,0,1))
        self.vertical_override_axis = None
        self.major_axis = Vector((1,0,0))

    def update(self, context, event, snap_point, snap_normal):
        # Stage 0: Set First Corner
        if self.stage == 0:
            self.update_initial_plane(context, event, snap_point, snap_normal)
            if self.Zp: self.ref_normal = self.Zp.copy()
            return

        # Stage 1: Dragging to Second Corner (Edge Definition)
        if self.stage == 1:
            corner1 = self.pivot
            coord = (event.mouse_region_x, event.mouse_region_y)
            target = snap_point

            # Snapping
            strength_deg = self.state.get("snap_strength", 6.0)
            axis_thresh = math.cos(math.radians(strength_deg))
            inf_loc, _, _ = get_axis_snapped_location(corner1, coord, context, snap_threshold=axis_thresh)
            if inf_loc and not event.alt: target = inf_loc

            # Perpendicular Logic
            bridge = target - corner1
            if bridge.length_squared > 1e-8:
                b_vec = bridge.normalized()
                self.major_axis = b_vec
                up = self.ref_normal
                is_perp = self.state.get("is_perpendicular", False)
                is_vertical = abs(b_vec.dot(up)) > (0.98 if getattr(self, "_is_vert_last", False) else 0.995)
                self._is_vert_last = is_vertical

                if is_perp or is_vertical:
                    if is_vertical:
                        ax_x, ax_y, _ = orthonormal_basis_from_normal(up)
                        if self.vertical_override_axis is None:
                            rv3d = context.region_data
                            view_fwd = rv3d.view_matrix.inverted().to_3x3() @ Vector((0,0,-1))
                            new_Zp = ax_x if abs(view_fwd.dot(ax_x)) > abs(view_fwd.dot(ax_y)) else ax_y
                        else:
                            new_Zp = ax_x if self.vertical_override_axis == 'X' else ax_y
                        
                        if new_Zp.length > 1e-4:
                            view_fwd = context.region_data.view_matrix.inverted().to_3x3() @ Vector((0,0,-1))
                            if new_Zp.dot(view_fwd) > 0: new_Zp = -new_Zp
                            self.Zp = new_Zp.normalized()
                            self.Yp = up.normalized()
                            self.Xp = self.Yp.cross(self.Zp).normalized()
                    else:
                        new_Zp = b_vec.cross(up).normalized()
                        if new_Zp.length > 1e-4:
                            view_fwd = context.region_data.view_matrix.inverted().to_3x3() @ Vector((0,0,-1))
                            if new_Zp.dot(view_fwd) > 0: new_Zp = -new_Zp
                            self.Zp = new_Zp.normalized()
                            self.Xp = b_vec
                            self.Yp = self.Zp.cross(self.Xp).normalized()
                else:
                    self.Zp = up.copy()
                    self.Xp = b_vec
                    self.Yp = self.Zp.cross(self.Xp).normalized()

            # Final project to current plane (only if NOT axis snapped)
            if not inf_loc:
                ray_origin = view3d_utils.region_2d_to_origin_3d(context.region, context.region_data, coord)
                ray_vector = view3d_utils.region_2d_to_vector_3d(context.region, context.region_data, coord)
                hit = geometry.intersect_line_plane(ray_origin, ray_origin + ray_vector, corner1, self.Zp)
                if hit: target = hit

            self.current = target
            
            # 1. Calculate Edge Vector on Plane
            d_vec = target - corner1
            d_plane = d_vec # Already on plane
            
            side_length = d_plane.length
            
            # Avoid division by zero if points are too close
            if side_length < 1e-6:
                self.preview_pts = []
                return

            self.segments = max(3, self.state["segments"])
            
            # 2. Calculate Circumradius (R) from Side Length (s)
            angle_step = math.pi / self.segments
            circumradius = side_length / (2.0 * math.sin(angle_step))
            
            # 3. Calculate Apothem (distance from edge center to polygon center)
            apothem = side_length / (2.0 * math.tan(angle_step))
            
            # 4. Find the Center of the Polygon
            edge_midpoint = corner1 + (d_plane * 0.5)
            edge_dir = d_plane.normalized()
            perp_dir = edge_dir.cross(self.Zp).normalized()
            
            # NOTE: This builds the polygon to the "Left" of the direction you draw.
            center = edge_midpoint + (perp_dir * apothem)
            
            # 5. Calculate Start Angle
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

    def handle_input(self, context, event):
        if super().handle_plane_lock_input(context, event):
            if self.Zp: self.ref_normal = self.Zp.copy()
            return True
        if event.type == 'P' and event.value == 'PRESS':
            if self.stage == 0: return False
            self.state["is_perpendicular"] = not self.state.get("is_perpendicular", False)
            self.vertical_override_axis = None 
            self.state["locked"], self.state["locked_normal"] = True, self.ref_normal
            return True
        if event.type in {'X', 'Y'} and event.value == 'PRESS' and self.stage > 0:
            self.vertical_override_axis = event.type
            return True
        return False

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
        self.ref_normal = Vector((0,0,1))
        self.vertical_override_axis = None
        self.major_axis = Vector((1,0,0))

    def update(self, context, event, snap_point, snap_normal):
        # Stage 0: Set First Point of Edge
        if self.stage == 0:
            self.update_initial_plane(context, event, snap_point, snap_normal)
            if self.Zp: self.ref_normal = self.Zp.copy()
            return

        # Stage 1: Dragging to Second Point of Edge
        if self.stage == 1:
            p1 = self.pivot
            coord = (event.mouse_region_x, event.mouse_region_y)
            target = snap_point

            # Snapping
            strength_deg = self.state.get("snap_strength", 6.0)
            axis_thresh = math.cos(math.radians(strength_deg))
            inf_loc, _, _ = get_axis_snapped_location(p1, coord, context, snap_threshold=axis_thresh)
            if inf_loc and not event.alt: target = inf_loc

            # Perpendicular Logic
            bridge = target - p1
            if bridge.length_squared > 1e-8:
                b_vec = bridge.normalized()
                self.major_axis = b_vec
                up = self.ref_normal
                is_perp = self.state.get("is_perpendicular", False)
                is_vertical = abs(b_vec.dot(up)) > (0.98 if getattr(self, "_is_vert_last", False) else 0.995)
                self._is_vert_last = is_vertical

                if is_perp or is_vertical:
                    if is_vertical:
                        ax_x, ax_y, _ = orthonormal_basis_from_normal(up)
                        if self.vertical_override_axis is None:
                            rv3d = context.region_data
                            view_fwd = rv3d.view_matrix.inverted().to_3x3() @ Vector((0,0,-1))
                            new_Zp = ax_x if abs(view_fwd.dot(ax_x)) > abs(view_fwd.dot(ax_y)) else ax_y
                        else:
                            new_Zp = ax_x if self.vertical_override_axis == 'X' else ax_y
                        
                        if new_Zp.length > 1e-4:
                            view_fwd = context.region_data.view_matrix.inverted().to_3x3() @ Vector((0,0,-1))
                            if new_Zp.dot(view_fwd) > 0: new_Zp = -new_Zp
                            self.Zp = new_Zp.normalized()
                            self.Yp = up.normalized()
                            self.Xp = self.Yp.cross(self.Zp).normalized()
                    else:
                        new_Zp = b_vec.cross(up).normalized()
                        if new_Zp.length > 1e-4:
                            view_fwd = context.region_data.view_matrix.inverted().to_3x3() @ Vector((0,0,-1))
                            if new_Zp.dot(view_fwd) > 0: new_Zp = -new_Zp
                            self.Zp = new_Zp.normalized()
                            self.Xp = b_vec
                            self.Yp = self.Zp.cross(self.Xp).normalized()
                else:
                    self.Zp = up.copy()
                    self.Xp = b_vec
                    self.Yp = self.Zp.cross(self.Xp).normalized()

            # Final project to current plane (only if NOT axis snapped)
            if not inf_loc:
                ray_origin = view3d_utils.region_2d_to_origin_3d(context.region, context.region_data, coord)
                ray_vector = view3d_utils.region_2d_to_vector_3d(context.region, context.region_data, coord)
                hit = geometry.intersect_line_plane(ray_origin, ray_origin + ray_vector, p1, self.Zp)
                if hit: target = hit

            self.current = target
            
            # 1. Calculate Edge Vector on Plane
            d_vec = target - p1
            d_plane = d_vec # Already on plane
            
            side_length = d_plane.length
            
            if side_length < 1e-6:
                self.preview_pts = []
                return

            self.segments = max(3, self.state["segments"])
            
            # 2. Geometry Math
            angle_step = math.pi / self.segments
            circumradius = side_length / (2.0 * math.sin(angle_step))
            
            # Apothem = s / (2 * tan(pi/n))
            apothem = side_length / (2.0 * math.tan(angle_step))
            
            # 3. Find Center
            edge_midpoint = p1 + (d_plane * 0.5)
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

    def handle_input(self, context, event):
        if super().handle_plane_lock_input(context, event):
            if self.Zp: self.ref_normal = self.Zp.copy()
            return True
        if event.type == 'P' and event.value == 'PRESS':
            if self.stage == 0: return False
            self.state["is_perpendicular"] = not self.state.get("is_perpendicular", False)
            self.vertical_override_axis = None 
            self.state["locked"], self.state["locked_normal"] = True, self.ref_normal
            return True
        if event.type in {'X', 'Y'} and event.value == 'PRESS' and self.stage > 0:
            self.vertical_override_axis = event.type
            return True
        return False

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