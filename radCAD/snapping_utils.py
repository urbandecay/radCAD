# snapping_utils.py

import bmesh
import math
import time
from mathutils import Vector, geometry
from bpy_extras import view3d_utils
from bpy_extras.view3d_utils import location_3d_to_region_2d

ELEMENT_SNAP_RADIUS_PX = 15.0
DEBUG_SNAP_TIMING = True


class ScreenSnapCache:
    """Caches mesh elements after projecting them into viewport screen space."""

    def __init__(self, cell_px=48):
        self.cell_px = float(cell_px)
        self._key = None
        self.vert_bins = {}
        self.edge_center_bins = {}
        self.face_center_bins = {}
        self.edge_bins = {}

    def invalidate(self):
        self._key = None
        self.vert_bins.clear()
        self.edge_center_bins.clear()
        self.face_center_bins.clear()
        self.edge_bins.clear()

    def _bin_key(self, p2d):
        return (
            int(math.floor(p2d.x / self.cell_px)),
            int(math.floor(p2d.y / self.cell_px)),
        )

    def _add_point(self, bins, p2d, item):
        key = self._bin_key(p2d)
        bins.setdefault(key, []).append(item)

    def _add_edge(self, edge_id, p1_2d, p2_2d, v1_world, v2_world, max_px):
        margin = max_px + 5.0
        min_x = min(p1_2d.x, p2_2d.x) - margin
        max_x = max(p1_2d.x, p2_2d.x) + margin
        min_y = min(p1_2d.y, p2_2d.y) - margin
        max_y = max(p1_2d.y, p2_2d.y) + margin

        min_i = int(math.floor(min_x / self.cell_px))
        max_i = int(math.floor(max_x / self.cell_px))
        min_j = int(math.floor(min_y / self.cell_px))
        max_j = int(math.floor(max_y / self.cell_px))

        item = (edge_id, p1_2d, p2_2d, v1_world, v2_world)
        for i in range(min_i, max_i + 1):
            for j in range(min_j, max_j + 1):
                self.edge_bins.setdefault((i, j), []).append(item)

    def _matrix_key(self, matrix):
        return tuple(round(v, 5) for row in matrix for v in row)

    def _cache_key(self, ctx, obj, bm, do_verts, do_edges, do_edge_center,
                   do_face_center, max_px):
        region, rv3d = ctx.region, ctx.region_data
        return (
            obj.name,
            len(bm.verts),
            len(bm.edges),
            len(bm.faces),
            self._matrix_key(obj.matrix_world),
            region.width,
            region.height,
            rv3d.view_perspective,
            self._matrix_key(rv3d.perspective_matrix),
            bool(do_verts),
            bool(do_edges),
            bool(do_edge_center),
            bool(do_face_center),
            round(float(max_px), 3),
        )

    def ensure(self, ctx, obj, bm, do_verts=True, do_edges=True,
               do_edge_center=True, do_face_center=True,
               max_px=ELEMENT_SNAP_RADIUS_PX):
        key = self._cache_key(
            ctx, obj, bm, do_verts, do_edges, do_edge_center,
            do_face_center, max_px
        )
        if key == self._key:
            return False

        self.invalidate()
        self._key = key

        region, rv3d = ctx.region, ctx.region_data
        mw = obj.matrix_world

        if do_verts:
            for v in bm.verts:
                if v.hide:
                    continue
                wco = mw @ v.co
                p2d = location_3d_to_region_2d(region, rv3d, wco)
                if p2d is not None:
                    self._add_point(self.vert_bins, p2d, (p2d, wco))

        if do_edges or do_edge_center:
            for edge_id, e in enumerate(bm.edges):
                if e.hide:
                    continue

                v1_world = mw @ e.verts[0].co
                v2_world = mw @ e.verts[1].co

                if do_edge_center:
                    center = (v1_world + v2_world) * 0.5
                    center_2d = location_3d_to_region_2d(region, rv3d, center)
                    if center_2d is not None:
                        self._add_point(self.edge_center_bins, center_2d, (center_2d, center))

                if do_edges:
                    p1_2d = location_3d_to_region_2d(region, rv3d, v1_world)
                    p2_2d = location_3d_to_region_2d(region, rv3d, v2_world)
                    if p1_2d is not None and p2_2d is not None:
                        self._add_edge(edge_id, p1_2d, p2_2d, v1_world, v2_world, max_px)

        if do_face_center:
            for f in bm.faces:
                if f.hide:
                    continue
                wco = mw @ f.calc_center_median()
                p2d = location_3d_to_region_2d(region, rv3d, wco)
                if p2d is not None:
                    self._add_point(self.face_center_bins, p2d, (p2d, wco))
        return True

    def query(self, x, y, max_px):
        margin = max_px + 5.0
        min_i = int(math.floor((x - margin) / self.cell_px))
        max_i = int(math.floor((x + margin) / self.cell_px))
        min_j = int(math.floor((y - margin) / self.cell_px))
        max_j = int(math.floor((y + margin) / self.cell_px))

        verts = []
        edge_centers = []
        face_centers = []
        edges = []
        seen_edges = set()

        for i in range(min_i, max_i + 1):
            for j in range(min_j, max_j + 1):
                key = (i, j)
                verts.extend(self.vert_bins.get(key, ()))
                edge_centers.extend(self.edge_center_bins.get(key, ()))
                face_centers.extend(self.face_center_bins.get(key, ()))

                for item in self.edge_bins.get(key, ()):
                    edge_id = item[0]
                    if edge_id in seen_edges:
                        continue
                    seen_edges.add(edge_id)
                    edges.append(item)

        return verts, edge_centers, face_centers, edges


_screen_snap_cache = ScreenSnapCache()


def invalidate_snap_cache(allow_incremental=False):
    _screen_snap_cache.invalidate()
    try:
        from .snap_pick_buffer import invalidate_snap_pick_buffer
        invalidate_snap_pick_buffer(allow_incremental=allow_incremental)
    except Exception:
        pass


def raycast_under_mouse(ctx, x, y):
    region, rv3d = ctx.region, ctx.region_data
    coord = (x, y)
    view_vec = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
    ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
    depsgraph = ctx.evaluated_depsgraph_get()

    hit, loc, norm, face_index, obj, _ = ctx.scene.ray_cast(
        depsgraph, ray_origin, view_vec
    )

    if hit and obj and obj.type == 'MESH':
        return loc, norm, obj
    return None, None, None


def is_visible_to_view(ctx, target_co, tolerance=0.1):
    region, rv3d = ctx.region, ctx.region_data
    depsgraph = ctx.evaluated_depsgraph_get()

    p2d = location_3d_to_region_2d(region, rv3d, target_co)
    if p2d is None:
        return False

    ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, p2d)
    ray_vector = view3d_utils.region_2d_to_vector_3d(region, rv3d, p2d)

    success, hit_loc, hit_normal, face_idx, hit_obj, hit_mat = ctx.scene.ray_cast(
        depsgraph,
        ray_origin,
        ray_vector,
        distance=10000.0
    )

    if success:
        dist_hit = (hit_loc - ray_origin).length
        dist_target = (target_co - ray_origin).length

        if dist_hit < (dist_target - tolerance):
            return False

    return True


def _edge_screen_hit(mouse, p1_2d, p2_2d):
    intersect_2d = geometry.intersect_point_line(mouse, p1_2d, p2_2d)
    if not intersect_2d:
        return None

    pt_on_seg_2d = intersect_2d[0]
    min_x, max_x = min(p1_2d.x, p2_2d.x), max(p1_2d.x, p2_2d.x)
    min_y, max_y = min(p1_2d.y, p2_2d.y), max(p1_2d.y, p2_2d.y)

    if not ((min_x - 5 <= pt_on_seg_2d.x <= max_x + 5) and
            (min_y - 5 <= pt_on_seg_2d.y <= max_y + 5)):
        return None

    seg_len = (p2_2d - p1_2d).length
    if seg_len <= 0.001:
        return None

    dist2 = (mouse - pt_on_seg_2d).length_squared
    factor = (pt_on_seg_2d - p1_2d).length / seg_len
    return dist2, max(0.0, min(1.0, factor))


def snap_to_mesh_components(ctx, obj, x, y, max_px=ELEMENT_SNAP_RADIUS_PX,
                            do_verts=True,
                            do_edges=True,
                            do_edge_center=True,
                            do_face_center=True,
                            **kwargs):
    """
    Screen-space snap logic using a cached projected index.

    Priority:
    0. Verts
    1. Edge/face centers
    2. Nearest point on edge
    """
    if obj is None or obj.type != 'MESH':
        return None

    t_total = time.perf_counter()
    if not getattr(snap_to_mesh_components, "_pick_buffer_failed", False):
        try:
            from .snap_pick_buffer import snap_with_pick_buffer
            debug_stats = {} if DEBUG_SNAP_TIMING else None
            result = snap_with_pick_buffer(
                ctx, obj, x, y, max_px,
                do_verts=do_verts,
                do_edges=do_edges,
                do_edge_center=do_edge_center,
                do_face_center=do_face_center,
                debug_stats=debug_stats,
            )
            if DEBUG_SNAP_TIMING:
                _log_pick_buffer_perf(debug_stats, t_total, result)
            return result
        except Exception as exc:
            print(f"[radCAD Snap] GPU pick-buffer path disabled, falling back to screen cache: {exc}")
            snap_to_mesh_components._pick_buffer_failed = True

    t_lookup = time.perf_counter()
    mouse = Vector((x, y))
    bm = bmesh.from_edit_mesh(obj.data)

    if do_verts:
        bm.verts.ensure_lookup_table()
    if do_edges or do_edge_center:
        bm.edges.ensure_lookup_table()
    if do_face_center:
        bm.faces.ensure_lookup_table()

    t_cache = time.perf_counter()
    rebuilt = _screen_snap_cache.ensure(
        ctx, obj, bm,
        do_verts=do_verts,
        do_edges=do_edges,
        do_edge_center=do_edge_center,
        do_face_center=do_face_center,
        max_px=max_px,
    )
    t_after_cache = time.perf_counter()
    verts, edge_centers, face_centers, edges = _screen_snap_cache.query(x, y, max_px)
    t_query = time.perf_counter()

    allow_occluded = False
    if ctx.space_data.type == 'VIEW_3D':
        shading = ctx.space_data.shading
        if shading.type == 'WIREFRAME' or shading.show_xray:
            allow_occluded = True

    candidates = []
    limit_sq = max_px * max_px

    if do_verts:
        for p2d, wco in verts:
            d2 = (mouse - p2d).length_squared
            if d2 < limit_sq:
                candidates.append((0, d2, wco))

    if do_edge_center:
        for p2d, wco in edge_centers:
            d2 = (mouse - p2d).length_squared
            if d2 < limit_sq:
                candidates.append((1, d2, wco))

    if do_face_center:
        for p2d, wco in face_centers:
            d2 = (mouse - p2d).length_squared
            if d2 < limit_sq:
                candidates.append((1, d2, wco))

    if do_edges:
        from .snap_pick_buffer import closest_world_point_on_edge_under_cursor

        for edge_id, p1_2d, p2_2d, v1_world, v2_world in edges:
            edge_hit = _edge_screen_hit(mouse, p1_2d, p2_2d)
            if edge_hit is None:
                continue

            dist2, factor = edge_hit
            if dist2 < limit_sq:
                closest_2d = p1_2d.lerp(p2_2d, factor)
                pt_3d = closest_world_point_on_edge_under_cursor(
                    ctx, closest_2d.x, closest_2d.y,
                    v1_world, v2_world, fallback_factor=factor
                )
                candidates.append((2, dist2, pt_3d))

    candidates.sort(key=lambda item: (item[0], item[1]))
    t_candidates = time.perf_counter()

    for prio, dist_sq, co in candidates:
        if allow_occluded:
            result = co
            if DEBUG_SNAP_TIMING:
                _log_screen_cache_perf(
                    t_total, t_lookup, t_cache, t_after_cache, t_query, t_candidates,
                    len(verts), len(edge_centers), len(face_centers), len(edges),
                    len(candidates), True, "occluded", result, rebuilt
                )
            return result

        if is_visible_to_view(ctx, co):
            result = co
            if DEBUG_SNAP_TIMING:
                _log_screen_cache_perf(
                    t_total, t_lookup, t_cache, t_after_cache, t_query, t_candidates,
                    len(verts), len(edge_centers), len(face_centers), len(edges),
                    len(candidates), True, "visible", result, rebuilt
                )
            return result

    if DEBUG_SNAP_TIMING:
        _log_screen_cache_perf(
            t_total, t_lookup, t_cache, t_after_cache, t_query, t_candidates,
            len(verts), len(edge_centers), len(face_centers), len(edges),
            len(candidates), False, "none", None, rebuilt
        )
    return None


def _fmt_ms(value):
    return f"{value:.2f}ms"


def _log_pick_buffer_perf(stats, t_total, result):
    if not DEBUG_SNAP_TIMING:
        return
    total_ms = (time.perf_counter() - t_total) * 1000.0
    print(
        "[SnapPerf] "
        f"total={_fmt_ms(total_ms)} "
        f"path=pick_buffer "
        f"coarse={stats.get('coarse', 'pass')} "
        f"mesh_update={stats.get('mesh_update', 'unknown')} "
        f"mesh_access={_fmt_ms(stats.get('mesh_access_ms', 0.0))} "
        f"mesh_build={_fmt_ms(stats.get('mesh_build_ms', 0.0))} "
        f"draw={_fmt_ms(stats.get('draw_ms', 0.0))} "
        f"buffer_read={_fmt_ms(stats.get('buffer_read_ms', 0.0))} "
        f"lookup={_fmt_ms(stats.get('lookup_ms', 0.0))} "
        f"resolve={_fmt_ms(stats.get('resolve_ms', 0.0))} "
        f"index={stats.get('index', 0)} "
        f"result={result is not None}"
    )


def _log_screen_cache_perf(t_total, t_lookup, t_cache, t_after_cache, t_query, t_candidates,
                           verts_n, edge_centers_n, face_centers_n, edges_n,
                           cand_n, result_ok, reason, result, rebuilt):
    if not DEBUG_SNAP_TIMING:
        return
    total_ms = (time.perf_counter() - t_total) * 1000.0
    print(
        "[SnapPerf] "
        f"total={_fmt_ms(total_ms)} "
        f"path=screen_cache "
        f"lookup={_fmt_ms((t_cache - t_lookup) * 1000.0)} "
        f"cache={_fmt_ms((t_after_cache - t_cache) * 1000.0)} "
        f"query={_fmt_ms((t_query - t_after_cache) * 1000.0)} "
        f"candidates={_fmt_ms((t_candidates - t_query) * 1000.0)} "
        f"verts={verts_n} edge_centers={edge_centers_n} face_centers={face_centers_n} edges={edges_n} "
        f"cand={cand_n} result={result_ok} reason={reason} rebuilt={rebuilt}"
    )
