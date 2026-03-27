# snapping_utils.py

import bmesh
import math
from mathutils import Vector, Matrix, geometry
from bpy_extras import view3d_utils
from bpy_extras.view3d_utils import location_3d_to_region_2d

ELEMENT_SNAP_RADIUS_PX = 15.0

# ---------------------------------------------------------------------------
#  Spatial Grid — coarse world-space pre-filter
# ---------------------------------------------------------------------------
# The grid does NOT do the snapping. It just eliminates geometry that's
# obviously too far away from the cursor to ever be within 15px on screen.
# All the actual snap decisions stay in screen space, exactly as before.

class SpatialGrid:
    """Bins mesh element world positions into a uniform 3D grid.

    cell_size controls the granularity. Smaller = more cells, tighter culling
    but more overhead. Larger = fewer cells, less culling benefit.
    A good default is roughly the size of a typical "working area" chunk —
    somewhere between 0.5 and 5 Blender units depending on scene scale.
    """

    def __init__(self, cell_size=2.0):
        self.cell_size = cell_size
        self.inv_cell = 1.0 / cell_size
        # Each cell stores lists: {"verts": [...], "edge_centers": [...],
        #                          "face_centers": [...], "edges": [...]}
        self.cells = {}
        # Cache key: (obj_name, matrix_hash)
        self._cache_obj = None
        self._cache_mat_hash = None
        # Track how many elements we've already binned (for incremental updates)
        self._binned_verts = 0
        self._binned_edges = 0
        self._binned_faces = 0
        # Debug: which cells were searched last frame
        self.debug_searched_cells = []
        self.debug_candidate_cells = []
        self.debug_all_cells = []

    def _cell_key(self, pos):
        """Map a world position to a grid cell (i,j,k) tuple."""
        return (int(math.floor(pos.x * self.inv_cell)),
                int(math.floor(pos.y * self.inv_cell)),
                int(math.floor(pos.z * self.inv_cell)))

    def _add_to_cell(self, key, category, data):
        if key not in self.cells:
            self.cells[key] = {"verts": [], "edge_centers": [],
                               "face_centers": [], "edges": [],
                               "vert_idxs": [], "edge_idxs": []}
        self.cells[key][category].append(data)

    def _full_rebuild(self, obj, bm):
        """Nuke the grid and re-bin everything from scratch."""
        mw = obj.matrix_world
        self.cells.clear()
        self._binned_verts = 0
        self._binned_edges = 0
        self._binned_faces = 0
        self._incremental_add(obj, bm, mw)

    def _incremental_add(self, obj, bm, mw):
        """Bin only the NEW elements (indices >= what we've already binned)."""
        nv = len(bm.verts)
        ne = len(bm.edges)
        nf = len(bm.faces)

        # Verts
        for i in range(self._binned_verts, nv):
            v = bm.verts[i]
            if v.hide: continue
            wco = mw @ v.co
            ck = self._cell_key(wco)
            self._add_to_cell(ck, "verts", wco)
            self._add_to_cell(ck, "vert_idxs", i)

        # Edges
        for i in range(self._binned_edges, ne):
            e = bm.edges[i]
            if e.hide: continue
            v1w = mw @ e.verts[0].co
            v2w = mw @ e.verts[1].co
            center = (v1w + v2w) * 0.5
            ck = self._cell_key(center)
            self._add_to_cell(ck, "edge_centers", center)
            self._add_to_cell(ck, "edges", (v1w, v2w))
            self._add_to_cell(ck, "edge_idxs", i)

        # Faces
        for i in range(self._binned_faces, nf):
            f = bm.faces[i]
            if f.hide: continue
            wco = mw @ f.calc_center_median()
            self._add_to_cell(self._cell_key(wco), "face_centers", wco)

        self._binned_verts = nv
        self._binned_edges = ne
        self._binned_faces = nf
        self.debug_all_cells = list(self.cells.keys())

    def build(self, obj, bm):
        """Update the grid. Does incremental add if only topology grew,
        full rebuild if object changed or transform moved."""
        mw = obj.matrix_world
        m = mw
        mat_hash = hash((round(m[0][0],4), round(m[0][3],4),
                         round(m[1][1],4), round(m[1][3],4),
                         round(m[2][2],4), round(m[2][3],4)))

        nv, ne, nf = len(bm.verts), len(bm.edges), len(bm.faces)
        obj_changed = (self._cache_obj != obj.name)
        mat_changed = (self._cache_mat_hash != mat_hash)

        # If the object or transform changed, full rebuild is required
        if obj_changed or mat_changed:
            self._cache_obj = obj.name
            self._cache_mat_hash = mat_hash
            self._full_rebuild(obj, bm)
            return

        # If counts shrunk (deletion happened), full rebuild
        if nv < self._binned_verts or ne < self._binned_edges or nf < self._binned_faces:
            self._full_rebuild(obj, bm)
            return

        # If counts are the same, nothing to do
        if nv == self._binned_verts and ne == self._binned_edges and nf == self._binned_faces:
            return

        # Counts grew — incremental add (the fast path!)
        self._incremental_add(obj, bm, mw)

    def invalidate(self):
        """Force a full rebuild on next build() call."""
        self._cache_obj = None
        self._cache_mat_hash = None
        self._binned_verts = 0
        self._binned_edges = 0
        self._binned_faces = 0

    def get_cells_along_ray(self, ray_origin, ray_dir, max_depth=100.0, padding=1):
        """Return all populated cell keys that the cursor ray passes through,
        plus `padding` neighbor cells around each one.

        This is dead simple: step along the ray, record which cells we enter,
        then expand by `padding` in all directions. No radius estimation,
        no unprojection math. The ray IS the cursor — if a cell is on the ray,
        the cursor is looking at it.
        """
        step = self.cell_size * 0.5  # Half-cell steps so we never skip a cell
        num_steps = int(max_depth / step)
        visited = set()

        for i in range(num_steps):
            t = i * step
            pt = ray_origin + ray_dir * t
            ck = self._cell_key(pt)
            visited.add(ck)

        # Expand each visited cell by padding to catch boundary geometry
        result_set = set()
        for (ci, cj, ck_z) in visited:
            for dx in range(-padding, padding + 1):
                for dy in range(-padding, padding + 1):
                    for dz in range(-padding, padding + 1):
                        key = (ci + dx, cj + dy, ck_z + dz)
                        if key in self.cells:
                            result_set.add(key)

        result = list(result_set)
        self.debug_searched_cells = result
        return result

    def cell_bounds(self, cell_key):
        """Return (min_corner, max_corner) world positions for a cell."""
        s = self.cell_size
        i, j, k = cell_key
        return (Vector((i * s, j * s, k * s)),
                Vector(((i + 1) * s, (j + 1) * s, (k + 1) * s)))

    def get_cells_in_bounds(self, min_v, max_v, padding=1):
        """Return populated cell keys overlapping the given world-space AABB."""
        min_i = int(math.floor(min_v.x * self.inv_cell)) - padding
        min_j = int(math.floor(min_v.y * self.inv_cell)) - padding
        min_k = int(math.floor(min_v.z * self.inv_cell)) - padding
        max_i = int(math.floor(max_v.x * self.inv_cell)) + padding
        max_j = int(math.floor(max_v.y * self.inv_cell)) + padding
        max_k = int(math.floor(max_v.z * self.inv_cell)) + padding

        result = []
        for ci in range(min_i, max_i + 1):
            for cj in range(min_j, max_j + 1):
                for ck in range(min_k, max_k + 1):
                    key = (ci, cj, ck)
                    if key in self.cells:
                        result.append(key)
        return result


# Module-level grid instance (persists across frames, rebuilt only on topo change)
_spatial_grid = SpatialGrid(cell_size=2.0)

def get_spatial_grid():
    """Access the grid for debug overlay drawing."""
    return _spatial_grid


# ---------------------------------------------------------------------------
#  Fast 2D projection (avoids location_3d_to_region_2d overhead)
# ---------------------------------------------------------------------------

def _make_project_fast(region, rv3d):
    """Build a fast projection closure using the perspective matrix directly."""
    W = region.width
    H = region.height
    pm = rv3d.perspective_matrix

    def project_fast(wco):
        v = pm @ wco.to_4d()
        if v.w <= 0:
            return None
        inv_w = 1.0 / v.w
        return Vector((W * (1.0 + v.x * inv_w) * 0.5,
                        H * (1.0 + v.y * inv_w) * 0.5))

    return project_fast


# ---------------------------------------------------------------------------
#  Core snapping functions (unchanged logic, grid pre-filter added)
# ---------------------------------------------------------------------------

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


def _estimate_search_radius(region, rv3d, world_pos, px_radius):
    """Convert a screen-space pixel radius to a generous world-space radius.

    We intentionally overshoot — this is just a coarse pre-filter.
    The exact screen-space check happens later on the candidates.
    """
    # Get two points: cursor center and cursor + px_radius in screen space
    p2d = location_3d_to_region_2d(region, rv3d, world_pos)
    if p2d is None:
        return 10.0  # Fallback: search wide

    # Get the world-space size of one pixel at this depth
    # by unprojecting two screen points at the same depth
    origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, p2d)
    depth = (world_pos - origin).length

    if depth < 0.001:
        return 10.0

    # For perspective: pixel size grows with depth
    # For ortho: pixel size is constant
    p2d_offset = Vector((p2d.x + px_radius, p2d.y))
    ray_o = view3d_utils.region_2d_to_origin_3d(region, rv3d, p2d_offset)
    ray_v = view3d_utils.region_2d_to_vector_3d(region, rv3d, p2d_offset)

    # Project to same depth along view direction
    view_dir = view3d_utils.region_2d_to_vector_3d(region, rv3d, p2d)
    offset_point = ray_o + ray_v * depth

    world_radius = (offset_point - world_pos).length

    # Multiply by safety margin — we'd rather search a few extra cells
    # than miss a valid snap candidate
    final_radius = world_radius * 3.0

    return final_radius


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

    Uses spatial grid as a coarse pre-filter to avoid iterating
    through all geometry. The actual snap decisions are still 100%
    screen-space (pixel distance), same as the original.
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

    # Build/update spatial grid (cached, only rebuilds on topo/transform change)
    _spatial_grid.build(obj, bm)

    # Cast a ray from the cursor into the scene and find which grid cells
    # it passes through.
    ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, (x, y))
    ray_dir = view3d_utils.region_2d_to_vector_3d(region, rv3d, (x, y))

    is_ortho = rv3d.view_perspective == 'ORTHO'

    if is_ortho:
        # Ortho rays are parallel — no camera point, so ray marching fails.
        # Fix: "undistort" by creating a fake camera point behind the view
        # and casting an angled ray from there through the cursor's world
        # position. Turns ortho into perspective-style marching.
        fake_cam = Vector(rv3d.view_location) - ray_dir * rv3d.view_distance
        cursor_world = view3d_utils.region_2d_to_location_3d(
            region, rv3d, (x, y), rv3d.view_location
        )
        angled_dir = (cursor_world - fake_cam).normalized()
        nearby_cells = _spatial_grid.get_cells_along_ray(
            fake_cam, angled_dir,
            max_depth=rv3d.view_distance + ctx.space_data.clip_end
        )
        _spatial_grid.debug_searched_cells = list(nearby_cells)
    else:
        nearby_cells = _spatial_grid.get_cells_along_ray(ray_origin, ray_dir)

    # Projection: ortho needs Blender's built-in (perspective_matrix doesn't work)
    if is_ortho:
        def project(wco):
            return location_3d_to_region_2d(region, rv3d, wco)
    else:
        project = _make_project_fast(region, rv3d)

    # Candidates list stores: (PRIORITY, DIST_SQ, WORLD_CO)
    candidates = []
    limit_sq = max_px * max_px

    # Track which cells had actual candidates (for debug overlay)
    candidate_cell_keys = set()

    # 1. Verts (Priority 0) — from grid cells only
    if do_verts:
        for ck in nearby_cells:
            cell = _spatial_grid.cells[ck]
            for wco in cell["verts"]:
                p2d = project(wco)
                if p2d is None: continue

                d2 = (mouse - p2d).length_squared
                if d2 < limit_sq:
                    candidates.append((0, d2, wco))
                    candidate_cell_keys.add(ck)

    # 2. Edge Centers (Priority 1)
    if do_edge_center:
        for ck in nearby_cells:
            cell = _spatial_grid.cells[ck]
            for wco in cell["edge_centers"]:
                p2d = project(wco)
                if p2d is None: continue

                d2 = (mouse - p2d).length_squared
                if d2 < limit_sq:
                    candidates.append((1, d2, wco))
                    candidate_cell_keys.add(ck)

    # 3. Face Centers (Priority 1)
    if do_face_center:
        for ck in nearby_cells:
            cell = _spatial_grid.cells[ck]
            for wco in cell["face_centers"]:
                p2d = project(wco)
                if p2d is None: continue

                d2 = (mouse - p2d).length_squared
                if d2 < limit_sq:
                    candidates.append((1, d2, wco))
                    candidate_cell_keys.add(ck)

    # 4. Nearest Point on Edge (Priority 2)
    if do_edges:
        for ck in nearby_cells:
            cell = _spatial_grid.cells[ck]
            for (v1_world, v2_world) in cell["edges"]:
                p1_2d = project(v1_world)
                p2_2d = project(v2_world)

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
                                    candidate_cell_keys.add(ck)

    _spatial_grid.debug_candidate_cells = list(candidate_cell_keys)

    # Ortho diagnostic — helps identify why snap fails in ortho
    if is_ortho and not candidates:
        nv = sum(len(_spatial_grid.cells[ck]["verts"]) for ck in nearby_cells) if nearby_cells else 0
        sample = "empty grid"
        for ck in nearby_cells:
            vs = _spatial_grid.cells[ck]["verts"]
            if vs:
                p = project(vs[0])
                sample = f"v3d={vs[0]} -> p2d={p}"
                break
        print(f"[ORTHO_SNAP] 0 candidates | {len(nearby_cells)} cells, {nv} verts | {sample} | mouse=({x},{y}) max_px={max_px}")

    # 5. Sort & Select — same logic as original
    candidates.sort(key=lambda item: (item[0], item[1]))

    for prio, dist_sq, co in candidates:
        if allow_occluded:
            return co

        # Optimization: skip visibility check for very close candidates
        if dist_sq < 25.0:  # < 5px — almost certainly visible
            return co

        if is_visible_to_view(ctx, co):
            return co

    return None
