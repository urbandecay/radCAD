"""Translation of the selected edit-mesh geometry for the Move tool."""

import bmesh
from bpy_extras.view3d_utils import region_2d_to_location_3d
from mathutils import Vector

from ..snapping_utils import invalidate_snap_cache
from ..units_utils import parse_length_input
from .base_tool import CAD_BaseTool


_AXES = {
    "X": Vector((1.0, 0.0, 0.0)),
    "Y": Vector((0.0, 1.0, 0.0)),
    "Z": Vector((0.0, 0.0, 1.0)),
}

_SHIFT_KEYS = {"LEFT_SHIFT", "RIGHT_SHIFT", "SHIFT"}


def _unit_or_none(value):
    if value is None:
        return None
    vector = Vector(value)
    if vector.length_squared <= 1.0e-12:
        return None
    return vector.normalized()


def _parse_signed_length(value):
    """Parse a distance while preserving a leading sign.

    ``parse_length_input`` intentionally normalizes some imperial shorthand by
    replacing hyphens with spaces.  Move needs the sign because a negative
    typed distance should move in the opposite direction.
    """
    text = str(value).strip()
    if not text:
        return None

    sign = 1.0
    if text[0] in {"-", "+"}:
        if text[0] == "-":
            sign = -1.0
        text = text[1:].strip()
    if not text:
        return None

    try:
        return sign * parse_length_input(text)
    except (TypeError, ValueError):
        return None


class MoveTool(CAD_BaseTool):
    """Move selected vertices/edges/faces with a live, reversible preview."""

    def __init__(self, core, context):
        super().__init__(core)
        self.mode = "MOVE"
        # Stage 0 picks the move-from/base point.  Stage 1 previews and
        # confirms the translation from that point.
        self.stage = 0
        self.current = None
        self.pivot = None
        # Blender's Translate operator stores the mouse position at the
        # beginning of the transform and applies a view-relative delta from
        # that reference.  Move has an explicit base-point click, so this is
        # captured by that first click instead of by operator invocation.
        self.reference_screen = None
        self.preview_pts = []

        self.Xp = Vector((1.0, 0.0, 0.0))
        self.Yp = Vector((0.0, 1.0, 0.0))
        self.Zp = Vector((0.0, 0.0, 1.0))
        self.move_plane_normal = None
        self.constraint_axis = None
        self.edge_lock_direction = None
        self.last_move_direction = None
        self.last_free_target = None
        self.move_delta = Vector((0.0, 0.0, 0.0))
        self.radius = 0.0
        self.move_distance = 0.0
        self.move_distance_active = False
        self.committed = False
        self.selection = []
        self.connected_edge_candidates = []
        self.selection_median = None

        self._capture_selection(context)
        self._sync_state()

    def _capture_selection(self, context):
        objects = getattr(context, "objects_in_mode_unique_data", ())
        if not objects and context.edit_object is not None:
            objects = (context.edit_object,)

        world_positions = []
        for obj in objects:
            if obj.type != "MESH" or not obj.data.is_editmode:
                continue

            bm = bmesh.from_edit_mesh(obj.data)
            selected = {
                vert
                for vert in bm.verts
                if vert.select and not vert.hide
            }
            selected_face_vertices = set()
            for edge in bm.edges:
                if edge.select and not edge.hide:
                    selected.update(vert for vert in edge.verts if not vert.hide)
            for face in bm.faces:
                if face.select and not face.hide:
                    face_vertices = {vert for vert in face.verts if not vert.hide}
                    selected.update(face_vertices)
                    selected_face_vertices.update(face_vertices)

            if not selected:
                continue

            matrix_world = obj.matrix_world.copy()
            # Shift is based on the selected face's local mesh edges, rather
            # than whichever edge happens to be nearest the mouse cursor.
            # Include both face edges and edges leaving the face: depending
            # on which side of a rail is selected, its lengthwise edges can
            # be either on the face or just beyond its boundary.
            direction_vertices = selected_face_vertices or selected
            for edge in bm.edges:
                if edge.hide or any(vert.hide for vert in edge.verts):
                    continue
                first, second = edge.verts
                touches_selected_face = (
                    first in direction_vertices or second in direction_vertices
                )
                if selected_face_vertices and not touches_selected_face:
                    continue
                world_edge = (matrix_world @ second.co) - (matrix_world @ first.co)
                direction = _unit_or_none(world_edge)
                if direction is not None:
                    self.connected_edge_candidates.append((direction, world_edge.length))
            positions = tuple(
                (vert, vert.co.copy())
                for vert in selected
            )
            self.selection.append(
                {
                    "object": obj,
                    "bmesh": bm,
                    "matrix_world": matrix_world,
                    "matrix_world_inverse": matrix_world.inverted_safe(),
                    "positions": positions,
                }
            )
            world_positions.extend(
                matrix_world @ original_local
                for _vert, original_local in positions
            )

        if world_positions:
            self.selection_median = sum(
                world_positions,
                Vector((0.0, 0.0, 0.0)),
            )
            self.selection_median /= len(world_positions)

    @property
    def has_selection(self):
        return bool(self.selection)

    def _connected_edge_axis(self):
        """Resolve the dominant local edge axis around the selected face."""
        if not self.connected_edge_candidates:
            return None
        clusters = []
        for direction, length in self.connected_edge_candidates:
            cluster = next(
                (
                    item
                    for item in clusters
                    if abs(item["axis"].dot(direction)) > 1.0 - 1.0e-4
                ),
                None,
            )
            if cluster is None:
                clusters.append(
                    {
                        "axis": direction.copy(),
                        "total_length": length,
                        "longest_edge": length,
                    }
                )
            else:
                cluster["total_length"] += length
                cluster["longest_edge"] = max(cluster["longest_edge"], length)

        if not clusters:
            return None
        dominant = max(
            clusters,
            key=lambda item: (item["longest_edge"], item["total_length"]),
        )
        return dominant["axis"].copy()

    def _sync_state(self):
        self.state["stage"] = self.stage
        self.state["pivot"] = self.pivot.copy() if self.pivot is not None else None
        self.state["current"] = self.current.copy() if self.current is not None else None
        self.state["preview_pts"] = [point.copy() for point in self.preview_pts]
        self.state["Xp"] = self.Xp
        self.state["Yp"] = self.Yp
        self.state["Zp"] = self.Zp
        self.state["move_delta"] = self.move_delta.copy()
        self.state["radius"] = self.radius
        self.state["move_distance"] = self.move_distance
        self.state["move_distance_active"] = self.move_distance_active
        self.state["constraint_axis"] = (
            self.constraint_axis.copy() if self.constraint_axis is not None else None
        )
        self.state["move_edge_direction"] = (
            self.edge_lock_direction.copy()
            if self.edge_lock_direction is not None
            else None
        )

    def _set_plane_from_normal(self, normal, context):
        candidate = _unit_or_none(normal)
        if candidate is None:
            rv3d = getattr(context, "region_data", None)
            if rv3d is not None:
                candidate = _unit_or_none(
                    rv3d.view_matrix.inverted().to_3x3() @ Vector((0.0, 0.0, -1.0))
                )
        if candidate is None:
            candidate = Vector((0.0, 0.0, 1.0))

        self.move_plane_normal = candidate
        self.Xp, self.Yp, self.Zp = self._basis_from_normal(candidate)

    @staticmethod
    def _basis_from_normal(normal):
        normal = normal.normalized()
        helper = Vector((0.0, 0.0, 1.0))
        if abs(normal.dot(helper)) > 0.95:
            helper = Vector((0.0, 1.0, 0.0))
        x_axis = helper.cross(normal).normalized()
        y_axis = normal.cross(x_axis).normalized()
        return x_axis, y_axis, normal

    def _event_region_coords(self, context, event):
        """Return the event position in the active modal region's space."""
        viewport_coords = getattr(self.core, "viewport_mouse_coords", None)
        if callable(viewport_coords):
            try:
                x, y = viewport_coords(event)
                return (float(x), float(y))
            except (AttributeError, TypeError):
                pass

        region_x = getattr(event, "mouse_region_x", None)
        region_y = getattr(event, "mouse_region_y", None)
        if region_x is not None and region_y is not None:
            return (float(region_x), float(region_y))

        region = getattr(context, "region", None)
        mouse_x = getattr(event, "mouse_x", None)
        mouse_y = getattr(event, "mouse_y", None)
        if region is not None and mouse_x is not None and mouse_y is not None:
            return (
                float(mouse_x - region.x),
                float(mouse_y - region.y),
            )
        return None

    def _native_view_delta(self, context, event):
        """Convert mouse motion to a world delta at the base point's depth.

        This is the Python-side equivalent of Blender's INPUT_VECTOR path:
        the initial mouse position is retained, the current position is
        converted in view space, and the resulting vector is constrained
        afterward.  It deliberately does not intersect the cursor ray with
        the world floor or with a drawing plane.
        """
        zero = Vector((0.0, 0.0, 0.0))
        if self.pivot is None or self.reference_screen is None:
            return zero

        region = getattr(context, "region", None)
        rv3d = getattr(context, "region_data", None)
        current_screen = self._event_region_coords(context, event)
        if region is None or rv3d is None or current_screen is None:
            return zero

        # The explicit base point is the equivalent of the depth used by
        # Blender's transform conversion.  Using it also guarantees that the
        # first post-click mouse sample has a zero translation.
        depth_point = self.pivot
        current_point = region_2d_to_location_3d(
            region,
            rv3d,
            current_screen,
            depth_point,
        )
        reference_point = region_2d_to_location_3d(
            region,
            rv3d,
            self.reference_screen,
            depth_point,
        )
        if current_point is None or reference_point is None:
            return zero
        return current_point - reference_point

    def _typed_distance(self):
        if self.state.get("input_mode") == "MOVE_DISTANCE":
            return _parse_signed_length(self.state.get("input_string", ""))
        if self.state.get("move_distance_active"):
            self.move_distance_active = True
            self.move_distance = self.state.get("move_distance", 0.0)
            return self.move_distance
        if self.move_distance_active:
            return self.move_distance
        return None

    def _apply_translation(self, delta):
        for item in self.selection:
            matrix_world = item["matrix_world"]
            matrix_world_inverse = item["matrix_world_inverse"]
            bm = item["bmesh"]
            for vert, original_local in item["positions"]:
                if not vert.is_valid:
                    continue
                original_world = matrix_world @ original_local
                vert.co = matrix_world_inverse @ (original_world + delta)
            bm.normal_update()
            bmesh.update_edit_mesh(
                item["object"].data,
                loop_triangles=False,
                destructive=False,
            )

    def update(self, context, event, snap_point, snap_normal):
        # While waiting for the base point, follow the same live plane choice
        # as the line tool.  Once the base is picked, keep that plane fixed.
        if self.stage == 0 or self.move_plane_normal is None:
            self._set_plane_from_normal(snap_normal, context)

        if self.stage == 0:
            self.current = snap_point.copy() if snap_point is not None else None
            self.preview_pts = []
            preview_axis = self.constraint_axis or self.edge_lock_direction
            self.state["current_axis_vector"] = (
                preview_axis.copy() if preview_axis is not None else None
            )
            self._sync_state()
            return

        if self.pivot is None:
            return

        self.state["move_edge_direction"] = (
            self.edge_lock_direction.copy()
            if self.edge_lock_direction is not None
            else None
        )

        use_snap_target = bool(
            snap_point is not None
            and (
                self.state.get("geometry_snap", False)
                or self.state.get("move_floor_snap", False)
            )
        )
        if use_snap_target:
            free_delta = snap_point.copy() - self.pivot
        else:
            free_delta = self._native_view_delta(context, event)
        self.last_free_target = self.pivot + free_delta

        axis = self.constraint_axis
        if axis is None and self.edge_lock_direction is not None:
            axis = self.edge_lock_direction

        if axis is not None:
            axis = axis.normalized()
            scalar = free_delta.dot(axis)
            self.state["current_axis_vector"] = axis.copy()
            if abs(scalar) > 1.0e-10:
                self.last_move_direction = axis.copy() * (1.0 if scalar >= 0.0 else -1.0)
            else:
                self.last_move_direction = axis.copy()
        else:
            self.state["current_axis_vector"] = None
            if free_delta.length_squared > 1.0e-12:
                self.last_move_direction = free_delta.normalized()
            scalar = None

        typed_distance = self._typed_distance()
        if typed_distance is not None:
            if axis is not None:
                delta = axis * typed_distance
            else:
                direction = self.last_move_direction
                if direction is None:
                    direction = self.Xp if self.Xp is not None else Vector((1.0, 0.0, 0.0))
                delta = direction.normalized() * typed_distance
        elif axis is not None:
            delta = axis * scalar
        else:
            delta = free_delta

        self.move_delta = delta.copy()
        self.radius = delta.length
        self.current = self.pivot + delta
        self.preview_pts = [self.pivot.copy(), self.current.copy()]
        self._apply_translation(delta)
        invalidate_snap_cache()
        self._sync_state()

    def refresh_preview(self):
        """Reapply a typed distance after the shared input overlay commits."""
        if self.pivot is None:
            return
        if self.move_distance_active:
            direction = (
                self.last_move_direction
                if self.last_move_direction is not None
                else self.Xp
            )
            if direction is not None and direction.length_squared > 1.0e-12:
                self.move_delta = direction.normalized() * self.move_distance
                self.radius = self.move_delta.length
                self.current = self.pivot + self.move_delta
                self.preview_pts = [self.pivot.copy(), self.current.copy()]
                self._apply_translation(self.move_delta)
                invalidate_snap_cache()
                self._sync_state()

    def handle_click(
        self,
        context,
        event,
        snap_point,
        snap_normal,
        button_id=None,
    ):
        del button_id
        if self.stage == 0:
            # The shared snap query supplies either a geometry snap or the
            # current drawing-plane point.  Both are valid base points.
            if snap_point is None:
                return None
            if self.move_plane_normal is None:
                self._set_plane_from_normal(snap_normal, context)
            if not self.state.get("move_edge_lock_active", False):
                self.edge_lock_direction = None
            self.pivot = snap_point.copy()
            self.reference_screen = self._event_region_coords(context, event)
            self.current = self.pivot.copy()
            self.move_delta = Vector((0.0, 0.0, 0.0))
            self.radius = 0.0
            self.last_move_direction = None
            self.stage = 1
            self._sync_state()
            return "NEXT_STAGE"

        if not self.committed:
            # Refresh from the click event so the final snap is not one mouse
            # event behind when the user confirms immediately after moving.
            self.update(context, event, snap_point, snap_normal)
        self.confirm(context)
        return "FINISHED"

    def handle_input(self, context, event):
        if event.value != "PRESS":
            return False

        if event.type in _SHIFT_KEYS:
            if getattr(event, "is_repeat", False):
                return True
            if self.state.get("move_edge_lock_active", False):
                self.state["move_edge_lock_active"] = False
                self.edge_lock_direction = None
                self.state["current_axis_vector"] = None
                self._sync_state()
                return True

            edge_direction = self._connected_edge_axis()
            if edge_direction is None:
                self.state["move_edge_lock_active"] = False
                self.core.report(
                    {"INFO"},
                    "Connected edges do not define one direction; select an end face with parallel connecting edges",
                )
            else:
                self.state["move_edge_lock_active"] = True
                self.edge_lock_direction = edge_direction
                self.constraint_axis = None
                self.state["constraint_axis"] = None
            self._sync_state()
            return True

        if event.type in _AXES:
            self.edge_lock_direction = None
            self.state["move_edge_lock_active"] = False
            self.state["move_edge_direction"] = None
            axis = _AXES[event.type]
            if self.constraint_axis is not None and self.constraint_axis == axis:
                self.constraint_axis = None
                self.state["constraint_axis"] = None
                self.state["current_axis_vector"] = None
            else:
                self.constraint_axis = axis.copy()
                self.state["constraint_axis"] = axis.copy()
                self.state["current_axis_vector"] = axis.copy()
            return True

        if event.type == "L":
            if self.stage == 0:
                self.core.report({"INFO"}, "Pick a base point before entering a distance")
                return True
            self.move_distance_active = False
            self.state["move_distance_active"] = False
            self.state["input_string"] = ""
            self.state["cursor_index"] = 0
            self.state["input_target"] = "MOVE_DISTANCE"
            self.state["input_mode"] = "MOVE_DISTANCE"
            return True

        return False

    def confirm(self, context):
        if self.committed:
            return
        self._apply_translation(self.move_delta)
        self.committed = True
        invalidate_snap_cache()
        if context.area is not None:
            context.area.tag_redraw()

    def cancel(self, context):
        if self.committed:
            return
        for item in self.selection:
            bm = item["bmesh"]
            for vert, original_local in item["positions"]:
                if vert.is_valid:
                    vert.co = original_local
            bm.normal_update()
            bmesh.update_edit_mesh(
                item["object"].data,
                loop_triangles=False,
                destructive=False,
            )
        invalidate_snap_cache()
        if context.area is not None:
            context.area.tag_redraw()
