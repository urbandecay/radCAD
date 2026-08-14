"""Compass-driven rotation of selected edit-mesh geometry."""

import bmesh
from mathutils import Matrix

from ..snapping_utils import invalidate_snap_cache
from .arc_tools import ArcTool_1Point


class RotateTool(ArcTool_1Point):
    """Reuse the 1-point arc interaction to rotate the current selection."""

    def __init__(self, core, context):
        super().__init__(core)
        self.selection = []
        self.committed = False

        objects = getattr(context, "objects_in_mode_unique_data", ())
        if not objects and context.edit_object is not None:
            objects = (context.edit_object,)

        for obj in objects:
            if obj.type != "MESH" or not obj.data.is_editmode:
                continue
            bm = bmesh.from_edit_mesh(obj.data)
            selected = {vert for vert in bm.verts if vert.select and not vert.hide}
            for edge in bm.edges:
                if edge.select and not edge.hide:
                    selected.update(vert for vert in edge.verts if not vert.hide)
            for face in bm.faces:
                if face.select and not face.hide:
                    selected.update(vert for vert in face.verts if not vert.hide)
            if selected:
                self.selection.append(
                    {
                        "object": obj,
                        "bmesh": bm,
                        "positions": tuple(
                            (vert, vert.co.copy())
                            for vert in selected
                        ),
                    }
                )

    @property
    def has_selection(self):
        return bool(self.selection)

    def _apply_rotation(self, angle):
        if self.pivot is None or self.Zp is None:
            return

        axis = self.Zp.normalized()
        rotation = Matrix.Rotation(angle, 3, axis)
        for item in self.selection:
            obj = item["object"]
            bm = item["bmesh"]
            matrix_world = obj.matrix_world
            matrix_world_inverse = matrix_world.inverted_safe()
            for vert, original_local in item["positions"]:
                if not vert.is_valid:
                    continue
                original_world = matrix_world @ original_local
                rotated_world = self.pivot + rotation @ (
                    original_world - self.pivot
                )
                vert.co = matrix_world_inverse @ rotated_world
            bm.normal_update()
            bmesh.update_edit_mesh(
                obj.data,
                loop_triangles=False,
                destructive=False,
            )

    def update(self, context, event, snap_point, snap_normal):
        super().update(context, event, snap_point, snap_normal)
        if self.stage == 2:
            self._apply_rotation(self.accum_angle)

    def refresh_preview(self):
        super().refresh_preview()
        if self.stage == 2:
            self._apply_rotation(self.accum_angle)

    def handle_click(
        self,
        context,
        event,
        snap_point,
        snap_normal,
        button_id=None,
    ):
        previous_stage = self.stage
        if previous_stage == 2:
            self.update(context, event, snap_point, snap_normal)
        result = super().handle_click(
            context,
            event,
            snap_point,
            snap_normal,
            button_id=button_id,
        )

        if previous_stage == 1 and self.stage == 2 and self.radius <= 1.0e-9:
            self.stage = 1
            self.core.report(
                {"WARNING"},
                "Place the reference point away from the pivot",
            )
            return None

        if result == "FINISHED":
            self.confirm(context)
        return result

    def confirm(self, context):
        if self.committed:
            return
        self._apply_rotation(self.accum_angle)
        self.committed = True
        invalidate_snap_cache()
        if context.area is not None:
            context.area.tag_redraw()

    def cancel(self, context):
        if self.committed:
            return
        for item in self.selection:
            obj = item["object"]
            bm = item["bmesh"]
            for vert, original_local in item["positions"]:
                if vert.is_valid:
                    vert.co = original_local
            bm.normal_update()
            bmesh.update_edit_mesh(
                obj.data,
                loop_triangles=False,
                destructive=False,
            )
        invalidate_snap_cache()
        if context.area is not None:
            context.area.tag_redraw()
