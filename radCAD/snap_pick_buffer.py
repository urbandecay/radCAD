# snap_pick_buffer.py

import math
import time

import bmesh
import gpu
from mathutils import Vector
from mathutils.geometry import intersect_line_line, intersect_point_line
from bpy_extras.view3d_utils import (
    location_3d_to_region_2d,
    region_2d_to_origin_3d,
    region_2d_to_vector_3d,
)

try:
    import numpy as np
except Exception:
    np = None


def closest_world_point_on_edge_under_cursor(ctx, x, y, p0, p1, fallback_factor=0.0):
    """Return the point on a world-space edge that projects closest to the cursor."""
    edge = p1 - p0
    edge_len_sq = edge.length_squared
    if edge_len_sq <= 1e-12:
        return p0.copy()

    ray_origin = region_2d_to_origin_3d(ctx.region, ctx.region_data, (x, y))
    ray_vector = region_2d_to_vector_3d(ctx.region, ctx.region_data, (x, y))
    closest = intersect_line_line(ray_origin, ray_origin + ray_vector, p0, p1)
    if closest is None:
        return p0.lerp(p1, max(0.0, min(1.0, fallback_factor)))

    factor = (closest[1] - p0).dot(edge) / edge_len_sq
    return p0.lerp(p1, max(0.0, min(1.0, factor)))


class _SnapOffscreen:
    def __init__(self, width, height):
        self.width = int(max(1, width))
        self.height = int(max(1, height))
        self._fbo = None
        self._tex_color = None
        self._tex_depth = None
        self._configure()

    def _configure(self):
        self.free()
        self._tex_color = gpu.types.GPUTexture((self.width, self.height), format='R32UI')
        self._tex_depth = gpu.types.GPUTexture((self.width, self.height), format='DEPTH_COMPONENT32F')
        self._fbo = gpu.types.GPUFrameBuffer(
            depth_slot=self._tex_depth,
            color_slots=self._tex_color,
        )

    def resize(self, width, height):
        width = int(max(1, width))
        height = int(max(1, height))
        if width == self.width and height == self.height:
            return
        self.width = width
        self.height = height
        self._configure()

    def bind(self):
        return self._fbo.bind()

    def clear(self):
        self._tex_color.clear(format='UINT', value=(0,))
        self._tex_depth.clear(format='FLOAT', value=(1.0,))

    def read(self):
        return self._tex_color.read()

    def free(self):
        self._fbo = None
        self._tex_color = None
        self._tex_depth = None


class _GpuSnapMesh:
    shader = None
    depth_shader = None

    def __init__(self, obj, bm, do_verts, do_edges, do_edge_center, do_face_center):
        self.vert_coords = []
        self.edge_indices = []
        self.face_tri_indices = []
        self.edge_centers = []
        self.face_centers = []
        self.batch_faces_depth = None
        self.batch_verts = None
        self.batch_edges = None
        self.batch_edge_centers = None
        self.batch_face_centers = None
        self.first_vert = 0
        self.first_edge = 0
        self.first_edge_center = 0
        self.first_face_center = 0
        self.do_verts = bool(do_verts)
        self.do_edges = bool(do_edges)
        self.do_edge_center = bool(do_edge_center)
        self.do_face_center = bool(do_face_center)
        self.source_vert_count = 0
        self.source_edge_count = 0
        self.source_face_count = 0
        self.uses_direct_bmesh_indices = False

        self._build_arrays(bm, do_verts, do_edges, do_edge_center, do_face_center)
        self._store_source_counts(bm)
        self._build_batches()

    @classmethod
    def _shader(cls, depth_only=False):
        if depth_only:
            if cls.depth_shader is not None:
                return cls.depth_shader

            shader_info = gpu.types.GPUShaderCreateInfo()
            shader_info.push_constant("MAT4", "ModelViewProjectionMatrix")
            shader_info.vertex_in(0, "VEC3", "pos")
            shader_info.fragment_out(0, "UINT", "fragColor")
            shader_info.vertex_source(
                "void main()"
                "{"
                "  gl_Position = ModelViewProjectionMatrix * vec4(pos, 1.0);"
                "}"
            )
            shader_info.fragment_source("void main() { fragColor = uint(0); }")
            cls.depth_shader = gpu.shader.create_from_info(shader_info)
            return cls.depth_shader

        if cls.shader is not None:
            return cls.shader

        shader_info = gpu.types.GPUShaderCreateInfo()
        shader_info.push_constant("MAT4", "ModelViewProjectionMatrix")
        shader_info.push_constant("INT", "offset")
        shader_info.push_constant("FLOAT", "point_size")
        shader_info.vertex_in(0, "VEC3", "pos")
        shader_info.fragment_out(0, "UINT", "fragColor")
        shader_info.vertex_source(
            "void main()"
            "{"
            "  gl_Position = ModelViewProjectionMatrix * vec4(pos, 1.0);"
            "  gl_PointSize = point_size;"
            "}"
        )
        shader_info.fragment_source(
            "void main()"
            "{"
            "  fragColor = uint(gl_PrimitiveID + offset);"
            "}"
        )
        cls.shader = gpu.shader.create_from_info(shader_info)
        return cls.shader

    def _build_arrays(self, bm, do_verts, do_edges, do_edge_center, do_face_center):
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()

        has_hidden_verts = any(v.hide for v in bm.verts)
        if np is not None and not has_hidden_verts:
            self._build_arrays_numpy(bm, do_verts, do_edges, do_edge_center, do_face_center)
            self.uses_direct_bmesh_indices = True
            return

        index_map = {}
        for v in bm.verts:
            if v.hide:
                continue
            index_map[v.index] = len(self.vert_coords)
            self.vert_coords.append(tuple(v.co))

        if do_edges or do_edge_center:
            for e in bm.edges:
                if e.hide or e.verts[0].hide or e.verts[1].hide:
                    continue
                i0 = index_map.get(e.verts[0].index)
                i1 = index_map.get(e.verts[1].index)
                if i0 is None or i1 is None:
                    continue
                if do_edges:
                    self.edge_indices.append((i0, i1))
                if do_edge_center:
                    self.edge_centers.append(tuple((e.verts[0].co + e.verts[1].co) * 0.5))

        if do_face_center:
            for f in bm.faces:
                if f.hide:
                    continue
                self.face_centers.append(tuple(f.calc_center_median()))

        for tri in bm.calc_loop_triangles():
            if not tri or tri[0].face.hide:
                continue
            indices = []
            for loop in tri:
                if loop.vert.hide:
                    indices = []
                    break
                mapped = index_map.get(loop.vert.index)
                if mapped is None:
                    indices = []
                    break
                indices.append(mapped)
            if len(indices) == 3:
                self.face_tri_indices.append(tuple(indices))

        self._visible_vert_count = len(self.vert_coords) if do_verts else 0
        self.uses_direct_bmesh_indices = len(self.vert_coords) == len(bm.verts)

    def _build_arrays_numpy(self, bm, do_verts, do_edges, do_edge_center, do_face_center):
        self.vert_coords = np.array([v.co for v in bm.verts], "f4")

        if do_edges or do_edge_center:
            edge_indices = [
                (e.verts[0].index, e.verts[1].index)
                for e in bm.edges
                if not (e.hide or e.verts[0].hide or e.verts[1].hide)
            ]
            if edge_indices:
                edge_indices = np.array(edge_indices, "i4")
                if do_edges:
                    self.edge_indices = edge_indices
                if do_edge_center:
                    self.edge_centers = (
                        self.vert_coords[edge_indices[:, 0]]
                        + self.vert_coords[edge_indices[:, 1]]
                    ) * 0.5

        if do_face_center:
            centers = [f.calc_center_median() for f in bm.faces if not f.hide]
            if centers:
                self.face_centers = np.array(centers, "f4")

        if bm.faces:
            tris = []
            for tri in bm.calc_loop_triangles():
                if not tri or tri[0].face.hide:
                    continue
                if tri[0].vert.hide or tri[1].vert.hide or tri[2].vert.hide:
                    continue
                tris.append((tri[0].vert.index, tri[1].vert.index, tri[2].vert.index))
            if tris:
                self.face_tri_indices = np.array(tris, "i4")

        self._visible_vert_count = len(self.vert_coords) if do_verts else 0
        self.uses_direct_bmesh_indices = True

    def _store_source_counts(self, bm):
        self.source_vert_count = len(bm.verts)
        self.source_edge_count = len(bm.edges)
        self.source_face_count = len(bm.faces)

    def _reset_batches(self):
        self.batch_faces_depth = None
        self.batch_verts = None
        self.batch_edges = None
        self.batch_edge_centers = None
        self.batch_face_centers = None

    def _append_coords(self, coords):
        if not coords:
            return
        if np is not None and hasattr(self.vert_coords, "shape"):
            self.vert_coords = np.concatenate((self.vert_coords, np.array(coords, "f4")), axis=0)
        else:
            self.vert_coords.extend(tuple(co) for co in coords)

    def _append_edge_indices(self, indices):
        if not indices:
            return
        if np is not None and hasattr(self.edge_indices, "shape"):
            self.edge_indices = np.concatenate((self.edge_indices, np.array(indices, "i4")), axis=0)
        elif len(self.edge_indices) == 0 and np is not None and hasattr(self.vert_coords, "shape"):
            self.edge_indices = np.array(indices, "i4")
        else:
            self.edge_indices.extend(indices)

    def _append_point_array(self, attr_name, coords):
        if not coords:
            return
        current = getattr(self, attr_name)
        if np is not None and hasattr(current, "shape"):
            setattr(self, attr_name, np.concatenate((current, np.array(coords, "f4")), axis=0))
        elif len(current) == 0 and np is not None and hasattr(self.vert_coords, "shape"):
            setattr(self, attr_name, np.array(coords, "f4"))
        else:
            current.extend(tuple(co) for co in coords)

    def _append_face_tri_indices(self, indices):
        if not indices:
            return
        if np is not None and hasattr(self.face_tri_indices, "shape"):
            self.face_tri_indices = np.concatenate((self.face_tri_indices, np.array(indices, "i4")), axis=0)
        elif len(self.face_tri_indices) == 0 and np is not None and hasattr(self.vert_coords, "shape"):
            self.face_tri_indices = np.array(indices, "i4")
        else:
            self.face_tri_indices.extend(indices)

    def try_append_from_bmesh(self, bm, do_verts, do_edges, do_edge_center, do_face_center):
        if (
            bool(do_verts) != self.do_verts
            or bool(do_edges) != self.do_edges
            or bool(do_edge_center) != self.do_edge_center
            or bool(do_face_center) != self.do_face_center
        ):
            return False

        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()

        vert_count = len(bm.verts)
        edge_count = len(bm.edges)
        face_count = len(bm.faces)
        if (
            vert_count < self.source_vert_count
            or edge_count < self.source_edge_count
            or face_count < self.source_face_count
        ):
            return False
        if (
            vert_count == self.source_vert_count
            and edge_count == self.source_edge_count
            and face_count == self.source_face_count
        ):
            return False
        if not self.uses_direct_bmesh_indices:
            return False

        new_coords = []
        for i in range(self.source_vert_count, vert_count):
            v = bm.verts[i]
            if v.hide:
                return False
            new_coords.append(tuple(v.co))
        self._append_coords(new_coords)

        new_edges = []
        new_edge_centers = []
        if do_edges or do_edge_center:
            for i in range(self.source_edge_count, edge_count):
                e = bm.edges[i]
                if e.hide or e.verts[0].hide or e.verts[1].hide:
                    return False
                edge = (e.verts[0].index, e.verts[1].index)
                if edge[0] >= len(self.vert_coords) or edge[1] >= len(self.vert_coords):
                    return False
                if do_edges:
                    new_edges.append(edge)
                if do_edge_center:
                    new_edge_centers.append(tuple((e.verts[0].co + e.verts[1].co) * 0.5))
        self._append_edge_indices(new_edges)
        self._append_point_array("edge_centers", new_edge_centers)

        new_face_centers = []
        if do_face_center:
            for i in range(self.source_face_count, face_count):
                f = bm.faces[i]
                if f.hide:
                    return False
                new_face_centers.append(tuple(f.calc_center_median()))
        self._append_point_array("face_centers", new_face_centers)

        if face_count != self.source_face_count:
            tris = []
            for tri in bm.calc_loop_triangles():
                if not tri or tri[0].face.index < self.source_face_count:
                    continue
                if tri[0].face.hide or tri[0].vert.hide or tri[1].vert.hide or tri[2].vert.hide:
                    return False
                tris.append((tri[0].vert.index, tri[1].vert.index, tri[2].vert.index))
            self._append_face_tri_indices(tris)

        self._visible_vert_count = len(self.vert_coords) if do_verts else 0
        self._store_source_counts(bm)
        self._reset_batches()
        self._build_batches()
        return True

    def _point_batch(self, coords):
        if len(coords) == 0:
            return None
        fmt = gpu.types.GPUVertFormat()
        fmt.attr_add(id="pos", comp_type="F32", len=3, fetch_mode="FLOAT")
        vbo = gpu.types.GPUVertBuf(fmt, len=len(coords))
        vbo.attr_fill(0, data=coords)
        batch = gpu.types.GPUBatch(type="POINTS", buf=vbo)
        batch.program_set(self._shader())
        return batch

    def _build_batches(self):
        shader = self._shader()

        if len(self.face_tri_indices) > 0:
            depth_shader = self._shader(depth_only=True)
            fmt = gpu.types.GPUVertFormat()
            fmt.attr_add(id="pos", comp_type="F32", len=3, fetch_mode="FLOAT")
            vbo = gpu.types.GPUVertBuf(fmt, len=len(self.vert_coords))
            vbo.attr_fill(0, data=self.vert_coords)
            ebo = gpu.types.GPUIndexBuf(type="TRIS", seq=self.face_tri_indices)
            self.batch_faces_depth = gpu.types.GPUBatch(type="TRIS", buf=vbo, elem=ebo)
            self.batch_faces_depth.program_set(depth_shader)

        if self._visible_vert_count:
            self.batch_verts = self._point_batch(self.vert_coords)

        if len(self.edge_indices) > 0:
            fmt = gpu.types.GPUVertFormat()
            fmt.attr_add(id="pos", comp_type="F32", len=3, fetch_mode="FLOAT")
            vbo = gpu.types.GPUVertBuf(fmt, len=len(self.vert_coords))
            vbo.attr_fill(0, data=self.vert_coords)
            ebo = gpu.types.GPUIndexBuf(type="LINES", seq=self.edge_indices)
            self.batch_edges = gpu.types.GPUBatch(type="LINES", buf=vbo, elem=ebo)
            self.batch_edges.program_set(shader)

        if len(self.edge_centers) > 0:
            self.batch_edge_centers = self._point_batch(self.edge_centers)

        if len(self.face_centers) > 0:
            self.batch_face_centers = self._point_batch(self.face_centers)

    def total_elements(self):
        return (
            self._visible_vert_count
            + len(self.edge_indices)
            + len(self.edge_centers)
            + len(self.face_centers)
        )

    def draw(self, matrix_world, view_projection_matrix, point_size, use_depth_occlusion=True):
        shader = self._shader()
        mvp = view_projection_matrix @ matrix_world

        if use_depth_occlusion and self.batch_faces_depth:
            depth_shader = self._shader(depth_only=True)
            depth_shader.bind()
            depth_shader.uniform_float("ModelViewProjectionMatrix", mvp)
            self.batch_faces_depth.draw(depth_shader)

        shader.bind()
        shader.uniform_float("ModelViewProjectionMatrix", mvp)
        shader.uniform_float("point_size", float(point_size))

        offset = 1
        self.first_vert = offset
        if self.batch_verts:
            shader.uniform_int("offset", offset)
            self.batch_verts.draw(shader)
            offset += self._visible_vert_count

        self.first_edge = offset
        if self.batch_edges:
            shader.uniform_int("offset", offset)
            self.batch_edges.draw(shader)
            offset += len(self.edge_indices)

        self.first_edge_center = offset
        if self.batch_edge_centers:
            shader.uniform_int("offset", offset)
            self.batch_edge_centers.draw(shader)
            offset += len(self.edge_centers)

        self.first_face_center = offset
        if self.batch_face_centers:
            shader.uniform_int("offset", offset)
            self.batch_face_centers.draw(shader)

    def resolve(self, index, ctx, obj, x, y, max_px):
        mw = obj.matrix_world

        if self.first_vert <= index < self.first_vert + self._visible_vert_count:
            local = Vector(self.vert_coords[index - self.first_vert])
            return mw @ local

        if self.first_edge <= index < self.first_edge + len(self.edge_indices):
            edge_index = index - self.first_edge
            i0, i1 = self.edge_indices[edge_index]
            p0 = mw @ Vector(self.vert_coords[i0])
            p1 = mw @ Vector(self.vert_coords[i1])
            p0_2d = location_3d_to_region_2d(ctx.region, ctx.region_data, p0)
            p1_2d = location_3d_to_region_2d(ctx.region, ctx.region_data, p1)
            if p0_2d is None or p1_2d is None:
                return None
            hit = intersect_point_line(Vector((x, y)), p0_2d, p1_2d)
            if not hit:
                return None
            factor = max(0.0, min(1.0, hit[1]))
            closest = p0_2d.lerp(p1_2d, factor)
            if (Vector((x, y)) - closest).length > max_px:
                return None
            return closest_world_point_on_edge_under_cursor(
                ctx, closest.x, closest.y, p0, p1, fallback_factor=factor
            )

        if self.first_edge_center <= index < self.first_edge_center + len(self.edge_centers):
            local = Vector(self.edge_centers[index - self.first_edge_center])
            return mw @ local

        if self.first_face_center <= index < self.first_face_center + len(self.face_centers):
            local = Vector(self.face_centers[index - self.first_face_center])
            return mw @ local

        return None


class SnapPickBuffer:
    def __init__(self):
        self._offscreen = None
        self._buffer = None
        self._mesh = None
        self._mesh_key = None
        self._draw_key = None
        self._last_error = None
        self._incremental_dirty = False

    def invalidate(self, allow_incremental=False):
        if allow_incremental and self._mesh is not None:
            self._buffer = None
            self._draw_key = None
            self._incremental_dirty = True
            return
        self._buffer = None
        self._mesh = None
        self._mesh_key = None
        self._draw_key = None
        self._incremental_dirty = False

    def free(self):
        self.invalidate()
        if self._offscreen:
            self._offscreen.free()
        self._offscreen = None

    def _matrix_key(self, matrix):
        return tuple(round(v, 6) for row in matrix for v in row)

    def _mesh_signature(self, obj, bm, do_verts, do_edges, do_edge_center, do_face_center):
        return (
            obj.as_pointer(),
            obj.data.as_pointer(),
            len(bm.verts),
            len(bm.edges),
            len(bm.faces),
            bool(do_verts),
            bool(do_edges),
            bool(do_edge_center),
            bool(do_face_center),
        )

    def _ensure_offscreen(self, region):
        if self._offscreen is None:
            self._offscreen = _SnapOffscreen(region.width, region.height)
        else:
            self._offscreen.resize(region.width, region.height)

    def _draw_signature(self, ctx, obj, max_px):
        region = ctx.region
        rv3d = ctx.region_data
        use_depth_occlusion = self._use_depth_occlusion(ctx)
        return (
            region.width,
            region.height,
            rv3d.view_perspective,
            self._matrix_key(rv3d.perspective_matrix),
            self._matrix_key(obj.matrix_world),
            use_depth_occlusion,
            round(float(max_px), 3),
            self._mesh_key,
        )

    def _use_depth_occlusion(self, ctx):
        if not getattr(ctx, "space_data", None) or ctx.space_data.type != 'VIEW_3D':
            return True
        shading = ctx.space_data.shading
        return not (shading.type == 'WIREFRAME' or shading.show_xray)

    def _mouse_near_object_bounds(self, ctx, obj, x, y, max_px):
        """Cheap coarse reject before touching dense edit mesh data."""
        region = ctx.region
        rv3d = ctx.region_data
        mw = obj.matrix_world
        projected = []

        for corner in obj.bound_box:
            co_2d = location_3d_to_region_2d(region, rv3d, mw @ Vector(corner))
            if co_2d is not None:
                projected.append(co_2d)

        if not projected:
            return True

        min_x = min(p.x for p in projected)
        max_x = max(p.x for p in projected)
        min_y = min(p.y for p in projected)
        max_y = max(p.y for p in projected)
        padding = max(64.0, float(max_px) * 4.0)

        return (
            min_x - padding <= x <= max_x + padding
            and min_y - padding <= y <= max_y + padding
        )

    def _read_nearest_index(self, x, y, max_px):
        if self._buffer is None:
            return 0

        threshold = int(max(1, math.ceil(max_px)))
        loc = [int(y), int(x)]
        rect = (
            (max(0, loc[0] - threshold), min(self._buffer.dimensions[0], loc[0] + threshold)),
            (max(0, loc[1] - threshold), min(self._buffer.dimensions[1], loc[1] + threshold)),
        )

        if loc[0] < rect[0][0] or loc[0] >= rect[0][1]:
            return 0
        if loc[1] < rect[1][0] or loc[1] >= rect[1][1]:
            return 0

        direction = 0
        best = int(self._buffer[loc[0]][loc[1]])
        if best:
            return best

        for radius in range(1, 2 * threshold + 1):
            for axis in range(2):
                for _ in range(radius):
                    if direction == 0:
                        loc[1] += 1
                    elif direction == 1:
                        loc[0] -= 1
                    elif direction == 2:
                        loc[1] -= 1
                    else:
                        loc[0] += 1

                    if loc[not axis] < rect[not axis][0] or loc[not axis] >= rect[not axis][1]:
                        return 0

                    value = int(self._buffer[loc[0]][loc[1]])
                    if value:
                        return value
                direction = (direction + 1) % 4

        return 0

    def snap(self, ctx, obj, x, y, max_px, do_verts, do_edges, do_edge_center, do_face_center, debug_stats=None):
        t_total = time.perf_counter()
        if obj is None or obj.type != 'MESH' or not obj.data.is_editmode:
            return None
        if ctx.region is None or ctx.region_data is None:
            return None

        if not self._mouse_near_object_bounds(ctx, obj, x, y, max_px):
            if debug_stats is not None:
                debug_stats["coarse"] = "bounds_reject"
                debug_stats["mesh_update"] = "skip"
                debug_stats["mesh_access_ms"] = 0.0
                debug_stats["mesh_build_ms"] = 0.0
                debug_stats["draw_ms"] = 0.0
                debug_stats["buffer_read_ms"] = 0.0
                debug_stats["lookup_ms"] = 0.0
                debug_stats["resolve_ms"] = 0.0
                debug_stats["total_ms"] = (time.perf_counter() - t_total) * 1000.0
                debug_stats["index"] = 0
                debug_stats["result"] = None
            return None

        t_mesh = time.perf_counter()
        bm = bmesh.from_edit_mesh(obj.data)
        mesh_key = self._mesh_signature(obj, bm, do_verts, do_edges, do_edge_center, do_face_center)
        if mesh_key != self._mesh_key or self._incremental_dirty:
            t_build = time.perf_counter()
            mesh_update = "full"
            if self._mesh is not None and self._mesh.try_append_from_bmesh(
                bm, do_verts, do_edges, do_edge_center, do_face_center
            ):
                mesh_update = "append"
            else:
                self._mesh = _GpuSnapMesh(obj, bm, do_verts, do_edges, do_edge_center, do_face_center)
            self._mesh_key = mesh_key
            self._incremental_dirty = False
            self._draw_key = None
            self._buffer = None
            if debug_stats is not None:
                debug_stats["mesh_build_ms"] = (time.perf_counter() - t_build) * 1000.0
                debug_stats["mesh_update"] = mesh_update
        elif debug_stats is not None:
            debug_stats["mesh_build_ms"] = 0.0
            debug_stats["mesh_update"] = "reuse"

        if self._mesh is None or self._mesh.total_elements() == 0:
            return None

        t_offscreen = time.perf_counter()
        self._ensure_offscreen(ctx.region)
        draw_key = self._draw_signature(ctx, obj, max_px)
        if draw_key != self._draw_key:
            t_draw = time.perf_counter()
            self._offscreen.clear()
            use_depth_occlusion = self._use_depth_occlusion(ctx)
            gpu.state.depth_mask_set(True)
            gpu.state.depth_test_set('LESS_EQUAL' if use_depth_occlusion else 'NONE')
            gpu.state.program_point_size_set(True)
            with self._offscreen.bind():
                self._mesh.draw(
                    obj.matrix_world,
                    ctx.region_data.perspective_matrix,
                    max(3.0, max_px * 0.35),
                    use_depth_occlusion=use_depth_occlusion,
                )
            gpu.state.program_point_size_set(False)
            gpu.state.depth_mask_set(False)
            gpu.state.depth_test_set('NONE')
            if debug_stats is not None:
                debug_stats["draw_ms"] = (time.perf_counter() - t_draw) * 1000.0
                debug_stats["depth_occlusion"] = use_depth_occlusion
                debug_stats["buffer_read_ms"] = 0.0
        else:
            if debug_stats is not None:
                debug_stats["draw_ms"] = 0.0
                debug_stats["depth_occlusion"] = self._use_depth_occlusion(ctx)
                debug_stats["buffer_read_ms"] = 0.0

        t_read = time.perf_counter()
        if draw_key != self._draw_key:
            self._buffer = self._offscreen.read()
            self._draw_key = draw_key
            if debug_stats is not None:
                debug_stats["buffer_read_ms"] = (time.perf_counter() - t_read) * 1000.0
        elif debug_stats is not None:
            debug_stats["buffer_read_ms"] = 0.0

        t_lookup = time.perf_counter()
        index = self._read_nearest_index(x, y, max_px)
        if debug_stats is not None:
            debug_stats["lookup_ms"] = (time.perf_counter() - t_lookup) * 1000.0
            debug_stats["mesh_access_ms"] = (t_offscreen - t_mesh) * 1000.0
            debug_stats["total_ms"] = (time.perf_counter() - t_total) * 1000.0
            debug_stats["index"] = int(index)
        if not index:
            return None
        t_resolve = time.perf_counter()
        result = self._mesh.resolve(index, ctx, obj, x, y, max_px)
        if debug_stats is not None:
            debug_stats["resolve_ms"] = (time.perf_counter() - t_resolve) * 1000.0
            debug_stats["result"] = result
            debug_stats["total_ms"] = (time.perf_counter() - t_total) * 1000.0
        return result


_snap_pick_buffer = SnapPickBuffer()


def invalidate_snap_pick_buffer(allow_incremental=False):
    _snap_pick_buffer.invalidate(allow_incremental=allow_incremental)


def snap_with_pick_buffer(ctx, obj, x, y, max_px, do_verts, do_edges, do_edge_center, do_face_center, debug_stats=None):
    return _snap_pick_buffer.snap(
        ctx, obj, x, y, max_px,
        do_verts, do_edges, do_edge_center, do_face_center,
        debug_stats=debug_stats,
    )
