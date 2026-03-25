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
        # Cache key: (obj_name, vert_count, edge_count, face_count, matrix_hash)
        self._cache_key = None
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
                               "face_centers": [], "edges": []}
        self.cells[key][category].append(data)

    def build(self, obj, bm):
        """Rebuild the grid from the bmesh. Only rebuilds if topology changed."""
        mw = obj.matrix_world
        # Quick hash of the matrix to detect transforms
        m = mw
        mat_hash = hash((round(m[0][0],4), round(m[0][3],4),
                         round(m[1][1],4), round(m[1][3],4),
                         round(m[2][2],4), round(m[2][3],4)))

        new_key = (obj.name, len(bm.verts), len(bm.edges), len(bm.faces), mat_hash)
        if new_key == self._cache_key:
            return  # No change, skip rebuild

        self._cache_key = new_key
        self.cells.clear()

        # Bin verts
        for v in bm.verts:
            if v.hide: continue
            wco = mw @ v.co
            ck = self._cell_key(wco)
            self._add_to_cell(ck, "verts", wco)

        # Bin edge centers + full edge data
        for e in bm.edges:
            if e.hide: continue
            v1w = mw @ e.verts[0].co
            v2w = mw @ e.verts[1].co
            center = (v1w + v2w) * 0.5
            ck = self._cell_key(center)
            self._add_to_cell(ck, "edge_centers", center)
            self._add_to_cell(ck, "edges", (v1w, v2w))

        # Bin face centers
        for f in bm.faces:
            if f.hide: continue
            wco = mw @ f.calc_center_median()
            ck = self._cell_key(wco)
            self._add_to_cell(ck, "face_centers", wco)

        self.debug_all_cells = list(self.cells.keys())

    def invalidate(self):
        """Force a full rebuild on next build() call."""
        self._cache_key = None

    def get_nearby_cells(self, world_pos, radius):
        """Return all cell keys within `radius` world units of `world_pos`.

        This is the coarse filter. We intentionally use a GENEROUS radius
        so we never miss valid snap candidates. The screen-space check
        handles precision.
        """
        inv = self.inv_cell
        cx = world_pos.x * inv
        cy = world_pos.y * inv
        cz = world_pos.z * inv
        r = max(1, int(math.ceil(radius * inv)))

        ix, iy, iz = int(math.floor(cx)), int(math.floor(cy)), int(math.floor(cz))

        result = []
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                for dz in range(-r, r + 1):
                    key = (ix + dx, iy + dy, iz + dz)
                    if key in self.cells:
                        result.append(key)

        # DEBUG: Uncomment to diagnose search radius issues
        # print(f"[GRID DEBUG] world_pos={world_pos}, radius={radius:.2f}, cell_size={self.cell_size}, "
        #       f"r_cells={r}, center_cell=({ix},{iy},{iz}), found_cells={len(result)}")

        self.debug_searched_cells = result
        return result

    def cell_bounds(self, cell_key):
        """Return (min_corner, max_corner) world positions for a cell."""
        s = self.cell_size
        i, j, k = cell_key
        return (Vector((i * s, j * s, k * s)),
                Vector(((i + 1) * s, (j + 1) * s, (k + 1) * s)))


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
    final_radius = world_radius * 2.0

    # DEBUG: Uncomment to diagnose search radius calculation
    # print(f"[RADIUS] px_radius={px_radius}, depth={depth:.3f}, world_radius={world_radius:.3f}, "
    #       f"final={final_radius:.3f}, p2d={p2d}")

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

    # Determine the world-space search area
    # We need a reference point to estimate the pixel-to-world scale.
    # Use the raycast hit point under the cursor, or fall back to view target.
    ref_hit, _, _ = raycast_under_mouse(ctx, x, y)
    if ref_hit is None:
        # No surface under cursor — use the view center/target as depth reference
        view_loc = rv3d.view_location
        ref_hit = view_loc

    search_radius = _estimate_search_radius(region, rv3d, ref_hit, max_px)
    nearby_cells = _spatial_grid.get_nearby_cells(ref_hit, search_radius)

    # Fast projection setup
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
