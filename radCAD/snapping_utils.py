# snapping_utils.py

import bmesh
from mathutils import Vector, geometry
from bpy_extras import view3d_utils

ELEMENT_SNAP_RADIUS_PX = 15.0

# Grid cache for fast spatial queries
_snap_cache = {}


def _get_snap_grid(obj, bm, mw, grid_size=10.0):
    """Build/return cached world-space spatial grid with normals."""
    key = (id(obj.data), len(bm.verts), len(bm.edges), len(bm.faces), grid_size)
    if key in _snap_cache:
        return _snap_cache[key]

    grid = {}
    rot = mw.to_3x3().normalized()  # rotation matrix for normals

    # Add verts to grid  — store (type, index, world_co, world_normal)
    for v in bm.verts:
        if v.hide: continue
        wco = mw @ v.co
        wnm = (rot @ v.normal).normalized()
        cell = (int(wco.x // grid_size), int(wco.y // grid_size), int(wco.z // grid_size))
        if cell not in grid:
            grid[cell] = []
        grid[cell].append(('vert', v.index, wco, wnm))

    # Add edge centers to grid
    for e in bm.edges:
        if e.hide: continue
        wco = mw @ ((e.verts[0].co + e.verts[1].co) * 0.5)
        avg_n = ((e.verts[0].normal + e.verts[1].normal) * 0.5)
        wnm = (rot @ avg_n).normalized()
        cell = (int(wco.x // grid_size), int(wco.y // grid_size), int(wco.z // grid_size))
        if cell not in grid:
            grid[cell] = []
        grid[cell].append(('edge_center', e.index, wco, wnm))

    # Add face centers to grid
    for f in bm.faces:
        if f.hide: continue
        wco = mw @ f.calc_center_median()
        wnm = (rot @ f.normal).normalized()
        cell = (int(wco.x // grid_size), int(wco.y // grid_size), int(wco.z // grid_size))
        if cell not in grid:
            grid[cell] = []
        grid[cell].append(('face_center', f.index, wco, wnm))

    _snap_cache[key] = grid
    return _snap_cache[key]


def snap_to_mesh_components(ctx, obj, x, y, max_px=ELEMENT_SNAP_RADIUS_PX,
                            do_verts=True,
                            do_edges=True,
                            do_edge_center=True,
                            do_face_center=True,
                            **kwargs):
    """
    Returns (world_co, world_normal) or None.
    Spatial grid partitioning + screen-space 15px bubble.
    Normal is derived from the snapped element — no scene raycast needed.
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
        px = W * (1 + v.x / v.w) * 0.5
        py = H * (1 + v.y / v.w) * 0.5
        if px < -50 or px > W + 50 or py < -50 or py > H + 50:
            return None
        return Vector((px, py))

    limit_sq = max_px * max_px

    # Get grid size from preferences
    try:
        import bpy
        prefs = bpy.context.preferences.addons[__package__].preferences
        grid_size = prefs.snap_grid_size
    except:
        grid_size = 10.0

    grid = _get_snap_grid(obj, bm, mw, grid_size)

    # Get 3D query point along camera ray
    ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, (x, y))
    ray_dir = view3d_utils.region_2d_to_vector_3d(region, rv3d, (x, y))
    view_center = Vector(rv3d.view_location)
    t = max(0.01, (view_center - ray_origin).dot(ray_dir))
    query_pt = ray_origin + ray_dir * t

    # Find cursor's grid cell and check nearby cells (7x7x7)
    qx = int(query_pt.x // grid_size)
    qy = int(query_pt.y // grid_size)
    qz = int(query_pt.z // grid_size)

    cells_to_search = 3
    nearby_cells = []
    for dx in range(-cells_to_search, cells_to_search + 1):
        for dy in range(-cells_to_search, cells_to_search + 1):
            for dz in range(-cells_to_search, cells_to_search + 1):
                cell = (qx + dx, qy + dy, qz + dz)
                if cell in grid:
                    nearby_cells.append(cell)

    # Track best candidates: (world_co, world_normal, screen_dist_sq)
    best_vert = None
    best_dist_vert = float('inf')
    best_edge_center = None
    best_dist_edge_center = float('inf')
    best_face_center = None
    best_dist_face_center = float('inf')

    world_cull_sq = (grid_size * 3.0) ** 2

    # --- Verts ---
    if do_verts:
        for cell in nearby_cells:
            for elem_type, idx, wco, wnm in grid[cell]:
                if elem_type != 'vert': continue
                if (wco - query_pt).length_squared > world_cull_sq:
                    continue
                p2d = project_fast(wco)
                if p2d is None: continue
                d2 = (mouse - p2d).length_squared
                if d2 < limit_sq and d2 < best_dist_vert:
                    best_vert = (wco, wnm)
                    best_dist_vert = d2

    if best_vert is not None:
        return best_vert

    # --- Edge centers ---
    if do_edge_center:
        for cell in nearby_cells:
            for elem_type, idx, wco, wnm in grid[cell]:
                if elem_type != 'edge_center': continue
                if (wco - query_pt).length_squared > world_cull_sq:
                    continue
                p2d = project_fast(wco)
                if p2d is None: continue
                d2 = (mouse - p2d).length_squared
                if d2 < limit_sq and d2 < best_dist_edge_center:
                    best_edge_center = (wco, wnm)
                    best_dist_edge_center = d2

    # --- Face centers ---
    if do_face_center:
        for cell in nearby_cells:
            for elem_type, idx, wco, wnm in grid[cell]:
                if elem_type != 'face_center': continue
                if (wco - query_pt).length_squared > world_cull_sq:
                    continue
                p2d = project_fast(wco)
                if p2d is None: continue
                d2 = (mouse - p2d).length_squared
                if d2 < limit_sq and d2 < best_dist_face_center:
                    best_face_center = (wco, wnm)
                    best_dist_face_center = d2

    # Return best edge or face center
    if best_edge_center is not None or best_face_center is not None:
        if best_edge_center is None:
            return best_face_center
        if best_face_center is None:
            return best_edge_center
        return best_edge_center if best_dist_edge_center < best_dist_face_center else best_face_center

    # --- Edge closest-point snapping ---
    if do_edges:
        rot = mw.to_3x3().normalized()
        for cell in nearby_cells:
            for elem_type, idx, wco, wnm in grid[cell]:
                if elem_type != 'edge_center': continue
                if (wco - query_pt).length_squared > world_cull_sq:
                    continue

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
                                return (pt_3d, wnm)

    return None
