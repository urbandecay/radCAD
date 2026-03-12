import math
from mathutils import Vector, geometry
from .base_tool import SurfaceDrawTool
from ..plane_utils import plane_to_world
from ..orientation_utils import orthonormal_basis_from_normal

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
        self.ref_normal = Vector((0,0,1))
        self.vertical_override_axis = None

    def update(self, context, event, snap_point, snap_normal):
        if self.stage == 0:
            self.update_initial_plane(context, event, snap_point, snap_normal)
            if self.Zp: self.ref_normal = self.Zp.copy()
            return

        if self.stage == 1:
            # 1. DIRECT PROJECT TO FLOOR
            from bpy_extras import view3d_utils
            coord = (event.mouse_region_x, event.mouse_region_y)
            ray_origin = view3d_utils.region_2d_to_origin_3d(context.region, context.region_data, coord)
            ray_vector = view3d_utils.region_2d_to_vector_3d(context.region, context.region_data, coord)
            target = geometry.intersect_line_plane(ray_origin, ray_origin + ray_vector, self.pivot, self.ref_normal)
            if target is None: target = snap_point 

            # 2. APPLY SNAPPING
            from ..inference_utils import get_axis_snapped_location
            strength_deg = self.state.get("snap_strength", 6.0)
            axis_thresh = math.cos(math.radians(strength_deg))
            inf_loc, _, _ = get_axis_snapped_location(self.pivot, (event.mouse_region_x, event.mouse_region_y), context, snap_threshold=axis_thresh)
            if inf_loc and not event.alt: target = inf_loc

            # 3. STABILIZED BASIS
            bridge = target - self.pivot
            if bridge.length_squared > 1e-8:
                b_vec = bridge.normalized()
                up = self.ref_normal
                dot_v = abs(b_vec.dot(up))
                was_vertical = getattr(self, "_is_vert_last", False)
                threshold = 0.98 if was_vertical else 0.995 
                is_vertical = dot_v > threshold
                self._is_vert_last = is_vertical

                if self.state.get("is_perpendicular") or is_vertical:
                    from bpy_extras.view3d_utils import location_3d_to_region_2d
                    p2d = location_3d_to_region_2d(context.region, context.region_data, self.pivot)
                    if p2d:
                        if is_vertical:
                            ax_x, ax_y, _ = orthonormal_basis_from_normal(up)
                            if self.vertical_override_axis is None:
                                rv3d = context.region_data
                                view_fwd = rv3d.view_matrix.inverted().to_3x3() @ Vector((0,0,-1))
                                new_Zp = ax_x if abs(view_fwd.dot(ax_x)) > abs(view_fwd.dot(ax_y)) else ax_y
                            else:
                                new_Zp = ax_x if self.vertical_override_axis == 'X' else ax_y
                        else:
                            new_Zp = b_vec.cross(up).normalized()
                        
                        if new_Zp.length > 1e-4:
                            rv3d = context.region_data
                            view_fwd = rv3d.view_matrix.inverted().to_3x3() @ Vector((0,0,-1))
                            if new_Zp.dot(view_fwd) > 0: new_Zp = -new_Zp
                            self.Zp = new_Zp.normalized()
                            self.Yp = up.normalized()
                            self.Xp = self.Yp.cross(self.Zp).normalized()
                else:
                    self.Zp = up.copy()
                    self.Xp = b_vec
                    self.Yp = self.Zp.cross(self.Xp).normalized()

            self.rx = (target - self.pivot).length
            self.current = target
            self.preview_pts = [self.pivot, self.pivot + (self.Xp * self.rx)]

        if self.stage == 2:
            pv = self.pivot
            d = snap_point - pv
            dist_y = d.dot(self.Yp)
            self.ry = abs(dist_y)
            self.current = pv + (self.Yp * dist_y)
            self.segments = self.state["segments"]
            self.preview_pts = ellipse_points_world(self.pivot, self.rx, self.ry, self.segments, self.Xp, self.Yp)

    def handle_input(self, context, event):
        if super().handle_plane_lock_input(context, event):
            if self.Zp: self.ref_normal = self.Zp.copy()
            return True
        if event.type == 'P' and event.value == 'PRESS':
            if self.stage == 0: return False
            self.state["is_perpendicular"] = not self.state.get("is_perpendicular", False)
            self.vertical_override_axis = None 
            self.state["locked"] = True
            self.state["locked_normal"] = self.ref_normal
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
            if self.Zp: self.ref_normal = self.Zp.copy()
            self.stage = 1
            return 'NEXT_STAGE'
        if self.stage == 1:
            if self.rx < 1e-6: return None
            self.stage = 2
            return 'NEXT_STAGE'
        if self.stage == 2: return 'FINISHED'
        return None

class EllipseTool_FociPoint(SurfaceDrawTool):
    def __init__(self, core):
        super().__init__(core)
        self.mode = "ELLIPSE_FOCI"
        self.segments = self.state["segments"]
        self.f1 = None 
        self.f2 = None
        self.rx = 0.0 
        self.ry = 0.0 
        self.current = None
        self.preview_pts = []
        self.ref_normal = Vector((0,0,1))
        self.vertical_override_axis = None

    def update(self, context, event, snap_point, snap_normal):
        if self.stage == 0:
            self.update_initial_plane(context, event, snap_point, snap_normal)
            if self.Zp: self.ref_normal = self.Zp.copy()
            return

        if self.stage == 1:
            self.f1 = self.pivot
            # PROJECT TO FLOOR
            from bpy_extras import view3d_utils
            coord = (event.mouse_region_x, event.mouse_region_y)
            ray_origin = view3d_utils.region_2d_to_origin_3d(context.region, context.region_data, coord)
            ray_vector = view3d_utils.region_2d_to_vector_3d(context.region, context.region_data, coord)
            target = geometry.intersect_line_plane(ray_origin, ray_origin + ray_vector, self.f1, self.ref_normal)
            if target is None: target = snap_point 

            # SNAPPING
            from ..inference_utils import get_axis_snapped_location
            strength_deg = self.state.get("snap_strength", 6.0)
            axis_thresh = math.cos(math.radians(strength_deg))
            inf_loc, _, _ = get_axis_snapped_location(self.f1, (event.mouse_region_x, event.mouse_region_y), context, snap_threshold=axis_thresh)
            if inf_loc and not event.alt: target = inf_loc

            # BASIS
            bridge = target - self.f1
            if bridge.length_squared > 1e-8:
                b_vec = bridge.normalized()
                up = self.ref_normal
                dot_v = abs(b_vec.dot(up))
                was_vertical = getattr(self, "_is_vert_last", False)
                threshold = 0.98 if was_vertical else 0.995 
                is_vertical = dot_v > threshold
                self._is_vert_last = is_vertical

                if self.state.get("is_perpendicular") or is_vertical:
                    if is_vertical:
                        ax_x, ax_y, _ = orthonormal_basis_from_normal(up)
                        if self.vertical_override_axis is None:
                            rv3d = context.region_data
                            view_fwd = rv3d.view_matrix.inverted().to_3x3() @ Vector((0,0,-1))
                            new_Zp = ax_x if abs(view_fwd.dot(ax_x)) > abs(view_fwd.dot(ax_y)) else ax_y
                        else:
                            new_Zp = ax_x if self.vertical_override_axis == 'X' else ax_y
                    else:
                        new_Zp = b_vec.cross(up).normalized()
                    
                    if new_Zp.length > 1e-4:
                        rv3d = context.region_data
                        view_fwd = rv3d.view_matrix.inverted().to_3x3() @ Vector((0,0,-1))
                        if new_Zp.dot(view_fwd) > 0: new_Zp = -new_Zp
                        self.Zp, self.Yp = new_Zp.normalized(), up.normalized()
                        self.Xp = self.Yp.cross(self.Zp).normalized()
                else:
                    self.Zp = up.copy()
                    self.Xp, self.Yp = b_vec, self.Zp.cross(b_vec).normalized()

            self.current = target
            self.preview_pts = [self.f1, self.current]

        if self.stage == 2:
            self.f1 = self.pivot
            self.state["f1"], self.state["f2"] = self.f1, self.f2
            d_raw = snap_point - self.f1
            d_plane = d_raw - self.Zp * d_raw.dot(self.Zp)
            P = self.f1 + d_plane
            self.current = P
            center = (self.f1 + self.f2) * 0.5
            c = (self.f2 - self.f1).length * 0.5
            dist_sum = (P - self.f1).length + (P - self.f2).length
            a = dist_sum * 0.5
            if a < c + 1e-6: a = c + 1e-6
            b = math.sqrt(a**2 - c**2)
            self.rx, self.ry = a, b
            self.segments = self.state["segments"]
            self.preview_pts = ellipse_points_world(center, self.rx, self.ry, self.segments, self.Xp, self.Yp)

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
            self.state["locked"], self.state["locked_normal"] = True, self.Zp
            if self.Zp: self.ref_normal = self.Zp.copy()
            self.stage = 1
            return 'NEXT_STAGE'
        if self.stage == 1:
            self.f2 = self.current if self.current else snap_point
            if (self.f2 - self.pivot).length < 1e-6: return None
            self.stage = 2
            return 'NEXT_STAGE'
        if self.stage == 2: return 'FINISHED'
        return None

class EllipseTool_FromEndpoints(SurfaceDrawTool):
    def __init__(self, core):
        super().__init__(core)
        self.mode = "ELLIPSE_ENDPOINTS"
        self.segments = self.state["segments"]
        self.p1 = None 
        self.p2 = None
        self.rx = 0.0 
        self.ry = 0.0 
        self.current = None
        self.preview_pts = []
        self.ref_normal = Vector((0,0,1))
        self.vertical_override_axis = None

    def update(self, context, event, snap_point, snap_normal):
        if self.stage == 0:
            self.update_initial_plane(context, event, snap_point, snap_normal)
            if self.Zp: self.ref_normal = self.Zp.copy()
            return

        if self.stage == 1:
            self.p1 = self.pivot
            # PROJECT TO FLOOR
            from bpy_extras import view3d_utils
            coord = (event.mouse_region_x, event.mouse_region_y)
            ray_origin = view3d_utils.region_2d_to_origin_3d(context.region, context.region_data, coord)
            ray_vector = view3d_utils.region_2d_to_vector_3d(context.region, context.region_data, coord)
            target = geometry.intersect_line_plane(ray_origin, ray_origin + ray_vector, self.p1, self.ref_normal)
            if target is None: target = snap_point 

            # SNAPPING
            from ..inference_utils import get_axis_snapped_location
            strength_deg = self.state.get("snap_strength", 6.0)
            axis_thresh = math.cos(math.radians(strength_deg))
            inf_loc, _, _ = get_axis_snapped_location(self.p1, (event.mouse_region_x, event.mouse_region_y), context, snap_threshold=axis_thresh)
            if inf_loc and not event.alt: target = inf_loc

            # BASIS
            bridge = target - self.p1
            if bridge.length_squared > 1e-8:
                b_vec = bridge.normalized()
                up = self.ref_normal
                dot_v = abs(b_vec.dot(up))
                was_vertical = getattr(self, "_is_vert_last", False)
                threshold = 0.98 if was_vertical else 0.995 
                is_vertical = dot_v > threshold
                self._is_vert_last = is_vertical

                if self.state.get("is_perpendicular") or is_vertical:
                    if is_vertical:
                        ax_x, ax_y, _ = orthonormal_basis_from_normal(up)
                        if self.vertical_override_axis is None:
                            rv3d = context.region_data
                            view_fwd = rv3d.view_matrix.inverted().to_3x3() @ Vector((0,0,-1))
                            new_Zp = ax_x if abs(view_fwd.dot(ax_x)) > abs(view_fwd.dot(ax_y)) else ax_y
                        else:
                            new_Zp = ax_x if self.vertical_override_axis == 'X' else ax_y
                    else:
                        new_Zp = b_vec.cross(up).normalized()
                    
                    if new_Zp.length > 1e-4:
                        rv3d = context.region_data
                        view_fwd = rv3d.view_matrix.inverted().to_3x3() @ Vector((0,0,-1))
                        if new_Zp.dot(view_fwd) > 0: new_Zp = -new_Zp
                        self.Zp, self.Yp = new_Zp.normalized(), up.normalized()
                        self.Xp = self.Yp.cross(self.Zp).normalized()
                else:
                    self.Zp = up.copy()
                    self.Xp, self.Yp = b_vec, self.Zp.cross(b_vec).normalized()

            diameter = (target - self.p1).length
            self.rx = diameter * 0.5
            self.current = target
            self.preview_pts = [self.p1, self.current]

        if self.stage == 2:
            center = (self.p1 + self.p2) * 0.5
            d_raw = snap_point - center
            d_plane = d_raw - self.Zp * d_raw.dot(self.Zp)
            dist_y = abs(d_plane.dot(self.Yp))
            self.ry = dist_y
            if d_plane.length > 1e-6:
                d2 = Vector((d_plane.dot(self.Xp), d_plane.dot(self.Yp)))
                angle = math.atan2(d2.y, d2.x)
                local_snap = Vector((self.rx * math.cos(angle), self.ry * math.sin(angle)))
                self.current = center + (self.Xp * local_snap.x) + (self.Yp * local_snap.y)
            else: self.current = center + d_plane
            self.segments = self.state["segments"]
            self.preview_pts = ellipse_points_world(center, self.rx, self.ry, self.segments, self.Xp, self.Yp)

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
            self.state["locked"], self.state["locked_normal"] = True, self.Zp
            if self.Zp: self.ref_normal = self.Zp.copy()
            self.stage = 1
            return 'NEXT_STAGE'
        if self.stage == 1:
            self.p2 = self.current if self.current else snap_point
            if (self.p2 - self.pivot).length < 1e-6: return None
            self.stage = 2
            return 'NEXT_STAGE'
        if self.stage == 2: return 'FINISHED'
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
        self.ref_normal = Vector((0,0,1))

    def update(self, context, event, snap_point, snap_normal):
        if self.stage == 0:
            self.update_initial_plane(context, event, snap_point, snap_normal)
            if self.Zp: self.ref_normal = self.Zp.copy()
            return
        if self.stage == 1:
            # PROJECT TO FLOOR
            from bpy_extras import view3d_utils
            coord = (event.mouse_region_x, event.mouse_region_y)
            ray_origin = view3d_utils.region_2d_to_origin_3d(context.region, context.region_data, coord)
            ray_vector = view3d_utils.region_2d_to_vector_3d(context.region, context.region_data, coord)
            target = geometry.intersect_line_plane(ray_origin, ray_origin + ray_vector, self.pivot, self.ref_normal)
            if target is None: target = snap_point 

            # Snapping handled by general inferencing if possible, but corners is usually surface-driven
            self.current = target
            d_vec = self.current - self.pivot
            width, height = d_vec.dot(self.Xp), d_vec.dot(self.Yp)
            self.rx, self.ry = abs(width) * 0.5, abs(height) * 0.5
            center = self.pivot + (self.Xp * (width * 0.5)) + (self.Yp * (height * 0.5))
            self.segments = self.state["segments"]
            self.preview_pts = ellipse_points_world(center, self.rx, self.ry, self.segments, self.Xp, self.Yp)

    def handle_click(self, context, event, snap_point, snap_normal, button_id=None):
        if self.stage == 0:
            self.pivot = snap_point
            self.state["locked"], self.state["locked_normal"] = True, self.Zp
            if self.Zp: self.ref_normal = self.Zp.copy()
            self.stage = 1
            return 'NEXT_STAGE'
        if self.stage == 1: return 'FINISHED'
        return None