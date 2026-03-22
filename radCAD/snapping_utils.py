# snapping_utils.py

import bmesh
from mathutils import Vector, geometry
from bpy_extras import view3d_utils
from bpy_extras.view3d_utils import location_3d_to_region_2d

ELEMENT_SNAP_RADIUS_PX = 15.0

# Grid cache for fast spatial queries
_snap_cache = {}

def _get_snap_grid(obj, bm, mw):
    """Build/return cached world-space spatial grid."""
    key = (id(obj.data), len(bm.verts), len(bm.edges), len(bm.faces))
    if key in _snap_cache:
        return _snap_cache[key]

    # Grid cell size in world units
    GRID_SIZE = 10.0
    grid = {}

    # Add verts to grid
    for v in bm.verts:
        if v.hide: continue
        wco = mw @ v.co
        cell = tuple((int(wco[i] / GRID_SIZE) for i in range(3)))
        if cell not in grid:
            grid[cell] = []
        grid[cell].append(('vert', v.index, wco))

    # Add edge centers to grid
    for e in bm.edges:
        if e.hide: continue
        wco = mw @ ((e.verts[0].co + e.verts[1].co) * 0.5)
        cell = tuple((int(wco[i] / GRID_SIZE) for i in range(3)))
        if cell not in grid:
            grid[cell] = []
        grid[cell].append(('edge_center', e.index, wco))

    # Add face centers to grid
    for f in bm.faces:
        if f.hide: continue
        wco = mw @ f.calc_center_median()
        cell = tuple((int(wco[i] / GRID_SIZE) for i in range(3)))
        if cell not in grid:
            grid[cell] = []
        grid[cell].append(('face_center', f.index, wco))

    _snap_cache.clear()
    _snap_cache[key] = {'grid': grid, 'grid_size': GRID_SIZE}
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
    Spatial grid partitioning + screen-space accuracy:
    1. Divide world into grid cells
    2. Query only nearby cells
    3. Final screen-space check confirms 15px bubble
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

    # Fast 2D projection using perspective matrix
    pm = rv3d.perspective_matrix
    W = region.width
    H = region.height

    def project_fast(wco):
        v = pm @ wco.to_4d()
        if v.w <= 0:
            return None
        return Vector((W * (1 + v.x / v.w) * 0.5, H * (1 + v.y / v.w) * 0.5))

    limit_sq = max_px * max_px
    cache = _get_snap_grid(obj, bm, mw)
    grid = cache['grid']
    grid_size = cache['grid_size']

    # Get 3D query point along camera ray
    ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, (x, y))
    ray_dir = view3d_utils.region_2d_to_vector_3d(region, rv3d, (x, y))
    view_center = Vector(rv3d.view_location)
    t = max(0.01, (view_center - ray_origin).dot(ray_dir))
    query_pt = ray_origin + ray_dir * t

    # Find cursor's grid cell and check nearby cells (7x7x7 to guarantee all verts found)
    query_cell = tuple((int(query_pt[i] / grid_size) for i in range(3)))
    nearby_cells = []
    for dx in range(-3, 4):
        for dy in range(-3, 4):
            for dz in range(-3, 4):
                cell = (query_cell[0] + dx, query_cell[1] + dy, query_cell[2] + dz)
                if cell in grid:
                    nearby_cells.append(cell)

    # Track best candidates
    best_vert = None
    best_dist_vert = float('inf')
    best_edge_center = None
    best_dist_edge_center = float('inf')
    best_face_center = None
    best_dist_face_center = float('inf')

    # Check only verts in nearby cells
    if do_verts:
        for cell in nearby_cells:
            for elem_type, idx, wco in grid[cell]:
                if elem_type != 'vert': continue
                p2d = project_fast(wco)
                if p2d is None: continue
                d2 = (mouse - p2d).length_squared
                if d2 < limit_sq and d2 < best_dist_vert:
                    best_vert = wco
                    best_dist_vert = d2

    if best_vert is not None:
        return best_vert

    # Check edge centers in nearby cells
    if do_edge_center:
        for cell in nearby_cells:
            for elem_type, idx, wco in grid[cell]:
                if elem_type != 'edge_center': continue
                p2d = project_fast(wco)
                if p2d is None: continue
                d2 = (mouse - p2d).length_squared
                if d2 < limit_sq and d2 < best_dist_edge_center:
                    best_edge_center = wco
                    best_dist_edge_center = d2

    # Check face centers in nearby cells
    if do_face_center:
        for cell in nearby_cells:
            for elem_type, idx, wco in grid[cell]:
                if elem_type != 'face_center': continue
                p2d = project_fast(wco)
                if p2d is None: continue
                d2 = (mouse - p2d).length_squared
                if d2 < limit_sq and d2 < best_dist_face_center:
                    best_face_center = wco
                    best_dist_face_center = d2

    # Return best edge or face center
    if best_edge_center is not None or best_face_center is not None:
        if best_edge_center is None:
            return best_face_center
        if best_face_center is None:
            return best_edge_center
        return best_edge_center if best_dist_edge_center < best_dist_face_center else best_face_center

    # Edge snapping (simplified - just check edges in nearby cells)
    if do_edges:
        for cell in nearby_cells:
            for elem_type, idx, _ in grid[cell]:
                if elem_type == 'edge_center':
                    e = bm.edges[idx]
                    if e.hide: continue

                    v1_world = mw @ e.verts[0].co
                    v2_world = mw @ e.verts[1].co

                    p1_2d = project_fast(v1_world)
                    p2_2d = project_fast(v2_world)

                    if p1_2d and p2_2d:
                        intersect_2d = geometry.intersect_point_line(mouse, p1_2d, p2_2d)
                        if intersect_2d:
                            pt_on_seg_2d = intersect_2d[0]
                            dist2 = (mouse - pt_on_seg_2d).length_squared
                            if dist2 < limit_sq:
                                seg_len = (p2_2d - p1_2d).length
                                if seg_len > 0.001:
                                    factor = (pt_on_seg_2d - p1_2d).length / seg_len
                                    pt_3d = v1_world + (v2_world - v1_world) * factor
                                    return pt_3d

    return None
