# Ortho Snapping Optimization — Performance Issue

**Status:** WORKING but SLOW
**Date:** 2026-03-26
**Problem:** Ortho snapping checks all 43 grid cells every frame, even cells far from cursor. This slows stage 1 drawing in ortho mode.

---

## THE ISSUE

### Current Behavior (Working but Inefficient)
```python
if is_ortho:
    nearby_cells = list(_spatial_grid.cells.keys())  # ALL 43 cells!
else:
    nearby_cells = _spatial_grid.get_cells_along_ray(...)  # Only ~5-10 cells near cursor
```

**Perspective mode:** Ray march finds only cells the ray passes through (~5-10 cells). Stage 1 drawing is fast.

**Ortho mode:** Brute-force checks all 43 cells in the scene, even cells thousands of units away from cursor. Stage 1 drawing is slow.

### User's Theory (CORRECT)
"45 cells is not that much. It's only ONE cell that it is looking for verts. If I had a million verts in only one cube it shouldn't affect performance if I am not near that cube."

**This is RIGHT.** The grid is SPATIAL. When drawing in area X, we should only check cells in area X, not every cell in the entire scene.

The slowdown is **unnecessary and fixable**.

---

## ROOT CAUSE

We optimized for correctness (made ortho snapping work) but skipped the efficiency optimization.

The master doc's research mentioned: **Column Search**
> In ortho mode, treat the cursor as defining an **infinite column** (or plane) of ray origins along the view axis. Find all spatial grid cells intersected by this column.

We never implemented this. Instead we went with "check all cells" as a quick fix.

---

## SOLUTION STRATEGY

### What to Do
Replace the "all cells" approach with a **column-based cell search**:

1. Get cursor 2D position (x, y)
2. Convert to 3D world coordinate on near clip plane
3. Get view direction (parallel rays in ortho)
4. Calculate column bounds from view distance and clip planes
5. Iterate spatial grid cell indices and collect those within column
6. Return collected cell keys

### Expected Result
Instead of checking all 43 cells, check only ~1-5 cells near cursor. Stage 1 drawing becomes as fast in ortho as perspective.

---

## CODE LOCATIONS

| What | File | Lines |
|------|------|-------|
| Current ortho cell discovery | `snapping_utils.py` | 371-381 |
| Snap function entry | `snapping_utils.py` | 325 |
| Ray march reference (perspective) | `snapping_utils.py` | 146-177 |
| Grid cell key calculation | `snapping_utils.py` | 45-49 |

---

## IMPLEMENTATION OUTLINE

### New Function to Write
```python
def _get_cells_for_ortho_cursor(cursor_x, cursor_y, region, rv3d, spatial_grid):
    """
    Find grid cells intersected by cursor column in ortho mode.

    Returns list of cell keys that the cursor column passes through.

    Algorithm:
    1. Convert cursor (x, y) to 3D world on near clip plane
    2. Get view direction (parallel rays in ortho)
    3. Calculate column bounds (view_distance ± clip planes)
    4. Iterate grid cells and collect those in column bounds
    5. Return cell keys
    """
    # Implementation here
    pass
```

### Where to Call It
In `snap_to_mesh_components()` at line 371, replace:
```python
if is_ortho:
    nearby_cells = list(_spatial_grid.cells.keys())  # OLD: all cells
```

With:
```python
if is_ortho:
    nearby_cells = _get_cells_for_ortho_cursor(x, y, region, rv3d, _spatial_grid)
```

---

## REFERENCE DATA (from previous research)

### View Distance Calculation
In Blender ortho mode:
- `rv3d.view_distance` = zoom/distance level
- `space_data.clip_start` = near clipping plane distance
- `space_data.clip_end` = far clipping plane distance

**Column must span:** `view_distance - clip_start` to `view_distance + clip_end`

### Grid Cell Math
Grid cells use indices:
```python
cell_key = (int(floor(x / cell_size)),
            int(floor(y / cell_size)),
            int(floor(z / cell_size)))
```

Column in ortho = range of X, Y indices at cursor position, all Z indices in clip range.

---

## TESTING CHECKLIST

- [ ] Implement `_get_cells_for_ortho_cursor()` function
- [ ] Call it from ortho branch in `snap_to_mesh_components()`
- [ ] Test stage 1 drawing in ortho — should be as fast as perspective now
- [ ] Verify snapping still works (verts, edges, face centers snap correctly)
- [ ] Remove the diagnostic print statements added earlier (lines ~377-379, ~465-475)
- [ ] Commit with message: "ortho snapping: optimize cell discovery with column search"

---

## NOTES FOR NEXT SESSION

- The snapping itself works perfectly now — this is purely a performance optimization
- The column search is more complex than "all cells" but way more efficient
- Reference the master doc's "Finding 1: Correct Ortho Cell Lookup Strategy" for the detailed column search theory
- Grid cell indices are integers, so bounds checking is simple integer range math
- Padding (neighboring cells) should still be applied like in `get_cells_along_ray()`

---

## FILES TO MODIFY

- `/radCAD/snapping_utils.py` — Add `_get_cells_for_ortho_cursor()` and update cell discovery at line 371

That's the only file that needs changes.
