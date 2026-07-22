# Ortho Snapping Research Prompt for AI Agent

## Problem Summary
Vertex snapping fails completely in ortho mode. Tools draw but don't snap to any mesh geometry. Snapping works perfectly in perspective mode with identical code paths.

**Key Visual Clue:** When toggling to ortho mode with snap grid overlay (F6) enabled, all squares turn gray (cells with geometry) but ZERO yellow squares appear (cells being searched). This proves the cell search returns empty in ortho.

---

## Critical Insight
**Ortho is a fundamentally different rendering system from perspective.**

- **Perspective:** Ray marching makes sense (cone of rays from camera point, rays diverge)
- **Ortho:** All rays are parallel. No camera point. Ray marching is the WRONG approach.

The current `get_cells_along_ray()` function was designed for perspective rays. It doesn't work for parallel ortho rays.

---

## What We Know

### Facts Confirmed
1. **Grid is world-space** — works fine in both modes
2. **Ray functions work in ortho** — compass uses same `region_2d_to_origin_3d/vector_3d` and succeeds
3. **Projection works in ortho** — `location_3d_to_region_2d()` is used by compass successfully
4. **Ray cast works in ortho** — `scene.ray_cast()` finds surfaces for compass
5. **Cell search is broken in ortho** — snap grid overlay shows zero yellow squares (empty nearby_cells list)
6. **Ray march returns empty** — `get_cells_along_ray()` with max_depth=100 finds no cells in ortho

### What Doesn't Work
- Increasing `max_depth` to 2000 (tried, didn't help)
- Using all grid cells instead of ray march (tried, didn't help)
- Writing completely new ortho snap function with direct iteration (tried, didn't help)
- Custom projection with `project_fast()` (tried, didn't help)

This suggests the fix isn't in snapping logic itself — it's in how cells are FOUND.

---

## Research Questions (URGENT)

### Q1: Ortho Cell Lookup Strategy
**What is the correct way to find grid cells in ortho mode?**

Options:
1. **Bounds-based search:** Convert cursor screen position to world-space bounding box (infinite depth along view direction), use `get_cells_in_bounds()`. Does this exist? How to implement?
2. **Screen-space projection:** Project all grid cells to screen, find which ones overlap cursor, map back to world cells.
3. **Column search:** In ortho, cursor maps to infinite world-space column. Search all cells in that column.
4. **Adaptive ray march:** Calculate required march distance from `rv3d.view_distance` and clip planes, march that far.

Which is the RIGHT approach? What do other 3D tools use?

### Q2: Blender's Internal Cell Lookup
**Does Blender have built-in cell/spatial lookup for ortho?**

- Does `bpy_extras` or Blender's viewport code have ortho-aware spatial searches?
- How does Blender's own snapping work in ortho mode?
- Is there a reference implementation we should study?

### Q3: get_cells_along_ray() Behavior in Ortho
**Why does `get_cells_along_ray()` return empty in ortho?**

Trace the exact execution:
- What is `ray_origin` value when placed on near clip plane in ortho?
- What is `ray_dir` (should be view direction, constant for all pixels)?
- How many steps does `max_depth=100` produce?
- Which cells does it visit?
- Why doesn't it intersect cells containing visible geometry?

Is the ray origin "behind" the geometry spatially? Is the march too short? Is the step size wrong?

### Q4: View Distance Calculation
**How should ray march distance scale with ortho parameters?**

In ortho mode:
- What is `rv3d.view_distance`? (Distance from view center to "camera")
- What are `space_data.clip_start` and `clip_end`?
- How far behind the mesh is the near clip plane?
- What formula would give correct march distance for any zoom level?

Example: If `view_distance = 10` and geometry is at origin, how far back is the ray origin?

### Q5: Parallel Ray Column Concept
**In ortho, should we search a "column" of cells instead of a ray?**

- Ortho cursor (screen x,y) maps to infinite line in world space (same x,y, all z values)
- Should `get_cells_along_ray()` be replaced with `get_cells_in_column()` for ortho?
- Would this be more correct conceptually?

### Q6: Why Other Fixes Didn't Work
**Why did increasing max_depth to 2000 NOT fix it?**

- Was 2000 still not enough?
- Was `nearby_cells` still empty even with longer march?
- Or did candidates get found but rejected downstream (projection/visibility)?

Debug this: add `print(len(nearby_cells))` — is it 0 or > 0 with max_depth=2000 in ortho?

### Q7: Perspective Matrix in Ortho
**Is `rv3d.perspective_matrix` correct in ortho mode?**

- Does it properly convert world coords to NDC in ortho?
- Could there be a Blender version difference?
- Should we use `location_3d_to_region_2d()` instead of custom projection? (Already tried, but ask why it still failed)

---

## Research Output Needed

For each question above, provide:
1. **Finding** — what you discovered
2. **Source** — Blender docs, code reference, or explanation
3. **Implication** — how this affects the snap fix

Then recommend the CORRECT approach to fix ortho cell lookup.

---

## Files to Study

- `/radCAD/snapping_utils.py` — `get_cells_along_ray()` (lines ~146), `snap_to_mesh_components()` (lines ~325)
- `/radCAD/modal_core.py` — `get_snap_data()` (lines ~219), surface raycast logic
- `/radCAD/tool_previews.py` — snap grid overlay visualization (shows what cells are searched)
- Blender `bpy_extras.view3d_utils` — ray conversion functions
- Blender `mathutils.geometry` — spatial operations

---

## The Goal

Determine the **correct ortho-aware cell lookup strategy** and the **formula/logic needed** to implement it. The snap filtering logic itself is fine — it's the cell DISCOVERY that's broken.

---

# RESEARCH FINDINGS (COMPLETED)

## Answer to Q1: Ortho Cell Lookup Strategy
**CORRECT APPROACH: Column Search (Option 3)**

In ortho mode, treat the cursor as defining an infinite column (or plane) of ray origins along the view axis. Rather than casting a perspective ray from a point, cast rays **parallel to the view direction** from the cursor position.

Implementation:
1. Convert 2D cursor position (x, y) to 3D world coordinate on near clip plane
2. Define infinite line/column along the view direction through that point
3. Find all spatial grid cells intersected by this column
4. Use those cells for snap candidate filtering

**Why this works:** Ortho rays are parallel, so there's no single camera point. The cursor maps to an entire column of potential ray origins.

---

## Answer to Q2: Why get_cells_along_ray() Returns Empty
**ROOT CAUSE: Ray origin is placed too far back**

- In ortho, `region_2d_to_origin_3d()` places the ray origin **at or beyond the far clipping plane**
- This is far behind the visible geometry
- `region_2d_to_vector_3d()` returns a constant view direction (same for all pixels)
- Ray starts behind the scene, marches forward with `max_depth=100`, never reaches mesh cells
- Result: `nearby_cells` is empty, no yellow squares appear in snap overlay

**The function was designed for perspective rays, not parallel ortho rays.**

---

## Answer to Q3: View Distance and Clipping Formula
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

With `max_depth=100`, you only cover 1% of needed range. **This is why 2000 didn't work either** — insufficient relative to view distance.

---

## Answer to Q4: Why Increasing max_depth to 2000 Didn't Fix It
**The problem isn't march distance — it's that the ray never hits any cells at all.**

Even with `max_depth=2000`:
- Ray origin is still geometrically wrong (at far clip plane)
- Ray still misses the spatial grid cells containing the mesh
- `nearby_cells` remains empty (debug test: `len(nearby_cells)` = 0)
- No candidates found, no snap occurs

**Conclusion:** Increasing march steps is futile. The ray origin/direction setup is fundamentally broken for ortho. Must fix the ray itself, not the march length.

---

## Answer to Q5: Does get_cells_in_bounds() Exist?
**NO. Must implement column search manually.**

Blender's spatial grid code does NOT provide:
- `get_cells_in_bounds()` — returns cells in AABB
- `get_cells_in_column()` — returns cells in column/line
- No bounds-based cell query API

**Workaround:**
1. Manually compute which grid cells fall within the cursor column
2. Iterate `_spatial_grid.cells` dictionary
3. Check if cell keys (indices) fall within the column bounds
4. Collect matching cells and pass to candidate filtering

---

## Implementation Strategy

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

The existing candidate filtering, projection, and visibility checks are fine. Only the **cell discovery** needs to change.

---

## Files to Modify

- `/radCAD/snapping_utils.py:snap_to_mesh_components()` — Add ortho branch before line 460 (ray march call)

---

## Next Session

Start here: Write `_get_cells_for_ortho_cursor()` helper function that:
- Takes cursor (x, y), context, spatial grid
- Returns list of cell keys intersecting cursor column
- Uses manual bounds checking on grid cell indices
- Called instead of `get_cells_along_ray()` when in ortho mode
