# Ortho Snapping Investigation — Updated Master File

**Status:** IN PROGRESS — snapping works but is SLOW
**Updated:** 2026-03-26
**Original problem:** Snapping failed entirely in ortho. That was fixed by using ALL cells.
**Current problem:** Using ALL cells is slow. Need to search fewer cells.

---

## PROBLEM STATEMENT

In **ortho mode only**, tools do not snap to mesh geometry (vertices, edges, face centers). Tools draw correctly using void drawing fallback, but snapping to existing geometry fails completely.

**Behavior:**
- ✅ Perspective mode: snapping works perfectly (verts, edges, centers all snap)
- ❌ Ortho mode: no snapping, tools draw on fallback void plane aligned to view
- ✅ Compass positioning: works correctly in both modes (positions flush on surfaces)

This is view-mode-specific — same mesh, same tools, same settings. Different code path behavior in ortho.

**Key Visual Clue:** When toggling to ortho mode with snap grid overlay (F6) enabled, all squares turn gray (cells with geometry) but ZERO yellow squares appear (cells being searched). This proves `nearby_cells` is empty in ortho.

---

## CRITICAL INSIGHT

**Ortho is a fundamentally different rendering system from perspective.**

- **Perspective:** Ray marching makes sense (cone of rays from camera point, rays diverge)
- **Ortho:** All rays are parallel. No camera point. Ray marching is the WRONG approach.

The current `get_cells_along_ray()` function was designed for perspective rays. It doesn't work for parallel ortho rays. This is an architectural mismatch, not a parameter tuning issue.

---

## WHAT WE KNOW (Confirmed Facts)

1. **Grid is world-space** — works fine in both modes
2. **Ray functions work in ortho** — compass uses same `region_2d_to_origin_3d/vector_3d` and succeeds
3. **Projection works in ortho** — `location_3d_to_region_2d()` is used by compass successfully
4. **Ray cast works in ortho** — `scene.ray_cast()` finds surfaces for compass
5. **Cell search is broken in ortho** — snap grid overlay shows zero yellow squares (empty `nearby_cells` list)
6. **Ray march returns empty** — `get_cells_along_ray()` with `max_depth=100` finds no cells in ortho

---

## WHAT WE TRIED (Failed Approaches)

All of these were attempted and did NOT fix the problem:

1. **Increased max_depth to 2000** (snapping_utils.py line 370)
   - Result: ❌ No change. Snapping still doesn't work.

2. **Used all grid cells instead of ray march** (snapping_utils.py line 369)
   - Changed to: `nearby_cells = list(_spatial_grid.cells.keys())`
   - Result: ❌ No change. Candidates still not found.

3. **Wrote completely new ortho snap function** (`_snap_ortho_direct()` in snapping_utils.py:325-398)
   - Bypassed grid entirely, iterated BMesh directly, used `location_3d_to_region_2d()`
   - Result: ❌ Still doesn't work.

4. **Custom projection with `project_fast()`**
   - Result: ❌ Projection is fine, not the issue.

5. **ALL cells + Blender's built-in projection** (snapping_utils.py lines 369-382, 386-398)
   - Used: `nearby_cells = list(_spatial_grid.cells.keys())` (ALL populated cells)
   - And: `location_3d_to_region_2d()` instead of `_make_project_fast()` for ortho
   - Reasoning: Both cell discovery AND projection must be correct
   - Result: ❌ Still doesn't work. No yellow squares appear in snap overlay. No snapping occurs.
   - **Critical Finding:** Even with ALL possible cells AND correct projection, candidates are still not found. This proves the problem is NOT in cell discovery or projection.

**Conclusion:** The problem is DEEPER than cell discovery, projection, or basic filtering. Something fundamental is broken about how snapping works in ortho mode that bypasses all these normal paths.

---

## RESEARCH FINDINGS (COMPLETED)

### Finding 1: Correct Ortho Cell Lookup Strategy

**CORRECT APPROACH: Column Search**

In ortho mode, treat the cursor as defining an **infinite column** (or plane) of ray origins along the view axis. Rather than casting a perspective ray from a single point, cast rays **parallel to the view direction** from the cursor position.

Implementation:
1. Convert 2D cursor position (x, y) to 3D world coordinate on near clip plane
2. Define infinite line/column along the view direction through that point
3. Find all spatial grid cells intersected by this column
4. Use those cells for snap candidate filtering

**Why this works:** Ortho rays are parallel, so there's no single camera point. The cursor maps to an entire column of potential ray origins.

---

### Finding 2: Why get_cells_along_ray() Returns Empty

**ROOT CAUSE: Ray origin is placed too far back**

- In ortho, `region_2d_to_origin_3d()` places the ray origin **at or beyond the far clipping plane**
- This is far behind the visible geometry
- `region_2d_to_vector_3d()` returns a constant view direction (same for all pixels)
- Ray starts behind the scene, marches forward with `max_depth=100`, never reaches mesh cells
- Result: `nearby_cells` is empty, no yellow squares appear in snap overlay

**The function was designed for perspective rays, not parallel ortho rays.**

---

### Finding 3: View Distance and Clipping Formula

**Ray march must span the full clip range in world space:**

In Blender ortho mode:
- `rv3d.view_distance` = zoom/distance level (grows with zoom level)
- Effective view distance relates to ortho_scale: `ortho_scale = view_distance * sensor_size / lens`
- `space_data.clip_start` = near clipping plane distance
- `space_data.clip_end` = far clipping plane distance

**To reach geometry from any zoom level, ray march must cover:**
- **Minimum depth:** `view_distance - clip_start` (near plane)
- **Maximum depth:** `view_distance + clip_end` (far plane)

For example: if `view_distance = 10`, `clip_start = 0.1`, `clip_end = 1000`, must march at least **1010 units** to guarantee reaching geometry.

With `max_depth=100`, you only cover ~1% of needed range.

---

### Finding 4: Why Increasing max_depth to 2000 Didn't Work

**The problem isn't march distance — it's that the ray never hits any cells at all.**

Even with `max_depth=2000`:
- Ray origin is still geometrically wrong (at far clip plane)
- Ray still misses the spatial grid cells containing the mesh
- `nearby_cells` remains empty (test: `len(nearby_cells)` = 0)
- No candidates found, no snap occurs

**Conclusion:** Increasing march steps is futile. The ray origin/direction setup is fundamentally broken for ortho. Must fix the ray itself, not the march length.

---

### Finding 5: Does get_cells_in_bounds() Exist?

**NO. Must implement column search manually.**

Blender's spatial grid code does NOT provide:
- `get_cells_in_bounds()` — returns cells in AABB
- `get_cells_in_column()` — returns cells in column/line
- No bounds-based cell query API in bpy_extras

**Workaround:**
1. Manually compute which grid cells fall within the cursor column
2. Iterate `_spatial_grid.cells` dictionary
3. Check if cell keys (indices) fall within the column bounds
4. Collect matching cells and pass to candidate filtering

---

## CODE LOCATIONS (Quick Reference)

| What | File | Lines |
|------|------|-------|
| Main snap entry point | `modal_core.py` | 219-364 |
| Snap function calls | `modal_core.py` | 233 |
| Mouse move handler | `modal_core.py` | 366-382 |
| Snapping logic | `snapping_utils.py` | 325-470 |
| Ray march function | `snapping_utils.py` | ~146 |
| Surface raycast | `modal_core.py` | 334-339 |
| Snap grid overlay | `tool_previews.py` | ~892-952 |

---

## IMPLEMENTATION STRATEGY (Ready to Code)

For ortho mode in `snap_to_mesh_components()`:

```
1. Detect ortho: if rv3d.view_perspective == 'ORTHO'
2. Get cursor world position using region_2d_to_location_3d()
3. Get view direction from region_2d_to_vector_3d()
4. Define column bounds from that position extending (view_distance - clip_start) to (view_distance + clip_end)
5. Manually collect grid cells within column bounds
6. Pass cells to existing candidate filtering logic (projection, distance, visibility)
7. Return best snap candidate (same as perspective path)
```

**Important:** The existing candidate filtering, projection, and visibility checks are fine. Only the **cell discovery** needs to change.

---

## WHAT TO CODE NEXT

Write `_get_cells_for_ortho_cursor()` helper function that:
- Takes cursor (x, y), context, spatial grid
- Returns list of cell keys intersecting cursor column
- Uses manual bounds checking on grid cell indices
- Called instead of `get_cells_along_ray()` when in ortho mode

This function should:
1. Convert cursor screen position to world 3D (on near clip plane)
2. Get view direction (parallel rays in ortho)
3. Calculate column bounds from view distance and clip planes
4. Iterate spatial grid cell indices and collect those within column
5. Return collected cell keys

---

## FILES TO MODIFY

- `/radCAD/snapping_utils.py:snap_to_mesh_components()` — Add ortho branch before line 460 (ray march call)

That's the only file that needs changes.

---

## CURRENT STATE (as of latest attempt)

### Code Changes Made
- **snapping_utils.py lines 369-398:** Added ortho detection and two-pronged fix:
  1. Cell discovery: Use ALL populated cells in ortho (`list(_spatial_grid.cells.keys())`) instead of ray march
  2. Projection: Use `location_3d_to_region_2d()` instead of `_make_project_fast()` for ortho
  - Both changes in place, but **still no snapping in ortho mode**

### Critical Finding
**Even with ALL cells AND Blender's built-in projection function, snapping still fails.** No yellow squares appear in the snap overlay (F6). This definitively proves:
- ❌ Cell discovery is NOT the problem
- ❌ Fast projection function is NOT the problem
- ❌ Basic screen-space filtering is NOT the problem

**The real problem is something more fundamental.** Possibilities:
1. The `snap_to_mesh_components()` function itself doesn't work in ortho due to coordinate system issues
2. The visibility check (`is_visible_to_view()`) rejects all candidates in ortho
3. Some property of ortho projection (perspective_matrix, clip planes, etc.) breaks the whole pipeline
4. The BMesh data or context is somehow invalid in ortho mode
5. An early return condition we haven't found
6. The function signature/parameter passing is incorrect for ortho context

### Why the Original Research was Wrong
The research doc concluded "the problem is in cell discovery" but contradicted itself by showing:
- Attempt #2: ALL cells → still no candidates
- Attempt #3: Bypass grid entirely + use `location_3d_to_region_2d()` → still doesn't work

This evidence clearly points to a problem BEYOND cell discovery. The conclusion was wrong.

---

## NEXT SESSION CHECKLIST

### Immediate Diagnostic Steps (DO THIS FIRST)
- [ ] Add print statements to `snap_to_mesh_components()` to see:
  - Is the function even being called in ortho? (print at line 341)
  - How many cells are in `nearby_cells` after ortho branch? (print after line 376)
  - How many candidates are found? (print after line 452)
  - Does `project()` return None for all vertices? (add print in candidate loops)
  - Is visibility check rejecting all candidates? (print in is_visible_to_view)

### Investigation Path
1. Print debug output in Blender console (run operator with F6 snap overlay enabled, check system console)
2. Once we know which step fails, we've found the real culprit
3. The problem is one of these specific failures, NOT a design issue

### Original Plan (if diagnostics pass)
- [ ] If cells + projection work, test visibility check in ortho
- [ ] If visibility is fine, inspect BMesh/grid data in ortho
- [ ] Commit with message: "[WIP] ortho snapping diagnostics — printing debug info"

---

## CURRENT SNAPPING_UTILS.PY STATE

The file has been modified to handle ortho at lines 369-398:
```python
is_ortho = rv3d.view_perspective == 'ORTHO'

if is_ortho:
    nearby_cells = list(_spatial_grid.cells.keys())  # ALL cells
    _spatial_grid.debug_searched_cells = nearby_cells
else:
    nearby_cells = _spatial_grid.get_cells_along_ray(ray_origin, ray_dir)

if is_ortho:
    def project(wco):
        return location_3d_to_region_2d(region, rv3d, wco)
else:
    project = _make_project_fast(region, rv3d)
```

This is the baseline. Don't change it unless diagnostic prints show it's wrong.

---

## END OF PREVIOUS SESSION NOTES

---

# NEW SESSION (2026-03-26) — Snapping Works, But Slow

## Current State of Code

`snapping_utils.py` line 371-383 (last git commit `fixing snap in ortho attempt 1`):

```python
if is_ortho:
    nearby_cells = list(_spatial_grid.cells.keys())  # ALL cells — this is the bug
    _spatial_grid.debug_searched_cells = list(nearby_cells)
else:
    nearby_cells = _spatial_grid.get_cells_along_ray(ray_origin, ray_dir)

# Projection: ortho uses slow built-in, perspective uses fast matrix
if is_ortho:
    def project(wco):
        return location_3d_to_region_2d(region, rv3d, wco)
else:
    project = _make_project_fast(region, rv3d)
```

## How the Debug Overlay Shows the Bug

With F6 snap overlay enabled:
- **Perspective mode**: Only a few cells turn yellow (the ones the cursor ray passes through). Fast.
- **Ortho mode**: ALL cells turn yellow every frame. Slow.

Yellow = being searched. Green = has a snap candidate.

## Why It's Slow — Two Things

1. **Searching all cells every frame** — the `list(_spatial_grid.cells.keys())` line
2. **Slow projection** — ortho uses `location_3d_to_region_2d()` (Blender built-in, slow) instead of the fast matrix multiply

We tested switching ortho to the fast projection — no noticeable improvement. The bottleneck is the cell count, not projection speed.

## Camera Ray Insight (Key to Understanding the Problem)

**Perspective**: camera is a single POINT behind the screen. Rays fan out from it. Ray origin is close to geometry. `get_cells_along_ray(max_depth=100)` works.

**Ortho**: camera is a PLANE (the entire screen). No single point. All rays are parallel — same direction, different origins (one per screen pixel). The cursor picks ONE of those parallel rays.

The cursor's ray in ortho starts at the near clip plane (far from geometry) and goes through the entire scene along the view direction. It legitimately passes through ALL cells along that column.

## What Was Tried in This Session (All Failed / Same Result)

1. **Lateral cell filtering** — find cursor's cell key from `ray_origin`, filter all cells by lateral coords (ignore depth axis). `ray_origin` is at the near clip plane so `cursor_cell` was wrong (empty space).

2. **Frustum culling** — projected 4 screen corners to world space, built bounding box, used `get_cells_in_bounds`. Same result.

3. **Perspective matrix inversion** — inverted `rv3d.perspective_matrix`, converted cursor NDC → world. Didn't reduce cells.

4. **clip_end max_depth** — used `get_cells_along_ray(ray_origin, ray_dir, max_depth=clip_end)`. Same result — near clip plane is far from geometry, still marches through all cells in the column.

5. **Fast projection for ortho** — switched `location_3d_to_region_2d` to `_make_project_fast`. No change in speed. Projection wasn't the bottleneck.

## Why Everything Failed

The cursor ray in ortho legitimately passes through ALL cells along the view direction column. This is physically correct — any vert at any depth on that screen column is a valid snap candidate. You can't exclude them.

So reducing cells searched may not be possible without losing valid snap results.

## Two Things That Still Need Solving

**Problem 1 — Cell count**: Is there ANY way to search fewer cells in ortho?
- Maybe: profile whether the bottleneck is (a) iterating cells or (b) per-vert projection cost. If it's (b), faster projection matters more than fewer cells.
- Maybe: build a 2D screen-space index (pixel grid) that gets invalidated on pan/zoom, so you only check verts near the cursor's pixel position.

**Problem 2 — Search radius**: The user confirmed ortho also needs the same radius-based cell padding as perspective (`padding=1` in `get_cells_along_ray`). Currently not happening.

## Important Constraint: Compass Must Stay Working

The 1-point arc tool uses `snap_normal` (3D surface normal at snap point) to flush the drawing plane to faces. Any fix must preserve 3D world position and surface normal output. Cannot go pure 2D screen space.

## Next Session Starting Point

1. Profile: add timing prints around the vert loop in ortho mode to see where time actually goes
2. Consider a 2D pixel-space vert index — verts sorted/binned by screen pixel, invalidated when view changes. But check if this breaks compass logic first.
3. The existing code path is in `snapping_utils.py` `snap_to_mesh_components()` around lines 365-495.

The file is at last git commit. No pending changes.
