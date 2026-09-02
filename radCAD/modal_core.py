import math
import time
import bpy
import bmesh
from mathutils import Vector, Matrix
from mathutils.geometry import intersect_line_plane
from bpy_extras.view3d_utils import region_2d_to_origin_3d, region_2d_to_vector_3d, location_3d_to_region_2d

from .modal_state import state, reset_state_from_context
from .orientation_utils import orthonormal_basis_from_normal
from .plane_utils import world_to_plane, plane_to_world, project_mouse_to_ground, raycast_under_mouse

# --- PERSISTENT REGISTRY FIX ---
# We store the registry in driver_namespace so it survives script reloads.
# This ensures "Clear Stuck Overlays" can always find handles from previous sessions.
if "radcad_draw_handler_registry" not in bpy.app.driver_namespace:
    bpy.app.driver_namespace["radcad_draw_handler_registry"] = {}

_DRAW_HANDLER_REGISTRY = bpy.app.driver_namespace["radcad_draw_handler_registry"]
# -------------------------------

class DrawManager:
    @staticmethod
    def add_handler(source_id, draw_func, args, region_type='WINDOW', draw_event='POST_VIEW'):
        if source_id in _DRAW_HANDLER_REGISTRY:
            DrawManager.remove_handler(source_id)
        try:
            handle = bpy.types.SpaceView3D.draw_handler_add(draw_func, args, region_type, draw_event)
            _DRAW_HANDLER_REGISTRY[source_id] = (handle, region_type)
        except Exception as e:
            print(f"[DrawManager] Failed to register {source_id}: {e}")

    @staticmethod
    def remove_handler(source_id):
        if source_id not in _DRAW_HANDLER_REGISTRY:
            return
        handle, region_type = _DRAW_HANDLER_REGISTRY[source_id]
        try:
            bpy.types.SpaceView3D.draw_handler_remove(handle, region_type)
        except: pass
        del _DRAW_HANDLER_REGISTRY[source_id]

    @staticmethod
    def clear_all():
        for source_id in list(_DRAW_HANDLER_REGISTRY.keys()):
            DrawManager.remove_handler(source_id)

def is_event_over_ui(context, event):
    if context.area.type != 'VIEW_3D': return False
    for region in context.area.regions:
        if region.type == 'WINDOW': continue
        if (region.x <= event.mouse_x <= region.x + region.width) and \
           (region.y <= event.mouse_y <= region.y + region.height):
            return True
    return False


class _ViewportMouseEvent:
    """Proxy an event with coordinates relative to the modal viewport region."""

    def __init__(self, event, mouse_region_x, mouse_region_y):
        self._event = event
        self.mouse_region_x = mouse_region_x
        self.mouse_region_y = mouse_region_y

    def __getattr__(self, name):
        return getattr(self._event, name)

def is_number_input(ev):
    valid_keys = {
        'ZERO', 'ONE', 'TWO', 'THREE', 'FOUR', 'FIVE', 'SIX', 'SEVEN', 'EIGHT', 'NINE',
        'PERIOD', 'MINUS',
        'NUMPAD_0', 'NUMPAD_1', 'NUMPAD_2', 'NUMPAD_3', 'NUMPAD_4',
        'NUMPAD_5', 'NUMPAD_6', 'NUMPAD_7', 'NUMPAD_8', 'NUMPAD_9',
        'NUMPAD_PERIOD', 'NUMPAD_MINUS'
    }
    return ev.type in valid_keys and ev.value == 'PRESS'


def _orthographic_view_axis_normal(rv3d, tolerance=0.999):
    """Return the world axis normal for an axis-aligned ortho view.

    Vertex and edge snaps identify a point on the mesh, but do not identify a
    unique drawing plane.  In an exact front/right/top view the least
    surprising plane is the plane of the screen.  Keep this helper limited to
    genuinely axis-aligned orthographic views so perspective and oblique views
    retain their existing surface-raycast fallback.
    """
    if rv3d is None or rv3d.view_perspective != 'ORTHO':
        return None

    view_dir = rv3d.view_matrix.inverted().to_3x3() @ Vector((0, 0, -1))
    if view_dir.length_squared <= 1.0e-12:
        return None
    view_dir.normalize()

    axes = (
        Vector((1, 0, 0)),
        Vector((0, 1, 0)),
        Vector((0, 0, 1)),
    )
    axis = max(axes, key=lambda candidate: abs(view_dir.dot(candidate)))
    alignment = view_dir.dot(axis)
    if abs(alignment) < tolerance:
        return None
    return axis if alignment >= 0.0 else -axis

def apply_custom_orbit(context, pivot, dx, dy):
    rv3d = context.region_data
    if not rv3d: return
    speed = 0.01
    view_mat = rv3d.view_matrix
    cam_mat = view_mat.inverted()
    trans_to = Matrix.Translation(pivot)
    trans_from = Matrix.Translation(-pivot)
    angle_z = -dx * speed
    rot_z = Matrix.Rotation(angle_z, 4, 'Z')
    orbit_z = trans_to @ rot_z @ trans_from
    cam_mat = orbit_z @ cam_mat
    cam_fwd = -cam_mat.col[2].xyz 
    world_up = Vector((0, 0, 1))
    if abs(cam_fwd.dot(world_up)) > 0.99: flat_right = cam_mat.col[0].xyz
    else: flat_right = cam_fwd.cross(world_up).normalized()
    angle_x = dy * speed 
    rot_x = Matrix.Rotation(angle_x, 4, flat_right)
    orbit_x = trans_to @ rot_x @ trans_from
    cam_mat = orbit_x @ cam_mat
    rv3d.view_matrix = cam_mat.inverted()

class ModalManager:
    def __init__(self, ctx, operator=None):
        self.operator = operator
        self.state = state
        self.active_tool = None
        self.region = ctx.region
        self.rv3d = ctx.region_data
        self.is_navigating = False
        self.last_mouse_x = 0
        self.last_mouse_y = 0
        
        if ctx.area.type != 'VIEW_3D' or (ctx.region and ctx.region.type != 'WINDOW'):
            for area in ctx.screen.areas:
                if area.type == 'VIEW_3D':
                    for region in area.regions:
                        if region.type == 'WINDOW':
                            self.region = region
                            self.rv3d = area.spaces.active.region_3d
                            break
                    if self.region: break

        t_mode = state.get("tool_mode", "1POINT")
        
        if t_mode == "1POINT": 
            from .operators import arc_tools
            self.active_tool = arc_tools.ArcTool_1Point(self)
        elif t_mode == "2POINT": 
            from .operators import arc_tools
            self.active_tool = arc_tools.ArcTool_2Point(self)
        elif t_mode == "3POINT": 
            from .operators import arc_tools
            self.active_tool = arc_tools.ArcTool_3Point(self)
            
        elif t_mode == "CIRCLE_1POINT":
            from .operators import circle_tools
            self.active_tool = circle_tools.CircleTool_1Point(self)
        elif t_mode == "CIRCLE_2POINT": 
            from .operators import circle_tools
            self.active_tool = circle_tools.CircleTool_2Point(self)
        elif t_mode == "CIRCLE_3POINT": 
            from .operators import circle_tools
            self.active_tool = circle_tools.CircleTool_3Point(self)
            
        elif t_mode == "CIRCLE_TAN_TAN": 
            from .operators import circle_tools
            self.active_tool = circle_tools.CircleTool_TanTan(self)

        elif t_mode == "CIRCLE_TAN_TAN_TAN":
            from .operators import op_circle_tan_tan_tan
            self.active_tool = op_circle_tan_tan_tan.CircleTool_TanTanTan(self)
            
        elif t_mode == "ELLIPSE_RADIUS": 
            from .operators import ellipse_tools
            self.active_tool = ellipse_tools.EllipseTool_FromRadius(self)
        elif t_mode == "ELLIPSE_ENDPOINTS": 
            from .operators import ellipse_tools
            self.active_tool = ellipse_tools.EllipseTool_FromEndpoints(self)
        elif t_mode == "ELLIPSE_FOCI":
            from .operators import ellipse_tools
            self.active_tool = ellipse_tools.EllipseTool_FociPoint(self)
        elif t_mode == "ELLIPSE_CORNERS": 
            from .operators import ellipse_tools
            self.active_tool = ellipse_tools.EllipseTool_FromCorners(self)
            
        elif t_mode == "POLYGON_CENTER_CORNER": 
            from .operators import polygon_tools
            self.active_tool = polygon_tools.PolygonTool_CenterCorner(self)
        elif t_mode == "POLYGON_CENTER_TANGENT": 
            from .operators import polygon_tools
            self.active_tool = polygon_tools.PolygonTool_CenterTangent(self)
        elif t_mode == "POLYGON_CORNER_CORNER": 
            from .operators import polygon_tools
            self.active_tool = polygon_tools.PolygonTool_CornerCorner(self)
        elif t_mode == "POLYGON_EDGE": 
            from .operators import polygon_tools
            self.active_tool = polygon_tools.PolygonTool_Edge(self)

        elif t_mode == "RECTANGLE_CENTER_CORNER":
            from .operators import rectangle_tools
            self.active_tool = rectangle_tools.RectangleTool_CenterCorner(self)
        elif t_mode == "RECTANGLE_CORNER_CORNER":
            from .operators import rectangle_tools
            self.active_tool = rectangle_tools.RectangleTool_CornerCorner(self)
        elif t_mode == "RECTANGLE_3_POINTS":
            from .operators import rectangle_tools
            self.active_tool = rectangle_tools.RectangleTool_3Point(self)
            
        elif t_mode == "LINE_POLY": 
            from .operators import line_tools
            self.active_tool = line_tools.LineTool_Poly(self)
        
        elif t_mode == "LINE_PERP_FROM_CURVE":
            from .operators import line_tools
            self.active_tool = line_tools.LineTool_PerpFromCurve(self)

        elif t_mode == "LINE_TAN_TAN": 
            from .operators import line_tools
            self.active_tool = line_tools.LineTool_TanTan(self)

        elif t_mode == "LINE_PERP_TO_TWO_CURVES": 
            from .operators import line_tools
            self.active_tool = line_tools.LineTool_PerpToTwoCurves(self)

        elif t_mode == "LINE_TANGENT_FROM_CURVE": 
            from .operators import line_tools
            self.active_tool = line_tools.LineTool_TangentFromCurve(self)

        elif t_mode == "CURVE_INTERPOLATE": 
            from .operators import curve_tools
            self.active_tool = curve_tools.CurveTool_Interpolate(self)
        elif t_mode == "CURVE_FREEHAND": 
            from .operators import curve_tools
            self.active_tool = curve_tools.CurveTool_Freehand(self)
            
        elif t_mode == "POINT_BY_ARCS":
            from .operators import point_tools
            self.active_tool = point_tools.PointTool_ByArcs(self)

        elif t_mode == "POINT_CENTER":
            from .operators import point_tools
            self.active_tool = point_tools.PointTool_Center(self)

        elif t_mode == "POINT_EDGE_CENTER":
            from .operators import point_tools
            self.active_tool = point_tools.PointTool_EdgeCenter(self)

        elif t_mode == "ROTATE":
            from .operators import rotate_tools
            self.active_tool = rotate_tools.RotateTool(self, ctx)
            
        else: 
            from .operators import arc_tools
            self.active_tool = arc_tools.ArcTool_1Point(self)

    def report(self, type_set, message):
        """Pass report messages to the operator if available."""
        if self.operator:
            self.operator.report(type_set, message)
        else:
            print(f"radCAD Report {type_set}: {message}")

    def viewport_mouse_coords(self, event):
        """Return window mouse coordinates in the modal viewport's space."""
        if self.region and hasattr(event, "mouse_x") and hasattr(event, "mouse_y"):
            return event.mouse_x - self.region.x, event.mouse_y - self.region.y
        return event.mouse_region_x, event.mouse_region_y

    def get_edge_center_snap_data(self, ctx, x, y):
        """Snap only to mesh edge centers for the Edge Center point tool."""
        from .snapping_utils import snap_mesh

        state["snap_point"] = None
        state["geometry_snap"] = False
        state["last_surface_hit"] = None
        state["last_surface_normal"] = None

        result = snap_mesh(
            ctx,
            ctx.edit_object,
            x,
            y,
            max_px=self.state.get("snap_strength", 6.0) * 2.0,
            snap_verts=False,
            snap_edges=False,
            snap_edge_center=True,
            snap_face_center=False,
            snap_faces=False,
            include_surface=False,
            snap_intersections=False,
        )
        if result is None or result.kind != "EDGE_CENTER":
            return None, None

        point = result.location.copy()
        state["snap_point"] = point
        state["geometry_snap"] = True
        state["last_surface_hit"] = point
        return point, None

    def get_snap_data(self, ctx, x, y):
        if state.get("tool_mode") == "POINT_EDGE_CENTER":
            return self.get_edge_center_snap_data(ctx, x, y)

        snapped_pos = None
        snapped_normal = None
        surface_result = None
        
        reg, rv3d = self.region, self.rv3d
        if not reg or not rv3d: return Vector((0,0,0)), Vector((0,0,1))
        snap_radius = self.state.get("snap_strength", 6.0) * 2.0
        mesh_snap_enabled = (
            state.get("snap_verts", True) or
            state.get("snap_edges", True) or
            state.get("snap_edge_center", True) or
            state.get("snap_face_center", True) or
            state.get("snap_faces", False) or
            state.get("snap_intersections", False)
        )

        # Freehand still gets inexpensive construction-guide snapping, while
        # retaining its existing optimization that skips the mesh snap buffer.
        try:
            from .construction_tool.model import has_visible_construction_lines
            guide_snap_available = has_visible_construction_lines(ctx.scene)
        except (AttributeError, ImportError):
            guide_snap_available = False

        if (
            (state.get("tool_mode") != "CURVE_FREEHAND" and mesh_snap_enabled)
            or guide_snap_available
        ):
            try:
                from .snapping_utils import snap_scene_geometry
            except ImportError:
                def snap_scene_geometry(*args, **kwargs): return None

            snap_result = snap_scene_geometry(
                ctx, ctx.edit_object, x, y, max_px=snap_radius,
                snap_verts=state.get("snap_verts", True),
                snap_edges=state.get("snap_edges", True),
                snap_edge_center=state.get("snap_edge_center", True),
                snap_faces=state.get("snap_faces", False),
                snap_face_center=state.get("snap_face_center", True),
                include_surface=True,
                snap_intersections=state.get("snap_intersections", False),
                enable_mesh=(
                    state.get("tool_mode") != "CURVE_FREEHAND"
                    and mesh_snap_enabled
                ),
            )
            if snap_result is not None:
                if snap_result.kind == "SURFACE":
                    surface_result = snap_result
                else:
                    snapped_pos = snap_result.location
                    snapped_normal = snap_result.normal

            # --- PREVIEW SNAPPING (SELF-SNAP) ---
            self_snap_targets = []
            if state.get("tool_mode") == "LINE_POLY":
                preview_pts = state.get("preview_pts", [])
                if len(preview_pts) > 1: self_snap_targets = preview_pts[:-1]
            elif state.get("tool_mode") == "CURVE_INTERPOLATE":
                # Snap to the full smooth curve preview (vertices and edges)
                preview_pts = state.get("preview_pts", [])
                if len(preview_pts) > 1: self_snap_targets = preview_pts[:-1]
            elif state.get("tool_mode") == "POINT_BY_ARCS":
                self_snap_targets = getattr(self.active_tool, "endpoints_1", [])

            if self_snap_targets:
                best_self_pt = None
                best_self_dist = float('inf')
                limit_sq = snap_radius * snap_radius
                mvec = Vector((x, y))

                for pt in self_snap_targets:
                    p2d = location_3d_to_region_2d(reg, rv3d, pt)
                    if p2d:
                        d2 = (mvec - p2d).length_squared
                        if d2 < limit_sq and d2 < best_self_dist:
                            best_self_dist = d2
                            best_self_pt = pt

                # For CURVE_INTERPOLATE, snap to edges/edge-centers if enabled
                if state.get("tool_mode") == "CURVE_INTERPOLATE":
                    preview_pts = state.get("preview_pts", [])
                    for i in range(len(preview_pts) - 2):
                        p0, p1 = preview_pts[i], preview_pts[i+1]
                        p0_2d = location_3d_to_region_2d(reg, rv3d, p0)
                        p1_2d = location_3d_to_region_2d(reg, rv3d, p1)
                        if p0_2d and p1_2d:
                            edge_2d = p1_2d - p0_2d
                            edge_len_sq = edge_2d.length_squared
                            if edge_len_sq > 1e-8:
                                # Snap to edge center if that button is on
                                if state.get("snap_edge_center", False):
                                    center_2d = p0_2d + edge_2d * 0.5
                                    d2 = (mvec - center_2d).length_squared
                                    if d2 < limit_sq and d2 < best_self_dist:
                                        best_self_dist = d2
                                        best_self_pt = (p0 + p1) * 0.5
                                # Snap to closest point on edge if that button is on
                                elif state.get("snap_edges", True):
                                    t = max(0, min(1, (mvec - p0_2d).dot(edge_2d) / edge_len_sq))
                                    closest_2d = p0_2d + edge_2d * t
                                    d2 = (mvec - closest_2d).length_squared
                                    if d2 < limit_sq and d2 < best_self_dist:
                                        best_self_dist = d2
                                        best_self_pt = p0 + (p1 - p0) * t

                if best_self_pt:
                    use_self = True
                    if snapped_pos:
                        p2d_mesh = location_3d_to_region_2d(reg, rv3d, snapped_pos)
                        if p2d_mesh:
                            dist_mesh = (mvec - p2d_mesh).length_squared
                            if dist_mesh <= best_self_dist:
                                use_self = False
                    if use_self:
                        snapped_pos = best_self_pt
                        snapped_normal = None

        # --- FALLBACK TO SURFACE/PLANE (STILL ACTIVE FOR FREEHAND) ---
        state["snap_point"] = None 
        if snapped_pos is not None:
            state["geometry_snap"] = True
            state["snap_point"] = snapped_pos
            state["last_surface_hit"] = snapped_pos
            locked_normal = state.get("locked_normal")
            if locked_normal and state.get("locked"):
                return snapped_pos, locked_normal
            nrm = snapped_normal
            if nrm is None:
                # A vertex/edge component has no face normal.  In an exact
                # axis-aligned ortho view, using the raycast face behind that
                # component can switch the compass to a different plane (for
                # example, a Y view can suddenly use Z and become edge-on).
                # The view plane is the stable, unambiguous fallback there.
                nrm = _orthographic_view_axis_normal(rv3d)
            if nrm is None:
                _, nrm, _ = raycast_under_mouse(ctx, x, y)
            if nrm is not None:
                state["last_surface_normal"] = nrm
            return snapped_pos, nrm if nrm else Vector((0,0,1))
        
        is_locked = state.get("locked")
        locked_normal = state.get("locked_normal")
        if is_locked and locked_normal:
            l_point = state.get("pivot") or state.get("locked_plane_point") or Vector((0,0,0))
            ray_origin = region_2d_to_origin_3d(reg, rv3d, (x,y))
            ray_vector = region_2d_to_vector_3d(reg, rv3d, (x,y))
            hit_plane = intersect_line_plane(ray_origin, ray_origin + ray_vector * 10000, l_point, locked_normal)
            if hit_plane:
                state["geometry_snap"] = False
                state["last_surface_hit"] = hit_plane
                state["last_surface_normal"] = locked_normal
                return hit_plane, locked_normal

        view_vec = region_2d_to_vector_3d(reg, rv3d, (x,y))
        ray_origin = region_2d_to_origin_3d(reg, rv3d, (x,y))

        if surface_result is not None:
            nrm = surface_result.normal if surface_result.normal is not None else Vector((0,0,1))
            state["geometry_snap"] = False
            state["last_surface_hit"] = surface_result.location
            state["last_surface_normal"] = nrm
            return surface_result.location, nrm

        if mesh_snap_enabled:
            depsgraph = ctx.evaluated_depsgraph_get()
            hit, loc, norm, _, _, _ = ctx.scene.ray_cast(depsgraph, ray_origin, view_vec)
            if hit:
                state["geometry_snap"] = False
                state["last_surface_hit"] = loc
                state["last_surface_normal"] = norm
                return loc, norm

        # --- FALLBACK: VOID DRAWING (Smart Ortho Alignment) ---
        plane_normal = Vector((0, 0, 1))
        if rv3d.view_perspective == 'ORTHO':
            view_dir = rv3d.view_matrix.inverted().to_3x3() @ Vector((0, 0, -1))
            x_align = abs(view_dir.dot(Vector((1, 0, 0))))
            y_align = abs(view_dir.dot(Vector((0, 1, 0))))
            z_align = abs(view_dir.dot(Vector((0, 0, 1))))
            limit = 0.99
            if x_align > limit: plane_normal = Vector((1, 0, 0))
            elif y_align > limit: plane_normal = Vector((0, 1, 0))
            elif z_align > limit: plane_normal = Vector((0, 0, 1))
            else: plane_normal = -view_dir

        denom = view_vec.dot(plane_normal)
        if abs(denom) > 1e-6:
            t = (Vector((0,0,0)) - ray_origin).dot(plane_normal) / denom
            gpos = ray_origin + view_vec * t
        else:
            gpos = Vector((0,0,0))
            
        state["geometry_snap"] = False
        state["last_surface_hit"] = gpos
        state["last_surface_normal"] = plane_normal
        return gpos, plane_normal

    def on_move(self, context, event):
        if self.active_tool:
            # --- FIX: One-frame bypass for numerical input ---
            if state.get("skip_mouse_update"):
                state["skip_mouse_update"] = False
                self.sync_tool_to_state()
                # Force the tool to recalculate its points from the new state
                if hasattr(self.active_tool, "refresh_preview"):
                    self.active_tool.refresh_preview()
                self.sync_tool_from_state()
                context.area.tag_redraw()
                return

            mouse_x, mouse_y = self.viewport_mouse_coords(event)
            move_event = _ViewportMouseEvent(event, mouse_x, mouse_y)

            def update_tool(update_context):
                snap_pt, snap_n = self.get_snap_data(update_context, mouse_x, mouse_y)
                self.active_tool.update(update_context, move_event, snap_pt, snap_n)

            if (
                self.region
                and hasattr(context, "temp_override")
                and context.region != self.region
            ):
                with context.temp_override(region=self.region):
                    update_tool(context)
            else:
                update_tool(context)
            self.sync_tool_from_state()
            context.area.tag_redraw()

    def sync_tool_from_state(self):
        """Copies tool properties into the shared 'state'."""
        t = self.active_tool
        state["stage"] = t.stage
        state["pivot"] = t.pivot
        state["current"] = t.current
        state["start"] = getattr(t, "start", None)
        state["p1"] = getattr(t, "p1", None)
        state["p2"] = getattr(t, "p2", None)
        state["f1"] = getattr(t, "f1", None)
        state["f2"] = getattr(t, "f2", None)
        state["midpoint"] = getattr(t, "midpoint", None)
        state["radius"] = getattr(t, "radius", 0.0)
        state["compass_rot"] = getattr(t, "compass_rot", 0.0)
        state["a0"] = getattr(t, "a0", 0.0)
        state["a1"] = getattr(t, "a1", 0.0)
        state["accum_angle"] = getattr(t, "accum_angle", 0.0)
        state["a_prev_raw"] = getattr(t, "a_prev_raw", 0.0)
        state["segments"] = getattr(t, "segments", 32)
        state["min_dist"] = getattr(t, "min_dist", 0.05)
        state["rx"] = getattr(t, "rx", 0.0)
        state["ry"] = getattr(t, "ry", 0.0)
        state["preview_pts"] = getattr(t, "preview_pts", [])
        state["intersection_pts"] = getattr(t, "intersection_pts", [])
        state["spline_geom"] = getattr(t, "spline_geom", [])
        state["Xp"] = t.Xp
        state["Yp"] = t.Yp
        state["Zp"] = t.Zp

    def sync_tool_to_state(self):
        """Copies shared 'state' values back into the tool instance."""
        t = self.active_tool
        if "radius" in state: t.radius = state["radius"]
        if "stage" in state: t.stage = state["stage"]
        if "start" in state: t.start = state["start"]
        if "p1" in state: t.p1 = state["p1"]
        if "p2" in state: t.p2 = state["p2"]
        if "f1" in state: t.f1 = state["f1"]
        if "f2" in state: t.f2 = state["f2"]
        if "rx" in state: t.rx = state["rx"]
        if "ry" in state: t.ry = state["ry"]
        if "midpoint" in state: t.midpoint = state["midpoint"]
        if "current" in state: t.current = state["current"]
        if "segments" in state: t.segments = state["segments"]
        if "min_dist" in state: t.min_dist = state["min_dist"]
        if "a0" in state: t.a0 = state["a0"]
        if "a1" in state: t.a1 = state["a1"]
        if "accum_angle" in state: t.accum_angle = state["accum_angle"]
        if "a_prev_raw" in state: t.a_prev_raw = state["a_prev_raw"]

def get_or_create_grey_material():
    mat_name = "radCAD_Grey"
    mat = bpy.data.materials.get(mat_name)
    if not mat:
        mat = bpy.data.materials.new(name=mat_name)
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        bsdf = nodes.get("Principled BSDF")
        if bsdf:
            # Use name-based access for Blender 4.0+ compatibility
            if "Base Color" in bsdf.inputs: bsdf.inputs["Base Color"].default_value = (0.5, 0.5, 0.5, 1)
            if "Roughness" in bsdf.inputs: bsdf.inputs["Roughness"].default_value = 1.0
    return mat

def get_or_create_black_material():
    mat_name = "radCAD_Black"
    mat = bpy.data.materials.get(mat_name)
    if not mat:
        mat = bpy.data.materials.new(name=mat_name)
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        bsdf = nodes.get("Principled BSDF")
        if bsdf:
            if "Base Color" in bsdf.inputs: bsdf.inputs["Base Color"].default_value = (0, 0, 0, 1)
            if "Roughness" in bsdf.inputs: bsdf.inputs["Roughness"].default_value = 1.0
    return mat

def commit_arc_to_mesh(ctx):
    from . import arc_weld_manager
    from .snapping_utils import invalidate_snap_cache

    # Determine tool name for the new object
    tool_mode = state.get("tool_mode", "CAD_Object")
    obj_name = tool_mode.replace("_", " ").title()

    obj = ctx.edit_object
    bm = bmesh.from_edit_mesh(obj.data)
    imw = obj.matrix_world.inverted()
    
    if state["tool_mode"] in ("POINT_BY_ARCS", "POINT_CENTER", "POINT_EDGE_CENTER"):
        int_pts = state.get("intersection_pts", [])
        if not int_pts: return
        bpy.ops.mesh.select_all(action='DESELECT')
        for wp in int_pts:
            v = bm.verts.new(imw @ wp)
            v.select = True
        bm.select_history.clear()
        bm.verts.ensure_lookup_table()
        bmesh.update_edit_mesh(obj.data)
        return

    pts = state["preview_pts"]
    if not pts: return

    tangent_contact_modes = {
        "CIRCLE_TAN_TAN",
        "CIRCLE_TAN_TAN_TAN",
    }
    if (
        state.get("tool_mode") in tangent_contact_modes
        and state.get("make_points_tangent", False)
    ):
        from .tangent_resampler import (
            resample_selected_curves_at_tangencies,
        )

        resampled = resample_selected_curves_at_tangencies(
            obj,
            bm,
            state.get("tan_points", []),
            state.get("tan_source_chains") or None,
        )
        if resampled:
            bmesh.update_edit_mesh(
                obj.data,
                loop_triangles=False,
                destructive=True,
            )
            bm = bmesh.from_edit_mesh(obj.data)
        else:
            print(
                "[radCAD] Make Points Tangent could not resample the "
                "selected source curves."
            )
    is_closed = abs(state["accum_angle"]) >= (2 * math.pi - 0.001)
    
    # Continuous tools that always have a "floating" mouse point at the end
    continuous_tools = ["LINE_POLY", "CURVE_INTERPOLATE"]
    
    # Shape tools that should be closed automatically
    shape_tools = ["CIRCLE_1POINT", "CIRCLE_2POINT", "CIRCLE_3POINT", "CIRCLE_TAN_TAN", "CIRCLE_TAN_TAN_TAN",
                   "ELLIPSE_RADIUS", "ELLIPSE_ENDPOINTS", "ELLIPSE_CORNERS", 
                   "POLYGON_CENTER_CORNER", "POLYGON_CENTER_TANGENT", "POLYGON_CORNER_CORNER", "POLYGON_EDGE", 
                   "RECTANGLE_CENTER_CORNER", "RECTANGLE_CORNER_CORNER", "RECTANGLE_3_POINTS"]

    # Line-to-Curve tools that should commit BOTH points without closing
    complete_line_tools = ["LINE_PERP_FROM_CURVE", "LINE_PERP_TO_TWO_CURVES", "LINE_TANGENT_FROM_CURVE", "LINE_TAN_TAN"]

    if state["tool_mode"] in shape_tools:
        is_closed = True
    
    # If it's a fixed line tool, we want to create exactly what's in preview_pts (usually 2 pts)
    elif state["tool_mode"] in complete_line_tools:
        is_closed = False
        # Do NOT discard any points for these tools
    
    elif state["tool_mode"] in continuous_tools:
        is_closed = False
        # Discard the last point if it is the "floating" mouse point.
        if len(pts) > 1 and not state.get("input_string"):
            pts = pts[:-1]

    bpy.ops.mesh.select_all(action='DESELECT')
    created_verts = []
    points_to_create = pts if not is_closed else pts[:-1] 
    
    for wp in points_to_create:
        v = bm.verts.new(imw @ wp)
        v.select = True 
        created_verts.append(v)
    created_edges = []
    for i in range(len(created_verts) - 1):
        v1, v2 = created_verts[i], created_verts[i+1]
        try: e = bm.edges.new((v1, v2))
        except ValueError: e = bm.edges.get((v1, v2))
        if e: 
            e.select = True
            created_edges.append(e)
    if is_closed and len(created_verts) > 2:
        v_last = created_verts[-1]
        v_first = created_verts[0]
        try: e = bm.edges.new((v_last, v_first))
        except ValueError: e = bm.edges.get((v_last, v_first))
        if e: 
            e.select = True
            created_edges.append(e)
    bm.select_history.clear()
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)

    # Tangify needs the completed line as its guide.  Run it after creating
    # the line but before auto-weld can join the guide to its source loops.
    if (
        state.get("tool_mode") in {
            "LINE_TANGENT_FROM_CURVE",
            "LINE_TAN_TAN",
        }
        and state.get("make_points_tangent", False)
    ):
        from .tangent_resampler import tangify_created_line

        tangified = tangify_created_line(
            obj,
            bm,
            state.get("tan_source_chains") or [],
            created_verts,
        )
        if tangified:
            bmesh.update_edit_mesh(
                obj.data,
                loop_triangles=False,
                destructive=False,
            )
        else:
            print(
                "[radCAD] Make Points Tangent could not tangify the "
                "completed line and its closed source curve(s)."
            )
    
    auto_weld_enabled = state.get("auto_weld", True)
    if auto_weld_enabled:
        arc_weld_manager.run(ctx, created_verts, created_edges)
        
    bpy.ops.mesh.select_all(action='DESELECT')
    bmesh.update_edit_mesh(obj.data)
    vertex_only_snap = (
        state.get("snap_verts", True)
        and not state.get("snap_edges", False)
        and not state.get("snap_edge_center", False)
        and not state.get("snap_face_center", False)
        and not state.get("snap_faces", False)
    )
    invalidate_snap_cache(allow_incremental=(not auto_weld_enabled or vertex_only_snap))

def begin_modal(self, ctx, ev):
    from .tool_previews import draw_cb_3d
    from .hud_overlay import draw_hud_2d 
    from .snapping_utils import invalidate_snap_cache

    if ctx.area.type != 'VIEW_3D' or ctx.mode != 'EDIT_MESH':
        self.report({'WARNING'}, "Run in Edit Mode on a mesh")
        return {'CANCELLED'}

    tool_icons = {
        "POINT_BY_ARCS": ("point", "point_by_arcs"),
        "POINT_CENTER": ("point", "point_center"),
        "POINT_EDGE_CENTER": ("point", "point_edge_center"),
        "LINE_POLY": ("line", "line"),
        "LINE_PERP_FROM_CURVE": ("line", "line_perpendicular_from_curve"),
        "LINE_TAN_TAN": ("line", "line_tangent_to_two_curves"),
        "LINE_PERP_TO_TWO_CURVES": ("line", "line_perpendicular_to_two_curves"),
        "LINE_TANGENT_FROM_CURVE": ("line", "line_tangent_from_curve"),
        "CURVE_INTERPOLATE": ("curve", "curve_interpolate_points"),
        "CURVE_FREEHAND": ("curve", "curve_freehand"),
        "1POINT": ("arc", "arc_1_point"),
        "2POINT": ("arc", "arc_2_point"),
        "3POINT": ("arc", "arc_3_point"),
        "CIRCLE_1POINT": ("circle", "circle_center_radius"),
        "CIRCLE_2POINT": ("circle", "circle_2_points"),
        "CIRCLE_3POINT": ("circle", "circle_3_points"),
        "CIRCLE_TAN_TAN_TAN": ("circle", "circle_tangent_to_three_curves"),
        "CIRCLE_TAN_TAN": ("circle", "circle_tangent_to_two_curves"),
        "ELLIPSE_RADIUS": ("ellipse", "ellipse_from_radius"),
        "ELLIPSE_FOCI": ("ellipse", "ellipse_foci_point"),
        "ELLIPSE_ENDPOINTS": ("ellipse", "ellipse_from_endpoints"),
        "ELLIPSE_CORNERS": ("ellipse", "ellipse_from_corners"),
        "POLYGON_CENTER_CORNER": ("polygon", "polygon_cen_cor"),
        "POLYGON_CENTER_TANGENT": ("polygon", "polygon_cen_tan"),
        "POLYGON_CORNER_CORNER": ("polygon", "polygon_cor_cor"),
        "POLYGON_EDGE": ("polygon", "polygon_size_size"),
        "RECTANGLE_CENTER_CORNER": ("rectangle", "rectangle_from_center"),
        "RECTANGLE_CORNER_CORNER": ("rectangle", "rectangle_from_corners"),
        "RECTANGLE_3_POINTS": ("rectangle", "rectangle_3_points"),
    }
    panel_icon = tool_icons.get(state.get("tool_mode"))
    if panel_icon is not None:
        panel_name, icon_name = panel_icon
        setattr(ctx.scene, f"radcad_{panel_name}_icon", icon_name)
        
    # --- CURSOR FIX: FORCE 'DEFAULT' ARROW ---
    ctx.window.cursor_modal_set('DEFAULT')
    
    DrawManager.clear_all()
    invalidate_snap_cache()
    reset_state_from_context(ctx)
    new_tool_id = f"{state['tool_mode']}_{time.time()}"
    self.tool_instance_id = new_tool_id
    ctx.scene.active_cad_tool_id = new_tool_id
    # Pass self (the operator) so ModalManager can report messages
    self.manager = ModalManager(ctx, self)
    
    DrawManager.add_handler('MAIN_3D', draw_cb_3d, (), 'WINDOW', 'POST_VIEW')
    DrawManager.add_handler('HUD_2D', draw_hud_2d, (), 'WINDOW', 'POST_PIXEL')
    
    ctx.window_manager.modal_handler_add(self)
    ctx.area.tag_redraw()
    return {'RUNNING_MODAL'}

def finish_modal(self, ctx):
    from .snapping_utils import free_snap_context

    current_id = getattr(ctx.scene, "active_cad_tool_id", "")
    if current_id == self.tool_instance_id:
        # --- RESTORE CURSOR ---
        ctx.window.cursor_modal_restore()
        DrawManager.clear_all()
        state["active"] = False
        ctx.scene.active_cad_tool_id = ""
        if state.get("tool_mode") in {
            "1POINT",
            "2POINT",
            "3POINT",
            "POINT_BY_ARCS",
            "POINT_CENTER",
            "POINT_EDGE_CENTER",
            "LINE_POLY",
            "LINE_PERP_FROM_CURVE",
            "LINE_TAN_TAN",
            "LINE_PERP_TO_TWO_CURVES",
            "LINE_TANGENT_FROM_CURVE",
            "CIRCLE_1POINT",
            "CIRCLE_2POINT",
            "CIRCLE_3POINT",
            "CIRCLE_TAN_TAN",
            "CIRCLE_TAN_TAN_TAN",
            "ELLIPSE_RADIUS",
            "ELLIPSE_FOCI",
            "ELLIPSE_ENDPOINTS",
            "ELLIPSE_CORNERS",
            "POLYGON_CENTER_CORNER",
            "POLYGON_CENTER_TANGENT",
            "POLYGON_CORNER_CORNER",
            "POLYGON_EDGE",
            "RECTANGLE_CENTER_CORNER",
            "RECTANGLE_CORNER_CORNER",
            "RECTANGLE_3_POINTS",
        }:
            tool_mode = state.get("tool_mode", "")
            if tool_mode in {"1POINT", "2POINT", "3POINT"}:
                ctx.scene.radcad_arc_icon = "arc_default"
            elif tool_mode in {"POINT_BY_ARCS", "POINT_CENTER", "POINT_EDGE_CENTER"}:
                ctx.scene.radcad_point_icon = "point_default"
            elif tool_mode.startswith("LINE_"):
                ctx.scene.radcad_line_icon = "line_default"
            elif tool_mode.startswith("CIRCLE_"):
                ctx.scene.radcad_circle_icon = "circle"
            elif tool_mode.startswith("ELLIPSE_"):
                ctx.scene.radcad_ellipse_icon = "ellipse"
            elif tool_mode.startswith("RECTANGLE_"):
                ctx.scene.radcad_rectangle_icon = "rectangle_default"
            else:
                ctx.scene.radcad_polygon_icon = "polygon_default"
        free_snap_context()
    ctx.area.tag_redraw()

def modal_arc_common(self, ctx, ev):
    from .text_entry_utils import handle_text_input
    
    current_id = getattr(ctx.scene, "active_cad_tool_id", "")
    if current_id != self.tool_instance_id:
        tool = getattr(getattr(self, "manager", None), "active_tool", None)
        if tool is not None:
            tool.cancel(ctx)
        return {'CANCELLED'}

    if ev.type in {'LEFTMOUSE', 'RIGHTMOUSE', 'MOUSEMOVE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'MIDDLEMOUSE'}:
        reg = self.manager.region
        is_outside_viewport = False
        if reg:
            mx, my = self.manager.viewport_mouse_coords(ev)
            rw, rh = reg.width, reg.height
            if not (0 <= mx <= rw and 0 <= my <= rh): is_outside_viewport = True
        is_over_ui = is_event_over_ui(ctx, ev)
        
        # Keep updating the preview while the cursor crosses the sidebar.  Only
        # pass actual button/wheel events through to the UI.
        if is_outside_viewport or is_over_ui:
            if ev.type == 'MOUSEMOVE':
                self.manager.on_move(ctx, ev)
            return {'PASS_THROUGH'}

    # --- PRIORITY: Handle Text Input First ---
    if state["input_mode"] is not None:
        consumed = handle_text_input(ctx, ev)
        if consumed: 
             # If input finished (e.g. HIT ENTER), sync state back to tool
             if state["input_mode"] is None:
                 if self.manager.active_tool:
                     # Most numeric tools use a one-frame bypass after Enter
                     # to avoid reprocessing the input event. Rectangle from
                     # Center must update immediately so its locked X/Y
                     # dimension is visible without another mouse move.
                     state["skip_mouse_update"] = (
                         state.get("tool_mode") != "RECTANGLE_CENTER_CORNER"
                     )
                     # Force immediate update with fresh coordinates
                     self.manager.on_move(ctx, ev)
                     if (
                         state.get("tool_mode") == "LINE_POLY"
                         and ev.type in {'RET', 'NUMPAD_ENTER'}
                         and ev.value == 'PRESS'
                         and state.get("stage", 0) > 0
                     ):
                         if hasattr(self.manager.active_tool, "apply_typed_length_now"):
                             self.manager.active_tool.apply_typed_length_now()
                         tool_current = getattr(self.manager.active_tool, "current", None)
                         self.manager.active_tool.handle_click(ctx, ev, tool_current, state.get("locked_normal"))
                         state["stage"] = self.manager.active_tool.stage
                         self.manager.on_move(ctx, ev)
                     
             return {'RUNNING_MODAL'}

    # --- Commit / Finish ---
    if (ev.type in {'SPACE', 'RET', 'NUMPAD_ENTER'} and ev.value == 'PRESS') or (ev.type == 'RIGHTMOUSE' and ev.value == 'PRESS'):
        if ev.type == 'RIGHTMOUSE' and state.get("tool_mode") == "ROTATE":
            if self.manager.active_tool:
                self.manager.active_tool.cancel(ctx)
            finish_modal(self, ctx)
            return {'CANCELLED'}

        if self.manager.active_tool:
            if state["tool_mode"] == "LINE_POLY":
                # If we have a keyboard value active, commit it as a click first
                if state.get("input_string"):
                    mx, my = ev.mouse_region_x, ev.mouse_region_y
                    snap_pt, snap_n = self.manager.get_snap_data(ctx, mx, my)
                    self.manager.active_tool.handle_click(ctx, ev, snap_pt, snap_n)
                    self.manager.on_move(ctx, ev)
            
            elif state["tool_mode"] == "CURVE_INTERPOLATE":
                # Build final curve including current mouse pos (snapped target),
                # then append a dummy copy of the last point so commit_arc_to_mesh's
                # strip-one-from-end eats the dummy instead of the real last edge.
                tool = self.manager.active_tool
                num_segs = state.get("segments", 12)
                if hasattr(tool, "_build_all_preview"):
                    pts = tool._build_all_preview(extra_pt=tool.current, num_segs=num_segs)
                    if pts:
                        state["preview_pts"] = pts + [pts[-1]]
                elif hasattr(tool, "control_points"):
                    from .operators.curve_tools import solve_catmull_rom_chain
                    state["preview_pts"] = solve_catmull_rom_chain(tool.control_points, num_segments=num_segs)

        if state.get("tool_mode") == "ROTATE":
            if self.manager.active_tool:
                self.manager.active_tool.confirm(ctx)
        else:
            commit_arc_to_mesh(ctx)
        finish_modal(self, ctx)
        return {'FINISHED'}

    if ev.type == 'ESC':
        if self.manager.active_tool:
            self.manager.active_tool.cancel(ctx)
        finish_modal(self, ctx)
        return {'CANCELLED'}

    if ev.type == 'MIDDLEMOUSE':
        if state.get("pivot") is None: return {'PASS_THROUGH'}
        if ev.shift or ev.ctrl or ev.alt: return {'PASS_THROUGH'}
        if ev.value == 'PRESS':
            self.manager.is_navigating = True
            self.manager.last_mouse_x = ev.mouse_x
            self.manager.last_mouse_y = ev.mouse_y
            return {'RUNNING_MODAL'}
        elif ev.value == 'RELEASE':
            self.manager.is_navigating = False
            return {'RUNNING_MODAL'}

    if self.manager.is_navigating and ev.type == 'MOUSEMOVE':
        if state.get("pivot"):
            dx = ev.mouse_x - self.manager.last_mouse_x
            dy = ev.mouse_y - self.manager.last_mouse_y
            apply_custom_orbit(ctx, state["pivot"], dx, dy)
            self.manager.last_mouse_x = ev.mouse_x
            self.manager.last_mouse_y = ev.mouse_y
            return {'RUNNING_MODAL'}
        else: return {'PASS_THROUGH'}

    if ev.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
        if ev.ctrl:
            delta = 1 if ev.type == 'WHEELUPMOUSE' else -1
            bpy.ops.view3d.zoom('INVOKE_DEFAULT', delta=delta, use_cursor_init=True)
            return {'RUNNING_MODAL'} 

    if ev.type == 'WHEELUPMOUSE':
        if state.get("tool_mode") not in ["POINT_BY_ARCS", "LINE_POLY", "ROTATE"]:
            step = 2 if state.get("tool_mode") == "POLYGON_EDGE" else 1
            state["segments"] = min(256, state["segments"] + step)
            if self.manager.active_tool: 
                self.manager.active_tool.segments = state["segments"]
                if hasattr(self.manager.active_tool, "refresh_preview"):
                    self.manager.active_tool.refresh_preview()
                    self.manager.sync_tool_from_state()
        ctx.area.tag_redraw()
        return {'RUNNING_MODAL'}
        
    if ev.type == 'WHEELDOWNMOUSE':
        if state.get("tool_mode") not in ["POINT_BY_ARCS", "LINE_POLY", "ROTATE"]:
            step = 2 if state.get("tool_mode") == "POLYGON_EDGE" else 1
            state["segments"] = max(1 if "CURVE" in state.get("tool_mode", "") else 3, state["segments"] - step)
            if self.manager.active_tool: 
                self.manager.active_tool.segments = state["segments"]
                if hasattr(self.manager.active_tool, "refresh_preview"):
                    self.manager.active_tool.refresh_preview()
                    self.manager.sync_tool_from_state()
        ctx.area.tag_redraw()
        return {'RUNNING_MODAL'}

    if ev.type == 'MOUSEMOVE':
        self.manager.on_move(ctx, ev)
        # Force redraw while drawing freehand to keep it smooth
        if state.get("tool_mode") == "CURVE_FREEHAND" and getattr(self.manager.active_tool, "is_drawing", False):
            ctx.area.tag_redraw()
        return {'RUNNING_MODAL'}

    if ev.value == 'PRESS':
        if self.manager.active_tool:
            if self.manager.active_tool.handle_input(ctx, ev):
                self.manager.on_move(ctx, ev)
                return {'RUNNING_MODAL'}
        
        if ev.type == 'F1': state["snap_verts"] = not state.get("snap_verts", True); ctx.area.tag_redraw(); return {'RUNNING_MODAL'}
        if ev.type == 'F2': state["snap_edges"] = not state.get("snap_edges", False); ctx.area.tag_redraw(); return {'RUNNING_MODAL'}
        if ev.type == 'F3': state["snap_edge_center"] = not state.get("snap_edge_center", False); ctx.area.tag_redraw(); return {'RUNNING_MODAL'}
        if ev.type == 'F4': state["snap_face_center"] = not state.get("snap_face_center", False); ctx.area.tag_redraw(); return {'RUNNING_MODAL'}
        if ev.type == 'F5': state["snap_faces"] = not state.get("snap_faces", False); ctx.area.tag_redraw(); return {'RUNNING_MODAL'}
        if ev.type == 'F6': state["snap_intersections"] = not state.get("snap_intersections", False); ctx.area.tag_redraw(); return {'RUNNING_MODAL'}
        if ev.type == 'C': state["use_angle_snap"] = not state.get("use_angle_snap", True); ctx.area.tag_redraw(); return {'RUNNING_MODAL'}
        if ev.type == 'W' and state.get("tool_mode") != "ROTATE": state["auto_weld"] = not state.get("auto_weld", True); ctx.area.tag_redraw(); return {'RUNNING_MODAL'}
        if (
            ev.type == 'T'
            and state.get("tool_mode") in {
                "CIRCLE_TAN_TAN",
                "CIRCLE_TAN_TAN_TAN",
                "LINE_TANGENT_FROM_CURVE",
                "LINE_TAN_TAN",
            }
        ):
            state["make_points_tangent"] = not state.get(
                "make_points_tangent",
                False,
            )
            if (
                self.manager.active_tool
                and hasattr(self.manager.active_tool, "refresh_preview")
            ):
                self.manager.active_tool.refresh_preview()
                self.manager.sync_tool_from_state()
            ctx.area.tag_redraw()
            return {'RUNNING_MODAL'}
        
        if ev.type == 'L':
            if state.get("locked"):
                state["locked"] = False
                state["locked_normal"] = None
                self.report({'INFO'}, "Unlocked")
            else:
                n = state.get("last_surface_normal")
                if n:
                    state["locked"] = True
                    state["locked_normal"] = n
                    self.report({'INFO'}, "Locked to Normal")
                else:
                    self.report({'WARNING'}, "No Normal to Lock To")
            ctx.area.tag_redraw()
            return {'RUNNING_MODAL'}

        target_mode = None
        tool_mode = state.get("tool_mode", "1POINT")
        
        if ev.type == 'S' and tool_mode != "ROTATE": target_mode = 'SEGMENTS'
        elif ev.type == 'M' and tool_mode == "CURVE_FREEHAND": target_mode = 'MIN_DIST'
        elif ev.type in {'X', 'Y'} and tool_mode == "RECTANGLE_CENTER_CORNER" and state["stage"] == 1:
            target_mode = f"RECTANGLE_{ev.type}"
        elif ev.type == 'R' and tool_mode not in ["ELLIPSE_CORNERS", "ROTATE", "RECTANGLE_CENTER_CORNER"]:
            if tool_mode != "ELLIPSE_FOCI" or state["stage"] == 1:
                target_mode = 'RADIUS'; state["input_target"] = 'RADIUS'
        elif ev.type == 'D' and tool_mode in ["2POINT", "CIRCLE_2POINT", "ELLIPSE_ENDPOINTS", "ELLIPSE_RADIUS"]: target_mode = 'RADIUS'; state["input_target"] = 'DIAMETER'
        elif ev.type == 'H' and tool_mode == "2POINT" and state["stage"] == 2: target_mode = 'RADIUS'; state["input_target"] = 'SAGITTA'
        elif ev.type == 'A' and tool_mode == "POLYGON_CENTER_TANGENT": target_mode = 'RADIUS'; state["input_target"] = 'RADIUS'
        elif ev.type == 'L' and tool_mode in ["POLYGON_CORNER_CORNER", "POLYGON_EDGE", "LINE_POLY"]: target_mode = 'RADIUS'; state["input_target"] = 'RADIUS'
        elif ev.type == 'A' and state["stage"] == 2 and tool_mode not in ["2POINT", "CIRCLE_TAN_TAN_TAN", "LINE_POLY", "ELLIPSE_CORNERS", "ELLIPSE_ENDPOINTS", "ELLIPSE_FOCI"]:
            target_mode = 'ANGLE'        
        if is_number_input(ev): 
            # --- FIX: Context-aware number typing ---
            is_angle_stage = False
            if tool_mode in ["1POINT", "ROTATE"] and state["stage"] == 2:
                is_angle_stage = True
            elif tool_mode == "POINT_BY_ARCS" and state["stage"] in [2, 5]:
                is_angle_stage = True
                
            if is_angle_stage:
                target_mode = 'ANGLE'
            elif (
                tool_mode == "RECTANGLE_CENTER_CORNER"
                and state["stage"] == 1
                and state.get("rectangle_square_locked", False)
            ):
                target_mode = 'RECTANGLE_SQUARE'
            elif tool_mode == "CURVE_FREEHAND":
                target_mode = 'MIN_DIST'
            elif tool_mode not in ["ELLIPSE_CORNERS", "ROTATE", "RECTANGLE_CENTER_CORNER"]:
                if tool_mode != "ELLIPSE_FOCI" or state["stage"] == 1:
                    target_mode = 'RADIUS' # Covers 2POINT Sagitta automatically as it's in Stage 2 but not an angle stage
                    if tool_mode == "2POINT" and state["stage"] == 2: state["input_target"] = 'SAGITTA'
                    elif tool_mode in ["CIRCLE_2POINT", "ELLIPSE_RADIUS", "ELLIPSE_ENDPOINTS"] and state["stage"] == 1: state["input_target"] = 'DIAMETER'
                    else: state["input_target"] = 'RADIUS'
            
        if target_mode:
            if self.manager.region and self.manager.rv3d and state["pivot"]:
                p2d = location_3d_to_region_2d(self.manager.region, self.manager.rv3d, state["pivot"])
                if p2d:
                    if state["input_mode"] is None: state["input_string"] = ""; state["cursor_index"] = 0
                    state["input_screen_pos"] = (p2d.x + 25, p2d.y + 10)
                    state["input_mode"] = target_mode
                    if is_number_input(ev): handle_text_input(ctx, ev)
                    ctx.area.tag_redraw()
                    return {'RUNNING_MODAL'}

        if ev.type == 'LEFTMOUSE' or ev.type in {'RET', 'NUMPAD_ENTER'}:
            mx, my = ev.mouse_region_x, ev.mouse_region_y
            clicked_ui_id = None
            for k, v in state["ui_hitboxes"].items():
                xmin, xmax, ymin, ymax = v
                if xmin <= mx <= xmax and ymin <= my <= ymax:
                    clicked_ui_id = k
                    if k == "snap_verts": state["snap_verts"] = not state.get("snap_verts", False)
                    elif k == "snap_edges": state["snap_edges"] = not state.get("snap_edges", False)
                    elif k == "snap_edge_center": state["snap_edge_center"] = not state.get("snap_edge_center", False)
                    elif k == "snap_face_center": state["snap_face_center"] = not state.get("snap_face_center", False)
                    elif k == "snap_faces": state["snap_faces"] = not state.get("snap_faces", False)
                    elif k == "snap_intersections": state["snap_intersections"] = not state.get("snap_intersections", False)
                    elif k == "toggle_angle": state["use_angle_snap"] = not state.get("use_angle_snap", True)
                    elif k == "weld_btn": state["auto_weld"] = not state.get("auto_weld", True)
                    elif k == "snap_tangent_curve_btn":
                        state["snap_tangent_curve"] = not state.get(
                            "snap_tangent_curve",
                            False,
                        )
                        state["snap_point"] = None
                        state["geometry_snap"] = False
                    elif k == "make_points_tangent_btn":
                        state["make_points_tangent"] = not state.get(
                            "make_points_tangent",
                            False,
                        )
                        if (
                            self.manager.active_tool
                            and hasattr(
                                self.manager.active_tool,
                                "refresh_preview",
                            )
                        ):
                            self.manager.active_tool.refresh_preview()
                            self.manager.sync_tool_from_state()
                    ctx.area.tag_redraw(); return {'RUNNING_MODAL'}
            
            if self.manager.active_tool:
                 snap_pt, snap_n = self.manager.get_snap_data(ctx, mx, my)
                 result = self.manager.active_tool.handle_click(ctx, ev, snap_pt, snap_n, button_id=clicked_ui_id)
                 state["stage"] = self.manager.active_tool.stage
                 if result == 'FINISHED':
                     if state.get("tool_mode") != "ROTATE":
                         commit_arc_to_mesh(ctx)
                     finish_modal(self, ctx)
                     return {'FINISHED'}
                 elif result == 'NEXT_STAGE':
                     self.manager.on_move(ctx, ev)
                     ctx.area.tag_redraw()
                     return {'RUNNING_MODAL'}

    elif ev.type == 'RIGHTMOUSE':
        finish_modal(self, ctx)
        return {'CANCELLED'}

    return {'RUNNING_MODAL'}

class VIEW3D_OT_radcad_modal(bpy.types.Operator):
    bl_idname = "view3d.radcad_modal"
    bl_label = "CAD Drawing Modal"
    bl_options = {'REGISTER', 'UNDO'}

    def modal(self, context, event):
        return modal_arc_common(self, context, event)

    def invoke(self, context, event):
        return begin_modal(self, context, event)

    def cancel(self, context):
        manager = getattr(self, "manager", None)
        tool = getattr(manager, "active_tool", None)
        if tool is not None:
            tool.cancel(context)
        finish_modal(self, context)
