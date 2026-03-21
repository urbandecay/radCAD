# snapping_utils.py

import bmesh
from mathutils import Vector, geometry, kdtree
from bpy_extras import view3d_utils
from bpy_extras.view3d_utils import location_3d_to_region_2d

ELEMENT_SNAP_RADIUS_PX = 15.0

# --- KD-TREE CACHE ---
# Built once per mesh state, reused every frame until topology changes.
# Key: (mesh data id, vert count, edge count, face count)
_snap_cache = {}

def _get_snap_cache(obj, bm, mw):
    key = (id(obj.data), len(bm.verts), len(bm.edges), len(bm.faces))
    if key in _snap_cache:
        return _snap_cache[key]

    kd_v = kdtree.KDTree(max(1, len(bm.verts)))
    for v in bm.verts:
        if not v.hide:
            kd_v.insert(mw @ v.co, v.index)
    kd_v.balance()

    kd_ec = kdtree.KDTree(max(1, len(bm.edges)))
    for e in bm.edges:
        if not e.hide:
            kd_ec.insert(mw @ ((e.verts[0].co + e.verts[1].co) * 0.5), e.index)
    kd_ec.balance()

    kd_fc = kdtree.KDTree(max(1, len(bm.faces)))
    for f in bm.faces:
        if not f.hide:
            kd_fc.insert(mw @ f.calc_center_median(), f.index)
    kd_fc.balance()

    _snap_cache.clear()
    _snap_cache[key] = {'kd_v': kd_v, 'kd_ec': kd_ec, 'kd_fc': kd_fc}
    return _snap_cache[key]


def raycast_under_mouse(ctx, x, y):
    region, rv3d = ctx.region, ctx.region_data
    coord = (x, y)
    view_vec = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
    ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
    depsgraph = ctx.evaluated_depsgraph_get()

    hit, loc, norm, face_index, obj, _ = ctx.scene.ray_cast(depsgraph, ray_origin, view_vec)

    if hit and obj and obj.type == 'MESH':
        return loc, norm, obj
    return None, None, None

def is_visible_to_view(ctx, target_co, tolerance=0.1):
    """
    Checks if a point is visible from the viewport.
    Uses view3d_utils to generate the ray, which correctly handles
    both PERSPECTIVE (conical) and ORTHO (parallel) projections.
    """
    region, rv3d = ctx.region, ctx.region_data
    depsgraph = ctx.evaluated_depsgraph_get()

    # 1. Project target to 2D screen space
    p2d = location_3d_to_region_2d(region, rv3d, target_co)
    if p2d is None:
        return False

    # 2. Get Ray Origin/Vector from Screen Point
    ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, p2d)
    ray_vector = view3d_utils.region_2d_to_vector_3d(region, rv3d, p2d)

    # 3. Cast Ray into scene
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

def snap_to_mesh_components(ctx, obj, x, y, max_px=ELEMENT_SNAP_RADIUS_PX,
                            do_verts=True,
                            do_edges=True,
                            do_edge_center=True,
                            do_face_center=True,
                            **kwargs):
    """
    Snap logic with strict Priority:
    0. Verts (Top Priority)
    1. Edge/Face Centers
    2. Nearest Edge (Bottom Priority)

    Uses cached KD-trees for fast proximity lookup — no Python loops over all geometry.
    """
    if obj is None or obj.type != 'MESH':
        return None

    region, rv3d = ctx.region, ctx.region_data
    mouse = Vector((x, y))
    bm = bmesh.from_edit_mesh(obj.data)

    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    mw = obj.matrix_world

    # Check for X-Ray / Wireframe
    allow_occluded = False
    if ctx.space_data.type == 'VIEW_3D':
        shading = ctx.space_data.shading
        if shading.type == 'WIREFRAME' or shading.show_xray:
            allow_occluded = True

    # Get/build cached KD-trees
    cache = _get_snap_cache(obj, bm, mw)

    # --- 3D QUERY POINT & SEARCH RADIUS ---
    # Project orbit target onto mouse ray for depth estimate (no expensive raycast).
    ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, (x, y))
    ray_dir = view3d_utils.region_2d_to_vector_3d(region, rv3d, (x, y))
    view_center = Vector(rv3d.view_location)
    t = max(0.01, (view_center - ray_origin).dot(ray_dir))
    query_pt = ray_origin + ray_dir * t

    # Calculate world-space radius
    ws_radius = None
    p_center = location_3d_to_region_2d(region, rv3d, query_pt)
    if p_center:
        view_inv = rv3d.view_matrix.inverted()
        cam_right = view_inv.to_3x3() @ Vector((1.0, 0.0, 0.0))
        p_right = location_3d_to_region_2d(region, rv3d, query_pt + cam_right)
        if p_right:
            px_per_meter = (p_right - p_center).length
            if px_per_meter > 0.1:
                ws_radius = (max_px * 10.0) / px_per_meter  # 10x = sticky snapping, KD-tree still fast

    if ws_radius is None:
        ws_radius = rv3d.view_distance * 2.0

    # Candidates list stores: (PRIORITY, DIST_SQ, WORLD_CO)
    candidates = []
    limit_sq = max_px * max_px
    found_vert = False

    # 1. Verts (Priority 0) — KD-tree query, no Python loop over all verts
    if do_verts:
        for wco, idx, _ in cache['kd_v'].find_range(query_pt, ws_radius):
            p2d = location_3d_to_region_2d(region, rv3d, wco)
            if p2d is None: continue
            d2 = (mouse - p2d).length_squared
            if d2 < limit_sq:
                candidates.append((0, d2, wco))
                found_vert = True

    # 2. Edge Centers (Priority 1) — KD-tree query
    if do_edge_center:
        for wco, idx, _ in cache['kd_ec'].find_range(query_pt, ws_radius):
            p2d = location_3d_to_region_2d(region, rv3d, wco)
            if p2d is None: continue
            d2 = (mouse - p2d).length_squared
            if d2 < limit_sq:
                candidates.append((1, d2, wco))

    # 3. Face Centers (Priority 1) — KD-tree query
    if do_face_center:
        for wco, idx, _ in cache['kd_fc'].find_range(query_pt, ws_radius):
            p2d = location_3d_to_region_2d(region, rv3d, wco)
            if p2d is None: continue
            d2 = (mouse - p2d).length_squared
            if d2 < limit_sq:
                candidates.append((1, d2, wco))

    # 4. Nearest Point on Edge (Priority 2)
    # Skip entirely if a vertex was already found — verts always win anyway.
    # Use edge center KD-tree to find candidate edges, then do full intersection on just those.
    if do_edges and not found_vert:
        checked_edges = set()
        for _, idx, _ in cache['kd_ec'].find_range(query_pt, ws_radius):
            if idx in checked_edges: continue
            checked_edges.add(idx)

            e = bm.edges[idx]
            if e.hide: continue

            v1_world = mw @ e.verts[0].co
            v2_world = mw @ e.verts[1].co

            p1_2d = location_3d_to_region_2d(region, rv3d, v1_world)
            p2_2d = location_3d_to_region_2d(region, rv3d, v2_world)

            if p1_2d and p2_2d:
                # Fast bounding box cull before expensive intersection math
                if (min(p1_2d.x, p2_2d.x) - max_px > mouse.x or
                    max(p1_2d.x, p2_2d.x) + max_px < mouse.x or
                    min(p1_2d.y, p2_2d.y) - max_px > mouse.y or
                    max(p1_2d.y, p2_2d.y) + max_px < mouse.y):
                    continue

                intersect_2d = geometry.intersect_point_line(mouse, p1_2d, p2_2d)

                if intersect_2d:
                    pt_on_seg_2d = intersect_2d[0]

                    min_x, max_x = min(p1_2d.x, p2_2d.x), max(p1_2d.x, p2_2d.x)
                    min_y, max_y = min(p1_2d.y, p2_2d.y), max(p1_2d.y, p2_2d.y)

                    if (min_x - 5 <= pt_on_seg_2d.x <= max_x + 5) and \
                       (min_y - 5 <= pt_on_seg_2d.y <= max_y + 5):

                        dist2 = (mouse - pt_on_seg_2d).length_squared

                        if dist2 < limit_sq:
                            seg_len = (p2_2d - p1_2d).length
                            if seg_len > 0.001:
                                factor = (pt_on_seg_2d - p1_2d).length / seg_len
                                pt_3d = v1_world + (v2_world - v1_world) * factor
                                candidates.append((2, dist2, pt_3d))

    # 5. Sort & Select
    candidates.sort(key=lambda item: (item[0], item[1]))

    for prio, dist_sq, co in candidates:
        if allow_occluded:
            return co
        if is_visible_to_view(ctx, co):
            return co

    return None
