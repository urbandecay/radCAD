# snapping_utils.py

import bmesh
from mathutils import Vector, geometry
from bpy_extras import view3d_utils
from bpy_extras.view3d_utils import location_3d_to_region_2d

ELEMENT_SNAP_RADIUS_PX = 15.0


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
    Pure brute force snap logic:
    0. Verts (Top Priority)
    1. Edge/Face Centers
    2. Nearest Point on Edge (Bottom Priority)

    Loops through ALL geometry, checks screen-space distance only.
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

    # Fast 2D projection using perspective matrix (avoids expensive API calls)
    pm = rv3d.perspective_matrix
    W = region.width
    H = region.height

    def project_fast(wco):
        v = pm @ wco.to_4d()
        if v.w <= 0:
            return None
        return Vector((W * (1 + v.x / v.w) * 0.5, H * (1 + v.y / v.w) * 0.5))

    # Candidates list stores: (PRIORITY, DIST_SQ, WORLD_CO)
    candidates = []
    limit_sq = max_px * max_px

    # 1. Verts (Priority 0) — Brute force loop
    if do_verts:
        for v in bm.verts:
            if v.hide: continue
            wco = mw @ v.co
            p2d = project_fast(wco)
            if p2d is None: continue
            d2 = (mouse - p2d).length_squared
            if d2 < limit_sq:
                candidates.append((0, d2, wco))

    # 2. Edge Centers (Priority 1) — Brute force loop
    if do_edge_center:
        for e in bm.edges:
            if e.hide: continue
            wco = mw @ ((e.verts[0].co + e.verts[1].co) * 0.5)
            p2d = project_fast(wco)
            if p2d is None: continue
            d2 = (mouse - p2d).length_squared
            if d2 < limit_sq:
                candidates.append((1, d2, wco))

    # 3. Face Centers (Priority 1) — Brute force loop
    if do_face_center:
        for f in bm.faces:
            if f.hide: continue
            wco = mw @ f.calc_center_median()
            p2d = project_fast(wco)
            if p2d is None: continue
            d2 = (mouse - p2d).length_squared
            if d2 < limit_sq:
                candidates.append((1, d2, wco))

    # 4. Nearest Point on Edge (Priority 2) — Brute force loop
    if do_edges:
        for e in bm.edges:
            if e.hide: continue

            v1_world = mw @ e.verts[0].co
            v2_world = mw @ e.verts[1].co

            p1_2d = project_fast(v1_world)
            p2_2d = project_fast(v2_world)

            if p1_2d and p2_2d:
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

    # 5. Sort & Select with Two-Tier Snapping
    candidates.sort(key=lambda item: (item[0], item[1]))

    # Tight bubble (0-5px) = snap instantly, outer bubble (5-15px) = only if tight is empty
    TIGHT_BUBBLE_PX = 5.0
    tight_bubble_sq = TIGHT_BUBBLE_PX * TIGHT_BUBBLE_PX

    tight_candidates = []
    outer_candidates = []

    # Split candidates
    for prio, dist_sq, co in candidates:
        if dist_sq < tight_bubble_sq:
            tight_candidates.append((prio, dist_sq, co))
        else:
            outer_candidates.append((prio, dist_sq, co))

    # Snap to closest candidate in tight bubble, no occlusion check
    if tight_candidates:
        prio, dist_sq, co = tight_candidates[0]
        return co

    # If nothing in tight bubble, snap to closest in outer bubble, no occlusion check
    if outer_candidates:
        prio, dist_sq, co = outer_candidates[0]
        return co

    return None
